"""Gate 3 da E3 — integração do router `/api/workspaces` via `TestClient`.

Fluxos felizes e de erro de cada rota, mais a confirmação de que a rota nova herdou a
proteção por `LocalSessionToken` do middleware existente (nada de auth reimplementada).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_GIT = shutil.which("git")


def _payload(directory: Path, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": "Meu Workspace",
        "type": "freelance",
        "local_path": str(directory),
    }
    body.update(overrides)
    return body


def _make_dir(tmp_path: Path, name: str = "ws") -> Path:
    directory = tmp_path / name
    directory.mkdir()
    return directory


# ---------------------------------------------------- autenticação herdada


def test_todas_as_rotas_exigem_bearer_token(api_client: TestClient, tmp_path: Path) -> None:
    """Sem `Authorization: Bearer`, o middleware de `app.main` recusa antes do router."""
    directory = _make_dir(tmp_path)
    assert api_client.get("/api/workspaces").status_code == 401
    assert api_client.post("/api/workspaces", json=_payload(directory)).status_code == 401
    assert api_client.get("/api/workspaces/qualquer").status_code == 401
    assert (
        api_client.patch("/api/workspaces/qualquer", json={"status": "archived"}).status_code == 401
    )
    assert api_client.get("/api/workspaces/qualquer/git").status_code == 401


def test_token_invalido_tambem_e_401(api_client: TestClient) -> None:
    resposta = api_client.get("/api/workspaces", headers={"Authorization": "Bearer nao-e-o-token"})
    assert resposta.status_code == 401


# --------------------------------------------------------------- POST + GET


def test_cria_workspace_e_aparece_na_listagem(auth_api_client: TestClient, tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)

    criado = auth_api_client.post("/api/workspaces", json=_payload(directory))
    assert criado.status_code == 201, criado.text
    corpo = criado.json()
    assert corpo["id"]
    assert corpo["type"] == "freelance"
    assert corpo["status"] == "active"
    assert corpo["linked_project_id"] is None
    assert corpo["repository_url"] is None
    assert Path(corpo["local_path"]).samefile(directory)
    assert set(corpo) == {
        "id",
        "name",
        "type",
        "local_path",
        "linked_project_id",
        "repository_url",
        "default_branch",
        "status",
        "created_at",
        "updated_at",
    }

    listagem = auth_api_client.get("/api/workspaces")
    assert listagem.status_code == 200
    assert [item["id"] for item in listagem.json()] == [corpo["id"]]


def test_cria_os_cinco_tipos_inclusive_sem_linked_project_id(
    auth_api_client: TestClient, tmp_path: Path
) -> None:
    for tipo in ("personal", "freelance", "study", "experiment", "open_source"):
        directory = _make_dir(tmp_path, tipo)
        resposta = auth_api_client.post(
            "/api/workspaces", json=_payload(directory, type=tipo, name=f"ws {tipo}")
        )
        assert resposta.status_code == 201, resposta.text
        assert resposta.json()["type"] == tipo
        assert resposta.json()["linked_project_id"] is None


def test_filtra_listagem_por_status(auth_api_client: TestClient, tmp_path: Path) -> None:
    ids: list[str] = []
    for indice in range(3):
        directory = _make_dir(tmp_path, f"w{indice}")
        ids.append(auth_api_client.post("/api/workspaces", json=_payload(directory)).json()["id"])
    auth_api_client.patch(f"/api/workspaces/{ids[0]}", json={"status": "archived"})

    ativos = auth_api_client.get("/api/workspaces", params={"status": "active"})
    assert {item["id"] for item in ativos.json()} == {ids[1], ids[2]}
    arquivados = auth_api_client.get("/api/workspaces", params={"status": "archived"})
    assert [item["id"] for item in arquivados.json()] == [ids[0]]


def test_cria_rejeita_campo_desconhecido(auth_api_client: TestClient, tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    resposta = auth_api_client.post("/api/workspaces", json=_payload(directory, campo_extra="x"))
    assert resposta.status_code == 422  # extra="forbid" em todos os schemas


def test_cria_rejeita_nome_em_branco(auth_api_client: TestClient, tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    resposta = auth_api_client.post("/api/workspaces", json=_payload(directory, name="   "))
    assert resposta.status_code == 422


def test_cria_rejeita_nome_acima_de_120(auth_api_client: TestClient, tmp_path: Path) -> None:
    """E3-AUD-007: limite ≤ 120 também é recusado na borda HTTP."""
    directory = _make_dir(tmp_path)
    resposta = auth_api_client.post("/api/workspaces", json=_payload(directory, name="n" * 121))
    assert resposta.status_code == 422


def test_cria_local_path_inexistente_e_422(auth_api_client: TestClient, tmp_path: Path) -> None:
    resposta = auth_api_client.post("/api/workspaces", json=_payload(tmp_path / "nao-existe"))
    assert resposta.status_code == 422
    assert resposta.json()["code"] == "invalid_local_path"


def test_cria_local_path_nao_diretorio_e_422(auth_api_client: TestClient, tmp_path: Path) -> None:
    arquivo = tmp_path / "arquivo.txt"
    arquivo.write_text("x\n", encoding="utf-8")
    resposta = auth_api_client.post("/api/workspaces", json=_payload(arquivo))
    assert resposta.status_code == 422
    assert resposta.json()["code"] == "invalid_local_path"


def test_cria_local_path_duplicado_e_409(auth_api_client: TestClient, tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    assert auth_api_client.post("/api/workspaces", json=_payload(directory)).status_code == 201
    resposta = auth_api_client.post("/api/workspaces", json=_payload(directory, name="outro nome"))
    assert resposta.status_code == 409
    assert resposta.json()["code"] == "duplicate_local_path"


def test_detalhe_existente_e_inexistente(auth_api_client: TestClient, tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    criado = auth_api_client.post("/api/workspaces", json=_payload(directory)).json()

    ok = auth_api_client.get(f"/api/workspaces/{criado['id']}")
    assert ok.status_code == 200
    assert ok.json()["id"] == criado["id"]

    faltando = auth_api_client.get("/api/workspaces/nao-existe")
    assert faltando.status_code == 404
    assert faltando.json()["code"] == "workspace_not_found"


# --------------------------------------------------------------------- PATCH


def test_patch_arquiva_e_reativa(auth_api_client: TestClient, tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    workspace_id = auth_api_client.post("/api/workspaces", json=_payload(directory)).json()["id"]

    arquivar = auth_api_client.patch(f"/api/workspaces/{workspace_id}", json={"status": "archived"})
    assert arquivar.status_code == 200
    assert arquivar.json()["status"] == "archived"

    reativar = auth_api_client.patch(f"/api/workspaces/{workspace_id}", json={"status": "active"})
    assert reativar.status_code == 200
    assert reativar.json()["status"] == "active"


def test_patch_para_o_mesmo_status_e_409(auth_api_client: TestClient, tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    workspace_id = auth_api_client.post("/api/workspaces", json=_payload(directory)).json()["id"]

    resposta = auth_api_client.patch(f"/api/workspaces/{workspace_id}", json={"status": "active"})
    assert resposta.status_code == 409
    assert resposta.json()["code"] == "invalid_status_transition"


def test_patch_status_fora_do_enum_e_422(auth_api_client: TestClient, tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    workspace_id = auth_api_client.post("/api/workspaces", json=_payload(directory)).json()["id"]

    assert (
        auth_api_client.patch(
            f"/api/workspaces/{workspace_id}", json={"status": "deleted"}
        ).status_code
        == 422
    )
    assert (
        auth_api_client.patch(
            f"/api/workspaces/{workspace_id}", json={"status": "active", "extra": 1}
        ).status_code
        == 422
    )


def test_patch_workspace_inexistente_e_404(auth_api_client: TestClient) -> None:
    resposta = auth_api_client.patch("/api/workspaces/nao-existe", json={"status": "archived"})
    assert resposta.status_code == 404


# ----------------------------------------------------------- GET {id}/git


def test_git_preflight_diretorio_sem_git(auth_api_client: TestClient, tmp_path: Path) -> None:
    directory = _make_dir(tmp_path, "sem-git")
    workspace_id = auth_api_client.post("/api/workspaces", json=_payload(directory)).json()["id"]

    resposta = auth_api_client.get(f"/api/workspaces/{workspace_id}/git")
    assert resposta.status_code == 200
    assert resposta.json() == {
        "is_git_repo": False,
        "head": None,
        "branch": None,
        "dirty_file_count": None,
    }


def test_git_preflight_workspace_inexistente_e_404(auth_api_client: TestClient) -> None:
    assert auth_api_client.get("/api/workspaces/nao-existe/git").status_code == 404


@pytest.mark.skipif(_GIT is None, reason="git indisponível no PATH")
def test_git_preflight_repo_real_com_mudanca(auth_api_client: TestClient, tmp_path: Path) -> None:
    directory = _make_dir(tmp_path, "repo")

    def run(*args: str) -> None:
        assert _GIT is not None
        subprocess.run(  # noqa: S603 — git de teste, argv literal, sem shell
            [_GIT, "-C", str(directory), *args], check=True, capture_output=True, text=True
        )

    run("init")
    run("config", "user.email", "t@example.invalid")
    run("config", "user.name", "Teste")
    run("config", "commit.gpgsign", "false")
    (directory / "a.txt").write_text("x\n", encoding="utf-8")
    run("add", "a.txt")
    run("commit", "-m", "init")
    (directory / "a.txt").write_text("y\n", encoding="utf-8")

    workspace_id = auth_api_client.post("/api/workspaces", json=_payload(directory)).json()["id"]
    corpo = auth_api_client.get(f"/api/workspaces/{workspace_id}/git").json()

    assert corpo["is_git_repo"] is True
    assert corpo["head"] is not None
    assert len(corpo["head"]) == 40
    assert corpo["branch"] in {"main", "master"}
    assert corpo["dirty_file_count"] == 1
