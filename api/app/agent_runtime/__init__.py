"""`agent_runtime` — contratos de provider e test runner (E7.2).

Só interfaces e DTOs neutros: nenhuma implementação concreta, nenhum processo, nenhum SDK.
Adaptadores (Claude Code, Codex) e o supervisor de processos são etapas posteriores.

A E7.6 acrescenta `verification`: a porta `CapabilityVerifier`, distinta da declaração, que
observa de forma independente o que foi aplicado. Nenhuma implementação existe em produção.

A E8.3 acrescenta `TestRunnerFailure`/`TestRunnerFailureCode` (falha estruturada do runner) e
o runner concreto em `agent_runtime/runners/` — infraestrutura do sistema, **não** ferramenta
do Developer; não é reexportado aqui, é ligado pelo composition root.
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
    RunFailureReason,
    RunLimits,
    RunReport,
    RunStatus,
    TestRequest,
    TestRunnerFailure,
    TestRunnerFailureCode,
    TestSummary,
    TokenSource,
)
from app.agent_runtime.interfaces import AuditorProvider, DeveloperProvider, TestRunner
from app.agent_runtime.verification import (
    CapabilityVerifier,
    CapabilityVerifierContractViolation,
    NotVerified,
    VerificationResult,
    Verified,
    binding_of,
    observe_declared_capabilities,
)
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
    "CapabilityVerifier",
    "CapabilityVerifierContractViolation",
    "DeveloperExecutionRequest",
    "DeveloperProvider",
    "EnforcementEvidence",
    "EnforcementMethod",
    "ExecutionWorkspaceRef",
    "FilesReadSource",
    "FindingSeverity",
    "MediatedTools",
    "MediatedUsage",
    "NotVerified",
    "RunFailureReason",
    "RunLimits",
    "RunReport",
    "RunScope",
    "RunStatus",
    "TestRequest",
    "TestRunner",
    "TestRunnerFailure",
    "TestRunnerFailureCode",
    "TestSummary",
    "TokenSource",
    "ToolExecutor",
    "ToolExecutorFactory",
    "ToolRequest",
    "ToolResult",
    "VerificationResult",
    "Verified",
    "binding_of",
    "observe_declared_capabilities",
]
