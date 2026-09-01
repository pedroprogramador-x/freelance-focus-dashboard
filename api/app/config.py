"""Configuração tipada da aplicação.

Este é o **único** ponto do backend que lê variáveis de ambiente
([01](../../docs/architecture/01-v1-architecture.md) §2). Espalhar `os.environ` pelos
módulos tornaria impossível auditar vazamento de segredo.

Separação deliberada:

* ``AppSettings``    — configuração operacional (paths, host/porta, nome).
* ``SecretSettings`` — segredos de provider. **Vazio na E2**: nenhum provider existe
  ainda, e nenhum campo recebe *default* inseguro. A classe existe para que a fronteira
  já esteja no lugar quando E8/E9 trouxerem credenciais.

Nada aqui cria diretório ou banco no import. A criação é explícita, por
``ensure_data_dir()``, chamada apenas no caminho de runtime.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_PREFIX = "FF_"


def default_data_dir() -> Path:
    """Diretório de dados de runtime, **fora** da árvore do repositório.

    No Windows resolve para ``%LOCALAPPDATA%\\FreelanceFocus``. O repositório desta
    máquina vive dentro do OneDrive, e ADR-0004 exige que os dados operacionais fiquem
    fora dele: sincronização concorrente causa lock de arquivo e corrompe `.git`/SQLite.
    """
    # `sys.platform` é estreitado estaticamente pelo mypy, que passa a considerar o outro
    # ramo inalcançável. A comparação indireta preserva a checagem dos dois caminhos.
    platform = sys.platform
    if platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "FreelanceFocus"
        return Path.home() / "AppData" / "Local" / "FreelanceFocus"

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "freelance-focus"
    return Path.home() / ".local" / "share" / "freelance-focus"


class SecretSettings(BaseSettings):
    """Segredos de provider.

    Intencionalmente **sem campos na E2**. Nenhum provider é usado nesta fase, e um campo
    de segredo com *default* seria exatamente o tipo de pegadinha que a auditoria proíbe.
    Quando E8 introduzir o Developer, as chaves entram aqui — obrigatórias, sem default —
    e permanecem confinadas ao processo do provider correspondente.
    """

    model_config = SettingsConfigDict(
        env_prefix=f"{_ENV_PREFIX}SECRET_",
        extra="ignore",
        frozen=True,
    )


class AppSettings(BaseSettings):
    """Configuração operacional. Não contém e nunca conterá segredo."""

    model_config = SettingsConfigDict(
        env_prefix=_ENV_PREFIX,
        extra="ignore",
        frozen=True,
    )

    app_name: str = "Freelance Focus — AI Dev Workspace"
    environment: str = "local"

    #: Loopback apenas. Nunca `0.0.0.0` ([01] §4).
    host: str = "127.0.0.1"
    port: int = 8756

    data_dir: Path = Field(default_factory=default_data_dir)

    #: Sobrescreve a URL derivada de ``data_dir``. Usado pelos testes com banco temporário.
    database_url: str | None = None

    #: Build compilado do SPA. É de onde sai o HTML que carrega o `LocalSessionToken`
    #: ([06] §1). `None` resolve para `<repo>/dist`; quando não existe, a rota `/`
    #: responde `404 web_ui_unavailable` — a API segue funcionando normalmente.
    web_dist_dir: Path | None = None

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        allowed = {"127.0.0.1", "::1", "localhost"}
        if value not in allowed:
            raise ValueError(
                "host deve ser loopback (127.0.0.1, ::1 ou localhost); "
                "expor a API na rede contraria docs/architecture/01 §4"
            )
        return value

    @property
    def database_path(self) -> Path:
        return self.data_dir / "workspace.db"

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url is not None:
            return self.database_url
        return f"sqlite+pysqlite:///{self.database_path.as_posix()}"

    @property
    def artifacts_dir(self) -> Path:
        """Rendered Context Artifacts ([02] §5). Criado a partir da E5, não agora."""
        return self.data_dir / "artifacts"

    @property
    def resolved_web_dist_dir(self) -> Path:
        """Diretório do SPA compilado. Padrão: `<repo>/dist`, irmão de `api/`."""
        if self.web_dist_dir is not None:
            return self.web_dist_dir
        return Path(__file__).resolve().parents[2] / "dist"

    @property
    def web_index_path(self) -> Path:
        return self.resolved_web_dist_dir / "index.html"

    @property
    def web_assets_dir(self) -> Path:
        return self.resolved_web_dist_dir / "assets"

    def ensure_data_dir(self) -> Path:
        """Cria o diretório de dados. **Chamada explícita**, nunca no import."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()


@lru_cache(maxsize=1)
def get_secrets() -> SecretSettings:
    return SecretSettings()
