"""Gate 1 da E3 — `git_runtime.preflight`, só leitura, nunca lança.

Cobre os quatro cenários exigidos pelo gate: repo limpo, repo com mudança não commitada,
diretório que não é repo e diretório inexistente. Um `git` de verdade é usado para montar
os repositórios de teste — se ele não estiver no `PATH`, a suíte pula em vez de falhar.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.git_runtime import _GIT_ENV_OVERRIDES, GitPreflight, _git_env, preflight

_GIT = shutil.which("git")
_NEEDS_GIT = pytest.mark.skipif(_GIT is None, reason="git indisponível no PATH")


def _git(cwd: Path, *args: str) -> None:
    assert _GIT is not None
    subprocess.run(  # noqa: S603 — git de teste, argv literal, sem shell
        [_GIT, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Repositório limpo com um commit inicial."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Teste")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "arquivo.txt").write_text("conteudo\n", encoding="utf-8")
    _git(root, "add", "arquivo.txt")
    _git(root, "commit", "-m", "commit inicial")
    return root


@_NEEDS_GIT
def test_repo_git_limpo(git_repo: Path) -> None:
    result = preflight(str(git_repo))

    assert result.is_git_repo is True
    assert result.head is not None
    assert len(result.head) == 40
    int(result.head, 16)  # é hexadecimal
    assert result.branch in {"main", "master"}
    assert result.dirty_file_count == 0


@_NEEDS_GIT
def test_repo_com_mudancas_nao_commitadas(git_repo: Path) -> None:
    (git_repo / "arquivo.txt").write_text("conteudo alterado\n", encoding="utf-8")
    (git_repo / "novo.txt").write_text("novo\n", encoding="utf-8")

    result = preflight(str(git_repo))

    assert result.is_git_repo is True
    assert result.head is not None
    assert result.dirty_file_count == 2


@_NEEDS_GIT
def test_repo_sem_nenhum_commit(tmp_path: Path) -> None:
    """Unborn branch: é repo, mas sem HEAD resolvível."""
    root = tmp_path / "vazio"
    root.mkdir()
    _git(root, "init")

    result = preflight(str(root))

    assert result.is_git_repo is True
    assert result.head is None
    assert result.dirty_file_count == 0


def test_diretorio_que_nao_e_repo_git(tmp_path: Path) -> None:
    plain = tmp_path / "sem-git"
    plain.mkdir()
    (plain / "algo.txt").write_text("x\n", encoding="utf-8")

    assert preflight(str(plain)) == GitPreflight(
        is_git_repo=False, head=None, branch=None, dirty_file_count=None
    )


def test_diretorio_inexistente(tmp_path: Path) -> None:
    assert preflight(str(tmp_path / "nao-existe")) == GitPreflight(
        is_git_repo=False, head=None, branch=None, dirty_file_count=None
    )


def test_preflight_nunca_lanca_em_caminho_absurdo(tmp_path: Path) -> None:
    """Byte nulo, caractere de controle e caminho inválido não podem escapar como exceção."""
    for candidate in (
        "\x00",
        "\x00/etc",
        str(tmp_path / "a\x01b"),
        str(tmp_path / "nunca" / "existiu" / "aqui"),
    ):
        result = preflight(candidate)
        assert result.is_git_repo is False
        assert result.head is None


# --------------------------------------------------- E3-AUD-003 / E3-AUD2-003: só leitura


#: Ambiente do processo, poluído com um monte de `GIT_*` (inclusive `GIT_EXEC_PATH` e
#: `GIT_TEMPLATE_DIR`, que já passaram despercebidos numa rodada) mais lixo arbitrário.
_CONTAMINATED_ENVIRON = {
    # dentro da allowlist — devem passar, com valor intacto
    "PATH": "/usr/bin:/bin",
    "HOME": "/home/tester",
    "LANG": "en_US.UTF-8",
    "SYSTEMROOT": r"C:\Windows",
    # GIT_* que NÃO podem passar — nenhum entra na allowlist
    "GIT_DIR": "/evil/.git",
    "GIT_WORK_TREE": "/evil",
    "GIT_INDEX_FILE": "/evil/index",
    "GIT_CONFIG": "/evil/config",
    "GIT_CONFIG_GLOBAL": "/evil/gc",
    "GIT_CONFIG_SYSTEM": "/evil/gs",
    "GIT_EXEC_PATH": "/evil/libexec/git-core",
    "GIT_TEMPLATE_DIR": "/evil/templates",
    "GIT_SSH_COMMAND": "ssh -o ProxyCommand=pwn",
    "GIT_ASKPASS": "/evil/askpass",
    "GIT_PROXY_COMMAND": "/evil/proxy",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/evil/objects",
    "GIT_NAMESPACE": "evil",
    "GIT_TRACE": "1",
    "GIT_PAGER": "/evil/pager",
    "GIT_EDITOR": "/evil/editor",
    "GIT_CEILING_DIRECTORIES": "/",
    "GIT_OPTIONAL_LOCKS": "1",  # herdado tentando reabilitar locks — a override tem de vencer
    "GIT_TERMINAL_PROMPT": "1",
    "GIT_TOTALLY_MADE_UP_VAR": "x",
    # lixo não-GIT fora da allowlist
    "LD_PRELOAD": "/evil/so",
    "RANDOM_JUNK": "x",
}

#: O que `_git_env` DEVE devolver para `_CONTAMINATED_ENVIRON`: só a interseção com a
#: allowlist, mais as duas overrides fixas — nada a mais.
_EXPECTED_GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/home/tester",
    "LANG": "en_US.UTF-8",
    "SYSTEMROOT": r"C:\Windows",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
}


def test_git_env_e_exatamente_a_allowlist() -> None:
    """Igualdade de conjunto, não checagem de nomes conhecidos.

    Passa a `_git_env` um ambiente contaminado com dezenas de `GIT_*` arbitrários
    (incluindo `GIT_EXEC_PATH` e `GIT_TEMPLATE_DIR`, removidos em E3-AUD2-003) e lixo, e
    afirma que o resultado é **exatamente** `_EXPECTED_GIT_ENV`. Qualquer variável a mais —
    nomeada no teste ou não — quebra a asserção; qualquer valor de allowlist alterado
    também; a override `GIT_OPTIONAL_LOCKS` tem de vencer o `"1"` herdado. É o fechamento
    por igualdade que faltava nas rodadas anteriores (que só checavam nomes conhecidos).
    """
    assert _git_env(_CONTAMINATED_ENVIRON) == _EXPECTED_GIT_ENV


@_NEEDS_GIT
def test_preflight_passa_travas_de_somente_leitura(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Toda invocação real do git leva `-c core.fsmonitor=false` e o env de `_git_env()`."""
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        assert isinstance(env, dict)
        calls.append((list(argv), dict(env)))
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="true\n", stderr="")

    monkeypatch.setattr("app.git_runtime.subprocess.run", fake_run)

    preflight(str(git_repo))

    expected_env = _git_env()
    assert calls, "preflight não chamou o git"
    for argv, env in calls:
        assert argv[1:3] == ["-c", "core.fsmonitor=false"], argv
        assert "-C" in argv
        # o env passado ao subprocess é EXATAMENTE o produzido por `_git_env`
        assert env == expected_env
        assert env["GIT_OPTIONAL_LOCKS"] == "0"
        assert not any(key.startswith("GIT_") and key not in _GIT_ENV_OVERRIDES for key in env)


@_NEEDS_GIT
def test_preflight_nao_altera_o_indice_do_git(git_repo: Path) -> None:
    """Mesmo forçando uma condição em que `git status` normalmente refaria o índice."""
    index_path = git_repo / ".git" / "index"
    # bump de mtime num arquivo rastreado → `git status` sem GIT_OPTIONAL_LOCKS=0
    # reescreveria o stat cache do índice.
    tracked = git_repo / "arquivo.txt"
    future = tracked.stat().st_mtime + 10_000
    os.utime(tracked, (future, future))

    before = index_path.read_bytes()
    before_mtime = index_path.stat().st_mtime_ns

    result = preflight(str(git_repo))
    assert result.is_git_repo is True
    assert result.dirty_file_count == 0  # conteúdo não mudou, só o mtime

    assert index_path.read_bytes() == before, "preflight reescreveu .git/index"
    assert index_path.stat().st_mtime_ns == before_mtime


@_NEEDS_GIT
def test_preflight_nao_dispara_fsmonitor_configurado(git_repo: Path, tmp_path: Path) -> None:
    """`-c core.fsmonitor=false` suprime o hook de fsmonitor do repo do usuário."""
    sentinel = tmp_path / "fsmonitor-rodou"
    hook = f"sh -c \"touch '{sentinel.as_posix()}'\""
    _git(git_repo, "config", "core.fsmonitor", hook)

    # sanity: sem a supressão, o hook realmente é invocado por um `git status`
    assert _GIT is not None
    subprocess.run(  # noqa: S603
        [_GIT, "-C", str(git_repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if not sentinel.exists():
        pytest.skip("fsmonitor não dispara neste git; nada a provar")
    sentinel.unlink()

    preflight(str(git_repo))

    assert not sentinel.exists(), "preflight invocou o fsmonitor configurado"
