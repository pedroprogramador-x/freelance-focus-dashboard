"""Safety Kernel — política **pura**.

Regra estrutural ([04](../../../docs/architecture/04-safety-and-git-runtime.md) §4):
este pacote **recebe fatos e decide**. Ele nunca coleta os fatos.

Proibido aqui: filesystem IO, `subprocess`, banco, FastAPI, rede, provider concreto.
Quem faz o IO de path é `app.path_runtime`, e a dependência corre só nessa direção
(`path_runtime → safety`), nunca ao contrário.

Escopo entregue na E2 (Safety Kernel + Path Runtime, [07] §"Divisão da segurança"):
pré-validação sintática de path, decisão sobre `PathFacts`, decisão pós-abertura,
política de segredos, validação de `source_refs`, redator e `policy_hash`.

A E6 acrescenta duas peças **puras** ao mesmo pacote, pelo mesmo motivo de dono único:
`test_policy` (o schema de `DevWorkspace.test_config`, [04] §5) e `objective_signals` (os
sinais de segredo no texto do objetivo, [03] §5). A segunda existe para que
`orchestrator/` **não** ganhe uma segunda lista de nomes sensíveis nem uma segunda
denylist de caminho — ela reaproveita `is_sensitive_key` e `classify_path_secrecy`.

A E7.1 acrescenta `capability_profile`: o contrato puro das sete capabilities e a política
de aceitação da V1 — vocabulário e veredito, sem prova nem provider.

A E7.4 acrescenta `worktree_location`: a decisão pura sobre a raiz de worktrees (fora do
OneDrive, do repositório e do `.git`), sobre `WorktreeRootFacts` coletados por `path_runtime`.

Fora da E2 (fica para o Full Safety Runtime, E7): política de comandos, enforcement de
escrita, `ToolExecutor`, capability, processos, timeout, worktree.
"""

from app.safety.capability_profile import (
    Capability,
    CapabilityProfileRejected,
    EnforcementMode,
    InvalidCapabilityProfile,
    ProviderCapabilityProfile,
    ProviderRole,
    check_v1,
    fingerprint_v1_projection,
    parse_capability_profile,
    require_v1,
)
from app.safety.objective_signals import (
    OBJECTIVE_MENTIONS_SECRET,
    AnalyzerSignal,
    detect_sensitive_objective_signals,
)
from app.safety.paths import (
    PathForm,
    PathIntent,
    classify_path_form,
    decide_path,
    decide_post_open,
    prevalidate_path_syntax,
)
from app.safety.policy import SafetyPolicy, policy_hash
from app.safety.redaction import (
    contains_redaction_marker,
    is_sensitive_key,
    redact,
    redact_document,
)
from app.safety.secrets import SecretPolicy, SecretVerdict, classify_path_secrecy
from app.safety.source_refs import SourceRefResult, validate_source_ref
from app.safety.types import (
    ObjectIdentity,
    PathFacts,
    SafetyDecision,
    TreeWriter,
    TreeWriterFactory,
    Tri,
    WorktreeRoot,
    WorktreeRootFacts,
)
from app.safety.worktree_location import decide_tree_paths, decide_worktree_root

__all__ = [
    "OBJECTIVE_MENTIONS_SECRET",
    "AnalyzerSignal",
    "Capability",
    "CapabilityProfileRejected",
    "EnforcementMode",
    "InvalidCapabilityProfile",
    "ObjectIdentity",
    "PathFacts",
    "PathForm",
    "PathIntent",
    "ProviderCapabilityProfile",
    "ProviderRole",
    "SafetyDecision",
    "SafetyPolicy",
    "SecretPolicy",
    "SecretVerdict",
    "SourceRefResult",
    "TreeWriter",
    "TreeWriterFactory",
    "Tri",
    "WorktreeRoot",
    "WorktreeRootFacts",
    "check_v1",
    "classify_path_form",
    "classify_path_secrecy",
    "contains_redaction_marker",
    "decide_path",
    "decide_post_open",
    "decide_tree_paths",
    "decide_worktree_root",
    "detect_sensitive_objective_signals",
    "fingerprint_v1_projection",
    "is_sensitive_key",
    "parse_capability_profile",
    "policy_hash",
    "prevalidate_path_syntax",
    "redact",
    "redact_document",
    "require_v1",
    "validate_source_ref",
]
