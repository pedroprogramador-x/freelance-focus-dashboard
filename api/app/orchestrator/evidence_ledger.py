"""Escritor de evidência R3/R4 do dono (E8.4.5 / Entrega A) — o único escritor de
`execution_evidence`.

A evidência é **append-only** e a ordem é o `seq`: uma negativa posterior anula uma positiva
anterior, nunca o contrário. Este módulo grava só o que o **dono** observou ao vivo, enquanto a
reserva não foi adotada:

* `in_process`: positiva `executor_drained`; negativas `drain_timeout` e `drain_reentrant`;
* `binding`: positivas `binding_released_observed` e `binding_revoked_observed`; negativa
  `binding_unproven`.

Fora do escopo desta entrega (Entrega B: recovery, adoção, lease): `cooperative_owner_quiescence`,
`quiescence_unproven`, `identity_probe_absent`, `job_guarantee_verified`,
`machine_restarted_since_declaration`, `probe_present`, `launch_unconfirmed_observed` e
`launch_start_failed_proven` (esta exige a confirmação da árvore pelo supervisor, que a porta do
observer não transporta). Elas não têm método aqui de propósito.

## Proveniência (C8.4) na escrita

Antes do `INSERT`, `evidence_write_refusal` (contrato I1) e a regra "o escritor é o dono": o
adotante não existe nesta entrega. O trigger `execution_evidence_insert_guard` continua o árbitro
final e confere o mesmo. A evidência nunca vem do pedido de liberação.

## O que NÃO é prova

Um desfecho sem observação positiva (`NOT_BOUND`, `REFUSED` do binding do Developer) **não** grava
evidência: ausência não é prova, e o contrato segue pedindo a prova até que ela exista. Uma
liberação que falhou (`RELEASE_FAILED`) grava `binding_unproven`.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Final

from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ExecutionEvidence, ExecutionReservation
from app.db.session import session_scope
from app.orchestrator import teardown_contract as tc
from app.orchestrator.reservation_ledger import MAX_OWNER_TOKEN_LENGTH


class EvidenceRefused(RuntimeError):
    """O ledger recusou a evidência. `reason` é um código estável."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BindingObservation(str, Enum):
    """O que o dono observou sobre um binding. Vocabulário próprio: não herda o do Developer."""

    RELEASED = "released"
    REVOKED = "revoked"
    UNPROVEN = "unproven"


_BASIS_OF: Final[dict[BindingObservation, tc.EvidenceBasis]] = {
    BindingObservation.RELEASED: tc.EvidenceBasis.BINDING_RELEASED_OBSERVED,
    BindingObservation.REVOKED: tc.EvidenceBasis.BINDING_REVOKED_OBSERVED,
    BindingObservation.UNPROVEN: tc.EvidenceBasis.BINDING_UNPROVEN,
}


#: O desfecho do binding do Developer (`BindingOutcome.value`, E8.4.3) → o que ele comprova. Os
#: valores são literais: este módulo não importa a invocação do Developer (a integração é a C), e
#: um teste cruza esta tabela com o enum real. `not_bound` e `refused` ficam de fora de propósito.
_OBSERVATION_OF_OUTCOME: Final[dict[str, BindingObservation]] = {
    "released": BindingObservation.RELEASED,
    "revoked": BindingObservation.REVOKED,
    "release_failed": BindingObservation.UNPROVEN,
}


def observation_of(outcome: object) -> BindingObservation | None:
    """A observação que um desfecho do binding do Developer comprova, ou `None`.

    `outcome` é um `BindingOutcome` (identificado pelo `value` string). `release_failed` é a única
    negativa. `not_bound` e `refused` não comprovam nada: devolvem `None` e **nenhuma evidência é
    gravada** (a ausência continua bloqueando a liberação). Valor desconhecido levanta.
    """
    value = getattr(outcome, "value", None)
    if type(value) is not str or type(outcome).__name__ != "BindingOutcome":
        raise TypeError("outcome: esperado BindingOutcome")
    if value in ("not_bound", "refused"):
        return None
    try:
        return _OBSERVATION_OF_OUTCOME[value]
    except KeyError:
        raise ValueError(f"desfecho de binding desconhecido: {value}") from None


class EvidenceWriter:
    """Grava a evidência R3/R4 de **uma** reserva, como o dono dela."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | Callable[[], Session],
        *,
        reservation_id: str,
        writer_token: str,
    ) -> None:
        if type(reservation_id) is not str or not reservation_id.strip():
            raise TypeError("reservation_id: esperado str não vazio")
        if (
            type(writer_token) is not str
            or not writer_token.strip()
            or len(writer_token) > MAX_OWNER_TOKEN_LENGTH
        ):
            raise TypeError("writer_token: esperado str não vazio")
        self._factory = session_factory
        self._reservation_id = reservation_id
        self._writer_token = writer_token

    # ------------------------------------------------------------------ R3 — operações em voo

    def executor_drained(self) -> int:
        """O executor foi observado drenado (nenhuma operação em voo). Positiva e viva."""
        return self._append(tc.EvidenceScope.IN_PROCESS, tc.EvidenceBasis.EXECUTOR_DRAINED)

    def drain_timeout(self) -> int:
        """O prazo da drenagem esgotou com operações em voo. Negativa."""
        return self._append(tc.EvidenceScope.IN_PROCESS, tc.EvidenceBasis.DRAIN_TIMEOUT)

    def drain_reentrant(self) -> int:
        """A drenagem foi pedida de dentro de uma operação em voo (esperaria por si mesma)."""
        return self._append(tc.EvidenceScope.IN_PROCESS, tc.EvidenceBasis.DRAIN_REENTRANT)

    # ------------------------------------------------------------------ R4 — bindings

    def binding(self, subject: tc.BindingSubject, observation: BindingObservation) -> int:
        """O desfecho observado do binding de `subject`."""
        if type(subject) is not tc.BindingSubject:
            raise TypeError("subject: esperado BindingSubject")
        if type(observation) is not BindingObservation:
            raise TypeError("observation: esperado BindingObservation")
        return self._append(tc.EvidenceScope.BINDING, _BASIS_OF[observation], subject=subject)

    def developer_binding(self, outcome: object) -> int | None:
        """Grava o desfecho do binding do Developer; `None` quando nada foi comprovado."""
        observation = observation_of(outcome)
        if observation is None:
            return None
        return self.binding(tc.BindingSubject.DEVELOPER, observation)

    # ------------------------------------------------------------------ escrita

    def _append(
        self,
        scope: tc.EvidenceScope,
        basis: tc.EvidenceBasis,
        *,
        subject: tc.BindingSubject | None = None,
    ) -> int:
        # Valida a forma como o banco validará (escopo×base, subject iff binding). `seq` é só um
        # marcador: o definitivo vem do banco.
        tc.EvidenceRecord(
            seq=0, scope=scope, basis=basis, writer_token=self._writer_token, subject=subject
        )
        with session_scope(self._factory) as session:  # type: ignore[arg-type]
            reservation = session.get(ExecutionReservation, self._reservation_id)
            if reservation is None:
                raise EvidenceRefused("reservation_missing")
            if reservation.owner_token != self._writer_token:
                raise EvidenceRefused(tc.WriteRefusal.WRITER_NOT_OWNER.value)
            refusal = tc.evidence_write_refusal(
                basis=basis,
                writer_token=self._writer_token,
                reservation_state=tc.ReservationState(reservation.state),
                owner_token=reservation.owner_token,
                adopted_by_token=reservation.adopted_by_token,
            )
            if refusal is not None:
                raise EvidenceRefused(refusal.value)
            row = ExecutionEvidence(
                reservation_id=self._reservation_id,
                scope=scope.value,
                subject=None if subject is None else subject.value,
                basis=basis.value,
                writer_token=self._writer_token,
            )
            session.add(row)
            session.flush()
            return int(row.seq)


__all__ = [
    "BindingObservation",
    "EvidenceRefused",
    "EvidenceWriter",
    "observation_of",
]
