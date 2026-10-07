"""`LocalSubprocessTestRunner` — o Test Runner V1 (`generic-subprocess-v1`, E8.3).

Executa **só** o que a `TestPolicy` configurada pelo sistema descreve, na worktree do run, sob o
Supervisor de Processos (`process_runtime.run_supervised`). Nunca o que um provider pediu.

## Fluxo, fail closed em cada passo

1. `runner_id` precisa ser `generic-subprocess-v1` (`UNSUPPORTED_RUNNER`); a política é
   revalidada pelo **mesmo** schema de `safety.test_policy` (`INVALID_POLICY`).
2. Cada nome da `env_allowlist` é conferido pela denylist pura da safety
   (`is_forbidden_test_env_name`): chave de provider, token, credencial → `INVALID_ENVIRONMENT`
   **antes** de qualquer processo.
3. Binding `(workspace_ref, run_scope)` → worktree **deste** run por `bind_workspace` — a mesma
   conferência da `ToolExecutorFactory` (`WORKSPACE_UNAVAILABLE`).
4. Executável lógico → caminho **absoluto confiável** (`resolve_trusted_executable`), só pelo
   `PATH` do ambiente confiável do host — nunca pelo `cwd`, nunca pela worktree, nunca um
   `.bat`/`.cmd` nem um intérprete de shell (`EXECUTABLE_UNAVAILABLE`).
5. Ambiente do filho **construído do zero**: a projeção da allowlist sobre o ambiente
   confiável. Nada é herdado; nome ausente não aparece.
6. `ProcessSpec(argv=(executável, *argv), cwd=<workspace na worktree>, env, timeout, limites)`
   → `run_supervised(spec, request.cancel_token.is_cancelled)`.
7. `EXITED` → `TestSummary` (inclusive `exit_code != 0`); `TIMEOUT`/`CANCELLED`/
   `SUPERVISION_FAILED` → `TestRunnerFailure` com o código. Nada é inventado.

## O que este runner **não** é

**Não é sandbox.** Ele garante `cwd` na worktree, comando e `argv` da `TestPolicy`, ambiente
por allowlist, timeout, cancelamento, término da árvore de processos, saída limitada e — fora
daqui, no `execution_verification` — a verificação pós-execução. Ele **não** impede o código do
projeto de ler `~/.ssh`, abrir rede, escrever fora da worktree ou usar APIs do SO ([04] §6):
risco residual aceito até a E14.

## Saída

`stdout`/`stderr` são drenados e limitados pelo Supervisor (para o filho nunca travar num pipe
cheio) e **descartados** aqui: não entram no `TestSummary`, em exceção, em log nem em
`output_ref` (sempre `None` na V1). O runner genérico **não interpreta** a saída: `passed`,
`failed` e `skipped` são `None`.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from collections.abc import Mapping
from types import MappingProxyType

from app.agent_runtime.dto import (
    TestRequest,
    TestRunnerFailure,
    TestRunnerFailureCode,
    TestSummary,
)
from app.process_runtime import (
    InvalidProcessSpec,
    ProcessOutcome,
    ProcessResult,
    ProcessSpec,
    run_supervised,
)
from app.safety.paths import PathForm, classify_path_form
from app.safety.test_policy import (
    InvalidTestPolicy,
    TestPolicy,
    is_forbidden_test_env_name,
    parse_test_policy,
)
from app.tool_executor.validation import ContractViolation, require_instance
from app.tool_executor.workspace import (
    BoundWorkspace,
    ToolExecutorError,
    WorkspaceResolver,
    bind_workspace,
)

#: O único runner desta implementação. Declarado na `TestPolicy`, nunca descoberto.
GENERIC_SUBPROCESS_RUNNER_ID = "generic-subprocess-v1"

_IS_WINDOWS = sys.platform == "win32"

#: O que o `CreateProcess` do Windows lança **nativamente**, sem intérprete. Conjunto fixo, não o
#: `PATHEXT` do ambiente (que traz `.BAT`, `.CMD`, `.VBS`, `.JS`…): a resolução não muda com a
#: configuração do host. Nome lógico sem extensão tenta estas, nesta ordem.
_WINDOWS_LAUNCHABLE_SUFFIXES = (".exe", ".com")

#: Recusados em qualquer SO, como na `ProcessSpec`: o Windows os entregaria ao `cmd.exe`.
_SHELL_SCRIPT_SUFFIXES = (".bat", ".cmd")

#: Caracteres que tornam um nome lógico algo além de **um** nome de arquivo: separadores (os dois,
#: em qualquer SO), `:` (drive ou *alternate data stream* no Windows) e NUL.
_NAME_FORBIDDEN_CHARS = frozenset("/\\:\x00")

#: Intérpretes de **linha de comando** (shells, hosts de script do Windows, e `env`, que é o
#: trampolim POSIX até eles). Recusados como executável — pelo nome encontrado **e** pelo
#: destino real —, com ou sem sufixo de versão (`bash5`, `ksh93`): `cmd /c "npm test"` ou
#: `sh -c "…"` devolveriam ao runner exatamente a gramática de shell que o `argv` estruturado da
#: `TestPolicy` existe para não ter, e `cmd.exe /c npm.cmd` contornaria a recusa de `.cmd`.
_SHELL_INTERPRETERS = re.compile(
    r"(?:cmd|command|powershell|powershell_ise|pwsh|bash|sh|zsh|dash|ash|ksh|mksh|"
    r"fish|csh|tcsh|busybox|env|wsl|wscript|cscript|mshta)(?:[-.]?[0-9][0-9.]*)?"
)


def _fail(
    code: TestRunnerFailureCode, *, tree_confirmed_dead: bool | None = None
) -> TestRunnerFailure:
    return TestRunnerFailure(code, tree_confirmed_dead=tree_confirmed_dead)


def _fold(name: str) -> str:
    """Nome de variável como o SO o compara: case-insensitive no Windows (`Path` = `PATH`)."""
    return name.upper() if _IS_WINDOWS else name


# --------------------------------------------------------------------- resolução do executável


def _launch_name(path: str) -> str:
    """O nome do arquivo como o Win32 o resolve (sem pontos/espaços finais), minúsculo."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return name.rstrip(" .").lower()


def _is_bare_name(logical: str) -> bool:
    """**Um** nome de arquivo, sem diretório: `pytest`, `node.exe`. Nunca `./x`, `../x`, `a/b`."""
    if logical in (".", "..") or _NAME_FORBIDDEN_CHARS.intersection(logical):
        return False
    return not (_IS_WINDOWS and logical != logical.rstrip(" ."))


def _is_absolute_dir(entry: str) -> bool:
    """Entrada de `PATH` utilizável: absoluta de verdade, no SO corrente.

    Entrada vazia (que no POSIX significa "diretório corrente"), relativa (`.`, `bin`),
    *root-relative*/*drive-relative* do Windows, UNC e *device namespace* são **ignoradas**:
    nenhuma delas é uma localização confiável e independente do `cwd`.
    """
    if not entry or "\x00" in entry:
        return False
    return classify_path_form(entry) is PathForm.ABSOLUTE_QUALIFIED and os.path.isabs(entry)


def _candidate_names(logical: str) -> tuple[str, ...]:
    if not _IS_WINDOWS:
        return (logical,)
    if _launch_name(logical).endswith(_WINDOWS_LAUNCHABLE_SUFFIXES):
        return (logical,)
    return tuple(logical + suffix for suffix in _WINDOWS_LAUNCHABLE_SUFFIXES)


def _inside(path: str, root: str) -> bool:
    """`path` é `root` ou está sob ele? Comparação de caminho normalizado, nunca substring."""
    child = os.path.normcase(os.path.normpath(path))
    parent = os.path.normcase(os.path.normpath(root))
    try:
        return os.path.commonpath((child, parent)) == parent
    except ValueError:  # drives diferentes, ou absoluto × relativo
        return False


def _is_shell_interpreter(path: str) -> bool:
    """O nome do arquivo (sem `.exe`/`.com`) é de um intérprete de linha de comando?"""
    name = _launch_name(path)
    for suffix in _WINDOWS_LAUNCHABLE_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return _SHELL_INTERPRETERS.fullmatch(name) is not None


def _is_trusted_executable(candidate: str, excluded_root: str) -> bool:
    """Arquivo regular existente, lançável, que não é shell, fora da worktree — pelo caminho
    **e** pelo destino real."""
    if _launch_name(candidate).endswith(_SHELL_SCRIPT_SUFFIXES) or _is_shell_interpreter(candidate):
        return False
    if _IS_WINDOWS and not _launch_name(candidate).endswith(_WINDOWS_LAUNCHABLE_SUFFIXES):
        return False
    try:
        info = os.stat(candidate)
        real = os.path.realpath(candidate, strict=True)
    except (OSError, ValueError, RuntimeError):
        return False
    if not stat.S_ISREG(info.st_mode) or _is_shell_interpreter(real):
        return False
    if not _IS_WINDOWS and not (info.st_mode & 0o111 and os.access(candidate, os.X_OK)):
        return False
    return not (_inside(candidate, excluded_root) or _inside(real, excluded_root))


def resolve_trusted_executable(
    logical: str, *, search_path: str | None, excluded_root: str
) -> str | None:
    """Executável lógico da `TestPolicy` → caminho **absoluto** confiável, ou `None`.

    Determinística e sem `shutil.which` (que, no Windows/3.11, consulta o diretório corrente
    antes do `PATH`):

    * **nome simples** (`pytest`, `node.exe`): procurado só nas entradas **absolutas** de
      ``search_path`` — o `PATH` do ambiente confiável do host, nunca o do filho —, na ordem.
      No Windows, sem extensão lançável, tenta `.exe` e depois `.com`; nunca o `PATHEXT`.
    * **caminho absoluto** configurado: aceito como está, se for um executável válido.
    * qualquer outra forma — `./evil`, `../evil`, `sub/x`, `C:x`, `\\\\x`, UNC, *device
      namespace*, nome com `:` — é recusada, nunca resolvida relativamente.

    Em todos os casos o alvo precisa ser arquivo regular existente, lançável no SO (no Windows,
    `.exe`/`.com`; no POSIX, bit de execução), **não** `.bat`/`.cmd`, **não** um intérprete de
    shell (`cmd`, `powershell`, `bash`, `sh`, `env`… — pelo nome e pelo destino real), e **fora** de
    ``excluded_root`` (a worktree do run) tanto pelo caminho quanto pelo destino real. O caminho
    devolvido é o encontrado — não o `realpath`: um `bin/python` de venv é um link, e executar o
    destino perderia o venv.
    """
    if not isinstance(logical, str) or not logical or logical != logical.strip():
        return None
    if "\x00" in logical or _launch_name(logical).endswith(_SHELL_SCRIPT_SUFFIXES):
        return None

    form = classify_path_form(logical)
    candidates: list[str]
    if form is PathForm.ABSOLUTE_QUALIFIED and os.path.isabs(logical):
        parts = logical.replace("\\", "/").split("/")
        if any(part in (".", "..") for part in parts):
            return None
        candidates = [os.path.normpath(logical)]
    elif form is PathForm.RELATIVE and _is_bare_name(logical):
        directories = [entry for entry in (search_path or "").split(os.pathsep) if entry]
        candidates = [
            os.path.join(os.path.normpath(directory), name)
            for directory in directories
            if _is_absolute_dir(directory)
            for name in _candidate_names(logical)
        ]
    else:
        return None

    for candidate in candidates:
        if _is_trusted_executable(candidate, excluded_root):
            return candidate
    return None


# ------------------------------------------------------------------------------ ambiente


def _host_value(environment: Mapping[str, str], name: str) -> tuple[str, str] | None:
    """`(chave, valor)` do ambiente confiável para ``name``, ou `None` se ausente.

    No Windows a comparação é case-insensitive; duas chaves que colapsam no mesmo nome
    (`Path` e `PATH`) são **ambíguas** e recusadas — o SO escolheria por nós.
    """
    wanted = _fold(name)
    found = [(key, value) for key, value in environment.items() if _fold(key) == wanted]
    if len(found) > 1:
        raise _fail(TestRunnerFailureCode.INVALID_ENVIRONMENT)
    return found[0] if found else None


def _checked_allowlist(allowlist: tuple[str, ...]) -> tuple[str, ...]:
    """Nomes bem formados, sem duplicata (no Windows, por caixa) e **nenhum** proibido."""
    seen: set[str] = set()
    for name in allowlist:
        if not name or "=" in name or "\x00" in name or name != name.strip():
            raise _fail(TestRunnerFailureCode.INVALID_ENVIRONMENT)
        if is_forbidden_test_env_name(name):
            raise _fail(TestRunnerFailureCode.INVALID_ENVIRONMENT)
        folded = _fold(name)
        if folded in seen:
            raise _fail(TestRunnerFailureCode.INVALID_ENVIRONMENT)
        seen.add(folded)
    return allowlist


def _child_environment(allowlist: tuple[str, ...], trusted: Mapping[str, str]) -> dict[str, str]:
    """O ambiente **completo** do filho, construído do zero: allowlist ∩ ambiente confiável."""
    child: dict[str, str] = {}
    for name in allowlist:
        entry = _host_value(trusted, name)
        if entry is None:
            continue  # ausente no host: simplesmente não aparece
        key, value = entry
        if "\x00" in value:
            raise _fail(TestRunnerFailureCode.INVALID_ENVIRONMENT)
        child[key] = value
    return child


# --------------------------------------------------------------------------- política


def _require_supported(policy: TestPolicy) -> None:
    """`runner_id` desta implementação e política na forma exata que o schema aceita.

    A `TestPolicy` é um dataclass sem validação própria — quem valida é `parse_test_policy`,
    o dono único do schema. Aqui a política é **revalidada por ele** (ida e volta pelo
    documento canônico): uma instância montada à mão, com tipo ou valor fora do schema, é
    recusada em vez de chegar à `ProcessSpec`.
    """
    if policy.runner_id != GENERIC_SUBPROCESS_RUNNER_ID:
        raise _fail(TestRunnerFailureCode.UNSUPPORTED_RUNNER)
    if not (isinstance(policy.argv, tuple) and isinstance(policy.env_allowlist, tuple)):
        raise _fail(TestRunnerFailureCode.INVALID_POLICY)
    try:
        document = policy.as_document()
        reparsed = parse_test_policy(document)
    except (InvalidTestPolicy, TypeError, ValueError, AttributeError):
        raise _fail(TestRunnerFailureCode.INVALID_POLICY) from None
    if reparsed is None or reparsed.as_document() != document:
        raise _fail(TestRunnerFailureCode.INVALID_POLICY)


def _worktree_toplevel(bound: BoundWorkspace) -> str:
    """A raiz da worktree inteira: o workspace vinculado menos os componentes do prefixo."""
    toplevel = bound.root.path
    prefix = bound.resolved.workspace_prefix
    for _ in range(len(prefix.rstrip("/").split("/")) if prefix else 0):
        toplevel = os.path.dirname(toplevel)
    return toplevel


# ------------------------------------------------------------------------------ runner


def _summary(result: ProcessResult) -> TestSummary:
    """`EXITED` é resultado de teste; o resto é falha estruturada. Nenhum byte de saída sobe."""
    if result.outcome is ProcessOutcome.EXITED and result.exit_code is not None:
        return TestSummary(
            framework=GENERIC_SUBPROCESS_RUNNER_ID,
            exit_code=result.exit_code,
            passed=None,
            failed=None,
            skipped=None,
            duration_ms=result.duration_ms,
            output_ref=None,
        )
    code = {
        ProcessOutcome.TIMEOUT: TestRunnerFailureCode.TIMEOUT,
        ProcessOutcome.CANCELLED: TestRunnerFailureCode.CANCELLED,
    }.get(result.outcome, TestRunnerFailureCode.SUPERVISION_FAILED)
    raise _fail(code, tree_confirmed_dead=result.tree_confirmed_dead)


class LocalSubprocessTestRunner:
    """`TestRunner` de **vida de aplicação**. Sem estado de run: cada `run` resolve o próprio.

    ``resolver`` é a porta interna de binding (`WorkspaceResolver`), a mesma que a
    `ToolExecutorFactory` recebe. ``trusted_environment`` é o ambiente **confiável do host**
    (o composition root passa uma cópia do ambiente do backend): fonte do `PATH` para resolver
    o executável e dos valores das variáveis allowlisted. É copiado na construção; nada é lido
    de `os.environ` depois.
    """

    def __init__(
        self, resolver: WorkspaceResolver, *, trusted_environment: Mapping[str, str]
    ) -> None:
        if not callable(getattr(resolver, "resolve", None)):
            raise ContractViolation("resolver precisa expor resolve()")
        if not isinstance(trusted_environment, Mapping):
            raise ContractViolation("trusted_environment precisa ser um Mapping")
        frozen: dict[str, str] = {}
        for key, value in trusted_environment.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ContractViolation("trusted_environment precisa mapear str → str")
            frozen[key] = value
        self._resolver = resolver
        self._environment: Mapping[str, str] = MappingProxyType(frozen)

    def __repr__(self) -> str:  # nem ambiente nem caminho em log/traceback
        return "<LocalSubprocessTestRunner>"

    def run(self, request: TestRequest) -> TestSummary:
        require_instance("request", request, TestRequest)
        policy = request.test_policy
        _require_supported(policy)
        allowlist = _checked_allowlist(policy.env_allowlist)

        try:
            bound = bind_workspace(self._resolver, request.workspace_ref, request.run_scope)
        except ToolExecutorError:
            raise _fail(TestRunnerFailureCode.WORKSPACE_UNAVAILABLE) from None

        path_entry = _host_value(self._environment, "PATH")
        executable = resolve_trusted_executable(
            policy.executable,
            search_path=None if path_entry is None else path_entry[1],
            excluded_root=_worktree_toplevel(bound),
        )
        if executable is None:
            raise _fail(TestRunnerFailureCode.EXECUTABLE_UNAVAILABLE)

        try:
            spec = ProcessSpec(
                argv=(executable, *policy.argv),
                cwd=bound.root.path,
                env=_child_environment(allowlist, self._environment),
                timeout_s=policy.timeout_seconds,
                max_stdout_bytes=policy.max_stdout_bytes,
                max_stderr_bytes=policy.max_stderr_bytes,
            )
        except InvalidProcessSpec:
            raise _fail(TestRunnerFailureCode.INVALID_POLICY) from None

        # O único mecanismo de cancelamento é o do request: o Supervisor o consulta antes de
        # iniciar e a cada ciclo; resposta inválida ou exceção ⇒ `SUPERVISION_FAILED`.
        return _summary(run_supervised(spec, request.cancel_token.is_cancelled))


__all__ = [
    "GENERIC_SUBPROCESS_RUNNER_ID",
    "LocalSubprocessTestRunner",
    "resolve_trusted_executable",
]
