"""Redator de segredos — camada 3 da proteção
([04](../../../docs/architecture/04-safety-and-git-runtime.md) §5).

É **um só** e vive aqui. Roda na seleção de contexto, na persistência de log e na
serialização de resposta JSON de `/api/*`.

Puro: opera sobre string, não lê arquivo, não consulta ambiente.

## Um motor, três formas de perguntar

`detect_secret_spans` é o **motor**: devolve as posições de tudo que os padrões
reconhecem, sem alterar o texto. As outras duas entradas são finas por cima dele:

* `redact(text)` — substitui cada span por `«redigido»`. É o que sempre foi;
* `is_sensitive_key(key)` — classifica um **nome de campo**, onde não existe adjacência
  textual nenhuma para um regex ver (`{"password": "x"}` não tem `password: x` em lugar
  nenhum até alguém linearizar).

`is_sensitive_key` e o padrão `assigned_secret` leem a **mesma** lista de nomes
(`_SENSITIVE_KEY_WORDS`). Duas listas divergindo seria o mesmo defeito que a E4 fechou na
gramática de glob: dois lugares decidindo a mesma coisa de formas diferentes.

O Context Engine (E5) consome `is_sensitive_key`/`detect_secret_spans` para redação
estrutural e posicional ([03](../../../docs/architecture/03-context-architecture.md) §4).
Ele **não** tem cópia de padrão nenhum — o que ele faz com os spans (mapear de volta para
o campo de origem, propagar valor conhecido) é decisão dele; reconhecer o segredo é
decisão daqui.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REDACTED = "«redigido»"

#: Nomes de campo que significam "o valor ao lado é segredo". **Uma só lista**, em forma
#: de palavras, para que as duas leituras saiam dela sem divergir:
#:
#: * `is_sensitive_key` compara contra a junção sem separador (`api` + `key` → `apikey`);
#: * `assigned_secret` monta a alternância com separador opcional (`api[_-]?key`).
_SENSITIVE_KEY_WORDS: tuple[tuple[str, ...], ...] = (
    ("api", "key"),
    ("secret",),
    ("token",),
    ("password",),
    ("passwd",),
    ("authorization",),
)

#: Forma normalizada de cada nome — o que `is_sensitive_key` compara.
_SENSITIVE_KEY_NAMES: frozenset[str] = frozenset("".join(words) for words in _SENSITIVE_KEY_WORDS)

#: A alternância do regex, derivada da MESMA lista. Preserva exatamente o texto que
#: `assigned_secret` sempre teve: `api[_-]?key|secret|token|password|passwd|authorization`.
_SENSITIVE_KEY_ALTERNATION = "|".join("[_-]?".join(words) for words in _SENSITIVE_KEY_WORDS)


@dataclass(frozen=True, slots=True)
class _Pattern:
    """Um padrão do catálogo, com **tudo** que o motor precisa saber sobre ele.

    Os dois campos de lookaround existem porque `E5-AUD4-003` provou que o intervalo do
    `match` não descreve o que a detecção leu. `url_credentials` só reconhece
    `reader:passcode` quando existe `://` antes e `@` depois, e nenhuma dessas duas
    âncoras aparece em `match.start()`/`match.end()`. Sem declará-las, `recognition_span`
    afirmava que a detecção cabia inteira dentro de um fragmento quando ela dependia de
    texto do fragmento vizinho — e a credencial atravessava a fronteira sem ser vista.

    Declarar aqui, e não no renderer, é o que mantém **um** motor: quem consome spans
    nunca precisa saber qual padrão é esse nem reimplementar a asserção.
    """

    name: str
    #: O padrão histórico, exatamente como `redact()` sempre o aplicou. É ele, e só ele,
    #: que decide o que vira `«redigido»`.
    pattern: re.Pattern[str]
    #: Grupo 1 é rótulo preservado (`Bearer `, `password: `): fica no texto, só o valor
    #: sai. Para estes o intervalo de substituição começa no fim do grupo 1.
    prefix_preserving: bool = False
    #: O texto que um lookbehind **positivo** exige imediatamente antes do match. Casado
    #: com `\Z` contra o trecho que termina onde o match começa.
    lookbehind: re.Pattern[str] | None = None
    #: O texto que um lookahead **positivo** exige imediatamente depois do match.
    lookahead: re.Pattern[str] | None = None


# Ordem importa: padrões mais específicos primeiro.
_PATTERNS: tuple[_Pattern, ...] = (
    _Pattern(
        "pem_block",
        re.compile(
            r"-----BEGIN[A-Z ]*PRIVATE KEY-----.*?-----END[A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    _Pattern(
        "url_credentials",
        re.compile(r"(?<=://)[^/\s:@]+:[^/\s:@]+(?=@)"),
        lookbehind=re.compile(r"://\Z"),
        lookahead=re.compile(r"@"),
    ),
    _Pattern(
        "bearer",
        re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-+/=]{8,}"),
        prefix_preserving=True,
    ),
    _Pattern("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9._\-]{8,}")),
    _Pattern("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{16,}")),
    _Pattern(
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})"),
    ),
    _Pattern("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    _Pattern(
        "assigned_secret",
        re.compile(
            r"(?i)\b((?:" + _SENSITIVE_KEY_ALTERNATION + r")"
            r"\s*[:=]\s*)(?:\"|')?([^\s\"',;]{6,})"
        ),
        prefix_preserving=True,
    ),
)

#: Lookaround **positivo** — o que exige que certo texto **exista**. O negativo (`(?!`,
#: `(?<!`) exige ausência: não há texto lido para incluir em `recognition_span`.
_POSITIVE_LOOKBEHIND = re.compile(r"\(\?<=")
_POSITIVE_LOOKAHEAD = re.compile(r"\(\?=")


def _validate_patterns(patterns: tuple[_Pattern, ...]) -> None:
    """Todo lookaround positivo do catálogo declara o texto que exige. Verificado **no
    import**.

    Um padrão novo com `(?=` e sem `lookahead=` é exatamente o defeito de
    `E5-AUD4-003` — e ele não pode depender de alguém lembrar de rodar a suíte: quem
    acrescenta o padrão não é quem lê esta docstring. `\\b` não entra na verificação: é
    uma propriedade do caractere vizinho, não texto adicional que a detecção leu, e
    incluí-lo em `recognition_span` inventaria travessia onde não há.
    """
    for spec in patterns:
        source = spec.pattern.pattern
        if _POSITIVE_LOOKBEHIND.search(source) and spec.lookbehind is None:
            raise ValueError(
                f"padrão {spec.name!r} usa lookbehind positivo sem declarar `lookbehind`"
            )
        if _POSITIVE_LOOKAHEAD.search(source) and spec.lookahead is None:
            raise ValueError(
                f"padrão {spec.name!r} usa lookahead positivo sem declarar `lookahead`"
            )


_validate_patterns(_PATTERNS)

#: Fronteira camelCase, para `is_sensitive_key` enxergar `apiKey` como `api` + `key`.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

#: Qualquer coisa que não seja letra ou dígito separa componentes de um nome de campo.
_KEY_SEPARATORS = re.compile(r"[^0-9A-Za-z]+")


@dataclass(frozen=True, slots=True)
class Interval:
    """Um intervalo de *code points* `[start, end)` no texto **exatamente como passado**."""

    start: int
    end: int

    def crosses(self, boundary: int) -> bool:
        """O intervalo começa antes de `boundary` e termina depois dele?"""
        return self.start < boundary < self.end


@dataclass(frozen=True, slots=True)
class SecretSpan:
    """Onde, no texto recebido, um padrão reconheceu segredo — em **dois** intervalos.

    A distinção entre os dois é o que `E5-AUD3-002` provou ser necessária, e tratá-los
    como a mesma coisa foi a causa daquele vazamento:

    * ``recognition_span`` — todo o texto que a detecção **leu** para se convencer:
      o match inteiro, o prefixo preservado e o que lookbehind/lookahead positivo exigiu.
      Em `Bearer ABCDEFGHIJKLMNOP` é a string inteira — sem a palavra `Bearer` não há
      como afirmar que o resto é credencial; em `https://reader:passcode@host` é
      `://reader:passcode@`, porque sem o `://` e o `@` aquilo é um par qualquer separado
      por dois-pontos.
    * ``replacement_span`` — só o que vira `«redigido»`. Nos mesmos exemplos, apenas
      `ABCDEFGHIJKLMNOP` e `reader:passcode`; `Bearer `, `://` e `@` são preservados,
      como `redact()` sempre fez.

    Para a maioria dos padrões os dois são **idênticos**. Diferem nos padrões de prefixo
    preservado (`bearer`, `assigned_secret`) e nos de lookaround (`url_credentials`), e é
    aí que confundi-los custa:

    * quem **substitui** texto tem de usar `replacement_span`, senão `redact()` passa a
      apagar o rótulo (`Bearer `, `password: `) e muda o texto que o backend emite;
    * quem **pergunta de onde veio a detecção** — se ela atravessa a fronteira entre dois
      fragmentos, por exemplo — tem de usar `recognition_span`, senão uma credencial cujo
      contexto está num campo e cujo valor está em outro parece nunca ter cruzado
      fronteira nenhuma, e a detecção é descartada. Era literalmente esse o vazamento.

    Ambos são índices no texto **como passado** a `detect_secret_spans`, o que permite ao
    chamador projetar a detecção de volta para o fragmento de origem em vez de recortar o
    resultado já substituído por delimitador (`E5-AUD2-003`).
    """

    recognition_span: Interval
    replacement_span: Interval
    pattern_name: str


@dataclass(frozen=True, slots=True)
class _Segment:
    """Um trecho do texto de trabalho e de onde ele veio no texto original.

    ``is_marker`` distingue um `«redigido»` já substituído de um trecho literal. Num
    marcador o mapa **não** é caractere a caractere: qualquer posição dentro dele
    corresponde à região inteira que ele substituiu.
    """

    working_start: int
    working_end: int
    original_start: int
    original_end: int
    is_marker: bool


def _project(text: str, regions: tuple[tuple[int, int], ...]) -> tuple[str, tuple[_Segment, ...]]:
    """O texto com ``regions`` já substituídas, mais o mapa de volta para o original."""
    parts: list[str] = []
    segments: list[_Segment] = []
    cursor = 0
    working = 0

    for start, end in regions:
        if start > cursor:
            chunk = text[cursor:start]
            parts.append(chunk)
            segments.append(_Segment(working, working + len(chunk), cursor, start, False))
            working += len(chunk)
        parts.append(REDACTED)
        segments.append(_Segment(working, working + len(REDACTED), start, end, True))
        working += len(REDACTED)
        cursor = end

    if cursor < len(text) or not segments:
        chunk = text[cursor:]
        parts.append(chunk)
        segments.append(_Segment(working, working + len(chunk), cursor, len(text), False))

    return "".join(parts), tuple(segments)


def _to_original_start(segments: tuple[_Segment, ...], position: int) -> int:
    for segment in segments:
        if segment.working_start <= position < segment.working_end:
            if segment.is_marker:
                return segment.original_start
            return segment.original_start + (position - segment.working_start)
    return segments[-1].original_end if segments else position


def _to_original_end(segments: tuple[_Segment, ...], position: int) -> int:
    """``position`` é exclusivo: quem decide é o último caractere coberto, em ``position-1``."""
    for segment in segments:
        if segment.working_start < position <= segment.working_end:
            if segment.is_marker:
                return segment.original_end
            return segment.original_start + (position - segment.working_start)
    return segments[-1].original_end if segments else position


def _recognition_window(spec: _Pattern, working: str, match: re.Match[str]) -> tuple[int, int]:
    """O intervalo que a detecção de fato **leu**, incluindo o exigido por lookaround.

    `match.start()`/`match.end()` cobrem só o texto **consumido**. Para
    `url_credentials`, o `://` e o `@` que provam que aquilo é credencial ficam de fora
    do match — e foi por isso que `https://` num campo e `reader:passcode@host` noutro
    produziam um `recognition_span` inteiramente contido no segundo, sem travessia
    aparente, com a credencial saindo crua nos bytes (`E5-AUD4-003`).

    Só amplia: se a asserção não casar aqui (não deveria acontecer — ela acabou de casar
    dentro do próprio `finditer`), o intervalo do match é mantido como está.
    """
    start, end = match.start(), match.end()

    if spec.lookbehind is not None:
        behind = spec.lookbehind.search(working, 0, start)
        if behind is not None and behind.end() == start:
            start = behind.start()

    if spec.lookahead is not None:
        ahead = spec.lookahead.match(working, end)
        if ahead is not None:
            end = ahead.end()

    return start, end


def detect_secret_spans(text: str) -> tuple[SecretSpan, ...]:
    """Todas as posições que os padrões reconhecem, **sem alterar o texto**.

    Os padrões são aplicados na mesma ordem e com a mesma semântica de sempre — cada um
    enxergando o resultado dos anteriores, porque essa cascata **é** o comportamento
    histórico de `redact()` e mudá-la mudaria silenciosamente o texto que o backend já
    emite hoje. A diferença é que aqui nada é substituído de verdade: a cada passo o
    texto de trabalho é reprojetado a partir do original mais as regiões já reconhecidas,
    e as posições encontradas são mapeadas **de volta para coordenadas do texto
    original**.

    É esse mapa que torna a função reutilizável fora daqui: quem recebe os spans pode
    dizer de qual campo cada segredo veio, em vez de tentar recortar o resultado já
    substituído por um delimitador — o recorte por delimitador foi exatamente o que
    devolvia a credencial pela chave em `E5-AUD2-003`.

    Os spans podem se sobrepor (dois padrões reconhecendo a mesma região). Quem
    substitui resolve com `merge_spans`; quem classifica campos aproveita a sobreposição
    como informação.
    """
    if not text:
        return ()

    spans: list[SecretSpan] = []
    regions: tuple[tuple[int, int], ...] = ()

    for spec in _PATTERNS:
        working, segments = _project(text, regions)
        found: list[tuple[int, int]] = []

        for match in spec.pattern.finditer(working):
            working_start = match.end(1) if spec.prefix_preserving else match.start()
            working_end = match.end()
            if working_end <= working_start:
                continue

            start = _to_original_start(segments, working_start)
            end = _to_original_end(segments, working_end)
            if end <= start:
                continue

            # O texto **lido** pela detecção: o match inteiro (incluindo o prefixo
            # preservado) mais o que lookbehind/lookahead positivo exigiram. Para os
            # padrões sem prefixo nem lookaround é o mesmo intervalo; para
            # `bearer`/`assigned_secret`/`url_credentials` é maior, e é essa diferença
            # que diz se a detecção precisou de contexto de outro fragmento para existir
            # (`E5-AUD3-002`, `E5-AUD4-003`).
            window_start, window_end = _recognition_window(spec, working, match)
            recognition_start = _to_original_start(segments, window_start)
            recognition_end = _to_original_end(segments, window_end)

            found.append((start, end))
            spans.append(
                SecretSpan(
                    recognition_span=Interval(
                        start=min(recognition_start, start), end=max(recognition_end, end)
                    ),
                    replacement_span=Interval(start=start, end=end),
                    pattern_name=spec.name,
                )
            )

        if found:
            regions = _merge_regions([*regions, *found])

    return tuple(
        sorted(
            spans,
            key=lambda span: (
                span.replacement_span.start,
                -span.replacement_span.end,
                span.pattern_name,
            ),
        )
    )


def _merge_regions(regions: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Regiões `(start, end)` disjuntas. Só **sobreposição** funde — ver `merge_spans`."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(regions):
        if merged and start < merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return tuple(merged)


def merge_spans(spans: tuple[SecretSpan, ...] | list[SecretSpan]) -> tuple[tuple[int, int], ...]:
    """Regiões `(start, end)` disjuntas cobrindo os spans. Só **sobreposição** funde.

    Spans que apenas se tocam (`[10,20]` e `[20,30]`) continuam separados: na cascata,
    cada padrão substitui o seu por um marcador próprio, e fundir aqui mudaria o texto
    emitido em relação ao que este módulo sempre produziu.

    Usa **`replacement_span`**, nunca `recognition_span`: quem substitui texto só pode
    apagar o valor, não o rótulo que o provou. Trocar os dois aqui faria `redact()` comer
    o `Bearer `/`password: ` que ele sempre preservou.
    """
    return _merge_regions(
        [(span.replacement_span.start, span.replacement_span.end) for span in spans]
    )


def redact(text: str) -> str:
    """Substitui segredos reconhecíveis por ``«redigido»``.

    Conservador por desenho: prefere redigir demais a deixar passar. Não é — e não
    pretende ser — detecção exaustiva; é a última das três camadas, não a única.

    Wrapper fino sobre `detect_secret_spans`: **um** motor de detecção, três formas de
    perguntar a ele.
    """
    if not text:
        return text

    regions = merge_spans(detect_secret_spans(text))
    if not regions:
        return text

    parts: list[str] = []
    cursor = 0
    for start, end in regions:
        parts.append(text[cursor:start])
        parts.append(REDACTED)
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _key_components(key: str) -> tuple[str, ...]:
    """`api_key`, `apiKey`, `api-key`, `API KEY` → `("api", "key")`.

    Separadores comuns e fronteira camelCase viram corte; o resto é *casefolded*. A
    fronteira camelCase é aplicada **antes** do *casefold*, senão não haveria maiúscula
    para reconhecer.
    """
    spaced = _CAMEL_BOUNDARY.sub(" ", key)
    return tuple(part.casefold() for part in _KEY_SEPARATORS.split(spaced) if part)


def is_sensitive_key(key: str) -> bool:
    """Este **nome de campo** significa "o valor é segredo"?

    Compara contra `_SENSITIVE_KEY_NAMES` — a mesma lista que alimenta `assigned_secret`.
    Casa qualquer **subsequência contígua** de componentes, não só o nome inteiro:

    * `password`, `api_key`, `apiKey`, `API-KEY` → sensível;
    * `db_password`, `my_api_key`, `auth.token` → sensível (o componente sensível está
      lá, só acompanhado);
    * `tokenizer`, `keyboard`, `secretary` → **não** sensível: `tokenizer` é um
      componente só, e `tokenizer` não é `token`. Casar por substring aqui redigiria
      `{"tokenizer": "gpt-4"}` inteiro, que é conteúdo legítimo.

    Chave vazia ou só com separadores devolve `False` — não há componente para
    classificar. Quem chama trata o caso de chave impresentável à parte: não conseguir
    classificar não é o mesmo que classificar como segura.
    """
    components = _key_components(key)
    if not components:
        return False

    for start in range(len(components)):
        for end in range(start + 1, len(components) + 1):
            if "".join(components[start:end]) in _SENSITIVE_KEY_NAMES:
                return True
    return False


def redact_path(path: str) -> str:
    """Redação aplicada a um caminho antes de virar ``subject`` de `SafetyEvent`.

    Um caminho pode carregar credencial (remote git com token). O resto do caminho é
    informação de diagnóstico legítima e é preservado.
    """
    return redact(path)


__all__ = [
    "REDACTED",
    "Interval",
    "SecretSpan",
    "detect_secret_spans",
    "is_sensitive_key",
    "merge_spans",
    "redact",
    "redact_path",
]
