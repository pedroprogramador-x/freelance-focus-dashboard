"""Gate 2 da E4 — `validate_source_ref(..., allow_glob_syntax=True)`.

O que este arquivo tem de provar, e nada além:

1. **O envelope de segurança não afrouxou.** Com `allow_glob_syntax=True`, cada uma das
   travas do módulo continua negando — inclusive quando o valor perigoso vem *acompanhado*
   de metacaracteres, que é a forma pela qual um atacante tentaria "esconder" a travessia
   dentro de um padrão (`../**`, `//srv/**`, `C:/**`, `~/**`, `a/..%/*`…).
2. **O único efeito da flag é pular `find_glob_metacharacters`.** Provado por diferença:
   para todo valor recusado com `False`, ou ele passa a ser aceito com `True` **e** o
   motivo da recusa era exatamente `source_ref.glob_not_supported`, ou ele continua
   recusado **com o mesmo `rule_id`**. Não há terceiro caso.
3. **`False` é idêntico ao comportamento da E2.** A suíte da E2 sobre este módulo
   (`test_source_refs.py`, `test_security_regressions.py`) roda sem alteração nenhuma; aqui
   a equivalência é reafirmada explicitamente pelo item 2.

Este arquivo **não** testa o que um glob casa — isso não é assunto deste módulo. A
gramática é de `app.context_engine.source_ref_expansion`, e os testes dela estão em
`test_source_ref_expansion.py`.
"""

from __future__ import annotations

import re

import pytest

from app.safety.source_refs import (
    find_glob_metacharacters,
    normalize_source_ref,
    validate_source_ref,
)

#: Valores perigosos **com metacaractere junto**. Cada um continua tendo de ser recusado
#: com `allow_glob_syntax=True`, e pelo motivo estrutural correto — não por conter `*`.
DANGEROUS_WITH_GLOB: tuple[tuple[str, str], ...] = (
    ("../**", "source_ref.parent_traversal"),
    ("../*.py", "source_ref.parent_traversal"),
    ("src/../../**", "source_ref.parent_traversal"),
    ("src/**/../*", "source_ref.parent_traversal"),
    ("//srv/**", "source_ref.unc"),
    ("//srv/share/*.key", "source_ref.unc"),
    ("\\\\srv\\**", "source_ref.unc"),
    ("\\\\?\\C:\\**", "source_ref.device_namespace"),
    ("\\\\.\\PhysicalDrive0\\*", "source_ref.device_namespace"),
    ("/etc/**", "source_ref.root_relative"),
    ("\\windows\\**", "source_ref.root_relative"),
    ("C:/Users/**", "source_ref.absolute_not_allowed"),
    ("C:temp/*", "source_ref.drive_relative"),
    ("~/**", "source_ref.home_reference"),
    ("~/.ssh/*", "source_ref.home_reference"),
    ("src/CON/*", "source_ref.reserved_name"),
    ("src/nul.*", "source_ref.reserved_name"),
    ("src/arquivo.txt:stream/*", "source_ref.alternate_data_stream"),
    ("PROGRA~1/**", "source_ref.short_name_alias"),
    ("src/pasta./**", "source_ref.trailing_dot_or_space"),
)


@pytest.mark.parametrize(("value", "rule_id"), DANGEROUS_WITH_GLOB, ids=lambda item: str(item))
def test_glob_liberado_nao_reabre_nenhuma_porta(value: str, rule_id: str) -> None:
    """`allow_glob_syntax=True` **não** é uma porta lateral para sintaxe perigosa.

    Cada caso aqui é um valor que só seria recusado por `glob_not_supported` se a checagem
    de metacaractere viesse primeiro. Ela não vem: `prevalidate_path_syntax` roda antes, e
    o `rule_id` gravado é o do problema mais grave, não o mais recente.
    """
    result = validate_source_ref(value, allow_glob_syntax=True)

    assert result.decision.allow is False, value
    assert result.normalized is None
    assert result.decision.rule_id == rule_id, value
    assert result.decision.rule_id != "source_ref.glob_not_supported"


@pytest.mark.parametrize(("value", "rule_id"), DANGEROUS_WITH_GLOB, ids=lambda item: str(item))
def test_o_mesmo_valor_e_recusado_pelo_mesmo_motivo_com_a_flag_desligada(
    value: str, rule_id: str
) -> None:
    """A flag não muda **o motivo** de nada que já era recusado estruturalmente."""
    del rule_id
    desligada = validate_source_ref(value, allow_glob_syntax=False)
    ligada = validate_source_ref(value, allow_glob_syntax=True)

    assert desligada.decision.allow is False
    assert desligada.decision.rule_id == ligada.decision.rule_id


#: Padrões seguros: metacaractere é a **única** razão pela qual a E2 os recusaria.
SAFE_GLOBS: tuple[str, ...] = (
    "src/*.py",
    "src/**",
    "src/**/*.py",
    "src/**/nested/*.ts",
    "docs/0?-*.md",
    "src/[abc]/mod.py",
    "src/[!x]/mod.py",
    "app/{a,b}/x.py",
    "!src/ignorado.py",
    "src/arquivo?.py",
)


@pytest.mark.parametrize("value", SAFE_GLOBS)
def test_padrao_seguro_e_recusado_sem_a_flag_e_aceito_com_ela(value: str) -> None:
    """A diferença exata entre `False` e `True`, num valor cuja única falta é o glob."""
    assert find_glob_metacharacters(value), "o caso de teste precisa ter metacaractere"

    sem_flag = validate_source_ref(value, allow_glob_syntax=False)
    assert sem_flag.decision.allow is False
    assert sem_flag.decision.rule_id == "source_ref.glob_not_supported"

    com_flag = validate_source_ref(value, allow_glob_syntax=True)
    assert com_flag.decision.allow is True, com_flag.decision.reason
    assert com_flag.normalized is not None


def test_padrao_seguro_normaliza_igual_ao_literal() -> None:
    """A normalização é a mesma dos dois lados — a flag não toca nela."""
    assert validate_source_ref("./src\\**/*.py", allow_glob_syntax=True).normalized == (
        "src/**/*.py"
    )
    assert validate_source_ref(".//src///*.py", allow_glob_syntax=True).normalized == "src/*.py"


# ----------------------------------------------- regressão do defeito de normalização (E4)
#
# O defeito, em uma frase: `normalize_source_ref` removia o prefixo `./` **antes** de
# colapsar as barras repetidas. Para `.//src/app.py` isso significa:
#
#     ordem ANTIGA, sobre ".//src/app.py"
#       1. remove "./"   ->  "/src/app.py"
#       2. colapsa "//"  ->  "/src/app.py"   <- barra inicial que o valor cru não tinha
#
#     ordem ATUAL, sobre ".//src/app.py"
#       1. colapsa "//"  ->  "./src/app.py"
#       2. remove "./"   ->  "src/app.py"
#
# A normalização **introduzia** uma barra inicial que o valor cru não tinha. E o valor cru é
# `RELATIVE` para `prevalidate_path_syntax`, então ele passava: a recusa de root-relative
# nunca chegava a ver a barra, porque ela só existia depois.
#
# Por que isso importa a ponto de merecer um teste dedicado: o **normalizado** é o que o
# expansor casa contra a saída de `git ls-tree`, e nenhum caminho do git começa com `/`. O
# `source_ref` resolveria silenciosamente para zero arquivo — um `source_hash` que não reage
# a mudança nenhuma naquele código, ou seja, um falso negativo de staleness. É a mesma classe
# de defeito que AUD-004 fechou, chegando por outro caminho.

#: `(valor cru, normalizado esperado, normalizado que a ordem ANTIGA produzia)`.
#:
#: As variações não são uma matriz exaustiva — são as três formas mínimas que separam "a
#: correção é da ORDEM das operações" de "a correção é específica de uma string":
#: subdiretório aninhado depois do `.//`, `./.` seguido de `//`, e `///` triplo.
NORMALIZATION_REGRESSION: tuple[tuple[str, str, str], ...] = (
    (".//src/app.py", "src/app.py", "/src/app.py"),
    (".//src/nested/app.py", "src/nested/app.py", "/src/nested/app.py"),
    ("././/src/app.py", "src/app.py", "/src/app.py"),
    (".///src/app.py", "src/app.py", "/src/app.py"),
)


def _normalize_com_a_ordem_antiga(value: str) -> str:
    """A implementação **anterior**, reproduzida aqui só para o contrafactual abaixo.

    Difere da atual em exatamente uma coisa: as duas operações trocadas de lugar.
    """
    normalized = value.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = re.sub(r"/{2,}", "/", normalized)
    return normalized.rstrip("/") or normalized


@pytest.mark.parametrize(
    ("value", "esperado", "antigo"), NORMALIZATION_REGRESSION, ids=lambda item: str(item)
)
def test_normalizacao_nao_introduz_barra_inicial(value: str, esperado: str, antigo: str) -> None:
    """**Este é o teste de regressão do defeito.** Ver o bloco de comentário acima.

    Ele falharia contra o código anterior à correção desta sessão porque, com a ordem
    invertida, `normalize_source_ref(value)` devolvia `antigo` — uma string começando com
    `/`. As duas asserções finais o pegam: a igualdade com `esperado` quebraria, e o
    `startswith("/")` quebraria mesmo que alguém afrouxasse a primeira.
    """
    assert antigo.startswith("/"), "o caso de teste precisa ser um que o defeito quebrava"

    result = validate_source_ref(value, allow_glob_syntax=True)

    assert result.decision.allow is True, result.decision.reason
    assert result.normalized == esperado, value
    assert not result.normalized.startswith("/")


@pytest.mark.parametrize(
    ("value", "esperado", "antigo"), NORMALIZATION_REGRESSION, ids=lambda item: str(item)
)
def test_contrafactual_a_ordem_antiga_produzia_a_barra_inicial(
    value: str, esperado: str, antigo: str
) -> None:
    """O contrafactual explícito: as duas ordens, lado a lado, sobre os mesmos valores.

    Sem isto, o teste acima seria só "o resultado é `src/app.py`" — verdadeiro, mas mudo
    sobre **por que** ele existe, e portanto fácil de enfraquecer numa refatoração futura.
    Aqui a diferença entre as duas ordens é afirmada diretamente, e a implementação de
    produção é comparada contra a antiga no mesmo caso.
    """
    assert _normalize_com_a_ordem_antiga(value) == antigo
    assert _normalize_com_a_ordem_antiga(value) != normalize_source_ref(value)
    assert normalize_source_ref(value) == esperado


@pytest.mark.parametrize(
    "value",
    [".//src///app.py", ".//.//src/app.py", "./src/app.py", ".\\\\src\\app.py", "src/app.py"],
)
def test_grafias_equivalentes_convergem_para_o_mesmo_caminho(value: str) -> None:
    """Toda forma de escrever o mesmo caminho relativo tem de chegar ao mesmo normalizado.

    Sem convergência, dois `source_refs` que descrevem o mesmo arquivo produziriam
    `source_hash` diferentes, e a lista congelada deixaria de ser função só do conteúdo.
    """
    result = validate_source_ref(value, allow_glob_syntax=True)

    assert result.decision.allow is True, result.decision.reason
    assert result.normalized == "src/app.py", value


def test_normalizacao_e_idempotente() -> None:
    """Normalizar o já-normalizado não muda nada — condição do `source_hash` ser estável."""
    for value in (".//src///nested//app.py", "./a/b/c.py", "src\\\\a\\b.py", "src/**/*.py"):
        uma_vez = normalize_source_ref(value)
        assert normalize_source_ref(uma_vez) == uma_vez, value


#: Padrões que apontam para segredo **como string literal**. Continuam recusados com a
#: flag ligada: a barreira barata deste módulo só consegue negar a mais, nunca a menos.
SECRET_LOOKING_GLOBS: tuple[str, ...] = (
    ".env*",
    ".env.*",
    "src/*.pem",
    "src/*.key",
    ".ssh/**",
    "secrets/**",
    "config/*secret*",
    "id_rsa*",
)


@pytest.mark.parametrize("value", SECRET_LOOKING_GLOBS)
def test_padrao_que_aponta_para_segredo_continua_recusado(value: str) -> None:
    result = validate_source_ref(value, allow_glob_syntax=True)

    assert result.decision.allow is False, value
    assert result.decision.rule_id == "source_ref.secret_denied"


def test_a_flag_desliga_exatamente_uma_checagem_e_nenhuma_outra() -> None:
    """Prova por diferença, sobre um corpus grande: só existem dois desfechos.

    Para todo valor recusado com `allow_glob_syntax=False`:

    * ou o `rule_id` era `source_ref.glob_not_supported` — e aí ligar a flag pode mudar o
      desfecho (aceitar, ou revelar outra recusa que estava atrás dessa);
    * ou ligar a flag deixa **exatamente** a mesma decisão, com o mesmo `rule_id`.

    Um terceiro caso — a flag mudando o veredito de algo que era recusado por outro motivo
    — é o que este teste existe para tornar impossível de passar despercebido.
    """
    corpus = (
        *[value for value, _rule in DANGEROUS_WITH_GLOB],
        *SAFE_GLOBS,
        *SECRET_LOOKING_GLOBS,
        "",
        "   ",
        ".",
        "src/app.py",
        "src",
        "a" * 300 + "/*.py",
        "src/\x00/*",
        "src/\x01/*",
        "src/.env",
        "src/./*.py",
    )

    for value in corpus:
        desligada = validate_source_ref(value, allow_glob_syntax=False)
        ligada = validate_source_ref(value, allow_glob_syntax=True)

        if desligada.decision.rule_id == "source_ref.glob_not_supported":
            continue

        assert desligada.decision.allow == ligada.decision.allow, value
        assert desligada.decision.rule_id == ligada.decision.rule_id, value
        assert desligada.normalized == ligada.normalized, value


def test_flag_padrao_e_false() -> None:
    """*Fail closed* por omissão: quem não pedir glob continua no regime da E2."""
    assert validate_source_ref("src/*.py").decision.rule_id == "source_ref.glob_not_supported"
