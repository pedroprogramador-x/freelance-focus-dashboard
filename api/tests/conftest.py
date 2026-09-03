"""Fixtures da suíte.

Todo banco é **temporário**. Nada aqui toca o `data_dir` real nem cria arquivo dentro do
repositório — requisito explícito da E2.

O schema vem sempre da **migration**, nunca de `create_all`: é a migration que produz o
banco de produção, então é ela que os testes precisam exercitar.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings
from app.db.session import create_engine, create_session_factory
from app.main import create_app

API_ROOT = Path(__file__).resolve().parents[1]


def run_migrations(url: str, revision: str = "head") -> None:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.attributes["sqlalchemy.url"] = url
    command.upgrade(config, revision)


def downgrade_migrations(url: str, revision: str = "base") -> None:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.attributes["sqlalchemy.url"] = url
    command.downgrade(config, revision)


#: `Host` realista de loopback. A API valida o header ([06] §1, defesa 2), então o
#: `testserver` padrão do TestClient seria recusado — e com razão.
LOCAL_BASE_URL = "http://127.0.0.1:8756"


@pytest.fixture
def temp_settings(tmp_path: Path) -> AppSettings:
    database = tmp_path / "workspace.db"
    return AppSettings(
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        # Aponta para um diretório inexistente de propósito: sem isso os testes veriam o
        # `dist/` real do repositório e passariam a depender de `npm run build`.
        web_dist_dir=tmp_path / "sem-build",
    )


@pytest.fixture
def web_dist(tmp_path: Path) -> Path:
    """Build de SPA mínimo, só para exercitar a injeção do token.

    Não usa `src/` nem exige `npm run build`: é um template de teste, e o frontend React
    existente permanece intocado.
    """
    dist = tmp_path / "web-dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>Freelance Focus</title></head>"
        '<body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    return dist


@pytest.fixture
def migrated_url(temp_settings: AppSettings) -> str:
    run_migrations(temp_settings.sqlalchemy_url)
    return temp_settings.sqlalchemy_url


@pytest.fixture
def engine(temp_settings: AppSettings, migrated_url: str) -> Iterator[Engine]:
    del migrated_url  # dependência de ordem: garante o schema aplicado
    created = create_engine(temp_settings)
    try:
        yield created
    finally:
        created.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as active:
        yield active


@pytest.fixture
def client(temp_settings: AppSettings) -> Iterator[TestClient]:
    """Cliente **sem** credencial: só `GET /api/health` responde."""
    with TestClient(create_app(temp_settings), base_url=LOCAL_BASE_URL) as test_client:
        yield test_client


@pytest.fixture
def api_client(temp_settings: AppSettings, migrated_url: str) -> Iterator[TestClient]:
    """Cliente **sem** credencial mas com o schema já migrado — rotas que tocam o banco (E3+)."""
    del migrated_url  # dependência de ordem: garante o schema aplicado
    with TestClient(create_app(temp_settings), base_url=LOCAL_BASE_URL) as test_client:
        yield test_client


@pytest.fixture
def auth_api_client(api_client: TestClient) -> TestClient:
    """`api_client` autenticado, como o SPA faria após ler a `<meta>` do HTML."""
    token = api_client.app.state.session_token  # type: ignore[attr-defined]
    api_client.headers["Authorization"] = f"Bearer {token}"
    return api_client


@pytest.fixture
def session_token(client: TestClient) -> str:
    """Token do app em teste, lido da memória do processo — nunca de uma rota."""
    return str(client.app.state.session_token)  # type: ignore[attr-defined]


@pytest.fixture
def auth_client(client: TestClient, session_token: str) -> TestClient:
    """Cliente autenticado, como o SPA faria depois de ler a `<meta>` do HTML."""
    client.headers["Authorization"] = f"Bearer {session_token}"
    return client


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    """Raiz de workspace realista para os testes de path."""
    root = tmp_path / "ws"
    (root / "src" / "nested").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "src" / "nested" / "deep.txt").write_text("deep\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=nao-deve-ser-lido\n", encoding="utf-8")
    (root / ".env.example").write_text("SECRET=\n", encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "loot.txt").write_text("loot\n", encoding="utf-8")

    return root


def supports_symlinks(tmp_path: Path) -> bool:
    """No Windows criar symlink exige privilégio ou modo desenvolvedor."""
    probe = tmp_path / "__symlink_probe__"
    target = tmp_path / "__symlink_target__"
    target.mkdir(exist_ok=True)
    try:
        probe.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        return False
    finally:
        if probe.is_symlink() or probe.exists():
            os.unlink(probe)
    return True
