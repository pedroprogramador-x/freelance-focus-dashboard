"""E8.4.5 / Entrega A — controle de lançamentos: observer SQLite, contexto declarado, drenagem
do executor e evidência R3/R4.

Banco SQLite **migrado de verdade** (descartável, em `tmp_path`); processos reais
(`sys.executable`) onde importa provar que **nada nasce** depois da fronteira. Os cenários de
concorrência usam `Event`/`Barrier`: nenhum depende de `sleep` para ser correto (o `join(timeout)`
curto só afirma que uma thread **continua bloqueada**).

Nada aqui habilita execução operacional: nenhum teste chama `admit_execution` fora do apoio de
teste, nenhuma rota é usada e nenhum código de produção instala escopo ou observer.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings
from app.db.models import WorkspaceTask
from app.orchestrator import cancel
from app.orchestrator import teardown_contract as tc
from app.orchestrator.developer_invocation_contract import BindingOutcome
from app.orchestrator.drain import DrainCoordinator, DrainStatus, GateRegistry
from app.orchestrator.evidence_ledger import (
    BindingObservation,
    EvidenceRefused,
    EvidenceWriter,
    observation_of,
)
from app.orchestrator.execution_manager import cancel_and_drain
from app.orchestrator.executor_drain import (
    DrainOutcome,
    ExecutorClosed,
    InProcessExecutor,
    drain_with_evidence,
)
from app.orchestrator.launch_ledger import LaunchRefused, SqliteLaunchObserver
from app.process_runtime import ProcessOutcome, ProcessResult, run_supervised
from app.process_runtime.launch import (
    BindingConflict,
    LaunchGate,
    LaunchIntent,
    LaunchPhaseTag,
    LaunchPlatform,
    LaunchScope,
    LockOrderViolation,
    LockRank,
    bind_launch_context,
    current_binding,
    current_scope,
    install_scope,
    ranked,
)
from tests.admission_support_e8_4_1 import admit, approved_task
from tests.execution_reservation_support_e8_4_5 import Db
from tests.reservation_support_e8_4_5 import DEFAULT_IDENTITY, release_for_test
from tests.test_launch_observer_e8_4_5 import SENTINEL_SCRIPT, make_spec, os_process

OWNER = DEFAULT_IDENTITY.identity.owner_token
WAIT = 15.0
KEY = "exec-key-a"

WAIT_FOR_FILE = (
    "import os, sys, time\nwhile not os.path.exists(sys.argv[1]):\n    time.sleep(0.02)\n"
)


# ------------------------------------------------------------------------------------ apoio


@pytest.fixture
def rdb(temp_settings: AppSettings, migrated_url: str) -> Iterator[Db]:
    del migrated_url
    db = Db(Path(temp_settings.sqlalchemy_url.split("///", 1)[1]))
    try:
        yield db
    finally:
        db.close()


class Pausing:
    """Observer que delega ao real e pausa/falha em pontos nomeados (determinístico)."""

    def __init__(self, inner: SqliteLaunchObserver) -> None:
        self.inner = inner
        self.before_intent: tuple[threading.Event, threading.Event] | None = None
        self.after_intent: tuple[threading.Event, threading.Event] | None = None
        self.fail_created = False

    @staticmethod
    def _pause(pair: tuple[threading.Event, threading.Event]) -> None:
        reached, proceed = pair
        reached.set()
        assert proceed.wait(WAIT), "pausa nunca liberada"

    def intent(self, intent: Any) -> object:
        if self.before_intent is not None:
            self._pause(self.before_intent)
        handle = self.inner.intent(intent)
        if self.after_intent is not None:
            self._pause(self.after_intent)
        return handle

    def created(self, handle: object, identity: Any) -> None:
        if self.fail_created:
            raise RuntimeError("falha injetada em created")
        self.inner.created(handle, identity)

    def contained(self, handle: object) -> None:
        self.inner.contained(handle)

    def ended(self, handle: object, end: Any) -> None:
        self.inner.ended(handle, end)


class Rig:
    """Uma task admitida (`executing`) com a sua reserva `active` e um escopo da reserva."""

    def __init__(
        self,
        session: Session,
        factory: sessionmaker[Session],
        db: Db,
        task: WorkspaceTask,
        control_run_id: str,
    ) -> None:
        self.session = session
        self.factory = factory
        self.db = db
        self.task = task
        self.control_run_id = control_run_id
        row = db.one(
            "SELECT id FROM execution_reservation WHERE control_run_id = ?", (control_run_id,)
        )
        assert row is not None
        self.rid: str = row[0]
        self.observer = SqliteLaunchObserver(factory, reservation_id=self.rid, owner_token=OWNER)
        self.pausing = Pausing(self.observer)
        self.gate = LaunchGate()
        self.scope = LaunchScope(
            self.pausing, self.gate, enter_timeout_s=5.0, reservation_id=self.rid
        )
        self.registry = GateRegistry()
        self.registry.register(control_run_id, self.gate)
        self.coordinator = DrainCoordinator(self.registry, factory, gate_timeout_s=0.4)
        self.writer = EvidenceWriter(factory, reservation_id=self.rid, writer_token=OWNER)

    # ---- leitura
    def table(self, name: str, order: str) -> list[dict[str, Any]]:
        cursor = self.db.run(
            f"SELECT * FROM {name} WHERE reservation_id = ? ORDER BY {order}",  # noqa: S608
            (self.rid,),
        )
        names = [c[0] for c in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def launches(self) -> list[dict[str, Any]]:
        return self.table("execution_launch", "id")

    def evidence(self) -> list[dict[str, Any]]:
        return self.table("execution_evidence", "seq")

    def reservation(self) -> dict[str, Any]:
        cursor = self.db.run("SELECT * FROM execution_reservation WHERE id = ?", (self.rid,))
        names = [c[0] for c in cursor.description]
        return dict(zip(names, cursor.fetchone(), strict=True))

    # ---- ações
    def intent(self, tag: LaunchPhaseTag | None = LaunchPhaseTag.PREPARATION, **kw: Any) -> Any:
        fields: dict[str, Any] = {
            "exe_name": "python.exe",
            "platform": LaunchPlatform.WIN32 if sys.platform == "win32" else LaunchPlatform.POSIX,
            "profile": "win_job_v1" if sys.platform == "win32" else "posix_pgrp_v1",
            "phase_tag": tag,
            "reservation_id": self.rid,
        }
        fields.update(kw)
        return self.observer.intent(LaunchIntent(**fields))

    def drain(self) -> Any:
        self.session.commit()
        self.session.expire_all()
        return cancel_and_drain(self.session, self.task.id, drain=self.coordinator)

    def snapshot(self) -> tc.ReleaseSnapshot:
        return self.db.snapshot(self.rid)

    def launch(
        self,
        tmp_path: Path,
        script: str = SENTINEL_SCRIPT,
        *args: str,
        tag: LaunchPhaseTag | None = LaunchPhaseTag.PREPARATION,
    ) -> ProcessResult:
        spec = make_spec(tmp_path, script, *args)
        with (
            install_scope(self.scope),
            bind_launch_context(self.rid, tag or LaunchPhaseTag.PREPARATION),
        ):
            return run_supervised(spec, lambda: False)


@pytest.fixture
def rig(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: Any,
    tmp_path: Path,
    rdb: Db,
) -> Rig:
    artifacts = tmp_path / "artifacts"
    task = approved_task(session, workspace, artifacts)
    admission = admit(session, task.id, artifacts, key=KEY)
    assert admission.run is not None
    session.commit()
    return Rig(session, session_factory, rdb, task, admission.run.id)


def blocker_codes(snapshot: tc.ReleaseSnapshot) -> list[tuple[str, str | None]]:
    return [(b.code.value, b.subject) for b in tc.classify_resources(snapshot).blockers]


def launch_blockers(snapshot: tc.ReleaseSnapshot) -> list[tuple[str, str | None]]:
    return [(c, s) for c, s in blocker_codes(snapshot) if c.startswith("launch_")]


def sentinel(tmp_path: Path, name: str = "ran.txt") -> Path:
    return tmp_path / name


# ------------------------------------------------------------- contexto declarado (launch.py)


def test_o_contexto_declara_reserva_e_fase_e_e_restaurado() -> None:
    assert current_binding() is None
    with bind_launch_context("r1", LaunchPhaseTag.PREPARATION) as outer:
        assert current_binding() == outer
        with bind_launch_context("r1", LaunchPhaseTag.DEVELOPER):  # muda de fase, mesma reserva
            assert current_binding() is not None
            assert current_binding().phase_tag is LaunchPhaseTag.DEVELOPER  # type: ignore[union-attr]
        assert current_binding() == outer
    assert current_binding() is None


def test_ligar_o_contexto_a_outra_reserva_levanta_e_nao_deixa_residuo() -> None:
    with (
        bind_launch_context("r1", LaunchPhaseTag.PREPARATION),
        pytest.raises(BindingConflict),
        bind_launch_context("r2", LaunchPhaseTag.PREPARATION),
    ):
        raise AssertionError("não deveria entrar")
    assert current_binding() is None


def test_nao_existe_other_nem_fase_por_omissao() -> None:
    assert {t.value for t in LaunchPhaseTag} == {"preparation", "developer", "test_runner"}
    assert {t.value for t in LaunchPhaseTag} <= {t.value for t in tc.PhaseTag}
    with pytest.raises(TypeError), bind_launch_context("r1", "preparation"):  # type: ignore[arg-type]
        raise AssertionError("não deveria entrar")
    with pytest.raises(TypeError):
        LaunchIntent("x", LaunchPlatform.POSIX, "posix_pgrp_v1", phase_tag="other")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        LaunchScope(Pausing, LaunchGate(), reservation_id="")  # type: ignore[arg-type]


# ------------------------------------------------------------------------ o ciclo completo


@os_process
def test_um_lancamento_real_grava_o_ciclo_inteiro_no_ledger(rig: Rig, tmp_path: Path) -> None:
    ran = sentinel(tmp_path)

    result = rig.launch(tmp_path, SENTINEL_SCRIPT, str(ran))

    assert result.outcome is ProcessOutcome.EXITED and ran.exists()
    [row] = rig.launches()
    assert row["phase_tag"] == "preparation"
    assert row["phase"] == "ended_confirmed" and row["end_basis"] == "tree_confirmed_dead"
    assert row["version"] == 4  # intent → created → contained → ended
    assert row["pid"] is not None and row["start_token"]
    assert row["ended_by_token"] == OWNER
    assert row["platform"] == ("win32" if sys.platform == "win32" else "posix")
    assert launch_blockers(rig.snapshot()) == []  # o recurso `launch` está comprovado fechado
    assert current_scope() is None  # nenhum escopo vaza


@os_process
def test_o_desfecho_do_supervisor_e_o_que_o_ledger_registra_quando_o_registro_falha(
    rig: Rig, tmp_path: Path
) -> None:
    """`created` falha depois de o processo nascer: o ledger fecha como `ledger_write_failed`, e o
    contrato **não** dá o lançamento como encerrado (fail closed)."""
    rig.pausing.fail_created = True
    ran = sentinel(tmp_path)

    result = rig.launch(tmp_path, SENTINEL_SCRIPT, str(ran))

    assert result.outcome is ProcessOutcome.SUPERVISION_FAILED
    [row] = rig.launches()
    assert row["phase"] == "ended_unconfirmed" and row["end_basis"] == "ledger_write_failed"
    assert row["pid"] is None and row["ended_by_token"] == OWNER
    assert ("launch_unresolved", f"launch:{row['id']}") in launch_blockers(rig.snapshot())


# ------------------------------------------------- contexto, reserva e thread (isolamento)


@os_process
def test_sem_contexto_declarado_o_escopo_da_reserva_recusa_antes_de_tudo(
    rig: Rig, tmp_path: Path
) -> None:
    ran = sentinel(tmp_path)
    spec = make_spec(tmp_path, SENTINEL_SCRIPT, str(ran))

    with install_scope(rig.scope):
        result = run_supervised(spec, lambda: False)

    assert result.outcome is ProcessOutcome.SUPERVISION_FAILED
    assert result.detail == "launch_refused:scope_mismatch"
    assert not ran.exists() and rig.launches() == []


@os_process
def test_contexto_de_outra_reserva_e_recusado_sem_intent_e_sem_processo(
    rig: Rig, tmp_path: Path
) -> None:
    ran = sentinel(tmp_path)
    spec = make_spec(tmp_path, SENTINEL_SCRIPT, str(ran))

    with install_scope(rig.scope), bind_launch_context("outra-reserva", LaunchPhaseTag.PREPARATION):
        result = run_supervised(spec, lambda: False)

    assert result.detail == "launch_refused:scope_mismatch"
    assert not ran.exists() and rig.launches() == []


@os_process
def test_o_contexto_e_da_thread_e_a_thread_que_o_declara_lanca(rig: Rig, tmp_path: Path) -> None:
    results: dict[str, ProcessResult] = {}
    ran_raw, ran_bound = sentinel(tmp_path, "raw.txt"), sentinel(tmp_path, "bound.txt")

    def work(label: str, ran: Path, *, bind: bool) -> None:
        spec = make_spec(tmp_path, SENTINEL_SCRIPT, str(ran))
        if bind:
            with bind_launch_context(rig.rid, LaunchPhaseTag.PREPARATION):
                results[label] = run_supervised(spec, lambda: False)
        else:
            results[label] = run_supervised(spec, lambda: False)

    with install_scope(rig.scope), bind_launch_context(rig.rid, LaunchPhaseTag.PREPARATION):
        raw = threading.Thread(target=work, args=("raw", ran_raw), kwargs={"bind": False})
        raw.start()
        raw.join(WAIT)
        bound = threading.Thread(target=work, args=("bound", ran_bound), kwargs={"bind": True})
        bound.start()
        bound.join(WAIT)

    # o contexto da thread chamadora NÃO é herdado pela filha; a que declara o próprio lança
    assert results["raw"].detail == "launch_refused:scope_mismatch" and not ran_raw.exists()
    assert results["bound"].outcome is ProcessOutcome.EXITED and ran_bound.exists()
    assert len(rig.launches()) == 1  # só a thread com o contexto deixou rastro


def test_o_observer_recusa_o_intent_de_outra_reserva_sem_escrever(rig: Rig) -> None:
    with pytest.raises(LaunchRefused) as refused:
        rig.intent(reservation_id="outra-reserva")
    assert refused.value.reason == "reservation_mismatch"
    with pytest.raises(LaunchRefused) as legacy:  # contexto ausente: o intent não traz a reserva
        rig.observer.intent(
            SimpleNamespace(
                exe_name="x",
                platform=LaunchPlatform.POSIX,
                profile="posix_pgrp_v1",
                phase_tag=LaunchPhaseTag.PREPARATION,
                reservation_id=None,
            )
        )
    assert legacy.value.reason == "reservation_mismatch"
    assert rig.launches() == []


def test_o_observer_so_escreve_como_o_dono_da_reserva(rig: Rig, session_factory: Any) -> None:
    impostor = SqliteLaunchObserver(session_factory, reservation_id=rig.rid, owner_token="impostor")
    with pytest.raises(LaunchRefused) as refused:
        impostor.intent(
            LaunchIntent(
                "x",
                LaunchPlatform.POSIX,
                "posix_pgrp_v1",
                phase_tag=LaunchPhaseTag.PREPARATION,
                reservation_id=rig.rid,
            )
        )
    assert refused.value.reason == "writer_not_owner" and rig.launches() == []
    ausente = SqliteLaunchObserver(session_factory, reservation_id="nao-existe", owner_token=OWNER)
    with pytest.raises(LaunchRefused) as missing:
        ausente.intent(
            LaunchIntent(
                "x",
                LaunchPlatform.POSIX,
                "posix_pgrp_v1",
                phase_tag=LaunchPhaseTag.PREPARATION,
                reservation_id="nao-existe",
            )
        )
    assert missing.value.reason == "reservation_missing"


def test_handle_de_outra_reserva_e_recusado_e_nao_toca_a_linha(rig: Rig) -> None:
    handle = rig.intent()
    foreign = SimpleNamespace(launch_id=handle.launch_id, reservation_id="outra")
    with pytest.raises(LaunchRefused) as refused:
        rig.observer.contained(foreign)
    assert refused.value.reason == "handle_foreign"
    [row] = rig.launches()
    assert row["phase"] == "intent" and row["version"] == 1


def test_duas_reservas_nao_se_misturam(rig: Rig, rdb: Db, session_factory: Any) -> None:
    """A reserva A é liberada (pelos triggers) e a B nasce; o observer de A não escreve em B."""
    release_for_test(Path(rdb.path), rig.task.id)
    cycle_b = rdb.reserve(owner_token="token-b", logical_key="lb", physical_key="pb")
    observer_b = SqliteLaunchObserver(
        session_factory, reservation_id=cycle_b.reservation_id, owner_token="token-b"
    )
    handle_b = observer_b.intent(
        LaunchIntent(
            "x",
            LaunchPlatform.POSIX,
            "posix_pgrp_v1",
            phase_tag=LaunchPhaseTag.PREPARATION,
            reservation_id=cycle_b.reservation_id,
        )
    )
    with pytest.raises(LaunchRefused) as foreign:  # handle de B no observer de A
        rig.observer.contained(handle_b)
    assert foreign.value.reason == "handle_foreign"
    with pytest.raises(LaunchRefused) as mismatch:  # intent de B no observer de A
        rig.observer.intent(
            LaunchIntent(
                "x",
                LaunchPlatform.POSIX,
                "posix_pgrp_v1",
                phase_tag=LaunchPhaseTag.PREPARATION,
                reservation_id=cycle_b.reservation_id,
            )
        )
    assert mismatch.value.reason == "reservation_mismatch"
    assert rig.launches() == []  # nada de A; a linha de B não foi alterada
    assert (
        rdb.scalar(
            "SELECT phase FROM execution_launch WHERE reservation_id = ?", (cycle_b.reservation_id,)
        )
        == "intent"
    )


# ------------------------------------------------------------------ phase_tag explícito


def test_intent_sem_fase_declarada_e_recusado_e_nunca_vira_other(rig: Rig) -> None:
    with pytest.raises(LaunchRefused) as refused:
        rig.intent(tag=None)
    assert refused.value.reason == "phase_tag_missing"
    forged = SimpleNamespace(
        exe_name="x",
        platform=LaunchPlatform.POSIX,
        profile="posix_pgrp_v1",
        phase_tag=SimpleNamespace(value="other"),
        reservation_id=rig.rid,
    )
    with pytest.raises(LaunchRefused) as other:
        rig.observer.intent(forged)
    assert other.value.reason == "phase_tag_missing"
    assert rig.launches() == []


@pytest.mark.parametrize("tag", [LaunchPhaseTag.DEVELOPER, LaunchPhaseTag.TEST_RUNNER])
def test_fase_do_developer_ou_do_runner_exige_o_developer_declarado(
    rig: Rig, rdb: Db, tag: LaunchPhaseTag
) -> None:
    with pytest.raises(LaunchRefused) as refused:
        rig.intent(tag=tag)
    assert refused.value.reason == "developer_not_declared" and rig.launches() == []

    rdb.declare_developer(rig.rid)

    handle = rig.intent(tag=tag)
    [row] = rig.launches()
    assert row["id"] == handle.launch_id and row["phase_tag"] == tag.value


# ---------------------------------------------------- nenhum lançamento depois da drenagem


@os_process
def test_depois_do_pedido_duravel_de_drenagem_nenhum_intent_novo(rig: Rig, tmp_path: Path) -> None:
    cancel(rig.session, rig.task.id)  # tx1 sem coordinator: só o pedido durável
    rig.session.commit()
    assert rig.reservation()["drain_requested_at"] is not None
    assert rig.reservation()["state"] == "active"
    ran = sentinel(tmp_path)

    result = rig.launch(tmp_path, SENTINEL_SCRIPT, str(ran))

    assert result.detail == "launch_refused:intent_failed"
    assert not ran.exists() and rig.launches() == []
    with pytest.raises(LaunchRefused) as refused:
        rig.intent()
    assert refused.value.reason == "drain_requested"


def test_o_trigger_continua_o_arbitro_final_mesmo_sem_o_observer(rig: Rig, rdb: Db) -> None:
    cancel(rig.session, rig.task.id)
    rig.session.commit()

    message = rdb.refused(
        "INSERT INTO execution_launch (reservation_id, phase_tag, exe_name, platform,"
        " containment_profile, phase, version, created_at)"
        " VALUES (?, 'preparation', 'x', 'posix', 'posix_pgrp_v1', 'intent', 1,"
        " '2026-01-01 00:00:00.000000')",
        (rig.rid,),
    )

    assert "launch_insert:drain_requested" in message


@os_process
def test_cancel_depois_do_intent_persistido_recusa_antes_do_popen(rig: Rig, tmp_path: Path) -> None:
    """Interleaving 1: o `intent` já foi persistido (gate tomado) quando o cancelamento chega. O
    gate não fecha no prazo (`GATE_TIMEOUT`), mas o `stop` vale: recusa **antes** do `Popen`."""
    reached, proceed = threading.Event(), threading.Event()
    rig.pausing.after_intent = (reached, proceed)
    ran = sentinel(tmp_path)
    box: list[ProcessResult] = []
    launcher = threading.Thread(
        target=lambda: box.append(rig.launch(tmp_path, SENTINEL_SCRIPT, str(ran)))
    )
    launcher.start()
    assert reached.wait(WAIT)
    assert [r["phase"] for r in rig.launches()] == ["intent"]

    outcome = rig.drain()  # request_stop → tx1 → gate ocupado ⇒ GATE_TIMEOUT

    assert outcome.drain.status is DrainStatus.GATE_TIMEOUT
    assert launcher.is_alive()  # continua dentro da seção protegida
    proceed.set()
    launcher.join(WAIT)
    assert not launcher.is_alive()
    [result] = box
    assert result.outcome is ProcessOutcome.CANCELLED and result.detail == "launch_refused:stopped"
    assert not ran.exists()
    [row] = rig.launches()
    assert row["phase"] == "ended_confirmed" and row["end_basis"] == "refused_before_creation"
    assert row["pid"] is None and row["ended_by_token"] == OWNER
    assert rig.reservation()["state"] == "active"  # nada foi linearizado
    assert launch_blockers(rig.snapshot()) == []


@os_process
def test_cancel_durante_a_seção_antes_do_intent_nao_deixa_linha_nem_processo(
    rig: Rig, tmp_path: Path
) -> None:
    """Interleaving 2: o lançador já tem o gate mas ainda não persistiu o `intent`. O pedido durável
    chega primeiro: o observer recusa e o trigger também recusaria. Zero linhas, zero processos."""
    reached, proceed = threading.Event(), threading.Event()
    rig.pausing.before_intent = (reached, proceed)
    ran = sentinel(tmp_path)
    box: list[ProcessResult] = []
    launcher = threading.Thread(
        target=lambda: box.append(rig.launch(tmp_path, SENTINEL_SCRIPT, str(ran)))
    )
    launcher.start()
    assert reached.wait(WAIT)

    outcome = rig.drain()

    assert outcome.drain.status is DrainStatus.GATE_TIMEOUT
    proceed.set()
    launcher.join(WAIT)
    [result] = box
    assert result.detail == "launch_refused:intent_failed"
    assert not ran.exists() and rig.launches() == []


@os_process
def test_cancel_linearizado_antes_do_lancamento_recusa_sem_intent(rig: Rig, tmp_path: Path) -> None:
    """Interleaving 3: o fechamento é linearizado (L) antes de o lançador chegar ao gate."""
    go = threading.Event()
    ran = sentinel(tmp_path)
    box: list[ProcessResult] = []

    def launcher() -> None:
        assert go.wait(WAIT)
        box.append(rig.launch(tmp_path, SENTINEL_SCRIPT, str(ran)))

    thread = threading.Thread(target=launcher)
    thread.start()

    outcome = rig.drain()
    assert outcome.drain.status is DrainStatus.DRAINING_COMMITTED
    assert rig.reservation()["state"] == "draining"
    go.set()
    thread.join(WAIT)

    [result] = box
    assert result.outcome is ProcessOutcome.CANCELLED and result.detail == "launch_refused:stopped"
    assert not ran.exists() and rig.launches() == []


@os_process
def test_o_lancamento_em_voo_termina_e_e_registrado_durante_o_draining(
    rig: Rig, tmp_path: Path
) -> None:
    """O fechamento não prende o gate à vida do processo: um lançamento que já passou da fronteira
    continua e o seu término é gravado com a reserva em `draining`."""
    go_file = tmp_path / "go"
    box: list[ProcessResult] = []
    thread = threading.Thread(
        target=lambda: box.append(rig.launch(tmp_path, WAIT_FOR_FILE, str(go_file)))
    )
    thread.start()
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        rows = rig.launches()
        if rows and rows[0]["phase"] == "contained":
            break
        time.sleep(0.02)
    assert rig.launches()[0]["phase"] == "contained"

    outcome = rig.drain()
    assert outcome.drain.status is DrainStatus.DRAINING_COMMITTED
    go_file.write_text("go")
    thread.join(WAIT)

    [result] = box
    assert result.outcome is ProcessOutcome.EXITED
    [row] = rig.launches()
    assert row["phase"] == "ended_confirmed" and row["end_basis"] == "tree_confirmed_dead"
    assert rig.reservation()["state"] == "draining"
    with pytest.raises(LaunchRefused):  # e nenhum lançamento NOVO depois disso
        rig.intent()


# ------------------------------------------------------ escritas do observer: CAS e proveniência


def test_duas_escritas_concorrentes_do_mesmo_passo_so_uma_vence(rig: Rig) -> None:
    handle = rig.intent()
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    identity = SimpleNamespace(pid=4242, start_token="tok")

    def attempt() -> None:
        barrier.wait(WAIT)
        try:
            rig.observer.created(handle, identity)
            outcomes.append("ok")
        except Exception as error:
            outcomes.append(type(error).__name__)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(WAIT)

    assert outcomes.count("ok") == 1 and len(outcomes) == 2
    [row] = rig.launches()
    assert row["phase"] == "created" and row["version"] == 2 and row["pid"] == 4242


def test_o_ledger_recusa_arestas_ilegais_e_terminal_imutavel(rig: Rig) -> None:
    handle = rig.intent()
    end = SimpleNamespace(value="tree_confirmed_dead")
    with pytest.raises(LaunchRefused) as no_process:  # término com árvore sem processo criado
        rig.observer.ended(handle, end)
    assert no_process.value.reason == "edge_illegal"
    with pytest.raises(LaunchRefused) as unknown:
        rig.observer.ended(handle, SimpleNamespace(value="inventado"))
    assert unknown.value.reason == "end_basis_unknown"

    rig.observer.ended(handle, SimpleNamespace(value="refused_before_creation"))

    with pytest.raises(LaunchRefused) as terminal:
        rig.observer.ended(handle, SimpleNamespace(value="ledger_write_failed"))
    assert terminal.value.reason == "terminal_immutable"
    with pytest.raises(LaunchRefused) as stale:
        rig.observer.created(handle, SimpleNamespace(pid=1, start_token="t"))
    assert stale.value.reason == "terminal_immutable"
    [row] = rig.launches()
    assert row["phase"] == "ended_confirmed" and row["version"] == 2


def test_depois_da_adocao_o_dono_nao_escreve_mais(rig: Rig, rdb: Db) -> None:
    handle = rig.intent()
    rdb.to_teardown_pending(rig.rid)
    rdb.adopt(rig.rid)

    with pytest.raises(LaunchRefused) as created:
        rig.observer.created(handle, SimpleNamespace(pid=1, start_token="t"))
    with pytest.raises(LaunchRefused) as new_intent:
        rig.intent()

    assert created.value.reason == "reservation_adopted"
    assert new_intent.value.reason == "reservation_adopted"
    [row] = rig.launches()
    assert row["phase"] == "intent" and row["version"] == 1


def test_fatos_de_fase_vem_do_banco_e_nao_do_handle(rig: Rig, rdb: Db) -> None:
    """Escrita anterior commitada sem o chamador saber: o término respeita a linha real."""
    handle = rig.intent()
    rdb.created(handle.launch_id)  # a linha real já está em `created`, o handle "não sabe"

    rig.observer.ended(handle, SimpleNamespace(value="ledger_write_failed"))

    [row] = rig.launches()
    assert row["phase"] == "ended_unconfirmed" and row["end_basis"] == "ledger_write_failed"
    assert row["pid"] == 4242  # o pid que já estava na linha não é apagado


# ------------------------------------------------------------- drenagem do executor (R3)


def _executor() -> InProcessExecutor:
    return InProcessExecutor(rank=lambda: ranked(LockRank.EXECUTOR))


def test_executor_sem_operacao_drena_e_fecha_a_admissao() -> None:
    executor = _executor()
    with executor.operation():
        assert executor.in_flight == 1
    open_before = not executor.closed
    assert executor.in_flight == 0 and open_before

    assert executor.drain(1.0) is DrainOutcome.DRAINED

    assert executor.closed is True
    with pytest.raises(ExecutorClosed), executor.operation():
        raise AssertionError("não deveria entrar")


def test_operacao_em_voo_gera_timeout_e_a_drenagem_posterior_conclui() -> None:
    executor = _executor()
    inside, release = threading.Event(), threading.Event()

    def work() -> None:
        with executor.operation():
            inside.set()
            assert release.wait(WAIT)

    thread = threading.Thread(target=work)
    thread.start()
    assert inside.wait(WAIT)

    assert executor.drain(0.2) is DrainOutcome.TIMEOUT
    assert executor.closed and executor.in_flight == 1
    release.set()
    thread.join(WAIT)

    assert executor.drain(WAIT) is DrainOutcome.DRAINED


def test_drenagem_pedida_de_dentro_de_uma_operacao_e_reentrante_e_nao_espera() -> None:
    executor = _executor()
    started = time.monotonic()

    with executor.operation():
        outcome = executor.drain(30.0)

    assert outcome is DrainOutcome.REENTRANT
    assert time.monotonic() - started < 5.0  # não esperou o prazo


def test_o_executor_respeita_a_ordem_de_locks() -> None:
    executor = _executor()
    with ranked(LockRank.GATE):
        with pytest.raises(LockOrderViolation), executor.operation():
            raise AssertionError("não deveria entrar")
        with pytest.raises(LockOrderViolation):
            executor.drain(1.0)
    with ranked(LockRank.SQLITE), pytest.raises(LockOrderViolation):
        executor.drain(1.0)
    assert executor.in_flight == 0


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), True, "1", None])
def test_prazo_invalido_da_drenagem_levanta(bad: Any) -> None:
    with pytest.raises(ValueError):
        _executor().drain(bad)


# ---------------------------------------------------------------- evidência R3 / R4 (proveniência)


def developer_present(rig: Rig) -> None:
    rig.db.declare_developer(rig.rid)


def test_executor_drenado_e_gravado_como_o_dono_e_fecha_o_r3(rig: Rig) -> None:
    developer_present(rig)
    assert ("drain_unproven", "in_process") in blocker_codes(rig.snapshot())

    record = drain_with_evidence(_executor(), rig.writer, 1.0)

    assert record.outcome is DrainOutcome.DRAINED
    [row] = rig.evidence()
    assert row["seq"] == record.evidence_seq
    assert (row["scope"], row["basis"], row["writer_token"]) == (
        "in_process",
        "executor_drained",
        OWNER,
    )
    assert row["subject"] is None and row["launch_id"] is None
    assert ("drain_unproven", "in_process") not in blocker_codes(rig.snapshot())


def test_negativa_posterior_anula_a_positiva_anterior_e_a_ordem_do_seq_decide(rig: Rig) -> None:
    developer_present(rig)
    first = rig.writer.executor_drained()
    second = rig.writer.drain_timeout()
    assert second > first
    assert ("drain_unproven", "in_process") in blocker_codes(rig.snapshot())
    assert tc.classify_resources(rig.snapshot()).verdict is tc.TeardownVerdict.STILL_ACTIVE

    third = rig.writer.executor_drained()  # a drenagem posterior concluiu

    assert third > second
    assert ("drain_unproven", "in_process") not in blocker_codes(rig.snapshot())


def test_drenagem_reentrante_e_negativa_e_bloqueia_o_r3(rig: Rig) -> None:
    developer_present(rig)
    executor = _executor()

    with executor.operation():
        record = drain_with_evidence(executor, rig.writer, 30.0)

    assert record.outcome is DrainOutcome.REENTRANT
    [row] = rig.evidence()
    assert row["basis"] == "drain_reentrant"
    assert ("drain_unproven", "in_process") in blocker_codes(rig.snapshot())
    assert tc.classify_resources(rig.snapshot()).verdict is tc.TeardownVerdict.TECHNICAL_FAILURE


def test_timeout_da_drenagem_grava_a_negativa_e_nao_a_positiva(rig: Rig) -> None:
    executor = _executor()
    inside, release = threading.Event(), threading.Event()

    def work() -> None:
        with executor.operation():
            inside.set()
            release.wait(WAIT)

    thread = threading.Thread(target=work)
    thread.start()
    assert inside.wait(WAIT)

    record = drain_with_evidence(executor, rig.writer, 0.2)
    release.set()
    thread.join(WAIT)

    assert record.outcome is DrainOutcome.TIMEOUT
    assert [r["basis"] for r in rig.evidence()] == ["drain_timeout"]


def test_falha_ao_gravar_a_evidencia_sobe_e_o_desfecho_nao_vale_como_prova(
    rig: Rig, session_factory: Any
) -> None:
    impostor = EvidenceWriter(session_factory, reservation_id=rig.rid, writer_token="impostor")

    with pytest.raises(EvidenceRefused) as refused:
        drain_with_evidence(_executor(), impostor, 1.0)

    assert refused.value.reason == "writer_not_owner"
    assert rig.evidence() == []


@pytest.mark.parametrize(
    ("outcome", "basis", "subject"),
    [
        (BindingOutcome.RELEASED, "binding_released_observed", "developer"),
        (BindingOutcome.REVOKED, "binding_revoked_observed", "developer"),
        (BindingOutcome.RELEASE_FAILED, "binding_unproven", "developer"),
    ],
)
def test_o_desfecho_do_binding_do_developer_vira_a_evidencia_exata(
    rig: Rig, outcome: BindingOutcome, basis: str, subject: str
) -> None:
    seq = rig.writer.developer_binding(outcome)

    [row] = rig.evidence()
    assert row["seq"] == seq
    assert (row["scope"], row["basis"], row["subject"], row["writer_token"]) == (
        "binding",
        basis,
        subject,
        OWNER,
    )


def test_toda_observacao_de_binding_do_developer_esta_classificada() -> None:
    """A tabela literal do escritor cobre **cada** membro do enum real (um novo quebra aqui)."""
    esperado = {
        BindingOutcome.RELEASED: BindingObservation.RELEASED,
        BindingOutcome.REVOKED: BindingObservation.REVOKED,
        BindingOutcome.RELEASE_FAILED: BindingObservation.UNPROVEN,
        BindingOutcome.NOT_BOUND: None,
        BindingOutcome.REFUSED: None,
    }
    assert set(esperado) == set(BindingOutcome)
    assert {o: observation_of(o) for o in BindingOutcome} == esperado


@pytest.mark.parametrize("outcome", [BindingOutcome.NOT_BOUND, BindingOutcome.REFUSED])
def test_binding_sem_observacao_positiva_nao_grava_evidencia(
    rig: Rig, outcome: BindingOutcome
) -> None:
    assert observation_of(outcome) is None
    assert rig.writer.developer_binding(outcome) is None
    assert rig.evidence() == []


def test_a_evidencia_de_um_binding_nao_vale_para_outro(rig: Rig) -> None:
    developer_present(rig)
    rig.writer.binding(tc.BindingSubject.CONTROL, BindingObservation.RELEASED)

    codes = blocker_codes(rig.snapshot())

    assert ("binding_unproven", "control") not in codes
    assert ("binding_unproven", "developer") in codes  # o do control não vale para o developer

    rig.writer.binding(tc.BindingSubject.DEVELOPER, BindingObservation.REVOKED)
    assert ("binding_unproven", "developer") not in blocker_codes(rig.snapshot())

    rig.writer.binding(
        tc.BindingSubject.DEVELOPER, BindingObservation.UNPROVEN
    )  # negativa posterior
    assert ("binding_unproven", "developer") in blocker_codes(rig.snapshot())


def test_so_o_dono_grava_e_a_base_viva_nao_passa_depois_da_adocao(rig: Rig, rdb: Db) -> None:
    rdb.to_teardown_pending(rig.rid)
    rdb.adopt(rig.rid)

    with pytest.raises(EvidenceRefused) as live:
        rig.writer.executor_drained()
    with pytest.raises(EvidenceRefused) as binding:
        rig.writer.binding(tc.BindingSubject.CONTROL, BindingObservation.RELEASED)

    assert live.value.reason == tc.WriteRefusal.LIVE_BASIS_AFTER_ADOPTION.value
    assert binding.value.reason == tc.WriteRefusal.LIVE_BASIS_AFTER_ADOPTION.value
    assert rig.evidence() == []


def test_reserva_liberada_nao_recebe_evidencia(rig: Rig, rdb: Db, session_factory: Any) -> None:
    release_for_test(Path(rdb.path), rig.task.id)
    writer = EvidenceWriter(session_factory, reservation_id=rig.rid, writer_token=OWNER)
    before = rdb.scalar("SELECT COUNT(*) FROM execution_evidence")

    with pytest.raises(EvidenceRefused) as refused:
        writer.drain_timeout()

    assert refused.value.reason == tc.WriteRefusal.RESERVATION_RELEASED.value
    assert rdb.scalar("SELECT COUNT(*) FROM execution_evidence") == before


def test_o_escritor_valida_a_forma_antes_do_banco(rig: Rig) -> None:
    with pytest.raises(TypeError):
        rig.writer.binding("developer", BindingObservation.RELEASED)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        rig.writer.binding(tc.BindingSubject.DEVELOPER, "released")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        EvidenceWriter(rig.factory, reservation_id="", writer_token=OWNER)
    with pytest.raises(TypeError):
        observation_of("released")
    assert rig.evidence() == []


def test_o_seq_da_evidencia_so_cresce(rig: Rig) -> None:
    seqs = [
        rig.writer.executor_drained(),
        rig.writer.drain_timeout(),
        rig.writer.binding(tc.BindingSubject.CONTROL, BindingObservation.RELEASED),
        rig.writer.drain_reentrant(),
    ]
    assert seqs == sorted(seqs) and len(set(seqs)) == 4


# ------------------------------------------------- a Entrega A não torna a liberação possível


@os_process
def test_com_todas_as_provas_da_a_a_liberacao_segue_bloqueada_sem_lease_e_historico(
    rig: Rig, rdb: Db, tmp_path: Path
) -> None:
    """Lançamento real fechado + R3 + R4 gravados pelo código de produção da A: os **recursos**
    ficam comprovados, mas `decide_release` segue em HOLD — lease e histórico (C7.7) são da B."""
    developer_present(rig)
    rig.launch(tmp_path, SENTINEL_SCRIPT, str(sentinel(tmp_path)))
    drain_with_evidence(_executor(), rig.writer, 1.0)
    rig.writer.binding(tc.BindingSubject.CONTROL, BindingObservation.RELEASED)
    rig.writer.developer_binding(BindingOutcome.RELEASED)

    snapshot = rig.snapshot()

    assert tc.classify_resources(snapshot).verdict is tc.TeardownVerdict.PROVEN_CLOSED
    unknown = rdb.snapshot(
        rig.rid, lease=tc.LeaseStatus.UNKNOWN, history=tc.HistoryCurrency.UNKNOWN
    )
    codes = {b.code for b in tc.assess_teardown(unknown).blockers}
    assert tc.BlockerCode.LEASE_UNKNOWN in codes
    assert tc.BlockerCode.HISTORY_NOT_PROVEN_CURRENT in codes
    assert not tc.assess_teardown(unknown).proven
    decision = rdb.decision(
        rig.rid, lease=tc.LeaseStatus.UNKNOWN, history=tc.HistoryCurrency.UNKNOWN
    )
    assert decision.verdict is not tc.ReleaseVerdict.RELEASE


def test_nenhum_escopo_vaza_entre_os_testes() -> None:
    assert current_scope() is None
    assert current_binding() is None


# ------------------------------------- binding ativo exige o escopo da mesma reserva (P1)


class StubBackend:
    """Backend falso: registra `start` e falha como um início recusado. Nenhum processo nasce."""

    def __init__(self) -> None:
        self.starts = 0

    def start(self, spec: Any, hooks: Any = None) -> Any:
        from app.process_runtime.contracts import StartFailed

        self.starts += 1
        raise StartFailed("stub: sem processo", tree_confirmed_dead=True)


class RecordingObserver:
    def __init__(self) -> None:
        self.intents: list[Any] = []

    def intent(self, intent: Any) -> object:
        self.intents.append(intent)
        return len(self.intents)

    def created(self, handle: object, identity: Any) -> None: ...

    def contained(self, handle: object) -> None: ...

    def ended(self, handle: object, end: Any) -> None: ...


@pytest.fixture
def stub_backend(monkeypatch: pytest.MonkeyPatch) -> StubBackend:
    stub = StubBackend()
    monkeypatch.setattr("app.process_runtime.supervisor._backend", stub)
    return stub


def _spec(tmp_path: Path) -> Any:
    return make_spec(tmp_path, "pass")


def _scope(observer: Any, reservation_id: str | None) -> LaunchScope:
    return LaunchScope(observer, LaunchGate(), enter_timeout_s=2.0, reservation_id=reservation_id)


@pytest.mark.parametrize("protected", [False, True])
def test_binding_ativo_sem_escopo_nao_chama_o_backend(
    stub_backend: StubBackend, tmp_path: Path, protected: bool
) -> None:
    with bind_launch_context("reservation-r", LaunchPhaseTag.PREPARATION):
        result = run_supervised(_spec(tmp_path), lambda: False, protected=protected)

    assert result.detail == "launch_refused:scope_missing"
    assert stub_backend.starts == 0


@pytest.mark.parametrize("protected", [False, True])
def test_binding_ativo_com_escopo_sem_reserva_nao_chama_o_backend(
    stub_backend: StubBackend, tmp_path: Path, protected: bool
) -> None:
    observer = RecordingObserver()
    with (
        install_scope(_scope(observer, None)),
        bind_launch_context("reservation-r", LaunchPhaseTag.PREPARATION),
    ):
        result = run_supervised(_spec(tmp_path), lambda: False, protected=protected)

    assert result.detail == "launch_refused:scope_mismatch"
    assert stub_backend.starts == 0 and observer.intents == []  # nem intent novo


def test_binding_ativo_com_reserva_diferente_e_recusado(
    stub_backend: StubBackend, tmp_path: Path
) -> None:
    observer = RecordingObserver()
    with (
        install_scope(_scope(observer, "reservation-a")),
        bind_launch_context("reservation-b", LaunchPhaseTag.PREPARATION),
    ):
        result = run_supervised(_spec(tmp_path), lambda: False)

    assert result.detail == "launch_refused:scope_mismatch"
    assert stub_backend.starts == 0 and observer.intents == []


def test_binding_e_escopo_correspondentes_preservam_o_caminho_autorizado(
    stub_backend: StubBackend, tmp_path: Path
) -> None:
    observer = RecordingObserver()
    with (
        install_scope(_scope(observer, "reservation-r")),
        bind_launch_context("reservation-r", LaunchPhaseTag.DEVELOPER),
    ):
        result = run_supervised(_spec(tmp_path), lambda: False)

    assert stub_backend.starts == 1  # chegou ao backend (que recusa o início, sem processo)
    assert result.detail is not None and "stub" in result.detail
    [intent] = observer.intents
    assert intent.reservation_id == "reservation-r"
    assert intent.phase_tag is LaunchPhaseTag.DEVELOPER


def test_sem_binding_o_comportamento_legado_e_preservado(
    stub_backend: StubBackend, tmp_path: Path
) -> None:
    result = run_supervised(_spec(tmp_path), lambda: False)  # sem escopo e sem binding
    assert stub_backend.starts == 1 and result.detail is not None and "stub" in result.detail

    observer = RecordingObserver()
    with install_scope(_scope(observer, None)):  # escopo ambiente sem reserva e sem binding
        run_supervised(_spec(tmp_path), lambda: False)
    assert stub_backend.starts == 2
    [intent] = observer.intents
    assert intent.reservation_id is None and intent.phase_tag is None

    result = run_supervised(_spec(tmp_path), lambda: False, protected=True)  # sem escopo
    assert result.detail == "launch_refused:scope_missing" and stub_backend.starts == 2
