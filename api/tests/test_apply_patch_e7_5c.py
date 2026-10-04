"""E7.5-C — `ApplyPatch` pelo executor concreto (`LocalWorktreeToolExecutor.execute`).

Todos os testes passam pela mesma porta do provider, sobre um diretório temporário real.
Fixtures benignas: arquivos de texto, uma junction NTFS (ou symlink em POSIX) e *mocks* das
primitivas de `path_runtime` reimportadas em `patch_ops`/`fs_ops` para simular corrida,
cancelamento, falha de E/S e falha do journal. Nenhum git, processo, shell ou rede: o patch é
**dado**, nunca código.

Limites pequenos vêm de um `ToolLimits` de teste (`dataclasses.replace`); os valores reais de
`TOOL_LIMITS_V1` também são exercitados nas bordas (wiring).
"""

from __future__ import annotations

import dataclasses
import errno
import hashlib
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.path_runtime import bind_root
from app.safety import TOOL_LIMITS_V1, SafetyDecision, SafetyPolicy, ToolLimits
from app.safety.capability_profile import (
    DEVELOPER_V1_PROFILE,
    EnforcementMode,
    ProviderCapabilityProfile,
)
from app.tool_executor import executor as executor_module
from app.tool_executor import fs_ops, patch_ops
from app.tool_executor.contracts import (
    ApplyPatch,
    DecisionCategory,
    GitStatus,
    ReadFile,
    RunScope,
    ToolDecisionRecord,
    ToolResult,
    ToolStatus,
)
from app.tool_executor.executor import LocalWorktreeToolExecutor
from app.tool_executor.journal import InMemoryDecisionJournal
from app.tool_executor.reasons import PARTIAL_APPLY_NOTE
from app.tool_executor.unified_diff import PatchOperation
from app.tool_executor.validation import ContractViolation
from app.tool_executor.workspace import ResolvedWorkspace
from tests.test_e7_5a_audit_p2 import zero_ids
from tests.test_e7_5b_audit_redaction_and_inspect import fail_on
from tests.test_tool_file_ops_e7_5b import (
    CountdownToken,
    Token,
    denied,
    errored,
    limits,
    make_dir_link,
    ok,
)

exm: Any = executor_module
po: Any = patch_ops  # primitivas reimportadas em `patch_ops` são alvo de `monkeypatch`
fsx: Any = fs_ops
COMMIT = "e" * 40
REDACTED = "«redigido»"

ERROS = [
    OSError(errno.EIO, "erro de E/S: C:\\Users\\x\\segredo"),
    PermissionError(errno.EACCES, "negado: C:\\Users\\x\\segredo"),
    OSError(errno.EINVAL, "genérico: C:\\Users\\x\\segredo"),
]
IDS = ["eio", "permissao", "oserror"]


# ------------------------------------------------------------------------------------ apoio


class SwitchJournal(InMemoryDecisionJournal):
    """Journal que passa a falhar quando ``fail`` vira `True`."""

    def __init__(self) -> None:
        super().__init__()
        self.fail = False

    def append(self, record: ToolDecisionRecord) -> None:
        if self.fail:
            raise RuntimeError("journal fora do ar: C:\\Users\\x\\segredo")
        super().append(record)


class PatchRig:
    def __init__(
        self,
        tmp_path: Path,
        *,
        tool_limits: ToolLimits | None = None,
        token: Any = None,
        profile: ProviderCapabilityProfile = DEVELOPER_V1_PROFILE,
    ) -> None:
        self.ws = tmp_path / "ws"
        self.ws.mkdir(parents=True)
        self.outside = tmp_path / "outside"
        self.outside.mkdir()
        self.token = token if token is not None else Token()
        self.journal = SwitchJournal()
        self.root = bind_root(self.ws)
        self.executor = LocalWorktreeToolExecutor(
            workspace=ResolvedWorkspace(
                workspace_id="wt-c",
                task_id="t1",
                run_id="r1",
                base_commit=COMMIT,
                workspace_path=str(self.ws),
                root_identity=self.root.identity,
            ),
            root=self.root,
            policy=SafetyPolicy(),
            profile=profile,
            run_scope=RunScope("t1", "r1", "i1"),
            cancel_token=self.token,
            journal=self.journal,
            limits=tool_limits if tool_limits is not None else TOOL_LIMITS_V1,
        )

    def put(self, relative: str, data: bytes | str) -> Path:
        path = self.ws / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def read(self, relative: str) -> bytes:
        return (self.ws / relative).read_bytes()

    def patch(self, text: str) -> ToolResult:
        return self.executor.execute(ApplyPatch(text))

    def rules(self) -> list[str]:
        return [record.rule_id for record in self.journal.records()]

    def snapshot(self) -> dict[str, bytes | None]:
        """Todo o conteúdo do workspace (diretório → `None`)."""
        state: dict[str, bytes | None] = {}
        for path in sorted(self.ws.rglob("*")):
            key = path.relative_to(self.ws).as_posix()
            state[key] = None if path.is_dir() else path.read_bytes()
        return state


@pytest.fixture
def rig(tmp_path: Path) -> PatchRig:
    return PatchRig(tmp_path)


def update(path: str, *hunks: str) -> str:
    return f"--- a/{path}\n+++ b/{path}\n" + "".join(hunks)


def create(path: str, *lines: str) -> str:
    body = "".join(f"+{line}\n" for line in lines)
    return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{body}"


def delete(path: str, *lines: str) -> str:
    body = "".join(f"-{line}\n" for line in lines)
    return f"--- a/{path}\n+++ /dev/null\n@@ -1,{len(lines)} +0,0 @@\n{body}"


def swap_line(path: str, line: int, old: str, new: str) -> str:
    return update(path, f"@@ -{line} +{line} @@\n-{old}\n+{new}\n")


def partial(result: ToolResult) -> bool:
    assert result.reason is not None
    return result.reason.endswith(PARTIAL_APPLY_NOTE)


def no_leak(result: ToolResult) -> None:
    text = repr(result)
    for raw in ("Errno", "WinError", "segredo", "C:\\Users\\x", "Traceback"):
        assert raw not in text


class Effects:
    """Espiona as primitivas de **efeito** reimportadas em `patch_ops` (e em `fs_ops`, que o
    `ApplyPatch` não deve usar para efeito). `events` registra a ordem."""

    NAMES = ("create_exclusive", "write_fd", "delete_if_identity", "discard_created")

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.events: list[str] = []
        for module, label in ((po, ""), (fsx, "fs_ops.")):
            for name in self.NAMES:
                if hasattr(module, name):
                    self._wrap(monkeypatch, module, name, label + name)
        real_plan = po._plan

        def plan(context: Any, target: Any) -> Any:
            self.events.append(f"plan:{target.relative}")
            return real_plan(context, target)

        monkeypatch.setattr(po, "_plan", plan)

    def _wrap(self, monkeypatch: pytest.MonkeyPatch, module: Any, name: str, label: str) -> None:
        real = getattr(module, name)

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            self.events.append(label)
            return real(*args, **kwargs)

        monkeypatch.setattr(module, name, wrapper)

    @property
    def effects(self) -> list[str]:
        return [e for e in self.events if not e.startswith("plan:")]


def before_effect(monkeypatch: pytest.MonkeyPatch, callback: Callable[[Any], None]) -> None:
    """Chama ``callback(plan)`` imediatamente antes do efeito de cada arquivo."""
    for operation, real in list(po._EFFECT.items()):

        def hooked(context: Any, plan: Any, effects: Any, _real: Any = real) -> None:
            callback(plan)
            _real(context, plan, effects)

        monkeypatch.setitem(po._EFFECT, operation, hooked)


def swap_identity(path: Path, data: bytes | None = None) -> None:
    """Troca o objeto no caminho (identidade nova) mantendo — ou não — o mesmo conteúdo."""
    content = path.read_bytes() if data is None else data
    tmp = path.with_name(path.name + ".tmp-swap")
    tmp.write_bytes(content)
    os.replace(tmp, path)


def rewrite_in_place(path: Path, data: bytes) -> None:
    """Mesmo objeto (mesma identidade), conteúdo diferente."""
    with path.open("r+b") as handle:
        handle.seek(0)
        handle.write(data)
        handle.truncate()


# =================================================================== 1. capability e ordem


def test_sem_write_files_nega_antes_de_qualquer_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = dataclasses.replace(DEVELOPER_V1_PROFILE, write_files=EnforcementMode.DISABLED)
    rig = PatchRig(tmp_path, profile=profile)

    def proibido(*_a: object, **_k: object) -> None:
        raise AssertionError("IO antes do capability gate")

    monkeypatch.setattr(executor_module, "verify_root", proibido)
    monkeypatch.setattr(po, "parse_unified_diff", proibido)
    for nome in ("inspect", "open_existing", "create_exclusive"):
        monkeypatch.setattr(fsx, nome, proibido)
    denied(rig.patch(create("x.txt", "a")), "capability.denied")
    assert rig.rules() == ["capability.denied"]
    assert not (rig.ws / "x.txt").exists()


def test_patch_malformado_nao_toca_o_filesystem(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def proibido(*_a: object, **_k: object) -> None:
        raise AssertionError("IO para um patch que nem parseia")

    monkeypatch.setattr(fsx, "inspect", proibido)
    errored(rig.patch("--- a/x\n+++ b/x\n@@ -1 +1 @@\n*a\n"), "invalid_patch")
    errored(rig.patch("diff --git a/x b/x\n" + create("x", "a")), "unsupported_patch_format")
    assert rig.rules() == []


def test_nul_no_patch_nem_chega_ao_executor() -> None:
    with pytest.raises(ContractViolation):
        ApplyPatch(create("x", "a\x00b"))


def test_patch_com_surrogate_e_invalid_patch(rig: PatchRig) -> None:
    errored(rig.patch(create("x.txt", "a\ud800b")), "invalid_patch")
    assert rig.snapshot() == {}


# ======================================================================== 2. operações OK


def test_update_create_delete_num_patch_so(rig: PatchRig) -> None:
    rig.put("a.txt", "um\ndois\ntres\n")
    rig.put("velho.txt", "fim\n")
    text = (
        update("a.txt", "@@ -2 +2,2 @@\n-dois\n+DOIS\n+dois e meio\n")
        + create("novo/sub/n.txt", "x", "y")
        + delete("velho.txt", "fim")
    )
    content = ok(rig.patch(text))
    assert content == "updated\ta.txt\ncreated\tnovo/sub/n.txt\ndeleted\tvelho.txt"
    assert rig.read("a.txt") == b"um\nDOIS\ndois e meio\ntres\n"
    assert rig.read("novo/sub/n.txt") == b"x\ny\n"
    assert not (rig.ws / "velho.txt").exists()
    assert rig.rules() == []


def test_create_normal_usa_lf(rig: PatchRig) -> None:
    assert ok(rig.patch(create("c.txt", "a", "b"))) == "created\tc.txt"
    assert rig.read("c.txt") == b"a\nb\n"


def test_create_com_pais_aninhados(rig: PatchRig) -> None:
    ok(rig.patch(create("p/q/r/s.txt", "z")))
    assert rig.read("p/q/r/s.txt") == b"z\n"


def test_delete_normal_usa_delete_if_identity(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("d.txt", "a\nb\n")
    spy = Effects(monkeypatch)
    assert ok(rig.patch(delete("d.txt", "a", "b"))) == "deleted\td.txt"
    assert not (rig.ws / "d.txt").exists()
    assert spy.effects == ["delete_if_identity"]


# ================================================================================ 3. limites


def _create_patch_of_size(path: str, size: int) -> str:
    lines = 512
    header = f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{lines} @@\n"
    budget = size - len(header.encode())
    per = budget // lines
    body = ["+" + "x" * (per - 2) + "\n"] * lines
    body[-1] = "+" + "x" * (per - 2 + budget - per * lines) + "\n"
    text = header + "".join(body)
    assert len(text.encode("utf-8")) == size
    return text


def test_patch_de_exatamente_512_kib_passa_e_um_byte_a_mais_e_negado(rig: PatchRig) -> None:
    assert TOOL_LIMITS_V1.apply_patch_input_bytes == 512 * 1024
    no_teto = _create_patch_of_size("teto.txt", 512 * 1024)
    ok(rig.patch(no_teto))
    acima = _create_patch_of_size("acima.txt", 512 * 1024 + 1)
    denied(rig.patch(acima), "limit.apply_patch_input_bytes")
    assert not (rig.ws / "acima.txt").exists()
    assert rig.journal.records()[-1].category is DecisionCategory.LIMIT_EXCEEDED


def test_50_arquivos_passam_e_51_sao_negados(rig: PatchRig) -> None:
    assert TOOL_LIMITS_V1.apply_patch_max_files == 50
    ok(rig.patch("".join(create(f"f{i:02}.txt", "x") for i in range(50))))
    assert len(list(rig.ws.iterdir())) == 50
    denied(
        rig.patch("".join(create(f"g{i:02}.txt", "x") for i in range(51))),
        "limit.apply_patch_max_files",
    )
    assert not any(p.name.startswith("g") for p in rig.ws.iterdir())


def _odd_line_hunks(count: int) -> str:
    return "".join(f"@@ -{k} +{k} @@\n-l{k}\n+L{k}\n" for k in range(1, 2 * count, 2))


def test_500_hunks_passam_e_501_sao_negados(rig: PatchRig) -> None:
    assert TOOL_LIMITS_V1.apply_patch_max_hunks == 500
    original = "".join(f"l{k}\n" for k in range(1, 1002))
    rig.put("h.txt", original)
    denied(rig.patch(update("h.txt", _odd_line_hunks(501))), "limit.apply_patch_max_hunks")
    assert rig.read("h.txt") == original.encode()
    ok(rig.patch(update("h.txt", _odd_line_hunks(500))))
    assert rig.read("h.txt").startswith(b"L1\nl2\nL3\n")


def test_hunks_contam_no_patch_inteiro(tmp_path: Path) -> None:
    rig = PatchRig(tmp_path, tool_limits=limits(apply_patch_max_hunks=2))
    rig.put("a.txt", "a\n")
    rig.put("b.txt", "b\n")
    text = (
        swap_line("a.txt", 1, "a", "A")
        + update("b.txt", "@@ -1 +1 @@\n-b\n+B\n")
        + create("c.txt", "c")
    )
    denied(rig.patch(text), "limit.apply_patch_max_hunks")
    assert rig.read("a.txt") == b"a\n"


def test_resultado_de_exatamente_1_mib_passa_e_um_byte_a_mais_e_negado(rig: PatchRig) -> None:
    mib = 1024 * 1024
    assert TOOL_LIMITS_V1.apply_patch_result_file_bytes == mib
    base = "a" * (mib - 101) + "\n"  # mib - 100 bytes
    rig.put("big.txt", base)
    denied(
        rig.patch(update("big.txt", "@@ -1,0 +2 @@\n+" + "y" * 100 + "\n")),
        "limit.apply_patch_result_file_bytes",
    )
    assert rig.read("big.txt") == base.encode()
    ok(rig.patch(update("big.txt", "@@ -1,0 +2 @@\n+" + "y" * 99 + "\n")))
    assert len(rig.read("big.txt")) == mib


def test_teto_do_resultado_vale_para_create(tmp_path: Path) -> None:
    rig = PatchRig(tmp_path, tool_limits=limits(apply_patch_result_file_bytes=4))
    ok(rig.patch(create("c4.txt", "abc")))  # 4 bytes
    denied(rig.patch(create("c5.txt", "abcd")), "limit.apply_patch_result_file_bytes")
    assert not (rig.ws / "c5.txt").exists()


def test_linha_de_base_acima_do_teto_derivado_e_negada_sem_ler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = PatchRig(
        tmp_path,
        tool_limits=limits(apply_patch_result_file_bytes=100, apply_patch_input_bytes=1000),
    )
    rig.put("grande.txt", "a\n" + "b" * 2000 + "\n")
    lidos: list[int] = []
    real = po.read_fd

    def espia(fd: int, n: int, **k: Any) -> bytes:
        lidos.append(n)
        return bytes(real(fd, n, **k))

    monkeypatch.setattr(po, "read_fd", espia)
    denied(rig.patch(swap_line("grande.txt", 1, "a", "A")), "limit.apply_patch_result_file_bytes")
    assert lidos == []  # o tamanho do handle já basta


# ========================================================================= 4. marcador


def test_marcador_de_redacao_no_patch_nega_antes_de_io(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def proibido(*_a: object, **_k: object) -> None:
        raise AssertionError("IO depois de um marcador no patch")

    monkeypatch.setattr(fsx, "inspect", proibido)
    denied(rig.patch(create("m.txt", f"token={REDACTED}")), "content.redaction_marker")
    denied(rig.patch(create("m.txt", "chave «chave redigida 1»")), "content.redaction_marker")
    assert rig.journal.records()[-1].category is DecisionCategory.SECRET_ACCESS_BLOCKED


def test_marcador_no_resultado_nega_antes_do_primeiro_efeito(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A linha de base já carregava o marcador numa linha que o patch não toca."""
    rig.put("a.txt", "a\n")
    rig.put("m.txt", f"x\nsenha={REDACTED}\n")
    spy = Effects(monkeypatch)
    text = swap_line("a.txt", 1, "a", "A") + swap_line("m.txt", 1, "x", "X")
    denied(rig.patch(text), "content.redaction_marker")
    assert spy.effects == []
    assert rig.read("a.txt") == b"a\n"
    assert rig.rules() == ["content.redaction_marker"]
    assert rig.journal.records()[0].subject_redacted == "m.txt"


# ============================================================ 5. preflight global: zero efeito


def _setup_valid_a(rig: PatchRig) -> str:
    rig.put("a.txt", "a1\na2\n")
    return swap_line("a.txt", 1, "a1", "A1") + create("a-novo.txt", "n")


def _b_malformed(rig: PatchRig) -> tuple[str, str, str]:
    return "--- a/z.txt\n+++ b/z.txt\n@@ -1 +1 @@\n?z\n", "error", "invalid_patch"


def _b_conflict(rig: PatchRig) -> tuple[str, str, str]:
    rig.put("z.txt", "z\n")
    return swap_line("z.txt", 1, "outra", "Z"), "error", "patch_conflict"


def _b_secret(rig: PatchRig) -> tuple[str, str, str]:
    rig.put(".env", "K=v\n")
    return swap_line(".env", 1, "K=v", "K=w"), "denied", "path.secret_denied"


def _b_dot_git(rig: PatchRig) -> tuple[str, str, str]:
    rig.put(".git/config", "[core]\n")
    return swap_line(".git/config", 1, "[core]", "[x]"), "denied", "path.dot_git_denied"


def _b_result_too_big(rig: PatchRig) -> tuple[str, str, str]:
    rig.put("z.txt", "z\n" + "q" * (1024 * 1024 - 10) + "\n")
    return (
        update("z.txt", "@@ -1 +1 @@\n-z\n+" + "Z" * 100 + "\n"),
        "denied",
        "limit.apply_patch_result_file_bytes",
    )


def _b_not_utf8(rig: PatchRig) -> tuple[str, str, str]:
    rig.put("z.txt", b"\xff\xfe z\n")
    return swap_line("z.txt", 1, "z", "Z"), "error", "not_text"


def _b_binary(rig: PatchRig) -> tuple[str, str, str]:
    rig.put("z.txt", b"z\x00\n")
    return swap_line("z.txt", 1, "z", "Z"), "error", "not_text"


def _b_directory(rig: PatchRig) -> tuple[str, str, str]:
    (rig.ws / "z.txt").mkdir()
    return swap_line("z.txt", 1, "z", "Z"), "error", "is_directory"


def _b_collision(rig: PatchRig) -> tuple[str, str, str]:
    return create("Zeta.txt", "1") + create("zeta.txt", "2"), "error", "invalid_patch"


def _b_create_existing(rig: PatchRig) -> tuple[str, str, str]:
    rig.put("z.txt", "z\n")
    return create("z.txt", "Z"), "error", "already_exists"


def _b_update_absent(rig: PatchRig) -> tuple[str, str, str]:
    return swap_line("z.txt", 1, "z", "Z"), "error", "not_found"


def _b_mixed_eol(rig: PatchRig) -> tuple[str, str, str]:
    rig.put("z.txt", "z\r\ny\n")
    return swap_line("z.txt", 1, "z", "Z"), "error", "unsupported_text_format"


def _b_delete_not_empty(rig: PatchRig) -> tuple[str, str, str]:
    rig.put("z.txt", "z\ny\n")
    return delete("z.txt", "z"), "error", "invalid_patch"


def _b_parent_is_file(rig: PatchRig) -> tuple[str, str, str]:
    rig.put("zfile", "x\n")
    return create("zfile/n.txt", "n"), "error", "not_directory"


INVALID_B = {
    "malformed": _b_malformed,
    "conflict": _b_conflict,
    "secret": _b_secret,
    "dot_git": _b_dot_git,
    "result_gt_1mib": _b_result_too_big,
    "not_utf8": _b_not_utf8,
    "binary": _b_binary,
    "directory": _b_directory,
    "collision": _b_collision,
    "create_existing": _b_create_existing,
    "update_absent": _b_update_absent,
    "mixed_eol": _b_mixed_eol,
    "delete_not_empty": _b_delete_not_empty,
    "parent_is_file": _b_parent_is_file,
}


@pytest.mark.parametrize("variant", list(INVALID_B))
def test_arquivo_valido_fica_intocado_quando_outro_e_invalido(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    rig = PatchRig(tmp_path)
    valid = _setup_valid_a(rig)
    invalid, status, code = INVALID_B[variant](rig)
    before = rig.snapshot()
    spy = Effects(monkeypatch)
    for text in (valid + invalid, invalid + valid):  # a posição no patch não importa
        result = rig.patch(text)
        assert result.status.value == status, result
        assert result.reason is not None and result.reason.startswith(f"{code}:"), result
        assert not partial(result)
        assert rig.snapshot() == before
        assert spy.effects == []
    expected_rules = [code, code] if status == "denied" else []
    assert rig.rules() == expected_rules


def test_nenhum_efeito_antes_do_fim_do_preflight(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("b.txt", "b\n")
    rig.put("c.txt", "c\n")
    spy = Effects(monkeypatch)
    text = swap_line("c.txt", 1, "c", "C") + create("a.txt", "a") + delete("b.txt", "b")
    ok(rig.patch(text))
    planos = [i for i, e in enumerate(spy.events) if e.startswith("plan:")]
    efeitos = [i for i, e in enumerate(spy.events) if not e.startswith("plan:")]
    assert len(planos) == 3 and efeitos
    assert max(planos) < min(efeitos)
    assert spy.effects == ["create_exclusive", "write_fd", "delete_if_identity", "write_fd"]


def test_politica_bloqueia_antes_de_ler_qualquer_conteudo(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fase 2 (path de todos) vem antes da fase 4 (leitura): o `.env` no fim nega sem ler."""
    rig.put("a.txt", "a\n")
    rig.put(".env", "K=v\n")
    aberturas: list[object] = []
    real = po.open_existing

    def espia(*a: Any, **k: Any) -> Any:
        aberturas.append(1)
        return real(*a, **k)

    monkeypatch.setattr(po, "open_existing", espia)
    denied(rig.patch(swap_line("a.txt", 1, "a", "A") + swap_line(".env", 1, "K=v", "X")))
    assert aberturas == []


def test_negacao_registra_so_o_primeiro_bloqueio(rig: PatchRig) -> None:
    rig.put(".env", "a\n")
    rig.put(".env.local", "a\n")
    text = swap_line(".env", 1, "a", "b") + swap_line(".env.local", 1, "a", "b")
    denied(rig.patch(text), "path.secret_denied")
    assert rig.rules() == ["path.secret_denied"]
    assert rig.executor.usage().denials == 1


#: `/x` é root-relative no Windows (sem drive) e absoluto em POSIX: negado nos dois.
ROOT_SLASH_RULE = "path.root_relative" if sys.platform == "win32" else "path.absolute_not_allowed"


@pytest.mark.parametrize(
    ("path", "rule"),
    [
        ("/etc/passwd", ROOT_SLASH_RULE),
        ("../fora.txt", "path.parent_traversal"),
        ("sub/../../fora.txt", "path.parent_traversal"),
        (".git/HEAD", "path.dot_git_denied"),
        ("src/.GIT/config", "path.dot_git_denied"),
    ],
)
def test_caminho_do_cabecalho_passa_pela_pipeline_normal(
    rig: PatchRig, path: str, rule: str
) -> None:
    denied(rig.patch(create(path, "x")), rule)
    assert rig.rules() == [rule]


def test_link_ou_reparse_no_caminho_e_negado(rig: PatchRig) -> None:
    (rig.outside / "f.txt").write_bytes(b"fora\n")
    if not make_dir_link(rig.ws / "lnk", rig.outside):
        pytest.skip("sem link de diretório nesta plataforma")
    denied(rig.patch(swap_line("lnk/f.txt", 1, "fora", "DENTRO")))
    denied(rig.patch(delete("lnk/f.txt", "fora")))
    denied(rig.patch(create("lnk/novo.txt", "x")))
    assert (rig.outside / "f.txt").read_bytes() == b"fora\n"
    assert not (rig.outside / "novo.txt").exists()
    assert len(rig.rules()) == 3


# ==================================================================== 6. colisões canônicas


def test_mesmo_arquivo_com_caixa_diferente_e_colisao(rig: PatchRig) -> None:
    rig.put("Foo.py", "a\n")
    text = swap_line("Foo.py", 1, "a", "b") + swap_line("foo.py", 1, "a", "c")
    errored(rig.patch(text), "invalid_patch")
    assert rig.read("Foo.py") == b"a\n"


def test_unicode_nfc_nfd_e_colisao(rig: PatchRig) -> None:
    nfc, nfd = "caf\u00e9.txt", "cafe\u0301.txt"
    errored(rig.patch(create(nfc, "1") + create(nfd, "2")), "invalid_patch")
    assert rig.snapshot() == {}


def test_hard_link_e_colisao_por_identidade(rig: PatchRig) -> None:
    alvo = rig.put("h1.txt", "a\n")
    try:
        os.link(alvo, rig.ws / "h2.txt")
    except (OSError, NotImplementedError):
        pytest.skip("sem hard link neste filesystem")
    text = swap_line("h1.txt", 1, "a", "b") + swap_line("h2.txt", 1, "a", "c")
    errored(rig.patch(text), "invalid_patch")
    assert rig.read("h1.txt") == b"a\n"


def test_um_alvo_ancestral_de_outro_e_colisao(rig: PatchRig) -> None:
    rig.put("d", "x\n")
    errored(rig.patch(delete("d", "x") + create("d/n.txt", "n")), "invalid_patch")
    errored(rig.patch(create("e", "1") + create("E/n.txt", "n")), "invalid_patch")
    assert rig.read("d") == b"x\n"


# ================================================================================ 7. CREATE


def test_create_em_arquivo_existente_nunca_sobrescreve(rig: PatchRig) -> None:
    rig.put("c.txt", "original\n")
    errored(rig.patch(create("c.txt", "novo")), "already_exists")
    assert rig.read("c.txt") == b"original\n"


def test_create_de_arquivo_que_aparece_depois_do_preflight(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def aparece(_plan: Any) -> None:
        rig.put("c.txt", "concorrente\n")

    before_effect(monkeypatch, aparece)
    result = rig.patch(create("c.txt", "meu"))
    errored(result, "already_exists")
    assert not partial(result)  # os pais já existiam e nada foi criado
    assert rig.read("c.txt") == b"concorrente\n"


def test_create_negado_pos_criacao_remove_o_vazio(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = Effects(monkeypatch)
    negar = SafetyDecision(False, "path.post_create_reparse", "x", "c.txt")
    monkeypatch.setattr(po, "decide_post_create", lambda *_a, **_k: negar)
    result = rig.patch(create("c.txt", "conteudo"))
    denied(result, "path.post_create_reparse")
    assert not partial(result)  # limpeza comprovada, pais preexistentes
    assert not (rig.ws / "c.txt").exists()
    assert "write_fd" not in spy.effects
    assert rig.rules() == ["path.post_create_reparse"]


def test_create_negado_com_limpeza_nao_comprovavel_deixa_o_arquivo(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    negar = SafetyDecision(False, "path.post_create_reparse", "x", "c.txt")
    monkeypatch.setattr(po, "decide_post_create", lambda *_a, **_k: negar)
    real = po.discard_created

    def preenche_antes(created: Any) -> bool:
        created.close()
        (rig.ws / "c.txt").write_bytes(b"de outro\n")  # não está mais vazio
        return bool(real(created))

    monkeypatch.setattr(po, "discard_created", preenche_antes)
    result = rig.patch(create("c.txt", "conteudo"))
    denied(result, "path.post_create_reparse")
    assert partial(result)  # o arquivo criado ficou: efeito declarado
    assert rig.read("c.txt") == b"de outro\n"


def test_create_nenhum_byte_antes_da_decisao_pos_criacao(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    vistos: list[int] = []
    real = po.decide_post_create

    def observa(facts: Any, **kwargs: Any) -> Any:
        vistos.append((rig.ws / "c.txt").stat().st_size)
        return real(facts, **kwargs)

    monkeypatch.setattr(po, "decide_post_create", observa)
    ok(rig.patch(create("c.txt", "abc")))
    assert vistos == [0]
    assert rig.read("c.txt") == b"abc\n"


def test_create_identidade_zero_e_negado(rig: PatchRig, monkeypatch: pytest.MonkeyPatch) -> None:
    zero_ids(monkeypatch, keep={os.stat(rig.ws).st_ino})
    result = rig.patch(create("c.txt", "abc"))
    denied(result, "path.integrity_unverifiable")
    assert rig.journal.records()[0].category is DecisionCategory.TOCTOU_RECHECK_FAILED
    # o vazio criado fica (sem identidade não se prova que é o nosso): declarado como efeito
    assert partial(result)
    assert (rig.ws / "c.txt").read_bytes() == b""


def test_create_cancelado_antes_dos_bytes(rig: PatchRig, monkeypatch: pytest.MonkeyPatch) -> None:
    real = po.decide_post_create

    def cancela(facts: Any, **kwargs: Any) -> Any:
        rig.token.cancelled = True
        return real(facts, **kwargs)

    monkeypatch.setattr(po, "decide_post_create", cancela)
    result = rig.patch(create("c.txt", "abc"))
    errored(result, "operation_cancelled")
    assert not partial(result)  # o vazio foi removido com prova
    assert not (rig.ws / "c.txt").exists()
    errored(rig.patch(create("d.txt", "x")), "operation_cancelled")  # sticky


# ================================================================================ 8. UPDATE


def test_update_alvo_ausente(rig: PatchRig) -> None:
    errored(rig.patch(swap_line("nada.txt", 1, "a", "b")), "not_found")


def test_update_alvo_diretorio(rig: PatchRig) -> None:
    (rig.ws / "dir").mkdir()
    errored(rig.patch(swap_line("dir", 1, "a", "b")), "is_directory")


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_update_falha_tecnica_de_inspecao_e_erro(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("u.txt", "a\n")
    fail_on(monkeypatch, "lstat", "u.txt", erro)
    result = rig.patch(swap_line("u.txt", 1, "a", "b"))
    errored(result, "path_inspection_failed")
    no_leak(result)
    assert rig.rules() == []


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_update_falha_tecnica_na_revalidacao_da_primitiva_e_erro(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    """`inspect` passou; o `lstat` da primitiva (`open_existing`) falha: técnico, não política."""
    rig.put("u.txt", "a\n")
    real = os.lstat

    def fake(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if os.path.basename(os.fspath(path)) == "u.txt":
            raise erro
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", fake)
    result = rig.patch(swap_line("u.txt", 1, "a", "b"))
    errored(result, "io_error")
    no_leak(result)
    assert rig.rules() == []


def test_update_pos_open_negado_no_preflight(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("u.txt", "a\n")
    negar = SafetyDecision(False, "path.toctou_recheck_failed", "x", "u.txt")
    monkeypatch.setattr(po, "decide_post_open", lambda *_a, **_k: negar)
    denied(rig.patch(swap_line("u.txt", 1, "a", "b")), "path.toctou_recheck_failed")
    assert rig.read("u.txt") == b"a\n"


def test_update_pos_open_negado_na_fase_de_efeitos(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("u.txt", "a\n")
    spy = Effects(monkeypatch)
    real = po.decide_post_open
    chamadas = {"n": 0}

    def segunda_nega(facts: Any, **kwargs: Any) -> Any:
        chamadas["n"] += 1
        if chamadas["n"] == 2:
            return SafetyDecision(False, "path.toctou_recheck_failed", "x", "u.txt")
        return real(facts, **kwargs)

    monkeypatch.setattr(po, "decide_post_open", segunda_nega)
    result = rig.patch(swap_line("u.txt", 1, "a", "b"))
    denied(result, "path.toctou_recheck_failed")
    assert not partial(result)
    assert rig.read("u.txt") == b"a\n"
    assert "write_fd" not in spy.effects


def test_update_conteudo_alterado_no_lugar_com_mesma_identidade(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Identidade igual não basta: o conteúdo é relido pelo handle e comparado."""
    alvo = rig.put("u.txt", "a\nb\n")
    ino = os.stat(alvo).st_ino
    spy = Effects(monkeypatch)
    before_effect(monkeypatch, lambda plan: rewrite_in_place(alvo, b"a\nB\n"))
    result = rig.patch(swap_line("u.txt", 1, "a", "A"))
    errored(result, "patch_conflict")
    assert not partial(result)
    assert os.stat(alvo).st_ino == ino
    assert alvo.read_bytes() == b"a\nB\n"  # nem truncado nem sobrescrito
    assert "write_fd" not in spy.effects


def test_update_conteudo_alterado_com_mesmo_tamanho_e_detectado(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    alvo = rig.put("u.txt", "aaaa\n")
    before_effect(monkeypatch, lambda plan: rewrite_in_place(alvo, b"aaab\n"))
    errored(rig.patch(swap_line("u.txt", 1, "aaaa", "x")), "patch_conflict")
    assert alvo.read_bytes() == b"aaab\n"


def test_update_identidade_trocada_e_integridade(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    alvo = rig.put("u.txt", "a\n")
    before_effect(monkeypatch, lambda plan: swap_identity(alvo))
    result = rig.patch(swap_line("u.txt", 1, "a", "A"))
    denied(result, "path.toctou_recheck_failed")
    assert rig.journal.records()[0].category is DecisionCategory.TOCTOU_RECHECK_FAILED
    assert rig.journal.records()[0].subject_redacted == "u.txt"
    assert alvo.read_bytes() == b"a\n"


def test_update_reparse_aparece_depois_do_preflight(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    alvo = rig.put("sub/u.txt", "a\n")
    (rig.outside / "u.txt").write_bytes(b"a\n")

    def troca_por_link(_plan: Any) -> None:
        alvo.unlink()
        (rig.ws / "sub").rmdir()
        if not make_dir_link(rig.ws / "sub", rig.outside):
            pytest.skip("sem link de diretório nesta plataforma")

    before_effect(monkeypatch, troca_por_link)
    result = rig.patch(swap_line("sub/u.txt", 1, "a", "A"))
    assert result.status is ToolStatus.DENIED, result
    assert (rig.outside / "u.txt").read_bytes() == b"a\n"
    assert rig.journal.records()


def test_update_cancelado_antes_de_truncar(rig: PatchRig, monkeypatch: pytest.MonkeyPatch) -> None:
    alvo = rig.put("u.txt", "a\n")
    real = po.read_fd
    leituras = {"n": 0}

    def cancela_na_releitura(fd: int, n: int, **kwargs: Any) -> bytes:
        leituras["n"] += 1
        data = real(fd, n, **kwargs)
        if leituras["n"] == 2:  # releitura da fase de efeitos
            rig.token.cancelled = True
        return bytes(data)

    monkeypatch.setattr(po, "read_fd", cancela_na_releitura)
    result = rig.patch(swap_line("u.txt", 1, "a", "A"))
    errored(result, "operation_cancelled")
    assert not partial(result)
    assert alvo.read_bytes() == b"a\n"


def test_update_trunca_so_depois_da_decisao_e_da_releitura(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("u.txt", "a\n")
    ordem: list[str] = []
    for name in ("open_existing", "decide_post_open", "read_fd", "write_fd"):
        real = getattr(po, name)

        def wrap(*a: Any, _real: Any = real, _name: str = name, **k: Any) -> Any:
            ordem.append(_name + (":rw" if k.get("readable") else ""))
            return _real(*a, **k)

        monkeypatch.setattr(po, name, wrap)
    ok(rig.patch(swap_line("u.txt", 1, "a", "A")))
    assert ordem == [
        "open_existing",
        "decide_post_open",
        "read_fd",  # preflight
        "open_existing:rw",
        "decide_post_open",
        "read_fd",  # releitura pelo mesmo handle
        "write_fd",
    ]


# ================================================================================ 9. DELETE


def test_delete_que_nao_esvazia_e_invalido(rig: PatchRig) -> None:
    rig.put("d.txt", "a\nb\n")
    errored(rig.patch(delete("d.txt", "a")), "invalid_patch")
    assert rig.read("d.txt") == b"a\nb\n"


def test_delete_alvo_ausente(rig: PatchRig) -> None:
    errored(rig.patch(delete("d.txt", "a")), "not_found")


def test_delete_alvo_diretorio(rig: PatchRig) -> None:
    (rig.ws / "d").mkdir()
    errored(rig.patch(delete("d", "a")), "is_directory")
    assert (rig.ws / "d").is_dir()


@pytest.mark.parametrize(
    ("path", "rule"), [(".env", "path.secret_denied"), (".git/x", "path.dot_git_denied")]
)
def test_delete_de_segredo_ou_git_e_negado(rig: PatchRig, path: str, rule: str) -> None:
    rig.put(path, "a\n")
    denied(rig.patch(delete(path, "a")), rule)
    assert rig.read(path) == b"a\n"


def test_delete_conteudo_alterado_nao_apaga(rig: PatchRig, monkeypatch: pytest.MonkeyPatch) -> None:
    alvo = rig.put("d.txt", "a\n")
    spy = Effects(monkeypatch)
    before_effect(monkeypatch, lambda plan: rewrite_in_place(alvo, b"b\n"))
    errored(rig.patch(delete("d.txt", "a")), "patch_conflict")
    assert alvo.read_bytes() == b"b\n"
    assert "delete_if_identity" not in spy.effects


def test_delete_identidade_trocada_entre_comparacao_e_remocao(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    alvo = rig.put("d.txt", "a\n")
    real = po.delete_if_identity

    def troca(*a: Any, **k: Any) -> None:
        swap_identity(alvo)
        real(*a, **k)

    monkeypatch.setattr(po, "delete_if_identity", troca)
    result = rig.patch(delete("d.txt", "a"))
    denied(result, "path.toctou_recheck_failed")
    assert alvo.read_bytes() == b"a\n"  # o objeto novo não foi removido


def test_delete_passa_a_identidade_observada_pelo_handle(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    alvo = rig.put("d.txt", "a\n")
    esperado = os.stat(alvo)
    vistos: list[Any] = []
    real = po.delete_if_identity

    def espia(*a: Any, **k: Any) -> None:
        vistos.append(k["expected_identity"])
        real(*a, **k)

    monkeypatch.setattr(po, "delete_if_identity", espia)
    ok(rig.patch(delete("d.txt", "a")))
    assert len(vistos) == 1 and vistos[0].file_id == esperado.st_ino


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_delete_falha_tecnica_de_inspecao(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("d.txt", "a\n")
    fail_on(monkeypatch, "lstat", "d.txt", erro)
    result = rig.patch(delete("d.txt", "a"))
    errored(result, "path_inspection_failed")
    no_leak(result)
    monkeypatch.undo()
    assert rig.read("d.txt") == b"a\n"


# ====================================================================== 10. multi-arquivo parcial


def _three(rig: PatchRig) -> str:
    rig.put("a.txt", "a\n")
    rig.put("b.txt", "b\n")
    rig.put("c.txt", "c\n")
    return (
        swap_line("c.txt", 1, "c", "C")
        + swap_line("a.txt", 1, "a", "A")
        + swap_line("b.txt", 1, "b", "B")
    )


def _assert_partial_state(rig: PatchRig) -> None:
    assert rig.read("a.txt") == b"A\n"  # primeiro na ordem canônica: aplicado e não desfeito
    assert rig.read("c.txt") == b"c\n"  # depois da falha: nunca tocado


def test_falha_de_io_no_segundo_arquivo(rig: PatchRig, monkeypatch: pytest.MonkeyPatch) -> None:
    text = _three(rig)
    real = po.write_fd
    escritas = {"n": 0}

    def falha_na_segunda(fd: int, data: bytes, **k: Any) -> None:
        escritas["n"] += 1
        if escritas["n"] == 2:
            raise po.PathOperationFailed(po.PathFailure.IO_ERROR)
        real(fd, data, **k)

    monkeypatch.setattr(po, "write_fd", falha_na_segunda)
    result = rig.patch(text)
    errored(result, "patch_partial_apply")
    assert partial(result)
    _assert_partial_state(rig)
    assert "b.txt" not in (result.reason or "") and "a.txt" not in (result.reason or "")


def test_conteudo_alterado_no_segundo_arquivo_depois_do_primeiro_efeito(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _three(rig)

    def altera_b(plan: Any) -> None:
        if plan.relative == "b.txt":
            rewrite_in_place(rig.ws / "b.txt", b"x\n")

    before_effect(monkeypatch, altera_b)
    result = rig.patch(text)
    errored(result, "patch_partial_apply")
    assert partial(result)
    _assert_partial_state(rig)
    assert rig.read("b.txt") == b"x\n"


def test_cancelamento_no_segundo_arquivo(rig: PatchRig, monkeypatch: pytest.MonkeyPatch) -> None:
    text = _three(rig)

    def cancela(plan: Any) -> None:
        if plan.relative == "b.txt":
            rig.token.cancelled = True

    before_effect(monkeypatch, cancela)
    result = rig.patch(text)
    errored(result, "operation_cancelled")
    assert partial(result)
    _assert_partial_state(rig)
    assert rig.read("b.txt") == b"b\n"
    errored(rig.patch(create("z.txt", "z")), "operation_cancelled")  # sticky, sem nota
    assert not partial(rig.patch(create("z.txt", "z")))


def test_integridade_no_segundo_arquivo(rig: PatchRig, monkeypatch: pytest.MonkeyPatch) -> None:
    text = _three(rig)

    def troca_b(plan: Any) -> None:
        if plan.relative == "b.txt":
            swap_identity(rig.ws / "b.txt")

    before_effect(monkeypatch, troca_b)
    result = rig.patch(text)
    denied(result, "path.toctou_recheck_failed")
    assert partial(result)
    _assert_partial_state(rig)
    assert rig.rules() == ["path.toctou_recheck_failed"]
    assert rig.journal.records()[0].subject_redacted == "b.txt"


def test_create_concorrente_no_segundo_arquivo(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("a.txt", "a\n")
    rig.put("c.txt", "c\n")
    text = swap_line("a.txt", 1, "a", "A") + create("b.txt", "B") + swap_line("c.txt", 1, "c", "C")

    def aparece(plan: Any) -> None:
        if plan.relative == "b.txt":
            rig.put("b.txt", "concorrente\n")

    before_effect(monkeypatch, aparece)
    result = rig.patch(text)
    errored(result, "patch_partial_apply")
    _assert_partial_state(rig)
    assert rig.read("b.txt") == b"concorrente\n"


# ============================================================================ 11. journal


def test_falha_do_journal_no_preflight_envenena_sem_efeito(rig: PatchRig) -> None:
    rig.put("a.txt", "a\n")
    rig.put(".env", "a\n")
    rig.journal.fail = True
    before = rig.snapshot()
    result = rig.patch(swap_line("a.txt", 1, "a", "A") + swap_line(".env", 1, "a", "b"))
    errored(result, "journal_unavailable")
    assert not partial(result)
    no_leak(result)
    assert rig.snapshot() == before
    rig.journal.fail = False
    errored(rig.patch(create("z.txt", "z")), "journal_unavailable")  # envenenado
    assert not (rig.ws / "z.txt").exists()


def test_falha_do_journal_depois_do_primeiro_efeito(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _three(rig)

    def troca_b(plan: Any) -> None:
        if plan.relative == "b.txt":
            swap_identity(rig.ws / "b.txt")
            rig.journal.fail = True

    before_effect(monkeypatch, troca_b)
    result = rig.patch(text)
    errored(result, "journal_unavailable")
    assert partial(result)
    _assert_partial_state(rig)
    rig.journal.fail = False
    errored(rig.executor.execute(ReadFile("a.txt")), "journal_unavailable")


def test_negacao_de_limite_vai_ao_journal(tmp_path: Path) -> None:
    rig = PatchRig(tmp_path, tool_limits=limits(apply_patch_max_files=1))
    denied(rig.patch(create("a", "1") + create("b", "2")), "limit.apply_patch_max_files")
    (record,) = rig.journal.records()
    assert record.category is DecisionCategory.LIMIT_EXCEEDED
    assert record.operation == "ApplyPatch"


def test_erro_tecnico_nao_gera_registro(rig: PatchRig) -> None:
    rig.put("a.txt", "a\n")
    errored(rig.patch(swap_line("a.txt", 1, "x", "y")), "patch_conflict")
    errored(rig.patch("lixo\n"), "invalid_patch")
    assert rig.rules() == []
    assert rig.executor.usage().denials == 0


# ================================================================ 12. classificação técnica


def _fail_os(monkeypatch: pytest.MonkeyPatch, name: str, error: BaseException) -> None:
    real = getattr(os, name)
    state = {"on": True}

    def fake(*args: Any, **kwargs: Any) -> Any:
        if state["on"]:
            raise error
        return real(*args, **kwargs)

    monkeypatch.setattr(os, name, fake)


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_falha_de_leitura_no_preflight_e_erro_tecnico(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("u.txt", "a\n")
    _fail_os(monkeypatch, "read", erro)
    result = rig.patch(swap_line("u.txt", 1, "a", "b"))
    monkeypatch.undo()
    errored(result, "io_error")
    no_leak(result)
    assert rig.rules() == []
    assert rig.read("u.txt") == b"a\n"


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_falha_de_abertura_e_erro_tecnico(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("u.txt", "a\n")
    _fail_os(monkeypatch, "open", erro)
    result = rig.patch(swap_line("u.txt", 1, "a", "b"))
    monkeypatch.undo()
    errored(result, "io_error")
    no_leak(result)
    assert rig.rules() == []


@pytest.mark.parametrize("erro", ERROS, ids=IDS)
def test_falha_na_releitura_da_fase_de_efeitos_e_erro_tecnico(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch, erro: OSError
) -> None:
    rig.put("u.txt", "a\n")

    def quebra(plan: Any) -> None:
        _fail_os(monkeypatch, "read", erro)

    before_effect(monkeypatch, quebra)
    result = rig.patch(swap_line("u.txt", 1, "a", "b"))
    monkeypatch.undo()
    errored(result, "io_error")
    assert not partial(result)
    no_leak(result)
    assert rig.rules() == []
    assert rig.read("u.txt") == b"a\n"


# =========================================================================== 13. fim de linha


def test_crlf_preservado_pelo_executor(rig: PatchRig) -> None:
    rig.put("w.txt", b"a\r\nb\r\nc\r\n")
    ok(rig.patch(update("w.txt", "@@ -2 +2,2 @@\n-b\n+B\n+B2\n")))
    assert rig.read("w.txt") == b"a\r\nB\r\nB2\r\nc\r\n"


def test_lf_preservado_pelo_executor(rig: PatchRig) -> None:
    rig.put("w.txt", b"a\nb\n")
    ok(rig.patch(swap_line("w.txt", 2, "b", "B")))
    assert rig.read("w.txt") == b"a\nB\n"


def test_sem_newline_final_com_marcador(rig: PatchRig) -> None:
    rig.put("w.txt", b"a\nb")
    marker = "\\ No newline at end of file\n"
    ok(rig.patch(update("w.txt", f"@@ -2 +2 @@\n-b\n{marker}+B\n{marker}")))
    assert rig.read("w.txt") == b"a\nB"
    errored(rig.patch(swap_line("w.txt", 2, "B", "C")), "patch_conflict")  # falta o marcador
    assert rig.read("w.txt") == b"a\nB"


def test_misturado_e_recusado_sem_normalizar(rig: PatchRig) -> None:
    rig.put("w.txt", b"a\r\nb\nc\r\n")
    errored(rig.patch(swap_line("w.txt", 2, "b", "B")), "unsupported_text_format")
    assert rig.read("w.txt") == b"a\r\nb\nc\r\n"


def test_patch_gerado_contra_crlf_aplica_no_arquivo_crlf(rig: PatchRig) -> None:
    rig.put("w.txt", b"a\r\nb\r\n")
    ok(rig.patch(swap_line("w.txt", 1, "a", "A").replace("\n", "\r\n")))
    assert rig.read("w.txt") == b"A\r\nb\r\n"


# ========================================================================= 14. saída e usage


def test_summary_deterministico_ordenado_por_bytes_e_sem_conteudo(rig: PatchRig) -> None:
    for name in ("z.txt", "B.txt", "a.txt", "é.txt"):
        rig.put(name, "segredo-do-arquivo\n")
    sections = [
        swap_line(n, 1, "segredo-do-arquivo", "novo-conteudo")
        for n in ("z.txt", "é.txt", "B.txt", "a.txt")
    ]
    content = ok(rig.patch("".join(sections)))
    assert content.split("\n") == [
        f"updated\t{n}" for n in sorted(("z.txt", "B.txt", "a.txt", "é.txt"), key=str.encode)
    ]
    assert "segredo-do-arquivo" not in content and "novo-conteudo" not in content
    assert "@@" not in content and str(rig.ws) not in content


def test_ordem_do_patch_nao_muda_a_ordem_dos_efeitos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resultados = []
    for indice, ordem in enumerate((("c", "a", "b"), ("b", "c", "a"))):
        rig = PatchRig(tmp_path / str(indice))
        spy = Effects(monkeypatch)
        content = ok(rig.patch("".join(create(f"{n}.txt", n) for n in ordem)))
        resultados.append((content, [e for e in spy.events if e.startswith("plan:")]))
        monkeypatch.undo()
    assert resultados[0] == resultados[1]
    assert resultados[0][1] == ["plan:a.txt", "plan:b.txt", "plan:c.txt"]


def test_apply_patch_nao_conta_files_read(rig: PatchRig) -> None:
    for i in range(5):
        rig.put(f"f{i}.txt", "x\n")
    rig.put("lido.txt", "y\n")
    ok(rig.executor.execute(ReadFile("lido.txt")))
    ok(rig.patch("".join(swap_line(f"f{i}.txt", 1, "x", "X") for i in range(5))))
    ok(rig.patch(delete("f0.txt", "X")))
    assert rig.executor.usage().files_read == ("lido.txt",)


def test_redacao_global_uma_vez_sobre_o_summary_completo(
    rig: PatchRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    chamadas: list[str] = []
    real = exm.redact

    def conta(text: str) -> str:
        chamadas.append(text)
        return str(real(text))

    monkeypatch.setattr(exm, "redact", conta)
    ok(rig.patch(create("a.txt", "1") + create("b.txt", "2")))
    assert chamadas == ["created\ta.txt\ncreated\tb.txt"]


def test_caminho_com_cara_de_segredo_e_redigido_no_summary(rig: PatchRig) -> None:
    token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    content = ok(rig.patch(create(f"{token}.txt", "x")))
    assert token not in content


def test_teto_final_do_tool_result_e_fail_closed_antes_dos_efeitos(tmp_path: Path) -> None:
    """C-AUD-001: o summary é previsível, então a negação vem **antes** do primeiro efeito."""
    rig = PatchRig(tmp_path, tool_limits=limits(tool_result_content_bytes=10))
    result = rig.patch(create("abcdefgh.txt", "x"))
    denied(result, "limit.tool_result_content_bytes")
    assert not partial(result)
    assert not (rig.ws / "abcdefgh.txt").exists()
    assert rig.executor.usage().files_read == ()


def test_plan_nao_expoe_caminho_absoluto_no_repr(rig: PatchRig) -> None:
    rig.put("a.txt", "a\n")
    planos: list[Any] = []
    real = po._plan

    def guarda(context: Any, target: Any) -> Any:
        plano = real(context, target)
        planos.append(plano)
        return plano

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(po, "_plan", guarda)
        ok(rig.patch(swap_line("a.txt", 1, "a", "A")))
    (plano,) = planos
    assert repr(plano) == "<PatchFilePlan update 'a.txt'>"
    assert str(rig.ws) not in repr(plano)
    assert plano.operation is PatchOperation.UPDATE
    assert plano.baseline_sha256 == hashlib.sha256(b"a\n").digest()


def test_git_ops_fora_de_repositorio_sao_erro_tecnico(rig: PatchRig) -> None:
    errored(rig.executor.execute(GitStatus()), "git_failed")


# ============================================================================= 15. cancelamento


@pytest.mark.parametrize("after", [0, 1, 2, 3, 4])
def test_cancelamento_antes_do_primeiro_efeito_nao_muda_nada(tmp_path: Path, after: int) -> None:
    token = CountdownToken(after)
    rig = PatchRig(tmp_path, token=token)
    rig.put("a.txt", "a\n")
    rig.put("b.txt", "b\n")
    before = rig.snapshot()
    result = rig.patch(swap_line("a.txt", 1, "a", "A") + swap_line("b.txt", 1, "b", "B"))
    errored(result, "operation_cancelled")
    assert not partial(result)
    assert rig.snapshot() == before


def test_varredura_de_cancelamento_nunca_esconde_efeito(tmp_path: Path) -> None:
    """Cancela em **cada** consulta possível ao token. Invariante: sem a nota de aplicação
    parcial, o workspace está byte a byte como antes; com `OK`, o patch inteiro foi aplicado."""
    text = create("a/novo.txt", "n") + swap_line("b.txt", 1, "b", "B") + delete("c.txt", "c")
    vistos = {"limpo": 0, "parcial": 0, "ok": 0}
    for after in range(200):
        rig = PatchRig(tmp_path / str(after), token=CountdownToken(after))
        rig.put("b.txt", "b\n")
        rig.put("c.txt", "c\n")
        before = rig.snapshot()
        result = rig.patch(text)
        if result.status is ToolStatus.OK:
            assert rig.read("a/novo.txt") == b"n\n" and rig.read("b.txt") == b"B\n"
            assert not (rig.ws / "c.txt").exists()
            vistos["ok"] += 1
            break
        errored(result, "operation_cancelled")
        if partial(result):
            vistos["parcial"] += 1
        else:
            assert rig.snapshot() == before, after
            vistos["limpo"] += 1
    assert vistos["ok"] == 1 and vistos["limpo"] > 5 and vistos["parcial"] > 3, vistos


def test_cancelamento_entre_hunks_do_parse(tmp_path: Path) -> None:
    class Flip:
        def __init__(self) -> None:
            self.calls = 0

        def is_cancelled(self) -> bool:
            self.calls += 1
            return self.calls > 3

    rig = PatchRig(tmp_path, token=Flip())
    rig.put("h.txt", "".join(f"l{k}\n" for k in range(1, 20)))
    result = rig.patch(update("h.txt", _odd_line_hunks(9)))
    errored(result, "operation_cancelled")
    assert rig.read("h.txt").startswith(b"l1\n")


@pytest.mark.skipif(sys.platform != "win32", reason="semântica de caixa do NTFS")
def test_windows_create_com_caixa_diferente_de_existente_e_already_exists(rig: PatchRig) -> None:
    rig.put("Readme.md", "x\n")
    errored(rig.patch(create("README.md", "y")), "already_exists")
    assert rig.read("Readme.md") == b"x\n"
