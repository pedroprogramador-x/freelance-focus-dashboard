"""Git **mediado** do Developer (E7.5-D): as leituras por trás de `GitStatus`, `GitDiff`, `GitShow`
e `GitListTree`.

Este módulo é o **único** dono do que essas operações pedem ao Git: resolve o executável, forma
o `argv`, fixa o ambiente e as opções, executa sob o Supervisor (`process_runtime`) e interpreta
a saída estrutural. Quem chama (`tool_executor`) recebe **fatos tipados** — nunca `argv`, nunca
saída crua, nunca `stderr` — e decide política, redação e o `ToolResult`. Nenhum parâmetro daqui
aceita `argv`, flag, subcomando, config ou ambiente vindos de fora: só um commit **já validado**,
um oid **já devolvido pelo próprio Git** ou uma ref tipada que vira um `<ref>^{commit}` atrás de
`--end-of-options`.

## O Git nunca lê o conteúdo da worktree

Nenhum comando daqui compara arquivos da worktree com o índice ou com um commit (`status`,
`diff`, `diff-files`, `diff-index` sem `--cached`): esses comandos podem rodar o *clean filter*
(e `process`) configurado no repositório — inclusive para uma entrada "racily clean" cujo stat
coincide. A mesma decisão do adendo E7.4: o pipeline de conversão de working tree do Git fica
de fora. O Git responde só sobre **objetos e índice**:

| Comando | Para quê |
| --- | --- |
| `rev-parse --show-object-format --show-prefix` | formato dos oids e prefixo do workspace |
| `rev-parse --verify --quiet --end-of-options <ref>^{commit}` | ref tipada → commit completo |
| `merge-base --is-ancestor <commit> <base>` | o commit está no histórico do `base_commit`? |
| `ls-tree -r -z -l --full-tree <commit>^{commit}` | árvore (modo, tipo, oid, tamanho) |
| `ls-files --stage -z --full-name` | o índice (lê só o índice) |
| `diff-index --cached --ita-invisible-in-index --raw -z …` | árvore × índice, com intent-to-add |
| `ls-files --others --exclude-standard -z --full-name` | não rastreados (sem ler conteúdo) |
| `cat-file blob <oid>` / `cat-file commit <oid>` | objeto cru, oid **recalculado** |

O conteúdo atual dos arquivos é lido por `tool_executor` pelas primitivas do `path_runtime`, e
a comparação é feita em Python sobre os bytes crus.

O `diff-index` é **o mesmo** da verificação de reuso da E7.4 (`worktree._index_diff_argv`, dono
único do literal): `--cached` (árvore × índice, nunca o disco), `--raw -z` (nenhum conteúdo de
diff), `--no-ext-diff --no-textconv --no-renames`. Existe porque `ls-files --stage` não expõe as
flags de entrada: uma entrada *intent-to-add* com o oid do blob vazio sai idêntica a uma normal
(D-AUD-001); com `--ita-invisible-in-index` ela conta como ausente e aparece.

## Invariantes do índice e da árvore (D-AUD-002)

Nenhum mapa por caminho é montado antes de validar: no índice, cada caminho tem **exatamente uma**
entrada de estágio 0, **ou** só estágios de conflito (1/2/3) sem repetição; na árvore, cada caminho
aparece uma vez. Qualquer outra forma (duplicata, mesmo idêntica; estágio 0 com conflito) é
`GIT_OUTPUT_UNVERIFIABLE` — nunca *last-write-wins*.

## Cancelamento (D-AUD-004)

O token é consultado antes do processo, durante (pelo Supervisor) e **depois** que ele devolve:
um cancelamento visto na volta vence qualquer desfecho, inclusive `EXITED` com saída completa.

## Opções e ambiente

`_READONLY_GIT_OPTIONS` (o mesmo `-c` já auditado das leituras E3/E4) mais: `--no-pager`,
`--no-lazy-fetch` (num partial clone, objeto ausente é erro — nunca rede), `--no-replace-objects`
(`refs/replace` não troca o objeto pedido), `protocol.allow=never`, `credential.helper=` (vazio),
`core.hooksPath=/dev/null`, `core.untrackedCache=false`, `gc.auto=0` e `maintenance.auto=false`.
Ambiente: o mesmo `_git_env` mínimo (`GIT_OPTIONAL_LOCKS=0`, `GIT_TERMINAL_PROMPT=0`,
`GIT_NO_LAZY_FETCH=1`; nenhum `GIT_*` herdado). Nenhum subcomando daqui é *alias* (subcomandos
embutidos têm precedência) nem escreve no repositório.

## Captura limitada

`stdout` estrutural é capturado até ``capture_bytes`` (4 MiB na V1, `git_internal_capture_bytes`);
passou disso, `GIT_OUTPUT_UNVERIFIABLE` — **nunca** se interpreta um prefixo como resposta
completa. `stderr` é limitado e descartado. Blob é lido com o tamanho já conhecido pela árvore.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

from app.git_runtime import _READONLY_GIT_OPTIONS, _git_env, _is_representable, _strip_prefix_bytes
from app.git_runtime.worktree import _index_diff_argv
from app.process_runtime import (
    InvalidProcessSpec,
    ProcessOutcome,
    ProcessResult,
    ProcessSpec,
    run_supervised,
)

#: Opções fixas de **toda** invocação mediada, além de `_READONLY_GIT_OPTIONS`.
_MEDIATED_GIT_OPTIONS = (
    "--no-pager",
    "--no-lazy-fetch",
    "--no-replace-objects",
    *_READONLY_GIT_OPTIONS,
    "-c",
    "protocol.allow=never",
    "-c",
    "credential.helper=",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "gc.auto=0",
    "-c",
    "maintenance.auto=false",
)

#: Prazo **técnico** de cada processo Git mediado (não é limite normativo).
TIMEOUT_S = 20.0
#: `stderr` é só diagnóstico interno, nunca entregue; limitado e descartado.
_STDERR_BYTES = 64 * 1024

_OID_RE = {"sha1": re.compile(rb"[0-9a-f]{40}"), "sha256": re.compile(rb"[0-9a-f]{64}")}
_REGULAR_MODES = frozenset((b"100644", b"100755"))
_SYMLINK_MODE = b"120000"
_GITLINK_MODE = b"160000"
#: Medido: num partial clone, `ls-tree -l` sai com código 0 e tamanho `BAD` para o blob que não
#: está no repositório local (e não busca: `--no-lazy-fetch`).
_MISSING_SIZE = b"BAD"
#: Registro `--raw` sem rename: `:<modo> <modo> <oid> <oid> <status>`.
_RAW_META = re.compile(rb":[0-7]{6} [0-7]{6} [0-9a-f]{4,64} [0-9a-f]{4,64} [ADMTUX]")


class GitFailure(str, Enum):
    """Por que uma leitura mediada não respondeu. **Técnico**: nenhuma é decisão de política."""

    UNAVAILABLE = "git_unavailable"
    TIMEOUT = "git_timeout"
    CANCELLED = "cancelled"
    OUTPUT_UNVERIFIABLE = "git_output_unverifiable"
    REF_NOT_FOUND = "git_ref_not_found"
    OBJECT_UNAVAILABLE = "git_object_unavailable"
    FAILED = "git_failed"


class MediatedGitError(Exception):
    """Falha técnica de uma leitura mediada. A mensagem é só o código — nunca `stderr`."""

    def __init__(self, failure: GitFailure) -> None:
        super().__init__(failure.value)
        self.failure = failure


def _fail(failure: GitFailure) -> MediatedGitError:
    return MediatedGitError(failure)


class EntryKind(str, Enum):
    FILE = "file"
    SYMLINK = "symlink"
    GITLINK = "gitlink"
    #: `ls-files --others` nomeia um repositório aninhado como diretório (`sub/`).
    NESTED_REPOSITORY = "nested_repository"


@dataclass(frozen=True, slots=True)
class TreeEntry:
    """Uma entrada de `ls-tree -r`, **relativa ao workspace**. ``size`` só para blob."""

    path: str
    mode: str
    kind: EntryKind
    oid: str
    size: int | None


@dataclass(frozen=True, slots=True)
class IndexEntry:
    path: str
    mode: str
    kind: EntryKind
    oid: str
    stage: int


_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class Listing(Generic[_T]):
    """Tipo-produto (como `TreeListing` da E4): o que foi nomeado **e** os bytes, relativos ao
    workspace, de caminhos de dentro que o vocabulário do `ToolExecutor` não sabe nomear (UTF-8
    inválido ou `\\` literal). Quem chama decide se algum deles cai no escopo pedido."""

    entries: tuple[_T, ...]
    unrepresentable: tuple[bytes, ...]


@dataclass(frozen=True, slots=True)
class CommitInfo:
    """Os campos de um objeto commit que o `GitShow` mostra. Texto ainda **em bytes**: quem
    chama decodifica (UTF-8 estrito) e decide o que fazer se não for texto."""

    oid: str
    parents: tuple[str, ...]
    author: bytes
    committer: bytes
    message: bytes


def _object_header(kind: bytes, data: bytes) -> bytes:
    return kind + b" %d\0" % len(data)


class MediatedGit:
    """Uma sessão de leituras Git mediadas sobre **um** workspace, num **único** pedido.

    ``workspace_path`` é a raiz vinculada (absoluta, canônica) do workspace; ``expected_prefix``
    é o prefixo que o binding registrou — o Git precisa confirmá-lo (`--show-prefix`), senão
    `GIT_OUTPUT_UNVERIFIABLE`. Nada roda no construtor.
    """

    def __init__(
        self,
        workspace_path: str,
        *,
        expected_prefix: str,
        is_cancelled: Callable[[], bool],
        capture_bytes: int,
        timeout_s: float | None = None,
        remaining_s: Callable[[], float] | None = None,
    ) -> None:
        self._cwd = workspace_path
        self._expected_prefix = expected_prefix
        self._is_cancelled = is_cancelled
        self._capture = capture_bytes
        self._timeout = TIMEOUT_S if timeout_s is None else timeout_s
        #: Prazo **global** de quem chama (E8.3), consultado a cada processo; `None` = só o fixo.
        self._remaining = remaining_s
        self._git: str | None = None
        self._format: str | None = None
        self._prefix: bytes | None = None

    def __repr__(self) -> str:
        return "<MediatedGit>"

    # --------------------------------------------------------------------------- processo

    def _executable(self) -> str:
        if self._git is None:
            found = shutil.which("git")
            if found is None:
                raise _fail(GitFailure.UNAVAILABLE)
            self._git = found
        return self._git

    def _step_timeout(self) -> float:
        """O prazo **deste** processo, calculado agora, imediatamente antes de iniciá-lo.

        Sem ``remaining_s``, o fixo da sessão (comportamento histórico). Com ele, o fixo
        encurtado — nunca aumentado — pelo que resta do prazo global; sem prazo restante (ou
        consulta que falha), o processo **não nasce** (`TIMEOUT`).
        """
        if self._remaining is None:
            return self._timeout
        try:
            remaining = float(self._remaining())
        except Exception:
            raise _fail(GitFailure.TIMEOUT) from None
        if not remaining > 0.0:  # inclui NaN
            raise _fail(GitFailure.TIMEOUT)
        return min(self._timeout, remaining)

    def _run(self, *args: str, max_stdout: int | None = None) -> ProcessResult:
        """O **único** ponto que roda Git mediado. Devolve só `EXITED` com saída completa."""
        timeout = self._step_timeout()
        try:
            spec = ProcessSpec(
                argv=(self._executable(), *_MEDIATED_GIT_OPTIONS, "-C", self._cwd, *args),
                cwd=self._cwd,
                env=_git_env(),
                timeout_s=timeout,
                max_stdout_bytes=max(1, self._capture if max_stdout is None else max_stdout),
                max_stderr_bytes=_STDERR_BYTES,
            )
        except InvalidProcessSpec:
            raise _fail(GitFailure.UNAVAILABLE) from None
        if self._cancelled():
            raise _fail(GitFailure.CANCELLED)  # antes de iniciar
        result = run_supervised(spec, self._is_cancelled)
        # Depois que o Supervisor devolve, e **antes** de olhar o desfecho: um cancelamento
        # que chegou durante a limpeza/confirmação vence até `EXITED` com saída completa.
        if self._cancelled() or result.outcome is ProcessOutcome.CANCELLED:
            raise _fail(GitFailure.CANCELLED)
        if result.outcome is ProcessOutcome.TIMEOUT:
            raise _fail(GitFailure.TIMEOUT)
        if result.outcome is not ProcessOutcome.EXITED:
            raise _fail(GitFailure.FAILED)
        if result.stdout_truncated:
            raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)  # nunca um prefixo como se fosse tudo
        return result

    def _cancelled(self) -> bool:
        """Fail closed: exceção ou resposta que não seja exatamente `False` cancela."""
        try:
            return self._is_cancelled() is not False
        except Exception:
            return True

    def _ok(self, *args: str, failure: GitFailure, max_stdout: int | None = None) -> bytes:
        result = self._run(*args, max_stdout=max_stdout)
        if result.exit_code != 0:
            raise _fail(failure)
        return result.stdout

    # ----------------------------------------------------------------------- repositório

    def _layout(self) -> tuple[str, bytes]:
        if self._format is None or self._prefix is None:
            data = self._ok(
                "rev-parse",
                "--show-object-format",
                "--show-prefix",
                failure=GitFailure.FAILED,
            )
            fmt, separator, rest = data.partition(b"\n")
            if not separator or fmt not in (b"sha1", b"sha256") or not rest.endswith(b"\n"):
                raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
            prefix = rest[:-1]  # só o terminador; o nome pode ter qualquer byte
            if prefix != self._expected_prefix.encode("utf-8"):
                raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
            self._format, self._prefix = fmt.decode("ascii"), prefix
        return self._format, self._prefix

    @property
    def object_format(self) -> str:
        return self._layout()[0]

    def is_oid(self, value: str) -> bool:
        return bool(_OID_RE[self.object_format].fullmatch(value.encode("ascii", "replace")))

    def _oid(self, raw: bytes) -> str:
        if not _OID_RE[self.object_format].fullmatch(raw):
            raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
        return raw.decode("ascii")

    def _commit_arg(self, commit: str) -> str:
        if not self.is_oid(commit):
            raise _fail(GitFailure.OBJECT_UNAVAILABLE)  # formato do repo ≠ do commit pedido
        return f"{commit}^{{commit}}"

    def _relative(self, raw_path: bytes) -> tuple[str | None, bytes | None]:
        """`(texto, None)` representável; `(None, bytes)` de dentro e não representável;
        `(None, None)` fora do workspace (descartado, nunca `../`)."""
        relative = _strip_prefix_bytes(raw_path, self._layout()[1])
        if relative is None:
            return None, None
        try:
            text = relative.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None, relative
        if not _is_representable(text):
            return None, relative
        return text, None

    @staticmethod
    def _records(data: bytes) -> list[bytes]:
        if not data:
            return []
        if not data.endswith(b"\0"):
            raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
        return data[:-1].split(b"\0")

    # ------------------------------------------------------------------------- revisões

    def resolve_commit(self, ref: str) -> str:
        """Ref tipada → oid **completo** de commit. A string já passou pelo contrato
        (`_ref`: sem `-` inicial, sem espaço, sem `:`) e ainda vai atrás de
        `--end-of-options`. Resolvida **uma vez**: quem chama usa só o oid devolvido."""
        self._layout()
        data = self._ok(
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            f"{ref}^{{commit}}",
            failure=GitFailure.REF_NOT_FOUND,
        )
        if not data.endswith(b"\n"):
            raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
        return self._oid(data[:-1])

    def is_ancestor(self, commit: str, base: str) -> bool:
        """`commit` é `base` ou ancestral dele? Exit 0 = sim, 1 = não, outro = falha."""
        result = self._run(
            "merge-base", "--is-ancestor", self._commit_arg(commit), self._commit_arg(base)
        )
        if result.exit_code == 0:
            return True
        if result.exit_code == 1:
            return False
        raise _fail(GitFailure.OBJECT_UNAVAILABLE)

    # -------------------------------------------------------------------------- árvores

    def list_tree(self, commit: str) -> Listing[TreeEntry]:
        """Todas as entradas não-árvore de `commit` **dentro do workspace**."""
        data = self._ok(
            "ls-tree",
            "-r",
            "-z",
            "-l",
            "--full-tree",
            self._commit_arg(commit),
            failure=GitFailure.OBJECT_UNAVAILABLE,
        )
        entries: list[TreeEntry] = []
        unrepresentable: list[bytes] = []
        for record in self._records(data):
            meta, separator, raw_path = record.partition(b"\t")
            fields = meta.split()
            if not separator or not raw_path or len(fields) != 4:
                raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
            mode, kind, raw_oid, raw_size = fields
            oid = self._oid(raw_oid)
            if kind == b"blob" and mode in _REGULAR_MODES | {_SYMLINK_MODE}:
                if raw_size == _MISSING_SIZE:
                    raise _fail(GitFailure.OBJECT_UNAVAILABLE)  # partial clone: não está aqui
                if not raw_size.isdigit():
                    raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
                entry_kind = EntryKind.FILE if mode in _REGULAR_MODES else EntryKind.SYMLINK
                size: int | None = int(raw_size)
            elif kind == b"commit" and mode == _GITLINK_MODE and raw_size == b"-":
                entry_kind, size = EntryKind.GITLINK, None
            else:
                raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
            text, bad = self._relative(raw_path)
            if bad is not None:
                unrepresentable.append(bad)
            if text is None:
                continue
            entries.append(TreeEntry(text, mode.decode("ascii"), entry_kind, oid, size))
        _validate_tree_entries(entries)
        return _freeze(entries, lambda entry: entry.path, unrepresentable)

    def list_index(self) -> Listing[IndexEntry]:
        """O índice, só lendo o índice: `ls-files --stage` não compara com a worktree."""
        data = self._ok("ls-files", "--stage", "-z", "--full-name", failure=GitFailure.FAILED)
        entries: list[IndexEntry] = []
        unrepresentable: list[bytes] = []
        for record in self._records(data):
            meta, separator, raw_path = record.partition(b"\t")
            fields = meta.split(b" ")
            if not separator or not raw_path or len(fields) != 3:
                raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
            mode, raw_oid, raw_stage = fields
            if raw_stage not in (b"0", b"1", b"2", b"3"):
                raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
            if mode in _REGULAR_MODES:
                entry_kind = EntryKind.FILE
            elif mode == _SYMLINK_MODE:
                entry_kind = EntryKind.SYMLINK
            elif mode == _GITLINK_MODE:
                entry_kind = EntryKind.GITLINK
            else:
                raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
            oid = self._oid(raw_oid)
            text, bad = self._relative(raw_path)
            if bad is not None:
                unrepresentable.append(bad)
            if text is None:
                continue
            entries.append(IndexEntry(text, mode.decode("ascii"), entry_kind, oid, int(raw_stage)))
        _validate_index_entries(entries)
        return _freeze(entries, lambda entry: entry.path, unrepresentable)

    def index_divergence(self, commit: str) -> Listing[str]:
        """Caminhos em que o **índice** difere da árvore de `commit`, *intent-to-add* incluído.

        Leitura estrutural árvore × índice (`worktree._index_diff_argv`, a mesma da E7.4): nunca
        olha o disco, nunca gera conteúdo de diff. Código 0 com saída vazia = sem divergência;
        1 com registros = diverge; qualquer outra combinação é `GIT_OUTPUT_UNVERIFIABLE`.
        """
        if not self.is_oid(commit):
            raise _fail(GitFailure.OBJECT_UNAVAILABLE)
        result = self._run(*_index_diff_argv(commit))
        if result.exit_code == 0 and result.stdout == b"":
            return Listing((), ())
        if result.exit_code != 1 or not result.stdout:
            raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
        records = self._records(result.stdout)
        if len(records) % 2:
            raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
        paths: set[str] = set()
        unrepresentable: list[bytes] = []
        for meta, raw_path in zip(records[::2], records[1::2], strict=True):
            if not _RAW_META.fullmatch(meta) or not raw_path:
                raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
            text, bad = self._relative(raw_path)
            if bad is not None:
                unrepresentable.append(bad)
            if text is not None:
                paths.add(text)
        return _freeze(list(paths), lambda path: path, unrepresentable)

    def list_untracked(self) -> tuple[Listing[str], tuple[str, ...]]:
        """Não rastreados e não ignorados: `(arquivos, repositórios aninhados)`."""
        data = self._ok(
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--full-name",
            failure=GitFailure.FAILED,
        )
        files: list[str] = []
        nested: list[str] = []
        unrepresentable: list[bytes] = []
        for record in self._records(data):
            if not record:
                raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
            is_dir = record.endswith(b"/")
            text, bad = self._relative(record[:-1] if is_dir else record)
            if bad is not None:
                unrepresentable.append(bad)
            if text is None:
                continue
            (nested if is_dir else files).append(text)
        return _freeze(files, lambda path: path, unrepresentable), tuple(sorted(nested, key=_key))

    # -------------------------------------------------------------------------- objetos

    def read_blob(self, oid: str, size: int) -> bytes:
        """O blob cru de um oid **que o próprio Git devolveu** (árvore), com o tamanho já
        conhecido: captura exatamente ``size`` bytes e confere o oid recalculado."""
        if not self.is_oid(oid):
            raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
        data = self._ok(
            "cat-file", "blob", oid, failure=GitFailure.OBJECT_UNAVAILABLE, max_stdout=size
        )
        if len(data) != size or self._hash(b"blob", data) != oid:
            raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
        return data

    def read_commit(self, commit: str) -> CommitInfo:
        """Objeto commit cru, oid conferido; só `parent`/`author`/`committer`/mensagem saem."""
        if not self.is_oid(commit):
            raise _fail(GitFailure.OBJECT_UNAVAILABLE)
        data = self._ok("cat-file", "commit", commit, failure=GitFailure.OBJECT_UNAVAILABLE)
        if self._hash(b"commit", data) != commit:
            raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
        header, separator, message = data.partition(b"\n\n")
        if not separator:
            raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
        lines = header.split(b"\n")
        if not lines[0].startswith(b"tree "):
            raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
        self._oid(lines[0][5:])
        parents: list[str] = []
        author: bytes | None = None
        committer: bytes | None = None
        for line in lines[1:]:
            if line.startswith(b" "):
                continue  # continuação de cabeçalho multilinha (gpgsig, mergetag): não sai
            key, _, value = line.partition(b" ")
            if key == b"parent":
                parents.append(self._oid(value))
            elif key == b"author" and author is None:
                author = value
            elif key == b"committer" and committer is None:
                committer = value
            elif key in (b"author", b"committer", b"tree"):
                raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
        if author is None or committer is None:
            raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
        return CommitInfo(commit, tuple(parents), author, committer, message)

    def blob_oid(self, data: bytes) -> str:
        """O oid que o Git daria a ``data`` como blob, no formato do repositório."""
        return self._hash(b"blob", data)

    def blob_hasher(self, size: int) -> tuple[Callable[[bytes], None], Callable[[], str]]:
        """Oid de blob em streaming: `(update, hexdigest)`, com o cabeçalho de ``size``."""
        digest = self._new_hash()
        digest.update(b"blob %d\0" % size)
        return digest.update, digest.hexdigest

    def _new_hash(self) -> hashlib._Hash:
        if self.object_format == "sha256":
            return hashlib.sha256()
        return hashlib.sha1(usedforsecurity=False)

    def _hash(self, kind: bytes, data: bytes) -> str:
        digest = self._new_hash()
        digest.update(_object_header(kind, data))
        digest.update(data)
        return digest.hexdigest()


def _key(path: str) -> bytes:
    return path.encode("utf-8")


def _validate_tree_entries(entries: list[TreeEntry]) -> None:
    """Cada caminho **uma** vez na árvore (já reduzido ao workspace). Duplicata → falha."""
    seen: set[str] = set()
    for entry in entries:
        if entry.path in seen:
            raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
        seen.add(entry.path)


def _validate_index_entries(entries: list[IndexEntry]) -> None:
    """D-AUD-002: antes de qualquer mapa por caminho. Válido por caminho: **exatamente** um
    estágio 0 e nada mais, **ou** só estágios 1/2/3 sem repetição. Duplicata (mesmo idêntica)
    ou estágio 0 junto de conflito → `GIT_OUTPUT_UNVERIFIABLE`; nunca *last-write-wins*."""
    stages: dict[str, list[int]] = {}
    for entry in entries:
        stages.setdefault(entry.path, []).append(entry.stage)
    for found in stages.values():
        if 0 in found:
            if found != [0]:
                raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)
        elif len(found) != len(set(found)):
            raise _fail(GitFailure.OUTPUT_UNVERIFIABLE)


def _freeze(
    entries: list[_T], path_of: Callable[[_T], str], unrepresentable: list[bytes]
) -> Listing[_T]:
    """Ordem determinística: caminho em bytes UTF-8 (e, no índice, estágio)."""
    entries.sort(key=lambda entry: (_key(path_of(entry)), getattr(entry, "stage", 0)))
    return Listing(tuple(entries), tuple(sorted(set(unrepresentable))))


__all__ = [
    "TIMEOUT_S",
    "CommitInfo",
    "EntryKind",
    "GitFailure",
    "IndexEntry",
    "Listing",
    "MediatedGit",
    "MediatedGitError",
    "TreeEntry",
]
