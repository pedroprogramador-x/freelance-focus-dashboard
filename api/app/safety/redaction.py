"""Redator de segredos — camada 3 da proteção
([04](../../../docs/architecture/04-safety-and-git-runtime.md) §5).

É **um só** e vive aqui. Roda na seleção de contexto, na persistência de log e na
serialização de resposta JSON de `/api/*`.

Puro: opera sobre string, não lê arquivo, não consulta ambiente.
"""

from __future__ import annotations

import re

REDACTED = "«redigido»"

# Ordem importa: padrões mais específicos primeiro.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "pem_block",
        re.compile(
            r"-----BEGIN[A-Z ]*PRIVATE KEY-----.*?-----END[A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    ("url_credentials", re.compile(r"(?<=://)[^/\s:@]+:[^/\s:@]+(?=@)")),
    ("bearer", re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-+/=]{8,}")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9._\-]{8,}")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{16,}")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b((?:api[_-]?key|secret|token|password|passwd|authorization)"
            r"\s*[:=]\s*)(?:\"|')?([^\s\"',;]{6,})"
        ),
    ),
)


def redact(text: str) -> str:
    """Substitui segredos reconhecíveis por ``«redigido»``.

    Conservador por desenho: prefere redigir demais a deixar passar. Não é — e não
    pretende ser — detecção exaustiva; é a última das três camadas, não a única.
    """
    if not text:
        return text

    result = text
    for name, pattern in _PATTERNS:
        if name in ("bearer", "assigned_secret"):
            result = pattern.sub(lambda m: f"{m.group(1)}{REDACTED}", result)
        else:
            result = pattern.sub(REDACTED, result)
    return result


def redact_path(path: str) -> str:
    """Redação aplicada a um caminho antes de virar ``subject`` de `SafetyEvent`.

    Um caminho pode carregar credencial (remote git com token). O resto do caminho é
    informação de diagnóstico legítima e é preservado.
    """
    return redact(path)
