"""E7.5-C — correções dirigidas da auditoria: C-AUD-001 e C-AUD-002.

* **C-AUD-001** — o summary de sucesso do `ApplyPatch` depende só de operação, caminho relativo e
  ordem canônica: é finalizado (redação central **uma vez** + teto de `ToolResult`) pela mesma
  fronteira do executor **antes** do primeiro efeito, e reutilizado intacto no sucesso.
* **C-AUD-002** — a contabilidade de efeitos do CREATE vem do `CreateMutationTrace` que o
  próprio `create_exclusive` preenche syscall a syscall, nunca do que o preflight observou.

Fixtures benignas: diretórios temporários, *mocks* de `os.mkdir`/`os.open`/… e do token. Nenhum
processo, shell, git ou rede.
"""

from __future__ import annotations

import dataclasses
import errno
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from app.path_runtime import (
    CreateMutationTrace,
    PathFailure,
    PathOperationFailed,
    bind_root,
    create_exclusive,
    inspect,
)
from app.safety import SafetyDecision, SafetyPolicy
from app.safety.redaction import redact as real_redact
from app.tool_executor.contracts import ApplyPatch, ToolStatus
from app.tool_executor.executor import LocalWorktreeToolExecutor
from app.tool_executor.outcome import Outcome, PreparedToolOutput
from tests.test_apply_patch_e7_5c import (
    PatchRig,
    before_effect,
    create,
    delete,
    exm,
    partial,
    po,
    swap_line,
)
from tests.test_e7_5a_audit_p2 import zero_ids
from tests.test_tool_file_ops_e7_5b import CountdownToken, denied, errored, limits, ok

EIO = OSError(errno.EIO, "erro de E/S: C:\\Users\\x\\segredo")


@pytest.fixture
def rig(tmp_path: Path) -> PatchRig:
    return PatchRig(tmp_path)


class EffectSpy:
    """Espiona as primitivas de efeito e a **criação** de `_Effects` (a fase de efeitos)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []
        self.effects_phase = 0
        for name in ("create_exclusive", "write_fd", "delete_if_identity"):
            real = getattr(po, name)

            def wrapper(*a: Any, _real: Any = real, _name: str = name, **k: Any) -> Any:
                self.calls.append(_name)
                return _real(*a, **k)

            monkeypatch.setattr(po, name, wrapper)
        real_effects = po._Effects

        def effects() -> Any:
            self.effects_phase += 1
            return real_effects()

        monkeypatch.setattr(po, "_Effects", effects)


class TraceSpy:
    """Guarda o `CreateMutationTrace` que `patch_ops` passa a `create_exclusive`."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.traces: list[CreateMutationTrace] = []
        real = po.create_exclusive

        def wrapper(*a: Any, **k: Any) -> Any:
            self.traces.append(k["mutation_trace"])
            return real(*a, **k)

        monkeypatch.setattr(po, "create_exclusive", wrapper)

    @property
    def last(self) -> tuple[int, bool]:
        trace = self.traces[-1]
        return trace.created_parent_count, trace.created_target


# ============================================================================ C-AUD-001


def _long_path(index: int, components: int) -> str:
    return f"p{index:02}/" + "/".join(["d" * 200] * components) + "/f.txt"


@pytest.mark.skipif(
    sys.platform != "win32", reason="caminho de ~5,4 KB: PATH_MAX do POSIX barra no inspect"
)
def test_contrafactual_original_50_arquivos_caminhos_longos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reprodução do finding: `max_path_bytes=6000`, ~270 KiB de patch, 50 arquivos e 50
    hunks, summary redigido > 256 KiB. Antes: 50 arquivos criados e depois `DENIED` + nota.
    Agora: `DENIED` antes da fase de efeitos, workspace idêntico, nenhuma primitiva de efeito."""
    rig = PatchRig(tmp_path)
    rig.executor._policy = SafetyPolicy(max_path_bytes=6000)
    paths = [_long_path(i, 27) for i in range(50)]
    text = "".join(create(path, "x") for path in paths)
    assert 260 * 1024 < len(text.encode("utf-8")) < 512 * 1024
    raw_summary = "\n".join(f"created\t{p}" for p in sorted(paths, key=str.encode))
    assert len(real_redact(raw_summary).encode("utf-8")) > 256 * 1024
    before = rig.snapshot()
    spy = EffectSpy(monkeypatch)

    result = rig.patch(text)

    denied(result, "limit.tool_result_content_bytes")
    assert not partial(result)
    assert rig.snapshot() == before == {}
    assert spy.calls == []
    assert spy.effects_phase == 0  # C-AUD-001 nunca chega à contabilidade de efeitos
    assert rig.rules() == ["limit.tool_result_content_bytes"]
    assert rig.executor.usage().files_read == ()


def test_summary_acima_do_teto_nega_antes_dos_efeitos_em_qualquer_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = PatchRig(tmp_path, tool_limits=limits(tool_result_content_bytes=200))
    rig.put("u.txt", "a\n")
    rig.put("d.txt", "b\n")
    text = (
        "".join(create(f"novo{i}/arquivo-{i:02}.txt", "x") for i in range(10))
        + swap_line("u.txt", 1, "a", "A")
        + delete("d.txt", "b")
    )
    before = rig.snapshot()
    spy = EffectSpy(monkeypatch)
    result = rig.patch(text)
    denied(result, "limit.tool_result_content_bytes")
    assert not partial(result)
    assert rig.snapshot() == before
    assert spy.calls == [] and spy.effects_phase == 0


def test_summary_exatamente_no_teto_passa_e_um_byte_acima_nega(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = "created\tabc.txt"
    no_teto = PatchRig(tmp_path / "a", tool_limits=limits(tool_result_content_bytes=len(summary)))
    assert ok(no_teto.patch(create("abc.txt", "x"))) == summary
    acima = PatchRig(tmp_path / "b", tool_limits=limits(tool_result_content_bytes=len(summary) - 1))
    spy = EffectSpy(monkeypatch)
    result = acima.patch(create("abc.txt", "x"))
    denied(result, "limit.tool_result_content_bytes")
    assert not partial(result)
    assert not (acima.ws / "abc.txt").exists()
    assert spy.calls == []


def test_redacao_que_encolhe_faz_caber(tmp_path: Path) -> None:
    token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    raw = f"created\t{token}.txt"
    redigido = real_redact(raw)
    assert len(redigido.encode()) < len(raw.encode())
    rig = PatchRig(tmp_path, tool_limits=limits(tool_result_content_bytes=len(redigido.encode())))
    assert ok(rig.patch(create(f"{token}.txt", "x"))) == redigido
    assert (rig.ws / f"{token}.txt").read_bytes() == b"x\n"


def test_redacao_que_expande_faz_exceder_antes_dos_efeitos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = PatchRig(tmp_path, tool_limits=limits(tool_result_content_bytes=50))
    monkeypatch.setattr(exm, "redact", lambda text: text.replace("EXP", "X" * 100))
    spy = EffectSpy(monkeypatch)
    result = rig.patch(create("EXP.txt", "x"))  # bruto: 15 bytes; redigido: 112
    denied(result, "limit.tool_result_content_bytes")
    assert not partial(result)
    assert rig.snapshot() == {}
    assert spy.calls == [] and spy.effects_phase == 0


def test_teto_do_summary_e_em_bytes_utf8(tmp_path: Path) -> None:
    raw = "created\téééé.txt"
    assert len(raw) == 16 and len(raw.encode("utf-8")) == 20
    por_caracteres = PatchRig(tmp_path / "a", tool_limits=limits(tool_result_content_bytes=16))
    denied(por_caracteres.patch(create("éééé.txt", "x")), "limit.tool_result_content_bytes")
    assert por_caracteres.snapshot() == {}
    por_bytes = PatchRig(tmp_path / "b", tool_limits=limits(tool_result_content_bytes=20))
    assert ok(por_bytes.patch(create("éééé.txt", "x"))) == raw


def test_journal_falha_na_negacao_do_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = PatchRig(tmp_path, tool_limits=limits(tool_result_content_bytes=5))
    rig.put("u.txt", "a\n")
    before = rig.snapshot()
    rig.journal.fail = True
    spy = EffectSpy(monkeypatch)
    result = rig.patch(swap_line("u.txt", 1, "a", "A") + create("n/novo.txt", "x"))
    errored(result, "journal_unavailable")
    assert not partial(result)
    assert rig.snapshot() == before
    assert spy.calls == [] and spy.effects_phase == 0
    rig.journal.fail = False
    errored(rig.patch(create("z.txt", "z")), "journal_unavailable")  # envenenado
    assert rig.snapshot() == before


def test_sucesso_entrega_exatamente_o_output_preparado_redigido_uma_vez(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("u.txt", "a\n")
    redacoes: list[str] = []

    def conta(text: str) -> str:
        redacoes.append(text)
        return str(real_redact(text))

    monkeypatch.setattr(exm, "redact", conta)
    preparados: list[PreparedToolOutput | None] = []
    real_prepare = LocalWorktreeToolExecutor._prepare_output

    def espia(self: Any, raw: str) -> PreparedToolOutput | None:
        prepared = real_prepare(self, raw)
        preparados.append(prepared)
        return prepared

    monkeypatch.setattr(LocalWorktreeToolExecutor, "_prepare_output", espia)
    ordem: list[str] = []
    before_effect(monkeypatch, lambda plan: ordem.append(f"efeito:{len(preparados)}"))

    result = rig.patch(swap_line("u.txt", 1, "a", "A") + create("b.txt", "b"))

    assert redacoes == ["created\tb.txt\nupdated\tu.txt"]  # uma vez, sobre o summary completo
    assert len(preparados) == 1 and preparados[0] is not None
    assert ordem == ["efeito:1", "efeito:1"]  # preparado antes do primeiro efeito
    assert result.status is ToolStatus.OK
    assert result.content is preparados[0].content  # o mesmo objeto: nada reconstruído


def test_output_preparado_que_nao_veio_desta_fronteira_e_recusado(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    forjado = PreparedToolOutput("conteudo que nao passou pela redacao")
    monkeypatch.setattr(
        LocalWorktreeToolExecutor,
        "_apply_patch",
        lambda self, context, request: Outcome(ToolStatus.OK, prepared=forjado),
    )
    errored(rig.patch(create("x.txt", "x")), "internal_error")


def test_prepared_exclui_content_e_so_vale_em_ok() -> None:
    from app.tool_executor.contracts import ToolStatus as Status
    from app.tool_executor.validation import ContractViolation

    prepared = PreparedToolOutput("x")
    with pytest.raises(ContractViolation):
        Outcome(Status.OK, content="y", prepared=prepared)
    with pytest.raises(ContractViolation):
        Outcome(Status.ERROR, rule_id="io_error", prepared=prepared)


def test_apply_patch_continua_sem_files_read(rig: PatchRig) -> None:
    rig.put("u.txt", "a\n")
    ok(rig.patch(swap_line("u.txt", 1, "a", "A")))
    assert rig.executor.usage().files_read == ()


# ===================================================================== C-AUD-002: trace


def _facts(root: Any, relative: str) -> Any:
    return inspect(relative, root.path, tolerate_absent_tail=True)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    path = tmp_path / "ws"
    path.mkdir()
    return path


def _create(ws: Path, relative: str, trace: CreateMutationTrace, **kwargs: Any) -> None:
    root = bind_root(ws)
    created = create_exclusive(_facts(root, relative), root, mutation_trace=trace, **kwargs)
    created.close()


def _mkdir_then(monkeypatch: pytest.MonkeyPatch, *, fail_call: int, error: BaseException) -> None:
    """`os.mkdir` real nas chamadas anteriores a ``fail_call``; nela, ``error``."""
    real = os.mkdir
    calls = {"n": 0}

    def fake(path: Any, *args: Any, **kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] == fail_call:
            raise error
        real(path, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", fake)


def test_trace_sem_pai_criado(ws: Path) -> None:
    trace = CreateMutationTrace()
    _create(ws, "x.txt", trace)
    assert (trace.created_parent_count, trace.created_target) == (0, True)


def test_trace_um_pai(ws: Path) -> None:
    trace = CreateMutationTrace()
    _create(ws, "a/x.txt", trace)
    assert (trace.created_parent_count, trace.created_target) == (1, True)


def test_trace_dois_pais(ws: Path) -> None:
    trace = CreateMutationTrace()
    _create(ws, "a/b/x.txt", trace)
    assert (trace.created_parent_count, trace.created_target) == (2, True)


def test_trace_pai_existente_nao_conta(ws: Path) -> None:
    (ws / "e").mkdir()
    trace = CreateMutationTrace()
    _create(ws, "e/f/x.txt", trace)
    assert (trace.created_parent_count, trace.created_target) == (1, True)


def test_trace_sobrevive_a_erro_depois_do_pai(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mkdir_then(monkeypatch, fail_call=2, error=EIO)
    trace = CreateMutationTrace()
    with pytest.raises(PathOperationFailed) as caught:
        _create(ws, "a/b/x.txt", trace)
    assert caught.value.category is PathFailure.IO_ERROR
    assert (trace.created_parent_count, trace.created_target) == (1, False)
    assert (ws / "a").is_dir() and not (ws / "a" / "b").exists()


def test_trace_sobrevive_a_cancelamento_depois_do_pai(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flag = {"cancel": False}
    real = os.mkdir

    def mkdir_e_cancela(path: Any, *args: Any, **kwargs: Any) -> None:
        real(path, *args, **kwargs)
        flag["cancel"] = True

    monkeypatch.setattr(os, "mkdir", mkdir_e_cancela)
    trace = CreateMutationTrace()
    with pytest.raises(PathOperationFailed) as caught:
        _create(ws, "a/b/x.txt", trace, is_cancelled=lambda: flag["cancel"])
    assert caught.value.category is PathFailure.CANCELLED
    assert (trace.created_parent_count, trace.created_target) == (1, False)


def test_trace_o_excl_recusado_nao_conta_alvo(ws: Path) -> None:
    root = bind_root(ws)
    facts = _facts(root, "x.txt")
    (ws / "x.txt").write_bytes(b"de outro\n")  # aparece entre a inspeção e a criação
    trace = CreateMutationTrace()
    with pytest.raises(PathOperationFailed) as caught:
        create_exclusive(facts, root, mutation_trace=trace)
    assert caught.value.category is PathFailure.ALREADY_EXISTS
    assert (trace.created_parent_count, trace.created_target) == (0, False)


def test_trace_diretorio_criado_por_outro_nao_conta(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = os.mkdir

    def outro_cria_antes(path: Any, *args: Any, **kwargs: Any) -> None:
        real(path, *args, **kwargs)
        raise FileExistsError(errno.EEXIST, "existe")

    monkeypatch.setattr(os, "mkdir", outro_cria_antes)
    trace = CreateMutationTrace()
    _create(ws, "a/x.txt", trace)
    assert (trace.created_parent_count, trace.created_target) == (0, True)


def test_trace_sem_trace_continua_compativel(ws: Path) -> None:
    root = bind_root(ws)
    created = create_exclusive(_facts(root, "a/x.txt"), root)
    created.close()
    assert (ws / "a" / "x.txt").exists()


def test_trace_nao_guarda_caminho(ws: Path) -> None:
    trace = CreateMutationTrace()
    _create(ws, "a/x.txt", trace)
    assert {f.name for f in dataclasses.fields(trace)} == {"created_parent_count", "created_target"}
    assert repr(trace) == "CreateMutationTrace(created_parent_count=1, created_target=True)"


# =================================================================== C-AUD-002: ApplyPatch


def _race_remove_parent(rig: PatchRig) -> None:
    """`p/q` existia no preflight; antes do efeito, some inteiro."""
    shutil.rmtree(rig.ws / "p")


def test_corrida_pai_recriado_e_cancelamento(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contrafactual C-AUD-002 (A): o preflight viu `p/q`; a corrida o remove; `create_exclusive`
    recria `p` e o cancelamento chega antes do próximo nível. `p` fica: nota obrigatória."""
    (rig.ws / "p" / "q").mkdir(parents=True)
    before_effect(monkeypatch, lambda plan: _race_remove_parent(rig))
    real = os.mkdir

    def mkdir_e_cancela(path: Any, *args: Any, **kwargs: Any) -> None:
        real(path, *args, **kwargs)
        rig.token.cancelled = True

    monkeypatch.setattr(os, "mkdir", mkdir_e_cancela)
    traces = TraceSpy(monkeypatch)
    result = rig.patch(create("p/q/n.txt", "n"))
    errored(result, "operation_cancelled")
    assert partial(result)
    assert traces.last == (1, False)
    assert (rig.ws / "p").is_dir() and not (rig.ws / "p" / "q").exists()


def test_corrida_pai_recriado_e_eio(rig: PatchRig, monkeypatch: pytest.MonkeyPatch) -> None:
    """Contrafactual C-AUD-002 (B): `p` recriado, depois EIO ao criar `q`."""
    (rig.ws / "p" / "q").mkdir(parents=True)
    before_effect(monkeypatch, lambda plan: _race_remove_parent(rig))
    _mkdir_then(monkeypatch, fail_call=2, error=EIO)
    traces = TraceSpy(monkeypatch)
    result = rig.patch(create("p/q/n.txt", "n"))
    errored(result, "patch_partial_apply")
    assert partial(result)
    assert traces.last == (1, False)
    assert (rig.ws / "p").is_dir()


def test_pai_ausente_desde_o_preflight_e_falha(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mkdir_then(monkeypatch, fail_call=2, error=EIO)
    result = rig.patch(create("n/m/x.txt", "x"))
    errored(result, "patch_partial_apply")
    assert partial(result)
    assert (rig.ws / "n").is_dir()


def test_falha_antes_de_qualquer_mutacao_nao_tem_nota(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    (rig.ws / "p").mkdir()
    before = rig.snapshot()
    real = os.open

    def excl_falha(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if flags & os.O_EXCL:
            raise EIO
        return real(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", excl_falha)
    traces = TraceSpy(monkeypatch)
    result = rig.patch(create("p/x.txt", "x"))
    monkeypatch.undo()
    errored(result, "io_error")
    assert not partial(result)
    assert traces.last == (0, False)
    assert rig.snapshot() == before


def _deny_post_create(monkeypatch: pytest.MonkeyPatch) -> None:
    negar = SafetyDecision(False, "path.post_create_reparse", "x", "alvo")
    monkeypatch.setattr(po, "decide_post_create", lambda *_a, **_k: negar)


def test_limpeza_do_alvo_com_pais_realmente_preexistentes_nao_tem_nota(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    (rig.ws / "p").mkdir()
    before = rig.snapshot()
    _deny_post_create(monkeypatch)
    traces = TraceSpy(monkeypatch)
    result = rig.patch(create("p/x.txt", "x"))
    denied(result, "path.post_create_reparse")
    assert not partial(result)
    assert traces.last == (0, True)
    assert rig.snapshot() == before


def test_limpeza_do_alvo_com_pai_criado_tem_nota(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deny_post_create(monkeypatch)
    traces = TraceSpy(monkeypatch)
    result = rig.patch(create("novo/x.txt", "x"))
    denied(result, "path.post_create_reparse")
    assert partial(result)
    assert traces.last == (1, True)
    assert (rig.ws / "novo").is_dir() and not (rig.ws / "novo" / "x.txt").exists()


def test_identidade_zero_deixa_o_alvo_e_tem_nota(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    zero_ids(monkeypatch, keep={os.stat(rig.ws).st_ino})
    traces = TraceSpy(monkeypatch)
    result = rig.patch(create("x.txt", "x"))
    denied(result, "path.integrity_unverifiable")
    assert partial(result)
    assert traces.last == (0, True)
    assert (rig.ws / "x.txt").read_bytes() == b""


def test_corrida_de_pai_sem_falha_continua_ok(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pai removido e recriado, sem falha: a identidade do pai mudou — integridade, com nota."""
    (rig.ws / "p" / "q").mkdir(parents=True)
    before_effect(monkeypatch, lambda plan: _race_remove_parent(rig))
    result = rig.patch(create("p/q/n.txt", "n"))
    denied(result, "path.parent_changed")
    assert partial(result)  # `p` e `q` recriados permanecem
    assert (rig.ws / "p" / "q").is_dir()


# ================================================================= C-AUD-002: varreduras


def _sweep_patch(rig: PatchRig) -> str:
    (rig.ws / "p" / "q").mkdir(parents=True)
    rig.put("u.txt", "u\n")
    rig.put("d.txt", "d\n")
    return (
        create("a/b/novo.txt", "n")
        + create("p/q/n.txt", "n")
        + swap_line("u.txt", 1, "u", "U")
        + delete("d.txt", "d")
    )


@pytest.mark.parametrize("race", [False, True], ids=["sem-corrida", "pai-removido"])
def test_varredura_de_cancelamento_com_diretorios(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, race: bool
) -> None:
    """Cancela em cada consulta ao token. Sem a nota ⇒ o snapshot completo (arquivos **e**
    diretórios) é o que havia — descontada só a mudança externa da corrida, se houve."""
    vistos = {"limpo": 0, "parcial": 0}
    for after in range(400):
        with monkeypatch.context() as mp:
            rig = PatchRig(tmp_path / str(after), token=CountdownToken(after))
            text = _sweep_patch(rig)
            state = {"expected": rig.snapshot(), "raced": False}

            def corrida(plan: Any, _rig: PatchRig = rig, _state: Any = state) -> None:
                if race and plan.relative == "p/q/n.txt" and not _state["raced"]:
                    shutil.rmtree(_rig.ws / "p")
                    _state["raced"] = True
                    _state["expected"] = _rig.snapshot()

            before_effect(mp, corrida)
            result = rig.patch(text)
        if result.status is not ToolStatus.ERROR:
            break  # passou de todas as consultas: OK (ou integridade da corrida)
        errored(result, "operation_cancelled")
        if partial(result):
            vistos["parcial"] += 1
        else:
            assert rig.snapshot() == state["expected"], after
            vistos["limpo"] += 1
    else:
        pytest.fail("a varredura não alcançou o fim da operação")
    assert vistos["limpo"] > 5 and vistos["parcial"] > 5, vistos


_IO_FUNCS = ("mkdir", "open", "lstat", "fstat", "read", "write", "fsync", "ftruncate", "unlink")


def test_varredura_de_eio_na_fase_de_efeitos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EIO na *k*-ésima syscall da fase de efeitos, para cada *k*. Workspace mudou ⇒ nota."""
    vistos = {"limpo": 0, "parcial": 0}
    for k in range(1, 600):
        with monkeypatch.context() as mp:
            rig = PatchRig(tmp_path / str(k))
            text = _sweep_patch(rig)
            before = rig.snapshot()
            armed = {"on": False, "n": 0}
            for name in _IO_FUNCS:
                real = getattr(os, name)

                def fake(
                    *a: Any, _real: Any = real, _armed: Any = armed, _k: int = k, **kw: Any
                ) -> Any:
                    if _armed["on"]:
                        _armed["n"] += 1
                        if _armed["n"] == _k:
                            raise OSError(errno.EIO, "erro de E/S")
                    return _real(*a, **kw)

                mp.setattr(os, name, fake)

            def arma(_plan: Any, _armed: Any = armed) -> None:
                _armed["on"] = True

            before_effect(mp, arma)
            result = rig.patch(text)
            armed["on"] = False
        if result.status is ToolStatus.OK:
            assert armed["n"] < k  # nenhuma falha injetada sobrou
            break
        if partial(result):
            vistos["parcial"] += 1
        else:
            assert rig.snapshot() == before, (k, result)
            vistos["limpo"] += 1
    else:
        pytest.fail("a varredura não alcançou o fim da operação")
    assert vistos["limpo"] > 3 and vistos["parcial"] > 10, vistos


def test_apply_patch_request_continua_o_mesmo_contrato() -> None:
    assert ApplyPatch(create("x", "y")).patch.startswith("--- /dev/null")
