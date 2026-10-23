"""Drenagem do executor em processo e a sua evidência R3 (E8.4.5 / Entrega A).

O "executor" é onde o trabalho do Developer roda dentro do processo (threads do provider). Antes de
dizer que uma reserva pode ser liberada, é preciso ter **observado** que nenhuma operação dele
segue em voo. Este módulo dá duas peças, ambas sem IO:

* `InProcessExecutor`: contabiliza as operações em voo (`operation()`) e fecha a admissão de novas
  (`drain()`). O fechamento é **irreversível**, como o `stop` do `LaunchGate`.
* `drain_with_evidence`: drena e grava, pelo `EvidenceWriter`, o fato **exato** que foi observado.

## Três desfechos, três fatos

* `DRAINED` (nenhuma operação em voo ao fim da espera) → `executor_drained`;
* `TIMEOUT` (o prazo esgotou com operações em voo) → `drain_timeout`;
* `REENTRANT` (`drain()` chamado de dentro de uma operação em voo da própria thread) →
  `drain_reentrant`.

`REENTRANT` não espera: esperar por si mesma seria um deadlock. `TIMEOUT` deixa o executor fechado
(nenhuma operação nova entra) e a evidência negativa; uma drenagem posterior que termine gravará a
positiva com `seq` maior — e só a ordem do `seq` decide (negativa posterior anula positiva
anterior). Se a gravação da evidência falha, a exceção sobe: **o desfecho não vale como prova**.

## Ordem de locks

O executor é o rank mais baixo (`executor (10) < gate (20) < transação SQLite (30)`). Este módulo
não importa `process_runtime` (regra de camadas): quem monta o executor injeta `rank`, um
gerenciador de contexto que declara o rank EXECUTOR (`ranked(LockRank.EXECUTOR)`). Entrar numa
operação ou esperar a drenagem segurando gate ou transação viola a ordem e levanta — antes de
bloquear. `rank` é obrigatório e sem padrão: um executor sem conferência de ordem não existe.
"""

from __future__ import annotations

import contextlib
import math
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import Enum

from app.orchestrator.evidence_ledger import EvidenceWriter


class DrainOutcome(str, Enum):
    DRAINED = "drained"
    TIMEOUT = "timeout"
    REENTRANT = "reentrant"


class ExecutorClosed(RuntimeError):
    """A admissão do executor foi fechada (drenagem pedida): nenhuma operação nova entra."""


def _timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("prazo precisa ser um número finito > 0")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("prazo precisa ser um número finito > 0")
    return min(number, threading.TIMEOUT_MAX)


class InProcessExecutor:
    """Contabilidade das operações em voo de **uma** tentativa."""

    def __init__(self, *, rank: Callable[[], AbstractContextManager[None]]) -> None:
        if not callable(rank):
            raise TypeError("rank: esperado um callable que devolve um gerenciador de contexto")
        self._rank = rank
        self._cond = threading.Condition(threading.Lock())
        self._closed = False
        self._active: dict[int, int] = {}

    @property
    def closed(self) -> bool:
        with self._cond:
            return self._closed

    @property
    def in_flight(self) -> int:
        with self._cond:
            return sum(self._active.values())

    @contextlib.contextmanager
    def operation(self) -> Iterator[None]:
        """Uma operação em voo. Recusada (`ExecutorClosed`) depois do fechamento."""
        with self._rank():
            ident = threading.get_ident()
            with self._cond:
                if self._closed:
                    raise ExecutorClosed("o executor está fechado para novas operações")
                self._active[ident] = self._active.get(ident, 0) + 1
            try:
                yield
            finally:
                with self._cond:
                    remaining = self._active[ident] - 1
                    if remaining:
                        self._active[ident] = remaining
                    else:
                        del self._active[ident]
                    self._cond.notify_all()

    def drain(self, timeout_s: float) -> DrainOutcome:
        """Fecha a admissão e espera as operações em voo, até `timeout_s`."""
        timeout = _timeout(timeout_s)
        ident = threading.get_ident()
        with self._cond:
            self._closed = True
            if self._active.get(ident, 0) > 0:
                return DrainOutcome.REENTRANT  # esperaria por si mesma
        with self._rank(), self._cond:  # `rank` confere a ordem de locks antes de bloquear
            if self._cond.wait_for(lambda: not self._active, timeout=timeout):
                return DrainOutcome.DRAINED
            return DrainOutcome.TIMEOUT


@dataclass(frozen=True, slots=True)
class DrainRecord:
    """O desfecho da drenagem e o `seq` da evidência que o registra."""

    outcome: DrainOutcome
    evidence_seq: int


def drain_with_evidence(
    executor: InProcessExecutor, writer: EvidenceWriter, timeout_s: float
) -> DrainRecord:
    """Drena e grava a evidência do desfecho observado. A exceção da gravação sobe."""
    if type(executor) is not InProcessExecutor:
        raise TypeError("executor: esperado InProcessExecutor")
    if type(writer) is not EvidenceWriter:
        raise TypeError("writer: esperado EvidenceWriter")
    outcome = executor.drain(timeout_s)
    if outcome is DrainOutcome.DRAINED:
        seq = writer.executor_drained()
    elif outcome is DrainOutcome.TIMEOUT:
        seq = writer.drain_timeout()
    else:
        seq = writer.drain_reentrant()
    return DrainRecord(outcome, seq)


__all__ = [
    "DrainOutcome",
    "DrainRecord",
    "ExecutorClosed",
    "InProcessExecutor",
    "drain_with_evidence",
]
