"""Porta de **verificação independente** de capability (E7.6-A).

Fontes congeladas: [04] §1, [05] §3 e [ADR-0009] §3 ("declaração não é prova").

## Declaração ≠ verificação

`CapabilityDeclaration` (E7.2) é o que o adaptador *afirma*. `CapabilityVerifier` é a
**segunda** porta, distinta da primeira: um mecanismo que observa — por meios que
pertencem ao transport do adaptador correspondente — o que foi realmente aplicado à
configuração preparada, e devolve um `VerifiedCapabilityObservation` com o perfil
**completo** (as sete capabilities) e o `CapabilityBinding` do que foi verificado.

* recebe a declaração só para saber *qual* configuração verificar — nunca para decidir;
* não conhece o Orchestrator, o banco, a rede nem nenhum provider concreto;
* chega por injeção: não há lookup global nem registry;
* **nenhuma implementação positiva existe em produção na E7.6.** Só testes fornecem uma.
  Sem verificador, o resultado é `VERIFIER_ABSENT` e a execução é recusada.

Este módulo **não interpreta flags** (`--sandbox`, `--no-network`, …) como prova universal:
o significado de um controle específico pertence ao adaptador/verificador do seu transport,
a ser integrado em etapa posterior.

## Resultados negativos × falha técnica

`NotVerified` é um resultado negativo *esperado* e tipado (`UnverifiedReason`): vira recusa
de política auditável. Uma **exceção** do verificador é falha técnica — sobe como defeito,
não vira `capability_unenforceable`, e a mensagem dela nunca chega a diagnóstico.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.agent_runtime.declaration import CapabilityDeclaration, EnforcementMethod
from app.safety.capability_profile import Capability
from app.safety.capability_verification import (
    CapabilityBinding,
    CapabilityRefusal,
    CapabilityRefusalCode,
    UnverifiedReason,
    VerifiedCapabilityObservation,
)


@dataclass(frozen=True, slots=True)
class Verified:
    """O verificador observou a configuração. A observação é dado — não um veredito."""

    observation: VerifiedCapabilityObservation


@dataclass(frozen=True, slots=True)
class NotVerified:
    """O verificador respondeu que **não** consegue verificar. Negativo esperado e tipado."""

    reason: UnverifiedReason


VerificationResult = Verified | NotVerified


class CapabilityVerifier(Protocol):
    """Observa, de forma independente do adaptador, o que foi aplicado à configuração."""

    def verify(self, declaration: CapabilityDeclaration) -> VerificationResult: ...


class CapabilityVerifierContractViolation(RuntimeError):
    """O verificador devolveu algo que não é `Verified` nem `NotVerified`: defeito, não política."""


def binding_of(declaration: CapabilityDeclaration) -> CapabilityBinding:
    """O binding que descreve `declaration` — para verificadores preencherem o que verificaram.

    **Não** é como o contexto *esperado* é obtido: esse vem do chamador confiável. Usar
    isto para montar o esperado a partir da mesma declaração que está sendo verificada
    tornaria a comparação uma tautologia.
    """
    return CapabilityBinding(
        role=declaration.role,
        adapter_id=declaration.adapter_id,
        adapter_version=declaration.adapter_version,
        transport=declaration.transport,
        model=declaration.model,
        execution_config_hash=declaration.execution_config_hash,
        declaration_hash=declaration.declaration_hash(),
    )


def observe_declared_capabilities(
    verifier: CapabilityVerifier | None,
    declaration: CapabilityDeclaration | None,
) -> VerifiedCapabilityObservation | CapabilityRefusal:
    """Pede ao verificador a observação de `declaration`, ou devolve a recusa tipada.

    Recusas (nenhuma é exceção): verificador/declaração ausentes; `not_enforceable` (o
    verificador **nem é chamado**: não há controle a observar); verificação negativa; e a
    declaração que **discorda** do observado — declaração mentirosa. O perfil efetivo
    *declarado* só participa como confronto; o único perfil que segue adiante é o observado.

    Esta função não decide aceitação: o perfil observado ainda precisa ser avaliado contra
    o papel e o binding esperados (`safety.evaluate_observation`) por quem tem o contexto
    confiável. Exceções do verificador **propagam**.
    """
    if verifier is None or declaration is None:
        return CapabilityRefusal(CapabilityRefusalCode.VERIFIER_ABSENT)

    if declaration.enforcement_method is EnforcementMethod.NOT_ENFORCEABLE:
        return CapabilityRefusal(CapabilityRefusalCode.DECLARATION_NOT_ENFORCEABLE)

    result = verifier.verify(declaration)

    if isinstance(result, NotVerified):
        return CapabilityRefusal(
            CapabilityRefusalCode.VERIFICATION_NEGATIVE, (result.reason.value,)
        )
    if not isinstance(result, Verified):
        raise CapabilityVerifierContractViolation(
            f"verify devolveu {type(result).__name__}, esperado Verified ou NotVerified"
        )

    observed = result.observation.profile
    declared = declaration.effective_capabilities
    lied_about = tuple(
        capability.value
        for capability in Capability
        if declared.mode_of(capability) is not observed.mode_of(capability)
    )
    if lied_about:
        return CapabilityRefusal(CapabilityRefusalCode.DECLARATION_OBSERVATION_MISMATCH, lied_about)

    return result.observation


__all__ = [
    "CapabilityVerifier",
    "CapabilityVerifierContractViolation",
    "NotVerified",
    "VerificationResult",
    "Verified",
    "binding_of",
    "observe_declared_capabilities",
]
