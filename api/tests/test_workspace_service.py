"""Gate 2 da E3 — serviço `app.workspace`, CRUD sem purga.

Cobre o exigido pelo gate: os 5 tipos de workspace, criação sem `linked_project_id`,
`local_path` duplicado (→ 409), inexistente (→ 422), não-diretório (→ 422), e transição
de status válida e inválida.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.enums import WorkspaceStatus, WorkspaceType
from app.workspace import (
    DuplicateLocalPath,
    InvalidLocalPath,
    InvalidStatusTransition,
    InvalidWorkspaceName,
    WorkspaceNotFound,
    create_workspace,
    get_workspace,
    list_workspaces,
    update_workspace_status,
    validate_local_path,
)


@pytest.mark.parametrize("ws_type", list(WorkspaceType))
def test_cria_workspace_de_cada_tipo(
    session: Session, tmp_path: Path, ws_type: WorkspaceType
) -> None:
    directory = tmp_path / ws_type.value
    directory.mkdir()

    workspace = create_workspace(
        session,
        name=f"  ws {ws_type.value}  ",
        workspace_type=ws_type,
        local_path=str(directory),
    )

    assert workspace.id
    assert workspace.type is ws_type
    assert workspace.name == f"ws {ws_type.value}"  # trim aplicado
    assert workspace.status is WorkspaceStatus.ACTIVE
    assert workspace.linked_project_id is None
    assert Path(workspace.local_path).samefile(directory)


def test_cria_sem_linked_project_id_e_com_campos_opcionais(
    session: Session, tmp_path: Path
) -> None:
    directory = tmp_path / "opt"
    directory.mkdir()

    workspace = create_workspace(
        session,
        name="com repo",
        workspace_type=WorkspaceType.OPEN_SOURCE,
        local_path=str(directory),
        repository_url="https://example.invalid/repo.git",
        default_branch="main",
    )

    assert workspace.linked_project_id is None
    assert workspace.repository_url == "https://example.invalid/repo.git"
    assert workspace.default_branch == "main"


def test_linked_project_id_e_string_opaca(session: Session, tmp_path: Path) -> None:
    directory = tmp_path / "linked"
    directory.mkdir()

    workspace = create_workspace(
        session,
        name="ligado",
        workspace_type=WorkspaceType.FREELANCE,
        local_path=str(directory),
        linked_project_id="qualquer-coisa-nao-resolvida",
    )

    assert workspace.linked_project_id == "qualquer-coisa-nao-resolvida"


def test_name_acima_de_120_e_recusado_na_camada_de_servico(
    session: Session, tmp_path: Path
) -> None:
    """E3-AUD-007: o limite ≤ 120 é invariante de domínio, não só da borda Pydantic."""
    directory = tmp_path / "longo"
    directory.mkdir()

    with pytest.raises(InvalidWorkspaceName) as error:
        create_workspace(
            session,
            name="n" * 121,
            workspace_type=WorkspaceType.PERSONAL,
            local_path=str(directory),
        )

    assert error.value.status_code == 422
    assert error.value.code == "invalid_workspace_name"


def test_name_exatamente_120_passa(session: Session, tmp_path: Path) -> None:
    directory = tmp_path / "limite"
    directory.mkdir()

    workspace = create_workspace(
        session,
        name="n" * 120,
        workspace_type=WorkspaceType.PERSONAL,
        local_path=str(directory),
    )
    assert len(workspace.name) == 120


def test_name_so_de_espacos_e_recusado(session: Session, tmp_path: Path) -> None:
    directory = tmp_path / "branco"
    directory.mkdir()

    with pytest.raises(InvalidWorkspaceName):
        create_workspace(
            session,
            name="   ",
            workspace_type=WorkspaceType.PERSONAL,
            local_path=str(directory),
        )


def test_local_path_duplicado_e_409(session: Session, tmp_path: Path) -> None:
    directory = tmp_path / "dup"
    directory.mkdir()
    create_workspace(
        session, name="primeiro", workspace_type=WorkspaceType.PERSONAL, local_path=str(directory)
    )

    with pytest.raises(DuplicateLocalPath) as error:
        create_workspace(
            session, name="segundo", workspace_type=WorkspaceType.STUDY, local_path=str(directory)
        )

    assert error.value.status_code == 409
    assert error.value.code == "duplicate_local_path"
    session.rollback()


def test_local_path_inexistente_e_422(session: Session, tmp_path: Path) -> None:
    with pytest.raises(InvalidLocalPath) as error:
        create_workspace(
            session,
            name="fantasma",
            workspace_type=WorkspaceType.FREELANCE,
            local_path=str(tmp_path / "nao-existe"),
        )

    assert error.value.status_code == 422
    assert error.value.rule_id == "workspace.path_missing"


def test_local_path_nao_e_diretorio_e_422(session: Session, tmp_path: Path) -> None:
    arquivo = tmp_path / "arquivo.txt"
    arquivo.write_text("conteudo\n", encoding="utf-8")

    with pytest.raises(InvalidLocalPath) as error:
        create_workspace(
            session,
            name="arquivo",
            workspace_type=WorkspaceType.EXPERIMENT,
            local_path=str(arquivo),
        )

    assert error.value.status_code == 422
    assert error.value.rule_id == "workspace.path_not_dir"


def test_local_path_com_traversal_e_recusado_pela_sintaxe(tmp_path: Path) -> None:
    with pytest.raises(InvalidLocalPath) as error:
        validate_local_path(str(tmp_path / ".." / "alvo"))

    assert error.value.status_code == 422
    assert error.value.rule_id == "path.parent_traversal"


def test_local_path_relativo_e_recusado(session: Session) -> None:
    with pytest.raises(InvalidLocalPath):
        create_workspace(
            session,
            name="relativo",
            workspace_type=WorkspaceType.PERSONAL,
            local_path="caminho/relativo",
        )


def test_transicao_de_status_valida_nos_dois_sentidos(session: Session, tmp_path: Path) -> None:
    directory = tmp_path / "estado"
    directory.mkdir()
    workspace = create_workspace(
        session, name="estado", workspace_type=WorkspaceType.PERSONAL, local_path=str(directory)
    )
    original_updated_at = workspace.updated_at

    archived = update_workspace_status(session, workspace.id, WorkspaceStatus.ARCHIVED)
    assert archived.status is WorkspaceStatus.ARCHIVED
    assert archived.updated_at >= original_updated_at

    reactivated = update_workspace_status(session, workspace.id, WorkspaceStatus.ACTIVE)
    assert reactivated.status is WorkspaceStatus.ACTIVE


def test_transicao_de_status_invalida_e_409(session: Session, tmp_path: Path) -> None:
    directory = tmp_path / "mesmo"
    directory.mkdir()
    workspace = create_workspace(
        session, name="mesmo", workspace_type=WorkspaceType.PERSONAL, local_path=str(directory)
    )

    with pytest.raises(InvalidStatusTransition) as error:
        update_workspace_status(session, workspace.id, WorkspaceStatus.ACTIVE)

    assert error.value.status_code == 409


def test_get_workspace_inexistente_e_404(session: Session) -> None:
    with pytest.raises(WorkspaceNotFound) as error:
        get_workspace(session, "id-que-nao-existe")

    assert error.value.status_code == 404


def test_lista_workspaces_e_filtra_por_status(session: Session, tmp_path: Path) -> None:
    created_ids: list[str] = []
    for index in range(3):
        directory = tmp_path / f"w{index}"
        directory.mkdir()
        workspace = create_workspace(
            session,
            name=f"w{index}",
            workspace_type=WorkspaceType.PERSONAL,
            local_path=str(directory),
        )
        created_ids.append(workspace.id)

    update_workspace_status(session, created_ids[0], WorkspaceStatus.ARCHIVED)

    assert len(list_workspaces(session)) == 3
    actives = list_workspaces(session, status=WorkspaceStatus.ACTIVE)
    assert {workspace.id for workspace in actives} == {created_ids[1], created_ids[2]}
    archived = list_workspaces(session, status=WorkspaceStatus.ARCHIVED)
    assert [workspace.id for workspace in archived] == [created_ids[0]]
