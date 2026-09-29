"""E6-CONS5 — os três findings da auditoria consolidada rodada 5
(`docs/audits/e6-consolidated-round-5.md`): classificação de falhas Git, conflito
concorrente na UI e diagnóstico falso de mudança de `HEAD`.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.git_runtime import (
    PROBE_NOT_A_REPO,
    PROBE_OK,
    PROBE_UNVERIFIABLE,
    PROBE_WITHOUT_HEAD,
    probe_head,
)
from tests.test_e6_cons4 import _detail, _repo, _task, _workspace

# ------------------------------------------------------- E6-CONS5-001: classificação Git


def _corrupt_file(path: Path, content: str) -> None:
    """Sobrescreve um arquivo interno do `.git`, inclusive quando o git o marcou read-only."""
    if path.exists():
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    path.write_text(content, encoding="utf-8")


def test_probe_head_confirma_diretorio_sem_git(tmp_path: Path) -> None:
    """A. Diretório comum, nunca foi repositório: `not_a_repo`, sem ambiguidade."""
    root = tmp_path / "sem_git"
    root.mkdir()
    assert probe_head(str(root)).state == PROBE_NOT_A_REPO


def test_probe_head_confirma_repositorio_sem_primeiro_commit(tmp_path: Path) -> None:
    """B. `git init`, nenhum commit ainda: `no_head`, legitimamente."""
    root = _repo(tmp_path / "sem_commit", commit=False)
    assert probe_head(str(root)).state == PROBE_WITHOUT_HEAD


def test_probe_head_git_config_corrompido_e_unverifiable_nao_not_a_repo(tmp_path: Path) -> None:
    """C. Repositório válido com commit, `.git/config` corrompido.

    Finding E6-CONS5-001: antes da correção isto respondia `not_a_repo` — o git falha ao
    rodar `rev-parse --is-inside-work-tree` com config ilegível exatamente como falha para
    um diretório que nunca foi repositório (mesmo código de saída, stdout vazio). O disco
    sabe a diferença mesmo quando o git não consegue dizer: há um `.git` de verdade aqui.
    """
    root = _repo(tmp_path / "config_corrompido")
    _corrupt_file(root / ".git" / "config", "isto nao e um ini valido [[[ lixo\n")
    assert probe_head(str(root)).state == PROBE_UNVERIFIABLE


def test_probe_head_head_corrompido_e_unverifiable_nao_not_a_repo(tmp_path: Path) -> None:
    """C-bis. Repositório válido com commit, `.git/HEAD` com conteúdo inválido.

    Mesma classe de ambiguidade que a config corrompida: com `HEAD` ilegível, até
    `--is-inside-work-tree` falha (o git não reconhece o diretório como repositório nenhum),
    então o retorno é idêntico ao de "nunca foi repositório". `_has_git_marker` resolve pela
    presença do `.git` no disco, não pelo texto do erro.
    """
    root = _repo(tmp_path / "head_conteudo_invalido")
    _corrupt_file(root / ".git" / "HEAD", "isto nao e uma ref nem um sha\n")
    assert probe_head(str(root)).state == PROBE_UNVERIFIABLE


def test_probe_head_referencia_de_head_corrompida_apos_commit_e_unverifiable(
    tmp_path: Path,
) -> None:
    """D. Repositório válido, HEAD apontava para um branch que existia; a referência some.

    Finding E6-CONS5-001: antes da correção isto respondia `no_head` — a mesma resposta de
    "nunca teve commit" (B). Mas este repositório TEVE um commit; a diferença está no
    reflog de HEAD, que só é criado na primeira atualização de referência e continua no
    disco mesmo depois que o branch é apagado ou renomeado por fora do git_runtime.
    """
    root = _repo(tmp_path / "head_referencia_corrompida")
    assert (root / ".git" / "logs" / "HEAD").exists(), "premissa: reflog existe após commit"
    _corrupt_file(root / ".git" / "HEAD", "ref: refs/heads/branch-que-nao-existe-mais\n")
    assert probe_head(str(root)).state == PROBE_UNVERIFIABLE


def test_probe_head_diretorio_removido_continua_not_a_repo(tmp_path: Path) -> None:
    """Não regride o comportamento documentado: diretório removido é persistente, não
    ambíguo, e continua `not_a_repo` (não `unverifiable`)."""
    root = _repo(tmp_path / "sera_removido")
    root.rename(root.with_name(root.name + "-removido"))
    assert probe_head(str(root)).state == PROBE_NOT_A_REPO


def test_probe_head_git_indisponivel_continua_unverifiable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Não regride o comportamento documentado: `git` fora do `PATH` continua `unverifiable`."""
    root = _repo(tmp_path / "git_ausente")
    monkeypatch.setattr("app.git_runtime.shutil.which", lambda _name: None)
    assert probe_head(str(root)).state == PROBE_UNVERIFIABLE


def test_probe_head_commit_valido_continua_ok(tmp_path: Path) -> None:
    """Não regride o caminho feliz: repositório íntegro com commit continua `ok`."""
    root = _repo(tmp_path / "integro")
    probe = probe_head(str(root))
    assert probe.state == PROBE_OK
    assert probe.head is not None


# A resposta pública nunca expõe caminho, stderr nem detalhe de corrupção (E6-CONS5-001 §6).
def test_probe_head_unverifiable_nao_carrega_detalhe_interno(tmp_path: Path) -> None:
    root = _repo(tmp_path / "sem_vazamento")
    _corrupt_file(root / ".git" / "config", "isto nao e um ini valido [[[ lixo\n")
    probe = probe_head(str(root))
    assert probe.state == PROBE_UNVERIFIABLE
    assert probe.head is None
    assert probe.branch is None


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _repo(tmp_path / "ws-cons5")


@pytest.fixture
def workspace_id(auth_api_client: TestClient, repo: Path) -> str:
    return _workspace(auth_api_client, repo)


@pytest.fixture
def task_id(auth_api_client: TestClient, workspace_id: str) -> str:
    return _task(auth_api_client, workspace_id)


@pytest.mark.parametrize(
    ("rotulo", "corrupt"),
    [
        (
            "config-corrompido",
            lambda root: _corrupt_file(
                root / ".git" / "config", "isto nao e um ini valido [[[ lixo\n"
            ),
        ),
        (
            "head-conteudo-invalido",
            lambda root: _corrupt_file(root / ".git" / "HEAD", "nao e ref nem sha\n"),
        ),
        (
            "head-referencia-corrompida-apos-commit",
            lambda root: _corrupt_file(root / ".git" / "HEAD", "ref: refs/heads/inexistente\n"),
        ),
    ],
)
def test_git_corrompido_nunca_habilita_planejamento_e_reason_bate_no_post_plan(
    auth_api_client: TestClient,
    task_id: str,
    repo: Path,
    rotulo: str,
    corrupt: object,
) -> None:
    """Contrato: o classificador de elegibilidade e `POST /plan` continuam a mesma fonte —
    e nenhuma forma de corrupção Git é lida como uma pré-condição satisfeita."""
    corrupt(repo)  # type: ignore[operator]

    planning = _detail(auth_api_client, task_id)["planning"]
    assert planning["blockers"] == ["git_unverifiable"], rotulo
    assert planning["eligible"] is False, rotulo

    refused = auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    assert refused.status_code == 409, rotulo
    assert refused.json()["reason"] == "git_unverifiable", rotulo
    body_text = refused.text.lower()
    for leak in (str(repo).lower(), "fatal:", "traceback", "config"):
        assert leak not in body_text, f"{rotulo}: vazou detalhe interno ({leak!r})"


# ------------------------------------------------------- E6-CONS5-003: diagnóstico de HEAD


def _force_needs_fix_sem_manifesto(
    session_factory: sessionmaker[Session], workspace_id: str, task_id: str
) -> None:
    """Simula, via banco, o estado que a auditoria só alcançou por escrita direta: HEAD
    nunca mudou, mas `needs_fix` ficou sem `approved_manifest_id` (E6-CONS5-003)."""
    from app.db.enums import TaskStatus
    from app.db.models import WorkspaceTask

    with session_factory() as session:
        task = session.get(WorkspaceTask, task_id)
        assert task is not None
        task.status = TaskStatus.NEEDS_FIX
        task.approved_manifest_id = None
        task.approved_at = None
        session.commit()


def test_needs_fix_sem_manifesto_nao_afirma_mudanca_de_head(
    auth_api_client: TestClient,
    workspace_id: str,
    task_id: str,
    session_factory: sessionmaker[Session],
) -> None:
    """A API por si só já não afirma nada sobre `HEAD`: o contrato é `transition: allowed` +
    `approval_state: requires_replan` + `plan_standing: historical`, sem nenhum campo
    dizendo "o HEAD mudou". A garantia de UI correspondente está em
    `src/test/task-detail-ui.test.tsx::needs_fix com requires_replan não afirma mudança de HEAD`.
    """
    auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})
    _force_needs_fix_sem_manifesto(session_factory, workspace_id, task_id)

    detail = _detail(auth_api_client, task_id)
    assert detail["approval_state"] == "requires_replan"
    assert detail["planning"]["transition"] == "allowed"
    assert detail["plan_standing"] == "historical"
