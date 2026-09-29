"""Vocabulário e forma da **declaração** de capability de um adaptador (E7.2).

Fonte congelada: [04] §1 ("O que cada adaptador declara — e prova") e [ADR-0009].

## Declaração não é prova

Este módulo descreve o que um adaptador *afirma* sobre si e amarra a afirmação à
configuração que seria executada. **Nada aqui autoriza execução**, e isso é deliberado:

* não há `proven`, `authorized`, `accepted` nem método que devolva "pode executar";
* `CapabilityDeclaration` não é (nem constrói) `orchestrator.state_machine.CapabilityProof`
  — `agent_runtime` nem pode importar `orchestrator` ([01] §3);
* não expõe `effective_profile_hash`: esse é o valor que o `CapabilityProver` devolve à
  guarda `approved → executing`, e derivá-lo de uma autodeclaração seria fabricar prova;
* um perfil efetivo perfeito, `enforcement_method = cli_flag` e evidência preenchida são
  exatamente o que um adaptador mentiroso também declararia.

O que a validação estrutural faz é recusar declarações **incoerentes** (método sem
evidência, `not_enforceable` com evidência, segredo na evidência) e calcular um
`declaration_hash` determinístico para correlação/diagnóstico. A comprovação real — sonda de
preflight, verificação independente do que foi aplicado — é da E7.6/E8.

O Auditor **não** herda a projeção histórica do Developer (`fingerprint_v1_projection`):
`auditor_binding` é `null` no fingerprint v1.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.safety.canonical import canonical_sha256
from app.safety.capability_profile import ProviderCapabilityProfile, ProviderRole
from app.safety.redaction import detect_secret_spans
from app.tool_executor.validation import (
    ContractViolation,
    require_instance,
    require_optional_text,
    require_sha256,
    require_text,
    require_text_tuple,
)


class EnforcementMethod(str, Enum):
    """[04] §1 — como o adaptador diz aplicar o perfil."""

    CLI_FLAG = "cli_flag"
    CONFIG_FILE = "config_file"
    TOOL_ALLOWLIST = "tool_allowlist"
    API_TOOL_SCHEMA = "api_tool_schema"
    PROCESS_ENV = "process_env"
    NOT_ENFORCEABLE = "not_enforceable"


@dataclass(frozen=True, slots=True)
class EnforcementEvidence:
    """O que o adaptador **relata** ter aplicado. Autodeclarado, portanto não confiável.

    `applied`: flags/configuração concretamente aplicadas. `probe_result`: resultado de
    uma sonda de preflight, "onde viável" ([04] §1). Nenhum dos dois pode conter segredo.
    """

    applied: tuple[str, ...] = ()
    probe_result: str | None = None

    def __post_init__(self) -> None:
        require_text_tuple("applied", self.applied)
        require_optional_text("probe_result", self.probe_result)
        for text in (*self.applied, *(() if self.probe_result is None else (self.probe_result,))):
            if detect_secret_spans(text):
                raise ContractViolation("evidência de enforcement não pode conter segredo")

    def as_document(self) -> dict[str, Any]:
        return {"applied": list(self.applied), "probe_result": self.probe_result}


@dataclass(frozen=True, slots=True)
class CapabilityDeclaration:
    """A declaração de um adaptador para **uma** configuração de execução.

    `supported_capabilities` e `effective_capabilities` são as duas colunas de [04] §1.
    A relação entre elas não é validada aqui: qual perfil é *aceitável* é `safety.check_v1`
    (E7.1), e se o efetivo é *verdadeiro* é prova (E7.6/E8).

    `execution_config_hash` identifica a configuração que será realmente executada
    (opaco para este módulo; quem a monta é o adaptador/E7.6). Ele entra no
    `declaration_hash`, então a mesma declaração sobre outra configuração tem outro hash.
    """

    role: ProviderRole
    adapter_id: str
    adapter_version: str
    transport: str
    model: str
    execution_config_hash: str
    supported_capabilities: ProviderCapabilityProfile
    effective_capabilities: ProviderCapabilityProfile
    enforcement_method: EnforcementMethod
    evidence: EnforcementEvidence

    def __post_init__(self) -> None:
        require_instance("role", self.role, ProviderRole)
        for name in ("adapter_id", "adapter_version", "transport", "model"):
            require_text(name, getattr(self, name))
        require_sha256("execution_config_hash", self.execution_config_hash)
        require_instance(
            "supported_capabilities", self.supported_capabilities, ProviderCapabilityProfile
        )
        require_instance(
            "effective_capabilities", self.effective_capabilities, ProviderCapabilityProfile
        )
        require_instance("enforcement_method", self.enforcement_method, EnforcementMethod)
        require_instance("evidence", self.evidence, EnforcementEvidence)

        has_evidence = bool(self.evidence.applied) or self.evidence.probe_result is not None
        if self.enforcement_method is EnforcementMethod.NOT_ENFORCEABLE:
            if has_evidence:
                raise ContractViolation("not_enforceable não pode trazer evidência de aplicação")
        elif not self.evidence.applied:
            raise ContractViolation(
                f"{self.enforcement_method.value} exige evidence.applied não vazio"
            )

    def as_document(self) -> dict[str, Any]:
        return {
            "v": 1,
            "role": self.role.value,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "transport": self.transport,
            "model": self.model,
            "execution_config_hash": self.execution_config_hash,
            "supported_capabilities": self.supported_capabilities.as_document(),
            "effective_capabilities": self.effective_capabilities.as_document(),
            "enforcement_method": self.enforcement_method.value,
            "evidence": self.evidence.as_document(),
        }

    def declaration_hash(self) -> str:
        """Identificador determinístico (mesma canonicalização dos demais hashes do projeto).

        Serve para correlacionar e detectar mudança. **Não** é prova nem autorização."""
        return canonical_sha256(self.as_document())
