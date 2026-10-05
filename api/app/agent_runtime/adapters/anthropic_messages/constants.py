"""Identidade e controles **fixos** do adaptador Claude Messages API (E8.2).

Constantes de código, centralizadas. `ADAPTER_VERSION` entra no `developer_binding` aprovado
(fingerprint) e a configuração preparada inteira entra no `execution_config_hash`.

Regra de versão: mudar o pin do SDK, o contrato do dispatcher, a superfície do request, os
schemas das ferramentas ou a política de transporte exige revisar/bumpar `ADAPTER_VERSION`.
Nenhum bump automático.
"""

from __future__ import annotations

from typing import Final

ADAPTER_ID: Final = "anthropic-messages-api"
#: Provider (fornecedor) que aparece em `AgentRunResult.provider`.
PROVIDER_NAME: Final = "anthropic"
#: [02] `transport`: `cli` · `api` · `process`. Sem runtime Claude Code: é a API.
TRANSPORT: Final = "api"

#: Versão **fixa** do SDK Python da Anthropic (pin exato em `pyproject.toml`).
ANTHROPIC_SDK_DISTRIBUTION: Final = "anthropic"
ANTHROPIC_SDK_VERSION: Final = "1.11.0"
ADAPTER_VERSION: Final = f"ff1-sdk{ANTHROPIC_SDK_VERSION}"

#: Endpoint **oficial**, passado explicitamente ao cliente (nunca `ANTHROPIC_BASE_URL`).
API_ENDPOINT: Final = "https://api.anthropic.com"
#: Sem retentativas implícitas: cada chamada é uma, sob a deadline do run.
MAX_RETRIES: Final = 0

#: Tier neutro → modelo concreto. Só este pacote (e o composition root) conhece os nomes.
STANDARD_MODEL: Final = "claude-sonnet-5-5"
STRONG_MODEL: Final = "claude-opus-5-5"
KNOWN_MODELS: Final = frozenset({STANDARD_MODEL, STRONG_MODEL})
KNOWN_EFFORTS: Final = ("medium", "high")

#: Adaptive thinking dos modelos 5.5, sem devolver o texto do raciocínio à aplicação.
THINKING_TYPE: Final = "adaptive"
THINKING_DISPLAY: Final = "omitted"
#: `auto` + paralelismo desligado: no máximo uma ferramenta por resposta.
TOOL_CHOICE_TYPE: Final = "auto"
DISABLE_PARALLEL_TOOL_USE: Final = True

CONFIG_VERSION: Final = 1
TOOL_SURFACE_VERSION: Final = "ff-client-tools-v1"
#: Contrato do dispatcher canônico (nome → `ToolRequest` → `MediatedTools.execute`).
DISPATCHER_CONTRACT_VERSION: Final = "ff-client-tools-v1"
#: Superfície do request à Messages API (parâmetros permitidos e seus valores).
REQUEST_SURFACE_VERSION: Final = "ff-messages-request-v1"
#: Logging do SDK silenciado durante todo run (AUD-005; `sdk_logging.py`).
SDK_LOGGING_POLICY: Final = "suppressed-v1"
#: `RunLimits.max_tokens` = total input (com cache) + output do run; `count_tokens` como
#: preflight antes de cada `create` (AUD-006; addendum E8.2 de [05] §6).
TOKEN_BUDGET_POLICY: Final = "run-total-input-output-v1"  # noqa: S105 — nome de política
#: Cliente montado só com credencial/endpoint/cliente HTTP explícitos, sem descoberta de
#: perfil/`active_config`/federação, dentro da supervisão do run (AUD-003).
CLIENT_CONSTRUCTION_POLICY: Final = "explicit-no-discovery-v1"
#: Prompt caching **desligado** na V1 (addendum E8.2 de [05] §6): nenhum `cache_control` em
#: nenhum nível do request; só sob esta política `None` nos campos de cache do `usage` vale 0.
#: Qualquer outra política é não suportada pela V1 (exigiria nova revisão do adaptador).
PROMPT_CACHE_POLICY: Final = "disabled"
#: Versão do perfil efetivo (o Developer V1 da E7.1).
CAPABILITY_PROFILE_VERSION: Final = 1
PROVIDER_ROLE: Final = "developer"
