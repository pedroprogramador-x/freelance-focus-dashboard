"""`content_hash` — a fórmula canônica **única** de [03] §2.

    normalize(s)      = NFC · CRLF→LF · espaços finais removidos por linha ·
                        quebras finais colapsadas em uma
    canonical_json(o) = chaves ordenadas · sem espaço supérfluo · UTF-8

    content_hash = sha256( canonical_json({
      "v":          1,
      "domain":     <domain>,
      "title":      normalize(title),
      "body":       normalize(body),
      "structured": <structured normalizado, ou null>
    }) )

[03](../../../docs/architecture/03-context-architecture.md) §2 é normativo e diz
explicitamente que **nenhum outro documento redefine a fórmula**; este módulo é a única
implementação dela.

Entra: `domain`, `title`, `body`, `structured`. **Não entra:** `id`, `workspace_id`,
`tags`, `source_refs`, `state`, `stale_reason`, `source_hash`, `origin`, timestamps. A
consequência prática é a que [03] §3 exige: editar `body`/`title`/`structured`/`tags` muda
o `content_hash` e **não toca** o baseline de `source_hash`; são perguntas diferentes.

`canonical_json` vem de `app.safety.canonical` — a mesma serialização do `policy_hash`
([02] §7), para que dois módulos não inventem duas normalizações.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from app.context_engine.file_map import encode_path_identity
from app.safety.canonical import CanonicalizationError, canonical_sha256

#: Versão da fórmula. Muda só se [03] §2 mudar — e aí todo `content_hash` gravado passa a
#: ser de outra versão, o que é exatamente o efeito desejado.
CONTENT_HASH_VERSION = 1


def normalize_text(value: str) -> str:
    """`normalize(s)` de [03] §2.

    Quatro passos, na ordem do documento:

    1. **NFC** — `é` composto e `e`+combinante viram a mesma sequência;
    2. **CRLF→LF** — só a sequência `\\r\\n`, como escrito. Um `\\r` solto sobrevive como
       caractere e é apanhado pelo passo 3 quando estiver no fim da linha;
    3. **espaços finais removidos por linha** — via `rstrip()`, que cobre espaço,
       tabulação e o `\\r` residual do passo anterior. Duas versões de um texto que só
       diferem em espaço no fim de linha têm de hashear igual, e é isso que `rstrip()`
       garante de forma exaustiva;
    4. **quebras finais colapsadas em uma** — `n` quebras no fim viram exatamente uma;
       nenhuma quebra continua sendo nenhuma.
    """
    normalized = unicodedata.normalize("NFC", value)
    normalized = normalized.replace("\r\n", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))

    without_trailing = normalized.rstrip("\n")
    if without_trailing != normalized:
        normalized = f"{without_trailing}\n"
    return normalized


def normalize_structured(value: Any) -> Any:
    """Aplica `normalize_text` a **toda** string de `structured`, chave ou valor.

    `structured` é conteúdo autoral tipado ([ADR-0006] item 5) — a justificativa de uma
    decisão vive lá. Se o corpo é normalizado e ele não fosse, o mesmo texto colado num
    campo ou no outro produziria hashes diferentes por um `\\r\\n`.

    Chaves também passam: `canonical_json` já as ordena e aplica NFC, mas a ordenação
    precisa acontecer sobre a forma final, não sobre uma intermediária.

    **Colisão de chave é recusada, não resolvida** (E4-AUD-007). Duas chaves distintas no
    JSON de entrada podem virar a mesma depois da normalização — `"x"` e `"x "` colidem,
    porque `normalize_text` remove espaço no fim de linha. A versão anterior usava uma
    compreensão de dicionário, e a segunda chave simplesmente sobrescrevia a primeira: o
    `content_hash` saía de um objeto com **menos dados** do que o que foi gravado, em
    silêncio, e duas entradas materialmente diferentes podiam hashear igual. Escolher qual
    chave sobrevive não é decisão que este módulo possa tomar sozinho, então ele não toma —
    recusa e deixa o autor desambiguar. A checagem é recursiva: vale em objeto aninhado.
    """
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, list):
        return [normalize_structured(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        origins: dict[str, str] = {}
        for key, item in value.items():
            text_key = str(key)
            normalized_key = normalize_text(text_key)
            if normalized_key in normalized:
                raise CanonicalizationError(
                    f"as chaves {origins[normalized_key]!r} e {text_key!r} de `structured` "
                    f"colidem em {normalized_key!r} depois da normalização; renomeie uma "
                    "delas — sobrescrever silenciosamente perderia dado do hash"
                )
            origins[normalized_key] = text_key
            normalized[normalized_key] = normalize_structured(item)
        return normalized
    return value


def compute_content_hash(
    *,
    domain: str,
    title: str,
    body: str,
    structured: dict[str, Any] | None,
) -> str:
    """A fórmula, literalmente. Levanta `CanonicalizationError` se `structured` não for JSON."""
    return canonical_sha256(
        {
            "v": CONTENT_HASH_VERSION,
            "domain": domain,
            "title": normalize_text(title),
            "body": normalize_text(body),
            "structured": normalize_structured(structured) if structured is not None else None,
        }
    )


#: Versão do `edit_hash`. Separada de `CONTENT_HASH_VERSION` de propósito: são duas
#: perguntas com ciclos de vida diferentes, e [03] §2 é normativa sobre o que entra no
#: `content_hash`. Mudar o conjunto de campos editáveis avança esta, não aquela.
EDIT_HASH_VERSION = 1


def compute_edit_hash(
    *,
    content_hash: str,
    tags: list[str] | tuple[str, ...],
    source_refs: list[str] | tuple[str, ...],
) -> str:
    """A **versão de edição** de uma entrada: tudo que um `PATCH` pode mudar (E6-AUD5-004).

    `content_hash` cobre `domain`/`title`/`body`/`structured` ([03] §2) e **não** cobre
    `tags` nem `source_refs`. Usá-lo como controle de concorrência deixava passar a perda de
    atualização nesses dois campos: dois editores com a mesma leitura mandavam `tags`
    diferentes, os dois recebiam `200`, e o segundo apagava o primeiro sem que ninguém visse.

    Este hash **reaproveita** `content_hash` em vez de recalcular a fórmula de
    título/corpo/estruturado. Duas fórmulas para a mesma pergunta divergiriam na primeira vez
    que uma delas mudasse — é o mesmo motivo pelo qual só existe um detector de segredo.

    `source_refs` entra por `encode_path_identity`: `canonical_json` normaliza texto em NFC,
    e dois caminhos grafados em NFC e NFD são **arquivos diferentes** ([02] §7). Colapsá-los
    aqui faria duas edições genuinamente distintas parecerem a mesma, que é precisamente o
    contrário do que uma versão de edição existe para detectar.

    A ordem de `tags` e `source_refs` é preservada como está na entrada: reordenar é uma
    edição, e o serviço já normaliza `tags` antes de gravar.
    """
    return canonical_sha256(
        {
            "v": EDIT_HASH_VERSION,
            "content_hash": content_hash,
            "tags": list(tags),
            "source_refs": [encode_path_identity(ref) for ref in source_refs],
        }
    )


__all__ = [
    "CONTENT_HASH_VERSION",
    "EDIT_HASH_VERSION",
    "CanonicalizationError",
    "compute_content_hash",
    "compute_edit_hash",
    "normalize_text",
]
