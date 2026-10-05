"""Guarda de logging do SDK Anthropic (E8.2, AUD-005).

O SDK 1.11.0 loga o request inteiro (`Request options: …`, com `json_data`/`messages`) no
logger `anthropic._base_client` sempre que DEBUG está habilitado — e o `httpx2`/`httpcore2`
logam trace de conexão. O transcript de um run carrega contexto do projeto, resultados de
ferramenta e blocos de *thinking*/assinatura, que só podem existir na memória do run. "Nós não
logamos" não basta: é preciso impedir que **o SDK** logue.

Duas camadas:

* `ANTHROPIC_LOG` presente e não vazio → `SdkLoggingRefused` antes de montar o cliente (o SDK
  lê essa variável na importação e liga DEBUG sozinho). Única leitura de ambiente do adaptador;
* `AnthropicSdkLoggingGuard` — enquanto um run está aberto, os loggers `anthropic*`, `httpx2*`
  e `httpcore2*` (os do pin 1.11.0) ficam **desligados** (`disabled=True` nos existentes, nível
  acima de CRITICAL nas raízes, para os criados durante o run herdarem). Vale mesmo com o root
  ou os próprios loggers em DEBUG por configuração externa. Na saída, o estado anterior
  (nível e `disabled`) é restaurado, inclusive em exceção.

Fail closed: depois de aplicar, a guarda **confere** que o logger real do SDK
(`anthropic._base_client.log`) e as raízes não emitem nem CRITICAL; estrutura inesperada →
`SdkLoggingRefused`. Nunca "não achei o logger, então está seguro".

Concorrência: um lock de processo protege um contador de profundidade — o primeiro a entrar
salva e aplica, o último a sair restaura (V1 tem `max_parallel_agents = 1`, mas a guarda não
depende disso). Só usa `logging` da stdlib; não recebe nem serializa request.
"""

from __future__ import annotations

import logging
import os
import threading
from types import TracebackType
from typing import Final

#: Raízes dos loggers que o SDK fixado usa (`anthropic._utils._logs`, `httpx2._client`,
#: `httpcore2._async.*`, `httpcore2._trace`).
SDK_LOGGER_ROOTS: Final = ("anthropic", "httpx2", "httpcore2")
_SILENT: Final = logging.CRITICAL + 10


class SdkLoggingRefused(RuntimeError):
    """O logging do SDK não pode ser garantidamente silenciado: o run não começa."""


def anthropic_log_requested() -> bool:
    """`ANTHROPIC_LOG` presente e não vazio (qualquer valor: V1 prefere a ausência)."""
    return bool(os.environ.get("ANTHROPIC_LOG", "").strip())


def _sdk_loggers() -> list[logging.Logger]:
    manager = logging.Logger.manager
    found = [logging.getLogger(root) for root in SDK_LOGGER_ROOTS]
    for name, obj in list(manager.loggerDict.items()):
        if isinstance(obj, logging.Logger) and name.startswith(
            tuple(f"{root}." for root in SDK_LOGGER_ROOTS)
        ):
            found.append(obj)
    return found


def _sdk_base_client_logger() -> logging.Logger:
    """O logger que emite `Request options` no pin 1.11.0 (estrutura conferida, não suposta)."""
    import anthropic._base_client as base_client

    logger = getattr(base_client, "log", None)
    if not isinstance(logger, logging.Logger) or not logger.name.startswith("anthropic"):
        raise SdkLoggingRefused("estrutura de logging do SDK diferente da esperada")
    return logger


class AnthropicSdkLoggingGuard:
    """Context manager reentrante entre threads: silencia o SDK enquanto houver run aberto."""

    _lock: Final = threading.Lock()
    _depth = 0
    _saved: list[tuple[logging.Logger, int, bool]] = []  # noqa: RUF012 — estado de classe guardado sob lock

    def __enter__(self) -> AnthropicSdkLoggingGuard:
        cls = type(self)
        with cls._lock:
            if cls._depth == 0:
                saved = [(lg, lg.level, lg.disabled) for lg in _sdk_loggers()]
                try:
                    for logger, _level, _disabled in saved:
                        logger.disabled = True
                    for root in SDK_LOGGER_ROOTS:
                        logging.getLogger(root).setLevel(_SILENT)
                    _assert_silenced()
                except BaseException:
                    _restore(saved)
                    raise
                cls._saved = saved
            cls._depth += 1
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        cls = type(self)
        with cls._lock:
            cls._depth -= 1
            if cls._depth == 0:
                _restore(cls._saved)
                cls._saved = []


def _assert_silenced() -> None:
    probes = [_sdk_base_client_logger(), *(logging.getLogger(r) for r in SDK_LOGGER_ROOTS)]
    probes += [logging.getLogger(f"{root}.ff_probe") for root in SDK_LOGGER_ROOTS]
    if any(probe.isEnabledFor(logging.CRITICAL) for probe in probes):
        raise SdkLoggingRefused("logging do SDK não ficou silenciado")


def _restore(saved: list[tuple[logging.Logger, int, bool]]) -> None:
    for logger, level, disabled in saved:
        logger.setLevel(level)
        logger.disabled = disabled
