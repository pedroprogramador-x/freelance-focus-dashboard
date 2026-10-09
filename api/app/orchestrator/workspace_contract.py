"""Contrato da preparação da execution workspace (E8.4.2) — **puro**: sem IO, banco ou relógio.

Os vocabulários fechados que o serviço `execution_workspace` devolve e que o Execution Manager
persiste: o desfecho local da preparação (separado de `TaskStatus`/`RunStatus`), a etapa em que
ela parou, o código estável da causa, o estado do binding e os fatos que a releitura do banco
entrega ao serviço. Nada aqui decide transição de task nem fecha Run: a finalização agregada é
da E8.4.5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class PreparationStatus(str, Enum):
    """Desfecho **local** da preparação. Só `PREPARED` entrega contexto utilizável."""

    PREPARED = "prepared"
    #: Pré-condição, política ou integridade recusou; nada novo foi adotado.
    REFUSED = "refused"
    #: Cancelamento observado (sinal injetado ou `cancel_requested` persistido).
    CANCELLED = "cancelled"
    #: O prazo da tentativa (`task_timeout_s` desde o Run de controle) acabou.
    TIMEOUT = "timeout"
    #: Um fato necessário não pôde ser observado ou provado. Fail closed.
    UNVERIFIABLE = "unverifiable"
    #: `id8`, caminho ou proveniência da worktree não provam que ela é desta task.
    OWNERSHIP_CONFLICT = "ownership_conflict"
    #: Outro escritor venceu (cancelamento, reconciliação, outra preparação): nada sobrescrito.
    SUPERSEDED = "superseded"


class PreparationStage(str, Enum):
    """A etapa (A–L do contrato E8.4.2) em que a preparação parou."""

    ADMISSION = "admission"
    PRECONDITIONS = "preconditions"  # A
    LAYOUT = "layout"  # B
    WORKTREE_ROOT = "worktree_root"  # C
    MAIN_SNAPSHOT = "main_snapshot"  # D/E
    PRE_CREATE = "pre_create"  # F
    CREATE = "create"  # G
    INSPECT = "inspect"  # H
    POST_CREATE = "post_create"  # I
    PERSIST = "persist"  # J
    BIND = "bind"  # K
    FINAL = "final"  # L
    DONE = "done"


class PreparationCode(str, Enum):
    """Causa estável. Vai para `Run.error_summary` prefixada; nunca caminho nem texto livre."""

    PREPARED = "prepared"
    NOT_ADMITTED = "not_admitted"
    PREPARATION_IN_PROGRESS = "preparation_in_progress"
    ALREADY_PREPARED = "already_prepared"
    TASK_MISSING = "task_missing"
    RUN_MISMATCH = "run_mismatch"
    TASK_NOT_EXECUTING = "task_not_executing"
    RUN_NOT_OPEN = "run_not_open"
    ATTEMPT_MISMATCH = "attempt_mismatch"
    TASK_VERSION_CHANGED = "task_version_changed"
    CANCEL_REQUESTED = "cancel_requested"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    WORKSPACE_INACTIVE = "workspace_inactive"
    PLAN_INCOHERENT = "plan_incoherent"
    BASE_COMMIT_INCOHERENT = "base_commit_incoherent"
    FINGERPRINT_DIVERGED = "fingerprint_diverged"
    TEST_POLICY_MISSING = "test_policy_missing"
    OBJECT_FORMAT_UNSUPPORTED = "object_format_unsupported"
    MAIN_HEAD_DIVERGED = "main_head_diverged"
    MAIN_HEAD_UNVERIFIABLE = "main_head_unverifiable"
    LAYOUT_UNVERIFIABLE = "layout_unverifiable"
    LAYOUT_CHANGED = "layout_changed"
    WORKTREE_ROOT_DENIED = "worktree_root_denied"
    WORKTREE_ROOT_UNVERIFIABLE = "worktree_root_unverifiable"
    IDENTITY_UNVERIFIABLE = "identity_unverifiable"
    IDENTITY_CHANGED = "identity_changed"
    ID8_COLLISION = "id8_collision"
    PATH_CLAIMED = "path_claimed"
    UNPROVEN_REUSE = "unproven_reuse"
    UNEXPECTED_REUSE = "unexpected_reuse"
    MAIN_SNAPSHOT_UNVERIFIABLE = "main_snapshot_unverifiable"
    WORKTREE_REFUSED = "worktree_refused"
    WORKTREE_UNVERIFIABLE = "worktree_unverifiable"
    WORKTREE_CREATED_INVALID = "worktree_created_invalid"
    WORKTREE_POSTCHECK_FAILED = "worktree_postcheck_failed"
    PUBLICATION_LOST = "publication_lost"
    PERSISTENCE_FAILED = "persistence_failed"
    BINDING_FAILED = "binding_failed"
    FINAL_CHECK_FAILED = "final_check_failed"
    INTERNAL_ERROR = "internal_error"


class BindingState(str, Enum):
    """O binding por Run desta preparação."""

    NOT_ATTEMPTED = "not_attempted"
    #: Registrado e conferido pela regra canônica; só existe em `PREPARED`.
    PUBLISHED = "published"
    #: O registro foi recusado; o adaptador desfez o próprio registro antes de levantar.
    REJECTED = "rejected"
    #: Estava publicado e a compensação **revogou** a autorização dele (só a própria): a
    #: resolução do Run está negada, ainda que a remoção física não se prove.
    REMOVED = "removed"
    #: A compensação não conseguiu provar a revogação. Exige recovery.
    REMOVAL_FAILED = "removal_failed"


#: Prefixo do `Run.error_summary` gravado por uma preparação que não concluiu.
ERROR_SUMMARY_PREFIX = "workspace_preparation"


def error_summary(status: PreparationStatus, code: PreparationCode) -> str:
    """`workspace_preparation:<status>:<code>` — só vocabulário fechado."""
    return f"{ERROR_SUMMARY_PREFIX}:{status.value}:{code.value}"


@dataclass(frozen=True, slots=True)
class PreparationFacts:
    """O que a releitura do banco entrega ao serviço. Valores, nunca ORM.

    ``code`` é a primeira incoerência encontrada (`None` = elegível). Os caminhos ficam fora do
    `repr`: são campos internos de diagnóstico.
    """

    code: PreparationCode | None
    task_id: str
    control_run_id: str
    workspace_id: str | None = None
    local_path: str | None = field(default=None, repr=False)
    base_commit: str | None = None
    task_version: int | None = None
    control_invocation_id: str | None = None
    run_started_at: datetime | None = None
    attempt_index: int | None = None
    fix_round: int | None = None
    run_timeout_s: int | None = None
    task_timeout_s: int | None = None
    #: Outra task (qualquer status, qualquer workspace) com o mesmo `id8`.
    id8_collision: bool = False
    #: `worktree_path` registrados por **outras** tasks ou pelos Runs delas.
    foreign_paths: tuple[str, ...] = field(default=(), repr=False)
    #: `worktree_path` registrados por esta task (Task ou Runs anteriores, mesma base).
    own_paths: tuple[str, ...] = field(default=(), repr=False)
    #: `Run.worktree_path` do Run de controle (`None` = ainda não publicado).
    control_worktree_path: str | None = field(default=None, repr=False)


__all__ = [
    "ERROR_SUMMARY_PREFIX",
    "BindingState",
    "PreparationCode",
    "PreparationFacts",
    "PreparationStage",
    "PreparationStatus",
    "error_summary",
]
