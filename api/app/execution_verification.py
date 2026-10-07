"""Verificação pós-execução (E8.3) — [04] §7, "Verificação pós-execução", itens 1 e 2.

Camada pequena e **neutra**: compõe fatos do `git_runtime` e do `path_runtime` com a política
pura do `safety` e devolve um **fato estruturado**. Não conhece banco, HTTP, provider,
orchestrator nem `agent_runtime`; não grava `SafetyEvent`, não muda estado de task. Quem
traduz o resultado em `SafetyEvent(out_of_worktree_write)`, `Run.status` e parada da task é o
Execution Manager (E8.4).

## Uso

```text
before = verifier.capture_main_tree(workspace.local_path, is_cancelled=…)   # ANTES do run
… Developer (mediado) e Test Runner (código do projeto) …
result = verifier.verify(before=before, workspace_path=…, workspace_prefix=…,
                         root_identity=…, base_commit=…, policy=composed_policy,
                         is_cancelled=…)
```

O snapshot `before` só nasce de `capture_main_tree` (não se constrói à mão) e vale para **uma**
verificação: não há como "reconstruir o antes" depois do run. ``is_cancelled`` é uma porta
neutra (`Callable[[], bool]`); a E8.4 adapta o `CancelToken` do run para ela.

## O que é verificado, nesta ordem

1. **Árvore principal** — o estado *read-only* e *filter-free* do **repositório principal
   inteiro** (a raiz vem do Git, nunca só o `local_path`): identidade da raiz, **índice efetivo**
   (bytes crus) e **filesystem** da raiz (rastreado, não rastreado, ignorado, repositórios
   aninhados), sem `git status`/`diff` e sem nenhum helper do projeto. Antes × depois diferente
   → `OUT_OF_WORKTREE_WRITE`. Estado ilegível antes ou depois → `UNVERIFIABLE`.
2. **Raiz do workspace na worktree** — `bind_root` com a identidade registrada no binding.
3. **Mudanças da worktree** contra o `base_commit`, no repositório da worktree **inteiro**
   (SHA-1 ou SHA-256), com a identidade da worktree conferida (`git_runtime.worktree_changes`).
   O Git Runtime é a única fonte: nenhuma lista do Developer ou do Test Runner é aceita.
4. Cada caminho mudado: fora do `workspace_prefix` → `OUTSIDE_WORKSPACE_CHANGE`; dentro, a
   pipeline de path de uma **escrita** mediada (`prevalidate_mediated_path` → `inspect` →
   `decide_path(WRITE)`); negado → `PATH_POLICY_DENIED`; inspeção que falhou → `UNVERIFIABLE`.
   Caminho apagado é validado pelo próprio caminho (`tolerate_absent_tail`), sem recriá-lo.

## Orçamento

`capture_main_tree` e `verify` têm, **cada um**, um único `VerificationBudget` — 100 000
entradas, 1 GiB de bytes efetivamente lidos, 60 s e o cancelamento —, que cobre todos os seus
subpassos (o estado "depois", a identidade da worktree, os registros do Git, o filesystem e o
hash). Orçamento esgotado é `UNVERIFIABLE` com causa estruturada (`limit_exceeded`,
`deadline_exceeded`, `cancelled`) — nunca `VERIFIED` parcial. Todo `VERIFIED` sai de um único
ponto (`_verified`), que revalida o orçamento imediatamente antes de liberar: prazo ou
cancelamento que cruzaram a última operação também bloqueiam.

## O que isto **não** é

É **detecção** depois do fato, não contenção: o Test Runner não é sandbox ([04] §6), e escrita
em qualquer outro lugar do disco não é vista. Na árvore principal, os metadados do `.git` além
do índice (`HEAD`, refs, objetos, config, hooks, `info/exclude`, reflogs) e *alternate data
streams* do NTFS ficam fora. Tudo aqui é **só leitura**.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from app.git_runtime import (
    MainTreeState,
    VerificationBudget,
    VerificationFailure,
    WorktreeChangeKind,
    main_tree_state,
    worktree_changes,
)
from app.git_runtime.post_execution import BudgetExhausted
from app.path_runtime import PathIntegrityViolation, PathOperationFailed, bind_root, inspect
from app.safety import decide_path, prevalidate_mediated_path
from app.safety.paths import PathIntent
from app.safety.policy import SafetyPolicy
from app.safety.types import ObjectIdentity


class PostExecutionStatus(str, Enum):
    """Veredito da verificação. Só `VERIFIED` libera; todo o resto bloqueia (E8.4)."""

    VERIFIED = "verified"
    #: A árvore principal mudou durante o run ([04] §7, item 1).
    OUT_OF_WORKTREE_WRITE = "out_of_worktree_write"
    #: Algo mudou na worktree **fora** do `workspace_prefix` da task.
    OUTSIDE_WORKSPACE_CHANGE = "outside_workspace_change"
    #: Um caminho mudado dentro do workspace não passaria pela política de escrita.
    PATH_POLICY_DENIED = "path_policy_denied"
    #: Link/reparse, entrada não suportada, objeto que mudou durante a leitura, identidade que não
    #: prova continuidade (`file_id == 0`), worktree que não é a vinculada, `HEAD` movido,
    #: operação Git em andamento ou raiz com identidade diferente.
    INTEGRITY_VIOLATION = "integrity_violation"
    #: Não foi possível observar o necessário para concluir (inclusive orçamento). Fail closed.
    UNVERIFIABLE = "unverifiable"


#: Códigos estáveis de motivo (`PostExecutionResult.reason`), além dos de `VerificationFailure`.
MAIN_TREE_UNVERIFIABLE_BEFORE = "main_tree_unverifiable_before"
MAIN_TREE_UNVERIFIABLE_AFTER = "main_tree_unverifiable_after"
MAIN_TREE_CHANGED = "main_tree_changed"
WORKSPACE_ROOT_INTEGRITY = "workspace_root_integrity"
WORKSPACE_ROOT_UNAVAILABLE = "workspace_root_unavailable"
PATH_INSPECTION_FAILED = "path_inspection_failed"
CHANGE_OUTSIDE_WORKSPACE = "change_outside_workspace"

_INTEGRITY_FAILURES = frozenset(
    {
        VerificationFailure.LINK_OR_REPARSE,
        VerificationFailure.UNSUPPORTED_ENTRY,
        VerificationFailure.CHANGED_DURING_READ,
        VerificationFailure.INTEGRITY_UNVERIFIABLE,
        VerificationFailure.NOT_LINKED_WORKTREE,
        VerificationFailure.HEAD_MOVED,
        VerificationFailure.OPERATION_IN_PROGRESS,
    }
)


@dataclass(frozen=True, slots=True)
class ChangedPath:
    """Uma mudança observada pelo Git Runtime. ``path`` é relativo à raiz do repositório da
    worktree (com `/`) — nunca absoluto."""

    path: str
    kind: WorktreeChangeKind


@dataclass(frozen=True, slots=True)
class PathFinding:
    """Um caminho mudado que impede `VERIFIED`, com o motivo estruturado."""

    path: str
    kind: WorktreeChangeKind
    status: PostExecutionStatus
    #: `rule_id` da safety quando a política negou; senão o código do motivo.
    rule_id: str


@dataclass(frozen=True, slots=True)
class PostExecutionResult:
    """O fato que a E8.4 persiste. Sem caminho absoluto, sem conteúdo, sem saída de processo.

    ``reason`` é o código estável do passo que bloqueou; ``failure`` é a causa estruturada
    quando ela veio de uma observação que não concluiu (orçamento, leitura, integridade) — é
    por ela, e nunca por texto, que se distingue `limit_exceeded`, `deadline_exceeded` e
    `cancelled`. ``changes`` lista as mudanças da worktree quando ela chegou a ser analisada;
    ``findings`` só os caminhos que impedem `VERIFIED`.
    """

    status: PostExecutionStatus
    reason: str | None
    changes: tuple[ChangedPath, ...] = ()
    findings: tuple[PathFinding, ...] = ()
    failure: VerificationFailure | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, PostExecutionStatus):
            raise TypeError("status precisa ser PostExecutionStatus")
        if self.failure is not None and not isinstance(self.failure, VerificationFailure):
            raise TypeError("failure precisa ser VerificationFailure")
        if self.status is PostExecutionStatus.VERIFIED:
            if self.reason is not None or self.findings or self.failure is not None:
                raise ValueError("VERIFIED não carrega reason, findings nem failure")
        elif not isinstance(self.reason, str) or not self.reason:
            raise ValueError("resultado que bloqueia precisa de reason")


_CAPTURE = object()


class MainTreeSnapshot:
    """O estado da árvore principal **antes** do run. Opaco, imutável e de uso único.

    Só `PostExecutionVerifier.capture_main_tree` o constrói. Uma captura que falhou também é
    um snapshot — **não verificável**, com a causa —, e `verify` o trata como tal: fail closed
    sem exigir que quem chama trate exceção de IO.
    """

    __slots__ = ("_consumed", "_failure", "_state")
    _state: MainTreeState | None
    _failure: VerificationFailure | None
    _consumed: bool

    def __init__(self, state: MainTreeState | VerificationFailure, *, _capture: object) -> None:
        if _capture is not _CAPTURE:
            raise TypeError("MainTreeSnapshot só nasce de PostExecutionVerifier.capture_main_tree")
        if isinstance(state, MainTreeState):
            object.__setattr__(self, "_state", state)
            object.__setattr__(self, "_failure", None)
        elif isinstance(state, VerificationFailure):
            object.__setattr__(self, "_state", None)
            object.__setattr__(self, "_failure", state)
        else:
            raise TypeError("state precisa ser MainTreeState ou VerificationFailure")
        object.__setattr__(self, "_consumed", False)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("MainTreeSnapshot é imutável")

    @property
    def verifiable(self) -> bool:
        return self._state is not None

    @property
    def failure(self) -> VerificationFailure | None:
        return self._failure

    def _take(self) -> MainTreeState | VerificationFailure:
        """Consome o snapshot: um "antes" vale para uma única verificação."""
        if self._consumed:
            raise ValueError("este MainTreeSnapshot já foi usado numa verificação")
        object.__setattr__(self, "_consumed", True)
        if self._state is not None:
            return self._state
        assert self._failure is not None
        return self._failure

    def __repr__(self) -> str:  # nunca caminho absoluto
        if self._state is not None:
            return "MainTreeSnapshot(<verifiable>)"
        assert self._failure is not None
        return f"MainTreeSnapshot(<unverifiable: {self._failure.value}>)"


def _blocked(
    status: PostExecutionStatus, reason: str, failure: VerificationFailure | None = None
) -> PostExecutionResult:
    return PostExecutionResult(status=status, reason=reason, failure=failure)


def _require_cancel(is_cancelled: object) -> Callable[[], bool]:
    if not callable(is_cancelled):
        raise TypeError("is_cancelled precisa ser Callable[[], bool]")
    return is_cancelled


class PostExecutionVerifier:
    """Sem estado de run: uma instância de vida da aplicação serve a todos os runs.

    ``clock`` existe para os testes (prazo); em produção é `time.monotonic`.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        if not callable(clock):
            raise TypeError("clock precisa ser callable")
        self._clock = clock

    def __repr__(self) -> str:
        return "<PostExecutionVerifier>"

    def _budget(self, is_cancelled: Callable[[], bool]) -> VerificationBudget:
        return VerificationBudget(is_cancelled=is_cancelled, clock=self._clock)

    def capture_main_tree(
        self, local_path: str, *, is_cancelled: Callable[[], bool]
    ) -> MainTreeSnapshot:
        """O estado do repositório principal de ``local_path`` (raiz ou subdiretório), sob um
        orçamento próprio e completo."""
        if not isinstance(local_path, str) or not local_path or "\x00" in local_path:
            raise ValueError("local_path precisa ser um caminho não vazio")
        budget = self._budget(_require_cancel(is_cancelled))
        return MainTreeSnapshot(main_tree_state(local_path, budget), _capture=_CAPTURE)

    def verify(
        self,
        *,
        before: MainTreeSnapshot,
        workspace_path: str,
        workspace_prefix: str,
        root_identity: ObjectIdentity,
        base_commit: str,
        policy: SafetyPolicy,
        is_cancelled: Callable[[], bool],
    ) -> PostExecutionResult:
        """Árvore principal antes × depois, e depois a worktree. Só leitura; nunca lança por IO.

        ``workspace_path``/``workspace_prefix``/``root_identity``/``base_commit`` são os do
        binding do run (`ResolvedWorkspace`); ``policy`` é a política composta do run. Um único
        orçamento cobre a verificação inteira.
        """
        if not isinstance(before, MainTreeSnapshot):
            raise TypeError("before precisa ser o MainTreeSnapshot capturado antes do run")
        if not isinstance(policy, SafetyPolicy):
            raise TypeError("policy precisa ser SafetyPolicy")
        if not isinstance(root_identity, ObjectIdentity):
            raise TypeError("root_identity precisa ser ObjectIdentity")
        for name, value in (
            ("workspace_path", workspace_path),
            ("workspace_prefix", workspace_prefix),
            ("base_commit", base_commit),
        ):
            if not isinstance(value, str) or "\x00" in value:
                raise TypeError(f"{name} precisa ser str sem NUL")
        budget = self._budget(_require_cancel(is_cancelled))

        state_before = before._take()
        if isinstance(state_before, VerificationFailure):
            return _blocked(
                PostExecutionStatus.UNVERIFIABLE, MAIN_TREE_UNVERIFIABLE_BEFORE, state_before
            )
        state_after = main_tree_state(state_before.toplevel, budget)
        if isinstance(state_after, VerificationFailure):
            return _blocked(
                PostExecutionStatus.UNVERIFIABLE, MAIN_TREE_UNVERIFIABLE_AFTER, state_after
            )
        if state_after != state_before:
            return _blocked(PostExecutionStatus.OUT_OF_WORKTREE_WRITE, MAIN_TREE_CHANGED)

        try:
            budget.check()
            root = bind_root(workspace_path, expected_identity=root_identity)
            budget.check()
        except BudgetExhausted as exhausted:
            return _blocked(
                PostExecutionStatus.UNVERIFIABLE, exhausted.failure.value, exhausted.failure
            )
        except PathIntegrityViolation:
            return _blocked(PostExecutionStatus.INTEGRITY_VIOLATION, WORKSPACE_ROOT_INTEGRITY)
        except PathOperationFailed:
            return _blocked(PostExecutionStatus.UNVERIFIABLE, WORKSPACE_ROOT_UNAVAILABLE)

        observed = worktree_changes(
            root.path,
            base_commit=base_commit,
            expected_prefix=workspace_prefix,
            main_toplevel=state_before.toplevel,
            main_git_common_dir=state_before.git_common_dir,
            budget=budget,
        )
        if isinstance(observed, VerificationFailure):
            verdict = (
                PostExecutionStatus.INTEGRITY_VIOLATION
                if observed in _INTEGRITY_FAILURES
                else PostExecutionStatus.UNVERIFIABLE
            )
            return _blocked(verdict, observed.value, observed)

        changes = tuple(ChangedPath(change.path, change.kind) for change in observed.changes)
        try:
            findings = tuple(
                finding
                for change in changes
                if (finding := _check_change(change, workspace_prefix, root.path, policy, budget))
                is not None
            )
        except BudgetExhausted as exhausted:
            return _blocked(
                PostExecutionStatus.UNVERIFIABLE, exhausted.failure.value, exhausted.failure
            )
        for verdict, reason in (
            (PostExecutionStatus.UNVERIFIABLE, PATH_INSPECTION_FAILED),
            (PostExecutionStatus.OUTSIDE_WORKSPACE_CHANGE, CHANGE_OUTSIDE_WORKSPACE),
            (PostExecutionStatus.PATH_POLICY_DENIED, PostExecutionStatus.PATH_POLICY_DENIED.value),
        ):
            if any(finding.status is verdict for finding in findings):
                return PostExecutionResult(
                    status=verdict, reason=reason, changes=changes, findings=findings
                )
        return _verified(changes, budget)


def _verified(changes: tuple[ChangedPath, ...], budget: VerificationBudget) -> PostExecutionResult:
    """O **único** construtor de `VERIFIED` (E8.3-FINAL-001).

    Revalida o orçamento imediatamente antes de liberar: um prazo que expirou ou um
    cancelamento que chegou durante a **última** operação (inspeção, Git, leitura) nunca termina
    em `VERIFIED` — vira `UNVERIFIABLE` com a causa estruturada, mesmo que tudo antes tenha
    passado e nada tenha mudado.
    """
    try:
        budget.check()
    except BudgetExhausted as exhausted:
        return _blocked(
            PostExecutionStatus.UNVERIFIABLE, exhausted.failure.value, exhausted.failure
        )
    return PostExecutionResult(status=PostExecutionStatus.VERIFIED, reason=None, changes=changes)


def _check_change(
    change: ChangedPath, prefix: str, root: str, policy: SafetyPolicy, budget: VerificationBudget
) -> PathFinding | None:
    """Um caminho mudado: dentro do prefixo e permitido como **escrita** mediada?"""
    budget.check()
    if prefix and not change.path.startswith(prefix):
        return PathFinding(
            change.path,
            change.kind,
            PostExecutionStatus.OUTSIDE_WORKSPACE_CHANGE,
            CHANGE_OUTSIDE_WORKSPACE,
        )
    relative = change.path[len(prefix) :]
    syntax = prevalidate_mediated_path(relative, policy=policy)
    if not syntax.allow:
        return PathFinding(
            change.path, change.kind, PostExecutionStatus.PATH_POLICY_DENIED, syntax.rule_id
        )
    facts = inspect(relative, root, tolerate_absent_tail=True)
    budget.check()  # a inspeção toca o disco: prazo/cancelamento que cruzaram nela valem aqui
    if facts.inspection_error is not None:
        return PathFinding(
            change.path, change.kind, PostExecutionStatus.UNVERIFIABLE, PATH_INSPECTION_FAILED
        )
    decision = decide_path(facts, policy=policy, intent=PathIntent.WRITE)
    if not decision.allow:
        return PathFinding(
            change.path, change.kind, PostExecutionStatus.PATH_POLICY_DENIED, decision.rule_id
        )
    return None


__all__ = [
    "CHANGE_OUTSIDE_WORKSPACE",
    "MAIN_TREE_CHANGED",
    "MAIN_TREE_UNVERIFIABLE_AFTER",
    "MAIN_TREE_UNVERIFIABLE_BEFORE",
    "PATH_INSPECTION_FAILED",
    "WORKSPACE_ROOT_INTEGRITY",
    "WORKSPACE_ROOT_UNAVAILABLE",
    "ChangedPath",
    "MainTreeSnapshot",
    "PathFinding",
    "PostExecutionResult",
    "PostExecutionStatus",
    "PostExecutionVerifier",
    "VerificationFailure",
]
