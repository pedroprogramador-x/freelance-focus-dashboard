"""Gate 1 da E4 — `git_runtime.list_tree` e `git_runtime.working_tree_status`.

As duas leituras que a verificação dupla de
[03](../../docs/architecture/03-context-architecture.md) §3 exige, sob as **mesmas travas
de somente-leitura** que a E3 fixou (E3-AUD-003 / E3-AUD2-003).

O que este arquivo prova, além dos casos felizes:

* árvore com arquivos em profundidades e nomes diversos (inclusive não-ASCII, que sem `-z`
  sairia *C-quoted* e produziria um caminho que não existe);
* commit inexistente, `commit` que não é SHA completo, diretório que não é repo →
  **`None`**, nunca `[]` (a distinção é o que separa `unknown` de um `source_hash` sobre o
  conjunto vazio);
* árvore limpa versus suja com **cada um** dos cinco tipos de divergência;
* o `env` entregue ao `subprocess` é **exatamente** o mesmo `_git_env()` já estabelecido
  pela E3 — reaproveitando `_EXPECTED_GIT_ENV` e o padrão de asserção de
  `test_git_runtime.py`, sem recriar a allowlist.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.git_runtime import (
    _GIT_ENV_OVERRIDES,
    _READONLY_GIT_OPTIONS,
    WorkingTreeChange,
    WorkingTreeEntry,
    _git_env,
    list_tree,
    preflight,
    working_tree_status,
)
from tests.context_helpers import readable
from tests.test_git_runtime import _EXPECTED_GIT_ENV, _GIT, _NEEDS_GIT, _git

_NOT_A_SHA = "nao-e-um-sha"
#: SHA sintaticamente válido que não existe em repositório nenhum.
_ABSENT_COMMIT = "0" * 39 + "1"


def _head(repo: Path) -> str:
    head = preflight(str(repo)).head
    assert head is not None
    return head


@pytest.fixture
def tree_repo(tmp_path: Path) -> Path:
    """Repositório com arquivos em várias profundidades, um nome não-ASCII e um vazio."""
    root = tmp_path / "tree-repo"
    (root / "src" / "nested").mkdir(parents=True)
    (root / "docs").mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Teste")
    _git(root, "config", "commit.gpgsign", "false")

    (root / "README.md").write_text("# raiz\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "src" / "nested" / "deep.txt").write_text("deep\n", encoding="utf-8")
    (root / "src" / "vazio.txt").write_text("", encoding="utf-8")
    (root / "docs" / "acentuação.md").write_text("ção\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "árvore inicial")
    return root


# --------------------------------------------------------------------------- list_tree


@_NEEDS_GIT
def test_list_tree_devolve_todos_os_blobs_ordenados(tree_repo: Path) -> None:
    result = readable(list_tree(str(tree_repo), _head(tree_repo)))

    assert result is not None
    paths = [path for path, _sha in result]
    assert paths == [
        "README.md",
        "docs/acentuação.md",
        "src/app.py",
        "src/nested/deep.txt",
        "src/vazio.txt",
    ]
    assert paths == sorted(paths), "a lista precisa sair ordenada para o hash ser estável"
    assert all(len(sha) == 40 and int(sha, 16) >= 0 for _path, sha in result)


@_NEEDS_GIT
def test_list_tree_nao_c_quota_nome_nao_ascii(tree_repo: Path) -> None:
    """Sem `-z` o git devolveria `"docs/acentua\\303\\247..."` — um caminho que não existe."""
    result = readable(list_tree(str(tree_repo), _head(tree_repo)))

    assert result is not None
    paths = [path for path, _sha in result]
    assert "docs/acentuação.md" in paths
    assert not any(path.startswith('"') or "\\3" in path for path in paths)


@_NEEDS_GIT
def test_list_tree_blob_sha_e_o_do_git_e_muda_com_o_conteudo(tree_repo: Path) -> None:
    """O `blob_sha` vem do git, sem abrir arquivo — e acompanha o conteúdo."""
    antes = dict(readable(list_tree(str(tree_repo), _head(tree_repo))))

    (tree_repo / "src" / "app.py").write_text("print('outro')\n", encoding="utf-8")
    _git(tree_repo, "add", "-A")
    _git(tree_repo, "commit", "-m", "muda app.py")

    depois = dict(readable(list_tree(str(tree_repo), _head(tree_repo))))

    assert antes["src/app.py"] != depois["src/app.py"]
    assert antes["README.md"] == depois["README.md"]


@_NEEDS_GIT
def test_list_tree_de_commit_antigo_ve_a_arvore_daquele_commit(tree_repo: Path) -> None:
    """`verification_commit` congelado: a Parte A enxerga o commit pedido, não o HEAD."""
    primeiro = _head(tree_repo)

    (tree_repo / "novo.txt").write_text("novo\n", encoding="utf-8")
    _git(tree_repo, "add", "-A")
    _git(tree_repo, "commit", "-m", "acrescenta novo.txt")

    antigo = readable(list_tree(str(tree_repo), primeiro))
    atual = readable(list_tree(str(tree_repo), _head(tree_repo)))

    assert antigo is not None and atual is not None
    assert "novo.txt" not in [path for path, _sha in antigo]
    assert "novo.txt" in [path for path, _sha in atual]


@_NEEDS_GIT
def test_list_tree_commit_inexistente_e_none(tree_repo: Path) -> None:
    """`None`, **não** `[]`: lista vazia viraria um `source_hash` legítimo do vazio."""
    assert list_tree(str(tree_repo), _ABSENT_COMMIT) is None


@_NEEDS_GIT
def test_list_tree_recusa_commit_que_nao_e_sha_completo(tree_repo: Path) -> None:
    """`commit` entra no `argv`; só SHA-1 completo passa (injeção de opção fechada)."""
    head = _head(tree_repo)
    for candidato in (
        "HEAD",
        head[:7],
        _NOT_A_SHA,
        "--upload-pack=/tmp/evil",
        f"{head} --output=/tmp/x",
        f" {head} ",
        "0x" + head[2:],
        "",
    ):
        assert list_tree(str(tree_repo), candidato) is None, candidato


def test_list_tree_em_diretorio_que_nao_e_repo(tmp_path: Path) -> None:
    plain = tmp_path / "sem-git"
    plain.mkdir()
    assert list_tree(str(plain), _ABSENT_COMMIT) is None
    assert list_tree(str(plain), "a" * 40) is None


def test_list_tree_em_diretorio_inexistente(tmp_path: Path) -> None:
    assert list_tree(str(tmp_path / "nao-existe"), "a" * 40) is None


@_NEEDS_GIT
def test_list_tree_ignora_submodulo(tree_repo: Path, tmp_path: Path) -> None:
    """Um submódulo aparece como `160000 commit`; não é arquivo que o contexto cubra.

    O `.gitmodules` (que é um blob de verdade) continua listado — a exclusão é do
    ponteiro, não do arquivo.
    """
    outro = tmp_path / "dependencia"
    outro.mkdir()
    _git(outro, "init")
    _git(outro, "config", "user.email", "test@example.invalid")
    _git(outro, "config", "user.name", "Teste")
    _git(outro, "config", "commit.gpgsign", "false")
    (outro / "lib.py").write_text("x = 1\n", encoding="utf-8")
    _git(outro, "add", "-A")
    _git(outro, "commit", "-m", "dep")

    assert _GIT is not None
    added = subprocess.run(  # noqa: S603 — git de teste, argv literal, sem shell
        [_GIT, "-c", "protocol.file.allow=always", "submodule", "add", outro.as_uri(), "vendor"],
        cwd=tree_repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if added.returncode != 0:
        pytest.skip(f"git submodule add indisponível neste ambiente: {added.stderr.strip()[:120]}")
    _git(tree_repo, "commit", "-m", "adiciona submódulo")

    result = readable(list_tree(str(tree_repo), _head(tree_repo)))

    assert result is not None
    paths = [path for path, _sha in result]
    assert "vendor" not in paths, "o ponteiro de submódulo não é um blob"
    assert ".gitmodules" in paths


# ------------------------------------------------------------------ working_tree_status


@_NEEDS_GIT
def test_working_tree_status_arvore_limpa(tree_repo: Path) -> None:
    assert readable(working_tree_status(str(tree_repo))) == []


def test_working_tree_status_em_diretorio_que_nao_e_repo(tmp_path: Path) -> None:
    """`None` (não `[]`): "não sei" e "está limpo" não podem ser a mesma resposta."""
    plain = tmp_path / "sem-git"
    plain.mkdir()
    assert working_tree_status(str(plain)) is None


def test_working_tree_status_em_diretorio_inexistente(tmp_path: Path) -> None:
    assert working_tree_status(str(tmp_path / "nao-existe")) is None


@_NEEDS_GIT
def test_working_tree_status_modified(tree_repo: Path) -> None:
    (tree_repo / "src" / "app.py").write_text("print('alterado')\n", encoding="utf-8")

    assert readable(working_tree_status(str(tree_repo))) == [
        _entry("src/app.py", WorkingTreeChange.MODIFIED)
    ]


@_NEEDS_GIT
def test_working_tree_status_staged(tree_repo: Path) -> None:
    (tree_repo / "src" / "app.py").write_text("print('alterado')\n", encoding="utf-8")
    _git(tree_repo, "add", "src/app.py")

    assert readable(working_tree_status(str(tree_repo))) == [
        _entry("src/app.py", WorkingTreeChange.STAGED)
    ]


@_NEEDS_GIT
def test_working_tree_status_deleted_sem_stage(tree_repo: Path) -> None:
    (tree_repo / "src" / "app.py").unlink()

    assert readable(working_tree_status(str(tree_repo))) == [
        _entry("src/app.py", WorkingTreeChange.DELETED)
    ]


@_NEEDS_GIT
def test_working_tree_status_deleted_com_stage(tree_repo: Path) -> None:
    """`git rm` deixa `D.`; a remoção continua sendo `deleted`, não `staged`."""
    _git(tree_repo, "rm", "-q", "src/app.py")

    assert readable(working_tree_status(str(tree_repo))) == [
        _entry("src/app.py", WorkingTreeChange.DELETED)
    ]


@_NEEDS_GIT
def test_working_tree_status_renamed_reporta_origem_e_destino(tree_repo: Path) -> None:
    """O caminho **original** também é divergência: quem o cobria não o tem mais lá."""
    _git(tree_repo, "mv", "src/app.py", "src/main.py")

    assert readable(working_tree_status(str(tree_repo))) == [
        _entry("src/app.py", WorkingTreeChange.RENAMED),
        _entry("src/main.py", WorkingTreeChange.RENAMED),
    ]


@_NEEDS_GIT
def test_working_tree_status_untracked(tree_repo: Path) -> None:
    (tree_repo / "src" / "novo.py").write_text("novo\n", encoding="utf-8")

    assert readable(working_tree_status(str(tree_repo))) == [
        _entry("src/novo.py", WorkingTreeChange.UNTRACKED)
    ]


@_NEEDS_GIT
def test_untracked_files_all_lista_arquivo_a_arquivo(tree_repo: Path) -> None:
    """Sem `--untracked-files=all` o git resumiria o diretório novo numa entrada só.

    O resumo esconderia exatamente o arquivo que um `source_ref` poderia cobrir.
    """
    (tree_repo / "gerado").mkdir()
    (tree_repo / "gerado" / "a.txt").write_text("a\n", encoding="utf-8")
    (tree_repo / "gerado" / "b.txt").write_text("b\n", encoding="utf-8")

    assert readable(working_tree_status(str(tree_repo))) == [
        _entry("gerado/a.txt", WorkingTreeChange.UNTRACKED),
        _entry("gerado/b.txt", WorkingTreeChange.UNTRACKED),
    ]


@_NEEDS_GIT
def test_working_tree_status_todos_os_tipos_juntos(tree_repo: Path) -> None:
    """Árvore suja com os cinco tipos ao mesmo tempo, num só `git status`."""
    (tree_repo / "README.md").write_text("# mudou\n", encoding="utf-8")  # modified
    (tree_repo / "src" / "vazio.txt").write_text("agora tem\n", encoding="utf-8")
    _git(tree_repo, "add", "src/vazio.txt")  # staged
    (tree_repo / "src" / "nested" / "deep.txt").unlink()  # deleted
    _git(tree_repo, "mv", "src/app.py", "src/main.py")  # renamed
    (tree_repo / "untracked.txt").write_text("novo\n", encoding="utf-8")  # untracked

    result = readable(working_tree_status(str(tree_repo)))

    assert result is not None
    assert {entry.kind for entry in result} == set(WorkingTreeChange)
    assert result == [
        _entry("README.md", WorkingTreeChange.MODIFIED),
        _entry("src/app.py", WorkingTreeChange.RENAMED),
        _entry("src/main.py", WorkingTreeChange.RENAMED),
        _entry("src/nested/deep.txt", WorkingTreeChange.DELETED),
        _entry("src/vazio.txt", WorkingTreeChange.STAGED),
        _entry("untracked.txt", WorkingTreeChange.UNTRACKED),
    ]


@_NEEDS_GIT
def test_working_tree_status_nome_nao_ascii(tree_repo: Path) -> None:
    (tree_repo / "docs" / "acentuação.md").write_text("outro ção\n", encoding="utf-8")

    assert readable(working_tree_status(str(tree_repo))) == [
        _entry("docs/acentuação.md", WorkingTreeChange.MODIFIED)
    ]


@_NEEDS_GIT
def test_working_tree_status_marcador_desconhecido_e_none(
    tree_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Formato inesperado → `None`. Lista parcial de divergência é falso `fresh`."""

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del kwargs
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout=b"9 formato-do-futuro\0", stderr=b""
        )

    monkeypatch.setattr("app.git_runtime.subprocess.run", fake_run)
    assert working_tree_status(str(tree_repo)) is None


@_NEEDS_GIT
def test_list_tree_registro_truncado_e_none(
    tree_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registro fora do formato → `None` inteiro, nunca uma lista parcial."""

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del kwargs
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout=(b"100644 blob " + b"a" * 40 + b"\tok.txt\x00" + b"100644 sem-tab-nem-sha\x00"),
            stderr=b"",
        )

    monkeypatch.setattr("app.git_runtime.subprocess.run", fake_run)
    assert list_tree(str(tree_repo), "b" * 40) is None


# ------------------------------------- E3-AUD2-003: as MESMAS travas de somente-leitura


@_NEEDS_GIT
@pytest.mark.parametrize("leitura", ["list_tree", "working_tree_status"])
def test_leituras_da_e4_passam_o_env_estabelecido_pela_e3(
    tree_repo: Path, monkeypatch: pytest.MonkeyPatch, leitura: str
) -> None:
    """O `env` de cada invocação é **exatamente** `_git_env()` — o mesmo teste da E3.

    Reaproveita `_EXPECTED_GIT_ENV` de `test_git_runtime.py` (a igualdade de conjunto
    fechada em E3-AUD2-003) em vez de reescrever a allowlist aqui: se a E4 tivesse aberto
    uma exceção de ambiente para o `ls-tree` ou para o `status`, esta asserção quebraria.
    """
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        env = kwargs.get("env")
        assert isinstance(env, dict)
        calls.append((list(argv), dict(env)))
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("app.git_runtime.subprocess.run", fake_run)

    if leitura == "list_tree":
        list_tree(str(tree_repo), "a" * 40)
    else:
        working_tree_status(str(tree_repo))

    expected_env = _git_env()
    assert calls, f"{leitura} não chamou o git"
    for argv, env in calls:
        assert argv[1 : 1 + len(_READONLY_GIT_OPTIONS)] == list(_READONLY_GIT_OPTIONS), argv
        assert "-C" in argv
        assert env == expected_env
        assert env["GIT_OPTIONAL_LOCKS"] == "0"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert not any(key.startswith("GIT_") and key not in _GIT_ENV_OVERRIDES for key in env)


@_NEEDS_GIT
def test_env_das_leituras_da_e4_e_o_mesmo_conjunto_fechado_da_e3() -> None:
    """O env é produzido por `_git_env`, e `_git_env` continua fechado por igualdade.

    O teste acima prova que as duas leituras novas usam `_git_env()`; este reafirma, com o
    ambiente contaminado fixo de `test_git_runtime.py`, que `_git_env` não ganhou exceção
    nenhuma para acomodar o `ls-tree` ou o `status --porcelain=v2`.
    """
    from tests.test_git_runtime import _CONTAMINATED_ENVIRON

    assert _git_env(_CONTAMINATED_ENVIRON) == _EXPECTED_GIT_ENV


@_NEEDS_GIT
def test_leituras_da_e4_nao_alteram_o_indice_do_git(tree_repo: Path) -> None:
    """`GIT_OPTIONAL_LOCKS=0` também vale aqui: `git status` não reescreve `.git/index`."""
    index_path = tree_repo / ".git" / "index"
    tracked = tree_repo / "src" / "app.py"
    future = tracked.stat().st_mtime + 10_000
    os.utime(tracked, (future, future))

    before = index_path.read_bytes()
    before_mtime = index_path.stat().st_mtime_ns

    assert readable(working_tree_status(str(tree_repo))) == []
    assert list_tree(str(tree_repo), _head(tree_repo)) is not None

    assert index_path.read_bytes() == before, "as leituras da E4 reescreveram .git/index"
    assert index_path.stat().st_mtime_ns == before_mtime


def _entry(path: str, kind: WorkingTreeChange) -> WorkingTreeEntry:
    return WorkingTreeEntry(path=path, kind=kind)
