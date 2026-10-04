"""`MediatedToolsFacade` (E7.5-A): o que o provider recebe de `mediated_tools()`.

Superfície pública **funcional**: só `execute(request)`. Não carrega `usage`, `close`,
`mediated_tools`, journal, workspace, perfil nem raiz como API normal.

**Não é sandbox.** Python não esconde atributos: código hostil rodando no mesmo processo
alcançaria o executor por reflexão (`__closure__`, `gc`…). A facade existe para que um
adaptador **correto** não encontre, nem use por acidente, métodos de autoridade — a V1 não
promete contenção contra código Python hostil no mesmo processo ([04] §6).
"""

from __future__ import annotations

from collections.abc import Callable

from app.tool_executor.contracts import ToolRequest, ToolResult


class MediatedToolsFacade:
    __slots__ = ("_call",)

    def __init__(self, call: Callable[[ToolRequest], ToolResult]) -> None:
        self._call = call

    def execute(self, request: ToolRequest) -> ToolResult:
        return self._call(request)

    def __repr__(self) -> str:
        return "<MediatedTools>"


__all__ = ["MediatedToolsFacade"]
