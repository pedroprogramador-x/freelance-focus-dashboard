"""Validação estrutural compartilhada pelos DTOs de `tool_executor` e `agent_runtime` (E7.2).

Só forma e tipo: nenhuma decisão de segurança. Se um caminho é permitido, se um segredo
vaza, se um perfil é aceitável — nada disso é decidido aqui; pertence a `safety/` e ao
executor (E7.5). `bool` é recusado onde se espera `int` (`True` seria `1`).
"""

from __future__ import annotations

import re
from typing import Any, TypeVar

_T = TypeVar("_T")


class ContractViolation(ValueError):
    """Um DTO de contrato recebeu um valor que não tem a forma exigida."""


_SHA256 = re.compile(r"[0-9a-f]{64}")


def require_text(name: str, value: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ContractViolation(f"{name} precisa ser str, não {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise ContractViolation(f"{name} não pode ser vazio")
    if "\x00" in value:
        raise ContractViolation(f"{name} não pode conter NUL")
    return value


def require_optional_text(name: str, value: Any) -> str | None:
    return None if value is None else require_text(name, value)


def require_int(name: str, value: Any, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractViolation(f"{name} precisa ser int, não {type(value).__name__}")
    if value < minimum:
        raise ContractViolation(f"{name} precisa ser >= {minimum}")
    return int(value)


def require_optional_int(name: str, value: Any, *, minimum: int = 0) -> int | None:
    return None if value is None else require_int(name, value, minimum=minimum)


def require_text_tuple(name: str, value: Any, *, allow_empty: bool = True) -> tuple[str, ...]:
    """Exige uma `tuple` de `str`. `list` é recusada: o DTO é imutável e não copia por você."""
    if not isinstance(value, tuple):
        raise ContractViolation(f"{name} precisa ser tuple, não {type(value).__name__}")
    if not allow_empty and not value:
        raise ContractViolation(f"{name} não pode ser vazio")
    return tuple(require_text(f"{name}[{index}]", item) for index, item in enumerate(value))


def require_sha256(name: str, value: Any) -> str:
    text = require_text(name, value)
    if not _SHA256.fullmatch(text):
        raise ContractViolation(f"{name} precisa ser um sha256 hexadecimal minúsculo de 64 chars")
    return text


def require_instance(name: str, value: Any, expected: type[_T]) -> _T:
    if not isinstance(value, expected):
        raise ContractViolation(
            f"{name} precisa ser {expected.__name__}, não {type(value).__name__}"
        )
    return value
