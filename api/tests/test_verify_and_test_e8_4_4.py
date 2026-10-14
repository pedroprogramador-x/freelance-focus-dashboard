"""E8.4.4 — verificação do diff + execução dos testes (componente interno, não operacional).

Git, SQLite, worktree, binding, admissão, preparação e **invocação do Developer** reais, em
diretórios temporários; a verificação é a da E8.3 e o Test Runner é o real (`python -c …`) ou um
double roteirizado (porta neutra). Nenhuma rede, credencial ou modelo real.

Mapa de aceite: 1 sucesso; 2 testes falhos (sem retry); 3 verificação reprovada antes do processo;
4 `SafetyEvent` preservado e associado ao Run reivindicado; 5 timeout e cancelamento com causa;
6 árvore não encerrada / binding revogado; 7 métricas completas ou integralmente `NULL`; 8 claim
(ordem, concorrência, colisão de identidade, claim incerto); 9 fechamento idempotente e resultado
não finalizado; 10 vazamento; 11 limites desta etapa.
"""

from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings
from app.db.enums import RunAgent, RunPurpose, RunStatus, SafetyEventKind, TaskStatus
from app.db.models import DevWorkspace, Run, SafetyEvent
from app.orchestrator import execution_manager as em
from app.orchestrator.developer_invocation_contract import BindingOutcome, Finalization
from app.orchestrator.execution_contract import (
    COMPONENT_TEST_RUNNER,
    IntegrityVerdict,
    RunnerOutcome,
    component_invocation_id,
)
from app.orchestrator.verify_and_test_contract import (
    FindingFact,
    MetricsReport,
    VerificationReport,
    VerificationStatus,
    VerifyCode,
    VerifyStatus,
)
from app.orchestrator.workspace_contract import PreparationCode
from tests.test_execution_workspace_e8_4_2 import Harness
from tests.verify_and_test_support_e8_4_4 import (
    ScriptedRunner,
    ScriptedVerification,
    VRig,
    build_rig,
    failed,
    python_test_config,
    write_in_worktree,
)

pytestmark = pytest.mark.skipif(
    __import__("shutil").which("git") is None, reason="git indisponível no PATH"
)

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nenhuma conexão a destino que não seja loopback: não há provider nem API nesta suíte."""
    real_connect = socket.socket.connect

    def guarded(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOOPBACK:
            raise AssertionError(f"rede proibida nos testes da E8.4.4: {host!r}")
        return real_connect(self, address)

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("rede proibida nos testes da E8.4.4")

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


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


# ----------------------------------------------------------------------------- leitura


def runner_invocation_id(rig: VRig) -> str:
    return component_invocation_id(rig.control_run_id, COMPONENT_TEST_RUNNER)


def runner_runs(rig: VRig) -> list[Run]:
    with rig.h.factory() as fresh:
        rows = list(fresh.scalars(select(Run).where(Run.agent == RunAgent.TEST_RUNNER)))
        fresh.expunge_all()
        return rows


def runner_run(rig: VRig) -> Run:
    rows = runner_runs(rig)
    assert len(rows) == 1, rows
    return rows[0]


def events_of(rig: VRig, run_id: str | None = None) -> list[SafetyEvent]:
    with rig.h.factory() as fresh:
        query = select(SafetyEvent).where(SafetyEvent.task_id == rig.dev.task.id)
        if run_id is not None:
            query = query.where(SafetyEvent.run_id == run_id)
        rows = list(fresh.scalars(query.order_by(SafetyEvent.created_at, SafetyEvent.id)))
        fresh.expunge_all()
        return rows


def untouched_control(rig: VRig) -> None:
    """A passagem não fecha o Run de controle, não transiciona a task e não grava métricas nele."""
    task = rig.h.task(rig.dev.task.id)
    control = rig.h.run(rig.control_run_id)
    assert task.status is TaskStatus.EXECUTING
    assert task.version == rig.context.task_version
    assert control.status is RunStatus.RUNNING and control.finished_at is None
    assert control.files_changed is None and control.diff_added is None
    assert control.diff_removed is None


def developer_run_untouched(rig: VRig) -> None:
    developer = rig.h.run(rig.developer.run_id)  # type: ignore[arg-type]
    assert developer.status is RunStatus.OK
    assert developer.files_changed is None and developer.test_summary is None


# ------------------------------------------------------------------------- 1. sucesso


def test_sucesso_real_roda_a_politica_aprovada_uma_vez(h: Harness) -> None:
    rig = build_rig(
        h,
        real_runner=True,
        developer_hook=lambda dev: write_in_worktree(
            dev, "src/util.py", "def util():\n    return 2\n"
        ),
    )
    result = rig.execute()

    assert result.status is VerifyStatus.COMPLETED, result
    assert result.code is VerifyCode.TESTS_PASSED
    assert result.run_status is RunStatus.OK
    assert result.process_started is True
    assert result.integrity is IntegrityVerdict.VERIFIED
    assert result.runner_outcome is RunnerOutcome.PASSED
    assert result.verification is VerificationStatus.VERIFIED
    assert result.exit_code == 0
    assert result.binding is BindingOutcome.RELEASED
    assert result.finalization is Finalization.CLOSED
    assert result.requires_finalization is False
    assert result.safety_events == 0

    run = runner_run(rig)
    assert run.id == result.run_id
    assert run.invocation_id == runner_invocation_id(rig)
    assert run.status is RunStatus.OK
    assert run.purpose is RunPurpose.EXECUTION
    assert (run.provider, run.provider_adapter, run.model) == (
        "system",
        "generic-subprocess-v1",
        None,
    )
    assert run.transport.value == "process"
    assert run.finished_at is not None and run.duration_ms is not None
    assert run.test_summary == {
        "framework": "generic-subprocess-v1",
        "exit_code": 0,
        "passed": None,
        "failed": None,
        "skipped": None,
        "duration_ms": run.test_summary["duration_ms"],  # type: ignore[index]
        "output_ref": None,
    }
    assert run.base_commit == rig.context.base_commit
    assert run.worktree_path == rig.context.worktree_path
    assert run.error_summary is None

    assert result.metrics is not None and result.metrics.measured
    assert result.metrics.files_changed == ("src/util.py",)
    assert (result.metrics.diff_added, result.metrics.diff_removed) == (1, 1)
    # As métricas Git ficam no resultado: o Run do Test Runner e o de controle não as afirmam.
    assert run.files_changed is None and run.diff_added is None
    untouched_control(rig)
    developer_run_untouched(rig)
    assert events_of(rig, run.id) == []


def test_sucesso_sem_mudanca_mede_zero_e_roda_os_testes(h: Harness) -> None:
    rig = build_rig(h)
    result = rig.execute()
    assert result.status is VerifyStatus.COMPLETED
    assert result.metrics is not None and result.metrics.measured
    assert result.metrics.files_changed == () and result.metrics.diff_added == 0
    assert len(rig.runner.calls) == 1


# -------------------------------------------------------------- 2. testes falhos, sem retry


def test_testes_falhos_fecham_o_run_com_summary_e_nao_repetem(h: Harness) -> None:
    rig = build_rig(h, real_runner=True, test_config=python_test_config("import sys; sys.exit(3)"))
    result = rig.execute()

    assert result.status is VerifyStatus.FAILED
    assert result.code is VerifyCode.TESTS_FAILED
    assert result.run_status is RunStatus.ERROR
    assert result.runner_outcome is RunnerOutcome.FAILED
    assert result.integrity is IntegrityVerdict.VERIFIED
    assert result.process_started is True
    assert result.exit_code == 3
    assert result.requires_finalization is False
    # Fim normal de uso: o binding é liberado, não revogado.
    assert result.binding is BindingOutcome.RELEASED
    run = runner_run(rig)
    assert run.status is RunStatus.ERROR
    assert run.test_summary is not None and run.test_summary["exit_code"] == 3
    assert run.error_summary == "verify_and_test:tests_failed"
    assert len(runner_runs(rig)) == 1  # nenhum retry automático

    again = rig.execute()
    assert again.status is VerifyStatus.ALREADY_CLAIMED
    assert again.requires_finalization is False
    assert len(runner_runs(rig)) == 1
    untouched_control(rig)


# ------------------------------------------- 3 e 4. verificação reprovada antes do processo


def _tamper_main_tree(rig: VRig) -> None:
    (rig.h.repo / "README.md").write_text("# alterado por outro\n", encoding="utf-8")


def test_arvore_principal_alterada_nao_roda_testes_e_preserva_o_evento(h: Harness) -> None:
    rig = build_rig(h)
    _tamper_main_tree(rig)  # depois do snapshot da preparação, antes da verificação
    result = rig.execute()

    assert result.status is VerifyStatus.FAILED
    assert result.code is VerifyCode.VERIFICATION_VIOLATED
    assert result.run_status is RunStatus.BLOCKED
    assert result.verification is VerificationStatus.OUT_OF_WORKTREE_WRITE
    assert result.integrity is IntegrityVerdict.VIOLATED
    assert result.runner_outcome is RunnerOutcome.NOT_RUN
    assert result.process_started is False
    assert result.binding is BindingOutcome.REVOKED
    assert rig.runner.calls == []
    assert len(rig.verification.verify_calls) == 1
    assert rig.verification.measure_calls == []

    run = runner_run(rig)
    assert run.status is RunStatus.BLOCKED and run.test_summary is None
    [event] = events_of(rig, run.id)
    assert event.kind is SafetyEventKind.OUT_OF_WORKTREE_WRITE
    assert event.run_id == run.id
    # A autoria não é atribuída ao Developer.
    text = f"{event.subject} {event.detail} {event.rule_id}".lower()
    assert "developer" not in text
    assert result.safety_events == 1
    untouched_control(rig)


def test_caminho_negado_pela_politica_e_violacao(h: Harness) -> None:
    rig = build_rig(h, developer_hook=lambda dev: write_in_worktree(dev, ".env", "SEGREDO=1\n"))
    result = rig.execute()
    assert result.verification in (
        VerificationStatus.PATH_POLICY_DENIED,
        VerificationStatus.OUTSIDE_WORKSPACE_CHANGE,
    )
    assert result.integrity is IntegrityVerdict.VIOLATED
    assert result.run_status is RunStatus.BLOCKED
    assert rig.runner.calls == []
    run = runner_run(rig)
    events = events_of(rig, run.id)
    assert events and all(e.kind is SafetyEventKind.PATH_DENIED for e in events)
    assert all(e.run_id == run.id for e in events)


def _unverifiable(failure: str | None = None) -> VerificationReport:
    return VerificationReport(
        VerificationStatus.UNVERIFIABLE, reason=failure or "unverifiable", failure=failure
    )


@pytest.mark.parametrize(
    ("failure", "kind"),
    [
        ("limit_exceeded", SafetyEventKind.LIMIT_EXCEEDED),
        ("deadline_exceeded", SafetyEventKind.TIMEOUT),
        ("cancelled", SafetyEventKind.CANCELLED),
    ],
)
def test_verificacao_incompleta_por_orcamento_nao_roda_e_registra_o_evento(
    h: Harness, failure: str, kind: SafetyEventKind
) -> None:
    rig = build_rig(h, verification=ScriptedVerification(report=_unverifiable(failure)))
    result = rig.execute()
    assert result.integrity is IntegrityVerdict.UNVERIFIABLE
    assert result.runner_outcome is RunnerOutcome.NOT_RUN
    assert result.process_started is False and rig.runner.calls == []
    assert result.status is VerifyStatus.FAILED
    [event] = events_of(rig, result.run_id)
    assert event.kind is kind


def test_verificacao_incompleta_sem_causa_comprovada_nao_gera_evento(h: Harness) -> None:
    rig = build_rig(h, verification=ScriptedVerification(report=_unverifiable("unverifiable")))
    result = rig.execute()
    assert result.integrity is IntegrityVerdict.UNVERIFIABLE
    assert result.run_status is RunStatus.ERROR
    assert result.code is VerifyCode.VERIFICATION_UNVERIFIABLE
    assert events_of(rig, result.run_id) == []


def test_porta_de_verificacao_que_levanta_e_nao_verificavel(h: Harness) -> None:
    rig = build_rig(h, verification=ScriptedVerification(raises=RuntimeError("boom")))
    result = rig.execute()
    assert result.integrity is IntegrityVerdict.UNVERIFIABLE
    assert result.runner_outcome is RunnerOutcome.NOT_RUN
    assert rig.runner.calls == []
    assert result.run_status is RunStatus.ERROR
    assert "boom" not in (runner_run(rig).error_summary or "")


def test_finding_de_politica_vira_um_evento_por_caminho(h: Harness) -> None:
    report = VerificationReport(
        VerificationStatus.PATH_POLICY_DENIED,
        reason="path_policy_denied",
        findings=(
            FindingFact("a.txt", "added", VerificationStatus.PATH_POLICY_DENIED, "path.rule_a"),
            FindingFact("b.txt", "added", VerificationStatus.PATH_POLICY_DENIED, "path.rule_b"),
        ),
    )
    rig = build_rig(h, verification=ScriptedVerification(report=report))
    result = rig.execute()
    events = events_of(rig, result.run_id)
    assert sorted(e.rule_id for e in events) == ["path.rule_a", "path.rule_b"]
    assert result.safety_events == 2


# ---------------------------------------------------------- 5. timeout e cancelamento


def test_prazo_no_processo_e_timeout_e_nao_cancelamento(h: Harness) -> None:
    holder: dict[str, VRig] = {}

    def advance(_request: Any) -> None:
        holder["rig"].clock.advance(10_000)  # o prazo efetivo vence com o processo "em curso"

    def cancelled_by_deadline(request: Any) -> Any:
        assert request.is_cancelled() is True
        return failed("cancelled", dead=True)

    runner = ScriptedRunner(cancelled_by_deadline, hook=advance)
    rig = build_rig(h, runner=runner)
    holder["rig"] = rig
    result = rig.execute()
    assert result.code is VerifyCode.TIMEOUT
    assert result.run_status is RunStatus.TIMEOUT
    assert result.runner_outcome is RunnerOutcome.TIMEOUT
    assert result.requires_finalization is False
    assert result.process_started is True
    [event] = events_of(rig, result.run_id)
    assert event.kind is SafetyEventKind.TIMEOUT


def test_cancelamento_externo_e_cancelled_e_nao_timeout(h: Harness) -> None:
    holder: dict[str, VRig] = {}

    def cancel(_request: Any) -> None:
        holder["rig"].signal.flag = True

    def cancelled(request: Any) -> Any:
        assert request.is_cancelled() is True
        return failed("cancelled", dead=True)

    rig = build_rig(h, runner=ScriptedRunner(cancelled, hook=cancel))
    holder["rig"] = rig
    result = rig.execute()
    assert result.code is VerifyCode.CANCELLED
    assert result.run_status is RunStatus.CANCELLED
    assert result.runner_outcome is RunnerOutcome.CANCELLED
    [event] = events_of(rig, result.run_id)
    assert event.kind is SafetyEventKind.CANCELLED


def test_cancelado_antes_de_iniciar_nao_cria_run(h: Harness) -> None:
    rig = build_rig(h)
    rig.signal.flag = True
    result = rig.execute()
    assert result.status is VerifyStatus.REFUSED
    assert result.code is VerifyCode.CANCELLED_BEFORE_START
    assert runner_runs(rig) == []
    assert rig.verification.verify_calls == [] and rig.runner.calls == []


def test_prazo_esgotado_antes_do_claim_nao_cria_run(h: Harness) -> None:
    rig = build_rig(h)
    with h.factory() as fresh:
        control = fresh.get(Run, rig.control_run_id)
        assert control is not None
    from datetime import timedelta

    h.clock.fixed = control.started_at + timedelta(days=2)
    result = rig.execute()
    assert result.status is VerifyStatus.REFUSED
    assert result.code is VerifyCode.DEADLINE_EXHAUSTED
    assert runner_runs(rig) == []


# -------------------------------------------- 6. árvore não encerrada / binding revogado


def test_arvore_nao_confirmada_exige_finalizacao_e_revoga(h: Harness) -> None:
    rig = build_rig(h, runner=ScriptedRunner(failed("supervision_failed", dead=False)))
    result = rig.execute()
    assert result.status is VerifyStatus.UNFINALIZED
    assert result.code is VerifyCode.SUPERVISION_FAILED
    assert result.requires_finalization is True
    assert result.runner_outcome is RunnerOutcome.TECHNICAL_FAILURE
    assert result.binding is BindingOutcome.REVOKED
    assert runner_run(rig).status is RunStatus.ERROR  # o Run fecha; a pendência é do fato


def test_timeout_sem_arvore_confirmada_exige_finalizacao(h: Harness) -> None:
    rig = build_rig(h, runner=ScriptedRunner(failed("timeout", dead=False)))
    result = rig.execute()
    assert result.run_status is RunStatus.TIMEOUT
    assert result.requires_finalization is True
    assert result.status is VerifyStatus.UNFINALIZED


def test_binding_que_deixa_de_valer_antes_dos_testes_nao_executa(h: Harness) -> None:
    # 1ª conferência: após o bind; 2ª: na última fronteira → falha aqui.
    rig = build_rig(h, fail_binding_from=2)
    result = rig.execute()
    assert result.code is VerifyCode.BINDING_REVOKED
    assert result.run_status is RunStatus.BLOCKED
    assert result.integrity is IntegrityVerdict.VERIFIED  # a verificação passou
    assert result.runner_outcome is RunnerOutcome.TECHNICAL_FAILURE
    assert result.process_started is False and rig.runner.calls == []
    assert result.binding is BindingOutcome.REVOKED


def test_binding_revogado_ao_fim_nunca_fecha_ok(h: Harness) -> None:
    rig = build_rig(h, fail_binding_from=3)  # bind, fronteira, e só a conferência final falha
    result = rig.execute()
    assert result.process_started is True
    assert result.run_status is RunStatus.BLOCKED
    assert result.code is VerifyCode.BINDING_REVOKED
    assert result.status is not VerifyStatus.COMPLETED
    assert len(rig.runner.calls) == 1


def test_binding_recusado_no_registro_nao_verifica_nem_executa(h: Harness) -> None:
    rig = build_rig(h, fail_binding_from=1)  # a conferência logo após o bind já falha
    result = rig.execute()
    assert result.code is VerifyCode.BINDING_REVOKED
    assert result.integrity is IntegrityVerdict.UNVERIFIABLE
    assert rig.verification.verify_calls == [] and rig.runner.calls == []
    assert result.binding is BindingOutcome.REVOKED


# ----------------------------------------------------------------------- 7. métricas


def test_metricas_ausentes_nao_impedem_os_testes_mas_nunca_viram_zero(h: Harness) -> None:
    rig = build_rig(
        h,
        developer_hook=lambda dev: write_in_worktree(dev, "src/blob.bin", b"\x00\x01\x02"),
    )
    result = rig.execute()
    assert result.status is VerifyStatus.COMPLETED
    assert result.metrics is not None and not result.metrics.measured
    assert result.metrics.failure == "binary"
    assert result.metrics.diff_added is None and result.metrics.files_changed is None


def test_conteudo_diferente_do_verificado_nao_roda_testes(h: Harness) -> None:
    rig = build_rig(
        h,
        developer_hook=lambda dev: write_in_worktree(
            dev, "src/util.py", "def util():\n    return 9\n"
        ),
    )

    def swap(request: Any) -> None:  # entre a verificação e a medição
        write_in_worktree(rig.dev, "src/util.py", "def util():\n    return 10\n")

    rig.verification.before_measure = swap
    result = rig.execute()
    assert result.code is VerifyCode.DIFF_CHANGED_SINCE_VERIFIED
    assert result.integrity is IntegrityVerdict.UNVERIFIABLE
    assert result.process_started is False and rig.runner.calls == []
    assert result.run_status is RunStatus.ERROR


def test_porta_de_medicao_que_levanta_nao_prova_a_integridade_e_bloqueia(h: Harness) -> None:
    rig = build_rig(h)
    rig.verification.measure_raises = RuntimeError("x")
    result = rig.execute()
    assert result.status is not VerifyStatus.COMPLETED
    assert result.metrics is not None and result.metrics.failure == "measure_failed"
    assert result.integrity is IntegrityVerdict.UNVERIFIABLE
    assert result.process_started is False and rig.runner.calls == []


def test_metricas_parciais_sao_recusadas_pelo_contrato() -> None:
    with pytest.raises(ValueError):
        MetricsReport(files_changed=("a",), diff_added=None, diff_removed=1)


# ----------------------------------------------------------- 8. claim e identidade


def test_claim_acontece_antes_de_consumir_o_snapshot(h: Harness) -> None:
    observed: dict[str, Any] = {}

    def probe(request: Any) -> None:
        with h.factory() as fresh:
            rows = list(fresh.scalars(select(Run).where(Run.agent == RunAgent.TEST_RUNNER)))
            observed["rows"] = [(r.status, r.finished_at) for r in rows]

    rig = build_rig(h, verification=None)
    rig.verification.before = probe
    rig.execute()
    assert observed["rows"] == [(RunStatus.RUNNING, None)]  # já durável e ainda aberto


def test_identidade_do_run_do_developer_vem_do_banco_e_nao_do_dto(h: Harness) -> None:
    rig = build_rig(h)
    forged = rig.developer.__class__(
        rig.developer.status,
        rig.developer.code,
        run_id=rig.control_run_id,  # o id do Run de controle disfarçado de Developer
        run_status=RunStatus.OK,
        developer_outcome=rig.developer.developer_outcome,
        binding=rig.developer.binding,
        finalization=rig.developer.finalization,
    )
    result = rig.execute(developer=forged)
    assert result.status is VerifyStatus.REFUSED
    assert result.code is VerifyCode.REFUSED_FACTS  # recusado na leitura de fatos, antes do claim
    assert result.fact_code is PreparationCode.RUN_MISMATCH
    assert runner_runs(rig) == []
    assert rig.verification.verify_calls == [] and rig.runner.calls == []


def test_developer_que_nao_fechou_ok_no_banco_recusa_mesmo_com_dto_ok(h: Harness) -> None:
    from app.orchestrator.developer_invocation_contract import (
        DeveloperInvocationResult,
        InvocationCode,
        InvocationStatus,
        ReportedStatus,
    )

    rig = build_rig(h, developer_report={"status": ReportedStatus.ERROR, "error_summary": "x"})
    assert rig.developer.run_status is RunStatus.ERROR  # o banco diz: o Developer falhou
    forged = DeveloperInvocationResult(
        InvocationStatus.COMPLETED,
        InvocationCode.COMPLETED,
        run_id=rig.developer.run_id,
        run_status=RunStatus.OK,  # o DTO mente
        developer_outcome=rig.developer.developer_outcome,
        binding=BindingOutcome.RELEASED,
        finalization=Finalization.CLOSED,
    )
    result = rig.execute(developer=forged)
    assert result.status is VerifyStatus.REFUSED
    assert result.fact_code is PreparationCode.RUN_MISMATCH
    assert runner_runs(rig) == []
    assert rig.verification.verify_calls == [] and rig.runner.calls == []


def test_dto_com_pendencia_ou_violacao_recusa(h: Harness) -> None:
    rig = build_rig(h)
    from app.orchestrator.developer_invocation_contract import InvocationCode, InvocationStatus

    pendente = rig.developer.__class__(
        InvocationStatus.UNFINALIZED,
        InvocationCode.COMPLETED,
        run_id=rig.developer.run_id,
        run_status=RunStatus.OK,
        requires_finalization=True,
    )
    assert rig.execute(developer=pendente).code is VerifyCode.DEVELOPER_NOT_COMPLETED
    violado = rig.developer.__class__(
        InvocationStatus.FAILED,
        InvocationCode.CRITICAL_VIOLATION,
        run_id=rig.developer.run_id,
        run_status=RunStatus.BLOCKED,
        integrity_violation=True,
    )
    assert rig.execute(developer=violado).code is VerifyCode.DEVELOPER_NOT_COMPLETED
    assert runner_runs(rig) == []


def test_dto_que_nao_e_ok_recusa_sem_ler_o_banco(h: Harness) -> None:
    rig = build_rig(h)
    result = rig.service.execute(rig.context, object(), is_cancelled=rig.signal)
    assert result.code is VerifyCode.DEVELOPER_NOT_COMPLETED
    assert runner_runs(rig) == []


def test_segunda_chamada_nao_verifica_nem_executa(h: Harness) -> None:
    rig = build_rig(h)
    first = rig.execute()
    assert first.status is VerifyStatus.COMPLETED
    second = rig.execute()
    assert second.status is VerifyStatus.ALREADY_CLAIMED
    assert second.run_status is RunStatus.OK
    assert len(rig.verification.verify_calls) == 1  # o snapshot é de uso único
    assert len(rig.runner.calls) == 1
    assert len(runner_runs(rig)) == 1


def test_claim_com_run_de_outro_componente_e_colisao_e_nao_e_adotado(h: Harness) -> None:
    rig = build_rig(h)
    # Um Run `developer` ocupando a chave do Test Runner (mesma task), para forçar a colisão.
    from datetime import UTC, datetime

    from app.db.enums import FilesReadSource, RunTransport, TokenSource

    with h.factory() as fresh:
        fresh.add(
            Run(
                invocation_id=runner_invocation_id(rig),
                task_id=rig.dev.task.id,
                agent=RunAgent.DEVELOPER,
                purpose=RunPurpose.EXECUTION,
                attempt_index=rig.context.attempt_index,
                fix_round=rig.context.fix_round,
                provider="x",
                provider_adapter="x",
                transport=RunTransport.API,
                status=RunStatus.RUNNING,
                started_at=datetime.now(UTC),
                token_source=TokenSource.UNAVAILABLE,
                files_read_source=FilesReadSource.UNAVAILABLE,
                base_commit=rig.context.base_commit,
            )
        )
        fresh.commit()
    result = rig.execute()
    assert result.status is VerifyStatus.REFUSED
    assert result.code is VerifyCode.CLAIM_COLLISION
    assert result.run_id is None and result.run_status is None  # nada do Run alheio vaza
    assert rig.verification.verify_calls == [] and rig.runner.calls == []
    assert runner_runs(rig) == []  # nenhum Run test_runner foi criado


def test_claim_incerto_resolve_por_identidade_e_nunca_executa(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build_rig(h)
    real_open = em.open_runner_run

    def commit_then_raise(session: Session, task_id: str, *, spec: Any) -> Any:
        real_open(session, task_id, spec=spec)
        raise RuntimeError("conexão perdida depois do COMMIT")

    monkeypatch.setattr(em, "open_runner_run", commit_then_raise)
    result = rig.execute()
    assert result.status is VerifyStatus.ALREADY_CLAIMED
    assert result.requires_finalization is True  # o Run ficou `running`
    assert rig.verification.verify_calls == [] and rig.runner.calls == []
    assert runner_run(rig).status is RunStatus.RUNNING


def test_claim_incerto_sem_run_e_recusa(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = build_rig(h)

    def boom(session: Session, task_id: str, *, spec: Any) -> Any:
        raise RuntimeError("falhou antes do INSERT")

    monkeypatch.setattr(em, "open_runner_run", boom)
    result = rig.execute()
    assert result.status is VerifyStatus.REFUSED and result.code is VerifyCode.CLAIM_FAILED
    assert rig.runner.calls == [] and runner_runs(rig) == []


def test_claim_recusado_sob_o_lock_desfaz_o_insert(h: Harness) -> None:
    rig = build_rig(h)
    with h.factory() as fresh:
        from sqlalchemy import update

        from app.db.models import WorkspaceTask

        fresh.execute(
            update(WorkspaceTask)
            .where(WorkspaceTask.id == rig.dev.task.id)
            .values(cancel_requested=True)
        )
        fresh.commit()
    result = rig.execute()
    assert result.status is VerifyStatus.REFUSED
    assert runner_runs(rig) == []
    assert rig.verification.verify_calls == []


# ------------------------------------------------------------ 9. fechamento e finalização


def test_fechamento_que_falha_duas_vezes_deixa_o_run_aberto_sem_repetir_o_processo(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build_rig(h)
    calls = {"n": 0}

    def boom(session: Session, task_id: str, *, run_id: str, closure: Any) -> Any:
        calls["n"] += 1
        raise RuntimeError("disco cheio")

    monkeypatch.setattr(em, "close_runner_run", boom)
    result = rig.execute()
    assert calls["n"] == 2
    assert result.status is VerifyStatus.UNFINALIZED
    assert result.finalization is Finalization.FAILED
    assert result.requires_finalization is True
    assert result.process_started is True and len(rig.runner.calls) == 1
    assert runner_run(rig).status is RunStatus.RUNNING
    monkeypatch.undo()
    again = rig.execute()
    assert again.status is VerifyStatus.ALREADY_CLAIMED and again.requires_finalization is True
    assert len(rig.runner.calls) == 1


def test_fechamento_e_idempotente_e_nao_duplica_eventos(h: Harness) -> None:
    report = VerificationReport(
        VerificationStatus.PATH_POLICY_DENIED,
        reason="path_policy_denied",
        findings=(FindingFact("a", "added", VerificationStatus.PATH_POLICY_DENIED, "path.r"),),
    )
    rig = build_rig(h, verification=ScriptedVerification(report=report))
    result = rig.execute()
    assert result.safety_events == 1
    from app.orchestrator.verify_and_test_contract import RunnerClosure

    closure = RunnerClosure(
        status=RunStatus.ERROR,
        finished_at=h.clock(),
        duration_ms=1,
        test_summary=None,
        summary=None,
        error_summary="x",
        events=(),
    )
    with h.factory() as fresh:
        second = em.close_runner_run(
            fresh, rig.dev.task.id, run_id=str(result.run_id), closure=closure
        )
    assert second.outcome.value == "not_open"
    assert second.existing_status is RunStatus.BLOCKED
    assert len(events_of(rig, result.run_id)) == 1
    assert runner_run(rig).status is RunStatus.BLOCKED  # nunca reescrito


def test_excecao_do_runner_fecha_error_e_exige_finalizacao(h: Harness) -> None:
    rig = build_rig(h, runner=ScriptedRunner(raises=RuntimeError("segredo-do-erro")))
    result = rig.execute()
    assert result.status is VerifyStatus.UNFINALIZED
    assert result.code is VerifyCode.INTERNAL_ERROR
    assert result.requires_finalization is True
    assert result.binding is BindingOutcome.REVOKED
    run = runner_run(rig)
    assert run.status is RunStatus.ERROR
    assert "segredo-do-erro" not in (run.error_summary or "")


def test_interrupcao_fecha_o_run_revoga_e_propaga(h: Harness) -> None:
    rig = build_rig(h, runner=ScriptedRunner(raises=KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        rig.execute()
    run = runner_run(rig)
    assert run.status is RunStatus.INTERRUPTED
    assert rig.bindings.revoked  # a autorização foi negada


def test_runner_com_identidade_diferente_da_politica_e_recusado(h: Harness) -> None:
    from app.orchestrator.verify_and_test_contract import RunnerIdentity

    other = RunnerIdentity(runner_id="outro-runner-v9", version="1")
    rig = build_rig(h, runner=ScriptedRunner(identity=other))
    result = rig.execute()
    assert result.code is VerifyCode.RUNNER_UNSUPPORTED
    assert runner_runs(rig) == []


# -------------------------------------------------------------------- 10. vazamento


def test_saida_do_processo_nao_chega_ao_banco_nem_ao_resultado(h: Harness) -> None:
    secret = "E844_SYNTHETIC_SECRET_DO_NOT_LEAK"
    code = f"import sys; print('{secret}'); sys.stderr.write('{secret}'); sys.exit(1)"
    rig = build_rig(h, real_runner=True, test_config=python_test_config(code))
    result = rig.execute()
    assert result.runner_outcome is RunnerOutcome.FAILED
    with h.factory() as fresh:
        run = fresh.scalar(select(Run).where(Run.agent == RunAgent.TEST_RUNNER))
        assert run is not None
        blob = " ".join(
            str(v)
            for v in (
                run.summary,
                run.error_summary,
                run.test_summary,
                run.log_ref,
                run.worktree_path and "",
            )
        )
        assert secret not in blob
        events = " ".join(f"{e.subject} {e.detail}" for e in fresh.scalars(select(SafetyEvent)))
        assert secret not in events
    assert secret not in repr(result)
    assert run.log_ref is None and run.test_summary["output_ref"] is None  # type: ignore[index]


# ---------------------------------------------------------------- 11. limites desta etapa


def test_nada_transiciona_a_task_nem_fecha_o_controle(h: Harness) -> None:
    rig = build_rig(h)
    rig.execute()
    untouched_control(rig)
    with h.factory() as fresh:
        total = fresh.scalar(select(func.count()).select_from(Run))
        assert total == 3  # controle + Developer + Test Runner


def test_start_execution_continua_no_limite() -> None:
    import inspect

    from app.orchestrator import start_execution

    assert "NotImplementedError" in inspect.getsource(start_execution)


_ = time


# ----------------------------------------- contexto em memória × banco; fronteira do prazo


@pytest.mark.parametrize(
    ("field", "value", "fact"),
    [
        ("workspace_id", "outro-workspace", PreparationCode.RUN_MISMATCH),
        ("base_commit", "f" * 40, PreparationCode.RUN_MISMATCH),
        ("attempt_index", 7, PreparationCode.RUN_MISMATCH),
        ("fix_round", 3, PreparationCode.RUN_MISMATCH),
        ("task_version", 999, PreparationCode.TASK_VERSION_CHANGED),
        ("worktree_path", "/outra/worktree", PreparationCode.FINAL_CHECK_FAILED),
    ],
)
def test_contexto_em_memoria_divergente_do_banco_e_recusado_antes_do_claim(
    h: Harness, field: str, value: Any, fact: PreparationCode
) -> None:
    from dataclasses import replace

    rig = build_rig(h)
    stale = replace(rig.context, **{field: value})
    result = rig.service.execute(stale, rig.developer, is_cancelled=rig.signal)
    assert result.status is VerifyStatus.REFUSED
    assert result.code is VerifyCode.REFUSED_FACTS
    assert result.fact_code is fact
    assert runner_runs(rig) == []
    assert rig.verification.verify_calls == [] and rig.runner.calls == []


def test_prazo_quase_esgotado_depois_da_medicao_nao_executa(h: Harness) -> None:
    """O sinal ainda não expirou, mas resta menos de 1 s: o processo não deve nascer."""
    rig = build_rig(h)

    def squeeze(request: Any) -> None:
        signal = request.is_cancelled
        rig.clock.advance(signal.remaining_s() - 0.4)

    rig.verification.after_measure = squeeze  # depois da medição, antes da última fronteira
    result = rig.execute()
    assert result.code is VerifyCode.TIMEOUT
    assert result.integrity is IntegrityVerdict.VERIFIED
    assert result.runner_outcome is RunnerOutcome.TIMEOUT
    assert result.process_started is False and rig.runner.calls == []


# ------------------------------- o claim e a leitura de fatos conferem o Developer no banco


def _spec(rig: VRig, **changes: Any) -> Any:
    from datetime import UTC, datetime

    from app.orchestrator.verify_and_test_contract import RunnerIdentity, RunnerRunSpec

    control = rig.h.run(rig.control_run_id)
    base: dict[str, Any] = {
        "invocation_id": runner_invocation_id(rig),
        "control_run_id": rig.control_run_id,
        "developer_run_id": rig.developer.run_id,
        "context_manifest_id": control.context_manifest_id,
        "identity": RunnerIdentity("generic-subprocess-v1", "1"),
        "base_commit": rig.context.base_commit,
        "worktree_path": rig.context.worktree_path,
        "expected_task_version": rig.context.task_version,
        "attempt_index": rig.context.attempt_index,
        "fix_round": rig.context.fix_round,
        "started_at": datetime.now(UTC),
    }
    return RunnerRunSpec(**{**base, **changes})


def _other_developer_run(rig: VRig, **changes: Any) -> str:
    """Um segundo Run `developer` já final (`ok`) da mesma tentativa, com outra chave."""
    from datetime import UTC, datetime

    from app.db.enums import FilesReadSource, RunTransport, TokenSource

    control = rig.h.run(rig.control_run_id)
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "invocation_id": "outra-chave-do-developer",
        "task_id": rig.dev.task.id,
        "context_manifest_id": control.context_manifest_id,
        "agent": RunAgent.DEVELOPER,
        "purpose": RunPurpose.EXECUTION,
        "attempt_index": rig.context.attempt_index,
        "fix_round": rig.context.fix_round,
        "provider": "x",
        "provider_adapter": "x",
        "transport": RunTransport.API,
        "status": RunStatus.OK,
        "started_at": now,
        "finished_at": now,
        "duration_ms": 1,
        "token_source": TokenSource.UNAVAILABLE,
        "files_read_source": FilesReadSource.UNAVAILABLE,
        "base_commit": rig.context.base_commit,
        "worktree_path": rig.context.worktree_path,
    }
    row = Run(**{**values, **changes})
    with rig.h.factory() as fresh:
        fresh.add(row)
        fresh.commit()
        return row.id


def test_claim_confere_o_developer_sob_o_lock_e_desfaz_o_insert(h: Harness) -> None:
    rig = build_rig(h)
    forged = [
        {"developer_run_id": rig.control_run_id},  # o Run de controle disfarçado
        {"developer_run_id": "00000000-0000-4000-8000-000000000000"},  # inexistente
        {"developer_run_id": _other_developer_run(rig)},  # Developer `ok`, mas de outra chave
    ]
    for change in forged:
        with h.factory() as fresh:
            claim = em.open_runner_run(fresh, rig.dev.task.id, spec=_spec(rig, **change))
        assert claim.outcome.value == "refused", change
        assert claim.code is PreparationCode.RUN_MISMATCH, change
        assert runner_runs(rig) == [], change  # o INSERT foi desfeito
    with h.factory() as fresh:
        ok = em.open_runner_run(fresh, rig.dev.task.id, spec=_spec(rig))
    assert ok.outcome.value == "claimed"


def test_fatos_recusam_developer_de_outra_chave_mesmo_sendo_ok_e_da_tentativa(
    h: Harness,
) -> None:
    rig = build_rig(h)
    other = _other_developer_run(rig)
    with h.factory() as fresh:
        facts = em.read_verify_facts(
            fresh,
            rig.dev.task.id,
            control_run_id=rig.control_run_id,
            developer_run_id=other,
            developer_binding_resolver=__import__(
                "tests.admission_support_e8_4_1", fromlist=["RESOLVER"]
            ).RESOLVER,
        )
    assert facts.code is PreparationCode.RUN_MISMATCH


def test_claim_recusa_task_que_nao_esta_mais_executando_e_run_de_controle_fechado(
    h: Harness,
) -> None:
    from sqlalchemy import text

    rig = build_rig(h)
    with h.factory() as fresh:
        fresh.execute(
            text("UPDATE workspace_task SET status = 'cancelled', phase = NULL WHERE id = :id"),
            {"id": rig.dev.task.id},
        )
        fresh.commit()
    with h.factory() as fresh:
        claim = em.open_runner_run(fresh, rig.dev.task.id, spec=_spec(rig))
    assert claim.outcome.value == "refused" and claim.code is PreparationCode.TASK_NOT_EXECUTING
    assert runner_runs(rig) == []


def test_claim_recusa_cancelamento_pedido_sob_o_lock(h: Harness) -> None:
    """A guarda do claim vale por si: chamada direta, sem a leitura de fatos antes."""
    from sqlalchemy import update

    from app.db.models import WorkspaceTask

    rig = build_rig(h)
    with h.factory() as fresh:
        fresh.execute(
            update(WorkspaceTask)
            .where(WorkspaceTask.id == rig.dev.task.id)
            .values(cancel_requested=True)
        )
        fresh.commit()
    with h.factory() as fresh:
        claim = em.open_runner_run(fresh, rig.dev.task.id, spec=_spec(rig))
    assert claim.outcome.value == "refused" and claim.code is PreparationCode.CANCEL_REQUESTED
    assert runner_runs(rig) == []


class _NoRelease:
    """Delega tudo, mas nem liberar nem revogar o binding se prova."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def release_binding(self, token: object) -> bool:
        return False

    def revoke_binding(self, token: object) -> bool:
        return False


def test_binding_que_nao_se_prova_liberado_nem_revogado_nao_completa(h: Harness) -> None:
    rig = build_rig(h)
    rig.bindings.inner = _NoRelease(rig.bindings.inner)
    result = rig.execute()
    assert result.run_status is RunStatus.OK  # o Run fechou ok…
    assert result.binding is BindingOutcome.RELEASE_FAILED  # …mas o binding não se provou
    assert result.status is VerifyStatus.UNFINALIZED  # logo a passagem NÃO está completa
    assert result.requires_finalization is True
