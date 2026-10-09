"""E8.4.1 — admissão durável de uma execução.

`admit_execution` é a porta interna: `approved → executing` + Run de controle `running` +
`attempts + 1` + slot reservado, numa transação, sem nenhum efeito externo. Estes testes travam:

* o caminho feliz e tudo o que a admissão grava (e o que **não** grava);
* idempotência (repetição, conflito de chave, chave nova com a task em execução);
* as 14 pré-condições, cada uma sem Run e sem tentativa consumida;
* o slot único, atômico entre threads **e entre processos**;
* CAS, falha no meio da transação e cancelamento que vence a corrida;
* a fronteira pública: `start_execution` segue no `NotImplementedError`, sem rota de execução.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings
from app.context_engine import rendered_artifact_intact
from app.db.enums import (
    FilesReadSource,
    RunAgent,
    RunPurpose,
    RunStatus,
    RunTransport,
    SafetyEventKind,
    TaskPhase,
    TaskStatus,
    TokenSource,
    WorkspaceStatus,
)
from app.db.models import DevWorkspace, Run, SafetyEvent, WorkspaceTask
from app.db.session import session_scope
from app.orchestrator import (
    ApprovalFingerprintMismatch,
    ConcurrentTaskUpdate,
    InvalidInvocationId,
    InvalidTransition,
    InvocationIdConflict,
    TaskAlreadyExecuting,
    TransitionGuardFailed,
    cancel,
    planner,
    start_execution,
)
from app.orchestrator import execution_manager as em
from app.orchestrator.errors import InvalidTestConfig
from app.orchestrator.execution_contract import (
    AdmissionOutcome,
    component_invocation_id,
)
from app.orchestrator.fingerprint import WorkflowPolicy
from app.orchestrator.state_machine import check_entry_capability
from app.safety.canonical import canonical_sha256
from app.workspace import set_test_config, update_workspace_status
from tests import context_helpers
from tests.admission_support_e8_4_1 import (
    RESOLVER,
    VALID_TEST_CONFIG,
    admit,
    approved_task,
    make_expected,
    make_prover,
)
from tests.capability_helpers import ScriptedVerifier, declaration, expected_binding
from tests.conftest import API_ROOT

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
KEY = "exec-key-1"


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    return tmp_path / "artifacts"


@pytest.fixture
def approved(session: Session, workspace: DevWorkspace, artifacts_dir: Path) -> WorkspaceTask:
    return approved_task(session, workspace, artifacts_dir)


# ----------------------------------------------------------------------- leitura do banco


def _task(factory: sessionmaker[Session], task_id: str) -> WorkspaceTask:
    with factory() as fresh:
        task = fresh.get(WorkspaceTask, task_id)
        assert task is not None
        fresh.expunge(task)
        return task


def _runs(factory: sessionmaker[Session], task_id: str | None = None) -> list[Run]:
    with factory() as fresh:
        statement = select(Run).order_by(Run.started_at, Run.id)
        if task_id is not None:
            statement = statement.where(Run.task_id == task_id)
        rows = list(fresh.scalars(statement))
        fresh.expunge_all()
        return rows


def _events(factory: sessionmaker[Session], task_id: str) -> list[SafetyEvent]:
    """Eventos de segurança da task **depois da aprovação** (o `approval_granted` do setup fica
    de fora: o que interessa é o que a admissão deixou)."""
    with factory() as fresh:
        rows = list(
            fresh.scalars(
                select(SafetyEvent)
                .where(
                    SafetyEvent.task_id == task_id,
                    SafetyEvent.kind != SafetyEventKind.APPROVAL_GRANTED,
                )
                .order_by(SafetyEvent.created_at, SafetyEvent.id)
            )
        )
        fresh.expunge_all()
        return rows


def _no_effect(
    factory: sessionmaker[Session], task_id: str, *, attempts: int = 0, status: TaskStatus
) -> None:
    """Nada foi consumido nem criado: tentativas intactas, nenhum Run, sem phase."""
    task = _task(factory, task_id)
    assert task.status is status
    assert task.attempts == attempts
    assert task.phase is None
    assert _runs(factory, task_id) == []


def _worktrees(repo: Path) -> list[str]:
    out = subprocess.run(  # noqa: S603 — git de teste, argv literal, sem shell
        ["git", "worktree", "list", "--porcelain"],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line.startswith("worktree ")]


# ===================================================================== caminho feliz


def test_admissao_grava_tudo_numa_so_transacao(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    attempts_antes, version_antes = approved.attempts, approved.version
    antes = datetime.now(UTC)
    admission = admit(session, approved.id, artifacts_dir, key=KEY)
    depois = datetime.now(UTC)

    assert admission.outcome is AdmissionOutcome.ADMITTED
    assert admission.code is None

    # ---- a task, lida por uma sessão NOVA: tudo estava commitado
    task = _task(session_factory, approved.id)
    assert task.status is TaskStatus.EXECUTING
    assert task.phase is TaskPhase.IMPLEMENTING
    assert attempts_antes == 0
    assert task.attempts == attempts_antes + 1 == 1
    assert task.version == version_antes + 1
    assert task.started_at is not None and antes <= task.started_at <= depois
    assert task.finished_at is None and task.failure_reason is None
    assert task.approved_at is not None  # a aprovação segue valendo
    assert task.worktree_path is None  # nenhuma worktree nesta etapa

    # ---- o Run de controle
    [run] = _runs(session_factory, approved.id)
    assert admission.run is not None and admission.run.id == run.id
    assert run.invocation_id == KEY
    assert run.agent is RunAgent.ORCHESTRATOR
    assert run.purpose is RunPurpose.EXECUTION
    assert run.status is RunStatus.RUNNING
    assert run.transport is RunTransport.PROCESS
    assert run.attempt_index == 0 and run.fix_round == 0
    assert run.finished_at is None and run.duration_ms is None
    assert antes <= run.started_at <= depois
    # vínculos de recuperação
    assert run.context_manifest_id == task.approved_manifest_id
    assert run.base_commit == task.planning_base_commit
    assert run.subject_run_id is None and run.supersedes_run_id is None
    assert run.worktree_path is None


def test_run_aberto_nao_afirma_metrica_nenhuma(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    """Métrica indisponível não é métrica medida: `NULL`, nunca `[]`/`0`."""
    admit(session, approved.id, artifacts_dir, key=KEY)
    [run] = _runs(session_factory, approved.id)

    assert run.files_changed is None
    assert run.diff_added is None and run.diff_removed is None
    assert run.files_read is None and run.files_read_source is FilesReadSource.UNAVAILABLE
    assert run.input_tokens is None and run.output_tokens is None
    assert run.token_source is TokenSource.UNAVAILABLE
    assert run.test_summary is None
    assert run.tool_profile_hash is None  # o perfil comprovado é do Run do Developer (E8.4.3)
    assert run.prompt_sha256 is None and run.log_ref is None


def test_a_aprovacao_e_o_fingerprint_seguem_intactos(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    fingerprint = approved.approved_fingerprint
    parts = json.loads(json.dumps(approved.approved_fingerprint_parts))
    plan_hash = approved.plan_hash
    admit(session, approved.id, artifacts_dir, key=KEY)
    task = _task(session_factory, approved.id)
    assert task.approved_fingerprint == fingerprint
    assert task.approved_fingerprint_parts == parts
    assert task.plan_hash == plan_hash


def test_admissao_nao_tem_efeito_externo(
    session: Session,
    approved: WorkspaceTask,
    artifacts_dir: Path,
    repo_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sem worktree, sem provider, sem Test Runner: só leituras de git e do artefato."""
    worktrees = _worktrees(repo_path)
    chamadas: list[list[str]] = []
    original_run = subprocess.run

    def espiao(argv: Any, *args: Any, **kwargs: Any) -> Any:
        chamadas.append([str(item) for item in argv])
        return original_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", espiao)
    prover, verifier = make_prover()

    admission = admit(session, approved.id, artifacts_dir, key=KEY, prover=prover)

    assert admission.outcome is AdmissionOutcome.ADMITTED
    monkeypatch.undo()

    assert chamadas, "o preflight de git é esperado"
    permitidos = {"rev-parse", "symbolic-ref", "status", "--show-object-format"}
    for argv in chamadas:
        assert Path(argv[0]).stem.lower() == "git", argv
        assert "worktree" not in argv and "add" not in argv and "read-tree" not in argv, argv
        assert permitidos & set(argv), argv
    assert _worktrees(repo_path) == worktrees
    # a prova de capability roda uma vez; nenhum provider ou runner existe neste caminho
    assert len(verifier.calls) == 1
    status = context_helpers.GIT
    assert status is not None
    out = subprocess.run(  # noqa: S603
        [status, "status", "--porcelain"], cwd=repo_path, capture_output=True, text=True, check=True
    ).stdout
    assert out.strip() == "", "a árvore principal não pode ser tocada"


# ===================================================================== idempotência


def test_repeticao_com_a_mesma_chave_devolve_a_execucao_existente(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primeira = admit(session, approved.id, artifacts_dir, key=KEY)
    assert primeira.run is not None
    task_antes = _task(session_factory, approved.id)

    # a repetição não faz leitura de git, não prova capability e não escreve nada
    def proibido(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a repetição não pode produzir efeito externo")

    monkeypatch.setattr(em, "preflight", proibido)
    monkeypatch.setattr(em, "repository_object_format", proibido)
    monkeypatch.setattr(em, "rendered_artifact_intact", proibido)
    prover, verifier = make_prover()

    segunda = admit(session, approved.id, artifacts_dir, key=KEY, prover=prover)
    terceira = admit(session, approved.id, artifacts_dir, key=KEY, prover=prover)

    for repetida in (segunda, terceira):
        assert repetida.outcome is AdmissionOutcome.REPLAYED
        assert repetida.run is not None and repetida.run.id == primeira.run.id
        assert repetida.code is None
    assert verifier.calls == []

    task_depois = _task(session_factory, approved.id)
    assert task_depois.attempts == task_antes.attempts == 1  # não incrementa
    assert task_depois.version == task_antes.version  # nenhuma escrita
    assert len(_runs(session_factory)) == 1  # não cria outro Run


def test_repeticao_continua_devolvendo_a_execucao_depois_que_a_task_mudou_de_estado(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    primeira = admit(session, approved.id, artifacts_dir, key=KEY)
    assert primeira.run is not None
    cancel(session, approved.id)
    session.commit()

    repetida = admit(session, approved.id, artifacts_dir, key=KEY)

    assert repetida.outcome is AdmissionOutcome.REPLAYED
    assert repetida.run is not None and repetida.run.id == primeira.run.id
    assert repetida.task.status is TaskStatus.CANCELLED  # o estado real, sem reabrir nada


def test_mesma_chave_em_outra_task_e_conflito(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    outra = approved_task(session, workspace, artifacts_dir, title="outra")
    admit(session, approved.id, artifacts_dir, key=KEY)

    with pytest.raises(InvocationIdConflict) as caught:
        admit(session, outra.id, artifacts_dir, key=KEY)

    assert caught.value.status_code == 409
    assert outra.id not in str(caught.value)
    _no_effect(session_factory, outra.id, status=TaskStatus.APPROVED)
    assert len(_runs(session_factory)) == 1


def test_chave_de_run_que_nao_e_de_controle_tambem_e_conflito(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    """A mesma task, mas o Run dono da chave é de um componente: a chave não é reaproveitável."""
    session.add(
        Run(
            invocation_id=KEY,
            task_id=approved.id,
            agent=RunAgent.DEVELOPER,
            purpose=RunPurpose.EXECUTION,
            provider="p",
            provider_adapter="a",
            transport=RunTransport.API,
            status=RunStatus.RUNNING,
            token_source=TokenSource.UNAVAILABLE,
            files_read_source=FilesReadSource.UNAVAILABLE,
        )
    )
    session.commit()

    with pytest.raises(InvocationIdConflict):
        admit(session, approved.id, artifacts_dir, key=KEY)

    task = _task(session_factory, approved.id)
    assert task.status is TaskStatus.APPROVED and task.attempts == 0


def test_chave_nova_com_a_task_ja_em_execucao_e_conflito(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    primeira = admit(session, approved.id, artifacts_dir, key=KEY)
    assert primeira.run is not None

    with pytest.raises(TaskAlreadyExecuting) as caught:
        admit(session, approved.id, artifacts_dir, key="outra-chave")

    assert caught.value.status_code == 409
    task = _task(session_factory, approved.id)
    assert task.attempts == 1
    assert [r.id for r in _runs(session_factory)] == [primeira.run.id]


@pytest.mark.parametrize("key", ["", "tem espaço", "a:b", "x" * 129, "-ruim"])
def test_chave_invalida_e_recusada_antes_de_qualquer_coisa(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    key: str,
) -> None:
    with pytest.raises(InvalidInvocationId):
        admit(session, approved.id, artifacts_dir, key=key)
    _no_effect(session_factory, approved.id, status=TaskStatus.APPROVED)
    assert _events(session_factory, approved.id) == []


def test_task_inexistente_e_404(session: Session, artifacts_dir: Path) -> None:
    from app.orchestrator import TaskNotFound

    with pytest.raises(TaskNotFound):
        admit(session, "nao-existe", artifacts_dir, key=KEY)


@pytest.mark.parametrize(
    "status",
    [TaskStatus.DRAFT, TaskStatus.AWAITING_APPROVAL, TaskStatus.DONE, TaskStatus.CANCELLED],
)
def test_task_fora_de_approved_nao_tem_aresta_para_executing(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    status: TaskStatus,
) -> None:
    session.execute(
        text("UPDATE workspace_task SET status = :s, approved_at = NULL WHERE id = :id"),
        {"s": status.value, "id": approved.id},
    )
    session.commit()

    with pytest.raises(InvalidTransition):
        admit(session, approved.id, artifacts_dir, key=KEY)

    task = _task(session_factory, approved.id)
    assert task.attempts == 0 and _runs(session_factory) == []


def test_segunda_tentativa_depois_de_needs_fix_usa_chave_nova_e_run_novo(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    from app.orchestrator import approve

    primeira = admit(session, approved.id, artifacts_dir, key=KEY)
    assert primeira.run is not None
    # Simula a finalização da E8.4.5: o Run fecha e a task vai para `needs_fix`.
    agora = datetime.now(UTC)
    with session_factory() as outra:
        outra.execute(
            text(
                "UPDATE run SET status = 'error', finished_at = :f, duration_ms = 5 WHERE id = :id"
            ),
            {"f": agora.astimezone(UTC).replace(tzinfo=None), "id": primeira.run.id},
        )
        outra.execute(
            text(
                "UPDATE workspace_task SET status = 'needs_fix', phase = NULL, "
                "version = version + 1 WHERE id = :id"
            ),
            {"id": approved.id},
        )
        outra.commit()

    session.commit()
    session.expire_all()
    fingerprint = _task(session_factory, approved.id).approved_fingerprint
    assert fingerprint is not None
    approve(
        session,
        approved.id,
        execution_fingerprint=fingerprint,
        developer_binding_resolver=RESOLVER,
    )
    session.commit()

    # a chave da primeira tentativa é a primeira tentativa, não a segunda
    repetida = admit(session, approved.id, artifacts_dir, key=KEY)
    assert repetida.outcome is AdmissionOutcome.REPLAYED
    assert repetida.run is not None and repetida.run.id == primeira.run.id
    assert _task(session_factory, approved.id).attempts == 1

    segunda = admit(session, approved.id, artifacts_dir, key="exec-key-2")

    assert segunda.outcome is AdmissionOutcome.ADMITTED
    assert segunda.run is not None and segunda.run.id != primeira.run.id
    assert segunda.run.attempt_index == 1
    task = _task(session_factory, approved.id)
    assert task.attempts == 2
    runs = _runs(session_factory, approved.id)
    assert [r.status for r in runs] == [RunStatus.ERROR, RunStatus.RUNNING]


# ============================================================ recusas de pré-admissão


def _refused(
    factory: sessionmaker[Session],
    task_id: str,
    *,
    status: TaskStatus = TaskStatus.APPROVED,
    attempts: int = 0,
) -> None:
    """A recusa não criou Run e não consumiu tentativa."""
    task = _task(factory, task_id)
    assert task.status is status
    assert task.attempts == attempts
    assert task.phase is None
    assert _runs(factory, task_id) == []


def _guard_event(factory: sessionmaker[Session], task_id: str, guard: str) -> SafetyEvent:
    eventos = [e for e in _events(factory, task_id) if e.rule_id.endswith(f".{guard}")]
    assert len(eventos) == 1, [e.rule_id for e in _events(factory, task_id)]
    assert eventos[0].decision.value == "deny"
    return eventos[0]


def test_fingerprint_invalido_invalida_a_aprovacao_sem_run_nem_tentativa(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    workspace: DevWorkspace,
) -> None:
    set_test_config(session, workspace.id, {**VALID_TEST_CONFIG, "argv": ["-q", "tests", "-x"]})
    session.commit()

    with pytest.raises(ApprovalFingerprintMismatch) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert "test_binding" in caught.value.diverged_fields
    _refused(session_factory, approved.id, status=TaskStatus.AWAITING_APPROVAL)
    task = _task(session_factory, approved.id)
    assert task.approved_at is None  # a aprovação foi invalidada
    _guard_event(session_factory, approved.id, "fingerprint_still_valid")


def test_head_que_mudou_exige_replanejar(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    repo_path: Path,
) -> None:
    context_helpers.write(repo_path, "novo.txt", "commit posterior\n")
    context_helpers.commit_all(repo_path, "depois da aprovação")

    with pytest.raises(TransitionGuardFailed) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert caught.value.guard == "head_matches_planning_base_commit"
    assert caught.value.requires_replan is True
    _refused(session_factory, approved.id, status=TaskStatus.AWAITING_APPROVAL)
    _guard_event(session_factory, approved.id, "head_matches_planning_base_commit")


def test_tentativas_esgotadas(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    session.execute(
        text("UPDATE workspace_task SET attempts = 2 WHERE id = :id"), {"id": approved.id}
    )
    session.commit()

    with pytest.raises(TransitionGuardFailed) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert caught.value.guard == "attempts_below_max"
    _refused(session_factory, approved.id, attempts=2)
    assert _guard_event(session_factory, approved.id, "attempts_below_max").kind is (
        SafetyEventKind.RETRY_LIMIT
    )


def test_cancelamento_solicitado(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    session.execute(
        text("UPDATE workspace_task SET cancel_requested = 1 WHERE id = :id"), {"id": approved.id}
    )
    session.commit()

    with pytest.raises(TransitionGuardFailed) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert caught.value.guard == "not_cancelled"
    _refused(session_factory, approved.id)
    assert _guard_event(session_factory, approved.id, "not_cancelled").kind is (
        SafetyEventKind.CANCELLED
    )


def test_workspace_arquivado(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    workspace: DevWorkspace,
) -> None:
    update_workspace_status(session, workspace.id, WorkspaceStatus.ARCHIVED)
    session.commit()

    with pytest.raises(TransitionGuardFailed) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert caught.value.guard == "workspace_active"
    _refused(session_factory, approved.id)


def test_artefato_renderizado_ausente_ou_corrompido(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    [blob] = [p for p in artifacts_dir.iterdir() if p.suffix == ".json"]
    original = blob.read_bytes()

    blob.write_bytes(original + b" ")  # corrompido: o sha256 dos bytes não bate mais
    with pytest.raises(TransitionGuardFailed) as corrompido:
        admit(session, approved.id, artifacts_dir, key=KEY)
    assert corrompido.value.guard == "plan_artifacts_coherent"
    _refused(session_factory, approved.id)

    blob.unlink()  # ausente
    with pytest.raises(TransitionGuardFailed) as ausente:
        admit(session, approved.id, artifacts_dir, key="exec-key-2")
    assert ausente.value.guard == "plan_artifacts_coherent"
    _refused(session_factory, approved.id)

    blob.write_bytes(original)  # restaurado: volta a ser admissível
    assert admit(session, approved.id, artifacts_dir, key="exec-key-3").run is not None


def test_plano_adulterado(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    session.execute(
        text("UPDATE workspace_task SET plan_hash = :h WHERE id = :id"),
        {"h": "0" * 64, "id": approved.id},
    )
    session.commit()

    with pytest.raises(TransitionGuardFailed) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert caught.value.guard == "plan_artifacts_coherent"
    _refused(session_factory, approved.id)


def test_referencia_do_artefato_incoerente_com_o_hash_do_manifest(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    session.execute(
        text("UPDATE context_manifest SET rendered_context_ref = 'artifacts/outro.json'")
    )
    session.commit()

    with pytest.raises(TransitionGuardFailed) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert caught.value.guard == "plan_artifacts_coherent"
    _refused(session_factory, approved.id)


@pytest.mark.parametrize("coluna", ["base_commit", "planning_base_commit"])
def test_commit_base_incoerente_entre_task_manifest_e_plano(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    coluna: str,
) -> None:
    session.execute(
        text(f"UPDATE workspace_task SET {coluna} = :c WHERE id = :id"),  # noqa: S608
        {"c": "b" * 40, "id": approved.id},
    )
    session.commit()

    with pytest.raises(TransitionGuardFailed) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert caught.value.guard == "base_commit_coherent"
    _refused(session_factory, approved.id)


def test_binding_do_developer_ausente_na_aprovacao(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    artifacts_dir: Path,
) -> None:
    sem_binding = approved_task(session, workspace, artifacts_dir, with_resolver=False)

    with pytest.raises(TransitionGuardFailed) as caught:
        admit(session, sem_binding.id, artifacts_dir, key=KEY, with_resolver=False)

    assert caught.value.guard == "developer_binding_approved"
    _refused(session_factory, sem_binding.id)


@pytest.mark.parametrize("campo", ["adapter_id", "adapter_version", "model"])
def test_contexto_esperado_da_prova_diferente_do_binding_aprovado(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    campo: str,
) -> None:
    decl = declaration()
    outro = expected_binding(decl, **{campo: "valor-diferente"})
    prover, verifier = make_prover()

    with pytest.raises(TransitionGuardFailed) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY, prover=prover, expected=outro)

    assert caught.value.guard == "developer_binding_approved"
    assert verifier.calls == [], "a prova nem é paga quando o binding não é o aprovado"
    _refused(session_factory, approved.id)


def test_capability_nao_comprovada_e_recusada_sem_run(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    with pytest.raises(TransitionGuardFailed) as ausente:
        admit(session, approved.id, artifacts_dir, key=KEY, prover=None)
    assert ausente.value.guard == "capability_profile_proven"
    assert ausente.value.reason_code == "verifier_absent"

    sem_contexto = make_prover()[0]
    with pytest.raises(TransitionGuardFailed) as sem_esperado:
        admit(session, approved.id, artifacts_dir, key=KEY, prover=sem_contexto, expected=None)
    assert sem_esperado.value.reason_code == "expected_context_missing"

    from app.capability_wiring import VerifyingCapabilityProver
    from app.safety import EnforcementMode
    from tests.capability_helpers import profile

    divergente = VerifyingCapabilityProver(
        verifier=ScriptedVerifier(observed_profile=profile(git_read=EnforcementMode.UNMEDIATED)),
        declaration=declaration(),
    )
    with pytest.raises(TransitionGuardFailed) as negativo:
        admit(session, approved.id, artifacts_dir, key=KEY, prover=divergente)
    assert negativo.value.guard == "capability_profile_matches_approved"

    _refused(session_factory, approved.id)
    kinds = [e.kind for e in _events(session_factory, approved.id)]
    assert kinds.count(SafetyEventKind.CAPABILITY_UNENFORCEABLE) == 2
    assert kinds.count(SafetyEventKind.CAPABILITY_DENIED) == 1


def test_falha_tecnica_do_verificador_nao_vira_politica_nem_consome_nada(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    from app.capability_wiring import VerifyingCapabilityProver

    quebrado = VerifyingCapabilityProver(
        verifier=ScriptedVerifier(observed_profile=None, raises=RuntimeError("quebrou")),
        declaration=declaration(),
    )
    with pytest.raises(RuntimeError, match="quebrou"):
        admit(session, approved.id, artifacts_dir, key=KEY, prover=quebrado)

    _refused(session_factory, approved.id)
    assert _events(session_factory, approved.id) == []


# ------------------------------------------------- política E8, limites e composição


def _normal_policy(decision: Any) -> WorkflowPolicy:
    """A política normal futura (E9): auditoria obrigatória."""
    return WorkflowPolicy(
        max_fix_rounds=decision.max_fix_rounds, max_attempts=decision.max_attempts
    )


def test_aprovacao_feita_sob_outra_politica_nao_executa_em_silencio(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    artifacts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # aprovada enquanto a política ativa exigia auditoria (como será a partir da E9)
    monkeypatch.setattr(planner, "active_workflow_policy", _normal_policy)
    sob_outra = approved_task(session, workspace, artifacts_dir)
    monkeypatch.undo()
    assert sob_outra.approved_fingerprint_parts is not None
    assert (
        sob_outra.approved_fingerprint_parts["workflow_policy_hash"]
        == WorkflowPolicy(max_fix_rounds=2, max_attempts=2).policy_hash()
    ), "a aprovação carrega o hash da política NORMAL, não o da E8"

    with pytest.raises(ApprovalFingerprintMismatch) as caught:
        admit(session, sob_outra.id, artifacts_dir, key=KEY)

    assert caught.value.diverged_fields == ("workflow_policy_hash",)
    _refused(session_factory, sob_outra.id, status=TaskStatus.AWAITING_APPROVAL)
    assert _task(session_factory, sob_outra.id).approved_at is None


def test_politica_vigente_com_auditoria_obrigatoria_e_inexecutavel_sem_auditor(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    artifacts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(planner, "active_workflow_policy", _normal_policy)
    monkeypatch.setattr(em, "active_workflow_policy", _normal_policy)
    aprovada = approved_task(session, workspace, artifacts_dir)

    with pytest.raises(TransitionGuardFailed) as caught:
        admit(session, aprovada.id, artifacts_dir, key=KEY)

    assert caught.value.guard == "workflow_policy_supported"
    _refused(session_factory, aprovada.id)
    # nenhum AuditRun nem AuditFinding fictício
    with session_factory() as fresh:
        assert fresh.scalar(select(func.count()).select_from(Run)) == 0
        assert fresh.execute(text("SELECT COUNT(*) FROM audit_finding")).scalar_one() == 0


@pytest.mark.parametrize(("constante", "novo"), [("RUN_TIMEOUT_S", 600), ("TASK_TIMEOUT_S", 900)])
def test_mudar_run_timeout_ou_task_timeout_invalida_a_aprovacao(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    constante: str,
    novo: int,
) -> None:
    assert approved.approved_fingerprint_parts is not None
    limites = approved.approved_fingerprint_parts["execution_limits"]
    assert limites["run_timeout_s"] == 1200 and limites["task_timeout_s"] == 1800

    monkeypatch.setattr(planner, constante, novo)
    with pytest.raises(ApprovalFingerprintMismatch) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert caught.value.diverged_fields == ("execution_limits",)
    _refused(session_factory, approved.id, status=TaskStatus.AWAITING_APPROVAL)


def test_aprovacao_anterior_sem_timeouts_diverge_do_fingerprint_novo(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    """Uma aprovação gravada antes da E8.4.1 (sem os dois limites) não executa."""
    assert approved.approved_fingerprint_parts is not None
    antigas = json.loads(json.dumps(approved.approved_fingerprint_parts))
    del antigas["execution_limits"]["run_timeout_s"]
    del antigas["execution_limits"]["task_timeout_s"]
    session.execute(
        text("UPDATE workspace_task SET approved_fingerprint_parts = :p WHERE id = :id"),
        {"p": json.dumps(antigas), "id": approved.id},
    )
    # e o hash aprovado é o do documento antigo, que não cobria os timeouts
    session.execute(
        text("UPDATE workspace_task SET approved_fingerprint = :h WHERE id = :id"),
        {"h": canonical_sha256(antigas), "id": approved.id},
    )
    session.commit()

    with pytest.raises(ApprovalFingerprintMismatch) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert caught.value.diverged_fields == ("execution_limits",)
    _refused(session_factory, approved.id, status=TaskStatus.AWAITING_APPROVAL)


@pytest.mark.parametrize("extra", [["architect"], ["researcher"], ["architect", "researcher"]])
def test_composicao_com_agente_indisponivel_e_recusada(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    extra: list[str],
) -> None:
    session.execute(
        text("UPDATE workspace_task SET agents = :a WHERE id = :id"),
        {"a": json.dumps(["developer", *extra]), "id": approved.id},
    )
    session.commit()

    with pytest.raises(TransitionGuardFailed) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert caught.value.guard == "composition_available"
    _refused(session_factory, approved.id)


def test_composicao_vazia_ou_sem_developer_e_recusada(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    for agents in ([], ["architect"]):
        session.execute(
            text("UPDATE workspace_task SET agents = :a WHERE id = :id"),
            {"a": json.dumps(agents), "id": approved.id},
        )
        session.commit()
        with pytest.raises(TransitionGuardFailed) as caught:
            admit(session, approved.id, artifacts_dir, key=KEY)
        assert caught.value.guard == "composition_available"
    _refused(session_factory, approved.id)


# ------------------------------------------------------------ TestPolicy e formato do repo


def test_sem_test_policy_a_execucao_nao_e_admitida(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    artifacts_dir: Path,
) -> None:
    sem_politica = approved_task(session, workspace, artifacts_dir, configure_tests=False)

    with pytest.raises(TransitionGuardFailed) as caught:
        admit(session, sem_politica.id, artifacts_dir, key=KEY)

    assert caught.value.guard == "test_policy_supported"
    _refused(session_factory, sem_politica.id)


def test_runner_nao_suportado_e_recusado(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    artifacts_dir: Path,
) -> None:
    set_test_config(session, workspace.id, {**VALID_TEST_CONFIG, "runner_id": "outro-runner-v9"})
    outro = approved_task(session, workspace, artifacts_dir, configure_tests=False)

    with pytest.raises(TransitionGuardFailed) as caught:
        admit(session, outro.id, artifacts_dir, key=KEY)

    assert caught.value.guard == "test_policy_supported"
    _refused(session_factory, outro.id)


def test_test_config_malformado_na_leitura_e_recusado_como_422(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    workspace: DevWorkspace,
) -> None:
    session.execute(
        text("UPDATE dev_workspace SET test_config = '{\"runner_id\": 1}' WHERE id = :id"),
        {"id": workspace.id},
    )
    session.commit()

    with pytest.raises(InvalidTestConfig) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert caught.value.status_code == 422
    _refused(session_factory, approved.id)


def test_repositorio_sha256_e_recusado_antes_da_admissao(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(em, "repository_object_format", lambda _path: "sha256")

    with pytest.raises(TransitionGuardFailed) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert caught.value.guard == "object_format_supported"
    assert "SHA-1" in caught.value.message
    _refused(session_factory, approved.id)


def test_formato_ilegivel_tambem_e_recusado(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(em, "repository_object_format", lambda _path: None)

    with pytest.raises(TransitionGuardFailed) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert caught.value.guard == "object_format_supported"
    _refused(session_factory, approved.id)


def test_repositorio_sha256_real_e_recusado(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    workspace: DevWorkspace,
    tmp_path: Path,
) -> None:
    """Um repositório SHA-256 **de verdade**, não só um dublê da leitura."""
    git = context_helpers.GIT
    assert git is not None
    repo = tmp_path / "repo-sha256"
    probe = subprocess.run(  # noqa: S603
        [git, "init", "-q", "--object-format=sha256", str(repo)],
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("o Git instalado não suporta --object-format=sha256")
    context_helpers.git(repo, "config", "user.email", "t@example.invalid")
    context_helpers.git(repo, "config", "user.name", "T")
    context_helpers.git(repo, "config", "commit.gpgsign", "false")
    context_helpers.write(repo, "a.txt", "a\n")
    context_helpers.commit_all(repo, "inicial")
    session.execute(
        text("UPDATE dev_workspace SET local_path = :p WHERE id = :id"),
        {"p": str(repo), "id": workspace.id},
    )
    session.commit()

    with pytest.raises(TransitionGuardFailed) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert caught.value.guard == "object_format_supported"
    _refused(session_factory, approved.id)


# =========================================================================== slot


def test_slot_ocupado_devolve_slot_busy_estruturado_e_nao_consome_nada(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    segunda = approved_task(session, workspace, artifacts_dir, title="segunda")
    primeira = admit(session, approved.id, artifacts_dir, key=KEY)
    assert primeira.outcome is AdmissionOutcome.ADMITTED
    prover, verifier = make_prover()

    ocupada = admit(session, segunda.id, artifacts_dir, key="exec-key-2", prover=prover)

    assert ocupada.outcome is AdmissionOutcome.SLOT_BUSY
    assert ocupada.code == "slot_busy"
    assert ocupada.run is None
    assert ocupada.task.status is TaskStatus.APPROVED
    _refused(session_factory, segunda.id)
    assert verifier.calls == [], "slot ocupado não paga a prova de capability"
    assert _events(session_factory, segunda.id) == []  # fila não é decisão de política
    assert len(_runs(session_factory)) == 1


def test_slot_busy_e_repetivel_e_a_task_continua_admissivel_quando_o_slot_libera(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    segunda = approved_task(session, workspace, artifacts_dir, title="segunda")
    admit(session, approved.id, artifacts_dir, key=KEY)

    for _ in range(3):
        assert admit(session, segunda.id, artifacts_dir, key="k2").outcome is (
            AdmissionOutcome.SLOT_BUSY
        )
    _refused(session_factory, segunda.id)

    cancel(session, approved.id)  # sai de `executing`: a admissão volta a ser possível
    session.commit()

    admitida = admit(session, segunda.id, artifacts_dir, key="k2")
    assert admitida.outcome is AdmissionOutcome.ADMITTED
    assert _task(session_factory, segunda.id).attempts == 1


def test_o_banco_recusa_duas_tasks_executing_mesmo_sem_passar_pelo_servico(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    """A garantia é do schema (índice único parcial), não de um lock Python em memória."""
    from sqlalchemy.exc import IntegrityError

    segunda = approved_task(session, workspace, artifacts_dir, title="segunda")
    admit(session, approved.id, artifacts_dir, key=KEY)

    with pytest.raises(IntegrityError, match="UNIQUE"):
        session.execute(
            text(
                "UPDATE workspace_task SET status = 'executing', phase = 'implementing' "
                "WHERE id = :id"
            ),
            {"id": segunda.id},
        )
    session.rollback()


# ================================================================ concorrência (threads)


def _barreira_de_threads(
    factory: sessionmaker[Session], chamadas: list[tuple[str, str]], artifacts_dir: Path
) -> list[Any]:
    """Cada chamada `(task_id, key)` roda em sua thread, com sessão própria, largando juntas."""
    barreira = threading.Barrier(len(chamadas))
    resultados: list[Any] = [None] * len(chamadas)

    def alvo(indice: int, task_id: str, key: str) -> None:
        try:
            with session_scope(factory) as sessao:
                barreira.wait(timeout=60)
                resultados[indice] = admit(sessao, task_id, artifacts_dir, key=key)
        except Exception as error:
            resultados[indice] = error

    threads = [
        threading.Thread(target=alvo, args=(i, task_id, key))
        for i, (task_id, key) in enumerate(chamadas)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
        assert not thread.is_alive()
    return resultados


def test_duas_tasks_concorrentes_so_uma_ocupa_o_slot(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    artifacts_dir: Path,
) -> None:
    tasks = [approved_task(session, workspace, artifacts_dir, title=f"t{i}") for i in range(2)]

    resultados = _barreira_de_threads(
        session_factory, [(t.id, f"key-{i}") for i, t in enumerate(tasks)], artifacts_dir
    )

    erros = [r for r in resultados if isinstance(r, Exception)]
    assert not erros, erros
    desfechos = sorted(r.outcome.value for r in resultados)
    assert desfechos == ["admitted", "slot_busy"]

    executando = [t for t in tasks if _task(session_factory, t.id).status is TaskStatus.EXECUTING]
    assert len(executando) == 1
    assert sorted(_task(session_factory, t.id).attempts for t in tasks) == [0, 1]
    [run] = _runs(session_factory)
    assert run.task_id == executando[0].id and run.status is RunStatus.RUNNING


def test_muitas_tasks_concorrentes_reservam_exatamente_um_slot(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    artifacts_dir: Path,
) -> None:
    tasks = [approved_task(session, workspace, artifacts_dir, title=f"t{i}") for i in range(6)]

    resultados = _barreira_de_threads(
        session_factory, [(t.id, f"key-{i}") for i, t in enumerate(tasks)], artifacts_dir
    )

    erros = [r for r in resultados if isinstance(r, Exception)]
    assert not erros, erros
    assert sorted(r.outcome.value for r in resultados) == ["admitted"] + ["slot_busy"] * 5
    assert len(_runs(session_factory)) == 1
    assert sum(_task(session_factory, t.id).attempts for t in tasks) == 1


def test_mesma_task_e_mesma_chave_concorrentes_admitem_uma_vez_e_repetem_a_outra(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    resultados = _barreira_de_threads(session_factory, [(approved.id, KEY)] * 4, artifacts_dir)

    erros = [r for r in resultados if isinstance(r, Exception)]
    assert not erros, erros
    assert sorted(r.outcome.value for r in resultados) == ["admitted"] + ["replayed"] * 3
    assert len({r.run.id for r in resultados}) == 1
    assert _task(session_factory, approved.id).attempts == 1  # exatamente uma vez
    assert len(_runs(session_factory)) == 1


def test_mesma_task_com_chaves_diferentes_concorrentes_admitem_uma_so(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    resultados = _barreira_de_threads(
        session_factory, [(approved.id, f"key-{i}") for i in range(4)], artifacts_dir
    )

    admitidos = [r for r in resultados if not isinstance(r, Exception)]
    erros = [r for r in resultados if isinstance(r, Exception)]
    assert len(admitidos) == 1 and admitidos[0].outcome is AdmissionOutcome.ADMITTED
    assert all(isinstance(e, TaskAlreadyExecuting) for e in erros), erros
    assert len(erros) == 3
    assert _task(session_factory, approved.id).attempts == 1
    assert len(_runs(session_factory)) == 1


def test_mesma_chave_em_tasks_diferentes_concorrentes_so_uma_vence(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    artifacts_dir: Path,
) -> None:
    tasks = [approved_task(session, workspace, artifacts_dir, title=f"t{i}") for i in range(2)]

    resultados = _barreira_de_threads(session_factory, [(t.id, KEY) for t in tasks], artifacts_dir)

    admitidos = [r for r in resultados if not isinstance(r, Exception)]
    erros = [r for r in resultados if isinstance(r, Exception)]
    assert len(admitidos) == 1 and admitidos[0].outcome is AdmissionOutcome.ADMITTED
    assert len(erros) == 1 and isinstance(erros[0], InvocationIdConflict), erros
    assert sum(_task(session_factory, t.id).attempts for t in tasks) == 1
    assert len(_runs(session_factory)) == 1


# =============================================================== concorrência (processos)


def _workers(
    tmp_path: Path,
    temp_settings: AppSettings,
    artifacts_dir: Path,
    chamadas: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Um processo Python por chamada; todos esperam o `go` e largam juntos."""
    go = tmp_path / "go"
    processos: list[tuple[subprocess.Popen[str], Path]] = []
    for indice, (task_id, key) in enumerate(chamadas):
        ready = tmp_path / f"ready-{indice}"
        spec = {
            "data_dir": str(temp_settings.data_dir),
            "database_url": temp_settings.database_url,
            "artifacts_dir": str(artifacts_dir),
            "task_id": task_id,
            "key": key,
            "ready": str(ready),
            "go": str(go),
        }
        processo = subprocess.Popen(  # noqa: S603 — worker de teste, argv literal, sem shell
            [sys.executable, "-m", "tests.admission_worker_e8_4_1", json.dumps(spec)],
            cwd=API_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processos.append((processo, ready))

    import time

    deadline = time.monotonic() + 120
    while not all(ready.exists() for _, ready in processos):
        assert time.monotonic() < deadline, "workers não ficaram prontos"
        for processo, _ in processos:
            assert processo.poll() is None, processo.communicate()
        time.sleep(0.05)
    go.write_text("1", encoding="utf-8")

    resultados: list[dict[str, Any]] = []
    for processo, _ in processos:
        saida, erro = processo.communicate(timeout=180)
        assert processo.returncode == 0, erro
        linha = [item for item in saida.splitlines() if item.startswith("RESULT ")][-1]
        resultados.append(json.loads(linha.removeprefix("RESULT ")))
    return resultados


def test_processos_distintos_disputam_o_slot_e_so_um_vence(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    artifacts_dir: Path,
    temp_settings: AppSettings,
    tmp_path: Path,
) -> None:
    """Um lock Python em memória não passaria aqui: são processos diferentes."""
    tasks = [approved_task(session, workspace, artifacts_dir, title=f"t{i}") for i in range(3)]

    resultados = _workers(
        tmp_path,
        temp_settings,
        artifacts_dir,
        [(t.id, f"proc-key-{i}") for i, t in enumerate(tasks)],
    )

    assert all("error" not in r for r in resultados), resultados
    assert sorted(r["outcome"] for r in resultados) == ["admitted", "slot_busy", "slot_busy"]
    executando = [t for t in tasks if _task(session_factory, t.id).status is TaskStatus.EXECUTING]
    assert len(executando) == 1
    assert sum(_task(session_factory, t.id).attempts for t in tasks) == 1
    [run] = _runs(session_factory)
    assert run.task_id == executando[0].id and run.status is RunStatus.RUNNING


def test_processos_distintos_com_a_mesma_task_e_chave_admitem_uma_vez(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    temp_settings: AppSettings,
    tmp_path: Path,
) -> None:
    resultados = _workers(
        tmp_path, temp_settings, artifacts_dir, [(approved.id, KEY), (approved.id, KEY)]
    )

    assert all("error" not in r for r in resultados), resultados
    assert sorted(r["outcome"] for r in resultados) == ["admitted", "replayed"]
    assert len({r["run_id"] for r in resultados}) == 1
    assert _task(session_factory, approved.id).attempts == 1
    assert len(_runs(session_factory)) == 1


# ====================================================== CAS, falha no meio, cancelamento


def _com_efeito_antes_do_claim(
    monkeypatch: pytest.MonkeyPatch,
    factory: sessionmaker[Session],
    efeito: Any,
) -> None:
    """Executa `efeito(sessao)` numa sessão OUTRA, logo depois da prova de capability e antes
    do claim — o ponto exato em que uma corrida real pode acontecer."""
    original = check_entry_capability
    em_efeito = {"ativo": False}

    def decorado(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        if em_efeito["ativo"]:  # a admissão do próprio efeito não dispara outro efeito
            return
        em_efeito["ativo"] = True
        try:
            with factory() as outra:
                efeito(outra)
                outra.commit()
        finally:
            em_efeito["ativo"] = False

    monkeypatch.setattr(em, "check_entry_capability", decorado)


def test_cas_com_versao_divergente_nao_admite_nada(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    versao = approved.version

    def bump(outra: Session) -> None:
        outra.execute(
            text("UPDATE workspace_task SET version = version + 1 WHERE id = :id"),
            {"id": approved.id},
        )

    _com_efeito_antes_do_claim(monkeypatch, session_factory, bump)

    with pytest.raises(ConcurrentTaskUpdate) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert caught.value.status_code == 409
    task = _task(session_factory, approved.id)
    assert task.status is TaskStatus.APPROVED
    assert task.attempts == 0 and task.phase is None
    assert task.version == versao + 1  # só a escrita concorrente
    assert _runs(session_factory) == []


def test_cancelamento_que_vence_a_corrida_nao_e_sobrescrito(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _com_efeito_antes_do_claim(monkeypatch, session_factory, lambda s: cancel(s, approved.id))

    with pytest.raises(InvalidTransition) as caught:
        admit(session, approved.id, artifacts_dir, key=KEY)

    assert caught.value.current == "cancelled"
    task = _task(session_factory, approved.id)
    assert task.status is TaskStatus.CANCELLED  # terminal intacto
    assert task.attempts == 0 and task.phase is None
    assert task.failure_reason is None
    assert _runs(session_factory) == []


def test_outra_task_que_toma_o_slot_entre_a_checagem_e_o_claim_vira_slot_busy(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    approved: WorkspaceTask,
    artifacts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rival_id = approved_task(session, workspace, artifacts_dir, title="rival").id

    def rival_ocupa(outra: Session) -> None:
        admit(outra, rival_id, artifacts_dir, key="rival-key")

    _com_efeito_antes_do_claim(monkeypatch, session_factory, rival_ocupa)

    resultado = admit(session, approved.id, artifacts_dir, key=KEY)

    assert resultado.outcome is AdmissionOutcome.SLOT_BUSY
    _refused(session_factory, approved.id)
    assert _task(session_factory, rival_id).status is TaskStatus.EXECUTING


def test_chave_tomada_no_meio_tempo_e_conflito_e_nao_slot_busy(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    approved: WorkspaceTask,
    artifacts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resposta determinística de uma chave que outra admissão acabou de tomar é a da chave
    (conflito), não a "ocupado": o rival leva o slot **e** a chave enquanto esta chamada lê."""
    rival_id = approved_task(session, workspace, artifacts_dir, title="rival").id
    original = rendered_artifact_intact
    estado = {"feito": False}

    def lendo_o_artefato(*args: Any, **kwargs: Any) -> bool:
        if not estado["feito"]:
            estado["feito"] = True
            with session_factory() as outra:
                assert admit(outra, rival_id, artifacts_dir, key=KEY).run is not None
        return original(*args, **kwargs)

    monkeypatch.setattr(em, "rendered_artifact_intact", lendo_o_artefato)

    with pytest.raises(InvocationIdConflict):
        admit(session, approved.id, artifacts_dir, key=KEY)

    _refused(session_factory, approved.id)
    assert _task(session_factory, rival_id).status is TaskStatus.EXECUTING


def test_falha_no_meio_da_transacao_desfaz_tudo(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    """O CAS e o `attempts + 1` já foram para o banco quando o INSERT do Run falha."""

    versao = approved.version

    def falha(*_args: Any) -> None:
        raise RuntimeError("falha injetada no INSERT do Run")

    event.listen(Run, "before_insert", falha)
    try:
        with pytest.raises(RuntimeError, match="falha injetada"):
            admit(session, approved.id, artifacts_dir, key=KEY)
    finally:
        event.remove(Run, "before_insert", falha)

    _refused(session_factory, approved.id)
    assert _task(session_factory, approved.id).version == versao
    assert _events(session_factory, approved.id) == []

    # e a task continua admissível depois que a causa some
    assert admit(session, approved.id, artifacts_dir, key=KEY).outcome is AdmissionOutcome.ADMITTED


def test_falha_no_commit_da_admissao_desfaz_tudo(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def falha_no_commit(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("disco cheio")

    # o claim é o último `commit` da admissão; as leituras anteriores já commitaram
    original = Session.commit
    estado = {"chamadas": 0}

    def commit_que_falha_no_claim(self: Session) -> None:
        estado["chamadas"] += 1
        if estado["chamadas"] == 3:  # fim da leitura estática, fim da leitura de fingerprint, claim
            falha_no_commit()
        original(self)

    monkeypatch.setattr(Session, "commit", commit_que_falha_no_claim)
    with pytest.raises(RuntimeError, match="disco cheio"):
        admit(session, approved.id, artifacts_dir, key=KEY)
    monkeypatch.undo()

    _refused(session_factory, approved.id)


# ======================================================== separação de Runs e fronteira


def test_run_de_controle_nao_usa_subject_run_id_e_os_ids_dos_componentes_sao_derivados(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
    artifacts_dir: Path,
) -> None:
    admission = admit(session, approved.id, artifacts_dir, key=KEY)
    assert admission.run is not None

    developer = component_invocation_id(admission.run.id, "developer")
    runner = component_invocation_id(admission.run.id, "test_runner")

    assert admission.run.subject_run_id is None
    assert len({KEY, developer, runner}) == 3
    # nenhum Run de componente existe ainda: E8.4.3 e E8.4.4 os criam
    assert [r.agent for r in _runs(session_factory, approved.id)] == [RunAgent.ORCHESTRATOR]
    # e a chave de um componente derivado jamais é uma chave de cliente válida
    with pytest.raises(InvalidInvocationId):
        admit(session, approved.id, artifacts_dir, key=developer)


def test_start_execution_publico_segue_no_limite_e_nao_admite(
    session: Session,
    session_factory: sessionmaker[Session],
    approved: WorkspaceTask,
) -> None:
    prover, verifier = make_prover()
    with pytest.raises(NotImplementedError):
        start_execution(
            session,
            approved.id,
            prover=prover,
            expected_capability_binding=make_expected(),
            developer_binding_resolver=RESOLVER,
        )

    assert len(verifier.calls) == 1  # a guarda de fato passou
    _refused(session_factory, approved.id)  # approved, sem Run, sem tentativa consumida


def test_admissao_nao_e_alcancavel_pela_camada_http() -> None:
    """Nenhum módulo de `app/api` nem `app/main` referencia a admissão: sem worker, uma rota
    deixaria tasks presas em `executing`."""
    for path in [*(APP_ROOT / "api").rglob("*.py"), APP_ROOT / "main.py"]:
        texto = path.read_text(encoding="utf-8")
        assert "admit_execution" not in texto, path.name
        assert "execution_contract" not in texto, path.name
        assert "/execute" not in texto, path.name


def test_execution_manager_e_contrato_nao_importam_provider_runner_nem_processos() -> None:
    proibidos = (
        "app.agent_runtime",
        "app.process_runtime",
        "app.tool_executor",
        "app.execution_verification",
        "app.developer_wiring",
        "app.capability_wiring",
        "subprocess",
    )
    for nome in ("execution_manager.py", "execution_contract.py"):
        tree = ast.parse((APP_ROOT / "orchestrator" / nome).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modulos: list[str] = []
            if isinstance(node, ast.Import):
                modulos = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modulos = [node.module]
            for modulo in modulos:
                assert not modulo.startswith(proibidos), f"{nome}: {modulo}"


def test_a_transacao_de_claim_nao_faz_io_externo() -> None:
    """`_claim_execution` não chama git, rede, filesystem nem subprocesso."""
    tree = ast.parse((APP_ROOT / "orchestrator" / "execution_manager.py").read_text("utf-8"))
    [claim] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_claim_execution"
    ]
    nomes = {ast.unparse(node.func) for node in ast.walk(claim) if isinstance(node, ast.Call)}
    for nome in nomes:
        assert not nome.startswith(
            ("preflight", "repository_object_format", "rendered_artifact_intact", "open", "os.")
        ), nome
        assert "subprocess" not in nome and "Path" not in nome, nome
