"""Elegibilidade de planejamento — o que `POST /plan` aceitaria **agora** (E6-CONS4).

Uma leitura, não um comando: não transiciona, não escreve no banco, não grava artefato,
não registra `SafetyEvent` e não executa git além de `git_runtime.probe_head` (dois
`rev-parse`). Por isso vive fora de `execution_manager`, onde toda função pública com
`session` é um `@command`.

## Por que existe

A orientação "planeje agora" já foi calculada em quatro lugares — o texto de
`GET /context`, `canPlan` do frontend, `StalePlanNotice` e o banner de `requires_replan` —,
cada um com uma parte das guardas. Cada rodada de auditoria (CONS, CONS2, CONS3, CONS4)
achou a guarda que faltava num deles. A resposta estrutural é ter uma derivação só, que
chama **as mesmas funções** que o planejamento usa para recusar (`planner.
workspace_planning_blocker`, `planner.git_planning_blocker`, `planner.
invalid_test_config_blocker`). O frontend não reimplementa nenhuma delas.

## A representação

* ``transition`` — a aresta, pela tabela de estados: ``allowed`` (`draft`, `needs_fix`),
  ``after_reject`` (`awaiting_approval`: `reject` volta a `draft`, de onde se planeja) ou
  ``forbidden`` (todo o resto). `planning → draft` existe na tabela, mas é o rollback do
  gerenciador, não uma decisão do humano — `planning` é ``forbidden``.
* ``checked`` — as pré-condições de workspace **e git** foram avaliadas. É `False` quando
  ``transition == forbidden`` (nada é consultado) e quando ``after_reject`` com plano ainda
  vigente (`approval_state = pending`): replanejar não é o próximo passo ali, e o git fica
  **adiado** — as pré-condições baratas (arquivamento, `test_config`) continuam listadas.
* ``blockers`` — os bloqueios **encontrados**, na ordem de execução de `plan()`. Com
  ``checked = False`` a lista é **parcial**: vazia não significa "nada falta".
* ``eligible`` — ``transition == allowed`` **e** ``checked`` **e** nenhum bloqueio. É o
  único campo que autoriza oferecer "Planejar". ``checked = False`` com lista vazia nunca é
  elegibilidade.

## Não é garantia

É uma fotografia. O workspace pode ser arquivado, o `.git` apagado ou o git parar de
responder entre esta leitura e o `POST /plan`, que repete todas as guardas e continua sendo
a autoridade. Há também recusas que não se pode prever sem executar o planejamento —
concorrência (`concurrent_task_update`), árvore ilegível durante a seleção, falha de IO ao
gravar o artefato —, e por isso ``eligible`` não promete `200`.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.enums import TaskStatus
from app.db.models import WorkspaceTask
from app.orchestrator import planner as _planner
from app.orchestrator.execution_manager import APPROVAL_STATE_REQUIRES_REPLAN, approval_state
from app.orchestrator.state_machine import can_transition
from app.workspace.service import get_workspace

TRANSITION_ALLOWED = "allowed"
TRANSITION_AFTER_REJECT = "after_reject"
TRANSITION_FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class PlanningEligibility:
    transition: str
    checked: bool
    blockers: tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        return self.transition == TRANSITION_ALLOWED and self.checked and not self.blockers

    def as_payload(self) -> dict[str, object]:
        return {
            "transition": self.transition,
            "checked": self.checked,
            "blockers": list(self.blockers),
            "eligible": self.eligible,
        }


def planning_transition(task: WorkspaceTask) -> str:
    """A componente de aresta. Pura; não consulta workspace nem git."""
    if can_transition(task.status, TaskStatus.PLANNING):
        return TRANSITION_ALLOWED
    if task.status is not TaskStatus.PLANNING and can_transition(task.status, TaskStatus.DRAFT):
        return TRANSITION_AFTER_REJECT
    return TRANSITION_FORBIDDEN


def planning_eligibility(session: Session, task: WorkspaceTask) -> PlanningEligibility:
    """Ver o docstring do módulo. **Somente leitura.**"""
    transition = planning_transition(task)
    if transition == TRANSITION_FORBIDDEN:
        return PlanningEligibility(transition=transition, checked=False)

    workspace = get_workspace(session, task.workspace_id)
    replanning_is_next = (
        transition == TRANSITION_ALLOWED or approval_state(task) == APPROVAL_STATE_REQUIRES_REPLAN
    )

    found: list[str | None] = [_planner.workspace_planning_blocker(workspace)]
    if replanning_is_next:
        found.append(_planner.git_planning_blocker(workspace))
    found.append(_planner.invalid_test_config_blocker(workspace))

    return PlanningEligibility(
        transition=transition,
        checked=replanning_is_next,
        blockers=tuple(blocker for blocker in found if blocker is not None),
    )


__all__ = [
    "TRANSITION_AFTER_REJECT",
    "TRANSITION_ALLOWED",
    "TRANSITION_FORBIDDEN",
    "PlanningEligibility",
    "planning_eligibility",
    "planning_transition",
]
