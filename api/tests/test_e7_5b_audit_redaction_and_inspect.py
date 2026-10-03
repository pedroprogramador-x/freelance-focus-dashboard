"""E7.5-B — B-AUD-004 (redação sobre a saída **completa**) e B-AUD-003 (falha de inspeção).

**B-AUD-004.** A redação por fragmento perdia o contexto multilinha: um bloco PEM *fictício e
inerte* dividido em fragmentos não era reconhecido, mas é reconhecido na saída concatenada. Agora o
executor junta o texto bruto, aplica o redator central **uma vez** na saída completa, mede os bytes
do **redigido** contra o teto (fail closed, sem truncar) e só então incorpora `files_read`.

**B-AUD-003.** `path_runtime.inspect` passou a preencher `inspection_error` com um **código interno
fixo** quando um `stat`/`lstat` necessário falha por um motivo que não é ausência comprovada; o
`fs_ops` usa **só** esse campo (nada de inferir a partir de combinações de `Tri.UNKNOWN`).

Fixtures inertes: o "PEM" é texto base64 fictício; as falhas de `stat`/`lstat` são *mocks*.
"""

from __future__ import annotations

import dataclasses
import errno
import inspect as std_inspect
import os
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app import path_runtime
from app.path_runtime import inspect
from app.safety import Tri, decide_path, redact
from app.safety.paths import PathIntent
from app.tool_executor import executor as executor_module
from app.tool_executor import fs_ops
from app.tool_executor.contracts import ListDirectory, ReadFile, SearchText, ToolStatus
from app.tool_executor.executor import LocalWorktreeToolExecutor
from app.tool_executor.outcome import Outcome, OutputFragment
from tests.test_tool_file_ops_e7_5b import Rig, denied, errored, limits, lines_of, ok

fsx: Any = fs_ops

BEGIN = "-----BEGIN RSA PRIVATE KEY-----"
END = "-----END RSA PRIVATE KEY-----"
BODY = [
    "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=",  # base64 de texto fictício
    "RklDVElDSU9fSU5FUlRFX05BT19FX0NIQVZF",
    "TUFSQ0FET1JfRElGRVJFTlRFX0RFX1NFR1JFRE8=",
]
INERT_BODY_MARKERS = ["QUJDREVG", "RklDVElD", "TUFSQ0FE"]


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def fake_search(monkeypatch: pytest.MonkeyPatch, fragments: tuple[OutputFragment, ...]) -> None:
    outcome = Outcome(ToolStatus.OK, fragments=fragments)
    monkeypatch.setattr(
        LocalWorktreeToolExecutor, "_search_text", lambda self, context, request: outcome
    )


def frags(*texts: str) -> tuple[OutputFragment, ...]:
    return tuple(OutputFragment(text, (f"f{i}.txt",)) for i, text in enumerate(texts))


def leaks(content: str) -> bool:
    return any(marker in content for marker in INERT_BODY_MARKERS)


def legacy_per_fragment(fragments: tuple[OutputFragment, ...]) -> str:
    """O algoritmo **anterior** (a regressão): redigir cada fragmento isoladamente."""
    return "\n".join(redact(f.text) for f in fragments)


# ================================================================================ B-AUD-004


def test_b_aud_004_o_redator_central_ve_o_padrao_so_na_saida_completa() -> None:
    lines = [BEGIN, *BODY, END]
    assert not leaks(redact("\n".join(lines)))  # a saída completa é redigida
    assert leaks("\n".join(redact(line) for line in lines))  # fragmento a fragmento, não é


@pytest.mark.parametrize(
    "partes",
    [
        pytest.param([[BEGIN, *BODY, END]], id="um-fragmento"),
        pytest.param([[BEGIN, BODY[0]], [BODY[1], BODY[2], END]], id="dois-fragmentos"),
        pytest.param([[BEGIN], [BODY[0]], [BODY[1]], [BODY[2]], [END]], id="cinco-fragmentos"),
        pytest.param([[BEGIN], [BODY[0], BODY[1]], [BODY[2]], [END]], id="quatro-fragmentos"),
    ],
)
def test_b_aud_004_segredo_dentro_de_um_atravessando_dois_ou_tres_ou_mais_fragmentos(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, partes: list[list[str]]
) -> None:
    fragments = frags(*("\n".join(parte) for parte in partes))
    assert leaks(legacy_per_fragment(fragments)) == (len(partes) > 1)  # contrafactual
    fake_search(monkeypatch, fragments)
    content = ok(rig.run(SearchText("x")))
    assert not leaks(content) and "PRIVATE KEY" not in content
    assert content == redact("\n".join(f.text for f in fragments))


def test_b_aud_004_a_redacao_roda_uma_unica_vez_sobre_o_texto_completo(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    chamadas: list[str] = []

    def espia(text: str) -> str:
        chamadas.append(text)
        return redact(text)

    monkeypatch.setattr(executor_module, "redact", espia)
    fragments = frags("antes", BEGIN, BODY[0], BODY[1], END, "depois")
    fake_search(monkeypatch, fragments)
    ok(rig.run(SearchText("x")))
    completo = "\n".join(f.text for f in fragments)
    assert chamadas == [completo]  # **uma** chamada, com o conteúdo completo
    assert all(f.text not in chamadas for f in fragments)  # nenhum fragmento isolado


def test_b_aud_004_multiplos_padroes_multilinha_e_unicode_ao_redor(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    textos = [
        "ação 日本 antes",
        BEGIN,
        BODY[0],
        END,
        "ç meio ç",
        BEGIN,
        BODY[1],
        BODY[2],
        END,
        "depois ❤ 日本",
    ]
    fake_search(monkeypatch, frags(*textos))
    content = ok(rig.run(SearchText("x")))
    assert not leaks(content) and "PRIVATE KEY" not in content
    for intacto in ("ação 日本 antes", "ç meio ç", "depois ❤ 日本"):
        assert intacto in content  # o que não é segredo sobrevive, Unicode incluso
    content.encode("utf-8").decode("utf-8")


def test_b_aud_004_conteudo_redigido_dentro_do_limite_entrega_e_conta_a_uniao(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(tool_result_content_bytes=400))
    fake_search(monkeypatch, frags("a.txt:1:1:x", BEGIN, BODY[0], END, "z.txt:9:9:y"))
    content = ok(rig.run(SearchText("x")))
    assert len(content.encode("utf-8")) <= 400
    assert rig.executor.usage().files_read == ("f0.txt", "f1.txt", "f2.txt", "f3.txt", "f4.txt")


def test_b_aud_004_o_limite_vale_para_o_redigido_nao_para_o_cru(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cru > teto, redigido ≤ teto: entrega (o bloco inteiro vira um marcador curto)."""
    rig = Rig(tmp_path, tool_limits=limits(tool_result_content_bytes=120))
    longo = "\n".join([BEGIN, *(BODY * 8), END])
    assert len(longo.encode("utf-8")) > 120
    fake_search(monkeypatch, frags(longo))
    content = ok(rig.run(SearchText("x")))
    assert len(content.encode("utf-8")) <= 120 and not leaks(content)
    assert rig.executor.usage().files_read == ("f0.txt",)


def test_b_aud_004_redigido_acima_do_limite_e_denied_e_nenhum_files_read_entra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(tool_result_content_bytes=100))
    fake_search(monkeypatch, frags("a" * 60, BEGIN, BODY[0], END, "b" * 60))
    result = rig.run(SearchText("x"))
    denied(result, "limit.tool_result_content_bytes")
    assert result.content is None and rig.executor.usage().files_read == ()


def test_b_aud_004_search_ponta_a_ponta_com_bloco_pem_que_atravessa_matches(rig: Rig) -> None:
    rig.put("chave.txt", "\n".join([BEGIN, *BODY, END]) + "\n")
    content = ok(rig.run(SearchText("E")))  # `E` aparece em todas as linhas: um fragmento por match
    assert not leaks(content)
    assert rig.executor.usage().files_read == ("chave.txt",)


def test_b_aud_004_listdirectory_e_readfile_tambem_passam_pela_redacao_completa(
    rig: Rig,
) -> None:
    rig.put("nota.txt", "\n".join([BEGIN, *BODY, END]))
    assert not leaks(ok(rig.run(ReadFile("nota.txt"))))
    assert "nota.txt" in ok(rig.run(ListDirectory(None)))


def test_b_aud_004_nenhum_truncamento_final_no_executor() -> None:
    fonte = std_inspect.getsource(executor_module)
    assert "_FRAGMENT_MARKER" not in fonte and "_TRUNCATION_MARKER" not in fonte
    assert "allow_truncation" not in std_inspect.getsource(fs_ops)
    assert "allow_truncation" not in {f.name for f in dataclasses.fields(Outcome)}


def test_b_aud_004_files_read_so_depois_do_resultado_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, tool_limits=limits(tool_result_content_bytes=30))
    fake_search(monkeypatch, frags("x" * 40))
    denied(rig.run(SearchText("x")), "limit.tool_result_content_bytes")
    assert rig.executor.usage().files_read == ()
    fake_search(monkeypatch, frags("x" * 10))
    ok(rig.run(SearchText("x")))
    assert rig.executor.usage().files_read == ("f0.txt",)


# ================================================================================ B-AUD-003
# --- na origem: `path_runtime.inspect`


def fail_on(
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    name: str,
    error: BaseException,
    *,
    on_call: int = 1,
) -> Counter[str]:
    """`Path.<attribute>` falha para o caminho cujo último componente é ``name`` na chamada
    ``on_call`` (1 = a primeira) e em todas as seguintes."""
    real = getattr(Path, attribute)
    contador: Counter[str] = Counter()

    def fake(self: Path, *args: Any, **kwargs: Any) -> Any:
        # `Path.lstat()` é `stat(follow_symlinks=False)`: só falha o `stat` que **segue** links
        seguindo = attribute == "stat" and kwargs.get("follow_symlinks", True) is not False
        if self.name == name and (attribute != "stat" or seguindo):
            contador[name] += 1
            if contador[name] >= on_call:
                raise error
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, attribute, fake)
    return contador


ERROS = [
    OSError(errno.EIO, "erro de E/S: C:\\Users\\x\\segredo"),
    PermissionError(errno.EACCES, "negado: C:\\Users\\x\\segredo"),
    OSError(errno.EINVAL, "genérico: C:\\Users\\x\\segredo"),
]
IDS = ["eio", "permissao", "oserror"]
CODIGOS = {
    "chain_lstat_failed",
    "target_stat_failed",
    "parent_stat_failed",
    "volume_stat_failed",
    "root_stat_failed",
    "root_unresolvable",
    "target_unresolvable",
    "fstat_failed",
}


def sem_texto_cru(facts: Any, tmp_path: Path) -> None:
    assert facts.inspection_error in CODIGOS, facts.inspection_error
    for cru in ("Errno", "WinError", str(tmp_path), "segredo", "C:\\"):
        assert cru not in facts.inspection_error


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_b_aud_003_lstat_que_falha_preenche_inspection_error_fixo(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("sub/alvo.txt")
    fail_on(monkeypatch, "lstat", "alvo.txt", erro)
    facts = inspect("sub/alvo.txt", rig.root.path, tolerate_absent_tail=True)
    assert facts.inspection_error == "chain_lstat_failed"
    sem_texto_cru(facts, rig.tmp_path)
    decision = decide_path(facts, intent=PathIntent.READ)
    assert not decision.allow and decision.rule_id == "path.inspection_failed"


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_b_aud_003_stat_que_falha_ao_verificar_existencia(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("alvo.txt")
    fail_on(monkeypatch, "stat", "alvo.txt", erro, on_call=1)
    facts = inspect("alvo.txt", rig.root.path, tolerate_absent_tail=True)
    assert facts.inspection_error == "target_stat_failed"
    sem_texto_cru(facts, rig.tmp_path)


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_b_aud_003_o_caso_do_auditor_exists_true_sem_identidade_agora_tem_erro_explicito(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    """`exists=True`, `target_identity=None`, `inspection_error=None` e link facts `FALSE`:
    a forma que a heurística do `fs_ops` não distinguia. Agora o erro é explícito."""
    rig.put("alvo.txt")
    real_exists = Path.exists
    monkeypatch.setattr(
        Path, "exists", lambda self: True if self.name == "alvo.txt" else real_exists(self)
    )  # `exists()` passa...
    fail_on(monkeypatch, "stat", "alvo.txt", erro)  # ...mas o `stat()` do alvo falha
    facts = inspect("alvo.txt", rig.root.path, tolerate_absent_tail=True)
    assert facts.exists and facts.target_identity is None
    assert (facts.is_symlink, facts.is_junction, facts.is_reparse_point) == (Tri.FALSE,) * 3
    assert facts.inspection_error == "target_stat_failed"
    sem_texto_cru(facts, rig.tmp_path)


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_b_aud_003_stat_do_pai_que_falha(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("sub/alvo.txt")
    fail_on(monkeypatch, "stat", "sub", erro)
    facts = inspect("sub/alvo.txt", rig.root.path, tolerate_absent_tail=True)
    assert facts.inspection_error == "parent_stat_failed"
    sem_texto_cru(facts, rig.tmp_path)


def test_b_aud_003_ausencia_comprovada_nao_e_erro(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = inspect("a/b/novo.txt", rig.root.path, tolerate_absent_tail=True)
    assert not facts.exists and facts.inspection_error is None
    assert (facts.is_symlink, facts.is_junction, facts.is_reparse_point) == (Tri.FALSE,) * 3
    # sumiu **entre** as duas leituras de `stat`: ainda é ausência, não falha
    rig.put("alvo.txt")
    real_exists = Path.exists
    monkeypatch.setattr(
        Path, "exists", lambda self: True if self.name == "alvo.txt" else real_exists(self)
    )
    fail_on(monkeypatch, "stat", "alvo.txt", FileNotFoundError(errno.ENOENT, "sumiu"))
    raced = inspect("alvo.txt", rig.root.path, tolerate_absent_tail=True)
    assert not raced.exists and raced.inspection_error is None


def test_b_aud_003_unknown_de_propriedade_individual_nao_e_inspection_error(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    alvo = rig.put("alvo.txt")
    inode = os.lstat(alvo).st_ino
    real = path_runtime._link_facts_of

    # o `lstat` **funcionou**; só a propriedade (tipo de reparse) não é comprovável
    def so_junction(info: os.stat_result) -> tuple[Tri, Tri, Tri]:
        if info.st_ino == inode:
            return Tri.FALSE, Tri.UNKNOWN, Tri.FALSE
        return real(info)

    monkeypatch.setattr(path_runtime, "_link_facts_of", so_junction)
    facts = inspect("alvo.txt", rig.root.path, tolerate_absent_tail=True)
    assert facts.is_junction is Tri.UNKNOWN and facts.inspection_error is None
    decision = decide_path(facts, intent=PathIntent.READ)
    assert not decision.allow and decision.rule_id == "path.junction_unverified"  # política


def test_b_aud_003_padrao_legado_ausente_continua_unknown_sem_inspection_error(
    rig: Rig,
) -> None:
    facts = inspect("nao-existe.txt", rig.root.path)  # sem a flag: contrato congelado (E2-AUD)
    assert facts.inspection_error is None and facts.is_symlink is Tri.UNKNOWN


def test_b_aud_003_falhas_de_resolucao_usam_codigo_fixo(tmp_path: Path) -> None:
    facts = inspect("x.txt", tmp_path / "raiz-inexistente")
    assert facts.inspection_error == "root_unresolvable"
    assert "raiz-inexistente" not in facts.inspection_error


def test_b_aud_003_fstat_que_falha_usa_codigo_fixo(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt")
    facts = inspect("a.txt", rig.root.path, tolerate_absent_tail=True)
    handle = open(rig.ws / "a.txt", "rb")  # noqa: SIM115 — fixture; fechado abaixo
    fd = handle.fileno()

    def quebrado(descriptor: int) -> os.stat_result:
        raise OSError(errno.EIO, r"x C:\y")

    try:
        monkeypatch.setattr(os, "fstat", quebrado)
        opened = path_runtime.inspect_opened(facts, fd)
    finally:
        monkeypatch.undo()
        handle.close()
    assert opened.inspection_error == "fstat_failed"


def test_b_aud_003_invariante_existe_implica_identidade_ou_erro(rig: Rig) -> None:
    rig.put("sub/a.txt")
    for relativo in (".", "sub", "sub/a.txt", "sub/novo.txt", "nada/novo.txt"):
        facts = inspect(relativo, rig.root.path, tolerate_absent_tail=True)
        assert facts.inspection_error is None
        assert (not facts.exists) or facts.target_identity is not None
        if facts.exists:
            assert facts.parent_identity is not None


# --- no fs_ops: a única fonte é `inspection_error`


def test_b_aud_003_inspection_failed_do_fs_ops_nao_infere_a_partir_de_unknown() -> None:
    fonte = std_inspect.getsource(fs_ops._inspection_failed)
    code = fonte.split('"""')[-1]  # só o corpo; a docstring explica
    assert "inspection_error" in code
    assert "is_unknown" not in code and "Tri" not in code


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_b_aud_003_listdirectory_com_stat_que_falha_e_error_sem_registro(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("alvo.txt")
    rig.put("outro.txt")
    fail_on(monkeypatch, "stat", "alvo.txt", erro, on_call=2)
    errored(rig.run(ListDirectory(None)), "path_inspection_failed")
    assert len(rig.journal) == 0 and rig.executor.usage().denials == 0


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_b_aud_003_searchtext_com_stat_que_falha_e_error_nunca_ok_vazio(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("alvo.txt", "NEEDLE aqui\n")
    rig.put("outro.txt", "nada\n")
    fail_on(monkeypatch, "stat", "alvo.txt", erro, on_call=2)
    result = rig.run(SearchText("NEEDLE"))
    errored(result, "path_inspection_failed")
    assert len(rig.journal) == 0 and rig.executor.usage().files_read == ()
    assert "Errno" not in (result.reason or "") and "segredo" not in (result.reason or "")


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_b_aud_003_readfile_com_inspecao_que_falha_e_error_tecnico_nao_negacao(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("alvo.txt", "conteudo")
    fail_on(monkeypatch, "stat", "alvo.txt", erro, on_call=2)
    result = rig.run(ReadFile("alvo.txt"))
    # B-AUD-003 (fecho): as quatro operações usam o mesmo código para a mesma condição
    errored(result, "path_inspection_failed")
    assert len(rig.journal) == 0 and rig.executor.usage().denials == 0


def test_b_aud_003_combinado_negacao_real_registrada_e_depois_stat_que_falha(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put(".env", "NEEDLE\n")
    rig.put("z.txt", "NEEDLE\n")
    fail_on(monkeypatch, "stat", "z.txt", OSError(errno.EIO, "erro de E/S"), on_call=2)
    errored(rig.run(SearchText("NEEDLE")), "path_inspection_failed")
    (record,) = rig.journal.records()  # só a negação **real**; nada para o erro de E/S
    assert record.rule_id == "path.secret_denied" and record.subject_redacted == ".env"
    assert rig.executor.usage().denials == 0


def test_b_aud_003_a_negacao_real_continua_omitindo_e_registrando(rig: Rig) -> None:
    rig.put(".env", "NEEDLE\n")
    rig.put("ok.txt", "NEEDLE\n")
    content = ok(rig.run(SearchText("NEEDLE")))
    assert content.startswith("ok.txt:1:1:NEEDLE") and ".env" not in content
    assert [r.rule_id for r in rig.journal.records()] == ["path.secret_denied"]


_: Callable[..., Any] = lines_of
