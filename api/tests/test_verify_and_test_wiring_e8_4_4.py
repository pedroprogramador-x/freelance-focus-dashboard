"""E8.4.4 — a ponte do composition root: tradução fiel, sem inventar nem perder fatos."""

from __future__ import annotations

from typing import Any

import pytest

from app.agent_runtime import TestRunnerFailure, TestRunnerFailureCode, TestSummary
from app.execution_verification import PostExecutionResult, PostExecutionStatus
from app.git_runtime.diff_stats import DiffMeasureFailure, DiffMetrics
from app.orchestrator.verify_and_test_contract import (
    MeasureInput,
    RunnerFailureCode,
    RunnerIdentity,
    RunnerInput,
    VerificationStatus,
)
from app.safety.test_policy import parse_test_policy
from app.verify_and_test_wiring import PostExecutionVerificationPort, TestRunnerPort, _report
from tests.verify_and_test_support_e8_4_4 import python_test_config

IDENTITY = RunnerIdentity("generic-subprocess-v1", "1")
SUMMARY = TestSummary("generic-subprocess-v1", 4, None, None, None, 12, None)


class FakeRunner:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.requests: list[Any] = []

    def run(self, request: Any) -> Any:
        self.requests.append(request)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _input(is_cancelled: Any = lambda: False) -> RunnerInput:
    policy = parse_test_policy(python_test_config())
    assert policy is not None
    return RunnerInput(
        task_id="t",
        run_id="r",
        invocation_id="inv",
        workspace_id="w",
        base_commit="a" * 40,
        test_policy=policy,
        is_cancelled=is_cancelled,
    )


def test_summary_do_runner_vira_relatorio_sem_saida() -> None:
    fake = FakeRunner(SUMMARY)
    report = TestRunnerPort(fake, IDENTITY).run(_input())
    assert (report.exit_code, report.framework, report.duration_ms) == (
        4,
        "generic-subprocess-v1",
        12,
    )
    assert report.failure is None and report.tree_confirmed_dead is None
    [request] = fake.requests
    assert (request.run_scope.run_id, request.run_scope.invocation_id) == ("r", "inv")
    assert request.workspace_ref.id == "w" and request.workspace_ref.base_commit == "a" * 40


@pytest.mark.parametrize("dead", [True, False, None])
@pytest.mark.parametrize("code", list(TestRunnerFailureCode))
def test_falha_do_runner_preserva_codigo_e_prova_da_arvore(
    code: TestRunnerFailureCode, dead: bool | None
) -> None:
    runner = FakeRunner(TestRunnerFailure(code, tree_confirmed_dead=dead))
    report = TestRunnerPort(runner, IDENTITY).run(_input())
    assert report.failure is RunnerFailureCode(code.value)
    assert report.tree_confirmed_dead is dead
    assert report.exit_code is None


def test_excecao_inesperada_do_runner_propaga_para_o_servico_decidir() -> None:
    with pytest.raises(RuntimeError):
        TestRunnerPort(FakeRunner(RuntimeError("x")), IDENTITY).run(_input())


def test_token_de_cancelamento_reflete_o_sinal_e_levantar_cancela() -> None:
    seen: list[bool] = []

    class Spy:
        def run(self, request: Any) -> Any:
            seen.append(request.cancel_token.is_cancelled())
            return SUMMARY

    def boom() -> bool:
        raise RuntimeError

    for signal in (lambda: True, lambda: False, boom, lambda: None):
        TestRunnerPort(Spy(), IDENTITY).run(_input(signal))
    assert seen == [True, False, True, True]


def test_identidade_e_a_injetada() -> None:
    assert TestRunnerPort(FakeRunner(None), IDENTITY).identity() is IDENTITY


def _measure_input(handle: Any) -> MeasureInput:
    return MeasureInput(
        handle=handle,
        workspace_path="/w",
        workspace_prefix="",
        base_commit="a" * 40,
        is_cancelled=lambda: False,
    )


def test_medicao_de_handle_estranho_e_metricas_ausentes_nao_zero() -> None:
    port = PostExecutionVerificationPort(
        object(),  # type: ignore[arg-type]
        lambda _w: None,  # type: ignore[arg-type,return-value]
    )
    report = port.measure(_measure_input("não é um resultado"))
    assert report.measured is False and report.failure == "no_verified_result"
    assert report.diff_added is None


class _Verifier:
    def __init__(self, out: Any) -> None:
        self.out = out

    def measure_diff(self, result: Any, **kwargs: Any) -> Any:
        return self.out


def _verified() -> PostExecutionResult:
    return PostExecutionResult(
        status=PostExecutionStatus.VERIFIED, reason=None, anchor=("/m", "/m/.git")
    )


def test_medicao_traduz_metricas_e_falhas() -> None:
    request = _measure_input(_verified())
    ok = PostExecutionVerificationPort(
        _Verifier(DiffMetrics(("a.py",), 3, 1)),  # type: ignore[arg-type]
        lambda _w: None,  # type: ignore[arg-type,return-value]
    ).measure(request)
    assert (ok.files_changed, ok.diff_added, ok.diff_removed) == (("a.py",), 3, 1)
    for failure, diverged in (
        (DiffMeasureFailure.CHANGED_SINCE_VERIFIED, True),
        (DiffMeasureFailure.BINARY, False),
        (DiffMeasureFailure.TOO_LARGE, False),
    ):
        out = PostExecutionVerificationPort(
            _Verifier(failure),  # type: ignore[arg-type]
            lambda _w: None,  # type: ignore[arg-type,return-value]
        ).measure(request)
        assert out.failure == failure.value and out.changed_since_verified is diverged
        assert out.diff_added is None and out.files_changed is None


def test_relatorio_so_carrega_o_handle_quando_verified() -> None:
    verified = _verified()
    assert _report(verified).handle is verified
    blocked = PostExecutionResult(status=PostExecutionStatus.UNVERIFIABLE, reason="unverifiable")
    out = _report(blocked)
    assert out.handle is None and out.status is VerificationStatus.UNVERIFIABLE
