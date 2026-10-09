"""E8.4.1 — contrato puro de execução: chave, ids de componentes, limites, política E8 e
classificação de resultados.

Nada aqui toca banco, git ou filesystem: é o que a E8.4.2–E8.4.6 vão consumir, travado antes de
existir qualquer pipeline.
"""

from __future__ import annotations

import ast
import itertools
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.agent_runtime.runners import GENERIC_SUBPROCESS_RUNNER_ID
from app.db.enums import FailureReason, RunStatus, TaskStatus
from app.orchestrator import resource_router
from app.orchestrator.errors import InvalidInvocationId
from app.orchestrator.execution_contract import (
    AUTOMATIC_RETRY_ON_TEST_FAILURE,
    COMPONENT_DEVELOPER,
    COMPONENT_TEST_RUNNER,
    E2E_OBJECT_FORMATS,
    E8_AVAILABLE_AGENTS,
    MAX_INVOCATION_ID_LENGTH,
    SUPPORTED_TEST_RUNNER_IDS,
    AdmissionOutcome,
    DeveloperOutcome,
    ExecutionAdmission,
    ExecutionClassification,
    ExecutionCode,
    ExecutionFacts,
    IntegrityVerdict,
    RunnerOutcome,
    TaskResolution,
    classify_execution,
    component_invocation_id,
    operation_timeout_s,
    task_deadline_remaining_s,
    unavailable_agents,
    validate_invocation_id,
    workflow_policy_supported,
)
from app.orchestrator.fingerprint import WorkflowPolicy, diverged_fields, e8_single_pass_policy
from app.orchestrator.resource_router import ResourceDecision

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
CONTROL_RUN_ID = "1111aaaa-2222-4333-8444-5555555555bb"

# ================================================================ chave idempotente


@pytest.mark.parametrize(
    "key", ["a", "run-1", "9f2c", "exec.2026-10-08_01", "A" * MAX_INVOCATION_ID_LENGTH]
)
def test_chave_valida_e_devolvida_intacta(key: str) -> None:
    assert validate_invocation_id(key) == key


@pytest.mark.parametrize(
    "key",
    [
        None,
        "",
        123,
        b"abc",
        "tem espaço",
        "tem:dois-pontos",
        "-comeca-com-hifen",
        ".comeca-com-ponto",
        "acentuação",
        "quebra\nlinha",
        "barra/inclinada",
        "A" * (MAX_INVOCATION_ID_LENGTH + 1),
    ],
)
def test_chave_ausente_ou_malformada_e_recusada(key: Any) -> None:
    with pytest.raises(InvalidInvocationId) as caught:
        validate_invocation_id(key)
    assert caught.value.status_code == 422


def test_o_alfabeto_da_chave_nao_contem_o_separador_dos_ids_derivados() -> None:
    """Colisão estruturalmente impossível: id derivado sempre tem `:`, chave de cliente nunca."""
    derived = {
        component_invocation_id(CONTROL_RUN_ID, c)
        for c in (COMPONENT_DEVELOPER, COMPONENT_TEST_RUNNER)
    }
    assert len(derived) == 2
    for value in derived:
        assert ":" in value
        with pytest.raises(InvalidInvocationId):
            validate_invocation_id(value)


def test_ids_de_componentes_sao_deterministicos_e_distintos_entre_runs() -> None:
    outro = "9999cccc-2222-4333-8444-5555555555bb"
    a1 = component_invocation_id(CONTROL_RUN_ID, COMPONENT_DEVELOPER)
    assert a1 == component_invocation_id(CONTROL_RUN_ID, COMPONENT_DEVELOPER)
    assert a1 == f"{CONTROL_RUN_ID}:developer"
    assert component_invocation_id(outro, COMPONENT_DEVELOPER) != a1
    assert component_invocation_id(CONTROL_RUN_ID, COMPONENT_TEST_RUNNER) != a1
    assert len(a1) <= MAX_INVOCATION_ID_LENGTH


@pytest.mark.parametrize("run_id", ["", "não-é-uuid", CONTROL_RUN_ID.upper(), "x" * 36])
def test_id_derivado_exige_uuid_de_run_de_controle(run_id: str) -> None:
    with pytest.raises(ValueError, match="UUID"):
        component_invocation_id(run_id, COMPONENT_DEVELOPER)


def test_componente_desconhecido_e_recusado() -> None:
    with pytest.raises(ValueError, match="componente"):
        component_invocation_id(CONTROL_RUN_ID, "auditor")


# ===================================================================== limites de tempo


def test_limites_aprovados_sao_os_valores_literais() -> None:
    assert resource_router.RUN_TIMEOUT_S == 1200
    assert resource_router.TASK_TIMEOUT_S == 1800


def test_execution_limits_carrega_os_timeouts_sem_mudar_as_demais_chaves() -> None:
    decision = ResourceDecision(
        agents=("developer",),
        max_context_tokens=60_000,
        max_total_tokens=200_000,
        max_attempts=2,
        max_fix_rounds=2,
        max_parallel_agents=1,
        max_agents=3,
        run_timeout_s=1200,
        task_timeout_s=1800,
    )
    assert decision.as_execution_limits() == {
        "max_context_tokens": 60_000,
        "max_total_tokens": 200_000,
        "max_attempts": 2,
        "max_fix_rounds": 2,
        "max_parallel_agents": 1,
        "max_agents": 3,
        "run_timeout_s": 1200,
        "task_timeout_s": 1800,
    }


def test_prazo_restante_da_tentativa() -> None:
    inicio = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    assert task_deadline_remaining_s(inicio, 1800, now=inicio) == 1800
    assert task_deadline_remaining_s(inicio, 1800, now=inicio + timedelta(seconds=600)) == 1200
    # esgotado nunca fica negativo
    assert task_deadline_remaining_s(inicio, 1800, now=inicio + timedelta(seconds=1800)) == 0.0
    assert task_deadline_remaining_s(inicio, 1800, now=inicio + timedelta(hours=5)) == 0.0


def test_prazo_recusa_instante_naive_e_timeout_nao_positivo() -> None:
    inicio = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="naive"):
        task_deadline_remaining_s(inicio.replace(tzinfo=None), 1800, now=inicio)
    with pytest.raises(ValueError, match="naive"):
        task_deadline_remaining_s(inicio, 1800, now=inicio.replace(tzinfo=None))
    with pytest.raises(ValueError, match="positivo"):
        task_deadline_remaining_s(inicio, 0, now=inicio)


def test_timeout_da_operacao_respeita_o_menor_dos_tetos() -> None:
    # o run (20 min) vence quando a task ainda tem muito tempo
    assert operation_timeout_s(run_timeout_s=1200, remaining_task_s=1700) == 1200
    # o que resta da task vence quando é menor que o do run
    assert operation_timeout_s(run_timeout_s=1200, remaining_task_s=300) == 300
    # TestPolicy/budget mais restritivo continua prevalecendo
    assert operation_timeout_s(run_timeout_s=1200, remaining_task_s=1700, policy_timeout_s=90) == 90
    # política mais frouxa do que o run não alarga
    assert (
        operation_timeout_s(run_timeout_s=1200, remaining_task_s=1700, policy_timeout_s=5000)
        == 1200
    )
    # sem tempo restante a operação não deve nascer
    assert operation_timeout_s(run_timeout_s=1200, remaining_task_s=0.0) == 0.0
    assert operation_timeout_s(run_timeout_s=1200, remaining_task_s=-5.0) == 0.0


def test_timeout_da_operacao_recusa_valores_nao_positivos() -> None:
    with pytest.raises(ValueError, match="run_timeout_s"):
        operation_timeout_s(run_timeout_s=0, remaining_task_s=10)
    with pytest.raises(ValueError, match="política"):
        operation_timeout_s(run_timeout_s=10, remaining_task_s=10, policy_timeout_s=0)


# ================================================================ política E8 single-pass


def test_politica_e8_nao_exige_auditoria_e_a_normal_continua_exigindo() -> None:
    e8 = e8_single_pass_policy(max_fix_rounds=2, max_attempts=2)
    normal = WorkflowPolicy(max_fix_rounds=2, max_attempts=2)

    assert e8.audit_required_on_nonempty_diff is False
    # a política normal futura (E9) NÃO foi alterada em silêncio
    assert WorkflowPolicy().audit_required_on_nonempty_diff is True
    assert normal.audit_required_on_nonempty_diff is True


def test_politica_e8_participa_do_workflow_policy_hash() -> None:
    e8 = e8_single_pass_policy(max_fix_rounds=2, max_attempts=2)
    normal = WorkflowPolicy(max_fix_rounds=2, max_attempts=2)

    assert e8.policy_hash() != normal.policy_hash()
    assert e8.as_canonical()["audit_required_on_nonempty_diff"] is False
    # a única diferença entre as duas é a obrigatoriedade de auditoria
    assert diverged_fields(e8.as_canonical(), normal.as_canonical()) == (
        "audit_required_on_nonempty_diff",
    )


def test_so_a_politica_sem_auditoria_obrigatoria_e_executavel_na_e8() -> None:
    assert workflow_policy_supported(e8_single_pass_policy(max_fix_rounds=2, max_attempts=2))
    assert not workflow_policy_supported(WorkflowPolicy())


# ===================================================== pré-requisitos desta fase (puros)


def test_apenas_o_developer_esta_disponivel_na_e8() -> None:
    assert frozenset({"developer"}) == E8_AVAILABLE_AGENTS
    assert unavailable_agents(("developer",)) == ()
    assert unavailable_agents(("developer", "architect")) == ("architect",)
    assert unavailable_agents(("developer", "architect", "researcher")) == (
        "architect",
        "researcher",
    )
    assert unavailable_agents(("auditor",)) == ("auditor",)


def test_formato_de_objeto_e2e_e_somente_sha1() -> None:
    assert frozenset({"sha1"}) == E2E_OBJECT_FORMATS


def test_runner_suportado_espelha_o_do_test_runner_v1() -> None:
    """O Orchestrator não importa `agent_runtime`; este teste cruza as duas constantes."""
    assert frozenset({GENERIC_SUBPROCESS_RUNNER_ID}) == SUPPORTED_TEST_RUNNER_IDS


# ============================================================ resultado da admissão


class _FakeRow:
    """Qualquer objeto serve: `ExecutionAdmission` só valida a forma do conjunto."""


def test_slot_busy_nao_carrega_run_e_tem_codigo() -> None:
    from app.orchestrator.execution_contract import SLOT_BUSY_CODE

    ok = ExecutionAdmission(AdmissionOutcome.SLOT_BUSY, _FakeRow(), code=SLOT_BUSY_CODE)  # type: ignore[arg-type]
    assert ok.run is None and ok.code == "slot_busy"
    with pytest.raises(ValueError, match="slot_busy"):
        ExecutionAdmission(AdmissionOutcome.SLOT_BUSY, _FakeRow(), _FakeRow(), code="slot_busy")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="slot_busy"):
        ExecutionAdmission(AdmissionOutcome.SLOT_BUSY, _FakeRow())  # type: ignore[arg-type]


@pytest.mark.parametrize("outcome", [AdmissionOutcome.ADMITTED, AdmissionOutcome.REPLAYED])
def test_admissao_e_repeticao_carregam_o_run_e_nenhum_codigo(outcome: AdmissionOutcome) -> None:
    with pytest.raises(ValueError, match="Run de controle"):
        ExecutionAdmission(outcome, _FakeRow())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Run de controle"):
        ExecutionAdmission(outcome, _FakeRow(), _FakeRow(), code="slot_busy")  # type: ignore[arg-type]
    assert ExecutionAdmission(outcome, _FakeRow(), _FakeRow()).code is None  # type: ignore[arg-type]


# =========================================================== classificação de resultados

V = IntegrityVerdict
D = DeveloperOutcome
R = RunnerOutcome


def _facts(
    integrity: IntegrityVerdict = V.VERIFIED,
    developer: DeveloperOutcome = D.COMPLETED,
    tests: RunnerOutcome = R.PASSED,
    *,
    policy: bool = True,
    terminal: TaskStatus | None = None,
) -> ExecutionFacts:
    return ExecutionFacts(
        integrity=integrity,
        developer=developer,
        tests=tests,
        policy_satisfied=policy,
        confirmed_terminal=terminal,
    )


def _resolution(status: TaskStatus, reason: FailureReason | None = None) -> TaskResolution:
    return TaskResolution(status, reason)


#: A tabela de [E8.4.1] §11, **literal** — não derivada do código.
TABELA_LITERAL: list[
    tuple[str, ExecutionFacts, ExecutionCode, RunStatus, TaskResolution | None]
] = [
    (
        "done",
        _facts(),
        ExecutionCode.SUCCESS,
        RunStatus.OK,
        _resolution(TaskStatus.DONE),
    ),
    (
        "needs_fix",
        _facts(tests=R.FAILED),
        ExecutionCode.TESTS_FAILED,
        RunStatus.ERROR,
        _resolution(TaskStatus.NEEDS_FIX),
    ),
    (
        "timeout-developer",
        _facts(developer=D.TIMEOUT, tests=R.NOT_RUN),
        ExecutionCode.TIMEOUT,
        RunStatus.TIMEOUT,
        _resolution(TaskStatus.FAILED, FailureReason.TIMEOUT),
    ),
    (
        "timeout-testes",
        _facts(tests=R.TIMEOUT),
        ExecutionCode.TIMEOUT,
        RunStatus.TIMEOUT,
        _resolution(TaskStatus.FAILED, FailureReason.TIMEOUT),
    ),
    (
        "limite-excedido",
        _facts(developer=D.LIMIT_EXCEEDED, tests=R.NOT_RUN),
        ExecutionCode.LIMIT_EXCEEDED,
        RunStatus.BLOCKED,
        _resolution(TaskStatus.FAILED, FailureReason.LIMIT_EXCEEDED),
    ),
    (
        "integridade-violada",
        _facts(integrity=V.VIOLATED),
        ExecutionCode.INTEGRITY_VIOLATED,
        RunStatus.BLOCKED,
        _resolution(TaskStatus.FAILED, FailureReason.BLOCKED_BY_POLICY),
    ),
    (
        "politica-nao-satisfeita",
        _facts(policy=False),
        ExecutionCode.POLICY_NOT_SATISFIED,
        RunStatus.BLOCKED,
        _resolution(TaskStatus.FAILED, FailureReason.BLOCKED_BY_POLICY),
    ),
    (
        "provider-bloqueado-por-politica",
        _facts(developer=D.POLICY_BLOCKED, tests=R.NOT_RUN),
        ExecutionCode.POLICY_BLOCKED,
        RunStatus.BLOCKED,
        _resolution(TaskStatus.FAILED, FailureReason.BLOCKED_BY_POLICY),
    ),
    (
        "provider-error",
        _facts(developer=D.PROVIDER_ERROR, tests=R.NOT_RUN),
        ExecutionCode.PROVIDER_ERROR,
        RunStatus.ERROR,
        _resolution(TaskStatus.FAILED, FailureReason.PROVIDER_ERROR),
    ),
    (
        "observacao-impossivel",
        _facts(integrity=V.UNVERIFIABLE),
        ExecutionCode.VERIFICATION_INCOMPLETE,
        RunStatus.ERROR,
        _resolution(TaskStatus.FAILED, FailureReason.INTERNAL_ERROR),
    ),
    (
        "falha-tecnica-do-runner",
        _facts(tests=R.TECHNICAL_FAILURE),
        ExecutionCode.INTERNAL_ERROR,
        RunStatus.ERROR,
        _resolution(TaskStatus.FAILED, FailureReason.INTERNAL_ERROR),
    ),
    (
        "falha-tecnica-do-developer",
        _facts(developer=D.INTERNAL_ERROR, tests=R.NOT_RUN),
        ExecutionCode.INTERNAL_ERROR,
        RunStatus.ERROR,
        _resolution(TaskStatus.FAILED, FailureReason.INTERNAL_ERROR),
    ),
    (
        "crash-do-developer",
        _facts(developer=D.INTERRUPTED, tests=R.NOT_RUN),
        ExecutionCode.INTERRUPTED,
        RunStatus.INTERRUPTED,
        _resolution(TaskStatus.FAILED, FailureReason.INTERRUPTED),
    ),
    (
        "crash-dos-testes",
        _facts(tests=R.INTERRUPTED),
        ExecutionCode.INTERRUPTED,
        RunStatus.INTERRUPTED,
        _resolution(TaskStatus.FAILED, FailureReason.INTERRUPTED),
    ),
    (
        "cancelamento-humano-venceu-o-cas",
        _facts(developer=D.CANCELLED, tests=R.NOT_RUN, terminal=TaskStatus.CANCELLED),
        ExecutionCode.CANCELLED,
        RunStatus.CANCELLED,
        None,  # a task já é `cancelled`: não é tocada
    ),
    (
        "testes-nao-rodaram-com-developer-ok",
        _facts(tests=R.NOT_RUN),
        ExecutionCode.INTERNAL_ERROR,
        RunStatus.ERROR,
        _resolution(TaskStatus.FAILED, FailureReason.INTERNAL_ERROR),
    ),
]


@pytest.mark.parametrize(
    ("facts", "code", "run_status", "resolution"),
    [row[1:] for row in TABELA_LITERAL],
    ids=[row[0] for row in TABELA_LITERAL],
)
def test_tabela_de_resultados_literal(
    facts: ExecutionFacts,
    code: ExecutionCode,
    run_status: RunStatus,
    resolution: TaskResolution | None,
) -> None:
    verdict = classify_execution(facts)
    assert verdict == ExecutionClassification(code, run_status, resolution)


# ---------------------------------------------------------------------- precedência


def test_precedencia_2_violacao_comprovada_vence_falha_de_provider_e_testes() -> None:
    verdict = classify_execution(
        _facts(integrity=V.VIOLATED, developer=D.PROVIDER_ERROR, tests=R.FAILED)
    )
    assert verdict.code is ExecutionCode.INTEGRITY_VIOLATED
    assert verdict.task_resolution == _resolution(
        TaskStatus.FAILED, FailureReason.BLOCKED_BY_POLICY
    )


def test_precedencia_2_politica_nao_satisfeita_vence_testes_falhos() -> None:
    verdict = classify_execution(_facts(policy=False, tests=R.FAILED))
    assert verdict.code is ExecutionCode.POLICY_NOT_SATISFIED


def test_precedencia_3_verificacao_incompleta_vence_provider_e_testes_falhos() -> None:
    """Não verificada ≠ sucesso nem "só testes falhos": sem integridade provada, nada de
    `needs_fix`."""
    for developer, tests in [
        (D.PROVIDER_ERROR, R.NOT_RUN),
        (D.TIMEOUT, R.NOT_RUN),
        (D.COMPLETED, R.FAILED),
        (D.COMPLETED, R.PASSED),
    ]:
        verdict = classify_execution(
            _facts(integrity=V.UNVERIFIABLE, developer=developer, tests=tests)
        )
        assert verdict.code is ExecutionCode.VERIFICATION_INCOMPLETE
        assert verdict.task_resolution == _resolution(
            TaskStatus.FAILED, FailureReason.INTERNAL_ERROR
        )


def test_precedencia_4_falha_do_provider_vence_testes_falhos() -> None:
    verdict = classify_execution(_facts(developer=D.PROVIDER_ERROR, tests=R.FAILED))
    assert verdict.code is ExecutionCode.PROVIDER_ERROR


def test_precedencia_5_testes_falhos_com_integridade_verificada_viram_needs_fix() -> None:
    verdict = classify_execution(_facts(tests=R.FAILED))
    assert verdict.task_resolution == _resolution(TaskStatus.NEEDS_FIX)
    assert verdict.task_resolution is not None and verdict.task_resolution.failure_reason is None


def test_precedencia_1_terminal_confirmado_por_cas_nunca_e_sobrescrito() -> None:
    for terminal in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
        for facts in (_facts(terminal=terminal), _facts(tests=R.FAILED, terminal=terminal)):
            assert classify_execution(facts).task_resolution is None


def test_terminal_cancelado_preserva_o_resultado_do_run() -> None:
    """[ADR-0008]: se o cancelamento vence após o trabalho terminar, o Run preserva o resultado."""
    verdict = classify_execution(_facts(terminal=TaskStatus.CANCELLED))
    assert verdict.run_status is RunStatus.OK
    assert verdict.code is ExecutionCode.SUCCESS
    assert verdict.task_resolution is None


def test_terminal_confirmado_nao_esconde_violacao_de_integridade_no_run() -> None:
    verdict = classify_execution(_facts(integrity=V.VIOLATED, terminal=TaskStatus.CANCELLED))
    assert verdict.run_status is RunStatus.BLOCKED
    assert verdict.task_resolution is None


def test_confirmed_terminal_precisa_ser_terminal() -> None:
    with pytest.raises(ValueError, match="terminal"):
        classify_execution(_facts(terminal=TaskStatus.EXECUTING))


@pytest.mark.parametrize("cancelled", [D.CANCELLED])
def test_cancelamento_observado_sem_cas_de_cancelamento_e_inconsistencia(
    cancelled: DeveloperOutcome,
) -> None:
    """Só o comando `cancel`, por CAS, cancela uma task: sem ele, é `internal_error`."""
    verdict = classify_execution(_facts(developer=cancelled, tests=R.NOT_RUN))
    assert verdict.code is ExecutionCode.INTERNAL_ERROR
    assert verdict.task_resolution == _resolution(TaskStatus.FAILED, FailureReason.INTERNAL_ERROR)
    verdict = classify_execution(_facts(tests=R.CANCELLED))
    assert verdict.code is ExecutionCode.INTERNAL_ERROR
    # com outro terminal confirmado (não `cancelled`) também é inconsistência no Run
    verdict = classify_execution(_facts(tests=R.CANCELLED, terminal=TaskStatus.FAILED))
    assert verdict.code is ExecutionCode.INTERNAL_ERROR


def test_negacao_de_ferramenta_nao_e_violacao_global() -> None:
    """O contrato só conhece fatos estruturados: não há "tool negada" entre eles, e um
    Developer `COMPLETED` com integridade verificada segue o caminho normal."""
    nomes = {member.name for member in D} | {member.name for member in V} | {m.name for m in R}
    assert not any("DENIED" in nome or "TOOL" in nome for nome in nomes)
    assert classify_execution(_facts()).code is ExecutionCode.SUCCESS


def test_needs_fix_nao_dispara_retry_automatico() -> None:
    assert AUTOMATIC_RETRY_ON_TEST_FAILURE is False
    verdict = classify_execution(_facts(tests=R.FAILED))
    assert set(ExecutionClassification.__slots__) == {
        "code",
        "run_status",
        "task_resolution",
    }, "a classificação não carrega pedido de retry"
    assert verdict.task_resolution is not None
    assert verdict.task_resolution.status is TaskStatus.NEEDS_FIX


# ------------------------------------------------- propriedades sobre o produto inteiro

_TERMINAIS: list[TaskStatus | None] = [
    None,
    TaskStatus.DONE,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
]
_PRODUTO = list(itertools.product(V, D, R, (True, False), _TERMINAIS))


def test_o_produto_inteiro_tem_veredito_e_respeita_as_invariantes() -> None:
    assert len(_PRODUTO) == 3 * 8 * 7 * 2 * 4

    for integrity, developer, tests, policy, terminal in _PRODUTO:
        facts = _facts(integrity, developer, tests, policy=policy, terminal=terminal)
        verdict = classify_execution(facts)
        resolution = verdict.task_resolution

        # nunca devolve um status aberto, e `ok` só acompanha sucesso
        assert verdict.run_status is not RunStatus.RUNNING
        assert (verdict.run_status is RunStatus.OK) == (verdict.code is ExecutionCode.SUCCESS)

        # terminal confirmado: a task nunca é tocada
        if terminal is not None:
            assert resolution is None
            continue

        assert resolution is not None, facts
        # `failure_reason` ⟺ failed (invariante de [02] §3)
        assert (resolution.status is TaskStatus.FAILED) == (resolution.failure_reason is not None)
        # nunca transiciona para um estado não-final da execução
        assert resolution.status in {TaskStatus.DONE, TaskStatus.NEEDS_FIX, TaskStatus.FAILED}

        sucesso = (
            integrity is V.VERIFIED and developer is D.COMPLETED and tests is R.PASSED and policy
        )
        assert (resolution.status is TaskStatus.DONE) == sucesso, facts

        precisa_corrigir = (
            integrity is V.VERIFIED and developer is D.COMPLETED and tests is R.FAILED and policy
        )
        assert (resolution.status is TaskStatus.NEEDS_FIX) == precisa_corrigir, facts

        # violação comprovada sempre vence
        if integrity is V.VIOLATED:
            assert resolution.failure_reason is FailureReason.BLOCKED_BY_POLICY
        # integridade não provada nunca vira sucesso nem needs_fix nem erro de provider
        if integrity is V.UNVERIFIABLE and not policy:
            assert resolution.failure_reason is FailureReason.BLOCKED_BY_POLICY
        elif integrity is V.UNVERIFIABLE:
            assert resolution.failure_reason is FailureReason.INTERNAL_ERROR


def test_classificacao_e_deterministica_e_imutavel() -> None:
    facts = _facts(tests=R.FAILED)
    assert classify_execution(facts) == classify_execution(facts)
    verdict = classify_execution(facts)
    with pytest.raises(AttributeError):
        verdict.code = ExecutionCode.SUCCESS  # type: ignore[misc]


def test_task_resolution_recusa_failure_reason_incoerente() -> None:
    with pytest.raises(ValueError, match="failure_reason"):
        TaskResolution(TaskStatus.FAILED, None)
    with pytest.raises(ValueError, match="failure_reason"):
        TaskResolution(TaskStatus.DONE, FailureReason.TIMEOUT)
    with pytest.raises(ValueError, match="failure_reason"):
        TaskResolution(TaskStatus.NEEDS_FIX, FailureReason.TESTS_FAILED)


# ======================================================================= arquitetura


def test_o_contrato_e_puro_sem_io_nem_relogio_nem_subprocess() -> None:
    """Sem IO: nenhum import de sistema de arquivos, processo, rede, git, sessão ou relógio."""
    caminho = APP_ROOT / "orchestrator" / "execution_contract.py"
    tree = ast.parse(caminho.read_text(encoding="utf-8"))
    importados: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            importados.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            importados.add(node.module)
    proibidos = (
        "subprocess",
        "os",
        "pathlib",
        "socket",
        "http",
        "urllib",
        "sqlalchemy",
        "app.git_runtime",
        "app.process_runtime",
        "app.agent_runtime",
        "app.tool_executor",
        "app.execution_verification",
        "app.api",
    )
    for modulo in importados:
        assert not modulo.startswith(proibidos), modulo

    chamadas = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    assert not any(name in {"open", "datetime.now", "time.time", "utcnow"} for name in chamadas)
