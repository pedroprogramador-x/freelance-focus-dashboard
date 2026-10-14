"""E8.4.3 — contrato **puro** da invocação do Developer: nada toca banco, git, filesystem ou rede.

A decisão que fecha o Run (`decide_run`) é verificada de forma **exaustiva**: toda combinação de
status do provider, violação crítica, binding, executor, limite, metadado do provider e
consistência da evidência satisfaz as propriedades que a E8.4.3 promete — sucesso só quando *tudo* é
coerente; violação crítica da **evidência do executor** vence qualquer metadado do provider;
binding não autorizado nunca vira sucesso; metadado contraditório nunca é fonte de tokens/resumo.
Também: o coletor de evidência (origem e associação) e a compatibilidade de identidade do claim.
"""

from __future__ import annotations

import itertools
import threading
from dataclasses import fields, replace
from typing import Any

import pytest

from app.db.enums import RunStatus
from app.orchestrator.developer_invocation_contract import (
    CRITICAL_DENIAL_CATEGORIES,
    KNOWN_DENIAL_CATEGORIES,
    BindingOutcome,
    ClaimOutcome,
    ClaimResult,
    DeveloperInvocationInput,
    DeveloperInvocationReport,
    DeveloperInvocationResult,
    DeveloperRunSpec,
    EvidenceCollector,
    EvidenceSnapshot,
    ExistingRunFacts,
    Finalization,
    InvocationCode,
    InvocationFacts,
    InvocationStatus,
    ReportedStatus,
    ToolDenial,
    approved_binding_refusal,
    claim_compatible,
    clip_summary,
    decide_run,
    escalate_for_evidence,
    evidence_inconsistencies,
    provider_metadata_contradictions,
    run_error_summary,
)
from app.orchestrator.execution_contract import DeveloperOutcome
from app.orchestrator.fingerprint import tool_profile_hash
from tests.capability_helpers import expected_binding
from tests.developer_invocation_support_e8_4_3 import denial, make_report, scripted_identity

RUN = "run-dev"
IDENT = scripted_identity()


def facts(**changes: Any) -> InvocationFacts:
    base = {
        "code": None,
        "task_id": "t",
        "control_run_id": "c",
        "approved_adapter": IDENT.adapter,
        "approved_adapter_version": IDENT.adapter_version,
        "approved_model": IDENT.model,
        "approved_tool_profile_hash": tool_profile_hash(),
    }
    return InvocationFacts(**{**base, **changes})


def expected(**changes: Any) -> Any:
    from tests.capability_helpers import declaration

    return expected_binding(declaration(transport="api"), **{"transport": "api", **changes})


# ------------------------------------------------------------------ decisão: exaustiva


def _collector() -> EvidenceCollector:
    return EvidenceCollector(RUN, task_id="task", invocation_id="inv")


def _evidence(*items: ToolDenial) -> EvidenceSnapshot:
    collector = _collector()
    for item in items:
        collector.append(item)
    return collector.snapshot()


def _cases() -> list[tuple[str, DeveloperInvocationReport, EvidenceSnapshot]]:
    out: list[tuple[str, DeveloperInvocationReport, EvidenceSnapshot]] = []
    identities = {"ident-ok": IDENT, "ident-outro": replace(IDENT, model="outro")}
    evidences = {
        "sem-negacao": _evidence(),
        "negacao-comum": _evidence(denial(RUN, 1, "path_denied")),
        "critica-out": _evidence(denial(RUN, 1, "out_of_worktree_write")),
        "critica-toctou": _evidence(denial(RUN, 1, "toctou_recheck_failed")),
        "evidencia-incoerente": _evidence(denial(RUN, 1), denial("outro-run", 1)),
    }
    for status, limit, (ename, ev), (iname, ident), closed in itertools.product(
        ReportedStatus, (False, True), evidences.items(), identities.items(), (True, False)
    ):
        report = make_report(
            ident,
            status=status,
            limit_exceeded=limit,
            executor_closed=closed,
            operations=len(ev.denials) + 1,
            denial_count=len(ev.denials),
            error_summary=None if status is ReportedStatus.OK else "x",
        )
        out.append((f"{status.value}/limit={limit}/{ename}/{iname}/closed={closed}", report, ev))
    return out


@pytest.mark.parametrize("binding_verified", [True, False])
def test_propriedades_da_decisao_valem_para_toda_combinacao(binding_verified: bool) -> None:
    cases = _cases()
    assert len(cases) == 5 * 2 * 5 * 2 * 2  # a tabela é de verdade exaustiva (200)
    for name, report, evidence in cases:
        decision = decide_run(report, evidence, identity=IDENT, binding_verified=binding_verified)
        meta_bad = provider_metadata_contradictions(report, IDENT)
        ev_bad = evidence_inconsistencies(report, evidence)
        succeeded = decision.run_status is RunStatus.OK

        # sucesso SÓ quando tudo é coerente — a conjunção inteira, nada menos.
        assert succeeded == (
            report.status is ReportedStatus.OK
            and not meta_bad
            and not ev_bad
            and not evidence.critical
            and binding_verified
            and report.executor_closed
        ), name
        assert (decision.outcome is DeveloperOutcome.COMPLETED) == succeeded, name
        assert (decision.code is InvocationCode.COMPLETED) == succeeded, name
        # a evidência do EXECUTOR vence qualquer metadado do provider (E843-AUD-002)
        if evidence.critical:
            assert decision.code is InvocationCode.CRITICAL_VIOLATION, name
            assert decision.run_status is RunStatus.BLOCKED and decision.critical, name
        elif not binding_verified:
            assert decision.code is InvocationCode.BINDING_REVOKED, name
            assert decision.run_status is RunStatus.BLOCKED and decision.critical, name
        elif meta_bad or ev_bad:
            assert decision.code is InvocationCode.REPORT_CONTRADICTORY, name
            assert decision.run_status is RunStatus.ERROR and not decision.critical, name
        # o que é do provider só é persistido se os metadados dele forem coerentes
        assert decision.provider_trusted == (not meta_bad), name
        # só o orçamento esgotado, reportado como `blocked` por metadado coerente, gera o evento
        if decision.limit_exceeded:
            assert report.limit_exceeded and report.status is ReportedStatus.BLOCKED, name
            assert not meta_bad, name


def test_violacao_critica_e_integrity_violation_mesmo_com_metadado_contraditorio() -> None:
    contradictory = make_report(replace(IDENT, model="outro"), limit_exceeded=True)
    for category in CRITICAL_DENIAL_CATEGORIES:
        decision = decide_run(
            contradictory,
            _evidence(denial(RUN, 1, category)),
            identity=IDENT,
            binding_verified=True,
        )
        assert decision.critical and decision.code is InvocationCode.CRITICAL_VIOLATION
        assert decision.run_status is RunStatus.BLOCKED
        assert decision.provider_trusted is False and decision.limit_exceeded is False


def test_statuses_do_provider_mapeiam_um_para_um_quando_nada_mais_falha() -> None:
    table = {
        ReportedStatus.ERROR: (RunStatus.ERROR, DeveloperOutcome.PROVIDER_ERROR),
        ReportedStatus.TIMEOUT: (RunStatus.TIMEOUT, DeveloperOutcome.TIMEOUT),
        ReportedStatus.CANCELLED: (RunStatus.CANCELLED, DeveloperOutcome.CANCELLED),
        ReportedStatus.BLOCKED: (RunStatus.BLOCKED, DeveloperOutcome.POLICY_BLOCKED),
    }
    for status, (run_status, outcome) in table.items():
        report = make_report(IDENT, status=status, error_summary="x")
        decision = decide_run(report, _evidence(), identity=IDENT, binding_verified=True)
        assert (decision.run_status, decision.outcome) == (run_status, outcome)
    limited = make_report(
        IDENT, status=ReportedStatus.BLOCKED, limit_exceeded=True, error_summary="x"
    )
    decision = decide_run(limited, _evidence(), identity=IDENT, binding_verified=True)
    assert decision.outcome is DeveloperOutcome.LIMIT_EXCEEDED and decision.limit_exceeded


def test_a_decisao_nunca_pede_retry_e_o_resultado_nao_tem_campo_de_retry() -> None:
    names = {f.name for f in fields(DeveloperInvocationResult)}
    assert not any("retry" in n or "again" in n or "repeat" in n for n in names)
    decision = decide_run(make_report(IDENT), _evidence(), identity=IDENT, binding_verified=True)
    assert not any("retry" in f for f in decision.__slots__)


def test_escalar_pela_evidencia_nao_deixa_decisao_sem_relatorio_esconder_violacao() -> None:
    soft = decide_run(
        make_report(IDENT, status=ReportedStatus.ERROR, error_summary="x"),
        _evidence(),
        identity=IDENT,
        binding_verified=True,
    )
    assert escalate_for_evidence(soft, _evidence(denial(RUN, 1, "path_denied"))) is soft
    for category in CRITICAL_DENIAL_CATEGORIES:
        hard = escalate_for_evidence(soft, _evidence(denial(RUN, 1, category)))
        assert hard.critical and hard.code is InvocationCode.CRITICAL_VIOLATION
        assert hard.run_status is RunStatus.BLOCKED
        assert escalate_for_evidence(hard, _evidence(denial(RUN, 1, category))) == hard


# ------------------------------------------- metadado do provider × evidência do executor


_META = {
    "limit sem blocked": make_report(IDENT, limit_exceeded=True),
    "ok sem chamada": make_report(IDENT, provider_called=False),
    "timeout sem chamada": make_report(
        IDENT, status=ReportedStatus.TIMEOUT, provider_called=False, error_summary="x"
    ),
    "outra identidade": make_report(replace(IDENT, model="outro")),
    "outro transporte": make_report(replace(IDENT, transport="cli")),
}


@pytest.mark.parametrize("name", list(_META))
def test_cada_contradicao_de_metadado_do_provider_e_detectada(name: str) -> None:
    assert provider_metadata_contradictions(_META[name], IDENT) is True
    assert evidence_inconsistencies(_META[name], _evidence()) is False  # são fontes distintas


def test_metadado_coerente_nao_e_contraditorio() -> None:
    assert provider_metadata_contradictions(make_report(IDENT), IDENT) is False
    err = make_report(
        IDENT,
        status=ReportedStatus.ERROR,
        provider_called=False,
        error_summary="x",
        files_read=None,
    )
    assert provider_metadata_contradictions(err, IDENT) is False


def test_cada_incoerencia_de_evidencia_e_detectada_sem_tocar_o_metadado() -> None:
    assert evidence_inconsistencies(make_report(IDENT, operations=2, denial_count=2), _evidence())
    assert evidence_inconsistencies(
        make_report(IDENT, operations=0, denial_count=1), _evidence(denial(RUN, 1))
    )
    assert evidence_inconsistencies(make_report(IDENT, files_read=None), _evidence())
    assert evidence_inconsistencies(make_report(IDENT), _evidence(denial("outro", 1)))  # rejeitado
    assert evidence_inconsistencies(make_report(IDENT), _evidence(denial(RUN, 2)))  # lacuna
    ok = make_report(IDENT, operations=3, denial_count=2)
    assert not evidence_inconsistencies(ok, _evidence(denial(RUN, 1), denial(RUN, 2)))
    for report in (make_report(IDENT, operations=2, denial_count=2),):
        assert provider_metadata_contradictions(report, IDENT) is False


def test_relatorio_do_provider_nao_tem_campo_de_negacao() -> None:
    """Não há por onde o provider forjar evidência do executor: o tipo não permite."""
    names = {f.name for f in fields(DeveloperInvocationReport)}
    assert not any("denial" in n and n != "denial_count" for n in names), names
    with pytest.raises(TypeError):
        make_report(IDENT, denials=())


# ------------------------------------------------------------------- coletor de evidência


def test_coletor_aceita_so_registro_do_proprio_run_em_sequencia() -> None:
    collector = _collector()
    collector.append(denial(RUN, 1))
    collector.append(denial(RUN, 2, "secret_access_blocked"))
    snap = collector.snapshot()
    assert [d.sequence for d in snap.denials] == [1, 2] and not snap.inconsistent
    assert snap.rejected == 0 and not snap.critical


def test_coletor_descarta_registro_de_outro_run_e_marca_inconsistente() -> None:
    collector = _collector()
    collector.append(denial("outro-run", 1, "toctou_recheck_failed"))
    collector.append(denial(RUN, 1))
    snap = collector.snapshot()
    assert [d.run_id for d in snap.denials] == [RUN]
    assert snap.inconsistent and snap.rejected == 1
    assert snap.critical is False  # a violação crítica ALHEIA não conta para este Run


@pytest.mark.parametrize(
    "foreign",
    [{"task_id": "outra"}, {"invocation_id": "outra"}, {"run_id": "outro"}],
)
def test_coletor_descarta_registro_de_outra_task_ou_invocacao(foreign: dict[str, str]) -> None:
    collector = _collector()
    changes = dict(foreign)
    collector.append(denial(changes.pop("run_id", RUN), 1, "toctou_recheck_failed", **changes))
    snap = collector.snapshot()
    assert snap.denials == () and snap.rejected == 1 and snap.inconsistent and not snap.critical


def test_coletor_exige_o_escopo_completo_na_construcao() -> None:
    for kwargs in ({"task_id": " ", "invocation_id": "i"}, {"task_id": "t", "invocation_id": ""}):
        with pytest.raises(ValueError):
            EvidenceCollector(RUN, **kwargs)


def test_coletor_descarta_duplicata_e_aceita_lacuna_marcando_perda() -> None:
    collector = _collector()
    collector.append(denial(RUN, 1))
    collector.append(denial(RUN, 1))  # duplicata
    collector.append(denial(RUN, 3, "toctou_recheck_failed"))  # lacuna: o 2 se perdeu
    snap = collector.snapshot()
    assert [d.sequence for d in snap.denials] == [1, 3] and snap.rejected == 1
    assert snap.inconsistent and snap.critical  # o que chegou e é do Run vale


def test_coletor_recusa_objeto_que_nao_e_negacao() -> None:
    collector = _collector()
    collector.append("path_denied")  # type: ignore[arg-type]
    assert collector.snapshot().inconsistent and collector.snapshot().denials == ()
    with pytest.raises(ValueError):
        EvidenceCollector(" ", task_id="task", invocation_id="inv")


def test_coletor_e_seguro_entre_threads_e_o_snapshot_e_imutavel() -> None:
    collector = _collector()
    barrier = threading.Barrier(8)

    def worker(index: int) -> None:
        barrier.wait()
        for _ in range(50):
            collector.append(denial(f"estranho-{index}", 1))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = collector.snapshot()
    assert snap.rejected == 400 and snap.denials == () and snap.inconsistent
    with pytest.raises(AttributeError):
        snap.denials = ()  # type: ignore[misc]


def test_o_input_da_porta_carrega_o_coletor_e_nao_o_expoe_no_repr() -> None:
    names = {f.name for f in fields(DeveloperInvocationInput)}
    assert "evidence" in names
    field = next(f for f in fields(DeveloperInvocationInput) if f.name == "evidence")
    assert field.repr is False and field.compare is False


# ---------------------------------------------- claim: o Run existente é deste componente?

CONTROL = "11111111-1111-4111-8111-111111111111"
INVOCATION = f"{CONTROL}:developer"


def _spec(**changes: Any) -> DeveloperRunSpec:
    base: dict[str, Any] = {
        "invocation_id": INVOCATION,
        "control_run_id": CONTROL,
        "context_manifest_id": "manifest-1",
        "identity": IDENT,
        "tool_profile_hash": tool_profile_hash(),
        "base_commit": "a" * 40,
        "worktree_path": "/wt",
        "expected_task_version": 3,
        "attempt_index": 0,
        "fix_round": 0,
    }
    return DeveloperRunSpec(**{**base, **changes})


def _existing(**changes: Any) -> ExistingRunFacts:
    base: dict[str, Any] = {
        "invocation_id": INVOCATION,
        "task_id": "task-1",
        "agent": "developer",
        "purpose": "execution",
        "attempt_index": 0,
        "fix_round": 0,
        "base_commit": "a" * 40,
        "context_manifest_id": "manifest-1",
        "subject_run_id": None,
        "supersedes_run_id": None,
        "provider": IDENT.provider,
        "provider_adapter": IDENT.adapter,
        "adapter_version": IDENT.adapter_version,
        "model": IDENT.model,
        "transport": IDENT.transport,
        "control_linked": True,
        "worktree_path": "/wt",  # E8.4.4: a worktree faz parte da identidade do claim
    }
    return ExistingRunFacts(**{**base, **changes})


def test_run_do_proprio_developer_e_um_claim_compativel() -> None:
    assert claim_compatible(_existing(), _spec(), "task-1") is True


@pytest.mark.parametrize(
    "changes",
    [
        {"invocation_id": "outro"},
        {"task_id": "task-2"},
        {"agent": "test_runner"},
        {"agent": "orchestrator"},
        {"agent": "auditor"},
        {"purpose": "workflow_audit"},
        {"purpose": "benchmark_evaluation"},
        {"attempt_index": 1},
        {"fix_round": 1},
        {"base_commit": "b" * 40},
        {"base_commit": None},
        {"context_manifest_id": "manifest-2"},
        {"context_manifest_id": None},
        {"subject_run_id": "x"},
        {"supersedes_run_id": "x"},
        {"provider": "outro"},
        {"provider_adapter": "outro"},
        {"adapter_version": "outra"},
        {"adapter_version": None},
        {"model": "outro"},
        {"model": None},
        {"transport": "process"},
        {"control_linked": False},
        {"worktree_path": "/outra"},
        {"worktree_path": None},
        {"worktree_path": ""},
    ],
)
def test_qualquer_divergencia_de_identidade_e_colisao(changes: dict[str, Any]) -> None:
    assert claim_compatible(_existing(**changes), _spec(), "task-1") is False


def test_claim_exige_invocation_id_derivado_do_run_de_controle() -> None:
    assert (
        claim_compatible(_existing(invocation_id="solto"), _spec(invocation_id="solto"), "task-1")
        is False
    )
    assert claim_compatible(_existing(), _spec(control_run_id="nao-e-uuid"), "task-1") is False
    other = "22222222-2222-4222-8222-222222222222"
    assert claim_compatible(_existing(), _spec(control_run_id=other), "task-1") is False


def test_recusa_e_colisao_nao_carregam_id_nem_status_de_run_algum() -> None:
    assert ClaimResult(ClaimOutcome.ALREADY, "r", RunStatus.RUNNING).run_id == "r"
    for outcome in (ClaimOutcome.REFUSED, ClaimOutcome.COLLISION):
        assert ClaimResult(outcome).run_id is None
        with pytest.raises(ValueError):
            ClaimResult(outcome, "run-alheio")
        with pytest.raises(ValueError):
            ClaimResult(outcome, None, RunStatus.RUNNING)


# ---------------------------------------------------------- vínculo ao binding aprovado


def test_binding_aprovado_e_expectativa_e_identidade_concordam() -> None:
    assert approved_binding_refusal(facts(), IDENT, expected()) is None


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"approved_adapter": None}, InvocationCode.BINDING_NOT_APPROVED),
        ({"approved_adapter_version": None}, InvocationCode.BINDING_NOT_APPROVED),
        ({"approved_model": None}, InvocationCode.BINDING_NOT_APPROVED),
        ({"approved_adapter": "outro"}, InvocationCode.BINDING_NOT_APPROVED),
        ({"approved_adapter_version": "9"}, InvocationCode.BINDING_NOT_APPROVED),
        ({"approved_model": "outro"}, InvocationCode.BINDING_NOT_APPROVED),
        ({"approved_tool_profile_hash": None}, InvocationCode.CAPABILITY_MISMATCH),
        ({"approved_tool_profile_hash": "0" * 64}, InvocationCode.CAPABILITY_MISMATCH),
    ],
)
def test_aprovado_incompleto_ou_divergente_e_recusado(
    changes: dict[str, Any], code: InvocationCode
) -> None:
    assert approved_binding_refusal(facts(**changes), IDENT, expected()) is code


@pytest.mark.parametrize("field", ["adapter_id", "adapter_version", "model", "transport"])
def test_expectativa_do_composition_root_diverge_do_aprovado(field: str) -> None:
    assert (
        approved_binding_refusal(facts(), IDENT, expected(**{field: "outro"}))
        is InvocationCode.BINDING_NOT_APPROVED
    )


@pytest.mark.parametrize("field", ["adapter", "adapter_version", "model"])
def test_identidade_do_provider_diverge_do_aprovado(field: str) -> None:
    assert (
        approved_binding_refusal(facts(), replace(IDENT, **{field: "outro"}), expected())
        is InvocationCode.BINDING_NOT_APPROVED
    )


def test_sem_expectativa_nao_ha_prova_possivel() -> None:
    assert approved_binding_refusal(facts(), IDENT, None) is InvocationCode.CAPABILITY_UNPROVEN


# ------------------------------------------------------------- validações dos dados neutros


def test_relatorio_recusa_dados_incoerentes() -> None:
    with pytest.raises(ValueError):
        make_report(IDENT, input_tokens=None, output_tokens=None, token_source="reported")
    with pytest.raises(ValueError):
        make_report(IDENT, token_source="unavailable")  # tokens presentes
    with pytest.raises(ValueError):
        make_report(IDENT, token_source="inventado")
    with pytest.raises(ValueError):
        make_report(IDENT, input_tokens=-1)
    with pytest.raises(ValueError):
        make_report(IDENT, files_read=("",))
    with pytest.raises(ValueError):
        DeveloperInvocationReport(status="ok", provider_called=True, identity=IDENT)  # type: ignore[arg-type]
    unavailable = make_report(
        IDENT, input_tokens=None, output_tokens=None, token_source="unavailable", files_read=None
    )
    assert unavailable.files_read is None


def test_negacao_so_aceita_categorias_do_journal() -> None:
    assert {"path_denied", "secret_access_blocked"} <= KNOWN_DENIAL_CATEGORIES
    assert CRITICAL_DENIAL_CATEGORIES <= KNOWN_DENIAL_CATEGORIES
    for category in KNOWN_DENIAL_CATEGORIES:
        assert ToolDenial(1, RUN, "task", "inv", "op", category, "r", "s", "m").category == category
    with pytest.raises(ValueError):
        denial(RUN, 1, "command_denied")  # não é categoria do executor
    with pytest.raises(ValueError):
        denial(RUN, 0)
    with pytest.raises(ValueError):
        denial(RUN, 1, subject_redacted=" ")


def test_identidade_exige_textos() -> None:
    with pytest.raises(ValueError):
        replace(IDENT, model="")


def test_resultado_completed_exige_tudo_limpo() -> None:
    ok: dict[str, Any] = {
        "status": InvocationStatus.COMPLETED,
        "code": InvocationCode.COMPLETED,
        "run_status": RunStatus.OK,
        "binding": BindingOutcome.RELEASED,
        "finalization": Finalization.CLOSED,
    }
    assert DeveloperInvocationResult(**ok).status is InvocationStatus.COMPLETED
    for bad in (
        {"code": InvocationCode.PROVIDER_ERROR},
        {"run_status": RunStatus.BLOCKED},
        {"integrity_violation": True},
        {"requires_finalization": True},
        {"finalization": Finalization.FAILED},
        {"finalization": Finalization.NOT_APPLICABLE},
        {"binding": BindingOutcome.RELEASE_FAILED},
        {"binding": BindingOutcome.NOT_BOUND},
    ):
        with pytest.raises(ValueError):
            DeveloperInvocationResult(**{**ok, **bad})


def test_resumos_sao_limitados_e_so_com_vocabulario_fechado() -> None:
    assert run_error_summary(InvocationCode.PROVIDER_ERROR) == "developer_invocation:provider_error"
    assert run_error_summary(InvocationCode.LIMIT_EXCEEDED, "x" * 5000).endswith("x" * 500)
    assert len(run_error_summary(InvocationCode.LIMIT_EXCEEDED, "x" * 5000)) < 600
    assert len(clip_summary("y" * 9000)) == 4000
    for code in InvocationCode:
        assert run_error_summary(code).startswith("developer_invocation:")
