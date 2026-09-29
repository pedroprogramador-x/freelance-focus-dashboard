"""Serviço do Context Registry — CRUD, `content_hash`, baseline e seed.

[01](../../../docs/architecture/01-v1-architecture.md) §2: toda a lógica de negócio da
feature vive aqui; `app.api.context` é só transporte.

## A regra do baseline, que é o coração desta sub-etapa

`source_hash` e `source_hash_commit` são o **baseline confirmado** ([03] §3). Eles são
escritos em **exatamente dois momentos**, e em nenhum outro:

1. na **criação** de uma entrada cujo `source_refs` não é vazio;
2. no `PATCH` que **altera explicitamente** `source_refs`.

Um `PATCH` que não menciona `source_refs` **jamais** toca esses dois campos, nem `state`,
nem `stale_reason` — mesmo mudando `body`, `title`, `structured` e `tags` de uma vez. Editar
o texto de uma entrada muda o `content_hash` (§2, "a entrada foi editada?") e não tem nada
a dizer sobre o `source_hash` (§3, "o código descrito mudou?"). São perguntas diferentes, e
misturá-las faria uma correção de digitação "curar" uma entrada `stale`.

`source_refs` alterado para **lista vazia** zera o baseline (os dois campos → `NULL`) e
devolve a entrada a `fresh` sem `stale_reason`, pela primeira linha da tabela de estado.

`verify()` não aparece nesta lista: ele nunca escreve baseline (ver `verification.py`).

## Distinção entre "não informado" e "informado como vazio"

O `PATCH` precisa distinguir `source_refs` ausente de `source_refs: []` — o primeiro não
toca em nada, o segundo zera o baseline. Um `None` não serve para os dois papéis, então o
serviço usa o sentinela `UNSET`. É a mesma distinção que o `extra="forbid"` + campos
opcionais do Pydantic fazem na borda HTTP, trazida para a camada de domínio, onde ela
também precisa valer para um chamador interno.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.context_engine.content_hash import (
    compute_content_hash,
    compute_edit_hash,
    normalize_structured,
)
from app.context_engine.errors import (
    ContextChanged,
    ContextEntryNotFound,
    InvalidContextEntry,
    InvalidSourceRefs,
    RedactedContentRejected,
)
from app.context_engine.source_ref_expansion import (
    ExpansionStatus,
    expand_against_commit,
    validate_and_compile,
)
from app.context_engine.types import PlanningSeed
from app.context_engine.verification import (
    WorkspaceTreeSnapshot,
    evaluate_freshness,
    read_workspace_tree,
)
from app.db.base import utcnow
from app.db.enums import ContextDomain, ContextOrigin, ContextState, StaleReason
from app.db.models import ContextRegistryEntry, DevWorkspace
from app.safety.canonical import CanonicalizationError, canonical_json
from app.safety.policy import SafetyPolicy
from app.safety.redaction import contains_redaction_marker
from app.safety.types import SafetyDecision

#: [02] §2: `title` é `String(255)`. O SQLite não recusa um VARCHAR longo (E3-AUD-007), e
#: um chamador interno não passa pelo Pydantic — o invariante vive aqui.
MAX_TITLE_LENGTH = 255


class _Unset(Enum):
    """Sentinela de "campo não informado", distinto de "informado como `None`/vazio"."""

    SENTINEL = "unset"

    def __repr__(self) -> str:  # pragma: no cover — só melhora a mensagem de erro
        return "UNSET"


#: `_Unset` tem um único membro, então `x is not UNSET` estreita `str | _Unset` para `str`
#: no mypy sem `cast` nem `assert`. É por isso que o sentinela é um `Enum` e não um objeto
#: qualquer: o tipo carrega a distinção que a regra do baseline depende de não perder.
UNSET = _Unset.SENTINEL


# --------------------------------------------------------------------------- validação


def _reject_redaction_markers(campo: str, valor: object) -> None:
    """Recusa conteúdo autoral que carregue marcador de redação (E6-AUD4-004).

    Vale para toda escrita do registry — `POST`, `PATCH` e o seed de importação —, porque
    as três desembocam nos mesmos limpadores. Pôr a guarda aqui, e não na borda HTTP, é a
    mesma razão de E3-AUD-007: um chamador interno não passa pelo Pydantic, e o invariante
    é do domínio.

    A varredura entra em `structured` inteiro, **chave e valor**, em qualquer profundidade:
    é exatamente onde a resposta redigida põe `«chave redigida N»`, e um `PATCH` que
    devolvesse o documento inteiro reescreveria os nomes de campo com os placeholders.
    """
    if isinstance(valor, str):
        if contains_redaction_marker(valor):
            raise RedactedContentRejected(
                f"{campo} contém marcador de redação e não pode ser persistido como "
                "conteúdo autoral; use POST /api/context/{entry_id}/edit-view para obter "
                "o conteúdo cru antes de editar"
            )
        return
    if isinstance(valor, dict):
        for chave, item in valor.items():
            _reject_redaction_markers(f"{campo} (chave)", chave)
            _reject_redaction_markers(campo, item)
        return
    if isinstance(valor, list | tuple):
        for item in valor:
            _reject_redaction_markers(campo, item)


def _clean_title(title: str) -> str:
    cleaned = title.strip()
    if not cleaned:
        raise InvalidContextEntry("title não pode ser vazio")
    if len(cleaned) > MAX_TITLE_LENGTH:
        raise InvalidContextEntry(f"title excede {MAX_TITLE_LENGTH} caracteres")
    _reject_redaction_markers("title", cleaned)
    return cleaned


def _clean_body(body: str) -> str:
    if not body or not body.strip():
        raise InvalidContextEntry("body não pode ser vazio")
    _reject_redaction_markers("body", body)
    return body


def _clean_tags(tags: list[str] | None) -> list[str]:
    """Ordenadas e sem duplicata: a mesma entrada produz sempre a mesma linha.

    `tags` **não** entra no `content_hash` ([03] §2), então ordenar aqui não muda hash
    nenhum — é só o que torna a leitura e a comparação estáveis.
    """
    if not tags:
        return []
    cleaned = [tag.strip() for tag in tags if tag and tag.strip()]
    _reject_redaction_markers("tags", cleaned)
    return sorted(dict.fromkeys(cleaned))


def _clean_structured(structured: dict[str, Any] | None) -> dict[str, Any] | None:
    if structured is None:
        return None
    if not isinstance(structured, dict):
        raise InvalidContextEntry("structured precisa ser um objeto JSON")
    try:
        # `normalize_structured` levanta em colisão de chave (E4-AUD-007); `canonical_json`
        # levanta em tipo não serializável. As duas viram o mesmo 422.
        normalize_structured(structured)
        canonical_json(structured)
    except CanonicalizationError as error:
        raise InvalidContextEntry(f"structured não é canonizável: {error}") from error
    _reject_redaction_markers("structured", structured)
    return structured


def edit_hash_of(entry: ContextRegistryEntry) -> str:
    """A versão de edição da entrada — **derivada**, nunca uma coluna.

    Persistir seria criar um segundo lugar onde a verdade mora, com todos os modos de falha
    de cache invalidado: uma escrita que esquecesse de atualizá-la produziria um `409`
    fantasma, ou pior, deixaria passar a corrida que ela existe para pegar. Derivar custa um
    `sha256` por leitura e não pode ficar defasado.
    """
    return compute_edit_hash(
        content_hash=entry.content_hash,
        tags=list(entry.tags),
        source_refs=list(entry.source_refs),
    )


def _content_hash_of(entry: ContextRegistryEntry) -> str:
    return compute_content_hash(
        domain=entry.domain.value,
        title=entry.title,
        body=entry.body,
        structured=entry.structured,
    )


# ---------------------------------------------------------------------------- baseline


@dataclass(frozen=True, slots=True)
class _Baseline:
    """Os quatro campos que a escrita de baseline produz. Calculado antes de ser aplicado."""

    source_hash: str | None
    source_hash_commit: str | None
    state: ContextState
    stale_reason: StaleReason | None


def _compute_baseline(
    source_refs: list[str],
    snapshot: WorkspaceTreeSnapshot,
    *,
    policy: SafetyPolicy | None = None,
) -> _Baseline:
    """Calcula o baseline e o `state`. **Não escreve nada** — quem aplica é o chamador.

    Calcular antes de aplicar é o que torna `create_entry` e `update_entry` atômicos por si
    sós: um `source_ref` recusado levanta `InvalidSourceRefs` (422) **antes** de qualquer
    mutação, então a entrada nunca fica meio alterada. Depender do `rollback` do chamador
    funcionaria pela rota HTTP (`session_scope` desfaz tudo), mas deixaria um chamador
    interno futuro com um objeto inconsistente na mão.

    Quando a árvore não pode ser lida (não é repo, sem commit, refs irresolvíveis), o
    resultado é `unknown` **sem** baseline: gravar um `source_hash` que não veio de uma
    leitura de verdade seria fabricar a linha de base. Isso **não** dispensa a validação de
    envelope e gramática, que roda antes e independe de git (E4-AUD-004).
    """
    if not source_refs:
        # [03] §3: `source_refs` vazio zera o baseline e devolve o estado a `fresh`.
        return _Baseline(
            source_hash=None,
            source_hash_commit=None,
            state=ContextState.FRESH,
            stale_reason=None,
        )

    # Etapa (a) — envelope e gramática. **Sempre**, com ou sem git (E4-AUD-004). Era aqui
    # que a validação inteira ficava atrás de um `if commit is not None`, e um workspace sem
    # repositório aceitava `../**`, `.env*` e `[` com `201`. Não conseguir resolver contra
    # uma árvore nunca foi motivo para deixar de conferir a forma do `source_ref`.
    compiled = validate_and_compile(source_refs, policy=policy)
    if isinstance(compiled, SafetyDecision):
        raise InvalidSourceRefs(compiled.reason, decision=compiled)

    # Etapa (b) — resolução e classificação de segredo. Só com um commit legível.
    if snapshot.verification_commit is not None:
        expansion = expand_against_commit(
            snapshot.local_path, snapshot.verification_commit, source_refs, policy=policy
        )
        if expansion.status is ExpansionStatus.DENIED:
            assert expansion.decision is not None
            raise InvalidSourceRefs(expansion.decision.reason, decision=expansion.decision)

    outcome = evaluate_freshness(
        source_refs=source_refs,
        baseline_source_hash=None,
        snapshot=snapshot,
        policy=policy,
        # O baseline está sendo escrito agora: o candidato **é** a linha de base, por
        # construção. É o único ponto do sistema que pode dizer isso.
        treat_missing_baseline_as_current=True,
    )

    return _Baseline(
        source_hash=outcome.candidate_source_hash,
        source_hash_commit=(
            outcome.verification_commit if outcome.candidate_source_hash is not None else None
        ),
        state=outcome.state,
        stale_reason=outcome.stale_reason,
    )


def _apply_baseline(entry: ContextRegistryEntry, baseline: _Baseline) -> None:
    """**O único lugar do sistema** que escreve `source_hash` e `source_hash_commit`.

    `verification.verify_freshness` escreve `state`/`stale_reason` também, mas nunca estes
    dois — é o que garante que uma entrada `stale` não se cure ao ser reverificada.
    """
    entry.source_hash = baseline.source_hash
    entry.source_hash_commit = baseline.source_hash_commit
    entry.state = baseline.state
    entry.stale_reason = baseline.stale_reason


# -------------------------------------------------------------------------------- CRUD


def create_entry(
    session: Session,
    workspace: DevWorkspace,
    *,
    domain: ContextDomain,
    title: str,
    body: str,
    structured: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    source_refs: list[str] | None = None,
    origin: ContextOrigin = ContextOrigin.MANUAL,
    snapshot: WorkspaceTreeSnapshot | None = None,
    policy: SafetyPolicy | None = None,
) -> ContextRegistryEntry:
    """Cria uma entrada. Estabelece o baseline **se e somente se** `source_refs` não é vazio.

    ``snapshot`` permite ao chamador congelar o repositório uma vez e criar várias entradas
    contra o mesmo `verification_commit` — é o que a importação do seed usa. Omitido, é lido
    aqui, uma vez.
    """
    refs = list(source_refs or [])
    clean = {
        "title": _clean_title(title),
        "body": _clean_body(body),
        "structured": _clean_structured(structured),
        "tags": _clean_tags(tags),
    }

    # Validação e expansão **antes** de instanciar: um `source_ref` recusado levanta 422 sem
    # ter posto nada na sessão.
    active_snapshot = (
        snapshot if snapshot is not None else read_workspace_tree(workspace.local_path)
    )
    baseline = _compute_baseline(refs, active_snapshot, policy=policy)

    entry = ContextRegistryEntry(
        workspace_id=workspace.id,
        domain=domain,
        source_refs=refs,
        origin=origin,
        state=ContextState.FRESH,
        stale_reason=None,
        **clean,
    )
    entry.content_hash = _content_hash_of(entry)
    _apply_baseline(entry, baseline)

    session.add(entry)
    session.flush()
    return entry


def list_entries(
    session: Session,
    workspace_id: str,
    *,
    domain: ContextDomain | None = None,
) -> list[ContextRegistryEntry]:
    """Entradas do workspace, na ordem de criação. `domain` opcional filtra."""
    query = (
        select(ContextRegistryEntry)
        .where(ContextRegistryEntry.workspace_id == workspace_id)
        .order_by(ContextRegistryEntry.created_at, ContextRegistryEntry.id)
    )
    if domain is not None:
        query = query.where(ContextRegistryEntry.domain == domain)
    return list(session.scalars(query))


def get_entry(session: Session, entry_id: str) -> ContextRegistryEntry:
    entry = session.get(ContextRegistryEntry, entry_id)
    if entry is None:
        raise ContextEntryNotFound(f"entrada de contexto '{entry_id}' não encontrada")
    return entry


def update_entry(
    session: Session,
    entry: ContextRegistryEntry,
    *,
    expected_edit_hash: str,
    title: str | _Unset = UNSET,
    body: str | _Unset = UNSET,
    structured: dict[str, Any] | None | _Unset = UNSET,
    tags: list[str] | _Unset = UNSET,
    source_refs: list[str] | _Unset = UNSET,
    snapshot: WorkspaceTreeSnapshot | None = None,
    policy: SafetyPolicy | None = None,
) -> ContextRegistryEntry:
    """Edita uma entrada. **`source_refs` ausente ⇒ o baseline não é tocado.**

    `domain` e `origin` não são editáveis: mudar o domínio de uma entrada é criar outra
    entrada, e a origem é um fato histórico ([03] §1 — o seed nasce `imported_planning` e
    não deixa de ter nascido assim porque alguém editou o texto depois).

    A ordem importa. O `content_hash` é recalculado a partir dos valores **já aplicados**,
    e o baseline só é mexido depois, e só se `source_refs` foi informado.

    ## `expected_edit_hash` é obrigatório, e cobre **tudo** que o PATCH pode mudar

    Ele é o `edit_hash` que o editor leu quando abriu a entrada. Divergiu, alguém escreveu no
    meio e a edição é recusada com `409` sem sobrescrever nada — controle de concorrência
    **otimista**: nada é travado, só detectado.

    Ele substituiu `expected_content_hash` em E6-AUD5-004. O `content_hash` é normativo sobre
    outra pergunta ([03] §2, "a entrada foi editada?") e cobre `domain`/`title`/`body`/
    `structured` — **não** cobre `tags` nem `source_refs`. Usá-lo como versão da entrada
    deixava passar a perda de atualização exatamente nesses dois campos: dois editores com a
    mesma leitura mandavam `tags` diferentes, os dois recebiam `200`, e o segundo apagava o
    primeiro. O `edit_hash` reaproveita o `content_hash` e acrescenta os dois — sem mudar o
    que o `content_hash` significa.

    Obrigatório porque opcional é a mesma inversão de modelo que três auditorias seguidas
    encontraram na redação: uma garantia que o chamador precisa lembrar de pedir nasce
    ausente em todo cliente novo, e o esquecimento é silencioso.

    A comparação acontece **dentro da mesma transação** da escrita: o `edit_hash` atual é
    recalculado a partir do estado que a sessão enxerga, e o `flush` da escrita acontece sem
    sair dela.
    """
    # A comparação vem antes de qualquer validação: recusar por conflito não deve depender
    # de o corpo novo estar bem formado, e um `409` é mais informativo que um `422` quando
    # as duas coisas são verdade.
    if edit_hash_of(entry) != expected_edit_hash:
        raise ContextChanged(
            f"a entrada '{entry.id}' mudou desde a leitura: o estado atual não "
            "corresponde ao expected_edit_hash enviado"
        )

    # Tudo é validado e calculado antes de qualquer atribuição: uma recusa em `source_refs`
    # não pode deixar `title` e `body` já trocados no objeto.
    novo_titulo = _clean_title(title) if title is not UNSET else None
    novo_corpo = _clean_body(body) if body is not UNSET else None
    novo_structured = _clean_structured(structured) if structured is not UNSET else None
    novas_tags = _clean_tags(tags) if tags is not UNSET else None

    baseline: _Baseline | None = None
    novos_refs: list[str] | None = None
    if source_refs is not UNSET:
        # O único caminho de `PATCH` que escreve baseline. `verification_commit` é capturado
        # **agora**, no momento do PATCH ([03] §3): o baseline é sempre medido no commit em
        # que a decisão de cobertura foi tomada.
        novos_refs = list(source_refs)
        active_snapshot = (
            snapshot
            if snapshot is not None
            else read_workspace_tree(_workspace_path(session, entry))
        )
        baseline = _compute_baseline(novos_refs, active_snapshot, policy=policy)

    # A partir daqui nada mais levanta.
    if novo_titulo is not None:
        entry.title = novo_titulo
    if novo_corpo is not None:
        entry.body = novo_corpo
    if structured is not UNSET:
        entry.structured = novo_structured
    if novas_tags is not None:
        entry.tags = novas_tags

    entry.content_hash = _content_hash_of(entry)

    if novos_refs is not None and baseline is not None:
        entry.source_refs = novos_refs
        _apply_baseline(entry, baseline)

    entry.updated_at = utcnow()
    session.flush()
    return entry


def delete_entry(session: Session, entry: ContextRegistryEntry) -> None:
    """Exclusão **livre**, sem restrição ([02] §2).

    O Rendered Context Artifact preserva o que foi entregue (§5), então apagar uma entrada
    não corrompe a auditoria histórica. Não há guarda, e nem deveria haver.
    """
    session.delete(entry)
    session.flush()


def _workspace_path(session: Session, entry: ContextRegistryEntry) -> str:
    workspace = session.get(DevWorkspace, entry.workspace_id)
    if workspace is None:  # pragma: no cover — FK com ON DELETE CASCADE impede
        raise ContextEntryNotFound(f"workspace '{entry.workspace_id}' não encontrado")
    return workspace.local_path


# ------------------------------------------------------------------------------- seed


def _bullets(items: tuple[str, ...] | list[str]) -> str:
    return "\n".join(f"- {item.strip()}" for item in items if item and item.strip())


def _title_from(text: str, *, fallback: str) -> str:
    """Título a partir da primeira linha de um texto livre, cortado em `MAX_TITLE_LENGTH`."""
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first_line:
        return fallback
    if len(first_line) <= MAX_TITLE_LENGTH:
        return first_line
    return first_line[: MAX_TITLE_LENGTH - 1].rstrip() + "…"


def import_planning_seed(
    session: Session,
    workspace: DevWorkspace,
    seed: PlanningSeed,
    *,
    policy: SafetyPolicy | None = None,
) -> list[ContextRegistryEntry]:
    """Seed de [03] §1. Todas as entradas nascem `imported_planning`, `source_refs=[]`, `fresh`.

    Mapeamento, exatamente a tabela de [03] §1:

    | Campo do seed | Vira |
    | --- | --- |
    | `problem` + `objective` | **uma** entrada `objective` |
    | `functional_requirements[]` / `non_functional_requirements[]` | **duas** `requirements` |
    | `stack[]` | **uma** `stack` |
    | `architecture` | **uma** `architecture` |
    | `technical_decisions[]` | **N** `decisions` |
    | `risks[]` | **N** `risks` |

    Campo vazio **não** vira entrada: `body` não pode ser vazio ([02] §2), e uma entrada de
    contexto sem conteúdo seria ruído para o Context Router da E5 pontuar.

    **Reimportar cria entradas novas e nunca sobrescreve** ([03] §1 + [ADR-0006] item 2). A
    importação acontece uma vez; depois os dois modelos evoluem independentemente, e
    sincronização bidirecional reintroduziria o acoplamento que [ADR-0002] elimina. Uma
    entrada editada à mão depois do seed seria silenciosamente desfeita por um reimport que
    sobrescrevesse — daí a regra ser "sempre cria", com a duplicata visível na UI.

    O snapshot do repositório é lido **uma vez** para a importação inteira, embora nenhuma
    entrada de seed tenha `source_refs`: mantém o custo constante e o caminho idêntico ao do
    `create_entry` avulso.
    """
    snapshot = read_workspace_tree(workspace.local_path)
    created: list[ContextRegistryEntry] = []

    def add(
        domain: ContextDomain,
        title: str,
        body: str,
        structured: dict[str, Any] | None = None,
    ) -> None:
        if not body.strip():
            return
        created.append(
            create_entry(
                session,
                workspace,
                domain=domain,
                title=title,
                body=body,
                structured=structured,
                tags=[],
                source_refs=[],
                origin=ContextOrigin.IMPORTED_PLANNING,
                snapshot=snapshot,
                policy=policy,
            )
        )

    objective_body = "\n\n".join(
        section
        for section in (
            f"## Problema\n\n{seed.problem.strip()}" if seed.problem.strip() else "",
            f"## Objetivo\n\n{seed.objective.strip()}" if seed.objective.strip() else "",
        )
        if section
    )
    add(ContextDomain.OBJECTIVE, "Objetivo", objective_body)

    add(
        ContextDomain.REQUIREMENTS,
        "Requisitos funcionais",
        _bullets(seed.functional_requirements),
    )
    add(
        ContextDomain.REQUIREMENTS,
        "Requisitos não funcionais",
        _bullets(seed.non_functional_requirements),
    )
    add(ContextDomain.STACK, "Stack", _bullets(seed.stack))
    add(ContextDomain.ARCHITECTURE, "Arquitetura", seed.architecture)

    for decision in seed.technical_decisions:
        body = decision.decision.strip() or decision.title.strip()
        add(
            ContextDomain.DECISIONS,
            _title_from(decision.title, fallback="Decisão técnica"),
            body,
            # [03] §1: `structured = {decision, reason, date, supersedes?}`. `date` fica
            # `null` — o seed não carrega data, e inventar `utcnow()` registraria a data da
            # importação como se fosse a data da decisão.
            {
                "decision": decision.decision.strip(),
                "reason": decision.reason.strip(),
                "date": None,
            },
        )

    for risk in seed.risks:
        add(
            ContextDomain.RISKS,
            _title_from(risk.description, fallback="Risco"),
            risk.description,
            # [03] §1: `structured = {mitigation, probability?, impact?}`. As duas opcionais
            # não vêm do seed e por isso não são inventadas como `null`.
            {"mitigation": risk.mitigation.strip()},
        )

    return created
