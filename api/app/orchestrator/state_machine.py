"""Máquina de estados de [02](../../../docs/architecture/02-data-model.md) §4 e [ADR-0008].

Nove estados, e a tabela de transições é **dado**, não uma cadeia de `if`. Isso é o que faz
"a aresta existe?" e "a guarda passou?" serem duas perguntas separadas — e é a diferença
entre `InvalidTransition` (409, a aresta não existe) e `TransitionGuardFailed` (409, a
aresta existe e uma pré-condição falhou).

## As duas invariantes de coluna

[02] §3 e [ADR-0008] regras 1–2, verificadas pelo banco **e** aqui:

* `phase` não nulo **se e somente se** `status = executing`;
* `failure_reason` não nulo **se e somente se** `status = failed`.

`apply_transition` as impõe montando os dois campos a partir do destino, em vez de confiar
em quem chamou. Um `CHECK` do SQLite que dispara é um `IntegrityError` sem diagnóstico; uma
transição que já nasce coerente nunca chega lá.

## `approved → executing` **falha nesta fase, por desenho**

A guarda de entrada exige "**perfil de capability provado** pelo adaptador — inclusive
`execute_commands = disabled`" ([02] §4, [ADR-0008] regra de 1B.3). Nenhum adaptador existe
antes da E8, e o `CapabilityProver` é injetado ([01] §2: o Orchestrator "recebe por
injeção — não os procura"). Com `prover=None`, a guarda **não tem como passar** e devolve
`409` com motivo explícito.

Isso não é uma lacuna: é [ADR-0004] e [ADR-0009] funcionando. *Fail closed* significa que a
ausência de prova é indistinguível de prova negativa, e as duas param a execução. A
alternativa — deixar a transição passar "porque ainda não há o que provar" — seria escrever
hoje o furo que E7 existe para fechar.

**A prova tem duas metades, e as duas são obrigatórias** (E6-AUD-012). `proven = True`
sozinho não prova nada sobre *qual* perfil foi comprovado: o adaptador pode afirmar sucesso
e devolver o hash de um perfil diferente — mais permissivo — do que o aprovado no
`execution_fingerprint`. Antes desta correção a guarda consultava só `proven`, e um
`effective_profile_hash` incompatível, ou ausente, passava. Os quatro desfechos possíveis
são agora três bloqueios e um sucesso:

| `proven` | `effective_profile_hash` | Resultado |
| --- | --- | --- |
| `True` | igual ao requerido | **passa** |
| `True` | diferente | bloqueia |
| `True` | ausente (`None`) | bloqueia |
| `False` | qualquer | bloqueia |
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from app.db.enums import FailureReason, TaskPhase, TaskStatus
from app.orchestrator.errors import InvalidTransition, TransitionGuardFailed

#: Estados terminais e **imutáveis** ([ADR-0008] regra 3): "retomar trabalho significa criar
#: uma nova tarefa". Nenhuma aresta sai daqui.
TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED}
)

#: A tabela de [02] §4, literal. Origem → destinos alcançáveis.
#:
#: `awaiting_approval → awaiting_approval` é uma aresta real e não um engano: é a
#: "aprovação invalidada" do diagrama — o fingerprint divergiu e a task **permanece**
#: aguardando, com `approved_*` limpos. Modelá-la como auto-laço mantém o diagrama e o
#: código com o mesmo número de arestas.
TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.DRAFT: frozenset({TaskStatus.PLANNING, TaskStatus.CANCELLED}),
    TaskStatus.PLANNING: frozenset(
        {
            TaskStatus.AWAITING_APPROVAL,
            TaskStatus.DRAFT,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.AWAITING_APPROVAL: frozenset(
        {
            TaskStatus.APPROVED,
            TaskStatus.DRAFT,
            TaskStatus.AWAITING_APPROVAL,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.APPROVED: frozenset(
        {TaskStatus.EXECUTING, TaskStatus.AWAITING_APPROVAL, TaskStatus.CANCELLED}
    ),
    TaskStatus.EXECUTING: frozenset(
        {
            TaskStatus.DONE,
            TaskStatus.NEEDS_FIX,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.NEEDS_FIX: frozenset(
        {TaskStatus.PLANNING, TaskStatus.APPROVED, TaskStatus.CANCELLED}
    ),
    TaskStatus.DONE: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


def is_terminal(status: TaskStatus) -> bool:
    return status in TERMINAL_STATUSES


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    """A **aresta** existe na tabela de [02] §4? Não diz nada sobre guardas."""
    return target in TRANSITIONS[current]


def require_transition(current: TaskStatus, target: TaskStatus) -> None:
    """Levanta `InvalidTransition` se a aresta não existe. Ver `can_transition`."""
    if not can_transition(current, target):
        if is_terminal(current):
            raise InvalidTransition(
                f"`{current.value}` é terminal e imutável; retomar significa criar uma "
                "nova task ([ADR-0008] regra 3)",
                current=current.value,
                requested=target.value,
            )
        raise InvalidTransition(
            f"transição `{current.value}` → `{target.value}` não existe na máquina de "
            "estados ([02] §4)",
            current=current.value,
            requested=target.value,
        )


# --------------------------------------------------------------------------- capability


@dataclass(frozen=True, slots=True)
class CapabilityProof:
    """O que um adaptador devolve ao **provar** o perfil efetivo ([05], E7+).

    ``proven`` `False` cobre os dois casos que [ADR-0009] trata igual: o adaptador declarou
    um perfil que não satisfaz o requerido, e o adaptador não consegue provar perfil nenhum
    (`enforcement_method = not_enforceable`). Os dois param a execução, e ``reason`` diz
    qual foi.
    """

    proven: bool
    #: O `tool_profile_hash` **efetivamente comprovado** neste momento. É o valor que vai
    #: para `Run.tool_profile_hash` ([02] §8) — distinto do perfil requerido/aprovado que
    #: vive no `execution_fingerprint` da task ([02] §7).
    effective_profile_hash: str | None = None
    reason: str = ""


class CapabilityProver(Protocol):
    """Quem prova o perfil de capability do adaptador. **Injetado, nunca procurado.**

    Nenhuma implementação existe antes da E7/E8. Ver o docstring do módulo para por que a
    ausência desta porta faz `approved → executing` falhar em vez de passar.
    """

    def prove(self, required_profile_hash: str) -> CapabilityProof:  # pragma: no cover — Protocol
        ...


# --------------------------------------------------------------------------- guardas


@dataclass(frozen=True, slots=True)
class EntryGuardFacts:
    """Os fatos que a guarda `approved → executing` de [02] §4 consulta.

    Coletados por quem tem IO (`git_runtime`, o banco) e passados como dados: a guarda em si
    é pura e testável sem repositório nenhum — mesma divisão de `safety.decide_path` sobre
    `PathFacts` ([04] §4).
    """

    fingerprint_matches: bool
    slot_available: bool
    is_git_repo: bool
    head: str | None
    planning_base_commit: str | None
    attempts: int
    max_attempts: int


def check_entry_guard(facts: EntryGuardFacts, *, prover: CapabilityProver | None) -> None:
    """As seis guardas de `approved → executing` ([02] §4). Levanta na primeira que falha.

    A ordem é a de [02] §4 e não é arbitrária: as baratas e determinísticas primeiro, a
    prova de capability por último. Provar capability pode custar um processo de provider;
    fazê-lo antes de conferir que o `HEAD` nem mudou seria pagar caro para descobrir algo
    que uma comparação de string já sabia.
    """
    if not facts.fingerprint_matches:
        raise TransitionGuardFailed(
            "o `execution_fingerprint` recalculado não é mais o aprovado; a aprovação foi "
            "invalidada ([02] §7)",
            guard="fingerprint_still_valid",
        )

    if not facts.slot_available:
        raise TransitionGuardFailed(
            "não há slot livre: `max_parallel_agents = 1` e outra task está executando ([04] §7)",
            guard="slot_available",
        )

    if not facts.is_git_repo:
        raise TransitionGuardFailed(
            "o workspace não é um repositório git; execução bloqueada ([04] §8)",
            guard="workspace_is_git_repo",
        )

    if facts.head is None or facts.head != facts.planning_base_commit:
        raise TransitionGuardFailed(
            "o `HEAD` divergiu do `planning_base_commit` congelado; a execução nunca salta "
            "para o HEAD novo ([02] §6)",
            guard="head_matches_planning_base_commit",
        )

    if facts.attempts >= facts.max_attempts:
        raise TransitionGuardFailed(
            f"`attempts` ({facts.attempts}) atingiu `max_attempts` ({facts.max_attempts}) "
            "([04] §7)",
            guard="attempts_below_max",
        )

    _check_capability(prover)


def _check_capability(prover: CapabilityProver | None) -> None:
    """A guarda de capability, *fail closed* nos **quatro** desfechos ruins.

    Importada de `fingerprint` aqui dentro e não no topo do módulo: o import de topo criaria
    `state_machine → fingerprint`, e `fingerprint` não depende de estado nenhum. Manter a
    dependência local ao ponto de uso deixa a máquina de estados importável sozinha.

    Ver a tabela no docstring do módulo para por que `proven` sozinho não basta.
    """
    from app.orchestrator.fingerprint import tool_profile_hash

    if prover is None:
        raise TransitionGuardFailed(
            "nenhum adaptador está disponível para **provar** o perfil de capability "
            "requerido (inclusive `execute_commands = disabled`). Provar é E7+; até lá a "
            "ausência de prova é tratada como prova negativa — *fail closed* "
            "([ADR-0009], [02] §4)",
            guard="capability_profile_proven",
        )

    required = tool_profile_hash()
    proof = prover.prove(required)

    if not proof.proven:
        raise TransitionGuardFailed(
            f"o adaptador não provou o perfil de capability requerido: {proof.reason}",
            guard="capability_profile_proven",
        )

    # E6-AUD-012. `proven = True` afirma "consegui provar **um** perfil"; qual perfil é o
    # que `effective_profile_hash` diz. Sem esta comparação, um adaptador que comprovasse
    # `execute_commands = enabled` e devolvesse `proven = True` entraria em execução com a
    # capability que [ADR-0009] existe para proibir — a guarda estaria conferindo que o
    # adaptador **respondeu**, não o que ele respondeu.
    if proof.effective_profile_hash is None:
        raise TransitionGuardFailed(
            "o adaptador afirmou ter provado o perfil de capability mas não informou o "
            "`effective_profile_hash`; sem o hash efetivo não há o que comparar com o "
            "perfil aprovado — *fail closed* ([ADR-0009], [02] §7)",
            guard="capability_profile_matches_approved",
        )

    if proof.effective_profile_hash != required:
        raise TransitionGuardFailed(
            "o perfil de capability **efetivamente comprovado** pelo adaptador não é o "
            "perfil requerido/aprovado no `execution_fingerprint` ([02] §7): a execução "
            "usaria capabilities diferentes das que o humano aprovou",
            guard="capability_profile_matches_approved",
        )


def check_approval_guard(*, stale_entry_ids: Iterable[str], risk_is_high: bool) -> None:
    """A guarda `awaiting_approval → approved` de [02] §4.

    O fingerprint é conferido pelo chamador (que devolve `ApprovalFingerprintMismatch`, com
    o diagnóstico de campo divergente, e não um `TransitionGuardFailed` genérico). O que
    sobra é a segunda metade: "nenhuma entrada `stale` selecionada se `risk = high`".

    Só em `risk = high`. Em `low`/`medium` uma entrada `stale` é um aviso que a UI mostra
    ([06] §4), não um impedimento — bloquear sempre transformaria qualquer edição não
    commitada num bloqueio de aprovação, e [03] §3 já registra a divergência sem exigir que
    ela pare o fluxo.
    """
    stale = tuple(stale_entry_ids)
    if risk_is_high and stale:
        raise TransitionGuardFailed(
            f"`risk = high` com {len(stale)} entrada(s) de contexto `stale` selecionada(s); "
            "reverifique o contexto antes de aprovar ([02] §4)",
            guard="no_stale_entry_when_high_risk",
        )


def check_needs_fix_guard(
    *,
    attempts: int,
    max_attempts: int,
    stale_entry_ids: Iterable[str],
    risk_is_high: bool,
) -> None:
    """A guarda `needs_fix → approved` de [02] §4: `attempts < max_attempts` e contexto
    reverificado.

    A aresta existe na tabela desde sempre; o **comando** não a realizava (E6-AUD-010), e o
    `approve` fixava `awaiting_approval` como origem do *compare-and-set* — uma nova
    tentativa legítima recebia `409 concurrent_task_update`, afirmando uma concorrência que
    não existia.

    As duas metades da guarda são distintas do caminho `awaiting_approval → approved`:

    * **`attempts`** — em `awaiting_approval` a task ainda não tentou nada, e o teto é
      conferido só na entrada de execução. Aqui ela já falhou pelo menos uma vez, e aprovar
      uma tentativa que a guarda de entrada recusaria em seguida seria empurrar o humano
      para um `409` que se podia dar agora, com o motivo certo;
    * **contexto reverificado** — `awaiting_approval → approved` confere os estados
      **congelados no manifest** (a seleção acabou de acontecer; reler o registry
      responderia outra pergunta). Em `needs_fix` o manifest é antigo: houve uma execução
      inteira entre o congelamento e este momento. "Reverificado" só pode significar o
      estado de **agora**, contra o `planning_base_commit` congelado — que é o que o
      chamador reverifica antes de passar `stale_entry_ids` para cá.
    """
    if attempts >= max_attempts:
        raise TransitionGuardFailed(
            f"`attempts` ({attempts}) atingiu `max_attempts` ({max_attempts}): uma nova "
            "tentativa a partir de `needs_fix` não é possível; replaneje ou cancele "
            "([02] §4)",
            guard="attempts_below_max",
        )

    check_approval_guard(stale_entry_ids=stale_entry_ids, risk_is_high=risk_is_high)


# --------------------------------------------------------------------------- aplicação


def transition_fields(
    target: TaskStatus,
    *,
    phase: TaskPhase | None = None,
    failure_reason: FailureReason | None = None,
) -> dict[str, Any]:
    """Os campos que **sempre** acompanham uma mudança de `status`, já coerentes.

    Impõe as duas invariantes de [02] §3 montando os valores a partir do destino:

    * fora de `executing`, `phase` é forçado a `None` — não "mantido";
    * fora de `failed`, `failure_reason` é forçado a `None`. [ADR-0008] correção 1B.1:
      "`failure_reason` existe apenas em falhas reais — nunca em `cancelled`, nunca em
      `done`". Uma task cancelada que carregasse o `failure_reason` de uma tentativa
      anterior envenenaria a taxa de sucesso do benchmark, que é exatamente o defeito que
      AUD-011 fechou.

    Levanta `ValueError` quando o destino **exige** o campo e ele não veio: é erro de
    programação do chamador, não condição de runtime.
    """
    if target is TaskStatus.EXECUTING and phase is None:
        raise ValueError("`executing` exige `phase` ([02] §3)")
    if target is TaskStatus.FAILED and failure_reason is None:
        raise ValueError("`failed` exige `failure_reason` ([02] §3)")

    return {
        "status": target,
        "phase": phase if target is TaskStatus.EXECUTING else None,
        "failure_reason": failure_reason if target is TaskStatus.FAILED else None,
    }


__all__ = [
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "CapabilityProof",
    "CapabilityProver",
    "EntryGuardFacts",
    "can_transition",
    "check_approval_guard",
    "check_entry_guard",
    "check_needs_fix_guard",
    "is_terminal",
    "require_transition",
    "transition_fields",
]
