"""Router do Workspace Registry — `/api/workspaces`.

[01](../../../docs/architecture/01-v1-architecture.md) §2–§3: esta é a **única** camada
desta feature que conhece FastAPI/HTTP. Toda a lógica vive em `app.workspace`; aqui só há
validação de forma (Pydantic), abertura de sessão e tradução de erro de domínio em status.

[06](../../../docs/architecture/06-api-and-ui-boundaries.md) §2, rotas desta fase:

| Método | Rota | Papel |
| --- | --- | --- |
| `GET` `POST` | `/api/workspaces` | Listar e criar |
| `GET` `PATCH` | `/api/workspaces/{id}` | Detalhe; `PATCH` alterna `active ⇄ archived` |
| `GET` | `/api/workspaces/{id}/git` | Preflight: é repo, HEAD, branch, divergência |

Não existe `DELETE` ([06] §2, nota 1B.3): arquivar é `PATCH` de `status`; purgar é
operação própria (sub-etapa 4). A autenticação por `LocalSessionToken` é herdada do
middleware de `app.main` — nenhuma rota aqui a reimplementa.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.db.enums import WorkspaceStatus, WorkspaceType
from app.db.models import DevWorkspace
from app.db.session import session_scope
from app.safety import redact
from app.workspace import (
    PurgeCounts,
    PurgeTokenStore,
    create_workspace,
    execute_purge,
    get_workspace,
    git_preflight,
    list_workspaces,
    purge_preview,
    update_workspace_status,
)

router = APIRouter(tags=["workspaces"])


# ------------------------------------------------------------------ schemas


class WorkspaceCreate(BaseModel):
    """Corpo de `POST /api/workspaces`. `extra="forbid"` como em `health.py`."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    type: WorkspaceType
    local_path: str = Field(min_length=1, max_length=4096)
    linked_project_id: str | None = Field(default=None, max_length=64)
    repository_url: str | None = Field(default=None, max_length=2048)
    default_branch: str | None = Field(default=None, max_length=255)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name não pode ser vazio")
        return stripped

    @field_validator("linked_project_id", "repository_url", "default_branch")
    @classmethod
    def _empty_string_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class WorkspaceResponse(BaseModel):
    """Projeção de leitura de um `DevWorkspace` ([02] §1)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: WorkspaceType
    local_path: str
    linked_project_id: str | None
    repository_url: str | None
    default_branch: str | None
    status: WorkspaceStatus
    created_at: str
    updated_at: str


class WorkspaceStatusUpdate(BaseModel):
    """Corpo de `PATCH /api/workspaces/{id}` — só o `status` alterna ([02] §1)."""

    model_config = ConfigDict(extra="forbid")

    status: WorkspaceStatus


class GitPreflightResponse(BaseModel):
    """Resposta de `GET /api/workspaces/{id}/git` ([06] §2)."""

    model_config = ConfigDict(extra="forbid")

    is_git_repo: bool
    head: str | None
    branch: str | None
    dirty_file_count: int | None


class PurgePreviewResponse(BaseModel):
    """Resposta de `GET /api/workspaces/{id}/purge-preview` ([02] §11).

    As 6 contagens do que a purga removeria, o `purge_token` de curta duração que o
    `POST .../purge` vai exigir, e `benchmark_protected` — `True` quando a regra 6 de §11
    impede a purga (o grupo de benchmark já tem avaliação registrada). Sem prévia não há
    token, e sem token não há purga.
    """

    model_config = ConfigDict(extra="forbid")

    workspaces: int
    tasks: int
    runs: int
    findings: int
    manifests: int
    artifacts: int
    benchmark_protected: bool
    purge_token: str


class PurgeRequest(BaseModel):
    """Corpo de `POST /api/workspaces/{id}/purge` — o token emitido pela prévia."""

    model_config = ConfigDict(extra="forbid")

    purge_token: str = Field(min_length=1)


class PurgeResultResponse(BaseModel):
    """O que a purga efetivamente removeu ([02] §11: mesmas 6 chaves)."""

    model_config = ConfigDict(extra="forbid")

    workspaces: int
    tasks: int
    runs: int
    findings: int
    manifests: int
    artifacts: int


# -------------------------------------------------------------- dependência


def _session(request: Request) -> Iterator[Session]:
    """Unidade de trabalho por requisição: commit no sucesso, rollback em exceção.

    A exceção de domínio (`WorkspaceError`) atravessa este `yield`, dispara o rollback de
    `session_scope` e segue para o handler registrado em `app.main`.
    """
    with session_scope(request.app.state.session_factory) as session:
        yield session


def _purge_store(request: Request) -> PurgeTokenStore:
    store: PurgeTokenStore = request.app.state.purge_token_store
    return store


#: `Annotated` em vez de `= Depends(...)` no default: evita o B008 do bugbear e é o
#: estilo recomendado do FastAPI atual.
SessionDep = Annotated[Session, Depends(_session)]
PurgeStoreDep = Annotated[PurgeTokenStore, Depends(_purge_store)]


def _counts_payload(counts: PurgeCounts) -> dict[str, int]:
    return {
        "workspaces": counts.workspaces,
        "tasks": counts.tasks,
        "runs": counts.runs,
        "findings": counts.findings,
        "manifests": counts.manifests,
        "artifacts": counts.artifacts,
    }


def _to_response(workspace: DevWorkspace) -> WorkspaceResponse:
    """Serializa redigindo `repository_url` ([01] §4: remote com credencial não vaza)."""
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        type=workspace.type,
        local_path=workspace.local_path,
        linked_project_id=workspace.linked_project_id,
        repository_url=redact(workspace.repository_url) if workspace.repository_url else None,
        default_branch=workspace.default_branch,
        status=workspace.status,
        created_at=workspace.created_at.isoformat(),
        updated_at=workspace.updated_at.isoformat(),
    )


# ------------------------------------------------------------------- rotas


@router.post(
    "/workspaces",
    response_model=WorkspaceResponse,
    status_code=201,
    summary="Registrar um DevWorkspace",
)
def create(payload: WorkspaceCreate, session: SessionDep) -> WorkspaceResponse:
    workspace = create_workspace(
        session,
        name=payload.name,
        workspace_type=payload.type,
        local_path=payload.local_path,
        linked_project_id=payload.linked_project_id,
        repository_url=payload.repository_url,
        default_branch=payload.default_branch,
    )
    return _to_response(workspace)


@router.get("/workspaces", response_model=list[WorkspaceResponse], summary="Listar workspaces")
def index(
    session: SessionDep,
    status: WorkspaceStatus | None = None,
) -> list[WorkspaceResponse]:
    return [_to_response(workspace) for workspace in list_workspaces(session, status=status)]


@router.get(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceResponse,
    summary="Detalhe de um workspace",
)
def show(workspace_id: str, session: SessionDep) -> WorkspaceResponse:
    return _to_response(get_workspace(session, workspace_id))


@router.patch(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceResponse,
    summary="Arquivar ou reativar (active ⇄ archived)",
)
def patch(
    workspace_id: str,
    payload: WorkspaceStatusUpdate,
    session: SessionDep,
) -> WorkspaceResponse:
    return _to_response(update_workspace_status(session, workspace_id, payload.status))


@router.get(
    "/workspaces/{workspace_id}/git",
    response_model=GitPreflightResponse,
    summary="Git preflight (só leitura)",
)
def git(workspace_id: str, session: SessionDep) -> GitPreflightResponse:
    result = git_preflight(session, workspace_id)
    return GitPreflightResponse(
        is_git_repo=result.is_git_repo,
        head=result.head,
        branch=result.branch,
        dirty_file_count=result.dirty_file_count,
    )


@router.get(
    "/workspaces/{workspace_id}/purge-preview",
    response_model=PurgePreviewResponse,
    summary="Contagens da purga e emissão do purge_token",
)
def purge_preview_route(
    workspace_id: str, session: SessionDep, store: PurgeStoreDep
) -> PurgePreviewResponse:
    counts = purge_preview(session, workspace_id)
    return PurgePreviewResponse(
        **_counts_payload(counts),
        benchmark_protected=counts.benchmark_protected,
        purge_token=store.issue(workspace_id),
    )


@router.post(
    "/workspaces/{workspace_id}/purge",
    response_model=PurgeResultResponse,
    summary="Purga destrutiva; exige purge_token de uma prévia recente",
)
def purge_route(
    workspace_id: str,
    payload: PurgeRequest,
    session: SessionDep,
    store: PurgeStoreDep,
) -> PurgeResultResponse:
    counts = execute_purge(session, store, workspace_id, payload.purge_token)
    return PurgeResultResponse(**_counts_payload(counts))
