"""Worker de **processo** da E8.4.2: tenta publicar (etapa J) UMA worktree e relata em JSON.

Roda como `python -m tests.publish_worker_e8_4_2 '<json>'`, a partir da raiz de `api/`. Existe para
provar que o CAS de publicação vale **entre processos** — o lock em memória do serviço não
sobreviveria a este teste. Espera o arquivo `go` e só então chama `publish_prepared_workspace`.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from app.config import AppSettings
from app.db.session import create_engine, create_session_factory
from app.orchestrator import execution_manager as em


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
        with factory() as session:
            version = em.publish_prepared_workspace(
                session,
                spec["task_id"],
                control_run_id=spec["run_id"],
                expected_version=spec["version"],
                worktree_path=spec["path"],
            )
        result = {"published": version}
    except Exception as error:  # o teste decide se o tipo do erro era o esperado
        result = {"error": type(error).__name__}
    finally:
        engine.dispose()

    sys.stdout.write("RESULT " + json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
