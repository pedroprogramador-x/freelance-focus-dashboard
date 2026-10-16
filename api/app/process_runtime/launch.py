"""Observer e gate de lançamento do Supervisor (E8.4.5 / I2) — stdlib apenas, folha em L1.

Tudo aqui é **aditivo**: sem escopo instalado, `run_supervised` se comporta exatamente como na E7.3.
Este pacote não conhece reserva, banco nem `orchestrator`: o observer é uma porta estrutural, e quem
a implementa (o ledger durável, I3+) traduz os nomes neutros daqui para os do contrato do I1.

## Um lançamento protegido

Quando existe um `LaunchScope` instalado, **todo** lançamento supervisionado do processo é
protegido; não há fallback para `Popen` sem registro. A ordem é única, nos dois SOs:

```
gate.acquire        (prazo tipado; falha ⇒ recusa, sem intent e sem processo)
  stop?             (sim ⇒ recusa, sem intent)
  observer.intent   (persistido ANTES de qualquer criação; falha ⇒ recusa, sem processo)
  stop?             (sim ⇒ `ended(REFUSED_BEFORE_CREATION)` e recusa — ainda sem Popen)
  ticket.cross()    (imediatamente antes da primeira instrução que cria o processo)
  Popen → observer.created(identidade) → contenção confirmada → observer.contained → resume
gate.release        (o gate NÃO fica preso durante a vida do processo)
  ...supervisão...
observer.ended      (confirmado, não confirmado ou recusado)
```

* A seção protegida vai do primeiro `acquire` até o `resume`: criação, associação e contenção.
* Falha do observer em `created` ou `contained` aborta o processo **antes** do `resume`.
* Falha do observer em `ended` converte o resultado em `SUPERVISION_FAILED`: a linha do ledger
  ficou aberta e isso nunca pode parecer sucesso.
* O que não pôde ser comprovado vira `TREE_UNCONFIRMED`/`LEDGER_WRITE_FAILED`/`POPEN_RAISED`, nunca
  um encerramento inventado. Exceção de `Popen` **não** comprova ausência de processo.

## Linearização do fechamento

`LaunchGate.close_and_linearize` pede `stop` (irreversível, sem lock), adquire o mesmo mutex da
seção de lançamento e só então marca `closing` e executa o `commit` (a transição `active→draining`
do banco, no futuro). Ponto de linearização **L** = o retorno do `commit` com o mutex tomado.
Nenhum lançamento cruza a fronteira depois de L; os que cruzaram antes terminaram a seção antes de
L começar. Prazo esgotado ⇒ `GATE_TIMEOUT`: L **não** ocorreu e ninguém pode declarar o fechamento.

## Ordem de locks (verificável)

`executor (10) < gate (20) < transação SQLite (30)`: só se adquire um rank estritamente maior que
todos os que a thread já segura. `acquire` do gate, e as chamadas do observer (que abrem
transação), são conferidas por uma pilha por thread; violação ⇒ `LockOrderViolation` ⇒ recusa
fechada. A ordem é a que a v2.1 fixou: nunca se pega o gate com transação aberta.
"""

from __future__ import annotations

import contextlib
import math
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Generic, Protocol, TypeVar

#: Perfis de contenção gravados no ledger. O prefixo `win_job_v1` implica `KILL_ON_JOB_CLOSE` sem
#: breakaway e handle do Job não herdável (propriedades do backend, fixadas por teste).
PROFILE_WIN_JOB_V1 = "win_job_v1"
PROFILE_POSIX_PGRP_V1 = "posix_pgrp_v1"

#: Prazo padrão da seção de lançamento (e do fechamento): bem acima do necessário (criar e conter
#: um processo leva milissegundos) e bem abaixo de qualquer prazo de tarefa.
DEFAULT_GATE_TIMEOUT_S = 10.0

_T = TypeVar("_T")


# ------------------------------------------------------------------- vocabulários neutros


class LaunchPlatform(str, Enum):
    WIN32 = "win32"
    POSIX = "posix"


class LaunchEnd(str, Enum):
    """Como o lançamento terminou, em vocabulário neutro. Os valores são idênticos aos de
    `EndBasis` do contrato do I1 (um teste os cruza)."""

    REFUSED_BEFORE_CREATION = "refused_before_creation"
    TREE_CONFIRMED_DEAD = "tree_confirmed_dead"
    TREE_UNCONFIRMED = "tree_unconfirmed"
    POPEN_RAISED = "popen_raised"
    LEDGER_WRITE_FAILED = "ledger_write_failed"


class LaunchRefusal(str, Enum):
    """Por que um lançamento protegido foi recusado **sem criar processo**."""

    GATE_TIMEOUT = "gate_timeout"
    STOPPED = "stopped"
    INTENT_FAILED = "intent_failed"
    SCOPE_MISSING = "scope_missing"
    LOCK_ORDER = "lock_order"


REFUSAL_DETAIL_PREFIX = "launch_refused:"


@dataclass(frozen=True, slots=True)
class LaunchIntent:
    """O que o observer recebe em `intent`: nunca `argv`, `cwd` nem ambiente (podem ter segredo)."""

    exe_name: str
    platform: LaunchPlatform
    profile: str

    def __post_init__(self) -> None:
        if type(self.exe_name) is not str or not self.exe_name:
            raise TypeError("exe_name: esperado str não vazio")
        if type(self.platform) is not LaunchPlatform:
            raise TypeError("platform: esperado LaunchPlatform")
        if self.profile not in (PROFILE_WIN_JOB_V1, PROFILE_POSIX_PGRP_V1):
            raise ValueError("perfil de contenção desconhecido")


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """Identidade do processo criado: PID e um token de início (FILETIME de criação no Windows,
    ticks de início do `/proc` no Linux, `"unavailable"` onde não há). O PID sozinho nunca basta."""

    pid: int
    start_token: str

    def __post_init__(self) -> None:
        if type(self.pid) is not int or self.pid < 1:
            raise TypeError("pid: esperado int >= 1")
        if type(self.start_token) is not str or not self.start_token:
            raise TypeError("start_token: esperado str não vazio")


class LaunchObserver(Protocol):
    """Porta de registro dos eventos de um lançamento protegido.

    `intent` devolve um *handle* opaco que o supervisor repassa às demais chamadas. Qualquer
    exceção em `intent`/`created`/`contained` impede o lançamento (ou aborta o processo antes do
    `resume`); em `ended` deixa a linha aberta. Todas rodam **de posse do gate** (exceto `ended`) e
    abrem, se quiserem, uma transação SQLite própria (rank 30).
    """

    def intent(self, intent: LaunchIntent) -> object: ...

    def created(self, handle: object, identity: ProcessIdentity) -> None: ...

    def contained(self, handle: object) -> None: ...

    def ended(self, handle: object, end: LaunchEnd) -> None: ...


# ------------------------------------------------------------------- ordem de locks


class LockRank(IntEnum):
    EXECUTOR = 10
    GATE = 20
    SQLITE = 30


class LockOrderViolation(RuntimeError):
    """Tentativa de adquirir um rank que não é estritamente maior que o já segurado."""


_held = threading.local()


def _stack() -> list[LockRank]:
    stack: list[LockRank] | None = getattr(_held, "stack", None)
    if stack is None:
        stack = []
        _held.stack = stack
    return stack


def held_ranks() -> tuple[LockRank, ...]:
    """Os ranks que a thread corrente segura, do mais externo ao mais interno."""
    return tuple(_stack())


def _push(rank: LockRank) -> None:
    stack = _stack()
    if stack and stack[-1] >= rank:
        raise LockOrderViolation(
            f"adquirir {rank.name} segurando {stack[-1].name} viola a ordem de locks"
        )
    stack.append(rank)


def _pop(rank: LockRank) -> None:
    stack = _stack()
    if not stack or stack[-1] is not rank:
        raise LockOrderViolation(f"liberar {rank.name} fora de ordem")
    stack.pop()


@contextlib.contextmanager
def ranked(rank: LockRank) -> Iterator[None]:
    """Declara que a thread entra num recurso de `rank` (p.ex. uma transação SQLite)."""
    _push(rank)
    try:
        yield
    finally:
        _pop(rank)


# ------------------------------------------------------------------- gate


class LinearizeStatus(str, Enum):
    LINEARIZED = "linearized"
    GATE_TIMEOUT = "gate_timeout"
    COMMIT_FAILED = "commit_failed"


@dataclass(frozen=True, slots=True)
class Linearization(Generic[_T]):
    """Resultado de `close_and_linearize`. Só `LINEARIZED` autoriza declarar o fechamento."""

    status: LinearizeStatus
    value: _T | None = None
    error: Exception | None = None

    @property
    def linearized(self) -> bool:
        return self.status is LinearizeStatus.LINEARIZED


def _valid_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("prazo precisa ser um número finito > 0")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("prazo precisa ser um número finito > 0")
    return min(number, threading.TIMEOUT_MAX)


class LaunchGate:
    """Um mutex **não reentrante**, um `stop` irreversível e um `closing` que só muda sob o mutex.

    Não há `reset`: um gate novo nasce por reserva. A seção protegida é curta (criação e
    contenção); o mutex nunca é segurado durante a vida do processo.

    Três coisas distintas, que não se confundem:

    * `closed is True`: a **admissão** está bloqueada, irreversivelmente (`closing` foi marcado
      sob o mutex). Não diz se o fechamento foi confirmado.
    * `LinearizeStatus.LINEARIZED`: o fechamento está **confirmado pelo commit** (L). Só este
      status autoriza declarar o fechamento.
    * `LinearizeStatus.COMMIT_FAILED`: nenhum fechamento foi confirmado, mas `closed` já é `True`
      e os lançamentos continuam bloqueados (fail closed).

    Logo `closed` pode ser `True` sem `LINEARIZED`; o inverso nunca ocorre.
    """

    def __init__(self) -> None:
        self._section = threading.Lock()
        self._stop = threading.Event()
        self._closing = False

    def request_stop(self) -> None:
        """Irreversível e sem lock: qualquer lançamento que observar volta sem criar processo."""
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self.is_stopped()

    def is_stopped(self) -> bool:
        """O estado **agora** (método, não propriedade: o valor muda entre duas leituras)."""
        return self._stop.is_set() or self._closing

    @property
    def closed(self) -> bool:
        """`True` quando a admissão está bloqueada de forma irreversível (`closing` marcado).

        Isto **não** é a confirmação do fechamento: com `COMMIT_FAILED` o gate fica `closed`
        sem que o fechamento tenha sido confirmado. A confirmação é `LINEARIZED`.
        """
        return self._closing

    def acquire(self, timeout_s: float) -> bool:
        """Adquire a seção. `False` no prazo esgotado. Viola a ordem ⇒ `LockOrderViolation`."""
        timeout = _valid_timeout(timeout_s)
        _push(LockRank.GATE)  # confere a ordem ANTES de bloquear
        try:
            got = self._section.acquire(timeout=timeout)
        except BaseException:
            _pop(LockRank.GATE)
            raise
        if not got:
            _pop(LockRank.GATE)
        return got

    def release(self) -> None:
        _pop(LockRank.GATE)
        self._section.release()

    def close_and_linearize(self, timeout_s: float, commit: Callable[[], _T]) -> Linearization[_T]:
        """Pede `stop`, toma a seção, marca `closing` e executa `commit` (transação, rank SQLITE).

        * `LINEARIZED`: `commit` retornou — este é L, o fechamento está confirmado pelo commit.
          Nenhum lançamento novo existe depois.
        * `GATE_TIMEOUT`: a seção não foi obtida no prazo (lançador travado). L **não** ocorreu e
          `closed` continua `False`; o `stop` já vale e recusa qualquer lançador que acorde.
        * `COMMIT_FAILED`: `commit` levantou. `closed` fica `True` (fail closed: nenhum
          lançamento novo), mas NENHUM fechamento foi confirmado e L não ocorreu.
        """
        self.request_stop()
        if not self.acquire(timeout_s):
            return Linearization(LinearizeStatus.GATE_TIMEOUT)
        try:
            self._closing = True
            try:
                with ranked(LockRank.SQLITE):
                    value = commit()
            except Exception as exc:
                return Linearization(LinearizeStatus.COMMIT_FAILED, error=exc)
            return Linearization(LinearizeStatus.LINEARIZED, value=value)
        finally:
            self.release()


# ------------------------------------------------------------------- escopo ambiente


@dataclass(frozen=True, slots=True)
class LaunchScope:
    """Observer + gate de **uma** tentativa protegida."""

    observer: LaunchObserver
    gate: LaunchGate
    enter_timeout_s: float = DEFAULT_GATE_TIMEOUT_S

    def __post_init__(self) -> None:
        for method in ("intent", "created", "contained", "ended"):
            if not callable(getattr(self.observer, method, None)):
                raise TypeError(f"observer precisa expor {method}()")
        if type(self.gate) is not LaunchGate:
            raise TypeError("gate: esperado LaunchGate")
        _valid_timeout(self.enter_timeout_s)


class ScopeAlreadyInstalled(RuntimeError):
    """Só uma tentativa protegida por processo (`max_parallel_agents = 1`)."""


_scope_lock = threading.Lock()
_current_scope: LaunchScope | None = None


def current_scope() -> LaunchScope | None:
    return _current_scope


@contextlib.contextmanager
def install_scope(scope: LaunchScope) -> Iterator[LaunchScope]:
    """Instala o escopo ambiente e o **remove sempre** ao sair (inclusive por exceção).

    Dois escopos simultâneos são recusados. Ao sair, o gate recebe `request_stop`: uma referência
    esquecida a ele não volta a autorizar nada.
    """
    global _current_scope
    if type(scope) is not LaunchScope:
        raise TypeError("scope: esperado LaunchScope")
    with _scope_lock:
        if _current_scope is not None:
            raise ScopeAlreadyInstalled("já existe um escopo de lançamento instalado")
        _current_scope = scope
    try:
        yield scope
    finally:
        with _scope_lock:
            if _current_scope is scope:
                _current_scope = None
        scope.gate.request_stop()


def _reset_scope_for_tests() -> LaunchScope | None:
    """Higiene de teste: remove um escopo vazado e o devolve (para o teste falhar alto)."""
    global _current_scope
    with _scope_lock:
        leaked, _current_scope = _current_scope, None
    if leaked is not None:
        leaked.gate.request_stop()
    return leaked


# ------------------------------------------------------------------- lançamento protegido


class _Recorded(IntEnum):
    NONE = 0
    INTENT = 1
    CREATED = 2
    CONTAINED = 3


class _TicketState(str, Enum):
    INTENT = "intent"
    CROSSING = "crossing"
    REFUSED = "refused"


class _Ticket:
    """`INTENT → CROSSING` ou `INTENT → REFUSED`.

    Recusar depois de cruzar seria uma mentira possível (o processo pode já existir): levanta.
    Mesma regra do `LaunchTicket` do I1."""

    __slots__ = ("state",)

    def __init__(self) -> None:
        self.state = _TicketState.INTENT

    def cross(self) -> None:
        if self.state is not _TicketState.INTENT:
            raise RuntimeError("ticket já decidido")
        self.state = _TicketState.CROSSING

    def refuse(self) -> LaunchEnd:
        if self.state is not _TicketState.INTENT:
            raise RuntimeError("recusa depois do cruzamento é inválida")
        self.state = _TicketState.REFUSED
        return LaunchEnd.REFUSED_BEFORE_CREATION


class StartHooks:
    """O que os backends chamam, nos pontos exatos do nascimento do processo."""

    def __init__(self, launch: ProtectedLaunch) -> None:
        self._launch = launch

    def created(self, pid: int, token_factory: Callable[[], str]) -> None:
        self._launch._on_created(pid, token_factory)

    def contained(self) -> None:
        self._launch._on_contained()


class ProtectedLaunch:
    """Máquina de estados de **um** lançamento protegido. Usada só pelo supervisor."""

    def __init__(self, scope: LaunchScope, intent: LaunchIntent) -> None:
        self._scope = scope
        self._intent = intent
        self._ticket = _Ticket()
        self._handle: object = None
        self._recorded = _Recorded.NONE
        self._process_exists = False
        self._holding = False
        self.hooks = StartHooks(self)

    # ---- admissão (S0–S3) ----------------------------------------------------------------
    def admit(self) -> LaunchRefusal | None:
        """Devolve `None` com o gate **adquirido** (liberar com `release_gate`) ou o motivo da
        recusa, caso em que nenhum processo foi criado e o gate não está segurado."""
        gate = self._scope.gate
        try:
            got = gate.acquire(self._scope.enter_timeout_s)
        except LockOrderViolation:
            return LaunchRefusal.LOCK_ORDER
        if not got:
            return LaunchRefusal.GATE_TIMEOUT
        self._holding = True
        try:
            if gate.is_stopped():
                self.release_gate()
                return LaunchRefusal.STOPPED
            try:
                with ranked(LockRank.SQLITE):
                    self._handle = self._scope.observer.intent(self._intent)
            except LockOrderViolation:
                self.release_gate()
                return LaunchRefusal.LOCK_ORDER
            except Exception:
                self.release_gate()
                return LaunchRefusal.INTENT_FAILED
            self._recorded = _Recorded.INTENT
            if gate.is_stopped():  # a fronteira: intent já persistido, Popen ainda não tentado
                self._ticket.refuse()
                self._record_end(LaunchEnd.REFUSED_BEFORE_CREATION)
                self.release_gate()
                return LaunchRefusal.STOPPED
            self._ticket.cross()
            return None
        except BaseException:
            self.release_gate()
            raise

    def release_gate(self) -> None:
        if self._holding:
            self._holding = False
            self._scope.gate.release()

    # ---- hooks do backend ----------------------------------------------------------------
    def _on_created(self, pid: int, token_factory: Callable[[], str]) -> None:
        self._process_exists = True  # o processo existe, mesmo que o registro falhe
        identity = ProcessIdentity(pid, token_factory())
        with ranked(LockRank.SQLITE):
            self._scope.observer.created(self._handle, identity)
        self._recorded = _Recorded.CREATED

    def _on_contained(self) -> None:
        with ranked(LockRank.SQLITE):
            self._scope.observer.contained(self._handle)
        self._recorded = _Recorded.CONTAINED

    # ---- encerramento --------------------------------------------------------------------
    def end_for_start_failure(self, tree_confirmed_dead: bool) -> LaunchEnd:
        """A base de término de um início que falhou. Nada além do que foi registrado é afirmado."""
        if not self._process_exists:
            return LaunchEnd.POPEN_RAISED  # NÃO comprova ausência de processo
        if self._recorded < _Recorded.CREATED:
            return LaunchEnd.LEDGER_WRITE_FAILED  # existe processo que o ledger nunca viu
        return LaunchEnd.TREE_CONFIRMED_DEAD if tree_confirmed_dead else LaunchEnd.TREE_UNCONFIRMED

    def end_for_result(self, tree_confirmed_dead: bool) -> LaunchEnd:
        if self._recorded < _Recorded.CREATED:
            return LaunchEnd.LEDGER_WRITE_FAILED
        return LaunchEnd.TREE_CONFIRMED_DEAD if tree_confirmed_dead else LaunchEnd.TREE_UNCONFIRMED

    def record_end(self, end: LaunchEnd) -> bool:
        return self._record_end(end)

    def _record_end(self, end: LaunchEnd) -> bool:
        try:
            with ranked(LockRank.SQLITE):
                self._scope.observer.ended(self._handle, end)
        except Exception:
            return False  # a linha fica aberta: conservador
        return True


def exe_name_of(argv0: str) -> str:
    """Só o nome do binário (sem diretório, sem argumentos)."""
    return argv0.replace("\\", "/").rsplit("/", 1)[-1] or "unknown"


__all__ = [
    "DEFAULT_GATE_TIMEOUT_S",
    "PROFILE_POSIX_PGRP_V1",
    "PROFILE_WIN_JOB_V1",
    "REFUSAL_DETAIL_PREFIX",
    "LaunchEnd",
    "LaunchGate",
    "LaunchIntent",
    "LaunchObserver",
    "LaunchPlatform",
    "LaunchRefusal",
    "LaunchScope",
    "Linearization",
    "LinearizeStatus",
    "LockOrderViolation",
    "LockRank",
    "ProcessIdentity",
    "ProtectedLaunch",
    "ScopeAlreadyInstalled",
    "StartHooks",
    "current_scope",
    "exe_name_of",
    "held_ranks",
    "install_scope",
    "ranked",
]
