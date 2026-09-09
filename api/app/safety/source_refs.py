"""Validação de `source_refs` — fundação para o Context Registry (E4).

[02](../../../docs/architecture/02-data-model.md) §2: `source_refs` são globs **relativos
ao workspace**, validados "sem `..`, sem path absoluto, sem casar com a denylist de
segredos".

## Política da E2: **somente caminho literal**

> Um `source_ref` só é aceito se for um **caminho literal relativo**. Qualquer sintaxe de
> glob é recusada — *fail closed*.

Duas tentativas anteriores falharam por motivos diferentes, e a segunda explica esta:

* **sondas finitas** (E2-AUD-003) — testavam o glob contra uma lista de caminhos-exemplo.
  Sonda demonstra a *presença* de um problema, nunca a *ausência*.
* **prova por interseção de segmentos** (E2-AUD-003 v2) — decidia interseção de globs com
  programação dinâmica. Mais honesto, mas a semântica implementada aqui **divergia** da
  semântica do expansor que ainda não existe. Um validador que aprova `a/**/b` segundo uma
  gramática e um expansor que resolve `a/**/b` segundo outra é um buraco de segurança com
  aparência de rigor. Padrões de caminho completo podiam ser burlados, e classes como `[]`
  nem estavam modeladas.

A E2 é fundação. Suportar glob exige **um** dono da gramática, e esse dono é o expansor
canônico do Context Engine, que chega junto com o Context Registry (E4). Até lá:
validador e expansor não podem discordar porque só existe um caso — o literal.

Isso não altera a arquitetura congelada; torna a fundação deliberadamente conservadora.

## E4: `allow_glob_syntax`, e o que ele **não** é

O expansor canônico existe agora, em `app.context_engine.source_ref_expansion`. Ele é o
**único dono da gramática de glob** em todo o sistema. Este módulo continua sendo, como
sempre foi, o **envelope de caminho seguro** — e `allow_glob_syntax=True` não muda isso:

* o que ele faz é **exatamente uma** coisa: pular a recusa por `find_glob_metacharacters`;
* o que ele **não** faz: interpretar `*`, `**`, `?`, `[]` ou `!`. Nada aqui decide o que
  um metacaractere significa, contra o que ele casa, ou quantos segmentos ele atravessa.
  A promessa da E2 — "validador e expansor não podem discordar" — é mantida porque este
  módulo continua sem opinião sobre a gramática, e não porque ele passou a compartilhá-la;
* tudo o mais continua valendo com `True`: `..`, absoluto, root-relative, drive-relative,
  UNC, device namespace, `~`, nome reservado, ADS, alias 8.3, byte nulo, caractere de
  controle, tamanho e normalização instável. O envelope não afrouxou em ponto nenhum.

Consequência a manter em mente: com `True`, a classificação de segredo feita aqui deixa de
ser exata — ela olha o padrão como se fosse uma string literal. `config/*` passa por ela e
ainda assim pode expandir para `config/.env.local`. Por isso a decisão de segredo que vale
é a do expansor, **arquivo por arquivo, depois de expandir**, e é lá que um único segredo
nega o `source_ref` inteiro. A checagem daqui é uma primeira barreira barata que só
consegue negar a mais, nunca a menos.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from app.safety.paths import prevalidate_path_syntax
from app.safety.policy import SafetyPolicy
from app.safety.secrets import SecretVerdict, classify_path_secrecy
from app.safety.types import SafetyDecision

#: Caracteres com semântica especial em algum expansor de caminho comum.
#:
#: `*` e `?` (fnmatch, pathlib, git), `[` e `]` (classes de caractere), `{` e `}`
#: (expansão de chaves em shell e em várias bibliotecas), `!` (negação em gitignore e
#: dentro de classes).
#:
#: A regra é de exclusão, não de sanitização: um `source_ref` com qualquer um destes é
#: recusado, nunca "escapado" para virar literal. Escapar exigiria escolher a gramática do
#: escape — de novo, uma decisão que pertence ao expansor.
GLOB_METACHARACTERS: frozenset[str] = frozenset("*?[]{}!")

#: Caracteres que `normalize_source_ref` traduz para `/` — hoje, só a barra invertida do
#: Windows. **Fonte única** (E4-AUD3-002): `git_runtime.UnrepresentablePaths` deriva
#: `_UNREPRESENTABLE_CHARS` diretamente deste conjunto, em vez de manter uma cópia. Um
#: caractere que o Git devolve e que está aqui dentro é, por construção, um caractere que
#: nenhum `source_ref` pode conter literalmente — a normalização o absorveu antes que ele
#: chegasse a virar padrão — e é exatamente esse o conjunto que `git_runtime` precisa saber
#: que não consegue nomear de volta. Acrescentar um caractere aqui já propaga a mudança
#: para o outro lado; não há um segundo lugar para lembrar de atualizar.
PATH_SEPARATOR_ALIASES: frozenset[str] = frozenset("\\")

_SEPARATOR_TRANSLATION = str.maketrans({char: "/" for char in PATH_SEPARATOR_ALIASES})


@dataclass(frozen=True, slots=True)
class SourceRefResult:
    decision: SafetyDecision
    normalized: str | None


def normalize_source_ref(value: str) -> str:
    """Forma normalizada e determinística: separador `/`, sem `./`, sem barras duplicadas.

    **A ordem das duas operações importa** (corrigido na E4). Enquanto o `./` era removido
    *antes* de colapsar as barras, `.//src/app.py` virava `/src/app.py`: a normalização
    **introduzia** uma barra inicial que o valor cru não tinha. O valor cru é `RELATIVE`
    para `prevalidate_path_syntax`, então ele passava, e o resultado normalizado — que é o
    que o expansor da E4 casa contra a saída de `git ls-tree` — nunca casaria com nada,
    porque nenhum caminho do git começa com `/`. Um `source_ref` que aponta para código
    resolvendo silenciosamente para zero arquivo é a receita do falso `fresh` que AUD-004
    existe para impedir.

    Colapsando as barras primeiro, `.//src///app.py` e `./src/app.py` chegam ambos em
    `src/app.py`, e a normalização passa a ser idempotente. Nada disso afrouxa: um caminho
    cru genuinamente root-relative (`/src`, `\\src`) já foi recusado por
    `prevalidate_path_syntax` antes de chegar aqui.
    """
    normalized = value.translate(_SEPARATOR_TRANSLATION).strip()
    normalized = re.sub(r"/{2,}", "/", normalized)
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.rstrip("/") or normalized


def find_glob_metacharacters(value: str) -> list[str]:
    """Metacaracteres presentes, em ordem de aparição e sem repetir."""
    return list(dict.fromkeys(char for char in value if char in GLOB_METACHARACTERS))


def validate_source_ref(
    value: str,
    *,
    policy: SafetyPolicy | None = None,
    allow_glob_syntax: bool = False,
) -> SourceRefResult:
    """Valida um único `source_ref`. **Envelope de caminho, nunca gramática de glob.**

    Recusa: vazio, device namespace, UNC, absoluto, root-relative, drive-relative, `..`,
    `~`, nome reservado, ADS, alias 8.3, byte nulo, caractere de controle, caminho longo
    demais, normalização instável e qualquer caminho que a política de segredos barre.

    ``allow_glob_syntax`` (E4) tem **um** efeito, e só ele: com `True`, a recusa por
    `find_glob_metacharacters` não é aplicada. Nenhuma outra checagem muda de
    comportamento, em nenhuma direção.

    **Esta função não interpreta gramática de glob e não pode passar a interpretar.** Ela
    não sabe o que `*` casa, se `**` atravessa `/`, o que há dentro de `[]` nem o que `!`
    nega. Quem sabe é `app.context_engine.source_ref_expansion`, o dono único da gramática
    — e ele é quem decide, arquivo por arquivo depois de expandir, se algum resultado é
    segredo. Se um dia a decisão sobre o significado de um metacaractere precisar ser
    tomada, ela **não** é tomada aqui: duas gramáticas discordando é exatamente o buraco de
    segurança que E2-AUD-003 fechou.

    Com `True`, a classificação de segredo abaixo trata o padrão como string literal e
    portanto **não é mais exata**: ela consegue negar a mais (um padrão que "parece"
    segredo), nunca a menos. A garantia real vem do expansor.
    """
    active = policy or SafetyPolicy()

    if not value or not value.strip():
        return SourceRefResult(
            SafetyDecision(False, "source_ref.empty", "source_ref vazio", ""), None
        )

    raw = value.strip()

    # Os prefixos perigosos são conferidos no valor cru: normalizar primeiro colapsaria
    # `//` em `/`, e `//servidor/share` — um UNC — viraria um inofensivo `/servidor`,
    # apagando a evidência que a política usa para decidir.
    if raw.startswith(("\\\\?\\", "\\\\.\\")):
        return SourceRefResult(
            SafetyDecision(
                False, "source_ref.device_namespace", "device namespace do Windows", raw
            ),
            None,
        )

    # A sintaxe de caminho vem antes da checagem de glob para que `../**` continue sendo
    # recusado como travessia e `//srv/**` como UNC — o motivo registrado precisa ser o
    # problema mais grave, não o mais recente.
    #
    # `windows_semantics=True` fixo: um `source_ref` é **sempre** relativo ao workspace,
    # então a leitura mais estrita de `/foo` (root-relative) vale em qualquer sistema. Sem
    # isso o `rule_id` gravado num `SafetyEvent` dependeria de onde o backend roda.
    syntax = prevalidate_path_syntax(
        raw, policy=active, allow_absolute=False, windows_semantics=True
    )
    if not syntax.allow:
        return SourceRefResult(
            SafetyDecision(
                False,
                f"source_ref.{syntax.rule_id.removeprefix('path.')}",
                syntax.reason,
                syntax.subject_redacted,
            ),
            None,
        )

    # A ÚNICA checagem que `allow_glob_syntax` desliga. Note que ela roda **depois** de
    # `prevalidate_path_syntax`: com o glob liberado, `../**` continua sendo recusado como
    # travessia e `//srv/**` como UNC, porque essas recusas já aconteceram acima.
    if not allow_glob_syntax:
        metacharacters = find_glob_metacharacters(raw)
        if metacharacters:
            return SourceRefResult(
                SafetyDecision(
                    False,
                    "source_ref.glob_not_supported",
                    f"sintaxe de glob ({''.join(metacharacters)}) não é suportada neste "
                    "contexto; só caminho literal relativo é aceito",
                    raw,
                ),
                None,
            )

    normalized = normalize_source_ref(value)
    if posixpath.normpath(normalized) != normalized and normalized not in (".", "/"):
        # A normalização precisa ser um ponto fixo: se `normpath` ainda muda o valor,
        # sobrou algo que a checagem sintática não viu.
        return SourceRefResult(
            SafetyDecision(
                False, "source_ref.unstable_normalization", "normalização instável", value
            ),
            None,
        )

    # Com `allow_glob_syntax=False` (o caso da E2) esta classificação é **exata**: o
    # caminho literal ou está na denylist ou não está, e isso é toda a prova necessária.
    # Com `True`, o padrão é classificado como se fosse literal — barato, e só capaz de
    # negar a mais. A prova de verdade passa a ser a do expansor, arquivo por arquivo.
    classification = classify_path_secrecy(normalized, active.secret_policy())
    if classification.verdict is SecretVerdict.SECRET:
        return SourceRefResult(
            SafetyDecision(
                False,
                "source_ref.secret_denied",
                f"source_ref aponta para segredo (padrão `{classification.matched_pattern}`)",
                normalized,
            ),
            None,
        )

    return SourceRefResult(
        SafetyDecision(True, "source_ref.ok", "source_ref aceito", normalized), normalized
    )


def validate_source_refs(
    values: list[str],
    *,
    policy: SafetyPolicy | None = None,
    allow_glob_syntax: bool = False,
) -> tuple[list[str], list[SafetyDecision]]:
    """Valida uma lista. Devolve os normalizados aceitos (ordenados, sem duplicata) e as recusas.

    A ordenação torna o resultado determinístico: a mesma entrada produz sempre a mesma
    lista, o que E4 precisa para calcular `source_hash` de forma estável.

    ``allow_glob_syntax`` é repassado a cada `validate_source_ref` sem nenhuma outra
    consequência — esta função também não interpreta gramática de glob.
    """
    accepted: list[str] = []
    rejected: list[SafetyDecision] = []

    for value in values:
        result = validate_source_ref(value, policy=policy, allow_glob_syntax=allow_glob_syntax)
        if result.decision.allow and result.normalized is not None:
            accepted.append(result.normalized)
        else:
            rejected.append(result.decision)

    return sorted(dict.fromkeys(accepted)), rejected
