"""E8.4.5 / I3 — a migration 0004: estrutura, sincronia, upgrade, downgrade e atomicidade.

Alembic **real** (`command.upgrade`/`downgrade`) sobre SQLite **real**. As falhas são injetadas por
evento de cursor, sem tocar no código da migration, e depois do erro o banco é comparado ao estado
inicial — schema (tabelas, índices e triggers, DDL literal), dados e versão.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, event
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateTable

from app.config import AppSettings
from app.db.base import Base
from app.db.models import ALL_MODELS, EXECUTION_MODELS
from app.db.session import create_engine as create_app_engine
from app.orchestrator import teardown_contract as tc
from tests.conftest import API_ROOT, downgrade_migrations, run_migrations
from tests.execution_reservation_support_e8_4_5 import Db, build_cycle, ts

PREV = "0003_execution_admission"
HEAD = "0004_execution_reservation"
FALHA = "falha injetada"

EXECUTION_TABLES = {"execution_reservation", "execution_launch", "execution_evidence"}
TRIGGERS_0004 = {
    "execution_reservation_insert_guard",
    "execution_reservation_update_guard",
    "execution_reservation_release_guard",
    "execution_reservation_no_delete",
    "execution_launch_insert_guard",
    "execution_launch_id_guard",
    "execution_launch_update_guard",
    "execution_launch_no_delete",
    "execution_evidence_insert_guard",
    "execution_evidence_order_guard",
    "execution_evidence_no_update",
    "execution_evidence_no_delete",
    "workspace_task_protect_unreleased_reservation",
    "dev_workspace_protect_unreleased_reservation",
    "run_protect_unreleased_reservation",
}


# ----------------------------------------------------------------------------- apoio


def _url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.as_posix()}"


def _config(url: str, connection: Connection | None = None) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.attributes["sqlalchemy.url"] = url
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def _legacy_engine(url: str, *, foreign_keys: bool) -> Engine:
    engine = sa.create_engine(url)
    if foreign_keys:

        @event.listens_for(engine, "connect")
        def _ligar(dbapi: Any, _record: Any) -> None:
            dbapi.execute("PRAGMA foreign_keys=ON")

    return engine


def _engine(modo: str, url: str, tmp_path: Path) -> Engine:
    if modo == "aplicacao":
        settings = AppSettings(data_dir=tmp_path, database_url=url, web_dist_dir=tmp_path / "x")
        return create_app_engine(settings)
    return _legacy_engine(url, foreign_keys=modo == "legado_fk_on")


MODOS = ("legado_fk_off", "legado_fk_on", "aplicacao")


@pytest.fixture(params=MODOS)
def modo(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def _snapshot(path: Path) -> dict[str, Any]:
    """Schema inteiro (tabelas, índices e triggers, DDL literal) + dados + versão."""
    connection = sqlite3.connect(path)
    try:
        schema = sorted(
            connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master"
                " WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()
        )
        tables = [r[1] for r in schema if r[0] == "table" and r[1] != "alembic_version"]
        data = {
            table: sorted(connection.execute(f"SELECT * FROM {table}").fetchall())  # noqa: S608
            for table in tables
        }
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        return {"schema": schema, "data": data, "version": version}
    finally:
        connection.close()


def _names(path: Path, kind: str) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            r[0]
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'",
                (kind,),
            )
        }
    finally:
        connection.close()


def _execute(engine: Engine, url: str, action: Callable[[Config], None]) -> BaseException | None:
    with engine.connect() as connection:
        try:
            action(_config(url, connection))
        except Exception as exc:  # o teste decide se era esperado
            return exc
    return None


def _fail_on(engine: Engine, prefix: str) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def _boom(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
        if statement.lstrip().upper().startswith(prefix.upper()):
            raise RuntimeError(f"{FALHA}: {prefix}")


def _db_at(tmp_path: Path, revision: str, name: str = "m.db") -> tuple[str, Path]:
    path = tmp_path / name
    url = _url(path)
    run_migrations(url, revision)
    return url, path


def _insert_task(db: Db, *, status: str) -> str:
    workspace, task = db.new_task(status="draft")
    if status == "executing":
        db.run(
            "UPDATE workspace_task SET status = 'executing', phase = 'implementing',"
            " started_at = ? WHERE id = ?",
            (ts(1), task),
        )
    return task


# ----------------------------------------------------------------------------- estrutura


def test_migration_cria_as_tres_tabelas_os_indices_e_os_triggers(engine: Engine) -> None:
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names()) - {"alembic_version"}
    assert tables >= EXECUTION_TABLES
    with engine.connect() as connection:
        triggers = {
            r[0]
            for r in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
    assert triggers >= TRIGGERS_0004
    indexes = {i["name"] for i in inspector.get_indexes("execution_reservation")}
    assert {
        "uq_execution_reservation_unreleased",
        "ix_execution_reservation_task_id",
        "ix_execution_reservation_workspace_id",
    } <= indexes
    assert any(
        i["name"] == "uq_execution_reservation_unreleased" and i["unique"]
        for i in inspector.get_indexes("execution_reservation")
    )


def test_o_slot_global_e_um_indice_parcial_sobre_uma_chave_constante(engine: Engine) -> None:
    with engine.connect() as connection:
        sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name = 'uq_execution_reservation_unreleased'"
        ).scalar_one()
    assert re.sub(r"\s+", " ", sql).endswith("(slot_key) WHERE state <> 'released'")


def test_so_o_dominio_de_execucao_entrou_alem_das_sete_entidades(engine: Engine) -> None:
    assert len(ALL_MODELS) == 7  # [02]: as sete entidades continuam sendo sete
    assert {m.__tablename__ for m in EXECUTION_MODELS} == EXECUTION_TABLES
    tables = set(sa.inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == {m.__tablename__ for m in ALL_MODELS} | EXECUTION_TABLES


# ----------------------------------------------------------------------------- vocabulário


def _constraint_sql(engine: Engine, table: str) -> dict[str, str]:
    """`nome → expressão` de cada `CHECK` nomeado da tabela, como está no banco."""
    with engine.connect() as connection:
        sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?", (table,)
        ).scalar_one()
    return _parse_checks(str(sql))


def _parse_checks(sql: str) -> dict[str, str]:
    checks: dict[str, str] = {}
    for match in re.finditer(r"CONSTRAINT (\w+) CHECK \(", sql):
        depth, index = 1, match.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(sql[index], 0)
            index += 1
        checks[match.group(1)] = re.sub(r"\s+", " ", sql[match.end() : index - 1]).strip()
    return checks


def _values(expression: str) -> set[str]:
    group = re.search(r"IN \(([^)]*)\)", expression)
    assert group, expression
    return set(re.findall(r"'([^']*)'", group.group(1)))


def _enum_values(enum: type[Enum]) -> set[str]:
    return {member.value for member in enum}


def test_vocabularios_do_check_sao_exatamente_os_do_contrato(engine: Engine) -> None:
    res = _constraint_sql(engine, "execution_reservation")
    assert _values(res["ck_execution_reservation_state_vocabulary"]) == _enum_values(
        tc.ReservationState
    )
    launch = _constraint_sql(engine, "execution_launch")
    assert _values(launch["ck_execution_launch_phase_tag_vocabulary"]) == _enum_values(tc.PhaseTag)
    assert _values(launch["ck_execution_launch_platform_vocabulary"]) == _enum_values(tc.Platform)
    assert _values(launch["ck_execution_launch_profile_vocabulary"]) == _enum_values(
        tc.ContainmentProfile
    )
    assert _values(launch["ck_execution_launch_phase_vocabulary"]) == _enum_values(tc.LaunchPhase)
    assert _values(launch["ck_execution_launch_end_basis_vocabulary"]) == _enum_values(tc.EndBasis)

    evidence = _constraint_sql(engine, "execution_evidence")
    assert _values(evidence["ck_execution_evidence_scope_vocabulary"]) == _enum_values(
        tc.EvidenceScope
    )
    assert _values(evidence["ck_execution_evidence_subject_vocabulary"]) == _enum_values(
        tc.BindingSubject
    )
    assert _values(evidence["ck_execution_evidence_basis_vocabulary"]) == _enum_values(
        tc.EvidenceBasis
    )


def test_bases_por_escopo_e_formas_terminais_do_check_batem_com_o_contrato(engine: Engine) -> None:
    evidence = _constraint_sql(engine, "execution_evidence")
    by_scope = dict(
        re.findall(
            r"scope = '(\w+)' AND basis IN \(([^)]*)\)",
            evidence["ck_execution_evidence_basis_by_scope"],
        )
    )
    assert set(by_scope) == _enum_values(tc.EvidenceScope)
    for scope, bases in tc.BASES_BY_SCOPE.items():
        assert set(re.findall(r"'([^']*)'", by_scope[scope.value])) == {b.value for b in bases}

    launch = _constraint_sql(engine, "execution_launch")["ck_execution_launch_end_basis_by_phase"]
    confirmed = re.search(r"phase <> 'ended_confirmed' OR end_basis IN \(([^)]*)\)", launch)
    unconfirmed = re.search(r"phase <> 'ended_unconfirmed' OR end_basis IN \(([^)]*)\)", launch)
    assert confirmed and unconfirmed
    assert set(re.findall(r"'([^']*)'", confirmed.group(1))) == {
        tc.EndBasis.REFUSED_BEFORE_CREATION.value,
        tc.EndBasis.TREE_CONFIRMED_DEAD.value,
    }
    assert set(re.findall(r"'([^']*)'", unconfirmed.group(1))) == {
        tc.EndBasis.TREE_UNCONFIRMED.value,
        tc.EndBasis.POPEN_RAISED.value,
        tc.EndBasis.LEDGER_WRITE_FAILED.value,
    }


def test_bases_observadas_ao_vivo_do_trigger_sao_as_do_contrato(engine: Engine) -> None:
    with engine.connect() as connection:
        sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name = 'execution_evidence_insert_guard'"
        ).scalar_one()
    live = re.search(r"NEW\.basis IN \(([^)]*)\) AND", sql)
    assert live
    assert set(re.findall(r"'([^']*)'", live.group(1))) == {b.value for b in tc.LIVE_OBSERVED_BASES}


def test_modelos_batem_com_a_migration_em_constraints_indices_e_fks(engine: Engine) -> None:
    inspector = sa.inspect(engine)
    dialect = sqlite_dialect.dialect()
    for model in EXECUTION_MODELS:
        table = Base.metadata.tables[model.__tablename__]
        ddl = str(CreateTable(table).compile(dialect=dialect))
        model_checks = _parse_checks(ddl)
        db_checks = _constraint_sql(engine, table.name)
        assert model_checks, table.name
        for name, expression in model_checks.items():
            assert db_checks.get(name) == expression, (table.name, name)

        db_indexes = {i["name"]: i for i in inspector.get_indexes(table.name)}
        for index in table.indexes:
            assert index.name in db_indexes, (table.name, index.name)
            assert bool(index.unique) == bool(db_indexes[str(index.name)]["unique"])
        for fk in inspector.get_foreign_keys(table.name):
            assert fk["options"].get("ondelete") == "RESTRICT"
        assert table.kwargs.get("sqlite_autoincrement") or table.name == "execution_reservation"


# ----------------------------------------------------------------------------- upgrade


def test_upgrade_em_banco_limpo_chega_ao_head(tmp_path: Path) -> None:
    url, path = _db_at(tmp_path, "head")
    snapshot = _snapshot(path)
    assert snapshot["version"] == HEAD
    assert _names(path, "table") >= EXECUTION_TABLES
    assert _names(path, "trigger") >= TRIGGERS_0004


def test_upgrade_de_0003_sem_execucao_aberta_preserva_os_dados(tmp_path: Path) -> None:
    url, path = _db_at(tmp_path, PREV)
    db = Db(path)
    draft = _insert_task(db, status="draft")
    finished = db.add_run(draft, "developer", status="ok")
    db.add_run(draft, "orchestrator", status="error")
    db.close()
    before = _snapshot(path)

    run_migrations(url)

    after = _snapshot(path)
    assert after["version"] == HEAD and before["version"] == PREV
    assert after["data"]["run"] == before["data"]["run"]
    assert after["data"]["workspace_task"] == before["data"]["workspace_task"]
    assert after["data"]["dev_workspace"] == before["data"]["dev_workspace"]
    assert after["data"]["execution_reservation"] == []
    db = Db(path)
    try:
        assert db.one("SELECT status FROM run WHERE id = ?", (finished,)) == ("ok",)
        assert db.scalar("PRAGMA integrity_check") == "ok"
        assert db.run("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db.close()


@pytest.mark.parametrize("seed", ["task_executing", "run_running", "ambos"])
def test_upgrade_recusado_com_execucao_aberta_nao_altera_nada(
    tmp_path: Path, modo: str, seed: str
) -> None:
    url, path = _db_at(tmp_path, PREV)
    db = Db(path)
    if seed in ("task_executing", "ambos"):
        _insert_task(db, status="executing")
    if seed in ("run_running", "ambos"):
        _workspace, task = db.new_task(status="draft")
        db.add_run(task, "orchestrator", status="running")
    db.close()
    before = _snapshot(path)

    engine = _engine(modo, url, tmp_path)
    try:
        error = _execute(engine, url, lambda cfg: command.upgrade(cfg, "head"))
    finally:
        engine.dispose()

    assert error is not None and "upgrade recusado" in str(error), error
    assert _snapshot(path) == before
    assert EXECUTION_TABLES.isdisjoint(_names(path, "table"))
    assert TRIGGERS_0004.isdisjoint(_names(path, "trigger"))


@pytest.mark.parametrize(
    "prefix",
    [
        "CREATE TABLE execution_launch",
        "CREATE UNIQUE INDEX uq_execution_reservation_unreleased",
        "CREATE TRIGGER execution_launch_update_guard",
        "UPDATE alembic_version",
    ],
)
def test_upgrade_interrompido_em_qualquer_ponto_volta_ao_estado_inicial(
    tmp_path: Path, modo: str, prefix: str
) -> None:
    url, path = _db_at(tmp_path, PREV)
    db = Db(path)
    _insert_task(db, status="draft")
    db.close()
    before = _snapshot(path)

    engine = _engine(modo, url, tmp_path)
    _fail_on(engine, prefix)
    try:
        error = _execute(engine, url, lambda cfg: command.upgrade(cfg, "head"))
    finally:
        engine.dispose()

    assert error is not None and FALHA in str(error), error
    assert _snapshot(path) == before, "DDL parcial sobreviveu à falha"
    assert EXECUTION_TABLES.isdisjoint(_names(path, "table"))


def test_upgrade_depois_da_falha_e_reaplicavel(tmp_path: Path) -> None:
    url, path = _db_at(tmp_path, PREV)
    engine = _legacy_engine(url, foreign_keys=False)
    _fail_on(engine, "CREATE TRIGGER execution_evidence_insert_guard")
    try:
        assert _execute(engine, url, lambda cfg: command.upgrade(cfg, "head")) is not None
    finally:
        engine.dispose()
    run_migrations(url)
    assert _snapshot(path)["version"] == HEAD


# ----------------------------------------------------------------------------- downgrade


def test_downgrade_deixa_o_schema_igual_ao_de_um_banco_que_parou_em_0003(tmp_path: Path) -> None:
    _url_a, path_a = _db_at(tmp_path, PREV, "a.db")
    url_b, path_b = _db_at(tmp_path, "head", "b.db")
    downgrade_migrations(url_b, PREV)
    assert _snapshot(path_b)["schema"] == _snapshot(path_a)["schema"]
    assert _snapshot(path_b)["version"] == PREV


def test_downgrade_total_remove_tudo(tmp_path: Path) -> None:
    url, path = _db_at(tmp_path, "head")
    downgrade_migrations(url)
    assert _names(path, "table") - {"alembic_version"} == set()
    assert _names(path, "trigger") == set()


def test_downgrade_recusado_com_reserva_nao_liberada_nao_altera_nada(
    tmp_path: Path, modo: str
) -> None:
    url, path = _db_at(tmp_path, "head")
    db = Db(path)
    db.reserve()
    db.close()
    before = _snapshot(path)

    engine = _engine(modo, url, tmp_path)
    try:
        error = _execute(engine, url, lambda cfg: command.downgrade(cfg, PREV))
    finally:
        engine.dispose()

    assert error is not None and "downgrade recusado" in str(error), error
    assert _snapshot(path) == before
    assert _names(path, "table") >= EXECUTION_TABLES


def test_downgrade_com_historico_liberado_e_recusado_e_preserva_a_auditoria(
    tmp_path: Path, modo: str
) -> None:
    """P2-I3-03: mesmo com TODAS as reservas `released`, o histórico não é descartado."""
    url, path = _db_at(tmp_path, "head")
    db = Db(path)
    rid = build_cycle(db).reservation_id
    assert db.release(rid) == 1
    db.close()
    before = _snapshot(path)
    assert before["data"]["execution_launch"] and before["data"]["execution_evidence"]

    engine = _engine(modo, url, tmp_path)
    try:
        error = _execute(engine, url, lambda cfg: command.downgrade(cfg, PREV))
    finally:
        engine.dispose()

    assert error is not None and "downgrade recusado" in str(error), error
    assert "não está vazio" in str(error) and "0 não liberada" in str(error)
    assert _snapshot(path) == before, "o downgrade recusado alterou o banco"
    assert _names(path, "table") >= EXECUTION_TABLES
    assert _names(path, "trigger") >= TRIGGERS_0004


def _only_table_with_rows(path: Path, table: str) -> None:
    """Deixa linhas SÓ em `table`, removendo as outras (triggers e FKs desligados, no arquivo)."""
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        for trigger in (
            "execution_reservation_no_delete",
            "execution_launch_no_delete",
            "execution_evidence_no_delete",
        ):
            connection.execute(f"DROP TRIGGER {trigger}")
        for other in EXECUTION_TABLES - {table}:
            connection.execute(f"DELETE FROM {other}")  # noqa: S608
    finally:
        connection.close()


@pytest.mark.parametrize("table", sorted(EXECUTION_TABLES))
def test_downgrade_recusa_com_linhas_em_qualquer_das_tres_tabelas(
    tmp_path: Path, table: str
) -> None:
    url, path = _db_at(tmp_path, "head")
    db = Db(path)
    rid = build_cycle(db).reservation_id
    assert db.release(rid) == 1
    db.close()
    _only_table_with_rows(path, table)
    before = _snapshot(path)
    assert before["data"][table] and all(
        not before["data"][other] for other in EXECUTION_TABLES - {table}
    )

    error = _execute(
        _legacy_engine(url, foreign_keys=False), url, lambda cfg: command.downgrade(cfg, PREV)
    )

    assert error is not None and "downgrade recusado" in str(error), error
    assert _snapshot(path) == before


def test_downgrade_legitimo_com_as_tres_tabelas_vazias(tmp_path: Path, modo: str) -> None:
    url, path = _db_at(tmp_path, "head")
    db = Db(path)
    _insert_task(db, status="draft")  # dados do domínio não impedem o downgrade
    db.close()
    domain = _snapshot(path)["data"]["workspace_task"]

    engine = _engine(modo, url, tmp_path)
    try:
        assert _execute(engine, url, lambda cfg: command.downgrade(cfg, PREV)) is None
    finally:
        engine.dispose()

    assert EXECUTION_TABLES.isdisjoint(_names(path, "table"))
    assert TRIGGERS_0004.isdisjoint(_names(path, "trigger"))
    snapshot = _snapshot(path)
    assert snapshot["version"] == PREV and snapshot["data"]["workspace_task"] == domain
    run_migrations(url)  # e o upgrade volta a funcionar


@pytest.mark.parametrize(
    "prefix",
    ["DROP TRIGGER IF EXISTS run_protect", "DROP TABLE execution_launch", "UPDATE alembic_version"],
)
def test_downgrade_interrompido_volta_ao_estado_inicial(
    tmp_path: Path, modo: str, prefix: str
) -> None:
    url, path = _db_at(tmp_path, "head")
    db = Db(path)
    _insert_task(db, status="draft")  # tabelas de execução vazias: o downgrade é permitido
    db.close()
    before = _snapshot(path)

    engine = _engine(modo, url, tmp_path)
    _fail_on(engine, prefix)
    try:
        error = _execute(engine, url, lambda cfg: command.downgrade(cfg, PREV))
    finally:
        engine.dispose()

    assert error is not None and FALHA in str(error), error
    assert _snapshot(path) == before
    assert _names(path, "trigger") >= TRIGGERS_0004


def test_downgrade_remove_os_triggers_das_tabelas_pais(tmp_path: Path) -> None:
    url, path = _db_at(tmp_path, "head")
    assert {
        "workspace_task_protect_unreleased_reservation",
        "dev_workspace_protect_unreleased_reservation",
        "run_protect_unreleased_reservation",
    } <= _names(path, "trigger")
    downgrade_migrations(url, PREV)
    names = _names(path, "trigger")
    assert not any(n.endswith("_protect_unreleased_reservation") for n in names)
    # os de 0003 continuam
    assert {"run_append_only_after_finish", "run_identity_immutable"} <= names


def test_nenhuma_migration_anterior_foi_alterada() -> None:
    versions = API_ROOT / "migrations" / "versions"
    assert sorted(p.name for p in versions.glob("0*.py")) == [
        "0001_initial_schema.py",
        "0002_workspace_test_config.py",
        "0003_execution_admission.py",
        "0004_execution_reservation.py",
    ]
