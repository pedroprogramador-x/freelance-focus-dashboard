"""Subset **fechado** de unified diff do `ApplyPatch` V1 (E7.5-C): parser e aplicação em memória.

Módulo **puro**: só `str`. Zero filesystem, zero `SafetyPolicy`, zero banco, zero processo, zero
git, zero `path_runtime`. Transforma o texto do patch em estrutura tipada e aplica hunks sobre
linhas já em memória. **Não decide se um caminho é seguro** — o caminho extraído do cabeçalho
segue, inteiro, pela pipeline normal de path de `patch_ops`.

## Gramática aceita (e só ela)

```
patch    := section+
section  := "--- " old LF "+++ " new LF hunk+
old      := "a/" REL | "/dev/null"
new      := "b/" REL | "/dev/null"
hunk     := "@@ -" N ["," N] " +" N ["," N] " @@" [" " heading] LF body
body     := (" " text | "-" text | "+" text | "\\ No newline at end of file")*
```

* UPDATE = `a/P` → `b/P` (mesmo `P`); CREATE = `/dev/null` → `b/P`; DELETE = `a/P` → `/dev/null`.
  `/dev/null` dos dois lados é inválido; `P` diferente nos dois lados seria *rename* — não
  suportado.
* O cabeçalho do hunk é lido **por contagem** (`old_count`/`new_count`); count omitido = 1.
  `heading` (o trecho de contexto que o git escreve depois de `@@`) é só exibição: ignorado.
* Uma linha do patch termina em `\\n`; um `\\r` imediatamente antes é terminador do **patch**
  (patch gerado contra arquivo CRLF), não conteúdo. Qualquer outro `\\r` é recusado.

Fora da gramática: `diff --git`, `index`, modos, rename/copy, binário, combined diff, context
diff, timestamp/TAB no cabeçalho, caminho entre aspas → `unsupported_patch_format`. Qualquer
outra linha fora de hunk, linha vazia, prefixo inválido em hunk, contagem inconsistente, hunk
fora de ordem/sobreposto → `invalid_patch`. Nada é ignorado em silêncio.

## Aplicação exata — sem fuzz

`apply_section` aplica cada hunk **exatamente** na posição declarada: contexto e linhas
removidas precisam coincidir byte a byte (como `str`) com a linha de base naquela posição.
Não há busca de contexto em outra posição, offset automático, *closest match* nem
normalização de espaço. Divergência → `patch_conflict`.

## Fim de linha

O arquivo existente precisa ter **uma** convenção (só LF ou só CRLF); misturado, ou com `\\r`
solto, → `unsupported_text_format` (nunca normalização silenciosa). O resultado usa a mesma
convenção em todas as linhas — as inalteradas ficam byte a byte iguais, as adicionadas seguem a
do arquivo. Arquivo sem nenhum terminador (e CREATE) usa LF. `\\ No newline at end of file` é
suportado com semântica exata (ver `apply_section`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

#: Códigos fixos que este módulo produz. Nenhum carrega linha do patch nem conteúdo.
INVALID_PATCH = "invalid_patch"
PATCH_CONFLICT = "patch_conflict"
UNSUPPORTED_PATCH_FORMAT = "unsupported_patch_format"
UNSUPPORTED_TEXT_FORMAT = "unsupported_text_format"

NO_NEWLINE_MARKER = "\\ No newline at end of file"


class PatchError(Exception):
    """Falha de parse ou de aplicação. A mensagem é **só** o código fixo — nunca a linha."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PatchOperation(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class LineKind(str, Enum):
    CONTEXT = " "
    DELETE = "-"
    ADD = "+"


@dataclass(frozen=True, slots=True)
class HunkLine:
    kind: LineKind
    text: str


@dataclass(frozen=True, slots=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[HunkLine, ...]
    #: A última linha do lado antigo (`-` ou ` `) não tinha terminador no arquivo original.
    old_no_eol: bool = False
    #: A última linha do lado novo (`+` ou ` `) não terá terminador no resultado.
    new_no_eol: bool = False

    @property
    def old_begin(self) -> int:
        """Índice (base 0) da primeira linha antiga coberta; com `old_count == 0`, o ponto de
        inserção (`old_start` é a linha **depois** da qual se insere)."""
        return self.old_start - 1 if self.old_count else self.old_start

    @property
    def new_begin(self) -> int:
        return self.new_start - 1 if self.new_count else self.new_start

    @property
    def old_end(self) -> int:
        return self.old_begin + self.old_count


@dataclass(frozen=True, slots=True)
class FileSection:
    operation: PatchOperation
    #: O caminho **relativo** do cabeçalho, sem o prefixo `a/`/`b/`. Não validado aqui.
    path: str
    hunks: tuple[Hunk, ...]


@dataclass(frozen=True, slots=True)
class ParsedPatch:
    sections: tuple[FileSection, ...]

    @property
    def hunk_count(self) -> int:
        return sum(len(section.hunks) for section in self.sections)


# ------------------------------------------------------------------------------------ parser

_HUNK_HEADER = re.compile(r"@@ -(\d{1,9})(?:,(\d{1,9}))? \+(\d{1,9})(?:,(\d{1,9}))? @@(?: .*)?")

#: Linhas fora de hunk que pertencem a formatos **conhecidos** mas fora do subset V1.
_UNSUPPORTED_PREFIXES = (
    "diff ",
    "index ",
    "new file mode",
    "deleted file mode",
    "old mode",
    "new mode",
    "similarity index",
    "dissimilarity index",
    "rename from",
    "rename to",
    "copy from",
    "copy to",
    "GIT binary patch",
    "Binary files ",
    "literal ",
    "delta ",
    "@@@",
    "*** ",
    "Only in ",
)


def _fail(code: str) -> PatchError:
    return PatchError(code)


def _split_patch_lines(text: str) -> list[str]:
    if "\x00" in text:
        raise _fail(INVALID_PATCH)
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    result: list[str] = []
    for line in lines:
        if line.endswith("\r"):
            line = line[:-1]  # terminador CRLF do próprio patch
        if "\r" in line:
            raise _fail(UNSUPPORTED_TEXT_FORMAT)
        result.append(line)
    return result


def _header_path(line: str, prefix: str) -> str | None:
    """`a/P`/`b/P` → `P`; `/dev/null` → `None`. Não decide se `P` é seguro."""
    value = line[4:]
    if "\t" in value or value.startswith('"'):
        raise _fail(UNSUPPORTED_PATCH_FORMAT)  # timestamp/metadata por TAB, caminho com aspas
    if value == "/dev/null":
        return None
    if not value.startswith(prefix) or len(value) == len(prefix):
        raise _fail(INVALID_PATCH)
    return value[len(prefix) :]


def _parse_hunk(lines: list[str], index: int) -> tuple[Hunk, int]:
    header = lines[index]
    match = _HUNK_HEADER.fullmatch(header)
    if match is None:
        raise _fail(UNSUPPORTED_PATCH_FORMAT if header.startswith("@@@") else INVALID_PATCH)
    old_start, new_start = int(match.group(1)), int(match.group(3))
    old_count = 1 if match.group(2) is None else int(match.group(2))
    new_count = 1 if match.group(4) is None else int(match.group(4))
    if old_count == 0 and new_count == 0:
        raise _fail(INVALID_PATCH)
    if (old_count and old_start == 0) or (new_count and new_start == 0):
        raise _fail(INVALID_PATCH)

    body: list[HunkLine] = []
    old_seen = new_seen = 0
    old_no_eol = new_no_eol = False
    previous: LineKind | None = None  # tipo da linha anterior; `None` depois de um marcador
    index += 1

    def take_marker() -> None:
        nonlocal old_no_eol, new_no_eol, previous
        if previous is None:
            raise _fail(INVALID_PATCH)  # marcador sem linha antes, ou dois seguidos
        if previous in (LineKind.CONTEXT, LineKind.DELETE):
            old_no_eol = True
        if previous in (LineKind.CONTEXT, LineKind.ADD):
            new_no_eol = True
        previous = None

    while old_seen < old_count or new_seen < new_count:
        if index >= len(lines):
            raise _fail(INVALID_PATCH)  # hunk truncado
        line = lines[index]
        index += 1
        if line == NO_NEWLINE_MARKER:
            take_marker()
            continue
        if not line or line[0] not in " -+":
            raise _fail(INVALID_PATCH)
        kind = LineKind(line[0])
        if kind is not LineKind.ADD:
            if old_no_eol:
                raise _fail(INVALID_PATCH)  # linha antiga depois do "fim sem terminador"
            old_seen += 1
        if kind is not LineKind.DELETE:
            if new_no_eol:
                raise _fail(INVALID_PATCH)
            new_seen += 1
        if old_seen > old_count or new_seen > new_count:
            raise _fail(INVALID_PATCH)  # cabeçalho inconsistente com as linhas
        body.append(HunkLine(kind, line[1:]))
        previous = kind
    if index < len(lines) and lines[index] == NO_NEWLINE_MARKER:
        take_marker()
        index += 1
    if not any(line.kind is not LineKind.CONTEXT for line in body):
        raise _fail(INVALID_PATCH)  # hunk sem mudança
    hunk = Hunk(old_start, old_count, new_start, new_count, tuple(body), old_no_eol, new_no_eol)
    return hunk, index


def _operation(old: str | None, new: str | None) -> tuple[PatchOperation, str]:
    if old is None and new is None:
        raise _fail(INVALID_PATCH)
    if old is None:
        assert new is not None
        return PatchOperation.CREATE, new
    if new is None:
        return PatchOperation.DELETE, old
    if old != new:
        raise _fail(UNSUPPORTED_PATCH_FORMAT)  # rename/copy
    return PatchOperation.UPDATE, old


def _validate_section(operation: PatchOperation, hunks: tuple[Hunk, ...]) -> None:
    """Ordem crescente, sem sobreposição, `new_start` coerente, marcadores só no último hunk e
    hunks possíveis para a operação. Tudo aritmética: nenhuma linha de base é necessária."""
    delta = 0
    previous: Hunk | None = None
    for position, hunk in enumerate(hunks):
        if previous is not None and (
            hunk.old_begin <= previous.old_begin or hunk.old_begin < previous.old_end
        ):
            raise _fail(INVALID_PATCH)  # fora de ordem ou sobreposto
        if hunk.new_begin != hunk.old_begin + delta:
            raise _fail(INVALID_PATCH)  # `+new_start` não corresponde aos hunks anteriores
        if (hunk.old_no_eol or hunk.new_no_eol) and position != len(hunks) - 1:
            raise _fail(INVALID_PATCH)
        if operation is PatchOperation.CREATE and (hunk.old_count or hunk.old_start):
            raise _fail(INVALID_PATCH)  # contexto/remoção contra linha de base vazia
        if operation is PatchOperation.DELETE and hunk.new_count:
            raise _fail(INVALID_PATCH)  # DELETE não adiciona nada
        delta += hunk.new_count - hunk.old_count
        previous = hunk


def parse_unified_diff(text: str, *, checkpoint: Callable[[], None] | None = None) -> ParsedPatch:
    """Parseia o subset V1. ``checkpoint`` (opcional) é chamado antes de cada seção e de cada
    hunk — é por onde o chamador coopera com cancelamento; o parser não sabe o que ele faz."""
    lines = _split_patch_lines(text)
    sections: list[FileSection] = []
    seen_paths: set[str] = set()
    index = 0
    while index < len(lines):
        if checkpoint is not None:
            checkpoint()
        line = lines[index]
        if not line.startswith("--- "):
            if line.startswith(_UNSUPPORTED_PREFIXES):
                raise _fail(UNSUPPORTED_PATCH_FORMAT)
            raise _fail(INVALID_PATCH)
        old = _header_path(line, "a/")
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise _fail(INVALID_PATCH)
        new = _header_path(lines[index], "b/")
        index += 1
        operation, path = _operation(old, new)
        if path in seen_paths:
            raise _fail(INVALID_PATCH)  # duas seções para o mesmo caminho textual
        seen_paths.add(path)

        hunks: list[Hunk] = []
        while index < len(lines) and lines[index].startswith("@@"):
            if checkpoint is not None:
                checkpoint()
            hunk, index = _parse_hunk(lines, index)
            hunks.append(hunk)
        if not hunks:
            raise _fail(INVALID_PATCH)
        frozen = tuple(hunks)
        _validate_section(operation, frozen)
        sections.append(FileSection(operation, path, frozen))
    if not sections:
        raise _fail(INVALID_PATCH)
    return ParsedPatch(tuple(sections))


# ------------------------------------------------------------------------ modelo de texto


@dataclass(frozen=True, slots=True)
class TextLines:
    """Um texto como linhas **sem** terminador + a convenção única + se termina com ela."""

    lines: tuple[str, ...]
    eol: str = "\n"
    final_newline: bool = False

    def render(self) -> str:
        if not self.lines:
            return ""
        body = self.eol.join(self.lines)
        return body + self.eol if self.final_newline else body


EMPTY_TEXT = TextLines(())


def split_text(text: str) -> TextLines:
    """Separa um texto existente. Convenção misturada ou `\\r` solto → `unsupported_text_format`.

    Nada é normalizado: um texto aceito volta **idêntico** por `render()`.
    """
    if not text:
        return EMPTY_TEXT
    parts = text.split("\n")
    final_newline = parts[-1] == ""
    if final_newline:
        parts.pop()
    crlf = lf = 0
    lines: list[str] = []
    for position, part in enumerate(parts):
        terminated = final_newline or position < len(parts) - 1
        if terminated and part.endswith("\r"):
            crlf += 1
            part = part[:-1]
        elif terminated:
            lf += 1
        if "\r" in part:
            raise _fail(UNSUPPORTED_TEXT_FORMAT)
        lines.append(part)
    if crlf and lf:
        raise _fail(UNSUPPORTED_TEXT_FORMAT)
    return TextLines(tuple(lines), "\r\n" if crlf else "\n", final_newline)


def apply_section(baseline: TextLines, section: FileSection) -> TextLines:
    """Aplica os hunks de **uma** seção, em memória, **exatamente** onde declaram.

    Semântica de `\\ No newline at end of file`:

    * se a linha de base **não** termina com terminador e algum hunk alcança o fim dela, a última
      linha antiga desse hunk precisa estar marcada (`old_no_eol`); e um marcador antigo só vale
      se o hunk alcança o fim de uma linha de base sem terminador final — senão `patch_conflict`;
    * se o último hunk alcança o fim, o resultado termina com terminador **a menos** que a última
      linha nova esteja marcada (`new_no_eol`); se não alcança, o final da linha de base é
      preservado, e um marcador novo é `patch_conflict`.

    DELETE exige resultado **vazio** (senão `invalid_patch`): o patch tem de demonstrar a
    transformação inteira, não basta o `/dev/null`.
    """
    old = baseline.lines
    out: list[str] = []
    cursor = 0
    for hunk in section.hunks:
        begin = hunk.old_begin
        if hunk.old_end > len(old):
            raise _fail(PATCH_CONFLICT)  # a posição declarada não existe na linha de base
        out.extend(old[cursor:begin])
        position = begin
        for line in hunk.lines:
            if line.kind is not LineKind.ADD:
                if old[position] != line.text:
                    raise _fail(PATCH_CONFLICT)  # sem fuzz: só a posição declarada
                position += 1
            if line.kind is not LineKind.DELETE:
                out.append(line.text)
        cursor = position
    out.extend(old[cursor:])

    last = section.hunks[-1]
    reaches_end = last.old_end == len(old)
    if old and not baseline.final_newline and reaches_end and not last.old_no_eol:
        raise _fail(PATCH_CONFLICT)
    if last.old_no_eol and (not old or baseline.final_newline or not reaches_end):
        raise _fail(PATCH_CONFLICT)
    if reaches_end:
        final_newline = not last.new_no_eol
    else:
        if last.new_no_eol:
            raise _fail(PATCH_CONFLICT)
        final_newline = baseline.final_newline

    result = TextLines(tuple(out), baseline.eol, final_newline)
    if section.operation is PatchOperation.DELETE and result.lines:
        raise _fail(INVALID_PATCH)
    return result


__all__ = [
    "EMPTY_TEXT",
    "INVALID_PATCH",
    "NO_NEWLINE_MARKER",
    "PATCH_CONFLICT",
    "UNSUPPORTED_PATCH_FORMAT",
    "UNSUPPORTED_TEXT_FORMAT",
    "FileSection",
    "Hunk",
    "HunkLine",
    "LineKind",
    "ParsedPatch",
    "PatchError",
    "PatchOperation",
    "TextLines",
    "apply_section",
    "parse_unified_diff",
    "split_text",
]
