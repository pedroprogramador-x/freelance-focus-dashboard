"""Sexta rodada de auditoria da E6 — custo do `pem_block` e o rótulo que o GATE 1 não via.

Dois findings, um em cada direção do mesmo arquivo:

* **E6-AUD6-001** — o catálogo tinha um padrão com custo quadrático. Não é vazamento; é a
  porta ao lado: uma entrada de 196 KB feita de aberturas PEM sem terminador prendia uma
  rota HTTP por segundos. Um detector que roda em **todo** boundary de saída ([04] §5) é
  um lugar onde custo é contrato.
* **E6-AUD6-002** — o GATE 1 afirmava três coisas, todas sobre **conjuntos de casos**.
  Nenhuma olhava para dentro de um caso já autorizado a divergir, e por isso um mutante
  podia apagar `Bearer ` sem que o gate reclamasse.

Cada teste daqui falhava antes da correção desta rodada e passa depois — os de tempo por
medição, os de comportamento por asserção direta.
"""

from __future__ import annotations

import ast
import hashlib
import itertools
import re
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.safety.redaction import (
    _PATTERNS,
    REDACTED,
    _scan_pem_block,
    detect_secret_spans,
    redact,
)
from tests.context_helpers import GIT, commit_all, init_repo, write
from tests.test_context_redaction_e5_round5 import (
    _CASCATA_HISTORICA,
    _mascara_da_emissao,
    _mascara_de_um_texto_redigido,
    _mascara_historica,
    _oraculo_sobre,
    _redact_historico,
    _rotulos_protegidos,
)

#: A expressão canônica do `pem_block`, tal como o catálogo a declara. O diferencial mede
#: a varredura linear contra **ela**, não contra uma reescrita.
PEM = _PATTERNS[0].pattern


# ============================================ E6-AUD6-001 — custo quadrático do pem_block

#: A reprodução exata do relatório: 7.000 aberturas, nenhum terminador, 196.000 caracteres.
ABERTURA = "-----BEGIN PRIVATE KEY-----\n"
CORPO_PATOLOGICO = ABERTURA * 7000

#: Texto comum do mesmo tamanho — a linha de base contra a qual "mesma ordem de grandeza"
#: é medida. Comparar com uma constante em milissegundos amarraria o teste à máquina.
CORPO_COMUM = ("linha de texto legitimo qualquer aqui\n" * 6000)[: len(CORPO_PATOLOGICO)]

#: Um bloco PEM completo e bem formado, que tem de continuar sendo redigido.
BLOCO = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQ\n-----END PRIVATE KEY-----"


def _mais_rapido(texto: str, repeticoes: int = 3) -> float:
    """O menor tempo de `redact(texto)` em segundos.

    O mínimo, não a média: ruído de escalonamento só empurra para cima, então a menor
    amostra é a estimativa menos contaminada do custo real.
    """
    melhor = float("inf")
    for _ in range(repeticoes):
        inicio = time.perf_counter()
        redact(texto)
        melhor = min(melhor, time.perf_counter() - inicio)
    return melhor


def test_e6_aud6_001_reproducao_196k_aberturas_sem_terminador() -> None:
    """A reprodução do relatório: 196.000 caracteres, 7.000 `BEGIN`, nenhum `END`.

    Medido antes da correção, nesta máquina: **7,3 s** em processo (9,7 s pela rota HTTP),
    contra 169 ms para texto comum do mesmo tamanho — 43x a linha de base. Depois: 130 ms,
    **abaixo** do texto comum, porque o texto patológico não tem nenhuma corrida de palavra
    para as âncoras do motor visitarem.

    As duas afirmações são relativas de propósito. Um limite em milissegundos passaria a
    medir a máquina; o que o finding pede é que o `pem_block` volte para a ordem de
    grandeza do resto do catálogo.
    """
    assert len(CORPO_PATOLOGICO) == 196_000
    assert CORPO_PATOLOGICO.count("-----BEGIN") == 7000
    assert "-----END" not in CORPO_PATOLOGICO

    patologico = _mais_rapido(CORPO_PATOLOGICO)
    comum = _mais_rapido(CORPO_COMUM)

    assert redact(CORPO_PATOLOGICO) == CORPO_PATOLOGICO, (
        "abertura sem terminador não é bloco PEM e não pode ser redigida"
    )
    assert patologico < 3 * comum, (
        f"o pem_block custa {patologico / comum:.1f}x o texto comum do mesmo tamanho "
        f"({patologico * 1000:.0f} ms contra {comum * 1000:.0f} ms) — o custo quadrático "
        "de E6-AUD6-001 voltou"
    )
    assert patologico < 2.0, f"{patologico:.2f} s para 196 KB é tempo de rota travada"


def test_e6_aud6_001_o_custo_e_linear_no_tamanho() -> None:
    """Quadruplicar a entrada quadruplica o custo — não o multiplica por dezesseis.

    É a medição que separa "ficou mais rápido" de "deixou de ser quadrático": com `.*?` a
    razão entre 4n e n é ~16; linear é ~4. O limite de 8 é o meio geométrico entre os dois,
    folgado para ruído e apertado o bastante para não aceitar o comportamento antigo.
    """
    pequeno = _mais_rapido(ABERTURA * 1750)
    grande = _mais_rapido(ABERTURA * 7000)

    assert grande < 8 * pequeno, (
        f"quadruplicar a entrada multiplicou o custo por {grande / pequeno:.1f} "
        "(linear seria ~4, quadrático ~16)"
    )


@pytest.mark.skipif(GIT is None, reason="git indisponível no PATH")
def test_e6_aud6_001_pela_rota_http(auth_api_client: TestClient, tmp_path: Path) -> None:
    """A reprodução onde o finding a mediu: uma rota HTTP normal, com o corpo de 196 KB.

    `POST /api/workspaces/{id}/context` devolve a entrada criada **pelo boundary** de
    [04] §5, então o corpo inteiro atravessa `redact_document` → `redact` na resposta. Era
    aqui que os 9,7 s do relatório apareciam.
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

    inicio = time.perf_counter()
    resposta = auth_api_client.post(
        f"/api/workspaces/{workspace.json()['id']}/context",
        json={
            "domain": "modules",
            "title": "corpo patologico",
            "body": CORPO_PATOLOGICO,
        },
        headers=mesma_origem,
    )
    decorrido = time.perf_counter() - inicio

    assert resposta.status_code == 201, resposta.text
    assert resposta.json()["body"] == CORPO_PATOLOGICO
    assert decorrido < 3.0, f"a rota levou {decorrido:.2f} s para 196 KB de aberturas PEM"


def test_e6_aud6_001_blocos_completos_continuam_redigidos() -> None:
    """Regressão explícita: o que o padrão reconhecia, ele continua reconhecendo.

    Corrigir custo é a forma mais fácil de perder cobertura sem perceber — foi assim que
    E6-AUD5-001 nasceu. Aqui cada forma bem formada é conferida uma a uma.
    """
    formas = (
        BLOCO,
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----\nMIIE\n-----END EC PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY----------END PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY-----\n\n-----END PRIVATE KEY-----",
    )
    for forma in formas:
        assert redact(forma) == REDACTED, f"{forma!r} deixou de ser redigido"
        assert redact(f"antes {forma} depois") == f"antes {REDACTED} depois"

    # Vários blocos no mesmo texto: um marcador por bloco, e o texto entre eles preservado.
    varios = f"a {BLOCO} b {BLOCO} c"
    assert redact(varios) == f"a {REDACTED} b {REDACTED} c"

    # Uma abertura **depois** de um bloco fechado não contamina o que veio antes.
    assert redact(f"{BLOCO}\n-----BEGIN PRIVATE KEY-----\n") == (
        f"{REDACTED}\n-----BEGIN PRIVATE KEY-----\n"
    )


def test_e6_aud6_001_spans_do_bloco_pem_inalterados() -> None:
    """`recognition_span` e `replacement_span` do `pem_block` continuam idênticos.

    O `pem_block` não tem prefixo preservado nem lookaround, então os dois intervalos
    coincidem com o match — e é exatamente essa igualdade que uma varredura reescrita
    poderia quebrar sem que `redact()` mudasse um byte.
    """
    texto = f"prefixo {BLOCO} sufixo"
    (span,) = [s for s in detect_secret_spans(texto) if s.pattern_name == "pem_block"]

    esperado = PEM.search(texto)
    assert esperado is not None
    assert (span.replacement_span.start, span.replacement_span.end) == esperado.span()
    assert (span.recognition_span.start, span.recognition_span.end) == esperado.span()


#: Peças com todas as formas de traço/BEGIN/END que podem se sobrepor entre si. As três
#: últimas são os casos em que abertura e terminador **compartilham traços**, que é onde
#: uma varredura por pares ingênua escolhe outro par que o `finditer`.
_PECAS_PEM: tuple[str, ...] = (
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
    "-----BEGIN PRIVATE KEY----------END PRIVATE KEY-----",
    "-----BEGIN PRIVATE KEY-----END PRIVATE KEY-----",
    "-----END PRIVATE KEY-----BEGIN PRIVATE KEY-----",
    "texto",
)


def test_e6_aud6_001_a_varredura_linear_acha_os_mesmos_matches() -> None:
    """Diferencial exaustivo: `_scan_pem_block` ≡ `PEM.finditer`, match a match.

    Não "redige o mesmo": **os mesmos objetos de match**, com os mesmos limites e o mesmo
    texto capturado. É a afirmação forte, e é a única que garante que
    `recognition_span`/`replacement_span` não podem ter mudado.

    O corpus cobre de propósito as formas em que abertura e terminador compartilham traços
    (`…PRIVATE KEY----------END…`), onde uma varredura por pares pode escolher outro par.
    """
    casos = set(_PECAS_PEM)
    casos |= {a + b for a, b in itertools.product(_PECAS_PEM, repeat=2)}
    casos |= {a + b + c for a, b, c in itertools.product(_PECAS_PEM, repeat=3)}
    casos |= {a + b + c + d for a, b, c, d in itertools.product(_PECAS_PEM[:12], repeat=4)}

    divergentes = []
    for caso in casos:
        esperado = [(m.start(), m.end(), m.group(0)) for m in PEM.finditer(caso)]
        obtido = [(m.start(), m.end(), m.group(0)) for m in _scan_pem_block(PEM, caso)]
        if esperado != obtido:
            divergentes.append((caso, esperado, obtido))

    assert len(casos) > 20_000, "o corpus do diferencial encolheu"
    assert not divergentes, (
        f"{len(divergentes)} casos divergem do `finditer` canônico; primeiro: {divergentes[0][0]!r}"
    )


def _entradas_geradas(quantidade: int) -> Iterator[str]:
    """Entradas pseudoaleatórias, derivadas de SHA-256 em vez de `random`.

    Determinismo total e independente da versão do Python: o mesmo `quantidade` produz
    exatamente o mesmo corpus em qualquer máquina, hoje e daqui a dois anos. `random` não
    promete isso entre versões, e num repositório onde `rendered_context_hash` é contrato
    ([02] §5) um gerador que possa mudar de saída é um gerador errado.

    O alfabeto é restrito aos caracteres que a expressão lê, para que o gerador passe a
    maior parte do tempo perto da fronteira em vez de sorteando texto que nunca casa.
    """
    alfabeto = "-BEGINDPRVATEKY \n"
    for indice in range(quantidade):
        fluxo = hashlib.sha256(f"e6-aud6-001:{indice}".encode()).digest()
        comprimento = 1 + fluxo[0] % 70
        yield "".join(
            alfabeto[fluxo[1 + posicao % 31] % len(alfabeto)] for posicao in range(comprimento)
        )


def test_e6_aud6_001_fuzz_diferencial() -> None:
    """O mesmo diferencial, com entradas geradas — inclusive as que ninguém escreveria."""
    divergentes = [
        caso
        for caso in _entradas_geradas(20_000)
        if [(m.start(), m.end()) for m in PEM.finditer(caso)]
        != [(m.start(), m.end()) for m in _scan_pem_block(PEM, caso)]
    ]

    assert not divergentes, (
        f"{len(divergentes)} entradas geradas divergem do `finditer`; primeira: {divergentes[0]!r}"
    )


# ================================= E6-AUD6-002 — GATE 1 e o rótulo preservado por posição

#: A entrada exata do relatório. Historicamente insegura (a cascata deixava `sk-123`
#: legível depois de `password: `), portanto num caso em que o GATE 1 **autoriza** que os
#: bytes divirjam — e é essa autorização que o mutante usava de escudo.
ENTRADA_AUD6_002 = "://Bearer password: sk-123"

#: O mutante do Codex: um `redact` que devolve um único span cobrindo o texto inteiro.
MASCARA_MUTANTE = [True] * len(ENTRADA_AUD6_002)


def _mascara_do_historico() -> list[bool]:
    """A máscara que a cascata histórica produz para a entrada da rodada 6."""
    return _mascara_de_um_texto_redigido(ENTRADA_AUD6_002, _redact_historico(ENTRADA_AUD6_002))


def test_e6_aud6_002_as_tres_clausulas_antigas_aceitam_o_mutante() -> None:
    """O controle negativo, na forma que importa: o gate **antigo** deixava passar.

    Um teste novo que falha contra um mutante só prova alguma coisa se o antigo o aceitava.
    As três cláusulas, aplicadas ao mutante neste caso:

    1. monotonicidade — esconder tudo esconde, em particular, tudo que a cascata escondia;
    2. emissão segura — não sobra nada para o oráculo reconhecer;
    3. divergência autorizada — o caso já está no conjunto histórico inseguro, porque a
       cascata deixava `sk-123` legível depois de `password: `.
    """
    assert _redact_historico(ENTRADA_AUD6_002) == "://Bearer «redigido»: sk-123"

    historica = _mascara_historica(ENTRADA_AUD6_002)
    assert all(
        not antes or agora for antes, agora in zip(historica, MASCARA_MUTANTE, strict=True)
    ), "cláusula (1) deveria aceitar o mutante"

    assert not _oraculo_sobre(ENTRADA_AUD6_002, MASCARA_MUTANTE), (
        "cláusula (2) deveria aceitar o mutante"
    )
    assert _oraculo_sobre(ENTRADA_AUD6_002, _mascara_do_historico()), (
        "cláusula (3): o caso precisa estar no conjunto autorizado a divergir"
    )


def test_e6_aud6_002_a_quarta_clausula_rejeita_o_mutante() -> None:
    """A reprodução exata: o span `[0, len)` apaga `Bearer `, e agora o gate acusa.

    As posições protegidas são calculadas do catálogo literal, sem consultar o motor nem a
    máscara — ver `_rotulos_protegidos`. Um mutante não consegue "justificar" o que apagou
    escondendo mais.
    """
    protegidos = _rotulos_protegidos(ENTRADA_AUD6_002)

    assert protegidos, "o caso precisa ter rótulo protegido, senão não reproduz nada"
    assert "".join(ENTRADA_AUD6_002[p] for p in sorted(protegidos)) == "Bearer : "

    apagados = sorted(p for p in protegidos if MASCARA_MUTANTE[p])
    assert apagados == [3, 4, 5, 6, 7, 8, 9, 18, 19], (
        "a quarta cláusula tem de apontar as posições exatas do rótulo apagado"
    )


def test_e6_aud6_002_o_gate_1_inteiro_reprova_o_mutante(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reprodução ponta a ponta: o **GATE 1 de verdade**, rodado contra o mutante.

    Os testes acima medem as cláusulas uma a uma. Este roda o gate inteiro — as quatro
    cláusulas, o corpus de 5.564 casos — com um `redact` que devolve um único span
    `[0, len)` **só** para a entrada do relatório, e confere que ele reprova.

    O mutante é cirúrgico de propósito. Um que redigisse tudo em todo caso seria pego pela
    cláusula (3), e o finding não é sobre isso: é sobre um caso já autorizado a divergir,
    onde as três cláusulas antigas não tinham nada a dizer sobre o que aconteceu **dentro**
    dele. A mensagem casada confirma que quem reprova é a cláusula (4), e não outra.
    """
    from app.safety.redaction import Interval, SecretSpan
    from tests import test_context_redaction_e5_round5 as gate

    # Os originais vêm do módulo de origem, não de `gate`: são o mesmo objeto (o gate os
    # importa de lá), e ler pelo módulo que os define dispensa reexportação.
    spans_reais = detect_secret_spans
    redact_real = redact
    total = Interval(start=0, end=len(ENTRADA_AUD6_002))

    def spans_mutantes(texto: str) -> tuple[SecretSpan, ...]:
        if texto != ENTRADA_AUD6_002:
            return spans_reais(texto)
        return (SecretSpan(recognition_span=total, replacement_span=total, pattern_name="mutante"),)

    def redact_mutante(texto: str) -> str:
        return REDACTED if texto == ENTRADA_AUD6_002 else redact_real(texto)

    monkeypatch.setattr(gate, "detect_secret_spans", spans_mutantes)
    monkeypatch.setattr(gate, "redact", redact_mutante)

    with pytest.raises(AssertionError, match="esconderam um rótulo"):
        gate.test_gate_1_redact_identico_a_cascata_historica()


def test_e6_aud6_002_o_motor_real_preserva_o_rotulo_nas_mesmas_posicoes() -> None:
    """O controle positivo: o motor de verdade passa a cláusula, e preserva `Bearer `."""
    emitida = _mascara_da_emissao(ENTRADA_AUD6_002)

    assert not [p for p in _rotulos_protegidos(ENTRADA_AUD6_002) if emitida[p]]
    assert redact(ENTRADA_AUD6_002) == "://Bearer «redigido»: «redigido»"


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("Bearer password: password: sk-123", "Bearer «redigido»: «redigido» «redigido»"),
        (
            "password: password: password: sk-123",
            "password: «redigido» «redigido» «redigido»",
        ),
        (
            "password: sk-123Bearer password: sk-123",
            "password: «redigido» «redigido»: «redigido»",
        ),
    ],
)
def test_e6_aud6_002_rotulo_que_tambem_e_carga_nao_vira_falso_positivo(
    entrada: str, esperado: str
) -> None:
    """Um rótulo que o catálogo também reconhece como **valor** pode ser escondido.

    Em `password: password: sk-123` o valor atribuído à primeira chave é literalmente o
    texto `password:`. Escondê-lo é o catálogo funcionando, não over-redaction — e uma
    quarta cláusula ingênua (`todo rótulo sempre visível`) reprovaria o motor **correto**
    em 11 casos do corpus. Por isso `_rotulos_protegidos` devolve `rótulo − carga`.
    """
    assert redact(entrada) == esperado
    emitida = _mascara_da_emissao(entrada)
    assert not [p for p in _rotulos_protegidos(entrada) if emitida[p]]


def test_e6_aud6_002_a_clausula_nao_consulta_o_motor() -> None:
    """`_rotulos_protegidos` depende só do catálogo literal do gate.

    Se ela lesse o motor — ou a máscara emitida — um mutante que esconde tudo passaria a
    produzir carga suficiente para justificar ter apagado o rótulo. É o mesmo buraco que
    E6-AUD5-003 fechou, e ele não pode reabrir por dentro da correção dele.

    A verificação é por AST, não por texto: a docstring da função **cita** os nomes
    proibidos para explicar por que não os usa, e uma varredura por substring confundiria
    a explicação com o uso.
    """
    fonte = Path(_rotulos_protegidos.__code__.co_filename).read_text(encoding="utf-8")
    (funcao,) = [
        node
        for node in ast.walk(ast.parse(fonte))
        if isinstance(node, ast.FunctionDef) and node.name == "_rotulos_protegidos"
    ]
    usados = {
        node.id
        for node in ast.walk(ast.Module(body=funcao.body, type_ignores=[]))
        if isinstance(node, ast.Name)
    } | {
        node.attr
        for node in ast.walk(ast.Module(body=funcao.body, type_ignores=[]))
        if isinstance(node, ast.Attribute)
    }

    proibidos = {
        "detect_secret_spans",
        "redact",
        "merge_spans",
        "_mascara_da_emissao",
        "_mascara_historica",
        "_redact_historico",
    }
    assert not (usados & proibidos), (
        f"`_rotulos_protegidos` passou a consultar {sorted(usados & proibidos)} — a "
        "cláusula (4) deixaria de ser independente do motor"
    )
    assert "_JANELAS" in usados, "a cláusula precisa sair do catálogo literal do gate"


def test_e6_aud6_002_as_janelas_de_prefixo_sao_as_do_catalogo() -> None:
    """Os padrões de prefixo preservado do gate são os mesmos do catálogo real.

    A cláusula (4) só protege rótulo de padrão `prefix_preserving`. Se o catálogo ganhar um
    terceiro e o gate não souber, a proteção nasce cega — exatamente o defeito de forma que
    `test_a_tabela_cobre_o_catalogo_inteiro` fecha para a tabela de intervalos.
    """
    do_gate = {fonte for fonte, prefixo, _flags in _CASCATA_HISTORICA if prefixo}
    do_catalogo = {spec.pattern.pattern for spec in _PATTERNS if spec.prefix_preserving}

    assert len(do_gate) == len(do_catalogo) == 2
    assert {re.sub(r"\s+", "", f) for f in do_gate} == {re.sub(r"\s+", "", f) for f in do_catalogo}
