"""Adaptador Claude Messages API do Developer (E8.2, `transport="api"`).

Único pacote que importa `anthropic`. Só o composition root (`app.developer_wiring`, chamado
por `main.py`) o conhece. Ver `request_plan.py` (plano estático + bundle de runtime),
`dispatcher.py` (despacho canônico das nove client tools), `verifier.py` (verificador real),
`provider.py` (loop manual da API) e `transport.py` (porta + cliente real).

Não há runtime Claude Code nesta cadeia (sem CLI, MCP, settings, hooks, plugins ou skills).
"""

from app.agent_runtime.adapters.anthropic_messages.config import (
    SUPPORTED_CAPABILITIES,
    SYSTEM_PROMPT,
    PreparedAnthropicMessagesConfig,
    UnsupportedDeveloperConfiguration,
    build_declaration,
    prepare_anthropic_messages_config,
)
from app.agent_runtime.adapters.anthropic_messages.constants import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    ANTHROPIC_SDK_VERSION,
    API_ENDPOINT,
    PROMPT_CACHE_POLICY,
    STANDARD_MODEL,
    STRONG_MODEL,
    TRANSPORT,
)
from app.agent_runtime.adapters.anthropic_messages.dispatcher import (
    CANONICAL_DISPATCHER,
    CanonicalToolDispatcher,
    DispatchOutcome,
    UnknownTool,
)
from app.agent_runtime.adapters.anthropic_messages.provider import (
    AnthropicMessagesDeveloperProvider,
    AnthropicMessagesProviderFactory,
)
from app.agent_runtime.adapters.anthropic_messages.request_plan import (
    AnthropicMessagesRequestPlan,
    AnthropicRuntimeBundle,
    CacheControlRejected,
    RequestBindingError,
    build_count_tokens_kwargs,
    build_create_kwargs,
    build_input_params,
    build_request_plan,
    build_runtime_bundle,
    has_cache_control_key,
    plan_hash,
    plan_static_params,
)
from app.agent_runtime.adapters.anthropic_messages.sdk_logging import (
    AnthropicSdkLoggingGuard,
    SdkLoggingRefused,
)
from app.agent_runtime.adapters.anthropic_messages.token_accounting import (
    PromptCacheActivity,
    UnsupportedPromptCachePolicy,
    next_output_cap,
    normalize_usage_for_disabled_cache,
    turn_total_tokens,
)
from app.agent_runtime.adapters.anthropic_messages.tool_surface import (
    TOOL_NAMES,
    TOOL_SPECS,
    tool_definitions,
    tool_schema_hash,
)
from app.agent_runtime.adapters.anthropic_messages.transport import (
    AnthropicApiTransport,
    AnthropicApiTransportFactory,
    ExplicitAsyncAnthropic,
    ExplicitTlsTransport,
    MessagesTransport,
    TransportEnvironmentRejected,
    TransportOpener,
    explicit_tls_context,
)
from app.agent_runtime.adapters.anthropic_messages.verifier import (
    AnthropicMessagesCapabilityVerifier,
    AnthropicVerifierTechnicalError,
)

__all__ = [
    "ADAPTER_ID",
    "ADAPTER_VERSION",
    "ANTHROPIC_SDK_VERSION",
    "API_ENDPOINT",
    "CANONICAL_DISPATCHER",
    "PROMPT_CACHE_POLICY",
    "STANDARD_MODEL",
    "STRONG_MODEL",
    "SUPPORTED_CAPABILITIES",
    "SYSTEM_PROMPT",
    "TOOL_NAMES",
    "TOOL_SPECS",
    "TRANSPORT",
    "AnthropicApiTransport",
    "AnthropicApiTransportFactory",
    "AnthropicMessagesCapabilityVerifier",
    "AnthropicMessagesDeveloperProvider",
    "AnthropicMessagesProviderFactory",
    "AnthropicMessagesRequestPlan",
    "AnthropicRuntimeBundle",
    "AnthropicSdkLoggingGuard",
    "AnthropicVerifierTechnicalError",
    "CacheControlRejected",
    "CanonicalToolDispatcher",
    "DispatchOutcome",
    "ExplicitAsyncAnthropic",
    "ExplicitTlsTransport",
    "MessagesTransport",
    "PreparedAnthropicMessagesConfig",
    "PromptCacheActivity",
    "RequestBindingError",
    "SdkLoggingRefused",
    "TransportEnvironmentRejected",
    "TransportOpener",
    "UnknownTool",
    "UnsupportedDeveloperConfiguration",
    "UnsupportedPromptCachePolicy",
    "build_count_tokens_kwargs",
    "build_create_kwargs",
    "build_declaration",
    "build_input_params",
    "build_request_plan",
    "build_runtime_bundle",
    "explicit_tls_context",
    "has_cache_control_key",
    "next_output_cap",
    "normalize_usage_for_disabled_cache",
    "plan_hash",
    "plan_static_params",
    "prepare_anthropic_messages_config",
    "tool_definitions",
    "tool_schema_hash",
    "turn_total_tokens",
]
