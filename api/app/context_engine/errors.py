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


class RedactedContentRejected(ContextError):
    """O campo enviado carrega um **marcador de redação**. **422** — nada é persistido.

    A Camada 3 de [04] §5 redige o que **sai**. Quem edita uma entrada no navegador vê a
    projeção redigida, e um `PATCH` que devolva esse texto ao backend persistiria o
    marcador como se fosse conteúdo autoral: `«redigido»` substituiria para sempre o trecho
    que ele estava escondendo, e o `content_hash` passaria a atestar o texto mutilado
    (E6-AUD4-004).

    A recusa é a única resposta correta, porque o backend **não pode** saber o que estava
    debaixo do marcador — ele o gerou sem guardar o original. Quem quer editar o conteúdo
    cru pede `POST /api/context/{entry_id}/edit-view`, que existe exatamente para isso.

    O preço é recusar um texto que legitimamente contenha `«redigido»` — alguém
    documentando este mecanismo, por exemplo. É um preço aceito de propósito: o modo de
    falha do outro lado é silencioso e destrutivo, e este é ruidoso e reversível.
    """

    code = "redacted_content_rejected"
    status_code = 422


class ContextChanged(ContextError):
    """A entrada mudou entre a leitura de edição e o `PATCH`. **409** — nada é sobrescrito.

    Concorrência **otimista** ([06] §2): a entrada não é travada em momento nenhum. O
    cliente lê `content_hash` junto com o conteúdo cru (`edit-view`) e o devolve como
    `expected_content_hash`; se o hash atual divergir, alguém escreveu no meio e a edição
    é recusada em vez de apagar o trabalho do outro.

    Travar seria pior num workspace local: um lock esquecido por um editor fechado deixaria
    a entrada inacessível, e não há sessão nem dono para expirá-lo.
    """

    code = "context_changed"
    status_code = 409


class ContextEntryUnreadable(ContextError):
    """A entrada está gravada numa forma que a projeção de edição não consegue ler. **500**.

    Acontece com documento legado ou escrito por outro caminho — `structured` guardado como
    lista, por exemplo: JSON válido para a coluna, inválido para o modelo que exige objeto.
    As rotas normais de escrita não aceitam essa forma.

    Existe para que esse caso **não** vire exceção não tratada (E6-AUD5-007). Uma
    `ValidationError` do Pydantic subindo até o canal de exceções do servidor leva o
    `input_value` junto — isto é, o conteúdo autoral cru — para o log do Uvicorn, um canal
    que não passa pelo boundary de [04] §5. A mensagem daqui é constante e nomeia o campo,
    nunca o valor.
    """

    code = "context_entry_unreadable"
    status_code = 500


class ContextEntryNotFound(ContextError):
    """Nenhuma `ContextRegistryEntry` com o `id` pedido. **404**."""

    code = "context_entry_not_found"
    status_code = 404
