"""E8.4.5 / I3 — invariantes estruturais da reserva durável, do ledger e da evidência.

Tudo contra **SQLite real**, com o schema vindo da **migration Alembic real** (o `template` é
migrado uma vez e copiado por teste) e SQL cru: o protótipo S2 não vale como teste. Cada recusa é
conferida pelo **código** do trigger ou pelo nome do CHECK, nunca só por "deu erro".

A comparação diferencial com `decide_release` do contrato I1 está em
`test_execution_reservation_differential_e8_4_5.py`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.db.base import new_uuid
from app.orchestrator import teardown_contract as tc
from tests.execution_reservation_support_e8_4_5 import (
    ADOPTER,
    OTHER,
    OWNER,
    SEAL,
    Db,
    build_cycle,
    make_template,
    migrated_copy,
    minimal_ready,
    ts,
)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_template(tmp_path_factory.mktemp("template"))


@pytest.fixture
def db(template: Path, tmp_path: Path) -> Iterator[Db]:
    database = Db(migrated_copy(template, tmp_path / "t.db"))
    try:
        yield database
    finally:
        database.close()


def released(db: Db) -> str:
    """Uma reserva legitimamente `released` (menor ciclo liberável)."""
    cycle = minimal_ready(db)
    assert db.release(cycle.reservation_id) == 1
    return cycle.reservation_id


# ============================================================ slot global e histórico


def test_segunda_reserva_global_nao_liberada_e_recusada(db: Db) -> None:
    db.reserve()
    with pytest.raises(sqlite3.IntegrityError, match="reservation_insert:slot_conflict"):
        db.reserve()  # outra task, outro workspace, outro Run: o slot é GLOBAL


def test_slot_key_diferente_de_um_e_recusado(db: Db) -> None:
    message = db.refused(
        "INSERT INTO execution_reservation (id, slot_key, task_id, workspace_id,"
        " control_run_id, worktree_ref, state, owner_token, logical_key, physical_key, created_at)"
        " VALUES (?, 2, 't', 'w', ?, 'ff-task-x', 'active', 'o', 'l', 'p', ?)",
        (new_uuid(), new_uuid(), ts()),
    )
    assert "ck_execution_reservation_slot_key" in message


def test_reservas_liberadas_coexistem_com_uma_ativa(db: Db) -> None:
    first = released(db)
    second = released(db)
    third = released(db)
    active = db.reserve()
    assert {first, second, third}.isdisjoint({active.reservation_id})
    assert db.scalar("SELECT COUNT(*) FROM execution_reservation WHERE state = 'released'") == 3
    with pytest.raises(sqlite3.IntegrityError, match="reservation_insert:slot_conflict"):
        db.reserve()


def test_nova_tentativa_da_mesma_task_e_permitida_depois_de_liberar(db: Db) -> None:
    task = db.new_task()
    first = db.reserve(task=task)
    db.binding(first.reservation_id, "control")
    db.to_draining(first.reservation_id)
    assert db.release(first.reservation_id) == 1

    second = db.reserve(task=task)  # mesma task, mesmo workspace, novo control_run_id
    assert second.control_run_id != first.control_run_id
    rows = db.run(
        "SELECT task_id, workspace_id, state FROM execution_reservation ORDER BY created_at, id"
    ).fetchall()
    assert len(rows) == 2 and {(r[0], r[1]) for r in rows} == {(task[1], task[0])}
    assert {r[2] for r in rows} == {"released", "active"}


def test_control_run_id_e_unico_para_sempre(db: Db) -> None:
    first = db.reserve()
    db.binding(first.reservation_id, "control")
    db.to_draining(first.reservation_id)
    assert db.release(first.reservation_id) == 1
    with pytest.raises(sqlite3.IntegrityError, match="reservation_insert:control_run_id_conflict"):
        db.reserve(control_run_id=first.control_run_id)


def test_nao_ha_unicidade_historica_em_task_nem_workspace(db: Db) -> None:
    unique_columns: set[tuple[str, ...]] = set()
    for _seq, name, unique, _origin, partial in db.run(
        "PRAGMA index_list(execution_reservation)"
    ).fetchall():
        if not unique:
            continue
        columns = tuple(r[2] for r in db.run(f"PRAGMA index_info({name})").fetchall())
        unique_columns.add(columns)
        if columns == ("slot_key",):
            assert partial == 1, "o índice global é parcial: só reservas não liberadas"
    assert ("control_run_id",) in unique_columns
    assert ("task_id",) not in unique_columns and ("workspace_id",) not in unique_columns


# ============================================================ INSERT da reserva


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", "draining"),
        ("state", "released"),
        ("version", 2),
        ("adopted_by_token", ADOPTER),
        ("ever_unproven", 1),
        ("first_unproven_at", ts(1)),
        ("ack_at", ts(1)),
        ("ack_reason", "x"),
        ("released_at", ts(1)),
        ("released_by_token", OWNER),
        ("release_evidence_hash", SEAL),
        ("developer_declared_at", ts(1)),
        ("drain_requested_at", ts(1)),
        ("draining_at", ts(1)),
    ],
)
def test_so_se_insere_reserva_fresca_e_ativa(db: Db, field: str, value: Any) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.reserve(**{field: value})
    assert db.scalar("SELECT COUNT(*) FROM execution_reservation") == 0


# ============================================================ arestas de estado


def _in_state(db: Db, state: str) -> str:
    """Uma reserva no `state`, alcançada só por arestas legais."""
    if state == "released":
        return released(db)
    cycle = db.reserve()
    rid = cycle.reservation_id
    if state == "draining":
        db.to_draining(rid)
    elif state == "teardown_pending":
        db.to_teardown_pending(rid)
    elif state == "unproven":
        db.to_unproven(rid)
    return rid


def _attempt_state(db: Db, rid: str, new: str) -> str | None:
    """Tenta levar a reserva a `new` com os campos que o estado de destino exige.

    Devolve `None` se aceito, ou a mensagem do banco.
    """
    row = db.one(
        "SELECT drain_requested_at, draining_at, ever_unproven FROM execution_reservation"
        " WHERE id = ?",
        (rid,),
    )
    assert row is not None
    drain_requested, draining, ever_unproven = row
    fields: dict[str, object] = {"state": new}
    if new == "draining":
        fields["drain_requested_at"] = drain_requested or ts(2)
        fields["draining_at"] = draining or ts(3)
    if new == "unproven" and not ever_unproven:
        fields.update(ever_unproven=1, first_unproven_at=ts(10))
    assignments = ", ".join(f"{k} = ?" for k in fields)
    try:
        db.run(
            f"UPDATE execution_reservation SET version = version + 1, {assignments}"  # noqa: S608
            " WHERE id = ?",
            (*fields.values(), rid),
        )
    except sqlite3.IntegrityError as error:
        return str(error)
    return None


@pytest.mark.parametrize("old", [s.value for s in tc.ReservationState])
@pytest.mark.parametrize("new", [s.value for s in tc.ReservationState if s.value != "released"])
def test_matriz_de_estados_da_reserva_bate_com_o_contrato(db: Db, old: str, new: str) -> None:
    rid = _in_state(db, old)
    outcome = _attempt_state(db, rid, new)
    expected = tc.reservation_edge_allowed(tc.ReservationState(old), tc.ReservationState(new))
    assert (outcome is None) is expected, (old, new, outcome)
    if old == "released":
        assert outcome is not None and "reservation_update:released_immutable" in outcome
    elif not expected:
        assert outcome is not None and "reservation_update:edge_illegal" in outcome


@pytest.mark.parametrize("old", [s.value for s in tc.ReservationState])
@pytest.mark.parametrize("new", [s.value for s in tc.ReservationState if s.value != "released"])
def test_adocao_so_na_mesma_instrucao_que_leva_a_teardown_pending(
    db: Db, old: str, new: str
) -> None:
    rid = _in_state(db, old)
    row = db.one(
        "SELECT drain_requested_at, draining_at, ever_unproven FROM execution_reservation"
        " WHERE id = ?",
        (rid,),
    )
    assert row is not None
    fields: dict[str, object] = {"state": new, "adopted_by_token": ADOPTER}
    if new == "draining":
        fields.update(drain_requested_at=row[0] or ts(2), draining_at=row[1] or ts(3))
    if new == "unproven" and not row[2]:
        fields.update(ever_unproven=1, first_unproven_at=ts(10))
    assignments = ", ".join(f"{k} = ?" for k in fields)
    try:
        db.run(
            f"UPDATE execution_reservation SET version = version + 1, {assignments}"  # noqa: S608
            " WHERE id = ?",
            (*fields.values(), rid),
        )
        accepted = True
    except sqlite3.IntegrityError:
        accepted = False
    o, n = tc.ReservationState(old), tc.ReservationState(new)
    assert accepted is (tc.adoption_edge_allowed(o, n) and tc.reservation_edge_allowed(o, n)), (
        old,
        new,
    )


def test_adotante_nao_pode_ser_o_dono_nem_trocar_depois(db: Db) -> None:
    rid = db.reserve().reservation_id
    message = db.refused_update(rid, state="teardown_pending", adopted_by_token=OWNER)
    assert "ck_execution_reservation_adopter_is_not_owner" in message
    db.adopt(rid)
    assert "adoption_monotone" in db.refused_update(rid, adopted_by_token=OTHER)
    assert "adoption_monotone" in db.refused_update(rid, adopted_by_token=None)


def test_adocao_sem_mudar_o_estado_e_recusada(db: Db) -> None:
    """A lacuna do S2: adotar numa instrução à parte deixaria o INSERT de launch passar."""
    rid = db.reserve().reservation_id
    message = db.refused_update(rid, adopted_by_token=ADOPTER)
    assert "reservation_update:adoption_edge" in message or "adoption_state" in message


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "task_id",
        "workspace_id",
        "control_run_id",
        "worktree_ref",
        "owner_token",
        "logical_key",
        "physical_key",
        "created_at",
    ],
)
def test_identidade_da_reserva_e_imutavel(db: Db, field: str) -> None:
    rid = db.reserve().reservation_id
    value = ts(9) if field == "created_at" else "novo-valor"
    assert "reservation_update:identity_immutable" in db.refused_update(rid, **{field: value})


@pytest.mark.parametrize("new_version", [1, 3, 0])
def test_a_versao_sobe_de_um_em_um(db: Db, new_version: int) -> None:
    rid = db.reserve().reservation_id
    message = db.refused_update(rid, version=new_version, developer_declared_at=ts(4))
    assert "reservation_update:version_step" in message or "version_positive" in message


@pytest.mark.parametrize("field", ["developer_declared_at", "drain_requested_at", "draining_at"])
def test_campos_de_uma_so_gravacao_nao_mudam_nem_voltam_a_nulo(db: Db, field: str) -> None:
    rid = db.reserve().reservation_id
    db.update_reservation(rid, drain_requested_at=ts(2), draining_at=ts(3))
    db.update_reservation(rid, developer_declared_at=ts(4))
    assert "reservation_update:set_once" in db.refused_update(rid, **{field: ts(50)})
    assert "reservation_update:set_once" in db.refused_update(rid, **{field: None})


def test_draining_exige_os_dois_instantes(db: Db) -> None:
    rid = db.reserve().reservation_id
    message = db.refused_update(rid, state="draining")
    assert "ck_execution_reservation_drain_shape" in message
    message = db.refused_update(rid, state="draining", draining_at=ts(3))
    assert "ck_execution_reservation_drain_shape" in message


def test_worktree_ref_e_so_um_nome(db: Db) -> None:
    for bad in ("C:\\\\repo\\\\wt", "/home/u/wt", "a/b", "c:wt"):
        with pytest.raises(sqlite3.IntegrityError, match="worktree_ref_is_name_only"):
            db.reserve(worktree_ref=bad)


# ============================================================ histórico UNPROVEN e ack


def test_unproven_exige_ever_unproven_e_primeiro_instante(db: Db) -> None:
    rid = db.reserve().reservation_id
    db.to_teardown_pending(rid)
    assert "unproven_sticky" in db.refused_update(rid, state="unproven")
    assert "first_unproven_iff_ever" in db.refused_update(rid, state="unproven", ever_unproven=1)
    assert "first_unproven_iff_ever" in db.refused_update(rid, first_unproven_at=ts(1))


def test_ever_unproven_e_pegajoso_e_o_historico_e_imutavel(db: Db) -> None:
    rid = db.reserve().reservation_id
    db.to_unproven(rid)
    db.update_reservation(rid, state="teardown_pending")  # saiu de unproven: o histórico fica
    assert db.scalar("SELECT ever_unproven FROM execution_reservation WHERE id = ?", (rid,)) == 1
    assert "sticky_unproven" in db.refused_update(rid, ever_unproven=0, first_unproven_at=None)
    assert "history_immutable" in db.refused_update(rid, first_unproven_at=ts(50))
    assert "ck_execution_reservation_ack_pair" in db.refused_update(rid, ack_reason="so o motivo")
    assert "ck_execution_reservation_ack_pair" in db.refused_update(rid, ack_at=ts(20))
    db.ack(rid, at=20)
    assert "history_immutable" in db.refused_update(rid, ack_at=ts(30), ack_reason="outro")
    assert "history_immutable" in db.refused_update(rid, ack_reason=None, ack_at=None)


def test_ack_isolado_nao_libera(db: Db) -> None:
    """D4: reconhecer não é provar. Sem evidência o release continua recusado, com ack."""
    rid = db.reserve(with_control_run=True).reservation_id
    db.to_unproven(rid)
    db.ack(rid)
    assert "release_blocked:binding_unproven" in db.refused_release(rid)


def test_ack_historico_e_obrigatorio_mesmo_depois_de_sair_de_unproven(db: Db) -> None:
    """CP-2b: unproven → teardown_pending → released ainda exige ack posterior."""
    cycle = minimal_ready(db)
    rid = cycle.reservation_id
    db.to_teardown_pending(rid)
    db.update_reservation(rid, state="unproven", ever_unproven=1, first_unproven_at=ts(10))
    db.update_reservation(rid, state="teardown_pending")
    assert db.state_of(rid) == "teardown_pending"
    assert "release_blocked:ack_required" in db.refused_release(rid)
    db.ack(rid, at=20)
    assert db.release(rid) == 1


def test_ack_anterior_ao_primeiro_unproven_nao_vale(db: Db) -> None:
    cycle = minimal_ready(db)
    rid = cycle.reservation_id
    db.update_reservation(rid, state="teardown_pending")
    db.update_reservation(rid, state="unproven", ever_unproven=1, first_unproven_at=ts(10))
    db.ack(rid, at=5)  # ANTES do primeiro unproven
    assert "release_blocked:ack_required" in db.refused_release(rid)
    assert db.decision(rid).verdict is tc.ReleaseVerdict.HOLD


def test_reserva_que_nunca_foi_unproven_nao_exige_ack(db: Db) -> None:
    rid = minimal_ready(db).reservation_id
    assert db.scalar("SELECT ever_unproven FROM execution_reservation WHERE id = ?", (rid,)) == 0
    assert db.release(rid) == 1


def test_released_e_imutavel_e_nao_pode_ser_apagada(db: Db) -> None:
    rid = released(db)
    assert "released_immutable" in db.refused_update(rid, ack_reason="x", ack_at=ts(1))
    assert "reservation_delete:append_only" in db.refused(
        "DELETE FROM execution_reservation WHERE id = ?", (rid,)
    )


def test_reserva_ativa_nao_pode_ser_apagada(db: Db) -> None:
    rid = db.reserve().reservation_id
    assert "reservation_delete:append_only" in db.refused(
        "DELETE FROM execution_reservation WHERE id = ?", (rid,)
    )


# ============================================================ LEDGER: INSERT


def _reservation_config(db: Db, name: str) -> str:
    """Reservas em configurações alcançáveis (estado × adoção × pedido de drenagem)."""
    cycle = db.reserve()
    rid = cycle.reservation_id
    if name == "drain_requested":
        db.update_reservation(rid, drain_requested_at=ts(2))
    elif name == "draining":
        db.to_draining(rid)
    elif name == "teardown_pending":
        db.to_teardown_pending(rid)
    elif name == "unproven":
        db.to_unproven(rid)
    elif name == "adopted_teardown_pending":
        db.adopt(rid)
    elif name == "adopted_unproven":
        db.adopt(rid)
        db.update_reservation(rid, state="unproven", ever_unproven=1, first_unproven_at=ts(10))
    elif name == "released":
        db.binding(rid, "control")
        db.to_draining(rid)
        assert db.release(rid) == 1
    elif name == "adopted_released":
        db.binding(rid, "control")
        db.adopt(rid)
        db.quiescence(rid, "binding", "control")
        assert db.release(rid) == 1
    return rid


CONFIGS = {
    "active": (tc.ReservationState.ACTIVE, None, False),
    "drain_requested": (tc.ReservationState.ACTIVE, None, True),
    "draining": (tc.ReservationState.DRAINING, None, True),
    "teardown_pending": (tc.ReservationState.TEARDOWN_PENDING, None, False),
    "unproven": (tc.ReservationState.UNPROVEN, None, False),
    "adopted_teardown_pending": (tc.ReservationState.TEARDOWN_PENDING, ADOPTER, False),
    "adopted_unproven": (tc.ReservationState.UNPROVEN, ADOPTER, False),
    "released": (tc.ReservationState.RELEASED, None, False),
    "adopted_released": (tc.ReservationState.RELEASED, ADOPTER, False),
}


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_insert_de_launch_obedece_launch_insert_refusal(db: Db, name: str) -> None:
    rid = _reservation_config(db, name)
    state, adopted, drain = CONFIGS[name]
    expected = tc.launch_insert_refusal(
        reservation_state=state, adopted_by_token=adopted, drain_requested=drain
    )
    try:
        db.add_launch(rid)
        outcome = None
    except sqlite3.IntegrityError as error:
        outcome = str(error)
    if expected is None:
        assert outcome is None
    else:
        assert outcome is not None and f"launch_insert:{expected.value}" in outcome, (
            name,
            expected,
            outcome,
        )


@pytest.mark.parametrize(
    "fields", [{"phase": "created", "pid": 1, "start_token": "t"}, {"version": 2}]
)
def test_launch_so_nasce_em_intent_com_versao_um(db: Db, fields: dict[str, Any]) -> None:
    rid = db.reserve().reservation_id
    with pytest.raises(sqlite3.IntegrityError, match="launch_insert:must_start_at_intent"):
        db.add_launch(rid, **fields)


# ============================================================ LEDGER: arestas e forma


def _launch_in(db: Db, rid: str, phase: str, basis: str | None = None) -> int:
    """Um launch na `phase` (e `basis`), alcançado só por arestas legais."""
    lid = db.add_launch(rid)
    if phase == "intent":
        return lid
    if phase.startswith("ended") and basis in (
        "refused_before_creation",
        "popen_raised",
        "ledger_write_failed",
    ):
        db.end(lid, basis)  # a partir de intent: sem pid
        return lid
    db.created(lid)
    if phase == "contained":
        db.contained(lid)
    elif phase.startswith("ended"):
        assert basis is not None
        db.end(lid, basis)
    return lid


OPEN_PHASES = ["intent", "created", "contained"]
NEW_BASES = [None, *(b.value for b in tc.EndBasis)]


@pytest.mark.parametrize("old", OPEN_PHASES)
@pytest.mark.parametrize("new", [p.value for p in tc.LaunchPhase])
@pytest.mark.parametrize("basis", NEW_BASES)
def test_matriz_de_fases_do_launch_bate_com_o_contrato(
    db: Db, old: str, new: str, basis: str | None
) -> None:
    rid = db.reserve().reservation_id
    lid = _launch_in(db, rid, old)
    fields: dict[str, object] = {"phase": new, "end_basis": basis}
    if new == "created" and old == "intent":
        fields.update(pid=4242, start_token="133000000000000000")
    if new in ("ended_confirmed", "ended_unconfirmed"):
        fields["ended_by_token"] = OWNER
    assignments = ", ".join(f"{k} = ?" for k in fields)
    try:
        db.run(
            f"UPDATE execution_launch SET version = version + 1, {assignments} WHERE id = ?",  # noqa: S608
            (*fields.values(), lid),
        )
        accepted = True
    except sqlite3.IntegrityError:
        accepted = False
    end_basis = None if basis is None else tc.EndBasis(basis)
    expected = tc.launch_edge_allowed(tc.LaunchPhase(old), tc.LaunchPhase(new), end_basis)
    assert accepted is expected, (old, new, basis)


TERMINAL_ROWS = [
    ("ended_confirmed", "refused_before_creation"),
    ("ended_confirmed", "tree_confirmed_dead"),
    ("ended_unconfirmed", "popen_raised"),
    ("ended_unconfirmed", "ledger_write_failed"),
    ("ended_unconfirmed", "tree_unconfirmed"),
]


@pytest.mark.parametrize(("phase", "basis"), TERMINAL_ROWS)
def test_launch_terminal_e_imutavel(db: Db, phase: str, basis: str) -> None:
    rid = db.reserve().reservation_id
    lid = _launch_in(db, rid, phase, basis)
    assert "launch_update:terminal_immutable" in db.refused_launch_update(lid, end_basis=basis)
    assert (
        tc.launch_write_refusal(
            reservation_state=tc.ReservationState.ACTIVE,
            owner_token=OWNER,
            adopted_by_token=None,
            writer_token=OWNER,
            old_phase=tc.LaunchPhase(phase),
            new_phase=tc.LaunchPhase(phase),
        )
        is tc.WriteRefusal.TERMINAL_IMMUTABLE
    )


def test_cp_1b_intent_para_ended_confirmed_so_com_refused_before_creation(db: Db) -> None:
    rid = db.reserve().reservation_id
    lid = db.add_launch(rid)
    message = db.refused_launch_update(
        lid,
        phase="ended_confirmed",
        end_basis="tree_confirmed_dead",
        ended_by_token=OWNER,
    )
    assert "launch_update:edge_illegal" in message


def test_cp_1c_refused_before_creation_nao_vale_depois_de_created(db: Db) -> None:
    rid = db.reserve().reservation_id
    lid = db.add_launch(rid)
    db.created(lid)
    message = db.refused_launch_update(
        lid,
        phase="ended_confirmed",
        end_basis="refused_before_creation",
        ended_by_token=OWNER,
    )
    assert "launch_update:edge_illegal" in message
    # e mesmo que o trigger não existisse, a forma do CHECK também recusa (pid com recusa)
    db.drop_trigger("execution_launch_update_guard")
    message = db.refused_launch_update(
        lid,
        phase="ended_confirmed",
        end_basis="refused_before_creation",
        ended_by_token=OWNER,
    )
    assert "ck_execution_launch_end_shape" in message


def test_recusado_antes_da_criacao_tem_pid_e_token_nulos(db: Db) -> None:
    rid = db.reserve().reservation_id
    lid = db.add_launch(rid)
    db.end(lid, "refused_before_creation")
    assert db.one("SELECT pid, start_token, phase FROM execution_launch WHERE id = ?", (lid,)) == (
        None,
        None,
        "ended_confirmed",
    )
    other = db.add_launch(rid, tag="other")
    message = db.refused_launch_update(
        other,
        version=2,
        phase="ended_confirmed",
        end_basis="refused_before_creation",
        ended_by_token=OWNER,
        pid=7,
        start_token="t",
    )
    assert "launch_update:pid_only_at_created" in message


@pytest.mark.parametrize(
    "field", ["phase_tag", "exe_name", "platform", "containment_profile", "reservation_id"]
)
def test_identidade_do_launch_e_imutavel(db: Db, field: str) -> None:
    rid = db.reserve().reservation_id
    lid = db.add_launch(rid)
    values = {
        "phase_tag": "other",
        "exe_name": "outro.exe",
        "platform": "posix",
        "containment_profile": "posix_pgrp_v1",
        "reservation_id": new_uuid(),
    }
    # aresta LEGAL (intent → created): senão `edge_illegal` dispara antes e mascara a regra
    fields: dict[str, object] = {
        "phase": "created",
        "pid": 1,
        "start_token": "t",
        field: values[field],
    }
    if field == "platform":
        fields["containment_profile"] = "posix_pgrp_v1"
    assert "launch_update:identity_immutable" in db.refused_launch_update(lid, **fields)


def test_pid_e_start_token_nao_mudam_depois_de_gravados(db: Db) -> None:
    rid = db.reserve().reservation_id
    lid = db.add_launch(rid)
    db.created(lid, pid=10)
    assert "identity_immutable" in db.refused_launch_update(lid, phase="contained", pid=11)
    assert "identity_immutable" in db.refused_launch_update(
        lid, phase="contained", start_token="outro"
    )


@pytest.mark.parametrize("version", [1, 3, 0])
def test_versao_do_launch_sobe_de_um_em_um(db: Db, version: int) -> None:
    rid = db.reserve().reservation_id
    lid = db.add_launch(rid)
    message = db.refused_launch_update(
        lid, version=version, phase="created", pid=1, start_token="t"
    )
    assert "launch_update:version_step" in message or "version_positive" in message


def test_somente_o_dono_original_encerra_o_launch(db: Db) -> None:
    rid = db.reserve().reservation_id
    for writer in (OTHER, ADOPTER):
        lid = db.add_launch(rid)
        message = db.refused_launch_update(
            lid,
            phase="ended_confirmed",
            end_basis="refused_before_creation",
            ended_by_token=writer,
        )
        assert "launch_update:writer_not_owner" in message
    ok = db.add_launch(rid)
    db.end(ok, "refused_before_creation", by=OWNER)


# ============================================================ LEDGER × contrato: precedência

LAUNCH_CONFIGS = [
    "active",
    "draining",
    "teardown_pending",
    "unproven",
    "adopted_teardown_pending",
    "adopted_unproven",
]


@pytest.mark.parametrize("name", LAUNCH_CONFIGS)
@pytest.mark.parametrize(
    ("new_phase", "basis", "writer"),
    [
        ("created", None, OWNER),
        ("contained", None, OWNER),  # aresta ilegal a partir de intent
        ("ended_confirmed", "refused_before_creation", OWNER),
        ("ended_confirmed", "refused_before_creation", OTHER),  # escritor errado
        ("ended_confirmed", "tree_confirmed_dead", OWNER),  # base ilegal a partir de intent
    ],
)
def test_update_de_launch_obedece_launch_write_refusal(
    db: Db, name: str, new_phase: str, basis: str | None, writer: str
) -> None:
    # o launch nasce com a reserva ativa e só depois ela muda de configuração
    cycle = db.reserve()
    rid = cycle.reservation_id
    lid = db.add_launch(rid)
    if name == "draining":
        db.to_draining(rid)
    elif name == "teardown_pending":
        db.to_teardown_pending(rid)
    elif name == "unproven":
        db.to_unproven(rid)
    elif name == "adopted_teardown_pending":
        db.adopt(rid)
    elif name == "adopted_unproven":
        db.adopt(rid)
        db.update_reservation(rid, state="unproven", ever_unproven=1, first_unproven_at=ts(10))
    state, adopted, _ = CONFIGS[name]
    fields: dict[str, object] = {"version": 2, "phase": new_phase, "end_basis": basis}
    if new_phase == "created":
        fields.update(pid=5, start_token="t")
    if new_phase.startswith("ended"):
        fields["ended_by_token"] = writer
    expected = tc.launch_write_refusal(
        reservation_state=state,
        owner_token=OWNER,
        adopted_by_token=adopted,
        writer_token=writer,
        old_phase=tc.LaunchPhase.INTENT,
        new_phase=tc.LaunchPhase(new_phase),
        end_basis=None if basis is None else tc.EndBasis(basis),
    )
    try:
        assignments = ", ".join(f"{k} = ?" for k in fields)
        db.run(
            f"UPDATE execution_launch SET {assignments} WHERE id = ?",  # noqa: S608
            (*fields.values(), lid),
        )
        outcome = None
    except sqlite3.IntegrityError as error:
        outcome = str(error)
    if expected is None:
        assert outcome is None, (name, new_phase, basis, writer, outcome)
    else:
        assert outcome is not None and f"launch_update:{expected.value}" in outcome, (
            name,
            new_phase,
            basis,
            writer,
            expected,
            outcome,
        )


def test_launch_de_reserva_liberada_nao_e_atualizado(db: Db) -> None:
    """Um launch `created` com sonda ausente (Windows) qualifica; depois do release, é imutável."""
    cycle = db.reserve()
    rid = cycle.reservation_id
    lid = db.add_launch(rid, tag="other", platform="win32")
    db.created(lid)
    db.add_evidence(rid, "launch", "identity_probe_absent", launch=lid)
    db.binding(rid, "control")
    db.to_draining(rid)
    assert db.release(rid) == 1
    assert "launch_update:reservation_not_writable" in db.refused_launch_update(
        lid, phase="contained"
    )


# ============================================================ proveniência (C8)


def test_recusa_gravada_pelo_dono_antes_da_adocao_continua_valida_depois(db: Db) -> None:
    cycle = build_cycle(db, adopted=True)
    rid = cycle.reservation_id
    refused = db.one(
        "SELECT ended_by_token, end_basis, pid FROM execution_launch"
        " WHERE reservation_id = ? AND phase_tag = 'test_runner'",
        (rid,),
    )
    assert refused == (OWNER, "refused_before_creation", None)
    assert db.decision(rid).verdict is tc.ReleaseVerdict.RELEASE
    assert db.release(rid) == 1


def test_depois_da_adocao_o_adotante_nao_escreve_no_ledger(db: Db) -> None:
    """CP-1d / C8.5 / C8.6: nem INSERT, nem UPDATE de launch."""
    rid = db.reserve().reservation_id
    lid = db.add_launch(rid)
    db.adopt(rid)
    assert "launch_insert:reservation_adopted" in db.refused(
        "INSERT INTO execution_launch (reservation_id, phase_tag, exe_name, platform,"
        " containment_profile, phase, version, created_at) VALUES (?, 'other', 'x', 'win32',"
        " 'win_job_v1', 'intent', 1, ?)",
        (rid, ts()),
    )
    assert "launch_update:reservation_adopted" in db.refused_launch_update(
        lid,
        phase="ended_confirmed",
        end_basis="refused_before_creation",
        ended_by_token=ADOPTER,
    )


def test_evidencia_anterior_a_adocao_continua_valida_e_a_proibida_e_recusada(db: Db) -> None:
    cycle = db.reserve()
    rid = cycle.reservation_id
    db.drained(rid)  # observado ao vivo pelo dono, ANTES da adoção
    db.binding(rid, "control", basis="binding_revoked_observed")
    db.adopt(rid)
    # depois da adoção: nada observado ao vivo, e a quiescência é só do adotante
    assert "evidence_insert:live_basis_after_adoption" in db.refused_evidence(
        rid, "in_process", "executor_drained", writer=OWNER
    )
    assert "evidence_insert:live_basis_after_adoption" in db.refused_evidence(
        rid, "binding", "binding_released_observed", subject="control", writer=OWNER
    )
    assert "evidence_insert:quiescence_needs_adopter" in db.refused_evidence(
        rid,
        "binding",
        "cooperative_owner_quiescence",
        subject="control",
        writer=OWNER,
        logical_key="logical-key-1",
        physical_key="physical-key-1",
        lock_files_match=1,
    )
    # a evidência do dono antes da adoção vale para o control: o release procede
    assert db.decision(rid).verdict is tc.ReleaseVerdict.RELEASE
    assert db.release(rid) == 1


# ============================================================ evidência: forma e proveniência

EVIDENCE_WRITERS = {"owner": OWNER, "adopter": ADOPTER, "other": OTHER}
BASIS_SCOPE = [
    (scope, basis.value)
    for scope, bases in tc.BASES_BY_SCOPE.items()
    for basis in sorted(bases, key=lambda b: b.value)
]


@pytest.mark.parametrize("name", sorted(CONFIGS))
@pytest.mark.parametrize("writer", sorted(EVIDENCE_WRITERS))
@pytest.mark.parametrize(
    ("scope", "basis"),
    [(s.value, b) for s, b in BASIS_SCOPE if s is not tc.EvidenceScope.LAUNCH],
)
def test_insert_de_evidencia_obedece_evidence_write_refusal(
    db: Db, name: str, writer: str, scope: str, basis: str
) -> None:
    rid = _reservation_config(db, name)
    state, adopted, _ = CONFIGS[name]
    expected = tc.evidence_write_refusal(
        basis=tc.EvidenceBasis(basis),
        writer_token=EVIDENCE_WRITERS[writer],
        reservation_state=state,
        owner_token=OWNER,
        adopted_by_token=adopted,
    )
    subject = "control" if scope == "binding" else None
    flags = {
        "logical_key": "logical-key-1",
        "physical_key": "physical-key-1",
        "lock_files_match": 1,
    }
    try:
        db.add_evidence(
            rid,
            scope,
            basis,
            writer=EVIDENCE_WRITERS[writer],
            subject=subject,
            **flags,
        )
        outcome = None
    except sqlite3.IntegrityError as error:
        outcome = str(error)
    if expected is None:
        assert outcome is None, (name, writer, basis, outcome)
    else:
        assert outcome is not None and f"evidence_insert:{expected.value}" in outcome, (
            name,
            writer,
            basis,
            expected,
            outcome,
        )


@pytest.mark.parametrize("name", ["active", "adopted_teardown_pending", "released"])
@pytest.mark.parametrize("writer", sorted(EVIDENCE_WRITERS))
@pytest.mark.parametrize(
    "basis", sorted(b.value for b in tc.BASES_BY_SCOPE[tc.EvidenceScope.LAUNCH])
)
def test_insert_de_evidencia_de_launch_obedece_o_contrato(
    db: Db, name: str, writer: str, basis: str
) -> None:
    cycle = db.reserve()
    rid = cycle.reservation_id
    lid = db.add_launch(rid, tag="other")
    db.created(lid)
    if name == "adopted_teardown_pending":
        db.adopt(rid)
    elif name == "released":
        db.add_evidence(rid, "launch", "identity_probe_absent", launch=lid)
        db.binding(rid, "control")
        db.to_draining(rid)
        assert db.release(rid) == 1
    state, adopted, _ = CONFIGS[name]
    expected = tc.evidence_write_refusal(
        basis=tc.EvidenceBasis(basis),
        writer_token=EVIDENCE_WRITERS[writer],
        reservation_state=state,
        owner_token=OWNER,
        adopted_by_token=adopted,
    )
    try:
        db.add_evidence(
            rid,
            "launch",
            basis,
            writer=EVIDENCE_WRITERS[writer],
            launch=lid,
            supervision_confirmed=1 if basis == "launch_start_failed_proven" else 0,
        )
        outcome = None
    except sqlite3.IntegrityError as error:
        outcome = str(error)
    if expected is None:
        assert outcome is None, (name, writer, basis, outcome)
    else:
        assert outcome is not None and f"evidence_insert:{expected.value}" in outcome


def test_evidencia_de_launch_exige_o_launch_da_mesma_reserva_na_versao_atual(db: Db) -> None:
    first = db.reserve()
    lid = db.add_launch(first.reservation_id, tag="other")
    db.created(lid)
    db.add_evidence(first.reservation_id, "launch", "identity_probe_absent", launch=lid)
    db.binding(first.reservation_id, "control")
    db.to_draining(first.reservation_id)
    assert db.release(first.reservation_id) == 1

    second = db.reserve()
    foreign = db.refused_evidence(
        second.reservation_id, "launch", "probe_present", launch=lid, launch_version=3
    )
    assert "evidence_insert:launch_mismatch" in foreign
    mine = db.add_launch(second.reservation_id, tag="other")
    stale = db.refused_evidence(
        second.reservation_id, "launch", "probe_present", launch=mine, launch_version=2
    )
    assert "evidence_insert:launch_mismatch" in stale
    db.add_evidence(second.reservation_id, "launch", "probe_present", launch=mine)


def test_launch_start_failed_proven_exige_supervisao_confirmada(db: Db) -> None:
    """Uma exceção de `Popen`, por si só, não comprova ausência de processo."""
    rid = db.reserve().reservation_id
    lid = db.add_launch(rid)
    db.end(lid, "popen_raised")
    message = db.refused_evidence(
        rid, "launch", "launch_start_failed_proven", launch=lid, supervision_confirmed=0
    )
    assert "start_failed_needs_supervision" in message
    db.add_evidence(
        rid, "launch", "launch_start_failed_proven", launch=lid, supervision_confirmed=1
    )


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_evidencia_e_append_only(db: Db, operation: str) -> None:
    rid = db.reserve().reservation_id
    seq = db.drained(rid)
    if operation == "update":
        message = db.refused("UPDATE execution_evidence SET boot_changed = 1 WHERE seq = ?", (seq,))
        assert "evidence_update:append_only" in message
    else:
        message = db.refused("DELETE FROM execution_evidence WHERE seq = ?", (seq,))
        assert "evidence_delete:append_only" in message
    assert db.scalar("SELECT COUNT(*) FROM execution_evidence") == 1


def test_launch_e_append_only_para_delete(db: Db) -> None:
    rid = db.reserve().reservation_id
    lid = db.add_launch(rid)
    assert "launch_delete:append_only" in db.refused(
        "DELETE FROM execution_launch WHERE id = ?", (lid,)
    )


def test_filhas_tem_fk_restrict_para_a_reserva(db: Db) -> None:
    for table in ("execution_launch", "execution_evidence"):
        refs = db.run(f"PRAGMA foreign_key_list({table})").fetchall()
        reservation = [r for r in refs if r[2] == "execution_reservation"]
        assert reservation and all(r[6] == "RESTRICT" for r in reservation)


def test_reserva_nao_tem_fk_para_task_workspace_nem_run(db: Db) -> None:
    assert db.run("PRAGMA foreign_key_list(execution_reservation)").fetchall() == []


# ============================================================ purga: Runs, task e workspace


def test_delete_direto_de_run_task_e_workspace_e_recusado_com_reserva_ativa(db: Db) -> None:
    cycle = db.reserve()
    before = (
        db.scalar("SELECT COUNT(*) FROM run"),
        db.scalar("SELECT COUNT(*) FROM workspace_task"),
        db.scalar("SELECT COUNT(*) FROM dev_workspace"),
    )
    assert "purge_blocked:unreleased_reservation_run" in db.refused(
        "DELETE FROM run WHERE id = ?", (cycle.control_run_id,)
    )
    assert "purge_blocked:unreleased_reservation_task" in db.refused(
        "DELETE FROM workspace_task WHERE id = ?", (cycle.task_id,)
    )
    assert "purge_blocked:unreleased_reservation_workspace" in db.refused(
        "DELETE FROM dev_workspace WHERE id = ?", (cycle.workspace_id,)
    )
    after = (
        db.scalar("SELECT COUNT(*) FROM run"),
        db.scalar("SELECT COUNT(*) FROM workspace_task"),
        db.scalar("SELECT COUNT(*) FROM dev_workspace"),
    )
    assert after == before


@pytest.mark.parametrize("state", ["draining", "teardown_pending", "unproven"])
def test_a_protecao_vale_em_todo_estado_nao_liberado(db: Db, state: str) -> None:
    rid = _in_state(db, state)
    workspace_id = db.scalar("SELECT workspace_id FROM execution_reservation WHERE id = ?", (rid,))
    assert "purge_blocked" in db.refused("DELETE FROM dev_workspace WHERE id = ?", (workspace_id,))


def test_delete_em_cascata_do_workspace_e_recusado_por_inteiro(db: Db) -> None:
    """S2: o gatilho do filho dispara na cascata de FK e aborta a instrução inteira."""
    cycle = db.reserve()
    other_task = db.new_task()[1]  # outra task do MESMO workspace, sem reserva
    before = db.scalar("SELECT COUNT(*) FROM workspace_task")
    message = db.refused("DELETE FROM dev_workspace WHERE id = ?", (cycle.workspace_id,))
    assert "purge_blocked" in message
    assert db.scalar("SELECT COUNT(*) FROM workspace_task") == before
    assert db.one("SELECT id FROM workspace_task WHERE id = ?", (other_task,)) is not None


def test_task_sem_reserva_pode_ser_apagada_com_outra_reserva_ativa(db: Db) -> None:
    db.reserve()
    _workspace, other_task = db.new_task()
    db.run("DELETE FROM workspace_task WHERE id = ?", (other_task,))
    assert db.one("SELECT id FROM workspace_task WHERE id = ?", (other_task,)) is None


def test_purga_legitima_depois_de_released_mantem_o_historico(db: Db) -> None:
    cycle = build_cycle(db)
    rid = cycle.reservation_id
    assert db.release(rid) == 1
    launches = db.scalar("SELECT COUNT(*) FROM execution_launch WHERE reservation_id = ?", (rid,))
    evidence = db.scalar("SELECT COUNT(*) FROM execution_evidence WHERE reservation_id = ?", (rid,))
    assert launches and evidence

    db.run("DELETE FROM dev_workspace WHERE id = ?", (cycle.workspace_id,))  # purga: cascata
    assert db.scalar("SELECT COUNT(*) FROM run") == 0
    assert db.scalar("SELECT COUNT(*) FROM workspace_task") == 0
    # o histórico fica, sem FK para o que foi purgado
    assert db.state_of(rid) == "released"
    assert (
        db.scalar("SELECT COUNT(*) FROM execution_launch WHERE reservation_id = ?", (rid,))
        == launches
    )
    assert (
        db.scalar("SELECT COUNT(*) FROM execution_evidence WHERE reservation_id = ?", (rid,))
        == evidence
    )
    # e o slot está livre para uma nova execução
    db.reserve()


def test_purga_so_de_um_run_depois_de_released(db: Db) -> None:
    cycle = minimal_ready(db)
    assert db.release(cycle.reservation_id) == 1
    db.run("DELETE FROM run WHERE id = ?", (cycle.control_run_id,))
    assert db.one("SELECT id FROM run WHERE id = ?", (cycle.control_run_id,)) is None


# ======================================================== release: caminhos válidos e UPDATE cru


@pytest.mark.parametrize("adopted", [False, True])
def test_release_valido_pelo_dono_e_pelo_adotante(db: Db, adopted: bool) -> None:
    cycle = build_cycle(db, adopted=adopted)
    rid = cycle.reservation_id
    assert db.decision(rid).verdict is tc.ReleaseVerdict.RELEASE
    assert db.release(rid) == 1
    row = db.one(
        "SELECT state, released_by_token, release_evidence_hash FROM execution_reservation"
        " WHERE id = ?",
        (rid,),
    )
    assert row == ("released", ADOPTER if adopted else OWNER, SEAL)


@pytest.mark.parametrize("state", ["draining", "teardown_pending", "unproven"])
def test_release_sem_provas_e_recusado_em_todo_estado_liberavel(db: Db, state: str) -> None:
    """UPDATE cru de `state` para `released`, só com o selo: o banco não libera."""
    rid = _in_state(db, state)
    if state == "unproven":
        db.ack(rid)  # o ack não é prova
    assert "release_blocked:binding_unproven" in db.refused_release(rid)
    assert db.state_of(rid) == state


def test_release_a_partir_de_active_e_uma_aresta_ilegal(db: Db) -> None:
    rid = db.reserve().reservation_id
    db.binding(rid, "control")
    assert "reservation_update:edge_illegal" in db.refused_release(rid)


def test_release_com_versao_antiga_nao_afeta_linha_alguma(db: Db) -> None:
    """VERSION_MISMATCH é o CAS do `UPDATE`: a cláusula `WHERE` não casa, nada é liberado."""
    rid = minimal_ready(db).reservation_id
    cursor = db.run(
        "UPDATE execution_reservation SET state = 'released', version = version + 1,"
        " released_at = ?, released_by_token = ?, release_evidence_hash = ?"
        " WHERE id = ? AND state = 'draining' AND version = 1",
        (ts(40), OWNER, SEAL, rid),
    )
    assert cursor.rowcount == 0 and db.state_of(rid) == "draining"


def test_forma_do_release_released_at_por_e_selo_andam_juntos(db: Db) -> None:
    rid = minimal_ready(db).reservation_id
    message = db.refused_update(rid, state="released", released_at=ts(40))
    assert "ck_execution_reservation_released_shape" in message or "release_blocked" in message
    message = db.refused_update(rid, released_at=ts(40))
    assert "ck_execution_reservation_released_shape" in message
