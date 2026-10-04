"""Limites V1 do `ToolExecutor` (E7.5-A): constantes de **release**, puras e imutáveis.

Decisão de Pedro (E7.5): estes valores **não** são configuração por workspace, **não** entram
em `SafetyPolicy.as_canonical` e **não** mudam o `execution_fingerprint` — um valor aqui muda
com a versão do código, nunca por política de workspace. Alterá-los exige decisão
arquitetural explícita (e, se um dia forem configuráveis, entrar na `SafetyPolicy` com bump
de versão do hash).

Dois tipos de limite, tratados de forma diferente pelo executor (E7.5-B em diante):

* **Entrada/arquivo inteiro** (`ReadFile`, `GitShow(path)`, `WriteFile`, `ApplyPatch`): acima
  do teto a operação é **negada** (`limit_exceeded`). Nunca se trunca um arquivo inteiro e se
  entrega o pedaço como se fosse o todo.
* **Listagem/busca/saída Git**: o resultado entregue ao provider pode ser truncado com
  marcador explícito.

`git_internal_capture_bytes` **não** é saída para o provider: é o teto de captura do que o
runtime lê do processo Git para **parsear**. Saída estrutural interna truncada nunca é
parseada — vira erro/`UNVERIFIABLE`.
"""

from __future__ import annotations

from dataclasses import dataclass

_KIB = 1024
_MIB = 1024 * _KIB


@dataclass(frozen=True, slots=True)
class ToolLimits:
    # ReadFile / GitShow(path)
    read_file_bytes: int = 256 * _KIB
    git_show_path_bytes: int = 256 * _KIB

    # WriteFile (UTF-8)
    write_file_bytes: int = 1 * _MIB

    # ApplyPatch
    apply_patch_input_bytes: int = 512 * _KIB
    apply_patch_max_files: int = 50
    apply_patch_max_hunks: int = 500
    apply_patch_result_file_bytes: int = 1 * _MIB

    # ListDirectory
    list_directory_entries: int = 1000

    # SearchText
    search_query_chars: int = 256
    search_max_matches: int = 200
    search_snippet_chars: int = 512
    search_file_bytes: int = 1 * _MIB
    search_max_files: int = 5000
    search_deadline_s: int = 20

    # Saída entregue ao provider
    git_result_bytes: int = 256 * _KIB
    tool_result_content_bytes: int = 256 * _KIB

    # Captura INTERNA de processo Git (não é saída do provider)
    git_internal_capture_bytes: int = 4 * _MIB


#: A única instância normativa da V1.
TOOL_LIMITS_V1 = ToolLimits()

__all__ = ["TOOL_LIMITS_V1", "ToolLimits"]
