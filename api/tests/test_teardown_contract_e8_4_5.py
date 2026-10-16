"""E8.4.5 / I1 — contrato puro de reserva, teardown e liberação. Sem banco, SO ou processo."""

from __future__ import annotations

import dataclasses
import itertools
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.db.enums import FailureReason, RunStatus, TaskStatus
from app.orchestrator import teardown_contract as tc
from app.orchestrator.execution_contract import (
    ExecutionClassification,
    ExecutionCode,
    TaskResolution,
)
from app.orchestrator.teardown_contract import (
    BindingSubject as BS,
)
from app.orchestrator.teardown_contract import (
    BlockerCode as BC,
)
from app.orchestrator.teardown_contract import (
    EndBasis,
    HistoryCurrency,
    LeaseStatus,
    PhaseTag,
    Platform,
    WriteRefusal,
)
from app.orchestrator.teardown_contract import (
    EvidenceBasis as EB,
)
from app.orchestrator.teardown_contract import (
    EvidenceScope as ES,
)
from app.orchestrator.teardown_contract import (
    LaunchPhase as LP,
)
from app.orchestrator.teardown_contract import (
    ReservationState as RS,
)
from app.orchestrator.teardown_contract import (
    TeardownVerdict as TV,
)


def _replace(obj: Any, **changes: Any) -> Any:
    """`dataclasses.replace` sem tipagem: os testes de entrada inválida passam valores errados."""
    return dataclasses.replace(obj, **changes)


OWNER, ADOPTER, OTHER = "owner-tok", "adopter-tok", "other-tok"
T0 = datetime(2026, 10, 14, tzinfo=UTC)

# ----------------------------------------------------------------------------- construtores


def launch(
    id_: int = 1,
    *,
    phase: LP = LP.ENDED_CONFIRMED,
    basis: EndBasis | None = None,
    by: str | None = None,
    platform: Platform = Platform.WIN32,
    tag: PhaseTag = PhaseTag.PREPARATION,
    version: int = 1,
) -> tc.LaunchRecord:
    profile = (
        tc.ContainmentProfile.WIN_JOB_V1
        if platform is Platform.WIN32
        else tc.ContainmentProfile.POSIX_PGRP_V1
    )
    if phase is LP.ENDED_CONFIRMED:
        basis = basis or EndBasis.TREE_CONFIRMED_DEAD
        by = by or OWNER
    if phase is LP.ENDED_UNCONFIRMED:
        basis = basis or EndBasis.TREE_UNCONFIRMED
        by = by or OWNER
    has_pid = phase in (LP.CREATED, LP.CONTAINED) or (
        phase in tc.TERMINAL_PHASES
        and basis not in (EndBasis.REFUSED_BEFORE_CREATION, EndBasis.POPEN_RAISED)
    )
    return tc.LaunchRecord(
        id=id_,
        phase_tag=tag,
        platform=platform,
        containment_profile=profile,
        phase=phase,
        version=version,
        pid=10 if has_pid else None,
        start_token="t" if has_pid else None,
        end_basis=basis,
        ended_by_token=by,
    )


_seq = itertools.count(1)


def ev(
    scope: ES,
    basis: EB,
    writer: str = OWNER,
    *,
    subject: BS | None = None,
    lid: int | None = None,
    lver: int | None = None,
    seq: int | None = None,
    match: bool = False,
    prof: bool = False,
    oq: bool = False,
    rp: bool = False,
    boot: bool = False,
    sup: bool = False,
    lk: str = "L",
    pk: str = "P",
) -> tc.EvidenceRecord:
    return tc.EvidenceRecord(
        seq=seq if seq is not None else next(_seq),
        scope=scope,
        basis=basis,
        writer_token=writer,
        subject=subject,
        launch_id=lid,
        launch_version=lver,
        logical_key=lk,
        physical_key=pk,
        lock_files_match=match,
        profile_ok=prof,
        owner_quiescence_ok=oq,
        root_probe_absent=rp,
        boot_changed=boot,
        supervision_confirmed=sup,
    )


def quiescence(scope: ES, subject: BS | None = None, **kw: object) -> tc.EvidenceRecord:
    return ev(scope, EB.COOPERATIVE_OWNER_QUIESCENCE, ADOPTER, subject=subject, match=True, **kw)  # type: ignore[arg-type]


def facts(
    state: RS = RS.DRAINING,
    *,
    adopted: bool = False,
    dev: bool = True,
    ever: bool = False,
    ack: datetime | None = None,
    version: int = 3,
) -> tc.ReservationFacts:
    return tc.ReservationFacts(
        state=state,
        version=version,
        control_run_id="C",
        owner_token=OWNER,
        adopted_by_token=ADOPTER if adopted else None,
        logical_key="L",
        physical_key="P",
        developer_declared=dev,
        ever_unproven=ever or state is RS.UNPROVEN,
        first_unproven_at=T0 if (ever or state is RS.UNPROVEN) else None,
        ack_at=ack,
    )


def runs(status: RunStatus = RunStatus.OK, *, runner: bool = True) -> tuple[tc.RunFact, ...]:
    out = [
        tc.RunFact(tc.RunRole.CONTROL, status, "C", "client-key"),
        tc.RunFact(tc.RunRole.DEVELOPER, status, "D", "C:developer"),
    ]
    if runner:
        out.append(tc.RunFact(tc.RunRole.TEST_RUNNER, status, "N", "C:test_runner"))
    return tuple(out)


def live_evidence() -> list[tc.EvidenceRecord]:
    out = [ev(ES.IN_PROCESS, EB.EXECUTOR_DRAINED)]
    out += [
        ev(ES.BINDING, EB.BINDING_RELEASED_OBSERVED, subject=s)
        for s in (BS.CONTROL, BS.DEVELOPER, BS.TEST_RUNNER)
    ]
    return out


def live_snapshot(**over: object) -> tc.ReleaseSnapshot:
    base: dict[str, object] = {
        "reservation": facts(),
        "launches": (
            launch(1),
            launch(
                2,
                tag=PhaseTag.DEVELOPER,
                phase=LP.ENDED_CONFIRMED,
                basis=EndBasis.REFUSED_BEFORE_CREATION,
            ),
            launch(3, tag=PhaseTag.TEST_RUNNER),
        ),
        "evidence": tuple(live_evidence()),
        "runs": runs(),
        "lease": LeaseStatus.CONSISTENT,
        "history": HistoryCurrency.PROVEN_CURRENT,
    }
    base.update(over)
    return tc.ReleaseSnapshot(**base)  # type: ignore[arg-type]


def adopted_snapshot(**over: object) -> tc.ReleaseSnapshot:
    evid = [quiescence(ES.IN_PROCESS)]
    evid += [quiescence(ES.BINDING, s) for s in (BS.CONTROL, BS.DEVELOPER, BS.TEST_RUNNER)]
    base: dict[str, object] = {
        "reservation": facts(RS.UNPROVEN, adopted=True, ack=T0 + timedelta(hours=1)),
        "launches": (launch(1, phase=LP.ENDED_CONFIRMED, basis=EndBasis.REFUSED_BEFORE_CREATION),),
        "evidence": tuple(evid),
        "runs": runs(RunStatus.INTERRUPTED),
    }
    base.update(over)
    return live_snapshot(**base)


def request(snap: tc.ReleaseSnapshot, **over: object) -> tc.ReleaseRequest:
    r = snap.reservation
    base: dict[str, object] = {
        "expected_state": r.state,
        "expected_version": r.version,
        "releaser_token": r.owner_token if r.adopted_by_token is None else r.adopted_by_token,
        "seal_present": True,
    }
    base.update(over)
    return tc.ReleaseRequest(**base)  # type: ignore[arg-type]


def decide(snap: tc.ReleaseSnapshot, **over: object) -> tc.ReleaseDecision:
    return tc.decide_release(snap, request(snap, **over))


def codes(d: tc.ReleaseDecision) -> set[BC]:
    return {b.code for b in d.blockers}


# ----------------------------------------------------------------------------- 1. estados


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (RS.ACTIVE, RS.DRAINING),
        (RS.ACTIVE, RS.TEARDOWN_PENDING),
        (RS.DRAINING, RS.TEARDOWN_PENDING),
        (RS.DRAINING, RS.RELEASED),
        (RS.TEARDOWN_PENDING, RS.UNPROVEN),
        (RS.TEARDOWN_PENDING, RS.RELEASED),
        (RS.UNPROVEN, RS.TEARDOWN_PENDING),
        (RS.UNPROVEN, RS.RELEASED),
    ],
)
def test_arestas_legais_da_reserva(old: RS, new: RS) -> None:
    assert tc.reservation_edge_allowed(old, new)


def test_todas_as_outras_arestas_sao_ilegais_e_released_e_imutavel() -> None:
    legal = {
        (RS.ACTIVE, RS.DRAINING),
        (RS.ACTIVE, RS.TEARDOWN_PENDING),
        (RS.DRAINING, RS.TEARDOWN_PENDING),
        (RS.DRAINING, RS.RELEASED),
        (RS.TEARDOWN_PENDING, RS.UNPROVEN),
        (RS.TEARDOWN_PENDING, RS.RELEASED),
        (RS.UNPROVEN, RS.TEARDOWN_PENDING),
        (RS.UNPROVEN, RS.RELEASED),
    }
    for old, new in itertools.product(RS, RS):
        got = tc.reservation_edge_allowed(old, new)
        if old is RS.RELEASED:
            assert not got, (old, new)  # nem auto-aresta
        elif old is new:
            assert got
        else:
            assert got == ((old, new) in legal), (old, new)
    assert not tc.reservation_edge_allowed(RS.ACTIVE, RS.RELEASED)  # nunca direto de active


def test_adocao_so_na_mesma_instrucao_que_leva_a_teardown_pending() -> None:
    assert tc.adoption_edge_allowed(RS.ACTIVE, RS.TEARDOWN_PENDING)
    assert tc.adoption_edge_allowed(RS.DRAINING, RS.TEARDOWN_PENDING)
    for new in (RS.ACTIVE, RS.DRAINING, RS.UNPROVEN, RS.RELEASED):
        assert not tc.adoption_edge_allowed(RS.ACTIVE, new)
    assert not tc.adoption_edge_allowed(RS.RELEASED, RS.TEARDOWN_PENDING)


def test_reserva_unproven_exige_ever_unproven_e_first_unproven_at() -> None:
    with pytest.raises(ValueError, match="pegajoso"):
        tc.ReservationFacts(RS.UNPROVEN, 1, "C", OWNER, None, "L", "P", True, False, None, None)
    with pytest.raises(ValueError, match="first_unproven_at"):
        tc.ReservationFacts(RS.DRAINING, 1, "C", OWNER, None, "L", "P", True, True, None, None)


# ----------------------------------------------------------------------------- 2. lançamento


LEGAL_TRIPLES = {
    (LP.INTENT, LP.CREATED, None),
    (LP.CREATED, LP.CONTAINED, None),
    (LP.INTENT, LP.ENDED_CONFIRMED, EndBasis.REFUSED_BEFORE_CREATION),
    (LP.CREATED, LP.ENDED_CONFIRMED, EndBasis.TREE_CONFIRMED_DEAD),
    (LP.CONTAINED, LP.ENDED_CONFIRMED, EndBasis.TREE_CONFIRMED_DEAD),
    (LP.INTENT, LP.ENDED_UNCONFIRMED, EndBasis.POPEN_RAISED),
    (LP.INTENT, LP.ENDED_UNCONFIRMED, EndBasis.LEDGER_WRITE_FAILED),
    (LP.CREATED, LP.ENDED_UNCONFIRMED, EndBasis.TREE_UNCONFIRMED),
    (LP.CREATED, LP.ENDED_UNCONFIRMED, EndBasis.LEDGER_WRITE_FAILED),
    (LP.CONTAINED, LP.ENDED_UNCONFIRMED, EndBasis.TREE_UNCONFIRMED),
    (LP.CONTAINED, LP.ENDED_UNCONFIRMED, EndBasis.LEDGER_WRITE_FAILED),
}


@pytest.mark.parametrize("old", list(LP))
@pytest.mark.parametrize("new", list(LP))
@pytest.mark.parametrize("basis", [None, *EndBasis])
def test_matriz_completa_origem_destino_end_basis(old: LP, new: LP, basis: EndBasis | None) -> None:
    """Oráculo independente: só os 11 triplos legais passam; as 5x5x6 combinações restantes não."""
    assert tc.launch_edge_allowed(old, new, basis) == ((old, new, basis) in LEGAL_TRIPLES)


def test_intent_para_ended_confirmed_so_com_recusa_antes_da_criacao() -> None:
    assert tc.launch_edge_allowed(LP.INTENT, LP.ENDED_CONFIRMED, EndBasis.REFUSED_BEFORE_CREATION)
    for basis in (EndBasis.TREE_CONFIRMED_DEAD, None):
        assert not tc.launch_edge_allowed(LP.INTENT, LP.ENDED_CONFIRMED, basis)


@pytest.mark.parametrize("old", [LP.CREATED, LP.CONTAINED])
@pytest.mark.parametrize("new", [LP.ENDED_CONFIRMED, LP.ENDED_UNCONFIRMED])
def test_recusa_antes_da_criacao_nao_vale_depois_de_created(old: LP, new: LP) -> None:
    assert not tc.launch_edge_allowed(old, new, EndBasis.REFUSED_BEFORE_CREATION)


def test_forma_do_launch_recusado_sem_pid_e_o_contrario_e_invalido() -> None:
    ok = launch(phase=LP.ENDED_CONFIRMED, basis=EndBasis.REFUSED_BEFORE_CREATION)
    assert ok.pid is None and ok.start_token is None
    with pytest.raises(ValueError):
        _replace(ok, pid=5, start_token="t")  # recusa com pid
    dead = launch(phase=LP.ENDED_CONFIRMED, basis=EndBasis.TREE_CONFIRMED_DEAD)
    with pytest.raises(ValueError):
        _replace(dead, pid=None, start_token=None)  # árvore morta sem pid
    with pytest.raises(ValueError):
        _replace(ok, ended_by_token=None)
    with pytest.raises(ValueError):
        launch(phase=LP.INTENT, by="x")
    with pytest.raises(ValueError):
        _replace(launch(phase=LP.CREATED), pid=None, start_token=None)


def test_popen_raised_nao_tem_pid_e_nao_e_ended_confirmed() -> None:
    row = launch(phase=LP.ENDED_UNCONFIRMED, basis=EndBasis.POPEN_RAISED)
    assert row.pid is None
    with pytest.raises(ValueError):
        _replace(row, pid=1, start_token="t")
    with pytest.raises(ValueError):
        _replace(row, phase=LP.ENDED_CONFIRMED)


def test_perfil_de_contencao_precisa_casar_com_a_plataforma() -> None:
    row = launch(platform=Platform.POSIX)
    with pytest.raises(ValueError):
        _replace(row, containment_profile=tc.ContainmentProfile.WIN_JOB_V1)


def test_ticket_so_recusa_antes_de_cruzar() -> None:
    t = tc.LaunchTicket()
    assert t.refuse() is EndBasis.REFUSED_BEFORE_CREATION
    assert t.state is tc.TicketState.REFUSED
    with pytest.raises(RuntimeError):
        t.refuse()  # repetida
    with pytest.raises(RuntimeError):
        t.cross()  # depois de recusar
    crossed = tc.LaunchTicket()
    crossed.cross()
    with pytest.raises(RuntimeError):
        crossed.refuse()  # recusa depois do cruzamento seria mentira possível
    assert crossed.state is tc.TicketState.CROSSING


# ----------------------------------------------------------------------------- 3. proveniência


def w(**over: object) -> WriteRefusal | None:
    base: dict[str, object] = {
        "reservation_state": RS.ACTIVE,
        "owner_token": OWNER,
        "adopted_by_token": None,
        "writer_token": OWNER,
        "old_phase": LP.INTENT,
        "new_phase": LP.ENDED_CONFIRMED,
        "end_basis": EndBasis.REFUSED_BEFORE_CREATION,
    }
    base.update(over)
    return tc.launch_write_refusal(**base)  # type: ignore[arg-type]


def test_dono_original_pode_registrar_a_recusa_antes_da_adocao() -> None:
    assert w() is None
    assert w(reservation_state=RS.DRAINING) is None


def test_recovery_nao_pode_fabricar_a_recusa() -> None:
    # adotante: reserva já adotada (state teardown_pending) e escritor diferente do dono
    assert (
        w(reservation_state=RS.TEARDOWN_PENDING, adopted_by_token=ADOPTER, writer_token=ADOPTER)
        is WriteRefusal.RESERVATION_ADOPTED
    )
    # mesmo imitando o token do dono, a reserva adotada recusa
    assert w(reservation_state=RS.TEARDOWN_PENDING, adopted_by_token=ADOPTER) is (
        WriteRefusal.RESERVATION_ADOPTED
    )
    # terceiro com a reserva ativa
    assert w(writer_token=OTHER) is WriteRefusal.WRITER_NOT_OWNER
    # estado ativo mas adotado (violação de C8.5): ainda recusa
    assert w(adopted_by_token=ADOPTER) is WriteRefusal.RESERVATION_ADOPTED


def test_terminal_e_imutavel_e_recusa_depois_de_created_e_ilegal() -> None:
    assert (
        w(
            old_phase=LP.ENDED_CONFIRMED,
            new_phase=LP.ENDED_UNCONFIRMED,
            end_basis=EndBasis.TREE_UNCONFIRMED,
        )
        is WriteRefusal.TERMINAL_IMMUTABLE
    )
    assert w(old_phase=LP.CREATED) is WriteRefusal.EDGE_ILLEGAL
    assert w(reservation_state=RS.TEARDOWN_PENDING) is WriteRefusal.RESERVATION_NOT_WRITABLE


def test_novo_intent_exige_reserva_ativa_nao_adotada_sem_drenagem_pedida() -> None:
    f = tc.launch_insert_refusal
    assert f(reservation_state=RS.ACTIVE, adopted_by_token=None, drain_requested=False) is None
    assert f(reservation_state=RS.ACTIVE, adopted_by_token=ADOPTER, drain_requested=False) is (
        WriteRefusal.RESERVATION_ADOPTED  # lacuna achada no S2
    )
    assert f(reservation_state=RS.DRAINING, adopted_by_token=None, drain_requested=False) is (
        WriteRefusal.RESERVATION_NOT_WRITABLE
    )
    assert f(reservation_state=RS.ACTIVE, adopted_by_token=None, drain_requested=True) is (
        WriteRefusal.DRAIN_REQUESTED
    )


def e(**over: object) -> WriteRefusal | None:
    base: dict[str, object] = {
        "basis": EB.EXECUTOR_DRAINED,
        "writer_token": OWNER,
        "reservation_state": RS.DRAINING,
        "owner_token": OWNER,
        "adopted_by_token": None,
    }
    base.update(over)
    return tc.evidence_write_refusal(**base)  # type: ignore[arg-type]


def test_evidencia_ao_vivo_so_antes_da_adocao_e_quiescencia_so_pelo_adotante() -> None:
    assert e() is None
    assert e(adopted_by_token=ADOPTER) is WriteRefusal.LIVE_BASIS_AFTER_ADOPTION  # fabricada
    for live in tc.LIVE_OBSERVED_BASES:
        assert e(basis=live, adopted_by_token=ADOPTER) is WriteRefusal.LIVE_BASIS_AFTER_ADOPTION
    q = EB.COOPERATIVE_OWNER_QUIESCENCE
    assert e(basis=q, writer_token=ADOPTER, adopted_by_token=ADOPTER) is None
    assert e(basis=q) is WriteRefusal.QUIESCENCE_NEEDS_ADOPTER  # dono sem adoção
    assert e(basis=q, adopted_by_token=ADOPTER) is WriteRefusal.QUIESCENCE_NEEDS_ADOPTER
    assert e(writer_token=OTHER) is WriteRefusal.WRITER_NOT_PARTICIPANT
    assert e(reservation_state=RS.RELEASED) is WriteRefusal.RESERVATION_RELEASED


def test_registro_de_evidencia_valida_escopo_e_campos() -> None:
    with pytest.raises(ValueError, match="escopo"):
        ev(ES.BINDING, EB.EXECUTOR_DRAINED, subject=BS.CONTROL)
    with pytest.raises(ValueError, match="launch_id"):
        ev(ES.LAUNCH, EB.IDENTITY_PROBE_ABSENT)
    with pytest.raises(ValueError, match="subject"):
        ev(ES.BINDING, EB.BINDING_RELEASED_OBSERVED)
    with pytest.raises(ValueError, match="writer_token"):
        ev(ES.IN_PROCESS, EB.EXECUTOR_DRAINED, "")


# ----------------------------------------------------------------------------- 4. release feliz


def test_caminho_vivo_completo_libera() -> None:
    snap = live_snapshot()
    d = decide(snap)
    assert d.verdict is tc.ReleaseVerdict.RELEASE and d.blockers == ()
    assert tc.classify_resources(snap).verdict is TV.PROVEN_CLOSED


def test_recusa_legitima_do_dono_preserva_validade_apos_adocao() -> None:
    """O dono grava `refused_before_creation`; crash; o recovery adota. O terminal imutável e a
    evidência de quiescência do adotante bastam: nada é invalidado retroativamente."""
    snap = adopted_snapshot()
    assert snap.launches[0].end_basis is EndBasis.REFUSED_BEFORE_CREATION
    assert snap.reservation.adopted_by_token == ADOPTER
    assert decide(snap).verdict is tc.ReleaseVerdict.RELEASE


def test_evidencia_ao_vivo_gravada_pelo_dono_continua_valida_depois_da_adocao() -> None:
    snap = adopted_snapshot(
        evidence=tuple(live_evidence()),  # executor_drained + bindings do DONO, antes da adoção
    )
    assert decide(snap).verdict is tc.ReleaseVerdict.RELEASE


# ----------------------------------------------------------------------------- 5. ausência ≠ prova


def test_ausencia_total_de_evidencia_nunca_libera() -> None:
    snap = live_snapshot(evidence=(), launches=())
    d = decide(snap)
    assert d.verdict is tc.ReleaseVerdict.HOLD
    assert {BC.DRAIN_UNPROVEN, BC.BINDING_UNPROVEN} <= codes(d)
    assert tc.classify_resources(snap).verdict is TV.UNPROVEN


def test_sem_launches_no_banco_nao_e_prova_de_que_nada_foi_lancado() -> None:
    """C7.7: a ausência de launches não vira RELEASE. Sem prova de histórico atual, é HOLD."""
    for hist in (HistoryCurrency.UNKNOWN, HistoryCurrency.STALE_SUSPECTED):
        snap = live_snapshot(
            launches=(),
            history=hist,
            runs=runs(runner=False),
            evidence=tuple(e_ for e_ in live_evidence() if e_.subject is not BS.TEST_RUNNER),
        )
        d = decide(snap)
        assert d.verdict is not tc.ReleaseVerdict.RELEASE
    ok = live_snapshot(
        launches=(),
        runs=runs(runner=False),
        evidence=tuple(e_ for e_ in live_evidence() if e_.subject is not BS.TEST_RUNNER),
    )
    assert decide(ok).verdict is tc.ReleaseVerdict.RELEASE  # só com histórico comprovado atual


def test_contraexemplo_c77_backup_restaurado_perde_o_launch() -> None:
    """Lease armado + reserva persistida + processo externo com intenção registrada + backup
    anterior restaurado no mesmo inode + crash: o banco só tem a reserva. Sem prova de histórico
    atual, o contrato nunca libera por ausência de launches."""
    snap = adopted_snapshot(
        launches=(),
        history=HistoryCurrency.UNKNOWN,
        lease=LeaseStatus.CONSISTENT,  # mesma reserva e mesma identidade física
    )
    d = decide(snap)
    assert d.verdict is tc.ReleaseVerdict.HOLD
    assert BC.HISTORY_NOT_PROVEN_CURRENT in codes(d)
    suspected = decide(_replace(snap, history=HistoryCurrency.STALE_SUSPECTED))
    assert suspected.verdict is tc.ReleaseVerdict.INCIDENT
    assert BC.HISTORY_STALE_SUSPECTED in codes(suspected)


def test_history_e_lease_nao_tem_padrao() -> None:
    fields = {f.name: f for f in dataclasses.fields(tc.ReleaseSnapshot)}
    assert fields["history"].default is dataclasses.MISSING
    assert fields["lease"].default is dataclasses.MISSING


def test_lease_desconhecido_ou_incidente_bloqueia() -> None:
    unknown = decide(live_snapshot(lease=LeaseStatus.UNKNOWN))
    assert unknown.verdict is tc.ReleaseVerdict.HOLD and BC.LEASE_UNKNOWN in codes(unknown)
    incident = decide(live_snapshot(lease=LeaseStatus.INCIDENT))
    assert incident.verdict is tc.ReleaseVerdict.INCIDENT and BC.LEASE_INCIDENT in codes(incident)


# ----------------------------------------------------------------------------- 6. bloqueadores


def test_launch_em_intent_sem_terminal_bloqueia_com_unproven() -> None:
    snap = live_snapshot(launches=(*live_snapshot().launches, launch(9, phase=LP.INTENT)))
    d = decide(snap)
    assert d.verdict is tc.ReleaseVerdict.HOLD
    assert BC.LAUNCH_INTENT_WITHOUT_TERMINAL in codes(d)
    assert tc.classify_resources(snap).verdict is TV.UNPROVEN


def test_intent_nunca_e_resolvido_por_evidencia_de_sonda_ou_recusa_externa() -> None:
    row = launch(9, phase=LP.INTENT)
    for basis in (
        EB.IDENTITY_PROBE_ABSENT,
        EB.JOB_GUARANTEE_VERIFIED,
        EB.LAUNCH_START_FAILED_PROVEN,
    ):
        snap = live_snapshot(
            launches=(row,),
            evidence=(
                *live_evidence(),
                ev(
                    ES.LAUNCH,
                    basis,
                    OWNER,
                    lid=9,
                    lver=1,
                    match=True,
                    prof=True,
                    oq=True,
                    rp=True,
                    sup=True,
                ),
            ),
            runs=runs(runner=False),
        )
        snap = _replace(
            snap, evidence=tuple(x for x in snap.evidence if x.subject is not BS.TEST_RUNNER)
        )
        assert decide(snap).verdict is not tc.ReleaseVerdict.RELEASE, basis


def test_run_aberto_bloqueia_a_conclusao_segura() -> None:
    for role in tc.RunRole:
        rs = tuple(
            _replace(r, status=RunStatus.RUNNING if r.role is role else RunStatus.OK)
            for r in runs()
        )
        d = decide(live_snapshot(runs=rs))
        assert d.verdict is tc.ReleaseVerdict.HOLD
        assert tc.Blocker(BC.RUN_OPEN, role.value) in d.blockers


def test_executor_nao_drenado_bloqueia_release() -> None:
    no_drain = tuple(x for x in live_evidence() if x.scope is not ES.IN_PROCESS)
    d = decide(live_snapshot(evidence=no_drain))
    assert BC.DRAIN_UNPROVEN in codes(d) and d.verdict is tc.ReleaseVerdict.HOLD
    for neg, verdict in (
        (EB.DRAIN_TIMEOUT, TV.STILL_ACTIVE),
        (EB.DRAIN_REENTRANT, TV.TECHNICAL_FAILURE),
        (EB.QUIESCENCE_UNPROVEN, TV.TECHNICAL_FAILURE),
    ):
        snap = live_snapshot(evidence=(*live_evidence(), ev(ES.IN_PROCESS, neg)))
        assert BC.DRAIN_UNPROVEN in codes(decide(snap)), neg
        assert tc.classify_resources(snap).verdict is verdict, neg


def control_only_runs(status: RunStatus = RunStatus.OK) -> tuple[tc.RunFact, ...]:
    return (tc.RunFact(tc.RunRole.CONTROL, status, "C", "client-key"),)


def test_componente_comprovadamente_nao_iniciado_nao_exige_prova_dele() -> None:
    """Caso legítimo: nada do Developer/runner existe (sem Run, sem launch, sem declaração)."""
    evid = tuple(x for x in live_evidence() if x.scope is ES.BINDING and x.subject is BS.CONTROL)
    snap = live_snapshot(
        reservation=facts(dev=False),
        evidence=evid,
        launches=(launch(1),),
        runs=control_only_runs(),
    )
    assert not snap.developer_present and not snap.runner_present
    assert decide(snap).verdict is tc.ReleaseVerdict.RELEASE


@pytest.mark.parametrize("subject", list(BS))
def test_binding_desconhecido_bloqueia_release(subject: BS) -> None:
    evid = tuple(x for x in live_evidence() if x.subject is not subject)
    d = decide(live_snapshot(evidence=evid))
    assert tc.Blocker(BC.BINDING_UNPROVEN, subject.value) in d.blockers
    assert d.verdict is tc.ReleaseVerdict.HOLD


def test_binding_unproven_posterior_bloqueia_mas_anterior_nao() -> None:
    positives = live_evidence()
    later = ev(ES.BINDING, EB.BINDING_UNPROVEN, subject=BS.DEVELOPER)  # seq maior que as positivas
    assert (
        tc.Blocker(BC.BINDING_UNPROVEN, "developer")
        in decide(live_snapshot(evidence=(*positives, later))).blockers
    )
    earlier = ev(ES.BINDING, EB.BINDING_UNPROVEN, subject=BS.DEVELOPER, seq=0)
    assert decide(live_snapshot(evidence=(earlier, *live_evidence()))).verdict is (
        tc.ReleaseVerdict.RELEASE
    )


def test_binding_de_controle_nunca_criado_so_com_quiescencia_do_adotante() -> None:
    # recovery: sem binding de controle registrado; a quiescência cooperativa cobre o assunto
    snap = adopted_snapshot()
    assert decide(snap).verdict is tc.ReleaseVerdict.RELEASE
    # quiescência com chaves divergentes (continuidade não comprovada) não cobre
    bad = tuple(
        _replace(x, physical_key="Q") if x.subject is BS.CONTROL else x for x in snap.evidence
    )
    d = decide(_replace(snap, evidence=bad))
    assert tc.Blocker(BC.BINDING_UNPROVEN, "control") in d.blockers
    nomatch = tuple(
        _replace(x, lock_files_match=False) if x.subject is BS.CONTROL else x for x in snap.evidence
    )
    assert BC.BINDING_UNPROVEN in codes(decide(_replace(snap, evidence=nomatch)))


def test_quiescencia_escrita_pelo_dono_ou_terceiro_nao_vale() -> None:
    for writer in (OWNER, OTHER):
        bad = tuple(
            _replace(x, writer_token=writer) if x.scope is ES.IN_PROCESS else x
            for x in adopted_snapshot().evidence
        )
        assert BC.DRAIN_UNPROVEN in codes(decide(_replace(adopted_snapshot(), evidence=bad)))


# ----------------------------------------------------------------------------- 7. contradição


def test_contradicao_de_proveniencia_da_recusa_mesmo_com_launch_ended_confirmed() -> None:
    for by in (OTHER, ADOPTER):
        row = launch(1, phase=LP.ENDED_CONFIRMED, basis=EndBasis.REFUSED_BEFORE_CREATION, by=by)
        d = decide(adopted_snapshot(launches=(row,)))
        assert d.verdict is tc.ReleaseVerdict.INCIDENT, by
        assert BC.CONTRADICTION_PROVENANCE in codes(d)


def test_ended_confirmed_com_negativa_posterior_ou_simultanea_e_contradicao() -> None:
    base = live_snapshot()
    neg = ev(ES.LAUNCH, EB.PROBE_PRESENT, lid=1, lver=1)
    d = decide(_replace(base, evidence=(*base.evidence, neg)))
    assert BC.EVIDENCE_CONTRADICTED in codes(d)
    assert tc.classify_resources(_replace(base, evidence=(*base.evidence, neg))).verdict is (
        TV.STILL_ACTIVE
    )
    unconfirmed = ev(ES.LAUNCH, EB.LAUNCH_UNCONFIRMED_OBSERVED, lid=1, lver=1)
    t = tc.classify_resources(_replace(base, evidence=(*base.evidence, unconfirmed)))
    assert t.verdict is TV.TECHNICAL_FAILURE
    # negativa gravada para versão ANTERIOR do launch (superada) não contradiz
    old = ev(ES.LAUNCH, EB.PROBE_PRESENT, lid=1, lver=1)
    newer = _replace(base.launches[0], version=2)
    snap = _replace(base, launches=(newer, *base.launches[1:]), evidence=(old, *base.evidence))
    assert decide(snap).verdict is tc.ReleaseVerdict.RELEASE


def test_launch_do_runner_sem_run_ou_antes_do_developer_e_incidente() -> None:
    row = launch(3, tag=PhaseTag.TEST_RUNNER)
    snap = live_snapshot(
        runs=runs(runner=False),
        evidence=tuple(x for x in live_evidence() if x.subject is not BS.TEST_RUNNER),
        launches=(row,),
    )
    d = decide(snap)
    assert d.verdict is tc.ReleaseVerdict.INCIDENT
    assert BC.CONTRADICTION_RUNNER_WITHOUT_RUN in codes(d)
    snap2 = live_snapshot(reservation=facts(dev=False), launches=(row,))
    assert BC.CONTRADICTION_RUNNER_BEFORE_DEVELOPER in codes(decide(snap2))


def test_evidencia_de_launch_inexistente_e_incidente() -> None:
    stray = ev(ES.LAUNCH, EB.IDENTITY_PROBE_ABSENT, lid=99, lver=1)
    d = decide(live_snapshot(evidence=(*live_evidence(), stray)))
    assert d.verdict is tc.ReleaseVerdict.INCIDENT and BC.EVIDENCE_FOREIGN in codes(d)


# --- 8. evidência defasada e launches por fase


def _snap_with(
    row: tc.LaunchRecord, *extra: tc.EvidenceRecord, adopted: bool = True
) -> tc.ReleaseSnapshot:
    base = adopted_snapshot() if adopted else live_snapshot()
    out: tc.ReleaseSnapshot = _replace(
        base,
        launches=(row,),
        evidence=(*base.evidence, *extra),
    )
    return out


def test_evidencia_defasada_por_versao_do_launch_bloqueia() -> None:
    row = launch(5, phase=LP.CREATED, version=2)
    stale = ev(ES.LAUNCH, EB.IDENTITY_PROBE_ABSENT, ADOPTER, lid=5, lver=1)
    d = decide(_snap_with(row, stale))
    assert BC.EVIDENCE_STALE in codes(d) and d.verdict is tc.ReleaseVerdict.HOLD
    fresh = ev(ES.LAUNCH, EB.IDENTITY_PROBE_ABSENT, ADOPTER, lid=5, lver=2)
    assert decide(_snap_with(row, fresh)).verdict is tc.ReleaseVerdict.RELEASE


def test_sonda_presente_depois_do_qualificador_bloqueia_e_antes_nao() -> None:
    row = launch(5, phase=LP.CREATED)
    ok = ev(ES.LAUNCH, EB.IDENTITY_PROBE_ABSENT, ADOPTER, lid=5, lver=1)
    present = ev(ES.LAUNCH, EB.PROBE_PRESENT, ADOPTER, lid=5, lver=1)
    d = decide(_snap_with(row, ok, present))
    assert BC.EVIDENCE_CONTRADICTED in codes(d)
    earlier = ev(ES.LAUNCH, EB.PROBE_PRESENT, ADOPTER, lid=5, lver=1, seq=0)
    later_ok = ev(ES.LAUNCH, EB.IDENTITY_PROBE_ABSENT, ADOPTER, lid=5, lver=1, seq=10_000)
    assert decide(_snap_with(row, earlier, later_ok)).verdict is tc.ReleaseVerdict.RELEASE
    assert tc.classify_resources(_snap_with(row, present)).verdict is TV.STILL_ACTIVE


def test_posix_nunca_e_liberado_por_sonda_e_so_por_terminal_ou_reboot() -> None:
    row = launch(5, phase=LP.CONTAINED, platform=Platform.POSIX)
    probe = ev(ES.LAUNCH, EB.IDENTITY_PROBE_ABSENT, ADOPTER, lid=5, lver=1)
    job = ev(
        ES.LAUNCH,
        EB.JOB_GUARANTEE_VERIFIED,
        ADOPTER,
        lid=5,
        lver=1,
        match=True,
        prof=True,
        oq=True,
        rp=True,
    )
    d = decide(_snap_with(row, probe, job))
    assert d.verdict is tc.ReleaseVerdict.HOLD and BC.LAUNCH_UNRESOLVED in codes(d)
    reboot = ev(
        ES.LAUNCH, EB.MACHINE_RESTARTED_SINCE_DECLARATION, ADOPTER, lid=5, lver=1, boot=True
    )
    assert decide(_snap_with(row, reboot)).verdict is tc.ReleaseVerdict.RELEASE
    no_boot = ev(
        ES.LAUNCH, EB.MACHINE_RESTARTED_SINCE_DECLARATION, ADOPTER, lid=5, lver=1, boot=False
    )
    assert decide(_snap_with(row, no_boot)).verdict is tc.ReleaseVerdict.HOLD
    ended = launch(5, phase=LP.ENDED_CONFIRMED, platform=Platform.POSIX)
    assert decide(_snap_with(ended)).verdict is tc.ReleaseVerdict.RELEASE


@pytest.mark.parametrize(
    "flag",
    ["prof", "oq", "rp", "match"],
)
def test_windows_contained_exige_todas_as_condicoes_j(flag: str) -> None:
    row = launch(5, phase=LP.CONTAINED)
    flags: dict[str, Any] = {"prof": True, "oq": True, "rp": True, "match": True}
    assert (
        decide(
            _snap_with(
                row, ev(ES.LAUNCH, EB.JOB_GUARANTEE_VERIFIED, ADOPTER, lid=5, lver=1, **flags)
            )
        ).verdict
        is tc.ReleaseVerdict.RELEASE
    )
    flags[flag] = False
    d = decide(
        _snap_with(row, ev(ES.LAUNCH, EB.JOB_GUARANTEE_VERIFIED, ADOPTER, lid=5, lver=1, **flags))
    )
    assert d.verdict is tc.ReleaseVerdict.HOLD


def test_job_guarantee_exige_adotante_e_o_dono_nao_basta() -> None:
    row = launch(5, phase=LP.CONTAINED)
    owner_wrote = ev(
        ES.LAUNCH,
        EB.JOB_GUARANTEE_VERIFIED,
        OWNER,
        lid=5,
        lver=1,
        match=True,
        prof=True,
        oq=True,
        rp=True,
    )
    assert decide(_snap_with(row, owner_wrote)).verdict is tc.ReleaseVerdict.HOLD
    live = live_snapshot(launches=(row,))
    live = _replace(live, evidence=(*live.evidence, owner_wrote))
    assert (
        decide(live).verdict is tc.ReleaseVerdict.HOLD
    )  # sem adoção não há garantia por construção


def test_ended_unconfirmed_so_resolve_por_reboot_exceto_popen_raised() -> None:
    row = launch(5, phase=LP.ENDED_UNCONFIRMED, basis=EndBasis.TREE_UNCONFIRMED)
    assert decide(_snap_with(row)).verdict is tc.ReleaseVerdict.HOLD
    popen = launch(5, phase=LP.ENDED_UNCONFIRMED, basis=EndBasis.POPEN_RAISED)
    assert decide(_snap_with(popen)).verdict is tc.ReleaseVerdict.HOLD  # sem evidência
    proven = ev(ES.LAUNCH, EB.LAUNCH_START_FAILED_PROVEN, OWNER, lid=5, lver=1, sup=True)
    assert decide(_snap_with(popen, proven)).verdict is tc.ReleaseVerdict.RELEASE
    by_adopter = ev(ES.LAUNCH, EB.LAUNCH_START_FAILED_PROVEN, ADOPTER, lid=5, lver=1, sup=True)
    assert decide(_snap_with(popen, by_adopter)).verdict is tc.ReleaseVerdict.HOLD


# ----------------------------------------------------------------------------- 9. pedido, selo, ack


def test_ack_historico_pegajoso_mesmo_apos_estados_intermediarios() -> None:
    for state in (RS.UNPROVEN, RS.TEARDOWN_PENDING, RS.DRAINING):
        for ack in (None, T0 - timedelta(seconds=1)):
            snap = adopted_snapshot(reservation=facts(state, adopted=True, ever=True, ack=ack))
            d = decide(snap)
            assert BC.ACK_REQUIRED in codes(d), (state, ack)
    ok = adopted_snapshot(reservation=facts(RS.TEARDOWN_PENDING, adopted=True, ever=True, ack=T0))
    assert decide(ok).verdict is tc.ReleaseVerdict.RELEASE
    never = live_snapshot()  # nunca passou por unproven: sem ack
    assert decide(never).verdict is tc.ReleaseVerdict.RELEASE


def test_pedido_de_liberacao_validado() -> None:
    snap = live_snapshot()
    assert BC.RELEASER_NOT_PARTICIPANT in codes(decide(snap, releaser_token=OTHER))
    assert BC.RELEASER_NOT_PARTICIPANT in codes(decide(snap, releaser_token=ADOPTER))
    ad = adopted_snapshot()
    assert BC.RELEASER_NOT_PARTICIPANT in codes(
        decide(ad, releaser_token=OWNER)
    )  # com adoção, o adotante
    assert BC.SEAL_MISSING in codes(decide(snap, seal_present=False))
    assert BC.VERSION_MISMATCH in codes(decide(snap, expected_version=99))
    assert BC.EDGE_ILLEGAL in codes(decide(snap, expected_state=RS.TEARDOWN_PENDING))
    active = live_snapshot(reservation=facts(RS.ACTIVE))
    assert BC.EDGE_ILLEGAL in codes(decide(active))  # active nunca vai direto a released
    released = live_snapshot(reservation=facts(RS.RELEASED))
    assert BC.EDGE_ILLEGAL in codes(decide(released))


def test_veredito_release_so_existe_sem_bloqueador() -> None:
    with pytest.raises(ValueError):
        tc.ReleaseDecision(tc.ReleaseVerdict.RELEASE, (tc.Blocker(BC.RUN_OPEN),))
    with pytest.raises(ValueError):
        tc.ReleaseDecision(tc.ReleaseVerdict.HOLD, ())


# --- 10. autorização indevida


def _defects() -> list[tuple[str, tc.ReleaseSnapshot]]:
    live = live_snapshot()
    ad = adopted_snapshot()
    out: list[tuple[str, tc.ReleaseSnapshot]] = [
        ("sem evidência", _replace(live, evidence=())),
        ("lease unknown", _replace(live, lease=LeaseStatus.UNKNOWN)),
        ("lease incident", _replace(live, lease=LeaseStatus.INCIDENT)),
        ("history unknown", _replace(live, history=HistoryCurrency.UNKNOWN)),
        ("history stale", _replace(live, history=HistoryCurrency.STALE_SUSPECTED)),
        ("adotado sem quiescência", _replace(ad, evidence=())),
        (
            "adotado, recusa por terceiro",
            _replace(
                ad,
                launches=(
                    launch(
                        1,
                        phase=LP.ENDED_CONFIRMED,
                        basis=EndBasis.REFUSED_BEFORE_CREATION,
                        by=OTHER,
                    ),
                ),
            ),
        ),
    ]
    return out


@pytest.mark.parametrize(("name", "snap"), _defects(), ids=[n for n, _ in _defects()])
def test_nenhum_defeito_unico_libera(name: str, snap: tc.ReleaseSnapshot) -> None:
    assert decide(snap).verdict is not tc.ReleaseVerdict.RELEASE, name
    assert tc.decide_release(snap, request(snap)).blockers


def test_nenhum_valor_desconhecido_de_enum_nem_tipo_errado_autoriza() -> None:
    snap = live_snapshot()
    with pytest.raises(ValueError):
        tc.LeaseStatus("trusted")
    with pytest.raises(ValueError):
        tc.HistoryCurrency("yes")
    # string crua (igual ao valor do enum) não é o membro: rejeitada na construção
    with pytest.raises(TypeError):
        _replace(snap, history="proven_current")
    with pytest.raises(TypeError):
        _replace(snap, lease="consistent")
    # contornar o construtor (objeto montado por __setattr__) é revalidado na entrada
    forged = _replace(snap)
    object.__setattr__(forged, "history", "proven_current")
    object.__setattr__(forged, "lease", "consistent")
    with pytest.raises(TypeError):
        tc.decide_release(forged, request(snap))
    with pytest.raises(TypeError):
        tc.assess_teardown(forged)


def test_evidencia_nao_vem_do_pedido() -> None:
    assert {f.name for f in dataclasses.fields(tc.ReleaseRequest)} == {
        "expected_state",
        "expected_version",
        "releaser_token",
        "seal_present",
    }


def test_determinismo_e_imutabilidade() -> None:
    snap = live_snapshot()
    assert decide(snap) == decide(snap)
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.lease = LeaseStatus.UNKNOWN  # type: ignore[misc]


def test_exaustivo_pequeno_so_libera_com_tudo_comprovado() -> None:
    """Combina presença/ausência das provas de um estado vivo: só a combinação completa libera."""
    pieces: dict[str, Callable[[tc.EvidenceRecord], bool]] = {
        "drain": lambda ev_: ev_.scope is ES.IN_PROCESS,
        "bind_c": lambda ev_: ev_.subject is BS.CONTROL,
        "bind_d": lambda ev_: ev_.subject is BS.DEVELOPER,
        "bind_r": lambda ev_: ev_.subject is BS.TEST_RUNNER,
    }
    names = list(pieces)
    for mask in itertools.product([True, False], repeat=len(names)):
        drop = [pieces[n] for n, keep in zip(names, mask, strict=True) if not keep]
        kept = tuple(x for x in live_evidence() if not any(f(x) for f in drop))
        for lease, hist in itertools.product(LeaseStatus, HistoryCurrency):
            snap = live_snapshot(evidence=kept, lease=lease, history=hist)
            released = decide(snap).verdict is tc.ReleaseVerdict.RELEASE
            expected = (
                all(mask)
                and lease is LeaseStatus.CONSISTENT
                and hist is HistoryCurrency.PROVEN_CURRENT
            )
            assert released == expected, (mask, lease, hist)


# ----------------------------------------------------------------------------- 11. teardown


def test_classificacao_agregada_pega_o_pior_e_so_proven_closed_quando_tudo_fecha() -> None:
    assert tc.classify_resources(live_snapshot()).verdict is TV.PROVEN_CLOSED
    intent = live_snapshot(launches=(*live_snapshot().launches, launch(9, phase=LP.INTENT)))
    assert tc.classify_resources(intent).verdict is TV.UNPROVEN
    present = ev(ES.LAUNCH, EB.PROBE_PRESENT, lid=9, lver=1)
    both = _replace(intent, evidence=(*intent.evidence, present))
    assert tc.classify_resources(both).verdict is TV.STILL_ACTIVE
    assert tc.classify_resources(both).blockers


# ----------------------------------------------------------------------------- 12. gate de teardown


def cls(code: ExecutionCode, run: RunStatus, res: TaskResolution | None) -> ExecutionClassification:
    return ExecutionClassification(code, run, res)


SUCCESS = cls(ExecutionCode.SUCCESS, RunStatus.OK, TaskResolution(TaskStatus.DONE, None))
TESTS_FAILED = cls(
    ExecutionCode.TESTS_FAILED, RunStatus.ERROR, TaskResolution(TaskStatus.NEEDS_FIX, None)
)
CANCELLED_OK = cls(ExecutionCode.SUCCESS, RunStatus.OK, None)  # trabalho ok, mas cancel venceu
CANCELLED = cls(ExecutionCode.CANCELLED, RunStatus.CANCELLED, None)
BLOCKED = cls(
    ExecutionCode.INTEGRITY_VIOLATED,
    RunStatus.BLOCKED,
    TaskResolution(TaskStatus.FAILED, FailureReason.BLOCKED_BY_POLICY),
)
NOT_PROVEN = [TV.UNPROVEN, TV.STILL_ACTIVE, TV.TECHNICAL_FAILURE]


def snapshot_for(verdict: TV) -> tc.ReleaseSnapshot:
    """Snapshot REAL cujo teardown completo avalia exatamente `verdict`."""
    positives = live_evidence()
    if verdict is TV.PROVEN_CLOSED:
        snap = live_snapshot()
    elif verdict is TV.UNPROVEN:
        snap = live_snapshot(evidence=tuple(x for x in positives if x.scope is not ES.IN_PROCESS))
    elif verdict is TV.STILL_ACTIVE:
        snap = live_snapshot(evidence=(*positives, ev(ES.IN_PROCESS, EB.DRAIN_TIMEOUT)))
    else:
        snap = live_snapshot(evidence=(*positives, ev(ES.IN_PROCESS, EB.DRAIN_REENTRANT)))
    assert tc.assess_teardown(snap).verdict is verdict
    return snap


def test_gate_com_teardown_comprovado_nao_altera_nada() -> None:
    for base in (SUCCESS, TESTS_FAILED, CANCELLED_OK, CANCELLED, BLOCKED):
        got = tc.gate_teardown(base, snapshot_for(TV.PROVEN_CLOSED))
        assert (got.code, got.run_status, got.task_resolution) == (
            base.code,
            base.run_status,
            base.task_resolution,
        )
        assert got.teardown_proven and not got.downgraded


@pytest.mark.parametrize("verdict", NOT_PROVEN)
def test_teardown_nao_comprovado_impede_done_e_needs_fix(verdict: TV) -> None:
    for base in (SUCCESS, TESTS_FAILED):
        got = tc.gate_teardown(base, snapshot_for(verdict))
        assert got.code is tc.FinalizationCode.TEARDOWN_UNPROVEN
        assert got.run_status is RunStatus.ERROR
        assert got.task_resolution == TaskResolution(
            TaskStatus.FAILED, FailureReason.INTERNAL_ERROR
        )
        assert got.downgraded and not got.teardown_proven


@pytest.mark.parametrize("verdict", NOT_PROVEN)
def test_gate_preserva_cancelamento_terminal_e_nao_deixa_run_ok(verdict: TV) -> None:
    got = tc.gate_teardown(CANCELLED_OK, snapshot_for(verdict))
    assert got.task_resolution is None  # terminal confirmado preservado
    assert got.run_status is RunStatus.ERROR and got.code is tc.FinalizationCode.TEARDOWN_UNPROVEN
    same = tc.gate_teardown(CANCELLED, snapshot_for(verdict))
    assert (same.code, same.run_status, same.task_resolution) == (
        ExecutionCode.CANCELLED,
        RunStatus.CANCELLED,
        None,
    )
    assert not same.teardown_proven


@pytest.mark.parametrize("verdict", NOT_PROVEN)
def test_gate_preserva_falha_mais_grave(verdict: TV) -> None:
    got = tc.gate_teardown(BLOCKED, snapshot_for(verdict))
    assert got.code is ExecutionCode.INTEGRITY_VIOLATED
    assert got.task_resolution == BLOCKED.task_resolution
    assert not got.teardown_proven and not got.downgraded


def test_gate_exige_o_snapshot_e_o_tipo_da_classificacao() -> None:
    for bad in (
        True,
        False,
        None,
        "proven_closed",
        1,
        TV.PROVEN_CLOSED,
        tc.TeardownReport(TV.PROVEN_CLOSED, ()),
        tc.assess_teardown(live_snapshot()),  # avaliação (diagnóstico) não é credencial
        live_snapshot().reservation,
        tc.decide_release(live_snapshot(), request(live_snapshot())),
    ):
        with pytest.raises(TypeError):
            tc.gate_teardown(SUCCESS, bad)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        tc.gate_teardown("success", snapshot_for(TV.PROVEN_CLOSED))  # type: ignore[arg-type]


def test_decisao_final_nao_admite_done_nem_run_ok_sem_teardown() -> None:
    with pytest.raises(ValueError):
        tc.FinalizationDecision(ExecutionCode.SUCCESS, RunStatus.OK, None, False, False)
    with pytest.raises(ValueError):
        tc.FinalizationDecision(
            ExecutionCode.SUCCESS,
            RunStatus.ERROR,
            TaskResolution(TaskStatus.DONE, None),
            False,
            False,
        )
    with pytest.raises(ValueError):
        tc.FinalizationDecision(
            ExecutionCode.TESTS_FAILED,
            RunStatus.ERROR,
            TaskResolution(TaskStatus.NEEDS_FIX, None),
            False,
            False,
        )


def test_classify_execution_e_execution_facts_estao_preservados() -> None:
    from app.orchestrator import execution_contract as ec

    assert not hasattr(ec, "FinalizationCode")
    assert {f.name for f in dataclasses.fields(ec.ExecutionFacts)} == {
        "integrity",
        "developer",
        "tests",
        "policy_satisfied",
        "confirmed_terminal",
    }
    facts_ok = ec.ExecutionFacts(
        integrity=ec.IntegrityVerdict.VERIFIED,
        developer=ec.DeveloperOutcome.COMPLETED,
        tests=ec.RunnerOutcome.PASSED,
    )
    base = ec.classify_execution(facts_ok)
    assert base.code is ExecutionCode.SUCCESS
    gated = tc.gate_teardown(base, snapshot_for(TV.UNPROVEN)).task_resolution
    assert gated is not None and gated.status is TaskStatus.FAILED


# ----------------------------------------------------------------------------- 13. lease (C7.4)


def lease(
    phase: tc.LeasePhase = tc.LeasePhase.ARMED, *, lk: str = "L", pk: str = "P"
) -> tc.LeaseDoc:
    return tc.LeaseDoc(phase, "R1", lk, pk)


def dl(doc: tc.LeaseDoc | None, **over: object) -> tc.LeaseDecision:
    base: dict[str, object] = {
        "current_logical_key": "L",
        "current_physical_key": "P",
        "reservation_found": True,
        "reservation_state": RS.DRAINING,
    }
    base.update(over)
    return tc.decide_lease(doc, **base)  # type: ignore[arg-type]


def test_decisao_do_lease_no_startup() -> None:
    assert dl(None) is tc.LeaseDecision.PROCEED
    assert (
        dl(lease(tc.LeasePhase.RESERVING), reservation_found=False, reservation_state=None)
        is tc.LeaseDecision.DISCARD
    )
    assert dl(lease(pk="X")) is tc.LeaseDecision.INCIDENT  # caminho → Y
    assert dl(lease(lk="X")) is tc.LeaseDecision.INCIDENT
    assert dl(lease(), reservation_found=False, reservation_state=None) is tc.LeaseDecision.INCIDENT
    assert dl(lease(), reservation_state=RS.RELEASED) is tc.LeaseDecision.REMOVE_STALE
    for st in (RS.ACTIVE, RS.DRAINING, RS.TEARDOWN_PENDING, RS.UNPROVEN):
        assert dl(lease(), reservation_state=st) is tc.LeaseDecision.RECOVER


def test_lease_recover_nao_e_prova_de_historico_atual() -> None:
    """`RECOVER` apenas autoriza a recuperação; liberar ainda exige `HistoryCurrency`."""
    assert dl(lease()) is tc.LeaseDecision.RECOVER
    snap = live_snapshot(lease=LeaseStatus.CONSISTENT, history=HistoryCurrency.UNKNOWN)
    assert decide(snap).verdict is tc.ReleaseVerdict.HOLD


# ----------------------------------------------------------------------------- 14. pureza


def test_modulo_e_puro_sem_io() -> None:
    import ast
    from pathlib import Path

    path = Path(tc.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = (
        "sqlite3",
        "sqlalchemy",
        "subprocess",
        "os",
        "pathlib",
        "shutil",
        "socket",
        "threading",
        "multiprocessing",
        "asyncio",
        "time",
        "msvcrt",
        "fcntl",
        "ctypes",
        "app.git_runtime",
        "app.process_runtime",
        "app.path_runtime",
        "app.tool_executor",
        "app.agent_runtime",
        "app.db.models",
        "app.db.session",
        "app.orchestrator.execution_manager",
    )
    for name in imported:
        assert not name.startswith(forbidden), name


# =============================================================================================
# Correção dos 4 findings da auditoria do Codex (P1-I1-01, P1-I1-02, P2-I1-03, P2-I1-C1)
# =============================================================================================
import random  # noqa: E402

BAD_BOOLS = ["false", "False", "true", 0, 1, None, "no", 1.0]


def _bad_enum_cases() -> list[tuple[str, Callable[[], object]]]:
    """(rótulo, construtor) com UM campo-enum trocado por string crua em cada fronteira."""
    ok_launch = launch(1)
    ok_ev = ev(ES.IN_PROCESS, EB.EXECUTOR_DRAINED)
    ok_facts = facts()
    return [
        ("RunFact.role", lambda: tc.RunFact("control", RunStatus.OK, "C", "k")),  # type: ignore[arg-type]
        ("RunFact.status", lambda: tc.RunFact(tc.RunRole.CONTROL, "running", "C", "k")),  # type: ignore[arg-type]
        ("Launch.phase", lambda: _replace(ok_launch, phase="ended_confirmed")),
        ("Launch.phase_tag", lambda: _replace(ok_launch, phase_tag="developer")),
        ("Launch.platform", lambda: _replace(ok_launch, platform="win32")),
        (
            "Launch.profile",
            lambda: _replace(ok_launch, containment_profile="win_job_v1"),
        ),
        (
            "Launch.end_basis",
            lambda: _replace(ok_launch, end_basis="tree_confirmed_dead"),
        ),
        ("Evidence.scope", lambda: _replace(ok_ev, scope="in_process")),
        ("Evidence.basis", lambda: _replace(ok_ev, basis="executor_drained")),
        ("Evidence.subject", lambda: _replace(ok_ev, subject="control")),
        ("Facts.state", lambda: _replace(ok_facts, state="draining")),
        ("Request.state", lambda: tc.ReleaseRequest("draining", 3, OWNER, True)),  # type: ignore[arg-type]
        ("Snapshot.lease", lambda: _replace(live_snapshot(), lease="consistent")),
        (
            "Snapshot.history",
            lambda: _replace(live_snapshot(), history="proven_current"),
        ),
        ("LeaseDoc.phase", lambda: tc.LeaseDoc("armed", "R", "L", "P")),  # type: ignore[arg-type]
        ("Blocker.code", lambda: tc.Blocker("run_open")),  # type: ignore[arg-type]
        ("Decision.verdict", lambda: tc.ReleaseDecision("release")),  # type: ignore[arg-type]
    ]


_ENUM_CASES = _bad_enum_cases()


@pytest.mark.parametrize(("label", "build"), _ENUM_CASES, ids=[c[0] for c in _ENUM_CASES])
def test_p1_01_enum_como_string_e_recusado_em_toda_fronteira(
    label: str, build: Callable[[], object]
) -> None:
    with pytest.raises(TypeError):
        build()


@pytest.mark.parametrize("bad", BAD_BOOLS, ids=[repr(b) for b in BAD_BOOLS])
@pytest.mark.parametrize(
    "field",
    [
        "lock_files_match",
        "profile_ok",
        "owner_quiescence_ok",
        "root_probe_absent",
        "boot_changed",
        "supervision_confirmed",
    ],
)
def test_p1_01_flags_booleanas_de_evidencia_precisam_ser_bool_real(field: str, bad: object) -> None:
    with pytest.raises(TypeError):
        _replace(ev(ES.IN_PROCESS, EB.EXECUTOR_DRAINED), **{field: bad})


@pytest.mark.parametrize("bad", BAD_BOOLS, ids=[repr(b) for b in BAD_BOOLS])
def test_p1_01_outros_booleanos_estritos(bad: object) -> None:
    with pytest.raises(TypeError):
        _replace(facts(), developer_declared=bad)
    with pytest.raises(TypeError):
        _replace(facts(), ever_unproven=bad)
    with pytest.raises(TypeError):
        tc.ReleaseRequest(RS.DRAINING, 3, OWNER, bad)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        tc.launch_insert_refusal(
            reservation_state=RS.ACTIVE,
            adopted_by_token=None,
            drain_requested=bad,  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        dl(None, reservation_found=bad)


@pytest.mark.parametrize("bad", [True, False, "1", 1.0, None])
def test_p1_01_inteiros_sao_inteiros_reais_nunca_bool(bad: object) -> None:
    builders: list[Callable[[], object]] = [
        lambda: _replace(launch(1), version=bad),
        lambda: _replace(launch(1), id=bad),
        lambda: _replace(facts(), version=bad),
        lambda: tc.ReleaseRequest(RS.DRAINING, bad, OWNER, True),  # type: ignore[arg-type]
        lambda: _replace(ev(ES.IN_PROCESS, EB.EXECUTOR_DRAINED), seq=bad),
    ]
    if bad is not None:  # `None` é um pid válido (ausente) — o par pid/start_token é outra regra
        builders.append(lambda: _replace(launch(1, phase=LP.CREATED), pid=bad))
    for build in builders:
        with pytest.raises(TypeError):
            build()
    with pytest.raises(ValueError):
        _replace(launch(1), version=0)
    with pytest.raises(ValueError):
        _replace(facts(), version=-1)


@pytest.mark.parametrize("bad", ["2026-10-14", 5])
def test_p1_01_datas_precisam_ser_datetime_com_fuso(bad: object) -> None:
    with pytest.raises(TypeError):
        _replace(facts(ever=True), ack_at=bad)
    with pytest.raises(ValueError):
        _replace(facts(ever=True), ack_at=datetime(2026, 10, 14))  # naive
    with pytest.raises(TypeError):
        _replace(facts(ever=True), first_unproven_at="x")


def test_p1_01_snapshot_exige_tuplas_de_registros_do_tipo_exato() -> None:
    snap = live_snapshot()
    for field, bad in (
        ("launches", ["x"]),
        ("launches", ("x",)),
        ("launches", None),
        ("evidence", [1]),
        ("evidence", (1,)),
        ("runs", None),
        ("runs", list(runs())),
        ("runs", ("control",)),
        ("reservation", object()),
    ):
        with pytest.raises(TypeError):
            _replace(snap, **{field: bad})


def test_p1_01_funcoes_publicas_rejeitam_entradas_invalidas() -> None:
    calls: list[Callable[[], object]] = [
        lambda: tc.reservation_edge_allowed("active", RS.DRAINING),  # type: ignore[arg-type]
        lambda: tc.reservation_edge_allowed(RS.ACTIVE, "draining"),  # type: ignore[arg-type]
        lambda: tc.reservation_edge_allowed(None, RS.DRAINING),  # type: ignore[arg-type]
        lambda: tc.adoption_edge_allowed("active", "teardown_pending"),  # type: ignore[arg-type]
        lambda: tc.adoption_edge_allowed(RS.ACTIVE, "teardown_pending"),  # type: ignore[arg-type]
        lambda: tc.adoption_edge_allowed(1, RS.TEARDOWN_PENDING),  # type: ignore[arg-type]
        lambda: tc.launch_edge_allowed("intent", LP.CREATED),  # type: ignore[arg-type]
        lambda: tc.launch_edge_allowed(LP.INTENT, LP.CREATED, "x"),  # type: ignore[arg-type]
        lambda: e(basis="executor_drained"),
        lambda: e(reservation_state="draining"),
        lambda: e(writer_token=""),
        lambda: e(writer_token=None),
        lambda: e(adopted_by_token=""),
        lambda: w(old_phase="intent"),
        lambda: w(end_basis="refused_before_creation"),
        lambda: w(writer_token=1),
        lambda: tc.decide_release("snapshot", request(live_snapshot())),  # type: ignore[arg-type]
        lambda: tc.decide_release(live_snapshot(), "request"),  # type: ignore[arg-type]
        lambda: tc.assess_teardown(None),  # type: ignore[arg-type]
        lambda: tc.classify_resources({}),  # type: ignore[arg-type]
        lambda: tc.decide_lease(
            "lease",  # type: ignore[arg-type]
            current_logical_key="L",
            current_physical_key="P",
            reservation_found=False,
            reservation_state=None,
        ),
        lambda: dl(None, current_logical_key=""),
        lambda: dl(None, reservation_state="draining"),
    ]
    for call in calls:
        with pytest.raises((TypeError, ValueError)):
            call()


def test_p1_01_objeto_montado_contornando_o_construtor_e_revalidado() -> None:
    snap = live_snapshot()
    victim = _replace(snap.runs[0])
    object.__setattr__(victim, "status", "running")  # contorna a validação do construtor
    forged = _replace(snap)
    object.__setattr__(forged, "runs", (victim, *snap.runs[1:]))
    with pytest.raises(TypeError):
        tc.decide_release(forged, request(snap))
    req = request(snap)
    object.__setattr__(req, "seal_present", "false")
    with pytest.raises(TypeError):
        tc.decide_release(snap, req)
    doc = lease()
    object.__setattr__(doc, "phase", "bogus")
    with pytest.raises(TypeError):
        dl(doc)


# ---------------------------------------------------------------------------- P1-I1-02


def test_p1_02_snapshot_sem_run_de_controle_nao_libera() -> None:
    no_runner = tuple(x for x in live_evidence() if x.subject is not BS.TEST_RUNNER)
    kw: dict[str, object] = {"launches": (launch(1),), "evidence": no_runner}
    d = decide(live_snapshot(runs=(), **kw))
    assert d.verdict is tc.ReleaseVerdict.HOLD and BC.RUN_CONTROL_MISSING in codes(d)
    only_dev = tuple(r for r in runs(runner=False) if r.role is not tc.RunRole.CONTROL)
    assert BC.RUN_CONTROL_MISSING in codes(decide(live_snapshot(runs=only_dev, **kw)))
    assert not tc.assess_teardown(live_snapshot(runs=(), **kw)).proven
    ok = live_snapshot(runs=runs(runner=False), **kw)  # controle da própria construção
    assert decide(ok).verdict is tc.ReleaseVerdict.RELEASE


@pytest.mark.parametrize("role", list(tc.RunRole))
def test_p1_02_run_duplicado_e_incidente_mesmo_com_status_final(role: tc.RunRole) -> None:
    base = list(runs())
    dup = next(r for r in base if r.role is role)
    snap = live_snapshot(runs=(*base, dup))
    d = decide(snap)
    assert d.verdict is tc.ReleaseVerdict.INCIDENT
    assert tc.Blocker(BC.RUN_DUPLICATED, role.value) in d.blockers
    assert not tc.assess_teardown(snap).proven


def test_p1_02_identidade_incompativel_do_run_e_incidente() -> None:
    cases = [
        _replace(runs()[0], run_id="OUTRO"),
        _replace(runs()[1], invocation_id="OUTRO:developer"),
        _replace(runs()[1], invocation_id="C:test_runner"),
        _replace(runs()[2], invocation_id="C:developer"),
    ]
    for bad in cases:
        rs = tuple(bad if r.role is bad.role else r for r in runs())
        d = decide(live_snapshot(runs=rs))
        assert d.verdict is tc.ReleaseVerdict.INCIDENT, bad
        assert BC.RUN_IDENTITY_MISMATCH in codes(d)


def test_p1_02_runner_run_exists_nao_e_campo_livre() -> None:
    assert "runner_run_exists" not in {f.name for f in dataclasses.fields(tc.ReleaseSnapshot)}
    with pytest.raises(TypeError):
        tc.ReleaseSnapshot(  # type: ignore[call-arg]
            reservation=facts(),
            launches=(),
            evidence=(),
            runs=control_only_runs(),
            lease=LeaseStatus.CONSISTENT,
            history=HistoryCurrency.PROVEN_CURRENT,
            runner_run_exists=False,
        )


def test_p1_02_flag_do_runner_nao_esconde_run_presente() -> None:
    """O Run do runner existe: o binding dele é exigido, sem launch de runner e sem flag."""
    evid = tuple(x for x in live_evidence() if x.subject is not BS.TEST_RUNNER)
    snap = live_snapshot(evidence=evid, launches=(launch(1),))
    assert snap.runner_present
    d = decide(snap)
    assert tc.Blocker(BC.BINDING_UNPROVEN, "test_runner") in d.blockers
    assert d.verdict is tc.ReleaseVerdict.HOLD


def test_p1_02_developer_declared_falso_com_run_ou_launch_do_developer() -> None:
    no_dev_evidence = tuple(
        x for x in live_evidence() if x.scope is not ES.IN_PROCESS and x.subject is not BS.DEVELOPER
    )
    # (a) Run do Developer presente, flag falsa, prova faltando: a prova continua EXIGIDA
    snap = live_snapshot(
        reservation=facts(dev=False),
        evidence=no_dev_evidence,
        launches=(launch(1),),
        runs=runs(runner=False),
    )
    d = decide(snap)
    assert d.verdict is tc.ReleaseVerdict.INCIDENT
    assert BC.CONTRADICTION_DEVELOPER_UNDECLARED in codes(d)
    assert {BC.DRAIN_UNPROVEN, BC.BINDING_UNPROVEN} <= codes(d)
    # (b) mesmo com TODA a prova, a contradição sozinha impede a liberação
    full = live_snapshot(
        reservation=facts(dev=False),
        runs=runs(runner=False),
        evidence=tuple(x for x in live_evidence() if x.subject is not BS.TEST_RUNNER),
        launches=(launch(1),),
    )
    assert decide(full).verdict is tc.ReleaseVerdict.INCIDENT
    # (c) só launch do Developer, sem Run
    only_launch = live_snapshot(
        reservation=facts(dev=False),
        runs=control_only_runs(),
        launches=(launch(1, tag=PhaseTag.DEVELOPER),),
        evidence=tuple(x for x in live_evidence() if x.subject is BS.CONTROL),
    )
    assert BC.CONTRADICTION_DEVELOPER_UNDECLARED in codes(decide(only_launch))
    assert BC.DRAIN_UNPROVEN in codes(decide(only_launch))


def test_p1_02_runner_sem_developer_declarado_e_launch_de_runner_sem_run() -> None:
    row = launch(3, tag=PhaseTag.TEST_RUNNER)
    d = decide(live_snapshot(reservation=facts(dev=False), launches=(row,)))
    assert BC.CONTRADICTION_RUNNER_BEFORE_DEVELOPER in codes(d)
    no_run = live_snapshot(launches=(row,), runs=runs(runner=False))
    d2 = decide(no_run)
    assert d2.verdict is tc.ReleaseVerdict.INCIDENT
    assert BC.CONTRADICTION_RUNNER_WITHOUT_RUN in codes(d2)
    # o recurso presente (launch do runner) continua exigindo a prova dele
    assert tc.Blocker(BC.BINDING_UNPROVEN, "test_runner") not in d2.blockers
    no_bind = live_snapshot(
        launches=(row,),
        runs=runs(runner=False),
        evidence=tuple(x for x in live_evidence() if x.subject is not BS.TEST_RUNNER),
    )
    assert tc.Blocker(BC.BINDING_UNPROVEN, "test_runner") in decide(no_bind).blockers


def test_p1_02_exaustivo_nenhuma_combinacao_de_flags_dispensa_prova() -> None:
    """declared × Run do dev × launch do dev × Run do runner × launch do runner: libera
    SOMENTE quando as flags concordam com os registros e toda prova exigida está presente."""
    for declared, dev_run, dev_launch, run_run, run_launch in itertools.product(
        [True, False], repeat=5
    ):
        rs = [tc.RunFact(tc.RunRole.CONTROL, RunStatus.OK, "C", "client-key")]
        if dev_run:
            rs.append(tc.RunFact(tc.RunRole.DEVELOPER, RunStatus.OK, "D", "C:developer"))
        if run_run:
            rs.append(tc.RunFact(tc.RunRole.TEST_RUNNER, RunStatus.OK, "N", "C:test_runner"))
        launches = []
        if dev_launch:
            launches.append(launch(1, tag=PhaseTag.DEVELOPER))
        if run_launch:
            launches.append(launch(2, tag=PhaseTag.TEST_RUNNER))
        dev_present = declared or dev_run or dev_launch
        runner_present = run_run or run_launch
        for variant in ("full", "minimal"):
            evid = (
                live_evidence()
                if variant == "full"
                else [x for x in live_evidence() if x.subject is BS.CONTROL]
            )
            snap = live_snapshot(
                reservation=facts(dev=declared),
                runs=tuple(rs),
                launches=tuple(launches),
                evidence=tuple(evid),
            )
            consistent = (
                (declared or not (dev_run or dev_launch))
                and not ((dev_launch or runner_present) and not dev_run)
                and not (run_launch and not run_run)
                and not (runner_present and not declared)
            )
            proof_present = variant == "full" or not (dev_present or runner_present)
            released = decide(snap).verdict is tc.ReleaseVerdict.RELEASE
            assert released == (consistent and proof_present), (
                declared,
                dev_run,
                dev_launch,
                run_run,
                run_launch,
                variant,
            )


def test_p1_02_caso_legitimo_componente_nao_iniciado_libera() -> None:
    snap = live_snapshot(
        reservation=facts(dev=False),
        runs=control_only_runs(),
        launches=(),
        evidence=tuple(x for x in live_evidence() if x.subject is BS.CONTROL),
    )
    assert not snap.developer_present and not snap.runner_present
    assert decide(snap).verdict is tc.ReleaseVerdict.RELEASE
    assert tc.assess_teardown(snap).proven


# ---------------------------------------------------------------------------- P2-I1-03


def test_p2_03_tree_unconfirmed_e_tree_confirmed_exigem_processo_criado() -> None:
    for basis in (EndBasis.TREE_UNCONFIRMED, EndBasis.TREE_CONFIRMED_DEAD):
        phase = LP.ENDED_UNCONFIRMED if basis is EndBasis.TREE_UNCONFIRMED else LP.ENDED_CONFIRMED
        row = launch(1, phase=phase, basis=basis)
        assert row.pid is not None
        with pytest.raises(ValueError):
            _replace(row, pid=None, start_token=None)


def test_p2_03_bases_sem_processo_nao_aceitam_pid_e_ledger_failure_aceita_ambos() -> None:
    for basis, phase in (
        (EndBasis.REFUSED_BEFORE_CREATION, LP.ENDED_CONFIRMED),
        (EndBasis.POPEN_RAISED, LP.ENDED_UNCONFIRMED),
    ):
        row = launch(1, phase=phase, basis=basis)
        with pytest.raises(ValueError):
            _replace(row, pid=7, start_token="t")
    with_pid = launch(1, phase=LP.ENDED_UNCONFIRMED, basis=EndBasis.LEDGER_WRITE_FAILED)
    no_pid = _replace(with_pid, pid=None, start_token=None)
    assert no_pid.pid is None and with_pid.pid == 10


@pytest.mark.parametrize("old", [LP.CREATED, LP.CONTAINED])
def test_p2_03_popen_raised_nao_resulta_de_processo_ja_criado_ou_contido(old: LP) -> None:
    assert not tc.launch_edge_allowed(old, LP.ENDED_UNCONFIRMED, EndBasis.POPEN_RAISED)
    assert w(old_phase=old, new_phase=LP.ENDED_UNCONFIRMED, end_basis=EndBasis.POPEN_RAISED) is (
        WriteRefusal.EDGE_ILLEGAL
    )


def test_p2_03_popen_raised_nao_comprova_ausencia_de_processo() -> None:
    popen = launch(5, phase=LP.ENDED_UNCONFIRMED, basis=EndBasis.POPEN_RAISED)
    assert decide(_snap_with(popen)).verdict is tc.ReleaseVerdict.HOLD  # sozinho: não basta
    with pytest.raises(ValueError):  # a evidência exige supervisão confirmada
        ev(ES.LAUNCH, EB.LAUNCH_START_FAILED_PROVEN, OWNER, lid=5, lver=1)
    ok = ev(ES.LAUNCH, EB.LAUNCH_START_FAILED_PROVEN, OWNER, lid=5, lver=1, sup=True)
    assert decide(_snap_with(popen, ok)).verdict is tc.ReleaseVerdict.RELEASE


def test_p2_03_lease_com_fase_desconhecida_nunca_recupera() -> None:
    with pytest.raises(TypeError):
        tc.LeaseDoc("bogus", "R", "L", "P")  # type: ignore[arg-type]
    doc = lease()
    object.__setattr__(doc, "phase", "bogus")
    with pytest.raises(TypeError):
        dl(doc)
    with pytest.raises(ValueError):
        dl(lease(), reservation_found=True, reservation_state=None)
    with pytest.raises(ValueError):
        dl(lease(), reservation_found=False, reservation_state=RS.DRAINING)


def test_p2_03_adotante_nao_pode_ser_o_dono() -> None:
    with pytest.raises(ValueError):
        tc.ReservationFacts(RS.DRAINING, 1, "C", OWNER, OWNER, "L", "P", True, False, None, None)


# ---------------------------------------------------------------------------- P2-I1-C1


@pytest.mark.parametrize("hist", [HistoryCurrency.UNKNOWN, HistoryCurrency.STALE_SUSPECTED])
@pytest.mark.parametrize("lease_", list(LeaseStatus))
def test_c1_recursos_fechados_mas_historico_ou_lease_incompletos_nao_autorizam_done(
    hist: HistoryCurrency, lease_: LeaseStatus
) -> None:
    snap = live_snapshot(history=hist, lease=lease_)
    # (1) os recursos enumerados estão fechados...
    assert tc.classify_resources(snap).verdict is TV.PROVEN_CLOSED
    # (3) ...mas o teardown COMPLETO não está comprovado
    a = tc.assess_teardown(snap)
    assert a.resources is TV.PROVEN_CLOSED
    assert a.completeness is not tc.Completeness.COMPLETE
    assert a.verdict is not TV.PROVEN_CLOSED and not a.proven
    for base in (SUCCESS, TESTS_FAILED):
        got = tc.gate_teardown(base, snap)
        assert got.task_resolution == TaskResolution(
            TaskStatus.FAILED, FailureReason.INTERNAL_ERROR
        )
        assert got.run_status is RunStatus.ERROR and not got.teardown_proven
    assert decide(snap).verdict is not tc.ReleaseVerdict.RELEASE


def test_c1_historico_stale_ou_lease_incidente_sao_incidente_de_completude() -> None:
    stale = tc.assess_teardown(live_snapshot(history=HistoryCurrency.STALE_SUSPECTED))
    assert stale.completeness is tc.Completeness.INCIDENT
    inc = tc.assess_teardown(live_snapshot(lease=LeaseStatus.INCIDENT))
    assert inc.completeness is tc.Completeness.INCIDENT
    unknown = tc.assess_teardown(live_snapshot(history=HistoryCurrency.UNKNOWN))
    assert unknown.completeness is tc.Completeness.INCOMPLETE


def test_c1_teardown_completo_comprovado_autoriza_done_e_needs_fix() -> None:
    snap = live_snapshot()
    a = tc.assess_teardown(snap)
    assert a.proven and a.completeness is tc.Completeness.COMPLETE and not a.blockers
    assert tc.gate_teardown(SUCCESS, snap).task_resolution == SUCCESS.task_resolution
    assert tc.gate_teardown(TESTS_FAILED, snap).task_resolution == TESTS_FAILED.task_resolution


def test_c1_exaustivo_done_so_com_recursos_lease_e_historico_comprovados() -> None:
    open_res = tuple(x for x in live_evidence() if x.scope is not ES.IN_PROCESS)  # sem drenagem
    for res_ok, lease_, hist in itertools.product([True, False], LeaseStatus, HistoryCurrency):
        snap = live_snapshot(
            evidence=tuple(live_evidence()) if res_ok else open_res, lease=lease_, history=hist
        )
        got = tc.gate_teardown(SUCCESS, snap)
        done = got.task_resolution is not None and got.task_resolution.status is TaskStatus.DONE
        assert done == (
            res_ok and lease_ is LeaseStatus.CONSISTENT and hist is HistoryCurrency.PROVEN_CURRENT
        ), (res_ok, lease_, hist)


def test_c1_cancelamento_terminal_e_precedencia_de_integridade_sao_preservados() -> None:
    incomplete = live_snapshot(history=HistoryCurrency.UNKNOWN)
    kept = tc.gate_teardown(CANCELLED_OK, incomplete)
    assert kept.task_resolution is None and kept.run_status is RunStatus.ERROR
    assert tc.gate_teardown(CANCELLED, incomplete).code is ExecutionCode.CANCELLED
    grave = tc.gate_teardown(BLOCKED, incomplete)
    assert grave.code is ExecutionCode.INTEGRITY_VIOLATED
    assert grave.task_resolution == BLOCKED.task_resolution and not grave.downgraded


def test_c1_avaliacao_independe_do_status_dos_runs_mas_a_liberacao_depende() -> None:
    """Pré-finalização: o Run de controle ainda está `running`. O teardown completo já pode estar
    comprovado (e autorizar o fechamento do Run); só `decide_release` exige os Runs fechados."""
    snap = live_snapshot(runs=runs(RunStatus.RUNNING))
    assert tc.assess_teardown(snap).proven
    d = decide(snap)
    assert d.verdict is tc.ReleaseVerdict.HOLD and BC.RUN_OPEN in codes(d)
    closed = live_snapshot()
    assert tc.assess_teardown(closed).proven
    assert decide(closed).verdict is tc.ReleaseVerdict.RELEASE


def test_c1_release_implica_teardown_completo_e_bloqueadores_do_teardown_sao_do_release() -> None:
    rng = random.Random(11)  # noqa: S311
    for _ in range(60):
        evid = [x for x in live_evidence() if rng.random() < 0.8]
        snap = live_snapshot(
            evidence=tuple(evid),
            lease=rng.choice(list(LeaseStatus)),
            history=rng.choice(list(HistoryCurrency)),
            runs=rng.choice([runs(), runs(RunStatus.RUNNING), control_only_runs()]),
        )
        a = tc.assess_teardown(snap)
        d = decide(snap)
        if d.verdict is tc.ReleaseVerdict.RELEASE:
            assert a.proven
        assert set(a.blockers) <= set(d.blockers)  # mesma regra, sem duplicar nem divergir


def test_c1_assessment_so_impoe_coerencia_interna() -> None:
    """Sem `issuer`: a avaliação é diagnóstico. Só a coerência interna é imposta."""
    complete = tc.Completeness.COMPLETE
    assert "issuer" not in {f.name for f in dataclasses.fields(tc.TeardownAssessment)}
    with pytest.raises(ValueError):
        tc.TeardownAssessment(TV.PROVEN_CLOSED, tc.Completeness.INCOMPLETE, TV.PROVEN_CLOSED, ())
    with pytest.raises(ValueError):
        tc.TeardownAssessment(TV.UNPROVEN, complete, TV.PROVEN_CLOSED, ())
    with pytest.raises(ValueError):
        tc.TeardownAssessment(
            TV.PROVEN_CLOSED, complete, TV.PROVEN_CLOSED, (tc.Blocker(BC.LEASE_UNKNOWN),)
        )
    with pytest.raises(TypeError):
        tc.TeardownAssessment("proven_closed", complete, TV.PROVEN_CLOSED, ())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- permutações


def _snapshots_for_permutation() -> list[tc.ReleaseSnapshot]:
    row5 = launch(5, phase=LP.CREATED)
    ok = ev(ES.LAUNCH, EB.IDENTITY_PROBE_ABSENT, ADOPTER, lid=5, lver=1)
    present = ev(ES.LAUNCH, EB.PROBE_PRESENT, ADOPTER, lid=5, lver=1)
    drain_to = ev(ES.IN_PROCESS, EB.DRAIN_TIMEOUT)
    bind_neg = ev(ES.BINDING, EB.BINDING_UNPROVEN, subject=BS.DEVELOPER)
    live = live_snapshot()
    adopted = adopted_snapshot()
    return [
        live,
        adopted,
        _replace(live, evidence=(*live.evidence, drain_to)),
        _replace(live, evidence=(*live.evidence, bind_neg)),
        _replace(adopted, launches=(row5,), evidence=(*adopted.evidence, ok)),
        _replace(adopted, launches=(row5,), evidence=(*adopted.evidence, ok, present)),
    ]


def test_permutar_evidencias_positivas_e_negativas_nao_muda_o_resultado() -> None:
    """A ordem semântica é `seq`, nunca a posição na tupla."""
    rng = random.Random(2026)  # noqa: S311
    for snap in _snapshots_for_permutation():
        base_release = decide(snap)
        base_assess = tc.assess_teardown(snap)
        for _ in range(40):
            shuffled = list(snap.evidence)
            rng.shuffle(shuffled)
            other = _replace(snap, evidence=tuple(shuffled))
            d = decide(other)
            a = tc.assess_teardown(other)
            assert d.verdict is base_release.verdict
            assert set(d.blockers) == set(base_release.blockers)
            assert (a.verdict, a.completeness) == (base_assess.verdict, base_assess.completeness)
            assert set(a.blockers) == set(base_assess.blockers)


def test_permutar_launches_e_runs_nao_muda_o_resultado() -> None:
    rng = random.Random(9)  # noqa: S311
    snap = live_snapshot()
    base = decide(snap)
    for _ in range(30):
        ls, rs = list(snap.launches), list(snap.runs)
        rng.shuffle(ls)
        rng.shuffle(rs)
        d = decide(_replace(snap, launches=tuple(ls), runs=tuple(rs)))
        assert d.verdict is base.verdict and set(d.blockers) == set(base.blockers)


def test_o_resultado_depende_de_seq_e_nao_da_posicao() -> None:
    """Mesma tupla, `seq` trocado: a negativa passa de anterior a posterior e muda o veredito."""
    positives = live_evidence()
    neg_first = ev(ES.BINDING, EB.BINDING_UNPROVEN, subject=BS.CONTROL, seq=0)
    after = _replace(neg_first, seq=10_000)
    released = decide(live_snapshot(evidence=(*positives, neg_first)))
    assert released.verdict is tc.ReleaseVerdict.RELEASE
    assert decide(live_snapshot(evidence=(*positives, after))).verdict is tc.ReleaseVerdict.HOLD


# -------------------------------------------------------------------- contraprovas complementares


@pytest.mark.parametrize("field", ["run_id", "invocation_id"])
def test_correcao_identidade_de_run_nao_pode_repetir_entre_papeis(field: str) -> None:
    rs = list(runs())
    rs[0] = _replace(rs[0], **{field: getattr(rs[1], field)})
    # Para run_id, preserve o elo do controle com a reserva: a única inconsistência
    # é o Developer carregar o mesmo ID que o controle.
    if field == "run_id":
        rs = list(runs())
        rs[1] = _replace(rs[1], run_id=rs[0].run_id)
    snap = live_snapshot(runs=tuple(rs))
    assert decide(snap).verdict is tc.ReleaseVerdict.INCIDENT
    assert BC.RUN_DUPLICATED in codes(decide(snap))
    assert not tc.assess_teardown(snap).proven


@pytest.mark.parametrize("runner_present", [False, True])
def test_correcao_atividade_de_componente_exige_o_run_correspondente(
    runner_present: bool,
) -> None:
    rs = tuple(r for r in runs() if r.role is not tc.RunRole.DEVELOPER)
    if runner_present:
        ls = (launch(1),)
        evid = tuple(live_evidence())
    else:
        rs = control_only_runs()
        ls = (launch(1, tag=PhaseTag.DEVELOPER),)
        evid = tuple(x for x in live_evidence() if x.subject is not BS.TEST_RUNNER)
    snap = live_snapshot(runs=rs, launches=ls, evidence=evid)
    assert decide(snap).verdict is not tc.ReleaseVerdict.RELEASE
    assert not tc.assess_teardown(snap).proven


def test_correcao_timestamp_historico_nao_pode_ser_ocultado_pela_flag() -> None:
    with pytest.raises(ValueError):
        _replace(facts(), first_unproven_at=T0, ever_unproven=False)


@pytest.mark.parametrize(
    "scope,basis",
    [(ES.IN_PROCESS, EB.EXECUTOR_DRAINED), (ES.BINDING, EB.BINDING_RELEASED_OBSERVED)],
)
@pytest.mark.parametrize("field", ["launch_id", "launch_version"])
def test_correcao_evidencia_fora_de_launch_recusa_referencia_parcial(
    scope: ES,
    basis: EB,
    field: str,
) -> None:
    kwargs: dict[str, Any] = {field: 1}
    if scope is ES.BINDING:
        kwargs["subject"] = BS.CONTROL
    with pytest.raises(ValueError):
        tc.EvidenceRecord(1, scope, basis, OWNER, **kwargs)


@pytest.mark.parametrize("bad_status", ["done", "invalid-status", 1, None])
def test_correcao_gate_valida_enums_da_resolucao_aninhada(bad_status: object) -> None:
    # O contrato original não é alterado; sua saída é validada na nova fronteira.
    resolution = TaskResolution(bad_status, None)  # type: ignore[arg-type]
    base = ExecutionClassification(ExecutionCode.SUCCESS, RunStatus.OK, resolution)
    complete = live_snapshot()
    with pytest.raises(TypeError):
        tc.gate_teardown(base, complete)
    with pytest.raises(TypeError):
        tc.FinalizationDecision(ExecutionCode.SUCCESS, RunStatus.OK, resolution, True, False)


def test_correcao_consulta_de_run_recusa_papel_sem_enum() -> None:
    with pytest.raises(TypeError):
        live_snapshot().runs_of("control")  # type: ignore[arg-type]


def test_correcao_gate_valida_failure_reason_da_resolucao_aninhada() -> None:
    resolution = TaskResolution(TaskStatus.FAILED, "internal_error")  # type: ignore[arg-type]
    base = ExecutionClassification(ExecutionCode.INTERNAL_ERROR, RunStatus.ERROR, resolution)
    with pytest.raises(TypeError):
        tc.gate_teardown(base, live_snapshot())
    with pytest.raises(TypeError):
        tc.FinalizationDecision(
            ExecutionCode.INTERNAL_ERROR, RunStatus.ERROR, resolution, True, False
        )


@pytest.mark.parametrize("field", ["reservation", "runs", "launches", "evidence"])
def test_correcao_snapshot_revalida_forma_antes_de_acessar_registros(field: str) -> None:
    forged = live_snapshot()
    object.__setattr__(forged, field, object())
    with pytest.raises(TypeError):
        tc.decide_release(forged, request(live_snapshot()))


# =============================================================================================
# P1-I1-G1 — o gate recebe os FATOS (snapshot) e reavalia; avaliação não é credencial
# =============================================================================================


def _done(decision: tc.FinalizationDecision) -> bool:
    res = decision.task_resolution
    return res is not None and res.status is TaskStatus.DONE


def test_g1_avaliacao_falsificada_por_replace_nao_e_aceita_pelo_gate() -> None:
    """A: snapshot com histórico UNKNOWN; a cópia falsificada da avaliação NÃO autoriza `done`."""
    snap = live_snapshot(history=HistoryCurrency.UNKNOWN)
    honest = tc.assess_teardown(snap)
    assert not honest.proven and honest.completeness is tc.Completeness.INCOMPLETE
    forged = _replace(
        honest,
        resources=TV.PROVEN_CLOSED,
        completeness=tc.Completeness.COMPLETE,
        verdict=TV.PROVEN_CLOSED,
        blockers=(),
    )
    assert forged.proven  # a falsificação é auto-coerente: só o gate de fatos a detém
    with pytest.raises(TypeError):
        tc.gate_teardown(SUCCESS, forged)
    got = tc.gate_teardown(SUCCESS, snap)  # os fatos reais
    assert not _done(got) and got.run_status is RunStatus.ERROR
    assert BC.HISTORY_NOT_PROVEN_CURRENT in {b.code for b in got.blockers}


def test_g1_avaliacao_obsoleta_nao_autoriza_e_version_nao_e_garantia_de_atualidade() -> None:
    """B: a avaliação antiga estava completa; chegou evidência negativa posterior (mesma
    `reservation.version`). O gate, alimentado com os fatos vigentes, recusa."""
    snap = live_snapshot()
    old = tc.assess_teardown(snap)
    assert old.proven and _done(tc.gate_teardown(SUCCESS, snap))
    later_negative = ev(ES.BINDING, EB.BINDING_UNPROVEN, subject=BS.CONTROL)
    current = _replace(snap, evidence=(*snap.evidence, later_negative))
    assert current.reservation.version == snap.reservation.version  # `version` inalterada
    assert tc.assess_teardown(current).proven is False
    got = tc.gate_teardown(SUCCESS, current)
    assert not _done(got) and got.run_status is RunStatus.ERROR
    assert tc.Blocker(BC.BINDING_UNPROVEN, "control") in got.blockers
    with pytest.raises(TypeError):
        tc.gate_teardown(SUCCESS, old)  # type: ignore[arg-type]  # a velha só serve de diagnóstico
    assert old.proven  # o valor antigo continua legível como diagnóstico


def test_g1_cada_mudanca_dos_fatos_muda_o_resultado_do_gate() -> None:
    """Não há cache: o mesmo gate, sobre fatos diferentes, dá resultados diferentes."""
    base_snap = live_snapshot()
    extra = base_snap.evidence
    variants: list[tuple[str, tc.ReleaseSnapshot]] = [
        (
            "drain_timeout posterior",
            _replace(base_snap, evidence=(*extra, ev(ES.IN_PROCESS, EB.DRAIN_TIMEOUT))),
        ),
        (
            "sonda presente",
            _replace(base_snap, evidence=(*extra, ev(ES.LAUNCH, EB.PROBE_PRESENT, lid=1, lver=1))),
        ),
        ("lease unknown", _replace(base_snap, lease=LeaseStatus.UNKNOWN)),
        ("history stale", _replace(base_snap, history=HistoryCurrency.STALE_SUSPECTED)),
        ("sem Run de controle", _replace(base_snap, runs=())),
    ]
    assert _done(tc.gate_teardown(SUCCESS, base_snap))
    for label, snap in variants:
        assert not _done(tc.gate_teardown(SUCCESS, snap)), label
        assert not _done(tc.gate_teardown(TESTS_FAILED, snap)), label


@pytest.mark.parametrize("hist", [HistoryCurrency.UNKNOWN, HistoryCurrency.STALE_SUSPECTED])
def test_g1_historico_unknown_ou_stale_nunca_autoriza_done_nem_needs_fix(
    hist: HistoryCurrency,
) -> None:
    snap = live_snapshot(history=hist)
    for base_cls in (SUCCESS, TESTS_FAILED):
        got = tc.gate_teardown(base_cls, snap)
        assert got.task_resolution == TaskResolution(
            TaskStatus.FAILED, FailureReason.INTERNAL_ERROR
        )
        assert got.code is tc.FinalizationCode.TEARDOWN_UNPROVEN and not got.teardown_proven


@pytest.mark.parametrize("lease_", [LeaseStatus.UNKNOWN, LeaseStatus.INCIDENT])
def test_g1_lease_nao_comprovado_nao_autoriza(lease_: LeaseStatus) -> None:
    got = tc.gate_teardown(SUCCESS, live_snapshot(lease=lease_))
    assert not _done(got) and not got.teardown_proven


def test_g1_caso_legitimo_done_e_needs_fix_continuam_permitidos() -> None:
    snap = live_snapshot()
    done = tc.gate_teardown(SUCCESS, snap)
    assert _done(done) and done.run_status is RunStatus.OK and done.teardown_proven
    assert done.blockers == () and not done.downgraded
    nf = tc.gate_teardown(TESTS_FAILED, snap)
    assert nf.task_resolution == TESTS_FAILED.task_resolution and nf.teardown_proven
    # adotado, com quiescência completa e ack: também comprova
    assert tc.gate_teardown(SUCCESS, adopted_snapshot()).teardown_proven


def test_g1_cancelamento_e_integridade_preservados_com_fatos_incompletos() -> None:
    incomplete = live_snapshot(history=HistoryCurrency.UNKNOWN)
    kept = tc.gate_teardown(CANCELLED_OK, incomplete)
    assert kept.task_resolution is None and kept.run_status is RunStatus.ERROR
    cancelled = tc.gate_teardown(CANCELLED, incomplete)
    assert (cancelled.code, cancelled.run_status, cancelled.task_resolution) == (
        ExecutionCode.CANCELLED,
        RunStatus.CANCELLED,
        None,
    )
    grave = tc.gate_teardown(BLOCKED, incomplete)
    assert grave.code is ExecutionCode.INTEGRITY_VIOLATED
    assert grave.task_resolution == BLOCKED.task_resolution and not grave.downgraded
    assert grave.blockers  # a pendência de teardown segue visível como diagnóstico


def test_g1_gate_rejeita_qualquer_objeto_que_nao_seja_o_snapshot() -> None:
    snap = live_snapshot()
    not_snapshots: list[object] = [
        tc.assess_teardown(snap),
        _replace(tc.assess_teardown(snap)),
        tc.classify_resources(snap),
        tc.TeardownVerdict.PROVEN_CLOSED,
        tc.Completeness.COMPLETE,
        True,
        False,
        None,
        "proven_closed",
        0,
        snap.reservation,
        snap.runs,
        (snap,),
        {"snapshot": snap},
    ]
    for bad in not_snapshots:
        with pytest.raises(TypeError):
            tc.gate_teardown(SUCCESS, bad)  # type: ignore[arg-type]
    bad_bases: list[object] = [
        None,
        "success",
        SUCCESS.task_resolution,
        tc.FinalizationCode.TEARDOWN_UNPROVEN,
    ]
    for bad_base in bad_bases:
        with pytest.raises(TypeError):
            tc.gate_teardown(bad_base, snap)  # type: ignore[arg-type]


def test_g1_gate_revalida_snapshot_montado_contornando_o_construtor() -> None:
    snap = live_snapshot()
    forged = _replace(snap)
    object.__setattr__(forged, "history", "proven_current")
    with pytest.raises(TypeError):
        tc.gate_teardown(SUCCESS, forged)


def test_g1_gate_nao_depende_do_status_dos_runs_nem_exige_run_fechado() -> None:
    """Sem dependência circular: o teardown é avaliado com o Run de controle ainda `running`."""
    snap = live_snapshot(runs=runs(RunStatus.RUNNING))
    got = tc.gate_teardown(SUCCESS, snap)
    assert got.teardown_proven and _done(got)
    assert decide(snap).verdict is tc.ReleaseVerdict.HOLD  # a liberação, essa sim, espera o Run


def test_g1_avaliacao_antiga_serve_so_como_diagnostico() -> None:
    snap = live_snapshot(history=HistoryCurrency.UNKNOWN)
    a = tc.assess_teardown(snap)
    assert a.blockers and not a.proven
    # diagnóstico estável: a mesma avaliação mostra os mesmos bloqueadores que o gate reporta
    assert set(a.blockers) == set(tc.gate_teardown(SUCCESS, snap).blockers)


def test_g1_decisao_final_coerente_com_bloqueadores() -> None:
    with pytest.raises(ValueError):
        tc.FinalizationDecision(
            ExecutionCode.SUCCESS,
            RunStatus.OK,
            SUCCESS.task_resolution,
            True,
            False,
            (tc.Blocker(BC.LEASE_UNKNOWN),),
        )
    with pytest.raises(TypeError):
        tc.FinalizationDecision(
            ExecutionCode.CANCELLED,
            RunStatus.CANCELLED,
            None,
            False,
            False,
            ["x"],  # type: ignore[arg-type]
        )
