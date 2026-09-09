"""Erros de domínio do Context Registry.

Mesmo desenho de `app.workspace.errors` ([01] §2): a camada de serviço levanta
exclusivamente estas exceções, o `status_code` mora aqui, e a camada HTTP
(`app.api.context`) só as deixa subir para o handler registrado em `app.main`. O router
não conhece status de erro e não tem árvore de `isinstance`.
"""

from __future__ import annotations

from app.safety.types import SafetyDecision


class ContextError(Exception):
    """Base de todo erro de domínio do Context Registry.

    ``code`` é o slug estável de `{code, message}` ([06] §2); ``status_code`` é o HTTP.
    """

    code: str = "context_error"
    status_code: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidContextEntry(ContextError):
    """Campo de entrada malformado: `title` vazio ou longo demais, `body` vazio, `tags` ou
    `structured` fora do formato JSON canonizável. **422** — entrada malformada, não
    conflito.

    O invariante vive aqui, e não só na coluna e no schema Pydantic, pelo motivo de
    E3-AUD-007: o SQLite não recusa um `VARCHAR` longo, e um chamador interno futuro não
    passa pelo Pydantic.
    """

    code = "invalid_context_entry"
    status_code = 422


class InvalidSourceRefs(ContextError):
    """Algum `source_ref` foi recusado — pelo envelope de caminho, pela gramática de glob
    ou por alcançar um segredo depois de expandir. **422**.

    ``decision`` carrega a `SafetyDecision` **verbatim** de quem negou. O `rule_id` diz qual
    camada decidiu: `source_ref.*` é o envelope (`app.safety`), `source_ref_expansion.*` é o
    expansor canônico. A UI mostra o motivo; nada aqui reescreve a decisão.
    """

    code = "invalid_source_refs"
    status_code = 422

    def __init__(self, message: str, *, decision: SafetyDecision) -> None:
        super().__init__(message)
        self.decision = decision
        self.rule_id = decision.rule_id


class ContextEntryNotFound(ContextError):
    """Nenhuma `ContextRegistryEntry` com o `id` pedido. **404**."""

    code = "context_entry_not_found"
    status_code = 404
