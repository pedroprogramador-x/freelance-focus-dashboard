"""Renderizador **puro** de unified diff do `GitDiff` (E7.5-D).

O Git nunca produz patch textual para o Developer (`diff -p`, `show -p`, `diff-tree -p` ficam
de fora: filtros, `textconv`, `diff.external` e saída dependente de configuração). O Git só
fornece os blobs crus; o diff é montado **aqui**, em Python, de forma determinística.

Formato (o mesmo subset que o `ApplyPatch` aceita):

```
--- a/<path>      (ou /dev/null na criação)
+++ b/<path>      (ou /dev/null na remoção)
@@ -<início>,<qtd> +<início>,<qtd> @@
 contexto / -removida / +adicionada
\\ No newline at end of file      (depois de uma linha sem terminador)
```

* Linhas comparadas **com** o terminador: uma troca só de fim de linha aparece como `-`/`+`
  (nada de normalização silenciosa); o `\\r` de uma linha CRLF fica no conteúdo, como no Git.
* Contexto de 3 linhas; contagens sempre explícitas; com contagem 0, o início é a linha
  **anterior** (convenção do unified diff).
* `difflib.SequenceMatcher` é determinístico para as mesmas entradas. Isto **só renderiza**:
  nenhum casamento aproximado é usado para aplicar coisa alguma.

Zero IO, zero processo, zero `safety`: entra texto, sai texto.
"""

from __future__ import annotations

from difflib import SequenceMatcher

NO_NEWLINE_MARKER = "\\ No newline at end of file"
_CONTEXT = 3


def _split(text: str) -> list[str]:
    """Linhas com o terminador, separando **só** em LF (`str.splitlines` também corta em
    CR solto, form feed, separadores Unicode… e mudaria o que é uma linha)."""
    if not text:
        return []
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _emit(lines: list[str], prefix: str, line: str) -> None:
    if line.endswith("\n"):
        lines.append(prefix + line[:-1])
    else:
        lines.append(prefix + line)
        lines.append(NO_NEWLINE_MARKER)


def render_file_diff(path: str, old: str | None, new: str | None) -> str:
    """O diff de **um** arquivo, sem `\\n` final. ``old=None`` cria; ``new=None`` remove.

    Arquivos iguais (ou criação/remoção de arquivo vazio) saem só com os dois cabeçalhos.
    """
    out = [
        "--- /dev/null" if old is None else f"--- a/{path}",
        "+++ /dev/null" if new is None else f"+++ b/{path}",
    ]
    a = _split(old or "")
    b = _split(new or "")
    if a == b:
        return "\n".join(out)
    matcher = SequenceMatcher(None, a, b)
    for group in matcher.get_grouped_opcodes(_CONTEXT):
        i1, i2 = group[0][1], group[-1][2]
        j1, j2 = group[0][3], group[-1][4]
        old_count, new_count = i2 - i1, j2 - j1
        old_start = i1 + 1 if old_count else i1
        new_start = j1 + 1 if new_count else j1
        out.append(f"@@ -{old_start},{old_count} +{new_start},{new_count} @@")
        for tag, a1, a2, b1, b2 in group:
            if tag == "equal":
                for line in a[a1:a2]:
                    _emit(out, " ", line)
                continue
            for line in a[a1:a2]:
                _emit(out, "-", line)
            for line in b[b1:b2]:
                _emit(out, "+", line)
    return "\n".join(out)


__all__ = ["NO_NEWLINE_MARKER", "render_file_diff"]
