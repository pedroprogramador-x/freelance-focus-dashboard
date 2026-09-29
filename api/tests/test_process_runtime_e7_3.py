"""E7.3 — Supervisor de Processos (`app.process_runtime`).

Três grupos, marcados no nome da seção:

* **Unitário** — validação de `ProcessSpec`/`ProcessResult` e precedência de eventos com um
  backend falso (sem processo real).
* **Integração com o SO** — processos reais (`sys.executable` + scripts deste arquivo), no
  Windows (Job Object) e no Linux (grupo de processos). A árvore é verificada **por PID**,
  com handles/identidades capturados enquanto os processos estão vivos, e o critério é
  sempre **zero** sobreviventes — nunca uma contagem exata (o `python.exe` de venv no
  Windows é um lançador que cria outro processo, e o `conhost.exe` do console também vive
  no Job).
* **Específico de SO** — `KILL_ON_JOB_CLOSE` e `CREATE_BREAKAWAY_FROM_JOB` (Windows);
  `SIGTERM`/`SIGKILL` e `setsid` (Linux).

Nenhum provider participa. Os testes de processo real exigem Windows ou Linux (`/proc`).
"""

from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

from app.process_runtime import (
    InvalidProcessSpec,
    ProcessOutcome,
    ProcessResult,
    ProcessSpec,
    run_supervised,
)
from app.process_runtime import supervisor as supervisor_module
from app.process_runtime.contracts import StartFailed

if sys.platform == "win32":
    from app.process_runtime import _windows as backend_module
else:
    from app.process_runtime import _posix as backend_module

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
API_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

os_process = pytest.mark.skipif(
    not (IS_WINDOWS or IS_LINUX), reason="verificação de PID exige Windows ou Linux (/proc)"
)
windows_only = pytest.mark.skipif(not IS_WINDOWS, reason="comportamento específico do Windows")
linux_only = pytest.mark.skipif(not IS_LINUX, reason="comportamento específico do POSIX/Linux")

# ============================================================== observação de PID por SO

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.TerminateProcess.restype = wintypes.BOOL
    _k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.GetCurrentProcess.argtypes = []
    _k32.GetProcessHandleCount.restype = wintypes.BOOL
    _k32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, wintypes.PDWORD]

    _SYNCHRONIZE = 0x00100000
    _PROCESS_TERMINATE = 0x0001
    _WAIT_TIMEOUT = 0x102

    class Watch:
        """Handle aberto enquanto o processo vive: identidade exata, sem reuso de PID."""

        def __init__(self, pid: int) -> None:
            self.pid = pid
            self._handle = _k32.OpenProcess(_SYNCHRONIZE | _PROCESS_TERMINATE, False, pid)
            assert self._handle, f"não foi possível observar o pid {pid}"

        def alive(self) -> bool:
            return bool(_k32.WaitForSingleObject(self._handle, 0) == _WAIT_TIMEOUT)

        def kill(self) -> None:
            _k32.TerminateProcess(self._handle, 1)

        def close(self) -> None:
            if self._handle:
                _k32.CloseHandle(self._handle)
                self._handle = None

    _k32.GetProcessId.restype = wintypes.DWORD
    _k32.GetProcessId.argtypes = [wintypes.HANDLE]

    def process_id_of(handle: Any) -> int:
        return int(_k32.GetProcessId(handle))

    def handle_count() -> int:
        count = wintypes.DWORD()
        assert _k32.GetProcessHandleCount(_k32.GetCurrentProcess(), ctypes.byref(count))
        return int(count.value)

else:
    import signal

    def _proc_stat(pid: int) -> tuple[str, str] | None:
        try:
            raw = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return None
        fields = raw[raw.rindex(")") + 2 :].split()
        return fields[0], fields[19]  # estado, starttime

    class Watch:
        """`starttime` de `/proc` capturado enquanto vivo: identidade sem reuso de PID."""

        def __init__(self, pid: int) -> None:
            self.pid = pid
            stat = _proc_stat(pid)
            assert stat is not None, f"não foi possível observar o pid {pid}"
            self._start = stat[1]

        def alive(self) -> bool:
            stat = _proc_stat(self.pid)
            return stat is not None and stat[1] == self._start and stat[0] not in ("Z", "X")

        def kill(self) -> None:
            if self.alive():
                os.kill(self.pid, signal.SIGKILL)

        def close(self) -> None:
            pass

    def handle_count() -> int:
        return len(os.listdir("/proc/self/fd"))


def eventually(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def watches() -> Iterator[dict[str, Watch]]:
    """Processos observados. No teardown, qualquer sobrevivente é morto (e o teste já falhou)."""
    observed: dict[str, Watch] = {}
    yield observed
    for watch in observed.values():
        if watch.alive():
            watch.kill()
        watch.close()


# ============================================================================= scripts

#: Árvore de profundidade configurável: `r` → `rc` → `rcc`. Cada nó grava o próprio PID
#: (atomicamente) em `<dir>/<nome>.pid`. Modo `stay` dorme; modo `exit` espera o arquivo
#: `go` e sai com 0 — "pai termina antes dos filhos".
TREE = """\
import os, subprocess, sys, time
d, name, depth, mode = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
flags = int(sys.argv[5]) if len(sys.argv) > 5 else 0
if depth > 0:
    try:
        subprocess.Popen([sys.executable, __file__, d, name + "c", str(depth - 1), "stay"],
                         creationflags=flags)
    except OSError as exc:
        open(os.path.join(d, "spawn-denied"), "w").write(repr(exc))
tmp = os.path.join(d, name + ".tmp")
with open(tmp, "w") as f:
    f.write(str(os.getpid()))
os.replace(tmp, os.path.join(d, name + ".pid"))
if mode == "exit":
    deadline = time.monotonic() + 60
    while not os.path.exists(os.path.join(d, "go")) and time.monotonic() < deadline:
        time.sleep(0.01)
    sys.exit(0)
time.sleep(120)
"""

ECHO_ARGV = "import json, sys\nsys.stdout.write(json.dumps(sys.argv[1:]))\n"

FLOOD = """\
import sys
out_total, err_total = int(sys.argv[1]), int(sys.argv[2])
block = b"o" * 65536
written = 0
while written < out_total:
    n = min(len(block), out_total - written)
    sys.stdout.buffer.write(block[:n]); written += n
sys.stdout.buffer.flush()
block = b"e" * 65536
written = 0
while written < err_total:
    n = min(len(block), err_total - written)
    sys.stderr.buffer.write(block[:n]); written += n
sys.stderr.buffer.flush()
"""


def child_env() -> dict[str, str]:
    """Ambiente mínimo para um `python` filho. `SYSTEMROOT` é indispensável no Windows."""
    keep = ("SYSTEMROOT", "PATH")
    return {key: os.environ[key] for key in keep if key in os.environ}


def script(tmp_path: Path, name: str, source: str) -> str:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return str(path)


def spec(
    tmp_path: Path,
    *args: str,
    timeout_s: float = 30.0,
    max_stdout_bytes: int = 1 << 20,
    max_stderr_bytes: int = 1 << 20,
    kill_grace_s: float = 2.0,
    env: dict[str, str] | None = None,
) -> ProcessSpec:
    return ProcessSpec(
        argv=(PYTHON, *args),
        cwd=str(tmp_path),
        env=child_env() if env is None else env,
        timeout_s=timeout_s,
        max_stdout_bytes=max_stdout_bytes,
        max_stderr_bytes=max_stderr_bytes,
        kill_grace_s=kill_grace_s,
    )


def never() -> bool:
    return False


def pids_ready(directory: Path, names: tuple[str, ...]) -> dict[str, int] | None:
    """PIDs gravados pelos scripts, ou `None` se algum ainda não existe. Tolerante à leitura
    concorrente com o `os.replace` do escritor (no Windows, violação de compartilhamento)."""
    found: dict[str, int] = {}
    for name in names:
        try:
            found[name] = int((directory / f"{name}.pid").read_text())
        except (OSError, ValueError):
            return None
    return found


def assert_clean_result(result: ProcessResult) -> None:
    assert result.tree_confirmed_dead, result.detail
    assert result.output_complete, result.detail


TREE_NAMES = ("r", "rc", "rcc")

# ================================================================ unitário: ProcessSpec


def test_spec_valida_e_congela_o_ambiente(tmp_path: Path) -> None:
    env = {"A": "1"}
    built = spec(tmp_path, "-c", "pass", env=env)
    env["A"] = "mudou"
    assert built.env["A"] == "1"
    assert isinstance(built.env, MappingProxyType)
    assert built.argv[0] == PYTHON
    assert built.kill_grace_s == 2.0


@pytest.mark.parametrize("argv", [[PYTHON], PYTHON, (), (PYTHON, 1), (PYTHON, "a\x00b")])
def test_spec_recusa_argv_nao_estruturado(tmp_path: Path, argv: Any) -> None:
    with pytest.raises(InvalidProcessSpec):
        ProcessSpec(
            argv=argv,
            cwd=str(tmp_path),
            env={},
            timeout_s=1,
            max_stdout_bytes=1,
            max_stderr_bytes=1,
        )


@pytest.mark.parametrize(
    "argv0",
    ["python", "bin/python", "./python", "", "C:python" if IS_WINDOWS else "~/python"]
    + (["\\Windows\\python.exe"] if IS_WINDOWS else []),
)
def test_spec_exige_executavel_absoluto(tmp_path: Path, argv0: str) -> None:
    with pytest.raises(InvalidProcessSpec):
        ProcessSpec(
            argv=(argv0,),
            cwd=str(tmp_path),
            env={},
            timeout_s=1,
            max_stdout_bytes=1,
            max_stderr_bytes=1,
        )


@pytest.mark.parametrize("name", ["x.bat", "X.CMD", "x.Bat", "x.bat.", "x.cmd  ", "x.cmd. ."])
def test_spec_recusa_bat_e_cmd(tmp_path: Path, name: str) -> None:
    """(J) `.bat`/`.cmd` iriam para o `cmd.exe`; recusados na construção, antes de qualquer
    processo — inclusive com os pontos/espaços finais que o Win32 descarta."""
    with pytest.raises(InvalidProcessSpec, match="bat/.cmd"):
        ProcessSpec(
            argv=(str(tmp_path / name),),
            cwd=str(tmp_path),
            env={},
            timeout_s=1,
            max_stdout_bytes=1,
            max_stderr_bytes=1,
        )


@pytest.mark.parametrize("cwd", ["relativo", ".", "", "C:relativo" if IS_WINDOWS else "~"])
def test_spec_exige_cwd_absoluto(cwd: str) -> None:
    with pytest.raises(InvalidProcessSpec):
        ProcessSpec(
            argv=(PYTHON,),
            cwd=cwd,
            env={},
            timeout_s=1,
            max_stdout_bytes=1,
            max_stderr_bytes=1,
        )


@pytest.mark.parametrize(
    "env", [None, [("A", "1")], {"A": 1}, {1: "a"}, {"": "a"}, {"A=B": "1"}, {"A": "x\x00"}]
)
def test_spec_exige_env_explicito_e_valido(tmp_path: Path, env: Any) -> None:
    with pytest.raises(InvalidProcessSpec):
        ProcessSpec(
            argv=(PYTHON,),
            cwd=str(tmp_path),
            env=env,
            timeout_s=1,
            max_stdout_bytes=1,
            max_stderr_bytes=1,
        )


@windows_only
def test_spec_recusa_chave_de_env_ambigua_no_windows(tmp_path: Path) -> None:
    with pytest.raises(InvalidProcessSpec, match="duplicada"):
        spec(tmp_path, env={"Path": "a", "PATH": "b"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_s", 0),
        ("timeout_s", -1),
        ("timeout_s", float("nan")),
        ("timeout_s", float("inf")),
        ("timeout_s", True),
        ("timeout_s", "1"),
        ("max_stdout_bytes", 0),
        ("max_stdout_bytes", 1.5),
        ("max_stdout_bytes", True),
        ("max_stderr_bytes", 0),
        ("max_stderr_bytes", -5),
        ("kill_grace_s", -0.1),
        ("kill_grace_s", float("nan")),
    ],
)
def test_spec_exige_limites_positivos(tmp_path: Path, field: str, value: Any) -> None:
    values: dict[str, Any] = {
        "timeout_s": 1,
        "max_stdout_bytes": 1,
        "max_stderr_bytes": 1,
        "kill_grace_s": 0,
    }
    values[field] = value
    with pytest.raises(InvalidProcessSpec):
        ProcessSpec(argv=(PYTHON,), cwd=str(tmp_path), env={}, **values)


def test_spec_aceita_argumentos_vazios_e_graca_zero(tmp_path: Path) -> None:
    built = spec(tmp_path, "", "x", kill_grace_s=0)
    assert built.argv[1:] == ("", "x")


# ============================================================== unitário: ProcessResult


def _result(**overrides: Any) -> ProcessResult:
    values: dict[str, Any] = {
        "outcome": ProcessOutcome.EXITED,
        "exit_code": 0,
        "stdout": b"",
        "stderr": b"",
        "stdout_truncated": False,
        "stderr_truncated": False,
        "output_complete": True,
        "duration_ms": 1,
        "tree_confirmed_dead": True,
        "orphans_killed": False,
    }
    values.update(overrides)
    return ProcessResult(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"exit_code": None},
        {"outcome": ProcessOutcome.TIMEOUT},
        {"outcome": ProcessOutcome.CANCELLED, "exit_code": 1},
        {"exit_code": True},
        {"stdout": "texto"},
        {"duration_ms": -1},
        {"tree_confirmed_dead": False},
        {"output_complete": False},
        {"outcome": ProcessOutcome.SUPERVISION_FAILED, "exit_code": None},
        {"outcome": ProcessOutcome.TIMEOUT, "exit_code": None, "orphans_killed": True},
    ],
)
def test_resultado_recusa_combinacoes_incoerentes(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _result(**overrides)


def test_resultado_so_admite_arvore_viva_em_supervision_failed() -> None:
    failed = _result(
        outcome=ProcessOutcome.SUPERVISION_FAILED,
        exit_code=None,
        tree_confirmed_dead=False,
        output_complete=False,
        detail="x",
    )
    assert not failed.tree_confirmed_dead


# ===================================================== unitário: precedência (backend falso)


class _FakePopen:
    def __init__(self, polls: list[int | None]) -> None:
        self._polls = polls
        self.returncode: int | None = None
        read_out, write_out = os.pipe()
        read_err, write_err = os.pipe()
        os.close(write_out)
        os.close(write_err)
        self.stdout = open(read_out, "rb")  # noqa: SIM115 — fechado pelo supervisor
        self.stderr = open(read_err, "rb")  # noqa: SIM115
        self.pid = -1

    def poll(self) -> int | None:
        if self._polls:
            value = self._polls.pop(0)
            if value is not None:
                self.returncode = value
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        time.sleep(min(timeout or 0.0, 0.001))
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout or 0.0)
        return self.returncode


class _FakeTree:
    def __init__(self, polls: list[int | None], *, confirm: bool = True) -> None:
        self.popen = _FakePopen(polls)
        self.kills = 0
        self.closed = 0
        self._confirm = confirm

    def orphans_alive(self) -> bool:
        return False

    def kill(self, grace_s: float) -> None:
        self.kills += 1

    def confirm_dead(self, timeout_s: float) -> bool:
        return self._confirm

    def close(self) -> None:
        self.closed += 1


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> Callable[[_FakeTree], None]:
    def install(tree: _FakeTree) -> None:
        def start(spec: ProcessSpec) -> _FakeTree:
            # Consome tempo real: um `timeout_s` minúsculo fica **garantidamente** vencido na
            # primeira volta, qualquer que seja a granularidade do relógio. (No Windows com o
            # Python 3.11 fixado pelo projeto, `time.get_clock_info("monotonic")` reporta
            # `GetTickCount64()` e resolução de 15,625 ms; a partir do Python 3.13 o
            # `monotonic` do Windows usa `QueryPerformanceCounter`. O teste não depende disso.)
            time.sleep(0.05)
            return tree

        backend = SimpleNamespace(
            start=start,
            readable_waiter=lambda fd: (lambda timeout: True),
        )
        monkeypatch.setattr(supervisor_module, "_backend", backend)

    return install


def _cancel_after_first_call() -> Callable[[], bool]:
    """Falso na checagem pré-início, verdadeiro daí em diante."""
    calls = iter([False])
    return lambda: next(calls, True)


def test_precedencia_saida_vence_cancelamento_e_timeout(
    tmp_path: Path, fake_backend: Callable[[_FakeTree], None]
) -> None:
    """Raiz já saiu, cancelamento pedido e prazo estourado ao mesmo tempo → EXITED."""
    tree = _FakeTree([7])
    fake_backend(tree)
    result = run_supervised(spec(tmp_path, timeout_s=1e-6), _cancel_after_first_call())
    assert (result.outcome, result.exit_code) == (ProcessOutcome.EXITED, 7)
    assert tree.kills == 1  # órfãos também são encerrados
    assert tree.closed == 1


def test_precedencia_cancelamento_vence_timeout(
    tmp_path: Path, fake_backend: Callable[[_FakeTree], None]
) -> None:
    tree = _FakeTree([])
    fake_backend(tree)
    result = run_supervised(spec(tmp_path, timeout_s=1e-6), _cancel_after_first_call())
    assert result.outcome is ProcessOutcome.CANCELLED
    assert result.exit_code is None
    assert tree.kills == 1


def test_precedencia_saida_no_ponto_de_decisao_vence_cancelamento(
    tmp_path: Path, fake_backend: Callable[[_FakeTree], None]
) -> None:
    """(O) O raiz sai entre a checagem do laço e o primeiro sinal: a saída foi confirmada
    antes do término, então o cancelamento não muda o desfecho."""
    tree = _FakeTree([None, 0])
    fake_backend(tree)
    result = run_supervised(spec(tmp_path), _cancel_after_first_call())
    assert (result.outcome, result.exit_code) == (ProcessOutcome.EXITED, 0)


def test_timeout_sem_cancelamento(
    tmp_path: Path, fake_backend: Callable[[_FakeTree], None]
) -> None:
    tree = _FakeTree([])
    fake_backend(tree)
    result = run_supervised(spec(tmp_path, timeout_s=0.05), never)
    assert result.outcome is ProcessOutcome.TIMEOUT
    assert result.duration_ms >= 50


@pytest.mark.parametrize("answer", [RuntimeError("boom"), "sim", 1])
def test_is_cancelled_invalido_falha_fechado(
    tmp_path: Path, fake_backend: Callable[[_FakeTree], None], answer: object
) -> None:
    tree = _FakeTree([])
    fake_backend(tree)
    calls = iter([False])

    def is_cancelled() -> Any:
        value = next(calls, answer)
        if isinstance(value, Exception):
            raise value
        return value

    result = run_supervised(spec(tmp_path), is_cancelled)
    assert result.outcome is ProcessOutcome.SUPERVISION_FAILED
    assert result.detail and "supervisão" in result.detail
    assert tree.kills >= 1  # a árvore é encerrada mesmo assim


def test_arvore_nao_confirmada_vira_supervision_failed(
    tmp_path: Path, fake_backend: Callable[[_FakeTree], None]
) -> None:
    """Um resultado "bom" nunca esconde sobrevivente."""
    tree = _FakeTree([0], confirm=False)
    fake_backend(tree)
    result = run_supervised(spec(tmp_path), never)
    assert result.outcome is ProcessOutcome.SUPERVISION_FAILED
    assert result.exit_code is None
    assert not result.tree_confirmed_dead
    assert result.detail and "não confirmada" in result.detail and "exited(0)" in result.detail


def test_cancelamento_antes_do_inicio_nao_cria_processo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(N)"""

    def forbidden(spec: ProcessSpec) -> None:
        raise AssertionError("nenhum processo pode ser criado")

    monkeypatch.setattr(backend_module, "start", forbidden)
    result = run_supervised(spec(tmp_path, "-c", "pass"), lambda: True)
    assert result.outcome is ProcessOutcome.CANCELLED
    assert (result.exit_code, result.duration_ms, result.stdout) == (None, 0, b"")
    assert result.tree_confirmed_dead


def test_falha_de_inicio_vira_supervision_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(spec: ProcessSpec) -> None:
        raise StartFailed("AssignProcessToJobObject falhou", tree_confirmed_dead=True)

    monkeypatch.setattr(backend_module, "start", refuse)
    result = run_supervised(spec(tmp_path, "-c", "pass"), never)
    assert result.outcome is ProcessOutcome.SUPERVISION_FAILED
    assert result.detail == "AssignProcessToJobObject falhou"


def test_uso_incorreto_levanta_type_error(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        run_supervised("python", never)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        run_supervised(spec(tmp_path), None)  # type: ignore[arg-type]


def test_leitor_nunca_guarda_mais_que_o_limite(monkeypatch: pytest.MonkeyPatch) -> None:
    """(L/M) O limite vale **durante** a leitura: o buffer nunca passa de `limit`, e o
    escritor não bloqueia porque o excedente continua sendo drenado."""
    monkeypatch.setattr(supervisor_module, "_READ_CHUNK", 4096)
    read_fd, write_fd = os.pipe()
    stream = open(read_fd, "rb")  # noqa: SIM115 — fechado por `finish`
    reader = supervisor_module._Reader(stream, 1000, "teste")
    peak = 0
    total = 8 * 1024 * 1024

    def write_all() -> None:
        with open(write_fd, "wb") as out:
            block = b"x" * 65536
            for _ in range(total // len(block)):
                out.write(block)

    writer = threading.Thread(target=write_all)
    reader.start()
    writer.start()
    while writer.is_alive():
        peak = max(peak, len(reader._buffer))
        time.sleep(0.0005)
    writer.join()
    reader.finish(time.monotonic() + 5)
    assert peak <= 1000
    assert reader.data() == b"x" * 1000
    assert reader.truncated and reader.eof
    assert stream.closed


# ====================================================== integração com o SO: básicos


@os_process
def test_processo_simples_conclui(tmp_path: Path) -> None:
    """(A)"""
    code = "import sys; sys.stdout.write('ok'); sys.stderr.write('aviso')"
    result = run_supervised(spec(tmp_path, "-c", code), never)
    assert result.outcome is ProcessOutcome.EXITED
    assert result.exit_code == 0
    assert (result.stdout, result.stderr) == (b"ok", b"aviso")
    assert not (result.stdout_truncated or result.stderr_truncated)
    assert not result.orphans_killed
    assert_clean_result(result)


@os_process
def test_timeout_mata_o_processo(tmp_path: Path, watches: dict[str, Watch]) -> None:
    """(B) O processo é observado vivo antes do prazo e está morto depois."""
    sleeper = script(tmp_path, "tree.py", TREE)

    def observe() -> bool:
        pids = pids_ready(tmp_path, ("r",))
        if pids and "r" not in watches:
            watches["r"] = Watch(pids["r"])
        return False

    started = time.monotonic()
    result = run_supervised(
        spec(tmp_path, sleeper, str(tmp_path), "r", "0", "stay", timeout_s=3), observe
    )
    assert result.outcome is ProcessOutcome.TIMEOUT
    assert result.exit_code is None
    assert 3000 <= result.duration_ms < 20000
    assert time.monotonic() - started < 20
    assert_clean_result(result)
    assert "r" in watches, "o processo não chegou a ser observado vivo"
    assert not watches["r"].alive()  # confirmado = já sinalizado, sem espera


@os_process
def test_exit_code_diferente_de_zero(tmp_path: Path) -> None:
    """(G)"""
    result = run_supervised(spec(tmp_path, "-c", "import sys; sys.exit(7)"), never)
    assert (result.outcome, result.exit_code) == (ProcessOutcome.EXITED, 7)
    assert_clean_result(result)


@os_process
def test_executavel_inexistente(tmp_path: Path) -> None:
    """(H)"""
    missing = str(tmp_path / ("nao-existe.exe" if IS_WINDOWS else "nao-existe"))
    built = ProcessSpec(
        argv=(missing,),
        cwd=str(tmp_path),
        env=child_env(),
        timeout_s=5,
        max_stdout_bytes=10,
        max_stderr_bytes=10,
    )
    result = run_supervised(built, never)
    assert result.outcome is ProcessOutcome.SUPERVISION_FAILED
    assert result.tree_confirmed_dead
    assert result.detail and "FileNotFoundError" in result.detail


@os_process
def test_cwd_inexistente(tmp_path: Path) -> None:
    """(I)"""
    built = ProcessSpec(
        argv=(PYTHON, "-c", "pass"),
        cwd=str(tmp_path / "nao-existe"),
        env=child_env(),
        timeout_s=5,
        max_stdout_bytes=10,
        max_stderr_bytes=10,
    )
    result = run_supervised(built, never)
    assert result.outcome is ProcessOutcome.SUPERVISION_FAILED
    assert result.tree_confirmed_dead
    assert result.detail and "processo não pôde ser criado" in result.detail


@os_process
def test_argv_estruturado_e_entregue_literalmente(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(K) e (H/I do plano) — sem shell, `argv` em lista, `executable` absoluto."""
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    real_popen = subprocess.Popen

    class SpyPopen(real_popen):  # type: ignore[valid-type, misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            calls.append((args, kwargs))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", SpyPopen)
    tricky = (
        "com espaço",
        'aspas "duplas"',
        "'simples'",
        "a&b",
        "a|b",
        "a;b",
        "%PATH%",
        "$HOME",
        "`whoami`",
        "ünïcødé ✓ 日本",
        "",
        "barra\\",
        "duas barras\\\\",
        'meio\\"fim',
        "*.py",
    )
    echo = script(tmp_path, "echo.py", ECHO_ARGV)
    result = run_supervised(spec(tmp_path, echo, *tricky), never)
    assert result.outcome is ProcessOutcome.EXITED, result.detail
    assert json.loads(result.stdout) == list(tricky)
    (args, kwargs), *_ = calls
    assert isinstance(args[0], list) and tuple(args[0]) == (PYTHON, echo, *tricky)
    assert kwargs["executable"] == PYTHON
    assert "shell" not in kwargs  # padrão do `subprocess`: desligado


@os_process
def test_ambiente_nao_e_herdado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FF_E7_3_SEGREDO_DE_TESTE", "vazou")
    code = "import os, sys; sys.stdout.write(os.environ.get('FF_E7_3_SEGREDO_DE_TESTE', '-'))"
    result = run_supervised(spec(tmp_path, "-c", code), never)
    assert result.stdout == b"-"


# ================================================ integração com o SO: saída limitada


@os_process
def test_stdout_e_stderr_muito_maiores_que_o_limite(tmp_path: Path) -> None:
    """(L/M) 4 MiB e 3 MiB contra limites de 1000 e 777 bytes: truncado, sem bloquear."""
    flood = script(tmp_path, "flood.py", FLOOD)
    built = spec(
        tmp_path,
        flood,
        str(4 * 1024 * 1024),
        str(3 * 1024 * 1024),
        max_stdout_bytes=1000,
        max_stderr_bytes=777,
    )
    result = run_supervised(built, never)
    assert (result.outcome, result.exit_code) == (ProcessOutcome.EXITED, 0), result.detail
    assert result.stdout == b"o" * 1000 and result.stdout_truncated
    assert result.stderr == b"e" * 777 and result.stderr_truncated
    assert_clean_result(result)


@os_process
def test_saida_exatamente_no_limite_nao_e_truncada(tmp_path: Path) -> None:
    flood = script(tmp_path, "flood.py", FLOOD)
    built = spec(tmp_path, flood, "5000", "0", max_stdout_bytes=5000, max_stderr_bytes=1)
    result = run_supervised(built, never)
    assert result.stdout == b"o" * 5000
    assert not result.stdout_truncated and not result.stderr_truncated


# =================================================== integração com o SO: árvore


def _tree_observer(
    directory: Path, watches: dict[str, Watch], *, then: Callable[[], bool]
) -> Callable[[], bool]:
    """`is_cancelled` que abre `Watch` de r/rc/rcc assim que existem, depois delega."""

    def is_cancelled() -> bool:
        if not watches:
            pids = pids_ready(directory, TREE_NAMES)
            if pids is None:
                return False
            for name, pid in pids.items():
                watches[name] = Watch(pid)
        return then()

    return is_cancelled


@os_process
def test_cancelamento_encerra_raiz_filho_e_neto(tmp_path: Path, watches: dict[str, Watch]) -> None:
    """(C) Zero descendentes vivos, verificado por PID."""
    tree = script(tmp_path, "tree.py", TREE)
    is_cancelled = _tree_observer(tmp_path, watches, then=lambda: True)
    result = run_supervised(
        spec(tmp_path, tree, str(tmp_path), "r", "2", "stay", timeout_s=60), is_cancelled
    )
    assert result.outcome is ProcessOutcome.CANCELLED, result.detail
    assert_clean_result(result)
    assert set(watches) == set(TREE_NAMES)
    assert not any(w.alive() for w in watches.values())  # sem espera adicional


@os_process
def test_pai_termina_antes_dos_filhos(tmp_path: Path, watches: dict[str, Watch]) -> None:
    """(D) O raiz sai com 0 deixando filho e neto; ambos são encerrados."""
    tree = script(tmp_path, "tree.py", TREE)

    def release() -> bool:
        (tmp_path / "go").write_text("1")
        return False

    is_cancelled = _tree_observer(tmp_path, watches, then=release)
    result = run_supervised(
        spec(tmp_path, tree, str(tmp_path), "r", "2", "exit", timeout_s=60), is_cancelled
    )
    assert (result.outcome, result.exit_code) == (ProcessOutcome.EXITED, 0), result.detail
    assert result.orphans_killed
    assert_clean_result(result)
    assert set(watches) == set(TREE_NAMES)
    assert not any(w.alive() for w in watches.values())  # sem espera adicional


@os_process
def test_cancelamento_concorrente_com_termino_normal(tmp_path: Path) -> None:
    """(E) Cancelamento em instantes variados ao redor da saída natural: o desfecho é
    sempre EXITED(0) ou CANCELLED, e a árvore sempre morre."""
    code = "import sys, time; time.sleep(float(sys.argv[1]))"
    seen: set[ProcessOutcome] = set()
    for index in range(12):
        sleep_s = 0.02 * (index % 4)
        cancel_after = 0.05 * index
        started = time.monotonic()
        result = run_supervised(
            spec(tmp_path, "-c", code, str(sleep_s), timeout_s=30),
            lambda: time.monotonic() - started >= cancel_after,  # noqa: B023
        )
        assert result.outcome in (ProcessOutcome.EXITED, ProcessOutcome.CANCELLED)
        if result.outcome is ProcessOutcome.EXITED:
            assert result.exit_code == 0
        else:
            assert result.exit_code is None
        assert_clean_result(result)
        seen.add(result.outcome)
    # Índice 0 cancela já na consulta anterior ao início; os demais variam com a máquina.
    assert ProcessOutcome.CANCELLED in seen


@os_process
def test_timeout_e_cancelamento_simultaneos(tmp_path: Path) -> None:
    """(F) Os dois de uma vez: vence o cancelamento, a árvore morre uma vez só."""
    code = "import time; time.sleep(30)"
    result = run_supervised(spec(tmp_path, "-c", code, timeout_s=1e-6), _cancel_after_first_call())
    assert result.outcome is ProcessOutcome.CANCELLED
    assert_clean_result(result)


@os_process
def test_cancelamento_depois_do_termino_nao_altera_o_resultado(tmp_path: Path) -> None:
    """(O) Depois de devolvido, o resultado é final: o token não é mais consultado."""
    calls: list[float] = []
    cancel = [False]

    def is_cancelled() -> bool:
        calls.append(time.monotonic())
        return cancel[0]

    result = run_supervised(spec(tmp_path, "-c", "pass"), is_cancelled)
    consulted = len(calls)
    cancel[0] = True
    time.sleep(0.1)
    assert result.outcome is ProcessOutcome.EXITED
    assert len(calls) == consulted


@os_process
def test_encerramento_e_idempotente(tmp_path: Path, watches: dict[str, Watch]) -> None:
    """(F) `kill`, `confirm_dead` e `close` repetidos no backend real."""
    backend = backend_module
    tree = script(tmp_path, "tree.py", TREE)
    handle = backend.start(spec(tmp_path, tree, str(tmp_path), "r", "2", "stay"))
    try:
        assert eventually(lambda: pids_ready(tmp_path, TREE_NAMES) is not None)
        for name, pid in (pids_ready(tmp_path, TREE_NAMES) or {}).items():
            watches[name] = Watch(pid)
        handle.kill(0.5)
        handle.kill(0.5)
        assert handle.confirm_dead(10)
        assert handle.confirm_dead(10)
        handle.kill(0.5)
        # Confirmado = objetos de processo sinalizados, sem espera adicional.
        assert not any(w.alive() for w in watches.values())
    finally:
        handle.close()
        handle.close()
        for stream in (handle.popen.stdout, handle.popen.stderr):
            if stream is not None:
                stream.close()


@os_process
def test_recursos_voltam_ao_patamar(tmp_path: Path) -> None:
    """Handles/fds e threads leitoras não vazam entre execuções."""
    fast = spec(tmp_path, "-c", "pass")
    slow = spec(tmp_path, "-c", "import time; time.sleep(30)", timeout_s=0.3)
    run_supervised(fast, never)  # aquecimento: imports, ctypes, cache do SO
    gc.collect()
    handles_before = handle_count()
    threads_before = threading.active_count()
    for _ in range(10):
        run_supervised(fast, never)
        run_supervised(slow, never)
    gc.collect()
    assert threading.active_count() == threads_before
    assert handle_count() - handles_before <= 10


# ================================================================ específicos do Windows


@windows_only
def test_supervisor_morto_leva_a_arvore_junto(tmp_path: Path, watches: dict[str, Watch]) -> None:
    """`KILL_ON_JOB_CLOSE`: o supervisor é morto sem cleanup e a árvore morre com ele."""
    tree = script(tmp_path, "tree.py", TREE)
    host = script(
        tmp_path,
        "host.py",
        "import os, sys\n"
        "from app.process_runtime import ProcessSpec, run_supervised\n"
        "d, tree = sys.argv[1], sys.argv[2]\n"
        "open(os.path.join(d, 'host.pid'), 'w').write(str(os.getpid()))\n"
        "spec = ProcessSpec(argv=(sys.executable, tree, d, 'r', '2', 'stay'), cwd=d,\n"
        "    env={'SYSTEMROOT': os.environ['SYSTEMROOT']}, timeout_s=120,\n"
        "    max_stdout_bytes=100, max_stderr_bytes=100)\n"
        "run_supervised(spec, lambda: False)\n",
    )
    env = {**child_env(), "PYTHONPATH": str(API_ROOT)}
    supervisor_process = subprocess.Popen(  # noqa: S603 — argv fixo do próprio teste
        [PYTHON, host, str(tmp_path), tree],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert eventually(lambda: pids_ready(tmp_path, TREE_NAMES) is not None, timeout=30)
        for name, pid in (pids_ready(tmp_path, TREE_NAMES) or {}).items():
            watches[name] = Watch(pid)
        assert all(w.alive() for w in watches.values())
        # O interpretador que segura o handle do Job — não o lançador do venv, se houver.
        host_watch = Watch(int((tmp_path / "host.pid").read_text()))
        try:
            host_watch.kill()  # TerminateProcess: nenhum finally do supervisor roda
            assert eventually(lambda: not host_watch.alive(), timeout=10)
        finally:
            host_watch.close()
        assert eventually(lambda: not any(w.alive() for w in watches.values()), timeout=10)
        supervisor_process.wait(timeout=10)
    finally:
        if supervisor_process.poll() is None:
            supervisor_process.kill()
            supervisor_process.wait(timeout=10)


@windows_only
def test_breakaway_do_job_nao_escapa(tmp_path: Path, watches: dict[str, Watch]) -> None:
    """Um filho criado com `CREATE_BREAKAWAY_FROM_JOB` continua morrendo com a árvore."""
    tree = script(tmp_path, "tree.py", TREE)
    create_breakaway_from_job = 0x01000000
    names = ("r", "rc")

    def is_cancelled() -> bool:
        if (tmp_path / "spawn-denied").exists():
            return True
        pids = pids_ready(tmp_path, names)
        if pids is None:
            return False
        for name, pid in pids.items():
            watches.setdefault(name, Watch(pid))
        return True

    result = run_supervised(
        spec(tmp_path, tree, str(tmp_path), "r", "1", "stay", str(create_breakaway_from_job)),
        is_cancelled,
    )
    assert result.outcome is ProcessOutcome.CANCELLED, result.detail
    assert_clean_result(result)
    assert not any(w.alive() for w in watches.values())  # sem espera adicional


# ============================================ Windows: regressão do P2 (evidência de membros)
#
# Auditoria E7.3, P2: a lista do Job podia ser aceita parcial, e falhas de `OpenProcess`/
# `IsProcessInJob` viravam "membro ausente" — `tree_confirmed_dead=True` sem evidência.
# Unitários com a Win32 falsificada por `monkeypatch` + integração com a árvore real.

if sys.platform == "win32":
    ERROR_ACCESS_DENIED = 5
    ERROR_INVALID_HANDLE = 6
    ERROR_INVALID_PARAMETER = 87
    ERROR_MORE_DATA = 234

    def _fake_pid_list_query(
        monkeypatch: pytest.MonkeyPatch, answer: Callable[[int], tuple[bool, int, int, int]]
    ) -> list[int]:
        """Troca a consulta `JOBOBJECT_BASIC_PROCESS_ID_LIST` por `answer(capacidade)` →
        `(ok, erro, atribuídos, listados)`. Devolve as capacidades consultadas, em ordem."""
        real = backend_module._QueryInformationJobObject
        capacities: list[int] = []

        def fake(job: Any, info_class: int, buffer: Any, size: int, returned: Any) -> int:
            if info_class != backend_module._JOB_OBJECT_BASIC_PROCESS_ID_LIST:
                return int(real(job, info_class, buffer, size, returned))
            info = buffer._obj
            capacity = len(info.ProcessIdList)
            capacities.append(capacity)
            ok, error, assigned, listed = answer(capacity)
            info.NumberOfAssignedProcesses = assigned
            info.NumberOfProcessIdsInList = listed
            for index in range(min(listed, capacity)):
                info.ProcessIdList[index] = 1000 + index
            ctypes.set_last_error(error)
            return int(ok)

        monkeypatch.setattr(backend_module, "_QueryInformationJobObject", fake)
        return capacities

    @windows_only
    def test_lista_parcial_com_sucesso_e_ampliada(monkeypatch: pytest.MonkeyPatch) -> None:
        """(A) Sucesso com `NumberOfProcessIdsInList < NumberOfAssignedProcesses` é lista
        parcial: o buffer cresce e a consulta se repete até a lista completa."""
        capacities = _fake_pid_list_query(
            monkeypatch, lambda capacity: (True, 0, 100, min(capacity, 100))
        )
        assert backend_module._job_process_ids(0) == [1000 + i for i in range(100)]
        assert len(capacities) == 2 and capacities[1] >= 100

    @windows_only
    def test_lista_com_error_more_data_e_ampliada(monkeypatch: pytest.MonkeyPatch) -> None:
        capacities = _fake_pid_list_query(
            monkeypatch,
            lambda capacity: (True, 0, 300, 300)
            if capacity >= 300
            else (False, ERROR_MORE_DATA, 300, 0),
        )
        assert len(backend_module._job_process_ids(0)) == 300
        assert capacities[-1] >= 300

    @pytest.mark.parametrize(
        "answer",
        [
            pytest.param(lambda c: (False, ERROR_ACCESS_DENIED, 0, 0), id="erro-da-api"),
            pytest.param(lambda c: (True, 0, 2, 3), id="listados-maior-que-atribuidos"),
            pytest.param(lambda c: (True, 0, c + 1, c), id="nunca-completa"),
            pytest.param(lambda c: (True, 0, (1 << 20) + 1, 0), id="acima-do-teto"),
        ],
    )
    @windows_only
    def test_lista_incompleta_ou_inconsistente_nunca_vira_sucesso(
        monkeypatch: pytest.MonkeyPatch, answer: Callable[[int], tuple[bool, int, int, int]]
    ) -> None:
        capacities = _fake_pid_list_query(monkeypatch, answer)
        with pytest.raises(OSError):
            backend_module._job_process_ids(0)
        assert len(capacities) <= backend_module._PID_LIST_ATTEMPTS  # nunca laço infinito

    def _fake_open_process(
        monkeypatch: pytest.MonkeyPatch, error: int, *, only: Callable[[int], bool] = lambda _: True
    ) -> None:
        real = backend_module._OpenProcess

        def fake(access: int, inherit: bool, pid: int) -> Any:
            if access & backend_module._SYNCHRONIZE and only(pid):
                ctypes.set_last_error(error)
                return None
            return real(access, inherit, pid)

        monkeypatch.setattr(backend_module, "_OpenProcess", fake)

    def _count_close_handle(monkeypatch: pytest.MonkeyPatch) -> list[int]:
        real = backend_module._CloseHandle
        closed: list[int] = []

        def counting(handle: Any) -> int:
            closed.append(int(handle))
            return int(real(handle))

        monkeypatch.setattr(backend_module, "_CloseHandle", counting)
        return closed

    @windows_only
    def test_open_process_distingue_membro_desmontado_de_falha(
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """(B) `ERROR_INVALID_PARAMETER` = nenhum processo com o PID: o membro terminou (C).
        Qualquer outra falha é falta de evidência (A) e levanta."""
        _fake_open_process(monkeypatch, ERROR_INVALID_PARAMETER)
        assert backend_module._open_member(os.getpid(), 0) is None
        _fake_open_process(monkeypatch, ERROR_ACCESS_DENIED)
        with pytest.raises(OSError, match="OpenProcess"):
            backend_module._open_member(os.getpid(), 0)

    @windows_only
    def test_is_process_in_job_falhando_nao_e_fora_do_job(monkeypatch: pytest.MonkeyPatch) -> None:
        """(C) Falha **da chamada** levanta — nunca é lida como "não pertence ao Job" — e o
        handle aberto é fechado."""
        closed = _count_close_handle(monkeypatch)

        def failing(handle: Any, job: Any, result: Any) -> int:
            ctypes.set_last_error(ERROR_INVALID_HANDLE)
            return 0

        monkeypatch.setattr(backend_module, "_IsProcessInJob", failing)
        with pytest.raises(OSError, match="IsProcessInJob"):
            backend_module._open_member(os.getpid(), 0)
        assert len(closed) == 1

    @windows_only
    def test_is_process_in_job_false_e_distinto_de_erro(monkeypatch: pytest.MonkeyPatch) -> None:
        """(D) A chamada funciona e responde "não": o PID já é de outro processo (ninguém sai de
        um Job), então o membro não existe mais — `None`, sem erro, handle fechado."""
        closed = _count_close_handle(monkeypatch)

        def not_in_job(handle: Any, job: Any, result: Any) -> int:
            result._obj.value = 0
            return 1

        monkeypatch.setattr(backend_module, "_IsProcessInJob", not_in_job)
        assert backend_module._open_member(os.getpid(), 0) is None
        assert len(closed) == 1

    def _unit_tree(monkeypatch: pytest.MonkeyPatch, lists: Iterator[list[int]]) -> Any:
        """`ProcessTree` sem processo real: congelamento neutro, listas em sequência e um
        raiz falso que registra quando é colhido."""
        monkeypatch.setattr(backend_module, "_freeze", lambda job: None)
        monkeypatch.setattr(backend_module, "_job_process_ids", lambda job: next(lists))
        reaped: list[float | None] = []

        def wait(timeout: float | None = None) -> int:
            reaped.append(timeout)
            return 0

        tree: Any = backend_module.ProcessTree(SimpleNamespace(wait=wait), 0)  # type: ignore[arg-type]
        tree._job = None  # nada de handle real a fechar
        tree.reaped = reaped
        return tree

    def _drained_job(monkeypatch: pytest.MonkeyPatch) -> None:
        """Job já vazio: `ActiveProcesses == 0` e lista completa vazia."""
        monkeypatch.setattr(backend_module, "_active_processes", lambda job: 0)
        monkeypatch.setattr(backend_module, "_job_process_ids", lambda job: [])

    @windows_only
    def test_lista_que_muda_entre_consultas_e_toda_classificada(
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """(E) PID que aparece numa consulta seguinte também é classificado; a captura só
        termina quando uma rodada não traz PID novo."""
        classified: list[int] = []

        def classify(pid: int, job: int) -> None:
            classified.append(pid)

        monkeypatch.setattr(backend_module, "_open_member", classify)
        tree = _unit_tree(monkeypatch, iter([[1], [1, 2], [1, 2], [1, 2]]))
        tree._capture_members(0)
        assert classified == [1, 2]
        assert not tree._members_failed

    @pytest.mark.parametrize(
        ("lists", "fail_on", "error"),
        [
            pytest.param([[i] for i in range(1, 100)], None, OSError, id="lista-nunca-estabiliza"),
            pytest.param([[1], [1, 2], [1, 2]], 2, OSError, id="pid-novo-sem-evidencia"),
            pytest.param([[1], [1]], 1, ValueError, id="falha-que-nao-e-oserror"),
        ],
    )
    @windows_only
    def test_lista_instavel_ou_membro_sem_evidencia_nao_confirma(
        monkeypatch: pytest.MonkeyPatch,
        lists: list[list[int]],
        fail_on: int | None,
        error: type[Exception],
    ) -> None:
        def classify(pid: int, job: int) -> None:
            if pid == fail_on:
                raise error("sem evidência")

        monkeypatch.setattr(backend_module, "_open_member", classify)
        tree = _unit_tree(monkeypatch, iter(lists))
        tree._capture_members(0)
        assert tree._members_failed
        _drained_job(monkeypatch)
        tree._job = 0
        assert tree.confirm_dead(10) is False  # Job vazio não supre a evidência que faltou
        assert tree.confirm_dead(10) is False  # idempotente: continua não confirmando
        assert tree.reaped  # mas o raiz foi colhido: nada fica pendurado

    @windows_only
    def test_confirmacao_exige_lista_vazia_alem_de_active_zero(
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`ActiveProcesses == 0` sozinho não confirma: com PID ainda listado, não há `True`."""
        monkeypatch.setattr(backend_module, "_open_member", lambda pid, job: None)
        tree = _unit_tree(monkeypatch, iter([[], []]))
        tree._capture_members(0)
        assert not tree._members_failed
        monkeypatch.setattr(backend_module, "_active_processes", lambda job: 0)
        monkeypatch.setattr(backend_module, "_job_process_ids", lambda job: [4242])
        tree._job = 0
        assert tree.confirm_dead(0.1) is False
        _drained_job(monkeypatch)
        assert tree.confirm_dead(0.1) is True  # controle: sem o PID listado, confirma

    @pytest.mark.parametrize("failure", ["open_process", "is_process_in_job"])
    @windows_only
    def test_membro_real_sem_evidencia_vira_supervision_failed(
        tmp_path: Path, watches: dict[str, Watch], monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        """(B/C) ponta a ponta: o neto não pode ser observado → a árvore é encerrada mesmo
        assim, mas **não** é declarada confirmada."""
        victim: dict[str, int] = {}
        if failure == "open_process":
            _fake_open_process(
                monkeypatch, ERROR_ACCESS_DENIED, only=lambda pid: pid == victim.get("rcc")
            )
        else:
            real_in_job = backend_module._IsProcessInJob

            def in_job(handle: Any, job: Any, result: Any) -> int:
                if process_id_of(handle) == victim.get("rcc"):
                    ctypes.set_last_error(ERROR_INVALID_HANDLE)
                    return 0
                return int(real_in_job(handle, job, result))

            monkeypatch.setattr(backend_module, "_IsProcessInJob", in_job)

        tree = script(tmp_path, "tree.py", TREE)

        def observed_then_cancel() -> bool:
            if not watches:
                pids = pids_ready(tmp_path, TREE_NAMES)
                if pids is None:
                    return False
                for name, pid in pids.items():
                    watches[name] = Watch(pid)
                victim["rcc"] = pids["rcc"]
            return True

        result = run_supervised(
            spec(tmp_path, tree, str(tmp_path), "r", "2", "stay", timeout_s=60),
            observed_then_cancel,
        )
        assert result.outcome is ProcessOutcome.SUPERVISION_FAILED
        assert not result.tree_confirmed_dead
        assert result.detail and "não confirmada" in result.detail
        # Não confirmar não é deixar vivo: o Job foi encerrado do mesmo jeito.
        assert eventually(lambda: not any(w.alive() for w in watches.values()), timeout=10)

    @windows_only
    def test_congelamento_impede_novos_membros(tmp_path: Path, watches: dict[str, Watch]) -> None:
        """Com o Job congelado, um `CreateProcess` dentro dele falha (`ERROR_NOT_ENOUGH_QUOTA`)
        e os membros atuais seguem vivos — a captura vira lista fechada."""
        spawner = script(
            tmp_path,
            "spawner.py",
            "import os, subprocess, sys, time\n"
            "d = sys.argv[1]\n"
            "open(os.path.join(d, 'r.tmp'), 'w').write(str(os.getpid()))\n"
            "os.replace(os.path.join(d, 'r.tmp'), os.path.join(d, 'r.pid'))\n"
            "while not os.path.exists(os.path.join(d, 'go')): time.sleep(0.01)\n"
            "try:\n"
            "    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "    r = 'spawned'\n"
            "except OSError as e:\n"
            "    r = 'failed %s' % getattr(e, 'winerror', None)\n"
            "open(os.path.join(d, 'result.txt'), 'w').write(r)\n"
            "time.sleep(60)\n",
        )
        handle = backend_module.start(spec(tmp_path, spawner, str(tmp_path)))
        try:
            assert eventually(lambda: pids_ready(tmp_path, ("r",)) is not None)
            watches["r"] = Watch((pids_ready(tmp_path, ("r",)) or {})["r"])
            assert handle._job is not None
            backend_module._freeze(handle._job)
            (tmp_path / "go").write_text("1")
            result_file = tmp_path / "result.txt"
            assert eventually(lambda: result_file.exists() and bool(result_file.read_text()))
            assert result_file.read_text() == "failed 1816"
            assert watches["r"].alive()
            handle.kill(0)
            assert handle.confirm_dead(10)
            assert not watches["r"].alive()
        finally:
            handle.close()
            for stream in (handle.popen.stdout, handle.popen.stderr):
                if stream is not None:
                    stream.close()

    @windows_only
    def test_captura_real_cobre_raiz_filho_e_neto(
        tmp_path: Path, watches: dict[str, Watch], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """(F) Caminho normal: o Job é congelado **antes** do término, a captura tem
        evidência de todo membro e a confirmação vale."""
        events: list[str] = []
        real_freeze = backend_module._freeze
        real_terminate = backend_module._TerminateJobObject

        def freeze(job: int) -> None:
            events.append("freeze")
            real_freeze(job)

        def terminate(job: Any, code: int) -> int:
            events.append("terminate")
            return int(real_terminate(job, code))

        monkeypatch.setattr(backend_module, "_freeze", freeze)
        monkeypatch.setattr(backend_module, "_TerminateJobObject", terminate)
        tree = script(tmp_path, "tree.py", TREE)
        handle = backend_module.start(spec(tmp_path, tree, str(tmp_path), "r", "2", "stay"))
        try:
            assert eventually(lambda: pids_ready(tmp_path, TREE_NAMES) is not None)
            for name, pid in (pids_ready(tmp_path, TREE_NAMES) or {}).items():
                watches[name] = Watch(pid)
            handle.kill(0)
            assert not handle._members_failed
            assert events == ["freeze", "terminate"]
            captured = {process_id_of(h) for h in handle._members}
            assert {w.pid for w in watches.values()} <= captured
            assert handle.confirm_dead(10)
            assert not any(w.alive() for w in watches.values())
        finally:
            handle.close()
            for stream in (handle.popen.stdout, handle.popen.stderr):
                if stream is not None:
                    stream.close()


# ================================================================== específicos do POSIX


@linux_only
def test_sigterm_ignorado_recebe_sigkill_apos_a_graca(
    tmp_path: Path, watches: dict[str, Watch]
) -> None:
    code = (
        "import os, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "open(os.path.join(sys.argv[1], 'r.pid'), 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    stubborn = script(tmp_path, "stubborn.py", code)

    def is_cancelled() -> bool:
        pids = pids_ready(tmp_path, ("r",))
        if pids is None:
            return False
        watches.setdefault("r", Watch(pids["r"]))
        return True

    started = time.monotonic()
    result = run_supervised(spec(tmp_path, stubborn, str(tmp_path), kill_grace_s=0.5), is_cancelled)
    elapsed = time.monotonic() - started
    assert result.outcome is ProcessOutcome.CANCELLED
    assert_clean_result(result)
    assert elapsed >= 0.5
    assert not watches["r"].alive()


@linux_only
def test_sigterm_tratado_encerra_sem_esperar_a_graca(tmp_path: Path) -> None:
    code = (
        "import os, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))\n"
        "open(os.path.join(sys.argv[1], 'r.pid'), 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    polite = script(tmp_path, "polite.py", code)
    started = time.monotonic()
    result = run_supervised(
        spec(tmp_path, polite, str(tmp_path), kill_grace_s=20),
        lambda: pids_ready(tmp_path, ("r",)) is not None,
    )
    assert result.outcome is ProcessOutcome.CANCELLED  # saída veio depois do sinal
    assert_clean_result(result)
    assert time.monotonic() - started < 15


@linux_only
def test_setsid_segurando_o_pipe_e_detectado(tmp_path: Path, watches: dict[str, Watch]) -> None:
    """Limitação declarada: `setsid` escapa do grupo. Se o fugitivo mantém o pipe, a
    captura não chega a EOF e o resultado é `SUPERVISION_FAILED` — nunca um falso "ok"."""
    code = (
        "import os, subprocess, sys\n"
        "d = sys.argv[1]\n"
        "subprocess.Popen([sys.executable, '-c',\n"
        '    \'import os,sys,time; open(os.path.join(sys.argv[1], "esc.pid"), "w")\'\n'
        "    '.write(str(os.getpid())); time.sleep(60)', d], start_new_session=True)\n"
        "import time\n"
        "while not os.path.exists(os.path.join(d, 'esc.pid')): time.sleep(0.01)\n"
    )
    escaper = script(tmp_path, "escaper.py", code)
    try:
        result = run_supervised(spec(tmp_path, escaper, str(tmp_path)), never)
        watches["esc"] = Watch(int((tmp_path / "esc.pid").read_text()))
        assert result.outcome is ProcessOutcome.SUPERVISION_FAILED
        assert not result.output_complete
        assert result.detail and "EOF" in result.detail
    finally:
        pid_file = tmp_path / "esc.pid"
        if pid_file.exists() and "esc" not in watches:
            watches["esc"] = Watch(int(pid_file.read_text()))


# ============================================ DIAGNÓSTICO TEMPORÁRIO (PR #4) — NÃO MERGEAR
# Descobre qual processo faz `orphans_alive()` responder True no windows-latest. Falha de
# propósito com os dados na mensagem para que apareçam no log da CI. Será removido.

if sys.platform == "win32":

    class _DiagProcessEntry32(ctypes.Structure):
        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        )

    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_DiagProcessEntry32)]
    _k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_DiagProcessEntry32)]

    def _diag_toolhelp() -> dict[int, dict[str, Any]]:
        snap = _k32.CreateToolhelp32Snapshot(0x2, 0)
        found: dict[int, dict[str, Any]] = {}
        entry = _DiagProcessEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        more = _k32.Process32FirstW(snap, ctypes.byref(entry))
        while more:
            found[int(entry.th32ProcessID)] = {
                "exe": entry.szExeFile,
                "parent": int(entry.th32ParentProcessID),
            }
            more = _k32.Process32NextW(snap, ctypes.byref(entry))
        _k32.CloseHandle(snap)
        return found

    def _diag_image_error(pid: int) -> Any:
        handle = backend_module._OpenProcess(
            backend_module._PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return {"open_error": ctypes.get_last_error()}
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        ok = backend_module._QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
        error = ctypes.get_last_error()
        backend_module._CloseHandle(handle)
        return {"ok": bool(ok), "error": None if ok else error, "value": buffer.value}

    def _diag_describe(tree: Any) -> dict[str, Any]:
        job = tree._job
        entries: list[dict[str, Any]] = []
        try:
            pids = backend_module._job_process_ids(job)
        except OSError as exc:
            return {"job_list_error": str(exc)}
        for pid in pids:
            entry: dict[str, Any] = {"pid": pid, "is_root": pid == tree.popen.pid}
            entry["image"] = backend_module._image_path(pid)
            entry["image_error"] = _diag_image_error(pid)
            entry["toolhelp"] = _diag_toolhelp().get(pid)
            entry["is_console_host"] = backend_module._is_console_host(pid)
            handle = backend_module._OpenProcess(
                backend_module._SYNCHRONIZE | backend_module._PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                pid,
            )
            if not handle:
                entry["open_error"] = ctypes.get_last_error()
            else:
                in_job = wintypes.BOOL()
                ok = backend_module._IsProcessInJob(handle, job, ctypes.byref(in_job))
                entry["in_job"] = bool(in_job.value) if ok else f"erro {ctypes.get_last_error()}"
                entry["signaled"] = backend_module._WaitForSingleObject(handle, 0) == 0
                backend_module._CloseHandle(handle)
            entries.append(entry)
        return {
            "root_pid": tree.popen.pid,
            "root_returncode": tree.popen.returncode,
            "active": backend_module._active_processes(job),
            "members": entries,
        }

    def test_zz_diagnostico_orfaos_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import platform

        observations: list[dict[str, Any]] = []
        real = backend_module.ProcessTree.orphans_alive

        identities: dict[int, Any] = {}
        stops: dict[int, threading.Event] = {}
        real_start = backend_module.start

        def watched_start(spec_: ProcessSpec) -> Any:
            tree = real_start(spec_)
            stop = threading.Event()

            def watch() -> None:
                while not stop.is_set() and tree._job is not None:
                    try:
                        pids = backend_module._job_process_ids(tree._job)
                    except OSError:
                        return
                    unknown = [pid for pid in pids if pid not in identities]
                    if unknown:
                        snap = _diag_toolhelp()
                        for pid in unknown:
                            identities[pid] = snap.get(pid, "fora do snapshot")
                    time.sleep(0.002)

            thread = threading.Thread(target=watch, daemon=True)
            thread.start()
            stops[id(tree)] = stop
            return tree

        def spy(self: Any) -> bool:
            job_pids = backend_module._job_process_ids(self._job)
            answer = bool(real(self))
            stops[id(self)].set()
            if answer:
                root = self.popen.pid
                observations.append(
                    {
                        "root_pid": root,
                        "root_identity": identities.get(root),
                        "flagged": [
                            {"pid": pid, "identity": identities.get(pid, "nunca visto")}
                            for pid in job_pids
                        ],
                    }
                )
            return answer

        monkeypatch.setattr(backend_module, "start", watched_start)
        monkeypatch.setattr(backend_module.ProcessTree, "orphans_alive", spy)
        code = "import sys; sys.stdout.write('ok'); sys.stderr.write('aviso')"
        flagged = 0
        for _ in range(30):
            result = run_supervised(spec(tmp_path, "-c", code), never)
            flagged += result.orphans_killed
        header = {
            "platform": platform.platform(),
            "python": sys.version,
            "executable": PYTHON,
            "console_host_expected": backend_module._CONSOLE_HOST,
            "runs": 30,
            "orphans_killed": flagged,
        }
        raise AssertionError(
            "DIAGNOSTICO " + json.dumps({"header": header, "observations": observations[:5]})
        )
