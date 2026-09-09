"""Verificação de *staleness* — a regra de estado de [03] §3, numa implementação só.

Duas coisas escrevem `state` nesta fase, e as duas passam por `evaluate_freshness`:

* `service._establish_baseline` — na **criação** com `source_refs` não vazio e no `PATCH`
  que **altera** `source_refs`. Só ele escreve `source_hash` e `source_hash_commit`.
* `verify_freshness` — a verificação sob demanda. Ela **nunca** escreve o baseline: lê o
  armazenado, recalcula o candidato no `verification_commit` e compara. Escreve
  exclusivamente `state`, `stale_reason`, `last_verified_at` e `last_verified_commit`.

Decorre daí, e é o que [03] §3 tornou normativo no planejamento desta fase: **uma entrada
`stale` não volta a `fresh` só porque `verify()` rodou de novo.** O baseline não muda
quando `verify()` roda, então a comparação continua dando o mesmo resultado.

## `verification_commit` é recebido, nunca lido aqui

`verify_freshness` **não lê `HEAD`**. Ela recebe o SHA já congelado pelo chamador, uma
única vez no início da verificação ([03] §3, "`verification_commit` — o SHA que a
verificação enxerga"). É o que permite a E6 reutilizar esta função passando o
`planning_base_commit` da task sem duplicar nem parametrizar lógica nenhuma:

| Onde roda | `verification_commit` |
| --- | --- |
| `POST /api/tasks/{id}/plan` (E6+) | `= planning_base_commit` |
| `POST /api/workspaces/{id}/context/verify` (E4) | `= HEAD`, lido uma vez pelo chamador |

`capture_verification_commit` existe para o chamador da E4 fazer essa leitura única.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.context_engine.source_ref_expansion import (
    ExpansionStatus,
    SourceRefMatcher,
    build_matcher,
    expand_against_commit,
)
from app.db.base import utcnow
from app.db.enums import ContextState, StaleReason
from app.db.models import ContextRegistryEntry, DevWorkspace
from app.git_runtime import (
    UnrepresentablePath,
    WorkingTreeEntry,
    preflight,
    working_tree_diff_against,
)
from app.safety.policy import SafetyPolicy


@dataclass(frozen=True, slots=True)
class FreshnessOutcome:
    """O veredito da regra de estado, mais o material que o manifest de [03] §3 registra.

    ``candidate_source_hash`` é o hash **recalculado** no `verification_commit`. Ele é
    devolvido para diagnóstico e para o `_establish_baseline` gravar; `verify_freshness`
    o calcula e **não** o grava em lugar nenhum.
    """

    state: ContextState
    stale_reason: StaleReason | None
    candidate_source_hash: str | None
    verification_commit: str | None
    covered_divergences: tuple[WorkingTreeEntry, ...] = ()
    dirty_file_count: int | None = None
    #: Caminhos do workspace que o registro não sabe nomear (E4-AUD3-001), para
    #: diagnóstico. Presente em **qualquer** desfecho: a árvore estar incompleta é um fato
    #: sobre o repositório, e não necessariamente sobre esta entrada (E4-AUD5-001).
    unrepresentable_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceTreeSnapshot:
    """O que uma verificação lê do repositório — **uma vez** — e reusa para todas as entradas.

    Ler o git uma vez por entrada seria N execuções de processo para responder à mesma
    pergunta, e — pior — abriria a janela para duas entradas da mesma verificação verem
    árvores diferentes.

    ``divergences`` é a **Parte B contra o `verification_commit`** (E4-AUD-003), não contra
    o `HEAD`: são as diferenças entre a árvore de trabalho atual e o commit que a
    verificação enxerga. Quando os dois coincidem — o caso da verificação sob demanda da E4
    — isso é exatamente "o que está sujo agora". Quando não coincidem — o caso da E6, com o
    `planning_base_commit` congelado —, inclui também o que foi commitado depois da base,
    que é justamente o que faltava.
    """

    local_path: str
    verification_commit: str | None
    divergences: tuple[WorkingTreeEntry, ...] | None
    #: Caminhos divergentes que a leitura **não soube nomear** (E4-AUD3-001, E4-AUD4-002).
    #: Ficam **ao lado** de ``divergences``, nunca no lugar delas (E4-AUD5-001): uma
    #: divergência conhecida num arquivo legível continua conhecida mesmo que outro arquivo,
    #: sem relação, tenha nome ilegível na mesma leitura. Os bytes são preservados porque é
    #: sobre eles que se decide, por `source_ref`, se algum poderia tê-los alcançado.
    unrepresentable: tuple[UnrepresentablePath, ...] = ()

    @property
    def unrepresentable_paths(self) -> tuple[str, ...]:
        """Só os textos de diagnóstico, para a resposta da API."""
        return tuple(sorted({item.display for item in self.unrepresentable}))

    @property
    def dirty_file_count(self) -> int | None:
        """Divergências no workspace inteiro ([03] §3, `working_tree_divergence`).

        Conta **caminhos distintos**: um rename produz duas entradas (destino e origem) na
        leitura tipada, e contá-lo como dois arquivos sujos seria enganoso na UI.

        "Workspace" aqui é o `DevWorkspace`, não o repositório: caminhos fora de
        `local_path` já foram descartados por `git_runtime` (E4-AUD-002), porque um
        `source_ref` é relativo ao workspace e nunca poderia alcançá-los.
        """
        if self.divergences is None:
            return None
        return len({entry.path for entry in self.divergences})


def capture_verification_commit(local_path: str) -> str | None:
    """Lê o `HEAD` do workspace **uma única vez**. `None` se não é repo ou não tem commit.

    Só o chamador da verificação sob demanda (E4) usa esta função. A partir da E6 o SHA vem
    do `planning_base_commit` já congelado, e nada aqui muda.
    """
    return preflight(local_path).head


def read_workspace_tree(
    local_path: str, *, verification_commit: str | None = None
) -> WorkspaceTreeSnapshot:
    """Congela o estado do repositório para uma verificação inteira.

    ``verification_commit`` explícito é o caminho da E6 (`planning_base_commit`); omitido, o
    `HEAD` é lido aqui, uma vez só, e tratado como congelado pelo resto da chamada.

    As **duas** metades usam esse mesmo SHA: o `ls-tree` da Parte A e o
    `working_tree_diff_against` da Parte B. Era aqui que E4-AUD-003 sangrava — a Parte B
    chamava `git status`, que compara contra o `HEAD` do momento e ignora o commit
    congelado.
    """
    commit = (
        capture_verification_commit(local_path)
        if verification_commit is None
        else verification_commit
    )
    # Sem commit não há base contra a qual comparar, e a Parte B não tem pergunta a fazer.
    reading = None if commit is None else working_tree_diff_against(local_path, commit)
    if reading is None:
        # `None` é o único caso de "não consegui ler": aí não há divergência conhecida
        # nenhuma a preservar.
        return WorkspaceTreeSnapshot(
            local_path=local_path, verification_commit=commit, divergences=None
        )
    # Leitura parcial ainda é leitura (E4-AUD5-001): as divergências legíveis entram como
    # divergências, e os caminhos ilegíveis viajam ao lado delas.
    return WorkspaceTreeSnapshot(
        local_path=local_path,
        verification_commit=commit,
        divergences=reading.divergences,
        unrepresentable=reading.unrepresentable,
    )


def evaluate_freshness(
    *,
    source_refs: list[str],
    baseline_source_hash: str | None,
    snapshot: WorkspaceTreeSnapshot,
    policy: SafetyPolicy | None = None,
    treat_missing_baseline_as_current: bool = False,
) -> FreshnessOutcome:
    """A tabela de estado de [03] §3, inteira, num lugar só.

    | Situação | `state` | `stale_reason` |
    | --- | --- | --- |
    | `source_refs` vazio | `fresh` | — |
    | hash confere **e** nenhum path coberto diverge | `fresh` | — |
    | `source_hash` difere do armazenado | `stale` | `sources_changed` |
    | path coberto diverge no working tree | `stale` | `working_tree` |
    | irresolvível · não é repo · `git` indisponível | `unknown` | — |
    | caminho ilegível que **estes** `source_refs` poderiam cobrir | `unknown` | — (abaixo) |

    A última linha é E4-AUD3-001, refinada por E4-AUD5-001. Não há `stale_reason` para ela —
    [03] §3 define dois, e ambos afirmam divergência, que é justamente o que **não** se pode
    afirmar aqui. O motivo viaja em ``unrepresentable_paths``, que a resposta da verificação
    carrega até a API.

    **"Poderiam cobrir" é por entrada, não por workspace** (E4-AUD5-001). Até a 4ª rodada
    bastava existir um caminho ilegível em qualquer canto do repositório para toda entrada
    virar `unknown`. Isso jogava fora dois tipos de resposta certa: a recusa de um segredo
    que já tinha sido encontrado em arquivo legível, e o veredito de entradas que
    demonstravelmente não têm relação com o arquivo ilegível. A pergunta passou a ser feita
    por `source_ref`, sobre os **bytes** do caminho — ver
    `source_ref_expansion._may_cover`.

    ## Ordem de precedência

    **Notícia ruim definitiva > incompleto > notícia boa.** Nesta ordem:

    1. `stale` por `sources_changed` e por `working_tree` — os dois são afirmações que a
       leitura parcial não invalida: o hash já não confere, ou já se viu um arquivo coberto
       divergir. Continuam valendo com a árvore incompleta.
    2. `unknown` por incompletude — só o que a incompletude realmente impede.
    3. `fresh` — a única afirmação que exige ter visto tudo.

    A ordem entre as duas linhas de `stale` é a do documento: `sources_changed` vence.
    Quando o código commitado já mudou, dizer "o working tree diverge" seria o motivo menos
    grave e menos informativo.

    ``treat_missing_baseline_as_current`` é usado **apenas** por `_establish_baseline`, o
    momento em que o baseline está sendo escrito e portanto o candidato *é* o baseline por
    construção. `verify_freshness` nunca o liga: sem baseline armazenado e com
    `source_refs` declarados, a resposta honesta é `unknown` — a entrada diz depender de
    código e não há contra o que comparar.
    """
    if not source_refs:
        # Entrada autoral: não declara depender de código, então não há o que verificar.
        return FreshnessOutcome(
            state=ContextState.FRESH,
            stale_reason=None,
            candidate_source_hash=None,
            verification_commit=snapshot.verification_commit,
            dirty_file_count=snapshot.dirty_file_count,
        )

    unknown = FreshnessOutcome(
        state=ContextState.UNKNOWN,
        stale_reason=None,
        candidate_source_hash=None,
        verification_commit=snapshot.verification_commit,
        dirty_file_count=snapshot.dirty_file_count,
        unrepresentable_paths=snapshot.unrepresentable_paths,
    )

    if snapshot.verification_commit is None or snapshot.divergences is None:
        # Sem commit para enxergar, ou sem leitura do repositório: não é repo, é repo sem
        # nenhum commit, ou o `git` não respondeu. "Declara depender de código e não
        # consegui verificar" — que é a definição de `unknown` em [03] §3.
        return unknown

    # Parte B, calculada **antes** de qualquer decisão de estado (E4-AUD-006 e E4-AUD2-004).
    #
    # Duas versões anteriores devolviam cedo e perdiam esta evidência em situações
    # diferentes: a primeira em `sources_changed` (o motivo mais grave apagava o que o outro
    # teria a dizer), a segunda em `unknown` por `source_ref` irresolúvel. A segunda é a mais
    # enganosa das duas — `["src/**", "missing/**"]` devolvia cobertura vazia embora
    # `src/**` tivesse resolvido perfeitamente e houvesse divergência real sob `src/`.
    #
    # "Não sei se está tudo coberto" e "não vou dizer o que sei" são respostas diferentes. A
    # cobertura é calculada primeiro, sempre, e acompanha **todos** os desfechos.
    #
    # Ela é recalculada pela **mesma** gramática, contra os caminhos divergentes — não
    # contra a lista já expandida. Um arquivo novo e `untracked` sob `src/` é coberto por
    # `src/**` sem existir na árvore do commit, e é o caso que uma comparação com a lista
    # expandida deixaria passar. Note que o matcher é montado com **todos** os
    # `source_refs`, inclusive os que não resolveram contra a árvore: um ref que casa zero
    # arquivo no commit ainda pode cobrir um caminho não rastreado de hoje.
    matcher = build_matcher(source_refs, policy=policy)
    if not isinstance(matcher, SourceRefMatcher):
        # A gramática ou o envelope recusaram — aqui não é lugar de decidir isso, e sem
        # matcher não há cobertura a calcular. "Na dúvida, `unknown`".
        return unknown

    covered = tuple(entry for entry in snapshot.divergences if matcher.covers(entry.path))

    expansion = expand_against_commit(
        snapshot.local_path, snapshot.verification_commit, source_refs, policy=policy
    )
    if expansion.status is not ExpansionStatus.RESOLVED:
        # `UNRESOLVED` é o caso "refs irresolvíveis" da tabela. `DENIED` chega aqui só se a
        # política de segredos endureceu depois da escrita da entrada — e a resposta certa
        # continua sendo `unknown`: não conseguimos verificar, e não é papel do `verify`
        # apagar ou reescrever uma entrada por causa disso.
        #
        # Mas `unknown` **com** a cobertura que se conseguiu apurar (E4-AUD2-004), e com o
        # motivo, quando a Parte A esbarrou num caminho sem nome (E4-AUD3-001).
        return replace(
            unknown,
            covered_divergences=covered,
            unrepresentable_paths=tuple(
                sorted(set(unknown.unrepresentable_paths) | set(expansion.unrepresentable_paths))
            ),
        )

    candidate = expansion.source_hash
    effective_baseline = (
        candidate
        if treat_missing_baseline_as_current and baseline_source_hash is None
        else baseline_source_hash
    )
    if effective_baseline is None:
        # `unknown`, mas com a evidência da Parte B preservada: a entrada não é verificável
        # e ainda assim o usuário merece ver o que diverge no que ela declara cobrir.
        return replace(unknown, covered_divergences=covered)

    if candidate != effective_baseline:
        return FreshnessOutcome(
            state=ContextState.STALE,
            stale_reason=StaleReason.SOURCES_CHANGED,
            candidate_source_hash=candidate,
            verification_commit=snapshot.verification_commit,
            covered_divergences=covered,
            dirty_file_count=snapshot.dirty_file_count,
            unrepresentable_paths=snapshot.unrepresentable_paths,
        )

    if covered:
        return FreshnessOutcome(
            state=ContextState.STALE,
            stale_reason=StaleReason.WORKING_TREE,
            candidate_source_hash=candidate,
            verification_commit=snapshot.verification_commit,
            covered_divergences=covered,
            dirty_file_count=snapshot.dirty_file_count,
            unrepresentable_paths=snapshot.unrepresentable_paths,
        )

    # Última parada antes de `fresh` (E4-AUD5-001): a Parte A resolveu, o hash confere e
    # nenhuma divergência **legível** é coberta — mas houve divergência que a leitura não
    # soube nomear, e algum destes `source_refs` poderia tê-la alcançado. Dizer `fresh` aqui
    # seria afirmar sobre um arquivo que ninguém conseguiu ler.
    #
    # A posição desta checagem é a regra inteira em uma linha: **notícia ruim definitiva
    # vence incompleto, e incompleto vence notícia boa.** Ela vem depois dos dois `stale`
    # (que são definitivos, e continuam valendo mesmo com a leitura parcial) e antes do
    # `fresh` (que é a única afirmação que a incompletude realmente impede).
    if any(matcher.may_cover_unreadable(item.raw) for item in snapshot.unrepresentable):
        return replace(unknown, covered_divergences=covered)

    return FreshnessOutcome(
        state=ContextState.FRESH,
        stale_reason=None,
        candidate_source_hash=candidate,
        verification_commit=snapshot.verification_commit,
        dirty_file_count=snapshot.dirty_file_count,
        unrepresentable_paths=snapshot.unrepresentable_paths,
    )


def verify_freshness(
    entry: ContextRegistryEntry,
    snapshot: WorkspaceTreeSnapshot,
    *,
    policy: SafetyPolicy | None = None,
) -> FreshnessOutcome:
    """Verifica **uma** entrada e grava só os quatro campos que [03] §3 autoriza.

    Escreve: `state`, `stale_reason`, `last_verified_at`, `last_verified_commit`. Esses
    quatro, e **nenhum outro** — `updated_at` inclusive (E4-AUD-010).
    **Não escreve**, em nenhuma circunstância: `source_hash`, `source_hash_commit`,
    `content_hash`, `source_refs`, `body`, `title`, `structured`, `tags`, `updated_at`.

    Sem isso, uma entrada `stale` "curaria" ao ser verificada de novo — o baseline
    acompanharia o código e a divergência sumiria justamente quando alguém foi conferi-la.
    """
    outcome = evaluate_freshness(
        source_refs=list(entry.source_refs or []),
        baseline_source_hash=entry.source_hash,
        snapshot=snapshot,
        policy=policy,
    )

    entry.state = outcome.state
    entry.stale_reason = outcome.stale_reason
    entry.last_verified_at = utcnow()
    entry.last_verified_commit = outcome.verification_commit
    # `updated_at` **não** é tocado (E4-AUD-010). Ele marca a última edição *autoral* da
    # entrada; uma verificação não edita a entrada, ela só emite um veredito sobre ela.
    # Mexer nele faria "verificar" parecer "modificar" em toda listagem ordenada por
    # `updated_at`, e apagaria a informação de quando o conteúdo mudou de verdade.
    return outcome


def verify_workspace_entries(
    session: Session,
    workspace: DevWorkspace,
    *,
    verification_commit: str | None = None,
    policy: SafetyPolicy | None = None,
) -> tuple[WorkspaceTreeSnapshot, list[tuple[ContextRegistryEntry, FreshnessOutcome]]]:
    """Verifica todas as entradas do workspace contra **um** `verification_commit`.

    O snapshot é lido uma vez e reusado: as duas metades da verificação (o `ls-tree` da
    Parte A e o `status` da Parte B) enxergam o mesmo SHA, e todas as entradas enxergam o
    mesmo working tree.
    """
    snapshot = read_workspace_tree(workspace.local_path, verification_commit=verification_commit)
    entries = list(
        session.scalars(
            select(ContextRegistryEntry)
            .where(ContextRegistryEntry.workspace_id == workspace.id)
            .order_by(ContextRegistryEntry.created_at, ContextRegistryEntry.id)
        )
    )

    results = [(entry, verify_freshness(entry, snapshot, policy=policy)) for entry in entries]
    session.flush()
    return snapshot, results
