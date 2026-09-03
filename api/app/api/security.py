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

#: Métodos que alteram estado. [01](../../../docs/architecture/01-v1-architecture.md) §4:
#: "requisições que alteram estado exigem mesma origem".
MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: `Origin` de escrita: só `http`/`https`, e a comparação é contra a **autoridade do
#: `Host`** — não "qualquer porta loopback". `[0-9]` (não `\d`) para porta, casamento total.
_ORIGIN_PATTERN = re.compile(r"(https?)://([a-z0-9.\-]+|\[[0-9a-f:]+\])(?::([0-9]{1,5}))?")

#: Autoridade de um header `Host`: `host[:porta]`, sem esquema.
_HOST_AUTHORITY_PATTERN = re.compile(r"([a-z0-9.\-]+|\[[0-9a-f:]+\])(?::([0-9]{1,5}))?")

_DEFAULT_PORT_BY_SCHEME: dict[str, int] = {"http": 80, "https": 443}

#: `Sec-Fetch-Site` aceitos para uma escrita via browser: só mesma origem. `same-site`
#: (outra porta/subdomínio) e qualquer valor desconhecido são recusados (E3-AUD2-004).
_ACCEPTED_SEC_FETCH_SITE: frozenset[str] = frozenset({"same-origin", "none"})


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


def _authority(host: str, port_group: str | None, scheme: str) -> tuple[str, int] | None:
    """`(host, porta)` normalizado; porta ausente vira a default do esquema. `None` se inválida."""
    if port_group is None:
        default = _DEFAULT_PORT_BY_SCHEME.get(scheme)
        return None if default is None else (host, default)
    port = int(port_group)
    if not (_MIN_PORT <= port <= _MAX_PORT):
        return None
    return host, port


def origin_matches_host(
    origin_header: str | None, host_header: str | None, *, request_scheme: str = "http"
) -> bool:
    """`True` se o `Origin` é **exatamente a origem servida**: esquema + host + porta do `Host`.

    - `Origin` ausente/vazio → `True`: clientes não-browser (a suíte, `curl`, o futuro
      *launcher*) não mandam `Origin`, e a barreira real é o `LocalSessionToken`.
    - `Origin` presente → tem de casar **esquema, host e porta** com a autoridade do
      `Host` efetivo da requisição. Ser loopback não basta (E3-AUD2-004):
      `http://localhost:5173` contra um `Host` `127.0.0.1:8756` é **negado** — porta
      diferente, e `localhost` ≠ `127.0.0.1` são origens distintas para o browser.
    - Proxy do Vite dev: `changeOrigin: false` preserva o `Host` do dev server
      (`localhost:5173`), que é o mesmo do `Origin` → casa.
    """
    if not origin_header or not origin_header.strip():
        return True
    if not host_header:
        return False

    origin_match = _ORIGIN_PATTERN.fullmatch(origin_header.strip().lower())
    if origin_match is None:
        return False
    origin_scheme, origin_host, origin_port = origin_match.groups()
    if origin_scheme != request_scheme.lower():
        return False

    host_match = _HOST_AUTHORITY_PATTERN.fullmatch(host_header.strip().lower())
    if host_match is None:
        return False
    host_name, host_port = host_match.groups()

    origin_authority = _authority(origin_host, origin_port, origin_scheme)
    host_authority = _authority(host_name, host_port, request_scheme.lower())
    return origin_authority is not None and origin_authority == host_authority


def sec_fetch_site_allows_write(value: str | None) -> bool:
    """`Sec-Fetch-Site` numa requisição mutante: só valores conhecidos e de mesma origem.

    - Ausente → aceito (cliente não-browser, ou browser sem *Fetch Metadata*).
    - `same-origin` / `none` → aceito.
    - `same-site`, `cross-site` e **qualquer valor desconhecido** (incluindo string vazia)
      → recusado (E3-AUD2-004). Uma escrita via browser legítima nesta app é sempre
      `same-origin` — a página é servida pela mesma origem da API, inclusive atrás do
      proxy do Vite dev.
    """
    if value is None:
        return True
    return value.strip().lower() in _ACCEPTED_SEC_FETCH_SITE


def same_origin_write_allowed(
    method: str,
    origin_header: str | None,
    sec_fetch_site: str | None,
    host_header: str | None,
    *,
    request_scheme: str = "http",
) -> bool:
    """Decisão composta para o middleware ([01] §4). `False` ⇒ bloquear a requisição.

    Só se aplica a métodos que alteram estado; `GET`/`HEAD`/`OPTIONS` passam sempre por
    aqui (a proteção deles é o token).
    """
    if method.upper() not in MUTATING_METHODS:
        return True
    return origin_matches_host(
        origin_header, host_header, request_scheme=request_scheme
    ) and sec_fetch_site_allows_write(sec_fetch_site)
