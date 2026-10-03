"""As quatro operações de arquivo do Developer (E7.5-B): `ReadFile`, `ListDirectory`,
`SearchText` e `WriteFile`.

O executor **coordena a decisão**; `path_runtime` **observa fatos e faz o IO autorizado**. Cada
operação percorre, nesta ordem:

```
cancelamento → limite/entrada → sintaxe (+ regra mediada de `.git`) → inspect → decide_path
→ primitiva de path_runtime → decisão pós-open / pós-create → IO → limite → Outcome
```

Nada aqui toca o filesystem diretamente (`open`, `Path.read_*`, `write_*`, `unlink`, `os.*`):
toda leitura e escrita passa por `open_existing`, `read_fd`, `create_exclusive`, `write_fd` e
`list_directory`. A **redação** acontece uma só vez, na fronteira de saída do executor — o
casamento, a comparação e o IO usam o dado cru. Os motivos são sempre frases fixas por `rule_id`.

Negação de política levanta `PathAccessDenied` (carrega a `SafetyDecision` do kernel);
falha técnica é `PathOperationFailed`; falha de integridade é `PathIntegrityViolation`. O
executor converte as três.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.path_runtime import (
    CreatedFile,
    DirectoryEntryFacts,
    EntryKind,
    PathAccessDenied,
    PathFailure,
    PathOperationFailed,
    create_exclusive,
    discard_created,
    inspect,
    list_directory,
    open_existing,
    read_fd,
    relative_target,
    write_fd,
)
from app.safety import (
    contains_redaction_marker,
    decide_path,
    decide_post_create,
    decide_post_open,
    prevalidate_mediated_path,
)
from app.safety.paths import PathIntent
from app.safety.types import PathFacts
from app.tool_executor.contracts import (
    ListDirectory,
    ReadFile,
    SearchText,
    ToolStatus,
    WriteFile,
)
from app.tool_executor.outcome import HandlerContext, InspectionFailed, Outcome, OutputFragment
from app.tool_executor.reasons import TECHNICAL_RULES

# ------------------------------------------------------------------------------- apoio comum


def _cancel(context: HandlerContext) -> None:
    """Cooperativo: o token do run (já adaptado e fail closed) cancelou?"""
    if context.is_cancelled():
        raise PathOperationFailed(PathFailure.CANCELLED)


def _denied(rule_id: str, subject: str) -> Outcome:
    return Outcome(ToolStatus.DENIED, rule_id=rule_id, subject=subject)


def _error(code: str) -> Outcome:
    return Outcome(ToolStatus.ERROR, rule_id=code)


def _authorize(context: HandlerContext, raw_path: str, intent: PathIntent) -> PathFacts:
    """Sintaxe + regra mediada de `.git` + inspeção + `decide_path`. Levanta a negação.

    A inspeção usa `tolerate_absent_tail`: **ausência comprovada** não é `UNKNOWN`. Sem isso, um
    alvo inexistente seria negado por `path.symlink_unverified` (e um `.env` inexistente negaria
    por um motivo diferente de um existente — um oráculo de existência). Com isso, o inexistente
    chega ao kernel como qualquer outro: segredo/contenção negam do mesmo jeito, e um alvo
    comum e ausente vira `ERROR not_found` na abertura.

    A flag **não** significa "aceitar alvo ausente": só separa ausência comprovada de falha
    ambígua. Falha técnica de coleta (`inspection_error`) → `InspectionFailed` (`ERROR
    path_inspection_failed`), nunca uma negação de política.
    """
    syntax = prevalidate_mediated_path(raw_path, policy=context.policy)
    if not syntax.allow:
        raise PathAccessDenied(syntax)
    return _decide(context, inspect(raw_path, context.root.path, tolerate_absent_tail=True), intent)


def _decide(context: HandlerContext, facts: PathFacts, intent: PathIntent) -> PathFacts:
    if _inspection_failed(facts):
        raise InspectionFailed
    decision = decide_path(facts, policy=context.policy, intent=intent)
    if not decision.allow:
        raise PathAccessDenied(decision)
    return facts


def _authorize_base(context: HandlerContext, raw_path: str | None) -> PathFacts:
    """`None` é a raiz da execution workspace: **não** passa `"."` pela gramática de path."""
    if raw_path is not None:
        return _authorize(context, raw_path, PathIntent.READ)
    facts = inspect(".", context.root.path, tolerate_absent_tail=True)
    return _decide(context, facts, PathIntent.READ)


def _inspection_failed(facts: PathFacts) -> bool:
    """A **coleta** de fatos falhou tecnicamente? A fonte é **explícita**: `inspection_error`.

    `path_runtime.inspect` preenche `inspection_error` com um código interno fixo quando um
    `stat`/`lstat` necessário falha por um motivo que não é ausência comprovada (E/S, permissão,
    `OSError` ambíguo). Aqui **não** se infere falha a partir de combinações de `Tri.UNKNOWN`: um
    fato individual `UNKNOWN` (propriedade não comprovável) continua sendo política fail closed.
    """
    return facts.inspection_error is not None


def _visible_facts(context: HandlerContext, relative: str) -> PathFacts | None:
    """O mesmo pipeline de path de qualquer leitura, aplicado a **uma entrada**.

    Ordem, por entrada:

    1. sintaxe + regra mediada de `.git` → `DENY` comprovado: **registra**, omite;
    2. `inspect`; se a **coleta** falhou → `InspectionFailed` (a operação vira `ERROR`: não se
       pode afirmar que a listagem/busca é completa; **nada** é registrado como política);
    3. `decide_path` → `DENY` comprovado: **registra a primeira negação**, omite; `ALLOW`: devolve.

    `None` = omitida por política (e a negação já está no journal). Nunca "falha técnica →
    negação genérica → omissão".
    """
    syntax = prevalidate_mediated_path(relative, policy=context.policy)
    if not syntax.allow:
        context.record_denial(syntax)
        return None
    facts = inspect(relative, context.root.path, tolerate_absent_tail=True)
    if _inspection_failed(facts):
        raise InspectionFailed
    decision = decide_path(facts, policy=context.policy, intent=PathIntent.READ)
    if decision.allow:
        return facts
    if decision.rule_id in TECHNICAL_RULES:  # defesa: regra técnica nunca é política
        raise InspectionFailed
    context.record_denial(decision)
    return None


def _join(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


# ----------------------------------------------------------------------------------- ReadFile


def read_file(context: HandlerContext, request: ReadFile) -> Outcome:
    """Lê **um arquivo de texto UTF-8** inteiro, ou nega/erra — nunca entrega parcial."""
    _cancel(context)
    facts = _authorize(context, request.path, PathIntent.READ)
    limit = context.limits.read_file_bytes
    with open_existing(facts, context.root, is_cancelled=context.is_cancelled) as opened:
        decision = decide_post_open(opened.facts, policy=context.policy)
        if not decision.allow:
            raise PathAccessDenied(decision)
        if opened.size > limit:  # antes de ler: nada de arquivo arbitrariamente grande
            return _denied("limit.read_file_bytes", request.path)
        data = read_fd(opened.fd, limit, is_cancelled=context.is_cancelled)
    if len(data) > limit:  # cresceu durante a leitura
        return _denied("limit.read_file_bytes", request.path)
    text = _decode_text(data)
    if text is None:
        return _error("not_text")
    return Outcome(
        ToolStatus.OK,
        content=text,
        files_read=(relative_target(facts, context.root),),
    )


def _decode_text(data: bytes) -> str | None:
    """UTF-8 **estrito** e sem NUL. NUL é binário por convenção (V1 não tem suporte binário)."""
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


# ------------------------------------------------------------------------------ ListDirectory

_KIND_LABEL = {
    EntryKind.FILE: "file",
    EntryKind.DIRECTORY: "directory",
    EntryKind.OTHER: "other",
}


def list_directory_entries(context: HandlerContext, request: ListDirectory) -> Outcome:
    """Nomes e tipos, ordenados, **sem** revelar o que a política nega.

    Formato (uma linha por entrada, sem metadado interno): `<tipo>\\t<caminho relativo>`, com
    tipo `file`, `directory` ou `other`. Entrada negada não aparece e só incrementa um contador
    genérico. O limite de 1000 vale para entradas **entregues**: as omitidas não consomem slot.
    """
    _cancel(context)
    facts = _authorize_base(context, request.path)
    base = relative_target(facts, context.root)
    listing = list_directory(facts, context.root, is_cancelled=context.is_cancelled)

    shown: list[tuple[str, str]] = []
    omitted = 0
    for entry in listing.entries:
        _cancel(context)
        relative = _join(base, entry.name)
        if _visible_facts(context, relative) is None:
            omitted += 1
            continue
        shown.append((_KIND_LABEL[entry.kind], relative))

    limit = context.limits.list_directory_entries
    delivered = shown[:limit]
    remaining = len(shown) - len(delivered)

    # Cada entrada e cada nota é um fragmento **inteiro**; nenhuma carrega `files_read`.
    fragments = [OutputFragment(f"{kind}\t{path}") for kind, path in delivered]
    if omitted:
        fragments.append(OutputFragment(f"[{omitted} entries omitted by policy]"))
    if remaining:
        fragments.append(OutputFragment(f"[truncated: {remaining} more entries not shown]"))
    if not fragments:
        fragments.append(OutputFragment("[empty]"))
    return Outcome(ToolStatus.OK, fragments=tuple(fragments))


# ------------------------------------------------------------------------------- SearchText


class _Budget:
    """Cancelamento do run **e** prazo de 20 s, num único `Callable[[], bool]` para as primitivas.

    Cancelar e estourar o prazo são desfechos diferentes (`ERROR cancelled` × `ERROR timeout`);
    `timed_out` os distingue depois que a primitiva levantou `CANCELLED`.
    """

    def __init__(self, context: HandlerContext) -> None:
        self._context = context
        self._deadline = time.monotonic() + context.limits.search_deadline_s
        self.timed_out = False

    def stop(self) -> bool:
        if self._context.is_cancelled():
            return True
        if time.monotonic() >= self._deadline:
            self.timed_out = True
            return True
        return False

    def check(self) -> None:
        if self.stop():
            raise PathOperationFailed(PathFailure.CANCELLED)


@dataclass(slots=True)
class _Search:
    context: HandlerContext
    query: str
    budget: _Budget
    matches: list[OutputFragment] = field(default_factory=list)
    policy_skipped: int = 0
    files_skipped: int = 0
    files_visited: int = 0
    truncated_matches: bool = False
    file_limit_hit: bool = False
    done: bool = False


def _entry_order_key(name: str, kind: EntryKind) -> bytes:
    """Ordem **global** de caminho: um diretório conta com `/` no fim, então `a.txt` < `a/x`."""
    suffix = b"/" if kind is EntryKind.DIRECTORY else b""
    return name.encode("utf-8", "surrogatepass") + suffix


def _snippet(line: str, column0: int, query_length: int, limit: int) -> str:
    """Até ``limit`` caracteres da linha, **contendo** o casamento (a consulta cabe: ≤ 256)."""
    if len(line) <= limit:
        return line
    start = max(0, column0 - (limit - query_length) // 2)
    start = min(start, len(line) - limit)
    return line[start : start + limit]


def _scan_text(state: _Search, relative: str, text: str) -> None:
    limits = state.context.limits
    query = state.query
    for number, raw_line in enumerate(text.split("\n"), start=1):
        line = raw_line[:-1] if raw_line.endswith("\r") else raw_line
        if query not in line:
            continue
        start = 0
        while True:
            position = line.find(query, start)
            if position < 0:
                break
            if len(state.matches) >= limits.search_max_matches:
                state.truncated_matches = True
                state.done = True
                return
            snippet = _snippet(line, position, len(query), limits.search_snippet_chars)
            state.matches.append(
                OutputFragment(f"{relative}:{number}:{position + 1}:{snippet}", (relative,))
            )
            start = position + len(query)


def _scan_file(state: _Search, facts: PathFacts, relative: str, known_size: int | None) -> None:
    context = state.context
    limits = context.limits
    if state.files_visited >= limits.search_max_files:
        state.file_limit_hit = True
        state.done = True
        return
    state.files_visited += 1
    if known_size is not None and known_size > limits.search_file_bytes:
        state.files_skipped += 1
        return
    with open_existing(facts, context.root, is_cancelled=state.budget.stop) as opened:
        decision = decide_post_open(opened.facts, policy=context.policy)
        if not decision.allow:
            raise PathAccessDenied(decision)
        if opened.size > limits.search_file_bytes:
            state.files_skipped += 1
            return
        data = read_fd(opened.fd, limits.search_file_bytes, is_cancelled=state.budget.stop)
    if len(data) > limits.search_file_bytes:  # cresceu durante a leitura
        state.files_skipped += 1
        return
    text = _decode_text(data)
    if text is None:
        state.files_skipped += 1
        return
    _scan_text(state, relative, text)


def _walk(state: _Search, directory_facts: PathFacts, relative_dir: str) -> None:
    listing = list_directory(directory_facts, state.context.root, is_cancelled=state.budget.stop)
    _walk_entries(state, listing.entries, relative_dir)


def _walk_entries(
    state: _Search, entries: Iterable[DirectoryEntryFacts], relative_dir: str
) -> None:
    ordered = sorted(entries, key=lambda e: _entry_order_key(e.name, e.kind))
    for entry in ordered:
        if state.done:
            return
        state.budget.check()
        if entry.kind is EntryKind.OTHER:
            state.files_skipped += 1
            continue
        relative = _join(relative_dir, entry.name)
        facts = _visible_facts(state.context, relative)
        if facts is None:
            state.policy_skipped += 1
            continue
        canonical = relative_target(facts, state.context.root)
        if entry.kind is EntryKind.DIRECTORY:
            _walk(state, facts, canonical)
        else:
            _scan_file(state, facts, canonical, entry.size)


def search_text(context: HandlerContext, request: SearchText) -> Outcome:
    """Busca **literal**, sensível a caixa, sem regex, dentro do workspace.

    O percurso é determinístico (ordem global de caminho, por bytes UTF-8), nunca segue link ou
    reparse e só abre o que a política deixa ler. Saída: `<caminho>:<linha>:<coluna>:<trecho>`.
    """
    _cancel(context)
    limits = context.limits
    if len(request.query) > limits.search_query_chars:
        return _denied("limit.search_query_chars", "query")
    if "\n" in request.query or "\r" in request.query:
        return _error("invalid_query")
    facts = _authorize_base(context, request.path)
    base = relative_target(facts, context.root)

    budget = _Budget(context)
    state = _Search(context=context, query=request.query, budget=budget)
    try:
        budget.check()  # antes do percurso (prazo 0 ou token já cancelado)
        try:
            listing = list_directory(facts, context.root, is_cancelled=budget.stop)
        except PathOperationFailed as failed:
            if failed.category is not PathFailure.NOT_DIRECTORY:
                raise
            _scan_file(state, facts, base, None)  # `path` aponta para um arquivo
        else:
            _walk_entries(state, listing.entries, base)
    except PathOperationFailed as failed:
        timed_out = failed.category is PathFailure.CANCELLED and budget.timed_out
        if timed_out and not context.is_cancelled():
            return _error("timeout")  # o prazo estourou (e o run **não** foi cancelado)
        raise

    notes: list[str] = []
    if state.policy_skipped:
        notes.append(f"[{state.policy_skipped} paths skipped by policy]")
    if state.files_skipped:
        notes.append(
            f"[{state.files_skipped} files skipped: not searchable text or larger than "
            f"{limits.search_file_bytes} bytes]"
        )
    if state.truncated_matches:
        notes.append(f"[results truncated: more than {limits.search_max_matches} matches]")
    if state.file_limit_hit:
        notes.append(f"[search incomplete: file limit of {limits.search_max_files} reached]")

    # Cada match é **um** fragmento com a sua proveniência (`files_read=(caminho,)`); as notas não
    # têm. `files_read` sai dos fragmentos que o executor de fato entregar, depois da redação e do
    # teto final — nunca de um parse do texto.
    fragments = list(state.matches)
    if not fragments:
        fragments.append(OutputFragment("[no matches]"))
    fragments.extend(OutputFragment(note) for note in notes)
    return Outcome(ToolStatus.OK, fragments=tuple(fragments))


# ------------------------------------------------------------------------------- WriteFile


def write_file(context: HandlerContext, request: WriteFile) -> Outcome:
    """Cria (`O_EXCL`) ou atualiza (sem truncar antes da decisão) **um arquivo regular**."""
    _cancel(context)
    try:
        data = request.content.encode("utf-8")
    except UnicodeEncodeError:
        return _error("not_text")
    if len(data) > context.limits.write_file_bytes:
        return _denied("limit.write_file_bytes", request.path)
    if contains_redaction_marker(request.content):
        # Conteúdo redigido que o provider tentasse persistir como se fosse o original.
        return _denied("content.redaction_marker", request.path)

    # **Uma** inspeção mediada, no mesmo modo das demais operações: a flag só distingue ausência
    # comprovada de falha ambígua (que vira `ERROR path_inspection_failed` **antes** de qualquer
    # abertura). CREATE × UPDATE sai dos fatos observados; um arquivo que aparecer depois da
    # inspeção esbarra no `O_EXCL` (`ALREADY_EXISTS`), um que sumir vira `NOT_FOUND` na abertura.
    facts = _authorize(context, request.path, PathIntent.WRITE)
    relative = relative_target(facts, context.root)
    if not facts.exists:
        _create(context, facts, data)
        return Outcome(ToolStatus.OK, content=f"created {relative} ({len(data)} bytes)")
    _update(context, facts, data)
    return Outcome(ToolStatus.OK, content=f"updated {relative} ({len(data)} bytes)")


def _create(context: HandlerContext, facts: PathFacts, data: bytes) -> None:
    created = create_exclusive(facts, context.root, is_cancelled=context.is_cancelled)
    try:
        _write_created(context, created, data)
    finally:
        created.close()


def _write_created(context: HandlerContext, created: CreatedFile, data: bytes) -> None:
    decision = decide_post_create(created.facts, policy=context.policy)
    if not decision.allow:
        # Remove o vazio **só** se a identidade ainda for provadamente a criada e ele seguir vazio.
        discard_created(created)
        raise PathAccessDenied(decision)
    try:
        write_fd(created.fd, data, truncate=False, is_cancelled=context.is_cancelled)
    except PathOperationFailed:
        discard_created(created)  # só remove se ainda vazio; parcial permanece (declarado)
        raise


def _update(context: HandlerContext, facts: PathFacts, data: bytes) -> None:
    with open_existing(
        facts, context.root, for_update=True, is_cancelled=context.is_cancelled
    ) as opened:
        decision = decide_post_open(opened.facts, policy=context.policy)
        if not decision.allow:
            raise PathAccessDenied(decision)  # nada foi truncado
        write_fd(opened.fd, data, truncate=True, is_cancelled=context.is_cancelled)


__all__ = [
    "list_directory_entries",
    "read_file",
    "search_text",
    "write_file",
]
