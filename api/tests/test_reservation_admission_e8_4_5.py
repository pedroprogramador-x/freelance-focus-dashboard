"""E8.4.5 / I4 — admissão atômica com a reserva durável.

Banco SQLite **real** migrado pelo Alembic (triggers da 0004 incluídos). A identidade da reserva vem
de uma porta **falsa de teste**; em produção ela não existe e a admissão permanece bloqueada.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings
from app.db.enums import SafetyEventKind, TaskStatus
from app.db.models import DevWorkspace, Run, SafetyEvent, WorkspaceTask
from app.orchestrator import TransitionGuardFailed, cancel
from app.orchestrator import execution_manager as em
from app.orchestrator.execution_contract import AdmissionGuard, AdmissionOutcome
from app.orchestrator.reservation_ledger import (
    ReservationIdentity,
    is_slot_conflict,
    resolve_identity,
)
from tests.admission_support_e8_4_1 import admit, approved_task, make_prover
from tests.execution_reservation_support_e8_4_5 import Db
from tests.reservation_support_e8_4_5 import (
    DEFAULT_IDENTITY,
    FakeIdentityProvider,
    RaisingIdentityProvider,
    WrongTypeIdentityProvider,
)
from tests.test_execution_admission_e8_4_1 import _barreira_de_threads

KEY = "exec-key-1"


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    return tmp_path / "artifacts"


@pytest.fixture
def approved(session: Session, workspace: DevWorkspace, artifacts_dir: Path) -> WorkspaceTask:
    return approved_task(session, workspace, artifacts_dir)


@pytest.fixture
def rdb(temp_settings: AppSettings, migrated_url: str) -> Iterator[Db]:
    del migrated_url
    db = Db(Path(temp_settings.sqlalchemy_url.split("///", 1)[1]))
    try:
        yield db
    finally:
        db.close()


def _reservations(db: Db, task_id: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM execution_reservation"
    params: tuple[Any, ...] = ()
    if task_id is not None:
        sql += " WHERE task_id = ?"
        params = (task_id,)
    cursor = db.run(sql, params)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _runs(factory: sessionmaker[Session], task_id: str) -> list[Run]:
    with factory() as fresh:
        rows = list(fresh.scalars(select(Run).where(Run.task_id == task_id)))
        fresh.expunge_all()
        return rows


def _task(factory: sessionmaker[Session], task_id: str) -> WorkspaceTask:
    with factory() as fresh:
        task = fresh.get(WorkspaceTask, task_id)
        assert task is not None
        fresh.expunge(task)
        return task


def _nothing_consumed(
    factory: sessionmaker[Session], db: Db, task_id: str, *, attempts: int = 0
) -> None:
    task = _task(factory, task_id)
    assert task.status is TaskStatus.APPROVED and task.attempts == attempts and task.phase is None
    assert _runs(factory, task_id) == []
    assert _reservations(db, task_id) == []


# ------------------------------------------------------------------ caminho feliz e atomicidade


def test_a_admissao_cria_exatamente_uma_reserva_ligada_ao_run_de_controle(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    rdb: Db,
) -> None:
    admission = admit(session, approved.id, artifacts_dir, key=KEY)

    assert admission.outcome is AdmissionOutcome.ADMITTED and admission.run is not None
    [row] = _reservations(rdb)
    assert row["task_id"] == approved.id and row["workspace_id"] == approved.workspace_id
    assert row["control_run_id"] == admission.run.id
    assert row["state"] == "active" and row["version"] == 1 and row["slot_key"] == 1
    assert row["worktree_ref"] == f"ff-task-{approved.id[:8]}"
    # a identidade é a que a porta forneceu, sem sentinela e sem derivar de nada
    assert row["owner_token"] == DEFAULT_IDENTITY.identity.owner_token
    assert row["logical_key"] == DEFAULT_IDENTITY.identity.logical_key
    assert row["physical_key"] == DEFAULT_IDENTITY.identity.physical_key
    for column in (
        "adopted_by_token",
        "developer_declared_at",
        "drain_requested_at",
        "draining_at",
        "released_at",
        "released_by_token",
        "release_evidence_hash",
        "ack_at",
    ):
        assert row[column] is None, column
    assert row["ever_unproven"] == 0
    task = _task(session_factory, approved.id)
    assert task.status is TaskStatus.EXECUTING and task.attempts == 1


def test_falha_no_insert_da_reserva_desfaz_cas_attempts_e_run(
    session: Session,
    session_factory: sessionmaker[Session],
    engine: Engine,
    approved: WorkspaceTask,
    artifacts_dir: Path,
    rdb: Db,
) -> None:
    boom = RuntimeError("falha injetada no INSERT da reserva")

    @event.listens_for(engine, "before_cursor_execute")
    def _fail(_c: Any, _cur: Any, statement: str, *_: Any) -> None:
        if statement.lstrip().upper().startswith("INSERT INTO EXECUTION_RESERVATION"):
            raise boom

    try:
        with pytest.raises(Exception) as caught:
            admit(session, approved.id, artifacts_dir, key=KEY)
        assert boom in (caught.value, caught.value.__cause__, getattr(caught.value, "orig", None))
    finally:
        event.remove(engine, "before_cursor_execute", _fail)

    _nothing_consumed(session_factory, rdb, approved.id)
    # sem efeito residual: a mesma chave agora admite normalmente
    assert admit(session, approved.id, artifacts_dir, key=KEY).outcome is AdmissionOutcome.ADMITTED
    assert len(_reservations(rdb, approved.id)) == 1


def test_a_repeticao_devolve_o_mesmo_run_sem_nova_reserva_e_sem_exigir_a_porta(
    session: Session, approved: WorkspaceTask, artifacts_dir: Path, rdb: Db
) -> None:
    primeira = admit(session, approved.id, artifacts_dir, key=KEY)
    version = _reservations(rdb)[0]["version"]

    repetida = admit(session, approved.id, artifacts_dir, key=KEY, identity=None)

    assert repetida.outcome is AdmissionOutcome.REPLAYED
    assert primeira.run is not None and repetida.run is not None
    assert repetida.run.id == primeira.run.id
    [row] = _reservations(rdb)
    assert row["version"] == version


# ------------------------------------------------------------------------- slot global


def test_task_cancelled_com_reserva_nao_liberada_ainda_ocupa_o_slot(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    approved: WorkspaceTask,
    artifacts_dir: Path,
    rdb: Db,
) -> None:
    segunda = approved_task(session, workspace, artifacts_dir, title="segunda")
    admit(session, approved.id, artifacts_dir, key=KEY)
    cancel(session, approved.id)
    session.commit()
    assert _task(session_factory, approved.id).status is TaskStatus.CANCELLED
    prover, verifier = make_prover()

    ocupada = admit(session, segunda.id, artifacts_dir, key="k2", prover=prover)

    assert ocupada.outcome is AdmissionOutcome.SLOT_BUSY
    assert ocupada.code == "slot_busy" and ocupada.run is None
    assert verifier.calls == [], "slot ocupado não paga a prova de capability"
    _nothing_consumed(session_factory, rdb, segunda.id)


def test_conflito_do_indice_da_reserva_vira_slot_busy_sem_consumir_nada(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    approved: WorkspaceTask,
    artifacts_dir: Path,
    rdb: Db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O pré-check "não vê" a reserva (corrida): o INSERT bate no trigger `slot_conflict` e a
    admissão resolve como `slot_busy`. A task A está `cancelled`, então só o slot da RESERVA — e não
    o índice de task — pode ser a causa."""
    segunda = approved_task(session, workspace, artifacts_dir, title="segunda")
    admit(session, approved.id, artifacts_dir, key=KEY)
    cancel(session, approved.id)
    session.commit()

    real = em._slot_available
    calls = {"n": 0}

    def stale_first(*args: Any, **kwargs: Any) -> bool:
        calls["n"] += 1
        return True if calls["n"] == 1 else bool(real(*args, **kwargs))

    monkeypatch.setattr(em, "_slot_available", stale_first)

    resultado = admit(session, segunda.id, artifacts_dir, key="k2")

    assert calls["n"] >= 2, "o pré-check foi enganado e a resolução leu o banco de verdade"
    assert resultado.outcome is AdmissionOutcome.SLOT_BUSY and resultado.run is None
    _nothing_consumed(session_factory, rdb, segunda.id)
    assert len(_reservations(rdb)) == 1  # só a da primeira task


def test_integrity_error_que_nao_e_de_slot_nao_e_mascarado_como_ocupado(
    session: Session, workspace: DevWorkspace, approved: WorkspaceTask, artifacts_dir: Path
) -> None:
    segunda = approved_task(session, workspace, artifacts_dir, title="segunda")
    admit(session, approved.id, artifacts_dir, key=KEY)
    session.commit()  # há reserva não liberada: "ocupado" seria a explicação preguiçosa
    session.expire_all()

    unrelated = IntegrityError(
        "INSERT", {}, Exception("UNIQUE constraint failed: run.invocation_id")
    )
    slot = IntegrityError("INSERT", {}, Exception("reservation_insert:slot_conflict"))
    assert is_slot_conflict(slot) and not is_slot_conflict(unrelated)

    with pytest.raises(IntegrityError) as caught:
        em._resolve_lost_claim(session, segunda.id, "k2", unrelated)
    assert caught.value is unrelated
    assert em._resolve_lost_claim(session, segunda.id, "k2", slot).outcome is (
        AdmissionOutcome.SLOT_BUSY
    )


def test_duas_tasks_concorrentes_so_uma_ocupa_o_slot_e_so_uma_reserva_nasce(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    approved: WorkspaceTask,
    artifacts_dir: Path,
    rdb: Db,
) -> None:
    segunda = approved_task(session, workspace, artifacts_dir, title="segunda")

    resultados = _barreira_de_threads(
        session_factory, [(approved.id, "k1"), (segunda.id, "k2")], artifacts_dir
    )

    erros = [r for r in resultados if isinstance(r, Exception)]
    assert erros == []
    outcomes = sorted(r.outcome.value for r in resultados)
    assert outcomes == ["admitted", "slot_busy"]
    assert len(_reservations(rdb)) == 1
    perdedora = next(
        t for t, r in zip((approved, segunda), resultados, strict=True) if r.run is None
    )
    _nothing_consumed(session_factory, rdb, perdedora.id)


# ------------------------------------------------------------- identidade (D3): fail closed


@pytest.mark.parametrize(
    "provider",
    [None, RaisingIdentityProvider(), WrongTypeIdentityProvider(), object()],
    ids=["ausente", "levanta", "tipo_errado", "sem_metodo"],
)
def test_sem_identidade_autorizada_a_admissao_e_recusada_sem_consumir_nada(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    rdb: Db,
    provider: Any,
) -> None:
    with pytest.raises(TransitionGuardFailed) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY, identity=provider)

    assert caught.value.guard == AdmissionGuard.RESERVATION_IDENTITY_AVAILABLE.value
    _nothing_consumed(session_factory, rdb, approved.id)
    with session_factory() as fresh:
        eventos = list(
            fresh.scalars(
                select(SafetyEvent).where(
                    SafetyEvent.task_id == approved.id,
                    SafetyEvent.rule_id
                    == "orchestrator.entry_guard.reservation_identity_available",
                )
            )
        )
    assert len(eventos) == 1
    assert eventos[0].kind is SafetyEventKind.CAPABILITY_UNENFORCEABLE
    # a aprovação NÃO foi invalidada: não é divergência de fingerprint nem de HEAD
    assert _task(session_factory, approved.id).approved_at is not None


def test_a_porta_so_e_chamada_depois_de_repeticao_e_slot_ocupado(
    session: Session, workspace: DevWorkspace, approved: WorkspaceTask, artifacts_dir: Path
) -> None:
    segunda = approved_task(session, workspace, artifacts_dir, title="segunda")
    primeira = FakeIdentityProvider()
    admit(session, approved.id, artifacts_dir, key=KEY, identity=primeira)
    assert primeira.calls == 1

    outra = FakeIdentityProvider()
    assert admit(session, segunda.id, artifacts_dir, key="k2", identity=outra).outcome is (
        AdmissionOutcome.SLOT_BUSY
    )
    admit(session, approved.id, artifacts_dir, key=KEY, identity=outra)  # repetição
    assert outra.calls == 0


def test_identidade_valida_e_sem_sentinela_nem_valor_padrao() -> None:
    for bad in ("", "   ", "x" * 65):
        with pytest.raises(ValueError):
            ReservationIdentity(owner_token=bad, logical_key="l", physical_key="p")
    with pytest.raises(ValueError):
        ReservationIdentity(owner_token="t", logical_key="l" * 129, physical_key="p")
    with pytest.raises(TypeError):
        ReservationIdentity(owner_token=b"t", logical_key="l", physical_key="p")  # type: ignore[arg-type]
    # um objeto forjado por `object.__setattr__` é pego na revalidação
    forged = ReservationIdentity(owner_token="t", logical_key="l", physical_key="p")
    object.__setattr__(forged, "physical_key", "   ")

    class Forging:
        def reservation_identity(self) -> ReservationIdentity:
            return forged

    assert resolve_identity(Forging()) is None
    assert resolve_identity(None) is None
    assert resolve_identity(FakeIdentityProvider()) is not None


def test_cancelar_antes_da_admissao_continua_vencendo_a_corrida(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    rdb: Db,
) -> None:
    """Contrato legado preservado: task `approved` cancelada não vira `executing` nem reserva."""
    cancel(session, approved.id)
    session.commit()

    with pytest.raises(Exception) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert type(caught.value).__name__ == "InvalidTransition"
    assert _reservations(rdb) == []
    assert _runs(session_factory, approved.id) == []
