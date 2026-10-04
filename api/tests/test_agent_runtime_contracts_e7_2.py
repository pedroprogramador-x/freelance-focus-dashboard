"""E7.2 — contratos de `agent_runtime` e `tool_executor.contracts` ([05] §1–§6, [04] §1–§3).

Não há provider, processo nem executor aqui: só DTOs e Protocols. Os *fakes* abaixo existem
para o **mypy** conferir as assinaturas dos Protocols (`runtime_checkable` sozinho só olha
nomes de método). Um fake que satisfizesse o Protocol em runtime mas errasse um tipo falharia
em `mypy`, não em `pytest` — por isso os gates estáticos fazem parte da prova desta etapa.

Nada disto prova enforcement: a comprovação real é da E7.6/E8.
"""

from __future__ import annotations

import dataclasses
import inspect
import typing
from collections.abc import Callable
from typing import Any

import pytest

from app import agent_runtime
from app.agent_runtime import (
    AgentRunResult,
    AuditFindingDraft,
    AuditorProvider,
    AuditPurpose,
    AuditRequest,
    AuditResult,
    AuditVerdict,
    CapabilityDeclaration,
    DeveloperExecutionRequest,
    DeveloperProvider,
    EnforcementEvidence,
    EnforcementMethod,
    ExecutionWorkspaceRef,
    FilesReadSource,
    FindingSeverity,
    MediatedTools,
    MediatedUsage,
    RunLimits,
    RunScope,
    RunStatus,
    TestRequest,
    TestSummary,
    TokenSource,
    ToolExecutor,
    ToolExecutorFactory,
    ToolRequest,
    ToolResult,
)
from app.agent_runtime import (
    TestRunner as RunnerProtocol,
)
from app.safety import test_policy as safety_test_policy
from app.safety.capability_profile import (
    AUDITOR_V1_PROFILE,
    DEVELOPER_V1_PROFILE,
    EnforcementMode,
    ProviderCapabilityProfile,
    ProviderRole,
    check_v1,
    fingerprint_v1_projection,
)
from app.safety.policy import SafetyPolicy
from app.tool_executor import contracts as tool_contracts
from app.tool_executor.contracts import (
    TOOL_OPERATIONS,
    ApplyPatch,
    CancelToken,
    DecisionJournal,
    GitDiff,
    GitListTree,
    GitShow,
    GitStatus,
    ListDirectory,
    ReadFile,
    SearchText,
    ToolDecisionRecord,
    ToolStatus,
    WorkspaceKind,
    WriteFile,
    required_capability,
)
from app.tool_executor.validation import ContractViolation

COMMIT = "d" * 40
SHA = "a" * 64


class Token:
    def is_cancelled(self) -> bool:
        return False


def workspace() -> ExecutionWorkspaceRef:
    return ExecutionWorkspaceRef(WorkspaceKind.LOCAL_WORKTREE, "wt-1", COMMIT)


def limits() -> RunLimits:
    return RunLimits(max_tokens=1000, timeout_s=60)


def developer_request(**changes: Any) -> DeveloperExecutionRequest:
    base: dict[str, Any] = {
        "task_id": "t1",
        "run_id": "r1",
        "invocation_id": "i1",
        "goal": "corrigir o bug",
        "acceptance_criteria": ("testes passam",),
        "plan_steps": ("passo 1",),
        "rendered_context": "ctx",
        "rendered_context_hash": SHA,
        "context_manifest_id": "m1",
        "workspace_ref": workspace(),
        "capability_profile": DEVELOPER_V1_PROFILE,
        "limits": limits(),
        "cancel_token": Token(),
    }
    return DeveloperExecutionRequest(**{**base, **changes})


def run_report_fields(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": RunStatus.OK,
        "provider": "p",
        "provider_adapter": "a",
        "adapter_version": "1",
        "model": "m",
        "transport": "cli",
        "input_tokens": 10,
        "output_tokens": 5,
        "token_source": TokenSource.REPORTED,
        "duration_ms": 12,
        "summary": "feito",
    }
    return {**base, **changes}


def agent_result(**changes: Any) -> AgentRunResult:
    fields = {
        **run_report_fields(),
        "files_read": ("a.py",),
        "files_read_source": FilesReadSource.REPORTED,
    }
    return AgentRunResult(**{**fields, **changes})


def declaration(**changes: Any) -> CapabilityDeclaration:
    base: dict[str, Any] = {
        "role": ProviderRole.DEVELOPER,
        "adapter_id": "fake",
        "adapter_version": "1",
        "transport": "cli",
        "model": "m",
        "execution_config_hash": SHA,
        "supported_capabilities": DEVELOPER_V1_PROFILE,
        "effective_capabilities": DEVELOPER_V1_PROFILE,
        "enforcement_method": EnforcementMethod.CLI_FLAG,
        "evidence": EnforcementEvidence(applied=("--disallowed-tools=shell",)),
    }
    return CapabilityDeclaration(**{**base, **changes})


# ------------------------------------------- 1. Protocols: fakes conferidos pelo mypy


class FakeTools:
    def execute(self, request: ToolRequest) -> ToolResult:
        return ToolResult(ToolStatus.DENIED, type(request).__name__, reason="negado")


class FakeExecutor:
    def execute(self, request: ToolRequest) -> ToolResult:
        return FakeTools().execute(request)

    def usage(self) -> MediatedUsage:
        return MediatedUsage(files_read=(), operations=0, denials=0)

    def mediated_tools(self) -> MediatedTools:
        return FakeTools()

    def close(self) -> None:
        return None


class FakeJournal:
    def append(self, record: ToolDecisionRecord) -> None:
        return None


class FakeFactory:
    def create(
        self,
        workspace_ref: ExecutionWorkspaceRef,
        composed_policy: SafetyPolicy,
        effective_capability_profile: ProviderCapabilityProfile,
        run_scope: RunScope,
        cancel_token: CancelToken,
        decision_journal: DecisionJournal,
    ) -> ToolExecutor:
        return FakeExecutor()


class FakeDeveloper:
    def capability_profile(self) -> ProviderCapabilityProfile:
        return DEVELOPER_V1_PROFILE

    def run(
        self, request: DeveloperExecutionRequest, mediated_tools: MediatedTools
    ) -> AgentRunResult:
        return agent_result()


class FakeAuditor:
    def capability_profile(self) -> ProviderCapabilityProfile:
        return AUDITOR_V1_PROFILE

    def audit(self, request: AuditRequest) -> AuditResult:
        return AuditResult(**run_report_fields(), verdict=AuditVerdict.PASS, findings=())


class FakeRunner:
    def run(self, request: TestRequest) -> TestSummary:
        return make_summary()


def test_fakes_satisfazem_os_protocols_por_atribuicao_tipada() -> None:
    """A atribuição é a prova para o mypy; em runtime só confirmamos que o fluxo roda."""
    factory: ToolExecutorFactory = FakeFactory()
    developer: DeveloperProvider = FakeDeveloper()
    auditor: AuditorProvider = FakeAuditor()
    runner: RunnerProtocol = FakeRunner()

    executor = factory.create(
        workspace(),
        SafetyPolicy(),
        DEVELOPER_V1_PROFILE,
        RunScope("t1", "r1", "i1"),
        Token(),
        FakeJournal(),
    )
    assert isinstance(developer.run(developer_request(), executor.mediated_tools()), AgentRunResult)
    assert executor.usage().operations == 0
    assert auditor.capability_profile() == AUDITOR_V1_PROFILE
    assert runner.run(make_request()).framework == "pytest"


PROTOCOL_SIGNATURES: dict[type, dict[str, tuple[str, ...]]] = {
    DeveloperProvider: {
        "capability_profile": ("self",),
        "run": ("self", "request", "mediated_tools"),
    },
    AuditorProvider: {"capability_profile": ("self",), "audit": ("self", "request")},
    RunnerProtocol: {"run": ("self", "request")},
    ToolExecutor: {
        "execute": ("self", "request"),
        "usage": ("self",),
        "mediated_tools": ("self",),
        "close": ("self",),
    },
    MediatedTools: {"execute": ("self", "request")},
    ToolExecutorFactory: {
        "create": (
            "self",
            "workspace_ref",
            "composed_policy",
            "effective_capability_profile",
            "run_scope",
            "cancel_token",
            "decision_journal",
        )
    },
}


@pytest.mark.parametrize("protocol", list(PROTOCOL_SIGNATURES))
def test_protocols_tem_exatamente_os_metodos_congelados_em_05(protocol: type) -> None:
    esperado = PROTOCOL_SIGNATURES[protocol]
    publicos = {
        nome: fn for nome, fn in vars(protocol).items() if callable(fn) and not nome.startswith("_")
    }
    assert set(publicos) == set(esperado)
    for nome, parametros in esperado.items():
        assert tuple(inspect.signature(publicos[nome]).parameters) == parametros


def test_os_protocols_de_ferramenta_sao_reexportados_por_agent_runtime() -> None:
    assert agent_runtime.MediatedTools is tool_contracts.MediatedTools
    assert agent_runtime.ToolExecutor is tool_contracts.ToolExecutor
    assert agent_runtime.ToolExecutorFactory is tool_contracts.ToolExecutorFactory
    assert agent_runtime.ExecutionWorkspaceRef is tool_contracts.ExecutionWorkspaceRef


def dataclass_fields(cls: type) -> set[str]:
    return {field.name for field in dataclasses.fields(cls)}


def test_campos_do_developer_request_batem_com_05() -> None:
    assert dataclass_fields(DeveloperExecutionRequest) == {
        "task_id",
        "run_id",
        "invocation_id",
        "goal",
        "acceptance_criteria",
        "plan_steps",
        "rendered_context",
        "rendered_context_hash",
        "context_manifest_id",
        "workspace_ref",
        "capability_profile",
        "limits",
        "cancel_token",
    }


def test_campos_de_audit_e_test_batem_com_05() -> None:
    assert dataclass_fields(AuditRequest) == {
        "goal",
        "acceptance_criteria",
        "unified_diff",
        "test_summary",
        "context_manifest_id",
        "subject_run_id",
        "purpose",
        "rubric_version",
        "limits",
        "cancel_token",
    }
    assert dataclass_fields(TestRequest) == {"workspace_ref", "test_policy", "cancel_token"}
    assert dataclass_fields(TestSummary) == {
        "framework",
        "exit_code",
        "passed",
        "failed",
        "skipped",
        "duration_ms",
        "output_ref",
    }
    assert dataclass_fields(AuditFindingDraft) == {
        "severity",
        "category",
        "file",
        "line",
        "summary",
        "detail",
    }


RUN_FIELDS = {
    "status",
    "provider",
    "provider_adapter",
    "adapter_version",
    "model",
    "transport",
    "input_tokens",
    "output_tokens",
    "token_source",
    "duration_ms",
    "summary",
    "error_summary",
    "log_ref",
}


def test_campos_dos_resultados_batem_com_05() -> None:
    assert dataclass_fields(AgentRunResult) == RUN_FIELDS | {"files_read", "files_read_source"}
    assert dataclass_fields(AuditResult) == RUN_FIELDS | {"verdict", "findings"}


def test_resultado_nao_carrega_mudanca_medida_nem_raciocinio() -> None:
    """[05] §6: o provider não é fonte confiável da própria mudança; sem chain-of-thought."""
    proibidos = ("files_changed", "diff_added", "diff_removed", "thinking", "reasoning", "prompt")
    for cls in (AgentRunResult, AuditResult, DeveloperExecutionRequest):
        for campo in dataclass_fields(cls):
            assert not any(p in campo for p in proibidos), f"{cls.__name__}.{campo}"


def test_workspace_ref_nao_expoe_caminho_de_filesystem() -> None:
    assert dataclass_fields(ExecutionWorkspaceRef) == {
        "kind",
        "id",
        "base_commit",
        "capabilities_hint",
    }
    assert {kind.value for kind in WorkspaceKind} == {"local_worktree", "remote"}


# ---------------------------------------------------------- 2. validação de entrada


def test_workspace_ref_valida_commit_e_kind() -> None:
    for commit in ("", "abc", "D" * 40, "z" * 40, "d" * 39):
        with pytest.raises(ContractViolation):
            ExecutionWorkspaceRef(WorkspaceKind.LOCAL_WORKTREE, "wt", commit)
    with pytest.raises(ContractViolation):
        ExecutionWorkspaceRef("local_worktree", "wt", COMMIT)  # type: ignore[arg-type]
    ExecutionWorkspaceRef(WorkspaceKind.REMOTE, "r", "e" * 64)


@pytest.mark.parametrize(
    "changes",
    [
        {"goal": " "},
        {"task_id": ""},
        {"acceptance_criteria": ["lista, não tupla"]},
        {"plan_steps": (1,)},
        {"rendered_context_hash": "abc"},
        {"rendered_context_hash": "A" * 64},
        {"workspace_ref": "wt"},
        {"capability_profile": {"read_files": "mediated"}},
        {"limits": (1, 2)},
        {"cancel_token": object()},
        {"goal": "a\x00b"},
    ],
)
def test_developer_request_recusa_entrada_invalida(changes: dict[str, Any]) -> None:
    with pytest.raises(ContractViolation):
        developer_request(**changes)


def test_run_limits_recusam_zero_negativo_e_bool() -> None:
    for kwargs in ({"max_tokens": 0, "timeout_s": 1}, {"max_tokens": 1, "timeout_s": -1}):
        with pytest.raises(ContractViolation):
            RunLimits(**kwargs)
    with pytest.raises(ContractViolation):
        RunLimits(max_tokens=True, timeout_s=1)


def test_audit_request_benchmark_exige_rubrica() -> None:
    campos: dict[str, Any] = {
        "goal": "g",
        "acceptance_criteria": (),
        "unified_diff": "",
        "test_summary": None,
        "context_manifest_id": "m",
        "subject_run_id": "r",
        "limits": limits(),
        "cancel_token": Token(),
    }
    with pytest.raises(ContractViolation):
        AuditRequest(**campos, purpose=AuditPurpose.BENCHMARK_EVALUATION, rubric_version=None)
    AuditRequest(**campos, purpose=AuditPurpose.BENCHMARK_EVALUATION, rubric_version="v1")
    AuditRequest(**campos, purpose=AuditPurpose.WORKFLOW_AUDIT, rubric_version=None)


def test_audit_request_so_carrega_o_diff_nao_o_repositorio() -> None:
    assert "workspace_ref" not in dataclass_fields(AuditRequest)
    assert "rendered_context" not in dataclass_fields(AuditRequest)


def test_audit_result_valida_veredito_e_findings() -> None:
    finding = AuditFindingDraft(FindingSeverity.HIGH, "seguranca", "a.py", 3, "resumo")
    AuditResult(**run_report_fields(), verdict=AuditVerdict.PASS_WITH_FINDINGS, findings=(finding,))
    with pytest.raises(ContractViolation):
        AuditResult(**run_report_fields(), verdict=AuditVerdict.PASS_WITH_FINDINGS, findings=())
    with pytest.raises(ContractViolation):
        AuditResult(**run_report_fields(), verdict=AuditVerdict.FAIL, findings=[finding])  # type: ignore[arg-type]
    with pytest.raises(ContractViolation):
        AuditFindingDraft(FindingSeverity.LOW, "c", "a.py", 0, "s")
    with pytest.raises(ContractViolation):
        AuditFindingDraft("high", "c", None, None, "s")  # type: ignore[arg-type]


def test_test_summary_recusa_contadores_invalidos() -> None:
    with pytest.raises(ContractViolation):
        TestSummary("pytest", 0, -1, 0, 0, 1, None)
    with pytest.raises(ContractViolation):
        TestSummary("pytest", True, 1, 0, 0, 1, None)


def test_tokens_e_origem_precisam_concordar() -> None:
    agent_result(input_tokens=None, output_tokens=None, token_source=TokenSource.UNAVAILABLE)
    agent_result(input_tokens=1, output_tokens=None, token_source=TokenSource.ESTIMATED)
    with pytest.raises(ContractViolation):
        agent_result(input_tokens=None, output_tokens=None, token_source=TokenSource.REPORTED)
    with pytest.raises(ContractViolation):
        agent_result(input_tokens=5, token_source=TokenSource.UNAVAILABLE)
    with pytest.raises(ContractViolation):
        agent_result(input_tokens=-1)


# ------------------------------------------------------------------ 3. imutabilidade

FROZEN_INSTANCES: list[Callable[[], Any]] = [
    workspace,
    limits,
    developer_request,
    agent_result,
    declaration,
    lambda: ReadFile("a.py"),
    lambda: GitStatus(),
    lambda: ToolResult(ToolStatus.OK, "ReadFile", content=""),
    lambda: MediatedUsage((), 0, 0),
    lambda: RunScope("t", "r", "i"),
    lambda: EnforcementEvidence(applied=("x",)),
    lambda: AuditFindingDraft(FindingSeverity.INFO, "c", None, None, "s"),
]


@pytest.mark.parametrize("factory", FROZEN_INSTANCES)
def test_dtos_sao_imutaveis(factory: Callable[[], Any]) -> None:
    instancia = factory()
    campo = next(iter(dataclass_fields(type(instancia))), None)
    if campo is None:  # GitStatus: sem campos, e slots impede atributo novo
        with pytest.raises((AttributeError, TypeError)):
            instancia.novo = 1
        return
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instancia, campo, "outro")


# --------------------------------------------- 4. ausente (None) ≠ coleção vazia


def test_files_read_none_difere_de_tupla_vazia() -> None:
    lido_nada = agent_result(files_read=(), files_read_source=FilesReadSource.REPORTED)
    indisponivel = agent_result(files_read=None, files_read_source=FilesReadSource.UNAVAILABLE)
    assert lido_nada.files_read == ()
    assert indisponivel.files_read is None
    assert lido_nada != indisponivel


def test_files_read_e_origem_precisam_concordar() -> None:
    with pytest.raises(ContractViolation):
        agent_result(files_read=None, files_read_source=FilesReadSource.REPORTED)
    with pytest.raises(ContractViolation):
        agent_result(files_read=(), files_read_source=FilesReadSource.UNAVAILABLE)
    with pytest.raises(ContractViolation):
        agent_result(files_read=["a.py"])
    agent_result(files_read=("a.py",), files_read_source=FilesReadSource.INFERRED)


def test_uso_mediado_e_sempre_uma_tupla_reportada() -> None:
    assert MediatedUsage(files_read=(), operations=0, denials=0).files_read == ()
    with pytest.raises(ContractViolation):
        MediatedUsage(files_read=None, operations=0, denials=0)  # type: ignore[arg-type]
    with pytest.raises(ContractViolation):
        MediatedUsage(files_read=(), operations=1, denials=2)


# ---------------------------------------------------- 5–6. superfície fechada de ferramentas

NOVE = {
    "ReadFile": "read_files",
    "ListDirectory": "read_files",
    "SearchText": "read_files",
    "WriteFile": "write_files",
    "ApplyPatch": "write_files",
    "GitStatus": "git_read",
    "GitDiff": "git_read",
    "GitShow": "git_read",
    "GitListTree": "git_read",
}


def test_tool_request_e_exatamente_a_uniao_das_nove_operacoes() -> None:
    membros = {t.__name__ for t in typing.get_args(ToolRequest)}
    assert membros == set(NOVE)
    assert {t.__name__ for t in TOOL_OPERATIONS} == set(NOVE)
    assert len(TOOL_OPERATIONS) == 9


def test_cada_operacao_mapeia_para_a_capability_de_04() -> None:
    amostras: list[ToolRequest] = [
        ReadFile("a"),
        ListDirectory("."),
        SearchText("x"),
        WriteFile("a", ""),
        ApplyPatch("--- a"),
        GitStatus(),
        GitDiff(),
        GitShow("HEAD"),
        GitListTree(),
    ]
    assert {type(a).__name__: required_capability(a).value for a in amostras} == NOVE


def test_nao_existe_operacao_de_execucao_de_comando() -> None:
    nomes = {
        name.lower().replace("execution", "").replace("executor", "").replace("execute", "")
        for name in vars(tool_contracts)
    }
    for termo in ("exec", "shell", "runcommand", "subprocess", "terminal"):
        assert not any(termo in n for n in nomes), termo
    for operacao in TOOL_OPERATIONS:
        for campo in dataclass_fields(operacao):
            assert campo not in {"argv", "command", "cmd", "shell", "executable", "args"}


def test_operacao_fora_da_uniao_e_recusada() -> None:
    class ExecCommand:  # o tipo que **não** pode existir
        pass

    with pytest.raises(ContractViolation):
        required_capability(ExecCommand())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "build",
    [
        lambda: ReadFile(""),
        lambda: ReadFile("a\x00b"),
        lambda: ListDirectory(" "),
        lambda: SearchText(""),
        lambda: WriteFile("a", 1),  # type: ignore[arg-type]
        lambda: ApplyPatch(""),
        lambda: GitDiff(ref="--output=/tmp/x"),
        lambda: GitDiff(ref="a b"),
        lambda: GitShow(ref="-p"),
        lambda: GitListTree(ref="--help"),
    ],
)
def test_operacoes_recusam_parametros_invalidos(build: Callable[[], Any]) -> None:
    with pytest.raises(ContractViolation):
        build()


REFS_COM_CAMINHO = ["HEAD:.env", "HEAD:README.md", "HEAD^{tree}:.env", ":/.env", "a:b", ":"]
REFS_LEGITIMAS = ["HEAD", "d" * 40, "e" * 64, "refs/heads/main", "HEAD~2", "v1.0", "main^{tree}"]


@pytest.mark.parametrize("ref", REFS_COM_CAMINHO)
def test_ref_nao_pode_carregar_caminho_em_nenhum_dto_git(ref: str) -> None:
    """P2 da auditoria E7.2: `ref="HEAD:.env"` contornaria a checagem de `path`."""
    for build in (
        lambda: GitShow(ref=ref),
        lambda: GitShow(ref=ref, path=None),
        lambda: GitShow(ref=ref, path="a.py"),
        lambda: GitDiff(ref=ref),
        lambda: GitListTree(ref=ref),
    ):
        with pytest.raises(ContractViolation):
            build()


@pytest.mark.parametrize("ref", REFS_LEGITIMAS)
def test_refs_legitimas_continuam_aceitas(ref: str) -> None:
    assert GitShow(ref=ref).ref == ref
    assert GitDiff(ref=ref).ref == ref
    assert GitListTree(ref=ref).ref == ref


def test_caminho_so_viaja_no_campo_path() -> None:
    assert GitShow(ref="HEAD", path=".env").path == ".env"
    for cls in (GitShow, GitDiff, GitListTree):
        assert [f for f in dataclass_fields(cls) if "ref" in f] == ["ref"]


def test_tool_result_negacao_e_explicita_e_ok_nao_tem_reason() -> None:
    with pytest.raises(ContractViolation):
        ToolResult(ToolStatus.DENIED, "ReadFile")
    with pytest.raises(ContractViolation):
        ToolResult(ToolStatus.DENIED, "ReadFile", content="x", reason="não")
    with pytest.raises(ContractViolation):
        ToolResult(ToolStatus.OK, "ReadFile", content="x", reason="não")
    ToolResult(ToolStatus.OK, "WriteFile")


# ------------------------------------------------------ 9. compatibilidade com a E7.1


def test_requests_carregam_o_perfil_da_e7_1_sem_o_aceitar() -> None:
    """Representar não é aceitar: um perfil recusável cabe no request."""
    permissivo = dataclasses.replace(
        DEVELOPER_V1_PROFILE, execute_commands=EnforcementMode.UNMEDIATED
    )
    request = developer_request(capability_profile=permissivo)
    assert check_v1(request.capability_profile, ProviderRole.DEVELOPER) != ()
    assert check_v1(developer_request().capability_profile, ProviderRole.DEVELOPER) == ()


def test_declaracao_reusa_o_perfil_da_e7_1_e_a_politica_continua_sendo_dela() -> None:
    ruim = dataclasses.replace(DEVELOPER_V1_PROFILE, execute_commands=EnforcementMode.MEDIATED)
    d = declaration(effective_capabilities=ruim)  # é representável...
    assert check_v1(d.effective_capabilities, ProviderRole.DEVELOPER)  # ...e recusável
    assert check_v1(declaration().effective_capabilities, ProviderRole.DEVELOPER) == ()
    assert check_v1(AUDITOR_V1_PROFILE, ProviderRole.AUDITOR) == ()


def test_fingerprint_v1_continua_intocado() -> None:
    assert fingerprint_v1_projection(DEVELOPER_V1_PROFILE) == {
        "v": 1,
        "execute_commands": "disabled",
        "read_files": "mediated",
        "write_files": "mediated",
        "network_access": "disabled",
    }


# ---------------------------------------- vocabulário e forma da declaração (E7.2)


def test_vocabulario_de_enforcement_method_e_o_de_04() -> None:
    assert {m.value for m in EnforcementMethod} == {
        "cli_flag",
        "config_file",
        "tool_allowlist",
        "api_tool_schema",
        "process_env",
        "not_enforceable",
    }


def test_declaracao_recusa_incoerencias_estruturais() -> None:
    with pytest.raises(ContractViolation):  # método sem nada aplicado
        declaration(evidence=EnforcementEvidence())
    with pytest.raises(ContractViolation):  # not_enforceable com evidência
        declaration(enforcement_method=EnforcementMethod.NOT_ENFORCEABLE)
    with pytest.raises(ContractViolation):
        declaration(execution_config_hash="curto")
    with pytest.raises(ContractViolation):
        declaration(effective_capabilities={"read_files": "mediated"})
    with pytest.raises(ContractViolation):
        declaration(enforcement_method="cli_flag")
    declaration(
        enforcement_method=EnforcementMethod.NOT_ENFORCEABLE, evidence=EnforcementEvidence()
    )


def test_evidencia_nao_pode_conter_segredo() -> None:
    with pytest.raises(ContractViolation):
        EnforcementEvidence(applied=("--api-key=" + "sk-" + "a1B2c3D4" * 5,))
    with pytest.raises(ContractViolation):
        EnforcementEvidence(applied=("ok",), probe_result="token ghp_" + "a1B2c3D4e5" * 4)


def test_declaration_hash_e_deterministico_e_sensivel_a_cada_campo() -> None:
    base = declaration()
    assert base.declaration_hash() == declaration().declaration_hash()
    variantes = [
        declaration(adapter_version="2"),
        declaration(adapter_id="outro"),
        declaration(transport="api"),
        declaration(model="outro"),
        declaration(execution_config_hash="b" * 64),
        declaration(enforcement_method=EnforcementMethod.CONFIG_FILE),
        declaration(evidence=EnforcementEvidence(applied=("--outra",))),
        declaration(role=ProviderRole.AUDITOR),
        declaration(
            supported_capabilities=dataclasses.replace(
                DEVELOPER_V1_PROFILE, network=EnforcementMode.MEDIATED
            )
        ),
    ]
    hashes = {v.declaration_hash() for v in variantes}
    assert base.declaration_hash() not in hashes
    assert len(hashes) == len(variantes)


# ---------------------- 10. declaração isolada nunca vira autorização de execução

AUTORIZACAO = ("proven", "authoriz", "approved", "accepted", "trusted", "can_execute", "allowed")


def test_declaracao_nao_tem_campo_nem_metodo_que_soe_como_prova() -> None:
    membros = {n.lower() for n in dir(CapabilityDeclaration) if not n.startswith("__")}
    membros |= {n.lower() for n in dir(EnforcementEvidence) if not n.startswith("__")}
    for termo in AUTORIZACAO:
        assert not any(termo in m for m in membros), termo
    assert "effective_profile_hash" not in membros


def test_declaracao_perfeita_continua_sendo_apenas_declaracao() -> None:
    """Perfil V1 exato + método válido + evidência: ainda é o que um mentiroso declararia."""
    d = declaration()
    assert check_v1(d.effective_capabilities, ProviderRole.DEVELOPER) == ()
    assert not hasattr(d, "proven")
    assert not hasattr(d, "effective_profile_hash")
    assert isinstance(d.declaration_hash(), str)  # identificador, não veredito
    # Nenhum caminho de agent_runtime devolve um `CapabilityProof`: o módulo nem o conhece.
    for nome in dir(agent_runtime):
        assert "proof" not in nome.lower()


def test_agent_runtime_nao_usa_a_prova_da_guarda_de_entrada() -> None:
    """`CapabilityProof`/`CapabilityProver`/`start_execution` são do orchestrator ([01] §3).

    Confere identificadores e imports via AST — docstrings podem (e devem) explicar por que
    a declaração **não** é uma `CapabilityProof`.
    """
    import ast
    from pathlib import Path

    proibidos = {"CapabilityProof", "CapabilityProver", "start_execution", "proven"}
    raiz = Path(agent_runtime.__file__).parent
    for arquivo in raiz.rglob("*.py"):
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        usados: set[str] = set()
        for no in ast.walk(arvore):
            if isinstance(no, ast.Name):
                usados.add(no.id)
            elif isinstance(no, ast.Attribute):
                usados.add(no.attr)
            elif isinstance(no, ast.arg):
                usados.add(no.arg)
            elif isinstance(no, ast.alias):
                usados.add(no.name.split(".")[-1])
        assert not (usados & proibidos), (arquivo.name, usados & proibidos)


# ------------------------------------------------------------------ TestRequest / TestPolicy


def make_summary(**changes: Any) -> TestSummary:
    fields: dict[str, Any] = {
        "framework": "pytest",
        "exit_code": 0,
        "passed": 3,
        "failed": 0,
        "skipped": 0,
        "duration_ms": 10,
        "output_ref": None,
    }
    return TestSummary(**{**fields, **changes})


def make_request(**changes: Any) -> TestRequest:
    policy = safety_test_policy.parse_test_policy(
        {
            "runner_id": "generic-subprocess-v1",
            "executable": "pytest",
            "argv": ["-q", "tests"],
            "timeout_seconds": 600,
            "env_allowlist": ["PATH", "HOME"],
            "network_policy": "unrestricted",
            "output_limits": {"max_stdout_bytes": 1_048_576, "max_stderr_bytes": 262_144},
            "cwd_mode": "task_worktree",
        }
    )
    fields: dict[str, Any] = {
        "workspace_ref": workspace(),
        "test_policy": policy,
        "cancel_token": Token(),
    }
    return TestRequest(**{**fields, **changes})


def test_test_request_reusa_a_test_policy_e_os_hashes_dela() -> None:
    request = make_request()
    assert isinstance(request.test_policy, safety_test_policy.TestPolicy)
    # Os hashes canônicos são métodos da própria política; este módulo não define outros.
    assert request.test_policy.command_hash() == request.test_policy.command_hash()
    for nome in ("command_hash", "policy_hash"):
        assert nome not in dataclass_fields(TestRequest)
        assert not hasattr(TestRequest, nome)


def test_test_request_recusa_politica_que_nao_e_test_policy() -> None:
    with pytest.raises(ContractViolation):
        make_request(test_policy={"executable": "pytest"})
    with pytest.raises(ContractViolation):
        make_request(test_policy=None)
    with pytest.raises(ContractViolation):
        make_request(cancel_token="x")
