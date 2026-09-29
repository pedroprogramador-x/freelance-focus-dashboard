"""Contratos do Supervisor de Processos (E7.3): `ProcessSpec`, `ProcessOutcome`, `ProcessResult`.

Só stdlib e zero `app.*`: o pacote é folha em L1 e não conhece worktree, política, provider
nem banco. Quem chama resolve o `cwd` (a worktree, na E8), filtra o ambiente (allowlist da
`TestPolicy`) e mapeia o resultado para `RunStatus`/`failure_reason` — nada disso é daqui.

**Supervisão de processo não é sandbox.** Estes contratos descrevem o ciclo de vida de uma
árvore de processos — iniciar, esperar, encerrar e confirmar que nada sobreviveu. Eles não
impedem leitura de arquivos, escrita fora da worktree, rede, acesso a segredos, uso de APIs
do SO nem criação de processos por mecanismos externos à árvore (serviços, WMI, Agendador
de Tarefas, `setsid` no POSIX). Essas garantias pertencem a outras camadas ou não existem
na V1 ([04] §0 e §6).
"""

from __future__ import annotations

import math
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from types import MappingProxyType


class InvalidProcessSpec(ValueError):
    """`ProcessSpec` recusada na construção. Nenhum processo é criado."""


class StartFailed(Exception):
    """Interno: o backend não conseguiu pôr a árvore sob contenção.

    Quem levanta já desfez tudo o que criou; `tree_confirmed_dead` diz se isso foi
    verificado. Não faz parte da API pública — `run_supervised` a converte em
    `ProcessOutcome.SUPERVISION_FAILED`.
    """

    def __init__(self, detail: str, *, tree_confirmed_dead: bool) -> None:
        super().__init__(detail)
        self.detail = detail
        self.tree_confirmed_dead = tree_confirmed_dead


#: Extensões que o `CreateProcess` do Windows entrega implicitamente ao `cmd.exe`, com
#: regras de citação próprias — `argv` deixaria de ser estruturado. Recusadas em qualquer SO
#: para que a mesma `ProcessSpec` tenha a mesma validade nos dois ambientes.
_SHELL_SCRIPT_SUFFIXES = (".bat", ".cmd")


#: Semântica de caminho do SO corrente. No Windows, `\\foo` (relativo ao drive corrente) e
#: `C:foo` (relativo ao diretório corrente do drive `C:`) **não** são absolutos:
#: `PureWindowsPath.is_absolute` exige drive **e** raiz, ao contrário de `os.path.isabs` no
#: Python 3.11.
_NativePath: type[PurePath]
if sys.platform == "win32":
    _NativePath = PureWindowsPath
else:
    _NativePath = PurePosixPath


def _is_absolute(value: str) -> bool:
    """Absoluto **de verdade** no SO corrente (ver `_NativePath`)."""
    return _NativePath(value).is_absolute()


def _require_text(name: str, value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise InvalidProcessSpec(f"{name} precisa ser str")
    if "\x00" in value:
        raise InvalidProcessSpec(f"{name} contém NUL")
    if not allow_empty and not value:
        raise InvalidProcessSpec(f"{name} não pode ser vazio")
    return value


def _require_absolute(name: str, value: object) -> str:
    text = _require_text(name, value)
    if not _is_absolute(text):
        raise InvalidProcessSpec(f"{name} precisa ser um caminho absoluto")
    return text


def _require_positive_seconds(name: str, value: object, *, allow_zero: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidProcessSpec(f"{name} precisa ser número")
    if not math.isfinite(value):
        raise InvalidProcessSpec(f"{name} precisa ser finito")
    if value < 0 or (value == 0 and not allow_zero):
        raise InvalidProcessSpec(f"{name} precisa ser {'>= 0' if allow_zero else '> 0'}")


def _require_positive_int(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidProcessSpec(f"{name} precisa ser int")
    if value <= 0:
        raise InvalidProcessSpec(f"{name} precisa ser > 0")


def _executable_name(argv0: str) -> str:
    """Nome do executável como o Win32 o resolve: sem pontos e espaços finais."""
    return PureWindowsPath(argv0).name.rstrip(" .").lower()


def _validate_env(env: object) -> Mapping[str, str]:
    if not isinstance(env, Mapping):
        raise InvalidProcessSpec("env precisa ser um Mapping explícito (nada é herdado)")
    frozen: dict[str, str] = {}
    folded: set[str] = set()
    for key, value in env.items():
        _require_text("chave de env", key)
        _require_text(f"env[{key!r}]", value, allow_empty=True)
        if "=" in key:
            raise InvalidProcessSpec(f"chave de env com '=': {key!r}")
        # No Windows o bloco de ambiente é case-insensitive: `Path` e `PATH` juntos deixam a
        # escolha para o SO. Ambíguo é recusado.
        fold = key.upper() if sys.platform == "win32" else key
        if fold in folded:
            raise InvalidProcessSpec(f"chave de env duplicada: {key!r}")
        folded.add(fold)
        frozen[key] = value
    return MappingProxyType(frozen)


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    """O que executar e sob quais limites. Validada na construção; imutável.

    * `argv` — tupla de `str`; `argv[0]` é o executável, **absoluto**, e é também o que o
      SO abre (sem busca em `PATH` nem no diretório corrente). `.bat`/`.cmd` são recusados.
      Os demais elementos podem ser vazios e são entregues literalmente.
    * `cwd` — absoluto. Existência não é verificada aqui: `cwd` inexistente vira
      `SUPERVISION_FAILED` na execução.
    * `env` — o ambiente **completo** do processo. Nada é herdado do supervisor. No Windows,
      o chamador precisa incluir `SYSTEMROOT` se o programa depender dele.
    * `timeout_s`, `max_stdout_bytes`, `max_stderr_bytes` — estritamente positivos.
    * `kill_grace_s` — período entre `SIGTERM` e `SIGKILL` no POSIX. Ignorado no Windows,
      onde não há término cooperativo comprovado e a árvore é encerrada à força (D4).
    """

    argv: tuple[str, ...]
    cwd: str
    env: Mapping[str, str] = field(hash=False)
    timeout_s: float
    max_stdout_bytes: int
    max_stderr_bytes: int
    kill_grace_s: float = 2.0

    def __post_init__(self) -> None:
        if not isinstance(self.argv, tuple):
            raise InvalidProcessSpec("argv precisa ser tuple[str, ...] (nunca uma string única)")
        if not self.argv:
            raise InvalidProcessSpec("argv não pode ser vazio")
        _require_absolute("argv[0]", self.argv[0])
        for index, arg in enumerate(self.argv[1:], start=1):
            _require_text(f"argv[{index}]", arg, allow_empty=True)
        if _executable_name(self.argv[0]).endswith(_SHELL_SCRIPT_SUFFIXES):
            raise InvalidProcessSpec("argv[0] .bat/.cmd é recusado: executaria via cmd.exe")
        _require_absolute("cwd", self.cwd)
        object.__setattr__(self, "env", _validate_env(self.env))
        _require_positive_seconds("timeout_s", self.timeout_s)
        _require_positive_int("max_stdout_bytes", self.max_stdout_bytes)
        _require_positive_int("max_stderr_bytes", self.max_stderr_bytes)
        _require_positive_seconds("kill_grace_s", self.kill_grace_s, allow_zero=True)


class ProcessOutcome(str, Enum):
    """Por que a supervisão terminou.

    Precedência quando os eventos coincidem: **o processo já terminou > cancelamento >
    timeout**. "Já terminou" significa: o supervisor observou a saída do processo raiz
    **antes** de enviar o primeiro sinal de término. Uma saída posterior a esse ponto é
    consequência do cancelamento/timeout e não muda o desfecho.
    """

    EXITED = "exited"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    #: A árvore não pôde ser posta sob contenção, não pôde ser confirmada morta, ou a
    #: captura de saída não terminou. Fail closed: nunca há processo rodando sem contenção.
    SUPERVISION_FAILED = "supervision_failed"


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessResult:
    """Resultado determinístico. Nada é persistido e nada é redigido aqui.

    * `exit_code` — só em `EXITED` (código do processo raiz); `None` nos demais.
    * `stdout`/`stderr` — bytes crus, no máximo `max_*_bytes` cada.
    * `*_truncated` — o stream produziu mais do que o limite; o excedente foi drenado e
      descartado.
    * `output_complete` — os dois streams chegaram a EOF.
    * `tree_confirmed_dead` — **verificado**, não suposto: nenhum processo da árvore
      continua vivo. Sem processo criado, é verdadeiro por vacuidade.
    * `orphans_killed` — o raiz saiu por conta própria deixando descendentes vivos, que
      foram encerrados.
    * `detail` — diagnóstico curto; obrigatório em `SUPERVISION_FAILED`.

    Fora de `SUPERVISION_FAILED`, `tree_confirmed_dead` e `output_complete` são sempre
    verdadeiros: um resultado "bom" nunca esconde sobrevivente nem captura pendente.
    """

    outcome: ProcessOutcome
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    output_complete: bool
    duration_ms: int
    tree_confirmed_dead: bool
    orphans_killed: bool
    detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ProcessOutcome):
            raise ValueError("outcome precisa ser ProcessOutcome")
        exited = self.outcome is ProcessOutcome.EXITED
        has_code = isinstance(self.exit_code, int) and not isinstance(self.exit_code, bool)
        if exited != has_code or (not exited and self.exit_code is not None):
            raise ValueError("exit_code: int em EXITED, None nos demais")
        for name in ("stdout", "stderr"):
            if not isinstance(getattr(self, name), bytes):
                raise ValueError(f"{name} precisa ser bytes")
        for name in (
            "stdout_truncated",
            "stderr_truncated",
            "output_complete",
            "tree_confirmed_dead",
            "orphans_killed",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} precisa ser bool")
        if isinstance(self.duration_ms, bool) or not isinstance(self.duration_ms, int):
            raise ValueError("duration_ms precisa ser int")
        if self.duration_ms < 0:
            raise ValueError("duration_ms negativo")
        if self.detail is not None and not isinstance(self.detail, str):
            raise ValueError("detail precisa ser str ou None")
        if self.outcome is ProcessOutcome.SUPERVISION_FAILED:
            if not self.detail:
                raise ValueError("SUPERVISION_FAILED exige detail")
        elif not (self.tree_confirmed_dead and self.output_complete):
            raise ValueError("só SUPERVISION_FAILED admite árvore viva ou captura incompleta")
        if self.orphans_killed and self.outcome not in (
            ProcessOutcome.EXITED,
            ProcessOutcome.SUPERVISION_FAILED,
        ):
            raise ValueError("orphans_killed só quando o raiz saiu por conta própria")


def describe_os_error(exc: BaseException) -> str:
    """Diagnóstico curto e estável: tipo + errno/winerror, sem mensagem livre do SO."""
    parts = [type(exc).__name__]
    if isinstance(exc, OSError):
        winerror = getattr(exc, "winerror", None)
        if winerror is not None:
            parts.append(f"winerror={winerror}")
        elif exc.errno is not None:
            parts.append(f"errno={exc.errno}")
    return " ".join(parts)


__all__ = [
    "InvalidProcessSpec",
    "ProcessOutcome",
    "ProcessResult",
    "ProcessSpec",
]
