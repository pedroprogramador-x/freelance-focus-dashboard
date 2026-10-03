"""As quatro leituras Git do Developer (E7.5-D): `GitStatus`, `GitDiff`, `GitShow`, `GitListTree`.

O `git_runtime.mediated` é o único dono do Git (argv, ambiente, processo supervisionado, parse
estrutural) e devolve **fatos**. Aqui se coordena a decisão, como em `fs_ops`/`patch_ops`:

* **revisão** — `ref=None` é o `base_commit` do run; uma ref tipada é resolvida **uma vez** para
  um commit completo e só vale se for o `base_commit` ou ancestral dele
  (`safety.decide_mediated_revision`; fora disso `DENIED git.ref_outside_base_history`);
* **caminhos** — `path` explícito passa pela pipeline de sempre (`_authorize`): negado, a
  operação inteira é `DENIED`. Caminho **descoberto** (árvore, índice, não rastreado) passa por
  `_visible_facts`: negado, a negação vai ao journal e ele é **omitido** (e nunca lido). Caminho
  do provider **nunca** vai ao `argv`: a árvore inteira é listada e filtrada aqui;
* **conteúdo atual** — lido pelas primitivas do `path_runtime` (inspect → política →
  `open_existing` → pós-abertura → leitura). É leitura **interna**: só entra em `files_read` o
  que o resultado entregue de fato mostra;
* **comparação** — em Python, sobre bytes crus: o oid de blob do arquivo atual contra o oid da
  árvore/índice. O Git nunca lê a worktree (nenhum filtro/`textconv`/`diff.external` roda);
* **saída** — texto bruto completo; a redação central e o teto são do executor (uma vez).

Caminho de dentro do workspace que o vocabulário do `ToolExecutor` não sabe nomear, no escopo
pedido → `ERROR git_output_unverifiable` (nunca uma listagem parcial apresentada como completa).
Symlink, gitlink e repositório aninhado no escopo → `ERROR unsupported_git_tree_entry`.

O índice é observado por dois sinais estruturais, sempre unidos: o inventário (`ls-files
--stage`, já validado contra duplicatas — D-AUD-002) e a divergência árvore × índice do
`diff-index --cached --ita-invisible-in-index` (enxerga *intent-to-add* — D-AUD-001).

Cancelamento: além do token antes e depois de cada processo Git (`MediatedGit`), cada
operação o consulta mais uma vez imediatamente antes de devolver `OK` (D-AUD-004): nada lido
internamente chega ao provider — nem a `files_read` — depois de um cancelamento.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from app.git_runtime.mediated import (
    EntryKind,
    GitFailure,
    IndexEntry,
    Listing,
    MediatedGit,
    MediatedGitError,
    TreeEntry,
)
from app.path_runtime import (
    PathAccessDenied,
    PathFailure,
    PathOperationFailed,
    digest_fd,
    open_existing,
    read_fd,
)
from app.safety import decide_mediated_revision, decide_post_open, path_components
from app.safety.paths import PathIntent
from app.safety.types import PathFacts
from app.tool_executor.contracts import GitDiff, GitListTree, GitShow, GitStatus, ToolStatus
from app.tool_executor.diff_render import render_file_diff

# A **mesma** pipeline de path e a mesma regra de texto das operações de arquivo.
from app.tool_executor.fs_ops import _authorize, _decode_text, _visible_facts
from app.tool_executor.outcome import HandlerContext, Outcome, OutputFragment, ToolError

# ------------------------------------------------------------------------------- apoio


def _cancel(context: HandlerContext) -> None:
    if context.is_cancelled():
        raise PathOperationFailed(PathFailure.CANCELLED)


def _key(path: str) -> bytes:
    return path.encode("utf-8")


@contextmanager
def _git_errors() -> Iterator[None]:
    """Falha técnica do Git → `ERROR` com código fixo; cancelamento → o mesmo cancelamento
    *sticky* das demais operações. Nunca vira negação de política."""
    try:
        yield
    except MediatedGitError as error:
        if error.failure is GitFailure.CANCELLED:
            raise PathOperationFailed(PathFailure.CANCELLED) from None
        raise ToolError(error.failure.value) from None


def _session(context: HandlerContext) -> MediatedGit:
    return MediatedGit(
        context.root.path,
        expected_prefix=context.workspace.workspace_prefix,
        is_cancelled=context.is_cancelled,
        capture_bytes=context.limits.git_internal_capture_bytes,
    )


def _revision(git: MediatedGit, context: HandlerContext, ref: str | None) -> str:
    """O commit da operação: o `base_commit`, ou uma ref resolvida **uma vez** e aprovada."""
    base = context.workspace.base_commit
    if not git.is_oid(base):
        raise ToolError("git_object_unavailable")  # formato do repo ≠ do base_commit
    if ref is None:
        return base
    commit = git.resolve_commit(ref)
    decision = decide_mediated_revision(commit, within_base_history=git.is_ancestor(commit, base))
    if not decision.allow:
        raise PathAccessDenied(decision)
    return commit


def _scope(context: HandlerContext, path: str | None) -> str | None:
    """`path` explícito: a pipeline inteira (negado → a operação é `DENIED`) e depois a forma
    léxica (`a/b`) usada para casar contra os caminhos do Git. `None` = o workspace todo."""
    if path is None:
        return None
    _authorize(context, path, PathIntent.READ)
    lexical = "/".join(path_components(path))
    return lexical or None


def _in_scope(path: str, scope: str | None) -> bool:
    return scope is None or path == scope or path.startswith(scope + "/")


def _raw_in_scope(raw: bytes, scope: str | None) -> bool:
    if scope is None:
        return True
    prefix = _key(scope)
    return raw == prefix or raw.startswith(prefix + b"/")


def _require_complete(scope: str | None, *listings: Listing[Any]) -> None:
    for listing in listings:
        if any(_raw_in_scope(raw, scope) for raw in listing.unrepresentable):
            raise ToolError("git_output_unverifiable")


def _require_files(entries: list[TreeEntry] | list[IndexEntry]) -> None:
    if any(entry.kind is not EntryKind.FILE for entry in entries):
        raise ToolError("unsupported_git_tree_entry")


class _Visible:
    """`_visible_facts` uma vez por caminho, e a contagem genérica do que foi omitido."""

    def __init__(self, context: HandlerContext) -> None:
        self._context = context
        self.omitted = 0

    def __call__(self, path: str) -> PathFacts | None:
        _cancel(self._context)
        facts = _visible_facts(self._context, path)
        if facts is None:
            self.omitted += 1
        return facts

    def note(self) -> list[str]:
        return [f"[{self.omitted} paths omitted by policy]"] if self.omitted else []


@dataclass(frozen=True, slots=True)
class _Current:
    """O arquivo atual: ausente, ou presente com o oid de blob dos bytes crus (e o conteúdo,
    quando pedido e dentro da captura)."""

    present: bool
    oid: str | None = None
    data: bytes | None = None


_ABSENT = _Current(False)


def _observe(
    context: HandlerContext, git: MediatedGit, facts: PathFacts, *, keep: bool
) -> _Current:
    """O mesmo caminho do `ReadFile` (open → pós-abertura → leitura), **interno**.

    Ausência **comprovada** — não existe, é diretório, não é arquivo regular — é ausência.
    Qualquer outra falha (E/S, permissão) sobe como técnica; nunca vira "ausente".
    """
    if not facts.exists:
        return _ABSENT
    cap = context.limits.git_internal_capture_bytes
    try:
        with open_existing(facts, context.root, is_cancelled=context.is_cancelled) as opened:
            decision = decide_post_open(opened.facts, policy=context.policy)
            if not decision.allow:
                raise PathAccessDenied(decision)
            if keep and opened.size <= cap:
                data = read_fd(opened.fd, cap, is_cancelled=context.is_cancelled)
                if len(data) <= cap:
                    return _Current(True, git.blob_oid(data), data)
            update, hexdigest = git.blob_hasher(opened.size)
            total = digest_fd(opened.fd, update, is_cancelled=context.is_cancelled)
            if total != opened.size:
                raise PathOperationFailed(PathFailure.IO_ERROR)  # mudou durante a leitura
            return _Current(True, hexdigest())
    except PathOperationFailed as failed:
        if failed.category in (
            PathFailure.NOT_FOUND,
            PathFailure.IS_DIRECTORY,
            PathFailure.NOT_REGULAR,
        ):
            return _ABSENT
        raise


def _text(data: bytes) -> str | None:
    return _decode_text(data)


# --------------------------------------------------------------------------- GitStatus


def git_status(context: HandlerContext, request: GitStatus) -> Outcome:
    """O estado da worktree contra o **`base_commit`** do run (nunca o `HEAD` ambiente).

    `<tipo>\\t<caminho>`, ordenado por caminho (bytes UTF-8) e tipo:

    * índice × `base_commit`: `staged` (novo ou alterado), `deleted` (saiu do índice),
      `renamed` (origem **e** destino, só para conteúdo idêntico — sem heurística de
      similaridade); conflito de merge → `modified`;
    * worktree × índice: `deleted` (arquivo sumiu), `modified` (bytes diferentes);
    * `untracked`: fora do índice e não ignorado.
    """
    _cancel(context)
    with _git_errors():
        git = _session(context)
        base = _revision(git, context, None)
        tree = git.list_tree(base)
        index = git.list_index()
        index_divergent = git.index_divergence(base)
        untracked, nested = git.list_untracked()
        _require_complete(None, tree, index, index_divergent, untracked)
        if nested:
            raise ToolError("unsupported_git_tree_entry")
        _require_files([*tree.entries])
        _require_files([*index.entries])

        base_map = {entry.path: entry for entry in tree.entries}
        staged = {entry.path: entry for entry in index.entries if entry.stage == 0}
        unmerged = {entry.path for entry in index.entries if entry.stage}
        candidates = (
            set(base_map)
            | set(staged)
            | unmerged
            | set(index_divergent.entries)
            | set(untracked.entries)
        )

        visible = _Visible(context)
        allowed = {
            path: facts
            for path in sorted(candidates, key=_key)
            if (facts := visible(path)) is not None
        }

        kinds: set[tuple[str, str]] = {(path, "modified") for path in unmerged if path in allowed}
        added = [p for p in sorted(staged, key=_key) if p not in base_map and p in allowed]
        removed = [
            p
            for p in sorted(base_map, key=_key)
            if p not in staged and p not in unmerged and p in allowed
        ]
        by_oid: dict[str, list[str]] = {}
        for path in removed:
            by_oid.setdefault(base_map[path].oid, []).append(path)
        renamed_from: set[str] = set()
        for path in added:
            sources = by_oid.get(staged[path].oid)
            if sources:
                origin = sources.pop(0)
                renamed_from.add(origin)
                kinds.update({(path, "renamed"), (origin, "renamed")})
            else:
                kinds.add((path, "staged"))
        kinds.update((path, "deleted") for path in removed if path not in renamed_from)
        for path, entry in staged.items():
            base_entry = base_map.get(path)
            if (
                path in allowed
                and base_entry is not None
                and (base_entry.mode, base_entry.oid) != (entry.mode, entry.oid)
            ):
                kinds.add((path, "staged"))
        # D-AUD-001: o índice diverge da árvore sem que o inventário mostre (intent-to-add com o
        # oid do blob vazio). Só ganha `staged` quem ainda não tem rótulo da camada do índice.
        flagged = {path for path, _kind in kinds}
        kinds.update(
            (path, "staged")
            for path in index_divergent.entries
            if path in allowed and path not in flagged
        )

        for path in sorted(staged, key=_key):
            if path not in allowed:
                continue
            current = _observe(context, git, allowed[path], keep=False)
            if not current.present:
                kinds.add((path, "deleted"))
            elif current.oid != staged[path].oid:
                kinds.add((path, "modified"))
        kinds.update((path, "untracked") for path in untracked.entries if path in allowed)

    lines = [f"{kind}\t{path}" for path, kind in sorted(kinds, key=lambda k: (_key(k[0]), k[1]))]
    lines = lines or ["[clean]"]
    _cancel(context)  # D-AUD-004: o último ponto antes de `OK`
    return Outcome(ToolStatus.OK, content="\n".join([*lines, *visible.note()]))


# ------------------------------------------------------------------------- GitListTree


def git_list_tree(context: HandlerContext, request: GitListTree) -> Outcome:
    """Os blobs de um commit aprovado, no workspace: `<modo>\\t<oid>\\t<caminho>`, por caminho."""
    _cancel(context)
    scope = _scope(context, request.path)
    with _git_errors():
        git = _session(context)
        commit = _revision(git, context, request.ref)
        tree = git.list_tree(commit)
    _require_complete(scope, tree)
    entries = [entry for entry in tree.entries if _in_scope(entry.path, scope)]
    _require_files(entries)
    visible = _Visible(context)
    lines = [
        f"{entry.mode}\t{entry.oid}\t{entry.path}"
        for entry in entries
        if visible(entry.path) is not None
    ]
    lines = lines or ["[empty]"]
    _cancel(context)  # D-AUD-004
    return Outcome(ToolStatus.OK, content="\n".join([*lines, *visible.note()]))


# ----------------------------------------------------------------------------- GitShow


def git_show(context: HandlerContext, request: GitShow) -> Outcome:
    """Com `path`: o blob **de texto** daquele caminho no commit aprovado (sem truncar).
    Sem `path`: metadados e mensagem do commit (nunca patch)."""
    _cancel(context)
    scope = _scope(context, request.path)
    with _git_errors():
        git = _session(context)
        commit = _revision(git, context, request.ref)
        if request.path is None:
            outcome = _show_commit(git, commit)
            _cancel(context)  # D-AUD-004
            return outcome
        if scope is None:
            raise ToolError("is_directory")  # o próprio workspace
        tree = git.list_tree(commit)
        entry = next((e for e in tree.entries if e.path == scope), None)
        if entry is None:
            if any(e.path.startswith(scope + "/") for e in tree.entries):
                raise ToolError("is_directory")
            raise ToolError("not_found")
        if entry.kind is not EntryKind.FILE or entry.size is None:
            raise ToolError("unsupported_git_tree_entry")
        if entry.size > context.limits.git_show_path_bytes:
            # Antes de ler: nada de capturar um blob grande para só depois negar.
            return Outcome(ToolStatus.DENIED, rule_id="limit.git_show_path_bytes", subject=scope)
        data = git.read_blob(entry.oid, entry.size)
    if b"\x00" in data:
        raise ToolError("not_text")
    text = _text(data)
    if text is None:
        raise ToolError("not_text")
    _cancel(context)  # D-AUD-004: blob lido internamente não vira conteúdo nem `files_read`
    return Outcome(ToolStatus.OK, content=text, files_read=(scope,))


def _show_commit(git: MediatedGit, commit: str) -> Outcome:
    info = git.read_commit(commit)
    author, committer, message = _text(info.author), _text(info.committer), _text(info.message)
    if author is None or committer is None or message is None or "\x00" in message:
        raise ToolError("not_text")
    lines = [f"commit\t{info.oid}"]
    lines.extend(f"parent\t{parent}" for parent in info.parents)
    lines.extend([f"author\t{author}", f"committer\t{committer}", "", message])
    return Outcome(ToolStatus.OK, content="\n".join(lines))


# ----------------------------------------------------------------------------- GitDiff


def git_diff(context: HandlerContext, request: GitDiff) -> Outcome:
    """Commit aprovado × **filesystem atual**, renderizado em Python, por caminho (bytes).

    Seção textual por arquivo divergente (`--- a/` / `+++ b/`, `/dev/null` na criação e na
    remoção; rename = remoção + criação). Marcadores sem conteúdo: `binary\\t<p>` (NUL ou não
    UTF-8) e `large\\t<p>` (um dos lados acima da captura interna). Só as seções textuais
    entram em `files_read`.

    O estado atual tem duas dimensões, e nenhuma esconde a outra (D-AUD-003): se o **índice**
    diverge do commit (inventário, conflito ou *intent-to-add*), sai `index\\t<p>` —
    **sempre**, diverja o filesystem ou não; se o **filesystem** diverge, sai a representação
    dele. Por caminho, o marcador do índice vem primeiro. Redundância (índice e disco iguais
    entre si) é aceita: determinística e explícita.
    """
    _cancel(context)
    scope = _scope(context, request.path)
    with _git_errors():
        git = _session(context)
        commit = _revision(git, context, request.ref)
        tree = git.list_tree(commit)
        index = git.list_index()
        index_divergent = git.index_divergence(commit)
        untracked, nested = git.list_untracked()
        _require_complete(scope, tree, index, index_divergent, untracked)
        if any(_in_scope(path, scope) for path in nested):
            raise ToolError("unsupported_git_tree_entry")
        base_map = {e.path: e for e in tree.entries if _in_scope(e.path, scope)}
        index_entries = [e for e in index.entries if _in_scope(e.path, scope)]
        _require_files([*base_map.values()])
        _require_files(index_entries)
        staged = {e.path: e for e in index_entries if e.stage == 0}
        unmerged = {e.path for e in index_entries if e.stage}
        fresh = {path for path in untracked.entries if _in_scope(path, scope)}
        index_flagged = {p for p in index_divergent.entries if _in_scope(p, scope)}
        candidates = set(base_map) | set(staged) | unmerged | fresh | index_flagged

        visible = _Visible(context)
        fragments: list[OutputFragment] = []
        cap = context.limits.git_internal_capture_bytes
        for path in sorted(candidates, key=_key):
            facts = visible(path)
            if facts is None:
                continue  # omitido: nunca lido
            base_entry = base_map.get(path)
            index_entry = staged.get(path)
            index_diverges = (
                path in unmerged
                or path in index_flagged
                or (
                    (base_entry is None) != (index_entry is None)
                    or (
                        base_entry is not None
                        and index_entry is not None
                        and (base_entry.mode, base_entry.oid) != (index_entry.mode, index_entry.oid)
                    )
                )
            )
            if index_diverges:
                # Metadado puro: nenhum conteúdo, nenhum `files_read`. Independe do disco.
                fragments.append(OutputFragment(f"index\t{path}"))
            current = _observe(context, git, facts, keep=True)
            unchanged = (
                base_entry is not None and current.present and current.oid == base_entry.oid
            ) or (base_entry is None and not current.present)
            if unchanged:
                continue
            old: bytes | None = None
            large = False
            if base_entry is not None:
                assert base_entry.size is not None  # `_require_files`
                if base_entry.size > cap:
                    large = True
                else:
                    old = git.read_blob(base_entry.oid, base_entry.size)
            new = current.data
            if current.present and new is None:
                large = True
            if large:
                fragments.append(OutputFragment(f"large\t{path}"))
                continue
            old_text = None if old is None else _text(old)
            new_text = None if new is None else _text(new)
            binary = (old is not None and (old_text is None or b"\x00" in old)) or (
                new is not None and (new_text is None or b"\x00" in new)
            )
            if binary:
                fragments.append(OutputFragment(f"binary\t{path}"))
                continue
            section = render_file_diff(
                path,
                old_text if base_entry is not None else None,
                new_text if current.present else None,
            )
            fragments.append(OutputFragment(section, (path,)))

    if not fragments:
        fragments.append(OutputFragment("[no differences]"))
    fragments.extend(OutputFragment(note) for note in visible.note())
    _cancel(context)  # D-AUD-004
    return Outcome(ToolStatus.OK, fragments=tuple(fragments))


__all__ = ["git_diff", "git_list_tree", "git_show", "git_status"]
