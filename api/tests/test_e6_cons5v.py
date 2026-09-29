"""E6-CONS5V-001 — reverificação BLOCKED do Codex
(`docs/audits/e6-cons5v-round-1.md`): `probe_head` ainda confundia "não deu para confirmar"
com "confirmadamente ausente" em dois casos que a rodada CONS5 não cobria.

* Caso A — commit válido com reflogs desativados, referência de `HEAD` quebrada depois:
  a ausência de `.git/logs/HEAD` sozinha não prova "nunca teve commit".
* Caso B — workspace em subdiretório de um repositório com commit, `.git/config` do
  diretório **pai** corrompido: a ausência de `.git` só em `local_path` não prova "não é
  repositório" quando o git reconheceria o workspace via busca ancestral.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.git_runtime import PROBE_OK, PROBE_UNVERIFIABLE, probe_head
from tests import context_helpers
from tests.test_e6_cons4 import _detail, _repo, _task, _workspace
from tests.test_e6_cons5 import _corrupt_file

# ------------------------------------------------- Caso A: reflogs desativados + HEAD quebrado


def _repo_sem_reflog(root: Path) -> Path:
    """Repositório com commit válido e `core.logAllRefUpdates=false` — nenhum
    `.git/logs/HEAD` é criado, mesmo depois do commit."""
    root.mkdir(parents=True)
    context_helpers.git(root, "init")
    context_helpers.git(root, "config", "user.email", "test@example.invalid")
    context_helpers.git(root, "config", "user.name", "Teste")
    context_helpers.git(root, "config", "commit.gpgsign", "false")
    context_helpers.git(root, "config", "core.logAllRefUpdates", "false")
    context_helpers.write(root, "README.md", "# projeto\n")
    context_helpers.commit_all(root, "inicial")
    return root


def test_probe_head_reflogs_desativados_com_head_quebrado_e_unverifiable(tmp_path: Path) -> None:
    """Caso A do CONS5V-001: antes da correção, isto respondia `no_head` — idêntico à
    resposta de um repositório que nunca teve commit algum. Reflog e objeto são efeitos
    independentes de um commit; desligar o reflog não impede o objeto de ser gravado."""
    root = _repo_sem_reflog(tmp_path / "reflogs_desativados")
    assert not (root / ".git" / "logs" / "HEAD").exists(), "premissa: reflogs desativados"
    assert any((root / ".git" / "objects").rglob("*")), "premissa: objetos do commit existem"

    _corrupt_file(root / ".git" / "HEAD", "ref: refs/heads/branch-que-nao-existe-mais\n")
    assert probe_head(str(root)).state == PROBE_UNVERIFIABLE


def test_reflogs_desativados_head_quebrado_nunca_habilita_planejamento(
    auth_api_client: TestClient, tmp_path: Path
) -> None:
    """Contrato: elegibilidade e `POST /plan` concordam, e nenhum vazamento de detalhe
    interno acompanha a resposta pública."""
    root = _repo_sem_reflog(tmp_path / "reflogs_desativados_api")
    _corrupt_file(root / ".git" / "HEAD", "ref: refs/heads/branch-que-nao-existe-mais\n")

    workspace_id = _workspace(auth_api_client, root)
    task_id = _task(auth_api_client, workspace_id)

    planning = _detail(auth_api_client, task_id)["planning"]
    assert planning["blockers"] == ["git_unverifiable"]
    assert planning["eligible"] is False

    refused = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert refused.status_code == 409
    assert refused.json()["reason"] == "git_unverifiable"
    body_text = refused.text.lower()
    for leak in (str(root).lower(), "fatal:", "traceback", "config"):
        assert leak not in body_text, f"vazou detalhe interno ({leak!r})"


# --------------------------------------------- Caso B: subdiretório + config ancestral quebrada


def _repo_com_subdiretorio(root: Path) -> Path:
    """Repositório com commit, contendo um workspace em `sub/ws`. Devolve o caminho do
    repositório — quem chama monta o `Path` do workspace a partir dele."""
    repo = _repo(root)
    context_helpers.write(repo, "sub/ws/arquivo.txt", "conteudo\n")
    context_helpers.commit_all(repo, "adiciona workspace")
    return repo


def test_probe_head_subdiretorio_com_config_ancestral_corrompida_e_unverifiable(
    tmp_path: Path,
) -> None:
    """Caso B do CONS5V-001: antes da correção, a busca por `.git` não subia ao diretório
    pai, e a resposta era `not_a_repo` — idêntico a um diretório que nunca foi repositório —
    embora o git reconhecesse o workspace antes da corrupção."""
    repo = _repo_com_subdiretorio(tmp_path / "repo_pai")
    workspace = repo / "sub" / "ws"

    # premissa: o git (e o probe_head não corrigido) reconhece o subdiretório como parte do
    # repositório antes da corrupção.
    assert probe_head(str(workspace)).state == PROBE_OK

    _corrupt_file(repo / ".git" / "config", "isto nao e um ini valido [[[ lixo\n")
    assert probe_head(str(workspace)).state == PROBE_UNVERIFIABLE


def test_subdiretorio_config_ancestral_corrompida_nunca_habilita_planejamento(
    auth_api_client: TestClient, tmp_path: Path
) -> None:
    """Contrato: elegibilidade e `POST /plan` concordam para o workspace registrado como o
    subdiretório, e nenhum vazamento de detalhe interno acompanha a resposta pública."""
    repo = _repo_com_subdiretorio(tmp_path / "repo_pai_api")
    workspace = repo / "sub" / "ws"
    _corrupt_file(repo / ".git" / "config", "isto nao e um ini valido [[[ lixo\n")

    workspace_id = _workspace(auth_api_client, workspace)
    task_id = _task(auth_api_client, workspace_id)

    planning = _detail(auth_api_client, task_id)["planning"]
    assert planning["blockers"] == ["git_unverifiable"]
    assert planning["eligible"] is False

    refused = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert refused.status_code == 409
    assert refused.json()["reason"] == "git_unverifiable"
    body_text = refused.text.lower()
    for leak in (str(repo).lower(), "fatal:", "traceback", "config"):
        assert leak not in body_text, f"vazou detalhe interno ({leak!r})"


# ------------------------------------------------------------------- não regride: worktrees


def test_probe_head_worktree_vinculado_continua_ok(tmp_path: Path) -> None:
    """Não regride (E6-CONS4): um worktree vinculado, com `.git` em **arquivo** apontando
    para `gitdir:` no repositório principal, continua `ok` pelo caminho feliz."""
    main = _repo(tmp_path / "principal")
    linked = tmp_path / "worktree-vinculado"
    context_helpers.git(main, "worktree", "add", str(linked), "-b", "feature")
    assert (linked / ".git").is_file(), "premissa: worktree vinculado usa .git em arquivo"

    probe = probe_head(str(linked))
    assert probe.state == PROBE_OK
    assert probe.head is not None


def test_probe_head_worktree_com_principal_corrompido_e_unverifiable(tmp_path: Path) -> None:
    """`_resolve_git_dir` segue o `gitdir:` do worktree até o repositório principal — se o
    principal está corrompido, a resposta é `unverifiable`, nunca `not_a_repo` nem `ok`."""
    main = _repo(tmp_path / "principal2")
    linked = tmp_path / "worktree-vinculado2"
    context_helpers.git(main, "worktree", "add", str(linked), "-b", "feature2")

    _corrupt_file(main / ".git" / "config", "isto nao e um ini valido [[[ lixo\n")
    assert probe_head(str(linked)).state == PROBE_UNVERIFIABLE
