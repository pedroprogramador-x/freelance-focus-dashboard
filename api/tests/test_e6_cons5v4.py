"""E6-CONS5V4-001/002 — reverificação BLOCKED do Codex
(`docs/audits/e6-cons5v-round-4.md`): duas checagens de existência (`os.path.isdir`) e uma
leitura (`commondir`) ainda engoliam `PermissionError` como se fosse "não existe" /
"não deu para seguir", produzindo `no_head` em vez de `unverifiable`.

* CONS5V4-001 — `os.path.isdir(objects_dir)` e `os.path.isdir(pack_dir)` (as checagens de
  existência de `_has_object_evidence`/`_has_commit_or_tag_object`, não o `scandir` já
  corrigido em CONS5V3) engolem qualquer `OSError`, inclusive `PermissionError` — o mesmo
  retorno de "não existe".
* CONS5V4-002 — `_common_git_dir` caía de volta no Git dir do próprio worktree quando a
  leitura de `commondir` falhava por qualquer motivo, perdendo o armazenamento comum.

Os testes injetam falhas com `monkeypatch` em `app.git_runtime.os.stat` e
`app.git_runtime.open`, sempre restritas a um caminho **exato** — todo outro caminho
continua delegando à função real, então nada além do alvo do teste é afetado.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.git_runtime import PROBE_UNVERIFIABLE, probe_head
from tests import context_helpers
from tests.test_e6_cons4 import _detail, _task, _workspace

_REAL_STAT = os.stat
_REAL_OPEN = open


def _stat_failing_at(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    """`os.stat(target)` levanta `PermissionError`; qualquer outro caminho é real."""
    target_norm = os.path.normcase(str(target))

    def patched(path: str, *args: Any, **kwargs: Any) -> os.stat_result:
        if os.path.normcase(str(path)) == target_norm:
            raise PermissionError("injetado para teste (E6-CONS5V4-001)")
        return _REAL_STAT(path, *args, **kwargs)

    monkeypatch.setattr("app.git_runtime.os.stat", patched)


def _open_failing_at(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    """`open(target, ...)` levanta `PermissionError`; qualquer outro caminho é real."""
    target_norm = os.path.normcase(str(target))

    def patched(path: str, *args: Any, **kwargs: Any) -> Any:
        if os.path.normcase(str(path)) == target_norm:
            raise PermissionError("injetado para teste (E6-CONS5V4-002)")
        return _REAL_OPEN(path, *args, **kwargs)

    monkeypatch.setattr("app.git_runtime.open", patched, raising=False)


# --------------------------------------------------------------- cenários de montagem


def _repo_com_commit_sem_reflog_head_quebrado(root: Path, *, gc: bool = False) -> Path:
    root.mkdir(parents=True)
    context_helpers.git(root, "init")
    context_helpers.git(root, "config", "user.email", "test@example.invalid")
    context_helpers.git(root, "config", "user.name", "Teste")
    context_helpers.git(root, "config", "commit.gpgsign", "false")
    context_helpers.git(root, "config", "core.logAllRefUpdates", "false")
    context_helpers.write(root, "README.md", "# projeto\n")
    context_helpers.commit_all(root, "inicial")
    if gc:
        context_helpers.git(root, "gc", "--no-prune")
    (root / ".git" / "HEAD").write_text(
        "ref: refs/heads/branch-que-nao-existe-mais\n", encoding="utf-8"
    )
    return root


def _repo_com_worktree_sem_reflog_head_quebrado(root: Path) -> tuple[Path, Path]:
    """Devolve `(worktree, commondir_file)`."""
    main = root / "principal"
    main.mkdir(parents=True)
    context_helpers.git(main, "init")
    context_helpers.git(main, "config", "user.email", "test@example.invalid")
    context_helpers.git(main, "config", "user.name", "Teste")
    context_helpers.git(main, "config", "commit.gpgsign", "false")
    context_helpers.git(main, "config", "core.logAllRefUpdates", "false")
    context_helpers.write(main, "README.md", "# projeto\n")
    context_helpers.commit_all(main, "inicial")

    linked = root / "worktree"
    context_helpers.git(main, "worktree", "add", "-q", str(linked), "-b", "feature")
    worktree_git_dir = main / ".git" / "worktrees" / "worktree"
    commondir_file = worktree_git_dir / "commondir"
    assert commondir_file.is_file(), "premissa: commondir existe"

    (worktree_git_dir / "HEAD").write_text(
        "ref: refs/heads/branch-que-nao-existe-mais\n", encoding="utf-8"
    )
    return linked, commondir_file


# ------------------------------------------------------------- CONS5V4-001: isdir(objects_dir)


def test_probe_head_unverifiable_quando_stat_de_objects_falha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Antes da correção, `os.path.isdir(objects_dir)` engolia o `PermissionError` e
    devolvia `False` — indistinguível de "objects/ não existe"."""
    root = _repo_com_commit_sem_reflog_head_quebrado(tmp_path / "objects_ilegivel")
    objects_dir = root / ".git" / "objects"
    _stat_failing_at(monkeypatch, objects_dir)

    assert probe_head(str(root)).state == PROBE_UNVERIFIABLE


def test_get_e_post_plan_nao_500_quando_stat_de_objects_falha(
    auth_api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo_com_commit_sem_reflog_head_quebrado(tmp_path / "objects_ilegivel_api")
    workspace_id = _workspace(auth_api_client, root)
    task_id = _task(auth_api_client, workspace_id)

    objects_dir = root / ".git" / "objects"
    _stat_failing_at(monkeypatch, objects_dir)

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


# --------------------------------------------------------- CONS5V4-001: isdir(objects/pack)


def test_probe_head_unverifiable_quando_stat_de_objects_pack_falha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repositório compactado (`git gc`): os objetos soltos viram um pacote, e é
    `objects/pack` — não `objects/` — que precisa existir para a evidência ser vista."""
    root = _repo_com_commit_sem_reflog_head_quebrado(tmp_path / "pack_ilegivel", gc=True)
    pack_dir = root / ".git" / "objects" / "pack"
    assert any(pack_dir.iterdir()), "premissa: repositório compactado tem pacote"
    _stat_failing_at(monkeypatch, pack_dir)

    assert probe_head(str(root)).state == PROBE_UNVERIFIABLE


def test_get_e_post_plan_nao_500_quando_stat_de_objects_pack_falha(
    auth_api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo_com_commit_sem_reflog_head_quebrado(tmp_path / "pack_ilegivel_api", gc=True)
    workspace_id = _workspace(auth_api_client, root)
    task_id = _task(auth_api_client, workspace_id)

    pack_dir = root / ".git" / "objects" / "pack"
    _stat_failing_at(monkeypatch, pack_dir)

    planning = _detail(auth_api_client, task_id)["planning"]
    assert planning["blockers"] == ["git_unverifiable"]
    assert planning["eligible"] is False

    refused = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert refused.status_code == 409
    assert refused.json()["reason"] == "git_unverifiable"


# ------------------------------------------------------------------ CONS5V4-002: commondir


def test_probe_head_unverifiable_quando_leitura_de_commondir_falha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Antes da correção, uma falha ao ler `commondir` caía de volta no Git dir do próprio
    worktree — perdendo o armazenamento comum onde o commit realmente está."""
    linked, commondir_file = _repo_com_worktree_sem_reflog_head_quebrado(
        tmp_path / "commondir_ilegivel"
    )
    _open_failing_at(monkeypatch, commondir_file)

    assert probe_head(str(linked)).state == PROBE_UNVERIFIABLE


def test_get_e_post_plan_nao_500_quando_leitura_de_commondir_falha(
    auth_api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked, commondir_file = _repo_com_worktree_sem_reflog_head_quebrado(
        tmp_path / "commondir_ilegivel_api"
    )
    workspace_id = _workspace(auth_api_client, linked)
    task_id = _task(auth_api_client, workspace_id)

    _open_failing_at(monkeypatch, commondir_file)

    detail_response = auth_api_client.get(f"/api/tasks/{task_id}")
    assert detail_response.status_code == 200, detail_response.text
    planning = detail_response.json()["planning"]
    assert planning["blockers"] == ["git_unverifiable"]
    assert planning["eligible"] is False

    refused = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert refused.status_code == 409
    assert refused.json()["reason"] == "git_unverifiable"
    body_text = refused.text.lower() + detail_response.text.lower()
    for leak in (str(linked).lower(), "fatal:", "traceback", "permissionerror", "injetado"):
        assert leak not in body_text, f"vazou detalhe interno ({leak!r})"


# ------------------------------------------------------------ bônus: mesma regra, logs/HEAD


def test_probe_head_unverifiable_quando_stat_de_logs_head_falha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_has_confirmed_history` também trocou `os.path.exists` por `_stat_or_raise` para a
    checagem de `logs/HEAD` — mesma regra única, mesmo comportamento sob falha de E/S."""
    root = _repo_com_commit_sem_reflog_head_quebrado(tmp_path / "logs_head_ilegivel")
    logs_head = root / ".git" / "logs" / "HEAD"
    _stat_failing_at(monkeypatch, logs_head)

    assert probe_head(str(root)).state == PROBE_UNVERIFIABLE
