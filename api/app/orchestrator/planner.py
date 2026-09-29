"""Execution Planner — `POST /api/tasks/{id}/plan`.

O passo que [07] resume como "planejar sem executar nada". A ordem abaixo é normativa e
cada item dela responde a uma linha de documento:

1. `git_runtime.probe_head` — congela `planning_base_commit` e `base_branch` **uma única
   vez** ([02] §6). Nada relê `HEAD` depois disso;
2. Task Analyzer ([03] §5);
3. Resource Router ([03] §6);
4. **`verify_workspace_entries`** ([03] §3) contra o commit congelado;
5. `select_context` ([03] §4) sobre o mesmo commit;
6. `freeze_manifest` ([02] §5) — que grava o artefato **por dentro**;
7. `plan` + `plan_hash`;
8. `test_binding` a partir de `DevWorkspace.test_config` ([04] §6);
9. `execution_fingerprint` candidato ([02] §7);
10. `planning → awaiting_approval`.

## O passo 4 existe porque o plano não pode congelar um `fresh` velho (E6-AUD-005)

[06] §2 descreve a rota como "congela `base_commit`, **verifica contexto**, seleciona,
renderiza, planeja", e a verificação faltava. `select_context` lê `entry.state` **do
registry** — o veredito da última verificação, que pode ser de semanas atrás. Uma entrada
marcada `fresh` no commit A, com a fonte alterada e commitada em B, entrava no manifest
como `fresh`: o `manifest_hash` afirmava frescor que não existia mais, e a guarda
`no_stale_entry_when_high_risk` de [02] §4 passava a depender de o usuário ter clicado em
"verificar" antes de planejar. *Fail-open* sobre a única guarda que protege uma aprovação
de alto risco.

`verify_workspace_entries` é a **API canônica da E4**, e recebe o `planning_base_commit`
que o passo 1 já congelou — nenhuma segunda captura de `HEAD` acontece. É o mesmo
princípio do `verification_commit` de [03] §3: quem inicia lê o SHA uma vez, e todo o resto
da operação enxerga aquele SHA.

## Duas chamadas ao Context Engine, nunca três

`freeze_manifest` chama `render_context` internamente (`context_engine/manifest.py`), e o
faz **antes** de montar a linha porque `rendered_context_hash` é `NOT NULL`. Chamar
`render_context` aqui também gravaria o artefato duas vezes e — pior — abriria a
possibilidade de o hash que o Planner conhece divergir do que o manifest registrou. O
Planner faz `select_context` e `freeze_manifest`, e nada mais.

## `planning_base_commit` congela antes da seleção, não depois

[02] §6: `planning_base_commit == ContextManifest.git_head == Run.base_commit`. O preflight
roda como **primeiro** passo e o SHA viaja por parâmetro para `select_context` e para
`freeze_manifest`. É o mesmo princípio do `verification_commit` da E4 ([03] §3): quem
inicia captura o SHA uma vez, e ninguém mais lê `HEAD` durante a operação. Duas leituras de
`HEAD` num mesmo planejamento podem ver commits diferentes, e o manifest passaria a afirmar
um commit que a seleção não enxergou.

## Decisões desta sessão (não especificadas em nenhum documento)

**`candidate_paths` vêm do payload de `POST /plan`, explicitamente.** [06] §2 descreve a
rota ("congela `base_commit`, verifica contexto, seleciona, renderiza, planeja") sem dizer
de onde saem os candidatos. As duas opções eram tokenizar o objetivo bruto ou receber uma
lista. A lista venceu: `select_context._classify_candidates` passa cada candidato por
`validate_source_ref`, e **toda** palavra de um objetivo em prosa que não seja um caminho
válido viraria `excluded(out_of_workspace)` — entrando no `manifest_hash`, que é comparável
entre tasks por desenho. Encher o manifest de lixo derivado de prosa destruiria essa
comparabilidade para ganhar um sinal que o próprio [03] §4 já diz nascer vazio sem análise.
Lista vazia é o caso normal e bem definido: os outros três sinais de scoring continuam
valendo. A partir da E8 a porta de enriquecimento acrescenta candidatos inferidos, e
`analyze` já une as duas fontes.

**`plan_hash = sha256(canonical_json(plan))`**, sobre o documento `plan` exatamente como
persistido. [02] §7 nomeia `plan_hash` no fingerprint mas não define a fórmula. Usar o
próprio documento — e não um subconjunto dele — é o que garante que *qualquer* mudança no
plano que o humano leu invalide a aprovação. `canonical_json` já proíbe transitório ([02]
§7), então o documento não carrega timestamp nem id.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.context_engine import (
    ContextSelection,
    freeze_manifest,
    select_context,
    verify_workspace_entries,
)
from app.db.enums import ContextState, WorkspaceStatus
from app.db.models import ContextManifest, DevWorkspace, WorkspaceTask
from app.git_runtime import (
    PROBE_NOT_A_REPO,
    PROBE_OK,
    PROBE_UNVERIFIABLE,
    PROBE_WITHOUT_HEAD,
    HeadProbe,
    probe_head,
)
from app.orchestrator.analyzer import AnalyzerEnrichmentPort, TaskAnalysis, analyze
from app.orchestrator.errors import InvalidTestConfig, WorkspaceNotPlannable
from app.orchestrator.fingerprint import (
    FingerprintParts,
    WorkflowPolicy,
    tool_profile_hash,
)
from app.orchestrator.resource_router import (
    DEFAULT_MAX_CONTEXT_TOKENS,
    DEFAULT_MAX_TOTAL_TOKENS,
    MAX_AGENTS,
    MAX_ATTEMPTS,
    MAX_FIX_ROUNDS,
    MAX_PARALLEL_AGENTS,
    ResourceDecision,
    route,
)
from app.safety.canonical import canonical_sha256
from app.safety.test_policy import (
    NULL_TEST_BINDING,
    InvalidTestPolicy,
    TestPolicy,
    parse_test_policy,
)
from app.workspace.policy import effective_policy_hash, resolve_effective_policy

#: Versão da forma do documento `plan`. Muda quando a estrutura muda, para que um
#: `plan_hash` desta versão não colida com o de outra.
PLAN_VERSION = 1

#: `developer_binding` enquanto nenhum `DeveloperProvider` existe (antes da E8).
#:
#: [02] §7 trata a ausência do **auditor** explicitamente (`null`), e a mesma lógica se
#: aplica aqui pelo mesmo motivo: omitir a chave faria "sem developer" e "developer X"
#: colidirem. `null` explícito é o que distingue os dois — e, quando E8 injetar um
#: adaptador, o fingerprint muda e toda aprovação vigente é invalidada, que é exatamente o
#: comportamento que [04] §7 exige de uma mudança de binding.
NULL_BINDING: None = None


# --------------------------------------------------------- pré-condições de planejamento
#
# E6-CONS4. As pré-condições que `POST /plan` aplica ao **workspace** vivem aqui, como
# funções que devolvem um slug de bloqueio (ou `None`). Dois consumidores e nenhum terceiro:
#
# * o próprio planejamento (`execution_manager.plan` e `_resolve_base_commit` abaixo), que
#   levanta o erro correspondente;
# * `orchestrator.eligibility.planning_eligibility`, que só as **reporta**.
#
# As quatro rodadas consolidadas anteriores foram o mesmo defeito: a orientação de ação era
# recalculada fora do Planner, cada vez com um subconjunto das guardas. Com uma função por
# guarda e os dois lados chamando a mesma, "a elegibilidade diz sim e o plano recusa" deixa
# de ser um caso de manutenção e passa a ser só concorrência (o estado mudou entre as duas
# chamadas) — que o `POST /plan` continua detectando, porque repete tudo na execução.

PLANNING_BLOCKER_WORKSPACE_ARCHIVED = "workspace_archived"
PLANNING_BLOCKER_NOT_GIT_REPO = "workspace_not_git_repo"
PLANNING_BLOCKER_WITHOUT_HEAD = "repository_without_head"
PLANNING_BLOCKER_GIT_UNVERIFIABLE = "git_unverifiable"
PLANNING_BLOCKER_INVALID_TEST_CONFIG = "invalid_test_config"

#: A ordem é a da execução real de `plan()`: arquivamento antes da transição, git no
#: primeiro passo do Planner, `test_config` ao montar o fingerprint.
PLANNING_BLOCKERS: tuple[str, ...] = (
    PLANNING_BLOCKER_WORKSPACE_ARCHIVED,
    PLANNING_BLOCKER_NOT_GIT_REPO,
    PLANNING_BLOCKER_WITHOUT_HEAD,
    PLANNING_BLOCKER_GIT_UNVERIFIABLE,
    PLANNING_BLOCKER_INVALID_TEST_CONFIG,
)

_GIT_BLOCKERS: dict[str, str] = {
    PROBE_NOT_A_REPO: PLANNING_BLOCKER_NOT_GIT_REPO,
    PROBE_WITHOUT_HEAD: PLANNING_BLOCKER_WITHOUT_HEAD,
    PROBE_UNVERIFIABLE: PLANNING_BLOCKER_GIT_UNVERIFIABLE,
}


def workspace_planning_blocker(workspace: DevWorkspace) -> str | None:
    """Workspace arquivado não aceita planejamento ([02] §1)."""
    if workspace.status is WorkspaceStatus.ARCHIVED:
        return PLANNING_BLOCKER_WORKSPACE_ARCHIVED
    return None


def classify_git(probe: HeadProbe) -> str | None:
    """O bloqueio que um `HeadProbe` impõe ao planejamento, ou `None` se há `HEAD`."""
    if probe.state == PROBE_OK:
        return None
    return _GIT_BLOCKERS.get(probe.state, PLANNING_BLOCKER_GIT_UNVERIFIABLE)


def git_planning_blocker(workspace: DevWorkspace) -> str | None:
    """A mesma sondagem e a mesma classificação de `_resolve_base_commit`, sem o branch.

    Só leitura: `probe_head` executa `rev-parse` duas vezes e nada mais.
    """
    return classify_git(probe_head(workspace.local_path, resolve_branch=False))


def invalid_test_config_blocker(workspace: DevWorkspace) -> str | None:
    """`test_config` malformado recusa o plano ao montar o fingerprint (`_read_test_policy`)."""
    try:
        _read_test_policy(workspace)
    except InvalidTestConfig:
        return PLANNING_BLOCKER_INVALID_TEST_CONFIG
    return None


def planning_blocker_error(workspace: DevWorkspace, blocker: str) -> WorkspaceNotPlannable:
    """O erro que `POST /plan` levanta para um bloqueio de workspace ou git.

    As mensagens descrevem a situação; nenhuma expõe `local_path`, argumentos ou `stderr`
    do git ([04] §5). O slug vai em `reason`.
    """
    messages = {
        PLANNING_BLOCKER_WORKSPACE_ARCHIVED: (
            f"o workspace '{workspace.name}' está arquivado ([02] §1)"
        ),
        PLANNING_BLOCKER_NOT_GIT_REPO: (
            f"o workspace '{workspace.name}' não é um repositório git: ele serve para "
            "contexto, mas planejar exige um `base_commit` para congelar ([04] §8). O "
            "backend nunca executa `git init`"
        ),
        PLANNING_BLOCKER_WITHOUT_HEAD: (
            f"o repositório de '{workspace.name}' não tem nenhum commit (*unborn branch*): "
            "não há `HEAD` para congelar como `planning_base_commit` ([02] §6)"
        ),
        PLANNING_BLOCKER_GIT_UNVERIFIABLE: (
            f"não foi possível consultar o git do workspace '{workspace.name}' agora; sem "
            "essa leitura não há `base_commit` para congelar ([02] §6)"
        ),
    }
    return WorkspaceNotPlannable(messages[blocker], reason=blocker)


def _resolve_base_commit(workspace: DevWorkspace) -> tuple[str, str | None]:
    """Congela `(planning_base_commit, base_branch)`. **Uma leitura, no início.** ([02] §6)

    Levanta `WorkspaceNotPlannable` quando não há o que congelar. Os casos de [04] §8 e de
    [07] ("Projeto criado do zero") saem com mensagens e `reason` distintos porque a ação do
    usuário é distinta: sem git, ele roda `git init` **fora do backend**; com git e sem
    `HEAD`, ele faz o primeiro commit; com o git inacessível, tenta de novo. O backend nunca
    faz nenhum dos três.
    """
    probe = probe_head(workspace.local_path)
    blocker = classify_git(probe)
    if blocker is not None:
        raise planning_blocker_error(workspace, blocker)

    assert probe.head is not None  # `classify_git` só devolve `None` com `state == ok`
    return probe.head, probe.branch


def _build_plan(
    task: WorkspaceTask,
    analysis: TaskAnalysis,
    decision: ResourceDecision,
    *,
    base_commit: str,
    base_branch: str | None,
) -> dict[str, Any]:
    """O documento `plan` de [02] §3. **Puro** — sem timestamp, sem id, sem caminho local.

    O que está aqui é o que o humano lê e aprova, e é exatamente o que o `plan_hash` cobre.
    `worktree_path` e `local_path` **não** entram: são caminhos que variam entre máquinas,
    que [02] §7 proíbe num hash estável.
    """
    return {
        "v": PLAN_VERSION,
        "goal": task.goal,
        "title": task.title,
        "base_commit": base_commit,
        "base_branch": base_branch,
        "analysis": analysis.as_canonical(),
        "agents": list(decision.agents),
        "acceptance_criteria": list(analysis.acceptance_criteria),
        "execution_limits": decision.as_execution_limits(),
    }


def stale_selected_entry_ids(selection: ContextSelection) -> tuple[str, ...]:
    """Entradas **selecionadas** cujo estado não é `fresh`, para a guarda de aprovação.

    `stale` **e** `unknown` contam. [02] §4 escreve "nenhuma entrada `stale` selecionada se
    `risk = high`", e `unknown` é o estado de [03] §3 para "não deu para verificar" — que,
    sob `risk = high`, merece o mesmo tratamento que "verificamos e mudou". Deixar `unknown`
    passar seria tratar ignorância como frescor, que é o falso `fresh` que AUD-004 fechou.
    """
    return tuple(
        entry.entry_id for entry in selection.selected if entry.state != ContextState.FRESH.value
    )


def reverify_context(
    session: Session, workspace: DevWorkspace, *, base_commit: str
) -> dict[str, str]:
    """Reverifica o registry contra um commit **já congelado** e devolve `entry_id → state`.

    Um só ponto de entrada para os dois chamadores que precisam disso — o `plan`
    (E6-AUD-005) e a guarda `needs_fix → approved` (E6-AUD-010) —, e sempre pela API
    canônica da E4. Nenhum deles lê `HEAD`: o commit vem de fora, congelado, exatamente
    como [02] §6 exige.

    `verify_workspace_entries` escreve `state`/`stale_reason`/`last_verified_*` nas linhas
    do registry e **nada mais** (E4-AUD-010), então chamar isto não "cura" uma entrada
    `stale` nem mexe no baseline: só atualiza o veredito.
    """
    _snapshot, results = verify_workspace_entries(
        session,
        workspace,
        verification_commit=base_commit,
        policy=resolve_effective_policy(workspace),
    )
    return {entry.id: outcome.state.value for entry, outcome in results}


def stale_manifest_entry_ids(manifest: ContextManifest, states: dict[str, str]) -> tuple[str, ...]:
    """Das entradas **deste manifest**, quais não estão `fresh` segundo `states`.

    O cruzamento é necessário porque `reverify_context` verifica o workspace inteiro: uma
    entrada `stale` que a seleção nunca escolheu não é motivo para recusar a aprovação —
    [02] §4 fala de "entrada `stale` **selecionada**".

    Uma entrada do manifest que sumiu do registry entra na lista: ela foi apagada depois do
    congelamento, e não conseguir afirmar que continua `fresh` é o mesmo desfecho de
    `unknown`, que `stale_selected_entry_ids` já trata como não-`fresh`.
    """
    return tuple(
        entry_id
        for entry_id in (str(item["entry_id"]) for item in manifest.entries)
        if states.get(entry_id, ContextState.UNKNOWN.value) != ContextState.FRESH.value
    )


def _read_test_policy(workspace: DevWorkspace) -> TestPolicy | None:
    """Lê `DevWorkspace.test_config` e valida de novo, na leitura.

    `set_test_config` já validou na escrita, então revalidar parece redundante — e não é. A
    coluna é JSON livre no banco: uma linha gravada por uma versão anterior do schema, ou
    editada por fora, chegaria aqui malformada. Sem esta checagem, um documento incompleto
    viraria um `test_binding` com campos faltando, que entraria no `execution_fingerprint` e
    seria **aprovado** — o fingerprint afirmaria uma política de teste que não existe.

    É a mesma razão pela qual `render_context` revalida o sha256 dos bytes em disco antes de
    reaproveitar o artefato (E5-AUD-009): "já foi gravado correto" não é o mesmo que "está
    correto agora".
    """
    try:
        return parse_test_policy(workspace.test_config)
    except InvalidTestPolicy as error:
        raise InvalidTestConfig(
            f"`test_config` do workspace '{workspace.name}' está malformado e não pode "
            f"virar um `test_binding`: {error}"
        ) from error


def build_fingerprint_parts(
    task: WorkspaceTask,
    workspace: DevWorkspace,
    manifest: ContextManifest,
    *,
    plan_hash: str,
    agents: tuple[str, ...],
    decision: ResourceDecision,
) -> FingerprintParts:
    """Monta os componentes de [02] §7. **Determinística sobre o estado atual do banco.**

    Chamada duas vezes com propósitos diferentes e **exatamente o mesmo código**: no plano,
    para produzir o candidato; no `approve` e na guarda de entrada, para recalcular. Um
    segundo caminho de montagem seria um jeito de os dois discordarem, e um fingerprint que
    discorda de si mesmo invalida aprovações válidas — ou, pior, valida inválidas.

    O que é **lido ao vivo** aqui é o que precisa invalidar a aprovação quando muda:
    `test_config` do workspace, política de segurança composta, política de workflow e
    limites. O que vem congelado é o que o plano fixou: `plan_hash`, o manifest, o
    `base_commit` e `agents`.
    """
    test_policy = _read_test_policy(workspace)

    workflow_policy = WorkflowPolicy(
        max_fix_rounds=decision.max_fix_rounds,
        max_attempts=decision.max_attempts,
    )

    return FingerprintParts(
        plan_hash=plan_hash,
        manifest_hash=manifest.manifest_hash,
        rendered_context_hash=manifest.rendered_context_hash,
        base_commit=manifest.git_head,
        # Ambos `null` até E8/E9 — `null` explícito, nunca chave omitida ([02] §7).
        developer_binding=NULL_BINDING,
        auditor_binding=NULL_BINDING,
        test_binding=test_policy.as_binding() if test_policy else dict(NULL_TEST_BINDING),
        agents=agents,
        tool_profile_hash=tool_profile_hash(),
        safety_policy_hash=effective_policy_hash(workspace),
        workflow_policy_hash=workflow_policy.policy_hash(),
        execution_limits=decision.as_execution_limits(),
    )


def decision_from_task(task: WorkspaceTask) -> ResourceDecision:
    """Reconstrói a `ResourceDecision` de uma task já planejada, para o recálculo.

    `agents` vem da **task**: foi a decisão do Router naquele planejamento, e replanejar é o
    que a atualiza.

    Os limites vêm das **constantes vigentes** de [03] §6, não do `plan` persistido, e a
    assimetria é o ponto: se alguém mudar `MAX_ATTEMPTS` ou um orçamento, o recálculo
    precisa ver o valor novo e **invalidar** a aprovação ([04] §7: "limites […] invalida a
    aprovação"). Ler o valor congelado no plano faria uma mudança de limite passar
    despercebida — o contrário do que o fingerprint existe para fazer.
    """
    return ResourceDecision(
        agents=tuple(task.agents or ()),
        max_context_tokens=DEFAULT_MAX_CONTEXT_TOKENS,
        max_total_tokens=DEFAULT_MAX_TOTAL_TOKENS,
        max_attempts=MAX_ATTEMPTS,
        max_fix_rounds=MAX_FIX_ROUNDS,
        max_parallel_agents=MAX_PARALLEL_AGENTS,
        max_agents=MAX_AGENTS,
    )


@dataclass(frozen=True, slots=True)
class PlanResult:
    """O que o planejamento produziu. A task já está persistida em `awaiting_approval`."""

    task: WorkspaceTask
    manifest: ContextManifest
    analysis: TaskAnalysis
    decision: ResourceDecision
    selection: ContextSelection
    plan_hash: str
    fingerprint: str
    fingerprint_parts: FingerprintParts


def plan_task(
    session: Session,
    task: WorkspaceTask,
    workspace: DevWorkspace,
    *,
    candidate_paths: list[str],
    artifacts_dir: Path,
    enrichment_port: AnalyzerEnrichmentPort | None = None,
) -> PlanResult:
    """Planeja `task`. **Não escreve um único arquivo do usuário** ([07], gate da E6).

    O único efeito em disco é o Rendered Context Artifact, sob `data_dir/artifacts/` —
    fora do repositório, endereçado por conteúdo, gravado por `freeze_manifest`.

    A transição de estado é do chamador (`execution_manager.plan`): esta função é a
    **mecânica** do planejamento, e mantê-la livre de *compare-and-set* é o que permite
    testá-la sem montar concorrência.
    """
    base_commit, base_branch = _resolve_base_commit(workspace)

    # Uma resolução, três consumidores. A política de segredo que decide o piso de risco do
    # Analyzer (E6-AUD2-003) é a **mesma** que decide o que a seleção pode ler ([04] §5):
    # resolvê-la duas vezes abriria a porta para as duas responderem diferente sobre o
    # mesmo arquivo no mesmo planejamento.
    policy = resolve_effective_policy(workspace)

    analysis = analyze(
        task.goal,
        candidate_paths,
        enrichment_port=enrichment_port,
        secret_policy=policy.secret_policy(),
    )
    decision = route(analysis)

    # [06] §2, passo "verifica contexto" (E6-AUD-005). Contra o commit **congelado** no
    # passo 1, nunca contra um `HEAD` relido — ver o docstring do módulo.
    reverify_context(session, workspace, base_commit=base_commit)

    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=list(analysis.candidate_paths),
        max_context_tokens=decision.max_context_tokens,
        affected_domains=analysis.affected_domains,
        objective_terms=analysis.objective_terms,
        policy=policy,
    )

    # `freeze_manifest` grava o artefato por dentro. Nenhuma chamada a `render_context`
    # aqui — ver o docstring do módulo.
    manifest = freeze_manifest(
        session,
        task,
        selection,
        base_branch=base_branch,
        artifacts_dir=artifacts_dir,
    )

    plan = _build_plan(task, analysis, decision, base_commit=base_commit, base_branch=base_branch)
    plan_hash = canonical_sha256(plan)

    parts = build_fingerprint_parts(
        task,
        workspace,
        manifest,
        plan_hash=plan_hash,
        agents=decision.agents,
        decision=decision,
    )

    task.risk = analysis.risk
    task.complexity = analysis.complexity
    task.risk_source = analysis.risk_source
    task.agents = list(decision.agents)
    task.plan = plan
    task.plan_hash = plan_hash
    task.planning_base_commit = base_commit
    task.base_commit = base_commit

    # O **candidato** a fingerprint, não a aprovação. `approved_at` continua `NULL`, e é
    # ele — e só ele — que significa "aprovada" ([02] §3: "`approved_at` não nulo exige
    # `plan_hash`, `approved_manifest_id` e `approved_fingerprint` não nulos"; a recíproca
    # não vale). Persistir o candidato aqui é o que permite à UI mostrar o fingerprint
    # antes de aprovar e devolvê-lo no `approve`.
    task.approved_manifest_id = manifest.id
    task.approved_fingerprint = parts.compute()
    task.approved_fingerprint_parts = parts.as_canonical()
    task.approved_at = None

    session.flush()

    return PlanResult(
        task=task,
        manifest=manifest,
        analysis=analysis,
        decision=decision,
        selection=selection,
        plan_hash=plan_hash,
        fingerprint=task.approved_fingerprint,
        fingerprint_parts=parts,
    )


__all__ = [
    "PLANNING_BLOCKERS",
    "PLANNING_BLOCKER_GIT_UNVERIFIABLE",
    "PLANNING_BLOCKER_INVALID_TEST_CONFIG",
    "PLANNING_BLOCKER_NOT_GIT_REPO",
    "PLANNING_BLOCKER_WITHOUT_HEAD",
    "PLANNING_BLOCKER_WORKSPACE_ARCHIVED",
    "PLAN_VERSION",
    "PlanResult",
    "build_fingerprint_parts",
    "classify_git",
    "decision_from_task",
    "git_planning_blocker",
    "invalid_test_config_blocker",
    "plan_task",
    "planning_blocker_error",
    "reverify_context",
    "stale_manifest_entry_ids",
    "stale_selected_entry_ids",
    "workspace_planning_blocker",
]
