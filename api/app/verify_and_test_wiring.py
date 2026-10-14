"""Composition root de "Tests and Results" (E8.4.4): as duas portas do `VerifyAndTestService`.

Este módulo enxerga, ao mesmo tempo, o Orchestrator (contrato neutro de `verify_and_test_contract`)
e as camadas concretas — a verificação pós-execução e a medição do diff (`execution_verification`,
E8.3/E8.4.4) e o `TestRunner` (`agent_runtime`, E8.3). Nenhum dos lados importa o outro: o
Orchestrator recebe só as portas e o núcleo continua sem `agent_runtime`.

## O que existe aqui

* `PostExecutionVerificationPort` — `VerificationPort` sobre o `PostExecutionVerifier`. Traduz o
  `PostExecutionResult` para o `VerificationReport` neutro (sem caminho absoluto) e a medição do
  diff para o `MetricsReport` (todas as métricas ou nenhuma);
* `TestRunnerPort` — `RunnerPort` sobre um `TestRunner` injetado. Monta o `TestRequest` com o
  binding do **Run do Test Runner** (`run_scope`), o `CancelToken` é o sinal do serviço (prazo +
  cancelamento, com causa) e devolve `RunnerReport` com **exatamente** o que o runner afirmou:
  `TestSummary` ou `TestRunnerFailure` (código e `tree_confirmed_dead`). Sem `stdout`/`stderr`.

## O que não existe

Nada aqui lê credencial, chama modelo, liga rota ou worker, nem executa algo por conta própria: o
serviço decide quando e se as portas são chamadas.
"""

from __future__ import annotations

from collections.abc import Callable

from app.agent_runtime import (
    ExecutionWorkspaceRef,
    RunScope,
    TestRequest,
    TestRunner,
    TestRunnerFailure,
    WorkspaceKind,
)
from app.execution_verification import PostExecutionResult, PostExecutionVerifier
from app.git_runtime.diff_stats import (
    CONTENT_DIVERGED_FAILURES,
    INTEGRITY_PROVEN_FAILURES,
    DiffMetrics,
)
from app.orchestrator.verify_and_test_contract import (
    FindingFact,
    MeasureInput,
    MetricsReport,
    RunnerFailureCode,
    RunnerIdentity,
    RunnerInput,
    RunnerReport,
    VerificationInput,
    VerificationReport,
    VerificationStatus,
)
from app.safety.policy import SafetyPolicy


class _CancelToken:
    """`CancelToken` sobre o sinal do serviço. Um sinal que levanta conta como cancelado."""

    __slots__ = ("_signal",)

    def __init__(self, signal: Callable[[], bool]) -> None:
        self._signal = signal

    def is_cancelled(self) -> bool:
        try:
            return self._signal() is not False
        except Exception:
            return True


class PostExecutionVerificationPort:
    """`VerificationPort` sobre o `PostExecutionVerifier` da E8.3."""

    def __init__(
        self,
        verifier: PostExecutionVerifier,
        policy_for_workspace: Callable[[str], SafetyPolicy],
    ) -> None:
        self._verifier = verifier
        self._policy_for = policy_for_workspace

    def __repr__(self) -> str:
        return "<PostExecutionVerificationPort>"

    def verify(self, request: VerificationInput) -> VerificationReport:
        # O snapshot é consumido aqui, ainda que a política falhe: uma verificação por claim.
        result = self._verifier.verify(
            before=request.snapshot,  # type: ignore[arg-type]  # opaco para o Orchestrator
            workspace_path=request.workspace_path,
            workspace_prefix=request.workspace_prefix,
            root_identity=request.root_identity,
            base_commit=request.base_commit,
            policy=self._policy_for(request.workspace_id),
            is_cancelled=request.is_cancelled,
        )
        return _report(result)

    def measure(self, request: MeasureInput) -> MetricsReport:
        handle = request.handle
        if not isinstance(handle, PostExecutionResult):
            return MetricsReport(failure="no_verified_result")
        measured = self._verifier.measure_diff(
            handle,
            workspace_path=request.workspace_path,
            workspace_prefix=request.workspace_prefix,
            base_commit=request.base_commit,
            is_cancelled=request.is_cancelled,
        )
        if isinstance(measured, DiffMetrics):
            return MetricsReport(
                files_changed=measured.files_changed,
                diff_added=measured.diff_added,
                diff_removed=measured.diff_removed,
            )
        return MetricsReport(
            failure=measured.value,
            changed_since_verified=measured in CONTENT_DIVERGED_FAILURES,
            integrity_proven=measured in INTEGRITY_PROVEN_FAILURES,
        )


def _report(result: PostExecutionResult) -> VerificationReport:
    status = VerificationStatus(result.status.value)
    return VerificationReport(
        status=status,
        reason=result.reason,
        failure=result.failure.value if result.failure is not None else None,
        findings=tuple(
            FindingFact(
                path=finding.path,
                kind=finding.kind.value,
                status=VerificationStatus(finding.status.value),
                rule_id=finding.rule_id,
            )
            for finding in result.findings
        ),
        handle=result if status is VerificationStatus.VERIFIED else None,
    )


class TestRunnerPort:
    """`RunnerPort` sobre um `TestRunner` injetado (infraestrutura, não ferramenta do Developer)."""

    __test__ = False  # nome `Test*`: pytest não deve tentar coletar

    def __init__(self, runner: TestRunner, identity: RunnerIdentity) -> None:
        self._runner = runner
        self._identity = identity

    def __repr__(self) -> str:
        return "<TestRunnerPort>"

    def identity(self) -> RunnerIdentity:
        return self._identity

    def run(self, request: RunnerInput) -> RunnerReport:
        workspace_ref = ExecutionWorkspaceRef(
            kind=WorkspaceKind.LOCAL_WORKTREE,
            id=request.workspace_id,
            base_commit=request.base_commit,
        )
        scope = RunScope(
            task_id=request.task_id, run_id=request.run_id, invocation_id=request.invocation_id
        )
        try:
            summary = self._runner.run(
                TestRequest(
                    workspace_ref=workspace_ref,
                    run_scope=scope,
                    test_policy=request.test_policy,
                    cancel_token=_CancelToken(request.is_cancelled),
                )
            )
        except TestRunnerFailure as failure:
            return RunnerReport(
                identity=self._identity,
                failure=RunnerFailureCode(failure.code.value),
                tree_confirmed_dead=failure.tree_confirmed_dead,
            )
        return RunnerReport(
            identity=self._identity,
            exit_code=summary.exit_code,
            framework=summary.framework,
            passed=summary.passed,
            failed=summary.failed,
            skipped=summary.skipped,
            duration_ms=summary.duration_ms,
        )


__all__ = ["PostExecutionVerificationPort", "TestRunnerPort"]
