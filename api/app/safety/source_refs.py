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


@dataclass(frozen=True, slots=True)
class SourceRefResult:
    decision: SafetyDecision
    normalized: str | None


def normalize_source_ref(value: str) -> str:
    """Forma normalizada e determinística: separador `/`, sem `./`, sem barras duplicadas."""
    normalized = value.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = re.sub(r"/{2,}", "/", normalized)
    return normalized.rstrip("/") or normalized


def find_glob_metacharacters(value: str) -> list[str]:
    """Metacaracteres presentes, em ordem de aparição e sem repetir."""
    return list(dict.fromkeys(char for char in value if char in GLOB_METACHARACTERS))


def validate_source_ref(
    value: str,
    *,
    policy: SafetyPolicy | None = None,
) -> SourceRefResult:
    """Valida um único `source_ref`.

    Recusa: vazio, device namespace, UNC, absoluto, root-relative, drive-relative, `..`,
    `~`, nome reservado, ADS, **qualquer sintaxe de glob** e qualquer caminho que a
    política de segredos barre.
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

    metacharacters = find_glob_metacharacters(raw)
    if metacharacters:
        return SourceRefResult(
            SafetyDecision(
                False,
                "source_ref.glob_not_supported",
                f"sintaxe de glob ({''.join(metacharacters)}) não é suportada na E2; "
                "só caminho literal relativo é aceito",
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

    # Sem curinga, esta classificação é **exata**: o caminho literal ou está na denylist ou
    # não está. É toda a prova de que a E2 precisa.
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
) -> tuple[list[str], list[SafetyDecision]]:
    """Valida uma lista. Devolve os normalizados aceitos (ordenados, sem duplicata) e as recusas.

    A ordenação torna o resultado determinístico: a mesma entrada produz sempre a mesma
    lista, o que E4 precisa para calcular `source_hash` de forma estável.
    """
    accepted: list[str] = []
    rejected: list[SafetyDecision] = []

    for value in values:
        result = validate_source_ref(value, policy=policy)
        if result.decision.allow and result.normalized is not None:
            accepted.append(result.normalized)
        else:
            rejected.append(result.decision)

    return sorted(dict.fromkeys(accepted)), rejected
