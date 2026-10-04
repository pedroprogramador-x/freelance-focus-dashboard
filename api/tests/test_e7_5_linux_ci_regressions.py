"""E7.5 — regressões que o API CI Linux do PR #6 encontrou depois do GREEN local.

Três causas-raiz, cobertas pela causa e não pelo sintoma; cada uma tem uma prova que roda em
**qualquer** plataforma (o Windows mascarava as três):

* **Linux-CI-001** — gramática de path × materialização. `safety.paths.path_components` trata `/`
  e `\\` como separadores em todo SO; `path_runtime.inspect` montava `root / requested`, que no
  POSIX faz de `sub\\a.txt` **um** nome literal. Agora a materialização relativa usa a mesma lista
  de componentes da safety (e da cadeia léxica).
* **Linux-CI-002** — o `SearchText` classificava `EntryKind.OTHER` como "não pesquisável" **antes**
  da política. No POSIX um symlink enumerado por `lstat` é `OTHER`: a política nunca rodava e nada
  ia ao journal. Agora a política vem primeiro; `OTHER` permitido continua "files skipped".
* **Linux-CI-003** — corrida de pai. O POSIX pode reutilizar o inode de um diretório apagado e
  recriado, então `(dev, ino)` sozinho não prova a troca. `create_exclusive` agora recusa quando o
  pai final **existia** na inspeção e esta tentativa precisou de `mkdir` — fato independente do
  inode.
* **Linux-CI-AUD-001** — request × nome descoberto. A gramática de request (`/` e `\\` separam)
  vale para o que o provider pede, nunca para um `entry.name` que o filesystem devolveu: no POSIX,
  `a\\b.txt` literal é **um** segmento, e reinterpretá-lo abria `a/b.txt`. Sem escape em V1, um nome
  descoberto com separador de request é `ERROR path_unrepresentable`, antes de política ou IO.

Fixtures benignas: diretórios temporários e *mocks* de identidade/enumeração. Sem processo, shell,
git ou rede.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from app import path_runtime
from app.path_runtime import (
    BoundRoot,
    CreateMutationTrace,
    EntryKind,
    IntegrityFailure,
    PathIntegrityViolation,
    bind_root,
    create_exclusive,
    inspect,
)
from app.safety.paths import path_components
from app.safety.types import ObjectIdentity
from app.tool_executor.contracts import (
    DecisionCategory,
    ListDirectory,
    ReadFile,
    SearchText,
    WriteFile,
)
from tests.test_apply_patch_e7_5c import PatchRig, before_effect, create, partial
from tests.test_tool_file_ops_e7_5b import (
    Rig,
    after_inspect,
    denied,
    errored,
    fsx,
    lines_of,
    make_dir_link,
    ok,
    search,
)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def no_backslash_names(root: Path) -> None:
    """Nenhum objeto materializado com `\\` literal no nome (o sintoma POSIX)."""
    assert all("\\" not in entry.name for entry in root.rglob("*"))


# ============================================================ Linux-CI-001: separadores


def test_readfile_barra_invertida_le_o_alvo_canonico(rig: Rig) -> None:
    rig.put("sub/a.txt", "x")
    assert ok(rig.run(ReadFile("sub\\a.txt"))) == "x"
    assert rig.executor.usage().files_read == ("sub/a.txt",)
    assert rig.journal.records() == ()


def test_listdirectory_barra_invertida(rig: Rig) -> None:
    rig.put("sub/deep/y.py")
    assert lines_of(ok(rig.run(ListDirectory("sub\\deep")))) == ["file\tsub/deep/y.py"]


def test_searchtext_barra_invertida_em_arquivo_unico(rig: Rig) -> None:
    rig.put("sub/deep/c.txt", "needle\n")
    assert search(rig, "needle", "sub\\deep\\c.txt") == ["sub/deep/c.txt:1:1:needle"]
    assert search(rig, "needle", "sub\\deep") == ["sub/deep/c.txt:1:1:needle"]


def test_writefile_barra_invertida_cria_a_cadeia_real(rig: Rig) -> None:
    ok(rig.run(WriteFile("d\\e\\f.txt", "y")))
    assert (rig.ws / "d" / "e" / "f.txt").read_bytes() == b"y"
    assert sorted(p.name for p in rig.ws.iterdir()) == ["d"]
    no_backslash_names(rig.ws)


def test_separadores_mistos_sao_um_alvo_so(rig: Rig) -> None:
    rig.put("a/b/c/d.txt", "m")
    for raw in ("a/b\\c/d.txt", "a\\b/c\\d.txt", "a\\\\b//c\\d.txt", "./a/b/./c/d.txt"):
        assert ok(rig.run(ReadFile(raw))) == "m", raw
    ok(rig.run(WriteFile("x/y\\z.txt", "w")))
    assert (rig.ws / "x" / "y" / "z.txt").read_bytes() == b"w"
    no_backslash_names(rig.ws)
    assert rig.executor.usage().files_read == ("a/b/c/d.txt",)


@pytest.mark.parametrize(
    "raw",
    ["sub/a.txt", "sub\\a.txt", "sub\\\\a.txt", "a/b\\c/d.txt", "./sub//a.txt", ".", "./"],
)
def test_materializacao_e_cadeia_usam_a_sequencia_de_path_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """Plataforma-neutro: alvo canônico **e** cadeia léxica = `path_components(raw)`."""
    ws = tmp_path / "ws"
    ws.mkdir()
    decompositions: list[list[str]] = []
    real_parts = path_runtime._lexical_parts

    def spy(requested: str, form: Any) -> list[str]:
        parts = real_parts(requested, form)
        decompositions.append(list(parts))
        return parts

    monkeypatch.setattr(path_runtime, "_lexical_parts", spy)
    facts = inspect(raw, ws, tolerate_absent_tail=True)
    expected = path_components(raw)
    assert facts.canonical_target is not None
    assert Path(facts.canonical_target).relative_to(ws.resolve()).parts == tuple(expected)
    assert decompositions == [expected, expected]  # materialização + cadeia léxica
    assert facts.requested_path == raw  # o pedido original fica para journal/política


def test_cadeia_lexica_segue_a_mesma_gramatica_da_materializacao(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Se a gramática apontar para um link, **alvo e cadeia** o veem — nenhuma divergência."""
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "fora"
    outside.mkdir()
    if not make_dir_link(ws / "atalho", outside):
        pytest.skip("link indisponível neste ambiente")
    monkeypatch.setattr(path_runtime, "path_components", lambda _raw: ["atalho", "a.txt"])
    facts = inspect("qualquer/coisa.txt", ws, tolerate_absent_tail=True)
    assert Path(facts.canonical_target or "") == (outside / "a.txt").resolve()
    assert (
        facts.is_symlink.is_true or facts.is_junction.is_true
    ) and facts.ancestor_link_outside_root.is_true


def test_ponto_e_a_propria_raiz(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    facts = inspect(".", ws, tolerate_absent_tail=True)
    assert facts.canonical_target == str(ws.resolve())
    assert facts.exists and facts.inspection_error is None


def test_materializacao_deriva_de_path_components_e_nao_da_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contrafactual que o Windows também vê: se a gramática disser outra coisa, o alvo segue a
    gramática — nunca `root / requested` (que no POSIX trata `\\` como letra)."""
    ws = tmp_path / "ws"
    (ws / "redirecionado").mkdir(parents=True)
    monkeypatch.setattr(path_runtime, "path_components", lambda _raw: ["redirecionado", "a.txt"])
    facts = inspect("qualquer/coisa.txt", ws, tolerate_absent_tail=True)
    assert facts.canonical_target == str((ws / "redirecionado" / "a.txt").resolve())
    assert facts.requested_path == "qualquer/coisa.txt"


# ============================================================ Linux-CI-002: SearchText × link


def _enumerate_as_other(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Simula o POSIX: a enumeração por `lstat` devolve estas entradas como `EntryKind.OTHER`."""
    real = fsx.list_directory

    def listing(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        entries = tuple(
            dataclasses.replace(entry, kind=EntryKind.OTHER) if entry.name in names else entry
            for entry in result.entries
        )
        return dataclasses.replace(result, entries=entries)

    monkeypatch.setattr(fsx, "list_directory", listing)


class OpenSpy:
    """Registra todo `os.open`/`os.scandir` cujo caminho cai fora do workspace."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, outside: Path) -> None:
        self.hits: list[str] = []
        marker = str(outside.resolve())
        for name in ("open", "scandir"):
            real = getattr(os, name)

            def wrapper(path: Any, *a: Any, _real: Any = real, **k: Any) -> Any:
                if isinstance(path, str | os.PathLike) and marker in os.fspath(path):
                    self.hits.append(str(os.fspath(path)))
                return _real(path, *a, **k)

            monkeypatch.setattr(os, name, wrapper)


def _link_rig(rig: Rig) -> None:
    (rig.outside / "x.txt").write_text("NEEDLE")
    if not make_dir_link(rig.ws / "atalho", rig.outside):
        pytest.skip("link indisponível neste ambiente")
    rig.put("a.txt", "NEEDLE\n")


def _assert_link_denied_once(rig: Rig, content: str) -> None:
    assert content.split("\n")[0] == "a.txt:1:1:NEEDLE"
    assert "x.txt" not in content and "atalho" not in content
    assert "[1 paths skipped by policy]" in content
    assert "files skipped" not in content  # o único `OTHER` é o link: nada de "não pesquisável"
    (record,) = rig.journal.records()
    assert record.category is DecisionCategory.PATH_DENIED
    assert record.subject_redacted == "atalho" and record.operation == "SearchText"
    assert rig.executor.usage().denials == 0  # a operação foi OK
    assert rig.executor.usage().files_read == ("a.txt",)


def test_link_enumerado_como_other_passa_pela_politica(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plataforma-neutro: o `kind` POSIX do link (`OTHER`) não pode pular política nem journal."""
    _link_rig(rig)
    _enumerate_as_other(monkeypatch, "atalho")
    spy = OpenSpy(monkeypatch, rig.outside)
    _assert_link_denied_once(rig, ok(rig.run(SearchText("NEEDLE"))))
    assert spy.hits == []  # o alvo do link nunca é aberto nem enumerado


def test_link_real_de_diretorio(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    _link_rig(rig)
    spy = OpenSpy(monkeypatch, rig.outside)
    _assert_link_denied_once(rig, ok(rig.run(SearchText("NEEDLE"))))
    assert spy.hits == []


def test_symlink_posix_de_arquivo(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    (rig.outside / "x.txt").write_text("NEEDLE")
    try:
        (rig.ws / "atalho").symlink_to(rig.outside / "x.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlink de arquivo indisponível neste ambiente")
    rig.put("a.txt", "NEEDLE\n")
    spy = OpenSpy(monkeypatch, rig.outside)
    _assert_link_denied_once(rig, ok(rig.run(SearchText("NEEDLE"))))
    assert spy.hits == []


def test_other_permitido_continua_nao_pesquisavel(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FIFO/socket/device sem link: passa pela política e só então vira "files skipped"."""
    rig.put("a.txt", "NEEDLE\n")
    rig.put("especial.bin", "NEEDLE\n")
    _enumerate_as_other(monkeypatch, "especial.bin")
    opened: list[str] = []
    real_open = fsx.open_existing

    def open_spy(facts: Any, *a: Any, **k: Any) -> Any:
        opened.append(Path(facts.canonical_target).name)
        return real_open(facts, *a, **k)

    monkeypatch.setattr(fsx, "open_existing", open_spy)
    content = ok(rig.run(SearchText("NEEDLE")))
    assert lines_of(content)[0] == "a.txt:1:1:NEEDLE"
    assert "especial.bin:" not in content
    assert "[1 files skipped: not searchable text or larger than" in content
    assert "skipped by policy" not in content
    assert rig.journal.records() == ()
    assert opened == ["a.txt"]
    assert rig.executor.usage().files_read == ("a.txt",)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO só em POSIX")
def test_fifo_real_e_pulado_sem_abrir(rig: Rig) -> None:
    rig.put("a.txt", "NEEDLE\n")
    posix: Any = os  # `mkfifo` não existe no stub Windows
    posix.mkfifo(rig.ws / "cano")  # abrir para leitura bloquearia: a prova é terminar
    content = ok(rig.run(SearchText("NEEDLE")))
    assert "[1 files skipped: not searchable text or larger than" in content
    assert rig.journal.records() == ()


def test_other_negado_pela_politica_conta_como_politica(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Um `OTHER` com nome de segredo é negação de política (registrada), não "não pesquisável"."""
    rig.put("a.txt", "NEEDLE\n")
    rig.put(".env", "NEEDLE\n")
    _enumerate_as_other(monkeypatch, ".env")
    content = ok(rig.run(SearchText("NEEDLE")))
    assert "[1 paths skipped by policy]" in content and "files skipped" not in content
    assert [r.subject_redacted for r in rig.journal.records()] == [".env"]


# ============================================================ Linux-CI-003: pai recriado


def _reuse_dir_identity(
    monkeypatch: pytest.MonkeyPatch, root: BoundRoot, old: ObjectIdentity
) -> Any:
    """Simula reuso de inode: todo diretório **exceto a raiz** passa a ter a identidade antiga."""
    real = path_runtime._identity

    def fake(info: os.stat_result) -> ObjectIdentity:
        identity = real(info)
        if identity == root.identity or not stat.S_ISDIR(info.st_mode):
            return identity
        return old

    monkeypatch.setattr(path_runtime, "_identity", fake)
    return fake


@pytest.mark.parametrize("removed", ["p", "p/q"])
def test_pai_que_existia_e_precisou_de_mkdir_e_troca_mesmo_com_inode_reutilizado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, removed: str
) -> None:
    ws = tmp_path / "ws"
    (ws / "p" / "q").mkdir(parents=True)
    root = bind_root(ws)
    facts = inspect("p/q/n.txt", ws, tolerate_absent_tail=True)
    assert facts.parent_identity is not None  # o pai final existia na inspeção
    shutil.rmtree(ws / removed)
    fake = _reuse_dir_identity(monkeypatch, root, facts.parent_identity)
    trace = CreateMutationTrace()
    with pytest.raises(PathIntegrityViolation) as raised:
        create_exclusive(facts, root, mutation_trace=trace)
    assert raised.value.category is IntegrityFailure.PARENT_IDENTITY_CHANGED
    assert not (ws / "p" / "q" / "n.txt").exists()
    assert (ws / "p" / "q").is_dir()  # recriados permanecem
    assert trace.created_parent_count == {"p": 2, "p/q": 1}[removed] and not trace.created_target
    # A simulação é efetiva: só `(dev, ino)` teria aceitado o pai recriado.
    assert fake(os.lstat(ws / "p" / "q")) == facts.parent_identity


def test_pai_ausente_na_inspecao_e_criado_normalmente(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    root = bind_root(ws)
    facts = inspect("novo/a/b.txt", ws, tolerate_absent_tail=True)
    assert facts.parent_identity is None
    trace = CreateMutationTrace()
    with create_exclusive(facts, root, mutation_trace=trace):
        pass
    assert trace.created_parent_count == 2 and trace.created_target
    assert (ws / "novo" / "a" / "b.txt").is_file()


def test_pai_existente_sem_corrida_nao_cria_nada(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "p" / "q").mkdir(parents=True)
    root = bind_root(ws)
    facts = inspect("p/q/n.txt", ws, tolerate_absent_tail=True)
    trace = CreateMutationTrace()
    with create_exclusive(facts, root, mutation_trace=trace):
        pass
    assert trace.created_parent_count == 0 and trace.created_target


def test_writefile_corrida_de_pai_com_inode_reutilizado_e_negada(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    (rig.ws / "p" / "q").mkdir(parents=True)
    old = path_runtime._identity(os.stat(rig.ws / "p" / "q"))

    def race(n: int) -> None:
        if n == 1:
            shutil.rmtree(rig.ws / "p")
            _reuse_dir_identity(monkeypatch, rig.root, old)

    after_inspect(monkeypatch, race)
    denied(rig.run(WriteFile("p/q/n.txt", "x")), "path.parent_changed")
    assert not (rig.ws / "p" / "q" / "n.txt").exists()
    assert [r.rule_id for r in rig.journal.records()] == ["path.parent_changed"]


def test_applypatch_corrida_de_pai_com_inode_reutilizado_e_denied_parcial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prig = PatchRig(tmp_path)
    (prig.ws / "p" / "q").mkdir(parents=True)
    old = path_runtime._identity(os.stat(prig.ws / "p" / "q"))

    def race(_plan: Any) -> None:
        shutil.rmtree(prig.ws / "p")
        _reuse_dir_identity(monkeypatch, prig.root, old)

    before_effect(monkeypatch, race)
    result = prig.patch(create("p/q/n.txt", "n"))
    denied(result, "path.parent_changed")
    assert partial(result)  # `p` e `q` recriados permanecem: efeito parcial honesto
    assert (prig.ws / "p" / "q").is_dir() and not (prig.ws / "p" / "q" / "n.txt").exists()
    assert prig.rules() == ["path.parent_changed"]


@pytest.mark.skipif(sys.platform == "win32", reason="reuso de inode real é fenômeno POSIX")
def test_applypatch_corrida_de_pai_real_no_posix(tmp_path: Path, monkeypatch: Any) -> None:
    """Sem simulação: o cenário exato do CI (o filesystem pode ou não reutilizar o inode)."""
    prig = PatchRig(tmp_path)
    (prig.ws / "p" / "q").mkdir(parents=True)
    before_effect(monkeypatch, lambda _plan: shutil.rmtree(prig.ws / "p"))
    result = prig.patch(create("p/q/n.txt", "n"))
    denied(result, "path.parent_changed")
    assert partial(result)


# ============================================================ Linux-CI-AUD-001: nome descoberto

UNREPRESENTABLE = "path_unrepresentable: um caminho descoberto não pode ser representado"


def _rename_enumerated(monkeypatch: pytest.MonkeyPatch, renames: dict[str, str]) -> list[Any]:
    """Simula um nome **literal** do filesystem (impossível de criar no Windows): a enumeração
    devolve ``renames[nome real]`` no lugar do nome real. Devolve a lista de diretórios listados."""
    real = fsx.list_directory
    listed: list[Any] = []

    def listing(facts: Any, *args: Any, **kwargs: Any) -> Any:
        listed.append(facts.canonical_target)
        result = real(facts, *args, **kwargs)
        entries = tuple(
            dataclasses.replace(entry, name=renames.get(entry.name, entry.name))
            for entry in result.entries
        )
        return dataclasses.replace(result, entries=entries)

    monkeypatch.setattr(fsx, "list_directory", listing)
    return listed


class PathSpy:
    """Tudo o que chega a `_visible_facts`, `inspect` e `open_existing` depois da enumeração."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
        self.visible: list[str] = []
        self.inspected: list[str] = []
        self.opened: list[str] = []
        real_visible, real_inspect, real_open = (
            fsx._visible_facts,
            fsx.inspect,
            fsx.open_existing,
        )
        canonical_root = root.resolve()

        def visible(context: Any, relative: str) -> Any:
            self.visible.append(relative)
            return real_visible(context, relative)

        def inspected(requested: str, *a: Any, **k: Any) -> Any:
            self.inspected.append(requested)
            return real_inspect(requested, *a, **k)

        def opened(facts: Any, *a: Any, **k: Any) -> Any:
            target = Path(facts.canonical_target).relative_to(canonical_root).as_posix()
            self.opened.append(target)
            return real_open(facts, *a, **k)

        monkeypatch.setattr(fsx, "_visible_facts", visible)
        monkeypatch.setattr(fsx, "inspect", inspected)
        monkeypatch.setattr(fsx, "open_existing", opened)

    def never_saw_backslash(self) -> None:
        for seen in (*self.visible, *self.inspected, *self.opened):
            assert "\\" not in seen, seen


def _unrepresentable(rig: Rig, result: Any) -> None:
    errored(result, "path_unrepresentable")
    assert result.reason == UNREPRESENTABLE  # fixo: sem nome, caminho, repr ou exceção
    assert rig.journal.records() == ()  # técnico: nenhuma `SafetyDecision`
    usage = rig.executor.usage()
    assert usage.denials == 0 and usage.files_read == ()


def _alias_rig(rig: Rig) -> None:
    """`a/b.txt` real + uma entrada enumerada como `a\\b.txt` literal (a real é `z.txt`)."""
    rig.put("a/b.txt", "NEEDLE normal\n")
    rig.put("z.txt", "NEEDLE literal\n")


def test_listdirectory_nome_descoberto_com_barra_invertida_e_error(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _alias_rig(rig)
    _rename_enumerated(monkeypatch, {"z.txt": "a\\b.txt"})
    spy = PathSpy(monkeypatch, rig.ws)
    _unrepresentable(rig, rig.run(ListDirectory(None)))
    assert spy.visible == []  # nenhuma entrada chegou à política: a recusa é antes
    assert spy.inspected == ["."]  # só a base pedida
    assert spy.opened == []


def test_searchtext_nome_descoberto_com_barra_invertida_e_error_sem_abrir_o_alias(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _alias_rig(rig)
    _rename_enumerated(monkeypatch, {"z.txt": "a\\b.txt"})
    spy = PathSpy(monkeypatch, rig.ws)
    _unrepresentable(rig, rig.run(SearchText("NEEDLE")))
    assert spy.visible == [] and spy.opened == []  # `a/b.txt` nunca aberto em nome do literal
    spy.never_saw_backslash()


def test_searchtext_match_anterior_nao_vaza_em_error(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Match já coletado (`a.txt`) antes do diretório com o nome ruim: `ERROR`, sem conteúdo e
    sem `files_read` — a proveniência só é entregue com `ToolResult OK`."""
    rig.put("a.txt", "NEEDLE\n")
    rig.put("sub/z.txt", "NEEDLE\n")
    rig.put("sub/y.txt", "NEEDLE\n")
    _rename_enumerated(monkeypatch, {"z.txt": "x\\y.txt"})
    spy = PathSpy(monkeypatch, rig.ws)
    _unrepresentable(rig, rig.run(SearchText("NEEDLE")))
    assert spy.opened == ["a.txt"]  # lido internamente; nada de `sub/` foi aberto
    spy.never_saw_backslash()


@pytest.mark.parametrize("kind", [EntryKind.DIRECTORY, EntryKind.OTHER, EntryKind.FILE])
def test_entrada_literal_de_qualquer_tipo_nao_e_recursada_nem_pulada(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, kind: EntryKind
) -> None:
    rig.put("d/x.txt", "NEEDLE\n")
    rig.put("ok.txt", "NEEDLE\n")
    listed = _rename_enumerated(monkeypatch, {"d": "a\\b"})
    real = fsx.list_directory

    def with_kind(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        entries = tuple(
            dataclasses.replace(e, kind=kind) if e.name == "a\\b" else e for e in result.entries
        )
        return dataclasses.replace(result, entries=entries)

    monkeypatch.setattr(fsx, "list_directory", with_kind)
    spy = PathSpy(monkeypatch, rig.ws)
    result = rig.run(SearchText("NEEDLE"))
    _unrepresentable(rig, result)
    assert "files skipped" not in (result.reason or "")
    assert len(listed) == 1  # só a raiz: o diretório literal não é recursado (nem como `a/b`)
    assert spy.visible == [] and spy.opened == []
    _unrepresentable(rig, rig.run(ListDirectory(None)))


@pytest.mark.parametrize("bad", ["0\\primeiro", "zz\\ultimo"], ids=["primeiro", "ultimo"])
def test_desfecho_nao_depende_da_ordem_nem_da_politica(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """O diretório inteiro é validado antes de qualquer política: nem a posição do nome ruim nem
    um `.env` negado antes dele mudam o desfecho — e o journal fica vazio."""
    rig.put(".env", "NEEDLE\n")
    rig.put("a.txt", "NEEDLE\n")
    rig.put("m.txt", "NEEDLE\n")
    _rename_enumerated(monkeypatch, {"m.txt": bad})
    for request in (ListDirectory(None), SearchText("NEEDLE")):
        _unrepresentable(rig, rig.run(request))


@pytest.mark.parametrize(
    "name", ["a/b.txt", "x\udcff.txt"], ids=["barra-injetada", "surrogate-solto"]
)
def test_outros_nomes_nao_representaveis(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """`/` não vem de um filesystem real (fixture injetada); um surrogate solto vem de byte não
    UTF-8 no POSIX (`surrogateescape`) ou de UTF-16 inválido no Windows — ambos sem texto fiel."""
    rig.put("a.txt", "NEEDLE\n")
    rig.put("m.txt", "NEEDLE\n")
    _rename_enumerated(monkeypatch, {"m.txt": name})
    spy = PathSpy(monkeypatch, rig.ws)
    for request in (ListDirectory(None), SearchText("NEEDLE")):
        _unrepresentable(rig, rig.run(request))
    assert spy.visible == [] and spy.opened == []


def test_fora_do_escopo_enumerado_nada_muda(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    """O nome ruim na raiz não afeta uma operação cujo escopo não o enumera."""
    rig.put("sub/a.txt", "NEEDLE\n")
    rig.put("z.txt", "x")
    _rename_enumerated(monkeypatch, {"z.txt": "a\\b.txt"})
    assert lines_of(ok(rig.run(ListDirectory("sub")))) == ["file\tsub/a.txt"]
    assert search(rig, "NEEDLE", "sub") == ["sub/a.txt:1:1:NEEDLE"]
    assert ok(rig.run(ReadFile("sub\\a.txt"))) == "NEEDLE\n"  # request com `\\`: intacto


# ---------------------------------------------------------------- AUD-001 no POSIX real

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="o Windows não permite `\\` literal em nome de arquivo"
)


@posix_only
def test_posix_literal_e_alias_real(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.put("a/b.txt", "NORMAL\n")
    (rig.ws / "a\\b.txt").write_text("LITERAL\n")
    spy = PathSpy(monkeypatch, rig.ws)
    for query in ("NORMAL", "LITERAL"):
        _unrepresentable(rig, rig.run(SearchText(query)))
    _unrepresentable(rig, rig.run(ListDirectory(None)))
    assert spy.opened == [] and spy.visible == []  # nem NORMAL em nome do literal, nem LITERAL
    spy.never_saw_backslash()


@posix_only
def test_posix_so_o_literal_nunca_vira_not_found(rig: Rig) -> None:
    (rig.ws / "a\\b.txt").write_text("LITERAL\n")
    _unrepresentable(rig, rig.run(SearchText("LITERAL")))
    _unrepresentable(rig, rig.run(ListDirectory(None)))


@posix_only
def test_posix_diretorio_literal_nao_e_recursado(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    (rig.ws / "a\\b").mkdir()
    (rig.ws / "a\\b" / "x.txt").write_text("NEEDLE\n")
    rig.put("a/b/x.txt", "NEEDLE\n")
    spy = PathSpy(monkeypatch, rig.ws)
    _unrepresentable(rig, rig.run(SearchText("NEEDLE")))
    _unrepresentable(rig, rig.run(ListDirectory(None)))
    assert spy.opened == []
    assert lines_of(ok(rig.run(ListDirectory("a")))) == ["directory\ta/b"]  # fora do escopo ruim
