"""Developer Model Router — E8.1.

**Determinístico. Nenhum LLM participa.** Decide, para o Developer, duas coisas
**distintas**:

1. a **classe de modelo** (`DeveloperModelTier`) — neutra de provider;
2. o **reasoning effort** (`DeveloperEffort`).

As duas saem de `(risk, complexity)` por uma tabela fechada, célula a célula. Nenhuma
das duas é derivada da outra: a tabela tem as quatro combinações (`standard/medium`,
`standard/high`, `strong/medium`, `strong/high`), e é isso que impede a regra implícita
"modelo forte ⇒ effort alto".

    | risco \\ complexidade | trivial         | low             | medium          | high          |
    | low    | standard/medium | standard/medium | standard/medium | strong/medium |
    | medium | standard/medium | standard/medium | standard/high   | strong/medium |
    | high   | standard/high   | standard/high   | strong/medium   | strong/high   |

## O que este módulo **não** sabe

Nome de fornecedor, identificador concreto de modelo, adaptador, transporte. O tier é uma
classe abstrata (`standard` = o modelo normal de coding; `strong` = o mais capaz), e quem
o converte em `{adapter, adapter_version, model}` é um `DeveloperBindingResolver`
injetado (`developer_binding.py`), cuja implementação concreta pertence ao composition
root — nunca ao Orchestrator.

## Separação do Resource Router

`resource_router.py` decide **quais agentes** e **quais orçamentos**; este módulo decide
**qual classe de modelo e qual effort** o Developer usa. As duas tabelas vivem separadas
para que mudar uma não exija ler a outra.

## Fail closed

Puro: sem IO, sem banco, sem ambiente, sem relógio, sem estado global mutável. Entrada
fora do contrato (`str` no lugar do enum, `None`, valor desconhecido) levanta
`ModelRoutingContractError` — nunca um default implícito. `RiskLevel`/`ComplexityLevel`
herdam de `str`, então `"low"` acharia a célula de `RiskLevel.LOW` num dicionário: a
checagem de tipo exata é o que impede essa coerção silenciosa.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from app.db.enums import ComplexityLevel, RiskLevel


class DeveloperModelTier(Enum):
    """Classe de modelo do Developer, **neutra de provider**."""

    #: O modelo normal de coding.
    STANDARD = "standard"
    #: O modelo mais capaz.
    STRONG = "strong"


class DeveloperEffort(Enum):
    """Reasoning effort do Developer. A política da E8.1 só produz estes dois valores."""

    MEDIUM = "medium"
    HIGH = "high"


class ModelRoutingContractError(ValueError):
    """Entrada ou saída fora do contrato do Model Router. É defeito, não decisão."""


@dataclass(frozen=True, slots=True)
class DeveloperModelDecision:
    """O que o Router decidiu: dois campos **independentes**, nunca uma string única."""

    tier: DeveloperModelTier
    effort: DeveloperEffort

    def __post_init__(self) -> None:
        if not isinstance(self.tier, DeveloperModelTier):
            raise ModelRoutingContractError(f"tier fora do contrato: {self.tier!r}")
        if not isinstance(self.effort, DeveloperEffort):
            raise ModelRoutingContractError(f"effort fora do contrato: {self.effort!r}")


_STANDARD = DeveloperModelTier.STANDARD
_STRONG = DeveloperModelTier.STRONG
_MEDIUM = DeveloperEffort.MEDIUM
_HIGH = DeveloperEffort.HIGH

#: A política V1, célula a célula. Imutável (`MappingProxyType`): nenhum chamador a altera
#: em tempo de execução.
DEVELOPER_MODEL_POLICY: Mapping[tuple[RiskLevel, ComplexityLevel], DeveloperModelDecision] = (
    MappingProxyType(
        {
            (RiskLevel.LOW, ComplexityLevel.TRIVIAL): DeveloperModelDecision(_STANDARD, _MEDIUM),
            (RiskLevel.LOW, ComplexityLevel.LOW): DeveloperModelDecision(_STANDARD, _MEDIUM),
            (RiskLevel.LOW, ComplexityLevel.MEDIUM): DeveloperModelDecision(_STANDARD, _MEDIUM),
            (RiskLevel.LOW, ComplexityLevel.HIGH): DeveloperModelDecision(_STRONG, _MEDIUM),
            (RiskLevel.MEDIUM, ComplexityLevel.TRIVIAL): DeveloperModelDecision(_STANDARD, _MEDIUM),
            (RiskLevel.MEDIUM, ComplexityLevel.LOW): DeveloperModelDecision(_STANDARD, _MEDIUM),
            (RiskLevel.MEDIUM, ComplexityLevel.MEDIUM): DeveloperModelDecision(_STANDARD, _HIGH),
            (RiskLevel.MEDIUM, ComplexityLevel.HIGH): DeveloperModelDecision(_STRONG, _MEDIUM),
            (RiskLevel.HIGH, ComplexityLevel.TRIVIAL): DeveloperModelDecision(_STANDARD, _HIGH),
            (RiskLevel.HIGH, ComplexityLevel.LOW): DeveloperModelDecision(_STANDARD, _HIGH),
            (RiskLevel.HIGH, ComplexityLevel.MEDIUM): DeveloperModelDecision(_STRONG, _MEDIUM),
            (RiskLevel.HIGH, ComplexityLevel.HIGH): DeveloperModelDecision(_STRONG, _HIGH),
        }
    )
)

# A tabela cobre o produto inteiro, sem célula a mais nem a menos. Conferido na importação
# para que um enum novo em `db/enums.py` quebre o carregamento em vez de cair num default.
if set(DEVELOPER_MODEL_POLICY) != {(r, c) for r in RiskLevel for c in ComplexityLevel}:
    raise ModelRoutingContractError("a política do Model Router não cobre risk x complexity")


def route_developer_model(risk: RiskLevel, complexity: ComplexityLevel) -> DeveloperModelDecision:
    """Aplica a tabela. **Sem LLM, sem IO, sem rede, sem default implícito.**"""
    if type(risk) is not RiskLevel:
        raise ModelRoutingContractError(f"risk fora do contrato: {risk!r}")
    if type(complexity) is not ComplexityLevel:
        raise ModelRoutingContractError(f"complexity fora do contrato: {complexity!r}")

    decision = DEVELOPER_MODEL_POLICY.get((risk, complexity))
    if not isinstance(decision, DeveloperModelDecision):
        raise ModelRoutingContractError(f"sem célula para ({risk.value}, {complexity.value})")
    return decision


__all__ = [
    "DEVELOPER_MODEL_POLICY",
    "DeveloperEffort",
    "DeveloperModelDecision",
    "DeveloperModelTier",
    "ModelRoutingContractError",
    "route_developer_model",
]
