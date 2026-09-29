"""Contratos de ferramenta do Developer (E7.2): **só tipos**, nenhum executor.

Fontes congeladas: [04] §2–§3 e [05] §1, §4–§5. O executor concreto é a E7.5; aqui existe
apenas o vocabulário que `agent_runtime` e o futuro executor compartilham. Por isso vive em
`tool_executor/`: `agent_runtime → tool_executor` é a direção permitida ([01] §3) e o inverso
criaria ciclo.

## A superfície é fechada

`ToolRequest` é a união das **nove** operações de [04] §2. Não existe `ExecCommand`,
`ShellCommand` nem equivalente, e nenhum campo aqui carrega `argv`: para `Git*` o provider
nomeia a operação e passa parâmetros tipados; o `argv` é formado pelo runtime (E7.5).
`required_capability` amarra cada operação à capability de [04] §2; um teste trava as nove.

## O que estes DTOs não decidem

Se um caminho é permitido, se o conteúdo vaza segredo, se a capability está concedida: nada
disso é decidido aqui. A validação é estrutural (tipo, vazio, NUL, ref que pareça opção de
linha de comando). `ExecutionWorkspaceRef` não tem caminho de filesystem ([05] §5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Protocol

from app.safety.capability_profile import Capability, ProviderCapabilityProfile
from app.safety.policy import SafetyPolicy
from app.tool_executor.validation import (
    ContractViolation,
    require_instance,
    require_int,
    require_optional_text,
    require_text,
    require_text_tuple,
)

# ------------------------------------------------------------------ workspace e escopo


class WorkspaceKind(str, Enum):
    """[05] §5. A V1 implementa apenas `local_worktree`; `remote` é só representável."""

    LOCAL_WORKTREE = "local_worktree"
    REMOTE = "remote"


_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


@dataclass(frozen=True, slots=True)
class ExecutionWorkspaceRef:
    """[05] §5. **Sem caminho de filesystem** — `Run.worktree_path` é só diagnóstico local."""

    kind: WorkspaceKind
    id: str
    base_commit: str
    capabilities_hint: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_instance("kind", self.kind, WorkspaceKind)
        require_text("id", self.id)
        if not _COMMIT.fullmatch(require_text("base_commit", self.base_commit)):
            raise ContractViolation("base_commit precisa ser um sha1/sha256 git em hex minúsculo")
        require_text_tuple("capabilities_hint", self.capabilities_hint)


@dataclass(frozen=True, slots=True)
class RunScope:
    """Metadados de task/run de [05] §2, com que a factory vincula o executor a **um** run."""

    task_id: str
    run_id: str
    invocation_id: str

    def __post_init__(self) -> None:
        require_text("task_id", self.task_id)
        require_text("run_id", self.run_id)
        require_text("invocation_id", self.invocation_id)


# ------------------------------------------------------------------------ operações


def _path(name: str, value: object) -> str:
    """Texto não vazio, sem NUL. Legalidade do caminho é da `SafetyPolicy`/`PathRuntime`."""
    return require_text(name, value)


def _ref(name: str, value: object) -> str:
    """Uma ref git tipada: sem espaço, sem ler-se como opção (`--output=...`) e **sem `:`**.

    `:` é a sintaxe `<rev>:<caminho>` (`HEAD:.env`, `:/.env`) do git: uma ref que carregasse
    caminho contornaria a checagem de `path` pela `SafetyPolicy`. Nome de ref git válido
    nunca contém `:` (`git check-ref-format`), então nada legítimo é perdido.
    """
    text = require_text(name, value)
    if text.startswith("-") or any(char.isspace() for char in text):
        raise ContractViolation(f"{name} não pode começar com `-` nem conter espaço")
    if ":" in text:
        raise ContractViolation(f"{name} não pode conter `:` (sintaxe rev:caminho)")
    return text


@dataclass(frozen=True, slots=True)
class ReadFile:
    path: str

    def __post_init__(self) -> None:
        _path("path", self.path)


@dataclass(frozen=True, slots=True)
class ListDirectory:
    path: str

    def __post_init__(self) -> None:
        _path("path", self.path)


@dataclass(frozen=True, slots=True)
class SearchText:
    """Busca implementada pelo runtime, não por `grep` externo ([04] §2)."""

    query: str
    path: str | None = None

    def __post_init__(self) -> None:
        require_text("query", self.query)
        if self.path is not None:
            _path("path", self.path)


@dataclass(frozen=True, slots=True)
class WriteFile:
    """Conteúdo integral ([04] §2)."""

    path: str
    content: str

    def __post_init__(self) -> None:
        _path("path", self.path)
        require_text("content", self.content, allow_empty=True)


@dataclass(frozen=True, slots=True)
class ApplyPatch:
    """Diff unificado aplicado pelo runtime ([04] §2)."""

    patch: str

    def __post_init__(self) -> None:
        require_text("patch", self.patch)


@dataclass(frozen=True, slots=True)
class GitStatus:
    pass


@dataclass(frozen=True, slots=True)
class GitDiff:
    ref: str | None = None
    path: str | None = None

    def __post_init__(self) -> None:
        if self.ref is not None:
            _ref("ref", self.ref)
        if self.path is not None:
            _path("path", self.path)


@dataclass(frozen=True, slots=True)
class GitShow:
    ref: str
    path: str | None = None

    def __post_init__(self) -> None:
        _ref("ref", self.ref)
        if self.path is not None:
            _path("path", self.path)


@dataclass(frozen=True, slots=True)
class GitListTree:
    ref: str | None = None
    path: str | None = None

    def __post_init__(self) -> None:
        if self.ref is not None:
            _ref("ref", self.ref)
        if self.path is not None:
            _path("path", self.path)


#: A superfície fechada de [04] §2. Adicionar uma operação exige reabrir a decisão.
ToolRequest = (
    ReadFile
    | ListDirectory
    | SearchText
    | WriteFile
    | ApplyPatch
    | GitStatus
    | GitDiff
    | GitShow
    | GitListTree
)

TOOL_OPERATIONS: tuple[type, ...] = (
    ReadFile,
    ListDirectory,
    SearchText,
    WriteFile,
    ApplyPatch,
    GitStatus,
    GitDiff,
    GitShow,
    GitListTree,
)

_CAPABILITY_OF: MappingProxyType[type, Capability] = MappingProxyType(
    {
        ReadFile: Capability.READ_FILES,
        ListDirectory: Capability.READ_FILES,
        SearchText: Capability.READ_FILES,
        WriteFile: Capability.WRITE_FILES,
        ApplyPatch: Capability.WRITE_FILES,
        GitStatus: Capability.GIT_READ,
        GitDiff: Capability.GIT_READ,
        GitShow: Capability.GIT_READ,
        GitListTree: Capability.GIT_READ,
    }
)


def required_capability(request: ToolRequest) -> Capability:
    """A capability que [04] §2 associa à operação. Levanta para tipo fora da união."""
    try:
        return _CAPABILITY_OF[type(request)]
    except KeyError:
        raise ContractViolation(
            f"{type(request).__name__} não é uma operação da superfície fechada"
        ) from None


# --------------------------------------------------------------------------- resultado


class ToolStatus(str, Enum):
    OK = "ok"
    #: Negação explícita ([04] §2): "não há caminho alternativo".
    DENIED = "denied"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ToolResult:
    """`content` só existe em `ok`; `reason` só em `denied`/`error` (e já redigido)."""

    status: ToolStatus
    operation: str
    content: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        require_instance("status", self.status, ToolStatus)
        require_text("operation", self.operation)
        require_optional_text("reason", self.reason)
        if self.content is not None:
            require_text("content", self.content, allow_empty=True)
        if self.status is ToolStatus.OK:
            if self.reason is not None:
                raise ContractViolation("resultado ok não carrega reason")
        else:
            if self.content is not None:
                raise ContractViolation("resultado negado ou com erro não carrega content")
            if self.reason is None:
                raise ContractViolation("negação e erro precisam de reason explícito")


@dataclass(frozen=True, slots=True)
class MediatedUsage:
    """Medição do executor. `files_read` é `tuple` — toda leitura é mediada, então o
    executor **sabe** ([04] §2): `files_read_source = reported`, nunca ausência."""

    files_read: tuple[str, ...]
    operations: int
    denials: int

    def __post_init__(self) -> None:
        require_text_tuple("files_read", self.files_read)
        require_int("operations", self.operations)
        require_int("denials", self.denials)
        if self.denials > self.operations:
            raise ContractViolation("denials não pode exceder operations")


# --------------------------------------------------------------------------- protocolos


class MediatedTools(Protocol):
    """O que o provider recebe: só `execute`, com escopo do run ([05] §2, §4)."""

    def execute(self, request: ToolRequest) -> ToolResult: ...


class ToolExecutor(Protocol):
    """Criado por run pela factory; nunca atravessa runs ([05] §2)."""

    def execute(self, request: ToolRequest) -> ToolResult: ...

    def usage(self) -> MediatedUsage: ...

    def mediated_tools(self) -> MediatedTools: ...


class ToolExecutorFactory(Protocol):
    """Vida da aplicação; injetada pelo composition root ([05] §2)."""

    def create(
        self,
        workspace_ref: ExecutionWorkspaceRef,
        composed_policy: SafetyPolicy,
        effective_capability_profile: ProviderCapabilityProfile,
        run_scope: RunScope,
    ) -> ToolExecutor: ...


__all__ = [
    "TOOL_OPERATIONS",
    "ApplyPatch",
    "ExecutionWorkspaceRef",
    "GitDiff",
    "GitListTree",
    "GitShow",
    "GitStatus",
    "ListDirectory",
    "MediatedTools",
    "MediatedUsage",
    "ReadFile",
    "RunScope",
    "SearchText",
    "ToolExecutor",
    "ToolExecutorFactory",
    "ToolRequest",
    "ToolResult",
    "ToolStatus",
    "WorkspaceKind",
    "WriteFile",
    "required_capability",
]
