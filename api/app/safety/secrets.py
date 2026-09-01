"""Classificação de segredos **por caminho**, antes de qualquer leitura.

Camada 1 e 2 da proteção de segredos
([04](../../../docs/architecture/04-safety-and-git-runtime.md) §5).

Regra central, e o motivo de este módulo existir separado: a decisão é tomada a partir do
**nome/caminho**, nunca do conteúdo. Abrir um arquivo para descobrir se ele é secreto já
seria tê-lo lido.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatchcase

#: Denylist normativa de [04] §5. Padrões estilo glob, avaliados sobre o caminho
#: relativo ao workspace, com separador `/` e caixa normalizada.
DEFAULT_SECRET_PATTERNS: tuple[str, ...] = (
    # `.env*` — **um** padrão, exatamente como o freeze. Dividir em `.env` + `.env.*`
    # deixava passar `.envrc`, `.environment` e qualquer variante sem ponto (E2-AUD-002).
    ".env*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    ".aws/**",
    ".ssh/**",
    "secrets/**",
    "**/credentials*",
    "**/*secret*",
)


class SecretVerdict(str, Enum):
    SECRET = "secret"  # noqa: S105 — rótulo de veredito, não uma credencial
    ALLOWED = "allowed"


@dataclass(frozen=True, slots=True)
class SecretClassification:
    verdict: SecretVerdict
    rule_id: str
    matched_pattern: str | None


@dataclass(frozen=True, slots=True)
class SecretPolicy:
    """Política de segredos.

    ``allow_exceptions`` existe e nasce **vazia**. A denylist congelada em [04] §5 inclui
    ``.env*``, o que abrange ``.env.example``. A documentação não abre exceção para
    templates, então a E2 **não** abre por conta própria: `.env.example` é tratado como
    segredo por padrão (*fail closed*). A exceção é possível, mas só por configuração
    explícita — nunca por heurística de nome.
    """

    patterns: tuple[str, ...] = DEFAULT_SECRET_PATTERNS
    allow_exceptions: frozenset[str] = field(default_factory=frozenset)


def _normalize(relative_path: str) -> str:
    """Normaliza para comparação: separador `/`, sem `./`, caixa baixa.

    Caixa baixa porque Windows é case-insensitive: sem isso, `.ENV` escaparia da
    denylist. Aplicado em todas as plataformas para que a decisão seja determinística e
    os testes não dependam do sistema onde rodam.
    """
    value = relative_path.replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    value = posixpath.normpath(value) if value else value
    if value == ".":
        value = ""
    return value.lower()


def _matches(pattern: str, normalized: str) -> bool:
    pattern = pattern.lower()

    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return normalized == prefix or normalized.startswith(f"{prefix}/")

    if pattern.startswith("**/"):
        tail = pattern[3:]
        basename = normalized.rsplit("/", 1)[-1]
        return fnmatchcase(basename, tail) or fnmatchcase(normalized, pattern)

    # Padrões sem `/` valem para qualquer diretório: `.env` cobre `sub/.env`.
    if "/" not in pattern:
        basename = normalized.rsplit("/", 1)[-1]
        return fnmatchcase(basename, pattern)

    return fnmatchcase(normalized, pattern)


def classify_path_secrecy(
    relative_path: str,
    policy: SecretPolicy | None = None,
) -> SecretClassification:
    """Classifica um caminho relativo ao workspace **sem abri-lo**."""
    active = policy or SecretPolicy()
    normalized = _normalize(relative_path)

    if not normalized:
        return SecretClassification(SecretVerdict.ALLOWED, "secret.empty_path", None)

    if normalized in {exception.lower() for exception in active.allow_exceptions}:
        return SecretClassification(SecretVerdict.ALLOWED, "secret.explicit_exception", None)

    for pattern in active.patterns:
        if _matches(pattern, normalized):
            return SecretClassification(SecretVerdict.SECRET, "secret.denylist", pattern)

    return SecretClassification(SecretVerdict.ALLOWED, "secret.not_matched", None)


def is_secret_path(relative_path: str, policy: SecretPolicy | None = None) -> bool:
    return classify_path_secrecy(relative_path, policy).verdict is SecretVerdict.SECRET
