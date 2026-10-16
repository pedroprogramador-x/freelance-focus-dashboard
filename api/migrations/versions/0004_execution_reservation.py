"""E8.4.5 / I3 — reserva durável de execução, ledger de lançamentos e evidência.

Três tabelas **aditivas** e as proteções estruturais que o SQLite consegue verificar sobre linhas.
Nenhuma tabela existente é recriada (a migration 0003 já reconstruiu `run`); só entram *triggers* de
`DELETE` em `run`, `workspace_task` e `dev_workspace`.

## O que é estrutural e o que é externo

* **Estrutural** (este arquivo): o que o banco prova olhando linhas — unicidade global do slot,
  arestas de estado e de fase, formas legais de término, proveniência no instante da escrita,
  evidência append-only, e as barreiras R1–R7 do predicado de liberação (`reservation_release_guard`).
* **Externo** (nunca aqui): a verdade do que foi observado no SO (árvore morta, sonda, drenagem), a
  posse real do lock do SO, o **lease** (C7) e a **atualidade do histórico** (C7.7, `HistoryCurrency`).
  Por isso o gatilho de release é um **piso**: ele equivale a `decide_release` do I1 *com lease
  consistente e histórico comprovado atual*. `decide_release` continua sendo o predicado completo, e
  quem libera (`release_reservation`, I5) roda os dois; discordância aborta.

## Tabelas

`execution_reservation` — uma linha por tentativa, identificada por `control_run_id` (UNIQUE). **Sem
FK** para task, workspace ou Run: a reserva liberada sobrevive à purga e fica como histórico. A
unicidade **global** é `slot_key` constante (`CHECK slot_key = 1`) + índice único parcial
`WHERE state <> 'released'`; não há unicidade em `task_id`/`workspace_id`.
`execution_launch` — o ledger. `execution_evidence` — append-only, `seq` é a ordem total.

## Proveniência (C8)

Registrada **no instante da escrita** e imutável depois: um término gravado pelo dono antes da
adoção continua válido depois dela. `UPDATE` de launch exige reserva `active`/`draining` **sem
adoção**; `ended_*` exige `ended_by_token = owner_token` agora. A adoção só acontece na mesma
instrução que leva a reserva a `teardown_pending` (C8.5). Evidência observada ao vivo só entra sem
adoção; quiescência só pelo adotante.

## Cascatas e purga

Filhas têm FK `ON DELETE RESTRICT` para a reserva. `DELETE` de reserva, launch e evidência é sempre
recusado; `DELETE` de Run, task e workspace é recusado enquanto existir reserva **não liberada** da
mesma task/workspace (o desenho não depende de gatilho em cascata, mas o SQLite o dispara — S2).
Reserva `released` não bloqueia nada.

## Atomicidade, upgrade e downgrade

`_begin_atomic` abre (ou reaproveita) **uma** transação SQLite antes de qualquer leitura ou DDL: o
SQLite tem DDL transacional, então falha em qualquer ponto volta o banco ao estado inicial. Fail
closed: o upgrade **recusa** se existir task `executing` ou Run `running` — sem reserva não há como
provar o encerramento do que está em voo, e o recovery de reserva só existe a partir daqui.

O downgrade recusa se **qualquer** das três tabelas tiver linhas, mesmo que todas as reservas
estejam `released`: o histórico de encerramento é trilha de auditoria e não há exclusão forçada
aqui. Só um banco com as três tabelas vazias é rebaixado (P2-I3-03).

## `INSERT OR REPLACE`, UPSERT e `seq` (P1-I3-01, P1-I3-02)

`INSERT OR REPLACE` resolve um conflito de PK/UNIQUE **apagando** a linha existente, e esse DELETE
não dispara os triggers `BEFORE DELETE` (só com `PRAGMA recursive_triggers=ON`, por conexão e
desligado por padrão). Os `BEFORE INSERT` das três tabelas recusam, antes da resolução do conflito,
qualquer INSERT que colidiria por `id`/`seq`, `control_run_id` ou pelo slot global — vale para
`INSERT`, `OR REPLACE`, `OR IGNORE` e UPSERT, com `recursive_triggers` ligado ou não. `seq` explícito
tem de ser maior que o maior já gravado; no `BEFORE INSERT` o identificador automático aparece como
`-1` (só o `AFTER` vê o definitivo), por isso um `AFTER INSERT` com `RAISE(ABORT)` reafirma que o
`seq` final é positivo e estritamente crescente, desfazendo a inserção inteira se não for.

Os literais abaixo são registro histórico: nada é importado de `app.db`, e os testes cruzam os
vocabulários com os enums do contrato (I1).

Revision ID: 0004_execution_reservation
Revises: 0003_execution_admission
Create Date: 2026-10-15
"""

# DDL e triggers são literais SQL longos por natureza (E501) e montados só de constantes deste
# arquivo — nenhuma entrada externa (S608).
# ruff: noqa: E501, S608

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from alembic import context, op
from sqlalchemy.engine import Connection

revision: str = "0004_execution_reservation"
down_revision: str | None = "0003_execution_admission"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ----------------------------------------------------------------------------- vocabulário


def _in(values: Sequence[str]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


STATES = ("active", "draining", "teardown_pending", "unproven", "released")
PHASE_TAGS = ("preparation", "developer", "test_runner", "other")
PLATFORMS = ("win32", "posix")
PROFILES = ("win_job_v1", "posix_pgrp_v1")
PHASES = ("intent", "created", "contained", "ended_confirmed", "ended_unconfirmed")
END_BASES = (
    "refused_before_creation",
    "tree_confirmed_dead",
    "tree_unconfirmed",
    "popen_raised",
    "ledger_write_failed",
)
SCOPES = ("launch", "in_process", "binding")
SUBJECTS = ("control", "developer", "test_runner")

_NEG_LAUNCH = ("probe_present", "launch_unconfirmed_observed")
_NEG_PROC = ("drain_timeout", "drain_reentrant", "quiescence_unproven")
_NEG_BIND = ("binding_unproven",)
_LIVE_PROC = ("executor_drained",)
_LIVE_BIND = ("binding_released_observed", "binding_revoked_observed")

LAUNCH_BASES = (
    "launch_start_failed_proven",
    "identity_probe_absent",
    "job_guarantee_verified",
    "machine_restarted_since_declaration",
    *_NEG_LAUNCH,
)
PROCESS_BASES = ("executor_drained", "cooperative_owner_quiescence", *_NEG_PROC)
BINDING_BASES = (
    "binding_released_observed",
    "binding_revoked_observed",
    "cooperative_owner_quiescence",
    *_NEG_BIND,
)
ALL_BASES = tuple(dict.fromkeys((*LAUNCH_BASES, *PROCESS_BASES, *BINDING_BASES)))
#: Bases observadas ao vivo pelo dono original: só enquanto a reserva não foi adotada.
LIVE_BASES = (*_LIVE_PROC, *_LIVE_BIND, "launch_start_failed_proven")

_TERMINAL = ("ended_confirmed", "ended_unconfirmed")


# ----------------------------------------------------------------------------- tabelas

_RESERVATION_TABLE = f"""
CREATE TABLE execution_reservation (
    id VARCHAR(36) NOT NULL,
    slot_key INTEGER NOT NULL DEFAULT 1,
    task_id VARCHAR(36) NOT NULL,
    workspace_id VARCHAR(36) NOT NULL,
    control_run_id VARCHAR(36) NOT NULL,
    worktree_ref VARCHAR(128) NOT NULL,
    state VARCHAR(16) NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    owner_token VARCHAR(64) NOT NULL,
    adopted_by_token VARCHAR(64),
    logical_key VARCHAR(128) NOT NULL,
    physical_key VARCHAR(128) NOT NULL,
    created_at DATETIME NOT NULL,
    developer_declared_at DATETIME,
    drain_requested_at DATETIME,
    draining_at DATETIME,
    ever_unproven BOOLEAN NOT NULL DEFAULT 0,
    first_unproven_at DATETIME,
    ack_at DATETIME,
    ack_reason VARCHAR(255),
    released_at DATETIME,
    released_by_token VARCHAR(64),
    release_evidence_hash VARCHAR(64),
    CONSTRAINT pk_execution_reservation PRIMARY KEY (id),
    CONSTRAINT uq_execution_reservation_control_run_id UNIQUE (control_run_id),
    CONSTRAINT ck_execution_reservation_slot_key CHECK (slot_key = 1),
    CONSTRAINT ck_execution_reservation_state_vocabulary CHECK (state IN {_in(STATES)}),
    CONSTRAINT ck_execution_reservation_version_positive CHECK (version >= 1),
    CONSTRAINT ck_execution_reservation_ever_unproven_boolean CHECK (ever_unproven IN (0, 1)),
    CONSTRAINT ck_execution_reservation_text_not_blank CHECK (length(trim(owner_token)) > 0 AND (adopted_by_token IS NULL OR length(trim(adopted_by_token)) > 0) AND length(trim(logical_key)) > 0 AND length(trim(physical_key)) > 0 AND length(trim(worktree_ref)) > 0 AND (released_by_token IS NULL OR length(trim(released_by_token)) > 0) AND (release_evidence_hash IS NULL OR length(trim(release_evidence_hash)) > 0) AND (ack_reason IS NULL OR length(trim(ack_reason)) > 0)),
    CONSTRAINT ck_execution_reservation_adopter_is_not_owner CHECK (adopted_by_token IS NULL OR adopted_by_token <> owner_token),
    CONSTRAINT ck_execution_reservation_worktree_ref_is_name_only CHECK (instr(worktree_ref, '/') = 0 AND instr(worktree_ref, '\\') = 0 AND instr(worktree_ref, ':') = 0),
    CONSTRAINT ck_execution_reservation_unproven_sticky CHECK (state <> 'unproven' OR ever_unproven = 1),
    CONSTRAINT ck_execution_reservation_first_unproven_iff_ever CHECK ((ever_unproven = 1) = (first_unproven_at IS NOT NULL)),
    CONSTRAINT ck_execution_reservation_ack_pair CHECK ((ack_at IS NULL) = (ack_reason IS NULL)),
    CONSTRAINT ck_execution_reservation_ack_history CHECK (ever_unproven = 0 OR state <> 'released' OR (ack_at IS NOT NULL AND ack_at >= first_unproven_at)),
    CONSTRAINT ck_execution_reservation_released_shape CHECK ((state = 'released') = (released_at IS NOT NULL) AND (state = 'released') = (released_by_token IS NOT NULL) AND (state = 'released') = (release_evidence_hash IS NOT NULL)),
    CONSTRAINT ck_execution_reservation_drain_shape CHECK ((draining_at IS NULL OR drain_requested_at IS NOT NULL) AND (state <> 'draining' OR draining_at IS NOT NULL)),
    CONSTRAINT ck_execution_reservation_adoption_state CHECK (adopted_by_token IS NULL OR state IN ('teardown_pending', 'unproven', 'released'))
)
"""

_LAUNCH_TABLE = f"""
CREATE TABLE execution_launch (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    reservation_id VARCHAR(36) NOT NULL,
    phase_tag VARCHAR(16) NOT NULL,
    exe_name VARCHAR(255) NOT NULL,
    platform VARCHAR(8) NOT NULL,
    containment_profile VARCHAR(16) NOT NULL,
    phase VARCHAR(18) NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    pid INTEGER,
    start_token VARCHAR(64),
    end_basis VARCHAR(24),
    ended_by_token VARCHAR(64),
    created_at DATETIME NOT NULL,
    CONSTRAINT fk_execution_launch_reservation_id_execution_reservation FOREIGN KEY(reservation_id) REFERENCES execution_reservation (id) ON DELETE RESTRICT,
    CONSTRAINT ck_execution_launch_phase_tag_vocabulary CHECK (phase_tag IN {_in(PHASE_TAGS)}),
    CONSTRAINT ck_execution_launch_platform_vocabulary CHECK (platform IN {_in(PLATFORMS)}),
    CONSTRAINT ck_execution_launch_profile_vocabulary CHECK (containment_profile IN {_in(PROFILES)}),
    CONSTRAINT ck_execution_launch_phase_vocabulary CHECK (phase IN {_in(PHASES)}),
    CONSTRAINT ck_execution_launch_end_basis_vocabulary CHECK (end_basis IS NULL OR end_basis IN {_in(END_BASES)}),
    CONSTRAINT ck_execution_launch_version_positive CHECK (version >= 1),
    CONSTRAINT ck_execution_launch_profile_matches_platform CHECK ((platform = 'win32' AND containment_profile = 'win_job_v1') OR (platform = 'posix' AND containment_profile = 'posix_pgrp_v1')),
    CONSTRAINT ck_execution_launch_pid_with_token CHECK ((pid IS NULL) = (start_token IS NULL) AND (pid IS NULL OR pid >= 1)),
    CONSTRAINT ck_execution_launch_text_not_blank CHECK (length(trim(exe_name)) > 0 AND (start_token IS NULL OR length(trim(start_token)) > 0) AND (ended_by_token IS NULL OR length(trim(ended_by_token)) > 0)),
    CONSTRAINT ck_execution_launch_exe_name_only CHECK (instr(exe_name, '/') = 0 AND instr(exe_name, '\\') = 0),
    CONSTRAINT ck_execution_launch_pid_after_created CHECK (phase NOT IN ('created', 'contained') OR (pid IS NOT NULL AND start_token IS NOT NULL)),
    CONSTRAINT ck_execution_launch_intent_shape CHECK (phase <> 'intent' OR (pid IS NULL AND end_basis IS NULL AND ended_by_token IS NULL)),
    CONSTRAINT ck_execution_launch_open_shape CHECK (phase NOT IN ('created', 'contained') OR (end_basis IS NULL AND ended_by_token IS NULL)),
    CONSTRAINT ck_execution_launch_end_basis_by_phase CHECK ((end_basis IS NULL) = (phase IN ('intent', 'created', 'contained')) AND (phase <> 'ended_confirmed' OR end_basis IN ('refused_before_creation', 'tree_confirmed_dead')) AND (phase <> 'ended_unconfirmed' OR end_basis IN ('tree_unconfirmed', 'popen_raised', 'ledger_write_failed'))),
    CONSTRAINT ck_execution_launch_end_shape CHECK (phase <> 'ended_confirmed' OR (end_basis = 'refused_before_creation' AND pid IS NULL AND start_token IS NULL) OR (end_basis = 'tree_confirmed_dead' AND pid IS NOT NULL AND start_token IS NOT NULL)),
    CONSTRAINT ck_execution_launch_unconfirmed_shape CHECK (phase <> 'ended_unconfirmed' OR ((end_basis <> 'tree_unconfirmed' OR pid IS NOT NULL) AND (end_basis <> 'popen_raised' OR pid IS NULL))),
    CONSTRAINT ck_execution_launch_terminal_has_writer CHECK ((phase IN ('ended_confirmed', 'ended_unconfirmed')) = (ended_by_token IS NOT NULL))
)
"""

_EVIDENCE_TABLE = f"""
CREATE TABLE execution_evidence (
    seq INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    reservation_id VARCHAR(36) NOT NULL,
    launch_id INTEGER,
    launch_version INTEGER,
    scope VARCHAR(10) NOT NULL,
    subject VARCHAR(12),
    basis VARCHAR(40) NOT NULL,
    writer_token VARCHAR(64) NOT NULL,
    logical_key VARCHAR(128),
    physical_key VARCHAR(128),
    lock_files_match BOOLEAN NOT NULL DEFAULT 0,
    profile_ok BOOLEAN NOT NULL DEFAULT 0,
    owner_quiescence_ok BOOLEAN NOT NULL DEFAULT 0,
    root_probe_absent BOOLEAN NOT NULL DEFAULT 0,
    boot_changed BOOLEAN NOT NULL DEFAULT 0,
    supervision_confirmed BOOLEAN NOT NULL DEFAULT 0,
    observed_at DATETIME NOT NULL,
    CONSTRAINT fk_execution_evidence_reservation_id_execution_reservation FOREIGN KEY(reservation_id) REFERENCES execution_reservation (id) ON DELETE RESTRICT,
    CONSTRAINT fk_execution_evidence_launch_id_execution_launch FOREIGN KEY(launch_id) REFERENCES execution_launch (id) ON DELETE RESTRICT,
    CONSTRAINT ck_execution_evidence_scope_vocabulary CHECK (scope IN {_in(SCOPES)}),
    CONSTRAINT ck_execution_evidence_subject_vocabulary CHECK (subject IS NULL OR subject IN {_in(SUBJECTS)}),
    CONSTRAINT ck_execution_evidence_basis_vocabulary CHECK (basis IN {_in(ALL_BASES)}),
    CONSTRAINT ck_execution_evidence_basis_by_scope CHECK ((scope = 'launch' AND basis IN {_in(LAUNCH_BASES)}) OR (scope = 'in_process' AND basis IN {_in(PROCESS_BASES)}) OR (scope = 'binding' AND basis IN {_in(BINDING_BASES)})),
    CONSTRAINT ck_execution_evidence_launch_fields_iff_launch CHECK ((scope = 'launch') = (launch_id IS NOT NULL AND launch_version IS NOT NULL) AND (launch_id IS NULL) = (launch_version IS NULL)),
    CONSTRAINT ck_execution_evidence_subject_iff_binding CHECK ((scope = 'binding') = (subject IS NOT NULL)),
    CONSTRAINT ck_execution_evidence_launch_version_positive CHECK (launch_version IS NULL OR launch_version >= 1),
    CONSTRAINT ck_execution_evidence_flags_boolean CHECK (lock_files_match IN (0, 1) AND profile_ok IN (0, 1) AND owner_quiescence_ok IN (0, 1) AND root_probe_absent IN (0, 1) AND boot_changed IN (0, 1) AND supervision_confirmed IN (0, 1)),
    CONSTRAINT ck_execution_evidence_text_not_blank CHECK (length(trim(writer_token)) > 0 AND (logical_key IS NULL OR length(trim(logical_key)) > 0) AND (physical_key IS NULL OR length(trim(physical_key)) > 0)),
    CONSTRAINT ck_execution_evidence_start_failed_needs_supervision CHECK (basis <> 'launch_start_failed_proven' OR supervision_confirmed = 1)
)
"""

_INDEXES = (
    "CREATE UNIQUE INDEX uq_execution_reservation_unreleased "
    "ON execution_reservation (slot_key) WHERE state <> 'released'",
    "CREATE INDEX ix_execution_reservation_task_id ON execution_reservation (task_id)",
    "CREATE INDEX ix_execution_reservation_workspace_id ON execution_reservation (workspace_id)",
    "CREATE INDEX ix_execution_launch_reservation_id ON execution_launch (reservation_id)",
    "CREATE INDEX ix_execution_evidence_reservation_id ON execution_evidence (reservation_id)",
    "CREATE INDEX ix_execution_evidence_launch_id ON execution_evidence (launch_id)",
)


# ----------------------------------------------------------------------------- triggers

_RAISE = "SELECT RAISE(ABORT, '{code}') WHERE {cond};"

_RESERVATION_EDGES = (
    "(OLD.state = 'active' AND NEW.state IN ('draining', 'teardown_pending')) OR "
    "(OLD.state = 'draining' AND NEW.state IN ('teardown_pending', 'released')) OR "
    "(OLD.state = 'teardown_pending' AND NEW.state IN ('unproven', 'released')) OR "
    "(OLD.state = 'unproven' AND NEW.state IN ('teardown_pending', 'released'))"
)

#: A matriz completa `(origem, destino, end_basis)` do contrato I1 (`_LAUNCH_TRANSITIONS`).
_LAUNCH_EDGES = (
    "(OLD.phase = 'intent' AND NEW.phase = 'created' AND NEW.end_basis IS NULL) OR "
    "(OLD.phase = 'created' AND NEW.phase = 'contained' AND NEW.end_basis IS NULL) OR "
    "(OLD.phase = 'intent' AND NEW.phase = 'ended_confirmed' AND NEW.end_basis = 'refused_before_creation') OR "
    "(OLD.phase IN ('created', 'contained') AND NEW.phase = 'ended_confirmed' AND NEW.end_basis = 'tree_confirmed_dead') OR "
    "(OLD.phase = 'intent' AND NEW.phase = 'ended_unconfirmed' AND NEW.end_basis IN ('popen_raised', 'ledger_write_failed')) OR "
    "(OLD.phase IN ('created', 'contained') AND NEW.phase = 'ended_unconfirmed' AND NEW.end_basis IN ('tree_unconfirmed', 'ledger_write_failed'))"
)

_RESERVATION_GUARDS = "\n".join(
    [
        "CREATE TRIGGER execution_reservation_insert_guard",
        "BEFORE INSERT ON execution_reservation",
        "BEGIN",
        # P1-I3-01: `INSERT OR REPLACE` apaga a linha em conflito SEM disparar os triggers de
        # DELETE (salvo `recursive_triggers=ON`, que é por conexão e desligado por padrão). Todo
        # INSERT que substituiria uma linha é recusado AQUI, antes da resolução do conflito.
        _RAISE.format(
            code="reservation_insert:id_conflict",
            cond="EXISTS (SELECT 1 FROM execution_reservation x WHERE x.id = NEW.id)",
        ),
        _RAISE.format(
            code="reservation_insert:control_run_id_conflict",
            cond=(
                "EXISTS (SELECT 1 FROM execution_reservation x "
                "WHERE x.control_run_id = NEW.control_run_id)"
            ),
        ),
        _RAISE.format(
            code="reservation_insert:slot_conflict",
            cond=(
                "EXISTS (SELECT 1 FROM execution_reservation x "
                "WHERE x.slot_key = NEW.slot_key AND x.state <> 'released')"
            ),
        ),
        _RAISE.format(
            code="reservation_insert:not_a_fresh_active_row",
            cond=(
                "NEW.state <> 'active' OR NEW.version <> 1 OR NEW.adopted_by_token IS NOT NULL "
                "OR NEW.ever_unproven <> 0 OR NEW.first_unproven_at IS NOT NULL "
                "OR NEW.ack_at IS NOT NULL OR NEW.ack_reason IS NOT NULL "
                "OR NEW.released_at IS NOT NULL OR NEW.released_by_token IS NOT NULL "
                "OR NEW.release_evidence_hash IS NOT NULL OR NEW.developer_declared_at IS NOT NULL "
                "OR NEW.drain_requested_at IS NOT NULL OR NEW.draining_at IS NOT NULL"
            ),
        ),
        "END",
    ]
)

_RESERVATION_NO_DELETE = """
CREATE TRIGGER execution_reservation_no_delete
BEFORE DELETE ON execution_reservation
BEGIN
    SELECT RAISE(ABORT, 'reservation_delete:append_only');
END
"""

_RESERVATION_UPDATE_GUARD = "\n".join(
    [
        "CREATE TRIGGER execution_reservation_update_guard",
        "BEFORE UPDATE ON execution_reservation",
        "BEGIN",
        _RAISE.format(code="reservation_update:released_immutable", cond="OLD.state = 'released'"),
        _RAISE.format(
            code="reservation_update:edge_illegal",
            cond=f"NEW.state IS NOT OLD.state AND NOT ({_RESERVATION_EDGES})",
        ),
        _RAISE.format(
            code="reservation_update:identity_immutable",
            cond=(
                "NEW.id IS NOT OLD.id OR NEW.slot_key IS NOT OLD.slot_key "
                "OR NEW.task_id IS NOT OLD.task_id OR NEW.workspace_id IS NOT OLD.workspace_id "
                "OR NEW.control_run_id IS NOT OLD.control_run_id "
                "OR NEW.worktree_ref IS NOT OLD.worktree_ref "
                "OR NEW.owner_token IS NOT OLD.owner_token "
                "OR NEW.logical_key IS NOT OLD.logical_key "
                "OR NEW.physical_key IS NOT OLD.physical_key OR NEW.created_at IS NOT OLD.created_at"
            ),
        ),
        _RAISE.format(
            code="reservation_update:version_step", cond="NEW.version IS NOT OLD.version + 1"
        ),
        _RAISE.format(
            code="reservation_update:adoption_monotone",
            cond="OLD.adopted_by_token IS NOT NULL AND NEW.adopted_by_token IS NOT OLD.adopted_by_token",
        ),
        _RAISE.format(
            code="reservation_update:adoption_edge",
            cond=(
                "OLD.adopted_by_token IS NULL AND NEW.adopted_by_token IS NOT NULL "
                "AND NEW.state <> 'teardown_pending'"
            ),
        ),
        _RAISE.format(
            code="reservation_update:sticky_unproven",
            cond="OLD.ever_unproven = 1 AND NEW.ever_unproven = 0",
        ),
        _RAISE.format(
            code="reservation_update:history_immutable",
            cond=(
                "(OLD.first_unproven_at IS NOT NULL AND NEW.first_unproven_at IS NOT OLD.first_unproven_at) "
                "OR (OLD.ack_at IS NOT NULL AND NEW.ack_at IS NOT OLD.ack_at) "
                "OR (OLD.ack_reason IS NOT NULL AND NEW.ack_reason IS NOT OLD.ack_reason)"
            ),
        ),
        _RAISE.format(
            code="reservation_update:set_once",
            cond=(
                "(OLD.developer_declared_at IS NOT NULL AND NEW.developer_declared_at IS NOT OLD.developer_declared_at) "
                "OR (OLD.drain_requested_at IS NOT NULL AND NEW.drain_requested_at IS NOT OLD.drain_requested_at) "
                "OR (OLD.draining_at IS NOT NULL AND NEW.draining_at IS NOT OLD.draining_at)"
            ),
        ),
        "END",
    ]
)


def _cycle_run(alias: str = "r") -> str:
    """Os Runs do ciclo: a identidade vem de `control_run_id`, o papel vem de `agent`."""
    return (
        f"{alias}.task_id = NEW.task_id AND ({alias}.id = NEW.control_run_id "
        f"OR {alias}.invocation_id = NEW.control_run_id || ':developer' "
        f"OR {alias}.invocation_id = NEW.control_run_id || ':test_runner')"
    )


def _run_of(agent: str) -> str:
    return f"EXISTS (SELECT 1 FROM run r WHERE {_cycle_run()} AND r.agent = '{agent}')"


def _launch_of(tag: str) -> str:
    return (
        "EXISTS (SELECT 1 FROM execution_launch l "
        f"WHERE l.reservation_id = NEW.id AND l.phase_tag = '{tag}')"
    )


_DEV_RUN = _run_of("developer")
_RUNNER_RUN = _run_of("test_runner")
_DEV_LAUNCH = _launch_of("developer")
_RUNNER_LAUNCH = _launch_of("test_runner")
_RUNNER_PRESENT = f"({_RUNNER_RUN} OR {_RUNNER_LAUNCH})"
_DEV_PRESENT = f"(NEW.developer_declared_at IS NOT NULL OR {_DEV_RUN} OR {_DEV_LAUNCH})"


def _closed(launch: str) -> str:
    """O launch está encerrado (R1): término confirmado do dono, ou evidência qualificadora."""
    neg = _in(_NEG_LAUNCH)
    return f"""(
 ({launch}.phase = 'ended_confirmed' AND {launch}.ended_by_token = NEW.owner_token
   AND NOT EXISTS (SELECT 1 FROM execution_evidence n WHERE n.launch_id = {launch}.id AND n.basis IN {neg}
                    AND n.launch_version >= {launch}.version))
 OR EXISTS (SELECT 1 FROM execution_evidence e WHERE e.launch_id = {launch}.id AND e.launch_version = {launch}.version
   AND e.reservation_id = NEW.id AND (
        ({launch}.phase = 'ended_unconfirmed' AND {launch}.end_basis = 'popen_raised'
           AND e.basis = 'launch_start_failed_proven' AND e.supervision_confirmed = 1
           AND e.writer_token = NEW.owner_token)
     OR ({launch}.phase = 'created' AND {launch}.platform = 'win32' AND e.basis = 'identity_probe_absent'
           AND (e.writer_token = NEW.owner_token OR e.writer_token IS NEW.adopted_by_token))
     OR ({launch}.phase = 'contained' AND {launch}.platform = 'win32'
           AND {launch}.containment_profile = 'win_job_v1'
           AND e.basis = 'job_guarantee_verified' AND e.profile_ok = 1 AND e.owner_quiescence_ok = 1
           AND e.root_probe_absent = 1 AND NEW.adopted_by_token IS NOT NULL
           AND e.writer_token = NEW.adopted_by_token AND e.logical_key IS NEW.logical_key
           AND e.physical_key IS NEW.physical_key AND e.lock_files_match = 1)
     OR (e.basis = 'machine_restarted_since_declaration' AND e.boot_changed = 1
           AND {launch}.phase <> 'ended_confirmed'
           AND (e.writer_token = NEW.owner_token OR e.writer_token IS NEW.adopted_by_token))
   )
   AND NOT EXISTS (SELECT 1 FROM execution_evidence n WHERE n.launch_id = {launch}.id AND n.basis IN {neg} AND n.seq > e.seq))
)"""


def _scope_ok(scope: str, subject: str | None, live: Sequence[str], neg: Sequence[str]) -> str:
    """R3/R4: evidência viva do dono, ou quiescência do adotante, sem negativa posterior."""
    pick = f"AND e.subject = '{subject}'" if subject else ""
    pick_neg = f"AND n.subject = '{subject}'" if subject else ""
    return f"""EXISTS (SELECT 1 FROM execution_evidence e WHERE e.reservation_id = NEW.id AND e.scope = '{scope}' {pick}
  AND ((e.basis IN {_in(live)} AND e.writer_token = NEW.owner_token)
    OR (e.basis = 'cooperative_owner_quiescence' AND NEW.adopted_by_token IS NOT NULL
        AND e.writer_token = NEW.adopted_by_token AND e.logical_key IS NEW.logical_key
        AND e.physical_key IS NEW.physical_key AND e.lock_files_match = 1))
  AND NOT EXISTS (SELECT 1 FROM execution_evidence n WHERE n.reservation_id = NEW.id AND n.scope = '{scope}' {pick_neg}
                  AND n.basis IN {_in(neg)} AND n.seq > e.seq))"""


def _blocker(code: str, cond: str) -> str:
    return _RAISE.format(code=f"release_blocked:{code}", cond=cond)


def _count_role(agent: str) -> str:
    return f"(SELECT COUNT(*) FROM run r WHERE {_cycle_run()} AND r.agent = '{agent}')"


_RELEASE_GUARD = "\n".join(
    [
        "CREATE TRIGGER execution_reservation_release_guard",
        "BEFORE UPDATE ON execution_reservation",
        "WHEN NEW.state = 'released' AND OLD.state <> 'released'",
        "BEGIN",
        # R2 — Runs do ciclo
        _blocker(
            "run_open",
            f"EXISTS (SELECT 1 FROM run r WHERE {_cycle_run()} AND r.status = 'running')",
        ),
        _blocker("run_control_missing", f"NOT {_run_of('orchestrator')}"),
        _blocker(
            "run_role_unknown",
            f"EXISTS (SELECT 1 FROM run r WHERE {_cycle_run()} "
            "AND r.agent NOT IN ('orchestrator', 'developer', 'test_runner'))",
        ),
        _blocker(
            "run_duplicated",
            f"{_count_role('orchestrator')} > 1 OR {_count_role('developer')} > 1 "
            f"OR {_count_role('test_runner')} > 1",
        ),
        _blocker(
            "run_identity_mismatch",
            f"EXISTS (SELECT 1 FROM run r WHERE {_cycle_run()} AND ("
            "(r.agent = 'orchestrator' AND r.id <> NEW.control_run_id) "
            "OR (r.agent = 'developer' AND r.invocation_id <> NEW.control_run_id || ':developer') "
            "OR (r.agent = 'test_runner' AND r.invocation_id <> NEW.control_run_id || ':test_runner')))",
        ),
        # R5 — contradições entre flags, Runs e launches
        _blocker(
            "run_component_missing", f"({_DEV_LAUNCH} OR {_RUNNER_PRESENT}) AND NOT {_DEV_RUN}"
        ),
        _blocker(
            "contradiction_developer_undeclared",
            f"({_DEV_RUN} OR {_DEV_LAUNCH}) AND NEW.developer_declared_at IS NULL",
        ),
        _blocker("contradiction_runner_without_run", f"{_RUNNER_LAUNCH} AND NOT {_RUNNER_RUN}"),
        _blocker(
            "contradiction_runner_before_developer",
            f"{_RUNNER_PRESENT} AND NEW.developer_declared_at IS NULL",
        ),
        _blocker(
            "evidence_foreign",
            "EXISTS (SELECT 1 FROM execution_evidence e WHERE e.reservation_id = NEW.id "
            "AND e.launch_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM execution_launch l "
            "WHERE l.id = e.launch_id AND l.reservation_id = NEW.id))",
        ),
        # R1 — launches
        _blocker(
            "launch_unresolved",
            "EXISTS (SELECT 1 FROM execution_launch l WHERE l.reservation_id = NEW.id "
            f"AND NOT {_closed('l')})",
        ),
        # R3 — operações em voo
        _blocker(
            "drain_unproven",
            f"{_DEV_PRESENT} AND NOT {_scope_ok('in_process', None, _LIVE_PROC, _NEG_PROC)}",
        ),
        # R4 — bindings
        _blocker(
            "binding_unproven",
            f"NOT {_scope_ok('binding', 'control', _LIVE_BIND, _NEG_BIND)}",
        ),
        _blocker(
            "binding_unproven",
            f"{_DEV_PRESENT} AND NOT {_scope_ok('binding', 'developer', _LIVE_BIND, _NEG_BIND)}",
        ),
        _blocker(
            "binding_unproven",
            f"{_RUNNER_PRESENT} AND NOT {_scope_ok('binding', 'test_runner', _LIVE_BIND, _NEG_BIND)}",
        ),
        # R7 — proprietário, selo e ack histórico
        _blocker(
            "releaser_not_participant",
            "NEW.released_by_token IS NOT "
            "(CASE WHEN NEW.adopted_by_token IS NULL THEN NEW.owner_token ELSE NEW.adopted_by_token END)",
        ),
        _blocker("seal_missing", "NEW.release_evidence_hash IS NULL"),
        _blocker(
            "ack_required",
            "NEW.ever_unproven = 1 AND NOT (NEW.ack_at IS NOT NULL "
            "AND NEW.first_unproven_at IS NOT NULL AND NEW.ack_at >= NEW.first_unproven_at)",
        ),
        "END",
    ]
)


def _reservation_of_launch(extra: str = "") -> str:
    return f"EXISTS (SELECT 1 FROM execution_reservation r WHERE r.id = {{ref}} {extra})"


_LAUNCH_INSERT_GUARD = "\n".join(
    [
        "CREATE TRIGGER execution_launch_insert_guard",
        "BEFORE INSERT ON execution_launch",
        "BEGIN",
        # No BEFORE INSERT, `NEW.id` vale -1 quando o identificador é automático (e só o AFTER
        # enxerga o definitivo): -1 significa "automático", e um -1 explícito cai no AFTER abaixo.
        _RAISE.format(
            code="launch_insert:id_conflict",
            cond=("NEW.id <> -1 AND EXISTS (SELECT 1 FROM execution_launch x WHERE x.id = NEW.id)"),
        ),
        _RAISE.format(
            code="launch_insert:reservation_adopted",
            cond=_reservation_of_launch("AND r.adopted_by_token IS NOT NULL").format(
                ref="NEW.reservation_id"
            ),
        ),
        _RAISE.format(
            code="launch_insert:reservation_not_writable",
            cond="NOT "
            + _reservation_of_launch("AND r.state = 'active'").format(ref="NEW.reservation_id"),
        ),
        _RAISE.format(
            code="launch_insert:drain_requested",
            cond=_reservation_of_launch("AND r.drain_requested_at IS NOT NULL").format(
                ref="NEW.reservation_id"
            ),
        ),
        _RAISE.format(
            code="launch_insert:must_start_at_intent",
            cond="NEW.phase <> 'intent' OR NEW.version <> 1",
        ),
        "END",
    ]
)

#: AFTER INSERT: o `id` definitivo (inclusive um `-1` explícito, indistinguível de "automático" no
#: BEFORE) tem de ser positivo. `RAISE(ABORT)` desfaz a inserção inteira.
_LAUNCH_ID_GUARD = """
CREATE TRIGGER execution_launch_id_guard
AFTER INSERT ON execution_launch
BEGIN
    SELECT RAISE(ABORT, 'launch_insert:id_not_positive') WHERE NEW.id < 1;
END
"""

_LAUNCH_UPDATE_GUARD = "\n".join(
    [
        "CREATE TRIGGER execution_launch_update_guard",
        "BEFORE UPDATE ON execution_launch",
        "BEGIN",
        _RAISE.format(
            code="launch_update:terminal_immutable",
            cond=f"OLD.phase IN {_in(_TERMINAL)}",
        ),
        _RAISE.format(
            code="launch_update:reservation_adopted",
            cond=_reservation_of_launch("AND r.adopted_by_token IS NOT NULL").format(
                ref="OLD.reservation_id"
            ),
        ),
        _RAISE.format(
            code="launch_update:reservation_not_writable",
            cond="NOT "
            + _reservation_of_launch("AND r.state IN ('active', 'draining')").format(
                ref="OLD.reservation_id"
            ),
        ),
        _RAISE.format(code="launch_update:edge_illegal", cond=f"NOT ({_LAUNCH_EDGES})"),
        _RAISE.format(
            code="launch_update:writer_not_owner",
            cond=(
                f"NEW.phase IN {_in(_TERMINAL)} AND NEW.ended_by_token IS NOT "
                "(SELECT r.owner_token FROM execution_reservation r WHERE r.id = OLD.reservation_id)"
            ),
        ),
        _RAISE.format(
            code="launch_update:identity_immutable",
            cond=(
                "NEW.id IS NOT OLD.id OR NEW.reservation_id IS NOT OLD.reservation_id "
                "OR NEW.phase_tag IS NOT OLD.phase_tag OR NEW.exe_name IS NOT OLD.exe_name "
                "OR NEW.platform IS NOT OLD.platform "
                "OR NEW.containment_profile IS NOT OLD.containment_profile "
                "OR NEW.created_at IS NOT OLD.created_at "
                "OR (OLD.pid IS NOT NULL AND (NEW.pid IS NOT OLD.pid "
                "OR NEW.start_token IS NOT OLD.start_token))"
            ),
        ),
        _RAISE.format(
            code="launch_update:pid_only_at_created",
            cond="OLD.pid IS NULL AND NEW.pid IS NOT NULL AND NEW.phase <> 'created'",
        ),
        _RAISE.format(code="launch_update:version_step", cond="NEW.version IS NOT OLD.version + 1"),
        "END",
    ]
)

_LAUNCH_NO_DELETE = """
CREATE TRIGGER execution_launch_no_delete
BEFORE DELETE ON execution_launch
BEGIN
    SELECT RAISE(ABORT, 'launch_delete:append_only');
END
"""

_EVIDENCE_INSERT_GUARD = "\n".join(
    [
        "CREATE TRIGGER execution_evidence_insert_guard",
        "BEFORE INSERT ON execution_evidence",
        "BEGIN",
        # P1-I3-01/02: `seq` é a ordem total das provas (a negativa PRECEDE a positiva pelo `seq`).
        # Um `seq` explícito já usado (REPLACE) ou não maior que o maior gravado reordenaria o
        # histórico. `NEW.seq = -1` é o automático; o AFTER abaixo afirma o valor definitivo.
        _RAISE.format(
            code="evidence_insert:seq_conflict",
            cond=(
                "NEW.seq <> -1 AND EXISTS (SELECT 1 FROM execution_evidence x "
                "WHERE x.seq = NEW.seq)"
            ),
        ),
        _RAISE.format(
            code="evidence_insert:seq_not_increasing",
            cond=(
                "NEW.seq <> -1 AND NEW.seq <= COALESCE((SELECT MAX(x.seq) "
                "FROM execution_evidence x), 0)"
            ),
        ),
        _RAISE.format(
            code="evidence_insert:reservation_released",
            cond=_reservation_of_launch("AND r.state = 'released'").format(
                ref="NEW.reservation_id"
            ),
        ),
        _RAISE.format(
            code="evidence_insert:writer_not_participant",
            cond="NOT "
            + _reservation_of_launch(
                "AND (NEW.writer_token = r.owner_token OR NEW.writer_token IS r.adopted_by_token)"
            ).format(ref="NEW.reservation_id"),
        ),
        _RAISE.format(
            code="evidence_insert:live_basis_after_adoption",
            cond=f"NEW.basis IN {_in(LIVE_BASES)} AND "
            + _reservation_of_launch("AND r.adopted_by_token IS NOT NULL").format(
                ref="NEW.reservation_id"
            ),
        ),
        _RAISE.format(
            code="evidence_insert:quiescence_needs_adopter",
            cond="NEW.basis = 'cooperative_owner_quiescence' AND NOT "
            + _reservation_of_launch(
                "AND r.adopted_by_token IS NOT NULL AND r.adopted_by_token = NEW.writer_token"
            ).format(ref="NEW.reservation_id"),
        ),
        _RAISE.format(
            code="evidence_insert:launch_mismatch",
            cond=(
                "NEW.launch_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM execution_launch l "
                "WHERE l.id = NEW.launch_id AND l.reservation_id = NEW.reservation_id "
                "AND l.version = NEW.launch_version)"
            ),
        ),
        "END",
    ]
)

#: AFTER INSERT: o `seq` definitivo é positivo e MAIOR que todos os outros. `RAISE(ABORT)` desfaz a
#: inserção inteira (e a atualização do `sqlite_sequence`).
_EVIDENCE_ORDER_GUARD = """
CREATE TRIGGER execution_evidence_order_guard
AFTER INSERT ON execution_evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence_insert:seq_not_increasing')
    WHERE NEW.seq < 1
       OR NEW.seq <= COALESCE((SELECT MAX(x.seq) FROM execution_evidence x WHERE x.seq <> NEW.seq), 0);
END
"""

_EVIDENCE_NO_UPDATE = """
CREATE TRIGGER execution_evidence_no_update
BEFORE UPDATE ON execution_evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence_update:append_only');
END
"""

_EVIDENCE_NO_DELETE = """
CREATE TRIGGER execution_evidence_no_delete
BEFORE DELETE ON execution_evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence_delete:append_only');
END
"""

_PARENT_TRIGGERS = (
    """
CREATE TRIGGER workspace_task_protect_unreleased_reservation
BEFORE DELETE ON workspace_task
WHEN EXISTS (SELECT 1 FROM execution_reservation x WHERE x.task_id = OLD.id AND x.state <> 'released')
BEGIN
    SELECT RAISE(ABORT, 'purge_blocked:unreleased_reservation_task');
END
""",
    """
CREATE TRIGGER dev_workspace_protect_unreleased_reservation
BEFORE DELETE ON dev_workspace
WHEN EXISTS (SELECT 1 FROM execution_reservation x WHERE x.workspace_id = OLD.id AND x.state <> 'released')
BEGIN
    SELECT RAISE(ABORT, 'purge_blocked:unreleased_reservation_workspace');
END
""",
    """
CREATE TRIGGER run_protect_unreleased_reservation
BEFORE DELETE ON run
WHEN EXISTS (SELECT 1 FROM execution_reservation x WHERE x.task_id = OLD.task_id AND x.state <> 'released')
BEGIN
    SELECT RAISE(ABORT, 'purge_blocked:unreleased_reservation_run');
END
""",
)

_TRIGGERS = (
    _RESERVATION_GUARDS,
    _RESERVATION_NO_DELETE,
    _RESERVATION_UPDATE_GUARD,
    _RELEASE_GUARD,
    _LAUNCH_INSERT_GUARD,
    _LAUNCH_ID_GUARD,
    _LAUNCH_UPDATE_GUARD,
    _LAUNCH_NO_DELETE,
    _EVIDENCE_INSERT_GUARD,
    _EVIDENCE_ORDER_GUARD,
    _EVIDENCE_NO_UPDATE,
    _EVIDENCE_NO_DELETE,
    *_PARENT_TRIGGERS,
)

_PARENT_TRIGGER_NAMES = (
    "workspace_task_protect_unreleased_reservation",
    "dev_workspace_protect_unreleased_reservation",
    "run_protect_unreleased_reservation",
)


# ----------------------------------------------------------------------------- migration


def _scalar(bind: Connection, sql: str) -> int:
    return int(bind.exec_driver_sql(sql).scalar() or 0)


def _begin_atomic(bind: Connection) -> None:
    """Garante que tudo o que a migration fizer a seguir esteja numa **única** transação SQLite.

    Roda antes de qualquer leitura ou DDL. O SQLite tem DDL transacional: falha em qualquer ponto
    desfaz tabelas, índices e *triggers* juntos. Recusa, sem efeito, quando não há como garantir:
    modo *offline*, conexão que não é `sqlite3`, ou nenhuma transação do Alembic/chamador para
    comitar ou desfazer. Sem transação SQLite aberta, abre `BEGIN IMMEDIATE`.
    """
    if context.is_offline_mode():
        raise RuntimeError("a migration 0004 não suporta modo offline (`--sql`)")
    raw = bind.connection.dbapi_connection
    if not isinstance(raw, sqlite3.Connection):
        raise RuntimeError("a migration 0004 exige uma conexão DBAPI `sqlite3`")
    if not bind.in_transaction():
        raise RuntimeError("a migration 0004 exige uma transação do Alembic ou do chamador")
    if not raw.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def upgrade() -> None:
    bind = op.get_bind()
    _begin_atomic(bind)

    # Fail closed ANTES de qualquer DDL — e já dentro da transação.
    executing = _scalar(bind, "SELECT COUNT(*) FROM workspace_task WHERE status = 'executing'")
    running = _scalar(bind, "SELECT COUNT(*) FROM run WHERE status = 'running'")
    if executing or running:
        raise RuntimeError(
            f"upgrade recusado: {executing} task(s) `executing` e {running} Run(s) `running`. "
            "Sem reserva durável não há como provar o encerramento do que está em voo. Suba o "
            "backend anterior para a `reconcile_on_startup` encerrar essas execuções (e confirme "
            "que nenhum processo ficou vivo) antes de migrar. Nada foi alterado."
        )

    bind.exec_driver_sql(_RESERVATION_TABLE)
    bind.exec_driver_sql(_LAUNCH_TABLE)
    bind.exec_driver_sql(_EVIDENCE_TABLE)
    for statement in _INDEXES:
        bind.exec_driver_sql(statement)
    for statement in _TRIGGERS:
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    bind = op.get_bind()
    _begin_atomic(bind)

    reservations = _scalar(bind, "SELECT COUNT(*) FROM execution_reservation")
    unreleased = _scalar(
        bind, "SELECT COUNT(*) FROM execution_reservation WHERE state <> 'released'"
    )
    launches = _scalar(bind, "SELECT COUNT(*) FROM execution_launch")
    evidence = _scalar(bind, "SELECT COUNT(*) FROM execution_evidence")
    if reservations or launches or evidence:
        raise RuntimeError(
            f"downgrade recusado: o histórico de execução não está vazio ({reservations} "
            f"reserva(s), das quais {unreleased} não liberada(s); {launches} launch(es); "
            f"{evidence} evidência(s)). Apagar as tabelas descartaria a trilha de auditoria de "
            "encerramento e, havendo reserva não liberada, soltaria o slot global de uma "
            "execução ainda não provada. Esta migration não oferece exclusão forçada: preserve ou "
            "exporte o banco. Só um banco com as três tabelas vazias é rebaixado. Nada foi "
            "alterado."
        )

    for name in _PARENT_TRIGGER_NAMES:
        bind.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
    # Filhas primeiro: as FKs são RESTRICT. Triggers e índices caem junto com a tabela.
    bind.exec_driver_sql("DROP TABLE execution_evidence")
    bind.exec_driver_sql("DROP TABLE execution_launch")
    bind.exec_driver_sql("DROP TABLE execution_reservation")
