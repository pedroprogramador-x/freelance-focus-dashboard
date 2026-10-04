"""Execution Manager — **o único lugar que transiciona uma `WorkspaceTask`**.

[01] §2 e [ADR-0008] regra 4: "só o Execution Manager transiciona. A API expõe comandos
(`plan`, `approve`, `reject`, `cancel`), nunca escrita direta de `status`." Este módulo é
também o único que escreve `SafetyEvent` ([01] §2).

## *Compare-and-set*, e por que `UPDATE … WHERE` e não `session.flush()`

[02] §4: "transição por *compare-and-set* em `(id, status, version)`. Quem commitar
primeiro vence; o perdedor vira no-op."

A forma ingênua — mudar o atributo do objeto ORM e dar `flush` — **não** é
compare-and-set: o SQLAlchemy emite `UPDATE … WHERE id = ?`, que sobrescreve
incondicionalmente. Dois `cancel` concorrentes, ou um `cancel` correndo com uma conclusão,
os dois teriam sucesso e o último a commitar venceria silenciosamente.

`_compare_and_set` emite `UPDATE … WHERE id = ? AND status = ? AND version = ?` e confere
o `rowcount`. Zero linhas significa que alguém mais transicionou entre a leitura e a
escrita: o perdedor recebe `ConcurrentTaskUpdate` (409) em vez de sobrescrever. O `version`
é incrementado na mesma sentença, então nenhuma janela existe entre conferir e incrementar.

`session.expire` depois do `UPDATE` é obrigatório: o `UPDATE` passa **por fora** do
*identity map*, e sem expirar o objeto em memória continuaria mostrando o estado antigo.

### O CAS em SQL não cobre sozinho a semântica do driver (E6-AUD-004)

`UPDATE … WHERE (id, status, version)` responde "alguém transicionou antes de mim?" **se o
`UPDATE` chegar a rodar**. Sob WAL, uma sessão que leu a linha antes de outra commitar
carrega um *snapshot* antigo, e o SQLite recusa a escrita com `SQLITE_BUSY_SNAPSHOT` —
antes de existir um `rowcount` para conferir. O perdedor de uma corrida **real** recebia,
por isso, um `OperationalError` não tratado, que a camada HTTP traduzia em
`500 internal_error`: um conflito de domínio perfeitamente previsto por [02] §4 aparecendo
como defeito do backend.

`_compare_and_set` reconhece esse conjunto fechado de códigos, faz `rollback` (o snapshot
velho precisa ser descartado antes de qualquer releitura) e levanta o mesmo
`ConcurrentTaskUpdate` do caminho `rowcount = 0`. Os dois são o mesmo fato — "quem commitou
primeiro venceu" — descoberto em dois momentos diferentes. **Nenhum outro erro de banco é
reinterpretado**: um `IntegrityError` de `CHECK` violado continua subindo como o defeito
que é, e um `OperationalError` que não seja um dos códigos de conflito também.

**O CAS não é o único lugar que escreve** (E6-AUD2-002). O `plan` faz um trabalho longo
entre os dois *compare-and-set*, e esse trabalho escreve: a verificação de contexto grava
vereditos, `freeze_manifest` grava a linha do manifest. Um `cancel` que commite no meio
disso invalida o snapshot da sessão que planeja, e o conflito estoura na **primeira escrita
seguinte** — dentro de `plan_task`, longe do CAS. O tratamento da primeira rodada cobria só
o CAS, então esse interleaving continuava chegando ao cliente como `500 internal_error`.

`_abort_planning` aplica a mesma classificação (`_is_write_conflict`) ao erro que recebe:
reconhecido, ele vira `ConcurrentTaskUpdate`; não reconhecido, sobe como está. O `rollback`
que ela já fazia antes de reler é o mesmo "descarte o snapshot velho" — e é o que faz a
releitura enxergar a vencedora e não sobrescrevê-la.

## O que esta fase deliberadamente não faz

Nenhum `Run` é criado. Nenhum worktree é criado. Nenhum provider é invocado. `POST
/approve` produz uma task `approved`, e é onde a E6 para — a entrada em `executing` é
recusada por desenho (ver `state_machine`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import wraps
from inspect import signature
from pathlib import Path
from typing import Any, ParamSpec, TypeVar, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.conflicts import is_write_conflict
from app.db.enums import (
    ComplexityLevel,
    ExecutionMode,
    FailureReason,
    RiskLevel,
    RiskSource,
    SafetyDecisionKind,
    SafetyEventKind,
    TaskStatus,
    WorkspaceStatus,
)
from app.db.models import ContextManifest, DevWorkspace, SafetyEvent, WorkspaceTask
from app.git_runtime import preflight
from app.orchestrator.analyzer import AnalyzerEnrichmentPort
from app.orchestrator.developer_binding import DeveloperBindingResolver
from app.orchestrator.errors import (
    ApprovalFingerprintMismatch,
    ConcurrentTaskUpdate,
    InvalidTask,
    OrchestratorError,
    TaskNotFound,
    TransitionGuardFailed,
    WorkspaceNotPlannable,
)
from app.orchestrator.fingerprint import diverged_fields
from app.orchestrator.planner import (
    PLANNING_BLOCKER_WORKSPACE_ARCHIVED,
    PlanResult,
    build_fingerprint_parts,
    decision_from_task,
    plan_task,
    planning_blocker_error,
    reverify_context,
    stale_manifest_entry_ids,
    workspace_planning_blocker,
)
from app.orchestrator.state_machine import (
    CapabilityProver,
    EntryGuardFacts,
    check_approval_guard,
    check_entry_guard,
    check_needs_fix_guard,
    require_transition,
    transition_fields,
)
from app.safety import redact
from app.safety.capability_verification import CapabilityBinding
from app.workspace.service import get_workspace

_MAX_TITLE_LENGTH = 255

#: Estados em que uma task é considerada "em voo" para a contagem de slot de [04] §7
#: (`max_parallel_agents = 1`). Só `executing` ocupa slot: `approved` é a **fila**
#: ([ADR-0008]: "com `max_parallel_agents = 1`, uma tarefa aprovada espera outra terminar.
#: É o estado de fila").
_SLOT_OCCUPYING_STATUSES = (TaskStatus.EXECUTING,)


def _utcnow() -> datetime:
    return datetime.now(UTC)


_P = ParamSpec("_P")
_R = TypeVar("_R")

#: Nomes dos comandos que passaram pela unidade de trabalho. Preenchido pelo decorador e
#: conferido por `test_architecture`: um comando novo que esqueça o decorador não entra
#: aqui, e a suíte quebra em vez de o esquecimento virar um `500` em produção.
GUARDED_COMMANDS: set[str] = set()


def _translate_write_conflict(
    error: BaseException, task_id: str | None
) -> ConcurrentTaskUpdate | None:
    """O erro é um conflito de escrita reconhecido? Devolve a tradução, ou `None`.

    Um só ponto de tradução para os três lugares que precisam dela — o *compare-and-set*,
    a unidade de trabalho de comando e o abort do planejamento —, sobre o classificador
    único de `db/conflicts.py`. O que ele não reconhece devolve `None` e sobe como está.
    """
    if not is_write_conflict(error):
        return None
    alvo = f"a task '{task_id}'" if task_id else "esta operação"
    return ConcurrentTaskUpdate(
        f"{alvo} perdeu uma corrida de escrita (conflito de snapshot do SQLite): quem "
        "commitou primeiro venceu e esta operação virou no-op ([02] §4)"
    )


@contextmanager
def _write_conflict_boundary(session: Session, task_id: str | None) -> Iterator[None]:
    """A unidade de trabalho de [04] §5 para conflito de escrita. Ver `command`.

    Os quatro passos, nesta ordem e sem atalho:

    1. o corpo do comando roda;
    2. as escritas acontecem dentro dele — `flush` de evento, `flush` de reverificação,
       `UPDATE` do CAS;
    3. conflito **reconhecido** → `rollback` primeiro, tradução depois. O `rollback` vem
       antes de qualquer releitura porque a sessão perdedora ainda segura o *snapshot* que
       o SQLite recusou, e ler antes dele responderia com o estado que a vencedora já
       substituiu;
    4. qualquer outra falha operacional sobe **sem mascaramento**.
    """
    try:
        yield
    except Exception as error:
        conflito = _translate_write_conflict(error, task_id)
        if conflito is None:
            raise
        session.rollback()
        raise conflito from error


def command(operation: Callable[_P, _R]) -> Callable[_P, _R]:
    """Marca uma função pública deste módulo como **comando** do Execution Manager.

    Todo comando escreve, e toda escrita pode perder uma corrida. Até a E6-AUD3 só o
    `plan` tinha tratamento, porque `_abort_planning` — que o implementava — só participa
    dele: o `INSERT safety_event` da recusa de fingerprint e o `UPDATE
    context_registry_entry` da reverificação de `needs_fix`, ambos dentro de `approve` e
    ambos **anteriores** ao *compare-and-set* final, continuavam produzindo `500`
    (E6-AUD3-002).

    O decorador é o que torna a cobertura verificável em vez de lembrada: `test_architecture`
    enumera as funções públicas que recebem `session` e exige o decorador em cada uma, de
    modo que o comando da E7 nasce coberto ou quebra a suíte.

    ``session`` é o primeiro parâmetro de todo comando; ``task_id`` existe na maioria deles
    e é lido **por nome**, via a assinatura ligada. Ler por posição parecia mais simples e
    estava errado: `execute_purge(session, store, task_id, token)` tem o `store` no segundo
    lugar, e o `task_id` sumia da mensagem sem que nada falhasse. A assinatura é resolvida
    uma vez, na decoração.

    A ausência de `task_id` (o caso de `reconcile_on_startup`) não muda nada do fluxo — só o
    texto do erro, que passa a falar de "esta operação".
    """
    assinatura = signature(operation)

    @wraps(operation)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        ligados = assinatura.bind_partial(*args, **kwargs).arguments
        session = cast("Session", ligados["session"])
        task_id = ligados.get("task_id")
        with _write_conflict_boundary(session, cast("str | None", task_id)):
            return operation(*args, **kwargs)

    GUARDED_COMMANDS.add(operation.__name__)
    return wrapper


# --------------------------------------------------------------------------- leitura


def get_task(session: Session, task_id: str) -> WorkspaceTask:
    task = session.get(WorkspaceTask, task_id)
    if task is None:
        raise TaskNotFound(f"task '{task_id}' não encontrada")
    return task


def list_tasks(
    session: Session, workspace_id: str, *, status: TaskStatus | None = None
) -> list[WorkspaceTask]:
    """Tasks de um workspace, mais recentes primeiro.

    `get_workspace` roda primeiro para que um `workspace_id` inexistente devolva `404` em
    vez de uma lista vazia — os dois são estados distintos e a UI precisa distingui-los.
    """
    get_workspace(session, workspace_id)

    statement = select(WorkspaceTask).where(WorkspaceTask.workspace_id == workspace_id)
    if status is not None:
        statement = statement.where(WorkspaceTask.status == status)

    # `id` como desempate: `created_at` tem resolução de microssegundo mas duas tasks
    # criadas na mesma transação podem empatar, e a ordem do `SELECT` não é garantida.
    ordered = statement.order_by(WorkspaceTask.created_at.desc(), WorkspaceTask.id)
    return list(session.scalars(ordered))


#: Onde a task está no **fluxo de aprovação**, derivado das colunas de [02] §3. Não é um
#: estado novo da máquina de [02] §4 e não é persistido: é uma leitura, e existe porque
#: `status` sozinho responde a pergunta errada em um caso (E6-AUD2-004).
#:
#: `awaiting_approval` significa duas situações que exigem ações **opostas** do humano:
#:
#: * há um plano vigente e falta aprová-lo — `pending`;
#: * havia um plano, a guarda de entrada o invalidou porque o `HEAD` mudou, e o candidato
#:   foi limpo. O `plan` que a task ainda exibe é **histórico**: aprovar responde `409`, e
#:   `POST /plan` também, porque `awaiting_approval → planning` não é aresta. O caminho
#:   real é `reject` → `draft` → `plan` — `requires_replan`.
#:
#: Sem essa distinção, [06] §4 não tem como oferecer o botão certo, e a mensagem de erro
#: acaba instruindo uma ação que responde `409` — que foi exatamente o que a auditoria
#: encontrou.
APPROVAL_STATE_NOT_PLANNED = "not_planned"
APPROVAL_STATE_PENDING = "pending"
APPROVAL_STATE_APPROVED = "approved"
APPROVAL_STATE_REQUIRES_REPLAN = "requires_replan"

APPROVAL_STATES: tuple[str, ...] = (
    APPROVAL_STATE_NOT_PLANNED,
    APPROVAL_STATE_PENDING,
    APPROVAL_STATE_APPROVED,
    APPROVAL_STATE_REQUIRES_REPLAN,
)


#: Os dois estados de [02] §4 em que existe uma decisão de aprovação **pendente**. Fora
#: deles não há o que aprovar, e por isso não há subestado de aprovação a distinguir.
_DECIDABLE_STATUSES = (TaskStatus.AWAITING_APPROVAL, TaskStatus.NEEDS_FIX)


def approval_state(task: WorkspaceTask) -> str:
    """Ver `APPROVAL_STATES`. **Puro**, derivado de colunas, nada persistido.

    A ordem dos testes importa, e cada um fecha um caso que os outros confundiriam:

    1. `approved_at` é a **única** fonte de verdade de "aprovada" ([02] §3), e vem primeiro;
    2. fora de `awaiting_approval`/`needs_fix` não há decisão pendente. O teste de status
       precisa vir antes do de candidato, porque `reject` limpa `approved_manifest_id` e
       **deixa `plan_hash`** — uma task rejeitada tem plano histórico sem candidato, que é
       a assinatura exata de `requires_replan`. Ela não é: o caminho a partir de `draft` é
       `POST /plan`, direto, e dizer "rejeite e replaneje" a quem já rejeitou seria repetir
       na projeção o mesmo tipo de instrução impossível que E6-AUD2-004 encontrou;
    3. dentro daqueles dois estados, candidato ausente só pode ser o rastro de
       `_invalidate_approval_on_entry` — nenhum outro caminho produz plano sem candidato ali.
    """
    if task.approved_at is not None:
        return APPROVAL_STATE_APPROVED
    if task.status not in _DECIDABLE_STATUSES:
        return APPROVAL_STATE_NOT_PLANNED
    if task.approved_manifest_id is None:
        return APPROVAL_STATE_REQUIRES_REPLAN
    return APPROVAL_STATE_PENDING


#: Se o `plan` que a task exibe está **em vigor** (E6-CONS4, decisão D2-A). Derivado de
#: colunas, nada persistido — o mesmo desenho de `approval_state`.
#:
#: O Planner escreve `plan` e `approved_manifest_id` juntos, e nenhuma transição apaga
#: `plan`: `reject` limpa só os campos candidatos, e o rollback recuperável de
#: `_abort_planning` não limpa nada. D2-A preserva os dois de propósito, como registro do
#: que foi planejado — então a tela precisa saber que eles não valem mais. O manifest, quando
#: existe, pertence a esse mesmo plano e tem o mesmo `standing`.
PLAN_STANDING_NONE = "none"
PLAN_STANDING_CURRENT = "current"
PLAN_STANDING_HISTORICAL = "historical"
PLAN_STANDING_FINAL = "final"

_TERMINAL = (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)
_NOT_IN_FORCE = (TaskStatus.DRAFT, TaskStatus.PLANNING)


def plan_standing(task: WorkspaceTask) -> str:
    """`none` · `current` · `historical` · `final`. **Puro**.

    * sem `plan` → `none`;
    * terminal → `final`: é o registro do que a task chegou a planejar, não um plano a
      seguir nem a refazer ([ADR-0008] regra 3);
    * `draft`/`planning` → `historical`: em `draft` nenhum plano está em vigor (a task foi
      rejeitada, ou um replan recuperável a devolveu); em `planning` o plano exibido é o
      anterior, que o replanejamento em andamento vai substituir;
    * `requires_replan` → `historical` (E6-AUD2-004);
    * o resto (`awaiting_approval` pendente, `approved`, `executing`, `needs_fix`) →
      `current`.
    """
    if task.plan is None:
        return PLAN_STANDING_NONE
    if task.status in _TERMINAL:
        return PLAN_STANDING_FINAL
    if task.status in _NOT_IN_FORCE:
        return PLAN_STANDING_HISTORICAL
    if approval_state(task) == APPROVAL_STATE_REQUIRES_REPLAN:
        return PLAN_STANDING_HISTORICAL
    return PLAN_STANDING_CURRENT


def latest_manifest(session: Session, task: WorkspaceTask) -> ContextManifest | None:
    """O manifest que este planejamento congelou.

    Lido por `approved_manifest_id` — que o Planner preenche já no plano, com
    `approved_at = NULL` (ver `planner.plan_task`). Não é uma busca pelo mais recente da
    task: replanejar cria um manifest novo e reaponta o campo, e o recálculo do fingerprint
    tem de usar **aquele** manifest, não o último que existir na tabela.
    """
    if task.approved_manifest_id is None:
        return None
    return session.get(ContextManifest, task.approved_manifest_id)


# --------------------------------------------------------------------------- escrita


def _compare_and_set(
    session: Session,
    task: WorkspaceTask,
    *,
    expected_status: TaskStatus,
    expected_version: int,
    values: dict[str, Any],
) -> None:
    """`UPDATE … WHERE (id, status, version)` + `version = version + 1`. Ver o docstring.

    Levanta `ConcurrentTaskUpdate` quando nenhuma linha casou — o lado perdedor de [02] §4.
    """
    statement = (
        update(WorkspaceTask)
        .where(
            WorkspaceTask.id == task.id,
            WorkspaceTask.status == expected_status,
            WorkspaceTask.version == expected_version,
        )
        .values(**values, version=WorkspaceTask.version + 1)
    )

    try:
        # `cast` e não `# type: ignore`: `Session.execute` é tipado como `Result[Any]` no
        # stub genérico, mas um `UPDATE` sempre devolve um `CursorResult` — que é quem tem
        # `rowcount`. O `cast` documenta *por que* o atributo existe em vez de silenciar a
        # checagem no ponto onde ela importa.
        result = cast("CursorResult[Any]", session.execute(statement))
    except OperationalError as error:
        # E6-AUD-004: o conflito de *snapshot* acontece **antes** de haver `rowcount`. Ver o
        # docstring do módulo. A `_write_conflict_boundary` do comando pegaria este erro de
        # todo modo; traduzi-lo aqui preserva o `id` da task e a mensagem específica de
        # "transicionada durante a escrita", que é diagnóstico melhor do que o genérico.
        conflito = _translate_write_conflict(error, task.id)
        if conflito is None:
            raise
        # O `rollback` vem primeiro e é obrigatório: a sessão perdedora ainda segura um
        # snapshot que o SQLite já recusou, e qualquer releitura antes dele responderia com
        # o estado velho.
        session.rollback()
        raise conflito from error

    if result.rowcount != 1:
        raise ConcurrentTaskUpdate(
            f"a task '{task.id}' mudou de estado desde a leitura (esperado "
            f"`{expected_status.value}` v{expected_version}); esta operação virou no-op "
            "([02] §4)"
        )

    # O `UPDATE` passou por fora do *identity map*: sem expirar, o objeto em memória
    # continuaria mostrando o estado anterior para quem o ler depois nesta mesma sessão.
    session.expire(task)


def _commit_audit_trail(session: Session) -> None:
    """Torna durável o que foi escrito antes de uma exceção de domínio subir.

    A `session_scope` da camada HTTP faz `rollback` em **qualquer** exceção — correto para
    erro de invariante, e errado para os dois casos em que o "erro" é o relato de uma
    decisão que já foi tomada e registrada:

    * aprovação invalidada — uma transição real de [02] §4, mais o `SafetyEvent` que
      [ADR-0008] regra 5 exige;
    * guarda de **aprovação** recusada — contexto `stale` sob `risk = high`, ou teto de
      tentativas esgotado em `needs_fix`. É o `SafetyEvent` de [02] §4 ("guardas que falham
      geram `409` […] e `SafetyEvent` quando a causa é política"); este caminho ficou de
      fora da primeira correção e é o E6-AUD-002;
    * guarda de **entrada** recusada — o mesmo `SafetyEvent`, e agora também a transição
      `approved → awaiting_approval` que [02] §4 prevê e que não estava sendo persistida
      (E6-AUD-001);
    * **entrada em `planning`** — de natureza diferente dos três acima, e é o E6-AUD-003:
      não é uma trilha que precisa sobreviver a uma exceção, é um estado que precisa existir
      no banco **antes** de o trabalho longo começar. Sem o commit, `planning` só existia
      dentro de uma transação aberta, e um crash real do processo (não uma exceção Python)
      fazia o SQLite descartá-la inteira: a task voltava a ser indistinguível de `draft` e a
      `reconcile_on_startup` não encontrava nada a recuperar — precisamente o motivo pelo
      qual [ADR-0008] introduziu esse estado.

    Em todos, a trilha de [02] §12 é **append-only e não cascateada**: ela existe
    precisamente para sobreviver ao que aconteceu em volta dela. Um `rollback` posterior
    sobre uma sessão já commitada não desfaz nada — ele apenas encerra a transação nova que
    o SQLAlchemy abriu depois do commit.
    """
    session.commit()


def record_safety_event(
    session: Session,
    *,
    kind: SafetyEventKind,
    decision: SafetyDecisionKind,
    rule_id: str,
    subject: str,
    workspace_id: str | None = None,
    task_id: str | None = None,
    detail: str | None = None,
) -> SafetyEvent:
    """Acrescenta uma linha à trilha append-only de [02] §12.

    `subject` e `detail` passam pelo redator **aqui**, no ponto de escrita, e não em quem
    chama: [02] §12 exige que o campo esteja "já redigido" no banco, e centralizar a
    redação num único ponto é o que impede um chamador novo de esquecê-la.
    """
    event = SafetyEvent(
        workspace_id=workspace_id,
        task_id=task_id,
        kind=kind,
        decision=decision,
        rule_id=rule_id,
        subject=redact(subject),
        detail=redact(detail) if detail else None,
    )
    session.add(event)
    session.flush()
    return event


@command
def create_task(
    session: Session,
    workspace_id: str,
    *,
    title: str,
    goal: str,
    execution_mode: ExecutionMode = ExecutionMode.ORCHESTRATED,
    benchmark_group_id: str | None = None,
) -> WorkspaceTask:
    """Cria uma task em `draft`. Nada é analisado nem planejado aqui.

    Uma task nasce `risk = low` / `complexity = low` / `risk_source = hard_rule`: as colunas
    são `NOT NULL` e nenhuma análise rodou ainda. Os valores reais chegam no `plan`, onde o
    piso de fallback de [03] §5 garante que uma task não analisada **nunca** fique `low` — e
    é por isso que um default `low` aqui não é um buraco: em `draft` ele não governa nada, e
    a primeira coisa que o `plan` faz é sobrescrevê-lo.
    """
    workspace = get_workspace(session, workspace_id)

    if workspace.status is WorkspaceStatus.ARCHIVED:
        raise WorkspaceNotPlannable(
            f"o workspace '{workspace.name}' está arquivado e não aceita novas tasks ([02] §1)",
            reason=PLANNING_BLOCKER_WORKSPACE_ARCHIVED,
        )

    clean_title = title.strip()
    if not clean_title:
        raise InvalidTask("title não pode ser vazio")
    if len(clean_title) > _MAX_TITLE_LENGTH:
        raise InvalidTask(f"title excede {_MAX_TITLE_LENGTH} caracteres")
    if not goal.strip():
        raise InvalidTask("goal não pode ser vazio")

    task = WorkspaceTask(
        workspace_id=workspace.id,
        title=clean_title,
        goal=goal.strip(),
        status=TaskStatus.DRAFT,
        risk=RiskLevel.LOW,
        complexity=ComplexityLevel.LOW,
        risk_source=RiskSource.HARD_RULE,
        execution_mode=execution_mode,
        benchmark_group_id=benchmark_group_id,
        agents=[],
    )
    session.add(task)
    session.flush()
    return task


@command
def plan(
    session: Session,
    task_id: str,
    *,
    candidate_paths: Sequence[str] = (),
    artifacts_dir: Path,
    enrichment_port: AnalyzerEnrichmentPort | None = None,
    developer_binding_resolver: DeveloperBindingResolver | None = None,
) -> PlanResult:
    """`draft | needs_fix → planning → awaiting_approval` ([02] §4).

    E8.1: ``developer_binding_resolver`` é a porta tier → binding concreto, fornecida pelo
    chamador confiável (composition root; na E8.1, só testes). `None` mantém
    `developer_binding: null`. Os três comandos que montam fingerprint — `plan`, `approve`
    e `start_execution` — recebem a mesma porta e passam pelo mesmo
    `build_fingerprint_parts`.

    ## `planning` é durável, e é por isso que o commit fica no meio (E6-AUD-003)

    [ADR-0008] criou este estado porque "planejar chama LLM e leva tempo. Sem esse estado,
    um crash durante o plano deixa a tarefa indistinguível de `draft`". Separar os dois
    *compare-and-set* **não** era suficiente para isso: os dois rodavam dentro da mesma
    transação, e uma transação não commitada não existe para ninguém de fora. Uma leitura
    concorrente via `draft` durante todo o planejamento, e um crash real do processo — sinal
    do SO, `os._exit`, queda de energia — fazia o SQLite descartar a transação inteira: a
    task voltava a `draft` e `reconcile_on_startup` não tinha o que recuperar. O estado
    existia no código e não no banco.

    O commit logo depois da entrada resolve os dois de uma vez, e tem um efeito colateral
    desejável: a transação de escrita fecha **antes** da análise e da seleção, em vez de
    segurar o lock do banco durante toda a operação longa.

    ## Os quatro desfechos do trabalho longo

    * **sucesso** → `planning → awaiting_approval`, o caminho normal;
    * **erro de domínio** (`OrchestratorError`) → `planning → draft`. É o "erro recuperável"
      de [02] §4: sem git, `test_config` malformado, `source_ref` inválido. O usuário
      corrige a causa e replaneja;
    * **erro inesperado** → `planning → failed(internal_error)`. É o "erro de provider ·
      limite" de [02] §4. Não sabemos o que houve, e devolver a task para `draft`
      afirmaria que replanejar resolve — o que ninguém verificou;
    * **a task saiu de `planning` no meio** → nada. Outra transação venceu (um `cancel`, ou
      uma reconciliação de startup), e o estado é dela.

    Em todos os casos de erro, o trabalho parcial é revertido e a transição de saída é
    commitada **antes** de a exceção original subir — a task nunca fica presa em `planning`
    por uma falha que o processo viu.
    """
    task = get_task(session, task_id)
    workspace = get_workspace(session, task.workspace_id)

    # A mesma função que `eligibility.planning_eligibility` reporta (E6-CONS4).
    blocker = workspace_planning_blocker(workspace)
    if blocker is not None:
        raise planning_blocker_error(workspace, blocker)

    require_transition(task.status, TaskStatus.PLANNING)
    _compare_and_set(
        session,
        task,
        expected_status=task.status,
        expected_version=task.version,
        values=transition_fields(TaskStatus.PLANNING),
    )
    # E6-AUD-003. Ver o docstring: sem este commit, `planning` não existe fora desta
    # transação e um crash real do processo a apaga.
    _commit_audit_trail(session)

    try:
        result = plan_task(
            session,
            task,
            workspace,
            candidate_paths=list(candidate_paths),
            artifacts_dir=artifacts_dir,
            enrichment_port=enrichment_port,
            developer_binding_resolver=developer_binding_resolver,
        )

        require_transition(TaskStatus.PLANNING, TaskStatus.AWAITING_APPROVAL)
        _compare_and_set(
            session,
            task,
            expected_status=TaskStatus.PLANNING,
            expected_version=task.version,
            values=transition_fields(TaskStatus.AWAITING_APPROVAL),
        )
    except Exception as error:
        # `Exception` e não `BaseException`: um `KeyboardInterrupt` ou um `SystemExit` aqui
        # é o processo indo embora, e o caso "o processo foi embora" é exatamente o que
        # `reconcile_on_startup` cobre — tentar classificá-lo aqui competiria com ela.
        translated = _abort_planning(session, task_id, error)
        if translated is not None:
            raise translated from error
        raise

    return result


def _abort_planning(session: Session, task_id: str, error: Exception) -> OrchestratorError | None:
    """Tira a task de `planning` depois de o planejamento falhar. Ver o docstring de `plan`.

    Devolve o erro de **domínio** que deve subir no lugar do original, ou `None` quando o
    original já é o certo. Hoje só um caso traduz: o conflito de escrita do SQLite
    (E6-AUD2-002), que é um `OperationalError` cru e precisa chegar ao cliente como o mesmo
    `409 concurrent_task_update` que o CAS já produzia. Um `OperationalError` que não seja
    um dos códigos de conflito **não** é traduzido — a distinção de `_is_write_conflict` é
    justamente o que impede "todo erro de banco é concorrência".

    A tradução também governa a classificação do desfecho, e de graça: um conflito é um
    `OrchestratorError`, logo recuperável, logo a task volta para `draft` — que é a
    orientação certa, porque replanejar de fato resolve um lock que já passou.

    **Nunca levanta.** Ela roda dentro de um `except` cujo trabalho é deixar uma exceção
    subir: trocar a causa por um erro levantado durante a limpeza esconderia o que de fato
    aconteceu. Quando a limpeza não pode acontecer, o estado já é de outra transação, e a
    reconciliação do próximo startup é a rede de segurança que sobra.
    """
    translated: OrchestratorError | None = _translate_write_conflict(error, task_id)
    outcome: Exception = translated or error

    # O trabalho parcial (manifest a meio, campos de plano escritos) é descartado; a entrada
    # em `planning` sobrevive porque já foi commitada. Este `rollback` é também o "descarte
    # o snapshot velho antes de reler" de E6-AUD2-002: sem ele, a releitura abaixo
    # responderia com o estado que a vencedora já substituiu.
    session.rollback()

    task = session.get(WorkspaceTask, task_id)
    if task is None or task.status is not TaskStatus.PLANNING:
        # A vencedora já transicionou a task (o caso do `cancel` concorrente). O estado é
        # dela e **não** é sobrescrito; só o erro traduzido sobe.
        return translated

    recoverable = isinstance(outcome, OrchestratorError)

    try:
        if recoverable:
            _compare_and_set(
                session,
                task,
                expected_status=TaskStatus.PLANNING,
                expected_version=task.version,
                values=transition_fields(TaskStatus.DRAFT),
            )
        else:
            _compare_and_set(
                session,
                task,
                expected_status=TaskStatus.PLANNING,
                expected_version=task.version,
                values={
                    **transition_fields(
                        TaskStatus.FAILED, failure_reason=FailureReason.INTERNAL_ERROR
                    ),
                    "finished_at": _utcnow(),
                    "started_at": task.started_at or _utcnow(),
                },
            )
            # Só a falha **inesperada** deixa trilha. O erro recuperável é de configuração
            # ou de forma — não é decisão de política ([02] §4), o `409` já carrega o motivo
            # e a volta para `draft` é durável por si. `CANCELLED` é o `SafetyEventKind` que
            # `reconcile_on_startup` já usa para "esta task parou sem terminar"; o conjunto
            # de [02] §12 é fechado e não tem um kind melhor.
            record_safety_event(
                session,
                kind=SafetyEventKind.CANCELLED,
                decision=SafetyDecisionKind.DENY,
                rule_id="orchestrator.plan_failed",
                subject=f"task:{task.id}",
                workspace_id=task.workspace_id,
                task_id=task.id,
                detail=f"planejamento interrompido por {type(outcome).__name__}",
            )
        _commit_audit_trail(session)
    except Exception:
        # O caso esperado é `ConcurrentTaskUpdate`: outra transação transicionou a task
        # entre a releitura e o CAS. Qualquer outro também é engolido, e de propósito — o
        # `except` largo aqui não esconde uma decisão, ele protege a exceção **original**
        # de ser substituída por um erro de limpeza. Uma task que ficar em `planning` por
        # causa disso é recuperada no próximo startup; um erro trocado é perdido para
        # sempre.
        session.rollback()

    return translated


def _recompute_fingerprint(
    session: Session,
    task: WorkspaceTask,
    *,
    developer_binding_resolver: DeveloperBindingResolver | None,
) -> tuple[str, dict[str, Any]]:
    """Recalcula o fingerprint sobre o estado **atual** do banco ([02] §7).

    Usa `build_fingerprint_parts`, o mesmo código do plano — ver o docstring dela para por
    que um segundo caminho de montagem seria um defeito.
    """
    manifest = latest_manifest(session, task)
    if manifest is None or task.plan_hash is None:
        raise ApprovalFingerprintMismatch(
            "a task não tem plano congelado; replaneje antes de aprovar",
            expected="",
            received="",
            diverged_fields=("plan_hash", "manifest_hash"),
        )

    workspace = get_workspace(session, task.workspace_id)
    decision = decision_from_task(task)
    parts = build_fingerprint_parts(
        task,
        workspace,
        manifest,
        plan_hash=task.plan_hash,
        agents=tuple(task.agents or ()),
        decision=decision,
        developer_binding_resolver=developer_binding_resolver,
    )
    return parts.compute(), parts.as_canonical()


@command
def approve(
    session: Session,
    task_id: str,
    *,
    execution_fingerprint: str,
    developer_binding_resolver: DeveloperBindingResolver | None = None,
) -> WorkspaceTask:
    """`awaiting_approval → approved`, exigindo o `execution_fingerprint` **completo**.

    [ADR-0008] regra 5 e [04] §7: divergência gera `409`,
    `SafetyEvent(approval_invalidated)` e a indicação de **qual campo** mudou. A task
    permanece em `awaiting_approval` — o auto-laço da tabela de [02] §4 — com os campos de
    aprovação **atualizados para o valor novo**: o cliente que reler a task vê o fingerprint
    vigente e pode reaprovar sem replanejar, que é o fluxo que [06] §4 descreve.
    """
    task = get_task(session, task_id)
    #: A origem **real**, e não `awaiting_approval` presumido. [02] §4 dá duas arestas para
    #: `approved`, e fixar a origem no CAS fazia a segunda (`needs_fix → approved`, "nova
    #: tentativa") responder `409 concurrent_task_update` — afirmando uma concorrência que
    #: não existia — em vez de aplicar a guarda daquela aresta (E6-AUD-010).
    origin = task.status
    require_transition(origin, TaskStatus.APPROVED)

    current_fingerprint, current_parts = _recompute_fingerprint(
        session, task, developer_binding_resolver=developer_binding_resolver
    )

    if execution_fingerprint != current_fingerprint:
        fields = diverged_fields(task.approved_fingerprint_parts, current_parts)

        record_safety_event(
            session,
            kind=SafetyEventKind.APPROVAL_INVALIDATED,
            decision=SafetyDecisionKind.DENY,
            rule_id="orchestrator.approval_fingerprint_mismatch",
            subject=f"task:{task.id}",
            workspace_id=task.workspace_id,
            task_id=task.id,
            detail=f"campos divergentes: {', '.join(fields) or '(nenhum campo de topo)'}",
        )

        # Vindo de `awaiting_approval`, isto é o auto-laço da tabela de [02] §4: a aprovação
        # foi invalidada e a task continua aguardando, agora com o fingerprint vigente à
        # mostra. Vindo de `needs_fix`, **não há transição** — `needs_fix → needs_fix` não é
        # aresta —, só a atualização dos campos de aprovação candidata no mesmo estado.
        # `transition_fields(origin)` serve aos dois porque ele deriva `phase` e
        # `failure_reason` do destino, e nos dois estados os dois são `NULL`.
        _compare_and_set(
            session,
            task,
            expected_status=origin,
            expected_version=task.version,
            values={
                **transition_fields(origin),
                "approved_fingerprint": current_fingerprint,
                "approved_fingerprint_parts": current_parts,
                "approved_at": None,
            },
        )

        # **O commit aqui é obrigatório.** O que acabou de acontecer não é um erro que
        # deva ser desfeito: é a transição `awaiting_approval → awaiting_approval` do
        # diagrama de [02] §4 ("fingerprint divergente → aprovação invalidada"), mais o
        # `SafetyEvent(approval_invalidated)` que [ADR-0008] regra 5 **exige** que exista.
        #
        # Sem ele, o `session_scope` da camada HTTP faz rollback ao ver a exceção subir e
        # descarta os dois: a trilha append-only de [02] §12 perderia justamente o evento
        # que ela existe para guardar, e a task voltaria a exibir o fingerprint velho.
        # Commitar é papel deste módulo — [01] §2 o define como **dono da transação**.
        _commit_audit_trail(session)

        raise ApprovalFingerprintMismatch(
            "o `execution_fingerprint` enviado não corresponde ao recalculado; a aprovação "
            "foi invalidada ([04] §7)",
            expected=current_fingerprint,
            received=execution_fingerprint,
            diverged_fields=fields,
        )

    manifest = latest_manifest(session, task)
    assert manifest is not None  # `_recompute_fingerprint` já teria levantado

    _check_approval_guards(session, task, manifest, origin=origin)

    _compare_and_set(
        session,
        task,
        expected_status=origin,
        expected_version=task.version,
        values={
            **transition_fields(TaskStatus.APPROVED),
            "approved_manifest_id": manifest.id,
            "approved_fingerprint": current_fingerprint,
            "approved_fingerprint_parts": current_parts,
            "approved_at": _utcnow(),
        },
    )

    record_safety_event(
        session,
        kind=SafetyEventKind.APPROVAL_GRANTED,
        decision=SafetyDecisionKind.ALLOW,
        rule_id="orchestrator.approval_granted",
        subject=f"task:{task.id}",
        workspace_id=task.workspace_id,
        task_id=task.id,
        detail=f"fingerprint {current_fingerprint}",
    )

    return get_task(session, task_id)


#: `SafetyEventKind` de cada guarda que pode recusar uma aprovação. O conjunto de [02] §12
#: é fechado, então o mapa escolhe o item mais próximo em vez de inventar um: teto de
#: tentativas é literalmente `retry_limit`; contexto `stale` sob alto risco é a recusa de
#: conceder a aprovação, que é o que `approval_invalidated` registra nos outros caminhos.
_APPROVAL_GUARD_EVENT_KINDS: dict[str, SafetyEventKind] = {
    "attempts_below_max": SafetyEventKind.RETRY_LIMIT,
    "no_stale_entry_when_high_risk": SafetyEventKind.APPROVAL_INVALIDATED,
}


def _check_approval_guards(
    session: Session,
    task: WorkspaceTask,
    manifest: ContextManifest,
    *,
    origin: TaskStatus,
) -> None:
    """As guardas de `→ approved`, que são **duas diferentes** conforme a origem ([02] §4).

    De `awaiting_approval`: "nenhuma entrada `stale` selecionada se `risk = high`", sobre os
    estados **congelados no manifest** — a seleção acabou de acontecer e o manifest é a
    linha imutável de [02] §5.

    De `needs_fix`: "`attempts < max_attempts` **e contexto reverificado**". Aqui o manifest
    é antigo (houve uma execução inteira desde o congelamento), então "reverificado" só pode
    significar o estado de agora — e a reverificação roda contra o `planning_base_commit`
    congelado, pela API canônica da E4, sem uma segunda leitura de `HEAD`.

    Qualquer recusa deixa `SafetyEvent` **durável antes** do `409` (E6-AUD-002). O
    caminho de `awaiting_approval` não deixava nenhum: `_commit_audit_trail` tinha sido
    aplicado à divergência de fingerprint e à guarda de entrada, e esta terceira recusa —
    que é uma decisão de política, exatamente o critério de [02] §4 — ficou de fora.
    """
    risk_is_high = task.risk is RiskLevel.HIGH

    try:
        if origin is TaskStatus.NEEDS_FIX:
            if task.planning_base_commit is None:
                raise ApprovalFingerprintMismatch(
                    "a task não tem `planning_base_commit` congelado; replaneje antes de "
                    "aprovar uma nova tentativa",
                    expected="",
                    received="",
                    diverged_fields=("base_commit",),
                )
            workspace = get_workspace(session, task.workspace_id)
            states = reverify_context(session, workspace, base_commit=task.planning_base_commit)
            check_needs_fix_guard(
                attempts=task.attempts,
                max_attempts=decision_from_task(task).max_attempts,
                stale_entry_ids=stale_manifest_entry_ids(manifest, states),
                risk_is_high=risk_is_high,
            )
        else:
            check_approval_guard(
                stale_entry_ids=_stale_entries_of_manifest(manifest),
                risk_is_high=risk_is_high,
            )
    except TransitionGuardFailed as exc:
        record_safety_event(
            session,
            kind=_APPROVAL_GUARD_EVENT_KINDS.get(exc.guard, SafetyEventKind.APPROVAL_INVALIDATED),
            decision=SafetyDecisionKind.DENY,
            rule_id=f"orchestrator.approval_guard.{exc.guard}",
            subject=f"task:{task.id}",
            workspace_id=task.workspace_id,
            task_id=task.id,
            detail=exc.message,
        )
        # O commit leva junto o resultado da reverificação, quando ela rodou. É desejado: um
        # veredito de frescor é estado durável legítimo — é o mesmo que `/context/verify`
        # grava —, e descartá-lo faria a próxima tentativa reverificar tudo de novo para
        # chegar à mesma conclusão.
        _commit_audit_trail(session)
        raise


def _stale_entries_of_manifest(manifest: ContextManifest) -> tuple[str, ...]:
    """Entradas não-`fresh` **congeladas no manifest**, não relidas do registry.

    O manifest é a linha imutável de [02] §5: ele registra o estado que cada entrada tinha
    **no momento da seleção**. Reler o registry aqui responderia uma pergunta diferente — "e
    agora, como está?" — e faria a guarda de aprovação depender de uma verificação que
    ninguém pediu. Se o estado mudou desde o plano, quem detecta é o `manifest_hash` dentro
    do fingerprint, que é o mecanismo certo para isso.
    """
    return tuple(
        str(entry["entry_id"]) for entry in manifest.entries if entry.get("state") != "fresh"
    )


@command
def reject(session: Session, task_id: str, *, note: str | None = None) -> WorkspaceTask:
    """`awaiting_approval → draft`, com nota ([ADR-0008]: "rejeitar devolve para `draft`").

    A aprovação candidata é **limpa**: `approved_fingerprint`, `_parts` e
    `approved_manifest_id` voltam a `NULL`. Deixá-los preenchidos faria uma task rejeitada
    parecer ter um plano vigente, e o próximo `approve` poderia casar um fingerprint de um
    plano que o usuário já recusou.

    O `ContextManifest` em si **não** é apagado: [02] §5 o define como linha imutável, e o
    Rendered Context Artifact que ele referencia é o registro do que foi entregue.
    """
    task = get_task(session, task_id)
    require_transition(task.status, TaskStatus.DRAFT)

    summary = redact(note.strip()) if note and note.strip() else None

    _compare_and_set(
        session,
        task,
        expected_status=task.status,
        expected_version=task.version,
        values={
            **transition_fields(TaskStatus.DRAFT),
            "approved_manifest_id": None,
            "approved_fingerprint": None,
            "approved_fingerprint_parts": None,
            "approved_at": None,
            "result_summary": summary,
        },
    )
    return get_task(session, task_id)


@command
def cancel(session: Session, task_id: str, *, note: str | None = None) -> WorkspaceTask:
    """`qualquer não-terminal → cancelled` ([02] §4).

    `failure_reason` fica `NULL` — [ADR-0008] correção 1B.1 (AUD-011): desistir é decisão
    humana, não falha do sistema, e classificá-la como falha envenenaria a taxa de sucesso
    do benchmark. `transition_fields` impõe isso por construção.
    """
    task = get_task(session, task_id)
    require_transition(task.status, TaskStatus.CANCELLED)

    summary = redact(note.strip()) if note and note.strip() else None

    _compare_and_set(
        session,
        task,
        expected_status=task.status,
        expected_version=task.version,
        values={
            **transition_fields(TaskStatus.CANCELLED),
            "cancel_requested": True,
            "finished_at": _utcnow(),
            "started_at": task.started_at or _utcnow(),
            "result_summary": summary,
        },
    )

    record_safety_event(
        session,
        kind=SafetyEventKind.CANCELLED,
        decision=SafetyDecisionKind.ALLOW,
        rule_id="orchestrator.task_cancelled",
        subject=f"task:{task.id}",
        workspace_id=task.workspace_id,
        task_id=task.id,
    )

    return get_task(session, task_id)


@command
def start_execution(
    session: Session,
    task_id: str,
    *,
    prover: CapabilityProver | None = None,
    expected_capability_binding: CapabilityBinding | None = None,
    developer_binding_resolver: DeveloperBindingResolver | None = None,
) -> WorkspaceTask:
    """`approved → executing`. **Recusada nesta fase, por desenho.**

    Com `prover=None` — o único valor em produção até a E8 — a guarda
    `capability_profile_proven` levanta `TransitionGuardFailed` (409) com motivo explícito.
    Ver o docstring de `state_machine` para por que isso é [ADR-0004] funcionando, e não uma
    lacuna.

    E7.6: ``expected_capability_binding`` é o contexto esperado, fornecido pelo chamador
    **confiável** (nunca extraído da prova). Sem ele a guarda de capability também recusa.
    Quando a guarda *passa*, a continuação real (Run, worktree, Developer, TestRunner…) é
    E8: o limite `NotImplementedError` abaixo é preservado, a task segue `approved` e nada
    é criado.

    E8.1: ``developer_binding_resolver`` participa só do **recálculo** do fingerprint (a
    mesma porta do `plan`/`approve`). Ele não prova capability nem abre a execução: sem
    provador, a guarda continua recusando.

    A função existe agora, e não em E7, porque a **guarda** é o entregável: escrevê-la junto
    com a máquina de estados é o que garante que E7 acrescente o provador a um ponto de
    extensão já fechado, em vez de abrir um caminho novo ao lado dela.
    """
    task = get_task(session, task_id)
    require_transition(task.status, TaskStatus.EXECUTING)

    workspace = get_workspace(session, task.workspace_id)
    facts_git = preflight(workspace.local_path)
    current_fingerprint, current_parts = _recompute_fingerprint(
        session, task, developer_binding_resolver=developer_binding_resolver
    )
    decision = decision_from_task(task)

    # Lidos **antes** do CAS de invalidação: ele sobrescreve os dois campos e expira o
    # objeto, e o diagnóstico de [06] §2 precisa comparar o que foi aprovado com o que vale
    # agora — depois da escrita, os dois lados seriam o mesmo valor.
    approved_fingerprint = task.approved_fingerprint
    approved_parts = task.approved_fingerprint_parts

    try:
        check_entry_guard(
            EntryGuardFacts(
                fingerprint_matches=current_fingerprint == approved_fingerprint,
                slot_available=_slot_available(session, exclude_task_id=task.id),
                is_git_repo=facts_git.is_git_repo,
                head=facts_git.head,
                planning_base_commit=task.planning_base_commit,
                attempts=task.attempts,
                max_attempts=decision.max_attempts,
            ),
            prover=prover,
            expected_binding=expected_capability_binding,
        )
    except TransitionGuardFailed as exc:
        # [02] §4: "guardas que falham geram `409` com o motivo, e `SafetyEvent` quando a
        # causa é política". Só `TransitionGuardFailed` é capturado: um erro inesperado
        # aqui é defeito, não decisão de política, e registrá-lo como `SafetyEvent`
        # poluiria a trilha com ruído que ninguém sabe interpretar.
        record_safety_event(
            session,
            kind=_ENTRY_GUARD_EVENT_KINDS.get(exc.guard, SafetyEventKind.APPROVAL_INVALIDATED),
            decision=SafetyDecisionKind.DENY,
            rule_id=f"orchestrator.entry_guard.{exc.guard}",
            subject=f"task:{task.id}",
            workspace_id=task.workspace_id,
            task_id=task.id,
            detail=exc.message,
        )

        invalidation = _invalidate_approval_on_entry(
            session,
            task,
            guard=exc.guard,
            current_fingerprint=current_fingerprint,
            current_parts=current_parts,
            approved_fingerprint=approved_fingerprint,
            approved_parts=approved_parts,
        )

        # Ver `_commit_audit_trail`: sem isto o `session_scope` da camada HTTP descarta o
        # evento **e** a transição que acabaram de ser gravados.
        _commit_audit_trail(session)

        if invalidation is not None:
            raise invalidation from exc
        raise

    raise NotImplementedError(  # a continuação real é E8; só um provador injetado chega aqui
        "a guarda de entrada passou, mas a continuação da execução é E8"
    )


#: As duas guardas de entrada que significam **a aprovação não vale mais**, e não "agora
#: não dá". [02] §4 desenha exatamente uma aresta para elas: `approved → awaiting_approval`,
#: "fingerprint divergente na entrada".
#:
#: As outras quatro ficam em `approved` de propósito, porque nenhuma invalida coisa alguma:
#: `slot_available` é a fila de [ADR-0008] ("uma tarefa aprovada espera outra terminar"),
#: `workspace_is_git_repo` é o ambiente, `attempts_below_max` é o teto que só replanejar ou
#: cancelar resolve, e as duas de capability são a ausência de E7. Rebaixar a aprovação em
#: qualquer uma delas faria o humano reaprovar um plano que não mudou.
_APPROVAL_INVALIDATING_GUARDS: frozenset[str] = frozenset(
    {"fingerprint_still_valid", "head_matches_planning_base_commit"}
)

#: `SafetyEventKind` por guarda de entrada. As duas de capability são `capability_*`; o
#: resto é a recusa de uma aprovação.
_ENTRY_GUARD_EVENT_KINDS: dict[str, SafetyEventKind] = {
    "capability_profile_proven": SafetyEventKind.CAPABILITY_UNENFORCEABLE,
    "capability_profile_matches_approved": SafetyEventKind.CAPABILITY_DENIED,
    "attempts_below_max": SafetyEventKind.RETRY_LIMIT,
}


def _invalidate_approval_on_entry(
    session: Session,
    task: WorkspaceTask,
    *,
    guard: str,
    current_fingerprint: str,
    current_parts: dict[str, Any],
    approved_fingerprint: str | None,
    approved_parts: dict[str, Any] | None,
) -> OrchestratorError | None:
    """`approved → awaiting_approval` quando a guarda de entrada invalida a aprovação.

    Devolve o erro **rico** que deve subir no lugar do `TransitionGuardFailed` genérico, ou
    `None` quando a guarda que falhou não invalida nada (aí o erro original sobe).

    ## O que estava errado (E6-AUD-001)

    O bloco anterior gravava o `SafetyEvent` e parava aí. A task continuava `approved`, com
    `approved_at` preenchido e o fingerprint velho: um estado que afirma "um humano aprovou
    exatamente isto" sobre um plano que já não é o que vale. Pior, era um beco — `/approve` e
    `/reject` respondem `409` a partir de `approved`, então não havia comando que
    devolvesse a task ao fluxo.

    ## Os dois desfechos, e por que são diferentes

    **Fingerprint divergente, `HEAD` intacto.** Algo coberto pelo fingerprint mudou
    (`test_config`, limites, `agents`) sem que a base do plano mudasse. O plano continua
    válido contra o mesmo commit, então os campos candidatos são **atualizados** para o
    valor vigente e o humano reaprova lendo o que mudou. É o mesmo tratamento que o
    `approve` já dá à divergência, e o erro devolvido é o mesmo
    `ApprovalFingerprintMismatch` — com `diverged_fields`, que era o diagnóstico que faltava.

    **`HEAD` mudou.** [02] §6 congela `planning_base_commit`, e a execução "nunca salta para
    o `HEAD` novo". Reaprovar **não** resolveria: o fingerprint cobre `base_commit`, que
    continua sendo o commit congelado, então ele bateria de novo e a guarda recusaria de
    novo — um laço. A base congelada também não pode ser substituída em silêncio: seria
    trocar o plano que o humano leu por outro sem que ninguém revisse a seleção de contexto.
    Resta forçar um `POST /plan` novo, e é o que a limpeza dos campos candidatos faz — o
    mesmo conjunto que o `reject` limpa. Sem `approved_manifest_id` não há fingerprint a
    recalcular, e `approve` responde "replaneje antes de aprovar" em vez de voltar ao laço.
    O `ContextManifest` em si continua na tabela: [02] §5 o define como linha imutável.
    """
    if guard not in _APPROVAL_INVALIDATING_GUARDS:
        return None

    requires_replan = guard == "head_matches_planning_base_commit"

    values: dict[str, Any] = {
        **transition_fields(TaskStatus.AWAITING_APPROVAL),
        "approved_at": None,
    }
    if requires_replan:
        values.update(
            approved_manifest_id=None,
            approved_fingerprint=None,
            approved_fingerprint_parts=None,
        )
    else:
        values.update(
            approved_fingerprint=current_fingerprint,
            approved_fingerprint_parts=current_parts,
        )

    _compare_and_set(
        session,
        task,
        expected_status=TaskStatus.APPROVED,
        expected_version=task.version,
        values=values,
    )

    if requires_replan:
        # A orientação nomeia o caminho que **existe** (E6-AUD2-004). A versão anterior
        # mandava rodar `POST /plan`, que responde `409`: a task está em
        # `awaiting_approval`, e `awaiting_approval → planning` não é aresta de [02] §4 —
        # corretamente, porque replanejar é decisão de quem rejeitou o plano anterior.
        return TransitionGuardFailed(
            "o `HEAD` do workspace divergiu do `planning_base_commit` congelado. A execução "
            "nunca salta para o `HEAD` novo ([02] §6) e a base congelada não é substituída "
            "em silêncio: a aprovação foi invalidada e o plano exibido passou a ser "
            "histórico. Para replanejar sobre o commit atual, rejeite primeiro "
            "(`POST /reject`) e depois rode `POST /plan` — a task precisa voltar a `draft`",
            guard="head_matches_planning_base_commit",
            requires_replan=True,
        )

    return ApprovalFingerprintMismatch(
        "o `execution_fingerprint` recalculado na entrada de execução não é mais o "
        "aprovado; a aprovação foi invalidada e a task voltou a `awaiting_approval` com o "
        "fingerprint vigente ([02] §7, [04] §7)",
        expected=current_fingerprint,
        received=approved_fingerprint or "",
        diverged_fields=diverged_fields(approved_parts, current_parts),
    )


def _slot_available(session: Session, *, exclude_task_id: str) -> bool:
    """`max_parallel_agents = 1` ([04] §7): existe alguma **outra** task executando?"""
    statement = (
        select(WorkspaceTask.id)
        .where(
            WorkspaceTask.status.in_(_SLOT_OCCUPYING_STATUSES),
            WorkspaceTask.id != exclude_task_id,
        )
        .limit(1)
    )
    return session.scalar(statement) is None


# --------------------------------------------------------------------------- recuperação


@command
def reconcile_on_startup(session: Session) -> tuple[str, ...]:
    """Tasks presas em `planning`/`executing` → `failed(interrupted)`. **Idempotente.**

    [02] §4 e [ADR-0008] regra 6: "rodar duas vezes produz o mesmo estado". A idempotência é
    estrutural, não defensiva: a segunda execução não encontra nenhuma task nos dois estados
    de origem, então o `SELECT` volta vazio e nada é escrito.

    Como `max_parallel_agents = 1` e o backend é um processo só, **toda** task nesses
    estados no startup é, por definição, de um processo que já morreu — não existe outro
    processo vivo que pudesse estar trabalhando nela. É o que dispensa um registro de PID.

    Esta função só passou a ter o que recuperar em `planning` depois de E6-AUD-003: até
    então a entrada nesse estado vivia dentro da transação do `plan`, e um crash real do
    processo a descartava junto com o resto — o `SELECT` abaixo voltava vazio porque a
    task tinha revertido para `draft`. O `commit` que `plan` faz logo após a entrada é o
    que torna esta recuperação alcançável, e não apenas escrita.

    As worktrees são **preservadas** ([02] §4, [04] §8): nenhuma é removida aqui. Nenhuma
    existe antes da E7, de todo modo.
    """
    stuck = list(
        session.scalars(
            select(WorkspaceTask)
            .where(WorkspaceTask.status.in_((TaskStatus.PLANNING, TaskStatus.EXECUTING)))
            .order_by(WorkspaceTask.id)
        )
    )

    reconciled: list[str] = []
    for task in stuck:
        origin = task.status
        _compare_and_set(
            session,
            task,
            expected_status=origin,
            expected_version=task.version,
            values={
                **transition_fields(TaskStatus.FAILED, failure_reason=FailureReason.INTERRUPTED),
                "finished_at": _utcnow(),
                "started_at": task.started_at or _utcnow(),
            },
        )
        record_safety_event(
            session,
            kind=SafetyEventKind.CANCELLED,
            decision=SafetyDecisionKind.ALLOW,
            rule_id="orchestrator.reconcile_interrupted",
            subject=f"task:{task.id}",
            workspace_id=task.workspace_id,
            task_id=task.id,
            detail=f"presa em `{origin.value}` sem processo vivo",
        )
        reconciled.append(task.id)

    return tuple(reconciled)


def workspace_of(session: Session, task: WorkspaceTask) -> DevWorkspace:
    return get_workspace(session, task.workspace_id)


__all__ = [
    "APPROVAL_STATES",
    "APPROVAL_STATE_APPROVED",
    "APPROVAL_STATE_NOT_PLANNED",
    "APPROVAL_STATE_PENDING",
    "APPROVAL_STATE_REQUIRES_REPLAN",
    "PLAN_STANDING_CURRENT",
    "PLAN_STANDING_FINAL",
    "PLAN_STANDING_HISTORICAL",
    "PLAN_STANDING_NONE",
    "approval_state",
    "approve",
    "cancel",
    "create_task",
    "get_task",
    "latest_manifest",
    "list_tasks",
    "plan",
    "plan_standing",
    "reconcile_on_startup",
    "record_safety_event",
    "reject",
    "start_execution",
    "workspace_of",
]
