"""Path Runtime: coleta de fatos e abertura verificada, contra filesystem real."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from app.path_runtime import PathAccessDenied, inspect, open_checked
from app.safety import SafetyPolicy, Tri, decide_path
from tests.conftest import supports_symlinks


def test_arquivo_dentro_do_workspace(workspace_root: Path) -> None:
    facts = inspect("src/app.py", workspace_root)

    assert facts.exists
    assert facts.contained.is_true
    assert facts.inspection_error is None
    assert facts.target_identity is not None
    assert decide_path(facts).allow


def test_diretorio_aninhado(workspace_root: Path) -> None:
    facts = inspect("src/nested/deep.txt", workspace_root)

    assert facts.contained.is_true
    assert decide_path(facts).allow


def test_traversal_sai_da_raiz(workspace_root: Path) -> None:
    """Mesmo que a sintaxe passasse, os fatos mostram o escape."""
    facts = inspect("../outside/loot.txt", workspace_root)

    assert facts.contained.is_false
    decision = decide_path(facts)
    assert not decision.allow
    assert decision.rule_id == "path.escapes_root"


def test_alvo_inexistente_com_pai_valido(workspace_root: Path) -> None:
    facts = inspect("src/ainda-nao-existe.py", workspace_root)

    assert not facts.exists
    assert facts.contained.is_true
    assert facts.parent_identity is not None
    assert facts.target_identity is None


def test_raiz_inexistente_produz_erro_de_inspecao(tmp_path: Path) -> None:
    facts = inspect("x.txt", tmp_path / "nao-existe")

    assert facts.inspection_error is not None
    assert facts.contained.is_unknown
    assert not decide_path(facts).allow


def test_fatos_de_sintaxe_windows_sao_registrados(workspace_root: Path) -> None:
    unc = inspect("//servidor/share/x", workspace_root)
    assert unc.is_unc.is_true

    device = inspect("\\\\?\\C:\\Windows", workspace_root)
    assert device.is_device_namespace.is_true

    drive_relative = inspect("C:arquivo.txt", workspace_root)
    assert drive_relative.is_drive_relative.is_true

    for facts in (unc, device, drive_relative):
        assert not decide_path(facts).allow


def test_secret_dentro_da_raiz_e_negado_sem_abrir(workspace_root: Path) -> None:
    """Classificação por caminho acontece **antes** de qualquer leitura."""
    facts = inspect(".env", workspace_root)

    assert facts.exists
    decision = decide_path(facts)
    assert not decision.allow
    assert decision.rule_id == "path.secret_denied"


def test_env_example_e_segredo_por_padrao(workspace_root: Path) -> None:
    """A denylist congelada de [04] §5 é `.env*` — inclui o template. Fail closed.

    Liberá-lo exige exceção explícita de configuração, nunca heurística de nome.
    """
    facts = inspect(".env.example", workspace_root)
    assert not decide_path(facts).allow

    liberado = SafetyPolicy(secret_allow_exceptions=frozenset({".env.example"}))
    assert decide_path(facts, policy=liberado).allow


@pytest.mark.skipif(sys.platform == "win32", reason="volumes distintos não são simuláveis aqui")
def test_volume_e_capturado(workspace_root: Path) -> None:
    facts = inspect("src/app.py", workspace_root)

    assert facts.volume is not None
    assert facts.volume == facts.root_volume


def test_symlink_para_fora_e_detectado(tmp_path: Path, workspace_root: Path) -> None:
    if not supports_symlinks(tmp_path):
        pytest.skip("symlink indisponível neste ambiente")

    alvo = tmp_path / "outside" / "loot.txt"
    link = workspace_root / "src" / "atalho.txt"
    link.symlink_to(alvo)

    facts = inspect("src/atalho.txt", workspace_root)

    assert facts.is_symlink.is_true or facts.contained.is_false
    assert not decide_path(facts).allow


def test_ancestral_com_symlink_para_fora_e_detectado(tmp_path: Path, workspace_root: Path) -> None:
    if not supports_symlinks(tmp_path):
        pytest.skip("symlink indisponível neste ambiente")

    fora = tmp_path / "outside"
    (fora / "sub").mkdir(parents=True, exist_ok=True)
    (fora / "sub" / "alvo.txt").write_text("x\n", encoding="utf-8")
    (workspace_root / "src" / "escape").symlink_to(fora / "sub", target_is_directory=True)

    facts = inspect("src/escape/alvo.txt", workspace_root)

    assert not decide_path(facts).allow


# ------------------------------------------------------------- open_checked


def test_open_checked_le_arquivo_permitido(workspace_root: Path) -> None:
    with open_checked("src/app.py", workspace_root) as (fd, facts):
        conteudo = os.read(fd, 1024)

    assert b"print" in conteudo
    assert facts.post_open_identity is not None


def test_open_checked_recusa_traversal(workspace_root: Path) -> None:
    with (
        pytest.raises(PathAccessDenied) as erro,
        open_checked("../outside/loot.txt", workspace_root),
    ):
        pass

    assert erro.value.decision.rule_id == "path.parent_traversal"


def test_open_checked_recusa_segredo(workspace_root: Path) -> None:
    with pytest.raises(PathAccessDenied) as erro, open_checked(".env", workspace_root):
        pass

    assert erro.value.decision.rule_id == "path.secret_denied"
    assert erro.value.decision.subject_redacted


def test_open_checked_nunca_trunca(workspace_root: Path) -> None:
    """A regra de ordem de [04] §4: nada é truncado antes da decisão pós-abertura.

    Verificado pela ausência de qualquer flag de truncamento no módulo — a checagem é
    estrutural porque um teste comportamental precisaria de um caminho de escrita, que só
    existe a partir da E7.
    """
    modulo = Path(__file__).resolve().parents[1] / "app" / "path_runtime.py"
    fonte = modulo.read_text(encoding="utf-8")

    assert "O_TRUNC" not in fonte
    assert "w+" not in fonte

    antes = (workspace_root / "src" / "app.py").read_text(encoding="utf-8")
    with open_checked("src/app.py", workspace_root):
        pass
    assert (workspace_root / "src" / "app.py").read_text(encoding="utf-8") == antes


def test_open_checked_valida_pos_abertura(workspace_root: Path) -> None:
    with open_checked("src/app.py", workspace_root) as (_fd, facts):
        assert facts.target_identity == facts.post_open_identity


def test_fatos_de_link_nunca_mentem_como_falso(workspace_root: Path) -> None:
    """Em plataforma sem junction, `FALSE` é verdade; onde não dá para saber, `UNKNOWN`."""
    facts = inspect("src/app.py", workspace_root)

    if sys.platform != "win32":
        assert facts.is_junction is Tri.FALSE
        assert facts.is_reparse_point is Tri.FALSE
    else:
        assert facts.is_junction in (Tri.FALSE, Tri.UNKNOWN)
