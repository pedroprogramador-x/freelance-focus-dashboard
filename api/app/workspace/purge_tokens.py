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
* **Vinculado ao `workspace_id`:** um token emitido para um workspace não serve para
  outro.

O motivo da recusa **não** é diferenciado para o chamador ([prompt E3 sub-etapa 4]: 403
genérico). `consume` só devolve `bool`.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable

#: [02] §11 diz "curta duração"; 60s é curto o bastante para exigir intenção deliberada e
#: folgado o bastante para um humano ler a prévia e confirmar.
_DEFAULT_TTL_SECONDS = 60.0


class PurgeTokenStore:
    """Emite e consome tokens de purga efêmeros, vinculados a um `workspace_id`."""

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

    def issue(self, workspace_id: str) -> str:
        """Gera um token novo (256 bits) vinculado a `workspace_id` e o registra."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._prune()
            self._entries[token] = (workspace_id, self._clock() + self._ttl_seconds)
        return token

    def consume(self, workspace_id: str, token: str) -> bool:
        """`True` só se o token existe, não expirou e é **deste** workspace.

        Descarta o token em qualquer caso — uso único, sem retry de força bruta.
        """
        with self._lock:
            entry = self._entries.pop(token, None)
        if entry is None:
            return False
        bound_workspace_id, expires_at = entry
        if self._clock() >= expires_at:
            return False
        return secrets.compare_digest(bound_workspace_id, workspace_id)

    def _prune(self) -> None:
        """Remove entradas expiradas. Chamado sob `self._lock`."""
        now = self._clock()
        for expired in [token for token, (_, exp) in self._entries.items() if now >= exp]:
            del self._entries[expired]

    def __repr__(self) -> str:  # pragma: no cover - diagnóstico, nunca imprime token
        return f"<PurgeTokenStore active_entries={len(self._entries)}>"
