"""E8.4.4 — contraprovas da reauditoria (Codex, BLOCKED: P2-R1 e P2-R2).

* P2-R1 — o Myers devolvia sucesso ao concluir **antes** de validar `max_ops` (5 000 001 operações
  com teto de 5 000 000 viravam `DiffMetrics` completo) e sem consultar cancelamento/prazo na
  conclusão;
* P2-R2 — o claim do **Developer** (E8.4.3) não comparava a worktree: um Run com a mesma chave e
  identidade, mas worktree divergente, era adotado como `ALREADY_CLAIMED`.
  (Correção retroativa da E8.4.3 incluída na E8.4.4.)
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings
from app.db.enums import FilesReadSource, RunAgent, RunPurpose, RunStatus, RunTransport, TokenSource
from app.db.models import DevWorkspace, Run
from app.git_runtime import diff_stats as ds
from app.git_runtime.diff_stats import DiffMeasureFailure as F
from app.git_runtime.diff_stats import DiffMetrics, count_changes
from app.orchestrator import execution_manager as em
from app.orchestrator.developer_invocation_contract import (
    ClaimOutcome,
    DeveloperRunSpec,
    InvocationCode,
    InvocationStatus,
)
from app.orchestrator.fingerprint import tool_profile_hash
from tests import test_diff_stats_e8_4_4 as _diff_tests
from tests.developer_invocation_support_e8_4_3 import build_scripted
from tests.test_developer_invocation_e8_4_3 import dev_id, dev_runs
from tests.test_diff_stats_e8_4_4 import inside, run_and_measure
from tests.test_execution_workspace_e8_4_2 import Harness
from tests.test_post_execution_verification_e8_3 import Rig

pytestmark = pytest.mark.skipif(
    __import__("shutil").which("git") is None, reason="git indisponível no PATH"
)

rig_factory = _diff_tests.rig_factory
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


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


# ======================================================== P2-R1 — teto estrito e conclusão


def _pair(n: int) -> tuple[bytes, bytes]:
    base = "".join(f"{i % 7}\n" for i in range(n)).encode()
    current = "".join(f"{(i * 3) % 7}\n" for i in range(n)).encode()
    return base, current


def _noop() -> None:
    return None


def _ops_of(base: bytes, current: bytes) -> int:
    return int(ds._count(base, current, _noop, 10**12)[2])


@pytest.mark.parametrize("size", [3, 40, 300])
def test_r1_limite_exatamente_atingido_passa_e_uma_operacao_a_mais_nao(size: int) -> None:
    base, current = _pair(size)
    ops = _ops_of(base, current)
    assert ops > 0
    exact = count_changes(base, current, tick=_noop, max_ops=ops)
    assert exact == count_changes(base, current, tick=_noop, max_ops=10**12)
    with pytest.raises(ds._Unmeasurable) as caught:  # ultrapassado por UMA operação
        count_changes(base, current, tick=_noop, max_ops=ops - 1)
    assert caught.value.failure is F.TOO_LARGE


def test_r1_conclusao_entre_dois_ticks_tambem_respeita_o_teto() -> None:
    """Passa de 2 048 passos (há ticks) e termina longe de uma fronteira de tick."""
    base, current = _pair(300)
    ops = _ops_of(base, current)
    assert ops > 2 * ds.TICK_EVERY and ops % ds.TICK_EVERY not in (0, ds.TICK_EVERY - 1)
    with pytest.raises(ds._Unmeasurable):
        count_changes(base, current, tick=_noop, max_ops=ops - 1)


def test_r1_toda_conclusao_bem_sucedida_consulta_cancelamento_e_prazo() -> None:
    for base, current in ((b"a\nb\n", b"a\nc\n"), (b"", b"x\n"), (b"x\n", b""), (b"a\n", b"a\n")):
        calls: list[int] = []

        def tick(calls: list[int] = calls) -> None:
            calls.append(1)
            raise KeyboardInterrupt  # cancelamento/prazo chegando exatamente na conclusão

        with pytest.raises(KeyboardInterrupt):
            count_changes(base, current, tick=tick, max_ops=10**6)
        assert calls == [1]


def test_r1_o_teto_para_o_laco_perto_do_limite_e_nao_so_no_proximo_tick() -> None:
    """Mutante N16: sem o `min(..., max_ops + 1)` o laço seguiria até 2 048 passos além do teto."""

    class Counting(list[int]):
        reads = 0

        def __getitem__(self, index: Any) -> Any:
            type(self).reads += 1
            return super().__getitem__(index)

    n = 400
    first = Counting(i % 7 for i in range(n))
    second = Counting((i * 3) % 7 for i in range(n))
    ceiling = 3 * ds.TICK_EVERY // 2  # entre o 1º e o 2º tick
    ticks: list[int] = []
    with pytest.raises(ds._Unmeasurable):
        ds._edit_distance(first, second, lambda: ticks.append(1), ceiling)
    assert ticks == [1]  # só o tick de 2 048; o teto vale antes do próximo
    # Cada passo lê no máximo duas posições; o sobrepasso é de um laço, não de um tick inteiro.
    assert Counting.reads <= 2 * (ceiling + 2 * n), Counting.reads


def test_r1_orcamento_acumulado_entre_arquivos_inclusive_no_limite_exato(
    rig_factory: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    base, current = _pair(120)
    one = _ops_of(base, current)

    def build() -> tuple[Rig, Callable[[], None]]:
        rig = rig_factory(base_files={"p1.txt": base, "p2.txt": base})

        def effect() -> None:
            inside(rig, "p1.txt").write_bytes(current)
            inside(rig, "p2.txt").write_bytes(current)

        return rig, effect

    rig, effect = build()
    monkeypatch.setattr(ds, "MAX_DIFF_OPS", 2 * one - 1)  # ultrapassado por uma operação
    assert run_and_measure(rig, effect) is F.TOO_LARGE
    rig, effect = build()
    monkeypatch.setattr(ds, "MAX_DIFF_OPS", 2 * one)  # exatamente atingido
    assert isinstance(run_and_measure(rig, effect), DiffMetrics)


def test_r1_contraprova_original_um_a_mais_que_o_teto_nao_devolve_metricas(
    rig_factory: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    base, current = _pair(150)
    rig = rig_factory(base_files={"p.txt": base})
    monkeypatch.setattr(ds, "MAX_DIFF_OPS", _ops_of(base, current) - 1)
    assert run_and_measure(rig, lambda: inside(rig, "p.txt").write_bytes(current)) is F.TOO_LARGE


# ================================================= P2-R2 — worktree na identidade do Developer


def _foreign_developer_run(rig: Any, worktree: str | None) -> str:
    """Um Run `developer` idêntico ao claim esperado em tudo — menos a worktree."""
    control = rig.h.run(rig.control_run_id)
    identity = rig.identity
    row = Run(
        invocation_id=dev_id(rig),
        task_id=rig.task.id,
        context_manifest_id=control.context_manifest_id,
        agent=RunAgent.DEVELOPER,
        purpose=RunPurpose.EXECUTION,
        attempt_index=rig.context.attempt_index,
        fix_round=rig.context.fix_round,
        provider=identity.provider,
        provider_adapter=identity.adapter,
        adapter_version=identity.adapter_version,
        model=identity.model,
        transport=RunTransport(identity.transport),
        status=RunStatus.RUNNING,
        started_at=datetime.now(UTC),
        token_source=TokenSource.UNAVAILABLE,
        files_read_source=FilesReadSource.UNAVAILABLE,
        base_commit=rig.context.base_commit,
        worktree_path=worktree,
    )
    with rig.h.factory() as fresh:
        fresh.add(row)
        fresh.commit()
        return row.id


def _spec(rig: Any, **changes: Any) -> DeveloperRunSpec:
    control = rig.h.run(rig.control_run_id)
    base: dict[str, Any] = {
        "invocation_id": dev_id(rig),
        "control_run_id": rig.control_run_id,
        "context_manifest_id": control.context_manifest_id,
        "identity": rig.identity,
        "tool_profile_hash": tool_profile_hash(),
        "base_commit": rig.context.base_commit,
        "worktree_path": rig.context.worktree_path,
        "expected_task_version": rig.context.task_version,
        "attempt_index": rig.context.attempt_index,
        "fix_round": rig.context.fix_round,
        "started_at": datetime.now(UTC),
    }
    return DeveloperRunSpec(**{**base, **changes})


def _unchanged(rig: Any, run_id: str, worktree: str | None) -> None:
    [row] = dev_runs(rig)
    assert row.id == run_id and row.worktree_path == worktree and row.status is RunStatus.RUNNING


@pytest.mark.parametrize("worktree", ["/outra/worktree", None, ""])
def test_r2_abertura_com_worktree_divergente_ou_ausente_e_colisao(
    h: Harness, worktree: str | None
) -> None:
    rig = build_scripted(h)
    runtime = rig.runtime
    run_id = _foreign_developer_run(rig, worktree)
    result = rig.invoke()
    assert result.status is InvocationStatus.REFUSED
    assert result.code is InvocationCode.CLAIM_COLLISION
    assert result.run_id is None and result.run_status is None  # nada do Run alheio vaza
    assert runtime.calls == []  # zero chamadas ao provider
    _unchanged(rig, run_id, worktree)


def test_r2_claim_incerto_com_worktree_divergente_e_colisao(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build_scripted(h)
    run_id = _foreign_developer_run(rig, "/outra/worktree")

    def lost(session: Session, task_id: str, *, spec: Any) -> Any:
        raise RuntimeError("conexão perdida depois do COMMIT")

    monkeypatch.setattr(em, "open_developer_run", lost)
    result = rig.invoke()
    assert result.status is InvocationStatus.REFUSED
    assert result.code is InvocationCode.CLAIM_COLLISION
    assert result.run_id is None and rig.runtime.calls == []
    _unchanged(rig, run_id, "/outra/worktree")


def test_r2_recuperacao_e_abertura_diretas_nao_adotam_run_alheio(h: Harness) -> None:
    rig = build_scripted(h)
    run_id = _foreign_developer_run(rig, "/outra/worktree")
    with h.factory() as fresh:
        found = em.find_developer_claim(fresh, rig.task.id, spec=_spec(rig))
    assert found is not None and found.outcome is ClaimOutcome.COLLISION
    assert found.run_id is None and found.existing_status is None
    with h.factory() as fresh:
        opened = em.open_developer_run(fresh, rig.task.id, spec=_spec(rig))
    assert opened.outcome is ClaimOutcome.COLLISION
    assert opened.run_id is None and opened.existing_status is None
    _unchanged(rig, run_id, "/outra/worktree")


def test_r2_claim_legitimo_na_mesma_worktree_continua_reconhecido(h: Harness) -> None:
    rig = build_scripted(h)
    run_id = _foreign_developer_run(rig, rig.context.worktree_path)
    result = rig.invoke()
    assert result.status is InvocationStatus.ALREADY_CLAIMED
    assert result.run_id == run_id and result.requires_finalization is True
    assert rig.runtime.calls == []
    with h.factory() as fresh:
        found = em.find_developer_claim(fresh, rig.task.id, spec=_spec(rig))
        again = em.open_developer_run(fresh, rig.task.id, spec=_spec(rig))
    assert found is not None and found.outcome is ClaimOutcome.ALREADY and found.run_id == run_id
    assert again.outcome is ClaimOutcome.ALREADY and again.run_id == run_id
    with h.factory() as fresh:
        assert len(list(fresh.scalars(select(Run).where(Run.invocation_id == dev_id(rig))))) == 1


def test_r2_a_worktree_do_spec_e_a_do_banco_nao_a_do_chamador(h: Harness) -> None:
    """A worktree que entra no claim é a do contexto preparado, já conferida contra a publicada
    pelo banco (`_context_incoherence`): um contexto com outra worktree nem chega ao claim."""
    from dataclasses import replace

    rig = build_scripted(h)
    stale = replace(rig.context, worktree_path="/outra/worktree")
    result = rig.service.invoke(stale, is_cancelled=rig.signal)
    assert result.status is InvocationStatus.REFUSED
    assert result.code is InvocationCode.REFUSED_FACTS
    assert rig.runtime.calls == [] and dev_runs(rig) == []
