"""Decisão pura sobre a raiz de worktrees (E7.4, D7).

[04](../../../docs/architecture/04-safety-and-git-runtime.md) §4 e §7: worktrees ficam fora do
OneDrive, fora da árvore principal e fora do `.git` do repositório. `app.path_runtime` coleta os
fatos (`WorktreeRootFacts`); aqui só se decide. Fecha em qualquer dúvida: um fato `UNKNOWN`
nega do mesmo jeito que um `TRUE`.

Nenhuma comparação de caminho acontece aqui — as relações de contenção chegam prontas nos
fatos, calculadas sobre caminhos canônicos. Detectar OneDrive por substring (`"OneDrive" in
path`) é exatamente o que **não** se faz: o que conta é estar sob uma raiz de sincronização
conhecida, pelo caminho real.
"""

from __future__ import annotations

from app.safety.paths import prevalidate_path_syntax
from app.safety.policy import SafetyPolicy
from app.safety.redaction import redact_path
from app.safety.types import SafetyDecision, Tri, WorktreeRootFacts


def _deny(rule_id: str, reason: str, subject: str) -> SafetyDecision:
    return SafetyDecision(
        allow=False, rule_id=rule_id, reason=reason, subject_redacted=redact_path(subject)
    )


def _tri_gate(value: Tri, rule: str, what: str, subject: str) -> SafetyDecision | None:
    if value.is_true:
        return _deny(f"worktree_root.{rule}", what, subject)
    if value.is_unknown:
        return _deny(
            f"worktree_root.{rule}_unverified", f"não verificado: {what}; fail closed", subject
        )
    return None


def decide_worktree_root(facts: WorktreeRootFacts) -> SafetyDecision:
    """Permite a raiz só quando **todos** os fatos foram verificados e são favoráveis."""
    subject = facts.requested_path

    if facts.inspection_error is not None:
        return _deny(
            "worktree_root.inspection_failed", f"inspeção falhou: {facts.inspection_error}", subject
        )
    if not facts.requested_is_absolute:
        return _deny(
            "worktree_root.not_absolute", "a raiz precisa ser um caminho absoluto", subject
        )
    if not facts.exists or facts.canonical_path is None:
        return _deny("worktree_root.missing", "a raiz não existe", subject)
    if not facts.is_directory.is_true:
        return _deny("worktree_root.not_directory", "a raiz não é um diretório", subject)
    if facts.identity is None:
        return _deny("worktree_root.no_identity", "identidade da raiz indisponível", subject)

    gates = (
        (facts.reparse_in_requested_chain, "link", "link/junction no caminho pedido"),
        (facts.reparse_in_canonical_chain, "reparse_point", "reparse point no caminho canônico"),
        (facts.overlaps_sync_root, "sync_root", "raiz sobreposta a pasta sincronizada (OneDrive)"),
        (facts.overlaps_repository_toplevel, "repository", "raiz sobreposta à árvore principal"),
        (facts.overlaps_git_common_dir, "git_dir", "raiz sobreposta ao diretório .git comum"),
    )
    for value, rule, what in gates:
        denied = _tri_gate(value, rule, what, subject)
        if denied is not None:
            return denied

    if (
        facts.repository_toplevel is None
        or facts.repository_toplevel_identity is None
        or facts.git_common_dir is None
        or facts.git_common_dir_identity is None
    ):
        return _deny(
            "worktree_root.repository_unbound",
            "repositório sem identidade verificada; fail closed",
            subject,
        )

    return SafetyDecision(
        allow=True,
        rule_id="worktree_root.allowed",
        reason="raiz canônica, sem reparse, fora de sincronização, do repo e do .git",
        subject_redacted=redact_path(subject),
    )


#: `MAX_PATH` do Win32 menos o terminador. Sem `\\?\` o Python e boa parte das ferramentas do
#: projeto (npm, pytest) não abrem caminho maior; a worktree precisaria ser usável.
_WINDOWS_MAX_PATH_CHARS = 259


def decide_tree_paths(
    paths: tuple[str, ...],
    *,
    absolute_prefix_chars: int | None = None,
    policy: SafetyPolicy | None = None,
) -> SafetyDecision:
    """A árvore inteira do `base_commit` pode ser materializada com segurança? (E7.4)

    Decidido **antes do primeiro byte escrito**, sobre todos os caminhos juntos:

    * cada caminho passa por `prevalidate_path_syntax` (relativo; sem `..`, `~`, absoluto,
      UNC, device namespace, drive-relative, ADS `:`, componente com ponto/espaço final,
      nome reservado, alias 8.3, caractere de controle, limite de bytes) — a mesma política
      de todo caminho mediado, nas mesmas regras em todos os SOs;
    * `\\` é recusada: o Git só separa com `/`, e no Windows ela viraria separador;
    * nenhum componente `.git` (em qualquer caixa): a worktree tem um só `.git`, o do Git;
    * nenhuma colisão por *case-fold* nem duplicata — duas entradas que o NTFS veria como o
      mesmo arquivo — **em qualquer prefixo**: cada prefixo (`Dir`, `Dir/a.txt`) tem uma só
      grafia na árvore inteira, então `Dir/a.txt` + `dir/b.txt` é recusado aqui, antes do
      primeiro byte, e não na pós-verificação (reauditoria P2-003). Como a regra do caminho
      completo, vale em todos os SOs: a decisão é pura e não consulta a plataforma;
    * nenhum conflito arquivo × diretório (`a` arquivo e `a/b` ao mesmo tempo);
    * com ``absolute_prefix_chars`` (Windows), o caminho absoluto final cabe em `MAX_PATH`.
    """
    active = policy or SafetyPolicy()
    files: set[str] = set()
    directories: set[str] = set()
    #: case-fold de cada prefixo (diretório ou caminho completo) → a única grafia admitida.
    spelling: dict[str, str] = {}
    for path in paths:
        if "\\" in path:
            return _deny("tree_path.backslash", "barra invertida em caminho da árvore", path)
        syntax = prevalidate_path_syntax(path, policy=active, allow_absolute=False)
        if not syntax.allow:
            return _deny(f"tree_path.{syntax.rule_id}", syntax.reason, path)
        components = path.split("/")
        if any(not component for component in components):
            return _deny("tree_path.empty_component", "componente vazio", path)
        if any(component.casefold() == ".git" for component in components):
            return _deny("tree_path.dot_git", "componente `.git` na árvore", path)
        key = path.casefold()
        if key in files:
            return _deny("tree_path.case_collision", "colisão por caixa ou duplicata", path)
        for depth in range(1, len(components) + 1):
            prefix = "/".join(components[:depth])
            if spelling.setdefault(prefix.casefold(), prefix) != prefix:
                return _deny(
                    "tree_path.case_collision", "prefixo com grafia de caixa conflitante", path
                )
        files.add(key)
        for depth in range(1, len(components)):
            directories.add("/".join(components[:depth]).casefold())
        if (
            absolute_prefix_chars is not None
            and absolute_prefix_chars + 1 + len(path) > _WINDOWS_MAX_PATH_CHARS
        ):
            return _deny("tree_path.too_long", "caminho absoluto excede MAX_PATH", path)
    conflict = files & directories
    if conflict:
        return _deny(
            "tree_path.file_directory_conflict",
            "arquivo e diretório com o mesmo nome",
            min(conflict),
        )
    return SafetyDecision(
        allow=True,
        rule_id="tree_path.allowed",
        reason=f"{len(paths)} caminhos materializáveis",
        subject_redacted="",
    )


__all__ = ["decide_tree_paths", "decide_worktree_root"]
