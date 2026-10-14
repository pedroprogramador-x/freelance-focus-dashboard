"""Métricas do diff da worktree (E8.4.4) — só leitura, **sem `git diff`**, sem filtro, sem shell.

Mede `files_changed`, `diff_added` e `diff_removed` ([02] §10) para as mudanças que a verificação
pós-execução da E8.3 **acabou de comprovar** (`worktree_changes`). Duas fontes, as duas lidas de
forma estável e conferidas por oid:

* **base** — o blob do `base_commit`, lido por `MediatedGit.read_blob` (que recalcula o oid);
* **worktree** — o arquivo regular, lido por `_read_regular` (`lstat` antes/depois, mesmo objeto) e
  cujo oid de blob é recalculado dos **bytes lidos** e comparado com o oid que a verificação
  observou. Conteúdo diferente do verificado é `CHANGED_SINCE_VERIFIED`: nunca se mede um artefato
  que não é o verificado, e a identidade da worktree é reconferida (`_check_identity` + `HEAD`)
  antes de qualquer leitura.

## Integridade primeiro, mensurabilidade depois

Para **todo** caminho verificado, antes de qualquer juízo de mensurabilidade, o tipo (regular,
diretório, ausente), a identidade e o conteúdo (oid recalculado do arquivo **inteiro**, sob o
orçamento da E8.3) são revalidados. Divergência é `CHANGED_SINCE_VERIFIED` e vence qualquer outra
falha: um arquivo binário ou grande não interrompe a revalidação dos demais, e trocar o verificado
por diretório, binário ou conteúdo acima do limite é divergência, não "métrica indisponível". Só
`BINARY`, `TOO_LARGE` e `TOO_MANY_PATHS` — com a integridade de tudo revalidada
(`INTEGRITY_PROVEN_FAILURES`) — permitem métricas `NULL` com os testes seguindo; orçamento
esgotado, cancelamento e leitura que não prova **bloqueiam**.

## Tudo ou nada

O resultado é `DiffMetrics` **completo** ou uma `DiffMeasureFailure`: nenhum valor parcial, nenhuma
estimativa — nunca um zero que pareça medido.

## Limites (V1; não são parâmetros)

* `MAX_PATHS` caminhos mudados; `MAX_FILE_BYTES` por lado e `MAX_LINES` por lado;
* `MAX_TOTAL_BYTES` lidos no total (base + worktree) — também cobrados do orçamento da E8.3;
* `MAX_DIFF_OPS` passos de comparação, somados no diff inteiro;
* o orçamento (`VerificationBudget`): entradas, bytes, 60 s e cancelamento.

## A contagem: interrompível por construção

Myers (distância de edição mínima) sobre linhas internadas em inteiros, só a distância: memória
O(|a| + |b|), sem traço. Cada diagonal avaliada e cada avanço de "cobra" é um passo contado; a cada
`TICK_EVERY` passos o orçamento é consultado (cancelamento e prazo) e `MAX_DIFF_OPS` é imposto
dentro do laço. Não há região de processamento sem consulta, o que dispensa isolar a comparação em
processo. Linhas terminam em `
` (a última, sem terminador, também conta). É determinística e
mínima, mas **não é idêntica** a `git diff --numstat`: o valor é uma contagem de linhas, não um
patch. Mudança de modo e caminho de índice divergente contam em `files_changed` com zero linhas.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

from app.git_runtime import _decode_text
from app.git_runtime.mediated import MediatedGit, MediatedGitError
from app.git_runtime.post_execution import (
    _GIT_CAPTURE_BYTES,
    _GIT_STEP_TIMEOUT_S,
    BudgetExhausted,
    VerificationBudget,
    VerificationFailure,
    WorktreeChange,
    WorktreeChangeKind,
    _base_and_index,
    _check_identity,
    _Failed,
    _git,
    _lstat,
    _read_regular,
)
from app.git_runtime.worktree import _is_reparse

MAX_PATHS = 2_000
MAX_FILE_BYTES = 256 * 1024
MAX_LINES = 5_000
MAX_TOTAL_BYTES = 8 * 1024 * 1024
#: Passos da comparação de linhas somados no diff inteiro (Myers; ~2-3 s no pior caso).
MAX_DIFF_OPS = 5_000_000


class DiffMeasureFailure(str, Enum):
    """Por que as métricas não existem. Nenhuma é "zero linhas"."""

    #: Algum lado contém NUL: não é texto mensurável por linhas.
    BINARY = "binary"
    #: Arquivo/linhas/volume/custo acima dos tetos acima.
    TOO_LARGE = "too_large"
    TOO_MANY_PATHS = "too_many_paths"
    #: A worktree não é mais a verificada: conteúdo diferente do oid observado, caminho que sumiu
    #: ou apareceu, objeto trocado durante a leitura.
    CHANGED_SINCE_VERIFIED = "changed_since_verified"
    #: Entrada que não é arquivo regular, link/reparse ou espécie de mudança que o contrato recusa.
    UNSUPPORTED = "unsupported"
    #: Leitura técnica do Git ou do filesystem falhou.
    UNVERIFIABLE = "unverifiable"
    LIMIT_EXCEEDED = "limit_exceeded"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    CANCELLED = "cancelled"


#: Falhas que dizem que a worktree **mudou depois de verificada** (não que a métrica é inviável).
CONTENT_DIVERGED_FAILURES: frozenset[DiffMeasureFailure] = frozenset(
    {DiffMeasureFailure.CHANGED_SINCE_VERIFIED}
)

#: As ÚNICAS falhas que significam "não dá para medir, mas a integridade do verificado foi
#: revalidada": só estas permitem métricas `NULL` com os testes seguindo. Divergência, orçamento
#: (limite/prazo/cancelamento) e leitura que não prova **não** estão aqui: bloqueiam a progressão.
INTEGRITY_PROVEN_FAILURES: frozenset[DiffMeasureFailure] = frozenset(
    {DiffMeasureFailure.BINARY, DiffMeasureFailure.TOO_LARGE, DiffMeasureFailure.TOO_MANY_PATHS}
)


@dataclass(frozen=True, slots=True)
class DiffMetrics:
    """As três métricas, **juntas**. ``files_changed`` ordenado por bytes UTF-8, sem duplicata."""

    files_changed: tuple[str, ...]
    diff_added: int
    diff_removed: int

    def __post_init__(self) -> None:
        if isinstance(self.diff_added, bool) or not isinstance(self.diff_added, int):
            raise ValueError("diff_added precisa ser int")
        if isinstance(self.diff_removed, bool) or not isinstance(self.diff_removed, int):
            raise ValueError("diff_removed precisa ser int")
        if self.diff_added < 0 or self.diff_removed < 0:
            raise ValueError("as contagens não podem ser negativas")
        if len(set(self.files_changed)) != len(self.files_changed):
            raise ValueError("files_changed sem duplicata")


_FAILURE_OF_VERIFICATION = {
    VerificationFailure.LIMIT_EXCEEDED: DiffMeasureFailure.LIMIT_EXCEEDED,
    VerificationFailure.DEADLINE_EXCEEDED: DiffMeasureFailure.DEADLINE_EXCEEDED,
    VerificationFailure.CANCELLED: DiffMeasureFailure.CANCELLED,
    VerificationFailure.CHANGED_DURING_READ: DiffMeasureFailure.CHANGED_SINCE_VERIFIED,
    VerificationFailure.LINK_OR_REPARSE: DiffMeasureFailure.UNSUPPORTED,
    VerificationFailure.UNSUPPORTED_ENTRY: DiffMeasureFailure.UNSUPPORTED,
}


class _Unmeasurable(Exception):
    def __init__(self, failure: DiffMeasureFailure) -> None:
        super().__init__(failure.value)
        self.failure = failure


def split_lines(data: bytes) -> list[bytes]:
    """Linhas com o terminador `\\n` preservado; a última sem terminador também é uma linha."""
    parts = data.split(b"\n")
    lines = [part + b"\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


#: Passos entre duas consultas ao orçamento (cancelamento/prazo). Cada passo é uma comparação de
#: linhas já internadas (inteiros): 2 048 deles levam bem menos de 1 ms.
TICK_EVERY = 2_048


def _intern(base: list[bytes], current: list[bytes]) -> tuple[list[int], list[int]]:
    """Linhas viram inteiros (igualdade de linha = igualdade de inteiro): comparações baratas."""
    table: dict[bytes, int] = {}
    first = [table.setdefault(line, len(table)) for line in base]
    second = [table.setdefault(line, len(table)) for line in current]
    return first, second


def _edit_distance(
    a: list[int], b: list[int], tick: Callable[[], None], max_ops: int
) -> tuple[int, int]:
    """Distância de edição mínima (inserções + remoções) entre ``a`` e ``b`` — o algoritmo de Myers,
    só a distância (memória O(|a| + |b|), sem traço). Devolve `(distância, passos)`.

    **Cooperativo e limitado**: cada passo (uma diagonal avaliada ou um avanço de "cobra") é
    contado; a cada `TICK_EVERY` passos ``tick`` consulta cancelamento e prazo (e pode levantar), e
    passar de ``max_ops`` levanta `TOO_LARGE`. Nenhuma região do laço fica sem consulta por mais
    de `TICK_EVERY` passos.
    """
    n, m = len(a), len(b)
    size = n + m
    if size == 0:
        tick()  # toda conclusão consulta cancelamento e prazo
        return 0, 0
    offset = size + 1
    v = [0] * (2 * size + 3)
    ops = 0
    next_check = min(TICK_EVERY, max_ops + 1)
    for d in range(size + 1):
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v[offset + k - 1] < v[offset + k + 1]):
                x = v[offset + k + 1]
            else:
                x = v[offset + k - 1] + 1
            y = x - k
            start = x
            while x < n and y < m and a[x] == b[y]:
                x += 1
                y += 1
            ops += 1 + (x - start)
            v[offset + k] = x
            if x >= n and y >= m:
                # A conclusão NÃO é isenta do orçamento: o teto vale por uma operação só e
                # cancelamento/prazo são consultados antes de qualquer resultado.
                if ops > max_ops:
                    raise _Unmeasurable(DiffMeasureFailure.TOO_LARGE)
                tick()
                return d, ops
            if ops >= next_check:
                if ops > max_ops:
                    raise _Unmeasurable(DiffMeasureFailure.TOO_LARGE)
                tick()
                next_check = min(ops + TICK_EVERY, max_ops + 1)
    return size, ops  # inalcançável: a distância máxima é |a| + |b|


def _count(
    base: bytes, current: bytes, tick: Callable[[], None], max_ops: int
) -> tuple[int, int, int]:
    before, after = _intern(split_lines(base), split_lines(current))
    lo = 0
    limit = min(len(before), len(after))
    while lo < limit and before[lo] == after[lo]:
        lo += 1
    hi_b, hi_a = len(before), len(after)
    while hi_b > lo and hi_a > lo and before[hi_b - 1] == after[hi_a - 1]:
        hi_b -= 1
        hi_a -= 1
    distance, ops = _edit_distance(before[lo:hi_b], after[lo:hi_a], tick, max_ops)
    delta = len(after) - len(before)  # adicionadas - removidas
    return (distance + delta) // 2, (distance - delta) // 2, ops


def count_changes(
    base: bytes,
    current: bytes,
    *,
    tick: Callable[[], None] | None = None,
    max_ops: int | None = None,
) -> tuple[int, int]:
    """`(adicionadas, removidas)` entre dois blobs de texto: a edição **mínima** por linhas.

    Interrompível (``tick``) e limitada (``max_ops``, padrão `MAX_DIFF_OPS`): passar do teto levanta
    `TOO_LARGE`. Os tetos de tamanho por arquivo são de quem chama.
    """
    added, removed, _ops = _count(
        base, current, tick or (lambda: None), MAX_DIFF_OPS if max_ops is None else max_ops
    )
    return added, removed


class _Volume:
    """O volume e o custo de comparação acumulados, com teto."""

    def __init__(self) -> None:
        self.bytes_read = 0
        self.ops = 0

    def add_bytes(self, count: int) -> None:
        self.bytes_read += count
        if self.bytes_read > MAX_TOTAL_BYTES:
            raise _Unmeasurable(DiffMeasureFailure.TOO_LARGE)


def _line_count(data: bytes) -> int:
    if b"\0" in data:
        raise _Unmeasurable(DiffMeasureFailure.BINARY)
    lines = split_lines(data)
    if len(lines) > MAX_LINES:
        raise _Unmeasurable(DiffMeasureFailure.TOO_LARGE)
    return len(lines)


def _changed() -> _Unmeasurable:
    return _Unmeasurable(DiffMeasureFailure.CHANGED_SINCE_VERIFIED)


def _lstat_or_changed(full: str) -> os.stat_result:
    try:
        return _lstat(full)
    except OSError:
        raise _changed() from None


def _verify_disk_file(
    toplevel: str,
    path: str,
    expected_oid: str | None,
    git: MediatedGit,
    budget: VerificationBudget,
    *,
    keep: bool,
) -> bytes | None:
    """**Integridade primeiro**: o arquivo da worktree ainda é o verificado?

    `lstat` (existe, não é link/reparse, é regular), leitura estável de **todo** o conteúdo (sob o
    orçamento da E8.3) com o oid recalculado e comparado ao observado pela verificação. Qualquer
    diferença — tipo, tamanho, conteúdo — é `CHANGED_SINCE_VERIFIED`, **antes** de qualquer juízo
    de mensurabilidade. Devolve os bytes só se ``keep`` e o arquivo couber no teto por arquivo.
    """
    full = os.path.join(toplevel, *path.split("/"))
    info = _lstat_or_changed(full)
    if expected_oid is None or _is_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise _changed()
    update, hexdigest = git.blob_hasher(info.st_size)
    collect = keep and info.st_size <= MAX_FILE_BYTES
    chunks: list[bytes] = []

    def feed(block: bytes) -> None:
        update(block)
        if collect:
            chunks.append(block)

    _read_regular(full, info, feed, budget)
    if hexdigest() != expected_oid:
        raise _changed()
    return b"".join(chunks) if collect else None


def _verify_directory(toplevel: str, path: str) -> None:
    info = _lstat_or_changed(os.path.join(toplevel, *path.split("/")))
    if _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise _changed()


def _absent(toplevel: str, path: str) -> bool:
    try:
        _lstat(os.path.join(toplevel, *path.split("/")))
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        raise _Unmeasurable(DiffMeasureFailure.UNVERIFIABLE) from None
    return False


def _measure(
    toplevel: str,
    changes: Sequence[WorktreeChange],
    tree: dict[str, tuple[str, str, int]],
    git: MediatedGit,
    budget: VerificationBudget,
) -> DiffMetrics:
    """Revalida **todos** os caminhos verificados e, se nada divergiu, mede.

    A revalidação (tipo, identidade e conteúdo) vem antes e independe da mensurabilidade: um
    arquivo binário, grande demais ou fora do teto de caminhos **não** interrompe a revalidação dos
    demais. A primeira falha de *mensuração* (`BINARY`/`TOO_LARGE`/`TOO_MANY_PATHS`) só é devolvida
    no fim, e só se toda a integridade ficou comprovada; qualquer divergência vence.
    """
    by_path: dict[str, set[WorktreeChangeKind]] = {}
    oids: dict[str, str | None] = {}
    for change in changes:
        by_path.setdefault(change.path, set()).add(change.kind)
        if change.oid is not None:
            oids[change.path] = change.oid
    unmeasurable: DiffMeasureFailure | None = (
        DiffMeasureFailure.TOO_MANY_PATHS if len(by_path) > MAX_PATHS else None
    )

    volume = _Volume()
    files: list[str] = []
    added = removed = 0

    def tick() -> None:
        budget.check()

    for path in sorted(by_path, key=lambda item: item.encode("utf-8")):
        budget.consume_entry()
        kinds = by_path[path]
        if kinds == {WorktreeChangeKind.DIRECTORY}:
            _verify_directory(toplevel, path)  # diretório novo: sem linhas, mas segue sendo um
            continue
        if WorktreeChangeKind.DIRECTORY in kinds:
            raise _changed()
        files.append(path)
        base_entry = tree.get(path)
        measuring = unmeasurable is None
        try:
            if WorktreeChangeKind.DELETED in kinds:
                if base_entry is None or not _absent(toplevel, path):
                    raise _changed()
                if measuring:
                    base = _read_base(base_entry, git, volume, budget)
                    removed += _line_count(base)
                continue

            if base_entry is None:
                # Fora do base: só pode ser `added` (com `index` opcional).
                allowed = {WorktreeChangeKind.ADDED, WorktreeChangeKind.INDEX}
                if WorktreeChangeKind.ADDED not in kinds or not kinds <= allowed:
                    raise _changed()
                current = _verify_disk_file(
                    toplevel, path, oids.get(path), git, budget, keep=measuring
                )
                if measuring:
                    if current is None:
                        raise _Unmeasurable(DiffMeasureFailure.TOO_LARGE)
                    volume.add_bytes(len(current))
                    added += _line_count(current)
                continue

            # Existe no base e no disco: `modified` (conteúdo/modo) ou só índice divergente.
            if not kinds <= {WorktreeChangeKind.MODIFIED, WorktreeChangeKind.INDEX}:
                raise _changed()
            expected = oids.get(path) or (
                base_entry[1] if kinds == {WorktreeChangeKind.INDEX} else None
            )
            current = _verify_disk_file(toplevel, path, expected, git, budget, keep=measuring)
            if not measuring:
                continue
            if current is None:
                raise _Unmeasurable(DiffMeasureFailure.TOO_LARGE)
            volume.add_bytes(len(current))
            if git.blob_oid(current) == base_entry[1]:
                continue  # mesmo conteúdo (mudança de modo/índice): zero linhas
            base = _read_base(base_entry, git, volume, budget)
            _line_count(base)
            _line_count(current)
            budget.check()
            lines_added, lines_removed, spent = _count(
                base, current, tick, max(0, MAX_DIFF_OPS - volume.ops)
            )
            volume.ops += spent
            added += lines_added
            removed += lines_removed
        except _Unmeasurable as failure:
            if failure.failure not in INTEGRITY_PROVEN_FAILURES:
                raise  # divergência ou leitura que não prova: nunca vira "métrica indisponível"
            unmeasurable = unmeasurable or failure.failure
    if unmeasurable is not None:
        raise _Unmeasurable(unmeasurable)
    return DiffMetrics(tuple(files), added, removed)


def _read_base(
    entry: tuple[str, str, int], git: MediatedGit, volume: _Volume, budget: VerificationBudget
) -> bytes:
    _mode, oid, size = entry
    if size > MAX_FILE_BYTES:
        raise _Unmeasurable(DiffMeasureFailure.TOO_LARGE)
    budget.check()
    try:
        data = git.read_blob(oid, size)
    except MediatedGitError:
        budget.check()
        raise _Unmeasurable(DiffMeasureFailure.UNVERIFIABLE) from None
    budget.consume_bytes(len(data))
    volume.add_bytes(len(data))
    return data


def measure_worktree_diff(
    workspace_path: str,
    *,
    base_commit: str,
    expected_prefix: str,
    main_toplevel: str,
    main_git_common_dir: str,
    changes: Sequence[WorktreeChange],
    budget: VerificationBudget,
) -> DiffMetrics | DiffMeasureFailure:
    """Mede o diff **verificado** da worktree do run, ou diz por que não consegue.

    ``changes`` são as mudanças devolvidas por `worktree_changes` na verificação que acabou de
    passar (com os oids observados); ``main_toplevel``/``main_git_common_dir`` são os da árvore
    principal (a worktree precisa continuar sendo a vinculada, diferente da principal). Nunca
    levanta por IO.
    """
    git_path = shutil.which("git")
    if git_path is None:
        return DiffMeasureFailure.UNVERIFIABLE
    try:
        toplevel, object_format = _check_identity(
            git_path,
            workspace_path,
            expected_prefix=expected_prefix,
            main_toplevel=main_toplevel,
            main_git_common_dir=main_git_common_dir,
            budget=budget,
        )
        head = _git(budget, git_path, toplevel, "rev-parse", "--verify", "--quiet", "HEAD")
        head_text = None if head is None else _decode_text(head)
        if head_text is None or head_text.strip() != base_commit:
            raise _Failed(VerificationFailure.HEAD_MOVED)
        git = MediatedGit(
            toplevel,
            expected_prefix="",
            is_cancelled=budget.interrupted,
            capture_bytes=_GIT_CAPTURE_BYTES,
            timeout_s=_GIT_STEP_TIMEOUT_S,
            remaining_s=budget.remaining_s,
        )
        if git.object_format != object_format:
            raise _Failed(VerificationFailure.UNVERIFIABLE)
        tree, _directories, _index = _base_and_index(git, base_commit, budget)
        metrics = _measure(toplevel, changes, tree, git, budget)
        budget.check()
    except BudgetExhausted as exhausted:
        return _FAILURE_OF_VERIFICATION.get(exhausted.failure, DiffMeasureFailure.UNVERIFIABLE)
    except _Failed as failed:
        return _FAILURE_OF_VERIFICATION.get(
            failed.failure,
            DiffMeasureFailure.CHANGED_SINCE_VERIFIED
            if failed.failure
            in (
                VerificationFailure.NOT_LINKED_WORKTREE,
                VerificationFailure.HEAD_MOVED,
                VerificationFailure.OPERATION_IN_PROGRESS,
            )
            else DiffMeasureFailure.UNVERIFIABLE,
        )
    except _Unmeasurable as unmeasurable:
        return unmeasurable.failure
    except Exception:  # defeito inesperado: indisponível, nunca parcial
        return DiffMeasureFailure.UNVERIFIABLE
    return metrics


__all__ = [
    "CONTENT_DIVERGED_FAILURES",
    "INTEGRITY_PROVEN_FAILURES",
    "MAX_DIFF_OPS",
    "MAX_FILE_BYTES",
    "MAX_LINES",
    "MAX_PATHS",
    "MAX_TOTAL_BYTES",
    "TICK_EVERY",
    "DiffMeasureFailure",
    "DiffMetrics",
    "count_changes",
    "measure_worktree_diff",
    "split_lines",
]
