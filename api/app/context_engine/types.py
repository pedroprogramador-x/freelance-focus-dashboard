"""Payload do *seed* de planejamento — o contrato de `POST /workspaces/{id}/context/import`.

[03](../../../docs/architecture/03-context-architecture.md) §1: "o **frontend** envia (…)
para `POST /api/workspaces/{id}/context/import`. **O backend não sabe de onde veio.**"

Isso é levado ao pé da letra aqui, e não é preciosismo de nomenclatura: [ADR-0002]
(persistência disjunta) e `test_architecture.py::test_backend_nao_conhece_o_dominio_
comercial` proíbem o backend de nomear qualquer entidade do domínio comercial. O contrato
é, portanto, uma estrutura **anônima** em `snake_case`, com os campos que o seed precisa —
não uma cópia com o nome da entidade que o frontend por acaso usa como origem. Quem traduz
do modelo comercial para este formato é o frontend, do lado dele.

O mapeamento para entradas do registry está em `service.import_planning_seed`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PlanningSeedDecision:
    """Uma decisão técnica. Vira **uma** entrada de domínio `decisions` ([03] §1)."""

    title: str
    decision: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class PlanningSeedRisk:
    """Um risco. Vira **uma** entrada de domínio `risks` ([03] §1)."""

    description: str
    mitigation: str = ""


@dataclass(frozen=True, slots=True)
class PlanningSeed:
    """O planejamento enviado pelo frontend, já sem nenhum vínculo com a origem dele."""

    problem: str = ""
    objective: str = ""
    functional_requirements: tuple[str, ...] = ()
    non_functional_requirements: tuple[str, ...] = ()
    stack: tuple[str, ...] = ()
    architecture: str = ""
    technical_decisions: tuple[PlanningSeedDecision, ...] = field(default_factory=tuple)
    risks: tuple[PlanningSeedRisk, ...] = field(default_factory=tuple)
