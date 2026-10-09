"""Resource Router — [03](../../../docs/architecture/03-context-architecture.md) §6.

**Determinístico. Nenhum LLM participa.** A tabela de §6, inteira:

    | risco \\ complexidade | trivial / low | medium | high |
    | low    | developer | developer | developer |
    | medium | developer | developer | developer (+ architect se sinalizado) |
    | high   | developer | developer (+ architect se sinalizado) | developer + architect |

Duas ausências na tabela são normativas:

* **o auditor não aparece** porque "não é opcional em operação normal" (§7) — ele não é
  uma escolha do Router. Ele também não entra em `agents` na E6: o `AuditorProvider` chega
  na E9, e listar um agente que nenhum código pode invocar faria `agents` — que entra no
  `execution_fingerprint` — afirmar uma composição que a execução não teria;
* **`researcher`** entra "só quando a análise aponta dependência externa desconhecida", que
  é o `needs_researcher` do enriquecimento. Como nenhuma porta de enriquecimento existe na
  E6, ele nunca é selecionado nesta fase — mas o ramo existe e é testado, porque a decisão
  é do Router e não do provider.

## `max_fix_rounds` é carregado e fica **inerte**

[03] §6 e a correção 1B.1 (AUD-013): `max_fix_rounds` governa o laço de correção dirigido
por auditoria, que **só existe a partir da E10**. Aqui ele é lido da configuração, entra no
`execution_fingerprint` (para que mudá-lo invalide a aprovação, como [04] §7 exige) e não
governa nada. Nenhum código desta fase ramifica sobre ele — de propósito: um laço de
correção antes de o auditor existir não teria sinal para consumir.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db.enums import ComplexityLevel, RiskLevel
from app.orchestrator.analyzer import TaskAnalysis

#: Os limites de [03] §6. Congelados como constantes de módulo porque entram no
#: `execution_fingerprint` ([02] §7, `execution_limits`): mudá-los invalida toda aprovação
#: vigente, o que é o comportamento correto e precisa ser uma mudança visível de código.
PREFER_SINGLE_AGENT = True
MAX_AGENTS = 3
MAX_PARALLEL_AGENTS = 1
MAX_ATTEMPTS = 2

#: Limites de tempo de [04] §7 (E8.4.1). `RUN_TIMEOUT_S` é o teto de **cada operação** (uma
#: invocação do Developer, uma execução de testes); `TASK_TIMEOUT_S` é o da tentativa inteira —
#: preparação, execução, testes e verificação. O Run de controle acompanha o prazo da task, e o
#: limite de cada operação futura respeita o tempo restante dela (`execution_contract`).
#: Entram no `execution_fingerprint` (mudá-los invalida a aprovação). A supervisão operacional
#: é das etapas de runtime; aqui são só os valores aprovados.
RUN_TIMEOUT_S = 1200
TASK_TIMEOUT_S = 1800

#: **Inerte até E10.** Ver o docstring do módulo.
MAX_FIX_ROUNDS = 2

#: Orçamentos de [03] §6, "configurável". Os valores são o default da V1; `plan` os recebe
#: por parâmetro para que a configuração por workspace (quando existir) não precise tocar
#: nesta tabela.
DEFAULT_MAX_CONTEXT_TOKENS = 60_000
DEFAULT_MAX_TOTAL_TOKENS = 200_000

#: Os cinco agentes de [02] §8 que este Router pode escolher. `auditor` e `test_runner` não
#: estão aqui: o primeiro é obrigatório e não escolhido (§7), o segundo é infraestrutura do
#: sistema e não agente de raciocínio ([04] §6).
AGENT_DEVELOPER = "developer"
AGENT_ARCHITECT = "architect"
AGENT_RESEARCHER = "researcher"


@dataclass(frozen=True, slots=True)
class ResourceDecision:
    """O que o Router decidiu. Tudo derivado da tabela — nada aqui é opinião."""

    #: Ordenada e **semanticamente relevante** ([02] §7): `agents` entra no fingerprint
    #: como array de ordem preservada. `developer` é sempre o primeiro.
    agents: tuple[str, ...]
    max_context_tokens: int
    max_total_tokens: int
    max_attempts: int
    #: Carregado, inerte. Ver o docstring do módulo.
    max_fix_rounds: int
    max_parallel_agents: int
    max_agents: int
    #: Limites de tempo aprovados (E8.4.1). Ver `RUN_TIMEOUT_S`/`TASK_TIMEOUT_S`.
    run_timeout_s: int
    task_timeout_s: int

    def as_execution_limits(self) -> dict[str, Any]:
        """O `execution_limits` de [02] §7 — "timeouts, orçamentos, max_attempts,
        max_fix_rounds".

        E8.4.1: `run_timeout_s` e `task_timeout_s` ([04] §7) passam a fazer parte do
        documento. Acrescentá-los muda o hash e invalida as aprovações vigentes — exatamente o
        comportamento correto para uma mudança de limite ([04] §7); a estrutura do
        fingerprint (versão, algoritmo, chaves de topo) não muda.
        """
        return {
            "max_context_tokens": self.max_context_tokens,
            "max_total_tokens": self.max_total_tokens,
            "max_attempts": self.max_attempts,
            "max_fix_rounds": self.max_fix_rounds,
            "max_parallel_agents": self.max_parallel_agents,
            "max_agents": self.max_agents,
            "run_timeout_s": self.run_timeout_s,
            "task_timeout_s": self.task_timeout_s,
        }


def _architect_selected(risk: RiskLevel, complexity: ComplexityLevel, *, signalled: bool) -> bool:
    """A tabela de [03] §6, célula a célula.

    Três células convocam o `architect`, e duas delas dependem do sinal da análise:

    | célula | regra |
    | --- | --- |
    | `high` × `high` | **sempre** — a única incondicional |
    | `high` × `medium` | só se sinalizado |
    | `medium` × `high` | só se sinalizado |
    | resto | nunca |
    """
    if risk is RiskLevel.HIGH and complexity is ComplexityLevel.HIGH:
        return True
    if risk is RiskLevel.HIGH and complexity is ComplexityLevel.MEDIUM:
        return signalled
    if risk is RiskLevel.MEDIUM and complexity is ComplexityLevel.HIGH:
        return signalled
    return False


def route(
    analysis: TaskAnalysis,
    *,
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS,
) -> ResourceDecision:
    """Aplica a tabela de [03] §6. **Sem LLM, sem IO, sem rede.**

    `agents` nunca excede `MAX_AGENTS`: no pior caso são três
    (`developer`, `architect`, `researcher`), que é exatamente o teto.
    """
    agents = [AGENT_DEVELOPER]

    if _architect_selected(analysis.risk, analysis.complexity, signalled=analysis.needs_architect):
        agents.append(AGENT_ARCHITECT)

    if analysis.needs_researcher:
        agents.append(AGENT_RESEARCHER)

    return ResourceDecision(
        agents=tuple(agents),
        max_context_tokens=max_context_tokens,
        max_total_tokens=max_total_tokens,
        max_attempts=MAX_ATTEMPTS,
        max_fix_rounds=MAX_FIX_ROUNDS,
        max_parallel_agents=MAX_PARALLEL_AGENTS,
        max_agents=MAX_AGENTS,
        run_timeout_s=RUN_TIMEOUT_S,
        task_timeout_s=TASK_TIMEOUT_S,
    )


__all__ = [
    "AGENT_ARCHITECT",
    "AGENT_DEVELOPER",
    "AGENT_RESEARCHER",
    "DEFAULT_MAX_CONTEXT_TOKENS",
    "DEFAULT_MAX_TOTAL_TOKENS",
    "MAX_AGENTS",
    "MAX_ATTEMPTS",
    "MAX_FIX_ROUNDS",
    "MAX_PARALLEL_AGENTS",
    "PREFER_SINGLE_AGENT",
    "RUN_TIMEOUT_S",
    "TASK_TIMEOUT_S",
    "ResourceDecision",
    "route",
]
