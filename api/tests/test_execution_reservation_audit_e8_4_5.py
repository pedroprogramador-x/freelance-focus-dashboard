"""E8.4.5 / I3 — contraprovas da auditoria do Codex (P1-I3-01 e P1-I3-02).

* **P1-I3-01** — `INSERT OR REPLACE` resolve um conflito de PK/UNIQUE apagando a linha existente, e
  esse DELETE **não dispara** os triggers `BEFORE DELETE` (só com `recursive_triggers=ON`, que é
  por conexão e vem desligado). Os guards de INSERT recusam, antes do conflito, tudo o que
  substituiria uma linha.
* **P1-I3-02** — `seq` explícito retroativo reordenava a evidência: uma negativa gravada depois
  podia receber `seq` menor que o da positiva e deixar de precedê-la.

SQLite real, migration Alembic real, `foreign_keys=ON` e conexão nova a cada teste, com
`recursive_triggers` desligado **e** ligado: a garantia não pode depender do pragma.
(O P2-I3-03, downgrade, está em `test_execution_reservation_schema_e8_4_5.py`.)
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.orchestrator import teardown_contract as tc
from tests.execution_reservation_support_e8_4_5 import (
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


@pytest.fixture(params=["recursive_off", "recursive_on"])
def db(request: pytest.FixtureRequest, template: Path, tmp_path: Path) -> Iterator[Db]:
    database = Db(migrated_copy(template, tmp_path / "t.db"))
    flag = "ON" if request.param == "recursive_on" else "OFF"
    database.run(f"PRAGMA recursive_triggers = {flag}")
    assert database.scalar("PRAGMA recursive_triggers") == (1 if flag == "ON" else 0)
    assert database.scalar("PRAGMA foreign_keys") == 1
    try:
        yield database
    finally:
        database.close()


# ----------------------------------------------------------------------------- apoio

TABLES = ("execution_reservation", "execution_launch", "execution_evidence")
VERBS = ("INSERT", "INSERT OR REPLACE", "INSERT OR IGNORE", "REPLACE")


def dump(db: Db) -> dict[str, list[tuple[Any, ...]]]:
    """Tudo o que pode mudar: as três tabelas e o contador do AUTOINCREMENT."""
    state: dict[str, list[tuple[Any, ...]]] = {
        table: [tuple(r) for r in db.run(f"SELECT * FROM {table} ORDER BY 1").fetchall()]  # noqa: S608
        for table in TABLES
    }
    state["sqlite_sequence"] = [
        tuple(r) for r in db.run("SELECT name, seq FROM sqlite_sequence ORDER BY name").fetchall()
    ]
    return state


def row_of(db: Db, table: str, key: str, value: Any) -> dict[str, Any]:
    cursor = db.run(f"SELECT * FROM {table} WHERE {key} = ?", (value,))  # noqa: S608
    names = [c[0] for c in cursor.description]
    return dict(zip(names, cursor.fetchone(), strict=True))


def put(db: Db, table: str, row: dict[str, Any], verb: str = "INSERT", tail: str = "") -> int:
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    keyword = "REPLACE INTO" if verb == "REPLACE" else f"{verb} INTO"
    cursor = db.run(
        f"{keyword} {table} ({columns}) VALUES ({marks}) {tail}",
        tuple(row.values()),
    )
    return int(cursor.lastrowid or 0)


def refused_put(
    db: Db, table: str, row: dict[str, Any], verb: str = "INSERT", tail: str = ""
) -> str:
    """O banco tem de recusar E deixar tudo como estava (nenhuma alteração parcial)."""
    before = dump(db)
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    keyword = "REPLACE INTO" if verb == "REPLACE" else f"{verb} INTO"
    message = db.refused(
        f"{keyword} {table} ({columns}) VALUES ({marks}) {tail}",
        tuple(row.values()),
    )
    assert dump(db) == before, "a recusa deixou alteração parcial"
    return message


def fresh_reservation(old: dict[str, Any], **override: Any) -> dict[str, Any]:
    """Uma linha de reserva NOVA (fresca, `active`) derivada de `old`."""
    row = {
        **old,
        "state": "active",
        "version": 1,
        "adopted_by_token": None,
        "ever_unproven": 0,
        "first_unproven_at": None,
        "ack_at": None,
        "ack_reason": None,
        "released_at": None,
        "released_by_token": None,
        "release_evidence_hash": None,
        "developer_declared_at": None,
        "drain_requested_at": None,
        "draining_at": None,
    }
    row.update(override)
    return row


# ============================================================ P1-I3-01 — as quatro contraprovas


@pytest.mark.parametrize("verb", VERBS)
def test_replace_de_negativa_de_binding_por_positiva_e_recusado(db: Db, verb: str) -> None:
    rid = db.reserve().reservation_id
    negative = db.add_evidence(rid, "binding", "binding_unproven", subject="control")
    db.to_draining(rid)
    assert "release_blocked:binding_unproven" in db.refused_release(rid)

    attack = db.evidence_row(
        rid, "binding", "binding_released_observed", subject="control", seq=negative
    )
    message = refused_put(db, "execution_evidence", attack, verb)

    assert "evidence_insert:seq_conflict" in message
    assert "release_blocked:binding_unproven" in db.refused_release(rid)  # a negativa vive
    assert db.decision(rid).verdict is not tc.ReleaseVerdict.RELEASE


@pytest.mark.parametrize("verb", VERBS)
def test_replace_de_reserva_unproven_nao_apaga_ever_unproven(db: Db, verb: str) -> None:
    rid = db.reserve().reservation_id
    db.to_unproven(rid)
    old = row_of(db, "execution_reservation", "id", rid)

    message = refused_put(db, "execution_reservation", fresh_reservation(old), verb)

    assert "reservation_insert:id_conflict" in message
    now = db.one(
        "SELECT state, ever_unproven, first_unproven_at FROM execution_reservation WHERE id = ?",
        (rid,),
    )
    assert now == ("unproven", 1, ts(10))


@pytest.mark.parametrize("verb", VERBS)
def test_replace_de_launch_created_por_intent_e_recusado(db: Db, verb: str) -> None:
    rid = db.reserve().reservation_id
    lid = db.add_launch(rid, tag="other")
    db.created(lid)
    old = row_of(db, "execution_launch", "id", lid)
    intent = {**old, "phase": "intent", "version": 1, "pid": None, "start_token": None}

    message = refused_put(db, "execution_launch", intent, verb)

    assert "launch_insert:id_conflict" in message
    assert db.one("SELECT phase, version, pid FROM execution_launch WHERE id = ?", (lid,)) == (
        "created",
        2,
        4242,
    )


@pytest.mark.parametrize("verb", VERBS)
def test_replace_de_reserva_ativa_por_outra_pelo_slot_global_e_recusado(db: Db, verb: str) -> None:
    first = db.reserve().reservation_id
    old = row_of(db, "execution_reservation", "id", first)
    other = fresh_reservation(old, id="outra-reserva", control_run_id="outro-controle")

    message = refused_put(db, "execution_reservation", other, verb)

    assert "reservation_insert:slot_conflict" in message
    assert [r[0] for r in db.run("SELECT id FROM execution_reservation")] == [first]


@pytest.mark.parametrize("verb", VERBS)
def test_control_run_id_de_historico_nao_e_substituido(db: Db, verb: str) -> None:
    """O slot está livre (a reserva está `released`): só o `control_run_id` conflita."""
    rid = minimal_ready(db).reservation_id
    assert db.release(rid) == 1
    old = row_of(db, "execution_reservation", "id", rid)
    other = fresh_reservation(old, id="nova-tentativa")  # mesmo control_run_id

    message = refused_put(db, "execution_reservation", other, verb)

    assert "reservation_insert:control_run_id_conflict" in message
    assert db.state_of(rid) == "released"


@pytest.mark.parametrize("verb", VERBS)
def test_o_slot_conflita_mesmo_com_id_e_control_run_id_novos(db: Db, verb: str) -> None:
    db.reserve()
    old = row_of(db, "execution_reservation", "slot_key", 1)
    other = fresh_reservation(old, id="x", control_run_id="y")
    assert "reservation_insert:slot_conflict" in refused_put(
        db, "execution_reservation", other, verb
    )


# ---------------------------------------------------------------- UPSERT


def test_upsert_da_reserva_nao_contorna_os_guards(db: Db) -> None:
    rid = db.reserve().reservation_id
    db.to_unproven(rid)
    attack = fresh_reservation(row_of(db, "execution_reservation", "id", rid))
    for tail in (
        "ON CONFLICT(id) DO UPDATE SET ever_unproven = 0, state = 'active'",
        "ON CONFLICT(id) DO NOTHING",
        "ON CONFLICT DO NOTHING",
    ):
        assert "reservation_insert:id_conflict" in refused_put(
            db, "execution_reservation", attack, "INSERT", tail
        )


def test_upsert_do_launch_nao_contorna_os_guards(db: Db) -> None:
    rid = db.reserve().reservation_id
    lid = db.add_launch(rid, tag="other")
    db.created(lid)
    intent_again = {**row_of(db, "execution_launch", "id", lid), "phase": "intent", "version": 1}
    for tail in (
        "ON CONFLICT(id) DO UPDATE SET phase = 'intent', version = 1",
        "ON CONFLICT(id) DO NOTHING",
    ):
        assert "launch_insert:id_conflict" in refused_put(
            db, "execution_launch", intent_again, "INSERT", tail
        )


def test_upsert_da_evidencia_nao_contorna_os_guards(db: Db) -> None:
    rid = db.reserve().reservation_id
    neg = db.add_evidence(rid, "binding", "binding_unproven", subject="control")
    attack = db.evidence_row(
        rid, "binding", "binding_released_observed", subject="control", seq=neg
    )
    for tail in (
        "ON CONFLICT(seq) DO UPDATE SET basis = 'binding_released_observed'",
        "ON CONFLICT(seq) DO NOTHING",
    ):
        assert "evidence_insert:seq_conflict" in refused_put(
            db, "execution_evidence", attack, "INSERT", tail
        )


def test_update_or_replace_tambem_nao_reorganiza(db: Db) -> None:
    first = db.reserve().reservation_id
    seq_a = db.add_evidence(first, "binding", "binding_unproven", subject="control")
    seq_b = db.add_evidence(first, "binding", "binding_unproven", subject="developer")
    before = dump(db)
    assert "evidence_update:append_only" in db.refused(
        "UPDATE OR REPLACE execution_evidence SET seq = ? WHERE seq = ?", (seq_a, seq_b)
    )
    assert "reservation_update:identity_immutable" in db.refused(
        "UPDATE OR REPLACE execution_reservation SET control_run_id = 'outro' WHERE id = ?",
        (first,),
    )
    assert dump(db) == before


# ---------------------------------------------------------------- caminhos legítimos preservados


def test_inserts_legitimos_continuam_funcionando_em_todas_as_formas(db: Db) -> None:
    # reserva: normal, depois de liberar a anterior (OR REPLACE sem conflito é um INSERT)
    first = minimal_ready(db)
    assert db.release(first.reservation_id) == 1
    second = db.reserve()
    rid = second.reservation_id
    assert db.state_of(rid) == "active"

    # launch sem id explícito, com id explícito inédito, por INSERT OR REPLACE sem conflito
    base = {
        "reservation_id": rid,
        "phase_tag": "other",
        "exe_name": "x.exe",
        "platform": "win32",
        "containment_profile": "win_job_v1",
        "phase": "intent",
        "version": 1,
        "created_at": ts(),
    }
    auto = put(db, "execution_launch", base)
    replaced = put(db, "execution_launch", base, "INSERT OR REPLACE")
    explicit = put(db, "execution_launch", {**base, "id": replaced + 50})
    ignored = put(db, "execution_launch", base, "INSERT OR IGNORE")
    assert auto < replaced < explicit < ignored

    # evidência: sem seq, e com seq explícito MAIOR que o maior
    ev = db.evidence_row(rid, "in_process", "drain_timeout")
    one = put(db, "execution_evidence", ev)
    two = put(db, "execution_evidence", ev, "INSERT OR REPLACE")
    far = put(db, "execution_evidence", {**ev, "seq": two + 500})
    nxt = put(db, "execution_evidence", ev)  # o AUTOINCREMENT continua do maior
    assert one < two < far < nxt == far + 1


def test_um_insert_que_falha_nao_deixa_nada_para_tras(db: Db) -> None:
    rid = db.reserve().reservation_id
    db.add_evidence(rid, "binding", "binding_unproven", subject="control", seq=10)
    before = dump(db)
    good = db.evidence_row(rid, "in_process", "drain_timeout", seq=20)
    bad = db.evidence_row(rid, "in_process", "drain_timeout", seq=5)
    columns = ", ".join(good)
    marks = ", ".join("?" for _ in good)
    message = db.refused(
        f"INSERT INTO execution_evidence ({columns}) VALUES ({marks}), ({marks})",  # noqa: S608
        (*good.values(), *bad.values()),
    )
    assert "evidence_insert:seq_not_increasing" in message
    assert dump(db) == before, "a linha boa do mesmo INSERT sobreviveu à falha da outra"


# ============================================================ P1-I3-02 — seq retroativo


def _declared_cycle(db: Db, *, positives_at: int = 100) -> str:
    """Developer declarado, com as positivas de drenagem e bindings em `seq` explícitos."""
    cycle = db.reserve()
    rid = cycle.reservation_id
    db.declare_developer(rid)
    db.add_run(
        cycle.task_id, "developer", invocation_id=f"{cycle.control_run_id}:developer", status="ok"
    )
    db.add_evidence(rid, "in_process", "executor_drained", seq=positives_at)
    db.add_evidence(
        rid, "binding", "binding_released_observed", subject="control", seq=positives_at + 1
    )
    db.add_evidence(
        rid, "binding", "binding_released_observed", subject="developer", seq=positives_at + 2
    )
    db.to_draining(rid)
    return rid


def test_o_estado_de_base_e_liberavel(db: Db) -> None:
    rid = _declared_cycle(db)
    assert db.decision(rid).verdict is tc.ReleaseVerdict.RELEASE


@pytest.mark.parametrize(
    ("scope", "basis", "subject", "blocker"),
    [
        ("binding", "binding_unproven", "control", "binding_unproven"),
        ("binding", "binding_unproven", "developer", "binding_unproven"),
        ("in_process", "drain_timeout", None, "drain_unproven"),
        ("in_process", "drain_reentrant", None, "drain_unproven"),
        ("in_process", "quiescence_unproven", None, "drain_unproven"),
    ],
)
def test_negativa_com_seq_retroativo_e_recusada(
    db: Db, scope: str, basis: str, subject: str | None, blocker: str
) -> None:
    rid = _declared_cycle(db)
    before = dump(db)
    message = db.refused_evidence(rid, scope, basis, subject=subject, seq=50)
    assert "evidence_insert:seq_not_increasing" in message
    assert dump(db) == before
    assert db.release(rid) == 1  # o ciclo legítimo continua liberável


@pytest.mark.parametrize(
    ("scope", "basis", "subject", "blocker"),
    [
        ("binding", "binding_unproven", "control", "binding_unproven"),
        ("in_process", "drain_timeout", None, "drain_unproven"),
    ],
)
def test_a_precedencia_das_negativas_segue_a_ordem_real_de_insercao(
    db: Db, scope: str, basis: str, subject: str | None, blocker: str
) -> None:
    rid = _declared_cycle(db)
    negative = db.add_evidence(rid, scope, basis, subject=subject)  # seq automático, depois
    assert negative > 102
    assert f"release_blocked:{blocker}" in db.refused_release(rid)
    assert db.decision(rid).verdict is not tc.ReleaseVerdict.RELEASE


def test_positiva_depois_da_negativa_continua_liberando(db: Db) -> None:
    cycle = db.reserve()
    rid = cycle.reservation_id
    db.add_evidence(rid, "binding", "binding_unproven", subject="control")
    db.binding(rid, "control")  # a positiva vem DEPOIS
    db.to_draining(rid)
    assert db.decision(rid).verdict is tc.ReleaseVerdict.RELEASE
    assert db.release(rid) == 1


def test_seq_explicito_igual_ou_menor_e_recusado_e_maior_e_aceito(db: Db) -> None:
    rid = db.reserve().reservation_id
    assert db.add_evidence(rid, "in_process", "drain_timeout", seq=100) == 100
    base = db.evidence_row(rid, "in_process", "drain_timeout")
    assert "seq_conflict" in refused_put(db, "execution_evidence", {**base, "seq": 100})
    for seq in (99, 50, 1):
        assert "seq_not_increasing" in refused_put(db, "execution_evidence", {**base, "seq": seq})
    assert put(db, "execution_evidence", {**base, "seq": 101}) == 101
    assert put(db, "execution_evidence", base) == 102


@pytest.mark.parametrize("seq", [0, -5, -1])
def test_seq_nao_positivo_e_recusado_e_desfaz_a_insercao(db: Db, seq: int) -> None:
    """`-1` é indistinguível de "automático" no BEFORE: quem o recusa é o AFTER INSERT."""
    rid = db.reserve().reservation_id
    before = dump(db)
    base = db.evidence_row(rid, "in_process", "drain_timeout")
    assert "seq_not_increasing" in refused_put(db, "execution_evidence", {**base, "seq": seq})
    assert dump(db) == before  # nem linha, nem avanço do sqlite_sequence
    assert db.add_evidence(rid, "in_process", "drain_timeout") == 1


def test_o_after_insert_sozinho_reafirma_a_ordem_sem_o_guard_before(db: Db) -> None:
    """Defesa em profundidade: sem o BEFORE, o AFTER ainda desfaz a inserção fora de ordem."""
    rid = db.reserve().reservation_id
    db.add_evidence(rid, "in_process", "drain_timeout", seq=100)
    db.drop_trigger("execution_evidence_insert_guard")
    before = dump(db)
    base = db.evidence_row(rid, "in_process", "drain_timeout")
    assert "evidence_insert:seq_not_increasing" in refused_put(
        db, "execution_evidence", {**base, "seq": 50}
    )
    assert dump(db) == before
    assert db.add_evidence(rid, "in_process", "drain_timeout") == 101  # AUTOINCREMENT intacto


def test_o_historico_nao_e_reorganizavel_por_update_nem_delete(db: Db) -> None:
    rid = db.reserve().reservation_id
    seq = db.add_evidence(rid, "binding", "binding_unproven", subject="control")
    before = dump(db)
    assert "evidence_update:append_only" in db.refused(
        "UPDATE execution_evidence SET seq = 9999 WHERE seq = ?", (seq,)
    )
    assert "evidence_delete:append_only" in db.refused(
        "DELETE FROM execution_evidence WHERE seq = ?", (seq,)
    )
    assert dump(db) == before


@pytest.mark.parametrize("launch_id", [0, -1, -9])
def test_id_de_launch_nao_positivo_e_recusado(db: Db, launch_id: int) -> None:
    rid = db.reserve().reservation_id
    before = dump(db)
    row = {
        "id": launch_id,
        "reservation_id": rid,
        "phase_tag": "other",
        "exe_name": "x.exe",
        "platform": "win32",
        "containment_profile": "win_job_v1",
        "phase": "intent",
        "version": 1,
        "created_at": ts(),
    }
    message = db.refused(
        f"INSERT INTO execution_launch ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",  # noqa: S608
        tuple(row.values()),
    )
    assert "id_not_positive" in message
    assert dump(db) == before


def test_ciclos_legitimos_de_release_continuam_funcionando(db: Db) -> None:
    for adopted in (False, True):  # o caminho do dono e o do adotante
        cycle = build_cycle(db, adopted=adopted)
        assert db.decision(cycle.reservation_id).verdict is tc.ReleaseVerdict.RELEASE
        assert db.release(cycle.reservation_id) == 1


def test_as_garantias_valem_numa_conexao_nova(db: Db) -> None:
    """O guard mora no schema: outra conexão, com os pragmas padrão, é barrada do mesmo jeito."""
    rid = db.reserve().reservation_id
    neg = db.add_evidence(rid, "binding", "binding_unproven", subject="control")
    other = Db(db.path)
    try:
        assert other.scalar("PRAGMA recursive_triggers") == 0
        attack = other.evidence_row(
            rid, "binding", "binding_released_observed", subject="control", seq=neg
        )
        message = refused_put(other, "execution_evidence", attack, "INSERT OR REPLACE")
        assert "evidence_insert:seq_conflict" in message
    finally:
        other.close()
