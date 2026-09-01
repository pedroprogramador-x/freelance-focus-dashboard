"""schema inicial: as sete entidades do AI Dev Workspace

Congelado a partir de docs/architecture/02-data-model.md
(architecture freeze 67d4df497b4f855acab1a74b8ab95a7b1fc07d96).

Os valores de enum aparecem aqui como literais, e não importados de `app.db.enums`, de
propósito: uma migration é um registro histórico e não pode mudar de significado quando o
código muda. O teste `test_migration_schema.py` compara os dois e falha se divergirem.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    """Enum como VARCHAR + CHECK. `create_constraint=True` não é padrão desde a 1.4."""
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


UUID = sa.String(36)
SHA256 = sa.String(64)
SHA1 = sa.String(40)
PATH = sa.String(4096)


def upgrade() -> None:
    # ------------------------------------------------------------------ dev_workspace
    op.create_table(
        "dev_workspace",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "type",
            _enum("workspace_type", "personal", "freelance", "study", "experiment", "open_source"),
            nullable=False,
        ),
        sa.Column("local_path", PATH, nullable=False),
        sa.Column("linked_project_id", sa.String(64), nullable=True),
        sa.Column("repository_url", sa.String(2048), nullable=True),
        sa.Column("default_branch", sa.String(255), nullable=True),
        sa.Column("status", _enum("workspace_status", "active", "archived"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        sa.CheckConstraint("length(local_path) > 0", name="local_path_not_blank"),
        sa.UniqueConstraint("local_path", name="uq_dev_workspace_local_path"),
    )

    # ----------------------------------------------------------------- workspace_task
    #
    # Criada antes de `context_manifest` embora referencie a tabela: o SQLite não valida
    # a existência do pai no CREATE TABLE — a checagem acontece no DML. As duas tabelas
    # se referenciam mutuamente, então alguma das duas tem de vir primeiro.
    op.create_table(
        "workspace_task",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column(
            "status",
            _enum(
                "task_status",
                "draft",
                "planning",
                "awaiting_approval",
                "approved",
                "executing",
                "needs_fix",
                "done",
                "failed",
                "cancelled",
            ),
            nullable=False,
        ),
        sa.Column(
            "phase",
            _enum("task_phase", "implementing", "testing", "auditing"),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("risk", _enum("risk_level", "low", "medium", "high"), nullable=False),
        sa.Column(
            "complexity",
            _enum("complexity_level", "trivial", "low", "medium", "high"),
            nullable=False,
        ),
        sa.Column("risk_source", _enum("risk_source", "hard_rule", "llm", "user"), nullable=False),
        sa.Column(
            "execution_mode",
            _enum("execution_mode", "claude_only", "orchestrated", "orchestrated_ruflo"),
            nullable=False,
        ),
        sa.Column("benchmark_group_id", UUID, nullable=True),
        sa.Column("agents", sa.JSON(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=True),
        sa.Column("plan_hash", SHA256, nullable=True),
        sa.Column("planning_base_commit", SHA1, nullable=True),
        sa.Column("approved_manifest_id", UUID, nullable=True),
        sa.Column("approved_fingerprint", SHA256, nullable=True),
        sa.Column("approved_fingerprint_parts", sa.JSON(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("fix_rounds", sa.Integer(), nullable=False),
        sa.Column("base_commit", SHA1, nullable=True),
        sa.Column("worktree_path", PATH, nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column(
            "failure_reason",
            _enum(
                "failure_reason",
                "timeout",
                "limit_exceeded",
                "blocked_by_policy",
                "capability_unenforceable",
                "provider_error",
                "tests_failed",
                "audit_failed",
                "interrupted",
                "internal_error",
            ),
            nullable=True,
        ),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["dev_workspace.id"],
            name="fk_workspace_task_workspace_id_dev_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approved_manifest_id"],
            ["context_manifest.id"],
            name="fk_workspace_task_approved_manifest_id_context_manifest",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
        sa.CheckConstraint(
            "(status = 'executing') = (phase IS NOT NULL)", name="phase_iff_executing"
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (failure_reason IS NOT NULL)",
            name="failure_reason_iff_failed",
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
    )
    op.create_index("ix_workspace_task_workspace_id", "workspace_task", ["workspace_id"])
    op.create_index(
        "ix_workspace_task_benchmark_group_id", "workspace_task", ["benchmark_group_id"]
    )
    op.create_index(
        "ix_workspace_task_workspace_status", "workspace_task", ["workspace_id", "status"]
    )

    # ------------------------------------------------------- context_registry_entry
    op.create_table(
        "context_registry_entry",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column(
            "domain",
            _enum(
                "context_domain",
                "objective",
                "architecture",
                "stack",
                "requirements",
                "modules",
                "decisions",
                "risks",
                "contracts",
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("structured", sa.JSON(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("source_refs", sa.JSON(), nullable=False),
        sa.Column("content_hash", SHA256, nullable=False),
        sa.Column("source_hash", SHA256, nullable=True),
        sa.Column("source_hash_commit", SHA1, nullable=True),
        sa.Column("state", _enum("context_state", "fresh", "stale", "unknown"), nullable=False),
        sa.Column(
            "stale_reason",
            _enum("stale_reason", "sources_changed", "working_tree"),
            nullable=True,
        ),
        sa.Column(
            "origin",
            _enum("context_origin", "manual", "imported_planning", "generated"),
            nullable=False,
        ),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        sa.Column("last_verified_commit", SHA1, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["dev_workspace.id"],
            name="fk_context_registry_entry_workspace_id_dev_workspace",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
        sa.CheckConstraint("length(body) > 0", name="body_not_empty"),
        sa.CheckConstraint(
            "(state = 'stale') = (stale_reason IS NOT NULL)", name="stale_reason_iff_stale"
        ),
    )
    op.create_index(
        "ix_context_registry_entry_workspace_id", "context_registry_entry", ["workspace_id"]
    )

    # ------------------------------------------------------------- context_manifest
    op.create_table(
        "context_manifest",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("task_id", UUID, nullable=False),
        sa.Column("git_head", SHA1, nullable=False),
        sa.Column("base_branch", sa.String(255), nullable=True),
        sa.Column("entries", sa.JSON(), nullable=False),
        sa.Column("source_files", sa.JSON(), nullable=False),
        sa.Column("working_tree_divergence", sa.JSON(), nullable=False),
        sa.Column("derived", sa.JSON(), nullable=False),
        sa.Column("excluded", sa.JSON(), nullable=False),
        sa.Column("rendered_context_hash", SHA256, nullable=False),
        sa.Column("rendered_context_ref", PATH, nullable=False),
        sa.Column("renderer_version", sa.String(64), nullable=False),
        sa.Column("approx_tokens", sa.Integer(), nullable=False),
        sa.Column("total_chars", sa.Integer(), nullable=False),
        sa.Column("manifest_hash", SHA256, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["workspace_task.id"],
            name="fk_context_manifest_task_id_workspace_task",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("approx_tokens >= 0", name="approx_tokens_non_negative"),
        sa.CheckConstraint("total_chars >= 0", name="total_chars_non_negative"),
    )
    op.create_index("ix_context_manifest_task_id", "context_manifest", ["task_id"])
    op.create_index(
        "ix_context_manifest_rendered_context_hash",
        "context_manifest",
        ["rendered_context_hash"],
    )

    # --------------------------------------------------------------------------- run
    op.create_table(
        "run",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("invocation_id", sa.String(128), nullable=False),
        sa.Column("task_id", UUID, nullable=False),
        sa.Column("subject_run_id", UUID, nullable=True),
        sa.Column("supersedes_run_id", UUID, nullable=True),
        sa.Column("context_manifest_id", UUID, nullable=True),
        sa.Column(
            "agent",
            _enum(
                "run_agent",
                "orchestrator",
                "developer",
                "auditor",
                "architect",
                "researcher",
                "test_runner",
            ),
            nullable=False,
        ),
        sa.Column(
            "purpose",
            _enum("run_purpose", "execution", "workflow_audit", "benchmark_evaluation"),
            nullable=False,
        ),
        sa.Column("attempt_index", sa.Integer(), nullable=False),
        sa.Column("fix_round", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("provider_adapter", sa.String(128), nullable=False),
        sa.Column("adapter_version", sa.String(64), nullable=True),
        sa.Column("transport", _enum("run_transport", "cli", "api", "process"), nullable=False),
        sa.Column("tool_profile_hash", SHA256, nullable=True),
        sa.Column(
            "status",
            _enum("run_status", "ok", "error", "timeout", "cancelled", "blocked", "interrupted"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "token_source",
            _enum("token_source", "reported", "estimated", "unavailable"),
            nullable=False,
        ),
        sa.Column("files_read", sa.JSON(), nullable=True),
        sa.Column(
            "files_read_source",
            _enum("files_read_source", "reported", "inferred", "unavailable"),
            nullable=False,
        ),
        sa.Column("files_changed", sa.JSON(), nullable=False),
        sa.Column("diff_added", sa.Integer(), nullable=False),
        sa.Column("diff_removed", sa.Integer(), nullable=False),
        sa.Column("test_summary", sa.JSON(), nullable=True),
        sa.Column("worktree_path", PATH, nullable=True),
        sa.Column("base_commit", SHA1, nullable=True),
        sa.Column("prompt_sha256", SHA256, nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("log_ref", PATH, nullable=True),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["workspace_task.id"],
            name="fk_run_task_id_workspace_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subject_run_id"], ["run.id"], name="fk_run_subject_run_id_run", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_run_id"],
            ["run.id"],
            name="fk_run_supersedes_run_id_run",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["context_manifest_id"],
            ["context_manifest.id"],
            name="fk_run_context_manifest_id_context_manifest",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("invocation_id", name="uq_run_invocation_id"),
        sa.CheckConstraint(
            "purpose = 'execution' OR subject_run_id IS NOT NULL", name="audit_requires_subject"
        ),
        sa.CheckConstraint(
            "subject_run_id IS NULL OR subject_run_id <> id", name="subject_not_self"
        ),
        sa.CheckConstraint(
            "supersedes_run_id IS NULL OR supersedes_run_id <> id", name="supersedes_not_self"
        ),
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
    )
    op.create_index("ix_run_task_id", "run", ["task_id"])
    op.create_index("ix_run_subject_run_id", "run", ["subject_run_id"])
    op.create_index("ix_run_subject_purpose", "run", ["subject_run_id", "purpose"])

    # ------------------------------------------------------------------ audit_finding
    op.create_table(
        "audit_finding",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column(
            "purpose",
            _enum("finding_purpose", "workflow_audit", "benchmark_evaluation"),
            nullable=False,
        ),
        sa.Column("rubric_version", sa.String(64), nullable=True),
        sa.Column(
            "severity",
            _enum("finding_severity", "info", "low", "medium", "high"),
            nullable=False,
        ),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("file", PATH, nullable=True),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "status",
            _enum("finding_status", "open", "accepted", "dismissed", "fixed"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["run.id"], name="fk_audit_finding_run_id_run", ondelete="CASCADE"
        ),
        sa.CheckConstraint("length(trim(summary)) > 0", name="summary_not_blank"),
        sa.CheckConstraint("length(trim(category)) > 0", name="category_not_blank"),
        sa.CheckConstraint(
            "purpose <> 'benchmark_evaluation' OR rubric_version IS NOT NULL",
            name="benchmark_requires_rubric",
        ),
        sa.CheckConstraint("line IS NULL OR line >= 1", name="line_positive"),
    )
    op.create_index("ix_audit_finding_run_id", "audit_finding", ["run_id"])
    op.create_index(
        "ix_audit_finding_run_severity_status",
        "audit_finding",
        ["run_id", "severity", "status"],
    )

    # ------------------------------------------------------------------- safety_event
    #
    # Sem FK de propósito: a trilha precisa sobreviver à purga do workspace, da task e do
    # run que a originaram ([02] §12).
    op.create_table(
        "safety_event",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("workspace_id", UUID, nullable=True),
        sa.Column("task_id", UUID, nullable=True),
        sa.Column("run_id", UUID, nullable=True),
        sa.Column(
            "kind",
            _enum(
                "safety_event_kind",
                "path_denied",
                "command_denied",
                "secret_access_blocked",
                "capability_unenforceable",
                "capability_denied",
                "limit_exceeded",
                "approval_granted",
                "approval_invalidated",
                "retry_limit",
                "timeout",
                "cancelled",
                "out_of_worktree_write",
                "toctou_recheck_failed",
                "purge_executed",
            ),
            nullable=False,
        ),
        sa.Column("decision", _enum("safety_decision_kind", "allow", "deny"), nullable=False),
        sa.Column("rule_id", sa.String(128), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("length(trim(rule_id)) > 0", name="rule_id_not_blank"),
    )
    op.create_index("ix_safety_event_workspace_id", "safety_event", ["workspace_id"])
    op.create_index("ix_safety_event_task_id", "safety_event", ["task_id"])
    op.create_index("ix_safety_event_created_at", "safety_event", ["created_at"])

    # ----------------------------------------------------- append-only por trigger
    #
    # A semântica append-only de [02] §8 e §12 passa a ser verificada pelo **banco**, não
    # só pela disciplina do código de serviço.
    op.execute(
        """
        CREATE TRIGGER safety_event_no_update
        BEFORE UPDATE ON safety_event
        BEGIN
            SELECT RAISE(ABORT, 'safety_event e append-only: UPDATE recusado');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER safety_event_no_delete
        BEFORE DELETE ON safety_event
        BEGIN
            SELECT RAISE(ABORT, 'safety_event e append-only: DELETE recusado');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER run_append_only_after_finish
        BEFORE UPDATE ON run
        WHEN OLD.finished_at IS NOT NULL
        BEGIN
            SELECT RAISE(ABORT, 'run finalizado e append-only: UPDATE recusado');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS run_append_only_after_finish")
    op.execute("DROP TRIGGER IF EXISTS safety_event_no_delete")
    op.execute("DROP TRIGGER IF EXISTS safety_event_no_update")

    op.drop_table("safety_event")
    op.drop_table("audit_finding")
    op.drop_table("run")
    op.drop_table("context_manifest")
    op.drop_table("context_registry_entry")
    op.drop_table("workspace_task")
    op.drop_table("dev_workspace")
