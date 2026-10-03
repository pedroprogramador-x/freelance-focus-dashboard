"""E7.5-A — fundação do `ToolExecutor` concreto: contratos, binding, journal, facade, limites.

Nenhuma das nove operações é executada nesta etapa (os corpos são da E7.5-B em diante): aqui se
prova o **pipeline** que todas vão atravessar. O executor **não** aceita handlers por parâmetro;
os testes que precisam observar contagem, `files_read`, journal e a conversão para `ToolResult`
substituem, via `monkeypatch`, os **métodos privados** da classe (`_read_file`…), exatamente o
que a E7.5-B vai implementar.

Só fixtures locais benignas: diretórios temporários e, onde o teste precisa de uma "raiz
trocada", uma junction NTFS (ou symlink em POSIX) para **outro diretório temporário**.
"""

from __future__ import annotations

import ast
import dataclasses
import os
import re
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app import agent_runtime
from app.agent_runtime import dto
from app.db.enums import SafetyEventKind
from app.path_runtime import (
    IntegrityFailure,
    PathAccessDenied,
    PathFailure,
    PathIntegrityViolation,
    PathOperationFailed,
    bind_root,
)
from app.safety import (
    TOOL_LIMITS_V1,
    CapabilityProfileRejected,
    EnforcementMode,
    ObjectIdentity,
    SafetyDecision,
    SafetyPolicy,
    ToolLimits,
    policy_hash,
)
from app.safety.capability_profile import DEVELOPER_V1_PROFILE, ProviderCapabilityProfile
from app.tool_executor import contracts
from app.tool_executor import executor as executor_module
from app.tool_executor.contracts import (
    TOOL_OPERATIONS,
    ApplyPatch,
    CancelToken,
    DecisionCategory,
    DecisionJournal,
    ExecutionWorkspaceRef,
    GitDiff,
    GitListTree,
    GitShow,
    GitStatus,
    ListDirectory,
    MediatedTools,
    ReadFile,
    RunScope,
    SearchText,
    ToolDecisionRecord,
    ToolExecutor,
    ToolExecutorFactory,
    ToolRequest,
    ToolResult,
    ToolStatus,
    WorkspaceKind,
    WriteFile,
)
from app.tool_executor.executor import (
    HandlerContext,
    LocalWorktreeToolExecutor,
    Outcome,
)
from app.tool_executor.facade import MediatedToolsFacade
from app.tool_executor.factory import LocalWorktreeToolExecutorFactory
from app.tool_executor.journal import InMemoryDecisionJournal
from app.tool_executor.reasons import (
    DENIAL_PHRASES,
    INTEGRITY_RULES,
    UnknownRule,
    category_of,
    phrase_for,
    reason_for,
    rule_for_integrity,
    status_for_rule,
)
from app.tool_executor.validation import ContractViolation
from app.tool_executor.workspace import (
    InMemoryWorkspaceResolver,
    ResolvedWorkspace,
    ToolExecutorError,
    UnsupportedWorkspace,
    WorkspaceResolver,
    WorkspaceUnavailable,
)

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
COMMIT = "d" * 40
OTHER_COMMIT = "e" * 40

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


# ------------------------------------------------------------------------------- apoio


class Token:
    def __init__(self) -> None:
        self.cancelled = False

    def is_cancelled(self) -> bool:
        return self.cancelled


class RaisingToken:
    def is_cancelled(self) -> bool:
        raise RuntimeError("falhou")


class NonBoolToken:
    def is_cancelled(self) -> bool:
        return "talvez"  # type: ignore[return-value]


class SpyResolver:
    """Delega ao resolvedor real e registra as chamadas."""

    def __init__(self, inner: WorkspaceResolver) -> None:
        self.inner = inner
        self.calls: list[tuple[ExecutionWorkspaceRef, RunScope]] = []

    def resolve(
        self, workspace_ref: ExecutionWorkspaceRef, run_scope: RunScope
    ) -> ResolvedWorkspace | None:
        self.calls.append((workspace_ref, run_scope))
        return self.inner.resolve(workspace_ref, run_scope)


class FailingJournal:
    def __init__(self) -> None:
        self.calls = 0

    def append(self, record: ToolDecisionRecord) -> None:
        self.calls += 1
        raise OSError("trilha indisponível: C:\\caminho\\absoluto\\secreto")


def identity_of(path: Path) -> ObjectIdentity:
    info = os.stat(path)
    return ObjectIdentity(info.st_dev, info.st_ino)


def ref(workspace_id: str = "wt-1", base: str = COMMIT) -> ExecutionWorkspaceRef:
    return ExecutionWorkspaceRef(WorkspaceKind.LOCAL_WORKTREE, workspace_id, base)


def scope(run: str = "r1", task: str = "t1") -> RunScope:
    return RunScope(task, run, "i1")


def resolved_for(
    path: Path,
    *,
    workspace_id: str = "wt-1",
    task: str = "t1",
    run: str = "r1",
    base: str = COMMIT,
    identity: ObjectIdentity | None = None,
) -> ResolvedWorkspace:
    return ResolvedWorkspace(
        workspace_id=workspace_id,
        task_id=task,
        run_id=run,
        base_commit=base,
        workspace_path=str(path),
        root_identity=identity if identity is not None else identity_of(path),
    )


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    directory = tmp_path / "ws"
    directory.mkdir()
    return directory


@pytest.fixture
def resolver(ws: Path) -> InMemoryWorkspaceResolver:
    instance = InMemoryWorkspaceResolver()
    instance.bind(resolved_for(ws))
    return instance


_MP: list[pytest.MonkeyPatch] = []


@pytest.fixture(autouse=True)
def _monkeypatch_para_os_metodos_privados(monkeypatch: pytest.MonkeyPatch) -> Any:
    _MP.append(monkeypatch)
    yield
    _MP.pop()


def install_ops(handlers: dict[type, Any] | None) -> None:
    """Troca, **nesta classe e só neste teste**, o método privado de cada operação por um fake.

    Não existe porta pública para isto: é `monkeypatch` sobre `LocalWorktreeToolExecutor`.
    """
    for operation, fake in (handlers or {}).items():
        name = executor_module._DISPATCH[operation]
        _MP[-1].setattr(
            LocalWorktreeToolExecutor,
            name,
            lambda self, context, request, _fake=fake: _fake(context, request),
        )


class Env:
    """Uma factory + ferramentas para criar executores observáveis."""

    def __init__(
        self,
        resolver: WorkspaceResolver,
        handlers: dict[type, Any] | None = None,
    ) -> None:
        self.resolver = resolver
        self.token = Token()
        self.journal = InMemoryDecisionJournal()
        install_ops(handlers)
        self.factory = LocalWorktreeToolExecutorFactory(resolver)

    def create(self, **changes: Any) -> ToolExecutor:
        args: dict[str, Any] = {
            "workspace_ref": ref(),
            "composed_policy": SafetyPolicy(),
            "effective_capability_profile": DEVELOPER_V1_PROFILE,
            "run_scope": scope(),
            "cancel_token": self.token,
            "decision_journal": self.journal,
        }
        args.update(changes)
        return self.factory.create(**args)


@pytest.fixture
def env(resolver: InMemoryWorkspaceResolver) -> Env:
    return Env(resolver)


def direct_executor(
    ws: Path,
    *,
    profile: ProviderCapabilityProfile = DEVELOPER_V1_PROFILE,
    handlers: dict[type, Any] | None = None,
    token: Any = None,
    journal: Any = None,
) -> tuple[LocalWorktreeToolExecutor, InMemoryDecisionJournal]:
    """Instancia a classe concreta **sem** a factory — o único jeito de testar o gate de
    capability com um perfil que o `require_v1` da factory nunca deixaria passar."""
    sink = journal if journal is not None else InMemoryDecisionJournal()
    install_ops(handlers)
    executor = LocalWorktreeToolExecutor(
        workspace=resolved_for(ws),
        root=bind_root(ws),
        policy=SafetyPolicy(),
        profile=profile,
        run_scope=scope(),
        cancel_token=token if token is not None else Token(),
        journal=sink,
    )
    return executor, sink


def amostras() -> list[ToolRequest]:
    return [
        ReadFile("a.txt"),
        ListDirectory(None),
        SearchText("x"),
        WriteFile("a.txt", "x"),
        ApplyPatch("--- a"),
        GitStatus(),
        GitDiff(),
        GitShow("HEAD"),
        GitListTree(),
    ]


def git_fora_de_repositorio() -> list[ToolRequest]:
    """As quatro operações de Git (E7.5-D). O `ws` destes testes **não** é repositório: elas
    rodam de verdade e devolvem `ERROR git_failed` — técnico, sem journal. Nenhuma operação
    devolve mais `operation_not_implemented`."""
    return [GitStatus(), GitDiff(), GitShow("HEAD"), GitListTree()]


def ok_handler(
    *files: str, content: str = "ok"
) -> Callable[[HandlerContext, ToolRequest], Outcome]:
    def handler(context: HandlerContext, request: ToolRequest) -> Outcome:
        return Outcome(ToolStatus.OK, content=content, files_read=tuple(files))

    return handler


# =========================================================== 1. contratos E7.2 ajustados


def test_continuam_exatamente_nove_operacoes() -> None:
    assert {t.__name__ for t in TOOL_OPERATIONS} == {
        "ReadFile",
        "ListDirectory",
        "SearchText",
        "WriteFile",
        "ApplyPatch",
        "GitStatus",
        "GitDiff",
        "GitShow",
        "GitListTree",
    }
    assert len(TOOL_OPERATIONS) == 9


def test_nenhum_exec_command_nem_shell_command_existe_no_projeto() -> None:
    proibidos = {"execcommand", "shellcommand"}
    for path in APP_ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            nome = None
            if isinstance(node, ast.ClassDef | ast.FunctionDef):
                nome = node.name
            elif isinstance(node, ast.Name):
                nome = node.id
            assert nome is None or nome.lower() not in proibidos, f"{path.name}: {nome}"


def test_cancel_token_e_definido_em_tool_executor_e_reexportado() -> None:
    assert dto.CancelToken is contracts.CancelToken
    assert agent_runtime.CancelToken is contracts.CancelToken
    assert CancelToken.__module__ == "app.tool_executor.contracts"


def test_list_directory_none_e_a_raiz_e_o_padrao() -> None:
    assert ListDirectory().path is None
    assert ListDirectory(None).path is None
    assert SearchText("x").path is None
    assert ListDirectory("src").path == "src"


@pytest.mark.parametrize("raw", ["", " ", "\x00"])
def test_list_directory_continua_recusando_texto_invalido(raw: str) -> None:
    with pytest.raises(ContractViolation):
        ListDirectory(raw)


@pytest.mark.parametrize("raw", ["", ".", "./", "/", "\\"])
def test_nenhuma_string_vira_atalho_de_raiz_no_caminho_mediado(raw: str) -> None:
    from app.safety import prevalidate_mediated_path

    assert not prevalidate_mediated_path(raw).allow


def test_tool_executor_tem_close_e_a_factory_recebe_token_e_journal() -> None:
    import inspect

    assert "close" in vars(ToolExecutor)
    params = tuple(inspect.signature(ToolExecutorFactory.create).parameters)
    assert params == (
        "self",
        "workspace_ref",
        "composed_policy",
        "effective_capability_profile",
        "run_scope",
        "cancel_token",
        "decision_journal",
    )


def test_contratos_de_ferramenta_continuam_sendo_so_tipos() -> None:
    """`contracts.py` não ganhou IO nem dependência de execução."""
    imports = {
        node.module
        for node in ast.walk(ast.parse((APP_ROOT / "tool_executor" / "contracts.py").read_text()))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert all(
        m.startswith(("app.safety", "app.tool_executor"))
        or m in {"__future__", "dataclasses", "enum", "re", "types", "typing"}
        for m in imports
    ), imports


# ============================================================================= 2. limites


def test_limites_v1_tem_exatamente_os_valores_aprovados() -> None:
    kib, mib = 1024, 1024 * 1024
    limits = TOOL_LIMITS_V1
    assert isinstance(limits, ToolLimits)
    assert limits.read_file_bytes == 256 * kib
    assert limits.git_show_path_bytes == 256 * kib
    assert limits.write_file_bytes == 1 * mib
    assert limits.apply_patch_input_bytes == 512 * kib
    assert limits.apply_patch_max_files == 50
    assert limits.apply_patch_max_hunks == 500
    assert limits.apply_patch_result_file_bytes == 1 * mib
    assert limits.list_directory_entries == 1000
    assert limits.search_query_chars == 256
    assert limits.search_max_matches == 200
    assert limits.search_snippet_chars == 512
    assert limits.search_file_bytes == 1 * mib
    assert limits.search_max_files == 5000
    assert limits.search_deadline_s == 20
    assert limits.git_result_bytes == 256 * kib
    assert limits.tool_result_content_bytes == 256 * kib
    assert limits.git_internal_capture_bytes == 4 * mib


def test_limites_sao_imutaveis_e_a_captura_interna_nao_e_saida_do_provider() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        TOOL_LIMITS_V1.read_file_bytes = 1  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        TOOL_LIMITS_V1.extra = 1  # type: ignore[attr-defined]
    assert TOOL_LIMITS_V1.git_internal_capture_bytes > TOOL_LIMITS_V1.git_result_bytes
    assert all(
        isinstance(getattr(TOOL_LIMITS_V1, f.name), int) for f in dataclasses.fields(ToolLimits)
    )


def test_limites_nao_tocam_a_safety_policy_nem_o_fingerprint() -> None:
    campos = {f.name for f in dataclasses.fields(SafetyPolicy)}
    nomes = {f.name for f in dataclasses.fields(ToolLimits)}
    assert not campos & nomes
    assert not (set(SafetyPolicy().as_canonical()) & nomes)
    # Vetor capturado no `main` base (ac0fdc0): a política padrão hasheia igual antes e depois.
    assert policy_hash(SafetyPolicy()) == (
        "c299da602155d610a7d574f7b04af81afb975231fcf78f85644bf3dc9bc7971d"
    )


# =============================================================================== 3. factory


def test_factory_cria_executor_utilizavel_para_o_run(env: Env) -> None:
    executor = env.create()
    assert isinstance(executor, LocalWorktreeToolExecutor)
    assert executor.usage().operations == 0


def test_factory_exige_require_v1_antes_de_criar_e_antes_de_consultar_o_resolvedor(
    ws: Path, resolver: InMemoryWorkspaceResolver
) -> None:
    spy = SpyResolver(resolver)
    env = Env(spy)
    for profile in (
        dataclasses.replace(DEVELOPER_V1_PROFILE, read_files=EnforcementMode.DISABLED),
        dataclasses.replace(DEVELOPER_V1_PROFILE, execute_commands=EnforcementMode.MEDIATED),
        dataclasses.replace(DEVELOPER_V1_PROFILE, git_write=EnforcementMode.FIXED_OPERATIONS_ONLY),
        dataclasses.replace(DEVELOPER_V1_PROFILE, network=EnforcementMode.UNMEDIATED),
    ):
        with pytest.raises(CapabilityProfileRejected):
            env.create(effective_capability_profile=profile)
    assert spy.calls == []


def test_factory_recusa_workspace_remote_sem_consultar_o_resolvedor(
    resolver: InMemoryWorkspaceResolver,
) -> None:
    spy = SpyResolver(resolver)
    env = Env(spy)
    remote = ExecutionWorkspaceRef(WorkspaceKind.REMOTE, "wt-1", COMMIT)
    with pytest.raises(UnsupportedWorkspace) as caught:
        env.create(workspace_ref=remote)
    assert str(caught.value) == "workspace_kind_unsupported"
    assert spy.calls == []


def test_resolvedor_recebe_workspace_ref_e_run_scope(
    ws: Path, resolver: InMemoryWorkspaceResolver
) -> None:
    spy = SpyResolver(resolver)
    env = Env(spy)
    run_scope, workspace_ref = scope(), ref()
    env.create(run_scope=run_scope, workspace_ref=workspace_ref)
    assert spy.calls == [(workspace_ref, run_scope)]


def test_o_mesmo_workspace_id_de_outro_run_nao_resolve(
    ws: Path, resolver: InMemoryWorkspaceResolver
) -> None:
    env = Env(resolver)
    for outro in (scope(run="r2"), scope(task="t2")):
        with pytest.raises(WorkspaceUnavailable) as caught:
            env.create(run_scope=outro)
        assert caught.value.code == "workspace_unbound"


def test_resolvedor_defeituoso_nao_consegue_entregar_binding_de_outro_run(
    ws: Path,
) -> None:
    class Buggy:
        def __init__(self, resolved: ResolvedWorkspace | None) -> None:
            self.resolved = resolved

        def resolve(
            self, workspace_ref: ExecutionWorkspaceRef, run_scope: RunScope
        ) -> ResolvedWorkspace | None:
            return self.resolved

    for resolved, code in (
        (resolved_for(ws, run="OUTRO"), "binding_mismatch"),
        (resolved_for(ws, task="OUTRA"), "binding_mismatch"),
        (resolved_for(ws, workspace_id="outra-ws"), "binding_mismatch"),
        (resolved_for(ws, base=OTHER_COMMIT), "base_commit_mismatch"),
        (None, "workspace_unbound"),
        ("nao-e-resolved", "resolver_invalid"),
    ):
        env = Env(Buggy(resolved))  # type: ignore[arg-type]
        with pytest.raises(WorkspaceUnavailable) as caught:
            env.create()
        assert caught.value.code == code
        assert str(caught.value) == code


def test_base_commit_da_ref_precisa_coincidir_com_o_binding(
    ws: Path, resolver: InMemoryWorkspaceResolver
) -> None:
    env = Env(resolver)
    with pytest.raises(WorkspaceUnavailable) as caught:
        env.create(workspace_ref=ref(base=OTHER_COMMIT))
    # A chave do binding é (id, task, run); o commit da ref é conferido contra o do binding.
    assert caught.value.code == "base_commit_mismatch"

    wrong = InMemoryWorkspaceResolver()
    wrong.bind(resolved_for(ws, base=OTHER_COMMIT))
    with pytest.raises(WorkspaceUnavailable) as mismatch:
        Env(wrong).create()
    assert mismatch.value.code == "base_commit_mismatch"


def test_identidade_da_raiz_diferente_da_registrada_e_recusada(tmp_path: Path, ws: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    bad = InMemoryWorkspaceResolver()
    bad.bind(resolved_for(ws, identity=identity_of(other)))
    with pytest.raises(WorkspaceUnavailable) as caught:
        Env(bad).create()
    assert caught.value.code == "root_unavailable"
    assert caught.value.rule_id == "path.root_changed"
    assert str(tmp_path) not in str(caught.value)


def test_raiz_ausente_ou_trocada_por_link_e_recusada(tmp_path: Path, ws: Path) -> None:
    inner = InMemoryWorkspaceResolver()
    inner.bind(resolved_for(ws))
    outside = tmp_path / "outside"
    outside.mkdir()
    ws.rename(tmp_path / "ws_old")
    with pytest.raises(WorkspaceUnavailable) as missing:
        Env(inner).create()
    assert missing.value.code == "root_unavailable"

    if make_dir_link(ws, outside):
        with pytest.raises(WorkspaceUnavailable) as linked:
            Env(inner).create()
        assert linked.value.code == "root_unavailable"
        assert linked.value.rule_id in {"path.reparse_detected", "path.root_changed"}


def test_factory_valida_os_argumentos_estruturais(env: Env) -> None:
    with pytest.raises(ContractViolation):
        env.create(cancel_token=object())
    with pytest.raises(ContractViolation):
        env.create(decision_journal=object())
    with pytest.raises(ContractViolation):
        env.create(workspace_ref="wt-1")
    with pytest.raises(ContractViolation):
        env.create(run_scope=("t1", "r1", "i1"))
    with pytest.raises(ContractViolation):
        env.create(composed_policy={})
    with pytest.raises(ContractViolation):
        LocalWorktreeToolExecutorFactory(object())  # type: ignore[arg-type]


def test_erros_de_criacao_nunca_carregam_caminho_absoluto(tmp_path: Path, ws: Path) -> None:
    inner = InMemoryWorkspaceResolver()
    inner.bind(resolved_for(ws))
    ws.rmdir()
    with pytest.raises(ToolExecutorError) as caught:
        Env(inner).create()
    assert str(tmp_path) not in str(caught.value) + repr(caught.value)


# ============================================================================ 4. resolvedor


def test_resolved_workspace_valida_e_nao_vaza_o_caminho_no_repr(ws: Path) -> None:
    resolved = resolved_for(ws)
    assert str(ws) not in repr(resolved)
    for changes in (
        {"workspace_path": "relativo/x"},
        {"workspace_path": "C:x"},
        {"base_commit": "abc"},
        {"base_commit": "G" * 40},
        {"workspace_prefix": "ws"},
        {"workspace_prefix": "ws\\"},
        {"workspace_prefix": "a/../b/"},
        {"workspace_prefix": ".git/"},
        {"workspace_id": ""},
        {"root_identity": (1, 2)},
    ):
        base: dict[str, Any] = {
            "workspace_id": "w",
            "task_id": "t",
            "run_id": "r",
            "base_commit": COMMIT,
            "workspace_path": str(ws),
            "root_identity": ObjectIdentity(1, 2),
        }
        base.update(changes)
        with pytest.raises(ContractViolation):
            ResolvedWorkspace(**base)
    assert (
        ResolvedWorkspace(
            workspace_id="w",
            task_id="t",
            run_id="r",
            base_commit=COMMIT,
            workspace_path=str(ws),
            root_identity=ObjectIdentity(1, 2),
            workspace_prefix="ws/sub/",
        ).workspace_prefix
        == "ws/sub/"
    )


def test_resolvedor_em_memoria_e_explicito_e_por_instancia(ws: Path) -> None:
    a, b = InMemoryWorkspaceResolver(), InMemoryWorkspaceResolver()
    resolved = resolved_for(ws)
    a.bind(resolved)
    assert a.resolve(ref(), scope()) is resolved
    assert b.resolve(ref(), scope()) is None  # nada de registry global implícito
    with pytest.raises(ValueError):
        a.bind(resolved)
    a.unbind("wt-1", "t1", "r1")
    assert a.resolve(ref(), scope()) is None
    with pytest.raises(ContractViolation):
        a.bind("x")  # type: ignore[arg-type]


def test_tool_executor_nao_consulta_banco_nem_provider() -> None:
    for path in (APP_ROOT / "tool_executor").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(("app.db", "sqlalchemy", "app.orchestrator"))
            if isinstance(node, ast.Import):
                assert not any(a.name.startswith(("app.db", "sqlalchemy")) for a in node.names)
            if isinstance(node, ast.Attribute):
                assert node.attr != "worktree_path", f"{path.name} usa Run.worktree_path"


# =========================================================================== 5. executor A/B


def test_executores_a_e_b_nao_compartilham_nenhum_estado(
    tmp_path: Path, ws: Path, resolver: InMemoryWorkspaceResolver
) -> None:
    ws_b = tmp_path / "ws_b"
    ws_b.mkdir()
    resolver.bind(resolved_for(ws_b, workspace_id="wt-2", run="r2"))
    journal_a, journal_b = InMemoryDecisionJournal(), InMemoryDecisionJournal()
    install_ops({ReadFile: ok_handler("a.py")})
    factory = LocalWorktreeToolExecutorFactory(resolver)
    token = Token()
    a = factory.create(ref(), SafetyPolicy(), DEVELOPER_V1_PROFILE, scope(), token, journal_a)
    b = factory.create(
        ref("wt-2"), SafetyPolicy(), DEVELOPER_V1_PROFILE, scope(run="r2"), token, journal_b
    )
    a.execute(ReadFile("a.py"))
    a.execute(object())  # type: ignore[arg-type]  # negação em A
    assert a.usage().operations == 2 and a.usage().denials == 1
    assert a.usage().files_read == ("a.py",)
    assert b.usage() == contracts.MediatedUsage(files_read=(), operations=0, denials=0)
    assert len(journal_a) == 1 and len(journal_b) == 0
    b.close()
    assert a.execute(ReadFile("a.py")).status is ToolStatus.OK  # fechar B não afeta A
    assert a.mediated_tools() is not b.mediated_tools()


# ================================================================== 6. dispatch estrutural


def test_tipo_fora_de_tool_operations_e_negado_antes_de_qualquer_io(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def proibido(*_a: object, **_k: object) -> None:
        raise AssertionError("IO antes do gate estrutural")

    monkeypatch.setattr(executor_module, "verify_root", proibido)
    executor, journal = direct_executor(ws)
    result = executor.execute("rm -rf /")  # type: ignore[arg-type]
    assert result.status is ToolStatus.DENIED
    assert result.operation == "str"
    assert result.reason is not None and result.reason.startswith("tool.unknown_operation")
    usage = executor.usage()
    assert (usage.operations, usage.denials) == (1, 1)
    (record,) = journal.records()
    assert record.rule_id == "tool.unknown_operation"
    assert record.category is DecisionCategory.CAPABILITY_DENIED
    assert record.sequence == 1 and record.run_scope == scope()


def test_nome_de_operacao_nao_confiavel_vira_unknown(ws: Path) -> None:
    for name in ("a b", "x" * 200, "ação"):
        hostile = type(name, (), {})()
        executor, journal = direct_executor(ws)
        result = executor.execute(hostile)
        assert result.operation == "<unknown>" and result.status is ToolStatus.DENIED
        assert journal.records()[0].operation == "<unknown>"


@pytest.mark.parametrize("request_", amostras(), ids=lambda r: type(r).__name__)
def test_capability_gate_roda_antes_de_qualquer_io(
    request_: ToolRequest, ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = contracts.required_capability(request_)
    profile = dataclasses.replace(
        DEVELOPER_V1_PROFILE, **{capability.value: EnforcementMode.DISABLED}
    )

    def proibido(*_a: object, **_k: object) -> None:
        raise AssertionError("IO antes do capability gate")

    monkeypatch.setattr(executor_module, "verify_root", proibido)
    handlers = {type(request_): proibido}
    executor, journal = direct_executor(ws, profile=profile, handlers=handlers)
    result = executor.execute(request_)
    assert result.status is ToolStatus.DENIED
    assert result.reason is not None and result.reason.startswith("capability.denied")
    (record,) = journal.records()
    assert record.category is DecisionCategory.CAPABILITY_DENIED
    assert (executor.usage().operations, executor.usage().denials) == (1, 1)


@pytest.mark.parametrize(
    ("capability", "mode"),
    [
        ("read_files", EnforcementMode.UNMEDIATED),
        ("write_files", EnforcementMode.FIXED_OPERATIONS_ONLY),
        ("git_read", EnforcementMode.MEDIATED),
        ("git_read", EnforcementMode.UNMEDIATED),
    ],
)
def test_capability_gate_exige_o_modo_exato(
    capability: str, mode: EnforcementMode, ws: Path
) -> None:
    profile = dataclasses.replace(DEVELOPER_V1_PROFILE, **{capability: mode})
    executor, _ = direct_executor(ws, profile=profile)
    by_cap = {contracts.required_capability(r).value: r for r in amostras()}
    assert executor.execute(by_cap[capability]).status is ToolStatus.DENIED


def test_gate_de_uma_capability_nao_nega_as_outras(ws: Path) -> None:
    profile = dataclasses.replace(DEVELOPER_V1_PROFILE, write_files=EnforcementMode.DISABLED)
    executor, _ = direct_executor(ws, profile=profile)
    assert executor.execute(WriteFile("a", "x")).status is ToolStatus.DENIED
    # `ReadFile` agora é real: um arquivo ausente é `ERROR not_found`, **não** negação
    assert executor.execute(ReadFile("a")).reason.startswith("not_found")  # type: ignore[union-attr]
    assert executor.execute(GitStatus()).reason.startswith("git_failed")  # type: ignore[union-attr]


@pytest.mark.parametrize("request_", git_fora_de_repositorio(), ids=lambda r: type(r).__name__)
def test_operacao_git_fora_de_repositorio_e_erro_fixo_sem_fallback(
    request_: ToolRequest, env: Env
) -> None:
    executor = env.create()
    result = executor.execute(request_)
    assert result.status is ToolStatus.ERROR
    assert result.reason is not None and result.reason.startswith("git_failed")
    assert result.content is None
    usage = executor.usage()
    assert (usage.operations, usage.denials, usage.files_read) == (1, 0, ())
    assert len(env.journal) == 0  # ERROR não gera record


def test_o_gate_de_capability_nao_depende_de_handler(ws: Path) -> None:
    executor, _ = direct_executor(ws, handlers={ReadFile: ok_handler("a.py")})
    assert executor.execute(ReadFile("a.py")).status is ToolStatus.OK
    assert executor.execute(GitStatus()).reason.startswith("git_failed")  # type: ignore[union-attr]


# ==================================================================== 7. usage e contagem


def test_usage_conta_operacoes_negacoes_e_deduplica_files_read(ws: Path) -> None:
    handlers = {
        ReadFile: lambda c, r: Outcome(
            ToolStatus.OK, content="x", files_read=("b.py", "a.py", "b.py")
        ),
        WriteFile: lambda c, r: Outcome(
            ToolStatus.DENIED, rule_id="path.secret_denied", subject=".env", files_read=("z.py",)
        ),
        SearchText: lambda c, r: Outcome(
            ToolStatus.ERROR, rule_id="io_error", files_read=("y.py",)
        ),
    }
    executor, journal = direct_executor(ws, handlers=handlers)
    executor.execute(ReadFile("a"))
    executor.execute(ReadFile("a"))
    executor.execute(WriteFile(".env", "x"))
    executor.execute(SearchText("q"))
    usage = executor.usage()
    assert usage.files_read == ("a.py", "b.py")  # ordenado, sem duplicata; DENIED/ERROR não somam
    assert (usage.operations, usage.denials) == (4, 1)  # ERROR não conta como negação
    (record,) = journal.records()
    assert record.category is DecisionCategory.SECRET_ACCESS_BLOCKED
    assert record.subject_redacted == ".env"


def test_usage_e_snapshot_imutavel(ws: Path) -> None:
    executor, _ = direct_executor(ws, handlers={ReadFile: ok_handler("a.py")})
    executor.execute(ReadFile("a"))
    snapshot = executor.usage()
    executor.execute(ReadFile("b"))
    assert snapshot.files_read == ("a.py",) and snapshot.operations == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.operations = 9  # type: ignore[misc]
    assert isinstance(snapshot.files_read, tuple)


def test_files_read_ordena_por_bytes_utf8(ws: Path) -> None:
    executor, _ = direct_executor(ws, handlers={ReadFile: ok_handler("b/z", "B/a", "a/é", "a/e")})
    executor.execute(ReadFile("x"))
    assert executor.usage().files_read == tuple(
        sorted({"b/z", "B/a", "a/é", "a/e"}, key=lambda s: s.encode("utf-8"))
    )


def test_usage_continua_valido_depois_de_close(ws: Path) -> None:
    executor, _ = direct_executor(ws, handlers={ReadFile: ok_handler("a.py")})
    executor.execute(ReadFile("a"))
    executor.close()
    assert executor.usage().files_read == ("a.py",)


# ========================================================================== 8. close / estados


def test_close_encerra_o_executor_e_e_idempotente(env: Env) -> None:
    executor = env.create()
    executor.close()
    executor.close()
    result = executor.execute(ReadFile("a"))
    assert result.status is ToolStatus.ERROR
    assert result.reason is not None and result.reason.startswith("executor_closed")
    assert executor.usage().operations == 0  # chamada depois do close não é operação
    assert executor.mediated_tools().execute(ReadFile("a")).reason.startswith("executor_closed")  # type: ignore[union-attr]


def test_cancelamento_e_sticky_e_conta_como_operacao(env: Env) -> None:
    executor = env.create()
    env.token.cancelled = True
    first = executor.execute(ReadFile("a"))
    assert first.status is ToolStatus.ERROR
    assert first.reason is not None and first.reason.startswith("operation_cancelled")
    env.token.cancelled = False  # o token "volta atrás": o executor não
    assert executor.execute(ReadFile("a")).reason.startswith("operation_cancelled")  # type: ignore[union-attr]
    usage = executor.usage()
    assert (usage.operations, usage.denials) == (2, 0)
    assert len(env.journal) == 0  # cancelar não é negação de safety


def test_cancelamento_vence_ate_o_tipo_desconhecido(env: Env) -> None:
    executor = env.create()
    env.token.cancelled = True
    assert executor.execute(object()).reason.startswith("operation_cancelled")  # type: ignore[arg-type,union-attr]


@pytest.mark.parametrize("token", [RaisingToken(), NonBoolToken()], ids=["lanca", "nao-bool"])
def test_token_que_lanca_ou_nao_devolve_bool_conta_como_cancelado(ws: Path, token: Any) -> None:
    executor, _ = direct_executor(ws, token=token)
    result = executor.execute(ReadFile("a"))
    assert result.status is ToolStatus.ERROR
    assert result.reason is not None and result.reason.startswith("operation_cancelled")


def test_handler_recebe_is_cancelled_ja_adaptado_e_fail_closed(ws: Path) -> None:
    seen: list[bool] = []

    def handler(context: HandlerContext, request: ToolRequest) -> Outcome:
        seen.append(context.is_cancelled())
        return Outcome(ToolStatus.OK)

    token = Token()
    executor, _ = direct_executor(ws, handlers={ReadFile: handler}, token=token)
    executor.execute(ReadFile("a"))
    assert seen == [False]

    seen.clear()
    explosive, _ = direct_executor(ws, handlers={ReadFile: handler}, token=RaisingToken())
    explosive.execute(ReadFile("a"))  # cancelado antes do handler
    assert seen == []


# ====================================================================== 9. raiz revalidada


def test_raiz_removida_depois_do_create_e_erro_tecnico(env: Env, ws: Path) -> None:
    executor = env.create()
    ws.rmdir()
    result = executor.execute(ReadFile("a"))
    assert result.status is ToolStatus.ERROR
    assert result.reason is not None and result.reason.startswith("workspace_unavailable")
    assert len(env.journal) == 0


def test_raiz_trocada_por_link_e_negada_como_toctou(env: Env, ws: Path, tmp_path: Path) -> None:
    executor = env.create()
    outside = tmp_path / "outside"
    outside.mkdir()
    ws.rename(tmp_path / "ws_old")
    if not make_dir_link(ws, outside):
        pytest.skip("link indisponível neste ambiente")
    result = executor.execute(ReadFile("a"))
    assert result.status is ToolStatus.DENIED
    assert result.reason is not None and result.reason.startswith("path.reparse_detected")
    (record,) = env.journal.records()
    assert record.category is DecisionCategory.TOCTOU_RECHECK_FAILED
    assert str(tmp_path) not in record.reason and str(tmp_path) not in record.subject_redacted


# ========================================================== 10. handlers → ToolResult/journal


def test_handler_que_levanta_path_access_denied_vira_denied_com_a_categoria_certa(ws: Path) -> None:
    def secret(c: HandlerContext, r: ToolRequest) -> Outcome:
        raise PathAccessDenied(SafetyDecision(False, "path.secret_denied", "X", ".env"))

    def escapes(c: HandlerContext, r: ToolRequest) -> Outcome:
        raise PathAccessDenied(SafetyDecision(False, "path.escapes_root", "X", "../x"))

    executor, journal = direct_executor(ws, handlers={ReadFile: secret, WriteFile: escapes})
    assert executor.execute(ReadFile(".env")).status is ToolStatus.DENIED
    assert executor.execute(WriteFile("../x", "")).status is ToolStatus.DENIED
    assert [r.category for r in journal.records()] == [
        DecisionCategory.SECRET_ACCESS_BLOCKED,
        DecisionCategory.OUT_OF_WORKTREE_WRITE,  # a mesma regra, em escrita
    ]
    assert [r.sequence for r in journal.records()] == [1, 2]


def test_escapar_da_raiz_em_leitura_e_path_denied(ws: Path) -> None:
    def escapes(c: HandlerContext, r: ToolRequest) -> Outcome:
        raise PathAccessDenied(SafetyDecision(False, "path.escapes_root", "X", "../x"))

    executor, journal = direct_executor(ws, handlers={ReadFile: escapes})
    executor.execute(ReadFile("../x"))
    assert journal.records()[0].category is DecisionCategory.PATH_DENIED


def test_inspecao_tecnica_que_falhou_e_erro_e_nao_negacao(ws: Path) -> None:
    def broken(c: HandlerContext, r: ToolRequest) -> Outcome:
        raise PathAccessDenied(
            SafetyDecision(False, "path.inspection_failed", "C:\\abs\\segredo\\x", "a.txt")
        )

    executor, journal = direct_executor(ws, handlers={ReadFile: broken})
    result = executor.execute(ReadFile("a.txt"))
    assert result.status is ToolStatus.ERROR
    assert "abs" not in (result.reason or "") and "segredo" not in (result.reason or "")
    assert (executor.usage().denials, len(journal)) == (0, 0)


@pytest.mark.parametrize("category", list(PathFailure))
def test_falha_tecnica_de_path_vira_error_com_a_categoria(ws: Path, category: PathFailure) -> None:
    def failing(c: HandlerContext, r: ToolRequest) -> Outcome:
        raise PathOperationFailed(category, errno_code=2)

    executor, journal = direct_executor(ws, handlers={ReadFile: failing})
    result = executor.execute(ReadFile("a"))
    assert result.status is ToolStatus.ERROR and result.content is None
    expected = "operation_cancelled" if category is PathFailure.CANCELLED else category.value
    assert result.reason is not None and result.reason.startswith(expected)
    assert len(journal) == 0 and executor.usage().denials == 0


def test_cancelamento_vindo_de_dentro_do_handler_tambem_e_sticky(ws: Path) -> None:
    calls = {"n": 0}

    def cancelling(c: HandlerContext, r: ToolRequest) -> Outcome:
        calls["n"] += 1
        raise PathOperationFailed(PathFailure.CANCELLED)

    executor, _ = direct_executor(ws, handlers={ReadFile: cancelling})
    assert executor.execute(ReadFile("a")).reason.startswith("operation_cancelled")  # type: ignore[union-attr]
    assert executor.execute(ReadFile("a")).reason.startswith("operation_cancelled")  # type: ignore[union-attr]
    assert calls["n"] == 1  # a segunda chamada nem chega ao handler


def test_excecao_inesperada_vira_erro_interno_sem_vazar_nada(ws: Path) -> None:
    def explodes(c: HandlerContext, r: ToolRequest) -> Outcome:
        raise RuntimeError("Traceback: C:\\Users\\pedro\\segredo\\x.py token=ghp_abcdefghijklmnop")

    executor, journal = direct_executor(ws, handlers={ReadFile: explodes})
    result = executor.execute(ReadFile("a"))
    assert result.status is ToolStatus.ERROR
    assert result.reason == "internal_error: falha interna do executor"
    assert len(journal) == 0


def test_regra_desconhecida_nunca_vira_texto_cru(ws: Path) -> None:
    def odd(c: HandlerContext, r: ToolRequest) -> Outcome:
        return Outcome(ToolStatus.DENIED, rule_id="regra.inventada")

    executor, journal = direct_executor(ws, handlers={ReadFile: odd})
    result = executor.execute(ReadFile("a"))
    assert result.status is ToolStatus.ERROR
    assert result.reason == "internal_error: falha interna do executor"
    assert len(journal) == 0 and executor.usage().denials == 0


def test_reason_e_sempre_a_frase_fixa_nunca_o_reason_cru_da_decisao(ws: Path) -> None:
    raw = "abertura falhou: [Errno 2] No such file: 'C:\\Users\\pedro\\worktrees\\ff-task-1\\x'"

    def leaky(c: HandlerContext, r: ToolRequest) -> Outcome:
        raise PathAccessDenied(SafetyDecision(False, "path.secret_denied", raw, "x"))

    executor, journal = direct_executor(ws, handlers={ReadFile: leaky})
    result = executor.execute(ReadFile("x"))
    assert result.reason == reason_for("path.secret_denied")
    assert "pedro" not in (result.reason or "") and "Errno" not in (result.reason or "")
    record = journal.records()[0]
    assert record.reason == result.reason
    assert "pedro" not in record.reason


def test_subject_do_record_passa_pelo_redator(ws: Path) -> None:
    secret_like = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    profile = dataclasses.replace(DEVELOPER_V1_PROFILE, read_files=EnforcementMode.DISABLED)
    executor, journal = direct_executor(ws, profile=profile)
    executor.execute(ReadFile(secret_like))
    assert secret_like not in journal.records()[0].subject_redacted


# ======================================================================= 11. fronteira de saída


def test_conteudo_ok_e_redigido_so_na_fronteira_e_o_handler_ve_o_dado_cru(ws: Path) -> None:
    secret_like = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    seen: list[str] = []

    def handler(c: HandlerContext, r: ToolRequest) -> Outcome:
        seen.append(secret_like)  # o handler trabalha com o dado cru (comparação/hash/patch)
        return Outcome(ToolStatus.OK, content=f"token = {secret_like}")

    executor, _ = direct_executor(ws, handlers={ReadFile: handler})
    result = executor.execute(ReadFile("a"))
    assert result.status is ToolStatus.OK and result.content is not None
    assert secret_like not in result.content
    assert seen == [secret_like]


def test_conteudo_acima_do_teto_final_e_denied_nunca_truncado(ws: Path) -> None:
    limit = TOOL_LIMITS_V1.tool_result_content_bytes
    handler = lambda c, r: Outcome(ToolStatus.OK, content="x" * (limit + 5000))  # noqa: E731
    executor, _ = direct_executor(ws, handlers={ReadFile: handler})
    result = executor.execute(ReadFile("a"))
    assert result.status is ToolStatus.DENIED
    assert result.reason is not None and result.reason.startswith("limit.tool_result_content_bytes")
    assert executor.usage().files_read == ()


def test_conteudo_dentro_do_teto_passa_intacto(ws: Path) -> None:
    executor, _ = direct_executor(ws, handlers={ReadFile: ok_handler(content="linha\n" * 100)})
    assert executor.execute(ReadFile("a")).content == "linha\n" * 100


def test_outcome_exige_rule_id_em_negacao_e_erro() -> None:
    with pytest.raises(ContractViolation):
        Outcome(ToolStatus.DENIED)
    with pytest.raises(ContractViolation):
        Outcome(ToolStatus.ERROR)
    assert Outcome(ToolStatus.OK).content == ""


# ============================================================================== 12. journal


def test_toda_negacao_gera_record_com_os_campos_pedidos(env: Env) -> None:
    executor = env.create()
    executor.execute(object())  # type: ignore[arg-type]
    (record,) = env.journal.records()
    assert record.sequence == 1
    assert record.operation == "object"
    assert record.category is DecisionCategory.CAPABILITY_DENIED
    assert record.rule_id == "tool.unknown_operation"
    assert record.subject_redacted == "object"
    assert record.reason == reason_for("tool.unknown_operation")
    assert record.run_scope == scope()


def test_journal_em_memoria_e_snapshot_ordenado_e_valida_o_tipo() -> None:
    journal = InMemoryDecisionJournal()
    record = ToolDecisionRecord(
        1, "ReadFile", DecisionCategory.PATH_DENIED, "path.empty", "x", "r", scope()
    )
    journal.append(record)
    snapshot = journal.records()
    journal.append(dataclasses.replace(record, sequence=2))
    assert snapshot == (record,) and len(journal) == 2
    with pytest.raises(ContractViolation):
        journal.append("não é record")  # type: ignore[arg-type]


def test_journal_e_thread_safe_no_append() -> None:
    journal = InMemoryDecisionJournal()
    record = ToolDecisionRecord(
        1, "ReadFile", DecisionCategory.PATH_DENIED, "path.empty", "x", "r", scope()
    )
    threads = [
        threading.Thread(target=lambda: [journal.append(record) for _ in range(200)])  # type: ignore[func-returns-value]
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(journal) == 1600


def test_record_valida_a_forma() -> None:
    base: dict[str, Any] = {
        "sequence": 1,
        "operation": "ReadFile",
        "category": DecisionCategory.PATH_DENIED,
        "rule_id": "path.empty",
        "subject_redacted": "x",
        "reason": "r",
        "run_scope": scope(),
    }
    ToolDecisionRecord(**base)
    for changes in (
        {"sequence": 0},
        {"sequence": True},
        {"operation": ""},
        {"category": "path_denied"},
        {"rule_id": " "},
        {"subject_redacted": ""},
        {"reason": ""},
        {"run_scope": ("t", "r", "i")},
    ):
        with pytest.raises(ContractViolation):
            ToolDecisionRecord(**{**base, **changes})


def test_categorias_do_journal_existem_em_safety_event_kind() -> None:
    persistidos = {kind.value for kind in SafetyEventKind}
    assert {c.value for c in DecisionCategory} <= persistidos


def test_toda_categoria_de_regra_conhecida_e_persistivel() -> None:
    persistidos = {kind.value for kind in SafetyEventKind}
    for rule in DENIAL_PHRASES:
        for write in (False, True):
            assert category_of(rule, write=write).value in persistidos


# ------------------------------------------------------------------ falha do journal


def test_falha_do_journal_retorna_error_e_envenena_o_executor(ws: Path) -> None:
    journal = FailingJournal()
    executor, _ = direct_executor(ws, handlers={ReadFile: ok_handler("a.py")}, journal=journal)

    first = executor.execute(object())  # type: ignore[arg-type]  # negação → append falha
    assert first.status is ToolStatus.ERROR  # NUNCA DENIED sem trilha
    assert first.reason is not None and first.reason.startswith("journal_unavailable")
    assert "absoluto" not in first.reason and "secreto" not in first.reason

    # sticky: até uma operação perfeitamente válida passa a devolver ERROR
    later = executor.execute(ReadFile("a.py"))
    assert later.status is ToolStatus.ERROR
    assert later.reason is not None and later.reason.startswith("journal_unavailable")
    assert journal.calls == 1  # o journal nem é consultado de novo

    usage = executor.usage()
    assert usage.denials == 0  # a negação nunca foi entregue como DENIED
    assert usage.files_read == ()  # e a leitura posterior nem rodou
    assert usage.operations == 1


def test_journal_que_falha_na_primeira_negacao_via_factory(
    resolver: InMemoryWorkspaceResolver,
) -> None:
    journal = FailingJournal()
    factory = LocalWorktreeToolExecutorFactory(resolver)
    executor = factory.create(
        ref(), SafetyPolicy(), DEVELOPER_V1_PROFILE, scope(), Token(), journal
    )
    assert executor.execute(object()).status is ToolStatus.ERROR  # type: ignore[arg-type]
    assert executor.mediated_tools().execute(ReadFile("a")).status is ToolStatus.ERROR


# ================================================================================ 13. facade


def test_mediated_tools_devolve_a_facade_dedicada_e_nunca_o_executor(env: Env) -> None:
    executor = env.create()
    tools = executor.mediated_tools()
    assert tools is not executor
    assert isinstance(tools, MediatedToolsFacade)
    assert not isinstance(tools, LocalWorktreeToolExecutor)
    assert executor.mediated_tools() is tools  # estável: um por executor


AUTORIDADE = ("usage", "close", "mediated_tools", "journal", "workspace", "profile", "root")


def test_facade_nao_expoe_metodo_de_autoridade_como_api_publica(env: Env) -> None:
    tools = env.create().mediated_tools()
    publicos = {n for n in dir(tools) if not n.startswith("_")}
    assert publicos == {"execute"}
    for nome in AUTORIDADE:
        assert not hasattr(tools, nome), nome
    # Python tem dunders e atributos privados; a facade **não** se diz sandbox. Mas ela não
    # carrega estado público nem `__dict__` para pendurar autoridade.
    assert not hasattr(tools, "__dict__")


def test_a_classe_da_facade_so_define_execute_como_metodo_publico() -> None:
    publicos = {
        n for n, v in vars(MediatedToolsFacade).items() if callable(v) and not n.startswith("_")
    }
    assert publicos == {"execute"}


def test_facade_executa_pelo_mesmo_pipeline_e_nao_vaza_no_repr(env: Env, ws: Path) -> None:
    executor = env.create()
    tools: MediatedTools = executor.mediated_tools()
    assert tools.execute(GitStatus()).reason.startswith("git_failed")  # type: ignore[union-attr]
    assert executor.usage().operations == 1
    assert str(ws) not in repr(tools) and str(ws) not in repr(executor)
    assert repr(tools) == "<MediatedTools>"


def test_facade_nao_e_isinstance_do_protocol_de_executor(env: Env) -> None:
    tools = env.create().mediated_tools()
    for nome in AUTORIDADE:
        assert not callable(getattr(tools, nome, None))


# ======================================================================== 14. reasons / taxonomia


def test_toda_regra_de_path_do_codigo_tem_frase_fixa() -> None:
    allow = {
        "path.allowed",
        "path.mediated_ok",
        "path.post_create_ok",
        "path.post_open_ok",
        "path.post_open_skipped",
        "path.syntax_ok",
    }
    encontrados: set[str] = set()
    for nome in ("safety/paths.py", "safety/mediated_paths.py", "path_runtime.py"):
        texto = (APP_ROOT / nome).read_text(encoding="utf-8")
        encontrados |= set(re.findall(r'"(path\.[a-z_]+)"', texto))
    encontrados -= allow
    # as regras montadas por f-string em `decide_path`
    for label in ("symlink", "junction", "reparse_point"):
        encontrados |= {f"path.{label}_denied", f"path.{label}_unverified"}
    assert encontrados - set(DENIAL_PHRASES) == set()


def test_frases_sao_fixas_e_sem_dado_de_runtime() -> None:
    for rule, phrase in DENIAL_PHRASES.items():
        assert "{" not in phrase and "\\" not in phrase and ":\\" not in phrase, rule
        assert reason_for(rule) == f"{rule}: {phrase}"


def test_regra_desconhecida_levanta() -> None:
    with pytest.raises(UnknownRule):
        reason_for("regra.inventada")
    with pytest.raises(UnknownRule):
        category_of("regra.inventada")
    with pytest.raises(UnknownRule):
        phrase_for("")


def test_regras_tecnicas_sao_erro_e_as_de_politica_sao_negacao() -> None:
    for rule in (
        "path.inspection_failed",
        "path.post_open_inspection_failed",
        "path.post_create_inspection_failed",
        "path.open_failed",
        "path.no_target",
    ):
        assert status_for_rule(rule) is ToolStatus.ERROR
    for rule in (
        "path.secret_denied",
        "path.dot_git_denied",
        "path.escapes_root",
        "limit.read_file_bytes",
    ):
        assert status_for_rule(rule) is ToolStatus.DENIED


def test_mapeamento_de_categorias() -> None:
    assert category_of("path.secret_denied") is DecisionCategory.SECRET_ACCESS_BLOCKED
    assert category_of("path.dot_git_denied") is DecisionCategory.PATH_DENIED
    assert category_of("limit.write_file_bytes") is DecisionCategory.LIMIT_EXCEEDED
    assert category_of("capability.denied") is DecisionCategory.CAPABILITY_DENIED
    assert category_of("tool.unknown_operation") is DecisionCategory.CAPABILITY_DENIED
    for rule in ("path.toctou_recheck_failed", "path.parent_changed", "path.root_changed"):
        assert category_of(rule) is DecisionCategory.TOCTOU_RECHECK_FAILED
    assert category_of("path.escapes_root") is DecisionCategory.PATH_DENIED
    assert category_of("path.escapes_root", write=True) is DecisionCategory.OUT_OF_WORKTREE_WRITE


def test_error_codes_do_executor_e_do_path_runtime_tem_frase() -> None:
    from app.tool_executor.reasons import ERROR_PHRASES

    for failure in PathFailure:
        assert failure.value in ERROR_PHRASES
    for code in (
        "operation_not_implemented",
        "journal_unavailable",
        "executor_closed",
        "operation_cancelled",
        "workspace_unavailable",
        "internal_error",
    ):
        assert code in ERROR_PHRASES


def test_toda_regra_de_limite_prevista_tem_frase() -> None:
    for rule in (
        "limit.read_file_bytes",
        "limit.git_show_path_bytes",
        "limit.write_file_bytes",
        "limit.apply_patch_input_bytes",
        "limit.apply_patch_max_files",
        "limit.apply_patch_max_hunks",
        "limit.apply_patch_result_file_bytes",
        "limit.search_query_chars",
    ):
        assert rule in DENIAL_PHRASES


# ================================================================== 15. protocolos tipados


def test_implementacoes_concretas_satisfazem_os_protocols_por_atribuicao_tipada(
    resolver: InMemoryWorkspaceResolver,
) -> None:
    """A atribuição é a prova para o mypy; em runtime só confirma que a cadeia monta."""
    factory: ToolExecutorFactory = LocalWorktreeToolExecutorFactory(resolver)
    journal: DecisionJournal = InMemoryDecisionJournal()
    token: CancelToken = Token()
    executor: ToolExecutor = factory.create(
        ref(), SafetyPolicy(), DEVELOPER_V1_PROFILE, scope(), token, journal
    )
    tools: MediatedTools = executor.mediated_tools()
    assert isinstance(tools.execute(GitStatus()), ToolResult)
    _resolver: WorkspaceResolver = resolver
    assert _resolver is resolver
    executor.close()


# ============================================== 16. integridade → DENIED (fecho da E7.5-A)


def test_toda_falha_de_integridade_tem_regra_frase_e_categoria() -> None:
    assert set(INTEGRITY_RULES) == set(IntegrityFailure)
    persistidos = {kind.value for kind in SafetyEventKind}
    for category in IntegrityFailure:
        rule = rule_for_integrity(category)
        assert rule in DENIAL_PHRASES, rule
        assert status_for_rule(rule) is ToolStatus.DENIED
        for write in (False, True):
            assert category_of(rule, write=write).value in persistidos


def test_mapeamento_de_integridade_para_regra() -> None:
    assert rule_for_integrity(IntegrityFailure.ROOT_IDENTITY_CHANGED) == "path.root_changed"
    assert rule_for_integrity(IntegrityFailure.PARENT_IDENTITY_CHANGED) == "path.parent_changed"
    assert (
        rule_for_integrity(IntegrityFailure.TARGET_IDENTITY_CHANGED) == "path.toctou_recheck_failed"
    )
    assert rule_for_integrity(IntegrityFailure.REPARSE_DETECTED) == "path.reparse_detected"
    assert (
        rule_for_integrity(IntegrityFailure.INTEGRITY_UNVERIFIABLE) == "path.integrity_unverifiable"
    )
    assert rule_for_integrity(IntegrityFailure.TARGET_OUTSIDE_ROOT) == "path.escapes_root"
    for category in IntegrityFailure:
        if category is not IntegrityFailure.TARGET_OUTSIDE_ROOT:
            assert (
                category_of(rule_for_integrity(category)) is DecisionCategory.TOCTOU_RECHECK_FAILED
            )
    assert category_of("path.escapes_root", write=True) is DecisionCategory.OUT_OF_WORKTREE_WRITE


@pytest.mark.parametrize("failure", list(IntegrityFailure))
def test_executor_converte_violacao_de_integridade_em_denied_com_journal(
    ws: Path, failure: IntegrityFailure
) -> None:
    def violating(context: HandlerContext, request: ToolRequest) -> Outcome:
        raise PathIntegrityViolation(failure)

    executor, journal = direct_executor(ws, handlers={ReadFile: violating, WriteFile: violating})
    for request in (ReadFile("a.txt"), WriteFile("a.txt", "x")):
        result = executor.execute(request)
        rule = rule_for_integrity(failure)
        assert result.status is ToolStatus.DENIED and result.content is None
        assert result.reason == reason_for(rule)
        assert "\\" not in (result.reason or "")
    assert (executor.usage().operations, executor.usage().denials) == (2, 2)
    read_record, write_record = journal.records()
    assert read_record.rule_id == write_record.rule_id == rule_for_integrity(failure)
    assert read_record.subject_redacted == "a.txt"
    assert read_record.category is category_of(rule_for_integrity(failure), write=False)
    assert write_record.category is category_of(rule_for_integrity(failure), write=True)
    assert (read_record.sequence, write_record.sequence) == (1, 2)


def test_violacao_de_integridade_sem_trilha_envenena_o_executor(ws: Path) -> None:
    def violating(context: HandlerContext, request: ToolRequest) -> Outcome:
        raise PathIntegrityViolation(IntegrityFailure.REPARSE_DETECTED)

    executor, _ = direct_executor(ws, handlers={ReadFile: violating}, journal=FailingJournal())
    first = executor.execute(ReadFile("a"))
    assert first.status is ToolStatus.ERROR  # nunca DENIED sem a trilha
    assert executor.usage().denials == 0
    assert executor.execute(ReadFile("a")).reason.startswith("journal_unavailable")  # type: ignore[union-attr]


def test_a_raiz_substituida_por_outro_diretorio_e_negada_como_root_changed(
    env: Env, ws: Path, tmp_path: Path
) -> None:
    executor = env.create()
    other = tmp_path / "other"
    other.mkdir()
    ws.rename(tmp_path / "ws_old")
    other.rename(ws)  # outro diretório real, outra identidade, sem link
    result = executor.execute(ReadFile("a"))
    assert result.status is ToolStatus.DENIED
    assert result.reason is not None and result.reason.startswith("path.root_changed")
    (record,) = env.journal.records()
    assert record.category is DecisionCategory.TOCTOU_RECHECK_FAILED
    assert record.subject_redacted == "<workspace>"
    assert str(tmp_path) not in record.reason


def test_identidade_da_raiz_nao_verificavel_e_negada_antes_do_metodo(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import path_runtime

    executor = env.create()
    real = path_runtime._identity
    monkeypatch.setattr(
        path_runtime, "_identity", lambda info: ObjectIdentity(real(info).volume_id, 0)
    )
    result = executor.execute(ReadFile("a"))
    assert result.status is ToolStatus.DENIED
    assert result.reason is not None and result.reason.startswith("path.integrity_unverifiable")


# ----------------------------------------------- dispatch fechado e sem handlers injetáveis


def test_nao_existe_api_publica_de_handlers_arbitrarios() -> None:
    import inspect

    for target in (LocalWorktreeToolExecutor.__init__, LocalWorktreeToolExecutorFactory.__init__):
        assert not [n for n in inspect.signature(target).parameters if "handler" in n.lower()]
    assert not [
        n
        for n in inspect.signature(LocalWorktreeToolExecutorFactory.create).parameters
        if "handler" in n.lower()
    ]
    for nome in ("NO_HANDLERS", "Handler", "register", "handlers"):
        assert not hasattr(executor_module, nome), nome
    publicos = {n for n in dir(LocalWorktreeToolExecutor) if not n.startswith("_")}
    assert publicos == {"execute", "usage", "close", "mediated_tools"}


def test_o_construtor_do_executor_tem_exatamente_estes_parametros() -> None:
    import inspect

    parametros = set(inspect.signature(LocalWorktreeToolExecutor.__init__).parameters) - {"self"}
    assert parametros == {
        "workspace",
        "root",
        "policy",
        "profile",
        "run_scope",
        "cancel_token",
        "journal",
        "limits",
    }
    with pytest.raises(TypeError):
        LocalWorktreeToolExecutor(handlers={ReadFile: ok_handler()})  # type: ignore[call-arg]


def test_a_factory_nao_aceita_nem_repassa_handlers(resolver: InMemoryWorkspaceResolver) -> None:
    with pytest.raises(TypeError):
        LocalWorktreeToolExecutorFactory(resolver, handlers={})  # type: ignore[call-arg]


def test_o_despacho_e_uma_tabela_fechada_nas_nove_operacoes() -> None:
    dispatch = executor_module._DISPATCH
    assert set(dispatch) == set(TOOL_OPERATIONS)
    assert len(dispatch) == 9
    with pytest.raises(TypeError):
        dispatch[ExecutionWorkspaceRef] = "_read_file"  # type: ignore[index]
    for nome in dispatch.values():
        metodo = getattr(LocalWorktreeToolExecutor, nome)
        assert callable(metodo) and nome.startswith("_")


def test_cada_operacao_git_privada_falha_tecnicamente_sem_callable_externo(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chama os quatro métodos privados de Git (sem substituição) fora de repositório: falha
    técnica fixa (`ToolError git_failed`), nenhum registro de política."""
    from app.tool_executor.outcome import ToolError

    executor, journal = direct_executor(ws)
    for request in git_fora_de_repositorio():
        context = HandlerContext(
            root=executor._root,
            workspace=executor._workspace,
            policy=executor._policy,
            limits=executor._limits,
            is_cancelled=lambda: False,
            record_denial=lambda decision: None,
        )
        with pytest.raises(ToolError) as caught:
            getattr(executor, executor_module._DISPATCH[type(request)])(context, request)
        assert caught.value.code == "git_failed"
    assert len(journal) == 0


def test_dispatch_usa_o_metodo_privado_da_classe_em_tempo_de_chamada(ws: Path) -> None:
    """É assim que os testes (e só eles) observam o pipeline: o método é resolvido na chamada."""
    chamadas: list[str] = []

    def spy(context: HandlerContext, request: ToolRequest) -> Outcome:
        chamadas.append(type(request).__name__)
        return Outcome(ToolStatus.OK, content="x")

    executor, _ = direct_executor(ws, handlers={GitShow: spy})
    assert executor.execute(GitShow("HEAD")).status is ToolStatus.OK
    assert executor.execute(GitStatus()).reason.startswith("git_failed")  # type: ignore[union-attr]
    assert chamadas == ["GitShow"]


def test_um_executor_novo_nao_herda_fakes_de_outro_teste(ws: Path) -> None:
    """O `monkeypatch` é desfeito ao fim de cada teste: o padrão volta a ser o do código."""
    executor, _ = direct_executor(ws)
    assert executor.execute(GitShow("HEAD")).reason.startswith("git_failed")  # type: ignore[union-attr]
    assert executor.execute(ReadFile("a")).reason.startswith("not_found")  # type: ignore[union-attr]
