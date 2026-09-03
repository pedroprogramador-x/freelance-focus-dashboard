"""Gate 4 da E3 (integração) — `purge-preview` e `purge` via `TestClient`.

Cobre: purga sem prévia (sem token) recusada; token de outro workspace recusado; token
expirado (relógio mockado) recusado; token reutilizado recusado; purga de workspace
`active` recusada mesmo com token válido; e o fluxo feliz completo
(archive → preview → purge).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings
from app.db.enums import (
    ComplexityLevel,
    ExecutionMode,
    FilesReadSource,
    FindingPurpose,
    FindingSeverity,
    RiskLevel,
    RiskSource,
    RunAgent,
    RunPurpose,
    RunStatus,
    RunTransport,
    TaskStatus,
    TokenSource,
)
from app.db.models import AuditFinding, ContextManifest, Run, WorkspaceTask
from app.main import create_app
from app.workspace import PurgeTokenStore
from tests.conftest import LOCAL_BASE_URL


class _Clock:
    def __init__(self) -> None:
        self.now = 10_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def purge_setup(
    temp_settings: AppSettings, migrated_url: str
) -> Iterator[tuple[TestClient, _Clock]]:
    """Cliente autenticado cujo `PurgeTokenStore` usa um relógio controlável."""
    del migrated_url
    app = create_app(temp_settings)
    clock = _Clock()
    app.state.purge_token_store = PurgeTokenStore(clock=clock)
    with TestClient(app, base_url=LOCAL_BASE_URL) as client:
        client.headers["Authorization"] = f"Bearer {app.state.session_token}"
        yield client, clock


def _new_workspace(client: TestClient, tmp_path: Path, name: str) -> str:
    directory = tmp_path / name
    directory.mkdir()
    response = client.post(
        "/api/workspaces",
        json={"name": name, "type": "personal", "local_path": str(directory)},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _archive(client: TestClient, workspace_id: str) -> None:
    assert (
        client.patch(f"/api/workspaces/{workspace_id}", json={"status": "archived"}).status_code
        == 200
    )


def _factory(client: TestClient) -> sessionmaker[Session]:
    return client.app.state.session_factory  # type: ignore[attr-defined,no-any-return]


def _terminal_task(workspace_id: str, *, benchmark_group_id: str | None = None) -> WorkspaceTask:
    return WorkspaceTask(
        workspace_id=workspace_id,
        title="t",
        goal="g",
        status=TaskStatus.DONE,
        risk=RiskLevel.LOW,
        complexity=ComplexityLevel.LOW,
        risk_source=RiskSource.HARD_RULE,
        execution_mode=ExecutionMode.ORCHESTRATED,
        benchmark_group_id=benchmark_group_id,
    )


def _run(task_id: str, invocation_id: str, purpose: RunPurpose, subject_run_id: str | None) -> Run:
    return Run(
        invocation_id=invocation_id,
        task_id=task_id,
        subject_run_id=subject_run_id,
        agent=RunAgent.DEVELOPER if purpose is RunPurpose.EXECUTION else RunAgent.AUDITOR,
        purpose=purpose,
        provider="test",
        provider_adapter="test",
        transport=RunTransport.CLI,
        status=RunStatus.OK,
        token_source=TokenSource.UNAVAILABLE,
        files_read_source=FilesReadSource.UNAVAILABLE,
    )


# ------------------------------------------------------------------- preview


def test_preview_conta_e_emite_token(
    purge_setup: tuple[TestClient, _Clock], tmp_path: Path
) -> None:
    client, _clock = purge_setup
    workspace_id = _new_workspace(client, tmp_path, "ws")

    response = client.get(f"/api/workspaces/{workspace_id}/purge-preview")
    assert response.status_code == 200
    body = response.json()
    assert body["workspaces"] == 1
    assert body["tasks"] == 0
    assert body["runs"] == 0
    assert body["findings"] == 0
    assert body["manifests"] == 0
    assert body["artifacts"] == 0
    assert body["benchmark_protected"] is False
    assert isinstance(body["purge_token"], str) and body["purge_token"]


def test_preview_workspace_inexistente_e_404(purge_setup: tuple[TestClient, _Clock]) -> None:
    client, _clock = purge_setup
    assert client.get("/api/workspaces/nao-existe/purge-preview").status_code == 404


def test_artifacts_e_sempre_zero_mesmo_com_manifest_presente(
    purge_setup: tuple[TestClient, _Clock], tmp_path: Path
) -> None:
    """E3-AUD-006: a purga não remove blob de disco; `artifacts` não pode afirmar remoção."""
    client, _clock = purge_setup
    workspace_id = _new_workspace(client, tmp_path, "com-manifest")

    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        task = WorkspaceTask(
            workspace_id=workspace_id,
            title="t",
            goal="g",
            risk=RiskLevel.LOW,
            complexity=ComplexityLevel.LOW,
            risk_source=RiskSource.HARD_RULE,
            execution_mode=ExecutionMode.CLAUDE_ONLY,
        )
        session.add(task)
        session.flush()
        session.add(
            ContextManifest(
                task_id=task.id,
                git_head="0" * 40,
                rendered_context_hash="a" * 64,
                rendered_context_ref="artifacts/aaaa.json",
                renderer_version="v1",
                approx_tokens=1,
                total_chars=1,
                manifest_hash="b" * 64,
            )
        )
        session.commit()

    preview = client.get(f"/api/workspaces/{workspace_id}/purge-preview").json()
    assert preview["manifests"] == 1  # contagem real
    assert preview["artifacts"] == 0  # nada é removido do disco


# --------------------------------------------------------------------- purge


def test_purga_sem_previa_e_403(purge_setup: tuple[TestClient, _Clock], tmp_path: Path) -> None:
    client, _clock = purge_setup
    workspace_id = _new_workspace(client, tmp_path, "ws")
    _archive(client, workspace_id)

    response = client.post(
        f"/api/workspaces/{workspace_id}/purge", json={"purge_token": "token-inventado"}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "purge_forbidden"


def test_purga_sem_token_no_corpo_e_422(
    purge_setup: tuple[TestClient, _Clock], tmp_path: Path
) -> None:
    client, _clock = purge_setup
    workspace_id = _new_workspace(client, tmp_path, "ws")

    assert client.post(f"/api/workspaces/{workspace_id}/purge", json={}).status_code == 422


def test_token_de_outro_workspace_e_403(
    purge_setup: tuple[TestClient, _Clock], tmp_path: Path
) -> None:
    client, _clock = purge_setup
    ws_a = _new_workspace(client, tmp_path, "a")
    ws_b = _new_workspace(client, tmp_path, "b")
    _archive(client, ws_b)

    token_a = client.get(f"/api/workspaces/{ws_a}/purge-preview").json()["purge_token"]

    response = client.post(f"/api/workspaces/{ws_b}/purge", json={"purge_token": token_a})
    assert response.status_code == 403
    assert response.json()["code"] == "purge_forbidden"


def test_token_expirado_e_403(purge_setup: tuple[TestClient, _Clock], tmp_path: Path) -> None:
    client, clock = purge_setup
    workspace_id = _new_workspace(client, tmp_path, "ws")
    _archive(client, workspace_id)

    token = client.get(f"/api/workspaces/{workspace_id}/purge-preview").json()["purge_token"]
    clock.advance(61.0)  # TTL padrão é 60s

    response = client.post(f"/api/workspaces/{workspace_id}/purge", json={"purge_token": token})
    assert response.status_code == 403


def test_token_reutilizado_e_403(purge_setup: tuple[TestClient, _Clock], tmp_path: Path) -> None:
    client, _clock = purge_setup
    workspace_id = _new_workspace(client, tmp_path, "ws")
    _archive(client, workspace_id)

    token = client.get(f"/api/workspaces/{workspace_id}/purge-preview").json()["purge_token"]

    primeira = client.post(f"/api/workspaces/{workspace_id}/purge", json={"purge_token": token})
    assert primeira.status_code == 200

    segunda = client.post(f"/api/workspaces/{workspace_id}/purge", json={"purge_token": token})
    assert segunda.status_code == 403


def test_purga_de_workspace_ativo_e_409_mesmo_com_token_valido(
    purge_setup: tuple[TestClient, _Clock], tmp_path: Path
) -> None:
    client, _clock = purge_setup
    workspace_id = _new_workspace(client, tmp_path, "ws")  # fica ACTIVE

    token = client.get(f"/api/workspaces/{workspace_id}/purge-preview").json()["purge_token"]

    response = client.post(f"/api/workspaces/{workspace_id}/purge", json={"purge_token": token})
    assert response.status_code == 409
    assert response.json()["code"] == "workspace_purge_blocked"
    # não foi removido
    assert client.get(f"/api/workspaces/{workspace_id}").status_code == 200


def test_fluxo_feliz_archive_preview_purge(
    purge_setup: tuple[TestClient, _Clock], tmp_path: Path
) -> None:
    client, _clock = purge_setup
    workspace_id = _new_workspace(client, tmp_path, "ws")

    _archive(client, workspace_id)
    token = client.get(f"/api/workspaces/{workspace_id}/purge-preview").json()["purge_token"]

    response = client.post(f"/api/workspaces/{workspace_id}/purge", json={"purge_token": token})
    assert response.status_code == 200
    assert response.json() == {
        "workspaces": 1,
        "tasks": 0,
        "runs": 0,
        "findings": 0,
        "manifests": 0,
        "artifacts": 0,
    }

    assert client.get(f"/api/workspaces/{workspace_id}").status_code == 404
    assert client.get("/api/workspaces").json() == []


# ----------------------------------------- E3-AUD2-005: proteção de benchmark


def test_purga_recusada_quando_grupo_de_benchmark_tem_avaliacao(
    purge_setup: tuple[TestClient, _Clock], tmp_path: Path
) -> None:
    """[02] §11 regra 6 / R8: a avaliação vive numa task de OUTRO workspace do mesmo grupo.

    Prova a regra inteira, não o caso nomeado: a checagem tem de partir das tasks do
    workspace A, subir ao `benchmark_group_id`, e achar o `AuditFinding` de benchmark numa
    task do workspace B. Uma checagem restrita "runs deste workspace" passaria batido.
    """
    client, _clock = purge_setup
    ws_a = _new_workspace(client, tmp_path, "bench-a")  # este vamos tentar purgar
    ws_b = _new_workspace(client, tmp_path, "bench-b")  # a avaliação vive aqui

    with _factory(client)() as session:
        task_a = _terminal_task(ws_a, benchmark_group_id="grupo-x")
        task_b = _terminal_task(ws_b, benchmark_group_id="grupo-x")
        session.add_all([task_a, task_b])
        session.flush()
        run_b = _run(task_b.id, "inv-exec-b", RunPurpose.EXECUTION, None)
        session.add(run_b)
        session.flush()
        session.add(
            AuditFinding(
                run_id=run_b.id,
                purpose=FindingPurpose.BENCHMARK_EVALUATION,
                rubric_version="rubric-v1",
                severity=FindingSeverity.INFO,
                category="benchmark",
                summary="avaliação de benchmark",
            )
        )
        session.commit()

    _archive(client, ws_a)
    preview = client.get(f"/api/workspaces/{ws_a}/purge-preview").json()
    assert preview["benchmark_protected"] is True
    token = preview["purge_token"]

    response = client.post(f"/api/workspaces/{ws_a}/purge", json={"purge_token": token})
    assert response.status_code == 409
    assert response.json()["code"] == "workspace_purge_benchmark_protected"
    assert client.get(f"/api/workspaces/{ws_a}").status_code == 200  # não removido


def test_purga_recusada_com_run_de_benchmark_evaluation(
    purge_setup: tuple[TestClient, _Clock], tmp_path: Path
) -> None:
    """O outro ramo da regra: um `Run` com `purpose = benchmark_evaluation` no grupo."""
    client, _clock = purge_setup
    workspace_id = _new_workspace(client, tmp_path, "bench-run")

    with _factory(client)() as session:
        task = _terminal_task(workspace_id, benchmark_group_id="grupo-y")
        session.add(task)
        session.flush()
        exec_run = _run(task.id, "inv-exec", RunPurpose.EXECUTION, None)
        session.add(exec_run)
        session.flush()
        session.add(_run(task.id, "inv-bench", RunPurpose.BENCHMARK_EVALUATION, exec_run.id))
        session.commit()

    _archive(client, workspace_id)
    preview = client.get(f"/api/workspaces/{workspace_id}/purge-preview").json()
    assert preview["benchmark_protected"] is True
    token = preview["purge_token"]

    response = client.post(f"/api/workspaces/{workspace_id}/purge", json={"purge_token": token})
    assert response.status_code == 409
    assert response.json()["code"] == "workspace_purge_benchmark_protected"


def test_grupo_de_benchmark_sem_avaliacao_nao_bloqueia(
    purge_setup: tuple[TestClient, _Clock], tmp_path: Path
) -> None:
    """Precisão: ter `benchmark_group_id` não basta — só a AVALIAÇÃO registrada bloqueia."""
    client, _clock = purge_setup
    workspace_id = _new_workspace(client, tmp_path, "bench-vazio")

    with _factory(client)() as session:
        task = _terminal_task(workspace_id, benchmark_group_id="grupo-z")
        session.add(task)
        session.flush()
        # só um Run de execução, nada de benchmark_evaluation
        session.add(_run(task.id, "inv-so-exec", RunPurpose.EXECUTION, None))
        session.commit()

    _archive(client, workspace_id)
    preview = client.get(f"/api/workspaces/{workspace_id}/purge-preview").json()
    assert preview["benchmark_protected"] is False
    token = preview["purge_token"]

    response = client.post(f"/api/workspaces/{workspace_id}/purge", json={"purge_token": token})
    assert response.status_code == 200  # purga acontece normalmente
    assert client.get(f"/api/workspaces/{workspace_id}").status_code == 404
