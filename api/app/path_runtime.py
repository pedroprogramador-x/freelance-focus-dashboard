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

E7.5-A: primitivas de **baixo nível** para o `ToolExecutor` (abrir existente sem truncar, criar
com exclusividade, atualizar, apagar por identidade, enumerar diretório, vincular a raiz da
execution workspace). Elas coletam e **revalidam fatos** e executam o efeito depois de
autorizado; **não decidem política** (capability, segredo, `.git`, `SafetyPolicy` do
Developer) — quem decide é `safety`, chamado pelo executor entre as etapas. Erro técnico é
`PathOperationFailed` (categoria estruturada, sem texto cru de `OSError`); negação segue
`PathAccessDenied`. Nenhuma primitiva importa `tool_executor` nem `CancelToken`: cooperam com
cancelamento por um `Callable[[], bool]`.

E7.4 (D7): `inspect_worktree_root` coleta os fatos da raiz de worktrees (caminho canônico,
reparse na cadeia, identidade, sobreposição com OneDrive/repositório/`.git`) e
`prepare_worktree_root` compõe com `safety.decide_worktree_root`, no mesmo padrão de
`open_checked`, devolvendo a `WorktreeRoot` que o `git_runtime` recebe pronta.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path, PurePath

from app.safety.paths import (
    PathForm,
    PathIntent,
    classify_path_form,
    decide_path,
    decide_post_open,
    path_components,
    prevalidate_path_syntax,
)
from app.safety.policy import SafetyPolicy
from app.safety.types import (
    ObjectIdentity,
    PathFacts,
    SafetyDecision,
    Tri,
    WorktreeRoot,
    WorktreeRootFacts,
)
from app.safety.worktree_location import decide_worktree_root

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
    """`(is_symlink, is_junction, is_reparse_point)` para um caminho, sem seguir links.

    Contrato **legado** (E2): falha de `lstat` vira `UNKNOWN` sem distinção de causa. Os fluxos
    da E7.5 não usam esta forma: leem o `lstat` **uma vez** e derivam os fatos de link do mesmo
    resultado (`_link_facts_of`), de modo que a falha de coleta tenha uma única origem explícita.
    """
    try:
        info = path.lstat()
    except (OSError, ValueError):
        return Tri.UNKNOWN, Tri.UNKNOWN, Tri.UNKNOWN
    return _link_facts_of(info)


def _link_facts_of(info: os.stat_result) -> tuple[Tri, Tri, Tri]:
    """Os fatos de link de um `lstat` **já lido**. Pura: nenhum IO, nenhuma falha a esconder."""
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
    #: Só no modo `tolerate_absent_tail`: o `lstat` de algum componente falhou por um motivo que
    #: **não** é ausência (E/S, permissão…). É falha de **coleta**, não propriedade do caminho.
    lstat_failed: bool = False


def _combine(current: Tri, observed: Tri) -> Tri:
    """`TRUE` vence: um link encontrado é fato, mesmo que outro componente seja incerto."""
    if current.is_true or observed.is_true:
        return Tri.TRUE
    if current.is_unknown or observed.is_unknown:
        return Tri.UNKNOWN
    return Tri.FALSE


_PRESENT = "present"
_ABSENT = "absent"
_FAILED = "failed"


def _probe_component(path: Path) -> tuple[str, os.stat_result | None]:
    """`(present, lstat)`, `(absent, None)` (ausência **comprovada**) ou `(failed, None)`.

    É a **única** leitura do componente: quem chama deriva os fatos de link do `lstat` devolvido
    (`_link_facts_of`) em vez de ler de novo — uma segunda leitura poderia falhar depois de esta ter
    tido sucesso e a falha se perderia como `UNKNOWN` sem causa (B-AUD-003).

    * `FileNotFoundError` no `lstat`: os componentes anteriores já foram lidos com sucesso, então
      este é o primeiro que falta → `absent`;
    * `NotADirectoryError` só vale se o **pai** for lido agora e **não** for diretório (ausência
      lógica: não existe nada sob um arquivo). Se o pai não puder ser lido → `failed`;
    * `PermissionError`, `OSError` genérico, `ValueError` e qualquer erro de `lstat` ambíguo
      **não** são ausência → `failed`.
    """
    try:
        info = path.lstat()
    except FileNotFoundError:
        return _ABSENT, None
    except NotADirectoryError:
        try:
            parent = path.parent.lstat()
        except (OSError, ValueError):
            return _FAILED, None
        return (_ABSENT if not stat.S_ISDIR(parent.st_mode) else _FAILED), None
    except (OSError, ValueError):
        return _FAILED, None
    return _PRESENT, info


def _absence_is_proven(path: Path) -> bool:
    """`path` **comprovadamente** não existe — e só isso (ver `_probe_component`)."""
    return _probe_component(path)[0] == _ABSENT


def _lexical_parts(requested: str, form: PathForm) -> list[str]:
    """Os componentes que o runtime **materializa** — a mesma decomposição da safety.

    Para `PathForm.RELATIVE` (todo caminho de `ToolRequest`) é exatamente
    `safety.paths.path_components`: `/` e `\\` são separadores em **todas** as plataformas,
    `""` e `.` somem (`"."` → `[]`, a própria raiz). Sem isso o POSIX materializava
    `sub\\a.txt` como **um** nome literal, enquanto a política tinha decidido sobre
    `["sub", "a.txt"]` (Linux-CI-001). Consequência deliberada da gramática: um nome POSIX que
    contém `\\` literal não é endereçável por `ToolRequest`.

    As demais formas (o absoluto de `allow_absolute`, e as que a pré-validação recusa)
    mantêm a decomposição legada, sem mudança de comportamento.
    """
    if form is PathForm.RELATIVE:
        return path_components(requested)
    return [part for part in re.split(r"[\\/]+", requested) if part not in ("", ".")]


def _lexical_chain_facts(
    root_path: Path,
    requested: str,
    canonical_root: Path,
    *,
    tolerate_absent_tail: bool = False,
) -> _ChainFacts:
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

    # A mesma decomposição que `inspect` usa para materializar o alvo (Linux-CI-001).
    parts = _lexical_parts(requested, classify_path_form(requested))
    current = root_path
    lstat_failed = False

    for part in parts:
        current = current / part
        if tolerate_absent_tail:
            # E7.5-A, **opt-in** (todos os fluxos mediados da E7.5): um componente
            # **comprovadamente ausente** não é link — e nenhum dos seguintes pode existir. É um
            # fato verificado, não `UNKNOWN`; sem isso a política nunca autoriza criar arquivo
            # novo. Qualquer outro erro (permissão, E/S, reparse ilegível) é falha de **coleta**:
            # os fatos de link ficam `UNKNOWN` (fail closed) **e** `lstat_failed` o torna
            # explícito, para `inspect` preencher `inspection_error`. Os fatos de link vêm do
            # **mesmo** `lstat` do probe — não há segunda leitura cuja falha pudesse se perder.
            # O padrão legado (E2-AUD) segue: ausente = UNKNOWN.
            probe, info = _probe_component(current)
            if probe == _ABSENT:
                break
            if info is None:
                lstat_failed = True
                observed_symlink = observed_junction = observed_reparse = Tri.UNKNOWN
            else:
                observed_symlink, observed_junction, observed_reparse = _link_facts_of(info)
        else:
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
        lstat_failed=lstat_failed,
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


def inspect(
    requested: str,
    root: Path | str,
    *,
    allow_absolute: bool = False,
    tolerate_absent_tail: bool = False,
) -> PathFacts:
    """Coleta `PathFacts` para um caminho relativo à raiz. **Não decide nada.**

    ``tolerate_absent_tail`` (E7.5-A) é para **criação** e leitura mediada: componentes que
    ainda não existem deixam de ser `UNKNOWN` para link/reparse (ausência verificada), e o resto
    da cadeia — o que **existe** — é inspecionado como sempre. O padrão preserva o contrato
    legado.

    **`inspection_error`** (E7.5-B) é a fonte explícita de falha **técnica** de coleta: quando um
    `stat`/`lstat` necessário falha por um motivo que **não** é ausência comprovada (E/S,
    permissão, `OSError` ambíguo), o campo recebe um **código interno fixo** — nunca o texto do
    erro, nunca caminho. Ausência comprovada (`FileNotFoundError`) **não** é erro. Um fato
    individual `UNKNOWN` (propriedade não comprovável) também **não** é erro: a política segue
    fail closed. Invariante: `exists` e `target_identity is None` só coexistem com
    `inspection_error`.
    """
    root_path = Path(root)
    failure: str | None = None

    try:
        canonical_root = root_path.resolve(strict=True)
    except (OSError, ValueError):
        return _failed_facts(requested, str(root), "root_unresolvable")

    root_stat: os.stat_result | None
    try:
        root_stat = canonical_root.stat()
    except FileNotFoundError:
        root_stat = None
    except (OSError, ValueError):
        root_stat = None
        failure = "root_stat_failed"

    form = classify_path_form(requested)
    is_unc = Tri.of(form is PathForm.UNC)
    is_device_namespace = Tri.of(form is PathForm.DEVICE_NAMESPACE)
    is_drive_relative = Tri.of(form is PathForm.DRIVE_RELATIVE)

    # Materialização e cadeia léxica (`_lexical_chain_facts`) usam **a mesma** decomposição,
    # `_lexical_parts`. `requested_path` segue a string original; `canonical_target` é o alvo real.
    if allow_absolute and form is PathForm.ABSOLUTE_QUALIFIED:
        candidate = Path(requested)
    elif form is PathForm.RELATIVE:
        candidate = root_path.joinpath(*_lexical_parts(requested, form))  # `"."` → a própria raiz
    else:
        candidate = root_path / requested

    try:
        canonical_target = candidate.resolve(strict=False)
    except (OSError, ValueError):
        return _failed_facts(requested, str(canonical_root), "target_unresolvable", canonical_root)

    try:
        exists = canonical_target.exists()
    except (OSError, ValueError):  # `exists()` só engole as errnos de **ausência**
        exists = False
        failure = failure or "target_stat_failed"

    target_stat: os.stat_result | None = None
    if exists:
        try:
            target_stat = canonical_target.stat()
        except FileNotFoundError:
            exists = False  # sumiu entre as duas leituras: ausência, não falha
        except (OSError, ValueError):
            failure = failure or "target_stat_failed"

    parent_stat: os.stat_result | None = None
    try:
        if canonical_target.parent.exists():
            parent_stat = canonical_target.parent.stat()
    except FileNotFoundError:
        parent_stat = None
    except (OSError, ValueError):
        failure = failure or "parent_stat_failed"

    contained = Tri.of(
        canonical_target == canonical_root or canonical_target.is_relative_to(canonical_root)
    )

    # Fatos de link vêm da cadeia **léxica** pedida, não da resolvida: um symlink
    # intermediário não pode desaparecer só porque `resolve()` o atravessou.
    chain = _lexical_chain_facts(
        root_path, requested, canonical_root, tolerate_absent_tail=tolerate_absent_tail
    )

    volume_reference: Path | None
    try:
        volume_reference = canonical_target if exists else _nearest_existing(canonical_target)
    except (OSError, ValueError):
        volume_reference = None
        failure = failure or "volume_stat_failed"
    volume_stat = target_stat
    if volume_stat is None and volume_reference is not None:
        try:
            volume_stat = volume_reference.stat()
        except FileNotFoundError:
            volume_stat = None
        except (OSError, ValueError):
            volume_stat = None
            failure = failure or "volume_stat_failed"

    if failure is None and chain.lstat_failed:
        failure = "chain_lstat_failed"

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
        inspection_error=failure,
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
    except OSError:
        return replace(facts, inspection_error="fstat_failed")

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


# ------------------------------------------------------------ E7.4: raiz de worktrees


def _has_reparse(info: os.stat_result) -> bool:
    """Link/reparse no próprio objeto (sem seguir). POSIX: symlink; Windows: atributo."""
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & _REPARSE_ATTRIBUTE)


def _reparse_in_chain(path: Path) -> Tri:
    """Algum componente de `path` (absoluto), da raiz do volume até ele, é reparse/link?

    Usado nas duas cadeias da raiz de worktrees: a **pedida** (um link em qualquer ponto
    recusa) e a **canônica** — depois de `resolve()` não deveria haver link nenhum, e o que
    sobra é reparse point que o `resolve()` não atravessa (placeholder de nuvem, por
    exemplo). Falha de `lstat` num componente é `UNKNOWN`, nunca `FALSE`.
    """
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except (OSError, ValueError):
            return Tri.UNKNOWN
        if _has_reparse(info):
            return Tri.TRUE
    return Tri.FALSE


def _overlaps(a: Path, b: Path) -> bool:
    """`a` e `b` (já canônicos) são iguais ou um contém o outro. `normcase` + `commonpath`."""
    left, right = os.path.normcase(str(a)), os.path.normcase(str(b))
    try:
        common = os.path.commonpath([left, right])
    except ValueError:  # volumes diferentes, ou absoluto × relativo: não se sobrepõem
        return False
    return common in (left, right)


def _canonical_identity(value: str) -> tuple[Path, ObjectIdentity] | None:
    try:
        canonical = Path(value).resolve(strict=True)
        return canonical, _identity(canonical.stat())
    except (OSError, ValueError, RuntimeError):
        return None


def inspect_worktree_root(
    requested: Path | str,
    *,
    sync_roots: tuple[Path | str, ...],
    repository_toplevel: str,
    git_common_dir: str,
) -> WorktreeRootFacts:
    """Coleta `WorktreeRootFacts` para a raiz de worktrees de **um** repositório. Não decide.

    ``sync_roots`` são as raízes de sincronização conhecidas (`config`), canonizadas com
    `strict=False` — uma pasta do OneDrive ainda inexistente continua proibindo o lugar onde
    ela nasceria. ``repository_toplevel`` e ``git_common_dir`` vêm do `git_runtime` e precisam
    existir: o que não canoniza vira `UNKNOWN`, e a política fecha.
    """
    text = str(requested)
    absolute = classify_path_form(text) is PathForm.ABSOLUTE_QUALIFIED and "\x00" not in text
    base = WorktreeRootFacts(
        requested_path=text,
        requested_is_absolute=absolute,
        canonical_path=None,
        exists=False,
        is_directory=Tri.UNKNOWN,
        reparse_in_canonical_chain=Tri.UNKNOWN,
        reparse_in_requested_chain=Tri.UNKNOWN,
        identity=None,
        overlaps_sync_root=Tri.UNKNOWN,
        overlaps_repository_toplevel=Tri.UNKNOWN,
        overlaps_git_common_dir=Tri.UNKNOWN,
    )
    if not absolute:
        return base
    if ".." in Path(text).parts:
        # `a\..\b` é colapsado léxicamente no Windows e atravessa `a` no POSIX: a cadeia
        # pedida deixaria de descrever o caminho que o SO segue. Fail closed.
        return replace(base, inspection_error="componente `..` no caminho pedido")

    try:
        canonical = Path(text).resolve(strict=True)
    except FileNotFoundError:
        return base
    except (OSError, ValueError, RuntimeError) as error:
        return replace(base, inspection_error=f"raiz irresolúvel: {type(error).__name__}")

    try:
        info = canonical.lstat()
    except (OSError, ValueError) as error:
        return replace(base, canonical_path=str(canonical), inspection_error=type(error).__name__)

    sync_overlap = Tri.FALSE
    for root in sync_roots:
        try:
            sync_canonical = Path(root).resolve(strict=False)
        except (OSError, ValueError, RuntimeError):
            sync_overlap = _combine(sync_overlap, Tri.UNKNOWN)
            continue
        sync_overlap = _combine(sync_overlap, Tri.of(_overlaps(canonical, sync_canonical)))

    repository = _canonical_identity(repository_toplevel)
    common = _canonical_identity(git_common_dir)

    return replace(
        base,
        canonical_path=str(canonical),
        exists=True,
        is_directory=Tri.of(stat.S_ISDIR(info.st_mode)),
        reparse_in_canonical_chain=_reparse_in_chain(canonical),
        reparse_in_requested_chain=_reparse_in_chain(Path(text)),
        identity=_identity(info),
        overlaps_sync_root=sync_overlap,
        overlaps_repository_toplevel=(
            Tri.UNKNOWN if repository is None else Tri.of(_overlaps(canonical, repository[0]))
        ),
        overlaps_git_common_dir=(
            Tri.UNKNOWN if common is None else Tri.of(_overlaps(canonical, common[0]))
        ),
        repository_toplevel=None if repository is None else str(repository[0]),
        repository_toplevel_identity=None if repository is None else repository[1],
        git_common_dir=None if common is None else str(common[0]),
        git_common_dir_identity=None if common is None else common[1],
    )


def prepare_worktree_root(
    requested: Path | str,
    *,
    sync_roots: tuple[Path | str, ...],
    repository_toplevel: str,
    git_common_dir: str,
) -> WorktreeRoot:
    """Inspeciona, decide e devolve a `WorktreeRoot` validada — o **único** ponto que a constrói.

    Mesmo padrão de `open_checked`: fatos aqui, decisão em `safety`, `PathAccessDenied` na
    negação. Não cria diretório nenhum: a raiz precisa existir (`AppSettings.ensure_worktrees_dir`).
    """
    facts = inspect_worktree_root(
        requested,
        sync_roots=sync_roots,
        repository_toplevel=repository_toplevel,
        git_common_dir=git_common_dir,
    )
    decision = decide_worktree_root(facts)
    if not decision.allow:
        raise PathAccessDenied(decision)
    assert facts.canonical_path is not None and facts.identity is not None
    assert facts.repository_toplevel is not None and facts.repository_toplevel_identity is not None
    assert facts.git_common_dir is not None and facts.git_common_dir_identity is not None
    return WorktreeRoot(
        canonical_path=facts.canonical_path,
        identity=facts.identity,
        repository_toplevel=facts.repository_toplevel,
        repository_toplevel_identity=facts.repository_toplevel_identity,
        git_common_dir=facts.git_common_dir,
        git_common_dir_identity=facts.git_common_dir_identity,
    )


# ------------------------------------------------------ E7.4: materialização da worktree


def _deny_write(rule_id: str, reason: str, subject: str) -> PathAccessDenied:
    return PathAccessDenied(SafetyDecision(False, rule_id, reason, subject))


class CheckedTreeWriter:
    """`TreeWriter` da E7.4: cria os arquivos do `base_commit` numa worktree **nova**.

    A worktree nasce com `git worktree add --no-checkout`; o conteúdo não passa pelo checkout
    do Git (filtros, atributos, hooks), e sim por aqui, a partir dos blobs crus. Regras:

    * o caminho relativo passa por `prevalidate_path_syntax` — a mesma política de sempre —
      e não pode ter barra invertida nem componente `.git`;
    * **antes de cada operação** (cada `mkdir`, a abertura do arquivo e, de novo, antes dos
      bytes) a cadeia inteira é revalidada: nenhum reparse/link do volume até a raiz, a raiz
      com a identidade recebida, e cada componente até o pai final diretório de verdade, sem
      reparse, com a identidade registrada quando foi criado. O registro é só a
      **expectativa** do que cada componente precisa continuar sendo — nunca autoriza nada
      sozinho (reauditoria P2-001: raiz movida e trocada por junction entre duas chamadas);
    * diretórios são criados **um nível por vez** (`os.mkdir`, nunca `makedirs`); a
      identidade de um diretório novo é estabelecida pelo `lstat` logo depois de criá-lo;
    * o arquivo é criado com `O_CREAT | O_EXCL` (e `O_NOFOLLOW` no POSIX): nunca sobrescreve
      nem segue um link pré-existente no nome final. Depois de aberto, `fstat` precisa ser
      arquivo regular com a mesma identidade que o `lstat` do caminho; só então os bytes
      são escritos;
    * `executable` vira `0o777`/`0o666` (menos a umask), como o Git faz; no Windows o bit não
      existe e não se finge que existe.

    **Não é sandbox**: entre as reconferências e a escrita resta a janela TOCTOU declarada em
    [04] §4. Falha deixa o que já foi escrito — nada é apagado.
    """

    def __init__(self, root: str, root_identity: ObjectIdentity) -> None:
        self._root = root
        try:
            info = os.lstat(root)
        except OSError as error:
            raise _deny_write("tree_write.root_missing", type(error).__name__, root) from error
        if not stat.S_ISDIR(info.st_mode) or _has_reparse(info) or _identity(info) != root_identity:
            raise _deny_write("tree_write.root_changed", "raiz da worktree mudou", root)
        self._root_identity = root_identity
        #: Identidade **esperada** de cada diretório já criado. Expectativa, nunca prova.
        self._expected: dict[tuple[str, ...], ObjectIdentity] = {}
        self._verify_chain(())

    def _join(self, parts: tuple[str, ...]) -> str:
        return os.path.join(self._root, *parts)

    def _verify_chain(self, parts: tuple[str, ...]) -> None:
        """Revalida do volume até ``parts``: é chamada antes de **toda** criação ou escrita.

        A raiz participa sempre: nenhum reparse/link em nenhum ancestral nem nela (um link
        no lugar da raiz faria um `lstat` de componente atravessá-lo sem ver nada de
        errado), e a mesma identidade de quando o escritor foi criado. Depois, cada
        componente, em ordem: diretório, sem reparse, identidade igual à registrada.
        """
        if _reparse_in_chain(Path(self._root)) is not Tri.FALSE:
            raise _deny_write("tree_write.root_changed", "link na cadeia da raiz", self._root)
        try:
            info = os.lstat(self._root)
        except OSError as error:
            raise _deny_write(
                "tree_write.root_missing", type(error).__name__, self._root
            ) from error
        if (
            not stat.S_ISDIR(info.st_mode)
            or _has_reparse(info)
            or _identity(info) != self._root_identity
        ):
            raise _deny_write("tree_write.root_changed", "raiz da worktree mudou", self._root)
        for depth in range(1, len(parts) + 1):
            prefix = parts[:depth]
            subject = "/".join(prefix)
            expected = self._expected.get(prefix)
            if expected is None:
                raise _deny_write("tree_write.dir_unknown", "diretório não criado aqui", subject)
            try:
                info = os.lstat(self._join(prefix))
            except OSError as error:
                raise _deny_write(
                    "tree_write.dir_missing", type(error).__name__, subject
                ) from error
            if not stat.S_ISDIR(info.st_mode) or _has_reparse(info) or _identity(info) != expected:
                raise _deny_write("tree_write.dir_changed", "diretório trocado", subject)

    def _ensure_dirs(self, parts: tuple[str, ...]) -> None:
        """Cria o que falta de ``parts``, um nível por vez, revalidando a cadeia antes de cada
        `mkdir`. A identidade de um nível novo nasce do `lstat` logo depois de criá-lo."""
        for depth in range(1, len(parts) + 1):
            prefix = parts[:depth]
            if prefix in self._expected:
                continue
            self._verify_chain(prefix[:-1])
            path = self._join(prefix)
            with contextlib.suppress(FileExistsError):
                os.mkdir(path)
            info = os.lstat(path)
            if not stat.S_ISDIR(info.st_mode) or _has_reparse(info):
                raise _deny_write(
                    "tree_write.not_directory", "componente não é diretório", "/".join(prefix)
                )
            self._expected[prefix] = _identity(info)

    def write_file(self, relative: str, content: bytes, *, executable: bool) -> None:
        if "\\" in relative:
            raise _deny_write("tree_write.backslash", "barra invertida", relative)
        syntax = prevalidate_path_syntax(relative, allow_absolute=False)
        if not syntax.allow:
            raise PathAccessDenied(syntax)
        parts = tuple(relative.split("/"))
        if any(part.casefold() == ".git" for part in parts):
            raise _deny_write("tree_write.dot_git", "componente .git", relative)
        parent = parts[:-1]
        self._ensure_dirs(parent)
        self._verify_chain(parent)

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        for extra in ("O_NOFOLLOW", "O_BINARY", "O_CLOEXEC"):
            flags |= getattr(os, extra, 0)
        path = self._join(parts)
        fd = os.open(path, flags, 0o777 if executable else 0o666)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode):
                raise _deny_write("tree_write.not_regular", "alvo aberto não é arquivo", relative)
            if _identity(os.lstat(path)) != _identity(opened):
                raise _deny_write("tree_write.toctou", "caminho trocado após a abertura", relative)
            self._verify_chain(parent)
            view = memoryview(content)
            while view:
                written = os.write(fd, view)
                view = view[written:]
        finally:
            os.close(fd)


# ------------------------------------------------- E7.5-A: primitivas para o ToolExecutor


class PathFailure(str, Enum):
    """Categoria de uma falha **técnica** de operação de path (não é negação de política)."""

    NOT_FOUND = "not_found"
    IS_DIRECTORY = "is_directory"
    NOT_DIRECTORY = "not_directory"
    NOT_REGULAR = "not_regular"
    ALREADY_EXISTS = "already_exists"
    IO_ERROR = "io_error"
    CANCELLED = "cancelled"
    #: A **coleta de fatos** de uma entrada (ou a releitura do diretório enumerado) falhou por E/S,
    #: permissão ou `OSError` ambíguo. Não é integridade (nada provou troca de objeto) nem política:
    #: a operação não pode afirmar que a enumeração é completa. O valor é o código de `ERROR` fixo.
    INSPECTION_FAILED = "path_inspection_failed"


class PathOperationFailed(Exception):
    """A operação foi **autorizada** mas falhou tecnicamente.

    Distinta de `PathAccessDenied` (decisão de política) e de `PathIntegrityViolation` (cadeia
    ou identidade não provada): o `ToolExecutor` devolve `ERROR` para esta e `DENIED` para as
    outras. A mensagem é só a categoria — **nunca** o texto do
    `OSError`, que carrega o caminho absoluto. `errno_code` fica para diagnóstico interno.
    """

    def __init__(self, category: PathFailure, *, errno_code: int | None = None) -> None:
        super().__init__(category.value)
        self.category = category
        self.errno_code = errno_code


def _classify_os_error(error: OSError) -> PathOperationFailed:
    category = PathFailure.IO_ERROR
    if isinstance(error, FileNotFoundError):
        category = PathFailure.NOT_FOUND
    elif isinstance(error, IsADirectoryError):
        category = PathFailure.IS_DIRECTORY
    elif isinstance(error, NotADirectoryError):
        category = PathFailure.NOT_DIRECTORY
    elif isinstance(error, FileExistsError):
        category = PathFailure.ALREADY_EXISTS
    elif error.errno == errno.ELOOP:  # O_NOFOLLOW num symlink
        category = PathFailure.NOT_REGULAR
    return PathOperationFailed(category, errno_code=error.errno)


def _check_cancel(is_cancelled: Callable[[], bool] | None) -> None:
    """Qualquer resposta que não seja exatamente `False` conta como cancelar (fail closed)."""
    if is_cancelled is not None and is_cancelled() is not False:
        raise PathOperationFailed(PathFailure.CANCELLED)


class IntegrityFailure(str, Enum):
    """Categoria de uma falha de **integridade** observada por uma primitiva (um fato)."""

    ROOT_IDENTITY_CHANGED = "root_identity_changed"
    PARENT_IDENTITY_CHANGED = "parent_identity_changed"
    TARGET_IDENTITY_CHANGED = "target_identity_changed"
    REPARSE_DETECTED = "reparse_detected"
    #: Não foi possível **provar** a identidade (ou a cadeia): fail closed.
    INTEGRITY_UNVERIFIABLE = "integrity_unverifiable"
    #: Os fatos recebidos situam o alvo fora da raiz vinculada (o chamador pulou a decisão).
    TARGET_OUTSIDE_ROOT = "target_outside_root"


class PathIntegrityViolation(Exception):
    """Uma primitiva **recusou o efeito** porque não consegue provar a cadeia/identidade.

    É um **fato** estruturado, não uma decisão: `path_runtime` não constrói `SafetyDecision`
    nem levanta `PathAccessDenied` aqui. Quem mapeia a violação para uma regra, uma
    `SafetyDecision` e um `DENIED` é o `ToolExecutor` (camada de decisão). A mensagem é só a
    categoria — nunca caminho absoluto.
    """

    def __init__(self, category: IntegrityFailure) -> None:
        super().__init__(category.value)
        self.category = category


def _violation(category: IntegrityFailure) -> PathIntegrityViolation:
    return PathIntegrityViolation(category)


def _trusted_identity(info: os.stat_result) -> ObjectIdentity:
    """A identidade do objeto, **ou** a recusa de uma que não prova nada (`file_id == 0`)."""
    identity = _identity(info)
    if not identity.is_verifiable:
        raise _violation(IntegrityFailure.INTEGRITY_UNVERIFIABLE)
    return identity


def _require_same_identity(
    expected: ObjectIdentity, observed: ObjectIdentity, changed: IntegrityFailure
) -> None:
    """`expected == observed`, e **ambas verificáveis**. `(volume, 0) == (volume, 0)` não prova."""
    if not expected.is_verifiable or not observed.is_verifiable:
        raise _violation(IntegrityFailure.INTEGRITY_UNVERIFIABLE)
    if expected != observed:
        raise _violation(changed)


def _same_identity(a: ObjectIdentity, b: ObjectIdentity) -> bool:
    return a.is_verifiable and b.is_verifiable and a == b


@dataclass(frozen=True, slots=True)
class BoundRoot:
    """A raiz da execution workspace, **vinculada**: caminho canônico e identidade verificados.

    Construída por `bind_root`. `verify_root` a revalida — o executor a chama antes de cada
    operação e as primitivas de escrita a chamam antes de cada passo.
    """

    path: str
    identity: ObjectIdentity


def _require_clean_chain(path: Path) -> None:
    """Nenhum componente é link/reparse — e a leitura de cada um **provou** isso.

    * link/reparse → `REPARSE_DETECTED`;
    * componente que **sumiu** (`FileNotFoundError`/`NotADirectoryError`) depois de a raiz ter
      sido lida → a cadeia mudou sob a operação: `INTEGRITY_UNVERIFIABLE` (como antes);
    * E/S, permissão, `OSError` ambíguo → falha **técnica** de observação: `IO_ERROR`, nunca
      uma negação de integridade sem causa.

    Não usa `_reparse_in_chain` (E7.4), que colapsa toda falha em `UNKNOWN`.
    """
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except (FileNotFoundError, NotADirectoryError):
            raise _violation(IntegrityFailure.INTEGRITY_UNVERIFIABLE) from None
        except (OSError, ValueError):
            raise PathOperationFailed(PathFailure.IO_ERROR) from None
        if _has_reparse(info):
            raise _violation(IntegrityFailure.REPARSE_DETECTED)


def bind_root(path: Path | str, *, expected_identity: ObjectIdentity | None = None) -> BoundRoot:
    """Vincula a raiz de uma execution workspace.

    Exige caminho absoluto qualificado, existente, diretório, **sem link/reparse** em nenhum
    componente — nem na cadeia pedida, nem na canônica — e, se ``expected_identity`` vier, a
    mesma identidade que o resolvedor registrou. `PathIntegrityViolation` para o que não se
    consegue provar; `PathOperationFailed` para o que não existe ou não pôde ser lido.
    """
    text = str(path)
    if "\x00" in text or classify_path_form(text) is not PathForm.ABSOLUTE_QUALIFIED:
        raise _violation(IntegrityFailure.INTEGRITY_UNVERIFIABLE)
    if ".." in Path(text).parts:
        raise _violation(IntegrityFailure.INTEGRITY_UNVERIFIABLE)
    try:
        canonical = Path(text).resolve(strict=True)
        info = canonical.lstat()
    except FileNotFoundError:
        raise PathOperationFailed(PathFailure.NOT_FOUND) from None
    except (OSError, ValueError, RuntimeError):
        raise PathOperationFailed(PathFailure.IO_ERROR) from None
    if not stat.S_ISDIR(info.st_mode):
        raise PathOperationFailed(PathFailure.NOT_DIRECTORY)
    _require_clean_chain(Path(text))
    _require_clean_chain(canonical)
    identity = _trusted_identity(info)
    if expected_identity is not None:
        _require_same_identity(expected_identity, identity, IntegrityFailure.ROOT_IDENTITY_CHANGED)
    return BoundRoot(path=str(canonical), identity=identity)


def verify_root(root: BoundRoot) -> None:
    """Revalida a raiz vinculada: continua diretório, sem reparse na cadeia e com a identidade."""
    try:
        info = os.lstat(root.path)
    except FileNotFoundError:
        raise PathOperationFailed(PathFailure.NOT_FOUND) from None
    except (OSError, ValueError):
        raise PathOperationFailed(PathFailure.IO_ERROR) from None
    if _has_reparse(info):
        raise _violation(IntegrityFailure.REPARSE_DETECTED)
    if not stat.S_ISDIR(info.st_mode):
        raise _violation(IntegrityFailure.ROOT_IDENTITY_CHANGED)
    _require_same_identity(
        root.identity, _trusted_identity(info), IntegrityFailure.ROOT_IDENTITY_CHANGED
    )
    _require_clean_chain(Path(root.path))


def _same_text_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _target_parts(facts: PathFacts, root: BoundRoot) -> tuple[str, ...]:
    """Componentes do alvo relativos à raiz vinculada. Só **confere** coerência de fatos.

    A decisão de contenção já foi tomada por `safety.decide_path`; aqui só se recusa agir se
    os fatos recebidos não são desta raiz ou o alvo não está sob ela — o que significaria que
    o chamador pulou a decisão.
    """
    if facts.canonical_target is None:
        raise PathOperationFailed(PathFailure.NOT_FOUND)
    if not _same_text_path(facts.canonical_root, root.path):
        raise _violation(IntegrityFailure.INTEGRITY_UNVERIFIABLE)
    try:
        relative = Path(facts.canonical_target).relative_to(Path(root.path))
    except ValueError:
        raise _violation(IntegrityFailure.TARGET_OUTSIDE_ROOT) from None
    return relative.parts


def relative_target(facts: PathFacts, root: BoundRoot) -> str:
    """O alvo relativo à raiz vinculada: `/` como separador, **grafia canônica** observada.

    `""` é a própria raiz. Só deriva um fato (os componentes do `canonical_target` sob a raiz);
    não decide nada. É a chave estável de `files_read` e dos caminhos que o executor mostra ao
    provider — nunca um caminho absoluto.
    """
    return "/".join(_target_parts(facts, root))


def _open_flags(base: int) -> int:
    flags = base
    for extra in ("O_NOFOLLOW", "O_BINARY", "O_CLOEXEC", "O_NONBLOCK"):
        flags |= getattr(os, extra, 0)
    return flags


def _verify_dirs(root: BoundRoot, seen: dict[tuple[str, ...], ObjectIdentity]) -> None:
    """Cada diretório já observado continua diretório, sem reparse e com a mesma identidade."""
    verify_root(root)
    for prefix, expected in seen.items():
        try:
            info = os.lstat(os.path.join(root.path, *prefix))
        except FileNotFoundError:
            raise PathOperationFailed(PathFailure.NOT_FOUND) from None
        except (OSError, ValueError):
            raise PathOperationFailed(PathFailure.IO_ERROR) from None
        if _has_reparse(info):
            raise _violation(IntegrityFailure.REPARSE_DETECTED)
        if not stat.S_ISDIR(info.st_mode):
            raise _violation(IntegrityFailure.PARENT_IDENTITY_CHANGED)
        _require_same_identity(
            expected, _trusted_identity(info), IntegrityFailure.PARENT_IDENTITY_CHANGED
        )


def _observe_dir(path: str) -> os.stat_result:
    """`lstat` de um diretório esperado. `FileNotFoundError` sobe: o chamador decide criar."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise
    except (OSError, ValueError):
        raise PathOperationFailed(PathFailure.IO_ERROR) from None
    if _has_reparse(info):
        raise _violation(IntegrityFailure.REPARSE_DETECTED)
    if not stat.S_ISDIR(info.st_mode):
        raise PathOperationFailed(PathFailure.NOT_DIRECTORY)
    return info


@dataclass(frozen=True, slots=True)
class OpenedFile:
    """Arquivo **existente** aberto por `open_existing`. `facts` já inclui a fase pós-abertura."""

    fd: int
    facts: PathFacts
    size: int
    writable: bool


@contextmanager
def open_existing(
    facts: PathFacts,
    root: BoundRoot,
    *,
    for_update: bool = False,
    readable: bool = False,
    is_cancelled: Callable[[], bool] | None = None,
) -> Iterator[OpenedFile]:
    """Abre um arquivo regular **existente**; a decisão pós-abertura é de quem chama.

    * **nunca** a flag de truncamento: abrir para atualizar (`O_WRONLY`) não destrói nada; o
      truncamento é uma chamada separada (`write_fd(truncate=True)`), só depois da
      autorização externa;
    * ``readable`` (E7.5-C) só vale com ``for_update``: abre `O_RDWR` para que quem chama possa
      **reler o conteúdo pelo mesmo handle** (`read_fd`) antes de truncar — o `ApplyPatch`
      compara o conteúdo atual com a linha de base do preflight. Sem ele, nada muda;
    * não segue symlink no componente final (`O_NOFOLLOW` onde existe; no Windows, `lstat`
      antes e identidade depois);
    * exige arquivo **regular** (diretório → `IS_DIRECTORY`; outro tipo → `NOT_REGULAR`);
    * devolve `facts` com `post_open_identity` — `safety.decide_post_open` decide;
    * recusa (`PathIntegrityViolation`) se a identidade não é verificável (`file_id == 0`).

    Quem chama precisa chamar `decide_post_open(opened.facts)` e só então ler/escrever.
    """
    _check_cancel(is_cancelled)
    verify_root(root)
    parts = _target_parts(facts, root)
    if not facts.exists or not parts:
        raise PathOperationFailed(PathFailure.NOT_FOUND)
    path = os.path.join(root.path, *parts)
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        raise PathOperationFailed(PathFailure.NOT_FOUND) from None
    except (OSError, ValueError):
        raise PathOperationFailed(PathFailure.IO_ERROR) from None
    if _has_reparse(before):
        raise _violation(IntegrityFailure.REPARSE_DETECTED)
    if stat.S_ISDIR(before.st_mode):
        raise PathOperationFailed(PathFailure.IS_DIRECTORY)
    if not stat.S_ISREG(before.st_mode):
        raise PathOperationFailed(PathFailure.NOT_REGULAR)

    if not for_update:
        access = os.O_RDONLY
    elif readable:
        access = os.O_RDWR
    else:
        access = os.O_WRONLY
    try:
        fd = os.open(path, _open_flags(access))
    except OSError as error:
        raise _classify_os_error(error) from None
    try:
        try:
            opened = os.fstat(fd)
        except OSError as error:
            raise _classify_os_error(error) from None
        if not stat.S_ISREG(opened.st_mode):
            raise PathOperationFailed(PathFailure.NOT_REGULAR)
        # Sem identidade que prove algo (o handle **ou** a base inspecionada com `file_id == 0`),
        # nenhuma decisão pós-abertura teria o que comparar: recusa o efeito.
        _trusted_identity(opened)
        if facts.target_identity is None or not facts.target_identity.is_verifiable:
            raise _violation(IntegrityFailure.INTEGRITY_UNVERIFIABLE)
        yield OpenedFile(
            fd=fd,
            facts=inspect_opened(facts, fd),
            size=opened.st_size,
            writable=for_update,
        )
    finally:
        os.close(fd)


_IO_CHUNK = 64 * 1024


def read_fd(fd: int, max_bytes: int, *, is_cancelled: Callable[[], bool] | None = None) -> bytes:
    """Lê do início até **`max_bytes + 1`** bytes (um a mais denuncia que passou do teto).

    O teto é decisão do chamador; aqui só se limita a memória. Cooperativa com cancelamento
    entre blocos.
    """
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        total = 0
        while total <= max_bytes:
            _check_cancel(is_cancelled)
            chunk = os.read(fd, min(_IO_CHUNK, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except OSError as error:
        raise _classify_os_error(error) from None
    return b"".join(chunks)


def digest_fd(
    fd: int,
    update: Callable[[bytes], None],
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> int:
    """Lê o handle **inteiro**, do início, em blocos, entregando cada bloco a ``update``.

    E7.5-D: para o Git mediado calcular o oid de um arquivo grande sem carregá-lo inteiro na
    memória. Só lê; quem chama decide o algoritmo e o que comparar. Devolve o total de bytes
    lidos (quem chama confere contra o tamanho esperado). Cooperativa com cancelamento entre
    blocos.
    """
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        total = 0
        while True:
            _check_cancel(is_cancelled)
            chunk = os.read(fd, _IO_CHUNK)
            if not chunk:
                return total
            update(chunk)
            total += len(chunk)
    except OSError as error:
        raise _classify_os_error(error) from None


def write_fd(
    fd: int,
    data: bytes,
    *,
    truncate: bool,
    is_cancelled: Callable[[], bool] | None = None,
) -> None:
    """Escreve ``data`` no handle. **Só** chamar depois da autorização externa.

    ``truncate=True`` é o caminho de *update*: `ftruncate(0)` + escrita desde o início.
    ``truncate=False`` é o da criação: o arquivo já nasceu vazio. O cancelamento é checado
    **antes** de truncar e entre blocos; cancelar no meio deixa o arquivo parcial (declarado).
    """
    _check_cancel(is_cancelled)
    try:
        if truncate:
            os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        view = memoryview(data)
        while view:
            _check_cancel(is_cancelled)
            written = os.write(fd, view[:_IO_CHUNK])
            view = view[written:]
        os.fsync(fd)
    except OSError as error:
        raise _classify_os_error(error) from None


class CreatedFile:
    """Arquivo regular **novo e vazio**, criado por `create_exclusive`.

    `facts` traz o que `safety.decide_post_create` precisa; `identity` é **sempre** verificável
    (sem identidade verificável `create_exclusive` levanta e não devolve nada). O arquivo está
    vazio: nenhum byte de conteúdo foi escrito antes da decisão. `close()` é idempotente;
    `discard_created` fecha e remove o vazio **só** se a identidade ainda for a criada.
    """

    __slots__ = ("_fd", "facts", "identity", "path")

    def __init__(
        self, fd: int, facts: PathFacts, identity: ObjectIdentity | None, path: str
    ) -> None:
        self._fd: int | None = fd
        self.facts = facts
        self.identity = identity
        self.path = path

    @property
    def fd(self) -> int:
        if self._fd is None:
            raise PathOperationFailed(PathFailure.IO_ERROR)
        return self._fd

    def close(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            with contextlib.suppress(OSError):
                os.close(fd)

    def __enter__(self) -> CreatedFile:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


@dataclass(slots=True)
class CreateMutationTrace:
    """O que `create_exclusive` **de fato mudou** no filesystem numa tentativa (E7.5-C).

    Preenchido **imediatamente** depois de cada syscall bem-sucedida — não no retorno —, então
    continua valendo quando a primitiva levanta depois (cancelamento, E/S, integridade):

    * ``created_parent_count`` — diretórios que **esta** tentativa criou (`os.mkdir` bem-sucedido;
      um diretório que outro processo criou no meio não conta). Nunca são removidos depois;
    * ``created_target`` — o `O_CREAT|O_EXCL` criou o arquivo-alvo (vazio). Um `O_EXCL` recusado
      porque o alvo já existia não conta.

    Só observabilidade: não carrega caminho, não decide política, não sabe quem chamou.
    """

    created_parent_count: int = 0
    created_target: bool = False


def create_exclusive(
    facts: PathFacts,
    root: BoundRoot,
    *,
    create_parents: bool = True,
    is_cancelled: Callable[[], bool] | None = None,
    mutation_trace: CreateMutationTrace | None = None,
) -> CreatedFile:
    """Cria um arquivo regular **novo** (`O_CREAT|O_EXCL`), com a cadeia de pais revalidada.

    * pais existentes: cada um precisa continuar diretório, sem reparse, com a identidade
      observada; o pai final confere com `facts.parent_identity`, se havia;
    * pais ausentes: criados **um nível por vez** (`os.mkdir`, nunca `makedirs`), revalidando
      toda a cadeia antes de cada `mkdir`;
    * alvo final: `O_EXCL` — nunca sobrescreve nem segue link. Um arquivo que **aparece**
      entre a inspeção e a criação vira `ALREADY_EXISTS`, nunca sobrescrita;
    * **nenhum byte** é escrito. Devolve o handle vazio e `facts` pós-criação; quem chama passa
      por `safety.decide_post_create` e só então usa `write_fd`;
    * se o objeto criado não tem identidade **verificável** (`file_id == 0`, ou `fstat`/`lstat`
      falham logo após o `O_EXCL`): fecha o handle e levanta
      `PathIntegrityViolation(INTEGRITY_UNVERIFIABLE)`. O arquivo vazio permanece; não há
      `CreatedFile` e nenhuma remoção é tentada.

    ``facts`` precisa vir de `inspect(..., tolerate_absent_tail=True)`: sem isso a cadeia
    léxica de um alvo que ainda não existe é `UNKNOWN` e `decide_path` nunca a autoriza.

    Um diretório criado fica, mesmo que a decisão posterior negue (como em `CheckedTreeWriter`).

    ``mutation_trace`` (E7.5-C, opcional): registra cada `mkdir` e o `O_EXCL` bem-sucedidos no
    instante em que acontecem, para que quem chama saiba o que mudou mesmo quando esta função
    levanta depois. Sem ele, nada muda.

    **Pai que existia e precisou ser criado** (Linux-CI-003): se a inspeção viu o pai final
    (`facts.parent_identity` presente) e **esta** tentativa ainda assim precisou de um `mkdir`
    bem-sucedido em algum nível da cadeia, a cadeia observada não existe mais —
    `PARENT_IDENTITY_CHANGED` antes do `O_EXCL`, **independentemente** de `(dev, ino)`: o
    POSIX pode reutilizar o inode de um diretório apagado e recriado, e a comparação de
    identidade sozinha passaria. Os diretórios recriados ficam (e o `mutation_trace` os
    conta); o alvo não é criado. Criar pais que a inspeção viu **ausentes** é o caso normal.
    """
    _check_cancel(is_cancelled)
    verify_root(root)
    parts = _target_parts(facts, root)
    if facts.exists or not parts:
        raise PathOperationFailed(PathFailure.ALREADY_EXISTS)

    parents = parts[:-1]
    seen: dict[tuple[str, ...], ObjectIdentity] = {}
    created_here = 0  # `mkdir` bem-sucedidos **desta** tentativa (o trace é opcional)
    for depth in range(1, len(parents) + 1):
        _check_cancel(is_cancelled)
        prefix = parents[:depth]
        _verify_dirs(root, seen)
        directory = os.path.join(root.path, *prefix)
        try:
            info = _observe_dir(directory)
        except FileNotFoundError:
            if not create_parents:
                raise PathOperationFailed(PathFailure.NOT_FOUND) from None
            try:
                os.mkdir(directory)
            except FileExistsError:
                pass  # criado por outro; a releitura abaixo o valida do mesmo jeito
            except OSError as error:
                raise _classify_os_error(error) from None
            else:
                created_here += 1
                if mutation_trace is not None:
                    mutation_trace.created_parent_count += 1
            try:
                info = _observe_dir(directory)
            except FileNotFoundError:
                raise PathOperationFailed(PathFailure.NOT_FOUND) from None
        seen[prefix] = _trusted_identity(info)

    _verify_dirs(root, seen)
    if facts.parent_identity is not None and created_here:
        # O pai final existia na inspeção; ter precisado criá-lo (ou um ancestral) prova a troca.
        raise _violation(IntegrityFailure.PARENT_IDENTITY_CHANGED)
    parent_identity = seen[parents] if parents else root.identity
    baseline = facts.parent_identity if facts.parent_identity is not None else parent_identity
    _require_same_identity(baseline, parent_identity, IntegrityFailure.PARENT_IDENTITY_CHANGED)

    _check_cancel(is_cancelled)
    path = os.path.join(root.path, *parts)
    try:
        fd = os.open(path, _open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL), 0o666)
    except OSError as error:
        raise _classify_os_error(error) from None
    if mutation_trace is not None:
        mutation_trace.created_target = True

    base_facts = replace(facts, exists=True, parent_identity=baseline)
    parent_path = os.path.join(root.path, *parents) if parents else root.path
    # O arquivo **já existe**, vazio. Se o objeto criado não tem identidade que o prove — ou se
    # ela nem pôde ser relida — não há `CreatedFile` parcialmente válido para devolver: o handle
    # é fechado, **nenhum byte** foi escrito e **nada é removido** (sem identidade verificável não
    # se prova que o que está no caminho é o que criamos; o vazio fica, risco residual declarado).
    # É uma violação de integridade, não um erro técnico genérico.
    try:
        try:
            opened = os.fstat(fd)
            path_info = os.lstat(path)
            parent_info = os.lstat(parent_path)
            created_identity = _trusted_identity(opened)
            path_identity = _trusted_identity(path_info)
        except (OSError, ValueError):
            raise _violation(IntegrityFailure.INTEGRITY_UNVERIFIABLE) from None
    except PathIntegrityViolation:
        with contextlib.suppress(OSError):
            os.close(fd)
        raise
    parent_identity_now = _identity(parent_info)
    parent_ok = (
        stat.S_ISDIR(parent_info.st_mode)
        and not _has_reparse(parent_info)
        and parent_identity_now.is_verifiable
    )
    post = replace(
        base_facts,
        post_open_identity=created_identity,
        post_create_path_identity=path_identity,
        post_create_parent_identity=parent_identity_now if parent_ok else None,
        post_create_regular=Tri.of(stat.S_ISREG(opened.st_mode)),
        post_create_reparse=Tri.of(_has_reparse(path_info)),
    )
    return CreatedFile(fd, post, created_identity, path)


def probe_create(facts: PathFacts, root: BoundRoot) -> None:
    """**Somente leitura**: `create_exclusive` encontraria agora um caminho criável? (E7.5-C)

    Existe para o *preflight* do `ApplyPatch`, que precisa descobrir **antes do primeiro efeito**
    tudo o que já se sabe que falharia. Observa, sem criar nada:

    * a raiz vinculada (`verify_root`);
    * cada ancestral **existente** do alvo: precisa ser diretório (`NOT_DIRECTORY`) e sem
      link/reparse (`REPARSE_DETECTED`); o primeiro ancestral ausente encerra a sonda — dali
      para baixo `create_exclusive` criaria os diretórios;
    * o alvo: se já existe, `ALREADY_EXISTS`.

    Não cria, não abre para escrita, não remove. Não substitui as revalidações de
    `create_exclusive` (a janela entre a sonda e a criação continua existindo — `O_EXCL` e a
    decisão pós-criação seguem obrigatórios).
    """
    verify_root(root)
    parts = _target_parts(facts, root)
    if facts.exists or not parts:
        raise PathOperationFailed(PathFailure.ALREADY_EXISTS)
    for depth in range(1, len(parts)):
        try:
            _observe_dir(os.path.join(root.path, *parts[:depth]))
        except FileNotFoundError:
            return
    try:
        os.lstat(os.path.join(root.path, *parts))
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        raise PathOperationFailed(PathFailure.IO_ERROR) from None
    raise PathOperationFailed(PathFailure.ALREADY_EXISTS)


def discard_created(created: CreatedFile) -> bool:
    """Remove o arquivo vazio criado por `create_exclusive` — **somente** se ainda for o mesmo.

    Fecha o handle primeiro (no Windows um arquivo aberto não pode ser removido). Remove se, e
    só se, o caminho ainda é arquivo regular, sem link, vazio e com **exatamente** a identidade
    criada. Qualquer outra coisa (trocado, preenchido, sumiu) é deixada em paz: devolve
    `False`. Nunca remove diretório.
    """
    created.close()
    if created.identity is None or not created.identity.is_verifiable:
        return False
    try:
        info = os.lstat(created.path)
        if (
            not stat.S_ISREG(info.st_mode)
            or _has_reparse(info)
            or info.st_size != 0
            or not _same_identity(_identity(info), created.identity)
        ):
            return False
        os.unlink(created.path)
    except (OSError, ValueError):
        return False
    return True


def delete_if_identity(
    facts: PathFacts,
    root: BoundRoot,
    *,
    expected_identity: ObjectIdentity | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> None:
    """Remove **um arquivo regular** — e só se a identidade observada for a esperada.

    A identidade esperada é, por padrão, a de `facts.target_identity`. A cadeia dos pais é
    revalidada (diretório, sem reparse, pai igual ao inspecionado). Nunca remove diretório
    (`IS_DIRECTORY`), link (`REPARSE_DETECTED`) nem arquivo trocado (`TARGET_IDENTITY_CHANGED`);
    identidade não verificável é `INTEGRITY_UNVERIFIABLE`.
    """
    _check_cancel(is_cancelled)
    verify_root(root)
    parts = _target_parts(facts, root)
    expected = expected_identity if expected_identity is not None else facts.target_identity
    if not facts.exists or not parts or expected is None:
        raise PathOperationFailed(PathFailure.NOT_FOUND)
    if not expected.is_verifiable:
        raise _violation(IntegrityFailure.INTEGRITY_UNVERIFIABLE)

    parents = parts[:-1]
    seen: dict[tuple[str, ...], ObjectIdentity] = {}
    for depth in range(1, len(parents) + 1):
        prefix = parents[:depth]
        try:
            seen[prefix] = _trusted_identity(_observe_dir(os.path.join(root.path, *prefix)))
        except FileNotFoundError:
            raise PathOperationFailed(PathFailure.NOT_FOUND) from None
    _verify_dirs(root, seen)
    if facts.parent_identity is not None:
        parent_identity = seen[parents] if parents else root.identity
        _require_same_identity(
            facts.parent_identity, parent_identity, IntegrityFailure.PARENT_IDENTITY_CHANGED
        )

    path = os.path.join(root.path, *parts)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise PathOperationFailed(PathFailure.NOT_FOUND) from None
    except (OSError, ValueError):
        raise PathOperationFailed(PathFailure.IO_ERROR) from None
    if _has_reparse(info):
        raise _violation(IntegrityFailure.REPARSE_DETECTED)
    if stat.S_ISDIR(info.st_mode):
        raise PathOperationFailed(PathFailure.IS_DIRECTORY)
    if not stat.S_ISREG(info.st_mode):
        raise PathOperationFailed(PathFailure.NOT_REGULAR)
    _require_same_identity(
        expected, _trusted_identity(info), IntegrityFailure.TARGET_IDENTITY_CHANGED
    )
    _check_cancel(is_cancelled)
    try:
        os.unlink(path)
    except OSError as error:
        raise _classify_os_error(error) from None


class EntryKind(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class DirectoryEntryFacts:
    """Fatos de **uma** entrada de diretório. Nenhuma decisão embutida.

    O chamador decide o que mostrar combinando `name` com o caminho do diretório (segredo,
    `.git`) e com os três fatos de link: `UNKNOWN` significa que não se pôde verificar, e quem
    decide fecha. O link nunca é seguido.
    """

    name: str
    kind: EntryKind
    is_symlink: Tri
    is_junction: Tri
    is_reparse_point: Tri
    #: **Sempre** verificável: uma entrada cuja identidade não se estabelece aborta a enumeração
    #: (`PathIntegrityViolation`, ou `PathOperationFailed(INSPECTION_FAILED)` se a leitura falhou
    #: tecnicamente) em vez de aparecer como entrada normal sem baseline.
    identity: ObjectIdentity
    size: int | None


@dataclass(frozen=True, slots=True)
class DirectoryListing:
    """A enumeração **completa** e válida de um diretório, em ordem determinística.

    Ordenada pelos bytes UTF-8 do nome (`_entry_sort_key`) — nunca pela ordem do `os.scandir`,
    que não é contrato. Não há limite aqui: 1000 entradas é limite de **ferramenta**
    (`TOOL_LIMITS_V1`) e o `ToolExecutor` o aplica **depois** desta ordenação.
    """

    entries: tuple[DirectoryEntryFacts, ...]


def _entry_sort_key(name: str) -> bytes:
    """A chave de ordenação: os bytes UTF-8 do nome. Sem locale, sem caixa, sem collation do SO.

    `surrogatepass` para que um nome com surrogate solto (Windows) ou byte não decodificável
    (POSIX, `surrogateescape`) tenha chave **determinística** em vez de levantar.
    """
    return name.encode("utf-8", "surrogatepass")


def _entry_facts(directory: str, name: str) -> DirectoryEntryFacts:
    """Fatos de uma entrada, de **um único** `lstat`. Nunca devolve entrada "normal" sem baseline.

    Classificação da leitura (B-AUD-005):

    * a entrada **sumiu** entre o `scandir` e o `lstat` (`FileNotFoundError`, ou
      `NotADirectoryError`: o diretório enumerado deixou de ser diretório) → corrida observada:
      `INTEGRITY_UNVERIFIABLE` (contrato congelado da P2-001);
    * E/S, permissão, `OSError` ambíguo → falha **técnica** de coleta:
      `PathOperationFailed(INSPECTION_FAILED)` — não é integridade nem política;
    * leitura bem-sucedida com `file_id == 0` → `INTEGRITY_UNVERIFIABLE` (`_trusted_identity`).

    Os fatos de link derivam do **mesmo** `lstat` (`_link_facts_of`): não há segunda leitura.
    """
    path = os.path.join(directory, name)
    try:
        info = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        raise _violation(IntegrityFailure.INTEGRITY_UNVERIFIABLE) from None
    except (OSError, ValueError):
        raise PathOperationFailed(PathFailure.INSPECTION_FAILED) from None
    identity = _trusted_identity(info)
    is_symlink, is_junction, is_reparse = _link_facts_of(info)
    if stat.S_ISDIR(info.st_mode):
        kind = EntryKind.DIRECTORY
    elif stat.S_ISREG(info.st_mode):
        kind = EntryKind.FILE
    else:
        kind = EntryKind.OTHER
    return DirectoryEntryFacts(
        name,
        kind,
        is_symlink,
        is_junction,
        is_reparse,
        identity,
        info.st_size if kind is EntryKind.FILE else None,
    )


def _recheck_listed_directory(directory: str, baseline: ObjectIdentity) -> None:
    """O diretório enumerado ainda é o do baseline? Antes de devolver qualquer listagem.

    Sem isto, uma listagem de um diretório que foi trocado (ou virou link, ou deixou de ter
    identidade provável) **durante** a enumeração seria aceita como se descrevesse o diretório
    verificado no início.

    Sumiu (`FileNotFoundError`/`NotADirectoryError`) é troca observada; E/S, permissão ou `OSError`
    ambíguo na releitura é falha **técnica** (`INSPECTION_FAILED`), não integridade.
    """
    try:
        after = os.lstat(directory)
    except (FileNotFoundError, NotADirectoryError):
        raise _violation(IntegrityFailure.TARGET_IDENTITY_CHANGED) from None
    except (OSError, ValueError):
        raise PathOperationFailed(PathFailure.INSPECTION_FAILED) from None
    if _has_reparse(after):
        raise _violation(IntegrityFailure.REPARSE_DETECTED)
    if not stat.S_ISDIR(after.st_mode):
        raise _violation(IntegrityFailure.TARGET_IDENTITY_CHANGED)
    _require_same_identity(
        baseline, _trusted_identity(after), IntegrityFailure.TARGET_IDENTITY_CHANGED
    )


def list_directory(
    facts: PathFacts,
    root: BoundRoot,
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> DirectoryListing:
    """Enumera um diretório **sem seguir link/reparse** e devolve fatos por entrada.

    Ordem lógica: enumerar **tudo** → coletar os fatos de **todas** as entradas → ordenar por
    bytes UTF-8 → devolver. Nunca "limitar e depois ordenar" (o `os.scandir` não tem ordem de
    contrato), e nenhuma entrada sem identidade verificável aparece: a enumeração inteira aborta
    com `PathIntegrityViolation(INTEGRITY_UNVERIFIABLE)`. O limite de entradas é do
    `ToolExecutor` (E7.5-B), aplicado sobre esta lista já ordenada.

    ``facts`` é de `inspect` sobre o diretório (a raiz inclusive: `inspect(".", root)` é uso
    **interno** — nenhuma string de `ToolRequest` vira atalho de raiz). A listagem só é devolvida
    com:

    * **baseline verificável do próprio diretório** (`facts.target_identity`): ausente ou com
      `file_id == 0` é `INTEGRITY_UNVERIFIABLE` **antes** de qualquer `os.scandir`;
    * **entradas verificáveis** (uma só que não seja aborta a enumeração);
    * o diretório **ainda correspondendo ao baseline ao concluir**: depois de enumerar e
      coletar, a identidade é relida (`TARGET_IDENTITY_CHANGED` se trocou,
      `REPARSE_DETECTED` se virou link, `INTEGRITY_UNVERIFIABLE` se deixou de ser provável).

    Falha **técnica** de leitura (E/S, permissão, `OSError` ambíguo) no `lstat` de uma entrada ou
    na releitura do diretório é `PathOperationFailed(INSPECTION_FAILED)` — nunca integridade.

    Não decide quais entradas aparecem.
    """
    _check_cancel(is_cancelled)
    verify_root(root)
    parts = _target_parts(facts, root)
    if not facts.exists:
        raise PathOperationFailed(PathFailure.NOT_FOUND)
    baseline = facts.target_identity
    if baseline is None or not baseline.is_verifiable:
        raise _violation(IntegrityFailure.INTEGRITY_UNVERIFIABLE)
    directory = os.path.join(root.path, *parts) if parts else root.path
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        raise PathOperationFailed(PathFailure.NOT_FOUND) from None
    except (OSError, ValueError):
        raise PathOperationFailed(PathFailure.IO_ERROR) from None
    if _has_reparse(info):
        raise _violation(IntegrityFailure.REPARSE_DETECTED)
    if not stat.S_ISDIR(info.st_mode):
        raise PathOperationFailed(PathFailure.NOT_DIRECTORY)
    _require_same_identity(
        baseline, _trusted_identity(info), IntegrityFailure.TARGET_IDENTITY_CHANGED
    )

    names: list[str] = []
    try:
        with os.scandir(directory) as scanner:
            for entry in scanner:
                _check_cancel(is_cancelled)
                names.append(entry.name)
    except OSError as error:
        raise _classify_os_error(error) from None
    collected: list[DirectoryEntryFacts] = []
    for name in names:
        _check_cancel(is_cancelled)
        collected.append(_entry_facts(directory, name))
    collected.sort(key=lambda entry: _entry_sort_key(entry.name))
    _recheck_listed_directory(directory, baseline)
    return DirectoryListing(entries=tuple(collected))
