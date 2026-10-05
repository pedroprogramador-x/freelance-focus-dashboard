"""Router do Orchestrator — `/api/workspaces/{id}/tasks` e `/api/tasks/{id}`.

[01](../../../docs/architecture/01-v1-architecture.md) §2–§3: esta é a **única** camada
desta feature que conhece FastAPI/HTTP. Toda a lógica vive em `app.orchestrator`; aqui só
há validação de forma (Pydantic), abertura de sessão e projeção de leitura.

[06](../../../docs/architecture/06-api-and-ui-boundaries.md) §2, rotas **desta** fase:

| Método | Rota | Papel |
| --- | --- | --- |
| `GET` `POST` | `/api/workspaces/{id}/tasks` | Listar e criar |
| `GET` | `/api/tasks/{id}` | Estado, `phase`, plano, agentes, limites, fingerprint |
| `POST` | `/api/tasks/{id}/plan` | Congela `base_commit`, seleciona, renderiza, planeja |
| `POST` | `/api/tasks/{id}/approve` | Exige o `execution_fingerprint` completo |
| `POST` | `/api/tasks/{id}/reject` | Volta para `draft` com nota |
| `POST` | `/api/tasks/{id}/cancel` | Em qualquer estado não terminal |
| `GET` | `/api/tasks/{id}/purge-preview` | Contagens + emissão do `purge_token` |
| `POST` | `/api/tasks/{id}/purge` | **Destrutivo**; só task terminal |
| `GET` | `/api/tasks/{id}/context` | Manifest + referência ao artefato |

Deliberadamente **fora** desta fase ([07], escopo da E6): `/runs`, `/diff`, `/findings`,
`/worktree/discard` e `/events` — E7 a E11. Nenhuma delas é declarada como placeholder:
uma rota que responde `501` ainda é superfície de API, e [06] §2 admite exatamente as que
estão na tabela acima.

A autenticação por `LocalSessionToken` é herdada do middleware de `app.main` — nenhuma rota
aqui a reimplementa.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.api.responses import RedactingJSONResponse
from app.db.enums import (
    ComplexityLevel,
    ExecutionMode,
    RiskLevel,
    RiskSource,
    TaskPhase,
    TaskStatus,
)
from app.db.models import ContextManifest, WorkspaceTask
from app.db.session import session_scope
from app.orchestrator import (
    APPROVAL_STATE_REQUIRES_REPLAN,
    approval_state,
    approve,
    can_transition,
    cancel,
    create_task,
    execute_task_purge,
    get_task,
    is_terminal,
    latest_manifest,
    list_tasks,
    plan,
    plan_standing,
    planning_eligibility,
    reject,
    task_purge_preview,
)
from app.orchestrator.developer_binding import DeveloperBindingResolver
from app.safety import redact, redact_document
from app.workspace import PurgeCounts, PurgeTokenStore, task_subject

router = APIRouter(tags=["tasks"])


# ------------------------------------------------------------------ schemas


class TaskCreate(BaseModel):
    """Corpo de `POST /api/workspaces/{id}/tasks`."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    goal: str = Field(min_length=1)
    execution_mode: ExecutionMode = ExecutionMode.ORCHESTRATED
    benchmark_group_id: str | None = Field(default=None, max_length=36)

    @field_validator("title", "goal")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("campo não pode ser vazio")
        return stripped


class PlanRequest(BaseModel):
    """Corpo de `POST /api/tasks/{id}/plan`.

    ``candidate_paths`` é **explícito e opcional** — a decisão documentada em
    `orchestrator/planner.py`. Lista vazia é o caso normal: os outros três sinais de
    scoring de [03] §4 continuam valendo, e nenhum caminho inventado entra no manifest.

    O teto de 500 existe porque cada candidato passa por `validate_source_ref` e depois
    é casado contra toda entrada do registry: é o custo por item, multiplicado.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_paths: list[str] = Field(default_factory=list, max_length=500)


class ApproveRequest(BaseModel):
    """Corpo de `POST /api/tasks/{id}/approve` — o `execution_fingerprint` **completo**.

    [ADR-0008] regra 5: aprovar exige o fingerprint, não um `confirm: true`. É o que
    vincula a aprovação ao que o humano de fato leu.
    """

    model_config = ConfigDict(extra="forbid")

    execution_fingerprint: str = Field(min_length=64, max_length=64)


class NoteRequest(BaseModel):
    """Corpo de `reject` e `cancel`: uma nota curta, opcional."""

    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=2000)


class TaskPurgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purge_token: str = Field(min_length=1)


class TaskSummaryResponse(BaseModel):
    """Projeção de lista — o suficiente para a aba *Tasks* sem carregar plano nenhum."""

    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    title: str
    status: TaskStatus
    phase: TaskPhase | None
    risk: RiskLevel
    complexity: ComplexityLevel
    risk_source: RiskSource
    execution_mode: ExecutionMode
    version: int
    created_at: str
    finished_at: str | None


class PlanningEligibilityResponse(BaseModel):
    """`TaskResponse.planning` — ver `orchestrator/eligibility.py` (E6-CONS4).

    Só no **detalhe**. `TaskSummaryResponse` não tem o campo: a listagem nunca sonda git
    por task, e a ausência do campo é a representação disso — não um "elegível" implícito.

    ``eligible`` é a única autorização para oferecer "Planejar". ``checked = false`` quer
    dizer que o git não foi consultado (transição proibida, ou replanejar não é o próximo
    passo), e aí ``blockers`` é parcial: vazio **não** significa "nada falta".
    """

    model_config = ConfigDict(extra="forbid")

    transition: Literal["allowed", "after_reject", "forbidden"]
    checked: bool
    blockers: list[
        Literal[
            "workspace_archived",
            "workspace_not_git_repo",
            "repository_without_head",
            "git_unverifiable",
            "invalid_test_config",
        ]
    ]
    eligible: bool


class TaskResponse(TaskSummaryResponse):
    """Projeção de detalhe — o que [06] §4 exige do *Task Detail*.

    `goal`, `plan` e `result_summary` passam pelo redator antes de serializar ([06] §2:
    "todo corpo JSON de `/api/*` passa pelo redator"). `plan` é redigido **recursivamente**
    porque ele carrega o objetivo do usuário, que é texto livre.
    """

    model_config = ConfigDict(extra="forbid")

    goal: str
    agents: list[str]
    plan: dict[str, Any] | None
    plan_hash: str | None
    planning_base_commit: str | None
    base_commit: str | None
    approved_manifest_id: str | None
    #: O que `POST /plan` aceitaria agora, derivado das **mesmas** funções de guarda que o
    #: planejamento aplica (E6-CONS4). Substitui `workspace_archived` (E6-CONS3-001), que
    #: cobria uma das cinco pré-condições. Fotografia, não garantia.
    planning: PlanningEligibilityResponse
    #: Se `plan` — e o manifest, quando existe, que pertence a ele — está em vigor:
    #: `none` · `current` · `historical` · `final` (E6-CONS4, D2-A). O fingerprint exibido
    #: só é "vigente" com `current`.
    plan_standing: Literal["none", "current", "historical", "final"]
    #: O fingerprint **vigente**: candidato enquanto `approved_at` é `null`, aprovado
    #: depois. É o valor que o `POST /approve` espera de volta. É um sha256 e não carrega
    #: texto livre: sai como está, e tem de sair — o cliente o devolve no `approve`.
    execution_fingerprint: str | None
    #: As partes do fingerprint, **redigidas** (E6-AUD2-001). Os hashes atravessam o
    #: redator intactos (não há span a detectar em hexadecimal); o que ele alcança aqui é
    #: `test_binding.runner` — e, a partir de E8/E9, `developer_binding`/`auditor_binding`,
    #: que também serão preenchidos com dado declarado.
    #:
    #: A redação é da **projeção**: `approved_fingerprint_parts` continua cru no banco, e o
    #: `execution_fingerprint` acima continua sendo o sha256 dos bytes reais. Hashear o
    #: valor redigido faria o fingerprint deixar de identificar a configuração real.
    execution_fingerprint_parts: dict[str, Any] | None
    approved_at: str | None
    #: Onde a task está no **fluxo de aprovação**, derivado das colunas ([02] §3). Não é um
    #: estado da máquina de [02] §4 e não é persistido — `status` continua sendo a verdade.
    #:
    #: Ele existe porque `awaiting_approval` significa duas coisas que pedem ações opostas
    #: do humano (E6-AUD2-004): `pending` é "há plano vigente, aprove"; `requires_replan` é
    #: "a guarda de entrada invalidou a aprovação porque o `HEAD` mudou, o `plan` abaixo é
    #: **histórico**, e o caminho é rejeitar e replanejar". Sem o campo, [06] §4 não tem
    #: como oferecer o botão certo, e a UI mostrava "Aprovar" desabilitado e nada mais.
    approval_state: Literal["not_planned", "pending", "approved", "requires_replan"]
    attempts: int
    fix_rounds: int
    cancel_requested: bool
    result_summary: str | None
    started_at: str | None


class ManifestResponse(BaseModel):
    """Resposta de `GET /api/tasks/{id}/context` — manifest + **referência** ao artefato.

    `rendered_context_ref` é o caminho **dentro do artifact store**, nunca o absoluto: [01]
    §4 mantém caminho de fora do workspace do lado do backend, e o `data_dir` varia por
    máquina. O **conteúdo** do artefato não é servido por esta rota; ela responde *o que foi
    congelado*, e o payload exato é o blob endereçado por conteúdo.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    task_id: str
    git_head: str
    base_branch: str | None
    entries: list[dict[str, Any]]
    source_files: list[dict[str, Any]]
    working_tree_divergence: dict[str, Any]
    derived: list[dict[str, Any]]
    excluded: list[dict[str, Any]]
    rendered_context_hash: str
    rendered_context_ref: str
    renderer_version: str
    approx_tokens: int
    total_chars: int
    manifest_hash: str
    created_at: str


class TaskPurgePreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspaces: int
    tasks: int
    runs: int
    findings: int
    manifests: int
    artifacts: int
    benchmark_protected: bool
    purge_token: str


class TaskPurgeResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspaces: int
    tasks: int
    runs: int
    findings: int
    manifests: int
    artifacts: int


# -------------------------------------------------------------- dependências


def _session(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.session_factory) as session:
        yield session


def _purge_store(request: Request) -> PurgeTokenStore:
    store: PurgeTokenStore = request.app.state.purge_token_store
    return store


def _artifacts_dir(request: Request) -> Any:
    return request.app.state.settings.artifacts_dir


def _developer_binding_resolver(request: Request) -> DeveloperBindingResolver | None:
    """O resolver tier → binding do composition root (E8.2). A rota só conhece a porta neutra."""
    resolver: DeveloperBindingResolver | None = request.app.state.developer_binding_resolver
    return resolver


SessionDep = Annotated[Session, Depends(_session)]
PurgeStoreDep = Annotated[PurgeTokenStore, Depends(_purge_store)]
ArtifactsDirDep = Annotated[Any, Depends(_artifacts_dir)]
BindingResolverDep = Annotated[
    DeveloperBindingResolver | None, Depends(_developer_binding_resolver)
]


# ------------------------------------------------------------------ projeção


def _redact_tree(value: Any) -> Any:
    """Projeção redigida de uma estrutura JSON desta camada ([06] §2).

    Delega a `safety.redact_document`, o redator recursivo canônico. Este módulo tinha a
    sua própria caminhada até a E6-AUD2 — e o defeito E6-AUD2-001 é o resultado direto
    disso: com três cópias da mesma recursão em três módulos, "aplicar a redação a esta
    projeção" virou uma decisão a lembrar em cada ponto de saída, em vez de o caminho
    padrão. O wrapper permanece só para dar nome ao *porquê* local.

    O `plan` carrega `goal` e `acceptance_criteria`, que são texto livre do usuário — e
    texto livre é exatamente onde um segredo colado por engano apareceria. Redigir só o
    `goal` de topo deixaria a cópia dele dentro de `plan` passar. O mesmo vale para as
    partes do fingerprint: `test_binding.runner` é o `runner_id` declarado, texto livre
    validado pelo schema mas **não** um identificador fechado.
    """
    return redact_document(value)


def _summary(task: WorkspaceTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "workspace_id": task.workspace_id,
        "title": redact(task.title),
        "status": task.status,
        "phase": task.phase,
        "risk": task.risk,
        "complexity": task.complexity,
        "risk_source": task.risk_source,
        "execution_mode": task.execution_mode,
        "version": task.version,
        "created_at": task.created_at.isoformat(),
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


def _to_summary(task: WorkspaceTask) -> TaskSummaryResponse:
    return TaskSummaryResponse(**_summary(task))


def _to_response(session: Session, task: WorkspaceTask) -> TaskResponse:
    return TaskResponse(
        **_summary(task),
        goal=redact(task.goal),
        agents=list(task.agents or []),
        plan=_redact_tree(task.plan) if task.plan else None,
        plan_hash=task.plan_hash,
        planning_base_commit=task.planning_base_commit,
        base_commit=task.base_commit,
        approved_manifest_id=task.approved_manifest_id,
        planning=PlanningEligibilityResponse.model_validate(
            planning_eligibility(session, task).as_payload()
        ),
        plan_standing=cast(
            'Literal["none", "current", "historical", "final"]', plan_standing(task)
        ),
        execution_fingerprint=task.approved_fingerprint,
        execution_fingerprint_parts=_redact_tree(task.approved_fingerprint_parts)
        if task.approved_fingerprint_parts
        else None,
        approved_at=task.approved_at.isoformat() if task.approved_at else None,
        approval_state=cast(
            'Literal["not_planned", "pending", "approved", "requires_replan"]',
            approval_state(task),
        ),
        attempts=task.attempts,
        fix_rounds=task.fix_rounds,
        cancel_requested=task.cancel_requested,
        result_summary=redact(task.result_summary) if task.result_summary else None,
        started_at=task.started_at.isoformat() if task.started_at else None,
    )


def _to_manifest(manifest: ContextManifest) -> ManifestResponse:
    return ManifestResponse(
        id=manifest.id,
        task_id=manifest.task_id,
        git_head=manifest.git_head,
        base_branch=manifest.base_branch,
        # Os cinco campos de conteúdo JSON passam pelo redator, não só `entries`. Eles
        # carregam caminhos do workspace, e um caminho pode carregar credencial — é a
        # mesma razão pela qual `safety.redact_path` existe. Nenhum deles foi apontado pela
        # auditoria; a classe de defeito de E6-AUD2-001 é "uma projeção ficou de fora", e a
        # resposta a ela é fechar a classe, não o caso citado.
        entries=_redact_tree(manifest.entries),
        source_files=_redact_tree(manifest.source_files),
        working_tree_divergence=_redact_tree(manifest.working_tree_divergence),
        derived=_redact_tree(manifest.derived),
        excluded=_redact_tree(manifest.excluded),
        rendered_context_hash=manifest.rendered_context_hash,
        rendered_context_ref=manifest.rendered_context_ref,
        renderer_version=manifest.renderer_version,
        approx_tokens=manifest.approx_tokens,
        total_chars=manifest.total_chars,
        manifest_hash=manifest.manifest_hash,
        created_at=manifest.created_at.isoformat(),
    )


def _counts_payload(counts: PurgeCounts) -> dict[str, int]:
    return {
        "workspaces": counts.workspaces,
        "tasks": counts.tasks,
        "runs": counts.runs,
        "findings": counts.findings,
        "manifests": counts.manifests,
        "artifacts": counts.artifacts,
    }


# ------------------------------------------------------------------- rotas


@router.post(
    "/workspaces/{workspace_id}/tasks",
    response_model=TaskResponse,
    status_code=201,
    summary="Criar uma task em draft",
)
def create(workspace_id: str, payload: TaskCreate, session: SessionDep) -> TaskResponse:
    task = create_task(
        session,
        workspace_id,
        title=payload.title,
        goal=payload.goal,
        execution_mode=payload.execution_mode,
        benchmark_group_id=payload.benchmark_group_id,
    )
    return _to_response(session, task)


@router.get(
    "/workspaces/{workspace_id}/tasks",
    response_model=list[TaskSummaryResponse],
    summary="Listar tasks de um workspace",
)
def index(
    workspace_id: str,
    session: SessionDep,
    status: TaskStatus | None = None,
) -> list[TaskSummaryResponse]:
    return [_to_summary(task) for task in list_tasks(session, workspace_id, status=status)]


@router.get("/tasks/{task_id}", response_model=TaskResponse, summary="Detalhe de uma task")
def show(task_id: str, session: SessionDep) -> TaskResponse:
    return _to_response(session, get_task(session, task_id))


@router.post(
    "/tasks/{task_id}/plan",
    response_model=TaskResponse,
    summary="Congelar base_commit, selecionar contexto e planejar",
)
def plan_route(
    task_id: str,
    payload: PlanRequest,
    session: SessionDep,
    artifacts_dir: ArtifactsDirDep,
    binding_resolver: BindingResolverDep,
) -> TaskResponse:
    # `enrichment_port` **não** é passado: nenhuma porta existe antes da E8, e o piso de
    # fallback de [03] §5 é o comportamento correto desta fase. Injetá-lo aqui exigiria que
    # a camada HTTP conhecesse um adaptador, o que [01] §2 proíbe — quando existir, ele vem
    # do composition root via `app.state`.
    result = plan(
        session,
        task_id,
        candidate_paths=payload.candidate_paths,
        artifacts_dir=artifacts_dir,
        developer_binding_resolver=binding_resolver,
    )
    return _to_response(session, result.task)


@router.post(
    "/tasks/{task_id}/approve",
    response_model=TaskResponse,
    summary="Aprovar; exige o execution_fingerprint completo",
)
def approve_route(
    task_id: str,
    payload: ApproveRequest,
    session: SessionDep,
    binding_resolver: BindingResolverDep,
) -> TaskResponse:
    approved = approve(
        session,
        task_id,
        execution_fingerprint=payload.execution_fingerprint,
        developer_binding_resolver=binding_resolver,
    )
    return _to_response(session, approved)


@router.post(
    "/tasks/{task_id}/reject",
    response_model=TaskResponse,
    summary="Rejeitar o plano; volta para draft com nota",
)
def reject_route(task_id: str, payload: NoteRequest, session: SessionDep) -> TaskResponse:
    return _to_response(session, reject(session, task_id, note=payload.note))


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=TaskResponse,
    summary="Cancelar em qualquer estado não terminal",
)
def cancel_route(task_id: str, payload: NoteRequest, session: SessionDep) -> TaskResponse:
    return _to_response(session, cancel(session, task_id, note=payload.note))


@router.get(
    "/tasks/{task_id}/context",
    response_model=ManifestResponse,
    summary="Manifest congelado + referência ao artefato renderizado",
)
def context_route(task_id: str, session: SessionDep) -> ManifestResponse:
    """O manifest congelado; `404` com um `reason` quando a task existe e ele não.

    ## O contrato (E6-CONS4, adendo autorizado a [06] §2)

    **O manifest, quando existe, sempre vence** — em qualquer `status`, inclusive `draft`
    (um replan recuperável preserva `approved_manifest_id`) e terminais (cancelada ou falha
    *depois* de congelar). Se ele é atual, histórico ou registro final é `TaskResponse.
    plan_standing` que diz; esta rota não duplica essa leitura.

    Sem manifest, **toda** task existente responde `404 task_not_found` com `reason`:

    * `not_planned` — `draft`/`needs_fix`: ainda não há contexto congelado. É o estado vazio
      normal de uma task nova, não um erro;
    * `approval_invalidated` — `awaiting_approval` cuja aprovação a guarda de entrada
      invalidou porque o `HEAD` divergiu (E6-AUD2-004): o contexto daquele plano não vale;
    * `planning_in_progress` — `planning`, entre o commit de entrada e o de saída;
    * `terminal_without_context` — terminal sem nunca ter congelado contexto;
    * `unavailable_in_state` — combinação que as guardas atuais não produzem
      (`approved`/`executing` sem manifest). Defensivo: a UI trata como erro real.

    Task **inexistente** continua `404 task_not_found` **sem** `reason` — a ausência do
    campo é o que a distingue de "existe, sem contexto".

    ## Por que as mensagens não ensinam mais comandos

    As quatro rodadas consolidadas (CONS a CONS4) foram a mesma falha: esta rota montava a
    sequência "rode `POST /plan`" / "rejeite e replaneje" a partir de um subconjunto das
    guardas, e cada auditoria encontrou a guarda que faltava (terminal, `planning`,
    workspace arquivado, `needs_fix → draft`, git sem `HEAD`…). A pergunta "o que dá para
    fazer agora" tem uma resposta só: `TaskResponse.planning`, derivada das funções que o
    próprio `POST /plan` usa para recusar. Aqui a mensagem **descreve** por que não há
    contexto, e para por aí.

    Nenhum ramo consulta git nem workspace: a rota é uma leitura de banco.
    """
    from app.orchestrator.errors import TaskNotFound

    task = get_task(session, task_id)
    manifest = latest_manifest(session, task)
    if manifest is not None:
        return _to_manifest(manifest)

    if approval_state(task) == APPROVAL_STATE_REQUIRES_REPLAN and can_transition(
        task.status, TaskStatus.DRAFT
    ):
        raise TaskNotFound(
            f"a task '{task_id}' teve a aprovação invalidada porque o `HEAD` divergiu do "
            "`planning_base_commit` congelado; o contexto daquele plano não vale mais",
            reason="approval_invalidated",
        )
    if is_terminal(task.status):
        raise TaskNotFound(
            f"a task '{task_id}' foi encerrada ({task.status.value}) sem ter congelado "
            "contexto; estados terminais são imutáveis ([ADR-0008] regra 3)",
            reason="terminal_without_context",
        )
    if task.status is TaskStatus.PLANNING:
        raise TaskNotFound(
            f"a task '{task_id}' está planejando; o contexto ainda não foi congelado",
            reason="planning_in_progress",
        )
    if can_transition(task.status, TaskStatus.PLANNING):
        raise TaskNotFound(
            f"a task '{task_id}' ainda não tem contexto congelado",
            reason="not_planned",
        )
    raise TaskNotFound(
        f"a task '{task_id}' não tem contexto congelado disponível no estado atual "
        f"('{task.status.value}')",
        reason="unavailable_in_state",
    )


@router.get(
    "/tasks/{task_id}/purge-preview",
    # `response_model=None`: a rota devolve o `RedactingJSONResponse` já montado — ver
    # o docstring dela. `TaskPurgePreviewResponse` continua validando a forma, no corpo da função.
    response_model=None,
    summary="Contagens da purga da task e emissão do purge_token",
)
def purge_preview_route(
    task_id: str, session: SessionDep, store: PurgeStoreDep
) -> RedactingJSONResponse:
    """Contagens + o `purge_token`, que é o **único** valor a atravessar sem redação.

    A resposta é montada pelo `RedactingJSONResponse` diretamente, e não devolvida como
    modelo. O motivo é mecânico e vale registrar, porque é contraintuitivo: o Pydantic
    **descarta subclasses de `str`** no `model_dump`, então um `Unredacted` devolvido dentro
    de um modelo chega ao boundary já rebaixado a `str` comum — e seria redigido como
    qualquer outra string. Verificado, não suposto.

    A validação de forma continua acontecendo: o modelo é construído (com `extra="forbid"`
    e os tipos declarados), e só o `model_dump` dele é que vira o corpo, com o token
    reinserido como `Unredacted`. Nada aqui é uma rota sem schema.
    """
    counts = task_purge_preview(session, task_id)
    token = store.issue(task_subject(task_id))
    payload = TaskPurgePreviewResponse(
        **_counts_payload(counts),
        benchmark_protected=counts.benchmark_protected,
        purge_token=token,
    )
    return RedactingJSONResponse(content={**payload.model_dump(), "purge_token": token})


@router.post(
    "/tasks/{task_id}/purge",
    response_model=TaskPurgeResultResponse,
    summary="Purga destrutiva; só task terminal, com purge_token de uma prévia recente",
)
def purge_route(
    task_id: str,
    payload: TaskPurgeRequest,
    session: SessionDep,
    store: PurgeStoreDep,
) -> TaskPurgeResultResponse:
    counts = execute_task_purge(session, store, task_id, payload.purge_token)
    return TaskPurgeResultResponse(**_counts_payload(counts))
