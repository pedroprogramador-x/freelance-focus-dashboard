"""Ambiente do Alembic.

A URL vem de `app.config` — nunca do `.ini` — para que exista **um** ponto de leitura de
ambiente. Testes injetam um banco temporário por `FF_DATABASE_URL` ou pelo atributo
`sqlalchemy.url` da configuração passada em `config.attributes`.

`render_as_batch=True` porque o SQLite não suporta a maioria dos `ALTER TABLE`; sem isso,
qualquer migration futura que altere coluna falharia.

## `PRAGMA foreign_keys` é deste arquivo, não das migrations (E841-AUD-002)

Recriar uma tabela pai no SQLite (procedimento oficial: cria, copia, `DROP`, renomeia) exige
`foreign_keys` **desligado** — com ele ligado, o `DROP TABLE` faz `DELETE` implícito e dispara
`ON DELETE CASCADE` nas filhas. O pragma, porém, **não tem efeito dentro de uma transação**, e
quem fecha a transação de cada migration é o Alembic, *depois* que `upgrade()`/`downgrade()`
retornam. Uma migration não consegue, portanto, religar o pragma de forma eficaz: o `ON` dela
roda com a transação ainda aberta e é ignorado em silêncio.

Por isso o pragma é tratado aqui, em volta de `run_migrations()`:

* antes: lê o estado **pela conexão DBAPI** (sem *autobegin* do SQLAlchemy) e só desliga se as
  FKs estiverem ligadas, se **não** houver transação SQLite aberta e se a conexão **não** estiver
  numa transação externa do chamador (que fecharia depois de nós, impedindo a restauração);
* depois — no sucesso e na falha, quando o Alembic já fez commit ou rollback —: religa, e confere
  que religou. Se não der (transação ainda aberta, pragma não voltou), a conexão é **invalidada**
  e o erro sobe: nenhuma conexão sem FKs volta ao chamador ou ao pool.

Quando o pragma não pode ser desligado com segurança, ele fica como está, e a migration que
precisar dele desligado recusa **antes de qualquer DDL**.

### Falhas no próprio cuidado com o pragma (E841-AUD-002, reauditoria)

A responsabilidade pela restauração começa **antes** do `PRAGMA foreign_keys=OFF` e não depende
de nada terminar bem: desligar e conferir rodam dentro do mesmo `try` da migration, e qualquer
erro a partir daí passa por `restore()`. Em `restore()`, religar, reler e conferir o valor são
uma coisa só: qualquer exceção (do driver, de conexão fechada, de transação ainda aberta) ou
valor errado significa "estado seguro não comprovado", e a conexão é invalidada.

Invalidar não é presumido. `Connection.invalidate()` descarta o registro do pool e fecha a
conexão DBAPI, mas o pool do SQLAlchemy **só registra em log** um erro nesse fechamento. Por isso
o fechamento físico é conferido aqui (`sqlite3` recusa qualquer uso de conexão fechada); se ele
não aconteceu, a conexão é fechada diretamente. O erro que sobe é sempre
`ForeignKeysRestoreError`, com a causa original da migration (se houve), a falha da restauração,
as falhas da invalidação e se a conexão física ficou de fato fechada. Se nem o fechamento direto
funcionar, a mensagem diz que a conexão é **insegura** — nunca que está tudo bem.
"""

from __future__ import annotations

import sqlite3
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

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


class ForeignKeysRestoreError(RuntimeError):
    """`foreign_keys` não pôde ser religado de forma comprovada; diz o que foi feito da conexão."""

    def __init__(
        self,
        *,
        original: BaseException | None,
        restore_error: BaseException,
        invalidation_errors: tuple[BaseException, ...],
        physically_closed: bool,
    ) -> None:
        self.original = original
        self.restore_error = restore_error
        self.invalidation_errors = invalidation_errors
        self.physically_closed = physically_closed
        parts = [f"`PRAGMA foreign_keys` não foi religado de forma comprovada ({restore_error!r})"]
        if invalidation_errors:
            parts.append("a invalidação falhou: " + "; ".join(map(repr, invalidation_errors)))
        if physically_closed:
            parts.append("a conexão física foi fechada e não volta ao uso")
        else:
            parts.append(
                "CONEXÃO INSEGURA: a conexão física continua aberta com `foreign_keys` em estado "
                "não comprovado — descarte o engine/processo, não a reutilize"
            )
        if original is not None:
            parts.append(f"erro original da migração: {original!r}")
        super().__init__("; ".join(parts))


def _physically_closed(raw: sqlite3.Connection) -> bool:
    # `sqlite3` recusa qualquer uso de uma conexão fechada com `ProgrammingError`.
    try:
        _ = raw.total_changes
    except sqlite3.ProgrammingError:
        return True
    return False


class _SqliteForeignKeys:
    """Desliga `foreign_keys` durante as migrations quando é seguro, e garante a restauração.

    O construtor não executa nada: quem desliga é `disable_if_safe()`, chamado já dentro do
    `try` de `_run`, e a responsabilidade (`_responsible`) é assumida **antes** do `OFF`.
    """

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._raw: sqlite3.Connection | None = None
        self._responsible = False

    def disable_if_safe(self) -> None:
        connection = self._connection
        if connection.dialect.name != "sqlite":
            return
        raw: Any = connection.connection.dbapi_connection
        if not isinstance(raw, sqlite3.Connection):
            return
        self._raw = raw
        # Lido ANTES de qualquer instrução pelo SQLAlchemy: é o que o Alembic vai considerar
        # "transação externa" (e aí ele não comita — quem comita é o chamador, depois de nós).
        external = connection.in_transaction()
        enabled = self._pragma()
        if enabled and not external and not raw.in_transaction:
            # Daqui em diante o estado pode ter mudado, mesmo que o `OFF` ou a conferência falhem.
            self._responsible = True
            raw.execute("PRAGMA foreign_keys=OFF")
            if self._pragma():
                raise RuntimeError("`PRAGMA foreign_keys=OFF` não teve efeito; migração recusada")

    def _pragma(self) -> bool:
        assert self._raw is not None
        return bool(self._raw.execute("PRAGMA foreign_keys").fetchone()[0])

    def restore(self, original: BaseException | None = None) -> None:
        """Religa e confere; se não comprovar, invalida a conexão e levanta erro estruturado."""
        if not self._responsible:
            return
        raw = self._raw
        assert raw is not None
        try:
            if raw.in_transaction:
                raise RuntimeError("a transação da migração ainda está aberta")
            raw.execute("PRAGMA foreign_keys=ON")
            if not self._pragma():
                raise RuntimeError("`PRAGMA foreign_keys=ON` não teve efeito")
        except BaseException as failure:
            error = self._invalidate(raw, failure, original)
            if not isinstance(failure, Exception):
                raise  # KeyboardInterrupt & cia.: a conexão já foi tratada acima
            raise error from failure
        self._responsible = False

    def _invalidate(
        self, raw: sqlite3.Connection, failure: BaseException, original: BaseException | None
    ) -> ForeignKeysRestoreError:
        errors: list[BaseException] = []
        try:
            # Descarta o registro no pool e fecha a conexão DBAPI; uma reconexão usa outra.
            self._connection.invalidate(failure)
        except BaseException as error:
            errors.append(error)
        # O pool só registra em log um erro ao fechar a conexão DBAPI: confere e, se preciso, fecha.
        if not _physically_closed(raw):
            try:
                raw.close()
            except BaseException as error:
                errors.append(error)
        return ForeignKeysRestoreError(
            original=original,
            restore_error=failure,
            invalidation_errors=tuple(errors),
            physically_closed=_physically_closed(raw),
        )


def _run(connection: Connection) -> None:
    foreign_keys = _SqliteForeignKeys(connection)
    try:
        foreign_keys.disable_if_safe()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    except BaseException as error:
        # Depois do rollback do Alembic. Se a restauração falhar, o erro dela carrega este.
        foreign_keys.restore(original=error)
        raise
    # Depois do commit do Alembic: só aqui o pragma volta a ter efeito.
    foreign_keys.restore()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
