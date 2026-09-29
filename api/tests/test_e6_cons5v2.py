"""E6-CONS5V2-001/002 — reverificação BLOCKED do Codex
(`docs/audits/e6-cons5v-round-2.md`): `_has_object_evidence` ainda confundia "não deu para
confirmar" com "confirmadamente ausente" em dois casos que a rodada CONS5V não cobria.

* CONS5V2-001 — worktree vinculado cujo commit tem reflogs desativados: os objetos vivem no
  repositório principal (`commondir`), não no Git dir do próprio worktree.
* CONS5V2-002 — `git add` antes do primeiro commit grava objetos (`blob`), mas nenhum deles
  é `commit`/`tag`: não é evidência de história nenhuma.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.git_runtime import PROBE_OK, PROBE_UNVERIFIABLE, PROBE_WITHOUT_HEAD, probe_head
from tests import context_helpers
from tests.test_e6_cons4 import _detail, _task, _workspace
from tests.test_e6_cons5 import _corrupt_file

# ------------------------------------------------- CONS5V2-001: worktree vinculado + commondir


def _repo_com_worktree_sem_reflog(root: Path) -> Path:
    """Repositório principal com commit e reflogs desativados, mais um worktree vinculado
    (`branch`, `.git` em arquivo). Devolve o caminho do **worktree** — é ele que vira
    `DevWorkspace.local_path` no teste de contrato."""
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
    assert (linked / ".git").is_file(), "premissa: worktree vinculado usa .git em arquivo"
    return linked


def test_probe_head_worktree_reflogs_desativados_head_quebrado_e_unverifiable(
    tmp_path: Path,
) -> None:
    """CONS5V2-001: antes da correção, isto respondia `no_head` — `_has_object_evidence`
    olhava só o Git dir do próprio worktree (sem `objects/`, que vive no principal via
    `commondir`), então nunca via o commit que de fato existe."""
    linked = _repo_com_worktree_sem_reflog(tmp_path)

    # premissa: worktree reconhecido normalmente antes da corrupção
    assert probe_head(str(linked)).state == PROBE_OK

    worktree_git_dir = linked.parent / "principal" / ".git" / "worktrees" / "worktree"
    assert (worktree_git_dir / "commondir").exists(), "premissa: commondir existe"
    _corrupt_file(worktree_git_dir / "HEAD", "ref: refs/heads/branch-que-nao-existe-mais\n")
    assert probe_head(str(linked)).state == PROBE_UNVERIFIABLE


def test_worktree_reflogs_desativados_head_quebrado_nunca_habilita_planejamento(
    auth_api_client: TestClient, tmp_path: Path
) -> None:
    """Contrato: elegibilidade e `POST /plan` concordam para o workspace registrado como o
    worktree, e nenhum vazamento de detalhe interno acompanha a resposta pública."""
    linked = _repo_com_worktree_sem_reflog(tmp_path)
    worktree_git_dir = linked.parent / "principal" / ".git" / "worktrees" / "worktree"
    _corrupt_file(worktree_git_dir / "HEAD", "ref: refs/heads/branch-que-nao-existe-mais\n")

    workspace_id = _workspace(auth_api_client, linked)
    task_id = _task(auth_api_client, workspace_id)

    planning = _detail(auth_api_client, task_id)["planning"]
    assert planning["blockers"] == ["git_unverifiable"]
    assert planning["eligible"] is False

    refused = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert refused.status_code == 409
    assert refused.json()["reason"] == "git_unverifiable"
    body_text = refused.text.lower()
    for leak in (str(linked).lower(), "fatal:", "traceback", "config"):
        assert leak not in body_text, f"vazou detalhe interno ({leak!r})"


# ------------------------------------------------- CONS5V2-002: git add sem commit


def _repo_sem_commit_com_add(root: Path) -> Path:
    """`git init` genuíno, sem nenhum commit, com um arquivo `add`ado (só objeto `blob`)."""
    root.mkdir(parents=True)
    context_helpers.git(root, "init")
    context_helpers.git(root, "config", "user.email", "test@example.invalid")
    context_helpers.git(root, "config", "user.name", "Teste")
    context_helpers.write(root, "arquivo.txt", "conteudo\n")
    context_helpers.git(root, "add", "-A")
    return root


def test_probe_head_git_add_sem_commit_continua_no_head(tmp_path: Path) -> None:
    """CONS5V2-002: antes da correção, isto respondia `unverifiable` — o blob gravado pelo
    `add` era contado como "qualquer objeto", e "qualquer objeto" virava evidência de
    commit. Um blob sozinho não é commit: continua `no_head`."""
    root = _repo_sem_commit_com_add(tmp_path / "sem_commit_com_add")
    assert any((root / ".git" / "objects").rglob("*")), "premissa: o blob do add existe"
    assert probe_head(str(root)).state == PROBE_WITHOUT_HEAD


def test_git_add_sem_commit_orienta_primeiro_commit_nao_git_unverifiable(
    auth_api_client: TestClient, tmp_path: Path
) -> None:
    """Contrato: a orientação pública continua "faça o primeiro commit"
    (`repository_without_head`), não "não foi possível consultar o Git"
    (`git_unverifiable`) — o bloqueio de planejamento continua seguro nos dois casos, só o
    motivo muda."""
    root = _repo_sem_commit_com_add(tmp_path / "sem_commit_com_add_api")
    workspace_id = _workspace(auth_api_client, root)
    task_id = _task(auth_api_client, workspace_id)

    planning = _detail(auth_api_client, task_id)["planning"]
    assert planning["blockers"] == ["repository_without_head"]
    assert planning["eligible"] is False

    refused = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert refused.status_code == 409
    assert refused.json()["reason"] == "repository_without_head"
