"""E8.4.1 — `Run` aberto, métricas não medidas e a migration 0003.

Tudo aqui é verificado pelo **banco** (CHECK, índices únicos parciais e *triggers*), não por
disciplina de código: um `UPDATE` direto também tem de ser recusado. A segunda metade cobre a
migration com dados anteriores — upgrade, downgrade e os casos em que ela falha fechada.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, exc, inspect, text
from sqlalchemy.orm import Session

from app.db import enums
from app.db.base import new_uuid
from app.db.models import DevWorkspace, Run, WorkspaceTask
from tests.conftest import downgrade_migrations, run_migrations
from tests.test_db_constraints import _run, _task, _workspace

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def _open_run(session: Session, task: WorkspaceTask, **overrides: Any) -> Run:
    values: dict[str, Any] = {
        "status": enums.RunStatus.RUNNING,
        "agent": enums.RunAgent.ORCHESTRATOR,
        "attempt_index": 0,
    }
    values.update(overrides)
    return _run(session, task, **values)


# ==================================================================== running persistido


def test_running_e_um_status_valido_e_persiste(session: Session, engine: Engine) -> None:
    task = _task(session, _workspace(session))
    run = _open_run(session, task)

    session.expire_all()
    stored = session.get(Run, run.id)
    assert stored is not None
    assert stored.status is enums.RunStatus.RUNNING
    assert stored.finished_at is None and stored.duration_ms is None
    # nenhuma métrica do Git Runtime afirmada: NULL, não [] nem 0
    assert stored.files_changed is None
    assert stored.diff_added is None and stored.diff_removed is None

    with engine.connect() as connection:
        raw = connection.execute(
            text("SELECT status, files_changed, diff_added FROM run WHERE id = :id"),
            {"id": run.id},
        ).one()
    assert tuple(raw) == ("running", None, None), "NULL do SQL, não o literal JSON `null`"


def test_o_enum_python_e_o_check_do_banco_conhecem_running() -> None:
    assert enums.RunStatus.RUNNING.value == "running"
    assert {member.value for member in enums.RunStatus} == {
        "running",
        "ok",
        "error",
        "timeout",
        "cancelled",
        "blocked",
        "interrupted",
    }


# ================================================================= invariantes de CHECK


def test_run_aberto_nao_tem_fim_nem_duracao(session: Session) -> None:
    task = _task(session, _workspace(session))
    for campos in (
        {"finished_at": NOW},
        {"duration_ms": 5},
        {"finished_at": NOW, "duration_ms": 5},
    ):
        with pytest.raises(exc.IntegrityError, match="open_run_has_no_closure"):
            _open_run(session, task, **campos)
        session.rollback()


def test_run_final_exige_finished_at(session: Session) -> None:
    task = _task(session, _workspace(session))
    for status in enums.RunStatus:
        if status is enums.RunStatus.RUNNING:
            continue
        with pytest.raises(exc.IntegrityError, match="final_run_is_finished"):
            _run(session, task, status=status, finished_at=None)
        session.rollback()
        ok = _run(session, task, status=status, finished_at=NOW, duration_ms=3)
        assert ok.status is status


def test_run_aberto_nao_afirma_metrica_do_git_runtime(session: Session) -> None:
    task = _task(session, _workspace(session))
    for campos in (
        {"files_changed": [], "diff_added": 0, "diff_removed": 0},
        {"files_changed": ["a.py"], "diff_added": 1, "diff_removed": 0},
    ):
        with pytest.raises(exc.IntegrityError, match="open_run_has_no_git_metrics"):
            _open_run(session, task, **campos)
        session.rollback()


def test_metricas_do_git_runtime_sao_tudo_ou_nada(session: Session) -> None:
    task = _task(session, _workspace(session))
    parciais: list[dict[str, Any]] = [
        {"files_changed": ["a.py"]},
        {"files_changed": [], "diff_added": 0},
        {"diff_added": 1, "diff_removed": 1},
        {"files_changed": [], "diff_removed": 0},
    ]
    for campos in parciais:
        with pytest.raises(exc.IntegrityError, match="git_metrics_all_or_none"):
            _run(session, task, **campos)
        session.rollback()

    medido_vazio = _run(session, task, files_changed=[], diff_added=0, diff_removed=0)
    nao_medido = _run(session, task)
    session.expire_all()
    # `[]`/`0` = medido, nada mudou; `NULL` = não medido. Os dois coexistem e são distintos.
    assert session.get(Run, medido_vazio.id).files_changed == []  # type: ignore[union-attr]
    assert session.get(Run, medido_vazio.id).diff_added == 0  # type: ignore[union-attr]
    assert session.get(Run, nao_medido.id).files_changed is None  # type: ignore[union-attr]
    assert session.get(Run, nao_medido.id).diff_added is None  # type: ignore[union-attr]


def test_diff_negativo_continua_recusado(session: Session) -> None:
    task = _task(session, _workspace(session))
    with pytest.raises(exc.IntegrityError, match="diff_added_non_negative"):
        _run(session, task, files_changed=[], diff_added=-1, diff_removed=0)
    session.rollback()
    with pytest.raises(exc.IntegrityError, match="diff_removed_non_negative"):
        _run(session, task, files_changed=[], diff_added=0, diff_removed=-1)
    session.rollback()


# =========================================================== finalização e imutabilidade


def _update(engine: Engine, sql: str, **params: Any) -> None:
    with engine.begin() as connection:
        connection.execute(text(sql), params)


def test_run_final_e_imutavel_por_update_direto(session: Session, engine: Engine) -> None:
    task = _task(session, _workspace(session))
    run = _run(session, task, status=enums.RunStatus.OK, finished_at=NOW, duration_ms=1)

    for sql in (
        "UPDATE run SET summary = 'reescrito' WHERE id = :id",
        "UPDATE run SET status = 'error' WHERE id = :id",
        "UPDATE run SET status = 'running', finished_at = NULL, duration_ms = NULL WHERE id = :id",
        "UPDATE run SET files_changed = '[]', diff_added = 0, diff_removed = 0 WHERE id = :id",
    ):
        with pytest.raises(exc.DatabaseError, match="append-only"):
            _update(engine, sql, id=run.id)


def test_run_com_status_final_sem_finished_at_legado_tambem_seria_imutavel(
    session: Session, engine: Engine
) -> None:
    """O gatilho olha o status, não só `finished_at`: um Run final nunca é reaberto."""
    task = _task(session, _workspace(session))
    run = _open_run(session, task)
    _update(
        engine,
        "UPDATE run SET status = 'ok', finished_at = :f, duration_ms = 2 WHERE id = :id",
        f=NOW.replace(tzinfo=None),
        id=run.id,
    )

    with pytest.raises(exc.DatabaseError, match="append-only"):
        _update(
            engine,
            "UPDATE run SET status = 'running', finished_at = NULL WHERE id = :id",
            id=run.id,
        )


def test_trigger_trata_status_final_como_imutavel_mesmo_sem_finished_at(
    session: Session, engine: Engine
) -> None:
    """Defesa em profundidade: o CHECK `final_run_is_finished` impede o estado, mas se uma linha
    final sem `finished_at` existir (CHECK ignorado, dado herdado), o *trigger* olha o status e
    ela continua imutável — não vira um Run reaberto por `UPDATE`."""
    task = _task(session, _workspace(session))
    run_id = new_uuid()
    database = engine.url.database
    assert database is not None
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "INSERT INTO run (id, invocation_id, task_id, agent, purpose, attempt_index,"
            " fix_round, provider, provider_adapter, transport, status, started_at,"
            " token_source, files_read_source) VALUES (?, 'sem-fim', ?, 'developer',"
            " 'execution', 0, 0, 'p', 'a', 'api', 'ok', '2026-02-01', 'unavailable',"
            " 'unavailable')",
            (run_id, task.id),
        )
        connection.commit()
        connection.execute("PRAGMA ignore_check_constraints = OFF")
        for sql in (
            "UPDATE run SET summary = 'reescrito' WHERE id = ?",
            "UPDATE run SET status = 'running' WHERE id = ?",
        ):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                connection.execute(sql, (run_id,))
            connection.rollback()
    finally:
        connection.close()


def test_fechar_um_run_exige_finished_at_e_duracao(session: Session, engine: Engine) -> None:
    task = _task(session, _workspace(session))
    run = _open_run(session, task)

    # só o status: recusado (CHECK `final_run_is_finished`)
    with pytest.raises(exc.IntegrityError):
        _update(engine, "UPDATE run SET status = 'ok' WHERE id = :id", id=run.id)
    # com `finished_at` mas sem duração: recusado pelo trigger de fechamento
    with pytest.raises(exc.DatabaseError, match="fechar exige"):
        _update(
            engine,
            "UPDATE run SET status = 'ok', finished_at = :f WHERE id = :id",
            f=NOW.replace(tzinfo=None),
            id=run.id,
        )
    # completo: fecha
    _update(
        engine,
        "UPDATE run SET status = 'ok', finished_at = :f, duration_ms = 9 WHERE id = :id",
        f=NOW.replace(tzinfo=None),
        id=run.id,
    )
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT status, duration_ms FROM run WHERE id = :id"), {"id": run.id}
        ).one() == ("ok", 9)


def test_run_aberto_pode_receber_progresso_mas_nao_mudar_de_identidade(
    session: Session, engine: Engine
) -> None:
    task = _task(session, _workspace(session))
    run = _open_run(session, task)

    _update(
        engine,
        "UPDATE run SET summary = 'progresso', worktree_path = '/w' WHERE id = :id",
        id=run.id,
    )  # running → running

    for campo, valor in (
        ("invocation_id", "outra-chave"),
        ("agent", "developer"),
        ("purpose", "workflow_audit"),
        ("attempt_index", 7),
        ("fix_round", 3),
        ("started_at", "2020-01-01 00:00:00.000000"),
        ("task_id", new_uuid()),
        ("id", new_uuid()),
    ):
        with pytest.raises(exc.DatabaseError):
            _update(engine, f"UPDATE run SET {campo} = :v WHERE id = :id", v=valor, id=run.id)  # noqa: S608


# =========================================================== índices únicos parciais


def test_no_maximo_um_run_de_controle_aberto_por_task(session: Session) -> None:
    workspace = _workspace(session)
    task_a = _task(session, workspace)
    task_b = _task(session, workspace)

    _open_run(session, task_a)
    with pytest.raises(exc.IntegrityError, match="UNIQUE"):
        _open_run(session, task_a)
    session.rollback()

    # outra task pode ter o seu; Runs de componente abertos não entram na regra
    _open_run(session, task_b)
    _open_run(session, task_a, agent=enums.RunAgent.DEVELOPER)
    _open_run(session, task_a, agent=enums.RunAgent.TEST_RUNNER)


def test_um_novo_run_de_controle_so_depois_de_fechar_o_anterior(
    session: Session, engine: Engine
) -> None:
    task = _task(session, _workspace(session))
    primeiro = _open_run(session, task)
    _update(
        engine,
        "UPDATE run SET status = 'error', finished_at = :f, duration_ms = 1 WHERE id = :id",
        f=NOW.replace(tzinfo=None),
        id=primeiro.id,
    )

    segundo = _open_run(session, task, attempt_index=1)
    assert segundo.status is enums.RunStatus.RUNNING


def _executing(session: Session, workspace: DevWorkspace) -> WorkspaceTask:
    return _task(
        session,
        workspace,
        status=enums.TaskStatus.EXECUTING,
        phase=enums.TaskPhase.IMPLEMENTING,
        started_at=NOW,
    )


def test_o_banco_recusa_duas_tasks_executing(session: Session, engine: Engine) -> None:
    workspace = _workspace(session)
    primeira_id = _executing(session, workspace).id  # lido já: o rollback expira o objeto

    with pytest.raises(exc.IntegrityError, match="UNIQUE"):
        _executing(session, workspace)
    session.rollback()

    # o índice de admissão só olha o status: sair de `executing` reabre a admissão (recursos
    # externos ainda em execução são obrigação da E8.4.5, não deste índice)
    _update(
        engine,
        "UPDATE workspace_task SET status = 'cancelled', phase = NULL, "
        "started_at = :s, finished_at = :s WHERE id = :id",
        s=NOW.replace(tzinfo=None),
        id=primeira_id,
    )
    assert _executing(session, workspace).status is enums.TaskStatus.EXECUTING


def test_o_indice_do_slot_nao_atinge_outros_estados(session: Session) -> None:
    workspace = _workspace(session)
    _task(session, workspace, status=enums.TaskStatus.APPROVED)
    _task(session, workspace, status=enums.TaskStatus.APPROVED)
    _task(session, workspace, status=enums.TaskStatus.DRAFT)
    _executing(session, workspace)


def test_schema_migrado_tem_os_indices_e_triggers_da_e8_4_1(engine: Engine) -> None:
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT type, name, sql FROM sqlite_master WHERE type IN ('index', 'trigger')")
        ).fetchall()
    by_name = {name: (kind, sql) for kind, name, sql in rows}

    assert (
        "UNIQUE INDEX uq_workspace_task_single_executing"
        in by_name["uq_workspace_task_single_executing"][1]
    )
    assert "WHERE status = 'executing'" in by_name["uq_workspace_task_single_executing"][1]
    assert "uq_run_open_control_per_task" in by_name
    for trigger in (
        "run_append_only_after_finish",
        "run_identity_immutable",
        "run_finalization_requires_closure",
        "safety_event_no_update",
        "safety_event_no_delete",
    ):
        assert by_name[trigger][0] == "trigger", trigger
    assert {c["name"] for c in inspect(engine).get_columns("run")} >= {
        "files_changed",
        "diff_added",
        "diff_removed",
    }
    nullable = {c["name"]: c["nullable"] for c in inspect(engine).get_columns("run")}
    assert nullable["files_changed"] and nullable["diff_added"] and nullable["diff_removed"]


# ============================================================ migration com dados anteriores

HEAD_BEFORE = "0002_workspace_test_config"


def _legacy_db(tmp_path: Path) -> tuple[str, Path]:
    path = tmp_path / "legacy.db"
    url = f"sqlite+pysqlite:///{path.as_posix()}"
    run_migrations(url, HEAD_BEFORE)
    return url, path


def _seed_legacy(path: Path, *, extra_runs: tuple[str, ...] = ()) -> dict[str, str]:
    """Dados no schema ANTERIOR, por SQL cru: workspace, task, dois Runs finais e um finding."""
    ids = {
        "workspace": new_uuid(),
        "task": new_uuid(),
        "run_ok": new_uuid(),
        "run_error": new_uuid(),
        "finding": new_uuid(),
    }
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO dev_workspace (id, name, type, local_path, status, created_at, updated_at)"
            " VALUES (?, 'ws', 'personal', '/tmp/legacy', 'active', '2026-01-01', '2026-01-01')",
            (ids["workspace"],),
        )
        connection.execute(
            "INSERT INTO workspace_task (id, workspace_id, title, goal, status, version, risk,"
            " complexity, risk_source, execution_mode, agents, attempts, fix_rounds,"
            " cancel_requested, created_at) VALUES (?, ?, 't', 'g', 'draft', 1, 'low', 'low',"
            " 'hard_rule', 'orchestrated', '[]', 0, 0, 0, '2026-01-01')",
            (ids["task"], ids["workspace"]),
        )

        def insert_run(
            run_id: str,
            key: str,
            status: str,
            files_read_source: str = "reported",
            finished: str | None = "2026-01-02 00:00:00.000000",
        ) -> None:
            connection.execute(
                "INSERT INTO run (id, invocation_id, task_id, agent, purpose, attempt_index,"
                " fix_round, provider, provider_adapter, transport, status, started_at,"
                " finished_at, duration_ms, token_source, files_read, files_read_source,"
                " files_changed, diff_added, diff_removed) VALUES (?, ?, ?, 'developer',"
                " 'execution', 0, 0, 'p', 'a', 'api', ?, '2026-01-01 00:00:00.000000', ?, 10,"
                " 'reported', '[]', ?, '[\"a.py\"]', 3, 1)",
                (run_id, key, ids["task"], status, finished, files_read_source),
            )

        insert_run(ids["run_ok"], "legacy-ok", "ok")
        insert_run(ids["run_error"], "legacy-error", "error")
        for index, status in enumerate(extra_runs):
            insert_run(new_uuid(), f"legacy-extra-{index}", status, finished=None)
        connection.execute(
            "INSERT INTO audit_finding (id, run_id, purpose, severity, category, summary, status,"
            " created_at) VALUES (?, ?, 'workflow_audit', 'low', 'x', 'achado', 'open',"
            " '2026-01-02')",
            (ids["finding"], ids["run_ok"]),
        )
        connection.commit()
    finally:
        connection.close()
    return ids


def _versao(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        return str(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0])
    finally:
        connection.close()


def test_upgrade_preserva_runs_finalizados_e_dependentes(tmp_path: Path) -> None:
    url, path = _legacy_db(tmp_path)
    ids = _seed_legacy(path)

    run_migrations(url)

    connection = sqlite3.connect(path)
    try:
        rows = {
            row[0]: row
            for row in connection.execute(
                "SELECT id, status, finished_at, duration_ms, files_changed, diff_added,"
                " diff_removed, files_read, invocation_id FROM run"
            )
        }
        assert set(rows) == {ids["run_ok"], ids["run_error"]}
        assert rows[ids["run_ok"]] == (
            ids["run_ok"],
            "ok",
            "2026-01-02 00:00:00.000000",
            10,
            '["a.py"]',
            3,
            1,
            "[]",
            "legacy-ok",
        )
        assert rows[ids["run_error"]][1] == "error"
        # o CASCADE não foi disparado: o finding sobreviveu à recriação de `run`
        assert connection.execute("SELECT COUNT(*) FROM audit_finding").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name = 'run_new'"
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()
    assert _versao(path) == "0003_execution_admission"


def test_runs_antigos_continuam_imutaveis_e_o_schema_novo_aceita_running(tmp_path: Path) -> None:
    url, path = _legacy_db(tmp_path)
    ids = _seed_legacy(path)
    run_migrations(url)

    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute("UPDATE run SET summary = 'x' WHERE id = ?", (ids["run_ok"],))
        connection.rollback()
        connection.execute(
            "INSERT INTO run (id, invocation_id, task_id, agent, purpose, attempt_index,"
            " fix_round, provider, provider_adapter, transport, status, started_at,"
            " token_source, files_read_source) VALUES (?, 'novo', ?, 'orchestrator',"
            " 'execution', 0, 0, 'p', 'a', 'process', 'running', '2026-02-01', 'unavailable',"
            " 'unavailable')",
            (new_uuid(), ids["task"]),
        )
        connection.commit()
        assert connection.execute(
            "SELECT status, files_changed FROM run WHERE invocation_id = 'novo'"
        ).fetchone() == ("running", None)
    finally:
        connection.close()


def test_upgrade_falha_fechado_com_run_sem_finished_at_e_nao_altera_o_banco(
    tmp_path: Path,
) -> None:
    url, path = _legacy_db(tmp_path)
    _seed_legacy(path, extra_runs=("ok",))

    with pytest.raises(RuntimeError, match="sem `finished_at`"):
        run_migrations(url)

    assert _versao(path) == HEAD_BEFORE
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 3
        sql = connection.execute("SELECT sql FROM sqlite_master WHERE name = 'run'").fetchone()[0]
        assert "'running'" not in sql
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'uq_%single%'"
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()


def test_upgrade_falha_fechado_com_mais_de_uma_task_executing(tmp_path: Path) -> None:
    url, path = _legacy_db(tmp_path)
    ids = _seed_legacy(path)
    connection = sqlite3.connect(path)
    try:
        for status in ("executing", "executing"):
            connection.execute(
                "INSERT INTO workspace_task (id, workspace_id, title, goal, status, phase,"
                " version, risk, complexity, risk_source, execution_mode, agents, attempts,"
                " fix_rounds, cancel_requested, created_at) VALUES (?, ?, 't', 'g', ?,"
                " 'implementing', 1, 'low', 'low', 'hard_rule', 'orchestrated', '[]', 0, 0, 0,"
                " '2026-01-01')",
                (new_uuid(), ids["workspace"], status),
            )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="executing"):
        run_migrations(url)

    assert _versao(path) == HEAD_BEFORE


def test_downgrade_preserva_dados_medidos_e_restaura_o_schema_anterior(tmp_path: Path) -> None:
    url, path = _legacy_db(tmp_path)
    ids = _seed_legacy(path)
    run_migrations(url)

    downgrade_migrations(url, HEAD_BEFORE)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT status, files_changed, diff_added FROM run WHERE id = ?", (ids["run_ok"],)
        ).fetchone() == ("ok", '["a.py"]', 3)
        sql = connection.execute("SELECT sql FROM sqlite_master WHERE name = 'run'").fetchone()[0]
        assert "files_changed JSON NOT NULL" in sql and "'running'" not in sql
        names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
        assert "uq_workspace_task_single_executing" not in names
        assert "uq_run_open_control_per_task" not in names
        assert "run_identity_immutable" not in names
        assert "run_append_only_after_finish" in names
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM audit_finding").fetchone()[0] == 1
    finally:
        connection.close()
    assert _versao(path) == HEAD_BEFORE


@pytest.mark.parametrize("motivo", ["running", "nao_medido"])
def test_downgrade_recusa_perder_a_distincao_entre_medido_e_nao_medido(
    tmp_path: Path, motivo: str
) -> None:
    url, path = _legacy_db(tmp_path)
    ids = _seed_legacy(path)
    run_migrations(url)
    connection = sqlite3.connect(path)
    try:
        if motivo == "running":
            connection.execute(
                "INSERT INTO run (id, invocation_id, task_id, agent, purpose, attempt_index,"
                " fix_round, provider, provider_adapter, transport, status, started_at,"
                " token_source, files_read_source) VALUES (?, 'aberto', ?, 'orchestrator',"
                " 'execution', 0, 0, 'p', 'a', 'process', 'running', '2026-02-01',"
                " 'unavailable', 'unavailable')",
                (new_uuid(), ids["task"]),
            )
        else:
            connection.execute(
                "INSERT INTO run (id, invocation_id, task_id, agent, purpose, attempt_index,"
                " fix_round, provider, provider_adapter, transport, status, started_at,"
                " finished_at, duration_ms, token_source, files_read_source) VALUES (?,"
                " 'sem-medicao', ?, 'developer', 'execution', 0, 0, 'p', 'a', 'api', 'error',"
                " '2026-02-01', '2026-02-02', 1, 'unavailable', 'unavailable')",
                (new_uuid(), ids["task"]),
            )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="downgrade recusado"):
        downgrade_migrations(url, HEAD_BEFORE)

    assert _versao(path) == "0003_execution_admission"
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 3
    finally:
        connection.close()
