"""Contrato puro de reserva, teardown e liberação (E8.4.5 / I1) — **sem IO**.

Sem banco, sistema de arquivos, processo, Git, relógio, lock do SO ou thread. Só `enum`,
`dataclasses` e `datetime` (valores). Este módulo fixa, como dados e funções testáveis sozinhas:

* os estados da reserva e das fases de lançamento, com a matriz completa de arestas;
* as bases e a **proveniência** das evidências (quem pode escrever o quê, e quando);
* a representação do lançamento **recusado antes da criação** (`intent → ended_confirmed`);
* o veredito de teardown dos **recursos** (`classify_resources`), a **avaliação de teardown
  completo** (`assess_teardown`) e o predicado de liberação (`decide_release`);
* o gate de teardown sobre o resultado de `classify_execution` (`gate_teardown`), que recebe o
  **snapshot** (nunca uma avaliação pronta) e reavalia;
* a decisão do lease de startup (C7.4).

`classify_execution` e `ExecutionFacts` **não** mudam: o gate envolve o resultado deles.

## Regras que valem em todo o módulo

* **Validação em runtime, não só anotação.** Todo dado que cruza uma fronteira pública é conferido
  por tipo **exato** (`type(x) is T`): um `str` nunca vale como enum, `"false"` nunca vale como
  `bool`, `bool` nunca vale como `int`. Tipo errado levanta `TypeError`; valor fora do domínio
  levanta `ValueError`. Nenhuma comparação por identidade ou igualdade acontece antes disso.
* **Fail closed.** Ausência de evidência nunca produz `RELEASE`. Qualquer entrada desconhecida
  (lease, histórico, binding, drenagem, fase) é `HOLD` ou `INCIDENT`.
* **Os fatos vêm dos registros, não de flags.** A presença do Developer e do Test Runner é
  **derivada** dos Runs e dos launches; `developer_declared` é uma declaração persistente que tem de
  **concordar** com eles. Uma flag contraditória nunca dispensa a prova de um recurso presente: o
  recurso continua exigindo prova e a contradição vira bloqueador.
* **Três perguntas distintas** (P2-I1-C1): (1) os recursos *enumerados* estão encerrados?
  (`classify_resources`); (2) a enumeração e o histórico são *completos* e atuais (lease
  consistente, histórico comprovado, Runs coerentes)?; (3) o teardown *completo* está comprovado?
  (`assess_teardown`: só ele autoriza `done`/`needs_fix`). `decide_release` reutiliza a mesma
  avaliação e acrescenta o que só existe **depois** da finalização (Runs fechados, selo, ack).
* **Proveniência fixada no momento da escrita.** Uma recusa gravada pelo dono original antes da
  adoção continua válida depois dela (terminal imutável).
* **Histórico não comprovadamente atual** (C7.7, ainda sem mecanismo): `HistoryCurrency` é campo
  **obrigatório e sem padrão**. Só `PROVEN_CURRENT` permite teardown completo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

from app.db.enums import FailureReason, RunStatus, TaskStatus
from app.orchestrator.execution_contract import (
    ExecutionClassification,
    ExecutionCode,
    TaskResolution,
)

# ------------------------------------------------------------------ validação estrita

_E = TypeVar("_E", bound=Enum)


def _enum(name: str, value: object, cls: type[_E]) -> _E:
    if type(value) is not cls:
        raise TypeError(f"{name}: esperado {cls.__name__}, recebido {type(value).__name__}")
    return value


def _opt_enum(name: str, value: object, cls: type[_E]) -> _E | None:
    return None if value is None else _enum(name, value, cls)


def _bool(name: str, value: object) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{name}: esperado bool, recebido {type(value).__name__}")
    return value


def _int(name: str, value: object, *, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise TypeError(f"{name}: esperado int, recebido {type(value).__name__}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name}: precisa ser >= {minimum}")
    return value


def _opt_int(name: str, value: object, *, minimum: int | None = None) -> int | None:
    return None if value is None else _int(name, value, minimum=minimum)


def _str(name: str, value: object) -> str:
    if type(value) is not str:
        raise TypeError(f"{name}: esperado str, recebido {type(value).__name__}")
    if not value:
        raise ValueError(f"{name}: não pode ser vazio")
    return value


def _opt_str(name: str, value: object) -> str | None:
    return None if value is None else _str(name, value)


def _dt(name: str, value: object) -> datetime:
    if type(value) is not datetime:
        raise TypeError(f"{name}: esperado datetime, recebido {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}: datetime precisa ter fuso")
    return value


def _opt_dt(name: str, value: object) -> datetime | None:
    return None if value is None else _dt(name, value)


def _tuple_of(name: str, value: object, cls: type[Any]) -> tuple[Any, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{name}: esperado tuple, recebido {type(value).__name__}")
    for item in value:
        if type(item) is not cls:
            raise TypeError(f"{name}: item {type(item).__name__}, esperado {cls.__name__}")
    return value


def _resolution(value: object) -> TaskResolution | None:
    """Valida a saída aninhada do contrato original sem alterar aquele contrato."""
    if value is None:
        return None
    if type(value) is not TaskResolution:
        raise TypeError("task_resolution: esperado TaskResolution ou None")
    _enum("task_resolution.status", value.status, TaskStatus)
    _opt_enum("task_resolution.failure_reason", value.failure_reason, FailureReason)
    value.__post_init__()
    return value


# ------------------------------------------------------------------------ reserva


class ReservationState(str, Enum):
    ACTIVE = "active"
    DRAINING = "draining"
    TEARDOWN_PENDING = "teardown_pending"
    UNPROVEN = "unproven"
    RELEASED = "released"


_STATE_EDGES: frozenset[tuple[ReservationState, ReservationState]] = frozenset(
    {
        (ReservationState.ACTIVE, ReservationState.DRAINING),
        (ReservationState.ACTIVE, ReservationState.TEARDOWN_PENDING),
        (ReservationState.DRAINING, ReservationState.TEARDOWN_PENDING),
        (ReservationState.DRAINING, ReservationState.RELEASED),
        (ReservationState.TEARDOWN_PENDING, ReservationState.UNPROVEN),
        (ReservationState.TEARDOWN_PENDING, ReservationState.RELEASED),
        (ReservationState.UNPROVEN, ReservationState.TEARDOWN_PENDING),
        (ReservationState.UNPROVEN, ReservationState.RELEASED),
    }
)

#: Estados a partir dos quais `released` é uma aresta legal.
RELEASABLE_FROM: frozenset[ReservationState] = frozenset(
    old for old, new in _STATE_EDGES if new is ReservationState.RELEASED
)


def reservation_edge_allowed(old: ReservationState, new: ReservationState) -> bool:
    """Aresta legal entre estados. Auto-aresta vale para todo estado **exceto** `released`
    (imutável); `released` nunca sai. Tipo inválido levanta `TypeError`."""
    _enum("old", old, ReservationState)
    _enum("new", new, ReservationState)
    if old is ReservationState.RELEASED:
        return False
    return old is new or (old, new) in _STATE_EDGES


def adoption_edge_allowed(old: ReservationState, new: ReservationState) -> bool:
    """C8.5: a adoção só pode ocorrer na mesma instrução que leva a reserva a `teardown_pending`."""
    _enum("old", old, ReservationState)
    _enum("new", new, ReservationState)
    return new is ReservationState.TEARDOWN_PENDING and old is not ReservationState.RELEASED


# ---------------------------------------------------------------------- lançamento


class LaunchPhase(str, Enum):
    INTENT = "intent"
    CREATED = "created"
    CONTAINED = "contained"
    ENDED_CONFIRMED = "ended_confirmed"
    ENDED_UNCONFIRMED = "ended_unconfirmed"


TERMINAL_PHASES: frozenset[LaunchPhase] = frozenset(
    {LaunchPhase.ENDED_CONFIRMED, LaunchPhase.ENDED_UNCONFIRMED}
)


class EndBasis(str, Enum):
    #: Só `intent → ended_confirmed`, sem `pid`/`start_token`: recusa **antes** de `Popen`.
    REFUSED_BEFORE_CREATION = "refused_before_creation"
    #: Só a partir de `created`/`contained`, com `pid` e `start_token`.
    TREE_CONFIRMED_DEAD = "tree_confirmed_dead"
    #: Pressupõe processo criado: só a partir de `created`/`contained`, com identidade.
    TREE_UNCONFIRMED = "tree_unconfirmed"
    #: `Popen` levantou, **sem** `pid`. Só a partir de `intent`. NÃO comprova ausência de processo:
    #: resolve-se por evidência supervisionada (`LAUNCH_START_FAILED_PROVEN`).
    POPEN_RAISED = "popen_raised"
    #: Falha de escrita do ledger; vale a partir de qualquer fase não terminal (com ou sem `pid`).
    LEDGER_WRITE_FAILED = "ledger_write_failed"


class Platform(str, Enum):
    WIN32 = "win32"
    POSIX = "posix"


class ContainmentProfile(str, Enum):
    WIN_JOB_V1 = "win_job_v1"
    POSIX_PGRP_V1 = "posix_pgrp_v1"


_PROFILE_OF: dict[Platform, ContainmentProfile] = {
    Platform.WIN32: ContainmentProfile.WIN_JOB_V1,
    Platform.POSIX: ContainmentProfile.POSIX_PGRP_V1,
}


class PhaseTag(str, Enum):
    PREPARATION = "preparation"
    DEVELOPER = "developer"
    TEST_RUNNER = "test_runner"
    OTHER = "other"


_L = LaunchPhase
_B_ = EndBasis

#: Matriz **completa** `(origem, destino, end_basis)`. Qualquer combinação fora dela é ilegal.
_LAUNCH_TRANSITIONS: frozenset[tuple[LaunchPhase, LaunchPhase, EndBasis | None]] = frozenset(
    {
        (_L.INTENT, _L.CREATED, None),
        (_L.CREATED, _L.CONTAINED, None),
        (_L.INTENT, _L.ENDED_CONFIRMED, _B_.REFUSED_BEFORE_CREATION),
        (_L.CREATED, _L.ENDED_CONFIRMED, _B_.TREE_CONFIRMED_DEAD),
        (_L.CONTAINED, _L.ENDED_CONFIRMED, _B_.TREE_CONFIRMED_DEAD),
        (_L.INTENT, _L.ENDED_UNCONFIRMED, _B_.POPEN_RAISED),
        (_L.INTENT, _L.ENDED_UNCONFIRMED, _B_.LEDGER_WRITE_FAILED),
        (_L.CREATED, _L.ENDED_UNCONFIRMED, _B_.TREE_UNCONFIRMED),
        (_L.CREATED, _L.ENDED_UNCONFIRMED, _B_.LEDGER_WRITE_FAILED),
        (_L.CONTAINED, _L.ENDED_UNCONFIRMED, _B_.TREE_UNCONFIRMED),
        (_L.CONTAINED, _L.ENDED_UNCONFIRMED, _B_.LEDGER_WRITE_FAILED),
    }
)


def launch_edge_allowed(
    old: LaunchPhase, new: LaunchPhase, end_basis: EndBasis | None = None
) -> bool:
    """Aresta legal `(origem, destino, end_basis)` da matriz completa.

    `intent → ended_confirmed` **só** com `refused_before_creation`, e essa base **só** a partir de
    `intent`; `popen_raised` só a partir de `intent`; `tree_*` só com processo criado; fase não
    terminal não tem base; terminal sempre tem. Terminal nunca sai. Tipo inválido levanta
    `TypeError`.
    """
    _enum("old", old, LaunchPhase)
    _enum("new", new, LaunchPhase)
    _opt_enum("end_basis", end_basis, EndBasis)
    return (old, new, end_basis) in _LAUNCH_TRANSITIONS


_BASES_WITH_PID = frozenset({EndBasis.TREE_CONFIRMED_DEAD, EndBasis.TREE_UNCONFIRMED})
_BASES_WITHOUT_PID = frozenset({EndBasis.REFUSED_BEFORE_CREATION, EndBasis.POPEN_RAISED})


@dataclass(frozen=True, slots=True)
class LaunchRecord:
    """Uma linha do ledger de lançamentos, validada como o CHECK do banco validará."""

    id: int
    phase_tag: PhaseTag
    platform: Platform
    containment_profile: ContainmentProfile
    phase: LaunchPhase
    version: int
    pid: int | None = None
    start_token: str | None = None
    end_basis: EndBasis | None = None
    ended_by_token: str | None = None

    def __post_init__(self) -> None:
        _int("id", self.id, minimum=1)
        _enum("phase_tag", self.phase_tag, PhaseTag)
        _enum("platform", self.platform, Platform)
        _enum("containment_profile", self.containment_profile, ContainmentProfile)
        _enum("phase", self.phase, LaunchPhase)
        _int("version", self.version, minimum=1)
        _opt_int("pid", self.pid, minimum=1)
        _opt_str("start_token", self.start_token)
        _opt_enum("end_basis", self.end_basis, EndBasis)
        _opt_str("ended_by_token", self.ended_by_token)
        if _PROFILE_OF[self.platform] is not self.containment_profile:
            raise ValueError("perfil de contenção incompatível com a plataforma")
        if (self.pid is None) != (self.start_token is None):
            raise ValueError("pid e start_token existem juntos ou não existem")
        has_pid = self.pid is not None
        if self.phase is LaunchPhase.INTENT:
            if has_pid or self.end_basis is not None or self.ended_by_token is not None:
                raise ValueError("intent não tem pid, base de término nem escritor de término")
        elif self.phase in (LaunchPhase.CREATED, LaunchPhase.CONTAINED):
            if not has_pid or self.end_basis is not None or self.ended_by_token is not None:
                raise ValueError("created/contained exigem pid e não têm término")
        else:
            if self.end_basis is None or self.ended_by_token is None:
                raise ValueError("fase terminal exige base e escritor de término")
            confirmed = self.phase is LaunchPhase.ENDED_CONFIRMED
            legal = (
                {EndBasis.REFUSED_BEFORE_CREATION, EndBasis.TREE_CONFIRMED_DEAD}
                if confirmed
                else {
                    EndBasis.TREE_UNCONFIRMED,
                    EndBasis.POPEN_RAISED,
                    EndBasis.LEDGER_WRITE_FAILED,
                }
            )
            if self.end_basis not in legal:
                raise ValueError("base de término incompatível com a fase terminal")
            if self.end_basis in _BASES_WITH_PID and not has_pid:
                raise ValueError("a base pressupõe processo criado: pid e start_token exigidos")
            if self.end_basis in _BASES_WITHOUT_PID and has_pid:
                raise ValueError("a base pressupõe que nenhum processo foi criado: sem pid")


class TicketState(str, Enum):
    INTENT = "intent"
    CROSSING = "crossing"
    REFUSED = "refused"


class LaunchTicket:
    """Estado **em memória** do lançamento (C1.3): `INTENT → CROSSING` ou `INTENT → REFUSED`.

    `refuse()` só é aceito em `INTENT`: depois de `cross()` (imediatamente antes da primeira
    instrução que cria o processo) a recusa é uma mentira possível e levanta. Sem thread nem lock:
    a sincronização é do LaunchGate (incremento futuro).
    """

    __slots__ = ("_state",)

    def __init__(self) -> None:
        self._state = TicketState.INTENT

    @property
    def state(self) -> TicketState:
        return self._state

    def cross(self) -> None:
        if self._state is not TicketState.INTENT:
            raise RuntimeError("ticket já decidido: não pode cruzar")
        self._state = TicketState.CROSSING

    def refuse(self) -> EndBasis:
        """Devolve a base de término a gravar. Levanta se o ticket já cruzou ou já recusou."""
        if self._state is not TicketState.INTENT:
            raise RuntimeError("recusa depois do cruzamento (ou repetida) é inválida")
        self._state = TicketState.REFUSED
        return EndBasis.REFUSED_BEFORE_CREATION


class WriteRefusal(str, Enum):
    """Por que uma escrita no ledger/evidência deve ser recusada (espelha os triggers)."""

    RESERVATION_NOT_WRITABLE = "reservation_not_writable"
    RESERVATION_ADOPTED = "reservation_adopted"
    DRAIN_REQUESTED = "drain_requested"
    WRITER_NOT_OWNER = "writer_not_owner"
    WRITER_NOT_PARTICIPANT = "writer_not_participant"
    LIVE_BASIS_AFTER_ADOPTION = "live_basis_after_adoption"
    QUIESCENCE_NEEDS_ADOPTER = "quiescence_needs_adopter"
    TERMINAL_IMMUTABLE = "terminal_immutable"
    EDGE_ILLEGAL = "edge_illegal"
    RESERVATION_RELEASED = "reservation_released"


def launch_insert_refusal(
    *,
    reservation_state: ReservationState,
    adopted_by_token: str | None,
    drain_requested: bool,
) -> WriteRefusal | None:
    """Novo `intent`: só com a reserva `active`, **não adotada** (C8.5) e sem drenagem pedida."""
    _enum("reservation_state", reservation_state, ReservationState)
    _opt_str("adopted_by_token", adopted_by_token)
    _bool("drain_requested", drain_requested)
    if adopted_by_token is not None:
        return WriteRefusal.RESERVATION_ADOPTED
    if reservation_state is not ReservationState.ACTIVE:
        return WriteRefusal.RESERVATION_NOT_WRITABLE
    if drain_requested:
        return WriteRefusal.DRAIN_REQUESTED
    return None


def launch_write_refusal(
    *,
    reservation_state: ReservationState,
    owner_token: str,
    adopted_by_token: str | None,
    writer_token: str,
    old_phase: LaunchPhase,
    new_phase: LaunchPhase,
    end_basis: EndBasis | None = None,
) -> WriteRefusal | None:
    """Atualização de launch (C8.1). A proveniência é conferida **na escrita**: dono original,
    reserva não adotada e em `active`/`draining`. Terminal é imutável. O recovery, adotante, nunca
    passa aqui."""
    _enum("reservation_state", reservation_state, ReservationState)
    _str("owner_token", owner_token)
    _opt_str("adopted_by_token", adopted_by_token)
    _str("writer_token", writer_token)
    _enum("old_phase", old_phase, LaunchPhase)
    _enum("new_phase", new_phase, LaunchPhase)
    _opt_enum("end_basis", end_basis, EndBasis)
    if old_phase in TERMINAL_PHASES:
        return WriteRefusal.TERMINAL_IMMUTABLE
    if adopted_by_token is not None:
        return WriteRefusal.RESERVATION_ADOPTED
    if reservation_state not in (ReservationState.ACTIVE, ReservationState.DRAINING):
        return WriteRefusal.RESERVATION_NOT_WRITABLE
    if not launch_edge_allowed(old_phase, new_phase, end_basis):
        return WriteRefusal.EDGE_ILLEGAL
    if writer_token != owner_token:
        return WriteRefusal.WRITER_NOT_OWNER
    return None


# ---------------------------------------------------------------------- evidência


class EvidenceScope(str, Enum):
    LAUNCH = "launch"
    IN_PROCESS = "in_process"
    BINDING = "binding"


class BindingSubject(str, Enum):
    CONTROL = "control"
    DEVELOPER = "developer"
    TEST_RUNNER = "test_runner"


class EvidenceBasis(str, Enum):
    # escopo `launch`
    LAUNCH_START_FAILED_PROVEN = "launch_start_failed_proven"
    IDENTITY_PROBE_ABSENT = "identity_probe_absent"
    JOB_GUARANTEE_VERIFIED = "job_guarantee_verified"
    MACHINE_RESTARTED_SINCE_DECLARATION = "machine_restarted_since_declaration"
    PROBE_PRESENT = "probe_present"  # negativa
    LAUNCH_UNCONFIRMED_OBSERVED = "launch_unconfirmed_observed"  # negativa
    # escopo `in_process`
    EXECUTOR_DRAINED = "executor_drained"
    DRAIN_TIMEOUT = "drain_timeout"  # negativa
    DRAIN_REENTRANT = "drain_reentrant"  # negativa
    QUIESCENCE_UNPROVEN = "quiescence_unproven"  # negativa
    # escopos `in_process` e `binding`
    COOPERATIVE_OWNER_QUIESCENCE = "cooperative_owner_quiescence"
    # escopo `binding`
    BINDING_RELEASED_OBSERVED = "binding_released_observed"
    BINDING_REVOKED_OBSERVED = "binding_revoked_observed"
    BINDING_UNPROVEN = "binding_unproven"  # negativa


_B = EvidenceBasis

NEGATIVE_BASES: frozenset[EvidenceBasis] = frozenset(
    {
        _B.PROBE_PRESENT,
        _B.LAUNCH_UNCONFIRMED_OBSERVED,
        _B.DRAIN_TIMEOUT,
        _B.DRAIN_REENTRANT,
        _B.QUIESCENCE_UNPROVEN,
        _B.BINDING_UNPROVEN,
    }
)

BASES_BY_SCOPE: dict[EvidenceScope, frozenset[EvidenceBasis]] = {
    EvidenceScope.LAUNCH: frozenset(
        {
            _B.LAUNCH_START_FAILED_PROVEN,
            _B.IDENTITY_PROBE_ABSENT,
            _B.JOB_GUARANTEE_VERIFIED,
            _B.MACHINE_RESTARTED_SINCE_DECLARATION,
            _B.PROBE_PRESENT,
            _B.LAUNCH_UNCONFIRMED_OBSERVED,
        }
    ),
    EvidenceScope.IN_PROCESS: frozenset(
        {
            _B.EXECUTOR_DRAINED,
            _B.COOPERATIVE_OWNER_QUIESCENCE,
            _B.DRAIN_TIMEOUT,
            _B.DRAIN_REENTRANT,
            _B.QUIESCENCE_UNPROVEN,
        }
    ),
    EvidenceScope.BINDING: frozenset(
        {
            _B.BINDING_RELEASED_OBSERVED,
            _B.BINDING_REVOKED_OBSERVED,
            _B.COOPERATIVE_OWNER_QUIESCENCE,
            _B.BINDING_UNPROVEN,
        }
    ),
}

#: Observadas **ao vivo** pelo dono original: só podem ser escritas enquanto a reserva não foi
#: adotada, e só valem se o escritor é o dono (`writer_token = owner_token`).
LIVE_OBSERVED_BASES: frozenset[EvidenceBasis] = frozenset(
    {
        _B.EXECUTOR_DRAINED,
        _B.BINDING_RELEASED_OBSERVED,
        _B.BINDING_REVOKED_OBSERVED,
        _B.LAUNCH_START_FAILED_PROVEN,
    }
)

_NEG_LAUNCH = frozenset({_B.PROBE_PRESENT, _B.LAUNCH_UNCONFIRMED_OBSERVED})
_NEG_PROC = frozenset({_B.DRAIN_TIMEOUT, _B.DRAIN_REENTRANT, _B.QUIESCENCE_UNPROVEN})
_NEG_BIND = frozenset({_B.BINDING_UNPROVEN})
_LIVE_PROC = frozenset({_B.EXECUTOR_DRAINED})
_LIVE_BIND = frozenset({_B.BINDING_RELEASED_OBSERVED, _B.BINDING_REVOKED_OBSERVED})


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """Uma linha de evidência (append-only). `seq` é a ordem total de gravação.

    `supervision_confirmed` é o fato, entregue pelo supervisor, de que a árvore foi **confirmada**
    morta (ou nunca existiu): `LAUNCH_START_FAILED_PROVEN` exige-o. Uma exceção de `Popen`, por si
    só, não comprova ausência de processo.
    """

    seq: int
    scope: EvidenceScope
    basis: EvidenceBasis
    writer_token: str
    subject: BindingSubject | None = None
    launch_id: int | None = None
    launch_version: int | None = None
    logical_key: str | None = None
    physical_key: str | None = None
    lock_files_match: bool = False
    profile_ok: bool = False
    owner_quiescence_ok: bool = False
    root_probe_absent: bool = False
    boot_changed: bool = False
    supervision_confirmed: bool = False

    def __post_init__(self) -> None:
        _int("seq", self.seq, minimum=0)
        _enum("scope", self.scope, EvidenceScope)
        _enum("basis", self.basis, EvidenceBasis)
        _str("writer_token", self.writer_token)
        _opt_enum("subject", self.subject, BindingSubject)
        _opt_int("launch_id", self.launch_id, minimum=1)
        _opt_int("launch_version", self.launch_version, minimum=1)
        _opt_str("logical_key", self.logical_key)
        _opt_str("physical_key", self.physical_key)
        for name in (
            "lock_files_match",
            "profile_ok",
            "owner_quiescence_ok",
            "root_probe_absent",
            "boot_changed",
            "supervision_confirmed",
        ):
            _bool(name, getattr(self, name))
        if self.basis not in BASES_BY_SCOPE[self.scope]:
            raise ValueError("base incompatível com o escopo")
        has_launch_id = self.launch_id is not None
        has_launch_version = self.launch_version is not None
        if has_launch_id != has_launch_version or (self.scope is EvidenceScope.LAUNCH) != (
            has_launch_id and has_launch_version
        ):
            raise ValueError("launch_id/launch_version existem se e somente se o escopo é launch")
        if (self.scope is EvidenceScope.BINDING) != (self.subject is not None):
            raise ValueError("subject existe se e somente se o escopo é binding")
        if self.basis is _B.LAUNCH_START_FAILED_PROVEN and not self.supervision_confirmed:
            raise ValueError("start_failed_proven exige supervisão confirmada")

    @property
    def negative(self) -> bool:
        return self.basis in NEGATIVE_BASES


def evidence_write_refusal(
    *,
    basis: EvidenceBasis,
    writer_token: str,
    reservation_state: ReservationState,
    owner_token: str,
    adopted_by_token: str | None,
) -> WriteRefusal | None:
    """Inserção de evidência (C8.4). Proveniência conferida **na escrita**:

    * reserva `released` não recebe evidência;
    * o escritor é o dono ou o adotante;
    * base observada ao vivo só enquanto **sem** adoção;
    * quiescência só pelo adotante.
    """
    _enum("basis", basis, EvidenceBasis)
    _str("writer_token", writer_token)
    _enum("reservation_state", reservation_state, ReservationState)
    _str("owner_token", owner_token)
    _opt_str("adopted_by_token", adopted_by_token)
    if reservation_state is ReservationState.RELEASED:
        return WriteRefusal.RESERVATION_RELEASED
    if writer_token != owner_token and writer_token != adopted_by_token:
        return WriteRefusal.WRITER_NOT_PARTICIPANT
    if basis in LIVE_OBSERVED_BASES and adopted_by_token is not None:
        return WriteRefusal.LIVE_BASIS_AFTER_ADOPTION
    if basis is _B.COOPERATIVE_OWNER_QUIESCENCE and (
        adopted_by_token is None or writer_token != adopted_by_token
    ):
        return WriteRefusal.QUIESCENCE_NEEDS_ADOPTER
    return None


# ---------------------------------------------------------------------- snapshot


class RunRole(str, Enum):
    CONTROL = "control"
    DEVELOPER = "developer"
    TEST_RUNNER = "test_runner"


@dataclass(frozen=True, slots=True)
class RunFact:
    """Um Run do ciclo, com a identidade que o banco guarda (`id` e `invocation_id`)."""

    role: RunRole
    status: RunStatus
    run_id: str
    invocation_id: str

    def __post_init__(self) -> None:
        _enum("role", self.role, RunRole)
        _enum("status", self.status, RunStatus)
        _str("run_id", self.run_id)
        _str("invocation_id", self.invocation_id)


class LeaseStatus(str, Enum):
    """Resultado de `decide_lease` já entregue ao snapshot. Só `CONSISTENT` é completo."""

    CONSISTENT = "consistent"
    UNKNOWN = "unknown"
    INCIDENT = "incident"


class HistoryCurrency(str, Enum):
    """O histórico do banco (launches/evidências) é comprovadamente o mais recente? (C7.7)

    `PROVEN_CURRENT` só poderá ser produzido por um mecanismo durável **ainda não autorizado**.
    Até lá o chamador real passa `UNKNOWN`. Banco restaurado de um backup anterior ao lançamento
    perde o registro dele e é indistinguível de "nenhum lançamento"; a ausência de linhas nunca é
    prova.
    """

    PROVEN_CURRENT = "proven_current"
    UNKNOWN = "unknown"
    STALE_SUSPECTED = "stale_suspected"


@dataclass(frozen=True, slots=True)
class ReservationFacts:
    state: ReservationState
    version: int
    control_run_id: str
    owner_token: str
    adopted_by_token: str | None
    logical_key: str
    physical_key: str
    developer_declared: bool
    ever_unproven: bool
    first_unproven_at: datetime | None
    ack_at: datetime | None

    def __post_init__(self) -> None:
        _enum("state", self.state, ReservationState)
        _int("version", self.version, minimum=1)
        _str("control_run_id", self.control_run_id)
        _str("owner_token", self.owner_token)
        _opt_str("adopted_by_token", self.adopted_by_token)
        _str("logical_key", self.logical_key)
        _str("physical_key", self.physical_key)
        _bool("developer_declared", self.developer_declared)
        _bool("ever_unproven", self.ever_unproven)
        _opt_dt("first_unproven_at", self.first_unproven_at)
        _opt_dt("ack_at", self.ack_at)
        if self.adopted_by_token is not None and self.adopted_by_token == self.owner_token:
            raise ValueError("o adotante não pode ser o dono")
        if self.state is ReservationState.UNPROVEN and not self.ever_unproven:
            raise ValueError("unproven implica ever_unproven (pegajoso)")
        if self.ever_unproven and self.first_unproven_at is None:
            raise ValueError("ever_unproven exige first_unproven_at")
        if not self.ever_unproven and self.first_unproven_at is not None:
            raise ValueError("first_unproven_at implica ever_unproven (pegajoso)")


@dataclass(frozen=True, slots=True)
class ReleaseSnapshot:
    """O que o contrato lê. Montado **dentro** da transação de release/finalização; nunca em cache.

    A presença do Developer e do Test Runner **não** é informada: é derivada dos Runs e dos
    launches (`developer_present`, `runner_present`). `lease` e `history` não têm padrão de
    propósito: esquecer de informá-los é erro de tipo, não um `RELEASE` silencioso.
    """

    reservation: ReservationFacts
    launches: tuple[LaunchRecord, ...]
    evidence: tuple[EvidenceRecord, ...]
    runs: tuple[RunFact, ...]
    lease: LeaseStatus
    history: HistoryCurrency

    def __post_init__(self) -> None:
        if type(self.reservation) is not ReservationFacts:
            raise TypeError("reservation: esperado ReservationFacts")
        _tuple_of("launches", self.launches, LaunchRecord)
        _tuple_of("evidence", self.evidence, EvidenceRecord)
        _tuple_of("runs", self.runs, RunFact)
        _enum("lease", self.lease, LeaseStatus)
        _enum("history", self.history, HistoryCurrency)
        if len({e.seq for e in self.evidence}) != len(self.evidence):
            raise ValueError("seq de evidência repetido")
        if len({launch.id for launch in self.launches}) != len(self.launches):
            raise ValueError("id de launch repetido")

    def runs_of(self, role: RunRole) -> tuple[RunFact, ...]:
        _enum("role", role, RunRole)
        return tuple(r for r in self.runs if r.role is role)

    @property
    def developer_present(self) -> bool:
        """Há Developer: Run do Developer **ou** launch do Developer **ou** declaração."""
        return (
            self.reservation.developer_declared
            or bool(self.runs_of(RunRole.DEVELOPER))
            or any(launch.phase_tag is PhaseTag.DEVELOPER for launch in self.launches)
        )

    @property
    def runner_present(self) -> bool:
        """Há Test Runner: Run do runner **ou** launch do runner."""
        return bool(self.runs_of(RunRole.TEST_RUNNER)) or any(
            launch.phase_tag is PhaseTag.TEST_RUNNER for launch in self.launches
        )


@dataclass(frozen=True, slots=True)
class ReleaseRequest:
    """O pedido de liberação. Não carrega evidência."""

    expected_state: ReservationState
    expected_version: int
    releaser_token: str
    seal_present: bool

    def __post_init__(self) -> None:
        _enum("expected_state", self.expected_state, ReservationState)
        _int("expected_version", self.expected_version, minimum=1)
        _str("releaser_token", self.releaser_token)
        _bool("seal_present", self.seal_present)


# ---------------------------------------------------------------------- teardown


class TeardownVerdict(str, Enum):
    PROVEN_CLOSED = "proven_closed"
    STILL_ACTIVE = "still_active"
    TECHNICAL_FAILURE = "technical_failure"
    UNPROVEN = "unproven"


_SEVERITY: dict[TeardownVerdict, int] = {
    TeardownVerdict.PROVEN_CLOSED: 0,
    TeardownVerdict.UNPROVEN: 1,
    TeardownVerdict.TECHNICAL_FAILURE: 2,
    TeardownVerdict.STILL_ACTIVE: 3,
}


class BlockerCode(str, Enum):
    EDGE_ILLEGAL = "edge_illegal"
    VERSION_MISMATCH = "version_mismatch"
    RUN_OPEN = "run_open"
    RUN_CONTROL_MISSING = "run_control_missing"
    RUN_COMPONENT_MISSING = "run_component_missing"
    RUN_DUPLICATED = "run_duplicated"
    RUN_IDENTITY_MISMATCH = "run_identity_mismatch"
    LAUNCH_UNRESOLVED = "launch_unresolved"
    LAUNCH_INTENT_WITHOUT_TERMINAL = "launch_intent_without_terminal"
    LAUNCH_STILL_ACTIVE = "launch_still_active"
    EVIDENCE_STALE = "evidence_stale"
    EVIDENCE_CONTRADICTED = "evidence_contradicted"
    EVIDENCE_FOREIGN = "evidence_foreign"
    DRAIN_UNPROVEN = "drain_unproven"
    BINDING_UNPROVEN = "binding_unproven"
    CONTRADICTION_PROVENANCE = "contradiction_provenance"
    CONTRADICTION_RUNNER_WITHOUT_RUN = "contradiction_runner_without_run"
    CONTRADICTION_RUNNER_BEFORE_DEVELOPER = "contradiction_runner_before_developer"
    CONTRADICTION_DEVELOPER_UNDECLARED = "contradiction_developer_undeclared"
    RELEASER_NOT_PARTICIPANT = "releaser_not_participant"
    SEAL_MISSING = "seal_missing"
    ACK_REQUIRED = "ack_required"
    LEASE_UNKNOWN = "lease_unknown"
    LEASE_INCIDENT = "lease_incident"
    HISTORY_NOT_PROVEN_CURRENT = "history_not_proven_current"
    HISTORY_STALE_SUSPECTED = "history_stale_suspected"


#: Bloqueadores que não são "falta de prova": são indício de integridade perdida.
INCIDENT_CODES: frozenset[BlockerCode] = frozenset(
    {
        BlockerCode.LEASE_INCIDENT,
        BlockerCode.HISTORY_STALE_SUSPECTED,
        BlockerCode.RUN_DUPLICATED,
        BlockerCode.RUN_COMPONENT_MISSING,
        BlockerCode.RUN_IDENTITY_MISMATCH,
        BlockerCode.CONTRADICTION_PROVENANCE,
        BlockerCode.CONTRADICTION_RUNNER_WITHOUT_RUN,
        BlockerCode.CONTRADICTION_RUNNER_BEFORE_DEVELOPER,
        BlockerCode.CONTRADICTION_DEVELOPER_UNDECLARED,
        BlockerCode.EVIDENCE_FOREIGN,
    }
)


@dataclass(frozen=True, slots=True)
class Blocker:
    code: BlockerCode
    subject: str | None = None

    def __post_init__(self) -> None:
        _enum("code", self.code, BlockerCode)
        _opt_str("subject", self.subject)


class ReleaseVerdict(str, Enum):
    RELEASE = "release"
    HOLD = "hold"
    INCIDENT = "incident"


@dataclass(frozen=True, slots=True)
class ReleaseDecision:
    verdict: ReleaseVerdict
    blockers: tuple[Blocker, ...] = ()

    def __post_init__(self) -> None:
        _enum("verdict", self.verdict, ReleaseVerdict)
        _tuple_of("blockers", self.blockers, Blocker)
        if (self.verdict is ReleaseVerdict.RELEASE) != (not self.blockers):
            raise ValueError("RELEASE existe se e somente se não há bloqueador")


@dataclass(frozen=True, slots=True)
class TeardownReport:
    """Veredito dos **recursos enumerados** apenas. Não autoriza `done`/`needs_fix`."""

    verdict: TeardownVerdict
    blockers: tuple[Blocker, ...]

    def __post_init__(self) -> None:
        _enum("verdict", self.verdict, TeardownVerdict)
        _tuple_of("blockers", self.blockers, Blocker)


class Completeness(str, Enum):
    """A enumeração e o histórico são completos e atuais?"""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    INCIDENT = "incident"


@dataclass(frozen=True, slots=True)
class TeardownAssessment:
    """Teardown **completo**: recursos + completude da enumeração/histórico + lease.

    **Diagnóstico, não credencial.** Um valor destes descreve os fatos de UM snapshot num instante;
    ele pode ser copiado (`dataclasses.replace`) e fica obsoleto assim que a evidência muda.
    Nenhuma função de autorização o aceita: `gate_teardown` recebe o **snapshot** e reavalia. Só a
    coerência interna é imposta aqui (comprovado exige recursos fechados, completude e nenhum
    bloqueio), para que um valor incoerente não engane quem o leia.
    """

    resources: TeardownVerdict
    completeness: Completeness
    verdict: TeardownVerdict
    blockers: tuple[Blocker, ...]

    def __post_init__(self) -> None:
        _enum("resources", self.resources, TeardownVerdict)
        _enum("completeness", self.completeness, Completeness)
        _enum("verdict", self.verdict, TeardownVerdict)
        _tuple_of("blockers", self.blockers, Blocker)
        if self.verdict is TeardownVerdict.PROVEN_CLOSED and (
            self.resources is not TeardownVerdict.PROVEN_CLOSED
            or self.completeness is not Completeness.COMPLETE
            or self.blockers
        ):
            raise ValueError(
                "teardown comprovado exige recursos fechados, completude e sem bloqueio"
            )

    @property
    def proven(self) -> bool:
        return self.verdict is TeardownVerdict.PROVEN_CLOSED


@dataclass(slots=True)
class _Resource:
    verdict: TeardownVerdict
    blockers: list[Blocker]


def _res(verdict: TeardownVerdict, *blockers: Blocker) -> _Resource:
    return _Resource(verdict, list(blockers))


def _later_negative(
    snap: ReleaseSnapshot,
    after_seq: int,
    scope: EvidenceScope,
    neg: frozenset[EvidenceBasis],
    *,
    subject: BindingSubject | None = None,
) -> EvidenceRecord | None:
    found: EvidenceRecord | None = None
    for e in snap.evidence:
        if e.scope is not scope or e.basis not in neg or e.seq <= after_seq:
            continue
        if subject is not None and e.subject is not subject:
            continue
        if found is None or e.seq > found.seq:
            found = e
    return found


def _keys_match(snap: ReleaseSnapshot, e: EvidenceRecord) -> bool:
    r = snap.reservation
    return e.logical_key == r.logical_key and e.physical_key == r.physical_key


def _is_quiescence(snap: ReleaseSnapshot, e: EvidenceRecord) -> bool:
    r = snap.reservation
    return (
        e.basis is _B.COOPERATIVE_OWNER_QUIESCENCE
        and r.adopted_by_token is not None
        and e.writer_token == r.adopted_by_token
        and _keys_match(snap, e)
        and e.lock_files_match
    )


def _participant(snap: ReleaseSnapshot, e: EvidenceRecord) -> bool:
    r = snap.reservation
    return e.writer_token == r.owner_token or (
        r.adopted_by_token is not None and e.writer_token == r.adopted_by_token
    )


def _launch_qualifies(snap: ReleaseSnapshot, launch: LaunchRecord, e: EvidenceRecord) -> bool:
    r = snap.reservation
    adopted = r.adopted_by_token
    b = e.basis
    if (
        launch.phase is LaunchPhase.ENDED_UNCONFIRMED
        and launch.end_basis is EndBasis.POPEN_RAISED
        and b is _B.LAUNCH_START_FAILED_PROVEN
        and e.supervision_confirmed
        and e.writer_token == r.owner_token
    ):
        return True
    if (
        launch.phase is LaunchPhase.CREATED
        and launch.platform is Platform.WIN32
        and b is _B.IDENTITY_PROBE_ABSENT
        and _participant(snap, e)
    ):
        return True
    if (
        launch.phase is LaunchPhase.CONTAINED
        and launch.platform is Platform.WIN32
        and launch.containment_profile is ContainmentProfile.WIN_JOB_V1
        and b is _B.JOB_GUARANTEE_VERIFIED
        and e.profile_ok
        and e.owner_quiescence_ok
        and e.root_probe_absent
        and adopted is not None
        and e.writer_token == adopted
        and _keys_match(snap, e)
        and e.lock_files_match
    ):
        return True
    return (
        b is _B.MACHINE_RESTARTED_SINCE_DECLARATION
        and e.boot_changed
        and launch.phase is not LaunchPhase.ENDED_CONFIRMED
        and _participant(snap, e)
    )


def _evaluate_launch(snap: ReleaseSnapshot, launch: LaunchRecord) -> _Resource:
    owner = snap.reservation.owner_token
    subj = f"launch:{launch.id}"
    own = [e for e in snap.evidence if e.scope is EvidenceScope.LAUNCH and e.launch_id == launch.id]
    negatives = [e for e in own if e.basis in _NEG_LAUNCH]

    if launch.phase is LaunchPhase.ENDED_CONFIRMED:
        if launch.ended_by_token != owner:
            return _res(
                TeardownVerdict.UNPROVEN,
                Blocker(BlockerCode.LAUNCH_UNRESOLVED, subj),
                Blocker(BlockerCode.CONTRADICTION_PROVENANCE, subj),
            )
        contradicting = [
            n
            for n in negatives
            if n.launch_version is not None and n.launch_version >= launch.version
        ]
        if not contradicting:
            return _res(TeardownVerdict.PROVEN_CLOSED)
        verdict = (
            TeardownVerdict.STILL_ACTIVE
            if any(n.basis is _B.PROBE_PRESENT for n in contradicting)
            else TeardownVerdict.TECHNICAL_FAILURE
        )
        return _res(verdict, Blocker(BlockerCode.EVIDENCE_CONTRADICTED, subj))

    current = [e for e in own if e.launch_version == launch.version and not e.negative]
    qualifying = [e for e in current if _launch_qualifies(snap, launch, e)]
    for q in sorted(qualifying, key=lambda x: x.seq, reverse=True):
        if not any(n.seq > q.seq for n in negatives):
            return _res(TeardownVerdict.PROVEN_CLOSED)
    if qualifying:
        later = [n for n in negatives if n.seq > min(q.seq for q in qualifying)]
        verdict = (
            TeardownVerdict.STILL_ACTIVE
            if any(n.basis is _B.PROBE_PRESENT for n in later)
            else TeardownVerdict.TECHNICAL_FAILURE
        )
        return _res(verdict, Blocker(BlockerCode.EVIDENCE_CONTRADICTED, subj))
    if any(n.basis is _B.PROBE_PRESENT for n in negatives):
        return _res(TeardownVerdict.STILL_ACTIVE, Blocker(BlockerCode.LAUNCH_STILL_ACTIVE, subj))
    if any(n.basis is _B.LAUNCH_UNCONFIRMED_OBSERVED for n in negatives):
        return _res(TeardownVerdict.TECHNICAL_FAILURE, Blocker(BlockerCode.LAUNCH_UNRESOLVED, subj))
    if any(e.launch_version != launch.version and not e.negative for e in own):
        return _res(TeardownVerdict.UNPROVEN, Blocker(BlockerCode.EVIDENCE_STALE, subj))
    if launch.phase is LaunchPhase.INTENT:
        return _res(
            TeardownVerdict.UNPROVEN, Blocker(BlockerCode.LAUNCH_INTENT_WITHOUT_TERMINAL, subj)
        )
    return _res(TeardownVerdict.UNPROVEN, Blocker(BlockerCode.LAUNCH_UNRESOLVED, subj))


def _evaluate_scope(
    snap: ReleaseSnapshot,
    *,
    scope: EvidenceScope,
    live: frozenset[EvidenceBasis],
    neg: frozenset[EvidenceBasis],
    code: BlockerCode,
    subject: BindingSubject | None,
    label: str,
) -> _Resource:
    owner = snap.reservation.owner_token
    candidates = [
        e
        for e in snap.evidence
        if e.scope is scope
        and (subject is None or e.subject is subject)
        and not e.negative
        and ((e.basis in live and e.writer_token == owner) or _is_quiescence(snap, e))
    ]
    for e in sorted(candidates, key=lambda x: x.seq, reverse=True):
        if _later_negative(snap, e.seq, scope, neg, subject=subject) is None:
            return _res(TeardownVerdict.PROVEN_CLOSED)
    negatives = [
        e
        for e in snap.evidence
        if e.scope is scope and e.basis in neg and (subject is None or e.subject is subject)
    ]
    if negatives:
        top = max(negatives, key=lambda x: x.seq)
        verdict = (
            TeardownVerdict.STILL_ACTIVE
            if top.basis is _B.DRAIN_TIMEOUT
            else TeardownVerdict.TECHNICAL_FAILURE
        )
        return _res(verdict, Blocker(code, label))
    return _res(TeardownVerdict.UNPROVEN, Blocker(code, label))


def _resources(snap: ReleaseSnapshot) -> list[_Resource]:
    """Recursos **exigidos**, derivados do que está presente (nunca de uma flag isolada)."""
    out = [_evaluate_launch(snap, launch) for launch in snap.launches]
    if snap.developer_present:
        out.append(
            _evaluate_scope(
                snap,
                scope=EvidenceScope.IN_PROCESS,
                live=_LIVE_PROC,
                neg=_NEG_PROC,
                code=BlockerCode.DRAIN_UNPROVEN,
                subject=None,
                label="in_process",
            )
        )
    required = [BindingSubject.CONTROL]
    if snap.developer_present:
        required.append(BindingSubject.DEVELOPER)
    if snap.runner_present:
        required.append(BindingSubject.TEST_RUNNER)
    for subject in required:
        out.append(
            _evaluate_scope(
                snap,
                scope=EvidenceScope.BINDING,
                live=_LIVE_BIND,
                neg=_NEG_BIND,
                code=BlockerCode.BINDING_UNPROVEN,
                subject=subject,
                label=subject.value,
            )
        )
    return out


def _worst(verdicts: list[TeardownVerdict]) -> TeardownVerdict:
    return max(verdicts, key=_SEVERITY.__getitem__, default=TeardownVerdict.PROVEN_CLOSED)


def _coherence(snap: ReleaseSnapshot) -> list[Blocker]:
    """Coerência entre flags, Runs, launches e evidência (P1-I1-02). Sem olhar `status` do Run."""
    r = snap.reservation
    out: list[Blocker] = []
    for field in ("run_id", "invocation_id"):
        seen: set[str] = set()
        for run in snap.runs:
            identity = getattr(run, field)
            if identity in seen:
                out.append(Blocker(BlockerCode.RUN_DUPLICATED, f"{field}:{identity}"))
            seen.add(identity)
    expected_inv = {
        RunRole.DEVELOPER: f"{r.control_run_id}:developer",
        RunRole.TEST_RUNNER: f"{r.control_run_id}:test_runner",
    }
    for role in RunRole:
        found = snap.runs_of(role)
        if role is RunRole.CONTROL and not found:
            out.append(Blocker(BlockerCode.RUN_CONTROL_MISSING))
        if len(found) > 1:
            out.append(Blocker(BlockerCode.RUN_DUPLICATED, role.value))
        for run in found:
            if role is RunRole.CONTROL:
                bad = run.run_id != r.control_run_id
            else:
                bad = run.invocation_id != expected_inv[role]
            if bad:
                out.append(Blocker(BlockerCode.RUN_IDENTITY_MISMATCH, role.value))

    has_dev_run = bool(snap.runs_of(RunRole.DEVELOPER))
    has_dev_launch = any(x.phase_tag is PhaseTag.DEVELOPER for x in snap.launches)
    if (has_dev_launch or snap.runner_present) and not has_dev_run:
        out.append(Blocker(BlockerCode.RUN_COMPONENT_MISSING, RunRole.DEVELOPER.value))
    if (has_dev_run or has_dev_launch) and not r.developer_declared:
        out.append(Blocker(BlockerCode.CONTRADICTION_DEVELOPER_UNDECLARED))
    has_runner_launch = any(x.phase_tag is PhaseTag.TEST_RUNNER for x in snap.launches)
    if has_runner_launch and not snap.runs_of(RunRole.TEST_RUNNER):
        out.append(Blocker(BlockerCode.CONTRADICTION_RUNNER_WITHOUT_RUN))
    if snap.runner_present and not r.developer_declared:
        out.append(Blocker(BlockerCode.CONTRADICTION_RUNNER_BEFORE_DEVELOPER))

    known_ids = {launch.id for launch in snap.launches}
    for e in snap.evidence:
        if e.scope is EvidenceScope.LAUNCH and e.launch_id not in known_ids:
            out.append(Blocker(BlockerCode.EVIDENCE_FOREIGN, f"evidence:{e.seq}"))
    return out


def _completeness_blockers(snap: ReleaseSnapshot) -> list[Blocker]:
    out = _coherence(snap)
    if snap.lease is LeaseStatus.INCIDENT:
        out.append(Blocker(BlockerCode.LEASE_INCIDENT))
    elif snap.lease is not LeaseStatus.CONSISTENT:
        out.append(Blocker(BlockerCode.LEASE_UNKNOWN))
    if snap.history is HistoryCurrency.STALE_SUSPECTED:
        out.append(Blocker(BlockerCode.HISTORY_STALE_SUSPECTED))
    elif snap.history is not HistoryCurrency.PROVEN_CURRENT:
        out.append(Blocker(BlockerCode.HISTORY_NOT_PROVEN_CURRENT))
    return out


def _snapshot(value: object) -> ReleaseSnapshot:
    """Confere o tipo **e revalida o conteúdo**: um objeto montado contornando o construtor
    (`object.__setattr__`) é pego aqui, em vez de confiar na anotação."""
    if type(value) is not ReleaseSnapshot:
        raise TypeError("snapshot: esperado ReleaseSnapshot")
    value.__post_init__()
    value.reservation.__post_init__()
    for launch in value.launches:
        launch.__post_init__()
    for e in value.evidence:
        e.__post_init__()
    for run in value.runs:
        run.__post_init__()
    return value


def classify_resources(snapshot: ReleaseSnapshot) -> TeardownReport:
    """Pergunta (1): os recursos **enumerados** estão encerrados? O pior vence.

    Precedência: `STILL_ACTIVE > TECHNICAL_FAILURE > UNPROVEN > PROVEN_CLOSED`. **Não** considera
    completude, histórico nem lease: `PROVEN_CLOSED` aqui NÃO autoriza `done`. Use
    `assess_teardown` para isso.
    """
    snap = _snapshot(snapshot)
    resources = _resources(snap)
    return TeardownReport(
        _worst([x.verdict for x in resources]),
        tuple(b for x in resources for b in x.blockers),
    )


def assess_teardown(snapshot: ReleaseSnapshot) -> TeardownAssessment:
    """Pergunta (3): o teardown **completo** está comprovado? Reutilizada por `decide_release`.

    Combina (1) os recursos enumerados e (2) a completude da enumeração e do histórico (Runs
    coerentes, flags concordando com os registros, lease consistente, histórico comprovado atual).
    Não olha `status` dos Runs, selo, ack nem o pedido de liberação — isso só existe depois da
    finalização. O resultado é diagnóstico: quem autoriza `done`/`needs_fix` é `gate_teardown`, que
    chama esta função sobre o snapshot vigente.
    """
    snap = _snapshot(snapshot)
    resources = _resources(snap)
    res_verdict = _worst([x.verdict for x in resources])
    res_blockers = [b for x in resources for b in x.blockers]
    comp_blockers = _completeness_blockers(snap)
    if any(b.code in INCIDENT_CODES for b in comp_blockers):
        completeness = Completeness.INCIDENT
    elif comp_blockers:
        completeness = Completeness.INCOMPLETE
    else:
        completeness = Completeness.COMPLETE
    verdict = res_verdict
    if (
        completeness is not Completeness.COMPLETE
        and _SEVERITY[verdict] < _SEVERITY[TeardownVerdict.UNPROVEN]
    ):
        verdict = TeardownVerdict.UNPROVEN
    return TeardownAssessment(
        resources=res_verdict,
        completeness=completeness,
        verdict=verdict,
        blockers=tuple(dict.fromkeys([*res_blockers, *comp_blockers])),
    )


# ---------------------------------------------------------------------- liberação


def decide_release(snapshot: ReleaseSnapshot, request: ReleaseRequest) -> ReleaseDecision:
    """Predicado **completo** de liberação (R1–R7 + lease + histórico). Pura e total.

    `RELEASE` ⇔ nenhum bloqueador: o teardown completo (`assess_teardown`) **e** o que só existe
    depois da finalização (Runs fechados, selo, ack, aresta/versão, releaser). A evidência **não**
    vem do pedido: só do snapshot lido dentro da transação.
    """
    snap = _snapshot(snapshot)
    if type(request) is not ReleaseRequest:
        raise TypeError("request: esperado ReleaseRequest")
    request.__post_init__()
    r = snap.reservation
    blockers: list[Blocker] = []

    if r.state is not request.expected_state or r.state not in RELEASABLE_FROM:
        blockers.append(Blocker(BlockerCode.EDGE_ILLEGAL))
    elif r.version != request.expected_version:
        blockers.append(Blocker(BlockerCode.VERSION_MISMATCH))

    blockers.extend(assess_teardown(snap).blockers)

    for run in snap.runs:
        if run.status is RunStatus.RUNNING:
            blockers.append(Blocker(BlockerCode.RUN_OPEN, run.role.value))

    expected_releaser = r.owner_token if r.adopted_by_token is None else r.adopted_by_token
    if request.releaser_token != expected_releaser:
        blockers.append(Blocker(BlockerCode.RELEASER_NOT_PARTICIPANT))
    if not request.seal_present:
        blockers.append(Blocker(BlockerCode.SEAL_MISSING))
    if r.ever_unproven and not (
        r.ack_at is not None and r.first_unproven_at is not None and r.ack_at >= r.first_unproven_at
    ):
        blockers.append(Blocker(BlockerCode.ACK_REQUIRED))

    if not blockers:
        return ReleaseDecision(ReleaseVerdict.RELEASE)
    unique = tuple(dict.fromkeys(blockers))
    verdict = (
        ReleaseVerdict.INCIDENT
        if any(b.code in INCIDENT_CODES for b in unique)
        else ReleaseVerdict.HOLD
    )
    return ReleaseDecision(verdict, unique)


# ---------------------------------------------------------------------- gate de teardown


class FinalizationCode(str, Enum):
    #: O trabalho pode ter ido bem, mas o teardown completo não está comprovado.
    TEARDOWN_UNPROVEN = "teardown_unproven"


@dataclass(frozen=True, slots=True)
class FinalizationDecision:
    """O resultado de `classify_execution` depois do gate. Sem `done`/`needs_fix`/Run `ok` enquanto
    o teardown completo não está comprovado."""

    code: ExecutionCode | FinalizationCode
    run_status: RunStatus
    task_resolution: TaskResolution | None
    teardown_proven: bool
    downgraded: bool
    #: Diagnóstico: por que o teardown não está comprovado (vazio quando comprovado).
    blockers: tuple[Blocker, ...] = ()

    def __post_init__(self) -> None:
        if type(self.code) not in (ExecutionCode, FinalizationCode):
            raise TypeError("code: esperado ExecutionCode ou FinalizationCode")
        _enum("run_status", self.run_status, RunStatus)
        _resolution(self.task_resolution)
        _bool("teardown_proven", self.teardown_proven)
        _bool("downgraded", self.downgraded)
        _tuple_of("blockers", self.blockers, Blocker)
        if self.teardown_proven and self.blockers:
            raise ValueError("teardown comprovado não tem bloqueador")
        if not self.teardown_proven:
            if self.run_status is RunStatus.OK:
                raise ValueError("Run `ok` com teardown não comprovado")
            if self.task_resolution is not None and self.task_resolution.status in (
                TaskStatus.DONE,
                TaskStatus.NEEDS_FIX,
            ):
                raise ValueError("`done`/`needs_fix` com teardown não comprovado")


def gate_teardown(base: ExecutionClassification, snapshot: ReleaseSnapshot) -> FinalizationDecision:
    """Aplica o gate central de teardown **sem alterar** `classify_execution`.

    ## A fronteira de autorização

    O gate recebe os **fatos** (`ReleaseSnapshot`) e chama `assess_teardown` ele mesmo. Não recebe
    — e recusa com `TypeError` — `TeardownAssessment`, `TeardownReport`, `TeardownVerdict`, `bool`,
    string ou qualquer outro objeto no lugar do snapshot: uma avaliação pode ser copiada com
    `dataclasses.replace` e fica obsoleta quando a evidência muda; nenhuma delas vale como
    credencial. Snapshot inválido levanta; snapshot incompleto não comprova (fail closed).

    ## Obrigação do futuro chamador (`ExecutionFinalizer`, ainda não implementado)

    O contrato é puro e só enxerga o snapshot que recebe; **a atualidade dele é do chamador**:

    1. impedir a admissão de novos efeitos pelas barreiras aprovadas (LaunchGate, `draining`);
    2. comprovar a drenagem e o encerramento necessários (executor, launches, bindings);
    3. construir o snapshot **vigente** a partir das fontes autorizadas (banco dentro da
       transação, lease, InstanceLock), sem reaproveitar um snapshot ou uma avaliação anterior;
    4. chamar `gate_teardown(base, snapshot)` com esse snapshot;
    5. persistir a decisão no mesmo contexto transacional protegido, **revalidando** (CAS) o que
       pode mudar entre a leitura e a escrita.

    Nenhuma etapa exige o Run de controle fechado: `assess_teardown` não olha o status dos Runs, por
    isso o teardown é avaliado **antes** do fechamento do Run.

    ## Efeito

    * comprovado (`assess_teardown(snapshot).proven`): o veredito de `base` passa intacto.
    * não comprovado e `base` quer `done`/`needs_fix`: vira `failed(internal_error)` com Run
      `error` e código `teardown_unproven`.
    * não comprovado e a task já tem terminal confirmado (`task_resolution is None`, p.ex.
      `cancelled`): o terminal é **preservado**; um Run `ok` é rebaixado a `error`.
    * não comprovado e `base` já é falha (integridade, política, timeout…): preservada, por ser
      mais grave.
    """
    snap = _snapshot(snapshot)
    if type(base) is not ExecutionClassification:
        raise TypeError("base: esperado ExecutionClassification")
    _enum("base.code", base.code, ExecutionCode)
    _enum("base.run_status", base.run_status, RunStatus)
    _resolution(base.task_resolution)
    assessment = assess_teardown(snap)
    if assessment.proven:
        return FinalizationDecision(base.code, base.run_status, base.task_resolution, True, False)

    why = assessment.blockers
    resolution = base.task_resolution
    if resolution is not None and resolution.status in (TaskStatus.DONE, TaskStatus.NEEDS_FIX):
        return FinalizationDecision(
            FinalizationCode.TEARDOWN_UNPROVEN,
            RunStatus.ERROR,
            TaskResolution(TaskStatus.FAILED, FailureReason.INTERNAL_ERROR),
            False,
            True,
            why,
        )
    if base.run_status is RunStatus.OK:
        return FinalizationDecision(
            FinalizationCode.TEARDOWN_UNPROVEN, RunStatus.ERROR, resolution, False, True, why
        )
    return FinalizationDecision(base.code, base.run_status, resolution, False, False, why)


# ---------------------------------------------------------------------- lease (C7.4)


class LeasePhase(str, Enum):
    RESERVING = "reserving"
    ARMED = "armed"


@dataclass(frozen=True, slots=True)
class LeaseDoc:
    phase: LeasePhase
    reservation_id: str
    logical_key: str
    physical_key: str

    def __post_init__(self) -> None:
        _enum("phase", self.phase, LeasePhase)
        _str("reservation_id", self.reservation_id)
        _str("logical_key", self.logical_key)
        _str("physical_key", self.physical_key)


class LeaseDecision(str, Enum):
    PROCEED = "proceed"
    DISCARD = "discard"
    REMOVE_STALE = "remove_stale"
    RECOVER = "recover"
    INCIDENT = "incident"


def decide_lease(
    lease: LeaseDoc | None,
    *,
    current_logical_key: str,
    current_physical_key: str,
    reservation_found: bool,
    reservation_state: ReservationState | None,
) -> LeaseDecision:
    """Decisão de startup sobre o lease (C7.4). B já detém as duas locks e abriu o banco.

    Entradas inválidas levantam; a fase só pode ser `RESERVING` ou `ARMED` e qualquer outra coisa
    é rejeitada na construção do `LeaseDoc`, nunca vira `RECOVER`.
    """
    if lease is not None:
        if type(lease) is not LeaseDoc:
            raise TypeError("lease: esperado LeaseDoc ou None")
        lease.__post_init__()
    _str("current_logical_key", current_logical_key)
    _str("current_physical_key", current_physical_key)
    _bool("reservation_found", reservation_found)
    _opt_enum("reservation_state", reservation_state, ReservationState)
    if reservation_found != (reservation_state is not None):
        raise ValueError("reservation_found e reservation_state precisam concordar")
    if lease is None:
        return LeaseDecision.PROCEED
    if lease.phase is LeasePhase.RESERVING:
        return LeaseDecision.DISCARD  # `armed` é precondição do `intent`: nenhum efeito existia
    if lease.phase is not LeasePhase.ARMED:
        raise ValueError("fase de lease desconhecida")
    if lease.logical_key != current_logical_key or lease.physical_key != current_physical_key:
        return LeaseDecision.INCIDENT
    if reservation_state is None:
        return LeaseDecision.INCIDENT
    if reservation_state is ReservationState.RELEASED:
        return LeaseDecision.REMOVE_STALE
    return LeaseDecision.RECOVER


__all__ = [
    "BASES_BY_SCOPE",
    "INCIDENT_CODES",
    "LIVE_OBSERVED_BASES",
    "NEGATIVE_BASES",
    "RELEASABLE_FROM",
    "TERMINAL_PHASES",
    "BindingSubject",
    "Blocker",
    "BlockerCode",
    "Completeness",
    "ContainmentProfile",
    "EndBasis",
    "EvidenceBasis",
    "EvidenceRecord",
    "EvidenceScope",
    "FinalizationCode",
    "FinalizationDecision",
    "HistoryCurrency",
    "LaunchPhase",
    "LaunchRecord",
    "LaunchTicket",
    "LeaseDecision",
    "LeaseDoc",
    "LeasePhase",
    "LeaseStatus",
    "PhaseTag",
    "Platform",
    "ReleaseDecision",
    "ReleaseRequest",
    "ReleaseSnapshot",
    "ReleaseVerdict",
    "ReservationFacts",
    "ReservationState",
    "RunFact",
    "RunRole",
    "TeardownAssessment",
    "TeardownReport",
    "TeardownVerdict",
    "TicketState",
    "WriteRefusal",
    "adoption_edge_allowed",
    "assess_teardown",
    "classify_resources",
    "decide_lease",
    "decide_release",
    "evidence_write_refusal",
    "gate_teardown",
    "launch_edge_allowed",
    "launch_insert_refusal",
    "launch_write_refusal",
    "reservation_edge_allowed",
]
