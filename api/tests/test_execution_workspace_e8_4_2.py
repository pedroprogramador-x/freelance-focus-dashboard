"""E8.4.2 — preparação da execution workspace (componente interno, não operacional).

Tudo com Git e SQLite **reais** em diretórios temporários (fora do repositório e do OneDrive):
admissão real da E8.4.1, `prepare_worktree_root`, `CheckedTreeWriter`, `create_worktree`,
`PostExecutionVerifier.capture_main_tree` e o binding concreto do `tool_executor`. As corridas são
injetadas de forma determinística nas fronteiras (`GitOperations`, portas, relógio, sinal de
cancelamento) — nenhum `sleep`.

Mapa para os critérios de aceite da E8.4.2 (numeração do contrato): 1 caminho feliz; 2 replay e
slot ocupado; 3 principal suja; 4 monorepo; 5 `HEAD` em cada fronteira; 6 layout/identidades; 7 e
19 `id8`/ownership/resíduo; 8 reuso legítimo; 9 worktree inutilizável; 10 snapshot; 11 uso único
do snapshot; 12 dimensões do binding; 13 raiz trocada; 14 cancelamento; 15 prazo; 16 falhas de
persistência e de binding; 17 CAS perdido; 18 concorrência; 20 métricas; 21 filtros/hooks;
22 vazamento; 23 gates (aqui e em `test_architecture`).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings
from app.db.enums import (
    FilesReadSource,
    RunAgent,
    RunPurpose,
    RunStatus,
    RunTransport,
    SafetyEventKind,
    TaskStatus,
    TokenSource,
)
from app.db.models import DevWorkspace, Run, SafetyEvent, WorkspaceTask
from app.execution_verification import PostExecutionStatus, PostExecutionVerifier
from app.git_runtime import (
    RepositoryLayout,
    WorktreeOutcome,
    WorktreeVerdict,
    classify_task_worktree,
    create_worktree,
    inspect_task_worktree,
    repository_layout,
)
from app.git_runtime import post_execution as pe
from app.git_runtime import worktree as wt
from app.orchestrator import ConcurrentTaskUpdate, cancel
from app.orchestrator import execution_manager as em
from app.orchestrator.execution_contract import (
    AdmissionOutcome,
    ExecutionAdmission,
    component_invocation_id,
)
from app.orchestrator.execution_workspace import (
    ComponentBindingRefused,
    ExecutionWorkspacePorts,
    ExecutionWorkspaceService,
    GitOperations,
    PreparationRequest,
    PreparationResult,
    PreparedWorkspace,
)
from app.orchestrator.workspace_contract import (
    BindingState,
    PreparationCode,
    PreparationStage,
    PreparationStatus,
    error_summary,
)
from app.path_runtime import CheckedTreeWriter, bind_root, prepare_worktree_root
from app.process_runtime import ProcessResult, ProcessSpec, run_supervised
from app.safety.types import ObjectIdentity
from app.tool_executor.contracts import ExecutionWorkspaceRef, RunScope, WorkspaceKind
from app.tool_executor.workspace import (
    BindingRevoked,
    InMemoryWorkspaceResolver,
    RunWorkspaceBindings,
    WorkspaceUnavailable,
    bind_workspace,
)
from app.workspace import set_test_config
from app.workspace.policy import resolve_effective_policy
from tests import context_helpers
from tests.admission_support_e8_4_1 import RESOLVER, VALID_TEST_CONFIG, admit, approved_task
from tests.conftest import API_ROOT

_GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(_GIT is None, reason="git indisponível no PATH")

_SUMMARY_RE = re.compile(r"workspace_preparation:[a-z_]+:[a-z0-9_]+")
_RULE_PREFIX = "orchestrator.workspace_preparation."


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Git de **montagem de cenário** (verbos mutantes de propósito)."""
    assert _GIT is not None
    return subprocess.run(  # noqa: S603 — argv literal, sem shell
        [
            _GIT,
            "-c",
            "user.name=Teste",
            "-c",
            "user.email=teste@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-C",
            str(cwd),
            *args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def rev(cwd: Path, name: str = "HEAD") -> str:
    return git(cwd, "rev-parse", "--verify", name).stdout.strip()


# ------------------------------------------------------------------------------ harness


class Signal:
    """Sinal de cancelamento da tentativa (a porta neutra)."""

    def __init__(self) -> None:
        self.flag = False

    def __call__(self) -> bool:
        return self.flag


@dataclass
class Clock:
    """Relógio de parede controlado. `None` = relógio real."""

    fixed: datetime | None = None

    def __call__(self) -> datetime:
        return self.fixed if self.fixed is not None else datetime.now(UTC)


class SpyCapture:
    """A captura real da E8.3, com ganchos antes/depois e o registro de cada snapshot."""

    def __init__(
        self,
        inner: Any,
        *,
        before: Callable[[], None] | None = None,
        after: Callable[[], None] | None = None,
    ) -> None:
        self.inner = inner
        self.before = before
        self.after = after
        self.snapshots: list[Any] = []

    def capture_main_tree(self, local_path: str, *, is_cancelled: Callable[[], bool]) -> Any:
        if self.before is not None:
            self.before()
        snapshot = self.inner.capture_main_tree(local_path, is_cancelled=is_cancelled)
        self.snapshots.append(snapshot)
        if self.after is not None:
            self.after()
        return snapshot


class HookedBindings:
    """O adaptador concreto, com ganchos para provar a compensação."""

    def __init__(
        self,
        inner: RunWorkspaceBindings,
        *,
        after_bind: Callable[[], None] | None = None,
        fail_bind: bool = False,
    ) -> None:
        self.inner = inner
        self.after_bind = after_bind
        self.fail_bind = fail_bind
        self.unbinds = 0
        self.revokes = 0

    def bind_run(self, **kwargs: Any) -> object:
        if self.fail_bind:
            raise WorkspaceUnavailable("binding_rejected_by_test")
        token = self.inner.bind_run(**kwargs)
        if self.after_bind is not None:
            self.after_bind()
        return token

    def verify_run(self, **kwargs: Any) -> None:
        self.inner.verify_run(**kwargs)

    def unbind_run(self, token: object) -> bool:
        self.unbinds += 1
        return self.inner.unbind_run(token)

    def revoke_run(self, token: object) -> bool:
        self.revokes += 1
        return self.inner.revoke_run(token)


def _identity(path: str) -> ObjectIdentity:
    return bind_root(path).identity


@dataclass
class Harness:
    session: Session
    factory: sessionmaker[Session]
    workspace: DevWorkspace
    repo: Path
    artifacts: Path
    settings: AppSettings
    tmp: Path
    bindings: RunWorkspaceBindings = field(default_factory=RunWorkspaceBindings)
    clock: Clock = field(default_factory=Clock)

    def approve(self, **kwargs: Any) -> WorkspaceTask:
        return approved_task(self.session, self.workspace, self.artifacts, **kwargs)

    def admit(self, task_id: str, key: str = "exec-1") -> ExecutionAdmission:
        return admit(self.session, task_id, self.artifacts, key=key)

    def ports(
        self,
        *,
        git_ops: GitOperations | None = None,
        capture: Any = None,
        bindings: Any = None,
        identity: Any = None,
        tree_writer: Any = None,
        root_port: Any = None,
    ) -> ExecutionWorkspacePorts[Any]:
        return ExecutionWorkspacePorts(
            prepare_worktree_root=root_port if root_port is not None else prepare_worktree_root,
            directory_identity=identity if identity is not None else _identity,
            tree_writer=tree_writer if tree_writer is not None else CheckedTreeWriter,
            main_tree=capture if capture is not None else PostExecutionVerifier(),
            bindings=bindings if bindings is not None else self.bindings,
            git=git_ops if git_ops is not None else GitOperations(),
        )

    def service(self, **kwargs: Any) -> ExecutionWorkspaceService[Any]:
        return ExecutionWorkspaceService(
            session_factory=self.factory,
            settings=self.settings,
            ports=self.ports(**kwargs),
            developer_binding_resolver=RESOLVER,
            now=self.clock,
        )

    def prepare(
        self,
        admission: ExecutionAdmission,
        *,
        is_cancelled: Callable[[], bool] | None = None,
        service: ExecutionWorkspaceService[Any] | None = None,
        **kwargs: Any,
    ) -> PreparationResult[Any]:
        request = PreparationRequest.from_admission(admission)
        self.session.commit()
        chosen = service if service is not None else self.service(**kwargs)
        return chosen.prepare(request, is_cancelled=is_cancelled or Signal())

    # ------------------------------------------------------------------- leitura

    def task(self, task_id: str) -> WorkspaceTask:
        with self.factory() as fresh:
            row = fresh.get(WorkspaceTask, task_id)
            assert row is not None
            fresh.expunge(row)
            return row

    def run(self, run_id: str) -> Run:
        with self.factory() as fresh:
            row = fresh.get(Run, run_id)
            assert row is not None
            fresh.expunge(row)
            return row

    def events(self, task_id: str) -> list[SafetyEvent]:
        with self.factory() as fresh:
            rows = list(
                fresh.scalars(
                    select(SafetyEvent)
                    .where(
                        SafetyEvent.task_id == task_id,
                        SafetyEvent.rule_id.startswith(_RULE_PREFIX),
                    )
                    .order_by(SafetyEvent.created_at, SafetyEvent.id)
                )
            )
            fresh.expunge_all()
            return rows

    def target(self, task_id: str) -> Path:
        return self.settings.resolved_worktrees_dir.resolve() / f"ff-task-{task_id[:8]}"

    def next_attempt(self, task_id: str, previous_run_id: str, key: str) -> ExecutionAdmission:
        """Simula o fim de uma tentativa (o finalizador é da E8.4.5) e admite a seguinte."""
        with self.factory() as fresh:
            fresh.execute(
                update(Run)
                .where(Run.id == previous_run_id)
                .values(status=RunStatus.ERROR, finished_at=datetime.now(UTC), duration_ms=1)
            )
            fresh.execute(
                update(WorkspaceTask)
                .where(WorkspaceTask.id == task_id)
                .values(status=TaskStatus.APPROVED, phase=None, version=WorkspaceTask.version + 1)
            )
            fresh.commit()
        self.session.expire_all()
        admission = self.admit(task_id, key=key)
        assert admission.outcome is AdmissionOutcome.ADMITTED
        return admission


@pytest.fixture
def h(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    repo_path: Path,
    temp_settings: AppSettings,
    tmp_path: Path,
) -> Harness:
    temp_settings.ensure_worktrees_dir()
    return Harness(
        session=session,
        factory=session_factory,
        workspace=workspace,
        repo=repo_path,
        artifacts=tmp_path / "artifacts",
        settings=temp_settings,
        tmp=tmp_path,
    )


def _admitted(h: Harness, key: str = "exec-1") -> tuple[WorkspaceTask, ExecutionAdmission]:
    task = h.approve()
    admission = h.admit(task.id, key=key)
    assert admission.outcome is AdmissionOutcome.ADMITTED
    return task, admission


def _run_id(admission: ExecutionAdmission) -> str:
    assert admission.run is not None
    return admission.run.id


def _resolve(
    bindings: RunWorkspaceBindings,
    *,
    workspace_id: str,
    task_id: str,
    run_id: str,
    base: str,
    invocation_id: str = "x",
) -> Any:
    return bind_workspace(
        bindings.resolver,
        ExecutionWorkspaceRef(kind=WorkspaceKind.LOCAL_WORKTREE, id=workspace_id, base_commit=base),
        RunScope(task_id=task_id, run_id=run_id, invocation_id=invocation_id),
    )


def _assert_not_usable(h: Harness, result: PreparationResult[Any], admission: Any) -> None:
    """Nenhum contexto e nenhum binding utilizável para o Run de controle."""
    assert result.status is not PreparationStatus.PREPARED
    assert result.context is None
    assert result.binding is not BindingState.PUBLISHED
    task = h.task(admission.task.id)
    with pytest.raises(WorkspaceUnavailable):
        _resolve(
            h.bindings,
            workspace_id=h.workspace.id,
            task_id=task.id,
            run_id=_run_id(admission),
            base=task.planning_base_commit or "0" * 40,
        )


def _assert_admission_intact(h: Harness, admission: ExecutionAdmission) -> None:
    """Falha depois da admissão não desfaz nada: task `executing`, Run aberto, tentativa contada."""
    task = h.task(admission.task.id)
    run = h.run(_run_id(admission))
    assert task.status is TaskStatus.EXECUTING
    assert task.attempts == run.attempt_index + 1
    assert run.status is RunStatus.RUNNING
    assert run.files_changed is None and run.diff_added is None and run.diff_removed is None


# =================================================================== 1 · caminho feliz


def test_admissao_nova_prepara_worktree_na_base_congelada(h: Harness) -> None:
    task, admission = _admitted(h)
    base = task.planning_base_commit
    assert base is not None
    result = h.prepare(admission)

    assert result.status is PreparationStatus.PREPARED, result
    assert result.code is PreparationCode.PREPARED and result.stage is PreparationStage.DONE
    assert result.created is True and result.reused is False
    assert result.binding is BindingState.PUBLISHED
    assert not result.requires_recovery and not result.requires_finalization
    ctx = result.context
    assert ctx is not None
    target = h.target(task.id)
    assert Path(ctx.worktree_path) == target
    assert Path(ctx.workspace_path) == target  # workspace na raiz: prefixo vazio
    assert ctx.workspace_prefix == ""
    assert ctx.base_commit == base and ctx.control_run_id == _run_id(admission)
    assert rev(target) == base
    assert git(target, "symbolic-ref", "HEAD").stdout.strip() == f"refs/heads/ff/task-{task.id[:8]}"
    assert (target / "src" / "util.py").read_text(encoding="utf-8") == "def util():\n    return 1\n"

    persisted_task, run = h.task(task.id), h.run(_run_id(admission))
    assert persisted_task.worktree_path == ctx.worktree_path == run.worktree_path
    assert persisted_task.version == ctx.task_version
    assert run.error_summary is None
    _assert_admission_intact(h, admission)
    # 20 · nenhuma métrica final afirmada no Run aberto.
    assert run.files_changed is None and run.diff_added is None and run.diff_removed is None
    assert h.events(task.id) == []

    bound = _resolve(
        h.bindings,
        workspace_id=h.workspace.id,
        task_id=task.id,
        run_id=ctx.control_run_id,
        base=base,
        invocation_id=ctx.control_invocation_id,
    )
    assert bound.resolved.run_id == ctx.control_run_id
    assert bound.resolved.workspace_path == ctx.workspace_path
    assert bound.root.identity == ctx.workspace_root_identity


# =========================================================== 2 · replay e slot ocupado


def test_replay_e_slot_busy_nao_tem_efeito_de_preparacao(h: Harness) -> None:
    task, admission = _admitted(h)
    replay = h.admit(task.id, key="exec-1")
    assert replay.outcome is AdmissionOutcome.REPLAYED

    other = h.approve(title="outra")
    busy = h.admit(other.id, key="exec-other")
    assert busy.outcome is AdmissionOutcome.SLOT_BUSY

    capture = SpyCapture(PostExecutionVerifier())
    for not_new in (replay, busy):
        result = h.prepare(not_new, capture=capture)
        assert result.status is PreparationStatus.REFUSED
        assert result.code is PreparationCode.NOT_ADMITTED
        assert result.stage is PreparationStage.ADMISSION
        assert not result.requires_finalization
    assert capture.snapshots == []
    assert list(h.settings.resolved_worktrees_dir.iterdir()) == []
    assert h.task(task.id).attempts == 1 and h.task(other.id).attempts == 0
    assert h.run(_run_id(admission)).worktree_path is None
    assert h.bindings.resolver._bindings == {}


# ======================================================== 3 · principal suja + 11 snapshot


def test_principal_suja_fica_intacta_e_nao_contamina_a_worktree(h: Harness) -> None:
    task, admission = _admitted(h)
    (h.repo / "src" / "util.py").write_text("SUJO\n", encoding="utf-8")
    (h.repo / "nao-rastreado.txt").write_text("x\n", encoding="utf-8")
    (h.repo / "README.md").write_text("staged\n", encoding="utf-8")
    git(h.repo, "add", "README.md")

    capture = SpyCapture(PostExecutionVerifier())
    result = h.prepare(admission, capture=capture)
    assert result.status is PreparationStatus.PREPARED, result
    ctx = result.context
    assert ctx is not None
    target = Path(ctx.worktree_path)
    assert (target / "src" / "util.py").read_text(encoding="utf-8") == "def util():\n    return 1\n"
    assert (target / "README.md").read_text(encoding="utf-8") == "# projeto\n"
    assert not (target / "nao-rastreado.txt").exists()
    assert (h.repo / "src" / "util.py").read_text(encoding="utf-8") == "SUJO\n"

    # 11 · o snapshot do contexto é o original da captura, usado uma única vez pela verificação.
    assert len(capture.snapshots) == 1 and ctx.main_snapshot is capture.snapshots[0]
    verifier = PostExecutionVerifier()
    outcome = verifier.verify(
        before=ctx.main_snapshot,
        workspace_path=ctx.workspace_path,
        workspace_prefix=ctx.workspace_prefix,
        root_identity=ctx.workspace_root_identity,
        base_commit=ctx.base_commit,
        policy=resolve_effective_policy(h.workspace),
        is_cancelled=lambda: False,
    )
    assert outcome.status is PostExecutionStatus.VERIFIED, outcome
    with pytest.raises(ValueError, match="já foi usado"):
        verifier.verify(
            before=ctx.main_snapshot,
            workspace_path=ctx.workspace_path,
            workspace_prefix=ctx.workspace_prefix,
            root_identity=ctx.workspace_root_identity,
            base_commit=ctx.base_commit,
            policy=resolve_effective_policy(h.workspace),
            is_cancelled=lambda: False,
        )


# ================================================================== 4 · monorepo


def _mono_harness(
    session: Session,
    session_factory: sessionmaker[Session],
    temp_settings: AppSettings,
    tmp_path: Path,
) -> Harness:
    """Um monorepo cujo workspace é `pkg/ws` (prefixo `pkg/ws/`)."""
    mono = tmp_path / "mono"
    context_helpers.init_repo(mono)
    context_helpers.write(mono, "raiz.txt", "raiz\n")
    context_helpers.write(mono, "pkg/ws/README.md", "# ws\n")
    context_helpers.write(mono, "pkg/ws/src/app.py", "print('ok')\n")
    context_helpers.commit_all(mono, "mono")
    workspace = context_helpers.make_workspace(session, mono / "pkg" / "ws", name="mono-ws")
    temp_settings.ensure_worktrees_dir()
    return Harness(
        session=session,
        factory=session_factory,
        workspace=workspace,
        repo=mono,
        artifacts=tmp_path / "artifacts",
        settings=temp_settings,
        tmp=tmp_path,
    )


def test_workspace_em_subdiretorio_usa_prefixo_e_snapshot_cobre_o_repo_inteiro(
    session: Session,
    session_factory: sessionmaker[Session],
    temp_settings: AppSettings,
    tmp_path: Path,
) -> None:
    h = _mono_harness(session, session_factory, temp_settings, tmp_path)
    mono, workspace = h.repo, h.workspace
    task, admission = _admitted(h)
    result = h.prepare(admission)
    assert result.status is PreparationStatus.PREPARED, result
    ctx = result.context
    assert ctx is not None
    assert ctx.workspace_prefix == "pkg/ws/"
    assert Path(ctx.workspace_path) == Path(ctx.worktree_path) / "pkg" / "ws"
    assert h.task(task.id).worktree_path == ctx.worktree_path  # a raiz completa, não o interno
    assert (Path(ctx.worktree_path) / "raiz.txt").is_file()

    # O snapshot cobre a principal inteira: uma escrita fora do workspace também é vista.
    (mono / "raiz.txt").write_text("mudou fora do workspace\n", encoding="utf-8")
    outcome = PostExecutionVerifier().verify(
        before=ctx.main_snapshot,
        workspace_path=ctx.workspace_path,
        workspace_prefix=ctx.workspace_prefix,
        root_identity=ctx.workspace_root_identity,
        base_commit=ctx.base_commit,
        policy=resolve_effective_policy(workspace),
        is_cancelled=lambda: False,
    )
    assert outcome.status is PostExecutionStatus.OUT_OF_WORKTREE_WRITE


def _swap_external_root(target: Path, parked: Path) -> None:
    """Troca a raiz **externa** da worktree por um diretório novo, no mesmo caminho, com o
    conteúdo movido por `rename` (inclusive `.git` e `pkg/`): o workspace interno mantém a
    identidade, e o Git continua vendo a mesma árvore limpa."""
    os.rename(target, parked)
    target.mkdir()
    for child in parked.iterdir():
        os.rename(child, target / child.name)


def test_raiz_externa_da_worktree_trocada_durante_o_binding_e_recusada(
    session: Session,
    session_factory: sessionmaker[Session],
    temp_settings: AppSettings,
    tmp_path: Path,
) -> None:
    """E842-AUD-003 · monorepo: só a identidade do `workspace_path` interno não basta em L."""
    h = _mono_harness(session, session_factory, temp_settings, tmp_path)
    task, admission = _admitted(h)
    target = h.target(task.id)
    workspace_dir = target / "pkg" / "ws"
    inner_before: list[ObjectIdentity] = []
    outer_before: list[ObjectIdentity] = []

    def swap() -> None:
        outer_before.append(_identity(str(target)))
        inner_before.append(_identity(str(workspace_dir)))
        _swap_external_root(target, h.tmp / "raiz-estacionada")

    bindings = HookedBindings(h.bindings, after_bind=swap)
    result = h.prepare(admission, bindings=bindings)

    assert result.status is PreparationStatus.REFUSED, result
    assert result.code is PreparationCode.IDENTITY_CHANGED
    assert result.stage is PreparationStage.FINAL
    assert result.binding is BindingState.REMOVED and bindings.revokes == 1
    assert h.bindings.resolver._bindings == {}
    # O cenário é o do finding: a identidade externa mudou, a interna não, e o Git não vê nada.
    assert _identity(str(target)) != outer_before[0]
    assert _identity(str(workspace_dir)) == inner_before[0]
    base = h.task(task.id).planning_base_commit
    assert base is not None
    local_path = h.workspace.local_path  # o workspace da **principal**, como em produção
    layout = repository_layout(local_path)
    assert layout is not None
    root = prepare_worktree_root(
        h.settings.resolved_worktrees_dir,
        sync_roots=(),
        repository_toplevel=layout.toplevel,
        git_common_dir=layout.git_common_dir,
    )
    facts = inspect_task_worktree(local_path, base_commit=base, task_id=task.id, root=root)
    assert classify_task_worktree(facts) is WorktreeVerdict.REUSABLE
    _assert_not_usable(h, result, admission)
    _assert_admission_intact(h, admission)
    [event] = h.events(task.id)
    assert event.kind is SafetyEventKind.TOCTOU_RECHECK_FAILED


# ================================================================ 5 · HEAD principal


def _advance_head(repo: Path) -> None:
    (repo / "avanco.txt").write_text("novo\n", encoding="utf-8")
    git(repo, "add", "avanco.txt")
    git(repo, "commit", "-q", "-m", "avanço")


def test_head_avancado_depois_da_admissao_bloqueia_antes_de_qualquer_efeito(
    h: Harness,
) -> None:
    task, admission = _admitted(h)
    _advance_head(h.repo)
    capture = SpyCapture(PostExecutionVerifier())
    result = h.prepare(admission, capture=capture)
    assert result.status is PreparationStatus.REFUSED
    assert result.code is PreparationCode.MAIN_HEAD_DIVERGED
    assert result.stage is PreparationStage.LAYOUT
    assert result.requires_replan and result.requires_finalization
    assert not result.residue_possible
    assert capture.snapshots == []
    assert not h.target(task.id).exists()
    _assert_not_usable(h, result, admission)
    _assert_admission_intact(h, admission)
    run = h.run(_run_id(admission))
    assert run.error_summary == error_summary(
        PreparationStatus.REFUSED, PreparationCode.MAIN_HEAD_DIVERGED
    )
    assert run.worktree_path is None
    [event] = h.events(task.id)
    assert event.kind is SafetyEventKind.TOCTOU_RECHECK_FAILED
    assert event.run_id == _run_id(admission)
    # A task não volta: nada de `awaiting_approval` a partir de `executing`.
    assert h.task(task.id).status is TaskStatus.EXECUTING


@pytest.mark.parametrize("fronteira", ["captura", "criacao", "binding"])
def test_head_alterado_em_cada_fronteira_impede_prepared(h: Harness, fronteira: str) -> None:
    task, admission = _admitted(h)

    def advance() -> None:
        _advance_head(h.repo)

    kwargs: dict[str, Any] = {}
    if fronteira == "captura":
        kwargs["capture"] = SpyCapture(PostExecutionVerifier(), after=advance)
        stage, residue = PreparationStage.PRE_CREATE, False
    elif fronteira == "criacao":

        def create_then_advance(*args: Any, **kw: Any) -> WorktreeOutcome:
            outcome = create_worktree(*args, **kw)
            advance()
            return outcome

        kwargs["git_ops"] = GitOperations(create=create_then_advance)
        stage, residue = PreparationStage.POST_CREATE, True
    else:
        kwargs["bindings"] = HookedBindings(h.bindings, after_bind=advance)
        stage, residue = PreparationStage.FINAL, True

    result = h.prepare(admission, **kwargs)
    assert result.status is PreparationStatus.REFUSED, result
    assert result.code is PreparationCode.MAIN_HEAD_DIVERGED
    assert result.stage is stage
    assert result.residue_possible is residue
    if fronteira == "binding":
        assert result.binding is BindingState.REMOVED
    _assert_not_usable(h, result, admission)
    _assert_admission_intact(h, admission)
    if residue:  # o resíduo fica, registrado honestamente
        assert h.target(task.id).is_dir()
        assert Path(h.run(_run_id(admission)).worktree_path or "") == h.target(task.id)


# ============================================================ 6 · layout e identidades


@pytest.mark.parametrize("campo", ["toplevel", "git_common_dir", "workspace_prefix"])
def test_layout_trocado_entre_observacoes_bloqueia(h: Harness, campo: str) -> None:
    task, admission = _admitted(h)
    calls = {"n": 0}

    def layout(local: str) -> RepositoryLayout | None:
        real = repository_layout(local)
        calls["n"] += 1
        if real is None or calls["n"] < 2:
            return real
        value = "sub/" if campo == "workspace_prefix" else str(h.tmp / "outro-repo")
        return replace(real, **{campo: value})

    result = h.prepare(admission, git_ops=GitOperations(repository_layout=layout))
    assert result.status is PreparationStatus.REFUSED
    assert result.code is PreparationCode.LAYOUT_CHANGED
    assert result.stage is PreparationStage.PRE_CREATE
    assert not h.target(task.id).exists()
    _assert_not_usable(h, result, admission)


@pytest.mark.parametrize("qual", ["raiz", "toplevel", "worktree"])
def test_identidade_trocada_entre_observacoes_bloqueia(h: Harness, qual: str) -> None:
    task, admission = _admitted(h)
    seen: dict[str, int] = {}

    def identity(path: str) -> ObjectIdentity:
        real = _identity(path)
        key = os.path.normcase(os.path.normpath(path))
        seen[key] = seen.get(key, 0) + 1
        watched = {
            "raiz": h.settings.resolved_worktrees_dir.resolve(),
            "toplevel": h.repo.resolve(),
            "worktree": h.target(task.id),
        }[qual]
        if key == os.path.normcase(str(watched)) and seen[key] >= 2:
            return ObjectIdentity(real.volume_id, real.file_id + 1)
        return real

    result = h.prepare(admission, identity=identity)
    assert result.status is PreparationStatus.REFUSED, result
    assert result.code is PreparationCode.IDENTITY_CHANGED
    _assert_not_usable(h, result, admission)
    [event] = h.events(task.id)
    assert event.kind is SafetyEventKind.TOCTOU_RECHECK_FAILED


def test_identidade_nao_verificavel_bloqueia(h: Harness) -> None:
    task, admission = _admitted(h)
    result = h.prepare(admission, identity=lambda path: ObjectIdentity(1, 0))
    assert result.status is PreparationStatus.UNVERIFIABLE
    assert result.code is PreparationCode.IDENTITY_UNVERIFIABLE
    assert not h.target(task.id).exists()
    _assert_not_usable(h, result, admission)


def test_raiz_negada_pela_politica_bloqueia(h: Harness) -> None:
    task, admission = _admitted(h)

    def root_inside_sync(requested: str, **kwargs: Any) -> Any:
        return prepare_worktree_root(
            requested, **{**kwargs, "sync_roots": (*kwargs["sync_roots"], requested)}
        )

    result = h.prepare(admission, root_port=root_inside_sync)
    assert result.status is PreparationStatus.REFUSED
    assert result.code is PreparationCode.WORKTREE_ROOT_DENIED
    _assert_not_usable(h, result, admission)


# =============================================================== 7/19 · ownership


def _task_with_id(session: Session, workspace: DevWorkspace, task_id: str) -> WorkspaceTask:
    task = context_helpers.make_task(session, workspace, title="homônima")
    session.flush()
    session.execute(update(WorkspaceTask).where(WorkspaceTask.id == task.id).values(id=task_id))
    session.commit()
    return task


def test_colisao_de_id8_com_outra_task_impede_criacao(h: Harness) -> None:
    task, admission = _admitted(h)
    (h.tmp / "outro").mkdir(exist_ok=True)
    other_ws = context_helpers.make_workspace(h.session, h.tmp / "outro", name="outro")
    twin = task.id[:8] + "-0000-4000-8000-000000000000"
    assert twin != task.id
    _task_with_id(h.session, other_ws, twin)

    result = h.prepare(admission)
    assert result.status is PreparationStatus.OWNERSHIP_CONFLICT
    assert result.code is PreparationCode.ID8_COLLISION
    assert result.stage is PreparationStage.PRECONDITIONS
    assert not h.target(task.id).exists()
    _assert_not_usable(h, result, admission)
    [event] = h.events(task.id)
    assert event.kind is SafetyEventKind.PATH_DENIED
    assert event.rule_id == f"{_RULE_PREFIX}id8_collision"


@pytest.mark.parametrize("reivindicacao", ["id8", "caminho"])
def test_reivindicacao_que_surge_durante_a_captura_bloqueia_antes_de_criar(
    h: Harness, reivindicacao: str
) -> None:
    task, admission = _admitted(h)

    def claim() -> None:
        with h.factory() as fresh:
            if reivindicacao == "id8":
                (h.tmp / "outro").mkdir(exist_ok=True)
                other_ws = context_helpers.make_workspace(fresh, h.tmp / "outro", name="outro")
                _task_with_id(fresh, other_ws, task.id[:8] + "-0000-4000-8000-000000000000")
            else:
                other = context_helpers.make_task(fresh, h.workspace, title="outra")
                other.worktree_path = str(h.target(task.id))
                fresh.commit()

    result = h.prepare(admission, capture=SpyCapture(PostExecutionVerifier(), after=claim))
    assert result.status is PreparationStatus.OWNERSHIP_CONFLICT
    assert result.stage is PreparationStage.PRE_CREATE
    assert result.code is (
        PreparationCode.ID8_COLLISION if reivindicacao == "id8" else PreparationCode.PATH_CLAIMED
    )
    assert not h.target(task.id).exists()
    _assert_not_usable(h, result, admission)


def test_caminho_reivindicado_por_outra_task_impede_criacao(h: Harness) -> None:
    task, admission = _admitted(h)
    other = context_helpers.make_task(h.session, h.workspace, title="outra")
    h.session.execute(
        update(WorkspaceTask)
        .where(WorkspaceTask.id == other.id)
        .values(worktree_path=str(h.target(task.id)))
    )
    h.session.commit()
    result = h.prepare(admission)
    assert result.status is PreparationStatus.OWNERSHIP_CONFLICT
    assert result.code is PreparationCode.PATH_CLAIMED
    assert not h.target(task.id).exists()


def test_residuo_sem_proveniencia_nunca_e_adotado(h: Harness) -> None:
    """19 · crash depois da criação e antes da persistência: a worktree estruturalmente
    reusável, sem registro desta task, é recusada — nunca adotada pelo nome."""
    task, admission = _admitted(h)
    layout = repository_layout(str(h.repo))
    assert layout is not None and task.planning_base_commit is not None
    root = prepare_worktree_root(
        h.settings.resolved_worktrees_dir,
        sync_roots=(),
        repository_toplevel=layout.toplevel,
        git_common_dir=layout.git_common_dir,
    )
    residue = create_worktree(
        str(h.repo),
        base_commit=task.planning_base_commit,
        task_id=task.id,
        root=root,
        tree_writer=CheckedTreeWriter,
    )
    assert residue.created and residue.verdict is WorktreeVerdict.REUSABLE

    capture = SpyCapture(PostExecutionVerifier())
    result = h.prepare(admission, capture=capture)
    assert result.status is PreparationStatus.OWNERSHIP_CONFLICT
    assert result.code is PreparationCode.UNPROVEN_REUSE
    assert result.stage is PreparationStage.WORKTREE_ROOT
    assert not result.residue_possible  # o resíduo não é desta preparação
    assert capture.snapshots == []
    assert h.run(_run_id(admission)).worktree_path is None  # nada é atribuído a esta task
    assert h.target(task.id).is_dir()  # intocado
    _assert_not_usable(h, result, admission)


# ============================================================ 8/9 · reuso da mesma task


def _prepared(h: Harness) -> tuple[WorkspaceTask, ExecutionAdmission, PreparedWorkspace[Any]]:
    task, admission = _admitted(h)
    result = h.prepare(admission)
    assert result.status is PreparationStatus.PREPARED, result
    assert result.context is not None
    return task, admission, result.context


def test_reuso_legitimo_tem_proveniencia_e_binding_e_snapshot_novos(h: Harness) -> None:
    task, first, ctx1 = _prepared(h)
    second = h.next_attempt(task.id, ctx1.control_run_id, key="exec-2")
    capture = SpyCapture(PostExecutionVerifier())
    result = h.prepare(second, capture=capture)

    assert result.status is PreparationStatus.PREPARED, result
    assert result.reused is True and result.created is False
    ctx2 = result.context
    assert ctx2 is not None
    assert ctx2.control_run_id == _run_id(second) != ctx1.control_run_id
    assert ctx2.worktree_path == ctx1.worktree_path
    assert ctx2.main_snapshot is capture.snapshots[0]
    assert ctx2.main_snapshot is not ctx1.main_snapshot
    assert ctx2.binding_token is not ctx1.binding_token
    assert ctx2.attempt_index == ctx1.attempt_index + 1
    for ctx in (ctx1, ctx2):
        bound = _resolve(
            h.bindings,
            workspace_id=h.workspace.id,
            task_id=task.id,
            run_id=ctx.control_run_id,
            base=ctx.base_commit,
        )
        assert bound.resolved.run_id == ctx.control_run_id


def _dirty_untracked(h: Harness, target: Path) -> None:
    (target / "extra.txt").write_text("x\n", encoding="utf-8")


def _locked(h: Harness, target: Path) -> None:
    git(h.repo, "worktree", "lock", str(target))


def _diverged(h: Harness, target: Path) -> None:
    (target / "README.md").write_text("humano\n", encoding="utf-8")
    git(target, "commit", "-q", "-am", "commit humano")


def _broken_dotgit(h: Harness, target: Path) -> None:
    dotgit = target / ".git"
    dotgit.unlink()  # no Windows o `.git` nasce oculto e não aceita `open("w")`
    dotgit.write_text("gitdir: /lugar/nenhum\n", encoding="utf-8")


def _stale(h: Harness, target: Path) -> None:
    shutil.rmtree(target)


def _orphan_branch(h: Harness, target: Path) -> None:
    shutil.rmtree(target)
    git(h.repo, "worktree", "prune")


def _linked(h: Harness, target: Path) -> None:
    moved = h.tmp / "worktree-movida"
    target.rename(moved)
    if sys.platform == "win32":  # literal: o mypy estreita a plataforma só assim
        import _winapi

        _winapi.CreateJunction(str(moved), str(target))
    else:
        os.symlink(moved, target, target_is_directory=True)


@pytest.mark.parametrize(
    ("estrago", "veredito"),
    [
        (_dirty_untracked, "dirty"),
        (_locked, "locked"),
        (_diverged, "branch_diverged"),
        (_broken_dotgit, "broken_link"),
        (_stale, "stale_metadata"),
        (_orphan_branch, "branch_orphaned"),
        (_linked, "foreign_path"),
    ],
)
def test_worktree_inutilizavel_da_mesma_task_bloqueia_reuso(
    h: Harness, estrago: Callable[[Harness, Path], None], veredito: str
) -> None:
    task, _first, ctx1 = _prepared(h)
    second = h.next_attempt(task.id, ctx1.control_run_id, key="exec-2")
    estrago(h, Path(ctx1.worktree_path))
    capture = SpyCapture(PostExecutionVerifier())
    result = h.prepare(second, capture=capture)
    assert result.status is PreparationStatus.REFUSED, result
    assert result.code is PreparationCode.WORKTREE_REFUSED
    assert result.cause == veredito
    assert capture.snapshots == []
    assert h.run(_run_id(second)).worktree_path is None
    _assert_not_usable(h, result, second)
    [event] = h.events(task.id)
    assert event.kind is SafetyEventKind.PATH_DENIED


# ================================================================== 10 · snapshot


def test_snapshot_com_orcamento_esgotado_bloqueia_antes_da_criacao(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    task, admission = _admitted(h)
    monkeypatch.setattr(pe, "MAX_ENTRIES", 1)  # orçamento real da E8.3, encolhido só no teste
    result = h.prepare(admission)
    assert result.status is PreparationStatus.UNVERIFIABLE
    assert result.code is PreparationCode.MAIN_SNAPSHOT_UNVERIFIABLE
    assert result.cause == "limit_exceeded"
    assert result.stage is PreparationStage.MAIN_SNAPSHOT
    assert not h.target(task.id).exists()
    _assert_not_usable(h, result, admission)


def test_snapshot_com_prazo_proprio_esgotado_bloqueia(h: Harness) -> None:
    task, admission = _admitted(h)
    ticks = iter(range(0, 10_000, 30))  # cada leitura do relógio monotônico avança 30 s
    result = h.prepare(admission, capture=PostExecutionVerifier(clock=lambda: float(next(ticks))))
    assert result.status is PreparationStatus.UNVERIFIABLE
    assert result.cause == "deadline_exceeded"
    assert not h.target(task.id).exists()


class _UnreadableSnapshot:
    verifiable = False
    failure = None


class _UnreadableCapture:
    def capture_main_tree(self, local_path: str, *, is_cancelled: Callable[[], bool]) -> Any:
        return _UnreadableSnapshot()


def test_snapshot_ilegivel_bloqueia(h: Harness) -> None:
    task, admission = _admitted(h)
    result = h.prepare(admission, capture=_UnreadableCapture())
    assert result.status is PreparationStatus.UNVERIFIABLE
    assert result.code is PreparationCode.MAIN_SNAPSHOT_UNVERIFIABLE
    assert result.cause is None
    assert not h.target(task.id).exists()


def test_cancelamento_durante_a_captura_bloqueia_antes_da_criacao(h: Harness) -> None:
    task, admission = _admitted(h)
    signal = Signal()

    def cancel_now() -> None:
        signal.flag = True

    result = h.prepare(
        admission,
        is_cancelled=signal,
        capture=SpyCapture(PostExecutionVerifier(), before=cancel_now),
    )
    assert result.status is PreparationStatus.CANCELLED
    assert result.stage is PreparationStage.MAIN_SNAPSHOT
    assert not h.target(task.id).exists()


# ========================================================== 12/13 · dimensões do binding


def test_binding_so_resolve_o_proprio_run_task_workspace_e_base(h: Harness) -> None:
    task, _admission, ctx = _prepared(h)
    common = {
        "workspace_id": h.workspace.id,
        "task_id": task.id,
        "run_id": ctx.control_run_id,
        "base": ctx.base_commit,
    }
    _resolve(h.bindings, **common)
    for key, value, code in (
        ("run_id", "11111111-1111-4111-8111-111111111111", "workspace_unbound"),
        ("task_id", "22222222-2222-4222-8222-222222222222", "workspace_unbound"),
        ("workspace_id", "outro-workspace", "workspace_unbound"),
        ("base", "f" * 40, "base_commit_mismatch"),
    ):
        with pytest.raises(WorkspaceUnavailable) as caught:
            _resolve(h.bindings, **{**common, key: value})
        assert caught.value.code == code


def test_resolver_defeituoso_e_recusado_pela_regra_canonica(h: Harness) -> None:
    task, admission = _admitted(h)

    class _DefectiveResolver(InMemoryWorkspaceResolver):
        """Guarda certo, mas resolve devolvendo o binding de **outro** Run."""

        def resolve(self, workspace_ref: Any, run_scope: Any) -> Any:
            found = super().resolve(workspace_ref, run_scope)
            if found is None:
                return None
            return replace(found, run_id="33333333-3333-4333-8333-333333333333")

    defective = RunWorkspaceBindings(_DefectiveResolver())
    result = h.prepare(admission, bindings=defective)
    assert result.status is PreparationStatus.UNVERIFIABLE
    assert result.code is PreparationCode.BINDING_FAILED
    assert result.cause == "binding_mismatch"
    assert result.binding is BindingState.REJECTED
    assert defective.resolver._bindings == {}  # o adaptador desfez o próprio registro
    # Registro que não passou na conferência canônica foi **revogado**, não só apagado: o mesmo
    # Run não volta a ser registrado nesta instância.
    with pytest.raises(WorkspaceUnavailable) as again:
        defective.bind_run(
            workspace_id=h.workspace.id,
            task_id=task.id,
            run_id=_run_id(admission),
            invocation_id="x",
            base_commit=h.task(task.id).planning_base_commit or "0" * 40,
            workspace_path=str(h.target(task.id)),
            workspace_prefix="",
            root_identity=_identity(str(h.target(task.id))),
        )
    assert again.value.code == "binding_revoked"
    assert h.target(task.id).is_dir()  # resíduo preservado, sem binding utilizável


def test_raiz_trocada_depois_do_binding_e_recusada_pela_conferencia_existente(
    h: Harness,
) -> None:
    task, _admission, ctx = _prepared(h)
    moved = h.tmp / "trocada"
    Path(ctx.workspace_path).rename(moved)
    Path(ctx.workspace_path).mkdir()
    with pytest.raises(WorkspaceUnavailable) as caught:
        _resolve(
            h.bindings,
            workspace_id=h.workspace.id,
            task_id=task.id,
            run_id=ctx.control_run_id,
            base=ctx.base_commit,
        )
    assert caught.value.code == "root_unavailable"


# ================================================================ 14 · cancelamento


@pytest.mark.parametrize("fronteira", ["admissao", "pre_criacao", "materializacao", "binding"])
def test_cancelamento_em_cada_fronteira_nao_deixa_contexto(h: Harness, fronteira: str) -> None:
    task, admission = _admitted(h)
    signal = Signal()

    def cancel_now() -> None:
        signal.flag = True

    kwargs: dict[str, Any] = {}
    if fronteira == "admissao":
        signal.flag = True
        stage, residue = PreparationStage.PRECONDITIONS, False
    elif fronteira == "pre_criacao":
        kwargs["capture"] = SpyCapture(PostExecutionVerifier(), after=cancel_now)
        stage, residue = PreparationStage.MAIN_SNAPSHOT, False
    elif fronteira == "materializacao":
        written: list[str] = []

        class _CancellingWriter:
            def __init__(self, root: str, identity: ObjectIdentity) -> None:
                self.inner = CheckedTreeWriter(root, identity)

            def write_file(self, relative: str, content: bytes, *, executable: bool) -> None:
                self.inner.write_file(relative, content, executable=executable)
                written.append(relative)
                cancel_now()

        kwargs["tree_writer"] = _CancellingWriter
        stage, residue = PreparationStage.CREATE, True
    else:
        kwargs["bindings"] = HookedBindings(h.bindings, after_bind=cancel_now)
        stage, residue = PreparationStage.FINAL, True

    result = h.prepare(admission, is_cancelled=signal, **kwargs)
    assert result.status is PreparationStatus.CANCELLED, result
    assert result.code is PreparationCode.CANCEL_REQUESTED
    assert result.stage is stage
    assert result.residue_possible is residue
    if fronteira == "materializacao":
        assert written and result.worktree_verdict is WorktreeVerdict.CREATED_INVALID
        assert result.created is False
    if fronteira == "binding":
        assert result.binding is BindingState.REMOVED
    if residue:
        assert h.target(task.id).is_dir()
        assert result.requires_recovery
    _assert_not_usable(h, result, admission)
    _assert_admission_intact(h, admission)


def test_cancel_humano_depois_da_publicacao_vence_e_nao_e_sobrescrito(h: Harness) -> None:
    task, admission = _admitted(h)

    def human_cancel() -> None:
        with h.factory() as fresh:
            cancel(fresh, task.id, note="desisti")
            fresh.commit()

    bindings = HookedBindings(h.bindings, after_bind=human_cancel)
    result = h.prepare(admission, bindings=bindings)
    assert result.status is PreparationStatus.SUPERSEDED
    assert result.code is PreparationCode.TASK_NOT_EXECUTING
    assert result.stage is PreparationStage.FINAL
    assert result.binding is BindingState.REMOVED
    persisted = h.task(task.id)
    assert persisted.status is TaskStatus.CANCELLED and persisted.cancel_requested
    run = h.run(_run_id(admission))
    assert run.status is RunStatus.RUNNING  # o finalizador é da E8.4.5
    assert run.error_summary == error_summary(
        PreparationStatus.SUPERSEDED, PreparationCode.TASK_NOT_EXECUTING
    )
    _assert_not_usable(h, result, admission)


def test_sinal_de_cancelamento_vence_o_cancel_persistido_na_checagem_final(h: Harness) -> None:
    """Os dois cancelamentos chegam durante o binding: a primeira checagem de L enxerga o sinal
    injetado (`CANCELLED`) **antes** de reler o banco, onde a task já é `cancelled`
    (`SUPERSEDED`). Remover essa checagem trocaria o desfecho."""
    task, admission = _admitted(h)
    signal = Signal()

    def both() -> None:
        signal.flag = True
        with h.factory() as fresh:
            cancel(fresh, task.id)
            fresh.commit()

    result = h.prepare(
        admission, is_cancelled=signal, bindings=HookedBindings(h.bindings, after_bind=both)
    )
    assert result.status is PreparationStatus.CANCELLED, result
    assert result.code is PreparationCode.CANCEL_REQUESTED
    assert result.stage is PreparationStage.FINAL
    assert result.binding is BindingState.REMOVED
    _assert_not_usable(h, result, admission)


# ======================================================================= 15 · prazo


def _started(h: Harness, admission: ExecutionAdmission) -> datetime:
    return h.run(_run_id(admission)).started_at


def test_prazo_esgotado_impede_qualquer_passo(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    task, admission = _admitted(h)
    spawned: list[ProcessSpec] = []

    def spy(spec: ProcessSpec, is_cancelled: Callable[[], bool]) -> ProcessResult:
        spawned.append(spec)
        return run_supervised(spec, is_cancelled)

    monkeypatch.setattr(wt, "run_supervised", spy)
    h.clock.fixed = _started(h, admission) + timedelta(seconds=1800)
    capture = SpyCapture(PostExecutionVerifier())
    result = h.prepare(admission, capture=capture)
    assert result.status is PreparationStatus.TIMEOUT
    assert result.code is PreparationCode.DEADLINE_EXCEEDED
    assert result.stage is PreparationStage.PRECONDITIONS
    assert spawned == [] and capture.snapshots == []
    assert not h.target(task.id).exists()


def test_supervisor_recebe_o_prazo_restante_da_tentativa(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    task, admission = _admitted(h)
    timeouts: list[float] = []

    def spy(spec: ProcessSpec, is_cancelled: Callable[[], bool]) -> ProcessResult:
        timeouts.append(spec.timeout_s)
        return run_supervised(spec, is_cancelled)

    monkeypatch.setattr(wt, "run_supervised", spy)
    h.clock.fixed = _started(h, admission) + timedelta(seconds=1800 - 30)
    result = h.prepare(admission)
    assert result.status is PreparationStatus.PREPARED, result
    assert len(timeouts) == 2  # `worktree add` e `read-tree`
    assert all(timeout == pytest.approx(30.0) for timeout in timeouts)


def test_timeout_no_ultimo_passo_nao_vira_sucesso(h: Harness) -> None:
    task, admission = _admitted(h)
    started = _started(h, admission)

    def create_then_expire(*args: Any, **kwargs: Any) -> WorktreeOutcome:
        outcome = create_worktree(*args, **kwargs)
        h.clock.fixed = started + timedelta(seconds=1801)
        return outcome

    result = h.prepare(admission, git_ops=GitOperations(create=create_then_expire))
    assert result.status is PreparationStatus.TIMEOUT
    assert result.stage is PreparationStage.CREATE
    assert result.created is True and result.residue_possible and result.requires_recovery
    run = h.run(_run_id(admission))
    assert Path(run.worktree_path or "") == h.target(task.id)  # resíduo registrado
    assert h.task(task.id).worktree_path is None  # nada publicado
    _assert_not_usable(h, result, admission)


def test_prazo_esgotado_na_checagem_final_remove_o_binding(h: Harness) -> None:
    task, admission = _admitted(h)
    started = _started(h, admission)

    def expire() -> None:
        h.clock.fixed = started + timedelta(seconds=1801)

    result = h.prepare(admission, bindings=HookedBindings(h.bindings, after_bind=expire))
    assert result.status is PreparationStatus.TIMEOUT
    assert result.stage is PreparationStage.FINAL
    assert result.binding is BindingState.REMOVED
    _assert_not_usable(h, result, admission)


def test_create_worktree_sem_prazo_restante_nao_inicia_passo(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = h.approve()
    assert task.planning_base_commit is not None
    layout = repository_layout(str(h.repo))
    assert layout is not None
    root = prepare_worktree_root(
        h.settings.resolved_worktrees_dir,
        sync_roots=(),
        repository_toplevel=layout.toplevel,
        git_common_dir=layout.git_common_dir,
    )
    spawned: list[ProcessSpec] = []
    monkeypatch.setattr(wt, "run_supervised", lambda spec, cancel: spawned.append(spec))
    outcome = create_worktree(
        str(h.repo),
        base_commit=task.planning_base_commit,
        task_id=task.id,
        root=root,
        tree_writer=CheckedTreeWriter,
        remaining_s=lambda: 0.0,
    )
    assert spawned == []
    assert outcome.verdict is WorktreeVerdict.UNVERIFIABLE and not outcome.created
    assert not h.target(task.id).exists()


@pytest.mark.parametrize(
    ("remaining", "expected"),
    [(None, 120.0), (lambda: 30.0, 30.0), (lambda: 500.0, 120.0), (lambda: 0.0, None)],
)
def test_prazo_do_passo_e_o_menor_e_preserva_o_padrao(
    remaining: Callable[[], float] | None, expected: float | None
) -> None:
    assert wt._step_timeout_s(remaining) == expected


def test_prazo_do_passo_falha_fechado() -> None:
    def boom() -> float:
        raise RuntimeError

    assert wt._step_timeout_s(boom) is None
    assert wt._step_timeout_s(lambda: float("nan")) is None


# ========================================================= 16 · persistência e binding


def test_falha_de_persistencia_nao_publica_binding_nem_remove_worktree(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    task, admission = _admitted(h)

    def boom(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("disco cheio")

    monkeypatch.setattr(em, "publish_prepared_workspace", boom)
    result = h.prepare(admission)
    assert result.status is PreparationStatus.UNVERIFIABLE
    assert result.code is PreparationCode.PERSISTENCE_FAILED
    assert result.stage is PreparationStage.PERSIST
    assert result.binding is BindingState.NOT_ATTEMPTED
    assert h.bindings.resolver._bindings == {}
    assert h.target(task.id).is_dir()
    assert git(h.repo, "rev-parse", "--verify", f"refs/heads/ff/task-{task.id[:8]}").returncode == 0
    assert h.task(task.id).worktree_path is None
    assert result.residue_possible and result.requires_recovery


def test_falha_do_binding_deixa_registro_honesto_e_nenhum_binding(h: Harness) -> None:
    task, admission = _admitted(h)
    result = h.prepare(admission, bindings=HookedBindings(h.bindings, fail_bind=True))
    assert result.status is PreparationStatus.UNVERIFIABLE
    assert result.code is PreparationCode.BINDING_FAILED
    assert result.cause == "binding_rejected_by_test"
    assert result.binding is BindingState.REJECTED
    assert h.target(task.id).is_dir()
    persisted, run = h.task(task.id), h.run(_run_id(admission))
    assert Path(persisted.worktree_path or "") == h.target(task.id) == Path(run.worktree_path or "")
    assert run.error_summary == error_summary(
        PreparationStatus.UNVERIFIABLE, PreparationCode.BINDING_FAILED
    )
    _assert_not_usable(h, result, admission)


# ============================================================== 17 · CAS perdido


def test_cancelamento_antes_da_publicacao_vence_o_cas(h: Harness) -> None:
    task, admission = _admitted(h)
    before = h.task(task.id)

    def create_then_cancel(*args: Any, **kwargs: Any) -> WorktreeOutcome:
        outcome = create_worktree(*args, **kwargs)
        with h.factory() as fresh:
            cancel(fresh, task.id)
            fresh.commit()
        return outcome

    result = h.prepare(admission, git_ops=GitOperations(create=create_then_cancel))
    assert result.status is PreparationStatus.SUPERSEDED
    assert result.code is PreparationCode.PUBLICATION_LOST
    assert result.binding is BindingState.NOT_ATTEMPTED
    after = h.task(task.id)
    assert after.status is TaskStatus.CANCELLED
    assert after.version == before.version + 1  # só o `cancel` escreveu
    assert after.worktree_path is None
    run = h.run(_run_id(admission))
    assert run.status is RunStatus.RUNNING
    assert Path(run.worktree_path or "") == h.target(task.id)  # resíduo registrado no Run aberto
    _assert_not_usable(h, result, admission)


def test_run_final_nunca_e_alterado_pela_preparacao(h: Harness) -> None:
    task, admission = _admitted(h)
    run_id = _run_id(admission)

    def create_then_finalize(*args: Any, **kwargs: Any) -> WorktreeOutcome:
        outcome = create_worktree(*args, **kwargs)
        with h.factory() as fresh:
            fresh.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(status=RunStatus.ERROR, finished_at=datetime.now(UTC), duration_ms=5)
            )
            fresh.commit()
        return outcome

    result = h.prepare(admission, git_ops=GitOperations(create=create_then_finalize))
    assert result.status is PreparationStatus.SUPERSEDED
    assert not result.failure_recorded
    run = h.run(run_id)
    assert run.status is RunStatus.ERROR and run.duration_ms == 5
    assert run.error_summary is None and run.worktree_path is None
    assert h.task(task.id).worktree_path is None  # o CAS da task foi desfeito junto


def _publish(h: Harness, task_id: str, run_id: str, *, version: int, path: str) -> int:
    with h.factory() as fresh:
        return em.publish_prepared_workspace(
            fresh, task_id, control_run_id=run_id, expected_version=version, worktree_path=path
        )


def test_publicacao_exige_versao_e_run_aberto_sem_worktree(h: Harness) -> None:
    """O CAS de J por si só: versão da task **e** `Run.worktree_path IS NULL` no Run aberto.
    Qualquer um que falhe desfaz os dois `UPDATE`s."""
    task, admission = _admitted(h)
    run_id, version = _run_id(admission), h.task(task.id).version
    path = str(h.target(task.id))

    with pytest.raises(ConcurrentTaskUpdate):
        _publish(h, task.id, run_id, version=version - 1, path=path)
    assert h.task(task.id).worktree_path is None and h.run(run_id).worktree_path is None

    with h.factory() as fresh:  # o Run já tem um caminho (outra preparação, ou resíduo)
        fresh.execute(update(Run).where(Run.id == run_id).values(worktree_path="outro"))
        fresh.commit()
    with pytest.raises(ConcurrentTaskUpdate):
        _publish(h, task.id, run_id, version=version, path=path)
    persisted = h.task(task.id)
    assert persisted.worktree_path is None and persisted.version == version  # rollback
    assert h.run(run_id).worktree_path == "outro"

    with h.factory() as fresh:
        fresh.execute(update(Run).where(Run.id == run_id).values(worktree_path=None))
        fresh.commit()
    assert _publish(h, task.id, run_id, version=version, path=path) == version + 1
    assert h.task(task.id).worktree_path == path == h.run(run_id).worktree_path


def test_processos_distintos_disputam_a_publicacao_de_j_e_so_um_vence(
    h: Harness, temp_settings: AppSettings
) -> None:
    """J entre **processos**: o lock em memória do serviço não ajuda aqui. Dois processos
    publicam caminhos diferentes para o mesmo Run e versão; o CAS da task e o predicado
    `Run.worktree_path IS NULL` deixam exatamente um vencer, e a Task e o Run ficam coerentes."""
    task, admission = _admitted(h)
    run_id, version = _run_id(admission), h.task(task.id).version
    go = h.tmp / "go-j"
    workers: list[tuple[subprocess.Popen[str], Path]] = []
    for index in range(2):
        ready = h.tmp / f"ready-j-{index}"
        spec = {
            "data_dir": str(temp_settings.data_dir),
            "database_url": temp_settings.database_url,
            "task_id": task.id,
            "run_id": run_id,
            "version": version,
            "path": str(h.tmp / f"worktree-{index}"),
            "ready": str(ready),
            "go": str(go),
        }
        process = subprocess.Popen(  # noqa: S603 — worker de teste, argv literal, sem shell
            [sys.executable, "-m", "tests.publish_worker_e8_4_2", json.dumps(spec)],
            cwd=API_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        workers.append((process, ready))
    deadline = time.monotonic() + 120
    while not all(ready.exists() for _, ready in workers):
        assert time.monotonic() < deadline, "workers não ficaram prontos"
        for process, _ in workers:
            assert process.poll() is None, process.communicate()
        time.sleep(0.05)
    go.write_text("1", encoding="utf-8")

    results: list[dict[str, Any]] = []
    for process, _ in workers:
        out, err = process.communicate(timeout=180)
        assert process.returncode == 0, err
        line = [item for item in out.splitlines() if item.startswith("RESULT ")][-1]
        results.append(json.loads(line.removeprefix("RESULT ")))

    winners = [r for r in results if "published" in r]
    losers = [r for r in results if "error" in r]
    assert len(winners) == 1 and winners[0]["published"] == version + 1, results
    assert losers == [{"error": "ConcurrentTaskUpdate"}], results
    persisted, run = h.task(task.id), h.run(run_id)
    assert persisted.version == version + 1
    assert persisted.worktree_path is not None
    assert persisted.worktree_path == run.worktree_path
    assert Path(persisted.worktree_path).name in {"worktree-0", "worktree-1"}
    assert run.status is RunStatus.RUNNING


def test_cancelamento_no_ultimo_instante_nao_devolve_contexto(h: Harness) -> None:
    """Cancelamento que chega depois de todas as conferências de L (durante a última, a do
    binding) ainda é visto pela checagem final — nunca há contexto utilizável."""
    task, admission = _admitted(h)
    signal = Signal()

    class _CancelOnVerify(HookedBindings):
        def verify_run(self, **kwargs: Any) -> None:
            super().verify_run(**kwargs)
            signal.flag = True

    result = h.prepare(admission, is_cancelled=signal, bindings=_CancelOnVerify(h.bindings))
    assert result.status is PreparationStatus.CANCELLED
    assert result.stage is PreparationStage.FINAL
    assert result.binding is BindingState.REMOVED
    _assert_not_usable(h, result, admission)


# ============================================================== 18 · concorrência


def test_preparacoes_simultaneas_no_mesmo_processo(h: Harness) -> None:
    task, admission = _admitted(h)
    entered, release = threading.Event(), threading.Event()

    def block() -> None:
        entered.set()
        assert release.wait(30)

    service = h.service(capture=SpyCapture(PostExecutionVerifier(), before=block))
    request = PreparationRequest.from_admission(admission)
    h.session.commit()
    results: list[PreparationResult[Any]] = []
    worker = threading.Thread(
        target=lambda: results.append(service.prepare(request, is_cancelled=Signal()))
    )
    worker.start()
    try:
        assert entered.wait(30)
        second = service.prepare(request, is_cancelled=Signal())
        assert second.status is PreparationStatus.REFUSED
        assert second.code is PreparationCode.PREPARATION_IN_PROGRESS
        assert not second.failure_recorded
    finally:
        release.set()
        worker.join(60)
    [first] = results
    assert first.status is PreparationStatus.PREPARED, first


def test_preparacoes_de_dois_servicos_publicam_um_unico_contexto(h: Harness) -> None:
    """Dois serviços (o análogo, num processo, de dois processos): o primeiro passou da
    revalidação F e está prestes a criar; o segundo prepara inteiro. O primeiro só encontra
    uma worktree que não provou ser sua — e a publicação do segundo não é tocada."""
    task, admission = _admitted(h)
    entered, release = threading.Event(), threading.Event()

    def create_after_wait(*args: Any, **kwargs: Any) -> WorktreeOutcome:
        entered.set()
        assert release.wait(30)
        return create_worktree(*args, **kwargs)

    other_bindings = RunWorkspaceBindings()
    slow = h.service(git_ops=GitOperations(create=create_after_wait), bindings=other_bindings)
    request = PreparationRequest.from_admission(admission)
    h.session.commit()
    results: list[PreparationResult[Any]] = []
    worker = threading.Thread(
        target=lambda: results.append(slow.prepare(request, is_cancelled=Signal()))
    )
    worker.start()
    try:
        assert entered.wait(30)
        fast = h.service().prepare(request, is_cancelled=Signal())
        assert fast.status is PreparationStatus.PREPARED, fast
    finally:
        release.set()
        worker.join(60)
    [late] = results
    assert late.status is PreparationStatus.OWNERSHIP_CONFLICT
    assert late.code is PreparationCode.UNEXPECTED_REUSE
    assert late.context is None and other_bindings.resolver._bindings == {}
    run = h.run(_run_id(admission))
    assert run.error_summary is None  # a publicação do vencedor não foi reescrita
    assert fast.context is not None and run.worktree_path == fast.context.worktree_path


def test_segunda_preparacao_do_mesmo_run_publicado_e_recusada(h: Harness) -> None:
    _task, admission, _ctx = _prepared(h)
    again = h.prepare(admission)
    assert again.status is PreparationStatus.REFUSED
    assert again.code is PreparationCode.ALREADY_PREPARED
    assert not again.failure_recorded
    assert h.run(_run_id(admission)).error_summary is None


# ===================================================== 21 · filtros, hooks e helpers


def test_filtros_hooks_e_fsmonitor_nao_executam_na_preparacao(h: Harness) -> None:
    marker = h.tmp / "sentinela.log"
    sentinel = f'echo x >> "{marker.as_posix()}"'
    (h.repo / ".gitattributes").write_text("*.dat filter=sentinela\n", encoding="utf-8")
    (h.repo / "dados.dat").write_bytes(b"cru\n")
    git(h.repo, "add", "-A")
    git(h.repo, "commit", "-q", "-m", "atributos")
    task, admission = _admitted(h)

    # Configurados **depois** da admissão (cujo `preflight` usa `git status`): o que rodar a
    # partir daqui só pode ter sido a preparação.
    hooks = h.tmp / "hooks"
    hooks.mkdir()
    for name in ("post-checkout", "post-index-change", "reference-transaction"):
        hook = hooks / name
        hook.write_text(f"#!/bin/sh\n{sentinel}\n", encoding="utf-8", newline="\n")
        hook.chmod(0o755)
    for key, value in (
        ("filter.sentinela.smudge", f"sh -c '{sentinel}; cat'"),
        ("filter.sentinela.clean", f"sh -c '{sentinel}; cat'"),
        ("filter.sentinela.process", f"sh -c '{sentinel}'"),
        ("filter.sentinela.required", "true"),
        ("core.hooksPath", hooks.as_posix()),
        ("core.fsmonitor", f"sh -c '{sentinel}'"),
    ):
        git(h.repo, "config", key, value)

    result = h.prepare(admission)
    assert result.status is PreparationStatus.PREPARED, result
    assert result.context is not None
    assert not marker.exists()
    assert (Path(result.context.worktree_path) / "dados.dat").read_bytes() == b"cru\n"


# ============================================================ 22 · vazamento


def test_resultados_e_registros_nao_vazam_caminho_nem_texto_livre(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    task, _admission, ctx = _prepared(h)
    tmp = str(h.tmp)
    for text in (repr(ctx), repr(h.bindings)):
        assert tmp not in text and tmp.replace("\\", "/") not in text

    second = h.next_attempt(task.id, ctx.control_run_id, key="exec-2")

    def boom(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError(f"falhou em {tmp} com segredo=abc")

    monkeypatch.setattr(em, "publish_prepared_workspace", boom)
    result = h.prepare(second)
    assert result.status is PreparationStatus.UNVERIFIABLE
    for text in (repr(result), str(result.cause)):
        assert tmp not in text and "segredo" not in text
    run = h.run(_run_id(second))
    assert run.error_summary is not None and _SUMMARY_RE.fullmatch(run.error_summary)
    with h.factory() as fresh:
        for event in fresh.scalars(select(SafetyEvent).where(SafetyEvent.task_id == task.id)):
            assert tmp not in (event.detail or "") and tmp not in event.subject
    refused = ComponentBindingRefused(PreparationCode.RUN_MISMATCH)
    assert str(refused) == "run_mismatch"


# ======================================================= bindings dos componentes


_CTX_BASE = "__base-do-contexto__"


def _component_run(
    h: Harness,
    ctx: PreparedWorkspace[Any],
    *,
    agent: RunAgent,
    invocation_id: str | None = None,
    base_commit: str | None = _CTX_BASE,
) -> str:
    """Um Run de componente criado **pelo teste** (E8.4.3/E8.4.4 o criarão de verdade)."""
    component = "developer" if agent is RunAgent.DEVELOPER else "test_runner"
    run = Run(
        invocation_id=invocation_id or component_invocation_id(ctx.control_run_id, component),
        task_id=ctx.task_id,
        agent=agent,
        purpose=RunPurpose.EXECUTION,
        attempt_index=ctx.attempt_index,
        fix_round=ctx.fix_round,
        provider="teste",
        provider_adapter="teste",
        transport=RunTransport.PROCESS,
        status=RunStatus.RUNNING,
        started_at=datetime.now(UTC),
        token_source=TokenSource.UNAVAILABLE,
        files_read=None,
        files_read_source=FilesReadSource.UNAVAILABLE,
        base_commit=ctx.base_commit if base_commit == _CTX_BASE else base_commit,
    )
    with h.factory() as fresh:
        fresh.add(run)
        fresh.commit()
        return run.id


def test_binding_de_componente_usa_o_run_id_do_componente(h: Harness) -> None:
    task, _admission, ctx = _prepared(h)
    service = h.service()
    dev = _component_run(h, ctx, agent=RunAgent.DEVELOPER)
    runner = _component_run(h, ctx, agent=RunAgent.TEST_RUNNER)
    tokens = [
        service.bind_component_run(ctx, run_id=run_id, is_cancelled=Signal())
        for run_id in (dev, runner)
    ]
    for run_id in (dev, runner, ctx.control_run_id):
        bound = _resolve(
            h.bindings,
            workspace_id=h.workspace.id,
            task_id=task.id,
            run_id=run_id,
            base=ctx.base_commit,
        )
        assert bound.resolved.run_id == run_id
        assert bound.resolved.workspace_path == ctx.workspace_path
    assert service.release_binding(tokens[0]) is True
    assert service.release_binding(tokens[0]) is False
    with pytest.raises(WorkspaceUnavailable):
        _resolve(
            h.bindings,
            workspace_id=h.workspace.id,
            task_id=task.id,
            run_id=dev,
            base=ctx.base_commit,
        )
    assert Path(ctx.worktree_path).is_dir()  # unbind não é teardown


def test_binding_de_componente_recusa_run_alheio_ou_tentativa_encerrada(h: Harness) -> None:
    task, _admission, ctx = _prepared(h)
    service = h.service()
    with pytest.raises(ComponentBindingRefused) as caught:
        service.bind_component_run(ctx, run_id=ctx.control_run_id, is_cancelled=Signal())
    assert caught.value.code is PreparationCode.RUN_MISMATCH
    forged = _component_run(h, ctx, agent=RunAgent.DEVELOPER, invocation_id="forjado-1")
    with pytest.raises(ComponentBindingRefused):
        service.bind_component_run(ctx, run_id=forged, is_cancelled=Signal())

    legit = _component_run(h, ctx, agent=RunAgent.TEST_RUNNER)
    cancelled = Signal()
    cancelled.flag = True
    with pytest.raises(ComponentBindingRefused) as caught:
        service.bind_component_run(ctx, run_id=legit, is_cancelled=cancelled)
    assert caught.value.code is PreparationCode.CANCEL_REQUESTED
    with h.factory() as fresh:
        cancel(fresh, task.id)
        fresh.commit()
    with pytest.raises(ComponentBindingRefused) as caught:
        service.bind_component_run(ctx, run_id=legit, is_cancelled=Signal())
    assert caught.value.code is PreparationCode.TASK_NOT_EXECUTING
    with pytest.raises(WorkspaceUnavailable):
        _resolve(
            h.bindings,
            workspace_id=h.workspace.id,
            task_id=task.id,
            run_id=legit,
            base=ctx.base_commit,
        )


@pytest.mark.parametrize("agent", [RunAgent.DEVELOPER, RunAgent.TEST_RUNNER])
@pytest.mark.parametrize("base", ["correta", "divergente", "nula"])
def test_binding_de_componente_exige_a_base_do_contexto(
    h: Harness, agent: RunAgent, base: str
) -> None:
    """E842-AUD-001: o `Run.base_commit` do componente precisa ser exatamente o do contexto."""
    task, _admission, ctx = _prepared(h)
    run_id = _component_run(
        h,
        ctx,
        agent=agent,
        base_commit={"correta": _CTX_BASE, "divergente": "f" * 40, "nula": None}[base],
    )
    service = h.service()

    def resolve() -> Any:
        return _resolve(
            h.bindings,
            workspace_id=h.workspace.id,
            task_id=task.id,
            run_id=run_id,
            base=ctx.base_commit,
        )

    if base == "correta":
        token = service.bind_component_run(ctx, run_id=run_id, is_cancelled=Signal())
        assert resolve().resolved.run_id == run_id
        assert service.release_binding(token) is True
        return
    with pytest.raises(ComponentBindingRefused) as caught:
        service.bind_component_run(ctx, run_id=run_id, is_cancelled=Signal())
    assert caught.value.code is PreparationCode.BASE_COMMIT_INCOHERENT
    with pytest.raises(WorkspaceUnavailable):
        resolve()
    assert set(h.bindings.resolver._bindings) == {(h.workspace.id, task.id, ctx.control_run_id)}


@pytest.mark.parametrize("agent", [RunAgent.DEVELOPER, RunAgent.TEST_RUNNER])
@pytest.mark.parametrize("interrupcao", ["cancelamento", "prazo"])
def test_interrupcao_durante_o_binding_de_componente_remove_so_o_proprio(
    h: Harness, agent: RunAgent, interrupcao: str
) -> None:
    """E842-AUD-002: cancelamento ou prazo que cruzam o `bind_run` desfazem **só** o binding
    desta operação; os demais seguem utilizáveis e nenhum token é devolvido."""
    task, _admission, ctx = _prepared(h)
    other_agent = RunAgent.TEST_RUNNER if agent is RunAgent.DEVELOPER else RunAgent.DEVELOPER
    other = _component_run(h, ctx, agent=other_agent)
    h.service().bind_component_run(ctx, run_id=other, is_cancelled=Signal())
    victim = _component_run(h, ctx, agent=agent)

    signal = Signal()

    def interrupt() -> None:
        if interrupcao == "cancelamento":
            signal.flag = True
        else:
            h.clock.fixed = ctx.run_started_at + timedelta(seconds=ctx.task_timeout_s + 1)

    bindings = HookedBindings(h.bindings, after_bind=interrupt)
    service = h.service(bindings=bindings)
    with pytest.raises(ComponentBindingRefused) as caught:
        service.bind_component_run(ctx, run_id=victim, is_cancelled=signal)
    assert caught.value.code is (
        PreparationCode.CANCEL_REQUESTED
        if interrupcao == "cancelamento"
        else PreparationCode.DEADLINE_EXCEEDED
    )
    assert caught.value.binding_revoked is True
    assert bindings.revokes == 1
    with pytest.raises(WorkspaceUnavailable):
        _resolve(
            h.bindings,
            workspace_id=h.workspace.id,
            task_id=task.id,
            run_id=victim,
            base=ctx.base_commit,
        )
    for intact in (other, ctx.control_run_id):
        bound = _resolve(
            h.bindings,
            workspace_id=h.workspace.id,
            task_id=task.id,
            run_id=intact,
            base=ctx.base_commit,
        )
        assert bound.resolved.run_id == intact


class _StuckResolver(InMemoryWorkspaceResolver):
    """A pior hipótese do finding: **toda** remoção física falha (nenhuma entrada sai do
    dicionário). Só a revogação explícita pode negar a resolução."""

    def unbind_exact(self, resolved: Any) -> bool:
        return False

    def _drop(self, key: Any) -> None:
        return None


class _StuckUnbind(HookedBindings):
    """O cenário original do Codex: `unbind_run` devolve `False` sem remover nada."""

    def unbind_run(self, token: object) -> bool:
        self.unbinds += 1
        return False


def _stuck_harness(h: Harness) -> InMemoryWorkspaceResolver:
    resolver = _StuckResolver()
    h.bindings = RunWorkspaceBindings(resolver)
    return resolver


@pytest.mark.parametrize("agent", [RunAgent.DEVELOPER, RunAgent.TEST_RUNNER])
@pytest.mark.parametrize("interrupcao", ["cancelamento", "prazo"])
def test_recusa_por_interrupcao_revoga_mesmo_sem_provar_a_remocao(
    h: Harness, agent: RunAgent, interrupcao: str
) -> None:
    """E842-AUD-002 (definitivo). Cancelamento/prazo durante o registro + remoção física que NÃO
    acontece: a autorização do Run recusado precisa estar **negada na resolução**, observada
    diretamente — não só o flag da exceção. Os demais Runs continuam funcionando."""
    resolver = _stuck_harness(h)
    task, _admission, ctx = _prepared(h)
    other_agent = RunAgent.TEST_RUNNER if agent is RunAgent.DEVELOPER else RunAgent.DEVELOPER
    other = _component_run(h, ctx, agent=other_agent)
    h.service().bind_component_run(ctx, run_id=other, is_cancelled=Signal())
    victim = _component_run(h, ctx, agent=agent)

    signal = Signal()

    def interrupt() -> None:
        if interrupcao == "cancelamento":
            signal.flag = True
        else:
            h.clock.fixed = ctx.run_started_at + timedelta(seconds=ctx.task_timeout_s + 1)

    bindings = _StuckUnbind(h.bindings, after_bind=interrupt)
    with pytest.raises(ComponentBindingRefused) as caught:
        h.service(bindings=bindings).bind_component_run(ctx, run_id=victim, is_cancelled=signal)
    assert caught.value.code is (
        PreparationCode.CANCEL_REQUESTED
        if interrupcao == "cancelamento"
        else PreparationCode.DEADLINE_EXCEEDED
    )
    assert caught.value.binding_revoked is True

    # A hipótese do finding vale: a entrada continua fisicamente no resolvedor…
    assert (h.workspace.id, task.id, victim) in resolver._bindings
    # …e mesmo assim a resolução do Run recusado é negada, pelo caminho que produz `BoundWorkspace`.
    with pytest.raises(WorkspaceUnavailable) as unavailable:
        _resolve(
            h.bindings,
            workspace_id=h.workspace.id,
            task_id=task.id,
            run_id=victim,
            base=ctx.base_commit,
        )
    assert unavailable.value.code == "workspace_unbound"
    for intact in (other, ctx.control_run_id):
        assert (
            _resolve(
                h.bindings,
                workspace_id=h.workspace.id,
                task_id=task.id,
                run_id=intact,
                base=ctx.base_commit,
            ).resolved.run_id
            == intact
        )


def test_autorizacao_revogada_nao_e_restaurada_por_operacoes_do_mesmo_run(h: Harness) -> None:
    resolver = _stuck_harness(h)
    task, _admission, ctx = _prepared(h)
    victim = _component_run(h, ctx, agent=RunAgent.DEVELOPER)
    signal = Signal()
    bindings = HookedBindings(h.bindings, after_bind=lambda: setattr(signal, "flag", True))
    service = h.service(bindings=bindings)
    with pytest.raises(ComponentBindingRefused):
        service.bind_component_run(ctx, run_id=victim, is_cancelled=signal)

    def resolve() -> Any:
        return _resolve(
            h.bindings,
            workspace_id=h.workspace.id,
            task_id=task.id,
            run_id=victim,
            base=ctx.base_commit,
        )

    token = next(v for k, v in resolver._bindings.items() if k[2] == victim)

    def rebind_via_adapter() -> object:
        return h.bindings.bind_run(
            workspace_id=h.workspace.id,
            task_id=task.id,
            run_id=victim,
            invocation_id=component_invocation_id(ctx.control_run_id, "developer"),
            base_commit=ctx.base_commit,
            workspace_path=ctx.workspace_path,
            workspace_prefix=ctx.workspace_prefix,
            root_identity=ctx.workspace_root_identity,
        )

    def rebind_via_resolver() -> None:
        resolver.bind(replace(token))

    def retry_via_service() -> object:
        # nova tentativa legítima do serviço, agora sem interrupção
        return h.service().bind_component_run(ctx, run_id=victim, is_cancelled=Signal())

    for attempt in (rebind_via_adapter, rebind_via_resolver, retry_via_service):
        with pytest.raises((WorkspaceUnavailable, ComponentBindingRefused, ValueError)):
            attempt()
        with pytest.raises(WorkspaceUnavailable):
            resolve()
    # Remover/liberar não limpa a revogação: depois do `unbind` (que apaga a entrada física), o
    # re-registro do mesmo Run continua recusado **como revogado**.
    resolver.unbind(h.workspace.id, task.id, victim)
    with pytest.raises(WorkspaceUnavailable) as revoked_again:
        rebind_via_adapter()
    assert revoked_again.value.code == "binding_revoked"
    with pytest.raises(BindingRevoked):
        resolver.bind(replace(token))
    assert h.service().release_binding(token) is False
    assert resolver.unbind_exact(token) is False
    with pytest.raises(WorkspaceUnavailable):
        resolve()
    # A revogação é do Run: outro Run da mesma task se registra normalmente.
    sibling = _component_run(h, ctx, agent=RunAgent.TEST_RUNNER)
    sibling_token = h.service().bind_component_run(ctx, run_id=sibling, is_cancelled=Signal())
    assert sibling_token is not None


def test_run_revogado_nao_volta_a_ser_registrado_mesmo_com_remocao_fisica_funcionando(
    h: Harness,
) -> None:
    task, _admission, ctx = _prepared(h)
    run_id = _component_run(h, ctx, agent=RunAgent.DEVELOPER)
    token = h.service().bind_component_run(ctx, run_id=run_id, is_cancelled=Signal())
    assert h.bindings.revoke_run(token) is True
    assert h.bindings.resolver._bindings.keys() == {
        (h.workspace.id, task.id, ctx.control_run_id)
    }  # a entrada saiu de fato
    kwargs: dict[str, Any] = {
        "workspace_id": h.workspace.id,
        "task_id": task.id,
        "run_id": run_id,
        "invocation_id": component_invocation_id(ctx.control_run_id, "developer"),
        "base_commit": ctx.base_commit,
        "workspace_path": ctx.workspace_path,
        "workspace_prefix": ctx.workspace_prefix,
        "root_identity": ctx.workspace_root_identity,
    }
    with pytest.raises(WorkspaceUnavailable) as again:
        h.bindings.bind_run(**kwargs)
    assert again.value.code == "binding_revoked"
    with pytest.raises(ComponentBindingRefused) as retry:
        h.service().bind_component_run(ctx, run_id=run_id, is_cancelled=Signal())
    assert retry.value.code is PreparationCode.BINDING_FAILED
    with pytest.raises(WorkspaceUnavailable):
        _resolve(
            h.bindings,
            workspace_id=h.workspace.id,
            task_id=task.id,
            run_id=run_id,
            base=ctx.base_commit,
        )


def test_revogacao_so_vale_para_o_proprio_binding_token(h: Harness) -> None:
    """Token velho não derruba o binding **de outro** que ocupa a mesma chave."""
    task, _admission, ctx = _prepared(h)
    run_id = _component_run(h, ctx, agent=RunAgent.DEVELOPER)
    service = h.service()
    stale = service.bind_component_run(ctx, run_id=run_id, is_cancelled=Signal())
    assert service.release_binding(stale) is True  # liberado legitimamente
    fresh = service.bind_component_run(ctx, run_id=run_id, is_cancelled=Signal())
    assert fresh is not stale
    assert h.bindings.revoke_run(stale) is False  # não é o objeto guardado
    assert (
        _resolve(
            h.bindings,
            workspace_id=h.workspace.id,
            task_id=task.id,
            run_id=run_id,
            base=ctx.base_commit,
        ).resolved
        is fresh
    )
    assert h.bindings.revoke_run(fresh) is True
    with pytest.raises(WorkspaceUnavailable):
        _resolve(
            h.bindings,
            workspace_id=h.workspace.id,
            task_id=task.id,
            run_id=run_id,
            base=ctx.base_commit,
        )
    assert h.bindings.revoke_run("nao-e-um-token") is False


def test_revogacao_e_atomica_com_a_resolucao_entre_threads(h: Harness) -> None:
    """Revogação × `resolve` concorrentes: depois que `revoke` **retornou**, nenhuma resolução
    (de nenhuma thread) devolve o binding. O flag é lido ANTES de resolver, então "flag ligado"
    significa "a revogação terminou antes desta resolução começar"."""
    task, _admission, ctx = _prepared(h)
    run_id = _component_run(h, ctx, agent=RunAgent.DEVELOPER)
    token = h.service().bind_component_run(ctx, run_id=run_id, is_cancelled=Signal())
    ref = ExecutionWorkspaceRef(
        kind=WorkspaceKind.LOCAL_WORKTREE, id=h.workspace.id, base_commit=ctx.base_commit
    )
    scope = RunScope(task_id=task.id, run_id=run_id, invocation_id="x")
    revoked = threading.Event()
    barrier = threading.Barrier(5)
    violations: list[int] = []
    seen_live: list[int] = []

    def reader() -> None:
        barrier.wait(30)
        for index in range(3000):
            after_revocation = revoked.is_set()
            found = h.bindings.resolver.resolve(ref, scope)
            if found is None:
                continue
            if after_revocation:
                violations.append(index)
            else:
                seen_live.append(index)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    barrier.wait(30)
    assert h.bindings.revoke_run(token) is True
    revoked.set()
    for thread in threads:
        thread.join(60)
    assert violations == []
    assert h.bindings.resolver.resolve(ref, scope) is None


def test_compensacao_da_preparacao_revoga_o_binding_do_run_de_controle(h: Harness) -> None:
    """A mesma propriedade no caminho de `prepare`: falha em L com remoção física impossível não
    deixa o Run de controle resolvível."""
    resolver = _stuck_harness(h)
    task, admission = _admitted(h)
    signal = Signal()
    result = h.prepare(
        admission,
        is_cancelled=signal,
        bindings=HookedBindings(h.bindings, after_bind=lambda: setattr(signal, "flag", True)),
    )
    assert result.status is PreparationStatus.CANCELLED and result.stage is PreparationStage.FINAL
    assert result.binding is BindingState.REMOVED
    assert (h.workspace.id, task.id, _run_id(admission)) in resolver._bindings  # hipótese
    with pytest.raises(WorkspaceUnavailable):
        _resolve(
            h.bindings,
            workspace_id=h.workspace.id,
            task_id=task.id,
            run_id=_run_id(admission),
            base=h.task(task.id).planning_base_commit or "0" * 40,
        )
    _assert_not_usable(h, result, admission)


# ======================================================= recusas da releitura (A)


def test_test_config_trocado_depois_da_admissao_e_recusado(h: Harness) -> None:
    task, admission = _admitted(h)
    set_test_config(h.session, h.workspace.id, {**VALID_TEST_CONFIG, "timeout_seconds": 60})
    h.session.commit()
    result = h.prepare(admission)
    assert result.status is PreparationStatus.REFUSED
    assert result.code is PreparationCode.FINGERPRINT_DIVERGED
    assert result.requires_replan
    assert not h.target(task.id).exists()
    # Nenhuma invalidação de pré-admissão sobre uma task `executing`.
    persisted = h.task(task.id)
    assert persisted.status is TaskStatus.EXECUTING and persisted.approved_at is not None


def test_resultado_exige_contexto_se_e_somente_se_preparado() -> None:
    with pytest.raises(ValueError):
        PreparationResult(
            PreparationStatus.PREPARED, PreparationStage.DONE, PreparationCode.PREPARED
        )
    with pytest.raises(ValueError):
        PreparationResult(
            PreparationStatus.REFUSED,
            PreparationStage.FINAL,
            PreparationCode.FINAL_CHECK_FAILED,
            binding=BindingState.PUBLISHED,
        )
    assert (
        error_summary(PreparationStatus.TIMEOUT, PreparationCode.DEADLINE_EXCEEDED)
        == "workspace_preparation:timeout:deadline_exceeded"
    )
