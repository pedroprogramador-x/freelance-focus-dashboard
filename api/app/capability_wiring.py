"""Ponte do composition root entre a verificação independente e a guarda (E7.6-C).

Este é o **único** módulo, fora de `main.py`, que enxerga ao mesmo tempo `agent_runtime`
(a porta `CapabilityVerifier`) e `orchestrator` (a porta `CapabilityProver`). Ele existe
para que nenhuma das duas camadas importe a outra ([01] §3): `agent_runtime` não conhece o
Orchestrator e o Orchestrator não conhece `agent_runtime`.

`VerifyingCapabilityProver` adapta uma declaração + um verificador **injetados** à porta que
a guarda `approved → executing` consome. Ele:

* **não autoriza nada**: devolve a observação independente; a guarda a reavalia sozinha
  contra o contexto esperado do chamador confiável e não confia em `proven`;
* não faz lookup de provider, não toca banco, rede ou processo;
* não captura exceções do verificador — falha técnica sobe como defeito, e não vira
  `capability_unenforceable`.

**Produção na E7.6:** nenhum verificador positivo existe. `main.py` não instala fake algum;
sem verificador, o resultado é `VERIFIER_ABSENT` e a execução é recusada. Doubles positivos
vivem só em `tests/`.
"""

from __future__ import annotations

from app.agent_runtime.declaration import CapabilityDeclaration
from app.agent_runtime.verification import CapabilityVerifier, observe_declared_capabilities
from app.orchestrator.state_machine import CapabilityProof
from app.safety.capability_profile import ProviderRole, check_v1
from app.safety.capability_verification import (
    CapabilityRefusal,
    CapabilityRefusalCode,
    historical_profile_hash,
)


class VerifyingCapabilityProver:
    """`CapabilityProver` baseado em verificação independente. Instância por execução."""

    def __init__(
        self,
        *,
        verifier: CapabilityVerifier | None,
        declaration: CapabilityDeclaration | None,
    ) -> None:
        self._verifier = verifier
        self._declaration = declaration

    def prove(self, required_profile_hash: str) -> CapabilityProof:
        del required_profile_hash  # a guarda compara; o provador só observa
        outcome = observe_declared_capabilities(self._verifier, self._declaration)

        if isinstance(outcome, CapabilityRefusal):
            return CapabilityProof(proven=False, refusal=outcome)

        # Esta ponte serve a guarda `approved → executing`, que é a do Developer. Só um perfil
        # aceitável para ele tem hash histórico; um recusável não pode fabricar o hash
        # aprovado (o documento v1 nem enxerga git_read/git_write/external_paths). O papel
        # e o binding da observação são julgados pela guarda, contra o contexto esperado.
        violations = check_v1(outcome.profile, ProviderRole.DEVELOPER)
        if violations:
            return CapabilityProof(
                proven=False,
                refusal=CapabilityRefusal(
                    CapabilityRefusalCode.PROFILE_MISMATCH,
                    tuple(v.capability.value for v in violations),
                ),
            )

        return CapabilityProof(
            proven=True,
            effective_profile_hash=historical_profile_hash(outcome.profile),
            observation=outcome,
        )


__all__ = ["VerifyingCapabilityProver"]
