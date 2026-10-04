"""`InMemoryDecisionJournal` (E7.5-A): a implementação de referência de `DecisionJournal`.

O Protocol, o `ToolDecisionRecord` e a `DecisionCategory` vivem em `contracts.py`. O journal
**não persiste**: guarda em memória, na ordem de chegada, e quem o lê (o Execution Manager, ou
um teste) é quem grava `SafetyEvent`. `tool_executor` não importa `db`.
"""

from __future__ import annotations

import threading

from app.tool_executor.contracts import ToolDecisionRecord
from app.tool_executor.validation import require_instance


class InMemoryDecisionJournal:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[ToolDecisionRecord] = []

    def append(self, record: ToolDecisionRecord) -> None:
        require_instance("record", record, ToolDecisionRecord)
        with self._lock:
            self._records.append(record)

    def records(self) -> tuple[ToolDecisionRecord, ...]:
        """Snapshot imutável, na ordem em que as negações chegaram."""
        with self._lock:
            return tuple(self._records)

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


__all__ = ["InMemoryDecisionJournal"]
