"""Expansor canônico de `source_refs` — **o dono único da gramática de glob**.

Nenhum outro módulo do sistema interpreta `*`, `**`, `?`, `[]` ou `!` num `source_ref`.
Essa unicidade não é estilo: é o fechamento do buraco descrito em
`app/safety/source_refs.py` (E2-AUD-003), em que um validador aprovava `a/**/b` segundo
uma gramática e um expansor o resolvia segundo outra.

## Divisão de responsabilidade

**`safety.source_refs`** decide o **envelope de caminho** — `..`, absoluto, UNC, ADS, nome
reservado, `~`… — e aplica a denylist sobre o padrão tratado como literal. Ele **não**
decide o que um metacaractere significa.

**Este módulo** decide a **gramática**, e aplica a denylist arquivo a arquivo *depois* de
expandir. Ele **não** decide nenhuma regra de caminho seguro.

Este módulo **não reimplementa** nada do envelope: ele chama
`validate_source_ref(raw, allow_glob_syntax=True)` e, numa recusa, **propaga a
`SafetyDecision` verbatim** — mesmo `rule_id`, mesma razão. Toda recusa própria vive num
espaço de nomes separado (`source_ref_expansion.*`), então é sempre possível dizer, olhando
só o `rule_id`, qual camada negou.

## Gramática suportada

| Sintaxe | Casa |
| --- | --- |
| `*` | qualquer sequência de caracteres **exceto** `/` |
| `**` como segmento inteiro | **zero ou mais** segmentos de diretório |
| `**` colado a outro caractere | qualquer sequência, **incluindo** `/` |
| `?` | exatamente um caractere, **exceto** `/` |
| `[abc]` `[a-z]` | um caractere da classe — **nunca** `/` |
| `[!abc]` | um caractere **fora** da classe — e **nunca** `/` |
| qualquer outro | literal |

### `**` é **zero ou mais** segmentos, e por quê

`a/**/b` casa `a/b`, `a/x/b` e `a/x/y/b`. `docs/**/*.md` alcança tanto `docs/readme.md`
quanto `docs/sub/readme.md`. É a convenção de `gitignore`/`globstar`, e é a semântica
normativa deste módulo — nenhum ADR nem documento de arquitetura especifica a gramática a
nível de caractere, então a decisão é daqui, do dono único da gramática.

A primeira implementação traduzia todo `**` para `.*` e deixava as duas barras literais no
padrão (`a/.*/b`), o que exigia **pelo menos um** segmento intermediário. Isso foi
corrigido, e a justificativa merece ficar registrada porque contraria o reflexo usual:

> **Neste componente, "mais restritivo" não é "mais seguro".** O risco dominante aqui é a
> **subcobertura silenciosa** — um `source_ref` que o autor da entrada acredita cobrir um
> arquivo e não cobre. O `source_hash` passa então a não reagir a mudanças naquele arquivo,
> e a entrada fica `fresh` para sempre descrevendo código que mudou: um **falso negativo de
> staleness**, que é exatamente a classe de defeito que AUD-004 existe para impedir. O
> risco oposto — sobrecobertura — não fica desprotegido: ele tem o seu próprio freio, a
> checagem de segredo **arquivo a arquivo depois da expansão**, que não mudou e continua
> negando o `source_ref` inteiro se qualquer resultado for segredo.

`**` **colado** a outro caractere (`a**b`, `src/**.py`) não é um segmento e continua
atravessando `/`. Nenhum dialeto define esse caso de forma consensual; sob o modelo de
risco acima, a leitura mais abrangente é a menos perigosa.

Detalhe de implementação em `_append_double_star`: o `/` que segue um `**`-segmento é
consumido **junto** com ele, e o token `SEGSTAR` resultante — barra incluída — pode casar
vazio. Sem consumir a barra, o "zero segmentos" não teria como ser expresso.

**Nenhuma classe de caracteres casa `/`**, nem por faixa. Quem garante isso é `_matches`,
que testa `caractere != "/"` **fora** do predicado da classe: `[.-0]` contém `/` no meio da
faixa (`.`=0x2E, `/`=0x2F, `0`=0x30) e ainda assim não o casa (E4-AUD-008). Uma classe é
uma construção de segmento único, e atravessar o separador é o caso adversarial mais fácil
de deixar passar.

**Sem gramática inventada.** `{`, `}` e `!` fora de uma classe são **recusados**, não
tratados como literais: os três têm significado consagrado em outros dialetos (expansão de
chaves, negação de padrão) e adivinhar qual vale aqui é exatamente o erro que a E2 proibiu.
Um `[` sem fechamento também é recusado, em vez de virar literal como o `fnmatch` faria.

## Fluxo

1. `validate_source_ref(raw, allow_glob_syntax=True)` — recusa propaga o motivo;
2. compilação do padrão em tokens, casados contra os caminhos de
   `git_runtime.list_tree(verification_commit)` por `_matches` — programação dinâmica de
   custo `O(tokens × caminho)`, sem backtracking (E4-AUD-001);
3. um `source_ref` que casa **zero** arquivo torna a expansão inteira `UNRESOLVED` — que
   [03] §3 traduz em `state = unknown`. Tratar o vazio como conjunto legítimo produziria um
   `source_hash` sobre `[]`, que nunca mais mudaria: falso `fresh`;
4. `classify_path_secrecy` em **cada** arquivo resultante. Um único segredo nega o
   `source_ref` **inteiro** — nunca filtra parcialmente. Filtrar deixaria a entrada parecer
   íntegra enquanto descreve código que ninguém conferiu;
5. `source_hash` sobre a lista congelada e ordenada, conforme [03] §3.

Contrato de importação ([01] §2 + regra de módulo da E4): este pacote importa `db`,
`git_runtime` (leitura), `path_runtime`, `safety` e `config`. Nunca `agent_runtime`,
`tool_executor`, `orchestrator` ou `api`. `subprocess` continua confinado a `git_runtime/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.git_runtime import TreeListing, list_tree
from app.safety.canonical import canonical_sha256
from app.safety.policy import SafetyPolicy
from app.safety.secrets import SecretVerdict, classify_path_secrecy
from app.safety.source_refs import validate_source_ref
from app.safety.types import SafetyDecision

#: Caracteres cujo significado **não** está definido nesta gramática. Recusados, nunca
#: reinterpretados como literal (ver docstring do módulo).
_UNSUPPORTED_OUTSIDE_CLASS = frozenset("{}")


class ExpansionStatus(str, Enum):
    """Desfecho de uma expansão. Três, e não dois, porque "não sei" não é "vazio"."""

    #: Todo `source_ref` casou pelo menos um arquivo, e nenhum resultado é segredo.
    RESOLVED = "resolved"
    #: Recusa de segurança — envelope, gramática ou segredo. `decision` diz qual.
    DENIED = "denied"
    #: Árvore ilegível, `source_ref` que casou zero arquivo, ou resolução **incompleta**
    #: — há caminho que a leitura não soube nomear e que este ref poderia ter alcançado
    #: (E4-AUD5-001). Os três viram `unknown` em [03] §3.
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class SourceRefExpansion:
    """Resultado de expandir a lista de `source_refs` de uma entrada.

    ``files`` e ``source_hash`` só são significativos em ``RESOLVED``. ``decision`` só é
    preenchido em ``DENIED``; ``unresolved_refs`` só em ``UNRESOLVED``.
    """

    status: ExpansionStatus
    files: tuple[tuple[str, str], ...] = ()
    source_hash: str | None = None
    decision: SafetyDecision | None = None
    unresolved_refs: tuple[str, ...] = ()
    normalized_refs: tuple[str, ...] = ()
    #: Caminhos da árvore que o envelope de `source_ref` não sabe nomear (E4-AUD3-001),
    #: para diagnóstico. Preenchido em **qualquer** desfecho — a árvore estar incompleta é
    #: um fato sobre o repositório, não sobre o veredito desta entrada (E4-AUD5-001).
    unrepresentable_paths: tuple[str, ...] = ()
    #: `source_refs` cuja resolução ficou **incompleta**: existe caminho ilegível que este
    #: ref poderia ter alcançado, então não dá para afirmar que ele resolveu para tudo o que
    #: cobre (E4-AUD5-001). Distinto de ``unresolved_refs`` — lá o ref casou **zero**
    #: arquivo, aqui ele pode ter casado vários e ainda assim não estar completo.
    incomplete_refs: tuple[str, ...] = ()

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(path for path, _sha in self.files)


@dataclass(frozen=True, slots=True)
class CompiledSourceRef:
    """Um `source_ref` normalizado e os tokens que o representam.

    ``literal`` é `True` quando o padrão não tem metacaractere nenhum — o caso "caminho
    literal", que resolve para si mesmo se for arquivo e recursivamente se for diretório, e
    que nem chega a usar os tokens.
    """

    normalized: str
    tokens: _Compiled
    literal: bool
    #: Menor comprimento que o padrão pode casar. Filtro barato, ver `_minimum_length`.
    minimum_length: int = 0

    def covers(self, path: str) -> bool:
        """Este `source_ref` cobre `path`? **A única pergunta de casamento do sistema.**"""
        if self.literal:
            return _literal_covers(self.normalized, path)
        if len(path) < self.minimum_length:
            return False
        return _matches(self.tokens, path)


class _Token(str, Enum):
    """As seis coisas que um `source_ref` compilado pode conter. Nada mais existe."""

    LITERAL = "literal"  # um caractere exato
    ANY_ONE = "any_one"  # `?` — um caractere, nunca `/`
    STAR = "star"  # `*` — zero ou mais caracteres, nenhum deles `/`
    GLOBSTAR = "globstar"  # `**` colado a texto — zero ou mais caracteres, `/` incluído
    SEGSTAR = "segstar"  # `**/` como segmento — zero ou mais segmentos completos
    CHAR_CLASS = "char_class"  # `[abc]` / `[!abc]` — um caractere, nunca `/`


#: Um token: `(tipo, carga)`. A carga é o caractere no `LITERAL`, o predicado no
#: `CHAR_CLASS`, e `None` nos demais.
_Compiled = tuple[tuple[_Token, object], ...]

#: Teto defensivo de tokens por padrão. `prevalidate_path_syntax` já limita o `source_ref`
#: a `max_path_bytes` (260 por padrão), então este limite é inalcançável na prática — ele
#: existe para que o custo do casamento seja **explicitamente** limitado no código, e não
#: por consequência de outra regra que alguém pode afrouxar sem perceber.
_MAX_PATTERN_TOKENS = 512


def _class_predicate(ranges: tuple[tuple[str, str], ...], negated: bool) -> object:
    """Fecha um predicado de classe sobre as faixas já parseadas."""

    def matches(char: str) -> bool:
        inside = any(low <= char <= high for low, high in ranges)
        return not inside if negated else inside

    return matches


def _simplify(tokens: list[tuple[_Token, object]]) -> list[tuple[_Token, object]]:
    """Colapsa quantificadores adjacentes **provadamente** redundantes (E4-AUD-001).

    Camada de mitigação estrutural: menos quantificadores é menos ambiguidade. O casador
    desta fase não faz backtracking (ver `_matches`), então isto é economia, não correção —
    mas reduzir a superfície vale por si, e o teste de propriedade
    `test_simplificacao_nunca_muda_o_que_o_padrao_casa` garante que economia não vire bug.

    Só estas cinco reduções são feitas, e cada uma é uma igualdade de linguagens:

    | Sequência | Vira | Porque |
    | --- | --- | --- |
    | `STAR STAR` | `STAR` | dois trechos sem `/` concatenados são um trecho sem `/` |
    | `GLOBSTAR STAR` | `GLOBSTAR` | "qualquer coisa" seguido de "sem `/`" é "qualquer coisa" |
    | `STAR GLOBSTAR` | `GLOBSTAR` | idem, na outra ordem |
    | `GLOBSTAR GLOBSTAR` | `GLOBSTAR` | idempotente |
    | `SEGSTAR SEGSTAR` | `SEGSTAR` | zero-ou-mais segmentos, duas vezes, é zero-ou-mais |

    **`SEGSTAR STAR` não é reduzido**, e a tentação de reduzi-lo foi um bug real durante
    esta correção: `SEGSTAR` só termina numa fronteira de `/`, então ele **não** absorve o
    `*` seguinte. Descartar o `*` fazia `docs/**/*.md` deixar de casar `docs/readme.md` —
    exatamente a subcobertura que a semântica de `**` existe para evitar.
    """
    simplified: list[tuple[_Token, object]] = []
    for token in tokens:
        if not simplified:
            simplified.append(token)
            continue

        anterior, atual = simplified[-1][0], token[0]

        if atual is _Token.STAR and anterior in (_Token.STAR, _Token.GLOBSTAR):
            continue
        if atual is _Token.GLOBSTAR and anterior in (_Token.STAR, _Token.GLOBSTAR):
            simplified[-1] = token
            continue
        if atual is _Token.SEGSTAR and anterior is _Token.SEGSTAR:
            continue

        simplified.append(token)
    return simplified


def _minimum_length(tokens: _Compiled) -> int:
    """Menor comprimento que o padrão consegue casar. Filtro barato e **provadamente são**.

    Só `LITERAL`, `ANY_ONE` e `CHAR_CLASS` consomem obrigatoriamente um caractere; os três
    quantificadores podem casar vazio. Um candidato mais curto que essa soma não pode casar,
    e descartá-lo antes da DP não muda resposta nenhuma — só evita o trabalho.
    """
    consumidores = (_Token.LITERAL, _Token.ANY_ONE, _Token.CHAR_CLASS)
    return sum(1 for kind, _payload in tokens if kind in consumidores)


def _matches(tokens: _Compiled, path: str) -> bool:
    """Casa `tokens` contra `path` inteiro, por **programação dinâmica**.

    ## Por que não é uma regex (E4-AUD-001)

    A tradução anterior gerava regex como `[^/]*a[^/]*a[^/]*a…`, cuja ambiguidade faz o
    motor de backtracking explorar um número combinatório de repartições. Um padrão de 33
    caracteres, sintaticamente banal, levava mais de quatro segundos — e `re` do Python não
    tem *timeout*, então não havia como limitar o estrago por fora.

    Esta função troca o motor por uma DP clássica sobre `(token, posição)`:
    `atual[j]` responde "os tokens vistos até aqui casam os primeiros `j` caracteres?".
    Cada token é uma passada linear sobre a linha anterior, então o custo é **sempre**
    `O(tokens × len(path))` — não há caminho ruim a descobrir, porque não há escolha a
    refazer. É a garantia de tempo que a mitigação por tamanho de entrada não daria.

    ## Por que `/` sai por construção

    `ANY_ONE`, `STAR` e `CHAR_CLASS` testam `caractere != "/"` como condição **separada** do
    resto. Uma faixa como `[.-0]`, que contém `/` no meio (`.`=0x2E, `/`=0x2F, `0`=0x30),
    não tem como escapar disso: a checagem não olha o conteúdo da classe (E4-AUD-008). Era
    o buraco que uma classe traduzida para regex deixava aberto.
    """
    length = len(path)
    previous = [False] * (length + 1)
    previous[0] = True

    for kind, payload in tokens:
        current = [False] * (length + 1)

        if kind is _Token.LITERAL:
            for index in range(1, length + 1):
                current[index] = previous[index - 1] and path[index - 1] == payload

        elif kind is _Token.ANY_ONE:
            for index in range(1, length + 1):
                current[index] = previous[index - 1] and path[index - 1] != "/"

        elif kind is _Token.CHAR_CLASS:
            predicate = payload
            for index in range(1, length + 1):
                char = path[index - 1]
                current[index] = previous[index - 1] and char != "/" and predicate(char)  # type: ignore[operator]

        elif kind is _Token.STAR:
            current[0] = previous[0]
            for index in range(1, length + 1):
                current[index] = previous[index] or (current[index - 1] and path[index - 1] != "/")

        elif kind is _Token.GLOBSTAR:
            current[0] = previous[0]
            for index in range(1, length + 1):
                current[index] = previous[index] or current[index - 1]

        else:  # _Token.SEGSTAR — vazio, ou qualquer coisa terminada em `/`
            reachable_before = False
            for index in range(length + 1):
                if index > 0:
                    reachable_before = reachable_before or previous[index - 1]
                current[index] = previous[index] or (
                    index > 0 and path[index - 1] == "/" and reachable_before
                )

        if not any(current):
            return False  # nenhuma posição alcançável: o resto do padrão não pode salvar
        previous = current

    return previous[length]


def _deny(rule_id: str, reason: str, subject: str) -> SafetyDecision:
    return SafetyDecision(allow=False, rule_id=rule_id, reason=reason, subject_redacted=subject)


def compile_source_ref(normalized: str) -> CompiledSourceRef | SafetyDecision:
    """Traduz um `source_ref` **já normalizado** em tokens. `SafetyDecision` numa recusa.

    Recebe o valor **normalizado** por `safety.source_refs`, nunca o cru: a normalização é
    de lá, e duplicá-la aqui recriaria a divergência de gramática que este módulo existe
    para evitar.

    São duas etapas: `_tokenize` produz os tokens crus, `_simplify` colapsa quantificadores
    redundantes. A separação existe para que a segunda seja testável **contra** a primeira —
    `test_aud001_simplificacao_nunca_muda_o_que_o_padrao_casa` compara as duas formas sobre
    o mesmo corpus, e é o que pega uma redução que altera a linguagem em vez de só encurtar
    o padrão.
    """
    tokens = _tokenize(normalized)
    if isinstance(tokens, SafetyDecision):
        return tokens

    if not tokens:
        return CompiledSourceRef(normalized=normalized, tokens=(), literal=True)

    simplified = tuple(_simplify(tokens))
    if len(simplified) > _MAX_PATTERN_TOKENS:  # pragma: no cover — inalcançável com max_path_bytes
        return _deny(
            "source_ref_expansion.pattern_too_complex",
            f"padrão com mais de {_MAX_PATTERN_TOKENS} elementos",
            normalized,
        )

    return CompiledSourceRef(
        normalized=normalized,
        tokens=simplified,
        literal=False,
        minimum_length=_minimum_length(simplified),
    )


def _tokenize(normalized: str) -> list[tuple[_Token, object]] | SafetyDecision:
    """Tokens **crus**, sem simplificação. Lista vazia significa "caminho literal"."""
    tokens: list[tuple[_Token, object]] = []
    has_meta = False
    index = 0
    length = len(normalized)

    while index < length:
        char = normalized[index]

        if char in _UNSUPPORTED_OUTSIDE_CLASS:
            return _deny(
                "source_ref_expansion.unsupported_syntax",
                f"`{char}` não tem significado definido nesta gramática de glob; "
                "expansão de chaves não é suportada e tratá-lo como literal criaria "
                "uma segunda gramática",
                normalized,
            )

        if char == "!":
            return _deny(
                "source_ref_expansion.unsupported_syntax",
                "`!` só é aceito imediatamente após `[`, como negação de classe; "
                "negação de padrão inteiro não é suportada",
                normalized,
            )

        if char == "*":
            if normalized.startswith("**", index):
                index = _append_double_star(tokens, normalized, index)
            else:
                tokens.append((_Token.STAR, None))
                index += 1
            has_meta = True
            continue

        if char == "?":
            tokens.append((_Token.ANY_ONE, None))
            index += 1
            has_meta = True
            continue

        if char == "[":
            translated = _translate_class(normalized, index)
            if isinstance(translated, SafetyDecision):
                return translated
            predicate, index = translated
            tokens.append((_Token.CHAR_CLASS, predicate))
            has_meta = True
            continue

        tokens.append((_Token.LITERAL, char))
        index += 1

    # Sem metacaractere nenhum, o `source_ref` é um caminho literal e nem chega a usar
    # tokens: quem responde é `_literal_covers`.
    return tokens if has_meta else []


def _append_double_star(tokens: list[tuple[_Token, object]], normalized: str, index: int) -> int:
    """Traduz o `**` que começa em `index`. Devolve o índice logo depois do que consumiu.

    Três formas, e a distinção entre elas é **posicional** — `**` só tem significado de
    "atravessa diretórios como segmento" quando ele *é* um segmento inteiro:

    | Forma | Exemplo | Token | Casa |
    | --- | --- | --- | --- |
    | segmento, com `/` depois | `a/**/b` | `SEGSTAR`, consumindo o `/` | zero ou mais segmentos |
    | segmento, no fim | `src/**`, `**` | `GLOBSTAR` | tudo abaixo do prefixo |
    | colado a outro caractere | `a**b`, `src/**.py` | `GLOBSTAR` | qualquer coisa, inclusive `/` |

    A primeira linha é o que faz `a/**/b` casar `a/b`: o `/` seguinte é consumido junto com
    o `**`, e o `SEGSTAR` inteiro — barra incluída — pode casar vazio.

    A terceira continua atravessando `/`: `**` colado a texto é ambíguo em qualquer dialeto,
    e sob o modelo de risco deste módulo — subcobertura é o erro caro — a leitura mais
    abrangente é a menos perigosa. A sobrecobertura tem o seu próprio freio, a checagem de
    segredo arquivo a arquivo, que roda depois da expansão.
    """
    after = index + 2
    starts_segment = index == 0 or normalized[index - 1] == "/"
    ends_segment = after >= len(normalized) or normalized[after] == "/"

    if starts_segment and ends_segment and after < len(normalized):
        tokens.append((_Token.SEGSTAR, None))
        return after + 1

    tokens.append((_Token.GLOBSTAR, None))
    return after


def _translate_class(value: str, start: int) -> tuple[object, int] | SafetyDecision:
    """Parseia `[...]` a partir de `value[start] == '['`. Devolve `(predicado, índice)`.

    Regras, todas fechadas para o lado seguro:

    * `[` sem `]` correspondente é **recusado**. O `fnmatch` o trataria como literal, e um
      `source_ref` que significa uma coisa aqui e outra em qualquer outra ferramenta é
      justamente o que não pode existir.
    * `]` como primeiro caractere do corpo é literal (convenção POSIX), então `[]]` é a
      classe que casa `]`.
    * corpo vazio (`[]`, `[!]`) é recusado — não casa nada e quase certamente é erro de
      digitação, não intenção.
    * faixa invertida (`[z-a]`) é recusada.
    * `/` **não** pode aparecer literalmente na classe, e — mais importante — nenhuma classe
      consegue casar `/` nem por faixa: quem garante isso é `_matches`, que testa
      `caractere != "/"` **fora** do predicado (E4-AUD-008). Uma faixa como `[.-0]` contém
      `/` no meio e ainda assim não o casa.
    """
    index = start + 1
    negated = False
    if index < len(value) and value[index] == "!":
        negated = True
        index += 1

    body: list[str] = []
    if index < len(value) and value[index] == "]":
        body.append("]")  # POSIX: `]` logo no início do corpo é literal
        index += 1

    closed = False
    while index < len(value):
        char = value[index]
        if char == "]":
            closed = True
            index += 1
            break
        body.append(char)
        index += 1

    if not closed:
        return _deny(
            "source_ref_expansion.invalid_character_class",
            "classe de caracteres sem `]` de fechamento; recusada em vez de virar literal",
            value,
        )

    if not body:
        return _deny(
            "source_ref_expansion.invalid_character_class",
            "classe de caracteres vazia não casa nada",
            value,
        )

    if "/" in body:
        return _deny(
            "source_ref_expansion.invalid_character_class",
            "`/` dentro de classe de caracteres: uma classe é de segmento único e nunca "
            "pode casar o separador",
            value,
        )

    ranges: list[tuple[str, str]] = []
    position = 0
    while position < len(body):
        # `a-z` é faixa; um `-` no início ou no fim do corpo é literal.
        if position + 2 < len(body) and body[position + 1] == "-":
            low, high = body[position], body[position + 2]
            if low > high:
                return _deny(
                    "source_ref_expansion.invalid_character_class",
                    f"faixa invertida `{low}-{high}` na classe de caracteres",
                    value,
                )
            ranges.append((low, high))
            position += 3
            continue
        ranges.append((body[position], body[position]))
        position += 1

    return _class_predicate(tuple(ranges), negated), index


def _literal_covers(ref: str, path: str) -> bool:
    """Caminho literal: cobre a si mesmo, e recursivamente tudo abaixo se for diretório.

    O teste do diretório é por **segmento** (`ref + "/"`), nunca por prefixo de string: sem
    a barra, `src` arrastaria `src2/outro.py` e `srcx.py` junto.
    """
    return path == ref or path.startswith(f"{ref}/")


#: Caracteres a partir dos quais o padrão deixa de ser literal. `{`/`}` não entram porque
#: já foram recusados pelo tokenizador, e `!` só tem significado dentro de uma classe.
_PATTERN_METACHARACTERS = frozenset("*?[")


def _literal_prefix(normalized: str) -> str:
    """O trecho inicial do padrão que casa **literalmente**, até o primeiro metacaractere.

    Todo caminho que este padrão casa começa por este trecho: os caracteres antes do
    primeiro metacaractere viram tokens `LITERAL`, e o casamento é ancorado no início. É a
    única afirmação sobre casamento que continua verdadeira quando o alvo **não é texto**.
    """
    for index, char in enumerate(normalized):
        if char in _PATTERN_METACHARACTERS:
            return normalized[:index]
    return normalized


def _may_cover(ref: CompiledSourceRef, raw: bytes) -> bool:
    """Este `source_ref` **poderia** cobrir um caminho ilegível, dado só os bytes dele?

    ## Por que esta pergunta existe (E4-AUD5-001)

    Um caminho que o registro não sabe nomear — barra invertida literal, ou bytes que não
    são UTF-8 — não pode passar por `covers()`: a gramática é definida sobre texto, e não há
    texto. Até a 4ª rodada a consequência era coletiva: **qualquer** caminho ilegível na
    árvore tornava **toda** expansão irresolvível. Isso é grosseiro demais em dois sentidos
    opostos ao mesmo tempo — apagava a classificação de segredo dos arquivos legíveis
    (E4-AUD5-001), e ao mesmo tempo marcava como incerto um `source_ref` que
    demonstravelmente não tem nada a ver com o arquivo ilegível.

    A pergunta certa é por **ref**, e ela é decidível em bytes sem interpretar gramática
    nenhuma sobre o alvo:

    * **ref literal** — cobre a si mesmo e o que está abaixo dele como diretório. A mesma
      regra de `_literal_covers`, byte a byte: `raw == ref` ou `raw` começa com `ref + "/"`.
      Comparação de bytes é exata, e não precisa que `raw` signifique alguma coisa.
    * **ref com glob** — todo caminho que ele casa começa pelo seu `_literal_prefix`, porque
      esses caracteres viram tokens `LITERAL` ancorados no início. Se `raw` não começa por
      esses bytes, o ref **não pode** casá-lo. Se começa, não dá para saber — e aí a
      resposta é sim, conservadora por construção.

    O resultado é uma sobre-aproximação **segura**: nunca diz "não pode" quando poderia.
    """
    if ref.literal:
        target = ref.normalized.encode("utf-8")
        return raw == target or raw.startswith(target + b"/")
    return raw.startswith(_literal_prefix(ref.normalized).encode("utf-8"))


@dataclass(frozen=True, slots=True)
class SourceRefMatcher:
    """Os `source_refs` compilados, para perguntar "este caminho é coberto?".

    Existe para a **Parte B** de [03] §3: a divergência do working tree precisa ser cruzada
    com a cobertura, e a cobertura tem de ser calculada pela **mesma** gramática que a
    Parte A usou. Um arquivo `untracked` novo em `src/` é coberto por `src/**` mesmo sem
    existir na árvore do `verification_commit` — comparar apenas com a lista já expandida
    perderia exatamente esse caso, que é o mais comum de todos.
    """

    compiled: tuple[CompiledSourceRef, ...]

    def covers(self, path: str) -> bool:
        return any(ref.covers(path) for ref in self.compiled)

    def may_cover_unreadable(self, raw: bytes) -> bool:
        """Algum destes refs **poderia** cobrir estes bytes ilegíveis? Ver `_may_cover`."""
        return any(_may_cover(ref, raw) for ref in self.compiled)


def build_matcher(
    raw_refs: list[str],
    *,
    policy: SafetyPolicy | None = None,
) -> SourceRefMatcher | SafetyDecision:
    """Compila `raw_refs` num `SourceRefMatcher`. Mesma validação e mesma gramática.

    Delega a `validate_and_compile`, que é a etapa (a) — envelope e gramática — usada
    também pela expansão e pela escrita de baseline. Uma segunda passagem de validação aqui
    seria uma segunda chance de divergir.
    """
    compiled = validate_and_compile(raw_refs, policy=policy)
    if isinstance(compiled, SafetyDecision):
        return compiled
    return SourceRefMatcher(compiled=compiled)


def validate_and_compile(
    raw_refs: list[str],
    *,
    policy: SafetyPolicy | None = None,
) -> tuple[CompiledSourceRef, ...] | SafetyDecision:
    """**Etapa (a): envelope + gramática.** Roda sempre, com ou sem git (E4-AUD-004).

    As duas etapas da validação de um `source_ref` são independentes e têm pré-requisitos
    diferentes:

    * **(a) envelope e gramática** — `..`, absoluto, UNC, ADS, nome reservado, `~`, e depois
      a sintaxe do glob. Não depende de repositório nenhum: é análise da string. **Sempre
      roda.**
    * **(b) resolução contra a árvore e classificação de segredo** — precisa de um commit
      legível. Sem ele, o estado da entrada vira `unknown`.

    Misturar as duas foi o defeito de E4-AUD-004: como a resolução era impossível sem
    commit, a validação inteira era pulada, e um workspace sem git aceitava `../**` e
    `.env*` com `201`. "Não consigo verificar contra o quê" nunca foi motivo para deixar de
    conferir a forma.
    """
    active = policy or SafetyPolicy()
    compiled: list[CompiledSourceRef] = []

    for raw in raw_refs:
        # Envelope: a recusa é propagada verbatim, sem reavaliar nem reescrever nada.
        validated = validate_source_ref(raw, policy=active, allow_glob_syntax=True)
        if not validated.decision.allow or validated.normalized is None:
            return validated.decision

        # Gramática: este módulo é o único lugar do sistema onde ela vive.
        result = compile_source_ref(validated.normalized)
        if isinstance(result, SafetyDecision):
            return result
        compiled.append(result)

    return tuple(compiled)


def expand_source_refs(
    raw_refs: list[str],
    tree: TreeListing | None,
    *,
    policy: SafetyPolicy | None = None,
) -> SourceRefExpansion:
    """Expande `raw_refs` contra `tree`. **Função pura** — não lê git, não lê disco.

    ``tree`` é a saída de `git_runtime.list_tree(local_path, verification_commit)`: um
    `TreeListing` com os arquivos legíveis **e** os caminhos que não deu para nomear, ou
    `None` quando não deu para ler nada. Recebê-la como parâmetro em vez de buscá-la aqui é
    o que permite exercitar a gramática contra árvores adversariais sem montar um
    repositório para cada caso — e é o que a E6 vai reaproveitar passando o
    `planning_base_commit`.

    Lista de `raw_refs` **vazia** devolve `RESOLVED` com zero arquivo e `source_hash=None`:
    é a entrada autoral de [03] §3, que nasce `fresh` sem baseline. Um `source_ref` que
    existe e casa zero arquivo é outra coisa — `UNRESOLVED`.

    ## Ordem dos desfechos

    **`DENIED` > incompleto (`UNRESOLVED`) > `RESOLVED`.** Uma regra só, com duas histórias
    de auditoria por trás:

    * `DENIED` vence `UNRESOLVED` desde E4-AUD-005. A versão de então decidia `UNRESOLVED`
      assim que um `source_ref` não casava nada, e voltava sem nunca classificar os arquivos
      que os **outros** refs tinham encontrado: `["config/*", "missing/**"]` com um
      `config/.env.local` na árvore era aceito, porque o `missing/**` interrompia o fluxo
      antes da checagem de segredo. Um erro de digitação num ref desligava a proteção do
      outro.
    * `DENIED` vence **incompleto** desde E4-AUD5-001, pelo mesmo motivo com outra roupa. A
      versão de então recebia da leitura ou os arquivos, ou o aviso de que havia caminho
      ilegível — nunca os dois. Um arquivo com nome inválido em qualquer canto da árvore
      apagava a lista inteira, e com ela a recusa do `.env` que estava **ao lado**, legível
      e resolvido. Ignorância sobre um arquivo virava ignorância sobre todos.

    A forma geral é a mesma nas duas: **o que já se sabe nunca é apagado pelo que não se
    sabe.** A classificação de segredo roda sobre tudo o que foi encontrado, **antes** de
    qualquer decisão sobre o que não foi encontrado ou não pôde ser lido.
    """
    active = policy or SafetyPolicy()

    if not raw_refs:
        return SourceRefExpansion(status=ExpansionStatus.RESOLVED, files=(), source_hash=None)

    # Etapa (a), sempre — inclusive quando não há árvore para resolver contra.
    compiled = validate_and_compile(raw_refs, policy=active)
    if isinstance(compiled, SafetyDecision):
        return SourceRefExpansion(status=ExpansionStatus.DENIED, decision=compiled)

    normalized_refs = tuple(ref.normalized for ref in compiled)

    if tree is None:
        return SourceRefExpansion(
            status=ExpansionStatus.UNRESOLVED,
            unresolved_refs=normalized_refs,
            normalized_refs=normalized_refs,
        )

    tree_paths = tuple(path for path, _sha in tree.files)
    blob_by_path = dict(tree.files)
    unreadable = tree.unrepresentable
    unreadable_display = tuple(sorted({item.display for item in unreadable}))

    # Defesa em profundidade: nada que o git devolva deveria ter esta forma, mas se tiver,
    # a expansão não é a camada certa para "consertar" — ela recusa.
    for path in tree_paths:
        if path.startswith("/") or ".." in path.split("/"):
            return SourceRefExpansion(
                status=ExpansionStatus.DENIED,
                decision=_deny(
                    "source_ref_expansion.malformed_tree_path",
                    "a árvore do commit contém um caminho absoluto ou com `..`",
                    path,
                ),
            )

    # Etapa (b): resolução. Nada de decidir desfecho aqui dentro — só coletar.
    matched: set[str] = set()
    unresolved: list[str] = []
    incomplete: list[str] = []
    for ref in compiled:
        hits = [path for path in tree_paths if ref.covers(path)]
        if not hits:
            # Casar zero arquivo é "irresolvível", não "conjunto vazio". A diferença é o que
            # separa `unknown` de um `source_hash` sobre `[]` que nunca mais mudaria.
            unresolved.append(ref.normalized)
        else:
            matched.update(hits)
        # Independente de ter casado ou não: este ref poderia ter alcançado algum caminho
        # que a leitura não soube nomear? Se sim, o que ele resolveu está **incompleto**, e
        # um `source_hash` sobre isso seria um falso `fresh` esperando acontecer. A pergunta
        # é por ref e decidida em bytes — ver `_may_cover` (E4-AUD5-001).
        if any(_may_cover(ref, item.raw) for item in unreadable):
            incomplete.append(ref.normalized)

    # A decisão de segredo que **vale**: arquivo por arquivo, sobre tudo o que foi
    # encontrado, e **antes** de olhar para o que não foi (E4-AUD-005) ou para o que não deu
    # para ler (E4-AUD5-001). Um único segredo nega tudo. Filtrar parcialmente deixaria a
    # entrada parecendo íntegra enquanto descreve código que ninguém conferiu, e o caminho
    # excluído sumiria em silêncio — [ADR-0006] item 9 exige o oposto: segredo aparece como
    # exclusão explícita.
    secret_policy = active.secret_policy()
    for path in sorted(matched):
        classification = classify_path_secrecy(path, secret_policy)
        if classification.verdict is SecretVerdict.SECRET:
            return SourceRefExpansion(
                status=ExpansionStatus.DENIED,
                decision=_deny(
                    "source_ref_expansion.secret_denied",
                    "a expansão alcança um caminho classificado como segredo "
                    f"(padrão `{classification.matched_pattern}`); o `source_ref` inteiro "
                    "é recusado, sem filtragem parcial",
                    path,
                ),
            )

    if unresolved or incomplete:
        return SourceRefExpansion(
            status=ExpansionStatus.UNRESOLVED,
            unresolved_refs=tuple(sorted(set(unresolved))),
            incomplete_refs=tuple(sorted(set(incomplete))),
            normalized_refs=normalized_refs,
            unrepresentable_paths=unreadable_display,
        )

    files = tuple(sorted((path, blob_by_path[path]) for path in matched))
    return SourceRefExpansion(
        status=ExpansionStatus.RESOLVED,
        files=files,
        source_hash=compute_source_hash(files),
        normalized_refs=normalized_refs,
        # A árvore pode estar incompleta e a expansão **desta entrada** ainda ser completa:
        # nenhum dos refs dela poderia alcançar o caminho ilegível. O diagnóstico viaja
        # junto mesmo assim — é um fato sobre o repositório que o operador merece ver.
        unrepresentable_paths=unreadable_display,
    )


def compute_source_hash(files: tuple[tuple[str, str], ...]) -> str:
    """`source_hash` de [03] §3: `sha256(canonical_json([ (path, blob_sha) ordenados ]))`.

    O par sai como lista de dois elementos, e não como tupla: `canonical_json` recusa
    `tuple` de propósito ([02] §7 — a ordem de uma tupla é ambígua para quem lê o JSON), e
    `["caminho", "sha"]` é a forma JSON natural do par. A ordenação é a da própria lista,
    que já vem congelada pela expansão.
    """
    return canonical_sha256([[path, blob_sha] for path, blob_sha in files])


def expand_against_commit(
    local_path: str,
    commit: str,
    raw_refs: list[str],
    *,
    policy: SafetyPolicy | None = None,
) -> SourceRefExpansion:
    """`expand_source_refs` lendo a árvore de `commit` — a única parte que toca o git.

    ``commit`` é o `verification_commit` já congelado pelo chamador ([03] §3). Esta função
    **não** lê `HEAD`: quem captura o SHA é quem inicia a verificação, uma única vez.

    `list_tree` tem **dois** desfechos, e os dois chegam inteiros a `expand_source_refs`:
    `None` ("não consegui ler nada") e um `TreeListing` — que já traz, junto, os arquivos
    legíveis e os caminhos que não deu para nomear. Desde E4-AUD5-001 não há mais um
    terceiro desfecho a interceptar aqui: interceptá-lo era exatamente o que fazia a
    expansão desistir dos arquivos legíveis antes de classificá-los.
    """
    if not raw_refs:
        return expand_source_refs([], TreeListing(files=()), policy=policy)

    return expand_source_refs(raw_refs, list_tree(local_path, commit), policy=policy)
