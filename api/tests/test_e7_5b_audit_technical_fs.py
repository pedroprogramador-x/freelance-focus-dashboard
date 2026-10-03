"""E7.5-B — fecho de B-AUD-003 e B-AUD-005: classificação **técnica** de filesystem.

Três classes que não se misturam:

* **falha técnica de observação** (E/S, `PermissionError`, `OSError` ambíguo) → `ERROR`
  técnico, sem `SafetyDecision`, sem registro no journal;
* **integridade violada** (objeto sumiu/trocou, identidade não verificável) → `DENIED` + journal;
* **política fail closed** (link conhecido, propriedade individual `UNKNOWN`, segredo, `.git`)
  → `DENIED`/omissão + journal.

**B-AUD-003.** A cadeia léxica lia cada componente **duas vezes** (probe, depois `_link_facts`); se
a segunda leitura falhasse, os fatos saíam `UNKNOWN` **sem** `inspection_error` e a política os
negava como `path.symlink_unverified`. Agora há **uma** leitura: os fatos de link derivam do mesmo
`lstat` do probe. O `WriteFile` usava a inspeção legada (sem `tolerate_absent_tail`); agora usa a
mesma inspeção mediada das demais operações.

**B-AUD-005.** `_entry_facts` convertia **qualquer** falha de `lstat` de uma entrada em
`INTEGRITY_UNVERIFIABLE`. Agora E/S/permissão/`OSError` ambíguo é
`PathOperationFailed(INSPECTION_FAILED)` (`ERROR path_inspection_failed`); a entrada que **sumiu**
depois do `scandir` e a identidade `file_id == 0` continuam integridade.

Fixtures inertes: arquivos de texto em diretório temporário; as falhas são *mocks* com mensagens
sintéticas.
"""

from __future__ import annotations

import errno
import os
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from app import path_runtime
from app.path_runtime import (
    IntegrityFailure,
    PathFailure,
    PathIntegrityViolation,
    PathOperationFailed,
    inspect,
    list_directory,
)
from app.safety import Tri, decide_path
from app.safety.paths import PathIntent
from app.tool_executor import fs_ops
from app.tool_executor.contracts import DecisionCategory, ListDirectory, SearchText, WriteFile
from app.tool_executor.reasons import ERROR_PHRASES
from tests.test_e7_5a_audit_p2 import ScandirHook, zero_ids
from tests.test_e7_5b_audit_redaction_and_inspect import fail_on
from tests.test_tool_file_ops_e7_5b import (
    Rig,
    after_inspect,
    denied,
    errored,
    make_dir_link,
    ok,
)

fsx: Any = fs_ops

ERROS = [
    OSError(errno.EIO, "erro de E/S: C:\\Users\\x\\segredo"),
    PermissionError(errno.EACCES, "negado: C:\\Users\\x\\segredo"),
    OSError(errno.EINVAL, "genérico: C:\\Users\\x\\segredo"),
]
IDS = ["eio", "permissao", "oserror"]


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def fail_os_lstat(monkeypatch: pytest.MonkeyPatch, name: str, error: BaseException) -> Counter[str]:
    """`os.lstat` (a leitura das primitivas de `path_runtime`) falha para o nome ``name``."""
    real = os.lstat
    contador: Counter[str] = Counter()

    def fake(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if os.path.basename(os.fspath(path)) == name:
            contador[name] += 1
            raise error
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", fake)
    return contador


def sem_vazamento(result: Any) -> None:
    texto = repr(result)
    for cru in ("Errno", "WinError", "segredo", "C:\\Users\\x"):
        assert cru not in texto


def legado_duas_leituras(path: Path) -> tuple[str, tuple[Tri, Tri, Tri]]:
    """O algoritmo **anterior** da cadeia tolerante: probe e, depois, uma segunda leitura."""
    probe, _ = path_runtime._probe_component(path)
    return probe, path_runtime._link_facts(path)


# ============================================================ B-AUD-003 — A. segunda leitura


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_a_contrafactual_a_segunda_leitura_antiga_perdia_a_falha(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    """Probe OK, segunda leitura falha: o algoritmo antigo devolvia `UNKNOWN` **sem** causa."""
    alvo = rig.put("alvo.txt")
    fail_on(monkeypatch, "lstat", "alvo.txt", erro, on_call=2)
    probe, fatos = legado_duas_leituras(alvo)
    assert probe == "present" and fatos == (Tri.UNKNOWN,) * 3  # a falha some no `UNKNOWN`


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_a_a_cadeia_le_cada_componente_uma_unica_vez(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    """A segunda leitura **não existe** mais: os fatos de link saem do `lstat` do probe."""
    rig.put("sub/alvo.txt")
    contador = fail_on(monkeypatch, "lstat", "alvo.txt", erro, on_call=2)
    facts = inspect("sub/alvo.txt", rig.root.path, tolerate_absent_tail=True)
    assert contador["alvo.txt"] == 1  # uma leitura; a falha "da segunda" nunca acontece
    assert facts.inspection_error is None
    assert (facts.is_symlink, facts.is_junction, facts.is_reparse_point) == (Tri.FALSE,) * 3
    assert decide_path(facts, intent=PathIntent.READ).allow


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_a_a_unica_leitura_que_falha_preenche_inspection_error_fixo(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("sub/alvo.txt")
    fail_on(monkeypatch, "lstat", "alvo.txt", erro)
    facts = inspect("sub/alvo.txt", rig.root.path, tolerate_absent_tail=True)
    assert facts.inspection_error == "chain_lstat_failed"
    assert "Errno" not in facts.inspection_error and "segredo" not in facts.inspection_error


@pytest.mark.parametrize("on_call", [1, 2, 3])
@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_a_invariante_link_unknown_por_falha_de_leitura_sempre_tem_inspection_error(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError, on_call: int
) -> None:
    """Para **qualquer** leitura que falhe, nunca `UNKNOWN` silencioso (sem causa técnica)."""
    rig.put("a/b/alvo.txt")
    for nome in ("a", "b", "alvo.txt"):
        monkeypatch.undo()
        fail_on(monkeypatch, "lstat", nome, erro, on_call=on_call)
        facts = inspect("a/b/alvo.txt", rig.root.path, tolerate_absent_tail=True)
        link = (facts.is_symlink, facts.is_junction, facts.is_reparse_point)
        assert facts.inspection_error is not None or Tri.UNKNOWN not in link, (nome, on_call)


# ===================================================== B-AUD-003 — B/C. ListDirectory/SearchText


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_b_listdirectory_leitura_que_falha_e_error_sem_registro_de_politica(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("alvo.txt")
    rig.put("outro.txt")
    fail_on(monkeypatch, "lstat", "alvo.txt", erro)
    result = rig.run(ListDirectory(None))
    errored(result, "path_inspection_failed")
    sem_vazamento(result)
    assert len(rig.journal) == 0 and rig.executor.usage().denials == 0


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_b_listdirectory_sem_segunda_leitura_a_entrada_aparece_e_nada_e_registrado(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    """Antes: a segunda leitura falhava → `symlink_unverified` → omissão **e** journal."""
    rig.put("alvo.txt")
    contador = fail_on(monkeypatch, "lstat", "alvo.txt", erro, on_call=2)
    content = ok(rig.run(ListDirectory(None)))
    assert content == "file\talvo.txt" and contador["alvo.txt"] == 1
    assert len(rig.journal) == 0


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_c_searchtext_leitura_que_falha_e_error_nunca_no_matches(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("alvo.txt", "NEEDLE\n")
    rig.put("outro.txt", "nada\n")
    fail_on(monkeypatch, "lstat", "alvo.txt", erro)
    result = rig.run(SearchText("NEEDLE"))
    errored(result, "path_inspection_failed")
    sem_vazamento(result)
    assert "path.symlink_unverified" not in rig.reasons() and len(rig.journal) == 0
    assert rig.executor.usage().files_read == ()


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_c_searchtext_sem_segunda_leitura_encontra_o_match(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("alvo.txt", "NEEDLE\n")
    fail_on(monkeypatch, "lstat", "alvo.txt", erro, on_call=2)
    assert ok(rig.run(SearchText("NEEDLE"))) == "alvo.txt:1:1:NEEDLE"
    assert len(rig.journal) == 0


# ===================================================================== B-AUD-003 — D. WriteFile


class Espiao:
    """Registra se alguma primitiva de **efeito** chegou a ser chamada."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []
        for nome in ("open_existing", "write_fd", "create_exclusive"):
            real = getattr(fsx, nome)

            def wrapper(*args: Any, _real: Any = real, _nome: str = nome, **kwargs: Any) -> Any:
                self.calls.append(_nome)
                return _real(*args, **kwargs)

            monkeypatch.setattr(fsx, nome, wrapper)


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_d_writefile_update_lstat_que_falha_e_error_sem_abrir_nem_truncar(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    alvo = rig.put("alvo.txt", "original")
    # contrafactual: a inspeção **legada** (a que o UPDATE usava) perde a causa e a política nega
    fail_on(monkeypatch, "lstat", "alvo.txt", erro)
    legado = inspect("alvo.txt", rig.root.path)
    assert legado.inspection_error is None and legado.is_symlink is Tri.UNKNOWN
    assert decide_path(legado, intent=PathIntent.WRITE).rule_id == "path.symlink_unverified"

    espiao = Espiao(monkeypatch)
    result = rig.run(WriteFile("alvo.txt", "novo"))
    errored(result, "path_inspection_failed")
    sem_vazamento(result)
    monkeypatch.undo()
    assert alvo.read_text() == "original" and espiao.calls == []
    assert len(rig.journal) == 0 and rig.executor.usage().denials == 0


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_d_writefile_update_stat_que_falha_e_error_sem_efeito(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    alvo = rig.put("alvo.txt", "original")
    espiao = Espiao(monkeypatch)
    fail_on(monkeypatch, "stat", "alvo.txt", erro)
    errored(rig.run(WriteFile("alvo.txt", "novo")), "path_inspection_failed")
    monkeypatch.undo()
    assert alvo.read_text() == "original" and espiao.calls == []
    assert len(rig.journal) == 0


def test_d_writefile_update_normal_continua_funcionando(rig: Rig) -> None:
    alvo = rig.put("alvo.txt", "original")
    assert ok(rig.run(WriteFile("alvo.txt", "novo"))) == "updated alvo.txt (4 bytes)"
    assert alvo.read_text() == "novo"


def test_d_writefile_update_arquivo_que_some_depois_da_inspecao_e_not_found(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    alvo = rig.put("alvo.txt", "original")
    after_inspect(monkeypatch, lambda n: alvo.unlink() if n == 1 else None)
    errored(rig.run(WriteFile("alvo.txt", "novo")), "not_found")
    assert not alvo.exists() and len(rig.journal) == 0


def test_d_writefile_link_comprovado_continua_negado_por_politica(
    rig: Rig,
) -> None:
    (rig.outside / "x.txt").write_text("fora")
    if not make_dir_link(rig.ws / "lnk", rig.outside):
        pytest.skip("link indisponível neste ambiente")
    denied(rig.run(WriteFile("lnk/x.txt", "novo")))
    assert (rig.outside / "x.txt").read_text() == "fora"
    assert len(rig.journal) == 1


def test_d_writefile_create_ausencia_comprovada_nao_e_inspection_error(rig: Rig) -> None:
    facts = inspect("a/b/novo.txt", rig.root.path, tolerate_absent_tail=True)
    assert not facts.exists and facts.inspection_error is None
    assert ok(rig.run(WriteFile("a/b/novo.txt", "x"))) == "created a/b/novo.txt (1 bytes)"


# ===================================================== B-AUD-003 — E/F. UNKNOWN legítimo, ausência


def unknown_junction_para(monkeypatch: pytest.MonkeyPatch, alvo: Path) -> None:
    """O `lstat` **funciona**; só a propriedade (tipo de reparse) não é comprovável."""
    inode = os.lstat(alvo).st_ino
    real = path_runtime._link_facts_of

    def fake(info: os.stat_result) -> tuple[Tri, Tri, Tri]:
        return (Tri.FALSE, Tri.UNKNOWN, Tri.FALSE) if info.st_ino == inode else real(info)

    monkeypatch.setattr(path_runtime, "_link_facts_of", fake)


def test_e_unknown_individual_no_writefile_e_politica_com_journal(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    alvo = rig.put("alvo.txt", "original")
    unknown_junction_para(monkeypatch, alvo)
    denied(rig.run(WriteFile("alvo.txt", "novo")), "path.junction_unverified")
    assert alvo.read_text() == "original"
    assert rig.reasons() == ["path.junction_unverified"]


def test_e_unknown_individual_no_listdirectory_omite_e_registra(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    alvo = rig.put("alvo.txt")
    rig.put("ok.txt")
    unknown_junction_para(monkeypatch, alvo)
    content = ok(rig.run(ListDirectory(None)))
    assert content == "file\tok.txt\n[1 entries omitted by policy]"
    assert rig.reasons() == ["path.junction_unverified"]
    assert rig.executor.usage().denials == 0  # filtro interno: a chamada terminou OK


def test_f_ausencia_comprovada_nao_e_inspection_error(rig: Rig) -> None:
    rig.put("a/existe.txt")
    for relativo in ("nada.txt", "a/nada.txt", "nada/nada/nada.txt", "a/existe.txt/x"):
        facts = inspect(relativo, rig.root.path, tolerate_absent_tail=True)
        assert facts.inspection_error is None, relativo
        assert not facts.exists


# ========================================================================= B-AUD-005 — primitiva


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_b_aud_005_lstat_tecnico_da_entrada_e_inspection_failed_nao_integridade(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("a.txt")
    rig.put("b.txt")
    fail_os_lstat(monkeypatch, "b.txt", erro)
    with pytest.raises(PathOperationFailed) as caught:
        list_directory(inspect(".", rig.root.path), rig.root)
    assert caught.value.category is PathFailure.INSPECTION_FAILED
    assert str(caught.value) == "path_inspection_failed"
    assert "segredo" not in repr(caught.value.args)


def test_b_aud_005_entrada_que_some_depois_do_scandir_continua_integridade(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt")
    rig.put("b.txt")
    fail_os_lstat(monkeypatch, "b.txt", FileNotFoundError(errno.ENOENT, "sumiu"))
    with pytest.raises(PathIntegrityViolation) as caught:
        list_directory(inspect(".", rig.root.path), rig.root)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE


def test_b_aud_005_entrada_que_some_de_verdade_durante_a_enumeracao(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt")
    rig.put("b.txt")
    ScandirHook(monkeypatch, on_exit=lambda: (rig.ws / "b.txt").unlink())
    with pytest.raises(PathIntegrityViolation) as caught:
        list_directory(inspect(".", rig.root.path), rig.root)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE


def test_b_aud_005_identidade_zero_continua_integrity_unverifiable(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt")
    zero_ids(monkeypatch, keep={rig.root.identity.file_id})
    with pytest.raises(PathIntegrityViolation) as caught:
        list_directory(inspect(".", rig.root.path), rig.root)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_b_aud_005_releitura_tecnica_do_diretorio_enumerado_e_inspection_failed(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("sub/a.txt")
    facts = inspect("sub", rig.root.path)

    def quebra_a_releitura() -> None:
        fail_os_lstat(monkeypatch, "sub", erro)

    ScandirHook(monkeypatch, on_exit=quebra_a_releitura)
    with pytest.raises(PathOperationFailed) as caught:
        list_directory(facts, rig.root)
    assert caught.value.category is PathFailure.INSPECTION_FAILED


def test_b_aud_005_o_codigo_tecnico_e_um_erro_fixo_do_executor() -> None:
    assert PathFailure.INSPECTION_FAILED.value in ERROR_PHRASES
    assert "{" not in ERROR_PHRASES[PathFailure.INSPECTION_FAILED.value]


# ======================================================================= B-AUD-005 — executor


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_b_aud_005_listdirectory_lstat_tecnico_e_error_sem_journal(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("a.txt")
    rig.put("b.txt")
    fail_os_lstat(monkeypatch, "b.txt", erro)
    result = rig.run(ListDirectory(None))
    errored(result, "path_inspection_failed")
    sem_vazamento(result)
    assert len(rig.journal) == 0 and rig.executor.usage().denials == 0


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_b_aud_005_searchtext_lstat_tecnico_e_error_sem_journal(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("a.txt", "NEEDLE\n")
    rig.put("b.txt", "NEEDLE\n")
    fail_os_lstat(monkeypatch, "b.txt", erro)
    result = rig.run(SearchText("NEEDLE"))
    errored(result, "path_inspection_failed")
    sem_vazamento(result)
    assert len(rig.journal) == 0 and rig.executor.usage().files_read == ()


def test_b_aud_005_executor_entrada_que_some_continua_denied_com_journal(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt")
    rig.put("b.txt")
    fail_os_lstat(monkeypatch, "b.txt", FileNotFoundError(errno.ENOENT, "sumiu"))
    denied(rig.run(ListDirectory(None)), "path.integrity_unverifiable")
    (record,) = rig.journal.records()
    assert record.category is DecisionCategory.TOCTOU_RECHECK_FAILED


def test_b_aud_005_executor_identidade_zero_continua_denied_com_journal(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt")
    zero_ids(monkeypatch, keep={rig.root.identity.file_id})
    denied(rig.run(ListDirectory(None)), "path.integrity_unverifiable")
    assert rig.reasons() == ["path.integrity_unverifiable"]


def test_b_aud_005_executor_diretorio_trocado_continua_toctou_com_journal(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("sub/a.txt")

    def troca() -> None:
        (rig.ws / "sub").rename(rig.ws / "sub_antigo")
        (rig.ws / "sub").mkdir()
        (rig.ws / "sub" / "a.txt").write_text("y")

    ScandirHook(monkeypatch, on_exit=troca)
    denied(rig.run(ListDirectory("sub")), "path.toctou_recheck_failed")
    assert rig.reasons() == ["path.toctou_recheck_failed"]


# ================================================================= cadeia da raiz (inventário)


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_cadeia_da_raiz_com_lstat_tecnico_e_erro_tecnico_nao_integridade(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    fail_on(monkeypatch, "lstat", Path(rig.root.path).name, erro)
    with pytest.raises(PathOperationFailed) as caught:
        path_runtime.verify_root(rig.root)
    assert caught.value.category is PathFailure.IO_ERROR
    errored(rig.run(ListDirectory(None)), "workspace_unavailable")
    assert len(rig.journal) == 0


def test_cadeia_da_raiz_com_componente_que_some_continua_integridade(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    fail_on(monkeypatch, "lstat", Path(rig.root.path).name, FileNotFoundError(errno.ENOENT, "x"))
    with pytest.raises(PathIntegrityViolation) as caught:
        path_runtime._require_clean_chain(Path(rig.root.path))
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE


# ========================================================================== teste combinado


def test_combinado_segredo_registrado_e_depois_lstat_tecnico_de_entrada(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.env` é negado de verdade (registro legítimo); depois, ao enumerar `z/`, o `lstat` de uma
    entrada falha tecnicamente. Resultado `ERROR`; o journal mantém **só** o registro real."""
    rig.put(".env", "NEEDLE\n")
    rig.put("z/b.txt", "NEEDLE\n")
    fail_os_lstat(monkeypatch, "b.txt", OSError(errno.EIO, "erro de E/S"))
    result = rig.run(SearchText("NEEDLE"))
    errored(result, "path_inspection_failed")
    (record,) = rig.journal.records()
    assert record.rule_id == "path.secret_denied" and record.subject_redacted == ".env"
    assert rig.executor.usage().denials == 0 and rig.executor.usage().files_read == ()


def test_combinado_segredo_registrado_e_depois_falha_da_inspecao_mediada(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put(".env", "NEEDLE\n")
    rig.put("z.txt", "NEEDLE\n")
    fail_on(monkeypatch, "lstat", "z.txt", OSError(errno.EIO, "erro de E/S"))
    errored(rig.run(SearchText("NEEDLE")), "path_inspection_failed")
    assert rig.reasons() == ["path.secret_denied"]


# ================================================================ _inspection_failed sem heurística


def test_inspection_failed_continua_sem_heuristica() -> None:
    import inspect as std_inspect

    corpo = std_inspect.getsource(fs_ops._inspection_failed).split('"""')[-1]
    assert corpo.strip() == "return facts.inspection_error is not None"


def test_todas_as_inspecoes_do_fs_ops_usam_o_modo_mediado() -> None:
    import inspect as std_inspect

    fonte = std_inspect.getsource(fs_ops)
    linhas = fonte.splitlines()
    chamadas = [linha for linha in linhas if "inspect(" in linha and "def " not in linha]
    assert chamadas, "nenhuma chamada encontrada"
    for linha in chamadas:
        assert "tolerate_absent_tail=True" in linha, linha
