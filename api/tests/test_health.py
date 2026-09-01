"""`GET /api/health` — a única rota pública da API.

A prova de que ela é **a única** vive em `test_auth_and_bootstrap.py`; aqui trata-se
apenas do conteúdo mínimo que ela devolve ([06] §1).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import __version__

#: Tudo o que [06] §1 mantém do lado do backend. Se qualquer um destes aparecer na
#: resposta de saúde, a informação vazou.
FORBIDDEN_IN_HEALTH = (
    "path",
    "dir",
    "data_dir",
    "database",
    "url",
    "token",
    "secret",
    "env",
    "host",
    "port",
    "python",
    "platform",
)


def test_health_responde_ok(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_health_nao_expoe_nada_alem_de_prontidao_e_versao(client: TestClient) -> None:
    payload = client.get("/api/health").json()

    assert set(payload) == {"status", "version"}
    serialized = str(payload).lower()
    for forbidden in FORBIDDEN_IN_HEALTH:
        assert forbidden not in serialized, f"health expôs `{forbidden}`"


def test_health_continua_publico_com_token(auth_client: TestClient) -> None:
    """Ser público não significa recusar credencial: o SPA manda o header em tudo."""
    assert auth_client.get("/api/health").status_code == 200


def test_app_nao_cria_diretorio_de_dados_ao_subir(client: TestClient, tmp_path: Path) -> None:
    """Criar o app não pode tocar o disco: nada de banco ou pasta no import/startup."""
    client.get("/api/health")

    assert not (tmp_path / "artifacts").exists()
