"""`HandlerContext` e `Outcome` (E7.5-B): o contrato interno entre o executor e as operações.

Moram aqui para que `executor.py` (que despacha) e `fs_ops.py` (que implementa) não se importem
um ao outro. **Nada disto é visto pelo provider.**
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.path_runtime import BoundRoot
from app.safety.policy import SafetyPolicy
from app.safety.tool_limits import ToolLimits
from app.safety.types import SafetyDecision
from app.tool_executor.contracts import ToolStatus
from app.tool_executor.validation import ContractViolation
from app.tool_executor.workspace import ResolvedWorkspace


@dataclass(frozen=True, slots=True)
class PreparedToolOutput:
    """Saída de sucesso **já finalizada** pela fronteira central do executor (E7.5-C).

    `content` já passou pela redação central **uma vez**, sobre o conteúdo completo, e cabe em
    `tool_result_content_bytes`. Só o executor a constrói (`_prepare_output`, a mesma autoridade
    que finaliza qualquer `OK`; um teste de arquitetura trava isso) e só aceita de volta, num
    `Outcome`, a instância que ele mesmo emitiu nesta chamada. Interno: não é contrato do
    provider e não carrega caminho absoluto.
    """

    content: str


@dataclass(frozen=True, slots=True)
class HandlerContext:
    """O que o método de uma operação pode usar. **Interno**: o provider nunca o vê."""

    root: BoundRoot
    workspace: ResolvedWorkspace
    policy: SafetyPolicy
    limits: ToolLimits
    #: Já adaptado pelo executor: nunca lança e só devolve `bool` (fail closed).
    is_cancelled: Callable[[], bool]
    #: Registra no `DecisionJournal` uma negação **real** de safety que o método usou para
    #: **omitir** um caminho (listagem/busca), sem transformar a operação em `DENIED`. Criada pelo
    #: executor para esta chamada; não é pública e não vem do provider. Se o journal falhar, o
    #: executor entra em *poison* e a operação inteira vira `ERROR journal_unavailable`.
    record_denial: Callable[[SafetyDecision], None]
    #: E7.5-C: a **fronteira de saída** do executor, para uma operação que precisa saber **antes
    #: dos efeitos** se o seu `OK` caberia: conteúdo bruto completo → redação central uma vez →
    #: teto em bytes UTF-8. `None` = não caberia. O `PreparedToolOutput` devolvido vai, intacto, no
    #: `Outcome` de sucesso (nada é redigido nem medido de novo).
    prepare_output: Callable[[str], PreparedToolOutput | None] | None = None


class InspectionFailed(Exception):
    """Não foi possível **inspecionar** um caminho (lstat/E-S/`inspection_error`).

    Não é política: não prova que o caminho é proibido. O executor a converte em
    `ERROR path_inspection_failed` — sem registro de política, sem omissão, sem `[no matches]`.
    A mensagem é vazia de propósito: nenhum texto cru de erro do SO viaja.
    """


class ToolError(Exception):
    """`ERROR` técnico com código fixo (E7.5-C), levantado de dentro de uma operação.

    Existe para a fase de efeitos do `ApplyPatch`, onde uma falha precisa atravessar o laço de
    arquivos e ainda carregar a informação de aplicação parcial (`OperationAborted`). O código é
    chave de `ERROR_PHRASES`; a mensagem é só o código.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class OperationAborted(Exception):
    """Uma operação de **vários alvos** (`ApplyPatch`) parou por ``cause`` ao tratar ``subject``.

    * ``cause`` é a falha original (`PathAccessDenied`, `PathIntegrityViolation`,
      `PathOperationFailed`, `InspectionFailed`, `ToolError`…): o executor a mapeia **como
      sempre** (técnica → `ERROR`, política/integridade → `DENIED` + journal);
    * ``subject`` é o caminho **relativo** que o journal registra (redigido lá), nunca absoluto;
    * ``effects_applied``: algum efeito de filesystem **pode** já ter acontecido antes da falha.
      O executor então marca o resultado, genericamente, como aplicação parcial — sem listar
      caminho e sem tentar desfazer nada.
    """

    def __init__(self, cause: Exception, *, subject: str, effects_applied: bool) -> None:
        super().__init__(type(cause).__name__)
        self.cause = cause
        self.subject = subject
        self.effects_applied = effects_applied


@dataclass(frozen=True, slots=True)
class OutputFragment:
    """Uma unidade de saída e a sua **proveniência estruturada** (`files_read`).

    **Não é redigida isoladamente**: o executor junta o texto bruto de todos os fragmentos e aplica
    o redator central **uma vez** na saída completa (um padrão multilinha atravessa fragmentos).
    `files_read` sai da proveniência dos fragmentos que compõem o resultado `OK` final — nunca de
    um parse do texto, e só depois de o `ToolResult` existir. Interno: o provider não o vê.
    """

    text: str
    files_read: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Outcome:
    """O que o método de uma operação devolve. O executor o transforma em `ToolResult`.

    `rule_id` (obrigatório em `DENIED`/`ERROR`) é a chave da frase fixa; `files_read` só conta
    em `OK` e já vem canônico (relativo ao workspace, `/`).
    """

    status: ToolStatus
    content: str = ""
    rule_id: str | None = None
    subject: str | None = None
    files_read: tuple[str, ...] = ()
    #: Saída em unidades com proveniência (`ListDirectory`, `SearchText`). Exclui `content` e
    #: `files_read`. **Todos** os fragmentos são entregues ou **nenhum** (não há truncamento final):
    #: a proveniência do resultado é a união da de todos os fragmentos.
    fragments: tuple[OutputFragment, ...] | None = None
    #: E7.5-C: saída de sucesso **já finalizada** (redigida e medida) por `prepare_output` antes
    #: dos efeitos. Exclui `content`, `files_read` e `fragments`; o executor a entrega como está.
    prepared: PreparedToolOutput | None = None

    def __post_init__(self) -> None:
        if self.status is not ToolStatus.OK and self.rule_id is None:
            raise ContractViolation("DENIED/ERROR precisam de rule_id")
        if self.fragments is not None and (self.content or self.files_read):
            raise ContractViolation("fragments excluem content e files_read")
        if self.prepared is not None and (
            self.status is not ToolStatus.OK
            or self.content
            or self.files_read
            or self.fragments is not None
        ):
            raise ContractViolation("prepared é só de OK e exclui content/files_read/fragments")


__all__ = [
    "HandlerContext",
    "InspectionFailed",
    "OperationAborted",
    "Outcome",
    "OutputFragment",
    "PreparedToolOutput",
    "ToolError",
]
