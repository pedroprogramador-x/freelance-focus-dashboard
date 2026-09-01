"""`SafetyPolicy` — configuração imutável da política.

[04](../../../docs/architecture/04-safety-and-git-runtime.md) §5: existe uma política
global e, opcionalmente, um *override* por workspace. **Um override só restringe, nunca
afrouxa** — a composição é intersecção de permissões.

`safety_policy_hash` cobre a política composta efetiva e entra no
`execution_fingerprint` ([02] §7).

Escopo E2: apenas os campos que a E2 realmente usa. Política de comandos, `TestPolicy` e
perfis de capability chegam na E7 — criá-los agora vazios seria esqueleto decorativo.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from app.safety.canonical import canonical_sha256
from app.safety.secrets import DEFAULT_SECRET_PATTERNS, SecretPolicy


@dataclass(frozen=True, slots=True)
class SafetyPolicy:
    """Política efetiva. Imutável: compor produz uma nova instância."""

    #: Denylist de segredos. Compor faz **união** — mais padrões, mais restritivo.
    secret_patterns: tuple[str, ...] = DEFAULT_SECRET_PATTERNS

    #: Exceções explícitas à denylist. Compor faz **intersecção** — menos exceções, mais
    #: restritivo. Nasce vazia: nem `.env.example` é liberado por padrão.
    secret_allow_exceptions: frozenset[str] = field(default_factory=frozenset)

    #: Quando `True`, um fato de link em `Tri.UNKNOWN` **nega** a operação (*fail
    #: closed*). É o padrão: preferimos recusar a fingir que verificamos.
    require_verified_link_status: bool = True

    #: Symlinks e junctions cujo alvo permanece dentro do root. Negados por padrão.
    allow_links_inside_root: bool = False

    #: Teto de tamanho do caminho, em bytes UTF-8. 260 é o limite clássico do Windows;
    #: o valor é conservador e vale em todas as plataformas para manter determinismo.
    max_path_bytes: int = 260

    def secret_policy(self) -> SecretPolicy:
        return SecretPolicy(
            patterns=self.secret_patterns,
            allow_exceptions=self.secret_allow_exceptions,
        )

    def compose(self, override: SafetyPolicyOverride) -> SafetyPolicy:
        """Aplica um override **restritivo**.

        Cada campo combina pela direção que aperta a política. Não existe caminho para
        afrouxar: um override malicioso ou mal configurado não consegue conceder nada.
        """
        return replace(
            self,
            secret_patterns=tuple(
                dict.fromkeys([*self.secret_patterns, *override.add_secret_patterns])
            ),
            secret_allow_exceptions=self.secret_allow_exceptions
            & frozenset(override.keep_secret_allow_exceptions)
            if override.keep_secret_allow_exceptions is not None
            else self.secret_allow_exceptions,
            require_verified_link_status=(
                self.require_verified_link_status or bool(override.require_verified_link_status)
            ),
            allow_links_inside_root=(
                self.allow_links_inside_root and bool(override.allow_links_inside_root)
                if override.allow_links_inside_root is not None
                else self.allow_links_inside_root
            ),
            max_path_bytes=min(
                self.max_path_bytes,
                override.max_path_bytes
                if override.max_path_bytes is not None
                else self.max_path_bytes,
            ),
        )

    def as_canonical(self) -> dict[str, Any]:
        """Forma canônica para hashing. Listas sem semântica de ordem são ordenadas."""
        return {
            "v": 1,
            "secret_patterns": sorted(self.secret_patterns),
            "secret_allow_exceptions": sorted(self.secret_allow_exceptions),
            "require_verified_link_status": self.require_verified_link_status,
            "allow_links_inside_root": self.allow_links_inside_root,
            "max_path_bytes": self.max_path_bytes,
        }


@dataclass(frozen=True, slots=True)
class SafetyPolicyOverride:
    """Override por workspace. Cada campo só consegue apertar a política."""

    add_secret_patterns: tuple[str, ...] = ()
    keep_secret_allow_exceptions: frozenset[str] | None = None
    require_verified_link_status: bool | None = None
    allow_links_inside_root: bool | None = None
    max_path_bytes: int | None = None


def policy_hash(policy: SafetyPolicy) -> str:
    """`safety_policy_hash` da política composta efetiva."""
    return canonical_sha256(policy.as_canonical())
