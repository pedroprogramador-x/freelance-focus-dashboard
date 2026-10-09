"""Worker de **processo** da E8.4.1: tenta admitir UMA execução e relata o resultado em JSON.

Roda como `python -m tests.admission_worker_e8_4_1 '<json>'`, a partir da raiz de `api/`. Existe
para provar que a reserva do slot vale **entre processos** — um lock em memória do Python não
sobreviveria a este teste. O processo avisa que está pronto, espera o arquivo `go` e só então
chama a admissão, para que as tentativas se sobreponham de verdade.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from app.config import AppSettings
from app.db.session import create_engine, create_session_factory, session_scope
from tests.admission_support_e8_4_1 import admit


def main() -> int:
    spec = json.loads(sys.argv[1])
    data_dir = Path(spec["data_dir"])
    settings = AppSettings(
        data_dir=data_dir,
        database_url=spec["database_url"],
        web_dist_dir=data_dir / "sem-build",
    )
    engine = create_engine(settings)
    factory = create_session_factory(engine)

    Path(spec["ready"]).write_text("1", encoding="utf-8")
    deadline = time.monotonic() + 120
    go = Path(spec["go"])
    while not go.exists():
        if time.monotonic() > deadline:
            return 3
        time.sleep(0.002)

    result: dict[str, object]
    try:
        with session_scope(factory) as session:
            admission = admit(
                session, spec["task_id"], Path(spec["artifacts_dir"]), key=spec["key"]
            )
            result = {
                "outcome": admission.outcome.value,
                "run_id": admission.run.id if admission.run is not None else None,
            }
    except Exception as error:  # o teste decide se o tipo do erro era o esperado
        result = {"error": type(error).__name__}
    finally:
        engine.dispose()

    sys.stdout.write("RESULT " + json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
