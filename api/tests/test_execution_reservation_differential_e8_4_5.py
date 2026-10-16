"""E8.4.5 / I3 — comparação diferencial: contrato I1 (Python) × triggers reais (SQLite).

O banco é o **piso** estrutural; `decide_release` é o predicado completo. Sob lease consistente e
histórico comprovado atual (o que o banco não enxerga), os dois têm de **concordar**: `RELEASE` se e
somente se o `UPDATE` de release é aceito. Quando o banco recusa, a regra que disparou tem de
corresponder a um bloqueador que o contrato também apontou — o oráculo não é só o veredito.

Três camadas:

1. casos escritos à mão: ciclos válidos, defeitos únicos (CP-2a) e controles qualificadores;
2. os `CHECK` das tabelas contra os construtores validados do contrato (`LaunchRecord`,
   `EvidenceRecord`, `ReservationFacts`), por enumeração;
3. um *fuzz* com semente fixa sobre operações legais e ilegais, que também exercita as recusas.

O predicado Python e o SQL partem da mesma especificação; erro comum aos dois é possível, e por isso
os defeitos únicos de (1) são independentes do fuzz. R5a (término gravado por terceiro) é
**sombreada** por R1 e fica como diagnóstico; os testes de proveniência abaixo removem o trigger de
escrita para exercitá-la.
"""

from __future__ import annotations

import contextlib
import itertools
import random
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from app.orchestrator import teardown_contract as tc
from tests.execution_reservation_support_e8_4_5 import (
    ADOPTER,
    LOGICAL,
    OTHER,
    OWNER,
    PHYSICAL,
    SEAL,
    Cycle,
    Db,
    build_cycle,
    make_template,
    migrated_copy,
    ts,
)

BC = tc.BlockerCode


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


# ----------------------------------------------------------------------------- oráculo

LAUNCH_FAMILY = frozenset(
    {
        BC.LAUNCH_UNRESOLVED,
        BC.LAUNCH_STILL_ACTIVE,
        BC.LAUNCH_INTENT_WITHOUT_TERMINAL,
        BC.EVIDENCE_STALE,
        BC.EVIDENCE_CONTRADICTED,
        BC.CONTRADICTION_PROVENANCE,
    }
)


def blocker_codes_for(message: str) -> frozenset[BC]:
    """Que bloqueadores do contrato correspondem à regra SQL que disparou."""
    if message.startswith("release_blocked:"):
        code = message.split(":", 1)[1]
        if code == "launch_unresolved":
            return LAUNCH_FAMILY
        return frozenset({BC(code)})
    if "edge_illegal" in message or "released_immutable" in message:
        return frozenset({BC.EDGE_ILLEGAL})
    if "ack_history" in message:
        return frozenset({BC.ACK_REQUIRED})
    if "released_shape" in message:
        return frozenset({BC.SEAL_MISSING, BC.RELEASER_NOT_PARTICIPANT})
    raise AssertionError(f"recusa do banco sem correspondente no contrato: {message}")


@dataclass
class Outcome:
    decision: tc.ReleaseDecision
    sql_released: bool
    message: str | None
    blockers: frozenset[BC] = field(default_factory=frozenset)

    @property
    def python_releases(self) -> bool:
        return self.decision.verdict is tc.ReleaseVerdict.RELEASE


def compare(db: Db, rid: str, *, by: str | None = None, seal: str | None = SEAL) -> Outcome:
    """Pergunta aos dois lados — o Python **antes**: o `UPDATE` bem-sucedido muda o estado."""
    decision = db.decision(rid, by=by, seal=seal is not None)
    try:
        released = db.release(rid, by=by, seal=seal) == 1
        message = None
    except sqlite3.IntegrityError as error:
        released, message = False, str(error)
    outcome = Outcome(decision, released, message, frozenset(b.code for b in decision.blockers))
    assert outcome.python_releases is released, (decision, message)
    if not released:
        assert message is not None
        matched = blocker_codes_for(message)
        assert matched & outcome.blockers, (
            f"a regra SQL ({message}) não corresponde a nenhum bloqueador do contrato "
            f"({sorted(c.value for c in outcome.blockers)})"
        )
    return outcome


# ----------------------------------------------------------------------------- ganchos de cenário


def other_launch(db: Db, c: Cycle, *, platform: str = "win32") -> int:
    return db.add_launch(c.reservation_id, tag="other", platform=platform)


def h_intent_aberto(db: Db, c: Cycle) -> None:
    other_launch(db, c)


def h_created_aberto(db: Db, c: Cycle) -> None:
    db.created(other_launch(db, c))


def h_contained_aberto(db: Db, c: Cycle) -> None:
    lid = other_launch(db, c)
    db.created(lid)
    db.contained(lid)


def h_popen_raised_sem_prova(db: Db, c: Cycle) -> None:
    db.end(other_launch(db, c), "popen_raised")


def h_popen_raised_provado(db: Db, c: Cycle) -> None:
    lid = other_launch(db, c)
    db.end(lid, "popen_raised")
    db.add_evidence(
        c.reservation_id,
        "launch",
        "launch_start_failed_proven",
        launch=lid,
        supervision_confirmed=1,
    )


def h_created_sonda_ausente(db: Db, c: Cycle) -> None:
    lid = other_launch(db, c)
    db.created(lid)
    db.add_evidence(c.reservation_id, "launch", "identity_probe_absent", launch=lid)


def h_posix_created_sonda_ausente(db: Db, c: Cycle) -> None:
    lid = other_launch(db, c, platform="posix")
    db.created(lid)
    db.add_evidence(c.reservation_id, "launch", "identity_probe_absent", launch=lid)


def h_evidencia_defasada(db: Db, c: Cycle) -> None:
    lid = other_launch(db, c)
    db.created(lid)
    db.add_evidence(c.reservation_id, "launch", "identity_probe_absent", launch=lid)
    db.contained(lid)  # a versão do launch subiu: a evidência ficou para trás


def h_encerrado_sem_confirmacao_depois_da_evidencia(db: Db, c: Cycle) -> None:
    lid = other_launch(db, c)
    db.created(lid)
    db.add_evidence(c.reservation_id, "launch", "identity_probe_absent", launch=lid)
    db.end(lid, "tree_unconfirmed")


def h_reinicio_comprovado(db: Db, c: Cycle) -> None:
    lid = other_launch(db, c)
    db.created(lid)
    db.add_evidence(
        c.reservation_id,
        "launch",
        "machine_restarted_since_declaration",
        launch=lid,
        boot_changed=1,
    )


def h_reinicio_sem_boot_alterado(db: Db, c: Cycle) -> None:
    lid = other_launch(db, c)
    db.created(lid)
    db.add_evidence(
        c.reservation_id,
        "launch",
        "machine_restarted_since_declaration",
        launch=lid,
        boot_changed=0,
    )


def h_reinicio_em_launch_encerrado_confirmado_nao_conta(db: Db, c: Cycle) -> None:
    """`machine_restarted…` não vale para quem já está `ended_confirmed` (não é a prova dele)."""
    lid = other_launch(db, c)
    db.end(lid, "refused_before_creation")
    db.add_evidence(c.reservation_id, "launch", "probe_present", launch=lid)


def h_reinicio_nao_rescata_launch_encerrado_e_contradito(db: Db, c: Cycle) -> None:
    """`machine_restarted…` não vale para `ended_confirmed`: a contradição continua de pé."""
    lid = other_launch(db, c)
    db.end(lid, "refused_before_creation")
    db.add_evidence(c.reservation_id, "launch", "probe_present", launch=lid)
    db.add_evidence(
        c.reservation_id,
        "launch",
        "machine_restarted_since_declaration",
        launch=lid,
        boot_changed=1,
    )


def h_contained_job(db: Db, c: Cycle) -> None:
    lid = other_launch(db, c)
    db.created(lid)
    db.contained(lid)


JOB = {
    "profile_ok": 1,
    "owner_quiescence_ok": 1,
    "root_probe_absent": 1,
    "logical_key": LOGICAL,
    "physical_key": PHYSICAL,
    "lock_files_match": 1,
}


def only_launch(db: Db, c: Cycle) -> int:
    return int(
        db.scalar(
            "SELECT id FROM execution_launch WHERE reservation_id = ? AND phase_tag = 'other'",
            (c.reservation_id,),
        )
    )


def a_job_guarantee(**override: Any) -> Callable[[Db, Cycle], None]:
    def apply(db: Db, c: Cycle) -> None:
        db.add_evidence(
            c.reservation_id,
            "launch",
            "job_guarantee_verified",
            writer=ADOPTER,
            launch=only_launch(db, c),
            **{**JOB, **override},
        )

    return apply


def a_sonda_ausente_adotante(db: Db, c: Cycle) -> None:
    db.add_evidence(
        c.reservation_id,
        "launch",
        "identity_probe_absent",
        writer=ADOPTER,
        launch=only_launch(db, c),
    )


def a_negativa(
    scope: str, basis: str, subject: str | None = None, launch: bool = False
) -> Callable[[Db, Cycle], None]:
    def apply(db: Db, c: Cycle) -> None:
        db.add_evidence(
            c.reservation_id,
            scope,
            basis,
            writer=OWNER,
            subject=subject,
            launch=only_launch(db, c) if launch else None,
        )

    return apply


def a_negativa_no_primeiro_launch(basis: str) -> Callable[[Db, Cycle], None]:
    def apply(db: Db, c: Cycle) -> None:
        first = int(
            db.scalar(
                "SELECT MIN(id) FROM execution_launch WHERE reservation_id = ?", (c.reservation_id,)
            )
        )
        db.add_evidence(c.reservation_id, "launch", basis, writer=OWNER, launch=first)

    return apply


def a_quiescencia_errada(**override: Any) -> Callable[[Db, Cycle], None]:
    def apply(db: Db, c: Cycle) -> None:
        row = {"logical_key": LOGICAL, "physical_key": PHYSICAL, "lock_files_match": 1, **override}
        db.add_evidence(
            c.reservation_id,
            "binding",
            "cooperative_owner_quiescence",
            writer=ADOPTER,
            subject="control",
            **row,
        )

    return apply


def a_unproven(ack: bool) -> Callable[[Db, Cycle], None]:
    def apply(db: Db, c: Cycle) -> None:
        rid = c.reservation_id
        if db.state_of(rid) == "draining":
            db.to_teardown_pending(rid)
        db.update_reservation(rid, state="unproven", ever_unproven=1, first_unproven_at=ts(10))
        if ack:
            db.ack(rid)

    return apply


def both(*hooks: Callable[[Db, Cycle], None]) -> Callable[[Db, Cycle], None]:
    def apply(db: Db, c: Cycle) -> None:
        for hook in hooks:
            hook(db, c)

    return apply


# ----------------------------------------------------------------------------- casos válidos


@dataclass(frozen=True)
class Case:
    name: str
    adopted: bool = False
    build: dict[str, Any] = field(default_factory=dict)
    release: dict[str, Any] = field(default_factory=dict)
    expect: frozenset[BC] = (
        frozenset()
    )  # defeitos: ao menos um destes tem de estar nos bloqueadores


VALID = [
    Case("dono"),
    Case("adotante", adopted=True),
    Case("dono, popen_raised provado", build={"before_close": h_popen_raised_provado}),
    Case(
        "dono, created com sonda ausente (Windows)", build={"before_close": h_created_sonda_ausente}
    ),
    Case("dono, reinício comprovado", build={"before_close": h_reinicio_comprovado}),
    Case(
        "adotante, created com sonda ausente",
        adopted=True,
        build={"before_close": h_created_aberto, "after": a_sonda_ausente_adotante},
    ),
    Case(
        "adotante, contained com garantia do Job",
        adopted=True,
        build={"before_close": h_contained_job, "after": a_job_guarantee()},
    ),
    Case("dono, unproven com ack", build={"after": a_unproven(ack=True)}),
    Case("adotante, unproven com ack", adopted=True, build={"after": a_unproven(ack=True)}),
]


def _run_case(db: Db, case: Case) -> Outcome:
    cycle = build_cycle(db, adopted=case.adopted, **case.build)
    return compare(db, cycle.reservation_id, **case.release)


@pytest.mark.parametrize("case", VALID, ids=[c.name for c in VALID])
def test_ciclos_validos_sao_liberados_pelo_contrato_e_pelo_banco(db: Db, case: Case) -> None:
    outcome = _run_case(db, case)
    assert outcome.python_releases and outcome.sql_released, outcome


# ----------------------------------------------------------------------------- defeitos únicos

DEFECTS = [
    # R1 — launches
    Case(
        "launch em intent sem desfecho",
        build={"before_close": h_intent_aberto},
        expect=frozenset({BC.LAUNCH_INTENT_WITHOUT_TERMINAL}),
    ),
    Case(
        "launch created sem prova",
        build={"before_close": h_created_aberto},
        expect=frozenset({BC.LAUNCH_UNRESOLVED}),
    ),
    Case(
        "launch contained sem prova (dono)",
        build={"before_close": h_contained_job},
        expect=frozenset({BC.LAUNCH_UNRESOLVED}),
    ),
    Case(
        "popen_raised sem a evidência",
        build={"before_close": h_popen_raised_sem_prova},
        expect=frozenset({BC.LAUNCH_UNRESOLVED}),
    ),
    Case(
        "sonda ausente em launch POSIX não qualifica",
        build={"before_close": h_posix_created_sonda_ausente},
        expect=frozenset({BC.LAUNCH_UNRESOLVED}),
    ),
    Case(
        "evidência de versão defasada",
        build={"before_close": h_evidencia_defasada},
        expect=frozenset({BC.EVIDENCE_STALE}),
    ),
    Case(
        "término sem confirmação depois da evidência",
        build={"before_close": h_encerrado_sem_confirmacao_depois_da_evidencia},
        expect=frozenset({BC.EVIDENCE_STALE, BC.LAUNCH_UNRESOLVED}),
    ),
    Case(
        "reinício sem boot alterado",
        build={"before_close": h_reinicio_sem_boot_alterado},
        expect=frozenset({BC.LAUNCH_UNRESOLVED}),
    ),
    Case(
        "negativa posterior à evidência qualificadora",
        build={
            "before_close": h_created_sonda_ausente,
            "after": a_negativa("launch", "probe_present", launch=True),
        },
        expect=frozenset({BC.EVIDENCE_CONTRADICTED}),
    ),
    Case(
        "reinício não rescata launch ended_confirmed contradito",
        build={"before_close": h_reinicio_nao_rescata_launch_encerrado_e_contradito},
        expect=frozenset({BC.EVIDENCE_CONTRADICTED}),
    ),
    Case(
        "launch_unconfirmed_observed posterior",
        build={
            "before_close": h_created_sonda_ausente,
            "after": a_negativa("launch", "launch_unconfirmed_observed", launch=True),
        },
        expect=frozenset({BC.EVIDENCE_CONTRADICTED}),
    ),
    Case(
        "negativa posterior em launch ended_confirmed (R5 C-b)",
        build={"after": a_negativa_no_primeiro_launch("probe_present")},
        expect=frozenset({BC.EVIDENCE_CONTRADICTED}),
    ),
    Case(
        "negativa posterior em ended_confirmed, adotante",
        adopted=True,
        build={"after": a_negativa_no_primeiro_launch("launch_unconfirmed_observed")},
        expect=frozenset({BC.EVIDENCE_CONTRADICTED}),
    ),
    Case(
        "garantia do Job no caminho do dono",
        build={"before_close": h_contained_job},
        expect=frozenset({BC.LAUNCH_UNRESOLVED}),
    ),
    Case(
        "garantia do Job sem profile_ok",
        adopted=True,
        build={"before_close": h_contained_job, "after": a_job_guarantee(profile_ok=0)},
        expect=frozenset({BC.LAUNCH_UNRESOLVED}),
    ),
    Case(
        "garantia do Job sem owner_quiescence_ok",
        adopted=True,
        build={"before_close": h_contained_job, "after": a_job_guarantee(owner_quiescence_ok=0)},
        expect=frozenset({BC.LAUNCH_UNRESOLVED}),
    ),
    Case(
        "garantia do Job com a raiz presente",
        adopted=True,
        build={"before_close": h_contained_job, "after": a_job_guarantee(root_probe_absent=0)},
        expect=frozenset({BC.LAUNCH_UNRESOLVED}),
    ),
    Case(
        "garantia do Job sem lock_files_match",
        adopted=True,
        build={"before_close": h_contained_job, "after": a_job_guarantee(lock_files_match=0)},
        expect=frozenset({BC.LAUNCH_UNRESOLVED}),
    ),
    Case(
        "garantia do Job com chave lógica de outro banco",
        adopted=True,
        build={"before_close": h_contained_job, "after": a_job_guarantee(logical_key="outra")},
        expect=frozenset({BC.LAUNCH_UNRESOLVED}),
    ),
    Case(
        "garantia do Job com chave física de outro banco",
        adopted=True,
        build={"before_close": h_contained_job, "after": a_job_guarantee(physical_key="outra")},
        expect=frozenset({BC.LAUNCH_UNRESOLVED}),
    ),
    # R2 — Runs
    Case(
        "Run do Test Runner ainda running",
        build={"runner_status": "running"},
        expect=frozenset({BC.RUN_OPEN}),
    ),
    Case(
        "Run de controle ausente",
        build={"control_run": False},
        expect=frozenset({BC.RUN_CONTROL_MISSING}),
    ),
    # R3 — drenagem
    Case(
        "drenagem ausente (dono)",
        build={"omit": frozenset({"drain"})},
        expect=frozenset({BC.DRAIN_UNPROVEN}),
    ),
    Case(
        "quiescência do processo ausente (adotante)",
        adopted=True,
        build={"omit": frozenset({"drain"})},
        expect=frozenset({BC.DRAIN_UNPROVEN}),
    ),
    Case(
        "drain_timeout posterior",
        build={"after": a_negativa("in_process", "drain_timeout")},
        expect=frozenset({BC.DRAIN_UNPROVEN}),
    ),
    Case(
        "drain_reentrant posterior",
        build={"after": a_negativa("in_process", "drain_reentrant")},
        expect=frozenset({BC.DRAIN_UNPROVEN}),
    ),
    Case(
        "quiescence_unproven posterior (adotante)",
        adopted=True,
        build={"after": a_negativa("in_process", "quiescence_unproven")},
        expect=frozenset({BC.DRAIN_UNPROVEN}),
    ),
    # R4 — bindings
    Case(
        "binding de controle ausente",
        build={"omit": frozenset({"bind:control"})},
        expect=frozenset({BC.BINDING_UNPROVEN}),
    ),
    Case(
        "binding do Developer ausente",
        build={"omit": frozenset({"bind:developer"})},
        expect=frozenset({BC.BINDING_UNPROVEN}),
    ),
    Case(
        "binding do Test Runner ausente",
        build={"omit": frozenset({"bind:test_runner"})},
        expect=frozenset({BC.BINDING_UNPROVEN}),
    ),
    Case(
        "binding do Developer ausente (adotante)",
        adopted=True,
        build={"omit": frozenset({"bind:developer"})},
        expect=frozenset({BC.BINDING_UNPROVEN}),
    ),
    Case(
        "binding_unproven posterior",
        build={"after": a_negativa("binding", "binding_unproven", subject="developer")},
        expect=frozenset({BC.BINDING_UNPROVEN}),
    ),
    Case(
        "quiescência do binding com chave de outro banco",
        adopted=True,
        build={
            "omit": frozenset({"bind:control"}),
            "after": a_quiescencia_errada(logical_key="outra"),
        },
        expect=frozenset({BC.BINDING_UNPROVEN}),
    ),
    Case(
        "quiescência do binding com outra chave física",
        adopted=True,
        build={
            "omit": frozenset({"bind:control"}),
            "after": a_quiescencia_errada(physical_key="outra"),
        },
        expect=frozenset({BC.BINDING_UNPROVEN}),
    ),
    Case(
        "quiescência do binding sem os arquivos de lock",
        adopted=True,
        build={
            "omit": frozenset({"bind:control"}),
            "after": a_quiescencia_errada(lock_files_match=0),
        },
        expect=frozenset({BC.BINDING_UNPROVEN}),
    ),
    # R5 — contradições
    Case(
        "Developer sem Run (há launch)",
        build={"dev_run": False},
        expect=frozenset({BC.RUN_COMPONENT_MISSING}),
    ),
    Case(
        "Developer não declarado",
        build={"declare": False},
        expect=frozenset({BC.CONTRADICTION_DEVELOPER_UNDECLARED}),
    ),
    Case(
        "launch do Test Runner sem Run",
        build={"runner_run": False},
        expect=frozenset({BC.CONTRADICTION_RUNNER_WITHOUT_RUN}),
    ),
    Case(
        "Test Runner sem Developer declarado",
        build={"declare": False, "dev_run": False, "dev_launch": False},
        expect=frozenset({BC.CONTRADICTION_RUNNER_BEFORE_DEVELOPER, BC.RUN_COMPONENT_MISSING}),
    ),
    Case(
        "identidade do Run do Developer errada",
        build={"dev_invocation": "{c}:test_runner", "runner_run": False},
        expect=frozenset({BC.RUN_IDENTITY_MISMATCH}),
    ),
    # R7 — proprietário, selo
    Case(
        "liberador que não é o dono",
        release={"by": OTHER},
        expect=frozenset({BC.RELEASER_NOT_PARTICIPANT}),
    ),
    Case(
        "liberador que não é o adotante",
        adopted=True,
        release={"by": OWNER},
        expect=frozenset({BC.RELEASER_NOT_PARTICIPANT}),
    ),
    Case("selo ausente", release={"seal": None}, expect=frozenset({BC.SEAL_MISSING})),
    Case(
        "ack ausente (dono)",
        build={"after": a_unproven(ack=False)},
        expect=frozenset({BC.ACK_REQUIRED}),
    ),
    Case(
        "ack ausente (adotante)",
        adopted=True,
        build={"after": a_unproven(ack=False)},
        expect=frozenset({BC.ACK_REQUIRED}),
    ),
]


@pytest.mark.parametrize("case", DEFECTS, ids=[c.name for c in DEFECTS])
def test_defeito_unico_e_recusado_pelos_dois_lados(db: Db, case: Case) -> None:
    outcome = _run_case(db, case)
    assert not outcome.python_releases and not outcome.sql_released
    assert outcome.blockers & case.expect, (
        sorted(c.value for c in case.expect),
        sorted(c.value for c in outcome.blockers),
    )


def test_r1_nao_depende_so_do_check_da_supervisao(db: Db) -> None:
    """Defesa em profundidade: sem o CHECK, `start_failed` sem supervisão ainda não libera.

    `PRAGMA ignore_check_constraints` simula um CHECK ausente ou contornado. O contrato nem
    consegue representar essa evidência (`EvidenceRecord` a recusa), então a comparação é só SQL.
    """
    cycle = db.reserve()
    rid = cycle.reservation_id
    lid = db.add_launch(rid, tag="other")
    db.end(lid, "popen_raised")
    db.binding(rid, "control")
    db.to_draining(rid)
    db.run("PRAGMA ignore_check_constraints = ON")
    try:
        db.add_evidence(
            rid, "launch", "launch_start_failed_proven", launch=lid, supervision_confirmed=0
        )
    finally:
        db.run("PRAGMA ignore_check_constraints = OFF")
    assert "release_blocked:launch_unresolved" in db.refused_release(rid)
    with pytest.raises(ValueError, match="supervisão"):
        db.snapshot(rid)


def test_run_duplicado_no_ciclo(db: Db) -> None:
    def duplicate(d: Db, c: Cycle) -> None:
        d.add_run(
            c.task_id, "developer", invocation_id=f"{c.control_run_id}:test_runner", status="ok"
        )

    cycle = build_cycle(db, runner_run=False, before_close=duplicate)
    outcome = compare(db, cycle.reservation_id)
    assert BC.RUN_DUPLICATED in outcome.blockers and not outcome.sql_released


def test_active_nao_e_liberavel_e_released_e_imutavel(db: Db) -> None:
    cycle = db.reserve()
    db.binding(cycle.reservation_id, "control")
    outcome = compare(db, cycle.reservation_id)  # `active` → `released` é aresta ilegal
    assert BC.EDGE_ILLEGAL in outcome.blockers
    db.to_draining(cycle.reservation_id)
    assert compare(db, cycle.reservation_id).sql_released
    again = db.refused_release(cycle.reservation_id)
    assert "released_immutable" in again


# --------------------------------------------------------------- divergências conhecidas


def test_o_banco_e_um_piso_nao_enxerga_lease_nem_historico(db: Db) -> None:
    """Divergência INTENCIONAL: lease e atualidade do histórico são externos ao SQLite.

    O contrato recusa (HOLD) com histórico `UNKNOWN` ou lease desconhecido; o banco, que só vê
    linhas, aceitaria. Por isso `release_reservation` (I5) roda `decide_release` ANTES do UPDATE e
    o trata como a palavra final: o trigger nunca é o único guarda.
    """
    cycle = build_cycle(db)
    rid = cycle.reservation_id
    unknown = db.decision(rid, history=tc.HistoryCurrency.UNKNOWN)
    assert unknown.verdict is tc.ReleaseVerdict.HOLD
    assert BC.HISTORY_NOT_PROVEN_CURRENT in {b.code for b in unknown.blockers}
    stale = db.decision(rid, history=tc.HistoryCurrency.STALE_SUSPECTED)
    assert stale.verdict is tc.ReleaseVerdict.INCIDENT
    lease = db.decision(rid, lease=tc.LeaseStatus.UNKNOWN)
    assert lease.verdict is tc.ReleaseVerdict.HOLD
    assert db.decision(rid).verdict is tc.ReleaseVerdict.RELEASE
    assert db.release(rid) == 1  # o banco, sozinho, libera: é só o piso


def test_run_de_agente_desconhecido_no_ciclo_o_banco_recusa_e_o_snapshot_nao_se_monta(
    db: Db,
) -> None:
    cycle = db.reserve()
    db.binding(cycle.reservation_id, "control")
    db.to_draining(cycle.reservation_id)
    db.add_run(
        cycle.task_id, "auditor", invocation_id=f"{cycle.control_run_id}:developer", status="ok"
    )
    assert "release_blocked:run_role_unknown" in db.refused_release(cycle.reservation_id)
    with pytest.raises(KeyError):
        db.snapshot(cycle.reservation_id)  # o contrato não tem papel para esse Run


def _drop(db: Db, *triggers: str) -> None:
    for name in triggers:
        db.drop_trigger(name)


def test_termino_gravado_por_terceiro_e_recusado_pelos_dois_lados(db: Db) -> None:
    """R5a/C8.3: só o banco SEM o trigger de escrita consegue a linha — e a liberação recusa."""
    cycle = db.reserve()
    rid = cycle.reservation_id
    _drop(db, "execution_launch_insert_guard", "execution_launch_update_guard")
    lid = db.add_launch(
        rid,
        tag="other",
        phase="ended_confirmed",
        end_basis="refused_before_creation",
        ended_by_token=OTHER,
    )
    db.binding(rid, "control")
    db.to_draining(rid)
    outcome = compare(db, rid)
    assert not outcome.sql_released
    assert BC.CONTRADICTION_PROVENANCE in outcome.blockers
    assert lid == 1


def test_evidencia_de_outra_reserva_e_recusada_pelos_dois_lados(db: Db) -> None:
    first = db.reserve()
    other_launch_id = db.add_launch(first.reservation_id, tag="other")
    db.end(other_launch_id, "refused_before_creation")
    db.binding(first.reservation_id, "control")
    db.to_draining(first.reservation_id)
    assert db.release(first.reservation_id) == 1

    second = db.reserve()
    rid = second.reservation_id
    _drop(db, "execution_evidence_insert_guard")
    db.add_evidence(rid, "launch", "probe_present", launch=other_launch_id, launch_version=2)
    db.binding(rid, "control")
    db.to_draining(rid)
    outcome = compare(db, rid)
    assert not outcome.sql_released and BC.EVIDENCE_FOREIGN in outcome.blockers


# ----------------------------------------------------------------------------- CHECK × construtores


def _checks_db(db: Db) -> None:
    db.drop_trigger("execution_launch_insert_guard")
    db.drop_trigger("execution_evidence_insert_guard")


def test_check_do_launch_equivale_ao_launch_record_por_enumeracao(db: Db) -> None:
    rid = db.reserve().reservation_id
    _checks_db(db)
    token_pairs = [(None, None), (5, "t"), (5, None), (None, "t")]
    accepted_expected = accepted_actual = 0
    for platform, profile, phase, basis, (pid, token), ended in itertools.product(
        ("win32", "posix"),
        ("win_job_v1", "posix_pgrp_v1"),
        [p.value for p in tc.LaunchPhase],
        [None, *(b.value for b in tc.EndBasis)],
        token_pairs,
        (None, "quem"),
    ):
        try:
            tc.LaunchRecord(
                1,
                tc.PhaseTag.OTHER,
                tc.Platform(platform),
                tc.ContainmentProfile(profile),
                tc.LaunchPhase(phase),
                1,
                pid,
                token,
                None if basis is None else tc.EndBasis(basis),
                ended,
            )
            expected = True
        except (ValueError, TypeError):
            expected = False
        try:
            db.run(
                "INSERT INTO execution_launch (reservation_id, phase_tag, exe_name, platform,"
                " containment_profile, phase, version, pid, start_token, end_basis,"
                " ended_by_token, created_at)"
                " VALUES (?, 'other', 'x.exe', ?, ?, ?, 1, ?, ?, ?, ?, ?)",
                (rid, platform, profile, phase, pid, token, basis, ended, ts()),
            )
            actual = True
        except sqlite3.IntegrityError:
            actual = False
        assert actual is expected, (platform, profile, phase, basis, pid, token, ended)
        accepted_expected += expected
        accepted_actual += actual
    # 9 formas legais por plataforma: intent, created, contained, 2 confirmados, popen_raised,
    # tree_unconfirmed e ledger_write_failed com e sem pid
    assert accepted_expected == accepted_actual == 18


def test_check_da_evidencia_equivale_ao_evidence_record_por_enumeracao(db: Db) -> None:
    rid = db.reserve().reservation_id
    lid = db.add_launch(rid, tag="other")
    _checks_db(db)
    launch_fields = [(None, None), (lid, 1), (lid, None), (None, 1)]
    accepted = 0
    for scope, basis, subject, (launch, version), supervised in itertools.product(
        [s.value for s in tc.EvidenceScope],
        [b.value for b in tc.EvidenceBasis],
        (None, "control"),
        launch_fields,
        (0, 1),
    ):
        try:
            tc.EvidenceRecord(
                1,
                tc.EvidenceScope(scope),
                tc.EvidenceBasis(basis),
                OWNER,
                None if subject is None else tc.BindingSubject(subject),
                launch,
                version,
                supervision_confirmed=bool(supervised),
            )
            expected = True
        except (ValueError, TypeError):
            expected = False
        try:
            db.run(
                "INSERT INTO execution_evidence (reservation_id, launch_id, launch_version,"
                " scope, subject, basis, writer_token, supervision_confirmed, observed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rid, launch, version, scope, subject, basis, OWNER, supervised, ts()),
            )
            actual = True
        except sqlite3.IntegrityError:
            actual = False
        assert actual is expected, (scope, basis, subject, launch, version, supervised)
        accepted += expected
    assert accepted > 20


def test_check_da_reserva_equivale_aos_fatos_do_contrato_por_enumeracao(db: Db) -> None:
    """`ReservationFacts` mais o que só o banco sabe: adoção só em estado pós-teardown."""
    db.drop_trigger("execution_reservation_insert_guard")
    accepted = 0
    for state, ever, first, adopted in itertools.product(
        ("active", "draining", "teardown_pending", "unproven"),
        (0, 1),
        (None, ts(10)),
        (None, OWNER, ADOPTER),
    ):
        try:
            tc.ReservationFacts(
                tc.ReservationState(state),
                1,
                "c",
                OWNER,
                adopted,
                LOGICAL,
                PHYSICAL,
                False,
                bool(ever),
                None if first is None else datetime(2026, 1, 1, 0, 0, 10, tzinfo=UTC),
                None,
            )
            valid = True
        except (ValueError, TypeError):
            valid = False
        expected = valid and (adopted is None or state in ("teardown_pending", "unproven"))
        drain: dict[str, Any] = (
            {"drain_requested_at": ts(2), "draining_at": ts(3)} if state == "draining" else {}
        )
        db.run("BEGIN")
        try:
            try:
                db.reserve(
                    state=state,
                    ever_unproven=ever,
                    first_unproven_at=first,
                    adopted_by_token=adopted,
                    **drain,
                )
                actual = True
            except sqlite3.IntegrityError:
                actual = False
        finally:
            db.run("ROLLBACK")
        assert actual is expected, (state, ever, first, adopted)
        accepted += expected
    assert accepted > 3


# ----------------------------------------------------------------------------- fuzz com semente

LAUNCH_BASES = sorted(b.value for b in tc.BASES_BY_SCOPE[tc.EvidenceScope.LAUNCH])
PROCESS_BASES = sorted(b.value for b in tc.BASES_BY_SCOPE[tc.EvidenceScope.IN_PROCESS])
BINDING_BASES = sorted(b.value for b in tc.BASES_BY_SCOPE[tc.EvidenceScope.BINDING])


def _try(action: Callable[[], object]) -> None:
    with contextlib.suppress(sqlite3.IntegrityError):
        action()


def _launch_ids(db: Db, rid: str) -> list[int]:
    return [
        int(r[0])
        for r in db.run(
            "SELECT id FROM execution_launch WHERE reservation_id = ? ORDER BY id", (rid,)
        ).fetchall()
    ]


def _random_evidence(db: Db, rid: str, rnd: random.Random, writers: list[str]) -> None:
    scope = rnd.choice(["launch", "in_process", "binding", "binding"])
    launches = _launch_ids(db, rid)
    kw: dict[str, Any] = {
        "writer": rnd.choice(writers),
        "lock_files_match": rnd.choice([0, 1, 1]),
        "profile_ok": rnd.choice([0, 1, 1]),
        "owner_quiescence_ok": rnd.choice([0, 1, 1]),
        "root_probe_absent": rnd.choice([0, 1, 1]),
        "boot_changed": rnd.choice([0, 1]),
        "supervision_confirmed": rnd.choice([0, 1, 1]),
        "logical_key": rnd.choice([LOGICAL, LOGICAL, LOGICAL, "outra"]),
        "physical_key": rnd.choice([PHYSICAL, PHYSICAL, PHYSICAL, "outra"]),
    }
    if scope == "launch":
        if not launches:
            return
        basis = rnd.choice(LAUNCH_BASES)
        _try(lambda: db.add_evidence(rid, "launch", basis, launch=rnd.choice(launches), **kw))
    elif scope == "in_process":
        _try(lambda: db.add_evidence(rid, "in_process", rnd.choice(PROCESS_BASES), **kw))
    else:
        subject = rnd.choice(["control", "developer", "test_runner"])
        _try(
            lambda: db.add_evidence(
                rid, "binding", rnd.choice(BINDING_BASES), subject=subject, **kw
            )
        )


def _random_launch(
    db: Db,
    rid: str,
    rnd: random.Random,
    tags: tuple[str, ...] = ("developer", "developer", "test_runner", "preparation", "other"),
) -> None:
    tag = rnd.choice(tags)
    platform = rnd.choice(["win32", "win32", "posix"])
    holder: list[int] = []
    _try(lambda: holder.append(db.add_launch(rid, tag=tag, platform=platform)))
    if not holder:
        return
    lid = holder[0]
    path = rnd.choice(
        [
            "intent",
            "created",
            "contained",
            "refused",
            "dead_c",
            "dead_k",
            "popen",
            "ledger_i",
            "ledger_c",
            "unconf_c",
            "unconf_k",
        ]
    )
    steps: dict[str, list[Callable[[], None]]] = {
        "intent": [],
        "created": [lambda: db.created(lid)],
        "contained": [lambda: db.created(lid), lambda: db.contained(lid)],
        "refused": [lambda: db.end(lid, "refused_before_creation")],
        "dead_c": [lambda: db.created(lid), lambda: db.end(lid, "tree_confirmed_dead")],
        "dead_k": [
            lambda: db.created(lid),
            lambda: db.contained(lid),
            lambda: db.end(lid, "tree_confirmed_dead"),
        ],
        "popen": [lambda: db.end(lid, "popen_raised")],
        "ledger_i": [lambda: db.end(lid, "ledger_write_failed")],
        "ledger_c": [lambda: db.created(lid), lambda: db.end(lid, "ledger_write_failed")],
        "unconf_c": [lambda: db.created(lid), lambda: db.end(lid, "tree_unconfirmed")],
        "unconf_k": [
            lambda: db.created(lid),
            lambda: db.contained(lid),
            lambda: db.end(lid, "tree_unconfirmed"),
        ],
    }
    for step in steps[path]:
        _try(step)


def _heal_owner(db: Db, rid: str, rnd: random.Random) -> None:
    """O dono, bem-comportado: encerra o que abriu e observa drenagem e bindings."""
    for lid in _launch_ids(db, rid):
        phase = db.scalar("SELECT phase FROM execution_launch WHERE id = ?", (lid,))
        if phase == "intent":
            _try(partial(db.end, lid, "refused_before_creation"))
        elif phase in ("created", "contained"):
            _try(partial(db.end, lid, "tree_confirmed_dead"))
    if rnd.random() < 0.9:
        _try(lambda: db.drained(rid))
    for subject in ("control", "developer", "test_runner"):
        if rnd.random() < 0.9:
            basis = rnd.choice(["binding_released_observed", "binding_revoked_observed"])
            _try(partial(db.binding, rid, subject, basis=basis))


def fuzz_case(db: Db, rnd: random.Random) -> Outcome:
    """Um caso aleatório; metade é CAÓTICA (Runs e flags quaisquer) e metade é COERENTE.

    No modo coerente os Runs e a declaração do Developer batem entre si, para que as regras de
    launch, drenagem, binding e ack — que vêm depois das de Run — também decidam o resultado.
    """
    coherent = rnd.random() < 0.6
    db.run("BEGIN")
    try:
        cycle = db.reserve(
            with_control_run=coherent or rnd.random() > 0.04,
            control_status="ok" if coherent else rnd.choice(["ok"] * 8 + ["running"]),
        )
        rid, ctrl = cycle.reservation_id, cycle.control_run_id
        tags: tuple[str, ...]
        if coherent:
            declared = rnd.random() < 0.6
            runner = declared and rnd.random() < 0.6
            if declared:
                db.declare_developer(rid)
                db.add_run(
                    cycle.task_id, "developer", invocation_id=f"{ctrl}:developer", status="ok"
                )
            if runner:
                db.add_run(
                    cycle.task_id, "test_runner", invocation_id=f"{ctrl}:test_runner", status="ok"
                )
            tags = ("preparation", "other") + (("developer",) if declared else ())
            tags += ("test_runner",) if runner else ()
        else:
            tags = ("developer", "developer", "test_runner", "preparation", "other")
            if rnd.random() < 0.7:
                db.declare_developer(rid)
            if rnd.random() < 0.7:
                invocation = (
                    f"{ctrl}:developer" if rnd.random() < 0.94 else f"{ctrl}:x{rnd.random()}"
                )
                _try(
                    lambda: db.add_run(
                        cycle.task_id,
                        "developer",
                        invocation_id=invocation,
                        status=rnd.choice(["ok"] * 8 + ["running"]),
                    )
                )
            if rnd.random() < 0.5:
                _try(
                    lambda: db.add_run(
                        cycle.task_id,
                        "test_runner",
                        invocation_id=f"{ctrl}:test_runner",
                        status=rnd.choice(["ok"] * 8 + ["running"]),
                    )
                )
        for _ in range(rnd.choice([0, 1, 1, 2, 3])):
            _random_launch(db, rid, rnd, tags)
        for _ in range(rnd.choice([0, 0, 1, 2, 3])):
            _random_evidence(db, rid, rnd, [OWNER, OWNER, OWNER, OTHER])
        if rnd.random() < 0.7:
            _heal_owner(db, rid, rnd)

        adopted = rnd.random() < 0.4
        close = rnd.choice(["draining", "draining", "teardown_pending", "unproven", "active"])
        if adopted:
            _try(lambda: db.adopt(rid))
        elif close == "draining":
            _try(lambda: db.to_draining(rid))
        elif close == "teardown_pending":
            _try(lambda: db.to_teardown_pending(rid))
        elif close == "unproven":
            _try(lambda: db.to_unproven(rid))
            if rnd.random() < 0.6:
                _try(lambda: db.ack(rid, at=rnd.choice([5, 20, 20])))
        if adopted:
            if rnd.random() < 0.8:
                _try(lambda: db.quiescence(rid, "in_process"))
            for subject in ("control", "developer", "test_runner"):
                if rnd.random() < 0.8:
                    _try(partial(db.quiescence, rid, "binding", subject))
            for _ in range(rnd.choice([0, 0, 1, 2])):
                _random_evidence(db, rid, rnd, [ADOPTER, ADOPTER, OWNER])
        if rnd.random() < 0.25:
            _random_evidence(db, rid, rnd, [OWNER, ADOPTER])
        by = None if rnd.random() < 0.9 else OTHER
        seal = SEAL if rnd.random() < 0.95 else None
        return compare(db, rid, by=by, seal=seal)
    finally:
        db.run("ROLLBACK")


@pytest.mark.parametrize("seed", [20260415, 20260416, 20260417])
def test_fuzz_com_semente_o_contrato_e_o_banco_nunca_divergem(db: Db, seed: int) -> None:
    rnd = random.Random(seed)  # noqa: S311 — determinístico, não é criptografia
    released = refused = 0
    reasons: set[str] = set()
    for _ in range(400):
        outcome = fuzz_case(db, rnd)
        if outcome.sql_released:
            released += 1
        else:
            refused += 1
            assert outcome.message is not None
            reasons.add(outcome.message)
    # o gerador tem de exercitar os dois lados, senão a concordância não significa nada
    assert released >= 40, released
    assert refused >= 200, refused
    assert len(reasons) >= 10, sorted(reasons)


def test_o_fuzz_nao_deixa_residuo_no_banco(db: Db) -> None:
    fuzz_case(db, random.Random(1))  # noqa: S311
    assert db.scalar("SELECT COUNT(*) FROM execution_reservation") == 0
    assert db.scalar("SELECT COUNT(*) FROM execution_launch") == 0
    assert db.scalar("SELECT COUNT(*) FROM execution_evidence") == 0
