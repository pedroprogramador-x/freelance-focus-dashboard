"""`render_block_text` — **a única função que produz texto emitido**.

[02] §5 separa três camadas, e a terceira — o Rendered Context Artifact — é a que
"responde *qual conhecimento o Developer recebeu?*". Isso só é verdade se o texto que o
orçamento mede e o texto que o artefato grava forem **o mesmo objeto**, produzido pelo
mesmo código.

Por isso este módulo tem dono único do assunto: `selection.select_context` mede orçamento
chamando `render_block_text`, e `manifest.render_context` grava chamando
`render_block_text`. Não existe função de estimativa de tamanho em lugar nenhum.

## As três camadas de redação ([03] §4)

Duas rodadas de auditoria reprovaram abordagens que decidiam "isto é segredo?" olhando o
texto **já achatado** e comparando *quantas* redações uma leitura combinada revelava
contra a soma de leituras isoladas. A contagem não isola fronteira nenhuma: um campo
`password` a mais, ou uma folha interposta entre duas metades de um token, mascarava o
número sem proteger nada (`E5-AUD2-001`). **Não existe contagem de matches em lugar
nenhum deste módulo.** O que existe são três camadas, em sequência:

1. **Estrutural** (`_collect_leaves`) — `structured` é percorrido como **árvore**, antes
   de virar texto. `safety.is_sensitive_key` classifica cada chave; uma chave sensível
   marca **toda a subárvore descendente**, atravessando listas, objetos e qualquer
   profundidade. Folha sob subárvore sensível é redigida **incondicionalmente**, sem
   depender de o valor "parecer" segredo (`E5-AUD2-004`).
2. **Posicional** (`_mark_fragments`) — `safety.detect_secret_spans` devolve **posições**
   no texto que recebeu, então cada detecção é projetada de volta para o fragmento de
   origem. Quatro leituras alimentam o mesmo conjunto de marcas, nenhuma comparando
   quantidade com outra: cada fragmento isolado; a linha `caminho: valor` (a adjacência
   que `assigned_secret` precisa, sem recorte por delimitador — `E5-AUD2-003`); cada
   **par ordenado** de fragmentos colados (o segredo partido, imune a folha interposta —
   `E5-AUD2-001`); e a projeção canônica inteira (corridas contíguas de três ou mais
   fragmentos).
3. **Propagação** (`_propagation_values`) — os valores **comprovadamente** sensíveis
   pelas duas camadas acima viram um conjunto; qualquer ocorrência literal deles em
   título, corpo ou `structured` é redigida, mesmo onde não há rótulo sensível
   (`E5-AUD2-002`). Escalar genérico (número, booleano, `null`) é redigido **localmente**
   pela camada 1 mas **não** entra nesse conjunto: propagar `42` redigiria todo `42` do
   documento.

## Invariante

Depois das três camadas, nenhum dado autoral cru reaparece em `blocks[].text`,
`blocks[].origin.title` ou `ContextManifest.entries[].title` — os dois últimos leem
`RenderedBlock.title`, que **é** o título já redigido, nunca uma cópia paralela.

Framing (cabeçalho fixo, rótulos) vem **depois** da redação. E o texto é normalizado em
NFC como **último** passo: `canonical_json` aplica NFC ao serializar o artefato, então
medir antes disso mediria uma forma intermediária que os bytes gravados não têm
(`E5-AUD2-005`). Com o NFC aqui, `len(RenderedBlock.text)` é exatamente o comprimento do
`blocks[].text` relido do disco.

## Fail-closed

Profundidade patológica, fragmento demais ou tipo que não dá para linearizar não viram
melhor esforço: `_fail_closed_block` devolve um bloco inteiramente redigido.
"""

from __future__ import annotations

import itertools
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.context_engine.content_hash import normalize_structured, normalize_text
from app.db.models import ContextRegistryEntry
from app.safety.redaction import (
    REDACTED,
    Interval,
    SecretSpan,
    detect_secret_spans,
    is_sensitive_key,
    merge_spans,
)

#: Versão do renderizador, gravada em `ContextManifest.renderer_version` ([02] §5).
#: `v5`: travessia de fronteira emitida pela **interseção** de `replacement_span` com
#: cada fragmento, nunca pelo fragmento inteiro (`E5-AUD4-002`); heurística de extensão
#: gulosa removida sem substituta (`E5-AUD4-001`). O contexto de lookaround passou a
#: entrar em `recognition_span` no motor único (`E5-AUD4-003`), o que muda quais
#: travessias são vistas — e portanto o texto emitido.
#: `v6`: `PROPAGATED_SECRET_REDACTION` (`E5-AUD5-001`) passou a entrar em
#: `transformations`. `text` não muda, mas `transformations` participa do payload
#: hasheado ([02] §5), então o bump segue o mesmo precedente de `v1`→`v2` (troca de
#: formato de `structured`): qualquer mudança que afete o payload hasheado avança esta
#: constante, mesmo quando o texto visível não muda.
RENDERER_VERSION = "e5.block.v6"

#: Divisor da estimativa de tokens. Inteiro, e a divisão arredonda para cima: nenhum
#: `float` participa de decisão de orçamento.
_CHARS_PER_TOKEN = 4

#: Separador entre rótulo e valor na linearização de `structured`. É **reservado**: um
#: caminho que o contenha não pode ser apresentado (o leitor não saberia onde o rótulo
#: termina), e vira `_PROTECTED_LABEL`.
_LABEL_SEPARATOR = ": "

#: Rótulo emitido quando o caminho não pode ser apresentado — porque contém o separador
#: reservado, porque ele próprio carrega segredo, ou porque a redação o alterou.
#: **Marcador fixo, nunca reconstrução parcial do caminho** (`E5-AUD2-003`).
_PROTECTED_LABEL = "<chave protegida>"

#: Comprimento mínimo para um valor entrar na propagação global (camada 3). Abaixo disso
#: a chance de coincidência com conteúdo legítimo domina o ganho: propagar `"1"`, `"ok"`
#: ou `"true"` redigiria o documento inteiro por coincidência, não por segredo.
_MIN_PROPAGATION_LENGTH = 6

#: Tetos de fail-closed. Acima deles o bloco inteiro é redigido — ver `_fail_closed_block`.
_MAX_STRUCTURED_DEPTH = 32
_MAX_FRAGMENTS = 256

#: Marcador acrescentado a `blocks[].transformations` quando **alguma** redação daquele
#: bloco veio de detecção que atravessa a fronteira entre dois fragmentos.
#:
#: É **sinalização de auditoria, não segurança**: não muda decisão de redação nenhuma, não
#: diz qual fragmento, qual posição, nem qual valor. Existe para responder, depois, *por
#: que* um bloco veio mais pobre do que o autor escreveu quando nenhum campo isolado
#: continha segredo — distinguir "coincidência entre um par de fragmentos" (o falso
#: positivo que a V1 aceita deliberadamente, [03] §4) de "segredo autocontido num campo".
CROSS_FRAGMENT_REDACTION = "cross_fragment_secret_redaction"

#: Marcador acrescentado a `blocks[].transformations` quando **alguma** redação daquele
#: bloco veio da camada 3 (propagação) — um valor já comprovado sensível redigido por
#: **cópia literal**, não por detecção direta naquele fragmento (`E5-AUD5-001`).
#:
#: Mesma natureza que `CROSS_FRAGMENT_REDACTION`: **sinalização de auditoria, não
#: segurança**. Não muda nenhuma decisão de redação — é lido depois de `whole`/`local`
#: já estarem decididos. Não carrega o valor, substring, posição nem contagem: um
#: booleano por bloco bastava para a pergunta que existe para responder — *este bloco
#: perdeu conteúdo público porque coincidiu com um segredo já visto em outro lugar dele
#: mesmo?* — e uma contagem exigiria que todo consumidor de `transformations` deixasse de
#: usar `in`/igualdade simples para reconhecer o marcador (ver [03] §4, que já previa essa
#: troca como condicional: "se o formato suportar sem ampliar escopo").
PROPAGATED_SECRET_REDACTION = "propagated_secret_redaction"  # noqa: S105 — nome de marcador, não credencial


class _Unrenderable(Exception):
    """`structured` que não dá para linearizar com prova de segurança. Fail-closed."""


class Transformation(str, Enum):
    """O que pode ter sido feito com o texto de um bloco. Registrado no artefato."""

    #: `normalize_text`/`normalize_structured` de [03] §2 — NFC, CRLF→LF, espaço final por
    #: linha, quebras finais.
    NORMALIZE = "normalize"
    #: As três camadas de redação. **Obrigatória.**
    REDACT = "redact"


#: A ordem do pipeline é desta constante, **não** da ordem em que o chamador listou.
_PIPELINE_ORDER: tuple[Transformation, ...] = (Transformation.NORMALIZE, Transformation.REDACT)

DEFAULT_TRANSFORMATIONS: tuple[Transformation, ...] = _PIPELINE_ORDER


@dataclass(frozen=True, slots=True)
class RenderedBlock:
    """O texto emitido de uma entrada, mais o título **já redigido**.

    `title` é o mesmo valor que se propaga para `ScoredEntry.title` e, por ele, para
    `blocks[].origin.title` e `ContextManifest.entries[].title` — nunca uma cópia paralela
    do título cru sobrevive onde a redação não chegou.

    `transformations` é a lista que o bloco grava em `blocks[].transformations` ([02] §5).
    Sai daqui, e não do chamador, porque é aqui que se sabe o que de fato aconteceu com o
    texto — inclusive se `CROSS_FRAGMENT_REDACTION` ou `PROPAGATED_SECRET_REDACTION`
    foram acionados.
    """

    text: str
    title: str
    transformations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Leaf:
    """Uma folha de `structured`, já classificada pela camada estrutural."""

    path: str
    text: str
    #: Camada 1: está sob uma subárvore cuja chave `is_sensitive_key` reconheceu.
    sensitive: bool
    #: Número, booleano ou `null`. Redigido localmente quando sensível, mas **nunca**
    #: entra na propagação global da camada 3.
    generic_scalar: bool


@dataclass(frozen=True, slots=True)
class _Fragment:
    """Um pedaço de conteúdo autoral que entra na projeção da camada posicional."""

    role: str  # "title" | "body" | "label" | "value"
    leaf_index: int  # -1 para title/body
    text: str
    #: Pode entrar na propagação global? Escalar genérico não pode.
    propagatable: bool


def approx_tokens(text: str) -> int:
    """Estimativa inteira de tokens: `ceil(len(text) / 4)`, sem `float`."""
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def normalize_transformations(
    transformations: tuple[Transformation, ...] | list[Transformation],
) -> tuple[Transformation, ...]:
    """Valida o conjunto pedido e devolve-o **na ordem canônica do pipeline**."""
    requested = set(transformations)
    unknown = requested - set(_PIPELINE_ORDER)
    if unknown:
        raise ValueError(f"transformação desconhecida: {sorted(item.value for item in unknown)}")
    if Transformation.REDACT not in requested:
        raise ValueError(
            "a redação de segredo não é opcional ([02] §5: aplicada antes do hash); "
            "`transformations` precisa conter `redact`"
        )
    return tuple(item for item in _PIPELINE_ORDER if item in requested)


# ------------------------------------------------------------------ camada 1 (estrutural)


def _scalar_text(value: Any) -> str:
    """Escalar → texto. `bool` antes de `int`: em Python `True` **é** `int`."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _collect_leaves(
    value: Any,
    path: str,
    sensitive: bool,
    depth: int,
    out: list[_Leaf],
) -> None:
    """Percorre `structured` como **árvore**, propagando sensibilidade para baixo.

    Uma chave que `is_sensitive_key` reconhece marca a subárvore inteira: a marca desce
    por `dict`, por `list`, por índice e por qualquer chave intermediária, em qualquer
    profundidade. É o que fecha `E5-AUD2-004` — `{"password": ["hunter2"]}` e
    `{"password": {"current": "hunter2"}}` linearizavam para `password[0]`/
    `password.current`, e o índice ou a subchave depois de `password` destruíam a
    adjacência de que o regex dependia. Aqui a classificação não depende de adjacência
    textual nenhuma: ela é decidida na árvore, antes de existir texto.
    """
    if depth > _MAX_STRUCTURED_DEPTH:
        raise _Unrenderable(f"`structured` mais profundo que {_MAX_STRUCTURED_DEPTH} níveis")

    if isinstance(value, dict):
        for key, item in value.items():
            key_text = key if isinstance(key, str) else str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            _collect_leaves(
                item, child_path, sensitive or is_sensitive_key(key_text), depth + 1, out
            )
        if not value and path:
            out.append(_Leaf(path=path, text="", sensitive=sensitive, generic_scalar=True))
        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            _collect_leaves(item, f"{path}[{index}]", sensitive, depth + 1, out)
        if not value and path:
            out.append(_Leaf(path=path, text="", sensitive=sensitive, generic_scalar=True))
        return

    if isinstance(value, str):
        out.append(_Leaf(path=path, text=value, sensitive=sensitive, generic_scalar=False))
        return

    if value is None or isinstance(value, bool | int | float):
        out.append(
            _Leaf(path=path, text=_scalar_text(value), sensitive=sensitive, generic_scalar=True)
        )
        return

    raise _Unrenderable(f"tipo não linearizável em `structured`: {type(value).__name__}")


def _leaves_of(structured: Any) -> list[_Leaf]:
    """As folhas de `structured`, em ordem canônica por `path`."""
    if structured is None:
        return []
    out: list[_Leaf] = []
    _collect_leaves(structured, "", False, 0, out)
    return sorted(out, key=lambda leaf: (leaf.path, leaf.text))


# ------------------------------------------------------------------ camada 2 (posicional)


def _build_fragments(title: str, body: str, leaves: list[_Leaf]) -> list[_Fragment]:
    """Título, corpo e, por folha, rótulo e valor — a ordem canônica da projeção.

    Rótulos entram como fragmento porque **são conteúdo autoral**: `E5-AUD2-003` provou
    que uma chave pode carregar o segredo inteiro.
    """
    fragments = [
        _Fragment(role="title", leaf_index=-1, text=title, propagatable=True),
        _Fragment(role="body", leaf_index=-1, text=body, propagatable=True),
    ]
    for index, leaf in enumerate(leaves):
        fragments.append(
            _Fragment(role="label", leaf_index=index, text=leaf.path, propagatable=True)
        )
        fragments.append(
            _Fragment(
                role="value",
                leaf_index=index,
                text=leaf.text,
                propagatable=not leaf.generic_scalar,
            )
        )
    return fragments


def _fragment_indices(leaf_index: int) -> tuple[int, int]:
    """`(índice do rótulo, índice do valor)` da folha `leaf_index` em `_build_fragments`."""
    return 2 + leaf_index * 2, 3 + leaf_index * 2


def _mark_fragments(
    fragments: list[_Fragment], leaves: list[_Leaf]
) -> tuple[set[int], dict[int, list[SecretSpan]], bool]:
    """Marca fragmentos para redação. Devolve `(inteiros, spans locais, cross_fragment)`.

    `inteiros` são fragmentos que saem por completo — porque a camada 1 os classificou
    sensíveis, ou porque a chave carrega o segredo e não há rótulo apresentável. `spans
    locais` são recortes dentro de um fragmento, que preservam o texto legítimo ao redor.
    `cross_fragment` diz se **alguma** marca veio de detecção que atravessa fronteira — é
    só sinalização de auditoria (ver `CROSS_FRAGMENT_REDACTION`), nunca entra em decisão
    de redação.

    **Travessia não produz marca inteira** (`E5-AUD4-002`). Produz o recorte de `_clip`:
    a interseção de `replacement_span` com cada fragmento. Marcar os dois fragmentos
    inteiros apagava, junto, o rótulo que o próprio motor identificou como contexto
    preservado — `password: ` e `https://` sumiam do texto sem que um só caractere deles
    fosse segredo.

    **Nenhuma comparação de quantidade entre leituras.** Cada leitura só acrescenta
    marcas; nenhuma decide olhando quantas marcas outra fez.

    ## Qual intervalo cada leitura usa (`E5-AUD3-002`)

    `SecretSpan` tem dois intervalos, e confundi-los foi o vazamento da rodada 3:

    * as leituras **entre** fragmentos — (d) e (e) — perguntam *de onde a detecção veio*,
      e isso é `recognition_span`: em `Bearer ABCDEFGHIJKLMNOP` partido entre título e
      corpo, o `replacement_span` cobre só `ABCDEFGHIJKLMNOP`, inteiramente dentro do
      corpo, e a fronteira parecia intocada. O `recognition_span` inclui a palavra
      `Bearer` — que está no outro fragmento — então a travessia aparece;
    * as leituras **dentro** de um fragmento — (b) e (c) — decidem *o que apagar*, e isso
      é `replacement_span`. Usar `recognition_span` em (c) faria `password: hunter2`
      marcar o próprio rótulo `password` como segredo, e a linha viraria
      `<chave protegida>` em vez de `password: «redigido»` — o nome do campo é o contexto
      que prova a detecção, não parte do segredo.

    Depois que (d)/(e) decidem *que* houve travessia, quem decide *o que apagar em cada
    fragmento* volta a ser `replacement_span`, via `_clip`. As duas perguntas são
    distintas e cada intervalo responde exatamente uma delas.
    """
    whole: set[int] = set()
    local: dict[int, list[SecretSpan]] = {}
    cross_fragment = False

    def add_local(index: int, span: SecretSpan) -> None:
        local.setdefault(index, []).append(span)

    # (a) Camada 1: valor sob subárvore sensível sai inteiro, sem olhar o conteúdo.
    for leaf_index, leaf in enumerate(leaves):
        if leaf.sensitive:
            whole.add(_fragment_indices(leaf_index)[1])

    # (b) Cada fragmento isolado — segredo autocontido num campo só.
    own: dict[int, tuple[SecretSpan, ...]] = {
        index: detect_secret_spans(fragment.text) if fragment.text else ()
        for index, fragment in enumerate(fragments)
    }
    for index in range(len(fragments)):
        if index in whole:
            continue
        for span in own[index]:
            add_local(index, span)

    # (c) A linha `caminho: valor`. É a adjacência que `assigned_secret` precisa para
    # reconhecer `password: hunter2` — reconstruída aqui e mapeada de volta **por
    # posição**, nunca recortada por delimitador (`E5-AUD2-003`).
    for leaf_index, leaf in enumerate(leaves):
        label_index, value_index = _fragment_indices(leaf_index)
        line = f"{leaf.path}{_LABEL_SEPARATOR}{leaf.text}"
        value_offset = len(leaf.path) + len(_LABEL_SEPARATOR)
        for span in detect_secret_spans(line):
            replacement = span.replacement_span
            if replacement.start < len(leaf.path):
                # O que seria **apagado** alcança o rótulo: a chave carrega segredo.
                whole.add(label_index)
            if replacement.end > value_offset:
                if replacement.start < value_offset:
                    # Atravessa rótulo e valor: não dá para dizer onde termina o segredo.
                    whole.add(value_index)
                else:
                    add_local(
                        value_index,
                        SecretSpan(
                            recognition_span=Interval(
                                start=max(0, span.recognition_span.start - value_offset),
                                end=span.recognition_span.end - value_offset,
                            ),
                            replacement_span=Interval(
                                start=replacement.start - value_offset,
                                end=replacement.end - value_offset,
                            ),
                            pattern_name=span.pattern_name,
                        ),
                    )

    # (d) Cada **par ordenado** de fragmentos, colados sem separador. É o que fecha
    # `E5-AUD2-001`: um token partido entre `body` e uma folha é reconhecido mesmo que a
    # ordem canônica ponha outra folha entre os dois. Só reconhecimento que **atravessa**
    # a fronteira conta — o que cabe inteiro num fragmento já veio de (b).
    texts = [fragment.text for fragment in fragments]
    for left, right in itertools.permutations(range(len(fragments)), 2):
        if not texts[left] or not texts[right]:
            continue
        boundary = len(texts[left])
        glued = texts[left] + texts[right]
        for span in detect_secret_spans(glued):
            if not span.recognition_span.crosses(boundary):
                continue
            windows = ((left, 0, boundary), (right, boundary, len(glued)))
            for index, start, end in windows:
                piece = _clip(span, start, end)
                if piece is not None:
                    add_local(index, piece)
                    cross_fragment = True

    # (e) A projeção canônica inteira — corrida contígua cobrindo três ou mais fragmentos,
    # que o par a par não enxerga.
    projection = "".join(texts)
    bounds: list[tuple[int, int, int]] = []
    cursor = 0
    for index, text in enumerate(texts):
        bounds.append((cursor, cursor + len(text), index))
        cursor += len(text)
    for span in detect_secret_spans(projection):
        recognition = span.recognition_span
        covered = [
            index
            for start, end, index in bounds
            if start < recognition.end and recognition.start < end
        ]
        if len(covered) <= 1:
            continue
        for start, end, index in bounds:
            piece = _clip(span, start, end)
            if piece is not None:
                add_local(index, piece)
                cross_fragment = True

    return whole, local, cross_fragment


def _clip(span: SecretSpan, start: int, end: int) -> SecretSpan | None:
    """`span` recortado para a janela `[start, end)`, em coordenadas locais dela.

    **É isto que substituiu a marca de fragmento inteiro na travessia** (`E5-AUD4-002`).
    `recognition_span` já respondeu *se* houve travessia; `replacement_span` responde *o
    que* apagar, e a interseção dele com cada janela responde *quanto disso caiu neste
    fragmento*. O resultado, fragmento a fragmento, é exatamente o que `redact()`
    produziria sobre o texto colado — recortado de volta na fronteira.

    Devolve `None` quando a substituição não alcança esta janela: o fragmento participou
    do **reconhecimento** sem que nenhum caractere dele seja segredo. É o caso de
    `title="Bearer"` / `body=" ABCDEFGHIJKLMNOP"`, onde a palavra `Bearer` prova a
    credencial e continua legível, e de `title="https://"`, onde o `://` faz o mesmo.
    """
    replacement_start = max(span.replacement_span.start, start)
    replacement_end = min(span.replacement_span.end, end)
    if replacement_end <= replacement_start:
        return None
    return SecretSpan(
        recognition_span=Interval(
            start=max(span.recognition_span.start, start) - start,
            end=min(span.recognition_span.end, end) - start,
        ),
        replacement_span=Interval(start=replacement_start - start, end=replacement_end - start),
        pattern_name=span.pattern_name,
    )


# ----------------------------------------------------------------- camada 3 (propagação)


def _propagation_values(
    fragments: list[_Fragment], whole: set[int], local: dict[int, list[SecretSpan]]
) -> tuple[str, ...]:
    """Os textos **comprovadamente** sensíveis, para redigir toda ocorrência literal deles.

    Duas origens: o fragmento inteiro, quando ele saiu inteiro e é propagável; e o recorte
    exato de cada span, quando a detecção isolou o segredo dentro de um fragmento maior.

    Escalar genérico não entra (`propagatable=False`) e nada abaixo de
    `_MIN_PROPAGATION_LENGTH` entra: propagar `"42"` ou `"true"` redigiria coincidência,
    não segredo.

    **`propagatable` vale nas duas origens** (`E5-AUD3-003`). A versão anterior consultava
    o atributo só ao acrescentar o fragmento inteiro; o laço dos recortes entrava sem
    checar, e `{"password": 123456}` mandava `123456` para o conjunto global pela segunda
    porta — apagando toda contagem pública igual no documento. O número continua redigido
    **localmente** sob a chave sensível (camada 1); o que ele não faz é tornar suspeito
    todo `123456` que apareça em outro lugar.

    O recorte sai de `replacement_span`, não de `recognition_span`: o que se propaga é o
    **valor** do segredo, não o rótulo que o provou. Propagar `recognition_span` de
    `password: hunter2` poria a string `password: hunter2` inteira no conjunto, e o
    `password` legítimo de outro campo viraria `«redigido»`.

    Ordenados do mais longo para o mais curto, para que a substituição de um valor que
    contenha outro não deixe um pedaço do maior para trás.
    """
    values: set[str] = set()

    for index, fragment in enumerate(fragments):
        if not fragment.propagatable:
            continue
        if index in whole and len(fragment.text) >= _MIN_PROPAGATION_LENGTH:
            values.add(fragment.text)
        for span in local.get(index, ()):
            piece = fragment.text[span.replacement_span.start : span.replacement_span.end]
            if len(piece) >= _MIN_PROPAGATION_LENGTH:
                values.add(piece)

    return tuple(sorted(values, key=lambda value: (-len(value), value)))


# ------------------------------------------------------------------------- montagem final


def _redacted_text(
    fragment: _Fragment,
    index: int,
    whole: set[int],
    local: dict[int, list[SecretSpan]],
    propagation: tuple[str, ...],
) -> tuple[str, bool]:
    """O texto final de um fragmento, e se **esta chamada** rediglu algo por propagação.

    O booleano é só para `PROPAGATED_SECRET_REDACTION` (`E5-AUD5-001`) — nunca carrega o
    valor, a posição nem quantas vezes. `index in whole` devolve `False`: aquele
    fragmento saiu inteiro pela camada 1 ou pela leitura (c), não por cópia de um valor
    já visto em outro lugar do bloco.
    """
    if index in whole:
        return REDACTED, False

    text = fragment.text
    spans = local.get(index)
    if spans:
        parts: list[str] = []
        cursor = 0
        for start, end in merge_spans(spans):
            parts.append(text[cursor:start])
            parts.append(REDACTED)
            cursor = end
        parts.append(text[cursor:])
        text = "".join(parts)

    propagated = False
    for value in propagation:
        if value and value in text:
            propagated = True
            text = text.replace(value, REDACTED)

    return text, propagated


def _fail_closed_block(
    entry: ContextRegistryEntry, applied: tuple[Transformation, ...]
) -> RenderedBlock:
    """Bloco inteiramente redigido — a resposta quando não dá para provar segurança.

    Não é melhor esforço: título, corpo e `structured` saem todos como marcador. Um
    `structured` profundo demais, grande demais ou de tipo não linearizável é raro o
    bastante para que perder o bloco seja preferível a emitir metade de um segredo.

    **Sem** `CROSS_FRAGMENT_REDACTION` nem `PROPAGATED_SECRET_REDACTION`: este bloco não
    foi redigido por detecção que atravessa fronteira nem por cópia de valor já
    conhecido — foi redigido por não ter sido possível analisá-lo. Misturar qualquer um
    dos dois tornaria o marcador inútil justamente para a pergunta que ele existe para
    responder.
    """
    text = f"### contexto · {entry.domain.value}\n\n## {REDACTED}\n\n{REDACTED}\n"
    return RenderedBlock(
        text=unicodedata.normalize("NFC", text),
        title=REDACTED,
        transformations=tuple(item.value for item in applied),
    )


def render_block_text(
    entry: ContextRegistryEntry,
    transformations: tuple[Transformation, ...] | list[Transformation] = DEFAULT_TRANSFORMATIONS,
) -> RenderedBlock:
    """O bloco emitido para uma entrada. **Determinístico e único.**

    Chamar duas vezes com a mesma entrada e as mesmas transformações produz exatamente o
    mesmo `RenderedBlock` — nada aqui lê relógio, ambiente, disco ou ordem de iteração de
    dict que não tenha sido explicitamente ordenada primeiro.
    """
    applied = normalize_transformations(transformations)
    normalize = Transformation.NORMALIZE in applied

    title = normalize_text(entry.title) if normalize else entry.title
    body = normalize_text(entry.body) if normalize else entry.body

    try:
        structured = (
            (normalize_structured(entry.structured) if normalize else entry.structured)
            if entry.structured is not None
            else None
        )
        leaves = _leaves_of(structured)
        fragments = _build_fragments(title, body, leaves)
        if len(fragments) > _MAX_FRAGMENTS:
            raise _Unrenderable(f"`structured` com mais de {_MAX_FRAGMENTS} fragmentos")
        whole, local, cross_fragment = _mark_fragments(fragments, leaves)
    except _Unrenderable:
        return _fail_closed_block(entry, applied)

    propagation = _propagation_values(fragments, whole, local)
    propagated = False

    def final(index: int) -> str:
        nonlocal propagated
        text, hit = _redacted_text(fragments[index], index, whole, local, propagation)
        propagated = propagated or hit
        return text

    title_final = final(0)
    body_final = final(1)

    lines: list[str] = []
    for leaf_index, leaf in enumerate(leaves):
        label_index, value_index = _fragment_indices(leaf_index)
        label_final = final(label_index)
        # O rótulo só é apresentado quando atravessou a redação **intacto** e não contém
        # o separador reservado. Qualquer alteração — span, marca inteira, propagação —
        # significa que ele carregava segredo, e aí vai o marcador fixo, nunca um
        # caminho parcialmente reconstruído (`E5-AUD2-003`).
        if label_final != leaf.path or _LABEL_SEPARATOR in leaf.path:
            lines.append(f"{_PROTECTED_LABEL}{_LABEL_SEPARATOR}{REDACTED}")
        else:
            lines.append(f"{label_final}{_LABEL_SEPARATOR}{final(value_index)}")

    structured_section = "\n".join(lines)
    content = "\n\n".join(part for part in (body_final, structured_section) if part)

    # Framing: literal fixo mais `domain` (enum fechado) e o título **já redigido**.
    text = f"### contexto · {entry.domain.value}\n\n## {title_final}\n\n{content}\n"

    # NFC por último: `canonical_json` normaliza ao serializar o artefato, então medir
    # antes disto mediria uma forma que os bytes gravados não têm (`E5-AUD2-005`).
    names = tuple(item.value for item in applied)
    if cross_fragment:
        names = (*names, CROSS_FRAGMENT_REDACTION)
    if propagated:
        names = (*names, PROPAGATED_SECRET_REDACTION)

    return RenderedBlock(
        text=unicodedata.normalize("NFC", text),
        title=unicodedata.normalize("NFC", title_final),
        transformations=names,
    )


__all__ = [
    "CROSS_FRAGMENT_REDACTION",
    "DEFAULT_TRANSFORMATIONS",
    "PROPAGATED_SECRET_REDACTION",
    "RENDERER_VERSION",
    "RenderedBlock",
    "Transformation",
    "approx_tokens",
    "normalize_transformations",
    "render_block_text",
]
