"""Fatos da **verificação pós-execução** (E8.3). Só leitura; nunca lança por IO.

Duas observações, para o `execution_verification` comparar e decidir — aqui não há decisão de
política, só fatos —, as duas sob **um orçamento explícito** (`VerificationBudget`):

* `main_tree_state` — o estado do **repositório principal inteiro** (a raiz vem do Git, nunca só
  o `local_path`, que pode ser subdiretório de um monorepo): o **índice efetivo** e o
  **filesystem** da raiz. Comparar dois estados é igualdade.
* `worktree_changes` — o que mudou na **worktree do run** contra o `base_commit`, no repositório da
  worktree **inteiro**, com a identidade da worktree conferida antes.

## O estado da árvore principal não passa por `git status` (E8.3-AUD-002/003)

`git status`/`diff` comparam o disco com o índice pelo pipeline de conversão do Git — e podem
rodar `clean`/`process`/`smudge` de filtros, `fsmonitor` e outros helpers configurados pelo
projeto. E a resposta deles depende do próprio índice: um caminho `assume-unchanged` ou
`skip-worktree` reescrito **não aparece**, nem um arquivo ignorado. Por isso o estado é:

1. **identidade da raiz**: toplevel, git dir e `.git` comum (caminho e identidade do objeto);
2. **índice efetivo**: o arquivo que o próprio Git aponta (`rev-parse --git-path index`), lido
   cru e resumido por SHA-256 — sem interpretá-lo;
3. **filesystem**: todo *directory entry* ordinário sob a raiz — rastreado, não rastreado,
   **ignorado**, e o conteúdo de repositórios aninhados como arquivo comum —, percorrido no
   Python **sem seguir link**: caminho em bytes, tipo, bits de modo, tamanho e SHA-256 do
   conteúdo para arquivo regular; o alvo (`readlink`) para symlink/junction, que nunca é
   seguido; o tipo para FIFO/socket/device, que **nunca é aberto**. Outro reparse point é falha
   (não verificável). O git dir administrativo da raiz **não** é percorrido; o índice é a exceção
   explícita. Cada diretório é revalidado (tipo, reparse, identidade) imediatamente antes e
   depois de enumerado (E8.3-FINAL-002) — vale também para a worktree. Troca-e-restauração
   invisível entre as duas observações é TOCTOU residual declarado.

O único Git aqui é `rev-parse` (raiz, git dirs, índice, formato de objeto), que não lê conteúdo
do working tree e não aciona filtro nem `fsmonitor`. Fora do contrato, declarado: os demais
metadados do `.git` (`HEAD`, refs, objetos, config, hooks, `info/exclude`, reflogs) e
*alternate data streams* do NTFS.

## Worktree do run: Git só sobre objetos e índice, ciente do formato de objeto (AUD-004)

Pelo Git **mediado** da E7.5-D (`MediatedGit`, sob o Supervisor): `rev-parse
--show-object-format` (só `sha1`/`sha256`), `ls-tree -r -l --full-tree`, `ls-files --stage`,
`diff-index --cached`. O disco é percorrido no Python com o oid de blob calculado no **formato do
repositório** (`b"blob <tamanho>\\0" + bytes`, SHA-1 ou SHA-256), em streaming.

## Orçamento (AUD-005)

Cada operação — `capture` ou `verify` — tem **um** orçamento: 100 000 entradas observadas,
1 GiB de bytes **efetivamente lidos**, 60 s de relógio monotônico e o cancelamento do run.
Entradas são cobradas **durante** a enumeração (nunca `list()` antes); bytes, por *chunk* lido
(nunca pelo `stat`); prazo e cancelamento antes **e depois** de cada passo potencialmente
longo (Git, diretório, *chunk*). Cada processo Git recebe no máximo o que resta do prazo,
calculado imediatamente antes de iniciá-lo (sem prazo restante, não inicia). Orçamento esgotado
é `LIMIT_EXCEEDED`/`DEADLINE_EXCEEDED`/`CANCELLED` — nunca um resultado parcial apresentado
como completo.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeVar

from app.git_runtime import _decode_text, _run_git
from app.git_runtime.mediated import EntryKind, MediatedGit, MediatedGitError
from app.git_runtime.worktree import (
    _IN_PROGRESS_MARKERS,
    _POSIX_MODES,
    _identity,
    _is_reparse,
    _same_path,
    _single_line,
)
from app.safety.types import ObjectIdentity

_IS_WINDOWS = sys.platform == "win32"

# ----------------------------------------------------------------------------- orçamento

#: Valores V1 aprovados pelo Product Owner (E8.3-AUD-005). Não são parâmetros.
MAX_ENTRIES = 100_000
MAX_BYTES_READ = 1_073_741_824
DEADLINE_S = 60.0

#: Bloco de leitura. O orçamento é cobrado pelo que cada `read` devolveu.
_CHUNK = 1024 * 1024
#: Teto **técnico** da saída estrutural de cada processo Git mediado (não é o orçamento).
_GIT_CAPTURE_BYTES = 64 * 1024 * 1024
#: Prazo técnico de cada processo Git mediado, antes de ser encurtado pelo que resta do prazo.
_GIT_STEP_TIMEOUT_S = 20.0


class VerificationFailure(str, Enum):
    """Por que uma observação **não** pôde concluir. Nenhum é "sem mudança"."""

    #: Leitura do Git ou do filesystem falhou ou saiu fora do formato.
    UNVERIFIABLE = "unverifiable"
    #: Nome no disco sem representação no vocabulário de caminho da política.
    UNREPRESENTABLE_PATH = "unrepresentable_path"
    #: Link, junction ou reparse point dentro da worktree.
    LINK_OR_REPARSE = "link_or_reparse"
    #: Entrada que não é arquivo regular nem diretório (FIFO, socket, device) na worktree, ou
    #: reparse point de tipo desconhecido na árvore principal.
    UNSUPPORTED_ENTRY = "unsupported_entry"
    #: O objeto mudou **durante** a observação (identidade, tipo ou tamanho).
    CHANGED_DURING_READ = "changed_during_read"
    #: A continuidade do objeto dependia de uma identidade que **não prova nada** (`file_id == 0`,
    #: `ObjectIdentity.is_verifiable` falso) — mesmo significado do `INTEGRITY_UNVERIFIABLE` do
    #: `path_runtime` (E7.5). Fail closed; nunca substituída por tipo/caminho/modo/reparse.
    INTEGRITY_UNVERIFIABLE = "integrity_unverifiable"
    #: O diretório não é a worktree vinculada esperada.
    NOT_LINKED_WORKTREE = "not_linked_worktree"
    #: O `HEAD` da worktree não é mais o `base_commit`.
    HEAD_MOVED = "head_moved"
    #: Merge/rebase/cherry-pick/revert/bisect em andamento no git dir da worktree.
    OPERATION_IN_PROGRESS = "operation_in_progress"
    #: Formato de objeto que não é `sha1` nem `sha256`, ou commit fora do formato do repo.
    UNSUPPORTED_OBJECT_FORMAT = "unsupported_object_format"
    #: Orçamento: mais de `MAX_ENTRIES` entradas ou de `MAX_BYTES_READ` bytes lidos.
    LIMIT_EXCEEDED = "limit_exceeded"
    #: Orçamento: o prazo de `DEADLINE_S` passou.
    DEADLINE_EXCEEDED = "deadline_exceeded"
    #: O cancelamento do run foi pedido (ou a consulta a ele falhou: fail closed).
    CANCELLED = "cancelled"


class BudgetExhausted(Exception):
    """O orçamento acabou. A mensagem é só o código."""

    def __init__(self, failure: VerificationFailure) -> None:
        super().__init__(failure.value)
        self.failure = failure


class _Failed(Exception):
    """Interna: uma observação terminou numa `VerificationFailure`. Nunca sai do módulo."""

    def __init__(self, failure: VerificationFailure) -> None:
        super().__init__(failure.value)
        self.failure = failure


class VerificationBudget:
    """Orçamento de **uma** operação (`capture` ou `verify`). Sem reinício entre subpassos.

    ``is_cancelled`` é a porta neutra de cancelamento (E8.4 adapta o `CancelToken`): exceção ou
    resposta que não seja exatamente `False` cancela. ``clock`` existe para os testes; em
    produção é `time.monotonic`.
    """

    def __init__(
        self,
        *,
        is_cancelled: Callable[[], bool],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(is_cancelled) or not callable(clock):
            raise TypeError("is_cancelled e clock precisam ser callables")
        self._is_cancelled = is_cancelled
        self._clock = clock
        self._deadline = clock() + DEADLINE_S
        self.entries_seen = 0
        self.bytes_read = 0

    def __repr__(self) -> str:
        return f"VerificationBudget(<{self.entries_seen} entries, {self.bytes_read} bytes>)"

    def _cancelled(self) -> bool:
        try:
            return self._is_cancelled() is not False
        except Exception:
            return True

    def check(self) -> None:
        """Cancelamento e prazo. Levanta `BudgetExhausted`."""
        if self._cancelled():
            raise BudgetExhausted(VerificationFailure.CANCELLED)
        if self._clock() >= self._deadline:
            raise BudgetExhausted(VerificationFailure.DEADLINE_EXCEEDED)

    def consume_entry(self) -> None:
        """Uma entrada observada. A que passaria do teto **não** é contada nem processada."""
        self.check()
        if self.entries_seen >= MAX_ENTRIES:
            raise BudgetExhausted(VerificationFailure.LIMIT_EXCEEDED)
        self.entries_seen += 1

    def consume_bytes(self, count: int) -> None:
        """Bytes **efetivamente lidos** (o tamanho do `read`, não o do `stat`)."""
        self.bytes_read += count
        if self.bytes_read > MAX_BYTES_READ:
            raise BudgetExhausted(VerificationFailure.LIMIT_EXCEEDED)
        self.check()

    def remaining_bytes(self) -> int:
        return max(0, MAX_BYTES_READ - self.bytes_read)

    def remaining_s(self) -> float:
        return max(0.0, self._deadline - self._clock())

    def interrupted(self) -> bool:
        """Para o Supervisor de um processo Git: cancelado **ou** sem prazo. Nunca levanta."""
        try:
            return self._cancelled() or self._clock() >= self._deadline
        except Exception:
            return True


def _git(budget: VerificationBudget, git: str, cwd: str, *args: str) -> bytes | None:
    """Uma leitura Git curta (`rev-parse`) no runner de leitura, dentro do prazo global.

    Cancelamento/prazo antes; o timeout do processo é o que resta do prazo, calculado
    imediatamente antes (nunca mais que o fixo do runner) — sem prazo restante, o processo nem
    nasce; cancelamento/prazo depois. `None` = falhou (código ≠ 0 ou processo).
    """
    budget.check()
    remaining = budget.remaining_s()
    if not remaining > 0.0:
        raise BudgetExhausted(VerificationFailure.DEADLINE_EXCEEDED)
    result = _run_git(git, cwd, *args, timeout=remaining)
    budget.check()
    if result is None or result.returncode != 0:
        return None
    return result.stdout


def _lines(data: bytes | None, expected: int) -> list[str]:
    """Exatamente ``expected`` linhas absolutas, não vazias, UTF-8 estrito. Senão, falha."""
    text = None if data is None else _decode_text(data)
    if text is None or not text.endswith("\n"):
        raise _Failed(VerificationFailure.UNVERIFIABLE)
    lines = text[:-1].split("\n")
    if len(lines) != expected or not all(line and os.path.isabs(line) for line in lines):
        raise _Failed(VerificationFailure.UNVERIFIABLE)
    return [os.path.normpath(line) for line in lines]


# ------------------------------------------------------------------------- leitura estável

_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def _proven_identity(info: os.stat_result) -> ObjectIdentity:
    """A identidade que vai **provar continuidade** do objeto, ou fail closed.

    A regra da foundation (E7.5, `path_runtime._trusted_identity`): `file_id == 0` não prova
    nada — `(volume, 0) == (volume, 0)` vale para objetos diferentes. Tipo, caminho, modo ou
    flag de reparse **não** substituem a identidade.
    """
    identity = _identity(info)
    if not identity.is_verifiable:
        raise _Failed(VerificationFailure.INTEGRITY_UNVERIFIABLE)
    return identity


def _same_object(a: os.stat_result, b: os.stat_result) -> bool:
    """Mesmo objeto (identidade **verificável**), mesmo tipo e tamanho. Identidade que não prova
    nada levanta `INTEGRITY_UNVERIFIABLE` — nunca é "igual"."""
    return (
        _proven_identity(a) == _proven_identity(b)
        and stat.S_IFMT(a.st_mode) == stat.S_IFMT(b.st_mode)
        and a.st_size == b.st_size
    )


def stream_digest(
    read: Callable[[int], bytes],
    size: int,
    update: Callable[[bytes], None],
    budget: VerificationBudget,
) -> None:
    """Lê **exatamente** ``size`` bytes por ``read``, em blocos, para ``update``.

    Antes de cada bloco, prazo/cancelamento; depois, o orçamento é cobrado pelo que ``read``
    **devolveu**. Cada `read` pede no máximo o que falta do arquivo **mais um** byte (para
    perceber crescimento) e no máximo o que resta do orçamento mais um (o byte que o estoura):
    nada é lido além disso para "achar o EOF". Mais bytes que ``size`` (o arquivo cresceu) ou
    menos (encolheu) é `CHANGED_DURING_READ`. Nada é guardado além do *digest*.
    """
    total = 0
    while True:
        budget.check()
        want = min(_CHUNK, size - total + 1, budget.remaining_bytes() + 1)
        chunk = read(want)
        if not chunk:
            break
        budget.consume_bytes(len(chunk))
        total += len(chunk)
        if total > size:
            raise _Failed(VerificationFailure.CHANGED_DURING_READ)
        update(chunk)
    if total != size:
        raise _Failed(VerificationFailure.CHANGED_DURING_READ)


def _read_regular(
    path: str,
    before: os.stat_result,
    update: Callable[[bytes], None],
    budget: VerificationBudget,
) -> None:
    """Leitura estável de um arquivo regular já observado por `lstat`.

    Abre sem seguir link e sem bloquear (um FIFO trocado no lugar não trava), confere que o
    descritor é **o mesmo** objeto regular, lê em blocos sob o orçamento e reconfere o caminho
    depois. Qualquer diferença: `CHANGED_DURING_READ` — nunca um estado híbrido silencioso.
    """
    budget.check()
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except OSError:
        raise _Failed(VerificationFailure.CHANGED_DURING_READ) from None
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or not _same_object(opened, before):
            raise _Failed(VerificationFailure.CHANGED_DURING_READ)
        stream_digest(lambda n: os.read(fd, n), before.st_size, update, budget)
    except OSError:
        raise _Failed(VerificationFailure.UNVERIFIABLE) from None
    finally:
        os.close(fd)
    try:
        after = os.lstat(path)
    except OSError:
        raise _Failed(VerificationFailure.CHANGED_DURING_READ) from None
    if not _same_object(after, before):
        raise _Failed(VerificationFailure.CHANGED_DURING_READ)


#: Os pontos de observação do filesystem, como nomes do módulo: os testes os trocam (iterador
#: sintético de 100 001 entradas, identidade sem `file_id`) sem tocar no `os` global. Não há
#: `stat` que segue link: toda observação que prova algo é `lstat` (E8.3-FOCAL-001).
_scandir = os.scandir
_lstat = os.lstat


def _children(directory: str, budget: VerificationBudget) -> list[tuple[str, str, os.stat_result]]:
    """`(nome, caminho, lstat)` de cada entrada, **cobrando o orçamento durante** a enumeração.

    Nunca `list(scandir(...))`: a entrada que passaria do teto levanta antes de ser lida, e nada
    depois dela é consumido. O que foi lido fica na memória só até o teto, para ordenar.
    """
    budget.check()
    found: list[tuple[str, str, os.stat_result]] = []
    try:
        with _scandir(directory) as iterator:
            for entry in iterator:
                budget.consume_entry()
                found.append((entry.name, entry.path, _lstat(entry.path)))
    except OSError:
        raise _Failed(VerificationFailure.UNVERIFIABLE) from None
    return found


def _directory_proof(
    path: str, budget: VerificationBudget, *, missing: VerificationFailure
) -> os.stat_result:
    """A observação que prova um diretório atravessado — **qualquer** um: raiz da principal, raiz
    da worktree ou filho. Sem exceção para a raiz (E8.3-FOCAL-001).

    `lstat` (nunca um `stat` que segue link: a identidade do **alvo** de uma junction não prova
    nada sobre o caminho); link/junction/reparse → `LINK_OR_REPARSE`; prazo e cancelamento antes
    e depois. Tipo e identidade ficam com quem chama.
    """
    budget.check()
    try:
        info = _lstat(path)
    except (OSError, ValueError):
        raise _Failed(missing) from None
    budget.check()
    if _is_reparse(info):
        raise _Failed(VerificationFailure.LINK_OR_REPARSE)
    return info


def _anchor_directory(path: str, budget: VerificationBudget) -> ObjectIdentity:
    """A identidade-âncora de um diretório que a operação vai atravessar ou comparar (raiz da
    principal, `.git` comum, raiz da worktree): não é link/reparse, é diretório e a identidade
    é **verificável**. Um arquivo `.git` dentro da raiz não a torna reparse."""
    info = _directory_proof(path, budget, missing=VerificationFailure.UNVERIFIABLE)
    if not stat.S_ISDIR(info.st_mode):
        raise _Failed(VerificationFailure.UNVERIFIABLE)
    return _proven_identity(info)


def _revalidate_directory(
    path: str, expected: ObjectIdentity, *, budget: VerificationBudget
) -> None:
    """O diretório da pilha **continua** o mesmo diretório ordinário (E8.3-FINAL-002/FOCAL-001).

    Chamada imediatamente **antes** do `scandir` (um `lstat` antigo não dá autoridade
    permanente: a entrada pode ter virado junction/symlink, arquivo ou outro diretório enquanto
    esperava na pilha) e de novo **depois** (uma troca durante a enumeração invalida o que foi
    lido). Vale igual para a raiz. Link/reparse → `LINK_OR_REPARSE`; outro tipo, outra
    identidade ou sumiço → `CHANGED_DURING_READ`; identidade esperada ou observada que não prova
    nada (`file_id == 0`) → `INTEGRITY_UNVERIFIABLE`, nunca aceita só porque os dois são
    diretórios.

    Não é proteção absoluta: um adversário concorrente que troque **e restaure** a entrada entre
    as duas observações, sem deixar rastro de identidade, não é visto — TOCTOU residual do
    contrato (só a sandbox, E14, fecha).
    """
    budget.check()
    if not expected.is_verifiable:
        raise _Failed(VerificationFailure.INTEGRITY_UNVERIFIABLE)
    info = _directory_proof(path, budget, missing=VerificationFailure.CHANGED_DURING_READ)
    if not stat.S_ISDIR(info.st_mode):
        raise _Failed(VerificationFailure.CHANGED_DURING_READ)
    if _proven_identity(info) != expected:
        raise _Failed(VerificationFailure.CHANGED_DURING_READ)


def _directory_children(
    path: str, expected: ObjectIdentity, *, budget: VerificationBudget
) -> list[tuple[str, str, os.stat_result]]:
    """`_children` de um diretório da pilha, entre duas revalidações dele."""
    _revalidate_directory(path, expected, budget=budget)
    found = _children(path, budget)
    _revalidate_directory(path, expected, budget=budget)
    return found


def _raw_name(name: str) -> bytes:
    """Os bytes do nome: no POSIX, os exatos (`surrogateescape`); no Windows, UTF-16 → UTF-8
    determinístico (`surrogatepass` preserva um surrogate solto)."""
    if _IS_WINDOWS:
        return name.encode("utf-8", "surrogatepass")
    return os.fsencode(name)


# ------------------------------------------------------------------- árvore principal

#: `rev-parse` só: raiz, git dir, `.git` comum e o índice efetivo, absolutos.
_LAYOUT_ARGS = (
    "rev-parse",
    "--path-format=absolute",
    "--show-toplevel",
    "--absolute-git-dir",
    "--git-common-dir",
    "--git-path",
    "index",
)

_REPARSE_TAG_SYMLINK = getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C)
_REPARSE_TAG_MOUNT_POINT = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)


@dataclass(frozen=True, slots=True)
class MainTreeState:
    """O estado da árvore principal. Comparável por igualdade; nunca exposto ao provider.

    Caminhos absolutos só existem para a segunda leitura usar a **mesma** raiz; não aparecem em
    `repr`. ``index_digest``/``filesystem_digest`` são SHA-256 locais (independem do formato de
    objeto do Git): servem só para comparar dois estados. Nenhum conteúdo é guardado.
    """

    toplevel: str = field(repr=False)
    git_dir: str = field(repr=False)
    git_common_dir: str = field(repr=False)
    toplevel_identity: ObjectIdentity = field(repr=False)
    common_dir_identity: ObjectIdentity = field(repr=False)
    index_digest: str | None = field(repr=False)
    filesystem_digest: str = field(repr=False)
    entry_count: int
    bytes_read: int = field(compare=False)

    def __repr__(self) -> str:
        return f"MainTreeState(<{self.entry_count} entries>)"


def _field(digest: hashlib._Hash, value: bytes) -> None:
    """Campo com prefixo de tamanho: concatenação sem ambiguidade."""
    digest.update(b"%d:" % len(value))
    digest.update(value)


def _entry_record(
    full: str, relative: bytes, info: os.stat_result, budget: VerificationBudget
) -> tuple[bytes, bool]:
    """O registro canônico de uma entrada, e se ela é um diretório a descer."""
    mode = b"%o" % stat.S_IMODE(info.st_mode)
    tag = getattr(info, "st_reparse_tag", 0)
    if stat.S_ISLNK(info.st_mode) or (
        _is_reparse(info) and tag in (_REPARSE_TAG_SYMLINK, _REPARSE_TAG_MOUNT_POINT)
    ):
        try:
            target = os.fsencode(os.readlink(full))  # metadado do link; o alvo nunca é seguido
        except (OSError, ValueError):
            raise _Failed(VerificationFailure.UNVERIFIABLE) from None
        return b"link\0" + hashlib.sha256(target).digest(), False
    if _is_reparse(info):
        raise _Failed(VerificationFailure.UNSUPPORTED_ENTRY)
    if stat.S_ISDIR(info.st_mode):
        return b"dir\0" + mode, True
    if not stat.S_ISREG(info.st_mode):
        # FIFO, socket, device: o tipo é observado pelo `lstat` e o objeto **nunca** é aberto.
        return b"other\0%o\0" % stat.S_IFMT(info.st_mode) + mode, False
    content = hashlib.sha256()
    _read_regular(full, info, content.update, budget)
    return b"file\0" + mode + b"\0%d\0" % info.st_size + content.digest(), False


def _filesystem_digest(
    toplevel: str,
    root_identity: ObjectIdentity,
    admin_dir_identity: ObjectIdentity | None,
    budget: VerificationBudget,
) -> tuple[str, int]:
    """SHA-256 do inventário inteiro da raiz, em ordem de bytes do caminho, e quantas entradas.

    O git dir administrativo da raiz (`.git` diretório com a identidade do git dir que o próprio
    Git informou) entra só como um marcador — não é percorrido. Cada diretório entra na pilha
    **com a identidade observada** e é revalidado antes e depois de enumerado.
    """
    records: list[tuple[bytes, bytes]] = []
    pending: list[tuple[bytes, str, ObjectIdentity]] = [(b"", toplevel, root_identity)]
    while pending:
        relative_dir, absolute_dir, expected = pending.pop()
        found = _directory_children(absolute_dir, expected, budget=budget)
        for name, full, info in found:
            relative = (relative_dir + b"/" if relative_dir else b"") + _raw_name(name)
            if (
                not relative_dir
                and name == ".git"
                and stat.S_ISDIR(info.st_mode)
                and not _is_reparse(info)
                and admin_dir_identity is not None
                and _identity(info) == admin_dir_identity
            ):
                records.append((relative, b"git-admin"))
                continue
            record, descend = _entry_record(full, relative, info, budget)
            records.append((relative, record))
            if descend:
                pending.append((relative, full, _proven_identity(info)))
    records.sort()
    digest = hashlib.sha256()
    for relative, record in records:
        budget.check()
        _field(digest, relative)
        _field(digest, record)
    return digest.hexdigest(), len(records)


def _index_digest(index_path: str, budget: VerificationBudget) -> str | None:
    """SHA-256 dos bytes crus do índice efetivo; `None` se ele comprovadamente não existe."""
    budget.check()
    try:
        info = os.lstat(index_path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError:
        raise _Failed(VerificationFailure.UNVERIFIABLE) from None
    if not stat.S_ISREG(info.st_mode) or _is_reparse(info):
        raise _Failed(VerificationFailure.UNSUPPORTED_ENTRY)
    digest = hashlib.sha256()
    _read_regular(index_path, info, digest.update, budget)
    return digest.hexdigest()


def main_tree_state(
    local_path: str, budget: VerificationBudget
) -> MainTreeState | VerificationFailure:
    """O estado do **repositório principal inteiro** de ``local_path`` (raiz ou subdiretório)."""
    git = shutil.which("git")
    if git is None:
        return VerificationFailure.UNVERIFIABLE
    try:
        # O caminho pedido também é provado antes de o Git resolvê-lo: uma junction no lugar da
        # raiz ancorada (o `verify` pede o `toplevel` do "antes") falha **aqui**, pelo reparse —
        # o Git a resolveria para o alvo e a raiz "pareceria" ordinária (E8.3-FOCAL-001).
        requested = _anchor_directory(local_path, budget)
        toplevel, git_dir, common_dir, index_path = _lines(
            _git(budget, git, local_path, *_LAYOUT_ARGS), expected=4
        )
        _revalidate_directory(local_path, requested, budget=budget)  # o Git viu o mesmo objeto
        # As três são **prova**: a raiz e o `.git` comum ancoram o anti-replay (antes × depois)
        # e a travessia; a do git dir prova qual `.git` é o administrativo. A raiz recebe a mesma
        # prova de qualquer diretório atravessado: sem link/reparse, diretório, `file_id`.
        toplevel_identity = _anchor_directory(toplevel, budget)
        common_identity = _anchor_directory(common_dir, budget)
        try:
            git_dir_info = _lstat(git_dir)
        except (OSError, ValueError):
            return VerificationFailure.UNVERIFIABLE
        admin_identity = _proven_identity(git_dir_info)
        index = _index_digest(index_path, budget)
        filesystem, entries = _filesystem_digest(
            toplevel, toplevel_identity, admin_identity, budget
        )
        budget.check()
    except BudgetExhausted as exhausted:
        return exhausted.failure
    except _Failed as failed:
        return failed.failure
    return MainTreeState(
        toplevel=toplevel,
        git_dir=git_dir,
        git_common_dir=common_dir,
        toplevel_identity=toplevel_identity,
        common_dir_identity=common_identity,
        index_digest=index,
        filesystem_digest=filesystem,
        entry_count=entries,
        bytes_read=budget.bytes_read,
    )


# ------------------------------------------------------------------------ worktree do run


class WorktreeChangeKind(str, Enum):
    """Espécie de mudança da worktree contra o `base_commit`. ``path`` relativo à raiz do repo."""

    #: Arquivo que não existe no `base_commit` (inclusive ignorado).
    ADDED = "added"
    #: Arquivo do `base_commit` com conteúdo, tamanho ou bit de execução diferente.
    MODIFIED = "modified"
    #: Arquivo do `base_commit` que não está mais no disco.
    DELETED = "deleted"
    #: Diretório que não existe no `base_commit` (inclusive vazio).
    DIRECTORY = "directory"
    #: O **índice** da worktree diverge do `base_commit` neste caminho (staged, conflito,
    #: intent-to-add).
    INDEX = "index"


@dataclass(frozen=True, slots=True)
class WorktreeChange:
    path: str
    kind: WorktreeChangeKind


@dataclass(frozen=True, slots=True)
class WorktreeChanges:
    """As mudanças da worktree inteira, ordenadas por caminho (bytes UTF-8) e espécie."""

    changes: tuple[WorktreeChange, ...]


_OID_PATTERNS = {"sha1": re.compile(r"[0-9a-f]{40}"), "sha256": re.compile(r"[0-9a-f]{64}")}


def oid_pattern(object_format: str) -> re.Pattern[str]:
    """O oid completo do formato: 40 hex (`sha1`) ou 64 hex (`sha256`). Outro: `KeyError`."""
    return _OID_PATTERNS[object_format]


def parse_object_format(raw: bytes | None) -> str | None:
    """A saída de `rev-parse --show-object-format`: só `sha1` ou `sha256`, exatamente."""
    if raw in (b"sha1\n", b"sha256\n"):
        assert raw is not None
        return raw[:-1].decode("ascii")
    return None


def _representable(name: str) -> bool:
    """O nome cabe, sem perda, no vocabulário de caminho da política (`/`, UTF-8 estrito)?"""
    if "\\" in name or "/" in name:
        return False
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _read_small_text(path: str, budget: VerificationBudget) -> str:
    """Arquivo administrativo pequeno (`.git`, `gitdir`): regular, sem link, UTF-8 estrito."""
    try:
        info = os.lstat(path)
    except OSError:
        raise _Failed(VerificationFailure.NOT_LINKED_WORKTREE) from None
    if not stat.S_ISREG(info.st_mode) or _is_reparse(info) or info.st_size > 64 * 1024:
        raise _Failed(VerificationFailure.NOT_LINKED_WORKTREE)
    chunks: list[bytes] = []
    _read_regular(path, info, chunks.append, budget)
    text = _decode_text(b"".join(chunks))
    if text is None:
        raise _Failed(VerificationFailure.NOT_LINKED_WORKTREE)
    return _single_line(text)


def _check_identity(
    git: str,
    workspace_path: str,
    *,
    expected_prefix: str,
    main_toplevel: str,
    main_git_common_dir: str,
    budget: VerificationBudget,
) -> tuple[str, str]:
    """A worktree vinculada esperada? Devolve `(raiz, formato de objeto)`. Nada é alterado.

    Tudo por `rev-parse` e por leitura direta dos dois arquivos do vínculo — sem `worktree list`
    e sem varrer `<common>/worktrees/`: o `.git` da worktree aponta para o admin dir, o admin dir
    é o git dir que o Git informa, fica em `<common>/worktrees/`, e o `gitdir` dele aponta de volta
    para o `.git` da worktree.
    """
    toplevel, git_dir, common_dir = _lines(
        _git(
            budget,
            git,
            workspace_path,
            "rev-parse",
            "--path-format=absolute",
            "--show-toplevel",
            "--absolute-git-dir",
            "--git-common-dir",
        ),
        expected=3,
    )
    prefix = _git(budget, git, workspace_path, "rev-parse", "--show-prefix")
    if prefix is None or not prefix.endswith(b"\n"):
        raise _Failed(VerificationFailure.UNVERIFIABLE)
    object_format = parse_object_format(
        _git(budget, git, workspace_path, "rev-parse", "--show-object-format")
    )
    if object_format is None:
        raise _Failed(VerificationFailure.UNSUPPORTED_OBJECT_FORMAT)
    prefix_parts = expected_prefix.rstrip("/").split("/") if expected_prefix else []
    if (
        prefix[:-1] != expected_prefix.encode("utf-8")
        or not _same_path(os.path.join(toplevel, *prefix_parts), workspace_path)
        or not _same_path(common_dir, main_git_common_dir)
        or _same_path(toplevel, main_toplevel)
        or not _same_path(os.path.dirname(git_dir), os.path.join(common_dir, "worktrees"))
    ):
        raise _Failed(VerificationFailure.NOT_LINKED_WORKTREE)

    pointer = _read_small_text(os.path.join(toplevel, ".git"), budget)
    back = _read_small_text(os.path.join(git_dir, "gitdir"), budget)
    if not pointer.startswith("gitdir: "):
        raise _Failed(VerificationFailure.NOT_LINKED_WORKTREE)
    pointed = pointer[len("gitdir: ") :]
    pointed = pointed if os.path.isabs(pointed) else os.path.join(toplevel, pointed)
    back = back if os.path.isabs(back) else os.path.join(git_dir, back)
    if not _same_path(pointed, git_dir) or not _same_path(back, os.path.join(toplevel, ".git")):
        raise _Failed(VerificationFailure.NOT_LINKED_WORKTREE)
    for marker in _IN_PROGRESS_MARKERS:
        budget.check()
        try:
            os.lstat(os.path.join(git_dir, marker))
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:
            raise _Failed(VerificationFailure.UNVERIFIABLE) from None
        raise _Failed(VerificationFailure.OPERATION_IN_PROGRESS)
    return toplevel, object_format


_T = TypeVar("_T")


def _mediated(git_call: Callable[[], _T], budget: VerificationBudget) -> _T:
    """Uma leitura do Git mediado sob o orçamento: falha técnica vira `UNVERIFIABLE`, e um
    processo interrompido pelo prazo/cancelamento vira o motivo do orçamento."""
    budget.check()
    try:
        result = git_call()
    except MediatedGitError:
        budget.check()  # o Supervisor parou por prazo/cancelamento? o motivo é esse
        raise _Failed(VerificationFailure.UNVERIFIABLE) from None
    budget.check()
    return result


def _filesystem_changes(
    toplevel: str,
    tree: dict[str, tuple[str, str, int]],
    directories: set[str],
    git: MediatedGit,
    budget: VerificationBudget,
) -> list[WorktreeChange]:
    """O disco contra a árvore do base, **todas** as diferenças. Na raiz, só o `.git` é tolerado.

    O oid de cada arquivo é calculado no formato do repositório (`MediatedGit.blob_hasher`).
    Cada diretório entra na pilha **com a identidade observada** e é revalidado antes e depois
    de enumerado: uma junction posta no lugar enquanto ele esperava não é seguida.
    """
    seen: set[str] = set()
    changes: list[WorktreeChange] = []
    pending: list[tuple[str, str, ObjectIdentity]] = [
        ("", toplevel, _anchor_directory(toplevel, budget))
    ]
    while pending:
        relative_dir, absolute_dir, expected = pending.pop()
        found = _directory_children(absolute_dir, expected, budget=budget)
        for name, full, info in found:
            if not _representable(name):
                raise _Failed(VerificationFailure.UNREPRESENTABLE_PATH)
            relative = f"{relative_dir}/{name}" if relative_dir else name
            if _is_reparse(info):
                raise _Failed(VerificationFailure.LINK_OR_REPARSE)
            if relative == ".git":
                if not stat.S_ISREG(info.st_mode):
                    raise _Failed(VerificationFailure.NOT_LINKED_WORKTREE)
                continue
            if stat.S_ISDIR(info.st_mode):
                if relative not in directories:
                    changes.append(WorktreeChange(relative, WorktreeChangeKind.DIRECTORY))
                pending.append((relative, full, _proven_identity(info)))
                continue
            if not stat.S_ISREG(info.st_mode):
                raise _Failed(VerificationFailure.UNSUPPORTED_ENTRY)  # nunca aberto
            spec = tree.get(relative)
            if spec is None:
                changes.append(WorktreeChange(relative, WorktreeChangeKind.ADDED))
                continue
            seen.add(relative)
            mode, oid, size = spec
            executable = bool(info.st_mode & stat.S_IXUSR)
            if (
                info.st_size != size
                or (_POSIX_MODES and executable != (mode == "100755"))
                or _blob_oid(full, info, git, budget) != oid
            ):
                changes.append(WorktreeChange(relative, WorktreeChangeKind.MODIFIED))
    changes.extend(WorktreeChange(path, WorktreeChangeKind.DELETED) for path in tree.keys() - seen)
    return changes


def _blob_oid(path: str, info: os.stat_result, git: MediatedGit, budget: VerificationBudget) -> str:
    """O oid de blob do arquivo **no formato do repositório**, em streaming."""
    update, hexdigest = git.blob_hasher(info.st_size)
    _read_regular(path, info, update, budget)
    return hexdigest()


def _base_and_index(
    git: MediatedGit, base_commit: str, budget: VerificationBudget
) -> tuple[dict[str, tuple[str, str, int]], set[str], set[str]]:
    """`(árvore do base, diretórios do base, caminhos com índice divergente)` — orçamento cobrado
    por registro. Symlink/gitlink no base ou caminho sem representação: falha."""
    listing = _mediated(lambda: git.list_tree(base_commit), budget)
    index = _mediated(git.list_index, budget)
    diverging = _mediated(lambda: git.index_divergence(base_commit), budget)
    tree: dict[str, tuple[str, str, int]] = {}
    directories: set[str] = set()
    if listing.unrepresentable or index.unrepresentable or diverging.unrepresentable:
        raise _Failed(VerificationFailure.UNREPRESENTABLE_PATH)
    for entry in listing.entries:
        budget.consume_entry()
        if entry.kind is not EntryKind.FILE or entry.size is None:
            raise _Failed(VerificationFailure.UNVERIFIABLE)
        tree[entry.path] = (entry.mode, entry.oid, entry.size)
        parts = entry.path.split("/")
        directories.update("/".join(parts[:depth]) for depth in range(1, len(parts)))
    in_index: dict[str, tuple[str, str, int]] = {}
    for staged in index.entries:
        budget.consume_entry()
        in_index[staged.path] = (staged.mode, staged.oid, staged.stage)
    wanted = {path: (mode, oid, 0) for path, (mode, oid, _size) in tree.items()}
    paths = {
        path for path in in_index.keys() | wanted.keys() if in_index.get(path) != wanted.get(path)
    }
    for path in diverging.entries:
        budget.consume_entry()
        paths.add(path)
    return tree, directories, paths


def worktree_changes(
    workspace_path: str,
    *,
    base_commit: str,
    expected_prefix: str,
    main_toplevel: str,
    main_git_common_dir: str,
    budget: VerificationBudget,
) -> WorktreeChanges | VerificationFailure:
    """Todas as mudanças da worktree do run contra ``base_commit``, ou o motivo de não saber.

    ``workspace_path`` é o workspace **dentro** da worktree (o `cwd` do Test Runner);
    ``expected_prefix`` é o prefixo que o binding registrou. A análise cobre a worktree
    **inteira**; decidir o que está fora do workspace é de quem chama. ``base_commit`` precisa
    ter o formato de objeto do repositório (40 ou 64 hex).
    """
    git_path = shutil.which("git")
    if git_path is None:
        return VerificationFailure.UNVERIFIABLE
    try:
        toplevel, object_format = _check_identity(
            git_path,
            workspace_path,
            expected_prefix=expected_prefix,
            main_toplevel=main_toplevel,
            main_git_common_dir=main_git_common_dir,
            budget=budget,
        )
        if not isinstance(base_commit, str) or not oid_pattern(object_format).fullmatch(
            base_commit
        ):
            raise _Failed(VerificationFailure.UNSUPPORTED_OBJECT_FORMAT)
        head = _git(budget, git_path, toplevel, "rev-parse", "--verify", "--quiet", "HEAD")
        head_text = None if head is None else _decode_text(head)
        if head_text is None or head_text.strip() != base_commit:
            raise _Failed(VerificationFailure.HEAD_MOVED)

        # O prazo de cada processo é recalculado **por chamada** (`remaining_s`): nunca o que
        # restava quando a sessão foi criada (E8.3-FINAL-001).
        git = MediatedGit(
            toplevel,
            expected_prefix="",
            is_cancelled=budget.interrupted,
            capture_bytes=_GIT_CAPTURE_BYTES,
            timeout_s=_GIT_STEP_TIMEOUT_S,
            remaining_s=budget.remaining_s,
        )
        if _mediated(lambda: git.object_format, budget) != object_format:
            raise _Failed(VerificationFailure.UNVERIFIABLE)
        tree, directories, index_paths = _base_and_index(git, base_commit, budget)
        disk = _filesystem_changes(toplevel, tree, directories, git, budget)
        budget.check()
    except BudgetExhausted as exhausted:
        return exhausted.failure
    except _Failed as failed:
        return failed.failure
    unique = {(change.path, change.kind) for change in disk}
    unique |= {(path, WorktreeChangeKind.INDEX) for path in index_paths}
    ordered = sorted(unique, key=lambda item: (item[0].encode("utf-8"), item[1].value))
    return WorktreeChanges(tuple(WorktreeChange(path, kind) for path, kind in ordered))


__all__ = [
    "DEADLINE_S",
    "MAX_BYTES_READ",
    "MAX_ENTRIES",
    "BudgetExhausted",
    "MainTreeState",
    "VerificationBudget",
    "VerificationFailure",
    "WorktreeChange",
    "WorktreeChangeKind",
    "WorktreeChanges",
    "main_tree_state",
    "oid_pattern",
    "parse_object_format",
    "stream_digest",
    "worktree_changes",
]
