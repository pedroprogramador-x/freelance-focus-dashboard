"""Tipos puros do Safety Kernel.

`PathFacts` é a fronteira entre o que o filesystem sabe e o que a política decide
([04](../../../docs/architecture/04-safety-and-git-runtime.md) §4). Ele é **preenchido**
por `app.path_runtime` e **consumido** por `app.safety.paths`.

Valores tipados em vez de dicts genéricos: um campo ausente vira erro de tipo, não uma
decisão silenciosamente permissiva.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Tri(str, Enum):
    """Verdadeiro, falso ou **não verificado**.

    O terceiro estado não é luxo. Algumas propriedades de path no Windows exigem APIs de
    baixo nível que a E2 não implementa; representá-las como ``FALSE`` seria mentir para a
    política e abrir o buraco exato que a auditoria fechou. ``UNKNOWN`` faz a política
    **fechar** onde ela exige verificação.
    """

    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"

    @classmethod
    def of(cls, value: bool | None) -> Tri:
        if value is None:
            return cls.UNKNOWN
        return cls.TRUE if value else cls.FALSE

    @property
    def is_true(self) -> bool:
        return self is Tri.TRUE

    @property
    def is_false(self) -> bool:
        return self is Tri.FALSE

    @property
    def is_unknown(self) -> bool:
        return self is Tri.UNKNOWN


@dataclass(frozen=True, slots=True)
class ObjectIdentity:
    """Identidade do objeto de filesystem.

    POSIX: ``(st_dev, st_ino)``. Windows: número serial do volume e índice do arquivo,
    expostos pelo CPython nos mesmos campos desde a 3.8.

    É o que permite a verificação pós-abertura comparar *o objeto que abrimos* com *o
    objeto que inspecionamos*, sem depender de re-derivar o caminho.
    """

    volume_id: int
    file_id: int


@dataclass(frozen=True, slots=True)
class PathFacts:
    """Fatos observados sobre um caminho. Nenhuma decisão embutida.

    Os campos seguem [04] §4. ``ancestor_link_outside_root`` não é conveniência: o
    documento exige inspeção de junctions e reparse points em **todos** os ancestrais
    entre `root` e o alvo, e a política precisa desse fato para decidir sem fazer IO.

    **Semântica dos três campos de link.** ``is_symlink``, ``is_junction`` e
    ``is_reparse_point`` descrevem a **cadeia léxica inteira** do caminho pedido —
    qualquer componente, do primeiro ao último —, não apenas o componente final. Um
    symlink intermediário que aponta para dentro da própria raiz continua sendo um link, e
    a política precisa vê-lo: `resolve()` o atravessaria e apagaria a evidência
    (E2-AUD-004). ``ancestor_link_outside_root`` é o sinal mais forte, reservado ao link
    que **sai** da raiz.
    """

    requested_path: str
    canonical_root: str
    canonical_target: str | None
    exists: bool
    parent_identity: ObjectIdentity | None
    target_identity: ObjectIdentity | None
    volume: str | None
    root_volume: str | None
    is_symlink: Tri
    is_junction: Tri
    is_reparse_point: Tri
    is_unc: Tri
    is_device_namespace: Tri
    is_drive_relative: Tri
    contained: Tri
    ancestor_link_outside_root: Tri
    post_open_target: str | None = None
    post_open_identity: ObjectIdentity | None = None
    inspection_error: str | None = None


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    """Resultado puro de uma avaliação.

    ``subject_redacted`` já passou pelo redator: quem persistir um `SafetyEvent` a partir
    daqui não precisa lembrar de sanitizar.
    """

    allow: bool
    rule_id: str
    reason: str
    subject_redacted: str

    def __bool__(self) -> bool:  # pragma: no cover - conveniência de leitura
        return self.allow
