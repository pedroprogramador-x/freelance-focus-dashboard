"""`ProviderCapabilityProfile` — o contrato **puro** de capabilities (E7.1).

Fontes congeladas: [04](../../../docs/architecture/04-safety-and-git-runtime.md) §1 (perfis
oficiais e modos de enforcement), [05](../../../docs/architecture/05-provider-contracts.md)
§3 e [ADR-0009](../../../docs/adr/0009-provider-capability-enforcement.md).

## O que este módulo é — e o que não é

É o **vocabulário** e a **política de aceitação**: as sete capabilities, os quatro modos de
enforcement, o perfil oficial de cada papel na V1 e o veredito "este perfil é aceitável para
execução?". Não abre processo, não fala com provider, não prova nada. **Declaração não é
prova** ([04] §1): quem prova o perfil efetivo é o adaptador (E7.6/E8) e a guarda
`approved → executing` (`state_machine.CapabilityProver`). Este módulo só diz o que a V1
aceitaria caso a prova exista.

`enforcement_method` e `enforcement_evidence` ([04] §1) pertencem à prova, não ao perfil, e
ficam fora desta etapa.

## Representar não é aceitar

`unmediated` ("provider age sozinho, sem verificação — **nunca aceito na V1**") é um valor
**representável**: um adaptador que declare isso precisa poder ser descrito num diagnóstico
e num `SafetyEvent`. Mas `check_v1` o lista como violação e `require_v1` o recusa. A
representação é permissiva de propósito; a aceitação é fail-closed.

## A V1 exige o perfil oficial *exatamente*

[02] §7: a guarda exige que o perfil efetivo comprovado satisfaça **exatamente** o perfil
aprovado. Por isso a política aqui é igualdade com o perfil oficial do papel, e não
"no máximo tão permissivo quanto". Um Developer com `read_files = disabled` é diferente do
aprovado, e a divergência é falha, não folga.

## A projeção para o `execution_fingerprint` v1 não cobre as sete capabilities

O documento histórico que gera `tool_profile_hash` (`REQUIRED_TOOL_PROFILE`, congelado na
E6) tem **quatro** capabilities — `execute_commands`, `read_files`, `write_files` e
`network_access` — e **não** tem `git_read`, `git_write` nem `external_paths`. Dois perfis
que só diferem em `git_write` produzem, portanto, o mesmo hash v1. É por isso que
`fingerprint_v1_projection` valida o perfil **completo** antes de projetar: o hash não pode
ser a única barreira, porque não enxerga o contrato inteiro. O nome também difere: o
contrato usa `network`; o documento histórico usa `network_access`. A projeção faz o mapa.

A forma serializada e o hash do documento histórico **não mudam** (decisão 1 da E7.1, sem
`model_policy_hash`); `tests/test_capability_profile_e7_1.py` os trava contra um vetor
capturado em `0fe357e`.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
from types import MappingProxyType
from typing import Any


class Capability(str, Enum):
    """As sete capabilities de [04] §1 / [05] §3, na ordem da tabela oficial."""

    READ_FILES = "read_files"
    WRITE_FILES = "write_files"
    EXECUTE_COMMANDS = "execute_commands"
    GIT_READ = "git_read"
    GIT_WRITE = "git_write"
    NETWORK = "network"
    EXTERNAL_PATHS = "external_paths"


class EnforcementMode(str, Enum):
    """Os quatro modos de enforcement de [04] §1."""

    DISABLED = "disabled"
    MEDIATED = "mediated"
    FIXED_OPERATIONS_ONLY = "fixed_operations_only"
    #: Representável para diagnóstico; **nunca aceito na V1**.
    UNMEDIATED = "unmediated"


class ProviderRole(str, Enum):
    """Os dois papéis que têm perfil oficial em [04] §1."""

    DEVELOPER = "developer"
    AUDITOR = "auditor"


class InvalidCapabilityProfile(ValueError):
    """Documento ou valor que nem sequer é um perfil de capability bem formado.

    `ValueError`, como `InvalidTestPolicy`: `safety/` é política pura e não conhece HTTP.
    Distinta de `CapabilityProfileRejected`: aqui o perfil não pôde ser **representado**; lá
    ele foi representado e a política da V1 o recusou.
    """


@dataclass(frozen=True, slots=True)
class ProfileViolation:
    """Uma capability cujo modo declarado difere do exigido pela V1."""

    capability: Capability
    declared: EnforcementMode
    required: EnforcementMode

    def describe(self) -> str:
        return (
            f"{self.capability.value}: declarado `{self.declared.value}`, "
            f"a V1 exige `{self.required.value}`"
        )


class CapabilityProfileRejected(ValueError):
    """O perfil é bem formado mas a política da V1 não o aceita para execução.

    Carrega as violações para que o chamador registre `SafetyEvent(capability_unenforceable)`
    com o motivo exato — a mensagem já é o diagnóstico completo.
    """

    def __init__(self, role: ProviderRole, violations: tuple[ProfileViolation, ...]) -> None:
        self.role = role
        self.violations = violations
        detail = "; ".join(violation.describe() for violation in violations)
        super().__init__(f"perfil de capability recusado para {role.value}: {detail}")


@dataclass(frozen=True, slots=True)
class ProviderCapabilityProfile:
    """Um modo de enforcement para cada uma das sete capabilities. Sem defaults.

    Sem default de propósito: um campo esquecido não pode virar `disabled` em silêncio — a
    ausência precisa ser erro, senão "declarei tudo" e "esqueci metade" ficam iguais.
    """

    read_files: EnforcementMode
    write_files: EnforcementMode
    execute_commands: EnforcementMode
    git_read: EnforcementMode
    git_write: EnforcementMode
    network: EnforcementMode
    external_paths: EnforcementMode

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if not isinstance(value, EnforcementMode):
                raise InvalidCapabilityProfile(
                    f"{field.name} precisa ser um EnforcementMode, não {type(value).__name__}"
                )

    def mode_of(self, capability: Capability) -> EnforcementMode:
        mode: EnforcementMode = getattr(self, capability.value)
        return mode

    def as_document(self) -> dict[str, str]:
        """O documento de sete chaves, na ordem oficial. Inverso de `parse_capability_profile`."""
        return {capability.value: self.mode_of(capability).value for capability in Capability}


_CAPABILITY_KEYS = frozenset(capability.value for capability in Capability)
_MODES_BY_VALUE = {mode.value: mode for mode in EnforcementMode}


def parse_capability_profile(document: Any) -> ProviderCapabilityProfile:
    """Valida forma e valores de um documento e o converte em `ProviderCapabilityProfile`.

    Estrito: exatamente as sete chaves, cada valor uma string igual a um dos quatro modos.
    Sem normalização (caixa, espaços) e sem coerção — `True`, `1`, `None` e `"Disabled"`
    são recusados, porque um perfil de segurança que "quase" bate não é o perfil aprovado.
    Não decide se o perfil é **aceitável**; isso é `check_v1`.
    """
    if not isinstance(document, dict):
        raise InvalidCapabilityProfile("o perfil de capability precisa ser um objeto JSON")

    non_string_keys = [key for key in document if not isinstance(key, str)]
    if non_string_keys:
        raise InvalidCapabilityProfile("o perfil de capability só aceita chaves string")

    unknown = sorted(set(document) - _CAPABILITY_KEYS)
    if unknown:
        raise InvalidCapabilityProfile(
            f"capability(ies) desconhecida(s): {', '.join(repr(key) for key in unknown)}"
        )

    missing = [c.value for c in Capability if c.value not in document]
    if missing:
        raise InvalidCapabilityProfile(f"capability(ies) ausente(s): {', '.join(missing)}")

    modes: dict[str, EnforcementMode] = {}
    for capability in Capability:
        raw = document[capability.value]
        if not isinstance(raw, str) or raw not in _MODES_BY_VALUE:
            allowed = ", ".join(mode.value for mode in EnforcementMode)
            raise InvalidCapabilityProfile(
                f"{capability.value}: modo de enforcement inválido {raw!r} (aceitos: {allowed})"
            )
        modes[capability.value] = _MODES_BY_VALUE[raw]

    return ProviderCapabilityProfile(**modes)


# ------------------------------------------------------------------- política da V1

#: Os perfis oficiais de [04] §1 / [05] §3. Transcrição literal das tabelas.
DEVELOPER_V1_PROFILE = ProviderCapabilityProfile(
    read_files=EnforcementMode.MEDIATED,
    write_files=EnforcementMode.MEDIATED,
    execute_commands=EnforcementMode.DISABLED,
    git_read=EnforcementMode.FIXED_OPERATIONS_ONLY,
    git_write=EnforcementMode.DISABLED,
    network=EnforcementMode.DISABLED,
    external_paths=EnforcementMode.DISABLED,
)

AUDITOR_V1_PROFILE = ProviderCapabilityProfile(
    read_files=EnforcementMode.DISABLED,
    write_files=EnforcementMode.DISABLED,
    execute_commands=EnforcementMode.DISABLED,
    git_read=EnforcementMode.DISABLED,
    git_write=EnforcementMode.DISABLED,
    network=EnforcementMode.DISABLED,
    external_paths=EnforcementMode.DISABLED,
)

V1_PROFILES: MappingProxyType[ProviderRole, ProviderCapabilityProfile] = MappingProxyType(
    {
        ProviderRole.DEVELOPER: DEVELOPER_V1_PROFILE,
        ProviderRole.AUDITOR: AUDITOR_V1_PROFILE,
    }
)


def _require_profile(profile: object) -> ProviderCapabilityProfile:
    if not isinstance(profile, ProviderCapabilityProfile):
        raise InvalidCapabilityProfile(
            f"esperado ProviderCapabilityProfile, recebido {type(profile).__name__}"
        )
    return profile


def check_v1(
    profile: ProviderCapabilityProfile, role: ProviderRole
) -> tuple[ProfileViolation, ...]:
    """Todas as divergências entre `profile` e o perfil oficial de `role`. Vazio = aceito.

    Determinística: a ordem é a da tabela oficial, e não a de inserção de nada. Confere as
    **sete** capabilities — nenhuma é opcional para a política.
    """
    profile = _require_profile(profile)
    required = V1_PROFILES[role]

    return tuple(
        ProfileViolation(
            capability=capability,
            declared=profile.mode_of(capability),
            required=required.mode_of(capability),
        )
        for capability in Capability
        if profile.mode_of(capability) is not required.mode_of(capability)
    )


def require_v1(profile: ProviderCapabilityProfile, role: ProviderRole) -> ProviderCapabilityProfile:
    """Devolve `profile` se e somente se a V1 o aceita para `role`; senão levanta.

    Fail closed: qualquer divergência — inclusive `unmediated` — é
    `CapabilityProfileRejected`. Não há modo "aceitar com aviso".
    """
    violations = check_v1(profile, role)
    if violations:
        raise CapabilityProfileRejected(role, violations)
    return profile


# ------------------------------------------------ compatibilidade com o fingerprint v1

#: Versão do documento histórico de perfil ([02] §7 / `fingerprint.TOOL_PROFILE_VERSION`).
#: Repetida aqui — `safety/` não importa `orchestrator/` — e travada por teste contra o
#: vetor capturado em `0fe357e`.
_FINGERPRINT_V1_PROFILE_VERSION = 1


def fingerprint_v1_projection(profile: ProviderCapabilityProfile) -> dict[str, Any]:
    """O documento histórico de `tool_profile_hash`, derivado de um perfil **já validado**.

    A validação do perfil completo (`require_v1` como Developer) acontece **antes** de
    qualquer projeção: o documento v1 omite `git_read`, `git_write` e `external_paths`, e
    portanto o hash sozinho não distinguiria um perfil recusável de um aceitável. Só um
    perfil aceito chega à projeção; a projeção de um perfil recusado levanta
    `CapabilityProfileRejected`.

    A saída é igual, chave a chave, a `orchestrator.fingerprint.REQUIRED_TOOL_PROFILE`, e
    `canonical_sha256` dela é o `tool_profile_hash` histórico. Só o Developer tem projeção:
    `auditor_binding` é `null` no fingerprint v1 e o Auditor não tem documento de perfil.
    """
    accepted = require_v1(profile, ProviderRole.DEVELOPER)
    return {
        "v": _FINGERPRINT_V1_PROFILE_VERSION,
        "execute_commands": accepted.execute_commands.value,
        "read_files": accepted.read_files.value,
        "write_files": accepted.write_files.value,
        "network_access": accepted.network.value,
    }


__all__ = [
    "AUDITOR_V1_PROFILE",
    "DEVELOPER_V1_PROFILE",
    "V1_PROFILES",
    "Capability",
    "CapabilityProfileRejected",
    "EnforcementMode",
    "InvalidCapabilityProfile",
    "ProfileViolation",
    "ProviderCapabilityProfile",
    "ProviderRole",
    "check_v1",
    "fingerprint_v1_projection",
    "parse_capability_profile",
    "require_v1",
]
