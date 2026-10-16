"""`run_supervised`: inicia, observa, encerra e confirma uma árvore de processos (E7.3).

Fluxo, igual nos dois SOs (o que muda está em `_windows`/`_posix`):

1. `is_cancelled()` antes de tudo — cancelado antes do início não cria processo.
2. O backend cria o processo **já contido** (Job Object / sessão própria) ou falha fechado.
3. Duas threads drenam `stdout` e `stderr` em blocos, guardando no máximo `max_*_bytes`
   cada e descartando o excedente **durante** a leitura — o filho nunca bloqueia num pipe
   cheio e a memória nunca passa do limite mais um bloco.
4. Laço de observação a cada `_POLL_S`: raiz saiu? → cancelado? → prazo estourou?, nessa
   ordem. Antes de agir sobre cancelamento/timeout o raiz é consultado de novo: saída
   observada antes do primeiro sinal vence ("já terminou > cancelamento > timeout").
5. A árvore é **sempre** encerrada e confirmada morta, inclusive quando o raiz saiu
   sozinho — descendentes deixados para trás são órfãos e morrem (`orphans_killed`).
6. As threads leitoras terminam em prazo fixo; os pipes e o Job são fechados em `finally`.

Qualquer falha inesperada no caminho encerra a árvore e vira `SUPERVISION_FAILED`.
`KeyboardInterrupt`/`SystemExit` também encerram a árvore antes de propagar.

`is_cancelled` é um callable, não um tipo novo: quem chama passa
`request.cancel_token.is_cancelled` (E7.2) sem que este pacote importe `agent_runtime`.
Ele é consultado da thread de quem chamou, a cada `_POLL_S`, e precisa ser rápido e não
bloquear: é uma leitura de flag, não uma espera. Levantar exceção ou devolver algo que não
seja `bool` é tratado como falha de supervisão (a árvore é encerrada).

**Não é sandbox** — ver `contracts`.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import IO

from app.process_runtime.contracts import (
    ProcessOutcome,
    ProcessResult,
    ProcessSpec,
    StartFailed,
    describe_os_error,
)
from app.process_runtime.launch import (
    PROFILE_POSIX_PGRP_V1,
    PROFILE_WIN_JOB_V1,
    REFUSAL_DETAIL_PREFIX,
    LaunchIntent,
    LaunchPlatform,
    LaunchRefusal,
    LaunchScope,
    ProtectedLaunch,
    current_scope,
    exe_name_of,
)

if sys.platform == "win32":
    from app.process_runtime import _windows as _backend
else:
    from app.process_runtime import _posix as _backend

#: Intervalo do laço de observação: latência de cancelamento e de detecção de saída.
_POLL_S = 0.02
#: Tamanho do bloco lido por vez. A memória por stream é limitada a `max_*_bytes` + isto.
_READ_CHUNK = 64 * 1024
#: Quanto uma thread leitora espera por dados antes de reolhar o pedido de parada.
_READER_WAIT_S = 0.05
#: Prazo para confirmar a árvore morta depois do encerramento.
_CONFIRM_DEAD_S = 10.0
#: Prazo para os pipes chegarem a EOF depois da árvore morta.
_DRAIN_S = 5.0
#: Prazo para uma thread leitora parar depois de receber o pedido de parada.
_READER_STOP_S = 1.0


def _ask(is_cancelled: Callable[[], bool]) -> bool:
    value = is_cancelled()
    if not isinstance(value, bool):
        raise TypeError("is_cancelled() precisa devolver bool")
    return value


class _Reader:
    """Drena um pipe em blocos, guardando no máximo `limit` bytes. Nunca bloqueia além de
    `_READER_WAIT_S` sem reolhar o pedido de parada."""

    def __init__(self, stream: IO[bytes], limit: int, name: str) -> None:
        self.stream = stream
        self._fd = stream.fileno()
        self._limit = limit
        self._wait = _backend.readable_waiter(self._fd)
        self._buffer = bytearray()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"process-runtime-{name}", daemon=True
        )
        self.truncated = False
        self.eof = False
        self.error: str | None = None

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                if not self._wait(_READER_WAIT_S):
                    continue
                chunk = os.read(self._fd, _READ_CHUNK)
                if not chunk:
                    self.eof = True
                    return
                room = self._limit - len(self._buffer)
                if room > 0:
                    self._buffer += chunk[:room]
                if len(chunk) > room:
                    self.truncated = True
        except Exception as exc:
            self.error = describe_os_error(exc)

    def finish(self, deadline: float) -> None:
        if self._thread.ident is not None:
            self._thread.join(max(0.0, deadline - time.monotonic()))
            if self._thread.is_alive():
                self._stop.set()
                self._thread.join(_READER_STOP_S)
        if not self._thread.is_alive():
            self.stream.close()

    def data(self) -> bytes:
        return bytes(self._buffer)


def _observe(
    popen: subprocess.Popen[bytes], deadline: float, is_cancelled: Callable[[], bool]
) -> tuple[ProcessOutcome, int | None]:
    while True:
        code = popen.poll()
        if code is not None:
            return ProcessOutcome.EXITED, code
        if _ask(is_cancelled):
            decided = ProcessOutcome.CANCELLED
            break
        now = time.monotonic()
        if now >= deadline:
            decided = ProcessOutcome.TIMEOUT
            break
        with contextlib.suppress(subprocess.TimeoutExpired):
            popen.wait(timeout=min(_POLL_S, deadline - now))
    # Ponto de decisão: a última consulta ao raiz antes do primeiro sinal de término.
    code = popen.poll()
    if code is not None:
        return ProcessOutcome.EXITED, code
    return decided, None


def _terminate(tree: _backend.ProcessTree, grace_s: float) -> tuple[bool, str | None]:
    """Encerra e confirma. Nunca levanta `Exception`: falha vira "não confirmado"."""
    try:
        tree.kill(grace_s)
        return tree.confirm_dead(_CONFIRM_DEAD_S), None
    except Exception as exc:
        return False, f"encerramento falhou: {describe_os_error(exc)}"


def _no_process(outcome: ProcessOutcome, detail: str | None) -> ProcessResult:
    return ProcessResult(
        outcome=outcome,
        exit_code=None,
        stdout=b"",
        stderr=b"",
        stdout_truncated=False,
        stderr_truncated=False,
        output_complete=True,
        duration_ms=0,
        tree_confirmed_dead=True,
        orphans_killed=False,
        detail=detail,
    )


def _refused(refusal: LaunchRefusal) -> ProcessResult:
    """Lançamento protegido recusado **antes** de qualquer processo (E8.4.5 / I2)."""
    outcome = (
        ProcessOutcome.CANCELLED
        if refusal is LaunchRefusal.STOPPED
        else ProcessOutcome.SUPERVISION_FAILED
    )
    return _no_process(outcome, f"{REFUSAL_DETAIL_PREFIX}{refusal.value}")


def run_supervised(
    spec: ProcessSpec, is_cancelled: Callable[[], bool], *, protected: bool = False
) -> ProcessResult:
    """Executa `spec` sob supervisão e devolve o resultado. Bloqueia até a árvore morrer.

    Só levanta para uso incorreto (`TypeError`); toda falha operacional vira
    `ProcessOutcome.SUPERVISION_FAILED`, com a árvore encerrada.

    **Lançamento protegido (E8.4.5 / I2).** Sem `LaunchScope` instalado, o comportamento é o da
    E7.3, byte a byte. Com um escopo instalado, **todo** lançamento do processo é protegido
    (`launch.py`): intent antes do `Popen`, gate, `created`/`contained` do observer, recusa fechada.
    `protected=True` declara que a chamada pertence a uma tentativa protegida: sem escopo, ela é
    recusada (`launch_refused:scope_missing`) em vez de cair silenciosamente no `Popen` legado.
    """
    if not isinstance(spec, ProcessSpec):
        raise TypeError("spec precisa ser ProcessSpec")
    if not callable(is_cancelled):
        raise TypeError("is_cancelled precisa ser callable")
    if type(protected) is not bool:
        raise TypeError("protected precisa ser bool")
    try:
        if _ask(is_cancelled):
            return _no_process(ProcessOutcome.CANCELLED, None)
    except Exception as exc:
        return _no_process(
            ProcessOutcome.SUPERVISION_FAILED,
            f"is_cancelled falhou antes do início: {describe_os_error(exc)}",
        )

    scope = current_scope()
    if scope is not None:
        return _run_protected(scope, spec, is_cancelled)
    if protected:
        return _refused(LaunchRefusal.SCOPE_MISSING)

    started = time.monotonic()
    try:
        tree = _backend.start(spec)
    except StartFailed as exc:
        return _start_failed_result(exc, started)
    return _supervise(tree, spec, is_cancelled, started)


def _start_failed_result(
    exc: StartFailed, started: float, *, extra: str | None = None
) -> ProcessResult:
    detail = exc.detail if extra is None else f"{exc.detail}; {extra}"
    return ProcessResult(
        outcome=ProcessOutcome.SUPERVISION_FAILED,
        exit_code=None,
        stdout=b"",
        stderr=b"",
        stdout_truncated=False,
        stderr_truncated=False,
        output_complete=True,
        duration_ms=int((time.monotonic() - started) * 1000),
        tree_confirmed_dead=exc.tree_confirmed_dead,
        orphans_killed=False,
        detail=detail,
    )


_LEDGER_END_FAILED = "launch_ledger_end_failed"


def _ledger_end_failed(result: ProcessResult) -> ProcessResult:
    """O processo terminou, mas a linha do ledger ficou aberta: nunca pode parecer sucesso."""
    observed = (
        result.outcome.value
        if result.exit_code is None
        else (f"{result.outcome.value}({result.exit_code})")
    )
    detail = f"{_LEDGER_END_FAILED}; desfecho observado: {observed}"
    if result.detail:
        detail = f"{result.detail}; {detail}"
    return ProcessResult(
        outcome=ProcessOutcome.SUPERVISION_FAILED,
        exit_code=None,
        stdout=result.stdout,
        stderr=result.stderr,
        stdout_truncated=result.stdout_truncated,
        stderr_truncated=result.stderr_truncated,
        output_complete=result.output_complete,
        duration_ms=result.duration_ms,
        tree_confirmed_dead=result.tree_confirmed_dead,
        orphans_killed=result.orphans_killed,
        detail=detail,
    )


def _run_protected(
    scope: LaunchScope, spec: ProcessSpec, is_cancelled: Callable[[], bool]
) -> ProcessResult:
    windows = sys.platform == "win32"
    intent = LaunchIntent(
        exe_name_of(spec.argv[0]),
        LaunchPlatform.WIN32 if windows else LaunchPlatform.POSIX,
        PROFILE_WIN_JOB_V1 if windows else PROFILE_POSIX_PGRP_V1,
    )
    launch = ProtectedLaunch(scope, intent)
    refusal = launch.admit()
    if refusal is not None:
        return _refused(refusal)

    started = time.monotonic()
    failure: StartFailed | None = None
    tree: _backend.ProcessTree | None = None
    try:
        try:
            tree = _backend.start(spec, launch.hooks)
        except StartFailed as exc:
            failure = exc
    finally:
        launch.release_gate()  # a seção protegida acaba no resume; o gate não vive com o processo

    if failure is not None:
        end = launch.end_for_start_failure(failure.tree_confirmed_dead)
        recorded = launch.record_end(end)
        return _start_failed_result(
            failure, started, extra=None if recorded else _LEDGER_END_FAILED
        )

    assert tree is not None
    result = _supervise(tree, spec, is_cancelled, started)
    if not launch.record_end(launch.end_for_result(result.tree_confirmed_dead)):
        return _ledger_end_failed(result)
    return result


def _supervise(
    tree: _backend.ProcessTree,
    spec: ProcessSpec,
    is_cancelled: Callable[[], bool],
    started: float,
) -> ProcessResult:
    popen = tree.popen
    readers: list[_Reader] = []
    outcome = ProcessOutcome.SUPERVISION_FAILED
    exit_code: int | None = None
    orphans = False
    problems: list[str] = []
    dead = False
    try:
        try:
            for stream, limit, name in (
                (popen.stdout, spec.max_stdout_bytes, "stdout"),
                (popen.stderr, spec.max_stderr_bytes, "stderr"),
            ):
                if stream is None:
                    raise RuntimeError(f"{name} sem pipe")
                reader = _Reader(stream, limit, name)
                readers.append(reader)
                reader.start()
            outcome, exit_code = _observe(popen, started + spec.timeout_s, is_cancelled)
            orphans = outcome is ProcessOutcome.EXITED and tree.orphans_alive()
        except Exception as exc:
            outcome = ProcessOutcome.SUPERVISION_FAILED
            problems.append(f"falha durante a supervisão: {describe_os_error(exc)}")
        dead, kill_problem = _terminate(tree, spec.kill_grace_s)
        if kill_problem:
            problems.append(kill_problem)
    except BaseException:
        _terminate(tree, spec.kill_grace_s)
        raise
    finally:
        drain_deadline = time.monotonic() + _DRAIN_S
        for reader in readers:
            reader.finish(drain_deadline)
        for stream in (popen.stdout, popen.stderr):
            if stream is not None and not any(r.stream is stream for r in readers):
                stream.close()
        tree.close()

    duration_ms = int((time.monotonic() - started) * 1000)
    if not dead:
        problems.append("árvore de processos não confirmada morta")
    for reader in readers:
        if reader.error:
            problems.append(f"leitura falhou: {reader.error}")
    complete = len(readers) == 2 and all(reader.eof for reader in readers)
    if not complete:
        problems.append("captura de saída não chegou a EOF")
    if problems:
        if outcome is not ProcessOutcome.SUPERVISION_FAILED:
            observed = outcome.value if exit_code is None else f"{outcome.value}({exit_code})"
            problems.append(f"desfecho observado antes da falha: {observed}")
        outcome, exit_code = ProcessOutcome.SUPERVISION_FAILED, None

    return ProcessResult(
        outcome=outcome,
        exit_code=exit_code,
        stdout=readers[0].data() if readers else b"",
        stderr=readers[1].data() if len(readers) > 1 else b"",
        stdout_truncated=bool(readers) and readers[0].truncated,
        stderr_truncated=len(readers) > 1 and readers[1].truncated,
        output_complete=complete,
        duration_ms=duration_ms,
        tree_confirmed_dead=dead,
        orphans_killed=orphans,
        detail="; ".join(problems) if problems else None,
    )
