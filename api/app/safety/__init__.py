"""Safety Kernel — política **pura**.

Regra estrutural ([04](../../../docs/architecture/04-safety-and-git-runtime.md) §4):
este pacote **recebe fatos e decide**. Ele nunca coleta os fatos.

Proibido aqui: filesystem IO, `subprocess`, banco, FastAPI, rede, provider concreto.
Quem faz o IO de path é `app.path_runtime`, e a dependência corre só nessa direção
(`path_runtime → safety`), nunca ao contrário.

Escopo entregue na E2 (Safety Kernel + Path Runtime, [07] §"Divisão da segurança"):
pré-validação sintática de path, decisão sobre `PathFacts`, decisão pós-abertura,
política de segredos, validação de `source_refs`, redator e `policy_hash`.

Fora da E2 (fica para o Full Safety Runtime, E7): política de comandos, enforcement de
escrita, `ToolExecutor`, capability, processos, timeout, worktree.
"""

from app.safety.paths import (
    PathForm,
    PathIntent,
    classify_path_form,
    decide_path,
    decide_post_open,
    prevalidate_path_syntax,
)
from app.safety.policy import SafetyPolicy, policy_hash
from app.safety.redaction import redact
from app.safety.secrets import SecretVerdict, classify_path_secrecy
from app.safety.source_refs import SourceRefResult, validate_source_ref
from app.safety.types import (
    ObjectIdentity,
    PathFacts,
    SafetyDecision,
    Tri,
)

__all__ = [
    "ObjectIdentity",
    "PathFacts",
    "PathForm",
    "PathIntent",
    "SafetyDecision",
    "SafetyPolicy",
    "SecretVerdict",
    "SourceRefResult",
    "Tri",
    "classify_path_form",
    "classify_path_secrecy",
    "decide_path",
    "decide_post_open",
    "policy_hash",
    "prevalidate_path_syntax",
    "redact",
    "validate_source_ref",
]
