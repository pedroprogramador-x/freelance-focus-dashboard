"""Contrato de "Tests and Results" (E8.4.4) — **puro**: sem IO, banco, processo ou relógio próprio.

Depois do Run do Developer fechado `ok` (E8.4.3), a tentativa tem **uma** passagem de
verificação + testes, com um Run próprio (`agent = test_runner`, `invocation_id =
<run de controle>:test_runner`). Este módulo fixa os vocabulários, os dados neutros que cruzam as
portas do composition root e as **decisões puras** que traduzem os fatos em fechamento do Run,
`SafetyEvent` e fatos estruturados para a E8.4.5. O Orchestrator não importa `agent_runtime` nem
`execution_verification` ([01] §2): o composition root traduz os tipos deles para os daqui.

## Ordem (aprovada na E8.4.4)

1. pré-condições e o Run do Developer conferido **no banco** (identidade, status, vínculo);
2. **claim** do Run do Test Runner (`INSERT` `UNIQUE`, `COMMIT` antes de qualquer outra coisa);
3. binding do Run do Test Runner estabelecido e conferido;
4. **verificação** pós-Developer, **uma única vez** (o snapshot da árvore principal é de uso
   único); só depois do claim durável;
5. `VERIFIED` → métricas do diff → (cancelamento, prazo e binding ainda válidos) → `TestPolicy`
   aprovada, **uma** execução;
6. fechamento do Run, `SafetyEvent`s e liberação ou revogação do binding.

Verificação reprovada ou incompleta ⇒ o processo de teste **não** nasce (`RunnerOutcome.NOT_RUN`).
Abrir o Run não significa que o processo rodou: a prova disso é ``process_started``.

## Integridade (aprovado)

`out_of_worktree_write`, `integrity_violation`, `outside_workspace_change` e `path_policy_denied`
⇒ `VIOLATED`; `unverifiable` ⇒ `UNVERIFIABLE`. Nenhum dos dois pode terminar em sucesso. Os
`SafetyEvent`s usam os códigos já previstos nos contratos e **não atribuem** ao Developer uma
alteração externa cuja autoria não está comprovada.

## Prazo e cancelamento com causa

O Test Runner recebe um único sinal (`AttemptSignal`) que combina o cancelamento externo e o prazo
efetivo da operação e **registra a causa** da primeira parada: prazo ⇒ `timeout`; cancelamento
externo ⇒ `cancelled`. A `TestPolicy` aprovada e seus hashes não são tocados.

## Claim por identidade completa (como E843-AUD-001)

`invocation_id` é `UNIQUE`, mas a unicidade não diz *de quem* é o Run. Só o Run que passa em
`runner_claim_compatible` é o claim deste Test Runner; qualquer outro é `COLLISION`, sem adotar
id, status ou dado do Run alheio.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from app.db.enums import RunAgent, RunPurpose, RunStatus, SafetyEventKind
from app.orchestrator.developer_invocation_contract import BindingOutcome, Finalization
from app.orchestrator.execution_contract import (
    COMPONENT_TEST_RUNNER,
    IntegrityVerdict,
    RunnerOutcome,
    component_invocation_id,
)
from app.orchestrator.workspace_contract import PreparationCode
from app.safety.test_policy import TestPolicy
from app.safety.types import ObjectIdentity

# ------------------------------------------------------------------------------ vocabulários


class VerifyStatus(str, Enum):
    """Desfecho **local** da passagem. Não é `TaskStatus` nem `RunStatus`."""

    #: Run do Test Runner fechado `ok` (processo rodou, `exit_code == 0`, tudo coerente).
    COMPLETED = "completed"
    #: Run reivindicado e fechado **não-ok** (inclusive testes falhos e verificação reprovada).
    FAILED = "failed"
    #: Nada foi reivindicado: pré-condição, política ou prazo recusou antes do Run.
    REFUSED = "refused"
    #: Já existe Run para este `invocation_id`: nada foi verificado nem executado de novo.
    ALREADY_CLAIMED = "already_claimed"
    #: Run reivindicado e o fechamento/encerramento não se provou: recuperação é da E8.4.5.
    UNFINALIZED = "unfinalized"


class VerifyCode(str, Enum):
    """Causa estável. Vai para `Run.error_summary` prefixada; nunca texto livre de exceção."""

    TESTS_PASSED = "tests_passed"
    TESTS_FAILED = "tests_failed"
    REFUSED_FACTS = "refused_facts"
    DEVELOPER_NOT_COMPLETED = "developer_not_completed"
    CANCELLED_BEFORE_START = "cancelled_before_start"
    DEADLINE_EXHAUSTED = "deadline_exhausted"
    RUNNER_UNSUPPORTED = "runner_unsupported"
    CLAIM_REFUSED = "claim_refused"
    CLAIM_COLLISION = "claim_collision"
    ALREADY_CLAIMED = "already_claimed"
    CLAIM_FAILED = "claim_failed"
    BINDING_REFUSED = "binding_refused"
    BINDING_REVOKED = "binding_revoked"
    VERIFICATION_VIOLATED = "verification_violated"
    VERIFICATION_UNVERIFIABLE = "verification_unverifiable"
    DIFF_CHANGED_SINCE_VERIFIED = "diff_changed_since_verified"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    RUNNER_FAILURE = "runner_failure"
    SUPERVISION_FAILED = "supervision_failed"
    REPORT_CONTRADICTORY = "report_contradictory"
    CLOSE_FAILED = "close_failed"
    INTERRUPTED = "interrupted"
    INTERNAL_ERROR = "internal_error"


class VerificationStatus(str, Enum):
    """Espelho neutro de `PostExecutionStatus` (E8.3). Só `VERIFIED` libera."""

    VERIFIED = "verified"
    OUT_OF_WORKTREE_WRITE = "out_of_worktree_write"
    OUTSIDE_WORKSPACE_CHANGE = "outside_workspace_change"
    PATH_POLICY_DENIED = "path_policy_denied"
    INTEGRITY_VIOLATION = "integrity_violation"
    UNVERIFIABLE = "unverifiable"


class RunnerFailureCode(str, Enum):
    """Espelho neutro de `TestRunnerFailureCode` (E8.3)."""

    UNSUPPORTED_RUNNER = "unsupported_runner"
    INVALID_POLICY = "invalid_policy"
    WORKSPACE_UNAVAILABLE = "workspace_unavailable"
    EXECUTABLE_UNAVAILABLE = "executable_unavailable"
    INVALID_ENVIRONMENT = "invalid_environment"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    SUPERVISION_FAILED = "supervision_failed"


class StopCause(str, Enum):
    """Quem parou a operação. Registrado pelo `AttemptSignal` na **primeira** parada."""

    DEADLINE = "deadline"
    CANCEL = "cancel"


#: O sinal do `VerificationFailure`/`DiffMeasureFailure` que significa "o prazo/limite/cancelamento
#: esgotou" (e não "a worktree está comprovadamente errada").
BUDGET_FAILURES: frozenset[str] = frozenset({"limit_exceeded", "deadline_exceeded", "cancelled"})

#: Teto de `SafetyEvent`s de caminho por verificação (o resto fica no resumo).
MAX_FINDING_EVENTS = 20

RULE_PREFIX = "orchestrator.verify_and_test."
RULE_MAIN_TREE = RULE_PREFIX + "main_tree_changed"
RULE_OUTSIDE_WORKSPACE = RULE_PREFIX + "outside_workspace_change"
RULE_INTEGRITY = RULE_PREFIX + "integrity_violation"
RULE_BUDGET = RULE_PREFIX + "verification_budget"

_CODE_RE = re.compile(r"[a-z0-9_]{1,64}")
_MAX_SUMMARY = 500
_MAX_FILES = 2_000


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} precisa ser texto não vazio")
    return value


def _opt_int(name: str, value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} precisa ser int >= 0 ou None")
    return value


# ------------------------------------------------------------------------- sinal com causa


class AttemptSignal:
    """O `is_cancelled` do Test Runner / da verificação: cancelamento externo **ou** prazo, com a
    causa da primeira parada.

    Fail closed: um sinal externo que levanta ou devolve algo que não seja exatamente `False`
    conta como cancelamento. Cancelamento externo vence prazo no mesmo instante (a intenção do
    operador é o fato mais forte). A causa, uma vez registrada, nunca muda.
    """

    def __init__(
        self,
        external: Callable[[], bool],
        *,
        deadline_at: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(external) or not callable(clock):
            raise TypeError("external e clock precisam ser callables")
        self._external = external
        self._deadline_at = deadline_at
        self._clock = clock
        self._lock = threading.Lock()
        self._cause: StopCause | None = None

    def __repr__(self) -> str:
        return "<AttemptSignal>"

    @property
    def cause(self) -> StopCause | None:
        return self._cause

    def __call__(self) -> bool:
        with self._lock:
            if self._cause is not None:
                return True
            try:
                cancelled = self._external() is not False
            except Exception:
                cancelled = True
            if cancelled:
                self._cause = StopCause.CANCEL
                return True
            try:
                expired = self._clock() >= self._deadline_at
            except Exception:
                expired = True
            if expired:
                self._cause = StopCause.DEADLINE
                return True
            return False

    def remaining_s(self) -> float:
        try:
            return max(0.0, self._deadline_at - self._clock())
        except Exception:
            return 0.0


# --------------------------------------------------------------------------- dados neutros


@dataclass(frozen=True, slots=True)
class RunnerIdentity:
    """Quem executa os testes, em valores para o Run. Comparado, nunca adotado."""

    runner_id: str
    version: str
    provider: str = "system"
    transport: str = "process"

    def __post_init__(self) -> None:
        for name in ("runner_id", "version", "provider", "transport"):
            _text(name, getattr(self, name))
        if self.transport != "process":
            raise ValueError("o Test Runner V1 roda como processo local")


@dataclass(frozen=True, slots=True)
class FindingFact:
    """Um caminho que impede `VERIFIED`, em valores neutros."""

    path: str
    kind: str
    status: VerificationStatus
    rule_id: str

    def __post_init__(self) -> None:
        _text("path", self.path)
        _text("kind", self.kind)
        _text("rule_id", self.rule_id)
        if self.status is VerificationStatus.VERIFIED:
            raise ValueError("um finding nunca é `verified`")


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """O fato da verificação pós-Developer. ``handle`` é opaco (volta à porta para a medição)."""

    status: VerificationStatus
    reason: str | None = None
    failure: str | None = None
    findings: tuple[FindingFact, ...] = ()
    handle: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.status, VerificationStatus):
            raise ValueError("status inválido")
        if self.status is VerificationStatus.VERIFIED:
            if self.reason is not None or self.failure is not None or self.findings:
                raise ValueError("`verified` não carrega reason, failure nem findings")
        elif self.reason is None or _CODE_RE.fullmatch(self.reason) is None:
            raise ValueError("resultado que bloqueia precisa de reason (código estável)")
        if self.failure is not None and _CODE_RE.fullmatch(self.failure) is None:
            raise ValueError("failure precisa ser um código estável")


@dataclass(frozen=True, slots=True)
class MetricsReport:
    """As métricas do diff — **todas** ou **nenhuma**. ``failure`` explica a ausência."""

    files_changed: tuple[str, ...] | None = None
    diff_added: int | None = None
    diff_removed: int | None = None
    failure: str | None = None
    #: A worktree mudou depois de verificada (não é só "inviável de medir").
    changed_since_verified: bool = False
    #: A ausência das métricas NÃO esconde divergência: a integridade do verificado foi
    #: revalidada (binário, grande demais, caminhos demais). Falso (o padrão) em qualquer outra
    #: falha — orçamento, cancelamento, leitura que não prova —, que bloqueia a progressão.
    integrity_proven: bool = False

    def __post_init__(self) -> None:
        values = (self.files_changed, self.diff_added, self.diff_removed)
        if any(v is None for v in values) and not all(v is None for v in values):
            raise ValueError("métricas: todas medidas ou nenhuma")
        _opt_int("diff_added", self.diff_added)
        _opt_int("diff_removed", self.diff_removed)
        if self.files_changed is not None:
            if len(self.files_changed) > _MAX_FILES or len(set(self.files_changed)) != len(
                self.files_changed
            ):
                raise ValueError("files_changed inválido")
            if not all(isinstance(p, str) and p for p in self.files_changed):
                raise ValueError("files_changed precisa conter só caminhos não vazios")
            if self.failure is not None or self.changed_since_verified:
                raise ValueError("métricas medidas não carregam falha")
        else:
            if self.integrity_proven and self.changed_since_verified:
                raise ValueError("divergência não é integridade comprovada")
            if self.failure is None or _CODE_RE.fullmatch(self.failure) is None:
                raise ValueError("métricas ausentes precisam de uma causa estável")

    @property
    def measured(self) -> bool:
        return self.files_changed is not None

    @property
    def blocks(self) -> bool:
        """Sem métricas **e** sem integridade comprovada: nada pode seguir."""
        return not self.measured and not self.integrity_proven


@dataclass(frozen=True, slots=True)
class VerificationInput:
    """O que a porta de verificação recebe. ``snapshot`` é o da E8.3 (opaco, de uso único)."""

    workspace_id: str
    workspace_path: str = field(repr=False)
    workspace_prefix: str
    root_identity: ObjectIdentity
    base_commit: str
    snapshot: object = field(repr=False, compare=False)
    is_cancelled: Callable[[], bool] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class MeasureInput:
    handle: object = field(repr=False, compare=False)
    workspace_path: str = field(repr=False)
    workspace_prefix: str
    base_commit: str
    is_cancelled: Callable[[], bool] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class RunnerInput:
    """O que o serviço entrega à porta do Test Runner. A ``test_policy`` é a **aprovada** (relida
    do banco e conferida pelo fingerprint); o prazo efetivo viaja em ``is_cancelled``."""

    task_id: str
    run_id: str
    invocation_id: str
    workspace_id: str
    base_commit: str
    test_policy: TestPolicy = field(repr=False)
    is_cancelled: Callable[[], bool] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class RunnerReport:
    """O fato do Test Runner: **ou** um processo que terminou (``exit_code``), **ou** uma falha
    estruturada (``failure``). Nunca os dois, nunca nenhum. Sem `stdout`/`stderr`."""

    identity: RunnerIdentity
    exit_code: int | None = None
    framework: str | None = None
    passed: int | None = None
    failed: int | None = None
    skipped: int | None = None
    duration_ms: int = 0
    failure: RunnerFailureCode | None = None
    #: `True` = árvore confirmada morta; `False` = não confirmada; `None` = nenhum processo.
    tree_confirmed_dead: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RunnerIdentity):
            raise ValueError("identity inválida")
        if (self.exit_code is None) == (self.failure is None):
            raise ValueError("ou exit_code ou failure, exatamente um")
        if self.exit_code is not None:
            if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
                raise ValueError("exit_code precisa ser int")
            _text("framework", self.framework)
            counters = (self.passed, self.failed, self.skipped)
            if any(c is None for c in counters) and not all(c is None for c in counters):
                raise ValueError("passed/failed/skipped: todos int ou todos None")
            for name, value in zip(("passed", "failed", "skipped"), counters, strict=True):
                _opt_int(name, value)
        elif not isinstance(self.failure, RunnerFailureCode):
            raise ValueError("failure inválida")
        _opt_int("duration_ms", self.duration_ms)
        if self.tree_confirmed_dead is not None and not isinstance(self.tree_confirmed_dead, bool):
            raise ValueError("tree_confirmed_dead precisa ser bool ou None")


class VerificationPort(Protocol):
    """A verificação pós-Developer (E8.3) e a medição do diff — composition root. Não levanta por
    estado do disco; pode levantar por defeito (o serviço trata como não verificável)."""

    def verify(self, request: VerificationInput) -> VerificationReport: ...

    def measure(self, request: MeasureInput) -> MetricsReport: ...


class RunnerPort(Protocol):
    """Quem de fato executa a `TestPolicy` (composition root). **Bloqueante**; pode levantar."""

    def identity(self) -> RunnerIdentity: ...

    def run(self, request: RunnerInput) -> RunnerReport: ...


# ---------------------------------------------------------------- fatos, claim e fechamento


@dataclass(frozen=True, slots=True)
class VerifyFacts:
    """O que a releitura do banco entrega ao serviço. Valores, nunca ORM (exceto a política, que
    é um valor imutável da safety). ``code`` é a primeira incoerência (`None` = elegível)."""

    code: PreparationCode | None
    task_id: str
    control_run_id: str
    workspace_id: str | None = None
    base_commit: str | None = None
    task_version: int | None = None
    control_worktree_path: str | None = field(default=None, repr=False)
    run_started_at: datetime | None = None
    attempt_index: int | None = None
    fix_round: int | None = None
    run_timeout_s: int | None = None
    task_timeout_s: int | None = None
    context_manifest_id: str | None = None
    developer_run_id: str | None = None
    test_policy: TestPolicy | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class RunnerRunSpec:
    """O que o claim grava. Tudo já validado contra o banco antes de chegar aqui."""

    invocation_id: str
    control_run_id: str
    developer_run_id: str
    context_manifest_id: str
    identity: RunnerIdentity
    base_commit: str
    worktree_path: str = field(repr=False)
    expected_task_version: int = 0
    attempt_index: int = 0
    fix_round: int = 0
    started_at: datetime | None = None


def runner_claim_compatible(existing: Any, spec: RunnerRunSpec, task_id: str) -> bool:
    """O Run existente é **o claim deste Test Runner** (e não de outro componente)?

    ``existing`` é um `ExistingRunFacts`. Todas as condições, sem exceção: `invocation_id` igual e
    derivado do Run de controle (`<controle>:test_runner`); mesma task; `agent = test_runner`;
    `purpose = execution`; mesma tentativa; mesma base, mesmo manifest e **mesma worktree**; sem
    `subject_run_id` nem `supersedes_run_id`; mesma identidade de runner (provider, adapter =
    `runner_id`, versão, transporte `process`, sem modelo); vínculo com o Run de controle
    conferido pelo banco.
    """
    try:
        derived = component_invocation_id(spec.control_run_id, COMPONENT_TEST_RUNNER)
    except ValueError:
        return False
    return (
        existing.invocation_id == spec.invocation_id == derived
        and existing.task_id == task_id
        and existing.agent == RunAgent.TEST_RUNNER.value
        and existing.purpose == RunPurpose.EXECUTION.value
        and existing.attempt_index == spec.attempt_index
        and existing.fix_round == spec.fix_round
        and existing.base_commit == spec.base_commit
        and existing.context_manifest_id == spec.context_manifest_id
        and existing.subject_run_id is None
        and existing.supersedes_run_id is None
        and existing.provider == spec.identity.provider
        and existing.provider_adapter == spec.identity.runner_id
        and existing.adapter_version == spec.identity.version
        and existing.model is None
        and existing.transport == spec.identity.transport
        and existing.worktree_path == spec.worktree_path
        and existing.control_linked
    )


@dataclass(frozen=True, slots=True)
class VerifyEvent:
    """Um `SafetyEvent` da verificação, antes de ter `run_id`: o fechamento o associa ao Run
    **reivindicado**. ``kind`` é um `SafetyEventKind` já existente."""

    kind: SafetyEventKind
    rule_id: str
    subject: str
    detail: str

    def __post_init__(self) -> None:
        _text("rule_id", self.rule_id)
        _text("subject", self.subject)
        _text("detail", self.detail)


@dataclass(frozen=True, slots=True)
class RunnerClosure:
    """O fechamento do Run do Test Runner: status final + `test_summary` + eventos, numa transação.

    ``test_summary`` é o `TestSummary` ([05] §6), **sem** saída de processo (`output_ref = None`).
    """

    status: RunStatus
    finished_at: datetime
    duration_ms: int
    test_summary: dict[str, Any] | None
    summary: str | None
    error_summary: str | None
    events: tuple[VerifyEvent, ...] = ()

    def __post_init__(self) -> None:
        if self.status is RunStatus.RUNNING:
            raise ValueError("fechar não é `running`")


# ------------------------------------------------------------------------------ decisões


@dataclass(frozen=True, slots=True)
class VerifyDecision:
    """A decisão pura sobre o fechamento, a partir dos fatos já observados.

    ``normal_end`` = o processo de teste terminou normalmente (`ok` ou testes falhos) com a
    integridade verificada e a árvore confirmada: o binding é **liberado**; qualquer outra coisa o
    **revoga**. ``requires_finalization`` = algo (processo, árvore) não se provou encerrado.
    """

    run_status: RunStatus
    code: VerifyCode
    integrity: IntegrityVerdict
    runner_outcome: RunnerOutcome
    normal_end: bool = False
    requires_finalization: bool = False

    def __post_init__(self) -> None:
        if self.normal_end and self.runner_outcome not in (
            RunnerOutcome.PASSED,
            RunnerOutcome.FAILED,
        ):
            raise ValueError("fim normal só com testes que rodaram")
        if self.normal_end and self.integrity is not IntegrityVerdict.VERIFIED:
            raise ValueError("fim normal exige integridade verificada")
        if self.run_status is RunStatus.OK and (
            self.runner_outcome is not RunnerOutcome.PASSED
            or self.integrity is not IntegrityVerdict.VERIFIED
            or self.requires_finalization
            or not self.normal_end
        ):
            raise ValueError("`ok` exige testes aprovados, integridade verificada e nada pendente")


def integrity_of(status: VerificationStatus) -> IntegrityVerdict:
    """`VERIFIED` ⇒ verificado; violação comprovada ⇒ `VIOLATED`; o resto ⇒ `UNVERIFIABLE`."""
    if status is VerificationStatus.VERIFIED:
        return IntegrityVerdict.VERIFIED
    if status is VerificationStatus.UNVERIFIABLE:
        return IntegrityVerdict.UNVERIFIABLE
    return IntegrityVerdict.VIOLATED


def decide_verification_failure(
    report: VerificationReport | None, cause: StopCause | None
) -> VerifyDecision:
    """O Run quando a verificação **não** liberou (ou nem rodou). Nunca sucesso; o processo de
    teste não nasce (`NOT_RUN`).

    ``report is None`` = a porta levantou: não verificável. Violação comprovada ⇒ `blocked`.
    Não verificável ⇒ o status do Run segue a **causa** quando houve parada (prazo ⇒ `timeout`,
    cancelamento ⇒ `cancelled`), senão `error`; a integridade fica `UNVERIFIABLE` em todos.
    """
    if report is not None and report.status is not VerificationStatus.UNVERIFIABLE:
        return VerifyDecision(
            RunStatus.BLOCKED,
            VerifyCode.VERIFICATION_VIOLATED,
            IntegrityVerdict.VIOLATED,
            RunnerOutcome.NOT_RUN,
        )
    return _unverifiable(cause, report.failure if report is not None else None)


def _unverifiable(cause: StopCause | None, failure: str | None) -> VerifyDecision:
    if cause is StopCause.DEADLINE or failure == "deadline_exceeded":
        status, code = RunStatus.TIMEOUT, VerifyCode.TIMEOUT
    elif cause is StopCause.CANCEL or failure == "cancelled":
        status, code = RunStatus.CANCELLED, VerifyCode.CANCELLED
    else:
        status, code = RunStatus.ERROR, VerifyCode.VERIFICATION_UNVERIFIABLE
    return VerifyDecision(status, code, IntegrityVerdict.UNVERIFIABLE, RunnerOutcome.NOT_RUN)


def decide_not_verified(code: VerifyCode, cause: StopCause | None) -> VerifyDecision:
    """O Run quando algo anterior à verificação parou a passagem (binding recusado, parada)."""
    if cause is StopCause.DEADLINE or code is VerifyCode.DEADLINE_EXHAUSTED:
        return _unverifiable(StopCause.DEADLINE, None)
    if cause is StopCause.CANCEL or code is VerifyCode.CANCELLED_BEFORE_START:
        return _unverifiable(StopCause.CANCEL, None)
    return VerifyDecision(
        RunStatus.ERROR if code is not VerifyCode.BINDING_REVOKED else RunStatus.BLOCKED,
        code,
        IntegrityVerdict.UNVERIFIABLE,
        RunnerOutcome.NOT_RUN,
    )


def decide_stop_after_verification(code: VerifyCode, cause: StopCause | None) -> VerifyDecision:
    """O Run quando a verificação **passou** e algo parou a passagem antes do processo (prazo,
    cancelamento, binding que deixou de valer). A integridade continua `VERIFIED`; o processo de
    teste não nasceu, mas o resultado carrega a causa (`timeout`/`cancelled`/falha técnica) para
    que a classificação da tentativa não veja um "não rodou" sem explicação."""
    verified = IntegrityVerdict.VERIFIED
    if cause is StopCause.DEADLINE or code is VerifyCode.DEADLINE_EXHAUSTED:
        return VerifyDecision(
            RunStatus.TIMEOUT, VerifyCode.TIMEOUT, verified, RunnerOutcome.TIMEOUT
        )
    if cause is StopCause.CANCEL or code is VerifyCode.CANCELLED_BEFORE_START:
        return VerifyDecision(
            RunStatus.CANCELLED, VerifyCode.CANCELLED, verified, RunnerOutcome.CANCELLED
        )
    return VerifyDecision(
        RunStatus.BLOCKED if code is VerifyCode.BINDING_REVOKED else RunStatus.ERROR,
        code,
        verified,
        RunnerOutcome.TECHNICAL_FAILURE,
    )


def decide_measurement_unproven(failure: str | None, cause: StopCause | None) -> VerifyDecision:
    """A medição não revalidou o verificado (orçamento, cancelamento, leitura que não prova): a
    integridade volta a `UNVERIFIABLE` e o processo de teste não nasce. O status do Run segue a
    causa quando houve parada (prazo ⇒ `timeout`, cancelamento ⇒ `cancelled`)."""
    return _unverifiable(cause, failure)


def decide_changed_since_verified() -> VerifyDecision:
    """O conteúdo medido não é o verificado: a worktree mudou **depois** da verificação, sem
    ninguém rodando. Não se confia nela: integridade `UNVERIFIABLE`, nenhum teste."""
    return VerifyDecision(
        RunStatus.ERROR,
        VerifyCode.DIFF_CHANGED_SINCE_VERIFIED,
        IntegrityVerdict.UNVERIFIABLE,
        RunnerOutcome.NOT_RUN,
    )


def decide_after_runner(
    report: RunnerReport | None,
    *,
    cause: StopCause | None,
    binding_verified: bool,
) -> VerifyDecision:
    """O Run depois que o processo de teste **foi chamado** (``report is None`` = a porta levantou:
    o processo pode ter nascido e não se provou encerrado ⇒ pendente).

    Exit normal (`0`/`!=0`) é autoritativo; falha estruturada é mapeada pela causa registrada no
    sinal (prazo ⇒ `timeout`, cancelamento externo ⇒ `cancelled`; `cancelled` sem causa é relatório
    contraditório). Binding que não resolve mais ao fim nunca fecha `ok`. Árvore não confirmada
    (`False`/`None` onde há processo) ⇒ ``requires_finalization``.
    """
    verified = IntegrityVerdict.VERIFIED
    if report is None:
        return VerifyDecision(
            RunStatus.ERROR,
            VerifyCode.INTERNAL_ERROR,
            verified,
            RunnerOutcome.TECHNICAL_FAILURE,
            requires_finalization=True,
        )

    if report.failure is None:
        # Processo terminou. Duas evidências independentes, nenhuma apaga a outra: binding perdido
        # ao fim (não confiar no resultado) e árvore contradita (pendência de finalização).
        if not binding_verified:
            return VerifyDecision(
                RunStatus.BLOCKED,
                VerifyCode.BINDING_REVOKED,
                verified,
                RunnerOutcome.TECHNICAL_FAILURE,
                requires_finalization=report.tree_confirmed_dead is False,
            )
        if report.tree_confirmed_dead is False:
            return VerifyDecision(
                RunStatus.ERROR,
                VerifyCode.SUPERVISION_FAILED,
                verified,
                RunnerOutcome.TECHNICAL_FAILURE,
                requires_finalization=True,
            )
        if report.exit_code == 0:
            return VerifyDecision(
                RunStatus.OK,
                VerifyCode.TESTS_PASSED,
                verified,
                RunnerOutcome.PASSED,
                normal_end=True,
            )
        return VerifyDecision(
            RunStatus.ERROR,
            VerifyCode.TESTS_FAILED,
            verified,
            RunnerOutcome.FAILED,
            normal_end=True,
        )

    failure = report.failure
    dead = report.tree_confirmed_dead
    if failure is RunnerFailureCode.TIMEOUT or (
        failure is RunnerFailureCode.CANCELLED and cause is StopCause.DEADLINE
    ):
        return VerifyDecision(
            RunStatus.TIMEOUT,
            VerifyCode.TIMEOUT,
            verified,
            RunnerOutcome.TIMEOUT,
            requires_finalization=dead is not True,
        )
    if failure is RunnerFailureCode.CANCELLED:
        if cause is StopCause.CANCEL:
            return VerifyDecision(
                RunStatus.CANCELLED,
                VerifyCode.CANCELLED,
                verified,
                RunnerOutcome.CANCELLED,
                requires_finalization=dead is False,
            )
        # `cancelled` sem que o nosso sinal tenha parado: não há como atribuir a causa.
        return VerifyDecision(
            RunStatus.ERROR,
            VerifyCode.REPORT_CONTRADICTORY,
            verified,
            RunnerOutcome.TECHNICAL_FAILURE,
            requires_finalization=dead is not True,
        )
    if failure is RunnerFailureCode.SUPERVISION_FAILED:
        return VerifyDecision(
            RunStatus.ERROR,
            VerifyCode.SUPERVISION_FAILED,
            verified,
            RunnerOutcome.TECHNICAL_FAILURE,
            requires_finalization=dead is not True,
        )
    # unsupported_runner, invalid_policy, workspace_unavailable, executable_unavailable,
    # invalid_environment: o processo nunca nasceu (nada a encerrar), salvo contradição.
    return VerifyDecision(
        RunStatus.ERROR,
        VerifyCode.RUNNER_FAILURE,
        verified,
        RunnerOutcome.TECHNICAL_FAILURE,
        requires_finalization=dead is False,
    )


def summary_of_report(report: RunnerReport) -> dict[str, Any] | None:
    """O `TestSummary` persistido ([05] §6) — só quando o processo terminou. Sem saída."""
    if report.exit_code is None or report.framework is None:
        return None
    return {
        "framework": report.framework,
        "exit_code": report.exit_code,
        "passed": report.passed,
        "failed": report.failed,
        "skipped": report.skipped,
        "duration_ms": report.duration_ms,
        "output_ref": None,
    }


def runner_events(decision: VerifyDecision) -> tuple[VerifyEvent, ...]:
    """`SafetyEvent` do processo de teste que estourou o prazo ou foi cancelado.

    Distingue o que está **provado** do que está **pendente**: com ``requires_finalization`` o
    evento diz que o encerramento da árvore de processos não foi comprovado (recuperação pendente);
    sem ele, só que não há pendência. Nunca afirma um encerramento que ninguém comprovou e nunca
    carrega texto de exceção.
    """
    pending = decision.requires_finalization
    suffix = "_unproven" if pending else ""
    tail = (
        "encerramento da árvore de processos não comprovado; recuperação pendente"
        if pending
        else "sem pendência de árvore de processos"
    )
    if decision.runner_outcome is RunnerOutcome.TIMEOUT:
        return (
            VerifyEvent(
                SafetyEventKind.TIMEOUT,
                RULE_PREFIX + "test_timeout" + suffix,
                "test_runner",
                "a execução dos testes excedeu o prazo efetivo; " + tail,
            ),
        )
    if decision.runner_outcome is RunnerOutcome.CANCELLED:
        return (
            VerifyEvent(
                SafetyEventKind.CANCELLED,
                RULE_PREFIX + "test_cancelled" + suffix,
                "test_runner",
                "a execução dos testes foi cancelada; " + tail,
            ),
        )
    return ()


def run_error_summary(code: VerifyCode) -> str:
    """Só o código estável — nunca texto livre de exceção, saída de processo ou caminho."""
    return f"verify_and_test:{code.value}"[:_MAX_SUMMARY]


def verification_events(
    report: VerificationReport | None, cause: StopCause | None
) -> tuple[VerifyEvent, ...]:
    """Os `SafetyEvent`s de uma verificação que não liberou, com os códigos já previstos.

    Nenhum evento nomeia o Developer nem afirma autoria: a árvore principal mudar durante a
    passagem é um fato do ambiente; quem escreveu não está comprovado. Caminhos são relativos à
    raiz do repositório da worktree (nunca absolutos). Limitado a `MAX_FINDING_EVENTS`.
    """
    if report is None or report.status is VerificationStatus.VERIFIED:
        return ()
    status = report.status
    if status is VerificationStatus.OUT_OF_WORKTREE_WRITE:
        return (
            VerifyEvent(
                SafetyEventKind.OUT_OF_WORKTREE_WRITE,
                RULE_MAIN_TREE,
                "main_tree",
                "a árvore principal difere do snapshot anterior à execução; autoria não comprovada",
            ),
        )
    if status is VerificationStatus.INTEGRITY_VIOLATION:
        reason = report.reason or "unknown"
        return (
            VerifyEvent(
                SafetyEventKind.TOCTOU_RECHECK_FAILED,
                f"{RULE_INTEGRITY}.{reason}",
                "worktree",
                f"a integridade da worktree não se provou ({reason})",
            ),
        )
    if status in (
        VerificationStatus.PATH_POLICY_DENIED,
        VerificationStatus.OUTSIDE_WORKSPACE_CHANGE,
    ):
        return tuple(
            VerifyEvent(
                SafetyEventKind.PATH_DENIED,
                RULE_OUTSIDE_WORKSPACE
                if finding.status is VerificationStatus.OUTSIDE_WORKSPACE_CHANGE
                else finding.rule_id,
                finding.path,
                "mudança na worktree rejeitada pela verificação pós-execução"
                f" ({finding.status.value}, {finding.kind})",
            )
            for finding in report.findings[:MAX_FINDING_EVENTS]
        )
    # UNVERIFIABLE: só orçamento/prazo/cancelamento viram evento; "não consegui ler" não prova nada.
    failure = report.failure
    if failure == "limit_exceeded":
        kind = SafetyEventKind.LIMIT_EXCEEDED
    elif failure == "deadline_exceeded" or cause is StopCause.DEADLINE:
        kind = SafetyEventKind.TIMEOUT
    elif failure == "cancelled" or cause is StopCause.CANCEL:
        kind = SafetyEventKind.CANCELLED
    else:
        return ()
    return (
        VerifyEvent(
            kind,
            RULE_BUDGET,
            "verification",
            f"a verificação pós-execução não concluiu ({failure or kind.value})",
        ),
    )


# ------------------------------------------------------------------------------ resultado


@dataclass(frozen=True, slots=True)
class VerifyAndTestResult:
    """O resultado estruturado. Não transiciona task e não fecha o Run de controle.

    Os fatos que a E8.4.5 consome: ``integrity``, ``runner_outcome``, ``metrics``,
    ``requires_finalization``. ``process_started`` diz se o processo de teste foi chamado — abrir o
    Run não é rodar testes.
    """

    status: VerifyStatus
    code: VerifyCode
    fact_code: PreparationCode | None = None
    run_id: str | None = None
    run_status: RunStatus | None = None
    process_started: bool = False
    verification: VerificationStatus | None = None
    integrity: IntegrityVerdict | None = None
    runner_outcome: RunnerOutcome | None = None
    metrics: MetricsReport | None = None
    exit_code: int | None = None
    binding: BindingOutcome = BindingOutcome.NOT_BOUND
    finalization: Finalization = Finalization.NOT_APPLICABLE
    safety_events: int = 0
    requires_finalization: bool = False

    def __post_init__(self) -> None:
        if self.status is VerifyStatus.COMPLETED and (
            self.code is not VerifyCode.TESTS_PASSED
            or self.run_status is not RunStatus.OK
            or self.integrity is not IntegrityVerdict.VERIFIED
            or self.runner_outcome is not RunnerOutcome.PASSED
            or not self.process_started
            or self.finalization not in (Finalization.CLOSED, Finalization.ALREADY_FINAL)
            or self.binding not in (BindingOutcome.RELEASED, BindingOutcome.REVOKED)
            or self.requires_finalization
        ):
            raise ValueError("`completed` exige testes aprovados, verificação ok e Run fechado")
        if self.process_started and self.runner_outcome is RunnerOutcome.NOT_RUN:
            raise ValueError("processo iniciado não é `not_run`")
        if (
            self.integrity is not IntegrityVerdict.VERIFIED
            and self.integrity is not None
            and self.process_started
        ):
            raise ValueError("sem integridade verificada o processo de teste não nasce")


__all__ = [
    "BUDGET_FAILURES",
    "MAX_FINDING_EVENTS",
    "RULE_BUDGET",
    "RULE_INTEGRITY",
    "RULE_MAIN_TREE",
    "RULE_OUTSIDE_WORKSPACE",
    "RULE_PREFIX",
    "AttemptSignal",
    "FindingFact",
    "MeasureInput",
    "MetricsReport",
    "RunnerClosure",
    "RunnerFailureCode",
    "RunnerIdentity",
    "RunnerInput",
    "RunnerPort",
    "RunnerReport",
    "RunnerRunSpec",
    "StopCause",
    "VerificationInput",
    "VerificationPort",
    "VerificationReport",
    "VerificationStatus",
    "VerifyAndTestResult",
    "VerifyCode",
    "VerifyDecision",
    "VerifyEvent",
    "VerifyFacts",
    "VerifyStatus",
    "decide_after_runner",
    "decide_changed_since_verified",
    "decide_measurement_unproven",
    "decide_not_verified",
    "decide_stop_after_verification",
    "decide_verification_failure",
    "integrity_of",
    "run_error_summary",
    "runner_claim_compatible",
    "runner_events",
    "summary_of_report",
    "verification_events",
]
