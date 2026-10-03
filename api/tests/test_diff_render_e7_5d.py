"""E7.5-D — o renderizador **puro** de unified diff do `GitDiff` (`diff_render`).

Prova principal: o que ele renderiza é aceito pelo parser exato da E7.5-C, e aplicar o diff ao
texto antigo devolve o novo (ida e volta) dentro do contrato de texto/fim de linha da C — LF,
CRLF uniforme e sem newline final. O renderizador não converte convenções: uma criação em CRLF,
por exemplo, é aplicada pelo `ApplyPatch` V1 com LF (decisão congelada da C).
"""

from __future__ import annotations

import pytest

from app.tool_executor.diff_render import NO_NEWLINE_MARKER, render_file_diff
from app.tool_executor.unified_diff import (
    EMPTY_TEXT,
    PatchOperation,
    apply_section,
    parse_unified_diff,
    split_text,
)


def roundtrip(old: str | None, new: str | None) -> str:
    diff = render_file_diff("x.txt", old, new)
    (section,) = parse_unified_diff(diff + "\n").sections
    baseline = EMPTY_TEXT if old is None else split_text(old)
    result = apply_section(baseline, section).render()
    if new is None:
        assert section.operation is PatchOperation.DELETE
    return result


CASES = [
    ("a\nb\nc\n", "a\nB\nc\n"),
    ("a\nb\nc\n", "z\na\nb\nc\n"),
    ("a\nb\nc\n", "a\nb\nc\nd\n"),
    ("a\nb\nc\n", "a\nc\n"),
    ("a\nb\nc\n", ""),
    ("1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n11\n12\n", "1\nX\n3\n4\n5\n6\n7\n8\n9\n10\nY\n12\n"),
    ("a\nb", "a\nB"),
    ("a\nb", "a\nb\n"),
    ("a\nb\n", "a\nb"),
    ("a\r\nb\r\n", "a\r\nB\r\n"),
    ("a\r\nb", "a\r\nb\r\nc"),
    ("x\x0cy\nz\u2028w\n", "x\x0cy\nZ\u2028w\n"),
]


@pytest.mark.parametrize(("old", "new"), CASES)
def test_ida_e_volta_pelo_parser_e_aplicador_exatos(old: str, new: str) -> None:
    assert roundtrip(old, new) == new


@pytest.mark.parametrize("new", ["a\n", "a\nb"])
def test_criacao_ida_e_volta(new: str) -> None:
    assert roundtrip(None, new) == new
    assert render_file_diff("x.txt", None, new).startswith("--- /dev/null\n+++ b/x.txt\n")


def test_criacao_crlf_preserva_o_cr_nas_linhas_renderizadas() -> None:
    """O renderizador mostra o `\\r`; o ApplyPatch V1 cria com LF (decisão da E7.5-C)."""
    assert render_file_diff("x", None, "a\r\nb\r\n").split("\n")[3:] == ["+a\r", "+b\r"]


@pytest.mark.parametrize("old", ["a\n", "a\nb", "a\r\nb\r\n"])
def test_remocao_ida_e_volta(old: str) -> None:
    assert roundtrip(old, None) == ""
    assert render_file_diff("x.txt", old, None).startswith("--- a/x.txt\n+++ /dev/null\n")


def test_formato_exato_com_contexto_de_tres_linhas() -> None:
    old = "".join(f"{i}\n" for i in range(1, 11))
    new = old.replace("5\n", "cinco\n")
    assert render_file_diff("p/q.txt", old, new) == "\n".join(
        [
            "--- a/p/q.txt",
            "+++ b/p/q.txt",
            "@@ -2,7 +2,7 @@",
            " 2",
            " 3",
            " 4",
            "-5",
            "+cinco",
            " 6",
            " 7",
            " 8",
        ]
    )


def test_marcador_sem_newline_final() -> None:
    out = render_file_diff("x", "a\nb", "a\nc").split("\n")
    assert out[-4:] == ["-b", NO_NEWLINE_MARKER, "+c", NO_NEWLINE_MARKER]


def test_troca_so_de_fim_de_linha_nao_some() -> None:
    out = render_file_diff("x", "a\n", "a\r\n")
    assert "-a" in out.split("\n") and "+a\r" in out.split("\n")


def test_iguais_e_vazios_so_cabecalho() -> None:
    assert render_file_diff("x", "a\n", "a\n") == "--- a/x\n+++ b/x"
    assert render_file_diff("x", None, "") == "--- /dev/null\n+++ b/x"
    assert render_file_diff("x", "", None) == "--- a/x\n+++ /dev/null"


def test_deterministico() -> None:
    old = "".join(f"linha {i % 7}\n" for i in range(300))
    new = "".join(f"linha {(i * 3) % 7}\n" for i in range(300))
    assert render_file_diff("x", old, new) == render_file_diff("x", old, new)
    assert roundtrip(old, new) == new
