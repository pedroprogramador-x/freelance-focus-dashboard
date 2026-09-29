"""E6-CONS5V3-001 — reverificação BLOCKED do Codex
(`docs/audits/e6-cons5v-round-3.md`): falha de E/S durante a inspeção de `.git/objects`
(`_has_commit_or_tag_object`) não era tratada de modo conservador.

* `os.scandir` do diretório principal sem proteção deixava a exceção atravessar `probe_head`
  até a API (500 em vez de `git_unverifiable`).
* `except OSError: continue` num subdiretório ilegível virava silenciosamente "sem objeto
  ali" — se o objeto do commit estivesse exatamente nesse subdiretório, a sondagem concluía
  `no_head` (nunca teve commit) quando na verdade não deu para saber.

Os testes injetam falhas de E/S com `monkeypatch` em `app.git_runtime.os.scandir`, sempre
restritas a um caminho **exato** — todo outro caminho continua delegando ao `os.scandir`
real, então nada além do alvo do teste é afetado.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.git_runtime import PROBE_UNVERIFIABLE, probe_head
from tests import context_helpers
from tests.test_e6_cons4 import _detail, _task, _workspace

_REAL_SCANDIR = os.scandir


def _scandir_failing_at(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    """`os.scandir(target)` levanta `PermissionError`; qualquer outro caminho é real."""
    target_norm = os.path.normcase(str(target))

    def patched(path: str = ".") -> Iterator[os.DirEntry[str]]:
        if os.path.normcase(str(path)) == target_norm:
            raise PermissionError("injetado para teste (E6-CONS5V3-001)")
        return _REAL_SCANDIR(path)

    monkeypatch.setattr("app.git_runtime.os.scandir", patched)


class _FailAfterEntries:
    """Itera `allowed` entradas reais e então levanta — simula falha **no meio** da
    iteração de um `os.scandir`, distinta de uma falha na chamada inicial."""

    def __init__(self, real_iterator: Any, allowed: int) -> None:
        self._real_iterator = real_iterator
        self._allowed = allowed
        self._yielded = 0

    def __iter__(self) -> _FailAfterEntries:
        return self

    def __next__(self) -> os.DirEntry[str]:
        if self._yielded >= self._allowed:
            raise PermissionError("falha de iteração injetada (E6-CONS5V3-001)")
        self._yielded += 1
        return next(self._real_iterator)

    def __enter__(self) -> _FailAfterEntries:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._real_iterator.close()


def _scandir_failing_mid_iteration_at(
    monkeypatch: pytest.MonkeyPatch, target: Path, *, allowed: int
) -> None:
    target_norm = os.path.normcase(str(target))

    def patched(path: str = ".") -> object:
        real_iterator = _REAL_SCANDIR(path)
        if os.path.normcase(str(path)) == target_norm:
            return _FailAfterEntries(real_iterator, allowed)
        return real_iterator

    monkeypatch.setattr("app.git_runtime.os.scandir", patched)


# --------------------------------------------------------------- cenários de montagem


def _repo_sem_commit_com_add(root: Path) -> Path:
    root.mkdir(parents=True)
    context_helpers.git(root, "init")
    context_helpers.git(root, "config", "user.email", "test@example.invalid")
    context_helpers.git(root, "config", "user.name", "Teste")
    context_helpers.write(root, "arquivo.txt", "conteudo\n")
    context_helpers.git(root, "add", "-A")
    return root


def _repo_com_commit_sem_reflog_head_quebrado(root: Path) -> tuple[Path, Path]:
    """Devolve `(root, subdiretorio_do_objeto_do_commit)`.

    O repositório também tem um `blob` e um `tree`, cada um no seu próprio subdiretório de
    dois hex — resolver o SHA do commit via `rev-parse` (antes de quebrar `HEAD`) é
    necessário para não confundir o subdiretório do commit com o de um dos outros dois."""
    root.mkdir(parents=True)
    context_helpers.git(root, "init")
    context_helpers.git(root, "config", "user.email", "test@example.invalid")
    context_helpers.git(root, "config", "user.name", "Teste")
    context_helpers.git(root, "config", "commit.gpgsign", "false")
    context_helpers.git(root, "config", "core.logAllRefUpdates", "false")
    context_helpers.write(root, "README.md", "# projeto\n")
    context_helpers.commit_all(root, "inicial")

    assert context_helpers.GIT is not None
    sha = subprocess.run(  # noqa: S603 — git de montagem de cenário, argv literal, sem shell
        [context_helpers.GIT, "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    commit_subdir = root / ".git" / "objects" / sha[:2]
    assert commit_subdir.is_dir(), "premissa: subdiretorio do objeto do commit existe"

    (root / ".git" / "HEAD").write_text(
        "ref: refs/heads/branch-que-nao-existe-mais\n", encoding="utf-8"
    )
    return root, commit_subdir


# --------------------------------------------------------- Cenário A: scandir do dir principal


def test_probe_head_nao_lanca_quando_scandir_do_diretorio_principal_falha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Antes da correção, `probe_head` propagava o `PermissionError` — a exceção nunca pode
    atravessar a sondagem."""
    root = _repo_sem_commit_com_add(tmp_path / "sem_commit_com_add")
    objects_dir = root / ".git" / "objects"
    _scandir_failing_at(monkeypatch, objects_dir)

    probe = probe_head(str(root))  # não deve lançar
    assert probe.state == PROBE_UNVERIFIABLE


def test_get_e_post_plan_nao_retornam_500_quando_objects_e_ilegivel(
    auth_api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo_sem_commit_com_add(tmp_path / "sem_commit_com_add_api")
    workspace_id = _workspace(auth_api_client, root)
    task_id = _task(auth_api_client, workspace_id)

    objects_dir = root / ".git" / "objects"
    _scandir_failing_at(monkeypatch, objects_dir)

    detail_response = auth_api_client.get(f"/api/tasks/{task_id}")
    assert detail_response.status_code == 200, detail_response.text
    planning = detail_response.json()["planning"]
    assert planning["blockers"] == ["git_unverifiable"]
    assert planning["eligible"] is False

    refused = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert refused.status_code == 409
    assert refused.json()["reason"] == "git_unverifiable"
    body_text = refused.text.lower() + detail_response.text.lower()
    for leak in (str(root).lower(), "fatal:", "traceback", "permissionerror", "injetado"):
        assert leak not in body_text, f"vazou detalhe interno ({leak!r})"


# ---------------------------------------------- Cenário B: subdiretório do objeto do commit


def test_probe_head_unverifiable_quando_subdiretorio_do_objeto_do_commit_e_ilegivel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Achado original do CONS5V3-001: com reflog desativado e `HEAD` quebrado, o único
    sinal de história é o objeto do commit — se o subdiretório dele especificamente não
    puder ser lido, a resposta correta é `unverifiable`, nunca `no_head`."""
    root, commit_subdir = _repo_com_commit_sem_reflog_head_quebrado(tmp_path / "reflog_off")
    _scandir_failing_at(monkeypatch, commit_subdir)

    assert probe_head(str(root)).state == PROBE_UNVERIFIABLE


def test_worktree_commit_com_subdiretorio_ilegivel_nunca_habilita_planejamento(
    auth_api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, commit_subdir = _repo_com_commit_sem_reflog_head_quebrado(tmp_path / "reflog_off_api")
    workspace_id = _workspace(auth_api_client, root)
    task_id = _task(auth_api_client, workspace_id)

    _scandir_failing_at(monkeypatch, commit_subdir)

    planning = _detail(auth_api_client, task_id)["planning"]
    assert planning["blockers"] == ["git_unverifiable"]
    assert planning["eligible"] is False

    refused = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert refused.status_code == 409
    assert refused.json()["reason"] == "git_unverifiable"


# ------------------------------------------------------------- Cenário C: falha na iteração


def test_probe_head_unverifiable_quando_iteracao_do_diretorio_principal_falha_no_meio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Distinto da falha na chamada inicial de `os.scandir`: aqui a chamada **sucede**, e é
    o avanço do iterador (segunda entrada em diante) que levanta."""
    root, _commit_subdir = _repo_com_commit_sem_reflog_head_quebrado(
        tmp_path / "iteracao_principal"
    )
    objects_dir = root / ".git" / "objects"
    _scandir_failing_mid_iteration_at(monkeypatch, objects_dir, allowed=1)

    assert probe_head(str(root)).state == PROBE_UNVERIFIABLE


def test_probe_head_unverifiable_quando_iteracao_de_subdiretorio_falha_no_meio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, commit_subdir = _repo_com_commit_sem_reflog_head_quebrado(tmp_path / "iteracao_sub")
    _scandir_failing_mid_iteration_at(monkeypatch, commit_subdir, allowed=0)

    assert probe_head(str(root)).state == PROBE_UNVERIFIABLE
