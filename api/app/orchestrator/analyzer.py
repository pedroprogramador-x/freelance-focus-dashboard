"""Task Analyzer — [03](../../../docs/architecture/03-context-architecture.md) §5.

Três passos, e a ordem entre eles é a economia que [07] cobra da E6 ("gastar token no que
uma regra resolve" é o risco principal da etapa):

1. **hard rules** — determinístico, custo zero, sempre roda;
2. **atalho** — se as três condições valem, nenhum modelo é chamado;
3. **enriquecimento** — só no que sobra, e nunca decide sozinho.

## A regra que fecha o ciclo

    final_risk = max(hard_rule_risk, enrichment_risk)

**Um LLM pode elevar o risco; nunca reduzi-lo abaixo do piso de uma hard rule.** É código,
não instrução de prompt — a função `_combine` não tem parâmetro que permita o contrário, e
é isso que torna a regra verificável em vez de recomendada.

## O piso de fallback

[03] §5, parágrafo final: a ausência, falha ou timeout do enriquecimento — **incluindo
quando nenhuma porta de enriquecimento está implementada**, que é exatamente o caso da E6 —
eleva o piso:

    risk       = max(hard_rule_risk, medium)
    complexity = max(hard_rule_complexity, medium)
    risk_source = hard_rule          # permanece

`risk_source` continuar `hard_rule` não é detalhe de contabilidade. O fallback é uma
**regra determinística de degradação**, não uma segunda fonte de risco: marcá-lo como `llm`
afirmaria que um modelo opinou quando nenhum rodou, e marcá-lo como `user` afirmaria uma
decisão humana que ninguém tomou. Nunca uma task fica `risk = low` só porque o
enriquecimento estava indisponível e nenhuma hard rule específica bateu.

O atalho do passo 2 é a **única** saída que não passa por esse piso, e ela é legítima
porque é uma afirmação positiva (`hard_rule_risk = low` **e** padrão trivial catalogado
**e** ≤ 1 arquivo alvo), não uma ausência de informação.

## Sinais de segredo no **objetivo** (E6-AUD-009)

[03] §5 exige `high` quando o objetivo **ou** os candidatos tocam segredos. As tabelas
abaixo cobriam só histórico git e comandos destrutivos, e "corrigir typo em `.env`" sem
`candidate_paths` — a forma normal que a UI envia — saía `low`/`trivial` pelo atalho.

`detect_sensitive_objective_signals` fecha isso, e mora em `safety/`, não aqui: ela
reaproveita `is_sensitive_key` e `classify_path_secrecy`, e escrevê-la neste módulo
exigiria uma segunda lista de nomes sensíveis e uma segunda denylist de caminho. Ela roda
**dentro de `evaluate_hard_rules`**, e é isso que torna o atalho impossível por construção
— o passo 2 lê `hard_rule.risk`, que já veio `high`.

## As hard rules rodam **duas** vezes quando há enriquecimento (E6-AUD-011)

O enriquecimento pode **acrescentar** `candidate_paths`. Avaliar as hard rules só sobre os
candidatos anteriores a ele deixaria um `.env` sugerido pelo LLM escapar do piso: a união
acontecia depois da decisão de risco. A segunda passada roda sobre a união deduplicada, e é
o resultado dela que entra no `max`. Não há gramática nova — é a mesma
`evaluate_hard_rules`, determinística e de custo zero, chamada de novo.

## Quem decide "isto é um path de segredo" (E6-AUD2-003)

**Não é este módulo.** A tabela abaixo tinha uma categoria `"secrets"` com dezoito padrões
copiados da denylist de [04] §5 — e ela **já tinha divergido**: `*.p12`, `*.pfx`, `id_rsa*`,
`id_ed25519*` e `.pypirc` estão na denylist canônica e não estavam aqui, então
`client.p12` como candidato saía `low`/`trivial`. Exatamente o defeito que a disciplina de
dono único existe para impedir, e o irmão do que E6-AUD-009 fechou do lado do objetivo.

O piso de risco por segredo agora chama `safety.secrets.is_secret_path`, a mesma função que
decide o que o Context Engine pode ler. As duas perguntas passam a ter uma resposta só, e
acrescentar um padrão à denylist passa a elevar o risco automaticamente.

O que **fica** em `HIGH_RISK_PATH_RULES` é o que genuinamente pertence à semântica de risco
do Analyzer e não à denylist do Safety Kernel: migrations/schema, CI/CD/deploy, dependências
e auth/sessão por nome. Nenhuma dessas categorias responde "isto é um segredo" — elas
respondem "mexer aqui é caro de errar", que é outra pergunta e é desta tabela.

Note a assimetria que isso torna explícita: [04] §5 **nega leitura** de um path de segredo;
[03] §5 **eleva o risco** de uma task que o toca. Reconhecer é a mesma operação; o que se
faz com o veredito é que difere. Uma função, dois consumidores.

## Casamento de padrão

As hard rules de path que sobraram usam `build_classification_matcher`, do **dono único da
gramática de glob** (`context_engine.source_ref_expansion`). Este módulo não interpreta
`*`, `**`, `?` nem `[]`, e não pode passar a interpretar — ver o docstring de
`compile_classification_pattern` para por que o envelope de `source_ref` não se aplica
aqui e por que a função nova nasceu lá, e não aqui.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.context_engine.source_ref_expansion import SourceRefMatcher, build_classification_matcher
from app.db.enums import ComplexityLevel, ContextDomain, RiskLevel, RiskSource
from app.safety.objective_signals import detect_sensitive_objective_signals
from app.safety.secrets import SecretPolicy, is_secret_path

# --------------------------------------------------------------------------- ordenação

#: Ordem total de `RiskLevel`, para que `max` seja definido sobre o enum. Um `dict` e não
#: `IntEnum`: [02] congela `RiskLevel` como enum de string, e dar-lhe ordem numérica no
#: próprio tipo mudaria o valor persistido.
_RISK_ORDER: dict[RiskLevel, int] = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}

_COMPLEXITY_ORDER: dict[ComplexityLevel, int] = {
    ComplexityLevel.TRIVIAL: 0,
    ComplexityLevel.LOW: 1,
    ComplexityLevel.MEDIUM: 2,
    ComplexityLevel.HIGH: 3,
}


def max_risk(*values: RiskLevel) -> RiskLevel:
    """`max` sobre `RiskLevel` pela ordem `low < medium < high`."""
    return max(values, key=lambda value: _RISK_ORDER[value])


def max_complexity(*values: ComplexityLevel) -> ComplexityLevel:
    """`max` sobre `ComplexityLevel` pela ordem `trivial < low < medium < high`."""
    return max(values, key=lambda value: _COMPLEXITY_ORDER[value])


# --------------------------------------------------------------------------- hard rules

#: Tabela de [03] §5, passo 1 — **dados, não código**. Categoria → padrões de path que
#: elevam `risk` para `high`. Os padrões são casados pela gramática do
#: `source_ref_expansion`; ver o docstring do módulo.
#:
#: **Não há categoria de segredo aqui** (E6-AUD2-003). "Isto é um path de segredo" é
#: pergunta da denylist de [04] §5, respondida por `safety.secrets.is_secret_path` e
#: consultada em `evaluate_hard_rules`. Ver o docstring do módulo.
HIGH_RISK_PATH_RULES: dict[str, tuple[str, ...]] = {
    # Cada padrão de diretório aparece **duas vezes**: ancorado na raiz e sob `**/`. A
    # tabela de [03] §5 escreve `migrations/**` supondo um repositório de projeto único,
    # mas num monorepo as migrations vivem em `api/migrations/` — e um padrão ancorado na
    # raiz não as alcança. Omitir a variante `**/` faria a regra mais importante do
    # Analyzer não casar exatamente no repositório em que ele roda: *fail-open* sobre
    # classificação de risco, que é o defeito que esta tabela existe para impedir.
    "migrations_schema": (
        "migrations/**",
        "**/migrations/**",
        "alembic/**",
        "**/alembic/**",
        "**/*.sql",
        "**/models.py",
        "**/models/**",
    ),
    "auth_permissions": ("**/auth*", "**/permission*", "**/session*", "**/*token*"),
    "ci_cd_deploy": (
        ".github/workflows/**",
        "**/.github/workflows/**",
        "Dockerfile*",
        "**/Dockerfile*",
        "**/*.compose.y*ml",
    ),
    "dependencies": (
        "package.json",
        "**/package.json",
        "package-lock.json",
        "**/package-lock.json",
        "pyproject.toml",
        "**/pyproject.toml",
        "poetry.lock",
        "**/poetry.lock",
        "requirements*.txt",
        "**/requirements*.txt",
    ),
}

#: Rótulo da categoria de segredo em `HardRuleOutcome.matched`. O nome diz de onde veio o
#: veredito — a política de segredo de [04] §5 —, e não uma linha desta tabela.
SECRET_PATH_RULE_ID = "path:secret_policy"  # noqa: S105 — id de regra, não credencial

#: Tabela de [03] §5, passo 1 — categoria → termos de objetivo que elevam para `high`.
#: Casamento por **substring** sobre o objetivo dobrado (`_fold`), não por glob: o alvo é
#: texto em linguagem natural, não caminho, e a gramática de glob não se aplica a ele.
HIGH_RISK_OBJECTIVE_RULES: dict[str, tuple[str, ...]] = {
    "git_history": ("rebase", "force push", "force-push", "reset --hard", "filter-branch"),
    "destructive_commands": (
        "rm -rf",
        "drop table",
        "truncate",
        "apagar tudo",
        "deletar tudo",
        "delete all",
    ),
}

#: [03] §5: "elevam para `medium` quando o objetivo […] toca configuração de build". O
#: outro gatilho de `medium` — "o objetivo cruza mais de um módulo" — é estrutural e vive
#: em `_crosses_modules`, não numa tabela de padrões.
MEDIUM_RISK_PATH_RULES: dict[str, tuple[str, ...]] = {
    "build_config": (
        "*.config.js",
        "**/*.config.js",
        "*.config.ts",
        "**/*.config.ts",
        "tsconfig*.json",
        "**/tsconfig*.json",
        "vite.config.*",
        "**/vite.config.*",
        "Makefile",
        "**/Makefile",
        "*.toml",
        "**/*.toml",
        "*.ini",
        "**/*.ini",
    ),
}

#: Padrões triviais catalogados de [03] §5, passo 2. Substring sobre o objetivo dobrado.
#: Deliberadamente curta: um catálogo generoso transformaria o atalho — que **pula** a
#: análise — na porta de entrada padrão, e o atalho existe para o caso óbvio, não para o
#: caso comum.
TRIVIAL_OBJECTIVE_PATTERNS: tuple[str, ...] = (
    "corrigir typo",
    "corrigir erro de digitacao",
    "fix typo",
    "renomear variavel",
    "rename variable",
    "atualizar comentario",
    "update comment",
    "formatar codigo",
    "format code",
)

#: Profundidade de diretório que define "módulo" em `_crosses_modules`. O primeiro
#: segmento do caminho: `src/a.py` e `src/b/c.py` são o mesmo módulo (`src`); `src/a.py` e
#: `api/b.py` são dois.
_MODULE_DEPTH = 1


def _fold(value: str) -> str:
    """Forma comparável: NFD → remove acentos → NFC → *case-folded*.

    A remoção de acento é o que faz `"apagar tudo"` casar um objetivo escrito
    `"apagar tudo"` ou `"APAGAR TUDO"`, e faz `"corrigir erro de digitacao"` casar
    `"corrigir erro de digitação"`. Sem ela a tabela precisaria de uma entrada por grafia,
    e a que faltasse seria um *fail-open* silencioso sobre classificação de risco.

    `casefold()` e não `lower()`: é a operação definida pelo Unicode para comparação
    insensível a caixa. Mesma escolha de `context_engine.selection._fold`.
    """
    decomposed = unicodedata.normalize("NFD", value)
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return unicodedata.normalize("NFC", without_marks).strip().casefold()


class _CompiledRules:
    """Os matchers compilados uma vez, no import. Ver `build_classification_matcher`.

    Compilar no import é o que faz um padrão malformado quebrar a suíte inteira em vez de
    virar uma hard rule que silenciosamente não casa nada.
    """

    def __init__(self) -> None:
        self.high_paths: dict[str, SourceRefMatcher] = {
            category: build_classification_matcher(patterns)
            for category, patterns in HIGH_RISK_PATH_RULES.items()
        }
        self.medium_paths: dict[str, SourceRefMatcher] = {
            category: build_classification_matcher(patterns)
            for category, patterns in MEDIUM_RISK_PATH_RULES.items()
        }
        self.high_objective: dict[str, tuple[str, ...]] = {
            category: tuple(_fold(term) for term in terms)
            for category, terms in HIGH_RISK_OBJECTIVE_RULES.items()
        }
        self.trivial_objective: tuple[str, ...] = tuple(
            _fold(pattern) for pattern in TRIVIAL_OBJECTIVE_PATTERNS
        )


_RULES = _CompiledRules()


# --------------------------------------------------------------------------- estruturas


@dataclass(frozen=True, slots=True)
class HardRuleOutcome:
    """O que o passo 1 concluiu, com as regras que dispararam nomeadas.

    ``matched`` é o diagnóstico que a UI de [06] §4 mostra ao explicar por que a task é
    `high` — "porque toca `migrations/**`" é acionável; "porque sim" não é.
    """

    risk: RiskLevel
    complexity: ComplexityLevel
    matched: tuple[str, ...] = ()

    def as_canonical(self) -> dict[str, Any]:
        return {
            "risk": self.risk.value,
            "complexity": self.complexity.value,
            "matched": list(self.matched),
        }


@dataclass(frozen=True, slots=True)
class AnalyzerEnrichment:
    """O que uma porta de enriquecimento devolve. **Nenhum campo é autoritativo sozinho.**

    ``risk`` e ``complexity`` são *propostas*: `analyze` as combina com o piso de hard rule
    por `max`, nunca as adota. ``affected_domains`` e ``objective_terms`` alimentam o
    scoring de `select_context` ([03] §4) e não influenciam risco nenhum.

    ``needs_architect``/``needs_researcher`` são os sinais que o Resource Router consome
    ([03] §6: "+ architect se sinalizado").
    """

    risk: RiskLevel | None = None
    complexity: ComplexityLevel | None = None
    summary: str | None = None
    affected_domains: tuple[ContextDomain, ...] = ()
    objective_terms: tuple[str, ...] = ()
    candidate_paths: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    needs_architect: bool = False
    needs_researcher: bool = False


class AnalyzerEnrichmentPort(Protocol):
    """A porta do passo 3. **Nenhuma implementação existe na E6** — e isso é o desenho.

    [01] §2: `orchestrator/` "recebe por injeção — não os procura". Um adaptador concreto
    aqui violaria a neutralidade de provider, e nenhum provider existe antes da E8.

    O contrato de falha é do **chamador**: `analyze` trata qualquer exceção, timeout ou
    `None` como "enriquecimento indisponível" e aplica o piso de fallback. Uma
    implementação não precisa — e não deve — traduzir os próprios erros em algo que o
    Analyzer entenda.
    """

    def enrich(
        self, objective: str, candidate_paths: list[str]
    ) -> AnalyzerEnrichment | None:  # pragma: no cover — Protocol
        ...


@dataclass(frozen=True, slots=True)
class TaskAnalysis:
    """A saída do Analyzer, inteira. É o que o Planner persiste e o Router consome."""

    risk: RiskLevel
    complexity: ComplexityLevel
    risk_source: RiskSource
    hard_rule: HardRuleOutcome
    #: `True` quando o passo 2 decidiu e **nenhuma** chamada ao enriquecimento aconteceu.
    shortcut_applied: bool
    #: `True` quando o piso de fallback de [03] §5 foi aplicado — porta ausente, exceção
    #: ou `None`. A UI mostra isto: uma task `medium` por fallback não é a mesma coisa que
    #: uma task `medium` por análise, ainda que o valor persistido seja o mesmo.
    enrichment_unavailable: bool
    #: Por que o enriquecimento não valeu. `None` quando ele valeu ou quando o atalho
    #: dispensou a chamada.
    enrichment_failure: str | None = None
    summary: str | None = None
    affected_domains: tuple[ContextDomain, ...] = ()
    objective_terms: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    needs_architect: bool = False
    needs_researcher: bool = False
    candidate_paths: tuple[str, ...] = field(default=())

    def as_canonical(self) -> dict[str, Any]:
        """Forma canônica para o `plan` e para o `plan_hash`.

        `objective_terms` e `affected_domains` saem **ordenados**: são conjuntos, e [02] §7
        manda ordenar listas sem semântica de ordem. `acceptance_criteria` **não** sai
        ordenado — critérios têm ordem de leitura, e reordená-los mudaria o documento que o
        humano aprovou.
        """
        return {
            "risk": self.risk.value,
            "complexity": self.complexity.value,
            "risk_source": self.risk_source.value,
            "hard_rule": self.hard_rule.as_canonical(),
            "shortcut_applied": self.shortcut_applied,
            "enrichment_unavailable": self.enrichment_unavailable,
            "enrichment_failure": self.enrichment_failure,
            "summary": self.summary,
            "affected_domains": sorted(domain.value for domain in self.affected_domains),
            "objective_terms": sorted(self.objective_terms),
            "acceptance_criteria": list(self.acceptance_criteria),
            "needs_architect": self.needs_architect,
            "needs_researcher": self.needs_researcher,
        }


# --------------------------------------------------------------------------- passo 1


def _crosses_modules(candidate_paths: Sequence[str]) -> bool:
    """[03] §5: "o objetivo cruza mais de um módulo". Módulo = primeiro segmento do path.

    Um arquivo na raiz (`README.md`, sem `/`) conta como o seu próprio módulo: mexer em
    `README.md` e em `src/app.py` cruza a fronteira entre a raiz e `src`, e tratar a raiz
    como "sem módulo" faria esse par não contar.
    """
    modules = {"/".join(path.split("/")[:_MODULE_DEPTH]) for path in candidate_paths}
    return len(modules) > 1


def evaluate_hard_rules(
    objective: str,
    candidate_paths: Sequence[str],
    *,
    secret_policy: SecretPolicy | None = None,
) -> HardRuleOutcome:
    """Passo 1 de [03] §5. **Determinístico, custo zero, sem rede.**

    A complexidade que as hard rules concluem sozinhas é `low`: elas sabem reconhecer
    *perigo*, não *tamanho*. Estimar tamanho é trabalho do enriquecimento, e quando ele não
    está disponível o piso de fallback assume `medium` — que é justamente a diferença entre
    "medi e é pequeno" e "não medi".
    """
    folded_objective = _fold(objective)
    matched: list[str] = []
    risk = RiskLevel.LOW

    # E6-AUD2-003: o piso por segredo vem da política canônica, não de uma tabela local.
    # `secret_policy` é a **efetiva** do workspace quando o Planner a passa — um override
    # que acrescenta padrões aperta a denylist e eleva o risco pelo mesmo caminho ([04] §5).
    if any(is_secret_path(path, secret_policy) for path in candidate_paths):
        matched.append(SECRET_PATH_RULE_ID)
        risk = RiskLevel.HIGH

    for category, matcher in _RULES.high_paths.items():
        if any(matcher.covers(path) for path in candidate_paths):
            matched.append(f"path:{category}")
            risk = RiskLevel.HIGH

    for category, terms in _RULES.high_objective.items():
        if any(term in folded_objective for term in terms):
            matched.append(f"objective:{category}")
            risk = RiskLevel.HIGH

    # E6-AUD-009. Sobre o objetivo **cru**, não o dobrado: `_fold` remove acento e caixa,
    # mas o reconhecimento de caminho depende do texto como escrito (`.env.Local` é o mesmo
    # arquivo que `.env.local` porque `classify_path_secrecy` normaliza por conta própria).
    # Só o `rule_id` e as categorias entram em `matched` — nunca o token que disparou, que
    # iria parar no `plan`, no `plan_hash` e na resposta HTTP.
    signal = detect_sensitive_objective_signals(objective, policy=secret_policy)
    if signal is not None:
        matched.append(f"objective:{signal.rule_id}")
        matched.extend(f"objective_signal:{category}" for category in signal.categories)
        risk = RiskLevel.HIGH

    for category, matcher in _RULES.medium_paths.items():
        if any(matcher.covers(path) for path in candidate_paths):
            matched.append(f"path:{category}")
            risk = max_risk(risk, RiskLevel.MEDIUM)

    if _crosses_modules(candidate_paths):
        matched.append("structure:crosses_modules")
        risk = max_risk(risk, RiskLevel.MEDIUM)

    return HardRuleOutcome(
        risk=risk, complexity=ComplexityLevel.LOW, matched=tuple(sorted(matched))
    )


# --------------------------------------------------------------------------- passo 2


def _shortcut_applies(
    objective: str, candidate_paths: Sequence[str], hard_rule: HardRuleOutcome
) -> bool:
    """As **três** condições de [03] §5, passo 2. Todas, ou nenhuma.

    "risco de hard rule `low`" é conferido sobre o resultado do passo 1, não sobre uma
    releitura das tabelas: se uma regra de `medium` disparou por cruzar módulos, o atalho
    não se aplica, mesmo que nada de `high` tenha batido.
    """
    folded = _fold(objective)
    return (
        hard_rule.risk is RiskLevel.LOW
        and any(pattern in folded for pattern in _RULES.trivial_objective)
        and len(candidate_paths) <= 1
    )


# --------------------------------------------------------------------------- passo 3


def _call_enrichment(
    port: AnalyzerEnrichmentPort | None,
    objective: str,
    candidate_paths: Sequence[str],
) -> tuple[AnalyzerEnrichment | None, str | None]:
    """Chama a porta e traduz **qualquer** desfecho ruim em `(None, motivo)`.

    O `except Exception` largo é deliberado e é o contrato documentado em
    `AnalyzerEnrichmentPort`: um adaptador de provider pode levantar qualquer coisa —
    timeout de socket, erro de parsing, `MemoryError` de uma resposta gigante — e nenhuma
    dessas é distinguível do ponto de vista da decisão que o Analyzer precisa tomar. Todas
    significam "não tenho análise", e todas levam ao mesmo piso conservador.

    Deixar a exceção propagar faria uma falha de provider virar `500` no `POST /plan`, o
    que é o oposto de [03] §5: "falha ou timeout do provider de análise **não bloqueia**".
    """
    if port is None:
        return None, "no_enrichment_port"

    try:
        enrichment = port.enrich(objective, list(candidate_paths))
    except Exception as exc:  # ver docstring: todo desfecho ruim é o mesmo desfecho
        return None, f"enrichment_error:{type(exc).__name__}"

    if enrichment is None:
        return None, "enrichment_returned_none"

    return enrichment, None


# --------------------------------------------------------------------------- composição


def analyze(
    objective: str,
    candidate_paths: Iterable[str],
    *,
    enrichment_port: AnalyzerEnrichmentPort | None = None,
    secret_policy: SecretPolicy | None = None,
) -> TaskAnalysis:
    """O Analyzer inteiro: hard rules → atalho → enriquecimento → combinação.

    ``enrichment_port`` é `None` em toda a E6 — nenhuma porta existe ainda, e é esse o
    caminho que o piso de fallback de [03] §5 cobre. O parâmetro está aqui para que E8
    injete um adaptador sem tocar nesta função.

    **Nunca levanta** por causa do enriquecimento. As únicas exceções que saem daqui são de
    programação (um padrão de hard rule malformado, que já teria quebrado no import).
    """
    paths = tuple(candidate_paths)
    hard_rule = evaluate_hard_rules(objective, paths, secret_policy=secret_policy)

    if _shortcut_applies(objective, paths, hard_rule):
        # [03] §5: "Resultado: `complexity = trivial`, `agents = [developer]`, zero token
        # gasto em classificação". `agents` é decidido pelo Resource Router, não aqui — o
        # que este ramo garante é o "zero token": o `port` não é tocado.
        return TaskAnalysis(
            risk=hard_rule.risk,
            complexity=ComplexityLevel.TRIVIAL,
            risk_source=RiskSource.HARD_RULE,
            hard_rule=hard_rule,
            shortcut_applied=True,
            enrichment_unavailable=False,
            candidate_paths=paths,
        )

    enrichment, failure = _call_enrichment(enrichment_port, objective, paths)

    if enrichment is None:
        # O piso de [03] §5. `risk_source` permanece `hard_rule`: é regra determinística de
        # fallback, não uma segunda fonte de risco.
        return TaskAnalysis(
            risk=max_risk(hard_rule.risk, RiskLevel.MEDIUM),
            complexity=max_complexity(hard_rule.complexity, ComplexityLevel.MEDIUM),
            risk_source=RiskSource.HARD_RULE,
            hard_rule=hard_rule,
            shortcut_applied=False,
            enrichment_unavailable=True,
            enrichment_failure=failure,
            candidate_paths=paths,
        )

    # E6-AUD-011: a união dos candidatos **primeiro**, e as hard rules de novo sobre ela.
    # `dict.fromkeys` preserva a ordem e deduplica; a ordem importa porque `candidate_paths`
    # viaja para `select_context` e de lá para o `manifest_hash`.
    merged_paths = tuple(dict.fromkeys([*paths, *enrichment.candidate_paths]))
    final_hard_rule = (
        hard_rule
        if merged_paths == paths
        else evaluate_hard_rules(objective, merged_paths, secret_policy=secret_policy)
    )

    # `max`, sempre. Não existe ramo em que o valor do enriquecimento substitua o piso —
    # e o piso agora é o da **segunda** passada, que enxergou os candidatos que o próprio
    # enriquecimento acrescentou.
    final_risk = max_risk(final_hard_rule.risk, enrichment.risk or RiskLevel.LOW)
    final_complexity = max_complexity(
        final_hard_rule.complexity, enrichment.complexity or ComplexityLevel.LOW
    )

    # [03] §5: "`risk_source` registra qual prevaleceu". `llm` só quando o enriquecimento
    # **elevou** — empatar com a hard rule não é prevalecer sobre ela. A comparação é contra
    # a segunda passada: um `.env` que o enriquecimento sugeriu eleva por **hard rule**, não
    # por opinião de modelo, e `risk_source` tem de dizer isso.
    prevailed_by_enrichment = (
        enrichment.risk is not None
        and _RISK_ORDER[enrichment.risk] > _RISK_ORDER[final_hard_rule.risk]
    )

    return TaskAnalysis(
        risk=final_risk,
        complexity=final_complexity,
        risk_source=RiskSource.LLM if prevailed_by_enrichment else RiskSource.HARD_RULE,
        hard_rule=final_hard_rule,
        shortcut_applied=False,
        enrichment_unavailable=False,
        summary=enrichment.summary,
        affected_domains=tuple(enrichment.affected_domains),
        objective_terms=tuple(enrichment.objective_terms),
        acceptance_criteria=tuple(enrichment.acceptance_criteria),
        needs_architect=enrichment.needs_architect,
        needs_researcher=enrichment.needs_researcher,
        candidate_paths=merged_paths,
    )


__all__ = [
    "HIGH_RISK_OBJECTIVE_RULES",
    "HIGH_RISK_PATH_RULES",
    "MEDIUM_RISK_PATH_RULES",
    "SECRET_PATH_RULE_ID",
    "TRIVIAL_OBJECTIVE_PATTERNS",
    "AnalyzerEnrichment",
    "AnalyzerEnrichmentPort",
    "HardRuleOutcome",
    "TaskAnalysis",
    "analyze",
    "evaluate_hard_rules",
    "max_complexity",
    "max_risk",
]
