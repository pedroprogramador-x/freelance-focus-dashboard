"""Contrato da invocação do Developer (E8.4.3) — **puro**: sem IO, banco, relógio ou provider.

Fixa os vocabulários fechados, os dados neutros que cruzam a porta com o composition root e a
**decisão** que traduz o relatório do provider no fechamento do Run do Developer. Nada aqui
executa coisa alguma e nada aqui importa `agent_runtime` ou `tool_executor` (o Orchestrator recebe
essas camadas por injeção, [01] §2): o composition root traduz os tipos deles para os daqui.

## Uma invocação, no máximo uma chamada ao provider

O Run do Developer tem `invocation_id = <run de controle>:developer` ([E8.4.1]) e esse valor é
`UNIQUE` no banco. A **reivindicação** é o `INSERT` desse Run, commitado **antes** de qualquer
chamada ao provider: quem perde a corrida (ou chega depois, inclusive depois de um crash) vê o Run
existente e **não** chama o provider. Não há retry implícito: um Run que ficou `running` fica para
a recuperação durável da E8.4.5.

## Duas fontes de informação, nunca misturadas (E843-AUD-002)

* **metadados do provider** (status, identidade, tokens, resumo, `limit_exceeded`): sujeitos à
  validação de identidade e de consistência; uma contradição **impede o sucesso**;
* **evidência do executor** (negações do `DecisionJournal`, `files_read`, contagens): produzida pelo
  executor do Run e entregue a um `EvidenceCollector` **do serviço**, vinculado ao `run_id`. O
  relatório do provider **não carrega negações** — não há por onde forjá-las — e o coletor rejeita
  registro de outro Run e sequência incoerente. Uma contradição nos metadados **não apaga** a
  evidência do executor: as negações associadas viram `SafetyEvent` e uma violação crítica marca
  `integrity_violation` mesmo assim.

## Precedência da decisão (fail closed)

1. violação **crítica** comprovada pela evidência (`out_of_worktree_write`, `toctou_recheck_failed`)
   → `blocked`;
2. binding do Run não mais autorizado ao fim → `blocked`;
3. metadados do provider contraditórios, ou evidência inconsistente → `error`;
4. `ok` com executor não encerrado → `error`;
5. o status que o provider reportou (`limit_exceeded` → `blocked` + `SafetyEvent`).

Uma única negação de ferramenta (`path_denied`, `secret_access_blocked`, `capability_denied`,
`limit_exceeded` de ferramenta) **não** é violação global ([E8.4.1] §11): é `SafetyEvent` e o Run
segue o status do provider. Quem classifica a tentativa inteira é a E8.4.5.

## Um claim só vale se o Run existente for **deste** componente (E843-AUD-001)

`invocation_id` é `UNIQUE`, mas a unicidade não diz *de quem* é o Run. Um Run existente só é
reconhecido como claim legítimo (`ALREADY`) se a identidade completa for compatível com a
invocação esperada (`claim_compatible`); qualquer outra coisa é `COLLISION` — recusa explícita, sem
adotar id, status ou dado algum do Run alheio.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol

from app.db.enums import RunAgent, RunPurpose, RunStatus, TokenSource
from app.orchestrator.execution_contract import (
    COMPONENT_DEVELOPER,
    DeveloperOutcome,
    component_invocation_id,
)
from app.orchestrator.fingerprint import tool_profile_hash
from app.orchestrator.workspace_contract import PreparationCode
from app.safety.capability_verification import CapabilityBinding

# ------------------------------------------------------------------------------ vocabulários


class InvocationStatus(str, Enum):
    """Desfecho **local** da invocação. Não é `TaskStatus` nem `RunStatus`."""

    #: Provider concluiu `ok`, tudo coerente, Run do Developer fechado `ok`.
    COMPLETED = "completed"
    #: O provider foi chamado (ou o Run foi reivindicado) e o Run fechou **não-ok**.
    FAILED = "failed"
    #: Nada foi reivindicado: pré-condição, política ou integridade recusou antes do Run.
    REFUSED = "refused"
    #: Já existe Run para este `invocation_id`: o provider **não** foi chamado de novo.
    ALREADY_CLAIMED = "already_claimed"
    #: O Run foi reivindicado mas o fechamento persistente falhou: inconsistência declarada.
    UNFINALIZED = "unfinalized"


class InvocationCode(str, Enum):
    """Causa estável. Vai para `Run.error_summary` prefixada; nunca texto livre de exceção."""

    COMPLETED = "completed"
    REFUSED_FACTS = "refused_facts"
    CANCELLED_BEFORE_START = "cancelled_before_start"
    DEADLINE_EXHAUSTED = "deadline_exhausted"
    BINDING_NOT_APPROVED = "binding_not_approved"
    CAPABILITY_UNPROVEN = "capability_unproven"
    CAPABILITY_MISMATCH = "capability_mismatch"
    CONTEXT_ARTIFACT_INVALID = "context_artifact_invalid"
    CLAIM_REFUSED = "claim_refused"
    CLAIM_COLLISION = "claim_collision"
    ALREADY_CLAIMED = "already_claimed"
    CLAIM_FAILED = "claim_failed"
    BINDING_REFUSED = "binding_refused"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_EXCEPTION = "provider_exception"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_CANCELLED = "provider_cancelled"
    LIMIT_EXCEEDED = "limit_exceeded"
    CRITICAL_VIOLATION = "critical_violation"
    BINDING_REVOKED = "binding_revoked"
    REPORT_CONTRADICTORY = "report_contradictory"
    EXECUTOR_NOT_CLOSED = "executor_not_closed"
    CLOSE_FAILED = "close_failed"
    INTERRUPTED = "interrupted"
    INTERNAL_ERROR = "internal_error"


class ReportedStatus(str, Enum):
    """O status que o provider reportou, em vocabulário neutro (espelha o do DTO do provider)."""

    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class BindingOutcome(str, Enum):
    """O que aconteceu com o binding do Run do Developer."""

    NOT_BOUND = "not_bound"
    #: Fim normal de uso: liberado (não revogado).
    RELEASED = "released"
    #: Autorização revogada (falha, violação ou resultado contraditório).
    REVOKED = "revoked"
    #: A liberação/revogação não se provou. Exige recuperação (E8.4.5).
    RELEASE_FAILED = "release_failed"
    #: O registro foi recusado; nenhum token saiu.
    REFUSED = "refused"


class Finalization(str, Enum):
    """O fechamento persistente do Run do Developer."""

    NOT_APPLICABLE = "not_applicable"
    CLOSED = "closed"
    #: Outro escritor já o havia fechado (idempotência); o estado final é o do banco.
    ALREADY_FINAL = "already_final"
    #: O fechamento falhou; o Run pode seguir `running`.
    FAILED = "failed"


class ClaimOutcome(str, Enum):
    CLAIMED = "claimed"
    #: Já existe Run **deste** componente para este `invocation_id` (compatível).
    ALREADY = "already"
    REFUSED = "refused"
    #: Existe Run com este `invocation_id`, mas de OUTRO componente/tentativa/task: não é adotado.
    COLLISION = "collision"


class CloseOutcome(str, Enum):
    CLOSED = "closed"
    NOT_OPEN = "not_open"


#: Negações cujo valor é uma **violação de integridade** comprovada (não só uma recusa que
#: segurou a linha). Espelha `IntegrityVerdict.VIOLATED` ([E8.4.1] §11).
CRITICAL_DENIAL_CATEGORIES: frozenset[str] = frozenset(
    {"out_of_worktree_write", "toctou_recheck_failed"}
)

#: As categorias que o `DecisionJournal` pode entregar ([02] §12; iguais a `SafetyEventKind`).
KNOWN_DENIAL_CATEGORIES: frozenset[str] = frozenset(
    {
        "path_denied",
        "secret_access_blocked",
        "capability_denied",
        "limit_exceeded",
        "toctou_recheck_failed",
        "out_of_worktree_write",
    }
)

#: Regra do `SafetyEvent` do orçamento de tokens esgotado (sinal estruturado do provider).
LIMIT_RULE_ID = "orchestrator.developer_invocation.token_budget"

_STATUS_MAX_SUMMARY = 4000
_STATUS_MAX_ERROR = 500


def _text(name: str, value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{name} precisa ser texto{'' if allow_empty else ' não vazio'}")
    return value


def _opt_int(name: str, value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} precisa ser int >= 0 ou None")
    return value


# --------------------------------------------------------------------------- dados neutros


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    """Quem executa. Comparado com o `developer_binding` **aprovado**, nunca adotado dele."""

    provider: str
    adapter: str
    adapter_version: str
    model: str
    transport: str

    def __post_init__(self) -> None:
        for name in ("provider", "adapter", "adapter_version", "model", "transport"):
            _text(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class ToolDenial:
    """Uma negação do executor, neutra: o composition root a traduz do `ToolDecisionRecord`."""

    sequence: int
    run_id: str
    task_id: str
    invocation_id: str
    operation: str
    category: str
    rule_id: str
    subject_redacted: str
    reason: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise ValueError("sequence precisa ser int >= 1")
        for name in (
            "run_id",
            "task_id",
            "invocation_id",
            "operation",
            "rule_id",
            "subject_redacted",
            "reason",
        ):
            _text(name, getattr(self, name))
        if self.category not in KNOWN_DENIAL_CATEGORIES:
            raise ValueError("categoria de negação desconhecida")


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    """O que o coletor aceitou até agora. ``inconsistent`` = houve registro rejeitado (de outro Run
    ou fora de sequência): a evidência aceita continua valendo, mas o Run não pode fechar `ok`."""

    denials: tuple[ToolDenial, ...] = ()
    inconsistent: bool = False
    rejected: int = 0

    @property
    def critical(self) -> bool:
        """Há violação **crítica** comprovada pela evidência aceita?"""
        return any(d.category in CRITICAL_DENIAL_CATEGORIES for d in self.denials)


class EvidenceCollector:
    """O canal da evidência do **executor** — do serviço, vinculado a **um** `run_id`.

    Quem o alimenta é o `DecisionJournal` do executor do Run (composition root); o provider nunca o
    recebe, e o relatório dele não tem campo de negação. O coletor só aceita um registro cujo
    escopo — `run_id`, `task_id` e `invocation_id` — seja o da invocação e cuja `sequence`
    continue a anterior: registro de outro Run é
    **descartado** (não vira `SafetyEvent`), duplicata é descartada e lacuna é aceita mas marca a
    evidência como inconsistente (houve perda). Thread-safe: o executor roda na thread do provider.
    """

    def __init__(self, run_id: str, *, task_id: str, invocation_id: str) -> None:
        _text("run_id", run_id)
        _text("task_id", task_id)
        _text("invocation_id", invocation_id)
        self._run_id = run_id
        self._task_id = task_id
        self._invocation_id = invocation_id
        self._lock = threading.Lock()
        self._items: list[ToolDenial] = []
        self._next = 1
        self._inconsistent = False
        self._rejected = 0

    def __repr__(self) -> str:
        return "<EvidenceCollector>"

    def append(self, denial: ToolDenial) -> None:
        with self._lock:
            if not isinstance(denial, ToolDenial) or (
                denial.run_id,
                denial.task_id,
                denial.invocation_id,
            ) != (self._run_id, self._task_id, self._invocation_id):
                self._rejected += 1
                self._inconsistent = True
                return
            if denial.sequence != self._next:
                self._inconsistent = True
                if denial.sequence < self._next:
                    self._rejected += 1
                    return  # duplicata ou retrocesso: nunca vira um segundo evento
            self._items.append(denial)
            self._next = denial.sequence + 1

    def snapshot(self) -> EvidenceSnapshot:
        with self._lock:
            return EvidenceSnapshot(tuple(self._items), self._inconsistent, self._rejected)


@dataclass(frozen=True, slots=True)
class DeveloperInvocationInput:
    """O que o serviço entrega à porta. Nada aqui é escolhido pela invocação além do que já foi
    aprovado e relido do banco; ``is_cancelled`` é o sinal injetado da tentativa. ``evidence`` é o
    canal do **executor** (o journal dele o alimenta); o provider nunca o vê."""

    task_id: str
    run_id: str
    invocation_id: str
    control_run_id: str
    workspace_id: str
    base_commit: str
    context_manifest_id: str
    goal: str
    acceptance_criteria: tuple[str, ...]
    plan_steps: tuple[str, ...]
    rendered_context: str = field(repr=False)
    rendered_context_hash: str = field(repr=False)
    evidence: EvidenceCollector = field(repr=False, compare=False)
    max_total_tokens: int = 1
    timeout_s: int = 1
    is_cancelled: Callable[[], bool] = field(default=lambda: False, repr=False, compare=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class DeveloperInvocationReport:
    """O relatório neutro da porta.

    Campos do **provider** (sujeitos a validação): `status`, `identity`, `limit_exceeded`, tokens,
    `summary`, `error_summary`, `duration_ms`. Campos do **executor** (`files_read`, `operations`,
    `denial_count`, `executor_closed`): o provider não mede a própria atividade. **As negações não
    estão aqui**: chegam pelo `EvidenceCollector` do serviço, alimentado pelo journal do executor.
    """

    status: ReportedStatus
    #: Houve chamada ao provider? `False` = a porta falhou antes (executor indisponível…).
    provider_called: bool
    identity: ProviderIdentity
    limit_exceeded: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    token_source: str = "unavailable"  # noqa: S105 — enum de contabilidade, não segredo
    duration_ms: int = 0
    summary: str = ""
    error_summary: str | None = None
    #: `None` = indisponível (o executor nem chegou a existir).
    files_read: tuple[str, ...] | None = None
    operations: int = 0
    denial_count: int = 0
    #: O executor do Run foi encerrado (`close()`) e isso foi confirmado.
    executor_closed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.status, ReportedStatus) or not isinstance(
            self.identity, ProviderIdentity
        ):
            raise ValueError("status/identity inválidos")
        _opt_int("input_tokens", self.input_tokens)
        _opt_int("output_tokens", self.output_tokens)
        if self.token_source not in {s.value for s in TokenSource}:
            raise ValueError("token_source desconhecido")
        has_tokens = self.input_tokens is not None or self.output_tokens is not None
        if has_tokens == (self.token_source == TokenSource.UNAVAILABLE.value):
            raise ValueError("token_source=unavailable <=> nenhum contador de tokens")
        _opt_int("duration_ms", self.duration_ms)
        _opt_int("operations", self.operations)
        _opt_int("denial_count", self.denial_count)
        _text("summary", self.summary, allow_empty=True)
        if self.files_read is not None and not all(
            isinstance(p, str) and p for p in self.files_read
        ):
            raise ValueError("files_read precisa conter só caminhos não vazios")


class DeveloperRunPort(Protocol):
    """Quem de fato chama o provider (composition root). **Bloqueante**; pode levantar.

    ``identity`` é estático e vem do composition root; o serviço o compara com o binding
    aprovado antes de reivindicar o Run. ``invoke`` recebe só a entrada derivada do banco e do
    contexto preparado — nenhuma expectativa de capability, perfil ou binding.
    """

    def identity(self) -> ProviderIdentity: ...

    def invoke(self, request: DeveloperInvocationInput) -> DeveloperInvocationReport: ...


# ---------------------------------------------------------------- fatos, claim e fechamento


@dataclass(frozen=True, slots=True)
class InvocationFacts:
    """O que a releitura do banco entrega ao serviço. Valores, nunca ORM.

    ``code`` é a primeira incoerência (`None` = elegível). Tudo o que governa a chamada vem do
    que foi **aprovado** (`approved_fingerprint_parts`) e do plano, não de quem invoca.
    """

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
    rendered_context_hash: str | None = field(default=None, repr=False)
    goal: str | None = field(default=None, repr=False)
    acceptance_criteria: tuple[str, ...] = field(default=(), repr=False)
    approved_adapter: str | None = None
    approved_adapter_version: str | None = None
    approved_model: str | None = None
    approved_tool_profile_hash: str | None = None
    max_total_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class DeveloperRunSpec:
    """O que a reivindicação grava. Tudo já validado contra o aprovado antes de chegar aqui."""

    invocation_id: str
    control_run_id: str
    context_manifest_id: str
    identity: ProviderIdentity
    tool_profile_hash: str
    base_commit: str
    worktree_path: str = field(repr=False)
    expected_task_version: int = 0
    attempt_index: int = 0
    fix_round: int = 0
    started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """`REFUSED` e `COLLISION` **não** carregam id nem status: nada de um Run alheio vaza."""

    outcome: ClaimOutcome
    run_id: str | None = None
    #: Para `ALREADY`: o status do Run existente (`running` = reivindicado e não fechado).
    existing_status: RunStatus | None = None
    #: Para `REFUSED`: a incoerência encontrada sob o lock de escrita.
    code: PreparationCode | None = None

    def __post_init__(self) -> None:
        if self.outcome in (ClaimOutcome.REFUSED, ClaimOutcome.COLLISION) and (
            self.run_id is not None or self.existing_status is not None
        ):
            raise ValueError("recusa/colisão não carregam o id nem o status de nenhum Run")
        if self.outcome is ClaimOutcome.COLLISION and self.code is not None:
            raise ValueError("colisão não carrega código de incoerência")


@dataclass(frozen=True, slots=True)
class ExistingRunFacts:
    """A identidade do Run que **já** tem este `invocation_id`, em valores (nunca ORM)."""

    invocation_id: str
    task_id: str
    agent: str
    purpose: str
    attempt_index: int
    fix_round: int
    base_commit: str | None
    context_manifest_id: str | None
    subject_run_id: str | None
    supersedes_run_id: str | None
    provider: str
    provider_adapter: str
    adapter_version: str | None
    model: str | None
    transport: str
    #: O Run de controle da invocação existe, é da mesma task e da mesma tentativa.
    control_linked: bool


def claim_compatible(existing: ExistingRunFacts, spec: DeveloperRunSpec, task_id: str) -> bool:
    """O Run existente é **o claim deste Developer** (e não de outro componente)?

    Todas as condições, sem exceção: `invocation_id` igual e derivado do Run de controle
    (`<controle>:developer`); mesma task; `agent = developer`; `purpose = execution`; mesma
    tentativa (`attempt_index`/`fix_round`); mesma base e mesmo manifest; sem `subject_run_id`
    nem `supersedes_run_id` (não é Run de auditoria); mesma identidade de provider; e o vínculo com
    o Run de controle conferido pelo banco. Qualquer divergência é colisão.
    """
    try:
        derived = component_invocation_id(spec.control_run_id, COMPONENT_DEVELOPER)
    except ValueError:
        return False
    return (
        existing.invocation_id == spec.invocation_id == derived
        and existing.task_id == task_id
        and existing.agent == RunAgent.DEVELOPER.value
        and existing.purpose == RunPurpose.EXECUTION.value
        and existing.attempt_index == spec.attempt_index
        and existing.fix_round == spec.fix_round
        and existing.base_commit == spec.base_commit
        and existing.context_manifest_id == spec.context_manifest_id
        and existing.subject_run_id is None
        and existing.supersedes_run_id is None
        and existing.provider == spec.identity.provider
        and existing.provider_adapter == spec.identity.adapter
        and existing.adapter_version == spec.identity.adapter_version
        and existing.model == spec.identity.model
        and existing.transport == spec.identity.transport
        and existing.control_linked
    )


@dataclass(frozen=True, slots=True)
class RunClosure:
    """O fechamento do Run do Developer: status final + métricas + eventos, numa transação."""

    status: RunStatus
    finished_at: datetime
    duration_ms: int
    token_source: TokenSource
    input_tokens: int | None
    output_tokens: int | None
    files_read: tuple[str, ...] | None
    summary: str | None
    error_summary: str | None
    denials: tuple[ToolDenial, ...] = ()
    limit_exceeded: bool = False


@dataclass(frozen=True, slots=True)
class CloseResult:
    outcome: CloseOutcome
    existing_status: RunStatus | None = None
    safety_events: int = 0


@dataclass(frozen=True, slots=True)
class RunDecision:
    """A decisão pura sobre o fechamento do Run, a partir do relatório e das verificações."""

    run_status: RunStatus
    code: InvocationCode
    outcome: DeveloperOutcome
    critical: bool
    limit_exceeded: bool
    #: Os metadados do provider (tokens, resumo, diagnóstico) podem ser persistidos? Não, quando
    #: eles se contradizem. A evidência do executor não depende disto.
    provider_trusted: bool = True


@dataclass(frozen=True, slots=True)
class DeveloperInvocationResult:
    """Resultado estruturado da invocação. Não transiciona task e não fecha o Run de controle.

    ``developer_outcome`` é o fato que a E8.4.5 consome em `classify_execution`;
    ``integrity_violation`` diz que houve violação **comprovada** (vence qualquer outro fato).
    ``requires_finalization`` = o Run do Developer pode seguir `running`, ou o binding não se
    provou liberado: a recuperação durável é da E8.4.5 e **nada** é repetido aqui.
    """

    status: InvocationStatus
    code: InvocationCode
    fact_code: PreparationCode | None = None
    run_id: str | None = None
    run_status: RunStatus | None = None
    provider_call_attempted: bool = False
    developer_outcome: DeveloperOutcome | None = None
    integrity_violation: bool = False
    binding: BindingOutcome = BindingOutcome.NOT_BOUND
    finalization: Finalization = Finalization.NOT_APPLICABLE
    safety_events: int = 0
    requires_finalization: bool = False
    report: DeveloperInvocationReport | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.status is InvocationStatus.COMPLETED and (
            self.code is not InvocationCode.COMPLETED
            or self.run_status is not RunStatus.OK
            or self.integrity_violation
            or self.requires_finalization
            or self.finalization not in (Finalization.CLOSED, Finalization.ALREADY_FINAL)
            or self.binding not in (BindingOutcome.RELEASED, BindingOutcome.REVOKED)
        ):
            raise ValueError("`completed` exige Run `ok`, binding liberado/negado e nada pendente")


# ------------------------------------------------------------------------- funções puras


def approved_binding_refusal(
    facts: InvocationFacts,
    identity: ProviderIdentity,
    expected: CapabilityBinding | None,
) -> InvocationCode | None:
    """O adaptador que vai rodar e o que será provado são **os mesmos que o humano aprovou**?

    Compara a identidade da porta, o contexto esperado do chamador confiável e o
    `developer_binding`/`tool_profile_hash` do fingerprint **aprovado** — três fontes
    independentes que precisam concordar. Sem `expected` a guarda de capability recusa depois;
    aqui ele já é exigido para não deixar a prova falar de outro adaptador.
    """
    if (
        facts.approved_adapter is None
        or facts.approved_adapter_version is None
        or facts.approved_model is None
    ):
        return InvocationCode.BINDING_NOT_APPROVED
    if (
        identity.adapter != facts.approved_adapter
        or identity.adapter_version != facts.approved_adapter_version
        or identity.model != facts.approved_model
    ):
        return InvocationCode.BINDING_NOT_APPROVED
    if facts.approved_tool_profile_hash != tool_profile_hash():
        return InvocationCode.CAPABILITY_MISMATCH
    if expected is None:
        return InvocationCode.CAPABILITY_UNPROVEN
    if (
        expected.adapter_id != facts.approved_adapter
        or expected.adapter_version != facts.approved_adapter_version
        or expected.model != facts.approved_model
        or expected.transport != identity.transport
    ):
        return InvocationCode.BINDING_NOT_APPROVED
    return None


def provider_metadata_contradictions(
    report: DeveloperInvocationReport, identity: ProviderIdentity
) -> bool:
    """Os **metadados do provider** dizem coisas incompatíveis entre si ou com o aprovado?

    Só olha o que o provider afirma: identidade, `limit_exceeded`, status e se houve chamada. Uma
    contradição aqui impede o sucesso e tira a confiança em tokens/resumo/diagnóstico, mas **não**
    toca a evidência do executor."""
    if report.identity != identity:
        return True  # outro adaptador/modelo respondeu: não é o que foi aprovado
    if report.limit_exceeded and report.status is not ReportedStatus.BLOCKED:
        return True
    if report.status is ReportedStatus.OK and not report.provider_called:
        return True  # `ok` sem chamada ao provider: nada pode ter dado `ok`
    return not report.provider_called and report.status not in (
        ReportedStatus.ERROR,
        ReportedStatus.CANCELLED,
    )


def evidence_inconsistencies(report: DeveloperInvocationReport, evidence: EvidenceSnapshot) -> bool:
    """A **evidência do executor** é incoerente? (perda de registro, rejeição, contagens)

    Não apaga o que foi aceito — só impede o sucesso: uma trilha com buraco não prova que nada
    aconteceu."""
    if evidence.inconsistent:
        return True
    if report.denial_count > len(evidence.denials):
        return True  # negação sem trilha: não há como gerar o SafetyEvent
    if report.denial_count > report.operations:
        return True
    # o executor sabe o que leu ([04] §2): ausência com `ok` é defeito
    return report.status is ReportedStatus.OK and report.files_read is None


def _critical_decision(*, limit_exceeded: bool, provider_trusted: bool) -> RunDecision:
    return RunDecision(
        RunStatus.BLOCKED,
        InvocationCode.CRITICAL_VIOLATION,
        DeveloperOutcome.POLICY_BLOCKED,
        critical=True,
        limit_exceeded=limit_exceeded,
        provider_trusted=provider_trusted,
    )


def escalate_for_evidence(decision: RunDecision, evidence: EvidenceSnapshot) -> RunDecision:
    """Uma decisão tomada **sem relatório** (exceção do provider, binding recusado, interrupção…)
    não pode esconder uma violação crítica que o executor já provou: a evidência vence."""
    if evidence.critical and not decision.critical:
        return _critical_decision(limit_exceeded=False, provider_trusted=decision.provider_trusted)
    return decision


def decide_run(
    report: DeveloperInvocationReport,
    evidence: EvidenceSnapshot,
    *,
    identity: ProviderIdentity,
    binding_verified: bool,
) -> RunDecision:
    """A decisão pura de fechamento do Run do Developer. Precedência no docstring do módulo."""
    metadata_bad = provider_metadata_contradictions(report, identity)
    trusted = not metadata_bad
    limit = trusted and report.limit_exceeded and report.status is ReportedStatus.BLOCKED
    if evidence.critical:
        return _critical_decision(limit_exceeded=limit, provider_trusted=trusted)
    if not binding_verified:
        return RunDecision(
            RunStatus.BLOCKED,
            InvocationCode.BINDING_REVOKED,
            DeveloperOutcome.POLICY_BLOCKED,
            critical=True,
            limit_exceeded=limit,
            provider_trusted=trusted,
        )
    if metadata_bad or evidence_inconsistencies(report, evidence):
        return RunDecision(
            RunStatus.ERROR,
            InvocationCode.REPORT_CONTRADICTORY,
            DeveloperOutcome.INTERNAL_ERROR,
            critical=False,
            limit_exceeded=False,
            provider_trusted=trusted,
        )
    if report.status is ReportedStatus.OK:
        if not report.executor_closed:
            return RunDecision(
                RunStatus.ERROR,
                InvocationCode.EXECUTOR_NOT_CLOSED,
                DeveloperOutcome.INTERNAL_ERROR,
                critical=False,
                limit_exceeded=False,
            )
        return RunDecision(
            RunStatus.OK,
            InvocationCode.COMPLETED,
            DeveloperOutcome.COMPLETED,
            critical=False,
            limit_exceeded=False,
        )
    match report.status:
        case ReportedStatus.TIMEOUT:
            return RunDecision(
                RunStatus.TIMEOUT,
                InvocationCode.PROVIDER_TIMEOUT,
                DeveloperOutcome.TIMEOUT,
                critical=False,
                limit_exceeded=False,
            )
        case ReportedStatus.CANCELLED:
            return RunDecision(
                RunStatus.CANCELLED,
                InvocationCode.PROVIDER_CANCELLED,
                DeveloperOutcome.CANCELLED,
                critical=False,
                limit_exceeded=False,
            )
        case ReportedStatus.BLOCKED:
            if report.limit_exceeded:
                return RunDecision(
                    RunStatus.BLOCKED,
                    InvocationCode.LIMIT_EXCEEDED,
                    DeveloperOutcome.LIMIT_EXCEEDED,
                    critical=False,
                    limit_exceeded=True,
                )
            return RunDecision(
                RunStatus.BLOCKED,
                InvocationCode.PROVIDER_ERROR,
                DeveloperOutcome.POLICY_BLOCKED,
                critical=False,
                limit_exceeded=False,
            )
        case _:
            return RunDecision(
                RunStatus.ERROR,
                InvocationCode.PROVIDER_ERROR,
                DeveloperOutcome.PROVIDER_ERROR,
                critical=False,
                limit_exceeded=False,
            )


#: Prefixo do `Run.error_summary` de um Run do Developer que não fechou `ok`.
ERROR_SUMMARY_PREFIX = "developer_invocation"


def run_error_summary(code: InvocationCode, provider_text: str | None = None) -> str:
    """`developer_invocation:<código>` (+ diagnóstico do provider, já truncado; redigido por quem
    persiste). Nunca texto livre de exceção."""
    base = f"{ERROR_SUMMARY_PREFIX}:{code.value}"
    if provider_text:
        return f"{base}: {provider_text[:_STATUS_MAX_ERROR]}"
    return base


def clip_summary(text: str) -> str:
    return text[:_STATUS_MAX_SUMMARY]


__all__ = [
    "CRITICAL_DENIAL_CATEGORIES",
    "ERROR_SUMMARY_PREFIX",
    "KNOWN_DENIAL_CATEGORIES",
    "LIMIT_RULE_ID",
    "BindingOutcome",
    "ClaimOutcome",
    "ClaimResult",
    "CloseOutcome",
    "CloseResult",
    "DeveloperInvocationInput",
    "DeveloperInvocationReport",
    "DeveloperInvocationResult",
    "DeveloperRunPort",
    "DeveloperRunSpec",
    "EvidenceCollector",
    "EvidenceSnapshot",
    "ExistingRunFacts",
    "Finalization",
    "InvocationCode",
    "InvocationFacts",
    "InvocationStatus",
    "ProviderIdentity",
    "ReportedStatus",
    "RunClosure",
    "RunDecision",
    "ToolDenial",
    "approved_binding_refusal",
    "claim_compatible",
    "clip_summary",
    "decide_run",
    "escalate_for_evidence",
    "evidence_inconsistencies",
    "provider_metadata_contradictions",
    "run_error_summary",
]
