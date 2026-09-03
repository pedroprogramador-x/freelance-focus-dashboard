"""Prévia e execução da purga de `DevWorkspace` ([02] §11).

[02](../../../docs/architecture/02-data-model.md) §11:

* **`archive`** é a operação normal e reversível — é só `update_workspace_status`.
* **`purge`** é destrutiva e explícita. Antes dela, **prévia obrigatória** com as
  contagens do que seria removido, e **confirmação forte** (o `PurgeTokenStore`).
* O workspace só pode ser purgado **arquivado e sem task não-terminal** (regra 1).
* O repositório do usuário **nunca** é tocado (regra 7) — a purga remove **linhas**.
* `SafetyEvent` não é apagado (regra 4) — não tem FK, sobrevive à purga.

Contagens da prévia, exatamente as 6 chaves de [02] §11:
`{ workspaces, tasks, runs, findings, manifests, artifacts }`.

`workspaces`, `tasks`, `runs`, `findings` e `manifests` são **consultas reais** — nesta
fase só `workspaces` é diferente de zero (as demais tabelas ainda não têm linha), mas a
contagem é feita de verdade para o código já nascer correto.

**`artifacts` é sempre `0`** (E3-AUD-006): a purga **não remove nenhum arquivo de disco**.
Os *Rendered Context Artifacts* ([02] §5) são blobs endereçados por conteúdo e só entram
em GC quando **nenhuma referência restante existir** ([02] §11 regra 5) — e esse GC ainda
não existe. Reportar um número não-zero aqui afirmaria uma remoção que não acontece. A
contagem passa a ser real (e o GC, implementado) junto com E5, quando manifests e blobs
começarem a existir.

**Proteção de dados de benchmark** ([02] §11 regra 6, risco R8) — **implementada nesta
fase** (E3-AUD2-005): as colunas `benchmark_group_id` / `Run.purpose` / `AuditFinding.
purpose` existem desde a E2, então "não há dado ainda" não garante risco zero e a regra é
congelada. `_has_benchmark_evaluation` verifica se alguma `WorkspaceTask` do workspace
compartilha o `benchmark_group_id` com um `Run` ou `AuditFinding` de
`purpose = benchmark_evaluation` — **inclusive em tasks de outro workspace do mesmo
grupo** (o grupo cruza workspaces e modos de execução, [02] §3). Se sim: a prévia devolve
`benchmark_protected=True` e `execute_purge` recusa com `WorkspaceBenchmarkProtected`
(409), mesmo com token válido.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.db.enums import FindingPurpose, RunPurpose, TaskStatus, WorkspaceStatus
from app.db.models import (
    AuditFinding,
    ContextManifest,
    DevWorkspace,
    Run,
    WorkspaceTask,
)
from app.workspace.errors import (
    PurgeTokenRejected,
    WorkspaceBenchmarkProtected,
    WorkspacePurgeBlocked,
)
from app.workspace.purge_tokens import PurgeTokenStore
from app.workspace.service import get_workspace

_TERMINAL_TASK_STATUSES = (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)


@dataclass(frozen=True, slots=True)
class PurgeCounts:
    """As 6 contagens de [02] §11 mais a marca de proteção de benchmark.

    `workspaces` é sempre 1 (o próprio). `artifacts` é sempre 0 até o GC por conteúdo
    existir (E3-AUD-006) — a purga não toca disco. `benchmark_protected` é `True` quando a
    regra 6 de §11 impede a purga (E3-AUD2-005).
    """

    workspaces: int
    tasks: int
    runs: int
    findings: int
    manifests: int
    artifacts: int
    benchmark_protected: bool


def _count(session: Session, statement: Select[tuple[int]]) -> int:
    return int(session.scalar(statement) or 0)


def _has_benchmark_evaluation(session: Session, workspace_id: str) -> bool:
    """[02] §11 regra 6: existe `Run`/`AuditFinding` de `benchmark_evaluation` num
    `benchmark_group_id` ao qual alguma task deste workspace pertence?

    A busca é **por grupo**, não por workspace: o `benchmark_group_id` liga execuções do
    mesmo objetivo em modos (e workspaces) diferentes ([02] §3), então a avaliação pode
    viver numa task de outro workspace do mesmo grupo.
    """
    groups = select(WorkspaceTask.benchmark_group_id).where(
        WorkspaceTask.workspace_id == workspace_id,
        WorkspaceTask.benchmark_group_id.is_not(None),
    )
    tasks_in_groups = select(WorkspaceTask.id).where(WorkspaceTask.benchmark_group_id.in_(groups))

    run_hit = (
        select(Run.id)
        .where(
            Run.task_id.in_(tasks_in_groups),
            Run.purpose == RunPurpose.BENCHMARK_EVALUATION,
        )
        .limit(1)
    )
    if session.scalar(run_hit) is not None:
        return True

    finding_hit = (
        select(AuditFinding.id)
        .join(Run, AuditFinding.run_id == Run.id)
        .where(
            Run.task_id.in_(tasks_in_groups),
            AuditFinding.purpose == FindingPurpose.BENCHMARK_EVALUATION,
        )
        .limit(1)
    )
    return session.scalar(finding_hit) is not None


def _count_purgeable(session: Session, workspace_id: str) -> PurgeCounts:
    task_ids = select(WorkspaceTask.id).where(WorkspaceTask.workspace_id == workspace_id)
    run_ids = select(Run.id).where(Run.task_id.in_(task_ids))

    return PurgeCounts(
        workspaces=1,
        tasks=_count(
            session,
            select(func.count())
            .select_from(WorkspaceTask)
            .where(WorkspaceTask.workspace_id == workspace_id),
        ),
        runs=_count(
            session, select(func.count()).select_from(Run).where(Run.task_id.in_(task_ids))
        ),
        findings=_count(
            session,
            select(func.count()).select_from(AuditFinding).where(AuditFinding.run_id.in_(run_ids)),
        ),
        manifests=_count(
            session,
            select(func.count())
            .select_from(ContextManifest)
            .where(ContextManifest.task_id.in_(task_ids)),
        ),
        # E3-AUD-006: a purga não remove blob nenhum do disco (GC por conteúdo não existe
        # ainda), então reportar >0 aqui seria afirmar uma remoção que não acontece.
        artifacts=0,
        benchmark_protected=_has_benchmark_evaluation(session, workspace_id),
    )


def purge_preview(session: Session, workspace_id: str) -> PurgeCounts:
    """Contagens do que a purga removeria + `benchmark_protected`.

    Levanta `WorkspaceNotFound` se o workspace não existir.
    """
    get_workspace(session, workspace_id)
    return _count_purgeable(session, workspace_id)


def _has_non_terminal_task(session: Session, workspace_id: str) -> bool:
    statement = (
        select(WorkspaceTask.id)
        .where(
            WorkspaceTask.workspace_id == workspace_id,
            WorkspaceTask.status.notin_(_TERMINAL_TASK_STATUSES),
        )
        .limit(1)
    )
    return session.scalar(statement) is not None


def execute_purge(
    session: Session,
    store: PurgeTokenStore,
    workspace_id: str,
    purge_token: str,
) -> PurgeCounts:
    """Consome o token, **revalida do zero** e remove as linhas do workspace.

    Ordem ([prompt E3 sub-etapa 4]): `consume` → (se `False`, 403 genérico) → revalidar
    (`status == archived`, nenhuma task não-terminal) → executar. Um token consumido não
    volta, mesmo que a revalidação falhe depois.
    """
    if not store.consume(workspace_id, purge_token):
        raise PurgeTokenRejected("purga não autorizada")

    workspace = get_workspace(session, workspace_id)

    if workspace.status is not WorkspaceStatus.ARCHIVED:
        raise WorkspacePurgeBlocked("workspace precisa estar arquivado para ser purgado")

    if _has_non_terminal_task(session, workspace_id):
        raise WorkspacePurgeBlocked("workspace tem task não-terminal; purga recusada")

    counts = _count_purgeable(session, workspace_id)

    if counts.benchmark_protected:
        # [02] §11 regra 6 / R8: métricas de comparação não somem por exclusão.
        raise WorkspaceBenchmarkProtected(
            "workspace pertence a um grupo de benchmark com avaliação registrada; purga recusada"
        )

    # FK `ON DELETE CASCADE` (com `PRAGMA foreign_keys=ON`, ver `app.db.session`) remove
    # context entries, tasks, manifests, runs e findings. `safety_event` não tem FK e
    # sobrevive ([02] §11 regra 4). O repositório do usuário não é tocado (regra 7). Nenhum
    # arquivo de disco é removido — `counts.artifacts` é 0 (E3-AUD-006).
    session.delete(workspace)
    session.flush()
    return counts


def workspace_exists(session: Session, workspace_id: str) -> bool:
    """Auxiliar de teste/serviço: `True` se a linha ainda está no banco."""
    return session.get(DevWorkspace, workspace_id) is not None
