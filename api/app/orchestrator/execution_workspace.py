"""Preparação da execution workspace (E8.4.2) — componente **interno, não operacional**.

Depois de uma admissão **nova** (`AdmissionOutcome.ADMITTED`, E8.4.1), prepara a worktree da
task e devolve um contexto utilizável pelas etapas seguintes (E8.4.3 Developer, E8.4.4 Test
Runner e verificação final), ou um resultado estruturado que impede a continuação. Nada aqui
está ligado a `main.py`, startup, rota, fila ou worker: a composição integrada é da E8.4.6, e
`start_execution` segue parando no `NotImplementedError`.

## Quem faz o quê

* O **Execution Manager** continua o único que escreve `WorkspaceTask`, `Run` e `SafetyEvent`:
  `read_preparation_facts`, `publish_prepared_workspace` e
  `record_workspace_preparation_failure`, cada uma numa sessão nova e curta.
* O **Git Runtime** devolve fatos (`probe_head`, `repository_layout`, `create_worktree`,
  `inspect_task_worktree`); este módulo não forma argv.
* O **Path Runtime** chega por injeção: a `WorktreeRoot` (`prepare_worktree_root`), a
  identidade verificável de diretório e o `CheckedTreeWriter`.
* A **captura da árvore principal** (E8.3) chega por injeção e o snapshot é **opaco**: este
  módulo só lê `verifiable`/`failure` e o entrega intacto no contexto, sem consumi-lo.
* O **binding por Run** chega por injeção (adaptador concreto em `tool_executor.workspace`),
  com dados neutros — este módulo não importa `tool_executor`, `agent_runtime`,
  `process_runtime`, `execution_verification` nem `path_runtime`.

## Ordem (A–L) e por que persistir antes de publicar o binding

A. releitura da admissão (sessão nova), ownership de `id8`, cancelamento e prazo;
B. layout real do repositório principal (toplevel, `.git` comum, prefixo), SHA-1 e `HEAD`;
C. `WorktreeRoot` pela porta, com as `sync_roots` **completas** da configuração; identidades
   ancoradas; reivindicações de caminho; pré-classificação (reuso só com proveniência);
D/E. captura da árvore principal **antes** do primeiro efeito; não verificável bloqueia;
F. revalidação imediatamente antes de criar (banco, `HEAD`, layout, identidades, cancelamento,
   prazo);
G. `create_worktree` com a base congelada, o UUID completo, a raiz validada, o escritor real, o
   cancelamento e o prazo restante;
H. reinspeção independente e identidades da worktree e do workspace interno;
I. revalidação de `HEAD`/layout/identidades;
J. publicação durável por CAS (task + Run de controle);
K. binding do Run de controle, conferido pela regra canônica;
L. checagem final (banco, `HEAD`, identidade, binding, cancelamento, prazo).

**J antes de K.** O CAS é o portão: se cancelamento ou finalização venceram, nenhum binding
utilizável chega a existir. Se J falhar, nada foi publicado em memória e o resíduo em disco é
relatado (não há atomicidade entre SQLite e filesystem). Se K falhar depois de J, a task e o
Run guardam o caminho da worktree que de fato existe — registro honesto do resíduo — e o código
da falha vai para o `error_summary` do Run aberto; o adaptador já revogou o próprio registro. Se
L falhar, a autorização do binding publicado por **esta** preparação é **revogada** (só a dele).
Revogar é negar a resolução do Run e impedir o re-registro dele, e vale ainda que a remoção
física não se prove (E842-AUD-002); não é teardown: a worktree e a branch ficam para diagnóstico
e para a E8.4.5.

## Limites declarados

* `HEAD`/refs são observações separadas do snapshot e revalidadas em A/B, F, I e L; uma troca e
  restauração entre duas leituras não é vista (TOCTOU residual — não é sandbox, [04] §4).
* A exclusão de preparações simultâneas do mesmo Run **neste processo** é um lock em memória;
  entre processos, quem garante que só uma publica é o predicado `Run.worktree_path IS NULL` do
  CAS de J. Nenhum dos dois é a reserva durável de recursos da E8.4.5.
* Falha depois da admissão **não** desfaz `attempts`, não apaga o Run e não devolve a task: ela
  fica `executing` e o Run `running` à espera do finalizador da E8.4.5.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Generic, Protocol, TypeVar

from sqlalchemy.orm import Session

from app.config import AppSettings
from app.db.enums import RunAgent, RunPurpose, RunStatus, SafetyEventKind
from app.db.models import Run
from app.git_runtime import (
    PROBE_OK,
    HeadProbe,
    RepositoryLayout,
    TaskWorktreeFacts,
    VerificationFailure,
    WorktreeOutcome,
    WorktreeVerdict,
    classify_task_worktree,
    create_worktree,
    inspect_task_worktree,
    probe_head,
    repository_layout,
    repository_object_format,
    task_worktree_names,
)
from app.orchestrator import execution_manager as em
from app.orchestrator.developer_binding import DeveloperBindingResolver
from app.orchestrator.errors import ConcurrentTaskUpdate
from app.orchestrator.execution_contract import (
    COMPONENT_DEVELOPER,
    COMPONENT_TEST_RUNNER,
    E2E_OBJECT_FORMATS,
    AdmissionOutcome,
    ExecutionAdmission,
    component_invocation_id,
    operation_timeout_s,
    task_deadline_remaining_s,
)
from app.orchestrator.workspace_contract import (
    BindingState,
    PreparationCode,
    PreparationFacts,
    PreparationStage,
    PreparationStatus,
    error_summary,
)
from app.safety.types import ObjectIdentity, TreeWriterFactory, WorktreeRoot

# ------------------------------------------------------------------------------ portas


class WorktreeRootPort(Protocol):
    """`path_runtime.prepare_worktree_root`: o **único** construtor da `WorktreeRoot`."""

    def __call__(
        self,
        requested: str,
        *,
        sync_roots: tuple[str, ...],
        repository_toplevel: str,
        git_common_dir: str,
    ) -> WorktreeRoot: ...


class DirectoryIdentityPort(Protocol):
    """Identidade **verificável** de um diretório ordinário (Path Runtime). Levanta se não prova."""

    def __call__(self, path: str) -> ObjectIdentity: ...


class MainTreeSnapshotLike(Protocol):
    """O que este módulo lê do snapshot opaco da E8.3 — e nada mais."""

    @property
    def verifiable(self) -> bool: ...

    @property
    def failure(self) -> object: ...


SnapshotT = TypeVar("SnapshotT", bound=MainTreeSnapshotLike)
SnapshotT_co = TypeVar("SnapshotT_co", bound=MainTreeSnapshotLike, covariant=True)


class MainTreeCapturePort(Protocol[SnapshotT_co]):
    """`PostExecutionVerifier.capture_main_tree` (E8.3), injetado."""

    def capture_main_tree(
        self, local_path: str, *, is_cancelled: Callable[[], bool]
    ) -> SnapshotT_co: ...


class RunBindingPort(Protocol):
    """Binding **por Run**, com dados neutros (`tool_executor.workspace.RunWorkspaceBindings`)."""

    def bind_run(
        self,
        *,
        workspace_id: str,
        task_id: str,
        run_id: str,
        invocation_id: str,
        base_commit: str,
        workspace_path: str,
        workspace_prefix: str,
        root_identity: ObjectIdentity,
    ) -> object: ...

    def verify_run(
        self,
        *,
        workspace_id: str,
        task_id: str,
        run_id: str,
        invocation_id: str,
        base_commit: str,
    ) -> None: ...

    def unbind_run(self, token: object) -> bool: ...

    def revoke_run(self, token: object) -> bool:
        """Nega a resolução do Run de ``token`` (e impede o re-registro). `True` só se a negação
        vale; remoção física não é exigida nem suficiente."""
        ...


def _probe_head(local_path: str) -> HeadProbe:
    return probe_head(local_path, resolve_branch=False)


@dataclass(frozen=True, slots=True)
class GitOperations:
    """As leituras e a criação do Git Runtime. Padrão: as funções reais; trocáveis nos testes
    para injetar corridas determinísticas nas fronteiras."""

    probe_head: Callable[[str], HeadProbe] = _probe_head
    repository_layout: Callable[[str], RepositoryLayout | None] = repository_layout
    object_format: Callable[[str], str | None] = repository_object_format
    inspect: Callable[..., TaskWorktreeFacts] = inspect_task_worktree
    create: Callable[..., WorktreeOutcome] = create_worktree


@dataclass(frozen=True, slots=True)
class ExecutionWorkspacePorts(Generic[SnapshotT]):
    """Tudo o que a preparação recebe por injeção (composition root; na E8.4.2, só testes)."""

    prepare_worktree_root: WorktreeRootPort
    directory_identity: DirectoryIdentityPort
    tree_writer: TreeWriterFactory
    main_tree: MainTreeCapturePort[SnapshotT]
    bindings: RunBindingPort
    git: GitOperations = field(default_factory=GitOperations)


# --------------------------------------------------------------------------- resultados


@dataclass(frozen=True, slots=True)
class PreparationRequest:
    """O que o chamador interno confiável entrega: o desfecho da admissão e as duas chaves."""

    outcome: AdmissionOutcome
    task_id: str
    control_run_id: str | None

    @classmethod
    def from_admission(cls, admission: ExecutionAdmission) -> PreparationRequest:
        """Extrai as chaves **enquanto a sessão da admissão está viva**."""
        run_id = admission.run.id if admission.run is not None else None
        return cls(admission.outcome, admission.task.id, run_id)


@dataclass(frozen=True, slots=True)
class PreparedWorkspace(Generic[SnapshotT]):
    """O contexto preparado de **uma** tentativa. Interno: nunca vai ao provider nem é serializado.

    ``worktree_path`` é a raiz completa da worktree (o que `Task/Run.worktree_path` guardam);
    ``workspace_path`` é o workspace **dentro** dela (`ResolvedWorkspace.workspace_path`, inclusive
    em monorepo). ``main_snapshot`` é o snapshot original da E8.3, associado a este Run de
    controle e entregue intacto à verificação da E8.4.4 — não há como reconstruí-lo.
    """

    task_id: str
    workspace_id: str
    control_run_id: str
    control_invocation_id: str
    attempt_index: int
    fix_round: int
    base_commit: str
    workspace_prefix: str
    task_version: int
    run_started_at: datetime
    task_timeout_s: int
    run_timeout_s: int
    worktree_identity: ObjectIdentity
    workspace_root_identity: ObjectIdentity
    worktree_path: str = field(repr=False)
    workspace_path: str = field(repr=False)
    main_snapshot: SnapshotT = field(repr=False, compare=False)
    binding_token: object = field(repr=False, compare=False)

    def __repr__(self) -> str:  # nunca caminho absoluto
        return f"PreparedWorkspace(<task {self.task_id}, run {self.control_run_id}>)"


@dataclass(frozen=True, slots=True)
class PreparationResult(Generic[SnapshotT]):
    """Resultado estruturado. ``context`` existe **se e somente se** ``status`` é `PREPARED`.

    ``created``/``reused``/``process_outcome``/``exit_code``/``tree_confirmed_dead`` são os fatos
    que o Git Runtime reportou (do último passo mutante, quando houve) — `tree_confirmed_dead` não
    prova teardown da tentativa inteira. ``residue_possible`` diz se esta preparação pode ter
    deixado worktree/branch no disco; ``requires_recovery`` pede a E8.4.5; ``requires_finalization``
    diz que a task segue `executing` e o Run `running` à espera do finalizador. Nenhum desses
    fluxos é executado aqui.
    """

    status: PreparationStatus
    stage: PreparationStage
    code: PreparationCode
    cause: str | None = None
    worktree_verdict: WorktreeVerdict | None = None
    created: bool | None = None
    reused: bool | None = None
    residue_possible: bool = False
    process_outcome: str | None = None
    exit_code: int | None = None
    tree_confirmed_dead: bool | None = None
    binding: BindingState = BindingState.NOT_ATTEMPTED
    failure_recorded: bool = False
    requires_replan: bool = False
    requires_recovery: bool = False
    requires_finalization: bool = False
    context: PreparedWorkspace[SnapshotT] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        prepared = self.status is PreparationStatus.PREPARED
        if prepared != (self.context is not None):
            raise ValueError("contexto existe se e somente se a preparação concluiu")
        if prepared != (self.binding is BindingState.PUBLISHED):
            raise ValueError("binding publicado se e somente se a preparação concluiu")


class ComponentBindingRefused(Exception):
    """O binding de um Run de componente foi recusado. A mensagem é só o código."""

    def __init__(self, code: PreparationCode, *, binding_revoked: bool = True) -> None:
        super().__init__(code.value)
        self.code = code
        #: `False` só se um binding foi registrado por esta operação e a **revogação** da
        #: autorização não se provou (remoção física não é o critério).
        self.binding_revoked = binding_revoked


# ------------------------------------------------------------------------- vocabulário

#: Códigos da releitura → desfecho local.
_FACT_STATUS: dict[PreparationCode, PreparationStatus] = {
    PreparationCode.TASK_MISSING: PreparationStatus.REFUSED,
    PreparationCode.RUN_MISMATCH: PreparationStatus.REFUSED,
    PreparationCode.TASK_NOT_EXECUTING: PreparationStatus.SUPERSEDED,
    PreparationCode.RUN_NOT_OPEN: PreparationStatus.SUPERSEDED,
    PreparationCode.ATTEMPT_MISMATCH: PreparationStatus.SUPERSEDED,
    PreparationCode.CANCEL_REQUESTED: PreparationStatus.CANCELLED,
    PreparationCode.WORKSPACE_INACTIVE: PreparationStatus.REFUSED,
    PreparationCode.PLAN_INCOHERENT: PreparationStatus.REFUSED,
    PreparationCode.BASE_COMMIT_INCOHERENT: PreparationStatus.REFUSED,
    PreparationCode.FINGERPRINT_DIVERGED: PreparationStatus.REFUSED,
    PreparationCode.TEST_POLICY_MISSING: PreparationStatus.REFUSED,
}

_REPLAN_CODES = frozenset(
    {
        PreparationCode.PLAN_INCOHERENT,
        PreparationCode.BASE_COMMIT_INCOHERENT,
        PreparationCode.FINGERPRINT_DIVERGED,
        PreparationCode.MAIN_HEAD_DIVERGED,
    }
)

#: Recusas que não pertencem à tentativa admitida: nada a registrar nem a finalizar por elas.
_NOT_OURS = frozenset(
    {
        PreparationCode.NOT_ADMITTED,
        PreparationCode.PREPARATION_IN_PROGRESS,
        PreparationCode.ALREADY_PREPARED,
        PreparationCode.TASK_MISSING,
        PreparationCode.RUN_MISMATCH,
    }
)

#: Decisões de política/integridade → `SafetyEvent`. O resto (técnico, prazo, cancelamento,
#: corrida perdida) não vira evento.
_SAFETY_KINDS: dict[PreparationCode, SafetyEventKind] = {
    PreparationCode.ID8_COLLISION: SafetyEventKind.PATH_DENIED,
    PreparationCode.PATH_CLAIMED: SafetyEventKind.PATH_DENIED,
    PreparationCode.UNPROVEN_REUSE: SafetyEventKind.PATH_DENIED,
    PreparationCode.UNEXPECTED_REUSE: SafetyEventKind.PATH_DENIED,
    PreparationCode.WORKTREE_REFUSED: SafetyEventKind.PATH_DENIED,
    PreparationCode.WORKTREE_ROOT_DENIED: SafetyEventKind.PATH_DENIED,
    PreparationCode.MAIN_HEAD_DIVERGED: SafetyEventKind.TOCTOU_RECHECK_FAILED,
    PreparationCode.LAYOUT_CHANGED: SafetyEventKind.TOCTOU_RECHECK_FAILED,
    PreparationCode.IDENTITY_CHANGED: SafetyEventKind.TOCTOU_RECHECK_FAILED,
}

_RULE_PREFIX = "orchestrator.workspace_preparation."

_SLUG = re.compile(r"[a-z][a-z0-9_]{0,63}")

_COMPONENT_OF_AGENT: dict[RunAgent, str] = {
    RunAgent.DEVELOPER: COMPONENT_DEVELOPER,
    RunAgent.TEST_RUNNER: COMPONENT_TEST_RUNNER,
}


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _slug(value: object) -> str | None:
    """Um código estável vindo de uma porta (`ToolExecutorError.code`) — nunca texto livre."""
    return value if isinstance(value, str) and _SLUG.fullmatch(value) else None


def _verdict_stop(verdict: WorktreeVerdict) -> tuple[PreparationStatus, PreparationCode]:
    if verdict is WorktreeVerdict.UNVERIFIABLE:
        return PreparationStatus.UNVERIFIABLE, PreparationCode.WORKTREE_UNVERIFIABLE
    if verdict is WorktreeVerdict.CREATED_INVALID:
        return PreparationStatus.UNVERIFIABLE, PreparationCode.WORKTREE_CREATED_INVALID
    if verdict is WorktreeVerdict.ROOT_INVALID:
        return PreparationStatus.REFUSED, PreparationCode.IDENTITY_CHANGED
    return PreparationStatus.REFUSED, PreparationCode.WORKTREE_REFUSED


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- o serviço


class _Stop(Exception):
    """Interna: a preparação parou com um desfecho estruturado. Nunca sai do módulo."""

    def __init__(
        self, status: PreparationStatus, code: PreparationCode, *, cause: str | None = None
    ) -> None:
        super().__init__(code.value)
        self.status = status
        self.code = code
        self.cause = cause


@dataclass
class _Attempt:
    """Estado mutável de **uma** chamada de `prepare`. Nunca sai do serviço."""

    request: PreparationRequest
    control_run_id: str
    is_cancelled: Callable[[], bool]
    now: Callable[[], datetime]
    stage: PreparationStage = PreparationStage.ADMISSION
    facts: PreparationFacts | None = None
    target: str | None = None
    reuse_allowed: bool = False
    outcome: WorktreeOutcome | None = None
    published_path: str | None = None
    binding: BindingState = BindingState.NOT_ATTEMPTED
    binding_token: object = None

    # ------------------------------------------------------------ prazo e cancelamento

    def cancelled(self) -> bool:
        try:
            return self.is_cancelled() is not False
        except Exception:
            return True  # fail closed

    def remaining_task_s(self) -> float:
        facts = self.facts
        if facts is None or facts.run_started_at is None or facts.task_timeout_s is None:
            return 0.0
        try:
            return task_deadline_remaining_s(
                facts.run_started_at, facts.task_timeout_s, now=self.now()
            )
        except Exception:
            return 0.0

    def step_remaining_s(self) -> float:
        """O teto de um passo mutante: `min(run_timeout_s, prazo restante da tentativa)`; o
        `create_worktree` ainda o limita ao teto E7.4."""
        facts = self.facts
        if facts is None or facts.run_timeout_s is None:
            return 0.0
        try:
            return operation_timeout_s(
                run_timeout_s=facts.run_timeout_s, remaining_task_s=self.remaining_task_s()
            )
        except Exception:
            return 0.0

    def interrupted(self) -> bool:
        """A porta neutra entregue ao Supervisor, ao escritor e à captura: cancelamento **ou**
        prazo esgotado. Quem distingue os dois depois é `checkpoint`, por fatos — não por texto."""
        return self.cancelled() or not self.remaining_task_s() > 0.0

    def checkpoint(self) -> None:
        if self.cancelled():
            raise _Stop(PreparationStatus.CANCELLED, PreparationCode.CANCEL_REQUESTED)
        if not self.remaining_task_s() > 0.0:
            raise _Stop(PreparationStatus.TIMEOUT, PreparationCode.DEADLINE_EXCEEDED)

    # -------------------------------------------------------------------- resíduos

    @property
    def mutated(self) -> bool:
        """Esta preparação rodou um passo Git mutante ou criou a worktree (fatos reportados)."""
        outcome = self.outcome
        return outcome is not None and (outcome.created or outcome.process_outcome is not None)

    @property
    def residue_path(self) -> str | None:
        """A raiz que **esta** preparação criou ou reusou com proveniência. Nunca a de outro."""
        outcome = self.outcome
        if outcome is None or self.target is None:
            return None
        if self.mutated or (outcome.reused and self.reuse_allowed):
            return self.target
        return None


class ExecutionWorkspaceService(Generic[SnapshotT]):
    """Prepara a execution workspace de uma admissão nova. Uma instância de vida da aplicação.

    ``session_factory`` abre uma sessão **nova** por fase de banco; nenhuma fica aberta durante
    IO. ``settings`` fornece a raiz de worktrees e as `sync_roots` completas. ``now`` é o relógio
    de parede (o prazo conta de `Run.started_at`, nunca de um relógio reiniciado aqui).
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        settings: AppSettings,
        ports: ExecutionWorkspacePorts[SnapshotT],
        developer_binding_resolver: DeveloperBindingResolver | None = None,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._ports = ports
        self._resolver = developer_binding_resolver
        self._now = now
        self._lock = threading.Lock()
        self._in_flight: set[str] = set()

    def __repr__(self) -> str:
        return "<ExecutionWorkspaceService>"

    # ------------------------------------------------------------------ exclusão local

    @contextmanager
    def _exclusive(self, control_run_id: str) -> Iterator[bool]:
        """Uma preparação por Run de controle **neste processo**. Não é garantia entre
        processos (lá vale o CAS de J) nem substitui a reserva durável da E8.4.5."""
        with self._lock:
            if control_run_id in self._in_flight:
                claimed = False
            else:
                self._in_flight.add(control_run_id)
                claimed = True
        try:
            yield claimed
        finally:
            if claimed:
                with self._lock:
                    self._in_flight.discard(control_run_id)

    # -------------------------------------------------------------------------- entrada

    def prepare(
        self, request: PreparationRequest, *, is_cancelled: Callable[[], bool]
    ) -> PreparationResult[SnapshotT]:
        """Prepara a workspace da tentativa admitida. Nunca devolve contexto parcial.

        ``is_cancelled`` é o sinal de cancelamento da tentativa (porta neutra). O
        `cancel_requested` persistido é conferido nas fronteiras A, F e L e pelo CAS de J.
        """
        if (
            request.outcome is not AdmissionOutcome.ADMITTED
            or request.control_run_id is None
            or not callable(is_cancelled)
        ):
            # REPLAYED/SLOT_BUSY não criam worktree, binding nem snapshot e não retomam nada.
            return PreparationResult(
                PreparationStatus.REFUSED, PreparationStage.ADMISSION, PreparationCode.NOT_ADMITTED
            )
        with self._exclusive(request.control_run_id) as claimed:
            if not claimed:
                return PreparationResult(
                    PreparationStatus.REFUSED,
                    PreparationStage.ADMISSION,
                    PreparationCode.PREPARATION_IN_PROGRESS,
                )
            attempt = _Attempt(
                request=request,
                control_run_id=request.control_run_id,
                is_cancelled=is_cancelled,
                now=self._now,
            )
            try:
                return self._prepare(attempt)
            except _Stop as stop:
                return self._stopped(attempt, stop)
            except BaseException:
                # Defeito: nenhum contexto sai daqui. Compensa o binding próprio, registra o
                # código (melhor esforço) e deixa o erro original subir.
                with suppress(Exception):
                    self._stopped(
                        attempt,
                        _Stop(PreparationStatus.UNVERIFIABLE, PreparationCode.INTERNAL_ERROR),
                    )
                raise

    # ------------------------------------------------------------------------ A — L

    def _prepare(self, a: _Attempt) -> PreparationResult[SnapshotT]:
        git = self._ports.git

        # A — admissão persistida, ownership de id8, cancelamento e prazo.
        a.stage = PreparationStage.PRECONDITIONS
        facts = self._read(a)
        if facts.control_worktree_path is not None:
            raise _Stop(PreparationStatus.REFUSED, PreparationCode.ALREADY_PREPARED)
        a.facts = facts
        a.checkpoint()
        if facts.id8_collision:
            raise _Stop(PreparationStatus.OWNERSHIP_CONFLICT, PreparationCode.ID8_COLLISION)
        assert facts.local_path is not None and facts.base_commit is not None
        local, base = facts.local_path, facts.base_commit

        # B — layout real, formato de objeto e HEAD (nunca segue o HEAD: só confere).
        a.stage = PreparationStage.LAYOUT
        if git.object_format(local) not in E2E_OBJECT_FORMATS:
            raise _Stop(PreparationStatus.REFUSED, PreparationCode.OBJECT_FORMAT_UNSUPPORTED)
        layout = git.repository_layout(local)
        if layout is None:
            raise _Stop(PreparationStatus.UNVERIFIABLE, PreparationCode.LAYOUT_UNVERIFIABLE)
        self._require_head(local, base)
        a.checkpoint()

        # C — raiz de worktrees, identidades ancoradas, reivindicações e pré-classificação.
        a.stage = PreparationStage.WORKTREE_ROOT
        root = self._worktree_root(layout)
        self._require_anchors(root, layout)
        try:
            names = task_worktree_names(facts.task_id)
        except ValueError:
            raise _Stop(PreparationStatus.REFUSED, PreparationCode.WORKTREE_REFUSED) from None
        target = os.path.normpath(os.path.join(root.canonical_path, names.directory_name))
        a.target = target
        if any(_same_path(path, target) for path in facts.foreign_paths):
            raise _Stop(PreparationStatus.OWNERSHIP_CONFLICT, PreparationCode.PATH_CLAIMED)
        provenance = any(_same_path(path, target) for path in facts.own_paths)
        before = classify_task_worktree(
            git.inspect(local, base_commit=base, task_id=facts.task_id, root=root)
        )
        if before is WorktreeVerdict.REUSABLE and not provenance:
            # Estruturalmente reusável não é "desta task": sem registro dela, nada é adotado.
            raise _Stop(PreparationStatus.OWNERSHIP_CONFLICT, PreparationCode.UNPROVEN_REUSE)
        if before not in (WorktreeVerdict.ABSENT, WorktreeVerdict.REUSABLE):
            status, code = _verdict_stop(before)
            raise _Stop(status, code, cause=before.value)
        a.reuse_allowed = before is WorktreeVerdict.REUSABLE
        a.checkpoint()

        # D/E — a árvore principal ANTES do primeiro efeito; incompleta bloqueia.
        a.stage = PreparationStage.MAIN_SNAPSHOT
        snapshot = self._ports.main_tree.capture_main_tree(local, is_cancelled=a.interrupted)
        a.checkpoint()
        if snapshot.verifiable is not True:
            failure = snapshot.failure
            raise _Stop(
                PreparationStatus.UNVERIFIABLE,
                PreparationCode.MAIN_SNAPSHOT_UNVERIFIABLE,
                cause=failure.value if isinstance(failure, VerificationFailure) else None,
            )

        # F — revalidação imediatamente antes do primeiro efeito.
        a.stage = PreparationStage.PRE_CREATE
        self._reread(a)
        self._require_head(local, base)
        self._require_same_layout(local, layout)
        self._require_anchors(root, layout)
        a.checkpoint()

        # G — criação (ou reuso comprovado) na base congelada, sob o prazo restante.
        a.stage = PreparationStage.CREATE
        outcome = git.create(
            local,
            base_commit=base,
            task_id=facts.task_id,
            root=root,
            tree_writer=self._ports.tree_writer,
            is_cancelled=a.interrupted,
            remaining_s=a.step_remaining_s,
        )
        a.outcome = outcome
        a.checkpoint()  # timeout/cancelamento que cruzaram o último passo nunca viram sucesso
        if outcome.verdict is not WorktreeVerdict.REUSABLE:
            status, code = _verdict_stop(outcome.verdict)
            raise _Stop(status, code, cause=outcome.verdict.value)
        if outcome.reused and not a.reuse_allowed:
            raise _Stop(PreparationStatus.OWNERSHIP_CONFLICT, PreparationCode.UNEXPECTED_REUSE)
        if (
            outcome.created == outcome.reused
            or outcome.path is None
            or outcome.workspace_path is None
            or not _same_path(outcome.path, target)
            or outcome.head != base
        ):
            raise _Stop(PreparationStatus.UNVERIFIABLE, PreparationCode.WORKTREE_POSTCHECK_FAILED)
        worktree_path, workspace_path = outcome.path, outcome.workspace_path

        # H — reinspeção independente e identidades da worktree e do workspace interno.
        a.stage = PreparationStage.INSPECT
        after = classify_task_worktree(
            git.inspect(local, base_commit=base, task_id=facts.task_id, root=root)
        )
        if after is not WorktreeVerdict.REUSABLE:
            raise _Stop(
                PreparationStatus.UNVERIFIABLE,
                PreparationCode.WORKTREE_POSTCHECK_FAILED,
                cause=after.value,
            )
        worktree_identity = self._identity(worktree_path)
        workspace_identity = self._identity(workspace_path)

        # I — a principal e as três identidades continuam as mesmas.
        a.stage = PreparationStage.POST_CREATE
        self._require_head(local, base)
        self._require_same_layout(local, layout)
        self._require_anchors(root, layout)
        if (
            self._identity(worktree_path) != worktree_identity
            or self._identity(workspace_path) != workspace_identity
        ):
            raise _Stop(PreparationStatus.REFUSED, PreparationCode.IDENTITY_CHANGED)
        a.checkpoint()

        # J — publicação durável, protegida por CAS. Nenhum IO externo dentro dela.
        a.stage = PreparationStage.PERSIST
        version = self._publish(a, worktree_path)
        a.published_path = worktree_path

        # K — binding do Run de controle, conferido pela regra canônica.
        a.stage = PreparationStage.BIND
        assert facts.workspace_id is not None and facts.control_invocation_id is not None
        try:
            token = self._ports.bindings.bind_run(
                workspace_id=facts.workspace_id,
                task_id=facts.task_id,
                run_id=a.control_run_id,
                invocation_id=facts.control_invocation_id,
                base_commit=base,
                workspace_path=workspace_path,
                workspace_prefix=layout.workspace_prefix,
                root_identity=workspace_identity,
            )
        except Exception as exc:
            a.binding = BindingState.REJECTED
            raise _Stop(
                PreparationStatus.UNVERIFIABLE,
                PreparationCode.BINDING_FAILED,
                cause=_slug(getattr(exc, "code", None)),
            ) from None
        a.binding, a.binding_token = BindingState.PUBLISHED, token

        # L — checagem final antes de devolver qualquer coisa utilizável.
        a.stage = PreparationStage.FINAL
        a.checkpoint()
        final = self._read(a)
        if (
            final.task_version != version
            or final.control_worktree_path is None
            or not _same_path(final.control_worktree_path, worktree_path)
        ):
            raise _Stop(PreparationStatus.SUPERSEDED, PreparationCode.FINAL_CHECK_FAILED)
        self._require_head(local, base)
        if (
            self._identity(worktree_path) != worktree_identity
            or self._identity(workspace_path) != workspace_identity
        ):
            raise _Stop(PreparationStatus.REFUSED, PreparationCode.IDENTITY_CHANGED)
        try:
            self._ports.bindings.verify_run(
                workspace_id=facts.workspace_id,
                task_id=facts.task_id,
                run_id=a.control_run_id,
                invocation_id=facts.control_invocation_id,
                base_commit=base,
            )
        except Exception as exc:
            raise _Stop(
                PreparationStatus.UNVERIFIABLE,
                PreparationCode.FINAL_CHECK_FAILED,
                cause=_slug(getattr(exc, "code", None)),
            ) from None
        a.checkpoint()

        assert (
            facts.attempt_index is not None
            and facts.fix_round is not None
            and facts.run_started_at is not None
            and facts.task_timeout_s is not None
            and facts.run_timeout_s is not None
        )
        context = PreparedWorkspace(
            task_id=facts.task_id,
            workspace_id=facts.workspace_id,
            control_run_id=a.control_run_id,
            control_invocation_id=facts.control_invocation_id,
            attempt_index=facts.attempt_index,
            fix_round=facts.fix_round,
            base_commit=base,
            workspace_prefix=layout.workspace_prefix,
            task_version=version,
            run_started_at=facts.run_started_at,
            task_timeout_s=facts.task_timeout_s,
            run_timeout_s=facts.run_timeout_s,
            worktree_identity=worktree_identity,
            workspace_root_identity=workspace_identity,
            worktree_path=worktree_path,
            workspace_path=workspace_path,
            main_snapshot=snapshot,
            binding_token=token,
        )
        a.stage = PreparationStage.DONE
        return self._result(
            a, PreparationStatus.PREPARED, PreparationCode.PREPARED, None, context=context
        )

    # ------------------------------------------------------------------- etapas de leitura

    def _read(self, a: _Attempt) -> PreparationFacts:
        """Releitura numa sessão **nova**, fechada antes de qualquer IO externo."""
        with self._session_factory() as session:
            facts = em.read_preparation_facts(
                session,
                a.request.task_id,
                control_run_id=a.control_run_id,
                developer_binding_resolver=self._resolver,
            )
            session.rollback()
        if facts.code is not None:
            raise _Stop(_FACT_STATUS.get(facts.code, PreparationStatus.REFUSED), facts.code)
        return facts

    def _reread(self, a: _Attempt) -> None:
        """F: a admissão continua a mesma — mesma versão da task, mesma base, nada publicado."""
        assert a.facts is not None
        again = self._read(a)
        if again.task_version != a.facts.task_version:
            raise _Stop(PreparationStatus.SUPERSEDED, PreparationCode.TASK_VERSION_CHANGED)
        if again.control_worktree_path is not None:
            raise _Stop(PreparationStatus.REFUSED, PreparationCode.ALREADY_PREPARED)
        if again.base_commit != a.facts.base_commit or again.local_path != a.facts.local_path:
            raise _Stop(PreparationStatus.REFUSED, PreparationCode.LAYOUT_CHANGED)
        if again.id8_collision:
            raise _Stop(PreparationStatus.OWNERSHIP_CONFLICT, PreparationCode.ID8_COLLISION)
        target = a.target
        if target is not None and any(_same_path(path, target) for path in again.foreign_paths):
            raise _Stop(PreparationStatus.OWNERSHIP_CONFLICT, PreparationCode.PATH_CLAIMED)
        a.facts = again

    def _require_head(self, local: str, base: str) -> None:
        probe = self._ports.git.probe_head(local)
        if probe.state != PROBE_OK or probe.head is None:
            raise _Stop(PreparationStatus.UNVERIFIABLE, PreparationCode.MAIN_HEAD_UNVERIFIABLE)
        if probe.head != base:
            raise _Stop(PreparationStatus.REFUSED, PreparationCode.MAIN_HEAD_DIVERGED)

    def _require_same_layout(self, local: str, layout: RepositoryLayout) -> None:
        current = self._ports.git.repository_layout(local)
        if current is None:
            raise _Stop(PreparationStatus.UNVERIFIABLE, PreparationCode.LAYOUT_UNVERIFIABLE)
        if current != layout:
            raise _Stop(PreparationStatus.REFUSED, PreparationCode.LAYOUT_CHANGED)

    def _worktree_root(self, layout: RepositoryLayout) -> WorktreeRoot:
        settings = self._settings
        try:
            return self._ports.prepare_worktree_root(
                str(settings.resolved_worktrees_dir),
                sync_roots=tuple(str(root) for root in settings.sync_roots),
                repository_toplevel=layout.toplevel,
                git_common_dir=layout.git_common_dir,
            )
        except PermissionError:  # `PathAccessDenied`: a política negou
            raise _Stop(PreparationStatus.REFUSED, PreparationCode.WORKTREE_ROOT_DENIED) from None
        except Exception:
            raise _Stop(
                PreparationStatus.UNVERIFIABLE, PreparationCode.WORKTREE_ROOT_UNVERIFIABLE
            ) from None

    def _identity(self, path: str) -> ObjectIdentity:
        try:
            identity = self._ports.directory_identity(path)
        except Exception:
            raise _Stop(
                PreparationStatus.UNVERIFIABLE, PreparationCode.IDENTITY_UNVERIFIABLE
            ) from None
        if not isinstance(identity, ObjectIdentity) or not identity.is_verifiable:
            raise _Stop(PreparationStatus.UNVERIFIABLE, PreparationCode.IDENTITY_UNVERIFIABLE)
        return identity

    def _require_anchors(self, root: WorktreeRoot, layout: RepositoryLayout) -> None:
        """Raiz configurada, toplevel e `.git` comum continuam os objetos que a `WorktreeRoot`
        registrou — a raiz validada é **deste** repositório."""
        if (
            self._identity(root.canonical_path) != root.identity
            or self._identity(layout.toplevel) != root.repository_toplevel_identity
            or self._identity(layout.git_common_dir) != root.git_common_dir_identity
        ):
            raise _Stop(PreparationStatus.REFUSED, PreparationCode.IDENTITY_CHANGED)

    # --------------------------------------------------------------------- escrita

    def _publish(self, a: _Attempt, worktree_path: str) -> int:
        assert a.facts is not None and a.facts.task_version is not None
        try:
            with self._session_factory() as session:
                return em.publish_prepared_workspace(
                    session,
                    a.request.task_id,
                    control_run_id=a.control_run_id,
                    expected_version=a.facts.task_version,
                    worktree_path=worktree_path,
                )
        except ConcurrentTaskUpdate:
            raise _Stop(PreparationStatus.SUPERSEDED, PreparationCode.PUBLICATION_LOST) from None
        except Exception:
            raise _Stop(
                PreparationStatus.UNVERIFIABLE, PreparationCode.PERSISTENCE_FAILED
            ) from None

    def _stopped(self, a: _Attempt, stop: _Stop) -> PreparationResult[SnapshotT]:
        """Compensa (só o binding próprio), registra (só no Run aberto, sem sobrescrever outro
        publicador) e devolve o resultado sem contexto."""
        if a.binding is BindingState.PUBLISHED:
            try:
                revoked = self._ports.bindings.revoke_run(a.binding_token)
            except Exception:
                revoked = False
            a.binding = BindingState.REMOVED if revoked is True else BindingState.REMOVAL_FAILED
        recorded = False
        if stop.code not in _NOT_OURS:
            kind = _SAFETY_KINDS.get(stop.code)
            try:
                with self._session_factory() as session:
                    recorded = em.record_workspace_preparation_failure(
                        session,
                        a.request.task_id,
                        control_run_id=a.control_run_id,
                        summary=error_summary(stop.status, stop.code),
                        published_path=a.published_path,
                        residue_path=a.residue_path,
                        safety_kind=kind,
                        rule_id=None if kind is None else f"{_RULE_PREFIX}{stop.code.value}",
                    )
            except Exception:
                recorded = False
        return self._result(a, stop.status, stop.code, stop.cause, recorded=recorded)

    def _result(
        self,
        a: _Attempt,
        status: PreparationStatus,
        code: PreparationCode,
        cause: str | None,
        *,
        recorded: bool = False,
        context: PreparedWorkspace[SnapshotT] | None = None,
    ) -> PreparationResult[SnapshotT]:
        outcome = a.outcome
        prepared = status is PreparationStatus.PREPARED
        residue = a.mutated or a.residue_path is not None
        verdict = outcome.verdict if outcome is not None else None
        return PreparationResult(
            status=status,
            stage=a.stage,
            code=code,
            cause=cause,
            worktree_verdict=verdict,
            created=None if outcome is None else outcome.created,
            reused=None if outcome is None else outcome.reused,
            residue_possible=residue,
            process_outcome=(
                None
                if outcome is None or outcome.process_outcome is None
                else outcome.process_outcome.value
            ),
            exit_code=None if outcome is None else outcome.exit_code,
            tree_confirmed_dead=None if outcome is None else outcome.tree_confirmed_dead,
            binding=a.binding,
            failure_recorded=recorded,
            requires_replan=code in _REPLAN_CODES,
            requires_recovery=not prepared
            and (residue or a.binding is BindingState.REMOVAL_FAILED),
            requires_finalization=not prepared and code not in _NOT_OURS,
            context=context,
        )

    # ---------------------------------------------------- bindings de componentes (E8.4.3+)

    def bind_component_run(
        self,
        context: PreparedWorkspace[SnapshotT],
        *,
        run_id: str,
        is_cancelled: Callable[[], bool],
    ) -> object:
        """Registra o binding de um Run **existente** de componente da mesma tentativa.

        Não cria Run nenhum (E8.4.3/E8.4.4 criam os seus). Exige: Run aberto, da mesma task,
        `developer`/`test_runner` com `purpose = execution`, mesma tentativa, `invocation_id`
        derivado do Run de controle e `base_commit` **igual** ao do contexto (nulo é recusa);
        a tentativa ainda é a publicada (versão, caminho, Run de
        controle aberto, sem cancelamento, no prazo) e a raiz do workspace com a identidade
        registrada. O binding usa o `run_id` **do componente** — nunca o do controle. Devolve o
        token para `release_binding`; recusa levanta `ComponentBindingRefused`. Cancelamento ou
        prazo que cruzam o registro desfazem **só** o binding desta operação (nenhum token sai).
        """
        if not isinstance(context, PreparedWorkspace) or run_id == context.control_run_id:
            raise ComponentBindingRefused(PreparationCode.RUN_MISMATCH)
        with self._session_factory() as session:
            run = session.get(Run, run_id)
            component = None if run is None else _COMPONENT_OF_AGENT.get(run.agent)
            eligible = (
                run is not None
                and component is not None
                and run.task_id == context.task_id
                and run.purpose is RunPurpose.EXECUTION
                and run.status is RunStatus.RUNNING
                and run.attempt_index == context.attempt_index
                and run.fix_round == context.fix_round
                and run.invocation_id == component_invocation_id(context.control_run_id, component)
            )
            invocation_id = run.invocation_id if run is not None else None
            run_base_commit = run.base_commit if run is not None else None
            facts = em.read_preparation_facts(
                session,
                context.task_id,
                control_run_id=context.control_run_id,
                developer_binding_resolver=self._resolver,
            )
            session.rollback()
        if not eligible or invocation_id is None:
            raise ComponentBindingRefused(PreparationCode.RUN_MISMATCH)
        if run_base_commit is None or run_base_commit != context.base_commit:
            raise ComponentBindingRefused(PreparationCode.BASE_COMMIT_INCOHERENT)
        if facts.code is not None:
            raise ComponentBindingRefused(facts.code)
        if (
            facts.task_version != context.task_version
            or facts.control_worktree_path is None
            or not _same_path(facts.control_worktree_path, context.worktree_path)
        ):
            raise ComponentBindingRefused(PreparationCode.FINAL_CHECK_FAILED)
        check = _Attempt(
            request=PreparationRequest(
                AdmissionOutcome.ADMITTED, context.task_id, context.control_run_id
            ),
            control_run_id=context.control_run_id,
            is_cancelled=is_cancelled,
            now=self._now,
            facts=facts,
        )
        try:
            check.checkpoint()
            if self._identity(context.workspace_path) != context.workspace_root_identity:
                raise _Stop(PreparationStatus.REFUSED, PreparationCode.IDENTITY_CHANGED)
        except _Stop as stop:
            raise ComponentBindingRefused(stop.code) from None
        try:
            token = self._ports.bindings.bind_run(
                workspace_id=context.workspace_id,
                task_id=context.task_id,
                run_id=run_id,
                invocation_id=invocation_id,
                base_commit=context.base_commit,
                workspace_path=context.workspace_path,
                workspace_prefix=context.workspace_prefix,
                root_identity=context.workspace_root_identity,
            )
        except Exception:
            raise ComponentBindingRefused(PreparationCode.BINDING_FAILED) from None
        # Cancelamento ou prazo que cruzaram o registro: o token desta operação não sai daqui e
        # a autorização é **revogada** (negada na resolução), não apenas removida (E842-AUD-002).
        try:
            check.checkpoint()
        except _Stop as stop:
            raise ComponentBindingRefused(stop.code, binding_revoked=self._revoke(token)) from None
        except BaseException:
            self._revoke(token)
            raise
        return token

    def _revoke(self, token: object) -> bool:
        try:
            return self._ports.bindings.revoke_run(token) is True
        except Exception:
            return False

    def release_binding(self, token: object) -> bool:
        """Libera (fim normal de uso) só o binding de ``token``. **Não** revoga nem é teardown."""
        try:
            return self._ports.bindings.unbind_run(token) is True
        except Exception:
            return False


__all__ = [
    "ComponentBindingRefused",
    "DirectoryIdentityPort",
    "ExecutionWorkspacePorts",
    "ExecutionWorkspaceService",
    "GitOperations",
    "MainTreeCapturePort",
    "MainTreeSnapshotLike",
    "PreparationRequest",
    "PreparationResult",
    "PreparedWorkspace",
    "RunBindingPort",
    "WorktreeRootPort",
]
