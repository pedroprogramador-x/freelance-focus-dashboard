"""`ApplyPatch` (E7.5-C): coordenação de um unified diff **seguro e determinístico**.

O parse e a aplicação em memória são de `unified_diff` (puro). Aqui só se coordena — política em
`safety`, fatos e IO em `path_runtime`, exatamente como em `fs_ops`. Nada aqui toca o filesystem
diretamente: os efeitos são **só** `create_exclusive`/`write_fd`/`delete_if_identity`.

## Duas fases, e nenhum efeito na primeira

```
PREFLIGHT (somente leitura; qualquer falha → zero efeitos)
  0. cancelamento → bytes do patch (limite) → marcador de redação → parse (+ cancelamento)
  1. limites de arquivos e hunks
  2. por arquivo, na ordem do patch: sintaxe + `.git` mediado + inspect + decide_path
     (a primeira negação bloqueia o patch inteiro; nada depois dela é avaliado)
  3. colisões: mesmo alvo canônico (caixa/Unicode), mesma identidade, um alvo ancestral de outro
  4. por arquivo, na ordem canônica: existência/tipo → leitura segura da linha de base (interna:
     não entra em `files_read`) → UTF-8 → hunks exatos em memória → teto do resultado →
     marcador de redação no resultado
  5. o summary de sucesso **exato** (ordem canônica) passa pela fronteira de saída do executor:
     redação central uma vez → teto de `ToolResult`. Não cabe → `DENIED`, zero efeitos; cabe →
     o `PreparedToolOutput` é guardado e entregue intacto se todos os efeitos terminarem
EFFECTS (ordem canônica: caminho relativo em bytes UTF-8)
  CREATE  create_exclusive → decide_post_create → write_fd
  UPDATE  open_existing(RDWR) → decide_post_open → reler pelo handle == linha de base → write_fd
  DELETE  open_existing → decide_post_open → reler == linha de base → delete_if_identity
```

## Sem rollback entre arquivos

Todo erro **detectável** acontece no preflight. Depois do primeiro efeito, uma corrida, falha de
E/S, cancelamento ou violação de integridade pode deixar o patch **parcialmente aplicado**: nada
é desfeito (um rollback improvisado pode piorar o estado). A falha sobe como `OperationAborted`
com `effects_applied`, e o executor marca o resultado com uma nota **genérica**, sem caminho.

`effects_applied` sai de efeitos **realmente ocorridos** na fase de efeitos — no CREATE, do
`CreateMutationTrace` que o próprio `create_exclusive` preenche syscall a syscall —, nunca de
uma observação do preflight, que uma corrida pode ter invalidado.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from app.path_runtime import (
    CreateMutationTrace,
    PathAccessDenied,
    PathFailure,
    PathOperationFailed,
    create_exclusive,
    delete_if_identity,
    discard_created,
    open_existing,
    probe_create,
    read_fd,
    relative_target,
    write_fd,
)
from app.safety import contains_redaction_marker, decide_post_create, decide_post_open
from app.safety.paths import PathIntent
from app.safety.types import ObjectIdentity, PathFacts
from app.tool_executor.contracts import ApplyPatch, ToolStatus

# A **mesma** pipeline de path das outras operações (sintaxe, `.git`, inspect, decide_path) e a
# mesma regra de texto: reaproveitadas de `fs_ops`, não reimplementadas.
from app.tool_executor.fs_ops import _authorize, _decode_text
from app.tool_executor.outcome import (
    HandlerContext,
    OperationAborted,
    Outcome,
    PreparedToolOutput,
    ToolError,
)
from app.tool_executor.unified_diff import (
    EMPTY_TEXT,
    INVALID_PATCH,
    PATCH_CONFLICT,
    FileSection,
    PatchError,
    PatchOperation,
    apply_section,
    parse_unified_diff,
    split_text,
)

_VERB = {
    PatchOperation.CREATE: "created",
    PatchOperation.UPDATE: "updated",
    PatchOperation.DELETE: "deleted",
}


# ------------------------------------------------------------------------------- apoio


def _cancel(context: HandlerContext) -> None:
    if context.is_cancelled():
        raise PathOperationFailed(PathFailure.CANCELLED)


def _denied(rule_id: str, subject: str | None = None) -> Outcome:
    return Outcome(ToolStatus.DENIED, rule_id=rule_id, subject=subject)


def _error(code: str) -> Outcome:
    return Outcome(ToolStatus.ERROR, rule_id=code)


class _Abort(Exception):
    """Interna: encerra o preflight devolvendo ``outcome`` (negação de limite/marcador, ERROR)."""

    def __init__(self, outcome: Outcome) -> None:
        super().__init__(outcome.rule_id)
        self.outcome = outcome


@dataclass(slots=True)
class _Effects:
    """`touched` = alguma mudança desta operação **pode permanecer** no workspace.

    Só liga, nunca desliga, e só a partir do que a fase de efeitos **fez**:

    * CREATE — do `CreateMutationTrace` de `create_exclusive`: diretório criado (fica sempre) ou
      arquivo-alvo criado e **não** removido com prova por `discard_created`;
    * UPDATE — antes de `write_fd`, que trunca (uma falha dele não prova que nada mudou);
    * DELETE — depois de `delete_if_identity` concluir (o `unlink` é o último passo).

    Nenhuma observação do preflight (pai existente etc.) entra na conta. Superestimar é seguro:
    a nota diz "pode ter ocorrido", nunca o contrário.
    """

    touched: bool = False


@contextmanager
def _about(subject: str, effects: _Effects | None = None) -> Iterator[None]:
    """Toda falha ao tratar ``subject`` sobe como `OperationAborted` (com o caminho relativo
    para o journal e o estado de efeitos). `_Abort` passa direto."""
    try:
        yield
    except _Abort:
        raise
    except PatchError as error:
        raise OperationAborted(
            ToolError(error.code),
            subject=subject,
            effects_applied=effects.touched if effects else False,
        ) from None
    except Exception as error:
        raise OperationAborted(
            error, subject=subject, effects_applied=effects.touched if effects else False
        ) from None


def _order_key(relative: str) -> bytes:
    return relative.encode("utf-8", "surrogatepass")


def _collision_key(relative: str) -> tuple[str, ...]:
    """Chave **conservadora** de alvo lógico: NFC + `casefold`, por componente, em todo SO.

    No Windows (e no macOS) `Foo.py` e `foo.py` são o mesmo arquivo; um patch que mexesse nos
    dois faria duas mutações do mesmo objeto. A V1 recusa a combinação em qualquer plataforma.
    """
    return tuple(unicodedata.normalize("NFC", part).casefold() for part in relative.split("/"))


# ------------------------------------------------------------------------------ o plano


@dataclass(frozen=True, slots=True, repr=False)
class PatchFilePlan:
    """O que o preflight provou para **um** arquivo. Interno: nunca vai ao provider.

    `facts` carrega caminhos absolutos — por isso o `repr` mostra só operação e caminho relativo.
    A linha de base é guardada como tamanho + SHA-256 (o conteúdo é relido na fase de efeitos
    **pelo handle** e comparado; mtime/tamanho sozinhos não bastam).
    """

    operation: PatchOperation
    relative: str
    facts: PathFacts
    result: bytes
    baseline_size: int = 0
    baseline_sha256: bytes = b""
    baseline_identity: ObjectIdentity | None = None

    def __repr__(self) -> str:
        return f"<PatchFilePlan {self.operation.value} {self.relative!r}>"

    def matches_baseline(self, data: bytes) -> bool:
        return len(data) == self.baseline_size and (
            hashlib.sha256(data).digest() == self.baseline_sha256
        )


# ----------------------------------------------------------------------------- preflight


@dataclass(frozen=True, slots=True)
class _Target:
    section: FileSection
    facts: PathFacts
    relative: str


def _authorize_all(context: HandlerContext, sections: tuple[FileSection, ...]) -> list[_Target]:
    """A política de path de **todos** os arquivos antes de qualquer leitura de conteúdo.

    A primeira negação (`PathAccessDenied`) ou falha técnica de inspeção (`InspectionFailed`)
    sobe na hora: o patch inteiro é bloqueado e os caminhos seguintes nem são avaliados.
    """
    targets: list[_Target] = []
    for section in sections:
        _cancel(context)
        facts = _authorize(context, section.path, PathIntent.WRITE)
        with _about(section.path):
            relative = relative_target(facts, context.root)
        targets.append(_Target(section, facts, relative))
    return targets


def _reject_collisions(targets: list[_Target]) -> None:
    keys: list[tuple[str, ...]] = []
    identities: set[ObjectIdentity] = set()
    for target in targets:
        key = _collision_key(target.relative)
        if key in keys:
            raise _Abort(_error(INVALID_PATCH))
        keys.append(key)
        identity = target.facts.target_identity
        if target.facts.exists and identity is not None and identity.is_verifiable:
            if identity in identities:
                raise _Abort(_error(INVALID_PATCH))  # hard link: dois nomes, um objeto
            identities.add(identity)
    for key in keys:
        for other in keys:
            if len(key) < len(other) and other[: len(key)] == key:
                raise _Abort(_error(INVALID_PATCH))  # um alvo é ancestral de outro


def _read_baseline(context: HandlerContext, target: _Target) -> tuple[bytes, ObjectIdentity]:
    """Leitura **interna** pela pipeline do `ReadFile` (open → post-open → read). O conteúdo nunca
    vai ao provider, então **não** entra em `files_read`.

    Teto derivado, não normativo novo: cada linha removida custa ao menos os mesmos bytes no patch,
    então uma linha de base acima de `resultado + patch` só poderia dar um resultado acima do teto
    por arquivo — negado aqui com a mesma regra, sem ler o arquivo inteiro para a memória.
    """
    limits = context.limits
    cap = limits.apply_patch_result_file_bytes + limits.apply_patch_input_bytes
    deny = _Abort(_denied("limit.apply_patch_result_file_bytes", target.relative))
    with open_existing(target.facts, context.root, is_cancelled=context.is_cancelled) as opened:
        decision = decide_post_open(opened.facts, policy=context.policy)
        if not decision.allow:
            raise PathAccessDenied(decision)
        if opened.size > cap:
            raise deny
        data = read_fd(opened.fd, cap, is_cancelled=context.is_cancelled)
        identity = opened.facts.post_open_identity
    if len(data) > cap:
        raise deny
    assert identity is not None  # decide_post_open nega sem identidade pós-abertura
    return data, identity


def _plan(context: HandlerContext, target: _Target) -> PatchFilePlan:
    section = target.section
    with _about(target.relative):
        _cancel(context)
        if section.operation is PatchOperation.CREATE:
            if target.facts.exists:
                raise ToolError("already_exists")
            probe_create(target.facts, context.root)
            baseline = EMPTY_TEXT
            data, identity = b"", None
        else:
            if not target.facts.exists:
                raise ToolError("not_found")
            data, identity = _read_baseline(context, target)
            text = _decode_text(data)
            if text is None:
                raise ToolError("not_text")
            baseline = split_text(text)

        result_text = apply_section(baseline, section).render()
        result = result_text.encode("utf-8")
        if len(result) > context.limits.apply_patch_result_file_bytes:
            raise _Abort(_denied("limit.apply_patch_result_file_bytes", target.relative))
        if b"\x00" in result:
            raise ToolError(INVALID_PATCH)  # defensivo: patch e linha de base já sem NUL
        if contains_redaction_marker(result_text):
            # Nem um conteúdo previamente redigido persistido como se fosse o original.
            raise _Abort(_denied("content.redaction_marker", target.relative))
    return PatchFilePlan(
        operation=section.operation,
        relative=target.relative,
        facts=target.facts,
        result=result,
        baseline_size=len(data),
        baseline_sha256=hashlib.sha256(data).digest(),
        baseline_identity=identity,
    )


# ------------------------------------------------------------------------------- efeitos


def _create(context: HandlerContext, plan: PatchFilePlan, effects: _Effects) -> None:
    # O trace é preenchido **dentro** de `create_exclusive`, syscall a syscall, e sobrevive à
    # exceção: é ele — não o que o preflight viu — que diz o que mudou.
    trace = CreateMutationTrace()
    target_removed = False
    try:
        created = create_exclusive(
            plan.facts,
            context.root,
            is_cancelled=context.is_cancelled,
            mutation_trace=trace,
        )
        try:
            decision = decide_post_create(created.facts, policy=context.policy)
            if not decision.allow:
                # Remove o vazio **só** se a identidade ainda for a criada e ele seguir vazio.
                target_removed = discard_created(created)
                raise PathAccessDenied(decision)
            try:
                write_fd(created.fd, plan.result, truncate=False, is_cancelled=context.is_cancelled)
            except PathOperationFailed:
                # Só remove se ainda vazio; um parcial permanece (residual declarado).
                target_removed = discard_created(created)
                raise
        finally:
            created.close()
    finally:
        # Antes de qualquer exceção sair daqui (e de `_about` ler `touched`): diretório criado
        # sempre permanece; o alvo criado permanece salvo remoção comprovada.
        residual_target = trace.created_target and not target_removed
        if trace.created_parent_count or residual_target:
            effects.touched = True


def _update(context: HandlerContext, plan: PatchFilePlan, effects: _Effects) -> None:
    with open_existing(
        plan.facts,
        context.root,
        for_update=True,
        readable=True,
        is_cancelled=context.is_cancelled,
    ) as opened:
        decision = decide_post_open(opened.facts, policy=context.policy)
        if not decision.allow:
            raise PathAccessDenied(decision)  # nada foi truncado
        # Mesma identidade não prova mesmo conteúdo: relê **pelo handle** antes de truncar.
        current = read_fd(opened.fd, plan.baseline_size, is_cancelled=context.is_cancelled)
        if not plan.matches_baseline(current):
            raise ToolError(PATCH_CONFLICT)  # mudou depois do preflight: nada é escrito
        _cancel(context)
        effects.touched = True
        write_fd(opened.fd, plan.result, truncate=True, is_cancelled=context.is_cancelled)


def _delete(context: HandlerContext, plan: PatchFilePlan, effects: _Effects) -> None:
    with open_existing(plan.facts, context.root, is_cancelled=context.is_cancelled) as opened:
        decision = decide_post_open(opened.facts, policy=context.policy)
        if not decision.allow:
            raise PathAccessDenied(decision)
        current = read_fd(opened.fd, plan.baseline_size, is_cancelled=context.is_cancelled)
        if not plan.matches_baseline(current):
            raise ToolError(PATCH_CONFLICT)
        identity = opened.facts.post_open_identity
    # Residual declarado: entre esta comparação e o `unlink` de `delete_if_identity` ainda há uma
    # janela (o handle precisa ser fechado antes de remover, no Windows). A identidade é
    # revalidada lá; o conteúdo, não.
    _cancel(context)
    delete_if_identity(
        plan.facts,
        context.root,
        expected_identity=identity,
        is_cancelled=context.is_cancelled,
    )
    effects.touched = True


_EFFECT = {
    PatchOperation.CREATE: _create,
    PatchOperation.UPDATE: _update,
    PatchOperation.DELETE: _delete,
}


# ---------------------------------------------------------------------------- a operação


def apply_patch(context: HandlerContext, request: ApplyPatch) -> Outcome:
    """Aplica um unified diff do subset V1 inteiro, ou nada antes do primeiro efeito."""
    _cancel(context)
    limits = context.limits
    try:
        size = len(request.patch.encode("utf-8"))
    except UnicodeEncodeError:
        return _error(INVALID_PATCH)
    if size > limits.apply_patch_input_bytes:
        return _denied("limit.apply_patch_input_bytes")
    if contains_redaction_marker(request.patch):
        return _denied("content.redaction_marker")
    try:
        parsed = parse_unified_diff(request.patch, checkpoint=lambda: _cancel(context))
    except PatchError as error:
        return _error(error.code)
    if len(parsed.sections) > limits.apply_patch_max_files:
        return _denied("limit.apply_patch_max_files")
    if parsed.hunk_count > limits.apply_patch_max_hunks:
        return _denied("limit.apply_patch_max_hunks")

    try:
        targets = _authorize_all(context, parsed.sections)
        _reject_collisions(targets)
        targets.sort(key=lambda target: _order_key(target.relative))
        plans = [_plan(context, target) for target in targets]
        prepared = _prepare_summary(context, plans)
    except _Abort as abort:
        return abort.outcome

    _cancel(context)  # último ponto sem efeito nenhum
    effects = _Effects()
    for plan in plans:
        with _about(plan.relative, effects):
            _cancel(context)
            _EFFECT[plan.operation](context, plan, effects)
    # Sucesso: exatamente a saída validada antes dos efeitos — nada é reconstruído nem redigido.
    return Outcome(ToolStatus.OK, prepared=prepared)


def _prepare_summary(context: HandlerContext, plans: list[PatchFilePlan]) -> PreparedToolOutput:
    """O summary **exato** de um sucesso, finalizado pela fronteira de saída do executor.

    Depende só de operação, caminho relativo e ordem canônica — tudo conhecido no preflight. A
    redação e o teto são os do executor (`HandlerContext.prepare_output`): aqui não há segundo
    redator nem segunda conta de bytes. Não cabe → `DENIED limit.tool_result_content_bytes` antes
    do primeiro efeito.
    """
    if context.prepare_output is None:
        raise _Abort(_error("internal_error"))  # sem a fronteira, nada de efeito
    raw = "\n".join(f"{_VERB[plan.operation]}\t{plan.relative}" for plan in plans)
    prepared = context.prepare_output(raw)
    if prepared is None:
        raise _Abort(_denied("limit.tool_result_content_bytes"))
    return prepared


__all__ = ["PatchFilePlan", "apply_patch"]
