"""E8.1 — Developer Model Router + Execution Binding.

Cobre:

* a tabela V1 nas **12** células, com os valores literais (não derivados do código);
* as quatro combinações de saída e a independência tier × effort;
* determinismo, imutabilidade e fail closed;
* o contrato `DeveloperBinding`/`DeveloperBindingResolver` (só o resolver conhece nomes
  concretos; só o tier atravessa para ele);
* o caminho completo decisão → resolver → `developer_binding` → `FingerprintParts` →
  `execution_fingerprint`, pelo `plan` real;
* invalidação de aprovação pelo caminho real (`approve` e guarda de entrada) quando muda
  o modelo, o adaptador, a versão do adaptador ou só o reasoning effort;
* comportamento legado sem resolver (`developer_binding: null`) e produção sem resolver;
* gates de arquitetura do Model Router e do contrato de binding.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path
from types import MappingProxyType

import pytest
from sqlalchemy.orm import Session

from app.db.enums import ComplexityLevel, RiskLevel, TaskStatus
from app.db.models import DevWorkspace, WorkspaceTask
from app.orchestrator import (
    ApprovalFingerprintMismatch,
    TransitionGuardFailed,
    approve,
    create_task,
    get_task,
    model_router,
    plan,
    start_execution,
)
from app.orchestrator.developer_binding import (
    DeveloperBinding,
    DeveloperBindingContractError,
    resolve_developer_binding,
    select_developer_execution,
)
from app.orchestrator.fingerprint import FINGERPRINT_VERSION, FingerprintParts
from app.orchestrator.model_router import (
    DEVELOPER_MODEL_POLICY,
    DeveloperEffort,
    DeveloperModelDecision,
    DeveloperModelTier,
    ModelRoutingContractError,
    route_developer_model,
)
from app.orchestrator.planner import DEVELOPER_REASONING_EFFORT_KEY

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
ORCHESTRATOR = APP_ROOT / "orchestrator"

STANDARD = DeveloperModelTier.STANDARD
STRONG = DeveloperModelTier.STRONG
MEDIUM = DeveloperEffort.MEDIUM
HIGH = DeveloperEffort.HIGH

#: A tabela V1 **literal**, transcrita da especificação da E8.1 — não importada do módulo,
#: para que uma célula alterada no código quebre o teste em vez de ser copiada por ele.
EXPECTED_TABLE: tuple[tuple[str, str, str, str], ...] = (
    ("low", "trivial", "standard", "medium"),
    ("low", "low", "standard", "medium"),
    ("low", "medium", "standard", "medium"),
    ("low", "high", "strong", "medium"),
    ("medium", "trivial", "standard", "medium"),
    ("medium", "low", "standard", "medium"),
    ("medium", "medium", "standard", "high"),
    ("medium", "high", "strong", "medium"),
    ("high", "trivial", "standard", "high"),
    ("high", "low", "standard", "high"),
    ("high", "medium", "strong", "medium"),
    ("high", "high", "strong", "high"),
)

#: As chaves de topo históricas do fingerprint v1 ([02] §7). Nenhuma a mais, nenhuma a menos.
HISTORICAL_TOP_LEVEL_KEYS = frozenset(
    {
        "v",
        "plan_hash",
        "manifest_hash",
        "rendered_context_hash",
        "base_commit",
        "developer_binding",
        "auditor_binding",
        "test_binding",
        "agents",
        "tool_profile_hash",
        "safety_policy_hash",
        "workflow_policy_hash",
        "execution_limits",
    }
)


class FakeResolver:
    """Resolver controlado: o **único** lugar destes testes com nomes concretos."""

    def __init__(
        self,
        *,
        adapter: str = "adapter-x",
        adapter_version: str = "version-v",
        standard_model: str = "model-s",
        strong_model: str = "model-o",
    ) -> None:
        self._adapter = adapter
        self._adapter_version = adapter_version
        self._models = {STANDARD: standard_model, STRONG: strong_model}
        self.calls: list[object] = []

    def resolve(self, tier: DeveloperModelTier) -> DeveloperBinding:
        self.calls.append(tier)
        return DeveloperBinding(
            adapter=self._adapter,
            adapter_version=self._adapter_version,
            model=self._models[tier],
        )


# ===================================================================== Model Router


@pytest.mark.parametrize(("risk", "complexity", "tier", "effort"), EXPECTED_TABLE)
def test_tabela_v1_celula_a_celula(risk: str, complexity: str, tier: str, effort: str) -> None:
    decision = route_developer_model(RiskLevel(risk), ComplexityLevel(complexity))

    assert decision.tier is DeveloperModelTier(tier)
    assert decision.effort is DeveloperEffort(effort)


def test_a_tabela_esperada_cobre_as_12_combinacoes_sem_repetir() -> None:
    pares = {(r, c) for r, c, _, _ in EXPECTED_TABLE}
    assert len(EXPECTED_TABLE) == 12
    assert pares == {(r.value, c.value) for r in RiskLevel for c in ComplexityLevel}


def test_a_politica_do_modulo_tem_exatamente_as_12_celulas() -> None:
    assert set(DEVELOPER_MODEL_POLICY) == {(r, c) for r in RiskLevel for c in ComplexityLevel}
    assert len(DEVELOPER_MODEL_POLICY) == 12


@pytest.mark.parametrize(
    ("risk", "complexity", "tier", "effort"),
    [
        (RiskLevel.LOW, ComplexityLevel.TRIVIAL, STANDARD, MEDIUM),
        (RiskLevel.MEDIUM, ComplexityLevel.MEDIUM, STANDARD, HIGH),
        (RiskLevel.LOW, ComplexityLevel.HIGH, STRONG, MEDIUM),
        (RiskLevel.HIGH, ComplexityLevel.HIGH, STRONG, HIGH),
    ],
    ids=["standard-medium", "standard-high", "strong-medium", "strong-high"],
)
def test_as_quatro_combinacoes_de_saida_existem(
    risk: RiskLevel,
    complexity: ComplexityLevel,
    tier: DeveloperModelTier,
    effort: DeveloperEffort,
) -> None:
    assert route_developer_model(risk, complexity) == DeveloperModelDecision(tier, effort)


def test_tier_e_effort_sao_independentes() -> None:
    """Nenhum dos dois é função do outro: cada tier aparece com os dois efforts e cada
    effort com os dois tiers."""
    saidas = {(d.tier, d.effort) for d in DEVELOPER_MODEL_POLICY.values()}

    assert saidas == {(STANDARD, MEDIUM), (STANDARD, HIGH), (STRONG, MEDIUM), (STRONG, HIGH)}
    for tier in DeveloperModelTier:
        assert {e for t, e in saidas if t is tier} == {MEDIUM, HIGH}
    for effort in DeveloperEffort:
        assert {t for t, e in saidas if e is effort} == {STANDARD, STRONG}


def test_a_decisao_tem_dois_campos_separados_e_nao_e_string() -> None:
    assert [f.name for f in dataclasses.fields(DeveloperModelDecision)] == ["tier", "effort"]

    decision = route_developer_model(RiskLevel.HIGH, ComplexityLevel.HIGH)
    valores: tuple[object, ...] = (decision, decision.tier, decision.effort)
    assert not any(isinstance(valor, str) for valor in valores)
    # Nem o tier carrega o effort, nem o contrário.
    assert "high" not in decision.tier.value
    assert decision.effort.value not in {t.value for t in DeveloperModelTier}


def test_a_politica_da_e8_1_so_produz_medium_e_high() -> None:
    assert {d.effort for d in DEVELOPER_MODEL_POLICY.values()} == {MEDIUM, HIGH}
    assert {e.value for e in DeveloperEffort} == {"medium", "high"}


def test_determinismo_mesma_entrada_mesma_saida() -> None:
    primeira = [route_developer_model(r, c) for r in RiskLevel for c in ComplexityLevel]
    invertida = [
        route_developer_model(r, c)
        for r in reversed(list(RiskLevel))
        for c in reversed(list(ComplexityLevel))
    ]

    assert primeira == list(reversed(invertida))
    for _ in range(3):
        for (r, c), decision in DEVELOPER_MODEL_POLICY.items():
            assert route_developer_model(r, c) is decision


def test_decisao_e_politica_sao_imutaveis() -> None:
    decision = route_developer_model(RiskLevel.LOW, ComplexityLevel.LOW)
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.effort = HIGH  # type: ignore[misc]
    with pytest.raises(TypeError):
        DEVELOPER_MODEL_POLICY[(RiskLevel.LOW, ComplexityLevel.LOW)] = decision  # type: ignore[index]


@pytest.mark.parametrize(
    ("risk", "complexity"),
    [
        ("low", ComplexityLevel.LOW),
        (RiskLevel.LOW, "low"),
        (None, ComplexityLevel.LOW),
        (RiskLevel.LOW, None),
        (ComplexityLevel.LOW, ComplexityLevel.LOW),
        (RiskLevel.LOW, RiskLevel.LOW),
        (1, 1),
    ],
)
def test_entrada_fora_do_contrato_falha_fechado(risk: object, complexity: object) -> None:
    with pytest.raises(ModelRoutingContractError):
        route_developer_model(risk, complexity)  # type: ignore[arg-type]


def test_celula_ausente_falha_fechado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem default implícito: uma célula que sumir da política é erro de contrato."""
    reduzida = dict(DEVELOPER_MODEL_POLICY)
    del reduzida[(RiskLevel.HIGH, ComplexityLevel.HIGH)]
    monkeypatch.setattr(model_router, "DEVELOPER_MODEL_POLICY", MappingProxyType(reduzida))

    with pytest.raises(ModelRoutingContractError):
        route_developer_model(RiskLevel.HIGH, ComplexityLevel.HIGH)


@pytest.mark.parametrize(
    ("tier", "effort"),
    [("standard", MEDIUM), (STANDARD, "medium"), (None, MEDIUM), (STANDARD, None)],
)
def test_decisao_recusa_campo_fora_do_enum(tier: object, effort: object) -> None:
    with pytest.raises(ModelRoutingContractError):
        DeveloperModelDecision(tier, effort)  # type: ignore[arg-type]


# ======================================================================= binding


def test_resolver_mapeia_tier_para_binding_concreto() -> None:
    resolver = FakeResolver()

    standard = resolve_developer_binding(DeveloperModelDecision(STANDARD, HIGH), resolver)
    strong = resolve_developer_binding(DeveloperModelDecision(STRONG, MEDIUM), resolver)

    assert standard.as_canonical() == {
        "adapter": "adapter-x",
        "adapter_version": "version-v",
        "model": "model-s",
    }
    assert strong.as_canonical() == {
        "adapter": "adapter-x",
        "adapter_version": "version-v",
        "model": "model-o",
    }


def test_so_o_tier_atravessa_para_o_resolver() -> None:
    """O resolver nunca vê o effort: não tem como codificá-lo no `model`."""
    resolver = FakeResolver()
    resolve_developer_binding(DeveloperModelDecision(STANDARD, HIGH), resolver)
    resolve_developer_binding(DeveloperModelDecision(STANDARD, MEDIUM), resolver)

    assert resolver.calls == [STANDARD, STANDARD]
    assert all(type(call) is DeveloperModelTier for call in resolver.calls)


def test_mesmo_tier_com_efforts_diferentes_da_o_mesmo_binding() -> None:
    resolver = FakeResolver()
    a = resolve_developer_binding(DeveloperModelDecision(STRONG, MEDIUM), resolver)
    b = resolve_developer_binding(DeveloperModelDecision(STRONG, HIGH), resolver)
    assert a == b
    assert "medium" not in a.model and "high" not in a.model


def test_a_decisao_nao_contem_nomes_concretos() -> None:
    concretos = ("adapter-x", "version-v", "model-s", "model-o")
    for decision in DEVELOPER_MODEL_POLICY.values():
        texto = repr(decision) + repr(dataclasses.asdict(decision))
        assert not any(nome in texto for nome in concretos)


def test_binding_tem_exatamente_a_forma_historica() -> None:
    binding = DeveloperBinding(adapter="a", adapter_version="1", model="m")
    assert list(binding.as_canonical()) == ["adapter", "adapter_version", "model"]
    assert [f.name for f in dataclasses.fields(DeveloperBinding)] == [
        "adapter",
        "adapter_version",
        "model",
    ]
    with pytest.raises(dataclasses.FrozenInstanceError):
        binding.model = "outro"  # type: ignore[misc]


@pytest.mark.parametrize(
    "campos",
    [
        {"adapter": "", "adapter_version": "1", "model": "m"},
        {"adapter": "a", "adapter_version": " 1", "model": "m"},
        {"adapter": "a", "adapter_version": "1", "model": "m\n"},
        {"adapter": "a", "adapter_version": "1", "model": "m\x00x"},
        {"adapter": "a", "adapter_version": "1", "model": "m" * 201},
        {"adapter": None, "adapter_version": "1", "model": "m"},
        {"adapter": "a", "adapter_version": 1, "model": "m"},
    ],
)
def test_binding_invalido_e_recusado(campos: dict[str, object]) -> None:
    with pytest.raises(DeveloperBindingContractError):
        DeveloperBinding(**campos)  # type: ignore[arg-type]


def test_resolver_que_devolve_fora_do_contrato_e_recusado() -> None:
    class DictResolver:
        def resolve(self, tier: DeveloperModelTier) -> DeveloperBinding:
            return {"adapter": "a", "adapter_version": "1", "model": "m"}  # type: ignore[return-value]

    with pytest.raises(DeveloperBindingContractError):
        resolve_developer_binding(DeveloperModelDecision(STANDARD, MEDIUM), DictResolver())


def test_selecao_sem_resolver_nao_inventa_binding() -> None:
    selection = select_developer_execution(RiskLevel.HIGH, ComplexityLevel.HIGH, resolver=None)
    assert selection.binding is None
    assert selection.decision == DeveloperModelDecision(STRONG, HIGH)


def test_selecao_com_resolver_resolve_o_tier_da_tabela() -> None:
    selection = select_developer_execution(
        RiskLevel.LOW, ComplexityLevel.HIGH, resolver=FakeResolver()
    )
    assert selection.decision == DeveloperModelDecision(STRONG, MEDIUM)
    assert selection.binding == DeveloperBinding("adapter-x", "version-v", "model-o")


# ============================================================ fingerprint (puro)


def _parts(*, binding: DeveloperBinding, effort: DeveloperEffort) -> FingerprintParts:
    return FingerprintParts(
        plan_hash="a" * 64,
        manifest_hash="b" * 64,
        rendered_context_hash="c" * 64,
        base_commit="d" * 40,
        developer_binding=binding.as_canonical(),
        auditor_binding=None,
        test_binding={"runner": None, "command_hash": None, "policy_hash": None},
        agents=("developer",),
        tool_profile_hash="e" * 64,
        safety_policy_hash="f" * 64,
        workflow_policy_hash="0" * 64,
        execution_limits={"max_attempts": 2, DEVELOPER_REASONING_EFFORT_KEY: effort.value},
    )


_BASE = DeveloperBinding("adapter-x", "version-v", "model-a")


@pytest.mark.parametrize(
    "outro",
    [
        DeveloperBinding("adapter-x", "version-v", "model-b"),
        DeveloperBinding("adapter-y", "version-v", "model-a"),
        DeveloperBinding("adapter-x", "version-w", "model-a"),
    ],
    ids=["model", "adapter", "adapter_version"],
)
def test_cada_campo_do_binding_muda_o_fingerprint(outro: DeveloperBinding) -> None:
    assert (
        _parts(binding=_BASE, effort=MEDIUM).compute()
        != _parts(binding=outro, effort=MEDIUM).compute()
    )


def test_so_o_effort_muda_o_fingerprint() -> None:
    assert (
        _parts(binding=_BASE, effort=MEDIUM).compute()
        != _parts(binding=_BASE, effort=HIGH).compute()
    )


def test_forma_de_topo_do_fingerprint_v1_preservada() -> None:
    assert FINGERPRINT_VERSION == 1
    canonico = _parts(binding=_BASE, effort=HIGH).as_canonical()
    assert set(canonico) == HISTORICAL_TOP_LEVEL_KEYS
    assert "model_policy_hash" not in canonico
    assert set(canonico["developer_binding"]) == {"adapter", "adapter_version", "model"}


# ===================================================== caminho real: plan/approve/entry


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    return tmp_path / "artifacts"


@pytest.fixture
def task(session: Session, workspace: DevWorkspace) -> WorkspaceTask:
    return create_task(session, workspace.id, title="task", goal="ajustar o util")


def _expected_decision(task: WorkspaceTask) -> DeveloperModelDecision:
    """A célula esperada, pela tabela **literal**."""
    for risk, complexity, tier, effort in EXPECTED_TABLE:
        if (risk, complexity) == (task.risk.value, task.complexity.value):
            return DeveloperModelDecision(DeveloperModelTier(tier), DeveloperEffort(effort))
    raise AssertionError("célula ausente na tabela esperada")


def test_plan_com_resolver_preenche_o_developer_binding(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    """decision → resolver → developer_binding → FingerprintParts → execution_fingerprint."""
    resolver = FakeResolver()
    result = plan(
        session, task.id, artifacts_dir=artifacts_dir, developer_binding_resolver=resolver
    )
    task = get_task(session, task.id)
    expected = _expected_decision(task)
    binding_esperado = FakeResolver().resolve(expected.tier).as_canonical()

    parts = result.fingerprint_parts
    assert parts.developer_binding == binding_esperado
    assert parts.auditor_binding is None
    assert parts.execution_limits[DEVELOPER_REASONING_EFFORT_KEY] == expected.effort.value
    assert parts.compute() == result.fingerprint == task.approved_fingerprint

    persistido = task.approved_fingerprint_parts
    assert persistido is not None
    assert set(persistido) == HISTORICAL_TOP_LEVEL_KEYS
    assert persistido["v"] == 1
    assert persistido["developer_binding"] == binding_esperado
    assert persistido["auditor_binding"] is None
    assert persistido["execution_limits"][DEVELOPER_REASONING_EFFORT_KEY] == expected.effort.value
    assert resolver.calls == [expected.tier]


def test_plan_sem_resolver_mantem_o_developer_binding_null(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    result = plan(session, task.id, artifacts_dir=artifacts_dir)
    task = get_task(session, task.id)

    assert result.fingerprint_parts.developer_binding is None
    assert result.fingerprint_parts.auditor_binding is None
    persistido = task.approved_fingerprint_parts
    assert persistido is not None
    assert persistido["developer_binding"] is None
    assert persistido["auditor_binding"] is None
    # O effort é decisão neutra do Orchestrator e é aprovado mesmo sem binding concreto.
    assert (
        persistido["execution_limits"][DEVELOPER_REASONING_EFFORT_KEY]
        == _expected_decision(task).effort.value
    )


def test_o_documento_plan_nao_muda_com_o_resolver(
    session: Session, workspace: DevWorkspace, artifacts_dir: Path
) -> None:
    """O binding vive só no fingerprint: `plan_hash` é o mesmo com e sem resolver."""
    a = create_task(session, workspace.id, title="task", goal="ajustar o util")
    b = create_task(session, workspace.id, title="task", goal="ajustar o util")

    sem = plan(session, a.id, artifacts_dir=artifacts_dir)
    com = plan(
        session, b.id, artifacts_dir=artifacts_dir, developer_binding_resolver=FakeResolver()
    )

    assert sem.plan_hash == com.plan_hash
    assert sem.fingerprint != com.fingerprint
    assert set(sem.fingerprint_parts.as_canonical()) == set(com.fingerprint_parts.as_canonical())


def test_approve_e_aprovado_com_o_mesmo_resolver(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    resolver = FakeResolver()
    result = plan(
        session, task.id, artifacts_dir=artifacts_dir, developer_binding_resolver=resolver
    )

    aprovada = approve(
        session,
        task.id,
        execution_fingerprint=result.fingerprint,
        developer_binding_resolver=FakeResolver(),
    )

    assert aprovada.status is TaskStatus.APPROVED
    assert aprovada.approved_fingerprint == result.fingerprint


_RESOLVER_CHANGES = {
    "model": {"standard_model": "model-s2", "strong_model": "model-o2"},
    "adapter": {"adapter": "adapter-y"},
    "adapter_version": {"adapter_version": "version-w"},
}


@pytest.mark.parametrize("campo", list(_RESOLVER_CHANGES))
def test_approve_invalida_quando_o_binding_concreto_muda(
    session: Session, task: WorkspaceTask, artifacts_dir: Path, campo: str
) -> None:
    result = plan(
        session, task.id, artifacts_dir=artifacts_dir, developer_binding_resolver=FakeResolver()
    )

    with pytest.raises(ApprovalFingerprintMismatch) as exc:
        approve(
            session,
            task.id,
            execution_fingerprint=result.fingerprint,
            developer_binding_resolver=FakeResolver(**_RESOLVER_CHANGES[campo]),
        )

    assert exc.value.diverged_fields == ("developer_binding",)
    recarregada = get_task(session, task.id)
    assert recarregada.status is TaskStatus.AWAITING_APPROVAL
    assert recarregada.approved_at is None


def test_approve_invalida_quando_um_resolver_passa_a_existir(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    """Planejado sem binding (legado), aprovado com binding: a configuração mudou."""
    result = plan(session, task.id, artifacts_dir=artifacts_dir)

    with pytest.raises(ApprovalFingerprintMismatch) as exc:
        approve(
            session,
            task.id,
            execution_fingerprint=result.fingerprint,
            developer_binding_resolver=FakeResolver(),
        )

    assert exc.value.diverged_fields == ("developer_binding",)


def test_guarda_de_entrada_invalida_a_aprovacao_quando_o_modelo_muda(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    result = plan(
        session, task.id, artifacts_dir=artifacts_dir, developer_binding_resolver=FakeResolver()
    )
    approve(
        session,
        task.id,
        execution_fingerprint=result.fingerprint,
        developer_binding_resolver=FakeResolver(),
    )

    with pytest.raises(ApprovalFingerprintMismatch) as exc:
        start_execution(
            session,
            task.id,
            developer_binding_resolver=FakeResolver(
                standard_model="model-s2", strong_model="model-o2"
            ),
        )

    assert exc.value.diverged_fields == ("developer_binding",)
    recarregada = get_task(session, task.id)
    assert recarregada.status is TaskStatus.AWAITING_APPROVAL
    assert recarregada.approved_at is None
    assert recarregada.approved_fingerprint == exc.value.expected
    assert recarregada.approved_fingerprint != result.fingerprint


def _flip_effort_of_cell(monkeypatch: pytest.MonkeyPatch, task: WorkspaceTask) -> None:
    """Muda **só** o effort da célula da task na política vigente; o tier fica igual."""
    chave = (task.risk, task.complexity)
    atual = DEVELOPER_MODEL_POLICY[chave]
    outro = HIGH if atual.effort is MEDIUM else MEDIUM
    nova = dict(DEVELOPER_MODEL_POLICY)
    nova[chave] = DeveloperModelDecision(atual.tier, outro)
    monkeypatch.setattr(model_router, "DEVELOPER_MODEL_POLICY", MappingProxyType(nova))


def test_guarda_de_entrada_invalida_a_aprovacao_quando_so_o_effort_muda(
    session: Session, task: WorkspaceTask, artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aprovado com um effort; a configuração vigente passa para o outro → divergência."""
    resolver = FakeResolver()
    result = plan(
        session, task.id, artifacts_dir=artifacts_dir, developer_binding_resolver=resolver
    )
    approve(
        session,
        task.id,
        execution_fingerprint=result.fingerprint,
        developer_binding_resolver=resolver,
    )
    task = get_task(session, task.id)
    efetivo_aprovado = result.fingerprint_parts.execution_limits[DEVELOPER_REASONING_EFFORT_KEY]

    _flip_effort_of_cell(monkeypatch, task)

    with pytest.raises(ApprovalFingerprintMismatch) as exc:
        start_execution(session, task.id, developer_binding_resolver=resolver)

    # Só o effort mudou: o binding (mesmo tier) é idêntico.
    assert exc.value.diverged_fields == ("execution_limits",)
    recarregada = get_task(session, task.id)
    assert recarregada.status is TaskStatus.AWAITING_APPROVAL
    assert recarregada.approved_fingerprint_parts is not None
    novos_limites = recarregada.approved_fingerprint_parts["execution_limits"]
    assert novos_limites[DEVELOPER_REASONING_EFFORT_KEY] != efetivo_aprovado
    assert (
        recarregada.approved_fingerprint_parts["developer_binding"]
        == result.fingerprint_parts.developer_binding
    )


def test_approve_invalida_quando_so_o_effort_muda(
    session: Session, task: WorkspaceTask, artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = FakeResolver()
    result = plan(
        session, task.id, artifacts_dir=artifacts_dir, developer_binding_resolver=resolver
    )

    _flip_effort_of_cell(monkeypatch, get_task(session, task.id))

    with pytest.raises(ApprovalFingerprintMismatch) as exc:
        approve(
            session,
            task.id,
            execution_fingerprint=result.fingerprint,
            developer_binding_resolver=resolver,
        )

    assert exc.value.diverged_fields == ("execution_limits",)


def test_effort_tambem_invalida_no_caminho_legado_sem_resolver(
    session: Session, task: WorkspaceTask, artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = plan(session, task.id, artifacts_dir=artifacts_dir)
    _flip_effort_of_cell(monkeypatch, get_task(session, task.id))

    with pytest.raises(ApprovalFingerprintMismatch) as exc:
        approve(session, task.id, execution_fingerprint=result.fingerprint)

    assert exc.value.diverged_fields == ("execution_limits",)


def test_resolver_nao_abre_a_execucao(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    """Com binding aprovado e coerente, a guarda continua fail-closed (sem provador):
    nenhum `NotImplementedError`, task segue `approved`."""
    resolver = FakeResolver()
    result = plan(
        session, task.id, artifacts_dir=artifacts_dir, developer_binding_resolver=resolver
    )
    approve(
        session,
        task.id,
        execution_fingerprint=result.fingerprint,
        developer_binding_resolver=resolver,
    )

    with pytest.raises(TransitionGuardFailed) as exc:
        start_execution(session, task.id, developer_binding_resolver=resolver)

    assert exc.value.guard == "capability_profile_proven"
    assert get_task(session, task.id).status is TaskStatus.APPROVED


def test_legado_sem_resolver_continua_igual_ao_e7(
    session: Session, task: WorkspaceTask, artifacts_dir: Path
) -> None:
    result = plan(session, task.id, artifacts_dir=artifacts_dir)
    aprovada = approve(session, task.id, execution_fingerprint=result.fingerprint)
    assert aprovada.status is TaskStatus.APPROVED

    with pytest.raises(TransitionGuardFailed) as exc:
        start_execution(session, task.id)

    assert exc.value.guard == "capability_profile_proven"
    assert get_task(session, task.id).status is TaskStatus.APPROVED


# ============================================================ produção sem resolver


def test_producao_so_o_composition_root_constroi_binding_e_so_a_rota_injeta_resolver() -> None:
    """E8.2: o resolver concreto existe em produção. Só `developer_wiring.py` constrói
    `DeveloperBinding`; fora do Orchestrator, só a rota de tasks (porta neutra, por
    `app.state`) e o composition root conhecem o resolver."""
    donos_do_resolver = {"tasks.py", "developer_wiring.py", "main.py"}
    for path in APP_ROOT.rglob("*.py"):
        texto = path.read_text(encoding="utf-8")
        tree = ast.parse(texto, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                nome = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if nome == "DeveloperBinding":
                    assert path.name == "developer_wiring.py", f"{path} constrói DeveloperBinding"
        if path.parent != ORCHESTRATOR and path.name not in donos_do_resolver:
            assert "developer_binding_resolver" not in texto, f"{path} injeta resolver"
            assert "DeveloperBindingResolver" not in texto, f"{path} conhece o resolver"


def test_startup_tem_o_resolver_de_developer_mas_nenhum_verificador_positivo(
    client: object,
) -> None:
    from app.developer_wiring import AnthropicDeveloperBindingResolver

    state = client.app.state  # type: ignore[attr-defined]
    assert isinstance(state.developer_binding_resolver, AnthropicDeveloperBindingResolver)
    assert state.capability_verifier is None


# ========================================================================= arquitetura


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_model_router_so_importa_stdlib_pura_e_os_enums() -> None:
    permitido = {"__future__", "collections.abc", "dataclasses", "enum", "types", "app.db.enums"}
    assert _imports(ORCHESTRATOR / "model_router.py") <= permitido


def test_contrato_de_binding_nao_inverte_dependencia() -> None:
    permitido = {
        "__future__",
        "dataclasses",
        "typing",
        "app.db.enums",
        "app.orchestrator.model_router",
    }
    assert _imports(ORCHESTRATOR / "developer_binding.py") <= permitido


@pytest.mark.parametrize("modulo", ["model_router.py", "developer_binding.py"])
def test_router_e_binding_sem_io_rede_banco_ou_provider(modulo: str) -> None:
    proibidos = {
        "anthropic",
        "claude_agent_sdk",
        "claude",
        "os",
        "sys",
        "random",
        "time",
        "datetime",
        "socket",
        "http",
        "urllib",
        "requests",
        "httpx",
        "sqlalchemy",
        "subprocess",
        "pathlib",
        "importlib",
    }
    for imported in _imports(ORCHESTRATOR / modulo):
        raiz = imported.split(".")[0]
        assert raiz not in proibidos, f"{modulo} importa {imported}"
        assert not imported.startswith(
            ("app.api", "app.main", "app.agent_runtime", "app.tool_executor", "app.db.models")
        ), f"{modulo} importa {imported}"
        assert imported not in {"app.db.session", "app.config"}


_VENDOR = re.compile(r"\b(?:claude|anthropic|sonnet|opus|haiku)\b|agent[ _-]?sdk", re.IGNORECASE)


@pytest.mark.parametrize("path", sorted(ORCHESTRATOR.rglob("*.py")), ids=lambda p: p.name)
def test_orchestrator_nao_conhece_fornecedor_nem_modelo_concreto(path: Path) -> None:
    assert not _VENDOR.search(path.read_text(encoding="utf-8")), (
        f"orchestrator/{path.name} nomeia fornecedor/modelo concreto"
    )


def test_tabela_de_modelo_nao_vive_no_resource_router() -> None:
    texto = (ORCHESTRATOR / "resource_router.py").read_text(encoding="utf-8")
    assert "app.orchestrator.model_router" not in _imports(ORCHESTRATOR / "resource_router.py")
    assert "DeveloperEffort" not in texto and "DeveloperModelTier" not in texto
    assert "app.orchestrator.resource_router" not in _imports(ORCHESTRATOR / "model_router.py")


def test_model_policy_hash_nao_existe_no_backend() -> None:
    """Decisão congelada: nenhum identificador, chave ou campo `model_policy_hash`.
    (Docstrings que registram a decisão de não tê-lo continuam permitidas.)"""
    assert "model_policy_hash" not in {f.name for f in dataclasses.fields(FingerprintParts)}
    for path in APP_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            nomes = {
                getattr(node, "id", None),
                getattr(node, "attr", None),
                getattr(node, "arg", None),
                node.value if isinstance(node, ast.Constant) else None,
            }
            assert "model_policy_hash" not in nomes, f"{path}:{getattr(node, 'lineno', '?')}"


def test_um_unico_ponto_monta_o_developer_binding() -> None:
    """Plano, `approve` e guarda de entrada passam pelo mesmo `build_fingerprint_parts`,
    e só ele chama `select_developer_execution`."""
    chamadores = []
    for path in APP_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                nome = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if nome in {"select_developer_execution", "resolve_developer_binding"}:
                    chamadores.append((path.name, nome))
    assert sorted(chamadores) == [
        ("developer_binding.py", "resolve_developer_binding"),
        ("planner.py", "select_developer_execution"),
    ]
