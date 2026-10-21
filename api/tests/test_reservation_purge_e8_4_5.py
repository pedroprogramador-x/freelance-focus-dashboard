"""E8.4.5 / I4 — a purga é recusada enquanto existir reserva não liberada.

Três camadas, todas exercitadas: (1) validação no serviço, (2) revalidação imediatamente antes do
`DELETE`, (3) *triggers* da migration 0004 (árbitro final, com o conflito reconhecido convertido no
erro de domínio). Reserva `released` não bloqueia, e a linha dela sobrevive à purga (sem FK).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

import app.orchestrator.purge as task_purge
import app.workspace.purge as workspace_purge
from app.config import AppSettings
from app.db.enums import WorkspaceStatus
from app.db.models import DevWorkspace, ExecutionReservation, Run, WorkspaceTask
from app.orchestrator import TaskPurgeBlocked, cancel
from app.orchestrator.purge import execute_purge as execute_task_purge
from app.orchestrator.purge import purge_preview as task_purge_preview
from app.orchestrator.reservation_ledger import is_reservation_purge_conflict
from app.workspace import PurgeTokenStore, update_workspace_status
from app.workspace.errors import WorkspacePurgeBlocked
from app.workspace.purge import execute_purge as execute_workspace_purge
from app.workspace.purge_tokens import task_subject, workspace_subject
from tests.admission_support_e8_4_1 import admit, approved_task
from tests.execution_reservation_support_e8_4_5 import Db, ts
from tests.reservation_support_e8_4_5 import release_for_test

STATES = ("active", "draining", "teardown_pending", "unproven")


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    return tmp_path / "artifacts"


@pytest.fixture
def rdb(temp_settings: AppSettings, migrated_url: str) -> Iterator[Db]:
    del migrated_url
    db = Db(Path(temp_settings.sqlalchemy_url.split("///", 1)[1]))
    try:
        yield db
    finally:
        db.close()


def _db_path(settings: AppSettings) -> Path:
    return Path(settings.sqlalchemy_url.split("///", 1)[1])


def _put_in_state(db: Db, task_id: str, state: str) -> None:
    rid, current = db.one(
        "SELECT id, state FROM execution_reservation WHERE task_id = ?", (task_id,)
    ) or ("", "")
    assert rid and current == "active"
    if state == "draining":
        db.update_reservation(rid, state="draining", draining_at=ts(3))
    elif state == "teardown_pending":
        db.update_reservation(rid, state="teardown_pending")
    elif state == "unproven":
        db.update_reservation(rid, state="teardown_pending")
        db.update_reservation(rid, state="unproven", ever_unproven=1, first_unproven_at=ts(9))


def _terminal_with_reservation(
    session: Session, workspace: DevWorkspace, artifacts_dir: Path, db: Db, state: str
) -> WorkspaceTask:
    """Uma task `cancelled` cuja reserva continua não liberada, no estado pedido."""
    task = approved_task(session, workspace, artifacts_dir)
    assert admit(session, task.id, artifacts_dir, key="exec-1").run is not None
    cancel(session, task.id)  # a task é terminal; a reserva é independente disso
    session.commit()
    _put_in_state(db, task.id, state)
    session.expire_all()
    return task


def _counts(factory: sessionmaker[Session], task_id: str) -> tuple[int, int, int]:
    with factory() as fresh:
        tasks = fresh.scalar(
            select(func.count()).select_from(WorkspaceTask).where(WorkspaceTask.id == task_id)
        )
        runs = fresh.scalar(select(func.count()).select_from(Run).where(Run.task_id == task_id))
        reservations = fresh.scalar(
            select(func.count())
            .select_from(ExecutionReservation)
            .where(ExecutionReservation.task_id == task_id)
        )
        return int(tasks or 0), int(runs or 0), int(reservations or 0)


# ---------------------------------------------------------------------- purga de task


@pytest.mark.parametrize("state", STATES)
def test_task_terminal_com_reserva_nao_liberada_nao_e_purgavel(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    artifacts_dir: Path,
    rdb: Db,
    state: str,
) -> None:
    task = _terminal_with_reservation(session, workspace, artifacts_dir, rdb, state)
    store = PurgeTokenStore()
    token = store.issue(task_subject(task.id))
    before = _counts(session_factory, task.id)

    # a prévia é informativa e continua funcionando
    assert task_purge_preview(session, task.id).tasks == 1
    session.rollback()

    with pytest.raises(TaskPurgeBlocked, match="reserva"):
        execute_task_purge(session, store, task.id, token)
    session.rollback()

    assert _counts(session_factory, task.id) == before == (1, before[1], 1)
    assert before[1] >= 1  # o Run de controle continua lá
    assert store.consume(task_subject(task.id), token) is False  # o token foi consumido


def test_reserva_released_permite_a_purga_e_preserva_o_historico_da_reserva(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    artifacts_dir: Path,
    temp_settings: AppSettings,
    rdb: Db,
) -> None:
    task = _terminal_with_reservation(session, workspace, artifacts_dir, rdb, "active")
    session.commit()  # encerra o snapshot de leitura de antes da liberação
    release_for_test(_db_path(temp_settings), task.id)
    session.commit()
    store = PurgeTokenStore()
    token = store.issue(task_subject(task.id))
    session.expire_all()

    counts = execute_task_purge(session, store, task.id, token)
    session.commit()

    assert counts.tasks == 1 and counts.runs >= 1
    tasks, runs, reservations = _counts(session_factory, task.id)
    assert (tasks, runs) == (0, 0)
    assert reservations == 1  # a reserva liberada sobrevive, como histórico
    assert rdb.scalar("SELECT state FROM execution_reservation WHERE task_id = ?", (task.id,)) == (
        "released"
    )


def test_corrida_entre_a_revalidacao_e_o_delete_e_resolvida_pelo_trigger_como_erro_de_dominio(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    artifacts_dir: Path,
    rdb: Db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As camadas 1 e 2 "não veem" a reserva (corrida): o DELETE bate no trigger da 0004 e o
    conflito RECONHECIDO vira `TaskPurgeBlocked` (409), nunca um `IntegrityError` cru (500)."""
    task = _terminal_with_reservation(session, workspace, artifacts_dir, rdb, "draining")
    monkeypatch.setattr(task_purge, "_refuse_unreleased_reservation", lambda *_a, **_k: None)
    store = PurgeTokenStore()
    token = store.issue(task_subject(task.id))
    before = _counts(session_factory, task.id)

    with pytest.raises(TaskPurgeBlocked, match="banco"):
        execute_task_purge(session, store, task.id, token)

    assert _counts(session_factory, task.id) == before  # nada foi apagado


def test_integrity_error_que_nao_e_de_reserva_na_purga_nao_e_convertido(
    session: Session,
    workspace: DevWorkspace,
    artifacts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = approved_task(session, workspace, artifacts_dir)
    cancel(session, task.id)
    session.commit()
    unrelated = IntegrityError("DELETE", {}, Exception("FOREIGN KEY constraint failed"))
    assert not is_reservation_purge_conflict(unrelated)

    def boom(*_a: Any, **_k: Any) -> None:
        raise unrelated

    monkeypatch.setattr(session, "flush", boom)
    store = PurgeTokenStore()
    token = store.issue(task_subject(task.id))

    with pytest.raises(IntegrityError) as caught:
        execute_task_purge(session, store, task.id, token)
    assert caught.value is unrelated


def test_o_trigger_da_0004_protege_o_delete_direto_da_task(
    session: Session,
    workspace: DevWorkspace,
    artifacts_dir: Path,
    rdb: Db,
) -> None:
    """Camada 3 sozinha, sem o serviço: o ORM também é recusado pelo banco."""
    task = _terminal_with_reservation(session, workspace, artifacts_dir, rdb, "teardown_pending")
    row = session.get(WorkspaceTask, task.id)
    assert row is not None

    session.delete(row)
    with pytest.raises(IntegrityError) as caught:
        session.flush()
    session.rollback()

    assert is_reservation_purge_conflict(caught.value)


def test_task_sem_reserva_continua_purgavel_como_antes(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    artifacts_dir: Path,
) -> None:
    task = approved_task(session, workspace, artifacts_dir)
    cancel(session, task.id)
    session.commit()
    store = PurgeTokenStore()
    token = store.issue(task_subject(task.id))

    execute_task_purge(session, store, task.id, token)
    session.commit()

    assert _counts(session_factory, task.id) == (0, 0, 0)


# ------------------------------------------------------------------ purga de workspace


def _archive(session: Session, workspace: DevWorkspace) -> None:
    update_workspace_status(session, workspace.id, WorkspaceStatus.ARCHIVED)
    session.commit()
    session.expire_all()


@pytest.mark.parametrize("state", STATES)
def test_workspace_arquivado_com_reserva_nao_liberada_nao_e_purgavel(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    artifacts_dir: Path,
    rdb: Db,
    state: str,
) -> None:
    task = _terminal_with_reservation(session, workspace, artifacts_dir, rdb, state)
    _archive(session, workspace)
    store = PurgeTokenStore()
    token = store.issue(workspace_subject(workspace.id))

    with pytest.raises(WorkspacePurgeBlocked, match="reserva"):
        execute_workspace_purge(session, store, workspace.id, token)
    session.rollback()

    with session_factory() as fresh:
        assert fresh.get(DevWorkspace, workspace.id) is not None
    assert _counts(session_factory, task.id)[0] == 1
    assert store.consume(workspace_subject(workspace.id), token) is False


def test_workspace_com_reserva_released_e_purgavel_e_a_reserva_sobrevive(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    artifacts_dir: Path,
    temp_settings: AppSettings,
    rdb: Db,
) -> None:
    task = _terminal_with_reservation(session, workspace, artifacts_dir, rdb, "active")
    task_id = task.id
    session.commit()  # encerra o snapshot de leitura de antes da liberação
    release_for_test(_db_path(temp_settings), task_id)
    session.commit()
    _archive(session, workspace)
    store = PurgeTokenStore()
    token = store.issue(workspace_subject(workspace.id))

    execute_workspace_purge(session, store, workspace.id, token)
    session.commit()

    with session_factory() as fresh:
        assert fresh.get(DevWorkspace, workspace.id) is None
    assert _counts(session_factory, task_id) == (0, 0, 1)


def test_corrida_na_purga_de_workspace_tambem_vira_erro_de_dominio(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    artifacts_dir: Path,
    rdb: Db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _terminal_with_reservation(session, workspace, artifacts_dir, rdb, "active")
    _archive(session, workspace)
    monkeypatch.setattr(workspace_purge, "_refuse_unreleased_reservation", lambda *_a, **_k: None)
    store = PurgeTokenStore()
    token = store.issue(workspace_subject(workspace.id))

    with pytest.raises(WorkspacePurgeBlocked, match="banco"):
        execute_workspace_purge(session, store, workspace.id, token)
    session.rollback()

    with session_factory() as fresh:
        assert fresh.get(DevWorkspace, workspace.id) is not None
