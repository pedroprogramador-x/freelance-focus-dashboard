"""Lado **banco** da reserva durável de execução (E8.4.5 / I4).

Tudo aqui lê ou escreve a tabela `execution_reservation` criada pela migration 0004 e **nada mais**:
não toca `execution_launch` nem `execution_evidence` (o observer SQLite e o `phase_tag` explícito
são do I5), não libera reserva (liberar é I5), não produz evidência e não conhece processo, gate ou
thread. As funções são internas ao Execution Manager: compõem a transação do chamador e **nunca**
fazem `commit`.

## O que o SQLite não prova

O banco guarda `owner_token`, `logical_key` e `physical_key` como texto. Ele **não** autentica o
token e **não** prova a posse do lock do SO: quem lê a coluna só sabe que alguém gravou o texto.
Por isso a identidade chega por uma **porta injetada** (`ReservationIdentityProvider`) que não tem
implementação em `app/`: o InstanceLock e o lease são do I5+, e sem eles não existe identidade
autorizada — logo não existe admissão de produção. Nenhum valor padrão, sentinela ou identidade
sintética é gravado aqui; as portas falsas vivem só em `tests/`.

## Fatos, não credenciais

`effects_gate` responde "esta reserva ainda autoriza **novos efeitos**?" a partir de uma leitura
feita **dentro** da transação do claim (depois de uma escrita, com o lock de escrita do SQLite). É
um predicado de recusa, nunca uma prova de que algo foi drenado ou encerrado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ExecutionReservation

#: Tetos das colunas `owner_token` (64) e `logical_key`/`physical_key` (128).
MAX_OWNER_TOKEN_LENGTH = 64
MAX_KEY_LENGTH = 128

STATE_ACTIVE = "active"
STATE_DRAINING = "draining"
STATE_RELEASED = "released"

#: Códigos dos *triggers* da migration 0004 que o serviço reconhece (e só eles).
SLOT_CONFLICT_MARKERS: tuple[str, ...] = (
    "reservation_insert:slot_conflict",
    "uq_execution_reservation_unreleased",
    "execution_reservation.slot_key",
    "uq_workspace_task_single_executing",
    "workspace_task.status",
)
PURGE_BLOCKED_MARKER = "purge_blocked:unreleased_reservation"


# ------------------------------------------------------------------------------ identidade


def _text(name: str, value: object, limit: int) -> str:
    if type(value) is not str:
        raise TypeError(f"{name}: esperado str, recebido {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{name}: não pode ser vazio nem só espaços")
    if len(value) > limit:
        raise ValueError(f"{name}: excede {limit} caracteres")
    return value


@dataclass(frozen=True, slots=True)
class ReservationIdentity:
    """O que a reserva grava sobre **quem** a detém. Valores fornecidos, nunca inventados aqui.

    `owner_token` **não** é prova de posse do InstanceLock e o SQLite não o autentica. As chaves
    lógica e física identificam o recurso que o InstanceLock protegerá (I5).
    """

    owner_token: str
    logical_key: str
    physical_key: str

    def __post_init__(self) -> None:
        _text("owner_token", self.owner_token, MAX_OWNER_TOKEN_LENGTH)
        _text("logical_key", self.logical_key, MAX_KEY_LENGTH)
        _text("physical_key", self.physical_key, MAX_KEY_LENGTH)


class ReservationIdentityProvider(Protocol):
    """Porta injetada pelo composition root. **Sem implementação em `app/`** (I4)."""

    def reservation_identity(self) -> ReservationIdentity: ...


def resolve_identity(provider: object) -> ReservationIdentity | None:
    """A identidade fornecida, ou `None` quando não há identidade **válida** (fail closed).

    Ausência da porta, exceção da porta, objeto que não é `ReservationIdentity` exato ou revalidação
    do conteúdo falhando (um objeto forjado com `object.__setattr__`) — tudo é "sem identidade".
    """
    if provider is None:
        return None
    method = getattr(provider, "reservation_identity", None)
    if not callable(method):
        return None
    try:
        identity = method()
        if type(identity) is not ReservationIdentity:
            return None
        identity.__post_init__()
    except Exception:
        return None
    return identity


# ------------------------------------------------------------------------------ leitura


@dataclass(frozen=True, slots=True)
class ReservationRow:
    """A reserva como o banco a mostra agora. Valores, não o objeto do identity map."""

    id: str
    task_id: str
    workspace_id: str
    control_run_id: str
    state: str
    version: int
    drain_requested_at: datetime | None
    draining_at: datetime | None
    developer_declared_at: datetime | None
    adopted_by_token: str | None


_COLUMNS = (
    ExecutionReservation.id,
    ExecutionReservation.task_id,
    ExecutionReservation.workspace_id,
    ExecutionReservation.control_run_id,
    ExecutionReservation.state,
    ExecutionReservation.version,
    ExecutionReservation.drain_requested_at,
    ExecutionReservation.draining_at,
    ExecutionReservation.developer_declared_at,
    ExecutionReservation.adopted_by_token,
)


def _row(values: Any) -> ReservationRow:
    return ReservationRow(*values)


def unreleased_reservation_of(session: Session, *, task_id: str) -> ReservationRow | None:
    """A reserva **não liberada** desta task. No máximo uma existe no banco inteiro."""
    found = session.execute(
        select(*_COLUMNS).where(
            ExecutionReservation.task_id == task_id,
            ExecutionReservation.state != STATE_RELEASED,
        )
    ).first()
    return None if found is None else _row(found)


def any_unreleased_reservation(session: Session) -> bool:
    """Existe reserva não liberada em qualquer lugar? É o slot **global** da execução."""
    return (
        session.scalar(
            select(ExecutionReservation.id)
            .where(ExecutionReservation.state != STATE_RELEASED)
            .limit(1)
        )
        is not None
    )


class EffectsGate(str, Enum):
    """Por que uma reserva autoriza ou não **novos efeitos**."""

    OK = "ok"
    MISSING = "missing"
    NOT_ACTIVE = "not_active"
    DRAIN_REQUESTED = "drain_requested"
    ADOPTED = "adopted"
    DEVELOPER_NOT_DECLARED = "developer_not_declared"


def effects_gate(
    session: Session, *, task_id: str, control_run_id: str, require_developer: bool = False
) -> EffectsGate:
    """A reserva **desta tentativa** autoriza novos efeitos agora?

    Exige: existir (casando `task_id` **e** `control_run_id`), estar `active`, sem
    `drain_requested_at` e sem adoção. Com `require_developer`, o Developer precisa estar declarado
    (o Test Runner só nasce depois dele). Chamada de dentro da transação do claim, **depois** de uma
    escrita: o lock de escrita do SQLite impede que outra transação mude a linha até o commit.
    """
    found = session.execute(
        select(*_COLUMNS).where(
            ExecutionReservation.control_run_id == control_run_id,
            ExecutionReservation.task_id == task_id,
        )
    ).first()
    if found is None:
        return EffectsGate.MISSING
    row = _row(found)
    if row.state != STATE_ACTIVE:
        return EffectsGate.NOT_ACTIVE
    if row.drain_requested_at is not None:
        return EffectsGate.DRAIN_REQUESTED
    if row.adopted_by_token is not None:
        return EffectsGate.ADOPTED
    if require_developer and row.developer_declared_at is None:
        return EffectsGate.DEVELOPER_NOT_DECLARED
    return EffectsGate.OK


# ------------------------------------------------------------------------------ escrita


def insert_reservation(
    session: Session,
    *,
    task_id: str,
    workspace_id: str,
    control_run_id: str,
    worktree_ref: str,
    identity: ReservationIdentity,
    now: datetime,
) -> str:
    """Insere a reserva `active` (versão 1) na transação do chamador e devolve o `id`.

    O *trigger* de inserção recusa tudo o que não for uma linha fresca e o índice parcial recusa um
    segundo slot não liberado. Sem `commit`: a atomicidade com o CAS da task e o Run é do chamador.
    """
    reservation = ExecutionReservation(
        task_id=task_id,
        workspace_id=workspace_id,
        control_run_id=control_run_id,
        worktree_ref=worktree_ref,
        state=STATE_ACTIVE,
        version=1,
        owner_token=identity.owner_token,
        logical_key=identity.logical_key,
        physical_key=identity.physical_key,
        created_at=now,
    )
    session.add(reservation)
    session.flush()
    return reservation.id


def mark_drain_requested(
    session: Session, *, reservation_id: str, expected_version: int, now: datetime
) -> bool:
    """tx1: registra `drain_requested_at` (set-once) com CAS em `(id, state, version)`.

    **Não** muda o estado: `active → draining` é a linearização, que só o `LaunchGate` autoriza
    (tx2, `drain.py`). Devolve se exatamente uma linha foi atualizada. A versão sobe em 1 (regra do
    *trigger*).
    """
    result = cast(
        "CursorResult[Any]",
        session.execute(
            update(ExecutionReservation)
            .where(
                ExecutionReservation.id == reservation_id,
                ExecutionReservation.version == expected_version,
                ExecutionReservation.state == STATE_ACTIVE,
                ExecutionReservation.drain_requested_at.is_(None),
                ExecutionReservation.adopted_by_token.is_(None),
            )
            .values(drain_requested_at=now, version=ExecutionReservation.version + 1)
            .execution_options(synchronize_session=False)
        ),
    )
    return result.rowcount == 1


def declare_developer(
    session: Session, *, task_id: str, control_run_id: str, now: datetime
) -> bool:
    """Declara o Developer (set-once) **se e somente se** a reserva ainda autoriza efeitos.

    Os predicados do `WHERE` são a fronteira de autorização (não uma leitura anterior): `active`,
    sem `drain_requested_at`, sem adoção. Depois do pedido de drenagem o `UPDATE` não casa nenhuma
    linha — o *trigger* da 0004 **não** impede essa escrita, o serviço sim (limitação registrada).
    """
    result = cast(
        "CursorResult[Any]",
        session.execute(
            update(ExecutionReservation)
            .where(
                ExecutionReservation.control_run_id == control_run_id,
                ExecutionReservation.task_id == task_id,
                ExecutionReservation.state == STATE_ACTIVE,
                ExecutionReservation.drain_requested_at.is_(None),
                ExecutionReservation.adopted_by_token.is_(None),
            )
            .values(
                developer_declared_at=func.coalesce(
                    ExecutionReservation.developer_declared_at, now
                ),
                version=ExecutionReservation.version + 1,
            )
            .execution_options(synchronize_session=False)
        ),
    )
    return result.rowcount == 1


# ------------------------------------------------------------------------------ erros do banco


def _driver_message(error: BaseException) -> str:
    return str(getattr(error, "orig", None) or error)


def is_slot_conflict(error: BaseException) -> bool:
    """O `IntegrityError` é o conflito do **slot** (task ou reserva)? Qualquer outro, não."""
    return isinstance(error, IntegrityError) and any(
        marker in _driver_message(error) for marker in SLOT_CONFLICT_MARKERS
    )


def is_reservation_purge_conflict(error: BaseException) -> bool:
    """O `IntegrityError` é a recusa de purga de uma reserva não liberada (triggers da 0004)?"""
    return isinstance(error, IntegrityError) and PURGE_BLOCKED_MARKER in _driver_message(error)


__all__ = [
    "MAX_KEY_LENGTH",
    "MAX_OWNER_TOKEN_LENGTH",
    "PURGE_BLOCKED_MARKER",
    "SLOT_CONFLICT_MARKERS",
    "STATE_ACTIVE",
    "STATE_DRAINING",
    "STATE_RELEASED",
    "EffectsGate",
    "ReservationIdentity",
    "ReservationIdentityProvider",
    "ReservationRow",
    "any_unreleased_reservation",
    "declare_developer",
    "effects_gate",
    "insert_reservation",
    "is_reservation_purge_conflict",
    "is_slot_conflict",
    "mark_drain_requested",
    "resolve_identity",
    "unreleased_reservation_of",
]
