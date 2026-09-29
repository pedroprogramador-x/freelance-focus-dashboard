"""Redator de segredos — camada 3 da proteção
([04](../../../docs/architecture/04-safety-and-git-runtime.md) §5).

É **um só** e vive aqui. Roda na seleção de contexto, na persistência de log e na
serialização de resposta JSON de `/api/*`.

Puro: opera sobre string, não lê arquivo, não consulta ambiente.

## Um motor, quatro formas de perguntar

`detect_secret_spans` é o **motor**: devolve as posições de tudo que os padrões
reconhecem, sem alterar o texto. As outras duas entradas são finas por cima dele:

* `redact(text)` — substitui cada span por `«redigido»`. É o que sempre foi;
* `is_sensitive_key(key)` — classifica um **nome de campo**, onde não existe adjacência
  textual nenhuma para um regex ver (`{"password": "x"}` não tem `password: x` em lugar
  nenhum até alguém linearizar).

* `redact_document(value)` — a **caminhada** recursiva sobre um documento JSON, que
  aplica `redact` a toda string alcançável, chave **ou** valor. É o boundary de saída de
  [04] §5 ("Camada 3"), consumido pelo `RedactingJSONResponse` de `api/`.

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
from collections.abc import Callable, Iterator
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
    #: Varredura alternativa, quando o `finditer` da própria expressão tem custo
    #: superlinear. Recebe a expressão canônica e o texto, e devolve **os mesmos
    #: `re.Match` que ela devolveria** — em outra ordem de trabalho, nunca com outro
    #: resultado. `None` (o caso normal) usa `pattern.finditer`.
    scan: Callable[[re.Pattern[str], str], Iterator[re.Match[str]]] | None = None

    def finditer(self, text: str) -> Iterator[re.Match[str]]:
        """Os matches deste padrão em `text`, pela varredura que ele declarar.

        Existe para que o motor tenha **um** ponto de entrada: quem lê
        `detect_secret_spans` não precisa saber que um dos padrões do catálogo é varrido
        de outro jeito, e um padrão novo entra sem `scan` e se comporta como sempre.
        """
        if self.scan is None:
            return self.pattern.finditer(text)
        return self.scan(self.pattern, text)


#: O terminador, usado só para achar o par abertura/terminador em custo linear. Não decide
#: nada: quem reconhece continua sendo a expressão canônica de `pem_block`.
_PEM_END = re.compile(r"-----END[A-Z ]*PRIVATE KEY-----")

#: Largura fixa dos cinco traços que uma abertura completa tem antes do corpo. `pem_block`
#: exige exatamente estes cinco — nem mais, nem menos — imediatamente antes de `BEGIN`.
_PEM_DASH_WIDTH = 5

#: Toda **corrida maximal** de letras maiúsculas e espaço — exatamente o alfabeto que
#: `[A-Z ]*` aceita. Uma classe de caractere repetida, sem alternância e sem sufixo
#: obrigatório depois dela: não há nada para o motor "tentar e desistir", então esta busca
#: é `O(n)` sem exceção, ao contrário de `BEGIN[A-Z ]*PRIVATE KEY-----` (ver
#: E6-AUD7-001-REV2 na docstring de `_localizar_corpos_pem`).
_PEM_CORRIDA_DE_PALAVRA = re.compile(r"[A-Z ]+")

#: A metade do corpo que cabe inteira dentro do alfabeto de `_PEM_CORRIDA_DE_PALAVRA` —
#: sem os cinco traços finais, que nunca poderiam fazer parte de uma corrida.
_PEM_SUFIXO_SEM_TRACOS = "PRIVATE KEY"

#: Os cinco traços que fecham o sufixo, verificados como texto bruto — nunca por regex.
_PEM_TRACOS = "-" * _PEM_DASH_WIDTH


def _localizar_corpos_pem(text: str) -> Iterator[tuple[int, int]]:
    """Os intervalos `(início, fim)` de cada corpo `BEGIN[A-Z ]*PRIVATE KEY-----` de
    `text` — o que `re.compile(r"BEGIN[A-Z ]*PRIVATE KEY-----").finditer(text)` devolveria,
    mas sem o retrocesso que torna aquela expressão quadrática (E6-AUD7-001-REV2).

    ## O retrocesso que isto corrige (E6-AUD7-001-REV2)

    A correção de E6-AUD7-001 trocou `_PEM_BEGIN` (com os cinco traços iniciais, e
    autossobreponível) por um corpo sem eles — mas o corpo continuava sendo
    `BEGIN[A-Z ]*PRIVATE KEY-----`, um quantificador guloso seguido de um sufixo
    **obrigatório que pode não existir**. Nessa forma, cada ocorrência de `BEGIN` inicia
    uma tentativa em que `[A-Z ]*` consome o quanto puder do alfabeto permitido e depois
    retrocede, um caractere de cada vez, procurando `PRIVATE KEY-----`. Se o sufixo nunca
    aparece, o retrocesso inteiro é desperdiçado — e se há `O(n)` ocorrências de `BEGIN`,
    cada uma pagando até `O(n)` de retrocesso, o total é `O(n²)`. Medido: `"BEGIN " * n`
    (nenhum `PRIVATE KEY-----` em lugar nenhum) crescia **~4×** a cada vez que `n`
    dobrava — a assinatura de `O(n²)`, não de `O(n)`.

    A revisão pré-auditoria que achou isto notou a mesma doença de forma que já apareceu
    neste módulo antes: um quantificador guloso com sufixo obrigatório **é** o padrão de
    custo superlinear (é a mesma família de `.*?` seguido de terminador que E6-AUD6-001
    corrigiu para o bloco inteiro) — só que desta vez dentro do próprio localizador que
    a correção anterior introduziu para resolver E6-AUD7-001.

    ## Por que a correção não pode ser "mais um quantificador guloso, mas diferente"

    Qualquer expressão da forma `<algo>[classe]*<sufixo obrigatório>` tem o mesmo risco
    estrutural: o motor de regex do Python retrocede por trás de um quantificador guloso
    quando o que vem depois pode falhar, e não tem como saber de antemão que vai falhar
    sem tentar. A saída não é trocar de expressão — é não usar quantificador guloso mais
    sufixo obrigatório para a MESMA busca. Aqui a busca é dividida em duas primitivas que
    não têm essa forma:

    * `_PEM_CORRIDA_DE_PALAVRA` (`[A-Z ]+`) é uma classe de caractere repetida **sem**
      nada obrigatório depois dela na mesma expressão — o motor nunca precisa retroceder,
      porque não há sufixo para falhar. Achar todas as corridas maximais é `O(n)`: cada
      caractere do texto pertence a no máximo uma corrida, e o motor a percorre uma vez;
    * dentro de cada corrida (já isolada como uma `str` comum), dois testes de string
      pura — `str.endswith` e `str.find` — decidem se ela termina em `PRIVATE KEY` e onde
      começa o primeiro `BEGIN`. Nenhum dos dois é regex; nenhum dos dois tem quantificador
      para retroceder. O custo de cada teste é, no pior caso, proporcional ao tamanho
      *daquela* corrida — e como as corridas são disjuntas entre si (a união delas nunca
      excede o texto), a soma de todos os testes é `O(n)`.

    Os cinco traços finais do sufixo (que `[A-Z ]*` nunca poderia conter) são conferidos
    à parte, como texto bruto: `text[fim_da_corrida : fim_da_corrida + 5] == "-----"`.
    Sem regex nenhuma nessa checagem — só fatiamento e igualdade, `O(1)`.

    ## Por que o resultado é idêntico ao de `BEGIN[A-Z ]*PRIVATE KEY-----`

    O quantificador guloso de `[A-Z ]*`, quando o casamento tem sucesso, sempre consome o
    **máximo possível** e recua o mínimo necessário para que `PRIVATE KEY-----` ainda
    caiba — ou seja, ele sempre encontra a ocorrência de `PRIVATE KEY-----` **mais à
    direita** que ainda está dentro de uma corrida contígua do alfabeto permitido.
    `str.endswith` sobre a corrida inteira pergunta exatamente isso: a corrida, como um
    todo, termina em `PRIVATE KEY`? E, dentro dela, `BEGIN` continua sendo achado pela
    correspondência mais à **esquerda** (`str.find`), que é a mesma preferência do motor
    de regex ao escolher onde a busca começa. As duas primitivas espelham exatamente as
    duas metades de como o regex decidiria, sem nenhuma das duas custar retrocesso.

    Verificado por diferencial exaustivo contra
    `re.compile(r"BEGIN[A-Z ]*PRIVATE KEY-----").finditer` — ver
    `test_e6_aud7_001_rev2_localizador_bate_com_o_regex_de_corpo` — e por medição direta de
    crescimento em `"BEGIN " * n` e nas demais famílias adversariais do finding.
    """
    for corrida in _PEM_CORRIDA_DE_PALAVRA.finditer(text):
        inicio_corrida, fim_corrida = corrida.span()
        grupo = corrida.group()
        if not grupo.endswith(_PEM_SUFIXO_SEM_TRACOS):
            continue
        if text[fim_corrida : fim_corrida + _PEM_DASH_WIDTH] != _PEM_TRACOS:
            continue
        posicao_begin = grupo.find("BEGIN")
        if posicao_begin == -1:
            continue
        yield inicio_corrida + posicao_begin, fim_corrida + _PEM_DASH_WIDTH


def _scan_pem_block(pattern: re.Pattern[str], text: str) -> Iterator[re.Match[str]]:
    """Os blocos PEM de `text`, em custo linear — os mesmos que `pattern.finditer` acha.

    ## O custo que isto corrige (E6-AUD6-001)

    `-----BEGIN…-----` `.*?` `-----END…-----` é quadrático quando há abertura sem
    fechamento: o `finditer` tenta a expressão em cada abertura, e em cada uma o `.*?`
    varre o resto do texto atrás de um terminador que não existe. Medido: 196.000
    caracteres com 7.000 aberturas e nenhum `END` custavam **7,3 s** em processo (9,7 s
    pela rota HTTP), contra 169 ms para texto comum do mesmo tamanho.

    ## A omissão que isto corrige (E6-AUD7-001)

    A primeira versão desta função achava aberturas com `_PEM_BEGIN.finditer`, onde
    `_PEM_BEGIN` incluía os cinco traços iniciais (`-----BEGIN[A-Z ]*PRIVATE KEY-----`).
    Essa expressão é **autossobreponível**: ela começa e termina com o mesmo literal de
    cinco traços. Quando uma abertura termina em `-----` e o texto seguinte é
    `BEGIN…KEY-----` sem os próprios traços, esses cinco traços finais servem ao mesmo
    tempo de fim da primeira abertura e de início da segunda — e `finditer`, que nunca
    sobrepõe matches, consome os traços na primeira e descarta a segunda inteira.
    É precisamente o padrão do contraexemplo independente que reproduziu o vazamento:

    ```text
    B = "-----BEGIN PRIVATE KEY-----"
    E = "-----END PRIVATE KEY-----"
    p = "MIIE_AUD7_SYNTHETIC_PRIVATE_MATERIAL"
    u = lambda x: "BEGIN PRIVATE KEY-----" * 2 + x + E
    texto = B + E + u(p) + u("")
    ```

    A expressão canônica acha `(0,52), (69,157), (174,226)`; a versão com `_PEM_BEGIN`
    achava só `(0,52), (152,226)` — a abertura em 69, que reaproveita os cinco traços
    finais da abertura em 47, nunca aparecia, e o segredo entre elas saía cru.

    A correção separa a detecção em duas partes que **não** compartilham a ambiguidade:

    * `_localizar_corpos_pem` acha o corpo (`BEGIN[A-Z ]*PRIVATE KEY-----`) **sem** os
      traços iniciais — sem eles, o corpo nunca se sobrepõe consigo mesmo, porque o
      próprio `[A-Z ]*` exclui `-`, e um corpo sempre termina exatamente onde o próximo
      pode começar. Isso acha **todas** as ocorrências, mesmo encadeadas sem separador
      nenhum. (A implementação em si não usa mais um regex de quantificador-mais-sufixo
      para isto — ver E6-AUD7-001-REV2 na docstring dela — mas o *resultado* continua sendo
      exatamente o mesmo que esse regex produziria.);
    * para cada corpo achado, um teste local de exatamente cinco caracteres
      (`text[início - 5 : início] == "-----"`) decide se ele forma uma abertura completa.
      O deslocamento é sempre cinco porque a expressão canônica exige exatamente cinco
      traços, nem mais nem menos, imediatamente antes de `BEGIN` — e o teste lê o texto
      bruto, nunca depende de qual corpo foi "achado primeiro".

    Como os corpos nunca se perdem, cada abertura completa (inclusive as que reaproveitam
    os traços finais de uma abertura anterior) aparece exatamente uma vez, na ordem certa.

    ## Por que a varredura continua linear

    Cada caractere é visitado um número constante de vezes:

    * os corpos saem de `_localizar_corpos_pem`, que não retrocede — `O(n)` (ver a
      docstring dela para a prova, incluindo por que a primeira versão desta correção
      *não* cumpria isso: E6-AUD7-001-REV2);
    * o teste de cinco traços por corpo é `O(1)`, e há no máximo um corpo por posição de
      `BEGIN`, então a soma de todos os testes é `O(n)`;
    * o terminador de cada abertura **ativa** (a que não foi pulada por já estar coberta)
      sai de **um** `search` a partir do fim dela. Esses `search` cobrem intervalos
      disjuntos e crescentes: um sucesso avança o cursor até o fim do bloco encontrado, e
      uma falha encerra a varredura inteira — nunca há dois `search` caros disputando a
      mesma região do texto;
    * a primeira abertura ativa **sem** terminador encerra a varredura. Isso é exato, não
      heurística: se não há `END` depois dela, também não há depois de nenhuma abertura
      posterior, porque a busca por `END` a partir de uma posição posterior nunca acha o
      que uma busca a partir de uma posição anterior não achou.

    Medido contra formas adversariais desenhadas para esta correção e para E6-AUD7-001-REV2
    especificamente — cadeias de sobreposição sem terminador, cadeias sobrepostas com um
    único terminador distante, "ilhas" repetidas de sobreposição com terminador,
    terminadores quase válidos intercalados, `"BEGIN " * n` sem sufixo nenhum, `BEGIN`
    seguido de uma corrida grande de maiúsculas que nunca fecha, muitos `BEGIN` dentro da
    mesma corrida, sufixos quase completos, e um único `PRIVATE KEY-----` distante depois
    de muito ruído — o crescimento observado dobra ao dobrar o tamanho da entrada em
    todas elas, nunca quadruplica.

    ## Por que o `re.Match` vem da expressão canônica

    `pattern.match(text, inicio)` custa o tamanho do bloco — e os blocos são disjuntos,
    então a soma continua linear. Pagar essa segunda passada é deliberado: o objeto que
    chega a `_emit_span` é produzido pela **expressão histórica**, então
    `recognition_span`/`replacement_span`, grupos e `endpos` não podem divergir do que
    sempre foram. Reconstruir um match à mão economizaria uma passada e criaria uma
    segunda fonte de verdade sobre o que o padrão reconheceu, que é o defeito que este
    módulo existe para não ter. `_localizar_corpos_pem` e o teste de traços só
    **localizam** candidatos; quem decide se um candidato é de fato um bloco válido —
    inclusive o terminador — continua sendo exclusivamente `pattern`, a expressão
    canônica.
    """
    cursor = 0
    for inicio_corpo, fim_corpo in _localizar_corpos_pem(text):
        inicio = inicio_corpo - _PEM_DASH_WIDTH
        if inicio < cursor:
            continue
        if inicio < 0 or text[inicio:inicio_corpo] != _PEM_TRACOS:
            continue
        if _PEM_END.search(text, fim_corpo) is None:
            return
        bloco = pattern.match(text, inicio)
        if bloco is None:  # pragma: no cover - o candidato acabou de casar, e há terminador
            continue
        yield bloco
        cursor = bloco.end()


# Ordem importa: padrões mais específicos primeiro.
#
# O catálogo é **canônico**: cada expressão é a histórica, sem nenhuma modificação de
# vizinhança. Foi tentar codificar adjacência aqui dentro — o *tempering* da rodada 4 —
# que produziu E6-AUD5-001: um token curto demais antes do vizinho deixava de casar, e a
# cobertura histórica **encolhia**. Adjacência é problema do motor, não do padrão.
_PATTERNS: tuple[_Pattern, ...] = (
    _Pattern(
        "pem_block",
        re.compile(
            r"-----BEGIN[A-Z ]*PRIVATE KEY-----.*?-----END[A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        scan=_scan_pem_block,
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

#: Teto de passes do laço de convergência. Um documento que não estabiliza dentro dele não
#: recebe redação parcial: o campo inteiro vira `«redigido»` (ver `detect_secret_spans` e
#: `redact`). Fail-closed, como [ADR-0009] manda tratar ausência de prova.
#: `pattern_name` do span que cobre o campo inteiro quando o laço não convergiu dentro
#: de `MAX_REDACTION_PASSES`. Quem inspeciona spans consegue distinguir "isto é um
#: segredo reconhecido" de "isto é um campo que o motor não conseguiu decidir".
UNCONVERGED = "unconverged"

MAX_REDACTION_PASSES = 8

#: O `\b` inicial (depois de flags inline), e o `\b` final. Recortados **mecanicamente**:
#: reescrever as expressões à mão criaria uma segunda fonte de verdade para o mesmo
#: reconhecimento, que é o defeito que este módulo inteiro existe para não ter.
_LEFT_WORD_BOUNDARY = re.compile(r"\A((?:\(\?[aiLmsux]+\))?)\\b")
_TRAILING_WORD_BOUNDARY = "\\b"


def _without_left_word_boundary(pattern: re.Pattern[str]) -> re.Pattern[str] | None:
    """A mesma expressão sem o `\b` inicial. `None` se ela não tiver um.

    Flags inline (`(?i)`) precisam continuar no começo da expressão — desde o Python 3.11
    elas são erro em qualquer outra posição —, por isso o recorte preserva o grupo de flags
    e tira só o `\b` que vem depois dele.
    """
    source = pattern.pattern
    match = _LEFT_WORD_BOUNDARY.match(source)
    if match is None:
        return None
    return re.compile(match.group(1) + source[match.end() :], pattern.flags)


def _without_trailing_word_boundary(pattern: re.Pattern[str]) -> re.Pattern[str]:
    source = pattern.pattern
    if not source.endswith(_TRAILING_WORD_BOUNDARY):
        return pattern
    return re.compile(source[: -len(_TRAILING_WORD_BOUNDARY)], pattern.flags)


#: **Uma janela que começa aqui.** A expressão canônica aplicada a partir de uma âncora,
#: como se o sufixo fosse uma string nova — que é exatamente a pergunta do oráculo de
#: adjacência: "se o texto a partir de `i` fosse o começo de tudo, o catálogo reconheceria
#: um segredo?".
#:
#: Derivada, nunca reescrita. Só existe para padrões que têm `\b` à esquerda: são eles, e só
#: eles, que uma vizinhança alfanumérica consegue silenciar.
_ANCHORED: dict[str, re.Pattern[str]] = {}
#: As **duas** fronteiras são recortadas, porque as duas são bordas da janela: a inicial
#: porque a janela começa ali, a final porque o que vem depois do que a janela contém não
#: participa da decisão. A final é o que torna duas *access keys* AWS coladas reconhecíveis
#: — nenhuma das duas casa a fronteira final contra o texto real, e sem isso não sobra
#: âncora nenhuma para o laço começar.
for _spec in _PATTERNS:
    _sem_esquerda = _without_left_word_boundary(_spec.pattern)
    if _sem_esquerda is None:
        continue
    _ANCHORED[_spec.name] = _without_trailing_word_boundary(_sem_esquerda)

#: O que `\b` considera "caractere de palavra" — **a mesma** definição, não uma
#: aproximação ASCII. O `\b` do Python é Unicode-aware, e usar `[A-Za-z0-9_]` aqui fazia
#: `caféghp_…` virar início de corrida (porque `é` não casava) enquanto `tokenizerghp_…`
#: não virava: o mesmo caso tratado de duas formas, por discrepância de alfabeto.
_WORD_CHARACTER = re.compile(r"\w")


def _word_run_starts(text: str) -> tuple[int, ...]:
    """Onde começa cada corrida de caracteres de palavra — as fronteiras **de verdade**.

    São as únicas âncoras que não dependem de já haver segredo reconhecido, e é por isso que
    elas não relaxam nada por vizinhança: numa corrida que começa com palavra comum
    (`tokenizerghp_…`) o catálogo simplesmente não casa na posição zero da corrida, e
    nenhuma posição interna é âncora.
    """
    return tuple(
        position
        for position in range(len(text))
        if _WORD_CHARACTER.match(text[position])
        and (position == 0 or not _WORD_CHARACTER.match(text[position - 1]))
    )


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


def _emit_span(
    spec: _Pattern,
    working: str,
    segments: tuple[_Segment, ...],
    match: re.Match[str],
) -> SecretSpan | None:
    """Um match do catálogo virando `SecretSpan` em coordenadas do texto **original**.

    Serve a cascata e a revarredura ancorada com o mesmo cálculo: duas cópias dele
    divergiriam, e o que elas produzem é a posição que o Context Engine usa para mapear o
    segredo de volta ao campo de origem.
    """
    working_start = match.end(1) if spec.prefix_preserving else match.start()
    working_end = match.end()
    if working_end <= working_start:
        return None

    start = _to_original_start(segments, working_start)
    end = _to_original_end(segments, working_end)
    if end <= start:
        return None

    # O texto **lido** pela detecção: o match inteiro (incluindo o prefixo preservado) mais
    # o que lookbehind/lookahead positivo exigiram. Para os padrões sem prefixo nem
    # lookaround é o mesmo intervalo; para `bearer`/`assigned_secret`/`url_credentials` é
    # maior, e é essa diferença que diz se a detecção precisou de contexto de outro
    # fragmento para existir (`E5-AUD3-002`, `E5-AUD4-003`).
    window_start, window_end = _recognition_window(spec, working, match)
    recognition_start = _to_original_start(segments, window_start)
    recognition_end = _to_original_end(segments, window_end)

    return SecretSpan(
        recognition_span=Interval(
            start=min(recognition_start, start), end=max(recognition_end, end)
        ),
        replacement_span=Interval(start=start, end=end),
        pattern_name=spec.name,
    )


def _covered_length(regions: tuple[tuple[int, int], ...]) -> int:
    return sum(end - start for start, end in regions)


def _anchor_positions(
    regions: tuple[tuple[int, int], ...], run_starts: tuple[int, ...]
) -> tuple[int, ...]:
    """As posições onde o catálogo é reexecutado como se ali começasse uma string nova.

    Duas origens, e nenhuma delas é "vizinhança genérica":

    * **dentro ou no fim de uma região já coberta** — adjacência real de segredo a segredo.
      É o que alcança o segundo token quando o primeiro match comeu o prefixo dele;
    * **início de uma corrida de caracteres de palavra** — onde existe fronteira de palavra
      de verdade. É a âncora que não depende de já haver segredo reconhecido, e sem ela duas
      *access keys* AWS coladas não teriam por onde começar.

    Uma corrida que começa com palavra comum (`tokenizerghp_…`) não produz âncora útil: o
    catálogo não casa na posição zero da corrida, e nenhuma posição interna vira âncora
    enquanto não houver segredo reconhecido ali.
    """
    posicoes: set[int] = {position for start, end in regions for position in range(start, end + 1)}
    posicoes.update(run_starts)
    return tuple(sorted(posicoes))


def _anchored_spans(
    text: str,
    identity: tuple[_Segment, ...],
    position: int,
    memo: dict[int, tuple[SecretSpan, ...]],
) -> tuple[SecretSpan, ...]:
    """O catálogo aplicado a partir de `position`, com a fronteira inicial já satisfeita.

    Memorizado por posição: uma âncora não muda de resposta entre passes — só o conjunto de
    regiões cresce —, então o trabalho total é proporcional ao texto, não ao número de
    passes.
    """
    memorizado = memo.get(position)
    if memorizado is not None:
        return memorizado

    encontrados: list[SecretSpan] = []
    for spec in _PATTERNS:
        anchored = _ANCHORED.get(spec.name)
        if anchored is None:
            continue
        match = anchored.match(text, position)
        if match is None:
            continue
        span = _emit_span(spec, text, identity, match)
        if span is not None:
            encontrados.append(span)

    memo[position] = tuple(encontrados)
    return memo[position]


def detect_secret_spans(text: str) -> tuple[SecretSpan, ...]:
    """Todas as posições que os padrões reconhecem, **sem alterar o texto**.

    ## O motor é um ponto fixo, e o catálogo nunca muda

    O algoritmo trabalha inteiro em coordenadas do texto **original**, que é imutável:

    1. a cascata canônica roda sobre o original — cada padrão enxergando a reprojeção dos
       anteriores, porque essa cascata **é** o comportamento histórico de `redact()`;
    2. o catálogo é reexecutado a partir de cada **âncora** (ver `_anchor_positions`), como
       se ali começasse uma string nova;
    3. tudo que for encontrado é mapeado de volta para o original e unido à coleção de
       regiões cobertas;
    4. repete enquanto a união **crescer**. Uma volta que só reencontra o que já estava
       coberto não é progresso e encerra o laço.

    O laço termina: cada volta ou aumenta o número de caracteres cobertos — limitado por
    `len(text)` — ou para. `MAX_REDACTION_PASSES` é o teto explícito; atingi-lo **com
    progresso ainda acontecendo** significa que a convergência não foi provada, e aí o campo
    inteiro é dado como segredo em vez de sair pela metade.

    ## Por que o catálogo voltou a ser canônico

    A rodada 4 tentou codificar adjacência dentro das expressões (*tempering*, variantes
    encadeadas, fronteira à direita por padrão). E6-AUD5-001 mostrou o preço: um primeiro
    token curto demais para o mínimo deixava de casar diante do vizinho, e a cobertura
    **encolhia** em relação ao detector histórico. Mexer no padrão muda o que ele reconhece
    isoladamente; mexer no motor não. Aqui o catálogo é o histórico, byte a byte, e toda a
    adjacência vive no laço.

    ## O mapa de volta, que é o que torna os spans reutilizáveis

    Quem recebe os spans pode dizer de qual campo cada segredo veio, em vez de recortar o
    resultado já substituído por um delimitador — o recorte por delimitador foi exatamente o
    que devolvia a credencial pela chave em `E5-AUD2-003`.

    Os spans podem se sobrepor (dois padrões reconhecendo a mesma região). Quem substitui
    resolve com `merge_spans`; quem classifica campos aproveita a sobreposição como
    informação.
    """
    if not text:
        return ()

    spans: list[SecretSpan] = []
    regions: tuple[tuple[int, int], ...] = ()
    identity = _project(text, ())[1]
    run_starts = _word_run_starts(text)
    memo: dict[int, tuple[SecretSpan, ...]] = {}

    for _passe in range(MAX_REDACTION_PASSES):
        antes = _covered_length(regions)
        encontrados: list[tuple[int, int]] = []

        # (1) a cascata canônica sobre a reprojeção do original.
        for spec in _PATTERNS:
            working, segments = _project(text, regions)
            achados: list[tuple[int, int]] = []
            for match in spec.finditer(working):
                span = _emit_span(spec, working, segments, match)
                if span is None:
                    continue
                achados.append((span.replacement_span.start, span.replacement_span.end))
                spans.append(span)
            if achados:
                regions = _merge_regions([*regions, *achados])
                encontrados.extend(achados)

        # (2) o catálogo reexecutado a partir de cada âncora, em coordenadas do original.
        for position in _anchor_positions(regions, run_starts):
            for span in _anchored_spans(text, identity, position, memo):
                encontrados.append((span.replacement_span.start, span.replacement_span.end))
                spans.append(span)

        if encontrados:
            regions = _merge_regions([*regions, *encontrados])

        if _covered_length(regions) <= antes:
            break
    else:
        # Teto atingido com progresso ainda acontecendo: convergência não provada. O campo
        # inteiro vira segredo — nunca um resultado parcial ([ADR-0009], fail-closed).
        return (
            SecretSpan(
                recognition_span=Interval(start=0, end=len(text)),
                replacement_span=Interval(start=0, end=len(text)),
                pattern_name=UNCONVERGED,
            ),
        )

    # Sem duplicata: o laço reencontra a mesma âncora a cada volta, e um span repetido
    # chegaria duas vezes a quem conta transformações — `transformations` participa do
    # payload hasheado do artifact ([02] §5). `SecretSpan` é `frozen`, então `dict.fromkeys`
    # deduplica preservando a ordem de inserção antes da ordenação final.
    return tuple(
        sorted(
            dict.fromkeys(spans),
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


class Unredacted(str):
    """Uma string que o boundary de saída **não** redige. Marcador estreito, não flag.

    Existe para exatamente um valor em todo o sistema: o `purge_token` de [02] §11. Ele é
    um segredo de autorização **gerado pelo backend e entregue por desenho** — a prévia de
    purga existe para emiti-lo, e mascará-lo quebraria a confirmação forte que ele
    implementa. É a diferença entre "credencial colada em conteúdo autoral", que a Camada 3
    existe para conter, e "credencial que esta resposta tem a função de entregar".

    ## Por que um tipo, e não um atributo

    A alternativa óbvia — `Field(json_schema_extra={"redact": False})`, uma allowlist de
    nome de campo, um `skip_redaction=True` — é uma **flag genérica**: qualquer campo novo
    pode adotá-la, e a exceção deixa de ser auditável no momento em que alguém a copia para
    um segundo lugar "porque também não deveria ser redigido". [04] §5 proíbe isso
    explicitamente.

    Um tipo nominal inverte o ônus: para escapar da redação é preciso **construir** um
    `Unredacted`, e construir um `Unredacted` é uma linha de código rastreável por AST.
    `test_architecture` afirma que existe **exatamente um** ponto de construção em todo o
    backend (`PurgeTokenStore.issue`). Um segundo call site quebra a suíte.

    ## O que ele não faz

    Não é opaco nem seguro por si: é uma `str` comum para todo o resto do Python, e
    `json.dumps` a serializa como qualquer string. A única coisa que ele muda é a resposta
    de `redact_document` à pergunta "esta string passa pelo redator?".

    Vale registrar, porque é o tipo de garantia que envelhece mal se não for escrita: hoje
    os padrões de `detect_secret_spans` **não** reconhecem a saída de `secrets.token_urlsafe`
    (verificado em `test_secrets_and_redaction`), então o `purge_token` chegaria intacto
    mesmo sem este marcador. O marcador não corrige um vazamento — ele impede que um padrão
    novo, acrescentado por um motivo não relacionado, quebre a purga em silêncio.
    """

    __slots__ = ()


#: Prefixo e sufixo do placeholder de chave mascarada. Ver `redact_document`.
REDACTED_KEY_PREFIX = "«chave redigida "
REDACTED_KEY_SUFFIX = "»"


#: A **forma** de um placeholder de chave. Serve para duas perguntas: "esta chave literal
#: do documento já ocupa um número?" (E6-AUD4-002) e "este texto carrega marcador de
#: redação?" (`contains_redaction_marker`, E6-AUD4-004).
#:
#: `[0-9]+` e não um número fixo: o placeholder é numerado por documento, e o documento de
#: entrada pode ter vindo de qualquer resposta anterior.
_REDACTED_KEY_SHAPE = re.compile(
    re.escape(REDACTED_KEY_PREFIX) + "([0-9]+)" + re.escape(REDACTED_KEY_SUFFIX)
)


def _reserved_key_numbers(value: object, reserved: set[int]) -> None:
    """Os números de placeholder que o documento de entrada **já usa como nome literal**.

    Uma chave com a forma `«chave redigida 1»` atravessa a caminhada intacta: `redact` não
    reconhece segredo nenhum nela, logo `_redact_key` a devolve como está. Se o contador
    emitisse `«chave redigida 1»` para uma chave secreta do **mesmo** `dict`, as duas
    colidiriam e uma apagaria a outra — o campo sumiria da resposta sem rastro, que é
    exatamente o desaparecimento silencioso que numerar existia para impedir
    (E6-AUD4-002).

    Só a forma de placeholder é coletada, e não todo nome de chave: qualquer outro nome é
    incapaz de colidir com o que o contador emite, e varrer por igualdade custaria uma
    chamada a `redact` por chave sem responder nada a mais.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                match = _REDACTED_KEY_SHAPE.fullmatch(key)
                if match is not None:
                    reserved.add(int(match.group(1)))
            _reserved_key_numbers(item, reserved)
    elif isinstance(value, list | tuple):
        for item in value:
            _reserved_key_numbers(item, reserved)


def contains_redaction_marker(text: str) -> bool:
    """O texto carrega um marcador de redação — `«redigido»` ou `«chave redigida N»`?

    Pergunta de **entrada**, não de saída: [04] §5 redige o que sai, e esta função é o que
    permite recusar o que volta. Um texto redigido que retorna pelo `PATCH` do Context
    Registry seria persistido como conteúdo autoral, e o marcador substituiria para sempre
    o trecho que ele estava escondendo (E6-AUD4-004).

    Mora aqui porque os dois marcadores moram aqui. Uma segunda definição de "o que é um
    marcador" noutro módulo divergiria desta na primeira vez que o formato mudasse.
    """
    return REDACTED in text or _REDACTED_KEY_SHAPE.search(text) is not None


class _RedactedKeyNames:
    """Numera as chaves mascaradas de **um** documento. Ver `redact_document`.

    Contador por documento — o escopo é a chamada de topo de `redact_document`, e não o
    dicionário nem o nível. Ver o docstring de `redact_document` para o porquê.
    """

    __slots__ = ("_emitted", "_reserved")

    def __init__(self, reserved: frozenset[int] = frozenset()) -> None:
        self._emitted = 0
        self._reserved = reserved

    def next_placeholder(self) -> str:
        """O próximo número **livre** — nem já emitido, nem já ocupado por chave literal.

        O contador é monotônico, então dois placeholders gerados nunca colidem entre si; o
        conjunto `reserved` fecha o outro lado, que era o buraco de E6-AUD4-002: um nome de
        chave que já tinha a forma do placeholder antes de a caminhada começar.
        """
        while True:
            self._emitted += 1
            if self._emitted not in self._reserved:
                return f"{REDACTED_KEY_PREFIX}{self._emitted}{REDACTED_KEY_SUFFIX}"


def _redact_key(key: object, names: _RedactedKeyNames) -> object:
    """A chave carrega segredo? Então vira um placeholder **opaco e único**.

    A substituição é do nome **inteiro**, e não do span — `"prefixo-sk-AAA…-sufixo"` não
    vira `"prefixo-«redigido»-sufixo"`. Dois motivos, e o segundo é o que E6-AUD3-003
    encontrou:

    1. o que sobra de uma chave parcialmente redigida ainda é substring do segredo, e
       [04] §5 pede placeholder **não-reversível**;
    2. duas chaves diferentes que compartilham o mesmo segredo colapsariam no **mesmo**
       texto — e um `dict` não tem duas chaves iguais: uma sobrescreveria a outra, e um
       campo desapareceria da resposta sem deixar rastro. O placeholder numerado é o que
       torna a colisão impossível por construção.

    Chave que não é `str` atravessa intacta: `json` só emite chave textual, e um `int` ou
    `bool` usado como chave não tem span a detectar.
    """
    if not isinstance(key, str) or isinstance(key, Unredacted):
        return key
    return names.next_placeholder() if redact(key) != key else key


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


def redact_document(
    value: object, *, _names: _RedactedKeyNames | None = None, _sensitive: bool = False
) -> object:
    """Redige **recursivamente** toda string de um documento JSON — chave **e** valor.

    É a Camada 3 de [04] §5 inteira. O redator canônico responde sobre **uma** string;
    quase todo consumidor tem um documento, e é esta função que garante que nenhuma string
    alcançável escape da pergunta, em qualquer profundidade.

    Ela não decide **o que** é segredo — isso continua sendo `detect_secret_spans`. Ela
    decide **o que é perguntado**, e a história desta camada é a história de essa fronteira
    ter sido desenhada pequena demais três vezes seguidas:

    * antes da E6-AUD2 havia três caminhadas idênticas em três módulos, e corrigir uma não
      corrigia as outras (E6-AUD2-001, o `runner_id` numa segunda saída);
    * até a E6-AUD3 esta caminhada percorria só **valores**, sob a premissa de que chave é
      sempre nome de campo de schema. `structured` do registry aceita chave livre, e
      `test_config` é JSON livre no banco: a premissa era falsa, e uma credencial usada
      como chave atravessava intacta (E6-AUD3-003).

    **Chaves agora são redigidas**, e por substituição opaca — ver `_redact_key`.

    ## O escopo do contador é o documento, não o dicionário

    `_RedactedKeyNames` é criado na chamada de **topo** e atravessa a recursão inteira, de
    modo que `«chave redigida 3»` é único na resposta toda — não apenas dentro do seu
    `dict`. Por nível bastaria para impedir a colisão que apaga um campo (duas chaves do
    mesmo `dict` nunca colidem em nenhum dos dois escopos), mas leria pior: dois
    placeholders idênticos em ramos diferentes do JSON pareceriam a mesma chave, e não são.
    Numerar por documento custa um inteiro e remove a ambiguidade.

    A numeração é **posicional**, não derivada do conteúdo: a mesma chave secreta que
    aparecer duas vezes recebe dois números. Isso é deliberado — derivar o placeholder do
    valor (hash, prefixo) vazaria a informação "estas duas chaves são iguais" e daria a
    quem observa um oráculo de igualdade sobre o segredo. O preço é não poder correlacionar
    ocorrências, que é exatamente o que se quer aqui.

    Determinística: a ordem de travessia de um `dict` Python é a de inserção, então o mesmo
    documento produz sempre o mesmo resultado.

    ## A chave sensível classifica a subárvore inteira (E6-AUD7-002)

    `is_sensitive_key` é a resposta do sistema para "não existe adjacência textual que um
    regex veja": `{"token": "x"}` não contém `token: x` em lugar nenhum até alguém
    linearizar. Até esta correção a caminhada aplicava `is_sensitive_key` às **chaves** (via
    `_redact_key`, que só pergunta se a chave *contém* segredo) e o detector aos
    **valores**, e a classificação estrutural nunca atravessava para o valor: um valor que
    não casasse com padrão nenhum do catálogo saía cru debaixo de uma chave que o próprio
    sistema classifica como "o valor aqui é segredo" (E6-AUD7-002).

    Agora a classificação **desce**: uma chave que `is_sensitive_key` reconhece marca a
    subárvore inteira, e a marca atravessa `dict`, `list`, índice e qualquer chave
    intermediária, em qualquer profundidade. Sob a marca, **toda folha** vira `REDACTED`
    sem o conteúdo ser consultado — inclusive `int`, `float`, `bool`, `None` e string
    vazia, que o detector textual jamais reconheceria e que são exatamente a forma de um
    PIN, de um número de conta ou de um "tem credencial configurada".

    Não é semântica nova: é a mesma que o Context Engine (E5) já aplica em
    `context_engine/rendering.py::_collect_leaves`, onde `sensitive or is_sensitive_key(key)`
    desce pela árvore e cada folha marcada "sai inteira, sem olhar o conteúdo". Lá os
    **caminhos** continuam visíveis e só o valor vira marcador; aqui, analogamente, as
    chaves mantêm a semântica que já tinham (`_redact_key`) e só as folhas viram marcador.

    ## Contêiner vazio sob chave sensível também é folha (E6-AUD7-002V-001)

    `[]` e `{}` não têm elemento para recursão nenhuma alcançar — sem um caso à parte,
    `list`/`dict` sempre recursavam antes de perguntar se havia algo dentro, e um
    contêiner vazio recursado devolve **a si mesmo**, nunca chegando ao `if _sensitive:
    return REDACTED` do ramo de folha. O resultado: `{"token": []}` saía com `[]`
    intacto — o tipo do contêiner e a condição de vazio de um campo classificado como
    segredo, visíveis na resposta pública. É a mesma incoerência que `_collect_leaves`
    já resolve na E5: lá, um `dict`/`list` vazio vira um `_Leaf(generic_scalar=True)` e
    sai como qualquer outro escalar redigido (linhas 268/275). Aqui, sob `_sensitive`,
    um contêiner vazio agora é tratado como a folha que semanticamente é: o marcador
    substitui o contêiner inteiro, não um elemento dentro dele — não haveria elemento
    para redigir. Contêiner vazio **fora** de subárvore sensível continua vazio: não há
    folha para redigir, e inventar uma onde o documento não tem nenhuma acrescentaria
    informação à projeção em vez de removê-la.

    **A marca só desce.** Ela não sobe e não atravessa para o lado: `{"name": "Pedro",
    "token": "x"}` redige `token` e devolve `name` intacto. Fail-closed é sobre a subárvore
    classificada, não sobre o documento — transformar a resposta inteira em marcador porque
    um campo é sensível destruiria a projeção pública sem fechar vazamento nenhum.

    ## O que atravessa intacto

    `Unredacted` (ver o docstring dele) e o que não é texto — `int`, `bool`, `None`, e
    qualquer objeto que o encoder JSON resolva depois, **fora** de subárvore sensível. Não
    há span a detectar em algo que não é string.

    `Unredacted` vence a classificação estrutural, e essa precedência é obrigatória, não
    cosmética: `is_sensitive_key("purge_token")` é **verdadeiro** (o componente `token` está
    lá), e é sob essa chave literal que a prévia de purga entrega o token que ela existe
    para emitir. Sem a precedência, esta correção quebraria a purga. As duas decisões não
    conflitam — `Unredacted` é a mais específica das duas: "esta resposta tem a função de
    entregar **este** valor" é uma afirmação sobre o valor, e a chave classifica por
    ausência de informação sobre ele.
    """
    if _names is None:
        # Varredura de reserva **antes** de qualquer alocação: o número escolhido precisa
        # ser livre no documento inteiro, não só entre os placeholders gerados até aqui.
        reservados: set[int] = set()
        _reserved_key_numbers(value, reservados)
        names = _RedactedKeyNames(frozenset(reservados))
    else:
        names = _names

    # Antes de tudo: o escape hatch é a decisão mais específica que existe sobre um valor,
    # e `is_sensitive_key("purge_token")` é verdadeiro. Ver o docstring.
    if isinstance(value, Unredacted):
        return value
    # Contêiner vazio sob subárvore sensível é folha, não estrutura para recursar: sem
    # este desvio, `[redact_document(item, ...) for item in []]` devolve `[]` antes de
    # qualquer checagem de `_sensitive` alcançar a folha (E6-AUD7-002V-001).
    if _sensitive and isinstance(value, list | dict) and not value:
        return REDACTED
    if isinstance(value, list):
        return [redact_document(item, _names=names, _sensitive=_sensitive) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_document(item, _names=names, _sensitive=_sensitive) for item in value)
    if isinstance(value, dict):
        return {
            _redact_key(key, names): redact_document(
                item,
                _names=names,
                _sensitive=_sensitive or (isinstance(key, str) and is_sensitive_key(key)),
            )
            for key, item in value.items()
        }
    # Folha. Sob subárvore sensível o conteúdo não é consultado — é a classificação
    # estrutural que decide, e ela cobre o que o detector textual não alcança (E6-AUD7-002).
    if _sensitive:
        return REDACTED
    if isinstance(value, str):
        return redact(value)
    return value


def redact_path(path: str) -> str:
    """Redação aplicada a um caminho antes de virar ``subject`` de `SafetyEvent`.

    Um caminho pode carregar credencial (remote git com token). O resto do caminho é
    informação de diagnóstico legítima e é preservado.
    """
    return redact(path)


__all__ = [
    "REDACTED",
    "REDACTED_KEY_PREFIX",
    "REDACTED_KEY_SUFFIX",
    "Interval",
    "SecretSpan",
    "Unredacted",
    "contains_redaction_marker",
    "detect_secret_spans",
    "is_sensitive_key",
    "merge_spans",
    "redact",
    "redact_document",
    "redact_path",
]
