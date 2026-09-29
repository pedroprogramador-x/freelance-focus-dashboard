"""O **inventário exaustivo** de campos livres da terceira auditoria, contra o boundary novo.

A rodada 3 do Codex entregou uma matriz de todo campo de texto livre de todas as respostas
da API, classificando cada um em **D** (redigido por `redact_document`), **S** (redigido por
`redact` escalar), **R** (**cru** — reproduzido vazando) ou **G**/**N** (gerado de conjunto
fixo, ou não exposto).

Este módulo transforma a coluna **R** dessa matriz numa varredura: cada campo que o
relatório observou saindo cru recebe um marcador sintético e a resposta inteira é conferida.
É a diferença entre "corrigi os exemplos citados" e "fechei a classe" — e foi não ter essa
diferença que produziu três rodadas de findings irmãos.

A única exceção declarada é o `purge_token`, marcado **N/A por desenho**: ele é o segredo
de autorização que a prévia de purga existe para entregar ([02] §11), e é protegido pelo
marcador `Unredacted` de [04] §5. Ele tem teste próprio em `test_e6_audit_round_3`.

## O que este módulo **não** afirma

Que nenhum segredo pode atravessar em hipótese nenhuma. Ele afirma que **toda resposta
atravessa o boundary**; o que o boundary reconhece é decisão de `detect_secret_spans`, e a
cobertura do detector é outra pergunta — ver
`test_o_boundary_aplica_o_detector_e_herda_os_limites_dele`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.safety import redact
from tests import context_helpers

#: Credencial **sintética** reconhecida pelo redator canônico. Nenhuma credencial real.
MARCADOR = "sk-" + ("A" * 24)


@pytest.fixture
def repo_com_marcador(tmp_path: Path) -> Path:
    """Repositório git real cujo **nome de branch** carrega o marcador."""
    root = tmp_path / "repo"
    context_helpers.init_repo(root)
    context_helpers.write(root, "README.md", "# projeto\n")
    context_helpers.write(root, "src/app.py", "valor = 1\n")
    context_helpers.commit_all(root, "inicial")
    context_helpers.git(root, "checkout", "-b", MARCADOR)
    return root


@pytest.fixture
def workspace(auth_api_client: TestClient, repo_com_marcador: Path) -> str:
    """Workspace cujos quatro escalares livres carregam o marcador."""
    resposta = auth_api_client.post(
        "/api/workspaces",
        json={
            "name": MARCADOR,
            "type": "personal",
            "local_path": str(repo_com_marcador),
            "linked_project_id": MARCADOR,
            "default_branch": MARCADOR,
        },
    )
    assert resposta.status_code == 201, resposta.text
    return str(resposta.json()["id"])


def _sem_marcador(rotulo: str, resposta: Any) -> None:
    assert resposta.status_code < 500, f"{rotulo} respondeu {resposta.status_code}"
    assert MARCADOR not in resposta.text, f"{rotulo} devolveu o campo cru"


def test_o_marcador_e_reconhecido_pelo_redator() -> None:
    """Premissa da varredura inteira: sem isto, todo teste abaixo passaria à toa."""
    assert redact(MARCADOR) != MARCADOR


# ------------------------------------------------------- DevWorkspace e preflight Git


def test_inventario_dev_workspace_e_preflight(auth_api_client: TestClient, workspace: str) -> None:
    """Linhas **R** do inventário: `name`, `local_path`, `linked_project_id`,
    `default_branch` e `GitPreflightResponse.branch`.

    `local_path` ser um diretório que existe de verdade não o isenta — o relatório é
    explícito: "ser path real não o torna isento de segredo". `linked_project_id` é string
    opaca, não um UUID validado.
    """
    _sem_marcador("GET /workspaces", auth_api_client.get("/api/workspaces"))
    _sem_marcador("GET /workspaces/{id}", auth_api_client.get(f"/api/workspaces/{workspace}"))
    _sem_marcador(
        "GET /workspaces/{id}/git", auth_api_client.get(f"/api/workspaces/{workspace}/git")
    )
    _sem_marcador(
        "PATCH /workspaces/{id}",
        auth_api_client.patch(f"/api/workspaces/{workspace}", json={"status": "archived"}),
    )


# ---------------------------------------------------------------- Context Registry


@pytest.fixture
def entrada(auth_api_client: TestClient, workspace: str) -> str:
    resposta = auth_api_client.post(
        f"/api/workspaces/{workspace}/context",
        json={
            "domain": "objective",
            "title": MARCADOR,
            "body": f"corpo com {MARCADOR}",
            "tags": [MARCADOR],
            "structured": {"campo": MARCADOR, MARCADOR: "segredo na chave"},
            "source_refs": ["src/app.py"],
        },
    )
    assert resposta.status_code == 201, resposta.text
    return str(resposta.json()["id"])


def test_inventario_context_registry(
    auth_api_client: TestClient, workspace: str, entrada: str
) -> None:
    """Linhas **R**: `title`, `body`, `tags`, folhas **e chaves** de `structured`.

    A projeção do registry não tinha redação nenhuma — era a superfície mais larga do
    finding. A chave livre de `structured` é a mesma reprodução de E6-AUD3-003 por uma rota
    normal, sem adulterar o banco.
    """
    _sem_marcador("GET .../context", auth_api_client.get(f"/api/workspaces/{workspace}/context"))
    atual = auth_api_client.get(f"/api/workspaces/{workspace}/context").json()
    hash_atual = next(item for item in atual if item["id"] == entrada)["content_hash"]
    _sem_marcador(
        "PATCH /context/{id}",
        auth_api_client.patch(
            f"/api/context/{entrada}",
            json={"expected_edit_hash": hash_atual, "body": MARCADOR},
        ),
    )
    _sem_marcador(
        "POST .../context/verify",
        auth_api_client.post(f"/api/workspaces/{workspace}/context/verify", json={}),
    )


def test_inventario_import_de_seed(auth_api_client: TestClient, workspace: str) -> None:
    """Linha **R**: os campos livres do seed de `ProjectPlanning` viram entradas autorais.

    `problem`/`objective`, `functional_requirements`, `non_functional_requirements`,
    `stack`, `architecture`, `technical_decisions` e `risks` entram na mesma projeção do
    registry — logo, na mesma varredura.
    """
    resposta = auth_api_client.post(
        f"/api/workspaces/{workspace}/context/import",
        json={
            "problem": f"problema com {MARCADOR}",
            "objective": f"objetivo com {MARCADOR}",
            "functional_requirements": [MARCADOR],
            "non_functional_requirements": [MARCADOR],
            "stack": [MARCADOR],
            "architecture": MARCADOR,
            "technical_decisions": [{"title": MARCADOR, "decision": MARCADOR, "reason": MARCADOR}],
            "risks": [{"description": MARCADOR, "mitigation": MARCADOR}],
        },
    )
    assert resposta.status_code in {200, 201}, resposta.text
    _sem_marcador("POST .../context/import", resposta)
    _sem_marcador(
        "GET .../context (pós-import)",
        auth_api_client.get(f"/api/workspaces/{workspace}/context"),
    )


# ------------------------------------------------------- WorkspaceTask e ContextManifest


def test_inventario_task_e_manifest(auth_api_client: TestClient, workspace: str) -> None:
    """Linhas **R**/**S**: `title`, `goal`, `result_summary`, e `base_branch` do manifest.

    `base_branch` é o caso que o relatório destacou como a prova do modelo quebrado: a
    mesma branch saía **redigida** dentro de `plan` e **crua** no manifest.
    """
    tarefa = auth_api_client.post(
        f"/api/workspaces/{workspace}/tasks",
        json={"title": MARCADOR, "goal": f"objetivo com {MARCADOR}"},
    )
    assert tarefa.status_code == 201, tarefa.text
    _sem_marcador("POST .../tasks", tarefa)

    task_id = tarefa.json()["id"]
    _sem_marcador("GET /tasks/{id}", auth_api_client.get(f"/api/tasks/{task_id}"))
    _sem_marcador(
        "GET .../tasks (lista)", auth_api_client.get(f"/api/workspaces/{workspace}/tasks")
    )

    planejada = auth_api_client.post(
        f"/api/tasks/{task_id}/plan", json={"candidate_paths": ["src/app.py"]}
    )
    assert planejada.status_code == 200, planejada.text
    _sem_marcador("POST /tasks/{id}/plan", planejada)
    _sem_marcador("GET /tasks/{id}/context", auth_api_client.get(f"/api/tasks/{task_id}/context"))

    rejeitada = auth_api_client.post(
        f"/api/tasks/{task_id}/reject", json={"note": f"nota com {MARCADOR}"}
    )
    _sem_marcador("POST /tasks/{id}/reject (result_summary)", rejeitada)
    _sem_marcador("GET /tasks/{id} (pós-nota)", auth_api_client.get(f"/api/tasks/{task_id}"))


def test_inventario_purge_preview_menos_o_token(
    auth_api_client: TestClient, workspace: str
) -> None:
    """A prévia de purga: tudo redigido **exceto** o `purge_token` (N/A por desenho).

    O relatório é explícito sobre a distinção, e ela é o motivo de o escape hatch ser um
    tipo e não uma flag: `purge_token` é "credencial que esta resposta tem a função de
    entregar", não "credencial colada em conteúdo autoral".
    """
    resposta = auth_api_client.get(f"/api/workspaces/{workspace}/purge-preview")
    assert resposta.status_code == 200, resposta.text

    corpo = resposta.json()
    token = corpo.pop("purge_token")
    assert len(token) > 20, "o token foi redigido; a confirmação forte de [02] §11 quebrou"
    assert MARCADOR not in str(corpo), "um campo vizinho ao token saiu cru"


# ----------------------------------------------------------------- respostas de erro


def test_inventario_respostas_de_erro(auth_api_client: TestClient, workspace: str) -> None:
    """Linha **R**: `detail[].input` do `422`. E os erros de domínio, que já eram **S**."""
    _sem_marcador(
        "422 (campo extra)",
        auth_api_client.post(
            f"/api/workspaces/{workspace}/tasks",
            json={"title": "t", "goal": "g", "extra": MARCADOR},
        ),
    )
    _sem_marcador(
        "422 (title longo)",
        auth_api_client.post(
            f"/api/workspaces/{workspace}/tasks",
            json={"title": MARCADOR + "z" * 300, "goal": "g"},
        ),
    )
    _sem_marcador("404 (id inexistente)", auth_api_client.get(f"/api/tasks/{MARCADOR}"))


def test_o_boundary_aplica_o_detector_e_herda_os_limites_dele(
    auth_api_client: TestClient, workspace: str
) -> None:
    """O que este módulo afirma, e o que ele **não** afirma.

    O boundary garante que toda resposta atravesse `redact_document`. Ele **não** garante
    que todo segredo seja reconhecido: isso é `detect_secret_spans`, e a cobertura do
    detector é uma pergunta separada, com história própria (as cinco rodadas da E5).

    Encontrado em self-review desta rodada, e registrado aqui porque a distinção importa:
    um marcador **repetido e colado em si mesmo** é redigido só na primeira ocorrência. O
    padrão de token do GitHub começa com `\\b`, e na segunda repetição não há fronteira de
    palavra — `…789ghp_…` tem `9` antes de `g`. O boundary rodou (a redação parcial prova),
    e o detector não reconheceu o resto.

    Este teste afirma o que é verdade hoje: a resposta **atravessou** o boundary. Ele não
    finge que o texto saiu limpo, e é por isso que existe em vez de um `assert` otimista.
    """
    repetido = MARCADOR * 4
    resposta = auth_api_client.post(
        f"/api/workspaces/{workspace}/tasks", json={"title": repetido, "goal": "g"}
    )

    assert resposta.status_code == 201, resposta.text
    corpo = resposta.json()
    # O boundary agiu: o título não voltou idêntico ao que entrou.
    assert corpo["title"] != repetido
    assert "«redigido»" in corpo["title"]
    # E a ocorrência isolada — o caso realista — sai integralmente redigida.
    unica = auth_api_client.post(
        f"/api/workspaces/{workspace}/tasks", json={"title": MARCADOR, "goal": "g"}
    )
    assert MARCADOR not in unica.text
