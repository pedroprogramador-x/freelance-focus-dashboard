"""Apoio da E8.4.3 — compartilhado pelos testes da invocação do Developer e pelo worker de processo.

**Só testes** montam um prover positivo: em produção não existe verificador positivo ainda (E7.6).
Duas pilhas:

* ``build_scripted`` — Git/SQLite/worktree/binding **reais** (admissão e preparação reais) e um
  ``DeveloperRunPort`` scriptado: cobre cada estado inconsistente de forma determinística e rápida;
* ``build_real`` — a mesma base, mas com a ponte real do composition root sobre o
  ``AnthropicMessagesDeveloperProvider`` **verdadeiro**, a ``LocalWorktreeToolExecutorFactory``
  verdadeira e um **transporte falso** (``FakeTransport`` da E8.2). Nenhuma rede, credencial ou
  modelo real.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from app.agent_runtime.adapters.anthropic_messages import (
    ADAPTER_ID as REAL_ADAPTER_ID,
)
from app.agent_runtime.adapters.anthropic_messages import (
    AnthropicMessagesDeveloperProvider,
)
from app.agent_runtime.adapters.anthropic_messages.constants import PROVIDER_NAME, TRANSPORT
from app.capability_wiring import VerifyingCapabilityProver
from app.db.models import WorkspaceTask
from app.developer_invocation_wiring import ProviderBackedDeveloperRuntime
from app.developer_wiring import (
    AnthropicDeveloperBindingResolver,
    prepare_developer_security_context,
)
from app.orchestrator import approve, create_task, get_task, plan
from app.orchestrator.developer_binding import DeveloperBinding
from app.orchestrator.developer_invocation import DeveloperInvocationService
from app.orchestrator.developer_invocation_contract import (
    DeveloperInvocationInput,
    DeveloperInvocationReport,
    ProviderIdentity,
    ReportedStatus,
    ToolDenial,
)
from app.orchestrator.execution_contract import ExecutionAdmission
from app.orchestrator.execution_manager import admit_execution
from app.orchestrator.execution_workspace import (
    ExecutionWorkspaceService,
    PreparedWorkspace,
)
from app.orchestrator.model_router import DeveloperEffort
from app.orchestrator.planner import DEVELOPER_REASONING_EFFORT_KEY
from app.safety.capability_profile import DEVELOPER_V1_PROFILE
from app.tool_executor.factory import LocalWorktreeToolExecutorFactory
from app.workspace import set_test_config
from app.workspace.policy import resolve_effective_policy
from tests.admission_support_e8_4_1 import (
    RESOLVER,
    VALID_TEST_CONFIG,
    admit,
)
from tests.capability_helpers import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    MODEL,
    ScriptedVerifier,
    declaration,
    expected_binding,
)
from tests.reservation_support_e8_4_5 import DEFAULT_IDENTITY
from tests.test_anthropic_messages_developer_adapter_e8_2 import FakeTransport, _opener
from tests.test_execution_workspace_e8_4_2 import Harness, Signal

#: O transporte do Run do Developer na persistência (`RunTransport`). Os dois lados concordam.
RUN_TRANSPORT = "api"

__all__ = [
    "Rig",
    "ScriptedRuntime",
    "Signal",
    "build_real",
    "build_scripted",
    "denial",
    "denial_for",
    "make_harness",
    "make_report",
    "scripted_identity",
]


def make_harness(
    session: Any,
    session_factory: Any,
    workspace: Any,
    repo_path: Any,
    temp_settings: Any,
    tmp_path: Any,
) -> Harness:
    """O `Harness` da E8.4.2 (Git/SQLite reais em diretórios temporários)."""
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


def scripted_identity(**changes: str) -> ProviderIdentity:
    """A identidade que casa com o `developer_binding` aprovado pelo `RESOLVER` de teste."""
    base = {
        "provider": "scripted",
        "adapter": ADAPTER_ID,
        "adapter_version": ADAPTER_VERSION,
        "model": MODEL,
        "transport": RUN_TRANSPORT,
    }
    return ProviderIdentity(**{**base, **changes})


def make_report(identity: ProviderIdentity, **changes: Any) -> DeveloperInvocationReport:
    """Um relatório `ok` coerente. ``**changes`` sobrepõe campos."""
    base: dict[str, Any] = {
        "status": ReportedStatus.OK,
        "provider_called": True,
        "identity": identity,
        "input_tokens": 100,
        "output_tokens": 50,
        "token_source": "reported",
        "duration_ms": 5,
        "summary": "feito",
        "files_read": ("src/util.py",),
        "operations": 2,
        "denial_count": 0,
        "executor_closed": True,
    }
    return DeveloperInvocationReport(**{**base, **changes})


def denial(
    run_id: str,
    sequence: int = 1,
    category: str = "path_denied",
    *,
    task_id: str = "task",
    invocation_id: str = "inv",
    **changes: Any,
) -> ToolDenial:
    base = {
        "sequence": sequence,
        "run_id": run_id,
        "task_id": task_id,
        "invocation_id": invocation_id,
        "operation": "ff_read_file",
        "category": category,
        "rule_id": "path.denied_for_test",
        "subject_redacted": "src/segredo.txt",
        "reason": "negado pela política",
    }
    return ToolDenial(**{**base, **changes})


def denial_for(
    request: DeveloperInvocationInput,
    sequence: int = 1,
    category: str = "path_denied",
    **changes: Any,
) -> ToolDenial:
    """Uma negação **deste** escopo (run, task e invocação da requisição)."""
    run_id = changes.pop("run_id", request.run_id)
    return denial(
        run_id,
        sequence,
        category,
        task_id=changes.pop("task_id", request.task_id),
        invocation_id=changes.pop("invocation_id", request.invocation_id),
        **changes,
    )


class ScriptedRuntime:
    """`DeveloperRunPort` roteirizado. Conta as chamadas — a prova de "no máximo uma".

    ``evidence`` faz o papel do **executor**: alimenta o `EvidenceCollector` do serviço (o canal
    real das negações). O relatório do provider não tem como carregá-las."""

    def __init__(
        self,
        identity: ProviderIdentity,
        report: DeveloperInvocationReport
        | Callable[[DeveloperInvocationInput], DeveloperInvocationReport]
        | None = None,
        *,
        raises: BaseException | None = None,
        hook: Callable[[DeveloperInvocationInput], None] | None = None,
        evidence: Callable[[DeveloperInvocationInput], Iterable[ToolDenial]] | None = None,
    ) -> None:
        self._identity = identity
        self.report = report
        self.raises = raises
        self.hook = hook
        self.evidence = evidence
        self.calls: list[DeveloperInvocationInput] = []

    def identity(self) -> ProviderIdentity:
        return self._identity

    def invoke(self, request: DeveloperInvocationInput) -> DeveloperInvocationReport:
        self.calls.append(request)
        if self.hook is not None:
            self.hook(request)
        if self.evidence is not None:
            for item in self.evidence(request):
                request.evidence.append(item)
        if self.raises is not None:
            raise self.raises
        report = self.report
        if callable(report):
            return report(request)
        return report if report is not None else make_report(self._identity)


@dataclass
class Rig:
    """Tudo o que um teste precisa, já admitido e preparado."""

    h: Harness
    task: WorkspaceTask
    admission: ExecutionAdmission
    context: PreparedWorkspace[Any]
    workspaces: ExecutionWorkspaceService[Any]
    service: DeveloperInvocationService
    runtime: Any
    prover: Any
    expected: Any
    signal: Signal
    identity: ProviderIdentity
    transport: FakeTransport | None = None

    @property
    def control_run_id(self) -> str:
        return self.context.control_run_id

    def invoke(self, **kwargs: Any) -> Any:
        return self.service.invoke(
            self.context, is_cancelled=kwargs.pop("is_cancelled", self.signal)
        )

    def with_service(self, **overrides: Any) -> DeveloperInvocationService:
        """Outro serviço sobre a mesma base (ex.: outro "processo", ou portas trocadas)."""
        params = {
            "session_factory": self.h.factory,
            "workspaces": self.workspaces,
            "runtime": self.runtime,
            "prover": self.prover,
            "expected_capability_binding": self.expected,
            "artifacts_dir": self.h.artifacts,
            "developer_binding_resolver": RESOLVER,
            "now": self.h.clock,
        }
        return DeveloperInvocationService(**{**params, **overrides})


def _scripted_capability() -> tuple[VerifyingCapabilityProver, Any]:
    decl = declaration(transport=RUN_TRANSPORT)
    verifier = ScriptedVerifier(observed_profile=DEVELOPER_V1_PROFILE)
    return VerifyingCapabilityProver(verifier=verifier, declaration=decl), expected_binding(
        decl, transport=RUN_TRANSPORT
    )


def build_scripted(
    h: Harness,
    runtime: ScriptedRuntime | Callable[[ProviderIdentity], ScriptedRuntime] | None = None,
    *,
    bindings: Any = None,
    prover: Any = "default",
    expected: Any = "default",
    runtime_prover: Any = "same",
    runtime_expected: Any = "same",
) -> Rig:
    """Task aprovada → admitida (com prover positivo de teste) → preparada → serviço pronto.

    ``prover``/``expected`` valem para a **admissão**; ``runtime_*`` para o serviço da invocação
    (por padrão, os mesmos) — assim um teste troca só a expectativa da invocação."""
    default_prover, default_expected = _scripted_capability()
    admit_prover = default_prover if prover == "default" else prover
    admit_expected = default_expected if expected == "default" else expected
    task = h.approve()
    admission = admit(
        h.session, task.id, h.artifacts, key="exec-1", prover=admit_prover, expected=admit_expected
    )
    result = h.prepare(admission, **({"bindings": bindings} if bindings is not None else {}))
    assert result.context is not None, result
    identity = scripted_identity()
    chosen = runtime(identity) if callable(runtime) else runtime or ScriptedRuntime(identity)
    svc_prover = admit_prover if runtime_prover == "same" else runtime_prover
    svc_expected = admit_expected if runtime_expected == "same" else runtime_expected
    ws = h.service(**({"bindings": bindings} if bindings is not None else {}))
    signal = Signal()
    service = DeveloperInvocationService(
        session_factory=h.factory,
        workspaces=ws,
        runtime=chosen,
        prover=svc_prover,
        expected_capability_binding=svc_expected,
        artifacts_dir=h.artifacts,
        developer_binding_resolver=RESOLVER,
        now=h.clock,
    )
    return Rig(
        h,
        task,
        admission,
        result.context,
        ws,
        service,
        chosen,
        svc_prover,
        svc_expected,
        signal,
        identity,
    )


def build_real(h: Harness, fake: FakeTransport) -> Rig:
    """A pilha **real** (provider Anthropic + executor + ponte do composition root) com
    transporte falso. Aprovação e prova de capability pelo caminho real do adaptador."""
    resolver = AnthropicDeveloperBindingResolver()
    set_test_config(h.session, h.workspace.id, VALID_TEST_CONFIG)
    task = create_task(h.session, h.workspace.id, title="real", goal="ajustar o util")
    planned = plan(
        h.session, task.id, artifacts_dir=h.artifacts, developer_binding_resolver=resolver
    )
    approve(
        h.session,
        task.id,
        execution_fingerprint=planned.fingerprint,
        developer_binding_resolver=resolver,
    )
    h.session.commit()
    approved = get_task(h.session, task.id)
    parts = approved.approved_fingerprint_parts
    assert parts is not None
    binding = DeveloperBinding(**parts["developer_binding"])
    effort = DeveloperEffort(parts["execution_limits"][DEVELOPER_REASONING_EFFORT_KEY])
    security = prepare_developer_security_context(binding, effort)

    h.session.commit()
    h.session.expire_all()
    admission = admit_execution(
        h.session,
        task.id,
        invocation_id="exec-1",
        artifacts_dir=h.artifacts,
        reservation_identity=DEFAULT_IDENTITY,
        prover=security.prover,
        expected_capability_binding=security.expected_binding,
        developer_binding_resolver=resolver,
    )
    ws = ExecutionWorkspaceService(
        session_factory=h.factory,
        settings=h.settings,
        ports=h.ports(),
        developer_binding_resolver=resolver,
        now=h.clock,
    )
    result = h.prepare(admission, service=ws)
    assert result.context is not None, result

    identity = ProviderIdentity(
        provider=PROVIDER_NAME,
        adapter=REAL_ADAPTER_ID,
        adapter_version=binding.adapter_version,
        model=binding.model,
        transport=TRANSPORT,
    )
    provider = AnthropicMessagesDeveloperProvider(
        security.runtime_bundle, transport_opener=_opener(fake)
    )
    runtime = ProviderBackedDeveloperRuntime(
        provider=provider,
        tool_executor_factory=LocalWorktreeToolExecutorFactory(h.bindings.resolver),
        policy_for_workspace=lambda _workspace_id: resolve_effective_policy(h.workspace),
        identity=identity,
    )
    signal = Signal()
    service = DeveloperInvocationService(
        session_factory=h.factory,
        workspaces=ws,
        runtime=runtime,
        prover=security.prover,
        expected_capability_binding=security.expected_binding,
        artifacts_dir=h.artifacts,
        developer_binding_resolver=resolver,
        now=h.clock,
    )
    return Rig(
        h,
        approved,
        admission,
        result.context,
        ws,
        service,
        runtime,
        security.prover,
        security.expected_binding,
        signal,
        identity,
        transport=fake,
    )
