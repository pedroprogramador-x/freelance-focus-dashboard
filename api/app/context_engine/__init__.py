"""Context Engine — o Context Registry e a verificação de *staleness* (E4).

[01](../../../docs/architecture/01-v1-architecture.md) §2 + regra de módulo da E4:

* **pode importar** `db`, `git_runtime` (só leitura), `path_runtime`, `safety`, `config`;
* **não pode importar** `agent_runtime`, `tool_executor`, `orchestrator`, `api`.

`subprocess` continua confinado a `app/git_runtime/`: qualquer leitura nova de git entra
como função nova **lá**, nunca aqui. `test_architecture.py` transforma as duas regras em
falha de suíte.

`source_ref_expansion` é o **dono único da gramática de glob** de todo o sistema. Nenhuma
segunda implementação de casamento de padrão de `source_ref` existe em lugar nenhum.
"""

from app.context_engine.content_hash import (
    CONTENT_HASH_VERSION,
    compute_content_hash,
    normalize_text,
)
from app.context_engine.errors import (
    ContextEntryNotFound,
    ContextError,
    InvalidContextEntry,
    InvalidSourceRefs,
)
from app.context_engine.service import (
    UNSET,
    create_entry,
    delete_entry,
    get_entry,
    import_planning_seed,
    list_entries,
    update_entry,
)
from app.context_engine.source_ref_expansion import (
    CompiledSourceRef,
    ExpansionStatus,
    SourceRefExpansion,
    SourceRefMatcher,
    build_matcher,
    compile_source_ref,
    compute_source_hash,
    expand_against_commit,
    expand_source_refs,
)
from app.context_engine.types import PlanningSeed, PlanningSeedDecision, PlanningSeedRisk
from app.context_engine.verification import (
    FreshnessOutcome,
    WorkspaceTreeSnapshot,
    capture_verification_commit,
    evaluate_freshness,
    read_workspace_tree,
    verify_freshness,
    verify_workspace_entries,
)

__all__ = [
    "CONTENT_HASH_VERSION",
    "UNSET",
    "CompiledSourceRef",
    "ContextEntryNotFound",
    "ContextError",
    "ExpansionStatus",
    "FreshnessOutcome",
    "InvalidContextEntry",
    "InvalidSourceRefs",
    "PlanningSeed",
    "PlanningSeedDecision",
    "PlanningSeedRisk",
    "SourceRefExpansion",
    "SourceRefMatcher",
    "WorkspaceTreeSnapshot",
    "build_matcher",
    "capture_verification_commit",
    "compile_source_ref",
    "compute_content_hash",
    "compute_source_hash",
    "create_entry",
    "delete_entry",
    "evaluate_freshness",
    "expand_against_commit",
    "expand_source_refs",
    "get_entry",
    "import_planning_seed",
    "list_entries",
    "normalize_text",
    "read_workspace_tree",
    "update_entry",
    "verify_freshness",
    "verify_workspace_entries",
]
