"""Regra pura de revisão do Git **mediado** do Developer (E7.5-D).

Uma ref que o Developer nomeia (`GitShow`, `GitDiff`, `GitListTree`) **não** concede acesso a
outra branch, tag ou história: o `base_commit` do run é a autoridade congelada daquele snapshot.
A ref é resolvida pelo `git_runtime` para um commit completo **uma vez**, e o fato observado —
"este commit é o `base_commit` ou um ancestral dele?" — chega aqui. A decisão é desta camada.

Constante de código, não campo da `SafetyPolicy` (como a regra mediada de `.git`).
"""

from __future__ import annotations

from app.safety.types import SafetyDecision

REF_OUTSIDE_BASE_HISTORY_RULE_ID = "git.ref_outside_base_history"


def decide_mediated_revision(resolved_commit: str, *, within_base_history: bool) -> SafetyDecision:
    """Permite só o `base_commit` e os seus ancestrais. ``resolved_commit`` é o oid completo
    (nunca a ref crua) e vira o *subject* do registro."""
    if within_base_history:
        return SafetyDecision(
            allow=True,
            rule_id="git.revision_ok",
            reason="revisão é o base_commit ou ancestral dele",
            subject_redacted=resolved_commit,
        )
    return SafetyDecision(
        allow=False,
        rule_id=REF_OUTSIDE_BASE_HISTORY_RULE_ID,
        reason="revisão fora do histórico do base_commit do run",
        subject_redacted=resolved_commit,
    )


__all__ = ["REF_OUTSIDE_BASE_HISTORY_RULE_ID", "decide_mediated_revision"]
