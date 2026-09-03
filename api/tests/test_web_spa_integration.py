"""E3-AUD2-002 — "mesma origem funcionando", com **build real** do frontend.

Este teste roda `vite build` de verdade (com `VITE_APP_MODE=local_dev_workspace`) num
diretório temporário e serve essa saída ao `TestClient`. Nada de HTML/JS/CSS fabricado à
mão: é o único jeito de confiar numa alegação sobre o `base` do build local e sobre o
FastAPI servir os assets com hash gerados pelo Vite.

Fluxo verificado, fim a fim:

1. `GET /` → SPA compilado real, `Cache-Control: no-store`, `base` `/` (não a do Pages);
2. cada `/assets/<hash>.{js,css}` referenciado no `index.html` real é servido pelo FastAPI;
3. o `<meta name="ff-session-token">` é injetado no HTML **servido** (pela FastAPI), e o
   token de lá autentica `GET`/`POST /api/workspaces` de verdade.

Comportamento quando o Node **não** está disponível (sem `npx` ou sem `node_modules`):

* No workflow `.github/workflows/cross-stack-ci.yml`, que define `FF_CROSS_STACK_CI=1` (e
  é o **único** que define essa variável): **FALHA** — este é o gate que não pode ficar
  mudo, e lá o Node é instalado de propósito.
* Em qualquer outro contexto (ex.: dentro de `api-ci.yml`, que não tem Node por desenho —
  ADR-0001 item 5, ou numa máquina sem `npm install`): **pula**, como antes.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.main import create_app
from tests.conftest import LOCAL_BASE_URL, run_migrations

_REPO_ROOT = Path(__file__).resolve().parents[2]
_NPX = shutil.which("npx")

#: Definida **só** pelo `cross-stack-ci.yml`. Liga o modo "não pode ficar mudo".
_CROSS_STACK_GATE = os.environ.get("FF_CROSS_STACK_CI") == "1"

_ASSET_REF = re.compile(r'(?:src|href)="(/assets/[^"]+)"')
_TOKEN_META = re.compile(r'<meta[^>]*name="ff-session-token"[^>]*content="([^"]+)"[^>]*>')


def _node_unavailable(reason: str) -> NoReturn:
    """Sem Node: falha no gate cross-stack, pula em qualquer outro contexto."""
    if _CROSS_STACK_GATE:
        pytest.fail(
            f"FF_CROSS_STACK_CI=1 e {reason}: o gate cross-stack precisa de Node e não "
            "pode ficar mudo — instale Node/`npm ci` neste job"
        )
    pytest.skip(f"{reason}: build real do frontend não exercitado (Node ausente)")


@pytest.fixture(scope="session")
def real_local_dist(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """`vite build` REAL em modo local, num diretório temporário fora do repo."""
    if _NPX is None:
        _node_unavailable("npx indisponível")
    if not (_REPO_ROOT / "node_modules").is_dir():
        _node_unavailable("node_modules ausente")

    out_dir = tmp_path_factory.mktemp("real-local-build") / "dist"
    env = {**os.environ, "VITE_APP_MODE": "local_dev_workspace"}
    result = subprocess.run(  # noqa: S603 — argv literal, sem shell, npx resolvido por which
        [_NPX, "vite", "build", "--outDir", str(out_dir), "--emptyOutDir"],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert result.returncode == 0, (
        "`vite build` (VITE_APP_MODE=local_dev_workspace) falhou:\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert (out_dir / "index.html").is_file(), "o build não produziu index.html"
    assert (out_dir / "assets").is_dir(), "o build não produziu assets/"
    return out_dir


@pytest.fixture
def spa_client(tmp_path: Path, real_local_dist: Path) -> Iterator[TestClient]:
    database = tmp_path / "workspace.db"
    settings = AppSettings(
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        web_dist_dir=real_local_dist,
    )
    run_migrations(settings.sqlalchemy_url)
    with TestClient(create_app(settings), base_url=LOCAL_BASE_URL) as client:
        yield client


def test_real_build_same_origin_bootstrap_and_authenticated_roundtrip(
    spa_client: TestClient, tmp_path: Path
) -> None:
    # 1. `/` serve o SPA compilado real
    index = spa_client.get("/")
    assert index.status_code == 200
    assert index.headers["cache-control"] == "no-store"
    html = index.text

    # base `/` do build local — jamais a base do GitHub Pages
    assert "/freelance-focus-dashboard/" not in html
    assert 'src="/assets/' in html

    # 2. cada asset real (nome com hash do Vite) é servido pelo FastAPI
    asset_refs = _ASSET_REF.findall(html)
    assert asset_refs, "o index.html real não referencia nenhum /assets/…"
    js_refs = [ref for ref in asset_refs if ref.endswith(".js")]
    assert js_refs, "o build real não tem bundle .js"
    for ref in asset_refs:
        asset = spa_client.get(ref)
        assert asset.status_code == 200, f"{ref} não foi servido pelo FastAPI"
        assert asset.content, f"{ref} veio vazio"

    # é o NOSSO bundle: uma string distintiva do código do Workspace Registry
    js_body = spa_client.get(js_refs[0]).text
    assert "workspace_mode_disabled" in js_body

    # 3. token extraído do HTML SERVIDO (injetado pela FastAPI), não fabricado no teste
    meta = _TOKEN_META.search(html)
    assert meta is not None, "meta ff-session-token ausente no HTML servido"
    token = meta.group(1)
    assert token == spa_client.app.state.session_token  # type: ignore[attr-defined]

    auth = {"Authorization": f"Bearer {token}", "Origin": LOCAL_BASE_URL}

    assert spa_client.get("/api/workspaces").status_code == 401
    listed = spa_client.get("/api/workspaces", headers=auth)
    assert listed.status_code == 200
    assert listed.json() == []

    # 4. escrita real de mesma origem
    project_dir = tmp_path / "projeto-real"
    project_dir.mkdir()
    created = spa_client.post(
        "/api/workspaces",
        headers=auth,
        json={"name": "Integração", "type": "freelance", "local_path": str(project_dir)},
    )
    assert created.status_code == 201, created.text
    again = spa_client.get("/api/workspaces", headers=auth)
    assert [item["id"] for item in again.json()] == [created.json()["id"]]
