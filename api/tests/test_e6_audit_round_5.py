"""As reproduções exatas da **quinta** auditoria independente da E6, como regressão.

* **E6-AUD5-001** — o *tempering* da rodada 4 **encolhia** a cobertura histórica: um primeiro
  token curto demais para o mínimo deixava de casar diante do vizinho, e `redact(s) == s`.
* **E6-AUD5-002** — concatenações de três famílias continuavam entregando tokens completos.
* **E6-AUD5-003** — o GATE 1 autorizava divergência por uma condição sobre a **entrada**, e
  por isso aceitava uma emissão crua. (O gate reescrito vive em
  `test_context_redaction_e5_round5.py`; aqui fica a prova de que ele reprova o mutante.)
* **E6-AUD5-004** — `expected_content_hash` não cobria `tags` nem `source_refs`, e dois
  editores sobrescreviam um ao outro recebendo `200` os dois.
* **E6-AUD5-005** — uma terceira subclasse de `Response`, construída direto na rota, passava
  por toda a suíte de arquitetura.
* **E6-AUD5-006** — o detector mudou e `renderer_version` continuou `e5.block.v6`.
* **E6-AUD5-007** — erro de forma no `edit-view` levava o conteúdo autoral cru para o log do
  servidor dentro do `input_value` de uma `ValidationError`.

O **gate de adjacência** gerado sobre o catálogo inteiro está em
`test_matriz_de_adjacencia_do_catalogo`: ele não testa casos escolhidos a dedo, gera a
matriz de pares ordenados de todas as famílias em quatro comprimentos cada.
"""

from __future__ import annotations

import ast
import itertools
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.context_engine import create_entry
from app.context_engine.rendering import RENDERER_VERSION
from app.context_engine.service import edit_hash_of
from app.db.enums import ContextDomain
from app.db.models import ContextRegistryEntry, DevWorkspace
from app.safety.redaction import (
    MAX_REDACTION_PASSES,
    REDACTED,
    UNCONVERGED,
    detect_secret_spans,
    merge_spans,
    redact,
)
from tests import context_helpers
from tests import test_architecture as arch

# ====================================================== o gate de adjacência (PARTE 1)

#: Uma amostra por família do catálogo, em quatro comprimentos: um abaixo do mínimo (não
#: deve casar sozinho), no mínimo, um acima, e longo. Os comprimentos mínimos vêm das
#: próprias expressões — é onde E6-AUD5-001 morava.
FAMILIAS: dict[str, dict[str, str]] = {
    "anthropic_key": {
        "abaixo": "sk-ant-" + "A" * 7,
        "minimo": "sk-ant-" + "A" * 8,
        "acima": "sk-ant-" + "A" * 9,
        "longo": "sk-ant-" + "A" * 40,
    },
    "openai_key": {
        "abaixo": "sk-" + "A" * 15,
        "minimo": "sk-" + "A" * 16,
        "acima": "sk-" + "A" * 17,
        "longo": "sk-" + "A" * 40,
    },
    "github_token": {
        "abaixo": "ghp_" + "A" * 15,
        "minimo": "ghp_" + "A" * 16,
        "acima": "ghp_" + "A" * 17,
        "longo": "ghp_" + "A" * 36,
    },
    "github_pat": {
        "abaixo": "github_pat_" + "A" * 19,
        "minimo": "github_pat_" + "A" * 20,
        "acima": "github_pat_" + "A" * 21,
        "longo": "github_pat_" + "A" * 40,
    },
    "aws_access_key": {
        "abaixo": "AKIA" + "0" * 15,
        "minimo": "AKIA" + "0123456789ABCDEF",
        "acima": "AKIA" + "0123456789ABCDEFG",
        "longo": "AKIA" + "0123456789ABCDEF",
    },
    "bearer": {
        "abaixo": "Bearer " + "A" * 7,
        "minimo": "Bearer " + "A" * 8,
        "acima": "Bearer " + "A" * 9,
        "longo": "Bearer " + "A" * 40,
    },
    "assigned_secret": {
        "abaixo": "token=" + "A" * 5,
        "minimo": "token=" + "A" * 6,
        "acima": "token=" + "A" * 7,
        "longo": "token=" + "A" * 40,
    },
    "url_credentials": {
        "abaixo": "https://u:@h",
        "minimo": "https://u:p@h",
        "acima": "https://us:pw@h",
        "longo": "https://user:" + "p" * 30 + "@host.test",
    },
}

COMPRIMENTOS = ("abaixo", "minimo", "acima", "longo")

#: O catálogo histórico, escrito aqui **literal**, com as duas fronteiras de palavra
#: recortadas. É o oráculo independente: "se o texto a partir daqui fosse o começo de tudo,
#: o catálogo reconheceria um segredo?". Não importa nada de `safety/redaction.py` — se o
#: motor e o oráculo compartilhassem código, o gate mediria o motor contra si mesmo.
_JANELAS_DO_CATALOGO: tuple[tuple[re.Pattern[str], bool], ...] = (
    (
        re.compile(
            r"-----BEGIN[A-Z ]*PRIVATE KEY-----.*?-----END[A-Z ]*PRIVATE KEY-----", re.DOTALL
        ),
        False,
    ),
    (re.compile(r"(?<=://)[^/\s:@]+:[^/\s:@]+(?=@)"), False),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]{8,}"), True),
    (re.compile(r"sk-ant-[A-Za-z0-9._\-]{8,}"), False),
    (re.compile(r"sk-[A-Za-z0-9]{16,}"), False),
    (re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})"), False),
    (re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}"), False),
    (
        re.compile(
            r"(?i)((?:api[_-]?key|secret|token|password|passwd|authorization)"
            r"\s*[:=]\s*)(?:\"|')?([^\s\"',;]{6,})"
        ),
        True,
    ),
)

_CARACTERE_DE_PALAVRA = re.compile(r"[A-Za-z0-9_]")


def mascara_da_emissao(entrada: str) -> list[bool]:
    """Que caracteres da entrada a redação esconde — e **prova** que foi isso que saiu.

    Duas metades, e é o par que dá a garantia:

    1. a máscara vem das regiões que o motor devolve;
    2. `redact` é confrontado com o texto reconstruído **a partir dessas regiões**. Se a
       emissão não for exatamente o que as regiões dizem, o ensaio para aqui.

    Reconstruir a máscara só a partir dos bytes emitidos seria ambíguo: um fragmento
    preservado pode aparecer mais de uma vez na entrada (`ghp_AAA…ghp_AAA…` tem `_AAA…` em
    duas posições), e o alinhamento da esquerda para a direita escolheria a errada. A
    verificação (2) fecha a mesma porta que a reconstrução fecharia — um `redact` que
    devolvesse a entrada crua quebra nela — sem herdar a ambiguidade.
    """
    regioes = merge_spans(detect_secret_spans(entrada))

    partes: list[str] = []
    cursor = 0
    for inicio, fim in regioes:
        partes.append(entrada[cursor:inicio])
        partes.append(REDACTED)
        cursor = fim
    partes.append(entrada[cursor:])
    reconstruido = "".join(partes)

    emitido = redact(entrada)
    assert emitido == reconstruido, (
        f"a emissão não corresponde às regiões detectadas para {entrada[:60]!r}: "
        f"{emitido[:80]!r} != {reconstruido[:80]!r}"
    )

    marcado = [False] * len(entrada)
    for inicio, fim in regioes:
        for indice in range(inicio, fim):
            marcado[indice] = True
    return marcado


def oraculo_de_adjacencia(entrada: str) -> list[tuple[int, int, int]]:
    """O oráculo independente do prompt, sobre o que de fato saiu.

    Para toda âncora `i` — começo de uma corrida de caracteres de palavra, ou posição
    escondida / imediatamente depois de uma —, se o catálogo reconhece um segredo tratando o
    sufixo a partir de `i` como string nova, a região correspondente tem de estar
    **inteiramente** escondida.

    Âncora é adjacência **real de segredo a segredo**, nunca vizinhança genérica: uma
    posição no meio de uma palavra comum não é âncora, e é isso que preserva
    `tokenizerghp_…`.

    As expressões vêm da cópia literal do catálogo neste módulo, não de
    `safety/redaction.py`: se o motor e o oráculo compartilhassem código, o gate mediria o
    motor contra si mesmo.
    """
    marcado = mascara_da_emissao(entrada)

    ancoras = {
        i
        for i in range(len(entrada))
        if _CARACTERE_DE_PALAVRA.match(entrada[i])
        and (i == 0 or not _CARACTERE_DE_PALAVRA.match(entrada[i - 1]))
    }
    ancoras.update(i for i, escondido in enumerate(marcado) if escondido)
    ancoras.update(i + 1 for i, escondido in enumerate(marcado) if escondido)

    faltando: list[tuple[int, int, int]] = []
    for ancora in sorted(a for a in ancoras if a <= len(entrada)):
        for janela, prefixo in _JANELAS_DO_CATALOGO:
            encontrado = janela.match(entrada, ancora)
            if encontrado is None:
                continue
            inicio = encontrado.end(1) if prefixo else encontrado.start()
            if encontrado.end() > inicio and not all(
                marcado[indice] for indice in range(inicio, encontrado.end())
            ):
                faltando.append((ancora, inicio, encontrado.end()))
    return faltando


def test_matriz_de_adjacencia_do_catalogo() -> None:
    """Todo par ordenado do catálogo, incluindo A=B, nos dois sentidos, em quatro
    comprimentos cada — contra o oráculo independente.

    Não são casos escolhidos a dedo: a matriz é gerada. Foi exatamente a cobertura finita e
    escolhida do corpus anterior que deixou E6-AUD5-001 passar — o caso dele (payload abaixo
    do mínimo antes do vizinho) não estava lá.
    """
    nomes = sorted(FAMILIAS)
    casos = {
        f"{a}[{ca}]+{b}[{cb}]": FAMILIAS[a][ca] + FAMILIAS[b][cb]
        for a, b in itertools.product(nomes, repeat=2)
        for ca, cb in itertools.product(COMPRIMENTOS, repeat=2)
    }

    falhas = {
        rotulo: oraculo_de_adjacencia(texto)
        for rotulo, texto in casos.items()
        if oraculo_de_adjacencia(texto)
    }

    assert not falhas, (
        f"{len(falhas)} de {len(casos)} combinações deixaram segredo reconhecível exposto; "
        f"primeiras: {sorted(falhas)[:5]}"
    )
    assert len(casos) == len(nomes) ** 2 * len(COMPRIMENTOS) ** 2 == 1024


def test_o_caso_do_pedro_nao_deixa_caractere_cru() -> None:
    """`ghp_` + 16 A + `ghp_` + 16 B: **nenhum** caractere de nenhum dos dois tokens sobra.

    Inclusive o `ghp` que o primeiro match consome do prefixo do segundo — era exatamente
    esse trecho que fazia o fecho da rodada 4 começar no lugar errado.
    """
    primeiro = "ghp_" + "A" * 16
    segundo = "ghp_" + "B" * 16
    texto = primeiro + segundo

    marcado = mascara_da_emissao(texto)

    assert all(marcado), f"sobrou caractere cru: {redact(texto)!r}"
    assert not oraculo_de_adjacencia(texto)


@pytest.mark.parametrize(
    "texto",
    [
        pytest.param("ghp_" + "A" * 13 + "ghp_" + "B" * 24, id="ghp-A13"),
        pytest.param("ghp_" + "A" * 15 + "ghp_" + "B" * 24, id="ghp-A15"),
        pytest.param("sk-" + "A" * 14 + "sk-" + "B" * 24, id="sk-A14"),
        pytest.param("sk-" + "A" * 15 + "sk-" + "B" * 24, id="sk-A15"),
    ],
)
def test_aud5_001_a_cobertura_historica_nao_encolhe(texto: str) -> None:
    """E6-AUD5-001: `redact(s) == s` — o *tempering* apagava o primeiro match.

    A expressão histórica reconhece `ghp_` seguido de 16 caracteres, e os 16 incluíam as
    letras `ghp` do vizinho. O *tempering* interrompia o token antes de ele satisfazer o
    mínimo: o primeiro match sumia, o segundo não tinha fronteira, e **nada** era redigido.
    """
    saida = redact(texto)

    assert saida != texto, "nenhum byte foi escondido — a regressão de AUD5-001 voltou"
    assert not oraculo_de_adjacencia(texto)


G = "ghp_FifthAuditSynthetic0123456789AB"
S = "sk-FifthAuditSynthetic0123456789AB"
A = "AKIA0123456789ABCDEF"


@pytest.mark.parametrize(
    "texto",
    [
        pytest.param((G + S + A) * 2, id="GSA-x2"),
        pytest.param((G + S + A) * 3, id="GSA-x3"),
        pytest.param((S + A + G) * 2, id="SAG-x2"),
        pytest.param((S + A + G) * 3, id="SAG-x3"),
    ],
)
def test_aud5_002_misturas_de_tres_familias(texto: str) -> None:
    """E6-AUD5-002: permutações heterogêneas deixavam tokens completos na saída."""
    saida = redact(texto)

    for token in (G, S, A):
        assert token not in saida, f"{token[:8]}… sobreviveu inteiro: {saida[:80]!r}"
    assert not oraculo_de_adjacencia(texto)


def test_o_limite_pedido_continua_valendo() -> None:
    """`tokenizerghp_…` continua **não** reconhecido: o relaxamento é só por adjacência."""
    texto = "tokenizer" + G
    assert redact(texto) == texto


def test_a_borda_de_janela_alarga_a_cobertura_da_aws_e_so_dela() -> None:
    """A consequência semântica de tratar a borda da janela como fronteira, dita em voz alta.

    Só `aws_access_key` tem fronteira de palavra à **direita**, e recortá-la na âncora é o
    que permite reconhecer duas *access keys* coladas. O preço é reconhecer também uma chave
    seguida de minúscula (`AKIA…x`), que a expressão canônica recusava — over-redaction, na
    direção segura, num formato que já é distintivo.

    O limite continua exato do outro lado: uma chave colada **depois** de palavra comum não
    é reconhecida, porque ali não há âncora nenhuma.
    """
    chave = "AKIAIOSFODNN7EXAMPLE"

    assert redact(chave) == REDACTED
    assert redact(chave + " fim") == f"{REDACTED} fim"
    # Novo nesta rodada: a borda de janela alcança o sufixo minúsculo.
    assert redact(chave + "x") == f"{REDACTED}x"
    # Inalterado: sem âncora, sem reconhecimento.
    assert redact("palavra" + chave) == "palavra" + chave
    # Inalterado: curto demais continua sendo texto comum.
    assert redact("AKIA123 curto") == "AKIA123 curto"


@pytest.mark.parametrize("repeticoes", [2, 3, 5, 9, 17])
def test_o_motor_converge_e_e_idempotente(repeticoes: int) -> None:
    """O ponto fixo não oscila, e redigir duas vezes dá o mesmo resultado."""
    texto = (G + S + A) * repeticoes
    uma_vez = redact(texto)

    assert redact(uma_vez) == uma_vez
    assert redact(texto) == uma_vez


def test_a_corrida_de_palavra_usa_a_mesma_definicao_que_a_fronteira() -> None:
    r"""Âncora de corrida e `\b` têm de concordar sobre o que é caractere de palavra.

    Encontrado em self-review desta rodada: com `[A-Za-z0-9_]`, `caféghp_…` virava início de
    corrida (o `é` não casava o alfabeto ASCII) e era redigido, enquanto `tokenizerghp_…`
    não era — o mesmo caso decidido de duas formas por uma discrepância de alfabeto. O
    `\b` do Python é Unicode-aware; a âncora passou a usar `\w`.
    """
    for prefixo in ("tokenizer", "café", "привет", "日本語", "ABCdef"):
        colado = prefixo + G
        assert redact(colado) == colado, f"{prefixo!r} virou âncora e não devia"

    # E o que é adjacência de verdade continua sendo reconhecido.
    assert redact(G + G) != G + G


def test_o_motor_nao_devolve_span_duplicado() -> None:
    """O laço reencontra a mesma âncora a cada volta — o span não pode sair repetido.

    Encontrado em self-review desta rodada, antes da suíte: `transformations` participa do
    payload hasheado do artifact ([02] §5), e um span repetido chegaria duas vezes a quem
    conta transformações. Determinístico não é o mesmo que correto.
    """
    for texto in (G, G * 3, S * 2 + A * 2, "texto " + G + " fim"):
        spans = detect_secret_spans(texto)
        assert len(spans) == len(set(spans)), f"span duplicado em {texto[:40]!r}"


def test_nao_convergir_redige_o_campo_inteiro() -> None:
    """Teto atingido **com progresso** ⇒ o campo inteiro, nunca um resultado parcial.

    O teto é baixado para 1 no teste: com um único passe permitido, um texto que precisa de
    dois não converge. A alternativa — devolver o que deu tempo de cobrir — entregaria uma
    redação parcial silenciosa, que é o modo de falha que [ADR-0009] proíbe.
    """
    texto = (G + S + A) * 3
    assert redact(texto) == REDACTED  # com o teto normal, converge e cobre tudo

    import app.safety.redaction as modulo

    original = modulo.MAX_REDACTION_PASSES
    try:
        modulo.MAX_REDACTION_PASSES = 1
        spans = detect_secret_spans(texto)
        assert len(spans) == 1
        assert spans[0].pattern_name == UNCONVERGED
        assert spans[0].replacement_span.start == 0
        assert spans[0].replacement_span.end == len(texto)
        assert redact(texto) == REDACTED
    finally:
        modulo.MAX_REDACTION_PASSES = original

    assert MAX_REDACTION_PASSES >= 2, "o teto precisa permitir ao menos um fecho"


def test_aud5_003_o_gate_reprova_a_emissao_crua(monkeypatch: pytest.MonkeyPatch) -> None:
    """E6-AUD5-003: o GATE 1 tem de reprovar um `redact` que emita a entrada crua.

    A mutação do auditor: devolver a entrada intacta para `('ghp_' + 'B' * 20) * 2` e
    delegar ao original em todo o resto. O gate antigo passava, porque a entrada continha
    adjacência e a divergência ficava autorizada por uma condição sobre a **entrada**.
    """
    import tests.test_context_redaction_e5_round5 as gate

    alvo = ("ghp_" + "B" * 20) * 2
    original = redact

    def mutante(texto: str) -> str:
        return texto if texto == alvo else original(texto)

    monkeypatch.setattr(gate, "redact", mutante)
    with pytest.raises(AssertionError):
        gate.test_gate_1_redact_identico_a_cascata_historica()


# ============================================================ E6-AUD5-004 — edit_hash


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    context_helpers.init_repo(root)
    context_helpers.write(root, "README.md", "# projeto\n")
    context_helpers.write(root, "src/app.py", "valor = 1\n")
    context_helpers.commit_all(root, "inicial")
    return root


@pytest.fixture
def workspace_id(auth_api_client: TestClient, repo: Path) -> str:
    resposta = auth_api_client.post(
        "/api/workspaces",
        json={"name": "ws", "type": "personal", "local_path": str(repo)},
    )
    assert resposta.status_code == 201, resposta.text
    return str(resposta.json()["id"])


@pytest.fixture
def entrada(auth_api_client: TestClient, workspace_id: str) -> dict[str, Any]:
    resposta = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context",
        json={
            "domain": "objective",
            "title": "Objetivo",
            "body": "corpo autoral",
            "tags": ["inicial"],
            "source_refs": ["README.md"],
        },
    )
    assert resposta.status_code == 201, resposta.text
    return dict(resposta.json())


def _edit_view(client: TestClient, entry_id: str) -> dict[str, Any]:
    resposta = client.post(f"/api/context/{entry_id}/edit-view")
    assert resposta.status_code == 200, resposta.text
    return dict(resposta.json())


@pytest.mark.parametrize(
    ("campo", "primeiro", "segundo"),
    [
        pytest.param("tags", ["first"], ["second"], id="tags"),
        pytest.param("source_refs", ["src/app.py"], ["README.md"], id="source_refs"),
    ],
)
def test_aud5_004_dois_editores_nao_se_sobrescrevem(
    auth_api_client: TestClient,
    entrada: dict[str, Any],
    campo: str,
    primeiro: list[str],
    segundo: list[str],
) -> None:
    """A reprodução exata: dois clientes leem o mesmo estado, os dois escrevem metadados.

    Com `expected_content_hash` os dois recebiam `200` e o segundo apagava o primeiro — o
    `content_hash` cobre `domain`/`title`/`body`/`structured` e **não** cobre estes campos.
    """
    lido = _edit_view(auth_api_client, entrada["id"])
    hash_compartilhado = lido["edit_hash"]

    cliente_1 = auth_api_client.patch(
        f"/api/context/{entrada['id']}",
        json={"expected_edit_hash": hash_compartilhado, campo: primeiro},
    )
    assert cliente_1.status_code == 200, cliente_1.text
    assert cliente_1.json()[campo] == primeiro

    cliente_2 = auth_api_client.patch(
        f"/api/context/{entrada['id']}",
        json={"expected_edit_hash": hash_compartilhado, campo: segundo},
    )

    assert cliente_2.status_code == 409, cliente_2.text
    assert cliente_2.json()["code"] == "context_changed"

    atual = auth_api_client.get(f"/api/workspaces/{entrada['workspace_id']}/context").json()[0]
    assert atual[campo] == primeiro, "a escrita do primeiro editor foi apagada"


def test_o_edit_hash_muda_com_conteudo_tags_e_source_refs(
    session: Session, workspace: DevWorkspace
) -> None:
    """As três dimensões que ele cobre, uma a uma, no nível do domínio."""
    entry = create_entry(
        session,
        workspace,
        domain=ContextDomain.MODULES,
        title="T",
        body="B\n",
        tags=["a"],
        source_refs=[],
    )
    base = edit_hash_of(entry)

    entry.tags = ["b"]
    assert edit_hash_of(entry) != base

    entry.tags = ["a"]
    assert edit_hash_of(entry) == base

    entry.source_refs = ["README.md"]
    assert edit_hash_of(entry) != base

    entry.source_refs = []
    entry.content_hash = "0" * 64
    assert edit_hash_of(entry) != base


def test_o_edit_hash_distingue_nfc_de_nfd(session: Session, workspace: DevWorkspace) -> None:
    """Identidade de caminho preservada: NFC e NFD são arquivos diferentes ([02] §7)."""
    import unicodedata

    entry = create_entry(
        session,
        workspace,
        domain=ContextDomain.MODULES,
        title="T",
        body="B\n",
        source_refs=[],
    )

    entry.source_refs = [unicodedata.normalize("NFC", "café.md")]
    em_nfc = edit_hash_of(entry)
    entry.source_refs = [unicodedata.normalize("NFD", "café.md")]
    em_nfd = edit_hash_of(entry)

    assert em_nfc != em_nfd, "canonical_json colapsou dois caminhos genuinamente diferentes"


def test_o_edit_hash_nao_virou_coluna() -> None:
    """Derivado, nunca persistido — um cache defasado produziria 409 fantasma."""
    assert not hasattr(ContextRegistryEntry, "edit_hash")


# ======================================================== E6-AUD5-005 — mutante de Response


def test_aud5_005_o_mutante_da_terceira_classe_quebra_a_suite(tmp_path: Path) -> None:
    """A reprodução do Codex: uma terceira subclasse de `JSONResponse`, construída direto
    na rota, **sem** `response_class=` no decorador.

    O mutante é montado numa cópia temporária da árvore — nenhum arquivo do projeto é
    tocado — e as regras de arquitetura são reapontadas para ela. Antes desta rodada, os 143
    testes passavam contra exatamente este mutante.
    """
    copia = tmp_path / "app"
    shutil.copytree(arch.APP_ROOT, copia, ignore=shutil.ignore_patterns("__pycache__"))

    responses = copia / "api" / "responses.py"
    responses.write_text(
        responses.read_text(encoding="utf-8")
        + "\n\nclass ThirdAuditResponse(JSONResponse):\n    pass\n",
        encoding="utf-8",
    )
    health = copia / "api" / "health.py"
    health.write_text(
        health.read_text(encoding="utf-8")
        + "\n\nfrom app.api.responses import ThirdAuditResponse\n\n\n"
        '@router.get("/mutante")\n'
        "def mutante() -> ThirdAuditResponse:\n"
        '    return ThirdAuditResponse(content={"value": "cru"})\n',
        encoding="utf-8",
    )

    arquivos = [
        caminho for caminho in sorted(copia.rglob("*.py")) if "__pycache__" not in caminho.parts
    ]

    original_root, original_files = arch.APP_ROOT, arch.ALL_FILES
    arch.APP_ROOT, arch.ALL_FILES = copia, arquivos
    try:
        with pytest.raises(AssertionError, match="ThirdAuditResponse"):
            arch.test_o_projeto_nao_define_uma_terceira_classe_de_resposta()
        with pytest.raises(AssertionError, match="ThirdAuditResponse"):
            arch.test_nenhuma_classe_de_resposta_e_construida_fora_do_lugar()
    finally:
        arch.APP_ROOT, arch.ALL_FILES = original_root, original_files


def test_a_arvore_real_passa_nas_duas_regras() -> None:
    """Controle: as mesmas regras, contra o código de verdade, não acusam nada."""
    arch.test_o_projeto_nao_define_uma_terceira_classe_de_resposta()
    arch.test_nenhuma_classe_de_resposta_e_construida_fora_do_lugar()


# ============================================================ E6-AUD5-006 — renderer


def test_aud5_006_a_versao_do_renderer_avancou() -> None:
    """O detector mudou o conteúdo redigido, logo o payload hasheado — a versão avança.

    A constante identifica a **semântica** do renderer/detector. Mantê-la faria duas
    revisões com redações diferentes gravarem o mesmo `renderer_version`, e um `manifest`
    antigo pareceria reproduzível contra um detector que não é o dele.
    """
    assert RENDERER_VERSION == "e5.block.v7"


def test_a_constante_do_renderer_documenta_o_bump() -> None:
    """O comentário da constante é normativo: qualquer mudança no payload avança a versão."""
    fonte = (arch.APP_ROOT / "context_engine" / "rendering.py").read_text(encoding="utf-8")
    assert "e5.block.v7" in fonte
    assert "E6-AUD5-006" in fonte


# ============================================================ E6-AUD5-007 — log


def test_aud5_007_documento_malformado_nao_ecoa_no_log(
    auth_api_client: TestClient,
    entrada: dict[str, Any],
    session_factory: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A precondição do auditor: `structured` gravado como lista, válido para a coluna e
    inválido para o modelo.

    Antes, a `ValidationError` subia com `input_value=['ghp_…']` e chegava inteira ao canal
    de exceções do servidor. Agora vira erro **tipado** com mensagem constante, servido pelo
    handler de domínio — que não gera *traceback*.
    """
    from app.db.session import session_scope

    with session_scope(session_factory) as sessao:
        registro = sessao.get(ContextRegistryEntry, entrada["id"])
        assert registro is not None
        registro.structured = [G]  # type: ignore[assignment]

    with caplog.at_level(0):
        resposta = auth_api_client.post(f"/api/context/{entrada['id']}/edit-view")

    assert resposta.status_code == 500, resposta.text
    assert resposta.json()["code"] == "context_entry_unreadable"
    assert G not in resposta.text, "o valor cru saiu na resposta"
    assert G not in caplog.text, "o valor cru foi para o log"
    assert "structured" in resposta.json()["message"], "o erro não diz nem qual campo falhou"


def test_a_rota_de_edit_view_nao_encadeia_a_excecao_original() -> None:
    """`from None`: encadear traria o `input_value` de volta por qualquer formatador
    que imprimisse a cadeia de causas."""
    fonte = (arch.APP_ROOT / "api" / "context.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    rota = next(
        node
        for node in ast.walk(arvore)
        if isinstance(node, ast.FunctionDef) and node.name == "edit_view"
    )
    levantadas = [node for node in ast.walk(rota) if isinstance(node, ast.Raise)]

    assert levantadas, "a rota deixou de tratar o erro de forma"
    for node in levantadas:
        assert isinstance(node.cause, ast.Constant) and node.cause.value is None, (
            "o `raise ... from None` sumiu: a exceção original voltaria ao log com o valor"
        )
