"""Backend Windows do Supervisor: Job Object + `CREATE_SUSPENDED` + `ResumeThread` (E7.3, D2).

Sequência de início — nenhuma instrução do processo roda fora do Job:

1. `CreateJobObjectW` + `SetInformationJobObject(JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)`.
2. `subprocess.Popen(..., creationflags=CREATE_SUSPENDED | CREATE_NO_WINDOW)`, sem shell,
   `argv` em lista e `executable=argv[0]` (o `CreateProcess` recebe `lpApplicationName`:
   sem busca em `PATH`, sem busca no diretório corrente, sem extensão implícita).
3. `AssignProcessToJobObject` e confirmação por `IsProcessInJob`.
4. Só então as threads do processo são retomadas: `CreateToolhelp32Snapshot` +
   `Thread32First`/`Thread32Next` localizam as threads cujo dono é o PID (o processo está
   suspenso, então o PID e os TIDs não podem ser reutilizados), `OpenThread` +
   `ResumeThread` as liberam. Apenas APIs Win32 documentadas — nada de `ntdll`.

Qualquer falha em 1–4 é fail closed: o que foi criado é encerrado e `StartFailed` sobe.
Como todo descendente nasce dentro do Job, "pai sai antes dos filhos" e "filho criado
durante o encerramento" não escapam: `TerminateJobObject` atinge todos.

Confirmação de morte. Depois do `TerminateJobObject`, a contabilidade do Job
(`ActiveProcesses`) e a lista de PIDs zeram **na hora**, alguns milissegundos antes de os
processos serem desmontados (medido na E7.3) — nenhuma das duas basta. Por isso, antes do
término, `kill`:

1. congela o Job (`ActiveProcessLimit = 1`): ninguém mais entra, os membros atuais seguem;
2. lê a lista **completa** de PIDs (`NumberOfProcessIdsInList == NumberOfAssignedProcesses`,
   ampliando o buffer quando parcial) até uma rodada não trazer PID novo;
3. para cada PID obtém evidência: handle `SYNCHRONIZE` de processo que `IsProcessInJob`
   confirma no Job, ou prova de que ele já não existe (`OpenProcess` →
   `ERROR_INVALID_PARAMETER`; `IsProcessInJob` bem-sucedido dizendo que não). Falha de API
   não é ausência de membro: invalida a captura.

`confirm_dead` só confirma com a captura completa, `ActiveProcesses == 0`, lista vazia e
todo handle capturado sinalizado; o resto é `False`, e o resultado vira
`supervision_failed`.

Se o próprio supervisor morrer, o SO fecha o handle do Job e `KILL_ON_JOB_CLOSE` encerra a
árvore. Um descendente que peça `CREATE_BREAKAWAY_FROM_JOB` continua no Job: o limite
`BREAKAWAY_OK` nunca é concedido.

Término (D4): o Windows não tem sinal cooperativo comprovado para uma árvore arbitrária
sem console compartilhado, então a árvore é encerrada à força e `kill_grace_s` é ignorado.

**Não é sandbox.** O Job controla ciclo de vida; não restringe arquivos, rede, segredos,
APIs do SO, nem processos criados por mecanismos externos à árvore (serviços, WMI, COM,
Agendador de Tarefas), que nunca pertencem ao Job.
"""

from __future__ import annotations

import contextlib
import ctypes
import msvcrt
import os
import subprocess
import sys
import time
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

from app.process_runtime.contracts import ProcessSpec, StartFailed, describe_os_error

# O resto do módulo só existe no Windows; o mypy também o trata assim em outras plataformas.
assert sys.platform == "win32"

# ----------------------------------------------------------------------------- constantes

_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000

_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_THREAD_SUSPEND_RESUME = 0x0002
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0

_TH32CS_SNAPTHREAD = 0x00000004
_ERROR_NO_MORE_FILES = 18
_ERROR_INVALID_PARAMETER = 87
_ERROR_MORE_DATA = 234
_UNICODE_PATH_CHARS = 32768
_RESUME_FAILED = 0xFFFFFFFF
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

#: Código de saída imposto por `TerminateJobObject`. Só diagnóstico: o desfecho nunca é
#: deduzido dele (ver a precedência em `contracts.ProcessOutcome`).
_TERMINATED_EXIT_CODE = 1

_PIPE_POLL_S = 0.01

#: `JOBOBJECT_BASIC_PROCESS_ID_LIST`: capacidade inicial, folga ao ampliar, teto defensivo
#: (Job maior que isto é recusado — fail closed) e número máximo de consultas.
_PID_LIST_INITIAL = 64
_PID_LIST_SLACK = 16
_PID_LIST_MAX = 1 << 20
_PID_LIST_ATTEMPTS = 16

#: Rodadas de captura: cada uma relê a lista completa; a captura termina quando uma rodada
#: não traz PID novo. Com o Job congelado, a segunda rodada já é estável.
_CAPTURE_ROUNDS = 8

# ---------------------------------------------------------------------------- estruturas


class _IoCounters(ctypes.Structure):
    _fields_ = (
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    )


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = (
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    )


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = (
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    )


class _BasicAccountingInformation(ctypes.Structure):
    _fields_ = (
        ("TotalUserTime", wintypes.LARGE_INTEGER),
        ("TotalKernelTime", wintypes.LARGE_INTEGER),
        ("ThisPeriodTotalUserTime", wintypes.LARGE_INTEGER),
        ("ThisPeriodTotalKernelTime", wintypes.LARGE_INTEGER),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    )


class _ThreadEntry32(ctypes.Structure):
    _fields_ = (
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    )


# ------------------------------------------------------------------------------- kernel32

#: Instância própria: `argtypes`/`restype` definidos aqui não vazam para `ctypes.windll`.
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _bind(name: str, restype: object, *argtypes: object) -> Any:
    function = getattr(_kernel32, name)
    function.restype = restype
    function.argtypes = list(argtypes)
    return function


_CreateJobObjectW = _bind("CreateJobObjectW", wintypes.HANDLE, wintypes.LPVOID, wintypes.LPCWSTR)
_SetInformationJobObject = _bind(
    "SetInformationJobObject",
    wintypes.BOOL,
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
)
_QueryInformationJobObject = _bind(
    "QueryInformationJobObject",
    wintypes.BOOL,
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.LPDWORD,
)
_AssignProcessToJobObject = _bind(
    "AssignProcessToJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE
)
_IsProcessInJob = _bind(
    "IsProcessInJob", wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE, wintypes.PBOOL
)
_TerminateJobObject = _bind("TerminateJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)
_OpenProcess = _bind("OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_OpenThread = _bind("OpenThread", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_WaitForSingleObject = _bind("WaitForSingleObject", wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
_ResumeThread = _bind("ResumeThread", wintypes.DWORD, wintypes.HANDLE)
_CloseHandle = _bind("CloseHandle", wintypes.BOOL, wintypes.HANDLE)
_CreateToolhelp32Snapshot = _bind(
    "CreateToolhelp32Snapshot", wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD
)
_Thread32First = _bind(
    "Thread32First", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)
)
_Thread32Next = _bind(
    "Thread32Next", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)
)
_QueryFullProcessImageNameW = _bind(
    "QueryFullProcessImageNameW",
    wintypes.BOOL,
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    wintypes.PDWORD,
)
_GetSystemDirectoryW = _bind("GetSystemDirectoryW", wintypes.UINT, wintypes.LPWSTR, wintypes.UINT)
_PeekNamedPipe = _bind(
    "PeekNamedPipe",
    wintypes.BOOL,
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.LPDWORD,
    wintypes.LPDWORD,
    wintypes.LPDWORD,
)


def _win_error(call: str) -> str:
    return f"{call} falhou (winerror={ctypes.get_last_error()})"


# ------------------------------------------------------------------------------------ job


def _create_job() -> int:
    job = _CreateJobObjectW(None, None)
    if not job:
        raise StartFailed(_win_error("CreateJobObjectW"), tree_confirmed_dead=True)
    info = _ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    configured = _SetInformationJobObject(
        job,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not configured:
        detail = _win_error("SetInformationJobObject")
        _CloseHandle(job)
        raise StartFailed(detail, tree_confirmed_dead=True)
    return int(job)


def _active_processes(job: int) -> int:
    info = _BasicAccountingInformation()
    ok = _QueryInformationJobObject(
        job,
        _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
        None,
    )
    if not ok:
        raise OSError(_win_error("QueryInformationJobObject"))
    return int(info.ActiveProcesses)


def _process_id_list_type(capacity: int) -> type[ctypes.Structure]:
    class _ProcessIdList(ctypes.Structure):
        _fields_ = (
            ("NumberOfAssignedProcesses", wintypes.DWORD),
            ("NumberOfProcessIdsInList", wintypes.DWORD),
            ("ProcessIdList", ctypes.c_size_t * capacity),
        )

    return _ProcessIdList


def _job_process_ids(job: int) -> list[int]:
    """Lista **completa** dos PIDs do Job (`JOBOBJECT_BASIC_PROCESS_ID_LIST`), ou `OSError`.

    Completa = a chamada teve sucesso **e** `NumberOfProcessIdsInList ==
    NumberOfAssignedProcesses`. Uma lista parcial — com sucesso e menos PIDs do que o Job
    tem, ou com `ERROR_MORE_DATA` — amplia o buffer e consulta de novo. Nunca devolve lista
    parcial como se fosse completa: resposta inconsistente, erro da API, lista que não
    estabiliza em `_PID_LIST_ATTEMPTS` consultas ou Job acima de `_PID_LIST_MAX` viram
    `OSError` — e quem chama falha fechado.
    """
    capacity = _PID_LIST_INITIAL
    for _ in range(_PID_LIST_ATTEMPTS):
        info: Any = _process_id_list_type(capacity)()
        ok = _QueryInformationJobObject(
            job,
            _JOB_OBJECT_BASIC_PROCESS_ID_LIST,
            ctypes.byref(info),
            ctypes.sizeof(info),
            None,
        )
        error = 0 if ok else ctypes.get_last_error()
        assigned = int(info.NumberOfAssignedProcesses)
        listed = int(info.NumberOfProcessIdsInList)
        if ok:
            if listed > capacity or listed > assigned:
                raise OSError(
                    f"QueryInformationJobObject inconsistente "
                    f"(listados={listed}, atribuídos={assigned}, capacidade={capacity})"
                )
            if listed == assigned:
                return [int(info.ProcessIdList[i]) for i in range(listed)]
        elif error != _ERROR_MORE_DATA:
            raise OSError(f"QueryInformationJobObject falhou (winerror={error})")
        # Lista parcial: cabe `assigned` com folga para quem entrar entre duas consultas.
        capacity = max(capacity * 2, assigned + _PID_LIST_SLACK)
        if capacity > _PID_LIST_MAX:
            raise OSError(f"Job com mais de {_PID_LIST_MAX} processos: lista recusada")
    raise OSError(f"lista de PIDs do Job incompleta após {_PID_LIST_ATTEMPTS} consultas")


def _freeze(job: int) -> None:
    """Impede novos membros: `ActiveProcessLimit = 1` com `KILL_ON_JOB_CLOSE` mantido.

    O limite só vale para **novas** associações — os membros atuais continuam vivos, e um
    `CreateProcess` dentro do Job passa a falhar com `ERROR_NOT_ENOUGH_QUOTA` sem que o filho
    chegue a existir (medido na correção do P2 da E7.3). É isso que torna a captura dos
    membros, feita logo depois, uma lista fechada até o `TerminateJobObject`.
    """
    info = _ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = (
        _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    )
    info.BasicLimitInformation.ActiveProcessLimit = 1
    ok = _SetInformationJobObject(
        job,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        raise OSError(_win_error("SetInformationJobObject(ActiveProcessLimit)"))


def _open_member(pid: int, job: int) -> int | None:
    """Evidência sobre um PID visto na lista do Job. Quatro casos, nunca confundidos:

    * **(D)** o processo existe e `IsProcessInJob` confirma que é do Job → devolve um handle
      `SYNCHRONIZE`, que `confirm_dead` vai esperar sinalizar;
    * **(C)** `OpenProcess` falha com `ERROR_INVALID_PARAMETER` → não existe processo com
      esse PID: o membro já terminou e foi desmontado → `None`;
    * **(B)** `IsProcessInJob` **funciona** e responde que não → o PID já é de outro
      processo (um processo nunca sai de um Job), então o membro não existe mais → `None`;
    * **(A)** qualquer outra falha de `OpenProcess`, ou falha da **chamada**
      `IsProcessInJob` → `OSError`: sem evidência, a árvore não pode ser confirmada.
    """
    handle = _OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == _ERROR_INVALID_PARAMETER:
            return None  # (C)
        raise OSError(f"OpenProcess({pid}) falhou (winerror={error})")  # (A)
    in_job = wintypes.BOOL()
    if not _IsProcessInJob(handle, job, ctypes.byref(in_job)):
        error = ctypes.get_last_error()
        _CloseHandle(handle)
        raise OSError(f"IsProcessInJob({pid}) falhou (winerror={error})")  # (A)
    if not in_job.value:
        _CloseHandle(handle)
        return None  # (B)
    return int(handle)  # (D)


def _system_console_host() -> str | None:
    buffer = ctypes.create_unicode_buffer(_UNICODE_PATH_CHARS)
    length = _GetSystemDirectoryW(buffer, len(buffer))
    if not length or length >= len(buffer):
        return None
    return os.path.normcase(os.path.join(buffer.value, "conhost.exe"))


#: `%SystemRoot%\System32\conhost.exe`, obtido do SO e não do ambiente.
_CONSOLE_HOST = _system_console_host()


def _image_path(pid: int) -> str | None:
    process = _OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(_UNICODE_PATH_CHARS)
        size = wintypes.DWORD(len(buffer))
        if not _QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value
    finally:
        _CloseHandle(process)


def _is_console_host(pid: int) -> bool:
    path = _image_path(pid)
    if _CONSOLE_HOST is None or path is None:
        return False
    return os.path.normcase(path) == _CONSOLE_HOST


def _assign(popen: subprocess.Popen[bytes], job: int) -> None:
    access = _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION
    process = _OpenProcess(access, False, popen.pid)
    if not process:
        raise StartFailed(_win_error("OpenProcess"), tree_confirmed_dead=False)
    try:
        if not _AssignProcessToJobObject(job, process):
            raise StartFailed(_win_error("AssignProcessToJobObject"), tree_confirmed_dead=False)
        in_job = wintypes.BOOL()
        if not _IsProcessInJob(process, job, ctypes.byref(in_job)):
            raise StartFailed(_win_error("IsProcessInJob"), tree_confirmed_dead=False)
        if not in_job.value:
            raise StartFailed("processo não confirmado dentro do Job", tree_confirmed_dead=False)
    finally:
        _CloseHandle(process)


def _thread_ids(pid: int) -> list[int]:
    snapshot = _CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if not snapshot or snapshot == _INVALID_HANDLE_VALUE:
        raise StartFailed(_win_error("CreateToolhelp32Snapshot"), tree_confirmed_dead=False)
    try:
        entry = _ThreadEntry32()
        entry.dwSize = ctypes.sizeof(_ThreadEntry32)
        found: list[int] = []
        more = _Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == pid:
                found.append(int(entry.th32ThreadID))
            entry.dwSize = ctypes.sizeof(_ThreadEntry32)
            more = _Thread32Next(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != _ERROR_NO_MORE_FILES:
            raise StartFailed(_win_error("Thread32Next"), tree_confirmed_dead=False)
        return found
    finally:
        _CloseHandle(snapshot)


def _resume(pid: int) -> None:
    """Retoma as threads do processo suspenso. Exige ao menos uma que estava suspensa."""
    thread_ids = _thread_ids(pid)
    if not thread_ids:
        raise StartFailed("nenhuma thread do processo para retomar", tree_confirmed_dead=False)
    resumed = False
    for tid in thread_ids:
        thread = _OpenThread(_THREAD_SUSPEND_RESUME, False, tid)
        if not thread:
            raise StartFailed(_win_error("OpenThread"), tree_confirmed_dead=False)
        try:
            previous = _ResumeThread(thread)
            if previous == _RESUME_FAILED:
                raise StartFailed(_win_error("ResumeThread"), tree_confirmed_dead=False)
            resumed = resumed or previous >= 1
        finally:
            _CloseHandle(thread)
    if not resumed:
        # O processo não estava suspenso: rodou antes de entrar no Job. Fail closed.
        raise StartFailed("processo não estava suspenso ao ser retomado", tree_confirmed_dead=False)


# --------------------------------------------------------------------------------- árvore


class ProcessTree:
    """A árvore de um `ProcessSpec`, contida num Job. Métodos idempotentes."""

    def __init__(self, popen: subprocess.Popen[bytes], job: int) -> None:
        self.popen = popen
        self._job: int | None = job
        self._confirmed_dead = False
        self._members: list[int] = []
        self._members_captured = False
        self._members_failed = False

    def _capture_members(self, job: int) -> None:
        """Congela o Job e reúne evidência sobre **todo** membro, antes do término.

        Depois do `TerminateJobObject` a lista do Job e o `ActiveProcesses` zeram na hora,
        alguns milissegundos antes de os processos serem desmontados — a evidência precisa
        ser colhida antes. Em rodadas: cada uma lê a lista **completa** e classifica os PIDs
        ainda não vistos (`_open_member`); a captura termina quando uma rodada não traz PID
        novo. Qualquer falha — congelar, listar, classificar, ou lista que não estabiliza em
        `_CAPTURE_ROUNDS` — marca a captura como falha e `confirm_dead` nunca confirma. O
        término acontece de qualquer jeito.
        """
        self._members_captured = True
        seen: set[int] = set()
        try:
            _freeze(job)
            listed_before = False
            for _ in range(_CAPTURE_ROUNDS):
                new = [pid for pid in _job_process_ids(job) if pid not in seen]
                if not new and listed_before:
                    return
                for pid in new:
                    seen.add(pid)
                    handle = _open_member(pid, job)
                    if handle is not None:
                        self._members.append(handle)
                listed_before = True
            raise OSError(f"lista de membros do Job não estabilizou em {_CAPTURE_ROUNDS} rodadas")
        except Exception:
            # Qualquer falha (não só `OSError`): sem evidência completa não há confirmação, e
            # a exceção não pode impedir o `TerminateJobObject` que `kill` faz em seguida.
            self._members_failed = True

    def _release_members(self) -> None:
        members, self._members = self._members, []
        for handle in members:
            _CloseHandle(handle)

    def orphans_alive(self) -> bool:
        """Chamado quando o raiz já saiu: resta no Job algum processo além do console host?

        `CREATE_NO_WINDOW` dá ao raiz um console próprio, e o `conhost.exe` desse console
        roda **dentro** do Job e sobrevive alguns milissegundos à saída do raiz. Ele não é um
        descendente deixado para trás — é infraestrutura do console — e é identificado pelo
        caminho da imagem no diretório de sistema, não por tempo de espera. Processo que não
        pôde ser identificado conta como órfão (sinaliza em vez de esconder). Todos, console
        host incluído, são encerrados do mesmo jeito por `kill`.
        """
        if self._job is None or self._confirmed_dead:
            return False
        return any(not _is_console_host(pid) for pid in _job_process_ids(self._job))

    def kill(self, grace_s: float) -> None:
        """Encerra a árvore inteira. `grace_s` é ignorado no Windows (D4)."""
        del grace_s
        if self._job is None or self._confirmed_dead:
            return
        if not self._members_captured:
            self._capture_members(self._job)
        if not _TerminateJobObject(self._job, _TERMINATED_EXIT_CODE):
            raise OSError(_win_error("TerminateJobObject"))

    def confirm_dead(self, timeout_s: float) -> bool:
        """`True` só com evidência de término de **todo** membro, até `timeout_s`:

        1. `ActiveProcesses == 0` **e** uma lista completa relida está vazia — necessários,
           não suficientes: os dois zeram antes de os processos serem desmontados;
        2. todo handle capturado está sinalizado (`WaitForSingleObject`);
        3. o raiz foi colhido;
        4. a captura feita por `kill` foi completa — Job congelado, lista completa e estável,
           todo PID classificado sem falha de API.

        Sem o item 4 a resposta é sempre `False`, mas 1–3 ainda são esperados: o raiz é
        colhido e nada fica pendurado só porque a confirmação não pôde ser dada. Falha de
        API aqui sobe como `OSError`, que o supervisor trata como não confirmado.
        """
        if self._confirmed_dead or self._job is None:
            return self._confirmed_dead
        if not self._members_captured:
            return False  # sem `kill` não houve término a confirmar
        deadline = time.monotonic() + timeout_s
        while _active_processes(self._job) > 0 or _job_process_ids(self._job):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)
        for handle in self._members:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if _WaitForSingleObject(handle, remaining_ms) != _WAIT_OBJECT_0:
                return False
        try:
            # `ActiveProcesses == 0` já implica o raiz terminado; isto só o colhe.
            self.popen.wait(timeout=max(deadline - time.monotonic(), 1.0))
        except subprocess.TimeoutExpired:
            return False
        if self._members_failed:
            return False  # algum membro ficou sem evidência de término: nunca confirmar
        self._confirmed_dead = True
        self._release_members()
        return True

    def close(self) -> None:
        """Fecha o handle do Job. Se algo ainda vivesse, `KILL_ON_JOB_CLOSE` o encerraria."""
        self._release_members()
        job, self._job = self._job, None
        if job is not None:
            _CloseHandle(job)


def _abort(popen: subprocess.Popen[bytes] | None, job: int) -> bool:
    """Desfaz um início que falhou. Devolve se o processo criado foi confirmado morto."""
    _TerminateJobObject(job, _TERMINATED_EXIT_CODE)
    dead = True
    if popen is not None:
        if popen.poll() is None:
            with contextlib.suppress(OSError):
                popen.kill()  # cobre o caso de ainda não estar no Job
        try:
            popen.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            dead = False
        for stream in (popen.stdout, popen.stderr):
            if stream is not None:
                stream.close()
    _CloseHandle(job)
    return dead


def start(spec: ProcessSpec) -> ProcessTree:
    """Cria o processo **já contido** no Job, ou levanta `StartFailed` sem deixar nada vivo."""
    job = _create_job()
    popen: subprocess.Popen[bytes] | None = None
    try:
        try:
            popen = subprocess.Popen(  # noqa: S603 — argv estruturado, sem shell, executável absoluto
                list(spec.argv),
                executable=spec.argv[0],
                cwd=spec.cwd,
                env=dict(spec.env),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=_CREATE_SUSPENDED | _CREATE_NO_WINDOW,
                close_fds=True,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            detail = f"processo não pôde ser criado: {describe_os_error(exc)}"
            raise StartFailed(detail, tree_confirmed_dead=True) from exc
        _assign(popen, job)
        _resume(popen.pid)
    except BaseException as exc:
        dead = _abort(popen, job)
        if isinstance(exc, StartFailed):
            raise StartFailed(exc.detail, tree_confirmed_dead=dead) from None
        if isinstance(exc, Exception):
            detail = f"falha inesperada ao iniciar: {describe_os_error(exc)}"
            raise StartFailed(detail, tree_confirmed_dead=dead) from exc
        raise
    return ProcessTree(popen, job)


# ------------------------------------------------------------------------------ leitura


def readable_waiter(fd: int) -> Callable[[float], bool]:
    """`wait(timeout_s)`: `True` quando um `os.read(fd)` não vai bloquear (dados ou EOF).

    Pipes anônimos do Windows não funcionam com `select`; `PeekNamedPipe` informa quantos
    bytes há sem consumir nada. Falha (tipicamente `ERROR_BROKEN_PIPE`: todo escritor
    fechou) também devolve `True` — o `os.read` seguinte devolve `b""` (EOF) ou levanta.
    """
    handle = msvcrt.get_osfhandle(fd)

    def wait(timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        available = wintypes.DWORD()
        while True:
            if not _PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
                return True
            if available.value:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_PIPE_POLL_S)

    return wait
