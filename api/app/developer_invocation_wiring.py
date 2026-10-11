"""Composition root da invocação do Developer (E8.4.3): a porta sobre o provider e o executor.

Junto de `capability_wiring` e `developer_wiring`, este é um dos poucos módulos que enxergam ao
mesmo tempo o Orchestrator (`DeveloperRunPort` e os dados neutros do contrato) e `agent_runtime`
(`DeveloperProvider`, `ToolExecutorFactory`). Nenhum dos dois importa o outro: o Orchestrator
continua neutro de fornecedor ([01] §3) e `agent_runtime` não conhece o Orchestrator.

## O que `ProviderBackedDeveloperRuntime.invoke` faz

1. confere que o perfil efetivo do provider é **exatamente** o oficial do Developer V1
   (`DEVELOPER_V1_PROFILE`) — o perfil exigido nunca vem de quem invoca;
2. cria o executor **deste** Run pela factory (`ToolExecutorFactory.create`: `require_v1`,
   binding conferido, raiz com identidade) e entrega ao provider só `mediated_tools()` — a
   superfície fechada de nove operações mediadas; não há shell, `argv` nem ferramenta nativa;
3. chama `provider.run` **uma vez**;
4. encerra o executor em `finally` e lê dele — não do provider — o que foi lido
   (`files_read`, `reported`), as contagens e as negações do `DecisionJournal`.

Qualquer falha antes da chamada (perfil, política, executor, request) devolve um relatório
`error` com `provider_called = False`. Exceção do provider vira relatório `error` sem o texto da
exceção. Nada aqui consulta banco, rede ou segredo: o transporte (e a credencial, se houver) já
vêm embutidos no `DeveloperProvider` injetado — nos testes, um transporte falso.

**Não está ligado a `main.py`, rota, fila ou worker** (integração é da E8.4.6); o Claude Agent
SDK não participa desta cadeia.
"""

from __future__ import annotations

from collections.abc import Callable

from app.agent_runtime import (
    DeveloperExecutionRequest,
    DeveloperProvider,
    ExecutionWorkspaceRef,
    RunFailureReason,
    RunLimits,
    RunScope,
    ToolDecisionRecord,
    ToolExecutorFactory,
    WorkspaceKind,
)
from app.orchestrator.developer_invocation_contract import (
    DeveloperInvocationInput,
    DeveloperInvocationReport,
    EvidenceCollector,
    ProviderIdentity,
    ReportedStatus,
    ToolDenial,
)
from app.safety.capability_profile import DEVELOPER_V1_PROFILE
from app.safety.policy import SafetyPolicy


class _Journal:
    """`DecisionJournal` do Run: entrega cada negação ao `EvidenceCollector` **do serviço**.

    É a única fonte de evidência do executor. O provider nunca recebe este objeto, e o relatório que
    sai desta ponte não tem campo de negação: o que o provider escrever em seu resultado não vira
    evidência. Falha ao traduzir/entregar levanta, e o executor trata como journal indisponível
    (fail closed). Nunca persiste."""

    def __init__(self, sink: EvidenceCollector) -> None:
        self._sink = sink

    def append(self, record: ToolDecisionRecord) -> None:
        self._sink.append(
            ToolDenial(
                sequence=record.sequence,
                run_id=record.run_scope.run_id,
                task_id=record.run_scope.task_id,
                invocation_id=record.run_scope.invocation_id,
                operation=record.operation,
                category=record.category.value,
                rule_id=record.rule_id,
                subject_redacted=record.subject_redacted,
                reason=record.reason,
            )
        )


class _CancelToken:
    """`CancelToken` sobre o sinal injetado. Um sinal que levanta conta como cancelado."""

    __slots__ = ("_signal",)

    def __init__(self, signal: Callable[[], bool]) -> None:
        self._signal = signal

    def is_cancelled(self) -> bool:
        try:
            return bool(self._signal())
        except Exception:
            return True


class ProviderBackedDeveloperRuntime:
    """`DeveloperRunPort` sobre um `DeveloperProvider` e uma `ToolExecutorFactory` injetados."""

    def __init__(
        self,
        *,
        provider: DeveloperProvider,
        tool_executor_factory: ToolExecutorFactory,
        policy_for_workspace: Callable[[str], SafetyPolicy],
        identity: ProviderIdentity,
    ) -> None:
        self._provider = provider
        self._factory = tool_executor_factory
        self._policy_for = policy_for_workspace
        self._identity = identity

    def __repr__(self) -> str:
        return "<ProviderBackedDeveloperRuntime>"

    def identity(self) -> ProviderIdentity:
        return self._identity

    def _failed(self, code: str, *, provider_called: bool = False) -> DeveloperInvocationReport:
        return DeveloperInvocationReport(
            status=ReportedStatus.ERROR,
            provider_called=provider_called,
            identity=self._identity,
            error_summary=code,
            executor_closed=True,  # nenhum executor ficou aberto nestes caminhos
        )

    def invoke(self, request: DeveloperInvocationInput) -> DeveloperInvocationReport:
        try:
            if self._provider.capability_profile() != DEVELOPER_V1_PROFILE:
                return self._failed("capability_profile_mismatch")
            policy = self._policy_for(request.workspace_id)
        except Exception:
            return self._failed("provider_unavailable")

        journal = _Journal(request.evidence)
        cancel = _CancelToken(request.is_cancelled)
        scope = RunScope(
            task_id=request.task_id, run_id=request.run_id, invocation_id=request.invocation_id
        )
        workspace_ref = ExecutionWorkspaceRef(
            kind=WorkspaceKind.LOCAL_WORKTREE,
            id=request.workspace_id,
            base_commit=request.base_commit,
        )
        try:
            executor = self._factory.create(
                workspace_ref, policy, DEVELOPER_V1_PROFILE, scope, cancel, journal
            )
        except Exception:
            return self._failed("executor_unavailable")

        result = None
        called = False
        try:
            dto = DeveloperExecutionRequest(
                task_id=request.task_id,
                run_id=request.run_id,
                invocation_id=request.invocation_id,
                goal=request.goal,
                acceptance_criteria=request.acceptance_criteria,
                plan_steps=request.plan_steps,
                rendered_context=request.rendered_context,
                rendered_context_hash=request.rendered_context_hash,
                context_manifest_id=request.context_manifest_id,
                workspace_ref=workspace_ref,
                capability_profile=DEVELOPER_V1_PROFILE,
                limits=RunLimits(max_tokens=request.max_total_tokens, timeout_s=request.timeout_s),
                cancel_token=cancel,
            )
            called = True
            result = self._provider.run(dto, executor.mediated_tools())
        except Exception:
            result = None
        finally:
            try:
                executor.close()
                closed = True
            except Exception:
                closed = False

        files_read: tuple[str, ...] | None
        try:
            usage = executor.usage()
            files_read, operations, denial_count = usage.files_read, usage.operations, usage.denials
        except Exception:
            files_read, operations, denial_count = None, 0, 0
        if result is None:
            return DeveloperInvocationReport(
                status=ReportedStatus.ERROR,
                provider_called=called,
                identity=self._identity,
                error_summary="provider_exception" if called else "request_invalid",
                files_read=files_read,
                operations=operations,
                denial_count=denial_count,
                executor_closed=closed,
            )
        return DeveloperInvocationReport(
            status=ReportedStatus(result.status.value),
            provider_called=True,
            identity=ProviderIdentity(
                provider=result.provider,
                adapter=result.provider_adapter,
                adapter_version=result.adapter_version,
                model=result.model,
                transport=result.transport,
            ),
            limit_exceeded=result.failure_reason is RunFailureReason.LIMIT_EXCEEDED,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            token_source=result.token_source.value,
            duration_ms=result.duration_ms,
            summary=result.summary,
            error_summary=result.error_summary,
            files_read=files_read,
            operations=operations,
            denial_count=denial_count,
            executor_closed=closed,
        )


__all__ = ["ProviderBackedDeveloperRuntime"]
