"""E8.3 — Test Runner real (`generic-subprocess-v1`) governado só pela `TestPolicy`.

Processos **reais** onde é seguro e determinístico: o executável de teste é o próprio Python da
suíte (`sys.executable`), resolvido pelo runner a partir de um `PATH` confiável **injetado** —
nada de `npm`, console script de `pytest`, rede, provider ou chave de API. A "worktree" aqui é um
diretório temporário com o binding explícito do `InMemoryWorkspaceResolver`: o runner não
depende de Git (a verificação pós-execução tem suíte própria).

Mapa: TR1–TR20 do planejamento E8.3, mais a resolução do executável (E1–E5) e os contrafactuais
M1–M7, M13–M15 descritos em cada teste.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from app.agent_runtime import (
    CancelToken,
    TestRequest,
    TestRunnerFailure,
    TestRunnerFailureCode,
    TestSummary,
)
from app.agent_runtime import TestRunner as RunnerPort
from app.agent_runtime.runners import (
    GENERIC_SUBPROCESS_RUNNER_ID,
    LocalSubprocessTestRunner,
    local_subprocess,
    resolve_trusted_executable,
)
from app.path_runtime import bind_root
from app.process_runtime import ProcessOutcome, ProcessResult, ProcessSpec, run_supervised
from app.safety.test_policy import TestPolicy as Policy
from app.safety.test_policy import is_forbidden_test_env_name, parse_test_policy
from app.tool_executor.contracts import ExecutionWorkspaceRef, RunScope, WorkspaceKind
from app.tool_executor.validation import ContractViolation
from app.tool_executor.workspace import InMemoryWorkspaceResolver, ResolvedWorkspace
from tests.test_process_runtime_e7_3 import TREE, Watch, eventually, os_process

IS_WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not IS_WINDOWS, reason="comportamento específico do Windows")
posix_only = pytest.mark.skipif(IS_WINDOWS, reason="comportamento específico do POSIX")

SENTINEL = "E83_SYNTHETIC_SECRET_DO_NOT_LEAK"
BASE = "a" * 40
OTHER_BASE = "b" * 40

PY_DIR, PY_FILE = os.path.split(sys.executable)
#: O nome **lógico** do Python da suíte, como uma `TestPolicy` o escreveria (`python`).
PY_LOGICAL = os.path.splitext(PY_FILE)[0] if IS_WINDOWS else PY_FILE

# ============================================================================== apoio


class Token:
    def __init__(self, predicate: Callable[[], bool] | None = None) -> None:
        self._predicate = predicate or (lambda: False)

    def is_cancelled(self) -> bool:
        return self._predicate()


def policy(
    argv: list[str],
    *,
    executable: str = PY_LOGICAL,
    env_allowlist: list[str] | None = None,
    timeout: int = 60,
    out: int = 64 * 1024,
    err: int = 64 * 1024,
    runner_id: str = GENERIC_SUBPROCESS_RUNNER_ID,
) -> Policy:
    parsed = parse_test_policy(
        {
            "runner_id": runner_id,
            "executable": executable,
            "argv": argv,
            "timeout_seconds": timeout,
            "env_allowlist": [] if env_allowlist is None else env_allowlist,
            "network_policy": "unrestricted",
            "output_limits": {"max_stdout_bytes": out, "max_stderr_bytes": err},
            "cwd_mode": "task_worktree",
        }
    )
    assert parsed is not None
    return parsed


@dataclass
class Rig:
    tmp: Path
    worktree: Path
    workspace: Path
    resolver: InMemoryWorkspaceResolver
    ref: ExecutionWorkspaceRef
    scope: RunScope
    resolved: ResolvedWorkspace
    reports: Path

    def request(
        self,
        test_policy: Policy,
        *,
        token: Any = None,
        ref: ExecutionWorkspaceRef | None = None,
        scope: RunScope | None = None,
    ) -> TestRequest:
        return TestRequest(
            workspace_ref=self.ref if ref is None else ref,
            run_scope=self.scope if scope is None else scope,
            test_policy=test_policy,
            cancel_token=Token() if token is None else token,
        )

    def runner(self, environment: dict[str, str] | None = None) -> LocalSubprocessTestRunner:
        env = {"PATH": PY_DIR} if environment is None else environment
        return LocalSubprocessTestRunner(self.resolver, trusted_environment=env)

    def script(self, name: str, source: str) -> str:
        """Scripts de teste ficam **fora** da worktree: não são o que está sob verificação."""
        path = self.tmp / "scripts" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return str(path)


def make_rig(tmp: Path, *, prefix: str = "") -> Rig:
    worktree = tmp / "worktrees" / "ff-task-abcdef12"
    workspace = worktree / prefix.rstrip("/") if prefix else worktree
    workspace.mkdir(parents=True)
    (worktree / "README.md").write_text("projeto\n", encoding="utf-8")
    reports = tmp / "reports"
    reports.mkdir()
    ref = ExecutionWorkspaceRef(kind=WorkspaceKind.LOCAL_WORKTREE, id="ws-1", base_commit=BASE)
    scope = RunScope(task_id="task-1", run_id="run-1", invocation_id="inv-1")
    resolved = ResolvedWorkspace(
        workspace_id="ws-1",
        task_id="task-1",
        run_id="run-1",
        base_commit=BASE,
        workspace_path=str(workspace),
        root_identity=bind_root(workspace).identity,
        workspace_prefix=prefix,
    )
    resolver = InMemoryWorkspaceResolver()
    resolver.bind(resolved)
    return Rig(tmp, worktree, workspace, resolver, ref, scope, resolved, reports)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return make_rig(tmp_path)


class Spy:
    """Envolve o Supervisor **real** e guarda a `ProcessSpec` e o `ProcessResult` vistos."""

    def __init__(self) -> None:
        self.specs: list[ProcessSpec] = []
        self.results: list[ProcessResult] = []

    def __call__(self, spec: ProcessSpec, is_cancelled: Callable[[], bool]) -> ProcessResult:
        self.specs.append(spec)
        result = _REAL_RUN_SUPERVISED(spec, is_cancelled)
        self.results.append(result)
        return result


_REAL_RUN_SUPERVISED = run_supervised


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> Spy:
    observed = Spy()
    monkeypatch.setattr(local_subprocess, "run_supervised", observed)
    return observed


def failure_of(call: Callable[[], object]) -> TestRunnerFailure:
    with pytest.raises(TestRunnerFailure) as info:
        call()
    return info.value


def no_leak(*texts: object) -> None:
    for text in texts:
        assert SENTINEL not in str(text) and SENTINEL not in repr(text)


REPORT = """\
import json, os, sys
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump({"cwd": os.getcwd(), "argv": sys.argv[1:], "env": dict(os.environ)}, handle)
sys.stdout.write(sys.argv[-1] if len(sys.argv) > 2 else "")
"""


def read_report(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


# ===================================================================== TR1, TR2, TR20


def test_tr1_caminho_feliz_cwd_argv_e_summary(rig: Rig, spy: Spy) -> None:
    script = rig.script("report.py", REPORT)
    out = rig.reports / "r.json"
    summary = rig.runner().run(rig.request(policy([script, str(out), "--flag", "x y"])))

    assert summary == TestSummary(
        framework="generic-subprocess-v1",
        exit_code=0,
        passed=None,
        failed=None,
        skipped=None,
        duration_ms=summary.duration_ms,
        output_ref=None,
    )
    report = read_report(out)
    assert os.path.normcase(report["cwd"]) == os.path.normcase(os.path.realpath(rig.workspace))
    assert report["argv"] == [str(out), "--flag", "x y"]  # literal: nada de shell, nada de split
    (spec,) = spy.specs
    assert os.path.isabs(spec.argv[0])
    assert os.path.normcase(spec.argv[0]) == os.path.normcase(os.path.join(PY_DIR, PY_FILE))
    assert spec.argv[1:] == (script, str(out), "--flag", "x y")
    assert spec.cwd == bind_root(rig.workspace).path
    assert spec.timeout_s == 60 and spec.max_stdout_bytes == spec.max_stderr_bytes == 65536


def test_runner_satisfaz_o_protocol(rig: Rig) -> None:
    runner: RunnerPort = rig.runner()
    assert repr(runner) == "<LocalSubprocessTestRunner>"


def test_tr2_exit_nao_zero_e_test_summary_legitimo(rig: Rig) -> None:
    """M6: tratar `exit_code != 0` como falha técnica faria este teste levantar."""
    summary = rig.runner().run(rig.request(policy(["-c", "import sys; sys.exit(7)"])))
    assert summary.exit_code == 7
    assert (summary.passed, summary.failed, summary.skipped) == (None, None, None)
    assert summary.output_ref is None


def test_tr20_runner_generico_nunca_interpreta_a_saida(rig: Rig, spy: Spy) -> None:
    """M7: um parser de "100 passed"/"5 failed" preencheria os contadores; aqui ficam `None`."""
    code = (
        "import sys; print('===== 100 passed, 5 failed, 2 skipped in 1.2s ====='); "
        "print('Tests: 5 failed, 100 passed', file=sys.stderr); sys.exit(1)"
    )
    summary = rig.runner().run(rig.request(policy(["-c", code])))
    assert summary.exit_code == 1
    assert (summary.passed, summary.failed, summary.skipped) == (None, None, None)
    assert b"100 passed" in spy.results[0].stdout  # a saída existiu; o runner só não a usou


def test_m13_output_ref_e_sempre_none_e_a_saida_nao_sobe(rig: Rig) -> None:
    code = f"import sys; print('{SENTINEL}'); print('{SENTINEL}', file=sys.stderr)"
    summary = rig.runner().run(rig.request(policy(["-c", code])))
    assert summary.output_ref is None
    no_leak(summary)


# ================================================================ TR3, TR4, TR5 — desfechos


def _run_in_thread(call: Callable[[], object]) -> tuple[threading.Thread, dict[str, object]]:
    box: dict[str, object] = {}

    def target() -> None:
        try:
            box["value"] = call()
        except BaseException as exc:  # devolvido ao teste
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, box


def _pid(path: Path) -> int:
    assert eventually(path.exists, timeout=30), "o processo não gravou o PID a tempo"
    return int(path.read_text(encoding="utf-8"))


@os_process
def test_tr3_timeout_e_falha_estruturada_com_a_arvore_morta(rig: Rig) -> None:
    loud = f"import sys\nprint({SENTINEL!r}, file=sys.stderr, flush=True)\n" + TREE
    tree = rig.script("tree_loud.py", loud)
    argv = [tree, str(rig.reports), "r", "1", "stay"]
    thread, box = _run_in_thread(lambda: rig.runner().run(rig.request(policy(argv, timeout=4))))
    root = Watch(_pid(rig.reports / "r.pid"))
    child = Watch(_pid(rig.reports / "rc.pid"))
    thread.join(60)
    assert not thread.is_alive()

    failure = box["error"]
    assert isinstance(failure, TestRunnerFailure)
    assert failure.code is TestRunnerFailureCode.TIMEOUT
    assert failure.tree_confirmed_dead is True
    assert str(failure) == "timeout" and repr(failure) == "TestRunnerFailure(timeout)"
    no_leak(failure, failure.args)
    assert not root.alive() and not child.alive()
    root.close()
    child.close()


@os_process
def test_tr4_cancelamento_e_falha_estruturada_com_a_arvore_morta(rig: Rig) -> None:
    tree = rig.script("tree.py", TREE)
    go = threading.Event()
    request = rig.request(
        policy([tree, str(rig.reports), "r", "1", "stay"]), token=Token(go.is_set)
    )
    thread, box = _run_in_thread(lambda: rig.runner().run(request))
    root = Watch(_pid(rig.reports / "r.pid"))
    child = Watch(_pid(rig.reports / "rc.pid"))
    go.set()
    thread.join(60)
    assert not thread.is_alive()

    failure = box["error"]
    assert isinstance(failure, TestRunnerFailure)
    assert failure.code is TestRunnerFailureCode.CANCELLED and failure.tree_confirmed_dead is True
    assert not root.alive() and not child.alive()
    root.close()
    child.close()


def test_tr4_cancelado_antes_nao_inicia_processo(rig: Rig) -> None:
    marker = rig.reports / "nasceu"
    request = rig.request(
        policy(["-c", f"open({str(marker)!r}, 'w').close()"]), token=Token(lambda: True)
    )
    failure = failure_of(lambda: rig.runner().run(request))
    assert failure.code is TestRunnerFailureCode.CANCELLED
    assert not marker.exists()


def test_tr5_falha_de_supervisao_e_estruturada_e_sem_vazamento(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(spec: ProcessSpec, is_cancelled: Callable[[], bool]) -> ProcessResult:
        return ProcessResult(
            outcome=ProcessOutcome.SUPERVISION_FAILED,
            exit_code=None,
            stdout=SENTINEL.encode(),
            stderr=SENTINEL.encode(),
            stdout_truncated=False,
            stderr_truncated=False,
            output_complete=False,
            duration_ms=1,
            tree_confirmed_dead=False,
            orphans_killed=False,
            detail=f"leitura falhou: {SENTINEL} C:\\Users\\x\\secret",
        )

    monkeypatch.setattr(local_subprocess, "run_supervised", broken)
    failure = failure_of(lambda: rig.runner().run(rig.request(policy(["-c", "pass"]))))
    assert failure.code is TestRunnerFailureCode.SUPERVISION_FAILED
    assert failure.tree_confirmed_dead is False
    assert str(failure) == "supervision_failed"
    no_leak(failure, failure.args, failure.__cause__, failure.__context__)


def test_tr5_token_que_levanta_vira_falha_de_supervisao_real(rig: Rig) -> None:
    def explode() -> bool:
        raise RuntimeError(SENTINEL)

    failure = failure_of(
        lambda: rig.runner().run(rig.request(policy(["-c", "pass"]), token=Token(explode)))
    )
    assert failure.code is TestRunnerFailureCode.SUPERVISION_FAILED
    no_leak(failure, failure.args)


# ============================================================ TR6, TR7 — saída limitada

FLOOD = """\
import sys
stream = sys.stdout.buffer if sys.argv[1] == "out" else sys.stderr.buffer
block = b"x" * 65536
for _ in range(int(sys.argv[2]) // 65536):
    stream.write(block)
stream.flush()
"""


@pytest.mark.parametrize("which", ["out", "err"])
def test_tr6_tr7_saida_acima_do_limite_e_drenada_sem_travar(rig: Rig, spy: Spy, which: str) -> None:
    flood = rig.script("flood.py", FLOOD)
    summary = rig.runner().run(
        rig.request(policy([flood, which, str(8 * 1024 * 1024)], out=1024, err=2048))
    )
    assert summary.exit_code == 0 and summary.output_ref is None
    (result,) = spy.results
    if which == "out":
        assert result.stdout_truncated and len(result.stdout) == 1024
    else:
        assert result.stderr_truncated and len(result.stderr) == 2048
    assert not hasattr(summary, "stdout") and not hasattr(summary, "stderr")


# ================================================================== TR8–TR10 — ambiente


def test_tr8_allowlist_vazia_filho_recebe_ambiente_vazio(rig: Rig, spy: Spy) -> None:
    out = rig.reports / "env.json"
    host = {"PATH": PY_DIR, "HOME": "/home/x", "ANTHROPIC_API_KEY": SENTINEL}
    rig.runner(host).run(rig.request(policy([rig.script("r.py", REPORT), str(out)])))
    assert dict(spy.specs[0].env) == {}
    assert read_report(out)["env"] == {}


def test_tr9_filho_recebe_so_o_allowlisted(rig: Rig, spy: Spy) -> None:
    """M4: `env=os.environ` (ou cópia filtrada) entregaria `PATH`/`HOME`/a chave."""
    out = rig.reports / "env.json"
    host = {
        "PATH": PY_DIR,
        "HOME": "B",
        "FF_TEST_ALLOWED": "C",
        "ANTHROPIC_API_KEY": SENTINEL,
    }
    rig.runner(host).run(
        rig.request(
            policy([rig.script("r.py", REPORT), str(out)], env_allowlist=["FF_TEST_ALLOWED"])
        )
    )
    assert dict(spy.specs[0].env) == {"FF_TEST_ALLOWED": "C"}
    assert read_report(out)["env"] == {"FF_TEST_ALLOWED": "C"}


def test_tr9_nome_ausente_no_host_simplesmente_nao_aparece(rig: Rig, spy: Spy) -> None:
    rig.runner({"PATH": PY_DIR}).run(
        rig.request(policy(["-c", "pass"], env_allowlist=["FF_NOT_IN_HOST", "PATH"]))
    )
    assert dict(spy.specs[0].env) == {"PATH": PY_DIR}  # PATH só porque foi allowlisted


def test_path_do_host_resolve_sem_ser_entregue_ao_filho(rig: Rig, spy: Spy) -> None:
    """D4: o `PATH` confiável serve à resolução; ao filho só chega se allowlisted."""
    rig.runner({"PATH": PY_DIR, "SYSTEMROOT": "C:\\Windows"}).run(
        rig.request(policy(["-c", "pass"]))
    )
    assert "PATH" not in spy.specs[0].env and "SYSTEMROOT" not in spy.specs[0].env


FORBIDDEN = [
    "ANTHROPIC_API_KEY",
    "anthropic_api_key",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "OPENAI_ORG_ID",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AZURE_OPENAI_ENDPOINT",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITLAB_PRIVATE_TOKEN",
    "HF_TOKEN",
    "NPM_TOKEN",
    "FF_SECRET_ANTHROPIC_API_KEY",
    "FF_SECRET_X",
    "DB_PASSWORD",
    "DATABASE_PASS",
    "SSH_AUTH_SOCK",
    "GIT_ASKPASS",
    "STRIPE_KEY",
    "MY_SERVICE_CREDENTIALS",
    "KUBECONFIG",
    "JWT",
    "___",
]

ALLOWED = [
    "PATH",
    "SYSTEMROOT",
    "HOME",
    "LANG",
    "TZ",
    "CI",
    "NODE_ENV",
    "PYTHONHASHSEED",
    "PYTHONDONTWRITEBYTECODE",
    "FF_TEST_ALLOWED",
    "TOKENIZERS_PARALLELISM",
    "KEYBOARD_LAYOUT",
    "AUTHOR_NAME",
]


@pytest.mark.parametrize("name", FORBIDDEN)
def test_classificacao_de_nome_proibido(name: str) -> None:
    assert is_forbidden_test_env_name(name)


@pytest.mark.parametrize("name", ALLOWED)
def test_classificacao_de_nome_permitido(name: str) -> None:
    assert not is_forbidden_test_env_name(name)


@pytest.mark.parametrize("name", ["ANTHROPIC_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY"])
def test_tr10_segredo_allowlisted_e_recusado_antes_do_processo(
    rig: Rig, spy: Spy, name: str, caplog: pytest.LogCaptureFixture
) -> None:
    """M5: aceitar a chave pela allowlist faria o processo nascer com ela."""
    caplog.set_level(logging.DEBUG)
    host = {"PATH": PY_DIR, name: SENTINEL}
    request = rig.request(policy(["-c", "pass"], env_allowlist=[name]))
    failure = failure_of(lambda: rig.runner(host).run(request))
    assert failure.code is TestRunnerFailureCode.INVALID_ENVIRONMENT
    assert spy.specs == []  # nenhum processo nasceu
    no_leak(failure, failure.args, caplog.text)
    assert SENTINEL not in repr(rig.runner(host))


#: E8.3-AUD-001: as 30 variáveis da auditoria independente. `DATABASE_URL` e `DOCKER_CONFIG`
#: atravessavam a classificação anterior.
AUDIT_CREDENTIALS = [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "NPM_TOKEN",
    "NODE_AUTH_TOKEN",
    "PYPI_TOKEN",
    "HF_TOKEN",
    "AZURE_CLIENT_SECRET",
    "DATABASE_URL",
    "SENTRY_AUTH_TOKEN",
    "BREVO_API_KEY",
    "SENDGRID_API_KEY",
    "SSH_AUTH_SOCK",
    "SSH_ASKPASS",
    "GIT_ASKPASS",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AWS_SHARED_CREDENTIALS_FILE",
    "DOCKER_CONFIG",
    "KUBECONFIG",
    "NETRC",
    "PRIVATE_KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
]

#: Credencial e localizador além da tabela: *connection strings* de outros armazenamentos e
#: localizadores comuns. E os controles que **não** podem ser bloqueados por engano.
MORE_CREDENTIALS = [
    "REDIS_URL",
    "MONGODB_URI",
    "SQLALCHEMY_DATABASE_URI",
    "POSTGRES_URL",
    "SENTRY_DSN",
    "PGPASSWORD",
    "DOCKER_HOST",
    "AWS_CONFIG_FILE",
    "GIT_CONFIG_GLOBAL",
    "database_url",
]
LOCATOR_LOOKALIKES_ALLOWED = ["BASE_URL", "API_URL", "SERVICE_URL", "DATABASE_NAME", "DOCKERFILE"]


def test_aud001_tabela_da_auditoria_tem_as_30_variaveis() -> None:
    assert len(AUDIT_CREDENTIALS) == len(set(AUDIT_CREDENTIALS)) == 30


@pytest.mark.parametrize("name", AUDIT_CREDENTIALS + MORE_CREDENTIALS)
def test_aud001_credencial_e_localizador_sao_proibidos(name: str) -> None:
    """A1/A2: tirar `DATABASE_URL` ou `DOCKER_CONFIG` da classificação faz isto falhar."""
    assert is_forbidden_test_env_name(name)


@pytest.mark.parametrize("name", LOCATOR_LOOKALIKES_ALLOWED)
def test_aud001_nome_parecido_continua_permitido(name: str) -> None:
    assert not is_forbidden_test_env_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "DATABASE_URL",
        "DOCKER_CONFIG",
        "ANTHROPIC_API_KEY",
        "AWS_SHARED_CREDENTIALS_FILE",
        "GIT_ASKPASS",
    ],
)
@pytest.mark.parametrize(
    "adulterada", [False, True], ids=["politica-valida", "politica-adulterada"]
)
def test_aud001_runner_real_recusa_antes_do_processo(
    rig: Rig, spy: Spy, name: str, adulterada: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """O runner se defende mesmo de uma `TestPolicy` montada à mão (não só da validada no
    `parse_test_policy`): o processo **não nasce** e o valor sintético não aparece em nada."""
    caplog.set_level(logging.DEBUG)
    marker = rig.reports / "nasceu"
    code = f"open({str(marker)!r}, 'w').close()"
    if adulterada:
        test_policy = replace(policy(["-c", code]), env_allowlist=("FF_TEST_ALLOWED", name))
    else:
        test_policy = policy(["-c", code], env_allowlist=["FF_TEST_ALLOWED", name])
    host = {"PATH": PY_DIR, "FF_TEST_ALLOWED": "C", name: SENTINEL}
    failure = failure_of(lambda: rig.runner(host).run(rig.request(test_policy)))
    assert failure.code is TestRunnerFailureCode.INVALID_ENVIRONMENT
    assert spy.specs == [] and not marker.exists()
    no_leak(failure, failure.args, caplog.text, rig.runner(host))


def test_aud001_controle_positivo_ff_test_allowed_continua_entregue(rig: Rig, spy: Spy) -> None:
    rig.runner({"PATH": PY_DIR, "FF_TEST_ALLOWED": "C", "DATABASE_URL": SENTINEL}).run(
        rig.request(policy(["-c", "pass"], env_allowlist=["FF_TEST_ALLOWED"]))
    )
    assert dict(spy.specs[0].env) == {"FF_TEST_ALLOWED": "C"}


@windows_only
def test_ambiente_confiavel_ambiguo_por_caixa_e_recusado(rig: Rig, spy: Spy) -> None:
    host = {"PATH": PY_DIR, "FF_X": "1", "ff_x": "2"}
    request = rig.request(policy(["-c", "pass"], env_allowlist=["FF_X"]))
    failure = failure_of(lambda: rig.runner(host).run(request))
    assert failure.code is TestRunnerFailureCode.INVALID_ENVIRONMENT and spy.specs == []


@windows_only
def test_allowlist_duplicada_por_caixa_e_recusada(rig: Rig, spy: Spy) -> None:
    request = rig.request(policy(["-c", "pass"], env_allowlist=["FF_X", "ff_x"]))
    failure = failure_of(lambda: rig.runner().run(request))
    assert failure.code is TestRunnerFailureCode.INVALID_ENVIRONMENT and spy.specs == []


# ================================================ TR11–TR14 — resolução do executável


def test_tr11_executavel_achado_pelo_path_confiavel_e_absoluto(rig: Rig, spy: Spy) -> None:
    empty = rig.tmp / "vazio"
    empty.mkdir()
    rig.runner({"PATH": os.pathsep.join([str(empty), PY_DIR])}).run(
        rig.request(policy(["-c", "pass"]))
    )
    argv0 = spy.specs[0].argv[0]
    assert os.path.isabs(argv0)
    assert os.path.normcase(argv0) == os.path.normcase(os.path.join(PY_DIR, PY_FILE))


def test_tr11_caminho_absoluto_configurado_e_aceito(rig: Rig, spy: Spy) -> None:
    rig.runner({}).run(rig.request(policy(["-c", "pass"], executable=sys.executable)))
    assert os.path.normcase(spy.specs[0].argv[0]) == os.path.normcase(sys.executable)


def _poison(directory: Path, name: str, marker: Path) -> None:
    """Um executável **homônimo** que, se escolhido, deixa rastro (POSIX) ou nem roda (Win)."""
    target = directory / name
    target.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_tr12_e1_e2_cwd_envenenado_nunca_ganha(
    rig: Rig, spy: Spy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M3: buscar no `cwd` (como `shutil.which` no Windows/3.11) escolheria o falso."""
    marker = rig.reports / "envenenado"
    for name in {PY_FILE, PY_LOGICAL, "pytest.exe", "pytest", "node.exe", "npm.cmd"}:
        _poison(rig.workspace, name, marker)
    monkeypatch.chdir(rig.workspace)
    path = os.pathsep.join(["", ".", "relativo", PY_DIR])
    summary = rig.runner({"PATH": path}).run(rig.request(policy(["-c", "pass"])))
    assert summary.exit_code == 0 and not marker.exists()
    assert os.path.normcase(spy.specs[0].argv[0]) == os.path.normcase(os.path.join(PY_DIR, PY_FILE))


def test_tr12_cwd_envenenado_fora_da_worktree_tambem_nao_conta(
    rig: Rig, spy: Spy, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M3 sem a rede da exclusão da worktree: o `cwd` do backend é outro diretório qualquer, e
    nem `.`/entrada vazia/relativa do `PATH` nem o próprio `cwd` entram na busca."""
    poisoned = rig.tmp / "cwd-do-backend"
    poisoned.mkdir()
    marker = rig.reports / "envenenado"
    for name in {PY_FILE, PY_LOGICAL}:
        _poison(poisoned, name, marker)
    monkeypatch.chdir(poisoned)
    path = os.pathsep.join(["", ".", PY_DIR])
    summary = rig.runner({"PATH": path}).run(rig.request(policy(["-c", "pass"])))
    assert summary.exit_code == 0 and not marker.exists()
    assert os.path.normcase(spy.specs[0].argv[0]) == os.path.normcase(os.path.join(PY_DIR, PY_FILE))
    failure = failure_of(lambda: rig.runner({"PATH": "."}).run(rig.request(policy(["-c", "pass"]))))
    assert failure.code is TestRunnerFailureCode.EXECUTABLE_UNAVAILABLE


def test_tr12_path_apontando_para_a_worktree_e_ignorado(rig: Rig, spy: Spy) -> None:
    marker = rig.reports / "envenenado"
    bindir = rig.worktree / "bin"
    bindir.mkdir()
    _poison(bindir, PY_FILE, marker)
    request = rig.request(policy(["-c", "pass"]))
    failure = failure_of(lambda: rig.runner({"PATH": str(bindir)}).run(request))
    assert failure.code is TestRunnerFailureCode.EXECUTABLE_UNAVAILABLE and spy.specs == []
    # E a mesma worktree antes do Python confiável: pulada, não escolhida.
    rig.runner({"PATH": os.pathsep.join([str(bindir), PY_DIR])}).run(request)
    assert os.path.normcase(spy.specs[0].argv[0]) == os.path.normcase(os.path.join(PY_DIR, PY_FILE))
    assert not marker.exists()


def test_e5_caminho_absoluto_dentro_da_worktree_e_recusado(rig: Rig, spy: Spy) -> None:
    _poison(rig.worktree, PY_FILE, rig.reports / "x")  # executável de verdade para o SO
    inside = rig.worktree / PY_FILE
    request = rig.request(policy(["-c", "pass"], executable=str(inside)))
    failure = failure_of(lambda: rig.runner().run(request))
    assert failure.code is TestRunnerFailureCode.EXECUTABLE_UNAVAILABLE and spy.specs == []


@pytest.mark.parametrize(
    "logical",
    [
        "./evil",
        "../evil",
        ".\\evil",
        "..\\evil",
        "sub/evil",
        "sub\\evil",
        ".",
        "..",
        "C:evil",
        "\\evil",
        "\\\\server\\share\\evil.exe",
        "\\\\?\\C:\\evil.exe",
        "evil.exe:stream",
    ],
)
def test_tr13_formas_relativas_sao_recusadas_sem_resolver(rig: Rig, spy: Spy, logical: str) -> None:
    (rig.workspace / "evil").write_text("x", encoding="utf-8")
    failure = failure_of(
        lambda: rig.runner().run(rig.request(policy(["-c", "pass"], executable=logical)))
    )
    assert failure.code is TestRunnerFailureCode.EXECUTABLE_UNAVAILABLE and spy.specs == []


@pytest.mark.parametrize("logical", ["npm.cmd", "build.bat", "NPM.CMD", "x.cmd.", "y.bat "])
def test_tr14_bat_cmd_por_nome_sao_recusados(rig: Rig, spy: Spy, logical: str) -> None:
    """M15: nenhuma rota entrega `.cmd`/`.bat` ao `cmd.exe`."""
    trusted = rig.tmp / "trusted"
    trusted.mkdir(exist_ok=True)
    (trusted / logical.strip(" .")).write_text("@echo off\n", encoding="utf-8")
    request = rig.request(policy(["-c", "pass"], executable=logical.strip() or logical))
    failure = failure_of(lambda: rig.runner({"PATH": str(trusted)}).run(request))
    assert failure.code is TestRunnerFailureCode.EXECUTABLE_UNAVAILABLE and spy.specs == []


@pytest.mark.parametrize(
    "name",
    ["cmd", "CMD.EXE", "powershell", "pwsh", "bash", "sh", "zsh", "dash", "ksh93", "env", "wsl"],
)
def test_tr14_interprete_de_shell_por_nome_e_recusado(rig: Rig, spy: Spy, name: str) -> None:
    """`cmd /c "npm test"` ou `sh -c "…"` devolveriam a gramática de shell ao comando — e
    `cmd.exe /c npm.cmd` contornaria a recusa de `.cmd`. Recusado mesmo achado no `PATH`."""
    trusted = rig.tmp / "shells"
    trusted.mkdir(exist_ok=True)
    stem = name.lower().removesuffix(".exe")
    filename = f"{stem}.exe" if IS_WINDOWS else stem
    _poison(trusted, filename, rig.reports / "shell")
    request = rig.request(policy(["/c", "echo x"], executable=name))
    failure = failure_of(lambda: rig.runner({"PATH": str(trusted)}).run(request))
    assert failure.code is TestRunnerFailureCode.EXECUTABLE_UNAVAILABLE and spy.specs == []


@windows_only
def test_tr14_cmd_exe_do_sistema_por_caminho_absoluto_e_recusado(rig: Rig, spy: Spy) -> None:
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    for absolute in (
        os.path.join(system_root, "System32", "cmd.exe"),
        os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
    ):
        if not os.path.isfile(absolute):
            continue
        request = rig.request(policy(["/c", "echo x"], executable=absolute))
        failure = failure_of(lambda: rig.runner({}).run(request))  # noqa: B023
        assert failure.code is TestRunnerFailureCode.EXECUTABLE_UNAVAILABLE
    assert spy.specs == []


@posix_only
def test_tr14_link_com_nome_inocente_para_shell_e_recusado(rig: Rig, spy: Spy) -> None:
    """O destino real conta: `python` → `/bin/sh` continua sendo um shell."""
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("sem /bin/sh")
    trusted = rig.tmp / "disfarce"
    trusted.mkdir()
    (trusted / "python").symlink_to(os.path.realpath(shell))
    request = rig.request(policy(["-c", "true"], executable="python"))
    failure = failure_of(lambda: rig.runner({"PATH": str(trusted)}).run(request))
    assert failure.code is TestRunnerFailureCode.EXECUTABLE_UNAVAILABLE and spy.specs == []


def test_nome_parecido_com_shell_nao_e_shell(tmp_path: Path) -> None:
    from app.agent_runtime.runners.local_subprocess import _is_shell_interpreter

    for innocent in ("python", "node.exe", "shellcheck", "bashate", "envoy", "cmake.exe", "shx"):
        assert not _is_shell_interpreter(innocent), innocent
    for shell in ("sh", "bash5.2", "pwsh.exe", "PowerShell.EXE", "ksh93", "busybox"):
        assert _is_shell_interpreter(shell), shell


def test_tr14_caminho_absoluto_para_cmd_e_recusado(rig: Rig, spy: Spy) -> None:
    script = rig.tmp / "x.cmd"
    script.write_text("@echo off\n", encoding="utf-8")
    request = rig.request(policy(["-c", "pass"], executable=str(script)))
    failure = failure_of(lambda: rig.runner().run(request))
    assert failure.code is TestRunnerFailureCode.EXECUTABLE_UNAVAILABLE and spy.specs == []


@windows_only
def test_tr14_npm_sem_extensao_nao_resolve_para_npm_cmd(rig: Rig, spy: Spy) -> None:
    """O `PATHEXT` do host (com `.CMD`) não participa: só `.exe`/`.com` são lançáveis."""
    trusted = rig.tmp / "node"
    trusted.mkdir()
    (trusted / "npm.cmd").write_text("@echo off\n", encoding="utf-8")
    (trusted / "npm.bat").write_text("@echo off\n", encoding="utf-8")
    (trusted / "npm.ps1").write_text("echo x\n", encoding="utf-8")
    host = {"PATH": str(trusted), "PATHEXT": ".COM;.EXE;.BAT;.CMD;.PS1"}
    request = rig.request(policy(["test"], executable="npm"))
    failure = failure_of(lambda: rig.runner(host).run(request))
    assert failure.code is TestRunnerFailureCode.EXECUTABLE_UNAVAILABLE and spy.specs == []


@windows_only
def test_resolucao_windows_tenta_exe_e_nao_aceita_extensao_estranha(tmp_path: Path) -> None:
    trusted = tmp_path / "bin"
    trusted.mkdir()
    (trusted / "tool.exe").write_bytes(b"MZ")
    (trusted / "other.ps1").write_text("x", encoding="utf-8")
    excluded = str(tmp_path / "wt")
    found = resolve_trusted_executable("tool", search_path=str(trusted), excluded_root=excluded)
    assert found is not None and found.lower().endswith("tool.exe") and os.path.isabs(found)
    assert resolve_trusted_executable("TOOL.EXE", search_path=str(trusted), excluded_root=excluded)
    assert (
        resolve_trusted_executable("other.ps1", search_path=str(trusted), excluded_root=excluded)
        is None
    )
    assert (
        resolve_trusted_executable("tool.exe.", search_path=str(trusted), excluded_root=excluded)
        is None
    )


@posix_only
def test_resolucao_posix_pula_arquivo_sem_bit_de_execucao(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    (first / "tool").write_text("#!/bin/sh\n", encoding="utf-8")  # 0644: não executável
    (second / "tool").write_text("#!/bin/sh\n", encoding="utf-8")
    (second / "tool").chmod(0o755)
    found = resolve_trusted_executable(
        "tool", search_path=f"{first}:{second}", excluded_root=str(tmp_path / "wt")
    )
    assert found == str(second / "tool")


def test_executavel_inexistente_e_falha_estruturada(rig: Rig, spy: Spy) -> None:
    request = rig.request(policy(["-c", "pass"], executable="definitely-not-installed-e83"))
    failure = failure_of(lambda: rig.runner().run(request))
    assert failure.code is TestRunnerFailureCode.EXECUTABLE_UNAVAILABLE and spy.specs == []


# =========================================================== TR15–TR17 — binding do run


def _expect_unbound(rig: Rig, spy: Spy, request: TestRequest) -> None:
    failure = failure_of(lambda: rig.runner().run(request))
    assert failure.code is TestRunnerFailureCode.WORKSPACE_UNAVAILABLE and spy.specs == []
    assert str(rig.workspace) not in str(failure) and str(rig.workspace) not in repr(failure)


def test_tr15_run_scope_de_outro_run_nao_resolve(rig: Rig, spy: Spy) -> None:
    """M2: resolver só pelo `workspace_id` acharia a worktree do run-1 para o run-2."""
    other = RunScope(task_id="task-1", run_id="run-2", invocation_id="inv-9")
    _expect_unbound(rig, spy, rig.request(policy(["-c", "pass"]), scope=other))
    other_task = RunScope(task_id="task-2", run_id="run-1", invocation_id="inv-1")
    _expect_unbound(rig, spy, rig.request(policy(["-c", "pass"]), scope=other_task))


def test_tr15_resolvedor_errado_que_ignora_a_chave_e_recusado(rig: Rig, spy: Spy) -> None:
    """M1: sem conferir o `run_scope` contra o binding devolvido, um resolvedor que devolve a
    worktree de outro run seria aceito."""

    class Sloppy:
        def __init__(self, resolved: ResolvedWorkspace) -> None:
            self.resolved = resolved

        def resolve(self, workspace_ref: object, run_scope: object) -> ResolvedWorkspace:
            return self.resolved

    other = RunScope(task_id="task-1", run_id="run-2", invocation_id="inv-2")
    runner = LocalSubprocessTestRunner(Sloppy(rig.resolved), trusted_environment={"PATH": PY_DIR})
    failure = failure_of(lambda: runner.run(rig.request(policy(["-c", "pass"]), scope=other)))
    assert failure.code is TestRunnerFailureCode.WORKSPACE_UNAVAILABLE and spy.specs == []


def test_tr15_workspace_ref_de_outro_workspace_nao_resolve(rig: Rig, spy: Spy) -> None:
    ref = ExecutionWorkspaceRef(kind=WorkspaceKind.LOCAL_WORKTREE, id="ws-2", base_commit=BASE)
    _expect_unbound(rig, spy, rig.request(policy(["-c", "pass"]), ref=ref))


def test_tr16_binding_ausente(tmp_path: Path, spy: Spy) -> None:
    rig = make_rig(tmp_path)
    rig.resolver.unbind("ws-1", "task-1", "run-1")
    _expect_unbound(rig, spy, rig.request(policy(["-c", "pass"])))


def test_tr17_base_commit_divergente(rig: Rig, spy: Spy) -> None:
    ref = replace(rig.ref, base_commit=OTHER_BASE)
    _expect_unbound(rig, spy, rig.request(policy(["-c", "pass"]), ref=ref))


def test_kind_remote_e_recusado(rig: Rig, spy: Spy) -> None:
    ref = replace(rig.ref, kind=WorkspaceKind.REMOTE)
    _expect_unbound(rig, spy, rig.request(policy(["-c", "pass"]), ref=ref))


def test_raiz_trocada_depois_do_binding_e_recusada(rig: Rig, spy: Spy) -> None:
    rig.workspace.rename(rig.tmp / "movido")
    rig.workspace.mkdir()
    _expect_unbound(rig, spy, rig.request(policy(["-c", "pass"])))


def test_workspace_em_subdiretorio_roda_no_workspace_e_exclui_a_worktree_inteira(
    tmp_path: Path, spy: Spy
) -> None:
    rig = make_rig(tmp_path, prefix="app/")
    out = rig.reports / "r.json"
    rig.runner().run(rig.request(policy([rig.script("r.py", REPORT), str(out)])))
    assert os.path.normcase(read_report(out)["cwd"]) == os.path.normcase(
        os.path.realpath(rig.workspace)
    )
    # Um executável na raiz da worktree (fora do prefixo) também é da worktree: recusado.
    _poison(rig.worktree, PY_FILE, rig.reports / "x")
    request = rig.request(policy(["-c", "pass"], executable=str(rig.worktree / PY_FILE)))
    failure = failure_of(lambda: rig.runner().run(request))
    assert failure.code is TestRunnerFailureCode.EXECUTABLE_UNAVAILABLE


# ============================================================ política e contrato (TR18/19)


def test_runner_id_diferente_e_unsupported_runner(rig: Rig, spy: Spy) -> None:
    request = rig.request(policy(["-c", "pass"], runner_id="pytest-json-v2"))
    failure = failure_of(lambda: rig.runner().run(request))
    assert failure.code is TestRunnerFailureCode.UNSUPPORTED_RUNNER and spy.specs == []


@pytest.mark.parametrize(
    "changes",
    [
        {"cwd_mode": "anywhere"},
        {"network_policy": "disabled"},
        {"argv": ["-c", "pass"]},  # lista, não tupla
        {"argv": ("-c", " pass ")},  # fora da forma normalizada
        {"timeout_seconds": 0},
        {"timeout_seconds": True},
        {"max_stdout_bytes": -1},
        {"env_allowlist": ("PATH", "")},
        {"executable": " python"},
        {"argv": ("ok", "nul\x00")},
    ],
)
def test_politica_montada_a_mao_fora_do_schema_e_invalid_policy(
    rig: Rig, spy: Spy, changes: dict[str, Any]
) -> None:
    bad = replace(policy(["-c", "pass"]), **changes)
    failure = failure_of(lambda: rig.runner().run(rig.request(bad)))
    assert failure.code is TestRunnerFailureCode.INVALID_POLICY and spy.specs == []


def test_tr18_test_request_exige_run_scope(rig: Rig) -> None:
    with pytest.raises(ContractViolation):
        TestRequest(rig.ref, None, policy(["-c", "pass"]), Token())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        TestRequest(workspace_ref=rig.ref, test_policy=policy(["-c", "pass"]), cancel_token=Token())  # type: ignore[call-arg]


def test_request_que_nao_e_test_request_e_erro_de_contrato(rig: Rig) -> None:
    with pytest.raises(ContractViolation):
        rig.runner().run({"test_policy": policy(["-c", "pass"])})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "counters",
    [(None, None, None), (0, 0, 0), (10, 2, 1)],
)
def test_tr19_contadores_validos(counters: tuple[int | None, int | None, int | None]) -> None:
    passed, failed, skipped = counters
    summary = TestSummary("generic-subprocess-v1", 0, passed, failed, skipped, 5, None)
    assert (summary.passed, summary.failed, summary.skipped) == counters


@pytest.mark.parametrize(
    "counters",
    [
        (1, None, None),
        (None, 0, None),
        (None, None, 0),
        (1, 2, None),
        (-1, 0, 0),
        (0, -1, 0),
        (True, 0, 0),
        (0, False, 0),
        (1.0, 0, 0),
        ("1", "0", "0"),
    ],
)
def test_tr19_contadores_invalidos(counters: tuple[Any, Any, Any]) -> None:
    passed, failed, skipped = counters
    with pytest.raises(ContractViolation):
        TestSummary("generic-subprocess-v1", 0, passed, failed, skipped, 5, None)


def test_falha_estruturada_tem_codigos_estaveis() -> None:
    assert {code.value for code in TestRunnerFailureCode} == {
        "unsupported_runner",
        "invalid_policy",
        "workspace_unavailable",
        "executable_unavailable",
        "invalid_environment",
        "timeout",
        "cancelled",
        "supervision_failed",
    }
    with pytest.raises(ContractViolation):
        TestRunnerFailure("timeout")  # type: ignore[arg-type]
    failure = TestRunnerFailure(TestRunnerFailureCode.CANCELLED, tree_confirmed_dead=True)
    assert failure.args == ("cancelled",)


def test_ambiente_confiavel_e_copiado_na_construcao(rig: Rig, spy: Spy) -> None:
    host = {"PATH": PY_DIR, "FF_TEST_ALLOWED": "antes"}
    runner = rig.runner(host)
    host["FF_TEST_ALLOWED"] = "depois"
    runner.run(rig.request(policy(["-c", "pass"], env_allowlist=["FF_TEST_ALLOWED"])))
    assert dict(spy.specs[0].env) == {"FF_TEST_ALLOWED": "antes"}
    with pytest.raises(ContractViolation):
        LocalSubprocessTestRunner(rig.resolver, trusted_environment={"PATH": 1})  # type: ignore[dict-item]


def test_cancel_token_e_o_unico_mecanismo(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Callable[[], bool]] = []

    def capture(spec: ProcessSpec, is_cancelled: Callable[[], bool]) -> ProcessResult:
        seen.append(is_cancelled)
        return _REAL_RUN_SUPERVISED(spec, is_cancelled)

    monkeypatch.setattr(local_subprocess, "run_supervised", capture)
    token: CancelToken = Token()
    rig.runner().run(rig.request(policy(["-c", "pass"]), token=token))
    assert seen == [token.is_cancelled]


@pytest.fixture(autouse=True)
def _no_stray_logging(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Nenhum teste deste módulo pode ver a sentinela em log, em nenhum logger."""
    caplog.set_level(logging.DEBUG)
    yield
    assert SENTINEL not in caplog.text
