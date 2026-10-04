"""`LocalWorktreeToolExecutor` (E7.5-A): o esqueleto run-scoped do executor concreto.

**Esta etapa não executa operação nenhuma.** As nove operações são métodos **privados** desta
classe (`_read_file`, `_write_file`…), e todas devolvem, por ora, `ERROR operation_not_implemented`;
cada uma ganha corpo no seu checkpoint (E7.5-B em diante). O despacho tipo → método é uma
tabela **fechada** interna (`_DISPATCH`): não é parâmetro do construtor, não vem da factory nem
do provider, e não aceita callable externo. Aqui existe o *pipeline* que todas atravessam, fail
closed:

```
fechado? → envenenado? → cancelado?            (sem IO)
tipo ∈ TOOL_OPERATIONS?                         (sem IO; senão DENIED tool.unknown_operation)
capability gate (perfil × operação)             (sem IO; senão DENIED capability.denied)
revalidar a raiz vinculada                      (primeiro IO)
método da operação                              (não implementado ⇒ ERROR fixo)
```

Falha de **integridade** das primitivas de `path_runtime` (`PathIntegrityViolation`: raiz, pai ou
alvo trocado, reparse, identidade não verificável) é um *fato*; é **este** módulo que a mapeia
para a regra, o `ToolDecisionRecord` e o `DENIED` (`reasons.rule_for_integrity`).

Todo `DENIED` gera um `ToolDecisionRecord` no `DecisionJournal` **antes** de voltar ao provider.
Se o journal falhar, o executor fica *poisoned*: devolve `ERROR` — nunca `DENIED`, que exigiria
uma trilha de decisão disponível — agora e em toda chamada seguinte.

Tudo o que sai ao provider é frase fixa por `rule_id` (`reasons.py`), nunca `decision.reason`
cru, `str(OSError)`, stderr, traceback nem caminho absoluto. A redação é aplicada **na fronteira
de saída**: dados internos (comparação, hash, patch, parsing) nunca são redigidos antes.

`execute` é serializada por um lock por run. `usage()` continua válido depois de `close()`.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from types import MappingProxyType

from app.path_runtime import (
    BoundRoot,
    PathAccessDenied,
    PathIntegrityViolation,
    PathOperationFailed,
    verify_root,
)
from app.safety.capability_profile import (
    Capability,
    EnforcementMode,
    ProviderCapabilityProfile,
)
from app.safety.policy import SafetyPolicy
from app.safety.redaction import redact, redact_path
from app.safety.tool_limits import TOOL_LIMITS_V1, ToolLimits
from app.safety.types import SafetyDecision
from app.tool_executor import fs_ops, git_ops, patch_ops
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
    MediatedTools,
    MediatedUsage,
    ReadFile,
    RunScope,
    SearchText,
    ToolDecisionRecord,
    ToolRequest,
    ToolResult,
    ToolStatus,
    WriteFile,
    required_capability,
)
from app.tool_executor.facade import MediatedToolsFacade
from app.tool_executor.outcome import (
    HandlerContext,
    InspectionFailed,
    OperationAborted,
    Outcome,
    PreparedToolOutput,
    ToolError,
)
from app.tool_executor.reasons import (
    PARTIAL_APPLY_NOTE,
    PARTIAL_KEEPS_CODE,
    UnknownRule,
    category_of,
    reason_for,
    rule_for_integrity,
    status_for_rule,
)
from app.tool_executor.validation import require_text
from app.tool_executor.workspace import ResolvedWorkspace

#: O modo de enforcement que cada capability de Developer precisa ter para a operação rodar.
_REQUIRED_MODE: Mapping[Capability, EnforcementMode] = MappingProxyType(
    {
        Capability.READ_FILES: EnforcementMode.MEDIATED,
        Capability.WRITE_FILES: EnforcementMode.MEDIATED,
        Capability.GIT_READ: EnforcementMode.FIXED_OPERATIONS_ONLY,
    }
)


class _JournalUnavailable(Exception):
    """Interna: o `DecisionJournal` falhou; o executor já está *poisoned*."""


#: As falhas que um método de operação pode levantar e que o executor sabe mapear (qualquer outra
#: vira `ERROR internal_error`). `OperationAborted` (E7.5-C) embrulha uma destas.
_MAPPED_FAILURES: tuple[type[Exception], ...] = (
    _JournalUnavailable,
    InspectionFailed,
    PathIntegrityViolation,
    PathAccessDenied,
    PathOperationFailed,
    ToolError,
)


#: Tabela **fechada** tipo → nome do método privado. Cobre exatamente as nove operações de
#: `TOOL_OPERATIONS` (um teste trava isso) e não é parâmetro de nada.
_DISPATCH: Mapping[type, str] = MappingProxyType(
    {
        ReadFile: "_read_file",
        ListDirectory: "_list_directory",
        SearchText: "_search_text",
        WriteFile: "_write_file",
        ApplyPatch: "_apply_patch",
        GitStatus: "_git_status",
        GitDiff: "_git_diff",
        GitShow: "_git_show",
        GitListTree: "_git_list_tree",
    }
)


def _operation_name(request: object) -> str:
    """Nome seguro para `ToolResult.operation`: o do tipo, ou `<unknown>` se não for confiável."""
    name = type(request).__name__
    if name.isascii() and name.isidentifier() and len(name) <= 64:
        return name
    return "<unknown>"


def _reason(rule_id: str, partial: bool) -> str:
    reason = reason_for(rule_id)
    return f"{reason} {PARTIAL_APPLY_NOTE}" if partial else reason


def _subject_of(request: object, operation: str) -> str:
    path = getattr(request, "path", None)
    return path if isinstance(path, str) and path else operation


class LocalWorktreeToolExecutor:
    """Executor de **um** run, ligado a uma raiz vinculada. Criado pela factory; nunca global."""

    def __init__(
        self,
        *,
        workspace: ResolvedWorkspace,
        root: BoundRoot,
        policy: SafetyPolicy,
        profile: ProviderCapabilityProfile,
        run_scope: RunScope,
        cancel_token: CancelToken,
        journal: DecisionJournal,
        limits: ToolLimits = TOOL_LIMITS_V1,
    ) -> None:
        self._workspace = workspace
        self._root = root
        self._policy = policy
        self._profile = profile
        self._scope = run_scope
        self._cancel_token = cancel_token
        self._journal = journal
        self._limits = limits

        self._lock = threading.RLock()
        self._closed = False
        self._poisoned = False
        self._cancel_seen = False
        self._operations = 0
        self._denials = 0
        self._sequence = 0
        self._files_read: set[str] = set()
        #: As saídas que `_prepare_output` emitiu **nesta** chamada: `_finish` só aceita estas.
        self._issued: list[PreparedToolOutput] = []
        self._facade = MediatedToolsFacade(self.execute)

    def __repr__(self) -> str:
        return "<LocalWorktreeToolExecutor>"

    # ------------------------------------------------------------------- superfície

    def mediated_tools(self) -> MediatedTools:
        return self._facade

    def usage(self) -> MediatedUsage:
        """Snapshot imutável. `files_read` ordenado pelos bytes UTF-8 e sem duplicatas."""
        with self._lock:
            files = tuple(
                sorted(self._files_read, key=lambda p: p.encode("utf-8", "surrogatepass"))
            )
            return MediatedUsage(
                files_read=files, operations=self._operations, denials=self._denials
            )

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def execute(self, request: ToolRequest) -> ToolResult:
        with self._lock:
            operation = _operation_name(request)
            if self._closed:
                return self._error(operation, "executor_closed")
            if self._poisoned:
                return self._error(operation, "journal_unavailable")
            self._operations += 1
            try:
                return self._pipeline(request, operation)
            except UnknownRule:
                return self._error(operation, "internal_error")
            except Exception:
                return self._error(operation, "internal_error")

    # ------------------------------------------------------------------- operações
    #
    # Privadas, sem fallback e sem parâmetro externo. As quatro de arquivo (E7.5-B) vivem em
    # `fs_ops`, o `ApplyPatch` (E7.5-C) em `patch_ops` e as quatro de Git (E7.5-D) em
    # `git_ops`, sobre o `git_runtime.mediated`.

    def _read_file(self, context: HandlerContext, request: ReadFile) -> Outcome:
        return fs_ops.read_file(context, request)

    def _list_directory(self, context: HandlerContext, request: ListDirectory) -> Outcome:
        return fs_ops.list_directory_entries(context, request)

    def _search_text(self, context: HandlerContext, request: SearchText) -> Outcome:
        return fs_ops.search_text(context, request)

    def _write_file(self, context: HandlerContext, request: WriteFile) -> Outcome:
        return fs_ops.write_file(context, request)

    def _apply_patch(self, context: HandlerContext, request: ApplyPatch) -> Outcome:
        return patch_ops.apply_patch(context, request)

    def _git_status(self, context: HandlerContext, request: GitStatus) -> Outcome:
        return git_ops.git_status(context, request)

    def _git_diff(self, context: HandlerContext, request: GitDiff) -> Outcome:
        return git_ops.git_diff(context, request)

    def _git_show(self, context: HandlerContext, request: GitShow) -> Outcome:
        return git_ops.git_show(context, request)

    def _git_list_tree(self, context: HandlerContext, request: GitListTree) -> Outcome:
        return git_ops.git_list_tree(context, request)

    # --------------------------------------------------------------------- pipeline

    def _cancelled(self) -> bool:
        """Sticky e fail closed: exceção ou resposta que não seja exatamente `False` cancela."""
        if self._cancel_seen:
            return True
        try:
            answer = self._cancel_token.is_cancelled()
        except Exception:
            answer = True
        if answer is not False:
            self._cancel_seen = True
        return self._cancel_seen

    def _pipeline(self, request: ToolRequest, operation: str) -> ToolResult:
        if self._cancelled():
            return self._error(operation, "operation_cancelled")

        if type(request) not in TOOL_OPERATIONS:
            return self._deny(operation, "tool.unknown_operation", operation, write=False)

        capability = required_capability(request)
        if self._profile.mode_of(capability) is not _REQUIRED_MODE[capability]:
            return self._deny(
                operation,
                "capability.denied",
                _subject_of(request, operation),
                write=capability is Capability.WRITE_FILES,
            )
        write = capability is Capability.WRITE_FILES

        try:
            verify_root(self._root)
        except PathIntegrityViolation as violation:
            return self._deny(
                operation, rule_for_integrity(violation.category), "<workspace>", write=write
            )
        except PathOperationFailed:
            return self._error(operation, "workspace_unavailable")

        method = getattr(self, _DISPATCH[type(request)])
        self._issued = []

        context = HandlerContext(
            root=self._root,
            workspace=self._workspace,
            policy=self._policy,
            limits=self._limits,
            is_cancelled=self._cancelled,
            record_denial=lambda decision: self._record_internal_denial(
                operation, decision, write=write
            ),
            prepare_output=self._prepare_output,
        )
        try:
            outcome = method(context, request)
        except OperationAborted as aborted:
            return self._failed(
                operation,
                aborted.cause,
                aborted.subject,
                write=write,
                partial=aborted.effects_applied,
            )
        except _MAPPED_FAILURES as failure:
            return self._failed(operation, failure, _subject_of(request, operation), write=write)
        return self._finish(operation, outcome, write=write)

    def _failed(
        self,
        operation: str,
        failure: Exception,
        subject: str,
        *,
        write: bool,
        partial: bool = False,
    ) -> ToolResult:
        """O **único** mapeamento falha → `ToolResult`: técnica → `ERROR`; política e integridade
        → `DENIED` + journal. ``partial`` (E7.5-C) marca o resultado de uma operação que parou
        depois de efeitos de filesystem — a categoria da falha **não** muda por isso."""
        if isinstance(failure, _JournalUnavailable):
            return self._error(operation, "journal_unavailable", partial=partial)
        if isinstance(failure, InspectionFailed):
            return self._error(operation, "path_inspection_failed", partial=partial)
        if isinstance(failure, PathIntegrityViolation):
            rule = rule_for_integrity(failure.category)
            return self._deny(operation, rule, subject, write=write, partial=partial)
        if isinstance(failure, PathAccessDenied):
            return self._from_rule(
                operation,
                failure.decision.rule_id,
                failure.decision.subject_redacted,
                write=write,
                partial=partial,
            )
        if isinstance(failure, PathOperationFailed):
            if failure.category.value == "cancelled":
                self._cancel_seen = True
                return self._error(operation, "operation_cancelled", partial=partial)
            return self._error(operation, failure.category.value, partial=partial)
        if isinstance(failure, ToolError):
            return self._error(operation, failure.code, partial=partial)
        return self._error(operation, "internal_error", partial=partial)

    def _finish(self, operation: str, outcome: Outcome, *, write: bool) -> ToolResult:
        if outcome.status is ToolStatus.OK:
            # Ordem definitiva: (1) render do bruto completo → (2) redação **central, uma vez**,
            # sobre o conteúdo completo → (3) bytes UTF-8 do redigido vs. o teto (**fail closed**:
            # nada de truncar a saída final) → (4) `ToolResult` → (5) **só então** `files_read` →
            # (6) `usage`. Sem `ToolResult OK`, `files_read` não muda.
            #
            # E7.5-C: uma operação com efeitos (`ApplyPatch`) chama (1)–(3) **antes** dos efeitos,
            # pela mesma `_prepare_output`, e devolve o `PreparedToolOutput`: aqui ele é entregue
            # como está — nem redigido nem medido de novo.
            if outcome.prepared is not None:
                if not any(outcome.prepared is issued for issued in self._issued):
                    return self._error(operation, "internal_error")  # não veio desta fronteira
                prepared: PreparedToolOutput | None = outcome.prepared
                delivered: tuple[str, ...] = ()
            else:
                if outcome.fragments is not None:
                    raw = "\n".join(fragment.text for fragment in outcome.fragments)
                    provenance = {path for f in outcome.fragments for path in f.files_read}
                    delivered = tuple(sorted(provenance))
                else:
                    raw, delivered = outcome.content, outcome.files_read
                prepared = self._prepare_output(raw)
            if prepared is None:
                return self._deny(
                    operation, "limit.tool_result_content_bytes", operation, write=write
                )
            for path in delivered:
                require_text("files_read", path)
            result = ToolResult(ToolStatus.OK, operation, content=prepared.content)
            self._files_read.update(delivered)
            return result
        assert outcome.rule_id is not None  # garantido por Outcome.__post_init__
        if outcome.status is ToolStatus.DENIED:
            return self._deny(operation, outcome.rule_id, outcome.subject or operation, write=write)
        return self._error(operation, outcome.rule_id)

    def _from_rule(
        self, operation: str, rule_id: str, subject: str, *, write: bool, partial: bool = False
    ) -> ToolResult:
        if status_for_rule(rule_id) is ToolStatus.ERROR:
            return self._error(operation, rule_id, partial=partial)
        return self._deny(operation, rule_id, subject, write=write, partial=partial)

    # ------------------------------------------------------------ fronteira de saída

    def _prepare_output(self, raw: str) -> PreparedToolOutput | None:
        """A **fronteira de saída**: redação **central, uma só vez**, sobre a saída **completa**;
        depois o teto, em bytes UTF-8.

        `None` = o conteúdo **redigido** passa de `tool_result_content_bytes`: a operação vira
        `DENIED limit.tool_result_content_bytes`. Nunca se corta a saída final (um corte depois da
        redação não teria como saber o que cada pedaço continha), e nenhum `files_read` entra.

        É a única autoridade: `_finish` a usa para todo `OK` comum, e o `ApplyPatch` (E7.5-C) a
        recebe pelo `HandlerContext` para finalizar o summary **antes** do primeiro efeito.
        """
        text = redact(raw)
        if len(text.encode("utf-8")) > self._limits.tool_result_content_bytes:
            return None
        prepared = PreparedToolOutput(text)
        self._issued.append(prepared)
        return prepared

    def _error(self, operation: str, code: str, *, partial: bool = False) -> ToolResult:
        """``partial``: a falha técnica veio **depois** de efeitos — o código vira
        `patch_partial_apply` (salvo cancelamento, poison e erro interno, que se mantêm) e o
        motivo ganha a nota fixa de aplicação parcial."""
        if partial and code not in PARTIAL_KEEPS_CODE:
            code = "patch_partial_apply"
        return ToolResult(ToolStatus.ERROR, operation, reason=redact(_reason(code, partial)))

    def _deny(
        self, operation: str, rule_id: str, subject: str, *, write: bool, partial: bool = False
    ) -> ToolResult:
        """Registra a negação no journal **antes** de devolvê-la. Falha do journal envenena.

        Com ``partial`` a negação continua `DENIED` (e continua indo ao journal), mas o motivo
        diz, genericamente, que efeitos anteriores podem já ter acontecido."""
        try:
            self._append_record(operation, rule_id, subject, write=write)
        except _JournalUnavailable:
            return self._error(operation, "journal_unavailable", partial=partial)
        self._denials += 1
        return ToolResult(ToolStatus.DENIED, operation, reason=redact(_reason(rule_id, partial)))

    def _append_record(self, operation: str, rule_id: str, subject: str, *, write: bool) -> None:
        """A **única** porta para o journal. Reason fixo; subject redigido; `sequence` contínua."""
        record = ToolDecisionRecord(
            sequence=self._sequence + 1,
            operation=operation,
            category=category_of(rule_id, write=write),
            rule_id=rule_id,
            subject_redacted=redact_path(subject) or operation,
            reason=redact(reason_for(rule_id)),
            run_scope=self._scope,
        )
        try:
            self._journal.append(record)
        except Exception:
            self._poisoned = True
            raise _JournalUnavailable from None
        self._sequence += 1

    def _record_internal_denial(
        self, operation: str, decision: SafetyDecision, *, write: bool
    ) -> None:
        """Negação **real** que um método usou para omitir um caminho: vai ao journal, mas a
        operação segue `OK` — logo `MediatedUsage.denials` **não** muda (ele conta `ToolResult
        DENIED`)."""
        self._append_record(operation, decision.rule_id, decision.subject_redacted, write=write)


__all__ = [
    "HandlerContext",
    "LocalWorktreeToolExecutor",
    "Outcome",
]
