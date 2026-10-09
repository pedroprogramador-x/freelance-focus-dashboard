"""E8.4.1 — admissão durável: `Run` aberto, métricas não medidas, slot único no banco

Três mudanças **aditivas** de contrato, todas verificadas pelo banco:

1. `run.status` ganha `running` — o Run **aberto** (admitido, não finalizado). Só ele tem
   `finished_at`/`duration_ms` nulos; todo outro status é final e imutável. Um *trigger*
   recusa `UPDATE` em Run final (antes só olhava `finished_at`), outro recusa trocar a
   identidade de um Run (id, chave, task, agente, propósito, início, posição no ciclo), e um
   terceiro recusa fechar um Run sem `finished_at` e `duration_ms`.
2. `run.files_changed`, `run.diff_added`, `run.diff_removed` deixam de ser `NOT NULL`. Antes
   um Run sem medição gravava `[]`/`0`, o que afirma "medido, nada mudou". `NULL` = não
   medido. Os três são tudo-ou-nada, e um Run aberto não afirma métrica nenhuma. A coleta real
   é da E8.4.4.
3. `uq_workspace_task_single_executing`: índice único **parcial** (`status = 'executing'`) —
   no máximo uma task `executing`, entre processos (`max_parallel_agents = 1`, [04] §7). É
   proteção de **admissão**: impede duas admissões concorrentes enquanto a task está
   `executing`, mas **não** prova que os recursos externos de uma execução (worktree, processos)
   foram encerrados depois de `cancelled`/`failed`. A reserva durável independente do estado
   terminal, o encerramento comprovado e a recuperação idempotente são obrigação da E8.4.5.
   `uq_run_open_control_per_task`: no máximo um Run de controle aberto por task.

## Reconstrução da tabela `run` — atômica (E841-AUD-001)

O SQLite não altera `CHECK`/`NOT NULL` no lugar; a tabela é recriada pelo procedimento oficial
(cria `run_new`, copia, descarta `run`, renomeia). Duas propriedades do ambiente decidem como:

* **DDL fora de transação é autocommit.** O Alembic trata o SQLite como DDL não transacional e
  abre só uma transação do *SQLAlchemy* por migration; o driver `sqlite3` no modo legado só
  inicia transação SQLite antes de DML. Sem cuidado, `DROP INDEX`/`CREATE TABLE` anteriores à
  primeira escrita de dados persistiam mesmo quando a migration falhava depois — o downgrade
  perdia o índice de exclusão global e deixava `run_new` pendurado.
* **`foreign_keys` precisa estar desligado** (senão `DROP TABLE run` apaga `audit_finding` em
  cascata), e o pragma não muda dentro de transação. Quem o desliga e o religa — fora da
  transação — é o `env.py` (E841-AUD-002).

Por isso `_begin_atomic` roda **antes de qualquer leitura ou DDL**: recusa se as FKs estiverem
ligadas ou se não houver transação dona do commit, e abre `BEGIN IMMEDIATE` quando não há
transação SQLite aberta (quando há — conexão com `BEGIN` explícito, transação externa —, a
migration roda dentro dela). Pré-checagens, todo DDL e DML, o `foreign_key_check` final e o
carimbo de versão do Alembic ficam numa **única** transação SQLite: falha em qualquer ponto
volta o banco ao estado inicial, índices e *triggers* inclusive. O commit é do Alembic (ou do
chamador da transação externa); a migration nunca comita.

Fail closed: Run final sem `finished_at` e mais de uma task `executing` recusam o upgrade, em
vez de "consertar" dado em silêncio. O DDL de `run_new` é um registro histórico: literais, não
importados de `app.db`. Modo *offline* (`--sql`) não é suportado.

Downgrade: recusa se existir Run `running` ou com métrica `NULL` — convertê-los em `[]`/`0`
reintroduziria exatamente a afirmação falsa que esta migration remove.

Revision ID: 0003_execution_admission
Revises: 0002_workspace_test_config
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from alembic import context, op
from sqlalchemy.engine import Connection

revision: str = "0003_execution_admission"
down_revision: str | None = "0002_workspace_test_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RUN_COLUMNS = (
    "id, invocation_id, task_id, subject_run_id, supersedes_run_id, context_manifest_id, "
    "agent, purpose, attempt_index, fix_round, provider, model, provider_adapter, "
    "adapter_version, transport, tool_profile_hash, status, started_at, finished_at, "
    "duration_ms, input_tokens, output_tokens, token_source, files_read, files_read_source, "
    "files_changed, diff_added, diff_removed, test_summary, worktree_path, base_commit, "
    "prompt_sha256, summary, error_summary, log_ref"
)

_RUN_TABLE_HEAD = """
CREATE TABLE run_new (
    id VARCHAR(36) NOT NULL,
    invocation_id VARCHAR(128) NOT NULL,
    task_id VARCHAR(36) NOT NULL,
    subject_run_id VARCHAR(36),
    supersedes_run_id VARCHAR(36),
    context_manifest_id VARCHAR(36),
    agent VARCHAR(12) NOT NULL,
    purpose VARCHAR(20) NOT NULL,
    attempt_index INTEGER NOT NULL,
    fix_round INTEGER NOT NULL,
    provider VARCHAR(64) NOT NULL,
    model VARCHAR(128),
    provider_adapter VARCHAR(128) NOT NULL,
    adapter_version VARCHAR(64),
    transport VARCHAR(7) NOT NULL,
    tool_profile_hash VARCHAR(64),
    status VARCHAR(11) NOT NULL,
    started_at DATETIME NOT NULL,
    finished_at DATETIME,
    duration_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    token_source VARCHAR(11) NOT NULL,
    files_read JSON,
    files_read_source VARCHAR(11) NOT NULL,
"""

_RUN_TABLE_CONSTRAINTS_COMMON = """
    test_summary JSON,
    worktree_path VARCHAR(4096),
    base_commit VARCHAR(40),
    prompt_sha256 VARCHAR(64),
    summary TEXT,
    error_summary TEXT,
    log_ref VARCHAR(4096),
    CONSTRAINT pk_run PRIMARY KEY (id),
    CONSTRAINT fk_run_task_id_workspace_task FOREIGN KEY(task_id) REFERENCES workspace_task (id)
            ON DELETE CASCADE,
    CONSTRAINT fk_run_subject_run_id_run FOREIGN KEY(subject_run_id) REFERENCES run (id) ON DELETE
            CASCADE,
    CONSTRAINT fk_run_supersedes_run_id_run FOREIGN KEY(supersedes_run_id) REFERENCES run (id) ON
            DELETE SET NULL,
    CONSTRAINT fk_run_context_manifest_id_context_manifest FOREIGN KEY(context_manifest_id)
            REFERENCES context_manifest (id) ON DELETE SET NULL,
    CONSTRAINT uq_run_invocation_id UNIQUE (invocation_id),
    CONSTRAINT ck_run_audit_requires_subject CHECK (purpose = 'execution' OR subject_run_id IS NOT
            NULL),
    CONSTRAINT ck_run_subject_not_self CHECK (subject_run_id IS NULL OR subject_run_id <> id),
    CONSTRAINT ck_run_supersedes_not_self CHECK (supersedes_run_id IS NULL OR supersedes_run_id <>
            id),
    CONSTRAINT ck_run_files_read_null_iff_unavailable CHECK ((files_read IS NULL) =
            (files_read_source = 'unavailable')),
    CONSTRAINT ck_run_duration_non_negative CHECK (duration_ms IS NULL OR duration_ms >= 0),
    CONSTRAINT ck_run_input_tokens_non_negative CHECK (input_tokens IS NULL OR input_tokens >= 0),
    CONSTRAINT ck_run_output_tokens_non_negative CHECK (output_tokens IS NULL OR output_tokens >=
            0),
    CONSTRAINT ck_run_attempt_index_non_negative CHECK (attempt_index >= 0),
    CONSTRAINT ck_run_fix_round_non_negative CHECK (fix_round >= 0),
    CONSTRAINT ck_run_run_agent CHECK (agent IN ('orchestrator', 'developer', 'auditor',
            'architect', 'researcher', 'test_runner')),
    CONSTRAINT ck_run_run_purpose CHECK (purpose IN ('execution', 'workflow_audit',
            'benchmark_evaluation')),
    CONSTRAINT ck_run_run_transport CHECK (transport IN ('cli', 'api', 'process')),
    CONSTRAINT ck_run_token_source CHECK (token_source IN ('reported', 'estimated', 'unavailable')),
    CONSTRAINT ck_run_files_read_source CHECK (files_read_source IN ('reported', 'inferred',
            'unavailable')),
"""

_RUN_NEW_SCHEMA = (
    _RUN_TABLE_HEAD
    + """
    files_changed JSON,
    diff_added INTEGER,
    diff_removed INTEGER,
"""
    + _RUN_TABLE_CONSTRAINTS_COMMON
    + """
    CONSTRAINT ck_run_diff_added_non_negative CHECK (diff_added IS NULL OR diff_added >= 0),
    CONSTRAINT ck_run_diff_removed_non_negative CHECK (diff_removed IS NULL OR diff_removed >= 0),
    CONSTRAINT ck_run_run_status CHECK (status IN ('running', 'ok', 'error', 'timeout',
            'cancelled', 'blocked', 'interrupted')),
    CONSTRAINT ck_run_open_run_has_no_closure CHECK (status <> 'running' OR (finished_at IS NULL
            AND duration_ms IS NULL)),
    CONSTRAINT ck_run_final_run_is_finished CHECK (status = 'running' OR finished_at IS NOT NULL),
    CONSTRAINT ck_run_git_metrics_all_or_none CHECK ((files_changed IS NULL) = (diff_added IS
            NULL) AND (diff_added IS NULL) = (diff_removed IS NULL)),
    CONSTRAINT ck_run_open_run_has_no_git_metrics CHECK (status <> 'running' OR files_changed IS
            NULL)
)
"""
)

#: O schema anterior (0001), para o downgrade.
_RUN_OLD_SCHEMA = (
    _RUN_TABLE_HEAD
    + """
    files_changed JSON NOT NULL,
    diff_added INTEGER NOT NULL,
    diff_removed INTEGER NOT NULL,
"""
    + _RUN_TABLE_CONSTRAINTS_COMMON
    + """
    CONSTRAINT ck_run_diff_added_non_negative CHECK (diff_added >= 0),
    CONSTRAINT ck_run_diff_removed_non_negative CHECK (diff_removed >= 0),
    CONSTRAINT ck_run_run_status CHECK (status IN ('ok', 'error', 'timeout', 'cancelled',
            'blocked', 'interrupted'))
)
"""
)

_RUN_INDEXES = (
    "CREATE INDEX ix_run_task_id ON run (task_id)",
    "CREATE INDEX ix_run_subject_run_id ON run (subject_run_id)",
    "CREATE INDEX ix_run_subject_purpose ON run (subject_run_id, purpose)",
)

_TRIGGER_APPEND_ONLY_V1 = """
CREATE TRIGGER run_append_only_after_finish
BEFORE UPDATE ON run
WHEN OLD.finished_at IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'run finalizado e append-only: UPDATE recusado');
END
"""

# Final = qualquer status que não seja `running`. Cobre também o status final sem `finished_at`.
_TRIGGER_APPEND_ONLY_V2 = """
CREATE TRIGGER run_append_only_after_finish
BEFORE UPDATE ON run
WHEN OLD.finished_at IS NOT NULL OR OLD.status <> 'running'
BEGIN
    SELECT RAISE(ABORT, 'run finalizado e append-only: UPDATE recusado');
END
"""

_TRIGGER_IDENTITY = """
CREATE TRIGGER run_identity_immutable
BEFORE UPDATE ON run
WHEN NEW.id <> OLD.id
  OR NEW.invocation_id <> OLD.invocation_id
  OR NEW.task_id <> OLD.task_id
  OR NEW.agent <> OLD.agent
  OR NEW.purpose <> OLD.purpose
  OR NEW.started_at <> OLD.started_at
  OR NEW.attempt_index <> OLD.attempt_index
  OR NEW.fix_round <> OLD.fix_round
BEGIN
    SELECT RAISE(ABORT, 'run: identidade imutavel: UPDATE recusado');
END
"""

_TRIGGER_CLOSURE = """
CREATE TRIGGER run_finalization_requires_closure
BEFORE UPDATE ON run
WHEN OLD.status = 'running' AND NEW.status <> 'running'
  AND (NEW.finished_at IS NULL OR NEW.duration_ms IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'run: fechar exige finished_at e duration_ms: UPDATE recusado');
END
"""

_OPEN_CONTROL_INDEX = (
    "CREATE UNIQUE INDEX uq_run_open_control_per_task ON run (task_id) "
    "WHERE agent = 'orchestrator' AND purpose = 'execution' AND status = 'running'"
)

_SINGLE_EXECUTING_INDEX = (
    "CREATE UNIQUE INDEX uq_workspace_task_single_executing ON workspace_task (status) "
    "WHERE status = 'executing'"
)


def _scalar(bind: Connection, sql: str) -> int:
    return int(bind.exec_driver_sql(sql).scalar() or 0)


def _begin_atomic(bind: Connection) -> None:
    """Garante que tudo o que a migration fizer a seguir esteja numa **única** transação SQLite.

    Roda antes de qualquer leitura ou DDL, e recusa (sem efeito) quando não há como garantir:

    * modo *offline*: não há conexão nem transação real;
    * `foreign_keys` ligado: o `DROP TABLE` apagaria filhas em cascata, e daqui não dá para
      desligar com efeito — o `env.py` desliga quando é seguro;
    * nenhuma transação SQLAlchemy aberta: ninguém comitaria nem desfaria o que vier.

    Sem transação SQLite aberta, abre `BEGIN IMMEDIATE` (lock de escrita já, sem disputar com
    gravações da aplicação no meio). Com uma aberta, a migration roda dentro dela.
    """
    if context.is_offline_mode():
        raise RuntimeError("a migration 0003 não suporta modo offline (`--sql`)")
    raw = bind.connection.dbapi_connection
    if not isinstance(raw, sqlite3.Connection):
        raise RuntimeError("a migration 0003 exige uma conexão DBAPI `sqlite3`")
    if raw.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise RuntimeError(
            "`PRAGMA foreign_keys` está ligado: recriar `run` apagaria `audit_finding` em "
            "cascata, e o pragma não pode ser desligado dentro de uma transação. Rode pelo "
            "`env.py` do projeto com uma conexão sem transação aberta (ele desliga e religa as "
            "FKs com segurança). Nada foi alterado."
        )
    if not bind.in_transaction():
        raise RuntimeError("a migration 0003 exige uma transação do Alembic ou do chamador")
    if not raw.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _verify_foreign_keys(bind: Connection) -> None:
    """As FKs ficaram desligadas durante a reconstrução: a integridade é conferida aqui."""
    violations = bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(
            f"{len(violations)} violação(ões) de chave estrangeira após a reconstrução; "
            "migração desfeita"
        )


def _rebuild_run(bind: Connection, schema: str) -> None:
    """Procedimento oficial do SQLite: cria, copia, confere FKs, descarta, renomeia.

    Só é chamada depois de `_begin_atomic`: cada instrução daqui é desfeita junto com o resto.
    """
    bind.exec_driver_sql("DROP TABLE IF EXISTS run_new")
    bind.exec_driver_sql(schema)
    bind.exec_driver_sql(
        f"INSERT INTO run_new ({_RUN_COLUMNS}) SELECT {_RUN_COLUMNS} FROM run"  # noqa: S608
    )
    violations = bind.exec_driver_sql("PRAGMA foreign_key_check(run_new)").fetchall()
    if violations:
        raise RuntimeError(
            f"{len(violations)} linha(s) de `run` violam chave estrangeira; migração desfeita"
        )
    bind.exec_driver_sql("DROP TABLE run")
    bind.exec_driver_sql("ALTER TABLE run_new RENAME TO run")
    for statement in _RUN_INDEXES:
        bind.exec_driver_sql(statement)


def upgrade() -> None:
    bind = op.get_bind()
    _begin_atomic(bind)

    # Fail closed ANTES de qualquer DDL — e já dentro da transação.
    unfinished = _scalar(bind, "SELECT COUNT(*) FROM run WHERE finished_at IS NULL")
    if unfinished:
        raise RuntimeError(
            f"{unfinished} Run(s) sem `finished_at` no banco: o status antigo não distingue "
            "'aberto' de 'final', e a nova invariante (final ⇒ finished_at) não pode ser "
            "aplicada sem adivinhar. Finalize ou remova esses Runs e rode a migração de novo."
        )
    executing = _scalar(bind, "SELECT COUNT(*) FROM workspace_task WHERE status = 'executing'")
    if executing > 1:
        raise RuntimeError(
            f"{executing} tasks `executing`: `max_parallel_agents = 1` não admite mais de uma. "
            "Suba o backend para a `reconcile_on_startup` marcá-las `failed(interrupted)` "
            "antes de migrar."
        )

    _rebuild_run(bind, _RUN_NEW_SCHEMA)
    bind.exec_driver_sql(_OPEN_CONTROL_INDEX)
    bind.exec_driver_sql(_TRIGGER_APPEND_ONLY_V2)
    bind.exec_driver_sql(_TRIGGER_IDENTITY)
    bind.exec_driver_sql(_TRIGGER_CLOSURE)
    bind.exec_driver_sql(_SINGLE_EXECUTING_INDEX)
    _verify_foreign_keys(bind)


def downgrade() -> None:
    bind = op.get_bind()
    _begin_atomic(bind)

    open_runs = _scalar(bind, "SELECT COUNT(*) FROM run WHERE status = 'running'")
    unmeasured = _scalar(bind, "SELECT COUNT(*) FROM run WHERE files_changed IS NULL")
    if open_runs or unmeasured:
        raise RuntimeError(
            f"downgrade recusado: {open_runs} Run(s) `running` e {unmeasured} Run(s) com "
            "métrica do Git Runtime não medida (`NULL`). O schema anterior só representa "
            "'medido': convertê-los em `[]`/`0` afirmaria uma medição que não aconteceu."
        )

    # Dentro da mesma transação da reconstrução: se a cópia falhar, o índice volta.
    bind.exec_driver_sql("DROP INDEX IF EXISTS uq_workspace_task_single_executing")
    # Os triggers e índices de `run` caem junto com a tabela.
    _rebuild_run(bind, _RUN_OLD_SCHEMA)
    bind.exec_driver_sql(_TRIGGER_APPEND_ONLY_V1)
    _verify_foreign_keys(bind)
