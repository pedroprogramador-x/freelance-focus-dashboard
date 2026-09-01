"""Invariantes que o **banco** verifica.

Cada teste aqui corresponde a uma frase normativa de
`docs/architecture/02-data-model.md`. A ideia é que uma regressão futura falhe no
`INSERT`, não numa revisão de código.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, exc, text
from sqlalchemy.dialects.sqlite.pysqlite import SQLiteDialect_pysqlite
from sqlalchemy.orm import Session

from app.db import enums
from app.db.base import UtcDateTime, new_uuid
from app.db.models import DevWorkspace, Run, WorkspaceTask


def _workspace(session: Session, *, path: str = "/tmp/ws-a") -> DevWorkspace:
    workspace = DevWorkspace(
        name="workspace de teste",
        type=enums.WorkspaceType.PERSONAL,
        local_path=path,
        status=enums.WorkspaceStatus.ACTIVE,
    )
    session.add(workspace)
    session.commit()
    return workspace


def _task(session: Session, workspace: DevWorkspace, **overrides: object) -> WorkspaceTask:
    values: dict[str, object] = {
        "workspace_id": workspace.id,
        "title": "tarefa",
        "goal": "objetivo",
        "status": enums.TaskStatus.DRAFT,
        "risk": enums.RiskLevel.LOW,
        "complexity": enums.ComplexityLevel.TRIVIAL,
        "risk_source": enums.RiskSource.HARD_RULE,
        "execution_mode": enums.ExecutionMode.ORCHESTRATED,
    }
    values.update(overrides)
    task = WorkspaceTask(**values)
    session.add(task)
    session.commit()
    return task


def _run(session: Session, task: WorkspaceTask, **overrides: object) -> Run:
    values: dict[str, object] = {
        "invocation_id": f"inv-{new_uuid()}",
        "task_id": task.id,
        "agent": enums.RunAgent.DEVELOPER,
        "purpose": enums.RunPurpose.EXECUTION,
        "provider": "none",
        "provider_adapter": "none",
        "transport": enums.RunTransport.PROCESS,
        "status": enums.RunStatus.OK,
        "token_source": enums.TokenSource.UNAVAILABLE,
        "files_read": None,
        "files_read_source": enums.FilesReadSource.UNAVAILABLE,
    }
    values.update(overrides)
    run = Run(**values)
    session.add(run)
    session.commit()
    return run


# ------------------------------------------------------------------ WorkspaceTask


def test_phase_existe_se_e_somente_se_executing(session: Session) -> None:
    workspace = _workspace(session)

    with pytest.raises(exc.IntegrityError):
        _task(session, workspace, status=enums.TaskStatus.DRAFT, phase=enums.TaskPhase.TESTING)
    session.rollback()

    with pytest.raises(exc.IntegrityError):
        _task(session, workspace, status=enums.TaskStatus.EXECUTING, phase=None)
    session.rollback()

    ok = _task(
        session,
        workspace,
        status=enums.TaskStatus.EXECUTING,
        phase=enums.TaskPhase.IMPLEMENTING,
    )
    assert ok.phase is enums.TaskPhase.IMPLEMENTING


def test_failure_reason_apenas_em_falha_real(session: Session) -> None:
    """[02] §3: nunca em `cancelled`, nunca em `done`. Desistir não é falha (AUD-011)."""
    workspace = _workspace(session)

    with pytest.raises(exc.IntegrityError):
        _task(
            session,
            workspace,
            status=enums.TaskStatus.CANCELLED,
            failure_reason=enums.FailureReason.INTERRUPTED,
        )
    session.rollback()

    with pytest.raises(exc.IntegrityError):
        _task(session, workspace, status=enums.TaskStatus.FAILED, failure_reason=None)
    session.rollback()

    ok = _task(
        session,
        workspace,
        status=enums.TaskStatus.FAILED,
        failure_reason=enums.FailureReason.TIMEOUT,
    )
    assert ok.failure_reason is enums.FailureReason.TIMEOUT


def test_aprovacao_exige_plano_manifest_e_fingerprint(session: Session) -> None:
    workspace = _workspace(session)

    with pytest.raises(exc.IntegrityError):
        _task(session, workspace, approved_at=datetime.now(UTC))
    session.rollback()


def test_enum_invalido_e_recusado(session: Session) -> None:
    workspace = _workspace(session)
    task = _task(session, workspace)

    with pytest.raises(exc.IntegrityError):
        session.execute(
            text("UPDATE workspace_task SET status = 'teleported' WHERE id = :id"),
            {"id": task.id},
        )
        session.commit()
    session.rollback()


# ------------------------------------------------------------------------- Run


def test_invocation_id_e_unico(session: Session) -> None:
    """Idempotência de `Run` ([02] §8): a mesma tentativa não vira dois registros."""
    workspace = _workspace(session)
    task = _task(session, workspace)
    _run(session, task, invocation_id="inv-repetido")

    with pytest.raises(exc.IntegrityError):
        _run(session, task, invocation_id="inv-repetido")
    session.rollback()


def test_duas_auditorias_do_mesmo_sujeito_sao_permitidas(session: Session) -> None:
    """REAUD-005: a constraint antiga impedia reauditoria legítima. Não pode voltar."""
    workspace = _workspace(session)
    task = _task(session, workspace)
    subject = _run(session, task)

    first = _run(
        session,
        task,
        agent=enums.RunAgent.AUDITOR,
        purpose=enums.RunPurpose.WORKFLOW_AUDIT,
        subject_run_id=subject.id,
    )
    second = _run(
        session,
        task,
        agent=enums.RunAgent.AUDITOR,
        purpose=enums.RunPurpose.WORKFLOW_AUDIT,
        subject_run_id=subject.id,
        supersedes_run_id=first.id,
        attempt_index=0,
        fix_round=0,
    )

    assert second.subject_run_id == subject.id
    assert second.supersedes_run_id == first.id


def test_auditoria_exige_subject_run_id(session: Session) -> None:
    workspace = _workspace(session)
    task = _task(session, workspace)

    with pytest.raises(exc.IntegrityError):
        _run(
            session,
            task,
            agent=enums.RunAgent.AUDITOR,
            purpose=enums.RunPurpose.WORKFLOW_AUDIT,
            subject_run_id=None,
        )
    session.rollback()


def test_files_read_nulo_se_e_somente_se_indisponivel(session: Session) -> None:
    """AUD-008: `[]` é *medido e nada foi lido*; ausência é `NULL`. Nunca `[]` para ausência."""
    workspace = _workspace(session)
    task = _task(session, workspace)

    medido = _run(
        session,
        task,
        files_read=[],
        files_read_source=enums.FilesReadSource.REPORTED,
    )
    assert medido.files_read == []

    with pytest.raises(exc.IntegrityError):
        _run(session, task, files_read=[], files_read_source=enums.FilesReadSource.UNAVAILABLE)
    session.rollback()

    with pytest.raises(exc.IntegrityError):
        _run(
            session,
            task,
            files_read=["src/a.py"],
            files_read_source=enums.FilesReadSource.UNAVAILABLE,
        )
    session.rollback()

    with pytest.raises(exc.IntegrityError):
        _run(session, task, files_read=None, files_read_source=enums.FilesReadSource.REPORTED)
    session.rollback()


def test_run_finalizado_e_append_only(session: Session, engine: Engine) -> None:
    """[02] §8: um `Run` finalizado nunca é reaberto — nem por `UPDATE` direto."""
    workspace = _workspace(session)
    task = _task(session, workspace)
    run = _run(session, task, finished_at=datetime.now(UTC), duration_ms=10)

    with engine.connect() as connection, pytest.raises(exc.DatabaseError):
        connection.execute(
            text("UPDATE run SET summary = 'reescrito' WHERE id = :id"), {"id": run.id}
        )
        connection.commit()


def test_run_em_andamento_ainda_pode_ser_atualizado(session: Session, engine: Engine) -> None:
    workspace = _workspace(session)
    task = _task(session, workspace)
    run = _run(session, task, finished_at=None)

    with engine.connect() as connection:
        connection.execute(
            text("UPDATE run SET summary = 'progresso' WHERE id = :id"), {"id": run.id}
        )
        connection.commit()

    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT summary FROM run WHERE id = :id"), {"id": run.id}
        ).scalar_one()
    assert stored == "progresso"


# ----------------------------------------------------------------- SafetyEvent


def test_safety_event_recusa_update_e_delete(engine: Engine) -> None:
    """[02] §12: trilha append-only, não cascateada."""
    with engine.connect() as connection:
        connection.execute(
            text(
                "INSERT INTO safety_event"
                " (id, kind, decision, rule_id, subject, created_at)"
                " VALUES (:id, 'path_denied', 'deny', 'path.escapes_root', '«redigido»',"
                " '2026-08-31 00:00:00')"
            ),
            {"id": new_uuid()},
        )
        connection.commit()

    with engine.connect() as connection, pytest.raises(exc.DatabaseError):
        connection.execute(text("UPDATE safety_event SET rule_id = 'x'"))
        connection.commit()

    with engine.connect() as connection, pytest.raises(exc.DatabaseError):
        connection.execute(text("DELETE FROM safety_event"))
        connection.commit()


def test_safety_event_sobrevive_a_purga_do_workspace(session: Session, engine: Engine) -> None:
    workspace = _workspace(session)

    with engine.connect() as connection:
        connection.execute(
            text(
                "INSERT INTO safety_event"
                " (id, workspace_id, kind, decision, rule_id, subject, created_at)"
                " VALUES (:id, :ws, 'path_denied', 'deny', 'r', 's', '2026-08-31 00:00:00')"
            ),
            {"id": new_uuid(), "ws": workspace.id},
        )
        connection.commit()

    session.delete(workspace)
    session.commit()

    with engine.connect() as connection:
        remaining = connection.execute(text("SELECT COUNT(*) FROM safety_event")).scalar_one()
    assert remaining == 1, "SafetyEvent não pode ser cascateado"


# ------------------------------------------------------ integridade referencial


def test_foreign_key_e_realmente_aplicada(session: Session) -> None:
    """Sem `PRAGMA foreign_keys=ON`, as FKs seriam decorativas."""
    with pytest.raises(exc.IntegrityError):
        session.add(
            WorkspaceTask(
                workspace_id="inexistente",
                title="t",
                goal="g",
                status=enums.TaskStatus.DRAFT,
                risk=enums.RiskLevel.LOW,
                complexity=enums.ComplexityLevel.TRIVIAL,
                risk_source=enums.RiskSource.USER,
                execution_mode=enums.ExecutionMode.ORCHESTRATED,
            )
        )
        session.commit()
    session.rollback()


def test_purga_de_workspace_cascateia_tasks_e_runs(session: Session, engine: Engine) -> None:
    workspace = _workspace(session)
    task = _task(session, workspace)
    _run(session, task)

    session.delete(workspace)
    session.commit()

    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM workspace_task")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM run")).scalar_one() == 0


def test_timestamps_voltam_aware_em_utc(session: Session) -> None:
    workspace = _workspace(session)

    session.expire_all()
    reloaded = session.get(DevWorkspace, workspace.id)

    assert reloaded is not None
    assert reloaded.created_at.tzinfo is not None
    assert reloaded.created_at.utcoffset() == datetime.now(UTC).utcoffset()


def test_datetime_naive_e_recusado_na_conversao() -> None:
    """Um `datetime` sem fuso não pode ser gravado como se fosse UTC."""
    with pytest.raises(ValueError, match="naive"):
        UtcDateTime().process_bind_param(
            datetime(2026, 8, 31, 12, 0, 0),
            SQLiteDialect_pysqlite(),
        )


def test_datetime_naive_e_recusado_no_commit(session: Session) -> None:
    workspace = _workspace(session)

    with pytest.raises(exc.StatementError, match="naive"):
        workspace.updated_at = datetime(2026, 8, 31, 12, 0, 0)
        session.commit()
    session.rollback()
