"""Doubles e fábricas da E7.6 — **só testes** podem fornecer um verificador positivo.

O ponto central: `DECLARED` e `OBSERVED` são controlados **separadamente**. Nenhum double aqui
faz `observed = declared` sozinho — `ScriptedVerifier` exige `observed_profile` explícito, de
modo que "declaração verdadeira" e "declaração mentirosa" são os dois representáveis.

O contexto esperado (`expected_binding`) é montado a partir de **literais** do teste — o que
o chamador confiável escolheu —, não da declaração que está sendo verificada.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.agent_runtime import (
    CapabilityDeclaration,
    EnforcementEvidence,
    EnforcementMethod,
    NotVerified,
    VerificationResult,
    Verified,
    binding_of,
)
from app.orchestrator.state_machine import CapabilityProof
from app.safety import (
    CapabilityBinding,
    ProviderCapabilityProfile,
    ProviderRole,
    UnverifiedReason,
    VerifiedCapabilityObservation,
    historical_profile_hash,
)
from app.safety.capability_profile import DEVELOPER_V1_PROFILE

ADAPTER_ID = "adapter-a"
ADAPTER_VERSION = "1"
TRANSPORT = "transport-x"
MODEL = "model-m"
CONFIG_HASH = "1" * 64


def declaration(**changes: Any) -> CapabilityDeclaration:
    """Uma declaração oficial e coerente. `**changes` sobrepõe campos."""
    base: dict[str, Any] = {
        "role": ProviderRole.DEVELOPER,
        "adapter_id": ADAPTER_ID,
        "adapter_version": ADAPTER_VERSION,
        "transport": TRANSPORT,
        "model": MODEL,
        "execution_config_hash": CONFIG_HASH,
        "supported_capabilities": DEVELOPER_V1_PROFILE,
        "effective_capabilities": DEVELOPER_V1_PROFILE,
        "enforcement_method": EnforcementMethod.TOOL_ALLOWLIST,
        "evidence": EnforcementEvidence(applied=("allowlist:mediated-tools",)),
    }
    return CapabilityDeclaration(**{**base, **changes})


def expected_binding(
    prepared: CapabilityDeclaration | None = None, /, **changes: Any
) -> CapabilityBinding:
    """O contexto que o chamador confiável espera para a declaração que **ele** preparou."""
    prepared = prepared or declaration()
    base: dict[str, Any] = {
        "role": ProviderRole.DEVELOPER,
        "adapter_id": ADAPTER_ID,
        "adapter_version": ADAPTER_VERSION,
        "transport": TRANSPORT,
        "model": MODEL,
        "execution_config_hash": CONFIG_HASH,
        "declaration_hash": prepared.declaration_hash(),
    }
    return CapabilityBinding(**{**base, **changes})


def profile(**modes: Any) -> ProviderCapabilityProfile:
    """O perfil oficial do Developer com as capabilities de `modes` trocadas."""
    return replace(DEVELOPER_V1_PROFILE, **modes)


class ScriptedVerifier:
    """Verificador de teste. `observed_profile` é OBRIGATÓRIO e independe da declaração."""

    def __init__(
        self,
        *,
        observed_profile: ProviderCapabilityProfile | None,
        binding_changes: dict[str, Any] | None = None,
        not_verified: UnverifiedReason | None = None,
        raises: Exception | None = None,
        returns: object = None,
    ) -> None:
        self.observed_profile = observed_profile
        self.binding_changes = binding_changes or {}
        self.not_verified = not_verified
        self.raises = raises
        self.returns = returns
        self.calls: list[CapabilityDeclaration] = []

    def verify(self, declaration: CapabilityDeclaration) -> VerificationResult:
        self.calls.append(declaration)
        if self.raises is not None:
            raise self.raises
        if self.returns is not None:
            return self.returns  # type: ignore[return-value]
        if self.not_verified is not None:
            return NotVerified(self.not_verified)
        assert self.observed_profile is not None
        return Verified(
            VerifiedCapabilityObservation(
                binding=replace(binding_of(declaration), **self.binding_changes),
                profile=self.observed_profile,
            )
        )


class FixedProver:
    """`CapabilityProver` que devolve uma prova pronta (para testar a guarda pura)."""

    def __init__(self, proof: CapabilityProof) -> None:
        self.proof = proof
        self.calls = 0

    def prove(self, required_profile_hash: str) -> CapabilityProof:
        del required_profile_hash
        self.calls += 1
        return self.proof


def full_proof(
    *,
    binding: CapabilityBinding | None = None,
    observed: ProviderCapabilityProfile = DEVELOPER_V1_PROFILE,
    effective_hash: str | None = None,
) -> CapabilityProof:
    """Uma prova completa e positiva (observação + hash) para a guarda pura."""
    observation = VerifiedCapabilityObservation(
        binding=binding or expected_binding(), profile=observed
    )
    return CapabilityProof(
        proven=True,
        effective_profile_hash=effective_hash or historical_profile_hash(DEVELOPER_V1_PROFILE),
        observation=observation,
    )
