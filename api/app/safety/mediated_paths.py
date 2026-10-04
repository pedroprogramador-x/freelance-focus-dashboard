"""Regra pura de acesso **mediado** do Developer (E7.5-A).

Existe porque a política global de path (`prevalidate_path_syntax` + `decide_path`) vale para
qualquer leitor do backend, e `.git` — o diretório administrativo, ou o arquivo `.git` de uma
worktree, que aponta para o git dir — **não** é segredo por nome nem sintaxe inválida. O
Developer nunca pode ler nem alterá-lo por `ToolExecutor`: ali moram config, hooks e refs.

Esta regra **acrescenta** à gramática existente; não a substitui nem a reimplementa. Os
componentes vêm de `path_components` (a única separação de path do kernel), então as defesas
que já existem — `..`, absoluto, UNC, device namespace, drive/root-relative, ADS, nomes
reservados, ponto/espaço final, alias 8.3, segredo, contenção, link/reparse — continuam sendo
decididas onde sempre foram.

É uma constante de código, não um campo da `SafetyPolicy`: não muda `policy_hash` nem o
fingerprint (decisão de Pedro, E7.5). **Não** nega `.gitattributes`, `.gitmodules` nem
`.gitignore`: são arquivos normais do projeto, e o Git mediado deve permanecer seguro mesmo
quando eles existem ou são modificados.
"""

from __future__ import annotations

from app.safety.paths import path_components, prevalidate_path_syntax
from app.safety.policy import SafetyPolicy
from app.safety.redaction import redact_path
from app.safety.types import SafetyDecision

DOT_GIT_RULE_ID = "path.dot_git_denied"


def decide_mediated_path(requested: str) -> SafetyDecision:
    """Nega qualquer componente `.git`, em qualquer caixa e em qualquer profundidade.

    Puro e só sobre a string. `casefold` porque o Windows é case-insensitive: `.GIT` e `.Git`
    são o mesmo diretório. As formas NTFS de `.git` (`.git.`, `.git `, `GIT~1`,
    `.git::$INDEX_ALLOCATION`) já são negadas pela pré-validação; um teste trava isso.
    """
    for component in path_components(requested):
        if component.casefold() == ".git":
            return SafetyDecision(
                allow=False,
                rule_id=DOT_GIT_RULE_ID,
                reason="componente `.git` não é acessível ao Developer",
                subject_redacted=redact_path(requested),
            )
    return SafetyDecision(
        allow=True,
        rule_id="path.mediated_ok",
        reason="nenhuma regra mediada se aplica",
        subject_redacted=redact_path(requested),
    )


def prevalidate_mediated_path(
    requested: str, *, policy: SafetyPolicy | None = None
) -> SafetyDecision:
    """Pré-validação de um path de `ToolRequest`: a sintaxe existente **e** a regra mediada.

    Sempre relativo (`allow_absolute=False`: `external_paths` é `disabled`). A sintaxe vem
    primeiro — uma sintaxe inválida é negada pela regra mais específica dela.
    """
    syntax = prevalidate_path_syntax(requested, policy=policy, allow_absolute=False)
    if not syntax.allow:
        return syntax
    return decide_mediated_path(requested)


__all__ = ["DOT_GIT_RULE_ID", "decide_mediated_path", "prevalidate_mediated_path"]
