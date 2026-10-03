"""Frases fixas por `rule_id` (E7.5-A): o **único** texto que sai do executor em negação/erro.

O que chega ao provider (e ao `DecisionJournal`) nunca é `decision.reason` cru, `str(OSError)`,
`stderr`, traceback nem caminho absoluto: `decision.reason` de algumas regras interpola o erro
do SO (que carrega o caminho da worktree). Aqui cada regra tem uma frase **fixa**, escrita
sem dado de runtime.

Também mora aqui a distinção `DENIED` × `ERROR` ([E7.5] taxonomia):

* `DENIED` — a operação foi **bloqueada** por safety/capability/limite;
* `ERROR` — foi **autorizada**, mas falhou tecnicamente (alvo inexistente, é diretório, IO,
  cancelamento, executor encerrado…).

Uma regra de path cuja decisão é, na verdade, "a inspeção técnica falhou"
(`path.inspection_failed`…) é `ERROR`, ainda que o kernel a represente como negação fail-closed.
"""

from __future__ import annotations

from types import MappingProxyType

from app.path_runtime import IntegrityFailure
from app.tool_executor.contracts import DecisionCategory, ToolStatus

#: Regras do kernel que dizem "a coleta técnica de fatos falhou", não "a política negou".
TECHNICAL_RULES = frozenset(
    {
        "path.inspection_failed",
        "path.post_open_inspection_failed",
        "path.post_create_inspection_failed",
        "path.open_failed",
        "path.no_target",
    }
)

#: Códigos de `ERROR` do próprio executor (sem `rule_id` de política).
ERROR_PHRASES: MappingProxyType[str, str] = MappingProxyType(
    {
        "operation_not_implemented": "operação ainda não implementada",
        "journal_unavailable": "a trilha de decisões está indisponível; o executor foi suspenso",
        "executor_closed": "o executor deste run foi encerrado",
        "operation_cancelled": "operação cancelada",
        "workspace_unavailable": "a execution workspace não está disponível",
        "internal_error": "falha interna do executor",
        "not_found": "alvo não encontrado",
        "is_directory": "o alvo é um diretório",
        "not_directory": "o alvo não é um diretório",
        "not_regular": "o alvo não é um arquivo regular",
        "already_exists": "o alvo já existe",
        "io_error": "falha de entrada/saída",
        "not_text": "o conteúdo não é texto UTF-8 válido",
        "timeout": "a operação excedeu o prazo",
        "invalid_query": "a consulta não pode conter quebra de linha",
        "path_inspection_failed": (
            "não foi possível inspecionar um caminho; a operação não pode afirmar que é completa"
        ),
        "cancelled": "operação cancelada",
        # ApplyPatch (E7.5-C). Nenhuma frase cita linha do patch, conteúdo ou caminho.
        "invalid_patch": "o patch não segue o subset de unified diff aceito",
        "patch_conflict": "o patch não se aplica exatamente ao conteúdo atual",
        "unsupported_patch_format": "recurso de diff fora do subset suportado",
        "unsupported_text_format": "convenção de fim de linha não suportada",
        "patch_partial_apply": "falha técnica depois de o patch ter sido aplicado em parte",
        # Git mediado (E7.5-D). Técnicos: nenhum é decisão de política; nunca carregam stderr.
        "git_unavailable": "o git não está disponível para a leitura mediada",
        "git_timeout": "a leitura git excedeu o prazo",
        "git_output_unverifiable": "a saída do git não pôde ser verificada por completo",
        "git_ref_not_found": "a revisão pedida não existe",
        "git_object_unavailable": "objeto git indisponível localmente",
        "git_failed": "a leitura git falhou",
        "unsupported_git_tree_entry": "entrada da árvore git fora da superfície suportada",
    }
)

#: Sufixo **fixo** de todo resultado não-`OK` de uma operação que parou depois de efeitos de
#: filesystem (aplicação parcial do `ApplyPatch`). Genérico: não diz quais arquivos.
PARTIAL_APPLY_NOTE = "[patch partially applied before failure]"

#: Códigos que **continuam** o motivo depois de uma aplicação parcial (o resto das falhas
#: técnicas vira `patch_partial_apply`): cancelamento, poison do journal e erro interno.
PARTIAL_KEEPS_CODE = frozenset({"operation_cancelled", "journal_unavailable", "internal_error"})

_PATH_PHRASES = {
    "path.absolute_not_allowed": "caminho absoluto não é aceito",
    "path.alternate_data_stream": "componente com `:` (alternate data stream)",
    "path.ancestor_link_escapes_root": "um ancestral é link que sai da raiz",
    "path.ancestor_link_unverified": "estado de link dos ancestrais não verificado",
    "path.containment_unverified": "contenção do caminho não verificada",
    "path.control_char": "caminho com caractere de controle",
    "path.cross_volume": "alvo em volume diferente da raiz",
    "path.device_namespace": "device namespace não é aceito",
    "path.dot_git_denied": "componente `.git` não é acessível",
    "path.drive_relative": "caminho relativo a drive não é aceito",
    "path.empty": "caminho vazio",
    "path.escapes_root": "alvo fora da raiz do workspace",
    "path.home_reference": "referência ao diretório home não é aceita",
    "path.inspection_failed": "a inspeção do caminho falhou",
    "path.no_components": "caminho sem componente utilizável",
    "path.no_target": "alvo indisponível",
    "path.nul_byte": "caminho com byte nulo",
    "path.open_failed": "a abertura do alvo falhou",
    "path.integrity_unverifiable": "identidade ou cadeia não verificável; fail closed",
    "path.parent_changed": "diretório pai trocado durante a operação",
    "path.reparse_detected": "link/reparse detectado durante a revalidação",
    "path.parent_traversal": "componente `..` não é aceito",
    "path.post_create_inspection_failed": "a inspeção pós-criação falhou",
    "path.post_create_no_parent_baseline": "sem identidade do diretório pai para comparar",
    "path.post_create_not_regular": "o arquivo criado não é regular",
    "path.post_create_reparse": "o arquivo criado é link/reparse ou não foi verificado",
    "path.post_create_unverified": "identidade do arquivo criado indisponível",
    "path.post_open_escapes_root": "o objeto aberto está fora da raiz",
    "path.post_open_inspection_failed": "a inspeção pós-abertura falhou",
    "path.post_open_no_baseline": "sem identidade prévia para comparar",
    "path.post_open_unverified": "identidade pós-abertura indisponível",
    "path.relative_underivable": "não foi possível derivar o caminho relativo",
    "path.reparse_point_denied": "alvo é reparse point",
    "path.reparse_point_unverified": "estado de reparse point não verificado",
    "path.reserved_name": "nome de dispositivo reservado",
    "path.root_changed": "a raiz da execution workspace mudou",
    "path.root_relative": "caminho relativo à raiz não é aceito",
    "path.secret_denied": "caminho classificado como segredo",
    "path.short_name_alias": "possível alias 8.3 não é aceito",
    "path.symlink_denied": "alvo é symlink",
    "path.symlink_unverified": "estado de symlink não verificado",
    "path.junction_denied": "alvo é junction",
    "path.junction_unverified": "estado de junction não verificado",
    "path.toctou_recheck_failed": "objeto trocado entre a verificação e o uso",
    "path.too_long": "caminho excede o tamanho máximo",
    "path.trailing_dot_or_space": "componente termina com ponto ou espaço",
    "path.unc": "caminho UNC não é aceito",
}

_OTHER_PHRASES = {
    "tool.unknown_operation": "operação fora da superfície fechada",
    "capability.denied": "capability não concedida para esta operação",
    "limit.read_file_bytes": "arquivo acima do limite de leitura",
    "limit.git_show_path_bytes": "arquivo acima do limite de leitura do Git",
    "limit.write_file_bytes": "conteúdo acima do limite de escrita",
    "limit.apply_patch_input_bytes": "patch acima do limite de tamanho",
    "limit.apply_patch_max_files": "patch com arquivos demais",
    "limit.apply_patch_max_hunks": "patch com hunks demais",
    "limit.apply_patch_result_file_bytes": "arquivo resultante acima do limite",
    "limit.search_query_chars": "consulta acima do limite",
    "limit.tool_result_content_bytes": "resultado acima do limite de saída",
    "content.redaction_marker": "o conteúdo carrega um marcador de redação",
    "git.ref_outside_base_history": "revisão fora do histórico do base_commit do run",
}

#: Frase fixa de cada regra de **negação** conhecida (`rule_id` → texto).
DENIAL_PHRASES: MappingProxyType[str, str] = MappingProxyType({**_PATH_PHRASES, **_OTHER_PHRASES})

_TOCTOU_RULES = frozenset(
    {
        "path.toctou_recheck_failed",
        "path.parent_changed",
        "path.root_changed",
        "path.reparse_detected",
        "path.integrity_unverifiable",
    }
)
_OUT_OF_ROOT_RULES = frozenset(
    {
        "path.escapes_root",
        "path.ancestor_link_escapes_root",
        "path.cross_volume",
        "path.post_open_escapes_root",
    }
)

#: Falha de **integridade** observada por `path_runtime` (um fato) → a regra de política que o
#: executor lhe atribui. `path_runtime` não decide: quem escolhe a regra é esta camada.
INTEGRITY_RULES: MappingProxyType[IntegrityFailure, str] = MappingProxyType(
    {
        IntegrityFailure.ROOT_IDENTITY_CHANGED: "path.root_changed",
        IntegrityFailure.PARENT_IDENTITY_CHANGED: "path.parent_changed",
        IntegrityFailure.TARGET_IDENTITY_CHANGED: "path.toctou_recheck_failed",
        IntegrityFailure.REPARSE_DETECTED: "path.reparse_detected",
        IntegrityFailure.INTEGRITY_UNVERIFIABLE: "path.integrity_unverifiable",
        IntegrityFailure.TARGET_OUTSIDE_ROOT: "path.escapes_root",
    }
)


def rule_for_integrity(category: IntegrityFailure) -> str:
    """A regra de negação de uma falha de integridade. Exaustiva sobre `IntegrityFailure`."""
    return INTEGRITY_RULES[category]


class UnknownRule(KeyError):
    """Uma regra sem frase fixa: o executor a trata como erro interno, nunca como texto cru."""


def status_for_rule(rule_id: str) -> ToolStatus:
    """`ERROR` se a regra representa falha técnica de coleta; senão `DENIED`."""
    return ToolStatus.ERROR if rule_id in TECHNICAL_RULES else ToolStatus.DENIED


def phrase_for(rule_id: str) -> str:
    """A frase fixa de uma regra de negação, ou o código de erro do executor."""
    phrase = DENIAL_PHRASES.get(rule_id) or ERROR_PHRASES.get(rule_id)
    if phrase is None:
        raise UnknownRule(rule_id)
    return phrase


def reason_for(rule_id: str) -> str:
    """`"<rule_id>: <frase fixa>"`. Determinístico; nenhum dado de runtime."""
    return f"{rule_id}: {phrase_for(rule_id)}"


def category_of(rule_id: str, *, write: bool = False) -> DecisionCategory:
    """Categoria de negação (valores de `SafetyEventKind`). Levanta `UnknownRule` se ignota."""
    if rule_id not in DENIAL_PHRASES:
        raise UnknownRule(rule_id)
    if rule_id == "path.secret_denied" or rule_id.startswith("content."):
        return DecisionCategory.SECRET_ACCESS_BLOCKED
    if rule_id.startswith("limit."):
        return DecisionCategory.LIMIT_EXCEEDED
    if rule_id.startswith(("capability.", "tool.", "git.")):
        # `git.`: uma revisão fora do snapshot aprovado ampliaria a autoridade do run (E7.5-D).
        return DecisionCategory.CAPABILITY_DENIED
    if rule_id in _TOCTOU_RULES:
        return DecisionCategory.TOCTOU_RECHECK_FAILED
    if write and rule_id in _OUT_OF_ROOT_RULES:
        return DecisionCategory.OUT_OF_WORKTREE_WRITE
    return DecisionCategory.PATH_DENIED


__all__ = [
    "DENIAL_PHRASES",
    "ERROR_PHRASES",
    "INTEGRITY_RULES",
    "PARTIAL_APPLY_NOTE",
    "PARTIAL_KEEPS_CODE",
    "TECHNICAL_RULES",
    "UnknownRule",
    "category_of",
    "phrase_for",
    "reason_for",
    "rule_for_integrity",
    "status_for_rule",
]
