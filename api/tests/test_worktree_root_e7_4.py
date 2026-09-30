"""E7.4 (D7) — raiz de worktrees: fatos em `path_runtime`, decisão pura em `safety`.

Gate da E7 em [07]: "worktree criada fora do OneDrive". Aqui se prova a metade da raiz: uma
`WorktreeRoot` só existe se o caminho canônico estiver fora de toda raiz de sincronização
conhecida, fora da árvore principal e do `.git` comum, sem link no caminho pedido e sem
reparse point no canônico. A outra metade — o `git_runtime` recusar quando a identidade
registrada muda — está em `test_git_worktree_e7_4.py`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from app.config import AppSettings, detected_sync_roots
from app.path_runtime import (
    PathAccessDenied,
    _reparse_in_chain,
    inspect_worktree_root,
    prepare_worktree_root,
)
from app.safety import decide_worktree_root
from app.safety.types import ObjectIdentity, Tri, WorktreeRootFacts

_IS_WINDOWS = sys.platform == "win32"
_GIT = shutil.which("git")
_NEEDS_GIT = pytest.mark.skipif(_GIT is None, reason="git indisponível no PATH")


if sys.platform == "win32":
    import _winapi

    def _junction(link: Path, target: Path) -> None:
        """Junction NTFS (sem privilégio de symlink)."""
        _winapi.CreateJunction(str(target), str(link))

else:

    def _junction(link: Path, target: Path) -> None:
        raise RuntimeError("junction é NTFS")


@pytest.fixture
def layout(tmp_path: Path) -> tuple[Path, Path]:
    """(toplevel, `.git`) de um repositório falso: só diretórios, a raiz não roda git."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo, repo / ".git"


def _prepare(root: Path, layout: tuple[Path, Path], *sync: Path) -> object:
    return prepare_worktree_root(
        root,
        sync_roots=tuple(sync),
        repository_toplevel=str(layout[0]),
        git_common_dir=str(layout[1]),
    )


def _rule(root: Path, layout: tuple[Path, Path], *sync: Path) -> str:
    with pytest.raises(PathAccessDenied) as caught:
        _prepare(root, layout, *sync)
    return caught.value.decision.rule_id


# ------------------------------------------------------------------ decisão pura


def _ok_facts() -> WorktreeRootFacts:
    ident = ObjectIdentity(volume_id=1, file_id=2)
    return WorktreeRootFacts(
        requested_path="C:/x/worktrees",
        requested_is_absolute=True,
        canonical_path="C:/x/worktrees",
        exists=True,
        is_directory=Tri.TRUE,
        reparse_in_canonical_chain=Tri.FALSE,
        reparse_in_requested_chain=Tri.FALSE,
        identity=ident,
        overlaps_sync_root=Tri.FALSE,
        overlaps_repository_toplevel=Tri.FALSE,
        overlaps_git_common_dir=Tri.FALSE,
        repository_toplevel="C:/repo",
        repository_toplevel_identity=ObjectIdentity(1, 3),
        git_common_dir="C:/repo/.git",
        git_common_dir_identity=ObjectIdentity(1, 4),
    )


def test_decisao_permite_so_com_todos_os_fatos_favoraveis() -> None:
    assert decide_worktree_root(_ok_facts()).allow


@pytest.mark.parametrize(
    ("campo", "valor", "regra"),
    [
        ("inspection_error", "boom", "worktree_root.inspection_failed"),
        ("requested_is_absolute", False, "worktree_root.not_absolute"),
        ("exists", False, "worktree_root.missing"),
        ("canonical_path", None, "worktree_root.missing"),
        ("is_directory", Tri.FALSE, "worktree_root.not_directory"),
        ("is_directory", Tri.UNKNOWN, "worktree_root.not_directory"),
        ("identity", None, "worktree_root.no_identity"),
        ("reparse_in_requested_chain", Tri.TRUE, "worktree_root.link"),
        ("reparse_in_requested_chain", Tri.UNKNOWN, "worktree_root.link_unverified"),
        ("reparse_in_canonical_chain", Tri.TRUE, "worktree_root.reparse_point"),
        ("reparse_in_canonical_chain", Tri.UNKNOWN, "worktree_root.reparse_point_unverified"),
        ("overlaps_sync_root", Tri.TRUE, "worktree_root.sync_root"),
        ("overlaps_sync_root", Tri.UNKNOWN, "worktree_root.sync_root_unverified"),
        ("overlaps_repository_toplevel", Tri.TRUE, "worktree_root.repository"),
        ("overlaps_repository_toplevel", Tri.UNKNOWN, "worktree_root.repository_unverified"),
        ("overlaps_git_common_dir", Tri.TRUE, "worktree_root.git_dir"),
        ("overlaps_git_common_dir", Tri.UNKNOWN, "worktree_root.git_dir_unverified"),
        ("repository_toplevel_identity", None, "worktree_root.repository_unbound"),
        ("git_common_dir_identity", None, "worktree_root.repository_unbound"),
    ],
)
def test_decisao_fecha_em_cada_fato_desfavoravel_ou_desconhecido(
    campo: str, valor: object, regra: str
) -> None:
    decision = decide_worktree_root(replace(_ok_facts(), **{campo: valor}))  # type: ignore[arg-type]
    assert not decision.allow
    assert decision.rule_id == regra


# ------------------------------------------------------------------ fatos reais


def test_raiz_valida_devolve_identidades_canonicas(
    tmp_path: Path, layout: tuple[Path, Path]
) -> None:
    root_dir = tmp_path / "wt root ç 名"
    root_dir.mkdir()
    root = prepare_worktree_root(
        root_dir, sync_roots=(), repository_toplevel=str(layout[0]), git_common_dir=str(layout[1])
    )
    assert Path(root.canonical_path) == root_dir.resolve(strict=True)
    info = os.stat(root.canonical_path)
    assert (root.identity.volume_id, root.identity.file_id) == (info.st_dev, info.st_ino)
    assert Path(root.repository_toplevel) == layout[0].resolve()
    assert Path(root.git_common_dir) == layout[1].resolve()


def test_raiz_relativa_e_recusada(layout: tuple[Path, Path]) -> None:
    assert _rule(Path("worktrees"), layout) == "worktree_root.not_absolute"


def test_raiz_com_ponto_ponto_e_recusada(tmp_path: Path, layout: tuple[Path, Path]) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert _rule(tmp_path / "a" / ".." / "b", layout) == "worktree_root.inspection_failed"


def test_raiz_inexistente_e_recusada(tmp_path: Path, layout: tuple[Path, Path]) -> None:
    assert _rule(tmp_path / "nao-existe", layout) == "worktree_root.missing"


def test_raiz_que_e_arquivo_e_recusada(tmp_path: Path, layout: tuple[Path, Path]) -> None:
    arquivo = tmp_path / "arquivo"
    arquivo.write_text("x", encoding="utf-8")
    assert _rule(arquivo, layout) == "worktree_root.not_directory"


def test_raiz_dentro_do_onedrive_e_recusada(tmp_path: Path, layout: tuple[Path, Path]) -> None:
    onedrive = tmp_path / "OneDrive - Pessoal"
    root_dir = onedrive / "FreelanceFocus" / "worktrees"
    root_dir.mkdir(parents=True)
    assert _rule(root_dir, layout, onedrive) == "worktree_root.sync_root"


def test_raiz_que_contem_o_onedrive_tambem_e_recusada(
    tmp_path: Path, layout: tuple[Path, Path]
) -> None:
    root_dir = tmp_path / "amplo"
    (root_dir / "OneDrive").mkdir(parents=True)
    assert _rule(root_dir, layout, root_dir / "OneDrive") == "worktree_root.sync_root"


def test_irmao_com_prefixo_textual_igual_nao_e_dentro(
    tmp_path: Path, layout: tuple[Path, Path]
) -> None:
    """`sync-irma` começa com `sync`, mas não está dentro dele: `commonpath`, não substring.
    A raiz de sincronização nem existe ainda — é canonizada com `strict=False`."""
    root_dir = tmp_path / "sync-irma"
    root_dir.mkdir()
    _prepare(root_dir, layout, tmp_path / "sync")


def test_nome_parecido_com_onedrive_nao_e_recusado_por_substring(
    tmp_path: Path, layout: tuple[Path, Path]
) -> None:
    root_dir = tmp_path / "OneDriveBackupNaoSincronizado" / "worktrees"
    root_dir.mkdir(parents=True)
    _prepare(root_dir, layout, tmp_path / "OneDrive")


@pytest.mark.skipif(not _IS_WINDOWS, reason="caixa de caminho só é insensível no Windows")
def test_sync_root_com_caixa_diferente_ainda_casa(
    tmp_path: Path, layout: tuple[Path, Path]
) -> None:
    onedrive = tmp_path / "OneDrive"
    root_dir = onedrive / "wt"
    root_dir.mkdir(parents=True)
    assert _rule(root_dir, layout, Path(str(onedrive).upper())) == "worktree_root.sync_root"


def test_raiz_dentro_da_arvore_principal_e_recusada(layout: tuple[Path, Path]) -> None:
    root_dir = layout[0] / "worktrees"
    root_dir.mkdir()
    assert _rule(root_dir, layout) == "worktree_root.repository"


def test_raiz_dentro_do_git_comum_e_recusada(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    common = tmp_path / "separado.git"  # `.git` fora da árvore (gitdir separado)
    repo.mkdir()
    (common / "worktrees").mkdir(parents=True)
    rule = _rule(common / "worktrees", (repo, common))
    assert rule == "worktree_root.git_dir"


def test_repositorio_inexistente_fecha(tmp_path: Path) -> None:
    root_dir = tmp_path / "wt"
    root_dir.mkdir()
    rule = _rule(root_dir, (tmp_path / "sumiu", tmp_path / "sumiu" / ".git"))
    assert rule == "worktree_root.repository_unverified"


@pytest.mark.skipif(not _IS_WINDOWS, reason="junction é NTFS")
def test_junction_no_caminho_pedido_e_recusada(tmp_path: Path, layout: tuple[Path, Path]) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    _junction(link, real)
    assert _rule(link, layout) == "worktree_root.link"
    (real / "wt").mkdir()
    assert _rule(link / "wt", layout) == "worktree_root.link"


@pytest.mark.skipif(not _IS_WINDOWS, reason="junction é NTFS")
def test_junction_para_dentro_do_onedrive_e_recusada(
    tmp_path: Path, layout: tuple[Path, Path]
) -> None:
    onedrive = tmp_path / "OneDrive"
    (onedrive / "wt").mkdir(parents=True)
    link = tmp_path / "aparenta-fora"
    _junction(link, onedrive / "wt")
    facts = inspect_worktree_root(
        link,
        sync_roots=(onedrive,),
        repository_toplevel=str(layout[0]),
        git_common_dir=str(layout[1]),
    )
    # O destino canônico é o que conta: mesmo sem o link, estaria no OneDrive.
    assert facts.overlaps_sync_root is Tri.TRUE
    assert facts.reparse_in_requested_chain is Tri.TRUE
    assert not decide_worktree_root(facts).allow


@pytest.mark.skipif(not _IS_WINDOWS, reason="junction é NTFS")
def test_cadeia_com_reparse_e_detectada(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    link = tmp_path / "j"
    _junction(link, real)
    assert _reparse_in_chain(link / "sub") is Tri.TRUE
    assert _reparse_in_chain(real / "sub") is Tri.FALSE


def test_symlink_no_caminho_pedido_e_recusado(tmp_path: Path, layout: tuple[Path, Path]) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlink indisponível sem privilégio")
    assert _rule(link, layout) == "worktree_root.link"


# ------------------------------------------------------------------ config


def test_worktrees_dir_padrao_e_override(tmp_path: Path) -> None:
    padrao = AppSettings(data_dir=tmp_path / "data")
    assert padrao.resolved_worktrees_dir == tmp_path / "data" / "worktrees"
    override = AppSettings(data_dir=tmp_path / "data", worktrees_dir=tmp_path / "outro")
    assert override.resolved_worktrees_dir == tmp_path / "outro"
    criado = override.ensure_worktrees_dir()
    assert criado.is_dir()


def test_sync_roots_detectadas_nao_podem_ser_desligadas(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OneDrive", str(tmp_path / "od"))
    monkeypatch.setenv("OneDriveCommercial", str(tmp_path / "odc"))
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    monkeypatch.setenv("FF_EXTRA_SYNC_ROOTS", "[]")
    monkeypatch.setenv("FF_SYNC_ROOTS", "[]")
    settings = AppSettings(extra_sync_roots=(tmp_path / "dropbox",))
    roots = set(settings.sync_roots)
    assert {tmp_path / "od", tmp_path / "odc", tmp_path / "dropbox"} <= roots
    assert set(detected_sync_roots()) == {tmp_path / "od", tmp_path / "odc"}


@_NEEDS_GIT
def test_data_dir_real_desta_maquina_e_aceito_quando_fora_do_onedrive(tmp_path: Path) -> None:
    """Caminho canônico real (MSIX pode virtualizar `%LOCALAPPDATA%`): validado pelo que é."""
    assert _GIT is not None
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run([_GIT, "init", "-q", str(repo)], check=True, capture_output=True)  # noqa: S603
    settings = AppSettings(data_dir=tmp_path / "data")
    root_dir = settings.ensure_worktrees_dir()
    root = prepare_worktree_root(
        root_dir,
        sync_roots=settings.sync_roots,
        repository_toplevel=str(repo),
        git_common_dir=str(repo / ".git"),
    )
    assert Path(root.canonical_path) == root_dir.resolve(strict=True)


# ---------------------------------------------- P2: política da árvore e escritor checked


@pytest.mark.parametrize(
    ("paths", "regra"),
    [
        (("a/../b",), "tree_path.path.parent_traversal"),
        (("/abs",), "tree_path.path.root_relative"),
        (("C:/abs",), "tree_path.path.absolute_not_allowed"),
        (("a\\b",), "tree_path.backslash"),
        (("x/.git/config",), "tree_path.dot_git"),
        ((".GIT",), "tree_path.dot_git"),
        (("CON.txt",), "tree_path.path.reserved_name"),
        (("a.txt:fluxo",), "tree_path.path.alternate_data_stream"),
        (("fim.",), "tree_path.path.trailing_dot_or_space"),
        (("PROGRA~1",), "tree_path.path.short_name_alias"),
        (("linha\nquebrada",), "tree_path.path.control_char"),
        (("A.txt", "a.txt"), "tree_path.case_collision"),
        (("foo.txt", "FOO.TXT"), "tree_path.case_collision"),
        (("a", "a/b"), "tree_path.file_directory_conflict"),
        (("A", "A/b.txt"), "tree_path.file_directory_conflict"),
        # Reauditoria P2-003: grafia conflitante de **prefixo**, em qualquer profundidade.
        (("Dir/a.txt", "dir/b.txt"), "tree_path.case_collision"),
        (("A/B/a.txt", "a/b/b.txt"), "tree_path.case_collision"),
        (("A/B/a.txt", "A/b/b.txt"), "tree_path.case_collision"),
        (("Dir/a.txt", "dir/sub/b.txt"), "tree_path.case_collision"),
        (("x/Dir/a.txt", "x/y.txt", "x/dir/b.txt"), "tree_path.case_collision"),
        (("Dir", "dir/b.txt"), "tree_path.case_collision"),
        (("Foo/a.txt", "Foo/b.txt"), None),
        (("Foo/Bar/a.txt", "Foo/Bar/b.txt", "Foo/c.txt"), None),
        (("x",), None),
    ],
)
def test_decide_tree_paths(paths: tuple[str, ...], regra: str | None) -> None:
    from app.safety import decide_tree_paths

    decision = decide_tree_paths(paths)
    if regra is None:
        assert decision.allow
    else:
        assert not decision.allow and decision.rule_id == regra


def test_decide_tree_paths_colisao_de_prefixo_vale_em_todo_so() -> None:
    """G: a política de caixa da E7.4 é a mesma em todos os SOs (já era para o caminho
    completo): a decisão é pura e não consulta a plataforma. No POSIX, `Dir/a.txt` +
    `dir/b.txt` também é recusado — a worktree precisa ser materializável no Windows."""
    from app.safety import decide_tree_paths

    decision = decide_tree_paths(("Dir/a.txt", "dir/b.txt"))
    assert not decision.allow and decision.rule_id == "tree_path.case_collision"


def test_decide_tree_paths_limita_max_path_no_windows() -> None:
    from app.safety import decide_tree_paths

    assert decide_tree_paths(("a" * 50,), absolute_prefix_chars=200).allow
    decision = decide_tree_paths(("a" * 70,), absolute_prefix_chars=200)
    assert decision.rule_id == "tree_path.too_long"


def _writer_root(tmp_path: Path) -> tuple[Path, object]:
    from app.path_runtime import CheckedTreeWriter
    from app.safety.types import ObjectIdentity

    root = tmp_path / "wt"
    root.mkdir()
    info = os.lstat(root)
    return root, CheckedTreeWriter(str(root), ObjectIdentity(info.st_dev, info.st_ino))


def test_writer_cria_diretorios_e_arquivos_crus(tmp_path: Path) -> None:
    root, writer = _writer_root(tmp_path)
    writer.write_file("a/b/c.txt", b"x\r\ny\n", executable=False)  # type: ignore[attr-defined]
    assert (root / "a" / "b" / "c.txt").read_bytes() == b"x\r\ny\n"


def test_writer_nunca_sobrescreve(tmp_path: Path) -> None:
    root, writer = _writer_root(tmp_path)
    (root / "existe.txt").write_bytes(b"do usuario")
    with pytest.raises(FileExistsError):
        writer.write_file("existe.txt", b"novo", executable=False)  # type: ignore[attr-defined]
    assert (root / "existe.txt").read_bytes() == b"do usuario"


@pytest.mark.parametrize("relative", ["a/../b", "x/.git/y", "c\\d", "CON"])
def test_writer_aplica_a_politica_de_caminho(tmp_path: Path, relative: str) -> None:
    _root, writer = _writer_root(tmp_path)
    with pytest.raises(PathAccessDenied):
        writer.write_file(relative, b"x", executable=False)  # type: ignore[attr-defined]


@pytest.mark.skipif(not _IS_WINDOWS, reason="junction é NTFS")
def test_writer_recusa_junction_no_caminho(tmp_path: Path) -> None:
    root, writer = _writer_root(tmp_path)
    fora = tmp_path / "fora"
    fora.mkdir()
    _junction(root / "sub", fora)
    with pytest.raises(PathAccessDenied):
        writer.write_file("sub/x.txt", b"x", executable=False)  # type: ignore[attr-defined]
    assert list(fora.iterdir()) == []


def test_writer_recusa_diretorio_trocado_depois_de_verificado(tmp_path: Path) -> None:
    root, writer = _writer_root(tmp_path)
    writer.write_file("sub/um.txt", b"1", executable=False)  # type: ignore[attr-defined]
    (root / "sub").rename(tmp_path / "sub-antigo")
    (root / "sub").mkdir()  # mesmo nome, outro objeto
    with pytest.raises(PathAccessDenied):
        writer.write_file("sub/dois.txt", b"2", executable=False)  # type: ignore[attr-defined]
    assert list((root / "sub").iterdir()) == []


def _dir_link(link: Path, target: Path) -> None:
    """Link de diretório criável sem privilégio: junction no Windows, symlink no POSIX."""
    if _IS_WINDOWS:
        _junction(link, target)
    else:
        link.symlink_to(target, target_is_directory=True)


def _write(writer: object, relative: str, content: bytes = b"x") -> None:
    writer.write_file(relative, content, executable=False)  # type: ignore[attr-defined]


def test_reaudit_p2_001_raiz_movida_e_trocada_por_link_entre_chamadas(tmp_path: Path) -> None:
    """Reprodutor exato da reauditoria: a troca acontece **antes** da segunda chamada. O cache
    do diretório `parent` não pode autorizar a escrita — a raiz participa de toda revalidação."""
    root, writer = _writer_root(tmp_path)
    _write(writer, "parent/first.txt", b"1")
    moved = tmp_path / "wt-movida"
    root.rename(moved)
    _dir_link(root, moved)
    with pytest.raises(PathAccessDenied):
        _write(writer, "parent/second.txt", b"2")
    assert sorted(p.name for p in (moved / "parent").iterdir()) == ["first.txt"]


def test_writer_recusa_ancestral_intermediario_trocado_por_link(tmp_path: Path) -> None:
    root, writer = _writer_root(tmp_path)
    _write(writer, "a/b/c1.txt")
    real = tmp_path / "a-real"
    (root / "a").rename(real)
    _dir_link(root / "a", real)
    with pytest.raises(PathAccessDenied):
        _write(writer, "a/b/c2.txt")
    assert sorted(p.name for p in (real / "b").iterdir()) == ["c1.txt"]


def test_writer_recusa_ancestral_trocado_por_diretorio_normal(tmp_path: Path) -> None:
    root, writer = _writer_root(tmp_path)
    _write(writer, "a/b/c1.txt")
    (root / "a").rename(tmp_path / "a-antigo")
    (root / "a" / "b").mkdir(parents=True)  # sem reparse, outra identidade em todo nível
    with pytest.raises(PathAccessDenied):
        _write(writer, "a/b/c2.txt")
    assert list((root / "a" / "b").iterdir()) == []


def test_writer_cache_em_varios_niveis_e_so_a_raiz_muda(tmp_path: Path) -> None:
    """Todo nível abaixo da raiz é o **mesmo** objeto do cache; só a raiz é outra."""
    root, writer = _writer_root(tmp_path)
    _write(writer, "x/y/z/1.txt")
    old = tmp_path / "wt-antiga"
    root.rename(old)
    root.mkdir()
    (old / "x").rename(root / "x")
    with pytest.raises(PathAccessDenied):
        _write(writer, "x/y/z/2.txt")
    assert sorted(p.name for p in (root / "x" / "y" / "z").iterdir()) == ["1.txt"]


def test_writer_cache_populado_e_so_o_intermediario_muda(tmp_path: Path) -> None:
    """`a` é outro objeto; `a/b` (o pai direto) é o **mesmo** do cache, movido para dentro."""
    root, writer = _writer_root(tmp_path)
    _write(writer, "a/b/c1.txt")
    (root / "a").rename(tmp_path / "a-antigo")
    (root / "a").mkdir()
    (tmp_path / "a-antigo" / "b").rename(root / "a" / "b")
    with pytest.raises(PathAccessDenied):
        _write(writer, "a/b/c2.txt")
    assert sorted(p.name for p in (root / "a" / "b").iterdir()) == ["c1.txt"]


def test_writer_sem_troca_escreve_normalmente_varias_vezes(tmp_path: Path) -> None:
    root, writer = _writer_root(tmp_path)
    _write(writer, "a/b/c1.txt", b"1")
    _write(writer, "a/b/c2.txt", b"2")
    _write(writer, "a/d.txt", b"3")
    _write(writer, "e.txt", b"4")
    assert (root / "a" / "b" / "c1.txt").read_bytes() == b"1"
    assert (root / "a" / "b" / "c2.txt").read_bytes() == b"2"
    assert (root / "a" / "d.txt").read_bytes() == b"3"
    assert (root / "e.txt").read_bytes() == b"4"


def test_writer_recusa_raiz_com_identidade_diferente(tmp_path: Path) -> None:
    from app.path_runtime import CheckedTreeWriter
    from app.safety.types import ObjectIdentity

    root = tmp_path / "wt"
    root.mkdir()
    with pytest.raises(PathAccessDenied):
        CheckedTreeWriter(str(root), ObjectIdentity(0, 0))
