"""`CapabilityVerifier` real do adaptador Messages API (E8.2).

Não conclui "positivo" porque a declaração diz "sem shell". Ele **observa** o bundle de
runtime que o provider vai executar (`AnthropicRuntimeBundle`) e o confronta com constantes
**próprias** — nunca com a declaração nem com a config preparada, que poderiam se
autovalidar.

## O que é observado

* identidade do adaptador e `transport == "api"`;
* versão do SDK **pela metadata da distribuição instalada**, no momento do `verify`;
* endpoint oficial explícito, `max_retries == 0`, cliente HTTP com `trust_env=False`;
* o request estático: só as chaves aprovadas (nada de `container`, `stop_sequences`,
  `extra_body`…), prompt de sistema próprio, `tool_choice` auto sem paralelismo, *adaptive
  thinking* com `display="omitted"`, `output_config.effort`, modelo conhecido;
* **exatamente** as nove *client tools*, cada uma com exatamente `{name, description,
  input_schema}` — qualquer `type` (toda *server tool* da Anthropic tem um) ou chave extra,
  ferramenta a mais, a menos ou alterada → negativo;
* o **dispatcher**: o tipo exato `CanonicalToolDispatcher` e a instância canônica — mesma
  superfície declarativa com outra implementação não passa;
* que o plano é exatamente o que a config preparada produz (hash do plano);
* nenhuma chave estrutural `cache_control` em nível algum da superfície estática (política
  de prompt cache `disabled`, que entra no hash pela config observada);
* **tipo exato** de bundle, plano, config e dispatcher — o request é montado por função de
  módulo (`request_plan.build_*`), não por método que uma subclasse pudesse sobrescrever.

## Resultado

* controle ausente/inseguro → `NotVerified(CONTROLS_NOT_OBSERVED)`;
* SDK/modelo/effort/superfície/declaração que não descrevem a config preparada à qual o
  verificador está vinculado → `NotVerified(UNSUPPORTED_CONFIGURATION)`;
* tudo confere → `Verified` com o binding **observado** (`execution_config_hash` recomputado
  do que foi observado; nada copiado da declaração);
* defeito técnico (fonte que não devolve um bundle, SDK não instalado) → **exceção**.

Não há runtime Claude Code nesta cadeia: nada de settings/hooks/MCP gerenciados a observar.
Limite honesto (premissa da E7.6): não protege contra Python hostil no mesmo processo.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.agent_runtime.adapters.anthropic_messages.config import (
    SYSTEM_PROMPT,
    PreparedAnthropicMessagesConfig,
    build_declaration,
    system_prompt_hash,
)
from app.agent_runtime.adapters.anthropic_messages.constants import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    ANTHROPIC_SDK_VERSION,
    API_ENDPOINT,
    CAPABILITY_PROFILE_VERSION,
    CLIENT_CONSTRUCTION_POLICY,
    CONFIG_VERSION,
    DISABLE_PARALLEL_TOOL_USE,
    DISPATCHER_CONTRACT_VERSION,
    KNOWN_EFFORTS,
    KNOWN_MODELS,
    MAX_RETRIES,
    PROMPT_CACHE_POLICY,
    PROVIDER_ROLE,
    REQUEST_SURFACE_VERSION,
    SDK_LOGGING_POLICY,
    THINKING_DISPLAY,
    THINKING_TYPE,
    TOKEN_BUDGET_POLICY,
    TOOL_CHOICE_TYPE,
    TOOL_SURFACE_VERSION,
    TRANSPORT,
)
from app.agent_runtime.adapters.anthropic_messages.dispatcher import (
    CANONICAL_DISPATCHER,
    CanonicalToolDispatcher,
)
from app.agent_runtime.adapters.anthropic_messages.request_plan import (
    STATIC_PARAM_KEYS,
    AnthropicMessagesRequestPlan,
    AnthropicRuntimeBundle,
    build_request_plan,
    has_cache_control_key,
    installed_sdk_version,
    plan_hash,
    plan_static_params,
)
from app.agent_runtime.adapters.anthropic_messages.tool_surface import (
    TOOL_NAMES,
    surface_hash,
    tool_definitions,
)
from app.agent_runtime.declaration import CapabilityDeclaration
from app.agent_runtime.verification import NotVerified, VerificationResult, Verified
from app.safety.capability_profile import (
    EnforcementMode,
    ProviderCapabilityProfile,
    ProviderRole,
)
from app.safety.capability_verification import (
    CapabilityBinding,
    UnverifiedReason,
    VerifiedCapabilityObservation,
)

BundleSource = Callable[[], AnthropicRuntimeBundle]

_CLIENT_TOOL_KEYS = frozenset({"name", "description", "input_schema"})


class AnthropicVerifierTechnicalError(RuntimeError):
    """Defeito técnico do verificador (não é recusa de política)."""


def _exact_types_hold(bundle: AnthropicRuntimeBundle) -> bool:
    """Tipo **exato** de bundle, plano, config e dispatcher (AUD-004): `isinstance` aceitaria
    uma subclasse que sobrescrevesse a montagem do request."""
    return (
        type(bundle.request_plan) is AnthropicMessagesRequestPlan
        and type(bundle.prepared) is PreparedAnthropicMessagesConfig
        and type(bundle.dispatcher) is CanonicalToolDispatcher
    )


def _transport_controls_hold(bundle: AnthropicRuntimeBundle) -> bool:
    plan = bundle.request_plan
    dispatcher = bundle.dispatcher
    return (
        plan.adapter_id == ADAPTER_ID
        and plan.adapter_version == ADAPTER_VERSION
        and plan.transport == TRANSPORT
        and plan.base_url == API_ENDPOINT
        and type(plan.max_retries) is int
        and plan.max_retries == MAX_RETRIES
        and plan.trust_env is False
        and type(dispatcher) is CanonicalToolDispatcher
        and dispatcher is CANONICAL_DISPATCHER
        and dispatcher.contract_version == DISPATCHER_CONTRACT_VERSION
    )


def _observed_tools(tools: object) -> list[dict[str, Any]] | None:
    """As nove *client tools* exatas, ou `None` (server tool, extra, ausente, alterada)."""
    if not isinstance(tools, list) or len(tools) != len(TOOL_NAMES):
        return None
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) != _CLIENT_TOOL_KEYS:
            return None  # `type` (server tool / variante) ou chave a mais/a menos
    names = [tool["name"] for tool in tools]
    if names != list(TOOL_NAMES):
        return None
    if tools != tool_definitions():
        return None
    return tools


def _request_controls_hold(params: object) -> bool:
    if not isinstance(params, dict) or set(params) != STATIC_PARAM_KEYS:
        return False
    if has_cache_control_key(params):  # prompt cache desligado: nenhuma chave em nível algum
        return False
    output_config = params["output_config"]
    return (
        params["system"] == SYSTEM_PROMPT
        and params["tool_choice"]
        == {"type": TOOL_CHOICE_TYPE, "disable_parallel_tool_use": DISABLE_PARALLEL_TOOL_USE}
        and params["thinking"] == {"type": THINKING_TYPE, "display": THINKING_DISPLAY}
        and isinstance(output_config, dict)
        and set(output_config) == {"effort"}
        and output_config["effort"] in KNOWN_EFFORTS
        and params["model"] in KNOWN_MODELS
        and _observed_tools(params["tools"]) is not None
    )


def _profile_from_controls() -> ProviderCapabilityProfile:
    """O perfil que os controles **já verificados** implicam:

    * `read_files`/`write_files`: só pelas operações mediadas do dispatcher canônico;
    * `execute_commands`: nenhuma ferramenta de comando, nenhuma server tool de execução,
      nenhum runtime Claude Code/hook, e `ToolRequest` não tem `ExecCommand` → `disabled`;
    * `git_read`: só GitStatus/GitDiff/GitShow/GitListTree → `fixed_operations_only`;
    * `git_write`: nenhuma operação git de escrita → `disabled`;
    * `network`: nenhuma tool de rede (client ou server); o tráfego do transporte à API não é
      capability do modelo → `disabled`;
    * `external_paths`: o modelo não tem filesystem; só `ToolRequest` confinado → `disabled`.
    """
    return ProviderCapabilityProfile(
        read_files=EnforcementMode.MEDIATED,
        write_files=EnforcementMode.MEDIATED,
        execute_commands=EnforcementMode.DISABLED,
        git_read=EnforcementMode.FIXED_OPERATIONS_ONLY,
        git_write=EnforcementMode.DISABLED,
        network=EnforcementMode.DISABLED,
        external_paths=EnforcementMode.DISABLED,
    )


def _observed_config(
    bundle: AnthropicRuntimeBundle, params: dict[str, Any], sdk_version: str
) -> PreparedAnthropicMessagesConfig:
    """A config **reconstruída do observado** (após os controles). Define o hash."""
    plan = bundle.request_plan
    return PreparedAnthropicMessagesConfig(
        model=params["model"],
        effort=params["output_config"]["effort"],
        version=CONFIG_VERSION,
        provider_role=PROVIDER_ROLE,
        adapter_id=plan.adapter_id,
        adapter_version=plan.adapter_version,
        transport=plan.transport,
        anthropic_sdk_version=sdk_version,
        api_endpoint=plan.base_url,
        max_retries=plan.max_retries,
        http_trust_env=plan.trust_env,
        thinking_type=params["thinking"]["type"],
        thinking_display=params["thinking"]["display"],
        system_prompt_sha256=system_prompt_hash(params["system"]),
        tool_surface_version=TOOL_SURFACE_VERSION,
        tool_names=tuple(tool["name"] for tool in params["tools"]),
        tool_schema_hash=surface_hash(params["tools"]),
        server_tools=(),
        dispatcher_contract_version=bundle.dispatcher.contract_version,
        tool_choice_type=params["tool_choice"]["type"],
        parallel_tool_use_disabled=params["tool_choice"]["disable_parallel_tool_use"],
        request_surface_version=REQUEST_SURFACE_VERSION,
        sdk_logging_policy=SDK_LOGGING_POLICY,
        token_budget_policy=TOKEN_BUDGET_POLICY,
        client_construction_policy=CLIENT_CONSTRUCTION_POLICY,
        prompt_cache_policy=PROMPT_CACHE_POLICY,
        capability_profile_version=CAPABILITY_PROFILE_VERSION,
        effective_capabilities=_profile_from_controls(),
    )


class AnthropicMessagesCapabilityVerifier:
    """Vinculado a **uma** config preparada; observa o bundle real a cada `verify`."""

    def __init__(
        self, prepared: PreparedAnthropicMessagesConfig, bundle_source: BundleSource
    ) -> None:
        if not isinstance(prepared, PreparedAnthropicMessagesConfig):
            raise AnthropicVerifierTechnicalError("prepared precisa ser a config preparada")
        self._prepared = prepared
        self._bundle_source = bundle_source

    def verify(self, declaration: CapabilityDeclaration) -> VerificationResult:
        bundle = self._bundle_source()
        if not isinstance(bundle, AnthropicRuntimeBundle):
            raise AnthropicVerifierTechnicalError("a fonte não devolveu um bundle de runtime")
        installed = installed_sdk_version()  # metadata real; ausência é falha técnica

        if type(bundle) is not AnthropicRuntimeBundle or not _exact_types_hold(bundle):
            return NotVerified(UnverifiedReason.CONTROLS_NOT_OBSERVED)
        if not _transport_controls_hold(bundle):
            return NotVerified(UnverifiedReason.CONTROLS_NOT_OBSERVED)
        params = plan_static_params(bundle.request_plan)
        if not _request_controls_hold(params):
            return NotVerified(UnverifiedReason.CONTROLS_NOT_OBSERVED)

        if not (bundle.request_plan.sdk_version == installed == ANTHROPIC_SDK_VERSION):
            return NotVerified(UnverifiedReason.UNSUPPORTED_CONFIGURATION)

        prepared_hash = self._prepared.execution_config_hash()
        observed = _observed_config(bundle, params, installed)
        observed_hash = observed.execution_config_hash()
        if (
            observed_hash != prepared_hash
            or bundle.prepared.execution_config_hash() != prepared_hash
            or plan_hash(bundle.request_plan) != plan_hash(build_request_plan(self._prepared))
        ):
            return NotVerified(UnverifiedReason.UNSUPPORTED_CONFIGURATION)

        rebuilt = build_declaration(observed)
        if declaration.declaration_hash() != rebuilt.declaration_hash():
            return NotVerified(UnverifiedReason.UNSUPPORTED_CONFIGURATION)

        return Verified(
            VerifiedCapabilityObservation(
                binding=CapabilityBinding(
                    role=ProviderRole.DEVELOPER,
                    adapter_id=bundle.request_plan.adapter_id,
                    adapter_version=bundle.request_plan.adapter_version,
                    transport=bundle.request_plan.transport,
                    model=observed.model,
                    execution_config_hash=observed_hash,
                    declaration_hash=rebuilt.declaration_hash(),
                ),
                profile=observed.effective_capabilities,
            )
        )


__all__ = [
    "AnthropicMessagesCapabilityVerifier",
    "AnthropicVerifierTechnicalError",
    "BundleSource",
]
