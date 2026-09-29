"""Sinais de alto risco **no texto do objetivo** — [03] §5, passo 1.

## Por que este módulo vive em `safety/`, e não no Analyzer

[03](../../../docs/architecture/03-context-architecture.md) §5 exige `risk = high` quando o
objetivo **ou** os candidatos tocam segredos. O lado dos candidatos já é resolvido pela
gramática de glob do `context_engine`; o lado do **objetivo** era o buraco de E6-AUD-009 —
"corrigir typo em `.env`" sem `candidate_paths` saía `low`/`trivial`.

Fechar esse buraco dentro de `orchestrator/analyzer.py` exigiria uma segunda lista de nomes
sensíveis e uma segunda denylist de caminho. É exatamente a classe de defeito que [04] §5 e
a disciplina de dono único existem para impedir: duas listas divergem, e a que ficar para
trás vira um *fail-open* silencioso sobre classificação de risco. Então a função nasce
**aqui**, ao lado das duas fontes que ela reaproveita, e o Analyzer a importa pronta:

* `redaction.is_sensitive_key` — semântica de **componentes**, não substring. É o que faz
  `password` e `api_key` dispararem enquanto `tokenizer` e `secretary` não;
* `secrets.classify_path_secrecy` — a denylist normativa de [04] §5, aplicada **só** a
  tokens que se parecem com referência a arquivo.

## A separação entre "parece caminho" e "parece nome"

`classify_path_secrecy` não pode rodar sobre palavra solta: o padrão `**/*secret*` casaria
o *basename* `secretary`, e "ajuste secretary page" viraria `high`. O oposto também vale —
`is_sensitive_key(".env")` não reconhece nada, porque `.env` é um caminho, não um nome de
campo. Cada token vai para o classificador que sabe respondê-lo, e `_looks_like_path`
decide qual é qual.

## O que **nunca** sai daqui

O texto que disparou. Um objetivo é campo livre e pode conter uma credencial colada por
engano; devolvê-la dentro do motivo a reimprimiria na resposta HTTP, no `plan`, no
`plan_hash` e na UI. O sinal carrega um `rule_id` estável e as **categorias** que bateram —
nada mais.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.safety.redaction import is_sensitive_key
from app.safety.secrets import SecretPolicy, SecretVerdict, classify_path_secrecy

#: `rule_id` estável desta regra, para a UI de [06] §4 e para `TaskAnalysis.hard_rule`.
OBJECTIVE_MENTIONS_SECRET = "objective_mentions_secret"  # noqa: S105 — id de regra

#: Categoria de um token que a denylist de caminho de [04] §5 reconheceu (`.env`, `*.pem`).
SIGNAL_SECRET_PATH = "secret_path"  # noqa: S105 — rótulo de categoria

#: Categoria de um token que `is_sensitive_key` reconheceu (`password`, `api_key`).
SIGNAL_SECRET_NAME = "secret_name"  # noqa: S105 — rótulo de categoria

#: Corta o texto em tokens. Espaço e a pontuação que cerca uma palavra em prosa; o ponto,
#: a barra, o hífen e o sublinhado **não** cortam, porque são parte do que se quer
#: reconhecer (`.env.local`, `src/.npmrc`, `.git-credentials`, `api_key`).
_TOKEN_SPLIT = re.compile(r"[\s,;:!?()\[\]{}<>\"'`|*]+")

#: Pontuação de fim de frase, removida **só do fim** (`rstrip`, nunca `strip`). O ponto
#: **final** de "edite o .env." não é parte do nome; o ponto **inicial** de `.env` é — e
#: `strip` comeria os dois, apagando justamente o que faz `.env` ser reconhecível.
_TRAILING = ".,;:!?"

#: Um token "parece caminho" quando tem separador, quando começa com ponto (arquivo oculto)
#: ou quando termina em algo com cara de extensão. `secretary` não satisfaz nenhuma.
_EXTENSION = re.compile(r"^[^.]*\.[A-Za-z0-9_-]{1,12}$")


@dataclass(frozen=True, slots=True)
class AnalyzerSignal:
    """Um sinal de alto risco encontrado no objetivo. **Sem o texto que o disparou.**

    ``categories`` diz *que tipo* de menção apareceu — o suficiente para a UI explicar
    "o objetivo menciona um arquivo de segredo" — e nunca qual token era.
    """

    rule_id: str
    categories: tuple[str, ...]

    @property
    def reason(self) -> str:
        """Frase fixa para a UI. Não interpola nada do objetivo, por construção."""
        if SIGNAL_SECRET_PATH in self.categories and SIGNAL_SECRET_NAME in self.categories:
            return "o objetivo menciona um arquivo de segredo e um nome de credencial"
        if SIGNAL_SECRET_PATH in self.categories:
            return "o objetivo menciona um arquivo classificado como segredo ([04] §5)"
        return "o objetivo menciona um nome de credencial"


def _tokens(text: str) -> tuple[str, ...]:
    """Tokens comparáveis do objetivo, sem pontuação de ponta."""
    raw = (piece.rstrip(_TRAILING) for piece in _TOKEN_SPLIT.split(text))
    return tuple(token for token in raw if token)


def _looks_like_path(token: str) -> bool:
    """Este token é uma **referência a arquivo**, e não uma palavra da frase?

    Três formas, e nenhuma delas casa uma palavra comum:

    * tem separador de caminho (`src/.npmrc`, `.aws/credentials`, separador do Windows inclusive);
    * começa com ponto e tem corpo (`.env`, `.npmrc`, `.git-credentials`);
    * tem exatamente uma cara de extensão curta (`app.py`, `chave.pem`).

    É esta função que impede `secretary` de chegar a `classify_path_secrecy`, onde o padrão
    `**/*secret*` o reconheceria pelo *basename* e "ajuste secretary page" viraria `high`.
    """
    if "/" in token or "\\" in token:
        return True
    if token.startswith(".") and len(token) > 1:
        return True
    return bool(_EXTENSION.match(token))


def detect_sensitive_objective_signals(
    text: str, *, policy: SecretPolicy | None = None
) -> AnalyzerSignal | None:
    """O objetivo menciona segredo de forma inequívoca? `None` quando não.

    Determinística, pura e sem rede: é uma hard rule de [03] §5, e roda **antes** do atalho
    trivial do passo 2 — um objetivo que cita `.env` não pode sair `low`/`trivial` por o
    texto também conter "corrigir typo".

    Reconhece o que as duas fontes canônicas já sabem reconhecer, e nada além disso. Não
    existe aqui uma terceira lista de nomes nem uma segunda denylist de caminho.
    """
    categories: list[str] = []

    for token in _tokens(text):
        if _looks_like_path(token):
            if classify_path_secrecy(token, policy).verdict is SecretVerdict.SECRET:
                categories.append(SIGNAL_SECRET_PATH)
            continue
        if is_sensitive_key(token):
            categories.append(SIGNAL_SECRET_NAME)

    if not categories:
        return None

    return AnalyzerSignal(
        rule_id=OBJECTIVE_MENTIONS_SECRET,
        # `dict.fromkeys` e não `set`: a ordem tem de ser determinística, porque a
        # categoria entra em `hard_rule.matched`, que entra no `plan_hash`.
        categories=tuple(dict.fromkeys(categories)),
    )


__all__ = [
    "OBJECTIVE_MENTIONS_SECRET",
    "SIGNAL_SECRET_NAME",
    "SIGNAL_SECRET_PATH",
    "AnalyzerSignal",
    "detect_sensitive_objective_signals",
]
