"""Runners concretos do `TestRunner` (E8.3). Infraestrutura do sistema, **não** ferramenta do
Developer ([04] §6): nenhum provider os recebe, e o composition root os injeta no Execution
Manager pela porta `agent_runtime.TestRunner`.

Só existe um: `LocalSubprocessTestRunner` (`generic-subprocess-v1`). Não há registry nem
dispatch dinâmico de runner.
"""

from app.agent_runtime.runners.local_subprocess import (
    GENERIC_SUBPROCESS_RUNNER_ID,
    LocalSubprocessTestRunner,
    resolve_trusted_executable,
)

__all__ = [
    "GENERIC_SUBPROCESS_RUNNER_ID",
    "LocalSubprocessTestRunner",
    "resolve_trusted_executable",
]
