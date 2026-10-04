"""E7.5-A — regressões dos três P2 da auditoria.

* **P2-001** — `list_directory` não pode emitir como entrada normal um objeto cuja identidade não
  se consegue estabelecer: a enumeração aborta com `PathIntegrityViolation`.
* **P2-002** — `create_exclusive` não devolve `CreatedFile` quando o objeto recém-criado não tem
  identidade verificável: levanta `INTEGRITY_UNVERIFIABLE`, sem escrever byte e sem `unlink`.
* **P2-003** — a ordem de `list_directory` é determinística e **independente** da ordem do
  `os.scandir` (enumerar → coletar → ordenar; o limite é do `ToolExecutor`, depois de ordenar).

Cada teste foi escrito para **falhar** contra o código anterior à correção (contrafactual) e só
usa fixtures benignas num diretório temporário, mocks de `os.scandir`/`os.lstat` e um
`_identity` que devolve `file_id == 0`. Nada de git, processo ou helper.
"""

from __future__ import annotations

import dataclasses
import errno
import inspect as std_inspect
import itertools
import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from app import path_runtime
from app.path_runtime import (
    BoundRoot,
    IntegrityFailure,
    PathIntegrityViolation,
    bind_root,
    create_exclusive,
    discard_created,
    inspect,
    list_directory,
    write_fd,
)
from app.safety import (
    ObjectIdentity,
    SafetyPolicy,
    prevalidate_mediated_path,
)
from app.safety.capability_profile import DEVELOPER_V1_PROFILE
from app.safety.paths import PathIntent
from app.safety.types import PathFacts
from app.tool_executor.contracts import (
    DecisionCategory,
    RunScope,
    ToolStatus,
    WriteFile,
)
from app.tool_executor.executor import HandlerContext, LocalWorktreeToolExecutor, Outcome
from app.tool_executor.journal import InMemoryDecisionJournal
from app.tool_executor.reasons import reason_for
from app.tool_executor.workspace import ResolvedWorkspace

COMMIT = "d" * 40


@pytest.fixture
def root(tmp_path: Path) -> BoundRoot:
    directory = tmp_path / "ws"
    directory.mkdir()
    return bind_root(directory)


def base(root: BoundRoot) -> Path:
    return Path(root.path)


def zero_ids(monkeypatch: pytest.MonkeyPatch, *, keep: set[int] | None = None) -> None:
    """A plataforma passa a devolver `file_id == 0` — exceto nos inodes em ``keep``."""
    real = path_runtime._identity
    keep = keep or set()

    def fake(info: os.stat_result) -> ObjectIdentity:
        identity = real(info)
        return identity if info.st_ino in keep else ObjectIdentity(identity.volume_id, 0)

    monkeypatch.setattr(path_runtime, "_identity", fake)


def authorized(relative: str, root: BoundRoot) -> PathFacts:
    assert prevalidate_mediated_path(relative).allow
    facts = inspect(relative, root.path, tolerate_absent_tail=True)
    from app.safety import decide_path

    assert decide_path(facts, intent=PathIntent.WRITE).allow
    return facts


def listing_names(root: BoundRoot) -> list[str]:
    listing = list_directory(inspect(".", root.path), root)
    return [entry.name for entry in listing.entries]


# ===================================================================== P2-001


def test_p2_001_entrada_normal_com_identidade_verificavel_lista_normalmente(
    root: BoundRoot,
) -> None:
    (base(root) / "a.txt").write_bytes(b"x")
    (base(root) / "sub").mkdir()
    listing = list_directory(inspect(".", root.path), root)
    assert [e.name for e in listing.entries] == ["a.txt", "sub"]
    for entry in listing.entries:
        assert entry.identity is not None and entry.identity.is_verifiable


def test_p2_001_entrada_com_file_id_zero_aborta_a_enumeracao(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    (base(root) / "a.txt").write_bytes(b"x")
    zero_ids(monkeypatch, keep={root.identity.file_id})
    with pytest.raises(PathIntegrityViolation) as caught:
        list_directory(inspect(".", root.path), root)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE


@pytest.mark.parametrize(
    ("error", "technical"),
    [
        # B-AUD-005: falha **técnica** de coleta não é integridade — mas continua abortando
        (PermissionError(errno.EACCES, "negado"), True),
        (OSError(errno.EIO, "erro de E/S"), True),
        (OSError(errno.EINVAL, "ambíguo"), True),
        # a entrada **sumiu** entre o scandir e o lstat: integridade (contrato congelado)
        (FileNotFoundError(errno.ENOENT, "sumiu entre o scandir e o lstat"), False),
    ],
    ids=["permissao", "io", "oserror", "sumiu"],
)
def test_p2_001_falha_de_lstat_que_impede_a_identidade_nunca_vira_entrada_normal(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch, error: OSError, technical: bool
) -> None:
    (base(root) / "a.txt").write_bytes(b"x")
    (base(root) / "b.txt").write_bytes(b"x")
    real_lstat = os.lstat

    def fake(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if Path(path).name == "b.txt":
            raise error
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", fake)
    expected: type[Exception] = (
        path_runtime.PathOperationFailed if technical else PathIntegrityViolation
    )
    with pytest.raises(expected) as caught:
        list_directory(inspect(".", root.path), root)
    if technical:
        assert caught.value.category is path_runtime.PathFailure.INSPECTION_FAILED  # type: ignore[attr-defined]
    else:
        assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE  # type: ignore[attr-defined]


def test_p2_001_uma_valida_e_uma_nao_verificavel_nao_devolve_listagem_aparentemente_completa(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    (base(root) / "boa.txt").write_bytes(b"x")
    (base(root) / "ruim.txt").write_bytes(b"x")
    zero_ids(monkeypatch, keep={root.identity.file_id, os.stat(base(root) / "boa.txt").st_ino})
    with pytest.raises(PathIntegrityViolation) as caught:
        list_directory(inspect(".", root.path), root)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE


def test_p2_001_a_excecao_nao_vaza_caminho_absoluto(
    root: BoundRoot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (base(root) / "a.txt").write_bytes(b"x")
    zero_ids(monkeypatch, keep={root.identity.file_id})
    with pytest.raises(PathIntegrityViolation) as caught:
        list_directory(inspect(".", root.path), root)
    text = str(caught.value) + repr(caught.value) + repr(caught.value.args)
    assert str(tmp_path) not in text and root.path not in text and "a.txt" not in text
    assert str(caught.value) == IntegrityFailure.INTEGRITY_UNVERIFIABLE.value


def test_p2_001_o_tipo_da_entrada_nao_admite_identidade_ausente() -> None:
    """Não existe o terceiro estado silencioso: `identity` é sempre um `ObjectIdentity`."""
    anotacao = path_runtime.DirectoryEntryFacts.__annotations__["identity"]
    assert "None" not in str(anotacao)


# ===================================================================== P2-002


class SpyOs:
    """Registra `os.open`/`os.close`/`os.unlink`/`os.write` do `path_runtime`, delegando."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.opened: list[int] = []
        self.closed: list[int] = []
        self.unlinked: list[str] = []
        self.written: list[bytes] = []
        real_open, real_close = os.open, os.close
        real_unlink, real_write = os.unlink, os.write

        def spy_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            fd = real_open(path, flags, *args, **kwargs)
            self.opened.append(fd)
            return fd

        def spy_close(fd: int) -> None:
            self.closed.append(fd)
            real_close(fd)

        def spy_unlink(path: Any, *args: Any, **kwargs: Any) -> None:
            self.unlinked.append(str(path))
            real_unlink(path, *args, **kwargs)

        def spy_write(fd: int, data: Any) -> int:
            self.written.append(bytes(data))
            return real_write(fd, data)

        monkeypatch.setattr(os, "open", spy_open)
        monkeypatch.setattr(os, "close", spy_close)
        monkeypatch.setattr(os, "unlink", spy_unlink)
        monkeypatch.setattr(os, "write", spy_write)


def test_p2_002_create_normal_devolve_created_file_valido(root: BoundRoot) -> None:
    facts = authorized("novo.txt", root)
    with create_exclusive(facts, root) as created:
        assert created.identity is not None and created.identity.is_verifiable
        assert created.facts.post_open_identity == created.identity
        assert (base(root) / "novo.txt").stat().st_size == 0


def test_p2_002_identidade_pos_create_zero_levanta_integrity_unverifiable(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = authorized("novo.txt", root)
    zero_ids(monkeypatch, keep={root.identity.file_id})
    with pytest.raises(PathIntegrityViolation) as caught:
        create_exclusive(facts, root)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE
    assert str(caught.value) == "integrity_unverifiable"


def test_p2_002_nenhum_byte_escrito_arquivo_vazio_fica_e_nada_e_removido(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = authorized("novo.txt", root)
    zero_ids(monkeypatch, keep={root.identity.file_id})
    spy = SpyOs(monkeypatch)
    with pytest.raises(PathIntegrityViolation):
        create_exclusive(facts, root)
    target = base(root) / "novo.txt"
    assert target.exists() and target.stat().st_size == 0  # o vazio permanece
    assert spy.written == []  # nenhum byte de conteúdo
    assert spy.unlinked == []  # nenhuma tentativa de remoção
    assert len(spy.opened) == 1 and spy.closed == spy.opened  # o handle foi fechado, uma vez


def test_p2_002_nao_ha_created_file_para_o_chamador_escrever_ou_descartar(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = authorized("novo.txt", root)
    zero_ids(monkeypatch, keep={root.identity.file_id})
    created = None
    with pytest.raises(PathIntegrityViolation):
        created = create_exclusive(facts, root)
    assert created is None
    assert (base(root) / "novo.txt").read_bytes() == b""


@pytest.mark.parametrize(
    "falha",
    [OSError(errno.EIO, "erro de E/S"), PermissionError(errno.EACCES, "negado")],
    ids=["io", "permissao"],
)
def test_p2_002_falha_de_releitura_pos_create_tambem_e_integridade_nao_created_file_parcial(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch, falha: OSError
) -> None:
    facts = authorized("novo.txt", root)
    real_fstat = os.fstat

    def fake(fd: int) -> os.stat_result:
        raise falha

    monkeypatch.setattr(os, "fstat", fake)
    spy = SpyOs(monkeypatch)
    with pytest.raises(PathIntegrityViolation) as caught:
        create_exclusive(facts, root)
    monkeypatch.setattr(os, "fstat", real_fstat)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE
    assert spy.unlinked == [] and spy.written == []
    assert spy.closed == spy.opened


def test_p2_002_discard_created_continua_so_removendo_com_identidade_provada(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    # identidade verificável e igual: remove
    created = create_exclusive(authorized("a.txt", root), root)
    assert discard_created(created) is True
    # identidade zero (forjada): nunca remove, nem que o arquivo seja o mesmo
    other = create_exclusive(authorized("b.txt", root), root)
    other.close()
    other.identity = ObjectIdentity(1, 0)
    assert discard_created(other) is False
    assert (base(root) / "b.txt").exists()
    # arquivo que já não está vazio: nunca remove
    full = create_exclusive(authorized("c.txt", root), root)
    write_fd(full.fd, b"conteudo", truncate=False)
    assert discard_created(full) is False
    assert (base(root) / "c.txt").read_bytes() == b"conteudo"


# --- o executor mapeia a violação do fluxo concreto de criação


def _executor(
    ws: Path, journal: Any, write_flow: Callable[[HandlerContext, Any], Outcome]
) -> LocalWorktreeToolExecutor:
    resolved = ResolvedWorkspace(
        workspace_id="wt-1",
        task_id="t1",
        run_id="r1",
        base_commit=COMMIT,
        workspace_path=str(ws),
        root_identity=bind_root(ws).identity,
    )

    class Token:
        def is_cancelled(self) -> bool:
            return False

    executor = LocalWorktreeToolExecutor(
        workspace=resolved,
        root=bind_root(ws),
        policy=SafetyPolicy(),
        profile=DEVELOPER_V1_PROFILE,
        run_scope=RunScope("t1", "r1", "i1"),
        cancel_token=Token(),
        journal=journal,
    )
    return executor


def _create_flow(context: HandlerContext, request: Any) -> Outcome:
    """O corpo mínimo do que a E7.5-C fará: criar com exclusividade (primitiva real)."""
    facts = inspect(request.path, context.root.path, tolerate_absent_tail=True)
    with create_exclusive(facts, context.root):
        return Outcome(ToolStatus.OK)  # pragma: no cover — a primitiva levanta antes


def test_p2_002_o_executor_mapeia_para_denied_integrity_unverifiable_com_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    journal = InMemoryDecisionJournal()
    executor = _executor(ws, journal, _create_flow)
    monkeypatch.setattr(
        LocalWorktreeToolExecutor,
        "_write_file",
        lambda self, context, request: _create_flow(context, request),
    )
    zero_ids(monkeypatch, keep={os.stat(ws).st_ino})
    result = executor.execute(WriteFile("novo.txt", "x"))
    assert result.status is ToolStatus.DENIED
    assert result.reason == reason_for("path.integrity_unverifiable")
    (record,) = journal.records()
    assert record.rule_id == "path.integrity_unverifiable"
    assert record.category is DecisionCategory.TOCTOU_RECHECK_FAILED
    assert record.operation == "WriteFile" and record.subject_redacted == "novo.txt"
    assert str(tmp_path) not in record.reason + record.subject_redacted
    assert (ws / "novo.txt").exists() and (ws / "novo.txt").stat().st_size == 0
    assert (executor.usage().operations, executor.usage().denials) == (1, 1)


def test_p2_002_journal_que_falha_nesse_fluxo_e_error_e_poison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingJournal:
        calls = 0

        def append(self, record: Any) -> None:
            FailingJournal.calls += 1
            raise OSError("trilha indisponível")

    ws = tmp_path / "ws"
    ws.mkdir()
    executor = _executor(ws, FailingJournal(), _create_flow)
    monkeypatch.setattr(
        LocalWorktreeToolExecutor,
        "_write_file",
        lambda self, context, request: _create_flow(context, request),
    )
    zero_ids(monkeypatch, keep={os.stat(ws).st_ino})
    first = executor.execute(WriteFile("novo.txt", "x"))
    assert first.status is ToolStatus.ERROR
    assert first.reason is not None and first.reason.startswith("journal_unavailable")
    assert executor.usage().denials == 0
    later = executor.execute(WriteFile("outro.txt", "x"))
    assert later.status is ToolStatus.ERROR
    assert later.reason is not None and later.reason.startswith("journal_unavailable")
    assert FailingJournal.calls == 1
    assert not (ws / "outro.txt").exists()  # envenenado: nem chegou ao fluxo


# ===================================================================== P2-003


class ScandirOrder:
    """Faz o `os.scandir` devolver **as mesmas entradas** numa ordem escolhida pelo teste."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.order: Callable[[list[Any]], list[Any]] = lambda entries: entries
        real = os.scandir
        outer = self

        class _Scanner:
            def __init__(self, entries: list[Any], inner: Any) -> None:
                self._entries = entries
                self._inner = inner

            def __enter__(self) -> Iterator[Any]:
                return iter(self._entries)

            def __exit__(self, *exc: object) -> None:
                self._inner.close()

        def fake(path: Any = ".") -> _Scanner:
            inner = real(path)
            entries = list(inner)
            return _Scanner(outer.order(entries), inner)

        monkeypatch.setattr(os, "scandir", fake)

    def by_names(self, names: list[str]) -> None:
        position = {name: i for i, name in enumerate(names)}
        self.order = lambda entries: sorted(entries, key=lambda e: position[e.name])


#: Sem pares que só diferem na caixa: em sistemas de arquivos case-insensitive (NTFS) eles
#: seriam o **mesmo** arquivo.
NOMES = ["c", "a", "B", "Z", "ab", "a b", "ação", "_x", "0", "ä", "é", "日本"]


def esperado(nomes: list[str]) -> list[str]:
    return sorted(nomes, key=lambda n: n.encode("utf-8"))


def test_p2_003_a_ordem_do_scandir_nao_muda_o_resultado(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    for nome in ("c", "a", "b"):
        (base(root) / nome).write_bytes(b"")
    scandir = ScandirOrder(monkeypatch)
    resultados = []
    for ordem in (["c", "a", "b"], ["b", "c", "a"], ["a", "b", "c"], ["b", "a", "c"]):
        scandir.by_names(ordem)
        resultados.append(listing_names(root))
    assert all(r == ["a", "b", "c"] for r in resultados)


def test_p2_003_o_subconjunto_das_primeiras_n_e_identico_para_qualquer_ordem(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    nomes = [f"f{i:03d}" for i in range(12)]
    for nome in nomes:
        (base(root) / nome).write_bytes(b"")
    scandir = ScandirOrder(monkeypatch)
    limite = 5  # o limite é aplicado **depois** de ordenar (pelo ToolExecutor, na E7.5-B)
    vistos = set()
    for ordem in itertools.islice(itertools.permutations(nomes[:4]), 6):
        resto = nomes[4:]
        scandir.by_names(list(ordem) + resto[::-1])
        vistos.add(tuple(listing_names(root)[:limite]))
    assert vistos == {tuple(sorted(nomes)[:limite])}


def test_p2_003_ordem_por_bytes_utf8_sem_locale_nem_caixa(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    for nome in NOMES:
        try:
            (base(root) / nome).write_bytes(b"")
        except OSError:
            pytest.skip("o sistema de arquivos não aceita todos os nomes do teste")
    scandir = ScandirOrder(monkeypatch)
    esperado_ = esperado(NOMES)
    for ordem in (NOMES, NOMES[::-1], sorted(NOMES, key=str.lower), sorted(NOMES, reverse=True)):
        scandir.by_names(ordem)
        assert listing_names(root) == esperado_
    # maiúsculas antes de minúsculas, ASCII antes de não-ASCII: é a ordem dos bytes
    assert esperado_.index("B") < esperado_.index("Z") < esperado_.index("a")
    assert esperado_.index("c") < esperado_.index("ä") < esperado_.index("é")
    assert esperado_.index("é") < esperado_.index("日本")


def test_p2_003_chave_de_ordenacao_e_a_dos_bytes_utf8_do_nome() -> None:
    from app.path_runtime import _entry_sort_key

    assert _entry_sort_key("é") == "é".encode()
    assert _entry_sort_key("a") < _entry_sort_key("é") < _entry_sort_key("日本")
    surrogate = "x\udcff"  # byte não decodificável (POSIX/surrogateescape) ou lone surrogate
    assert isinstance(_entry_sort_key(surrogate), bytes)  # não levanta; determinístico
    assert _entry_sort_key(surrogate) == _entry_sort_key(surrogate)


def test_p2_003_o_limite_nao_e_responsabilidade_do_path_runtime() -> None:
    parametros = set(std_inspect.signature(list_directory).parameters)
    assert "max_entries" not in parametros and "limit" not in parametros
    campos = {f.name for f in dataclasses.fields(path_runtime.DirectoryListing)}
    assert campos == {"entries"}


def test_p2_003_listagem_grande_e_devolvida_inteira_e_ordenada(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem limite aqui: a camada de ferramenta fatia depois (1000 = `TOOL_LIMITS_V1`)."""
    nomes = [f"n{i:04d}" for i in range(1100)]
    for nome in nomes:
        (base(root) / nome).write_bytes(b"")
    scandir = ScandirOrder(monkeypatch)
    scandir.order = lambda entries: entries[::-1]
    assert listing_names(root) == sorted(nomes)


def test_p2_003_cancelamento_durante_a_enumeracao_continua_funcionando(root: BoundRoot) -> None:
    (base(root) / "a").write_bytes(b"")
    with pytest.raises(path_runtime.PathOperationFailed) as caught:
        list_directory(inspect(".", root.path), root, is_cancelled=lambda: True)
    assert caught.value.category is path_runtime.PathFailure.CANCELLED


# ================================================== P2-001 (restante): baseline do DIRETÓRIO


class ScandirHook:
    """Observa o `os.scandir`: conta chamadas e executa um *callback* ao **fim** da enumeração
    (para simular o diretório sendo alterado entre a leitura e a devolução da listagem)."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, *, on_exit: Callable[[], None] | None = None
    ) -> None:
        self.calls = 0
        real = os.scandir
        outer = self

        class _Scanner:
            def __init__(self, inner: Any) -> None:
                self._inner = inner

            def __enter__(self) -> Iterator[Any]:
                return iter(list(self._inner))

            def __exit__(self, *exc: object) -> None:
                self._inner.close()
                if on_exit is not None:
                    on_exit()

        def fake(path: Any = ".") -> _Scanner:
            outer.calls += 1
            return _Scanner(real(path))

        monkeypatch.setattr(os, "scandir", fake)


def with_target(facts: PathFacts, identity: ObjectIdentity | None) -> PathFacts:
    return dataclasses.replace(facts, target_identity=identity)


def make_sub(root: BoundRoot, *names: str) -> PathFacts:
    sub = base(root) / "sub"
    sub.mkdir()
    for name in names:
        (sub / name).write_bytes(b"x")
    return inspect("sub", root.path)


if sys.platform == "win32":
    import _winapi

    def _link(link: Path, target: Path) -> bool:
        _winapi.CreateJunction(str(target), str(link))
        return True

else:

    def _link(link: Path, target: Path) -> bool:
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            return False
        return True


def test_p2_001_dir_a_diretorio_normal_com_baseline_verificavel_lista(root: BoundRoot) -> None:
    facts = make_sub(root, "a.txt", "b.txt")
    assert facts.target_identity is not None and facts.target_identity.is_verifiable
    listing = list_directory(facts, root)
    assert [e.name for e in listing.entries] == ["a.txt", "b.txt"]
    # a raiz também é um diretório válido
    assert list_directory(inspect(".", root.path), root).entries


def test_p2_001_dir_b_baseline_ausente_levanta_integrity_unverifiable(root: BoundRoot) -> None:
    facts = with_target(make_sub(root, "a.txt"), None)
    with pytest.raises(PathIntegrityViolation) as caught:
        list_directory(facts, root)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE


def test_p2_001_dir_c_baseline_com_file_id_zero_levanta_integrity_unverifiable(
    root: BoundRoot,
) -> None:
    real = make_sub(root, "a.txt")
    assert real.target_identity is not None
    zero = ObjectIdentity(real.target_identity.volume_id, 0)
    with pytest.raises(PathIntegrityViolation) as caught:
        list_directory(with_target(real, zero), root)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE


@pytest.mark.parametrize("baseline", ["none", "zero"])
def test_p2_001_dir_d_baseline_invalido_nao_chega_a_chamar_scandir(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch, baseline: str
) -> None:
    real = make_sub(root, "a.txt")
    assert real.target_identity is not None
    invalid = None if baseline == "none" else ObjectIdentity(real.target_identity.volume_id, 0)
    hook = ScandirHook(monkeypatch)
    with pytest.raises(PathIntegrityViolation):
        list_directory(with_target(real, invalid), root)
    assert hook.calls == 0  # nenhum os.scandir depois de detectar baseline inválido


def test_p2_001_dir_e_diretorio_que_desaparece_antes_da_enumeracao_falha_fechado(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = make_sub(root, "a.txt")
    (base(root) / "sub" / "a.txt").unlink()
    (base(root) / "sub").rmdir()
    hook = ScandirHook(monkeypatch)
    with pytest.raises(path_runtime.PathOperationFailed) as caught:
        list_directory(facts, root)
    assert caught.value.category is path_runtime.PathFailure.NOT_FOUND
    assert hook.calls == 0


def test_p2_001_dir_f_diretorio_substituido_apos_o_baseline_e_target_identity_changed(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = make_sub(root, "a.txt", "b.txt")

    def swap() -> None:
        # outro diretório real, **com os mesmos nomes**: as entradas continuam verificáveis,
        # só o diretório enumerado deixou de ser o do baseline
        (base(root) / "sub").rename(base(root) / "sub_antigo")
        (base(root) / "sub").mkdir()
        (base(root) / "sub" / "a.txt").write_bytes(b"y")
        (base(root) / "sub" / "b.txt").write_bytes(b"y")

    ScandirHook(monkeypatch, on_exit=swap)
    with pytest.raises(PathIntegrityViolation) as caught:
        list_directory(facts, root)
    assert caught.value.category is IntegrityFailure.TARGET_IDENTITY_CHANGED


def test_p2_001_dir_g_identidade_pos_enumeracao_nao_verificavel_levanta_integrity_unverifiable(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = make_sub(root, "a.txt")
    keep = {root.identity.file_id, os.stat(base(root) / "sub" / "a.txt").st_ino}
    # só o diretório enumerado passa a ter `file_id == 0`, **depois** da enumeração
    ScandirHook(monkeypatch, on_exit=lambda: zero_ids(monkeypatch, keep=keep))
    with pytest.raises(PathIntegrityViolation) as caught:
        list_directory(facts, root)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE


def test_p2_001_dir_h_reparse_que_aparece_durante_a_operacao_e_reparse_detected(
    root: BoundRoot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = make_sub(root, "a.txt")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_bytes(b"y")

    def swap() -> None:
        (base(root) / "sub").rename(base(root) / "sub_antigo")
        if not _link(base(root) / "sub", outside):
            pytest.skip("link indisponível neste ambiente")

    ScandirHook(monkeypatch, on_exit=swap)
    with pytest.raises(PathIntegrityViolation) as caught:
        list_directory(facts, root)
    assert caught.value.category is IntegrityFailure.REPARSE_DETECTED


def test_p2_001_dir_diretorio_removido_durante_a_enumeracao_nao_devolve_listagem(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = make_sub(root, "a.txt")

    def vanish() -> None:
        (base(root) / "sub" / "a.txt").unlink()
        (base(root) / "sub").rmdir()

    ScandirHook(monkeypatch, on_exit=vanish)
    with pytest.raises(PathIntegrityViolation):  # a entrada sumiu: não há baseline da entrada
        list_directory(facts, root)


def test_p2_001_dir_fatos_de_outra_raiz_ou_fora_da_raiz_nao_listam(
    root: BoundRoot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.txt").write_bytes(b"x")
    hook = ScandirHook(monkeypatch)
    foreign = inspect(".", str(other))
    with pytest.raises(PathIntegrityViolation) as other_root:
        list_directory(foreign, root)
    assert other_root.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE
    outside = dataclasses.replace(inspect(".", root.path), canonical_target=str(tmp_path))
    with pytest.raises(PathIntegrityViolation) as escapes:
        list_directory(outside, root)
    assert escapes.value.category is IntegrityFailure.TARGET_OUTSIDE_ROOT
    assert hook.calls == 0


def test_p2_001_dir_alvo_que_nao_e_diretorio_nao_enumera(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    (base(root) / "f.txt").write_bytes(b"x")
    hook = ScandirHook(monkeypatch)
    with pytest.raises(path_runtime.PathOperationFailed) as caught:
        list_directory(inspect("f.txt", root.path), root)
    assert caught.value.category is path_runtime.PathFailure.NOT_DIRECTORY
    assert hook.calls == 0


def test_p2_001_dir_nenhuma_excecao_contem_caminho_absoluto(
    root: BoundRoot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = make_sub(root, "a.txt")
    erros: list[BaseException] = []
    for facts in (with_target(real, None), with_target(real, ObjectIdentity(1, 0))):
        with pytest.raises(PathIntegrityViolation) as caught:
            list_directory(facts, root)
        erros.append(caught.value)

    def swap() -> None:
        (base(root) / "sub").rename(base(root) / "sub_antigo")
        (base(root) / "sub").mkdir()
        (base(root) / "sub" / "a.txt").write_bytes(b"y")

    ScandirHook(monkeypatch, on_exit=swap)
    with pytest.raises(PathIntegrityViolation) as swapped:
        list_directory(real, root)
    erros.append(swapped.value)
    for erro in erros:
        text = str(erro) + repr(erro) + repr(erro.args)
        assert str(tmp_path) not in text and root.path not in text and "a.txt" not in text
        assert not hasattr(erro, "decision")
