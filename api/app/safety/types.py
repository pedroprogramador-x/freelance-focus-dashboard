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
from typing import Protocol


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

    @property
    def is_verifiable(self) -> bool:
        """Esta identidade **prova** algo? Não, se o `file_id` é 0.

        Alguns sistemas de arquivos/volumes não expõem um índice estável e devolvem `0` para
        qualquer arquivo; nesse caso `(volume, 0) == (volume, 0)` vale para objetos diferentes.
        Fail closed: identidade não verificável nunca é prova de nada (E7.5). O `volume_id` não
        é filtrado — `0` ali é legítimo em alguns dispositivos.
        """
        return self.file_id != 0


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
    #: E7.5 — fatos de **criação exclusiva** (`path_runtime.create_exclusive`). Sem identidade
    #: prévia do alvo (ele não existia), a base de comparação é o que o próprio handle criado
    #: e o caminho/pai observados logo depois dizem. `parent_identity` é a linha de base do pai.
    post_create_path_identity: ObjectIdentity | None = None
    post_create_parent_identity: ObjectIdentity | None = None
    post_create_regular: Tri = Tri.UNKNOWN
    post_create_reparse: Tri = Tri.UNKNOWN


@dataclass(frozen=True, slots=True)
class WorktreeRootFacts:
    """Fatos sobre a raiz de worktrees (E7.4). Preenchido por `app.path_runtime`.

    Tudo é observado sobre o caminho **canônico** (`resolve(strict=True)`): neste ambiente o
    `%LOCALAPPDATA%` léxico pode ser virtualizado (MSIX) para outro lugar, e é o caminho real
    que o Git grava. ``reparse_in_canonical_chain`` cobre **todos** os componentes do caminho
    canônico, da raiz do volume até o próprio diretório — um reparse point que o `resolve()`
    não atravessa (placeholder de nuvem, por exemplo) continua visível.

    Os três ``overlaps_*`` são relações de contenção **nos dois sentidos** (um dentro do outro,
    ou iguais), calculadas sobre caminhos canônicos com `normcase` + `commonpath`, nunca por
    substring. ``UNKNOWN`` quando o outro lado não pôde ser canonizado.
    """

    requested_path: str
    requested_is_absolute: bool
    canonical_path: str | None
    exists: bool
    is_directory: Tri
    reparse_in_canonical_chain: Tri
    #: Algum componente **pedido** (léxico) é link/junction/reparse? A raiz nunca é aceita
    #: por um caminho que passe por link, mesmo que o destino canônico fosse aceitável.
    reparse_in_requested_chain: Tri
    identity: ObjectIdentity | None
    overlaps_sync_root: Tri
    overlaps_repository_toplevel: Tri
    overlaps_git_common_dir: Tri
    repository_toplevel: str | None = None
    repository_toplevel_identity: ObjectIdentity | None = None
    git_common_dir: str | None = None
    git_common_dir_identity: ObjectIdentity | None = None
    inspection_error: str | None = None


@dataclass(frozen=True, slots=True)
class WorktreeRoot:
    """Raiz de worktrees **já validada** para **um** repositório (E7.4, D7).

    Construída só por `app.path_runtime.prepare_worktree_root`, depois que
    `app.safety.worktree_location.decide_worktree_root` permitiu — `test_architecture.py` trava
    o ponto único de construção. `app.git_runtime` a recebe pronta e **não** reavalia política:
    só confere, antes e depois de agir, que as identidades registradas aqui não mudaram
    (a janela TOCTOU é estreitada, não fechada — [04] §4).

    ``repository_*`` e ``git_common_dir*`` amarram a raiz ao repositório para o qual ela foi
    validada: usada com outro repositório, a identidade não confere e a criação é recusada.
    """

    canonical_path: str
    identity: ObjectIdentity
    repository_toplevel: str
    repository_toplevel_identity: ObjectIdentity
    git_common_dir: str
    git_common_dir_identity: ObjectIdentity


class TreeWriter(Protocol):
    """Escreve os arquivos de **uma** worktree nova (E7.4). Implementado só em `path_runtime`.

    Cada `write_file` cria um arquivo **novo** (nunca sobrescreve, nunca segue link) sob a raiz
    com que o escritor foi aberto, criando os diretórios intermediários de forma verificada.
    Qualquer recusa ou falha levanta `OSError` (a negação de política é `PathAccessDenied`,
    subclasse de `PermissionError`); o que já foi escrito fica para diagnóstico.
    """

    def write_file(self, relative: str, content: bytes, *, executable: bool) -> None: ...


class TreeWriterFactory(Protocol):
    """Abre um `TreeWriter` sobre a raiz já criada pelo Git, conferindo a identidade dela."""

    def __call__(self, root: str, root_identity: ObjectIdentity) -> TreeWriter: ...


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
