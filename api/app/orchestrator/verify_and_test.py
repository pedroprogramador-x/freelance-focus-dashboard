"""Verificação do diff e execução dos testes (E8.4.4) — componente **interno, não operacional**.

Depois de uma invocação do Developer que fechou `ok` (E8.4.3), faz **uma** passagem: reivindica o
Run do Test Runner, verifica o diff (E8.3), mede as métricas, executa a `TestPolicy` aprovada e
fecha o Run com os fatos e `SafetyEvent`s. Nada aqui está ligado a `main.py`, startup, rota, fila ou
worker; a composição integrada é da E8.4.6, e `start_execution` segue no `NotImplementedError`.

## O que este módulo **não** faz

Não transiciona a task, não fecha o Run de controle, não grava as métricas de Git no Run de
controle, não tenta de novo, não classifica a tentativa (`classify_execution`) e não recupera
nada: reserva durável, teardown comprovado e recovery são da E8.4.5. O resultado devolve os fatos
que ela consome (`integrity`, `runner_outcome`, `metrics`, `requires_finalization`).

## Ordem (aprovada)

A. relê o banco (`read_verify_facts`): admissão coerente, fingerprint igual ao aprovado, e o Run do
   Developer **no banco** (identidade, `ok`, vínculo) — o `DeveloperInvocationResult` recebido
   não é fonte de verdade;
B. cancelamento e prazo; identidade do runner × `TestPolicy` aprovada;
C. **claim** (`open_runner_run`): `INSERT` `UNIQUE`, reconferência sob o lock, `COMMIT` — antes de
   consumir o snapshot da verificação;
D. binding do Run do Test Runner estabelecido e conferido;
E. **verificação** pós-Developer, **uma vez**; reprovada ou incompleta ⇒ o processo de teste não
   nasce (`NOT_RUN`), `SafetyEvent` associado ao Run reivindicado;
F. `VERIFIED` ⇒ métricas do diff (todas ou nenhuma); conteúdo diferente do verificado ⇒ nada roda;
G. última fronteira: cancelamento, prazo e binding ainda válidos ⇒ `RunnerPort.run` — a **única**
   chamada do `invocation_id`;
H. decisão pura, fechamento do Run com os eventos (idempotente, 2 tentativas) e liberação (fim
   normal) ou revogação do binding.

Nada se repete: a verificação consome um snapshot de uso único e o processo de teste roda no máximo
uma vez por `invocation_id` (claim persistente e atômico, inclusive entre processos). Um claim
incerto é resolvido por consulta de identidade completa, nunca por nova execução.

## Limites declarados

* O Test Runner não é sandbox ([04] §6): uma escrita fora da worktree **durante** os testes não é
  vista, pois o snapshot da árvore principal é verificado antes deles (decisão D1 da E8.4.4).
* O prazo é monotônico e único para a passagem; a `TestPolicy.timeout_seconds` continua sendo
  imposta pelo próprio runner. Cancelamento em tempo real depende do sinal injetado (E8.4.6).
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.enums import RunStatus
from app.orchestrator import execution_manager as em
from app.orchestrator.developer_binding import DeveloperBindingResolver
from app.orchestrator.developer_invocation import ComponentBindingPort
from app.orchestrator.developer_invocation_contract import (
    BindingOutcome,
    ClaimOutcome,
    CloseOutcome,
    DeveloperInvocationResult,
    Finalization,
    InvocationStatus,
)
from app.orchestrator.execution_contract import (
    COMPONENT_TEST_RUNNER,
    IntegrityVerdict,
    RunnerOutcome,
    component_invocation_id,
    operation_timeout_s,
    task_deadline_remaining_s,
)
from app.orchestrator.execution_workspace import ComponentBindingRefused, PreparedWorkspace
from app.orchestrator.verify_and_test_contract import (
    AttemptSignal,
    MeasureInput,
    MetricsReport,
    RunnerClosure,
    RunnerInput,
    RunnerPort,
    RunnerReport,
    RunnerRunSpec,
    VerificationInput,
    VerificationPort,
    VerificationReport,
    VerificationStatus,
    VerifyAndTestResult,
    VerifyCode,
    VerifyDecision,
    VerifyEvent,
    VerifyFacts,
    VerifyStatus,
    decide_after_runner,
    decide_changed_since_verified,
    decide_measurement_unproven,
    decide_not_verified,
    decide_stop_after_verification,
    decide_verification_failure,
    integrity_of,
    run_error_summary,
    runner_events,
    summary_of_report,
    verification_events,
)
from app.orchestrator.workspace_contract import PreparationCode

#: Fechamento: duas tentativas (idempotente). O processo de teste nunca é repetido.
_CLOSE_ATTEMPTS = 2


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _refused(code: VerifyCode, *, fact_code: PreparationCode | None = None) -> VerifyAndTestResult:
    return VerifyAndTestResult(VerifyStatus.REFUSED, code, fact_code=fact_code)


def _stopped(is_cancelled: Callable[[], bool]) -> bool:
    """O sinal de cancelamento. Um sinal que levanta, ou não devolve exatamente `False`, cancela."""
    try:
        return is_cancelled() is not False
    except Exception:
        return True


def _developer_reported_ok(result: object) -> bool:
    """O resultado recebido só **sugere** que o Developer terminou bem; a verdade é o banco. Esta
    checagem barata evita abrir um Run quando o chamador já sabe que não há o que testar."""
    return (
        isinstance(result, DeveloperInvocationResult)
        and result.status is InvocationStatus.COMPLETED
        and result.run_status is RunStatus.OK
        and result.run_id is not None
        and not result.integrity_violation
        and not result.requires_finalization
    )


@dataclass
class _Pass:
    """O que a passagem observou até agora. Mutável e **local** a uma chamada: existe para que uma
    saída anormal (defeito, interrupção) feche o Run com o que de fato aconteceu — em especial, se o
    processo de teste chegou a ser chamado — e não com o que o fluxo feliz teria devolvido."""

    verification: VerificationReport | None = None
    metrics: MetricsReport | None = None
    events: tuple[VerifyEvent, ...] = ()
    runner_report: RunnerReport | None = None
    process_started: bool = False

    def abnormal(self, status: RunStatus, code: VerifyCode, *, interrupted: bool) -> VerifyDecision:
        """O Run de uma saída anormal. A integridade é a da verificação, se ela chegou a concluir;
        com o processo chamado, a árvore não tem prova de encerramento ⇒ pendente."""
        integrity = (
            IntegrityVerdict.UNVERIFIABLE
            if self.verification is None
            else integrity_of(self.verification.status)
        )
        if self.process_started:
            outcome = RunnerOutcome.INTERRUPTED if interrupted else RunnerOutcome.TECHNICAL_FAILURE
        else:
            outcome = RunnerOutcome.NOT_RUN
        return VerifyDecision(
            status, code, integrity, outcome, requires_finalization=self.process_started
        )


class VerifyAndTestService:
    """Uma passagem de verificação + testes por tentativa. Sem estado mutável: tudo o que importa
    está no banco. As portas chegam por injeção (composition root); o Orchestrator não importa
    `agent_runtime` nem `execution_verification`."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        workspaces: ComponentBindingPort,
        verification: VerificationPort,
        runner: RunnerPort,
        developer_binding_resolver: DeveloperBindingResolver | None,
        now: Callable[[], datetime] = _utcnow,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._workspaces = workspaces
        self._verification = verification
        self._runner = runner
        self._resolver = developer_binding_resolver
        self._now = now
        self._clock = clock

    def __repr__(self) -> str:
        return "<VerifyAndTestService>"

    # -------------------------------------------------------------------------- entrada

    def execute(
        self,
        context: object,
        developer: object,
        *,
        is_cancelled: Callable[[], bool],
    ) -> VerifyAndTestResult:
        """Faz **uma** passagem. Nunca levanta por estado: devolve resultado estruturado.

        Só `KeyboardInterrupt`/`SystemExit` atravessam — depois de o Run ser fechado `interrupted`
        e o binding ser revogado."""
        if not isinstance(context, PreparedWorkspace):
            return _refused(VerifyCode.INTERNAL_ERROR)
        if not _developer_reported_ok(developer):
            return _refused(VerifyCode.DEVELOPER_NOT_COMPLETED)
        assert isinstance(developer, DeveloperInvocationResult) and developer.run_id is not None
        invocation_id = component_invocation_id(context.control_run_id, COMPONENT_TEST_RUNNER)

        # A. fatos relidos do banco, inclusive o Run do Developer.
        try:
            facts = self._facts(context, developer.run_id)
        except Exception:
            return _refused(VerifyCode.INTERNAL_ERROR)
        if facts.code is not None:
            return _refused(VerifyCode.REFUSED_FACTS, fact_code=facts.code)
        incoherence = self._context_incoherence(facts, context)
        if incoherence is not None:
            return _refused(VerifyCode.REFUSED_FACTS, fact_code=incoherence)
        assert facts.run_started_at is not None and facts.task_timeout_s is not None
        assert facts.run_timeout_s is not None and facts.test_policy is not None
        assert facts.context_manifest_id is not None and facts.developer_run_id is not None

        # B. cancelamento, prazo e identidade do runner × política aprovada.
        if _stopped(is_cancelled):
            return _refused(VerifyCode.CANCELLED_BEFORE_START)
        budget_s = self._passage_budget_s(facts)
        if (
            math.floor(
                operation_timeout_s(
                    run_timeout_s=facts.run_timeout_s,
                    remaining_task_s=budget_s,
                    policy_timeout_s=facts.test_policy.timeout_seconds,
                )
            )
            < 1
        ):
            return _refused(VerifyCode.DEADLINE_EXHAUSTED)
        try:
            identity = self._runner.identity()
        except Exception:
            return _refused(VerifyCode.INTERNAL_ERROR)
        if identity.runner_id != facts.test_policy.runner_id:
            return _refused(VerifyCode.RUNNER_UNSUPPORTED)

        # O prazo monotônico da passagem é FIXADO aqui, antes do claim: claim, binding, verificação
        # e métricas gastam do mesmo orçamento; nenhuma etapa reconstrói um prazo cheio.
        signal = AttemptSignal(
            is_cancelled, deadline_at=self._clock() + max(0.0, budget_s), clock=self._clock
        )

        # C. reivindicação atômica e durável — ANTES de consumir o snapshot da verificação.
        started = self._now()
        spec = RunnerRunSpec(
            invocation_id=invocation_id,
            control_run_id=context.control_run_id,
            developer_run_id=facts.developer_run_id,
            context_manifest_id=facts.context_manifest_id,
            identity=identity,
            base_commit=context.base_commit,
            worktree_path=context.worktree_path,
            expected_task_version=context.task_version,
            attempt_index=context.attempt_index,
            fix_round=context.fix_round,
            started_at=started,
        )
        try:
            with self._session_factory() as session:
                claim = em.open_runner_run(session, context.task_id, spec=spec)
        except Exception:
            return self._claim_unknown(context.task_id, spec)
        if claim.outcome is ClaimOutcome.COLLISION:
            return _refused(VerifyCode.CLAIM_COLLISION)  # Run de OUTRO componente: não adotado
        if claim.outcome is ClaimOutcome.ALREADY:
            return self._already(claim.run_id, claim.existing_status)
        if claim.outcome is ClaimOutcome.REFUSED or claim.run_id is None:
            return _refused(VerifyCode.CLAIM_REFUSED, fact_code=claim.code)

        return self._run_claimed(context, facts, claim.run_id, invocation_id, started, signal)

    # ------------------------------------------------------------------------- D–H

    def _run_claimed(
        self,
        context: PreparedWorkspace[Any],
        facts: VerifyFacts,
        run_id: str,
        invocation_id: str,
        started: datetime,
        signal: AttemptSignal,
    ) -> VerifyAndTestResult:
        assert facts.test_policy is not None
        token: object | None = None
        binding_refused: bool | None = None  # None = não recusado; bool = revogação provada?
        state = _Pass()
        interrupt: BaseException | None = None
        try:
            try:
                token = self._workspaces.bind_component_run(
                    context, run_id=run_id, is_cancelled=signal
                )
            except ComponentBindingRefused as refused:
                binding_refused = refused.binding_revoked
                decision = decide_not_verified(self._binding_code(refused.code), signal.cause)
            except Exception:
                binding_refused = False
                decision = decide_not_verified(VerifyCode.BINDING_REFUSED, signal.cause)
            else:
                if not self._binding_verified(context, run_id, invocation_id):
                    decision = decide_not_verified(VerifyCode.BINDING_REVOKED, signal.cause)
                else:
                    decision = self._verify_and_run(
                        context, facts, run_id, invocation_id, signal, state
                    )
        except Exception:  # defeito inesperado: o Run fecha `error`, nada vaza e nada repete
            decision = state.abnormal(RunStatus.ERROR, VerifyCode.INTERNAL_ERROR, interrupted=False)
        except BaseException as exc:  # KeyboardInterrupt/SystemExit: limpa e relança
            interrupt = exc
            decision = state.abnormal(
                RunStatus.INTERRUPTED, VerifyCode.INTERRUPTED, interrupted=True
            )

        binding = self._end_binding(token, revoke=not decision.normal_end, refused=binding_refused)
        result = self._close(context, run_id, started, decision, state, binding)
        if interrupt is not None:
            raise interrupt
        return result

    def _verify_and_run(
        self,
        context: PreparedWorkspace[Any],
        facts: VerifyFacts,
        run_id: str,
        invocation_id: str,
        signal: AttemptSignal,
        state: _Pass,
    ) -> VerifyDecision:
        assert facts.test_policy is not None
        # E. verificação — uma única vez, depois do claim durável.
        try:
            state.verification = self._verification.verify(
                VerificationInput(
                    workspace_id=context.workspace_id,
                    workspace_path=context.workspace_path,
                    workspace_prefix=context.workspace_prefix,
                    root_identity=context.workspace_root_identity,
                    base_commit=context.base_commit,
                    snapshot=context.main_snapshot,
                    is_cancelled=signal,
                )
            )
        except Exception:
            state.verification = None
        report = state.verification
        if report is None or report.status is not VerificationStatus.VERIFIED:
            state.events = verification_events(report, signal.cause)
            return decide_verification_failure(report, signal.cause)

        # F. métricas do diff — somente do que foi verificado; todas ou nenhuma.
        try:
            state.metrics = self._verification.measure(
                MeasureInput(
                    handle=report.handle,
                    workspace_path=context.workspace_path,
                    workspace_prefix=context.workspace_prefix,
                    base_commit=context.base_commit,
                    is_cancelled=signal,
                )
            )
        except Exception:
            state.metrics = MetricsReport(failure="measure_failed")
        metrics = state.metrics
        if metrics.changed_since_verified:
            return decide_changed_since_verified()
        if metrics.blocks:
            # Sem métricas E sem a integridade revalidada (orçamento, cancelamento, leitura que não
            # prova): o verificado não está mais comprovado. "NULL" só vale com integridade provada.
            if metrics.failure == "cancelled":
                signal()  # a medição foi cancelada por quem: registra a causa agora
            return decide_measurement_unproven(metrics.failure, signal.cause)

        # G. última fronteira: cancelamento, prazo e binding ainda válidos.
        if signal():
            return decide_stop_after_verification(VerifyCode.CANCELLED, signal.cause)
        if signal.remaining_s() < 1:
            return decide_stop_after_verification(VerifyCode.DEADLINE_EXHAUSTED, None)
        if not self._binding_verified(context, run_id, invocation_id):
            return decide_stop_after_verification(VerifyCode.BINDING_REVOKED, None)

        # G'. a única chamada ao runner deste invocation_id. O estado registra que o processo foi
        # chamado ANTES: qualquer saída anormal daqui em diante deixa a árvore sem prova.
        state.process_started = True
        try:
            state.runner_report = self._runner.run(
                RunnerInput(
                    task_id=context.task_id,
                    run_id=run_id,
                    invocation_id=invocation_id,
                    workspace_id=context.workspace_id,
                    base_commit=context.base_commit,
                    test_policy=facts.test_policy,
                    is_cancelled=signal,
                )
            )
        except Exception:
            state.runner_report = None
        decision = decide_after_runner(
            state.runner_report,
            cause=signal.cause,
            binding_verified=self._binding_verified(context, run_id, invocation_id),
        )
        state.events = runner_events(decision)
        return decision

    @staticmethod
    def _binding_code(code: PreparationCode) -> VerifyCode:
        if code is PreparationCode.CANCEL_REQUESTED:
            return VerifyCode.CANCELLED_BEFORE_START
        if code is PreparationCode.DEADLINE_EXCEEDED:
            return VerifyCode.DEADLINE_EXHAUSTED
        return VerifyCode.BINDING_REFUSED

    def _binding_verified(
        self, context: PreparedWorkspace[Any], run_id: str, invocation_id: str
    ) -> bool:
        try:
            return bool(
                self._workspaces.verify_component_binding(
                    context, run_id=run_id, invocation_id=invocation_id
                )
            )
        except Exception:
            return False

    def _end_binding(
        self, token: object | None, *, revoke: bool, refused: bool | None
    ) -> BindingOutcome:
        """Libera (fim normal: `ok` ou testes que rodaram e falharam) ou revoga (qualquer outro
        desfecho) o binding **deste** Run."""
        if token is None:
            if refused is None:
                return BindingOutcome.NOT_BOUND
            return BindingOutcome.REFUSED if refused else BindingOutcome.RELEASE_FAILED
        revoked = False
        if revoke:
            revoked = self._attempt(self._workspaces.revoke_binding, token)
        released = self._attempt(self._workspaces.release_binding, token)
        if revoke:
            return BindingOutcome.REVOKED if revoked else BindingOutcome.RELEASE_FAILED
        if released:
            return BindingOutcome.RELEASED
        if self._attempt(self._workspaces.revoke_binding, token):
            return BindingOutcome.REVOKED
        return BindingOutcome.RELEASE_FAILED

    @staticmethod
    def _attempt(operation: Callable[[object], bool], token: object) -> bool:
        try:
            return operation(token) is True
        except Exception:
            return False

    # ---------------------------------------------------------------------------- H

    def _close(
        self,
        context: PreparedWorkspace[Any],
        run_id: str,
        started: datetime,
        decision: VerifyDecision,
        state: _Pass,
        binding: BindingOutcome,
    ) -> VerifyAndTestResult:
        verification, metrics, events = state.verification, state.metrics, state.events
        runner_report, process_started = state.runner_report, state.process_started
        finished = self._now()
        duration_ms = max(0, int((finished - started).total_seconds() * 1000))
        exited = runner_report is not None and runner_report.exit_code is not None
        closure = RunnerClosure(
            status=decision.run_status,
            finished_at=finished,
            duration_ms=duration_ms,
            test_summary=summary_of_report(runner_report) if runner_report is not None else None,
            summary=(
                f"testes terminaram com exit_code {runner_report.exit_code}"
                if runner_report is not None and exited
                else None
            ),
            error_summary=(
                None if decision.run_status is RunStatus.OK else run_error_summary(decision.code)
            ),
            events=events,
        )

        close = None
        for _ in range(_CLOSE_ATTEMPTS):
            try:
                with self._session_factory() as session:
                    close = em.close_runner_run(
                        session, context.task_id, run_id=run_id, closure=closure
                    )
                break
            except Exception:
                close = None

        def result(
            status: VerifyStatus,
            *,
            run_status: RunStatus | None,
            finalization: Finalization,
            safety_events: int,
            pending: bool,
        ) -> VerifyAndTestResult:
            return VerifyAndTestResult(
                status,
                decision.code,
                run_id=run_id,
                run_status=run_status,
                process_started=process_started,
                verification=verification.status if verification is not None else None,
                integrity=decision.integrity,
                runner_outcome=decision.runner_outcome,
                metrics=metrics,
                exit_code=runner_report.exit_code if runner_report is not None else None,
                binding=binding,
                finalization=finalization,
                safety_events=safety_events,
                requires_finalization=pending,
            )

        if close is None:
            return result(
                VerifyStatus.UNFINALIZED,
                run_status=None,
                finalization=Finalization.FAILED,
                safety_events=0,
                pending=True,
            )
        run_status: RunStatus | None
        if close.outcome is CloseOutcome.CLOSED:
            finalization, run_status, written = (
                Finalization.CLOSED,
                decision.run_status,
                close.safety_events,
            )
        else:
            finalization, run_status, written = (
                Finalization.ALREADY_FINAL,
                close.existing_status,
                0,
            )
        pending = (
            decision.requires_finalization
            or binding is BindingOutcome.RELEASE_FAILED
            or run_status is RunStatus.RUNNING
        )
        completed = (
            run_status is RunStatus.OK
            and decision.run_status is RunStatus.OK
            and binding in (BindingOutcome.RELEASED, BindingOutcome.REVOKED)
            and not pending
        )
        status = (
            VerifyStatus.COMPLETED
            if completed
            else VerifyStatus.UNFINALIZED
            if pending
            else VerifyStatus.FAILED
        )
        return result(
            status,
            run_status=run_status,
            finalization=finalization,
            safety_events=written,
            pending=pending,
        )

    # --------------------------------------------------------------------- apoio e leitura

    def _facts(self, context: PreparedWorkspace[Any], developer_run_id: str) -> VerifyFacts:
        with self._session_factory() as session:
            facts = em.read_verify_facts(
                session,
                context.task_id,
                control_run_id=context.control_run_id,
                developer_run_id=developer_run_id,
                developer_binding_resolver=self._resolver,
            )
            session.rollback()
        return facts

    @staticmethod
    def _context_incoherence(
        facts: VerifyFacts, context: PreparedWorkspace[Any]
    ) -> PreparationCode | None:
        """O contexto preparado em memória é a tentativa que o banco ainda publica?"""
        if (
            facts.workspace_id != context.workspace_id
            or facts.base_commit != context.base_commit
            or facts.attempt_index != context.attempt_index
            or facts.fix_round != context.fix_round
        ):
            return PreparationCode.RUN_MISMATCH
        if facts.task_version != context.task_version or facts.control_worktree_path is None:
            return PreparationCode.TASK_VERSION_CHANGED
        if not _same_path(facts.control_worktree_path, context.worktree_path):
            return PreparationCode.FINAL_CHECK_FAILED
        if None in (
            facts.run_started_at,
            facts.task_timeout_s,
            facts.run_timeout_s,
            facts.context_manifest_id,
            facts.developer_run_id,
            facts.test_policy,
        ):
            return PreparationCode.PLAN_INCOHERENT
        return None

    def _passage_budget_s(self, facts: VerifyFacts) -> float:
        """Segundos que a passagem inteira pode usar: o menor entre `run_timeout_s` e o que resta
        da task (antes da `TestPolicy`, que o runner impõe por conta própria)."""
        assert facts.run_started_at is not None and facts.task_timeout_s is not None
        assert facts.run_timeout_s is not None
        remaining = task_deadline_remaining_s(
            facts.run_started_at, facts.task_timeout_s, now=self._now()
        )
        return operation_timeout_s(run_timeout_s=facts.run_timeout_s, remaining_task_s=remaining)

    def _claim_unknown(self, task_id: str, spec: RunnerRunSpec) -> VerifyAndTestResult:
        """A reivindicação levantou: o `COMMIT` pode ter acontecido. Nunca verifica nem testa.

        A consulta aplica a MESMA verificação de identidade da reivindicação: um Run de outro
        componente com o mesmo `invocation_id` é colisão, nunca adoção."""
        try:
            with self._session_factory() as session:
                found = em.find_runner_claim(session, task_id, spec=spec)
        except Exception:
            return _refused(VerifyCode.CLAIM_FAILED)
        if found is None:
            return _refused(VerifyCode.CLAIM_FAILED)
        if found.outcome is ClaimOutcome.COLLISION:
            return _refused(VerifyCode.CLAIM_COLLISION)
        if found.outcome is not ClaimOutcome.ALREADY:
            return _refused(VerifyCode.CLAIM_FAILED)
        return self._already(found.run_id, found.existing_status)

    @staticmethod
    def _already(run_id: str | None, status: RunStatus | None) -> VerifyAndTestResult:
        return VerifyAndTestResult(
            VerifyStatus.ALREADY_CLAIMED,
            VerifyCode.ALREADY_CLAIMED,
            run_id=run_id,
            run_status=status,
            requires_finalization=status is RunStatus.RUNNING,
        )


__all__ = ["VerifyAndTestService"]
