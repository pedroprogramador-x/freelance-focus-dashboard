"""Worker de crash do I2 (E8.4.5). Rodado como subprocesso isolado pelos testes.

Instala um escopo de lançamento cujo observer grava um ledger em arquivo (com `fsync`) e morre
(`os._exit(77)`) numa fronteira escolhida. Nada aqui toca banco, repositório ou rede.

Uso: ``python launch_crash_worker_e8_4_5.py <ledger> <sentinela> <ponto>``, onde ``ponto`` é um de
``after_intent``, ``after_created``, ``after_contained``, ``during_run``, ``after_ended`` ou
``none``. O processo supervisionado escreve a sentinela e dorme; ``during_run`` morre depois que a
sentinela existe (o supervisor consulta ``is_cancelled`` a cada ciclo).
"""

from __future__ import annotations

import json
import os
import sys

from app.process_runtime import ProcessSpec, run_supervised
from app.process_runtime.launch import LaunchGate, LaunchScope, install_scope

CRASH_CODE = 77
CHILD = "import sys, time; open(sys.argv[1], 'w').write('x'); time.sleep(60)"


class FileLedger:
    def __init__(self, path: str, crash_at: str) -> None:
        self._path = path
        self._crash_at = crash_at

    def _write(self, kind: str, **fields: object) -> None:
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"kind": kind, **fields}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _maybe_crash(self, point: str) -> None:
        if self._crash_at == point:
            os._exit(CRASH_CODE)

    def intent(self, intent: object) -> object:
        self._write("intent")
        self._maybe_crash("after_intent")
        return 1

    def created(self, handle: object, identity: object) -> None:
        self._write("created", pid=identity.pid, token=identity.start_token)  # type: ignore[attr-defined]
        self._maybe_crash("after_created")

    def contained(self, handle: object) -> None:
        self._write("contained")
        self._maybe_crash("after_contained")

    def ended(self, handle: object, end: object) -> None:
        self._write("ended", end=end.value)  # type: ignore[attr-defined]
        self._maybe_crash("after_ended")


def main(ledger: str, sentinel: str, point: str) -> int:
    env = {k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "PATH", "TEMP", "TMP")}
    spec = ProcessSpec(
        argv=(sys.executable, "-c", CHILD, sentinel),
        cwd=os.getcwd(),
        env=env,
        timeout_s=60,
        max_stdout_bytes=1024,
        max_stderr_bytes=1024,
        kill_grace_s=0.5,
    )

    def is_cancelled() -> bool:
        if point == "during_run" and os.path.exists(sentinel):
            os._exit(CRASH_CODE)
        return False

    with install_scope(LaunchScope(FileLedger(ledger, point), LaunchGate())):
        result = run_supervised(spec, is_cancelled)
    print(result.outcome.value)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
