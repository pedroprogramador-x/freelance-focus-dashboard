"""Worktree de task — criação isolada, inspeção factual e classificação (E7.4).

[04](../../../docs/architecture/04-safety-and-git-runtime.md) §8 e o adendo E7.4: a execução de
uma task acontece numa worktree em `<worktrees_dir>/ff-task-<id8>`, na branch `ff/task-<id8>`,
nascida do **SHA congelado no planejamento** — nunca de um nome de branch, nunca do `HEAD`
atual. Este módulo define **onde** o trabalho ocorre; **como** o processo Git é supervisionado
é do `process_runtime` (adendo E7.4 a [01] §2).

## O que existe aqui

* `task_worktree_names` — nomes determinísticos a partir de um UUID4 canônico. Nada do
  `task_id` além dos 8 primeiros hex vira caminho.
* `repository_layout` — toplevel, `.git` comum e prefixo do workspace, lidos do Git.
* `list_worktrees` — inventário factual: `git worktree list --porcelain -z` **mais** a
  varredura de `<common>/worktrees/`, porque o `list` não enxerga admin dir órfão.
* `inspect_task_worktree` / `classify_task_worktree` — fatos e veredito puro.
* `create_worktree` — idempotente: só `ABSENT` cria; `REUSABLE` é devolvido como reuso; todo o
  resto é recusa com o fato. Nada é removido, movido, podado ou reparado — nunca.

## Invariantes

* **O Git não materializa conteúdo** (P2-001/P2-002 da auditoria E7.4). A criação é
  `git worktree add --no-checkout` (só branch, metadata e o arquivo `.git`), depois
  `git read-tree` **sem `-u`** (só o índice) e, por fim, o runtime escreve cada arquivo a partir
  do **blob cru** (`cat-file --batch`, com o oid conferido no Python) pelo `TreeWriter` do
  `path_runtime`. Nenhum checkout, `reset --hard`, `checkout-index`, `read-tree -u`,
  `restore`, `switch` ou `archive` participa: atributos e filtros (`smudge`/`process`/`clean`,
  LFS) ficam **fora do caminho**, qualquer que seja a config — `includeIf`, `info/attributes`
  alternando no meio da operação, valor literal `set`/`unset`/`unspecified`. Um ponteiro LFS
  versionado vira o ponteiro, byte a byte.
* **Os únicos verbos mutantes são esses dois**, em `_add_argv` e `_read_tree_argv`, os dois
  sob `process_runtime` (árvore contida, timeout, cancelamento, confirmação de morte). Nenhum
  `reset`, `branch -D`, `update-ref`, `remove`, `prune` ou `repair` (`test_architecture.py`).
* **Limpeza é verificada sem `git status`** — que roda `clean` filters: índice exatamente igual
  ao `base_commit` (`ls-files --stage`, só estágio 0; `ls-files -v`, só `H`; e
  `diff-index --cached --ita-invisible-in-index`, que enxerga `intent-to-add`) **e** filesystem
  exatamente igual ao snapshot (sem seguir link; oid de blob calculado dos bytes crus; bit de
  execução no POSIX). Arquivo extra, diretório extra, ignorado ou link = sujo. A **mesma**
  verificação decide o reuso e a pós-condição da criação.
* **Árvore validada antes do primeiro byte**: só `100644`/`100755`; symlink e gitlink são
  `UNSUPPORTED_TREE_ENTRY` (medido: o Git os grava de jeitos diferentes conforme SO e
  `core.symlinks`); caminho inseguro é `UNSAFE_TREE_PATH` (`safety.decide_tree_paths`). Todo
  blob precisa existir localmente: `--no-lazy-fetch` + `GIT_NO_LAZY_FETCH=1`, nunca rede.
* **Start-point `<sha>^{commit}`.** Medido no planejamento: com uma ref chamada com os 40 hex
  de A apontando para B, `worktree add -b X P <shaA>` faz checkout de **B**, em qualquer
  namespace de ref. O sufixo `^{commit}` não é nome de ref e força a semântica de objeto; a
  pós-condição `HEAD == base_commit` confere o resultado.
* **Hooks desligados por comando** (D2): `core.hooksPath=/dev/null` — o `add` ainda atualiza
  refs (`reference-transaction`) e o `read-tree` escreve índice (`post-index-change`).
* **Ownership limitado** (D1): com 8 hex (32 bits) duas tasks podem colidir. Este módulo
  **não** afirma ownership completo — ele prova só identidade estrutural (caminho, registro,
  `.git` comum, branch, `HEAD == ponta == base`, limpeza estrita). Garantir `id8` único entre
  as tasks relevantes é do Execution Manager. Por isso uma branch `ff/task-<id8>` que existe
  sem worktree reusável é **recusada** (D5): nada prova de quem ela é.
* **Nada é limpo**: timeout, cancelamento, falha ou pós-condição quebrada deixam o que existir
  para diagnóstico (`CREATED_INVALID` e afins).
* **Nunca lança** por IO: falha vira `UNVERIFIABLE`. Só entrada inválida de programação
  (`task_id` fora do formato, `root`/`tree_writer` ausentes) levanta `InvalidWorktreeRequest`.
* **Não é sandbox.** Supervisão controla o ciclo de vida do processo Git; a verificação de
  identidade da raiz e dos diretórios estreita a janela TOCTOU, não a fecha ([04] §4).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from app.git_runtime import (
    _decode_text,
    _git_env,
    _has_git_marker,
    _run_git,
    _workspace_prefix,
)
from app.process_runtime import (
    InvalidProcessSpec,
    ProcessOutcome,
    ProcessResult,
    ProcessSpec,
    run_supervised,
)
from app.safety.types import ObjectIdentity, TreeWriterFactory, Tri, WorktreeRoot
from app.safety.worktree_location import decide_tree_paths

if TYPE_CHECKING:
    from subprocess import CompletedProcess

# ------------------------------------------------------------------------------- nomes

#: UUID4 canônico, em minúsculas — o formato de `db.base.new_uuid`. Estrito: o `id8` vira
#: nome de diretório e de branch, e maiúscula criaria alias num filesystem case-insensitive.
_TASK_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")

#: SHA-1 completo, **minúsculo**. Nada de SHA curto, ref, `HEAD`, opção ou sha256 (E7.4).
_BASE_COMMIT_RE = re.compile(r"[0-9a-f]{40}")

DIRECTORY_PREFIX = "ff-task-"
BRANCH_PREFIX = "ff/task-"


class InvalidWorktreeRequest(ValueError):
    """Entrada fora do contrato (erro de programação). Nenhum comando Git é executado."""


@dataclass(frozen=True, slots=True)
class TaskWorktreeNames:
    """Os nomes congelados de [04] §8. Derivados só dos 8 primeiros hex do `task_id`."""

    task_id: str
    id8: str
    directory_name: str
    branch: str
    branch_ref: str


def task_worktree_names(task_id: object) -> TaskWorktreeNames:
    """`ff-task-<id8>` e `refs/heads/ff/task-<id8>`. Levanta para `task_id` não canônico."""
    if not isinstance(task_id, str) or not _TASK_ID_RE.fullmatch(task_id):
        raise InvalidWorktreeRequest("task_id precisa ser um UUID4 canônico em minúsculas")
    id8 = task_id[:8]
    branch = f"{BRANCH_PREFIX}{id8}"
    return TaskWorktreeNames(
        task_id=task_id,
        id8=id8,
        directory_name=f"{DIRECTORY_PREFIX}{id8}",
        branch=branch,
        branch_ref=f"refs/heads/{branch}",
    )


# ----------------------------------------------------------------------------- vereditos


class WorktreeVerdict(str, Enum):
    """Estado classificado da worktree de uma task. Só `ABSENT` cria; só `REUSABLE` reusa."""

    ABSENT = "absent"
    REUSABLE = "reusable"
    DIRTY = "dirty"
    HEAD_MISMATCH = "head_mismatch"
    BRANCH_DIVERGED = "branch_diverged"
    BRANCH_BUSY = "branch_busy"
    #: `ff/task-<id8>` existe na base, sem worktree nenhuma. Recusada (D5): 32 bits não provam
    #: a quem ela pertence.
    BRANCH_ORPHANED = "branch_orphaned"
    LOCKED = "locked"
    STALE_METADATA = "stale_metadata"
    FOREIGN_PATH = "foreign_path"
    BROKEN_LINK = "broken_link"
    BASE_INVALID = "base_invalid"
    NOT_A_REPO = "not_a_repo"
    ROOT_INVALID = "root_invalid"
    UNVERIFIABLE = "unverifiable"
    #: O `add` terminou com sucesso, mas alguma pós-condição falhou. Preservada.
    CREATED_INVALID = "created_invalid"
    #: A árvore do base tem symlink, gitlink ou blob grande demais — sem contrato na V1.
    UNSUPPORTED_TREE_ENTRY = "unsupported_tree_entry"
    #: Algum caminho da árvore do base não é materializável com segurança.
    UNSAFE_TREE_PATH = "unsafe_tree_path"


class RepositoryState(str, Enum):
    OK = "ok"
    NOT_A_REPO = "not_a_repo"
    UNVERIFIABLE = "unverifiable"


class RootState(str, Enum):
    OK = "ok"
    #: Identidade da raiz, do toplevel ou do `.git` comum não confere com a `WorktreeRoot`.
    INVALID = "invalid"
    UNKNOWN = "unknown"


class TargetKind(str, Enum):
    ABSENT = "absent"
    DIRECTORY = "directory"
    FILE = "file"
    #: Link, junction ou qualquer reparse point — ou um caminho cujo canônico não é ele mesmo.
    REPARSE = "reparse"
    UNKNOWN = "unknown"


class LinkState(str, Enum):
    """O `.git` dentro do alvo aponta de volta para o admin dir que o registra?"""

    NOT_APPLICABLE = "not_applicable"
    CONSISTENT = "consistent"
    BROKEN = "broken"
    #: `.git` é um diretório: um repositório independente, não uma worktree vinculada.
    REPOSITORY = "repository"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------- repositório


@dataclass(frozen=True, slots=True)
class RepositoryLayout:
    """Onde o repositório vive, como o Git o vê. Caminhos absolutos, `normpath`."""

    toplevel: str
    git_common_dir: str
    #: Prefixo do workspace dentro do repo (`""` na raiz), com `/` no fim, como o Git emite.
    workspace_prefix: str


def _norm(path: str) -> str:
    return os.path.normpath(path)


def _same_path(a: str, b: str) -> bool:
    """Igualdade de caminho absoluto: `normcase(normpath)`. Nunca substring."""
    return os.path.normcase(_norm(a)) == os.path.normcase(_norm(b))


def _probe_repository(git: str, local_path: str) -> tuple[RepositoryState, RepositoryLayout | None]:
    inside = _run_git(git, local_path, "rev-parse", "--is-inside-work-tree")
    if inside is None:
        return RepositoryState.UNVERIFIABLE, None
    if inside.returncode != 0:
        state = (
            RepositoryState.UNVERIFIABLE
            if _has_git_marker(local_path)
            else RepositoryState.NOT_A_REPO
        )
        return state, None
    answer = _decode_text(inside.stdout)
    if answer is None:
        return RepositoryState.UNVERIFIABLE, None
    if answer.strip() != "true":
        return RepositoryState.NOT_A_REPO, None  # bare: sem árvore de trabalho

    paths = _run_git(
        git,
        local_path,
        "rev-parse",
        "--path-format=absolute",
        "--show-toplevel",
        "--git-common-dir",
    )
    lines = _exact_lines(paths, expected=2)
    prefix = _workspace_prefix(git, local_path)
    if lines is None or prefix is None:
        return RepositoryState.UNVERIFIABLE, None
    toplevel, common = lines
    return RepositoryState.OK, RepositoryLayout(
        toplevel=_norm(toplevel), git_common_dir=_norm(common), workspace_prefix=prefix
    )


def _exact_lines(
    result: CompletedProcess[bytes] | None, *, expected: int
) -> tuple[str, ...] | None:
    """Exatamente `expected` linhas absolutas, não vazias. Um `\\n` num caminho → `None`."""
    if result is None or result.returncode != 0:
        return None
    text = _decode_text(result.stdout)
    if text is None or not text.endswith("\n"):
        return None
    lines = tuple(text[:-1].split("\n"))
    if len(lines) != expected or not all(line and os.path.isabs(line) for line in lines):
        return None
    return lines


def repository_layout(local_path: str) -> RepositoryLayout | None:
    """Toplevel e `.git` comum do repositório de `local_path`. `None` se não deu para ler.

    É o que quem chama precisa para preparar a `WorktreeRoot`
    (`path_runtime.prepare_worktree_root`) antes de `create_worktree`.
    """
    git = shutil.which("git")
    if git is None:
        return None
    state, layout = _probe_repository(git, local_path)
    return layout if state is RepositoryState.OK else None


# ------------------------------------------------------------------------ worktree list


@dataclass(frozen=True, slots=True)
class WorktreeRecord:
    """Um registro de `git worktree list --porcelain -z`. Caminho em `normpath`."""

    path: str
    head: str | None
    branch_ref: str | None
    detached: bool
    bare: bool
    locked: bool
    lock_reason: str | None
    prunable: bool
    prune_reason: str | None


_HEX40 = re.compile(r"[0-9a-f]{40}")
_VALUE_KEYS = (b"HEAD", b"branch")
_FLAG_KEYS = (b"detached", b"bare")
_OPTIONAL_VALUE_KEYS = (b"locked", b"prunable")


def _build_record(fields: dict[bytes, bytes | None]) -> WorktreeRecord | None:
    decoded: dict[bytes, str | None] = {}
    for key, value in fields.items():
        if value is None:
            decoded[key] = None
            continue
        text = _decode_text(value)
        if text is None:
            return None
        decoded[key] = text
    path = decoded[b"worktree"]
    if path is None or not os.path.isabs(path):
        return None
    bare = b"bare" in fields
    head = decoded.get(b"HEAD")
    branch = decoded.get(b"branch")
    detached = b"detached" in fields
    if bare:
        if head is not None or branch is not None or detached:
            return None
    else:
        if head is None or not _HEX40.fullmatch(head):
            return None
        if detached == (branch is not None):
            return None  # exatamente um dos dois
        if branch is not None and not branch.startswith("refs/"):
            return None
    return WorktreeRecord(
        path=_norm(path),
        head=head,
        branch_ref=branch,
        detached=detached,
        bare=bare,
        locked=b"locked" in fields,
        lock_reason=decoded.get(b"locked"),
        prunable=b"prunable" in fields,
        prune_reason=decoded.get(b"prunable"),
    )


def _parse_worktree_porcelain(data: bytes) -> tuple[WorktreeRecord, ...] | None:
    """Parser determinístico de `worktree list --porcelain -z`, em bytes. `None` se malformado.

    Formato: cada atributo termina em NUL; um NUL extra fecha o registro. Atributo
    desconhecido, repetido, fora de ordem (o primeiro precisa ser `worktree`), valor ausente
    onde é obrigatório, registro sem fechamento ou saída vazia → `None` inteiro. Nenhum texto
    legível por humano é interpretado: os motivos de `locked`/`prunable` são guardados, nunca
    comparados.
    """
    if not data.endswith(b"\0"):
        return None
    tokens = data.split(b"\0")
    tokens.pop()
    records: list[WorktreeRecord] = []
    current: dict[bytes, bytes | None] | None = None
    for token in tokens:
        if token == b"":
            if current is None:
                return None
            record = _build_record(current)
            if record is None:
                return None
            records.append(record)
            current = None
            continue
        key, separator, value = token.partition(b" ")
        if current is None:
            if key != b"worktree" or not separator or not value:
                return None
            current = {b"worktree": value}
            continue
        if key in current:
            return None
        if key in _VALUE_KEYS:
            if not separator or not value:
                return None
            current[key] = value
        elif key in _FLAG_KEYS:
            if separator:
                return None
            current[key] = None
        elif key in _OPTIONAL_VALUE_KEYS:
            current[key] = value if separator else None
        else:
            return None
    if current is not None or not records:
        return None
    return tuple(records)


# ----------------------------------------------------------------------- admin dirs


@dataclass(frozen=True, slots=True)
class WorktreeAdminEntry:
    """Um `<common>/worktrees/<name>/`. ``gitdir`` é o `<worktree>/.git` que ele registra."""

    name: str
    path: str
    gitdir: str | None
    locked: bool


class _Unverifiable(Exception):
    """Interna: uma leitura de filesystem não permitiu concluir. Nunca sai do módulo."""


def _lexists(path: str) -> bool:
    """Existência **sem seguir link**; ausência só quando confirmada, dúvida levanta."""
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as exc:
        raise _Unverifiable(path) from exc
    return True


def _read_text_file(path: str) -> str | None:
    """Conteúdo UTF-8 estrito; `None` se comprovadamente ausente; dúvida levanta."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(64 * 1024)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise _Unverifiable(path) from exc
    text = _decode_text(raw)
    if text is None:
        raise _Unverifiable(path)
    return text


def _single_line(text: str) -> str:
    """Remove só o terminador de linha. Espaço faz parte de nome de diretório."""
    if text.endswith("\r\n"):
        return text[:-2]
    if text.endswith("\n"):
        return text[:-1]
    return text


def _scan_admin_entries(common_dir: str) -> tuple[WorktreeAdminEntry, ...]:
    """Varre `<common>/worktrees/` **sem alterar nada**. `_Unverifiable` em qualquer dúvida."""
    base = os.path.join(common_dir, "worktrees")
    try:
        with os.scandir(base) as iterator:
            dirs = [entry for entry in iterator if entry.is_dir(follow_symlinks=False)]
    except (FileNotFoundError, NotADirectoryError):
        return ()
    except OSError as exc:
        raise _Unverifiable(base) from exc
    found: list[WorktreeAdminEntry] = []
    for entry in dirs:
        content = _read_text_file(os.path.join(entry.path, "gitdir"))
        gitdir: str | None = None
        if content is not None:
            line = _single_line(content)
            if line:
                gitdir = _norm(line if os.path.isabs(line) else os.path.join(entry.path, line))
        found.append(
            WorktreeAdminEntry(
                name=entry.name,
                path=_norm(entry.path),
                gitdir=gitdir,
                locked=_lexists(os.path.join(entry.path, "locked")),
            )
        )
    found.sort(key=lambda item: item.name)
    return tuple(found)


@dataclass(frozen=True, slots=True)
class WorktreeInventory:
    """Registros do Git **e** admin dirs no disco. Os dois lados, porque um não vê o outro."""

    git_common_dir: str
    records: tuple[WorktreeRecord, ...]
    admin_entries: tuple[WorktreeAdminEntry, ...]

    def record_at(self, path: str) -> WorktreeRecord | None:
        matches = [record for record in self.records if _same_path(record.path, path)]
        return matches[0] if len(matches) == 1 else None

    def branch_holders(self, branch_ref: str) -> tuple[WorktreeRecord, ...]:
        return tuple(record for record in self.records if record.branch_ref == branch_ref)

    def admin_for(self, worktree_path: str) -> tuple[WorktreeAdminEntry, ...]:
        dotgit = os.path.join(worktree_path, ".git")
        return tuple(
            entry
            for entry in self.admin_entries
            if entry.gitdir is not None and _same_path(entry.gitdir, dotgit)
        )

    def related_admin(self, directory_name: str) -> tuple[WorktreeAdminEntry, ...]:
        """Admin dirs com o nome da task ou com os sufixos numéricos que o Git acrescenta."""
        pattern = re.compile(re.escape(directory_name) + r"[0-9]*", re.IGNORECASE)
        return tuple(entry for entry in self.admin_entries if pattern.fullmatch(entry.name))

    @property
    def orphan_admin_entries(self) -> tuple[WorktreeAdminEntry, ...]:
        """Admin dirs que nenhum registro do `list` explica (sem `gitdir`, ou apontando para
        fora de todo registro). O `list` não os mostra; o `prune` os apagaria — nós não."""
        orphans = []
        for entry in self.admin_entries:
            explained = entry.gitdir is not None and any(
                _same_path(entry.gitdir, os.path.join(record.path, ".git"))
                for record in self.records
            )
            if not explained:
                orphans.append(entry)
        return tuple(orphans)


def _inventory(git: str, local_path: str, common_dir: str) -> WorktreeInventory | None:
    result = _run_git(git, local_path, "worktree", "list", "--porcelain", "-z")
    if result is None or result.returncode != 0:
        return None
    records = _parse_worktree_porcelain(result.stdout)
    if records is None:
        return None
    try:
        admin = _scan_admin_entries(common_dir)
    except _Unverifiable:
        return None
    return WorktreeInventory(git_common_dir=common_dir, records=records, admin_entries=admin)


def list_worktrees(local_path: str) -> WorktreeInventory | None:
    """Inventário factual das worktrees do repositório de `local_path`. Nunca altera nada."""
    git = shutil.which("git")
    if git is None:
        return None
    state, layout = _probe_repository(git, local_path)
    if state is not RepositoryState.OK or layout is None:
        return None
    return _inventory(git, local_path, layout.git_common_dir)


# --------------------------------------------------------- snapshot do base_commit

#: Os únicos modos materializados. Symlink (`120000`) e gitlink (`160000`) são recusados:
#: medido, o Git grava um symlink como arquivo comum com o alvo (Windows, `core.symlinks=false`)
#: ou como link de verdade, e um gitlink como diretório vazio — nenhum tem contrato na V1.
_REGULAR_MODES = frozenset(("100644", "100755"))

#: Maior blob materializado. Memória limitada: um lote de `cat-file --batch` carrega no máximo
#: `_BATCH_BUDGET_BYTES` (ou um único blob maior que isso, até este teto).
_MAX_BLOB_BYTES = 256 * 1024 * 1024
_BATCH_BUDGET_BYTES = 32 * 1024 * 1024

#: Em toda leitura de objeto: nada de pager, e **nenhum fetch preguiçoso** — num partial clone
#: um objeto ausente é erro, nunca rede. Git sem `--no-lazy-fetch` (< 2.44) falha o comando:
#: fail closed. `GIT_NO_LAZY_FETCH=1` também está no ambiente (`_git_env`).
_OBJECT_READ_OPTIONS = ("--no-pager", "--no-lazy-fetch")

_HASH_CHUNK = 1024 * 1024
_POSIX_MODES = os.name != "nt"


@dataclass(frozen=True, slots=True)
class TreeEntry:
    """Um arquivo regular do `base_commit`. ``path`` relativo à raiz do repo, com `/`."""

    path: str
    mode: str
    oid: str
    size: int

    @property
    def executable(self) -> bool:
        return self.mode == "100755"


@dataclass(frozen=True, slots=True)
class TreeSnapshot:
    """O `base_commit` inteiro, validado: arquivos e os diretórios que eles exigem."""

    commit: str
    entries: tuple[TreeEntry, ...]
    directories: frozenset[str]

    def by_path(self) -> dict[str, TreeEntry]:
        return {entry.path: entry for entry in self.entries}


def _read_snapshot(
    git: str, cwd: str, base_commit: str, *, absolute_prefix_chars: int | None
) -> TreeSnapshot | WorktreeVerdict:
    """`ls-tree -r -z -l --full-tree <base>^{commit}` → snapshot, **ou** o motivo da recusa.

    Toda a árvore é validada antes de qualquer escrita: modo e tipo de cada entrada, oid,
    tamanho, UTF-8 estrito do caminho e a política de `safety.decide_tree_paths` sobre o
    conjunto inteiro (colisão por caixa, conflito arquivo × diretório, `MAX_PATH`).
    """
    result = _run_git(
        git,
        cwd,
        *_OBJECT_READ_OPTIONS,
        "ls-tree",
        "-r",
        "-z",
        "-l",
        "--full-tree",
        f"{base_commit}^{{commit}}",
        "--",
    )
    if result is None or result.returncode != 0:
        return WorktreeVerdict.UNVERIFIABLE
    data = result.stdout
    if data and not data.endswith(b"\0"):
        return WorktreeVerdict.UNVERIFIABLE
    entries: list[TreeEntry] = []
    for record in data.split(b"\0")[:-1] if data else ():
        meta, separator, raw_path = record.partition(b"\t")
        fields = meta.split()
        if not separator or not raw_path or len(fields) != 4:
            return WorktreeVerdict.UNVERIFIABLE
        texts = [_decode_text(field) for field in fields]
        if any(text is None for text in texts):
            return WorktreeVerdict.UNVERIFIABLE
        mode, kind, oid, size_text = (str(text) for text in texts)
        if mode not in _REGULAR_MODES or kind != "blob":
            return WorktreeVerdict.UNSUPPORTED_TREE_ENTRY
        if not _HEX40.fullmatch(oid) or not size_text.isdigit():
            return WorktreeVerdict.UNVERIFIABLE
        size = int(size_text)
        if size > _MAX_BLOB_BYTES:
            return WorktreeVerdict.UNSUPPORTED_TREE_ENTRY
        path = _decode_text(raw_path)
        if path is None:
            return WorktreeVerdict.UNSAFE_TREE_PATH
        entries.append(TreeEntry(path=path, mode=mode, oid=oid, size=size))
    decision = decide_tree_paths(
        tuple(entry.path for entry in entries), absolute_prefix_chars=absolute_prefix_chars
    )
    if not decision.allow:
        return WorktreeVerdict.UNSAFE_TREE_PATH
    directories: set[str] = set()
    for entry in entries:
        parts = entry.path.split("/")
        directories.update("/".join(parts[:depth]) for depth in range(1, len(parts)))
    entries.sort(key=lambda entry: entry.path)
    return TreeSnapshot(
        commit=base_commit, entries=tuple(entries), directories=frozenset(directories)
    )


def _blob_oid(content: bytes) -> str:
    """Oid SHA-1 do objeto blob: `sha1(b"blob <tamanho>\\0" + bytes)`."""
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(b"blob %d\0" % len(content))
    digest.update(content)
    return digest.hexdigest()


def _file_oid(fd: int, size: int) -> str:
    """O mesmo oid, em streaming, a partir de um descritor já aberto."""
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(b"blob %d\0" % size)
    remaining = size
    while True:
        chunk = os.read(fd, _HASH_CHUNK)
        if not chunk:
            break
        remaining -= len(chunk)
        digest.update(chunk)
    if remaining != 0:
        raise _Unverifiable("tamanho mudou durante a leitura")
    return digest.hexdigest()


def _blobs_present(git: str, cwd: str, snapshot: TreeSnapshot) -> bool | None:
    """Todo blob do snapshot existe **localmente**, com o tipo e o tamanho esperados?"""
    unique = sorted({(entry.oid, entry.size) for entry in snapshot.entries})
    if not unique:
        return True
    request = b"".join(oid.encode("ascii") + b"\n" for oid, _size in unique)
    result = _run_git(git, cwd, *_OBJECT_READ_OPTIONS, "cat-file", "--batch-check", stdin=request)
    if result is None or result.returncode != 0:
        return None
    expected = b"".join(f"{oid} blob {size}\n".encode("ascii") for oid, size in unique)
    return result.stdout == expected


def _fetch_batch(git: str, cwd: str, batch: list[tuple[str, int]]) -> dict[str, bytes]:
    """Um `cat-file --batch` delimitado. Cabeçalho, tamanho, terminador e **oid recalculado**
    conferidos para cada objeto; qualquer divergência levanta `_Unverifiable`."""
    request = b"".join(oid.encode("ascii") + b"\n" for oid, _size in batch)
    result = _run_git(git, cwd, *_OBJECT_READ_OPTIONS, "cat-file", "--batch", stdin=request)
    if result is None or result.returncode != 0:
        raise _Unverifiable("cat-file --batch falhou")
    data = result.stdout
    position = 0
    contents: dict[str, bytes] = {}
    for oid, size in batch:
        header_end = data.find(b"\n", position)
        if data[position:header_end] != f"{oid} blob {size}".encode("ascii"):
            raise _Unverifiable(f"cabeçalho inesperado para {oid}")
        start = header_end + 1
        end = start + size
        if data[end : end + 1] != b"\n":
            raise _Unverifiable(f"conteúdo truncado para {oid}")
        content = data[start:end]
        if _blob_oid(content) != oid:
            raise _Unverifiable(f"oid não confere para {oid}")
        contents[oid] = content
        position = end + 1
    if position != len(data):
        raise _Unverifiable("saída do cat-file com sobra")
    return contents


def _blob_batches(git: str, cwd: str, snapshot: TreeSnapshot) -> Iterator[dict[str, bytes]]:
    """Os blobs crus em lotes de até `_BATCH_BUDGET_BYTES` — nunca o repositório inteiro."""
    batch: list[tuple[str, int]] = []
    total = 0
    for oid, size in sorted({(entry.oid, entry.size) for entry in snapshot.entries}):
        if batch and total + size > _BATCH_BUDGET_BYTES:
            yield _fetch_batch(git, cwd, batch)
            batch, total = [], 0
        batch.append((oid, size))
        total += size
    if batch:
        yield _fetch_batch(git, cwd, batch)


# ------------------------------------------------------------ verificação raw (D4)


def _index_entries(data: bytes) -> dict[str, tuple[str, str, str]] | None:
    """`ls-files --stage -z`: `"<modo> <oid> <estágio>\\t<caminho>\\0"`. `None` se malformado."""
    entries: dict[str, tuple[str, str, str]] = {}
    if not data:
        return entries
    if not data.endswith(b"\0"):
        return None
    for record in data[:-1].split(b"\0"):
        meta, separator, raw_path = record.partition(b"\t")
        fields = meta.split(b" ")
        path = _decode_text(raw_path)
        if not separator or len(fields) != 3 or path is None:
            return None
        texts = [_decode_text(field) for field in fields]
        if any(text is None for text in texts):
            return None
        mode, oid, stage = (str(text) for text in texts)
        # Mais de um estágio para o mesmo caminho é conflito: diverge do base por construção.
        entries[path] = ("", "", "conflito") if path in entries else (mode, oid, stage)
    return entries


def _index_is_integral(data: bytes) -> bool | None:
    """`ls-files -z -v`: `"<tag> <caminho>\\0"` por entrada. Só `H` é íntegro — nada
    `skip-worktree` (`S`) nem `assume-unchanged` (minúscula). `None` se malformado."""
    if not data:
        return True
    if not data.endswith(b"\0"):
        return None
    integral = True
    for record in data[:-1].split(b"\0"):
        if len(record) < 3 or record[1:2] != b" ":
            return None
        if record[:1] != b"H":
            integral = False
    return integral


def _index_diff_argv(base_commit: str) -> tuple[str, ...]:
    """Árvore do base × **índice**, sem working tree (reauditoria P2-002).

    `ls-files` não expõe as flags de entrada (`--stage`, `-v`, `-t` e `--format` foram medidos:
    uma entrada `intent-to-add` com o oid do blob vazio aparece idêntica a uma normal). Este é
    o plumbing documentado que as enxerga: com `--ita-invisible-in-index`, uma entrada
    `intent-to-add` conta como **ausente** do índice e sai como `D`. `--cached` não olha o
    disco — nada é lido do working tree, então nenhum `clean`/`process` pode rodar —, e a
    saída `--raw` não gera conteúdo de diff: sem textconv, sem diff externo, sem rename
    (e os três ainda vão desligados explicitamente). Medido com drivers maliciosos ativos
    (`filter`, `diff.<driver>.textconv`/`command`, `diff.external`): nenhum processo filho.
    ``--exit-code``: 0 = índice igual à árvore; 1 = difere.
    """
    return (
        *_OBJECT_READ_OPTIONS,
        "diff-index",
        "--cached",
        "--ita-invisible-in-index",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
        "--ignore-submodules=none",
        "--raw",
        "-z",
        "--exit-code",
        f"{base_commit}^{{commit}}",
        "--",
    )


def _index_matches_tree(result: CompletedProcess[bytes] | None) -> bool | None:
    """Leitura estrita do `diff-index`: saída vazia **e** 0 = igual; saída **e** 1 = difere.
    Qualquer outra combinação (erro, Git sem a opção, saída incoerente) é `None`."""
    if result is None:
        return None
    if result.returncode == 0 and result.stdout == b"":
        return True
    if result.returncode == 1 and result.stdout != b"":
        return False
    return None


def _filesystem_mismatch(target: str, snapshot: TreeSnapshot) -> str | None:
    """O primeiro motivo de o filesystem **não** ser exatamente o snapshot, ou `None`.

    Percorre sem seguir link/reparse. Na raiz, só o arquivo `.git` administrativo é tolerado.
    Arquivo ou diretório fora do snapshot (inclusive ignorado ou vazio), link, tipo inesperado,
    tamanho, oid dos bytes crus ou — no POSIX — bit de execução diferente: sujo.
    `_Unverifiable` quando um IO não permite concluir.
    """
    expected = snapshot.by_path()
    seen: set[str] = set()
    pending: list[tuple[str, str]] = [("", target)]
    while pending:
        relative_dir, absolute_dir = pending.pop()
        try:
            with os.scandir(absolute_dir) as iterator:
                children = list(iterator)
            infos = [(child, os.lstat(child.path)) for child in children]
        except OSError as exc:
            raise _Unverifiable(absolute_dir) from exc
        for child, info in infos:
            relative = f"{relative_dir}/{child.name}" if relative_dir else child.name
            if _is_reparse(info):
                return f"link ou reparse point: {relative}"
            if relative == ".git":
                if not stat.S_ISREG(info.st_mode):
                    return "`.git` administrativo não é arquivo"
                continue
            if stat.S_ISDIR(info.st_mode):
                if relative not in snapshot.directories:
                    return f"diretório fora do base: {relative}"
                pending.append((relative, child.path))
                continue
            spec = expected.get(relative)
            if spec is None:
                return f"arquivo fora do base: {relative}"
            if not stat.S_ISREG(info.st_mode):
                return f"tipo inesperado: {relative}"
            if info.st_size != spec.size:
                return f"conteúdo difere: {relative}"
            if _POSIX_MODES and bool(info.st_mode & stat.S_IXUSR) != spec.executable:
                return f"modo difere: {relative}"
            if _hash_path(child.path, spec.size) != spec.oid:
                return f"conteúdo difere: {relative}"
            seen.add(relative)
    missing = expected.keys() - seen
    if missing:
        return f"arquivo ausente: {min(missing)}"
    return None


def _hash_path(path: str, size: int) -> str:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise _Unverifiable(path) from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size != size:
            return ""
        return _file_oid(fd, size)
    except OSError as exc:
        raise _Unverifiable(path) from exc
    finally:
        os.close(fd)


def _contents_mismatch(git: str, target: str, snapshot: TreeSnapshot) -> str | None:
    """Índice **e** filesystem contra o snapshot. `None` = exatamente o base. Levanta
    `_Unverifiable` se não deu para ler. Nenhum comando aqui consulta atributos ou filtros:
    `ls-files` e `diff-index --cached` leem só índice e objetos, e o conteúdo do disco é
    hasheado no Python.

    "Índice == base" é estrutura **e** flags: caminho, modo, oid e estágio exatos
    (`ls-files --stage`); nenhuma entrada `skip-worktree`/`assume-unchanged` (`ls-files -v`);
    nenhuma `intent-to-add` nem outra diferença que o Git veja entre índice e árvore
    (`diff-index`, `_index_diff_argv`)."""
    stage = _run_git(git, target, "--no-pager", "ls-files", "--stage", "-z")
    flags = _run_git(git, target, "--no-pager", "ls-files", "-v", "-z")
    if stage is None or stage.returncode != 0 or flags is None or flags.returncode != 0:
        raise _Unverifiable("ls-files falhou")
    index = _index_entries(stage.stdout)
    integral = _index_is_integral(flags.stdout)
    if index is None or integral is None:
        raise _Unverifiable("índice ilegível")
    wanted = {entry.path: (entry.mode, entry.oid, "0") for entry in snapshot.entries}
    if index != wanted:
        return "índice difere do base (staged, conflito ou modo)"
    if not integral:
        return "índice com skip-worktree ou assume-unchanged"
    matches = _index_matches_tree(_run_git(git, target, *_index_diff_argv(snapshot.commit)))
    if matches is None:
        raise _Unverifiable("diff-index do índice ilegível")
    if not matches:
        return "índice difere do base (intent-to-add ou outra flag de entrada)"
    return _filesystem_mismatch(target, snapshot)


# ------------------------------------------------------------------- fatos da task


@dataclass(frozen=True, slots=True)
class WorktreeInterior:
    """O que o Git responde **de dentro** do alvo, mais a verificação raw do conteúdo."""

    toplevel: str
    git_dir: str
    git_common_dir: str
    #: `None` = detached.
    head_ref: str | None
    head: str | None
    #: `None` = índice e filesystem são exatamente o `base_commit`; senão, o motivo.
    contents_mismatch: str | None
    operations_in_progress: tuple[str, ...]


#: Marcadores de operação em andamento no git dir **da worktree** (não no comum).
_IN_PROGRESS_MARKERS = (
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "REBASE_HEAD",
    "BISECT_LOG",
    "rebase-merge",
    "rebase-apply",
    "sequencer",
)


def _inspect_interior(git: str, target: str, snapshot: TreeSnapshot) -> WorktreeInterior | None:
    paths = _run_git(
        git,
        target,
        "rev-parse",
        "--path-format=absolute",
        "--show-toplevel",
        "--absolute-git-dir",
        "--git-common-dir",
    )
    lines = _exact_lines(paths, expected=3)
    if lines is None:
        return None
    toplevel, git_dir, common = (_norm(line) for line in lines)

    symbolic = _run_git(git, target, "symbolic-ref", "--quiet", "HEAD")
    if symbolic is None or symbolic.returncode not in (0, 1):
        return None
    head_ref: str | None = None
    if symbolic.returncode == 0:
        text = _decode_text(symbolic.stdout)
        if text is None or not text.endswith("\n"):
            return None
        head_ref = text[:-1]

    head_result = _run_git(git, target, "rev-parse", "--verify", "--quiet", "HEAD")
    if head_result is None or head_result.returncode != 0:
        return None
    head_text = _decode_text(head_result.stdout)
    if head_text is None or not _HEX40.fullmatch(head_text.strip()):
        return None

    try:
        mismatch = _contents_mismatch(git, target, snapshot)
        markers = tuple(
            name for name in _IN_PROGRESS_MARKERS if _lexists(os.path.join(git_dir, name))
        )
    except _Unverifiable:
        return None

    return WorktreeInterior(
        toplevel=toplevel,
        git_dir=git_dir,
        git_common_dir=common,
        head_ref=head_ref,
        head=head_text.strip(),
        contents_mismatch=mismatch,
        operations_in_progress=markers,
    )


def _identity(info: os.stat_result) -> ObjectIdentity:
    return ObjectIdentity(volume_id=info.st_dev, file_id=info.st_ino)


_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _is_reparse(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE)


def _root_state(root: WorktreeRoot, layout: RepositoryLayout) -> RootState:
    """A `WorktreeRoot` ainda é a mesma e ainda é deste repositório? Só identidade — a
    política já foi decidida em `safety.decide_worktree_root` (D7)."""
    try:
        info = os.lstat(root.canonical_path)
        toplevel = os.stat(layout.toplevel)
        common = os.stat(layout.git_common_dir)
    except (FileNotFoundError, NotADirectoryError):
        return RootState.INVALID
    except (OSError, ValueError):
        return RootState.UNKNOWN
    if not stat.S_ISDIR(info.st_mode) or _is_reparse(info) or _identity(info) != root.identity:
        return RootState.INVALID
    if _identity(toplevel) != root.repository_toplevel_identity:
        return RootState.INVALID
    if _identity(common) != root.git_common_dir_identity:
        return RootState.INVALID
    return RootState.OK


def _target_kind(target: str) -> TargetKind:
    try:
        info = os.lstat(target)
    except (FileNotFoundError, NotADirectoryError):
        return TargetKind.ABSENT
    except (OSError, ValueError):
        return TargetKind.UNKNOWN
    if _is_reparse(info):
        return TargetKind.REPARSE
    if not stat.S_ISDIR(info.st_mode):
        return TargetKind.FILE
    try:
        canonical = os.path.realpath(target, strict=True)
    except (OSError, ValueError):
        return TargetKind.UNKNOWN
    if not _same_path(canonical, target):
        return TargetKind.REPARSE  # algum componente foi redirecionado desde a validação
    return TargetKind.DIRECTORY


def _dotgit_link(target: str, admin: tuple[WorktreeAdminEntry, ...]) -> LinkState:
    """`<alvo>/.git` é um arquivo `gitdir: <admin>` apontando para o admin que o registra?"""
    dotgit = os.path.join(target, ".git")
    try:
        info = os.lstat(dotgit)
    except (FileNotFoundError, NotADirectoryError):
        return LinkState.BROKEN
    except OSError:
        return LinkState.UNKNOWN
    if stat.S_ISDIR(info.st_mode):
        return LinkState.REPOSITORY
    if not stat.S_ISREG(info.st_mode) or _is_reparse(info):
        return LinkState.BROKEN
    try:
        content = _read_text_file(dotgit)
    except _Unverifiable:
        return LinkState.UNKNOWN
    if content is None:
        return LinkState.BROKEN
    line = _single_line(content)
    if not line.startswith("gitdir: "):
        return LinkState.BROKEN
    pointed = line[len("gitdir: ") :]
    pointed = _norm(pointed if os.path.isabs(pointed) else os.path.join(target, pointed))
    if len(admin) != 1 or not _same_path(pointed, admin[0].path):
        return LinkState.BROKEN
    return LinkState.CONSISTENT


@dataclass(frozen=True, slots=True)
class TaskWorktreeFacts:
    """Tudo o que `classify_task_worktree` precisa. Nenhuma decisão embutida."""

    names: TaskWorktreeNames
    base_commit: str
    repository: RepositoryState
    layout: RepositoryLayout | None = None
    base_is_commit: Tri = Tri.UNKNOWN
    root: RootState = RootState.UNKNOWN
    target_path: str | None = None
    target: TargetKind = TargetKind.UNKNOWN
    branch_known: bool = False
    branch_tip: str | None = None
    inventory: WorktreeInventory | None = None
    link: LinkState = LinkState.NOT_APPLICABLE
    #: Só quando a verificação do conteúdo foi tentada e a árvore do base foi recusada.
    snapshot_verdict: WorktreeVerdict | None = None
    interior: WorktreeInterior | None = None


def _absolute_prefix_chars(target: str) -> int | None:
    """No Windows, o comprimento do alvo conta para `MAX_PATH` (`decide_tree_paths`)."""
    return len(target) if sys.platform == "win32" else None


def _verify_base(git: str, local_path: str, base_commit: str) -> Tri:
    """O SHA aprovado é um **commit** acessível? Nunca consulta `HEAD` para decidir a base."""
    result = _run_git(
        git,
        local_path,
        "rev-parse",
        "--verify",
        "--quiet",
        "--end-of-options",
        f"{base_commit}^{{commit}}",
    )
    if result is None:
        return Tri.UNKNOWN
    if result.returncode == 1:
        return Tri.FALSE
    if result.returncode != 0:
        return Tri.UNKNOWN
    text = _decode_text(result.stdout)
    if text is None:
        return Tri.UNKNOWN
    return Tri.of(text.strip() == base_commit)


def _branch_tip(git: str, local_path: str, branch_ref: str) -> tuple[bool, str | None]:
    result = _run_git(
        git, local_path, "rev-parse", "--verify", "--quiet", "--end-of-options", branch_ref
    )
    if result is None or result.returncode not in (0, 1):
        return False, None
    if result.returncode == 1:
        return True, None
    text = _decode_text(result.stdout)
    if text is None or not _HEX40.fullmatch(text.strip()):
        return False, None
    return True, text.strip()


def _collect(
    git: str, local_path: str, names: TaskWorktreeNames, base_commit: str, root: WorktreeRoot
) -> TaskWorktreeFacts:
    well_formed = isinstance(base_commit, str) and bool(_BASE_COMMIT_RE.fullmatch(base_commit))
    state, layout = _probe_repository(git, local_path)
    if not well_formed:
        # Nada com um valor fora do formato entra em argv nenhum: nem o `rev-parse`.
        return TaskWorktreeFacts(
            names=names,
            base_commit=str(base_commit),
            repository=state,
            layout=layout,
            base_is_commit=Tri.FALSE,
        )
    facts = TaskWorktreeFacts(names=names, base_commit=base_commit, repository=state, layout=layout)
    if state is not RepositoryState.OK or layout is None:
        return facts

    base_is_commit = _verify_base(git, local_path, base_commit)
    root_state = _root_state(root, layout)
    target = os.path.join(root.canonical_path, names.directory_name)
    kind = _target_kind(target)
    branch_known, tip = _branch_tip(git, local_path, names.branch_ref)
    inventory = _inventory(git, local_path, layout.git_common_dir)

    link = LinkState.NOT_APPLICABLE
    snapshot_verdict: WorktreeVerdict | None = None
    interior: WorktreeInterior | None = None
    if kind is TargetKind.DIRECTORY and inventory is not None:
        link = _dotgit_link(target, inventory.admin_for(target))
        if link is LinkState.CONSISTENT and inventory.record_at(target) is not None:
            snapshot = _read_snapshot(
                git, local_path, base_commit, absolute_prefix_chars=_absolute_prefix_chars(target)
            )
            if isinstance(snapshot, WorktreeVerdict):
                snapshot_verdict = snapshot
            else:
                interior = _inspect_interior(git, target, snapshot)

    return TaskWorktreeFacts(
        names=names,
        base_commit=base_commit,
        repository=state,
        layout=layout,
        base_is_commit=base_is_commit,
        root=root_state,
        target_path=target,
        target=kind,
        branch_known=branch_known,
        branch_tip=tip,
        inventory=inventory,
        link=link,
        snapshot_verdict=snapshot_verdict,
        interior=interior,
    )


def inspect_task_worktree(
    local_path: str, *, base_commit: str, task_id: str, root: WorktreeRoot
) -> TaskWorktreeFacts:
    """Fatos sobre a worktree da task. **Só leitura**; nunca lança por IO."""
    names = task_worktree_names(task_id)
    _require_root(root)
    git = shutil.which("git")
    if git is None:
        return TaskWorktreeFacts(
            names=names, base_commit=str(base_commit), repository=RepositoryState.UNVERIFIABLE
        )
    return _collect(git, local_path, names, base_commit, root)


def _require_root(root: object) -> None:
    if not isinstance(root, WorktreeRoot):
        raise InvalidWorktreeRequest("root precisa ser uma WorktreeRoot validada pelo path_runtime")


# ---------------------------------------------------------------------- classificação


def classify_task_worktree(facts: TaskWorktreeFacts) -> WorktreeVerdict:
    """Veredito **puro** sobre os fatos. Na dúvida, recusa.

    `REUSABLE` exige **todas**: alvo registrado no caminho correto, não `locked`, não
    `prunable`; `.git` interno consistente com o admin dir; toplevel, git dir e `.git` comum
    esperados; `HEAD` ligado a `refs/heads/ff/task-<id8>`; `HEAD == ponta == base`; índice e
    filesystem **exatamente** o `base_commit` pela verificação raw (inclusive ignorados, sem
    `git status`); nenhuma operação em andamento; raiz íntegra.
    """
    if facts.repository is RepositoryState.NOT_A_REPO:
        return WorktreeVerdict.NOT_A_REPO
    if facts.base_is_commit.is_false:
        return WorktreeVerdict.BASE_INVALID
    if facts.repository is not RepositoryState.OK or facts.layout is None:
        return WorktreeVerdict.UNVERIFIABLE
    if facts.base_is_commit.is_unknown:
        return WorktreeVerdict.UNVERIFIABLE
    if facts.root is RootState.INVALID:
        return WorktreeVerdict.ROOT_INVALID
    inventory = facts.inventory
    target = facts.target_path
    if (
        facts.root is not RootState.OK
        or target is None
        or inventory is None
        or not facts.branch_known
        or facts.target is TargetKind.UNKNOWN
    ):
        return WorktreeVerdict.UNVERIFIABLE

    names = facts.names
    base = facts.base_commit
    record = inventory.record_at(target)
    related = inventory.related_admin(names.directory_name)
    holders = inventory.branch_holders(names.branch_ref)

    if record is not None and record.locked:
        return WorktreeVerdict.LOCKED

    if facts.target is TargetKind.ABSENT:
        if record is not None or related:
            return WorktreeVerdict.STALE_METADATA
        if holders:
            return WorktreeVerdict.BRANCH_BUSY
        if facts.branch_tip is not None:
            if facts.branch_tip != base:
                return WorktreeVerdict.BRANCH_DIVERGED
            return WorktreeVerdict.BRANCH_ORPHANED
        return WorktreeVerdict.ABSENT

    if facts.target is not TargetKind.DIRECTORY or record is None:
        return WorktreeVerdict.FOREIGN_PATH
    if record.prunable:
        return WorktreeVerdict.STALE_METADATA
    if facts.link is LinkState.UNKNOWN:
        return WorktreeVerdict.UNVERIFIABLE
    if facts.link is not LinkState.CONSISTENT:
        return WorktreeVerdict.BROKEN_LINK
    own = inventory.admin_for(target)
    if len(own) != 1 or any(entry not in own for entry in related) or own[0].locked:
        return WorktreeVerdict.STALE_METADATA

    if facts.snapshot_verdict is not None:
        return facts.snapshot_verdict
    interior = facts.interior
    if interior is None:
        return WorktreeVerdict.UNVERIFIABLE
    if not (
        _same_path(interior.toplevel, target)
        and _same_path(interior.git_common_dir, facts.layout.git_common_dir)
        and _same_path(interior.git_dir, own[0].path)
    ):
        return WorktreeVerdict.BROKEN_LINK
    if (
        record.detached
        or record.branch_ref != names.branch_ref
        or interior.head_ref != names.branch_ref
        or len(holders) != 1
    ):
        return WorktreeVerdict.HEAD_MISMATCH
    # Ligado à branch certa: se a ponta andou (commit humano na worktree), é divergência da
    # branch; um `HEAD` diferente da ponta com a branch na base é incoerência de `HEAD`.
    if facts.branch_tip != base:
        return WorktreeVerdict.BRANCH_DIVERGED
    if interior.head != base or record.head != base:
        return WorktreeVerdict.HEAD_MISMATCH
    if interior.operations_in_progress:
        return WorktreeVerdict.DIRTY
    if interior.contents_mismatch is not None:
        return WorktreeVerdict.DIRTY
    return WorktreeVerdict.REUSABLE


# ------------------------------------------------------------------------ criação

#: Prazo de cada passo Git mutante (`add --no-checkout`, `read-tree`) sob supervisão.
_GIT_STEP_TIMEOUT_S = 120.0
_MAX_OUTPUT_BYTES = 64 * 1024

#: Endurecimento **por comando** das operações mutantes (D2). Nada é persistido. Filtros e
#: atributos não aparecem aqui porque nenhum dos dois comandos materializa conteúdo.
_MUTATING_GIT_OPTIONS = (
    *_OBJECT_READ_OPTIONS,
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "submodule.recurse=false",
    "-c",
    "branch.autoSetupMerge=false",
    "-c",
    "worktree.useRelativePaths=false",
    "-c",
    "core.sparseCheckout=false",
    "-c",
    "core.sparseCheckoutCone=false",
)


def _add_argv(
    git: str, local_path: str, names: TaskWorktreeNames, target: str, base_commit: str
) -> tuple[str, ...]:
    """Mutante 1/2: branch, metadata e `.git` da worktree — **sem checkout** (`--no-checkout`).
    Start-point `<sha>^{commit}`, nunca SHA cru."""
    return (
        git,
        *_MUTATING_GIT_OPTIONS,
        "-C",
        local_path,
        "worktree",
        "add",
        "--no-checkout",
        "--no-track",
        "--no-guess-remote",
        "-b",
        names.branch,
        "--",
        target,
        f"{base_commit}^{{commit}}",
    )


def _read_tree_argv(git: str, target: str, base_commit: str) -> tuple[str, ...]:
    """Mutante 2/2: o índice da worktree nova recebe o base inteiro. **Sem `-u`**: nenhum
    arquivo é escrito pelo Git; `--no-sparse-checkout`: sparse herdado não recorta nada."""
    return (
        git,
        *_MUTATING_GIT_OPTIONS,
        "-C",
        target,
        "read-tree",
        "--no-sparse-checkout",
        f"{base_commit}^{{commit}}",
    )


def _run_git_step(
    argv: tuple[str, ...], cwd: str, is_cancelled: Callable[[], bool]
) -> ProcessResult | None:
    """O **único** ponto que roda Git mutante: sob `process_runtime` (adendo E7.4)."""
    try:
        spec = ProcessSpec(
            argv=argv,
            cwd=cwd,
            env=_git_env(),
            timeout_s=_GIT_STEP_TIMEOUT_S,
            max_stdout_bytes=_MAX_OUTPUT_BYTES,
            max_stderr_bytes=_MAX_OUTPUT_BYTES,
        )
    except InvalidProcessSpec:
        return None
    return run_supervised(spec, is_cancelled)


def _step_ok(result: ProcessResult | None) -> bool:
    return (
        result is not None
        and result.outcome is ProcessOutcome.EXITED
        and result.exit_code == 0
        and result.tree_confirmed_dead
    )


@dataclass(frozen=True, slots=True)
class WorktreeOutcome:
    """Resultado de `create_worktree`. Fatos, para o Execution Manager persistir."""

    verdict: WorktreeVerdict
    created: bool
    reused: bool
    names: TaskWorktreeNames
    path: str | None
    #: `<worktree>/<prefixo do workspace>`: onde o workspace da task vive dentro da worktree.
    workspace_path: str | None
    head: str | None
    #: Do último passo Git mutante executado (`add` ou `read-tree`).
    process_outcome: ProcessOutcome | None = None
    exit_code: int | None = None
    tree_confirmed_dead: bool | None = None
    detail: str | None = None


def _outcome(
    facts: TaskWorktreeFacts,
    verdict: WorktreeVerdict,
    *,
    created: bool = False,
    reused: bool = False,
    result: ProcessResult | None = None,
    detail: str | None = None,
) -> WorktreeOutcome:
    path = facts.target_path
    usable = verdict is WorktreeVerdict.REUSABLE and path is not None and facts.layout is not None
    workspace_path: str | None = None
    if usable and path is not None and facts.layout is not None:
        prefix = facts.layout.workspace_prefix.rstrip("/")
        workspace_path = _norm(os.path.join(path, *prefix.split("/"))) if prefix else path
    return WorktreeOutcome(
        verdict=verdict,
        created=created,
        reused=reused,
        names=facts.names,
        path=path,
        workspace_path=workspace_path,
        head=facts.interior.head if facts.interior is not None else None,
        process_outcome=None if result is None else result.outcome,
        exit_code=None if result is None else result.exit_code,
        tree_confirmed_dead=None if result is None else result.tree_confirmed_dead,
        detail=detail,
    )


def _never_cancelled() -> bool:
    return False


def _asked_to_cancel(is_cancelled: Callable[[], bool]) -> bool:
    """Consulta entre arquivos. Resposta que não é `bool`, ou exceção, conta como cancelar."""
    try:
        answer = is_cancelled()
    except Exception:
        return True
    return answer is not False


def _materialize(
    git: str,
    local_path: str,
    target: str,
    root: WorktreeRoot,
    snapshot: TreeSnapshot,
    tree_writer: TreeWriterFactory,
    is_cancelled: Callable[[], bool],
) -> str | None:
    """Escreve o snapshot a partir dos blobs crus. `None` = tudo escrito; senão, o motivo.
    Interrompe ao primeiro problema e **não apaga** nada do que já foi escrito."""
    try:
        if sorted(os.listdir(target)) != [".git"]:
            return "a worktree nova não está vazia depois do add"
        parent = os.lstat(root.canonical_path)
        info = os.lstat(target)
    except OSError as exc:
        return f"alvo ilegível: {type(exc).__name__}"
    if _identity(parent) != root.identity or not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
        return "raiz ou alvo mudaram antes da materialização"
    by_oid: dict[str, list[TreeEntry]] = {}
    for entry in snapshot.entries:
        by_oid.setdefault(entry.oid, []).append(entry)
    try:
        writer = tree_writer(target, _identity(info))
        for batch in _blob_batches(git, local_path, snapshot):
            for oid, content in batch.items():
                for entry in by_oid[oid]:
                    if _asked_to_cancel(is_cancelled):
                        return "cancelado durante a materialização"
                    writer.write_file(entry.path, content, executable=entry.executable)
    except (OSError, _Unverifiable) as exc:
        return f"materialização falhou: {type(exc).__name__}"
    return None


def create_worktree(
    local_path: str,
    *,
    base_commit: str,
    task_id: str,
    root: WorktreeRoot,
    tree_writer: TreeWriterFactory,
    is_cancelled: Callable[[], bool] | None = None,
) -> WorktreeOutcome:
    """Cria (ou reconhece para reuso) a worktree da task. Idempotente e fail closed.

    1. Inspeciona e classifica. `REUSABLE` → devolve com `reused=True`; qualquer veredito
       diferente de `ABSENT` → recusa, sem tocar em nada.
    2. `ABSENT` → lê e valida o snapshot inteiro do base e confere que todo blob existe
       localmente — **antes** de qualquer mutação. Recusa: `UNSUPPORTED_TREE_ENTRY`,
       `UNSAFE_TREE_PATH` ou `UNVERIFIABLE`.
    3. `worktree add --no-checkout` e `read-tree` sem `-u`, cada um sob `process_runtime`.
    4. Materializa os blobs crus pelo ``tree_writer`` (o `CheckedTreeWriter` do
       `path_runtime`), consultando ``is_cancelled`` entre arquivos.
    5. **Sempre** reinspeciona com a mesma verificação do reuso. `created=True` só se tudo
       deu certo **e** o resultado é `REUSABLE`; senão `CREATED_INVALID` (ou a recusa
       conservadora observada), com o que sobrou **preservado**.
    """
    names = task_worktree_names(task_id)
    _require_root(root)
    if not callable(tree_writer):
        raise InvalidWorktreeRequest("tree_writer precisa ser o TreeWriterFactory do path_runtime")
    cancelled = is_cancelled if is_cancelled is not None else _never_cancelled
    git = shutil.which("git")
    if git is None:
        facts = TaskWorktreeFacts(
            names=names, base_commit=str(base_commit), repository=RepositoryState.UNVERIFIABLE
        )
        return _outcome(facts, WorktreeVerdict.UNVERIFIABLE, detail="git ausente do PATH")

    before = _collect(git, local_path, names, base_commit, root)
    verdict = classify_task_worktree(before)
    if verdict is WorktreeVerdict.REUSABLE:
        return _outcome(before, verdict, reused=True)
    if verdict is not WorktreeVerdict.ABSENT:
        return _outcome(before, verdict)

    assert before.target_path is not None and before.layout is not None
    target = before.target_path
    snapshot = _read_snapshot(
        git, local_path, base_commit, absolute_prefix_chars=_absolute_prefix_chars(target)
    )
    if isinstance(snapshot, WorktreeVerdict):
        return _outcome(
            before, snapshot, detail="árvore do base recusada antes de qualquer escrita"
        )
    if _blobs_present(git, local_path, snapshot) is not True:
        return _outcome(
            before, WorktreeVerdict.UNVERIFIABLE, detail="blob ausente localmente (sem lazy fetch)"
        )
    # Última reconferência antes de agir: a janela TOCTOU encolhe, não fecha ([04] §4).
    if _root_state(root, before.layout) is not RootState.OK:
        return _outcome(before, WorktreeVerdict.ROOT_INVALID)
    if _target_kind(target) is not TargetKind.ABSENT:
        return _outcome(before, WorktreeVerdict.FOREIGN_PATH)

    added = _run_git_step(
        _add_argv(git, local_path, names, target, base_commit), local_path, cancelled
    )
    if not _step_ok(added):
        after = _collect(git, local_path, names, base_commit, root)
        after_verdict = classify_task_worktree(after)
        if added is not None and not added.tree_confirmed_dead:
            final = WorktreeVerdict.UNVERIFIABLE
        elif after_verdict is WorktreeVerdict.ABSENT:
            final = WorktreeVerdict.UNVERIFIABLE  # nada criado, e o motivo é desconhecido
        elif after_verdict is WorktreeVerdict.REUSABLE:
            final = WorktreeVerdict.CREATED_INVALID
        else:
            final = after_verdict
        outcome = "ProcessSpec inválida" if added is None else added.outcome.value
        return _outcome(after, final, result=added, detail=f"git worktree add: {outcome}")

    indexed = _run_git_step(_read_tree_argv(git, target, base_commit), target, cancelled)
    problem: str | None
    if not _step_ok(indexed):
        outcome = "ProcessSpec inválida" if indexed is None else indexed.outcome.value
        problem = f"git read-tree: {outcome}"
    else:
        problem = _materialize(git, local_path, target, root, snapshot, tree_writer, cancelled)

    after = _collect(git, local_path, names, base_commit, root)
    after_verdict = classify_task_worktree(after)
    last = indexed if indexed is not None else added
    if problem is None and after_verdict is WorktreeVerdict.REUSABLE:
        return _outcome(after, WorktreeVerdict.REUSABLE, created=True, result=last)
    reason = problem if problem is not None else f"pós-condição falhou: {after_verdict.value}"
    return _outcome(after, WorktreeVerdict.CREATED_INVALID, result=last, detail=reason)


__all__ = [
    "BRANCH_PREFIX",
    "DIRECTORY_PREFIX",
    "InvalidWorktreeRequest",
    "LinkState",
    "RepositoryLayout",
    "RepositoryState",
    "RootState",
    "TargetKind",
    "TaskWorktreeFacts",
    "TaskWorktreeNames",
    "TreeEntry",
    "TreeSnapshot",
    "WorktreeAdminEntry",
    "WorktreeInterior",
    "WorktreeInventory",
    "WorktreeOutcome",
    "WorktreeRecord",
    "WorktreeVerdict",
    "classify_task_worktree",
    "create_worktree",
    "inspect_task_worktree",
    "list_worktrees",
    "repository_layout",
    "task_worktree_names",
]
