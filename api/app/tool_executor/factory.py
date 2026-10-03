"""`LocalWorktreeToolExecutorFactory` (E7.5-A): a factory de **vida de aplicação**.

`create` produz um executor novo, utilizável, para **um** run — ou levanta. Ordem, fail closed:

1. tipos dos argumentos (estrutural);
2. `require_v1(perfil, DEVELOPER)` — o perfil efetivo precisa ser **exatamente** o oficial
   (`CapabilityProfileRejected` para o Execution Manager registrar `capability_unenforceable`);
3. `kind`: `REMOTE` é recusado na V1 (`UnsupportedWorkspace`) **antes** de consultar o resolvedor;
4. `resolver.resolve(workspace_ref, run_scope)` — a chave inclui o run, nunca só a ref;
5. o binding devolvido é conferido contra a ref **e** o run (base_commit, workspace, task, run);
6. `bind_root` — a raiz existe, é diretório, sem reparse e com a identidade registrada.

Nada aqui consulta banco, provider ou registry global; nenhuma operação é executada.
"""

from __future__ import annotations

from app.path_runtime import PathIntegrityViolation, PathOperationFailed, bind_root
from app.safety.capability_profile import (
    ProviderCapabilityProfile,
    ProviderRole,
    require_v1,
)
from app.safety.policy import SafetyPolicy
from app.safety.tool_limits import TOOL_LIMITS_V1, ToolLimits
from app.tool_executor.contracts import (
    CancelToken,
    DecisionJournal,
    ExecutionWorkspaceRef,
    RunScope,
    ToolExecutor,
    WorkspaceKind,
)
from app.tool_executor.executor import LocalWorktreeToolExecutor
from app.tool_executor.reasons import rule_for_integrity
from app.tool_executor.validation import ContractViolation, require_instance
from app.tool_executor.workspace import (
    ResolvedWorkspace,
    UnsupportedWorkspace,
    WorkspaceResolver,
    WorkspaceUnavailable,
)


def _require_callable(name: str, value: object, attribute: str) -> None:
    if not callable(getattr(value, attribute, None)):
        raise ContractViolation(f"{name} precisa expor {attribute}()")


class LocalWorktreeToolExecutorFactory:
    def __init__(
        self,
        resolver: WorkspaceResolver,
        *,
        limits: ToolLimits = TOOL_LIMITS_V1,
    ) -> None:
        _require_callable("resolver", resolver, "resolve")
        self._resolver = resolver
        self._limits = limits

    def create(
        self,
        workspace_ref: ExecutionWorkspaceRef,
        composed_policy: SafetyPolicy,
        effective_capability_profile: ProviderCapabilityProfile,
        run_scope: RunScope,
        cancel_token: CancelToken,
        decision_journal: DecisionJournal,
    ) -> ToolExecutor:
        require_instance("workspace_ref", workspace_ref, ExecutionWorkspaceRef)
        require_instance("composed_policy", composed_policy, SafetyPolicy)
        require_instance(
            "effective_capability_profile", effective_capability_profile, ProviderCapabilityProfile
        )
        require_instance("run_scope", run_scope, RunScope)
        _require_callable("cancel_token", cancel_token, "is_cancelled")
        _require_callable("decision_journal", decision_journal, "append")

        profile = require_v1(effective_capability_profile, ProviderRole.DEVELOPER)

        if workspace_ref.kind is not WorkspaceKind.LOCAL_WORKTREE:
            raise UnsupportedWorkspace("workspace_kind_unsupported")

        resolved = self._resolver.resolve(workspace_ref, run_scope)
        if resolved is None:
            raise WorkspaceUnavailable("workspace_unbound")
        if not isinstance(resolved, ResolvedWorkspace):
            raise WorkspaceUnavailable("resolver_invalid")
        if resolved.base_commit != workspace_ref.base_commit:
            raise WorkspaceUnavailable("base_commit_mismatch")
        if (
            resolved.workspace_id != workspace_ref.id
            or resolved.task_id != run_scope.task_id
            or resolved.run_id != run_scope.run_id
        ):
            raise WorkspaceUnavailable("binding_mismatch")

        try:
            root = bind_root(resolved.workspace_path, expected_identity=resolved.root_identity)
        except PathIntegrityViolation as violation:
            raise WorkspaceUnavailable(
                "root_unavailable", rule_id=rule_for_integrity(violation.category)
            ) from None
        except PathOperationFailed:
            raise WorkspaceUnavailable("root_unavailable") from None

        return LocalWorktreeToolExecutor(
            workspace=resolved,
            root=root,
            policy=composed_policy,
            profile=profile,
            run_scope=run_scope,
            cancel_token=cancel_token,
            journal=decision_journal,
            limits=self._limits,
        )


__all__ = ["LocalWorktreeToolExecutorFactory"]
