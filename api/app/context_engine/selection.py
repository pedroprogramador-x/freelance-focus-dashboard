"""Context Router — a seleção determinística de [03] §4 e [ADR-0006] item 7.

> "A seleção de contexto é determinística. Pontuação por sobreposição de `source_refs`,
> domínio afetado, tags, proximidade no file map, frescor e recência."

Esta é a camada **transitória** das três de [02] §5: ela responde *quais candidatos, com
que pontuação*, e não persiste nada. Quem congela é `manifest.freeze_manifest`.

## O que "determinístico" exige, concretamente

Determinismo aqui não é "não chama LLM". É a lista abaixo, e cada item dela é um jeito
conhecido de o Python variar em silêncio entre duas execuções do mesmo código:

* **Nenhum `float` decide nada.** Pesos, distâncias e o corte de orçamento são `int`. Uma
  soma de `float` pode diferir na última casa entre plataformas, e o efeito seria um
  desempate mudando de lado sem que nada semântico tenha mudado.
* **`updated_at` é comparado como epoch inteiro em segundos**, calculado por aritmética de
  `timedelta` — nunca `datetime.timestamp()`, que devolve `float`, nem string ISO, cuja
  ordem lexicográfica depende do formato de saída.
* **A chave de ordenação é total**: `score` desc → `updated_at` desc → `entry_id` asc.
  `entry_id` é único, então nenhum empate sobra para a ordem do `SELECT` desempatar. É o
  que faz a ordem de inserção no banco não importar.
* **Toda coleção que entra em score ou em hash é ordenada por chave estável.** Nenhuma
  ordem de `dict`, de `set`, de inserção ou de consulta SQL sobrevive até o resultado.
* **O corte de orçamento mede o texto real**, produzido por `rendering.render_block_text`
  — a mesma função que o artefato grava. Não existe estimativa paralela.

## Orçamento — a política V1 de [02] §5

Sem truncamento parcial: entrada que não cabe inteira é excluída inteira, com
`reason=budget`. `domain=objective` é sempre incluída, mesmo que sozinha ultrapasse
`max_context_tokens` — e nesse caso `approx_tokens > max_context_tokens` é o resultado
esperado, não um defeito.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.context_engine.errors import ContextError
from app.context_engine.file_map import FileMap, build_file_map_from_tree
from app.context_engine.rendering import (
    DEFAULT_TRANSFORMATIONS,
    Transformation,
    approx_tokens,
    normalize_transformations,
    render_block_text,
)
from app.context_engine.source_ref_expansion import (
    ExpansionStatus,
    SourceRefMatcher,
    build_matcher,
    expand_source_refs,
)
from app.context_engine.verification import read_workspace_tree
from app.db.enums import ContextDomain
from app.db.models import ContextRegistryEntry, DevWorkspace
from app.git_runtime import TreeListing, WorkingTreeEntry, list_tree
from app.safety.policy import SafetyPolicy
from app.safety.source_refs import validate_source_ref

# --------------------------------------------------------------------------- pesos

#: Match **literal exato** entre um `source_ref` da entrada e um `candidate_path` — não
#: cobertura por glob, comparação de string entre as duas formas já normalizadas. Fecha
#: AUD-008: [03] §4 exige que este sinal supere **qualquer** combinação possível de
#: `SCORE_SOURCE_REF_OVERLAP + PROXIMITY_MAX` (100 + 30 = 130), então `131` — a menor
#: margem inteira que garante a vitória sem depender de nenhum outro sinal.
#:
#: **Independe de o candidato existir no `base_commit`**: é comparação de string entre
#: `CompiledSourceRef.normalized` (literal, sem metacaractere de glob) e um
#: `candidate_path` já normalizado — nunca resolução contra a árvore. E não é "todos os
#: `source_refs` são literais": é match contra um candidato **específico**. Uma entrada
#: com `source_refs=["src/app.py"]` só ganha o bônus se `"src/app.py"` estiver entre os
#: candidatos — apontar para um caminho literal que ninguém pediu não é precisão, é
#: coincidência.
SCORE_EXACT_LITERAL_MATCH = 131

#: Sobreposição entre os `source_refs` da entrada e os caminhos candidatos da análise.
#: Peso "alto" de [03] §4. Binário: uma entrada que cobre um candidato cobre o assunto.
#: Somar por arquivo casado faria uma entrada com `src/**` vencer qualquer entrada precisa
#: só por apontar para mais arquivos.
SCORE_SOURCE_REF_OVERLAP = 100

#: `domain` entre os domínios que a análise marcou como afetados. O outro peso "alto".
SCORE_AFFECTED_DOMAIN = 100

#: Casamento de `tags` com termos do objetivo. Peso "médio".
SCORE_TAG_MATCH = 50

#: Proximidade no file map: `max(0, PROXIMITY_MAX - distância)`. A distância é o número de
#: saltos de diretório entre o que a entrada cobre e o que a análise apontou.
PROXIMITY_MAX = 30

#: `state = fresh`. Bônus pequeno de [03] §4 — não é o que decide relevância, é o que
#: decide entre duas entradas igualmente relevantes.
SCORE_FRESH = 10

#: Motivos de exclusão de [02] §5. São exatamente três; não há um quarto.
REASON_BUDGET = "budget"
REASON_SECRET_POLICY = "secret_policy"  # noqa: S105 — motivo de exclusão, não credencial
REASON_OUT_OF_WORKSPACE = "out_of_workspace"

#: `rule_id` do envelope que significa "este caminho é segredo" ([04] §5). Qualquer outra
#: recusa é problema de caminho, não de política de segredo.
_SECRET_RULE_ID = "source_ref.secret_denied"  # noqa: S105 — `rule_id`, não credencial

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class ContextTreeUnavailable(ContextError):
    """A árvore de `base_commit` não pôde ser lida: sem git, sem repo, ou SHA inválido.

    **409**, e não 404: o workspace existe e a entrada existe; o que falta é a base contra
    a qual selecionar. Sem árvore não há file map, não há expansão e não há `source_files`
    — e um manifest sem isso mentiria sobre o que congelou.
    """

    code = "context_tree_unavailable"
    status_code = 409


# --------------------------------------------------------------------------- estruturas


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    """Os seis sinais, separados. Todos `int`; `total` é a soma deles, nada mais.

    Guardar as parcelas — e não só o total — é o que permite responder *por que* uma
    entrada ficou acima de outra sem reexecutar a seleção.
    """

    exact_literal_match: int = 0
    source_ref_overlap: int = 0
    affected_domain: int = 0
    tag_match: int = 0
    proximity: int = 0
    fresh: int = 0

    @property
    def total(self) -> int:
        return (
            self.exact_literal_match
            + self.source_ref_overlap
            + self.affected_domain
            + self.tag_match
            + self.proximity
            + self.fresh
        )

    def as_canonical(self) -> dict[str, Any]:
        return {
            "exact_literal_match": self.exact_literal_match,
            "source_ref_overlap": self.source_ref_overlap,
            "affected_domain": self.affected_domain,
            "tag_match": self.tag_match,
            "proximity": self.proximity,
            "fresh": self.fresh,
            "total": self.total,
        }


@dataclass(frozen=True, slots=True)
class ScoredEntry:
    """Uma entrada pontuada e já renderizada.

    ``text`` é o texto **emitido**, produzido por `render_block_text` — o mesmo objeto que
    o artefato vai gravar, não uma prévia dele. ``chars`` e ``tokens`` medem exatamente
    ele, depois da redação.

    Os campos de identidade (``entry_id``, ``domain``, ``title``, ``content_hash``,
    ``state``, ``stale_reason``) são cópias, não referências à linha: o manifest e o
    artefato precisam continuar respondendo depois de a entrada de origem ser apagada
    ([ADR-0006] item 9).
    """

    entry_id: str
    domain: str
    #: **Já redigido** — o mesmo `RenderedBlock.title` que compõe `text`. Nunca o valor cru
    #: de `entry.title`: é o que garante que `origin.title`/`entries[].title` (que leem
    #: este campo) nunca carreguem um segredo que `text` já não carrega (fecha AUD-002).
    title: str
    content_hash: str
    state: str
    stale_reason: str | None
    #: Epoch em **microssegundos** ([03] §4, critério de desempate). Fecha AUD-005: por
    #: segundo inteiro, duas entradas só diferindo em microssegundo empatavam e caíam para
    #: o `entry_id`, que não tem relação com recência.
    updated_at_epoch_us: int
    score: ScoreBreakdown
    text: str
    chars: int
    tokens: int
    transformations: tuple[str, ...]
    #: `True` para `domain=objective`: entra sempre, mesmo estourando o orçamento.
    always_included: bool
    #: Arquivos que os `source_refs` desta entrada resolveram no `base_commit`.
    source_files: tuple[tuple[str, str], ...] = ()
    #: Os `source_refs` **como declarados**, não a lista expandida. A cobertura da Parte B
    #: de [03] §3 precisa do padrão: um arquivo novo e não rastreado sob `src/` é coberto
    #: por `src/**` sem existir na árvore do commit, e comparar com a lista expandida
    #: perderia exatamente esse caso — o mais comum de todos.
    source_refs: tuple[str, ...] = ()

    def as_manifest_entry(self) -> dict[str, Any]:
        """A linha de `ContextManifest.entries` de [02] §5."""
        return {
            "entry_id": self.entry_id,
            "domain": self.domain,
            "title": self.title,
            "content_hash": self.content_hash,
            "state": self.state,
            "stale_reason": self.stale_reason,
        }


@dataclass(frozen=True, slots=True)
class ExcludedItem:
    """Uma linha de `ContextManifest.excluded`: `{path_or_entry, reason}` ([02] §5)."""

    path_or_entry: str
    reason: str

    def as_canonical(self) -> dict[str, Any]:
        return {"path_or_entry": self.path_or_entry, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class ContextSelection:
    """A camada transitória inteira. Nada aqui foi persistido ainda."""

    base_commit: str
    file_map: FileMap
    #: Candidatos aceitos, normalizados, deduplicados e ordenados.
    candidate_paths: tuple[str, ...]
    #: Todas as entradas do workspace, pontuadas e na ordem do ranking.
    ranked: tuple[ScoredEntry, ...]
    #: As que entraram, na ordem em que serão emitidas.
    selected: tuple[ScoredEntry, ...]
    #: Exclusões, ordenadas por `(reason, path_or_entry)`.
    excluded: tuple[ExcludedItem, ...]
    #: `{dirty_file_count, covered: [{path, kind}]}` de [03] §3.
    working_tree_divergence: dict[str, Any]
    max_context_tokens: int
    approx_tokens: int
    total_chars: int

    @property
    def source_files(self) -> tuple[dict[str, str], ...]:
        """`[{path, blob_sha}]` das entradas **selecionadas**, congelada e ordenada.

        União por caminho: um mesmo arquivo coberto por duas entradas aparece uma vez. O
        `blob_sha` não pode divergir entre elas — as duas leram a mesma árvore do mesmo
        commit, onde `path` é único.
        """
        merged: dict[str, str] = {}
        for entry in self.selected:
            for path, blob_sha in entry.source_files:
                merged[path] = blob_sha
        return tuple({"path": path, "blob_sha": merged[path]} for path in sorted(merged))


# --------------------------------------------------------------------------- utilidades


def epoch_microseconds(value: datetime) -> int:
    """`datetime` → microssegundos inteiros desde a época, sem passar por `float`.

    Fecha AUD-005: a versão anterior desempatava por segundo inteiro
    (`days*86400 + seconds`), então duas entradas que só diferiam em microssegundo
    empatavam nesse critério e caíam para o próximo (`entry_id`) — que não tem relação
    nenhuma com recência. `(days*86400 + seconds) * 1_000_000 + microseconds` preserva a
    resolução real que `updated_at` já tem no banco.

    `datetime.timestamp()` devolve `float`: a partir de ~2038 a mantissa de 53 bits ainda
    representa segundos exatamente, mas a conversão passa por um tipo cuja aritmética
    depende de arredondamento, e o desempate da seleção é decidido nessa comparação. A
    subtração de `datetime` devolve `timedelta`, cujos campos (`days`, `seconds`,
    `microseconds`) são inteiros por construção — a aritmética inteira aqui não perde nada
    que `timedelta` já não tenha descartado (sub-microssegundo, que `datetime` não guarda).
    """
    delta = value.astimezone(UTC) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _fold(term: str) -> str:
    """Forma comparável de um termo: NFC, sem espaço nas pontas, *case-folded*.

    `casefold()` é definido pelo Unicode e não consulta locale — `str.lower()` também não
    em Python, mas `casefold` é o que trata `ß`/`ss` e é a operação certa para comparação
    insensível a caixa.
    """
    return unicodedata.normalize("NFC", term).strip().casefold()


def _directory_distance(left: str, right: str) -> int:
    """Saltos de diretório entre dois `dir_path` — subir até o ancestral comum e descer.

    `"src/a"` e `"src/b"` distam 2; `"src"` e `"src/a"` distam 1; iguais distam 0. É a
    distância de árvore, não de string: `"src"` e `"srcx"` não são vizinhos por
    compartilharem prefixo textual.
    """
    left_segments = left.split("/") if left else []
    right_segments = right.split("/") if right else []

    common = 0
    for a, b in zip(left_segments, right_segments, strict=False):
        if a != b:
            break
        common += 1

    return (len(left_segments) - common) + (len(right_segments) - common)


def _dir_of(path: str) -> str:
    head, separator, _name = path.rpartition("/")
    return head if separator else ""


# --------------------------------------------------------------------------- candidatos


def _classify_candidates(
    candidate_paths: Iterable[str], policy: SafetyPolicy
) -> tuple[tuple[str, ...], tuple[ExcludedItem, ...]]:
    """Normaliza, valida e deduplica os candidatos. Recusa vira `excluded`.

    Passa por `validate_source_ref` — o **mesmo** envelope dos `source_refs`, com
    `allow_glob_syntax=False`: o parâmetro se chama `candidate_paths` e é isso que ele é
    nesta fase. Um segredo apontado pela análise sai como `excluded(secret_policy)`, que é
    a única camada em que [ADR-0006] item 9 permite um segredo aparecer — como **caminho**,
    nunca como conteúdo. Qualquer outra recusa de envelope (`..`, absoluto, UNC, nome
    reservado) é `out_of_workspace`.

    A ordenação final é por caminho, e a deduplicação acontece **depois** da normalização:
    `./src/app.py` e `src/app.py` são o mesmo candidato, e contá-los duas vezes não mudaria
    score nenhum mas mudaria a lista congelada no manifest.
    """
    accepted: set[str] = set()
    excluded: dict[tuple[str, str], ExcludedItem] = {}

    for raw in candidate_paths:
        result = validate_source_ref(raw, policy=policy, allow_glob_syntax=False)
        if result.normalized is None:
            reason = (
                REASON_SECRET_POLICY
                if result.decision.rule_id == _SECRET_RULE_ID
                else REASON_OUT_OF_WORKSPACE
            )
            subject = result.decision.subject_redacted or raw
            excluded[(reason, subject)] = ExcludedItem(path_or_entry=subject, reason=reason)
            continue
        accepted.add(result.normalized)

    ordered_exclusions = tuple(excluded[key] for key in sorted(excluded))
    return tuple(sorted(accepted)), ordered_exclusions


# --------------------------------------------------------------------------- scoring


def _has_exact_literal_match(
    matcher: SourceRefMatcher | None, candidate_paths: frozenset[str]
) -> bool:
    """Existe `source_ref` desta entrada que é padrão **literal** (sem metacaractere de
    glob) e cujo valor normalizado é **exatamente** um `candidate_path`?

    Comparação de string entre `CompiledSourceRef.normalized` e o conjunto de candidatos
    já normalizados — nunca resolução contra `base_commit`. Um `source_ref` glob (mesmo
    que resolva para um único arquivo) não conta: `.literal` só é `True` para o padrão sem
    `*`/`?`/`[`/`**` ([04]/`source_ref_expansion.compile_source_ref`).
    """
    if matcher is None:
        return False
    return any(ref.literal and ref.normalized in candidate_paths for ref in matcher.compiled)


def _score_entry(
    entry: ContextRegistryEntry,
    *,
    candidate_paths: tuple[str, ...],
    candidate_paths_set: frozenset[str],
    candidate_dirs: tuple[str, ...],
    matcher: SourceRefMatcher | None,
    resolved_files: tuple[tuple[str, str], ...],
    affected: frozenset[str],
    objective_terms: frozenset[str],
) -> ScoreBreakdown:
    """A tabela de [03] §4, inteira, com aritmética exclusivamente inteira."""
    exact_literal = (
        SCORE_EXACT_LITERAL_MATCH if _has_exact_literal_match(matcher, candidate_paths_set) else 0
    )

    overlap = 0
    if matcher is not None and any(matcher.covers(path) for path in candidate_paths):
        overlap = SCORE_SOURCE_REF_OVERLAP

    domain = SCORE_AFFECTED_DOMAIN if entry.domain.value in affected else 0

    tags = frozenset(_fold(tag) for tag in (entry.tags or []))
    tag_match = SCORE_TAG_MATCH if tags & objective_terms else 0

    proximity = _proximity_score(resolved_files, candidate_dirs)

    fresh = SCORE_FRESH if entry.state.value == "fresh" else 0

    return ScoreBreakdown(
        exact_literal_match=exact_literal,
        source_ref_overlap=overlap,
        affected_domain=domain,
        tag_match=tag_match,
        proximity=proximity,
        fresh=fresh,
    )


def _proximity_score(
    resolved_files: tuple[tuple[str, str], ...], candidate_dirs: tuple[str, ...]
) -> int:
    """`max(0, PROXIMITY_MAX - menor distância)` entre o que a entrada cobre e os candidatos.

    Sem arquivo resolvido ou sem candidato, não há distância a medir e o sinal vale `0` —
    "não sei" não é "perto". A entrada que **sobrepõe** um candidato tem distância `0` e
    ganha os 30 pontos por cima dos 100 da sobreposição: proximidade e sobreposição são
    sinais diferentes de [03] §4, e nenhum dos dois absorve o outro.
    """
    if not resolved_files or not candidate_dirs:
        return 0

    entry_dirs = sorted({_dir_of(path) for path, _sha in resolved_files})
    closest = min(
        _directory_distance(entry_dir, candidate_dir)
        for entry_dir in entry_dirs
        for candidate_dir in candidate_dirs
    )
    return max(0, PROXIMITY_MAX - closest)


def _rank_key(scored: ScoredEntry) -> tuple[int, int, str]:
    """Chave de ordenação **total**: score desc → `updated_at` (microssegundo) desc →
    `entry_id` asc.

    A negação faz o `sort` crescente ordenar os dois primeiros de forma decrescente sem um
    `reverse=True` que inverteria também o `entry_id`. `entry_id` é PK, então a chave nunca
    empata — e é por isso que a ordem de inserção no banco não altera o resultado.
    """
    return (-scored.score.total, -scored.updated_at_epoch_us, scored.entry_id)


# --------------------------------------------------------------------------- seleção


def select_context(
    session: Session,
    workspace: DevWorkspace,
    *,
    base_commit: str,
    candidate_paths: list[str],
    max_context_tokens: int,
    affected_domains: Iterable[ContextDomain] = (),
    objective_terms: Iterable[str] = (),
    transformations: tuple[Transformation, ...] = DEFAULT_TRANSFORMATIONS,
    policy: SafetyPolicy | None = None,
) -> ContextSelection:
    """Seleciona o contexto de `workspace` no `base_commit` congelado. **Sem LLM, sem rede.**

    ``base_commit`` é o `planning_base_commit` de [02] §6, já congelado pelo chamador —
    esta função não lê `HEAD`, pela mesma razão que `verify_freshness` não lê ([03] §3).

    ``affected_domains`` e ``objective_terms`` são a saída do Task Analyzer ([03] §5), que
    só existe a partir da E6. Nascem vazios: sem análise, os dois sinais correspondentes
    valem zero para todas as entradas e a seleção continua determinística — ela apenas se
    apoia nos outros três.

    Levanta `ContextTreeUnavailable` quando a árvore do commit não pôde ser lida.
    """
    active = policy or SafetyPolicy()
    applied = normalize_transformations(transformations)

    tree = list_tree(workspace.local_path, base_commit)
    if tree is None:
        raise ContextTreeUnavailable(
            f"não foi possível ler a árvore do commit '{base_commit}' em "
            f"'{workspace.name}': o workspace pode não ser um repositório git, o git pode "
            "estar indisponível, ou o commit pode não existir"
        )

    file_map = build_file_map_from_tree(tree)
    candidates, candidate_exclusions = _classify_candidates(candidate_paths, active)
    candidate_paths_set = frozenset(candidates)
    candidate_dirs = tuple(sorted({_dir_of(path) for path in candidates}))

    affected = frozenset(domain.value for domain in affected_domains)
    terms = frozenset(_fold(term) for term in objective_terms if _fold(term))

    ranked = _rank_entries(
        session,
        workspace,
        tree=tree,
        candidates=candidates,
        candidate_paths_set=candidate_paths_set,
        candidate_dirs=candidate_dirs,
        affected=affected,
        terms=terms,
        applied=applied,
        policy=active,
    )

    selected, budget_exclusions = _apply_budget(ranked, max_context_tokens)

    excluded = tuple(
        sorted(
            [*candidate_exclusions, *budget_exclusions],
            key=lambda item: (item.reason, item.path_or_entry),
        )
    )

    return ContextSelection(
        base_commit=base_commit,
        file_map=file_map,
        candidate_paths=candidates,
        ranked=ranked,
        selected=selected,
        excluded=excluded,
        working_tree_divergence=_working_tree_divergence(workspace, base_commit, selected, active),
        max_context_tokens=max_context_tokens,
        approx_tokens=sum(item.tokens for item in selected),
        total_chars=sum(item.chars for item in selected),
    )


def _rank_entries(
    session: Session,
    workspace: DevWorkspace,
    *,
    tree: TreeListing,
    candidates: tuple[str, ...],
    candidate_paths_set: frozenset[str],
    candidate_dirs: tuple[str, ...],
    affected: frozenset[str],
    terms: frozenset[str],
    applied: tuple[Transformation, ...],
    policy: SafetyPolicy,
) -> tuple[ScoredEntry, ...]:
    """Pontua e renderiza todas as entradas do workspace, na ordem do ranking.

    O `ORDER BY` da consulta existe só para tornar a leitura reprodutível durante a
    depuração; ele **não** é o que garante o resultado. Quem garante é `_rank_key`, que é
    total — a consulta poderia devolver em qualquer ordem que a saída seria a mesma.
    """
    entries = list(
        session.scalars(
            select(ContextRegistryEntry)
            .where(ContextRegistryEntry.workspace_id == workspace.id)
            .order_by(ContextRegistryEntry.id)
        )
    )

    scored: list[ScoredEntry] = []
    for entry in entries:
        refs = list(entry.source_refs or [])
        matcher_or_decision = build_matcher(refs, policy=policy) if refs else None
        matcher = matcher_or_decision if isinstance(matcher_or_decision, SourceRefMatcher) else None

        expansion = expand_source_refs(refs, tree, policy=policy)
        resolved = expansion.files if expansion.status is ExpansionStatus.RESOLVED else ()

        breakdown = _score_entry(
            entry,
            candidate_paths=candidates,
            candidate_paths_set=candidate_paths_set,
            candidate_dirs=candidate_dirs,
            matcher=matcher,
            resolved_files=resolved,
            affected=affected,
            objective_terms=terms,
        )

        rendered = render_block_text(entry, applied)
        scored.append(
            ScoredEntry(
                entry_id=entry.id,
                domain=entry.domain.value,
                # `rendered.title` — já redigido, nunca `entry.title` cru (fecha AUD-002).
                title=rendered.title,
                content_hash=entry.content_hash,
                state=entry.state.value,
                stale_reason=entry.stale_reason.value if entry.stale_reason else None,
                updated_at_epoch_us=epoch_microseconds(entry.updated_at),
                score=breakdown,
                text=rendered.text,
                chars=len(rendered.text),
                tokens=approx_tokens(rendered.text),
                # Vem do renderizador, não daqui: só ele sabe se `CROSS_FRAGMENT_REDACTION`
                # foi acionado neste bloco.
                transformations=rendered.transformations,
                always_included=entry.domain is ContextDomain.OBJECTIVE,
                source_files=resolved,
                source_refs=tuple(refs),
            )
        )

    return tuple(sorted(scored, key=_rank_key))


def _apply_budget(
    ranked: tuple[ScoredEntry, ...], max_context_tokens: int
) -> tuple[tuple[ScoredEntry, ...], tuple[ExcludedItem, ...]]:
    """O corte de [02] §5, na política V1: sem truncamento parcial.

    Duas passadas, e a ordem entre elas é a regra:

    1. **`domain=objective` entra sempre**, na ordem do ranking, consumindo orçamento. Se
       isso sozinho já ultrapassa `max_context_tokens`, ultrapassou — [02] §5 declara esse
       resultado esperado. Uma seleção sem o objetivo seria contexto sobre nada.
    2. **O resto entra enquanto couber inteiro.** Uma entrada que não cabe é excluída
       inteira, com `reason=budget`, e a varredura **continua**: uma entrada menor mais
       abaixo no ranking ainda pode caber. Parar na primeira que não coube desperdiçaria
       orçamento já pago sem melhorar a ordem, e a ordem de emissão é preservada de todo
       jeito porque a lista final é reordenada pelo ranking.
    """
    kept: list[ScoredEntry] = []
    excluded: list[ExcludedItem] = []
    used = 0

    for item in ranked:
        if item.always_included:
            kept.append(item)
            used += item.tokens

    for item in ranked:
        if item.always_included:
            continue
        if used + item.tokens <= max_context_tokens:
            kept.append(item)
            used += item.tokens
        else:
            excluded.append(ExcludedItem(path_or_entry=item.entry_id, reason=REASON_BUDGET))

    return tuple(sorted(kept, key=_rank_key)), tuple(excluded)


def _working_tree_divergence(
    workspace: DevWorkspace,
    base_commit: str,
    selected: tuple[ScoredEntry, ...],
    policy: SafetyPolicy,
) -> dict[str, Any]:
    """`{dirty_file_count, covered}` de [03] §3, contra o **`base_commit` congelado**.

    ``dirty_file_count`` é do workspace inteiro e é registrado sempre — [03] §3: "a
    divergência é registrada mesmo quando não afeta nenhuma entrada selecionada. Ela nunca
    é escondida". ``covered`` é o recorte que as entradas selecionadas de fato cobrem.

    A cobertura é calculada **por entrada** e depois unida, nunca por um matcher único
    montado com a união dos `source_refs`: um `source_ref` que a política recusa hoje
    invalidaria o matcher inteiro e apagaria a cobertura conhecida das outras entradas —
    exatamente o *fail open* que E4-AUD5-001 fechou.
    """
    snapshot = read_workspace_tree(workspace.local_path, verification_commit=base_commit)

    covered: dict[tuple[str, str], WorkingTreeEntry] = {}
    if snapshot.divergences:
        for item in selected:
            refs = list(item.source_refs)
            if not refs:
                continue
            matcher = build_matcher(refs, policy=policy)
            if not isinstance(matcher, SourceRefMatcher):
                continue
            for divergence in snapshot.divergences:
                if matcher.covers(divergence.path):
                    covered[(divergence.path, divergence.kind.value)] = divergence

    return {
        "dirty_file_count": snapshot.dirty_file_count,
        "covered": [{"path": path, "kind": kind} for path, kind in sorted(covered)],
    }


__all__ = [
    "PROXIMITY_MAX",
    "REASON_BUDGET",
    "REASON_OUT_OF_WORKSPACE",
    "REASON_SECRET_POLICY",
    "SCORE_AFFECTED_DOMAIN",
    "SCORE_EXACT_LITERAL_MATCH",
    "SCORE_FRESH",
    "SCORE_SOURCE_REF_OVERLAP",
    "SCORE_TAG_MATCH",
    "ContextSelection",
    "ContextTreeUnavailable",
    "ExcludedItem",
    "ScoreBreakdown",
    "ScoredEntry",
    "epoch_microseconds",
    "select_context",
]
