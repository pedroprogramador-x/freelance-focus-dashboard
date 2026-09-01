"""As **sete** entidades de [02](../../../docs/architecture/02-data-model.md).

`MetricSample` não existe ([ADR-0007]). O *Rendered Context Artifact* também não é
tabela: é blob endereçado por conteúdo em `data_dir/artifacts/<sha256>.json`, referenciado
por `ContextManifest.rendered_context_ref` ([02] §5).

Invariantes que o **banco** verifica aparecem como `CheckConstraint`. Invariantes que
dependem de mais de uma linha — a cadeia `supersedes_run_id`, a auditoria vigente — são de
serviço e chegam com o Execution Manager (E9); estão anotadas onde faltam.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db import enums
from app.db.base import Base, UtcDateTime, enum_column, new_uuid, nullable_json, utcnow


class DevWorkspace(Base):
    """[02] §1. Entidade independente de `Project` ([ADR-0003])."""

    __tablename__ = "dev_workspace"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    type: Mapped[enums.WorkspaceType] = mapped_column(
        enum_column(enums.WorkspaceType, "workspace_type"), nullable=False
    )
    local_path: Mapped[str] = mapped_column(sa.String(4096), nullable=False, unique=True)

    #: String **opaca**: sem FK, sem validação, sem resolução pelo backend ([ADR-0003]).
    #: O vínculo com o `Project` comercial é resolvido só pelo frontend.
    linked_project_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)

    repository_url: Mapped[str | None] = mapped_column(sa.String(2048), nullable=True)
    default_branch: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    status: Mapped[enums.WorkspaceStatus] = mapped_column(
        enum_column(enums.WorkspaceStatus, "workspace_status"),
        nullable=False,
        default=enums.WorkspaceStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        sa.CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        sa.CheckConstraint("length(local_path) > 0", name="local_path_not_blank"),
    )


class ContextRegistryEntry(Base):
    """[02] §2. `file_map` **não** é entrada — é artefato derivado ([ADR-0006])."""

    __tablename__ = "context_registry_entry"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(
        sa.String(36),
        sa.ForeignKey("dev_workspace.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    domain: Mapped[enums.ContextDomain] = mapped_column(
        enum_column(enums.ContextDomain, "context_domain"), nullable=False
    )
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    structured: Mapped[dict[str, Any] | None] = mapped_column(nullable_json(), nullable=True)
    tags: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False, default=list)
    source_refs: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False, default=list)
    content_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    source_hash: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    source_hash_commit: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    state: Mapped[enums.ContextState] = mapped_column(
        enum_column(enums.ContextState, "context_state"), nullable=False
    )
    stale_reason: Mapped[enums.StaleReason | None] = mapped_column(
        enum_column(enums.StaleReason, "stale_reason"), nullable=True
    )
    origin: Mapped[enums.ContextOrigin] = mapped_column(
        enum_column(enums.ContextOrigin, "context_origin"), nullable=False
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_verified_commit: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        sa.CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
        sa.CheckConstraint("length(body) > 0", name="body_not_empty"),
        # [02] §2: `stale_reason` não nulo **se e somente se** `state = 'stale'`.
        sa.CheckConstraint(
            "(state = 'stale') = (stale_reason IS NOT NULL)", name="stale_reason_iff_stale"
        ),
    )


class WorkspaceTask(Base):
    """[02] §3 e §4. Máquina de estados de nove estados ([ADR-0008])."""

    __tablename__ = "workspace_task"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(
        sa.String(36),
        sa.ForeignKey("dev_workspace.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    goal: Mapped[str] = mapped_column(sa.Text, nullable=False)

    status: Mapped[enums.TaskStatus] = mapped_column(
        enum_column(enums.TaskStatus, "task_status"),
        nullable=False,
        default=enums.TaskStatus.DRAFT,
    )
    phase: Mapped[enums.TaskPhase | None] = mapped_column(
        enum_column(enums.TaskPhase, "task_phase"), nullable=True
    )
    #: Concorrência otimista: toda transição é *compare-and-set* sobre `(id, status,
    #: version)` ([02] §4, "cancel versus conclusão").
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)

    risk: Mapped[enums.RiskLevel] = mapped_column(
        enum_column(enums.RiskLevel, "risk_level"), nullable=False
    )
    complexity: Mapped[enums.ComplexityLevel] = mapped_column(
        enum_column(enums.ComplexityLevel, "complexity_level"), nullable=False
    )
    risk_source: Mapped[enums.RiskSource] = mapped_column(
        enum_column(enums.RiskSource, "risk_source"), nullable=False
    )
    execution_mode: Mapped[enums.ExecutionMode] = mapped_column(
        enum_column(enums.ExecutionMode, "execution_mode"), nullable=False
    )
    #: Liga as execuções do mesmo objetivo em modos diferentes ([ADR-0010]).
    benchmark_group_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True, index=True)

    agents: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False, default=list)
    plan: Mapped[dict[str, Any] | None] = mapped_column(nullable_json(), nullable=True)
    plan_hash: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)

    #: Congelado no início do planejamento ([02] §6):
    #: `planning_base_commit == manifest.git_head == run.base_commit`.
    planning_base_commit: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)

    approved_manifest_id: Mapped[str | None] = mapped_column(
        sa.String(36), sa.ForeignKey("context_manifest.id", ondelete="SET NULL"), nullable=True
    )
    approved_fingerprint: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    approved_fingerprint_parts: Mapped[dict[str, Any] | None] = mapped_column(
        nullable_json(), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    fix_rounds: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    base_commit: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    worktree_path: Mapped[str | None] = mapped_column(sa.String(4096), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    failure_reason: Mapped[enums.FailureReason | None] = mapped_column(
        enum_column(enums.FailureReason, "failure_reason"), nullable=True
    )
    result_summary: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    __table_args__ = (
        sa.CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
        # [02] §3: `phase` não nulo **se e somente se** `status = 'executing'`.
        sa.CheckConstraint(
            "(status = 'executing') = (phase IS NOT NULL)", name="phase_iff_executing"
        ),
        # [02] §3: `failure_reason` só em falha real — nunca em `cancelled`, nunca em `done`.
        sa.CheckConstraint(
            "(status = 'failed') = (failure_reason IS NOT NULL)", name="failure_reason_iff_failed"
        ),
        sa.CheckConstraint(
            "approved_at IS NULL OR ("
            " plan_hash IS NOT NULL"
            " AND approved_manifest_id IS NOT NULL"
            " AND approved_fingerprint IS NOT NULL)",
            name="approval_requires_bindings",
        ),
        sa.CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        sa.CheckConstraint("fix_rounds >= 0", name="fix_rounds_non_negative"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NOT NULL", name="finished_requires_started"
        ),
        sa.Index("ix_workspace_task_workspace_status", "workspace_id", "status"),
    )


class ContextManifest(Base):
    """[02] §5. Linha **imutável**: registra as fontes; o payload vive no artefato."""

    __tablename__ = "context_manifest"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_uuid)
    task_id: Mapped[str] = mapped_column(
        sa.String(36),
        sa.ForeignKey("workspace_task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    git_head: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    base_branch: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)

    entries: Mapped[list[dict[str, Any]]] = mapped_column(sa.JSON, nullable=False, default=list)
    source_files: Mapped[list[dict[str, Any]]] = mapped_column(
        sa.JSON, nullable=False, default=list
    )
    working_tree_divergence: Mapped[dict[str, Any]] = mapped_column(
        sa.JSON, nullable=False, default=dict
    )
    derived: Mapped[list[dict[str, Any]]] = mapped_column(sa.JSON, nullable=False, default=list)
    excluded: Mapped[list[dict[str, Any]]] = mapped_column(sa.JSON, nullable=False, default=list)

    rendered_context_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    #: Caminho no artifact store, endereçado pelo hash. O blob nunca é uma linha.
    rendered_context_ref: Mapped[str] = mapped_column(sa.String(4096), nullable=False)
    renderer_version: Mapped[str] = mapped_column(sa.String(64), nullable=False)

    approx_tokens: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    total_chars: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        sa.CheckConstraint("approx_tokens >= 0", name="approx_tokens_non_negative"),
        sa.CheckConstraint("total_chars >= 0", name="total_chars_non_negative"),
    )


class Run(Base):
    """[02] §8. Uma execução concreta de um agente, provider ou runner.

    Idempotência por `invocation_id` UNIQUE. A constraint antiga
    `(task_id, agent, attempt_index, fix_round, purpose)` **não existe**: ela impedia duas
    auditorias legítimas sobre o mesmo sujeito (REAUD-005).
    """

    __tablename__ = "run"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_uuid)
    #: Chave idempotente de **uma tentativa concreta**. Reenviar a mesma requisição
    #: reaproveita este `Run`; uma tentativa real nova gera outro `invocation_id`.
    invocation_id: Mapped[str] = mapped_column(sa.String(128), nullable=False, unique=True)

    task_id: Mapped[str] = mapped_column(
        sa.String(36),
        sa.ForeignKey("workspace_task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: O `Run` avaliado. Obrigatório quando `purpose` é auditoria ou avaliação.
    subject_run_id: Mapped[str | None] = mapped_column(
        sa.String(36), sa.ForeignKey("run.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: Auditoria anterior que esta pretende substituir. Perder o elo é aceitável;
    #: perder o sujeito não seria — daí `SET NULL` aqui e `CASCADE` acima.
    supersedes_run_id: Mapped[str | None] = mapped_column(
        sa.String(36), sa.ForeignKey("run.id", ondelete="SET NULL"), nullable=True
    )
    context_manifest_id: Mapped[str | None] = mapped_column(
        sa.String(36), sa.ForeignKey("context_manifest.id", ondelete="SET NULL"), nullable=True
    )

    agent: Mapped[enums.RunAgent] = mapped_column(
        enum_column(enums.RunAgent, "run_agent"), nullable=False
    )
    purpose: Mapped[enums.RunPurpose] = mapped_column(
        enum_column(enums.RunPurpose, "run_purpose"), nullable=False
    )
    #: Informativos e de ordenação. **Não** são chave e não restringem nada ([02] §8).
    attempt_index: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    fix_round: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    provider: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    provider_adapter: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    adapter_version: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    transport: Mapped[enums.RunTransport] = mapped_column(
        enum_column(enums.RunTransport, "run_transport"), nullable=False
    )
    tool_profile_hash: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)

    status: Mapped[enums.RunStatus] = mapped_column(
        enum_column(enums.RunStatus, "run_status"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    input_tokens: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    token_source: Mapped[enums.TokenSource] = mapped_column(
        enum_column(enums.TokenSource, "token_source"), nullable=False
    )

    #: `[]` significa *medido, nada foi lido*. Indisponível é **`NULL`**. A CHECK abaixo
    #: torna essa distinção verificável pelo banco ([02] §10).
    files_read: Mapped[list[str] | None] = mapped_column(nullable_json(), nullable=True)
    files_read_source: Mapped[enums.FilesReadSource] = mapped_column(
        enum_column(enums.FilesReadSource, "files_read_source"), nullable=False
    )

    #: Sempre derivados pelo Git Runtime — nunca reportados pelo provider ([02] §10).
    files_changed: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False, default=list)
    diff_added: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    diff_removed: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    test_summary: Mapped[dict[str, Any] | None] = mapped_column(nullable_json(), nullable=True)
    worktree_path: Mapped[str | None] = mapped_column(sa.String(4096), nullable=True)
    base_commit: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)

    #: Impressão digital do prompt — **nunca** o prompt. Raciocínio privado e
    #: *chain-of-thought* não são persistidos em lugar nenhum ([02] §8).
    prompt_sha256: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    summary: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    log_ref: Mapped[str | None] = mapped_column(sa.String(4096), nullable=True)

    __table_args__ = (
        sa.CheckConstraint(
            "purpose = 'execution' OR subject_run_id IS NOT NULL",
            name="audit_requires_subject",
        ),
        sa.CheckConstraint(
            "subject_run_id IS NULL OR subject_run_id <> id", name="subject_not_self"
        ),
        sa.CheckConstraint(
            "supersedes_run_id IS NULL OR supersedes_run_id <> id", name="supersedes_not_self"
        ),
        # Invariante de proveniência: `NULL` ⟺ telemetria indisponível.
        sa.CheckConstraint(
            "(files_read IS NULL) = (files_read_source = 'unavailable')",
            name="files_read_null_iff_unavailable",
        ),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_non_negative"),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="input_tokens_non_negative"
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="output_tokens_non_negative"
        ),
        sa.CheckConstraint("diff_added >= 0", name="diff_added_non_negative"),
        sa.CheckConstraint("diff_removed >= 0", name="diff_removed_non_negative"),
        sa.CheckConstraint("attempt_index >= 0", name="attempt_index_non_negative"),
        sa.CheckConstraint("fix_round >= 0", name="fix_round_non_negative"),
        sa.Index("ix_run_subject_purpose", "subject_run_id", "purpose"),
    )


class AuditFinding(Base):
    """[02] §9. `run_id` aponta **sempre para o `Run` do auditor**.

    O sujeito é resolvido por `AuditFinding.run_id → Run.subject_run_id`.
    """

    __tablename__ = "audit_finding"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[enums.FindingPurpose] = mapped_column(
        enum_column(enums.FindingPurpose, "finding_purpose"), nullable=False
    )
    rubric_version: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    severity: Mapped[enums.FindingSeverity] = mapped_column(
        enum_column(enums.FindingSeverity, "finding_severity"), nullable=False
    )
    category: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    file: Mapped[str | None] = mapped_column(sa.String(4096), nullable=True)
    line: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    summary: Mapped[str] = mapped_column(sa.Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    status: Mapped[enums.FindingStatus] = mapped_column(
        enum_column(enums.FindingStatus, "finding_status"),
        nullable=False,
        default=enums.FindingStatus.OPEN,
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        sa.CheckConstraint("length(trim(summary)) > 0", name="summary_not_blank"),
        sa.CheckConstraint("length(trim(category)) > 0", name="category_not_blank"),
        # `rubric_version` é obrigatório em avaliação de benchmark ([02] §9).
        sa.CheckConstraint(
            "purpose <> 'benchmark_evaluation' OR rubric_version IS NOT NULL",
            name="benchmark_requires_rubric",
        ),
        sa.CheckConstraint("line IS NULL OR line >= 1", name="line_positive"),
        sa.Index("ix_audit_finding_run_severity_status", "run_id", "severity", "status"),
    )


class SafetyEvent(Base):
    """[02] §12. Trilha **append-only**: nunca atualizada, nunca apagada, não cascateada.

    As referências são texto solto de propósito — **sem FK**. Um `SafetyEvent` precisa
    sobreviver à purga do workspace, da task e do run que o originaram. Os *triggers*
    criados pela migration recusam `UPDATE` e `DELETE` no próprio banco.
    """

    __tablename__ = "safety_event"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True)

    kind: Mapped[enums.SafetyEventKind] = mapped_column(
        enum_column(enums.SafetyEventKind, "safety_event_kind"), nullable=False
    )
    decision: Mapped[enums.SafetyDecisionKind] = mapped_column(
        enum_column(enums.SafetyDecisionKind, "safety_decision_kind"), nullable=False
    )
    rule_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    #: Já redigido pelo `safety.redact` antes de chegar aqui.
    subject: Mapped[str] = mapped_column(sa.Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, index=True
    )

    __table_args__ = (sa.CheckConstraint("length(trim(rule_id)) > 0", name="rule_id_not_blank"),)


#: Ordem de criação. `workspace_task` referencia `context_manifest`, que referencia
#: `workspace_task` de volta — um ciclo. O SQLite não valida a existência da tabela pai
#: no `CREATE TABLE`, então a referência adiante é legal; a ordem abaixo é a que a
#: migration usa.
TABLE_CREATION_ORDER: tuple[str, ...] = (
    "dev_workspace",
    "workspace_task",
    "context_registry_entry",
    "context_manifest",
    "run",
    "audit_finding",
    "safety_event",
)

ALL_MODELS = (
    DevWorkspace,
    ContextRegistryEntry,
    WorkspaceTask,
    ContextManifest,
    Run,
    AuditFinding,
    SafetyEvent,
)
