"""Enums do domínio, exatamente como [02](../../../docs/architecture/02-data-model.md).

São persistidos como `VARCHAR` com `CHECK`, não como enum nativo: o SQLite não tem tipo
enum, e o `CHECK` gerado deixa a restrição verificável **pelo banco**, não só pelo Python.
"""

from __future__ import annotations

from enum import Enum


class WorkspaceType(str, Enum):
    PERSONAL = "personal"
    FREELANCE = "freelance"
    STUDY = "study"
    EXPERIMENT = "experiment"
    OPEN_SOURCE = "open_source"


class WorkspaceStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ContextDomain(str, Enum):
    OBJECTIVE = "objective"
    ARCHITECTURE = "architecture"
    STACK = "stack"
    REQUIREMENTS = "requirements"
    MODULES = "modules"
    DECISIONS = "decisions"
    RISKS = "risks"
    CONTRACTS = "contracts"


class ContextState(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class StaleReason(str, Enum):
    SOURCES_CHANGED = "sources_changed"
    WORKING_TREE = "working_tree"


class ContextOrigin(str, Enum):
    MANUAL = "manual"
    IMPORTED_PLANNING = "imported_planning"
    GENERATED = "generated"


class TaskStatus(str, Enum):
    DRAFT = "draft"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    NEEDS_FIX = "needs_fix"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPhase(str, Enum):
    IMPLEMENTING = "implementing"
    TESTING = "testing"
    AUDITING = "auditing"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ComplexityLevel(str, Enum):
    TRIVIAL = "trivial"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskSource(str, Enum):
    HARD_RULE = "hard_rule"
    LLM = "llm"
    USER = "user"


class ExecutionMode(str, Enum):
    CLAUDE_ONLY = "claude_only"
    ORCHESTRATED = "orchestrated"
    ORCHESTRATED_RUFLO = "orchestrated_ruflo"


class FailureReason(str, Enum):
    TIMEOUT = "timeout"
    LIMIT_EXCEEDED = "limit_exceeded"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    CAPABILITY_UNENFORCEABLE = "capability_unenforceable"
    PROVIDER_ERROR = "provider_error"
    TESTS_FAILED = "tests_failed"
    AUDIT_FAILED = "audit_failed"
    INTERRUPTED = "interrupted"
    INTERNAL_ERROR = "internal_error"


class RunAgent(str, Enum):
    ORCHESTRATOR = "orchestrator"
    DEVELOPER = "developer"
    AUDITOR = "auditor"
    ARCHITECT = "architect"
    RESEARCHER = "researcher"
    TEST_RUNNER = "test_runner"


class RunPurpose(str, Enum):
    EXECUTION = "execution"
    WORKFLOW_AUDIT = "workflow_audit"
    BENCHMARK_EVALUATION = "benchmark_evaluation"


class RunTransport(str, Enum):
    CLI = "cli"
    API = "api"
    PROCESS = "process"


class RunStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    INTERRUPTED = "interrupted"


class TokenSource(str, Enum):
    REPORTED = "reported"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


class FilesReadSource(str, Enum):
    REPORTED = "reported"
    INFERRED = "inferred"
    UNAVAILABLE = "unavailable"


class FindingPurpose(str, Enum):
    WORKFLOW_AUDIT = "workflow_audit"
    BENCHMARK_EVALUATION = "benchmark_evaluation"


class FindingSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FindingStatus(str, Enum):
    OPEN = "open"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    FIXED = "fixed"


class SafetyEventKind(str, Enum):
    PATH_DENIED = "path_denied"
    COMMAND_DENIED = "command_denied"
    SECRET_ACCESS_BLOCKED = "secret_access_blocked"  # noqa: S105 — nome de evento, não credencial
    CAPABILITY_UNENFORCEABLE = "capability_unenforceable"
    CAPABILITY_DENIED = "capability_denied"
    LIMIT_EXCEEDED = "limit_exceeded"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_INVALIDATED = "approval_invalidated"
    RETRY_LIMIT = "retry_limit"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    OUT_OF_WORKTREE_WRITE = "out_of_worktree_write"
    TOCTOU_RECHECK_FAILED = "toctou_recheck_failed"
    PURGE_EXECUTED = "purge_executed"


class SafetyDecisionKind(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
