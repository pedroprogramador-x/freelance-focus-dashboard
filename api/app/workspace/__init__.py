"""Agregado `DevWorkspace` — CRUD, validação de `local_path`, arquivamento e purga.

[01](../../../docs/architecture/01-v1-architecture.md) §2. Importa `db`, `safety`,
`path_runtime`, `git_runtime`, `config`; nunca `api`, `orchestrator`, `agent_runtime`,
`tool_executor`. A camada HTTP (`app.api.workspaces`) é a única que conhece FastAPI.
"""

from app.workspace.errors import (
    DuplicateLocalPath,
    InvalidLocalPath,
    InvalidStatusTransition,
    InvalidWorkspaceName,
    PurgeTokenRejected,
    WorkspaceBenchmarkProtected,
    WorkspaceError,
    WorkspaceNotFound,
    WorkspacePurgeBlocked,
)
from app.workspace.purge import PurgeCounts, execute_purge, purge_preview
from app.workspace.purge_tokens import PurgeTokenStore
from app.workspace.service import (
    create_workspace,
    get_workspace,
    git_preflight,
    list_workspaces,
    update_workspace_status,
    validate_local_path,
)

__all__ = [
    "DuplicateLocalPath",
    "InvalidLocalPath",
    "InvalidStatusTransition",
    "InvalidWorkspaceName",
    "PurgeCounts",
    "PurgeTokenRejected",
    "PurgeTokenStore",
    "WorkspaceBenchmarkProtected",
    "WorkspaceError",
    "WorkspaceNotFound",
    "WorkspacePurgeBlocked",
    "create_workspace",
    "execute_purge",
    "get_workspace",
    "git_preflight",
    "list_workspaces",
    "purge_preview",
    "update_workspace_status",
    "validate_local_path",
]
