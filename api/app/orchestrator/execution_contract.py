"""Contrato de execução da E8.4.1 — **puro**: sem IO, sem banco, sem relógio, sem provider.

Este módulo fixa, em dados e funções testáveis sozinhas, o que a E8.4.2–E8.4.6 vão construir
por cima: a chave idempotente, a derivação dos `invocation_id` dos componentes, os limites de
tempo, os pré-requisitos desta fase e a **classificação de resultados** que a futura
finalização aplicará. Nada aqui executa coisa alguma; `execution_manager.admit_execution` é
quem consome o que for estrutura de admissão.

## Chave idempotente e `invocation_id` dos componentes

A chave que o cliente envia é o `invocation_id` do **Run de controle** (`agent =
orchestrator`, `purpose = execution`). Os `invocation_id` dos Runs dos componentes (Developer,
Test Runner) nascem do **`id` do Run de controle** — não da chave —, no formato
`<control_run_id>:<componente>`. O alfabeto da chave **não contém `:`**, então uma chave
escolhida pelo cliente não colide, por construção, com nenhum id derivado; e o `id` do Run de
controle é um UUID, então dois Runs de controle nunca geram o mesmo id de componente.

`subject_run_id` **não** é usado como elo pai-filho: o campo preserva a semântica de
auditoria/avaliação ([02] §8–§9).

## Classificação de resultados (a futura finalização)

Precedência ([E8.4.1] §11), da mais forte para a mais fraca:

1. estado terminal já confirmado por *compare-and-set* nunca é sobrescrito;
2. integridade/política violada **comprovada**;
3. verificação incompleta ou não verificável;
4. falha do provider ou do runner (timeout, limite, provider, interrupção, técnica);
5. testes falhos **com integridade verificada**;
6. sucesso integral.

A classificação consome fatos **estruturados** (enums fechados). Ela não interpreta texto livre
nem trata a negação de uma única chamada de ferramenta como violação global: isso é decisão de
quem monta os fatos a partir da classificação estruturada que já existe (E8.2/E8.3).

`needs_fix` nunca agenda nova execução: `AUTOMATIC_RETRY_ON_TEST_FAILURE` é `False`, e
nenhuma classificação carrega um pedido de retry. O laço de correção é E10.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from app.db.enums import FailureReason, RunStatus, TaskStatus
from app.orchestrator.errors import InvalidInvocationId
from app.orchestrator.fingerprint import WorkflowPolicy

if TYPE_CHECKING:  # só anotação: o contrato não carrega o ORM em tempo de execução
    from app.db.models import Run, WorkspaceTask

# ------------------------------------------------------------------- chave idempotente

#: Teto da coluna `run.invocation_id`.
MAX_INVOCATION_ID_LENGTH = 128

#: Alfabeto da chave do cliente. **Sem `:`** — separador dos ids derivados dos componentes.
_INVOCATION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: Componentes que terão Run próprio (E8.4.3 e E8.4.4). Fechado.
COMPONENT_DEVELOPER = "developer"
COMPONENT_TEST_RUNNER = "test_runner"
EXECUTION_COMPONENTS: tuple[str, ...] = (COMPONENT_DEVELOPER, COMPONENT_TEST_RUNNER)

_COMPONENT_SEPARATOR = ":"
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def validate_invocation_id(value: object) -> str:
    """A chave idempotente do cliente, ou `InvalidInvocationId` (422).

    A chave é **obrigatória**: sem ela não há como distinguir uma repetição de uma tentativa
    nova, e reaproveitar um valor padrão seria autorizar duplicação silenciosa.
    """
    if not isinstance(value, str) or not value:
        raise InvalidInvocationId("`invocation_id` é obrigatório (chave idempotente)")
    if len(value) > MAX_INVOCATION_ID_LENGTH:
        raise InvalidInvocationId(f"`invocation_id` excede {MAX_INVOCATION_ID_LENGTH} caracteres")
    if _INVOCATION_ID_RE.fullmatch(value) is None:
        raise InvalidInvocationId(
            "`invocation_id` aceita apenas letras ASCII, dígitos, `.`, `_` e `-`, "
            "começando por letra ou dígito"
        )
    return value


def component_invocation_id(control_run_id: str, component: str) -> str:
    """`<control_run_id>:<componente>` — determinístico, sem colisão com chave de cliente."""
    if _UUID_RE.fullmatch(control_run_id) is None:
        raise ValueError("o id do Run de controle precisa ser um UUID em minúsculas")
    if component not in EXECUTION_COMPONENTS:
        raise ValueError(f"componente desconhecido: {component!r}")
    return f"{control_run_id}{_COMPONENT_SEPARATOR}{component}"


# ----------------------------------------------------------------------- limites de tempo


def task_deadline_remaining_s(started_at: datetime, task_timeout_s: int, *, now: datetime) -> float:
    """Segundos que restam da tentativa (`task_timeout_s` a partir do Run de controle).

    Nunca negativo: `0.0` significa **prazo esgotado**. Os dois instantes precisam ser *aware*.
    """
    if started_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("instantes naive não são aceitos: informe o fuso")
    if task_timeout_s <= 0:
        raise ValueError("`task_timeout_s` precisa ser positivo")
    elapsed = (now - started_at).total_seconds()
    return max(0.0, task_timeout_s - elapsed)


def operation_timeout_s(
    *, run_timeout_s: int, remaining_task_s: float, policy_timeout_s: float | None = None
) -> float:
    """O teto de **uma** operação: o menor entre `run_timeout_s`, o que resta da task e o
    timeout mais restritivo da `TestPolicy`/budget (quando existir).

    `0.0` = não resta tempo: a operação não deve nascer. A supervisão que honra o valor é das
    etapas de runtime (E8.4.3–E8.4.4); aqui só se calcula o limite.
    """
    if run_timeout_s <= 0:
        raise ValueError("`run_timeout_s` precisa ser positivo")
    candidates = [float(run_timeout_s), max(0.0, remaining_task_s)]
    if policy_timeout_s is not None:
        if policy_timeout_s <= 0:
            raise ValueError("o timeout da política precisa ser positivo")
        candidates.append(float(policy_timeout_s))
    return min(candidates)


# ------------------------------------------------------ pré-requisitos desta fase (E8.4.1)

#: Agentes que a E8 consegue invocar. `architect`/`researcher` não têm provider; o `auditor`
#: só chega na E9. Uma composição que exija outro agente é recusada **antes** da admissão.
E8_AVAILABLE_AGENTS: frozenset[str] = frozenset({"developer"})

#: Formatos de objeto do repositório aceitos pelo fluxo E2E da V1. A E8.3 sabe verificar
#: SHA-256 isoladamente, mas a criação de worktree (E7.4) e o `planning_base_commit` são SHA-1.
E2E_OBJECT_FORMATS: frozenset[str] = frozenset({"sha1"})

#: `runner_id`s que o Test Runner V1 executa. Espelha `GENERIC_SUBPROCESS_RUNNER_ID` de
#: `agent_runtime.runners` (o Orchestrator não importa `agent_runtime`; um teste cruza os dois).
SUPPORTED_TEST_RUNNER_IDS: frozenset[str] = frozenset({"generic-subprocess-v1"})


class AdmissionGuard(str, Enum):
    """Slugs das guardas de pré-admissão que a E8.4.1 acrescenta (`TransitionGuardFailed.guard`).

    As seis guardas históricas de [02] §4 mantêm os slugs de `state_machine`.
    """

    WORKSPACE_ACTIVE = "workspace_active"
    NOT_CANCELLED = "not_cancelled"
    PLAN_ARTIFACTS_COHERENT = "plan_artifacts_coherent"
    BASE_COMMIT_COHERENT = "base_commit_coherent"
    DEVELOPER_BINDING_APPROVED = "developer_binding_approved"
    WORKFLOW_POLICY_SUPPORTED = "workflow_policy_supported"
    TEST_POLICY_SUPPORTED = "test_policy_supported"
    COMPOSITION_AVAILABLE = "composition_available"
    OBJECT_FORMAT_SUPPORTED = "object_format_supported"


def unavailable_agents(agents: Sequence[str]) -> tuple[str, ...]:
    """Agentes da composição aprovada que esta fase não consegue invocar.

    Uma lista vazia, ou que não comece por `developer`, também é recusada pelo chamador: o
    `Router` sempre põe `developer` primeiro ([03] §6).
    """
    return tuple(agent for agent in agents if agent not in E8_AVAILABLE_AGENTS)


def workflow_policy_supported(policy: WorkflowPolicy) -> bool:
    """A política exige algo que a E8 não tem? Auditoria obrigatória sem Auditor é inexecutável."""
    return not policy.audit_required_on_nonempty_diff


# ------------------------------------------------------------------ resultado da admissão


class AdmissionOutcome(str, Enum):
    """O que `admit_execution` devolve quando **não** recusa."""

    #: Admissão nova: slot reservado, `approved → executing`, Run de controle `running`.
    ADMITTED = "admitted"
    #: Mesma chave + mesma task: a execução existente, sem efeito novo.
    REPLAYED = "replayed"
    #: Slot ocupado por outra task. A task segue `approved`; nada foi consumido nem criado.
    SLOT_BUSY = "slot_busy"


SLOT_BUSY_CODE = "slot_busy"


@dataclass(frozen=True, slots=True)
class ExecutionAdmission:
    """Resultado estruturado da admissão. ``run`` é `None` só em `SLOT_BUSY`."""

    outcome: AdmissionOutcome
    task: WorkspaceTask
    run: Run | None = None
    #: Slug estável, presente só quando o resultado é uma não-admissão (`slot_busy`).
    code: str | None = None

    def __post_init__(self) -> None:
        if self.outcome is AdmissionOutcome.SLOT_BUSY:
            if self.run is not None or self.code != SLOT_BUSY_CODE:
                raise ValueError("`slot_busy` não carrega Run e leva o código `slot_busy`")
        elif self.run is None or self.code is not None:
            raise ValueError("admissão e repetição carregam o Run de controle e nenhum código")


# --------------------------------------------------------------- classificação de resultados

#: Nenhuma falha de teste dispara retry nesta etapa; `needs_fix` não reexecuta (E10).
AUTOMATIC_RETRY_ON_TEST_FAILURE = False


class IntegrityVerdict(str, Enum):
    """Resultado da verificação pós-execução (E8.3), reduzido ao que decide o estado."""

    VERIFIED = "verified"
    #: Violação **comprovada** (escrita fora da worktree, política de caminho, etc.).
    VIOLATED = "violated"
    #: Incompleta, não verificável ou não executada. Nunca equivale a `VERIFIED`.
    UNVERIFIABLE = "unverifiable"


class DeveloperOutcome(str, Enum):
    """Desfecho estruturado do único run do Developer."""

    COMPLETED = "completed"
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"
    LIMIT_EXCEEDED = "limit_exceeded"
    #: O provider recusou rodar por política (perfil/capability/ambiente) — fato estruturado do
    #: adaptador, não texto.
    POLICY_BLOCKED = "policy_blocked"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    INTERNAL_ERROR = "internal_error"


class RunnerOutcome(str, Enum):
    """Desfecho estruturado da única execução de testes (Test Runner)."""

    PASSED = "passed"
    #: `exit_code != 0`: um `TestSummary` **legítimo**, não falha técnica ([05] E8.3).
    FAILED = "failed"
    NOT_RUN = "not_run"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    #: Falha técnica/pré-condição do runner (`TestRunnerFailure` que não é timeout/cancelamento).
    TECHNICAL_FAILURE = "technical_failure"
    INTERRUPTED = "interrupted"


class ExecutionCode(str, Enum):
    """Causa estável do resultado (vai para o `error_summary`/`result_summary` redigidos)."""

    SUCCESS = "success"
    TESTS_FAILED = "tests_failed"
    TIMEOUT = "timeout"
    LIMIT_EXCEEDED = "limit_exceeded"
    INTEGRITY_VIOLATED = "integrity_violated"
    POLICY_BLOCKED = "policy_blocked"
    POLICY_NOT_SATISFIED = "policy_not_satisfied"
    VERIFICATION_INCOMPLETE = "verification_incomplete"
    PROVIDER_ERROR = "provider_error"
    INTERRUPTED = "interrupted"
    INTERNAL_ERROR = "internal_error"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ExecutionFacts:
    """Fatos estruturados de **uma** tentativa, na entrada da classificação."""

    integrity: IntegrityVerdict
    developer: DeveloperOutcome
    tests: RunnerOutcome
    #: A política de workflow vigente (E8 single-pass) foi satisfeita?
    policy_satisfied: bool = True
    #: Estado terminal que um *compare-and-set* já confirmou (`cancel`, reconciliação…).
    confirmed_terminal: TaskStatus | None = None


@dataclass(frozen=True, slots=True)
class TaskResolution:
    status: TaskStatus
    failure_reason: FailureReason | None

    def __post_init__(self) -> None:
        if (self.status is TaskStatus.FAILED) != (self.failure_reason is not None):
            raise ValueError("`failure_reason` existe se e somente se a task falhou ([02] §3)")


@dataclass(frozen=True, slots=True)
class ExecutionClassification:
    """O veredito. ``task_resolution`` é `None` quando a task **não** deve ser tocada."""

    code: ExecutionCode
    run_status: RunStatus
    task_resolution: TaskResolution | None


_TERMINAL = frozenset({TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED})


def _failed(reason: FailureReason) -> TaskResolution:
    return TaskResolution(TaskStatus.FAILED, reason)


def _developer_failure(outcome: DeveloperOutcome) -> ExecutionClassification | None:
    match outcome:
        case DeveloperOutcome.COMPLETED:
            return None
        case DeveloperOutcome.TIMEOUT:
            return ExecutionClassification(
                ExecutionCode.TIMEOUT, RunStatus.TIMEOUT, _failed(FailureReason.TIMEOUT)
            )
        case DeveloperOutcome.LIMIT_EXCEEDED:
            return ExecutionClassification(
                ExecutionCode.LIMIT_EXCEEDED,
                RunStatus.BLOCKED,
                _failed(FailureReason.LIMIT_EXCEEDED),
            )
        case DeveloperOutcome.POLICY_BLOCKED:
            return ExecutionClassification(
                ExecutionCode.POLICY_BLOCKED,
                RunStatus.BLOCKED,
                _failed(FailureReason.BLOCKED_BY_POLICY),
            )
        case DeveloperOutcome.PROVIDER_ERROR:
            return ExecutionClassification(
                ExecutionCode.PROVIDER_ERROR, RunStatus.ERROR, _failed(FailureReason.PROVIDER_ERROR)
            )
        case DeveloperOutcome.INTERRUPTED:
            return ExecutionClassification(
                ExecutionCode.INTERRUPTED, RunStatus.INTERRUPTED, _failed(FailureReason.INTERRUPTED)
            )
        case DeveloperOutcome.CANCELLED:
            return ExecutionClassification(ExecutionCode.CANCELLED, RunStatus.CANCELLED, None)
        case DeveloperOutcome.INTERNAL_ERROR:
            return _internal_error()


def _runner_failure(outcome: RunnerOutcome) -> ExecutionClassification | None:
    match outcome:
        case RunnerOutcome.PASSED | RunnerOutcome.FAILED:
            return None
        case RunnerOutcome.TIMEOUT:
            return ExecutionClassification(
                ExecutionCode.TIMEOUT, RunStatus.TIMEOUT, _failed(FailureReason.TIMEOUT)
            )
        case RunnerOutcome.INTERRUPTED:
            return ExecutionClassification(
                ExecutionCode.INTERRUPTED, RunStatus.INTERRUPTED, _failed(FailureReason.INTERRUPTED)
            )
        case RunnerOutcome.CANCELLED:
            return ExecutionClassification(ExecutionCode.CANCELLED, RunStatus.CANCELLED, None)
        case RunnerOutcome.TECHNICAL_FAILURE | RunnerOutcome.NOT_RUN:
            # `NOT_RUN` com o Developer concluído é inconsistência: sem teste não há `done`.
            return _internal_error()


def _internal_error() -> ExecutionClassification:
    return ExecutionClassification(
        ExecutionCode.INTERNAL_ERROR, RunStatus.ERROR, _failed(FailureReason.INTERNAL_ERROR)
    )


def _classify(facts: ExecutionFacts) -> ExecutionClassification:
    # 2. violação comprovada de integridade/política — vence qualquer outra coisa.
    if facts.integrity is IntegrityVerdict.VIOLATED:
        return ExecutionClassification(
            ExecutionCode.INTEGRITY_VIOLATED,
            RunStatus.BLOCKED,
            _failed(FailureReason.BLOCKED_BY_POLICY),
        )
    if not facts.policy_satisfied:
        return ExecutionClassification(
            ExecutionCode.POLICY_NOT_SATISFIED,
            RunStatus.BLOCKED,
            _failed(FailureReason.BLOCKED_BY_POLICY),
        )

    # 3. verificação incompleta/não verificável: nunca vira "falha do provider" nem sucesso.
    if facts.integrity is IntegrityVerdict.UNVERIFIABLE:
        return ExecutionClassification(
            ExecutionCode.VERIFICATION_INCOMPLETE,
            RunStatus.ERROR,
            _failed(FailureReason.INTERNAL_ERROR),
        )

    # 4. falha do provider ou do runner (Developer primeiro: o runner nem deveria ter rodado).
    failure = _developer_failure(facts.developer)
    if failure is None:
        failure = _runner_failure(facts.tests)
    if failure is not None:
        return failure

    # 5. testes falhos, com a integridade já verificada.
    if facts.tests is RunnerOutcome.FAILED:
        return ExecutionClassification(
            ExecutionCode.TESTS_FAILED, RunStatus.ERROR, TaskResolution(TaskStatus.NEEDS_FIX, None)
        )

    # 6. sucesso integral — só aqui `done`.
    return ExecutionClassification(
        ExecutionCode.SUCCESS, RunStatus.OK, TaskResolution(TaskStatus.DONE, None)
    )


def classify_execution(facts: ExecutionFacts) -> ExecutionClassification:
    """Aplica a precedência de [E8.4.1] §11. **Pura e total**: todo fato válido tem veredito.

    * `confirmed_terminal` (precedência 1) nunca é sobrescrito: o `Run` ainda recebe o veredito
      calculado dos fatos — "o Run preserva o resultado" ([ADR-0008]) —, mas
      ``task_resolution`` sai `None`.
    * Cancelamento observado sem terminal `cancelled` confirmado é inconsistência e vira
      `internal_error`: só o comando `cancel`, por *compare-and-set*, cancela uma task.
    """
    if facts.confirmed_terminal is not None and facts.confirmed_terminal not in _TERMINAL:
        raise ValueError("`confirmed_terminal` precisa ser um estado terminal")

    verdict = _classify(facts)

    if verdict.code is ExecutionCode.CANCELLED and (
        facts.confirmed_terminal is not TaskStatus.CANCELLED
    ):
        verdict = _internal_error()

    if facts.confirmed_terminal is not None:
        return ExecutionClassification(verdict.code, verdict.run_status, None)
    return verdict


__all__ = [
    "AUTOMATIC_RETRY_ON_TEST_FAILURE",
    "COMPONENT_DEVELOPER",
    "COMPONENT_TEST_RUNNER",
    "E2E_OBJECT_FORMATS",
    "E8_AVAILABLE_AGENTS",
    "EXECUTION_COMPONENTS",
    "MAX_INVOCATION_ID_LENGTH",
    "SLOT_BUSY_CODE",
    "SUPPORTED_TEST_RUNNER_IDS",
    "AdmissionGuard",
    "AdmissionOutcome",
    "DeveloperOutcome",
    "ExecutionAdmission",
    "ExecutionClassification",
    "ExecutionCode",
    "ExecutionFacts",
    "IntegrityVerdict",
    "RunnerOutcome",
    "TaskResolution",
    "classify_execution",
    "component_invocation_id",
    "operation_timeout_s",
    "task_deadline_remaining_s",
    "unavailable_agents",
    "validate_invocation_id",
    "workflow_policy_supported",
]
