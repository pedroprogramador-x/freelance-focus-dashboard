"""E3-AUD-005 + E3-AUD2-004 — `Origin` tem de bater com o `Host`, não só ser loopback.

[01](../../docs/architecture/01-v1-architecture.md) §4: "requisições que alteram estado
exigem mesma origem". O middleware `local_guard` recusa `POST`/`PATCH`/`PUT`/`DELETE` sob
`/api/` quando o `Origin` não é **exatamente** a origem servida (esquema+host+porta do
`Host` efetivo) ou o `Sec-Fetch-Site` não é `same-origin`/`none`.

Os testes de `TestClient` usam `base_url` `http://127.0.0.1:8756` (fixtures em
`conftest.py`), então o `Host` efetivo é `127.0.0.1:8756`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.security import (
    origin_matches_host,
    same_origin_write_allowed,
    sec_fetch_site_allows_write,
)

_SERVED = "http://127.0.0.1:8756"  # == LOCAL_BASE_URL das fixtures


def _payload(directory: Path) -> dict[str, str]:
    return {"name": "Origem", "type": "personal", "local_path": str(directory)}


# ---------------------------------------------------------------- helpers puros


@pytest.mark.parametrize(
    ("origin", "host", "expected"),
    [
        # ausente/vazio → aceito (cliente não-browser)
        (None, "127.0.0.1:8756", True),
        ("", "127.0.0.1:8756", True),
        # casamento EXATO da origem servida
        ("http://127.0.0.1:8756", "127.0.0.1:8756", True),
        # proxy do Vite dev: Host preservado (changeOrigin:false) → Host == Origin
        ("http://localhost:5173", "localhost:5173", True),
        # porta default do esquema
        ("http://127.0.0.1", "127.0.0.1:80", True),
        # --- negados ---
        ("http://127.0.0.1:5173", "127.0.0.1:8756", False),  # porta loopback diferente
        ("http://localhost:8756", "127.0.0.1:8756", False),  # nome de host diferente
        ("https://127.0.0.1:8756", "127.0.0.1:8756", False),  # esquema diferente
        ("http://evil.example", "127.0.0.1:8756", False),
        ("https://127.0.0.1.evil.com", "127.0.0.1:8756", False),
        ("null", "127.0.0.1:8756", False),
        ("http://127.0.0.1:99999", "127.0.0.1:8756", False),  # porta inválida
        ("http://127.0.0.1:8756/extra", "127.0.0.1:8756", False),  # não é origem pura
        ("http://127.0.0.1:8756", None, False),  # sem Host não há com o que casar
    ],
)
def test_origin_matches_host(origin: str | None, host: str | None, expected: bool) -> None:
    assert origin_matches_host(origin, host, request_scheme="http") is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ("same-origin", True),
        ("SAME-ORIGIN", True),  # case-insensitive
        ("none", True),
        ("same-site", False),  # E3-AUD2-004: outra porta/subdomínio não é mesma origem
        ("cross-site", False),
        ("", False),  # valor presente porém vazio → desconhecido → recusado
        ("cross-origin", False),  # valor inexistente no padrão → recusado
    ],
)
def test_sec_fetch_site_allows_write(value: str | None, expected: bool) -> None:
    assert sec_fetch_site_allows_write(value) is expected


def test_same_origin_write_allowed_composto() -> None:
    # leitura: guarda de origem não se aplica
    assert (
        same_origin_write_allowed("GET", "http://evil.example", "cross-site", "127.0.0.1:8756")
        is True
    )
    # escrita, origem exata + Sec-Fetch-Site ok
    assert same_origin_write_allowed("POST", _SERVED, "same-origin", "127.0.0.1:8756") is True
    # escrita sem Origin (não-browser) → passa
    assert same_origin_write_allowed("POST", None, None, "127.0.0.1:8756") is True
    # escrita, origem loopback mas porta errada → nega
    assert (
        same_origin_write_allowed("PATCH", "http://127.0.0.1:5173", None, "127.0.0.1:8756") is False
    )
    # escrita, origem exata mas Sec-Fetch-Site same-site → nega
    assert same_origin_write_allowed("DELETE", _SERVED, "same-site", "127.0.0.1:8756") is False


# ---------------------------------------------------------------- via HTTP real


def test_post_sem_origin_passa(auth_api_client: TestClient, tmp_path: Path) -> None:
    """Cliente não-browser (sem `Origin`): a proteção é o token, não o header."""
    directory = tmp_path / "ok"
    directory.mkdir()
    response = auth_api_client.post("/api/workspaces", json=_payload(directory))
    assert response.status_code == 201


def test_post_com_origem_exata_passa(auth_api_client: TestClient, tmp_path: Path) -> None:
    directory = tmp_path / "exata"
    directory.mkdir()
    response = auth_api_client.post(
        "/api/workspaces",
        json=_payload(directory),
        headers={"Origin": _SERVED, "Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 201


def test_post_com_porta_loopback_diferente_e_403(
    auth_api_client: TestClient, tmp_path: Path
) -> None:
    """E3-AUD2-004: loopback não basta — a porta tem de casar com o `Host` servido."""
    directory = tmp_path / "porta"
    directory.mkdir()
    response = auth_api_client.post(
        "/api/workspaces",
        json=_payload(directory),
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "cross_origin_denied"


def test_post_com_host_de_nome_diferente_e_403(auth_api_client: TestClient, tmp_path: Path) -> None:
    directory = tmp_path / "nome"
    directory.mkdir()
    response = auth_api_client.post(
        "/api/workspaces",
        json=_payload(directory),
        headers={"Origin": "http://localhost:8756"},  # Host efetivo é 127.0.0.1:8756
    )
    assert response.status_code == 403


def test_proxy_vite_dev_simulado_passa(auth_api_client: TestClient, tmp_path: Path) -> None:
    """Com `Host` e `Origin` do dev server (proxy `changeOrigin:false`), a escrita passa."""
    directory = tmp_path / "proxy"
    directory.mkdir()
    response = auth_api_client.post(
        "/api/workspaces",
        json=_payload(directory),
        headers={
            "Host": "localhost:5173",
            "Origin": "http://localhost:5173",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert response.status_code == 201


def test_post_com_sec_fetch_site_desconhecido_e_403(
    auth_api_client: TestClient, tmp_path: Path
) -> None:
    directory = tmp_path / "sfs"
    directory.mkdir()
    for value in ("cross-site", "same-site", "bogus-value"):
        response = auth_api_client.post(
            "/api/workspaces",
            json=_payload(directory),
            headers={"Origin": _SERVED, "Sec-Fetch-Site": value},
        )
        assert response.status_code == 403, value
        assert response.json()["code"] == "cross_origin_denied"


def test_patch_com_origin_divergente_e_403(auth_api_client: TestClient, tmp_path: Path) -> None:
    directory = tmp_path / "patch"
    directory.mkdir()
    workspace_id = auth_api_client.post("/api/workspaces", json=_payload(directory)).json()["id"]

    response = auth_api_client.patch(
        f"/api/workspaces/{workspace_id}",
        json={"status": "archived"},
        headers={"Origin": "http://attacker.test"},
    )
    assert response.status_code == 403


def test_get_com_origin_divergente_ainda_passa(auth_api_client: TestClient) -> None:
    """Leitura não altera estado: o guarda de origem não se aplica (o token basta)."""
    response = auth_api_client.get("/api/workspaces", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200


def test_origin_divergente_recusado_antes_do_token(api_client: TestClient, tmp_path: Path) -> None:
    """403 de origem vem **antes** da checagem de token: aba maliciosa não sonda a rota."""
    directory = tmp_path / "pre"
    directory.mkdir()
    response = api_client.post(  # sem Authorization
        "/api/workspaces",
        json=_payload(directory),
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "cross_origin_denied"
