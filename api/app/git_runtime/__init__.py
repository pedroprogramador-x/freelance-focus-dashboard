"""Git Runtime — adaptador de Git.

[01](../../../docs/architecture/01-v1-architecture.md) §2: `git_runtime/` é o adaptador de
Git e o ciclo de vida de worktree. Nesta fase (E3) só o **preflight de leitura** existe:
dado um caminho absoluto, responde se é repositório, qual o `HEAD`, o branch e quantos
arquivos divergem da árvore de trabalho.

[07](../../../docs/architecture/07-roadmap-v1.md) "Projeto criado do zero": um
`DevWorkspace` pode **não** ser repositório Git, e isso não é erro — é
`is_git_repo=False`, com os demais campos `None`.

Invariantes congelados ([01] contrato de `git_runtime/`, [07] gate E3):

* **Só leitura.** Nenhum subcomando que altere o repositório do usuário — sem `commit`,
  `merge`, `push`, `rebase`, `reset`, `checkout`, `clean`, `init`, `apply`, `stash`.
  `test_architecture.py::test_git_runtime_e_somente_leitura` transforma isso em falha de
  suíte.
* **Nunca lança.** Qualquer falha de IO, `timeout`, `git` ausente do `PATH` ou saída
  inesperada vira o resultado neutro `_NOT_A_REPO`.
* **Timeout curto e fixo.** Um preflight travado não pode segurar uma requisição HTTP.

`git_runtime/` pode importar `safety`, `config` e stdlib — e nada mais ([01]). O
`subprocess` fica confinado a este pacote; qualquer outro uso continua proibido até o
Full Safety Runtime (E7).
"""

from __future__ import annotations

import os
import shutil

# git de LEITURA apenas; verbos mutantes proibidos (ver docstring + test_architecture.py).
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass

#: Folgado para operações locais; curto o bastante para não segurar o event loop.
_TIMEOUT_SECONDS = 5

_SHA1_LENGTH = 40

#: Opções globais em **toda** invocação (E3-AUD-003, "estritamente somente-leitura"):
#: `core.fsmonitor=false` impede o git de iniciar o processo de fsmonitor configurado no
#: repo do usuário; a variável `GIT_OPTIONAL_LOCKS=0` (em `_git_env`) impede o refresh do
#: `.git/index` que um `git status` normal faria.
_READONLY_GIT_OPTIONS = ("-c", "core.fsmonitor=false")

#: Ambiente **mínimo**: só o que o `git` precisa para funcionar como leitor no SO. **Nenhum
#: `GIT_*` entra** — nem `GIT_EXEC_PATH`/`GIT_TEMPLATE_DIR` (E3-AUD2-003): o git resolve o
#: exec-path a partir do próprio binário, e template dir só importa em `git init`, que
#: nunca chamamos. `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE`/`GIT_CONFIG`/helpers de
#: credencial/editor/pager herdados do processo pai ficam todos de fora.
#: `test_git_runtime.py::test_git_env_e_exatamente_a_allowlist` trava isso por
#: **igualdade de conjunto**, não por checagem de nomes conhecidos.
_GIT_ENV_ALLOWLIST = frozenset(
    name.upper()
    for name in (
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMDATA",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
    )
)

#: As duas únicas variáveis que `_git_env` **adiciona** ao ambiente filtrado.
_GIT_ENV_OVERRIDES = {
    "GIT_OPTIONAL_LOCKS": "0",  # nada de refresh/lock do índice num comando de leitura
    "GIT_TERMINAL_PROMPT": "0",  # nunca abre prompt de credencial
}


@dataclass(frozen=True, slots=True)
class GitPreflight:
    """Estado observável de um diretório quanto a Git. Sem decisão embutida.

    ``head`` é `None` num repositório recém-criado sem nenhum commit (*unborn branch*),
    embora ``is_git_repo`` já seja `True` e ``branch`` possa estar preenchido.
    ``branch`` é `None` quando o `HEAD` está *detached*. ``dirty_file_count`` conta as
    linhas de ``git status --porcelain`` — `0` numa árvore limpa, `None` se a leitura
    falhou.
    """

    is_git_repo: bool
    head: str | None
    branch: str | None
    dirty_file_count: int | None


_NOT_A_REPO = GitPreflight(is_git_repo=False, head=None, branch=None, dirty_file_count=None)


def _git_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Ambiente mínimo para o git: `(source ∩ allowlist) ∪ _GIT_ENV_OVERRIDES`. Nada mais.

    `source` é `os.environ` por padrão; o parâmetro existe só para os testes poderem
    contaminar a entrada sem mexer no ambiente global. As overrides vêm por último e
    vencem qualquer valor herdado (E3-AUD2-003).
    """
    environ = os.environ if source is None else source
    env = {key: value for key, value in environ.items() if key.upper() in _GIT_ENV_ALLOWLIST}
    env.update(_GIT_ENV_OVERRIDES)
    return env


def _run_git(git: str, local_path: str, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Executa `git -c core.fsmonitor=false -C <local_path> <args>` sem shell, ambiente mínimo.

    Devolve `None` em qualquer falha.
    """
    try:
        return subprocess.run(  # noqa: S603 — sem shell; argv literal; git resolvido por shutil.which
            [git, *_READONLY_GIT_OPTIONS, "-C", local_path, *args],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
            env=_git_env(),
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        # ValueError cobre, entre outros, byte nulo embutido no caminho — que o
        # `subprocess` recusa antes de chegar ao `git`.
        return None


def _stdout_if_ok(result: subprocess.CompletedProcess[str] | None) -> str | None:
    if result is None or result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def preflight(local_path: str) -> GitPreflight:
    """Lê o estado Git de `local_path` (caminho absoluto, canônico). Nunca lança."""
    git = shutil.which("git")
    if git is None:
        return _NOT_A_REPO

    inside = _run_git(git, local_path, "rev-parse", "--is-inside-work-tree")
    if _stdout_if_ok(inside) != "true":
        return _NOT_A_REPO

    head = _stdout_if_ok(_run_git(git, local_path, "rev-parse", "HEAD"))
    if head is not None and (len(head) != _SHA1_LENGTH or not _is_hex(head)):
        head = None

    branch = _stdout_if_ok(_run_git(git, local_path, "symbolic-ref", "--quiet", "--short", "HEAD"))

    dirty_file_count: int | None = None
    status = _run_git(git, local_path, "status", "--porcelain")
    if status is not None and status.returncode == 0:
        dirty_file_count = sum(1 for line in status.stdout.splitlines() if line.strip())

    return GitPreflight(
        is_git_repo=True,
        head=head,
        branch=branch,
        dirty_file_count=dirty_file_count,
    )


def _is_hex(value: str) -> bool:
    try:
        int(value, 16)
    except ValueError:
        return False
    return True
