"""`GET /api/health`.

[06](../../../docs/architecture/06-api-and-ui-boundaries.md) §1–§2: **única** rota não
autenticada, e devolve **informação mínima** — prontidão e versão. Sem caminho, sem
configuração, sem contagem, sem ambiente, sem nada que ajude a mapear a máquina.

Quando o `LocalSessionToken` entrar (E3), esta continua sendo a única rota aberta. Não
existe segunda.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Resposta mínima. `extra="forbid"` impede alguém acrescentar um campo por descuido."""

    model_config = ConfigDict(extra="forbid")

    status: str
    version: str


@router.get("/health", response_model=HealthResponse, summary="Prontidão do backend local")
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)
