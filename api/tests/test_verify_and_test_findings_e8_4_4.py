"""E8.4.4 — contraprovas dos seis findings da auditoria independente (Codex, BLOCKED).

Cada teste falhava na implementação auditada e passa depois da correção. Relógios, repositórios,
arquivos e processos são sintéticos; nada aqui depende de tempo real além de limites de latência
folgados.

* P1-001 divergência de integridade confundida com "métrica indisponível";
* P1-002 comparação de linhas que ignorava cancelamento e prazo (`SequenceMatcher`);
* P1-003 `tree_confirmed_dead=False` apagado por binding perdido;
* P1-004 prazo da passagem reconstruído depois do claim;
* P2-005 worktree fora da identidade do claim;
* P2-006 `SafetyEvent` afirmando encerramento não comprovado da árvore.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings
from app.db.enums import (
    FilesReadSource,
    RunAgent,
    RunPurpose,
    RunStatus,
    RunTransport,
    SafetyEventKind,
    TokenSource,
)
from app.db.models import DevWorkspace, Run
from app.execution_verification import PostExecutionVerifier
from app.git_runtime import diff_stats as ds
from app.git_runtime.diff_stats import DiffMeasureFailure as F
from app.git_runtime.diff_stats import DiffMetrics, count_changes
from app.orchestrator import execution_manager as em
from app.orchestrator.developer_invocation_contract import ExistingRunFacts
from app.orchestrator.execution_contract import IntegrityVerdict, RunnerOutcome
from app.orchestrator.verify_and_test_contract import (
    MetricsReport,
    RunnerFailureCode,
    RunnerIdentity,
    RunnerReport,
    StopCause,
    VerifyCode,
    VerifyDecision,
    VerifyStatus,
    decide_after_runner,
    runner_claim_compatible,
    runner_events,
)
from tests import test_diff_stats_e8_4_4 as _diff_tests
from tests.conftest import supports_symlinks
from tests.test_diff_stats_e8_4_4 import inside, run_and_measure
from tests.test_execution_workspace_e8_4_2 import Harness
from tests.test_post_execution_verification_e8_3 import Rig, never, write
from tests.test_verify_and_test_e8_4_4 import events_of, runner_invocation_id, runner_runs
from tests.verify_and_test_support_e8_4_4 import (
    ScriptedRunner,
    VRig,
    build_rig,
    exited,
    failed,
    write_in_worktree,
)

pytestmark = pytest.mark.skipif(
    __import__("shutil").which("git") is None, reason="git indisponível no PATH"
)

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
ID = RunnerIdentity("generic-subprocess-v1", "1")


rig_factory = _diff_tests.rig_factory  # a mesma fábrica de repositórios sintéticos


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    real_connect = socket.socket.connect

    def guarded(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOOPBACK:
            raise AssertionError(f"rede proibida nos testes da E8.4.4: {host!r}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture
def h(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    repo_path: Path,
    temp_settings: AppSettings,
    tmp_path: Path,
) -> Harness:
    temp_settings.ensure_worktrees_dir()
    return Harness(
        session=session,
        factory=session_factory,
        workspace=workspace,
        repo=repo_path,
        artifacts=tmp_path / "artifacts",
        settings=temp_settings,
        tmp=tmp_path,
    )


# ============================================================ P1-001 — divergência × métrica


def _swap_for_directory(rig: Rig, name: str) -> Callable[[], None]:
    def swap() -> None:
        inside(rig, name).unlink()
        inside(rig, name).mkdir()

    return swap


def test_p1_001_arquivo_trocado_por_diretorio_e_divergencia(
    rig_factory: Callable[..., Rig],
) -> None:
    rig = rig_factory()
    out = run_and_measure(
        rig,
        lambda: write(inside(rig, "a.py"), "A = 2\n"),
        between=_swap_for_directory(rig, "a.py"),
    )
    assert out is F.CHANGED_SINCE_VERIFIED


@pytest.mark.parametrize(
    ("label", "content"),
    [
        pytest.param("bytes", b"z" * (ds.MAX_FILE_BYTES + 1), id="acima_do_limite_de_bytes"),
        pytest.param("bin", b"\x00\x01\x02 outro conteudo\n", id="binario"),
        pytest.param("linhas", b"l\n" * (ds.MAX_LINES + 1), id="linhas_demais"),
    ],
)
def test_p1_001_conteudo_trocado_por_nao_mensuravel_e_divergencia_e_nao_nulo(
    rig_factory: Callable[..., Rig], label: str, content: bytes
) -> None:
    """Trocar o verificado por algo *não mensurável* não pode virar "métrica indisponível"."""
    rig = rig_factory()
    out = run_and_measure(
        rig,
        lambda: write(inside(rig, "a.py"), "A = 2\n"),
        between=lambda: inside(rig, "a.py").write_bytes(content),
    )
    assert out is F.CHANGED_SINCE_VERIFIED, label


def test_p1_001_nao_mensuravel_antes_nao_esconde_divergencia_depois(
    rig_factory: Callable[..., Rig],
) -> None:
    """Um arquivo binário (indisponível) não pode abortar a revalidação dos demais caminhos."""
    rig = rig_factory()

    def effect() -> None:
        inside(rig, "a_bin.dat").write_bytes(b"\x00\x01")  # 1º na ordem; não mensurável
        write(inside(rig, "z_ok.py"), "ok = 1\n")

    out = run_and_measure(
        rig, effect, between=lambda: write(inside(rig, "z_ok.py"), "trocado = 2\n")
    )
    assert out is F.CHANGED_SINCE_VERIFIED


def test_p1_001_caminhos_demais_tambem_revalidam_a_integridade(
    rig_factory: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = rig_factory()
    monkeypatch.setattr(ds, "MAX_PATHS", 1)

    def effect() -> None:
        write(inside(rig, "n1.txt"), "1\n")
        write(inside(rig, "n2.txt"), "2\n")

    out = run_and_measure(rig, effect, between=lambda: write(inside(rig, "n2.txt"), "trocado\n"))
    assert out is F.CHANGED_SINCE_VERIFIED


def test_p1_001_nao_mensuravel_com_integridade_comprovada_continua_nulo(
    rig_factory: Callable[..., Rig],
) -> None:
    rig = rig_factory()
    out = run_and_measure(rig, lambda: inside(rig, "bin.dat").write_bytes(b"\x00\x01"))
    assert out is F.BINARY  # nada mudou depois da verificação: NULL permitido


def test_p1_001_servico_nao_prossegue_quando_o_verificado_vira_diretorio(h: Harness) -> None:
    rig = build_rig(
        h, developer_hook=lambda dev: write_in_worktree(dev, "src/util.py", "def util(): ...\n")
    )

    def swap(_request: Any) -> None:
        target = Path(rig.context.workspace_path) / "src" / "util.py"
        target.unlink()
        target.mkdir()

    rig.verification.before_measure = swap
    result = rig.execute()
    assert result.code is VerifyCode.DIFF_CHANGED_SINCE_VERIFIED
    assert result.status is not VerifyStatus.COMPLETED
    assert result.run_status is not RunStatus.OK
    assert result.process_started is False and rig.runner.calls == []


@pytest.mark.parametrize(
    "failure",
    ["limit_exceeded", "deadline_exceeded", "cancelled", "unverifiable", "measure_failed"],
)
def test_p1_001_servico_nao_prossegue_quando_a_medicao_nao_prova_a_integridade(
    h: Harness, failure: str
) -> None:
    """Falha de medição *sem* prova de integridade (orçamento/limite/leitura) bloqueia."""
    rig = build_rig(h)
    rig.verification.metrics = MetricsReport(failure=failure)
    result = rig.execute()
    assert result.process_started is False and rig.runner.calls == []
    assert result.integrity is IntegrityVerdict.UNVERIFIABLE
    assert result.run_status is not RunStatus.OK and result.status is not VerifyStatus.COMPLETED
    expected = {
        "deadline_exceeded": VerifyCode.TIMEOUT,
        "cancelled": VerifyCode.CANCELLED,
    }.get(failure, VerifyCode.VERIFICATION_UNVERIFIABLE)
    assert result.code is expected  # a causa é preservada; nunca vira "divergência"


# ============================================================== P1-002 — comparação interrompível


def _hard_lines(n: int) -> tuple[bytes, bytes]:
    """O padrão que fazia o `SequenceMatcher` passar de 10 s com 2 000 linhas (alfabeto pequeno,
    ordem embaralhada)."""
    base = "".join(f"{i % 7}\n" for i in range(n)).encode()
    current = "".join(f"{(i * 3) % 7}\n" for i in range(n)).encode()
    return base, current


def test_p1_002_a_contagem_consulta_o_orcamento_com_frequencia() -> None:
    base, current = _hard_lines(600)
    ticks: list[int] = []

    def tick() -> None:
        ticks.append(1)
        if len(ticks) >= 3:
            raise KeyboardInterrupt  # qualquer exceção do orçamento interrompe a contagem

    with pytest.raises(KeyboardInterrupt):
        count_changes(base, current, tick=tick, max_ops=10**9)
    assert len(ticks) == 3


def test_p1_002_a_contagem_tem_teto_explicito_de_operacoes() -> None:
    base, current = _hard_lines(600)
    assert count_changes(base, current, tick=lambda: None, max_ops=10**9) is not None
    with pytest.raises(ds._Unmeasurable) as caught:
        count_changes(base, current, tick=lambda: None, max_ops=100)
    assert caught.value.failure is F.TOO_LARGE


def _start_marker(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    """Registra o instante em que a **comparação de linhas** começa (depois da verificação e das
    leituras), para que cancelamento e prazo sejam armados *dentro* dela."""
    marks: dict[str, float] = {}
    # `_count` (depois da correção) ou `count_changes` (implementação auditada).
    name = "_count" if hasattr(ds, "_count") else "count_changes"
    real = getattr(ds, name)

    def spy(*args: Any, **kwargs: Any) -> Any:
        marks.setdefault("start", time.monotonic())
        return real(*args, **kwargs)

    monkeypatch.setattr(ds, name, spy)
    return marks


def test_p1_002_cancelamento_interrompe_a_medicao_em_tempo_curto(
    rig_factory: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Sem teto de operações: só o cancelamento pode parar a comparação.
    monkeypatch.setattr(ds, "MAX_DIFF_OPS", 10**12, raising=False)
    marks = _start_marker(monkeypatch)
    base, current = _hard_lines(2000)
    rig = rig_factory(base_files={"p.txt": base})
    out = run_and_measure(
        rig,
        lambda: inside(rig, "p.txt").write_bytes(current),
        is_cancelled=lambda: time.monotonic() > marks.get("start", float("inf")) + 0.3,
    )
    elapsed = time.monotonic() - marks["start"]
    assert out is F.CANCELLED, out
    assert elapsed < 3.0, f"a comparação ignorou o cancelamento por {elapsed:.1f}s"


def test_p1_002_prazo_do_orcamento_interrompe_a_medicao_em_tempo_curto(
    rig_factory: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ds, "MAX_DIFF_OPS", 10**12, raising=False)
    marks = _start_marker(monkeypatch)
    base, current = _hard_lines(2000)
    rig = rig_factory(base_files={"p.txt": base})
    real = time.monotonic

    def clock() -> float:
        # Antes da comparação o relógio é o real; dentro dela anda 400x mais depressa
        # (60 s do orçamento viram 0,15 s).
        start = marks.get("start")
        if start is None:
            return real()
        return start + (real() - start) * 400

    out = run_and_measure(
        rig,
        lambda: inside(rig, "p.txt").write_bytes(current),
        verifier=PostExecutionVerifier(clock=clock),
    )
    elapsed = real() - marks["start"]
    assert out is F.DEADLINE_EXCEEDED, out
    assert elapsed < 3.0, f"a comparação ignorou o prazo por {elapsed:.1f}s"


def test_p1_002_o_resultado_continua_exato_nos_casos_pequenos() -> None:
    assert count_changes(b"a\nb\nc\n", b"a\nX\nc\n", tick=lambda: None, max_ops=10**6) == (1, 1)
    assert count_changes(b"", b"a\n", tick=lambda: None, max_ops=10**6) == (1, 0)
    assert count_changes(b"a\n", b"", tick=lambda: None, max_ops=10**6) == (0, 1)


def test_p1_002_integridade_nao_comprovada_pelo_orcamento_nao_vira_nulo_permitido() -> None:
    assert F.CANCELLED not in ds.INTEGRITY_PROVEN_FAILURES
    assert F.DEADLINE_EXCEEDED not in ds.INTEGRITY_PROVEN_FAILURES
    assert F.LIMIT_EXCEEDED not in ds.INTEGRITY_PROVEN_FAILURES
    assert F.UNVERIFIABLE not in ds.INTEGRITY_PROVEN_FAILURES
    assert {F.BINARY, F.TOO_LARGE, F.TOO_MANY_PATHS} == set(ds.INTEGRITY_PROVEN_FAILURES)


# ================================================================= P1-003 — árvore × binding


@pytest.mark.parametrize("binding_ok", [True, False])
def test_p1_003_arvore_nao_confirmada_prevalece_sobre_binding_perdido(binding_ok: bool) -> None:
    report = exited(0, tree_confirmed_dead=False)
    decision = decide_after_runner(report, cause=None, binding_verified=binding_ok)
    assert decision.requires_finalization is True
    assert decision.run_status is not RunStatus.OK
    if not binding_ok:
        assert decision.code is VerifyCode.BINDING_REVOKED  # a outra evidência é preservada


@pytest.mark.parametrize("code", [0, 1])
def test_p1_003_servico_devolve_unfinalized_com_binding_perdido_e_arvore_nao_provada(
    h: Harness, code: int
) -> None:
    rig = build_rig(
        h, runner=ScriptedRunner(exited(code, tree_confirmed_dead=False)), fail_binding_from=3
    )
    result = rig.execute()
    assert result.process_started is True
    assert result.requires_finalization is True
    assert result.status is VerifyStatus.UNFINALIZED
    assert result.code is VerifyCode.BINDING_REVOKED  # não apaga a falha de binding
    assert result.run_status is not RunStatus.OK


# ===================================================================== P1-004 — prazo da passagem


def test_p1_004_prazo_fixado_antes_do_claim_conta_o_tempo_do_claim(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build_rig(h)
    real_open = em.open_runner_run

    def slow_claim(session: Session, task_id: str, *, spec: Any) -> Any:
        out = real_open(session, task_id, spec=spec)
        rig.clock.advance(1201)  # o claim "demorou" mais que o run_timeout_s (1200)
        return out

    monkeypatch.setattr(em, "open_runner_run", slow_claim)
    result = rig.execute()
    assert rig.runner.calls == [], "o Test Runner não pode ser chamado com o prazo já vencido"
    assert rig.verification.verify_calls == []
    assert result.process_started is False
    assert result.run_status is RunStatus.TIMEOUT and result.code is VerifyCode.TIMEOUT


def test_p1_004_prazo_nao_e_reconstruido_depois_da_verificacao(h: Harness) -> None:
    rig = build_rig(h)

    def burn(_request: Any) -> None:
        rig.clock.advance(1201)  # a verificação consome o orçamento inteiro

    rig.verification.before = burn
    result = rig.execute()
    assert rig.runner.calls == []
    assert result.process_started is False and result.run_status is not RunStatus.OK


# ===================================================================== P2-005 — worktree no claim


def _existing(**changes: Any) -> ExistingRunFacts:
    base: dict[str, Any] = {
        "invocation_id": "x",
        "task_id": "t",
        "agent": "test_runner",
        "purpose": "execution",
        "attempt_index": 0,
        "fix_round": 0,
        "base_commit": "a" * 40,
        "context_manifest_id": "m",
        "subject_run_id": None,
        "supersedes_run_id": None,
        "provider": "system",
        "provider_adapter": "generic-subprocess-v1",
        "adapter_version": "1",
        "model": None,
        "transport": "process",
        "control_linked": True,
        "worktree_path": "/w/ok",
    }
    return ExistingRunFacts(**{**base, **changes})


def test_p2_005_claim_compativel_exige_a_mesma_worktree() -> None:
    from datetime import UTC, datetime

    from app.orchestrator.execution_contract import component_invocation_id
    from app.orchestrator.verify_and_test_contract import RunnerRunSpec

    control = "11111111-1111-4111-8111-111111111111"
    key = component_invocation_id(control, "test_runner")
    spec = RunnerRunSpec(
        invocation_id=key,
        control_run_id=control,
        developer_run_id="d",
        context_manifest_id="m",
        identity=ID,
        base_commit="a" * 40,
        worktree_path="/w/ok",
        started_at=datetime.now(UTC),
    )
    assert runner_claim_compatible(_existing(invocation_id=key), spec, "t") is True
    for other in ("/w/outra", None, ""):
        assert (
            runner_claim_compatible(_existing(invocation_id=key, worktree_path=other), spec, "t")
            is False
        )


def _foreign_runner_run(rig: VRig, **changes: Any) -> None:
    """Um Run `test_runner` idêntico em tudo ao esperado — menos a worktree."""
    from datetime import UTC, datetime

    control = rig.h.run(rig.control_run_id)
    values: dict[str, Any] = {
        "invocation_id": runner_invocation_id(rig),
        "task_id": rig.dev.task.id,
        "context_manifest_id": control.context_manifest_id,
        "agent": RunAgent.TEST_RUNNER,
        "purpose": RunPurpose.EXECUTION,
        "attempt_index": rig.context.attempt_index,
        "fix_round": rig.context.fix_round,
        "provider": "system",
        "provider_adapter": "generic-subprocess-v1",
        "adapter_version": "1",
        "model": None,
        "transport": RunTransport.PROCESS,
        "status": RunStatus.RUNNING,
        "started_at": datetime.now(UTC),
        "token_source": TokenSource.UNAVAILABLE,
        "files_read_source": FilesReadSource.UNAVAILABLE,
        "base_commit": rig.context.base_commit,
        "worktree_path": "/outra/worktree",
    }
    with rig.h.factory() as fresh:
        fresh.add(Run(**{**values, **changes}))
        fresh.commit()


def test_p2_005_worktree_incompativel_e_colisao_sem_adotar_nem_executar(h: Harness) -> None:
    rig = build_rig(h)
    _foreign_runner_run(rig)
    result = rig.execute()
    assert result.status is VerifyStatus.REFUSED and result.code is VerifyCode.CLAIM_COLLISION
    assert result.run_id is None and result.run_status is None
    assert rig.verification.verify_calls == [] and rig.runner.calls == []
    [row] = runner_runs(rig)
    assert row.worktree_path == "/outra/worktree" and row.status is RunStatus.RUNNING


def test_p2_005_worktree_incompativel_no_claim_incerto_tambem_e_colisao(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build_rig(h)
    _foreign_runner_run(rig)

    def uncertain(session: Session, task_id: str, *, spec: Any) -> Any:
        raise RuntimeError("conexão perdida")

    monkeypatch.setattr(em, "open_runner_run", uncertain)
    result = rig.execute()
    assert result.code is VerifyCode.CLAIM_COLLISION and result.status is VerifyStatus.REFUSED
    assert rig.verification.verify_calls == [] and rig.runner.calls == []


def test_p2_005_worktree_incompativel_na_recuperacao_find_runner_claim(h: Harness) -> None:
    from tests.test_verify_and_test_e8_4_4 import _spec

    rig = build_rig(h)
    _foreign_runner_run(rig)
    with h.factory() as fresh:
        found = em.find_runner_claim(fresh, rig.dev.task.id, spec=_spec(rig))
    assert found is not None and found.outcome.value == "collision"
    assert found.run_id is None and found.existing_status is None
    with h.factory() as fresh:
        opened = em.open_runner_run(fresh, rig.dev.task.id, spec=_spec(rig))
    assert opened.outcome.value == "collision"


def test_p2_005_mesma_worktree_continua_sendo_already(h: Harness) -> None:
    rig = build_rig(h)
    _foreign_runner_run(rig, worktree_path=rig.context.worktree_path)
    result = rig.execute()
    assert result.status is VerifyStatus.ALREADY_CLAIMED


# ============================================================ P2-006 — evento sem afirmação falsa


@pytest.mark.parametrize("dead", [False, None])
@pytest.mark.parametrize("failure", [RunnerFailureCode.TIMEOUT, RunnerFailureCode.CANCELLED])
def test_p2_006_evento_nao_afirma_encerramento_nao_comprovado(
    h: Harness, failure: RunnerFailureCode, dead: bool | None
) -> None:
    holder: dict[str, VRig] = {}

    def report(request: Any) -> Any:
        if failure is RunnerFailureCode.CANCELLED:
            holder["rig"].signal.flag = True
            request.is_cancelled()  # o runner real observa o sinal antes de abortar
        return failed(failure.value, dead=dead)

    rig = build_rig(h, runner=ScriptedRunner(report))
    holder["rig"] = rig
    result = rig.execute()
    [event] = events_of(rig, result.run_id)
    text = f"{event.rule_id} {event.subject} {event.detail}".lower()
    assert "foi encerrada" not in text and "encerrada" not in text.replace("não comprovado", "")
    # O texto acompanha o fato: pendência ⇔ "não comprovado"; sem pendência, nenhuma afirmação.
    assert ("não comprovado" in text) == result.requires_finalization
    if failure is RunnerFailureCode.TIMEOUT or dead is False:
        assert result.requires_finalization is True
    assert event.kind in (SafetyEventKind.TIMEOUT, SafetyEventKind.CANCELLED)


def test_p2_006_encerramento_comprovado_e_pendencia_sao_eventos_distintos() -> None:
    proven = VerifyDecision(
        RunStatus.TIMEOUT, VerifyCode.TIMEOUT, IntegrityVerdict.VERIFIED, RunnerOutcome.TIMEOUT
    )
    pending = VerifyDecision(
        RunStatus.TIMEOUT,
        VerifyCode.TIMEOUT,
        IntegrityVerdict.VERIFIED,
        RunnerOutcome.TIMEOUT,
        requires_finalization=True,
    )
    [a] = runner_events(proven)
    [b] = runner_events(pending)
    assert a.rule_id != b.rule_id and a.detail != b.detail
    assert "não comprovado" in b.detail and "não comprovado" not in a.detail
    assert "foi encerrada" not in a.detail + b.detail


def test_p2_006_nenhum_evento_carrega_texto_de_excecao(h: Harness) -> None:
    rig = build_rig(h, runner=ScriptedRunner(raises=RuntimeError("segredo-da-excecao")))
    result = rig.execute()
    for event in events_of(rig, result.run_id):
        assert "segredo-da-excecao" not in f"{event.subject} {event.detail} {event.rule_id}"


_ = (never, threading, StopCause, RunnerReport, DiffMetrics)


def test_p1_001_diretorio_novo_trocado_por_arquivo_e_divergencia(
    rig_factory: Callable[..., Rig],
) -> None:
    rig = rig_factory()

    def effect() -> None:
        inside(rig, "pasta_nova").mkdir()
        write(inside(rig, "pasta_nova/f.txt"), "1\n")

    def swap() -> None:
        (inside(rig, "pasta_nova") / "f.txt").unlink()
        inside(rig, "pasta_nova").rmdir()
        write(inside(rig, "pasta_nova"), "agora sou arquivo\n")

    # `pasta_nova` (diretório) e `pasta_nova/f.txt` foram verificados; ambos mudaram de tipo.
    assert run_and_measure(rig, effect, between=swap) is F.CHANGED_SINCE_VERIFIED


def test_p1_001_diretorio_vazio_novo_trocado_por_arquivo_e_divergencia(
    rig_factory: Callable[..., Rig],
) -> None:
    rig = rig_factory()

    def swap() -> None:
        inside(rig, "pasta_vazia").rmdir()
        write(inside(rig, "pasta_vazia"), "agora sou arquivo\n")

    out = run_and_measure(rig, lambda: inside(rig, "pasta_vazia").mkdir(), between=swap)
    assert out is F.CHANGED_SINCE_VERIFIED


def test_p1_002_o_teto_de_operacoes_vale_antes_do_primeiro_tick() -> None:
    """Entrada minúscula (bem abaixo de `TICK_EVERY` passos) já respeita `max_ops`."""
    with pytest.raises(ds._Unmeasurable) as caught:
        count_changes(b"a\nb\n", b"c\nd\n", tick=lambda: None, max_ops=1)
    assert caught.value.failure is F.TOO_LARGE
    assert count_changes(b"a\nb\n", b"c\nd\n", tick=lambda: None, max_ops=10**6) == (2, 2)


def test_p1_002_o_teto_de_operacoes_e_cumulativo_no_diff_inteiro(
    rig_factory: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    base, current = _hard_lines(300)
    one_file = ds._count(base, current, lambda: None, 10**9)[2]
    rig = rig_factory(base_files={"p1.txt": base, "p2.txt": base})

    def effect() -> None:
        inside(rig, "p1.txt").write_bytes(current)
        inside(rig, "p2.txt").write_bytes(current)

    # Cada arquivo sozinho cabe; os dois juntos, não.
    monkeypatch.setattr(ds, "MAX_DIFF_OPS", int(one_file * 1.5))
    assert run_and_measure(rig, effect) is F.TOO_LARGE
    monkeypatch.setattr(ds, "MAX_DIFF_OPS", int(one_file * 2.5))
    rig2 = rig_factory(base_files={"p1.txt": base, "p2.txt": base})

    def effect2() -> None:
        inside(rig2, "p1.txt").write_bytes(current)
        inside(rig2, "p2.txt").write_bytes(current)

    assert isinstance(run_and_measure(rig2, effect2), DiffMetrics)


def test_p1_001_arquivo_verificado_trocado_por_link_com_o_mesmo_conteudo_e_divergencia(
    rig_factory: Callable[..., Rig], tmp_path: Path
) -> None:
    if not supports_symlinks(tmp_path):
        pytest.skip("symlink indisponível nesta plataforma")
    rig = rig_factory()

    def swap() -> None:
        alvo = inside(rig, "alvo.txt")
        alvo.write_bytes(b"A = 2\n")
        inside(rig, "a.py").unlink()
        inside(rig, "a.py").symlink_to(alvo)

    out = run_and_measure(rig, lambda: write(inside(rig, "a.py"), "A = 2\n"), between=swap)
    assert out is F.CHANGED_SINCE_VERIFIED
