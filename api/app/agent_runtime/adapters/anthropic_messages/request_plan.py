"""Plano de request à Messages API e **bundle de runtime** verificável (E8.2).

## Verificar exatamente o que executa (AUD-004)

O request não é montado por método de objeto: um método pode ser sobrescrito por subclasse e
o verificador acabaria olhando um "gêmeo semântico" enquanto o provider envia outra coisa. Por
isso:

* `AnthropicMessagesRequestPlan` e `AnthropicRuntimeBundle` são `frozen`/`slots`, **finais em
  runtime** (`__init_subclass__` recusa subclasse) e exigidos por **tipo exato** em todo ponto
  de uso (`require_exact_plan`/`require_exact_bundle`) — `isinstance` não basta;
* a parte estática do request vive numa `str` de JSON canônico (`params_json`): imutável de
  fato, ao contrário de `dict`/`list` dentro de dataclass `frozen`;
* **uma** função de módulo, `build_input_params`, materializa um payload **novo** a partir
  dessa `str` + as `messages` do turno; `build_count_tokens_kwargs` e `build_create_kwargs`
  só acrescentam o que é dinâmico (`timeout`; e `max_tokens` no `create`). Contar uma coisa e
  executar outra é impossível: os dois saem da mesma função, sobre o mesmo plano;
* o verificador e o provider usam essas mesmas funções. Nenhum método virtual participa.

## Estático × dinâmico

Estático e aprovado: `model`, `system`, `tools`, `tool_choice`, `thinking`, `output_config`,
endpoint, `max_retries`, `trust_env`, identidade do adaptador e versão do SDK (lida da metadata
da distribuição instalada). Dinâmico: `messages`, `max_tokens` daquela request e `timeout`
restante. Conteúdo do run (contexto, resposta do modelo) não alcança a parte estática.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from importlib.metadata import version as distribution_version
from typing import Any, final

from app.agent_runtime.adapters.anthropic_messages.config import (
    SYSTEM_PROMPT,
    PreparedAnthropicMessagesConfig,
)
from app.agent_runtime.adapters.anthropic_messages.constants import ANTHROPIC_SDK_DISTRIBUTION
from app.agent_runtime.adapters.anthropic_messages.dispatcher import (
    CANONICAL_DISPATCHER,
    CanonicalToolDispatcher,
)
from app.agent_runtime.adapters.anthropic_messages.tool_surface import tool_definitions
from app.safety.canonical import canonical_json, canonical_sha256

#: As únicas chaves estáticas permitidas no request. Qualquer outra (`container`,
#: `stop_sequences`, `metadata`, `extra_body`, `service_tier`…) é recusada pelo verificador.
STATIC_PARAM_KEYS: frozenset[str] = frozenset(
    {"model", "system", "tools", "tool_choice", "thinking", "output_config"}
)


class RequestBindingError(TypeError):
    """Objeto de request/bundle que não é exatamente o tipo canônico: defeito, nunca política."""


class CacheControlRejected(RequestBindingError):
    """Chave estrutural `cache_control` num request: proibida pela política `disabled`."""


#: A chave **estrutural** que liga prompt caching na Messages API, em qualquer nível.
CACHE_CONTROL_KEY = "cache_control"


def has_cache_control_key(value: object) -> bool:
    """Procura a **chave** `cache_control` em `dict`/`list`/`tuple` aninhados.

    Só chave estrutural conta: um texto que contenha a palavra "cache_control" (prompt, conteúdo
    de arquivo, resultado de ferramenta) não é recusado. Puro, sem IO."""
    stack: list[object] = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if CACHE_CONTROL_KEY in item:
                return True
            stack.extend(item.values())
        elif isinstance(item, list | tuple):
            stack.extend(item)
    return False


def installed_sdk_version() -> str:
    """Versão do SDK pela **metadata da distribuição instalada** (não por constante)."""
    return distribution_version(ANTHROPIC_SDK_DISTRIBUTION)


@final
@dataclass(frozen=True, slots=True)
class AnthropicMessagesRequestPlan:
    adapter_id: str
    adapter_version: str
    transport: str
    sdk_version: str
    base_url: str
    max_retries: int
    trust_env: bool
    params_json: str

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("AnthropicMessagesRequestPlan é final: subclasse recusada")


@final
@dataclass(frozen=True, slots=True)
class AnthropicRuntimeBundle:
    """Config + plano + dispatcher de **uma** execução. O que se verifica é o que se executa."""

    prepared: PreparedAnthropicMessagesConfig
    request_plan: AnthropicMessagesRequestPlan
    dispatcher: CanonicalToolDispatcher

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("AnthropicRuntimeBundle é final: subclasse recusada")


def require_exact_plan(plan: object) -> AnthropicMessagesRequestPlan:
    if type(plan) is not AnthropicMessagesRequestPlan:
        raise RequestBindingError("plano de request fora do tipo canônico")
    return plan


def require_exact_bundle(bundle: object) -> AnthropicRuntimeBundle:
    if type(bundle) is not AnthropicRuntimeBundle:
        raise RequestBindingError("bundle de runtime fora do tipo canônico")
    return bundle


def plan_static_params(plan: AnthropicMessagesRequestPlan) -> dict[str, Any]:
    """Cópia nova da parte estática (lida da `str` canônica do plano de tipo exato)."""
    params: dict[str, Any] = json.loads(require_exact_plan(plan).params_json)
    return params


def plan_document(plan: AnthropicMessagesRequestPlan) -> dict[str, Any]:
    exact = require_exact_plan(plan)
    return {
        "adapter_id": exact.adapter_id,
        "adapter_version": exact.adapter_version,
        "transport": exact.transport,
        "sdk_version": exact.sdk_version,
        "base_url": exact.base_url,
        "max_retries": exact.max_retries,
        "trust_env": exact.trust_env,
        "params": plan_static_params(exact),
    }


def plan_hash(plan: AnthropicMessagesRequestPlan) -> str:
    return canonical_sha256(plan_document(plan))


def build_input_params(
    plan: AnthropicMessagesRequestPlan, messages: list[dict[str, Any]]
) -> dict[str, Any]:
    """O **input** canônico do turno — o mesmo para `count_tokens` e `create`.

    Payload novo a cada chamada: nada do que o transporte receber aponta para o estado do
    plano ou do transcript do provider. Prompt cache desligado (V1): o payload inteiro é
    varrido e uma chave `cache_control` em qualquer nível → `CacheControlRejected` — nenhum
    `count_tokens` nem `create` é montado com ela."""
    payload = {**plan_static_params(plan), "messages": copy.deepcopy(messages)}
    if has_cache_control_key(payload):
        raise CacheControlRejected("cache_control no request: prompt cache desligado na V1")
    return payload


def build_count_tokens_kwargs(
    plan: AnthropicMessagesRequestPlan, messages: list[dict[str, Any]], *, timeout: float
) -> dict[str, Any]:
    return {**build_input_params(plan, messages), "timeout": timeout}


def build_create_kwargs(
    plan: AnthropicMessagesRequestPlan,
    messages: list[dict[str, Any]],
    *,
    max_output_tokens: int,
    timeout: float,
) -> dict[str, Any]:
    if type(max_output_tokens) is not int or max_output_tokens < 1:
        raise RequestBindingError("max_tokens da request precisa ser inteiro positivo")
    return {
        **build_input_params(plan, messages),
        "max_tokens": max_output_tokens,
        "timeout": timeout,
    }


def build_request_plan(prepared: PreparedAnthropicMessagesConfig) -> AnthropicMessagesRequestPlan:
    """O plano estático a partir da config preparada. Sem rede, sem credencial."""
    params = {
        "model": prepared.model,
        "system": SYSTEM_PROMPT,
        "tools": tool_definitions(),
        "tool_choice": {
            "type": prepared.tool_choice_type,
            "disable_parallel_tool_use": prepared.parallel_tool_use_disabled,
        },
        "thinking": {"type": prepared.thinking_type, "display": prepared.thinking_display},
        "output_config": {"effort": prepared.effort},
    }
    return AnthropicMessagesRequestPlan(
        adapter_id=prepared.adapter_id,
        adapter_version=prepared.adapter_version,
        transport=prepared.transport,
        sdk_version=installed_sdk_version(),
        base_url=prepared.api_endpoint,
        max_retries=prepared.max_retries,
        trust_env=prepared.http_trust_env,
        params_json=canonical_json(params),
    )


def build_runtime_bundle(prepared: PreparedAnthropicMessagesConfig) -> AnthropicRuntimeBundle:
    return AnthropicRuntimeBundle(
        prepared=prepared,
        request_plan=build_request_plan(prepared),
        dispatcher=CANONICAL_DISPATCHER,
    )
