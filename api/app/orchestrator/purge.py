"""Prévia e execução da purga de `WorkspaceTask` ([02] §11).

Mesmo desenho da purga de workspace (`app.workspace.purge`), com o escopo de [02] §11
regra 1 para task: **"task só pode ser purgada em `done`, `failed` ou `cancelled`"**.

As mesmas seis contagens, a mesma prévia obrigatória, a mesma confirmação forte, a mesma
proteção de benchmark (regra 6), o mesmo `SafetyEvent` preservado (regra 4) e o mesmo
repositório do usuário intocado (regra 7).

## O `PurgeTokenStore` é reaproveitado — com o sujeito **namespaced**

[02] §11 regra 3 exige confirmação forte que "não pode ser um simples parâmetro de query".
A E3 já resolveu isso com o `PurgeTokenStore` (token de 256 bits, TTL 60 s, uso único,
vinculado ao sujeito). Criar um segundo store para task teria duplicado a lógica de TTL e
de uso único sem ganhar nada.

O que **mudou** é o sujeito: ele passou de `workspace_id` cru para um rótulo com espaço de
nomes (`workspace:<id>` / `task:<id>`). Sem isso, a única coisa que impediria um token
emitido por `GET /workspaces/{x}/purge-preview` de ser aceito por `POST /tasks/{x}/purge`
seria um `workspace_id` nunca coincidir com um `task_id` — verdade prática com UUID v4,
mas invariante **não imposta** por nada. Com o prefixo, a confusão de tipo é impossível por
construção em vez de improvável por sorte.
"""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.db.enums import FindingPurpose, RunPurpose, TaskStatus
from app.db.models import AuditFinding, ContextManifest, Run, WorkspaceTask
from app.orchestrator.errors import (
    PurgeTokenRejected,
    TaskBenchmarkProtected,
    TaskPurgeBlocked,
)
from app.orchestrator.execution_manager import command, get_task
from app.workspace.purge import PurgeCounts
from app.workspace.purge_tokens import PurgeTokenStore, task_subject

_TERMINAL_TASK_STATUSES = (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)


def _count(session: Session, statement: Select[tuple[int]]) -> int:
    return int(session.scalar(statement) or 0)


def _has_benchmark_evaluation(session: Session, task: WorkspaceTask) -> bool:
    """[02] §11 regra 6, no escopo da task.

    Uma task **sem** `benchmark_group_id` não pertence a grupo nenhum e não pode ser
    protegida por ele: a busca por grupo com `NULL` casaria todas as outras tasks soltas,
    que é o oposto do que a regra quer. Por isso o retorno antecipado.
    """
    if task.benchmark_group_id is None:
        return False

    tasks_in_group = select(WorkspaceTask.id).where(
        WorkspaceTask.benchmark_group_id == task.benchmark_group_id
    )

    run_hit = (
        select(Run.id)
        .where(
            Run.task_id.in_(tasks_in_group),
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
            Run.task_id.in_(tasks_in_group),
            AuditFinding.purpose == FindingPurpose.BENCHMARK_EVALUATION,
        )
        .limit(1)
    )
    return session.scalar(finding_hit) is not None


def _count_purgeable(session: Session, task: WorkspaceTask) -> PurgeCounts:
    run_ids = select(Run.id).where(Run.task_id == task.id)

    return PurgeCounts(
        # A purga de uma task nunca remove o workspace dela.
        workspaces=0,
        tasks=1,
        runs=_count(session, select(func.count()).select_from(Run).where(Run.task_id == task.id)),
        findings=_count(
            session,
            select(func.count()).select_from(AuditFinding).where(AuditFinding.run_id.in_(run_ids)),
        ),
        manifests=_count(
            session,
            select(func.count())
            .select_from(ContextManifest)
            .where(ContextManifest.task_id == task.id),
        ),
        # Mesma razão de E3-AUD-006: a purga não remove blob nenhum do disco. O GC de
        # artefato por contagem de referência ([02] §11 regra 5) ainda não existe, e um
        # número não-zero aqui afirmaria uma remoção que não acontece. O blob é endereçado
        # por conteúdo e pode estar referenciado por manifests de outras tasks.
        artifacts=0,
        benchmark_protected=_has_benchmark_evaluation(session, task),
    )


def purge_preview(session: Session, task_id: str) -> PurgeCounts:
    """Contagens do que a purga removeria + `benchmark_protected`.

    **Não** exige que a task já esteja terminal: a prévia é informativa e ver o que uma
    purga futura removeria não é destrutivo. Quem recusa é `execute_purge`.
    """
    return _count_purgeable(session, get_task(session, task_id))


@command
def execute_purge(
    session: Session,
    store: PurgeTokenStore,
    task_id: str,
    purge_token: str,
) -> PurgeCounts:
    """Consome o token, **revalida do zero** e remove as linhas da task.

    Mesma ordem de `workspace.purge.execute_purge`: `consume` → (se `False`, 403 genérico) →
    revalidar → executar. Um token consumido não volta, mesmo que a revalidação falhe
    depois — é o que impede usar a recusa como oráculo para tentar de novo.
    """
    if not store.consume(task_subject(task_id), purge_token):
        raise PurgeTokenRejected("purga não autorizada")

    task = get_task(session, task_id)

    if task.status not in _TERMINAL_TASK_STATUSES:
        raise TaskPurgeBlocked(
            f"task em `{task.status.value}`: só `done`, `failed` ou `cancelled` podem ser "
            "purgadas ([02] §11 regra 1)"
        )

    counts = _count_purgeable(session, task)

    if counts.benchmark_protected:
        raise TaskBenchmarkProtected(
            "a task pertence a um grupo de benchmark com avaliação registrada; purga "
            "recusada ([02] §11 regra 6)"
        )

    # FK `ON DELETE CASCADE` remove manifests, runs e findings. `safety_event` não tem FK e
    # sobrevive (regra 4). O repositório do usuário não é tocado (regra 7).
    session.delete(task)
    session.flush()
    return counts


__all__ = ["execute_purge", "purge_preview"]
