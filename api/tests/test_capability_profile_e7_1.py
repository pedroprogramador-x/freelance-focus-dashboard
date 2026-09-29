"""E7.1 — `ProviderCapabilityProfile` ([04] §1, [05] §3, [ADR-0009]).

Este arquivo tem duas responsabilidades separadas:

* o **contrato novo** (`app.safety.capability_profile`): vocabulário, validação de forma e
  política de aceitação da V1;
* a **compatibilidade histórica**: o documento e o hash do perfil requerido da E6 e o
  `execution_fingerprint` v1 não podem mudar (decisão 1 da E7.1, sem `model_policy_hash`).

Os vetores `HISTORICAL_*` abaixo são **literais**, capturados de `main` em `0fe357e`
(merge do PR #1) **antes** de qualquer edição da E7.1. Não são recalculados por este
arquivo: um teste que comparasse duas chamadas da mesma implementação passaria mesmo que o
formato mudasse. Se um deles falhar, o formato histórico mudou — não atualize o literal,
investigue.

Nada aqui usa provider: são fakes de dado puro. Isto **não** prova enforcement real — a
prova efetiva é do adaptador (E8) e de testes reais no Windows (E7.3+).
"""

from __future__ import annotations

import ast
import dataclasses
import itertools
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.orchestrator import fingerprint as fingerprint_module
from app.orchestrator.fingerprint import (
    FINGERPRINT_VERSION,
    REQUIRED_TOOL_PROFILE,
    TOOL_PROFILE_VERSION,
    FingerprintParts,
    tool_profile_hash,
)
from app.safety import capability_profile as cp
from app.safety.canonical import canonical_json, canonical_sha256
from app.safety.capability_profile import (
    AUDITOR_V1_PROFILE,
    DEVELOPER_V1_PROFILE,
    V1_PROFILES,
    Capability,
    CapabilityProfileRejected,
    EnforcementMode,
    InvalidCapabilityProfile,
    ProviderCapabilityProfile,
    ProviderRole,
    check_v1,
    fingerprint_v1_projection,
    parse_capability_profile,
    require_v1,
)

# ------------------------------------------------- vetores capturados em 0fe357e (literais)

HISTORICAL_PROFILE_JSON = (
    '{"execute_commands":"disabled","network_access":"disabled",'
    '"read_files":"mediated","v":1,"write_files":"mediated"}'
)
HISTORICAL_PROFILE_HASH = "22995a18da907e13d75cee3bd16a555cd373b33ed802ae5ac476f0e7ab040aa1"

HISTORICAL_FINGERPRINT_JSON = (
    '{"agents":["developer"],"auditor_binding":null,'
    '"base_commit":"dddddddddddddddddddddddddddddddddddddddd",'
    '"developer_binding":null,"execution_limits":{"max_attempts":1},'
    '"manifest_hash":"' + "b" * 64 + '","plan_hash":"' + "a" * 64 + '",'
    '"rendered_context_hash":"' + "c" * 64 + '","safety_policy_hash":"' + "e" * 64 + '",'
    '"test_binding":{"command_hash":null,"policy_hash":null,"runner":null},'
    '"tool_profile_hash":"' + HISTORICAL_PROFILE_HASH + '","v":1,'
    '"workflow_policy_hash":"' + "f" * 64 + '"}'
)
HISTORICAL_FINGERPRINT_HASH = "ac3adc91532a58246919b7e6692a34b439d3a750aa271025e06bb515c927731c"

ALL_CAPABILITIES = list(Capability)
ALL_MODES = list(EnforcementMode)


def _parts() -> FingerprintParts:
    return FingerprintParts(
        plan_hash="a" * 64,
        manifest_hash="b" * 64,
        rendered_context_hash="c" * 64,
        base_commit="d" * 40,
        developer_binding=None,
        auditor_binding=None,
        test_binding={"runner": None, "command_hash": None, "policy_hash": None},
        agents=("developer",),
        tool_profile_hash=tool_profile_hash(),
        safety_policy_hash="e" * 64,
        workflow_policy_hash="f" * 64,
        execution_limits={"max_attempts": 1},
    )


def _developer_with(**changes: EnforcementMode) -> ProviderCapabilityProfile:
    return dataclasses.replace(DEVELOPER_V1_PROFILE, **changes)


def _auditor_with(**changes: EnforcementMode) -> ProviderCapabilityProfile:
    return dataclasses.replace(AUDITOR_V1_PROFILE, **changes)


# ============================================================ 1. as sete capabilities


def test_o_contrato_tem_exatamente_as_sete_capabilities_e_os_quatro_modos_documentados() -> None:
    """Os nomes vêm literalmente de [04] §1; inventar ou omitir um é quebra de contrato."""
    assert [c.value for c in Capability] == [
        "read_files",
        "write_files",
        "execute_commands",
        "git_read",
        "git_write",
        "network",
        "external_paths",
    ]
    assert {m.value for m in EnforcementMode} == {
        "disabled",
        "mediated",
        "fixed_operations_only",
        "unmediated",
    }
    assert [f.name for f in dataclasses.fields(ProviderCapabilityProfile)] == [
        c.value for c in Capability
    ]


def test_o_perfil_oficial_do_developer_e_a_transcricao_da_tabela() -> None:
    assert DEVELOPER_V1_PROFILE.as_document() == {
        "read_files": "mediated",
        "write_files": "mediated",
        "execute_commands": "disabled",
        "git_read": "fixed_operations_only",
        "git_write": "disabled",
        "network": "disabled",
        "external_paths": "disabled",
    }


def test_o_perfil_oficial_do_auditor_e_tudo_desligado() -> None:
    assert AUDITOR_V1_PROFILE.as_document() == {c.value: "disabled" for c in Capability}


def test_os_perfis_oficiais_sao_aceitos_pelo_proprio_papel() -> None:
    assert require_v1(DEVELOPER_V1_PROFILE, ProviderRole.DEVELOPER) is DEVELOPER_V1_PROFILE
    assert require_v1(AUDITOR_V1_PROFILE, ProviderRole.AUDITOR) is AUDITOR_V1_PROFILE
    assert check_v1(DEVELOPER_V1_PROFILE, ProviderRole.DEVELOPER) == ()
    assert check_v1(AUDITOR_V1_PROFILE, ProviderRole.AUDITOR) == ()


def test_o_registro_de_perfis_e_somente_leitura_e_fechado() -> None:
    assert set(V1_PROFILES) == {ProviderRole.DEVELOPER, ProviderRole.AUDITOR}
    with pytest.raises(TypeError):
        V1_PROFILES[ProviderRole.DEVELOPER] = AUDITOR_V1_PROFILE  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEVELOPER_V1_PROFILE.execute_commands = EnforcementMode.MEDIATED  # type: ignore[misc]


@pytest.mark.parametrize("capability", ALL_CAPABILITIES, ids=lambda c: c.value)
@pytest.mark.parametrize("role", list(ProviderRole), ids=lambda r: r.value)
def test_cada_capability_e_conferida_individualmente_pela_politica(
    capability: Capability, role: ProviderRole
) -> None:
    """Para cada capability e cada papel: qualquer modo diferente do oficial é violação
    **daquela** capability, e só dela. Nenhuma das sete é ignorada pela política."""
    official = V1_PROFILES[role]
    for mode in ALL_MODES:
        candidate = dataclasses.replace(official, **{capability.value: mode})
        violations = check_v1(candidate, role)

        if mode is official.mode_of(capability):
            assert violations == ()
            continue

        assert [v.capability for v in violations] == [capability]
        assert violations[0].declared is mode
        assert violations[0].required is official.mode_of(capability)
        with pytest.raises(CapabilityProfileRejected) as raised:
            require_v1(candidate, role)
        assert raised.value.violations == violations
        assert raised.value.role is role
        assert capability.value in str(raised.value)


def test_um_perfil_de_um_papel_nao_e_aceito_como_o_do_outro() -> None:
    assert check_v1(DEVELOPER_V1_PROFILE, ProviderRole.AUDITOR)
    assert check_v1(AUDITOR_V1_PROFILE, ProviderRole.DEVELOPER)


# ===================================================== 2. campos ausentes/desconhecidos/inválidos


def test_parse_roundtrip_do_documento_oficial() -> None:
    assert parse_capability_profile(DEVELOPER_V1_PROFILE.as_document()) == DEVELOPER_V1_PROFILE
    assert parse_capability_profile(AUDITOR_V1_PROFILE.as_document()) == AUDITOR_V1_PROFILE


@pytest.mark.parametrize("capability", ALL_CAPABILITIES, ids=lambda c: c.value)
def test_campo_ausente_e_recusado_e_nomeado(capability: Capability) -> None:
    document = DEVELOPER_V1_PROFILE.as_document()
    del document[capability.value]

    with pytest.raises(InvalidCapabilityProfile, match=capability.value):
        parse_capability_profile(document)


def test_varios_campos_ausentes_sao_todos_reportados() -> None:
    with pytest.raises(InvalidCapabilityProfile, match=r"git_write.*network"):
        parse_capability_profile({"read_files": "mediated"})


def test_documento_vazio_e_recusado() -> None:
    with pytest.raises(InvalidCapabilityProfile, match="ausente"):
        parse_capability_profile({})


@pytest.mark.parametrize(
    "extra",
    ["network_access", "shell", "Read_Files", "v", "model_policy_hash", ""],
)
def test_campo_desconhecido_e_recusado(extra: str) -> None:
    """Inclui `network_access` (nome do documento histórico) e `v`: o contrato completo
    não os aceita — a projeção v1 é uma saída, nunca uma entrada."""
    document: dict[str, Any] = DEVELOPER_V1_PROFILE.as_document()
    document[extra] = "disabled"

    with pytest.raises(InvalidCapabilityProfile, match="desconhecida"):
        parse_capability_profile(document)


def test_chave_nao_string_e_recusada() -> None:
    document: dict[Any, Any] = DEVELOPER_V1_PROFILE.as_document()
    document[1] = "disabled"

    with pytest.raises(InvalidCapabilityProfile, match="chaves string"):
        parse_capability_profile(document)


@pytest.mark.parametrize(
    "bad",
    [None, True, False, 0, 1, 1.5, "", " disabled", "disabled ", "Disabled", "DISABLED",
     "enabled", "allowed", "mediated\n", ["disabled"], {"mode": "disabled"}],
)  # fmt: skip
def test_valor_de_modo_invalido_e_recusado_sem_coercao(bad: Any) -> None:
    document: dict[str, Any] = DEVELOPER_V1_PROFILE.as_document()
    document["execute_commands"] = bad

    with pytest.raises(InvalidCapabilityProfile, match="execute_commands"):
        parse_capability_profile(document)


@pytest.mark.parametrize("bad", [None, [], "disabled", 7, [("read_files", "mediated")]])
def test_documento_que_nao_e_objeto_e_recusado(bad: Any) -> None:
    with pytest.raises(InvalidCapabilityProfile, match="objeto"):
        parse_capability_profile(bad)


def test_construcao_direta_exige_enforcement_mode_e_nao_string() -> None:
    """Sem isto, um perfil com `"disabled"` (str) passaria pelo `==` do modo por acidente
    de `str, Enum` num lugar e falharia em outro."""
    kwargs: dict[str, Any] = {c.value: EnforcementMode.DISABLED for c in Capability}
    kwargs["execute_commands"] = "disabled"

    with pytest.raises(InvalidCapabilityProfile, match="execute_commands"):
        ProviderCapabilityProfile(**kwargs)


def test_construcao_direta_sem_campo_e_erro_nao_default_silencioso() -> None:
    kwargs: dict[str, Any] = {c.value: EnforcementMode.DISABLED for c in Capability}
    del kwargs["git_write"]

    with pytest.raises(TypeError):
        ProviderCapabilityProfile(**kwargs)


@pytest.mark.parametrize("bad", [None, {}, DEVELOPER_V1_PROFILE.as_document(), "developer"])
def test_a_politica_so_aceita_o_tipo_do_contrato(bad: Any) -> None:
    """Um dict com os valores certos **não** é um perfil: passa por `parse_capability_profile`."""
    with pytest.raises(InvalidCapabilityProfile, match="ProviderCapabilityProfile"):
        check_v1(bad, ProviderRole.DEVELOPER)
    with pytest.raises(InvalidCapabilityProfile, match="ProviderCapabilityProfile"):
        require_v1(bad, ProviderRole.DEVELOPER)
    with pytest.raises(InvalidCapabilityProfile, match="ProviderCapabilityProfile"):
        fingerprint_v1_projection(bad)


# =================================================== 3. incompatibilidade com a política da V1


def test_unmediated_e_representavel_para_diagnostico_mas_nunca_aceito() -> None:
    """Decisão da E7.1: representar ≠ aceitar. O adaptador que declara `unmediated` precisa
    poder ser descrito num diagnóstico, e a execução continua recusada."""
    for capability in ALL_CAPABILITIES:
        profile = _developer_with(**{capability.value: EnforcementMode.UNMEDIATED})

        assert profile.mode_of(capability) is EnforcementMode.UNMEDIATED
        assert profile.as_document()[capability.value] == "unmediated"
        assert parse_capability_profile(profile.as_document()) == profile  # roundtrip
        with pytest.raises(CapabilityProfileRejected):
            require_v1(profile, ProviderRole.DEVELOPER)
        with pytest.raises(CapabilityProfileRejected):
            fingerprint_v1_projection(profile)


def test_unmediated_em_tudo_e_diagnosticado_capability_a_capability() -> None:
    everything = ProviderCapabilityProfile(
        **{c.value: EnforcementMode.UNMEDIATED for c in Capability}
    )

    violations = check_v1(everything, ProviderRole.DEVELOPER)

    assert [v.capability for v in violations] == ALL_CAPABILITIES
    assert all(v.declared is EnforcementMode.UNMEDIATED for v in violations)


def test_developer_com_shell_habilitado_e_recusado_em_qualquer_modo() -> None:
    """[ADR-0009]: `execute_commands = disabled`, sem exceção."""
    for mode in (
        EnforcementMode.MEDIATED,
        EnforcementMode.FIXED_OPERATIONS_ONLY,
        EnforcementMode.UNMEDIATED,
    ):
        with pytest.raises(CapabilityProfileRejected, match="execute_commands"):
            require_v1(_developer_with(execute_commands=mode), ProviderRole.DEVELOPER)


@pytest.mark.parametrize(
    "capability", [Capability.GIT_WRITE, Capability.NETWORK, Capability.EXTERNAL_PATHS],
    ids=lambda c: c.value,
)  # fmt: skip
def test_capabilities_que_a_v1_nunca_habilita_sao_recusadas_habilitadas(
    capability: Capability,
) -> None:
    for mode in (EnforcementMode.MEDIATED, EnforcementMode.FIXED_OPERATIONS_ONLY):
        with pytest.raises(CapabilityProfileRejected, match=capability.value):
            require_v1(_developer_with(**{capability.value: mode}), ProviderRole.DEVELOPER)
        with pytest.raises(CapabilityProfileRejected, match=capability.value):
            require_v1(_auditor_with(**{capability.value: mode}), ProviderRole.AUDITOR)


def test_o_auditor_nao_recebe_capability_alguma() -> None:
    for capability in ALL_CAPABILITIES:
        for mode in ALL_MODES:
            if mode is EnforcementMode.DISABLED:
                continue
            with pytest.raises(CapabilityProfileRejected):
                require_v1(_auditor_with(**{capability.value: mode}), ProviderRole.AUDITOR)


def test_mais_restritivo_que_o_aprovado_tambem_e_recusado() -> None:
    """[02] §7: a guarda exige **exatamente** o perfil aprovado. Um Developer sem
    `read_files` não é o Developer aprovado — divergência é falha, não folga."""
    with pytest.raises(CapabilityProfileRejected, match="read_files"):
        require_v1(_developer_with(read_files=EnforcementMode.DISABLED), ProviderRole.DEVELOPER)
    with pytest.raises(CapabilityProfileRejected, match="git_read"):
        require_v1(_developer_with(git_read=EnforcementMode.DISABLED), ProviderRole.DEVELOPER)


def test_varias_violacoes_saem_todas_e_na_ordem_da_tabela() -> None:
    profile = _developer_with(
        network=EnforcementMode.MEDIATED,
        execute_commands=EnforcementMode.UNMEDIATED,
        read_files=EnforcementMode.DISABLED,
    )

    violations = check_v1(profile, ProviderRole.DEVELOPER)

    assert [v.capability for v in violations] == [
        Capability.READ_FILES,
        Capability.EXECUTE_COMMANDS,
        Capability.NETWORK,
    ]
    assert violations[1].describe() == (
        "execute_commands: declarado `unmediated`, a V1 exige `disabled`"
    )


def test_nao_ha_modo_aceitar_com_aviso() -> None:
    """`require_v1` ou devolve o próprio perfil ou levanta; nunca um valor alternativo."""
    for capability, mode in itertools.product(ALL_CAPABILITIES, ALL_MODES):
        profile = _developer_with(**{capability.value: mode})
        if check_v1(profile, ProviderRole.DEVELOPER):
            with pytest.raises(CapabilityProfileRejected):
                require_v1(profile, ProviderRole.DEVELOPER)
        else:
            assert require_v1(profile, ProviderRole.DEVELOPER) is profile


# ================================= 4. o JSON histórico e o hash capturados antes das alterações


def test_o_json_historico_do_perfil_requerido_e_identico_ao_vetor_de_0fe357e() -> None:
    assert canonical_json(REQUIRED_TOOL_PROFILE) == HISTORICAL_PROFILE_JSON


def test_o_hash_historico_do_perfil_requerido_e_identico_ao_vetor_de_0fe357e() -> None:
    assert tool_profile_hash() == HISTORICAL_PROFILE_HASH
    assert tool_profile_hash(REQUIRED_TOOL_PROFILE) == HISTORICAL_PROFILE_HASH
    assert canonical_sha256(REQUIRED_TOOL_PROFILE) == HISTORICAL_PROFILE_HASH


def test_o_documento_historico_tem_quatro_capabilities_e_a_versao_1() -> None:
    """Fixa a **forma**: mudar as chaves (ex.: renomear `network_access`) quebra aprovações
    já gravadas em `approved_fingerprint_parts`."""
    assert REQUIRED_TOOL_PROFILE == {
        "v": 1,
        "execute_commands": "disabled",
        "read_files": "mediated",
        "write_files": "mediated",
        "network_access": "disabled",
    }
    assert TOOL_PROFILE_VERSION == 1


def test_a_projecao_do_perfil_oficial_reproduz_o_documento_historico_byte_a_byte() -> None:
    projection = fingerprint_v1_projection(DEVELOPER_V1_PROFILE)

    assert projection == REQUIRED_TOOL_PROFILE
    assert canonical_json(projection) == HISTORICAL_PROFILE_JSON
    assert canonical_sha256(projection) == HISTORICAL_PROFILE_HASH
    assert tool_profile_hash(projection) == HISTORICAL_PROFILE_HASH


def test_o_fingerprint_v1_completo_e_identico_ao_vetor_de_0fe357e() -> None:
    parts = _parts()

    assert canonical_json(parts.as_canonical()) == HISTORICAL_FINGERPRINT_JSON
    assert parts.compute() == HISTORICAL_FINGERPRINT_HASH


def test_a_projecao_nao_enxerga_git_read_git_write_nem_external_paths() -> None:
    """Prova por construção **por que** a validação completa tem de vir antes do hash: o
    documento v1 é cego a três das sete capabilities. Se este teste falhar (o hash passou a
    cobri-las), o formato histórico mudou."""
    hostile = _developer_with(
        git_read=EnforcementMode.UNMEDIATED,
        git_write=EnforcementMode.UNMEDIATED,
        external_paths=EnforcementMode.UNMEDIATED,
    )
    naive_projection = {
        "v": 1,
        "execute_commands": hostile.execute_commands.value,
        "read_files": hostile.read_files.value,
        "write_files": hostile.write_files.value,
        "network_access": hostile.network.value,
    }

    # o hash sozinho NÃO distingue o perfil hostil do aprovado…
    assert canonical_sha256(naive_projection) == HISTORICAL_PROFILE_HASH
    # …então quem barra é a validação do perfil completo.
    with pytest.raises(CapabilityProfileRejected):
        fingerprint_v1_projection(hostile)


@pytest.mark.parametrize("capability", ALL_CAPABILITIES, ids=lambda c: c.value)
def test_a_projecao_valida_o_perfil_completo_antes_de_projetar(capability: Capability) -> None:
    """Para **cada** capability, incluindo as três que o documento v1 não serializa."""
    for mode in ALL_MODES:
        profile = _developer_with(**{capability.value: mode})
        if mode is DEVELOPER_V1_PROFILE.mode_of(capability):
            assert fingerprint_v1_projection(profile) == REQUIRED_TOOL_PROFILE
        else:
            with pytest.raises(CapabilityProfileRejected):
                fingerprint_v1_projection(profile)


def test_o_auditor_nao_tem_projecao_para_o_fingerprint_v1() -> None:
    with pytest.raises(CapabilityProfileRejected):
        fingerprint_v1_projection(AUDITOR_V1_PROFILE)


# ============================================================== 5. determinismo


def test_validacao_e_serializacao_sao_deterministicas() -> None:
    profile = _developer_with(
        network=EnforcementMode.MEDIATED, execute_commands=EnforcementMode.UNMEDIATED
    )
    first_violations = check_v1(profile, ProviderRole.DEVELOPER)
    first_doc = canonical_json(profile.as_document())
    first_projection = canonical_json(fingerprint_v1_projection(DEVELOPER_V1_PROFILE))

    for _ in range(25):
        assert check_v1(profile, ProviderRole.DEVELOPER) == first_violations
        assert canonical_json(profile.as_document()) == first_doc
        assert canonical_json(fingerprint_v1_projection(DEVELOPER_V1_PROFILE)) == first_projection
        with pytest.raises(CapabilityProfileRejected) as raised:
            require_v1(profile, ProviderRole.DEVELOPER)
        assert raised.value.violations == first_violations


def test_a_ordem_das_chaves_do_documento_de_entrada_nao_muda_o_perfil() -> None:
    document = DEVELOPER_V1_PROFILE.as_document()
    reversed_document = dict(reversed(list(document.items())))

    assert parse_capability_profile(reversed_document) == DEVELOPER_V1_PROFILE
    assert canonical_json(reversed_document) == canonical_json(document)


def test_parse_nao_muta_o_documento_de_entrada() -> None:
    document = DEVELOPER_V1_PROFILE.as_document()
    snapshot = dict(document)

    parse_capability_profile(document)

    assert document == snapshot


def test_a_projecao_devolve_um_dict_novo_a_cada_chamada() -> None:
    """Mutar a projeção não pode contaminar a seguinte nem `REQUIRED_TOOL_PROFILE`."""
    first = fingerprint_v1_projection(DEVELOPER_V1_PROFILE)
    first["execute_commands"] = "mediated"

    assert fingerprint_v1_projection(DEVELOPER_V1_PROFILE) == REQUIRED_TOOL_PROFILE
    assert REQUIRED_TOOL_PROFILE["execute_commands"] == "disabled"


# ================================================ 6. nenhuma alteração indevida no fingerprint v1


def test_a_estrutura_do_fingerprint_v1_continua_fechada_e_sem_model_policy_hash() -> None:
    """Decisões 1 e 2: nada de `model_policy_hash`; `developer_binding`/`auditor_binding`
    intocados. O conjunto de chaves é o de [02] §7, tal como congelado em 0fe357e."""
    assert FINGERPRINT_VERSION == 1
    assert set(_parts().as_canonical()) == {
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
    assert [f.name for f in dataclasses.fields(FingerprintParts)] == [
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
    ]


def test_o_orchestrator_nao_importa_o_modulo_novo() -> None:
    """A E7.1 não religa nada: o fingerprint segue calculando o perfil requerido da
    constante histórica, sem passar pelo contrato novo. A ligação vem na E7.6."""
    orchestrator_root = Path(fingerprint_module.__file__).parent

    for path in orchestrator_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "capability_profile" not in node.module, path.name
                assert not any(a.name == "capability_profile" for a in node.names), path.name
            elif isinstance(node, ast.Import):
                assert not any("capability_profile" in a.name for a in node.names), path.name


# ============================================= 7. nenhuma execução de agente/subprocesso


def test_o_modulo_novo_so_importa_biblioteca_padrao_pura() -> None:
    tree = ast.parse(Path(cp.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])

    assert imported <= {"__future__", "dataclasses", "enum", "types", "typing"}


def test_nenhuma_operacao_do_contrato_cria_processo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercita toda a API pública com a criação de processo armada para falhar."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("o contrato de capabilities não pode criar processo")

    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _boom)
    for name in ("system", "popen", "spawnv", "execv", "startfile"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _boom)

    for role in ProviderRole:
        official = V1_PROFILES[role]
        parse_capability_profile(official.as_document())
        assert check_v1(official, role) == ()
        require_v1(official, role)
    fingerprint_v1_projection(DEVELOPER_V1_PROFILE)
    with pytest.raises(CapabilityProfileRejected):
        require_v1(
            _developer_with(execute_commands=EnforcementMode.UNMEDIATED), ProviderRole.DEVELOPER
        )
