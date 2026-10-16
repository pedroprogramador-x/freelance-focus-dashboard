"""Backend POSIX do Supervisor: sessão/grupo de processos próprio (E7.3).

O processo nasce com `start_new_session=True`: o `setsid()` acontece no filho **antes** do
`exec`, então o raiz e todo descendente que não mude de grupo compartilham o grupo cujo id
é o PID do raiz. Não há janela de corrida entre criar e conter.

Término: `SIGTERM` ao grupo → espera até `kill_grace_s` o grupo esvaziar → `SIGKILL` ao
grupo → colhe o raiz → confirma o grupo vazio (`killpg(pgid, 0)` → `ESRCH`).

Limitações declaradas — este backend **não** as resolve:

* Um descendente que chame `setsid()`/`setpgid()` sai do grupo e escapa do encerramento.
* Não existe equivalente simples a `KILL_ON_JOB_CLOSE`: se o supervisor receber `SIGKILL`,
  os descendentes continuam vivos.
* Zumbis só somem quando alguém os colhe. Descendentes órfãos são colhidos pelo `init` (ou
  subreaper) do sistema; num contêiner cujo PID 1 não colhe, a confirmação expira e o
  resultado vira `SUPERVISION_FAILED` — fail closed, nunca "morto" por suposição.
* Depois que o raiz é colhido e o grupo esvazia, o número do grupo pode ser reutilizado.
  O supervisor só sinaliza o grupo quando acabou de observá-lo existente e nunca depois de
  confirmá-lo vazio; a janela residual é de microssegundos e exigiria um processo novo que
  virasse líder de grupo com exatamente aquele número.

Sem cgroups, subreaper ou contêiner. **Não é sandbox**: nada disto restringe arquivos,
rede, segredos ou APIs do SO.
"""

from __future__ import annotations

import contextlib
import os
import select
import signal
import subprocess
import sys
import time
from collections.abc import Callable

from app.process_runtime.contracts import ProcessSpec, StartFailed, describe_os_error
from app.process_runtime.launch import StartHooks

# O resto do módulo só existe fora do Windows; o mypy também o trata assim no Windows.
assert sys.platform != "win32"

_GROUP_POLL_S = 0.005
_ABORT_CONFIRM_S = 10.0


class ProcessTree:
    """A árvore de um `ProcessSpec`: o grupo de processos do raiz. Métodos idempotentes."""

    def __init__(self, popen: subprocess.Popen[bytes]) -> None:
        self.popen = popen
        self._pgid = popen.pid
        self._confirmed_dead = False

    def _group_exists(self) -> bool:
        try:
            os.killpg(self._pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # existe, mas não é nosso: nunca tratar como morto
        return True

    def _signal_group(self, signum: int) -> None:
        if self._confirmed_dead:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self._pgid, signum)

    def orphans_alive(self) -> bool:
        """Chamado quando o raiz já saiu: resta alguém no grupo?"""
        if self._confirmed_dead:
            return False
        self.popen.poll()
        return self._group_exists()

    def kill(self, grace_s: float) -> None:
        """`SIGTERM` → até `grace_s` → `SIGKILL`, sempre ao grupo inteiro."""
        if self._confirmed_dead:
            return
        self.popen.poll()
        if not self._group_exists():
            return
        self._signal_group(signal.SIGTERM)
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            self.popen.poll()
            if not self._group_exists():
                return
            time.sleep(_GROUP_POLL_S)
        self.popen.poll()
        if self._group_exists():
            self._signal_group(signal.SIGKILL)

    def confirm_dead(self, timeout_s: float) -> bool:
        """Raiz colhido **e** grupo vazio, até `timeout_s`."""
        if self._confirmed_dead:
            return True
        deadline = time.monotonic() + timeout_s
        while True:
            if self.popen.poll() is not None and not self._group_exists():
                self._confirmed_dead = True
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_GROUP_POLL_S)

    def close(self) -> None:
        """Nada a liberar além dos pipes, que são do supervisor."""


def _start_token(pid: int) -> str:
    """Ticks de início do `/proc/<pid>/stat` (campo 22), quando existe; senão `"unavailable"`.

    Serve só de identidade complementar ao PID. No POSIX **nenhuma** sonda posterior libera um
    lançamento (`killpg`/`ESRCH` prova apenas a ausência do grupo consultado, não a de
    descendentes que escaparam), então a ausência do token nunca vira prova."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            raw = handle.read().decode("ascii", "replace")
        fields = raw.rsplit(")", 1)[1].split()
        return fields[19] if len(fields) > 19 and fields[19].isdigit() else "unavailable"
    except OSError:
        return "unavailable"


def _abort(tree: ProcessTree, grace_s: float) -> bool:
    """Desfaz um início cujo registro falhou. Devolve se o grupo foi confirmado vazio."""
    popen = tree.popen
    dead = False
    try:
        tree.kill(grace_s)
        dead = tree.confirm_dead(_ABORT_CONFIRM_S)
    except Exception:
        dead = False
    for stream in (popen.stdout, popen.stderr):
        if stream is not None:
            with contextlib.suppress(OSError):
                stream.close()
    return dead


def start(spec: ProcessSpec, hooks: StartHooks | None = None) -> ProcessTree:
    """Cria o processo em sessão própria, ou levanta `StartFailed` sem deixar nada vivo.

    Com `hooks` (E8.4.5 / I2): `Popen → hooks.created → hooks.contained`. No POSIX o `setsid`
    acontece antes do `exec`, então a contenção (grupo próprio) já existe quando o raiz nasce.
    Falha de um hook mata o grupo e confirma, mas **só** `tree_confirmed_dead` fala sobre o grupo:
    descendentes que fizerem `setsid` escapam (limite declarado da E7.3)."""
    try:
        popen = subprocess.Popen(  # noqa: S603 — argv estruturado, sem shell, executável absoluto
            list(spec.argv),
            executable=spec.argv[0],
            cwd=spec.cwd,
            env=dict(spec.env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # `exec` que falha é reportado ao pai depois que o filho já foi colhido pelo próprio
        # `subprocess`: nada sobrevive.
        detail = f"processo não pôde ser criado: {describe_os_error(exc)}"
        raise StartFailed(detail, tree_confirmed_dead=True) from exc
    tree = ProcessTree(popen)
    if hooks is not None:
        try:
            hooks.created(popen.pid, lambda: _start_token(popen.pid))
            hooks.contained()
        except BaseException as exc:
            dead = _abort(tree, spec.kill_grace_s)
            if isinstance(exc, Exception):
                detail = f"falha ao registrar o lançamento: {describe_os_error(exc)}"
                raise StartFailed(detail, tree_confirmed_dead=dead) from exc
            raise
    return tree


def readable_waiter(fd: int) -> Callable[[float], bool]:
    """`wait(timeout_s)`: `True` quando um `os.read(fd)` não vai bloquear (dados ou EOF).

    `poll` em vez de `select`: não tem o teto de `FD_SETSIZE`.
    """
    poller = select.poll()
    poller.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR)

    def wait(timeout_s: float) -> bool:
        return bool(poller.poll(max(0, int(timeout_s * 1000))))

    return wait
