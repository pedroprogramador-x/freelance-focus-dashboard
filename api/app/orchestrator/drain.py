"""Pedido durável de drenagem e linearização pelo `LaunchGate` (E8.4.5 / I4).

## Duas coisas que não se confundem

* **Pedido de drenagem** (tx1): `drain_requested_at` gravado no banco, junto do cancelamento da
  task. É durável e já basta para que nenhum claim nem `intent` novo seja autorizado. **Não**
  prova nada sobre processos, threads ou o gate.
* **Linearização** (tx2): `active → draining`, executada **dentro** de
  `LaunchGate.close_and_linearize` (mutex do gate + barreira SQLite). Só o retorno `LINEARIZED` do
  gate, com a tx2 commitada, autoriza dizer "o fechamento ocorreu". Mesmo assim isto é um fato do
  banco sobre o gate **desta** tentativa, não uma prova de que os processos terminaram: a prova de
  encerramento é R1–R7 do I1 (I5+). Nenhum caminho aqui libera a reserva.

## O que NÃO é credencial

`gate.closed` (estado em memória) **não** é credencial de fechamento, e este módulo nunca o lê. Após
`GATE_TIMEOUT` ou `COMMIT_FAILED` nada é declarado: sem retry, sem releitura do banco "para
confirmar", sem evidência gravada. O marcador durável (`drain_requested_at` com a reserva `active`
ou `draining`) é o que o recovery do I5/I6 vai ler; a reserva sai desse limbo por adoção, nunca por
inferência do estado em memória.

## O gate certo

O gate vem **só** do `GateRegistry`, pela chave `control_run_id` lida da própria reserva no banco.
O escopo ambiente (`current_scope()`) nunca é consultado. O registry é uma instância injetada, não
um global: nenhuma tentativa herda o gate de outra. O `orchestrator` não importa `process_runtime`
(regra de camadas), então o gate é uma porta estrutural (`GatePort`) e o status do gate é comparado
pelo **valor** do enum (um teste cruza os valores com `LinearizeStatus`).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from sqlalchemy import CursorResult, update
from sqlalchemy.orm import Session

from app.db.models import ExecutionReservation
from app.orchestrator.reservation_ledger import STATE_ACTIVE, STATE_DRAINING

#: Valores de `LinearizeStatus` (process_runtime.launch). Um teste os cruza.
LINEARIZED = "linearized"
GATE_TIMEOUT_VALUE = "gate_timeout"
COMMIT_FAILED = "commit_failed"

#: Prazo padrão do fechamento (espelha `DEFAULT_GATE_TIMEOUT_S` de `launch.py`; um teste cruza).
DEFAULT_DRAIN_TIMEOUT_S = 10.0


class DrainStatus(str, Enum):
    #: A task não tem reserva não liberada (fluxo legado): nada a drenar.
    NOT_REQUIRED = "not_required"
    #: tx1 durável, mas sem gate para esta tentativa (ou sem coordinator): a reserva segue `active`
    #: e **ninguém comprovou fechamento**. Caminho explicitamente não operacional.
    REQUESTED_NO_GATE = "requested_no_gate"
    #: `LINEARIZED` + tx2 commitada: `active → draining` no banco. Não é prova de encerramento.
    DRAINING_COMMITTED = "draining_committed"
    #: O gate não foi obtido no prazo: L não ocorreu. A reserva segue `active` com o pedido.
    GATE_TIMEOUT = "gate_timeout"
    #: O commit da tx2 falhou ou o resultado é incerto: nada é declarado.
    COMMIT_UNCERTAIN = "commit_uncertain"
    #: A reserva já estava em `draining`/`teardown_pending`/`unproven`: nada foi escrito.
    ALREADY_PAST = "already_past"


@dataclass(frozen=True, slots=True)
class DrainResult:
    status: DrainStatus
    control_run_id: str | None = None
    reservation_version: int | None = None

    @property
    def linearization_confirmed(self) -> bool:
        """Só `DRAINING_COMMITTED` — nunca derivado de estado em memória."""
        return self.status is DrainStatus.DRAINING_COMMITTED


class GatePort(Protocol):
    """O que o coordinator exige do `LaunchGate` (estrutural; ver o docstring do módulo)."""

    def request_stop(self) -> None: ...

    def close_and_linearize(self, timeout_s: float, commit: Callable[[], Any]) -> Any: ...


class DrainPreconditionError(RuntimeError):
    """Uso incorreto do coordinator (transação aberta, gate inválido). Falha fechada."""


class GateRegistry:
    """`control_run_id → gate` daquela tentativa. Instância injetada; sem `unregister` no I4
    (remover o gate de uma reserva é parte de liberá-la, I5)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._gates: dict[str, GatePort] = {}

    def register(self, control_run_id: str, gate: GatePort) -> None:
        if type(control_run_id) is not str or not control_run_id:
            raise TypeError("control_run_id: esperado str não vazio")
        for method in ("request_stop", "close_and_linearize"):
            if not callable(getattr(gate, method, None)):
                raise TypeError(f"gate precisa expor {method}()")
        with self._lock:
            if control_run_id in self._gates:
                raise ValueError("já existe um gate registrado para esta tentativa")
            self._gates[control_run_id] = gate

    def gate_for(self, control_run_id: str) -> GatePort | None:
        with self._lock:
            return self._gates.get(control_run_id)


class DrainNotApplied(RuntimeError):
    """A tx2 não casou exatamente uma linha (versão, estado ou pedido mudaram)."""


class DrainCoordinator:
    """Orquestra o `request_stop` e a tx2 sob o gate **da tentativa certa**."""

    def __init__(
        self,
        registry: GateRegistry,
        session_factory: Callable[[], Session],
        *,
        gate_timeout_s: float = DEFAULT_DRAIN_TIMEOUT_S,
    ) -> None:
        if not isinstance(registry, GateRegistry):
            raise TypeError("registry: esperado GateRegistry")
        if not callable(session_factory):
            raise TypeError("session_factory: esperado callable")
        self._registry = registry
        self._session_factory = session_factory
        self._timeout_s = gate_timeout_s

    def request_stop(self, control_run_id: str) -> bool:
        """`request_stop` no gate desta tentativa, **antes** da tx1. `False` = sem gate local."""
        gate = self._registry.gate_for(control_run_id)
        if gate is None:
            return False
        gate.request_stop()
        return True

    def linearize(
        self, *, session: Session, control_run_id: str, expected_version: int, now: datetime
    ) -> DrainResult:
        """tx2 sob o gate. A `session` do chamador NÃO pode ter transação aberta: o gate (rank 20)
        nunca é tomado com uma transação SQLite (rank 30) pendente."""
        if session.in_transaction():
            raise DrainPreconditionError(
                "linearize exige uma sessão sem transação aberta (ordem de locks gate < SQLite)"
            )
        gate = self._registry.gate_for(control_run_id)
        if gate is None:
            return DrainResult(DrainStatus.REQUESTED_NO_GATE, control_run_id, expected_version)

        def commit_tx2() -> int:
            with self._session_factory() as tx2:
                try:
                    result: CursorResult[Any] = tx2.execute(  # type: ignore[assignment]
                        update(ExecutionReservation)
                        .where(
                            ExecutionReservation.control_run_id == control_run_id,
                            ExecutionReservation.version == expected_version,
                            ExecutionReservation.state == STATE_ACTIVE,
                            ExecutionReservation.drain_requested_at.is_not(None),
                            ExecutionReservation.adopted_by_token.is_(None),
                        )
                        .values(
                            state=STATE_DRAINING,
                            draining_at=now,
                            version=ExecutionReservation.version + 1,
                        )
                        .execution_options(synchronize_session=False)
                    )
                    if result.rowcount != 1:
                        raise DrainNotApplied("a reserva mudou entre a tx1 e a tx2")
                    tx2.commit()
                except BaseException:
                    tx2.rollback()
                    raise
            return expected_version + 1

        outcome = gate.close_and_linearize(self._timeout_s, commit_tx2)
        status = getattr(getattr(outcome, "status", None), "value", None)
        if status == LINEARIZED:
            return DrainResult(DrainStatus.DRAINING_COMMITTED, control_run_id, expected_version + 1)
        if status == GATE_TIMEOUT_VALUE:
            return DrainResult(DrainStatus.GATE_TIMEOUT, control_run_id, expected_version)
        # `commit_failed` e qualquer valor desconhecido: fail closed, nada é declarado.
        return DrainResult(DrainStatus.COMMIT_UNCERTAIN, control_run_id, expected_version)


__all__ = [
    "COMMIT_FAILED",
    "DEFAULT_DRAIN_TIMEOUT_S",
    "GATE_TIMEOUT_VALUE",
    "LINEARIZED",
    "DrainCoordinator",
    "DrainNotApplied",
    "DrainPreconditionError",
    "DrainResult",
    "DrainStatus",
    "GatePort",
    "GateRegistry",
]
