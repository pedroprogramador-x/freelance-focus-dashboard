"""Path Runtime — coleta de fatos de filesystem.

Contraparte de `app.safety`: aqui mora **todo** o IO de path
([04](../../docs/architecture/04-safety-and-git-runtime.md) §4). A dependência corre só
nesta direção — `path_runtime → safety`, para os tipos. `safety` nunca importa este
módulo.

Fluxo implementado:

```
safety.prevalidate_path_syntax(requested)   # puro
path_runtime.inspect(requested, root)       # IO      → PathFacts
safety.decide_path(facts)                   # puro
os.open(...)                                # SEM truncar
path_runtime.inspect_opened(facts, fd)      # IO      → PathFacts + pós-abertura
safety.decide_post_open(facts)              # puro
<operação>                                  # só aqui escreve/trunca
```

**Isto não é sandbox.** A verificação pós-abertura estreita a janela TOCTOU; não a fecha.
O risco residual está declarado em [04] §4 e não é contornado aqui.

Escopo E2 (*foundation*): inspeção e abertura para **leitura**, sobre alvos
pré-existentes. Criação, escrita e truncamento pertencem ao Full Safety Runtime (E7) —
por isso nenhuma flag de truncamento aparece neste módulo.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path, PurePath

from app.safety.paths import (
    PathForm,
    PathIntent,
    classify_path_form,
    decide_path,
    decide_post_open,
    prevalidate_path_syntax,
)
from app.safety.policy import SafetyPolicy
from app.safety.types import ObjectIdentity, PathFacts, SafetyDecision, Tri

_IS_WINDOWS = sys.platform == "win32"

#: `FILE_ATTRIBUTE_REPARSE_POINT`. Exposto por `stat` apenas no Windows.
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_TAG_MOUNT_POINT = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
_TAG_SYMLINK = getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C)


class PathAccessDenied(PermissionError):
    """Operação recusada pela política. Carrega a decisão para virar `SafetyEvent`."""

    def __init__(self, decision: SafetyDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


def _identity(stat_result: os.stat_result) -> ObjectIdentity:
    """Identidade do objeto.

    POSIX: `(st_dev, st_ino)`. Windows: o CPython preenche os mesmos campos com o número
    serial do volume e o índice do arquivo, o que serve exatamente ao mesmo propósito.
    """
    return ObjectIdentity(volume_id=stat_result.st_dev, file_id=stat_result.st_ino)


def _volume_of(path: Path, stat_result: os.stat_result | None) -> str | None:
    if _IS_WINDOWS:
        drive = PurePath(path).drive
        return drive.upper() or None
    if stat_result is None:
        return None
    return str(stat_result.st_dev)


def _link_facts(path: Path) -> tuple[Tri, Tri, Tri]:
    """`(is_symlink, is_junction, is_reparse_point)` para um caminho, sem seguir links."""
    try:
        info = path.lstat()
    except (OSError, ValueError):
        return Tri.UNKNOWN, Tri.UNKNOWN, Tri.UNKNOWN

    is_symlink = Tri.of(stat.S_ISLNK(info.st_mode))

    if not _IS_WINDOWS:
        # Junction e reparse point são conceitos exclusivos do NTFS.
        return is_symlink, Tri.FALSE, Tri.FALSE

    attributes = getattr(info, "st_file_attributes", None)
    if attributes is None:
        return is_symlink, Tri.UNKNOWN, Tri.UNKNOWN

    is_reparse = Tri.of(bool(attributes & _REPARSE_ATTRIBUTE))
    if is_reparse.is_false:
        return is_symlink, Tri.FALSE, Tri.FALSE

    tag = getattr(info, "st_reparse_tag", None)
    if tag is None:
        # É reparse point, mas não sabemos de que tipo: fato desconhecido, não falso.
        return is_symlink, Tri.UNKNOWN, is_reparse

    if tag == _TAG_MOUNT_POINT:
        return is_symlink, Tri.TRUE, is_reparse
    if tag == _TAG_SYMLINK:
        return Tri.TRUE, Tri.FALSE, is_reparse
    return is_symlink, Tri.FALSE, is_reparse


@dataclass(frozen=True, slots=True)
class _ChainFacts:
    """Resultado da varredura **léxica** dos componentes pedidos."""

    is_symlink: Tri
    is_junction: Tri
    is_reparse_point: Tri
    escapes_root: Tri


def _combine(current: Tri, observed: Tri) -> Tri:
    """`TRUE` vence: um link encontrado é fato, mesmo que outro componente seja incerto."""
    if current.is_true or observed.is_true:
        return Tri.TRUE
    if current.is_unknown or observed.is_unknown:
        return Tri.UNKNOWN
    return Tri.FALSE


def _lexical_chain_facts(root_path: Path, requested: str, canonical_root: Path) -> _ChainFacts:
    """Percorre os componentes **léxicos pedidos**, sem resolver antes.

    É o coração da correção de E2-AUD-004. A versão anterior resolvia o alvo e só então
    subia pelos pais — mas o `resolve()` já tinha atravessado os links, então a cadeia
    inspecionada era a **real**, não a pedida. Em

    ```
    workspace/real/
    workspace/link -> real/
    workspace/link/file.txt
    ```

    ela via `workspace/real/file.txt` e concluía "nenhum link".

    Aqui cada prefixo é montado por concatenação e submetido a `lstat` **antes** de seguir
    para o próximo — inclusive o componente final. Os fatos resultantes descrevem a cadeia
    inteira: um symlink em qualquer posição fica visível para a política, esteja o alvo
    dentro ou fora da raiz ([04] §4).

    **Todos** os componentes são percorridos. Havia um `parts[:128]` aqui e ele truncava em
    silêncio: um link no componente 150 não era inspecionado e os fatos voltavam limpos —
    `FALSE`, não `UNKNOWN` — então a política liberava um caminho que ninguém verificou. O
    corte também não protegia de nada: a lista de componentes é finita por construção,
    derivada de uma string finita que a pré-validação já limita por `max_path_bytes`.
    """
    symlink = junction = reparse = Tri.FALSE
    escapes = Tri.FALSE

    parts = [part for part in re.split(r"[\\/]+", requested) if part not in ("", ".")]
    current = root_path

    for part in parts:
        current = current / part
        observed_symlink, observed_junction, observed_reparse = _link_facts(current)

        symlink = _combine(symlink, observed_symlink)
        junction = _combine(junction, observed_junction)
        reparse = _combine(reparse, observed_reparse)

        if not any(
            fact.is_true for fact in (observed_symlink, observed_junction, observed_reparse)
        ):
            continue

        # Só aqui resolvemos — e apenas para saber se **este** link sai da raiz.
        try:
            resolved = current.resolve(strict=False)
        except (OSError, ValueError):
            escapes = _combine(escapes, Tri.UNKNOWN)
            continue

        stays_inside = resolved == canonical_root or resolved.is_relative_to(canonical_root)
        escapes = _combine(escapes, Tri.of(not stays_inside))

    return _ChainFacts(
        is_symlink=symlink,
        is_junction=junction,
        is_reparse_point=reparse,
        escapes_root=escapes,
    )


def _nearest_existing(path: Path) -> Path | None:
    """Sobe até achar um ancestral existente, ou até a raiz do volume.

    Sem contador defensivo: `Path.parent` é idempotente na raiz, então `current.parent ==
    current` termina o laço sempre. O contador anterior parava em 128 e devolvia `None`,
    o que zerava o fato de volume em caminhos profundos — outra degradação silenciosa.
    """
    current = path
    while True:
        if current.exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def inspect(requested: str, root: Path | str, *, allow_absolute: bool = False) -> PathFacts:
    """Coleta `PathFacts` para um caminho relativo à raiz. **Não decide nada.**"""
    root_path = Path(root)

    try:
        canonical_root = root_path.resolve(strict=True)
    except (OSError, ValueError) as error:
        return _failed_facts(requested, str(root), f"raiz irresolúvel: {error}")

    root_stat: os.stat_result | None
    try:
        root_stat = canonical_root.stat()
    except (OSError, ValueError):
        root_stat = None

    form = classify_path_form(requested)
    is_unc = Tri.of(form is PathForm.UNC)
    is_device_namespace = Tri.of(form is PathForm.DEVICE_NAMESPACE)
    is_drive_relative = Tri.of(form is PathForm.DRIVE_RELATIVE)

    candidate = (
        Path(requested)
        if allow_absolute and form is PathForm.ABSOLUTE_QUALIFIED
        else root_path / requested
    )

    try:
        canonical_target = candidate.resolve(strict=False)
    except (OSError, ValueError) as error:
        return _failed_facts(
            requested, str(canonical_root), f"alvo irresolúvel: {error}", canonical_root
        )

    exists = canonical_target.exists()

    target_stat: os.stat_result | None = None
    if exists:
        try:
            target_stat = canonical_target.stat()
        except (OSError, ValueError):
            target_stat = None

    parent_stat: os.stat_result | None = None
    try:
        if canonical_target.parent.exists():
            parent_stat = canonical_target.parent.stat()
    except (OSError, ValueError):
        parent_stat = None

    contained = Tri.of(
        canonical_target == canonical_root or canonical_target.is_relative_to(canonical_root)
    )

    # Fatos de link vêm da cadeia **léxica** pedida, não da resolvida: um symlink
    # intermediário não pode desaparecer só porque `resolve()` o atravessou.
    chain = _lexical_chain_facts(root_path, requested, canonical_root)

    volume_reference = canonical_target if exists else _nearest_existing(canonical_target)
    volume_stat = target_stat
    if volume_stat is None and volume_reference is not None:
        try:
            volume_stat = volume_reference.stat()
        except (OSError, ValueError):
            volume_stat = None

    return PathFacts(
        requested_path=requested,
        canonical_root=str(canonical_root),
        canonical_target=str(canonical_target),
        exists=exists,
        parent_identity=_identity(parent_stat) if parent_stat else None,
        target_identity=_identity(target_stat) if target_stat else None,
        volume=_volume_of(volume_reference or canonical_target, volume_stat),
        root_volume=_volume_of(canonical_root, root_stat),
        is_symlink=chain.is_symlink,
        is_junction=chain.is_junction,
        is_reparse_point=chain.is_reparse_point,
        is_unc=is_unc,
        is_device_namespace=is_device_namespace,
        is_drive_relative=is_drive_relative,
        contained=contained,
        ancestor_link_outside_root=chain.escapes_root,
    )


def _failed_facts(
    requested: str,
    root: str,
    error: str,
    canonical_root: Path | None = None,
) -> PathFacts:
    return PathFacts(
        requested_path=requested,
        canonical_root=str(canonical_root) if canonical_root else root,
        canonical_target=None,
        exists=False,
        parent_identity=None,
        target_identity=None,
        volume=None,
        root_volume=None,
        is_symlink=Tri.UNKNOWN,
        is_junction=Tri.UNKNOWN,
        is_reparse_point=Tri.UNKNOWN,
        is_unc=Tri.UNKNOWN,
        is_device_namespace=Tri.UNKNOWN,
        is_drive_relative=Tri.UNKNOWN,
        contained=Tri.UNKNOWN,
        ancestor_link_outside_root=Tri.UNKNOWN,
        inspection_error=error,
    )


def inspect_opened(facts: PathFacts, fd: int) -> PathFacts:
    """Enriquece os fatos com o que só o **handle aberto** sabe.

    `post_open_target` — re-derivar o caminho a partir do descritor — só é possível de
    forma portável no Linux (`/proc/self/fd`). No Windows exigiria
    `GetFinalPathNameByHandle` via `ctypes`, que a E2 não introduz; o campo fica `None` e
    a verificação recai sobre a **identidade do objeto**, que é comparável em ambos.
    """
    try:
        opened = os.fstat(fd)
    except OSError as error:
        return replace(facts, inspection_error=f"fstat falhou: {error}")

    post_open_target: str | None = None
    if sys.platform.startswith("linux"):
        try:
            post_open_target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            post_open_target = None

    return replace(
        facts,
        post_open_identity=_identity(opened),
        post_open_target=post_open_target,
    )


@contextmanager
def open_checked(
    requested: str,
    root: Path | str,
    *,
    policy: SafetyPolicy | None = None,
    intent: PathIntent = PathIntent.READ,
) -> Iterator[tuple[int, PathFacts]]:
    """Abre um arquivo existente após as duas fases de validação.

    Nunca usa flag de truncamento — nem no futuro caminho de escrita. Truncar antes da
    decisão pós-abertura destruiria o arquivo mesmo quando a decisão fosse negar
    ([04] §4, "regra de ordem, obrigatória").

    Levanta `PathAccessDenied` na primeira negação.
    """
    active = policy or SafetyPolicy()

    syntax = prevalidate_path_syntax(requested, policy=active, allow_absolute=False)
    if not syntax.allow:
        raise PathAccessDenied(syntax)

    facts = inspect(requested, root)
    decision = decide_path(facts, policy=active, intent=intent)
    if not decision.allow:
        raise PathAccessDenied(decision)

    if facts.canonical_target is None:
        raise PathAccessDenied(
            SafetyDecision(False, "path.no_target", "alvo indisponível", requested)
        )

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW  # POSIX: recusa symlink no componente final
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY  # Windows: sem tradução de fim de linha

    try:
        fd = os.open(facts.canonical_target, flags)
    except OSError as error:
        raise PathAccessDenied(
            SafetyDecision(False, "path.open_failed", f"abertura falhou: {error}", requested)
        ) from error

    try:
        post_facts = inspect_opened(facts, fd)
        post_decision = decide_post_open(post_facts, policy=active)
        if not post_decision.allow:
            raise PathAccessDenied(post_decision)
        yield fd, post_facts
    finally:
        os.close(fd)
