"""Sétima rodada de auditoria da E6 — E6-AUD7-001: a omissão do scanner PEM otimizado.

Escopo **exclusivo** desta correção: o scanner de `pem_block` (`_scan_pem_block`) omitia
uma abertura sempre que ela reaproveitava os cinco traços finais de uma abertura anterior
— o mesmo defeito de classe que já apareceu uma vez neste módulo (E6-AUD5-001: mexer no
que um reconhecimento decide *isoladamente* muda seu comportamento de formas que só
aparecem sob composição). Aqui quem mudou não foi o padrão, foi a **varredura auxiliar**
que localiza candidatos para ele — e ela tinha a mesma doença: era autossobreponível.

## E6-AUD7-001-REV2 — a revisão pré-auditoria encontrou uma segunda doença na correção

A primeira correção candidata trocou `_PEM_BEGIN` (autossobreponível) por um corpo
`BEGIN[A-Z ]*PRIVATE KEY-----` sem os traços — mas manteve o quantificador guloso
`[A-Z ]*` seguido de um sufixo **obrigatório que pode não existir**. Isso é, em si, o
padrão de custo superlinear: `"BEGIN " * n`, sem `PRIVATE KEY-----` em lugar nenhum, faz
cada ocorrência de `BEGIN` retroceder por todo o resto do texto — `O(n²)`. A revisão
pré-Codex achou isso **depois** de a correção candidata já ter sido reportada como
resolvida, antes de qualquer auditoria independente confirmar.

A correção desta segunda rodada substitui a busca de corpo por duas primitivas sem
quantificador-mais-sufixo: uma classe de caractere repetida sem nada obrigatório depois
dela (`[A-Z ]+`, sempre `O(n)`, nunca retrocede) mais dois testes de string pura
(`str.endswith`, `str.find`) dentro de cada corrida já isolada. Ver a docstring de
`_localizar_corpos_pem` em `app/safety/redaction.py` para a prova completa.

E6-AUD7-002 (a chave estruturalmente sensível — `is_sensitive_key`/`structured`)
**não é tratado aqui**. Continua aberto, tratado em outra etapa. O rótulo
"E6-AUD7-001-REV2" usado neste módulo e em `redaction.py` é uma segunda rodada sobre o
MESMO finding AUD7-001 — não deve ser confundido com o E6-AUD7-002 do relatório original.

## E6-AUD7V-001 — o gate temporal em si era instável (verificação independente)

A verificação independente confirmou a correção e a linearidade de E6-AUD7-001-REV2, mas
achou o próprio *gate* de crescimento instável: numa execução isolada, o teste de razão
de `"BEGIN " * n` reprovou a implementação linear com uma razão de 3,41x, enquanto quatro
outras repetições da mesma implementação passaram normalmente. Ver
`docs/audits/e6-round-7v.md` para o relatório integral.

Causa: `_mais_rapido` (ver a seção "desempenho" abaixo) mede `time.perf_counter()`
(*wall-clock*) e toma o **mínimo de apenas 2 repetições**, sem aquecimento e sem
controle de coleta de lixo. Duas repetições só protegem contra ruído se o ruído atingir
no máximo uma delas; um evento do sistema operacional (ou uma coleta de lixo geracional
cujo limiar de alocação foi cruzado bem naquele ponto) que dure o suficiente para atingir
**as duas** chamadas de uma mesma medição contamina o mínimo inteiro — sem que o
algoritmo tenha mudado de custo. Reproduzido artificialmente (ver AGENT_LOG.md,
entrada de E6-AUD7V-001): injetar um atraso de 250ms em duas chamadas consecutivas do
ponto `2n` faz a metodologia antiga reportar 5,30x nas mesmíssimas amostras em que a
metodologia nova reporta 1,97x.

A correção troca a métrica por `_tempo_mediano_ms`: uma chamada de aquecimento (não
medida) mais 7 amostras independentes, cada uma precedida por `gc.collect()` (elimina o
lixo da amostra anterior antes de medir, para que uma coleta geracional não caia
*dentro* de uma medição), das quais se toma a **mediana** — não o mínimo. A mediana de 7
só se move se 4 ou mais amostras forem contaminadas na mesma direção; um único evento
transitório, por mais longo que seja, não derruba o teste. `time.process_time_ns()`
(tempo de CPU, a alternativa sugerida a wall-clock) foi medido e descartado: nesta
máquina Windows sua resolução empírica é de ~15,6ms (`GetProcessTimes` arredonda para o
tique do relógio do sistema), grande demais frente às medições de dezenas a poucas
centenas de milissegundos usadas aqui — teria trocado ruído de agendamento por ruído de
quantização, sem ganho. `time.perf_counter_ns()` (resolução empírica ~1µs nesta máquina)
continua sendo o relógio usado; o que mudou foi a amostragem, não o relógio.

A correção introduziu `_tempo_mediano_ms` (aquecimento + mediana de 7 amostras
sequenciais por tamanho), usada apenas pelos dois testes de crescimento que a
verificação independente apontou (`..._crescimento_e_linear_nas_familias_do_finding` e
`..._reproducao_exata_nao_regride`). `_mais_rapido` continua existindo, sem alteração,
para os dois testes de desempenho anteriores a esta rodada
(`test_e6_aud7_001_desempenho_permanece_linear_no_caso_de_e6_aud6_001` e
`test_e6_aud7_001_desempenho_e_linear_na_familia_de_sobreposicao`) — nenhum dos dois foi
apontado como instável, e seus limiares (8x/12x) já têm folga bem maior do que a
instabilidade observada aqui. Os testes funcionais (contraexemplo, diferencial
scanner/canônica, fuzz, sobreposição, regressão HTTP, `recognition_span`,
`replacement_span`) e o código de produção (`redaction.py`) não foram tocados.

## E6-AUD7V2-001 — a mediana-de-7-por-tamanho ainda falhava sob contenção localizada

A reverificação independente (`docs/audits/e6-round-7v2.md`) confirmou de novo a correção
funcional e a linearidade de E6-AUD7-001-REV2, mas achou `_tempo_mediano_ms` vulnerável a
um tipo diferente de ruído: numa bateria de dez tripletas sob dois processos concorrentes
de CPU, uma rodada produziu T(2n)/T(n) = 3,5066x — acima do limiar — com tempos de
98,199 / 344,347 / 410,535 ms. A causa não é o relógio nem a mediana em si — é a ORDEM da
medição. `_tempo_mediano_ms` mede as 7 amostras de `n`, DEPOIS as 7 de `2n`, DEPOIS as 7 de
`4n`: blocos sequenciais e contíguos por tamanho. Uma fase de contenção que dure o
suficiente para atingir 4 ou mais das 7 amostras de UM bloco desloca a mediana inteira
daquele tamanho, sem tocar nenhuma amostra dos outros dois — e é exatamente essa
assimetria (um tamanho contaminado, os outros dois normais) que produz uma razão fora da
faixa esperada, mesmo com o algoritmo continuando linear. Reproduzido de forma
determinística nesta correção com uma rajada de carga agendada para coincidir com a
janela de medição de `2n`: 8 de 10 rodadas ultrapassaram o limiar com a metodologia antiga
(ver AGENT_LOG.md, entrada de E6-AUD7V2-001, para a tabela completa).

A correção troca a AGREGAÇÃO, não o relógio nem o limiar: em vez de sete amostras
consecutivas por tamanho, sete TRIPLETAS independentes (`_ORDEM_DAS_TRIPLAS`), cada uma
medindo `n`, `2n` e `4n` uma vez cada, em ordens diferentes e balanceadas — nenhum tamanho
é sempre medido na mesma fase temporal. Uma rajada de contenção localizada num instante
só pode atingir a amostra que calhar de estar em execução naquele instante, espalhada
entre os três tamanhos e entre tripletas diferentes, em vez de se concentrar inteira nas
sete amostras de um único tamanho. `_razoes_por_triplas_intercaladas` calcula
T(2n)/T(n) e T(4n)/T(2n) DENTRO de cada tripleta e toma a MEDIANA dessas sete razões — a
mesma proteção estatística de antes (a mediana de 7 só se desloca se 4 ou mais forem
contaminadas), agora aplicada à razão por tripleta, não ao tempo por tamanho. O custo
total continua sendo 21 chamadas de `redact()` medidas (7 tripletas × 3 tamanhos) mais 3
de aquecimento — o mesmo orçamento de `_tempo_mediano_ms` (1 aquecimento + 21 amostras).

Validado com REV2 (limpa, sob dois processos concorrentes o tempo todo, e sob a rajada
agendada que quebrou a metodologia antiga): 10/10 rodadas abaixo do limiar em cada
cenário. Validado contra a candidata quadrática original (o localizador
`BEGIN[A-Z ]*PRIVATE KEY-----` com retrocesso, reintroduzido apenas num protótipo de
comparação isolado — nunca em `redaction.py`): 10/10 rodadas acima do limiar, limpa e sob
carga. `_LIMIAR_RAZAO` (2,75) não mudou — os dados novos continuam separando as duas
distribuições com folga (REV2 até 2,45x nos cenários testados; quadrática a partir de
3,00x). Ver `docs/audits/e6-round-7v2.md` e AGENT_LOG.md (entrada de E6-AUD7V2-001) para
as distribuições completas e o protocolo experimental. `_tempo_mediano_ms`,
`_cresce_com_gerador` e `_razoes` foram removidas: nada mais as chama.
"""

from __future__ import annotations

import gc
import hashlib
import itertools
import re
import statistics
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from fastapi.testclient import TestClient

from app.safety.redaction import (
    _PATTERNS,
    REDACTED,
    _localizar_corpos_pem,
    _scan_pem_block,
    detect_secret_spans,
    redact,
)
from tests.context_helpers import GIT, commit_all, init_repo, write

#: A expressão canônica do `pem_block`, tal como o catálogo a declara. Todo diferencial
#: mede o scanner contra **ela**, nunca contra uma reescrita.
PEM = _PATTERNS[0].pattern


# ============================================== a reprodução exata do contraexemplo

#: O contraexemplo independente que a Rodada 7 do Codex usou para provar a omissão.
#: Reproduzido caractere a caractere a partir de `docs/audits/e6-round-7.md`.
_B = "-----BEGIN PRIVATE KEY-----"
_E = "-----END PRIVATE KEY-----"
MATERIAL_SINTETICO = "MIIE_AUD7_SYNTHETIC_PRIVATE_MATERIAL"


def _u(x: str) -> str:
    return "BEGIN PRIVATE KEY-----" * 2 + x + _E


TEXTO_CONTRAEXEMPLO = _B + _E + _u(MATERIAL_SINTETICO) + _u("")

#: Os spans que o Codex reportou para a expressão canônica e para o scanner com o bug.
#: Fixados aqui como constantes de regressão: se um deles mudar, algo na expressão
#: canônica ou no texto do contraexemplo divergiu do relatório, e o teste deve acusar
#: isso antes de comparar scanner com scanner.
SPANS_CANONICOS_ESPERADOS: tuple[tuple[int, int], ...] = ((0, 52), (69, 157), (174, 226))
SPANS_DO_SCANNER_COM_BUG: tuple[tuple[int, int], ...] = ((0, 52), (152, 226))


def test_e6_aud7_001_o_contraexemplo_bate_com_o_relatorio_do_codex() -> None:
    """Confirma, antes de mais nada, que o texto reproduzido aqui é o mesmo do relatório.

    Se este teste falhar, a causa não é o scanner — é o contraexemplo ter sido transcrito
    errado. Ele tem de falhar *antes* de qualquer teste sobre o scanner, para que uma
    divergência de transcrição nunca seja lida como confirmação ou refutação do bug.
    """
    assert len(TEXTO_CONTRAEXEMPLO) == 226
    assert tuple(m.span() for m in PEM.finditer(TEXTO_CONTRAEXEMPLO)) == SPANS_CANONICOS_ESPERADOS


def test_e6_aud7_001_reproducao_exata_do_contraexemplo_independente() -> None:
    """A reprodução ponta a ponta: `_scan_pem_block` bate com a expressão canônica.

    Antes da correção, o scanner achava só `(0,52), (152,226)` — a abertura em `(69,157)`,
    que reaproveita os cinco traços finais da abertura em `(47,74)`, nunca aparecia, e
    `MATERIAL_SINTETICO` saía cru de `redact()`. Ver `test_e6_aud7_001_o_scanner_antigo_
    de_fato_falhava_aqui` para a prova de que isto **falhava** antes desta correção.
    """
    obtido = tuple(m.span() for m in _scan_pem_block(PEM, TEXTO_CONTRAEXEMPLO))
    assert obtido == SPANS_CANONICOS_ESPERADOS, (
        f"o scanner corrigido deveria bater com a expressão canônica; obtido {obtido}"
    )
    assert obtido != SPANS_DO_SCANNER_COM_BUG

    saida = redact(TEXTO_CONTRAEXEMPLO)
    assert MATERIAL_SINTETICO not in saida, (
        f"material sintético ainda visível após redact(): {saida!r}"
    )
    # Três marcadores, um por match canônico. O texto entre eles ("BEGIN PRIVATE KEY",
    # sem os traços finais — estes pertencem ao match seguinte) é o que sobra fora dos
    # três spans, e por isso o motor o preserva como texto público.
    assert saida == f"{REDACTED}BEGIN PRIVATE KEY{REDACTED}BEGIN PRIVATE KEY{REDACTED}"


def test_e6_aud7_001_grupos_e_recognition_replacement_span_batem_com_o_canonico() -> None:
    """Não só os limites do match — grupos, `recognition_span` e `replacement_span`
    também têm de ser idênticos aos que a expressão canônica produziria isoladamente para
    cada abertura, inclusive a que reaproveita traços da anterior.
    """
    esperados = list(PEM.finditer(TEXTO_CONTRAEXEMPLO))
    obtidos = list(_scan_pem_block(PEM, TEXTO_CONTRAEXEMPLO))

    assert len(esperados) == len(obtidos) == 3
    for esperado, obtido in zip(esperados, obtidos, strict=True):
        assert obtido.span() == esperado.span()
        assert obtido.group(0) == esperado.group(0)
        assert obtido.groups() == esperado.groups()

    spans = [s for s in detect_secret_spans(TEXTO_CONTRAEXEMPLO) if s.pattern_name == "pem_block"]
    assert len(spans) == 3
    for span, esperado in zip(spans, esperados, strict=True):
        assert (span.replacement_span.start, span.replacement_span.end) == esperado.span()
        assert (span.recognition_span.start, span.recognition_span.end) == esperado.span()


# ================================== prova contra a implementação anterior (item 10)


def _scan_pem_block_com_o_bug_de_aud7_001(
    pattern: re.Pattern[str], text: str
) -> Iterator[re.Match[str]]:
    """A implementação **exatamente como saiu da rodada 6**, antes desta correção.

    Reescrita aqui, literal — não importada — pelo mesmo motivo de `_CASCATA_HISTORICA`
    em `test_context_redaction_e5_round5.py`: o diferencial tem de comparar a correção
    contra uma segunda implementação congelada, nunca contra si mesma. `_PEM_BEGIN`, aqui,
    inclui os cinco traços iniciais — é exatamente essa inclusão que a torna
    autossobreponível e produz a omissão de E6-AUD7-001. A assinatura (`Iterator`, não
    `list`) é a mesma de `_Pattern.scan` — só assim ela serve para substituí-lo no teste
    de troca de árvore, mais abaixo.
    """
    pem_begin_com_bug = re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----")
    pem_end = re.compile(r"-----END[A-Z ]*PRIVATE KEY-----")

    cursor = 0
    for abertura in pem_begin_com_bug.finditer(text):
        if abertura.start() < cursor:
            continue
        if pem_end.search(text, abertura.end()) is None:
            return
        bloco = pattern.match(text, abertura.start())
        if bloco is None:
            continue
        yield bloco
        cursor = bloco.end()


def test_e6_aud7_001_o_scanner_antigo_de_fato_falhava_aqui() -> None:
    """O controle negativo: a implementação da rodada 6, reproduzida literalmente aqui,
    **diverge** da expressão canônica no contraexemplo — provando que o teste anterior
    (`test_e6_aud6_001_a_varredura_linear_acha_os_mesmos_matches`) não cobria esta família
    de entrada, e que a correção desta etapa endereça um bug real, não um risco teórico.
    """
    obtido_com_bug = [
        m.span() for m in _scan_pem_block_com_o_bug_de_aud7_001(PEM, TEXTO_CONTRAEXEMPLO)
    ]
    assert tuple(obtido_com_bug) == SPANS_DO_SCANNER_COM_BUG, (
        "a reconstrução do scanner antigo não reproduziu a omissão relatada pelo Codex — "
        f"obtido {obtido_com_bug}"
    )
    assert tuple(obtido_com_bug) != SPANS_CANONICOS_ESPERADOS

    # ... e a correção não reproduz o bug: o mesmo diferencial, com o scanner de verdade.
    obtido_corrigido = [m.span() for m in _scan_pem_block(PEM, TEXTO_CONTRAEXEMPLO)]
    assert tuple(obtido_corrigido) == SPANS_CANONICOS_ESPERADOS


def test_e6_aud7_001_falhava_na_arvore_anterior_passa_na_corrigida(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reprodução ponta a ponta de `redact()`, com o motor apontado para o scanner antigo.

    `_Pattern` é `frozen`/`slots`, e o `scan` de cada entrada de `_PATTERNS` é resolvido
    **na construção** da tupla — trocar o nome de módulo `_scan_pem_block` depois não
    afeta a instância já construída. Por isso o monkeypatch é em `_PATTERNS` inteiro:
    uma cópia da tupla com só o `pem_block` reconstruído usando o scanner antigo, o resto
    idêntico. `detect_secret_spans` lê `_PATTERNS` como global a cada chamada, então a
    troca tem efeito imediato.

    Confirma que, sob essa substituição, `MATERIAL_SINTETICO` **volta** a vazar — a prova
    de que este teste de regressão detecta especificamente o bug introduzido pela
    otimização da rodada 6, e não uma propriedade que já seria verdadeira de qualquer
    forma.
    """
    from dataclasses import replace

    from app.safety import redaction as modulo

    original = modulo._PATTERNS[0]
    assert original.name == "pem_block"
    com_bug = replace(original, scan=_scan_pem_block_com_o_bug_de_aud7_001)
    patterns_com_bug = (com_bug, *modulo._PATTERNS[1:])

    monkeypatch.setattr(modulo, "_PATTERNS", patterns_com_bug)
    saida_com_bug = modulo.redact(TEXTO_CONTRAEXEMPLO)
    assert MATERIAL_SINTETICO in saida_com_bug, (
        "a árvore com o scanner antigo deveria deixar o material sintético visível"
    )

    monkeypatch.undo()
    saida_corrigida = redact(TEXTO_CONTRAEXEMPLO)
    assert MATERIAL_SINTETICO not in saida_corrigida


# ==================================================== causa raiz, isolada e verificada


def test_e6_aud7_001_causa_raiz_e_a_autossobreposicao_do_padrao_de_abertura() -> None:
    """A causa raiz não é "a enumeração pula posições" em geral — é que `-----BEGIN[A-Z
    ]*PRIVATE KEY-----` começa e termina com o **mesmo** literal de cinco traços, e por
    isso é autossobreponível: um `finditer` não sobreposto consome esses traços na
    primeira ocorrência e não sobra nada para a segunda reivindicar.

    O corpo sem os traços iniciais (`BEGIN[A-Z ]*PRIVATE KEY-----`) não tem essa
    propriedade — o próprio `[A-Z ]*` exclui `-`, então um corpo sempre termina exatamente
    onde o próximo pode começar, e nunca precisa "emprestar" caractere nenhum do vizinho.
    Por isso a correção separa localizar o corpo (sem ambiguidade) de verificar os cinco
    traços (um teste local, independente de qual corpo foi achado primeiro).
    """
    pem_begin_com_dashes = re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----")
    corpo_sem_dashes = re.compile(r"BEGIN[A-Z ]*PRIVATE KEY-----")

    # Duas aberturas encadeadas sem separador nenhum: a segunda reaproveita os cinco
    # traços finais da primeira.
    caso = "-----" + "BEGIN PRIVATE KEY-----" * 2

    com_dashes = [m.span() for m in pem_begin_com_dashes.finditer(caso)]
    assert len(com_dashes) == 1, "a versão autossobreponível perde a segunda abertura"

    corpos = [m.span() for m in corpo_sem_dashes.finditer(caso)]
    assert len(corpos) == 2, "o corpo sem traços iniciais tem de achar as duas ocorrências"
    # As duas ocorrências são adjacentes, não sobrepostas entre si.
    assert corpos[0][1] == corpos[1][0]


def test_e6_aud7_001_o_deslocamento_de_cinco_caracteres_e_exato() -> None:
    """A abertura exige exatamente cinco traços — nem quatro, nem seis soltos — antes de
    `BEGIN`. Um corpo precedido por só quatro traços não é uma abertura válida.
    """
    quatro_tracos = "----BEGIN PRIVATE KEY----------END PRIVATE KEY-----"
    assert not list(PEM.finditer(quatro_tracos))
    assert not list(_scan_pem_block(PEM, quatro_tracos))

    seis_tracos = "------BEGIN PRIVATE KEY----------END PRIVATE KEY-----"
    esperado = [m.span() for m in PEM.finditer(seis_tracos)]
    obtido = [m.span() for m in _scan_pem_block(PEM, seis_tracos)]
    assert obtido == esperado
    assert esperado, "com seis traços a canônica reconhece um bloco (usando os últimos 5)"


# ============================================ diferencial exaustivo com a nova família


#: A família de peças da rodada 6, preservada — a correção não pode regredir nada que já
#: estava coberto.
_PECAS_PEM_V6: tuple[str, ...] = (
    "",
    "-",
    "-----",
    "-----BEGIN",
    "-----BEGIN ",
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN PRIVATE KEY PRIVATE KEY-----",
    "-----END",
    "-----END PRIVATE KEY-----",
    "-----END EC PRIVATE KEY-----",
    "MIIEvQIBADANBg",
    "\n",
    " ",
    "PRIVATE KEY-----",
    "BEGIN",
    "END",
    "texto",
)

#: A família que os geradores da rodada 6 **não** cobriam: corpo de abertura **sem** os
#: cinco traços iniciais — a peça que só é válida quando encadeada logo depois de uma
#: abertura ou terminador que termina em `-----`. É exatamente a forma de
#: `E6-AUD7-001`.
_PECAS_SOBREPOSICAO: tuple[str, ...] = (
    "BEGIN PRIVATE KEY-----",
    "BEGIN RSA PRIVATE KEY-----",
    "BEGIN PRIVATE KEY----------BEGIN PRIVATE KEY-----",
    "PRIVATE KEY-----BEGIN",
    "KEY-----BEGIN PRIVATE",
)

_PECAS_PEM: tuple[str, ...] = _PECAS_PEM_V6 + _PECAS_SOBREPOSICAO


def _diverge(texto: str) -> tuple[list[tuple[int, int, str]], list[tuple[int, int, str]]] | None:
    esperado = [(m.start(), m.end(), m.group(0)) for m in PEM.finditer(texto)]
    obtido = [(m.start(), m.end(), m.group(0)) for m in _scan_pem_block(PEM, texto)]
    return None if esperado == obtido else (esperado, obtido)


def test_e6_aud7_001_diferencial_exaustivo_com_a_familia_de_sobreposicao() -> None:
    """Diferencial exaustivo scanner-corrigido vs. expressão canônica, com a família de
    sobreposição (peças **sem** os cinco traços iniciais) explicitamente incluída — a
    ausência dela é o motivo de a suíte da rodada 6 não ter pego E6-AUD7-001.

    Combinatório até n=5, restrito às primeiras 15/10 peças nos graus maiores para manter
    o corpus tratável — a família de sobreposição está nas primeiras posições da tupla,
    então continua bem representada em todo grau.
    """
    casos = set(_PECAS_PEM)
    casos |= {a + b for a, b in itertools.product(_PECAS_PEM, repeat=2)}
    casos |= {a + b + c for a, b, c in itertools.product(_PECAS_PEM, repeat=3)}
    casos |= {a + b + c + d for a, b, c, d in itertools.product(_PECAS_PEM[:15], repeat=4)}
    casos |= {a + b + c + d + e for a, b, c, d, e in itertools.product(_PECAS_PEM[:10], repeat=5)}

    divergentes = [(caso, _diverge(caso)) for caso in casos if _diverge(caso) is not None]

    assert len(casos) > 100_000, "o corpus do diferencial encolheu"
    assert not divergentes, (
        f"{len(divergentes)} casos divergem da expressão canônica; "
        f"primeiro: {divergentes[0][0]!r} -> {divergentes[0][1]}"
    )


def _entradas_geradas(quantidade: int, largura_maxima: int, semente: str) -> list[str]:
    """Entradas pseudoaleatórias determinísticas, derivadas de SHA-256 — não `random`.

    Mesmo raciocínio de `test_e6_audit_round_6.py::_entradas_geradas`: `random` não
    promete a mesma sequência entre versões do Python, e este repositório trata geradores
    de teste como parte do contrato de reprodutibilidade. O alfabeto cobre exatamente os
    caracteres que `pem_block` lê, incluindo tudo que basta para montar `BEGIN`, `END`,
    `PRIVATE`, `KEY` e a família de sobreposição sem depender de nenhum literal fixo.
    """
    alfabeto = "-BEGINDPRVATEKY \n"
    entradas = []
    for indice in range(quantidade):
        fluxo = hashlib.sha256(f"{semente}:{indice}".encode()).digest()
        comprimento = 1 + fluxo[0] % largura_maxima
        entradas.append(
            "".join(
                alfabeto[fluxo[1 + posicao % 31] % len(alfabeto)] for posicao in range(comprimento)
            )
        )
    return entradas


def test_e6_aud7_001_fuzz_diferencial_deterministico() -> None:
    """O mesmo diferencial, sobre entradas geradas — determinístico, sem `random`."""
    divergentes = [
        caso
        for caso in _entradas_geradas(200_000, largura_maxima=90, semente="e6-aud7-001")
        if _diverge(caso) is not None
    ]
    assert not divergentes, (
        f"{len(divergentes)} entradas geradas divergem da expressão canônica; "
        f"primeira: {divergentes[0]!r}"
    )


# ======================================= os cenários nomeados exigidos pela tarefa


def test_e6_aud7_001_abertura_iniciada_nos_tracos_finais_de_outra() -> None:
    """O caso central do finding: uma abertura cujos cinco traços de fechamento também
    servem de cinco traços de abertura para a próxima — sem terminador entre elas.
    """
    caso = "-----BEGIN PRIVATE KEY-----BEGIN PRIVATE KEY-----MATERIAL-----END PRIVATE KEY-----"
    esperado = [m.span() for m in PEM.finditer(caso)]
    obtido = [m.span() for m in _scan_pem_block(PEM, caso)]
    assert obtido == esperado
    assert redact(caso).count(REDACTED) == len(esperado)
    assert "MATERIAL" not in redact(caso)


def test_e6_aud7_001_multiplas_sobreposicoes_consecutivas() -> None:
    """Uma cadeia de quatro aberturas encadeadas, cada uma reaproveitando os traços da
    anterior, com um único terminador no fim — deve virar um único bloco (a canônica
    consome tudo com `.*?` até o primeiro `END`), e a canônica é quem decide isso.
    """
    caso = "-----" + "BEGIN PRIVATE KEY-----" * 4 + "-----END PRIVATE KEY-----"
    esperado = [m.span() for m in PEM.finditer(caso)]
    obtido = [m.span() for m in _scan_pem_block(PEM, caso)]
    assert obtido == esperado
    assert len(esperado) == 1


def test_e6_aud7_001_bloco_valido_seguido_de_abertura_sobreposta() -> None:
    """Um bloco PEM completo e bem formado, imediatamente seguido de uma cadeia de
    aberturas sobrepostas entre si (a sobreposição não pode ser com o bloco válido: o
    match dele consome os próprios cinco traços finais junto com o terminador, então não
    sobra traço nenhum do bloco válido para a abertura seguinte reaproveitar — por isso a
    abertura seguinte traz os seus próprios cinco traços, e é a cadeia **depois** dela que
    se sobrepõe).
    """
    bloco_valido = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\n-----END PRIVATE KEY-----"
    cadeia_sobreposta = "-----" + "BEGIN PRIVATE KEY-----" * 2 + "SEGUNDO-----END PRIVATE KEY-----"
    caso = bloco_valido + cadeia_sobreposta
    esperado = [m.span() for m in PEM.finditer(caso)]
    obtido = [m.span() for m in _scan_pem_block(PEM, caso)]
    assert obtido == esperado
    assert len(esperado) == 2, "o bloco válido e a cadeia sobreposta são dois blocos distintos"
    assert "SEGUNDO" not in redact(caso)


def test_e6_aud7_001_abertura_sobreposta_seguida_de_bloco_valido() -> None:
    """O inverso: uma abertura sobreposta primeiro, seguida de um bloco PEM comum e bem
    formado, sem nenhuma relação de sobreposição com ele.
    """
    caso = (
        "-----BEGIN PRIVATE KEY-----BEGIN PRIVATE KEY-----PRIMEIRO-----END PRIVATE KEY-----"
        "\ntexto comum\n"
        "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\n-----END PRIVATE KEY-----"
    )
    esperado = [m.span() for m in PEM.finditer(caso)]
    obtido = [m.span() for m in _scan_pem_block(PEM, caso)]
    assert obtido == esperado
    assert len(esperado) == 2
    assert "PRIMEIRO" not in redact(caso)


def test_e6_aud7_001_muitos_candidatos_sobrepostos() -> None:
    """Uma cadeia longa (500 aberturas encadeadas por sobreposição) com um terminador só
    no fim — o motor tem de continuar encontrando exatamente um bloco, cobrindo tudo.
    """
    caso = "-----" + "BEGIN PRIVATE KEY-----" * 500 + "SEGREDO_NO_MEIO-----END PRIVATE KEY-----"
    esperado = [m.span() for m in PEM.finditer(caso)]
    obtido = [m.span() for m in _scan_pem_block(PEM, caso)]
    assert obtido == esperado
    assert len(esperado) == 1
    assert "SEGREDO_NO_MEIO" not in redact(caso)


def test_e6_aud7_001_terminadores_quase_validos_intercalados() -> None:
    """Terminadores que **não** casam `_PEM_END` intercalados entre aberturas sobrepostas
    — nenhum deles pode ser confundido com o terminador real.

    `[A-Z ]*PRIVATE KEY-----` depois de `END` aceita **qualquer** letra maiúscula ali —
    `"END EC PRIVATE KEY-----"` é uma forma válida do catálogo. Um "quase válido" de
    verdade precisa quebrar o char class com algo que ele exclui: um dígito.
    """
    quase_valido = "-----END3 PRIVATE KEY-----"
    caso = (
        "-----BEGIN PRIVATE KEY-----BEGIN PRIVATE KEY-----"
        + quase_valido * 50
        + "MATERIAL-----END PRIVATE KEY-----"
    )
    esperado = [m.span() for m in PEM.finditer(caso)]
    obtido = [m.span() for m in _scan_pem_block(PEM, caso)]
    assert obtido == esperado
    assert len(esperado) == 1, (
        "nenhum dos 50 terminadores quase válidos pode encerrar o bloco antes do real"
    )
    assert "MATERIAL" not in redact(caso)


def test_e6_aud7_001_terminador_muito_distante() -> None:
    """O terminador aparece só perto do fim de um texto grande, com sobreposições no
    início — o motor não pode parar de procurar cedo demais.
    """
    caso = (
        "-----BEGIN PRIVATE KEY-----BEGIN PRIVATE KEY-----"
        + "x" * 50_000
        + "MATERIAL_DISTANTE-----END PRIVATE KEY-----"
    )
    esperado = [m.span() for m in PEM.finditer(caso)]
    obtido = [m.span() for m in _scan_pem_block(PEM, caso)]
    assert obtido == esperado
    assert "MATERIAL_DISTANTE" not in redact(caso)


def test_e6_aud7_001_conteudo_semelhante_a_begin_nao_e_confundido() -> None:
    """Conteúdo dentro do bloco que contém a palavra `BEGIN` (ou fragmentos parecidos com
    ela) não pode ser tratado como uma nova abertura — só o que casa `_PEM_OPEN_BODY` de
    verdade (`BEGIN` + `[A-Z ]*` + `PRIVATE KEY-----`) conta.
    """
    caso = (
        "-----BEGIN PRIVATE KEY-----\n"
        "BEGIN OF SOMETHING ELSE, not a key marker\n"
        "BEGINNING middle text\n"
        "-----END PRIVATE KEY-----"
    )
    esperado = [m.span() for m in PEM.finditer(caso)]
    obtido = [m.span() for m in _scan_pem_block(PEM, caso)]
    assert obtido == esperado
    assert len(esperado) == 1


@pytest.mark.parametrize("multiplicador", [1, 2, 4])
def test_e6_aud7_001_composicao_2n_4n_para_crescimento(multiplicador: int) -> None:
    """A mesma composição adversarial (cadeia de aberturas sobrepostas + terminador
    único e distante) em n, 2n e 4n — usada pelo teste de desempenho para medir a razão
    de crescimento; aqui só confirma corretude em cada tamanho.
    """
    n = 2_000 * multiplicador
    caso = "-----" + "BEGIN PRIVATE KEY-----" * n + "MATERIAL-----END PRIVATE KEY-----"
    esperado = [m.span() for m in PEM.finditer(caso)]
    obtido = [m.span() for m in _scan_pem_block(PEM, caso)]
    assert obtido == esperado
    assert len(esperado) == 1
    assert "MATERIAL" not in redact(caso)


# =============================================================== regressão HTTP


@pytest.mark.skipif(GIT is None, reason="git indisponível no PATH")
def test_e6_aud7_001_material_nao_atravessa_a_rota_http_de_tasks(
    auth_api_client: TestClient, tmp_path: Path
) -> None:
    """A reprodução no mesmo *boundary* onde a Rodada 7 achou o vazamento: `POST
    /api/workspaces/{id}/tasks`, com o contraexemplo no campo `goal`.

    Não basta testar `redact()` isoladamente — o relatório reproduziu o vazamento **pela
    API**, então a prova de correção precisa atravessar `RedactingJSONResponse` de
    verdade: `TaskResponse.goal` sai de `_to_response` já passado por `redact()` (ver
    `api/tasks.py`), e a resposta inteira ainda atravessa o boundary de [04] §5 antes de
    virar bytes. Confirma nos **bytes crus** da resposta, não só no JSON decodificado —
    um vazamento que sobrevivesse a uma reserialização não seria pego só olhando o dict.
    """
    repo = tmp_path / "repo"
    init_repo(repo)
    write(repo, "README.md", "# projeto\n")
    commit_all(repo, "inicial")

    mesma_origem = {"Origin": "http://127.0.0.1:8756", "Sec-Fetch-Site": "same-origin"}
    workspace = auth_api_client.post(
        "/api/workspaces",
        json={"name": "ws", "type": "personal", "local_path": str(repo)},
        headers=mesma_origem,
    )
    assert workspace.status_code == 201, workspace.text

    resposta = auth_api_client.post(
        f"/api/workspaces/{workspace.json()['id']}/tasks",
        json={"title": "task com material PEM", "goal": TEXTO_CONTRAEXEMPLO},
        headers=mesma_origem,
    )

    assert resposta.status_code == 201, resposta.text
    assert MATERIAL_SINTETICO.encode() not in resposta.content, (
        f"material sintético visível nos bytes crus da resposta: {resposta.content!r}"
    )
    assert MATERIAL_SINTETICO not in resposta.json()["goal"]


# ==================================================================== desempenho


def _mais_rapido(texto: str, repeticoes: int = 3) -> float:
    """O menor tempo de `redact(texto)` em segundos — a menor amostra é a estimativa
    menos contaminada por ruído de escalonamento do sistema operacional.
    """
    melhor = float("inf")
    for _ in range(repeticoes):
        inicio = time.perf_counter()
        redact(texto)
        melhor = min(melhor, time.perf_counter() - inicio)
    return melhor


def test_e6_aud7_001_desempenho_permanece_linear_no_caso_de_e6_aud6_001() -> None:
    """Regressão de desempenho: a correção não pode reverter o ganho de E6-AUD6-001.
    Mesma reprodução: 196.000 caracteres, 7.000 aberturas, nenhum terminador.
    """
    abertura = "-----BEGIN PRIVATE KEY-----\n"
    corpo_patologico = abertura * 7000
    corpo_comum = ("linha de texto legitimo qualquer aqui\n" * 6000)[: len(corpo_patologico)]

    assert len(corpo_patologico) == 196_000
    patologico = _mais_rapido(corpo_patologico)
    comum = _mais_rapido(corpo_comum)

    assert redact(corpo_patologico) == corpo_patologico
    assert patologico < 3 * comum, (
        f"custo do pem_block {patologico / comum:.1f}x o texto comum "
        f"({patologico * 1000:.0f} ms vs {comum * 1000:.0f} ms) — regressão de E6-AUD6-001"
    )


def test_e6_aud7_001_desempenho_e_linear_na_familia_de_sobreposicao() -> None:
    """A forma nova de ataque que **este** finding introduziu: cadeias de aberturas
    sobrepostas. Cresce n / 2n / 4n; a razão de custo tem de dobrar, não quadruplicar.

    Dois formatos, porque atacam partes diferentes do algoritmo:

    * sem terminador nenhum — testa que a parada antecipada continua funcionando quando
      as aberturas ativas são as sobrepostas, não as separadas por traço;
    * com um terminador único e distante — testa que resolver o bloco (a segunda
      passada com a expressão canônica) não vira caro por haver muitas aberturas
      candidatas "dentro" do que acaba sendo um único bloco grande.
    """
    formatos = (
        ("sem terminador", ""),
        ("com terminador distante", "-----END PRIVATE KEY-----"),
    )
    for rotulo, sufixo in formatos:
        tempos = []
        for n in (2000, 4000, 8000):
            caso = "-----" + "BEGIN PRIVATE KEY-----" * n + sufixo
            tempos.append(_mais_rapido(caso, repeticoes=2))

        razao_2x = tempos[1] / tempos[0] if tempos[0] > 0 else float("inf")
        razao_4x = tempos[2] / tempos[0] if tempos[0] > 0 else float("inf")
        assert razao_2x < 8, (
            f"[{rotulo}] dobrar a cadeia sobreposta multiplicou o custo por {razao_2x:.1f}x"
        )
        assert razao_4x < 12, (
            f"[{rotulo}] quadruplicar a cadeia sobreposta multiplicou o custo por "
            f"{razao_4x:.1f}x (linear seria ~4, quadrático ~16)"
        )


# ============================================ E6-AUD7-001-REV2 — o localizador em si

#: O corpo, como um regex de quantificador-mais-sufixo o expressaria. Usado **só** como
#: oráculo do diferencial — nunca no motor, e nunca com corpus grande o bastante para
#: expor o próprio custo quadrático dele (isso é medido à parte, sem diferencial).
_CORPO_REGEX_QUADRATICO = re.compile(r"BEGIN[A-Z ]*PRIVATE KEY-----")


def _diverge_localizador(texto: str) -> bool:
    esperado = [m.span() for m in _CORPO_REGEX_QUADRATICO.finditer(texto)]
    obtido = list(_localizar_corpos_pem(texto))
    return esperado != obtido


def test_e6_aud7_001_rev2_localizador_bate_com_o_regex_de_corpo() -> None:
    """`_localizar_corpos_pem` tem de achar exatamente o que
    `BEGIN[A-Z ]*PRIVATE KEY-----` acharia — só que sem o retrocesso dele.

    O corpus aqui é pequeno de propósito (o oráculo É a expressão com o bug de
    desempenho; um corpus grande demoraria para calcular o próprio oráculo). O corpus
    grande, sem oráculo por regex, está nos testes de desempenho abaixo.
    """
    pecas = (
        "",
        "-",
        "-----",
        "BEGIN",
        "BEGIN ",
        "BEGIN PRIVATE KEY-----",
        "BEGIN RSA PRIVATE KEY-----",
        "PRIVATE KEY-----",
        "PRIVATE KEY",
        "PRIVATE",
        "KEY-----",
        "KEY",
        " ",
        "\n",
        "X",
        "texto",
        "END",
        "BEGIN BEGIN PRIVATE KEY-----",
        "BEGINPRIVATE",
        "BEGIN PRIVATE",
    )
    casos = set(pecas)
    casos |= {a + b for a, b in itertools.product(pecas, repeat=2)}
    casos |= {a + b + c for a, b, c in itertools.product(pecas, repeat=3)}
    casos |= {a + b + c + d for a, b, c, d in itertools.product(pecas[:14], repeat=4)}

    divergentes = [caso for caso in casos if _diverge_localizador(caso)]
    assert len(casos) > 25_000, "o corpus do diferencial do localizador encolheu"
    assert not divergentes, f"{len(divergentes)} casos divergem; primeiro: {divergentes[0]!r}"


def test_e6_aud7_001_rev2_localizador_fuzz_deterministico() -> None:
    """O mesmo diferencial, com entradas geradas por SHA-256 — sem `random`."""
    divergentes = [
        caso
        for caso in _entradas_geradas(50_000, largura_maxima=90, semente="e6-aud7-001-rev2-loc")
        if _diverge_localizador(caso)
    ]
    assert not divergentes, (
        f"{len(divergentes)} entradas geradas divergem; primeira: {divergentes[0]!r}"
    )


def test_e6_aud7_001_rev2_scanner_continua_batendo_com_o_canonico() -> None:
    """O diferencial de ponta a ponta (scanner vs. expressão canônica de verdade, não o
    oráculo interno do localizador) continua com o mesmo corpus da primeira correção —
    a REV2 não pode reabrir nenhuma divergência que a primeira correção fechou.
    """
    divergentes = [
        caso
        for caso in _entradas_geradas(200_000, largura_maxima=90, semente="e6-aud7-001")
        if _diverge(caso) is not None
    ]
    assert not divergentes, (
        f"{len(divergentes)} entradas do corpus original voltaram a divergir após a REV2; "
        f"primeira: {divergentes[0]!r}"
    )


# ==================== desempenho: a família que a REV2 corrige (item 5, requisitos 1-3)

#: Meio caminho entre o pior caso linear observado (REV2, ~2,1x por dobra, em dezenas de
#: rodadas — ver AGENT_LOG.md, entrada de E6-AUD7V-001) e o melhor caso quadrático
#: observado (implementação anterior à REV2, ~3,4x por dobra). Não é o limiar antigo
#: (3,0) mantido por inércia: foi recalculado com a metodologia de mediana-por-tamanho
#: e é consequência das medições, não ponto de partida — ver a entrada de AGENT_LOG.md
#: para a tabela completa de onde 2,10 e 3,44 vieram. A correção de E6-AUD7V2-001 trocou
#: a AGREGAÇÃO (ver `_razoes_por_triplas_intercaladas`), não este valor: os dados novos
#: (REV2 até 2,45x, quadrática a partir de 3,00x, nos mesmos cenários — AGENT_LOG.md,
#: entrada de E6-AUD7V2-001) continuam com folga confortável dos dois lados de 2,75.
_LIMIAR_RAZAO = 2.75

#: As 7 tripletas da prova temporal — cada uma mede `n`, `2n` e `4n` uma vez cada, na
#: ordem indicada (índices em `textos`: 0=n, 1=2n, 2=4n). São as 6 permutações de 3
#: elementos, uma vez cada, mais uma repetição da primeira para fechar 7 — o mesmo custo
#: total (21 janelas medidas) da mediana-de-7-por-tamanho que esta tabela substitui
#: (E6-AUD7V2-001). A propriedade que importa: cada tamanho aparece em posições
#: temporais diferentes entre as tripletas, então nenhum tamanho é sempre medido na
#: mesma fase — uma rajada de contenção localizada num instante só atinge a amostra que
#: calhar de estar em execução naquele instante, nunca as sete amostras de um só tamanho.
_ORDEM_DAS_TRIPLAS: tuple[tuple[int, int, int], ...] = (
    (0, 1, 2),
    (2, 0, 1),
    (1, 2, 0),
    (0, 2, 1),
    (1, 0, 2),
    (2, 1, 0),
    (0, 1, 2),
)


def _medir_uma_amostra_ms(texto: str) -> float:
    """Uma medição isolada de `redact(texto)`, em milissegundos de *wall-clock*
    (`time.perf_counter_ns`), com `gc.collect()` imediatamente antes — a mesma disciplina
    de coleta de lixo de antes, aplicada a uma amostra por vez em vez de sete seguidas.
    """
    gc.collect()
    inicio = time.perf_counter_ns()
    redact(texto)
    return (time.perf_counter_ns() - inicio) / 1e6


class ResultadoTriplas(NamedTuple):
    """As razões de crescimento medidas por `_razoes_por_triplas_intercaladas`, com
    detalhe suficiente para uma mensagem de falha diagnosticável: razão por tripleta,
    mediana final, e os tempos brutos que produziram cada razão.
    """

    mediana_2x: float
    mediana_4x: float
    razoes_2x: list[float]
    razoes_4x: list[float]
    tempos_por_tripla: list[tuple[float, float, float]]


def _razoes_por_triplas_intercaladas(textos: tuple[str, ...]) -> ResultadoTriplas:
    """Mediana de T(2n)/T(n) e de T(4n)/T(2n) sobre `_ORDEM_DAS_TRIPLAS` — a correção de
    E6-AUD7V2-001 para a fragilidade que sobrou em `_tempo_mediano_ms`.

    ## O problema que isto corrige (E6-AUD7V2-001)

    A mediana-de-7-por-tamanho (a correção anterior, E6-AUD7V-001) mede as 7 amostras de
    `n`, DEPOIS as 7 de `2n`, DEPOIS as 7 de `4n`: blocos sequenciais e contíguos por
    tamanho. Ela protege contra um evento *breve* (a mediana de 7 só se desloca se 4 ou
    mais amostras forem contaminadas), mas não contra uma fase de contenção que dure o
    bastante para atingir 4+ das 7 amostras de UM bloco inteiro — nesse caso a mediana
    daquele tamanho se desloca sozinha, sem que os outros dois tamanhos sejam tocados, e a
    razão entre eles sai da faixa esperada mesmo com o algoritmo continuando linear. A
    reverificação independente reproduziu isso sob dois processos concorrentes de CPU:
    T(2n)/T(n) = 3,5066x numa rodada, com tempos de 98,199 / 344,347 / 410,535 ms (ver
    `docs/audits/e6-round-7v2.md`).

    ## Por que tripletas em vez de mais amostras ou limiar mais frouxo

    Mais amostras do mesmo bloco sequencial não ajudam: uma fase de contenção longa o
    bastante ainda pode atingir a maioria de um bloco maior, e um limiar mais frouxo abriria
    espaço para a própria regressão quadrática que este teste existe para pegar. O que
    protege contra contenção **localizada no tempo** é não ter blocos contíguos por
    tamanho: cada tripleta mede os três tamanhos uma vez cada, em ordens diferentes
    (`_ORDEM_DAS_TRIPLAS`), de forma que uma rajada em qualquer instante só pode contaminar
    a amostra que calhar de estar em execução — nunca as sete amostras de um único
    tamanho. A razão é calculada DENTRO de cada tripleta (as três medições de uma mesma
    tripleta compartilham a mesma janela de contenção ambiente, então a razão entre elas
    cancela boa parte do ruído comum), e a mediana das sete razões preserva a mesma
    proteção estatística de antes — só que agora aplicada à razão, não ao tempo bruto.

    ## Validação (AGENT_LOG.md, entrada de E6-AUD7V2-001, para as tabelas completas)

    REV2, limpa e sob dois cenários de carga (dois processos concorrentes o tempo todo; e
    uma rajada de 15 processos agendada especificamente para coincidir com a janela de
    medição de `2n` — o cenário que quebrou `_tempo_mediano_ms` 8 de 10 vezes): 10/10
    rodadas abaixo do limiar em cada cenário, nos três casos. A candidata quadrática
    original (localizador com retrocesso, reintroduzida só num protótipo de comparação
    isolado, nunca em `redaction.py`): 10/10 rodadas acima do limiar, limpa e sob carga.
    """
    for texto in textos:
        redact(texto)  # aquecimento por tamanho: fora de qualquer janela medida

    razoes_2x: list[float] = []
    razoes_4x: list[float] = []
    tempos_por_tripla: list[tuple[float, float, float]] = []

    for ordem in _ORDEM_DAS_TRIPLAS:
        tempos: list[float] = [0.0, 0.0, 0.0]
        for indice in ordem:
            tempos[indice] = _medir_uma_amostra_ms(textos[indice])
        razoes_2x.append(tempos[1] / tempos[0] if tempos[0] > 0 else float("inf"))
        razoes_4x.append(tempos[2] / tempos[1] if tempos[1] > 0 else float("inf"))
        tempos_por_tripla.append((tempos[0], tempos[1], tempos[2]))

    return ResultadoTriplas(
        mediana_2x=statistics.median(razoes_2x),
        mediana_4x=statistics.median(razoes_4x),
        razoes_2x=razoes_2x,
        razoes_4x=razoes_4x,
        tempos_por_tripla=tempos_por_tripla,
    )


def _mensagem_de_falha_triplas(rotulo: str, resultado: ResultadoTriplas) -> str:
    """Mensagem de diagnóstico comum às duas asserções de uma `ResultadoTriplas`: razões
    individuais por tripleta, mediana, limiar e os tempos brutos que as produziram — o
    suficiente para depurar uma falha sem precisar reexecutar o teste com instrumentação.
    """
    tempos_fmt = [tuple(round(t, 1) for t in tp) for tp in resultado.tempos_por_tripla]
    return (
        f"[{rotulo}] mediana T(2n)/T(n)={resultado.mediana_2x:.3f}x "
        f"mediana T(4n)/T(2n)={resultado.mediana_4x:.3f}x (limiar {_LIMIAR_RAZAO}x) — "
        f"razões 2x por tripleta: {[round(r, 3) for r in resultado.razoes_2x]}; "
        f"razões 4x por tripleta: {[round(r, 3) for r in resultado.razoes_4x]}; "
        f"tempos (n, 2n, 4n) ms por tripleta: {tempos_fmt}"
    )


@pytest.mark.parametrize(
    ("rotulo", "gerador", "tamanhos"),
    [
        (
            "BEGIN(espaço) * n, sem PRIVATE KEY em lugar nenhum — o caso do finding",
            lambda n: "BEGIN " * n,
            (20_000, 40_000, 80_000),
        ),
        (
            "BEGIN seguido de uma corrida grande de maiúsculas que nunca fecha",
            lambda n: "BEGIN " + "X" * n,
            (50_000, 100_000, 200_000),
        ),
        (
            "muitos BEGIN dentro da mesma corrida, que nunca fecha",
            lambda n: ("BEGIN " * n) + "X" * n,
            (20_000, 40_000, 80_000),
        ),
        (
            "sufixos PRIVATE KEY quase completos, repetidos (falta 1 caractere)",
            lambda n: ("BEGIN PRIVATE KE" + " ") * n,
            (20_000, 40_000, 80_000),
        ),
        (
            "um único PRIVATE KEY----- distante, depois de muito ruído",
            lambda n: "BEGIN " + "X" * n + "PRIVATE KEY-----",
            (50_000, 100_000, 200_000),
        ),
    ],
)
def test_e6_aud7_001_rev2_crescimento_e_linear_nas_familias_do_finding(
    rotulo: str, gerador: Callable[[int], str], tamanhos: tuple[int, ...]
) -> None:
    """`redact()` sobre cada família adversarial do finding cresce ~linearmente.

    Requisitos 2/3 da tarefa: a asserção é sobre a **razão** de crescimento entre n, 2n e
    4n, não sobre um tempo absoluto — um limiar absoluto seria frágil (dependeria da
    máquina); a razão não. Medido com a metodologia corrigida por E6-AUD7V2-001
    (`_razoes_por_triplas_intercaladas`: sete tripletas independentes, cada uma medindo os
    três tamanhos uma vez em ordens diferentes, mediana das razões por tripleta — não mais
    mediana-de-7-amostras-por-tamanho, vulnerável a contenção localizada num só bloco): as
    cinco famílias ficam entre 1,8x e 2,45x a cada dobra de tamanho, em dezenas de rodadas,
    limpas e sob duas formas de contenção de CPU (constante e em rajada agendada). Medido
    na implementação anterior à REV2 (regex de corpo com retrocesso), com a mesma
    metodologia: 3,0x–4,3x — a assinatura de `O(n²)` continua nitidamente separada.

    **O limiar é `_LIMIAR_RAZAO` = 2,75, consequência das medições, não ponto de
    partida.** Ver a docstring de `_LIMIAR_RAZAO` e AGENT_LOG.md (entrada de
    E6-AUD7V2-001) para a tabela completa de distribuições que o embasam. Um limiar frouxo
    (o primeiro que escrevi aqui era 8×, depois 3,0) deixaria passar exatamente a
    regressão que este teste existe para pegar.
    """
    textos = tuple(gerador(n) for n in tamanhos)
    resultado = _razoes_por_triplas_intercaladas(textos)
    mensagem = _mensagem_de_falha_triplas(rotulo, resultado)
    assert resultado.mediana_2x < _LIMIAR_RAZAO, mensagem
    assert resultado.mediana_4x < _LIMIAR_RAZAO, mensagem


def test_e6_aud7_001_rev2_reproducao_exata_nao_regride() -> None:
    """Requisito 1 da tarefa, na forma de teste automatizado: `"BEGIN " * n` não pode
    voltar a crescer quadraticamente. Reprodução direta do que a revisão pré-auditoria
    mediu: T(2n)/T(n) e T(4n)/T(2n) para a família exata do problema, com o texto
    completo passando por `redact()` — não só pelo localizador isolado.

    Esta é, literalmente, a reprodução exata dos dois achados que este módulo já
    corrigiu duas vezes: a instabilidade de E6-AUD7V-001 (mínimo-de-2 sem aquecimento) e
    a de E6-AUD7V2-001 (mediana-de-7-por-tamanho vulnerável a contenção localizada num
    bloco inteiro). A correção atual (`_razoes_por_triplas_intercaladas`, `_LIMIAR_RAZAO`)
    está documentada na docstring do módulo e em
    `test_e6_aud7_001_rev2_crescimento_e_linear_nas_familias_do_finding`.
    """
    n_base = 20_000
    textos = tuple("BEGIN " * (n_base * multiplicador) for multiplicador in (1, 2, 4))
    resultado = _razoes_por_triplas_intercaladas(textos)
    mensagem = _mensagem_de_falha_triplas("reproducao exata", resultado)

    assert resultado.mediana_2x < _LIMIAR_RAZAO, mensagem
    assert resultado.mediana_4x < _LIMIAR_RAZAO, mensagem


#: O localizador com quantificador guloso e sufixo obrigatório que motivou a correção
#: original de E6-AUD7-001-REV2 — `O(n²)` porque `[A-Z ]*` retrocede procurando
#: `PRIVATE KEY-----`, que nunca aparece em `"BEGIN " * n`. Existe só para o controle
#: negativo abaixo; nunca é usado por `redaction.py`.
_LOCALIZADOR_COM_RETROCESSO = re.compile(r"BEGIN[A-Z ]*PRIVATE KEY-----")


def _localizador_com_retrocesso(text: str) -> Iterator[tuple[int, int]]:
    for m in _LOCALIZADOR_COM_RETROCESSO.finditer(text):
        yield m.start(), m.end()


def test_e6_aud7v2_001_a_metodologia_de_triplas_reprova_o_localizador_com_retrocesso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Controle negativo automatizado de E6-AUD7V2-001 (item 11 da tarefa): a mesma
    metodologia (`_razoes_por_triplas_intercaladas`) tem de continuar reprovando a
    candidata que motivou a correção original de E6-AUD7-001-REV2 — o localizador com
    quantificador guloso e sufixo obrigatório, `O(n²)` — mesmo depois de a agregação
    temporal ter mudado. Trocar só a agregação e nunca mais testar contra um caso
    quadrático deixaria passar despercebida uma futura regressão que enfraquecesse a
    nova agregação a ponto de ela aceitar qualquer coisa.

    Usa tamanhos bem menores que os das famílias de crescimento (2.000/4.000/8.000
    contra 20.000–200.000): a 80.000 caracteres esta candidata quadrática levaria dezenas
    de segundos por chamada de `redact()` (medido: 2.000→4.000→8.000→16.000 caracteres
    levam ~43/158/640/2385 ms, a ~3,6-3,7x por dobra — a assinatura de `O(n²)`), o que
    tornaria a suíte proibitivamente lenta sem provar nada que os tamanhos menores não
    provem. A metodologia testada é a MESMA; só a escala muda (item 11 da tarefa).

    O localizador é trocado só neste teste, via `monkeypatch` no atributo do módulo
    (`app.safety.redaction._localizar_corpos_pem`) — nunca no arquivo `redaction.py` — e
    é restaurado automaticamente ao final do teste, inclusive se a asserção falhar.
    """
    monkeypatch.setattr("app.safety.redaction._localizar_corpos_pem", _localizador_com_retrocesso)
    textos = tuple("BEGIN " * n for n in (2_000, 4_000, 8_000))
    resultado = _razoes_por_triplas_intercaladas(textos)
    mensagem = _mensagem_de_falha_triplas("controle negativo (quadrático)", resultado)

    assert resultado.mediana_2x >= _LIMIAR_RAZAO, (
        f"a metodologia de tripletas deixou de distinguir o candidato quadrático: {mensagem}"
    )
    assert resultado.mediana_4x >= _LIMIAR_RAZAO, (
        f"a metodologia de tripletas deixou de distinguir o candidato quadrático: {mensagem}"
    )


def test_e6_aud7_001_rev2_a_sobreposicao_continua_corrigida() -> None:
    """Requisito 5 da tarefa: a correção de sobreposição da primeira rodada de
    E6-AUD7-001 continua verde depois da REV2 — reprodução direta do contraexemplo
    original, ponta a ponta.
    """
    obtido = tuple(m.span() for m in _scan_pem_block(PEM, TEXTO_CONTRAEXEMPLO))
    assert obtido == SPANS_CANONICOS_ESPERADOS
    saida = redact(TEXTO_CONTRAEXEMPLO)
    assert MATERIAL_SINTETICO not in saida


@pytest.mark.skipif(GIT is None, reason="git indisponível no PATH")
def test_e6_aud7_001_rev2_regressao_http_continua_verde(
    auth_api_client: TestClient, tmp_path: Path
) -> None:
    """A mesma regressão HTTP da primeira correção, repetida após a REV2 — a rota real,
    o mesmo *boundary*, o mesmo contraexemplo.
    """
    repo = tmp_path / "repo"
    init_repo(repo)
    write(repo, "README.md", "# projeto\n")
    commit_all(repo, "inicial")

    mesma_origem = {"Origin": "http://127.0.0.1:8756", "Sec-Fetch-Site": "same-origin"}
    workspace = auth_api_client.post(
        "/api/workspaces",
        json={"name": "ws", "type": "personal", "local_path": str(repo)},
        headers=mesma_origem,
    )
    assert workspace.status_code == 201, workspace.text

    resposta = auth_api_client.post(
        f"/api/workspaces/{workspace.json()['id']}/tasks",
        json={"title": "task com material PEM (rev2)", "goal": TEXTO_CONTRAEXEMPLO},
        headers=mesma_origem,
    )

    assert resposta.status_code == 201, resposta.text
    assert MATERIAL_SINTETICO.encode() not in resposta.content
