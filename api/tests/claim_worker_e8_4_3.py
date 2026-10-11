"""Worker de **processo** da E8.4.3: tenta REIVINDICAR (abrir) o Run do Developer e relata em JSON.

Roda como `python -m tests.claim_worker_e8_4_3 '<json>'`, a partir da raiz de `api/`. Existe para
provar que "no máximo uma chamada por `invocation_id`" vale **entre processos**: o único vencedor
sai do `UNIQUE(invocation_id)` + `INSERT` como 1ª instrução da transação, não de lock em memória.
Espera o arquivo `go` e só então chama `open_developer_run`.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from app.config import AppSettings
from app.db.session import create_engine, create_session_factory
from app.orchestrator import execution_manager as em
from app.orchestrator.developer_invocation_contract import DeveloperRunSpec, ProviderIdentity
from app.orchestrator.fingerprint import tool_profile_hash


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

    provider, adapter, version, model, transport = spec["identity"]
    claim = DeveloperRunSpec(
        invocation_id=spec["invocation_id"],
        control_run_id=spec["control_run_id"],
        context_manifest_id=spec["manifest_id"],
        identity=ProviderIdentity(provider, adapter, version, model, transport),
        tool_profile_hash=tool_profile_hash(),
        base_commit=spec["base_commit"],
        worktree_path=spec["worktree_path"],
        expected_task_version=spec["task_version"],
        attempt_index=spec["attempt_index"],
        fix_round=spec["fix_round"],
        started_at=datetime.now(UTC),
    )
    result: dict[str, object]
    try:
        with factory() as session:
            outcome = em.open_developer_run(session, spec["task_id"], spec=claim)
        result = {"outcome": outcome.outcome.value, "run_id": outcome.run_id}
    except Exception as error:  # o teste decide se o tipo do erro era o esperado
        result = {"error": type(error).__name__}
    finally:
        engine.dispose()

    sys.stdout.write("RESULT " + json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
