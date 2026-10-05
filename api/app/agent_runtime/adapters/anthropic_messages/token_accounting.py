"""Contabilidade de tokens do run (E8.2 — addendum de [05] §6). Puro: sem IO, sem SDK.

Duas camadas, de propósito separadas:

* **fórmula geral** (`turn_total_tokens`, `next_output_cap`): um turno consome
  `input + cache_creation + cache_read + output`; a próxima request só pode gerar
  `restante − previsto` de output, e nada se `previsto ≥ restante`. Vale para qualquer política;
* **normalização sob a política V1** (`normalize_usage_for_disabled_cache`): com prompt cache
  **desligado** (`prompt_cache_policy = "disabled"`, request verificada sem `cache_control`), um
  campo de cache ausente ou `None` significa "nenhuma atividade de cache" e vale 0; `0` explícito
  vale 0; **um número > 0 é violação** (houve cache apesar da política) e o run não continua.
  Em qualquer outra política, `None` **não** pode ser presumido 0 — a função recusa.
"""

from __future__ import annotations

from typing import Final

from app.agent_runtime.adapters.anthropic_messages.constants import PROMPT_CACHE_POLICY

CACHE_USAGE_FIELDS: Final = ("cache_creation_input_tokens", "cache_read_input_tokens")


class UnsupportedPromptCachePolicy(ValueError):
    """Política de cache que a V1 não sabe contabilizar: `None` não pode virar 0."""


class PromptCacheActivity(RuntimeError):
    """O `usage` reportou tokens de cache apesar da política `disabled`."""


def _count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def turn_total_tokens(
    *, input_tokens: int, cache_creation: int, cache_read: int, output_tokens: int
) -> int:
    """Fórmula geral de um turno: input total (com as duas categorias de cache) + output."""
    return input_tokens + cache_creation + cache_read + output_tokens


def next_output_cap(*, budget: int, consumed: int, predicted_input: int) -> int | None:
    """Teto de output da próxima request, ou `None` se ela não cabe (não chamar `create`)."""
    remaining = budget - consumed
    if predicted_input >= remaining:
        return None
    return remaining - predicted_input


def normalize_usage_for_disabled_cache(
    usage: object, *, prompt_cache_policy: str
) -> tuple[int, int] | None:
    """`(input total, output)` de um turno sob a política `disabled`.

    `None` = `usage` não confiável (input/output ausente, negativo, não inteiro; cache em tipo
    inválido). `PromptCacheActivity` = cache > 0 apesar da política. Política ≠ `disabled` →
    `UnsupportedPromptCachePolicy` (nunca presume 0)."""
    if prompt_cache_policy != PROMPT_CACHE_POLICY:
        raise UnsupportedPromptCachePolicy("prompt cache diferente de disabled não é suportado")
    base = _count(getattr(usage, "input_tokens", None))
    output = _count(getattr(usage, "output_tokens", None))
    if base is None or output is None:
        return None
    cache: dict[str, int] = {}
    for field in CACHE_USAGE_FIELDS:
        raw = getattr(usage, field, None)
        if raw is None:
            cache[field] = 0  # ausente/None sob `disabled` + request sem cache_control
            continue
        value = _count(raw)
        if value is None:
            return None
        if value > 0:
            raise PromptCacheActivity("tokens de cache reportados com a política disabled")
        cache[field] = 0
    total_input = turn_total_tokens(
        input_tokens=base,
        cache_creation=cache["cache_creation_input_tokens"],
        cache_read=cache["cache_read_input_tokens"],
        output_tokens=0,
    )
    return total_input, output
