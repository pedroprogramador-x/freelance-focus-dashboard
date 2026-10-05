"""Transporte da Messages API: porta testável + implementação real (E8.2).

A porta (`MessagesTransport`) é o único ponto em que o provider fala com a rede: `count_tokens`
(preflight de orçamento), `create` (inferência) e `aclose`. A implementação real é uma **ponte
fina** — repassa os kwargs canônicos já montados por `request_plan.build_*` sem acrescentar,
remover ou trocar nada (travado por teste de arquitetura).

## Abertura supervisionada e sem subprocesso (AUD-003)

Não existe factory síncrona no `run`: o transporte é aberto por um **opener assíncrono**
(`TransportOpener`) executado no event loop do run, sob a mesma deadline e cancelamento. Mas
cancelar uma coroutine não interrompe código síncrono — então a **construção não pode conter**
operação potencialmente longa. No pin (anthropic 1.11.0 / httpx2 2.13.1, CPython 3.11 no
Windows) havia três caminhos até `platform.uname()` → `win32_ver()` → `subprocess` (`cmd /c ver`):

1. `httpx2.create_ssl_context` faz `import truststore` **incondicional**, e o módulo do
   `truststore` chama `platform.system()` ao ser importado — `AsyncHTTPTransport.__init__`
   sempre chama `create_ssl_context`, mesmo com `verify=<SSLContext>`;
2. o SDK resolve `get_platform()` (→ `platform.system()`) via `asyncify` — numa **thread**
   — na primeira request, se `_platform` for `None`;
3. `platform_headers` chama `get_architecture()` → `platform.machine()`.

Os três foram eliminados com APIs públicas do pin, sem dependência nova:

* **TLS**: `explicit_tls_context()` monta um `ssl.SSLContext(PROTOCOL_TLS_CLIENT)` da stdlib
  (verificação de certificado e de hostname **ligadas**, TLS ≥ 1.2), carregando as CAs do
  sistema por API pública — Windows: `ssl.enum_certificates("CA"/"ROOT")` filtradas por
  `Purpose.SERVER_AUTH` (o mesmo critério do `load_default_certs` da stdlib); demais: os
  caminhos **compilados** do OpenSSL (`get_default_verify_paths().openssl_*`, não
  `SSL_CERT_FILE`/`SSL_CERT_DIR` do ambiente). Sem CA carregada → recusa. Esse contexto vai a
  um `httpcore2.AsyncConnectionPool(ssl_context=…)` (API pública do httpcore2) dentro de
  `ExplicitTlsTransport`, passado ao `httpx2.AsyncClient(transport=…)` — que, com transporte
  explícito, não cria o padrão. `create_ssl_context`/`truststore` nunca são chamados;
* **plataforma**: `ExplicitAsyncAnthropic` fixa `_platform` e `platform_headers()` a partir de
  `sys.platform`/`sysconfig.get_platform()`/`sys.version_info` (sem `platform.uname()`), então
  o `asyncify(get_platform)` e o `platform.machine()` não acontecem.

**Limite que permanece (AUD-003 — bloqueio reportado):** o próprio `httpcore2` faz
`import truststore` no nível do módulo (`httpcore2/_ssl.py`), e o `truststore` chama
`platform.system()` ao ser importado. Qualquer HTTP pelo `httpx2` precisa do `httpcore2`, e não
há configuração pública no pin que evite esse import. Num processo em que `platform.uname()`
ainda não foi resolvido, no Windows/CPython 3.11, isso roda `cmd /c ver` (`check_output`, sem
timeout) na primeira abertura — dentro do worker supervisionado, mas não preemptível. No processo
da aplicação o `uname` já foi resolvido no import de `app.main` (o SQLAlchemy chama
`platform.machine()` em `sqlalchemy/util/compat.py`), então ali o caminho não roda — mas isso é
efeito colateral de outra biblioteca, não garantia deste adaptador.

`ExplicitAsyncAnthropic` também sai da cadeia de descoberta de credencial/config (perfil,
`active_config`, federação), que no pin só roda para a classe base exata (`_is_base_client`).

## Ambiente

* `api_key` e `base_url` explícitos (nada de `ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL`);
  `max_retries=0`; `trust_env=False` (sem proxies/`.netrc` do ambiente);
* `ANTHROPIC_LOG` presente → `SdkLoggingRefused`; `ANTHROPIC_CUSTOM_HEADERS` (o SDK sempre o
  mescla) ou endpoint diferente → `TransportEnvironmentRejected` — antes de qualquer request.

A credencial não entra em config, hash, declaração, binding, log nem resultado.
"""

from __future__ import annotations

import ssl
import sys
import sysconfig
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, final

import anthropic
import httpx2

from app.agent_runtime.adapters.anthropic_messages.request_plan import (
    AnthropicMessagesRequestPlan,
    require_exact_plan,
)
from app.agent_runtime.adapters.anthropic_messages.sdk_logging import (
    SdkLoggingRefused,
    anthropic_log_requested,
)


class MessagesTransport(Protocol):
    """Preflight de tokens, inferência e fechamento. Nada além disso."""

    async def count_tokens(self, request: dict[str, Any]) -> Any: ...

    async def create(self, request: dict[str, Any]) -> Any: ...

    async def aclose(self) -> None: ...


#: Plano → transporte aberto, **dentro** do loop supervisionado do run.
TransportOpener = Callable[[AnthropicMessagesRequestPlan], Awaitable[MessagesTransport]]


class TransportEnvironmentRejected(RuntimeError):
    """O ambiente alteraria o request (headers/endpoint/TLS): o transporte não é aberto."""


# ------------------------------------------------------------------------------ TLS


def explicit_tls_context() -> ssl.SSLContext:
    """Contexto TLS seguro com as CAs do sistema, sem `truststore` e sem ler o ambiente."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # CERT_REQUIRED + check_hostname
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    loaded = False
    if sys.platform == "win32":
        server_auth = ssl.Purpose.SERVER_AUTH.oid
        for store in ("CA", "ROOT"):
            certs = bytearray()
            for cert, encoding, trust in ssl.enum_certificates(store):  # type: ignore[attr-defined,unused-ignore]
                trusted = trust is True or (isinstance(trust, set) and server_auth in trust)
                if encoding == "x509_asn" and trusted:
                    certs.extend(cert)
            if certs:
                context.load_verify_locations(cadata=bytes(certs))
                loaded = True
    else:
        paths = ssl.get_default_verify_paths()
        for kwargs in ({"cafile": paths.openssl_cafile}, {"capath": paths.openssl_capath}):
            try:
                context.load_verify_locations(**kwargs)
                loaded = True
            except (OSError, ssl.SSLError):
                continue
    if not loaded or context.verify_mode is not ssl.CERT_REQUIRED or not context.check_hostname:
        raise TransportEnvironmentRejected("nenhuma CA do sistema ou TLS sem verificação")
    return context


@final
class ExplicitTlsTransport(httpx2.AsyncHTTPTransport):
    """`AsyncHTTPTransport` cujo pool recebe o `SSLContext` explícito.

    Não chama `super().__init__` de propósito: é ele que chama `create_ssl_context` (e, com
    ele, `import truststore`). Herda `handle_async_request`/`aclose`, que só usam `self._pool`
    (estrutura conferida por teste no pin)."""

    def __init__(self, ssl_context: ssl.SSLContext) -> None:
        # Import preguiçoso, como o próprio httpx2 faz: `httpcore2` importa `truststore` no nível
        # do módulo (→ `platform.system()`); trazê-lo para o import do adaptador o levaria ao
        # startup. Ver o limite residual documentado no módulo.
        import httpcore2

        self._pool = httpcore2.AsyncConnectionPool(ssl_context=ssl_context, http1=True, http2=False)


# ------------------------------------------------------------------------- plataforma


def _sdk_platform() -> Any:
    name: str = sys.platform  # sem estreitamento por plataforma no mypy
    if name.startswith("win"):
        return "Windows"
    if name.startswith("linux"):
        return "Linux"
    if name == "darwin":
        return "MacOS"
    return "Unknown"


def _sdk_arch() -> str:
    machine = sysconfig.get_platform().rsplit("-", 1)[-1].lower()
    if machine in ("amd64", "x86_64"):
        return "x64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return "unknown"


@final
class ExplicitAsyncAnthropic(anthropic.AsyncAnthropic):
    """`AsyncAnthropic` sem descoberta de credencial/config e sem `platform.uname()`."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._platform = _sdk_platform()

    def platform_headers(self) -> dict[str, str]:
        version = sys.version_info
        return {
            "X-Stainless-Lang": "python",
            "X-Stainless-Package-Version": self._version,
            "X-Stainless-OS": str(self._platform),
            "X-Stainless-Arch": _sdk_arch(),
            "X-Stainless-Runtime": sys.implementation.name.replace("cpython", "CPython"),
            "X-Stainless-Runtime-Version": f"{version.major}.{version.minor}.{version.micro}",
        }


# -------------------------------------------------------------------------- transporte


@final
class AnthropicApiTransport:
    """Ponte fina sobre o cliente real: repassa os kwargs canônicos sem tocá-los."""

    __slots__ = ("_client",)

    def __init__(self, client: anthropic.AsyncAnthropic) -> None:
        self._client = client

    async def count_tokens(self, request: dict[str, Any]) -> Any:
        return await self._client.messages.count_tokens(**request)

    async def create(self, request: dict[str, Any]) -> Any:
        return await self._client.messages.create(**request)

    async def aclose(self) -> None:
        await self._client.close()


@final
class AnthropicApiTransportFactory:
    """Opener real com credencial injetada. Sem estado mutável além dela."""

    __slots__ = ("__api_key",)

    def __init__(self, api_key: str) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("credencial da Messages API ausente")
        self.__api_key = api_key

    def __repr__(self) -> str:
        return "AnthropicApiTransportFactory(api_key=«redigido»)"

    async def __call__(self, plan: AnthropicMessagesRequestPlan) -> AnthropicApiTransport:
        exact = require_exact_plan(plan)
        if anthropic_log_requested():
            raise SdkLoggingRefused("ANTHROPIC_LOG presente: logging do SDK recusado")
        client = ExplicitAsyncAnthropic(
            api_key=self.__api_key,
            base_url=exact.base_url,
            max_retries=exact.max_retries,
            http_client=httpx2.AsyncClient(
                transport=ExplicitTlsTransport(explicit_tls_context()),
                trust_env=exact.trust_env,
            ),
        )
        # Atributo privado do SDK fixado (1.11.0), lido só para recusar headers do ambiente.
        if client._custom_headers:
            await client.close()
            raise TransportEnvironmentRejected("headers customizados do ambiente recusados")
        if str(client.base_url).rstrip("/") != exact.base_url.rstrip("/"):
            await client.close()
            raise TransportEnvironmentRejected("endpoint diferente do oficial")
        return AnthropicApiTransport(client)
