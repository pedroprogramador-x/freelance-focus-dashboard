"""DTOs neutros de `agent_runtime` (E7.2): requisições, resultados e limites.

Fonte congelada: [05] §6. Nenhum campo nomeia Claude, Codex ou Ruflo; `transport`,
`provider_adapter` e `adapter_version` são strings livres do adaptador.

Três decisões deste módulo vêm direto de [05] §6:

* `files_changed`, `diff_added` e `diff_removed` **não existem** aqui — o Git Runtime os
  deriva da worktree; um provider não mede a própria mudança.
* `files_read` é `None` quando indisponível, **nunca `[]` para ausência**; a origem
  (`files_read_source`) precisa concordar: `None` ⇔ `unavailable`.
* Nada de *chain-of-thought*, *thinking* ou prompt completo: só `summary` (resultado, não
  raciocínio) e `log_ref`.

Validação só estrutural; redação e política são de `safety/` e do Execution Manager.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.safety.capability_profile import ProviderCapabilityProfile
from app.safety.test_policy import TestPolicy
from app.tool_executor.contracts import CancelToken as CancelToken  # E7.5-A: definido lá
from app.tool_executor.contracts import ExecutionWorkspaceRef, RunScope
from app.tool_executor.validation import (
    ContractViolation,
    require_instance,
    require_int,
    require_optional_int,
    require_optional_text,
    require_sha256,
    require_text,
    require_text_tuple,
)

# ------------------------------------------------------------------------------ enums


class RunStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class RunFailureReason(str, Enum):
    """Motivo **estruturado** de um run que não terminou `ok` (addendum E8.2 de [05] §6).

    É o control plane do resultado: quem decide transições (E8.4) lê este campo, **nunca**
    `error_summary` (diagnóstico humano, redigido, não estruturado). Só o motivo necessário
    hoje existe; outros entram quando houver necessidade real.
    """

    #: O run parou por exceder o seu orçamento (`limits.max_tokens`). E8.4 →
    #: `SafetyEvent(limit_exceeded)`.
    LIMIT_EXCEEDED = "limit_exceeded"


class TokenSource(str, Enum):
    REPORTED = "reported"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


class FilesReadSource(str, Enum):
    REPORTED = "reported"
    INFERRED = "inferred"
    UNAVAILABLE = "unavailable"


class AuditVerdict(str, Enum):
    PASS = "pass"  # noqa: S105 — veredito de auditoria, não senha
    PASS_WITH_FINDINGS = "pass_with_findings"  # noqa: S105
    FAIL = "fail"


class AuditPurpose(str, Enum):
    WORKFLOW_AUDIT = "workflow_audit"
    BENCHMARK_EVALUATION = "benchmark_evaluation"


class FindingSeverity(str, Enum):
    """[02] §`AuditFinding.severity`."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ------------------------------------------------------------------ limites e cancelamento


def _require_cancel_token(value: object) -> None:
    if not callable(getattr(value, "is_cancelled", None)):
        raise ContractViolation("cancel_token precisa expor is_cancelled()")


@dataclass(frozen=True, slots=True)
class RunLimits:
    max_tokens: int
    timeout_s: int

    def __post_init__(self) -> None:
        require_int("max_tokens", self.max_tokens, minimum=1)
        require_int("timeout_s", self.timeout_s, minimum=1)


# ------------------------------------------------------------------------- requisições


@dataclass(frozen=True, slots=True)
class DeveloperExecutionRequest:
    """[05] §6. As ferramentas chegam **à parte**, como `MediatedTools` — não aqui.

    `capability_profile` é o perfil *exigido*. Representar não é aceitar: quem recusa um
    perfil fora da V1 é `safety.require_v1`, e quem prova o efetivo é a E7.6/E8.
    """

    task_id: str
    run_id: str
    invocation_id: str
    goal: str
    acceptance_criteria: tuple[str, ...]
    plan_steps: tuple[str, ...]
    rendered_context: str
    rendered_context_hash: str
    context_manifest_id: str
    workspace_ref: ExecutionWorkspaceRef
    capability_profile: ProviderCapabilityProfile
    limits: RunLimits
    cancel_token: CancelToken

    def __post_init__(self) -> None:
        require_text("task_id", self.task_id)
        require_text("run_id", self.run_id)
        require_text("invocation_id", self.invocation_id)
        require_text("goal", self.goal)
        require_text_tuple("acceptance_criteria", self.acceptance_criteria)
        require_text_tuple("plan_steps", self.plan_steps)
        require_text("rendered_context", self.rendered_context, allow_empty=True)
        require_sha256("rendered_context_hash", self.rendered_context_hash)
        require_text("context_manifest_id", self.context_manifest_id)
        require_instance("workspace_ref", self.workspace_ref, ExecutionWorkspaceRef)
        require_instance("capability_profile", self.capability_profile, ProviderCapabilityProfile)
        require_instance("limits", self.limits, RunLimits)
        _require_cancel_token(self.cancel_token)


@dataclass(frozen=True, slots=True)
class TestSummary:
    """[05] §6. O `TestRunner` a produz; o auditor a recebe no `AuditRequest`.

    Addendum E8.3: `passed`/`failed`/`skipped` são **todos** `int >= 0` ou **todos** `None`
    (métricas desconhecidas). Mistura parcial é recusada. Um runner genérico, que não
    interpreta a saída do processo, devolve os três `None`; `exit_code` continua sendo a
    fonte autoritativa de sucesso/falha do comando.
    """

    __test__ = False  # nome `Test*`: pytest não deve tentar coletar

    framework: str
    exit_code: int
    passed: int | None
    failed: int | None
    skipped: int | None
    duration_ms: int
    output_ref: str | None

    def __post_init__(self) -> None:
        require_text("framework", self.framework)
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ContractViolation("exit_code precisa ser int")
        counters = (self.passed, self.failed, self.skipped)
        if any(value is None for value in counters):
            if not all(value is None for value in counters):
                raise ContractViolation("passed/failed/skipped: todos int ou todos None")
        else:
            require_int("passed", self.passed)
            require_int("failed", self.failed)
            require_int("skipped", self.skipped)
        require_int("duration_ms", self.duration_ms)
        require_optional_text("output_ref", self.output_ref)


@dataclass(frozen=True, slots=True)
class AuditRequest:
    """[05] §6. O auditor recebe **o diff, não o repositório**."""

    goal: str
    acceptance_criteria: tuple[str, ...]
    unified_diff: str
    test_summary: TestSummary | None
    context_manifest_id: str
    subject_run_id: str
    purpose: AuditPurpose
    rubric_version: str | None
    limits: RunLimits
    cancel_token: CancelToken

    def __post_init__(self) -> None:
        require_text("goal", self.goal)
        require_text_tuple("acceptance_criteria", self.acceptance_criteria)
        require_text("unified_diff", self.unified_diff, allow_empty=True)
        if self.test_summary is not None:
            require_instance("test_summary", self.test_summary, TestSummary)
        require_text("context_manifest_id", self.context_manifest_id)
        require_text("subject_run_id", self.subject_run_id)
        require_instance("purpose", self.purpose, AuditPurpose)
        require_optional_text("rubric_version", self.rubric_version)
        # [02] §`AuditFinding.rubric_version`: obrigatório em benchmark_evaluation.
        if self.purpose is AuditPurpose.BENCHMARK_EVALUATION and self.rubric_version is None:
            raise ContractViolation("benchmark_evaluation exige rubric_version")
        require_instance("limits", self.limits, RunLimits)
        _require_cancel_token(self.cancel_token)


@dataclass(frozen=True, slots=True)
class TestRequest:
    """[05] §6. Reutiliza `safety.TestPolicy`; `command_hash`/`policy_hash` são métodos
    dela e **não** são recalculados aqui (o cálculo do runner pertence à E8).

    Addendum E8.3: `run_scope` é obrigatório. `ExecutionWorkspaceRef` não tem caminho de
    filesystem, e um runner de vida de aplicação só acha a worktree **deste** run pelo
    binding explícito `(workspace_ref, run_scope)` — nunca só pelo `workspace_ref.id`.
    """

    __test__ = False

    workspace_ref: ExecutionWorkspaceRef
    run_scope: RunScope
    test_policy: TestPolicy
    cancel_token: CancelToken

    def __post_init__(self) -> None:
        require_instance("workspace_ref", self.workspace_ref, ExecutionWorkspaceRef)
        require_instance("run_scope", self.run_scope, RunScope)
        require_instance("test_policy", self.test_policy, TestPolicy)
        _require_cancel_token(self.cancel_token)


class TestRunnerFailureCode(str, Enum):
    """Por que o `TestRunner` não produziu um `TestSummary` (addendum E8.3 de [05] §6).

    Só falhas **técnicas** ou de pré-condição. Um processo que terminou normalmente com
    `exit_code != 0` **não** é falha do runner: é um `TestSummary` legítimo. Quem mapeia
    estes códigos para `Run.status`/estado da task/`SafetyEvent` é o Execution Manager (E8.4).
    """

    __test__ = False

    #: `test_policy.runner_id` não é o runner implementado.
    UNSUPPORTED_RUNNER = "unsupported_runner"
    #: A `TestPolicy` recebida não tem a forma que a V1 aceita (ou não vira `ProcessSpec`).
    INVALID_POLICY = "invalid_policy"
    #: O binding `(workspace_ref, run_scope)` não existe, diverge ou não é verificável.
    WORKSPACE_UNAVAILABLE = "workspace_unavailable"
    #: O executável lógico não resolve para um executável confiável e absoluto.
    EXECUTABLE_UNAVAILABLE = "executable_unavailable"
    #: A allowlist de ambiente pede variável proibida, ou o ambiente confiável é ambíguo.
    INVALID_ENVIRONMENT = "invalid_environment"
    #: O processo excedeu `timeout_seconds`; a árvore foi encerrada.
    TIMEOUT = "timeout"
    #: O `cancel_token` pediu o cancelamento; a árvore foi encerrada (ou nem nasceu).
    CANCELLED = "cancelled"
    #: O Supervisor não conseguiu conter, observar ou confirmar a árvore morta. Fail closed.
    SUPERVISION_FAILED = "supervision_failed"


class TestRunnerFailure(Exception):
    """Falha estruturada do `TestRunner`. A mensagem é **só** o código.

    Nunca carrega `stdout`/`stderr`, caminho absoluto, valor de ambiente nem texto livre de
    exceção. ``tree_confirmed_dead`` repete o fato do Supervisor quando houve processo
    (`None` quando nenhum processo chegou a ser iniciado pelo runner).
    """

    __test__ = False

    def __init__(
        self, code: TestRunnerFailureCode, *, tree_confirmed_dead: bool | None = None
    ) -> None:
        require_instance("code", code, TestRunnerFailureCode)
        if tree_confirmed_dead is not None and not isinstance(tree_confirmed_dead, bool):
            raise ContractViolation("tree_confirmed_dead precisa ser bool ou None")
        super().__init__(code.value)
        self.code = code
        self.tree_confirmed_dead = tree_confirmed_dead

    def __repr__(self) -> str:
        return f"TestRunnerFailure({self.code.value})"


# ---------------------------------------------------------------------------- resultados


@dataclass(frozen=True, slots=True, kw_only=True)
class RunReport:
    """Identificação, uso e duração comuns a `AgentRunResult` e `AuditResult` ([05] §6)."""

    status: RunStatus
    provider: str
    provider_adapter: str
    adapter_version: str
    model: str
    transport: str
    input_tokens: int | None
    output_tokens: int | None
    token_source: TokenSource
    duration_ms: int
    summary: str
    error_summary: str | None = None
    log_ref: str | None = None

    def __post_init__(self) -> None:
        require_instance("status", self.status, RunStatus)
        for name in ("provider", "provider_adapter", "adapter_version", "model", "transport"):
            require_text(name, getattr(self, name))
        require_optional_int("input_tokens", self.input_tokens)
        require_optional_int("output_tokens", self.output_tokens)
        require_instance("token_source", self.token_source, TokenSource)
        # Tokens nulos vêm com `token_source` ([05] §7): sem número, a origem é `unavailable`.
        has_tokens = self.input_tokens is not None or self.output_tokens is not None
        if has_tokens == (self.token_source is TokenSource.UNAVAILABLE):
            raise ContractViolation("token_source=unavailable ⇔ nenhum contador de tokens")
        require_int("duration_ms", self.duration_ms)
        require_text("summary", self.summary, allow_empty=True)
        require_optional_text("error_summary", self.error_summary)
        require_optional_text("log_ref", self.log_ref)


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentRunResult(RunReport):
    """`files_read=None` significa indisponível; `()` significa "leu zero arquivos"."""

    files_read: tuple[str, ...] | None
    files_read_source: FilesReadSource
    #: Addendum E8.2: `ok` ⇒ `None`; `limit_exceeded` ⇒ `blocked` (o provider parou antes de
    #: continuar). Demais status podem vir com `None`. Não entra no fingerprint (é runtime).
    failure_reason: RunFailureReason | None = None

    def __post_init__(self) -> None:
        RunReport.__post_init__(self)
        require_instance("files_read_source", self.files_read_source, FilesReadSource)
        if self.files_read is not None:
            require_text_tuple("files_read", self.files_read)
        if (self.files_read is None) != (self.files_read_source is FilesReadSource.UNAVAILABLE):
            raise ContractViolation("files_read=None ⇔ files_read_source=unavailable")
        if self.failure_reason is not None:
            require_instance("failure_reason", self.failure_reason, RunFailureReason)
            if self.status is RunStatus.OK:
                raise ContractViolation("status=ok não carrega failure_reason")
            if (
                self.failure_reason is RunFailureReason.LIMIT_EXCEEDED
                and self.status is not RunStatus.BLOCKED
            ):
                raise ContractViolation("failure_reason=limit_exceeded exige status=blocked")


@dataclass(frozen=True, slots=True)
class AuditFindingDraft:
    """Um achado devolvido pelo auditor ([02] §`AuditFinding`, sem os campos de persistência)."""

    severity: FindingSeverity
    category: str
    file: str | None
    line: int | None
    summary: str
    detail: str | None = None

    def __post_init__(self) -> None:
        require_instance("severity", self.severity, FindingSeverity)
        require_text("category", self.category)
        require_optional_text("file", self.file)
        require_optional_int("line", self.line, minimum=1)
        require_text("summary", self.summary)
        require_optional_text("detail", self.detail)


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditResult(RunReport):
    verdict: AuditVerdict
    findings: tuple[AuditFindingDraft, ...]

    def __post_init__(self) -> None:
        RunReport.__post_init__(self)
        require_instance("verdict", self.verdict, AuditVerdict)
        if not isinstance(self.findings, tuple):
            raise ContractViolation("findings precisa ser tuple")
        for finding in self.findings:
            require_instance("findings[]", finding, AuditFindingDraft)
        if self.verdict is AuditVerdict.PASS_WITH_FINDINGS and not self.findings:
            raise ContractViolation("pass_with_findings exige ao menos um finding")
