"""E7.6 — Capability Enforcement Integration.

Declaração → verificação independente → validação das sete capabilities → `CapabilityProver`
→ guarda `approved → executing` → recusa auditável. O estado **positivo** é demonstrado só
por doubles (`tests/capability_helpers.py`); em produção não há verificador positivo.

Princípios que estes testes travam:

* declaração perfeita, sozinha, nunca autoriza;
* as **sete** capabilities são conferidas por igualdade exata — o hash histórico (que não vê
  `git_read`/`git_write`/`external_paths`) nunca é a única barreira;
* a verificação vale só para o contexto esperado que vem do chamador confiável;
* recusa de política é tipada e sanitizada; falha técnica do verificador não vira política;
* nada de E8: nenhum Run, worktree ou tentativa consumida.
"""

from __future__ import annotations

import ast
import dataclasses
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent_runtime import (
    CapabilityVerifierContractViolation,
    EnforcementEvidence,
    EnforcementMethod,
    NotVerified,
    Verified,
    binding_of,
    observe_declared_capabilities,
)
from app.capability_wiring import VerifyingCapabilityProver
from app.config import AppSettings
from app.db.enums import SafetyEventKind, TaskStatus
from app.db.models import DevWorkspace, Run, SafetyEvent, WorkspaceTask
from app.db.session import session_scope
from app.main import create_app
from app.orchestrator import (
    TransitionGuardFailed,
    approve,
    create_task,
    get_task,
    plan,
    start_execution,
)
from app.orchestrator.fingerprint import REQUIRED_TOOL_PROFILE, tool_profile_hash
from app.orchestrator.state_machine import CapabilityProof, EntryGuardFacts, check_entry_guard
from app.safety import (
    Capability,
    CapabilityBinding,
    CapabilityRefusal,
    CapabilityRefusalCode,
    EnforcementMode,
    InvalidCapabilityVerification,
    ProviderRole,
    UnverifiedReason,
    VerifiedCapabilityObservation,
    evaluate_observation,
    fingerprint_v1_projection,
    historical_profile_hash,
)
from app.safety.canonical import canonical_sha256
from app.safety.capability_profile import (
    AUDITOR_V1_PROFILE,
    DEVELOPER_V1_PROFILE,
    CapabilityProfileRejected,
)
from tests.capability_helpers import (
    CONFIG_HASH,
    FixedProver,
    ScriptedVerifier,
    declaration,
    expected_binding,
    full_proof,
    profile,
)

APP_ROOT = Path(__file__).resolve().parent.parent / "app"

HISTORICAL_PROFILE_HASH = "22995a18da907e13d75cee3bd16a555cd373b33ed802ae5ac476f0e7ab040aa1"
HISTORICAL_PROFILE_JSON_FIELDS = {
    "v": 1,
    "execute_commands": "disabled",
    "read_files": "mediated",
    "write_files": "mediated",
    "network_access": "disabled",
}

OFFICIAL = DEVELOPER_V1_PROFILE


def _facts(**overrides: object) -> EntryGuardFacts:
    base: dict[str, object] = {
        "fingerprint_matches": True,
        "slot_available": True,
        "is_git_repo": True,
        "head": "a" * 40,
        "planning_base_commit": "a" * 40,
        "attempts": 0,
        "max_attempts": 2,
    }
    return EntryGuardFacts(**{**base, **overrides})  # type: ignore[arg-type]


def _prover(verifier: ScriptedVerifier | None, decl: Any = "default") -> VerifyingCapabilityProver:
    chosen = declaration() if decl == "default" else decl
    return VerifyingCapabilityProver(verifier=verifier, declaration=chosen)


def _guard(prover: object, binding: CapabilityBinding | None = None, **facts: object) -> None:
    check_entry_guard(
        _facts(**facts),
        prover=prover,  # type: ignore[arg-type]
        expected_binding=expected_binding() if binding is None else binding,
    )


def _refusal(prover: object, binding: CapabilityBinding | None = None) -> TransitionGuardFailed:
    with pytest.raises(TransitionGuardFailed) as caught:
        _guard(prover, binding)
    return caught.value


def _other_modes(capability: Capability) -> list[EnforcementMode]:
    required = OFFICIAL.mode_of(capability)
    return [mode for mode in EnforcementMode if mode is not required]


# =================================================== E7.6-A — declaração ≠ verificação


def test_1_declaracao_oficial_sem_verificador_nao_autoriza() -> None:
    """Declaração perfeita + nenhum verificador => fail closed, `verifier_absent`."""
    decl = declaration()
    assert decl.effective_capabilities == OFFICIAL  # a declaração é perfeita…

    erro = _refusal(_prover(None, decl))  # …e mesmo assim não autoriza

    assert erro.guard == "capability_profile_proven"
    assert erro.reason_code == "verifier_absent"
    assert erro.status_code == 409


def test_1b_sem_declaracao_tambem_e_fail_closed() -> None:
    erro = _refusal(_prover(ScriptedVerifier(observed_profile=OFFICIAL), decl=None))
    assert erro.reason_code == "verifier_absent"


@pytest.mark.parametrize("reason", list(UnverifiedReason))
def test_2_verificacao_negativa_tipada_recusa(reason: UnverifiedReason) -> None:
    verifier = ScriptedVerifier(observed_profile=None, not_verified=reason)

    erro = _refusal(_prover(verifier))

    assert erro.guard == "capability_profile_proven"
    assert erro.reason_code == "verification_negative"
    assert reason.value in erro.message  # token de vocabulário fechado
    assert len(verifier.calls) == 1


def test_3_declaracao_not_enforceable_nunca_produz_prova_positiva() -> None:
    decl = declaration(
        enforcement_method=EnforcementMethod.NOT_ENFORCEABLE, evidence=EnforcementEvidence()
    )
    verifier = ScriptedVerifier(observed_profile=OFFICIAL)  # até um verificador "positivo"

    proof = _prover(verifier, decl).prove(tool_profile_hash())

    assert proof.proven is False
    assert proof.observation is None
    assert proof.effective_profile_hash is None
    assert proof.refusal == CapabilityRefusal(CapabilityRefusalCode.DECLARATION_NOT_ENFORCEABLE)
    assert verifier.calls == []  # não há controle a observar: nem é consultado
    assert _refusal(_prover(verifier, decl)).reason_code == "declaration_not_enforceable"


@pytest.mark.parametrize("method", list(EnforcementMethod))
def test_m1_a_declaracao_sozinha_nunca_produz_prova_positiva(method: EnforcementMethod) -> None:
    """Contrafactual M1: qualquer método declarado + perfil perfeito + verificador negativo."""
    evidence = (
        EnforcementEvidence()
        if method is EnforcementMethod.NOT_ENFORCEABLE
        else EnforcementEvidence(applied=("x",), probe_result="ok")
    )
    decl = declaration(enforcement_method=method, evidence=evidence)
    verifier = ScriptedVerifier(
        observed_profile=None, not_verified=UnverifiedReason.CONTROLS_NOT_OBSERVED
    )

    proof = _prover(verifier, decl).prove(tool_profile_hash())

    assert proof.proven is False
    assert proof.observation is None


def test_4_declaracao_mentirosa_declara_oficial_observado_incompativel() -> None:
    declared_official = declaration()
    observed = profile(execute_commands=EnforcementMode.MEDIATED)  # o que de fato foi aplicado
    verifier = ScriptedVerifier(observed_profile=observed)

    assert declared_official.effective_capabilities == OFFICIAL
    erro = _refusal(_prover(verifier, declared_official))

    assert erro.guard == "capability_profile_matches_approved"
    assert erro.reason_code == "declaration_observation_mismatch"
    assert "execute_commands" in erro.message
    # …e a mentira não é rescuada pelo hash: nenhum hash efetivo é fabricado.
    assert _prover(verifier, declared_official).prove(tool_profile_hash()).proven is False


def test_4b_declaracao_verdadeira_e_mentirosa_sao_ambas_representaveis() -> None:
    """O double controla DECLARED e OBSERVED separadamente — nada de observed = declared."""
    lie = observe_declared_capabilities(
        ScriptedVerifier(observed_profile=profile(network=EnforcementMode.MEDIATED)), declaration()
    )
    truth = observe_declared_capabilities(
        ScriptedVerifier(observed_profile=OFFICIAL), declaration()
    )

    assert isinstance(lie, CapabilityRefusal)
    assert isinstance(truth, VerifiedCapabilityObservation)


def test_a_porta_de_verificacao_e_distinta_da_declaracao() -> None:
    decl = declaration()
    assert not hasattr(decl, "verify")
    assert not hasattr(ScriptedVerifier(observed_profile=OFFICIAL), "declaration_hash")


# =================================================== E7.6-B — as sete capabilities


@pytest.mark.parametrize(
    ("capability", "mode"),
    [(c, m) for c in Capability for m in _other_modes(c)],
    ids=lambda v: v.value if hasattr(v, "value") else str(v),
)
def test_5_cada_capability_divergindo_individualmente_recusa(
    capability: Capability, mode: EnforcementMode
) -> None:
    """Qualquer modo diferente do oficial — mais permissivo **ou** mais restritivo."""
    observed = dataclasses.replace(OFFICIAL, **{capability.value: mode})
    decl = declaration(effective_capabilities=observed)  # declaração coerente com o observado
    verifier = ScriptedVerifier(observed_profile=observed)

    erro = _refusal(_prover(verifier, decl))

    assert erro.guard == "capability_profile_matches_approved"
    assert erro.reason_code == "profile_mismatch"
    assert capability.value in erro.message
    # A mesma recusa na guarda pura, sem passar pela ponte:
    erro_pura = _refusal(FixedProver(full_proof(observed=observed)))
    assert erro_pura.reason_code == "profile_mismatch"


def _historical_document(p: Any) -> dict[str, Any]:
    """O documento histórico v1 montado à mão — sem passar por `fingerprint_v1_projection`."""
    return {
        "v": 1,
        "execute_commands": p.execute_commands.value,
        "read_files": p.read_files.value,
        "write_files": p.write_files.value,
        "network_access": p.network.value,
    }


@pytest.mark.parametrize(
    ("capability", "mode"),
    [
        (Capability.GIT_READ, EnforcementMode.DISABLED),
        (Capability.GIT_READ, EnforcementMode.UNMEDIATED),
        (Capability.GIT_WRITE, EnforcementMode.FIXED_OPERATIONS_ONLY),
        (Capability.GIT_WRITE, EnforcementMode.UNMEDIATED),
        (Capability.EXTERNAL_PATHS, EnforcementMode.MEDIATED),
        (Capability.EXTERNAL_PATHS, EnforcementMode.UNMEDIATED),
    ],
)
def test_6_7_8_capability_fora_do_hash_historico_e_recusada_com_hash_igual(
    capability: Capability, mode: EnforcementMode
) -> None:
    """git_read / git_write / external_paths: o fingerprint histórico NÃO as enxerga."""
    observed = dataclasses.replace(OFFICIAL, **{capability.value: mode})

    # Pré-condição do cenário: perfis distintos, MESMO hash histórico.
    assert observed != OFFICIAL
    assert canonical_sha256(_historical_document(observed)) == tool_profile_hash()
    assert canonical_sha256(_historical_document(observed)) == HISTORICAL_PROFILE_HASH

    # Uma prova que informa o hash aprovado (correto!) e a observação divergente:
    forged = CapabilityProof(
        proven=True,
        effective_profile_hash=tool_profile_hash(),
        observation=VerifiedCapabilityObservation(binding=expected_binding(), profile=observed),
    )
    erro = _refusal(FixedProver(forged))

    assert erro.reason_code == "profile_mismatch"
    assert capability.value in erro.message
    # A projeção histórica recusa o perfil completo ANTES de produzir hash:
    with pytest.raises(CapabilityProfileRejected):
        fingerprint_v1_projection(observed)
    with pytest.raises(CapabilityProfileRejected):
        historical_profile_hash(observed)
    # E o caminho integrado (declaração + verificador) recusa também:
    decl = declaration(effective_capabilities=observed)
    assert (
        _refusal(_prover(ScriptedVerifier(observed_profile=observed), decl)).reason_code
        == "profile_mismatch"
    )


@pytest.mark.parametrize("capability", list(Capability))
@pytest.mark.parametrize("mode", [m for m in EnforcementMode if m is not EnforcementMode.DISABLED])
def test_9_auditor_com_qualquer_capability_habilitada_e_incompativel(
    capability: Capability, mode: EnforcementMode
) -> None:
    auditor = dataclasses.replace(AUDITOR_V1_PROFILE, **{capability.value: mode})
    binding = expected_binding(role=ProviderRole.AUDITOR)

    refusal = evaluate_observation(
        binding, VerifiedCapabilityObservation(binding=binding, profile=auditor)
    )

    assert refusal is not None
    assert refusal.code is CapabilityRefusalCode.PROFILE_MISMATCH
    assert refusal.details == (capability.value,)


def test_9b_auditor_todo_disabled_e_aceito_pelo_avaliador_sem_projecao_historica() -> None:
    binding = expected_binding(role=ProviderRole.AUDITOR)
    observation = VerifiedCapabilityObservation(binding=binding, profile=AUDITOR_V1_PROFILE)

    assert evaluate_observation(binding, observation) is None
    # Sem projeção/fingerprint alternativos para o Auditor: a E7.6 não inventa uma.
    with pytest.raises(CapabilityProfileRejected):
        historical_profile_hash(AUDITOR_V1_PROFILE)


def test_9c_a_guarda_de_entrada_e_a_do_developer() -> None:
    """Um contexto esperado de Auditor nunca autoriza `approved → executing`."""
    auditor_binding = expected_binding(role=ProviderRole.AUDITOR)
    verifier = ScriptedVerifier(observed_profile=OFFICIAL)

    erro = _refusal(_prover(verifier), auditor_binding)

    assert erro.reason_code == "binding_mismatch"
    assert "role" in erro.message
    assert verifier.calls == []  # barato: recusa antes de chamar o verificador


def test_ordem_perfil_completo_antes_do_binding() -> None:
    """Perfil divergente + binding divergente: o perfil (passo 1) é o reportado."""
    observed = profile(git_write=EnforcementMode.MEDIATED)
    obs = VerifiedCapabilityObservation(
        binding=expected_binding(adapter_id="outro"), profile=observed
    )

    refusal = evaluate_observation(expected_binding(), obs)

    assert refusal is not None and refusal.code is CapabilityRefusalCode.PROFILE_MISMATCH


# =================================================== E7.6-C — binding e anti-replay


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("adapter_id", "adapter-b"),
        ("adapter_version", "2"),
        ("transport", "transport-y"),
        ("model", "model-n"),
        ("execution_config_hash", "2" * 64),
    ],
)
def test_11_a_15_binding_de_outra_configuracao_recusa(campo: str, valor: str) -> None:
    """Verificação feita para (A, v1, X, M, H1) reapresentada para outro contexto."""
    verifier = ScriptedVerifier(observed_profile=OFFICIAL)  # verifica a config original
    esperado = expected_binding(**{campo: valor})  # o chamador confiável espera outra

    erro = _refusal(_prover(verifier), esperado)

    assert erro.guard == "capability_profile_matches_approved"
    assert erro.reason_code == "binding_mismatch"
    assert campo in erro.message


def test_10_role_diferente_na_observacao_recusa() -> None:
    verifier = ScriptedVerifier(
        observed_profile=OFFICIAL, binding_changes={"role": ProviderRole.AUDITOR}
    )

    erro = _refusal(_prover(verifier))

    assert erro.reason_code == "binding_mismatch"
    assert "role" in erro.message


def test_11_a_15_inversos_o_verificador_atesta_outra_config_que_a_esperada() -> None:
    """O mesmo cenário, com a divergência do lado do verificador em vez do esperado."""
    for campo, valor in (
        ("adapter_id", "adapter-b"),
        ("adapter_version", "2"),
        ("transport", "transport-y"),
        ("model", "model-n"),
        ("execution_config_hash", "2" * 64),
    ):
        verifier = ScriptedVerifier(observed_profile=OFFICIAL, binding_changes={campo: valor})
        erro = _refusal(_prover(verifier))
        assert erro.reason_code == "binding_mismatch", campo
        assert campo in erro.message


def test_16_resultado_verificado_reapresentado_em_outro_contexto_preparado() -> None:
    """Verificação válida da declaração A, reapresentada para a declaração B (outra config)."""
    decl_a = declaration()
    decl_b = declaration(execution_config_hash="2" * 64)
    stored = observe_declared_capabilities(ScriptedVerifier(observed_profile=OFFICIAL), decl_a)
    assert isinstance(stored, VerifiedCapabilityObservation)
    antiga: VerifiedCapabilityObservation = stored

    class Replay:
        """Devolve a verificação antiga, ignorando a declaração que recebe."""

        def verify(self, declaration: Any) -> Verified:
            del declaration
            return Verified(antiga)

    # O chamador confiável prepara B e espera o contexto de B:
    esperado_b = expected_binding(decl_b, execution_config_hash="2" * 64)
    prover = VerifyingCapabilityProver(verifier=Replay(), declaration=decl_b)

    erro = _refusal(prover, esperado_b)

    assert erro.reason_code == "binding_mismatch"
    assert "execution_config_hash" in erro.message and "declaration_hash" in erro.message
    # Controle: a verificação original continua válida para o contexto original.
    _guard(
        VerifyingCapabilityProver(verifier=Replay(), declaration=decl_a), expected_binding(decl_a)
    )


def test_16b_mesma_config_mas_outra_declaracao_prepara_outro_declaration_hash() -> None:
    """O `declaration_hash` amarra a observação à declaração concreta, não só aos campos."""
    base = declaration()
    outra = declaration(evidence=EnforcementEvidence(applied=("allowlist:outro",)))
    assert binding_of(base).divergences_from(binding_of(outra)) == ("declaration_hash",)

    verifier = ScriptedVerifier(observed_profile=OFFICIAL)
    erro = _refusal(_prover(verifier, base), expected_binding(outra))

    assert erro.reason_code == "binding_mismatch"
    assert "declaration_hash" in erro.message


def test_o_contexto_esperado_vem_do_chamador_e_nao_da_prova() -> None:
    """A prova pode afirmar qualquer coisa sobre si; só o `expected_binding` decide."""
    esperado = expected_binding(adapter_id="adapter-b")
    # Uma observação que "diz" ser de adapter-b mas foi gerada para adapter-a:
    verifier = ScriptedVerifier(observed_profile=OFFICIAL)
    assert _refusal(_prover(verifier), esperado).reason_code == "binding_mismatch"

    # Sem contexto esperado, não há contra o que comparar: recusa (nem chama o provador).
    prover = FixedProver(full_proof())
    with pytest.raises(TransitionGuardFailed) as caught:
        check_entry_guard(_facts(), prover=prover)
    assert caught.value.reason_code == "expected_context_missing"
    assert caught.value.guard == "capability_profile_proven"
    assert prover.calls == 0


# =================================================== prova legada e hash efetivo


def test_17_prova_legada_proven_true_e_hash_igual_nao_satisfaz() -> None:
    legada = CapabilityProof(proven=True, effective_profile_hash=tool_profile_hash())

    erro = _refusal(FixedProver(legada))

    assert erro.guard == "capability_profile_proven"
    assert erro.reason_code == "verification_missing"


def test_18_effective_profile_hash_ausente_recusa() -> None:
    sem_hash = dataclasses.replace(full_proof(), effective_profile_hash=None)

    erro = _refusal(FixedProver(sem_hash))

    assert erro.guard == "capability_profile_matches_approved"
    assert erro.reason_code == "effective_hash_missing"


def test_19_effective_profile_hash_incompativel_recusa() -> None:
    errado = dataclasses.replace(full_proof(), effective_profile_hash="0" * 64)

    erro = _refusal(FixedProver(errado))

    assert erro.guard == "capability_profile_matches_approved"
    assert erro.reason_code == "effective_hash_mismatch"


def test_proven_false_com_observacao_perfeita_continua_recusado() -> None:
    """A guarda não é um `proven or observation`: `False` bloqueia sempre."""
    negada = dataclasses.replace(full_proof(), proven=False)

    assert _refusal(FixedProver(negada)).guard == "capability_profile_proven"


# =================================================== positivo controlado (só na guarda)


def test_20_caso_positivo_controlado_a_guarda_aceita_a_prova() -> None:
    decl = declaration()
    verifier = ScriptedVerifier(observed_profile=OFFICIAL)
    prover = _prover(verifier, decl)

    _guard(prover, expected_binding(decl))  # não levanta

    assert len(verifier.calls) == 1
    proof = prover.prove(tool_profile_hash())
    assert proof.proven is True
    assert proof.effective_profile_hash == tool_profile_hash()
    assert proof.observation is not None
    assert proof.observation.profile == OFFICIAL
    assert proof.observation.binding == expected_binding(decl)


# =================================================== guardas anteriores poupam o verificador


@pytest.mark.parametrize(
    ("mutacao", "guarda"),
    [
        ({"fingerprint_matches": False}, "fingerprint_still_valid"),
        ({"slot_available": False}, "slot_available"),
        ({"is_git_repo": False}, "workspace_is_git_repo"),
        ({"head": "b" * 40}, "head_matches_planning_base_commit"),
        ({"attempts": 2, "max_attempts": 2}, "attempts_below_max"),
    ],
)
def test_25_guardas_anteriores_bloqueiam_antes_de_consultar_o_verificador(
    mutacao: dict[str, object], guarda: str
) -> None:
    verifier = ScriptedVerifier(observed_profile=OFFICIAL)

    with pytest.raises(TransitionGuardFailed) as caught:
        check_entry_guard(
            _facts(**mutacao), prover=_prover(verifier), expected_binding=expected_binding()
        )

    assert caught.value.guard == guarda
    assert verifier.calls == []


# =================================================== falha técnica ≠ política


def test_excecao_inesperada_do_verificador_nao_vira_recusa_de_politica() -> None:
    verifier = ScriptedVerifier(
        observed_profile=None, raises=RuntimeError("token sk-live-SEGREDO em /home/x")
    )

    with pytest.raises(RuntimeError, match="SEGREDO"):  # propaga intacta: é defeito
        _guard(_prover(verifier))


def test_verificador_que_viola_o_contrato_e_defeito() -> None:
    verifier = ScriptedVerifier(observed_profile=None, returns="verdadeiro")

    with pytest.raises(CapabilityVerifierContractViolation):
        _guard(_prover(verifier))


def test_nao_verified_e_verified_sao_os_unicos_resultados() -> None:
    assert isinstance(NotVerified(UnverifiedReason.PROBE_FAILED).reason, UnverifiedReason)


# =================================================== contratos imutáveis e validados


def test_binding_e_observacao_sao_imutaveis() -> None:
    binding = expected_binding()
    observation = VerifiedCapabilityObservation(binding=binding, profile=OFFICIAL)

    with pytest.raises(dataclasses.FrozenInstanceError):
        binding.model = "outro"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        observation.profile = AUDITOR_V1_PROFILE  # type: ignore[misc]


@pytest.mark.parametrize(
    "changes",
    [
        {"role": "developer"},
        {"adapter_id": ""},
        {"adapter_version": "   "},
        {"transport": 3},
        {"model": "a\x00b"},
        {"execution_config_hash": "abc"},
        {"declaration_hash": "G" * 64},
    ],
)
def test_binding_malformado_nao_e_representavel(changes: dict[str, Any]) -> None:
    with pytest.raises(InvalidCapabilityVerification):
        expected_binding(**changes)


def test_observacao_malformada_nao_e_representavel() -> None:
    with pytest.raises(InvalidCapabilityVerification):
        VerifiedCapabilityObservation(binding=expected_binding(), profile="oficial")  # type: ignore[arg-type]
    with pytest.raises(InvalidCapabilityVerification):
        VerifiedCapabilityObservation(binding="b", profile=OFFICIAL)  # type: ignore[arg-type]


def test_avaliacao_de_algo_que_nao_e_observacao_e_verification_missing() -> None:
    for lixo in (None, "ok", {"profile": OFFICIAL}, OFFICIAL, binding_of(declaration())):
        refusal = evaluate_observation(expected_binding(), lixo)
        assert refusal == CapabilityRefusal(CapabilityRefusalCode.VERIFICATION_MISSING)


def test_recusa_so_aceita_vocabulario_fechado() -> None:
    CapabilityRefusal(CapabilityRefusalCode.PROFILE_MISMATCH, ("git_read",))
    with pytest.raises(InvalidCapabilityVerification):
        CapabilityRefusal(CapabilityRefusalCode.PROFILE_MISMATCH, ("C:\\Users\\x\\.env",))
    with pytest.raises(InvalidCapabilityVerification):
        CapabilityRefusal("profile_mismatch")  # type: ignore[arg-type]


# =================================================== 26. fingerprint histórico intacto


def test_26_fingerprint_historico_preservado_literalmente() -> None:
    assert REQUIRED_TOOL_PROFILE == HISTORICAL_PROFILE_JSON_FIELDS
    assert list(REQUIRED_TOOL_PROFILE) == list(HISTORICAL_PROFILE_JSON_FIELDS)  # ordem e chaves
    assert tool_profile_hash() == HISTORICAL_PROFILE_HASH
    assert (
        canonical_sha256(fingerprint_v1_projection(DEVELOPER_V1_PROFILE)) == HISTORICAL_PROFILE_HASH
    )
    assert historical_profile_hash(DEVELOPER_V1_PROFILE) == HISTORICAL_PROFILE_HASH
    for ausente in ("git_read", "git_write", "external_paths", "network", "model_policy_hash"):
        assert ausente not in REQUIRED_TOOL_PROFILE
    assert "network_access" in REQUIRED_TOOL_PROFILE


# =================================================== 21. produção sem verificador


def test_21_producao_nao_tem_verificador_positivo(temp_settings: AppSettings) -> None:
    app = create_app(temp_settings)
    assert app.state.capability_verifier is None

    proof = VerifyingCapabilityProver(
        verifier=app.state.capability_verifier, declaration=declaration()
    ).prove(tool_profile_hash())
    assert proof.proven is False
    assert proof.refusal == CapabilityRefusal(CapabilityRefusalCode.VERIFIER_ABSENT)


def test_m5_nenhum_verificador_ou_fake_positivo_existe_no_codigo_de_producao() -> None:
    """Nenhum módulo de `app/` implementa `CapabilityVerifier.verify`, constrói `Verified`
    nem uma observação, nem nomeia um fake/always-pass — fora de quem só define os tipos."""
    permitidos_a_definir_tipos = {
        APP_ROOT / "agent_runtime" / "verification.py",
        APP_ROOT / "safety" / "capability_verification.py",
        # E8.2: o verificador REAL do adaptador Messages API — observa o bundle; nunca um fake.
        APP_ROOT / "agent_runtime" / "adapters" / "anthropic_messages" / "verifier.py",
    }
    suspeitos = ("always", "fake", "alwayspass", "stubverifier")
    for path in sorted(APP_ROOT.rglob("*.py")):
        arvore = ast.parse(path.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if isinstance(no, ast.ClassDef | ast.FunctionDef) and no.name != "verify":
                assert not any(s in no.name.lower() for s in suspeitos), (path.name, no.name)
            if path in permitidos_a_definir_tipos:
                continue
            if isinstance(no, ast.FunctionDef) and no.name == "verify":
                anotacoes = " ".join(
                    ast.unparse(a.annotation) for a in no.args.args if a.annotation
                )
                assert "CapabilityDeclaration" not in anotacoes, (
                    f"{path.name} implementa o `verify` de CapabilityVerifier"
                )
            if isinstance(no, ast.Call):
                alvo = no.func.id if isinstance(no.func, ast.Name) else getattr(no.func, "attr", "")
                assert alvo not in {"Verified", "VerifiedCapabilityObservation"}, (
                    f"{path.name} constrói `{alvo}` em código de produção"
                )


def test_main_nao_importa_a_ponte_nem_agent_runtime() -> None:
    imports: set[str] = set()
    for no in ast.walk(ast.parse((APP_ROOT / "main.py").read_text(encoding="utf-8"))):
        if isinstance(no, ast.ImportFrom) and no.module:
            imports.add(no.module)
        elif isinstance(no, ast.Import):
            imports.update(a.name for a in no.names)
    assert not any(i.startswith(("app.agent_runtime", "app.capability_wiring")) for i in imports)


# =================================================== 27. fronteiras de importação


def _imports_de(path: Path) -> set[str]:
    found: set[str] = set()
    for no in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(no, ast.Import):
            found.update(a.name for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module and no.level == 0:
            found.add(no.module)
    return found


def test_27_fronteiras_do_contrato_novo() -> None:
    safety = _imports_de(APP_ROOT / "safety" / "capability_verification.py")
    assert all(
        i.split(".")[0] in {"__future__", "re", "dataclasses", "enum", "app"} for i in safety
    )
    assert all(i.startswith("app.safety") for i in safety if i.startswith("app"))

    porta = _imports_de(APP_ROOT / "agent_runtime" / "verification.py")
    assert all(
        i.startswith(("app.agent_runtime", "app.safety"))
        or i in {"__future__", "dataclasses", "typing"}
        for i in porta
    )

    ponte = _imports_de(APP_ROOT / "capability_wiring.py")
    for i in ponte:
        assert i == "__future__" or i.startswith(
            ("app.agent_runtime", "app.orchestrator.state_machine", "app.safety")
        ), i
    for proibido in (
        "sqlalchemy",
        "app.db",
        "app.api",
        "app.tool_executor",
        "subprocess",
        "socket",
    ):
        assert not any(i == proibido or i.startswith(proibido + ".") for i in ponte)


def test_27b_orchestrator_nao_conhece_provider_concreto() -> None:
    for path in (APP_ROOT / "orchestrator").rglob("*.py"):
        texto = path.read_text(encoding="utf-8").lower()
        for nome in ("claude", "codex", "anthropic", "openai"):
            assert not re.search(rf'["\']{nome}', texto), f"{path.name} nomeia `{nome}`"
        assert not any(
            i.startswith(("app.agent_runtime", "app.capability_wiring")) for i in _imports_de(path)
        )


# =================================================== integração com banco (E7.6-D)


VALID_SECRET_LIKE = "secret-token-ABC123XYZ"


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    return tmp_path / "artifacts"


@pytest.fixture
def approved(session: Session, workspace: DevWorkspace, artifacts_dir: Path) -> WorkspaceTask:
    task = create_task(session, workspace.id, title="task", goal="ajustar o util")
    planned = plan(session, task.id, artifacts_dir=artifacts_dir)
    approve(session, task.id, execution_fingerprint=planned.fingerprint)
    session.commit()
    return get_task(session, task.id)


def _capability_events(factory: sessionmaker[Session], task_id: str) -> list[SafetyEvent]:
    with factory() as fresh:
        rows = fresh.scalars(select(SafetyEvent).where(SafetyEvent.task_id == task_id)).all()
        fresh.expunge_all()
        return [e for e in rows if e.kind.value.startswith("capability_")]


def _run_count(factory: sessionmaker[Session]) -> int:
    with factory() as fresh:
        return int(fresh.scalar(select(func.count()).select_from(Run)) or 0)


def _worktrees(repo: Path) -> list[str]:
    out = subprocess.run(  # noqa: S603 — git de teste, argv literal, sem shell
        ["git", "worktree", "list", "--porcelain"],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line.startswith("worktree ")]


@pytest.mark.parametrize(
    ("verifier_factory", "binding_changes", "evento", "guarda"),
    [
        (lambda: None, {}, SafetyEventKind.CAPABILITY_UNENFORCEABLE, "capability_profile_proven"),
        (
            lambda: ScriptedVerifier(
                observed_profile=None, not_verified=UnverifiedReason.PROBE_FAILED
            ),
            {},
            SafetyEventKind.CAPABILITY_UNENFORCEABLE,
            "capability_profile_proven",
        ),
        (
            lambda: ScriptedVerifier(observed_profile=profile(git_read=EnforcementMode.UNMEDIATED)),
            {},
            SafetyEventKind.CAPABILITY_DENIED,
            "capability_profile_matches_approved",
        ),
        (
            lambda: ScriptedVerifier(observed_profile=OFFICIAL),
            {"adapter_id": VALID_SECRET_LIKE},
            SafetyEventKind.CAPABILITY_DENIED,
            "capability_profile_matches_approved",
        ),
    ],
    ids=["verifier-ausente", "verificacao-negativa", "perfil-divergente", "binding-divergente"],
)
def test_22_a_24_recusa_e_auditavel_duravel_e_nao_inicia_execucao(
    approved: WorkspaceTask,
    session_factory: sessionmaker[Session],
    repo_path: Path,
    verifier_factory: Any,
    binding_changes: dict[str, Any],
    evento: SafetyEventKind,
    guarda: str,
) -> None:
    attempts_antes = approved.attempts
    worktrees_antes = _worktrees(repo_path)
    verifier = verifier_factory()
    decl = declaration(effective_capabilities=OFFICIAL)
    prover = VerifyingCapabilityProver(verifier=verifier, declaration=decl)
    esperado = expected_binding(decl, **binding_changes)

    # A sessão do chamador faz rollback ao ver a exceção — como a camada HTTP.
    with pytest.raises(TransitionGuardFailed) as caught, session_scope(session_factory) as ativa:
        start_execution(ativa, approved.id, prover=prover, expected_capability_binding=esperado)

    erro = caught.value
    assert erro.status_code == 409
    assert erro.guard == guarda

    # 22. SafetyEvent durável — lido por sessão NOVA, depois do rollback do chamador.
    eventos = _capability_events(session_factory, approved.id)
    assert [e.kind for e in eventos] == [evento]
    assert eventos[0].rule_id == f"orchestrator.entry_guard.{guarda}"
    assert eventos[0].decision.value == "deny"

    # 23. diagnóstico sanitizado: só texto fixo + códigos fechados.
    for texto in (erro.message, eventos[0].detail or "", eventos[0].subject, str(erro)):
        assert VALID_SECRET_LIKE not in texto
        assert "\\" not in texto and "/home" not in texto and "Traceback" not in texto
        assert "object at 0x" not in texto
    assert erro.reason_code is not None and erro.reason_code in (eventos[0].detail or "")

    # 24. nenhuma execução: task approved, tentativa não consumida, sem Run nem worktree.
    with session_factory() as fresh:
        depois = fresh.get(WorkspaceTask, approved.id)
        assert depois is not None
        assert depois.status is TaskStatus.APPROVED
        assert depois.attempts == attempts_antes
        assert depois.approved_at is not None
    assert _run_count(session_factory) == 0
    assert _worktrees(repo_path) == worktrees_antes


def test_producao_start_execution_sem_prover_recusa_com_evento_duravel(
    approved: WorkspaceTask, session_factory: sessionmaker[Session]
) -> None:
    with pytest.raises(TransitionGuardFailed) as caught, session_scope(session_factory) as ativa:
        start_execution(ativa, approved.id)  # exatamente o que a aplicação real pode fazer

    assert caught.value.guard == "capability_profile_proven"
    assert caught.value.reason_code == "verifier_absent"
    assert [e.kind for e in _capability_events(session_factory, approved.id)] == [
        SafetyEventKind.CAPABILITY_UNENFORCEABLE
    ]


def test_prover_sem_contexto_esperado_do_chamador_tambem_recusa(
    approved: WorkspaceTask, session_factory: sessionmaker[Session]
) -> None:
    verifier = ScriptedVerifier(observed_profile=OFFICIAL)
    prover = VerifyingCapabilityProver(verifier=verifier, declaration=declaration())

    with pytest.raises(TransitionGuardFailed) as caught, session_scope(session_factory) as ativa:
        start_execution(ativa, approved.id, prover=prover)

    assert caught.value.reason_code == "expected_context_missing"
    assert verifier.calls == []


def test_falha_tecnica_do_verificador_nao_gera_safety_event_de_politica(
    approved: WorkspaceTask, session_factory: sessionmaker[Session]
) -> None:
    verifier = ScriptedVerifier(observed_profile=None, raises=RuntimeError("quebrou"))
    decl = declaration()

    with pytest.raises(RuntimeError, match="quebrou"), session_scope(session_factory) as ativa:
        start_execution(
            ativa,
            approved.id,
            prover=VerifyingCapabilityProver(verifier=verifier, declaration=decl),
            expected_capability_binding=expected_binding(decl),
        )

    assert _capability_events(session_factory, approved.id) == []
    with session_factory() as fresh:
        assert fresh.get(WorkspaceTask, approved.id).status is TaskStatus.APPROVED  # type: ignore[union-attr]


def test_19b_guarda_anterior_bloqueia_antes_do_verificador_no_fluxo_real(
    approved: WorkspaceTask, session_factory: sessionmaker[Session], repo_path: Path
) -> None:
    from tests import context_helpers

    context_helpers.write(repo_path, "next.txt", "commit posterior\n")
    context_helpers.commit_all(repo_path, "depois da aprovação")
    verifier = ScriptedVerifier(observed_profile=OFFICIAL)
    decl = declaration()

    with pytest.raises(TransitionGuardFailed) as caught, session_scope(session_factory) as ativa:
        start_execution(
            ativa,
            approved.id,
            prover=VerifyingCapabilityProver(verifier=verifier, declaration=decl),
            expected_capability_binding=expected_binding(decl),
        )

    assert caught.value.guard == "head_matches_planning_base_commit"
    assert verifier.calls == []


def test_guarda_passa_mas_start_execution_continua_bloqueado_sem_efeitos(
    approved: WorkspaceTask, session_factory: sessionmaker[Session], repo_path: Path
) -> None:
    """O positivo é demonstrado na guarda; a continuação real (E8) segue bloqueada."""
    verifier = ScriptedVerifier(observed_profile=OFFICIAL)
    decl = declaration()
    worktrees_antes = _worktrees(repo_path)

    with pytest.raises(NotImplementedError, match="E8"), session_scope(session_factory) as ativa:
        start_execution(
            ativa,
            approved.id,
            prover=VerifyingCapabilityProver(verifier=verifier, declaration=decl),
            expected_capability_binding=expected_binding(decl),
        )

    assert len(verifier.calls) == 1  # a guarda de fato passou pela verificação
    with session_factory() as fresh:
        depois = fresh.get(WorkspaceTask, approved.id)
        assert depois is not None
        assert depois.status is TaskStatus.APPROVED  # nunca fica em `executing` sem execução
        assert depois.phase is None
        assert depois.attempts == approved.attempts
    assert _run_count(session_factory) == 0
    assert _worktrees(repo_path) == worktrees_antes
    assert _capability_events(session_factory, approved.id) == []


def test_nenhuma_aresta_approved_para_failed_foi_adicionada() -> None:
    """Divergência documental conhecida: [04] cita `failed(capability_unenforceable)`, a
    máquina (ADR-0008) mantém `approved`. A E7.6 preserva a máquina."""
    from app.orchestrator.state_machine import TRANSITIONS

    assert TaskStatus.FAILED not in TRANSITIONS[TaskStatus.APPROVED]


def test_constantes_de_configuracao_usadas_pelos_helpers_sao_consistentes() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", CONFIG_HASH)
