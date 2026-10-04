"""E7.5-A — regra mediada de `.git`, `decide_post_create` e as primitivas de path.

Só fixtures locais benignas num diretório temporário: arquivos e diretórios comuns, e — onde
o teste precisa de uma "troca de pai/raiz" — uma junction NTFS (ou symlink em POSIX) apontando
para **outro diretório temporário**. Nada de git, processo ou payload.

O fluxo que as primitivas habilitam, e que os testes percorrem à mão como o executor fará:

    prevalidate → inspect → decide_path → [primitiva] → decide_post_* → efeito
"""

from __future__ import annotations

import ast
import os
import stat
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from app import path_runtime
from app.path_runtime import (
    BoundRoot,
    EntryKind,
    IntegrityFailure,
    PathAccessDenied,
    PathFailure,
    PathIntegrityViolation,
    PathOperationFailed,
    bind_root,
    create_exclusive,
    delete_if_identity,
    discard_created,
    inspect,
    list_directory,
    open_existing,
    read_fd,
    verify_root,
    write_fd,
)
from app.safety import (
    DOT_GIT_RULE_ID,
    ObjectIdentity,
    PathFacts,
    Tri,
    decide_mediated_path,
    decide_path,
    decide_post_create,
    decide_post_open,
    path_components,
    prevalidate_mediated_path,
    prevalidate_path_syntax,
)
from app.safety.paths import PathIntent
from tests.conftest import supports_symlinks

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

if sys.platform == "win32":
    import _winapi

    def make_dir_link(link: Path, target: Path) -> bool:
        _winapi.CreateJunction(str(target), str(link))
        return True

else:

    def make_dir_link(link: Path, target: Path) -> bool:
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            return False
        return True


@pytest.fixture
def root(tmp_path: Path) -> BoundRoot:
    directory = tmp_path / "ws"
    directory.mkdir()
    return bind_root(directory)


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    directory = tmp_path / "outside"
    directory.mkdir()
    return directory


def rootdir(root: BoundRoot) -> Path:
    return Path(root.path)


def facts_for(relative: str, root: BoundRoot):  # type: ignore[no-untyped-def]
    return inspect(relative, root.path, tolerate_absent_tail=True)


def authorized(relative: str, root: BoundRoot, intent: PathIntent = PathIntent.WRITE):  # type: ignore[no-untyped-def]
    """O que o executor fará antes de qualquer primitiva: sintaxe, `.git`, inspeção, decisão."""
    syntax = prevalidate_mediated_path(relative)
    assert syntax.allow, syntax
    facts = facts_for(relative, root)
    decision = decide_path(facts, intent=intent)
    assert decision.allow, decision
    return facts


# --------------------------------------------------------------- regra mediada de `.git`


@pytest.mark.parametrize(
    "requested",
    [
        ".git",
        ".GIT",
        ".Git",
        ".git/config",
        ".git\\config",
        "a/.git/HEAD",
        "a\\.git\\HEAD",
        "./.git",
        "src/./.git/x",
        "a//.GiT//b",
    ],
)
def test_qualquer_componente_dot_git_e_negado_em_qualquer_caixa(requested: str) -> None:
    decision = prevalidate_mediated_path(requested)
    assert not decision.allow
    assert decision.rule_id == DOT_GIT_RULE_ID
    assert decide_mediated_path(requested).rule_id == DOT_GIT_RULE_ID


@pytest.mark.parametrize(
    "requested",
    [".git.", ".git ", "GIT~1", "git~1/x", ".git::$INDEX_ALLOCATION", ".git./x", "a/.git /x"],
)
def test_formas_ntfs_de_dot_git_ja_caem_na_pre_validacao_existente(requested: str) -> None:
    """Não dependem da regra nova: a gramática existente as nega (e a regra nova não as
    afrouxa)."""
    assert not prevalidate_path_syntax(requested).allow
    assert not prevalidate_mediated_path(requested).allow


@pytest.mark.parametrize(
    "requested",
    [
        ".gitattributes",
        ".gitmodules",
        ".gitignore",
        "sub/.gitattributes",
        "sub/.gitmodules",
        ".github/workflows/ci.yml",
        "a.git/x",
        "legit.git",
        "git/x",
        "src/gitx",
    ],
)
def test_gitattributes_gitmodules_e_afins_continuam_arquivos_normais(requested: str) -> None:
    assert prevalidate_mediated_path(requested).allow, requested


@pytest.mark.parametrize(
    "requested",
    [
        "",
        " ",
        ".",
        "./",
        "/",
        "\\",
        "..",
        "../x",
        "a/../b",
        "/etc/passwd",
        "C:\\Windows\\x",
        "C:x",
        "\\x",
        "\\\\srv\\share\\f",
        "//srv/share/f",
        "\\\\?\\C:\\x",
        "\\\\.\\COM1",
        "a\x00b",
        "a\x01b",
        "~/x",
        "file.txt:stream",
        "con",
        "NUL.txt",
        "name.",
        "name ",
        "PROGRA~1",
    ],
)
def test_a_gramatica_existente_continua_decidindo_o_resto(requested: str) -> None:
    """A regra mediada **acrescenta**: a decisão de sintaxe é a mesma, `rule_id` incluso."""
    base = prevalidate_path_syntax(requested)
    mediated = prevalidate_mediated_path(requested)
    assert not base.allow
    assert (mediated.allow, mediated.rule_id) == (base.allow, base.rule_id)


@pytest.mark.parametrize("requested", ["", ".", "./", "/", "\\"])
def test_nenhuma_string_vira_atalho_de_raiz(requested: str) -> None:
    assert not prevalidate_mediated_path(requested).allow


def test_a_regra_mediada_usa_a_unica_gramatica_de_separacao() -> None:
    assert path_components("a\\b//./c") == ["a", "b", "c"]
    assert path_components("C:\\x\\y") == ["x", "y"]
    tree = ast.parse((APP_ROOT / "safety" / "mediated_paths.py").read_text(encoding="utf-8"))
    chamadas = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "split" not in chamadas, "segundo parser de path em mediated_paths.py"
    importados = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "re" not in importados


def test_segredo_e_contencao_continuam_sendo_decididos_pelo_kernel(root: BoundRoot) -> None:
    (rootdir(root) / ".env").write_text("X=1", encoding="utf-8")
    assert prevalidate_mediated_path(".env").allow  # sintaxe e `.git`: nada a dizer
    decision = decide_path(facts_for(".env", root))
    assert not decision.allow and decision.rule_id == "path.secret_denied"


def test_a_raiz_e_representavel_nos_fatos_mas_nao_por_string(root: BoundRoot) -> None:
    """`inspect(".")` é uso interno do executor para `path=None`; a string nunca passa."""
    assert not prevalidate_mediated_path(".").allow
    facts = inspect(".", root.path)
    assert facts.exists and decide_path(facts).allow


# ---------------------------------------------------------------------- decide_post_create


def synthetic_facts() -> PathFacts:
    ident = ObjectIdentity(1, 10)
    parent = ObjectIdentity(1, 5)
    return PathFacts(
        requested_path="a/b.txt",
        canonical_root="R",
        canonical_target="R/a/b.txt",
        exists=True,
        parent_identity=parent,
        target_identity=None,
        volume="C:",
        root_volume="C:",
        is_symlink=Tri.FALSE,
        is_junction=Tri.FALSE,
        is_reparse_point=Tri.FALSE,
        is_unc=Tri.FALSE,
        is_device_namespace=Tri.FALSE,
        is_drive_relative=Tri.FALSE,
        contained=Tri.TRUE,
        ancestor_link_outside_root=Tri.FALSE,
        post_open_identity=ident,
        post_create_path_identity=ident,
        post_create_parent_identity=parent,
        post_create_regular=Tri.TRUE,
        post_create_reparse=Tri.FALSE,
    )


def test_decide_post_create_aceita_objeto_e_pai_estaveis() -> None:
    decision = decide_post_create(synthetic_facts())
    assert decision.allow and decision.rule_id == "path.post_create_ok"


@pytest.mark.parametrize(
    ("changes", "rule"),
    [
        ({"inspection_error": "x"}, "path.post_create_inspection_failed"),
        ({"post_open_identity": None}, "path.post_create_unverified"),
        ({"post_create_path_identity": None}, "path.post_create_unverified"),
        ({"post_create_regular": Tri.FALSE}, "path.post_create_not_regular"),
        ({"post_create_regular": Tri.UNKNOWN}, "path.post_create_not_regular"),
        ({"post_create_reparse": Tri.TRUE}, "path.post_create_reparse"),
        ({"post_create_reparse": Tri.UNKNOWN}, "path.post_create_reparse"),
        ({"post_create_path_identity": ObjectIdentity(1, 11)}, "path.toctou_recheck_failed"),
        ({"parent_identity": None}, "path.post_create_no_parent_baseline"),
        ({"post_create_parent_identity": None}, "path.post_create_no_parent_baseline"),
        ({"post_create_parent_identity": ObjectIdentity(1, 6)}, "path.toctou_recheck_failed"),
    ],
)
def test_decide_post_create_fecha_em_qualquer_duvida(changes: dict[str, object], rule: str) -> None:
    decision = decide_post_create(replace(synthetic_facts(), **changes))  # type: ignore[arg-type]
    assert not decision.allow
    assert decision.rule_id == rule


def test_decide_post_open_legado_continua_negando_criacao() -> None:
    """A criação tem decisão própria; a de abertura segue recusando alvo sem baseline."""
    decision = decide_post_open(synthetic_facts())
    assert not decision.allow and decision.rule_id == "path.post_open_no_baseline"


# ---------------------------------------------------------------------------- bind_root


def test_bind_root_registra_caminho_canonico_e_identidade(tmp_path: Path) -> None:
    directory = tmp_path / "ws"
    directory.mkdir()
    bound = bind_root(directory)
    info = os.stat(directory)
    assert bound.identity == ObjectIdentity(info.st_dev, info.st_ino)
    assert Path(bound.path) == directory.resolve()
    assert bind_root(directory, expected_identity=bound.identity) == bound
    verify_root(bound)


def test_bind_root_recusa_caminho_relativo_e_com_ponto_ponto(tmp_path: Path) -> None:
    for text in ("ws", ".", "..\\x", str(tmp_path / ".." / "x")):
        with pytest.raises(PathIntegrityViolation):
            bind_root(text)


def test_bind_root_distingue_ausente_de_arquivo(tmp_path: Path) -> None:
    with pytest.raises(PathOperationFailed) as missing:
        bind_root(tmp_path / "nao-existe")
    assert missing.value.category is PathFailure.NOT_FOUND
    arquivo = tmp_path / "f.txt"
    arquivo.write_text("x")
    with pytest.raises(PathOperationFailed) as notdir:
        bind_root(arquivo)
    assert notdir.value.category is PathFailure.NOT_DIRECTORY


def test_bind_root_recusa_identidade_diferente_da_registrada(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    registered = bind_root(a).identity
    with pytest.raises(PathIntegrityViolation) as caught:
        bind_root(b, expected_identity=registered)
    assert caught.value.category is IntegrityFailure.ROOT_IDENTITY_CHANGED


def test_bind_root_recusa_link_na_cadeia_pedida(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "ws").mkdir()
    link = tmp_path / "link"
    if not make_dir_link(link, real):
        pytest.skip("link indisponível neste ambiente")
    with pytest.raises(PathIntegrityViolation) as caught:
        bind_root(link / "ws")
    assert caught.value.category is IntegrityFailure.REPARSE_DETECTED


def test_verify_root_detecta_raiz_trocada_por_link(tmp_path: Path, outside: Path) -> None:
    directory = tmp_path / "ws"
    directory.mkdir()
    bound = bind_root(directory)
    directory.rename(tmp_path / "ws_old")
    if not make_dir_link(directory, outside):
        pytest.skip("link indisponível neste ambiente")
    with pytest.raises(PathIntegrityViolation) as caught:
        verify_root(bound)
    assert caught.value.category is IntegrityFailure.REPARSE_DETECTED


def test_verify_root_detecta_raiz_recriada_com_outra_identidade(tmp_path: Path) -> None:
    directory = tmp_path / "ws"
    directory.mkdir()
    bound = bind_root(directory)
    other = tmp_path / "other"
    other.mkdir()
    directory.rename(tmp_path / "ws_old")
    other.rename(directory)
    with pytest.raises(PathIntegrityViolation) as caught:
        verify_root(bound)
    assert caught.value.category is IntegrityFailure.ROOT_IDENTITY_CHANGED


def test_verify_root_distingue_raiz_removida(tmp_path: Path) -> None:
    directory = tmp_path / "ws"
    directory.mkdir()
    bound = bind_root(directory)
    directory.rmdir()
    with pytest.raises(PathOperationFailed) as caught:
        verify_root(bound)
    assert caught.value.category is PathFailure.NOT_FOUND


# ----------------------------------------------------------------------- taxonomia técnica


def test_erros_do_so_viram_categorias_sem_texto_cru() -> None:
    cases = {
        FileNotFoundError(2, "x", "C:\\abs\\p"): PathFailure.NOT_FOUND,
        IsADirectoryError(21, "x", "C:\\abs\\p"): PathFailure.IS_DIRECTORY,
        NotADirectoryError(20, "x", "C:\\abs\\p"): PathFailure.NOT_DIRECTORY,
        FileExistsError(17, "x", "C:\\abs\\p"): PathFailure.ALREADY_EXISTS,
        PermissionError(13, "x", "C:\\abs\\p"): PathFailure.IO_ERROR,
        OSError(5, "x", "C:\\abs\\p"): PathFailure.IO_ERROR,
    }
    for error, category in cases.items():
        failed = path_runtime._classify_os_error(error)
        assert failed.category is category
        assert str(failed) == category.value
        assert "abs" not in str(failed)
        assert failed.errno_code == error.errno


def test_categorias_cobrem_o_pedido() -> None:
    assert {c.value for c in PathFailure} >= {
        "not_found",
        "is_directory",
        "not_regular",
        "already_exists",
        "io_error",
        "cancelled",
    }


def test_negacao_de_politica_continua_sendo_path_access_denied(root: BoundRoot) -> None:
    assert issubclass(PathAccessDenied, PermissionError)
    assert not issubclass(PathOperationFailed, PermissionError | OSError)
    assert not issubclass(PathIntegrityViolation, PermissionError | OSError | PathAccessDenied)


def test_o_codigo_novo_nunca_trunca() -> None:
    """Sem `O_TRUNC` no **código** (a docstring pode citá-lo): nem `os.O_TRUNC`, nem
    `getattr(os, "O_TRUNC")`."""
    tree = ast.parse((APP_ROOT / "path_runtime.py").read_text(encoding="utf-8"))
    usos: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "O_TRUNC":
            usos.append("attr")
        if isinstance(node, ast.Name) and node.id == "O_TRUNC":
            usos.append("name")
        if isinstance(node, ast.Call):
            usos.extend(
                "call-arg"
                for arg in node.args
                if isinstance(arg, ast.Constant) and arg.value == "O_TRUNC"
            )
    assert usos == []


# ------------------------------------------------------------------------ open_existing


def test_open_existing_le_sem_alterar_e_entrega_fatos_pos_abertura(root: BoundRoot) -> None:
    target = rootdir(root) / "a.txt"
    target.write_bytes(b"conteudo original")
    facts = authorized("a.txt", root, PathIntent.READ)
    with open_existing(facts, root) as opened:
        assert opened.size == len(b"conteudo original")
        assert opened.facts.post_open_identity is not None
        assert decide_post_open(opened.facts).allow
        assert read_fd(opened.fd, 1024) == b"conteudo original"
    assert target.read_bytes() == b"conteudo original"


def test_read_fd_le_no_maximo_um_byte_alem_do_teto(root: BoundRoot) -> None:
    (rootdir(root) / "big.bin").write_bytes(b"x" * 100)
    with open_existing(authorized("big.bin", root, PathIntent.READ), root) as opened:
        assert len(read_fd(opened.fd, 10)) == 11
        assert len(read_fd(opened.fd, 1000)) == 100


def test_open_existing_para_update_nao_trunca_ao_abrir(root: BoundRoot) -> None:
    target = rootdir(root) / "a.txt"
    target.write_bytes(b"nao pode sumir")
    facts = authorized("a.txt", root)
    with open_existing(facts, root, for_update=True) as opened:
        assert opened.writable
        # Aberto para escrita, e **nada** foi truncado: a autorização externa ainda não houve.
        assert target.read_bytes() == b"nao pode sumir"
        assert os.fstat(opened.fd).st_size == len(b"nao pode sumir")
    assert target.read_bytes() == b"nao pode sumir"


def test_update_so_trunca_na_escrita_autorizada(root: BoundRoot) -> None:
    target = rootdir(root) / "a.txt"
    target.write_bytes(b"conteudo longo que sera substituido")
    with open_existing(authorized("a.txt", root), root, for_update=True) as opened:
        assert decide_post_open(opened.facts).allow
        write_fd(opened.fd, b"novo", truncate=True)
    assert target.read_bytes() == b"novo"


def test_cancelamento_antes_de_truncar_preserva_o_arquivo(root: BoundRoot) -> None:
    target = rootdir(root) / "a.txt"
    target.write_bytes(b"intacto")
    with open_existing(authorized("a.txt", root), root, for_update=True) as opened:
        with pytest.raises(PathOperationFailed) as caught:
            write_fd(opened.fd, b"x", truncate=True, is_cancelled=lambda: True)
        assert caught.value.category is PathFailure.CANCELLED
    assert target.read_bytes() == b"intacto"


def test_cancelamento_com_resposta_nao_bool_conta_como_cancelar(root: BoundRoot) -> None:
    (rootdir(root) / "a.txt").write_bytes(b"x")
    facts = authorized("a.txt", root, PathIntent.READ)
    with (
        pytest.raises(PathOperationFailed) as caught,
        open_existing(facts, root, is_cancelled=lambda: "nao"),  # type: ignore[arg-type,return-value]
    ):
        pass
    assert caught.value.category is PathFailure.CANCELLED


def test_open_existing_distingue_diretorio_ausente_e_nao_regular(root: BoundRoot) -> None:
    (rootdir(root) / "dir").mkdir()
    (rootdir(root) / "a.txt").write_bytes(b"x")
    dir_facts = authorized("dir", root, PathIntent.READ)
    with (
        pytest.raises(PathOperationFailed) as is_dir,
        open_existing(dir_facts, root),
    ):
        pass
    assert is_dir.value.category is PathFailure.IS_DIRECTORY

    missing = inspect("nao-existe.txt", root.path)
    with (
        pytest.raises(PathOperationFailed) as not_found,
        open_existing(missing, root),
    ):
        pass
    assert not_found.value.category is PathFailure.NOT_FOUND

    # arquivo que sumiu depois da inspeção
    facts = authorized("a.txt", root, PathIntent.READ)
    (rootdir(root) / "a.txt").unlink()
    with (
        pytest.raises(PathOperationFailed) as vanished,
        open_existing(facts, root),
    ):
        pass
    assert vanished.value.category is PathFailure.NOT_FOUND


def test_open_existing_entrega_fatos_que_negam_objeto_trocado(root: BoundRoot) -> None:
    """Troca o arquivo entre a inspeção e a abertura: a identidade difere e a decisão nega."""
    target = rootdir(root) / "a.txt"
    target.write_bytes(b"original")
    facts = authorized("a.txt", root)
    other = rootdir(root) / "outro.txt"
    other.write_bytes(b"trocado")
    os.replace(other, target)
    with open_existing(facts, root) as opened:
        decision = decide_post_open(opened.facts)
    assert not decision.allow and decision.rule_id == "path.toctou_recheck_failed"
    assert target.read_bytes() == b"trocado"  # nada foi escrito


def test_open_existing_recusa_alvo_trocado_por_link_depois_da_inspecao(
    root: BoundRoot, outside: Path
) -> None:
    target = rootdir(root) / "a.txt"
    target.write_bytes(b"x")
    facts = authorized("a.txt", root, PathIntent.READ)
    target.unlink()
    if not make_dir_link(target, outside):
        pytest.skip("link indisponível neste ambiente")
    with (
        pytest.raises(PathIntegrityViolation) as caught,
        open_existing(facts, root),
    ):
        pass
    assert caught.value.category is IntegrityFailure.REPARSE_DETECTED


def test_open_existing_recusa_fatos_de_outra_raiz(tmp_path: Path, root: BoundRoot) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.txt").write_bytes(b"x")
    foreign = inspect("a.txt", str(other))
    with (
        pytest.raises(PathIntegrityViolation) as caught,
        open_existing(foreign, root),
    ):
        pass
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE


def test_open_existing_revalida_a_raiz_antes_de_abrir(tmp_path: Path, outside: Path) -> None:
    directory = tmp_path / "ws"
    directory.mkdir()
    (directory / "a.txt").write_bytes(b"x")
    bound = bind_root(directory)
    facts = inspect("a.txt", bound.path)
    directory.rename(tmp_path / "ws_old")
    if not make_dir_link(directory, outside):
        pytest.skip("link indisponível neste ambiente")
    with (
        pytest.raises(PathIntegrityViolation) as caught,
        open_existing(facts, bound),
    ):
        pass
    assert caught.value.category is IntegrityFailure.REPARSE_DETECTED


@pytest.mark.skipif(sys.platform == "win32", reason="FIFO só existe em POSIX")
def test_open_existing_recusa_fifo_sem_bloquear(root: BoundRoot) -> None:
    fifo = rootdir(root) / "pipe"
    os.mkfifo(fifo)  # type: ignore[attr-defined,unused-ignore]
    facts = inspect("pipe", root.path)
    with (
        pytest.raises(PathOperationFailed) as caught,
        open_existing(facts, root),
    ):
        pass
    assert caught.value.category is PathFailure.NOT_REGULAR


# --------------------------------------------------------------------- create_exclusive


def test_create_exclusive_cria_vazio_e_so_escreve_depois_da_decisao(root: BoundRoot) -> None:
    target = rootdir(root) / "novo.txt"
    facts = authorized("novo.txt", root)
    assert not facts.exists
    with create_exclusive(facts, root) as created:
        assert target.exists() and target.stat().st_size == 0  # nenhum byte ainda
        assert decide_post_create(created.facts).allow
        write_fd(created.fd, b"conteudo", truncate=False)
    assert target.read_bytes() == b"conteudo"


def test_create_exclusive_cria_pais_ausentes_nivel_a_nivel(root: BoundRoot) -> None:
    facts = authorized("a/b/c/novo.txt", root)
    assert not facts.exists and facts.parent_identity is None
    with create_exclusive(facts, root) as created:
        assert decide_post_create(created.facts).allow
        write_fd(created.fd, b"x", truncate=False)
    assert (rootdir(root) / "a" / "b" / "c" / "novo.txt").read_bytes() == b"x"


def test_create_exclusive_em_pai_existente_confere_a_identidade_inspecionada(
    root: BoundRoot,
) -> None:
    (rootdir(root) / "sub").mkdir()
    facts = authorized("sub/novo.txt", root)
    assert facts.parent_identity is not None
    with create_exclusive(facts, root) as created:
        assert decide_post_create(created.facts).allow
        assert created.facts.parent_identity == created.facts.post_create_parent_identity


def test_create_exclusive_sem_criar_pais_recusa_pai_ausente(root: BoundRoot) -> None:
    facts = authorized("a/novo.txt", root)
    with pytest.raises(PathOperationFailed) as caught:
        create_exclusive(facts, root, create_parents=False)
    assert caught.value.category is PathFailure.NOT_FOUND
    assert not (rootdir(root) / "a").exists()


def test_arquivo_que_aparece_durante_a_criacao_nunca_e_sobrescrito(root: BoundRoot) -> None:
    facts = authorized("corrida.txt", root)
    assert not facts.exists
    target = rootdir(root) / "corrida.txt"
    target.write_bytes(b"outro processo chegou primeiro")
    with pytest.raises(PathOperationFailed) as caught:
        create_exclusive(facts, root)
    assert caught.value.category is PathFailure.ALREADY_EXISTS
    assert target.read_bytes() == b"outro processo chegou primeiro"


def test_create_exclusive_em_alvo_existente_segundo_os_fatos(root: BoundRoot) -> None:
    (rootdir(root) / "ja.txt").write_bytes(b"x")
    facts = authorized("ja.txt", root)
    with pytest.raises(PathOperationFailed) as caught:
        create_exclusive(facts, root)
    assert caught.value.category is PathFailure.ALREADY_EXISTS


def test_pai_trocado_por_link_entre_inspecao_e_criacao_e_recusado(
    root: BoundRoot, outside: Path
) -> None:
    (rootdir(root) / "sub").mkdir()
    facts = authorized("sub/novo.txt", root)
    (rootdir(root) / "sub").rmdir()
    if not make_dir_link(rootdir(root) / "sub", outside):
        pytest.skip("link indisponível neste ambiente")
    with pytest.raises(PathIntegrityViolation) as caught:
        create_exclusive(facts, root)
    assert caught.value.category is IntegrityFailure.REPARSE_DETECTED
    assert list(outside.iterdir()) == []  # nada nasceu fora da raiz


def test_pai_trocado_por_outro_diretorio_e_recusado(root: BoundRoot) -> None:
    (rootdir(root) / "sub").mkdir()
    facts = authorized("sub/novo.txt", root)
    (rootdir(root) / "outro").mkdir()
    (rootdir(root) / "sub").rename(rootdir(root) / "sub_antigo")
    (rootdir(root) / "outro").rename(rootdir(root) / "sub")
    with pytest.raises(PathIntegrityViolation) as caught:
        create_exclusive(facts, root)
    assert caught.value.category is IntegrityFailure.PARENT_IDENTITY_CHANGED
    assert not (rootdir(root) / "sub" / "novo.txt").exists()


def test_pai_que_virou_arquivo_e_erro_tecnico(root: BoundRoot) -> None:
    facts = authorized("sub/novo.txt", root)
    (rootdir(root) / "sub").write_bytes(b"sou arquivo")
    with pytest.raises(PathOperationFailed) as caught:
        create_exclusive(facts, root)
    assert caught.value.category is PathFailure.NOT_DIRECTORY


def test_create_exclusive_revalida_a_raiz(tmp_path: Path, outside: Path) -> None:
    directory = tmp_path / "ws"
    directory.mkdir()
    bound = bind_root(directory)
    facts = inspect("novo.txt", bound.path)
    directory.rename(tmp_path / "ws_old")
    if not make_dir_link(directory, outside):
        pytest.skip("link indisponível neste ambiente")
    with pytest.raises(PathIntegrityViolation) as caught:
        create_exclusive(facts, bound)
    assert caught.value.category is IntegrityFailure.REPARSE_DETECTED
    assert list(outside.iterdir()) == []


def test_create_exclusive_respeita_cancelamento(root: BoundRoot) -> None:
    facts = authorized("novo.txt", root)
    with pytest.raises(PathOperationFailed) as caught:
        create_exclusive(facts, root, is_cancelled=lambda: True)
    assert caught.value.category is PathFailure.CANCELLED
    assert not (rootdir(root) / "novo.txt").exists()


# ----------------------------------------------------------------------- discard_created


def test_decisao_pos_criacao_negando_remove_o_vazio_sem_nunca_ter_escrito(
    root: BoundRoot,
) -> None:
    target = rootdir(root) / "negado.txt"
    facts = authorized("negado.txt", root)
    created = create_exclusive(facts, root)
    # simula a negação de safety: fatos que a decisão recusa
    denied = decide_post_create(replace(created.facts, post_create_reparse=Tri.TRUE))
    assert not denied.allow
    assert target.stat().st_size == 0
    assert discard_created(created) is True
    assert not target.exists()


def test_cleanup_do_vazio_so_acontece_com_a_identidade_criada(root: BoundRoot) -> None:
    target = rootdir(root) / "trocado.txt"
    created = create_exclusive(authorized("trocado.txt", root), root)
    created.close()
    outro = rootdir(root) / "outro.txt"
    outro.write_bytes(b"")
    os.replace(outro, target)  # outro arquivo vazio, **outra** identidade
    assert discard_created(created) is False
    assert target.exists()


def test_cleanup_nao_remove_arquivo_que_ja_tem_conteudo(root: BoundRoot) -> None:
    target = rootdir(root) / "cheio.txt"
    created = create_exclusive(authorized("cheio.txt", root), root)
    write_fd(created.fd, b"conteudo", truncate=False)
    assert discard_created(created) is False
    assert target.read_bytes() == b"conteudo"


def test_cleanup_de_arquivo_que_sumiu_devolve_false(root: BoundRoot) -> None:
    target = rootdir(root) / "sumiu.txt"
    created = create_exclusive(authorized("sumiu.txt", root), root)
    created.close()
    target.unlink()
    assert discard_created(created) is False


def test_discard_e_idempotente_e_o_handle_e_fechado(root: BoundRoot) -> None:
    created = create_exclusive(authorized("x.txt", root), root)
    assert discard_created(created) is True
    assert discard_created(created) is False
    with pytest.raises(PathOperationFailed):
        _ = created.fd


def test_discard_nunca_remove_diretorio_criado(root: BoundRoot) -> None:
    created = create_exclusive(authorized("novo_dir/x.txt", root), root)
    assert discard_created(created) is True
    assert (rootdir(root) / "novo_dir").is_dir()  # os diretórios criados ficam (declarado)


# --------------------------------------------------------------------- delete_if_identity


def test_delete_remove_arquivo_regular_com_a_identidade_esperada(root: BoundRoot) -> None:
    target = rootdir(root) / "alvo.txt"
    target.write_bytes(b"x")
    delete_if_identity(authorized("alvo.txt", root), root)
    assert not target.exists()


def test_delete_recusa_objeto_trocado_e_o_preserva(root: BoundRoot) -> None:
    target = rootdir(root) / "alvo.txt"
    target.write_bytes(b"original")
    facts = authorized("alvo.txt", root)
    outro = rootdir(root) / "outro.txt"
    outro.write_bytes(b"trocado")
    os.replace(outro, target)
    with pytest.raises(PathIntegrityViolation) as caught:
        delete_if_identity(facts, root)
    assert caught.value.category is IntegrityFailure.TARGET_IDENTITY_CHANGED
    assert target.read_bytes() == b"trocado"


def test_delete_com_identidade_esperada_explicita(root: BoundRoot) -> None:
    target = rootdir(root) / "alvo.txt"
    target.write_bytes(b"x")
    facts = authorized("alvo.txt", root)
    info = target.stat()
    delete_if_identity(facts, root, expected_identity=ObjectIdentity(info.st_dev, info.st_ino))
    assert not target.exists()
    other = rootdir(root) / "b.txt"
    other.write_bytes(b"x")
    with pytest.raises(PathIntegrityViolation):
        delete_if_identity(authorized("b.txt", root), root, expected_identity=ObjectIdentity(0, 0))
    assert other.exists()


def test_delete_nunca_remove_diretorio(root: BoundRoot) -> None:
    directory = rootdir(root) / "dir"
    directory.mkdir()
    (directory / "f.txt").write_bytes(b"x")
    with pytest.raises(PathOperationFailed) as caught:
        delete_if_identity(authorized("dir", root), root)
    assert caught.value.category is PathFailure.IS_DIRECTORY
    assert (directory / "f.txt").exists()


def test_delete_distingue_alvo_ausente(root: BoundRoot) -> None:
    for facts in (inspect("nada.txt", root.path),):
        with pytest.raises(PathOperationFailed) as caught:
            delete_if_identity(facts, root)
        assert caught.value.category is PathFailure.NOT_FOUND
    target = rootdir(root) / "sumiu.txt"
    target.write_bytes(b"x")
    facts = authorized("sumiu.txt", root)
    target.unlink()
    with pytest.raises(PathOperationFailed) as gone:
        delete_if_identity(facts, root)
    assert gone.value.category is PathFailure.NOT_FOUND


def test_delete_recusa_pai_trocado_por_link(root: BoundRoot, outside: Path) -> None:
    (rootdir(root) / "sub").mkdir()
    (rootdir(root) / "sub" / "f.txt").write_bytes(b"x")
    facts = authorized("sub/f.txt", root)
    (outside / "f.txt").write_bytes(b"de fora")
    (rootdir(root) / "sub" / "f.txt").unlink()
    (rootdir(root) / "sub").rmdir()
    if not make_dir_link(rootdir(root) / "sub", outside):
        pytest.skip("link indisponível neste ambiente")
    with pytest.raises(PathIntegrityViolation) as caught:
        delete_if_identity(facts, root)
    assert caught.value.category is IntegrityFailure.REPARSE_DETECTED
    assert (outside / "f.txt").exists()


def test_delete_recusa_alvo_trocado_por_link_depois_da_inspecao(
    root: BoundRoot, outside: Path
) -> None:
    target = rootdir(root) / "a.txt"
    target.write_bytes(b"x")
    facts = authorized("a.txt", root)
    target.unlink()
    if not make_dir_link(target, outside):
        pytest.skip("link indisponível neste ambiente")
    with pytest.raises(PathIntegrityViolation) as caught:
        delete_if_identity(facts, root)
    assert caught.value.category is IntegrityFailure.REPARSE_DETECTED
    assert outside.exists()


# --------------------------------------------------------------------- list_directory


def test_list_directory_ordena_por_bytes_e_classifica(root: BoundRoot) -> None:
    base = rootdir(root)
    (base / "b.txt").write_bytes(b"12345")
    (base / "a_dir").mkdir()
    (base / "A.txt").write_bytes(b"")
    listing = list_directory(inspect(".", root.path), root)
    assert [e.name for e in listing.entries] == ["A.txt", "a_dir", "b.txt"]
    kinds = {e.name: e.kind for e in listing.entries}
    assert kinds == {"A.txt": EntryKind.FILE, "a_dir": EntryKind.DIRECTORY, "b.txt": EntryKind.FILE}
    sizes = {e.name: e.size for e in listing.entries}
    assert sizes == {"A.txt": 0, "a_dir": None, "b.txt": 5}
    assert all(e.identity is not None for e in listing.entries)
    assert all(e.is_reparse_point is Tri.FALSE for e in listing.entries)


def test_list_directory_de_subdiretorio_autorizado(root: BoundRoot) -> None:
    (rootdir(root) / "sub").mkdir()
    (rootdir(root) / "sub" / "x.py").write_bytes(b"x")
    listing = list_directory(authorized("sub", root, PathIntent.READ), root)
    assert [e.name for e in listing.entries] == ["x.py"]


def test_list_directory_devolve_a_enumeracao_inteira_o_limite_e_do_executor(
    root: BoundRoot,
) -> None:
    for index in range(5):
        (rootdir(root) / f"f{index}.txt").write_bytes(b"")
    listing = list_directory(inspect(".", root.path), root)
    assert [e.name for e in listing.entries] == [f"f{i}.txt" for i in range(5)]


def test_list_directory_nao_segue_link_e_o_expoe_como_fato(root: BoundRoot, outside: Path) -> None:
    (outside / "segredo.txt").write_bytes(b"x")
    if not make_dir_link(rootdir(root) / "dlink", outside):
        pytest.skip("link indisponível neste ambiente")
    listing = list_directory(inspect(".", root.path), root)
    entry = next(e for e in listing.entries if e.name == "dlink")
    assert entry.is_reparse_point.is_true or entry.is_symlink.is_true
    assert not [e for e in listing.entries if e.name == "segredo.txt"]


def test_list_directory_distingue_arquivo_e_ausente(root: BoundRoot) -> None:
    (rootdir(root) / "f.txt").write_bytes(b"x")
    with pytest.raises(PathOperationFailed) as notdir:
        list_directory(authorized("f.txt", root, PathIntent.READ), root)
    assert notdir.value.category is PathFailure.NOT_DIRECTORY
    with pytest.raises(PathOperationFailed) as missing:
        list_directory(inspect("nada", root.path), root)
    assert missing.value.category is PathFailure.NOT_FOUND


def test_list_directory_recusa_diretorio_trocado(root: BoundRoot) -> None:
    (rootdir(root) / "sub").mkdir()
    facts = authorized("sub", root, PathIntent.READ)
    (rootdir(root) / "outro").mkdir()
    (rootdir(root) / "sub").rename(rootdir(root) / "sub_antigo")
    (rootdir(root) / "outro").rename(rootdir(root) / "sub")
    with pytest.raises(PathIntegrityViolation) as caught:
        list_directory(facts, root)
    assert caught.value.category is IntegrityFailure.TARGET_IDENTITY_CHANGED


def test_list_directory_recusa_diretorio_que_virou_link(root: BoundRoot, outside: Path) -> None:
    (rootdir(root) / "sub").mkdir()
    facts = authorized("sub", root, PathIntent.READ)
    (rootdir(root) / "sub").rmdir()
    if not make_dir_link(rootdir(root) / "sub", outside):
        pytest.skip("link indisponível neste ambiente")
    with pytest.raises(PathIntegrityViolation) as caught:
        list_directory(facts, root)
    assert caught.value.category is IntegrityFailure.REPARSE_DETECTED


def test_list_directory_respeita_cancelamento(root: BoundRoot) -> None:
    (rootdir(root) / "a.txt").write_bytes(b"")
    with pytest.raises(PathOperationFailed) as caught:
        list_directory(inspect(".", root.path), root, is_cancelled=lambda: True)
    assert caught.value.category is PathFailure.CANCELLED


# ---------------------------------------------------------------- nenhum texto cru nas falhas


def test_falhas_tecnicas_nunca_carregam_caminho_absoluto(root: BoundRoot) -> None:
    facts = inspect("nada.txt", root.path)
    with (
        pytest.raises(PathOperationFailed) as caught,
        open_existing(facts, root),
    ):
        pass
    text = str(caught.value) + repr(caught.value)
    assert root.path not in text and str(rootdir(root).parent) not in text


def test_violacoes_de_integridade_so_carregam_a_categoria(tmp_path: Path, outside: Path) -> None:
    directory = tmp_path / "ws"
    directory.mkdir()
    bound = bind_root(directory)
    directory.rename(tmp_path / "ws_old")
    if not make_dir_link(directory, outside):
        pytest.skip("link indisponível neste ambiente")
    with pytest.raises(PathIntegrityViolation) as caught:
        verify_root(bound)
    assert str(caught.value) == IntegrityFailure.REPARSE_DETECTED.value
    assert str(tmp_path) not in str(caught.value) + repr(caught.value)
    assert not hasattr(caught.value, "decision")  # um fato, não uma SafetyDecision


def test_symlink_helper_consistente(tmp_path: Path) -> None:
    """Só documenta a dependência: sem link no ambiente, os testes de link são pulados."""
    assert isinstance(supports_symlinks(tmp_path), bool)
    assert stat.S_ISDIR(os.stat(tmp_path).st_mode)
