"""Execution Planner e Execution Manager, contra banco e repositório git reais.

Gates cobertos aqui (sub-etapas 5 e 6 do prompt da E6):

* `plan_hash` / `manifest_hash` / `execution_fingerprint` **reproduzíveis** — rodar duas
  vezes sobre o mesmo estado produz os mesmos hashes;
* mudar `test_config` entre duas chamadas de `plan` **muda** o fingerprint;
* workspace sem `test_config` → `test_binding` com os três campos `null`;
* `approved → executing` recusada com 409 e motivo claro;
* `reconcile_on_startup` idempotente;
* **nenhum arquivo do usuário escrito** ([07], gate da E6).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.enums import ComplexityLevel, RiskLevel, RiskSource, TaskStatus, WorkspaceStatus
from app.db.models import DevWorkspace, SafetyEvent, WorkspaceTask
from app.orchestrator import (
    ApprovalFingerprintMismatch,
    ConcurrentTaskUpdate,
    InvalidTask,
    InvalidTestConfig,
    InvalidTransition,
    TaskNotFound,
    TransitionGuardFailed,
    WorkspaceNotPlannable,
    approve,
    cancel,
    create_task,
    get_task,
    latest_manifest,
    list_tasks,
    plan,
    reconcile_on_startup,
    reject,
    start_execution,
)
from app.workspace import set_test_config, update_workspace_status
from tests import context_helpers

VALID_TEST_CONFIG = {
    "runner_id": "generic-subprocess-v1",
    "executable": "pytest",
    "argv": ["-q", "tests"],
    "timeout_seconds": 600,
    "env_allowlist": ["PATH"],
    "network_policy": "unrestricted",
    "output_limits": {"max_stdout_bytes": 1_048_576, "max_stderr_bytes": 262_144},
    "cwd_mode": "task_worktree",
}


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    """Artifact store **fora** do repositório do usuário — como em produção ([02] §5)."""
    return tmp_path / "artifacts"


@pytest.fixture
def task(session: Session, workspace: DevWorkspace) -> WorkspaceTask:
    return create_task(session, workspace.id, title="task", goal="ajustar o util")


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    """Todo arquivo sob `root`, com conteúdo. Para provar que nada foi escrito."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# ----------------------------------------------------------------------- criação


def test_criar_task_nasce_em_draft(session: Session, workspace: DevWorkspace) -> None:
    created = create_task(session, workspace.id, title="  título  ", goal="  objetivo  ")

    assert created.status is TaskStatus.DRAFT
    assert created.title == "título"
    assert created.goal == "objetivo"
    assert created.version == 1
    assert created.phase is None
    assert created.failure_reason is None
    assert created.agents == []


def test_criar_task_recusa_titulo_ou_goal_vazio(session: Session, workspace: DevWorkspace) -> None:
    with pytest.raises(InvalidTask, match="title"):
        create_task(session, workspace.id, title="   ", goal="ok")

    with pytest.raises(InvalidTask, match="goal"):
        create_task(session, workspace.id, title="ok", goal="   ")


def test_workspace_arquivado_nao_aceita_task_nova(
    session: Session, workspace: DevWorkspace
) -> None:
    """[02] §1: "arquivado bloqueia novas tasks"."""
    update_workspace_status(session, workspace.id, WorkspaceStatus.ARCHIVED)

    with pytest.raises(WorkspaceNotPlannable, match="arquivado"):
        create_task(session, workspace.id, title="t", goal="g")


def test_listar_tasks_de_workspace_inexistente_e_404(session: Session) -> None:
    """404, e não lista vazia: os dois estados são distintos para a UI."""
    from app.workspace import WorkspaceNotFound

    with pytest.raises(WorkspaceNotFound):
        list_tasks(session, "nao-existe")


def test_get_task_inexistente_e_404(session: Session) -> None:
    with pytest.raises(TaskNotFound):
        get_task(session, "nao-existe")


# ------------------------------------------------------------------- planejamento


def test_plan_congela_base_commit_e_vai_para_awaiting_approval(
    session: Session, task: WorkspaceTask, repo_path: Path, artifacts_dir: Path
) -> None:
    result = plan(session, task.id, artifacts_dir=artifacts_dir)
    head = context_helpers.head_of(repo_path)

    assert result.task.status is TaskStatus.AWAITING_APPROVAL
    # [02] §6: planning_base_commit == manifest.git_head == base_commit
    assert result.task.planning_base_commit == head
    assert result.task.base_commit == head
    assert result.manifest.git_head == head
    assert result.task.approved_at is None, "planejar não aprova"
    assert result.task.approved_fingerprint is not None, "o candidato é persistido"


def test_plan_nao_escreve_nenhum_arquivo_do_usuario(
    session: Session, task: WorkspaceTask, repo_path: Path, artifacts_dir: Path
) -> None:
    """**GATE de [07] para a E6**: "nenhum arquivo escrito".

    O único efeito em disco permitido é o Rendered Context Artifact, sob `data_dir`, fora
    do repositório.
    """
    antes = _snapshot_tree(repo_path)

    plan(session, task.id, artifacts_dir=artifacts_dir)

    assert _snapshot_tree(repo_path) == antes, "o planejamento tocou o repositório do usuário"
    assert artifacts_dir.is_dir(), "o artefato deveria estar fora do repositório"
    assert list(artifacts_dir.glob("*.json")), "o artefato renderizado não foi gravado"


def test_plan_aplica_o_piso_de_fallback_sem_porta_de_enriquecimento(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    """Toda a E6 roda sem porta: [03] §5 exige que a task não fique `low`."""
    result = plan(session, task.id, artifacts_dir=artifacts_dir)

    assert result.task.risk is RiskLevel.MEDIUM
    assert result.task.complexity is ComplexityLevel.MEDIUM
    assert result.task.risk_source is RiskSource.HARD_RULE
    assert result.analysis.enrichment_unavailable is True


def test_plan_usa_candidate_paths_do_payload(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    """A decisão desta sessão: candidatos são explícitos, não derivados da prosa."""
    result = plan(session, task.id, candidate_paths=["src/util.py"], artifacts_dir=artifacts_dir)

    assert "src/util.py" in result.selection.candidate_paths


def test_plan_com_candidato_que_dispara_hard_rule_fica_high(
    session: Session, workspace: DevWorkspace, repo_path: Path, artifacts_dir: Path
) -> None:
    context_helpers.write(repo_path, "migrations/0001_init.py", "# migration\n")
    context_helpers.commit_all(repo_path, "migration")

    created = create_task(session, workspace.id, title="t", goal="mexer no schema")
    result = plan(
        session,
        created.id,
        candidate_paths=["migrations/0001_init.py"],
        artifacts_dir=artifacts_dir,
    )

    assert result.task.risk is RiskLevel.HIGH
    assert "path:migrations_schema" in result.analysis.hard_rule.matched


def test_plan_recusa_workspace_sem_git(
    session: Session, workspace_sem_git: DevWorkspace, artifacts_dir: Path
) -> None:
    """[04] §8 / [07]: sem git, o workspace serve para contexto mas não para planejar."""
    created = create_task(session, workspace_sem_git.id, title="t", goal="g")

    with pytest.raises(WorkspaceNotPlannable, match="repositório git"):
        plan(session, created.id, artifacts_dir=artifacts_dir)


def test_plan_recusa_workspace_arquivado(
    session: Session, task: WorkspaceTask, workspace: DevWorkspace, artifacts_dir: Path
) -> None:
    update_workspace_status(session, workspace.id, WorkspaceStatus.ARCHIVED)

    with pytest.raises(WorkspaceNotPlannable, match="arquivado"):
        plan(session, task.id, artifacts_dir=artifacts_dir)


# ------------------------------------------- GATE: reprodutibilidade dos três hashes


def test_hashes_sao_reproduziveis_entre_duas_chamadas(
    session: Session, workspace: DevWorkspace, artifacts_dir: Path
) -> None:
    """**GATE**: mesmo estado, duas chamadas, os mesmos `plan_hash`/`manifest_hash`/
    `execution_fingerprint`.

    Duas **tasks** distintas, e não a mesma task planejada duas vezes: o `plan_hash` cobre
    `title` e `goal`, então as duas precisam ser idênticas nesses campos para que o teste
    meça determinismo do planejamento e não igualdade trivial.
    """
    primeira = create_task(session, workspace.id, title="mesma", goal="mesmo objetivo")
    segunda = create_task(session, workspace.id, title="mesma", goal="mesmo objetivo")

    a = plan(session, primeira.id, candidate_paths=["src/util.py"], artifacts_dir=artifacts_dir)
    b = plan(session, segunda.id, candidate_paths=["src/util.py"], artifacts_dir=artifacts_dir)

    assert a.plan_hash == b.plan_hash
    assert a.manifest.manifest_hash == b.manifest.manifest_hash
    assert a.manifest.rendered_context_hash == b.manifest.rendered_context_hash
    assert a.fingerprint == b.fingerprint


def test_replanejar_a_mesma_task_produz_os_mesmos_hashes(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    """Rejeitar e replanejar sem mudar nada tem de reproduzir o fingerprint."""
    primeira = plan(session, task.id, artifacts_dir=artifacts_dir)
    fingerprint_inicial = primeira.fingerprint

    reject(session, task.id, note="quero rever")
    segunda = plan(session, task.id, artifacts_dir=artifacts_dir)

    assert segunda.plan_hash == primeira.plan_hash
    assert segunda.fingerprint == fingerprint_inicial


def test_artefato_e_deduplicado_por_conteudo(
    session: Session, workspace: DevWorkspace, artifacts_dir: Path
) -> None:
    """[02] §5: endereçamento por conteúdo dá deduplicação de graça."""
    a = create_task(session, workspace.id, title="a", goal="objetivo a")
    b = create_task(session, workspace.id, title="b", goal="objetivo b")

    plan(session, a.id, artifacts_dir=artifacts_dir)
    plan(session, b.id, artifacts_dir=artifacts_dir)

    # Os dois planejamentos selecionam o mesmo contexto (o registry está vazio), então o
    # payload renderizado é idêntico e existe **um** blob.
    assert len(list(artifacts_dir.glob("*.json"))) == 1


# ------------------------------------------------ GATE: test_config e o test_binding


def test_sem_test_config_o_binding_tem_os_tres_campos_null(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    """**GATE**: workspace sem `test_config` → `test_binding` todo `null` ([02] §7)."""
    result = plan(session, task.id, artifacts_dir=artifacts_dir)

    binding = result.fingerprint_parts.test_binding

    assert set(binding) == {"runner", "command_hash", "policy_hash"}
    assert binding == {"runner": None, "command_hash": None, "policy_hash": None}


def test_mudar_test_config_entre_dois_planos_muda_o_fingerprint(
    session: Session, workspace: DevWorkspace, artifacts_dir: Path
) -> None:
    """**GATE**: configurar o Test Runner invalida o fingerprint anterior ([04] §7)."""
    primeira = create_task(session, workspace.id, title="mesma", goal="mesmo objetivo")
    sem_config = plan(session, primeira.id, artifacts_dir=artifacts_dir)

    set_test_config(session, workspace.id, VALID_TEST_CONFIG)

    segunda = create_task(session, workspace.id, title="mesma", goal="mesmo objetivo")
    com_config = plan(session, segunda.id, artifacts_dir=artifacts_dir)

    assert com_config.fingerprint != sem_config.fingerprint
    assert com_config.plan_hash == sem_config.plan_hash, (
        "o plano não mudou; só o binding de teste mudou"
    )
    assert com_config.fingerprint_parts.test_binding["runner"] == "generic-subprocess-v1"


def test_mudar_o_comando_de_teste_muda_o_fingerprint(
    session: Session, workspace: DevWorkspace, artifacts_dir: Path
) -> None:
    set_test_config(session, workspace.id, VALID_TEST_CONFIG)
    primeira = create_task(session, workspace.id, title="x", goal="y")
    antes = plan(session, primeira.id, artifacts_dir=artifacts_dir).fingerprint

    set_test_config(session, workspace.id, {**VALID_TEST_CONFIG, "argv": ["-x", "tests"]})
    segunda = create_task(session, workspace.id, title="x", goal="y")
    depois = plan(session, segunda.id, artifacts_dir=artifacts_dir).fingerprint

    assert antes != depois


def test_test_config_malformado_no_banco_para_o_planejamento(
    session: Session, task: WorkspaceTask, workspace: DevWorkspace, artifacts_dir: Path
) -> None:
    """Revalidação na leitura: uma linha malformada nunca vira um binding incompleto.

    Escreve **direto no modelo**, contornando `set_test_config`, para simular a linha que
    uma versão anterior do schema (ou uma edição por fora) teria deixado.
    """
    workspace.test_config = {"runner_id": "x"}  # faltam sete chaves
    session.flush()

    with pytest.raises(InvalidTestConfig, match="malformado"):
        plan(session, task.id, artifacts_dir=artifacts_dir)


def test_set_test_config_recusa_documento_invalido_na_escrita(
    session: Session, workspace: DevWorkspace
) -> None:
    from app.workspace import InvalidTestConfig as WorkspaceInvalidTestConfig

    with pytest.raises(WorkspaceInvalidTestConfig):
        set_test_config(session, workspace.id, {**VALID_TEST_CONFIG, "network_policy": "disabled"})


def test_set_test_config_none_limpa(session: Session, workspace: DevWorkspace) -> None:
    set_test_config(session, workspace.id, VALID_TEST_CONFIG)
    assert workspace.test_config is not None

    set_test_config(session, workspace.id, None)
    assert workspace.test_config is None


def test_set_test_config_grava_a_forma_normalizada(
    session: Session, workspace: DevWorkspace
) -> None:
    """Espaço nas pontas e ordem de `env_allowlist` normalizam na escrita."""
    set_test_config(
        session,
        workspace.id,
        {**VALID_TEST_CONFIG, "executable": "  pytest  ", "env_allowlist": ["PATH", "HOME"]},
    )

    assert workspace.test_config is not None
    assert workspace.test_config["executable"] == "pytest"
    assert workspace.test_config["env_allowlist"] == ["HOME", "PATH"]


# ------------------------------------------------------------------- aprovação


def test_approve_com_fingerprint_correto(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    result = plan(session, task.id, artifacts_dir=artifacts_dir)

    approved = approve(session, task.id, execution_fingerprint=result.fingerprint)

    assert approved.status is TaskStatus.APPROVED
    assert approved.approved_at is not None
    assert approved.approved_manifest_id == result.manifest.id
    assert approved.approved_fingerprint == result.fingerprint


def test_approve_registra_safety_event_de_aprovacao(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    result = plan(session, task.id, artifacts_dir=artifacts_dir)
    approve(session, task.id, execution_fingerprint=result.fingerprint)

    eventos = session.query(SafetyEvent).filter(SafetyEvent.task_id == task.id).all()

    assert any(e.kind.value == "approval_granted" for e in eventos)


def test_approve_com_fingerprint_divergente_e_409_com_campo(
    session: Session, workspace: DevWorkspace, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    """**GATE de [07]**: "mudar […] comando de teste […] invalida a aprovação e a UI diz
    qual campo mudou"."""
    result = plan(session, task.id, artifacts_dir=artifacts_dir)

    # O usuário leu a task; **depois** alguém configurou o Test Runner.
    set_test_config(session, workspace.id, VALID_TEST_CONFIG)

    with pytest.raises(ApprovalFingerprintMismatch) as exc:
        approve(session, task.id, execution_fingerprint=result.fingerprint)

    assert exc.value.status_code == 409
    assert "test_binding" in exc.value.diverged_fields
    assert exc.value.as_payload()["diverged_fields"] == list(exc.value.diverged_fields)


def test_aprovacao_invalidada_mantem_a_task_aguardando_com_o_fingerprint_novo(
    session: Session, workspace: DevWorkspace, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    """O auto-laço de [02] §4: a task continua em `awaiting_approval`, reaprovável."""
    result = plan(session, task.id, artifacts_dir=artifacts_dir)
    set_test_config(session, workspace.id, VALID_TEST_CONFIG)

    with pytest.raises(ApprovalFingerprintMismatch) as exc:
        approve(session, task.id, execution_fingerprint=result.fingerprint)

    recarregada = get_task(session, task.id)
    assert recarregada.status is TaskStatus.AWAITING_APPROVAL
    assert recarregada.approved_at is None
    assert recarregada.approved_fingerprint == exc.value.expected

    # E reaprovar com o valor novo funciona, **sem replanejar**.
    aprovada = approve(session, task.id, execution_fingerprint=exc.value.expected)
    assert aprovada.status is TaskStatus.APPROVED


def test_invalidacao_registra_safety_event(
    session: Session, workspace: DevWorkspace, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    """[ADR-0008] regra 5: divergência gera `SafetyEvent(approval_invalidated)`."""
    result = plan(session, task.id, artifacts_dir=artifacts_dir)
    set_test_config(session, workspace.id, VALID_TEST_CONFIG)

    with pytest.raises(ApprovalFingerprintMismatch):
        approve(session, task.id, execution_fingerprint=result.fingerprint)

    eventos = session.query(SafetyEvent).filter(SafetyEvent.task_id == task.id).all()
    assert any(e.kind.value == "approval_invalidated" for e in eventos)


def test_approve_de_task_em_draft_e_transicao_invalida(
    session: Session, task: WorkspaceTask
) -> None:
    with pytest.raises(InvalidTransition):
        approve(session, task.id, execution_fingerprint="0" * 64)


# -------------------------------------------------------------- reject e cancel


def test_reject_volta_para_draft_e_limpa_a_aprovacao_candidata(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    plan(session, task.id, artifacts_dir=artifacts_dir)

    rejeitada = reject(session, task.id, note="refazer o plano")

    assert rejeitada.status is TaskStatus.DRAFT
    assert rejeitada.approved_fingerprint is None
    assert rejeitada.approved_fingerprint_parts is None
    assert rejeitada.approved_manifest_id is None
    assert rejeitada.result_summary == "refazer o plano"


def test_reject_preserva_o_manifest(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    """[02] §5: o manifest é linha imutável; rejeitar o plano não apaga o histórico."""
    result = plan(session, task.id, artifacts_dir=artifacts_dir)
    manifest_id = result.manifest.id

    reject(session, task.id)

    from app.db.models import ContextManifest

    assert session.get(ContextManifest, manifest_id) is not None


@pytest.mark.parametrize("planejar", [False, True])
def test_cancel_funciona_em_qualquer_estado_nao_terminal(
    session: Session, task: WorkspaceTask, artifacts_dir: Path, planejar: bool
) -> None:
    if planejar:
        plan(session, task.id, artifacts_dir=artifacts_dir)

    cancelada = cancel(session, task.id, note="desisti")

    assert cancelada.status is TaskStatus.CANCELLED
    assert cancelada.cancel_requested is True
    assert cancelada.finished_at is not None


def test_cancel_nunca_grava_failure_reason(session: Session, task: WorkspaceTask) -> None:
    """[ADR-0008] AUD-011: desistir é decisão humana, não falha do sistema."""
    cancelada = cancel(session, task.id)

    assert cancelada.failure_reason is None


def test_cancel_de_task_terminal_e_recusado(session: Session, task: WorkspaceTask) -> None:
    cancel(session, task.id)

    with pytest.raises(InvalidTransition, match="terminal"):
        cancel(session, task.id)


# --------------------------------------------------- GATE: approved → executing recusada


def test_start_execution_e_recusada_por_capability_nao_provavel(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    """**GATE da sub-etapa 6**: 409 com motivo claro, nunca exceção não tratada."""
    result = plan(session, task.id, artifacts_dir=artifacts_dir)
    approve(session, task.id, execution_fingerprint=result.fingerprint)

    with pytest.raises(TransitionGuardFailed) as exc:
        start_execution(session, task.id)

    assert exc.value.status_code == 409
    assert exc.value.guard == "capability_profile_proven"
    assert "fail closed" in exc.value.message.lower()


def test_recusa_de_entrada_registra_safety_event_de_capability(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    result = plan(session, task.id, artifacts_dir=artifacts_dir)
    approve(session, task.id, execution_fingerprint=result.fingerprint)

    with pytest.raises(TransitionGuardFailed):
        start_execution(session, task.id)

    eventos = session.query(SafetyEvent).filter(SafetyEvent.task_id == task.id).all()
    assert any(e.kind.value == "capability_unenforceable" for e in eventos)


def test_task_permanece_approved_depois_da_recusa(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    """A recusa não muda o estado: a task continua na fila, aprovada."""
    result = plan(session, task.id, artifacts_dir=artifacts_dir)
    approve(session, task.id, execution_fingerprint=result.fingerprint)

    with pytest.raises(TransitionGuardFailed):
        start_execution(session, task.id)

    assert get_task(session, task.id).status is TaskStatus.APPROVED


def test_start_execution_fora_de_approved_e_transicao_invalida(
    session: Session, task: WorkspaceTask
) -> None:
    with pytest.raises(InvalidTransition):
        start_execution(session, task.id)


# --------------------------------------------------------- compare-and-set


def test_compare_and_set_recusa_escrita_sobre_versao_obsoleta(
    session: Session, task: WorkspaceTask
) -> None:
    """[02] §4: "quem commitar primeiro vence; o perdedor vira no-op".

    O segundo `cancel` sobre o **mesmo objeto lido antes** encontra a linha já cancelada.
    Aqui o que barra é a própria máquina de estados (terminal), então o teste força o caso
    de versão: dois `reject` concorrentes a partir do mesmo estado lido.
    """
    from app.orchestrator.execution_manager import _compare_and_set
    from app.orchestrator.state_machine import transition_fields

    versao_lida = task.version

    _compare_and_set(
        session,
        task,
        expected_status=TaskStatus.DRAFT,
        expected_version=versao_lida,
        values=transition_fields(TaskStatus.PLANNING),
    )

    # Segundo escritor, ainda acreditando no estado antigo.
    with pytest.raises(ConcurrentTaskUpdate):
        _compare_and_set(
            session,
            task,
            expected_status=TaskStatus.DRAFT,
            expected_version=versao_lida,
            values=transition_fields(TaskStatus.CANCELLED),
        )


def test_version_incrementa_a_cada_transicao(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    inicial = task.version

    plan(session, task.id, artifacts_dir=artifacts_dir)

    # `draft → planning` e `planning → awaiting_approval`: duas transições.
    assert get_task(session, task.id).version == inicial + 2


# ----------------------------------------------------- GATE: reconcile idempotente


def test_reconcile_move_task_presa_em_planning_para_failed_interrupted(
    session: Session, task: WorkspaceTask
) -> None:
    from app.orchestrator.execution_manager import _compare_and_set
    from app.orchestrator.state_machine import transition_fields

    _compare_and_set(
        session,
        task,
        expected_status=TaskStatus.DRAFT,
        expected_version=task.version,
        values=transition_fields(TaskStatus.PLANNING),
    )

    reconciliadas = reconcile_on_startup(session)

    recarregada = get_task(session, task.id)
    assert reconciliadas == (task.id,)
    assert recarregada.status is TaskStatus.FAILED
    assert recarregada.failure_reason is not None
    assert recarregada.failure_reason.value == "interrupted"


def test_reconcile_e_idempotente(session: Session, task: WorkspaceTask) -> None:
    """**GATE**: rodar duas vezes produz o mesmo estado ([ADR-0008] regra 6)."""
    from app.orchestrator.execution_manager import _compare_and_set
    from app.orchestrator.state_machine import transition_fields

    _compare_and_set(
        session,
        task,
        expected_status=TaskStatus.DRAFT,
        expected_version=task.version,
        values=transition_fields(TaskStatus.PLANNING),
    )

    primeira = reconcile_on_startup(session)
    estado_depois = get_task(session, task.id).status
    versao_depois = get_task(session, task.id).version

    segunda = reconcile_on_startup(session)

    assert primeira == (task.id,)
    assert segunda == (), "a segunda passada não pode reconciliar nada"
    assert get_task(session, task.id).status is estado_depois
    assert get_task(session, task.id).version == versao_depois


def test_reconcile_nao_toca_task_em_draft_ou_terminal(
    session: Session, workspace: DevWorkspace
) -> None:
    rascunho = create_task(session, workspace.id, title="a", goal="a")
    cancelada = create_task(session, workspace.id, title="b", goal="b")
    cancel(session, cancelada.id)

    assert reconcile_on_startup(session) == ()
    assert get_task(session, rascunho.id).status is TaskStatus.DRAFT
    assert get_task(session, cancelada.id).status is TaskStatus.CANCELLED


# -------------------------------------------------------------------- manifest


def test_latest_manifest_segue_o_approved_manifest_id(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    """Replanejar reaponta o campo; o recálculo tem de usar o manifest **daquele** plano."""
    primeiro = plan(session, task.id, artifacts_dir=artifacts_dir)
    reject(session, task.id)
    segundo = plan(session, task.id, candidate_paths=["src/app.py"], artifacts_dir=artifacts_dir)

    vigente = latest_manifest(session, get_task(session, task.id))

    assert vigente is not None
    assert vigente.id == segundo.manifest.id
    assert segundo.manifest.id != primeiro.manifest.id


def test_sem_plano_nao_ha_manifest(session: Session, task: WorkspaceTask) -> None:
    assert latest_manifest(session, task) is None
