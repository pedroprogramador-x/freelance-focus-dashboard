"""Engine e sessão SQLite.

Dois pontos que o SQLite não faz sozinho e que a arquitetura exige:

* **`PRAGMA foreign_keys = ON`** por conexão. O SQLite ignora *foreign keys* por padrão;
  sem este pragma, todas as FKs declaradas em `models.py` seriam decorativas.
* **Transação explícita.** O driver `pysqlite` abre transação implícita de um jeito que
  quebra `SAVEPOINT` e DDL. Desligamos o gerenciamento dele e emitimos `BEGIN` nós mesmos.

Nada aqui cria arquivo no import: a engine só toca o disco quando alguém conecta.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings, get_settings


def _configure_connection(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        # WAL melhora leitura concorrente e é seguro para um backend local.
        cursor.execute("PRAGMA journal_mode = WAL")
        # Espera em vez de falhar de imediato quando outra conexão segura o lock.
        cursor.execute("PRAGMA busy_timeout = 5000")
    finally:
        cursor.close()


def _begin_explicitly(connection: Any) -> None:
    connection.exec_driver_sql("BEGIN")


def create_engine(settings: AppSettings | None = None, *, echo: bool = False) -> Engine:
    active = settings or get_settings()
    engine = sa.create_engine(
        active.sqlalchemy_url,
        echo=echo,
        future=True,
        # Desliga o BEGIN implícito do pysqlite; emitimos o nosso em `begin`.
        connect_args={"check_same_thread": False},
        isolation_level=None,
    )
    event.listen(engine, "connect", _configure_connection)
    event.listen(engine, "begin", _begin_explicitly)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Unidade de trabalho: commit no sucesso, rollback em qualquer exceção.

    Erro de invariante **não** falha em silêncio ([15] do prompt da E2 e a lição do
    `upsert` comercial): a exceção sobe depois do rollback.
    """
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
