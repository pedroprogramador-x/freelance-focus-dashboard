"""Gate da E2: banco vazio → migration → schema esperado.

A migration é a fonte do schema persistente. `create_all` não é usado em lugar nenhum,
nem nos testes — se fosse, a migration poderia divergir sem ninguém perceber.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, inspect

from app.db import enums
from app.db.base import Base
from app.db.models import ALL_MODELS
from tests.conftest import downgrade_migrations, run_migrations

EXPECTED_TABLES = {
    "dev_workspace",
    "context_registry_entry",
    "workspace_task",
    "context_manifest",
    "run",
    "audit_finding",
    "safety_event",
}

#: Mapeia coluna → enum Python, para conferir que o `CHECK` do banco aceita exatamente os
#: mesmos valores. A migration guarda os valores como literais (é registro histórico);
#: este teste é o que impede os dois lados de divergirem em silêncio.
ENUM_COLUMNS: dict[tuple[str, str], type[Enum]] = {
    ("dev_workspace", "type"): enums.WorkspaceType,
    ("dev_workspace", "status"): enums.WorkspaceStatus,
    ("context_registry_entry", "domain"): enums.ContextDomain,
    ("context_registry_entry", "state"): enums.ContextState,
    ("context_registry_entry", "stale_reason"): enums.StaleReason,
    ("context_registry_entry", "origin"): enums.ContextOrigin,
    ("workspace_task", "status"): enums.TaskStatus,
    ("workspace_task", "phase"): enums.TaskPhase,
    ("workspace_task", "risk"): enums.RiskLevel,
    ("workspace_task", "complexity"): enums.ComplexityLevel,
    ("workspace_task", "risk_source"): enums.RiskSource,
    ("workspace_task", "execution_mode"): enums.ExecutionMode,
    ("workspace_task", "failure_reason"): enums.FailureReason,
    ("run", "agent"): enums.RunAgent,
    ("run", "purpose"): enums.RunPurpose,
    ("run", "transport"): enums.RunTransport,
    ("run", "status"): enums.RunStatus,
    ("run", "token_source"): enums.TokenSource,
    ("run", "files_read_source"): enums.FilesReadSource,
    ("audit_finding", "purpose"): enums.FindingPurpose,
    ("audit_finding", "severity"): enums.FindingSeverity,
    ("audit_finding", "status"): enums.FindingStatus,
    ("safety_event", "kind"): enums.SafetyEventKind,
    ("safety_event", "decision"): enums.SafetyDecisionKind,
}


def _table_sql(engine: Engine, table: str) -> str:
    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": table},
        ).scalar_one()
    return str(row)


def _in_clause_value_sets(sql: str) -> list[frozenset[str]]:
    """Todos os conjuntos de literais que aparecem em cláusulas `IN (...)`."""
    sets: list[frozenset[str]] = []
    for group in re.findall(r"IN\s*\(([^)]*)\)", sql, flags=re.IGNORECASE):
        literals = re.findall(r"'([^']*)'", group)
        if literals:
            sets.append(frozenset(literals))
    return sets


def test_migration_cria_exatamente_as_sete_tabelas(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())

    assert tables >= EXPECTED_TABLES
    domain_tables = tables - {"alembic_version"}
    assert domain_tables == EXPECTED_TABLES, "tabela a mais ou a menos no schema"
    assert len(ALL_MODELS) == 7


def test_metric_sample_nao_existe(engine: Engine) -> None:
    """ADR-0007: a entidade genérica foi recusada e não pode voltar por descuido."""
    tables = {name.lower() for name in inspect(engine).get_table_names()}

    assert not any("metric" in name for name in tables)


def test_rendered_context_artifact_nao_e_tabela(engine: Engine) -> None:
    """[02] §5: é blob endereçado por conteúdo, referenciado pelo manifest."""
    tables = {name.lower() for name in inspect(engine).get_table_names()}
    assert "rendered_context_artifact" not in tables

    columns = {column["name"] for column in inspect(engine).get_columns("context_manifest")}
    assert {"rendered_context_hash", "rendered_context_ref"} <= columns


@pytest.mark.parametrize(("table", "column"), sorted(ENUM_COLUMNS))
def test_check_de_enum_aceita_exatamente_os_valores_do_python(
    engine: Engine, table: str, column: str
) -> None:
    expected = frozenset(member.value for member in ENUM_COLUMNS[(table, column)])
    value_sets = _in_clause_value_sets(_table_sql(engine, table))

    assert expected in value_sets, (
        f"{table}.{column}: CHECK do banco não corresponde ao enum Python.\n"
        f"esperado {sorted(expected)}\nencontrado {[sorted(s) for s in value_sets]}"
    )


def test_schema_migrado_bate_com_os_modelos(engine: Engine) -> None:
    """Guarda contra drift entre `models.py` e a migration."""
    inspector = inspect(engine)

    for table_name, table in Base.metadata.tables.items():
        assert inspector.has_table(table_name), f"tabela ausente no banco: {table_name}"

        db_columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        model_columns = {column.name: column for column in table.columns}

        assert set(db_columns) == set(model_columns), f"colunas divergentes em {table_name}"

        for name, model_column in model_columns.items():
            assert db_columns[name]["nullable"] == model_column.nullable, (
                f"nullability divergente em {table_name}.{name}"
            )


def test_indices_e_unicidade_esperados(engine: Engine) -> None:
    inspector = inspect(engine)

    run_indexes = {index["name"] for index in inspector.get_indexes("run")}
    assert {"ix_run_task_id", "ix_run_subject_run_id", "ix_run_subject_purpose"} <= run_indexes

    run_unique = {constraint["name"] for constraint in inspector.get_unique_constraints("run")}
    assert "uq_run_invocation_id" in run_unique

    workspace_unique = {
        constraint["name"] for constraint in inspector.get_unique_constraints("dev_workspace")
    }
    assert "uq_dev_workspace_local_path" in workspace_unique


def test_foreign_keys_declaradas_e_ativas(engine: Engine) -> None:
    inspector = inspect(engine)

    task_fks = {fk["referred_table"] for fk in inspector.get_foreign_keys("workspace_task")}
    assert {"dev_workspace", "context_manifest"} <= task_fks

    run_fks = inspector.get_foreign_keys("run")
    assert {fk["referred_table"] for fk in run_fks} >= {
        "workspace_task",
        "run",
        "context_manifest",
    }

    # `safety_event` não tem FK de propósito: sobrevive à purga do que o originou.
    assert inspector.get_foreign_keys("safety_event") == []

    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


def test_triggers_append_only_existem(engine: Engine) -> None:
    with engine.connect() as connection:
        triggers = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }

    assert {
        "safety_event_no_update",
        "safety_event_no_delete",
        "run_append_only_after_finish",
    } <= triggers


def test_upgrade_e_downgrade_sao_reproduziveis(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{(tmp_path / 'roundtrip.db').as_posix()}"

    run_migrations(url)
    engine = sa.create_engine(url)
    try:
        assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES
    finally:
        engine.dispose()

    downgrade_migrations(url)
    engine = sa.create_engine(url)
    try:
        remaining = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert remaining == set()
    finally:
        engine.dispose()
