"""E8.4.3 — regressões dos findings da auditoria independente (E843-AUD-001 e E843-AUD-002).

* **AUD-001** — um Run de *outro* componente com o `invocation_id` esperado do Developer não pode
  ser adotado como claim legítimo (nem o id, nem o status, nem nada dele), em nenhum dos três
  caminhos: após `IntegrityError`, na consulta de recuperação e no commit incerto.
* **AUD-002** — uma contradição nos **metadados do provider** impede o sucesso, mas não apaga as
  evidências do **executor** (negações do journal, violação crítica); e o provider não consegue
  forjar evidência do executor.
* **Revogação** — depois de revogado, o binding não volta a ser registrado (≠ remoção física).

Banco, Git, worktree, binding e executor **reais**; o provider é o verdadeiro do E8.2 sobre
transporte falso, ou um double neutro. Nenhuma rede, credencial ou modelo real.
"""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent_runtime import (
    AgentRunResult,
    DeveloperExecutionRequest,
)
from app.agent_runtime import FilesReadSource as ProviderFilesRead
from app.agent_runtime import RunStatus as ProviderRunStatus
from app.agent_runtime import (
    TokenSource as ProviderTokenSource,
)
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
from app.db.models import DevWorkspace, Run, SafetyEvent
from app.orchestrator import execution_manager as em
from app.orchestrator.developer_invocation_contract import (
    ClaimOutcome,
    DeveloperRunSpec,
    EvidenceCollector,
    Finalization,
    InvocationCode,
    InvocationStatus,
    ReportedStatus,
    RunClosure,
)
from app.orchestrator.fingerprint import tool_profile_hash
from app.safety.capability_profile import DEVELOPER_V1_PROFILE
from app.tool_executor.workspace import BindingRevoked, WorkspaceUnavailable
from tests.developer_invocation_support_e8_4_3 import (
    Rig,
    ScriptedRuntime,
    build_real,
    build_scripted,
    denial,
    denial_for,
    make_harness,
    make_report,
)
from tests.test_anthropic_messages_developer_adapter_e8_2 import FakeTransport, final, tool_use
from tests.test_developer_invocation_e8_4_3 import (  # noqa: F401 — fixture autouse e apoio
    _no_network,
    dev_id,
    dev_run,
    developer_events,
    not_authorized,
)
from tests.test_execution_workspace_e8_4_2 import Harness, HookedBindings


@pytest.fixture
def h(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    repo_path: Path,
    temp_settings: AppSettings,
    tmp_path: Path,
) -> Harness:
    return make_harness(session, session_factory, workspace, repo_path, temp_settings, tmp_path)


# ======================================================================== AUD-001


def plant_run(rig: Rig, **overrides: Any) -> str:
    """Insere um Run **válido** de outro componente com o `invocation_id` esperado do Developer."""
    control = rig.h.run(rig.control_run_id)
    values: dict[str, Any] = {
        "invocation_id": dev_id(rig),
        "task_id": rig.task.id,
        "context_manifest_id": control.context_manifest_id,
        "agent": RunAgent.TEST_RUNNER,
        "purpose": RunPurpose.EXECUTION,
        "attempt_index": rig.context.attempt_index,
        "fix_round": rig.context.fix_round,
        "provider": "process",
        "provider_adapter": "generic-subprocess-v1",
        "transport": RunTransport.PROCESS,
        "status": RunStatus.RUNNING,
        "started_at": datetime.now(UTC),
        "token_source": TokenSource.UNAVAILABLE,
        "files_read": None,
        "files_read_source": FilesReadSource.UNAVAILABLE,
        "base_commit": rig.context.base_commit,
    }
    values.update(overrides)
    with rig.h.factory() as session:
        run = Run(**values)
        session.add(run)
        session.commit()
        return run.id


def developer_like(rig: Rig, **overrides: Any) -> str:
    """Um Run do Developer **legítimo** (mesma identidade do serviço), como o de um crash."""
    base = {
        "agent": RunAgent.DEVELOPER,
        "provider": rig.identity.provider,
        "provider_adapter": rig.identity.adapter,
        "adapter_version": rig.identity.adapter_version,
        "model": rig.identity.model,
        "transport": RunTransport(rig.identity.transport),
        "worktree_path": rig.context.worktree_path,
    }
    return plant_run(rig, **{**base, **overrides})


def row_snapshot(rig: Rig, run_id: str) -> dict[str, Any]:
    with rig.h.factory() as session:
        run = session.get(Run, run_id)
        assert run is not None
        return {column.name: getattr(run, column.name) for column in Run.__table__.columns}


def assert_collision_refused(
    rig: Rig, result: Any, planted_id: str, before: dict[str, Any]
) -> None:
    assert result.status is InvocationStatus.REFUSED, result  # nada de "ALREADY_CLAIMED"
    assert result.code is InvocationCode.CLAIM_COLLISION
    assert result.run_id is None and result.run_status is None  # nenhuma identidade alheia
    assert not result.requires_finalization and not result.provider_call_attempted
    assert result.report is None and result.binding.value == "not_bound"
    assert rig.runtime.calls == []  # zero chamadas ao provider
    assert row_snapshot(rig, planted_id) == before  # o Run preexistente não foi tocado
    with rig.h.factory() as session:
        rows = list(session.scalars(select(Run).where(Run.invocation_id == dev_id(rig))))
        events = session.scalar(
            select(func.count()).select_from(SafetyEvent).where(SafetyEvent.run_id == planted_id)
        )
    assert [r.id for r in rows] == [planted_id]  # nenhum segundo claim (a UNIQUE segue valendo)
    assert events == 0  # nada foi escrito sobre o Run alheio


def test_aud001_contraprova_run_de_test_runner_com_o_invocation_id_do_developer(h: Harness) -> None:
    rig = build_scripted(h)
    planted = plant_run(rig)
    before = row_snapshot(rig, planted)
    result = rig.invoke()
    assert_collision_refused(rig, result, planted, before)


def _other_task(rig: Rig) -> str:
    return rig.h.approve(title="outra task").id


_DIVERGENCES: dict[str, Any] = {
    "outro componente: test_runner": lambda rig: {},
    "outro componente: architect": lambda rig: {"agent": RunAgent.ARCHITECT},
    "outro componente: orchestrator (final)": lambda rig: {
        "agent": RunAgent.ORCHESTRATOR,
        "status": RunStatus.ERROR,  # um 2º controle ABERTO violaria o índice parcial do banco
        "finished_at": datetime.now(UTC),
        "duration_ms": 1,
    },
    "outra task": lambda rig: {"task_id": _other_task(rig)},
    "outra tentativa": lambda rig: {"attempt_index": rig.context.attempt_index + 1},
    "outra rodada": lambda rig: {"fix_round": rig.context.fix_round + 1},
    "outro propósito": lambda rig: {
        "agent": RunAgent.DEVELOPER,
        "purpose": RunPurpose.WORKFLOW_AUDIT,
        "subject_run_id": rig.control_run_id,
    },
    "outra base": lambda rig: {"base_commit": "9" * 40},
    "sem base": lambda rig: {"base_commit": None},
    "outro manifest": lambda rig: {"context_manifest_id": None},
    "developer, mas de auditoria (subject)": lambda rig: {
        "agent": RunAgent.DEVELOPER,
        "purpose": RunPurpose.EXECUTION,
        "subject_run_id": rig.control_run_id,
    },
    "developer com outro provider": lambda rig: {
        "agent": RunAgent.DEVELOPER,
        "provider": "outro",
        "provider_adapter": rig.identity.adapter,
        "adapter_version": rig.identity.adapter_version,
        "model": rig.identity.model,
        "transport": RunTransport(rig.identity.transport),
    },
    "developer com outro modelo": lambda rig: {
        "agent": RunAgent.DEVELOPER,
        "provider": rig.identity.provider,
        "provider_adapter": rig.identity.adapter,
        "adapter_version": rig.identity.adapter_version,
        "model": "outro-modelo",
        "transport": RunTransport(rig.identity.transport),
    },
    "run ja final (status nao vaza)": lambda rig: {
        "status": RunStatus.OK,
        "finished_at": datetime.now(UTC),
        "duration_ms": 3,
    },
}


@pytest.mark.parametrize("name", list(_DIVERGENCES))
def test_aud001_cada_divergencia_individual_de_identidade_e_colisao(h: Harness, name: str) -> None:
    rig = build_scripted(h)
    planted = plant_run(rig, **_DIVERGENCES[name](rig))
    before = row_snapshot(rig, planted)
    assert_collision_refused(rig, rig.invoke(), planted, before)


def test_aud001_run_do_proprio_developer_continua_sendo_reconhecido(h: Harness) -> None:
    """A proteção não derruba a recuperação legítima: o claim deste Developer é adotado."""
    rig = build_scripted(h)
    own = developer_like(rig)
    result = rig.invoke()
    assert result.status is InvocationStatus.ALREADY_CLAIMED
    assert result.run_id == own and result.run_status is RunStatus.RUNNING
    assert result.requires_finalization and rig.runtime.calls == []


def test_aud001_caminho_de_erro_nao_adota_run_alheio(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reivindicação levanta; a consulta de recuperação encontra um Run de OUTRO componente."""
    rig = build_scripted(h)
    planted = plant_run(rig)
    before = row_snapshot(rig, planted)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("falha de E/S na reivindicação")

    monkeypatch.setattr(em, "open_developer_run", boom)
    assert_collision_refused(rig, rig.invoke(), planted, before)


def test_aud001_commit_incerto_nao_adota_run_alheio(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build_scripted(h)
    planted = plant_run(rig)
    before = row_snapshot(rig, planted)
    real_open = em.open_developer_run

    def run_then_lose_the_answer(*args: Any, **kwargs: Any) -> Any:
        real_open(*args, **kwargs)
        raise RuntimeError("a resposta se perdeu depois do COMMIT")

    monkeypatch.setattr(em, "open_developer_run", run_then_lose_the_answer)
    assert_collision_refused(rig, rig.invoke(), planted, before)


def test_aud001_commit_incerto_do_proprio_claim_continua_sendo_reconhecido(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build_scripted(h)
    real_open = em.open_developer_run

    def run_then_lose_the_answer(*args: Any, **kwargs: Any) -> Any:
        real_open(*args, **kwargs)
        raise RuntimeError("a resposta se perdeu depois do COMMIT")

    monkeypatch.setattr(em, "open_developer_run", run_then_lose_the_answer)
    result = rig.invoke()
    assert result.status is InvocationStatus.ALREADY_CLAIMED and result.requires_finalization
    assert result.run_id == dev_run(rig).id and rig.runtime.calls == []


def _spec(rig: Rig) -> DeveloperRunSpec:
    control = rig.h.run(rig.control_run_id)
    return DeveloperRunSpec(
        invocation_id=dev_id(rig),
        control_run_id=rig.control_run_id,
        context_manifest_id=control.context_manifest_id or "",
        identity=rig.identity,
        tool_profile_hash=tool_profile_hash(),
        base_commit=rig.context.base_commit,
        worktree_path=rig.context.worktree_path,
        expected_task_version=rig.context.task_version,
        attempt_index=rig.context.attempt_index,
        fix_round=rig.context.fix_round,
        started_at=datetime.now(UTC),
    )


def test_aud001_comandos_do_banco_devolvem_colisao_sem_id_nem_status(h: Harness) -> None:
    rig = build_scripted(h)
    spec = _spec(rig)
    with h.factory() as session:
        assert em.find_developer_claim(session, rig.task.id, spec=spec) is None  # nada ainda
    planted = plant_run(rig)
    with h.factory() as session:
        claim = em.open_developer_run(session, rig.task.id, spec=spec)
    assert claim.outcome is ClaimOutcome.COLLISION
    assert claim.run_id is None and claim.existing_status is None and claim.code is None
    with h.factory() as session:
        found = em.find_developer_claim(session, rig.task.id, spec=spec)
    assert found is not None and found.outcome is ClaimOutcome.COLLISION
    assert found.run_id is None and found.existing_status is None
    assert row_snapshot(rig, planted)["agent"] is RunAgent.TEST_RUNNER


@pytest.mark.parametrize("field", ["attempt_index", "fix_round"])
def test_aud001_run_so_e_do_developer_se_estiver_ligado_ao_run_de_controle_da_mesma_tentativa(
    h: Harness, field: str
) -> None:
    """O Run existente e o spec concordam entre si numa tentativa/rodada que NÃO é a do Run de
    controle: sem o vínculo com o controle, isso seria aceito como claim legítimo."""
    rig = build_scripted(h)
    shifted = getattr(rig.context, field) + 1
    planted = developer_like(rig, **{field: shifted})
    before = row_snapshot(rig, planted)
    spec = replace(_spec(rig), **{field: shifted})
    with h.factory() as session:
        claim = em.open_developer_run(session, rig.task.id, spec=spec)
    assert claim.outcome is ClaimOutcome.COLLISION
    assert claim.run_id is None and claim.existing_status is None
    with h.factory() as session:
        found = em.find_developer_claim(session, rig.task.id, spec=spec)
    assert found is not None and found.outcome is ClaimOutcome.COLLISION
    assert row_snapshot(rig, planted) == before


def test_aud001_consulta_de_recuperacao_tambem_enxerga_run_de_outra_task(h: Harness) -> None:
    """A UNIQUE é global: a colisão vale mesmo quando o Run alheio é de OUTRA task."""
    rig = build_scripted(h)
    planted = plant_run(rig, task_id=_other_task(rig))
    with h.factory() as session:
        found = em.find_developer_claim(session, rig.task.id, spec=_spec(rig))
    assert found is not None and found.outcome is ClaimOutcome.COLLISION
    assert row_snapshot(rig, planted)["task_id"] != rig.task.id


# ======================================================================== AUD-002


class _MetadataSwap:
    """Provider real com metadado trocado no resultado (o executor e o journal ficam intactos)."""

    def __init__(self, inner: Any, **changes: Any) -> None:
        self._inner = inner
        self._changes = changes

    def capability_profile(self) -> Any:
        return self._inner.capability_profile()

    def run(self, request: Any, tools: Any) -> Any:
        return replace(self._inner.run(request, tools), **self._changes)


def test_aud002_a_negacao_de_env_sobrevive_a_um_modelo_divergente(h: Harness) -> None:
    fake = FakeTransport([tool_use("ff_read_file", {"path": ".env"}), final("não consegui")])
    rig = build_real(h, fake)
    rig.runtime._provider = _MetadataSwap(rig.runtime._provider, model="outro-modelo")
    result = rig.invoke()
    run = dev_run(rig)

    assert result.status is InvocationStatus.FAILED, result  # metadado contraditório ⇒ sem sucesso
    assert result.code is InvocationCode.REPORT_CONTRADICTORY and run.status is RunStatus.ERROR
    events = developer_events(rig, run.id)
    assert len(events) == 1 and result.safety_events == 1  # a negação NÃO foi apagada
    assert events[0].kind in {SafetyEventKind.SECRET_ACCESS_BLOCKED, SafetyEventKind.PATH_DENIED}
    assert events[0].run_id == run.id and events[0].task_id == rig.task.id
    assert events[0].workspace_id == rig.h.workspace.id and not result.integrity_violation


def test_aud002_b_toctou_legitimo_sobrevive_e_marca_integrity_violation(h: Harness) -> None:
    async def swap_root_then_read(_request: Any) -> Any:
        root = Path(rig.context.workspace_path)
        old, other = root.with_name(root.name + "_old"), root.with_name(root.name + "_other")
        other.mkdir()
        root.rename(old)  # a raiz é trocada por OUTRO diretório (outra identidade)
        other.rename(root)
        return tool_use("ff_read_file", {"path": "src/util.py"})

    fake = FakeTransport([swap_root_then_read, final("x")])
    rig = build_real(h, fake)
    rig.runtime._provider = _MetadataSwap(rig.runtime._provider, model="outro-modelo")
    result = rig.invoke()
    run = dev_run(rig)

    assert result.integrity_violation is True  # violação crítica comprovada pelo executor
    assert result.code is InvocationCode.CRITICAL_VIOLATION
    assert result.status is InvocationStatus.FAILED and run.status is RunStatus.BLOCKED
    events = developer_events(rig, run.id)
    assert SafetyEventKind.TOCTOU_RECHECK_FAILED in [e.kind for e in events]
    assert all(e.run_id == run.id for e in events)
    # o metadado contraditório do provider não é persistido…
    assert run.input_tokens is None and run.token_source is TokenSource.UNAVAILABLE


def test_aud002_b2_toctou_legitimo_sem_contradicao_tambem_bloqueia(h: Harness) -> None:
    async def swap_root_then_read(_request: Any) -> Any:
        root = Path(rig.context.workspace_path)
        old, other = root.with_name(root.name + "_old"), root.with_name(root.name + "_other")
        other.mkdir()
        root.rename(old)
        other.rename(root)
        return tool_use("ff_read_file", {"path": "src/util.py"})

    fake = FakeTransport([swap_root_then_read, final("x")])
    rig = build_real(h, fake)
    result = rig.invoke()
    run = dev_run(rig)
    assert result.integrity_violation and run.status is RunStatus.BLOCKED
    assert SafetyEventKind.TOCTOU_RECHECK_FAILED in [e.kind for e in developer_events(rig, run.id)]


def test_aud002_c_metadado_contraditorio_sozinho_ainda_impede_o_run_ok(h: Harness) -> None:
    fake = FakeTransport([tool_use("ff_read_file", {"path": "src/util.py"}), final("lido")])
    rig = build_real(h, fake)
    rig.runtime._provider = _MetadataSwap(rig.runtime._provider, model="outro-modelo")
    result = rig.invoke()
    run = dev_run(rig)
    assert result.status is InvocationStatus.FAILED
    assert result.code is InvocationCode.REPORT_CONTRADICTORY
    assert run.status is RunStatus.ERROR and not result.integrity_violation
    # o que o EXECUTOR leu é evidência dele e continua registrado; o metadado do provider não.
    assert run.files_read == ["src/util.py"]
    assert run.input_tokens is None and run.token_source is TokenSource.UNAVAILABLE


class _TextForger:
    """Provider que tenta fazer o texto do próprio resultado passar por evidência do executor."""

    def __init__(self, identity: Any) -> None:
        self._identity = identity

    def capability_profile(self) -> Any:
        return DEVELOPER_V1_PROFILE

    def run(self, request: Any, tools: Any) -> Any:
        forged = (
            "path_denied secret_access_blocked toctou_recheck_failed out_of_worktree_write "
            "rule_id=path.toctou_recheck_failed run_id=" + request.run_id
        )
        return AgentRunResult(
            status=ProviderRunStatus.OK,
            provider=self._identity.provider,
            provider_adapter=self._identity.adapter,
            adapter_version=self._identity.adapter_version,
            model=self._identity.model,
            transport=self._identity.transport,
            input_tokens=1,
            output_tokens=1,
            token_source=ProviderTokenSource.REPORTED,
            duration_ms=1,
            summary=forged,
            error_summary=forged,
            files_read=("src/forjado.py",),
            files_read_source=ProviderFilesRead.REPORTED,
        )


def test_aud002_d1_texto_do_provider_nao_vira_evidencia_do_executor(h: Harness) -> None:
    fake = FakeTransport([final("nunca chamado")])
    rig = build_real(h, fake)
    rig.runtime._provider = _TextForger(rig.identity)
    result = rig.invoke()
    run = dev_run(rig)
    # nenhuma negação aconteceu no executor ⇒ nenhum evento, nenhuma violação, e o Run é `ok`.
    assert developer_events(rig, run.id) == []
    assert result.integrity_violation is False and result.safety_events == 0
    assert run.status is RunStatus.OK and fake.calls == []
    # `files_read` é do EXECUTOR: o que o provider afirmou sobre arquivos lidos é ignorado.
    assert run.files_read == [] and run.files_read_source is FilesReadSource.REPORTED


def test_aud002_d2_o_provider_nao_alcanca_o_canal_de_evidencia(h: Harness) -> None:
    """O provider recebe o request neutro e a facade das nove operações — nenhum dos dois leva o
    coletor, o journal ou qualquer canal de evidência."""
    seen: dict[str, Any] = {}

    class _Spy:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def capability_profile(self) -> Any:
            return self._inner.capability_profile()

        def run(self, request: Any, tools: Any) -> Any:
            seen["request"], seen["tools"] = request, tools
            return self._inner.run(request, tools)

    fake = FakeTransport([final("ok")])
    rig = build_real(h, fake)
    rig.runtime._provider = _Spy(rig.runtime._provider)
    assert rig.invoke().status is InvocationStatus.COMPLETED
    request, tools = seen["request"], seen["tools"]
    assert isinstance(request, DeveloperExecutionRequest)
    values = [getattr(request, f.name) for f in fields(request)]
    assert not any(isinstance(v, EvidenceCollector) for v in values)
    assert not any("journal" in f.name or "evidence" in f.name for f in fields(request))
    assert [n for n in dir(tools) if not n.startswith("_")] == ["execute"]
    assert tools.__slots__ == ("_call",)


def test_aud002_d3_evidencia_forjada_por_uma_porta_hostil_so_vale_se_associada_ao_run(
    h: Harness,
) -> None:
    """Uma porta que tenta injetar evidência de OUTRO Run (inclusive violação crítica alheia),
    duplicata e lacuna: o que não é deste Run é descartado e nunca vira `SafetyEvent`."""
    rig = build_scripted(
        h,
        lambda ident: ScriptedRuntime(
            ident,
            make_report(ident, operations=5, denial_count=1),
            evidence=lambda req: [
                denial_for(req, 1, "toctou_recheck_failed", run_id="outro-run-estranho"),  # alheio
                denial_for(req, 1, "path_denied"),
                denial_for(req, 1, "secret_access_blocked"),  # duplicata da sequência 1
            ],
        ),
    )
    result = rig.invoke()
    run = dev_run(rig)
    assert result.integrity_violation is False  # a violação alheia NÃO conta para este Run
    assert result.status is InvocationStatus.FAILED and run.status is RunStatus.ERROR
    events = developer_events(rig, run.id)
    assert [e.kind for e in events] == [SafetyEventKind.PATH_DENIED]  # só o associado, uma vez
    with rig.h.factory() as session:
        foreign = session.scalar(
            select(func.count())
            .select_from(SafetyEvent)
            .where(SafetyEvent.kind == SafetyEventKind.TOCTOU_RECHECK_FAILED)
        )
    assert foreign == 0


@pytest.mark.parametrize(
    "foreign",
    [
        {"run_id": "outro-run"},
        {"task_id": "outra-task"},
        {"invocation_id": "outra-invocacao"},
    ],
)
def test_aud002_g_evidencia_de_outra_task_ou_invocacao_tambem_e_descartada(
    h: Harness, foreign: dict[str, str]
) -> None:
    """A associação é ao ESCOPO inteiro da invocação (run, task e invocation_id), não só ao run."""
    rig = build_scripted(
        h,
        lambda ident: ScriptedRuntime(
            ident,
            make_report(ident, operations=3, denial_count=0),
            evidence=lambda req: [denial_for(req, 1, "toctou_recheck_failed", **foreign)],
        ),
    )
    result = rig.invoke()
    run = dev_run(rig)
    assert result.integrity_violation is False and developer_events(rig, run.id) == []
    assert result.status is InvocationStatus.FAILED and run.status is RunStatus.ERROR  # rejeitado


def test_aud002_e_excecao_do_provider_nao_apaga_violacao_critica_ja_provada(h: Harness) -> None:
    rig = build_scripted(
        h,
        lambda ident: ScriptedRuntime(
            ident,
            raises=RuntimeError("o provider caiu depois da violação"),
            evidence=lambda req: [denial_for(req, 1, "out_of_worktree_write")],
        ),
    )
    result = rig.invoke()
    run = dev_run(rig)
    assert result.integrity_violation and result.code is InvocationCode.CRITICAL_VIOLATION
    assert run.status is RunStatus.BLOCKED
    assert [e.kind for e in developer_events(rig, run.id)] == [
        SafetyEventKind.OUT_OF_WORKTREE_WRITE
    ]
    assert "caiu" not in (run.error_summary or "")


def test_aud002_f_negacao_de_ferramenta_sobrevive_a_excecao_do_provider(h: Harness) -> None:
    rig = build_scripted(
        h,
        lambda ident: ScriptedRuntime(
            ident,
            raises=RuntimeError("falha"),
            evidence=lambda req: [denial_for(req, 1, "path_denied")],
        ),
    )
    result = rig.invoke()
    run = dev_run(rig)
    assert result.code is InvocationCode.PROVIDER_EXCEPTION and not result.integrity_violation
    assert len(developer_events(rig, run.id)) == 1 and result.safety_events == 1


# ------------------------------------------------ fechamento idempotente: nenhum evento em dobro


def _closure(**changes: Any) -> RunClosure:
    base: dict[str, Any] = {
        "status": RunStatus.ERROR,
        "finished_at": datetime.now(UTC),
        "duration_ms": 1,
        "token_source": TokenSource.UNAVAILABLE,
        "input_tokens": None,
        "output_tokens": None,
        "files_read": None,
        "summary": None,
        "error_summary": "developer_invocation:provider_error",
        "denials": (),
        "limit_exceeded": False,
    }
    return RunClosure(**{**base, **changes})


def test_fechar_duas_vezes_nao_duplica_eventos_nem_reescreve_o_run(h: Harness) -> None:
    rig = build_scripted(h)
    spec = _spec(rig)
    with h.factory() as session:
        claim = em.open_developer_run(session, rig.task.id, spec=spec)
    assert claim.run_id is not None
    closure = _closure(
        denials=(
            denial(claim.run_id, 1, task_id=rig.task.id, invocation_id=dev_id(rig)),
            denial(claim.run_id, 2, task_id=rig.task.id, invocation_id=dev_id(rig)),
        ),
        limit_exceeded=True,
    )
    with h.factory() as session:
        first = em.close_developer_run(session, rig.task.id, run_id=claim.run_id, closure=closure)
    with h.factory() as session:
        second = em.close_developer_run(session, rig.task.id, run_id=claim.run_id, closure=closure)
    assert first.safety_events == 3  # duas negações + o orçamento
    assert second.outcome.value == "not_open" and second.safety_events == 0
    assert second.existing_status is RunStatus.ERROR
    assert len(developer_events(rig, claim.run_id)) == 3  # nada em dobro


def test_fechamento_commitado_cuja_resposta_se_perdeu_nao_duplica_a_negacao(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_close = em.close_developer_run
    calls: list[int] = []

    def commit_then_raise(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        out = real_close(*args, **kwargs)
        if len(calls) == 1:
            raise RuntimeError("resposta perdida")
        return out

    rig = build_scripted(
        h,
        lambda ident: ScriptedRuntime(
            ident,
            make_report(ident, operations=2, denial_count=1),
            evidence=lambda req: [denial_for(req, 1, "path_denied")],
        ),
    )
    monkeypatch.setattr(em, "close_developer_run", commit_then_raise)
    result = rig.invoke()
    assert len(calls) == 2 and result.finalization is Finalization.ALREADY_FINAL
    assert len(developer_events(rig, dev_run(rig).id)) == 1  # exatamente um, não dois


# --------------------------------------- revogação é permanente (≠ remoção física do binding)


class _Capturing(HookedBindings):
    def __init__(self, inner: Any) -> None:
        super().__init__(inner)
        self.tokens: list[Any] = []

    def bind_run(self, **kwargs: Any) -> object:
        token = super().bind_run(**kwargs)
        self.tokens.append(token)
        return token


def test_binding_revogado_pela_invocacao_nao_volta_a_ser_registrado(h: Harness) -> None:
    hooked = _Capturing(h.bindings)
    rig = build_scripted(
        h,
        lambda ident: ScriptedRuntime(
            ident, make_report(ident, status=ReportedStatus.ERROR, error_summary="x")
        ),
        bindings=hooked,
    )
    result = rig.invoke()
    run = dev_run(rig)
    assert result.binding.value == "revoked" and run.status is RunStatus.ERROR
    developer_token = hooked.tokens[-1]  # o último registro é o do Run do Developer
    assert developer_token.run_id == run.id
    resolver = h.bindings.resolver
    with pytest.raises(BindingRevoked):  # a CHAVE foi revogada — não é só a entrada que sumiu
        resolver.bind(developer_token)
    with pytest.raises(WorkspaceUnavailable, match="binding_revoked"):
        h.bindings.bind_run(
            workspace_id=developer_token.workspace_id,
            task_id=developer_token.task_id,
            run_id=developer_token.run_id,
            invocation_id=dev_id(rig),
            base_commit=developer_token.base_commit,
            workspace_path=developer_token.workspace_path,
            workspace_prefix=developer_token.workspace_prefix,
            root_identity=developer_token.root_identity,
        )
    not_authorized(rig, run.id)


def test_binding_liberado_no_fim_normal_nao_e_revogado(h: Harness) -> None:
    """A distinção que o teste anterior protege: liberar (ok) ≠ revogar (falha)."""
    hooked = _Capturing(h.bindings)
    rig = build_scripted(h, bindings=hooked)
    assert rig.invoke().binding.value == "released"
    token = hooked.tokens[-1]
    h.bindings.resolver.bind(token)  # não levanta: a chave não foi revogada
    h.bindings.resolver.unbind_exact(token)
