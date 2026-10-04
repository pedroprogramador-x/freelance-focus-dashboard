"""Contrato de binding do Developer — E8.1.

Converte a decisão **neutra** do Model Router (`DeveloperModelDecision`) no
`developer_binding` de [02] §7 — `{adapter, adapter_version, model}` — sem que o
Orchestrator conheça um único nome concreto.

## Quem sabe o quê

* o Orchestrator conhece o **contrato**: `DeveloperBinding` (validado, imutável) e a
  porta `DeveloperBindingResolver` (`Protocol`, injetada — [01] §2: o Orchestrator
  "recebe por injeção, não os procura");
* a implementação concreta (tier → adaptador/versão/modelo) pertence ao composition root
  e chega na E8.2. Na E8.1 só testes fornecem resolvers.

## O resolver recebe só o tier

`resolve_developer_binding` recebe a decisão inteira, mas entrega à porta **apenas o
tier**. É isso que torna estruturalmente impossível um resolver codificar o effort no
`model` (`"…-high"`): ele nem o vê. Model e effort são campos independentes do
fingerprint — o binding em `developer_binding`, o effort em `execution_limits`.

## Sem resolver, sem binding

`select_developer_execution(..., resolver=None)` devolve `binding=None`, que o Planner
serializa como `developer_binding: null` — o comportamento anterior à E8, preservado até o
composition root injetar um resolver real. Nenhum fallback concreto existe aqui.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.db.enums import ComplexityLevel, RiskLevel
from app.orchestrator.model_router import (
    DeveloperModelDecision,
    DeveloperModelTier,
    route_developer_model,
)

#: Teto de tamanho de cada campo do binding. Identidades de adaptador/modelo são curtas;
#: um valor enorme é sinal de dado errado, não de configuração legítima.
_MAX_FIELD_LENGTH = 200


class DeveloperBindingContractError(ValueError):
    """O resolver devolveu algo fora do contrato. É defeito de configuração, não decisão."""


def _field(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise DeveloperBindingContractError(f"`{name}` precisa ser str, veio {type(value)}")
    if not value or value != value.strip():
        raise DeveloperBindingContractError(f"`{name}` vazio ou com espaço nas bordas")
    if len(value) > _MAX_FIELD_LENGTH:
        raise DeveloperBindingContractError(f"`{name}` excede {_MAX_FIELD_LENGTH} caracteres")
    if not value.isprintable():
        raise DeveloperBindingContractError(f"`{name}` contém caractere não imprimível")
    return value


@dataclass(frozen=True, slots=True)
class DeveloperBinding:
    """O `developer_binding` de [02] §7, validado. A forma histórica, sem campo a mais."""

    adapter: str
    adapter_version: str
    model: str

    def __post_init__(self) -> None:
        _field("adapter", self.adapter)
        _field("adapter_version", self.adapter_version)
        _field("model", self.model)

    def as_canonical(self) -> dict[str, str]:
        return {
            "adapter": self.adapter,
            "adapter_version": self.adapter_version,
            "model": self.model,
        }


class DeveloperBindingResolver(Protocol):
    """Porta: tier neutro → binding concreto. Implementação concreta só no composition root."""

    def resolve(self, tier: DeveloperModelTier) -> DeveloperBinding: ...


def resolve_developer_binding(
    decision: DeveloperModelDecision, resolver: DeveloperBindingResolver
) -> DeveloperBinding:
    """Resolve o binding da decisão. Só o tier atravessa para o resolver (ver o módulo)."""
    if not isinstance(decision, DeveloperModelDecision):
        raise DeveloperBindingContractError(f"decisão fora do contrato: {decision!r}")
    binding = resolver.resolve(decision.tier)
    if not isinstance(binding, DeveloperBinding):
        raise DeveloperBindingContractError(
            f"o resolver precisa devolver `DeveloperBinding`, devolveu {type(binding)}"
        )
    return binding


@dataclass(frozen=True, slots=True)
class DeveloperExecutionSelection:
    """Decisão neutra + binding concreto (ou `None` sem resolver). Imutável."""

    decision: DeveloperModelDecision
    binding: DeveloperBinding | None


def select_developer_execution(
    risk: RiskLevel,
    complexity: ComplexityLevel,
    *,
    resolver: DeveloperBindingResolver | None,
) -> DeveloperExecutionSelection:
    """O **único** caminho decisão → binding, usado por plano, `approve` e guarda de entrada."""
    decision = route_developer_model(risk, complexity)
    binding = None if resolver is None else resolve_developer_binding(decision, resolver)
    return DeveloperExecutionSelection(decision=decision, binding=binding)


__all__ = [
    "DeveloperBinding",
    "DeveloperBindingContractError",
    "DeveloperBindingResolver",
    "DeveloperExecutionSelection",
    "resolve_developer_binding",
    "select_developer_execution",
]
