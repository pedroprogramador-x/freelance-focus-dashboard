"""E8.4.5 / I4 — arquitetura fail-closed: nada operacional foi habilitado.

Estes testes leem o **código** de `app/` (AST) e travam o que o I4 prometeu NÃO fazer: nenhuma
implementação de identidade de reserva, nenhuma admissão fora dos testes, nenhum escopo/observer
operacional, nenhuma escrita no ledger/evidência, nenhum release, nenhuma rota nova e nenhum
`PROVEN_CURRENT`. Mais o contrato HTTP intacto e o `reconcile_on_startup` que não toca a reserva.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import AppSettings
from app.db.enums import FailureReason, TaskStatus
from app.db.models import DevWorkspace, WorkspaceTask
from app.orchestrator import execution_manager as em
from app.orchestrator import reconcile_on_startup
from tests.admission_support_e8_4_1 import admit, approved_task
from tests.execution_reservation_support_e8_4_5 import Db

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
ALL_FILES = sorted(p for p in APP_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _rel(path: Path) -> str:
    return path.relative_to(APP_ROOT).as_posix()


def _call_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            names.append(func.id if isinstance(func, ast.Name) else getattr(func, "attr", ""))
    return names


# ------------------------------------------------------------ identidade da reserva (D3)


def test_nenhuma_implementacao_de_identidade_de_reserva_existe_em_app() -> None:
    """Só o `Protocol` declara `reservation_identity`. Sem implementação em `app/`, não existe
    identidade autorizada e a admissão de produção permanece bloqueada."""
    found: list[tuple[str, str]] = []
    for path in ALL_FILES:
        for cls in [n for n in ast.walk(_tree(path)) if isinstance(n, ast.ClassDef)]:
            for item in cls.body:
                if isinstance(item, ast.FunctionDef) and item.name == "reservation_identity":
                    found.append((_rel(path), cls.name))
    assert found == [("orchestrator/reservation_ledger.py", "ReservationIdentityProvider")]


def test_admit_execution_nao_e_chamada_por_nenhum_modulo_de_app() -> None:
    for path in ALL_FILES:
        assert "admit_execution" not in _call_names(_tree(path)), _rel(path)
    for path in (APP_ROOT / "api").rglob("*.py"):
        assert "admit_execution" not in path.read_text(encoding="utf-8"), _rel(path)
    assert "admit_execution" not in (APP_ROOT / "main.py").read_text(encoding="utf-8")


def test_a_assinatura_exige_a_porta_de_identidade_sem_valor_padrao() -> None:
    [fn] = [
        n
        for n in _tree(APP_ROOT / "orchestrator" / "execution_manager.py").body  # type: ignore[attr-defined]
        if isinstance(n, ast.FunctionDef) and n.name == "admit_execution"
    ]
    kwonly = {a.arg: d for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=True)}
    assert "reservation_identity" in kwonly and kwonly["reservation_identity"] is None


# ----------------------------------------------- nada operacional (escopo, observer, ledger)


def test_nenhum_escopo_de_lancamento_operacional_e_instalado() -> None:
    allowed = {"process_runtime/launch.py", "process_runtime/supervisor.py"}
    for path in ALL_FILES:
        if _rel(path) in allowed:
            continue
        names = _call_names(_tree(path))
        for forbidden in ("install_scope", "LaunchScope", "ProtectedLaunch", "LaunchGate"):
            assert forbidden not in names, f"{_rel(path)} usa {forbidden}"


#: Entrega A: o ledger e a evidência têm **um** escritor cada, e é um módulo nomeado. Qualquer
#: outro módulo de `app/` que construa essas linhas, ou escreva SQL nelas, falha aqui.
LEDGER_WRITER = "orchestrator/launch_ledger.py"
EVIDENCE_WRITER = "orchestrator/evidence_ledger.py"


def test_so_os_dois_escritores_nomeados_escrevem_o_ledger_e_a_evidencia() -> None:
    constructors = {"ExecutionLaunch": LEDGER_WRITER, "ExecutionEvidence": EVIDENCE_WRITER}
    for path in ALL_FILES:
        text = path.read_text(encoding="utf-8").lower()
        names = _call_names(_tree(path))
        for model, owner in constructors.items():
            if model in names:
                assert _rel(path) == owner, f"{_rel(path)} constrói {model} (dono: {owner})"
        for fragment in (
            "insert into execution_launch",
            "insert into execution_evidence",
            "update execution_launch",
            "delete from execution_launch",
            "delete from execution_evidence",
        ):
            assert fragment not in text, f"{_rel(path)}: {fragment}"
    importers = {
        (_rel(path), name)
        for path in ALL_FILES
        for n in ast.walk(_tree(path))
        if isinstance(n, ast.ImportFrom)
        for name in (a.name for a in n.names)
        if name in constructors and not _rel(path).startswith("db/")
    }
    assert importers == {(LEDGER_WRITER, "ExecutionLaunch"), (EVIDENCE_WRITER, "ExecutionEvidence")}
    # o ledger nunca apaga: os dois módulos não têm `delete`
    for owner in (LEDGER_WRITER, EVIDENCE_WRITER):
        assert "delete" not in _call_names(_tree(APP_ROOT / owner)), owner


def test_nenhum_modulo_de_producao_instala_o_observer_o_escritor_ou_o_executor() -> None:
    """A é o componente, não a composição: nada em `app/` instancia o observer, o escritor de
    evidência nem o executor, e nada declara um contexto de lançamento. A integração é a C."""
    own = {
        "SqliteLaunchObserver": LEDGER_WRITER,
        "EvidenceWriter": EVIDENCE_WRITER,
        "InProcessExecutor": "orchestrator/executor_drain.py",
        "bind_launch_context": "process_runtime/launch.py",
    }
    for path in ALL_FILES:
        names = _call_names(_tree(path))
        for name in own:
            if name in names:
                # só dentro do próprio módulo que o define (ex.: `drain_with_evidence` valida tipos)
                assert _rel(path) == own[name], f"{_rel(path)} usa {name}"


def test_nenhum_modulo_de_producao_produz_o_tag_other_nem_o_deixa_por_omissao() -> None:
    from app.process_runtime.launch import LaunchPhaseTag

    assert {t.value for t in LaunchPhaseTag} == {"preparation", "developer", "test_runner"}
    for rel in (LEDGER_WRITER, EVIDENCE_WRITER, "process_runtime/launch.py"):
        text = (APP_ROOT / rel).read_text(encoding="utf-8")
        assert "PhaseTag.OTHER" not in text and '"other"' not in text, rel


def test_o_executor_exige_a_conferencia_de_ordem_sem_valor_padrao() -> None:
    from app.orchestrator.executor_drain import InProcessExecutor

    [init] = [
        n
        for n in ast.walk(_tree(APP_ROOT / "orchestrator" / "executor_drain.py"))
        if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    ]
    kwonly = {a.arg: d for a, d in zip(init.args.kwonlyargs, init.args.kw_defaults, strict=True)}
    assert kwonly == {"rank": None}
    with pytest.raises(TypeError):
        InProcessExecutor()  # type: ignore[call-arg]


def test_os_escritores_da_a_nao_importam_o_runtime_de_processo_nem_dao_release() -> None:
    for rel in (LEDGER_WRITER, EVIDENCE_WRITER, "orchestrator/executor_drain.py"):
        tree = _tree(APP_ROOT / rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("app.process_runtime"), rel
            elif isinstance(node, ast.Import):
                assert not any(a.name.startswith("app.process_runtime") for a in node.names), rel
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            for kw in call.keywords:
                assert kw.arg not in {"released_at", "released_by_token", "release_evidence_hash"}


def test_nenhum_codigo_de_producao_libera_reserva_nem_produz_proven_current() -> None:
    for path in ALL_FILES:
        tree = _tree(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg in {"released_at", "released_by_token", "release_evidence_hash"}:
                    raise AssertionError(f"{_rel(path)} grava {kw.arg}")
                if kw.arg == "state":
                    value = kw.value
                    released = (isinstance(value, ast.Name) and value.id == "STATE_RELEASED") or (
                        isinstance(value, ast.Constant) and value.value == "released"
                    )
                    assert not released, f"{_rel(path)} leva a reserva a `released`"
        if _rel(path) != "orchestrator/teardown_contract.py":
            assert "PROVEN_CURRENT" not in path.read_text(encoding="utf-8"), _rel(path)


def test_o_ledger_e_o_drain_nao_dao_commit_nem_importam_o_runtime_de_processo() -> None:
    ledger = APP_ROOT / "orchestrator" / "reservation_ledger.py"
    assert "commit" not in _call_names(_tree(ledger)), "o ledger compõe a transação do chamador"
    for name in ("reservation_ledger.py", "drain.py"):
        for node in ast.walk(_tree(APP_ROOT / "orchestrator" / name)):
            if isinstance(node, ast.ImportFrom | ast.Import):
                module = node.module if isinstance(node, ast.ImportFrom) else None
                assert not (module or "").startswith("app.process_runtime"), name


# --------------------------------------------------------------------- sem rotas novas


def test_nenhuma_rota_nova_expoe_drenagem_cancelamento_com_gate_ou_identidade() -> None:
    names = (
        "cancel_and_drain",
        "DrainCoordinator",
        "GateRegistry",
        "ReservationIdentity",
        "reservation_ledger",
        "orchestrator.drain",
    )
    for path in [*(APP_ROOT / "api").rglob("*.py"), APP_ROOT / "main.py"]:
        text = path.read_text(encoding="utf-8")
        for name in names:
            assert name not in text, f"{_rel(path)} menciona {name}"
        assert "/drain" not in text and "/execute" not in text


def test_a_rota_de_cancelamento_continua_chamando_cancel_sem_coordinator() -> None:
    tree = _tree(APP_ROOT / "api" / "tasks.py")
    [route] = [
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "cancel_route"
    ]
    assert [a.arg for a in route.args.args] == ["task_id", "payload", "session"]
    calls = [c for c in ast.walk(route) if isinstance(c, ast.Call)]
    names = _call_names(route)
    assert "cancel" in names and "cancel_and_drain" not in names
    [cancel_call] = [c for c in calls if isinstance(c.func, ast.Name) and c.func.id == "cancel"]
    assert {k.arg for k in cancel_call.keywords} == {"note"}


def test_os_comandos_novos_passam_pela_unidade_de_trabalho() -> None:
    assert {"cancel", "cancel_and_drain", "admit_execution"} <= em.GUARDED_COMMANDS


# -------------------------------------------------------------- contrato HTTP e recovery


def test_contrato_http_do_cancelamento_nao_mudou_com_reserva(
    auth_api_client: TestClient,
    session: Session,
    workspace: DevWorkspace,
    tmp_path: Path,
    temp_settings: AppSettings,
) -> None:
    from app.api.tasks import TaskResponse

    artifacts = tmp_path / "artifacts"
    task = approved_task(session, workspace, artifacts)
    assert admit(session, task.id, artifacts, key="k1").run is not None
    session.commit()

    response = auth_api_client.post(f"/api/tasks/{task.id}/cancel", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert set(body) == set(TaskResponse.model_fields)  # nenhum campo de drenagem/reserva
    again = auth_api_client.post(f"/api/tasks/{task.id}/cancel", json={})
    assert again.status_code == 409 and again.json()["code"] == "invalid_transition"
    db = Db(Path(temp_settings.sqlalchemy_url.split("///", 1)[1]))
    try:
        state, requested = db.one(
            "SELECT state, drain_requested_at FROM execution_reservation WHERE task_id = ?",
            (task.id,),
        ) or (None, None)
    finally:
        db.close()
    assert state == "active" and requested is not None  # só o pedido: nada linearizado/liberado


def test_reconcile_on_startup_nao_toca_a_reserva_e_o_slot_continua_ocupado(
    session: Session,
    workspace: DevWorkspace,
    tmp_path: Path,
    temp_settings: AppSettings,
) -> None:
    artifacts = tmp_path / "artifacts"
    primeira = approved_task(session, workspace, artifacts)
    segunda = approved_task(session, workspace, artifacts, title="segunda")
    assert admit(session, primeira.id, artifacts, key="k1").run is not None
    session.commit()
    db = Db(Path(temp_settings.sqlalchemy_url.split("///", 1)[1]))
    try:
        before = db.one("SELECT * FROM execution_reservation WHERE task_id = ?", (primeira.id,))
        session.expire_all()

        assert reconcile_on_startup(session) == (primeira.id,)
        session.commit()
        session.expire_all()

        task = session.get(WorkspaceTask, primeira.id)
        assert task is not None
        assert task.status is TaskStatus.FAILED and task.failure_reason is FailureReason.INTERRUPTED
        assert db.one("SELECT * FROM execution_reservation WHERE task_id = ?", (primeira.id,)) == (
            before
        )
        # a reserva não liberada (sem recovery até o I5/I6) segue segurando o slot global
        busy = admit(session, segunda.id, artifacts, key="k2")
        assert busy.outcome.value == "slot_busy"
    finally:
        db.close()


@pytest.mark.parametrize("name", ["start_execution"])
def test_start_execution_continua_sem_continuacao_operacional(name: str) -> None:
    source = (APP_ROOT / "orchestrator" / "execution_manager.py").read_text(encoding="utf-8")
    assert "raise NotImplementedError" in source
    assert hasattr(em, name)
