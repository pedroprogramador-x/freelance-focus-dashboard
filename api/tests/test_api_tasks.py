"""Rotas do Orchestrator ([06] §2) — contrato HTTP da E6.

Cobre a sub-etapa 7 do prompt: as nove rotas que entram, e a confirmação de que as **cinco
que não entram** (`/runs`, `/diff`, `/findings`, `/worktree/discard`, `/events`) não existem
como superfície nesta fase.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.db.enums import TaskStatus
from app.db.models import WorkspaceTask
from tests import context_helpers

VALID_TEST_CONFIG = {
    "runner_id": "generic-subprocess-v1",
    "executable": "pytest",
    "argv": ["-q", "tests"],
    "timeout_seconds": 600,
    "env_allowlist": ["PATH"],
    "network_policy": "unrestricted",
    "output_limits": {"max_stdout_bytes": 1_048_576, "max_stderr_bytes": 262_144},
    "cwd_mode": "task_worktree",
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "ws-api"
    context_helpers.init_repo(root)
    context_helpers.write(root, "README.md", "# projeto\n")
    context_helpers.write(root, "src/app.py", "print('ok')\n")
    context_helpers.commit_all(root, "inicial")
    return root


@pytest.fixture
def workspace_id(auth_api_client: TestClient, repo: Path) -> str:
    response = auth_api_client.post(
        "/api/workspaces",
        json={"name": "ws", "type": "personal", "local_path": str(repo)},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


@pytest.fixture
def task_id(auth_api_client: TestClient, workspace_id: str) -> str:
    response = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/tasks",
        json={"title": "ajustar util", "goal": "melhorar o util"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


# ---------------------------------------------------------------- criação e leitura


def test_criar_task_devolve_201_em_draft(auth_api_client: TestClient, workspace_id: str) -> None:
    response = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/tasks",
        json={"title": "t", "goal": "g"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["phase"] is None
    assert body["execution_fingerprint"] is None


def test_criar_task_recusa_campo_desconhecido(
    auth_api_client: TestClient, workspace_id: str
) -> None:
    """`extra="forbid"`: a borda não aceita o que não declarou."""
    response = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/tasks",
        json={"title": "t", "goal": "g", "status": "approved"},
    )

    assert response.status_code == 422


def test_listar_e_filtrar_por_status(
    auth_api_client: TestClient, workspace_id: str, task_id: str
) -> None:
    todas = auth_api_client.get(f"/api/workspaces/{workspace_id}/tasks")
    filtradas = auth_api_client.get(f"/api/workspaces/{workspace_id}/tasks?status=draft")
    vazias = auth_api_client.get(f"/api/workspaces/{workspace_id}/tasks?status=done")

    assert todas.status_code == 200
    assert [t["id"] for t in todas.json()] == [task_id]
    assert len(filtradas.json()) == 1
    assert vazias.json() == []


def test_workspace_inexistente_e_404(auth_api_client: TestClient) -> None:
    response = auth_api_client.get("/api/workspaces/nao-existe/tasks")

    assert response.status_code == 404
    assert response.json()["code"] == "workspace_not_found"


def test_task_inexistente_e_404(auth_api_client: TestClient) -> None:
    response = auth_api_client.get("/api/tasks/nao-existe")

    assert response.status_code == 404
    assert response.json()["code"] == "task_not_found"


def test_todas_as_rotas_de_task_exigem_token(api_client: TestClient) -> None:
    """[06] §1: `GET /api/health` é a única rota aberta — não existe segunda."""
    leituras = ["/api/tasks/x", "/api/tasks/x/context", "/api/tasks/x/purge-preview"]
    escritas = ["/api/tasks/x/plan", "/api/tasks/x/approve", "/api/tasks/x/cancel"]

    for path in leituras:
        assert api_client.get(path).status_code == 401, f"GET {path} não exigiu credencial"

    for path in escritas:
        # 403 é aceitável: o guarda de mesma origem roda **antes** do de token, de
        # propósito ([01] §4 — uma aba maliciosa não chega nem a sondar a rota).
        status = api_client.post(path, json={}).status_code
        assert status in (401, 403), f"POST {path} não exigiu credencial"


# ------------------------------------------------------------------- plan e approve


def test_plan_devolve_a_task_aguardando_aprovacao(
    auth_api_client: TestClient, task_id: str
) -> None:
    response = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "awaiting_approval"
    assert body["planning_base_commit"] is not None
    assert body["plan_hash"] is not None
    assert body["execution_fingerprint"] is not None
    assert body["approved_at"] is None
    # O piso de [03] §5, visível no contrato.
    assert body["risk"] == "medium"
    assert body["risk_source"] == "hard_rule"


def test_plan_aceita_candidate_paths(auth_api_client: TestClient, task_id: str) -> None:
    response = auth_api_client.post(
        f"/api/tasks/{task_id}/plan", json={"candidate_paths": ["src/app.py"]}
    )

    assert response.status_code == 200


def test_plan_em_workspace_sem_git_e_409(auth_api_client: TestClient, tmp_path: Path) -> None:
    plano = tmp_path / "sem-git"
    plano.mkdir()
    (plano / "a.txt").write_text("x", encoding="utf-8")

    workspace = auth_api_client.post(
        "/api/workspaces",
        json={"name": "sem-git", "type": "personal", "local_path": str(plano)},
    ).json()
    task = auth_api_client.post(
        f"/api/workspaces/{workspace['id']}/tasks", json={"title": "t", "goal": "g"}
    ).json()

    response = auth_api_client.post(f"/api/tasks/{task['id']}/plan", json={})

    assert response.status_code == 409
    assert response.json()["code"] == "workspace_not_plannable"

    # E a task **não** fica presa em `planning`. Desde E6-AUD-003 a entrada em `planning`
    # é commitada antes do trabalho longo, então a volta deixou de ser um efeito colateral
    # do `rollback` e passou a ser a transição explícita `planning → draft` de [02] §4
    # ("erro recuperável"). Ver `test_e6_audit_round_1` para os quatro desfechos.
    assert auth_api_client.get(f"/api/tasks/{task['id']}").json()["status"] == "draft"


def test_approve_com_o_fingerprint_devolvido_pelo_plan(
    auth_api_client: TestClient, task_id: str
) -> None:
    planejada = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={}).json()

    response = auth_api_client.post(
        f"/api/tasks/{task_id}/approve",
        json={"execution_fingerprint": planejada["execution_fingerprint"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"
    assert response.json()["approved_at"] is not None


def test_approve_com_fingerprint_divergente_e_409_dizendo_qual_campo(
    auth_api_client: TestClient, workspace_id: str, task_id: str
) -> None:
    """**GATE de [07]**: a UI precisa dizer **qual campo** mudou."""
    planejada = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={}).json()

    # Configurar o Test Runner depois de o usuário ter lido o plano.
    patched = auth_api_client.patch(
        f"/api/workspaces/{workspace_id}", json={"test_config": VALID_TEST_CONFIG}
    )
    assert patched.status_code == 200, patched.text

    response = auth_api_client.post(
        f"/api/tasks/{task_id}/approve",
        json={"execution_fingerprint": planejada["execution_fingerprint"]},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "approval_fingerprint_mismatch"
    assert "test_binding" in body["diverged_fields"]
    assert body["expected_fingerprint"] != body["received_fingerprint"]


def test_reaprovar_com_o_fingerprint_novo_funciona_sem_replanejar(
    auth_api_client: TestClient, workspace_id: str, task_id: str
) -> None:
    planejada = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={}).json()
    auth_api_client.patch(
        f"/api/workspaces/{workspace_id}", json={"test_config": VALID_TEST_CONFIG}
    )

    recusa = auth_api_client.post(
        f"/api/tasks/{task_id}/approve",
        json={"execution_fingerprint": planejada["execution_fingerprint"]},
    )
    novo = recusa.json()["expected_fingerprint"]

    aceita = auth_api_client.post(
        f"/api/tasks/{task_id}/approve", json={"execution_fingerprint": novo}
    )

    assert aceita.status_code == 200
    assert aceita.json()["status"] == "approved"


def test_a_trilha_de_auditoria_sobrevive_ao_409(
    auth_api_client: TestClient, workspace_id: str, task_id: str, session_factory: object
) -> None:
    """A invalidação é uma **transição**, não um erro a desfazer ([02] §4).

    Regressão do defeito que a `session_scope` escondia: ela faz `rollback` em qualquer
    exceção, então o `SafetyEvent(approval_invalidated)` de [ADR-0008] regra 5 e a
    atualização do fingerprint eram descartados assim que o 409 subia. Os testes de
    serviço não pegavam isso porque usam a sessão crua, sem passar pela unidade de
    trabalho da camada HTTP — só um teste de ponta a ponta revela.
    """
    from app.db.models import SafetyEvent

    planejada = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={}).json()
    auth_api_client.patch(
        f"/api/workspaces/{workspace_id}", json={"test_config": VALID_TEST_CONFIG}
    )

    recusa = auth_api_client.post(
        f"/api/tasks/{task_id}/approve",
        json={"execution_fingerprint": planejada["execution_fingerprint"]},
    )
    assert recusa.status_code == 409

    factory = cast("sessionmaker[Session]", session_factory)
    with factory() as leitura:
        kinds = [
            event.kind.value
            for event in leitura.query(SafetyEvent).filter(SafetyEvent.task_id == task_id).all()
        ]

    assert "approval_invalidated" in kinds, "o SafetyEvent foi perdido no rollback"

    # E a task passou a exibir o fingerprint vigente, não o antigo.
    atual = auth_api_client.get(f"/api/tasks/{task_id}").json()
    assert atual["execution_fingerprint"] == recusa.json()["expected_fingerprint"]
    assert atual["status"] == "awaiting_approval"


def test_safety_event_da_guarda_de_entrada_tambem_sobrevive(
    auth_api_client: TestClient, task_id: str, session_factory: object
) -> None:
    """Mesma regressão, no outro caminho: a guarda de `approved → executing`.

    A rota de execução não existe nesta fase, então o gatilho é o próprio `approve` seguido
    da recusa de entrada — exercitada pelo serviço, mas com a **sessão da API**, que é onde
    o rollback acontecia.
    """
    from app.db.models import SafetyEvent
    from app.orchestrator import TransitionGuardFailed, start_execution

    planejada = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={}).json()
    aprovada = auth_api_client.post(
        f"/api/tasks/{task_id}/approve",
        json={"execution_fingerprint": planejada["execution_fingerprint"]},
    )
    assert aprovada.status_code == 200

    factory = cast("sessionmaker[Session]", session_factory)
    with factory() as escrita, pytest.raises(TransitionGuardFailed):
        start_execution(escrita, task_id)

    with factory() as leitura:
        kinds = [
            event.kind.value
            for event in leitura.query(SafetyEvent).filter(SafetyEvent.task_id == task_id).all()
        ]

    assert "capability_unenforceable" in kinds


def test_approve_exige_fingerprint_de_64_caracteres(
    auth_api_client: TestClient, task_id: str
) -> None:
    auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})

    response = auth_api_client.post(
        f"/api/tasks/{task_id}/approve", json={"execution_fingerprint": "curto"}
    )

    assert response.status_code == 422


def test_approve_de_task_em_draft_e_409(auth_api_client: TestClient, task_id: str) -> None:
    response = auth_api_client.post(
        f"/api/tasks/{task_id}/approve", json={"execution_fingerprint": "0" * 64}
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "invalid_transition"
    assert body["current_status"] == "draft"
    assert body["requested_status"] == "approved"


# ------------------------------------------------------------------ reject e cancel


def test_reject_volta_para_draft(auth_api_client: TestClient, task_id: str) -> None:
    auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})

    response = auth_api_client.post(
        f"/api/tasks/{task_id}/reject", json={"note": "quero outro plano"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "draft"
    assert body["execution_fingerprint"] is None
    assert body["result_summary"] == "quero outro plano"


def test_cancel_em_draft(auth_api_client: TestClient, task_id: str) -> None:
    response = auth_api_client.post(f"/api/tasks/{task_id}/cancel", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_cancel_de_task_terminal_e_409(auth_api_client: TestClient, task_id: str) -> None:
    auth_api_client.post(f"/api/tasks/{task_id}/cancel", json={})

    response = auth_api_client.post(f"/api/tasks/{task_id}/cancel", json={})

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_transition"


# ------------------------------------------------------------------------ contexto


def test_context_devolve_o_manifest_e_a_referencia_ao_artefato(
    auth_api_client: TestClient, task_id: str
) -> None:
    auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})

    response = auth_api_client.get(f"/api/tasks/{task_id}/context")

    assert response.status_code == 200
    body = response.json()
    assert body["manifest_hash"]
    assert body["rendered_context_hash"]
    # [01] §4: caminho **dentro** do artifact store, nunca o absoluto da máquina.
    assert body["rendered_context_ref"].startswith("artifacts/")
    assert ":" not in body["rendered_context_ref"], "vazou caminho absoluto do Windows"


def test_context_antes_do_plano_e_404(auth_api_client: TestClient, task_id: str) -> None:
    response = auth_api_client.get(f"/api/tasks/{task_id}/context")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "task_not_found"
    # E6-CONS4-003: a ausência normal de contexto tem `reason` próprio — é o que a separa
    # do 404 de task inexistente (sem `reason`) — e a mensagem descreve, não instrui.
    assert body["reason"] == "not_planned"
    assert "POST" not in body["message"]


# ------------------------------------------------------- E6-CONS-001: cancelada antes de planejar


def test_cancelada_antes_de_planejar_nao_instrui_post_plan(
    auth_api_client: TestClient, task_id: str
) -> None:
    """Reprodução exata do finding: `draft → cancelled` nunca passa por `planning`, então
    a task não tem manifest. `GET /context` tem de dizer isso sem mandar `POST /plan` —
    que responderia `409`, porque `cancelled` é terminal e imutável.
    """
    cancelado = auth_api_client.post(f"/api/tasks/{task_id}/cancel", json={})
    assert cancelado.status_code == 200, cancelado.text
    assert cancelado.json()["status"] == "cancelled"

    contexto = auth_api_client.get(f"/api/tasks/{task_id}/context")
    assert contexto.status_code == 404
    body = contexto.json()
    assert body["code"] == "task_not_found"
    assert body["reason"] == "terminal_without_context"
    # A mensagem pode falar de "planejamento" (dizendo que não há um a retomar) — o que
    # não pode aparecer é a instrução de rodar a rota, que o backend recusaria com 409.
    assert "POST" not in body["message"]
    assert "/plan`" not in body["message"]

    plano = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert plano.status_code == 409
    assert plano.json()["code"] == "invalid_transition"


def test_cancelada_depois_de_planejar_ainda_devolve_o_manifest(
    auth_api_client: TestClient, task_id: str
) -> None:
    """Quando a task **tinha** contexto congelado antes de ser cancelada, o comportamento
    autorizado existente é preservado: `GET /context` continua `200` com o manifest —
    `cancelled` sem manifest e `cancelled` com manifest não podem ser tratados igual.
    """
    auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    cancelado = auth_api_client.post(f"/api/tasks/{task_id}/cancel", json={})
    assert cancelado.status_code == 200, cancelado.text
    assert cancelado.json()["status"] == "cancelled"

    contexto = auth_api_client.get(f"/api/tasks/{task_id}/context")
    assert contexto.status_code == 200
    assert contexto.json()["manifest_hash"]


def test_task_inexistente_continua_sem_reason(auth_api_client: TestClient) -> None:
    """Task genuinamente inexistente: `404 task_not_found`, sem `reason` — o campo é
    exclusivo dos casos em que a task existe mas o manifest não (E6-CONS-001).
    """
    response = auth_api_client.get("/api/tasks/nao-existe/context")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "task_not_found"
    assert "reason" not in body


def test_task_de_outro_workspace_continua_acessivel_por_id(
    auth_api_client: TestClient, workspace_id: str, task_id: str, repo: Path
) -> None:
    """Não há isolamento por workspace em `/api/tasks/{id}` — é um app de usuário único,
    não multi-tenant ([01] §2) — e esta correção não introduz um. Uma task de **outro**
    workspace continua acessível pelo próprio id, sem 403/404 espúrio.
    """
    outro_repo = repo.parent / "outro-repo"
    context_helpers.init_repo(outro_repo)
    context_helpers.write(outro_repo, "README.md", "# outro\n")
    context_helpers.commit_all(outro_repo, "inicial")
    outro_ws = auth_api_client.post(
        "/api/workspaces",
        json={"name": "outro", "type": "personal", "local_path": str(outro_repo)},
    )
    assert outro_ws.status_code == 201, outro_ws.text
    outro_ws_id = outro_ws.json()["id"]
    assert outro_ws_id != workspace_id

    response = auth_api_client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    assert response.json()["workspace_id"] == workspace_id


# ------------------------------------------------------- E6-CONS2-001/002: matriz sistêmica


def test_falha_inesperada_no_plan_nao_e_apresentada_como_cancelamento(
    auth_api_client: TestClient, task_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E6-CONS2-001. `planning → failed` é uma aresta real ([02] §4, `_abort_planning`);
    a task fica terminal sem nunca ter congelado contexto — mesmo `reason` de
    `cancelled` sem manifest, mas a mensagem **não pode** confundir os dois: ela usa o
    `status` real da task, não um texto fixo.
    """

    def _falha_inesperada(*args: object, **kwargs: object) -> object:
        raise RuntimeError("falha inesperada simulada")

    monkeypatch.setattr("app.orchestrator.execution_manager.plan_task", _falha_inesperada)

    with pytest.raises(RuntimeError):
        auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})

    show = auth_api_client.get(f"/api/tasks/{task_id}")
    assert show.json()["status"] == "failed"

    contexto = auth_api_client.get(f"/api/tasks/{task_id}/context")
    assert contexto.status_code == 404
    body = contexto.json()
    assert body["code"] == "task_not_found"
    assert body["reason"] == "terminal_without_context"
    assert "(failed)" in body["message"]
    assert "cancel" not in body["message"].lower()

    plano = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert plano.status_code == 409
    assert plano.json()["code"] == "invalid_transition"


def _forcar_planning_sem_manifesto(session_factory: sessionmaker[Session], task_id: str) -> None:
    """Simula o estado durável real: `plan()` comita a entrada em `planning` **antes**
    de planejar (E6-AUD-003) — um crash nesse meio-tempo deixa exatamente esta linha,
    e é o que `reconcile_on_startup` reconhece como "processo morreu no meio". Escrita
    direta na sessão de teste, sem persistir nada além do banco temporário do teste.
    """
    with session_factory() as session:
        task = session.get(WorkspaceTask, task_id)
        assert task is not None
        task.status = TaskStatus.PLANNING
        task.version += 1
        session.commit()


def test_planning_sem_manifesto_nao_instrui_novo_post_plan(
    auth_api_client: TestClient, task_id: str, session_factory: sessionmaker[Session]
) -> None:
    """E6-CONS2-002. `planning` sem manifest é um estado durável legítimo, não um erro
    nem "ainda não planejada" — e `planning → planning` não é aresta ([02] §4), então a
    mensagem não pode instruir outro `POST /plan`.
    """
    _forcar_planning_sem_manifesto(session_factory, task_id)

    contexto = auth_api_client.get(f"/api/tasks/{task_id}/context")
    assert contexto.status_code == 404
    body = contexto.json()
    assert body["code"] == "task_not_found"
    assert body["reason"] == "planning_in_progress"
    assert "/plan`" not in body["message"] or "não é uma transição válida" in body["message"]

    replano = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert replano.status_code == 409
    assert replano.json()["code"] == "invalid_transition"


def _montar_draft(
    client: TestClient, tid: str, sf: sessionmaker[Session], mp: pytest.MonkeyPatch
) -> None:
    del client, tid, sf, mp  # já nasce em draft — nada a fazer


def _montar_planning(
    client: TestClient, tid: str, sf: sessionmaker[Session], mp: pytest.MonkeyPatch
) -> None:
    del client, mp
    _forcar_planning_sem_manifesto(sf, tid)


def _montar_cancelled(
    client: TestClient, tid: str, sf: sessionmaker[Session], mp: pytest.MonkeyPatch
) -> None:
    del sf, mp
    resposta = client.post(f"/api/tasks/{tid}/cancel", json={})
    assert resposta.status_code == 200, resposta.text


def _montar_failed(
    client: TestClient, tid: str, sf: sessionmaker[Session], mp: pytest.MonkeyPatch
) -> None:
    del sf

    def _falha_inesperada(*args: object, **kwargs: object) -> object:
        raise RuntimeError("falha inesperada simulada")

    mp.setattr("app.orchestrator.execution_manager.plan_task", _falha_inesperada)
    with pytest.raises(RuntimeError):
        client.post(f"/api/tasks/{tid}/plan", json={})


@pytest.mark.parametrize(
    ("rotulo", "montar_estado", "reason_esperado"),
    [
        ("draft", _montar_draft, "not_planned"),
        ("planning", _montar_planning, "planning_in_progress"),
        ("cancelled", _montar_cancelled, "terminal_without_context"),
        ("failed", _montar_failed, "terminal_without_context"),
    ],
)
def test_matriz_de_reason_por_status_sem_manifesto(
    auth_api_client: TestClient,
    task_id: str,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    rotulo: str,
    montar_estado: Callable[[TestClient, str, sessionmaker[Session], pytest.MonkeyPatch], None],
    reason_esperado: str | None,
) -> None:
    """A matriz de `context_route`, num só teste: para cada `status` sem manifest, o
    `reason` estruturado (ou a ausência dele) é exatamente o que a docstring da rota
    promete — não uma mensagem para inspecionar. Cobre os quatro estados alcançáveis
    sem manifest pelas guardas atuais; `awaiting_approval`/`needs_fix` sem manifest já
    têm cobertura própria em `requires_replan` (E6-AUD2-004), e `approved`/`executing`/
    `done` sem manifest são estruturalmente inalcançáveis pelas guardas de `approve()`.
    """
    montar_estado(auth_api_client, task_id, session_factory, monkeypatch)

    contexto = auth_api_client.get(f"/api/tasks/{task_id}/context")
    assert contexto.status_code == 404, rotulo
    body = contexto.json()
    assert body["code"] == "task_not_found", rotulo
    if reason_esperado is None:
        assert "reason" not in body, rotulo
    else:
        assert body["reason"] == reason_esperado, rotulo


# ---------------------------------------------- E6-CONS3-001/002/003: matriz sistêmica (2)


def test_workspace_arquivado_nao_promete_post_plan_em_draft_sem_contexto(
    auth_api_client: TestClient, workspace_id: str, task_id: str
) -> None:
    """E6-CONS3-001, no contrato de E6-CONS4. `draft` sem manifest é planejável **na tabela
    de estados**, mas `plan()` recusa workspace arquivado com `409 workspace_not_plannable`
    ([02] §1). `/context` descreve a ausência (`not_planned`) sem prometer nada; quem diz
    que planejar está bloqueado é `planning`, e o `409` carrega o mesmo slug.
    """
    arquivar = auth_api_client.patch(f"/api/workspaces/{workspace_id}", json={"status": "archived"})
    assert arquivar.status_code == 200, arquivar.text

    contexto = auth_api_client.get(f"/api/tasks/{task_id}/context")
    assert contexto.status_code == 404
    body = contexto.json()
    assert body["code"] == "task_not_found"
    assert body["reason"] == "not_planned"
    assert "POST" not in body["message"]

    planning = auth_api_client.get(f"/api/tasks/{task_id}").json()["planning"]
    assert planning["transition"] == "allowed"
    assert "workspace_archived" in planning["blockers"]
    assert planning["eligible"] is False

    # Consistência: o bloqueio reportado é exatamente a recusa do comando.
    plano = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert plano.status_code == 409
    assert plano.json()["code"] == "workspace_not_plannable"
    assert plano.json()["reason"] == "workspace_archived"


def test_workspace_ativo_draft_e_elegivel(auth_api_client: TestClient, task_id: str) -> None:
    """Contraprova: com workspace ativo e repositório com `HEAD` (o caso comum), `planning`
    é elegível e `POST /plan` aceita.
    """
    planning = auth_api_client.get(f"/api/tasks/{task_id}").json()["planning"]
    assert planning == {
        "transition": "allowed",
        "checked": True,
        "blockers": [],
        "eligible": True,
    }

    plano = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert plano.status_code == 200


def _forcar_needs_fix_sem_manifesto(session_factory: sessionmaker[Session], task_id: str) -> None:
    """Estado só alcançável por escrita direta no banco (E6-CONS3-002): a única rotina
    real que limpa o candidato num status *decidível* é `_invalidate_approval_on_entry`,
    e ela só escreve em `awaiting_approval` — nunca em `needs_fix`. Não é um caminho da
    E6 (que ainda não executa tasks), mas o tratamento defensivo de `/context` precisa
    acertar mesmo neste estado inconsistente, que é exatamente o que o relatório força.
    """
    with session_factory() as session:
        task = session.get(WorkspaceTask, task_id)
        assert task is not None
        task.status = TaskStatus.NEEDS_FIX
        task.approved_at = None
        task.approved_manifest_id = None
        task.version += 1
        session.commit()


def test_needs_fix_sem_manifesto_nao_instrui_rejeitar(
    auth_api_client: TestClient, task_id: str, session_factory: sessionmaker[Session]
) -> None:
    """E6-CONS3-002. `needs_fix → draft` **não é aresta** (`TRANSITIONS[needs_fix]` só
    tem `planning`/`approved`/`cancelled`) — diferente de `awaiting_approval`, onde
    `reject` é o caminho real de `requires_replan`. Para `needs_fix`, `POST /reject`
    responde `409 invalid_transition`, então nada pode instruir essa ação;
    `needs_fix → planning` **é** aresta, e `POST /plan` direto é o caminho que de fato
    funciona — que é o que `planning` reporta (E6-CONS4).
    """
    _forcar_needs_fix_sem_manifesto(session_factory, task_id)

    contexto = auth_api_client.get(f"/api/tasks/{task_id}/context")
    assert contexto.status_code == 404
    body = contexto.json()
    assert body["code"] == "task_not_found"
    assert body["reason"] == "not_planned"
    assert "rejeite" not in body["message"].lower()

    planning = auth_api_client.get(f"/api/tasks/{task_id}").json()["planning"]
    assert planning["transition"] == "allowed"
    assert planning["eligible"] is True

    # Consistência: a ação que nada recomenda de fato falha...
    rejeitar = auth_api_client.post(f"/api/tasks/{task_id}/reject", json={})
    assert rejeitar.status_code == 409
    assert rejeitar.json()["code"] == "invalid_transition"

    # ...e a que `planning` declara elegível de fato funciona.
    planejar = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert planejar.status_code == 200
    assert planejar.json()["status"] == "awaiting_approval"


def test_needs_fix_com_manifesto_ainda_devolve_o_contexto(
    auth_api_client: TestClient, task_id: str, session_factory: sessionmaker[Session]
) -> None:
    """Contraprova: `needs_fix` **com** `approved_manifest_id` (o caso comum, uma vez que
    a E7 execute tasks de verdade) continua servido pela precedência do manifest —
    `latest_manifest` não filtra por `status`.
    """
    planejada = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert planejada.status_code == 200, planejada.text

    with session_factory() as session:
        task = session.get(WorkspaceTask, task_id)
        assert task is not None
        task.status = TaskStatus.NEEDS_FIX
        task.version += 1
        session.commit()

    contexto = auth_api_client.get(f"/api/tasks/{task_id}/context")
    assert contexto.status_code == 200
    assert contexto.json()["manifest_hash"]


def test_draft_com_manifesto_historico_apos_replan_recuperavel_devolve_o_contexto(
    auth_api_client: TestClient,
    task_id: str,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E6-CONS3-003. `plan()` só limpa `approved_manifest_id` em `reject` e na invalidação
    de entrada — o desfecho "erro recuperável" de `_abort_planning` (`planning → draft`)
    usa `transition_fields(draft)`, que não toca o campo. Uma task `needs_fix` com
    manifest que falha de forma recuperável ao tentar um novo `POST /plan` volta a
    `draft` **preservando** o manifest antigo: `GET /context` responde `200`, e é isso —
    não "ainda não planejada" — o que a tela precisa mostrar mesmo com `status = draft`.
    """
    planejada = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert planejada.status_code == 200, planejada.text
    manifest_id_original = planejada.json()["approved_manifest_id"]
    assert manifest_id_original

    with session_factory() as session:
        task = session.get(WorkspaceTask, task_id)
        assert task is not None
        task.status = TaskStatus.NEEDS_FIX
        task.version += 1
        session.commit()

    from app.orchestrator.errors import InvalidTestConfig

    def _erro_recuperavel(*args: object, **kwargs: object) -> object:
        raise InvalidTestConfig("test_config malformado simulado")

    monkeypatch.setattr("app.orchestrator.execution_manager.plan_task", _erro_recuperavel)
    segunda_tentativa = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert segunda_tentativa.status_code == 422, segunda_tentativa.text

    depois = auth_api_client.get(f"/api/tasks/{task_id}")
    assert depois.json()["status"] == "draft"
    assert depois.json()["approved_manifest_id"] == manifest_id_original

    contexto = auth_api_client.get(f"/api/tasks/{task_id}/context")
    assert contexto.status_code == 200
    assert contexto.json()["id"] == manifest_id_original


# --------------------------------------------------------------------------- purga


def test_purge_preview_emite_token_e_contagens(auth_api_client: TestClient, task_id: str) -> None:
    response = auth_api_client.get(f"/api/tasks/{task_id}/purge-preview")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "workspaces",
        "tasks",
        "runs",
        "findings",
        "manifests",
        "artifacts",
        "benchmark_protected",
        "purge_token",
    }
    assert body["tasks"] == 1
    assert body["workspaces"] == 0, "purgar uma task nunca remove o workspace"
    assert body["artifacts"] == 0, "a purga não remove blob do disco (E3-AUD-006)"
    assert body["purge_token"]


def test_purga_de_task_nao_terminal_e_recusada(auth_api_client: TestClient, task_id: str) -> None:
    """[02] §11 regra 1: só `done`, `failed` ou `cancelled`."""
    token = auth_api_client.get(f"/api/tasks/{task_id}/purge-preview").json()["purge_token"]

    response = auth_api_client.post(f"/api/tasks/{task_id}/purge", json={"purge_token": token})

    assert response.status_code == 409
    assert response.json()["code"] == "task_purge_blocked"


def test_purga_de_task_terminal_funciona(auth_api_client: TestClient, task_id: str) -> None:
    auth_api_client.post(f"/api/tasks/{task_id}/cancel", json={})
    token = auth_api_client.get(f"/api/tasks/{task_id}/purge-preview").json()["purge_token"]

    response = auth_api_client.post(f"/api/tasks/{task_id}/purge", json={"purge_token": token})

    assert response.status_code == 200
    assert response.json()["tasks"] == 1
    assert auth_api_client.get(f"/api/tasks/{task_id}").status_code == 404


def test_purga_sem_token_valido_e_403(auth_api_client: TestClient, task_id: str) -> None:
    auth_api_client.post(f"/api/tasks/{task_id}/cancel", json={})

    response = auth_api_client.post(
        f"/api/tasks/{task_id}/purge", json={"purge_token": "inventado"}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "purge_token_rejected"


def test_token_e_de_uso_unico(auth_api_client: TestClient, task_id: str) -> None:
    auth_api_client.post(f"/api/tasks/{task_id}/cancel", json={})
    token = auth_api_client.get(f"/api/tasks/{task_id}/purge-preview").json()["purge_token"]

    primeira = auth_api_client.post(f"/api/tasks/{task_id}/purge", json={"purge_token": token})
    segunda = auth_api_client.post(f"/api/tasks/{task_id}/purge", json={"purge_token": token})

    assert primeira.status_code == 200
    assert segunda.status_code in (403, 404)


def test_token_de_workspace_nao_serve_para_task(
    auth_api_client: TestClient, workspace_id: str, task_id: str
) -> None:
    """O namespacing do sujeito, como asserção.

    Sem o prefixo `workspace:`/`task:`, a única coisa que separaria os dois seria um
    `workspace_id` nunca coincidir com um `task_id` — verdade prática, invariante não
    imposta.
    """
    auth_api_client.post(f"/api/tasks/{task_id}/cancel", json={})
    token_de_workspace = auth_api_client.get(
        f"/api/workspaces/{workspace_id}/purge-preview"
    ).json()["purge_token"]

    response = auth_api_client.post(
        f"/api/tasks/{task_id}/purge", json={"purge_token": token_de_workspace}
    )

    assert response.status_code == 403


# ------------------------------------------------------------------- test_config


def test_patch_configura_e_limpa_test_config(
    auth_api_client: TestClient, workspace_id: str
) -> None:
    configurado = auth_api_client.patch(
        f"/api/workspaces/{workspace_id}", json={"test_config": VALID_TEST_CONFIG}
    )
    assert configurado.status_code == 200
    assert configurado.json()["test_config"]["runner_id"] == "generic-subprocess-v1"

    limpo = auth_api_client.patch(f"/api/workspaces/{workspace_id}", json={"test_config": None})
    assert limpo.status_code == 200
    assert limpo.json()["test_config"] is None


def test_patch_de_status_nao_toca_test_config(
    auth_api_client: TestClient, workspace_id: str
) -> None:
    """Campos independentes: omitir um o deixa intacto."""
    auth_api_client.patch(
        f"/api/workspaces/{workspace_id}", json={"test_config": VALID_TEST_CONFIG}
    )

    arquivado = auth_api_client.patch(
        f"/api/workspaces/{workspace_id}", json={"status": "archived"}
    )

    assert arquivado.status_code == 200
    assert arquivado.json()["status"] == "archived"
    assert arquivado.json()["test_config"] is not None


def test_patch_com_test_config_invalido_e_422(
    auth_api_client: TestClient, workspace_id: str
) -> None:
    response = auth_api_client.patch(
        f"/api/workspaces/{workspace_id}",
        json={"test_config": {**VALID_TEST_CONFIG, "network_policy": "disabled"}},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_test_config"


# ------------------------------------------------- rotas que NÃO existem nesta fase


@pytest.mark.parametrize(
    "caminho",
    ["/runs", "/diff", "/findings", "/events", "/worktree/discard"],
)
def test_rotas_de_fases_futuras_nao_existem(
    auth_api_client: TestClient, task_id: str, caminho: str
) -> None:
    """[07]: `/runs`, `/diff`, `/findings`, `/worktree/discard` e `/events` são E7–E11.

    Uma rota que responde `501` ainda é superfície de API, e [06] §2 admite exatamente a
    tabela desta fase. `404` é o contrato correto para algo que não existe.
    """
    response = auth_api_client.get(f"/api/tasks/{task_id}{caminho}")

    assert response.status_code == 404


def test_nao_existe_escrita_direta_de_status(auth_api_client: TestClient, task_id: str) -> None:
    """[ADR-0008] regra 4: "a API expõe comandos […], nunca escrita direta de `status`"."""
    response = auth_api_client.patch(f"/api/tasks/{task_id}", json={"status": "approved"})

    assert response.status_code == 405
