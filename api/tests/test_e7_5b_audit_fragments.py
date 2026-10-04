"""E7.5-B (fecho) — o finalizador por **fragmentos** e a porta interna de negação.

Exercita o executor por dentro (via `monkeypatch` dos métodos privados, como nos testes da
foundation): a proveniência de `files_read` é **estruturada** (`OutputFragment`), nunca deduzida
do texto final, e só é incorporada depois que o `ToolResult` existe.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from app.safety import SafetyDecision
from app.tool_executor import executor as executor_module
from app.tool_executor.contracts import (
    DecisionCategory,
    ListDirectory,
    ReadFile,
    SearchText,
    ToolStatus,
)
from app.tool_executor.executor import LocalWorktreeToolExecutor
from app.tool_executor.outcome import (
    HandlerContext,
    InspectionFailed,
    Outcome,
    OutputFragment,
)
from app.tool_executor.reasons import ERROR_PHRASES
from app.tool_executor.validation import ContractViolation
from tests.test_tool_file_ops_e7_5b import Rig, errored, limits, lines_of, ok


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def fake_search(monkeypatch: pytest.MonkeyPatch, outcome: Outcome) -> None:
    monkeypatch.setattr(
        LocalWorktreeToolExecutor, "_search_text", lambda self, context, request: outcome
    )


def test_outcome_fragments_excluem_content_e_files_read() -> None:
    OutputFragment("x", ("a",))
    Outcome(ToolStatus.OK, fragments=(OutputFragment("x"),))
    with pytest.raises(ContractViolation):
        Outcome(ToolStatus.OK, content="x", fragments=(OutputFragment("x"),))
    with pytest.raises(ContractViolation):
        Outcome(ToolStatus.OK, files_read=("a",), fragments=(OutputFragment("x"),))


def test_fragmento_e_interno_frozen_e_sem_caminho_absoluto_no_repr(rig: Rig) -> None:
    fragment = OutputFragment("texto", ("a.txt",))
    with pytest.raises(dataclasses.FrozenInstanceError):
        fragment.text = "x"  # type: ignore[misc]
    assert str(rig.ws) not in repr(fragment)
    assert not hasattr(fragment, "__dict__")  # slots


def test_proveniencia_e_estruturada_e_nao_depende_do_formato_do_texto(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Texto com `:`, colchetes e Unicode: o caminho vem do fragmento, não de um parse."""
    fake_search(
        monkeypatch,
        Outcome(
            ToolStatus.OK,
            fragments=(
                OutputFragment("::: [não é um match] :::", ("estranho:caminho[1].txt",)),
                OutputFragment("[nota sem proveniência]"),
                OutputFragment("ação:日本:ç", ("ação/日本.txt",)),
            ),
        ),
    )
    ok(rig.run(SearchText("x")))
    assert rig.executor.usage().files_read == ("ação/日本.txt", "estranho:caminho[1].txt")


def test_proveniencia_e_a_uniao_de_todos_os_fragmentos_quando_cabe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(tool_result_content_bytes=100))
    fake_search(
        monkeypatch,
        Outcome(
            ToolStatus.OK,
            fragments=(
                OutputFragment("a" * 20, ("primeiro",)),
                OutputFragment("b" * 20, ("segundo",)),
                OutputFragment("[nota]"),
                OutputFragment("c" * 20, ("terceiro", "primeiro")),
            ),
        ),
    )
    content = ok(rig.run(SearchText("x")))
    assert content == "\n".join(["a" * 20, "b" * 20, "[nota]", "c" * 20])
    assert rig.executor.usage().files_read == ("primeiro", "segundo", "terceiro")


def test_fragmentos_acima_do_teto_sao_denied_e_nenhum_files_read_entra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(tool_result_content_bytes=60))
    fake_search(
        monkeypatch,
        Outcome(
            ToolStatus.OK,
            fragments=(
                OutputFragment("a" * 20, ("primeiro",)),
                OutputFragment("b" * 20, ("segundo",)),
                OutputFragment("c" * 20, ("terceiro",)),
            ),
        ),
    )  # 20 + 1 + 20 + 1 + 20 = 62 > 60
    result = rig.run(SearchText("x"))
    assert result.status is ToolStatus.DENIED
    assert result.reason is not None and result.reason.startswith("limit.tool_result_content_bytes")
    assert rig.executor.usage().files_read == ()


def test_o_orcamento_usa_bytes_utf8_do_conteudo_completo_ja_redigido(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(tool_result_content_bytes=70))
    monkeypatch.setattr(executor_module, "redact", lambda text: text.replace("seg", "ççççççççç"))
    fake_search(
        monkeypatch,
        Outcome(
            ToolStatus.OK,
            fragments=tuple(OutputFragment("seg", (nome,)) for nome in ("a", "b", "c", "d")),
        ),
    )
    # o tamanho **cru** (4 × 3 + 3 = 15 bytes) caberia; o redigido (4 × 18 + 3 = 75) não
    assert rig.run(SearchText("x")).status is ToolStatus.DENIED
    assert rig.executor.usage().files_read == ()


def test_o_exato_no_teto_e_integral_e_nunca_ha_marcador(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(tool_result_content_bytes=11))
    fake_search(
        monkeypatch,
        Outcome(
            ToolStatus.OK,
            fragments=(OutputFragment("12345", ("a",)), OutputFragment("12345", ("b",))),
        ),
    )
    assert ok(rig.run(SearchText("x"))) == "12345\n12345"  # 5 + 1 + 5 = 11: exatamente o teto
    assert rig.executor.usage().files_read == ("a", "b")


def test_resultado_e_deterministico_e_integral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resultados = set()
    for indice in range(3):
        rig = Rig(tmp_path / f"r{indice}", tool_limits=limits(tool_result_content_bytes=500))
        fake_search(
            monkeypatch,
            Outcome(
                ToolStatus.OK,
                fragments=tuple(OutputFragment(f"linha-{i}-ç", (f"f{i}",)) for i in range(12)),
            ),
        )
        content = ok(rig.run(SearchText("x")))
        resultados.add((content, rig.executor.usage().files_read))
        assert lines_of(content) == [f"linha-{i}-ç" for i in range(12)]
    assert len(resultados) == 1


def test_files_read_so_depois_do_tool_result_se_a_construcao_falha_nada_e_incorporado(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_search(
        monkeypatch,
        Outcome(ToolStatus.OK, fragments=(OutputFragment("tem\x00nul", ("a.txt",)),)),
    )
    errored(rig.run(SearchText("x")), "internal_error")
    assert rig.executor.usage().files_read == ()  # nunca atualizado antes do resultado final


def test_leitura_inteira_continua_sem_truncar_e_so_conta_se_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(tool_result_content_bytes=30))
    rig.put("a.txt", "x" * 25)
    assert ok(rig.run(ReadFile("a.txt"))) == "x" * 25
    monkeypatch.setattr(executor_module, "redact", lambda text: text * 3)
    rig.put("b.txt", "y" * 25)
    assert rig.run(ReadFile("b.txt")).status is ToolStatus.DENIED
    assert rig.executor.usage().files_read == ("a.txt",)


def test_listdirectory_e_searchtext_usam_fragmentos_nao_conteudo_plano(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    capturados: list[Outcome] = []
    real_finish = LocalWorktreeToolExecutor._finish

    def espia(self: Any, operation: str, outcome: Outcome, *, write: bool) -> Any:
        capturados.append(outcome)
        return real_finish(self, operation, outcome, write=write)

    monkeypatch.setattr(LocalWorktreeToolExecutor, "_finish", espia)
    rig.put("a.txt", "hit\n")
    ok(rig.run(ListDirectory(None)))
    ok(rig.run(SearchText("hit")))
    ok(rig.run(ReadFile("a.txt")))
    listing, search, read = capturados
    assert listing.fragments is not None and listing.content == "" and listing.files_read == ()
    assert search.fragments is not None and search.fragments[0].files_read == ("a.txt",)
    assert read.fragments is None and read.files_read == ("a.txt",)


# ----------------------------------------------------------- a porta interna de negação


def test_record_denial_e_criada_pelo_executor_e_registra_sem_negar_a_operacao(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    capturado: list[HandlerContext] = []

    def metodo(self: Any, context: HandlerContext, request: Any) -> Outcome:
        capturado.append(context)
        context.record_denial(SafetyDecision(False, "path.secret_denied", "CRU", ".env"))
        context.record_denial(SafetyDecision(False, "path.dot_git_denied", "CRU", ".git"))
        return Outcome(ToolStatus.OK, fragments=(OutputFragment("ok"),))

    monkeypatch.setattr(LocalWorktreeToolExecutor, "_list_directory", metodo)
    assert ok(rig.run(ListDirectory(None))) == "ok"
    first, second = rig.journal.records()
    assert (first.sequence, second.sequence) == (1, 2)
    assert first.category is DecisionCategory.SECRET_ACCESS_BLOCKED
    assert second.category is DecisionCategory.PATH_DENIED
    assert first.reason.startswith("path.secret_denied: ") and "CRU" not in first.reason
    assert (first.operation, first.subject_redacted) == ("ListDirectory", ".env")
    assert rig.executor.usage().denials == 0  # a operação foi `OK`
    assert callable(capturado[0].record_denial)


def test_record_denial_nao_e_exposta_ao_provider_nem_por_construtor(rig: Rig) -> None:
    import inspect

    assert "record_denial" not in set(dir(rig.executor.mediated_tools()))
    assert "record_denial" not in inspect.signature(LocalWorktreeToolExecutor.__init__).parameters


def test_record_denial_com_regra_desconhecida_nunca_vira_texto_cru(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def metodo(self: Any, context: HandlerContext, request: Any) -> Outcome:
        context.record_denial(SafetyDecision(False, "regra.inventada", "CRU", "x"))
        return Outcome(ToolStatus.OK, content="x")

    monkeypatch.setattr(LocalWorktreeToolExecutor, "_list_directory", metodo)
    errored(rig.run(ListDirectory(None)), "internal_error")
    assert len(rig.journal) == 0


def test_journal_que_falha_na_porta_interna_envenena_o_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Falha:
        calls = 0

        def append(self, record: Any) -> None:
            Falha.calls += 1
            raise OSError("indisponível")

    rig = Rig(tmp_path)
    rig.executor._journal = Falha()

    def metodo(self: Any, context: HandlerContext, request: Any) -> Outcome:
        context.record_denial(SafetyDecision(False, "path.secret_denied", "x", ".env"))
        return Outcome(ToolStatus.OK, content="NUNCA")  # não pode ser alcançado

    monkeypatch.setattr(LocalWorktreeToolExecutor, "_list_directory", metodo)
    errored(rig.run(ListDirectory(None)), "journal_unavailable")
    errored(rig.run(ReadFile("a.txt")), "journal_unavailable")
    assert Falha.calls == 1 and rig.executor.usage().denials == 0


def test_inspection_failed_vira_error_fixo_sem_registro_de_politica(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def metodo(self: Any, context: HandlerContext, request: Any) -> Outcome:
        raise InspectionFailed("[Errno 5] C:\\segredo\\absoluto")

    monkeypatch.setattr(LocalWorktreeToolExecutor, "_list_directory", metodo)
    result = rig.run(ListDirectory(None))
    errored(result, "path_inspection_failed")
    assert "Errno" not in (result.reason or "") and "segredo" not in (result.reason or "")
    assert len(rig.journal) == 0 and rig.executor.usage().denials == 0


def test_a_frase_do_erro_novo_e_fixa() -> None:
    assert "path_inspection_failed" in ERROR_PHRASES
    assert "{" not in ERROR_PHRASES["path_inspection_failed"]
