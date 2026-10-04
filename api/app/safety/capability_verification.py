"""Contrato **puro** da verificação independente de capability (E7.6-A/B).

Fontes congeladas: [04] §1 ("declaração acompanhada de prova"), [05] §3 e
[ADR-0009](../../../docs/adr/0009-provider-capability-enforcement.md) §3–§4.

## Duas coisas que nunca se confundem

* a **declaração** (`agent_runtime.CapabilityDeclaration`) é o que o adaptador *afirma* sobre
  si — autodeclarada, portanto não confiável. Uma declaração perfeita é exatamente o que um
  adaptador mentiroso também declararia;
* a **observação verificada** (`VerifiedCapabilityObservation`, aqui) é o que um mecanismo
  *independente* do adaptador viu ser aplicado à configuração preparada. É o único insumo
  de prova positiva — e mesmo ela só vale **contra um contexto esperado** que vem do
  chamador confiável (`CapabilityBinding`), nunca da própria observação.

Este módulo é só vocabulário e veredito. Não executa processo, não fala com provider, não
conhece banco, `agent_runtime` nem `orchestrator`; recebe fatos e decide, como o resto de
`safety/`. A porta que *produz* a observação vive em `agent_runtime`, a guarda que a
*consome* vive em `orchestrator`, e a ponte entre as duas só existe no composition root.

## Ordem obrigatória da avaliação (E7.6-B)

1. o perfil **completo** observado — as **sete** capabilities — é confrontado, por
   igualdade exata, com o perfil oficial do papel esperado (`check_v1`, a mesma autoridade
   de E7.1; nenhuma regra concorrente é reescrita aqui);
2. o `binding` observado é confrontado campo a campo com o esperado;
3. **só depois** o documento histórico do `tool_profile_hash` é projetado e hasheado
   (`historical_profile_hash`).

O hash histórico cobre apenas `execute_commands`, `read_files`, `write_files` e
`network_access`. Ele **não enxerga** `git_read`, `git_write` nem `external_paths`; por isso
nunca pode ser a única barreira, e `historical_profile_hash` recusa o perfil que não passou
no passo 1.

## O que isto não alega

Nenhuma proteção contra código Python hostil rodando no mesmo processo: quem constrói uma
`VerifiedCapabilityObservation` à mão é o verificador (infraestrutura confiável), e este
módulo não tenta impedir que outro código do processo faça o mesmo. A ameaça tratada é a de
**provider** que declara mais do que aplica e a de uma verificação **reapresentada** para
outra configuração.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from enum import Enum

from app.safety.canonical import canonical_sha256
from app.safety.capability_profile import (
    Capability,
    ProviderCapabilityProfile,
    ProviderRole,
    check_v1,
    fingerprint_v1_projection,
)

_SHA256 = re.compile(r"[0-9a-f]{64}")


class InvalidCapabilityVerification(ValueError):
    """Dado que nem sequer forma uma observação/binding bem formado.

    Distinta de `CapabilityRefusal`: aqui o objeto não pôde ser *representado* (erro de quem
    o construiu); lá ele foi representado e a política o recusou.
    """


class UnverifiedReason(str, Enum):
    """Por que um verificador **não conseguiu** produzir uma observação positiva.

    Resultado negativo *esperado* e tipado — distinto de falha técnica, que é exceção e
    nunca vira recusa de política. Vocabulário fechado: o texto livre de um adaptador não
    chega a diagnóstico nenhum.
    """

    CONTROLS_NOT_OBSERVED = "controls_not_observed"
    UNSUPPORTED_CONFIGURATION = "unsupported_configuration"
    PROBE_FAILED = "probe_failed"


class CapabilityRefusalCode(str, Enum):
    """Códigos estáveis de recusa. Ordem lógica: ausência → negativa → divergência."""

    #: Não há verificador/declaração injetados (produção na E7.6).
    VERIFIER_ABSENT = "verifier_absent"
    #: O chamador confiável não informou o contexto esperado a que a prova se vincula.
    EXPECTED_CONTEXT_MISSING = "expected_context_missing"
    #: O adaptador declarou `not_enforceable`: nada a verificar, nada a provar.
    DECLARATION_NOT_ENFORCEABLE = "declaration_not_enforceable"
    #: O verificador respondeu, e a resposta foi "não verifiquei" (`UnverifiedReason`).
    VERIFICATION_NEGATIVE = "verification_negative"
    #: A prova não traz observação verificada (p.ex. a prova legada `proven + hash`).
    VERIFICATION_MISSING = "verification_missing"
    #: A declaração e o que foi observado discordam: a declaração é inconfiável.
    DECLARATION_OBSERVATION_MISMATCH = "declaration_observation_mismatch"
    #: O perfil completo observado difere do oficial do papel (qualquer das sete).
    PROFILE_MISMATCH = "profile_mismatch"
    #: A observação pertence a outro papel/adaptador/versão/transport/model/config/declaração.
    BINDING_MISMATCH = "binding_mismatch"
    #: A prova não informou o hash efetivo.
    EFFECTIVE_HASH_MISSING = "effective_hash_missing"
    #: O hash efetivo informado não é o requerido nem o derivado da observação.
    EFFECTIVE_HASH_MISMATCH = "effective_hash_mismatch"


#: Campos do `binding` — também o vocabulário (fechado) dos detalhes de um mismatch.
BINDING_FIELDS: tuple[str, ...] = (
    "role",
    "adapter_id",
    "adapter_version",
    "transport",
    "model",
    "execution_config_hash",
    "declaration_hash",
)

_DETAIL_VOCABULARY: frozenset[str] = frozenset(
    {
        *(capability.value for capability in Capability),
        *BINDING_FIELDS,
        *(reason.value for reason in UnverifiedReason),
    }
)


def _text(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise InvalidCapabilityVerification(f"{name} precisa ser str, não {type(value).__name__}")
    if not value.strip() or "\x00" in value:
        raise InvalidCapabilityVerification(f"{name} não pode ser vazio nem conter NUL")
    return value


def _sha256(name: str, value: object) -> str:
    text = _text(name, value)
    if not _SHA256.fullmatch(text):
        raise InvalidCapabilityVerification(f"{name} precisa ser sha256 hexadecimal minúsculo")
    return text


@dataclass(frozen=True, slots=True)
class CapabilityBinding:
    """A que configuração uma verificação (ou uma expectativa) pertence. Imutável.

    Mudar **qualquer** campo exige nova verificação: não existe "mesmo adaptador, outra
    versão". `declaration_hash` identifica a declaração/configuração preparada que foi de
    fato verificada; como o `declaration_hash` cobre papel, adaptador, versão, transport,
    model e `execution_config_hash` da declaração, ele ainda amarra a observação à
    declaração concreta e não só aos campos soltos.

    O mesmo tipo serve aos dois lados: o verificador o preenche com o que *verificou*; o
    chamador confiável o preenche com o que *espera* — e só o segundo decide.
    """

    role: ProviderRole
    adapter_id: str
    adapter_version: str
    transport: str
    model: str
    execution_config_hash: str
    declaration_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, ProviderRole):
            raise InvalidCapabilityVerification(
                f"role precisa ser ProviderRole, não {type(self.role).__name__}"
            )
        for name in ("adapter_id", "adapter_version", "transport", "model"):
            _text(name, getattr(self, name))
        _sha256("execution_config_hash", self.execution_config_hash)
        _sha256("declaration_hash", self.declaration_hash)

    def divergences_from(self, expected: CapabilityBinding) -> tuple[str, ...]:
        """Nomes (vocabulário fechado) dos campos que diferem de `expected`. Vazio = igual."""
        return tuple(
            field.name
            for field in fields(self)
            if getattr(self, field.name) != getattr(expected, field.name)
        )


@dataclass(frozen=True, slots=True)
class VerifiedCapabilityObservation:
    """O que um mecanismo independente **observou** ser aplicado — perfil completo + binding.

    `profile` carrega as **sete** capabilities; não há projeção parcial. Não tem `proven`,
    `accepted` nem veredito: é dado. Quem decide é `evaluate_observation`, contra o
    contexto esperado.
    """

    binding: CapabilityBinding
    profile: ProviderCapabilityProfile

    def __post_init__(self) -> None:
        if not isinstance(self.binding, CapabilityBinding):
            raise InvalidCapabilityVerification("binding precisa ser CapabilityBinding")
        if not isinstance(self.profile, ProviderCapabilityProfile):
            raise InvalidCapabilityVerification("profile precisa ser ProviderCapabilityProfile")


@dataclass(frozen=True, slots=True)
class CapabilityRefusal:
    """Uma recusa de política, tipada e sanitizada.

    `details` só aceita tokens do vocabulário fechado (nomes de capability, de campo do
    binding ou `UnverifiedReason`): nenhum texto do adaptador, caminho, configuração ou
    `repr` de objeto consegue chegar a uma mensagem ou a um `SafetyEvent` por aqui.
    """

    code: CapabilityRefusalCode
    details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, CapabilityRefusalCode):
            raise InvalidCapabilityVerification("code precisa ser CapabilityRefusalCode")
        unknown = [d for d in self.details if d not in _DETAIL_VOCABULARY]
        if unknown:
            raise InvalidCapabilityVerification("detalhe fora do vocabulário fechado")

    def describe(self) -> str:
        """Texto estável: o código e, se houver, os tokens fechados. Nada mais."""
        if not self.details:
            return self.code.value
        return f"{self.code.value} ({', '.join(self.details)})"


def evaluate_observation(
    expected: CapabilityBinding, observation: object
) -> CapabilityRefusal | None:
    """Confronta a observação com o contexto esperado. `None` = nenhuma divergência.

    `expected` vem do chamador **confiável**; a observação nunca escolhe contra o que será
    comparada. `observation` é `object` de propósito: o que chega de um verificador pode não
    ser uma observação, e isso é `VERIFICATION_MISSING`, não um `AttributeError`.

    Ordem (ver docstring do módulo): perfil completo primeiro, binding depois. Não calcula
    hash — `historical_profile_hash` é chamado só por quem já recebeu `None` daqui.
    """
    if not isinstance(expected, CapabilityBinding):
        raise InvalidCapabilityVerification("expected precisa ser CapabilityBinding")

    if not isinstance(observation, VerifiedCapabilityObservation):
        return CapabilityRefusal(CapabilityRefusalCode.VERIFICATION_MISSING)

    violations = check_v1(observation.profile, expected.role)
    if violations:
        return CapabilityRefusal(
            CapabilityRefusalCode.PROFILE_MISMATCH,
            tuple(violation.capability.value for violation in violations),
        )

    divergent = observation.binding.divergences_from(expected)
    if divergent:
        return CapabilityRefusal(CapabilityRefusalCode.BINDING_MISMATCH, divergent)

    return None


def historical_profile_hash(profile: ProviderCapabilityProfile) -> str:
    """O `tool_profile_hash` histórico de um perfil **já aceito** (Developer).

    Passa por `fingerprint_v1_projection`, que valida as sete capabilities antes de projetar
    — então um perfil que só difere em `git_read`/`git_write`/`external_paths` (mesmo hash
    histórico!) levanta `CapabilityProfileRejected` em vez de produzir o hash aprovado.
    """
    return canonical_sha256(fingerprint_v1_projection(profile))


__all__ = [
    "BINDING_FIELDS",
    "CapabilityBinding",
    "CapabilityRefusal",
    "CapabilityRefusalCode",
    "InvalidCapabilityVerification",
    "UnverifiedReason",
    "VerifiedCapabilityObservation",
    "evaluate_observation",
    "historical_profile_hash",
]
