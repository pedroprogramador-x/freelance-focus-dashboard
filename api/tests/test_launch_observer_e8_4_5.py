"""E8.4.5 / I2 — LaunchObserver + LaunchGate no Supervisor de Processos.

Portas e callbacks falsos para as transições; processos reais (``sys.executable``) para provar que
nada nasce depois da fronteira. Os testes específicos de SO estão marcados; os de POSIX/Linux só
rodam em Linux (CI): o ambiente Windows local **não** os executa e nenhuma prova POSIX é inventada.
"""

from __future__ import annotations

import ast
import contextlib
import functools
import itertools
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.orchestrator import teardown_contract as tc
from app.process_runtime import (
    ProcessOutcome,
    ProcessResult,
    ProcessSpec,
    run_supervised,
)
from app.process_runtime import launch as launch_module
from app.process_runtime.contracts import StartFailed
from app.process_runtime.launch import (
    LaunchEnd,
    LaunchGate,
    LaunchIntent,
    LaunchPlatform,
    LaunchScope,
    LinearizeStatus,
    LockOrderViolation,
    LockRank,
    ProcessIdentity,
    ScopeAlreadyInstalled,
    current_scope,
    held_ranks,
    install_scope,
    ranked,
)

if sys.platform == "win32":
    from app.process_runtime import _windows as _backend_module
else:
    from app.process_runtime import _posix as _backend_module

backend: Any = _backend_module

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
windows_only = pytest.mark.skipif(not IS_WINDOWS, reason="comportamento específico do Windows")
linux_only = pytest.mark.skipif(not IS_LINUX, reason="execução real em Linux (CI): não improvisada")
os_process = pytest.mark.skipif(not (IS_WINDOWS or IS_LINUX), reason="exige Windows ou Linux")

API_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = API_ROOT / "app"
WORKER = API_ROOT / "tests" / "launch_crash_worker_e8_4_5.py"
SENTINEL_SCRIPT = "import sys; open(sys.argv[1], 'w').write('x')"
WAIT = 15.0


# ------------------------------------------------------------------------------ apoio


def _mark(box: list[Any], item: Any, value: Any = None) -> Any:
    box.append(item)
    return value


def make_spec(
    tmp_path: Path, script: str = SENTINEL_SCRIPT, *args: str, timeout_s: float = 30.0
) -> ProcessSpec:
    env = {k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "PATH", "TEMP", "TMP")}
    return ProcessSpec(
        argv=(sys.executable, "-c", script, *args),
        cwd=str(tmp_path),
        env=env,
        timeout_s=timeout_s,
        max_stdout_bytes=4096,
        max_stderr_bytes=4096,
        kill_grace_s=0.5,
    )


class Fake:
    """Observer falso: registra tudo com uma ordem global, falha e pausa por nome de evento."""

    def __init__(self, *, fail_at: set[str] | None = None) -> None:
        self.events: list[tuple[int, str, Any]] = []
        self._lock = threading.Lock()
        self._counter = itertools.count(1)
        self.fail_at = fail_at or set()
        self.pauses: dict[str, tuple[threading.Event, threading.Event]] = {}
        self.handles = itertools.count(1)

    def pause_at(self, name: str) -> tuple[threading.Event, threading.Event]:
        reached, proceed = threading.Event(), threading.Event()
        self.pauses[name] = (reached, proceed)
        return reached, proceed

    def names(self) -> list[str]:
        with self._lock:
            return [name for _, name, _ in self.events]

    def payloads(self, name: str) -> list[Any]:
        with self._lock:
            return [p for _, n, p in self.events if n == name]

    def index(self, name: str) -> int:
        with self._lock:
            return next(i for i, n, _ in self.events if n == name)

    def _step(self, name: str, payload: Any = None) -> None:
        with self._lock:
            self.events.append((next(self._counter), name, payload))
        if name in self.pauses:
            reached, proceed = self.pauses[name]
            reached.set()
            assert proceed.wait(WAIT), f"pausa em {name} nunca foi liberada"
        if name in self.fail_at:
            raise RuntimeError(f"falha injetada em {name}")

    def intent(self, intent: LaunchIntent) -> object:
        self._step("intent", intent)
        return next(self.handles)

    def created(self, handle: object, identity: ProcessIdentity) -> None:
        self._step("created", identity)

    def contained(self, handle: object) -> None:
        self._step("contained")

    def ended(self, handle: object, end: LaunchEnd) -> None:
        self._step("ended", end)


def scope_of(obs: Fake, *, timeout_s: float = 5.0) -> LaunchScope:
    return LaunchScope(obs, LaunchGate(), enter_timeout_s=timeout_s)


def run(spec: ProcessSpec, *, protected: bool = False) -> ProcessResult:
    return run_supervised(spec, lambda: False, protected=protected)


def in_thread(fn: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    box: list[Any] = []

    def target() -> None:
        try:
            box.append(fn())
        except BaseException as exc:
            box.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, box


def join(thread: threading.Thread) -> None:
    thread.join(WAIT)
    assert not thread.is_alive(), "deadlock: thread não terminou"


def gate_free(gate: LaunchGate) -> bool:
    """O gate não está preso (adquire e solta sem bloquear)."""
    if not gate.acquire(1.0):
        return False
    gate.release()
    return True


# --- 1. legado


def test_sem_escopo_o_comportamento_legado_e_preservado(tmp_path: Path) -> None:
    sentinel = tmp_path / "ok"
    result = run(make_spec(tmp_path, SENTINEL_SCRIPT, str(sentinel)))
    assert result.outcome is ProcessOutcome.EXITED and result.exit_code == 0
    assert sentinel.exists() and result.tree_confirmed_dead
    assert current_scope() is None


def test_protected_sem_escopo_e_recusado_e_nunca_cai_no_popen(tmp_path: Path) -> None:
    sentinel = tmp_path / "nunca"
    result = run(make_spec(tmp_path, SENTINEL_SCRIPT, str(sentinel)), protected=True)
    assert result.outcome is ProcessOutcome.SUPERVISION_FAILED
    assert result.detail == "launch_refused:scope_missing"
    assert result.tree_confirmed_dead and result.exit_code is None
    assert not sentinel.exists()


def test_protected_precisa_ser_bool(tmp_path: Path) -> None:
    for bad in ("true", 1, None):
        with pytest.raises(TypeError):
            run_supervised(make_spec(tmp_path), lambda: False, protected=bad)  # type: ignore[arg-type]


# --- 2. caminho feliz


@os_process
def test_ordem_dos_eventos_e_gate_nao_preso_durante_a_vida_do_processo(tmp_path: Path) -> None:
    obs = Fake()
    scope = scope_of(obs)
    sentinel = tmp_path / "vivo"
    script = "import sys,time; open(sys.argv[1],'w').write('x'); time.sleep(1.5)"
    with install_scope(scope):
        thread, box = in_thread(lambda: run(make_spec(tmp_path, script, str(sentinel))))
        deadline = time.monotonic() + WAIT
        while "contained" not in obs.names() and time.monotonic() < deadline:
            time.sleep(0.01)
        # o processo ainda vive (não há `ended`) e o gate já está livre: a seção acabou no resume
        deadline = time.monotonic() + WAIT
        acquired = False
        while time.monotonic() < deadline and "ended" not in obs.names():
            if gate_free(scope.gate):
                acquired = True
                break
            time.sleep(0.01)
        assert acquired and "ended" not in obs.names()
        join(thread)
    result = box[0]
    assert result.outcome is ProcessOutcome.EXITED and sentinel.exists()
    assert obs.names() == ["intent", "created", "contained", "ended"]
    assert obs.payloads("ended") == [LaunchEnd.TREE_CONFIRMED_DEAD]
    [identity] = obs.payloads("created")
    assert identity.pid > 0 and identity.start_token
    [intent] = obs.payloads("intent")
    assert intent.exe_name == Path(sys.executable).name
    assert intent.platform is (LaunchPlatform.WIN32 if IS_WINDOWS else LaunchPlatform.POSIX)
    assert current_scope() is None


@windows_only
def test_windows_nada_do_processo_roda_antes_de_contained(tmp_path: Path) -> None:
    sentinel = tmp_path / "sentinela"

    class Probe(Fake):
        seen_at_contained: bool | None = None
        seen_at_created: bool | None = None

        def created(self, handle: object, identity: ProcessIdentity) -> None:
            Probe.seen_at_created = sentinel.exists()
            super().created(handle, identity)

        def contained(self, handle: object) -> None:
            time.sleep(0.4)  # folga: se o processo já rodasse, a sentinela existiria
            Probe.seen_at_contained = sentinel.exists()
            super().contained(handle)

    obs = Probe()
    with install_scope(scope_of(obs)):
        result = run(make_spec(tmp_path, SENTINEL_SCRIPT, str(sentinel)))
    assert result.outcome is ProcessOutcome.EXITED and sentinel.exists()
    assert Probe.seen_at_created is False and Probe.seen_at_contained is False


@windows_only
def test_windows_token_de_inicio_identifica_o_processo() -> None:
    popen = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import time; time.sleep(5)"], stdout=subprocess.DEVNULL
    )
    other = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import time; time.sleep(5)"], stdout=subprocess.DEVNULL
    )
    try:
        first = backend._creation_token(popen.pid)
        assert first.isdigit() and first == backend._creation_token(popen.pid)
        assert first != backend._creation_token(other.pid)
    finally:
        popen.kill()
        other.kill()
        popen.wait()
        other.wait()


@linux_only
def test_linux_token_de_inicio_do_proc(tmp_path: Path) -> None:
    popen = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])  # noqa: S603
    try:
        token = backend._start_token(popen.pid)
        assert token.isdigit()
    finally:
        popen.kill()
        popen.wait()
    assert backend._start_token(2**30) == "unavailable"


# --- 3. linearização


@os_process
def test_pausa_entre_intent_e_popen_com_stop_concorrente_nao_cria_processo(tmp_path: Path) -> None:
    """CP-G1: o `intent` já está persistido; o cancelamento chega; o lançador retoma e RECUSA."""
    obs = Fake()
    reached, proceed = obs.pause_at("intent")
    scope = scope_of(obs)
    sentinel = tmp_path / "nunca"
    committed: list[int] = []
    with install_scope(scope):
        launcher, lbox = in_thread(lambda: run(make_spec(tmp_path, SENTINEL_SCRIPT, str(sentinel))))
        assert reached.wait(WAIT)
        closer, cbox = in_thread(
            lambda: scope.gate.close_and_linearize(5.0, lambda: _mark(committed, 1, "L"))
        )
        deadline = time.monotonic() + WAIT
        while not scope.gate.stopped and time.monotonic() < deadline:
            time.sleep(0.005)
        time.sleep(0.2)
        # o fechamento ainda NÃO linearizou: o lançador segura a seção
        assert scope.gate.stopped and not scope.gate.closed and committed == []
        proceed.set()
        join(launcher)
        join(closer)
    result = lbox[0]
    assert result.outcome is ProcessOutcome.CANCELLED
    assert result.detail == "launch_refused:stopped" and result.tree_confirmed_dead
    assert not sentinel.exists()
    assert obs.names() == ["intent", "ended"]
    assert obs.payloads("ended") == [LaunchEnd.REFUSED_BEFORE_CREATION]
    lin = cbox[0]
    assert lin.status is LinearizeStatus.LINEARIZED and lin.value == "L" and committed == [1]
    assert scope.gate.closed


@os_process
def test_stop_depois_da_fronteira_o_lancamento_e_contabilizado_e_o_fechamento_espera(
    tmp_path: Path,
) -> None:
    obs = Fake()
    reached, proceed = obs.pause_at("created")  # o processo já existe; ainda dentro da seção
    scope = scope_of(obs)
    sentinel = tmp_path / "criado"
    order: list[str] = []
    with install_scope(scope):
        launcher, lbox = in_thread(lambda: run(make_spec(tmp_path, SENTINEL_SCRIPT, str(sentinel))))
        assert reached.wait(WAIT)
        closer, cbox = in_thread(
            lambda: scope.gate.close_and_linearize(5.0, lambda: _mark(order, "commit", 1))
        )
        time.sleep(0.3)
        assert order == [] and not scope.gate.closed  # fechamento bloqueado pela seção
        proceed.set()
        join(launcher)
        join(closer)
        # depois de L, nenhum lançamento novo
        before = list(obs.names())
        refused = run(make_spec(tmp_path, SENTINEL_SCRIPT, str(tmp_path / "depois")))
    assert lbox[0].outcome is ProcessOutcome.EXITED and sentinel.exists()
    assert cbox[0].linearized and order == ["commit"]
    assert before == ["intent", "created", "contained", "ended"]
    assert refused.outcome is ProcessOutcome.CANCELLED
    assert refused.detail == "launch_refused:stopped"
    assert obs.names() == before  # nem intent novo
    assert not (tmp_path / "depois").exists()


def test_timeout_do_gate_na_admissao_recusa_sem_intent_e_sem_processo(tmp_path: Path) -> None:
    obs = Fake()
    scope = scope_of(obs, timeout_s=0.2)
    sentinel = tmp_path / "nunca"
    held, release = threading.Event(), threading.Event()

    def holder() -> None:
        assert scope.gate.acquire(5.0)
        held.set()
        release.wait(WAIT)
        scope.gate.release()

    thread, _ = in_thread(holder)
    assert held.wait(WAIT)
    try:
        with install_scope(scope):
            result = run(make_spec(tmp_path, SENTINEL_SCRIPT, str(sentinel)))
    finally:
        release.set()
        join(thread)
    assert result.outcome is ProcessOutcome.SUPERVISION_FAILED
    assert result.detail == "launch_refused:gate_timeout" and result.tree_confirmed_dead
    assert obs.names() == [] and not sentinel.exists()


def test_timeout_do_fechamento_nao_declara_linearizacao(tmp_path: Path) -> None:
    gate = LaunchGate()
    held, release = threading.Event(), threading.Event()

    def holder() -> None:
        assert gate.acquire(5.0)
        held.set()
        release.wait(WAIT)
        gate.release()

    thread, _ = in_thread(holder)
    assert held.wait(WAIT)
    commits: list[int] = []
    try:
        lin = gate.close_and_linearize(0.2, lambda: commits.append(1))
    finally:
        release.set()
        join(thread)
    assert lin.status is LinearizeStatus.GATE_TIMEOUT and not lin.linearized
    assert commits == [] and not gate.closed  # L não ocorreu
    assert gate.stopped  # mas o stop irreversível já vale: qualquer lançador que acordar recusa
    obs = Fake()
    with install_scope(LaunchScope(obs, gate, enter_timeout_s=1.0)):
        result = run(make_spec(tmp_path, SENTINEL_SCRIPT, str(tmp_path / "x")))
    assert result.detail == "launch_refused:stopped" and obs.names() == []


def test_falha_do_commit_fecha_por_seguranca_mas_nao_declara_o_fechamento() -> None:
    gate = LaunchGate()

    def commit() -> None:
        raise RuntimeError("banco indisponível")

    lin = gate.close_and_linearize(1.0, commit)
    assert lin.status is LinearizeStatus.COMMIT_FAILED and isinstance(lin.error, RuntimeError)
    assert not lin.linearized and gate.closed and gate.stopped
    assert gate_free(gate)  # o mutex foi solto


def test_stop_e_irreversivel_e_nao_existe_reset() -> None:
    gate = LaunchGate()
    assert not gate.is_stopped()
    gate.request_stop()
    gate.request_stop()
    assert gate.is_stopped() and not gate.closed
    assert not any(name in dir(gate) for name in ("reset", "clear", "reopen", "unstop"))


def test_nenhum_lancamento_novo_depois_do_fechamento_confirmado(tmp_path: Path) -> None:
    obs = Fake()
    scope = scope_of(obs)
    with install_scope(scope):
        assert scope.gate.close_and_linearize(1.0, lambda: None).linearized
        for index in range(3):
            result = run(make_spec(tmp_path, SENTINEL_SCRIPT, str(tmp_path / f"n{index}")))
            assert result.detail == "launch_refused:stopped"
    assert obs.names() == [] and not list(tmp_path.glob("n*"))


def _stress_round(tmp_path: Path, round_: int) -> None:
    obs = Fake()
    scope = scope_of(obs, timeout_s=10.0)
    commit_at: list[int] = []

    def launch(i: int) -> ProcessResult:
        sentinel = tmp_path / f"r{round_}_{i}"
        return run(make_spec(tmp_path, SENTINEL_SCRIPT, str(sentinel)))

    def commit() -> None:
        commit_at.append(len(obs.events))

    with install_scope(scope):
        launchers = [in_thread(functools.partial(launch, i)) for i in range(3)]
        time.sleep(0.05 * round_)
        closer, cbox = in_thread(lambda: scope.gate.close_and_linearize(10.0, commit))
        for thread, _ in launchers:
            join(thread)
        join(closer)
    assert cbox[0].linearized
    boundary = commit_at[0]  # quantos eventos existiam quando L ocorreu
    names = [name for _, name, _ in obs.events]
    # nenhum `intent`/`created`/`contained` posterior a L
    assert all(n == "ended" for n in names[boundary:]), (round_, names, boundary)
    refused = obs.payloads("ended").count(LaunchEnd.REFUSED_BEFORE_CREATION)
    assert names.count("intent") == names.count("created") + refused
    for _, box in launchers:
        assert isinstance(box[0], ProcessResult)
        assert box[0].outcome in (ProcessOutcome.EXITED, ProcessOutcome.CANCELLED)


@os_process
def test_estresse_nenhum_created_depois_de_L_e_sem_deadlock(tmp_path: Path) -> None:
    """Vários lançadores contra um fechamento: tudo que passou da fronteira está antes de L."""
    for round_ in range(4):
        _stress_round(tmp_path, round_)


# --- 4. ordem de locks


def test_ordem_de_locks_e_verificavel() -> None:
    gate = LaunchGate()
    assert held_ranks() == ()
    with ranked(LockRank.EXECUTOR):
        assert gate.acquire(1.0)  # executor < gate: legal
        assert held_ranks() == (LockRank.EXECUTOR, LockRank.GATE)
        with ranked(LockRank.SQLITE):  # gate < sqlite: legal
            assert held_ranks()[-1] is LockRank.SQLITE
        gate.release()
    assert held_ranks() == ()


def test_gate_com_transacao_aberta_e_violacao_e_o_gate_nao_e_reentrante() -> None:
    gate = LaunchGate()
    with ranked(LockRank.SQLITE), pytest.raises(LockOrderViolation):
        gate.acquire(1.0)
    assert gate_free(gate) and held_ranks() == ()
    assert gate.acquire(1.0)
    with pytest.raises(LockOrderViolation):
        gate.acquire(0.05)  # mesma thread, mesmo rank
    gate.release()
    with pytest.raises(LockOrderViolation), ranked(LockRank.GATE), ranked(LockRank.EXECUTOR):
        pass
    assert held_ranks() == ()


def test_lancamento_com_transacao_aberta_na_thread_e_recusado_sem_processo(tmp_path: Path) -> None:
    obs = Fake()
    sentinel = tmp_path / "nunca"
    with install_scope(scope_of(obs)), ranked(LockRank.SQLITE):
        result = run(make_spec(tmp_path, SENTINEL_SCRIPT, str(sentinel)))
    assert result.detail == "launch_refused:lock_order" and obs.names() == []
    assert not sentinel.exists()


def test_o_commit_do_fechamento_roda_sob_o_rank_sqlite() -> None:
    gate = LaunchGate()
    seen: list[tuple[LockRank, ...]] = []
    inner: list[bool] = []

    def commit() -> None:
        seen.append(held_ranks())
        try:
            gate.acquire(0.05)  # SQLite → gate seria a ordem inversa
        except LockOrderViolation:
            inner.append(True)

    assert gate.close_and_linearize(1.0, commit).linearized
    assert seen == [(LockRank.GATE, LockRank.SQLITE)] and inner == [True]
    assert held_ranks() == ()


def test_observer_que_tenta_o_gate_dentro_do_intent_e_recusado(tmp_path: Path) -> None:
    class Bad(Fake):
        gate: LaunchGate

        def intent(self, intent: LaunchIntent) -> object:
            self.gate.acquire(0.05)  # já segura o gate: viola a ordem
            return 1

    obs = Bad()
    scope = scope_of(obs)
    obs.gate = scope.gate
    with install_scope(scope):
        result = run(make_spec(tmp_path, SENTINEL_SCRIPT, str(tmp_path / "nunca")))
    assert result.detail == "launch_refused:lock_order"
    assert not (tmp_path / "nunca").exists() and gate_free(scope.gate)


# --- 5. falhas do observer


def test_falha_em_intent_recusa_sem_processo_e_libera_o_gate(tmp_path: Path) -> None:
    obs = Fake(fail_at={"intent"})
    scope = scope_of(obs)
    sentinel = tmp_path / "nunca"
    with install_scope(scope):
        result = run(make_spec(tmp_path, SENTINEL_SCRIPT, str(sentinel)))
    assert result.outcome is ProcessOutcome.SUPERVISION_FAILED
    assert result.detail == "launch_refused:intent_failed" and result.tree_confirmed_dead
    assert obs.names() == ["intent"] and not sentinel.exists() and gate_free(scope.gate)


@os_process
def test_falha_em_created_aborta_o_processo_e_registra_ledger_write_failed(tmp_path: Path) -> None:
    obs = Fake(fail_at={"created"})
    scope = scope_of(obs)
    sentinel = tmp_path / "sentinela"
    with install_scope(scope):
        result = run(make_spec(tmp_path, SENTINEL_SCRIPT, str(sentinel)))
    assert result.outcome is ProcessOutcome.SUPERVISION_FAILED and result.tree_confirmed_dead
    assert obs.names() == ["intent", "created", "ended"]
    # o ledger nunca viu `created` (a gravação falhou): o fim é "não comprovado", não "morto"
    assert obs.payloads("ended") == [LaunchEnd.LEDGER_WRITE_FAILED]
    if IS_WINDOWS:
        assert not sentinel.exists()  # abortado ainda suspenso: nenhuma instrução rodou
    assert gate_free(scope.gate)


@os_process
def test_falha_em_contained_aborta_antes_do_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    obs = Fake(fail_at={"contained"})
    scope = scope_of(obs)
    sentinel = tmp_path / "sentinela"
    resumes: list[int] = []
    if IS_WINDOWS:
        real = backend._resume

        def counting(pid: int) -> None:
            resumes.append(pid)
            real(pid)

        monkeypatch.setattr(backend, "_resume", counting)
    with install_scope(scope):
        result = run(make_spec(tmp_path, SENTINEL_SCRIPT, str(sentinel)))
    assert result.outcome is ProcessOutcome.SUPERVISION_FAILED and result.tree_confirmed_dead
    assert obs.names() == ["intent", "created", "contained", "ended"]
    # `contained` falhou, mas `created` foi gravado e a árvore foi confirmada morta
    assert obs.payloads("ended") == [LaunchEnd.TREE_CONFIRMED_DEAD]
    if IS_WINDOWS:
        assert resumes == [] and not sentinel.exists()
    assert gate_free(scope.gate)


@os_process
def test_falha_em_ended_nunca_parece_sucesso(tmp_path: Path) -> None:
    obs = Fake(fail_at={"ended"})
    scope = scope_of(obs)
    sentinel = tmp_path / "sentinela"
    with install_scope(scope):
        result = run(make_spec(tmp_path, SENTINEL_SCRIPT, str(sentinel)))
    assert sentinel.exists()  # o processo rodou e terminou bem...
    assert result.outcome is ProcessOutcome.SUPERVISION_FAILED  # ...mas o ledger ficou aberto
    assert result.exit_code is None and result.tree_confirmed_dead
    assert result.detail is not None and "launch_ledger_end_failed" in result.detail
    assert "exited(0)" in result.detail
    assert gate_free(scope.gate)


def test_falha_em_ended_na_recusa_continua_recusa(tmp_path: Path) -> None:
    obs = Fake(fail_at={"ended"})
    reached, proceed = obs.pause_at("intent")
    scope = scope_of(obs)
    with install_scope(scope):
        launcher, box = in_thread(
            lambda: run(make_spec(tmp_path, SENTINEL_SCRIPT, str(tmp_path / "nunca")))
        )
        assert reached.wait(WAIT)
        scope.gate.request_stop()
        proceed.set()
        join(launcher)
    assert box[0].detail == "launch_refused:stopped" and not (tmp_path / "nunca").exists()
    assert gate_free(scope.gate)


def test_excecao_na_criacao_do_processo_registra_popen_raised(tmp_path: Path) -> None:
    missing = str(tmp_path / ("nao-existe.exe" if IS_WINDOWS else "nao-existe"))
    env = {k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "PATH")}
    spec = ProcessSpec(
        argv=(missing,),
        cwd=str(tmp_path),
        env=env,
        timeout_s=5,
        max_stdout_bytes=64,
        max_stderr_bytes=64,
    )
    obs = Fake()
    scope = scope_of(obs)
    with install_scope(scope):
        result = run(spec)
    assert result.outcome is ProcessOutcome.SUPERVISION_FAILED
    assert obs.names() == ["intent", "ended"]  # nunca houve `created`
    assert obs.payloads("ended") == [LaunchEnd.POPEN_RAISED]
    # POPEN_RAISED não é prova de ausência de processo: no contrato do I1 ele é NÃO confirmado
    assert tc.launch_edge_allowed(
        tc.LaunchPhase.INTENT, tc.LaunchPhase.ENDED_UNCONFIRMED, tc.EndBasis.POPEN_RAISED
    )
    assert not tc.launch_edge_allowed(
        tc.LaunchPhase.INTENT, tc.LaunchPhase.ENDED_CONFIRMED, tc.EndBasis.POPEN_RAISED
    )
    assert gate_free(scope.gate)


@windows_only
def test_windows_processo_criado_sem_confirmacao_de_contencao(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`created` gravado, associação ao Job NÃO confirmada: nunca `contained`, nunca `resume`."""
    resumes: list[int] = []

    def failing_assign(popen: Any, job: int) -> None:
        raise StartFailed("processo não confirmado dentro do Job", tree_confirmed_dead=False)

    monkeypatch.setattr(backend, "_assign", failing_assign)
    monkeypatch.setattr(backend, "_resume", lambda pid: resumes.append(pid))
    obs = Fake()
    scope = scope_of(obs)
    sentinel = tmp_path / "sentinela"
    with install_scope(scope):
        result = run(make_spec(tmp_path, SENTINEL_SCRIPT, str(sentinel)))
    assert result.outcome is ProcessOutcome.SUPERVISION_FAILED
    assert obs.names() == ["intent", "created", "ended"]  # sem `contained`
    assert resumes == [] and not sentinel.exists()
    # o processo suspenso foi morto e confirmado: CREATED → ended_confirmed é legal no I1
    assert obs.payloads("ended") == [LaunchEnd.TREE_CONFIRMED_DEAD]
    assert tc.launch_edge_allowed(
        tc.LaunchPhase.CREATED, tc.LaunchPhase.ENDED_CONFIRMED, tc.EndBasis.TREE_CONFIRMED_DEAD
    )


@windows_only
def test_windows_abort_sem_confirmacao_vira_tree_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_assign(popen: Any, job: int) -> None:
        raise StartFailed("falha", tree_confirmed_dead=False)

    real_abort = backend._abort
    monkeypatch.setattr(backend, "_assign", failing_assign)
    monkeypatch.setattr(backend, "_abort", lambda popen, job: (real_abort(popen, job), False)[1])
    obs = Fake()
    with install_scope(scope_of(obs)):
        result = run(make_spec(tmp_path))
    assert not result.tree_confirmed_dead
    assert obs.payloads("ended") == [LaunchEnd.TREE_UNCONFIRMED]  # nunca inventa "morto"


# --- 6. escopo ambiente


def test_escopo_ambiente_nao_vaza_nem_acumula(tmp_path: Path) -> None:
    obs = Fake()
    scope = scope_of(obs)
    with install_scope(scope) as installed:
        assert installed is scope and current_scope() is scope
        with pytest.raises(ScopeAlreadyInstalled), install_scope(scope_of(Fake())):
            pass
        assert current_scope() is scope  # a recusa não derrubou o escopo vigente
    assert current_scope() is None and scope.gate.stopped
    with pytest.raises(RuntimeError, match="boom"), install_scope(scope_of(Fake())):
        raise RuntimeError("boom")
    assert current_scope() is None  # removido mesmo por exceção
    with install_scope(scope_of(Fake())):  # e um novo pode ser instalado depois
        pass


def test_escopo_remove_o_registro_e_para_o_gate_ao_sair(tmp_path: Path) -> None:
    scope = scope_of(Fake())
    with install_scope(scope):
        pass
    # referência esquecida ao gate não volta a autorizar nada
    assert scope.gate.stopped
    result = run(make_spec(tmp_path, SENTINEL_SCRIPT, str(tmp_path / "legado")))
    assert result.outcome is ProcessOutcome.EXITED  # sem escopo: legado, nunca protegido
    assert (tmp_path / "legado").exists()


def test_escopo_valida_as_portas() -> None:
    with pytest.raises(TypeError):
        LaunchScope(object(), LaunchGate())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        LaunchScope(Fake(), object())  # type: ignore[arg-type]
    for bad in (0, -1, float("nan"), float("inf"), "1", True, None):
        with pytest.raises((ValueError, TypeError)):
            LaunchScope(Fake(), LaunchGate(), enter_timeout_s=bad)  # type: ignore[arg-type]
    with pytest.raises(TypeError), install_scope("escopo"):  # type: ignore[arg-type]
        pass


def test_fixture_de_conftest_detecta_vazamento() -> None:
    from app.process_runtime.launch import _reset_scope_for_tests

    assert _reset_scope_for_tests() is None
    cm = install_scope(scope_of(Fake()))
    cm.__enter__()
    leaked = _reset_scope_for_tests()  # simula o que o fixture autouse faria
    assert leaked is not None and current_scope() is None
    cm.__exit__(None, None, None)


# --- 7. vocabulário e I1


def test_vocabulario_neutro_e_identico_ao_do_contrato_do_i1() -> None:
    assert {m.value for m in LaunchEnd} == {m.value for m in tc.EndBasis}
    assert {m.value for m in LaunchPlatform} == {m.value for m in tc.Platform}
    assert {launch_module.PROFILE_WIN_JOB_V1, launch_module.PROFILE_POSIX_PGRP_V1} == {
        m.value for m in tc.ContainmentProfile
    }


def test_ticket_do_supervisor_e_o_do_i1_tem_a_mesma_regra() -> None:
    for make in (tc.LaunchTicket, launch_module._Ticket):
        ticket = make()
        assert ticket.refuse().value == "refused_before_creation"
        with pytest.raises(RuntimeError):
            ticket.refuse()
        with pytest.raises(RuntimeError):
            ticket.cross()
        crossed = make()
        crossed.cross()
        with pytest.raises(RuntimeError):
            crossed.refuse()


def test_intent_e_identidade_validam_o_que_recebem() -> None:
    with pytest.raises(TypeError):
        LaunchIntent("", LaunchPlatform.WIN32, launch_module.PROFILE_WIN_JOB_V1)
    with pytest.raises(TypeError):
        LaunchIntent("x", "win32", launch_module.PROFILE_WIN_JOB_V1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        LaunchIntent("x", LaunchPlatform.WIN32, "outro")
    for bad in (0, True, "1", None):
        with pytest.raises(TypeError):
            ProcessIdentity(bad, "t")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ProcessIdentity(1, "")
    # o intent nunca carrega argv, cwd nem ambiente
    assert set(LaunchIntent.__dataclass_fields__) == {"exe_name", "platform", "profile"}
    assert launch_module.exe_name_of("C:\\a\\b\\python.exe") == "python.exe"
    assert launch_module.exe_name_of("/usr/bin/python3") == "python3"


def test_so_o_process_runtime_importa_o_modulo_de_lancamento() -> None:
    """I2 não está integrado: nenhum módulo fora de `process_runtime` importa `launch`."""
    for path in APP_ROOT.rglob("*.py"):
        if path.is_relative_to(APP_ROOT / "process_runtime"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            elif isinstance(node, ast.Import):
                names.extend(a.name for a in node.names)
            for name in names:
                assert not name.startswith("app.process_runtime.launch"), (path.name, name)


# --- 8. crash isolado


def _read_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.TerminateProcess.restype = wintypes.BOOL
    _k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.GetProcessTimes.restype = wintypes.BOOL
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4

    _SYNCHRONIZE = 0x00100000
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _WAIT_OBJECT_0, _WAIT_TIMEOUT = 0x0, 0x102

    def pid_alive(pid: int) -> bool:
        handle = _k32.OpenProcess(_SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return bool(_k32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT)
        finally:
            _k32.CloseHandle(handle)

    def kill_created(pid: int, token: str, timeout_s: float = 10.0) -> bool:
        """Encerra **somente** o processo que este teste criou: PID + FILETIME de criação.

        Nada por nome de imagem nem por utilitário externo. Se o PID já morreu (ou foi
        reutilizado por outro processo, com FILETIME diferente), não toca em nada. `True` só
        com a morte confirmada pelo próprio handle do processo.
        """
        access = _SYNCHRONIZE | _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION
        handle = _k32.OpenProcess(access, False, pid)
        if not handle:
            return not pid_alive(pid)
        try:
            created = wintypes.FILETIME()
            ignored = [wintypes.FILETIME() for _ in range(3)]
            if not _k32.GetProcessTimes(handle, created, *ignored):
                return False  # sem identidade verificável, não encerra
            if str((created.dwHighDateTime << 32) | created.dwLowDateTime) != token:
                return not pid_alive(pid)  # outro processo ocupa o PID: não é o nosso
            if _k32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT:
                _k32.TerminateProcess(handle, 1)
            return bool(_k32.WaitForSingleObject(handle, int(timeout_s * 1000)) == _WAIT_OBJECT_0)
        finally:
            _k32.CloseHandle(handle)

else:

    def pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def kill_created(pid: int, token: str, timeout_s: float = 10.0) -> bool:
        """POSIX: encerra o grupo que o próprio teste criou (o PID do raiz é o pgid)."""
        import signal

        sig: Any = signal
        os_any: Any = os
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os_any.killpg(pid, sig.SIGKILL)
        return _wait_dead(pid, timeout_s)


def _wait_dead(pid: int, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return not pid_alive(pid)


def _crash(tmp_path: Path, point: str) -> tuple[list[dict[str, Any]], Path, int]:
    ledger, sentinel = tmp_path / "ledger.jsonl", tmp_path / "sentinela"
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(WORKER), str(ledger), str(sentinel), point],
        cwd=str(API_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(API_ROOT)},
    )
    return _read_ledger(ledger), sentinel, proc.returncode


def _kinds(rows: list[dict[str, Any]]) -> list[str]:
    return [row["kind"] for row in rows]


@os_process
def test_crash_depois_do_intent_nao_deixa_processo_nem_pid(tmp_path: Path) -> None:
    rows, sentinel, code = _crash(tmp_path, "after_intent")
    assert code == 77 and _kinds(rows) == ["intent"]
    assert not sentinel.exists()  # sem pid, sem processo observável — e o ledger NÃO prova isso:
    # `intent` sem `created` é incerteza durável, nunca ausência provada (I1 → UNPROVEN)
    row = tc.LaunchRecord(
        1,
        tc.PhaseTag.TEST_RUNNER,
        tc.Platform.WIN32 if IS_WINDOWS else tc.Platform.POSIX,
        tc.ContainmentProfile.WIN_JOB_V1 if IS_WINDOWS else tc.ContainmentProfile.POSIX_PGRP_V1,
        tc.LaunchPhase.INTENT,
        1,
    )
    assert row.phase is tc.LaunchPhase.INTENT


@windows_only
def test_windows_crash_depois_de_created_deixa_orfao_suspenso_fora_do_job(tmp_path: Path) -> None:
    """A janela real do achado P0-3: `created` gravado, associação ao Job ainda não feita."""
    rows, sentinel, code = _crash(tmp_path, "after_created")
    assert code == 77 and _kinds(rows) == ["intent", "created"]
    pid, token = rows[1]["pid"], rows[1]["token"]
    try:
        assert pid_alive(pid), "o órfão suspenso deveria sobreviver: não está no Job"
        assert not sentinel.exists()  # nunca foi retomado: nenhuma instrução rodou
        assert token.isdigit()
    finally:
        assert kill_created(pid, token), "o órfão do teste não foi encerrado"
    assert _wait_dead(pid)


@windows_only
def test_windows_crash_depois_de_contained_antes_do_resume_morre_com_o_job(tmp_path: Path) -> None:
    rows, sentinel, code = _crash(tmp_path, "after_contained")
    assert code == 77 and _kinds(rows) == ["intent", "created", "contained"]
    pid, token = rows[1]["pid"], rows[1]["token"]
    try:
        assert _wait_dead(pid), "KILL_ON_JOB_CLOSE deveria encerrar o processo contido"
        assert not sentinel.exists()
    finally:
        assert kill_created(pid, token)


@windows_only
def test_windows_crash_durante_a_execucao_o_job_encerra_a_arvore(tmp_path: Path) -> None:
    rows, sentinel, code = _crash(tmp_path, "during_run")
    assert code == 77 and _kinds(rows) == ["intent", "created", "contained"]  # sem `ended`
    pid, token = rows[1]["pid"], rows[1]["token"]
    try:
        assert sentinel.exists() and _wait_dead(pid)
    finally:
        assert kill_created(pid, token)


@linux_only
@pytest.mark.parametrize("point", ["after_created", "after_contained", "during_run"])
def test_linux_crash_nao_encerra_o_processo_e_o_ledger_nao_prova_nada(
    tmp_path: Path, point: str
) -> None:
    """POSIX não tem equivalente a KILL_ON_JOB_CLOSE: o processo sobrevive ao crash do dono."""
    rows, _sentinel, code = _crash(tmp_path, point)
    assert code == 77 and "ended" not in _kinds(rows)
    pid, token = rows[1]["pid"], rows[1]["token"]
    try:
        assert pid_alive(pid)
    finally:
        assert kill_created(pid, token)
    assert _wait_dead(pid)


def test_o_prazo_informado_ao_gate_e_o_prazo_honrado() -> None:
    """Curto não espera o padrão; longo espera até o dono soltar (nem 1 s fixo nem sem limite)."""
    gate = LaunchGate()
    held, release = threading.Event(), threading.Event()

    def holder() -> None:
        assert gate.acquire(5.0)
        held.set()
        release.wait(WAIT)
        gate.release()

    thread, _ = in_thread(holder)
    assert held.wait(WAIT)
    started = time.monotonic()
    assert gate.acquire(0.1) is False
    assert time.monotonic() - started < 0.6  # respeitou 0,1 s (não esperou 1 s)
    threading.Timer(1.2, release.set).start()
    started = time.monotonic()
    assert gate.acquire(8.0) is True  # esperou o dono soltar (> 1 s) sem estourar
    elapsed = time.monotonic() - started
    gate.release()
    join(thread)
    assert 0.8 < elapsed < 6.0
    for bad in (0, -1, float("nan"), float("inf"), "1", True, None):
        with pytest.raises(ValueError):
            gate.acquire(bad)  # type: ignore[arg-type]
    assert held_ranks() == ()
