"""E6-CONS4 — elegibilidade de planejamento, `reason` de `/context` e `plan_standing`.

Os quatro findings da auditoria consolidada rodada 4 (`docs/audits/e6-consolidated-round-4.md`)
e os testes de contrato que o diagnóstico propôs. O ponto central de todos: a orientação de
ação vem **só** de `TaskResponse.planning`, derivada das mesmas funções de guarda que
`POST /plan` aplica — e cada teste de bloqueio confere o `reason` do próprio comando.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.enums import TaskStatus
from app.db.models import ContextManifest, DevWorkspace, SafetyEvent, WorkspaceTask
from app.db.session import session_scope
from app.orchestrator import TransitionGuardFailed, start_execution
from app.orchestrator.errors import ConcurrentTaskUpdate, InvalidTestConfig
from tests import context_helpers

# ----------------------------------------------------------------------------- montagem


def _gone(path: Path) -> None:
    """Faz `path` deixar de existir. Renomeia em vez de apagar: no Windows os objetos do
    git são somente leitura e `shutil.rmtree` falharia no meio."""
    path.rename(path.with_name(path.name + "-removido"))


def _repo(root: Path, *, git: bool = True, commit: bool = True) -> Path:
    if git:
        context_helpers.init_repo(root)
    else:
        root.mkdir(parents=True)
    context_helpers.write(root, "README.md", "# projeto\n")
    if git and commit:
        context_helpers.commit_all(root, "inicial")
    return root


def _workspace(client: TestClient, root: Path, name: str = "ws") -> str:
    response = client.post(
        "/api/workspaces", json={"name": name, "type": "personal", "local_path": str(root)}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _task(client: TestClient, workspace_id: str) -> str:
    response = client.post(
        f"/api/workspaces/{workspace_id}/tasks", json={"title": "t", "goal": "melhorar o util"}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _detail(client: TestClient, task_id: str) -> dict[str, Any]:
    response = client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _archive(client: TestClient, workspace_id: str, status: str = "archived") -> None:
    response = client.patch(f"/api/workspaces/{workspace_id}", json={"status": status})
    assert response.status_code == 200, response.text


def _invalidate_by_head(
    client: TestClient, task_id: str, root: Path, session_factory: sessionmaker[Session]
) -> None:
    """plan → approve → novo commit → guarda de entrada: `requires_replan` real."""
    fingerprint = client.post(f"/api/tasks/{task_id}/plan", json={}).json()["execution_fingerprint"]
    approved = client.post(
        f"/api/tasks/{task_id}/approve", json={"execution_fingerprint": fingerprint}
    )
    assert approved.status_code == 200, approved.text
    context_helpers.write(root, "novo.txt", "x\n")
    context_helpers.commit_all(root, "segundo")
    with pytest.raises(TransitionGuardFailed), session_scope(session_factory) as session:
        start_execution(session, task_id)
    assert _detail(client, task_id)["approval_state"] == "requires_replan"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _repo(tmp_path / "ws-cons4")


@pytest.fixture
def workspace_id(auth_api_client: TestClient, repo: Path) -> str:
    return _workspace(auth_api_client, repo)


@pytest.fixture
def task_id(auth_api_client: TestClient, workspace_id: str) -> str:
    return _task(auth_api_client, workspace_id)


@pytest.fixture
def probe_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Conta as sondagens de git feitas **pelo Planner/elegibilidade** (um só ponto)."""
    from app.git_runtime import probe_head as real

    calls: list[str] = []

    def _counting(local_path: str, **kwargs: Any) -> Any:
        calls.append(local_path)
        return real(local_path, **kwargs)

    monkeypatch.setattr("app.orchestrator.planner.probe_head", _counting)
    return calls


# --------------------------------------------------------------- E6-CONS4-001: reject + arquivo


def test_cons4_001_requires_replan_em_workspace_arquivado(
    auth_api_client: TestClient,
    workspace_id: str,
    task_id: str,
    repo: Path,
    session_factory: sessionmaker[Session],
) -> None:
    """Rejeitar agora é permitido; planejar depois exige reativar. As duas coisas aparecem
    separadas, e nenhuma é prometida além do que a guarda aceita."""
    _invalidate_by_head(auth_api_client, task_id, repo, session_factory)
    _archive(auth_api_client, workspace_id)

    detail = _detail(auth_api_client, task_id)
    assert detail["planning"] == {
        "transition": "after_reject",
        "checked": True,
        "blockers": ["workspace_archived"],
        "eligible": False,
    }
    assert detail["plan_standing"] == "historical"

    context = auth_api_client.get(f"/api/tasks/{task_id}/context")
    assert context.status_code == 404
    assert context.json()["reason"] == "approval_invalidated"
    assert "rejeite" not in context.json()["message"].lower()
    assert "POST" not in context.json()["message"]

    rejected = auth_api_client.post(f"/api/tasks/{task_id}/reject", json={})
    assert rejected.status_code == 200
    assert rejected.json()["planning"]["transition"] == "allowed"
    assert rejected.json()["planning"]["blockers"] == ["workspace_archived"]
    assert rejected.json()["planning"]["eligible"] is False

    refused = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert refused.status_code == 409
    assert refused.json()["reason"] == "workspace_archived"

    # A reativação é o controle existente (`PATCH status=active`); depois dela, elegível.
    _archive(auth_api_client, workspace_id, "active")
    assert _detail(auth_api_client, task_id)["planning"]["eligible"] is True
    assert auth_api_client.post(f"/api/tasks/{task_id}/plan", json={}).status_code == 200


def test_requires_replan_arquivado_e_sem_git_lista_os_dois_bloqueios(
    auth_api_client: TestClient,
    workspace_id: str,
    task_id: str,
    repo: Path,
    session_factory: sessionmaker[Session],
) -> None:
    _invalidate_by_head(auth_api_client, task_id, repo, session_factory)
    _archive(auth_api_client, workspace_id)
    _gone(repo / ".git")

    planning = _detail(auth_api_client, task_id)["planning"]
    assert planning["transition"] == "after_reject"
    assert planning["checked"] is True
    assert planning["blockers"] == ["workspace_archived", "workspace_not_git_repo"]
    assert planning["eligible"] is False


def test_verificacao_adiada_nao_vira_elegibilidade(
    auth_api_client: TestClient,
    workspace_id: str,
    task_id: str,
    repo: Path,
    probe_calls: list[str],
) -> None:
    """`awaiting_approval` com plano vigente: replanejar não é o próximo passo, e o git
    fica **adiado** (`checked = false`). Os bloqueios baratos continuam listados, mas a
    lista é parcial — o `.git` removido não aparece, e nada disso vira elegibilidade."""
    assert auth_api_client.post(f"/api/tasks/{task_id}/plan", json={}).status_code == 200
    _archive(auth_api_client, workspace_id)
    _gone(repo / ".git")
    probe_calls.clear()

    planning = _detail(auth_api_client, task_id)["planning"]
    assert planning == {
        "transition": "after_reject",
        "checked": False,
        "blockers": ["workspace_archived"],
        "eligible": False,
    }
    assert probe_calls == [], "o git não deveria ter sido consultado"


# ------------------------------------------------------------- E6-CONS4-002: pré-condições git


def _no_git(root: Path) -> None:
    del root


def _break_git_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """`timeout`/falha de IO ao executar o git: `_run_git` devolve `None`."""
    monkeypatch.setattr("app.git_runtime._run_git", lambda *a, **k: None)


def _git_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.git_runtime.shutil.which", lambda _name: None)


GitScenario = tuple[str, dict[str, bool], Callable[[pytest.MonkeyPatch], None] | None, bool, str]

GIT_SCENARIOS: list[GitScenario] = [
    ("sem .git", {"git": False}, None, False, "workspace_not_git_repo"),
    ("sem HEAD", {"git": True, "commit": False}, None, False, "repository_without_head"),
    ("diretorio removido", {}, None, True, "workspace_not_git_repo"),
    ("git nao executa", {}, _break_git_process, False, "git_unverifiable"),
    ("git ausente do PATH", {}, _git_absent, False, "git_unverifiable"),
]


@pytest.mark.parametrize(
    ("rotulo", "repo_kwargs", "break_after", "remove_dir", "expected"), GIT_SCENARIOS
)
def test_cons4_002_precondicoes_git(
    auth_api_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rotulo: str,
    repo_kwargs: dict[str, bool],
    break_after: Callable[[pytest.MonkeyPatch], None] | None,
    remove_dir: bool,
    expected: str,
) -> None:
    root = _repo(tmp_path / "ws", **repo_kwargs)
    wid = _workspace(auth_api_client, root)
    tid = _task(auth_api_client, wid)
    if remove_dir:
        _gone(root)
    if break_after is not None:
        break_after(monkeypatch)

    planning = _detail(auth_api_client, tid)["planning"]
    assert planning["blockers"] == [expected], rotulo
    assert planning["checked"] is True, rotulo
    assert planning["eligible"] is False, rotulo

    context = auth_api_client.get(f"/api/tasks/{tid}/context")
    assert context.json()["reason"] == "not_planned", rotulo
    assert "POST" not in context.json()["message"], rotulo

    refused = auth_api_client.post(f"/api/tasks/{tid}/plan", json={})
    assert refused.status_code == 409, rotulo
    assert refused.json()["code"] == "workspace_not_plannable", rotulo
    assert refused.json()["reason"] == expected, rotulo
    assert _detail(auth_api_client, tid)["status"] == "draft", rotulo


def _corrupt_test_config(session_factory: sessionmaker[Session], workspace_id: str) -> None:
    """A coluna é JSON livre: uma linha antiga/editada por fora chega malformada."""
    with session_factory() as session:
        workspace = session.get(DevWorkspace, workspace_id)
        assert workspace is not None
        workspace.test_config = {"runner_id": 1}
        session.commit()


def test_test_config_malformado_e_bloqueio_e_422_com_o_mesmo_reason(
    auth_api_client: TestClient,
    workspace_id: str,
    task_id: str,
    session_factory: sessionmaker[Session],
) -> None:
    _corrupt_test_config(session_factory, workspace_id)

    planning = _detail(auth_api_client, task_id)["planning"]
    assert planning["blockers"] == ["invalid_test_config"]
    assert planning["eligible"] is False

    refused = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert refused.status_code == 422
    assert refused.json()["reason"] == "invalid_test_config"
    assert _detail(auth_api_client, task_id)["status"] == "draft"


# ------------------------------------------- contrato: elegibilidade × POST /plan, mesma fonte


def _scenario_ok(**_: Any) -> None:
    return None


def _scenario_archived(client: TestClient, workspace_id: str, **_: Any) -> None:
    _archive(client, workspace_id)


def _scenario_not_git(repo: Path, **_: Any) -> None:
    _gone(repo / ".git")


def _scenario_archived_not_git(client: TestClient, workspace_id: str, repo: Path, **_: Any) -> None:
    _archive(client, workspace_id)
    _gone(repo / ".git")


def _scenario_not_git_bad_config(
    repo: Path, workspace_id: str, session_factory: sessionmaker[Session], **_: Any
) -> None:
    _gone(repo / ".git")
    _corrupt_test_config(session_factory, workspace_id)


@pytest.mark.parametrize(
    "montar",
    [
        _scenario_ok,
        _scenario_archived,
        _scenario_not_git,
        _scenario_archived_not_git,
        _scenario_not_git_bad_config,
    ],
)
def test_elegibilidade_e_post_plan_concordam(
    auth_api_client: TestClient,
    workspace_id: str,
    task_id: str,
    repo: Path,
    session_factory: sessionmaker[Session],
    montar: Callable[..., None],
) -> None:
    """Sem mudança de estado entre as duas chamadas: `eligible` ⇔ `200`, e uma recusa
    carrega como `reason` o **primeiro** bloqueio listado — a lista segue a ordem de
    execução real de `plan()`. Concorrência e IO ficam fora de propósito (ver o teste de
    fotografia abaixo): elegibilidade positiva não promete `200` em geral."""
    montar(
        client=auth_api_client,
        workspace_id=workspace_id,
        repo=repo,
        session_factory=session_factory,
    )
    planning = _detail(auth_api_client, task_id)["planning"]
    response = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})

    if planning["eligible"]:
        assert response.status_code == 200, response.text
    else:
        assert response.status_code in (409, 422), response.text
        assert response.json()["reason"] == planning["blockers"][0]


def test_elegibilidade_e_plan_usam_a_mesma_classificacao(
    auth_api_client: TestClient, task_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prova de caminho compartilhado: trocar **uma** função do Planner muda as duas
    respostas. Uma segunda implementação das regras não reagiria."""
    monkeypatch.setattr(
        "app.orchestrator.planner.classify_git", lambda _probe: "repository_without_head"
    )

    assert _detail(auth_api_client, task_id)["planning"]["blockers"] == ["repository_without_head"]
    refused = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert refused.status_code == 409
    assert refused.json()["reason"] == "repository_without_head"


def test_elegivel_e_fotografia_nao_garantia(
    auth_api_client: TestClient, task_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uma recusa que a elegibilidade não tem como prever (concorrência) continua possível."""
    assert _detail(auth_api_client, task_id)["planning"]["eligible"] is True

    def _perdeu_a_corrida(*_a: object, **_k: object) -> object:
        raise ConcurrentTaskUpdate("outra transação venceu")

    monkeypatch.setattr("app.orchestrator.execution_manager.plan_task", _perdeu_a_corrida)
    response = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert response.status_code == 409
    assert response.json()["code"] == "concurrent_task_update"
    assert "reason" not in response.json()


# -------------------------------------------------------------- mudança entre GET e POST


def test_workspace_arquivado_entre_get_e_post(
    auth_api_client: TestClient, workspace_id: str, task_id: str
) -> None:
    assert _detail(auth_api_client, task_id)["planning"]["eligible"] is True
    _archive(auth_api_client, workspace_id)

    refused = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert refused.status_code == 409
    assert refused.json()["reason"] == "workspace_archived"
    assert _detail(auth_api_client, task_id)["planning"]["blockers"] == ["workspace_archived"]


def test_git_removido_entre_get_e_post(
    auth_api_client: TestClient, task_id: str, repo: Path
) -> None:
    assert _detail(auth_api_client, task_id)["planning"]["eligible"] is True
    _gone(repo / ".git")

    refused = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert refused.status_code == 409
    assert refused.json()["reason"] == "workspace_not_git_repo"
    detail = _detail(auth_api_client, task_id)
    assert detail["status"] == "draft"
    assert detail["planning"]["blockers"] == ["workspace_not_git_repo"]


# --------------------------------------------------------- somente leitura e custo das sondagens


def _counts(session_factory: sessionmaker[Session]) -> tuple[int, int]:
    with session_factory() as session:
        events = session.scalar(select(func.count()).select_from(SafetyEvent)) or 0
        manifests = session.scalar(select(func.count()).select_from(ContextManifest)) or 0
    return events, manifests


def test_get_individual_e_somente_leitura(
    auth_api_client: TestClient,
    task_id: str,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv_log: list[list[str]] = []
    real_run = subprocess.run

    def _recording_run(args: list[str], *a: Any, **k: Any) -> Any:
        argv_log.append(list(args))
        return real_run(args, *a, **k)

    monkeypatch.setattr("app.git_runtime.subprocess.run", _recording_run)
    artifacts: Path = auth_api_client.app.state.settings.artifacts_dir  # type: ignore[attr-defined]

    before = _detail(auth_api_client, task_id)
    counts_before = _counts(session_factory)
    files_before = sorted(artifacts.rglob("*")) if artifacts.exists() else []
    argv_log.clear()

    for _ in range(3):
        after = _detail(auth_api_client, task_id)

    assert (after["status"], after["version"]) == (before["status"], before["version"])
    assert _counts(session_factory) == counts_before
    assert (sorted(artifacts.rglob("*")) if artifacts.exists() else []) == files_before
    # Custo real: 2 leituras por GET de detalhe em `draft`, e só `rev-parse`.
    assert len(argv_log) == 6
    subcommands = {args[args.index("-C") + 2] for args in argv_log}
    assert subcommands == {"rev-parse"}


def test_listagem_nao_sonda_git_por_task(
    auth_api_client: TestClient, workspace_id: str, probe_calls: list[str]
) -> None:
    for _ in range(12):
        _task(auth_api_client, workspace_id)
    probe_calls.clear()

    listed = auth_api_client.get(f"/api/workspaces/{workspace_id}/tasks")
    assert listed.status_code == 200
    assert len(listed.json()) == 12
    assert all("planning" not in item for item in listed.json())
    assert probe_calls == []


def test_context_nao_sonda_git(
    auth_api_client: TestClient, task_id: str, probe_calls: list[str]
) -> None:
    probe_calls.clear()
    assert auth_api_client.get(f"/api/tasks/{task_id}/context").status_code == 404
    assert probe_calls == []


def test_transicao_proibida_nao_consulta_git(
    auth_api_client: TestClient, task_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert auth_api_client.post(f"/api/tasks/{task_id}/cancel", json={}).status_code == 200

    def _nao_deveria_rodar(*_a: object, **_k: object) -> object:
        raise AssertionError("git consultado para uma task terminal")

    monkeypatch.setattr("app.orchestrator.planner.probe_head", _nao_deveria_rodar)
    planning = _detail(auth_api_client, task_id)["planning"]
    assert planning == {
        "transition": "forbidden",
        "checked": False,
        "blockers": [],
        "eligible": False,
    }


# ------------------------------------------------------ E6-CONS4-003/004: `/context` e histórico


def test_cons4_003_ausencia_normal_e_distinta_de_task_inexistente(
    auth_api_client: TestClient, api_client: TestClient, task_id: str
) -> None:
    normal = auth_api_client.get(f"/api/tasks/{task_id}/context")
    assert normal.status_code == 404
    assert normal.json()["reason"] == "not_planned"

    missing = auth_api_client.get("/api/tasks/nao-existe/context")
    assert missing.status_code == 404
    assert missing.json()["code"] == "task_not_found"
    assert "reason" not in missing.json()

    del api_client.headers["Authorization"]
    assert api_client.get(f"/api/tasks/{task_id}/context").status_code == 401


def test_estado_sem_manifest_inconsistente_tem_reason_proprio(
    auth_api_client: TestClient, task_id: str, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        task = session.get(WorkspaceTask, task_id)
        assert task is not None
        task.status = TaskStatus.APPROVED
        task.version += 1
        session.commit()

    response = auth_api_client.get(f"/api/tasks/{task_id}/context")
    assert response.status_code == 404
    assert response.json()["reason"] == "unavailable_in_state"


def test_plano_preservado_apos_reject_e_historico_sem_manifest(
    auth_api_client: TestClient, task_id: str
) -> None:
    """plan → reject é fluxo **normal** da E6: o plano fica (registro), o candidato sai."""
    planned = _detail(auth_api_client, task_id)
    assert planned["plan_standing"] == "none"

    assert auth_api_client.post(f"/api/tasks/{task_id}/plan", json={}).status_code == 200
    assert _detail(auth_api_client, task_id)["plan_standing"] == "current"

    rejected = auth_api_client.post(f"/api/tasks/{task_id}/reject", json={}).json()
    assert rejected["plan"] is not None
    assert rejected["plan_standing"] == "historical"
    assert rejected["approved_manifest_id"] is None
    assert rejected["execution_fingerprint"] is None

    # Plano preservado ≠ manifest preservado: aqui não há manifest a marcar como histórico.
    context = auth_api_client.get(f"/api/tasks/{task_id}/context")
    assert context.status_code == 404
    assert context.json()["reason"] == "not_planned"


def test_cons4_004_manifest_preservado_apos_rollback_recuperavel(
    auth_api_client: TestClient,
    task_id: str,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planned = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={}).json()
    with session_factory() as session:
        task = session.get(WorkspaceTask, task_id)
        assert task is not None
        task.status = TaskStatus.NEEDS_FIX
        task.version += 1
        session.commit()

    def _recuperavel(*_a: object, **_k: object) -> object:
        raise InvalidTestConfig("simulado")

    monkeypatch.setattr("app.orchestrator.execution_manager.plan_task", _recuperavel)
    assert auth_api_client.post(f"/api/tasks/{task_id}/plan", json={}).status_code == 422

    detail = _detail(auth_api_client, task_id)
    assert detail["status"] == "draft"
    assert detail["plan_standing"] == "historical"
    assert detail["approved_manifest_id"] == planned["approved_manifest_id"]
    # O fingerprint preservado continua visível, mas o plano a que ele pertence é histórico.
    assert detail["execution_fingerprint"] == planned["execution_fingerprint"]

    context = auth_api_client.get(f"/api/tasks/{task_id}/context")
    assert context.status_code == 200
    assert context.json()["id"] == planned["approved_manifest_id"]


def test_plan_standing_nos_demais_estados(
    auth_api_client: TestClient,
    workspace_id: str,
    task_id: str,
    repo: Path,
    session_factory: sessionmaker[Session],
) -> None:
    fingerprint = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={}).json()[
        "execution_fingerprint"
    ]
    approved = auth_api_client.post(
        f"/api/tasks/{task_id}/approve", json={"execution_fingerprint": fingerprint}
    ).json()
    assert approved["plan_standing"] == "current"

    cancelled = auth_api_client.post(f"/api/tasks/{task_id}/cancel", json={}).json()
    assert cancelled["plan_standing"] == "final"
    assert auth_api_client.get(f"/api/tasks/{task_id}/context").status_code == 200

    fresh = _task(auth_api_client, workspace_id)
    auth_api_client.post(f"/api/tasks/{fresh}/cancel", json={})
    assert _detail(auth_api_client, fresh)["plan_standing"] == "none"
    assert auth_api_client.get(f"/api/tasks/{fresh}/context").json()["reason"] == (
        "terminal_without_context"
    )

    invalidated = _task(auth_api_client, workspace_id)
    _invalidate_by_head(auth_api_client, invalidated, repo, session_factory)
    assert _detail(auth_api_client, invalidated)["plan_standing"] == "historical"


# ----------------------------------------------------------------------------- vazamento


def test_bloqueios_nao_vazam_caminho_nem_saida_do_git(
    auth_api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path / "ws-secreto-local", git=False)
    tid = _task(auth_api_client, _workspace(auth_api_client, root, "vazamento"))

    bodies = [
        auth_api_client.get(f"/api/tasks/{tid}").text,
        auth_api_client.get(f"/api/tasks/{tid}/context").text,
        auth_api_client.post(f"/api/tasks/{tid}/plan", json={}).text,
    ]
    _break_git_process(monkeypatch)
    bodies.append(auth_api_client.get(f"/api/tasks/{tid}").text)
    bodies.append(auth_api_client.post(f"/api/tasks/{tid}/plan", json={}).text)

    forbidden = (
        str(root),
        root.as_posix(),
        "ws-secreto-local",
        "fatal:",
        "not a git repository",
        "rev-parse",
        "stderr",
    )
    for body in bodies:
        for needle in forbidden:
            assert needle not in body, (needle, body)
