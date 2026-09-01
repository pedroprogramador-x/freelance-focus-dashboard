"""Composition root da aplicação.

É o **único** lugar que conhece implementações concretas
([01](../../docs/architecture/01-v1-architecture.md) §3). Na E2 há pouco a compor —
nenhum provider, nenhuma `ToolExecutorFactory` — mas a fronteira já está no lugar para
que E7/E8 injetem interfaces aqui, e não dentro do Orchestrator.

Superfície HTTP desta fase, e nada além:

| Rota | Autenticação |
| --- | --- |
| `GET /api/health` | **pública** — a única, e só neste método |
| qualquer outro método em `/api/health` | `Authorization: Bearer <LocalSessionToken>` |
| qualquer outra `/api/*` | `Authorization: Bearer <LocalSessionToken>` |
| `GET /` | pública por desenho: é o canal que **entrega** o token |
| `/assets/*` | estática, montada só quando há build compilado |

OpenAPI, Swagger e ReDoc estão **desligados**. Eles seriam superfícies públicas
adicionais, e [06] §1 admite exatamente uma.

Fora do escopo da E2, deliberadamente: Workspace Registry (E3), Context Registry (E4–E5),
Orchestrator (E6), `ToolExecutor` e worktrees (E7), providers (E8+), streaming SSE (E11).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import health, web
from app.api.security import (
    extract_bearer_token,
    generate_session_token,
    host_is_local,
    requires_session_token,
    token_is_valid,
)
from app.config import AppSettings, get_settings
from app.safety import redact


async def _unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Erro interno vira `{code, message}` estável.

    Nunca devolve *stack trace*, caminho local ou mensagem crua de exceção: qualquer um
    dos três entrega ao browser informação que [01] §4 mantém do lado do backend. O texto
    ainda passa pelo redator, por garantia.
    """
    del exc  # o detalhe fica no log do servidor, não na resposta
    return JSONResponse(
        status_code=500,
        content={"code": "internal_error", "message": redact("erro interno do backend")},
    )


def create_app(settings: AppSettings | None = None) -> FastAPI:
    active = settings or get_settings()

    app = FastAPI(
        title=active.app_name,
        version=__version__,
        # As três desligadas: cada uma seria uma rota pública a mais, e a arquitetura
        # admite uma só. Proteger o OpenAPI seria pior — viraria uma exceção de
        # autenticação a manter.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.state.settings = active
    #: Efêmero, só em memória, novo a cada `create_app` — logo, a cada reinício.
    app.state.session_token = generate_session_token()

    @app.middleware("http")
    async def local_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Validação de `Host` e do `LocalSessionToken`, por prefixo de caminho.

        Middleware, e não dependência por rota, de propósito: uma rota nova sob `/api/`
        fica protegida sem que ninguém precise lembrar de anotá-la. Fail-closed por
        construção.
        """
        if not host_is_local(request.headers.get("host")):
            return JSONResponse(
                status_code=400,
                content={"code": "invalid_host", "message": "host não permitido"},
            )

        if requires_session_token(request.method, request.url.path):
            presented = extract_bearer_token(request.headers.get("authorization"))
            if not token_is_valid(presented, request.app.state.session_token):
                # Sem eco do que foi apresentado e sem pista do valor esperado.
                return JSONResponse(
                    status_code=401,
                    content={
                        "code": "unauthorized",
                        "message": "token de sessão local ausente ou inválido",
                    },
                )

        return await call_next(request)

    app.include_router(health.router, prefix="/api")
    app.include_router(web.router)

    assets_dir = active.web_assets_dir
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    app.add_exception_handler(Exception, _unhandled_exception_handler)
    return app


#: Alvo do `uvicorn app.main:app`. Criar o app não cria diretório nem banco.
app = create_app()
