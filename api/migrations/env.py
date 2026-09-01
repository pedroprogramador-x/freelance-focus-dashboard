"""Ambiente do Alembic.

A URL vem de `app.config` — nunca do `.ini` — para que exista **um** ponto de leitura de
ambiente. Testes injetam um banco temporário por `FF_DATABASE_URL` ou pelo atributo
`sqlalchemy.url` da configuração passada em `config.attributes`.

`render_as_batch=True` porque o SQLite não suporta a maioria dos `ALTER TABLE`; sem isso,
qualquer migration futura que altere coluna falharia.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db.base import Base
from app.db import models  # noqa: F401  (registra as tabelas na MetaData)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    injected = config.attributes.get("sqlalchemy.url")
    if injected:
        return str(injected)
    return get_settings().sqlalchemy_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()

    connectable = config.attributes.get("connection", None)
    if connectable is None:
        engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
        with engine.connect() as connection:
            _run(connection)
        engine.dispose()
    else:
        _run(connectable)


def _run(connection: object) -> None:
    context.configure(
        connection=connection,  # type: ignore[arg-type]
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
