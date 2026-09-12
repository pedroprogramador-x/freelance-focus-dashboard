"""Helpers dos testes da E4 (Context Registry). **Sem fixtures** — elas vivem no `conftest.py`.

A separação é deliberada: fixture importada por `from … import` num arquivo de teste vira
`F811` assim que um parâmetro de teste tem o mesmo nome. Fixtures ficam onde o pytest as
descobre sozinho; aqui ficam só as funções que os testes chamam explicitamente.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db.enums import (
    ComplexityLevel,
    ExecutionMode,
    RiskLevel,
    RiskSource,
    TaskStatus,
    WorkspaceType,
)
from app.db.models import DevWorkspace, WorkspaceTask
from app.git_runtime import TreeListing, WorkingTreeListing
from app.workspace import create_workspace

GIT = shutil.which("git")


def git(cwd: Path, *args: str) -> None:
    """`git` de **montagem de cenário**, não do runtime.

    Usa verbos mutantes (`init`, `add`, `commit`, `mv`, `rm`) de propósito: é o teste
    construindo o repositório contra o qual o `git_runtime` — que é só leitura — vai rodar.
    """
    assert GIT is not None
    subprocess.run(  # noqa: S603 — git de teste, argv literal, sem shell
        [GIT, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def init_repo(root: Path) -> None:
    """Repositório com identidade fixa e sem assinatura — determinístico entre máquinas."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Teste")
    git(root, "config", "commit.gpgsign", "false")


def commit_all(root: Path, message: str) -> None:
    git(root, "add", "-A", "-f")
    git(root, "commit", "-m", message)


def write(root: Path, relative: str, content: str) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def make_workspace(session: Session, path: Path, *, name: str = "ws") -> DevWorkspace:
    workspace = create_workspace(
        session,
        name=name,
        workspace_type=WorkspaceType.PERSONAL,
        local_path=str(path),
    )
    session.flush()
    return workspace


def readable(result: TreeListing | WorkingTreeListing | None) -> list[Any]:
    """Afirma que uma leitura da E4 foi **completa** e devolve só a parte útil dela.

    Desde E4-AUD5-001 as leituras devolvem um tipo-produto: o que deu para ler **e** o que
    não deu. Num teste que não está exercitando a leitura parcial, "tratar" a segunda metade
    é **afirmar que ela está vazia** — que é o que este helper faz. Ele assere em vez de
    só ignorar o campo: ignorar deixaria o teste passar por acidente se a leitura degradasse
    para parcial, e é justamente uma leitura que degradava em silêncio que abriu as rodadas
    3, 4 e 5 desta auditoria.
    """
    assert result is not None
    assert not result.unrepresentable, (
        f"leitura ficou parcial: {[item.display for item in result.unrepresentable]}"
    )
    if isinstance(result, TreeListing):
        return list(result.files)
    return list(result.divergences)


# --------------------------------------------------------------- E5: Context Router


def make_task(session: Session, workspace: DevWorkspace, *, title: str = "task") -> WorkspaceTask:
    """`WorkspaceTask` mínima criada **direto pela sessão de teste**, nunca pela API.

    `ContextManifest.task_id` é FK obrigatória, e a E5 não introduz nenhum endpoint HTTP de
    task: `freeze_manifest` é capacidade interna de `context_engine/`, consumida pelo
    Orchestrator Planner só a partir da E6. Esta função existe **exclusivamente** para
    satisfazer a FK nos testes e **não é caminho de produção** — nada aqui monta a máquina
    de estados de [02] §4, que é assunto da E6.
    """
    task = WorkspaceTask(
        workspace_id=workspace.id,
        title=title,
        goal="objetivo de teste",
        status=TaskStatus.DRAFT,
        risk=RiskLevel.LOW,
        complexity=ComplexityLevel.LOW,
        risk_source=RiskSource.HARD_RULE,
        execution_mode=ExecutionMode.CLAUDE_ONLY,
    )
    session.add(task)
    session.flush()
    return task


def head_of(root: Path) -> str:
    """`HEAD` do repositório de teste, como SHA-1 completo."""
    assert GIT is not None
    result = subprocess.run(  # noqa: S603 — git de teste, argv literal, sem shell
        [GIT, "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()
