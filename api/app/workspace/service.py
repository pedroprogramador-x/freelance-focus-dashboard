"""Serviço do Workspace Registry — CRUD de `DevWorkspace` e validação de `local_path`.

[01](../../../docs/architecture/01-v1-architecture.md) §2, contrato de `workspace/`:
pode importar `db`, `safety`, `path_runtime`, `git_runtime`, `config`; **não** pode
importar `api`, `orchestrator`, `agent_runtime`, `tool_executor`. Toda a lógica de
negócio da feature vive aqui — o router é só transporte.

Escopo desta unidade (E3, sub-etapa 2): `create_workspace`, `list_workspaces`,
`get_workspace`, `update_workspace_status`. Purga e prévia de purga entram na sub-etapa 4
(`app.workspace.purge`), arquivamento é apenas um caso de `update_workspace_status`.

Validação de `local_path` ([02] §1: "canonizado via path_runtime, existente, diretório e
único; decidido por safety na criação"):

1. `safety.prevalidate_path_syntax` — rejeita sintaxe perigosa antes de qualquer IO;
2. `path_runtime.inspect(local_path, root=local_path, allow_absolute=True)` — coleta fatos;
3. `safety.decide_path` — decide sobre os fatos;
4. o alvo canônico precisa **existir** e ser **diretório**.

Qualquer recusa vira `InvalidLocalPath` (→ 422). A unicidade é da constraint
`uq_dev_workspace_local_path` (E2); a violação vira `DuplicateLocalPath` (→ 409) antes de
escapar como `IntegrityError`.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.enums import WorkspaceStatus, WorkspaceType
from app.db.models import DevWorkspace
from app.git_runtime import GitPreflight
from app.git_runtime import preflight as _git_preflight_read
from app.path_runtime import inspect
from app.safety import SafetyPolicy, decide_path, prevalidate_path_syntax
from app.workspace.errors import (
    DuplicateLocalPath,
    InvalidLocalPath,
    InvalidStatusTransition,
    InvalidWorkspaceName,
    WorkspaceNotFound,
)

#: [02] §1: `name` "não vazio, ≤ 120". Invariante de domínio, não só da borda HTTP.
_MAX_WORKSPACE_NAME_LEN = 120


def validate_local_path(local_path: str, *, policy: SafetyPolicy | None = None) -> str:
    """Canoniza e valida `local_path`. Devolve o caminho canônico; levanta `InvalidLocalPath`.

    O `root` é o próprio `local_path`: um `DevWorkspace` **é** a raiz do seu workspace, não
    um caminho relativo a outra. `allow_absolute=True` porque é o caminho que o usuário
    digitou — a única exceção à regra "tudo é relativo ao workspace" ([06] §2).

    Limitação conhecida do Path Runtime da E2: `inspect()` percorre a cadeia **léxica** de
    `requested` a partir de `root`; quando `requested == root` e ambos são absolutos, essa
    cadeia não existe (`root / "C:" / "Users" / ...`), e `is_symlink/junction/reparse_point`
    voltam `UNKNOWN` — não porque a verificação falhou, mas porque não há cadeia relativa a
    inspecionar. Com a policy padrão (`require_verified_link_status=True`) isso faria
    `decide_path` negar **todo** diretório absoluto válido como `path.symlink_unverified`.
    A validação do alvo-raiz roda com essa checagem desligada; UNC, device namespace,
    drive-relative, contenção, cross-volume e a política de segredos seguem valendo, e
    `prevalidate_path_syntax` (com a policy cheia) cobre `..`, nomes reservados, ADS, alias
    8.3 e `~`. Ver AGENT_LOG: recomendável um follow-up ensinando `_lexical_chain_facts` a
    tratar o caso absoluto.
    """
    active = policy or SafetyPolicy()

    syntax = prevalidate_path_syntax(local_path, policy=active, allow_absolute=True)
    if not syntax.allow:
        raise InvalidLocalPath(syntax.reason, rule_id=syntax.rule_id)

    facts = inspect(local_path, root=local_path, allow_absolute=True)

    # Mensagem enxuta e sem eco do erro cru do SO: caminho inexistente ou inacessível.
    if facts.inspection_error is not None or not facts.exists or facts.canonical_target is None:
        raise InvalidLocalPath(
            "local_path não existe ou não é acessível", rule_id="workspace.path_missing"
        )

    # TODO(E3-AUD-001, antes de E4/E7): `path_runtime._lexical_chain_facts` precisa
    # aprender a percorrer a cadeia de um caminho-raiz absoluto. Enquanto isso não existe,
    # a verificação de link do alvo-raiz fica desligada aqui (ver docstring). Escopo
    # confirmado como correto pela auditoria via `dataclasses.replace`; a correção de
    # verdade é do Path Runtime, não desta função.
    root_probe_policy = replace(active, require_verified_link_status=False)
    decision = decide_path(facts, policy=root_probe_policy)
    if not decision.allow:
        raise InvalidLocalPath(decision.reason, rule_id=decision.rule_id)

    if not Path(facts.canonical_target).is_dir():
        raise InvalidLocalPath("local_path não é um diretório", rule_id="workspace.path_not_dir")

    return facts.canonical_target


def create_workspace(
    session: Session,
    *,
    name: str,
    workspace_type: WorkspaceType,
    local_path: str,
    linked_project_id: str | None = None,
    repository_url: str | None = None,
    default_branch: str | None = None,
    policy: SafetyPolicy | None = None,
) -> DevWorkspace:
    """Cria um `DevWorkspace`. `linked_project_id` é string opaca — nunca resolvida ([ADR-0003])."""
    clean_name = name.strip()
    if not clean_name or len(clean_name) > _MAX_WORKSPACE_NAME_LEN:
        raise InvalidWorkspaceName(
            f"name deve ser não vazio e ter no máximo {_MAX_WORKSPACE_NAME_LEN} caracteres"
        )

    canonical_path = validate_local_path(local_path, policy=policy)

    workspace = DevWorkspace(
        name=clean_name,
        type=workspace_type,
        local_path=canonical_path,
        linked_project_id=linked_project_id,
        repository_url=repository_url,
        default_branch=default_branch,
        status=WorkspaceStatus.ACTIVE,
    )
    session.add(workspace)
    try:
        session.flush()
    except IntegrityError as error:
        if _is_local_path_unique_violation(error):
            raise DuplicateLocalPath(
                "já existe um workspace registrado neste local_path"
            ) from error
        raise

    return workspace


def list_workspaces(
    session: Session, *, status: WorkspaceStatus | None = None
) -> list[DevWorkspace]:
    """Lista workspaces, mais recentes primeiro. `status` opcional filtra active/archived."""
    query = select(DevWorkspace).order_by(DevWorkspace.created_at.desc(), DevWorkspace.id)
    if status is not None:
        query = query.where(DevWorkspace.status == status)
    return list(session.scalars(query))


def get_workspace(session: Session, workspace_id: str) -> DevWorkspace:
    """Busca por `id`. Levanta `WorkspaceNotFound` se não existir."""
    workspace = session.get(DevWorkspace, workspace_id)
    if workspace is None:
        raise WorkspaceNotFound(f"workspace '{workspace_id}' não encontrado")
    return workspace


def update_workspace_status(
    session: Session, workspace_id: str, new_status: WorkspaceStatus
) -> DevWorkspace:
    """Alterna `status` entre `active` e `archived` ([02] §1). Pedir o estado atual é 409."""
    workspace = get_workspace(session, workspace_id)

    if workspace.status == new_status:
        raise InvalidStatusTransition(f"workspace já está em '{new_status.value}'")

    workspace.status = new_status
    workspace.updated_at = utcnow()
    session.flush()
    return workspace


def git_preflight(session: Session, workspace_id: str) -> GitPreflight:
    """Preflight de Git do `local_path` do workspace ([06] §2, `GET /workspaces/{id}/git`).

    A camada API não pode importar `git_runtime` ([01] §3); a orquestração — carregar o
    workspace, ler o disco — é lógica de `workspace/`. Só leitura, nunca lança.
    """
    workspace = get_workspace(session, workspace_id)
    return _git_preflight_read(workspace.local_path)


def _is_local_path_unique_violation(error: IntegrityError) -> bool:
    """Distingue a violação de `uq_dev_workspace_local_path` de outras `IntegrityError`.

    Uma CHECK que falhou (`name_not_blank`, etc.) também é `IntegrityError`, mas é bug de
    chamador, não conflito de recurso — essa deve continuar subindo crua.
    """
    detail = str(getattr(error, "orig", error)).lower()
    return "unique" in detail and "local_path" in detail
