"""Base declarativa, convenção de nomes e tipos de coluna compartilhados."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

#: Nomes determinísticos para constraints e índices.
#:
#: Sem isso, o SQLite gera nomes anônimos para os `CHECK` de enum, e nenhum teste
#: consegue afirmar "esta coluna aceita exatamente estes valores". Migrations também
#: ficam ilegíveis.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UtcDateTime(TypeDecorator[datetime]):
    """`datetime` sempre em UTC e sempre *aware*.

    O SQLite guarda datas como texto sem fuso. Sem este decorador, o que entra *aware*
    volta *naive*, e comparações silenciosamente erram por horas. Aqui a entrada é
    convertida para UTC antes de gravar e reidratada com `tzinfo` na leitura.
    """

    impl = sa.DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime naive não é aceito: informe o fuso (use UTC)")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


def new_uuid() -> str:
    """UUID v4 em texto — o mesmo formato de `crypto.randomUUID()` no frontend.

    [02] §0: padronizar agora evita ter de reconciliar formatos numa eventual unificação.
    """
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


def nullable_json() -> sa.JSON:
    """Coluna JSON opcional em que `None` vira **SQL NULL**, não o literal JSON `null`.

    O padrão de `sa.JSON` grava `None` como a string `'null'`. Com isso `coluna IS NULL`
    é falso e a distinção de [02] §10 — `[]` é *medido e nada foi lido*, ausência é
    `NULL` — desmoronaria justamente na coluna que ela existe para proteger
    (`Run.files_read`). O `CHECK` correspondente pegou isso na primeira execução da suíte.
    """
    return sa.JSON(none_as_null=True)


def enum_column(python_enum: type[Any], name: str) -> sa.Enum:
    """Enum persistido como `VARCHAR` + `CHECK`.

    `native_enum=False` porque o SQLite não tem tipo enum. `create_constraint=True` é
    obrigatório e não é o padrão: desde a SQLAlchemy 1.4 ele vem `False`, e sem ele o
    `CHECK` simplesmente não é emitido — a coluna aceitaria qualquer string.
    `validate_strings=True` faz um valor inválido falhar na escrita, não só na leitura.
    """
    return sa.Enum(
        python_enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
    )


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)
