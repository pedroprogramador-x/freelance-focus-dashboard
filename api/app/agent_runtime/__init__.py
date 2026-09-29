"""`agent_runtime` — contratos de provider e test runner (E7.2).

Só interfaces e DTOs neutros: nenhuma implementação concreta, nenhum processo, nenhum SDK.
Adaptadores (Claude Code, Codex) e o supervisor de processos são etapas posteriores.
Ver `docs/architecture/05-provider-contracts.md`.
"""

from app.agent_runtime.declaration import (
    CapabilityDeclaration,
    EnforcementEvidence,
    EnforcementMethod,
)
from app.agent_runtime.dto import (
    AgentRunResult,
    AuditFindingDraft,
    AuditPurpose,
    AuditRequest,
    AuditResult,
    AuditVerdict,
    CancelToken,
    DeveloperExecutionRequest,
    FilesReadSource,
    FindingSeverity,
    RunLimits,
    RunReport,
    RunStatus,
    TestRequest,
    TestSummary,
    TokenSource,
)
from app.agent_runtime.interfaces import AuditorProvider, DeveloperProvider, TestRunner
from app.tool_executor.contracts import (
    ExecutionWorkspaceRef,
    MediatedTools,
    MediatedUsage,
    RunScope,
    ToolExecutor,
    ToolExecutorFactory,
    ToolRequest,
    ToolResult,
)

__all__ = [
    "AgentRunResult",
    "AuditFindingDraft",
    "AuditPurpose",
    "AuditRequest",
    "AuditResult",
    "AuditVerdict",
    "AuditorProvider",
    "CancelToken",
    "CapabilityDeclaration",
    "DeveloperExecutionRequest",
    "DeveloperProvider",
    "EnforcementEvidence",
    "EnforcementMethod",
    "ExecutionWorkspaceRef",
    "FilesReadSource",
    "FindingSeverity",
    "MediatedTools",
    "MediatedUsage",
    "RunLimits",
    "RunReport",
    "RunScope",
    "RunStatus",
    "TestRequest",
    "TestRunner",
    "TestSummary",
    "TokenSource",
    "ToolExecutor",
    "ToolExecutorFactory",
    "ToolRequest",
    "ToolResult",
]
