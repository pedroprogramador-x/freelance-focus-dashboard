"""E8.4.4 — concorrência, processo real e fechamento: o que precisa valer **sob contenção**.

* claim entre threads e entre **processos**: uma única verificação e um único processo de teste;
* Test Runner **real**: cancelamento externo e prazo matam a árvore e são classificados pela causa;
* binding do Run do Test Runner: resolve durante a execução, não resolve depois;
* fechamento cujo resultado se perdeu (idempotência) e eventos que não duplicam.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings
from app.db.enums import RunAgent, RunStatus, SafetyEventKind
from app.db.models import DevWorkspace, Run, SafetyEvent
from app.orchestrator import execution_manager as em
from app.orchestrator.developer_invocation_contract import BindingOutcome, Finalization
from app.orchestrator.execution_contract import IntegrityVerdict, RunnerOutcome
from app.orchestrator.verify_and_test_contract import VerifyCode, VerifyStatus
from app.tool_executor.contracts import ExecutionWorkspaceRef, RunScope, WorkspaceKind
from app.tool_executor.workspace import WorkspaceUnavailable, bind_workspace
from tests.conftest import API_ROOT
from tests.test_execution_workspace_e8_4_2 import Harness
from tests.test_verify_and_test_e8_4_4 import (
    events_of,
    runner_invocation_id,
    runner_run,
    runner_runs,
    untouched_control,
)
from tests.verify_and_test_support_e8_4_4 import (
    ScriptedRunner,
    ScriptedVerification,
    VRig,
    build_rig,
    exited,
    python_test_config,
    write_in_worktree,
)

pytestmark = pytest.mark.skipif(
    __import__("shutil").which("git") is None, reason="git indisponível no PATH"
)

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    real_connect = socket.socket.connect

    def guarded(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOOPBACK:
            raise AssertionError(f"rede proibida nos testes da E8.4.4: {host!r}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(
        socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rede"))
    )


@pytest.fixture
def h(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    repo_path: Path,
    temp_settings: AppSettings,
    tmp_path: Path,
) -> Harness:
    temp_settings.ensure_worktrees_dir()
    return Harness(
        session=session,
        factory=session_factory,
        workspace=workspace,
        repo=repo_path,
        artifacts=tmp_path / "artifacts",
        settings=temp_settings,
        tmp=tmp_path,
    )


def _authorized(rig: VRig, run_id: str, invocation_id: str) -> None:
    bind_workspace(
        rig.h.bindings.resolver,
        ExecutionWorkspaceRef(
            kind=WorkspaceKind.LOCAL_WORKTREE,
            id=rig.h.workspace.id,
            base_commit=rig.context.base_commit,
        ),
        RunScope(task_id=rig.dev.task.id, run_id=run_id, invocation_id=invocation_id),
    )


def _not_authorized(rig: VRig, run_id: str, invocation_id: str) -> None:
    with pytest.raises(WorkspaceUnavailable):
        _authorized(rig, run_id, invocation_id)


# ---------------------------------------------------------------- claim concorrente


def test_threads_concorrentes_verificam_e_executam_uma_unica_vez(h: Harness) -> None:
    gate = threading.Barrier(4)
    rig = build_rig(h)

    def slow(_request: Any) -> None:
        time.sleep(0.3)  # alarga a janela em que os outros chegam

    rig.verification.before = slow
    results: list[Any] = []
    errors: list[BaseException] = []

    def go() -> None:
        try:
            gate.wait(timeout=30)
            results.append(rig.execute())
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=go) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not errors, errors
    assert len(results) == 4
    completed = [r for r in results if r.status is VerifyStatus.COMPLETED]
    assert len(completed) == 1, [(r.status, r.code) for r in results]
    others = [r for r in results if r.status is not VerifyStatus.COMPLETED]
    assert all(r.status in (VerifyStatus.ALREADY_CLAIMED, VerifyStatus.REFUSED) for r in others), [
        (r.status, r.code) for r in others
    ]
    assert len(rig.verification.verify_calls) == 1  # o snapshot de uso único
    assert len(rig.runner.calls) == 1
    assert len(runner_runs(rig)) == 1


def test_processos_concorrentes_so_um_reivindica(h: Harness, tmp_path: Path) -> None:
    rig = build_rig(h)
    control = h.run(rig.control_run_id)
    go = tmp_path / "go"
    workers = []
    for index in range(4):
        ready = tmp_path / f"ready-{index}"
        spec = {
            "data_dir": str(h.settings.data_dir),
            "database_url": h.settings.database_url,
            "task_id": rig.dev.task.id,
            "control_run_id": rig.control_run_id,
            "developer_run_id": rig.developer.run_id,
            "manifest_id": control.context_manifest_id,
            "invocation_id": runner_invocation_id(rig),
            "base_commit": rig.context.base_commit,
            "worktree_path": rig.context.worktree_path,
            "task_version": rig.context.task_version,
            "attempt_index": rig.context.attempt_index,
            "fix_round": rig.context.fix_round,
            "runner_id": "generic-subprocess-v1",
            "version": "1",
            "ready": str(ready),
            "go": str(go),
        }
        process = subprocess.Popen(  # noqa: S603 — worker de teste, argv literal, sem shell
            [sys.executable, "-m", "tests.claim_worker_e8_4_4", json.dumps(spec)],
            cwd=API_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        workers.append((process, ready))
    deadline = time.monotonic() + 120
    while not all(ready.exists() for _, ready in workers):
        assert time.monotonic() < deadline, "workers não ficaram prontos"
        for process, _ in workers:
            assert process.poll() is None, process.communicate()
        time.sleep(0.05)
    go.write_text("1", encoding="utf-8")

    results: list[dict[str, Any]] = []
    for process, _ in workers:
        out, err = process.communicate(timeout=180)
        assert process.returncode == 0, err
        line = [item for item in out.splitlines() if item.startswith("RESULT ")][-1]
        results.append(json.loads(line.removeprefix("RESULT ")))
    claimed = [r for r in results if r.get("outcome") == "claimed"]
    assert len(claimed) == 1, results
    others = [r for r in results if r.get("outcome") != "claimed"]
    assert all(
        r.get("outcome") == "already" or r.get("error") == "ConcurrentTaskUpdate" for r in others
    ), results
    [only] = runner_runs(rig)
    assert only.id == claimed[0]["run_id"] and only.status is RunStatus.RUNNING
    # Quem chega depois do claim vê o Run existente: nem verifica, nem executa.
    after = rig.execute()
    assert after.status is VerifyStatus.ALREADY_CLAIMED and after.requires_finalization is True
    assert rig.verification.verify_calls == [] and rig.runner.calls == []


# ------------------------------------------------------------------ Test Runner real


def test_cancelamento_externo_mata_a_arvore_e_e_cancelled(h: Harness) -> None:
    config = python_test_config("import time; time.sleep(120)")
    rig = build_rig(h, real_runner=True, test_config=config)
    started = threading.Event()
    original = rig.verification.measure

    def measure_then_arm(request: Any) -> Any:
        out = original(request)
        started.set()
        return out

    rig.verification.measure = measure_then_arm  # type: ignore[method-assign]

    def cancel_soon() -> None:
        started.wait(timeout=60)
        time.sleep(1.0)  # dá tempo ao processo de nascer
        rig.signal.flag = True

    thread = threading.Thread(target=cancel_soon)
    thread.start()
    began = time.monotonic()
    result = rig.execute()
    thread.join(timeout=30)
    assert time.monotonic() - began < 60  # não esperou os 120 s
    assert result.code is VerifyCode.CANCELLED
    assert result.run_status is RunStatus.CANCELLED
    assert result.runner_outcome is RunnerOutcome.CANCELLED
    assert result.process_started is True
    assert result.requires_finalization is False  # árvore confirmada morta
    assert result.binding is BindingOutcome.REVOKED
    [event] = events_of(rig, result.run_id)
    assert event.kind is SafetyEventKind.CANCELLED


def test_prazo_efetivo_mata_a_arvore_e_e_timeout(h: Harness) -> None:
    config = python_test_config("import time; time.sleep(120)")
    rig = build_rig(h, real_runner=True, test_config=config)
    original = rig.verification.measure

    def measure_then_expire(request: Any) -> Any:
        out = original(request)

        def expire() -> None:
            time.sleep(1.0)
            rig.clock.advance(100_000)  # o prazo efetivo da passagem vence

        threading.Thread(target=expire, daemon=True).start()
        return out

    rig.verification.measure = measure_then_expire  # type: ignore[method-assign]
    began = time.monotonic()
    result = rig.execute()
    assert time.monotonic() - began < 60
    assert result.code is VerifyCode.TIMEOUT
    assert result.run_status is RunStatus.TIMEOUT
    assert result.runner_outcome is RunnerOutcome.TIMEOUT
    assert result.requires_finalization is False
    [event] = events_of(rig, result.run_id)
    assert event.kind is SafetyEventKind.TIMEOUT


def test_timeout_da_propria_politica(h: Harness) -> None:
    config = python_test_config("import time; time.sleep(120)", timeout_seconds=1)
    rig = build_rig(h, real_runner=True, test_config=config)
    result = rig.execute()
    assert result.code is VerifyCode.TIMEOUT
    assert result.runner_outcome is RunnerOutcome.TIMEOUT
    assert result.requires_finalization is False


def test_executavel_inexistente_e_falha_tecnica_sem_processo(h: Harness) -> None:
    config = python_test_config(executable="nao-existe-e844-xyz")
    rig = build_rig(h, real_runner=True, test_config=config)
    result = rig.execute()
    assert result.code is VerifyCode.RUNNER_FAILURE
    assert result.run_status is RunStatus.ERROR
    assert result.runner_outcome is RunnerOutcome.TECHNICAL_FAILURE
    assert result.requires_finalization is False
    assert result.integrity is IntegrityVerdict.VERIFIED


# ------------------------------------------------------------------------ binding


def test_binding_do_run_resolve_durante_a_execucao_e_nao_depois(h: Harness) -> None:
    rig = build_rig(h)
    seen: dict[str, Any] = {}

    def probe(request: Any) -> None:
        _authorized(rig, request.run_id, request.invocation_id)
        seen["during"] = True

    rig.runner.hook = probe
    result = rig.execute()
    assert seen == {"during": True}
    assert result.status is VerifyStatus.COMPLETED and result.binding is BindingOutcome.RELEASED
    _not_authorized(rig, str(result.run_id), runner_invocation_id(rig))


def test_binding_e_revogado_em_qualquer_desfecho_que_nao_e_fim_normal(h: Harness) -> None:
    rig = build_rig(h, verification=ScriptedVerification(raises=RuntimeError("x")))
    result = rig.execute()
    assert result.binding is BindingOutcome.REVOKED
    _not_authorized(rig, str(result.run_id), runner_invocation_id(rig))
    # Revogado ≠ apenas removido: registrar de novo é recusado.
    with pytest.raises(Exception):  # noqa: B017 — `BindingRevoked`/`WorkspaceUnavailable`
        rig.dev.workspaces.bind_component_run(
            rig.context, run_id=result.run_id, is_cancelled=lambda: False
        )


def test_testes_falhos_liberam_o_binding_e_nao_o_revogam(h: Harness) -> None:
    rig = build_rig(h, runner=ScriptedRunner(exited(5)))
    result = rig.execute()
    assert result.runner_outcome is RunnerOutcome.FAILED
    assert result.binding is BindingOutcome.RELEASED
    assert rig.bindings.revoked == []


# --------------------------------------------------------------- fechamento idempotente


def test_fechamento_cujo_resultado_se_perdeu_nao_duplica_eventos(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.orchestrator.verify_and_test_contract import (
        FindingFact,
        VerificationReport,
        VerificationStatus,
    )

    report = VerificationReport(
        VerificationStatus.PATH_POLICY_DENIED,
        reason="path_policy_denied",
        findings=(
            FindingFact("a", "added", VerificationStatus.PATH_POLICY_DENIED, "path.r"),
            FindingFact("b", "added", VerificationStatus.PATH_POLICY_DENIED, "path.r"),
        ),
    )
    rig = build_rig(h, verification=ScriptedVerification(report=report))
    real_close = em.close_runner_run
    calls = {"n": 0}

    def commit_then_raise_once(session: Session, task_id: str, **kwargs: Any) -> Any:
        calls["n"] += 1
        out = real_close(session, task_id, **kwargs)
        if calls["n"] == 1:
            raise RuntimeError("conexão perdida depois do COMMIT")
        return out

    monkeypatch.setattr(em, "close_runner_run", commit_then_raise_once)
    result = rig.execute()
    assert calls["n"] == 2
    assert result.finalization is Finalization.ALREADY_FINAL
    assert result.status is VerifyStatus.FAILED and result.run_status is RunStatus.BLOCKED
    assert result.requires_finalization is False
    assert len(events_of(rig, result.run_id)) == 2  # nem 0 nem 4
    assert runner_run(rig).status is RunStatus.BLOCKED


def test_eventos_ficam_no_run_do_test_runner_e_nao_no_de_controle(h: Harness) -> None:
    report = ScriptedVerification(
        report=__import__(
            "app.orchestrator.verify_and_test_contract", fromlist=["x"]
        ).VerificationReport(
            __import__(
                "app.orchestrator.verify_and_test_contract", fromlist=["x"]
            ).VerificationStatus.OUT_OF_WORKTREE_WRITE,
            reason="main_tree_changed",
        )
    )
    rig = build_rig(h, verification=report)
    result = rig.execute()
    assert len(events_of(rig, result.run_id)) == 1
    assert [
        e
        for e in events_of(rig, rig.control_run_id)
        if e.kind is SafetyEventKind.OUT_OF_WORKTREE_WRITE
    ] == []
    untouched_control(rig)


def test_um_run_so_e_fechado_por_quem_o_abriu(h: Harness) -> None:
    """`close_runner_run` só casa um Run `test_runner` aberto: o Developer e o controle ficam."""
    rig = build_rig(h)
    from app.orchestrator.verify_and_test_contract import RunnerClosure

    closure = RunnerClosure(
        status=RunStatus.ERROR,
        finished_at=h.clock(),
        duration_ms=1,
        test_summary=None,
        summary=None,
        error_summary="x",
    )
    with h.factory() as fresh:
        out = em.close_runner_run(
            fresh, rig.dev.task.id, run_id=rig.control_run_id, closure=closure
        )
    assert out.outcome.value == "not_open"
    assert h.run(rig.control_run_id).status is RunStatus.RUNNING
    with h.factory() as fresh:
        out = em.close_runner_run(
            fresh,
            rig.dev.task.id,
            run_id=str(rig.developer.run_id),
            closure=closure,
        )
    assert out.outcome.value == "not_open"
    assert h.run(rig.developer.run_id).status is RunStatus.OK  # type: ignore[arg-type]


def test_todas_as_tentativas_de_tests_vao_para_o_mesmo_unico_run(h: Harness) -> None:
    rig = build_rig(h, developer_hook=lambda dev: write_in_worktree(dev, "src/new.py", "x = 1\n"))
    for _ in range(3):
        rig.execute()
    with h.factory() as fresh:
        n = fresh.scalar(
            select(func.count()).select_from(Run).where(Run.agent == RunAgent.TEST_RUNNER)
        )
        assert n == 1
        runner_ids = [r.id for r in runner_runs(rig)]
        runner_events = fresh.scalar(
            select(func.count()).select_from(SafetyEvent).where(SafetyEvent.run_id.in_(runner_ids))
        )
        assert runner_events == 0
