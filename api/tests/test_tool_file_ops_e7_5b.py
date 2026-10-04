"""E7.5-B — `ReadFile`, `ListDirectory`, `SearchText` e `WriteFile` pelo executor concreto.

Todos os testes passam pelo `LocalWorktreeToolExecutor.execute` (a mesma porta do provider) sobre
um diretório temporário real. Fixtures benignas: arquivos de texto, uma junction NTFS (ou symlink
em POSIX) para **outro** diretório temporário, e *mocks* de `os.scandir`, do relógio e das
primitivas de `path_runtime` para simular corrida, cancelamento e prazo. Nenhum git, processo,
rede ou helper externo.

Limites pequenos nos testes vêm de um `ToolLimits` **de teste** (`dataclasses.replace`); a constante
`TOOL_LIMITS_V1` não é alterada. Onde o valor real é barato, o teste usa o real.
"""

from __future__ import annotations

import dataclasses
import itertools
import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from app.path_runtime import (
    IntegrityFailure,
    PathFailure,
    PathIntegrityViolation,
    PathOperationFailed,
    bind_root,
)
from app.safety import (
    TOOL_LIMITS_V1,
    EnforcementMode,
    ObjectIdentity,
    SafetyDecision,
    SafetyPolicy,
    ToolLimits,
)
from app.safety.capability_profile import DEVELOPER_V1_PROFILE
from app.tool_executor import fs_ops
from app.tool_executor.contracts import (
    DecisionCategory,
    ListDirectory,
    ReadFile,
    RunScope,
    SearchText,
    ToolResult,
    ToolStatus,
    WriteFile,
)
from app.tool_executor.executor import LocalWorktreeToolExecutor
from app.tool_executor.journal import InMemoryDecisionJournal
from app.tool_executor.outcome import HandlerContext, Outcome
from app.tool_executor.reasons import reason_for
from app.tool_executor.workspace import ResolvedWorkspace

COMMIT = "d" * 40
fsx: Any = fs_ops  # as primitivas reimportadas em `fs_ops` são alvo de `monkeypatch`
FS_OPS_FILE = fs_ops.__file__
TOKEN_LIKE = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"  # formato de token: fake

if sys.platform == "win32":
    import _winapi

    def make_dir_link(link: Path, target: Path) -> bool:
        _winapi.CreateJunction(str(target), str(link))
        return True

else:

    def make_dir_link(link: Path, target: Path) -> bool:
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            return False
        return True


# ------------------------------------------------------------------------------------ apoio


class Token:
    def __init__(self) -> None:
        self.cancelled = False

    def is_cancelled(self) -> bool:
        return self.cancelled


class CountdownToken:
    """Passa a cancelar depois de ``after`` consultas — cancela **no meio** da operação."""

    def __init__(self, after: int) -> None:
        self.after = after
        self.calls = 0

    def is_cancelled(self) -> bool:
        self.calls += 1
        return self.calls > self.after


def limits(**changes: int) -> ToolLimits:
    return dataclasses.replace(TOOL_LIMITS_V1, **changes)


class Rig:
    """Um workspace temporário + um executor real ligado a ele."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        tool_limits: ToolLimits | None = None,
        token: Any = None,
    ) -> None:
        self.tmp_path = tmp_path
        self.ws = tmp_path / "ws"
        self.ws.mkdir(parents=True)
        self.outside = tmp_path / "outside"
        self.outside.mkdir()
        self.token = token if token is not None else Token()
        self.journal = InMemoryDecisionJournal()
        bound = bind_root(self.ws)
        self.root = bound
        self.executor = LocalWorktreeToolExecutor(
            workspace=ResolvedWorkspace(
                workspace_id="wt-1",
                task_id="t1",
                run_id="r1",
                base_commit=COMMIT,
                workspace_path=str(self.ws),
                root_identity=bound.identity,
            ),
            root=bound,
            policy=SafetyPolicy(),
            profile=DEVELOPER_V1_PROFILE,
            run_scope=RunScope("t1", "r1", "i1"),
            cancel_token=self.token,
            journal=self.journal,
            limits=tool_limits if tool_limits is not None else TOOL_LIMITS_V1,
        )

    def put(self, relative: str, data: bytes | str = b"x") -> Path:
        path = self.ws / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def run(self, request: Any) -> ToolResult:
        return self.executor.execute(request)

    def reasons(self) -> list[str]:
        return [record.rule_id for record in self.journal.records()]


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def ok(result: ToolResult) -> str:
    assert result.status is ToolStatus.OK, result
    assert result.content is not None
    return result.content


def denied(result: ToolResult, rule: str | None = None) -> None:
    assert result.status is ToolStatus.DENIED, result
    assert result.content is None
    if rule is not None:
        assert result.reason is not None and result.reason.startswith(f"{rule}:"), result.reason


def errored(result: ToolResult, code: str) -> None:
    assert result.status is ToolStatus.ERROR, result
    assert result.content is None
    assert result.reason is not None and result.reason.startswith(f"{code}:"), result.reason


def after_inspect(monkeypatch: pytest.MonkeyPatch, callback: Callable[[int], None]) -> None:
    """Executa ``callback(n)`` logo depois da *n*-ésima chamada de `inspect` do `fsx`."""
    real = fsx.inspect
    counter = {"n": 0}

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        facts = real(*args, **kwargs)
        counter["n"] += 1
        callback(counter["n"])
        return facts

    monkeypatch.setattr(fsx, "inspect", wrapper)


def zero_ids(monkeypatch: pytest.MonkeyPatch, keep: set[int]) -> None:
    from app import path_runtime

    real = path_runtime._identity

    def fake(info: os.stat_result) -> ObjectIdentity:
        identity = real(info)
        return identity if info.st_ino in keep else ObjectIdentity(identity.volume_id, 0)

    monkeypatch.setattr(path_runtime, "_identity", fake)


class ScandirOrder:
    """O `os.scandir` devolve as mesmas entradas numa ordem escolhida pelo teste."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.reverse = False
        self.shift = 0
        real = os.scandir
        outer = self

        class _Scanner:
            def __init__(self, inner: Any) -> None:
                self._inner = inner

            def __enter__(self) -> Iterator[Any]:
                entries = list(self._inner)
                entries = entries[::-1] if outer.reverse else entries
                k = outer.shift % len(entries) if entries else 0
                return iter(entries[k:] + entries[:k])

            def __exit__(self, *exc: object) -> None:
                self._inner.close()

        monkeypatch.setattr(os, "scandir", lambda path=".": _Scanner(real(path)))


# ===================================================================================== ReadFile


def test_readfile_arquivo_utf8_pequeno_e_files_read(rig: Rig) -> None:
    rig.put("a.txt", "olá, mundo\nlinha 2\n")
    result = rig.run(ReadFile("a.txt"))
    assert ok(result) == "olá, mundo\nlinha 2\n"
    assert rig.executor.usage().files_read == ("a.txt",)
    assert rig.executor.usage().operations == 1 and rig.executor.usage().denials == 0


def test_readfile_preserva_crlf_e_bom_e_arquivo_vazio(rig: Rig) -> None:
    rig.put("crlf.txt", b"a\r\nb\r\n")
    rig.put("bom.txt", b"\xef\xbb\xbfx")
    rig.put("vazio.txt", b"")
    assert ok(rig.run(ReadFile("crlf.txt"))) == "a\r\nb\r\n"
    assert ok(rig.run(ReadFile("bom.txt"))) == "\ufeffx"
    assert ok(rig.run(ReadFile("vazio.txt"))) == ""
    assert rig.executor.usage().files_read == ("bom.txt", "crlf.txt", "vazio.txt")


def test_readfile_aceita_separador_barra_invertida_e_registra_barra(rig: Rig) -> None:
    rig.put("sub/a.txt", "x")
    assert ok(rig.run(ReadFile("sub\\a.txt"))) == "x"
    assert rig.executor.usage().files_read == ("sub/a.txt",)


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS é case-insensitive")
def test_readfile_files_read_usa_a_grafia_canonica_do_disco(rig: Rig) -> None:
    rig.put("Src/Main.py", "x")
    ok(rig.run(ReadFile("src/main.py")))
    assert rig.executor.usage().files_read == ("Src/Main.py",)


@pytest.mark.parametrize(
    "raw", ["/a.txt", "\\a.txt", ".", "./", "../a.txt", "C:a.txt", "C:\\a.txt"]
)
def test_readfile_caminhos_root_relative_absolutos_e_perigosos_sao_negados(
    rig: Rig, raw: str
) -> None:
    rig.put("a.txt", "x")
    denied(rig.run(ReadFile(raw)))
    assert rig.executor.usage().files_read == ()
    assert len(rig.journal) == 1


@pytest.mark.parametrize(
    "raw", ["\\\\srv\\share\\f", "\\\\?\\C:\\x", "a.txt:stream", "con", "PROGRA~1"]
)
def test_readfile_unc_device_ads_reservado_e_8_3_sao_negados(rig: Rig, raw: str) -> None:
    denied(rig.run(ReadFile(raw)))


@pytest.mark.parametrize("raw", [".git/config", ".GIT/HEAD", "sub/.git/x", "a\\.git\\y"])
def test_readfile_dot_git_e_negado_exista_ou_nao(rig: Rig, raw: str) -> None:
    (rig.ws / ".git").mkdir()
    (rig.ws / ".git" / "config").write_text("[core]")
    result = rig.run(ReadFile(raw))
    denied(result, "path.dot_git_denied")
    assert rig.journal.records()[-1].category is DecisionCategory.PATH_DENIED


def test_readfile_gitattributes_e_gitmodules_sao_arquivos_normais(rig: Rig) -> None:
    rig.put(".gitattributes", "* text=auto\n")
    rig.put(".gitmodules", "")
    assert ok(rig.run(ReadFile(".gitattributes"))) == "* text=auto\n"
    ok(rig.run(ReadFile(".gitmodules")))


@pytest.mark.parametrize(
    "raw", [".env", ".env.example", "sub/.env", "keys/server.pem", "my_secret.txt"]
)
def test_readfile_segredo_e_negado_existente_ou_nao_sem_oraculo_de_existencia(
    rig: Rig, raw: str
) -> None:
    nonexistent = rig.run(ReadFile(raw))
    rig.put(raw, "TOKEN=1")
    existing = rig.run(ReadFile(raw))
    denied(nonexistent, "path.secret_denied")
    denied(existing, "path.secret_denied")
    assert nonexistent.reason == existing.reason
    assert all(r.category is DecisionCategory.SECRET_ACCESS_BLOCKED for r in rig.journal.records())


def test_readfile_link_ou_reparse_e_negado(rig: Rig) -> None:
    (rig.outside / "segredo.txt").write_text("fora")
    if not make_dir_link(rig.ws / "link", rig.outside):
        pytest.skip("link indisponível neste ambiente")
    denied(rig.run(ReadFile("link/segredo.txt")))
    denied(rig.run(ReadFile("link")))
    assert rig.executor.usage().files_read == ()


def test_readfile_diretorio_e_inexistente_sao_erro(rig: Rig) -> None:
    (rig.ws / "dir").mkdir()
    errored(rig.run(ReadFile("dir")), "is_directory")
    errored(rig.run(ReadFile("nao-existe.txt")), "not_found")
    errored(rig.run(ReadFile("a/b/c/nao-existe.txt")), "not_found")
    assert len(rig.journal) == 0 and rig.executor.usage().denials == 0


def test_readfile_utf8_invalido_e_binario_sao_erro_fixo(rig: Rig) -> None:
    rig.put("invalido.txt", b"ok \xff\xfe fim")
    rig.put("nulo.txt", b"texto\x00texto")
    rig.put("png.bin", b"\x89PNG\r\n\x1a\n\x00\x00")
    for name in ("invalido.txt", "nulo.txt", "png.bin"):
        errored(rig.run(ReadFile(name)), "not_text")
    assert rig.executor.usage().files_read == ()


def test_readfile_exatamente_o_limite_e_ok_e_um_byte_a_mais_e_negado(rig: Rig) -> None:
    limit = TOOL_LIMITS_V1.read_file_bytes
    rig.put("limite.txt", "x" * limit)
    rig.put("acima.txt", "x" * (limit + 1))
    assert len(ok(rig.run(ReadFile("limite.txt")))) == limit
    result = rig.run(ReadFile("acima.txt"))
    denied(result, "limit.read_file_bytes")
    assert rig.journal.records()[-1].category is DecisionCategory.LIMIT_EXCEEDED
    assert rig.executor.usage().files_read == ("limite.txt",)


def test_readfile_limite_e_em_bytes_utf8_nao_em_caracteres(tmp_path: Path) -> None:
    rig = Rig(tmp_path, tool_limits=limits(read_file_bytes=10))
    rig.put("cinco.txt", "é" * 5)  # 10 bytes
    rig.put("seis.txt", "é" * 6)  # 12 bytes
    assert ok(rig.run(ReadFile("cinco.txt"))) == "é" * 5
    denied(rig.run(ReadFile("seis.txt")), "limit.read_file_bytes")


def test_readfile_acima_do_limite_e_detectado_pelo_tamanho_sem_ler_o_conteudo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(read_file_bytes=8))
    rig.put("grande.txt", "x" * 100)

    def proibido(*_a: object, **_k: object) -> bytes:
        raise AssertionError("o conteúdo não pode ser lido quando o tamanho já excede o limite")

    monkeypatch.setattr(fsx, "read_fd", proibido)
    denied(rig.run(ReadFile("grande.txt")), "limit.read_file_bytes")


def test_readfile_arquivo_que_cresce_durante_a_leitura_nunca_e_parcial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(read_file_bytes=8))
    rig.put("cresce.txt", "x" * 4)  # o `fstat` diz 4...
    monkeypatch.setattr(fsx, "read_fd", lambda fd, max_bytes, **k: b"x" * (max_bytes + 1))
    denied(rig.run(ReadFile("cresce.txt")), "limit.read_file_bytes")  # ...mas a leitura passou
    assert rig.executor.usage().files_read == ()


def test_readfile_cancelamento_antes_e_no_meio_da_leitura_e_sticky(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path)
    rig.put("a.txt", "x")
    rig.token.cancelled = True
    errored(rig.run(ReadFile("a.txt")), "operation_cancelled")
    rig.token.cancelled = False
    errored(rig.run(ReadFile("a.txt")), "operation_cancelled")  # sticky

    rig2 = Rig(tmp_path / "outro")
    rig2.put("a.txt", "x")

    def cancelando(fd: int, max_bytes: int, **kwargs: Any) -> bytes:
        raise PathOperationFailed(PathFailure.CANCELLED)

    monkeypatch.setattr(fsx, "read_fd", cancelando)
    errored(rig2.run(ReadFile("a.txt")), "operation_cancelled")
    monkeypatch.undo()
    errored(rig2.run(ReadFile("a.txt")), "operation_cancelled")
    assert rig.executor.usage().files_read == () and rig2.executor.usage().files_read == ()


def test_readfile_violacao_de_integridade_e_denied_com_journal(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "x")

    def trocar(n: int) -> None:
        (rig.ws / "a.txt").unlink()
        if not make_dir_link(rig.ws / "a.txt", rig.outside):
            pytest.skip("link indisponível neste ambiente")

    after_inspect(monkeypatch, trocar)
    denied(rig.run(ReadFile("a.txt")), "path.reparse_detected")
    (record,) = rig.journal.records()
    assert record.category is DecisionCategory.TOCTOU_RECHECK_FAILED
    assert record.subject_redacted == "a.txt"


def test_readfile_alvo_trocado_apos_a_inspecao_e_negado_pela_decisao_pos_open(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "original")

    def trocar(n: int) -> None:
        other = rig.ws / "outro.txt"
        other.write_text("trocado")
        os.replace(other, rig.ws / "a.txt")

    after_inspect(monkeypatch, trocar)
    denied(rig.run(ReadFile("a.txt")), "path.toctou_recheck_failed")
    assert rig.executor.usage().files_read == ()


def test_readfile_identidade_nao_verificavel_e_negada(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "x")
    zero_ids(monkeypatch, keep={os.stat(rig.ws).st_ino})
    denied(rig.run(ReadFile("a.txt")), "path.integrity_unverifiable")


def test_readfile_redacao_so_na_fronteira_de_saida(rig: Rig) -> None:
    rig.put("config.py", f"API = '{TOKEN_LIKE}'\nNAME = 'x'\n")
    content = ok(rig.run(ReadFile("config.py")))
    assert TOKEN_LIKE not in content and "NAME = 'x'" in content
    assert rig.executor.usage().files_read == ("config.py",)  # o dado cru foi lido e contado


def test_readfile_nunca_trunca_se_a_saida_nao_cabe_e_negada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(read_file_bytes=64, tool_result_content_bytes=16))
    rig.put("a.txt", "y" * 40)  # cabe no limite de leitura, não no teto do resultado
    denied(rig.run(ReadFile("a.txt")), "limit.tool_result_content_bytes")
    assert rig.executor.usage().files_read == ()
    assert rig.journal.records()[-1].category is DecisionCategory.LIMIT_EXCEEDED


def test_readfile_erro_e_negacao_nunca_contam_em_files_read(rig: Rig) -> None:
    rig.put("ok.txt", "x")
    rig.put(".env", "x")
    rig.put("bin.dat", b"\x00\x01")
    ok(rig.run(ReadFile("ok.txt")))
    denied(rig.run(ReadFile(".env")))
    errored(rig.run(ReadFile("bin.dat")), "not_text")
    errored(rig.run(ReadFile("nada.txt")), "not_found")
    ok(rig.run(ReadFile("ok.txt")))  # repetida: deduplica
    usage = rig.executor.usage()
    assert usage.files_read == ("ok.txt",)
    assert (usage.operations, usage.denials) == (5, 1)


def test_readfile_reasons_e_journal_nunca_vazam_caminho_absoluto(rig: Rig) -> None:
    results = [
        rig.run(ReadFile("nada.txt")),
        rig.run(ReadFile(".env")),
        rig.run(ReadFile(".git/x")),
        rig.run(ReadFile("/etc/passwd")),
    ]
    for result in results:
        assert str(rig.tmp_path) not in (result.reason or "")
    for record in rig.journal.records():
        assert str(rig.tmp_path) not in record.reason + record.subject_redacted


# ================================================================================ ListDirectory


def lines_of(content: str) -> list[str]:
    return content.split("\n") if content else []


def test_listdirectory_none_e_a_raiz_com_formato_estavel(rig: Rig) -> None:
    rig.put("b.txt")
    rig.put("a.txt")
    (rig.ws / "sub").mkdir()
    content = ok(rig.run(ListDirectory(None)))
    assert lines_of(content) == ["file\ta.txt", "file\tb.txt", "directory\tsub"]
    assert rig.executor.usage().files_read == ()
    assert str(rig.ws) not in content


def test_listdirectory_subdiretorio_usa_o_pipeline_normal(rig: Rig) -> None:
    rig.put("sub/x.py")
    rig.put("sub/deep/y.py")
    assert lines_of(ok(rig.run(ListDirectory("sub")))) == [
        "directory\tsub/deep",
        "file\tsub/x.py",
    ]
    assert lines_of(ok(rig.run(ListDirectory("sub\\deep")))) == ["file\tsub/deep/y.py"]
    assert rig.executor.usage().files_read == ()


@pytest.mark.parametrize("raw", [".", "./", "/", "\\", "..", "../x", "C:\\", "\\x"])
def test_listdirectory_strings_root_like_nao_sao_atalho_de_raiz(rig: Rig, raw: str) -> None:
    rig.put("a.txt")
    denied(rig.run(ListDirectory(raw)))


def test_listdirectory_dot_git_e_segredo_nao_aparecem_nem_pelo_nome(rig: Rig) -> None:
    (rig.ws / ".git").mkdir()
    rig.put(".git/config")
    rig.put(".env")
    rig.put(".env.local")
    rig.put("keys/server.pem")
    rig.put("normal.txt")
    content = ok(rig.run(ListDirectory(None)))
    # o diretório `keys` não é segredo (só o `.pem` dentro dele); `.git`, `.env`, `.env.local` são
    assert lines_of(content) == [
        "directory\tkeys",
        "file\tnormal.txt",
        "[3 entries omitted by policy]",
    ]
    for proibido in (".git", ".env", ".env.local"):
        assert proibido not in content
    # cada negação **real** usada para omitir vai ao journal (B-AUD-002); a operação segue OK
    assert len(rig.journal) == 3
    assert rig.executor.usage().denials == 0


def test_listdirectory_dentro_de_diretorio_filtra_segredo_sem_negar_a_listagem(rig: Rig) -> None:
    rig.put("keys/server.pem")
    rig.put("keys/ok.txt")
    content = ok(rig.run(ListDirectory("keys")))
    assert lines_of(content) == ["file\tkeys/ok.txt", "[1 entries omitted by policy]"]
    assert "server.pem" not in content


def test_listdirectory_link_ou_reparse_e_omitido_nao_seguido_e_nao_nomeado(rig: Rig) -> None:
    (rig.outside / "x.txt").write_text("fora")
    rig.put("a.txt")
    if not make_dir_link(rig.ws / "atalho", rig.outside):
        pytest.skip("link indisponível neste ambiente")
    content = ok(rig.run(ListDirectory(None)))
    assert "atalho" not in content and "x.txt" not in content
    assert "file\ta.txt" in content and "[1 entries omitted by policy]" in content


def test_listdirectory_ordenacao_deterministica_independe_do_scandir(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    for nome in ["c", "a", "B", "Z", "ab", "a b", "é", "_x", "0", "ä"]:
        rig.put(nome)
    scandir = ScandirOrder(monkeypatch)
    resultados = set()
    for reverse, shift in itertools.product((False, True), (0, 3, 7)):
        scandir.reverse, scandir.shift = reverse, shift
        resultados.add(ok(rig.run(ListDirectory(None))))
    assert len(resultados) == 1
    nomes = [line.split("\t")[1] for line in lines_of(resultados.pop())]
    assert nomes == sorted(nomes, key=lambda n: n.encode("utf-8"))


def test_listdirectory_limite_de_entradas_entregues_com_marcador_de_truncamento(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(list_directory_entries=5))
    for i in range(8):
        rig.put(f"f{i}.txt")
    content = ok(rig.run(ListDirectory(None)))
    assert lines_of(content) == [f"file\tf{i}.txt" for i in range(5)] + [
        "[truncated: 3 more entries not shown]"
    ]


def test_listdirectory_exatamente_1000_nao_trunca_e_1001_trunca(rig: Rig) -> None:
    for i in range(1000):
        rig.put(f"exato/f{i:04d}.txt", b"")
    for i in range(1001):
        rig.put(f"mais/f{i:04d}.txt", b"")
    exact = lines_of(ok(rig.run(ListDirectory("exato"))))
    assert len(exact) == 1000 and not any(line.startswith("[") for line in exact)
    more = lines_of(ok(rig.run(ListDirectory("mais"))))
    assert len(more) == 1001 and more[-1] == "[truncated: 1 more entries not shown]"
    assert sum(1 for line in more if line.startswith("file\t")) == 1000


def test_listdirectory_entradas_omitidas_por_politica_nao_consomem_slot_do_limite(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(list_directory_entries=3))
    rig.put(".env")
    rig.put(".env.local")
    (rig.ws / ".git").mkdir()
    for nome in "abcd":
        rig.put(nome)
    content = ok(rig.run(ListDirectory(None)))
    assert lines_of(content) == [
        "file\ta",
        "file\tb",
        "file\tc",
        "[3 entries omitted by policy]",
        "[truncated: 1 more entries not shown]",
    ]


def test_listdirectory_diretorio_vazio_e_ausente_e_arquivo(rig: Rig) -> None:
    (rig.ws / "vazio").mkdir()
    rig.put("f.txt")
    assert ok(rig.run(ListDirectory("vazio"))) == "[empty]"
    errored(rig.run(ListDirectory("nao-existe")), "not_found")
    errored(rig.run(ListDirectory("f.txt")), "not_directory")
    denied(rig.run(ListDirectory(".git")), "path.dot_git_denied")
    denied(rig.run(ListDirectory(".env")), "path.secret_denied")


def test_listdirectory_troca_do_diretorio_durante_a_enumeracao_e_denied(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("sub/a.txt")
    real = os.scandir

    class _Scanner:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def __enter__(self) -> Iterator[Any]:
            return iter(list(self._inner))

        def __exit__(self, *exc: object) -> None:
            self._inner.close()
            (rig.ws / "sub").rename(rig.ws / "sub_antigo")
            (rig.ws / "sub").mkdir()
            (rig.ws / "sub" / "a.txt").write_text("y")

    monkeypatch.setattr(os, "scandir", lambda path=".": _Scanner(real(path)))
    denied(rig.run(ListDirectory("sub")), "path.toctou_recheck_failed")
    assert rig.journal.records()[0].category is DecisionCategory.TOCTOU_RECHECK_FAILED


def test_listdirectory_cancelamento_inicial_e_no_meio_e_sticky(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    for i in range(20):
        rig.put(f"f{i}.txt")
    rig.token.cancelled = True
    errored(rig.run(ListDirectory(None)), "operation_cancelled")

    rig2 = Rig(tmp_path / "x", token=CountdownToken(after=8))
    for i in range(20):
        rig2.put(f"f{i}.txt")
    errored(rig2.run(ListDirectory(None)), "operation_cancelled")
    errored(rig2.run(ListDirectory(None)), "operation_cancelled")


def test_listdirectory_nao_expoe_identidade_volume_ou_caminho_absoluto(rig: Rig) -> None:
    rig.put("a.txt")
    content = ok(rig.run(ListDirectory(None)))
    assert str(rig.ws) not in content and str(rig.tmp_path) not in content
    assert not any(ch in content for ch in ("\\",))
    assert all(len(line.split("\t")) == 2 for line in lines_of(content))


@pytest.mark.skipif(sys.platform == "win32", reason="NTFS não aceita controle no nome")
def test_listdirectory_nome_com_caractere_de_controle_nao_injeta_linha(rig: Rig) -> None:
    rig.put("a\nfile\tinjetado.txt")
    rig.put("b.txt")
    content = ok(rig.run(ListDirectory(None)))
    assert "injetado" not in content and lines_of(content)[0] == "file\tb.txt"


def test_listdirectory_foundation_baseline_e_entradas_verificaveis(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("sub/a.txt")
    zero_ids(monkeypatch, keep={os.stat(rig.ws).st_ino})
    denied(rig.run(ListDirectory("sub")), "path.integrity_unverifiable")
    denied(rig.run(ListDirectory(None)), "path.integrity_unverifiable")


# ================================================================================== SearchText


def search(rig: Rig, query: str, path: str | None = None) -> list[str]:
    content = ok(rig.run(SearchText(query, path)))
    return lines_of(content)


def test_searchtext_literal_formato_e_files_read(rig: Rig) -> None:
    rig.put("a.py", "x = 1\nfoo = 2\nbar foo baz\n")
    rig.put("b.py", "nada\n")
    assert search(rig, "foo") == ["a.py:2:1:foo = 2", "a.py:3:5:bar foo baz"]
    assert rig.executor.usage().files_read == ("a.py",)  # b.py foi escaneado sem match


def test_searchtext_e_case_sensitive(rig: Rig) -> None:
    rig.put("a.txt", "foo\nFoo\nFOO\n")
    assert search(rig, "Foo") == ["a.txt:2:1:Foo"]
    assert search(rig, "foo") == ["a.txt:1:1:foo"]


def test_searchtext_nao_e_regex_nem_wildcard(rig: Rig) -> None:
    rig.put("a.txt", "abc\na.c\nx*y\n[z]\n(w)\n")
    assert search(rig, "a.c") == ["a.txt:2:1:a.c"]
    assert search(rig, "x*y") == ["a.txt:3:1:x*y"]
    assert search(rig, "[z]") == ["a.txt:4:1:[z]"]
    assert search(rig, "(w)") == ["a.txt:5:1:(w)"]
    assert search(rig, ".*") == ["[no matches]"]
    assert search(rig, "^abc$") == ["[no matches]"]


def test_searchtext_multiplas_ocorrencias_na_linha_e_crlf(rig: Rig) -> None:
    rig.put("a.txt", b"ab ab ab\r\nxx ab\r\n")
    assert search(rig, "ab") == [
        "a.txt:1:1:ab ab ab",
        "a.txt:1:4:ab ab ab",
        "a.txt:1:7:ab ab ab",
        "a.txt:2:4:xx ab",
    ]


def test_searchtext_query_limite_de_256_caracteres(rig: Rig) -> None:
    rig.put("a.txt", "x" * 256 + "\n")
    assert search(rig, "x" * 256) == [f"a.txt:1:1:{'x' * 256}"]
    denied(rig.run(SearchText("x" * 257)), "limit.search_query_chars")
    assert rig.journal.records()[-1].category is DecisionCategory.LIMIT_EXCEEDED


def test_searchtext_query_com_quebra_de_linha_e_erro_de_contrato(rig: Rig) -> None:
    errored(rig.run(SearchText("a\nb")), "invalid_query")
    errored(rig.run(SearchText("a\rb")), "invalid_query")


def test_searchtext_raiz_none_subdiretorio_e_arquivo_unico(rig: Rig) -> None:
    rig.put("a.txt", "needle\n")
    rig.put("sub/b.txt", "needle\n")
    rig.put("sub/deep/c.txt", "needle\n")
    assert search(rig, "needle") == [
        "a.txt:1:1:needle",
        "sub/b.txt:1:1:needle",
        "sub/deep/c.txt:1:1:needle",
    ]
    assert search(rig, "needle", "sub") == ["sub/b.txt:1:1:needle", "sub/deep/c.txt:1:1:needle"]
    assert search(rig, "needle", "sub\\deep\\c.txt") == ["sub/deep/c.txt:1:1:needle"]
    assert rig.executor.usage().files_read == ("a.txt", "sub/b.txt", "sub/deep/c.txt")


def test_searchtext_caminho_inexistente_dot_git_e_segredo(rig: Rig) -> None:
    (rig.ws / ".git").mkdir()
    errored(rig.run(SearchText("x", "nao-existe")), "not_found")
    denied(rig.run(SearchText("x", ".git")), "path.dot_git_denied")
    denied(rig.run(SearchText("x", ".env")), "path.secret_denied")
    denied(rig.run(SearchText("x", "/abs")))
    denied(rig.run(SearchText("x", ".")))


def test_searchtext_ordem_global_de_caminho_independe_do_scandir(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    for caminho in ["a.txt", "a/x.txt", "a0.txt", "b/y.txt", "B.txt", "é.txt", "_/z.txt"]:
        rig.put(caminho, "needle\n")
    scandir = ScandirOrder(monkeypatch)
    resultados = set()
    for reverse, shift in itertools.product((False, True), (0, 2, 5)):
        scandir.reverse, scandir.shift = reverse, shift
        resultados.add(tuple(search(rig, "needle")))
    assert len(resultados) == 1
    caminhos = [line.split(":", 1)[0] for line in resultados.pop()]
    assert caminhos == sorted(caminhos, key=lambda p: p.encode("utf-8"))
    assert caminhos.index("a.txt") < caminhos.index("a/x.txt") < caminhos.index("a0.txt")


def test_searchtext_200_matches_exatos_nao_trunca_e_201_trunca(rig: Rig) -> None:
    rig.put("exato.txt", "hit\n" * 200)
    assert len(search(rig, "hit", "exato.txt")) == 200
    rig.put("mais.txt", "hit\n" * 250)
    lines = search(rig, "hit", "mais.txt")
    assert len([line for line in lines if line.startswith("mais.txt:")]) == 200
    assert lines[-1] == "[results truncated: more than 200 matches]"


def test_searchtext_limite_de_matches_e_files_read_so_com_match_incluido(tmp_path: Path) -> None:
    rig = Rig(tmp_path, tool_limits=limits(search_max_matches=2))
    rig.put("a.txt", "hit\nhit\n")
    rig.put("b.txt", "hit\n")  # o 3º match: cortado
    rig.put("c.txt", "sem\n")
    lines = search(rig, "hit")
    assert lines == [
        "a.txt:1:1:hit",
        "a.txt:2:1:hit",
        "[results truncated: more than 2 matches]",
    ]
    assert rig.executor.usage().files_read == ("a.txt",)


def test_searchtext_snippet_de_ate_512_caracteres_contendo_o_casamento(rig: Rig) -> None:
    longa = "a" * 700 + "NEEDLE" + "b" * 700
    rig.put("a.txt", longa + "\n")
    (line,) = search(rig, "NEEDLE")
    snippet = line.split(":", 3)[3]
    assert len(snippet) == 512 and "NEEDLE" in snippet
    assert line.startswith("a.txt:1:701:")
    rig.put("b.txt", "NEEDLE" + "x" * 700 + "\n")
    (line_b,) = search(rig, "NEEDLE", "b.txt")
    assert len(line_b.split(":", 3)[3]) == 512
    rig.put("c.txt", "y" * 700 + "NEEDLE\n")
    (line_c,) = search(rig, "NEEDLE", "c.txt")
    assert len(line_c.split(":", 3)[3]) == 512 and line_c.split(":", 3)[3].endswith("NEEDLE")


def test_searchtext_arquivo_grande_invalido_e_binario_sao_pulados_sem_erro(tmp_path: Path) -> None:
    rig = Rig(tmp_path, tool_limits=limits(search_file_bytes=64))
    rig.put("grande.txt", "needle " + "x" * 200)
    rig.put("invalido.txt", b"needle \xff\xfe")
    rig.put("binario.txt", b"needle\x00bin")
    rig.put("ok.txt", "needle\n")
    lines = search(rig, "needle")
    assert lines[0] == "ok.txt:1:1:needle"
    assert lines[1].startswith("[3 files skipped")
    assert rig.executor.usage().files_read == ("ok.txt",)


def test_searchtext_arquivo_de_1_mib_real_e_pulado_e_1_mib_exato_e_pesquisado(rig: Rig) -> None:
    mib = 1024 * 1024
    rig.put("exato.txt", "needle\n" + "x" * (mib - 7))
    rig.put("acima.txt", "needle\n" + "x" * (mib - 6))
    lines = search(rig, "needle")
    assert lines[0].startswith("exato.txt:1:1:needle")
    assert any(line.startswith("[1 files skipped") for line in lines)
    assert not any(line.startswith("acima.txt:") for line in lines)


def test_searchtext_arquivo_que_cresce_durante_a_leitura_e_pulado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(search_file_bytes=8))
    rig.put("cresce.txt", "needle")
    monkeypatch.setattr(fsx, "read_fd", lambda fd, max_bytes, **k: b"n" * (max_bytes + 1))
    lines = search(rig, "n")
    assert lines[0] == "[no matches]" and any("skipped" in line for line in lines)


def test_searchtext_segredo_dot_git_e_politica_nao_sao_pesquisados_nem_nomeados(rig: Rig) -> None:
    (rig.ws / ".git").mkdir()
    rig.put(".git/config", "needle")
    rig.put(".env", "needle")
    rig.put("secrets/api.txt", "needle")
    rig.put("keys/server.pem", "needle")
    rig.put("publico.txt", "needle\n")
    content = ok(rig.run(SearchText("needle")))
    assert "publico.txt:1:1:needle" in content
    for proibido in (".git", ".env", "secrets", "server.pem", "api.txt"):
        assert proibido not in content
    assert "[4 paths skipped by policy]" in content
    assert rig.executor.usage().files_read == ("publico.txt",)
    assert (
        len(rig.journal) == 4
    )  # `.git`, `.env`, `secrets` e `server.pem`: uma por caminho omitido
    assert rig.executor.usage().denials == 0


def test_searchtext_nao_segue_link_nem_reparse(rig: Rig) -> None:
    (rig.outside / "x.txt").write_text("needle")
    if not make_dir_link(rig.ws / "atalho", rig.outside):
        pytest.skip("link indisponível neste ambiente")
    rig.put("a.txt", "needle\n")
    content = ok(rig.run(SearchText("needle")))
    assert "x.txt" not in content and "atalho" not in content
    assert "a.txt:1:1:needle" in content and "[1 paths skipped by policy]" in content


def test_searchtext_limite_de_arquivos_visitados_com_marcador_explicito(tmp_path: Path) -> None:
    rig = Rig(tmp_path, tool_limits=limits(search_max_files=5))
    for i in range(8):
        rig.put(f"f{i}.txt", "hit\n")
    lines = search(rig, "hit")
    assert [line.split(":", 1)[0] for line in lines if line.startswith("f")] == [
        f"f{i}.txt" for i in range(5)
    ]
    assert lines[-1] == "[search incomplete: file limit of 5 reached]"
    assert rig.executor.usage().files_read == tuple(f"f{i}.txt" for i in range(5))


def test_searchtext_exatamente_o_limite_de_arquivos_nao_e_incompleto(tmp_path: Path) -> None:
    rig = Rig(tmp_path, tool_limits=limits(search_max_files=5))
    for i in range(5):
        rig.put(f"f{i}.txt", "hit\n")
    assert not any(line.startswith("[search incomplete") for line in search(rig, "hit"))


def test_searchtext_prazo_zero_e_erro_timeout_nunca_ok_completo(tmp_path: Path) -> None:
    rig = Rig(tmp_path, tool_limits=limits(search_deadline_s=0))
    rig.put("a.txt", "hit\n")
    errored(rig.run(SearchText("hit")), "timeout")
    assert rig.executor.usage().files_read == ()
    # timeout não é cancelamento: o executor segue utilizável
    errored(rig.run(SearchText("hit")), "timeout")
    assert rig.run(ReadFile("a.txt")).status is ToolStatus.OK


def test_searchtext_prazo_estourando_no_meio_do_percurso(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path)
    for i in range(10):
        rig.put(f"f{i}.txt", "hit\n")
    tempo = itertools.count(0, 7)  # cada leitura do relógio avança 7 s: passa de 20 s no meio
    monkeypatch.setattr(fsx.time, "monotonic", lambda: float(next(tempo)))
    errored(rig.run(SearchText("hit")), "timeout")
    assert rig.executor.usage().files_read == ()


def test_searchtext_cancelamento_e_sticky_e_distinto_de_timeout(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.put("a.txt", "hit\n")
    rig.token.cancelled = True
    errored(rig.run(SearchText("hit")), "operation_cancelled")
    rig.token.cancelled = False
    errored(rig.run(SearchText("hit")), "operation_cancelled")

    rig2 = Rig(tmp_path / "x", token=CountdownToken(after=12))
    for i in range(20):
        rig2.put(f"f{i}.txt", "hit\n")
    errored(rig2.run(SearchText("hit")), "operation_cancelled")
    errored(rig2.run(SearchText("hit")), "operation_cancelled")


def test_searchtext_io_ambiguo_nao_vira_ok_vazio(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.put("a.txt", "hit\n")
    rig.put("b.txt", "hit\n")
    real = fsx.open_existing

    def quebrado(facts: Any, root: Any, **kwargs: Any) -> Any:
        if str(facts.canonical_target).endswith("b.txt"):
            raise PathOperationFailed(PathFailure.IO_ERROR)
        return real(facts, root, **kwargs)

    monkeypatch.setattr(fsx, "open_existing", quebrado)
    errored(rig.run(SearchText("hit")), "io_error")
    assert rig.executor.usage().files_read == ()


def test_searchtext_arquivo_que_some_durante_a_busca_e_erro(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "hit\n")
    rig.put("b.txt", "hit\n")

    def some(n: int) -> None:
        if n == 3:  # base, a.txt, **b.txt**: some logo depois da inspeção do próprio b.txt
            (rig.ws / "b.txt").unlink()

    after_inspect(monkeypatch, some)
    result = rig.run(SearchText("hit"))
    assert result.status is ToolStatus.ERROR  # nunca um OK que finge ter varrido tudo


def test_searchtext_violacao_de_integridade_no_arquivo_e_denied(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "hit\n")
    real = fsx.open_existing

    def viola(facts: Any, root: Any, **kwargs: Any) -> Any:
        raise PathIntegrityViolation(IntegrityFailure.TARGET_IDENTITY_CHANGED)

    monkeypatch.setattr(fsx, "open_existing", viola)
    denied(rig.run(SearchText("hit")), "path.toctou_recheck_failed")
    assert real is not viola


def test_searchtext_redacao_so_depois_do_casamento(rig: Rig) -> None:
    rig.put("config.py", f"TOKEN = '{TOKEN_LIKE}'\n")
    # a consulta casa com o dado **cru**; só o que sai é redigido
    content = ok(rig.run(SearchText(TOKEN_LIKE[:20])))
    assert TOKEN_LIKE not in content
    assert content.startswith("config.py:1:10:")
    assert rig.executor.usage().files_read == ("config.py",)


def test_searchtext_sem_matches_e_sem_caminho_absoluto(rig: Rig) -> None:
    rig.put("a.txt", "x\n")
    content = ok(rig.run(SearchText("zzz")))
    assert content == "[no matches]"
    rig.put("b.txt", "zzz\n")
    assert str(rig.tmp_path) not in ok(rig.run(SearchText("zzz")))


def test_searchtext_so_texto_utf8_e_um_arquivo_binario_unico_vira_nota(rig: Rig) -> None:
    rig.put("bin.dat", b"\x00\x01needle")
    lines = search(rig, "needle", "bin.dat")
    assert lines[0] == "[no matches]" and lines[1].startswith("[1 files skipped")


# ================================================================================== WriteFile


def test_writefile_cria_arquivo_novo_e_nao_conta_files_read(rig: Rig) -> None:
    content = ok(rig.run(WriteFile("novo.txt", "olá\n")))
    assert content == "created novo.txt (5 bytes)"
    assert (rig.ws / "novo.txt").read_bytes() == "olá\n".encode()
    assert rig.executor.usage().files_read == ()


def test_writefile_cria_diretorios_pais_novos(rig: Rig) -> None:
    ok(rig.run(WriteFile("a/b/c/novo.txt", "x")))
    assert (rig.ws / "a" / "b" / "c" / "novo.txt").read_bytes() == b"x"
    ok(rig.run(WriteFile("d\\e\\f.txt", "y")))
    assert (rig.ws / "d" / "e" / "f.txt").read_bytes() == b"y"


def test_writefile_conteudo_vazio_cria_arquivo_vazio(rig: Rig) -> None:
    assert ok(rig.run(WriteFile("vazio.txt", ""))) == "created vazio.txt (0 bytes)"
    assert (rig.ws / "vazio.txt").read_bytes() == b""


def test_writefile_grava_utf8_sem_bom_e_sem_traduzir_quebras_de_linha(rig: Rig) -> None:
    ok(rig.run(WriteFile("a.txt", "é\r\nb\nc\r")))
    assert (rig.ws / "a.txt").read_bytes() == "é\r\nb\nc\r".encode()


def test_writefile_faz_uma_unica_inspecao_mediada_com_a_flag(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-AUD-003 (fecho): CREATE e UPDATE usam **uma** inspeção, `tolerate_absent_tail=True`."""
    chamadas: list[dict[str, Any]] = []
    real = fsx.inspect

    def espia(*args: Any, **kwargs: Any) -> Any:
        chamadas.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(fsx, "inspect", espia)
    ok(rig.run(WriteFile("novo.txt", "a")))  # CREATE
    ok(rig.run(WriteFile("novo.txt", "b")))  # UPDATE
    assert chamadas == [{"tolerate_absent_tail": True}] * 2


def test_writefile_corrida_arquivo_aparece_antes_do_o_excl_nunca_sobrescreve(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def aparece(n: int) -> None:
        if n == 1:  # depois da (única) inspeção, antes do `O_EXCL`
            (rig.ws / "corrida.txt").write_text("outro processo")

    after_inspect(monkeypatch, aparece)
    errored(rig.run(WriteFile("corrida.txt", "meu")), "already_exists")
    assert (rig.ws / "corrida.txt").read_text() == "outro processo"
    assert len(rig.journal) == 0


def test_writefile_post_create_negado_remove_o_vazio_e_e_denied(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        fsx,
        "decide_post_create",
        lambda facts, policy=None: SafetyDecision(False, "path.toctou_recheck_failed", "x", "n"),
    )
    spy: list[bytes] = []
    real_write = fsx.write_fd

    def spy_write(*args: Any, **kwargs: Any) -> None:
        spy.append(b"!")
        real_write(*args, **kwargs)

    monkeypatch.setattr(fsx, "write_fd", spy_write)
    denied(rig.run(WriteFile("novo.txt", "conteudo")), "path.toctou_recheck_failed")
    assert not (rig.ws / "novo.txt").exists()  # o vazio criado foi removido por identidade
    assert spy == []  # nenhum byte foi escrito antes da decisão
    assert rig.journal.records()[0].category is DecisionCategory.TOCTOU_RECHECK_FAILED


def test_writefile_post_create_negado_com_cleanup_impossivel_mantem_o_vazio_e_o_denied(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        fsx,
        "decide_post_create",
        lambda facts, policy=None: SafetyDecision(False, "path.post_create_reparse", "x", "n"),
    )
    monkeypatch.setattr(fsx, "discard_created", lambda created: False)  # não dá para provar
    denied(rig.run(WriteFile("novo.txt", "conteudo")), "path.post_create_reparse")
    assert (rig.ws / "novo.txt").read_bytes() == b""  # permanece, vazio; nada de unlink inseguro


def test_writefile_post_create_com_falha_tecnica_de_inspecao_e_erro(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        fsx,
        "decide_post_create",
        lambda facts, policy=None: SafetyDecision(
            False, "path.post_create_inspection_failed", "x", "n"
        ),
    )
    errored(rig.run(WriteFile("novo.txt", "x")), "path.post_create_inspection_failed")
    assert len(rig.journal) == 0


def test_writefile_identidade_zero_pos_criacao_e_denied_e_nenhum_byte(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    zero_ids(monkeypatch, keep={os.stat(rig.ws).st_ino})
    denied(rig.run(WriteFile("novo.txt", "conteudo")), "path.integrity_unverifiable")
    assert (rig.ws / "novo.txt").read_bytes() == b""  # o vazio fica; nenhum byte de conteúdo


def test_writefile_cancelamento_antes_de_qualquer_io_nao_cria_nada(rig: Rig) -> None:
    rig.token.cancelled = True
    errored(rig.run(WriteFile("novo.txt", "x")), "operation_cancelled")
    assert not (rig.ws / "novo.txt").exists()


def test_writefile_cancelamento_antes_de_escrever_remove_o_vazio_provadamente_nosso(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = fsx.write_fd

    def cancela_e_escreve(fd: int, data: bytes, **kwargs: Any) -> None:
        rig.token.cancelled = True
        real(fd, data, **kwargs)

    monkeypatch.setattr(fsx, "write_fd", cancela_e_escreve)
    errored(rig.run(WriteFile("novo.txt", "conteudo")), "operation_cancelled")
    assert not (rig.ws / "novo.txt").exists()
    errored(rig.run(ReadFile("novo.txt")), "operation_cancelled")  # sticky


def test_writefile_update_substitui_o_conteudo_e_encolhe(rig: Rig) -> None:
    rig.put("a.txt", "conteudo bem longo que sera substituido")
    assert ok(rig.run(WriteFile("a.txt", "curto"))) == "updated a.txt (5 bytes)"
    assert (rig.ws / "a.txt").read_bytes() == b"curto"
    assert rig.executor.usage().files_read == ()


def test_writefile_update_so_trunca_depois_da_decisao_pos_open(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "original")
    monkeypatch.setattr(
        fsx,
        "decide_post_open",
        lambda facts, policy=None: SafetyDecision(False, "path.toctou_recheck_failed", "x", "a"),
    )
    denied(rig.run(WriteFile("a.txt", "novo")), "path.toctou_recheck_failed")
    assert (rig.ws / "a.txt").read_bytes() == b"original"  # nada foi truncado


def test_writefile_update_alvo_que_some_entre_inspecao_e_abertura_e_erro(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "original")
    after_inspect(monkeypatch, lambda n: (rig.ws / "a.txt").unlink())
    errored(rig.run(WriteFile("a.txt", "novo")), "not_found")
    assert not (rig.ws / "a.txt").exists()  # e nada foi (re)criado por baixo dos panos


def test_writefile_update_alvo_trocado_apos_a_inspecao_e_denied_e_o_novo_nao_e_tocado(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "original")

    def troca(n: int) -> None:
        other = rig.ws / "outro.txt"
        other.write_text("trocado")
        os.replace(other, rig.ws / "a.txt")

    after_inspect(monkeypatch, troca)
    denied(rig.run(WriteFile("a.txt", "novo")), "path.toctou_recheck_failed")
    assert (rig.ws / "a.txt").read_text() == "trocado"


def test_writefile_update_reparse_que_aparece_e_denied(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "original")

    def vira_link(n: int) -> None:
        (rig.ws / "a.txt").unlink()
        if not make_dir_link(rig.ws / "a.txt", rig.outside):
            pytest.skip("link indisponível neste ambiente")

    after_inspect(monkeypatch, vira_link)
    denied(rig.run(WriteFile("a.txt", "novo")), "path.reparse_detected")
    assert list(rig.outside.iterdir()) == []


def test_writefile_cancelamento_antes_de_truncar_preserva_o_original(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "original intacto")
    real = fsx.write_fd

    def cancela_antes(fd: int, data: bytes, **kwargs: Any) -> None:
        rig.token.cancelled = True
        real(fd, data, **kwargs)  # checa o cancelamento **antes** de `ftruncate`

    monkeypatch.setattr(fsx, "write_fd", cancela_antes)
    errored(rig.run(WriteFile("a.txt", "novo")), "operation_cancelled")
    assert (rig.ws / "a.txt").read_bytes() == b"original intacto"


def test_writefile_diretorio_como_alvo_e_erro(rig: Rig) -> None:
    (rig.ws / "dir").mkdir()
    errored(rig.run(WriteFile("dir", "x")), "is_directory")


def test_writefile_pai_que_e_arquivo_e_erro_e_nada_e_criado(rig: Rig) -> None:
    rig.put("a", "sou arquivo")
    errored(rig.run(WriteFile("a/b/c.txt", "x")), "not_directory")
    assert (rig.ws / "a").read_bytes() == b"sou arquivo"


@pytest.mark.parametrize("raw", [".git/config", ".GIT/x", "sub/.git/hooks/pre-commit"])
def test_writefile_dot_git_e_negado_e_nada_e_criado(rig: Rig, raw: str) -> None:
    denied(rig.run(WriteFile(raw, "x")), "path.dot_git_denied")
    assert not (rig.ws / ".git").exists() and not (rig.ws / "sub").exists()


@pytest.mark.parametrize("raw", [".env", "sub/.env.local", "keys/id_rsa", "x.pem"])
def test_writefile_segredo_e_negado_e_nada_e_criado(rig: Rig, raw: str) -> None:
    denied(rig.run(WriteFile(raw, "x")), "path.secret_denied")
    assert not (rig.ws / raw).exists()
    assert rig.journal.records()[-1].category is DecisionCategory.SECRET_ACCESS_BLOCKED


@pytest.mark.parametrize("raw", ["/abs.txt", "\\abs.txt", "../fora.txt", "C:\\x.txt", "a/../../b"])
def test_writefile_caminhos_perigosos_sao_negados(rig: Rig, raw: str) -> None:
    denied(rig.run(WriteFile(raw, "x")))
    assert list(rig.tmp_path.glob("fora.txt")) == []


def test_writefile_escrita_em_diretorio_link_e_negada_e_nada_vaza_para_fora(rig: Rig) -> None:
    if not make_dir_link(rig.ws / "atalho", rig.outside):
        pytest.skip("link indisponível neste ambiente")
    denied(rig.run(WriteFile("atalho/novo.txt", "x")))
    assert list(rig.outside.iterdir()) == []


def test_writefile_limite_de_1_mib_utf8_em_bytes(tmp_path: Path) -> None:
    rig = Rig(tmp_path, tool_limits=limits(write_file_bytes=10))
    ok(rig.run(WriteFile("dez.txt", "é" * 5)))  # 10 bytes
    denied(rig.run(WriteFile("doze.txt", "é" * 6)), "limit.write_file_bytes")  # 12 bytes, 6 chars
    assert not (rig.ws / "doze.txt").exists()
    assert rig.journal.records()[-1].category is DecisionCategory.LIMIT_EXCEEDED


def test_writefile_1_mib_real_exato_e_ok_e_um_byte_a_mais_e_negado(rig: Rig) -> None:
    mib = 1024 * 1024
    ok(rig.run(WriteFile("exato.txt", "x" * mib)))
    assert (rig.ws / "exato.txt").stat().st_size == mib
    denied(rig.run(WriteFile("acima.txt", "x" * (mib + 1))), "limit.write_file_bytes")
    assert not (rig.ws / "acima.txt").exists()


@pytest.mark.parametrize("marker", ["«redigido»", "antes «redigido» depois", "«chave redigida 3»"])
def test_writefile_conteudo_com_marcador_de_redacao_e_negado(rig: Rig, marker: str) -> None:
    rig.put("existente.txt", "intacto")
    denied(rig.run(WriteFile("novo.txt", marker)), "content.redaction_marker")
    denied(rig.run(WriteFile("existente.txt", marker)), "content.redaction_marker")
    assert not (rig.ws / "novo.txt").exists()
    assert (rig.ws / "existente.txt").read_bytes() == b"intacto"
    assert rig.journal.records()[-1].category is DecisionCategory.SECRET_ACCESS_BLOCKED


def test_writefile_surrogate_solto_nao_e_texto_utf8(rig: Rig) -> None:
    errored(rig.run(WriteFile("a.txt", "x\udcff")), "not_text")
    assert not (rig.ws / "a.txt").exists()


def test_writefile_e_readfile_ida_e_volta_e_files_read_so_do_read(rig: Rig) -> None:
    ok(rig.run(WriteFile("sub/a.txt", "linha\n")))
    assert ok(rig.run(ReadFile("sub/a.txt"))) == "linha\n"
    assert rig.executor.usage().files_read == ("sub/a.txt",)


def test_writefile_reasons_e_journal_nunca_vazam_caminho_absoluto(rig: Rig) -> None:
    results = [
        rig.run(WriteFile(".git/x", "x")),
        rig.run(WriteFile(".env", "x")),
        rig.run(WriteFile("/abs", "x")),
        rig.run(WriteFile("a.txt", "«redigido»")),
    ]
    for result in results:
        assert str(rig.tmp_path) not in (result.reason or "")
    for record in rig.journal.records():
        assert str(rig.tmp_path) not in record.reason + record.subject_redacted


# ===================================================================== executor: gate e fronteira


@pytest.mark.parametrize(
    "request_",
    [ReadFile("a"), ListDirectory(None), SearchText("x"), WriteFile("a", "x")],
    ids=lambda r: type(r).__name__,
)
def test_capability_gate_continua_antes_de_qualquer_io_nas_quatro(
    request_: Any, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.tool_executor import executor as executor_module

    capability = {
        "ReadFile": "read_files",
        "ListDirectory": "read_files",
        "SearchText": "read_files",
        "WriteFile": "write_files",
    }[type(request_).__name__]
    rig.executor._profile = dataclasses.replace(
        DEVELOPER_V1_PROFILE, **{capability: EnforcementMode.DISABLED}
    )

    def proibido(*_a: object, **_k: object) -> None:
        raise AssertionError("IO antes do capability gate")

    monkeypatch.setattr(executor_module, "verify_root", proibido)
    for nome in ("inspect", "open_existing", "list_directory", "create_exclusive"):
        monkeypatch.setattr(fsx, nome, proibido)
    denied(rig.run(request_), "capability.denied")


def test_as_quatro_operacoes_git_fora_de_repositorio_sao_erro_tecnico(rig: Rig) -> None:
    """E7.5-D implementou as quatro de Git; o `ws` aqui não é repositório: `ERROR git_failed`."""
    from app.tool_executor.contracts import (
        GitDiff,
        GitListTree,
        GitShow,
        GitStatus,
    )

    for request in (GitStatus(), GitDiff(), GitShow("HEAD"), GitListTree()):
        errored(rig.run(request), "git_failed")
    assert rig.reasons() == []


def test_ordenacao_de_usage_e_deterministica_com_varias_operacoes(rig: Rig) -> None:
    for nome in ("z.txt", "B.txt", "a.txt", "é.txt"):
        rig.put(nome, "hit\n")
    for nome in ("z.txt", "é.txt", "B.txt", "a.txt"):
        ok(rig.run(ReadFile(nome)))
    ok(rig.run(SearchText("hit")))
    assert rig.executor.usage().files_read == tuple(
        sorted(("z.txt", "B.txt", "a.txt", "é.txt"), key=lambda n: n.encode("utf-8"))
    )


def test_fs_ops_nunca_usa_atalhos_de_arquivo_nem_subprocess() -> None:
    import ast

    tree = ast.parse(Path(FS_OPS_FILE).read_text(encoding="utf-8"))
    proibidos = {
        "read_text", "read_bytes", "write_text", "write_bytes", "rglob", "glob", "walk",
        "listdir", "scandir", "copy", "copyfile", "unlink", "remove", "mkdir", "open",
        "system", "Popen", "run",
    }  # fmt: skip
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            nome = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            assert nome not in proibidos, f"fsx chama `{nome}`"
        if isinstance(node, ast.Import | ast.ImportFrom):
            modulos = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            assert not any(
                m.split(".")[0] in {"os", "shutil", "subprocess", "pathlib"} for m in modulos
            )


def test_outcome_e_handler_context_ficam_internos(rig: Rig) -> None:
    assert Outcome.__module__ == "app.tool_executor.outcome"
    assert HandlerContext.__module__ == "app.tool_executor.outcome"
    publicos = {n for n in dir(rig.executor.mediated_tools()) if not n.startswith("_")}
    assert publicos == {"execute"}


def test_reason_fixa_para_cada_regra_nova() -> None:
    assert reason_for("content.redaction_marker").startswith("content.redaction_marker:")
    assert reason_for("limit.tool_result_content_bytes")
    for code in ("not_text", "timeout", "invalid_query"):
        assert reason_for(code).startswith(f"{code}:")
