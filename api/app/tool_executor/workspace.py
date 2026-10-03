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
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Protocol

from app.safety.paths import PathForm, classify_path_form
from app.safety.types import ObjectIdentity
from app.tool_executor.contracts import ExecutionWorkspaceRef, RunScope
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


class InMemoryWorkspaceResolver:
    """Resolvedor com binding **explícito**, em memória. Uma instância por quem a cria.

    A chave é `(workspace_id, task_id, run_id)`: o mesmo `workspace_id` ligado ao run A não
    resolve para o run B. Não há instância global nem lookup implícito.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bindings: dict[tuple[str, str, str], ResolvedWorkspace] = {}

    @staticmethod
    def _key(workspace_id: str, task_id: str, run_id: str) -> tuple[str, str, str]:
        return (workspace_id, task_id, run_id)

    def bind(self, resolved: ResolvedWorkspace) -> None:
        require_instance("resolved", resolved, ResolvedWorkspace)
        key = self._key(resolved.workspace_id, resolved.task_id, resolved.run_id)
        with self._lock:
            if key in self._bindings:
                raise ValueError("binding já existe para este (workspace, task, run)")
            self._bindings[key] = resolved

    def unbind(self, workspace_id: str, task_id: str, run_id: str) -> None:
        with self._lock:
            self._bindings.pop(self._key(workspace_id, task_id, run_id), None)

    def resolve(
        self, workspace_ref: ExecutionWorkspaceRef, run_scope: RunScope
    ) -> ResolvedWorkspace | None:
        key = self._key(workspace_ref.id, run_scope.task_id, run_scope.run_id)
        with self._lock:
            return self._bindings.get(key)


__all__ = [
    "InMemoryWorkspaceResolver",
    "ResolvedWorkspace",
    "ToolExecutorError",
    "UnsupportedWorkspace",
    "WorkspaceResolver",
    "WorkspaceUnavailable",
]
