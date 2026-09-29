"""Protocols de provider e test runner (E7.2). Fonte congelada: [05] §1.

Nenhum menciona fornecedor. Implementações concretas (adaptadores) não existem nesta etapa e,
quando existirem (E8/E9), ficam em `agent_runtime/adapters/`, resolvidas só no composition
root ([05] §2). Os Protocols de ferramenta vêm de `tool_executor.contracts` e são
reexportados por `agent_runtime` para formar a API pública.
"""

from __future__ import annotations

from typing import Protocol

from app.agent_runtime.dto import (
    AgentRunResult,
    AuditRequest,
    AuditResult,
    DeveloperExecutionRequest,
    TestRequest,
    TestSummary,
)
from app.safety.capability_profile import ProviderCapabilityProfile
from app.tool_executor.contracts import MediatedTools


class DeveloperProvider(Protocol):
    def capability_profile(self) -> ProviderCapabilityProfile: ...

    def run(
        self, request: DeveloperExecutionRequest, mediated_tools: MediatedTools
    ) -> AgentRunResult: ...


class AuditorProvider(Protocol):
    def capability_profile(self) -> ProviderCapabilityProfile: ...

    def audit(self, request: AuditRequest) -> AuditResult: ...


class TestRunner(Protocol):
    """Infraestrutura do sistema, **não** ferramenta do Developer ([04] §6)."""

    def run(self, request: TestRequest) -> TestSummary: ...
