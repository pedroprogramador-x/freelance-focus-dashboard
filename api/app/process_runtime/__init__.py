"""Process Runtime — supervisão do ciclo de vida de processos (E7.3).

Adendo E7.3 a [01] §2 e [04] §7: este pacote, e não `agent_runtime/`, é o dono da
supervisão de processos. Folha em L1: importa só stdlib, e `agent_runtime`,
`tool_executor`, `git_runtime`, `orchestrator`, `db` e `api` não aparecem aqui. Os
consumidores previstos são o `TestRunner` e os adaptadores de provider (E8), ligados no
composition root; o `ToolExecutor` não o usa — não existe `ExecCommand`.

A worktree define **onde** um processo roda (o `cwd`, resolvido por quem chama); este pacote
define **como** ele é controlado: iniciar sem shell, prazo, cancelamento, encerramento da
árvore inteira, confirmação de que nada sobreviveu e captura limitada de saída.

**Supervisão de processo não é sandbox.** Nada aqui impede o processo de ler arquivos,
escrever fora da worktree, abrir rede, ler segredos, usar APIs do SO ou criar processos por
mecanismos externos à árvore. Ver [04] §0 e §6.

API pública: `ProcessSpec`, `ProcessOutcome`, `ProcessResult`, `InvalidProcessSpec`,
`run_supervised`.
"""

from app.process_runtime.contracts import (
    InvalidProcessSpec,
    ProcessOutcome,
    ProcessResult,
    ProcessSpec,
)
from app.process_runtime.supervisor import run_supervised

__all__ = [
    "InvalidProcessSpec",
    "ProcessOutcome",
    "ProcessResult",
    "ProcessSpec",
    "run_supervised",
]
