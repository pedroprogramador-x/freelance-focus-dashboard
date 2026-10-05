"""Composition root do Developer (E8.2): resolver concreto e contexto de segurança.

Este é — com `main.py` — o único módulo que enxerga ao mesmo tempo o Orchestrator
(`DeveloperBinding`, tiers, effort) e o adaptador concreto
(`agent_runtime.adapters.anthropic_messages`, Claude Messages API, `transport="api"`). Nenhum
dos dois importa o outro: o Orchestrator continua neutro de fornecedor ([01] §3) e o
adaptador não conhece o Orchestrator.

## O que existe aqui

* `AnthropicDeveloperBindingResolver` — tier neutro → `DeveloperBinding` concreto
  (adaptador, versão, modelo). Recebe só o tier: o effort não pode ir parar no `model`;
* `prepare_developer_security_context` — a partir do binding **aprovado** e do effort
  **aprovado**, monta a config preparada confiável, o bundle de runtime (plano + dispatcher
  canônico), a declaração, o `CapabilityBinding` **esperado**, o verificador real (vinculado
  a esse bundle) e o provador. O esperado vem da config preparada e do binding aprovado —
  **nunca** de uma `CapabilityProof`, de uma observação ou de uma declaração recebida de fora.

## O que não existe (E8.4)

Nada aqui chama a API, lê credencial, cria worktree ou executa um run. O provider real
(`AnthropicMessagesProviderFactory`) precisa de uma factory de transporte com credencial
injetada, cujo wiring de segredo é E8.4. `app.state.capability_verifier` segue `None`: o
verificador é vinculado a **um** bundle, e um verificador global quebraria o vínculo por-run.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from app.agent_runtime.adapters.anthropic_messages import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    STANDARD_MODEL,
    STRONG_MODEL,
    AnthropicMessagesCapabilityVerifier,
    AnthropicRuntimeBundle,
    PreparedAnthropicMessagesConfig,
    build_declaration,
    build_runtime_bundle,
    prepare_anthropic_messages_config,
)
from app.agent_runtime.declaration import CapabilityDeclaration
from app.capability_wiring import VerifyingCapabilityProver
from app.orchestrator.developer_binding import DeveloperBinding
from app.orchestrator.model_router import DeveloperEffort, DeveloperModelTier
from app.safety.capability_profile import ProviderRole
from app.safety.capability_verification import CapabilityBinding

#: Tier neutro → modelo concreto. Nomes concretos vivem só aqui e no adaptador.
_MODEL_BY_TIER: MappingProxyType[DeveloperModelTier, str] = MappingProxyType(
    {
        DeveloperModelTier.STANDARD: STANDARD_MODEL,
        DeveloperModelTier.STRONG: STRONG_MODEL,
    }
)

#: Effort neutro → `output_config.effort` da API. Explícito: nunca inferido do modelo.
_API_EFFORT: MappingProxyType[DeveloperEffort, str] = MappingProxyType(
    {
        DeveloperEffort.MEDIUM: "medium",
        DeveloperEffort.HIGH: "high",
    }
)


class AnthropicDeveloperBindingResolver:
    """`DeveloperBindingResolver` concreto. Sem estado; determinístico; sem rede."""

    def resolve(self, tier: DeveloperModelTier) -> DeveloperBinding:
        return DeveloperBinding(
            adapter=ADAPTER_ID, adapter_version=ADAPTER_VERSION, model=_MODEL_BY_TIER[tier]
        )


class BindingNotServed(ValueError):
    """O binding aprovado não é servido por este adaptador (outro adaptador/versão/modelo)."""


@dataclass(frozen=True, slots=True)
class PreparedDeveloperSecurityContext:
    """Tudo o que a guarda `approved → executing` precisa, para **uma** execução.

    `runtime_bundle` é o que o provider de E8.4 deve executar: o mesmo que o verificador
    observa."""

    prepared_config: PreparedAnthropicMessagesConfig
    runtime_bundle: AnthropicRuntimeBundle
    declaration: CapabilityDeclaration
    expected_binding: CapabilityBinding
    verifier: AnthropicMessagesCapabilityVerifier
    prover: VerifyingCapabilityProver


def prepare_developer_security_context(
    binding: DeveloperBinding, effort: DeveloperEffort
) -> PreparedDeveloperSecurityContext:
    """Monta o contexto de segurança do binding e effort **aprovados**. Sem rede, sem segredo."""
    if binding.adapter != ADAPTER_ID:
        raise BindingNotServed("binding de outro adaptador")
    if effort not in _API_EFFORT:
        raise BindingNotServed("effort fora do contrato")

    prepared = prepare_anthropic_messages_config(
        model=binding.model,
        effort=_API_EFFORT[effort],
        adapter_version=binding.adapter_version,
    )
    bundle = build_runtime_bundle(prepared)
    declaration = build_declaration(prepared)
    verifier = AnthropicMessagesCapabilityVerifier(prepared, lambda: bundle)
    expected = CapabilityBinding(
        role=ProviderRole.DEVELOPER,
        adapter_id=binding.adapter,
        adapter_version=binding.adapter_version,
        transport=prepared.transport,
        model=binding.model,
        execution_config_hash=prepared.execution_config_hash(),
        declaration_hash=declaration.declaration_hash(),
    )
    return PreparedDeveloperSecurityContext(
        prepared_config=prepared,
        runtime_bundle=bundle,
        declaration=declaration,
        expected_binding=expected,
        verifier=verifier,
        prover=VerifyingCapabilityProver(verifier=verifier, declaration=declaration),
    )


__all__ = [
    "AnthropicDeveloperBindingResolver",
    "BindingNotServed",
    "PreparedDeveloperSecurityContext",
    "prepare_developer_security_context",
]
