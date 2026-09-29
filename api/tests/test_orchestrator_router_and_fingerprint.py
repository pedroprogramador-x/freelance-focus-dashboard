"""Resource Router ([03] §6), `execution_fingerprint` ([02] §7), `TestPolicy` ([04] §6)
e `safety_policy_hash` composto ([04] §5).

Gates cobertos aqui:

* sub-etapa 2 — hash estável entre chamadas; muda quando a política composta muda;
* sub-etapa 4 — a tabela risco×complexidade nas **9** combinações;
* sub-etapa 5 (parte pura) — `test_binding` tudo-ou-nada, `auditor_binding` `null`
  explícito, e o fingerprint reproduzível.
"""

from __future__ import annotations

import pytest

from app.db.enums import ComplexityLevel, RiskLevel, RiskSource
from app.db.models import DevWorkspace
from app.orchestrator.analyzer import HardRuleOutcome, TaskAnalysis
from app.orchestrator.fingerprint import (
    REQUIRED_TOOL_PROFILE,
    FingerprintParts,
    WorkflowPolicy,
    diverged_fields,
    tool_profile_hash,
)
from app.orchestrator.resource_router import (
    AGENT_ARCHITECT,
    AGENT_DEVELOPER,
    AGENT_RESEARCHER,
    MAX_AGENTS,
    MAX_FIX_ROUNDS,
    route,
)
from app.safety.canonical import canonical_json
from app.safety.policy import SafetyPolicy, SafetyPolicyOverride
from app.safety.test_policy import (
    NULL_TEST_BINDING,
    InvalidTestPolicy,
    parse_test_policy,
)
from app.workspace.policy import effective_policy_hash, resolve_effective_policy


def _analysis(
    risk: RiskLevel,
    complexity: ComplexityLevel,
    *,
    needs_architect: bool = False,
    needs_researcher: bool = False,
) -> TaskAnalysis:
    return TaskAnalysis(
        risk=risk,
        complexity=complexity,
        risk_source=RiskSource.HARD_RULE,
        hard_rule=HardRuleOutcome(risk=risk, complexity=complexity),
        shortcut_applied=False,
        enrichment_unavailable=True,
        needs_architect=needs_architect,
        needs_researcher=needs_researcher,
    )


VALID_TEST_CONFIG = {
    "runner_id": "generic-subprocess-v1",
    "executable": "pytest",
    "argv": ["-q", "tests"],
    "timeout_seconds": 600,
    "env_allowlist": ["PATH", "HOME"],
    "network_policy": "unrestricted",
    "output_limits": {"max_stdout_bytes": 1_048_576, "max_stderr_bytes": 262_144},
    "cwd_mode": "task_worktree",
}


# ------------------------------------------------------- GATE sub-etapa 4: as 9 células


#: As **nove** combinações de [03] §6 com `needs_architect=False`. `trivial` e `low`
#: compartilham a mesma coluna na tabela do documento, então `trivial` é testado à parte.
_TABELA_SEM_SINAL = [
    (RiskLevel.LOW, ComplexityLevel.LOW, (AGENT_DEVELOPER,)),
    (RiskLevel.LOW, ComplexityLevel.MEDIUM, (AGENT_DEVELOPER,)),
    (RiskLevel.LOW, ComplexityLevel.HIGH, (AGENT_DEVELOPER,)),
    (RiskLevel.MEDIUM, ComplexityLevel.LOW, (AGENT_DEVELOPER,)),
    (RiskLevel.MEDIUM, ComplexityLevel.MEDIUM, (AGENT_DEVELOPER,)),
    (RiskLevel.MEDIUM, ComplexityLevel.HIGH, (AGENT_DEVELOPER,)),
    (RiskLevel.HIGH, ComplexityLevel.LOW, (AGENT_DEVELOPER,)),
    (RiskLevel.HIGH, ComplexityLevel.MEDIUM, (AGENT_DEVELOPER,)),
    (RiskLevel.HIGH, ComplexityLevel.HIGH, (AGENT_DEVELOPER, AGENT_ARCHITECT)),
]

#: As mesmas nove, com `needs_architect=True`. Só três células reagem ao sinal.
_TABELA_COM_SINAL = [
    (RiskLevel.LOW, ComplexityLevel.LOW, (AGENT_DEVELOPER,)),
    (RiskLevel.LOW, ComplexityLevel.MEDIUM, (AGENT_DEVELOPER,)),
    (RiskLevel.LOW, ComplexityLevel.HIGH, (AGENT_DEVELOPER,)),
    (RiskLevel.MEDIUM, ComplexityLevel.LOW, (AGENT_DEVELOPER,)),
    (RiskLevel.MEDIUM, ComplexityLevel.MEDIUM, (AGENT_DEVELOPER,)),
    (RiskLevel.MEDIUM, ComplexityLevel.HIGH, (AGENT_DEVELOPER, AGENT_ARCHITECT)),
    (RiskLevel.HIGH, ComplexityLevel.LOW, (AGENT_DEVELOPER,)),
    (RiskLevel.HIGH, ComplexityLevel.MEDIUM, (AGENT_DEVELOPER, AGENT_ARCHITECT)),
    (RiskLevel.HIGH, ComplexityLevel.HIGH, (AGENT_DEVELOPER, AGENT_ARCHITECT)),
]


@pytest.mark.parametrize(("risk", "complexity", "esperado"), _TABELA_SEM_SINAL)
def test_tabela_do_router_sem_sinal_de_architect(
    risk: RiskLevel, complexity: ComplexityLevel, esperado: tuple[str, ...]
) -> None:
    """**GATE**: as 9 combinações risco×complexidade, sem sinal."""
    assert route(_analysis(risk, complexity)).agents == esperado


@pytest.mark.parametrize(("risk", "complexity", "esperado"), _TABELA_COM_SINAL)
def test_tabela_do_router_com_sinal_de_architect(
    risk: RiskLevel, complexity: ComplexityLevel, esperado: tuple[str, ...]
) -> None:
    """**GATE**: as mesmas 9 combinações, agora com `needs_architect=True`."""
    decision = route(_analysis(risk, complexity, needs_architect=True))
    assert decision.agents == esperado


def test_complexidade_trivial_nunca_convoca_architect() -> None:
    """`trivial` compartilha a coluna de `low` na tabela de [03] §6."""
    for risk in RiskLevel:
        decision = route(_analysis(risk, ComplexityLevel.TRIVIAL, needs_architect=True))
        assert decision.agents == (AGENT_DEVELOPER,)


def test_researcher_entra_so_quando_sinalizado() -> None:
    sem = route(_analysis(RiskLevel.LOW, ComplexityLevel.LOW))
    com = route(_analysis(RiskLevel.LOW, ComplexityLevel.LOW, needs_researcher=True))

    assert AGENT_RESEARCHER not in sem.agents
    assert com.agents == (AGENT_DEVELOPER, AGENT_RESEARCHER)


def test_o_pior_caso_nao_excede_max_agents() -> None:
    decision = route(
        _analysis(RiskLevel.HIGH, ComplexityLevel.HIGH, needs_architect=True, needs_researcher=True)
    )

    assert decision.agents == (AGENT_DEVELOPER, AGENT_ARCHITECT, AGENT_RESEARCHER)
    assert len(decision.agents) <= MAX_AGENTS


def test_auditor_nunca_entra_em_agents() -> None:
    """[03] §6: o auditor "não é opcional em operação normal" — não é escolha do Router.

    E na E6 ele nem existe: listá-lo faria `agents`, que entra no fingerprint, afirmar uma
    composição que a execução não teria.
    """
    for risk in RiskLevel:
        for complexity in ComplexityLevel:
            decision = route(
                _analysis(risk, complexity, needs_architect=True, needs_researcher=True)
            )
            assert "auditor" not in decision.agents
            assert "test_runner" not in decision.agents


def test_max_fix_rounds_e_carregado_mas_inerte() -> None:
    """[03] §6 / AUD-013: carregado no limite, sem governar nada antes da E10."""
    decision = route(_analysis(RiskLevel.LOW, ComplexityLevel.LOW))

    assert decision.max_fix_rounds == MAX_FIX_ROUNDS
    assert decision.as_execution_limits()["max_fix_rounds"] == MAX_FIX_ROUNDS


# --------------------------------------------------- GATE sub-etapa 2: safety_policy_hash


def test_policy_hash_e_estavel_entre_chamadas(workspace: DevWorkspace) -> None:
    """**GATE**: mesma política, mesmo hash — quantas vezes forem."""
    primeiro = effective_policy_hash(workspace)
    segundo = effective_policy_hash(workspace)
    terceiro = effective_policy_hash(workspace)

    assert primeiro == segundo == terceiro
    assert len(primeiro) == 64


def test_policy_hash_muda_quando_a_politica_composta_do_workspace_muda(
    workspace: DevWorkspace,
) -> None:
    """**GATE**: um override restritivo produz uma política efetiva diferente.

    Exercita o mecanismo real de [04] §5 (`SafetyPolicy.compose`), que é o que a coluna de
    override vai alimentar quando existir — ver o docstring de `workspace/policy.py`.
    """
    base = effective_policy_hash(workspace)
    override = SafetyPolicyOverride(add_secret_patterns=("**/*.custom-secret",))

    composto = effective_policy_hash(workspace, override=override)

    assert composto != base


def test_composicao_so_restringe(workspace: DevWorkspace) -> None:
    """[04] §5: "um override só restringe, nunca afrouxa"."""
    base = resolve_effective_policy(workspace)
    composto = resolve_effective_policy(
        workspace,
        override=SafetyPolicyOverride(add_secret_patterns=("**/*.extra",), max_path_bytes=120),
    )

    assert set(base.secret_patterns).issubset(set(composto.secret_patterns))
    assert composto.max_path_bytes <= base.max_path_bytes


def test_override_nao_consegue_afrouxar_max_path_bytes(workspace: DevWorkspace) -> None:
    composto = resolve_effective_policy(
        workspace, override=SafetyPolicyOverride(max_path_bytes=99_999)
    )

    assert composto.max_path_bytes == SafetyPolicy().max_path_bytes


# ------------------------------------------------------------------------ TestPolicy


def test_test_config_none_continua_none() -> None:
    """`None` é "sem Test Runner configurado", não um erro ([02] §7)."""
    assert parse_test_policy(None) is None


def test_test_config_valido_produz_binding_com_os_tres_campos() -> None:
    policy = parse_test_policy(VALID_TEST_CONFIG)
    assert policy is not None

    binding = policy.as_binding()

    assert set(binding) == {"runner", "command_hash", "policy_hash"}
    assert all(value is not None for value in binding.values())
    assert binding["runner"] == "generic-subprocess-v1"


def test_binding_nulo_tem_os_tres_campos_none_e_nao_chaves_ausentes() -> None:
    """[02] §7: `null` explícito, nunca chave omitida — senão dois cenários colidem."""
    assert set(NULL_TEST_BINDING) == {"runner", "command_hash", "policy_hash"}
    assert all(value is None for value in NULL_TEST_BINDING.values())


def test_invariante_tudo_ou_nada_do_test_binding() -> None:
    """Os três campos são todos `null` ou todos não-`null`. Nunca um estado misto."""
    configurado = parse_test_policy(VALID_TEST_CONFIG)
    assert configurado is not None

    preenchidos = [v is not None for v in configurado.as_binding().values()]
    vazios = [v is not None for v in NULL_TEST_BINDING.values()]

    assert all(preenchidos)
    assert not any(vazios)


@pytest.mark.parametrize(
    ("mutacao", "esperado"),
    [
        ({"network_policy": "disabled"}, "network_policy"),
        ({"cwd_mode": "/tmp/x"}, "cwd_mode"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"timeout_seconds": "600"}, "timeout_seconds"),
        ({"timeout_seconds": True}, "timeout_seconds"),
        ({"argv": "-q tests"}, "argv"),
        ({"executable": "  "}, "executable"),
        ({"runner_id": ""}, "runner_id"),
    ],
)
def test_test_config_malformado_e_recusado(mutacao: dict[str, object], esperado: str) -> None:
    documento = {**VALID_TEST_CONFIG, **mutacao}

    with pytest.raises(InvalidTestPolicy, match=esperado):
        parse_test_policy(documento)


def test_network_policy_disabled_e_recusada_por_prometer_o_que_nao_cumpre() -> None:
    """[04] §6: a política de rede é **declarada, não imposta**. Ver o docstring do módulo."""
    with pytest.raises(InvalidTestPolicy, match="unrestricted"):
        parse_test_policy({**VALID_TEST_CONFIG, "network_policy": "disabled"})


def test_chave_desconhecida_e_chave_faltando_sao_recusadas() -> None:
    with pytest.raises(InvalidTestPolicy, match="desconhecida"):
        parse_test_policy({**VALID_TEST_CONFIG, "shell": "bash -c"})

    sem_argv = {k: v for k, v in VALID_TEST_CONFIG.items() if k != "argv"}
    with pytest.raises(InvalidTestPolicy, match="argv"):
        parse_test_policy(sem_argv)


def test_dicionario_vazio_e_recusado_porque_provavelmente_queria_null() -> None:
    with pytest.raises(InvalidTestPolicy, match="null"):
        parse_test_policy({})


def test_command_hash_reage_a_argv_e_policy_hash_a_timeout() -> None:
    """Dois hashes, para que a UI diga **qual campo** mudou ([06] §4)."""
    base = parse_test_policy(VALID_TEST_CONFIG)
    outro_argv = parse_test_policy({**VALID_TEST_CONFIG, "argv": ["-x", "tests"]})
    outro_timeout = parse_test_policy({**VALID_TEST_CONFIG, "timeout_seconds": 900})

    assert base and outro_argv and outro_timeout

    assert base.command_hash() != outro_argv.command_hash()
    assert base.command_hash() == outro_timeout.command_hash()
    assert base.policy_hash() != outro_timeout.policy_hash()


def test_ordem_de_argv_e_semantica_mas_env_allowlist_e_conjunto() -> None:
    """[02] §7: `argv` preserva ordem; lista sem semântica de ordem é ordenada."""
    invertido = parse_test_policy({**VALID_TEST_CONFIG, "argv": ["tests", "-q"]})
    env_invertido = parse_test_policy({**VALID_TEST_CONFIG, "env_allowlist": ["HOME", "PATH"]})
    base = parse_test_policy(VALID_TEST_CONFIG)

    assert base and invertido and env_invertido

    assert base.command_hash() != invertido.command_hash()
    assert base.policy_hash() == env_invertido.policy_hash()


# ------------------------------------------------------------------- fingerprint puro


def _parts(**overrides: object) -> FingerprintParts:
    defaults: dict[str, object] = {
        "plan_hash": "a" * 64,
        "manifest_hash": "b" * 64,
        "rendered_context_hash": "c" * 64,
        "base_commit": "d" * 40,
        "developer_binding": None,
        "auditor_binding": None,
        "test_binding": dict(NULL_TEST_BINDING),
        "agents": ("developer",),
        "tool_profile_hash": tool_profile_hash(),
        "safety_policy_hash": "e" * 64,
        "workflow_policy_hash": "f" * 64,
        "execution_limits": {"max_attempts": 2},
    }
    defaults.update(overrides)
    return FingerprintParts(**defaults)  # type: ignore[arg-type]


def test_fingerprint_e_reproduzivel() -> None:
    assert _parts().compute() == _parts().compute()
    assert len(_parts().compute()) == 64


def test_auditor_ausente_serializa_como_null_explicito() -> None:
    """**GATE de [07] para a E6**: "auditor ausente serializa como `null` explícito"."""
    canonical = _parts().as_canonical()

    assert "auditor_binding" in canonical
    assert canonical["auditor_binding"] is None
    assert '"auditor_binding":null' in canonical_json(canonical)


def test_omitir_a_chave_colidiria_com_auditor_presente() -> None:
    """O motivo de [02] §7 exigir `null` explícito, demonstrado.

    Duas situações distintas — "sem auditor" e "com auditor X" — precisam de hashes
    distintos. É a omissão da chave que as faria colidir.
    """
    sem_auditor = _parts(auditor_binding=None).compute()
    com_auditor = _parts(
        auditor_binding={"adapter": "codex-cli", "adapter_version": "1", "model": "m"}
    ).compute()

    assert sem_auditor != com_auditor


def test_agents_preserva_ordem_no_hash() -> None:
    """[02] §7: `agents` é array de ordem semanticamente relevante."""
    a = _parts(agents=("developer", "architect")).compute()
    b = _parts(agents=("architect", "developer")).compute()

    assert a != b


@pytest.mark.parametrize(
    "campo",
    [
        "plan_hash",
        "manifest_hash",
        "rendered_context_hash",
        "base_commit",
        "tool_profile_hash",
        "safety_policy_hash",
        "workflow_policy_hash",
    ],
)
def test_qualquer_campo_coberto_muda_o_fingerprint(campo: str) -> None:
    """[04] §7: "qualquer campo coberto que mude […] invalida a aprovação"."""
    assert _parts().compute() != _parts(**{campo: "9" * 64}).compute()


def test_test_binding_muda_o_fingerprint() -> None:
    policy = parse_test_policy(VALID_TEST_CONFIG)
    assert policy is not None

    sem = _parts().compute()
    com = _parts(test_binding=policy.as_binding()).compute()

    assert sem != com


def test_execution_limits_mudam_o_fingerprint() -> None:
    assert _parts().compute() != _parts(execution_limits={"max_attempts": 3}).compute()


def test_diverged_fields_aponta_exatamente_o_que_mudou() -> None:
    antes = _parts().as_canonical()
    depois = _parts(plan_hash="9" * 64, agents=("developer", "architect")).as_canonical()

    assert diverged_fields(antes, depois) == ("agents", "plan_hash")


def test_diverged_fields_sem_base_devolve_tudo() -> None:
    """Sem `approved_fingerprint_parts` não há base; "nada mudou" seria mentira."""
    atual = _parts().as_canonical()

    assert diverged_fields(None, atual) == tuple(sorted(atual))


def test_tool_profile_hash_e_do_perfil_requerido_e_nunca_do_comprovado() -> None:
    """A correção de terminologia de 2026-09-12, como asserção.

    O perfil requerido é constante e inclui `execute_commands = disabled` ([ADR-0009]).
    Nada aqui tem como produzir um perfil "comprovado" — isso é E7+, por `Run`.
    """
    assert REQUIRED_TOOL_PROFILE["execute_commands"] == "disabled"
    assert REQUIRED_TOOL_PROFILE["read_files"] == "mediated"
    assert tool_profile_hash() == tool_profile_hash(REQUIRED_TOOL_PROFILE)


def test_workflow_policy_hash_reage_a_cada_item() -> None:
    base = WorkflowPolicy().policy_hash()

    assert WorkflowPolicy(audit_skip_docs_only=True).policy_hash() != base
    assert WorkflowPolicy(max_attempts=5).policy_hash() != base
    assert WorkflowPolicy(block_done_on_high_open_finding=False).policy_hash() != base
