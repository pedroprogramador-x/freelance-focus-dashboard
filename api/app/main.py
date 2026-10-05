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

É aqui também que o **boundary de saída** de [04] §5 é instalado, e em dois lugares que
precisam concordar: `default_response_class` cobre as rotas, e cada `exception_handler`
cobre o caminho de erro — inclusive o `422` do FastAPI, que sem um handler nosso devolveria
o valor recusado dentro de `detail[].input` (E6-AUD3-001). Um dos dois sem o outro deixa
metade das respostas fora da garantia.

Fora do escopo até aqui, deliberadamente: Context Router e file map (E5), Orchestrator
(E6), `ToolExecutor` e worktrees (E7), providers (E8+), streaming SSE (E11).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api import context, health, tasks, web, workspaces
from app.api.responses import RedactingJSONResponse
from app.api.security import (
    extract_bearer_token,
    generate_session_token,
    host_is_local,
    is_api_path,
    requires_session_token,
    same_origin_write_allowed,
    token_is_valid,
)
from app.config import AppSettings, get_settings
from app.context_engine import ContextError
from app.db.conflicts import WriteConflict
from app.db.session import create_engine, create_session_factory, session_scope
from app.developer_wiring import AnthropicDeveloperBindingResolver
from app.orchestrator import OrchestratorError, reconcile_on_startup
from app.workspace import PurgeTokenStore, WorkspaceError


async def _unhandled_exception_handler(_request: Request, exc: Exception) -> Response:
    """Erro interno vira `{code, message}` estável.

    Nunca devolve *stack trace*, caminho local ou mensagem crua de exceção: qualquer um
    dos três entrega ao browser informação que [01] §4 mantém do lado do backend. O texto
    é constante e não vem de lugar nenhum — e ainda assim sai pelo boundary de redação,
    porque a regra de [04] §5 é "toda resposta", sem exceção para conteúdo que o autor
    julga seguro.
    """
    del exc  # o detalhe fica no log do servidor, não na resposta
    return RedactingJSONResponse(
        status_code=500,
        content={"code": "internal_error", "message": "erro interno do backend"},
    )


async def _validation_error_handler(_request: Request, exc: Exception) -> Response:
    """`422` de validação de forma, **pelo boundary de redação** (E6-AUD3-001).

    O handler padrão do FastAPI devolve `detail[]` com `input` — o valor que o cliente
    mandou e que o schema recusou. Um `name` longo demais, um campo extra, um `title` fora
    do limite: o corpo de erro ecoa o dado cru de volta, e era a única superfície de `/api/*`
    que nem sequer passava por uma projeção nossa.

    A forma do `detail` do FastAPI é preservada — o cliente que já a interpreta continua
    funcionando —, e `code`/`message` são acrescentados para que a resposta tenha o mesmo
    par estável de [06] §2 que todo outro erro desta API tem.

    `exc.errors()` pode carregar objetos que não são JSON (o `ValueError` original em
    `ctx.error`, por exemplo). `jsonable_encoder` os normaliza **antes** da redação, para
    que a caminhada receba um documento e não uma exceção embrulhada.
    """
    assert isinstance(exc, RequestValidationError)
    return RedactingJSONResponse(
        status_code=422,
        content={
            "code": "validation_error",
            "message": "corpo ou parâmetro inválido",
            "detail": jsonable_encoder(exc.errors()),
        },
    )


#: Status em que o HTTP proíbe corpo. O handler devolve `Response` cru para eles — um
#: JSON aqui viraria `null` no *wire*, que é justamente o que o status recusa.
_SEM_CORPO = frozenset({204, 304})


async def _http_exception_handler(_request: Request, exc: Exception) -> Response:
    """`HTTPException` do framework — `404` de rota, `405` de método — **pelo boundary**.

    O handler embutido do Starlette constrói um `JSONResponse` cru, e ele é o caminho de
    todo erro que nasce **antes** de qualquer rota nossa: caminho inexistente, método não
    permitido, e qualquer `HTTPException` que uma dependência levante. Era a última saída
    JSON de `/api/*` fora da Camada 3 de [04] §5 (E6-AUD4-003) — a garantia de que "toda
    resposta nasce redigida" tinha uma porta aberta no ponto em que o `detail` pode carregar
    texto vindo da requisição.

    `status_code` e `headers` são preservados **verbatim**: o `Allow` que acompanha um `405`
    e o `WWW-Authenticate` de um `401` fazem parte do contrato HTTP, e engoli-los quebraria
    o roteamento em vez de protegê-lo.

    A forma do corpo segue o resto da API: o par estável `{code, message}` de [06] §2 mais o
    `detail` do framework, para o cliente que já o interpreta.
    """
    assert isinstance(exc, StarletteHTTPException)

    if exc.status_code in _SEM_CORPO:
        return Response(status_code=exc.status_code, headers=exc.headers)

    detalhe = jsonable_encoder(exc.detail)
    return RedactingJSONResponse(
        status_code=exc.status_code,
        content={
            "code": "http_error",
            "message": detalhe if isinstance(detalhe, str) else "requisição recusada",
            "detail": detalhe,
        },
        headers=exc.headers,
    )


async def _domain_error_handler(_request: Request, exc: Exception) -> Response:
    """Erro de domínio → `{code, message}` no status que o domínio pediu ([06] §2).

    O router não conhece HTTP status de erro: levanta a exceção tipada, e a tradução mora
    aqui, no composition root. Toda mensagem passa pelo redator ([06] §2).

    Serve `WorkspaceError` (E3), `ContextError` (E4), `OrchestratorError` (E6) e
    `WriteConflict` (a tradução de conflito de escrita da unidade de trabalho) — as quatro
    hierarquias têm a mesma forma (`code`, `status_code`, `message`) de propósito, então um
    handler só basta e uma feature nova não precisa de um ramo de `isinstance` novo.

    `OrchestratorError` acrescenta `as_payload()`, e é por ele que [06] §2 consegue cumprir
    "`409` […] sempre com o motivo e **qual campo divergiu**": `diverged_fields` chega ao
    cliente como dado estruturado, não como texto dentro de `message`.

    Nada aqui chama o redator: `RedactingJSONResponse` o faz sobre o corpo inteiro, e um
    `redact` a mais neste ponto seria a redação distribuída que E6-AUD3-001 removeu —
    redigir duas vezes é inofensivo, mas deixa a dúvida sobre qual das duas é a garantia.
    """
    assert isinstance(exc, WorkspaceError | ContextError | OrchestratorError | WriteConflict)

    content: dict[str, object] = {"code": exc.code, "message": exc.message}
    if isinstance(exc, OrchestratorError):
        content.update(exc.as_payload())

    return RedactingJSONResponse(status_code=exc.status_code, content=content)


#: A tabela cuja ausência caracteriza "schema ainda não migrado". É a que a recuperação lê,
#: e existe desde a primeira migration — se ela não está lá, nenhuma task pode estar presa.
_RECOVERY_TABLE = "workspace_task"


def _schema_is_migrated(app: FastAPI) -> bool:
    """O banco já tem o schema que a recuperação precisa?

    Uma **pergunta**, não um `except` (E6-AUD-008). `except OperationalError: pass`
    descrevia uma classe genérica de falha de banco, não a condição esperada: um lock
    contendido, uma tabela corrompida e um gatilho que aborta o `UPDATE` produzem a mesma
    exceção que "a tabela não existe", e todos eram silenciados — a recuperação inteira era
    revertida e o servidor subia como se tivesse rodado. Perguntar antes deixa a única
    condição tolerável explícita e devolve **toda** falha operacional ao comportamento
    *fail closed*: ela sobe, o startup não completa, e o operador vê o que aconteceu em vez
    de descobrir depois que tasks presas nunca foram recuperadas.

    Um erro **aqui** não é tolerado e nem deveria ser: não conseguir nem inspecionar o banco
    (diretório inexistente, arquivo sem permissão, arquivo que não é um banco SQLite) é uma
    falha operacional real, distinta de "o arquivo está vazio porque é a primeira execução"
    — esse caso o SQLite resolve criando o arquivo, e a inspeção responde `False`.
    """
    return bool(inspect(app.state.db_engine).has_table(_RECOVERY_TABLE))


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """[ADR-0008] regra 6: tasks presas em `planning`/`executing` → `failed(interrupted)`.

    Roda no startup do **servidor**, não no `create_app`: criar o app continua sem efeito
    colateral (gate da E2), e é o ciclo de vida do processo que caracteriza "sem processo
    vivo". Como `max_parallel_agents = 1` e o backend é um processo só, toda task nesses
    estados no startup é, por definição, de um processo que já morreu.

    A **única** condição tolerada é o schema ainda não migrado — a primeira execução, antes
    de `alembic upgrade head`. Falhar o startup por isso impediria justamente o operador de
    chegar ao terminal para rodar a migration. Ela é reconhecida por inspeção
    (`_schema_is_migrated`), não capturando uma classe de exceção que também cobre falhas
    reais; qualquer erro durante a recuperação de um banco já migrado sobe.
    """
    if _schema_is_migrated(app):
        with session_scope(app.state.session_factory) as session:
            reconcile_on_startup(session)

    yield


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
        lifespan=_lifespan,
        # [04] §5, Camada 3: **toda** resposta JSON desta aplicação nasce redigida. Uma
        # rota nova não precisa lembrar de nada — é o default que a protege, e escapar
        # exige construir um `Unredacted`. Ver `api/responses.py`.
        default_response_class=RedactingJSONResponse,
    )

    app.state.settings = active
    #: Efêmero, só em memória, novo a cada `create_app` — logo, a cada reinício.
    app.state.session_token = generate_session_token()

    #: Engine e fábrica de sessão. Construir não toca o disco — só o primeiro `connect`
    #: o faz (ver `app.db.session`), então criar o app segue sem efeito colateral.
    app.state.db_engine = create_engine(active)
    app.state.session_factory = create_session_factory(app.state.db_engine)

    #: Confirmação forte da purga ([02] §11). Uma instância por app, só em memória.
    app.state.purge_token_store = PurgeTokenStore()

    #: E7.6 — a porta de verificação independente de capability ([04] §1, ADR-0009). **Sem
    #: implementação positiva em produção**: só um adaptador real (E8+) poderá preenchê-la.
    #: `None` faz a guarda `approved → executing` recusar (*fail closed*,
    #: `capability_profile_proven`). Nenhum fake/always-pass é instalado aqui — doubles
    #: positivos moram em `tests/`. Ver `app.capability_wiring` para a ponte.
    app.state.capability_verifier = None

    #: E8.2 — resolver concreto tier → binding do Developer (Claude Messages API,
    #: `transport="api"`). Só instancia um objeto sem estado: nenhum cliente HTTP, nenhuma
    #: credencial, nenhuma rede, nenhum diretório. `plan`
    #: e `approve` o recebem por `app.state` (a rota só conhece a porta neutra), de modo que
    #: o `developer_binding` do fingerprint é concreto e idêntico no recálculo. Não há rota
    #: nem laço de execução: `start_execution` segue levantando `NotImplementedError` (E8.4).
    app.state.developer_binding_resolver = AnthropicDeveloperBindingResolver()

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
            return RedactingJSONResponse(
                status_code=400,
                content={"code": "invalid_host", "message": "host não permitido"},
            )

        # [01] §4: requisições que alteram estado exigem mesma origem. Um POST/PATCH/PUT/
        # DELETE sob `/api/` é recusado (antes da verificação de token — aba maliciosa não
        # sonda a rota) quando o `Origin` não bate exatamente com a autoridade do `Host`
        # servido, ou o `Sec-Fetch-Site` não é `same-origin`/`none`.
        if is_api_path(request.url.path) and not same_origin_write_allowed(
            request.method,
            request.headers.get("origin"),
            request.headers.get("sec-fetch-site"),
            request.headers.get("host"),
            request_scheme=request.url.scheme,
        ):
            return RedactingJSONResponse(
                status_code=403,
                content={
                    "code": "cross_origin_denied",
                    "message": "origem não permitida para operação que altera estado",
                },
            )

        if requires_session_token(request.method, request.url.path):
            presented = extract_bearer_token(request.headers.get("authorization"))
            if not token_is_valid(presented, request.app.state.session_token):
                # Sem eco do que foi apresentado e sem pista do valor esperado.
                return RedactingJSONResponse(
                    status_code=401,
                    content={
                        "code": "unauthorized",
                        "message": "token de sessão local ausente ou inválido",
                    },
                )

        return await call_next(request)

    app.include_router(health.router, prefix="/api")
    app.include_router(workspaces.router, prefix="/api")
    app.include_router(context.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    app.include_router(web.router)

    assets_dir = active.web_assets_dir
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    app.add_exception_handler(WorkspaceError, _domain_error_handler)
    app.add_exception_handler(ContextError, _domain_error_handler)
    app.add_exception_handler(OrchestratorError, _domain_error_handler)
    app.add_exception_handler(WriteConflict, _domain_error_handler)
    # Sobrescreve o handler embutido do FastAPI, que devolveria `detail[].input` cru.
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    # E o do Starlette, que serve `404`/`405` de roteamento fora do boundary (E6-AUD4-003).
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
    return app


#: Alvo do `uvicorn app.main:app`. Criar o app não cria diretório nem banco.
app = create_app()
