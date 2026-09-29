"""`PurgeTokenStore` — confirmação forte da purga ([02] §11).

[02](../../../docs/architecture/02-data-model.md) §11, regra 3: a purga "exige confirmação
forte — `confirm_phrase` ou token de purga de curta duração. A forma exata não é
implementada nesta fase; o contrato registra que a confirmação é obrigatória e **não pode
ser um simples parâmetro de query**". A E3 escolhe o **token de curta duração**.

Garantias, todas verificadas por `test_purge_tokens.py`:

* **Só memória.** Nunca gravado em disco, log ou banco. É um `dict` de processo e nada
  mais; o `__repr__` jamais imprime o valor de um token.
* **TTL curto:** 60s. Passou do prazo, não vale mais.
* **Uso único:** consumir um token — com sucesso ou não — o descarta.
* **Vinculado a um sujeito:** um token emitido para um sujeito não serve para outro.

O motivo da recusa **não** é diferenciado para o chamador ([prompt E3 sub-etapa 4]: 403
genérico). `consume` só devolve `bool`.

## O sujeito tem espaço de nomes (E6)

Até a E5 o sujeito era o `workspace_id` cru, porque workspace era o único purgável. A E6
acrescenta a purga de **task** ([02] §11 regra 1) e reaproveita este store — TTL, uso único
e vínculo são exatamente os mesmos requisitos.

Com ids crus, a única coisa que impediria um token emitido por
`GET /workspaces/{x}/purge-preview` de ser aceito por `POST /tasks/{x}/purge` seria um
`workspace_id` jamais coincidir com um `task_id`. Isso é verdade na prática (UUID v4), mas
**nada no sistema impõe**. `workspace_subject`/`task_subject` prefixam o tipo, e a confusão
passa a ser impossível por construção em vez de improvável por sorte.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable

from app.safety.redaction import Unredacted

#: [02] §11 diz "curta duração"; 60s é curto o bastante para exigir intenção deliberada e
#: folgado o bastante para um humano ler a prévia e confirmar.
_DEFAULT_TTL_SECONDS = 60.0


def workspace_subject(workspace_id: str) -> str:
    """Sujeito de um token de purga de workspace. Ver o docstring do módulo."""
    return f"workspace:{workspace_id}"


def task_subject(task_id: str) -> str:
    """Sujeito de um token de purga de task. Ver o docstring do módulo."""
    return f"task:{task_id}"


class PurgeTokenStore:
    """Emite e consome tokens de purga efêmeros, vinculados a um **sujeito**.

    O sujeito é opaco para o store e deve vir de `workspace_subject`/`task_subject` — ver o
    docstring do módulo.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
    ) -> None:
        #: `monotonic` e não `time()`: imune a ajuste de relógio do sistema.
        self._clock = clock or time.monotonic
        self._ttl_seconds = ttl_seconds
        self._entries: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def issue(self, subject: str) -> Unredacted:
        """Gera um token novo (256 bits) vinculado a `subject` e o registra.

        Devolve `Unredacted` — **o único ponto de construção desse tipo em todo o backend**,
        afirmado por `test_architecture`. Ver o docstring de `safety.redaction.Unredacted`
        para por que a exceção ao boundary de saída de [04] §5 é um tipo e não uma flag.

        O escape hatch é estreito de propósito: ele não diz "este campo é público", diz
        "**este valor** foi gerado aqui para ser entregue". Um segundo `Unredacted(...)` em
        qualquer lugar quebra a suíte, e é isso que impede a exceção de virar um costume.
        """
        token = Unredacted(secrets.token_urlsafe(32))
        with self._lock:
            self._prune()
            self._entries[token] = (subject, self._clock() + self._ttl_seconds)
        return token

    def consume(self, subject: str, token: str) -> bool:
        """`True` só se o token existe, não expirou e é **deste** sujeito.

        Descarta o token em qualquer caso — uso único, sem retry de força bruta.
        """
        with self._lock:
            entry = self._entries.pop(token, None)
        if entry is None:
            return False
        bound_subject, expires_at = entry
        if self._clock() >= expires_at:
            return False
        return secrets.compare_digest(bound_subject, subject)

    def _prune(self) -> None:
        """Remove entradas expiradas. Chamado sob `self._lock`."""
        now = self._clock()
        for expired in [token for token, (_, exp) in self._entries.items() if now >= exp]:
            del self._entries[expired]

    def __repr__(self) -> str:  # pragma: no cover - diagnóstico, nunca imprime token
        return f"<PurgeTokenStore active_entries={len(self._entries)}>"
