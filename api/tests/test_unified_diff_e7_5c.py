"""E7.5-C — o parser puro de unified diff e a aplicação **exata** em memória (`unified_diff`).

Nada aqui toca filesystem: o módulo só recebe e devolve `str`/estruturas. Os casos cobrem a
gramática fechada (§5–§12 do plano), a aplicação sem fuzz (§10, §45) e a semântica de fim de
linha (§13, §54).
"""

from __future__ import annotations

import pytest

from app.tool_executor.unified_diff import (
    EMPTY_TEXT,
    INVALID_PATCH,
    PATCH_CONFLICT,
    UNSUPPORTED_PATCH_FORMAT,
    UNSUPPORTED_TEXT_FORMAT,
    FileSection,
    LineKind,
    PatchError,
    PatchOperation,
    TextLines,
    apply_section,
    parse_unified_diff,
    split_text,
)

# ------------------------------------------------------------------------------------ apoio


def update(path: str, *hunks: str) -> str:
    return f"--- a/{path}\n+++ b/{path}\n" + "".join(hunks)


def create(path: str, *lines: str) -> str:
    body = "".join(f"+{line}\n" for line in lines)
    return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{body}"


def delete(path: str, *lines: str) -> str:
    body = "".join(f"-{line}\n" for line in lines)
    return f"--- a/{path}\n+++ /dev/null\n@@ -1,{len(lines)} +0,0 @@\n{body}"


def code_of(text: str) -> str:
    with pytest.raises(PatchError) as caught:
        parse_unified_diff(text)
    return caught.value.code


def section(text: str) -> FileSection:
    parsed = parse_unified_diff(text)
    assert len(parsed.sections) == 1
    return parsed.sections[0]


def apply(old: str, patch: str) -> str:
    return apply_section(split_text(old), section(patch)).render()


def apply_code(old: str, patch: str) -> str:
    with pytest.raises(PatchError) as caught:
        apply(old, patch)
    return caught.value.code


FIVE = "l1\nl2\nl3\nl4\nl5\n"

# =============================================================================== 1. parser


def test_update_simples() -> None:
    parsed = parse_unified_diff(update("src/a.py", "@@ -1,2 +1,2 @@\n x\n-y\n+Y\n"))
    (only,) = parsed.sections
    assert only.operation is PatchOperation.UPDATE
    assert only.path == "src/a.py"
    (hunk,) = only.hunks
    assert (hunk.old_start, hunk.old_count, hunk.new_start, hunk.new_count) == (1, 2, 1, 2)
    assert [(line.kind, line.text) for line in hunk.lines] == [
        (LineKind.CONTEXT, "x"),
        (LineKind.DELETE, "y"),
        (LineKind.ADD, "Y"),
    ]
    assert parsed.hunk_count == 1


def test_create_simples() -> None:
    only = section(create("novo/x.txt", "a", "b"))
    assert only.operation is PatchOperation.CREATE
    assert only.path == "novo/x.txt"


def test_delete_simples() -> None:
    only = section(delete("velho.txt", "a"))
    assert only.operation is PatchOperation.DELETE
    assert only.path == "velho.txt"


def test_varios_arquivos_e_varios_hunks() -> None:
    text = (
        update("a.txt", "@@ -1 +1 @@\n-1\n+um\n", "@@ -3 +3 @@\n-3\n+tres\n")
        + create("b.txt", "b")
        + delete("c.txt", "c")
    )
    parsed = parse_unified_diff(text)
    assert [s.operation for s in parsed.sections] == [
        PatchOperation.UPDATE,
        PatchOperation.CREATE,
        PatchOperation.DELETE,
    ]
    assert parsed.hunk_count == 4


def test_count_omitido_vale_um() -> None:
    (hunk,) = section(update("a", "@@ -2 +2 @@\n-x\n+y\n")).hunks
    assert (hunk.old_count, hunk.new_count) == (1, 1)


def test_heading_depois_do_cabecalho_de_hunk_e_so_exibicao() -> None:
    (hunk,) = section(update("a", "@@ -1 +1 @@ def f():\n-x\n+y\n")).hunks
    assert [line.text for line in hunk.lines] == ["x", "y"]


def test_linha_de_conteudo_que_parece_cabecalho_e_lida_por_contagem() -> None:
    """Dentro do hunk vale a contagem: `--- x`/`+++ y` são linhas removida/adicionada."""
    (hunk,) = section(update("a", "@@ -1 +1 @@\n--- x\n+++ y\n")).hunks
    assert [(line.kind, line.text) for line in hunk.lines] == [
        (LineKind.DELETE, "-- x"),
        (LineKind.ADD, "++ y"),
    ]


def test_crlf_do_proprio_patch_e_terminador_nao_conteudo() -> None:
    text = update("a", "@@ -1 +1 @@\n-x\n+y\n").replace("\n", "\r\n")
    (hunk,) = section(text).hunks
    assert [line.text for line in hunk.lines] == ["x", "y"]


@pytest.mark.parametrize(
    "text",
    [
        "--- a/x\n@@ -1 +1 @@\n-a\n+b\n",  # sem +++
        "+++ b/x\n--- a/x\n@@ -1 +1 @@\n-a\n+b\n",  # ordem trocada
        "---a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n",  # sem espaço
        "--- x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n",  # sem prefixo a/
        "--- a/x\n+++ x\n@@ -1 +1 @@\n-a\n+b\n",  # sem prefixo b/
        "--- b/x\n+++ a/x\n@@ -1 +1 @@\n-a\n+b\n",  # prefixos trocados
        "--- a/\n+++ b/\n@@ -1 +1 @@\n-a\n+b\n",  # caminho vazio
        "--- a/x\n+++ b/x\n",  # sem hunk
        "",
        "\n",
        "texto solto\n",
    ],
)
def test_cabecalho_malformado_e_invalid_patch(text: str) -> None:
    assert code_of(text) == INVALID_PATCH


def test_dev_null_dos_dois_lados_e_invalido() -> None:
    assert code_of("--- /dev/null\n+++ /dev/null\n@@ -0,0 +1 @@\n+a\n") == INVALID_PATCH


def test_rename_old_diferente_de_new_nao_e_suportado() -> None:
    text = "--- a/x.txt\n+++ b/y.txt\n@@ -1 +1 @@\n-a\n+b\n"
    assert code_of(text) == UNSUPPORTED_PATCH_FORMAT


def test_caminho_duplicado_textual_e_invalido() -> None:
    text = update("a", "@@ -1 +1 @@\n-x\n+y\n") + update("a", "@@ -3 +3 @@\n-x\n+y\n")
    assert code_of(text) == INVALID_PATCH
    assert code_of(update("a", "@@ -1 +1 @@\n-x\n+y\n") + delete("a", "y")) == INVALID_PATCH


@pytest.mark.parametrize(
    "metadata",
    [
        "diff --git a/x b/x",
        "index 0123abc..4567def 100644",
        "new file mode 100644",
        "deleted file mode 100644",
        "old mode 100644",
        "new mode 100755",
        "similarity index 90%",
        "rename from x",
        "rename to y",
        "copy from x",
        "copy to y",
        "diff --cc x",
        "*** a/x",
    ],
)
def test_metadata_fora_do_subset_e_recusada_explicitamente(metadata: str) -> None:
    assert code_of(f"{metadata}\n" + update("x", "@@ -1 +1 @@\n-a\n+b\n")) == (
        UNSUPPORTED_PATCH_FORMAT
    )
    # nem entre seções ela é ignorada
    text = update("x", "@@ -1 +1 @@\n-a\n+b\n") + f"{metadata}\n" + create("y", "z")
    assert code_of(text) == UNSUPPORTED_PATCH_FORMAT


@pytest.mark.parametrize(
    "text",
    [
        "GIT binary patch\nliteral 3\nabc\n",
        "Binary files a/x and b/x differ\n",
        "--- a/x\n+++ b/x\nGIT binary patch\nliteral 0\n",
    ],
)
def test_patch_binario_nao_e_suportado(text: str) -> None:
    assert code_of(text) in {UNSUPPORTED_PATCH_FORMAT, INVALID_PATCH}
    assert code_of(text.replace("--- a/x\n+++ b/x\n", "")) == UNSUPPORTED_PATCH_FORMAT


def test_combined_diff_nao_e_suportado() -> None:
    text = "--- a/x\n+++ b/x\n@@@ -1,1 -1,1 +1,1 @@@\n- a\n +b\n"
    assert code_of(text) == UNSUPPORTED_PATCH_FORMAT


@pytest.mark.parametrize(
    "header",
    [
        "--- a/x\t2024-01-01 00:00:00\n+++ b/x\n",
        "--- a/x\n+++ b/x\t2024-01-01\n",
        '--- "a/x y"\n+++ "b/x y"\n',
    ],
)
def test_timestamp_tab_e_aspas_no_cabecalho_nao_sao_suportados(header: str) -> None:
    assert code_of(header + "@@ -1 +1 @@\n-a\n+b\n") == UNSUPPORTED_PATCH_FORMAT


def test_parser_nao_decide_seguranca_do_caminho() -> None:
    """Absoluto, `..`, `.git`, `.env`: o parser só extrai; quem decide é a pipeline de path."""
    for path in ("/etc/passwd", "../fora", ".git/config", ".env", "C:/x"):
        text = f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-a\n+b\n"
        assert section(text).path == path


@pytest.mark.parametrize(
    "header",
    [
        "@@ -1 +1\n",
        "@@ 1 +1 @@\n",
        "@@ -1,-2 +1 @@\n",
        "@@ --1 +1 @@\n",
        "@@ -a +1 @@\n",
        "@@ -1 +1 @@x\n",
        "@@  -1 +1 @@\n",
        "@@ -1,1 +1,1\n",
        "@@\n",
        "@@ -0,1 +1 @@\n",  # count > 0 com start 0
        "@@ -1 +0,1 @@\n",
        "@@ -0,0 +0,0 @@\n",  # hunk vazio
    ],
)
def test_cabecalho_de_hunk_invalido(header: str) -> None:
    assert code_of("--- a/x\n+++ b/x\n" + header + "-a\n+b\n") == INVALID_PATCH


@pytest.mark.parametrize(
    "hunk",
    [
        "@@ -1,2 +1,1 @@\n-a\n+b\n",  # old_count maior que as linhas (lê a próxima seção/EOF)
        "@@ -1,1 +1,2 @@\n-a\n+b\n",  # new_count maior
        "@@ -1,1 +1,1 @@\n-a\n-b\n+c\n",  # linha antiga a mais
        "@@ -1,1 +1,1 @@\n-a\n+b\n+c\n",  # linha nova a mais
        "@@ -1,2 +1,2 @@\n a\n",  # truncado
    ],
)
def test_contagem_inconsistente_e_invalid_patch(hunk: str) -> None:
    assert code_of("--- a/x\n+++ b/x\n" + hunk) == INVALID_PATCH


def test_contagem_maior_nao_consome_a_proxima_secao_em_silencio() -> None:
    text = update("x", "@@ -1,2 +1,2 @@\n-a\n+b\n") + create("y", "z")
    assert code_of(text) == INVALID_PATCH


def test_hunks_sobrepostos_ou_fora_de_ordem_sao_invalidos() -> None:
    overlap = update("x", "@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n", "@@ -3 +3 @@\n-c\n+C\n")
    assert code_of(overlap) == INVALID_PATCH
    backwards = update("x", "@@ -5 +5 @@\n-e\n+E\n", "@@ -2 +2 @@\n-b\n+B\n")
    assert code_of(backwards) == INVALID_PATCH
    same_insertion = update("x", "@@ -2,0 +3 @@\n+n\n", "@@ -2,0 +4 @@\n+m\n")
    assert code_of(same_insertion) == INVALID_PATCH


def test_hunks_adjacentes_em_ordem_sao_aceitos() -> None:
    text = update("x", "@@ -1 +1 @@\n-a\n+A\n", "@@ -2 +2 @@\n-b\n+B\n")
    assert len(section(text).hunks) == 2


def test_new_start_incoerente_com_os_hunks_anteriores_e_invalido() -> None:
    ok = update("x", "@@ -1 +1,2 @@\n-a\n+A\n+A2\n", "@@ -3 +4 @@\n-c\n+C\n")
    assert len(section(ok).hunks) == 2
    wrong = update("x", "@@ -1 +1,2 @@\n-a\n+A\n+A2\n", "@@ -3 +3 @@\n-c\n+C\n")
    assert code_of(wrong) == INVALID_PATCH


@pytest.mark.parametrize("line", ["", "x", "*a", "!a", "\t a", "#a"])
def test_linha_de_hunk_com_prefixo_invalido(line: str) -> None:
    assert code_of(f"--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n a\n{line}\n") == INVALID_PATCH


def test_hunk_sem_mudanca_e_invalido() -> None:
    assert code_of(update("x", "@@ -1 +1 @@\n a\n")) == INVALID_PATCH


def test_create_com_contexto_ou_remocao_e_invalido() -> None:
    assert code_of("--- /dev/null\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n") == INVALID_PATCH
    assert code_of("--- /dev/null\n+++ b/x\n@@ -1,1 +1,2 @@\n a\n+b\n") == INVALID_PATCH


def test_delete_que_adiciona_e_invalido() -> None:
    assert code_of("--- a/x\n+++ /dev/null\n@@ -1 +1 @@\n-a\n+b\n") == INVALID_PATCH


def test_nul_no_patch_e_invalid_patch() -> None:
    assert code_of(update("x", "@@ -1 +1 @@\n-a\n+b\x00\n")) == INVALID_PATCH


def test_cr_solto_no_patch_nao_e_suportado() -> None:
    assert code_of(update("x", "@@ -1 +1 @@\n-a\n+b\rc\n")) == UNSUPPORTED_TEXT_FORMAT


def test_linha_vazia_entre_secoes_nao_e_ignorada() -> None:
    text = update("x", "@@ -1 +1 @@\n-a\n+b\n") + "\n" + create("y", "z")
    assert code_of(text) == INVALID_PATCH


def test_marcador_no_newline_e_parseado() -> None:
    (hunk,) = section(update("x", "@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n")).hunks
    assert (hunk.old_no_eol, hunk.new_no_eol) == (True, False)
    (hunk,) = section(update("x", "@@ -1 +1 @@\n-a\n+b\n\\ No newline at end of file\n")).hunks
    assert (hunk.old_no_eol, hunk.new_no_eol) == (False, True)
    (hunk,) = section(
        update("x", "@@ -1,2 +1,2 @@\n-a\n+A\n b\n\\ No newline at end of file\n")
    ).hunks
    assert (hunk.old_no_eol, hunk.new_no_eol) == (True, True)


@pytest.mark.parametrize(
    "hunk",
    [
        "@@ -1 +1 @@\n\\ No newline at end of file\n-a\n+b\n",  # sem linha antes
        "@@ -1 +1 @@\n-a\n\\ No newline at end of file\n\\ No newline at end of file\n+b\n",
        "@@ -1,2 +1,2 @@\n-a\n\\ No newline at end of file\n b\n+c\n",  # antiga depois
        "@@ -1 +1,2 @@\n-a\n+b\n\\ No newline at end of file\n+c\n",  # nova depois
        "@@ -1 +1 @@\n-a\n+b\n\\ no newline at end of file\n",  # texto diferente
        "@@ -1 +1 @@\n-a\n+b\n\\\n",
    ],
)
def test_marcador_no_newline_mal_posicionado_e_invalido(hunk: str) -> None:
    assert code_of("--- a/x\n+++ b/x\n" + hunk) == INVALID_PATCH


def test_marcador_so_no_ultimo_hunk() -> None:
    text = update(
        "x",
        "@@ -1 +1 @@\n-a\n+A\n\\ No newline at end of file\n",
        "@@ -3 +3 @@\n-c\n+C\n",
    )
    assert code_of(text) == INVALID_PATCH


def test_checkpoint_e_chamado_por_secao_e_por_hunk() -> None:
    calls: list[int] = []
    text = update("x", "@@ -1 +1 @@\n-a\n+b\n", "@@ -3 +3 @@\n-c\n+d\n") + create("y", "z")
    parse_unified_diff(text, checkpoint=lambda: calls.append(1))
    assert len(calls) == 5  # 2 seções + 3 hunks


def test_checkpoint_que_levanta_interrompe_o_parse() -> None:
    class Stop(Exception):
        pass

    def stop() -> None:
        raise Stop

    with pytest.raises(Stop):
        parse_unified_diff(create("y", "z"), checkpoint=stop)


def test_erro_nunca_carrega_a_linha_do_patch() -> None:
    segredo = "linha-com-conteudo-sensivel"
    with pytest.raises(PatchError) as caught:
        parse_unified_diff(f"{segredo}\n")
    assert segredo not in str(caught.value)
    assert str(caught.value) == INVALID_PATCH


# ======================================================================= 2. aplicação exata


def test_contexto_e_remocao_exatos() -> None:
    assert apply(FIVE, update("x", "@@ -2,3 +2,3 @@\n l2\n-l3\n+L3\n l4\n")) == (
        "l1\nl2\nL3\nl4\nl5\n"
    )


def test_adicao_pura() -> None:
    assert apply(FIVE, update("x", "@@ -2,0 +3,2 @@\n+n1\n+n2\n")) == (
        "l1\nl2\nn1\nn2\nl3\nl4\nl5\n"
    )


def test_insercao_no_inicio() -> None:
    assert apply(FIVE, update("x", "@@ -0,0 +1 @@\n+zero\n")) == "zero\n" + FIVE


def test_hunk_na_primeira_linha() -> None:
    assert apply(FIVE, update("x", "@@ -1 +1 @@\n-l1\n+L1\n")) == "L1\nl2\nl3\nl4\nl5\n"


def test_hunk_no_fim() -> None:
    assert apply(FIVE, update("x", "@@ -5 +5,2 @@\n-l5\n+L5\n+l6\n")) == (
        "l1\nl2\nl3\nl4\nL5\nl6\n"
    )
    assert apply(FIVE, update("x", "@@ -5,0 +6 @@\n+l6\n")) == FIVE + "l6\n"


def test_multiplos_hunks() -> None:
    patch = update("x", "@@ -1 +1 @@\n-l1\n+A\n", "@@ -3 +2,0 @@\n-l3\n", "@@ -5 +4 @@\n-l5\n+E\n")
    assert apply(FIVE, patch) == "A\nl2\nl4\nE\n"


def test_remover_todas_as_linhas_num_update_deixa_vazio() -> None:
    assert apply("a\nb\n", update("x", "@@ -1,2 +0,0 @@\n-a\n-b\n")) == ""


def test_posicao_errada_e_conflito_mesmo_com_o_texto_em_outra_linha() -> None:
    """SEM FUZZ: `l3` existe, mas não na linha 2."""
    assert apply_code(FIVE, update("x", "@@ -2 +2 @@\n-l3\n+X\n")) == PATCH_CONFLICT


def test_conteudo_parecido_em_outra_posicao_nao_e_procurado() -> None:
    old = "dup\nx\ndup\ny\n"
    # o hunk declara a linha 2 com contexto `dup` antes; existe `dup` na 3, mas a posição é 1–2
    assert apply_code(old, update("x", "@@ -2,2 +2,2 @@\n dup\n-y\n+Y\n")) == PATCH_CONFLICT
    assert apply(old, update("x", "@@ -3,2 +3,2 @@\n dup\n-y\n+Y\n")) == "dup\nx\ndup\nY\n"


@pytest.mark.parametrize("offset", [-1, 1])
def test_offset_de_uma_linha_nao_e_aceito(offset: int) -> None:
    start = 3 + offset
    patch = update("x", f"@@ -{start},2 +{start},2 @@\n l3\n-l4\n+L4\n")
    assert apply_code(FIVE, patch) == PATCH_CONFLICT
    assert apply(FIVE, update("x", "@@ -3,2 +3,2 @@\n l3\n-l4\n+L4\n")) == ("l1\nl2\nl3\nL4\nl5\n")


def test_contexto_divergente_e_conflito() -> None:
    assert apply_code(FIVE, update("x", "@@ -2,2 +2,2 @@\n L2\n-l3\n+X\n")) == PATCH_CONFLICT
    # sem normalização de espaço
    assert apply_code(FIVE, update("x", "@@ -2,2 +2,2 @@\n l2 \n-l3\n+X\n")) == PATCH_CONFLICT


def test_texto_removido_divergente_e_conflito() -> None:
    assert apply_code(FIVE, update("x", "@@ -3 +3 @@\n-L3\n+X\n")) == PATCH_CONFLICT


def test_hunk_alem_do_fim_e_conflito() -> None:
    assert apply_code(FIVE, update("x", "@@ -6 +6 @@\n-l6\n+X\n")) == PATCH_CONFLICT
    assert apply_code(FIVE, update("x", "@@ -7,0 +8 @@\n+X\n")) == PATCH_CONFLICT


def test_delete_precisa_transformar_em_vazio() -> None:
    assert apply("a\n", delete("x", "a")) == ""
    assert apply_code("a\nb\n", delete("x", "a")) == INVALID_PATCH


def test_create_contra_linha_de_base_vazia() -> None:
    assert apply_section(EMPTY_TEXT, section(create("x", "a", "b"))).render() == "a\nb\n"


# ========================================================================= 3. fim de linha


def test_split_e_render_sao_identidade() -> None:
    for text in ("", "a", "a\n", "a\nb", "a\r\nb\r\n", "a\r\nb", "\n", "\r\n", "\n\n"):
        assert split_text(text).render() == text


def test_lf_preservado() -> None:
    assert apply("a\nb\n", update("x", "@@ -2 +2 @@\n-b\n+B\n")) == "a\nB\n"


def test_crlf_preservado_e_adicoes_seguem_o_arquivo() -> None:
    old = "a\r\nb\r\nc\r\n"
    assert apply(old, update("x", "@@ -2 +2,2 @@\n-b\n+B\n+B2\n")) == "a\r\nB\r\nB2\r\nc\r\n"


def test_patch_com_crlf_contra_arquivo_crlf() -> None:
    old = "a\r\nb\r\n"
    patch = update("x", "@@ -2 +2 @@\n-b\n+B\n").replace("\n", "\r\n")
    assert apply(old, patch) == "a\r\nB\r\n"


def test_misturado_nao_e_suportado_nem_normalizado() -> None:
    with pytest.raises(PatchError) as caught:
        split_text("a\r\nb\nc\r\n")
    assert caught.value.code == UNSUPPORTED_TEXT_FORMAT


def test_cr_solto_no_arquivo_nao_e_suportado() -> None:
    for text in ("a\rb\n", "a\n\r", "a\r"):
        with pytest.raises(PatchError) as caught:
            split_text(text)
        assert caught.value.code == UNSUPPORTED_TEXT_FORMAT


def test_arquivo_sem_terminador_usa_lf() -> None:
    assert split_text("abc") == TextLines(("abc",), "\n", False)


NO_EOL = "\\ No newline at end of file\n"


def test_sem_newline_final_exige_marcador_quando_o_hunk_alcanca_o_fim() -> None:
    old = "a\nb"
    assert apply(old, update("x", f"@@ -2 +2 @@\n-b\n{NO_EOL}+B\n{NO_EOL}")) == "a\nB"
    assert apply(old, update("x", f"@@ -2 +2 @@\n-b\n{NO_EOL}+B\n")) == "a\nB\n"
    # sem o marcador antigo: a linha de base não é a que o patch descreve
    assert apply_code(old, update("x", "@@ -2 +2 @@\n-b\n+B\n")) == PATCH_CONFLICT
    # inserção depois de uma última linha sem terminador: impossível sem tocá-la
    assert apply_code(old, update("x", "@@ -2,0 +3 @@\n+c\n")) == PATCH_CONFLICT


def test_hunk_longe_do_fim_preserva_a_falta_de_newline_final() -> None:
    assert apply("a\nb\nc", update("x", "@@ -1 +1 @@\n-a\n+A\n")) == "A\nb\nc"


def test_marcador_antigo_contra_arquivo_com_newline_e_conflito() -> None:
    assert apply_code("a\nb\n", update("x", f"@@ -2 +2 @@\n-b\n{NO_EOL}+B\n")) == PATCH_CONFLICT


def test_marcador_novo_longe_do_fim_e_conflito() -> None:
    patch = update("x", f"@@ -1 +1 @@\n-a\n+A\n{NO_EOL}")
    assert apply_code("a\nb\n", patch) == PATCH_CONFLICT


def test_remover_o_newline_final() -> None:
    assert apply("a\nb\n", update("x", f"@@ -2 +2 @@\n-b\n+b\n{NO_EOL}")) == "a\nb"


def test_marcador_em_contexto_vale_para_os_dois_lados() -> None:
    assert apply("a\nb", update("x", f"@@ -1,2 +1,2 @@\n-a\n+A\n b\n{NO_EOL}")) == "A\nb"


def test_remover_a_ultima_linha_sem_newline() -> None:
    assert apply("a\nb\nc", update("x", f"@@ -2,2 +2 @@\n b\n-c\n{NO_EOL}")) == "a\nb\n"


def test_crlf_sem_newline_final() -> None:
    assert apply("a\r\nb", update("x", f"@@ -2 +2 @@\n-b\n{NO_EOL}+B\n{NO_EOL}")) == "a\r\nB"


def test_create_sem_newline_final() -> None:
    patch = f"--- /dev/null\n+++ b/x\n@@ -0,0 +1 @@\n+a\n{NO_EOL}"
    assert apply_section(EMPTY_TEXT, section(patch)).render() == "a"


def test_delete_de_arquivo_sem_newline_final() -> None:
    assert apply("a", f"--- a/x\n+++ /dev/null\n@@ -1 +0,0 @@\n-a\n{NO_EOL}") == ""
    assert apply_code("a", delete("x", "a")) == PATCH_CONFLICT


def test_linhas_inalteradas_ficam_byte_a_byte() -> None:
    old = "ação\r\n  espaço \r\n\tTAB\r\nfim\r\n"
    out = apply(old, update("x", "@@ -4 +4 @@\n-fim\n+FIM\n"))
    assert out == "ação\r\n  espaço \r\n\tTAB\r\nFIM\r\n"
