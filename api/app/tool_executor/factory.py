"""`LocalWorktreeToolExecutorFactory` (E7.5-A): a factory de **vida de aplicação**.

`create` produz um executor novo, utilizável, para **um** run — ou levanta. Ordem, fail closed:

1. tipos dos argumentos (estrutural);
2. `require_v1(perfil, DEVELOPER)` — o perfil efetivo precisa ser **exatamente** o oficial
   (`CapabilityProfileRejected` para o Execution Manager registrar `capability_unenforceable`);
3. `kind`: `REMOTE` é recusado na V1 (`UnsupportedWorkspace`) **antes** de consultar o resolvedor;
4. `resolver.resolve(workspace_ref, run_scope)` — a chave inclui o run, nunca só a ref;
5. o binding devolvido é conferido contra a ref **e** o run (base_commit, workspace, task, run);
6. `bind_root` — a raiz existe, é diretório, sem reparse e com a identidade registrada.

Os passos 3–6 são `workspace.bind_workspace` (extraída na E8.3, mesma ordem e mesmos códigos),
compartilhada com o `TestRunner`.

Nada aqui consulta banco, provider ou registry global; nenhuma operação é executada.
"""

from __future__ import annotations

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
)
from app.tool_executor.executor import LocalWorktreeToolExecutor
from app.tool_executor.validation import ContractViolation, require_instance
from app.tool_executor.workspace import WorkspaceResolver, bind_workspace


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

        # Passos 3–6: a conferência única do binding (E8.3), a mesma do `TestRunner`.
        bound = bind_workspace(self._resolver, workspace_ref, run_scope)

        return LocalWorktreeToolExecutor(
            workspace=bound.resolved,
            root=bound.root,
            policy=composed_policy,
            profile=profile,
            run_scope=run_scope,
            cancel_token=cancel_token,
            journal=decision_journal,
            limits=self._limits,
        )


__all__ = ["LocalWorktreeToolExecutorFactory"]
