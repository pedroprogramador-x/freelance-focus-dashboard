"""Apoio da E8.4.1 — compartilhado pelos testes de admissão e pelo worker de processo.

**Só testes** montam um prover positivo: em produção não existe verificador positivo ainda
(E7.6). O resolver devolve sempre o mesmo binding concreto, igual ao contexto esperado de
`tests.capability_helpers`, para que a prova de capability e o `developer_binding` aprovado
falem do mesmo adaptador.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.capability_wiring import VerifyingCapabilityProver
from app.db.models import DevWorkspace, WorkspaceTask
from app.orchestrator import approve, create_task, get_task, plan
from app.orchestrator.developer_binding import DeveloperBinding
from app.orchestrator.execution_contract import ExecutionAdmission
from app.orchestrator.execution_manager import admit_execution
from app.orchestrator.model_router import DeveloperModelTier
from app.safety.capability_profile import DEVELOPER_V1_PROFILE
from app.workspace import set_test_config
from tests.capability_helpers import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    MODEL,
    ScriptedVerifier,
    declaration,
    expected_binding,
)

VALID_TEST_CONFIG: dict[str, Any] = {
    "runner_id": "generic-subprocess-v1",
    "executable": "pytest",
    "argv": ["-q", "tests"],
    "timeout_seconds": 600,
    "env_allowlist": ["PATH"],
    "network_policy": "unrestricted",
    "output_limits": {"max_stdout_bytes": 1_048_576, "max_stderr_bytes": 262_144},
    "cwd_mode": "task_worktree",
}


class StaticResolver:
    """Tier → o mesmo binding concreto (o contexto esperado de `capability_helpers`)."""

    def resolve(self, tier: DeveloperModelTier) -> DeveloperBinding:
        del tier
        return DeveloperBinding(adapter=ADAPTER_ID, adapter_version=ADAPTER_VERSION, model=MODEL)


RESOLVER = StaticResolver()


def make_prover() -> tuple[VerifyingCapabilityProver, ScriptedVerifier]:
    """Prover positivo de teste (declaração oficial + observação oficial) e o seu verificador."""
    verifier = ScriptedVerifier(observed_profile=DEVELOPER_V1_PROFILE)
    return VerifyingCapabilityProver(verifier=verifier, declaration=declaration()), verifier


def make_expected() -> Any:
    return expected_binding(declaration())


def approved_task(
    session: Session,
    workspace: DevWorkspace,
    artifacts_dir: Path,
    *,
    title: str = "task",
    goal: str = "ajustar o util",
    with_resolver: bool = True,
    configure_tests: bool = True,
) -> WorkspaceTask:
    """Uma task `approved` pelo caminho real (`plan` + `approve`), pronta para a admissão."""
    if configure_tests:
        set_test_config(session, workspace.id, VALID_TEST_CONFIG)
    resolver = RESOLVER if with_resolver else None
    task = create_task(session, workspace.id, title=title, goal=goal)
    planned = plan(
        session, task.id, artifacts_dir=artifacts_dir, developer_binding_resolver=resolver
    )
    approve(
        session,
        task.id,
        execution_fingerprint=planned.fingerprint,
        developer_binding_resolver=resolver,
    )
    session.commit()
    return get_task(session, task.id)


def admit(
    session: Session,
    task_id: str,
    artifacts_dir: Path,
    *,
    key: str = "exec-1",
    with_resolver: bool = True,
    prover: Any = "default",
    expected: Any = "default",
) -> ExecutionAdmission:
    """`admit_execution` como o composition root a chamaria, com prover positivo de teste.

    Em produção cada chamada nasce de uma sessão **nova**. Aqui a sessão do teste é reaproveitada,
    então ela encerra a transação de leitura (o *snapshot* do WAL) e descarta o identity map antes
    de admitir — senão enxergaria o estado antigo de uma linha alterada por outra conexão.
    """
    session.commit()
    session.expire_all()
    chosen_prover = make_prover()[0] if prover == "default" else prover
    chosen_expected = make_expected() if expected == "default" else expected
    return admit_execution(
        session,
        task_id,
        invocation_id=key,
        artifacts_dir=artifacts_dir,
        prover=chosen_prover,
        expected_capability_binding=chosen_expected,
        developer_binding_resolver=RESOLVER if with_resolver else None,
    )
