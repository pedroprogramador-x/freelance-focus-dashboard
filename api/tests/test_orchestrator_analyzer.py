"""Task Analyzer ([03] §5) — hard rules, atalho e o piso de fallback.

Os três gates da sub-etapa 3 do prompt da E6:

1. porta **fake** devolvendo risco baixo → o piso de hard rule prevalece;
2. `port=None` → fallback conservador (`risk`/`complexity` ≥ `medium`, `risk_source`
   permanece `hard_rule`);
3. atalho → **zero** chamada à porta.

Nenhum adaptador real aparece aqui, e nenhum pode aparecer: [01] §2 proíbe
`orchestrator/ → adaptador concreto`, e `test_architecture.py` proíbe importar provider em
qualquer lugar do backend antes da E8.
"""

from __future__ import annotations

import pytest

from app.context_engine.source_ref_expansion import (
    ClassificationPatternError,
    build_classification_matcher,
    compile_classification_pattern,
)
from app.db.enums import ComplexityLevel, ContextDomain, RiskLevel, RiskSource
from app.orchestrator.analyzer import (
    HIGH_RISK_OBJECTIVE_RULES,
    HIGH_RISK_PATH_RULES,
    MEDIUM_RISK_PATH_RULES,
    AnalyzerEnrichment,
    analyze,
    evaluate_hard_rules,
    max_complexity,
    max_risk,
)
from app.safety.types import SafetyDecision


class _FakePort:
    """Porta de enriquecimento **fake**, nunca um adaptador real.

    Conta as chamadas: é o que transforma "o atalho não gasta token" numa afirmação
    verificável em vez de uma intenção documentada.
    """

    def __init__(self, result: AnalyzerEnrichment | None) -> None:
        self.result = result
        self.calls = 0

    def enrich(self, objective: str, candidate_paths: list[str]) -> AnalyzerEnrichment | None:
        del objective, candidate_paths
        self.calls += 1
        return self.result


class _RaisingPort:
    """Porta que explode. [03] §5: falha do provider de análise **não bloqueia**."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def enrich(self, objective: str, candidate_paths: list[str]) -> AnalyzerEnrichment | None:
        del objective, candidate_paths
        self.calls += 1
        raise self.error


# --------------------------------------------------------------- gramática compartilhada


def test_todo_padrao_de_hard_rule_compila_na_gramatica_do_dono_unico() -> None:
    """Nenhuma hard rule pode ser um padrão que a gramática de glob recusa.

    Um padrão que não compila viraria uma regra que **nunca casa nada** — *fail-open* sobre
    classificação de risco. `build_classification_matcher` levanta no import, mas este teste
    o afirma explicitamente, padrão a padrão, para que a mensagem de falha diga **qual**.
    """
    todas = [
        *[p for patterns in HIGH_RISK_PATH_RULES.values() for p in patterns],
        *[p for patterns in MEDIUM_RISK_PATH_RULES.values() for p in patterns],
    ]
    assert todas, "a tabela de hard rules não pode estar vazia"

    for pattern in todas:
        compiled = compile_classification_pattern(pattern)
        assert not isinstance(compiled, SafetyDecision), (
            f"o padrão de hard rule `{pattern}` não compila: {compiled}"
        )


def test_padroes_de_segredo_sao_aceitos_como_classificacao_e_recusados_como_source_ref() -> None:
    """**O motivo de `compile_classification_pattern` existir.**

    `.env*` é ao mesmo tempo:

    * um `source_ref` **inaceitável** — o envelope de `safety.source_refs` o nega pela
      denylist de [04] §5;
    * um padrão de hard rule **obrigatório** — [03] §5 o lista para elevar `risk` a `high`.

    Se a hard rule passasse pelo envelope, a regra que reconhece segredo seria negada por
    apontar para segredo, e um objetivo que toca `.env` deixaria de ser `high`.
    """
    from app.context_engine.source_ref_expansion import validate_and_compile

    como_source_ref = validate_and_compile([".env*"])
    assert isinstance(como_source_ref, SafetyDecision)
    assert not como_source_ref.allow
    assert como_source_ref.rule_id == "source_ref.secret_denied"

    como_classificacao = build_classification_matcher([".env*"])
    assert como_classificacao.covers(".env")
    assert como_classificacao.covers(".env.local")


def test_matcher_de_classificacao_usa_a_mesma_gramatica() -> None:
    """`**` como segmento continua sendo zero-ou-mais, igual ao expansor."""
    matcher = build_classification_matcher(["migrations/**"])

    assert matcher.covers("migrations/0001.py")
    assert matcher.covers("migrations/versions/0002.py")
    assert not matcher.covers("outro/migrations/0001.py")


def test_padrao_de_classificacao_malformado_levanta_no_lugar_de_nao_casar_nada() -> None:
    """*Fail loud*: uma constante quebrada para a suíte, não degrada em silêncio."""
    with pytest.raises(ClassificationPatternError, match="não compila"):
        build_classification_matcher(["a{b,c}"])


# --------------------------------------------------------------------------- hard rules


@pytest.mark.parametrize(
    ("path", "esperado"),
    [
        ("migrations/0001_init.py", "path:migrations_schema"),
        ("api/alembic/env.py", "path:migrations_schema"),
        ("db/schema.sql", "path:migrations_schema"),
        ("app/auth_service.py", "path:auth_permissions"),
        ("src/session_store.ts", "path:auth_permissions"),
        # A categoria de segredo delega para `safety.secrets` (E6-AUD2-003): o rótulo diz
        # de onde veio o veredito, e os cinco últimos são os que a tabela local do Analyzer
        # não tinha — `client.p12` saía `low`/`trivial` antes da delegação.
        (".env", "path:secret_policy"),
        ("config/.env.local", "path:secret_policy"),
        ("certs/server.pem", "path:secret_policy"),
        ("client.p12", "path:secret_policy"),
        ("client.pfx", "path:secret_policy"),
        ("id_rsa", "path:secret_policy"),
        ("id_ed25519", "path:secret_policy"),
        (".pypirc", "path:secret_policy"),
        (".github/workflows/ci.yml", "path:ci_cd_deploy"),
        ("Dockerfile", "path:ci_cd_deploy"),
        ("package.json", "path:dependencies"),
        ("api/pyproject.toml", "path:dependencies"),
    ],
)
def test_hard_rule_de_path_eleva_para_high(path: str, esperado: str) -> None:
    outcome = evaluate_hard_rules("mexer num arquivo", [path])

    assert outcome.risk is RiskLevel.HIGH
    assert esperado in outcome.matched


@pytest.mark.parametrize(
    "objetivo",
    [
        "fazer rebase da branch",
        "force push para main",
        "rodar reset --hard",
        "usar filter-branch",
        "rm -rf node_modules",
        "drop table clientes",
        "apagar tudo e recomeçar",
        "APAGAR TUDO",  # `_fold` normaliza caixa
    ],
)
def test_hard_rule_de_objetivo_eleva_para_high(objetivo: str) -> None:
    outcome = evaluate_hard_rules(objetivo, [])

    assert outcome.risk is RiskLevel.HIGH
    assert any(item.startswith("objective:") for item in outcome.matched)


def test_hard_rule_ignora_acento_na_comparacao_de_objetivo() -> None:
    """`_fold` remove diacrítico: a tabela não precisa de uma entrada por grafia.

    Sem isso, "apagar tudo" com acento em outra palavra da frase ainda casaria, mas um
    termo acentuado na própria tabela não — e a regra que faltasse seria *fail-open*.
    """
    com_acento = evaluate_hard_rules("corrigir erro de digitação no título", [])
    sem_acento = evaluate_hard_rules("corrigir erro de digitacao no titulo", [])

    assert com_acento.matched == sem_acento.matched


def test_cruzar_modulos_eleva_para_medium() -> None:
    outcome = evaluate_hard_rules("mexer em dois lugares", ["src/a.py", "api/b.py"])

    assert outcome.risk is RiskLevel.MEDIUM
    assert "structure:crosses_modules" in outcome.matched


def test_um_modulo_so_nao_eleva() -> None:
    outcome = evaluate_hard_rules("mexer num lugar", ["src/a.py", "src/nested/b.py"])

    assert outcome.risk is RiskLevel.LOW
    assert outcome.matched == ()


def test_arquivo_na_raiz_conta_como_modulo_proprio() -> None:
    """Mexer em `README.md` **e** em `src/app.py` cruza a fronteira raiz↔`src`."""
    outcome = evaluate_hard_rules("documentar", ["README.md", "src/app.py"])

    assert "structure:crosses_modules" in outcome.matched


def test_config_de_build_eleva_para_medium() -> None:
    outcome = evaluate_hard_rules("ajustar build", ["vite.config.ts"])

    assert outcome.risk is RiskLevel.MEDIUM
    assert "path:build_config" in outcome.matched


def test_high_vence_medium_quando_os_dois_disparam() -> None:
    outcome = evaluate_hard_rules("ajustar", ["vite.config.ts", "migrations/0001.py"])

    assert outcome.risk is RiskLevel.HIGH
    assert "path:build_config" in outcome.matched
    assert "path:migrations_schema" in outcome.matched


def test_tabela_de_objetivo_nao_esta_vazia() -> None:
    """Guarda contra alguém esvaziar a tabela e o teste acima passar à toa."""
    assert HIGH_RISK_OBJECTIVE_RULES


# --------------------------------------------------------------------------- ordenação


def test_max_risk_e_max_complexity_ordenam_corretamente() -> None:
    assert max_risk(RiskLevel.LOW, RiskLevel.HIGH) is RiskLevel.HIGH
    assert max_risk(RiskLevel.MEDIUM, RiskLevel.LOW) is RiskLevel.MEDIUM
    assert max_complexity(ComplexityLevel.TRIVIAL, ComplexityLevel.MEDIUM) is ComplexityLevel.MEDIUM
    assert max_complexity(ComplexityLevel.HIGH, ComplexityLevel.MEDIUM) is ComplexityLevel.HIGH


# ----------------------------------------------------------------------- GATE: atalho


def test_atalho_nao_chama_a_porta_de_enriquecimento() -> None:
    """**GATE**: as três condições de [03] §5 passo 2 → zero token gasto."""
    port = _FakePort(AnalyzerEnrichment(risk=RiskLevel.HIGH))

    analysis = analyze("corrigir typo no README", ["README.md"], enrichment_port=port)

    assert port.calls == 0, "o atalho chamou a porta: [03] §5 exige zero chamada"
    assert analysis.shortcut_applied is True
    assert analysis.complexity is ComplexityLevel.TRIVIAL
    assert analysis.risk is RiskLevel.LOW
    assert analysis.risk_source is RiskSource.HARD_RULE
    assert analysis.enrichment_unavailable is False


@pytest.mark.parametrize(
    ("objetivo", "paths", "motivo"),
    [
        ("corrigir typo", ["migrations/0001.py"], "hard rule elevou o risco"),
        ("corrigir typo", ["a/x.py", "b/y.py"], "mais de um arquivo candidato"),
        ("refatorar o módulo inteiro", ["README.md"], "objetivo não é padrão trivial"),
    ],
)
def test_atalho_exige_as_tres_condicoes(objetivo: str, paths: list[str], motivo: str) -> None:
    """Qualquer uma das três faltando desliga o atalho — e aí a porta **é** chamada."""
    port = _FakePort(AnalyzerEnrichment())

    analysis = analyze(objetivo, paths, enrichment_port=port)

    assert port.calls == 1, f"o atalho se aplicou apesar de: {motivo}"
    assert analysis.shortcut_applied is False


# ------------------------------------------------------- GATE: piso de hard rule vs LLM


def test_enriquecimento_nunca_reduz_o_piso_de_hard_rule() -> None:
    """**GATE**: porta fake devolvendo `low` sobre uma task que a hard rule marcou `high`.

    [03] §5: "um LLM pode elevar o risco; nunca reduzi-lo abaixo do piso de uma hard rule".
    """
    port = _FakePort(AnalyzerEnrichment(risk=RiskLevel.LOW, complexity=ComplexityLevel.TRIVIAL))

    analysis = analyze("mexer na migration", ["migrations/0001_init.py"], enrichment_port=port)

    assert port.calls == 1
    assert analysis.risk is RiskLevel.HIGH, "o enriquecimento reduziu o piso de hard rule"
    assert analysis.risk_source is RiskSource.HARD_RULE, (
        "a hard rule prevaleceu, então `risk_source` tem de registrá-la"
    )


def test_enriquecimento_pode_elevar_e_ai_risk_source_vira_llm() -> None:
    port = _FakePort(AnalyzerEnrichment(risk=RiskLevel.HIGH, complexity=ComplexityLevel.HIGH))

    analysis = analyze("refatorar o core", ["src/core.py"], enrichment_port=port)

    assert analysis.risk is RiskLevel.HIGH
    assert analysis.complexity is ComplexityLevel.HIGH
    assert analysis.risk_source is RiskSource.LLM


def test_empate_nao_e_prevalecer() -> None:
    """O enriquecimento concordar com a hard rule não o torna a fonte do risco."""
    port = _FakePort(AnalyzerEnrichment(risk=RiskLevel.HIGH))

    analysis = analyze("mexer na migration", ["migrations/0001.py"], enrichment_port=port)

    assert analysis.risk is RiskLevel.HIGH
    assert analysis.risk_source is RiskSource.HARD_RULE


def test_enriquecimento_propaga_dominios_e_termos_para_a_selecao() -> None:
    port = _FakePort(
        AnalyzerEnrichment(
            summary="resumo",
            affected_domains=(ContextDomain.MODULES, ContextDomain.STACK),
            objective_terms=("auth", "login"),
            candidate_paths=("src/extra.py",),
            acceptance_criteria=("critério A", "critério B"),
            needs_architect=True,
            needs_researcher=True,
        )
    )

    analysis = analyze("mexer", ["src/a.py"], enrichment_port=port)

    assert analysis.summary == "resumo"
    assert analysis.affected_domains == (ContextDomain.MODULES, ContextDomain.STACK)
    assert analysis.objective_terms == ("auth", "login")
    assert analysis.needs_architect is True
    assert analysis.needs_researcher is True
    # Candidatos do enriquecimento são **unidos** aos do payload, sem duplicar.
    assert analysis.candidate_paths == ("src/a.py", "src/extra.py")
    assert analysis.acceptance_criteria == ("critério A", "critério B")


def test_criterios_de_aceite_preservam_a_ordem_no_canonico() -> None:
    """Critérios têm ordem de leitura; reordená-los mudaria o documento aprovado."""
    port = _FakePort(AnalyzerEnrichment(acceptance_criteria=("zebra", "alfa")))

    canonical = analyze("mexer", ["src/a.py"], enrichment_port=port).as_canonical()

    assert canonical["acceptance_criteria"] == ["zebra", "alfa"]


# ------------------------------------------------------------- GATE: piso de fallback


def test_sem_porta_de_enriquecimento_aplica_o_piso_conservador() -> None:
    """**GATE**: `port=None` — o caso de toda a E6.

    [03] §5: "a ausência, falha ou timeout do enriquecimento (incluindo quando nenhuma
    porta de enriquecimento está implementada) também eleva o piso de risco".
    """
    analysis = analyze("refatorar alguma coisa", ["src/a.py"], enrichment_port=None)

    assert analysis.risk is RiskLevel.MEDIUM
    assert analysis.complexity is ComplexityLevel.MEDIUM
    assert analysis.risk_source is RiskSource.HARD_RULE
    assert analysis.enrichment_unavailable is True
    assert analysis.enrichment_failure == "no_enrichment_port"


def test_nunca_fica_low_so_porque_o_enriquecimento_estava_indisponivel() -> None:
    """A frase literal de [03] §5, como asserção.

    Objetivo inócuo, um arquivo inócuo, nenhuma hard rule específica — e ainda assim o
    resultado **não** é `low`.
    """
    analysis = analyze("mexer num arquivo qualquer", ["src/a.py"], enrichment_port=None)

    assert analysis.hard_rule.risk is RiskLevel.LOW, "o cenário precisa ser de hard rule `low`"
    assert analysis.risk is not RiskLevel.LOW


def test_falha_da_porta_nao_bloqueia_e_aplica_o_mesmo_piso() -> None:
    port = _RaisingPort(TimeoutError("o provider não respondeu"))

    analysis = analyze("refatorar", ["src/a.py"], enrichment_port=port)

    assert port.calls == 1
    assert analysis.risk is RiskLevel.MEDIUM
    assert analysis.risk_source is RiskSource.HARD_RULE
    assert analysis.enrichment_failure == "enrichment_error:TimeoutError"


def test_porta_devolvendo_none_e_tratada_como_indisponivel() -> None:
    port = _FakePort(None)

    analysis = analyze("refatorar", ["src/a.py"], enrichment_port=port)

    assert analysis.enrichment_unavailable is True
    assert analysis.enrichment_failure == "enrichment_returned_none"


def test_fallback_nao_reduz_uma_hard_rule_high() -> None:
    """O piso é `max`, não atribuição: `high` continua `high`."""
    analysis = analyze("mexer na migration", ["migrations/0001.py"], enrichment_port=None)

    assert analysis.risk is RiskLevel.HIGH
    assert analysis.complexity is ComplexityLevel.MEDIUM


def test_atalho_nao_passa_pelo_piso_de_fallback() -> None:
    """A **única** saída que escapa do piso, e legitimamente: é afirmação, não ausência."""
    analysis = analyze("corrigir typo", ["README.md"], enrichment_port=None)

    assert analysis.shortcut_applied is True
    assert analysis.risk is RiskLevel.LOW
    assert analysis.complexity is ComplexityLevel.TRIVIAL
    assert analysis.enrichment_unavailable is False
