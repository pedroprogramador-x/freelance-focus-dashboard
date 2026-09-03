"""Erros de domínio do Workspace Registry.

A camada de serviço (`app.workspace.service`) levanta **exclusivamente** estas exceções.
A camada HTTP (`app.api.workspaces`) é a única que as traduz em status
([01](../../../docs/architecture/01-v1-architecture.md) §2 — `workspace/` não conhece
FastAPI). O mapeamento vive em `status_code`, aqui, para que o router não precise de uma
árvore de `isinstance`.
"""

from __future__ import annotations


class WorkspaceError(Exception):
    """Base de todo erro de domínio do Workspace Registry.

    ``code`` é o slug estável que vai no corpo `{code, message}` ([06] §2, "Convenções").
    ``status_code`` é o HTTP que o router deve devolver.
    """

    code: str = "workspace_error"
    status_code: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidLocalPath(WorkspaceError):
    """`local_path` recusado pelo Path Runtime + Safety Kernel, ou não é diretório.

    Inclui: sintaxe perigosa, caminho fora de política, inexistente, e alvo que existe
    mas não é diretório. Todos viram **422** — é entrada malformada, não conflito.
    """

    code = "invalid_local_path"
    status_code = 422

    def __init__(self, message: str, *, rule_id: str) -> None:
        super().__init__(message)
        #: Regra do kernel que negou (ex.: `path.escapes_root`), para diagnóstico na UI.
        self.rule_id = rule_id


class InvalidWorkspaceName(WorkspaceError):
    """`name` vazio ou acima de 120 caracteres ([02] §1: "não vazio, ≤ 120").

    O limite existe na coluna (`String(120)`) e no schema Pydantic (borda HTTP), mas o
    SQLite **não** trunca nem rejeita um VARCHAR longo (E3-AUD-007), e um chamador interno
    futuro não passa pelo Pydantic. O invariante vive aqui, na camada de domínio. **422**.
    """

    code = "invalid_workspace_name"
    status_code = 422


class DuplicateLocalPath(WorkspaceError):
    """Já existe um `DevWorkspace` com este `local_path` canônico ([02] §1: único).

    A unicidade é garantida pela constraint `uq_dev_workspace_local_path` (E2); este erro
    é o que a violação vira antes de vazar como `IntegrityError` cru. **409**.
    """

    code = "duplicate_local_path"
    status_code = 409


class WorkspaceNotFound(WorkspaceError):
    """Nenhum `DevWorkspace` com o `id` pedido. **404**."""

    code = "workspace_not_found"
    status_code = 404


class InvalidStatusTransition(WorkspaceError):
    """Transição de `status` fora de `active ⇄ archived` ([02] §1).

    Na V1 a máquina tem exatamente dois estados, então a única transição inválida
    possível é pedir o estado em que o workspace já está. **409** ([06] §2: guarda não
    satisfeita).
    """

    code = "invalid_status_transition"
    status_code = 409


class PurgeTokenRejected(WorkspaceError):
    """`purge_token` ausente, inválido, expirado, já usado ou de outro workspace.

    **403 genérico** — o motivo **não** é diferenciado na mensagem ([02] §11 + prompt E3
    sub-etapa 4): quem tenta purgar sem uma prévia legítima recente não recebe pistas.
    """

    code = "purge_forbidden"
    status_code = 403


class WorkspacePurgeBlocked(WorkspaceError):
    """Revalidação da purga falhou: workspace não arquivado, ou com task não-terminal.

    A prévia e o token não bastam — [02] §11 regra 1 exige revalidar do zero no momento
    da execução. **409**.
    """

    code = "workspace_purge_blocked"
    status_code = 409


class WorkspaceBenchmarkProtected(WorkspaceError):
    """Purga recusada: o workspace pertence a um `benchmark_group_id` com avaliação.

    [02] §11 regra 6 (risco R8): se alguma `WorkspaceTask` do workspace compartilha o
    `benchmark_group_id` com um `Run` ou `AuditFinding` de `purpose = benchmark_evaluation`
    — mesmo em outro workspace do grupo —, métricas de comparação não podem sumir por
    exclusão. Recusada **mesmo com token válido**. **409**.
    """

    code = "workspace_purge_benchmark_protected"
    status_code = 409
