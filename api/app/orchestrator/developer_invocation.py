"""Invocação do Developer (E8.4.3) — componente **interno, não operacional**.

Depois de uma preparação (`PreparedWorkspace`, E8.4.2), faz **uma** invocação do Developer: abre
o Run do Developer, registra o binding dele, entrega a entrada neutra à porta
(`DeveloperRunPort`, implementada no composition root), traduz o relatório em fechamento do Run e
em `SafetyEvent`, e libera ou revoga o binding. Nada aqui está ligado a `main.py`, startup, rota,
fila ou worker; a composição integrada é da E8.4.6, e `start_execution` segue no
`NotImplementedError`.

## O que este módulo **não** faz

Não transiciona a task, não fecha o Run de controle, não roda testes, não verifica o diff, não
tenta de novo. A classificação agregada, o teardown comprovado e a recuperação durável são da
E8.4.4/E8.4.5. O resultado devolve os fatos estruturados que elas consomem
(`developer_outcome`, `integrity_violation`, `requires_finalization`).

## Ordem

A. relê o banco (`read_invocation_facts`): admissão coerente, fingerprint recalculado igual ao
   aprovado, objetivo/critérios/manifest/limites **aprovados**;
B. cancelamento e prazo da tentativa;
C. identidade do adaptador × `developer_binding` aprovado × expectativa do composition root ×
   `tool_profile_hash` aprovado; **prova de capability** reavaliada (a mesma guarda da admissão);
D. bytes do artefato renderizado conferidos contra o hash do manifest;
E. **reivindicação** (`open_developer_run`): `INSERT` do Run, `UNIQUE(invocation_id)`, reconferência
   sob o lock de escrita, `COMMIT` — antes de qualquer chamada ao provider;
F. binding do Run do Developer (`bind_component_run`);
G. último cancelamento/prazo; chamada à porta (a **única** do `invocation_id`);
H. binding ainda autorizado? decisão pura (`decide_run`); liberação (ou revogação, se não-ok);
I. fechamento do Run + `SafetyEvent` numa transação (`close_developer_run`).

## Falha de encerramento

O fechamento é idempotente (condicionado a `running`) e tentado duas vezes. Se ambas falham, o
resultado é `UNFINALIZED`, com a **causa original preservada** em ``code`` e
``finalization = FAILED``: o Run pode seguir `running`. Nada é repetido — nem o provider, nem a
reivindicação. Um Run que ficou `running` (crash, falha de fechamento) é lido por qualquer
nova tentativa como `ALREADY_CLAIMED` e **não** reexecuta.

## Limites declarados

* A exclusão entre processos é a do banco (`UNIQUE` + índice parcial do Run de controle); não há
  lock em memória a que se confie.
* A revogação nega **resoluções futuras**; um `ResolvedWorkspace` já entregue ao executor antes
  dela não é retomado (E842-AUD-002). Por isso o binding é conferido ao fim e um Run cuja
  autorização não se prova não fecha `ok`.
* Cancelamento persistido é conferido nas fronteiras e na reivindicação; o sinal injetado é a via
  em tempo real (a E8.4.6 liga os dois).
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.context_engine import read_rendered_artifact
from app.db.enums import RunStatus, SafetyEventKind, TokenSource
from app.orchestrator import execution_manager as em
from app.orchestrator.developer_binding import DeveloperBindingResolver
from app.orchestrator.developer_invocation_contract import (
    BindingOutcome,
    ClaimOutcome,
    CloseOutcome,
    DeveloperInvocationInput,
    DeveloperInvocationReport,
    DeveloperInvocationResult,
    DeveloperRunPort,
    DeveloperRunSpec,
    EvidenceCollector,
    EvidenceSnapshot,
    Finalization,
    InvocationCode,
    InvocationFacts,
    InvocationStatus,
    ProviderIdentity,
    RunClosure,
    RunDecision,
    approved_binding_refusal,
    clip_summary,
    decide_run,
    escalate_for_evidence,
    run_error_summary,
)
from app.orchestrator.errors import TransitionGuardFailed
from app.orchestrator.execution_contract import (
    COMPONENT_DEVELOPER,
    DeveloperOutcome,
    component_invocation_id,
    operation_timeout_s,
    task_deadline_remaining_s,
)
from app.orchestrator.execution_workspace import ComponentBindingRefused, PreparedWorkspace
from app.orchestrator.state_machine import CapabilityProver, check_entry_capability
from app.orchestrator.workspace_contract import PreparationCode
from app.safety.capability_verification import CapabilityBinding

_SHA256 = re.compile(r"[0-9a-f]{64}")
#: Teto do artefato renderizado lido para a memória (o orçamento de contexto é muito menor).
_MAX_CONTEXT_BYTES = 8 * 1024 * 1024
#: Fechamento: duas tentativas (idempotente). O provider nunca é repetido.
_CLOSE_ATTEMPTS = 2
_RULE_PREFIX = "orchestrator.developer_invocation."


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


class ComponentBindingPort(Protocol):
    """O que a invocação usa do serviço de preparação (E8.4.2), por estrutura."""

    def bind_component_run(
        self, context: Any, *, run_id: str, is_cancelled: Callable[[], bool]
    ) -> object: ...

    def verify_component_binding(
        self, context: Any, *, run_id: str, invocation_id: str
    ) -> bool: ...

    def revoke_binding(self, token: object) -> bool: ...

    def release_binding(self, token: object) -> bool: ...


def _refused(
    code: InvocationCode, *, fact_code: PreparationCode | None = None
) -> DeveloperInvocationResult:
    return DeveloperInvocationResult(InvocationStatus.REFUSED, code, fact_code=fact_code)


def _stopped(is_cancelled: Callable[[], bool]) -> bool:
    """O sinal de cancelamento. Um sinal que levanta conta como cancelado (fail closed)."""
    try:
        return bool(is_cancelled())
    except Exception:
        return True


_CANCELLED = RunDecision(
    RunStatus.CANCELLED,
    InvocationCode.PROVIDER_CANCELLED,
    DeveloperOutcome.CANCELLED,
    critical=False,
    limit_exceeded=False,
)
_DEADLINE = RunDecision(
    RunStatus.TIMEOUT,
    InvocationCode.DEADLINE_EXHAUSTED,
    DeveloperOutcome.TIMEOUT,
    critical=False,
    limit_exceeded=False,
)
_BINDING_REFUSED = RunDecision(
    RunStatus.ERROR,
    InvocationCode.BINDING_REFUSED,
    DeveloperOutcome.INTERNAL_ERROR,
    critical=False,
    limit_exceeded=False,
)
_PROVIDER_EXCEPTION = RunDecision(
    RunStatus.ERROR,
    InvocationCode.PROVIDER_EXCEPTION,
    DeveloperOutcome.PROVIDER_ERROR,
    critical=False,
    limit_exceeded=False,
)
_INTERNAL = RunDecision(
    RunStatus.ERROR,
    InvocationCode.INTERNAL_ERROR,
    DeveloperOutcome.INTERNAL_ERROR,
    critical=False,
    limit_exceeded=False,
)
_INTERRUPTED = RunDecision(
    RunStatus.INTERRUPTED,
    InvocationCode.INTERRUPTED,
    DeveloperOutcome.INTERRUPTED,
    critical=False,
    limit_exceeded=False,
)


class DeveloperInvocationService:
    """Uma invocação do Developer por tentativa. Sem estado mutável: tudo o que importa está no
    banco. As portas chegam por injeção (composition root); o Orchestrator não importa
    `agent_runtime` nem `tool_executor`."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        workspaces: ComponentBindingPort,
        runtime: DeveloperRunPort,
        prover: CapabilityProver | None,
        expected_capability_binding: CapabilityBinding | None,
        artifacts_dir: Path,
        developer_binding_resolver: DeveloperBindingResolver | None,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._workspaces = workspaces
        self._runtime = runtime
        self._prover = prover
        self._expected = expected_capability_binding
        self._artifacts_dir = artifacts_dir
        self._resolver = developer_binding_resolver
        self._now = now

    def __repr__(self) -> str:
        return "<DeveloperInvocationService>"

    # -------------------------------------------------------------------------- entrada

    def invoke(
        self, context: object, *, is_cancelled: Callable[[], bool]
    ) -> DeveloperInvocationResult:
        """Faz **uma** invocação. Nunca levanta por estado: devolve resultado estruturado.

        Só `KeyboardInterrupt`/`SystemExit` atravessam — depois de o Run ser fechado como
        `interrupted` e o binding ser liberado ou revogado."""
        if not isinstance(context, PreparedWorkspace):
            return _refused(InvocationCode.INTERNAL_ERROR)
        invocation_id = component_invocation_id(context.control_run_id, COMPONENT_DEVELOPER)

        # A. fatos relidos do banco.
        try:
            facts = self._facts(context)
        except Exception:
            return _refused(InvocationCode.INTERNAL_ERROR)
        if facts.code is not None:
            return _refused(InvocationCode.REFUSED_FACTS, fact_code=facts.code)
        incoherence = self._context_incoherence(facts, context)
        if incoherence is not None:
            return _refused(InvocationCode.REFUSED_FACTS, fact_code=incoherence)
        assert facts.run_started_at is not None and facts.task_timeout_s is not None
        assert facts.run_timeout_s is not None and facts.max_total_tokens is not None
        assert facts.goal is not None and facts.context_manifest_id is not None

        # B. cancelamento e prazo.
        if _stopped(is_cancelled):
            return _refused(InvocationCode.CANCELLED_BEFORE_START)
        timeout_s = self._timeout_s(facts)
        if timeout_s < 1:
            return _refused(InvocationCode.DEADLINE_EXHAUSTED)

        # C. identidade × aprovado × expectativa do composition root; prova de capability.
        try:
            identity = self._runtime.identity()
        except Exception:
            return _refused(InvocationCode.INTERNAL_ERROR)
        refusal = approved_binding_refusal(facts, identity, self._expected)
        if refusal is not None:
            self._record_refusal(context, refusal)
            return _refused(refusal)
        try:
            check_entry_capability(self._prover, self._expected)
        except TransitionGuardFailed as exc:
            code = (
                InvocationCode.CAPABILITY_UNPROVEN
                if exc.guard == "capability_profile_proven"
                else InvocationCode.CAPABILITY_MISMATCH
            )
            self._record_refusal(context, code)
            return _refused(code)
        except Exception:
            return _refused(InvocationCode.INTERNAL_ERROR)  # falha técnica do provador: fail closed

        # D. o contexto que o provider vai ler é o aprovado (bytes × hash do manifest).
        rendered = self._read_context(facts.rendered_context_hash)
        if rendered is None or facts.rendered_context_hash is None:
            return _refused(InvocationCode.CONTEXT_ARTIFACT_INVALID)

        # E. reivindicação atômica e durável — antes de qualquer chamada ao provider.
        started = self._now()
        spec = DeveloperRunSpec(
            invocation_id=invocation_id,
            control_run_id=context.control_run_id,
            context_manifest_id=facts.context_manifest_id,
            identity=identity,
            tool_profile_hash=facts.approved_tool_profile_hash or "",
            base_commit=context.base_commit,
            worktree_path=context.worktree_path,
            expected_task_version=context.task_version,
            attempt_index=context.attempt_index,
            fix_round=context.fix_round,
            started_at=started,
        )
        try:
            with self._session_factory() as session:
                claim = em.open_developer_run(session, context.task_id, spec=spec)
        except Exception:
            return self._claim_unknown(context.task_id, spec)
        if claim.outcome is ClaimOutcome.COLLISION:
            return _refused(InvocationCode.CLAIM_COLLISION)  # Run de OUTRO componente: não adotado
        if claim.outcome is ClaimOutcome.ALREADY:
            return self._already(claim.run_id, claim.existing_status)
        if claim.outcome is ClaimOutcome.REFUSED or claim.run_id is None:
            return _refused(InvocationCode.CLAIM_REFUSED, fact_code=claim.code)

        request = DeveloperInvocationInput(
            task_id=context.task_id,
            run_id=claim.run_id,
            invocation_id=invocation_id,
            control_run_id=context.control_run_id,
            workspace_id=context.workspace_id,
            base_commit=context.base_commit,
            context_manifest_id=facts.context_manifest_id,
            goal=facts.goal,
            acceptance_criteria=facts.acceptance_criteria,
            plan_steps=(),  # o plano aprovado não tem passos ([E8.4.3] D3): nenhum é inventado
            rendered_context=rendered,
            rendered_context_hash=facts.rendered_context_hash,
            evidence=EvidenceCollector(
                claim.run_id, task_id=context.task_id, invocation_id=invocation_id
            ),
            max_total_tokens=facts.max_total_tokens,
            timeout_s=timeout_s,
            is_cancelled=is_cancelled,
        )
        return self._run_claimed(context, facts, request, identity, started, is_cancelled)

    # --------------------------------------------------------------------------- F–I

    def _run_claimed(
        self,
        context: PreparedWorkspace[Any],
        facts: InvocationFacts,
        request: DeveloperInvocationInput,
        identity: ProviderIdentity,
        started: datetime,
        is_cancelled: Callable[[], bool],
    ) -> DeveloperInvocationResult:
        run_id = request.run_id
        token: object | None = None
        report: DeveloperInvocationReport | None = None
        attempted = False
        binding_refused: bool | None = None  # None = não recusado; bool = revogação provada?
        interrupt: BaseException | None = None
        try:
            try:
                token = self._workspaces.bind_component_run(
                    context, run_id=run_id, is_cancelled=is_cancelled
                )
            except ComponentBindingRefused as refused:
                binding_refused = refused.binding_revoked
                decision = self._refused_binding_decision(refused.code)
            except Exception:
                binding_refused = False
                decision = _BINDING_REFUSED
            else:
                stop, timeout_s = self._last_gate(facts, is_cancelled)
                if stop is not None:
                    decision = stop
                else:
                    attempted = True
                    try:
                        report = self._runtime.invoke(_with_timeout(request, timeout_s))
                    except Exception:
                        decision = _PROVIDER_EXCEPTION
                    else:
                        decision = decide_run(
                            report,
                            request.evidence.snapshot(),
                            identity=identity,
                            binding_verified=self._binding_verified(context, request),
                        )
        except Exception:  # defeito inesperado: o Run fecha `error`, nada vaza e nada repete
            decision = _INTERNAL
        except BaseException as exc:  # KeyboardInterrupt/SystemExit: limpa e relança
            interrupt = exc
            decision = _INTERRUPTED

        # A evidência do executor vence qualquer decisão tomada SEM ela (exceção, interrupção,
        # binding recusado…): uma violação crítica já provada nunca é escondida (E843-AUD-002).
        evidence = request.evidence.snapshot()
        decision = escalate_for_evidence(decision, evidence)
        binding = self._end_binding(
            token, revoke=decision.run_status is not RunStatus.OK, refused=binding_refused
        )
        result = self._close(
            context, request, started, decision, report, evidence, attempted, binding
        )
        if interrupt is not None:
            raise interrupt
        return result

    def _last_gate(
        self, facts: InvocationFacts, is_cancelled: Callable[[], bool]
    ) -> tuple[RunDecision | None, int]:
        """Cancelamento e prazo na última fronteira antes do provider."""
        if _stopped(is_cancelled):
            return _CANCELLED, 0
        timeout_s = self._timeout_s(facts)
        if timeout_s < 1:
            return _DEADLINE, 0
        return None, timeout_s

    @staticmethod
    def _refused_binding_decision(code: PreparationCode) -> RunDecision:
        if code is PreparationCode.CANCEL_REQUESTED:
            return _CANCELLED
        if code is PreparationCode.DEADLINE_EXCEEDED:
            return _DEADLINE
        return _BINDING_REFUSED

    def _binding_verified(
        self, context: PreparedWorkspace[Any], request: DeveloperInvocationInput
    ) -> bool:
        try:
            return bool(
                self._workspaces.verify_component_binding(
                    context, run_id=request.run_id, invocation_id=request.invocation_id
                )
            )
        except Exception:
            return False

    def _end_binding(
        self, token: object | None, *, revoke: bool, refused: bool | None
    ) -> BindingOutcome:
        """Libera (ok) ou revoga (qualquer outro desfecho) o binding **deste** Run."""
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
        # Fim normal não se provou liberado: nega a resolução (a revogação vale sem remoção).
        if self._attempt(self._workspaces.revoke_binding, token):
            return BindingOutcome.REVOKED
        return BindingOutcome.RELEASE_FAILED

    @staticmethod
    def _attempt(operation: Callable[[object], bool], token: object) -> bool:
        try:
            return operation(token) is True
        except Exception:
            return False

    # ---------------------------------------------------------------------------- I

    def _close(
        self,
        context: PreparedWorkspace[Any],
        request: DeveloperInvocationInput,
        started: datetime,
        decision: RunDecision,
        report: DeveloperInvocationReport | None,
        evidence: EvidenceSnapshot,
        attempted: bool,
        binding: BindingOutcome,
    ) -> DeveloperInvocationResult:
        finished = self._now()
        duration_ms = max(0, int((finished - started).total_seconds() * 1000))
        # Duas fontes, nunca misturadas: os METADADOS do provider só são persistidos se coerentes;
        # a EVIDÊNCIA do executor (negações aceitas pelo coletor, `files_read`) é persistida sempre.
        meta = report if decision.provider_trusted else None
        closure = RunClosure(
            status=decision.run_status,
            finished_at=finished,
            duration_ms=duration_ms,
            token_source=TokenSource(meta.token_source) if meta else TokenSource.UNAVAILABLE,
            input_tokens=meta.input_tokens if meta else None,
            output_tokens=meta.output_tokens if meta else None,
            files_read=report.files_read if report is not None else None,
            summary=clip_summary(meta.summary) if meta and meta.summary else None,
            error_summary=(
                None
                if decision.run_status is RunStatus.OK
                else run_error_summary(decision.code, meta.error_summary if meta else None)
            ),
            denials=evidence.denials,
            limit_exceeded=decision.limit_exceeded,
        )

        close = None
        for _ in range(_CLOSE_ATTEMPTS):
            try:
                with self._session_factory() as session:
                    close = em.close_developer_run(
                        session, context.task_id, run_id=request.run_id, closure=closure
                    )
                break
            except Exception:
                close = None

        if close is None:
            return DeveloperInvocationResult(
                InvocationStatus.UNFINALIZED,
                decision.code,
                run_id=request.run_id,
                provider_call_attempted=attempted,
                developer_outcome=decision.outcome,
                integrity_violation=decision.critical,
                binding=binding,
                finalization=Finalization.FAILED,
                requires_finalization=True,
                report=report,
            )

        run_status: RunStatus | None
        if close.outcome is CloseOutcome.CLOSED:
            finalization, run_status, events = (
                Finalization.CLOSED,
                decision.run_status,
                close.safety_events,
            )
        else:
            finalization, run_status, events = (
                Finalization.ALREADY_FINAL,
                close.existing_status,
                0,
            )
        pending = binding is BindingOutcome.RELEASE_FAILED or run_status is RunStatus.RUNNING
        completed = (
            run_status is RunStatus.OK
            and decision.run_status is RunStatus.OK
            and binding in (BindingOutcome.RELEASED, BindingOutcome.REVOKED)
        )
        status = (
            InvocationStatus.COMPLETED
            if completed
            else InvocationStatus.UNFINALIZED
            if pending
            else InvocationStatus.FAILED
        )
        return DeveloperInvocationResult(
            status,
            decision.code,
            run_id=request.run_id,
            run_status=run_status,
            provider_call_attempted=attempted,
            developer_outcome=decision.outcome,
            integrity_violation=decision.critical,
            binding=binding,
            finalization=finalization,
            safety_events=events,
            requires_finalization=pending,
            report=report,
        )

    # --------------------------------------------------------------------- apoio e leitura

    def _facts(self, context: PreparedWorkspace[Any]) -> InvocationFacts:
        with self._session_factory() as session:
            facts = em.read_invocation_facts(
                session,
                context.task_id,
                control_run_id=context.control_run_id,
                developer_binding_resolver=self._resolver,
            )
            session.rollback()
        return facts

    @staticmethod
    def _context_incoherence(
        facts: InvocationFacts, context: PreparedWorkspace[Any]
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
            facts.max_total_tokens,
            facts.goal,
            facts.context_manifest_id,
            facts.rendered_context_hash,
        ):
            return PreparationCode.PLAN_INCOHERENT
        return None

    def _timeout_s(self, facts: InvocationFacts) -> int:
        """Segundos inteiros que o provider pode usar (o menor entre Run e o que resta da task)."""
        assert facts.run_started_at is not None and facts.task_timeout_s is not None
        assert facts.run_timeout_s is not None
        remaining = task_deadline_remaining_s(
            facts.run_started_at, facts.task_timeout_s, now=self._now()
        )
        return math.floor(
            operation_timeout_s(run_timeout_s=facts.run_timeout_s, remaining_task_s=remaining)
        )

    def _read_context(self, digest: str | None) -> str | None:
        """Os bytes do artefato renderizado, conferidos contra o hash do manifest numa **única**
        leitura (`read_rendered_artifact`), como texto UTF-8 estrito."""
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            return None
        data = read_rendered_artifact(self._artifacts_dir, digest, max_bytes=_MAX_CONTEXT_BYTES)
        if data is None:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def _record_refusal(self, context: PreparedWorkspace[Any], code: InvocationCode) -> None:
        """`SafetyEvent` de uma recusa de **política** de capability. Falha ao gravar não muda a
        recusa."""
        kind = (
            SafetyEventKind.CAPABILITY_UNENFORCEABLE
            if code is InvocationCode.CAPABILITY_UNPROVEN
            else SafetyEventKind.CAPABILITY_DENIED
        )
        try:
            with self._session_factory() as session:
                em.record_developer_invocation_refusal(
                    session,
                    context.task_id,
                    control_run_id=context.control_run_id,
                    kind=kind,
                    rule_id=f"{_RULE_PREFIX}{code.value}",
                    detail=f"developer_invocation:{code.value}",
                )
        except Exception:
            return

    def _claim_unknown(self, task_id: str, spec: DeveloperRunSpec) -> DeveloperInvocationResult:
        """A reivindicação levantou: o `COMMIT` pode ter acontecido. Nunca chama o provider.

        A consulta aplica a MESMA verificação de identidade da reivindicação: um Run de outro
        componente com o mesmo `invocation_id` é colisão, nunca adoção."""
        try:
            with self._session_factory() as session:
                found = em.find_developer_claim(session, task_id, spec=spec)
        except Exception:
            return _refused(InvocationCode.CLAIM_FAILED)
        if found is None:
            return _refused(InvocationCode.CLAIM_FAILED)
        if found.outcome is ClaimOutcome.COLLISION:
            return _refused(InvocationCode.CLAIM_COLLISION)
        if found.outcome is not ClaimOutcome.ALREADY:
            return _refused(InvocationCode.CLAIM_FAILED)
        return self._already(found.run_id, found.existing_status)

    @staticmethod
    def _already(run_id: str | None, status: RunStatus | None) -> DeveloperInvocationResult:
        return DeveloperInvocationResult(
            InvocationStatus.ALREADY_CLAIMED,
            InvocationCode.ALREADY_CLAIMED,
            run_id=run_id,
            run_status=status,
            requires_finalization=status is RunStatus.RUNNING,
        )


def _with_timeout(request: DeveloperInvocationInput, timeout_s: int) -> DeveloperInvocationInput:
    return replace(request, timeout_s=timeout_s)


__all__ = ["ComponentBindingPort", "DeveloperInvocationService"]
