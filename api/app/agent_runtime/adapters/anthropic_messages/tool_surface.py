"""A superfície **fechada** de nove *client tools* enviadas à Messages API (E8.2).

Uma lista canônica (`TOOL_SPECS`), uma por operação mediada de [04] §2. Cada definição é uma
ferramenta **definida pela aplicação** — exatamente `{name, description, input_schema}`, sem
`type` (nenhuma *server tool* da Anthropic). O Claude só *devolve* `tool_use`; quem executa é
o dispatcher canônico, via `ToolRequest` oficial → `MediatedTools.execute`.

Não existe ferramenta de comando, de rede ou de filesystem nativo. O schema JSON é só a
primeira barreira: `build_request` revalida tudo em Python e os contratos E7.2/E7.5 decidem
caminho/ref.

O `tool_schema_hash` deriva do documento canônico (nome, descrição, schema, operação e
capability) pela canonicalização oficial — nunca de `repr` de função.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from app.safety.canonical import canonical_sha256
from app.tool_executor.contracts import (
    ApplyPatch,
    GitDiff,
    GitListTree,
    GitShow,
    GitStatus,
    ListDirectory,
    ReadFile,
    SearchText,
    ToolRequest,
    WriteFile,
    required_capability,
)
from app.tool_executor.validation import ContractViolation


class InvalidToolInput(Exception):
    """Entrada de ferramenta fora do contrato. Sem eco do payload na mensagem."""

    def __init__(self) -> None:
        super().__init__("entrada inválida")


#: (nome do parâmetro, obrigatório). Todo parâmetro é `string`.
_Param = tuple[str, bool]

_PROBES: MappingProxyType[type, ToolRequest] = MappingProxyType(
    {
        ReadFile: ReadFile("x"),
        ListDirectory: ListDirectory(),
        SearchText: SearchText("x"),
        WriteFile: WriteFile("x", ""),
        ApplyPatch: ApplyPatch("x"),
        GitStatus: GitStatus(),
        GitDiff: GitDiff(),
        GitShow: GitShow("x"),
        GitListTree: GitListTree(),
    }
)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    request_type: type
    params: tuple[_Param, ...]

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {name: {"type": "string"} for name, _ in self.params},
            "required": [name for name, required in self.params if required],
            "additionalProperties": False,
        }

    def definition(self) -> dict[str, Any]:
        """A *client tool* exatamente como vai à API: três chaves, sem `type`."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema(),
        }

    def capability_value(self) -> str:
        return required_capability(_PROBES[self.request_type]).value


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "ff_read_file",
        "Lê o conteúdo de um arquivo da worktree da task, por caminho relativo.",
        ReadFile,
        (("path", True),),
    ),
    ToolSpec(
        "ff_list_directory",
        "Lista um diretório da worktree. Sem `path`, lista a raiz da worktree.",
        ListDirectory,
        (("path", False),),
    ),
    ToolSpec(
        "ff_search_text",
        "Busca um texto literal nos arquivos da worktree, opcionalmente sob `path`.",
        SearchText,
        (("query", True), ("path", False)),
    ),
    ToolSpec(
        "ff_write_file",
        "Escreve o conteúdo integral de um arquivo da worktree.",
        WriteFile,
        (("path", True), ("content", True)),
    ),
    ToolSpec(
        "ff_apply_patch",
        "Aplica um diff unificado à worktree.",
        ApplyPatch,
        (("patch", True),),
    ),
    ToolSpec(
        "ff_git_status",
        "Estado da worktree em relação ao commit-base (somente leitura).",
        GitStatus,
        (),
    ),
    ToolSpec(
        "ff_git_diff",
        "Diff somente leitura contra `ref` (padrão: commit-base), opcionalmente de um `path`.",
        GitDiff,
        (("ref", False), ("path", False)),
    ),
    ToolSpec(
        "ff_git_show",
        "Mostra o conteúdo de um objeto git em `ref`, opcionalmente de um `path`.",
        GitShow,
        (("ref", True), ("path", False)),
    ),
    ToolSpec(
        "ff_git_list_tree",
        "Lista a árvore git em `ref` (padrão: commit-base), opcionalmente sob `path`.",
        GitListTree,
        (("ref", False), ("path", False)),
    ),
)

TOOL_NAMES: tuple[str, ...] = tuple(spec.name for spec in TOOL_SPECS)
SPEC_BY_NAME: MappingProxyType[str, ToolSpec] = MappingProxyType(
    {spec.name: spec for spec in TOOL_SPECS}
)


def tool_definitions() -> list[dict[str, Any]]:
    """As nove definições canônicas, em ordem canônica (cópias novas a cada chamada)."""
    return [spec.definition() for spec in TOOL_SPECS]


def surface_document(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Documento do hash: cada definição + operação/capability do nome (desconhecido = None)."""
    document = []
    for definition in definitions:
        spec = SPEC_BY_NAME.get(definition.get("name"))  # type: ignore[arg-type]
        document.append(
            {
                "definition": definition,
                "request": None if spec is None else spec.request_type.__name__,
                "capability": None if spec is None else spec.capability_value(),
            }
        )
    return document


def surface_hash(definitions: list[dict[str, Any]]) -> str:
    return canonical_sha256(surface_document(definitions))


def tool_schema_hash() -> str:
    """`tool_schema_hash` da superfície canônica."""
    return surface_hash(tool_definitions())


def build_request(spec: ToolSpec, arguments: object) -> ToolRequest:
    """Valida **exatamente** os campos esperados e constrói o `ToolRequest` oficial.

    `InvalidToolInput` (mensagem fixa, sem eco do payload) para não-objeto, campo ausente,
    campo extra ou valor que não é `str`. Caminho/ref: `ContractViolation` dos contratos.
    """
    if not isinstance(arguments, dict):
        raise InvalidToolInput
    names = {name for name, _ in spec.params}
    required = {name for name, req in spec.params if req}
    if not set(arguments) <= names or not required <= set(arguments):
        raise InvalidToolInput
    if any(type(value) is not str for value in arguments.values()):
        raise InvalidToolInput
    try:
        request: ToolRequest = spec.request_type(**arguments)
    except (ContractViolation, TypeError) as exc:
        raise InvalidToolInput from exc
    return request
