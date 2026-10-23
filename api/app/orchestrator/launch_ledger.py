"""Observer SQLite do ledger de lançamentos (E8.4.5 / Entrega A) — o único escritor de
`execution_launch`.

Implementa a porta estrutural `LaunchObserver` do Supervisor sem importar `process_runtime`
(regra de camadas): os tipos neutros (`LaunchIntent`, `ProcessIdentity`, `LaunchEnd`) chegam por
**atributo**, não por import. Cada evento (`intent`, `created`, `contained`, `ended`) abre a sua
própria transação (rank SQLITE do `launch.py`), faz commit e devolve; nada fica aberto durante a
vida do processo.

## O que este módulo recusa antes de o banco recusar

O SQLite continua sendo o árbitro final (triggers da 0004); aqui se confere o mesmo, antes, para
que a recusa venha com um motivo legível e para que nenhuma linha inválida chegue a ser tentada:

* `intent` sem `phase_tag` declarado → recusa. **Não existe `other` como padrão.**
* `intent` de outra reserva (o contexto declarou uma, o observer é de outra) → recusa.
* `intent` quando o dono da reserva não é este observer, ou quando a reserva não está `active`, já
  foi adotada ou tem `drain_requested_at` (`launch_insert_refusal` do contrato) → recusa.
* `intent` de Developer/Test Runner antes de o Developer estar declarado → recusa (a liberação
  trataria o lançamento como contradição de proveniência).

Qualquer exceção em `intent` impede o lançamento (`INTENT_FAILED`): nenhum processo nasce.

## Fatos vêm do banco, não do handle

O handle só carrega o `id` da linha. A fase e a versão que valem para a próxima escrita são lidas do
banco **dentro** da transação: se uma escrita anterior falhou depois de commitar (resultado
incerto), a linha real é a que decide, e o término gravado nunca contradiz o que está lá.
O término é o CAS `(id, version)`: nenhuma outra escrita pode ter passado antes.

## Proveniência

Toda escrita é do dono (`owner_token`), na hora da escrita (C8.1). O observer nunca é o adotante:
o recovery (Entrega B) não passa por aqui.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ExecutionLaunch, ExecutionReservation
from app.db.session import session_scope
from app.orchestrator import teardown_contract as tc
from app.orchestrator.reservation_ledger import MAX_OWNER_TOKEN_LENGTH

#: Fases que só existem depois de o Developer ser declarado (`developer_declared_at`).
_NEEDS_DEVELOPER: Final = frozenset({"developer", "test_runner"})
#: As fases que um contexto pode declarar. `other` existe no vocabulário do banco, mas nenhuma
#: escrita daqui o produz.
_DECLARABLE_TAGS: Final = frozenset({"preparation", "developer", "test_runner"})

_END_OF: Final[dict[str, tuple[tc.LaunchPhase, tc.EndBasis]]] = {
    "refused_before_creation": (
        tc.LaunchPhase.ENDED_CONFIRMED,
        tc.EndBasis.REFUSED_BEFORE_CREATION,
    ),
    "tree_confirmed_dead": (tc.LaunchPhase.ENDED_CONFIRMED, tc.EndBasis.TREE_CONFIRMED_DEAD),
    "tree_unconfirmed": (tc.LaunchPhase.ENDED_UNCONFIRMED, tc.EndBasis.TREE_UNCONFIRMED),
    "popen_raised": (tc.LaunchPhase.ENDED_UNCONFIRMED, tc.EndBasis.POPEN_RAISED),
    "ledger_write_failed": (tc.LaunchPhase.ENDED_UNCONFIRMED, tc.EndBasis.LEDGER_WRITE_FAILED),
}


class LaunchRefused(RuntimeError):
    """O ledger recusou a escrita. `reason` é um código estável (nunca texto livre do chamador)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class LaunchHandle:
    """O handle opaco que o supervisor repassa. Só a identidade da linha: nenhum fato de fase."""

    launch_id: int
    reservation_id: str


def _state(row: ExecutionReservation) -> tc.ReservationState:
    try:
        return tc.ReservationState(row.state)
    except ValueError as error:
        raise LaunchRefused("reservation_state_unknown") from error


class SqliteLaunchObserver:
    """O `LaunchObserver` durável de **uma** reserva.

    `owner_token` é o do dono da reserva: o observer só escreve se for exatamente o que está na
    linha. Não prova posse de InstanceLock algum (Entrega B).
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session] | Callable[[], Session],
        *,
        reservation_id: str,
        owner_token: str,
    ) -> None:
        if type(reservation_id) is not str or not reservation_id.strip():
            raise TypeError("reservation_id: esperado str não vazio")
        if (
            type(owner_token) is not str
            or not owner_token.strip()
            or len(owner_token) > MAX_OWNER_TOKEN_LENGTH
        ):
            raise TypeError("owner_token: esperado str não vazio")
        self._factory = session_factory
        self._reservation_id = reservation_id
        self._owner_token = owner_token

    @property
    def reservation_id(self) -> str:
        return self._reservation_id

    # ------------------------------------------------------------------ leituras

    def _reservation(self, session: Session) -> ExecutionReservation:
        row = session.get(ExecutionReservation, self._reservation_id)
        if row is None:
            raise LaunchRefused("reservation_missing")
        if row.owner_token != self._owner_token:
            raise LaunchRefused(tc.WriteRefusal.WRITER_NOT_OWNER.value)
        return row

    def _launch(self, session: Session, handle: object) -> ExecutionLaunch:
        if type(handle) is not LaunchHandle or handle.reservation_id != self._reservation_id:
            raise LaunchRefused("handle_foreign")
        row = session.get(ExecutionLaunch, handle.launch_id)
        if row is None or row.reservation_id != self._reservation_id:
            raise LaunchRefused("launch_missing")
        return row

    # ------------------------------------------------------------------ eventos

    def intent(self, intent: Any) -> object:
        reservation_id = getattr(intent, "reservation_id", None)
        if reservation_id != self._reservation_id:
            raise LaunchRefused("reservation_mismatch")
        tag = getattr(intent, "phase_tag", None)
        tag_value = getattr(tag, "value", None)
        if tag_value not in _DECLARABLE_TAGS:
            raise LaunchRefused("phase_tag_missing")
        with session_scope(self._factory) as session:  # type: ignore[arg-type]
            reservation = self._reservation(session)
            refusal = tc.launch_insert_refusal(
                reservation_state=_state(reservation),
                adopted_by_token=reservation.adopted_by_token,
                drain_requested=reservation.drain_requested_at is not None,
            )
            if refusal is not None:
                raise LaunchRefused(refusal.value)
            if tag_value in _NEEDS_DEVELOPER and reservation.developer_declared_at is None:
                raise LaunchRefused("developer_not_declared")
            row = ExecutionLaunch(
                reservation_id=self._reservation_id,
                phase_tag=tag_value,
                exe_name=intent.exe_name,
                platform=intent.platform.value,
                containment_profile=intent.profile,
                phase=tc.LaunchPhase.INTENT.value,
                version=1,
            )
            session.add(row)
            session.flush()
            return LaunchHandle(row.id, self._reservation_id)

    def created(self, handle: object, identity: Any) -> None:
        pid, start_token = int(identity.pid), str(identity.start_token)
        self._advance(handle, tc.LaunchPhase.CREATED, None, pid=pid, start_token=start_token)

    def contained(self, handle: object) -> None:
        self._advance(handle, tc.LaunchPhase.CONTAINED, None)

    def ended(self, handle: object, end: Any) -> None:
        key = getattr(end, "value", None)
        if key not in _END_OF:
            raise LaunchRefused("end_basis_unknown")
        phase, basis = _END_OF[key]
        self._advance(handle, phase, basis)

    # ------------------------------------------------------------------ escrita (CAS)

    def _advance(
        self,
        handle: object,
        new_phase: tc.LaunchPhase,
        end_basis: tc.EndBasis | None,
        **identity: Any,
    ) -> None:
        with session_scope(self._factory) as session:  # type: ignore[arg-type]
            reservation = self._reservation(session)
            launch = self._launch(session, handle)
            old_phase = tc.LaunchPhase(launch.phase)
            refusal = tc.launch_write_refusal(
                reservation_state=_state(reservation),
                owner_token=reservation.owner_token,
                adopted_by_token=reservation.adopted_by_token,
                writer_token=self._owner_token,
                old_phase=old_phase,
                new_phase=new_phase,
                end_basis=end_basis,
            )
            if refusal is not None:
                raise LaunchRefused(refusal.value)
            values: dict[str, Any] = {
                "phase": new_phase.value,
                "version": launch.version + 1,
                **identity,
            }
            if end_basis is not None:
                values["end_basis"] = end_basis.value
                values["ended_by_token"] = self._owner_token
            result = session.execute(
                update(ExecutionLaunch)
                .where(
                    ExecutionLaunch.id == launch.id,
                    ExecutionLaunch.version == launch.version,
                    ExecutionLaunch.phase == old_phase.value,
                )
                .values(**values)
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise LaunchRefused("cas_lost")


__all__ = ["LaunchHandle", "LaunchRefused", "SqliteLaunchObserver"]
