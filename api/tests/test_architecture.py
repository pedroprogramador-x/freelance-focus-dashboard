"""Teste de arquitetura: as arestas de importação proibidas.

[01](../../docs/architecture/01-v1-architecture.md) §3 lista dependências que não podem
existir. Aqui elas viram falha de suíte, e não item de checklist de revisão.

Usa só `ast` da biblioteca padrão — trazer uma ferramenta de contratos de importação para
verificar meia dúzia de regras seria desproporcional.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

#: Nada disto pode ser importado em lugar nenhum do backend na E2 — nem em módulo, nem em
#: teste. Providers e agentes chegam a partir da E8.
FORBIDDEN_EVERYWHERE = (
    "anthropic",
    "openai",
    "claude",
    "codex",
    "ruflo",
    "mcp",
    "langchain",
    "llama_index",
    "chromadb",
    "faiss",
    "celery",
    "redis",
)

#: `multiprocessing` e `pty` são superfície de execução de processo e pertencem ao Full
#: Safety Runtime (E7) e ao Test Runner, nenhum dos dois existindo ainda.
FORBIDDEN_PROCESS = ("multiprocessing", "pty")

#: `subprocess` é liberado **exclusivamente** em `git_runtime/`, e só para o preflight de
#: LEITURA da E3 ([01] contrato de `git_runtime/`, [07] gate E3). Em qualquer outro módulo
#: continua proibido até E7.
SUBPROCESS_ALLOWED_UNDER = APP_ROOT / "git_runtime"


def _python_files() -> list[Path]:
    return sorted(APP_ROOT.rglob("*.py"))


def _module_name(path: Path) -> str:
    relative = path.relative_to(APP_ROOT.parent).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


ALL_FILES = _python_files()


def test_existem_modulos_para_analisar() -> None:
    assert ALL_FILES, "nenhum módulo encontrado — o teste estaria passando à toa"


@pytest.mark.parametrize("path", ALL_FILES, ids=_module_name)
def test_nenhum_provider_ou_agente_e_importado(path: Path) -> None:
    for imported in _imports(path):
        root = imported.split(".")[0].lower()
        assert root not in FORBIDDEN_EVERYWHERE, (
            f"{_module_name(path)} importa `{imported}`: providers e agentes só a partir da E8"
        )


@pytest.mark.parametrize("path", ALL_FILES, ids=_module_name)
def test_nenhuma_execucao_de_processo(path: Path) -> None:
    for imported in _imports(path):
        root = imported.split(".")[0].lower()
        assert root not in FORBIDDEN_PROCESS, (
            f"{_module_name(path)} importa `{imported}`: execução de processo é E7"
        )
        if root == "subprocess":
            assert path.is_relative_to(SUBPROCESS_ALLOWED_UNDER), (
                f"{_module_name(path)} importa `subprocess` fora de git_runtime/: "
                "execução de processo fora do preflight de leitura é E7"
            )


def test_git_runtime_e_somente_leitura() -> None:
    """[01]: `git_runtime/` nunca executa verbo que altere o repositório do usuário."""
    mutating_verbs = (
        "commit",
        "merge",
        "push",
        "rebase",
        "reset",
        "checkout",
        "clean",
        "init",
        "apply",
        "stash",
        "cherry-pick",
        "restore",
        "switch",
        "tag",
        "fetch",
        "pull",
        "gc",
        "prune",
    )
    for path in (APP_ROOT / "git_runtime").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for verb in mutating_verbs:
            assert f'"{verb}"' not in source, (
                f"git_runtime/{path.name} usa o verbo git `{verb}`: o adaptador é só leitura"
            )


def test_safety_e_puro() -> None:
    """`safety/` recebe fatos e decide. Não faz IO, não abre banco, não conhece HTTP."""
    proibidos = {
        "fastapi",
        "starlette",
        "sqlalchemy",
        "alembic",
        "pydantic_settings",
        "app.path_runtime",
        "app.db",
        "app.api",
        "app.main",
        "app.config",
        "pathlib",
        "os",
        "io",
        "shutil",
        "socket",
        "requests",
        "httpx",
    }

    for path in (APP_ROOT / "safety").rglob("*.py"):
        for imported in _imports(path):
            root = imported.split(".")[0]
            assert imported not in proibidos and root not in proibidos, (
                f"safety/{path.name} importa `{imported}`: o kernel precisa continuar puro"
            )


def test_safety_nao_importa_path_runtime() -> None:
    """A dependência corre só na direção `path_runtime → safety` ([04] §4)."""
    for path in (APP_ROOT / "safety").rglob("*.py"):
        assert not any("path_runtime" in imported for imported in _imports(path))


def test_path_runtime_nao_importa_camadas_superiores() -> None:
    proibidos = {"app.db", "app.api", "app.main", "fastapi", "sqlalchemy", "alembic"}

    for imported in _imports(APP_ROOT / "path_runtime.py"):
        assert imported not in proibidos, f"path_runtime importa `{imported}`"


def test_db_nao_importa_camadas_superiores() -> None:
    proibidos = {"app.api", "app.main", "app.path_runtime", "fastapi"}

    for path in (APP_ROOT / "db").rglob("*.py"):
        for imported in _imports(path):
            assert imported not in proibidos, f"db/{path.name} importa `{imported}`"


def test_api_nao_importa_infraestrutura_de_execucao() -> None:
    """Uma rota nunca executa processo nem git diretamente ([01] §3)."""
    proibidos = {"app.agent_runtime", "app.tool_executor", "app.git_runtime", "app.path_runtime"}

    for path in (APP_ROOT / "api").rglob("*.py"):
        for imported in _imports(path):
            assert imported not in proibidos, f"api/{path.name} importa `{imported}`"


def test_backend_nao_conhece_o_dominio_comercial() -> None:
    """[ADR-0002]: o backend não importa `Client`, `Proposal`, `Project` nem lê localStorage."""
    termos = ("localstorage", "freelance_focus_data", "projectplanning")

    for path in ALL_FILES:
        conteudo = path.read_text(encoding="utf-8").lower()
        for termo in termos:
            assert termo not in conteudo, f"{_module_name(path)} referencia `{termo}`"


def test_nenhum_shell_true() -> None:
    for path in ALL_FILES:
        assert "shell=True" not in path.read_text(encoding="utf-8")
