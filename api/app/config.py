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


#: Variáveis que o cliente do OneDrive define com a raiz de cada conta sincronizada.
_ONEDRIVE_ENV_VARS = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")


def detected_sync_roots() -> tuple[Path, ...]:
    """Raízes de sincronização conhecidas pelo ambiente (E7.4): hoje, as do OneDrive.

    Lidas aqui porque este é o único módulo que lê ambiente. **Não** são um campo de
    configuração: nenhuma variável `FF_*` consegue esvaziar a lista — só acrescentar
    (`extra_sync_roots`).
    """
    roots: list[Path] = []
    for name in _ONEDRIVE_ENV_VARS:
        value = os.environ.get(name)
        if value:
            roots.append(Path(value))
    return tuple(roots)


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

    #: Raiz das worktrees de task (E7.4). `None` resolve para `<data_dir>/worktrees`
    #: ([04] §8). O caminho canônico — não este léxico — é o que vale: ver
    #: `path_runtime.prepare_worktree_root`.
    worktrees_dir: Path | None = None

    #: Raízes de sincronização **adicionais** às detectadas (`detected_sync_roots`). Só
    #: acrescenta; a detecção do OneDrive não pode ser desligada por configuração.
    extra_sync_roots: tuple[Path, ...] = ()

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

    @property
    def resolved_worktrees_dir(self) -> Path:
        """Raiz das worktrees: override explícito ou `<data_dir>/worktrees`."""
        if self.worktrees_dir is not None:
            return self.worktrees_dir
        return self.data_dir / "worktrees"

    @property
    def sync_roots(self) -> tuple[Path, ...]:
        """Detectadas (OneDrive) mais as extras configuradas. Nunca menos que as detectadas."""
        return detected_sync_roots() + tuple(self.extra_sync_roots)

    def ensure_data_dir(self) -> Path:
        """Cria o diretório de dados. **Chamada explícita**, nunca no import."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir

    def ensure_worktrees_dir(self) -> Path:
        """Cria a raiz das worktrees. Chamada explícita; a validação vem depois, no
        `path_runtime.prepare_worktree_root`."""
        root = self.resolved_worktrees_dir
        root.mkdir(parents=True, exist_ok=True)
        return root


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()


@lru_cache(maxsize=1)
def get_secrets() -> SecretSettings:
    return SecretSettings()
