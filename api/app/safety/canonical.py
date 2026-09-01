"""Serialização canônica e hashing.

Implementa `canonical_json` conforme
[02](../../../docs/architecture/02-data-model.md) §7. É a base de `policy_hash` agora e
será a de `content_hash` (E4) e `execution_fingerprint` (E6) — uma única definição, para
que dois módulos não inventem duas normalizações.

Puro: sem IO, sem relógio, sem ambiente.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

#: Tipos aceitos. `set`/`tuple` não entram de propósito: a ordem de um `set` não é
#: estável e a de uma `tuple` é ambígua para o leitor. Quem precisa de lista sem
#: semântica de ordem ordena **antes** de chamar — a regra está no docstring de
#: `canonical_json`.
JsonValue = None | bool | int | float | str | list[Any] | dict[str, Any]


class CanonicalizationError(ValueError):
    """Valor não representável de forma canônica e estável."""


def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise CanonicalizationError("NaN e infinito não são JSON válido")
        return value

    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)

    if isinstance(value, list):
        return [_normalize(item) for item in value]

    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"chave não-string em objeto canônico: {key!r}")
            normalized[unicodedata.normalize("NFC", key)] = _normalize(item)
        return normalized

    raise CanonicalizationError(f"tipo não canonizável: {type(value).__name__}")


def canonical_json(value: JsonValue) -> str:
    """Serializa de forma determinística.

    Garantias: UTF-8 sem BOM, chaves ordenadas por code point, sem espaço entre tokens,
    strings em NFC, `null` explícito (nunca chave omitida), sem `NaN`/`Infinity`.

    **Ordem de array é preservada.** Onde a ordem não for semântica, o chamador ordena
    antes — a serialização não pode adivinhar qual dos dois casos é o seu.

    O chamador também é responsável por não incluir valores transitórios (timestamp, PID,
    caminho que varia entre máquinas, contador): eles quebrariam a estabilidade do hash.
    """
    return json.dumps(
        _normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_sha256(value: JsonValue) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
