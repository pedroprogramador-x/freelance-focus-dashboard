"""E8.4.5 / I4 — cancelamento: pedido durável (tx1) separado da linearização pelo gate (tx2).

Banco SQLite real migrado; gates reais (`LaunchGate`) onde a semântica do gate importa e portas
falsas isoladas (`SpyGate`) onde só a ordem e o contrato importam. Nada é liberado, nenhuma
evidência é gravada e `LaunchScope`/observer SQLite não participam (I5).

Os cenários de concorrência usam `Event`/`Barrier`: nenhum depende de `sleep` para ser correto (o
`join(timeout)` curto só afirma que uma thread **continua bloqueada**).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings
from app.db.enums import TaskStatus
from app.db.models import WorkspaceTask
from app.orchestrator import InvalidTransition, cancel
from app.orchestrator.drain import (
    DrainCoordinator,
    DrainPreconditionError,
    DrainStatus,
    GateRegistry,
)
from app.orchestrator.execution_manager import cancel_and_drain
from app.process_runtime.launch import (
    LaunchGate,
    LaunchIntent,
    LaunchPlatform,
    LaunchRefusal,
    LaunchScope,
    LinearizeStatus,
    LockOrderViolation,
    LockRank,
    ProtectedLaunch,
    install_scope,
    ranked,
)
from tests.admission_support_e8_4_1 import admit, approved_task
from tests.execution_reservation_support_e8_4_5 import Db
from tests.reservation_support_e8_4_5 import SpyGate

KEY = "exec-key-1"


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    return tmp_path / "artifacts"


@pytest.fixture
def rdb(temp_settings: AppSettings, migrated_url: str) -> Iterator[Db]:
    del migrated_url
    db = Db(Path(temp_settings.sqlalchemy_url.split("///", 1)[1]))
    try:
        yield db
    finally:
        db.close()


class Ctx:
    """Uma task admitida (`executing`) com a sua reserva `active`."""

    def __init__(
        self,
        session: Session,
        factory: sessionmaker[Session],
        db: Db,
        task: WorkspaceTask,
        run: str,
    ) -> None:
        self.session = session
        self.factory = factory
        self.db = db
        self.task = task
        self.control_run_id = run
        self.registry = GateRegistry()
        self.coordinator = DrainCoordinator(self.registry, factory, gate_timeout_s=2.0)

    def row(self) -> dict[str, Any]:
        cursor = self.db.run(
            "SELECT * FROM execution_reservation WHERE control_run_id = ?", (self.control_run_id,)
        )
        names = [c[0] for c in cursor.description]
        return dict(zip(names, cursor.fetchone(), strict=True))

    def task_status(self) -> str:
        return str(
            self.db.scalar("SELECT status FROM workspace_task WHERE id = ?", (self.task.id,))
        )

    def drain(self, **kw: Any) -> Any:
        self.session.commit()
        self.session.expire_all()
        return cancel_and_drain(self.session, self.task.id, drain=self.coordinator, **kw)

    def assert_never_released(self) -> None:
        row = self.row()
        assert row["state"] != "released" and row["released_at"] is None
        assert self.db.scalar("SELECT COUNT(*) FROM execution_evidence") == 0
        assert self.db.scalar("SELECT COUNT(*) FROM execution_launch") == 0


@pytest.fixture
def ctx(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: Any,
    artifacts_dir: Path,
    rdb: Db,
) -> Ctx:
    task = approved_task(session, workspace, artifacts_dir)
    admission = admit(session, task.id, artifacts_dir, key=KEY)
    assert admission.run is not None
    session.commit()
    return Ctx(session, session_factory, rdb, task, admission.run.id)


# --------------------------------------------------------------------- sem reserva (legado)


def test_cancel_de_task_sem_reserva_e_o_de_sempre_e_nao_toca_gate_nenhum(
    session: Session, workspace: Any, artifacts_dir: Path, rdb: Db
) -> None:
    task = approved_task(session, workspace, artifacts_dir)
    gate = SpyGate()
    registry = GateRegistry()
    registry.register("qualquer", gate)
    coordinator = DrainCoordinator(registry, lambda: Session())  # a factory nem é usada

    outcome = cancel_and_drain(session, task.id, drain=coordinator)

    assert outcome.drain.status is DrainStatus.NOT_REQUIRED
    assert outcome.task.status is TaskStatus.CANCELLED and outcome.task.cancel_requested
    assert gate.stops == 0 and gate.closes == []


def test_cancel_sem_coordinator_so_grava_o_pedido_e_nao_comprova_fechamento_do_gate(
    ctx: Ctx,
) -> None:
    """Limites do caminho não operacional: nenhum gate é tocado, a reserva segue `active` e o gate
    da tentativa (aqui, real e registrado) nem fica sabendo — logo um `intent` já persistido ou um
    lançador dentro da seção protegida NÃO são alcançados por `cancel()`."""
    gate = LaunchGate()
    ctx.registry.register(ctx.control_run_id, gate)

    cancelled = cancel(ctx.session, ctx.task.id)
    ctx.session.commit()

    assert cancelled.status is TaskStatus.CANCELLED
    row = ctx.row()
    assert row["state"] == "active" and row["version"] == 2
    assert row["drain_requested_at"] is not None and row["draining_at"] is None
    assert gate.is_stopped() is False and gate.closed is False  # ninguém falou com o gate
    ctx.assert_never_released()
    # o Run de controle segue aberto: cancelar não encerra nem comprova nada
    assert ctx.db.scalar("SELECT status FROM run WHERE id = ?", (ctx.control_run_id,)) == "running"


def test_cancel_de_task_terminal_continua_409_e_nao_mexe_na_reserva(ctx: Ctx) -> None:
    ctx.drain()
    before = ctx.row()

    with pytest.raises(InvalidTransition):
        ctx.drain()

    assert ctx.row() == before


# ------------------------------------------------------------------------- a ordem do contrato


def test_ordem_request_stop_depois_tx1_duravel_depois_gate_depois_tx2(ctx: Ctx) -> None:
    seen: list[tuple[str, str, bool, str]] = []

    class Ordered(SpyGate):
        def request_stop(self) -> None:
            super().request_stop()
            # antes de QUALQUER transição: a task ainda é `executing` e não há pedido gravado
            seen.append(
                (
                    "stop",
                    ctx.task_status(),
                    ctx.row()["drain_requested_at"] is not None,
                    ctx.row()["state"],
                )
            )

    def outcome(_timeout: float, commit: Callable[[], Any]) -> Any:
        # a tx1 é durável para OUTRA conexão antes do gate ser tomado
        seen.append(
            (
                "close",
                ctx.task_status(),
                ctx.row()["drain_requested_at"] is not None,
                ctx.row()["state"],
            )
        )
        value = commit()
        return SimpleNamespace(status=LinearizeStatus.LINEARIZED, value=value, error=None)

    gate = Ordered(outcome)
    ctx.registry.register(ctx.control_run_id, gate)

    result = ctx.drain()

    assert seen == [
        ("stop", "executing", False, "active"),
        ("close", "cancelled", True, "active"),
    ]
    assert result.drain.status is DrainStatus.DRAINING_COMMITTED
    assert result.drain.linearization_confirmed and result.drain.reservation_version == 3
    row = ctx.row()
    assert row["state"] == "draining" and row["version"] == 3
    assert row["drain_requested_at"] is not None and row["draining_at"] is not None
    assert gate.stops == 1 and gate.closes == [2.0]
    ctx.assert_never_released()


def test_linearizacao_com_o_launch_gate_real_confirma_active_para_draining(ctx: Ctx) -> None:
    gate = LaunchGate()
    ctx.registry.register(ctx.control_run_id, gate)

    result = ctx.drain()

    assert result.drain.status is DrainStatus.DRAINING_COMMITTED
    assert ctx.row()["state"] == "draining"
    # depois de L, nenhum lançamento cruza: o gate recusa antes mesmo do `intent`
    calls: list[str] = []

    class Observer:
        def intent(self, intent: LaunchIntent) -> object:
            calls.append("intent")
            return object()

        def created(self, handle: object, identity: object) -> None:
            calls.append("created")

        def contained(self, handle: object) -> None:
            calls.append("contained")

        def ended(self, handle: object, end: object) -> None:
            calls.append("ended")

    launch = ProtectedLaunch(
        LaunchScope(Observer(), gate),
        LaunchIntent("python.exe", LaunchPlatform.WIN32, "win_job_v1"),
    )
    assert launch.admit() is LaunchRefusal.STOPPED
    assert calls == []
    ctx.assert_never_released()


def test_cancelamento_espera_o_lancador_dentro_da_secao_e_so_linearize_depois_dele(
    ctx: Ctx,
) -> None:
    gate = LaunchGate()
    ctx.registry.register(ctx.control_run_id, gate)
    inside = threading.Event()
    leave = threading.Event()

    def launcher() -> None:
        assert gate.acquire(5.0)
        try:
            inside.set()
            assert leave.wait(10.0)
        finally:
            gate.release()

    launcher_thread = threading.Thread(target=launcher)
    launcher_thread.start()
    assert inside.wait(10.0)

    done: dict[str, Any] = {}

    def canceller() -> None:
        with ctx.factory() as other:
            done["result"] = cancel_and_drain(other, ctx.task.id, drain=ctx.coordinator)

    cancel_thread = threading.Thread(target=canceller)
    cancel_thread.start()
    cancel_thread.join(timeout=0.4)

    # o cancelamento da task (tx1) já é durável, mas L NÃO ocorreu enquanto o lançador está dentro
    assert cancel_thread.is_alive(), "o fechamento deveria esperar a seção do lançador"
    row = ctx.row()
    assert ctx.task_status() == "cancelled" and row["drain_requested_at"] is not None
    assert row["state"] == "active" and row["draining_at"] is None
    assert gate.is_stopped() is True and gate.closed is False  # `stop` ≠ fechamento confirmado

    leave.set()
    launcher_thread.join(10.0)
    cancel_thread.join(10.0)
    assert not cancel_thread.is_alive()
    assert done["result"].drain.status is DrainStatus.DRAINING_COMMITTED
    assert ctx.row()["state"] == "draining"
    ctx.assert_never_released()


# ------------------------------------------------------------- timeout e commit: nunca L


def test_gate_timeout_nao_e_linearizacao_e_nao_declara_nada(ctx: Ctx) -> None:
    gate = LaunchGate()
    ctx.registry.register(ctx.control_run_id, gate)
    ctx.coordinator = DrainCoordinator(ctx.registry, ctx.factory, gate_timeout_s=0.05)
    holding = threading.Event()
    release = threading.Event()

    def holder() -> None:
        assert gate.acquire(5.0)
        holding.set()
        release.wait(10.0)
        gate.release()

    thread = threading.Thread(target=holder)
    thread.start()
    assert holding.wait(10.0)
    try:
        result = ctx.drain()
    finally:
        release.set()
        thread.join(10.0)

    assert result.drain.status is DrainStatus.GATE_TIMEOUT
    assert not result.drain.linearization_confirmed
    assert result.task.status is TaskStatus.CANCELLED  # tx1 permanece
    row = ctx.row()
    assert row["state"] == "active" and row["drain_requested_at"] is not None
    assert row["draining_at"] is None and row["version"] == 2
    assert gate.is_stopped() is True and gate.closed is False
    ctx.assert_never_released()


def test_commit_failed_nao_declara_drenagem_nao_repete_nem_usa_gate_closed(
    ctx: Ctx, engine: Engine
) -> None:
    gate = LaunchGate()
    ctx.registry.register(ctx.control_run_id, gate)
    attempts: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _fail(_c: Any, _cur: Any, statement: str, *_: Any) -> None:
        if statement.lstrip().upper().startswith("UPDATE EXECUTION_RESERVATION SET STATE"):
            attempts.append(statement)
            raise RuntimeError("falha injetada na tx2")

    try:
        result = ctx.drain()
    finally:
        event.remove(engine, "before_cursor_execute", _fail)

    assert result.drain.status is DrainStatus.COMMIT_UNCERTAIN
    assert not result.drain.linearization_confirmed
    assert len(attempts) == 1, "sem retry"
    row = ctx.row()
    assert row["state"] == "active" and row["draining_at"] is None
    assert row["drain_requested_at"] is not None  # o pedido (tx1) permanece
    # o gate ficou fechado em memória (fail closed), e isso NÃO foi tratado como prova
    assert gate.closed is True
    assert not result.drain.linearization_confirmed
    ctx.assert_never_released()


def test_commit_incerto_depois_de_gravar_tambem_nao_vira_linearizacao(
    ctx: Ctx, engine: Engine
) -> None:
    """A tx2 chega a commitar, mas a resposta se perde: o chamador não sabe. O banco pode dizer
    `draining`, e mesmo assim o resultado é `COMMIT_UNCERTAIN` — nada é reconstruído a partir do
    banco nem da memória."""

    class LostAnswer(Session):
        def commit(self) -> None:
            super().commit()
            raise RuntimeError("a resposta se perdeu depois do COMMIT")

    gate = LaunchGate()
    ctx.registry.register(ctx.control_run_id, gate)
    ctx.coordinator = DrainCoordinator(
        ctx.registry, lambda: LostAnswer(bind=engine, expire_on_commit=False), gate_timeout_s=2.0
    )

    result = ctx.drain()

    assert result.drain.status is DrainStatus.COMMIT_UNCERTAIN
    assert not result.drain.linearization_confirmed
    assert ctx.row()["state"] == "draining"  # fato do banco, não uma credencial
    ctx.assert_never_released()


@pytest.mark.parametrize("value", ["commit_failed", "weird", None])
def test_status_desconhecido_ou_gate_closed_nunca_viram_linearizacao(ctx: Ctx, value: Any) -> None:
    class Closed(SpyGate):
        closed = True

    gate = Closed(lambda _t, _c: SimpleNamespace(status=SimpleNamespace(value=value), error=None))
    ctx.registry.register(ctx.control_run_id, gate)

    result = ctx.drain()

    assert result.drain.status is DrainStatus.COMMIT_UNCERTAIN
    assert ctx.row()["state"] == "active"  # o `commit` do gate falso nem foi executado


def test_valores_do_gate_batem_com_linearize_status() -> None:
    from app.orchestrator import drain
    from app.process_runtime.launch import DEFAULT_GATE_TIMEOUT_S

    assert LinearizeStatus.LINEARIZED.value == drain.LINEARIZED
    assert LinearizeStatus.GATE_TIMEOUT.value == drain.GATE_TIMEOUT_VALUE
    assert LinearizeStatus.COMMIT_FAILED.value == drain.COMMIT_FAILED
    assert drain.DEFAULT_DRAIN_TIMEOUT_S == DEFAULT_GATE_TIMEOUT_S


# ------------------------------------------------------- pré-condições e o gate da tentativa


def test_linearize_com_transacao_aberta_e_recusada_sem_tocar_o_gate(ctx: Ctx) -> None:
    gate = SpyGate(lambda _t, _c: pytest.fail("o gate não podia ser tomado"))
    ctx.registry.register(ctx.control_run_id, gate)
    from sqlalchemy import text

    other = ctx.factory()
    try:
        other.execute(text("SELECT 1"))  # abre transação
        assert other.in_transaction()
        with pytest.raises(DrainPreconditionError):
            ctx.coordinator.linearize(
                session=other, control_run_id=ctx.control_run_id, expected_version=1, now=_now()
            )
    finally:
        other.close()

    assert gate.closes == [] and ctx.row()["state"] == "active"


def test_violacao_da_ordem_de_locks_propaga_e_nada_e_declarado(ctx: Ctx) -> None:
    gate = LaunchGate()
    ctx.registry.register(ctx.control_run_id, gate)
    ctx.session.commit()

    with ranked(LockRank.SQLITE), pytest.raises(LockOrderViolation):
        ctx.coordinator.linearize(
            session=ctx.session, control_run_id=ctx.control_run_id, expected_version=1, now=_now()
        )

    assert ctx.row()["state"] == "active" and ctx.row()["draining_at"] is None


def test_o_gate_e_o_da_tentativa_certa_e_o_escopo_ambiente_e_ignorado(ctx: Ctx) -> None:
    other_attempt = SpyGate(lambda _t, _c: pytest.fail("gate de outra tentativa"))
    ctx.registry.register("00000000-0000-4000-8000-000000000000", other_attempt)

    class Observer:
        def intent(self, intent: LaunchIntent) -> object:
            return object()

        def created(self, handle: object, identity: object) -> None: ...

        def contained(self, handle: object) -> None: ...

        def ended(self, handle: object, end: object) -> None: ...

    ambient_gate = LaunchGate()
    with install_scope(LaunchScope(Observer(), ambient_gate)):
        result = ctx.drain()

    # sem gate registrado para ESTA tentativa: só o pedido durável, nada linearizado
    assert result.drain.status is DrainStatus.REQUESTED_NO_GATE
    assert other_attempt.stops == 0 and other_attempt.closes == []
    assert ctx.row()["state"] == "active" and ctx.row()["drain_requested_at"] is not None
    assert ctx.task_status() == "cancelled"
    ctx.assert_never_released()


def test_registry_recusa_gate_duplicado_e_invalido() -> None:
    registry = GateRegistry()
    registry.register("run-1", SpyGate())
    with pytest.raises(ValueError):
        registry.register("run-1", SpyGate())
    with pytest.raises(TypeError):
        registry.register("run-2", object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        registry.register("", SpyGate())
    assert registry.gate_for("inexistente") is None


def test_reserva_ja_alem_de_active_nao_e_reescrita(ctx: Ctx) -> None:
    ctx.db.update_reservation(ctx.row()["id"], state="teardown_pending")
    before = ctx.row()
    gate = SpyGate(lambda _t, _c: pytest.fail("não há o que linearizar"))
    ctx.registry.register(ctx.control_run_id, gate)

    result = ctx.drain()

    assert result.drain.status is DrainStatus.ALREADY_PAST
    assert result.task.status is TaskStatus.CANCELLED
    assert ctx.row() == before and gate.closes == []


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)
