"""Apoio da E8.4.4 — verificação do diff + execução dos testes (interno, não operacional).

Monta a mesma base da E8.4.3 (Git/SQLite/worktree/binding/admissão/preparação **reais**, prover
positivo só de teste), faz a invocação do Developer com um runtime roteirizado — que pode **escrever
na worktree**, como o executor mediado faria — e entrega o `VerifyAndTestService` pronto, com:

* ``build_rig(..., real_runner=False)``: runner roteirizado (conta chamadas, observa o sinal) e a
  verificação **real** da E8.3 (`PostExecutionVerificationPort` sobre o `PostExecutionVerifier`);
* ``real_runner=True``: o `LocalSubprocessTestRunner` real executando um comando inofensivo
  (`python -c …`) sobre o binding real.

Nenhuma rede, credencial ou modelo real.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.agent_runtime.runners import LocalSubprocessTestRunner
from app.execution_verification import PostExecutionVerifier
from app.orchestrator.developer_invocation import DeveloperInvocationService
from app.orchestrator.developer_invocation_contract import DeveloperInvocationResult
from app.orchestrator.verify_and_test import VerifyAndTestService
from app.orchestrator.verify_and_test_contract import (
    MeasureInput,
    MetricsReport,
    RunnerIdentity,
    RunnerInput,
    RunnerReport,
    VerificationInput,
    VerificationReport,
)
from app.verify_and_test_wiring import PostExecutionVerificationPort, TestRunnerPort
from app.workspace import set_test_config
from app.workspace.policy import resolve_effective_policy
from tests.admission_support_e8_4_1 import RESOLVER, admit
from tests.developer_invocation_support_e8_4_3 import (
    Rig as DeveloperRig,
)
from tests.developer_invocation_support_e8_4_3 import (
    ScriptedRuntime,
    _scripted_capability,
    make_report,
    scripted_identity,
)
from tests.test_execution_workspace_e8_4_2 import Harness, Signal

RUNNER_IDENTITY = RunnerIdentity(runner_id="generic-subprocess-v1", version="1")


def python_test_config(code: str = "import sys; sys.exit(0)", **changes: Any) -> dict[str, Any]:
    """Um `test_config` válido que executa o Python do próprio venv de teste (caminho absoluto)."""
    base: dict[str, Any] = {
        "runner_id": "generic-subprocess-v1",
        "executable": sys.executable,
        "argv": ["-c", code],
        "timeout_seconds": 120,
        "env_allowlist": ["PATH", "SYSTEMROOT"],
        "network_policy": "unrestricted",
        "output_limits": {"max_stdout_bytes": 65536, "max_stderr_bytes": 65536},
        "cwd_mode": "task_worktree",
    }
    return {**base, **changes}


class ScriptedVerification:
    """`VerificationPort` roteirizado. Conta as chamadas (a prova de "uma verificação") e delega à
    verificação real, a menos que um relatório seja fornecido."""

    def __init__(
        self,
        inner: Any = None,
        *,
        report: VerificationReport | None = None,
        metrics: MetricsReport | None = None,
        raises: BaseException | None = None,
        measure_raises: BaseException | None = None,
        before: Callable[[VerificationInput], None] | None = None,
        before_measure: Callable[[MeasureInput], None] | None = None,
        after_measure: Callable[[MeasureInput], None] | None = None,
    ) -> None:
        self.inner = inner
        self.report = report
        self.metrics = metrics
        self.raises = raises
        self.measure_raises = measure_raises
        self.before = before
        self.before_measure = before_measure
        self.after_measure = after_measure
        self.verify_calls: list[VerificationInput] = []
        self.measure_calls: list[MeasureInput] = []

    def verify(self, request: VerificationInput) -> VerificationReport:
        self.verify_calls.append(request)
        if self.before is not None:
            self.before(request)
        if self.raises is not None:
            raise self.raises
        if self.report is not None:
            return self.report
        assert self.inner is not None
        out: VerificationReport = self.inner.verify(request)
        return out

    def measure(self, request: MeasureInput) -> MetricsReport:
        self.measure_calls.append(request)
        if self.before_measure is not None:
            self.before_measure(request)
        if self.measure_raises is not None:
            raise self.measure_raises
        if self.metrics is not None:
            return self.metrics
        assert self.inner is not None
        out: MetricsReport = self.inner.measure(request)
        if self.after_measure is not None:
            self.after_measure(request)
        return out


class ScriptedRunner:
    """`RunnerPort` roteirizado. Conta as chamadas — a prova de "no máximo uma execução"."""

    def __init__(
        self,
        report: RunnerReport | Callable[[RunnerInput], RunnerReport] | None = None,
        *,
        raises: BaseException | None = None,
        hook: Callable[[RunnerInput], None] | None = None,
        identity: RunnerIdentity = RUNNER_IDENTITY,
    ) -> None:
        self._identity = identity
        self.report = report
        self.raises = raises
        self.hook = hook
        self.calls: list[RunnerInput] = []

    def identity(self) -> RunnerIdentity:
        return self._identity

    def run(self, request: RunnerInput) -> RunnerReport:
        self.calls.append(request)
        if self.hook is not None:
            self.hook(request)
        if self.raises is not None:
            raise self.raises
        report = self.report
        if callable(report):
            return report(request)
        return report if report is not None else exited(0, self._identity)


def exited(
    code: int = 0, identity: RunnerIdentity = RUNNER_IDENTITY, **changes: Any
) -> RunnerReport:
    base: dict[str, Any] = {
        "identity": identity,
        "exit_code": code,
        "framework": "generic-subprocess-v1",
        "duration_ms": 7,
    }
    return RunnerReport(**{**base, **changes})


def failed(
    code: str, identity: RunnerIdentity = RUNNER_IDENTITY, *, dead: bool | None = None
) -> RunnerReport:
    from app.orchestrator.verify_and_test_contract import RunnerFailureCode

    return RunnerReport(
        identity=identity, failure=RunnerFailureCode(code), tree_confirmed_dead=dead
    )


class FlakyBindings:
    """Envolve o serviço de preparação e **falha a conferência do binding** a partir da N-ésima
    chamada (simula uma revogação no meio da passagem). Delega todo o resto."""

    def __init__(self, inner: Any, *, fail_from: int | None = None) -> None:
        self.inner = inner
        self.fail_from = fail_from
        self.verify_calls = 0
        self.released: list[object] = []
        self.revoked: list[object] = []

    def bind_component_run(self, context: Any, *, run_id: str, is_cancelled: Any) -> object:
        return self.inner.bind_component_run(context, run_id=run_id, is_cancelled=is_cancelled)

    def verify_component_binding(self, context: Any, *, run_id: str, invocation_id: str) -> bool:
        self.verify_calls += 1
        if self.fail_from is not None and self.verify_calls >= self.fail_from:
            return False
        return bool(
            self.inner.verify_component_binding(context, run_id=run_id, invocation_id=invocation_id)
        )

    def release_binding(self, token: object) -> bool:
        self.released.append(token)
        return bool(self.inner.release_binding(token))

    def revoke_binding(self, token: object) -> bool:
        self.revoked.append(token)
        return bool(self.inner.revoke_binding(token))


@dataclass
class VRig:
    """Tudo o que um teste precisa: a base do Developer já invocada e o serviço pronto."""

    dev: DeveloperRig
    developer: DeveloperInvocationResult
    service: VerifyAndTestService
    verification: ScriptedVerification
    runner: Any
    bindings: FlakyBindings
    signal: Signal
    clock: FakeClock
    real_verification: PostExecutionVerificationPort
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def h(self) -> Harness:
        return self.dev.h

    @property
    def context(self) -> Any:
        return self.dev.context

    @property
    def control_run_id(self) -> str:
        return self.dev.control_run_id

    def execute(self, **kwargs: Any) -> Any:
        return self.service.execute(
            self.context,
            kwargs.pop("developer", self.developer),
            is_cancelled=kwargs.pop("is_cancelled", self.signal),
        )

    def with_service(self, **overrides: Any) -> VerifyAndTestService:
        params: dict[str, Any] = {
            "session_factory": self.h.factory,
            "workspaces": self.bindings,
            "verification": self.verification,
            "runner": self.runner,
            "developer_binding_resolver": RESOLVER,
            "now": self.h.clock,
            "clock": self.clock,
        }
        return VerifyAndTestService(**{**params, **overrides})


class FakeClock:
    """Relógio monotônico controlado: `advance` empurra o tempo; sem ele, avança com o real."""

    def __init__(self) -> None:
        import time

        self._offset = 0.0
        self._time = time
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return float(self._time.monotonic() + self._offset)

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._offset += seconds


def build_rig(
    h: Harness,
    *,
    developer_hook: Callable[[Any], None] | None = None,
    test_config: dict[str, Any] | None = None,
    runner: Any = None,
    real_runner: bool = False,
    verification: ScriptedVerification | None = None,
    fail_binding_from: int | None = None,
    invoke_developer: bool = True,
    developer_report: dict[str, Any] | None = None,
) -> VRig:
    """Task aprovada (com o ``test_config`` dado) → admitida → preparada → Developer invocado `ok`
    → `VerifyAndTestService` pronto. ``developer_hook(rig)`` roda **dentro** da invocação do
    Developer (é onde o "executor" escreve na worktree)."""
    from app.orchestrator.execution_workspace import PreparedWorkspace  # noqa: F401

    set_test_config(h.session, h.workspace.id, test_config or python_test_config())
    task = h.approve(configure_tests=False)
    default_prover, default_expected = _scripted_capability()
    admission = admit(
        h.session,
        task.id,
        h.artifacts,
        key="exec-1",
        prover=default_prover,
        expected=default_expected,
    )
    prepared = h.prepare(admission)
    assert prepared.context is not None, prepared
    identity = scripted_identity()
    holder: dict[str, Any] = {}

    def hook(_request: Any) -> None:
        if developer_hook is not None:
            developer_hook(holder["rig"])

    runtime = ScriptedRuntime(
        identity, lambda _r: make_report(identity, **(developer_report or {})), hook=hook
    )
    ws = h.service()
    signal = Signal()
    dev_service = DeveloperInvocationService(
        session_factory=h.factory,
        workspaces=ws,
        runtime=runtime,
        prover=default_prover,
        expected_capability_binding=default_expected,
        artifacts_dir=h.artifacts,
        developer_binding_resolver=RESOLVER,
        now=h.clock,
    )
    dev = DeveloperRig(
        h,
        task,
        admission,
        prepared.context,
        ws,
        dev_service,
        runtime,
        default_prover,
        default_expected,
        signal,
        identity,
    )
    holder["rig"] = dev
    developer = dev.invoke() if invoke_developer else None
    if invoke_developer:
        assert developer is not None and developer.run_status is not None

    real_verification = PostExecutionVerificationPort(
        PostExecutionVerifier(), lambda _workspace_id: resolve_effective_policy(h.workspace)
    )
    scripted_verification = verification or ScriptedVerification(real_verification)
    if real_runner:
        chosen_runner: Any = TestRunnerPort(
            LocalSubprocessTestRunner(h.bindings.resolver, trusted_environment=dict(os.environ)),
            RUNNER_IDENTITY,
        )
    else:
        chosen_runner = runner or ScriptedRunner()
    bindings = FlakyBindings(ws, fail_from=fail_binding_from)
    clock = FakeClock()
    service = VerifyAndTestService(
        session_factory=h.factory,
        workspaces=bindings,
        verification=scripted_verification,
        runner=chosen_runner,
        developer_binding_resolver=RESOLVER,
        now=h.clock,
        clock=clock,
    )
    assert developer is not None or not invoke_developer
    return VRig(
        dev,
        developer,  # type: ignore[arg-type]
        service,
        scripted_verification,
        chosen_runner,
        bindings,
        Signal(),
        clock,
        real_verification,
    )


def write_in_worktree(rig: DeveloperRig, relative: str, content: str | bytes) -> None:
    """O "executor" do Developer escrevendo na worktree (o que o `WriteFile` mediado faria)."""
    path = os.path.join(rig.context.workspace_path, *relative.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = content.encode("utf-8") if isinstance(content, str) else content
    with open(path, "wb") as handle:
        handle.write(data)


__all__ = [
    "RUNNER_IDENTITY",
    "FakeClock",
    "FlakyBindings",
    "ScriptedRunner",
    "ScriptedVerification",
    "VRig",
    "build_rig",
    "exited",
    "failed",
    "python_test_config",
    "write_in_worktree",
]
