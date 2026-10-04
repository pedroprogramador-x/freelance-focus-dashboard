"""Erros de domínio do Orchestrator.

Mesmo desenho de `app.workspace.errors` e `app.context_engine.errors` ([01] §2): a camada
de serviço levanta exclusivamente estas exceções, o `status_code` mora aqui, e a camada
HTTP (`app.api.tasks`) só as deixa subir para o handler registrado em `app.main`.

`409` é o status de [06] §2 para **transição inválida, guarda não satisfeita e fingerprint
desatualizado** — "sempre com o motivo e qual campo divergiu". Por isso
`ApprovalFingerprintMismatch` e `TransitionGuardFailed` carregam `diverged_fields` /
`guard`: a UI de [06] §4 precisa dizer *qual campo mudou*, não só que algo mudou.
"""

from __future__ import annotations


class OrchestratorError(Exception):
    """Base de todo erro de domínio do Orchestrator.

    ``code`` é o slug estável de `{code, message}` ([06] §2); ``status_code`` é o HTTP.
    """

    code: str = "orchestrator_error"
    status_code: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def as_payload(self) -> dict[str, object]:
        """Campos extra que a camada HTTP acrescenta a `{code, message}`.

        Vazio por padrão. Quem tem diagnóstico estruturado a oferecer — divergência de
        fingerprint, guarda que falhou — sobrescreve.
        """
        return {}


class TaskNotFound(OrchestratorError):
    """Nenhuma `WorkspaceTask` com o `id` pedido — **ou** a task existe, mas o
    subrecurso pedido (o manifest de `/context`) não. **404** nos dois casos.

    ``reason`` é o mesmo idioma de `TransitionGuardFailed.requires_replan`: um slug
    estável e opcional, para quem consome o erro diferenciar os motivos sem depender do
    texto da mensagem (E6-CONS-001). `None` (o padrão) é "task realmente inexistente" —
    comportamento inalterado. `GET /tasks/{id}/context` usa
    `reason="terminal_without_context"` quando a task chegou a um estado terminal sem
    nunca ter tido um manifest: nesse caso `POST /plan` responderia `409
    invalid_transition` (estado terminal é imutável), então a mensagem não pode
    instruir essa ação, e quem lê a resposta programaticamente não deveria precisar
    adivinhar isso a partir da prosa.
    """

    code = "task_not_found"
    status_code = 404

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason

    def as_payload(self) -> dict[str, object]:
        return {"reason": self.reason} if self.reason is not None else {}


class InvalidTask(OrchestratorError):
    """Campo de criação malformado: `title` vazio ou longo demais, `goal` vazio. **422**.

    O invariante vive aqui, e não só na coluna e no schema Pydantic, pelo motivo de
    E3-AUD-007: o SQLite não recusa um `VARCHAR` longo, e um chamador interno futuro não
    passa pelo Pydantic.
    """

    code = "invalid_task"
    status_code = 422


class WorkspaceNotPlannable(OrchestratorError):
    """O workspace não pode receber planejamento. **409**.

    Dois casos, ambos de [02] §1 e [04] §8: workspace **arquivado** (bloqueia novas tasks)
    e workspace que **não é repositório git** ou está sem `HEAD` (*unborn branch*). O
    segundo é o "Projeto criado do zero" de [07]: o workspace serve para contexto, mas
    planejar exige um `base_commit` para congelar, e o backend **nunca** roda `git init`.
    """

    code = "workspace_not_plannable"
    status_code = 409

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        #: O mesmo slug que `TaskResponse.planning.blockers` usa (E6-CONS4): quem recebe o
        #: `409` associa a recusa a uma pré-condição sem interpretar a prosa.
        self.reason = reason

    def as_payload(self) -> dict[str, object]:
        return {"reason": self.reason}


class InvalidTestConfig(OrchestratorError):
    """`DevWorkspace.test_config` malformado. **422**.

    Levantado tanto na escrita (`PATCH /api/workspaces/{id}`) quanto na leitura durante o
    planejamento. A segunda existe porque a coluna é JSON livre no banco: uma linha gravada
    por uma versão anterior do schema não pode virar um `test_binding` silenciosamente
    incompleto — isso entraria no fingerprint e seria aprovado.
    """

    code = "invalid_test_config"
    status_code = 422

    def as_payload(self) -> dict[str, object]:
        # Mesmo slug do bloqueio de elegibilidade (E6-CONS4), para o `422` do `POST /plan`
        # ser rastreável até `planning.blockers` como o `409` de `WorkspaceNotPlannable`.
        return {"reason": "invalid_test_config"}


class InvalidTransition(OrchestratorError):
    """A transição pedida não existe na tabela de [02] §4. **409**.

    Distinta de `TransitionGuardFailed`: aqui a aresta **não existe** (ex.: sair de um
    estado terminal, ou `draft → executing`); lá ela existe e uma pré-condição falhou.
    """

    code = "invalid_transition"
    status_code = 409

    def __init__(self, message: str, *, current: str, requested: str) -> None:
        super().__init__(message)
        self.current = current
        self.requested = requested

    def as_payload(self) -> dict[str, object]:
        return {"current_status": self.current, "requested_status": self.requested}


class TransitionGuardFailed(OrchestratorError):
    """A aresta existe, mas uma guarda de [02] §4 não foi satisfeita. **409**.

    ``guard`` é o identificador estável da guarda que falhou, para que a UI possa explicar
    *o que* falta em vez de repetir a mensagem.

    ``diverged_fields`` e ``requires_replan`` existem para a guarda de entrada (E6-AUD-001).
    [06] §2 exige que o `409` diga "sempre com o motivo e **qual campo divergiu**", e a
    recusa na entrada de execução é justamente onde faltava: o cliente recebia o nome da
    guarda e nada sobre o que mudou. ``requires_replan`` distingue os dois desfechos que
    uma invalidação na entrada pode ter — reaprovar resolve, ou só um `POST /plan` novo
    resolve — e é a diferença entre a UI oferecer um botão útil e um laço infinito.
    """

    code = "transition_guard_failed"
    status_code = 409

    def __init__(
        self,
        message: str,
        *,
        guard: str,
        diverged_fields: tuple[str, ...] = (),
        requires_replan: bool = False,
        reason_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.guard = guard
        self.diverged_fields = diverged_fields
        self.requires_replan = requires_replan
        #: Código estável (E7.6) da recusa de capability, do vocabulário fechado de
        #: `safety.CapabilityRefusalCode`. Fora do `as_payload`: o corpo do `409` não muda.
        self.reason_code = reason_code

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"guard": self.guard}
        if self.diverged_fields:
            payload["diverged_fields"] = list(self.diverged_fields)
        if self.requires_replan:
            payload["requires_replan"] = True
        return payload


class ApprovalFingerprintMismatch(OrchestratorError):
    """O `execution_fingerprint` enviado não bate com o recalculado. **409**.

    [02] §7 e [04] §7: "qualquer campo coberto que mude invalida a aprovação: `409`,
    `SafetyEvent(approval_invalidated)`, volta a `awaiting_approval` indicando **qual
    campo** mudou". ``diverged_fields`` é esse "qual campo" — a lista de chaves de
    `approved_fingerprint_parts` cujo valor mudou desde que o cliente leu a task.
    """

    code = "approval_fingerprint_mismatch"
    status_code = 409

    def __init__(
        self,
        message: str,
        *,
        expected: str,
        received: str,
        diverged_fields: tuple[str, ...],
    ) -> None:
        super().__init__(message)
        self.expected = expected
        self.received = received
        self.diverged_fields = diverged_fields

    def as_payload(self) -> dict[str, object]:
        return {
            "expected_fingerprint": self.expected,
            "received_fingerprint": self.received,
            "diverged_fields": list(self.diverged_fields),
        }


class ConcurrentTaskUpdate(OrchestratorError):
    """O *compare-and-set* de `(id, status, version)` não encontrou a linha. **409**.

    [02] §4: "quem commitar primeiro vence; o perdedor vira no-op". Este erro é o lado
    perdedor, e é informativo — o estado do banco está correto, só não é o que este
    chamador viu.
    """

    code = "concurrent_task_update"
    status_code = 409


class TaskPurgeBlocked(OrchestratorError):
    """A task não pode ser purgada. **409**.

    [02] §11 regra 1: "task só pode ser purgada em `done`, `failed` ou `cancelled`".
    """

    code = "task_purge_blocked"
    status_code = 409


class TaskBenchmarkProtected(OrchestratorError):
    """[02] §11 regra 6: o grupo de benchmark já tem avaliação registrada. **409**."""

    code = "task_benchmark_protected"
    status_code = 409


class PurgeTokenRejected(OrchestratorError):
    """Token de purga ausente, expirado, já usado ou de outro sujeito. **403** genérico.

    O motivo **não** é diferenciado para o chamador — mesma regra de
    `app.workspace.errors.PurgeTokenRejected`.
    """

    code = "purge_token_rejected"
    status_code = 403


__all__ = [
    "ApprovalFingerprintMismatch",
    "ConcurrentTaskUpdate",
    "InvalidTask",
    "InvalidTestConfig",
    "InvalidTransition",
    "OrchestratorError",
    "PurgeTokenRejected",
    "TaskBenchmarkProtected",
    "TaskNotFound",
    "TaskPurgeBlocked",
    "TransitionGuardFailed",
    "WorkspaceNotPlannable",
]
