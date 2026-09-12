"""Context Engine — o Context Registry e a verificação de *staleness* (E4).

[01](../../../docs/architecture/01-v1-architecture.md) §2 + regra de módulo da E4:

* **pode importar** `db`, `git_runtime` (só leitura), `path_runtime`, `safety`, `config`;
* **não pode importar** `agent_runtime`, `tool_executor`, `orchestrator`, `api`.

`subprocess` continua confinado a `app/git_runtime/`: qualquer leitura nova de git entra
como função nova **lá**, nunca aqui. `test_architecture.py` transforma as duas regras em
falha de suíte.

`source_ref_expansion` é o **dono único da gramática de glob** de todo o sistema. Nenhuma
segunda implementação de casamento de padrão de `source_ref` existe em lugar nenhum.

A E5 acrescenta o Context Router, com a mesma disciplina de dono único em três pontos:

* `rendering.render_block_text` é a **única** função que produz texto emitido — o corte de
  orçamento mede o texto real, nunca uma estimativa paralela. A redação roda sobre uma
  representação **plana** de título+corpo+`structured` linearizado, antes de qualquer
  formatação estrutural — fecha AUD-002/003/004, onde a serialização JSON de `structured`
  rodando antes da redação escondia segredo em `password: valor`;
* `file_map` é derivado do commit e cacheado por `(workspace_id, base_commit)`
  ([ADR-0006] item 4), nunca uma entrada editável. `encode_path_identity` pré-codifica
  todo campo de identidade de caminho antes de entrar em `canonical_json` — fecha
  AUD-001/006/007, onde a normalização NFC de `canonical_json` colapsava dois arquivos
  Git genuinamente distintos (NFC vs NFD) num único hash;
* `manifest.render_context` grava o artefato em **bytes** (`"wb"`), porque o modo texto do
  Python traduziria a quebra de linha no Windows e o arquivo deixaria de hashear como o
  hash gravado; e revalida o sha256 dos bytes em disco antes de reaproveitar por
  idempotência, reescrevendo se divergir — fecha AUD-009.

Nenhuma rota HTTP nova entra nesta fase: `select_context`, `freeze_manifest` e
`render_context` são capacidades internas, consumidas pelo Orchestrator Planner a partir
da E6.
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
from app.context_engine.file_map import (
    FILE_MAP_VERSION,
    FileMap,
    FileMapItem,
    build_file_map,
    build_file_map_from_tree,
    clear_file_map_cache,
    compute_file_map_hash,
    encode_path_identity,
)
from app.context_engine.manifest import (
    ARTIFACT_STORE_PREFIX,
    MANIFEST_HASH_VERSION,
    RenderedContext,
    build_rendered_payload,
    compute_manifest_hash,
    freeze_manifest,
    render_context,
)
from app.context_engine.rendering import (
    DEFAULT_TRANSFORMATIONS,
    RENDERER_VERSION,
    RenderedBlock,
    Transformation,
    approx_tokens,
    normalize_transformations,
    render_block_text,
)
from app.context_engine.selection import (
    PROXIMITY_MAX,
    REASON_BUDGET,
    REASON_OUT_OF_WORKSPACE,
    REASON_SECRET_POLICY,
    SCORE_AFFECTED_DOMAIN,
    SCORE_EXACT_LITERAL_MATCH,
    SCORE_FRESH,
    SCORE_SOURCE_REF_OVERLAP,
    SCORE_TAG_MATCH,
    ContextSelection,
    ContextTreeUnavailable,
    ExcludedItem,
    ScoreBreakdown,
    ScoredEntry,
    epoch_microseconds,
    select_context,
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
    "ARTIFACT_STORE_PREFIX",
    "CONTENT_HASH_VERSION",
    "DEFAULT_TRANSFORMATIONS",
    "FILE_MAP_VERSION",
    "MANIFEST_HASH_VERSION",
    "PROXIMITY_MAX",
    "REASON_BUDGET",
    "REASON_OUT_OF_WORKSPACE",
    "REASON_SECRET_POLICY",
    "RENDERER_VERSION",
    "SCORE_AFFECTED_DOMAIN",
    "SCORE_EXACT_LITERAL_MATCH",
    "SCORE_FRESH",
    "SCORE_SOURCE_REF_OVERLAP",
    "SCORE_TAG_MATCH",
    "UNSET",
    "CompiledSourceRef",
    "ContextEntryNotFound",
    "ContextError",
    "ContextSelection",
    "ContextTreeUnavailable",
    "ExcludedItem",
    "ExpansionStatus",
    "FileMap",
    "FileMapItem",
    "FreshnessOutcome",
    "InvalidContextEntry",
    "InvalidSourceRefs",
    "PlanningSeed",
    "PlanningSeedDecision",
    "PlanningSeedRisk",
    "RenderedBlock",
    "RenderedContext",
    "ScoreBreakdown",
    "ScoredEntry",
    "SourceRefExpansion",
    "SourceRefMatcher",
    "Transformation",
    "WorkspaceTreeSnapshot",
    "approx_tokens",
    "build_file_map",
    "build_file_map_from_tree",
    "build_matcher",
    "build_rendered_payload",
    "capture_verification_commit",
    "clear_file_map_cache",
    "compile_source_ref",
    "compute_content_hash",
    "compute_file_map_hash",
    "compute_manifest_hash",
    "compute_source_hash",
    "create_entry",
    "delete_entry",
    "encode_path_identity",
    "epoch_microseconds",
    "evaluate_freshness",
    "expand_against_commit",
    "expand_source_refs",
    "freeze_manifest",
    "get_entry",
    "import_planning_seed",
    "list_entries",
    "normalize_text",
    "normalize_transformations",
    "read_workspace_tree",
    "render_block_text",
    "render_context",
    "select_context",
    "update_entry",
    "verify_freshness",
    "verify_workspace_entries",
]
