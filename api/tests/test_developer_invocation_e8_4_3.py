"""E8.4.3 — núcleo da invocação do Developer (componente interno, não operacional).

Tudo com Git, SQLite, worktree, binding, admissão e preparação **reais** em diretórios temporários;
o provider é um double scriptado (porta neutra) ou o provider Anthropic **verdadeiro** sobre um
**transporte falso**. Nenhuma rede, credencial ou modelo real (um *autouse* proíbe `connect`).

Mapa de aceite: 1 sucesso; 2 recusa de ferramentas; 3 falha do provider; 4 interrupção
(cancelamento/prazo); 5 estados inconsistentes; 6 no máximo uma chamada por `invocation_id`
(persistente, atômica, entre threads e processos); 7 falha de fechamento/liberação; 8 capability
vinculada ao aprovado; 9 vazamento; 10 limites desta etapa.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.capability_wiring import VerifyingCapabilityProver
from app.config import AppSettings
from app.db.enums import (
    FilesReadSource,
    RunAgent,
    RunPurpose,
    RunStatus,
    SafetyEventKind,
    TaskStatus,
    TokenSource,
)
from app.db.models import DevWorkspace, Run, SafetyEvent, WorkspaceTask
from app.orchestrator import execution_manager as em
from app.orchestrator.developer_invocation_contract import (
    BindingOutcome,
    ClaimOutcome,
    DeveloperInvocationReport,
    DeveloperRunSpec,
    Finalization,
    InvocationCode,
    InvocationStatus,
    ProviderIdentity,
    ReportedStatus,
)
from app.orchestrator.execution_contract import (
    COMPONENT_DEVELOPER,
    DeveloperOutcome,
    component_invocation_id,
)
from app.orchestrator.fingerprint import tool_profile_hash
from app.orchestrator.workspace_contract import PreparationCode
from app.safety.capability_profile import DEVELOPER_V1_PROFILE
from app.tool_executor.contracts import ExecutionWorkspaceRef, RunScope, WorkspaceKind
from app.tool_executor.workspace import WorkspaceUnavailable, bind_workspace
from tests.capability_helpers import ScriptedVerifier, declaration, expected_binding, profile
from tests.conftest import API_ROOT
from tests.developer_invocation_support_e8_4_3 import (
    RUN_TRANSPORT,
    Rig,
    ScriptedRuntime,
    build_real,
    build_scripted,
    denial_for,
    make_report,
)
from tests.test_anthropic_messages_developer_adapter_e8_2 import (
    FakeTransport,
    _forever,
    final,
    tool_use,
)
from tests.test_execution_workspace_e8_4_2 import Harness, HookedBindings, git

pytestmark = pytest.mark.skipif(
    __import__("shutil").which("git") is None, reason="git indisponível no PATH"
)


# ------------------------------------------------------------------------------ harness


_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nenhuma conexão a destino que não seja loopback nesta suíte: o transporte é sempre o falso.

    Loopback fica liberado porque o `socketpair()` do event loop do provider (Windows) conecta em
    `127.0.0.1`; qualquer outro destino — inclusive a API real — falha o teste."""
    real_connect = socket.socket.connect

    def guarded(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOOPBACK:
            raise AssertionError(f"rede proibida nos testes da E8.4.3: {host!r}")
        return real_connect(self, address)

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("rede proibida nos testes da E8.4.3")

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


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


def _task_timeout(rig: Rig) -> int:
    parts = rig.h.task(rig.task.id).approved_fingerprint_parts
    assert parts is not None
    return int(parts["execution_limits"]["task_timeout_s"])


def dev_id(rig: Rig) -> str:
    return component_invocation_id(rig.control_run_id, COMPONENT_DEVELOPER)


def dev_runs(rig: Rig) -> list[Run]:
    with rig.h.factory() as fresh:
        rows = list(fresh.scalars(select(Run).where(Run.invocation_id == dev_id(rig))))
        fresh.expunge_all()
        return rows


def dev_run(rig: Rig) -> Run:
    rows = dev_runs(rig)
    assert len(rows) == 1, rows
    return rows[0]


def events_of(rig: Rig, run_id: str | None = None) -> list[SafetyEvent]:
    with rig.h.factory() as fresh:
        query = select(SafetyEvent).where(SafetyEvent.task_id == rig.task.id)
        if run_id is not None:
            query = query.where(SafetyEvent.run_id == run_id)
        rows = list(fresh.scalars(query.order_by(SafetyEvent.created_at, SafetyEvent.id)))
        fresh.expunge_all()
        return rows


def developer_events(rig: Rig, run_id: str) -> list[SafetyEvent]:
    return events_of(rig, run_id)


def mutate(rig: Rig, model: type[Any], row_id: str, **values: Any) -> None:
    with rig.h.factory() as fresh:
        fresh.execute(update(model).where(model.id == row_id).values(**values))
        fresh.commit()


def untouched_control(rig: Rig) -> None:
    """A invocação não fecha o Run de controle nem transiciona a task."""
    task = rig.h.task(rig.task.id)
    control = rig.h.run(rig.control_run_id)
    assert task.status is TaskStatus.EXECUTING
    assert task.version == rig.context.task_version
    assert task.attempts == rig.context.attempt_index + 1
    assert control.status is RunStatus.RUNNING and control.finished_at is None
    assert control.files_changed is None and control.diff_added is None


def not_authorized(rig: Rig, run_id: str) -> None:
    """O binding do Run do Developer **não** resolve mais."""
    with pytest.raises(WorkspaceUnavailable):
        bind_workspace(
            rig.h.bindings.resolver,
            ExecutionWorkspaceRef(
                kind=WorkspaceKind.LOCAL_WORKTREE,
                id=rig.h.workspace.id,
                base_commit=rig.context.base_commit,
            ),
            RunScope(task_id=rig.task.id, run_id=run_id, invocation_id=dev_id(rig)),
        )


def authorized(rig: Rig, run_id: str) -> None:
    bind_workspace(
        rig.h.bindings.resolver,
        ExecutionWorkspaceRef(
            kind=WorkspaceKind.LOCAL_WORKTREE,
            id=rig.h.workspace.id,
            base_commit=rig.context.base_commit,
        ),
        RunScope(task_id=rig.task.id, run_id=run_id, invocation_id=dev_id(rig)),
    )


# ============================================================== 1 · sucesso (scriptado)


def test_sucesso_persiste_o_run_do_developer_e_nao_toca_o_controle(h: Harness) -> None:
    rig = build_scripted(h)
    result = rig.invoke()

    assert result.status is InvocationStatus.COMPLETED, result
    assert result.code is InvocationCode.COMPLETED
    assert result.run_status is RunStatus.OK and result.finalization is Finalization.CLOSED
    assert result.binding is BindingOutcome.RELEASED and not result.requires_finalization
    assert result.developer_outcome is DeveloperOutcome.COMPLETED and not result.integrity_violation
    assert result.provider_call_attempted and len(rig.runtime.calls) == 1

    run = dev_run(rig)
    assert run.id == result.run_id
    assert run.agent is RunAgent.DEVELOPER and run.purpose is RunPurpose.EXECUTION
    assert run.invocation_id == f"{rig.control_run_id}:developer"
    assert run.status is RunStatus.OK and run.error_summary is None
    assert run.attempt_index == rig.context.attempt_index and run.fix_round == rig.context.fix_round
    assert run.task_id == rig.task.id and run.base_commit == rig.context.base_commit
    assert run.worktree_path == rig.context.worktree_path
    assert run.context_manifest_id == rig.h.run(rig.control_run_id).context_manifest_id
    assert run.provider == "scripted" and run.model == rig.identity.model
    assert run.provider_adapter == rig.identity.adapter and run.transport.value == RUN_TRANSPORT
    assert run.tool_profile_hash == tool_profile_hash()  # comprovado = aprovado
    assert run.input_tokens == 100 and run.output_tokens == 50
    assert run.token_source is TokenSource.REPORTED
    assert run.files_read == ["src/util.py"] and run.files_read_source is FilesReadSource.REPORTED
    assert run.summary == "feito" and run.prompt_sha256 is None
    assert run.finished_at is not None and run.duration_ms is not None and run.duration_ms >= 0
    # o Git Runtime é quem mede o diff: esta etapa não afirma nada (E8.4.4).
    assert run.files_changed is None and run.diff_added is None and run.diff_removed is None
    untouched_control(rig)
    not_authorized(rig, run.id)  # liberado: o fim normal de uso


def test_entrada_da_porta_vem_do_aprovado_e_nao_de_quem_invoca(h: Harness) -> None:
    rig = build_scripted(h)
    rig.invoke()
    [call] = rig.runtime.calls
    task = rig.h.task(rig.task.id)
    parts = task.approved_fingerprint_parts
    assert parts is not None
    assert call.goal == task.goal and call.plan_steps == ()  # sem passos fictícios (D3)
    assert task.plan is not None
    assert call.acceptance_criteria == tuple(task.plan["acceptance_criteria"])
    assert call.max_total_tokens == parts["execution_limits"]["max_total_tokens"]
    assert 1 <= call.timeout_s <= parts["execution_limits"]["run_timeout_s"]
    assert call.base_commit == task.planning_base_commit == rig.context.base_commit
    assert call.invocation_id == dev_id(rig) and call.control_run_id == rig.control_run_id
    assert call.run_id == dev_run(rig).id and call.workspace_id == rig.h.workspace.id
    # o contexto entregue é o artefato aprovado, byte a byte, e o hash é o do manifest.
    import hashlib

    artifact = (rig.h.artifacts / f"{call.rendered_context_hash}.json").read_bytes()
    assert call.rendered_context.encode("utf-8") == artifact
    assert hashlib.sha256(artifact).hexdigest() == call.rendered_context_hash
    assert call.context_manifest_id == dev_run(rig).context_manifest_id
    assert "worktree" not in repr(call) and rig.context.worktree_path not in repr(call)


def test_o_binding_do_developer_vale_durante_a_chamada_e_e_do_run_do_developer(h: Harness) -> None:
    seen: dict[str, Any] = {}

    def hook(request: Any) -> None:
        authorized(rig, request.run_id)  # durante a chamada: resolve, com o run_id do Developer
        with pytest.raises(WorkspaceUnavailable):
            bind_workspace(  # o Run de controle NÃO é o do Developer
                rig.h.bindings.resolver,
                ExecutionWorkspaceRef(
                    kind=WorkspaceKind.LOCAL_WORKTREE,
                    id=rig.h.workspace.id,
                    base_commit=rig.context.base_commit,
                ),
                RunScope(task_id=rig.task.id, run_id="outro", invocation_id=dev_id(rig)),
            )
        seen["during"] = True

    rig = build_scripted(h, lambda ident: ScriptedRuntime(ident, hook=hook))
    result = rig.invoke()
    assert seen == {"during": True} and result.status is InvocationStatus.COMPLETED


# ============================================ 1b · sucesso e recusa de ferramentas (real)


def test_pilha_real_sucesso_le_e_escreve_pela_superficie_mediada(h: Harness) -> None:
    fake = FakeTransport(
        [
            tool_use("ff_read_file", {"path": "src/util.py"}),
            final("lido"),
        ]
    )
    rig = build_real(h, fake)
    result = rig.invoke()
    assert result.status is InvocationStatus.COMPLETED, result
    run = dev_run(rig)
    assert run.status is RunStatus.OK
    assert run.files_read == ["src/util.py"] and run.files_read_source is FilesReadSource.REPORTED
    assert run.token_source is TokenSource.REPORTED and run.input_tokens and run.output_tokens
    assert run.provider == "anthropic" and run.transport.value == "api"
    assert fake.closed and len(fake.requests) == 2  # dois turnos, um transporte, fechado
    # superfície: exatamente as nove ferramentas mediadas, nenhuma nativa/shell.
    for request in fake.requests:
        names = sorted(tool["name"] for tool in request["tools"])
        assert names == sorted(
            [
                "ff_read_file",
                "ff_list_directory",
                "ff_search_text",
                "ff_write_file",
                "ff_apply_patch",
                "ff_git_status",
                "ff_git_diff",
                "ff_git_show",
                "ff_git_list_tree",
            ]
        )
    untouched_control(rig)
    not_authorized(rig, run.id)


def test_pilha_real_escrita_acontece_so_na_worktree_da_task(h: Harness) -> None:
    fake = FakeTransport(
        [
            tool_use("ff_write_file", {"path": "src/novo.py", "content": "X = 1\n"}),
            final("escrito"),
        ]
    )
    rig = build_real(h, fake)
    main_before = git(h.repo, "status", "--porcelain").stdout
    result = rig.invoke()
    assert result.status is InvocationStatus.COMPLETED, result
    assert (Path(rig.context.worktree_path) / "src" / "novo.py").read_text(encoding="utf-8") == (
        "X = 1\n"
    )
    assert git(h.repo, "status", "--porcelain").stdout == main_before  # árvore principal intacta
    assert not (h.repo / "src" / "novo.py").exists()


@pytest.mark.parametrize("target", [".env", "../fora.txt", ".git/config"])
def test_pilha_real_negacao_de_ferramenta_gera_safety_event_e_nao_vira_violacao_global(
    h: Harness, target: str
) -> None:
    fake = FakeTransport([tool_use("ff_read_file", {"path": target}), final("não consegui")])
    rig = build_real(h, fake)
    result = rig.invoke()
    run = dev_run(rig)
    # uma negação que segurou a linha NÃO bloqueia o Run (nem é violação global): [E8.4.1] §11.
    assert result.status is InvocationStatus.COMPLETED, result
    assert run.status is RunStatus.OK and not result.integrity_violation
    events = developer_events(rig, run.id)
    assert len(events) == 1 and result.safety_events == 1
    [event] = events
    assert event.kind in {
        SafetyEventKind.PATH_DENIED,
        SafetyEventKind.SECRET_ACCESS_BLOCKED,
        SafetyEventKind.OUT_OF_WORKTREE_WRITE,
    }
    assert event.run_id == run.id and event.task_id == rig.task.id
    assert event.workspace_id == rig.h.workspace.id
    assert run.files_read == []  # nada foi lido: `[]` medido, não `None`
    assert run.files_read_source is FilesReadSource.REPORTED


def test_pilha_real_ferramenta_fora_da_superficie_e_erro_de_protocolo_sem_executar(
    h: Harness,
) -> None:
    fake = FakeTransport([tool_use("Bash", {"command": "echo pwned > pwned.txt"})])
    rig = build_real(h, fake)
    result = rig.invoke()
    run = dev_run(rig)
    assert result.status is InvocationStatus.FAILED and run.status is RunStatus.ERROR
    assert result.developer_outcome is DeveloperOutcome.PROVIDER_ERROR
    assert not (Path(rig.context.worktree_path) / "pwned.txt").exists()
    assert not (h.repo / "pwned.txt").exists()
    assert run.error_summary is not None and run.error_summary.startswith("developer_invocation:")
    not_authorized(rig, run.id)  # falha ⇒ autorização revogada, não só liberada
    assert result.binding is BindingOutcome.REVOKED


def test_pilha_real_orcamento_esgotado_vira_blocked_com_safety_event(h: Harness) -> None:
    fake = FakeTransport([], counts=[10_000_000])  # a contagem prevista já passa do orçamento
    rig = build_real(h, fake)
    result = rig.invoke()
    run = dev_run(rig)
    assert result.status is InvocationStatus.FAILED
    assert run.status is RunStatus.BLOCKED
    assert result.developer_outcome is DeveloperOutcome.LIMIT_EXCEEDED
    assert result.code is InvocationCode.LIMIT_EXCEEDED and not result.integrity_violation
    kinds = [e.kind for e in developer_events(rig, run.id)]
    assert kinds == [SafetyEventKind.LIMIT_EXCEEDED]
    assert fake.calls == ["count"]  # nenhum `create`: o provider nem chegou a pedir inferência
    assert run.error_summary is not None and "limit_exceeded" in run.error_summary


def test_pilha_real_prazo_curto_vira_timeout_e_encerra_o_cliente(h: Harness) -> None:
    fake = FakeTransport([_forever])
    rig = build_real(h, fake)
    control = h.run(rig.control_run_id)
    assert control.started_at is not None
    started = (
        control.started_at if control.started_at.tzinfo else control.started_at.replace(tzinfo=UTC)
    )
    # restam ~1,5 s da tentativa: o provider recebe `timeout_s = 1`.
    h.clock.fixed = started + timedelta(seconds=_task_timeout(rig) - 1.5)
    begin = time.monotonic()
    result = rig.invoke()
    assert time.monotonic() - begin < 20
    run = dev_run(rig)
    assert result.status is InvocationStatus.FAILED, result
    assert run.status is RunStatus.TIMEOUT and result.developer_outcome is DeveloperOutcome.TIMEOUT
    assert fake.closed


# ================================================================== 3 · falha do provider


@pytest.mark.parametrize(
    ("reported", "limit", "run_status", "outcome", "code"),
    [
        (
            ReportedStatus.ERROR,
            False,
            RunStatus.ERROR,
            DeveloperOutcome.PROVIDER_ERROR,
            InvocationCode.PROVIDER_ERROR,
        ),
        (
            ReportedStatus.TIMEOUT,
            False,
            RunStatus.TIMEOUT,
            DeveloperOutcome.TIMEOUT,
            InvocationCode.PROVIDER_TIMEOUT,
        ),
        (
            ReportedStatus.CANCELLED,
            False,
            RunStatus.CANCELLED,
            DeveloperOutcome.CANCELLED,
            InvocationCode.PROVIDER_CANCELLED,
        ),
        (
            ReportedStatus.BLOCKED,
            True,
            RunStatus.BLOCKED,
            DeveloperOutcome.LIMIT_EXCEEDED,
            InvocationCode.LIMIT_EXCEEDED,
        ),
        (
            ReportedStatus.BLOCKED,
            False,
            RunStatus.BLOCKED,
            DeveloperOutcome.POLICY_BLOCKED,
            InvocationCode.PROVIDER_ERROR,
        ),
    ],
)
def test_status_do_provider_vira_status_do_run(
    h: Harness,
    reported: ReportedStatus,
    limit: bool,
    run_status: RunStatus,
    outcome: DeveloperOutcome,
    code: InvocationCode,
) -> None:
    rig = build_scripted(
        h,
        lambda ident: ScriptedRuntime(
            ident,
            make_report(
                ident, status=reported, limit_exceeded=limit, error_summary="falha do provider"
            ),
        ),
    )
    result = rig.invoke()
    run = dev_run(rig)
    assert result.status is InvocationStatus.FAILED and result.code is code
    assert run.status is run_status and result.run_status is run_status
    assert result.developer_outcome is outcome and not result.integrity_violation
    assert run.error_summary == f"developer_invocation:{code.value}: falha do provider"
    assert result.finalization is Finalization.CLOSED and not result.requires_finalization
    assert result.binding is BindingOutcome.REVOKED
    not_authorized(rig, run.id)
    limit_events = [
        e for e in developer_events(rig, run.id) if e.kind is SafetyEventKind.LIMIT_EXCEEDED
    ]
    assert len(limit_events) == (1 if limit else 0)
    untouched_control(rig)
    assert len(rig.runtime.calls) == 1  # falhou: NÃO há retry


def test_excecao_do_provider_nao_vaza_texto_e_fecha_o_run(h: Harness) -> None:
    boom = RuntimeError("falha com token ghp_0123456789abcdefghijklmnopqrstuvwxyz e /home/segredo")
    rig = build_scripted(h, lambda ident: ScriptedRuntime(ident, raises=boom))
    result = rig.invoke()
    run = dev_run(rig)
    assert (
        result.status is InvocationStatus.FAILED
        and result.code is InvocationCode.PROVIDER_EXCEPTION
    )
    assert run.status is RunStatus.ERROR and result.provider_call_attempted
    assert run.error_summary == "developer_invocation:provider_exception"
    blob = json.dumps(
        [run.error_summary, run.summary, [e.detail for e in events_of(rig)], repr(result)]
    )
    assert "ghp_" not in blob and "segredo" not in blob and "RuntimeError" not in blob
    assert len(rig.runtime.calls) == 1 and result.binding is BindingOutcome.REVOKED
    not_authorized(rig, run.id)


def test_executor_indisponivel_fecha_o_run_sem_chamar_o_provider(h: Harness) -> None:
    fake = FakeTransport([final("nunca")])
    rig = build_real(h, fake)

    class _BrokenFactory:
        def create(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("não foi possível criar o executor")

    rig.runtime._factory = _BrokenFactory()  # a factory do composition root falha ao criar
    result = rig.invoke()
    run = dev_run(rig)
    assert result.status is InvocationStatus.FAILED and run.status is RunStatus.ERROR
    assert fake.calls == [] and not fake.requests  # o provider nunca foi chamado
    assert not result.report.provider_called
    assert run.files_read is None and run.files_read_source is FilesReadSource.UNAVAILABLE


def test_resumo_e_diagnostico_passam_pelo_redator_no_ponto_de_escrita(h: Harness) -> None:
    secret = "ghp_0123456789abcdefghijklmnopqrstuvwxyz"
    rig = build_scripted(
        h,
        lambda ident: ScriptedRuntime(ident, make_report(ident, summary=f"token {secret} usado")),
    )
    rig.invoke()
    assert secret not in (dev_run(rig).summary or "")


# ====================================================================== 4 · interrupção


def test_cancelado_antes_de_comecar_nao_cria_run_nem_chama_o_provider(h: Harness) -> None:
    rig = build_scripted(h)
    rig.signal.flag = True
    result = rig.invoke()
    assert result.status is InvocationStatus.REFUSED
    assert result.code is InvocationCode.CANCELLED_BEFORE_START
    assert dev_runs(rig) == [] and rig.runtime.calls == []
    untouched_control(rig)


def test_cancelamento_persistido_e_visto_na_releitura(h: Harness) -> None:
    rig = build_scripted(h)
    mutate(rig, WorkspaceTask, rig.task.id, cancel_requested=True)
    result = rig.invoke()
    assert result.status is InvocationStatus.REFUSED
    assert result.fact_code is PreparationCode.CANCEL_REQUESTED
    assert dev_runs(rig) == [] and rig.runtime.calls == []


def test_sinal_que_levanta_conta_como_cancelado(h: Harness) -> None:
    rig = build_scripted(h)

    def broken() -> bool:
        raise RuntimeError

    result = rig.service.invoke(rig.context, is_cancelled=broken)
    assert result.code is InvocationCode.CANCELLED_BEFORE_START
    assert dev_runs(rig) == [] and rig.runtime.calls == []


def test_cancelamento_durante_a_chamada_fecha_cancelled_e_revoga(h: Harness) -> None:
    def hook(request: Any) -> None:
        rig.signal.flag = True  # chega no meio da chamada

    rig = build_scripted(
        h,
        lambda ident: ScriptedRuntime(
            ident, make_report(ident, status=ReportedStatus.CANCELLED), hook=hook
        ),
    )
    result = rig.invoke()
    run = dev_run(rig)
    assert result.status is InvocationStatus.FAILED and run.status is RunStatus.CANCELLED
    assert result.developer_outcome is DeveloperOutcome.CANCELLED
    assert result.binding is BindingOutcome.REVOKED
    not_authorized(rig, run.id)


def test_cancelamento_no_ultimo_instante_antes_do_provider_nao_chama_o_provider(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build_scripted(h)
    real_bind = rig.workspaces.bind_component_run

    def bind_then_cancel(*args: Any, **kwargs: Any) -> object:
        token = real_bind(*args, **kwargs)
        rig.signal.flag = True  # depois do registro, antes da chamada
        return token

    monkeypatch.setattr(rig.workspaces, "bind_component_run", bind_then_cancel)
    result = rig.invoke()
    run = dev_run(rig)
    assert rig.runtime.calls == [] and not result.provider_call_attempted
    assert run.status is RunStatus.CANCELLED and result.status is InvocationStatus.FAILED
    assert result.binding is BindingOutcome.REVOKED
    not_authorized(rig, run.id)


def test_prazo_esgotado_antes_nao_cria_run(h: Harness) -> None:
    rig = build_scripted(h)
    control = h.run(rig.control_run_id)
    assert control.started_at is not None
    started = (
        control.started_at if control.started_at.tzinfo else control.started_at.replace(tzinfo=UTC)
    )
    h.clock.fixed = started + timedelta(seconds=_task_timeout(rig))  # task_timeout_s esgotado
    result = rig.invoke()
    assert result.status is InvocationStatus.REFUSED
    assert result.code is InvocationCode.DEADLINE_EXHAUSTED
    assert dev_runs(rig) == [] and rig.runtime.calls == []


def test_prazo_que_acaba_entre_a_reivindicacao_e_a_chamada_fecha_timeout(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build_scripted(h)
    control = h.run(rig.control_run_id)
    assert control.started_at is not None
    started = (
        control.started_at if control.started_at.tzinfo else control.started_at.replace(tzinfo=UTC)
    )
    real_open = em.open_developer_run

    def open_then_expire(*args: Any, **kwargs: Any) -> Any:
        claim = real_open(*args, **kwargs)
        h.clock.fixed = started + timedelta(seconds=_task_timeout(rig) + 1)  # o prazo cruza o claim
        return claim

    monkeypatch.setattr(em, "open_developer_run", open_then_expire)
    result = rig.invoke()
    run = dev_run(rig)
    assert rig.runtime.calls == [] and run.status is RunStatus.TIMEOUT
    assert result.code is InvocationCode.DEADLINE_EXHAUSTED
    assert result.developer_outcome is DeveloperOutcome.TIMEOUT
    not_authorized(rig, run.id)


def test_prazo_que_acaba_depois_do_binding_e_pego_na_ultima_fronteira(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O prazo cruza o registro do binding: o binding já passou, só a última fronteira vê."""
    rig = build_scripted(h)
    control = h.run(rig.control_run_id)
    assert control.started_at is not None
    started = (
        control.started_at if control.started_at.tzinfo else control.started_at.replace(tzinfo=UTC)
    )
    real_bind = rig.workspaces.bind_component_run

    def bind_then_expire(*args: Any, **kwargs: Any) -> object:
        token = real_bind(*args, **kwargs)
        h.clock.fixed = started + timedelta(seconds=_task_timeout(rig) + 1)
        return token

    monkeypatch.setattr(rig.workspaces, "bind_component_run", bind_then_expire)
    result = rig.invoke()
    run = dev_run(rig)
    assert rig.runtime.calls == [] and not result.provider_call_attempted
    assert run.status is RunStatus.TIMEOUT and result.code is InvocationCode.DEADLINE_EXHAUSTED
    assert result.binding is BindingOutcome.REVOKED
    not_authorized(rig, run.id)


def test_keyboard_interrupt_fecha_interrupted_revoga_e_propaga(h: Harness) -> None:
    rig = build_scripted(h, lambda ident: ScriptedRuntime(ident, raises=KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        rig.invoke()
    run = dev_run(rig)
    assert run.status is RunStatus.INTERRUPTED and run.finished_at is not None
    assert run.error_summary == "developer_invocation:interrupted"
    not_authorized(rig, run.id)
    assert len(rig.runtime.calls) == 1


# ===================================================================== 5 · estados inconsistentes


def _task_not_executing(rig: Rig) -> None:
    mutate(rig, WorkspaceTask, rig.task.id, status=TaskStatus.APPROVED, phase=None)


def _control_closed(rig: Rig) -> None:
    mutate(
        rig,
        Run,
        rig.control_run_id,
        status=RunStatus.ERROR,
        finished_at=datetime.now(UTC),
        duration_ms=1,
    )


def _attempts_bumped(rig: Rig) -> None:
    mutate(rig, WorkspaceTask, rig.task.id, attempts=rig.task.attempts + 5)


def _workspace_archived(rig: Rig) -> None:
    from app.db.enums import WorkspaceStatus

    mutate(rig, DevWorkspace, rig.h.workspace.id, status=WorkspaceStatus.ARCHIVED)


def _plan_hash_changed(rig: Rig) -> None:
    mutate(rig, WorkspaceTask, rig.task.id, plan_hash="0" * 64)


@pytest.mark.parametrize(
    ("break_state", "fact_code"),
    [
        (_task_not_executing, PreparationCode.TASK_NOT_EXECUTING),
        (_control_closed, PreparationCode.RUN_NOT_OPEN),
        (_attempts_bumped, PreparationCode.ATTEMPT_MISMATCH),
        (_workspace_archived, PreparationCode.WORKSPACE_INACTIVE),
        (_plan_hash_changed, PreparationCode.PLAN_INCOHERENT),
    ],
)
def test_estado_do_banco_incoerente_recusa_sem_run_nem_chamada(
    h: Harness, break_state: Any, fact_code: PreparationCode
) -> None:
    rig = build_scripted(h)
    break_state(rig)
    result = rig.invoke()
    assert result.status is InvocationStatus.REFUSED and result.fact_code is fact_code
    assert result.code is InvocationCode.REFUSED_FACTS
    assert dev_runs(rig) == [] and rig.runtime.calls == [] and not result.provider_call_attempted


def test_fingerprint_divergente_recusa(h: Harness, session: Session) -> None:
    rig = build_scripted(h)
    from app.workspace import set_test_config
    from tests.admission_support_e8_4_1 import VALID_TEST_CONFIG

    with h.factory() as fresh:
        set_test_config(fresh, h.workspace.id, {**VALID_TEST_CONFIG, "timeout_seconds": 5})
        fresh.commit()
    result = rig.invoke()
    assert result.fact_code is PreparationCode.FINGERPRINT_DIVERGED
    assert dev_runs(rig) == [] and rig.runtime.calls == []


def test_contexto_preparado_de_outra_tentativa_ou_com_caminho_trocado_recusa(h: Harness) -> None:
    rig = build_scripted(h)
    for broken in (
        replace(rig.context, task_version=rig.context.task_version + 1),
        replace(rig.context, worktree_path=str(Path(rig.context.worktree_path).parent / "outra")),
        replace(rig.context, base_commit="1" * 40),
        replace(rig.context, attempt_index=rig.context.attempt_index + 1),
        replace(rig.context, workspace_id="outro-workspace"),
    ):
        result = rig.service.invoke(broken, is_cancelled=rig.signal)
        assert result.status is InvocationStatus.REFUSED, broken
        assert result.code is InvocationCode.REFUSED_FACTS
    assert dev_runs(rig) == [] and rig.runtime.calls == []
    assert (
        rig.service.invoke(object(), is_cancelled=rig.signal).code is InvocationCode.INTERNAL_ERROR
    )


_BREAKERS: dict[str, Any] = {
    "cancel_requested": lambda rig: mutate(rig, WorkspaceTask, rig.task.id, cancel_requested=True),
    "versao_da_task": lambda rig: mutate(
        rig, WorkspaceTask, rig.task.id, version=rig.context.task_version + 1
    ),
    "run_de_controle_fechado": lambda rig: _control_closed(rig),
    "task_fora_de_executing": lambda rig: _task_not_executing(rig),
    "worktree_publicada_trocada": lambda rig: mutate(
        rig, Run, rig.control_run_id, worktree_path=rig.context.worktree_path + "-outra"
    ),
}


@pytest.mark.parametrize(
    ("name", "expected_code"),
    [
        ("cancel_requested", PreparationCode.CANCEL_REQUESTED),
        ("versao_da_task", PreparationCode.TASK_VERSION_CHANGED),
        ("run_de_controle_fechado", PreparationCode.RUN_NOT_OPEN),
        ("task_fora_de_executing", PreparationCode.TASK_NOT_EXECUTING),
        ("worktree_publicada_trocada", PreparationCode.FINAL_CHECK_FAILED),
    ],
)
def test_estado_que_muda_entre_a_leitura_e_a_reivindicacao_e_pego_sob_o_lock(
    h: Harness, monkeypatch: pytest.MonkeyPatch, name: str, expected_code: PreparationCode
) -> None:
    """O `INSERT` do claim é a 1ª instrução; a reconferência sob o lock desfaz o `INSERT`."""
    rig = build_scripted(h)
    real_facts = em.read_invocation_facts

    def facts_then_break(*args: Any, **kwargs: Any) -> Any:
        facts = real_facts(*args, **kwargs)
        _BREAKERS[name](rig)  # outro escritor vence DEPOIS da leitura e ANTES da reivindicação
        return facts

    monkeypatch.setattr(em, "read_invocation_facts", facts_then_break)
    result = rig.invoke()
    assert result.status is InvocationStatus.REFUSED, result
    assert result.code is InvocationCode.CLAIM_REFUSED and result.fact_code is expected_code
    assert dev_runs(rig) == [] and rig.runtime.calls == []  # o INSERT foi desfeito


def test_artefato_renderizado_adulterado_recusa(h: Harness) -> None:
    rig = build_scripted(h)
    [artifact] = [p for p in h.artifacts.iterdir() if p.suffix == ".json"]
    artifact.write_bytes(artifact.read_bytes() + b" ")
    result = rig.invoke()
    assert result.code is InvocationCode.CONTEXT_ARTIFACT_INVALID
    assert dev_runs(rig) == [] and rig.runtime.calls == []


def test_artefato_ausente_ou_nao_utf8_recusa(h: Harness) -> None:
    rig = build_scripted(h)
    [artifact] = [p for p in h.artifacts.iterdir() if p.suffix == ".json"]
    saved = artifact.read_bytes()
    artifact.unlink()
    assert rig.invoke().code is InvocationCode.CONTEXT_ARTIFACT_INVALID
    artifact.write_bytes(saved)
    assert rig.invoke().status is InvocationStatus.COMPLETED


def test_binding_recusado_fecha_o_run_com_erro_e_nao_chama_o_provider(h: Harness) -> None:
    class _FailsOnSecondBind(HookedBindings):
        """O 1º registro (Run de controle, na preparação) passa; o do Developer é recusado."""

        def __init__(self, inner: Any) -> None:
            super().__init__(inner)
            self.count = 0

        def bind_run(self, **kwargs: Any) -> object:
            self.count += 1
            if self.count >= 2:
                raise WorkspaceUnavailable("binding_rejected_by_test")
            return super().bind_run(**kwargs)

    hooked = _FailsOnSecondBind(h.bindings)
    rig = build_scripted(h, bindings=hooked)
    result = rig.invoke()
    run = dev_run(rig)
    assert rig.runtime.calls == [] and not result.provider_call_attempted
    assert (
        result.status is InvocationStatus.FAILED and result.code is InvocationCode.BINDING_REFUSED
    )
    assert run.status is RunStatus.ERROR and result.binding is BindingOutcome.REFUSED
    assert run.error_summary == "developer_invocation:binding_refused"
    not_authorized(rig, run.id)


def test_binding_revogado_durante_a_chamada_impede_o_sucesso(h: Harness) -> None:
    class _Capturing(HookedBindings):
        def __init__(self, inner: Any) -> None:
            super().__init__(inner)
            self.tokens: list[object] = []

        def bind_run(self, **kwargs: Any) -> object:
            token = super().bind_run(**kwargs)
            self.tokens.append(token)
            return token

    hooked = _Capturing(h.bindings)

    def hook(request: Any) -> None:
        assert hooked.inner.revoke_run(hooked.tokens[-1]) is True  # revogado no meio do uso

    rig = build_scripted(
        h, lambda ident: ScriptedRuntime(ident, make_report(ident), hook=hook), bindings=hooked
    )
    result = rig.invoke()
    run = dev_run(rig)
    assert result.status is InvocationStatus.FAILED, result
    assert result.code is InvocationCode.BINDING_REVOKED and result.integrity_violation
    assert (
        run.status is RunStatus.BLOCKED
        and result.developer_outcome is DeveloperOutcome.POLICY_BLOCKED
    )
    assert result.run_status is RunStatus.BLOCKED
    not_authorized(rig, run.id)


@pytest.mark.parametrize("category", ["out_of_worktree_write", "toctou_recheck_failed"])
def test_violacao_critica_impede_o_sucesso_mesmo_com_provider_ok(h: Harness, category: str) -> None:
    rig = build_scripted(
        h,
        lambda ident: ScriptedRuntime(
            ident,
            make_report(ident, operations=3, denial_count=1),
            evidence=lambda req: [denial_for(req, 1, category)],
        ),
    )
    result = rig.invoke()
    run = dev_run(rig)
    assert result.status is InvocationStatus.FAILED and result.integrity_violation
    assert result.code is InvocationCode.CRITICAL_VIOLATION
    assert (
        run.status is RunStatus.BLOCKED
        and result.developer_outcome is DeveloperOutcome.POLICY_BLOCKED
    )
    [event] = developer_events(rig, run.id)
    assert event.kind is SafetyEventKind(category) and event.run_id == run.id
    assert result.binding is BindingOutcome.REVOKED
    not_authorized(rig, run.id)


@pytest.mark.parametrize(
    "category",
    ["path_denied", "secret_access_blocked", "capability_denied", "limit_exceeded"],
)
def test_negacao_nao_critica_e_safety_event_e_o_run_segue_o_provider(
    h: Harness, category: str
) -> None:
    rig = build_scripted(
        h,
        lambda ident: ScriptedRuntime(
            ident,
            make_report(ident, operations=2, denial_count=2),
            evidence=lambda req: [denial_for(req, 1, category), denial_for(req, 2, category)],
        ),
    )
    result = rig.invoke()
    run = dev_run(rig)
    assert result.status is InvocationStatus.COMPLETED and run.status is RunStatus.OK
    assert [e.kind.value for e in developer_events(rig, run.id)] == [category, category]
    assert result.safety_events == 2


# Metadados do PROVIDER contraditórios: sem sucesso, sem confiar em tokens/resumo — mas a
# evidência do EXECUTOR (aqui, uma negação e `files_read`) continua registrada (E843-AUD-002).
_METADATA_CONTRADICTIONS: dict[str, Callable[[ProviderIdentity], DeveloperInvocationReport]] = {
    "limit sem blocked": lambda i: make_report(
        i, limit_exceeded=True, operations=3, denial_count=1
    ),
    "ok sem chamada ao provider": lambda i: make_report(
        i, provider_called=False, operations=3, denial_count=1
    ),
    "outro modelo respondeu": lambda i: make_report(
        replace(i, model="outro-modelo"), operations=3, denial_count=1
    ),
    "outro adaptador respondeu": lambda i: make_report(
        replace(i, adapter="outro-adapter"), operations=3, denial_count=1
    ),
    "outro transporte": lambda i: make_report(
        replace(i, transport="cli"), operations=3, denial_count=1
    ),
}


@pytest.mark.parametrize("name", list(_METADATA_CONTRADICTIONS))
def test_metadado_contraditorio_impede_sucesso_sem_apagar_a_evidencia_do_executor(
    h: Harness, name: str
) -> None:
    builder = _METADATA_CONTRADICTIONS[name]
    rig = build_scripted(
        h,
        lambda ident: ScriptedRuntime(
            ident,
            lambda req: builder(ident),
            evidence=lambda req: [denial_for(req, 1, "path_denied")],
        ),
    )
    result = rig.invoke()
    run = dev_run(rig)
    assert result.status is InvocationStatus.FAILED, name
    assert result.code is InvocationCode.REPORT_CONTRADICTORY
    assert (
        run.status is RunStatus.ERROR
        and result.developer_outcome is DeveloperOutcome.INTERNAL_ERROR
    )
    # o que é do PROVIDER não é confiável…
    assert run.input_tokens is None and run.output_tokens is None
    assert run.token_source is TokenSource.UNAVAILABLE and run.summary is None
    # …o que é do EXECUTOR continua valendo.
    assert run.files_read == ["src/util.py"] and run.files_read_source is FilesReadSource.REPORTED
    [event] = developer_events(rig, run.id)
    assert event.kind is SafetyEventKind.PATH_DENIED and event.run_id == run.id
    assert result.safety_events == 1 and not result.integrity_violation
    not_authorized(rig, run.id)


# Evidência do EXECUTOR incoerente: sem sucesso; só o que o coletor aceitou vira `SafetyEvent`.
# (relatório, evidência fornecida pelo "executor", eventos esperados no banco)
_EVIDENCE_CASES: dict[str, Any] = {
    "negacao sem trilha": ({"operations": 2, "denial_count": 2}, lambda req: [], 0),
    "trilha com lacuna": (
        {"operations": 2, "denial_count": 1},
        lambda req: [denial_for(req, 2)],
        1,
    ),
    "negacao de outro run": (
        {"operations": 2, "denial_count": 1},
        lambda req: [denial_for(req, 1, run_id="outro-run")],
        0,
    ),
    "duplicata nao duplica evento": (
        {"operations": 2, "denial_count": 1},
        lambda req: [denial_for(req, 1), denial_for(req, 1)],
        1,
    ),
    "mais negacoes que operacoes": (
        {"operations": 0, "denial_count": 1},
        lambda req: [denial_for(req, 1)],
        1,
    ),
    "ok sem files_read": ({"files_read": None}, lambda req: [], 0),
}


@pytest.mark.parametrize("name", list(_EVIDENCE_CASES))
def test_evidencia_incoerente_impede_sucesso_e_so_o_aceito_vira_evento(
    h: Harness, name: str
) -> None:
    changes, evidence, expected_events = _EVIDENCE_CASES[name]
    rig = build_scripted(
        h,
        lambda ident: ScriptedRuntime(ident, make_report(ident, **changes), evidence=evidence),
    )
    result = rig.invoke()
    run = dev_run(rig)
    assert result.status is InvocationStatus.FAILED, name
    assert result.code is InvocationCode.REPORT_CONTRADICTORY and run.status is RunStatus.ERROR
    events = developer_events(rig, run.id)
    assert len(events) == expected_events == result.safety_events, name
    assert all(e.run_id == run.id for e in events)  # nada de outro Run
    assert run.input_tokens == 100  # o metadado do provider estava coerente: é persistido
    not_authorized(rig, run.id)


def test_ok_com_executor_nao_encerrado_nao_e_sucesso(h: Harness) -> None:
    rig = build_scripted(
        h, lambda ident: ScriptedRuntime(ident, make_report(ident, executor_closed=False))
    )
    result = rig.invoke()
    assert result.status is InvocationStatus.FAILED
    assert result.code is InvocationCode.EXECUTOR_NOT_CLOSED
    assert dev_run(rig).status is RunStatus.ERROR


# =================================================== 6 · no máximo uma chamada por invocation_id


def test_segunda_invocacao_da_mesma_tentativa_nao_chama_o_provider(h: Harness) -> None:
    rig = build_scripted(h)
    first = rig.invoke()
    second = rig.invoke()
    assert first.status is InvocationStatus.COMPLETED
    assert second.status is InvocationStatus.ALREADY_CLAIMED
    assert second.run_id == first.run_id and second.run_status is RunStatus.OK
    assert not second.requires_finalization and not second.provider_call_attempted
    assert len(rig.runtime.calls) == 1 and len(dev_runs(rig)) == 1


def test_repeticao_apos_falha_tambem_nao_reexecuta_sem_retry_implicito(h: Harness) -> None:
    rig = build_scripted(
        h, lambda ident: ScriptedRuntime(ident, make_report(ident, status=ReportedStatus.ERROR))
    )
    first = rig.invoke()
    again = rig.invoke()
    assert first.status is InvocationStatus.FAILED
    assert again.status is InvocationStatus.ALREADY_CLAIMED and again.run_status is RunStatus.ERROR
    assert len(rig.runtime.calls) == 1


def test_run_que_ficou_running_apos_crash_nao_e_reexecutado(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build_scripted(h)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("o processo morreu antes de fechar")

    monkeypatch.setattr(em, "close_developer_run", boom)
    first = rig.invoke()
    assert first.status is InvocationStatus.UNFINALIZED and first.requires_finalization
    assert dev_run(rig).status is RunStatus.RUNNING
    monkeypatch.undo()
    # um "novo processo" vê o Run `running`: não reexecuta, e diz que precisa de recuperação.
    again = rig.with_service().invoke(rig.context, is_cancelled=rig.signal)
    assert again.status is InvocationStatus.ALREADY_CLAIMED
    assert again.run_status is RunStatus.RUNNING and again.requires_finalization
    assert len(rig.runtime.calls) == 1


def test_claim_duas_vezes_e_idempotente_no_banco(h: Harness) -> None:
    rig = build_scripted(h)
    control = h.run(rig.control_run_id)
    spec = DeveloperRunSpec(
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
    with h.factory() as one:
        a = em.open_developer_run(one, rig.task.id, spec=spec)
    with h.factory() as two:
        b = em.open_developer_run(two, rig.task.id, spec=spec)
    assert a.outcome is ClaimOutcome.CLAIMED and b.outcome is ClaimOutcome.ALREADY
    assert a.run_id == b.run_id and b.existing_status is RunStatus.RUNNING
    assert len(dev_runs(rig)) == 1


def test_claims_concorrentes_entre_threads_tem_um_unico_vencedor(h: Harness) -> None:
    rig = build_scripted(
        h,
        lambda ident: ScriptedRuntime(ident, hook=lambda req: time.sleep(0.15)),
    )
    results: list[Any] = []
    gate = threading.Barrier(4)

    def worker() -> None:
        service = rig.with_service()
        gate.wait()
        results.append(service.invoke(rig.context, is_cancelled=rig.signal))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert len(results) == 4
    completed = [r for r in results if r.status is InvocationStatus.COMPLETED]
    assert len(completed) == 1, [r.status for r in results]
    assert all(
        r.status
        in {InvocationStatus.ALREADY_CLAIMED, InvocationStatus.REFUSED, InvocationStatus.COMPLETED}
        for r in results
    )
    assert len(rig.runtime.calls) == 1 and len(dev_runs(rig)) == 1


def test_claims_concorrentes_entre_processos_tem_um_unico_vencedor(
    h: Harness, tmp_path: Path
) -> None:
    rig = build_scripted(h)
    control = h.run(rig.control_run_id)
    go = tmp_path / "go"
    workers = []
    for index in range(4):
        ready = tmp_path / f"ready-{index}"
        spec = {
            "data_dir": str(h.settings.data_dir),
            "database_url": h.settings.database_url,
            "task_id": rig.task.id,
            "control_run_id": rig.control_run_id,
            "manifest_id": control.context_manifest_id,
            "invocation_id": dev_id(rig),
            "base_commit": rig.context.base_commit,
            "worktree_path": rig.context.worktree_path,
            "task_version": rig.context.task_version,
            "attempt_index": rig.context.attempt_index,
            "fix_round": rig.context.fix_round,
            "identity": [
                rig.identity.provider,
                rig.identity.adapter,
                rig.identity.adapter_version,
                rig.identity.model,
                rig.identity.transport,
            ],
            "ready": str(ready),
            "go": str(go),
        }
        process = subprocess.Popen(  # noqa: S603 — worker de teste, argv literal, sem shell
            [sys.executable, "-m", "tests.claim_worker_e8_4_3", json.dumps(spec)],
            cwd=API_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        workers.append((process, ready))
    deadline = time.monotonic() + 120
    while not all(ready.exists() for _, ready in workers):
        assert time.monotonic() < deadline, "workers não ficaram prontos"
        for process, _ in workers:
            assert process.poll() is None, process.communicate()
        time.sleep(0.05)
    go.write_text("1", encoding="utf-8")

    results: list[dict[str, Any]] = []
    for process, _ in workers:
        out, err = process.communicate(timeout=180)
        assert process.returncode == 0, err
        line = [item for item in out.splitlines() if item.startswith("RESULT ")][-1]
        results.append(json.loads(line.removeprefix("RESULT ")))
    claimed = [r for r in results if r.get("outcome") == "claimed"]
    assert len(claimed) == 1, results
    others = [r for r in results if r.get("outcome") != "claimed"]
    assert all(
        r.get("outcome") == "already" or r.get("error") == "ConcurrentTaskUpdate" for r in others
    ), results
    [only] = dev_runs(rig)
    assert only.id == claimed[0]["run_id"] and only.status is RunStatus.RUNNING


def test_o_claim_e_commitado_antes_da_chamada_ao_provider(h: Harness) -> None:
    seen: dict[str, Any] = {}

    def hook(request: Any) -> None:
        with h.factory() as other:  # outra conexão, durante a chamada
            row = other.execute(
                select(Run.status, Run.finished_at).where(Run.invocation_id == dev_id(rig))
            ).one()
            seen["status"], seen["finished_at"] = row

    rig = build_scripted(h, lambda ident: ScriptedRuntime(ident, hook=hook))
    rig.invoke()
    assert seen["status"] is RunStatus.RUNNING and seen["finished_at"] is None


def test_falha_ao_reivindicar_nao_chama_o_provider(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build_scripted(h)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("disco cheio")

    monkeypatch.setattr(em, "open_developer_run", boom)
    result = rig.invoke()
    assert result.status is InvocationStatus.REFUSED and result.code is InvocationCode.CLAIM_FAILED
    assert rig.runtime.calls == [] and dev_runs(rig) == []


def test_claim_que_commitou_e_levantou_depois_e_tratado_como_ja_reivindicado(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build_scripted(h)
    real_open = em.open_developer_run

    def commit_then_raise(*args: Any, **kwargs: Any) -> Any:
        real_open(*args, **kwargs)
        raise RuntimeError("a resposta se perdeu depois do COMMIT")

    monkeypatch.setattr(em, "open_developer_run", commit_then_raise)
    result = rig.invoke()
    assert result.status is InvocationStatus.ALREADY_CLAIMED and result.requires_finalization
    assert rig.runtime.calls == [] and dev_run(rig).status is RunStatus.RUNNING


# ==================================================== 7 · falha de fechamento e de liberação


def test_falha_de_fechamento_e_explicita_preserva_a_causa_e_nao_repete(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[int] = []

    def boom(*args: Any, **kwargs: Any) -> Any:
        attempts.append(1)
        raise RuntimeError("banco indisponível")

    rig = build_scripted(
        h, lambda ident: ScriptedRuntime(ident, make_report(ident, status=ReportedStatus.ERROR))
    )
    monkeypatch.setattr(em, "close_developer_run", boom)
    result = rig.invoke()
    assert result.status is InvocationStatus.UNFINALIZED and result.requires_finalization
    assert result.finalization is Finalization.FAILED
    assert result.code is InvocationCode.PROVIDER_ERROR  # a causa original NÃO é mascarada
    assert result.developer_outcome is DeveloperOutcome.PROVIDER_ERROR
    assert len(attempts) == 2  # o fechamento (idempotente) é tentado de novo; o provider, nunca
    assert len(rig.runtime.calls) == 1
    assert dev_run(rig).status is RunStatus.RUNNING
    assert result.binding is BindingOutcome.REVOKED  # o binding foi tratado mesmo assim
    not_authorized(rig, dev_run(rig).id)


def test_fechamento_que_falha_uma_vez_e_refeito_sem_duplicar_eventos(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_close = em.close_developer_run
    calls: list[int] = []

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("falha transitória")
        return real_close(*args, **kwargs)

    rig = build_scripted(
        h,
        lambda ident: ScriptedRuntime(
            ident,
            make_report(ident, operations=2, denial_count=1),
            evidence=lambda req: [denial_for(req, 1)],
        ),
    )
    monkeypatch.setattr(em, "close_developer_run", flaky)
    result = rig.invoke()
    assert (
        result.status is InvocationStatus.COMPLETED and result.finalization is Finalization.CLOSED
    )
    assert len(calls) == 2 and len(developer_events(rig, dev_run(rig).id)) == 1


def test_fechamento_commitado_cuja_resposta_se_perdeu_e_reconhecido_como_ja_final(
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

    rig = build_scripted(h)
    monkeypatch.setattr(em, "close_developer_run", commit_then_raise)
    result = rig.invoke()
    assert result.finalization is Finalization.ALREADY_FINAL
    assert result.run_status is RunStatus.OK and result.status is InvocationStatus.COMPLETED
    assert len(developer_events(rig, dev_run(rig).id)) == 0


def test_run_ja_fechado_por_outro_escritor_nunca_e_reescrito(h: Harness) -> None:
    def hook(request: Any) -> None:
        mutate(  # a recuperação (E8.4.5) fechou o Run enquanto o provider rodava
            rig,
            Run,
            request.run_id,
            status=RunStatus.INTERRUPTED,
            finished_at=datetime.now(UTC),
            duration_ms=7,
        )

    rig = build_scripted(h, lambda ident: ScriptedRuntime(ident, hook=hook))
    result = rig.invoke()
    run = dev_run(rig)
    assert run.status is RunStatus.INTERRUPTED and run.duration_ms == 7 and run.summary is None
    assert result.finalization is Finalization.ALREADY_FINAL
    assert (
        result.run_status is RunStatus.INTERRUPTED
        and result.status is not InvocationStatus.COMPLETED
    )


def test_liberacao_do_binding_que_nao_se_prova_e_reportada(h: Harness) -> None:
    class _Stuck(HookedBindings):
        def unbind_run(self, token: object) -> bool:
            return False

        def revoke_run(self, token: object) -> bool:
            return False

    rig = build_scripted(h, bindings=_Stuck(h.bindings))
    result = rig.invoke()
    run = dev_run(rig)
    assert run.status is RunStatus.OK  # o Run fecha pelo que o provider fez…
    assert result.binding is BindingOutcome.RELEASE_FAILED  # …e a pendência é explícita
    assert result.status is InvocationStatus.UNFINALIZED and result.requires_finalization


def test_liberacao_que_falha_mas_revogacao_vale_fecha_ok_com_binding_negado(h: Harness) -> None:
    class _NoUnbind(HookedBindings):
        def unbind_run(self, token: object) -> bool:
            return False

    rig = build_scripted(h, bindings=_NoUnbind(h.bindings))
    result = rig.invoke()
    assert result.status is InvocationStatus.COMPLETED
    assert result.binding is BindingOutcome.REVOKED
    not_authorized(rig, dev_run(rig).id)


# ======================================================= 8 · capability vinculada ao aprovado


def _capability_refused(rig: Rig, code: InvocationCode, event: SafetyEventKind | None) -> None:
    result = rig.invoke()
    assert result.status is InvocationStatus.REFUSED and result.code is code, result
    assert dev_runs(rig) == [] and rig.runtime.calls == []
    kinds = [e.kind for e in events_of(rig, rig.control_run_id)]
    if event is None:
        assert not kinds
    else:
        assert event in kinds


def test_sem_prover_a_invocacao_recusa_com_safety_event(h: Harness) -> None:
    rig = build_scripted(h)
    rig.service = rig.with_service(prover=None)
    _capability_refused(
        rig, InvocationCode.CAPABILITY_UNPROVEN, SafetyEventKind.CAPABILITY_UNENFORCEABLE
    )


def test_sem_expectativa_a_invocacao_recusa(h: Harness) -> None:
    rig = build_scripted(h)
    rig.service = rig.with_service(expected_capability_binding=None)
    _capability_refused(
        rig, InvocationCode.CAPABILITY_UNPROVEN, SafetyEventKind.CAPABILITY_UNENFORCEABLE
    )


@pytest.mark.parametrize("field", ["adapter_id", "adapter_version", "model"])
def test_expectativa_que_diverge_do_binding_aprovado_e_recusada(h: Harness, field: str) -> None:
    rig = build_scripted(h)
    wrong = expected_binding(
        declaration(transport=RUN_TRANSPORT), transport=RUN_TRANSPORT, **{field: "outro"}
    )
    rig.service = rig.with_service(expected_capability_binding=wrong)
    _capability_refused(rig, InvocationCode.BINDING_NOT_APPROVED, SafetyEventKind.CAPABILITY_DENIED)


def test_expectativa_com_outro_transporte_e_recusada(h: Harness) -> None:
    rig = build_scripted(h)
    wrong = expected_binding(declaration(transport=RUN_TRANSPORT), transport="cli")
    rig.service = rig.with_service(expected_capability_binding=wrong)
    _capability_refused(rig, InvocationCode.BINDING_NOT_APPROVED, SafetyEventKind.CAPABILITY_DENIED)


@pytest.mark.parametrize("field", ["adapter", "adapter_version", "model"])
def test_identidade_do_provider_diferente_do_aprovado_e_recusada(h: Harness, field: str) -> None:
    rig = build_scripted(h, lambda ident: ScriptedRuntime(replace(ident, **{field: "outro"})))
    _capability_refused(rig, InvocationCode.BINDING_NOT_APPROVED, SafetyEventKind.CAPABILITY_DENIED)


def test_perfil_aprovado_diferente_do_requerido_e_recusado(h: Harness) -> None:
    rig = build_scripted(h)
    with h.factory() as fresh:
        task = fresh.get(WorkspaceTask, rig.task.id)
        assert task is not None and task.approved_fingerprint_parts is not None
        parts = dict(task.approved_fingerprint_parts)
        parts["tool_profile_hash"] = "f" * 64
        task.approved_fingerprint_parts = parts
        fresh.commit()
    _capability_refused(rig, InvocationCode.CAPABILITY_MISMATCH, SafetyEventKind.CAPABILITY_DENIED)


def test_perfil_efetivo_que_habilita_comando_nao_e_comprovado(h: Harness) -> None:
    rig = build_scripted(h)
    from app.safety.capability_profile import EnforcementMode

    bad_prover = VerifyingCapabilityProver(
        verifier=ScriptedVerifier(
            observed_profile=profile(execute_commands=EnforcementMode.UNMEDIATED)
        ),
        declaration=declaration(transport=RUN_TRANSPORT),
    )
    rig.service = rig.with_service(prover=bad_prover)
    result = rig.invoke()
    assert result.status is InvocationStatus.REFUSED
    assert result.code in {InvocationCode.CAPABILITY_UNPROVEN, InvocationCode.CAPABILITY_MISMATCH}
    assert dev_runs(rig) == [] and rig.runtime.calls == []
    assert events_of(rig, rig.control_run_id)


def test_falha_tecnica_do_provador_nao_vira_prova_e_nao_chama_o_provider(h: Harness) -> None:
    class _Broken:
        def prove(self, required: str) -> Any:
            raise RuntimeError("verificador caiu")

    rig = build_scripted(h)
    rig.service = rig.with_service(prover=_Broken())
    result = rig.invoke()
    assert (
        result.status is InvocationStatus.REFUSED and result.code is InvocationCode.INTERNAL_ERROR
    )
    assert dev_runs(rig) == [] and rig.runtime.calls == []


def test_a_invocacao_nao_aceita_expectativa_pelo_metodo_invoke(h: Harness) -> None:
    import inspect

    from app.orchestrator.developer_invocation import DeveloperInvocationService

    assert list(inspect.signature(DeveloperInvocationService.invoke).parameters) == [
        "self",
        "context",
        "is_cancelled",
    ]


# ============================================================ 9/10 · limites desta etapa


def test_a_invocacao_nao_cria_outro_run_nem_toca_testes_diff_ou_task(h: Harness) -> None:
    rig = build_scripted(h)
    rig.invoke()
    with h.factory() as fresh:
        agents = sorted(
            a.value for a in fresh.scalars(select(Run.agent).where(Run.task_id == rig.task.id))
        )
        total = fresh.scalar(
            select(func.count()).select_from(Run).where(Run.task_id == rig.task.id)
        )
    assert agents == ["developer", "orchestrator"] and total == 2  # sem test_runner, sem auditor
    untouched_control(rig)


def test_o_estado_do_banco_nao_guarda_caminho_prompt_nem_raciocinio(h: Harness) -> None:
    rig = build_scripted(h)
    rig.invoke()
    run = dev_run(rig)
    assert run.prompt_sha256 is None and run.log_ref is None
    blob = json.dumps([run.summary, run.error_summary, [e.subject for e in events_of(rig)]])
    assert rig.context.worktree_path not in blob
    assert not any(token in blob.lower() for token in ("thinking", "chain-of-thought"))


def test_nao_ha_rede_nem_credencial_na_pilha_real(h: Harness) -> None:
    fake = FakeTransport([final("ok")])
    rig = build_real(h, fake)
    # o guarda de rede está ativo (prova do negativo): destino externo falha o teste.
    with socket.socket() as probe, pytest.raises(AssertionError, match="rede proibida"):
        probe.connect(("203.0.113.9", 443))
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert rig.invoke().status is InvocationStatus.COMPLETED
    assert fake.closed


def test_perfil_do_provider_diferente_do_oficial_nao_chama_o_provider(h: Harness) -> None:
    fake = FakeTransport([final("nunca")])
    rig = build_real(h, fake)
    from app.safety.capability_profile import EnforcementMode

    calls: list[Any] = []

    class _Mislabeled:
        def capability_profile(self) -> Any:
            return profile(execute_commands=EnforcementMode.UNMEDIATED)

        def run(self, *a: Any, **k: Any) -> Any:
            calls.append(a)  # NÃO levanta: se for chamado, a lista denuncia
            return None

    rig.runtime._provider = _Mislabeled()
    result = rig.invoke()
    assert result.status is InvocationStatus.FAILED
    run = dev_run(rig)
    assert run.status is RunStatus.ERROR and fake.calls == []
    assert calls == [] and not result.report.provider_called
    assert profile(execute_commands=EnforcementMode.UNMEDIATED) != DEVELOPER_V1_PROFILE


def test_o_executor_do_run_e_encerrado_e_nao_aceita_mais_chamadas(h: Harness) -> None:
    """Depois da invocação o executor está fechado: uma referência retida não executa nada."""
    from app.tool_executor.contracts import ReadFile, ToolStatus

    fake = FakeTransport([tool_use("ff_read_file", {"path": "src/util.py"}), final("lido")])
    rig = build_real(h, fake)
    created: list[Any] = []
    real_factory = rig.runtime._factory

    class _Spy:
        def create(self, *args: Any, **kwargs: Any) -> Any:
            executor = real_factory.create(*args, **kwargs)
            created.append(executor)
            return executor

    rig.runtime._factory = _Spy()
    assert rig.invoke().status is InvocationStatus.COMPLETED
    [executor] = created
    late = executor.execute(ReadFile("src/util.py"))
    assert late.status is ToolStatus.ERROR  # `executor_closed`: nada executa depois do Run


def test_defeito_inesperado_apos_a_reivindicacao_fecha_o_run_com_erro_interno(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Um bug nosso depois do claim não deixa o Run `running`, não vaza texto e não relança."""
    import app.orchestrator.developer_invocation as service_module

    def bug(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bug com ghp_0123456789abcdefghijklmnopqrstuvwxyz")

    rig = build_scripted(h)
    monkeypatch.setattr(service_module, "decide_run", bug)
    result = rig.invoke()
    run = dev_run(rig)
    assert result.status is InvocationStatus.FAILED and result.code is InvocationCode.INTERNAL_ERROR
    assert (
        run.status is RunStatus.ERROR and run.error_summary == "developer_invocation:internal_error"
    )
    assert result.binding is BindingOutcome.REVOKED and "ghp_" not in repr(result)
    not_authorized(rig, run.id)
    assert len(rig.runtime.calls) == 1
