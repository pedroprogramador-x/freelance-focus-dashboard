"""`LocalSessionToken` e proteção da API local.

[06](../../../docs/architecture/06-api-and-ui-boundaries.md) §1. Três garantias:

1. **`GET /api/health` é a única entrada `/api/*` sem token.** A exceção é o par
   **(método, caminho)**, não o caminho sozinho: `POST /api/health` exige credencial.
2. A regra é aplicada por **prefixo de caminho**, não rota a rota: qualquer rota nova sob
   `/api/` nasce protegida. Uma allowlist explícita é o único caminho para o contrário —
   esquecer de proteger deixou de ser possível.
3. O token **nunca** aparece em resposta JSON, log ou mensagem de erro. Ele só existe em
   memória do processo e no HTML de bootstrap ([`app.api.web`](web.py)).

Não existe rota de bootstrap: `/api/session/bootstrap` foi eliminada por REAUD-003 e não
pode voltar.
"""

from __future__ import annotations

import re
import secrets

#: Nome da `<meta>` que carrega o token no HTML inicial ([06] §1).
SESSION_TOKEN_META_NAME = "ff-session-token"  # noqa: S105 — nome de meta tag, não credencial

#: **A** exceção, como par `(método, caminho)`.
#:
#: Guardar só o caminho deixava `POST /api/health` público — e um `POST` a um caminho
#: público chega ao router sem credencial, o que é exatamente o que a policy congelada
#: não permite. `HEAD` e `OPTIONS` **não** herdam a exceção: a policy diz `GET`, e
#: derivar métodos por conta própria seria alargar a superfície sem decisão.
PUBLIC_API_ENDPOINTS: frozenset[tuple[str, str]] = frozenset({("GET", "/api/health")})

#: Defesa 2 de [06] §1 — DNS rebinding. Só nomes de loopback são aceitos.
ALLOWED_HOSTNAMES: frozenset[str] = frozenset({"127.0.0.1", "localhost", "[::1]"})

#: Parser fechado: consome a **string inteira**, sem prefixo e sem sobra.
#:
#: `[0-9]` em vez de `\d` de propósito — `\d` casa dígitos Unicode (`٨`, `８`), e
#: `int()` os aceitaria, deixando `localhost:８０` passar por uma porta que ninguém
#: escreveu.
_HOST_PATTERN = re.compile(r"(localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?")

_MIN_PORT = 1
_MAX_PORT = 65535

_BEARER_PREFIX = "bearer "


def generate_session_token() -> str:
    """Token efêmero de sessão local.

    ``token_urlsafe(32)`` são 256 bits de `secrets`, o CSPRNG do sistema. Gerado a cada
    criação de app, mantido só em memória, nunca gravado em disco, `.env` ou log.
    """
    return secrets.token_urlsafe(32)


def is_api_path(path: str) -> bool:
    """`/api` e tudo abaixo — e nada além disso.

    `startswith("/api")` sozinho casaria `/apidocs`, que não é rota da API e ficaria
    protegido sem necessidade; pior, um `/api-publico` futuro pareceria protegido sem ser.
    """
    return path == "/api" or path.startswith("/api/")


def requires_session_token(method: str, path: str) -> bool:
    """Exige credencial para tudo em `/api/*`, exceto os pares explicitamente públicos."""
    if not is_api_path(path):
        return False
    return (method.upper(), path) not in PUBLIC_API_ENDPOINTS


def extract_bearer_token(authorization_header: str | None) -> str | None:
    """Extrai o token de `Authorization: Bearer <token>`. Não valida nada."""
    if not authorization_header:
        return None
    if not authorization_header.lower().startswith(_BEARER_PREFIX):
        return None
    presented = authorization_header[len(_BEARER_PREFIX) :].strip()
    return presented or None


def token_is_valid(presented: str | None, expected: str) -> bool:
    """Comparação em tempo constante, para não vazar o token por temporização."""
    if not presented:
        return False
    return secrets.compare_digest(presented, expected)


def host_is_local(host_header: str | None) -> bool:
    """Aceita apenas `Host` de loopback, com ou sem porta válida.

    Casamento **total**, nunca por prefixo. A versão anterior fatiava a string e comparava
    o pedaço da frente, então `[::1]evil.com` e `localhost:abc` passavam: o sufixo era
    simplesmente ignorado. Um `Host` de outro domínio apontando para 127.0.0.1 é o formato
    clássico de DNS rebinding — o browser trataria a resposta como pertencente àquele
    domínio.

    Espaços em volta também recusam: o valor não é normalizado aqui de propósito, para que
    nada seja "consertado" silenciosamente antes da decisão.
    """
    if not host_header:
        return False

    # `fullmatch`, e não `match` com `$`: em Python, `$` também pode casar antes de uma
    # quebra de linha final. A fronteira de segurança precisa consumir literalmente todos
    # os caracteres recebidos.
    match = _HOST_PATTERN.fullmatch(host_header.lower())
    if match is None:
        return False

    port = match.group(2)
    if port is None:
        return True

    # `[0-9]{1,5}` já garante só dígitos ASCII; falta o intervalo.
    return _MIN_PORT <= int(port) <= _MAX_PORT
