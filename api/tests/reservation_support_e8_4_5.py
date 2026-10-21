"""Apoio dos testes do I4 (E8.4.5): portas **falsas** de identidade e de gate. Só testes.

Nada aqui é código de produção e nada daqui pode ser importado por `app/`. Em produção não existe
`ReservationIdentityProvider`: o InstanceLock e o lease são do I5+ (um teste de arquitetura trava
isso). As identidades abaixo são valores **válidos** (para o CHECK do banco) fornecidos por um banco
temporário real — não são sentinelas e não provam posse de lock algum.
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.orchestrator.reservation_ledger import ReservationIdentity


class FakeIdentityProvider:
    """Entrega sempre a mesma identidade válida (token aleatório por instância)."""

    def __init__(
        self,
        *,
        owner_token: str | None = None,
        logical_key: str | None = None,
        physical_key: str | None = None,
    ) -> None:
        self.identity = ReservationIdentity(
            owner_token=owner_token or secrets.token_hex(32),
            logical_key=logical_key or f"logical-{secrets.token_hex(8)}",
            physical_key=physical_key or f"physical-{secrets.token_hex(8)}",
        )
        self.calls = 0

    def reservation_identity(self) -> ReservationIdentity:
        self.calls += 1
        return self.identity


class RaisingIdentityProvider:
    def reservation_identity(self) -> ReservationIdentity:
        raise RuntimeError("identidade indisponível")


class WrongTypeIdentityProvider:
    """Devolve algo que parece uma identidade mas não é `ReservationIdentity`."""

    def reservation_identity(self) -> Any:
        class _Lookalike:
            owner_token = "t" * 10
            logical_key = "l"
            physical_key = "p"

        return _Lookalike()


#: Identidade padrão dos helpers de admissão legados.
DEFAULT_IDENTITY = FakeIdentityProvider()


def release_for_test(db_path: Path, task_id: str) -> None:
    """Libera a reserva não liberada da task pelos **mesmos triggers** do I3 (SQL cru, só teste).

    Fecha o Run de controle, grava a evidência do binding de controle (como o dono) e leva a
    reserva a `draining` → `released`. É o equivalente de teste do que o `release_reservation` do I5
    fará com as provas R1–R7: aqui existe só para exercitar o cenário legítimo "o slot volta
    quando a reserva é liberada". O I4 não tem código de produção que libere.
    """
    from tests.execution_reservation_support_e8_4_5 import Db

    db = Db(db_path)
    try:
        row = db.one(
            "SELECT id, control_run_id, state FROM execution_reservation"
            " WHERE task_id = ? AND state <> 'released'",
            (task_id,),
        )
        assert row is not None, "nenhuma reserva não liberada para esta task"
        rid, control, state = row
        if db.scalar("SELECT status FROM run WHERE id = ?", (control,)) == "running":
            db.close_run(control, "cancelled")
        owner = db.scalar("SELECT owner_token FROM execution_reservation WHERE id = ?", (rid,))
        db.binding(rid, "control", writer=owner)
        if state == "active":
            fields: dict[str, Any] = {
                "state": "draining",
                "draining_at": "2026-01-01 00:00:03.000000",
            }
            if (
                db.scalar(
                    "SELECT drain_requested_at FROM execution_reservation WHERE id = ?", (rid,)
                )
                is None
            ):
                fields["drain_requested_at"] = "2026-01-01 00:00:02.000000"
            db.update_reservation(rid, **fields)
        assert db.release(rid, by=owner) == 1
    finally:
        db.close()


class SpyGate:
    """Um gate falso: registra as chamadas e devolve o que o teste mandar.

    `outcome` é um callable que recebe `(timeout_s, commit)` e devolve o objeto de resultado (com
    `.status.value`). Por padrão não executa `commit` nenhum.
    """

    def __init__(self, outcome: Callable[[float, Callable[[], Any]], Any] | None = None) -> None:
        self.stops = 0
        self.closes: list[float] = []
        self._outcome = outcome
        self.lock = threading.Lock()

    def request_stop(self) -> None:
        with self.lock:
            self.stops += 1

    def close_and_linearize(self, timeout_s: float, commit: Callable[[], Any]) -> Any:
        with self.lock:
            self.closes.append(timeout_s)
        if self._outcome is None:
            raise AssertionError("SpyGate sem outcome não deveria ser fechado")
        return self._outcome(timeout_s, commit)
