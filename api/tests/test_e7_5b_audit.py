"""E7.5-B — regressões de B-AUD-001, B-AUD-002 e B-AUD-003.

* **B-AUD-001** — `files_read` precisa corresponder ao que foi **efetivamente entregue** em um
  `ToolResult OK`, depois da redação e do teto final: um arquivo cujo match foi cortado não conta.
* **B-AUD-002** — cada negação **real** de safety usada para omitir uma entrada/caminho em
  `ListDirectory`/`SearchText` vai para a trilha de auditoria (journal), sem nomear o proibido no
  resultado e sem aumentar `MediatedUsage.denials`.
* **B-AUD-003** — falha **técnica/ambígua de inspeção** (lstat com `EIO`, `PermissionError`,
  `inspection_error`) é `ERROR` da operação: não é "path proibido", não é omissão silenciosa, não
  é `[no matches]` e não gera `SafetyEvent` de política.

Cada teste foi escrito para **falhar** no código anterior (contrafactual). Só fixtures benignas;
as falhas de `lstat` e a expansão da redação são *mocks*.
"""

from __future__ import annotations

import dataclasses
import errno
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.safety import Tri
from app.tool_executor import executor as executor_module
from app.tool_executor import fs_ops
from app.tool_executor.contracts import (
    DecisionCategory,
    ListDirectory,
    ReadFile,
    SearchText,
    ToolStatus,
)
from tests.test_tool_file_ops_e7_5b import (
    CountdownToken,
    Rig,
    denied,
    errored,
    limits,
    lines_of,
    make_dir_link,
    ok,
)

fsx: Any = fs_ops

#: Marcador de teste: a "redação" **expande** `NEEDLE` (6 bytes) para 26 bytes.
EXPANDED = "[XXXXXXXXXXXXXXXXXXXX]"


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def expanding_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """A redação passa a **crescer** o texto (como pode ocorrer com um marcador longo)."""
    monkeypatch.setattr(executor_module, "redact", lambda text: text.replace("NEEDLE", EXPANDED))


class FailingJournal:
    def __init__(self) -> None:
        self.calls = 0

    def append(self, record: Any) -> None:
        self.calls += 1
        raise OSError("trilha indisponível: C:\\caminho\\absoluto")


# ======================================================================== B-AUD-001


def files_in(content: str) -> set[str]:
    """(só nos testes) os caminhos dos fragmentos de match presentes no texto entregue."""
    return {
        line.split(":", 1)[0] for line in content.split("\n") if re.match(r"^[^:\[]+:\d+:", line)
    }


def test_b_aud_001_saida_acima_do_teto_apos_a_redacao_e_denied_e_nenhum_arquivo_conta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cabe **antes** da redação, não cabe **depois**: fail closed — nada é entregue nem contado."""
    rig = Rig(tmp_path, tool_limits=limits(tool_result_content_bytes=150))
    rig.put("a.txt", "NEEDLE\n" * 6)
    rig.put("z.txt", "NEEDLE\n")
    expanding_redaction(monkeypatch)
    denied(rig.run(SearchText("NEEDLE")), "limit.tool_result_content_bytes")
    assert rig.executor.usage().files_read == ()  # nem a.txt, nem z.txt
    assert rig.journal.records()[-1].category is DecisionCategory.LIMIT_EXCEEDED


def test_b_aud_001_tudo_ou_nada_a_mesma_busca_passa_de_todos_contados_para_nenhum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expanding_redaction(monkeypatch)
    cabe = Rig(tmp_path / "cabe", tool_limits=limits(tool_result_content_bytes=4000))
    nao_cabe = Rig(tmp_path / "nao", tool_limits=limits(tool_result_content_bytes=150))
    for rig in (cabe, nao_cabe):
        rig.put("a.txt", "NEEDLE\n" * 10)
        rig.put("b.txt", "NEEDLE\n")
    content = ok(cabe.run(SearchText("NEEDLE")))
    assert len([line for line in lines_of(content) if line.startswith("a.txt:")]) == 10
    assert cabe.executor.usage().files_read == ("a.txt", "b.txt")
    denied(nao_cabe.run(SearchText("NEEDLE")), "limit.tool_result_content_bytes")
    assert nao_cabe.executor.usage().files_read == ()


def test_b_aud_001_se_tudo_cabe_depois_da_redacao_todos_contam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(tool_result_content_bytes=4000))
    rig.put("a.txt", "NEEDLE\n")
    rig.put("z.txt", "NEEDLE\n")
    expanding_redaction(monkeypatch)
    content = ok(rig.run(SearchText("NEEDLE")))
    assert files_in(content) == {"a.txt", "z.txt"}
    assert rig.executor.usage().files_read == ("a.txt", "z.txt")
    assert "truncated by output limit" not in content


@pytest.mark.parametrize("limite", list(range(20, 260, 7)))
def test_b_aud_001_invariante_ok_entrega_tudo_e_conta_tudo_denied_nao_conta_nada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limite: int
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(tool_result_content_bytes=limite))
    for nome, repeticoes in (("a.txt", 4), ("b.txt", 1), ("m.txt", 3), ("z.txt", 2)):
        rig.put(nome, "NEEDLE\n" * repeticoes)
    expanding_redaction(monkeypatch)
    result = rig.run(SearchText("NEEDLE"))
    if result.status is ToolStatus.OK:
        content = ok(result)
        assert len(content.encode("utf-8")) <= limite  # em BYTES, depois da redação
        assert files_in(content) == {"a.txt", "b.txt", "m.txt", "z.txt"}  # a saída é a completa
        assert set(rig.executor.usage().files_read) == files_in(content)
        assert "truncated by output limit" not in content  # nunca há truncamento final
        assert len([ln for ln in lines_of(content) if ln.endswith(EXPANDED)]) == 10
    else:
        denied(result, "limit.tool_result_content_bytes")
        assert rig.executor.usage().files_read == ()


def test_b_aud_001_unicode_fica_integro_ok_inteiro_ou_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expanding_redaction(monkeypatch)
    pequeno = Rig(tmp_path / "p", tool_limits=limits(tool_result_content_bytes=90))
    grande = Rig(tmp_path / "g", tool_limits=limits(tool_result_content_bytes=1000))
    for rig in (pequeno, grande):
        rig.put("a.txt", ("ção NEEDLE ação 日本\n") * 8)
    denied(pequeno.run(SearchText("NEEDLE")), "limit.tool_result_content_bytes")
    content = ok(grande.run(SearchText("NEEDLE")))
    content.encode("utf-8").decode("utf-8")  # UTF-8 válido
    assert len(content.encode("utf-8")) <= 1000
    assert all(line.endswith("日本") for line in lines_of(content))
    assert len(lines_of(content)) == 8


def test_b_aud_001_nao_existe_marcador_de_truncamento_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expanding_redaction(monkeypatch)
    for teto in (120, 400, 4000, 262144):
        rig = Rig(tmp_path / f"t{teto}", tool_limits=limits(tool_result_content_bytes=teto))
        rig.put("a.txt", "NEEDLE\n" * 20)
        result = rig.run(SearchText("NEEDLE"))
        assert "truncated by output limit" not in (result.content or "")
        assert result.status is ToolStatus.DENIED or len((result.content or "").encode()) <= teto


def test_b_aud_001_teto_minusculo_e_denied_sem_proveniencia(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(tool_result_content_bytes=10))
    rig.put("a.txt", "NEEDLE\n" * 3)
    expanding_redaction(monkeypatch)
    denied(rig.run(SearchText("NEEDLE")), "limit.tool_result_content_bytes")
    assert rig.executor.usage().files_read == ()


def test_b_aud_001_readfile_negado_depois_da_redacao_nao_conta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(tool_result_content_bytes=40))
    rig.put("a.txt", "NEEDLE " * 4)  # 28 bytes cru: cabe; redigido: 26*4 — não cabe
    expanding_redaction(monkeypatch)
    denied(rig.run(ReadFile("a.txt")), "limit.tool_result_content_bytes")
    assert rig.executor.usage().files_read == ()


def test_b_aud_001_listdirectory_acima_do_teto_final_e_denied_sem_cortar_entradas(
    tmp_path: Path,
) -> None:
    pequeno = Rig(tmp_path / "p", tool_limits=limits(tool_result_content_bytes=80))
    grande = Rig(tmp_path / "g", tool_limits=limits(tool_result_content_bytes=4000))
    for rig in (pequeno, grande):
        for i in range(10):
            rig.put(f"arquivo{i}.txt", "x")
    denied(pequeno.run(ListDirectory(None)), "limit.tool_result_content_bytes")
    content = ok(grande.run(ListDirectory(None)))
    assert [
        line for line in lines_of(content) if not re.fullmatch(r"file\tarquivo\d\.txt", line)
    ] == []
    assert pequeno.executor.usage().files_read == grande.executor.usage().files_read == ()


# ======================================================================== B-AUD-002


def build_filter_fixture(rig: Rig) -> None:
    (rig.ws / ".git").mkdir()
    rig.put(".git/config", "NEEDLE")
    rig.put(".git/objects/aa", "NEEDLE")
    rig.put(".env", "NEEDLE")
    rig.put("secrets/api.txt", "NEEDLE")
    rig.put("safe.txt", "NEEDLE ok\n")


def test_b_aud_002_listdirectory_registra_cada_negacao_e_continua_ok(rig: Rig) -> None:
    build_filter_fixture(rig)
    content = ok(rig.run(ListDirectory(None)))
    for proibido in (".git", ".env", "secrets", "api.txt"):
        assert proibido not in content
    assert "file\tsafe.txt" in content and "[3 entries omitted by policy]" in content
    records = rig.journal.records()
    assert [r.rule_id for r in records] == [
        "path.secret_denied",  # `.env`
        "path.dot_git_denied",  # `.git` (a ordem é a dos bytes: `.env` < `.git`)
        "path.secret_denied",  # `secrets` (diretório)
    ] or sorted(r.rule_id for r in records) == sorted(
        ["path.secret_denied", "path.dot_git_denied", "path.secret_denied"]
    )
    assert len(records) == 3  # uma por entrada omitida, **uma só** por entrada
    assert {r.operation for r in records} == {"ListDirectory"}
    assert [r.sequence for r in records] == [1, 2, 3]
    categories = {r.rule_id: r.category for r in records}
    assert categories["path.dot_git_denied"] is DecisionCategory.PATH_DENIED
    assert categories["path.secret_denied"] is DecisionCategory.SECRET_ACCESS_BLOCKED
    assert all(r.run_scope.run_id == "r1" for r in records)


def test_b_aud_002_o_record_nao_tem_reason_cru_nem_caminho_absoluto(rig: Rig) -> None:
    build_filter_fixture(rig)
    ok(rig.run(ListDirectory(None)))
    for record in rig.journal.records():
        assert str(rig.tmp_path) not in record.reason + record.subject_redacted
        assert record.reason.startswith(f"{record.rule_id}: ")


def test_b_aud_002_denials_do_usage_nao_conta_filtro_interno(rig: Rig) -> None:
    build_filter_fixture(rig)
    ok(rig.run(ListDirectory(None)))
    ok(rig.run(SearchText("NEEDLE")))
    usage = rig.executor.usage()
    assert (usage.operations, usage.denials) == (2, 0)  # só `ToolResult DENIED` conta
    assert len(rig.journal) > 0


def test_b_aud_002_searchtext_registra_a_negacao_do_diretorio_uma_vez_e_nao_desce(
    rig: Rig,
) -> None:
    build_filter_fixture(rig)
    content = ok(rig.run(SearchText("NEEDLE")))
    assert content.split("\n")[0] == "safe.txt:1:1:NEEDLE ok"
    for proibido in (".git", ".env", "secrets", "api.txt", "objects", "config"):
        assert proibido not in content
    assert "[3 paths skipped by policy]" in content
    records = rig.journal.records()
    assert len(records) == 3  # `.env`, `.git` (o diretório), `secrets` (o diretório)
    assert {r.subject_redacted for r in records} == {".env", ".git", "secrets"}  # nada sob eles
    assert {r.operation for r in records} == {"SearchText"}
    assert rig.executor.usage().files_read == ("safe.txt",)


def test_b_aud_002_negacao_de_link_e_registrada_uma_vez(rig: Rig) -> None:
    (rig.outside / "x.txt").write_text("NEEDLE")
    if not make_dir_link(rig.ws / "atalho", rig.outside):
        pytest.skip("link indisponível neste ambiente")
    rig.put("a.txt", "NEEDLE\n")
    ok(rig.run(SearchText("NEEDLE")))
    (record,) = rig.journal.records()
    assert record.category is DecisionCategory.PATH_DENIED and record.subject_redacted == "atalho"


def test_b_aud_002_falha_do_journal_no_meio_do_filtro_e_error_e_poison(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.journal = FailingJournal()  # type: ignore[assignment]
    rig.executor._journal = rig.journal  # o executor passa a usar a trilha que falha
    build_filter_fixture(rig)
    first = rig.run(ListDirectory(None))
    errored(first, "journal_unavailable")
    errored(rig.run(ListDirectory(None)), "journal_unavailable")  # sticky
    errored(rig.run(ReadFile("safe.txt")), "journal_unavailable")
    assert rig.executor.usage().denials == 0 and rig.executor.usage().files_read == ()
    assert rig.journal.calls == 1  # type: ignore[attr-defined]


def test_b_aud_002_falha_do_journal_depois_de_entradas_ja_processadas(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.journal = FailingJournal()  # type: ignore[assignment]
    rig.executor._journal = rig.journal
    rig.put("a.txt", "NEEDLE\n")
    rig.put("b.txt", "NEEDLE\n")
    rig.put("secrets/k.txt", "NEEDLE\n")  # ordenado depois de a.txt e b.txt
    result = rig.run(SearchText("NEEDLE"))
    errored(result, "journal_unavailable")  # nunca um OK com os dois primeiros arquivos
    assert rig.executor.usage().files_read == ()
    errored(rig.run(SearchText("NEEDLE")), "journal_unavailable")


# ======================================================================== B-AUD-003


def eio_on(monkeypatch: pytest.MonkeyPatch, name: str, error: BaseException) -> None:
    """`Path.lstat` falha para o caminho cujo último componente é ``name`` (e só para ele)."""
    real = Path.lstat

    def fake(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == name:
            raise error
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", fake)


ERROS = [
    OSError(errno.EIO, "erro de E/S"),
    PermissionError(errno.EACCES, "negado"),
    OSError(errno.EINVAL, "genérico"),
]
IDS = ["eio", "permissao", "oserror"]


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_b_aud_003_searchtext_com_falha_de_inspecao_e_error_nunca_ok_vazio(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("alvo.txt", "NEEDLE aqui\n")
    rig.put("outro.txt", "nada\n")
    eio_on(monkeypatch, "alvo.txt", erro)
    result = rig.run(SearchText("NEEDLE"))
    errored(result, "path_inspection_failed")  # antes: OK `[no matches]` + `[1 paths skipped...]`
    assert rig.executor.usage().files_read == ()
    assert len(rig.journal) == 0  # nenhum SafetyEvent de política para um erro de E/S
    assert str(rig.tmp_path) not in (result.reason or "")
    assert "errno" not in (result.reason or "").lower() and "EIO" not in (result.reason or "")


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_b_aud_003_listdirectory_com_falha_de_inspecao_e_error_nao_omissao(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("alvo.txt")
    rig.put("outro.txt")
    eio_on(monkeypatch, "alvo.txt", erro)
    errored(rig.run(ListDirectory(None)), "path_inspection_failed")  # antes: OK com omissão
    assert len(rig.journal) == 0
    assert rig.executor.usage().denials == 0


def test_b_aud_003_inspection_error_nos_fatos_tambem_e_error(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "NEEDLE\n")
    rig.put("b.txt", "NEEDLE\n")
    real = fsx.inspect

    def com_erro(relative: str, *args: Any, **kwargs: Any) -> Any:
        facts = real(relative, *args, **kwargs)
        if relative == "b.txt":
            return dataclasses.replace(facts, inspection_error="alvo irresolúvel: [Errno 5] C:\\x")
        return facts

    monkeypatch.setattr(fsx, "inspect", com_erro)
    result = rig.run(SearchText("NEEDLE"))
    errored(result, "path_inspection_failed")
    assert "Errno" not in (result.reason or "") and "C:" not in (result.reason or "")
    assert len(rig.journal) == 0


def test_b_aud_003_unknown_que_nao_e_falha_de_inspecao_continua_sendo_politica(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Só **um** fato `UNKNOWN` (propriedade não comprovável) não é falha de coleta: segue
    fail closed pela política — omitido e registrado, não `ERROR`."""
    rig.put("a.txt", "NEEDLE\n")
    rig.put("b.txt", "NEEDLE\n")
    real = fsx.inspect

    def so_junction(relative: str, *args: Any, **kwargs: Any) -> Any:
        facts = real(relative, *args, **kwargs)
        if relative == "b.txt":
            return dataclasses.replace(facts, is_junction=Tri.UNKNOWN)
        return facts

    monkeypatch.setattr(fsx, "inspect", so_junction)
    content = ok(rig.run(SearchText("NEEDLE")))
    assert content.split("\n")[0] == "a.txt:1:1:NEEDLE"
    assert "[1 paths skipped by policy]" in content
    (record,) = rig.journal.records()
    assert record.rule_id == "path.junction_unverified"


def test_b_aud_003_arquivo_que_some_durante_a_busca_e_error(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "NEEDLE\n")
    rig.put("b.txt", "NEEDLE\n")
    real = fsx.inspect
    chamadas = {"n": 0}

    def some(relative: str, *args: Any, **kwargs: Any) -> Any:
        facts = real(relative, *args, **kwargs)
        chamadas["n"] += 1
        if chamadas["n"] == 3:
            (rig.ws / "b.txt").unlink()
        return facts

    monkeypatch.setattr(fsx, "inspect", some)
    result = rig.run(SearchText("NEEDLE"))
    assert result.status is ToolStatus.ERROR
    assert len(rig.journal) == 0


# ===================================================================== interação


def test_interacao_1_negacao_registrada_e_depois_falha_de_inspecao_continua_error(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put(".env", "NEEDLE")  # negado pela política: ordenado antes de `z.txt`
    rig.put("z.txt", "NEEDLE\n")
    eio_on(monkeypatch, "z.txt", OSError(errno.EIO, "erro de E/S"))
    errored(rig.run(SearchText("NEEDLE")), "path_inspection_failed")
    (record,) = rig.journal.records()  # a negação **real** anterior permanece registrada
    assert record.rule_id == "path.secret_denied" and record.subject_redacted == ".env"
    assert rig.executor.usage().denials == 0 and rig.executor.usage().files_read == ()


def test_interacao_2_multiplos_matches_expansao_limite_final_e_segredo_pulado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(tool_result_content_bytes=160))
    rig.put(".env", "NEEDLE\n")
    rig.put("a.txt", "NEEDLE\n" * 3)
    rig.put("b.txt", "NEEDLE\n" * 3)
    rig.put("z.txt", "NEEDLE\n" * 3)
    expanding_redaction(monkeypatch)
    denied(rig.run(SearchText("NEEDLE")), "limit.tool_result_content_bytes")
    assert rig.executor.usage().files_read == ()  # nada foi entregue: nada conta
    rules = [r.rule_id for r in rig.journal.records()]
    assert rules == ["path.secret_denied", "limit.tool_result_content_bytes"]
    assert rig.executor.usage().denials == 1  # só o `ToolResult DENIED` final


def test_interacao_3_falha_do_journal_depois_de_entradas_ja_processadas_na_listagem(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, token=CountdownToken(after=10_000))
    rig.journal = FailingJournal()  # type: ignore[assignment]
    rig.executor._journal = rig.journal
    for nome in ("a.txt", "b.txt", "c.txt"):
        rig.put(nome)
    rig.put("secrets/k.txt")  # `secrets` (diretório) é a primeira negação, depois de a, b, c
    errored(rig.run(ListDirectory(None)), "journal_unavailable")
    errored(rig.run(ListDirectory(None)), "journal_unavailable")
    assert rig.executor.usage().denials == 0


_: Callable[..., Any] = ok


def test_b_aud_001_caso_do_auditor_com_o_limite_real_de_256_kib(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cabe em 256 KiB **antes** da redação; **depois** ela cresce: DENIED, nada contado."""
    monkeypatch.setattr(executor_module, "redact", lambda t: t.replace("PAD", EXPANDED))
    rig.put("a.txt", ("PAD " * 120 + "hit\n") * 190)
    rig.put("z.txt", "PAD hit\n")
    denied(rig.run(SearchText("hit")), "limit.tool_result_content_bytes")
    assert rig.executor.usage().files_read == ()
