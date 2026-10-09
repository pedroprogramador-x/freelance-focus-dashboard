"""E7.4 — worktree de task: criação isolada, reuso estrito, recusa fail closed.

Tudo com `git` de verdade em repositórios temporários. Nenhum teste toca o repositório do
projeto nem a configuração global do Git: identidade de commit vai por `-c` em cada chamada,
e hooks/filtros descartáveis ficam na `.git/config` **do repositório temporário**.

Mapa para a matriz do planejamento: criação do SHA congelado, principal suja, `HEAD` avançado,
SHA inválido, blob/árvore, ref homônima de 40 hex, branch órfã (recusa, D5), divergente, reuso
idempotente, sujeira de todo tipo (inclusive ignorados, D4), operação em andamento, `locked`,
`prunable`, alvo pré-existente, metadata órfã, `.git` quebrado, outro repo no alvo, espaços e
unicode, junction/raiz trocada, hooks (D2), filtros (D3), timeout e cancelamento com a árvore
inteira morta (D6), pós-condição falhando, falha do git.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.git_runtime import (
    InvalidWorktreeRequest,
    WorktreeOutcome,
    WorktreeVerdict,
    _git_env,
    _run_git,
    classify_task_worktree,
    create_worktree,
    inspect_task_worktree,
    list_worktrees,
    repository_layout,
    task_worktree_names,
)
from app.git_runtime import worktree as wt
from app.git_runtime.worktree import _parse_worktree_porcelain
from app.path_runtime import CheckedTreeWriter, prepare_worktree_root
from app.process_runtime import ProcessOutcome, ProcessResult, ProcessSpec, run_supervised
from app.safety.types import WorktreeRoot

_GIT = shutil.which("git")
_IS_WINDOWS = sys.platform == "win32"

pytestmark = pytest.mark.skipif(_GIT is None, reason="git indisponível no PATH")

_ID = (
    "-c",
    "user.name=Teste",
    "-c",
    "user.email=teste@example.invalid",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "advice.objectNameWarning=false",
)

TASK = "abcdef12-3456-4789-8abc-def012345678"
OTHER_TASK = "0badc0de-1111-4222-9333-444455556666"


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """`git` de **teste**: sem o endurecimento do runtime, de propósito (controle)."""
    assert _GIT is not None
    return subprocess.run(  # noqa: S603 — argv literal, sem shell
        [_GIT, *_ID, "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def rev(cwd: Path, name: str = "HEAD") -> str:
    return git(cwd, "rev-parse", "--verify", name).stdout.strip()


def posix(path: Path) -> str:
    """Caminho que o `sh` do Git entende nos dois SOs (`C:/...` no Windows)."""
    return path.as_posix()


@dataclass
class Env:
    repo: Path
    root_dir: Path
    root: WorktreeRoot
    base: str  # commit A
    newer: str  # commit B — o `HEAD` da principal
    tmp: Path

    @property
    def target(self) -> Path:
        return Path(self.root.canonical_path) / "ff-task-abcdef12"


def _make_env(tmp: Path, repo_name: str = "repo", root_name: str = "worktrees") -> Env:
    repo = tmp / repo_name
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("A\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored.log\nbuild/\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "A")
    base = rev(repo)
    (repo / "a.txt").write_text("B\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "B")
    newer = rev(repo)
    root_dir = tmp / root_name
    root_dir.mkdir()
    layout = repository_layout(str(repo))
    assert layout is not None
    root = prepare_worktree_root(
        root_dir,
        sync_roots=(),
        repository_toplevel=layout.toplevel,
        git_common_dir=layout.git_common_dir,
    )
    return Env(repo=repo, root_dir=root_dir, root=root, base=base, newer=newer, tmp=tmp)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return _make_env(tmp_path)


def create(
    env: Env, *, base: str | None = None, task: str = TASK, **kwargs: object
) -> WorktreeOutcome:
    return create_worktree(
        str(env.repo),
        base_commit=env.base if base is None else base,
        task_id=task,
        root=env.root,
        tree_writer=kwargs.pop("tree_writer", CheckedTreeWriter),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def verdict(env: Env, *, base: str | None = None, task: str = TASK) -> WorktreeVerdict:
    facts = inspect_task_worktree(
        str(env.repo), base_commit=env.base if base is None else base, task_id=task, root=env.root
    )
    return classify_task_worktree(facts)


@pytest.fixture
def no_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Qualquer passo Git mutante falha o teste: a recusa não pode agir."""

    def forbidden(spec: ProcessSpec, is_cancelled: Callable[[], bool]) -> ProcessResult:
        raise AssertionError("Git mutante executado num caso que deveria ser recusado")

    monkeypatch.setattr(wt, "run_supervised", forbidden)


if sys.platform == "win32":
    import _winapi

    def _junction(link: Path, target: Path) -> None:
        """Junction NTFS (sem privilégio de symlink)."""
        _winapi.CreateJunction(str(target), str(link))

else:

    def _junction(link: Path, target: Path) -> None:
        raise RuntimeError("junction é NTFS")


def branch_exists(env: Env, name: str = "ff/task-abcdef12") -> bool:
    return (
        git(
            env.repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}", check=False
        ).returncode
        == 0
    )


# ------------------------------------------------------------------------- nomes


def test_nomes_derivados_so_do_id8() -> None:
    names = task_worktree_names(TASK)
    assert names.id8 == "abcdef12"
    assert names.directory_name == "ff-task-abcdef12"
    assert names.branch == "ff/task-abcdef12"
    assert names.branch_ref == "refs/heads/ff/task-abcdef12"


@pytest.mark.parametrize(
    "task_id",
    [
        "ABCDEF12-3456-4789-8abc-def012345678",  # maiúscula: alias em FS case-insensitive
        "abcdef12-3456-1789-8abc-def012345678",  # não é v4
        "abcdef12-3456-4789-cabc-def012345678",  # variante errada
        "abcdef123456478 98abcdef012345678",
        "abcdef12-3456-4789-8abc-def012345678\n",
        "../../../../etc/passwd",
        "abcdef12",
        "",
        None,
        123,
    ],
)
def test_task_id_fora_do_formato_e_recusado(task_id: object) -> None:
    with pytest.raises(InvalidWorktreeRequest):
        task_worktree_names(task_id)


# ------------------------------------------------------------------- criação normal


def test_criacao_normal_nasce_do_sha_congelado(env: Env) -> None:
    out = create(env)

    assert out.verdict is WorktreeVerdict.REUSABLE
    assert out.created is True and out.reused is False
    assert out.process_outcome is ProcessOutcome.EXITED and out.exit_code == 0
    assert out.tree_confirmed_dead is True
    assert out.path is not None and Path(out.path) == env.target
    assert out.workspace_path == out.path
    assert out.head == env.base
    assert rev(env.target) == env.base
    assert rev(env.repo, "refs/heads/ff/task-abcdef12") == env.base
    assert git(env.target, "symbolic-ref", "HEAD").stdout.strip() == "refs/heads/ff/task-abcdef12"
    # Bytes do blob cru: nenhuma conversão de fim de linha (nem `autocrlf`), nenhum filtro.
    assert (env.target / "a.txt").read_bytes() == b"A\n"
    assert rev(env.repo) == env.newer  # a principal não se moveu


def test_head_principal_avancado_nao_muda_a_base(env: Env) -> None:
    (env.repo / "a.txt").write_text("C\n", encoding="utf-8")
    git(env.repo, "commit", "-q", "-am", "C")
    out = create(env)
    assert out.created and rev(env.target) == env.base


def test_criacao_e_idempotente_create_depois_reuse(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[ProcessSpec] = []
    real = run_supervised

    def spy(spec: ProcessSpec, is_cancelled: Callable[[], bool]) -> ProcessResult:
        calls.append(spec)
        return real(spec, is_cancelled)

    monkeypatch.setattr(wt, "run_supervised", spy)
    first = create(env)
    second = create(env)
    assert first.created and not first.reused
    assert second.verdict is WorktreeVerdict.REUSABLE
    assert second.reused and not second.created
    assert len(calls) == 2  # add --no-checkout + read-tree, só na criação


def test_passos_mutantes_sao_add_sem_checkout_e_read_tree_sem_u(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Os dois únicos passos Git mutantes, os dois sob `process_runtime`, endurecidos."""
    git(env.repo, "config", "--local", "filter.meu.driver.smudge", "cat")
    captured: list[ProcessSpec] = []
    real = run_supervised

    def spy(spec: ProcessSpec, is_cancelled: Callable[[], bool]) -> ProcessResult:
        captured.append(spec)
        return real(spec, is_cancelled)

    monkeypatch.setattr(wt, "run_supervised", spy)
    assert create(env).created
    assert len(captured) == 2
    add, read_tree = (list(spec.argv) for spec in captured)
    for argv in (add, read_tree):
        assert Path(argv[0]).is_absolute()
        assert "--no-pager" in argv and "--no-lazy-fetch" in argv
        for option in (
            "core.hooksPath=/dev/null",
            "core.fsmonitor=false",
            "submodule.recurse=false",
            "core.sparseCheckout=false",
        ):
            assert argv[argv.index(option) - 1] == "-c"
        # Filtros não aparecem: nenhum passo materializa conteúdo.
        assert not any(item.startswith("filter.") for item in argv)
        assert "-u" not in argv and "--attr-source" not in " ".join(argv)
    tail = add[add.index("worktree") :]
    assert tail[:7] == [
        "worktree",
        "add",
        "--no-checkout",
        "--no-track",
        "--no-guess-remote",
        "-b",
        "ff/task-abcdef12",
    ]
    assert tail[7] == "--" and tail[-1] == f"{env.base}^{{commit}}"
    assert read_tree[read_tree.index("read-tree") :] == [
        "read-tree",
        "--no-sparse-checkout",
        f"{env.base}^{{commit}}",
    ]
    assert Path(captured[1].cwd) == env.target
    for spec in captured:
        assert spec.timeout_s == wt._GIT_STEP_TIMEOUT_S
        assert set(spec.env) <= {*_git_env()}  # allowlist, nada herdado a mais
        assert spec.env["GIT_NO_LAZY_FETCH"] == "1"


def test_principal_suja_nao_contamina_a_worktree(env: Env) -> None:
    (env.repo / "a.txt").write_text("SUJO\n", encoding="utf-8")
    (env.repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(env.repo, "add", "staged.txt")
    (env.repo / "untracked.txt").write_text("u\n", encoding="utf-8")
    (env.repo / "ignored.log").write_text("i\n", encoding="utf-8")
    index_before = (env.repo / ".git" / "index").read_bytes()
    config_before = (env.repo / ".git" / "config").read_bytes()
    status_before = git(
        env.repo, "status", "--porcelain=v2", "-z", "--untracked-files=all", "--ignored"
    ).stdout

    out = create(env)

    assert out.created
    assert (env.target / "a.txt").read_text(encoding="utf-8").strip() == "A"
    for name in ("staged.txt", "untracked.txt", "ignored.log"):
        assert not (env.target / name).exists()
    assert (env.repo / ".git" / "index").read_bytes() == index_before
    assert (env.repo / ".git" / "config").read_bytes() == config_before
    status_after = git(
        env.repo, "status", "--porcelain=v2", "-z", "--untracked-files=all", "--ignored"
    ).stdout
    assert status_after == status_before


def test_espacos_e_unicode_no_repo_e_na_raiz(tmp_path: Path) -> None:
    env = _make_env(tmp_path, repo_name="repo ç ã 日本 x", root_name="raiz ü 名 y")
    out = create(env)
    assert out.created and rev(env.target) == env.base
    inventory = list_worktrees(str(env.repo))
    assert inventory is not None
    assert inventory.record_at(str(env.target)) is not None


# ---------------------------------------------------------------------- base_commit


@pytest.mark.parametrize(
    "base",
    [
        "HEAD",
        "main",
        "--output=x",
        "-c",
        "",
        "abc1234",
        "A" * 40,
        "a" * 64,  # sha256: fora desta implementação
        "1234567890abcdef1234567890abcdef12345678",  # formato certo, objeto inexistente
    ],
)
def test_base_invalida_e_recusada_sem_processo(env: Env, no_process: None, base: str) -> None:
    out = create(env, base=base)
    assert out.verdict is WorktreeVerdict.BASE_INVALID
    assert not out.created
    assert not env.target.exists()
    assert not branch_exists(env)


def test_base_maiuscula_do_sha_real_e_recusada(env: Env, no_process: None) -> None:
    assert create(env, base=env.base.upper()).verdict is WorktreeVerdict.BASE_INVALID


@pytest.mark.parametrize("kind", ["tree", "blob"])
def test_objeto_que_nao_e_commit_e_recusado(env: Env, no_process: None, kind: str) -> None:
    obj = rev(env.repo, f"{env.base}^{{tree}}" if kind == "tree" else f"{env.base}:a.txt")
    assert create(env, base=obj).verdict is WorktreeVerdict.BASE_INVALID


@pytest.mark.parametrize(
    "namespace", ["refs/heads/", "refs/tags/", "refs/remotes/", "refs/", "refs/remotes/x/"]
)
def test_ref_homonima_de_40_hex_nao_desvia_a_base(env: Env, namespace: str) -> None:
    """Achado do planejamento: SHA cru como start-point segue a ref homônima. `^{commit}` não."""
    git(env.repo, "update-ref", f"{namespace}{env.base}", env.newer)
    out = create(env)
    assert out.created
    assert rev(env.target) == env.base


# ------------------------------------------------------------------- branch (D5)


def test_branch_correta_sem_worktree_e_recusada(env: Env, no_process: None) -> None:
    git(env.repo, "branch", "ff/task-abcdef12", env.base)
    out = create(env)
    assert out.verdict is WorktreeVerdict.BRANCH_ORPHANED
    assert not out.created and not env.target.exists()
    assert rev(env.repo, "refs/heads/ff/task-abcdef12") == env.base  # intocada


def test_branch_divergente_sem_worktree_e_recusada(env: Env, no_process: None) -> None:
    git(env.repo, "branch", "ff/task-abcdef12", env.newer)
    assert create(env).verdict is WorktreeVerdict.BRANCH_DIVERGED
    assert rev(env.repo, "refs/heads/ff/task-abcdef12") == env.newer


def test_commit_humano_na_worktree_diverge_a_branch(env: Env) -> None:
    assert create(env).created
    (env.target / "a.txt").write_text("humano\n", encoding="utf-8")
    git(env.target, "commit", "-q", "-am", "humano")
    assert verdict(env) is WorktreeVerdict.BRANCH_DIVERGED


def test_branch_ocupada_por_outra_worktree_e_recusada(env: Env, no_process: None) -> None:
    git(
        env.repo,
        "worktree",
        "add",
        "-q",
        "-b",
        "ff/task-abcdef12",
        str(env.tmp / "outro-lugar"),
        env.base,
    )
    assert create(env).verdict is WorktreeVerdict.BRANCH_BUSY


def test_alvo_com_outra_branch_e_recusado(env: Env, no_process: None) -> None:
    git(env.repo, "worktree", "add", "-q", "-b", "outra", str(env.target), env.base)
    assert create(env).verdict is WorktreeVerdict.HEAD_MISMATCH


def test_detached_e_recusado(env: Env) -> None:
    assert create(env).created
    git(env.target, "checkout", "-q", "--detach")
    assert verdict(env) is WorktreeVerdict.HEAD_MISMATCH


# ------------------------------------------------------------------- limpeza (D4)


def _dirty_modified(target: Path) -> None:
    (target / "a.txt").write_text("mudou\n", encoding="utf-8")


def _dirty_staged(target: Path) -> None:
    (target / "novo.txt").write_text("n\n", encoding="utf-8")
    git(target, "add", "novo.txt")


def _dirty_untracked(target: Path) -> None:
    (target / "solto.txt").write_text("u\n", encoding="utf-8")


def _dirty_ignored(target: Path) -> None:
    (target / "build").mkdir()
    (target / "build" / "saida.bin").write_bytes(b"\0")


def _dirty_deleted(target: Path) -> None:
    (target / "a.txt").unlink()


@pytest.mark.parametrize(
    "sujar",
    [_dirty_modified, _dirty_staged, _dirty_untracked, _dirty_ignored, _dirty_deleted],
    ids=["modified", "staged", "untracked", "ignored", "deleted"],
)
def test_worktree_suja_nao_e_reusada_e_e_preservada(
    env: Env, sujar: Callable[[Path], None]
) -> None:
    assert create(env).created
    sujar(env.target)
    snapshot = sorted(p.relative_to(env.target).as_posix() for p in env.target.rglob("*"))

    out = create(env)

    assert out.verdict is WorktreeVerdict.DIRTY
    assert not out.created and not out.reused
    assert sorted(p.relative_to(env.target).as_posix() for p in env.target.rglob("*")) == snapshot


def test_conflito_unmerged_e_sujo(env: Env) -> None:
    assert create(env).created
    (env.repo / "a.txt").write_text("C\n", encoding="utf-8")
    git(env.repo, "commit", "-q", "-am", "C")  # C altera B → conflita ao aplicar sobre A
    picked = git(env.target, "cherry-pick", rev(env.repo), check=False)
    assert picked.returncode != 0
    assert (
        "UU" in git(env.target, "status", "--short").stdout
        or "AA" in git(env.target, "status", "--short").stdout
    )
    assert verdict(env) is WorktreeVerdict.DIRTY


@pytest.mark.parametrize(
    ("marker", "is_dir"),
    [
        ("MERGE_HEAD", False),
        ("CHERRY_PICK_HEAD", False),
        ("REVERT_HEAD", False),
        ("REBASE_HEAD", False),
        ("rebase-merge", True),
        ("rebase-apply", True),
        ("sequencer", True),
    ],
)
def test_operacao_em_andamento_impede_reuso_mesmo_com_status_limpo(
    env: Env, marker: str, is_dir: bool
) -> None:
    assert create(env).created
    git_dir = Path(git(env.target, "rev-parse", "--absolute-git-dir").stdout.strip())
    assert git_dir != env.repo / ".git"  # o marcador é da worktree, não do `.git` comum
    if is_dir:
        (git_dir / marker).mkdir()
    else:
        (git_dir / marker).write_text(env.base + "\n", encoding="utf-8")
    assert verdict(env) is WorktreeVerdict.DIRTY


def test_marcador_no_git_comum_nao_contamina_a_worktree(env: Env) -> None:
    assert create(env).created
    (env.repo / ".git" / "MERGE_HEAD").write_text(env.base + "\n", encoding="utf-8")
    assert verdict(env) is WorktreeVerdict.REUSABLE


# ----------------------------------------------------------------- metadata e alvo


def test_worktree_locked_e_recusada(env: Env) -> None:
    assert create(env).created
    git(env.repo, "worktree", "lock", "--reason", "diagnóstico", str(env.target))
    assert create(env).verdict is WorktreeVerdict.LOCKED


def test_prunable_e_stale_metadata_e_preservada(env: Env, no_process: None) -> None:
    names_dir = env.repo / ".git" / "worktrees" / "ff-task-abcdef12"
    # Montado com o git de teste (o `run_supervised` do runtime está proibido aqui).
    git(
        env.repo,
        "worktree",
        "add",
        "-q",
        "-b",
        "ff/task-abcdef12",
        str(env.target),
        f"{env.base}^{{commit}}",
    )
    shutil.rmtree(env.target)
    out = create(env)
    assert out.verdict is WorktreeVerdict.STALE_METADATA
    assert names_dir.is_dir()  # nada foi podado


def test_diretorio_vazio_pre_existente_e_recusado(env: Env, no_process: None) -> None:
    env.target.mkdir()
    assert create(env).verdict is WorktreeVerdict.FOREIGN_PATH
    assert list(env.target.iterdir()) == []
    assert not branch_exists(env)


def test_diretorio_nao_vazio_pre_existente_e_intocado(env: Env, no_process: None) -> None:
    env.target.mkdir()
    (env.target / "do-usuario.txt").write_text("não apagar", encoding="utf-8")
    assert create(env).verdict is WorktreeVerdict.FOREIGN_PATH
    assert (env.target / "do-usuario.txt").read_text(encoding="utf-8") == "não apagar"
    assert not branch_exists(env)


def test_arquivo_no_alvo_e_intocado(env: Env, no_process: None) -> None:
    env.target.write_text("sou arquivo", encoding="utf-8")
    assert create(env).verdict is WorktreeVerdict.FOREIGN_PATH
    assert env.target.read_text(encoding="utf-8") == "sou arquivo"


def test_outro_repositorio_no_alvo_e_recusado(env: Env, no_process: None) -> None:
    env.target.mkdir()
    git(env.target, "init", "-q")
    assert create(env).verdict is WorktreeVerdict.FOREIGN_PATH


@pytest.mark.parametrize("admin_name", ["ff-task-abcdef12", "ff-task-abcdef121"])
def test_admin_dir_orfao_e_stale_metadata(env: Env, no_process: None, admin_name: str) -> None:
    orphan = env.repo / ".git" / "worktrees" / admin_name
    orphan.mkdir(parents=True)
    (orphan / "HEAD").write_text(env.base + "\n", encoding="utf-8")
    inventory = list_worktrees(str(env.repo))
    assert inventory is not None
    assert [entry.name for entry in inventory.orphan_admin_entries] == [admin_name]
    assert all(record.path != str(env.target) for record in inventory.records)  # o `list` não vê
    assert create(env).verdict is WorktreeVerdict.STALE_METADATA
    assert orphan.is_dir()


def test_git_interno_quebrado_e_broken_link(env: Env) -> None:
    assert create(env).created
    dotgit = env.target / ".git"
    with open(dotgit, "r+", encoding="utf-8") as handle:  # `.git` é oculto no Windows
        handle.seek(0)
        handle.write("gitdir: C:/nao/existe\n" if _IS_WINDOWS else "gitdir: /nao/existe\n")
        handle.truncate()
    assert create(env).verdict is WorktreeVerdict.BROKEN_LINK
    assert dotgit.exists()


def test_diretorio_que_nao_e_repo_e_not_a_repo(env: Env, tmp_path: Path, no_process: None) -> None:
    plain = tmp_path / "sem-git"
    plain.mkdir()
    out = create_worktree(
        str(plain), base_commit=env.base, task_id=TASK, root=env.root, tree_writer=CheckedTreeWriter
    )
    assert out.verdict is WorktreeVerdict.NOT_A_REPO


# ------------------------------------------------------------------ raiz (D7)


def test_raiz_trocada_depois_da_validacao_e_recusada(env: Env, no_process: None) -> None:
    env.root_dir.rename(env.tmp / "raiz-antiga")
    env.root_dir.mkdir()  # mesmo caminho, outro objeto
    assert create(env).verdict is WorktreeVerdict.ROOT_INVALID


def test_raiz_de_outro_repositorio_e_recusada(env: Env, tmp_path: Path, no_process: None) -> None:
    other = _make_env(tmp_path / "outro")
    out = create_worktree(
        str(env.repo),
        base_commit=env.base,
        task_id=TASK,
        root=other.root,
        tree_writer=CheckedTreeWriter,
    )
    assert out.verdict is WorktreeVerdict.ROOT_INVALID


def test_raiz_que_nao_e_worktreeroot_levanta(env: Env) -> None:
    with pytest.raises(InvalidWorktreeRequest):
        create_worktree(
            str(env.repo),
            base_commit=env.base,
            task_id=TASK,
            root="C:/x",  # type: ignore[arg-type]
            tree_writer=CheckedTreeWriter,
        )


@pytest.mark.skipif(not _IS_WINDOWS, reason="junction é NTFS")
def test_junction_no_alvo_e_recusada(env: Env, no_process: None) -> None:
    elsewhere = env.tmp / "outro-destino"
    elsewhere.mkdir()
    _junction(env.target, elsewhere)
    assert create(env).verdict is WorktreeVerdict.FOREIGN_PATH
    assert list(elsewhere.iterdir()) == []


@pytest.mark.skipif(not _IS_WINDOWS, reason="junction é NTFS")
def test_raiz_trocada_por_junction_e_recusada(env: Env, no_process: None) -> None:
    moved = env.tmp / "raiz-real"
    env.root_dir.rename(moved)
    _junction(env.root_dir, moved)
    assert create(env).verdict is WorktreeVerdict.ROOT_INVALID


# --------------------------------------------------------------------- hooks (D2)

_HOOKS = ("post-checkout", "post-index-change", "reference-transaction")


def _write_hook(directory: Path, name: str, marker: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    hook = directory / name
    hook.write_text(
        f'#!/bin/sh\necho "{name}" >> "{posix(marker)}"\n', encoding="utf-8", newline="\n"
    )
    hook.chmod(0o755)


@pytest.mark.parametrize(
    "via_hooks_path", [False, True], ids=["git-hooks", "core.hooksPath=.husky"]
)
def test_hooks_nao_executam_na_criacao(env: Env, via_hooks_path: bool) -> None:
    marker = env.tmp / "hooks.log"
    hooks_dir = env.repo / (".husky" if via_hooks_path else ".git/hooks")
    for name in _HOOKS:
        _write_hook(hooks_dir, name, marker)
    if via_hooks_path:
        git(env.repo, "config", "--local", "core.hooksPath", ".husky")

    # Controle: sem o endurecimento, os hooks existem e disparam.
    git(env.repo, "worktree", "add", "-q", "-b", "controle", str(env.tmp / "controle"), env.base)
    fired = marker.read_text(encoding="utf-8")
    assert "post-checkout" in fired and "reference-transaction" in fired
    marker.unlink()

    out = create(env)

    assert out.created
    assert not marker.exists(), marker.read_text(encoding="utf-8")


# ------------------------------------------------------------------- filtros (D3)


def _commit_filtered_files(env: Env, marker: Path) -> str:
    (env.repo / ".gitattributes").write_text(
        "*.evil filter=evil\n*.proc filter=proc\n*.lfs filter=lfs\n", encoding="utf-8"
    )
    (env.repo / "a.evil").write_text("conteúdo evil\n", encoding="utf-8")
    (env.repo / "b.proc").write_text("conteúdo proc\n", encoding="utf-8")
    (env.repo / "c.lfs").write_text("conteúdo lfs\n", encoding="utf-8")
    git(env.repo, "add", "-A")
    git(env.repo, "commit", "-q", "-m", "filtros")
    base = rev(env.repo)
    m = posix(marker)
    git(env.repo, "config", "--local", "filter.evil.smudge", f'echo evil >> "{m}"; cat')
    git(env.repo, "config", "--local", "filter.evil.required", "true")
    git(env.repo, "config", "--local", "filter.proc.process", f'echo proc >> "{m}"; exit 1')
    git(env.repo, "config", "--local", "filter.proc.required", "true")
    # `lfs` na config local sobrepõe o `git-lfs` do sistema: prova o nome, sem depender dele.
    git(env.repo, "config", "--local", "filter.lfs.process", f'echo lfs >> "{m}"; exit 1')
    git(env.repo, "config", "--local", "filter.lfs.smudge", f'echo lfs-smudge >> "{m}"; cat')
    return base


def test_filtros_nao_executam_na_criacao(env: Env) -> None:
    marker = env.tmp / "filters.log"
    base = _commit_filtered_files(env, marker)

    # Controle: sem neutralização, os filtros (inclusive `required`) disparam.
    git(
        env.repo,
        "worktree",
        "add",
        "-q",
        "-b",
        "controle",
        str(env.tmp / "controle"),
        base,
        check=False,
    )
    fired = marker.read_text(encoding="utf-8")
    assert "evil" in fired or "proc" in fired or "lfs" in fired
    marker.unlink()

    out = create(env, base=base)

    assert out.verdict is WorktreeVerdict.REUSABLE and out.created
    assert not marker.exists(), marker.read_text(encoding="utf-8")
    # Bytes do blob cru, exatos: nenhum smudge, nenhuma conversão. Com `git-lfs` instalado no
    # sistema, o `add` do preparo já gravou `c.lfs` como **ponteiro** LFS — e é o ponteiro que a
    # worktree recebe (consequência aceita em D3).
    for name in ("a.evil", "b.proc", "c.lfs"):
        assert (env.target / name).read_bytes() == _blob_bytes(env, base, name)


def _blob_bytes(env: Env, base: str, name: str) -> bytes:
    assert _GIT is not None
    return subprocess.run(  # noqa: S603 — argv literal
        [_GIT, "-C", str(env.repo), "cat-file", "blob", f"{base}:{name}"],
        capture_output=True,
        check=True,
    ).stdout


def test_driver_com_nome_estranho_e_irrelevante(env: Env) -> None:
    """Antes, um nome não neutralizável fechava a criação. Agora nenhum driver é consultado."""
    git(env.repo, "config", "--local", "filter.a=b.smudge", "cat")
    assert create(env).created


# ------------------------------------ filtros por includeIf e fontes de atributo (finding)
#
# Reproduzido antes da correção: um `includeIf "gitdir:…/.git/worktrees/**"` só ativa no
# contexto da worktree **nova** — que é onde o `reset` interno do `worktree add` faz o
# checkout. A enumeração de `filter.*` feita da principal não o via, e o driver executava.
# Cada teste tem um **controle** (o mesmo cenário sem o runtime) que prova o marcador vivo.


def _include_only_in_worktrees(env: Env, body: str) -> None:
    """Config condicional que o repositório principal **não** vê e a worktree vinculada vê."""
    conditional = env.tmp / "condicional.cfg"
    conditional.write_text(body, encoding="utf-8")
    pattern = f"gitdir:{posix(env.repo / '.git')}/worktrees/**"
    git(env.repo, "config", "--local", f"includeIf.{pattern}.path", posix(conditional))
    main_view = git(env.repo, "config", "--name-only", "--get-regexp", r"^filter\.", check=False)
    assert "evil" not in main_view.stdout  # pré-condição do cenário


def _commit(env: Env, files: dict[str, str]) -> str:
    for name, content in files.items():
        path = env.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(env.repo, "add", "-A")
    git(env.repo, "commit", "-q", "-m", "arquivos")
    return rev(env.repo)


def _control_add(env: Env, base: str) -> None:
    """`git worktree add` sem nenhum endurecimento: o controle que dispara o marcador."""
    git(
        env.repo,
        "worktree",
        "add",
        "-q",
        "-b",
        "controle",
        str(env.tmp / "controle"),
        base,
        check=False,
    )


def _marks(marker: Path) -> list[str]:
    return marker.read_text(encoding="utf-8").split() if marker.exists() else []


@pytest.mark.parametrize("key", ["process", "smudge"])
def test_include_if_define_filtro_so_na_worktree_nao_executa(env: Env, key: str) -> None:
    """A/B: `filter.evil.{process,smudge}` só por `includeIf` + `* filter=evil` no base."""
    marker = env.tmp / "evil.log"
    base = _commit(env, {".gitattributes": "* filter=evil\n", "x.txt": "x\n"})
    command = f'echo {key} >> "{posix(marker)}"; ' + ("exit 1" if key == "process" else "cat")
    _include_only_in_worktrees(env, f'[filter "evil"]\n\t{key} = {command}\n\trequired = true\n')

    _control_add(env, base)
    assert _marks(marker), "controle: o filtro condicional precisa disparar sem o runtime"
    marker.unlink()

    out = create(env, base=base)

    assert out.created and out.verdict is WorktreeVerdict.REUSABLE
    assert _marks(marker) == []


def test_include_if_attributes_file_nao_aplica_filtro(env: Env) -> None:
    """C: `core.attributesFile` só por `includeIf`, com `* filter=evil2` e driver condicional —
    nem o `.gitattributes` do projeto menciona `evil2`."""
    marker = env.tmp / "evil2.log"
    attributes = env.tmp / "atributos-condicionais"
    attributes.write_text("* filter=evil2\n", encoding="utf-8")
    _include_only_in_worktrees(
        env,
        f"[core]\n\tattributesFile = {posix(attributes)}\n"
        f'[filter "evil2"]\n\tsmudge = echo evil2 >> "{posix(marker)}"; cat\n',
    )

    _control_add(env, env.base)
    assert _marks(marker)
    marker.unlink()

    assert create(env).created
    assert _marks(marker) == []


def test_info_attributes_do_git_comum_e_neutralizado(env: Env) -> None:
    """D: `.git/info/attributes` (não versionado) com `* filter=evil3`, driver condicional."""
    marker = env.tmp / "evil3.log"
    (env.repo / ".git" / "info").mkdir(exist_ok=True)
    (env.repo / ".git" / "info" / "attributes").write_text("* filter=evil3\n", encoding="utf-8")
    _include_only_in_worktrees(
        env, f'[filter "evil3"]\n\tsmudge = echo evil3 >> "{posix(marker)}"; cat\n'
    )

    _control_add(env, env.base)
    assert _marks(marker)
    marker.unlink()

    assert create(env).created
    assert _marks(marker) == []


def test_include_if_attr_tree_nao_troca_a_fonte_de_atributos(env: Env) -> None:
    """`attr.tree` só por `includeIf`, apontando para uma árvore com `* filter=evil4`: o
    `--attr-source=<base>` do runtime vence."""
    marker = env.tmp / "evil4.log"
    assert _GIT is not None
    blob = (
        subprocess.run(  # noqa: S603
            [_GIT, "-C", str(env.repo), "hash-object", "-w", "--stdin"],
            input=b"* filter=evil4\n",
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    tree = (
        subprocess.run(  # noqa: S603
            [_GIT, "-C", str(env.repo), "mktree"],
            input=f"100644 blob {blob}\t.gitattributes\n".encode(),
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    _include_only_in_worktrees(
        env,
        f"[attr]\n\ttree = {tree}\n"
        f'[filter "evil4"]\n\tsmudge = echo evil4 >> "{posix(marker)}"; cat\n',
    )

    _control_add(env, env.base)
    assert _marks(marker)
    marker.unlink()

    assert create(env).created
    assert _marks(marker) == []


def test_lfs_definido_por_include_if_continua_neutralizado(env: Env) -> None:
    """F: o driver `lfs` com comando só no contexto da worktree (sem depender de git-lfs)."""
    marker = env.tmp / "lfs.log"
    base = _commit(env, {".gitattributes": "*.bin filter=lfs\n", "dado.bin": "bytes\n"})
    _include_only_in_worktrees(
        env,
        f'[filter "lfs"]\n\tprocess = echo lfs >> "{posix(marker)}"; exit 1\n'
        f'\tsmudge = echo lfs-smudge >> "{posix(marker)}"; cat\n\trequired = true\n',
    )

    _control_add(env, base)
    assert _marks(marker)
    marker.unlink()

    assert create(env, base=base).created
    assert _marks(marker) == []


def test_git_sem_no_lazy_fetch_fecha_sem_agir(
    env: Env, no_process: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git < 2.44 não conhece `--no-lazy-fetch`: a leitura do snapshot falha e nada muda."""
    monkeypatch.setattr(wt, "_OBJECT_READ_OPTIONS", ("--opcao-inexistente-e7-4",))
    out = create(env)
    assert out.verdict is WorktreeVerdict.UNVERIFIABLE
    assert not env.target.exists() and not branch_exists(env)


def _no_status_nor_attributes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Registra toda leitura Git do módulo e falha em `status`/`check-attr`/`diff`."""
    seen: list[tuple[str, ...]] = []
    real = _run_git

    def spy(
        git_path: str, cwd: str, *args: str, stdin: bytes | None = None
    ) -> subprocess.CompletedProcess[bytes] | None:
        assert not {"status", "check-attr", "diff", "checkout-index"} & set(args), args
        seen.append(args)
        return real(git_path, cwd, *args, stdin=stdin)

    monkeypatch.setattr(wt, "_run_git", spy)
    return seen


def _stat_dirty(path: Path, seconds: int) -> None:
    """Mesmo conteúdo, stat diferente: um `git status` precisaria reler (e filtrar)."""
    os.utime(path, (seconds, seconds))


def test_inspecao_nao_roda_status_nem_consulta_atributos(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E: worktree existente; `filter.evil.clean` só por `includeIf`; arquivo rastreado com
    `filter=evil`. A inspeção não roda `git status` (que executaria o clean) — prova raw."""
    marker = env.tmp / "clean.log"
    base = _commit(env, {".gitattributes": "*.txt filter=evil\n", "x.txt": "x\n"})
    assert create(env, base=base).created
    _include_only_in_worktrees(
        env, f'[filter "evil"]\n\tclean = echo clean >> "{posix(marker)}"; cat\n'
    )

    # Controle: `git status` puro na worktree executa o clean condicional.
    _stat_dirty(env.target / "x.txt", 1_000_000_000)
    git(env.target, "status", "--porcelain=v2")
    assert _marks(marker), "controle: o clean condicional precisa disparar sem o runtime"
    marker.unlink()

    seen = _no_status_nor_attributes(monkeypatch)
    _stat_dirty(env.target / "x.txt", 1_000_000_100)
    assert verdict(env, base=base) is WorktreeVerdict.REUSABLE
    assert seen, "a espiã precisa ter visto as leituras"
    assert _marks(marker) == []

    # `.gitattributes` da worktree alterado nomeando outro driver condicional: sujo, e nada roda.
    (env.target / ".gitattributes").write_text("*.txt filter=evil9\n", encoding="utf-8")
    (env.tmp / "condicional.cfg").write_text(
        f'[filter "evil9"]\n\tclean = echo clean9 >> "{posix(marker)}"; cat\n', encoding="utf-8"
    )
    _stat_dirty(env.target / "x.txt", 1_000_000_200)
    assert verdict(env, base=base) is WorktreeVerdict.DIRTY
    assert _marks(marker) == []


# ------------------------------------------------------------ P2-001 (literais especiais)


@pytest.mark.parametrize("name", ["set", "unset", "unspecified"])
@pytest.mark.parametrize("origem", ["local", "includeIf"])
def test_p2_001_driver_com_nome_de_estado_especial_nunca_executa(
    env: Env, name: str, origem: str
) -> None:
    """`filter=set|unset|unspecified` é **valor** de atributo. O parser antigo os descartava
    como estados e o driver homônimo executava no checkout (reproduzido antes da correção)."""
    marker = env.tmp / "p2-001.log"
    m = posix(marker)
    base = _commit(env, {".gitattributes": f"*.txt filter={name}\n", "x.txt": "x\n"})
    body = (
        f'[filter "{name}"]\n'
        f'\tsmudge = echo {name}-smudge >> "{m}"; cat\n'
        f'\tclean = echo {name}-clean >> "{m}"; cat\n'
        f'\tprocess = echo {name}-process >> "{m}"; exit 1\n'
        "\trequired = true\n"
    )
    if origem == "includeIf":
        _include_only_in_worktrees(env, body)
    else:
        (env.tmp / "local.cfg").write_text(body, encoding="utf-8")
        git(env.repo, "config", "--local", "include.path", posix(env.tmp / "local.cfg"))

    _control_add(env, base)
    assert _marks(marker), "controle: o driver homônimo precisa disparar sem o runtime"
    marker.unlink()

    out = create(env, base=base)
    assert out.created and out.verdict is WorktreeVerdict.REUSABLE
    _stat_dirty(env.target / "x.txt", 1_000_000_000)
    assert verdict(env, base=base) is WorktreeVerdict.REUSABLE
    assert create(env, base=base).reused
    assert _marks(marker) == []
    assert (env.target / "x.txt").read_bytes() == b"x\n"


# -------------------------------------------------- P2-002 (corrida em info/attributes)


class _AttributesToggler:
    """Alterna `.git/info/attributes` entre `filter=one` e `filter=two` sem parar, e conta."""

    def __init__(self, env: Env) -> None:
        self._file = env.repo / ".git" / "info" / "attributes"
        self._file.parent.mkdir(exist_ok=True)
        self._stop = threading.Event()
        self.count = 0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            text = "* filter=two\n" if self.count % 2 else "* filter=one\n"
            try:
                self._file.write_text(text, encoding="utf-8")
            except OSError:
                continue  # o arquivo pode estar aberto por outro leitor no Windows
            self.count += 1
            time.sleep(0.001)

    def __enter__(self) -> _AttributesToggler:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()


def _one_two_drivers(env: Env, marker: Path, key: str) -> None:
    m = posix(marker)
    tail = "exit 1" if key == "process" else "cat"
    _include_only_in_worktrees(
        env,
        "".join(
            f'[filter "{d}"]\n\t{key} = echo {d}-{key} >> "{m}"; {tail}\n\trequired = true\n'
            for d in ("one", "two")
        ),
    )


@pytest.mark.parametrize("key", ["smudge", "process"])
def test_p2_002_corrida_em_info_attributes_durante_create(tmp_path: Path, key: str) -> None:
    """Reprodutor do Codex (executava `filter.two.smudge` em 2/6 rodadas no modelo antigo):
    agora os atributos simplesmente não participam da materialização."""
    for rodada in range(4):
        env = _make_env(tmp_path / f"r{rodada}")
        marker = env.tmp / "p2-002.log"
        base = _commit(env, {f"f{i}.txt": f"{i}\n" for i in range(40)})
        _one_two_drivers(env, marker, key)
        with _AttributesToggler(env) as toggler:
            before = toggler.count
            out = create(env, base=base)
            during = toggler.count - before
        assert during > 0, "o toggler precisa ter alterado o arquivo durante a criação"
        assert out.created, (rodada, out.verdict, out.detail)
        assert _marks(marker) == [], (rodada, _marks(marker))


def test_p2_002_corrida_em_info_attributes_durante_inspect(env: Env) -> None:
    marker = env.tmp / "p2-002-clean.log"
    base = _commit(env, {f"f{i}.txt": f"{i}\n" for i in range(40)})
    assert create(env, base=base).created
    _one_two_drivers(env, marker, "clean")
    with _AttributesToggler(env) as toggler:
        for rodada in range(8):
            _stat_dirty(env.target / "f0.txt", 1_000_000_000 + rodada)
            before = toggler.count
            assert verdict(env, base=base) is WorktreeVerdict.REUSABLE
            assert toggler.count > before or rodada > 0
    assert toggler.count > 0
    assert _marks(marker) == []


def test_comandos_da_e7_4_nao_invocam_textconv_diff_externo_pager_nem_editor(env: Env) -> None:
    """Item 8: nenhum comando novo produz diff nem abre pager/editor — provado com marcador."""
    marker = env.tmp / "diff.log"
    m = posix(marker)
    base = _commit(env, {".gitattributes": "* diff=evil\n", "x.txt": "x\n"})
    for key, value in (
        ("diff.external", f'echo external >> "{m}"'),
        ("diff.evil.textconv", f'echo textconv >> "{m}"; cat'),
        ("core.pager", f'echo pager >> "{m}"; cat'),
        ("pager.status", "true"),
        ("core.editor", f'echo editor >> "{m}"'),
        ("sequence.editor", f'echo editor >> "{m}"'),
    ):
        git(env.repo, "config", "--local", key, value)

    assert create(env, base=base).created
    assert verdict(env, base=base) is WorktreeVerdict.REUSABLE
    assert list_worktrees(str(env.repo)) is not None
    assert _marks(marker) == []


# ----------------------------------------------------- integridade do checkout (D4)


@pytest.mark.parametrize("modo", ["sparse-checkout set", "core.sparseCheckout compartilhado"])
def test_sparse_herdado_nao_deixa_a_worktree_parcial(env: Env, modo: str) -> None:
    """I: medido antes da correção, o sparse da principal era copiado e a worktree nascia
    parcial (bits `skip-worktree`) com `status` limpo — e passava como `REUSABLE`."""
    base = _commit(env, {"sub/s.txt": "s\n", "outro/o.txt": "o\n"})
    if modo == "sparse-checkout set":
        git(env.repo, "sparse-checkout", "set", "sub")
    else:
        git(env.repo, "config", "--local", "core.sparseCheckout", "true")
        (env.repo / ".git" / "info").mkdir(exist_ok=True)
        (env.repo / ".git" / "info" / "sparse-checkout").write_text("/sub/\n", encoding="utf-8")

    out = create(env, base=base)

    assert out.created and out.verdict is WorktreeVerdict.REUSABLE
    materializado = sorted(
        p.relative_to(env.target).as_posix()
        for p in env.target.rglob("*")
        if p.is_file() and p.name != ".git"
    )
    no_base = sorted(git(env.repo, "ls-tree", "-r", "--name-only", base).stdout.split())
    assert materializado == no_base
    tags = {line[:1] for line in git(env.target, "ls-files", "-v").stdout.splitlines()}
    assert tags == {"H"}


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_bits_do_indice_que_escondem_mudanca_impedem_reuso(env: Env, flag: str) -> None:
    """Medido: `assume-unchanged` + arquivo modificado deixa o `status` **limpo**. Só o
    `ls-files -v` revela; qualquer entrada que não seja `H` é sujeira para o reuso."""
    assert create(env).created
    git(env.target, "update-index", flag, "a.txt")
    (env.target / "a.txt").write_text("mudou escondido\n", encoding="utf-8")
    assert git(env.target, "status", "--porcelain").stdout.strip() == ""  # o status não vê
    assert verdict(env) is WorktreeVerdict.DIRTY


# ------------------------------------------------------- timeout e cancelamento (D6)


def _install_endless_hook(env: Env, heartbeat: Path) -> None:
    """Um `reference-transaction` que nunca termina e bate um pulso — neto do `worktree add`
    (roda dentro do `git branch` que o `add` cria para o `-b`)."""
    hook = env.repo / ".git" / "hooks" / "reference-transaction"
    hook.write_text(
        f'#!/bin/sh\nwhile :; do echo b >> "{posix(heartbeat)}"; sleep 0.2; done\n',
        encoding="utf-8",
        newline="\n",
    )
    hook.chmod(0o755)


def _assert_heartbeat_stopped(heartbeat: Path) -> None:
    """Se algum descendente sobrevivesse, o arquivo continuaria crescendo."""
    time.sleep(0.6)
    size = heartbeat.stat().st_size
    time.sleep(1.5)
    assert heartbeat.stat().st_size == size, "um processo filho continuou vivo"


def _with_hooks_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # Só para estes testes: reabilita hooks para haver um neto lento a matar. A neutralização
    # em si é provada em `test_hooks_nao_executam_na_criacao`.
    options = list(wt._MUTATING_GIT_OPTIONS)
    index = options.index("core.hooksPath=/dev/null")
    del options[index - 1 : index + 1]
    monkeypatch.setattr(wt, "_MUTATING_GIT_OPTIONS", tuple(options))


_INTERRUPTED = {
    WorktreeVerdict.LOCKED,
    WorktreeVerdict.STALE_METADATA,
    WorktreeVerdict.CREATED_INVALID,
    WorktreeVerdict.FOREIGN_PATH,
    WorktreeVerdict.UNVERIFIABLE,
    WorktreeVerdict.DIRTY,
    WorktreeVerdict.BRANCH_ORPHANED,
    WorktreeVerdict.BRANCH_BUSY,
}


def test_timeout_mata_a_arvore_inteira_e_preserva_o_estado(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    heartbeat = env.tmp / "heartbeat.log"
    _install_endless_hook(env, heartbeat)
    _with_hooks_enabled(monkeypatch)
    monkeypatch.setattr(wt, "_GIT_STEP_TIMEOUT_S", 4.0)

    out = create(env)

    assert out.process_outcome is ProcessOutcome.TIMEOUT
    assert out.tree_confirmed_dead is True
    assert out.created is False
    assert out.verdict in _INTERRUPTED
    assert heartbeat.exists(), "o hook precisa ter rodado para o teste provar algo"
    _assert_heartbeat_stopped(heartbeat)
    assert verdict(env) is not WorktreeVerdict.REUSABLE  # interrompido nunca vira reuso


def test_cancelamento_durante_o_add_mata_a_arvore(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    heartbeat = env.tmp / "heartbeat.log"
    _install_endless_hook(env, heartbeat)
    _with_hooks_enabled(monkeypatch)

    out = create(env, is_cancelled=heartbeat.exists)

    assert out.process_outcome is ProcessOutcome.CANCELLED
    assert out.tree_confirmed_dead is True
    assert not out.created and out.verdict in _INTERRUPTED
    _assert_heartbeat_stopped(heartbeat)


def _install_endless_index_hook(env: Env, heartbeat: Path) -> None:
    """Um `post-index-change` que nunca termina: neto do `read-tree` (roda depois de o índice
    ser gravado). O `add --no-checkout` não grava índice, então só o segundo passo trava."""
    hook = env.repo / ".git" / "hooks" / "post-index-change"
    hook.write_text(
        f'#!/bin/sh\nwhile :; do echo i >> "{posix(heartbeat)}"; sleep 0.2; done\n',
        encoding="utf-8",
        newline="\n",
    )
    hook.chmod(0o755)


def _count_steps(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    steps: list[tuple[str, ...]] = []
    real = wt._run_git_step

    def spy(
        argv: tuple[str, ...],
        cwd: str,
        is_cancelled: Callable[[], bool],
        remaining_s: Callable[[], float] | None = None,  # E8.4.2: prazo opcional do chamador
    ) -> object:
        steps.append(argv)
        return real(argv, cwd, is_cancelled, remaining_s)

    monkeypatch.setattr(wt, "_run_git_step", spy)
    return steps


@pytest.mark.parametrize("modo", ["timeout", "cancelamento"])
def test_interrupcao_do_read_tree_mata_a_arvore_e_nunca_vira_reuso(
    env: Env, monkeypatch: pytest.MonkeyPatch, modo: str
) -> None:
    heartbeat = env.tmp / "heartbeat-index.log"
    _install_endless_index_hook(env, heartbeat)
    _with_hooks_enabled(monkeypatch)
    steps = _count_steps(monkeypatch)
    if modo == "timeout":
        monkeypatch.setattr(wt, "_GIT_STEP_TIMEOUT_S", 4.0)
        out = create(env)
        assert out.process_outcome is ProcessOutcome.TIMEOUT
    else:
        out = create(env, is_cancelled=heartbeat.exists)
        assert out.process_outcome is ProcessOutcome.CANCELLED

    assert heartbeat.exists(), "o hook do read-tree precisa ter rodado para o teste provar algo"
    assert out.tree_confirmed_dead is True
    _assert_heartbeat_stopped(heartbeat)
    assert out.created is False and out.verdict is WorktreeVerdict.CREATED_INVALID
    assert out.detail is not None and out.detail.startswith("git read-tree:")
    # Exatamente os dois passos: nenhuma nova tentativa, nenhuma limpeza.
    assert [argv[argv.index("-C") + 2] for argv in steps] == ["worktree", "read-tree"]
    assert env.target.is_dir() and (env.target / ".git").is_file() and branch_exists(env)
    assert not (env.target / "a.txt").exists()  # nada materializado depois da interrupção
    assert verdict(env) is not WorktreeVerdict.REUSABLE
    assert create(env).verdict is not WorktreeVerdict.REUSABLE


def test_cancelamento_durante_a_materializacao_preserva_o_parcial(env: Env) -> None:
    base = _commit(env, {f"f{i}.txt": f"{i}\n" for i in range(10)})
    written: list[str] = []

    class Counting(CheckedTreeWriter):
        def write_file(self, relative: str, content: bytes, *, executable: bool) -> None:
            super().write_file(relative, content, executable=executable)
            written.append(relative)

    out = create(env, base=base, tree_writer=Counting, is_cancelled=lambda: len(written) >= 3)

    assert out.verdict is WorktreeVerdict.CREATED_INVALID and not out.created
    assert out.detail == "cancelado durante a materialização"
    assert len(written) == 3
    assert all((env.target / name).exists() for name in written)  # nada apagado
    assert verdict(env, base=base) is WorktreeVerdict.DIRTY


def test_cancelado_antes_de_comecar_nao_cria_nada(env: Env) -> None:
    out = create(env, is_cancelled=lambda: True)
    assert out.process_outcome is ProcessOutcome.CANCELLED  # antes de o add criar processo
    assert not out.created and out.verdict is WorktreeVerdict.UNVERIFIABLE
    assert not env.target.exists() and not branch_exists(env)


# ------------------------------------------------------------ pós-condições e falhas


def test_pos_condicao_falhando_e_created_invalid_preservada(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = run_supervised

    def sabotage(spec: ProcessSpec, is_cancelled: Callable[[], bool]) -> ProcessResult:
        result = real(spec, is_cancelled)
        (env.target / "intruso.txt").write_text("x", encoding="utf-8")
        return result

    monkeypatch.setattr(wt, "run_supervised", sabotage)
    out = create(env)
    assert out.verdict is WorktreeVerdict.CREATED_INVALID
    assert out.created is False
    assert (env.target / "intruso.txt").exists()
    assert out.workspace_path is None


def test_extra_depois_da_materializacao_e_created_invalid(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pós-condição é a **mesma** verificação raw do reuso: um extra escrito no meio falha."""
    real = wt._materialize

    def sabotage(*args: object, **kwargs: object) -> str | None:
        problem = real(*args, **kwargs)  # type: ignore[arg-type]
        (env.target / "vazio").mkdir()
        return problem

    monkeypatch.setattr(wt, "_materialize", sabotage)
    out = create(env)
    assert out.verdict is WorktreeVerdict.CREATED_INVALID
    assert out.detail == "pós-condição falhou: dirty"
    assert (env.target / "vazio").is_dir()


def test_falha_do_git_nao_e_criacao(env: Env) -> None:
    # `refs/heads/ff/task-abcdef12/x` impede criar `ff/task-abcdef12` (conflito D/F).
    git(env.repo, "branch", "ff/task-abcdef12/x", env.base)
    out = create(env)
    assert out.process_outcome is ProcessOutcome.EXITED and out.exit_code != 0
    assert out.tree_confirmed_dead is True
    assert out.verdict is WorktreeVerdict.UNVERIFIABLE
    assert not out.created and not env.target.exists()


def test_workspace_em_subdiretorio_aponta_para_dentro_da_worktree(tmp_path: Path) -> None:
    env = _make_env(tmp_path)
    (env.repo / "ws").mkdir()
    (env.repo / "ws" / "f.txt").write_text("f\n", encoding="utf-8")
    git(env.repo, "add", "-A")
    git(env.repo, "commit", "-q", "-m", "ws")
    base = rev(env.repo)
    out = create_worktree(
        str(env.repo / "ws"),
        base_commit=base,
        task_id=TASK,
        root=env.root,
        tree_writer=CheckedTreeWriter,
    )
    assert out.created
    assert out.workspace_path is not None
    assert Path(out.workspace_path) == env.target / "ws"
    assert (Path(out.workspace_path) / "f.txt").exists()


# ------------------------------------------------ materialização raw: árvore e conteúdo


def test_diretorio_extra_vazio_e_sujo(env: Env) -> None:
    assert create(env).created
    (env.target / "novo-vazio").mkdir()
    assert verdict(env) is WorktreeVerdict.DIRTY


def test_indice_diferente_do_base_e_sujo_mesmo_com_arquivos_iguais(env: Env) -> None:
    """Staged remove + arquivo de volta no disco: o filesystem bate, o índice não."""
    assert create(env).created
    git(env.target, "rm", "-q", "--cached", "a.txt")
    assert (env.target / "a.txt").exists()
    assert verdict(env) is WorktreeVerdict.DIRTY


def test_intent_to_add_e_sujo(env: Env) -> None:
    assert create(env).created
    (env.target / "novo.txt").write_text("n\n", encoding="utf-8")
    git(env.target, "add", "--intent-to-add", "novo.txt")
    assert verdict(env) is WorktreeVerdict.DIRTY


def _ita_flag_set(target: Path, path: str) -> bool:
    """Só para o teste confirmar o cenário: `--debug` expõe o bit `CE_INTENT_TO_ADD`."""
    debug = git(target, "ls-files", "--debug", "--", path).stdout
    flags = [line.split("flags:")[1].strip() for line in debug.splitlines() if "flags:" in line]
    return len(flags) == 1 and bool(int(flags[0], 16) & 0x20000000)


def test_reaudit_p2_002_intent_to_add_em_arquivo_vazio_do_base_nao_e_reusavel(env: Env) -> None:
    """Reprodutor da reauditoria: o base tem um arquivo **vazio**; a entrada `intent-to-add`
    tem o mesmo modo, o mesmo oid (blob vazio) e estágio 0, `ls-files -v` diz `H` e o disco
    bate byte a byte. Só a flag do índice difere — e ela precisa impedir o reuso."""
    base = _commit(env, {"vazio.txt": ""})
    assert create(env, base=base).created
    antes = git(env.target, "ls-files", "--stage", "-z").stdout
    git(env.target, "rm", "-q", "--cached", "vazio.txt")
    git(env.target, "add", "--intent-to-add", "vazio.txt")

    # O cenário é exatamente o do finding: nada visível à prova antiga mudou.
    assert git(env.target, "ls-files", "--stage", "-z").stdout == antes
    assert {line[:1] for line in git(env.target, "ls-files", "-v").stdout.splitlines()} == {"H"}
    assert (env.target / "vazio.txt").read_bytes() == b""
    assert _ita_flag_set(env.target, "vazio.txt")

    assert verdict(env, base=base) is WorktreeVerdict.DIRTY
    out = create(env, base=base)
    assert out.verdict is WorktreeVerdict.DIRTY and not out.reused and not out.created


def _index_only(env: Env, change: str, base: str) -> None:
    """Muda **só o índice** da worktree; o filesystem continua exatamente o base."""
    t = env.target
    if change == "assume-unchanged":
        git(t, "update-index", "--assume-unchanged", "a.txt")
    elif change == "skip-worktree":
        git(t, "update-index", "--skip-worktree", "a.txt")
    elif change == "intent-to-add":
        git(t, "rm", "-q", "--cached", "vazio.txt")
        git(t, "add", "--intent-to-add", "vazio.txt")
    elif change == "staged-modification":
        other = _hash_blob(env, b"outro conteudo\n")
        git(t, "update-index", "--cacheinfo", f"100644,{other},a.txt")
    elif change == "staged-add":
        git(t, "update-index", "--add", "--cacheinfo", f"100644,{_hash_blob(env, b'n')},novo.txt")
    elif change == "staged-delete":
        git(t, "rm", "-q", "--cached", "a.txt")
    elif change == "mode-change":
        git(t, "update-index", "--chmod=+x", "a.txt")
    elif change == "unmerged":
        oid = git(t, "rev-parse", f"{base}:a.txt").stdout.strip()
        entries = f"0 {'0' * 40}\ta.txt\n100644 {oid} 1\ta.txt\n100644 {oid} 2\ta.txt\n"
        assert _GIT is not None
        subprocess.run(  # noqa: S603 — argv literal
            [_GIT, "-C", str(t), "update-index", "--index-info"],
            input=entries.encode(),
            capture_output=True,
            check=True,
        )
    else:
        assert change == "normal"


@pytest.mark.parametrize(
    "change",
    [
        "normal",
        "assume-unchanged",
        "skip-worktree",
        "intent-to-add",
        "unmerged",
        "staged-modification",
        "staged-add",
        "staged-delete",
        "mode-change",
    ],
)
def test_estado_do_indice_sozinho_decide_o_reuso(env: Env, change: str) -> None:
    """Gate da reauditoria P2-002: "índice == base" é conteúdo estrutural **e** flags. Em
    cada caso o disco é o base intacto — só o índice pode revelar a diferença."""
    base = _commit(env, {"vazio.txt": ""})
    assert create(env, base=base).created
    _index_only(env, change, base)
    expected = WorktreeVerdict.REUSABLE if change == "normal" else WorktreeVerdict.DIRTY
    assert verdict(env, base=base) is expected


def test_prova_do_indice_por_diff_index_fecha_em_git_sem_a_opcao(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git que não conhece `--ita-invisible-in-index` (ou qualquer erro) não vira "limpo"."""
    assert create(env).created
    real = wt._index_diff_argv
    monkeypatch.setattr(
        wt,
        "_index_diff_argv",
        lambda base: tuple(
            "--opcao-inexistente" if a == "--ita-invisible-in-index" else a for a in real(base)
        ),
    )
    assert verdict(env) is WorktreeVerdict.UNVERIFIABLE


@pytest.mark.parametrize(
    ("returncode", "stdout", "esperado"),
    [
        (0, b"", True),
        (1, b":100644 000000 e69d 0000 D\0vazio.txt\0", False),
        (0, b":100644 000000 e69d 0000 D\0vazio.txt\0", None),
        (1, b"", None),
        (128, b"", None),
        (129, b"", None),
    ],
)
def test_resultado_do_diff_index_e_estrito(
    returncode: int, stdout: bytes, esperado: bool | None
) -> None:
    done = subprocess.CompletedProcess(args=(), returncode=returncode, stdout=stdout, stderr=b"")
    assert wt._index_matches_tree(done) is esperado
    assert wt._index_matches_tree(None) is None


def test_conteudo_trocado_com_mesmo_tamanho_e_sujo(env: Env) -> None:
    assert create(env).created
    (env.target / "a.txt").write_bytes(b"Z\n")  # mesmo tamanho de "A\n"
    assert verdict(env) is WorktreeVerdict.DIRTY


def test_nome_com_caixa_diferente_e_sujo(env: Env) -> None:
    assert create(env).created
    (env.target / "a.txt").rename(env.target / "tmp-rename")
    (env.target / "tmp-rename").rename(env.target / "A.TXT")
    assert verdict(env) is WorktreeVerdict.DIRTY


@pytest.mark.skipif(_IS_WINDOWS, reason="bit de execução é POSIX")
def test_bit_de_execucao_e_materializado_e_verificado(env: Env) -> None:
    (env.repo / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    git(env.repo, "add", "run.sh")
    git(env.repo, "update-index", "--chmod=+x", "run.sh")
    git(env.repo, "commit", "-q", "-m", "exec")
    base = rev(env.repo)
    assert create(env, base=base).created
    assert os.stat(env.target / "run.sh").st_mode & 0o100
    assert not os.stat(env.target / "a.txt").st_mode & 0o100
    os.chmod(env.target / "run.sh", 0o644)
    assert verdict(env, base=base) is WorktreeVerdict.DIRTY


def test_modo_executavel_no_indice_e_conferido_em_todo_so(env: Env) -> None:
    (env.repo / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    git(env.repo, "add", "run.sh")
    git(env.repo, "update-index", "--chmod=+x", "run.sh")
    git(env.repo, "commit", "-q", "-m", "exec")
    base = rev(env.repo)
    assert create(env, base=base).created
    git(env.target, "update-index", "--chmod=-x", "run.sh")
    assert verdict(env, base=base) is WorktreeVerdict.DIRTY


def _commit_index_entry(env: Env, mode: str, oid: str, path: str) -> str:
    git(env.repo, "update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}")
    git(env.repo, "commit", "-q", "-m", f"{mode} {path}")
    return rev(env.repo)


def _hash_blob(env: Env, content: bytes) -> str:
    assert _GIT is not None
    return (
        subprocess.run(  # noqa: S603 — argv literal
            [_GIT, "-C", str(env.repo), "hash-object", "-w", "--stdin"],
            input=content,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )


def test_symlink_na_arvore_e_recusado_antes_de_agir(env: Env, no_process: None) -> None:
    """Medido: o Git grava symlink como arquivo comum (Windows, `core.symlinks=false`) ou como
    link. Sem contrato na V1 → recusa explícita, nada criado."""
    base = _commit_index_entry(env, "120000", _hash_blob(env, b"../../fora"), "link")
    out = create(env, base=base)
    assert out.verdict is WorktreeVerdict.UNSUPPORTED_TREE_ENTRY
    assert not env.target.exists() and not branch_exists(env)


def test_gitlink_na_arvore_e_recusado_antes_de_agir(env: Env, no_process: None) -> None:
    base = _commit_index_entry(env, "160000", env.base, "submodulo")
    out = create(env, base=base)
    assert out.verdict is WorktreeVerdict.UNSUPPORTED_TREE_ENTRY
    assert not env.target.exists() and not branch_exists(env)


def _commit_tree(env: Env, names: list[str]) -> str:
    """Commit com uma árvore **montada à mão** (`mktree`): nomes que um checkout recusaria."""
    assert _GIT is not None
    blob = _hash_blob(env, b"x\n")
    listing = "".join(f"100644 blob {blob}\t{name}\n" for name in names).encode()
    made = subprocess.run(  # noqa: S603 — argv literal
        [_GIT, "-C", str(env.repo), "mktree"], input=listing, capture_output=True, check=False
    )
    if made.returncode != 0:
        pytest.skip(f"git mktree recusou a árvore: {made.stderr.decode(errors='replace')}")
    tree = made.stdout.decode().strip()
    return git(env.repo, "commit-tree", "-m", "arvore montada", tree).stdout.strip()


@pytest.mark.parametrize(
    "names",
    [
        ["CON.txt"],
        ["aux"],
        ["arquivo.txt:fluxo"],
        ["ponto-final."],
        ["espaco-final "],
        ["PROGRA~1"],
        ["A.txt", "a.txt"],
        ["barra\\invertida"],
        [".GIT"],
    ],
    ids=[
        "reservado",
        "reservado-sem-ext",
        "ads",
        "ponto",
        "espaco",
        "alias-8.3",
        "caixa",
        "barra-invertida",
        "dot-git",
    ],
)
def test_caminho_inseguro_na_arvore_e_recusado_antes_de_agir(
    env: Env, no_process: None, names: list[str]
) -> None:
    base = _commit_tree(env, names)
    out = create(env, base=base)
    assert out.verdict is WorktreeVerdict.UNSAFE_TREE_PATH
    assert not env.target.exists() and not branch_exists(env)


def _commit_nested(env: Env, paths: list[str]) -> str:
    """Commit com árvore **aninhada** montada à mão (`mktree` por nível): permite grafias de
    diretório que o índice de um Windows case-insensitive nunca produziria."""
    assert _GIT is not None
    blob = _hash_blob(env, b"x\n")

    def make(entries: list[list[str]]) -> str:
        files = sorted(e[0] for e in entries if len(e) == 1)
        subdirs: dict[str, list[list[str]]] = {}
        for e in entries:
            if len(e) > 1:
                subdirs.setdefault(e[0], []).append(e[1:])
        lines = [f"100644 blob {blob}\t{name}\n" for name in files]
        lines += [f"040000 tree {make(rest)}\t{name}\n" for name, rest in sorted(subdirs.items())]
        made = subprocess.run(  # noqa: S603 — argv literal
            [_GIT, "-C", str(env.repo), "mktree"],
            input="".join(lines).encode(),
            capture_output=True,
            check=True,
        )
        return made.stdout.decode().strip()

    tree = make([p.split("/") for p in paths])
    return git(env.repo, "commit-tree", "-m", "arvore aninhada", tree).stdout.strip()


@pytest.mark.parametrize(
    "paths",
    [
        ["Dir/a.txt", "dir/b.txt"],
        ["A/B/a.txt", "a/b/b.txt"],
        ["Dir/a.txt", "dir/sub/b.txt"],
        ["x/Dir/a.txt", "x/dir/b.txt"],
    ],
    ids=["um-nivel", "dois-niveis", "prefixo-com-subdir", "profundo"],
)
def test_reaudit_p2_003_prefixo_com_caixa_conflitante_e_recusado_antes_de_agir(
    env: Env, no_process: None, paths: list[str]
) -> None:
    """Reauditoria P2-003: `Dir` e `dir` são o mesmo diretório no NTFS. A recusa acontece na
    validação da árvore inteira: nenhum `worktree add`, nenhuma branch, nenhum alvo, nenhum
    byte — o escritor nem é construído."""
    base = _commit_nested(env, paths)
    listed = git(env.repo, "ls-tree", "-r", "--name-only", base).stdout.split()
    assert sorted(listed) == sorted(paths)  # a árvore montada é a do cenário

    def forbidden_writer(root: str, identity: object) -> object:
        raise AssertionError("escritor construído numa árvore que deveria ser recusada")

    out = create(env, base=base, tree_writer=forbidden_writer)
    assert out.verdict is WorktreeVerdict.UNSAFE_TREE_PATH
    assert not out.created and out.process_outcome is None
    assert not env.target.exists() and not branch_exists(env)
    assert not (env.repo / ".git" / "worktrees").exists()


def test_prefixo_com_a_mesma_grafia_e_materializado(env: Env) -> None:
    base = _commit_nested(env, ["Foo/a.txt", "Foo/b.txt", "Foo/Bar/c.txt"])
    assert create(env, base=base).created
    assert sorted(p.name for p in (env.target / "Foo").iterdir()) == ["Bar", "a.txt", "b.txt"]


def test_lfs_ponteiro_versionado_vira_o_blob_cru_sem_git_lfs(env: Env) -> None:
    """LFS: o ponteiro commitado é materializado byte a byte; o driver `lfs` — mesmo definido
    só por `includeIf` e marcado como `required` — nunca roda."""
    marker = env.tmp / "lfs.log"
    pointer = (
        b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"0" * 64 + b"\nsize 12345\n"
    )
    (env.repo / ".gitattributes").write_text("*.bin filter=lfs -text\n", encoding="utf-8")
    git(env.repo, "add", ".gitattributes")
    git(
        env.repo,
        "update-index",
        "--add",
        "--cacheinfo",
        f"100644,{_hash_blob(env, pointer)},modelo.bin",
    )
    git(env.repo, "commit", "-q", "-m", "lfs")
    base = rev(env.repo)
    _include_only_in_worktrees(
        env,
        f'[filter "lfs"]\n\tsmudge = echo lfs >> "{posix(marker)}"; cat\n'
        f'\tprocess = echo lfs-process >> "{posix(marker)}"; exit 1\n\trequired = true\n',
    )
    out = create(env, base=base)
    assert out.created
    assert (env.target / "modelo.bin").read_bytes() == pointer
    assert _marks(marker) == []


def test_partial_clone_com_blob_ausente_fecha_sem_fetch(tmp_path: Path) -> None:
    """Blob ausente num partial clone: `--no-lazy-fetch` + `GIT_NO_LAZY_FETCH=1` → recusa antes
    de agir, e o blob continua ausente (nenhum fetch aconteceu)."""
    assert _GIT is not None
    remote = tmp_path / "remoto"
    remote.mkdir()
    git(remote, "init", "-q", "-b", "main")
    (remote / "grande.txt").write_text("conteúdo remoto\n", encoding="utf-8")
    git(remote, "add", "-A")
    git(remote, "commit", "-q", "-m", "r")
    git(remote, "config", "uploadpack.allowFilter", "true")
    git(remote, "config", "uploadpack.allowAnySHA1InWant", "true")
    local = tmp_path / "local"
    subprocess.run(  # noqa: S603 — argv literal
        [_GIT, "clone", "-q", "--no-checkout", "--filter=blob:none", remote.as_uri(), str(local)],
        capture_output=True,
        check=True,
    )
    base = rev(local)
    blob = git(local, "ls-tree", "-r", "HEAD").stdout.split()[2]
    no_lazy = dict(os.environ, GIT_NO_LAZY_FETCH="1")

    def missing() -> bool:
        probe = subprocess.run(  # noqa: S603 — argv literal
            [_GIT, "-C", str(local), "cat-file", "--batch-check"],
            input=f"{blob}\n".encode(),
            capture_output=True,
            env=no_lazy,
            check=True,
        )
        return probe.stdout.decode().strip().endswith("missing")

    assert missing()
    root_dir = tmp_path / "raiz"
    root_dir.mkdir()
    layout = repository_layout(str(local))
    assert layout is not None
    root = prepare_worktree_root(
        root_dir,
        sync_roots=(),
        repository_toplevel=layout.toplevel,
        git_common_dir=layout.git_common_dir,
    )
    out = create_worktree(
        str(local), base_commit=base, task_id=TASK, root=root, tree_writer=CheckedTreeWriter
    )
    assert out.verdict is WorktreeVerdict.UNVERIFIABLE
    assert not out.created
    assert not (Path(root.canonical_path) / "ff-task-abcdef12").exists()
    assert missing(), "nenhum fetch preguiçoso pode ter acontecido"


# ------------------------------------------------------------------ parser (bytes)

_A = "a" * 40
_B = "b" * 40


def test_parser_varios_registros_com_todos_os_atributos() -> None:
    data = (
        f"worktree /r/main repo\0HEAD {_A}\0branch refs/heads/main\0\0"
        f"worktree /w/ff-task-1 ç 名\0HEAD {_B}\0detached\0locked\0\0"
        f"worktree /w/x\0HEAD {_A}\0branch refs/heads/x\0locked motivo\ncom quebra\0"
        "prunable gitdir file points to non-existent location\0\0"
        "worktree /bare.git\0bare\0\0"
    ).encode()
    records = _parse_worktree_porcelain(data)
    assert records is not None and len(records) == 4
    main, detached, locked, bare = records
    assert main.branch_ref == "refs/heads/main" and not main.detached and main.head == _A
    assert detached.path == os.path.normpath("/w/ff-task-1 ç 名")
    assert detached.detached and detached.locked and detached.lock_reason is None
    assert locked.locked and locked.lock_reason == "motivo\ncom quebra"
    assert locked.prunable and locked.prune_reason is not None
    assert bare.bare and bare.head is None and bare.branch_ref is None


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\0",
        f"worktree /a\0HEAD {_A}\0branch refs/heads/m\0".encode(),  # registro sem fechamento
        f"HEAD {_A}\0worktree /a\0\0".encode(),  # primeiro atributo não é worktree
        f"worktree /a\0HEAD {_A}\0HEAD {_A}\0branch refs/heads/m\0\0".encode(),  # repetido
        f"worktree /a\0HEAD {_A}\0branch refs/heads/m\0novidade\0\0".encode(),  # desconhecido
        f"worktree /a\0HEAD {_A}\0\0".encode(),  # nem branch nem detached
        f"worktree /a\0HEAD {_A}\0branch refs/heads/m\0detached\0\0".encode(),  # os dois
        b"worktree /a\0HEAD xyz\0detached\0\0",  # HEAD inválido
        f"worktree relativo\0HEAD {_A}\0detached\0\0".encode(),  # caminho relativo
        b"worktree /a\0HEAD " + _A.encode() + b"\0branch refs/heads/\xff\0\0",  # não UTF-8
        f"worktree /a\0HEAD {_A}\0detached x\0\0".encode(),  # flag com valor
        f"worktree /a\0bare\0HEAD {_A}\0\0".encode(),  # bare com HEAD
        f"worktree /a\0HEAD {_A}\0detached\0\0\0".encode(),  # NUL sobrando
    ],
)
def test_parser_saida_malformada_e_none(data: bytes) -> None:
    assert _parse_worktree_porcelain(data) is None


def test_list_worktrees_real_casa_com_o_parser(env: Env) -> None:
    assert create(env).created
    inventory = list_worktrees(str(env.repo))
    assert inventory is not None
    assert [Path(r.path) for r in inventory.records] == [env.repo.resolve(), env.target]
    assert [entry.name for entry in inventory.admin_entries] == ["ff-task-abcdef12"]
    assert inventory.orphan_admin_entries == ()


def test_inspecao_nunca_altera_o_repositorio(env: Env) -> None:
    assert create(env).created
    before = sorted(
        (p.relative_to(env.repo).as_posix(), p.stat().st_mtime_ns)
        for p in (env.repo / ".git").rglob("*")
        if p.is_file()
    )
    for _ in range(3):
        assert verdict(env) is WorktreeVerdict.REUSABLE
        list_worktrees(str(env.repo))
    after = sorted(
        (p.relative_to(env.repo).as_posix(), p.stat().st_mtime_ns)
        for p in (env.repo / ".git").rglob("*")
        if p.is_file()
    )
    assert after == before


def test_task_diferente_nao_ve_a_worktree_da_outra(env: Env) -> None:
    assert create(env).created
    assert verdict(env, task=OTHER_TASK) is WorktreeVerdict.ABSENT
