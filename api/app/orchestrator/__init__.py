"""Orchestrator — Task Analyzer, Resource Router, Execution Planner e Execution Manager.

[01](../../../docs/architecture/01-v1-architecture.md) §2, contrato do módulo:

* **pode importar** `db`, `context_engine`, `git_runtime`, `safety`, `workspace` (leitura),
  `config`;
* **não pode importar** `api`, e **nunca um adaptador concreto de provider**;
* **recebe** `DeveloperProvider`, `AuditorProvider`, `TestRunner` e `ToolExecutorFactory`
  **por injeção** — não os procura, não consulta registry, não conhece nome de fornecedor;
* é o **único** módulo que escreve `WorkspaceTask`, `Run`, `AuditFinding` e `SafetyEvent`.

`test_architecture.py` transforma cada uma dessas regras em falha de suíte.

## As portas desta fase, todas vazias

Duas portas nascem aqui e **nenhuma tem implementação na E6** — de propósito:

| Porta | Quem implementa | Efeito de estar ausente |
| --- | --- | --- |
| `AnalyzerEnrichmentPort` | E8 | piso de fallback de [03] §5: `risk`/`complexity` ≥ `medium` |
| `CapabilityProver` | E7/E8 | `approved → executing` recusada, *fail closed* ([ADR-0009]) |

As duas ausências têm o **mesmo** desenho e o **mesmo** motivo: uma porta não implementada
degrada para o lado seguro, e o lado seguro é sempre o mais restritivo. Nenhuma delas tem
um ramo "como não existe, siga em frente".

## O que a E6 não faz

Nenhum `Run` é criado, nenhum worktree nasce, nenhum provider é invocado e **nenhum arquivo
do usuário é escrito** ([07], gate da E6). O único efeito em disco é o Rendered Context
Artifact sob `data_dir/artifacts/`, gravado pelo `context_engine` dentro de
`freeze_manifest`.
"""

from app.orchestrator.analyzer import (
    AnalyzerEnrichment,
    AnalyzerEnrichmentPort,
    HardRuleOutcome,
    TaskAnalysis,
    analyze,
    evaluate_hard_rules,
)
from app.orchestrator.eligibility import (
    TRANSITION_AFTER_REJECT,
    TRANSITION_ALLOWED,
    TRANSITION_FORBIDDEN,
    PlanningEligibility,
    planning_eligibility,
)
from app.orchestrator.errors import (
    ApprovalFingerprintMismatch,
    ConcurrentTaskUpdate,
    InvalidTask,
    InvalidTestConfig,
    InvalidTransition,
    OrchestratorError,
    PurgeTokenRejected,
    TaskBenchmarkProtected,
    TaskNotFound,
    TaskPurgeBlocked,
    TransitionGuardFailed,
    WorkspaceNotPlannable,
)
from app.orchestrator.execution_manager import (
    APPROVAL_STATE_APPROVED,
    APPROVAL_STATE_NOT_PLANNED,
    APPROVAL_STATE_PENDING,
    APPROVAL_STATE_REQUIRES_REPLAN,
    APPROVAL_STATES,
    PLAN_STANDING_CURRENT,
    PLAN_STANDING_FINAL,
    PLAN_STANDING_HISTORICAL,
    PLAN_STANDING_NONE,
    approval_state,
    approve,
    cancel,
    create_task,
    get_task,
    latest_manifest,
    list_tasks,
    plan,
    plan_standing,
    reconcile_on_startup,
    record_safety_event,
    reject,
    start_execution,
)
from app.orchestrator.fingerprint import (
    FINGERPRINT_VERSION,
    REQUIRED_TOOL_PROFILE,
    FingerprintParts,
    WorkflowPolicy,
    diverged_fields,
    tool_profile_hash,
)
from app.orchestrator.planner import PLAN_VERSION, PLANNING_BLOCKERS, PlanResult, plan_task
from app.orchestrator.purge import execute_purge as execute_task_purge
from app.orchestrator.purge import purge_preview as task_purge_preview
from app.orchestrator.resource_router import ResourceDecision, route
from app.orchestrator.state_machine import (
    TERMINAL_STATUSES,
    TRANSITIONS,
    CapabilityProof,
    CapabilityProver,
    EntryGuardFacts,
    can_transition,
    is_terminal,
)
from app.safety.test_policy import NULL_TEST_BINDING, TestPolicy, parse_test_policy

__all__ = [
    "APPROVAL_STATES",
    "APPROVAL_STATE_APPROVED",
    "APPROVAL_STATE_NOT_PLANNED",
    "APPROVAL_STATE_PENDING",
    "APPROVAL_STATE_REQUIRES_REPLAN",
    "FINGERPRINT_VERSION",
    "NULL_TEST_BINDING",
    "PLANNING_BLOCKERS",
    "PLAN_STANDING_CURRENT",
    "PLAN_STANDING_FINAL",
    "PLAN_STANDING_HISTORICAL",
    "PLAN_STANDING_NONE",
    "PLAN_VERSION",
    "REQUIRED_TOOL_PROFILE",
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "TRANSITION_AFTER_REJECT",
    "TRANSITION_ALLOWED",
    "TRANSITION_FORBIDDEN",
    "AnalyzerEnrichment",
    "AnalyzerEnrichmentPort",
    "ApprovalFingerprintMismatch",
    "CapabilityProof",
    "CapabilityProver",
    "ConcurrentTaskUpdate",
    "EntryGuardFacts",
    "FingerprintParts",
    "HardRuleOutcome",
    "InvalidTask",
    "InvalidTestConfig",
    "InvalidTransition",
    "OrchestratorError",
    "PlanResult",
    "PlanningEligibility",
    "PurgeTokenRejected",
    "ResourceDecision",
    "TaskAnalysis",
    "TaskBenchmarkProtected",
    "TaskNotFound",
    "TaskPurgeBlocked",
    "TestPolicy",
    "TransitionGuardFailed",
    "WorkflowPolicy",
    "WorkspaceNotPlannable",
    "analyze",
    "approval_state",
    "approve",
    "can_transition",
    "cancel",
    "create_task",
    "diverged_fields",
    "evaluate_hard_rules",
    "execute_task_purge",
    "get_task",
    "is_terminal",
    "latest_manifest",
    "list_tasks",
    "parse_test_policy",
    "plan",
    "plan_standing",
    "plan_task",
    "planning_eligibility",
    "reconcile_on_startup",
    "record_safety_event",
    "reject",
    "route",
    "start_execution",
    "task_purge_preview",
    "tool_profile_hash",
]
