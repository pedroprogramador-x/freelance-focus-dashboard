"""Dispatcher **canônico** das nove *client tools* (E8.2).

`tool_use{name, input}` → lookup no mapping **fechado e imutável** → validação →
`ToolRequest` oficial → `MediatedTools.execute` → `ToolResult` → resultado curto para o
`tool_result`. Nada além disso:

* sem reflection, import dinâmico ou `getattr` por nome; nome desconhecido não é executado
  (`UnknownTool`, falha de protocolo — o provider aborta o run);
* sem IO: nenhum `open`, `Path`, `os`, `subprocess`, git, HTTP ou socket — o único efeito é o
  `MediatedTools` recebido do run;
* nada cru volta ao modelo: negação/erro são o `reason` já sanitizado pelo executor, e
  exceção técnica vira texto fixo (e encerra o run).

Há **uma** instância (`CANONICAL_DISPATCHER`), sem estado e com `__slots__` vazio. O
verificador exige *esta* instância e *este* tipo exato no bundle de runtime: um dispatcher
com a mesma superfície declarativa, mas outra implementação, não passa.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, final

from app.agent_runtime.adapters.anthropic_messages.constants import DISPATCHER_CONTRACT_VERSION
from app.agent_runtime.adapters.anthropic_messages.tool_surface import (
    SPEC_BY_NAME,
    TOOL_NAMES,
    InvalidToolInput,
    build_request,
)
from app.tool_executor.contracts import MediatedTools, ToolStatus

_GENERIC_ERROR: Final = "erro interno da ferramenta"


class UnknownTool(Exception):
    """`tool_use` com nome fora da superfície fechada: nunca é executado."""


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """O que volta ao modelo como `tool_result`. `technical_failure` encerra o run."""

    content: str
    is_error: bool
    technical_failure: bool = False


@final
class CanonicalToolDispatcher:
    """Sem estado. Use `CANONICAL_DISPATCHER`; não há motivo para outra instância. Final em
    runtime: uma subclasse (que poderia sobrescrever `dispatch`) é recusada na definição."""

    __slots__ = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("CanonicalToolDispatcher é final: subclasse recusada")

    contract_version: Final = DISPATCHER_CONTRACT_VERSION

    def tool_names(self) -> tuple[str, ...]:
        return TOOL_NAMES

    def dispatch(self, name: object, arguments: object, mediated: MediatedTools) -> DispatchOutcome:
        if not isinstance(name, str) or name not in SPEC_BY_NAME:
            raise UnknownTool
        spec = SPEC_BY_NAME[name]
        try:
            request = build_request(spec, arguments)
        except InvalidToolInput:
            return DispatchOutcome(f"entrada inválida para {spec.name}", is_error=True)
        try:
            result = mediated.execute(request)
        except Exception:  # fronteira: nada cru (stack, caminho, repr) volta ao modelo
            return DispatchOutcome(_GENERIC_ERROR, is_error=True, technical_failure=True)
        if result.status is ToolStatus.OK:
            return DispatchOutcome(result.content or "", is_error=False)
        prefix = "negado" if result.status is ToolStatus.DENIED else "erro"
        return DispatchOutcome(f"{prefix}: {result.reason}", is_error=True)


CANONICAL_DISPATCHER: Final = CanonicalToolDispatcher()
