"""Apoio dos testes do I3 (E8.4.5): banco **migrado de verdade** pelo Alembic e SQL cru.

Nada aqui é código de produção: o construtor de `ReleaseSnapshot` abaixo é o que o I5 terá de
reproduzir (com a mesma seleção de Runs do ciclo) e existe para a comparação diferencial entre
`decide_release` (contrato I1) e os *triggers* reais.

A conexão é `sqlite3` com `isolation_level=None` e `foreign_keys=ON`: cada instrução é atômica, e
`RAISE(ABORT)` de um trigger aborta só a instrução (S2) — por isso `refused()` devolve a mensagem.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.db.base import new_uuid
from app.db.enums import RunStatus
from app.orchestrator import teardown_contract as tc
from tests.conftest import run_migrations

OWNER = "owner-token"
ADOPTER = "adopter-token"
OTHER = "other-token"
LOGICAL = "logical-key-1"
PHYSICAL = "physical-key-1"
SEAL = "a" * 64


def ts(n: int = 0) -> str:
    """Instante no formato do `UtcDateTime` (largura constante: a ordem textual é a cronológica)."""
    return f"2026-01-01 00:{n // 60:02d}:{n % 60:02d}.000000"


def parse_ts(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)


def migrated_copy(template: Path, destination: Path) -> Path:
    shutil.copyfile(template, destination)
    return destination


def make_template(directory: Path) -> Path:
    path = directory / "template.db"
    run_migrations(f"sqlite+pysqlite:///{path.as_posix()}")
    return path


@dataclass
class Cycle:
    """Os identificadores de uma tentativa."""

    task_id: str
    workspace_id: str
    control_run_id: str
    reservation_id: str


class Db:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._workspace_id: str | None = None

    def close(self) -> None:
        self.conn.close()

    # ----------------------------------------------------------------------- SQL cru
    def run(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else tuple(row)

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def refused(self, sql: str, params: tuple[Any, ...] = ()) -> str:
        """Executa e exige recusa do banco; devolve a mensagem (código do trigger ou do CHECK)."""
        try:
            self.conn.execute(sql, params)
        except sqlite3.IntegrityError as error:
            return str(error)
        raise AssertionError(f"o banco aceitou: {sql[:90]}")

    def drop_trigger(self, name: str) -> None:
        self.conn.execute(f"DROP TRIGGER {name}")

    # ----------------------------------------------------------------------- domínio
    def new_task(self, *, status: str = "approved") -> tuple[str, str]:
        if (
            self._workspace_id is None
            or self.one("SELECT 1 FROM dev_workspace WHERE id = ?", (self._workspace_id,)) is None
        ):
            self._workspace_id = new_uuid()
            self.run(
                "INSERT INTO dev_workspace (id, name, type, local_path, status, created_at,"
                " updated_at) VALUES (?, 'ws', 'personal', ?, 'active', ?, ?)",
                (self._workspace_id, f"/tmp/{self._workspace_id}", ts(), ts()),
            )
        task_id = new_uuid()
        self.run(
            "INSERT INTO workspace_task (id, workspace_id, title, goal, status, version, risk,"
            " complexity, risk_source, execution_mode, agents, attempts, fix_rounds,"
            " cancel_requested, created_at) VALUES (?, ?, 't', 'g', ?, 1, 'low', 'low',"
            " 'hard_rule', 'orchestrated', '[]', 0, 0, 0, ?)",
            (task_id, self._workspace_id, status, ts()),
        )
        return self._workspace_id, task_id

    def add_run(
        self,
        task_id: str,
        agent: str,
        *,
        run_id: str | None = None,
        invocation_id: str | None = None,
        status: str = "running",
    ) -> str:
        run_id = run_id or new_uuid()
        finished = None if status == "running" else ts(5)
        self.run(
            "INSERT INTO run (id, invocation_id, task_id, agent, purpose, attempt_index, fix_round,"
            " provider, provider_adapter, transport, status, started_at, finished_at, duration_ms,"
            " token_source, files_read_source) VALUES (?, ?, ?, ?, 'execution', 0, 0, 'p', 'a',"
            " 'process', ?, ?, ?, ?, 'unavailable', 'unavailable')",
            (
                run_id,
                invocation_id or f"inv-{run_id}",
                task_id,
                agent,
                status,
                ts(1),
                finished,
                None if finished is None else 10,
            ),
        )
        return run_id

    def close_run(self, run_id: str, status: str = "ok") -> None:
        self.run(
            "UPDATE run SET status = ?, finished_at = ?, duration_ms = 10 WHERE id = ?",
            (status, ts(6), run_id),
        )

    def reserve(
        self,
        *,
        task: tuple[str, str] | None = None,
        control_run_id: str | None = None,
        with_control_run: bool = True,
        control_status: str = "ok",
        **fields: Any,
    ) -> Cycle:
        """Insere uma reserva `active` (o trigger só aceita linha fresca) e o Run de controle."""
        workspace_id, task_id = task or self.new_task()
        control = control_run_id or new_uuid()
        rid = fields.pop("id", new_uuid())
        row: dict[str, Any] = {
            "id": rid,
            "slot_key": 1,
            "task_id": task_id,
            "workspace_id": workspace_id,
            "control_run_id": control,
            "worktree_ref": "ff-task-abcd1234",
            "state": "active",
            "version": 1,
            "owner_token": OWNER,
            "logical_key": LOGICAL,
            "physical_key": PHYSICAL,
            "created_at": ts(),
        }
        row.update(fields)
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self.run(
            f"INSERT INTO execution_reservation ({columns}) VALUES ({marks})",  # noqa: S608
            tuple(row.values()),
        )
        if with_control_run:
            self.add_run(task_id, "orchestrator", run_id=control, status=control_status)
        return Cycle(task_id, workspace_id, control, rid)

    def update_reservation(self, rid: str, **fields: Any) -> None:
        """`UPDATE` que sobe a versão em 1 (a regra de fencing do trigger)."""
        assignments = ", ".join(f"{name} = ?" for name in fields)
        self.run(
            f"UPDATE execution_reservation SET version = version + 1, {assignments} WHERE id = ?",  # noqa: S608
            (*fields.values(), rid),
        )

    def refused_update(self, rid: str, **fields: Any) -> str:
        """`UPDATE` que o banco deve recusar. A versão sobe sozinha, salvo se informada: assim a
        regra testada não é mascarada pelo `version_step`, que dispara antes das outras."""
        if "version" not in fields:
            current = int(
                self.scalar("SELECT version FROM execution_reservation WHERE id = ?", (rid,))
            )
            fields = {"version": current + 1, **fields}
        assignments = ", ".join(f"{name} = ?" for name in fields)
        return self.refused(
            f"UPDATE execution_reservation SET {assignments} WHERE id = ?",  # noqa: S608
            (*fields.values(), rid),
        )

    def state_of(self, rid: str) -> str:
        return str(self.scalar("SELECT state FROM execution_reservation WHERE id = ?", (rid,)))

    # ---- caminhos legais até cada estado
    def to_draining(self, rid: str) -> None:
        self.update_reservation(rid, state="draining", drain_requested_at=ts(2), draining_at=ts(3))

    def to_teardown_pending(self, rid: str) -> None:
        self.update_reservation(rid, state="teardown_pending")

    def to_unproven(self, rid: str) -> None:
        if self.state_of(rid) == "active":
            self.to_teardown_pending(rid)
        self.update_reservation(rid, state="unproven", ever_unproven=1, first_unproven_at=ts(10))

    def ack(self, rid: str, at: int = 20) -> None:
        self.update_reservation(rid, ack_at=ts(at), ack_reason="reconhecido pelo operador")

    def adopt(self, rid: str, adopter: str = ADOPTER) -> None:
        self.update_reservation(rid, state="teardown_pending", adopted_by_token=adopter)

    def declare_developer(self, rid: str) -> None:
        self.update_reservation(rid, developer_declared_at=ts(4))

    # ----------------------------------------------------------------------- ledger
    def add_launch(
        self,
        rid: str,
        *,
        tag: str = "developer",
        platform: str = "win32",
        exe: str = "python.exe",
        **fields: Any,
    ) -> int:
        profile = "win_job_v1" if platform == "win32" else "posix_pgrp_v1"
        row: dict[str, Any] = {
            "reservation_id": rid,
            "phase_tag": tag,
            "exe_name": exe,
            "platform": platform,
            "containment_profile": profile,
            "phase": "intent",
            "version": 1,
            "created_at": ts(),
        }
        row.update(fields)
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        cursor = self.run(
            f"INSERT INTO execution_launch ({columns}) VALUES ({marks})",  # noqa: S608
            tuple(row.values()),
        )
        assert cursor.lastrowid is not None
        return int(cursor.lastrowid)

    def advance_launch(self, lid: int, **fields: Any) -> None:
        assignments = ", ".join(f"{name} = ?" for name in fields)
        self.run(
            f"UPDATE execution_launch SET version = version + 1, {assignments} WHERE id = ?",  # noqa: S608
            (*fields.values(), lid),
        )

    def refused_launch_update(self, lid: int, **fields: Any) -> str:
        if "version" not in fields:
            current = int(self.scalar("SELECT version FROM execution_launch WHERE id = ?", (lid,)))
            fields = {"version": current + 1, **fields}
        assignments = ", ".join(f"{name} = ?" for name in fields)
        return self.refused(
            f"UPDATE execution_launch SET {assignments} WHERE id = ?",  # noqa: S608
            (*fields.values(), lid),
        )

    def created(self, lid: int, pid: int = 4242, filetime: str = "133000000000000000") -> None:
        self.advance_launch(lid, phase="created", pid=pid, start_token=filetime)

    def contained(self, lid: int) -> None:
        self.advance_launch(lid, phase="contained")

    def end(self, lid: int, basis: str, *, by: str = OWNER) -> None:
        confirmed = basis in ("refused_before_creation", "tree_confirmed_dead")
        self.advance_launch(
            lid,
            phase="ended_confirmed" if confirmed else "ended_unconfirmed",
            end_basis=basis,
            ended_by_token=by,
        )

    def launch_version(self, lid: int) -> int:
        return int(self.scalar("SELECT version FROM execution_launch WHERE id = ?", (lid,)))

    # ----------------------------------------------------------------------- evidência
    def evidence_row(
        self,
        rid: str,
        scope: str,
        basis: str,
        *,
        writer: str = OWNER,
        subject: str | None = None,
        launch: int | None = None,
        launch_version: int | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        if scope == "launch" and launch is not None and launch_version is None:
            launch_version = self.launch_version(launch)
        row: dict[str, Any] = {
            "reservation_id": rid,
            "launch_id": launch,
            "launch_version": launch_version,
            "scope": scope,
            "subject": subject,
            "basis": basis,
            "writer_token": writer,
            "observed_at": ts(30),
        }
        row.update(fields)
        return row

    def add_evidence(self, rid: str, scope: str, basis: str, **kw: Any) -> int:
        row = self.evidence_row(rid, scope, basis, **kw)
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        cursor = self.run(
            f"INSERT INTO execution_evidence ({columns}) VALUES ({marks})",  # noqa: S608
            tuple(row.values()),
        )
        assert cursor.lastrowid is not None
        return int(cursor.lastrowid)

    def refused_evidence(self, rid: str, scope: str, basis: str, **kw: Any) -> str:
        row = self.evidence_row(rid, scope, basis, **kw)
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        return self.refused(
            f"INSERT INTO execution_evidence ({columns}) VALUES ({marks})",  # noqa: S608
            tuple(row.values()),
        )

    # ---- atalhos de evidência qualificadora
    def drained(self, rid: str, writer: str = OWNER) -> int:
        return self.add_evidence(rid, "in_process", "executor_drained", writer=writer)

    def binding(
        self, rid: str, subject: str, basis: str = "binding_released_observed", writer: str = OWNER
    ) -> int:
        return self.add_evidence(rid, "binding", basis, subject=subject, writer=writer)

    def quiescence(self, rid: str, scope: str, subject: str | None = None) -> int:
        return self.add_evidence(
            rid,
            scope,
            "cooperative_owner_quiescence",
            writer=ADOPTER,
            subject=subject,
            logical_key=LOGICAL,
            physical_key=PHYSICAL,
            lock_files_match=1,
        )

    # ----------------------------------------------------------------------- liberação
    def release(self, rid: str, *, by: str | None = None, seal: str | None = SEAL) -> int:
        """O `UPDATE` de release com CAS em `(id, state, version)`; devolve as linhas afetadas."""
        state, version, owner, adopted = self.one(
            "SELECT state, version, owner_token, adopted_by_token FROM execution_reservation"
            " WHERE id = ?",
            (rid,),
        ) or ("", 0, "", None)
        by = by if by is not None else (adopted or owner)
        cursor = self.run(
            "UPDATE execution_reservation SET state = 'released', version = version + 1,"
            " released_at = ?, released_by_token = ?, release_evidence_hash = ?"
            " WHERE id = ? AND state = ? AND version = ?",
            (ts(40), by, seal, rid, state, version),
        )
        return cursor.rowcount

    def refused_release(self, rid: str, *, by: str | None = None, seal: str | None = SEAL) -> str:
        state, version, owner, adopted = self.one(
            "SELECT state, version, owner_token, adopted_by_token FROM execution_reservation"
            " WHERE id = ?",
            (rid,),
        ) or ("", 0, "", None)
        by = by if by is not None else (adopted or owner)
        return self.refused(
            "UPDATE execution_reservation SET state = 'released', version = version + 1,"
            " released_at = ?, released_by_token = ?, release_evidence_hash = ?"
            " WHERE id = ? AND state = ? AND version = ?",
            (ts(40), by, seal, rid, state, version),
        )

    # ----------------------------------------------------------------------- contrato
    def snapshot(
        self,
        rid: str,
        *,
        lease: tc.LeaseStatus = tc.LeaseStatus.CONSISTENT,
        history: tc.HistoryCurrency = tc.HistoryCurrency.PROVEN_CURRENT,
    ) -> tc.ReleaseSnapshot:
        """Lê **as linhas** e monta o snapshot do I1. Lease e histórico são externos ao banco."""
        r = self.conn.execute("SELECT * FROM execution_reservation WHERE id = ?", (rid,))
        names = [c[0] for c in r.description]
        res = dict(zip(names, r.fetchone(), strict=True))
        facts = tc.ReservationFacts(
            state=tc.ReservationState(res["state"]),
            version=res["version"],
            control_run_id=res["control_run_id"],
            owner_token=res["owner_token"],
            adopted_by_token=res["adopted_by_token"],
            logical_key=res["logical_key"],
            physical_key=res["physical_key"],
            developer_declared=res["developer_declared_at"] is not None,
            ever_unproven=bool(res["ever_unproven"]),
            first_unproven_at=parse_ts(res["first_unproven_at"]),
            ack_at=parse_ts(res["ack_at"]),
        )
        launches = tuple(self._launches(rid))
        evidence = tuple(self._evidence(rid))
        runs = tuple(self._runs(res))
        return tc.ReleaseSnapshot(facts, launches, evidence, runs, lease, history)

    def _rows(self, sql: str, params: tuple[Any, ...]) -> Iterator[dict[str, Any]]:
        cursor = self.conn.execute(sql, params)
        names = [c[0] for c in cursor.description]
        for row in cursor.fetchall():
            yield dict(zip(names, row, strict=True))

    def _launches(self, rid: str) -> Iterator[tc.LaunchRecord]:
        for row in self._rows(
            "SELECT * FROM execution_launch WHERE reservation_id = ? ORDER BY id", (rid,)
        ):
            yield tc.LaunchRecord(
                id=row["id"],
                phase_tag=tc.PhaseTag(row["phase_tag"]),
                platform=tc.Platform(row["platform"]),
                containment_profile=tc.ContainmentProfile(row["containment_profile"]),
                phase=tc.LaunchPhase(row["phase"]),
                version=row["version"],
                pid=row["pid"],
                start_token=row["start_token"],
                end_basis=None if row["end_basis"] is None else tc.EndBasis(row["end_basis"]),
                ended_by_token=row["ended_by_token"],
            )

    def _evidence(self, rid: str) -> Iterator[tc.EvidenceRecord]:
        for row in self._rows(
            "SELECT * FROM execution_evidence WHERE reservation_id = ? ORDER BY seq", (rid,)
        ):
            yield tc.EvidenceRecord(
                seq=row["seq"],
                scope=tc.EvidenceScope(row["scope"]),
                basis=tc.EvidenceBasis(row["basis"]),
                writer_token=row["writer_token"],
                subject=None if row["subject"] is None else tc.BindingSubject(row["subject"]),
                launch_id=row["launch_id"],
                launch_version=row["launch_version"],
                logical_key=row["logical_key"],
                physical_key=row["physical_key"],
                lock_files_match=bool(row["lock_files_match"]),
                profile_ok=bool(row["profile_ok"]),
                owner_quiescence_ok=bool(row["owner_quiescence_ok"]),
                root_probe_absent=bool(row["root_probe_absent"]),
                boot_changed=bool(row["boot_changed"]),
                supervision_confirmed=bool(row["supervision_confirmed"]),
            )

    def _runs(self, res: dict[str, Any]) -> Iterator[tc.RunFact]:
        """Os Runs do ciclo: a **identidade** seleciona, o `agent` dá o papel."""
        control = res["control_run_id"]
        roles = {
            "orchestrator": tc.RunRole.CONTROL,
            "developer": tc.RunRole.DEVELOPER,
            "test_runner": tc.RunRole.TEST_RUNNER,
        }
        for row in self._rows(
            "SELECT id, invocation_id, agent, status FROM run WHERE task_id = ? AND (id = ?"
            " OR invocation_id = ? OR invocation_id = ?) ORDER BY id",
            (res["task_id"], control, f"{control}:developer", f"{control}:test_runner"),
        ):
            yield tc.RunFact(
                roles[row["agent"]], RunStatus(row["status"]), row["id"], row["invocation_id"]
            )

    def decision(
        self,
        rid: str,
        *,
        by: str | None = None,
        seal: bool = True,
        lease: tc.LeaseStatus = tc.LeaseStatus.CONSISTENT,
        history: tc.HistoryCurrency = tc.HistoryCurrency.PROVEN_CURRENT,
    ) -> tc.ReleaseDecision:
        snap = self.snapshot(rid, lease=lease, history=history)
        r = snap.reservation
        releaser = by if by is not None else (r.adopted_by_token or r.owner_token)
        request = tc.ReleaseRequest(r.state, r.version, releaser, seal)
        return tc.decide_release(snap, request)


# --------------------------------------------------------------------------- cenários prontos


def build_cycle(
    db: Db,
    *,
    adopted: bool = False,
    omit: frozenset[str] = frozenset(),
    control_run: bool = True,
    dev_run: bool = True,
    runner_run: bool = True,
    runner_status: str = "ok",
    declare: bool = True,
    dev_invocation: str | None = None,
    dev_launch: bool = True,
    runner_launch: bool = True,
    before_close: Callable[[Db, Cycle], None] | None = None,
    after: Callable[[Db, Cycle], None] | None = None,
) -> Cycle:
    """Um ciclo de liberação com Developer e Test Runner; `omit` retira uma prova por vez.

    Caminho do dono (`adopted=False`): launches encerrados pelo dono, drenagem e bindings
    observados ao vivo. Caminho do recovery (`adopted=True`): o dono encerrou os launches e
    morreu; a adoção e a quiescência cobrem o que vive no espaço de endereço dele.
    `omit` aceita `drain`, `bind:control`, `bind:developer` e `bind:test_runner`.
    `before_close` roda enquanto o dono ainda pode escrever launches (antes de drenar/adotar);
    `after` roda no fim. `dev_invocation` aceita `{c}` para o `control_run_id`.
    """
    cycle = db.reserve(with_control_run=control_run)
    rid = cycle.reservation_id
    if declare:
        db.declare_developer(rid)
    if dev_run:
        db.add_run(
            cycle.task_id,
            "developer",
            invocation_id=(dev_invocation or "{c}:developer").format(c=cycle.control_run_id),
            status="ok",
        )
    if runner_run:
        db.add_run(
            cycle.task_id,
            "test_runner",
            invocation_id=f"{cycle.control_run_id}:test_runner",
            status=runner_status,
        )
    if dev_launch:
        dev = db.add_launch(rid, tag="developer")
        db.created(dev)
        db.contained(dev)
        db.end(dev, "tree_confirmed_dead")
    if runner_launch:
        runner = db.add_launch(rid, tag="test_runner")
        db.end(runner, "refused_before_creation")
    if before_close is not None:
        before_close(db, cycle)
    subjects = ("control", "developer", "test_runner")
    if not adopted:
        if "drain" not in omit:
            db.drained(rid)
        for subject in subjects:
            if f"bind:{subject}" not in omit:
                db.binding(rid, subject)
        db.to_draining(rid)
    else:
        db.adopt(rid)
        if "drain" not in omit:
            db.quiescence(rid, "in_process")
        for subject in subjects:
            if f"bind:{subject}" not in omit:
                db.quiescence(rid, "binding", subject)
    if after is not None:
        after(db, cycle)
    return cycle


def developer_cycle(db: Db, *, adopted: bool = False) -> Cycle:
    return build_cycle(db, adopted=adopted)


def minimal_ready(db: Db) -> Cycle:
    """O menor ciclo liberável: só o Run de controle e o binding de controle, em `draining`."""
    cycle = db.reserve()
    db.binding(cycle.reservation_id, "control")
    db.to_draining(cycle.reservation_id)
    return cycle
