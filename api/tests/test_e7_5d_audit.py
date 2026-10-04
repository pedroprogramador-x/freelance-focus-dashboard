"""E7.5-D — correções dirigidas da auditoria: D-AUD-001, D-AUD-002, D-AUD-003 e D-AUD-004.

* **D-AUD-001** — *intent-to-add* não aparece como limpo: além do inventário (`ls-files
  --stage`), o índice é observado por `diff-index --cached --ita-invisible-in-index --raw -z`
  (árvore × índice; nunca o disco, nunca patch).
* **D-AUD-002** — entradas duplicadas do índice nunca são colapsadas: a validação por caminho
  roda **antes** de qualquer mapa. Os contrafactuais usam um índice v2 **real** escrito pelo teste.
* **D-AUD-003** — no `GitDiff`, índice divergente do commit gera `index\\t<p>` **sempre**,
  independentemente do filesystem.
* **D-AUD-004** — um cancelamento visto quando o Supervisor devolve vence até `EXITED` completo.

Repositórios reais em diretório temporário; sentinel benigno para configuração hostil.
"""

from __future__ import annotations

import hashlib
import shutil
import struct
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.git_runtime import mediated
from app.git_runtime.mediated import (
    EntryKind,
    GitFailure,
    IndexEntry,
    MediatedGit,
    MediatedGitError,
)
from app.process_runtime import ProcessOutcome, ProcessResult
from app.tool_executor import git_ops
from app.tool_executor.contracts import GitDiff, GitListTree, GitShow, GitStatus, ToolStatus
from tests.test_git_mediated_e7_5d import (
    GIT,
    GitRig,
    _armed_repo,
    fake_git,
    fake_result,
    git,
    lines,
    subcommand,
)
from tests.test_tool_file_ops_e7_5b import denied, errored, limits, ok

md: Any = mediated
pytestmark = pytest.mark.skipif(GIT is None, reason="git ausente")


@pytest.fixture
def rig(tmp_path: Path) -> GitRig:
    return GitRig(tmp_path)


def blob(rig: GitRig, data: bytes) -> str:
    assert GIT is not None
    done = subprocess.run(  # noqa: S603 — fixture de teste
        [GIT, "hash-object", "-w", "--stdin"], cwd=rig.repo, input=data, capture_output=True
    )
    return done.stdout.decode().strip()


def write_index(rig: GitRig, entries: list[tuple[str, int, str]]) -> None:
    """Escreve um índice v2 **válido** (assinatura, versão, entradas alinhadas, SHA-1 final)
    com exatamente as entradas dadas, na ordem dada: `(caminho, estágio, oid)`."""
    body = b"DIRC" + struct.pack(">II", 2, len(entries))
    for path, stage, oid in entries:
        name = path.encode("utf-8")
        entry = struct.pack(">10I", 0, 0, 0, 0, 0, 0, 0o100644, 0, 0, 0) + bytes.fromhex(oid)
        entry += struct.pack(">H", (stage << 12) | min(len(name), 0xFFF)) + name
        entry += b"\0" * (8 - len(entry) % 8)
        body += entry
    body += hashlib.sha1(body, usedforsecurity=False).digest()
    (rig.repo / ".git" / "index").write_bytes(body)


# ================================================================ D-AUD-001: intent-to-add


def test_ita_de_arquivo_novo_nao_e_limpo(rig: GitRig) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.put("new.txt", "novo\n")
    rig.g("add", "--intent-to-add", "new.txt")
    assert "staged\tnew.txt" in lines(ok(rig.run(GitStatus())))
    content = ok(rig.run(GitDiff()))
    assert content != "[no differences]" and lines(content)[0] == "index\tnew.txt"


def test_ita_de_arquivo_vazio_do_base_contrafactual(rig: GitRig) -> None:
    """O finding: `ls-files --stage` sai idêntico ao base (oid do blob vazio)."""
    rig.put("a.txt", b"")
    rig.commit()
    rig.g("rm", "-q", "--cached", "a.txt")
    rig.g("add", "--intent-to-add", "a.txt")
    stage = rig.g("ls-files", "--stage", "a.txt")
    assert stage.split()[1] == rig.g("rev-parse", "HEAD:a.txt")  # inventário igual ao base
    assert ok(rig.run(GitStatus())) == "staged\ta.txt"
    assert ok(rig.run(GitDiff())) == "index\ta.txt"
    assert rig.executor.usage().files_read == ()


def test_ita_de_arquivo_nao_vazio_do_base(rig: GitRig) -> None:
    rig.put("a.txt", "conteudo\n")
    rig.commit()
    rig.g("rm", "-q", "--cached", "a.txt")
    rig.g("add", "--intent-to-add", "a.txt")
    # o índice (ITA = blob vazio) diverge do base **e** o disco diverge do índice
    assert lines(ok(rig.run(GitStatus()))) == ["modified\ta.txt", "staged\ta.txt"]
    assert ok(rig.run(GitDiff())) == "index\ta.txt"


def test_ita_desfeito_volta_a_ser_limpo(rig: GitRig) -> None:
    rig.put("a.txt", b"")
    rig.commit()
    rig.g("rm", "-q", "--cached", "a.txt")
    rig.g("add", "--intent-to-add", "a.txt")
    assert ok(rig.run(GitStatus())) != "[clean]"
    rig.g("add", "a.txt")  # índice normal, igual ao base
    assert ok(rig.run(GitStatus())) == "[clean]"
    assert ok(rig.run(GitDiff())) == "[no differences]"


def test_ita_sob_configuracao_hostil_nada_executa(rig: GitRig) -> None:
    sentinel = _armed_repo(rig)
    (rig.ws / "a.txt").write_bytes(b"a\n")  # disco volta ao base; só o índice diverge
    desarmado = (  # só para preparar a fixture; o código testado nunca precisa disso
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "filter.evil.clean=",
        "-c",
        "filter.evil.smudge=",
        "-c",
        "filter.evil.process=",
    )
    git(rig.repo, *desarmado, "rm", "-q", "--cached", "a.txt")
    git(rig.repo, *desarmado, "add", "--intent-to-add", "a.txt")
    assert not sentinel.exists()
    subprocess.run(  # noqa: S603 — controle vivo: o porcelain roda o diff externo
        [str(GIT), "diff", "--cached", "HEAD"], cwd=rig.repo, check=False, capture_output=True
    )
    assert sentinel.exists(), "a fixture não armou nada"
    sentinel.unlink()
    rig.start()
    assert "staged\ta.txt" in lines(ok(rig.run(GitStatus())))
    assert "index\ta.txt" in lines(ok(rig.run(GitDiff())))
    assert not sentinel.exists()


def test_diff_index_truncado_bem_formado_nunca_e_lido(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.start()
    prefix = b":100644 000000 " + b"a" * 40 + b" " + b"0" * 40 + b" D\0a.txt\0"
    fake_git(monkeypatch, {"diff-index": fake_result(prefix, code=1, truncated=True)})
    errored(rig.run(GitStatus()), "git_output_unverifiable")
    errored(rig.run(GitDiff()), "git_output_unverifiable")


@pytest.mark.parametrize(
    ("stdout", "code"),
    [
        (b"", 1),  # diverge sem registro
        (b":100644 000000 " + b"a" * 40 + b" " + b"0" * 40 + b" D\0a.txt\0", 0),  # 0 com saída
        (b"lixo\0a.txt\0", 1),
        (b":100644 000000 " + b"a" * 40 + b" " + b"0" * 40 + b" D\0", 1),  # sem caminho
        (b"", 128),
    ],
)
def test_diff_index_incoerente_e_inverificavel(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch, stdout: bytes, code: int
) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.start()
    fake_git(monkeypatch, {"diff-index": fake_result(stdout, code=code)})
    errored(rig.run(GitStatus()), "git_output_unverifiable")


def test_diff_index_cancelado_na_volta(rig: GitRig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.start()
    real = md.run_supervised

    def cancela_na_volta(spec: Any, is_cancelled: Callable[[], bool]) -> ProcessResult:
        result: ProcessResult = real(spec, is_cancelled)
        if subcommand(spec.argv) == "diff-index":
            rig.token.cancelled = True
        return result

    monkeypatch.setattr(md, "run_supervised", cancela_na_volta)
    errored(rig.run(GitStatus()), "operation_cancelled")


# ======================================================== D-AUD-002: duplicatas do índice


def _dup_rig(rig: GitRig) -> tuple[str, str]:
    rig.put("a.txt", "A\n")
    rig.commit()
    return rig.g("rev-parse", "HEAD:a.txt"), blob(rig, b"B\n")


@pytest.mark.parametrize("order", ["A,B", "B,A"])
def test_duas_entradas_estagio_zero_contrafactual(rig: GitRig, order: str) -> None:
    a, b = _dup_rig(rig)
    first, second = (a, b) if order == "A,B" else (b, a)
    write_index(rig, [("a.txt", 0, first), ("a.txt", 0, second)])
    assert rig.g("ls-files", "--stage").count("a.txt") == 2  # o Git emite as duas
    results = [rig.run(GitStatus()), rig.run(GitDiff())]
    for result in results:
        errored(result, "git_output_unverifiable")
    assert rig.rules() == []


@pytest.mark.parametrize(
    "stages",
    [
        [(0, "a"), (0, "a")],  # duplicata idêntica
        [(1, "a"), (1, "b"), (2, "b")],
        [(1, "a"), (2, "a"), (2, "b")],
        [(1, "a"), (3, "a"), (3, "b")],
        [(0, "a"), (1, "b")],
        [(0, "a"), (2, "b"), (3, "a")],
    ],
    ids=["mesmo-oid", "estagio1", "estagio2", "estagio3", "zero+1", "zero+2+3"],
)
def test_combinacoes_invalidas_de_estagio(rig: GitRig, stages: list[tuple[int, str]]) -> None:
    a, b = _dup_rig(rig)
    oid = {"a": a, "b": b}
    write_index(rig, [("a.txt", stage, oid[key]) for stage, key in stages])
    for request in (GitStatus(), GitDiff(), GitDiff(None, "a.txt")):
        errored(rig.run(request), "git_output_unverifiable")


def test_conflito_valido_1_2_3_nunca_e_limpo(rig: GitRig) -> None:
    a, b = _dup_rig(rig)
    write_index(rig, [("a.txt", 1, a), ("a.txt", 2, b), ("a.txt", 3, a)])
    assert ok(rig.run(GitStatus())) == "modified\ta.txt"
    content = ok(rig.run(GitDiff()))
    assert lines(content) == ["index\ta.txt"]  # disco igual ao base; o conflito não some


def test_validacao_de_estagios_unitaria() -> None:
    def entry(path: str, stage: int) -> IndexEntry:
        return IndexEntry(path, "100644", EntryKind.FILE, "a" * 40, stage)

    mediated._validate_index_entries([entry("a", 0), entry("b", 0)])
    mediated._validate_index_entries([entry("a", 1), entry("a", 2), entry("a", 3)])
    mediated._validate_index_entries([entry("a", 2), entry("a", 3)])
    for bad in (
        [entry("a", 0), entry("a", 0)],
        [entry("a", 0), entry("a", 1)],
        [entry("a", 2), entry("a", 2)],
    ):
        with pytest.raises(MediatedGitError) as caught:
            mediated._validate_index_entries(bad)
        assert caught.value.failure is GitFailure.OUTPUT_UNVERIFIABLE


def test_duplicata_na_arvore_tambem_e_inverificavel(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.start()
    oid = rig.g("rev-parse", "HEAD:a.txt").encode()
    record = b"100644 blob " + oid + b" 2\ta.txt\0"
    fake_git(monkeypatch, {"ls-tree": fake_result(record * 2)})
    errored(rig.run(GitListTree()), "git_output_unverifiable")


# ===================================================== D-AUD-003: índice × filesystem no diff


def _matrix(rig: GitRig, index: bytes | None, disk: bytes | None) -> None:
    """base `A`; índice e disco conforme pedido (`None` = sem mudança no índice / apagado)."""
    rig.put("a.txt", "A\n")
    rig.commit()
    if index is not None:
        rig.put("a.txt", index)
        rig.g("add", "a.txt")
    if disk is None:
        (rig.ws / "a.txt").unlink()
    else:
        rig.put("a.txt", disk)


DIFF_A_TO = {
    b"B\n": ["--- a/a.txt", "+++ b/a.txt", "@@ -1,1 +1,1 @@", "-A", "+B"],
    b"C\n": ["--- a/a.txt", "+++ b/a.txt", "@@ -1,1 +1,1 @@", "-A", "+C"],
}


@pytest.mark.parametrize(
    ("index", "disk", "expected", "read"),
    [
        (None, b"A\n", ["[no differences]"], ()),  # A/A/A
        (b"B\n", b"A\n", ["index\ta.txt"], ()),  # A/B/A
        (b"B\n", b"B\n", ["index\ta.txt", *DIFF_A_TO[b"B\n"]], ("a.txt",)),  # A/B/B
        (b"B\n", b"C\n", ["index\ta.txt", *DIFF_A_TO[b"C\n"]], ("a.txt",)),  # A/B/C (finding)
        (None, b"C\n", DIFF_A_TO[b"C\n"], ("a.txt",)),  # A/A/C
        (b"B\n", b"C\x00\n", ["index\ta.txt", "binary\ta.txt"], ()),
        (
            b"B\n",
            None,
            ["index\ta.txt", "--- a/a.txt", "+++ /dev/null", "@@ -1,1 +0,0 @@", "-A"],
            ("a.txt",),
        ),
    ],
    ids=["AAA", "ABA", "ABB", "ABC", "AAC", "AB-binario", "AB-apagado"],
)
def test_matriz_base_indice_disco(
    rig: GitRig,
    index: bytes | None,
    disk: bytes | None,
    expected: list[str],
    read: tuple[str, ...],
) -> None:
    _matrix(rig, index, disk)
    assert lines(ok(rig.run(GitDiff()))) == expected
    assert rig.executor.usage().files_read == read


def test_indice_divergente_e_conteudo_grande(tmp_path: Path) -> None:
    rig = GitRig(tmp_path, tool_limits=limits(git_internal_capture_bytes=400))
    _matrix(rig, b"B\n", b"C" * 500 + b"\n")
    assert lines(ok(rig.run(GitDiff()))) == ["index\ta.txt", "large\ta.txt"]
    assert rig.executor.usage().files_read == ()


def test_criado_no_indice_e_no_disco(rig: GitRig) -> None:
    rig.put("a.txt", "A\n")
    rig.commit()
    rig.put("n.txt", "novo\n")
    rig.g("add", "n.txt")
    assert lines(ok(rig.run(GitDiff()))) == [
        "index\tn.txt",
        "--- /dev/null",
        "+++ b/n.txt",
        "@@ -0,0 +1,1 @@",
        "+novo",
    ]


def test_ordem_por_caminho_marcador_antes_do_disco(rig: GitRig) -> None:
    rig.put("b.txt", "1\n")
    rig.put("a.txt", "1\n")
    rig.commit()
    for name in ("b.txt", "a.txt"):
        rig.put(name, "2\n")
        rig.g("add", name)
        rig.put(name, "3\n")
    out = lines(ok(rig.run(GitDiff())))
    assert [line for line in out if line.startswith(("index", "--- "))] == [
        "index\ta.txt",
        "--- a/a.txt",
        "index\tb.txt",
        "--- a/b.txt",
    ]


def test_caminho_negado_nao_aparece_nem_como_marcador(rig: GitRig) -> None:
    rig.put(".env", "K=1\n")
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.put(".env", "K=2\n")
    rig.g("add", ".env")
    rig.put(".env", "K=3\n")
    content = ok(rig.run(GitDiff()))
    assert ".env" not in content and content == "[no differences]\n[1 paths omitted by policy]"
    assert rig.rules() == ["path.secret_denied"]


def test_teto_final_com_marcador_nega_sem_files_read(tmp_path: Path) -> None:
    rig = GitRig(tmp_path, tool_limits=limits(tool_result_content_bytes=60))
    _matrix(rig, b"B\n", b"".join(b"linha %d\n" % i for i in range(20)))
    denied(rig.run(GitDiff()), "limit.tool_result_content_bytes")
    assert rig.executor.usage().files_read == ()


# =========================================================== D-AUD-004: cancelamento na volta


def _completion_race(
    monkeypatch: pytest.MonkeyPatch, rig: GitRig, *, after: int, before: bool = False
) -> list[str]:
    """Ativa o token na ``after``-ésima fronteira de processo: **depois** que o Supervisor
    devolveu (padrão — o processo já saiu e a árvore foi confirmada morta) ou **antes** de
    iniciar (``before``). Devolve os subcomandos que chegaram a rodar."""
    real = md.run_supervised
    started: list[str] = []

    def race(spec: Any, is_cancelled: Callable[[], bool]) -> ProcessResult:
        if before and len(started) + 1 == after:
            rig.token.cancelled = True
        started.append(subcommand(spec.argv))
        result: ProcessResult = real(spec, is_cancelled)
        if before and len(started) == after:
            # O Supervisor viu o token antes do spawn: nenhum processo existiu.
            assert result.outcome is ProcessOutcome.CANCELLED and result.duration_ms >= 0
        if not before and len(started) == after:
            assert result.tree_confirmed_dead
            rig.token.cancelled = True
        return result

    monkeypatch.setattr(md, "run_supervised", race)
    return started


def test_cancelamento_na_conclusao_do_ultimo_cat_file_contrafactual(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O finding: o último `cat-file blob` sai, o token é ativado durante a limpeza, e o
    Supervisor devolve `EXITED`. O conteúdo já lido nunca vira `OK` nem `files_read`."""
    rig.put("a.txt", "conteudo\n")
    rig.commit()
    rig.start()
    before = rig.state()
    real = md.run_supervised
    vistos: list[ProcessOutcome] = []

    def cancela_na_volta(spec: Any, is_cancelled: Callable[[], bool]) -> ProcessResult:
        result: ProcessResult = real(spec, is_cancelled)
        if subcommand(spec.argv) == "cat-file":
            vistos.append(result.outcome)
            assert result.tree_confirmed_dead and result.stdout == b"conteudo\n"
            rig.token.cancelled = True
        return result

    monkeypatch.setattr(md, "run_supervised", cancela_na_volta)
    result = rig.run(GitShow("HEAD", "a.txt"))
    errored(result, "operation_cancelled")
    assert vistos == [ProcessOutcome.EXITED]
    assert rig.executor.usage().files_read == ()
    assert rig.state() == before
    errored(rig.run(GitListTree()), "operation_cancelled")  # sticky


def _ops_rig(rig: GitRig) -> None:
    rig.put("a.txt", "a\n")
    rig.put("b.txt", "b\n")
    rig.commit()
    rig.put("a.txt", "A\n")


OPS = [GitStatus(), GitListTree(), GitShow("HEAD"), GitShow("HEAD", "a.txt"), GitDiff()]


@pytest.mark.parametrize(
    "request_", OPS, ids=lambda r: f"{type(r).__name__}-{getattr(r, 'path', '')}"
)
@pytest.mark.parametrize("before", [False, True], ids=["na-conclusao", "antes-do-spawn"])
def test_varredura_de_fronteiras_de_processo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request_: Any, before: bool
) -> None:
    """Cancela em **cada** fronteira de processo. Enquanto houver processo a cancelar, o
    resultado é `operation_cancelled` sem `files_read`; depois da última, `OK`."""
    processos = 0
    for after in range(1, 20):
        rig = GitRig(tmp_path / f"{after}")
        _ops_rig(rig)
        rig.start()
        before_state = rig.state()
        with monkeypatch.context() as mp:
            started = _completion_race(mp, rig, after=after, before=before)
            result = rig.run(request_)
        if result.status is ToolStatus.OK:
            processos = len(started)
            assert after > processos
            break
        errored(result, "operation_cancelled")
        assert rig.executor.usage().files_read == ()
        assert rig.state() == before_state
        assert len(started) == after  # nenhuma fronteira depois da que cancelou
    else:
        pytest.fail("a varredura não alcançou o fim")
    assert processos >= 2


@pytest.mark.parametrize(
    "request_", [GitStatus(), GitDiff(), GitListTree()], ids=lambda r: type(r).__name__
)
def test_cancelamento_entre_dois_processos_nao_inicia_o_seguinte(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch, request_: Any
) -> None:
    """O token é ativado enquanto a saída de um processo é interpretada (depois da checagem
    de volta dele): o processo seguinte nunca é iniciado."""
    _ops_rig(rig)
    rig.start()
    real_freeze = md._freeze
    spawned: list[str] = []
    real_run = md.run_supervised

    def conta(spec: Any, is_cancelled: Callable[[], bool]) -> ProcessResult:
        spawned.append(subcommand(spec.argv))
        result: ProcessResult = real_run(spec, is_cancelled)
        return result

    def cancela_no_parse(*args: Any, **kwargs: Any) -> Any:
        rig.token.cancelled = True
        return real_freeze(*args, **kwargs)

    monkeypatch.setattr(md, "run_supervised", conta)
    monkeypatch.setattr(md, "_freeze", cancela_no_parse)
    errored(rig.run(request_), "operation_cancelled")
    assert spawned[-1] == "ls-tree"  # o primeiro parse com `_freeze`; nada depois dele
    assert rig.executor.usage().files_read == ()


def test_cancelamento_entre_ler_o_blob_e_o_tool_result(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.start()
    real = git_ops._text

    def cancela_depois_de_decodificar(data: bytes) -> str | None:
        text = real(data)
        rig.token.cancelled = True
        return text

    monkeypatch.setattr(git_ops, "_text", cancela_depois_de_decodificar)
    errored(rig.run(GitShow("HEAD", "a.txt")), "operation_cancelled")
    assert rig.executor.usage().files_read == ()


def test_run_central_cancelamento_vence_exited_completo(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    flag = {"cancel": False}

    def exited_e_cancela(spec: Any, is_cancelled: Callable[[], bool]) -> ProcessResult:
        flag["cancel"] = True
        return fake_result(b"sha1\n\n")

    monkeypatch.setattr(md, "run_supervised", exited_e_cancela)
    gitx = MediatedGit(
        str(rig.ws), expected_prefix="", is_cancelled=lambda: flag["cancel"], capture_bytes=1024
    )
    with pytest.raises(MediatedGitError) as caught:
        gitx.object_format  # noqa: B018 — a leitura dispara o processo
    assert caught.value.failure is GitFailure.CANCELLED


def test_token_que_lanca_conta_como_cancelado(rig: GitRig) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()

    def quebra() -> bool:
        raise RuntimeError("token quebrado")

    gitx = MediatedGit(str(rig.ws), expected_prefix="", is_cancelled=quebra, capture_bytes=1024)
    with pytest.raises(MediatedGitError) as caught:
        gitx.object_format  # noqa: B018
    assert caught.value.failure is GitFailure.CANCELLED


def test_nenhum_git_roda_com_token_ja_cancelado(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    chamadas: list[object] = []
    monkeypatch.setattr(md, "run_supervised", lambda *a: chamadas.append(a))
    gitx = MediatedGit(str(rig.ws), expected_prefix="", is_cancelled=lambda: True, capture_bytes=9)
    with pytest.raises(MediatedGitError):
        gitx.object_format  # noqa: B018
    assert chamadas == []
    assert shutil.which("git") is not None
