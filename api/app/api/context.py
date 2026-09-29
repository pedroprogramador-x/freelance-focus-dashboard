"""Router do Context Registry — `/api/workspaces/{id}/context` e `/api/context/{id}`.

[01](../../../docs/architecture/01-v1-architecture.md) §2–§3: esta é a **única** camada
desta feature que conhece FastAPI/HTTP. Toda a lógica vive em `app.context_engine`; aqui só
há validação de forma (Pydantic), abertura de sessão e projeção de resposta. O router não
conhece status de erro: levanta-se `ContextError` tipado e a tradução mora em `app.main`.

[06](../../../docs/architecture/06-api-and-ui-boundaries.md) §2, rotas desta fase:

| Método | Rota | Papel |
| --- | --- | --- |
| `GET` `POST` | `/api/workspaces/{id}/context` | Listar e criar entradas |
| `PATCH` `DELETE` | `/api/context/{entry_id}` | Editar e remover |
| `POST` | `/api/context/{entry_id}/edit-view` | Conteúdo cru, para edição |
| `POST` | `/api/workspaces/{id}/context/verify` | Recalcular `fresh`/`stale`/`unknown` |
| `POST` | `/api/workspaces/{id}/context/import` | Seed de planejamento |

A autenticação por `LocalSessionToken` e a guarda de mesma origem são **herdadas** do
middleware de `app.main` — nenhuma rota aqui as reimplementa, e uma rota nova sob `/api/`
nasce protegida sem ninguém precisar lembrar de anotá-la.

## `PATCH`: ausente ≠ nulo

`ContextEntryUpdate` tem todos os campos opcionais, e o router usa `model_fields_set` para
saber **quais vieram no corpo**. A distinção é obrigatória: `source_refs` ausente não toca o
baseline, `source_refs: []` o zera ([03] §3). Um `None` não conseguiria significar as duas
coisas, e `extra="forbid"` garante que um erro de digitação (`sourceRefs`) vire 422 em vez
de virar "campo ausente" silencioso.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.api.responses import EditViewJSONResponse
from app.context_engine import (
    ContextEntryUnreadable,
    PlanningSeed,
    PlanningSeedDecision,
    PlanningSeedRisk,
    create_entry,
    delete_entry,
    get_entry,
    import_planning_seed,
    list_entries,
    update_entry,
    verify_workspace_entries,
)
from app.context_engine.service import UNSET, edit_hash_of
from app.db.enums import ContextDomain, ContextOrigin, ContextState, StaleReason
from app.db.models import ContextRegistryEntry
from app.db.session import session_scope
from app.workspace import get_workspace

router = APIRouter(tags=["context"])

MAX_TITLE = 255
MAX_BODY = 200_000
MAX_REF = 4096
MAX_LIST = 200
#: `content_hash` é um SHA-256 em hexa; a folga cobre um prefixo de versão futuro.
MAX_HASH = 128


# ------------------------------------------------------------------ schemas


class ContextEntryCreate(BaseModel):
    """Corpo de `POST /api/workspaces/{id}/context`.

    `origin` **não** é aceito do cliente: uma entrada criada por esta rota é `manual` por
    definição. `imported_planning` só nasce da rota de import, e `generated` só a partir dos
    agentes consultivos (E8+). Deixar o cliente escolher permitiria forjar a procedência de
    uma entrada, que é justamente o que [03] §1 usa para explicar de onde o contexto veio.
    """

    model_config = ConfigDict(extra="forbid")

    domain: ContextDomain
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    body: str = Field(min_length=1, max_length=MAX_BODY)
    structured: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    source_refs: list[str] = Field(default_factory=list, max_length=MAX_LIST)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title não pode ser vazio")
        return stripped

    @field_validator("source_refs", "tags")
    @classmethod
    def _items_within_bounds(cls, value: list[str]) -> list[str]:
        for item in value:
            if len(item) > MAX_REF:
                raise ValueError(f"item excede {MAX_REF} caracteres")
        return value


class ContextEntryUpdate(BaseModel):
    """Corpo de `PATCH /api/context/{entry_id}`. Todo campo é opcional.

    `domain` e `origin` estão ausentes de propósito: trocar o domínio de uma entrada é criar
    outra entrada, e a origem é fato histórico. Quais campos vieram é lido de
    `model_fields_set` — ver o docstring do módulo.
    """

    model_config = ConfigDict(extra="forbid")

    #: O `edit_hash` que o editor leu ao abrir a entrada, em `POST .../edit-view`.
    #: **Obrigatório**, e substituiu `expected_content_hash` em E6-AUD5-004: aquele cobria
    #: só `domain`/`title`/`body`/`structured`, então dois editores podiam sobrescrever
    #: `tags` ou `source_refs` um do outro recebendo `200` os dois.
    #:
    #: `expected_content_hash` foi **removido** do contrato em vez de mantido como
    #: redundância: `edit_hash` já contém o `content_hash`, então nenhuma divergência de
    #: conteúdo escapa do novo campo, e um segundo controle estritamente mais fraco só
    #: serviria para alguém mandar o fraco e achar que está protegido.
    expected_edit_hash: str = Field(min_length=1, max_length=MAX_HASH)
    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE)
    body: str | None = Field(default=None, min_length=1, max_length=MAX_BODY)
    structured: dict[str, Any] | None = None
    tags: list[str] | None = Field(default=None, max_length=MAX_LIST)
    source_refs: list[str] | None = Field(default=None, max_length=MAX_LIST)

    @field_validator("source_refs", "tags")
    @classmethod
    def _items_within_bounds(cls, value: list[str] | None) -> list[str] | None:
        for item in value or []:
            if len(item) > MAX_REF:
                raise ValueError(f"item excede {MAX_REF} caracteres")
        return value


class ContextEntryResponse(BaseModel):
    """Projeção de leitura de uma `ContextRegistryEntry` ([02] §2)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    domain: ContextDomain
    title: str
    body: str
    structured: dict[str, Any] | None
    tags: list[str]
    source_refs: list[str]
    content_hash: str
    source_hash: str | None
    source_hash_commit: str | None
    state: ContextState
    stale_reason: StaleReason | None
    origin: ContextOrigin
    last_verified_at: str | None
    last_verified_commit: str | None
    created_at: str
    updated_at: str


class ContextEditViewResponse(BaseModel):
    """Conteúdo **cru** de uma entrada, para edição. Tipo estreito e de uso único.

    Não é `ContextEntryResponse` com outro nome: carrega só os três campos editáveis mais
    o `content_hash` que vira o `expected_content_hash` do `PATCH` seguinte. Reaproveitar a
    projeção de leitura aumentaria a superfície que escapa da redação sem nenhum ganho —
    `state`, `origin`, `source_hash` e as datas já saem redigidos pela rota normal, e não
    há motivo para uma segunda cópia deles fora do boundary.

    Nenhum campo desta resposta pode ser logado, virar `SafetyEvent` ou entrar em mensagem
    de erro — ver `EditViewJSONResponse`.
    """

    model_config = ConfigDict(extra="forbid")

    entry_id: str
    title: str
    body: str
    structured: dict[str, Any] | None
    content_hash: str
    #: A versão de edição desta leitura, que volta no `PATCH` como `expected_edit_hash`.
    #: Derivada, nunca persistida — ver `edit_hash_of`.
    edit_hash: str


class WorkingTreeDivergence(BaseModel):
    r"""[03] §3, "Registro no manifest".

    `dirty_file_count` é a divergência do **workspace inteiro**; `covered` são as
    divergências em caminhos cobertos por algum `source_refs`. A divergência é registrada
    **mesmo quando não afeta nenhuma entrada selecionada** — ela nunca é escondida.
    `dirty_file_count` é `null` quando a leitura do git não foi possível, que é diferente de
    `0` (árvore limpa).

    `unrepresentable_paths` (E4-AUD3-001) lista os caminhos que o git nomeia e que o
    envelope de `source_ref` não consegue nomear de volta — hoje, os que contêm `\` literal
    no nome. Quando essa lista não está vazia, a leitura do repositório **não pode ser
    usada**: as entradas com `source_refs` saem `unknown` e `dirty_file_count` é `null`. É o
    motivo registrado de um `unknown` que, sem ele, pareceria "o git não respondeu".
    """

    model_config = ConfigDict(extra="forbid")

    dirty_file_count: int | None
    covered: list[CoveredDivergence]
    unrepresentable_paths: list[str] = Field(default_factory=list)


class CoveredDivergence(BaseModel):
    """Um caminho coberto que diverge, e o tipo da divergência."""

    model_config = ConfigDict(extra="forbid")

    path: str
    kind: str
    entry_id: str


class ContextVerifyResponse(BaseModel):
    """Resposta de `POST /api/workspaces/{id}/context/verify`.

    `verification_commit` é o SHA que **toda** a verificação enxergou — capturado uma única
    vez no início e imutável durante ela ([03] §3). `null` quando o workspace não é
    repositório git ou ainda não tem commit; nesse caso as entradas com `source_refs` saem
    `unknown`.
    """

    model_config = ConfigDict(extra="forbid")

    verification_commit: str | None
    entries: list[ContextEntryResponse]
    working_tree_divergence: WorkingTreeDivergence


class PlanningImportDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    decision: str = Field(default="", max_length=MAX_BODY)
    reason: str = Field(default="", max_length=MAX_BODY)


class PlanningImportRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=MAX_BODY)
    mitigation: str = Field(default="", max_length=MAX_BODY)


class PlanningImport(BaseModel):
    """Corpo de `POST /api/workspaces/{id}/context/import` — o seed de [03] §1.

    O contrato é **anônimo e em `snake_case`**, sem o nome da entidade que o frontend por
    acaso usa como origem: [03] §1 diz que "o backend não sabe de onde veio", [ADR-0002]
    proíbe o acoplamento, e `test_architecture.py::test_backend_nao_conhece_o_dominio_
    comercial` transforma isso em falha de suíte. Quem traduz do modelo comercial para este
    formato é o frontend.
    """

    model_config = ConfigDict(extra="forbid")

    problem: str = Field(default="", max_length=MAX_BODY)
    objective: str = Field(default="", max_length=MAX_BODY)
    functional_requirements: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    non_functional_requirements: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    stack: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    architecture: str = Field(default="", max_length=MAX_BODY)
    technical_decisions: list[PlanningImportDecision] = Field(
        default_factory=list, max_length=MAX_LIST
    )
    risks: list[PlanningImportRisk] = Field(default_factory=list, max_length=MAX_LIST)

    def to_seed(self) -> PlanningSeed:
        return PlanningSeed(
            problem=self.problem,
            objective=self.objective,
            functional_requirements=tuple(self.functional_requirements),
            non_functional_requirements=tuple(self.non_functional_requirements),
            stack=tuple(self.stack),
            architecture=self.architecture,
            technical_decisions=tuple(
                PlanningSeedDecision(title=item.title, decision=item.decision, reason=item.reason)
                for item in self.technical_decisions
            ),
            risks=tuple(
                PlanningSeedRisk(description=item.description, mitigation=item.mitigation)
                for item in self.risks
            ),
        )


class ContextImportResponse(BaseModel):
    """O que a importação criou. **Sempre entradas novas** — reimportar nunca sobrescreve."""

    model_config = ConfigDict(extra="forbid")

    created: int
    entries: list[ContextEntryResponse]


WorkingTreeDivergence.model_rebuild()


# -------------------------------------------------------------- dependência


def _session(request: Request) -> Iterator[Session]:
    """Unidade de trabalho por requisição: commit no sucesso, rollback em exceção.

    Mesma dependência de `app.api.workspaces` — a `ContextError` atravessa este `yield`,
    dispara o rollback de `session_scope` e segue para o handler de `app.main`.
    """
    with session_scope(request.app.state.session_factory) as session:
        yield session


SessionDep = Annotated[Session, Depends(_session)]


def _to_response(entry: ContextRegistryEntry) -> ContextEntryResponse:
    return ContextEntryResponse(
        id=entry.id,
        workspace_id=entry.workspace_id,
        domain=entry.domain,
        title=entry.title,
        body=entry.body,
        structured=entry.structured,
        tags=list(entry.tags),
        source_refs=list(entry.source_refs),
        content_hash=entry.content_hash,
        source_hash=entry.source_hash,
        source_hash_commit=entry.source_hash_commit,
        state=entry.state,
        stale_reason=entry.stale_reason,
        origin=entry.origin,
        last_verified_at=(entry.last_verified_at.isoformat() if entry.last_verified_at else None),
        last_verified_commit=entry.last_verified_commit,
        created_at=entry.created_at.isoformat(),
        updated_at=entry.updated_at.isoformat(),
    )


# ------------------------------------------------------------------- rotas


@router.get(
    "/workspaces/{workspace_id}/context",
    response_model=list[ContextEntryResponse],
    summary="Listar entradas de contexto do workspace",
)
def index(
    workspace_id: str,
    session: SessionDep,
    domain: ContextDomain | None = None,
) -> list[ContextEntryResponse]:
    workspace = get_workspace(session, workspace_id)  # 404 se não existir
    return [_to_response(entry) for entry in list_entries(session, workspace.id, domain=domain)]


@router.post(
    "/workspaces/{workspace_id}/context",
    response_model=ContextEntryResponse,
    status_code=201,
    summary="Criar uma entrada de contexto",
)
def create(
    workspace_id: str, payload: ContextEntryCreate, session: SessionDep
) -> ContextEntryResponse:
    workspace = get_workspace(session, workspace_id)
    entry = create_entry(
        session,
        workspace,
        domain=payload.domain,
        title=payload.title,
        body=payload.body,
        structured=payload.structured,
        tags=payload.tags,
        source_refs=payload.source_refs,
        origin=ContextOrigin.MANUAL,
    )
    return _to_response(entry)


@router.patch(
    "/context/{entry_id}",
    response_model=ContextEntryResponse,
    summary="Editar uma entrada; só um source_refs informado mexe no baseline",
)
def patch(entry_id: str, payload: ContextEntryUpdate, session: SessionDep) -> ContextEntryResponse:
    entry = get_entry(session, entry_id)
    informados = payload.model_fields_set

    updated = update_entry(
        session,
        entry,
        expected_edit_hash=payload.expected_edit_hash,
        # `UNSET` para o que não veio no corpo. É aqui que "ausente" e "nulo" deixam de
        # poder ser confundidos: só um `source_refs` **informado** escreve baseline.
        title=payload.title if "title" in informados and payload.title is not None else UNSET,
        body=payload.body if "body" in informados and payload.body is not None else UNSET,
        structured=payload.structured if "structured" in informados else UNSET,
        tags=(payload.tags or []) if "tags" in informados else UNSET,
        source_refs=(payload.source_refs or []) if "source_refs" in informados else UNSET,
    )
    return _to_response(updated)


@router.post(
    "/context/{entry_id}/edit-view",
    # A **única** rota de `/api/*` fora do boundary de redação de [04] §5. Ver
    # `EditViewJSONResponse` e `test_o_conjunto_de_respostas_sem_redacao_e_fechado`.
    response_class=EditViewJSONResponse,
    response_model=ContextEditViewResponse,
    summary="Conteúdo cru da entrada, para edição",
)
def edit_view(entry_id: str, session: SessionDep, response: Response) -> ContextEditViewResponse:
    """O conteúdo **como está gravado**, para quem vai editá-lo (E6-AUD4-004).

    ## Por que esta rota existe

    A projeção de leitura sai redigida, como toda resposta ([04] §5). Um editor que carregue
    `GET .../context`, mostre o texto e salve de volta persiste `«redigido»` no lugar do
    trecho escondido: a redação, que é uma projeção de saída, viraria conteúdo. O `PATCH`
    recusa esse texto (`RedactedContentRejected`), e esta rota é o caminho que a recusa
    pressupõe.

    ## Por que `POST`, e não `GET`

    Não por ter efeito — ela não escreve nada. `POST` é o que a coloca sob a guarda de mesma
    origem de [01] §4, que o middleware aplica só a métodos mutantes: uma aba de terceiro não
    consegue disparar esta leitura e ler a resposta. Um `GET` equivalente seria alcançável
    por navegação simples, e o que ele devolve é o conteúdo **não redigido**.

    `Cache-Control: no-store` pelo mesmo motivo: nada disto pode sobrar em cache de disco.

    ## O que esta função deliberadamente não faz

    Não registra `SafetyEvent`, não escreve log, não emite métrica e não compõe mensagem de
    erro com o conteúdo. Um `404` daqui carrega o `entry_id` e nada mais — ele vem de
    `get_entry`, antes de qualquer campo ser lido. É a rota mais curta da API de propósito:
    cada linha a mais é uma chance de o conteúdo cru vazar para um canal que não passa pelo
    boundary.

    O `try` em volta da projeção é a outra metade disso (E6-AUD5-007): um documento gravado
    numa forma que o modelo recusa faria o Pydantic levantar uma `ValidationError` **com o
    valor recusado dentro**, e ela chegaria ao `uvicorn.error` inteira.
    """
    entry = get_entry(session, entry_id)
    response.headers["Cache-Control"] = "no-store"
    try:
        return ContextEditViewResponse(
            entry_id=entry.id,
            title=entry.title,
            body=entry.body,
            structured=entry.structured,
            content_hash=entry.content_hash,
            edit_hash=edit_hash_of(entry),
        )
    except ValidationError as error:
        # E6-AUD5-007: uma `ValidationError` que sobe daqui chega ao canal de exceções do
        # servidor **com o `input_value` junto** — isto é, com o conteúdo autoral cru num log
        # que não passa pelo boundary de [04] §5. O erro tipado tem mensagem constante, é
        # servido pelo handler de domínio e não gera *traceback*.
        #
        # `from None` é deliberado: encadear preservaria a `ValidationError` original como
        # `__cause__`, e qualquer formatador que imprimisse a cadeia traria o valor de volta.
        campos = ", ".join(str(item["loc"][-1]) for item in error.errors()) or "desconhecido"
        raise ContextEntryUnreadable(
            f"a entrada '{entry.id}' está gravada numa forma que a projeção de edição não "
            f"consegue ler (campo: {campos})"
        ) from None


@router.delete(
    "/context/{entry_id}",
    status_code=204,
    # `response_class` explícito: sem ele o FastAPI monta um `JSONResponse` e recusa o 204,
    # que por definição não pode ter corpo.
    response_class=Response,
    summary="Remover uma entrada (livre)",
)
def destroy(entry_id: str, session: SessionDep) -> Response:
    """[02] §2: exclusão **livre**, sem guarda. O artefato renderizado preserva o passado."""
    delete_entry(session, get_entry(session, entry_id))
    return Response(status_code=204)


@router.post(
    "/workspaces/{workspace_id}/context/verify",
    response_model=ContextVerifyResponse,
    summary="Recalcular fresh/stale/unknown de todas as entradas",
)
def verify(workspace_id: str, session: SessionDep) -> ContextVerifyResponse:
    """Verificação sob demanda ([03] §3): o `HEAD` é lido **uma vez** e vale para todas.

    O `verification_commit` devolvido é o mesmo que toda a verificação enxergou — quem
    quiser reproduzi-la depois sabe exatamente contra o quê.
    """
    workspace = get_workspace(session, workspace_id)
    snapshot, results = verify_workspace_entries(session, workspace)

    covered = [
        CoveredDivergence(path=item.path, kind=item.kind.value, entry_id=entry.id)
        for entry, outcome in results
        for item in outcome.covered_divergences
    ]

    # A Parte B (snapshot) e a Parte A (por entrada, via expansão) podem esbarrar em
    # caminhos diferentes; a resposta carrega a união dos dois (E4-AUD3-001).
    unrepresentable = set(snapshot.unrepresentable_paths)
    unrepresentable.update(
        path for _entry, outcome in results for path in outcome.unrepresentable_paths
    )

    return ContextVerifyResponse(
        verification_commit=snapshot.verification_commit,
        entries=[_to_response(entry) for entry, _outcome in results],
        working_tree_divergence=WorkingTreeDivergence(
            dirty_file_count=snapshot.dirty_file_count,
            covered=covered,
            unrepresentable_paths=sorted(unrepresentable),
        ),
    )


@router.post(
    "/workspaces/{workspace_id}/context/import",
    response_model=ContextImportResponse,
    status_code=201,
    summary="Seed a partir de um planejamento; sempre cria, nunca sobrescreve",
)
def import_seed(
    workspace_id: str, payload: PlanningImport, session: SessionDep
) -> ContextImportResponse:
    workspace = get_workspace(session, workspace_id)
    created = import_planning_seed(session, workspace, payload.to_seed())
    return ContextImportResponse(
        created=len(created), entries=[_to_response(entry) for entry in created]
    )
