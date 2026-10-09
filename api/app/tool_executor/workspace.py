"""Binding da execution workspace (E7.5-A): `WorkspaceResolver`, `ResolvedWorkspace`.

O provider recebe um `ExecutionWorkspaceRef` — sem caminho de filesystem ([05] §5). Quem traduz
essa referência no diretório físico é uma **porta interna** do `tool_executor`, injetada na
factory: o provider nunca vê o `ResolvedWorkspace`, `tool_executor` nunca consulta o banco, e
não existe registry global implícito.

## A chave do binding é `(workspace_ref, run_scope)`, não só a ref

O mesmo identificador de workspace nunca pode, por acidente, resolver a worktree de **outro**
run ou task: o resolvedor recebe a ref **e** o `RunScope`, e a factory ainda confere que o que
voltou é exatamente deste run (defesa contra um resolvedor errado).

Nesta etapa só existe o resolvedor em memória (`InMemoryWorkspaceResolver`), com binding
**explícito**, instanciado por quem o usa. A ligação concreta a partir do `WorktreeOutcome` da
E7.4 é integração da E8.

## Uma só conferência do binding (E8.3)

`bind_workspace` é a conferência que a factory fazia inline — kind, resolução pela chave
`(workspace_ref, run_scope)`, tipo do que voltou, `base_commit`, workspace/task/run e a raiz
vinculada (`bind_root` com a identidade registrada). Ela foi extraída **sem mudar a ordem nem
os códigos** para que o `TestRunner` (E8.3), que também precisa achar a worktree **deste** run,
use exatamente a mesma regra em vez de uma segunda implementação que pudesse divergir.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Protocol

from app.path_runtime import BoundRoot, PathIntegrityViolation, PathOperationFailed, bind_root
from app.safety.paths import PathForm, classify_path_form
from app.safety.types import ObjectIdentity
from app.tool_executor.contracts import ExecutionWorkspaceRef, RunScope, WorkspaceKind
from app.tool_executor.reasons import rule_for_integrity
from app.tool_executor.validation import (
    ContractViolation,
    require_instance,
    require_text,
)

_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


class ToolExecutorError(Exception):
    """Falha de **criação** de executor. A mensagem é só um código — nunca um caminho."""

    def __init__(self, code: str, *, rule_id: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.rule_id = rule_id


class UnsupportedWorkspace(ToolExecutorError):
    """`kind` que a V1 não implementa (`REMOTE`)."""


class WorkspaceUnavailable(ToolExecutorError):
    """Não existe binding utilizável: ausente, de outro run, de outro commit ou raiz alterada."""


def _require_prefix(value: object) -> str:
    text = require_text("workspace_prefix", value, allow_empty=True)
    if not text:
        return text
    if not text.endswith("/") or "\\" in text:
        raise ContractViolation("workspace_prefix precisa terminar em `/` e usar só `/`")
    for part in text[:-1].split("/"):
        if part in ("", ".", "..") or part.casefold() == ".git":
            raise ContractViolation("workspace_prefix com componente inválido")
    return text


@dataclass(frozen=True, slots=True)
class ResolvedWorkspace:
    """O diretório físico de **um** run. Interno: o provider **nunca** o vê.

    `workspace_path` é onde o workspace da task vive **dentro** da worktree
    (`WorktreeOutcome.workspace_path`); `workspace_prefix` é o prefixo dele no repositório
    (`""` na raiz, ou `"ws/"`), para o Git mediado. `root_identity` é a identidade confiável
    registrada quando o binding foi criado.
    """

    workspace_id: str
    task_id: str
    run_id: str
    base_commit: str
    workspace_path: str
    root_identity: ObjectIdentity
    workspace_prefix: str = ""

    def __post_init__(self) -> None:
        require_text("workspace_id", self.workspace_id)
        require_text("task_id", self.task_id)
        require_text("run_id", self.run_id)
        if not _COMMIT.fullmatch(require_text("base_commit", self.base_commit)):
            raise ContractViolation("base_commit precisa ser um sha1/sha256 git em hex minúsculo")
        path = require_text("workspace_path", self.workspace_path)
        if classify_path_form(path) is not PathForm.ABSOLUTE_QUALIFIED:
            raise ContractViolation("workspace_path precisa ser caminho absoluto")
        require_instance("root_identity", self.root_identity, ObjectIdentity)
        _require_prefix(self.workspace_prefix)

    def __repr__(self) -> str:  # nunca vazar o caminho em log/traceback
        return "ResolvedWorkspace(<binding>)"


class WorkspaceResolver(Protocol):
    """Porta interna: `(workspace_ref, run_scope)` → binding físico, ou `None` se não há.

    Nunca recebe um identificador isolado do run. Não consulta registry global e não é
    chamada pelo provider.
    """

    def resolve(
        self, workspace_ref: ExecutionWorkspaceRef, run_scope: RunScope
    ) -> ResolvedWorkspace | None: ...


class BindingRevoked(ValueError):
    """A chave `(workspace, task, run)` foi revogada: ela não volta a ser registrada."""


class InMemoryWorkspaceResolver:
    """Resolvedor com binding **explícito**, em memória. Uma instância por quem a cria.

    A chave é `(workspace_id, task_id, run_id)`: o mesmo `workspace_id` ligado ao run A não
    resolve para o run B. Não há instância global nem lookup implícito.

    ## Remover não é revogar (E842-AUD-002)

    `unbind`/`unbind_exact` só **apagam** a entrada; quem depende de "a remoção funcionou" para
    negar acesso fica autorizado quando ela não acontece (o `False` de `unbind_exact` era só um
    relato). `revoke` é a operação de autorização: grava a chave em `_revoked` **antes** de tentar
    qualquer remoção, e `resolve` e `bind` consultam esse conjunto sob o mesmo lock. A negação
    portanto vale na própria resolução, mesmo que a entrada continue fisicamente guardada, e não
    é desfeita por `unbind`, `unbind_exact`, novo `bind` ou reuso do mesmo Run. A revogação é da
    **chave** (um Run); nenhum outro Run é afetado. Um `ResolvedWorkspace` já entregue por
    `resolve` antes da revogação não é retomado — o escopo é impedir resoluções futuras.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bindings: dict[tuple[str, str, str], ResolvedWorkspace] = {}
        self._revoked: set[tuple[str, str, str]] = set()

    @staticmethod
    def _key(workspace_id: str, task_id: str, run_id: str) -> tuple[str, str, str]:
        return (workspace_id, task_id, run_id)

    def bind(self, resolved: ResolvedWorkspace) -> None:
        require_instance("resolved", resolved, ResolvedWorkspace)
        key = self._key(resolved.workspace_id, resolved.task_id, resolved.run_id)
        with self._lock:
            if key in self._revoked:
                raise BindingRevoked("a chave deste (workspace, task, run) foi revogada")
            if key in self._bindings:
                raise ValueError("binding já existe para este (workspace, task, run)")
            self._bindings[key] = resolved

    def unbind(self, workspace_id: str, task_id: str, run_id: str) -> None:
        with self._lock:
            self._bindings.pop(self._key(workspace_id, task_id, run_id), None)

    def unbind_exact(self, resolved: ResolvedWorkspace) -> bool:
        """Remove o binding **só se** ele ainda é este objeto (E8.4.2): a compensação de quem o
        criou nunca derruba um binding registrado por outro. `True` = removido agora."""
        key = self._key(resolved.workspace_id, resolved.task_id, resolved.run_id)
        with self._lock:
            if self._bindings.get(key) is not resolved:
                return False
            self._drop(key)
            return True

    def _drop(self, key: tuple[str, str, str]) -> None:
        """A remoção física. Chamada com o lock tomado; a autorização não depende dela."""
        del self._bindings[key]

    def revoke(self, resolved: ResolvedWorkspace) -> bool:
        """Revoga a autorização deste binding **para sempre** (nesta instância). `True` = a
        resolução desta chave está negada.

        Seletiva por token: se a chave guarda **outro** objeto (o deste já saiu e alguém registrou
        um novo), nada é revogado e devolve `False` — o binding de outro não é nosso. Ausente ou
        igual ao token: a chave é revogada primeiro e só então a entrada é apagada (se o `_drop`
        falhar, a negação continua valendo em `resolve`).
        """
        require_instance("resolved", resolved, ResolvedWorkspace)
        key = self._key(resolved.workspace_id, resolved.task_id, resolved.run_id)
        with self._lock:
            stored = self._bindings.get(key)
            if stored is not None and stored is not resolved:
                return False
            self._revoked.add(key)
            if stored is not None:
                self._drop(key)
            return True

    def resolve(
        self, workspace_ref: ExecutionWorkspaceRef, run_scope: RunScope
    ) -> ResolvedWorkspace | None:
        key = self._key(workspace_ref.id, run_scope.task_id, run_scope.run_id)
        with self._lock:
            if key in self._revoked:
                return None
            return self._bindings.get(key)


@dataclass(frozen=True, slots=True)
class BoundWorkspace:
    """O binding conferido de **um** run e a raiz já vinculada. Interno, como o resolvido."""

    resolved: ResolvedWorkspace
    root: BoundRoot

    def __repr__(self) -> str:  # nunca vazar o caminho em log/traceback
        return "BoundWorkspace(<binding>)"


def bind_workspace(
    resolver: WorkspaceResolver, workspace_ref: ExecutionWorkspaceRef, run_scope: RunScope
) -> BoundWorkspace:
    """`(workspace_ref, run_scope)` → binding conferido e raiz vinculada, ou levanta. Fail closed.

    1. `kind`: `REMOTE` é recusado na V1 (`UnsupportedWorkspace`) **antes** do resolvedor;
    2. `resolver.resolve(workspace_ref, run_scope)` — a chave inclui o run, nunca só a ref;
    3. o binding devolvido é conferido contra a ref **e** o run (tipo, `base_commit`,
       workspace, task, run) — defesa contra um resolvedor errado;
    4. `bind_root` — a raiz existe, é diretório, sem reparse e com a identidade registrada.

    Os códigos são os mesmos que a factory sempre usou. Nada aqui consulta banco nem registry.
    """
    require_instance("workspace_ref", workspace_ref, ExecutionWorkspaceRef)
    require_instance("run_scope", run_scope, RunScope)
    if workspace_ref.kind is not WorkspaceKind.LOCAL_WORKTREE:
        raise UnsupportedWorkspace("workspace_kind_unsupported")

    resolved = resolver.resolve(workspace_ref, run_scope)
    if resolved is None:
        raise WorkspaceUnavailable("workspace_unbound")
    if not isinstance(resolved, ResolvedWorkspace):
        raise WorkspaceUnavailable("resolver_invalid")
    if resolved.base_commit != workspace_ref.base_commit:
        raise WorkspaceUnavailable("base_commit_mismatch")
    if (
        resolved.workspace_id != workspace_ref.id
        or resolved.task_id != run_scope.task_id
        or resolved.run_id != run_scope.run_id
    ):
        raise WorkspaceUnavailable("binding_mismatch")

    try:
        root = bind_root(resolved.workspace_path, expected_identity=resolved.root_identity)
    except PathIntegrityViolation as violation:
        raise WorkspaceUnavailable(
            "root_unavailable", rule_id=rule_for_integrity(violation.category)
        ) from None
    except PathOperationFailed:
        raise WorkspaceUnavailable("root_unavailable") from None
    return BoundWorkspace(resolved=resolved, root=root)


class RunWorkspaceBindings:
    """Binding **por Run** a partir de dados neutros (E8.4.2). Adaptador concreto da porta que o
    `orchestrator.execution_workspace` recebe por injeção — sem importar o Orchestrator.

    Cada registro é um `ResolvedWorkspace` com o `run_id` **daquele** Run, conferido logo depois
    pela regra canônica (`bind_workspace`: chave `(workspace_ref, run_scope)`, base, identidades e
    `bind_root` com a identidade esperada). Conferência que falha desfaz o próprio registro antes
    de levantar. O token devolvido por `bind_run` é o que `unbind_run` aceita: só o binding criado
    por quem o recebeu é removido. Unbind **não** é teardown — a worktree continua no disco.
    """

    def __init__(self, resolver: InMemoryWorkspaceResolver | None = None) -> None:
        self._resolver = resolver if resolver is not None else InMemoryWorkspaceResolver()

    def __repr__(self) -> str:
        return "RunWorkspaceBindings(<bindings>)"

    @property
    def resolver(self) -> InMemoryWorkspaceResolver:
        """O resolvedor que a factory e o Test Runner desta composição recebem."""
        return self._resolver

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
    ) -> object:
        """Registra e confere o binding do Run. Levanta `ToolExecutorError`/`ContractViolation`."""
        resolved = ResolvedWorkspace(
            workspace_id=workspace_id,
            task_id=task_id,
            run_id=run_id,
            base_commit=base_commit,
            workspace_path=workspace_path,
            root_identity=root_identity,
            workspace_prefix=workspace_prefix,
        )
        try:
            self._resolver.bind(resolved)
        except BindingRevoked:
            raise WorkspaceUnavailable("binding_revoked") from None
        except ValueError:
            raise WorkspaceUnavailable("binding_exists") from None
        try:
            self.verify_run(
                workspace_id=workspace_id,
                task_id=task_id,
                run_id=run_id,
                invocation_id=invocation_id,
                base_commit=base_commit,
            )
        except BaseException:
            # Registro que não passou na conferência canônica nunca foi autorizado: revoga.
            self._resolver.revoke(resolved)
            raise
        return resolved

    def verify_run(
        self,
        *,
        workspace_id: str,
        task_id: str,
        run_id: str,
        invocation_id: str,
        base_commit: str,
    ) -> None:
        """A mesma conferência da factory/Test Runner para este Run. Levanta se não resolve."""
        bind_workspace(
            self._resolver,
            ExecutionWorkspaceRef(
                kind=WorkspaceKind.LOCAL_WORKTREE, id=workspace_id, base_commit=base_commit
            ),
            RunScope(task_id=task_id, run_id=run_id, invocation_id=invocation_id),
        )

    def unbind_run(self, token: object) -> bool:
        """Libera o binding criado por `bind_run` que devolveu ``token`` (fim normal de uso).
        Nunca outro. **Não** revoga: o Run pode ser registrado de novo."""
        if not isinstance(token, ResolvedWorkspace):
            return False
        return self._resolver.unbind_exact(token)

    def revoke_run(self, token: object) -> bool:
        """Revoga o binding de ``token`` (recusa por interrupção, compensação). `True` = a
        resolução deste Run está **negada**, ainda que a remoção física não se prove; o Run não
        volta a ser registrado nesta instância. Nunca revoga o binding de outro."""
        if not isinstance(token, ResolvedWorkspace):
            return False
        return self._resolver.revoke(token)


__all__ = [
    "BindingRevoked",
    "BoundWorkspace",
    "InMemoryWorkspaceResolver",
    "ResolvedWorkspace",
    "RunWorkspaceBindings",
    "ToolExecutorError",
    "UnsupportedWorkspace",
    "WorkspaceResolver",
    "WorkspaceUnavailable",
    "bind_workspace",
]
