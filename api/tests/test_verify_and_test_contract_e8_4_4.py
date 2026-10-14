"""E8.4.4 — contrato puro de "Tests and Results": decisões, eventos, sinal com causa e claim.

Sem banco, sem Git, sem processo: tudo é função pura sobre dados neutros. As tabelas são
**exaustivas** nos eixos que decidem o estado (verificação × falha do runner × causa × árvore ×
binding), porque é aqui que o fail-closed mora.
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.db.enums import RunStatus, SafetyEventKind
from app.orchestrator.developer_invocation_contract import (
    BindingOutcome,
    ExistingRunFacts,
    Finalization,
)
from app.orchestrator.execution_contract import (
    ExecutionFacts,
    IntegrityVerdict,
    RunnerOutcome,
    classify_execution,
    component_invocation_id,
)
from app.orchestrator.verify_and_test_contract import (
    MAX_FINDING_EVENTS,
    AttemptSignal,
    FindingFact,
    MetricsReport,
    RunnerFailureCode,
    RunnerIdentity,
    RunnerReport,
    RunnerRunSpec,
    StopCause,
    VerificationReport,
    VerificationStatus,
    VerifyAndTestResult,
    VerifyCode,
    VerifyDecision,
    VerifyStatus,
    decide_after_runner,
    decide_changed_since_verified,
    decide_not_verified,
    decide_stop_after_verification,
    decide_verification_failure,
    integrity_of,
    run_error_summary,
    runner_claim_compatible,
    runner_events,
    summary_of_report,
    verification_events,
)

ID = RunnerIdentity("generic-subprocess-v1", "1")
CAUSES = [None, StopCause.DEADLINE, StopCause.CANCEL]
VS = VerificationStatus


def exited(code: int, **changes: Any) -> RunnerReport:
    return RunnerReport(
        identity=ID, exit_code=code, framework="generic-subprocess-v1", duration_ms=1, **changes
    )


def failure(code: RunnerFailureCode, dead: bool | None = None) -> RunnerReport:
    return RunnerReport(identity=ID, failure=code, tree_confirmed_dead=dead)


# ------------------------------------------------------------------ integridade (D5)


@pytest.mark.parametrize(
    ("status", "verdict"),
    [
        (VS.VERIFIED, IntegrityVerdict.VERIFIED),
        (VS.OUT_OF_WORKTREE_WRITE, IntegrityVerdict.VIOLATED),
        (VS.INTEGRITY_VIOLATION, IntegrityVerdict.VIOLATED),
        (VS.OUTSIDE_WORKSPACE_CHANGE, IntegrityVerdict.VIOLATED),
        (VS.PATH_POLICY_DENIED, IntegrityVerdict.VIOLATED),
        (VS.UNVERIFIABLE, IntegrityVerdict.UNVERIFIABLE),
    ],
)
def test_classificacao_de_integridade_aprovada(
    status: VerificationStatus, verdict: IntegrityVerdict
) -> None:
    assert integrity_of(status) is verdict


def test_todo_status_de_verificacao_esta_classificado() -> None:
    for status in VerificationStatus:
        integrity_of(status)


@pytest.mark.parametrize(
    "status", [s for s in VerificationStatus if s is not VerificationStatus.VERIFIED]
)
@pytest.mark.parametrize("cause", CAUSES)
def test_verificacao_que_nao_liberou_nunca_vira_sucesso_nem_roda_testes(
    status: VerificationStatus, cause: StopCause | None
) -> None:
    report = VerificationReport(status, reason="x_reason")
    decision = decide_verification_failure(report, cause)
    assert decision.run_status is not RunStatus.OK
    assert decision.runner_outcome is RunnerOutcome.NOT_RUN
    assert decision.normal_end is False and decision.requires_finalization is False
    assert decision.integrity is integrity_of(status)
    assert decision.integrity is not IntegrityVerdict.VERIFIED
    if integrity_of(status) is IntegrityVerdict.VIOLATED:
        assert (decision.run_status, decision.code) == (
            RunStatus.BLOCKED,
            VerifyCode.VERIFICATION_VIOLATED,
        )


@pytest.mark.parametrize(
    ("cause", "failure_code", "status", "code"),
    [
        (StopCause.DEADLINE, None, RunStatus.TIMEOUT, VerifyCode.TIMEOUT),
        (StopCause.CANCEL, None, RunStatus.CANCELLED, VerifyCode.CANCELLED),
        (None, "deadline_exceeded", RunStatus.TIMEOUT, VerifyCode.TIMEOUT),
        (None, "cancelled", RunStatus.CANCELLED, VerifyCode.CANCELLED),
        (None, "limit_exceeded", RunStatus.ERROR, VerifyCode.VERIFICATION_UNVERIFIABLE),
        (None, None, RunStatus.ERROR, VerifyCode.VERIFICATION_UNVERIFIABLE),
    ],
)
def test_nao_verificavel_segue_a_causa_mas_a_integridade_fica_unverifiable(
    cause: StopCause | None, failure_code: str | None, status: RunStatus, code: VerifyCode
) -> None:
    report = VerificationReport(VS.UNVERIFIABLE, reason="u", failure=failure_code)
    decision = decide_verification_failure(report, cause)
    assert (decision.run_status, decision.code) == (status, code)
    assert decision.integrity is IntegrityVerdict.UNVERIFIABLE
    assert decision.runner_outcome is RunnerOutcome.NOT_RUN
    assert decide_verification_failure(None, cause).integrity is IntegrityVerdict.UNVERIFIABLE


def test_violacao_comprovada_vence_a_causa_de_parada() -> None:
    report = VerificationReport(VS.OUT_OF_WORKTREE_WRITE, reason="main_tree_changed")
    for cause in CAUSES:
        assert decide_verification_failure(report, cause).run_status is RunStatus.BLOCKED


# ----------------------------------------------------------- decisão depois do runner


@pytest.mark.parametrize("cause", CAUSES)
@pytest.mark.parametrize("dead", [True, False, None])
@pytest.mark.parametrize("binding", [True, False])
def test_tabela_exaustiva_do_runner(
    cause: StopCause | None, dead: bool | None, binding: bool
) -> None:
    reports: list[RunnerReport | None] = [None]
    reports += [exited(code, tree_confirmed_dead=dead) for code in (0, 1, 127, -9)]
    reports += [failure(code, dead) for code in RunnerFailureCode]
    for report in reports:
        d = decide_after_runner(report, cause=cause, binding_verified=binding)
        assert d.integrity is IntegrityVerdict.VERIFIED
        # `ok` só com testes aprovados, binding provado, árvore não contraditória, nada pendente.
        if d.run_status is RunStatus.OK:
            assert report is not None and report.exit_code == 0 and binding
            assert dead is not False and not d.requires_finalization and d.normal_end
            assert d.runner_outcome is RunnerOutcome.PASSED
        if d.normal_end:
            assert d.runner_outcome in (RunnerOutcome.PASSED, RunnerOutcome.FAILED)
            assert binding and dead is not False
        if report is None:
            assert d.requires_finalization and d.run_status is RunStatus.ERROR
        if report is not None and report.failure in (
            RunnerFailureCode.TIMEOUT,
            RunnerFailureCode.SUPERVISION_FAILED,
        ):
            assert d.requires_finalization == (dead is not True)
        assert d.runner_outcome is not RunnerOutcome.NOT_RUN  # o processo foi chamado


def test_exit_zero_e_exit_diferente_de_zero() -> None:
    ok = decide_after_runner(exited(0), cause=None, binding_verified=True)
    assert (ok.run_status, ok.code, ok.runner_outcome) == (
        RunStatus.OK,
        VerifyCode.TESTS_PASSED,
        RunnerOutcome.PASSED,
    )
    bad = decide_after_runner(exited(1), cause=None, binding_verified=True)
    assert (bad.run_status, bad.code, bad.runner_outcome, bad.normal_end) == (
        RunStatus.ERROR,
        VerifyCode.TESTS_FAILED,
        RunnerOutcome.FAILED,
        True,
    )


def test_deadline_e_timeout_e_cancelamento_externo_e_cancelled() -> None:
    timeout = decide_after_runner(
        failure(RunnerFailureCode.CANCELLED, True), cause=StopCause.DEADLINE, binding_verified=True
    )
    assert (timeout.run_status, timeout.runner_outcome) == (
        RunStatus.TIMEOUT,
        RunnerOutcome.TIMEOUT,
    )
    cancelled = decide_after_runner(
        failure(RunnerFailureCode.CANCELLED, True), cause=StopCause.CANCEL, binding_verified=True
    )
    assert (cancelled.run_status, cancelled.runner_outcome) == (
        RunStatus.CANCELLED,
        RunnerOutcome.CANCELLED,
    )
    own = decide_after_runner(
        failure(RunnerFailureCode.TIMEOUT, True), cause=None, binding_verified=True
    )
    assert own.run_status is RunStatus.TIMEOUT  # o timeout da própria política


def test_cancelled_sem_causa_registrada_e_relatorio_contraditorio() -> None:
    d = decide_after_runner(
        failure(RunnerFailureCode.CANCELLED, True), cause=None, binding_verified=True
    )
    assert d.code is VerifyCode.REPORT_CONTRADICTORY
    assert d.run_status is RunStatus.ERROR and d.requires_finalization is False
    pending = decide_after_runner(
        failure(RunnerFailureCode.CANCELLED, None), cause=None, binding_verified=True
    )
    assert pending.requires_finalization is True  # árvore sem prova: fail closed


@pytest.mark.parametrize(
    "code",
    [
        RunnerFailureCode.UNSUPPORTED_RUNNER,
        RunnerFailureCode.INVALID_POLICY,
        RunnerFailureCode.WORKSPACE_UNAVAILABLE,
        RunnerFailureCode.EXECUTABLE_UNAVAILABLE,
        RunnerFailureCode.INVALID_ENVIRONMENT,
    ],
)
def test_falhas_de_pre_condicao_do_runner(code: RunnerFailureCode) -> None:
    d = decide_after_runner(failure(code), cause=None, binding_verified=True)
    assert (d.run_status, d.code, d.runner_outcome) == (
        RunStatus.ERROR,
        VerifyCode.RUNNER_FAILURE,
        RunnerOutcome.TECHNICAL_FAILURE,
    )
    assert d.requires_finalization is False  # o processo nunca nasceu
    contradictory = decide_after_runner(failure(code, False), cause=None, binding_verified=True)
    assert contradictory.requires_finalization is True


def test_binding_perdido_ao_fim_nunca_fecha_ok_nem_fim_normal() -> None:
    d = decide_after_runner(exited(0), cause=None, binding_verified=False)
    assert (d.run_status, d.code, d.normal_end) == (
        RunStatus.BLOCKED,
        VerifyCode.BINDING_REVOKED,
        False,
    )
    assert d.runner_outcome is RunnerOutcome.TECHNICAL_FAILURE


def test_arvore_contradita_com_exit_normal_exige_finalizacao() -> None:
    d = decide_after_runner(exited(0, tree_confirmed_dead=False), cause=None, binding_verified=True)
    assert d.run_status is RunStatus.ERROR and d.requires_finalization is True
    assert d.code is VerifyCode.SUPERVISION_FAILED


# ------------------------------------------------------ paradas antes do processo


@pytest.mark.parametrize("cause", CAUSES)
@pytest.mark.parametrize(
    "code",
    [
        VerifyCode.BINDING_REFUSED,
        VerifyCode.BINDING_REVOKED,
        VerifyCode.CANCELLED_BEFORE_START,
        VerifyCode.DEADLINE_EXHAUSTED,
    ],
)
def test_parada_antes_da_verificacao_nao_a_conta_como_verificada(
    code: VerifyCode, cause: StopCause | None
) -> None:
    d = decide_not_verified(code, cause)
    assert d.integrity is IntegrityVerdict.UNVERIFIABLE
    assert d.runner_outcome is RunnerOutcome.NOT_RUN
    assert d.run_status is not RunStatus.OK and not d.normal_end


@pytest.mark.parametrize("cause", CAUSES)
@pytest.mark.parametrize(
    "code",
    [
        VerifyCode.BINDING_REFUSED,
        VerifyCode.BINDING_REVOKED,
        VerifyCode.CANCELLED,
        VerifyCode.DEADLINE_EXHAUSTED,
    ],
)
def test_parada_depois_da_verificacao_preserva_a_integridade(
    code: VerifyCode, cause: StopCause | None
) -> None:
    d = decide_stop_after_verification(code, cause)
    assert d.integrity is IntegrityVerdict.VERIFIED
    assert d.run_status is not RunStatus.OK and not d.normal_end
    assert d.runner_outcome in (
        RunnerOutcome.TIMEOUT,
        RunnerOutcome.CANCELLED,
        RunnerOutcome.TECHNICAL_FAILURE,
    )


def test_conteudo_diferente_do_verificado_nao_e_verificado() -> None:
    d = decide_changed_since_verified()
    assert d.integrity is IntegrityVerdict.UNVERIFIABLE
    assert d.runner_outcome is RunnerOutcome.NOT_RUN and d.run_status is RunStatus.ERROR


def test_decisoes_alimentam_a_classificacao_da_tentativa_sem_sucesso_indevido() -> None:
    """Nenhuma combinação que o contrato produz vira `done` se não for sucesso integral."""
    decisions: list[VerifyDecision] = [
        decide_verification_failure(VerificationReport(s, reason="r"), c)
        for s in VerificationStatus
        if s is not VS.VERIFIED
        for c in CAUSES
    ]
    decisions += [decide_changed_since_verified()]
    decisions += [
        decide_after_runner(r, cause=c, binding_verified=b)
        for r in [None, exited(0), exited(2)]
        + [failure(code, d) for code in RunnerFailureCode for d in (True, False, None)]
        for c in CAUSES
        for b in (True, False)
    ]
    for d in decisions:
        facts = ExecutionFacts(
            integrity=d.integrity,
            developer=__import__(
                "app.orchestrator.execution_contract", fromlist=["x"]
            ).DeveloperOutcome.COMPLETED,
            tests=d.runner_outcome,
            confirmed_terminal=None,
        )
        verdict = classify_execution(facts)
        if d.run_status is not RunStatus.OK:
            assert verdict.code.value != "success", d


# ---------------------------------------------------------------------------- eventos


def test_eventos_da_verificacao_usam_codigos_previstos_e_nao_atribuem_autoria() -> None:
    cases = {
        VS.OUT_OF_WORKTREE_WRITE: SafetyEventKind.OUT_OF_WORKTREE_WRITE,
        VS.INTEGRITY_VIOLATION: SafetyEventKind.TOCTOU_RECHECK_FAILED,
    }
    for status, kind in cases.items():
        report = VerificationReport(status, reason="head_moved")
        [event] = verification_events(report, None)
        assert event.kind is kind
        assert "developer" not in f"{event.rule_id} {event.subject} {event.detail}".lower()
    findings = tuple(
        FindingFact(f"p{i}.txt", "added", VS.PATH_POLICY_DENIED, f"path.r{i}")
        for i in range(MAX_FINDING_EVENTS + 5)
    )
    report = VerificationReport(
        VS.PATH_POLICY_DENIED, reason="path_policy_denied", findings=findings
    )
    events = verification_events(report, None)
    assert len(events) == MAX_FINDING_EVENTS
    assert {e.kind for e in events} == {SafetyEventKind.PATH_DENIED}
    outside = VerificationReport(
        VS.OUTSIDE_WORKSPACE_CHANGE,
        reason="change_outside_workspace",
        findings=(
            FindingFact("x", "added", VS.OUTSIDE_WORKSPACE_CHANGE, "change_outside_workspace"),
        ),
    )
    [event] = verification_events(outside, None)
    assert event.kind is SafetyEventKind.PATH_DENIED and "outside_workspace" in event.rule_id


@pytest.mark.parametrize(
    ("failure_code", "cause", "kind"),
    [
        ("limit_exceeded", None, SafetyEventKind.LIMIT_EXCEEDED),
        ("deadline_exceeded", None, SafetyEventKind.TIMEOUT),
        ("cancelled", None, SafetyEventKind.CANCELLED),
        (None, StopCause.DEADLINE, SafetyEventKind.TIMEOUT),
        (None, StopCause.CANCEL, SafetyEventKind.CANCELLED),
    ],
)
def test_eventos_de_orcamento_da_verificacao(
    failure_code: str | None, cause: StopCause | None, kind: SafetyEventKind
) -> None:
    report = VerificationReport(VS.UNVERIFIABLE, reason="u", failure=failure_code)
    [event] = verification_events(report, cause)
    assert event.kind is kind


def test_nao_verificavel_sem_causa_de_orcamento_nao_gera_evento() -> None:
    assert verification_events(VerificationReport(VS.UNVERIFIABLE, reason="u"), None) == ()
    assert (
        verification_events(VerificationReport(VS.UNVERIFIABLE, reason="u", failure="x"), None)
        == ()
    )
    assert verification_events(None, None) == ()
    assert verification_events(VerificationReport(VS.VERIFIED), None) == ()


def test_eventos_do_runner_so_para_timeout_e_cancelamento() -> None:
    for outcome, kind in (
        (RunnerOutcome.TIMEOUT, SafetyEventKind.TIMEOUT),
        (RunnerOutcome.CANCELLED, SafetyEventKind.CANCELLED),
    ):
        d = VerifyDecision(
            RunStatus.TIMEOUT, VerifyCode.TIMEOUT, IntegrityVerdict.VERIFIED, outcome
        )
        [event] = runner_events(d)
        assert event.kind is kind
    for outcome in (RunnerOutcome.PASSED, RunnerOutcome.FAILED, RunnerOutcome.TECHNICAL_FAILURE):
        d = VerifyDecision(
            RunStatus.ERROR, VerifyCode.TESTS_FAILED, IntegrityVerdict.VERIFIED, outcome
        )
        assert runner_events(d) == ()


# -------------------------------------------------------------------- sinal com causa


class Tick:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_sinal_registra_a_causa_da_primeira_parada_e_nao_muda() -> None:
    clock = Tick()
    flag = {"cancel": False}
    signal = AttemptSignal(lambda: flag["cancel"], deadline_at=10.0, clock=clock)
    causes: list[StopCause | None] = []
    assert signal() is False
    causes.append(signal.cause)
    clock.now = 11.0
    assert signal() is True
    causes.append(signal.cause)
    flag["cancel"] = True
    assert signal() is True
    causes.append(signal.cause)  # a causa não muda depois de registrada
    assert causes == [None, StopCause.DEADLINE, StopCause.DEADLINE]


def test_cancelamento_externo_vence_o_prazo_no_mesmo_instante() -> None:
    signal = AttemptSignal(lambda: True, deadline_at=0.0, clock=Tick(5.0))
    assert signal() is True and signal.cause is StopCause.CANCEL


@pytest.mark.parametrize(
    "bad", [lambda: (_ for _ in ()).throw(RuntimeError()), lambda: None, lambda: 0]
)
def test_sinal_externo_que_levanta_ou_nao_e_false_cancela(bad: Any) -> None:
    signal = AttemptSignal(bad, deadline_at=1e9, clock=Tick())
    assert signal() is True and signal.cause is StopCause.CANCEL


def test_relogio_que_levanta_expira_o_prazo() -> None:
    def boom() -> float:
        raise RuntimeError

    signal = AttemptSignal(lambda: False, deadline_at=1.0, clock=boom)
    assert signal() is True and signal.cause is StopCause.DEADLINE
    assert signal.remaining_s() == 0.0


def test_remaining_nunca_e_negativo() -> None:
    signal = AttemptSignal(lambda: False, deadline_at=5.0, clock=Tick(9.0))
    assert signal.remaining_s() == 0.0


# --------------------------------------------------------- dados: validação de contrato


def test_relatorio_do_runner_exige_exit_ou_falha() -> None:
    with pytest.raises(ValueError):
        RunnerReport(identity=ID)
    with pytest.raises(ValueError):
        RunnerReport(identity=ID, exit_code=0, framework="x", failure=RunnerFailureCode.TIMEOUT)
    with pytest.raises(ValueError):
        RunnerReport(identity=ID, exit_code=0, framework="x", passed=1)
    with pytest.raises(ValueError):
        RunnerReport(identity=ID, exit_code=True, framework="x")


def test_relatorio_de_verificacao_valida_reason_e_findings() -> None:
    with pytest.raises(ValueError):
        VerificationReport(VS.VERIFIED, reason="x")
    with pytest.raises(ValueError):
        VerificationReport(VS.OUT_OF_WORKTREE_WRITE)  # bloqueia sem reason
    with pytest.raises(ValueError):
        VerificationReport(VS.UNVERIFIABLE, reason="Tem Espaço /caminho")
    with pytest.raises(ValueError):
        FindingFact("p", "added", VS.VERIFIED, "r")


def test_metricas_todas_ou_nenhuma() -> None:
    ok = MetricsReport(files_changed=("a",), diff_added=1, diff_removed=0)
    assert ok.measured
    for kwargs in (
        {"files_changed": ("a",), "diff_added": 1},
        {"files_changed": None, "diff_added": 1, "diff_removed": 1, "failure": "x"},
        {"files_changed": ("a", "a"), "diff_added": 0, "diff_removed": 0},
        {"files_changed": ("a",), "diff_added": 0, "diff_removed": 0, "failure": "binary"},
        {"files_changed": ("a",), "diff_added": -1, "diff_removed": 0},
        {},  # ausente sem causa
    ):
        with pytest.raises(ValueError):
            MetricsReport(**kwargs)
    absent = MetricsReport(failure="binary")
    assert not absent.measured and absent.diff_added is None


def test_decisao_ok_exige_tudo() -> None:
    base = {
        "run_status": RunStatus.OK,
        "code": VerifyCode.TESTS_PASSED,
        "integrity": IntegrityVerdict.VERIFIED,
        "runner_outcome": RunnerOutcome.PASSED,
        "normal_end": True,
    }
    VerifyDecision(**base)  # type: ignore[arg-type]
    for change in (
        {"integrity": IntegrityVerdict.UNVERIFIABLE},
        {"runner_outcome": RunnerOutcome.FAILED},
        {"requires_finalization": True},
        {"normal_end": False},
    ):
        with pytest.raises(ValueError):
            VerifyDecision(**{**base, **change})  # type: ignore[arg-type]


def test_resultado_completed_exige_prova_completa() -> None:
    base: dict[str, Any] = {
        "status": VerifyStatus.COMPLETED,
        "code": VerifyCode.TESTS_PASSED,
        "run_id": "r",
        "run_status": RunStatus.OK,
        "process_started": True,
        "integrity": IntegrityVerdict.VERIFIED,
        "runner_outcome": RunnerOutcome.PASSED,
        "binding": BindingOutcome.RELEASED,
        "finalization": Finalization.CLOSED,
    }
    VerifyAndTestResult(**base)
    for change in (
        {"run_status": RunStatus.ERROR},
        {"process_started": False},
        {"integrity": IntegrityVerdict.UNVERIFIABLE},
        {"runner_outcome": RunnerOutcome.FAILED},
        {"binding": BindingOutcome.RELEASE_FAILED},
        {"finalization": Finalization.FAILED},
        {"requires_finalization": True},
        {"code": VerifyCode.TESTS_FAILED},
    ):
        with pytest.raises(ValueError):
            VerifyAndTestResult(**{**base, **change})
    with pytest.raises(ValueError):  # processo iniciado não é `not_run`
        VerifyAndTestResult(
            VerifyStatus.FAILED,
            VerifyCode.TESTS_FAILED,
            process_started=True,
            runner_outcome=RunnerOutcome.NOT_RUN,
        )
    with pytest.raises(ValueError):  # sem integridade verificada o processo não nasce
        VerifyAndTestResult(
            VerifyStatus.FAILED,
            VerifyCode.TESTS_FAILED,
            process_started=True,
            integrity=IntegrityVerdict.VIOLATED,
            runner_outcome=RunnerOutcome.FAILED,
        )


# --------------------------------------------------------------- resumo e claim


def test_summary_de_teste_nao_carrega_saida() -> None:
    summary = summary_of_report(exited(3))
    assert summary == {
        "framework": "generic-subprocess-v1",
        "exit_code": 3,
        "passed": None,
        "failed": None,
        "skipped": None,
        "duration_ms": 1,
        "output_ref": None,
    }
    assert summary_of_report(failure(RunnerFailureCode.TIMEOUT)) is None


def test_error_summary_so_carrega_o_codigo() -> None:
    for code in VerifyCode:
        text = run_error_summary(code)
        assert text == f"verify_and_test:{code.value}"


CONTROL = "11111111-1111-4111-8111-111111111111"


def _spec(**changes: Any) -> RunnerRunSpec:
    base: dict[str, Any] = {
        "invocation_id": component_invocation_id(CONTROL, "test_runner"),
        "control_run_id": CONTROL,
        "developer_run_id": "22222222-2222-4222-8222-222222222222",
        "context_manifest_id": "m1",
        "identity": ID,
        "base_commit": "a" * 40,
        "worktree_path": "/w",
        "expected_task_version": 3,
        "attempt_index": 0,
        "fix_round": 0,
        "started_at": datetime.now(UTC),
    }
    return RunnerRunSpec(**{**base, **changes})


def _existing(**changes: Any) -> ExistingRunFacts:
    spec = _spec()
    base: dict[str, Any] = {
        "invocation_id": spec.invocation_id,
        "task_id": "t1",
        "agent": "test_runner",
        "purpose": "execution",
        "attempt_index": 0,
        "fix_round": 0,
        "base_commit": spec.base_commit,
        "context_manifest_id": "m1",
        "subject_run_id": None,
        "supersedes_run_id": None,
        "provider": "system",
        "provider_adapter": "generic-subprocess-v1",
        "adapter_version": "1",
        "model": None,
        "transport": "process",
        "control_linked": True,
        "worktree_path": "/w",
    }
    return ExistingRunFacts(**{**base, **changes})


def test_claim_compativel_exige_a_identidade_completa() -> None:
    assert runner_claim_compatible(_existing(), _spec(), "t1") is True
    mutations: list[dict[str, Any]] = [
        {"invocation_id": "outra-chave"},
        {"task_id": "t2"},
        {"agent": "developer"},
        {"agent": "orchestrator"},
        {"purpose": "workflow_audit"},
        {"attempt_index": 1},
        {"fix_round": 1},
        {"base_commit": "b" * 40},
        {"context_manifest_id": "m2"},
        {"subject_run_id": "x"},
        {"supersedes_run_id": "x"},
        {"provider": "outro"},
        {"provider_adapter": "outro-runner"},
        {"adapter_version": "2"},
        {"model": "algum-modelo"},
        {"transport": "api"},
        {"control_linked": False},
    ]
    for change in mutations:
        assert runner_claim_compatible(_existing(**change), _spec(), "t1") is False, change
    assert runner_claim_compatible(_existing(), _spec(), "t2") is False


def test_claim_com_chave_nao_derivada_do_controle_e_incompativel() -> None:
    spec = _spec(invocation_id="exec-1")
    existing = _existing(invocation_id="exec-1")
    assert runner_claim_compatible(existing, spec, "t1") is False
    bad_control = _spec(control_run_id="nao-e-uuid")
    assert runner_claim_compatible(_existing(), bad_control, "t1") is False


def test_identidade_do_runner_so_aceita_processo_local() -> None:
    with pytest.raises(ValueError):
        RunnerIdentity("r", "1", transport="api")
    with pytest.raises(ValueError):
        RunnerIdentity("", "1")


def test_replace_do_spec_nao_e_um_atalho_de_identidade() -> None:
    spec = _spec()
    assert replace(spec, attempt_index=1).attempt_index == 1


_ = (itertools, SimpleNamespace)
