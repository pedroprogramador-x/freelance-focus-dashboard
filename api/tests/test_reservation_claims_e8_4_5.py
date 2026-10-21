"""E8.4.5 / I4 — os claims que autorizam efeitos exigem a reserva `active` e sem drenagem pedida.

A reserva é lida **dentro** da transação do claim, depois de uma escrita (lock de escrita):
é a fronteira de autorização, não a leitura consultiva que veio antes. Os cenários forçam a reserva
a um estado que não autoriza efeitos **com a task ainda `executing`** — o caso que só o predicado de
reserva enxerga (o de `cancel_requested`/`status` não).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings
from app.db.enums import RunAgent
from app.db.models import DevWorkspace, Run
from app.orchestrator import ConcurrentTaskUpdate, cancel
from app.orchestrator import execution_manager as em
from app.orchestrator.developer_invocation_contract import ClaimOutcome
from app.orchestrator.reservation_ledger import (
    EffectsGate,
    declare_developer,
    effects_gate,
)
from app.orchestrator.workspace_contract import PreparationCode
from tests.developer_invocation_support_e8_4_3 import build_scripted, make_harness
from tests.execution_reservation_support_e8_4_5 import Db, ts
from tests.test_anthropic_messages_developer_adapter_e8_2 import FakeTransport  # noqa: F401
from tests.test_developer_invocation_audit_e8_4_3 import _spec
from tests.test_developer_invocation_e8_4_3 import _no_network, dev_id  # noqa: F401
from tests.test_execution_workspace_e8_4_2 import Harness
from tests.test_verify_and_test_e8_4_4 import _spec as runner_spec
from tests.verify_and_test_support_e8_4_4 import build_rig


@pytest.fixture
def h(
    session: Session,
    session_factory: sessionmaker[Session],
    workspace: DevWorkspace,
    repo_path: Path,
    temp_settings: AppSettings,
    tmp_path: Path,
) -> Harness:
    return make_harness(session, session_factory, workspace, repo_path, temp_settings, tmp_path)


@pytest.fixture
def rdb(temp_settings: AppSettings, migrated_url: str):  # type: ignore[no-untyped-def]
    del migrated_url
    db = Db(Path(temp_settings.sqlalchemy_url.split("///", 1)[1]))
    try:
        yield db
    finally:
        db.close()


def _reservation(db: Db, control_run_id: str) -> dict[str, Any]:
    cursor = db.run(
        "SELECT * FROM execution_reservation WHERE control_run_id = ?", (control_run_id,)
    )
    names = [c[0] for c in cursor.description]
    return dict(zip(names, cursor.fetchone(), strict=True))


def _dev_runs(h: Harness, task_id: str) -> list[Run]:
    with h.factory() as fresh:
        rows = list(
            fresh.scalars(
                select(Run).where(Run.task_id == task_id, Run.agent == RunAgent.DEVELOPER)
            )
        )
        fresh.expunge_all()
        return rows


def _fresh(h: Harness) -> Session:
    """A sessão do harness sem estado velho: o identity map não pode mostrar a task de antes."""
    h.session.commit()
    h.session.expire_all()
    return h.session


def _request_drain(db: Db, control_run_id: str) -> None:
    """O pedido de drenagem, como a tx1 do cancelamento o grava — mas **sem** cancelar a task."""
    row = _reservation(db, control_run_id)
    db.update_reservation(row["id"], drain_requested_at=ts(2))


# ------------------------------------------------------------- Developer: claim e declaração


def test_o_claim_do_developer_declara_o_developer_atomicamente(h: Harness, rdb: Db) -> None:
    rig = build_scripted(h)
    before = _reservation(rdb, rig.control_run_id)
    assert before["developer_declared_at"] is None

    claim = em.open_developer_run(_fresh(h), rig.task.id, spec=_spec(rig))

    assert claim.outcome is ClaimOutcome.CLAIMED
    after = _reservation(rdb, rig.control_run_id)
    assert after["developer_declared_at"] is not None
    assert after["version"] == before["version"] + 1 and after["state"] == "active"
    assert len(_dev_runs(h, rig.task.id)) == 1


def test_falha_na_declaracao_desfaz_o_run_do_developer(
    h: Harness, rdb: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = build_scripted(h)

    def boom(*_a: Any, **_k: Any) -> bool:
        raise RuntimeError("falha injetada na declaração")

    monkeypatch.setattr("app.orchestrator.execution_manager.declare_developer", boom)
    with pytest.raises(RuntimeError, match="declaração"):
        em.open_developer_run(_fresh(h), rig.task.id, spec=_spec(rig))

    assert _dev_runs(h, rig.task.id) == []
    assert _reservation(rdb, rig.control_run_id)["developer_declared_at"] is None


def test_depois_do_pedido_de_drenagem_o_claim_e_recusado_com_a_task_ainda_executing(
    h: Harness, rdb: Db
) -> None:
    rig = build_scripted(h)
    _request_drain(rdb, rig.control_run_id)
    h.session.commit()
    assert h.task(rig.task.id).status.value == "executing"  # nada de cancelamento da task

    claim = em.open_developer_run(_fresh(h), rig.task.id, spec=_spec(rig))

    assert claim.outcome is ClaimOutcome.REFUSED and claim.code is PreparationCode.DRAIN_REQUESTED
    assert _dev_runs(h, rig.task.id) == []  # o INSERT foi desfeito
    row = _reservation(rdb, rig.control_run_id)
    assert row["developer_declared_at"] is None  # recusa a declaração depois do pedido


@pytest.mark.parametrize("state", ["teardown_pending", "unproven"])
def test_reserva_fora_de_active_nao_autoriza_claim(h: Harness, rdb: Db, state: str) -> None:
    rig = build_scripted(h)
    row = _reservation(rdb, rig.control_run_id)
    fields: dict[str, Any] = {"state": "teardown_pending"}
    rdb.update_reservation(row["id"], **fields)
    if state == "unproven":
        rdb.update_reservation(
            row["id"], state="unproven", ever_unproven=1, first_unproven_at=ts(9)
        )
    h.session.commit()

    claim = em.open_developer_run(_fresh(h), rig.task.id, spec=_spec(rig))

    assert claim.outcome is ClaimOutcome.REFUSED
    assert claim.code is PreparationCode.RESERVATION_NOT_ACTIVE
    assert _dev_runs(h, rig.task.id) == []


def test_a_releitura_tambem_enxerga_a_drenagem_pedida(h: Harness, rdb: Db) -> None:
    rig = build_scripted(h)
    _request_drain(rdb, rig.control_run_id)
    h.session.commit()
    h.session.expire_all()

    facts = em.read_preparation_facts(
        _fresh(h),
        rig.task.id,
        control_run_id=rig.control_run_id,
        developer_binding_resolver=None,
    )
    assert facts.code is PreparationCode.DRAIN_REQUESTED


def test_a_publicacao_da_worktree_nao_acontece_depois_do_pedido_de_drenagem(
    h: Harness, rdb: Db
) -> None:
    task = h.approve()
    admission = h.admit(task.id)
    assert admission.run is not None
    _request_drain(rdb, admission.run.id)
    h.session.commit()
    version = h.task(task.id).version

    with pytest.raises(ConcurrentTaskUpdate):
        em.publish_prepared_workspace(
            _fresh(h),
            task.id,
            control_run_id=admission.run.id,
            expected_version=version,
            worktree_path=str(h.target(task.id)),
        )

    assert h.task(task.id).worktree_path is None
    assert h.run(admission.run.id).worktree_path is None


def test_limitacao_aceita_o_trigger_da_0004_nao_impede_a_declaracao_apos_o_pedido(
    h: Harness, rdb: Db
) -> None:
    """RISCO REGISTRADO: nenhum trigger da 0004 impede `developer_declared_at` depois de
    `drain_requested_at` (a migration é congelada). Quem impede é o SERVIÇO — este teste prova as
    duas metades: o banco aceita a escrita crua, e `declare_developer` não a faz."""
    task = h.approve()
    admission = h.admit(task.id)
    assert admission.run is not None
    cid = admission.run.id
    _request_drain(rdb, cid)
    h.session.commit()

    assert declare_developer(_fresh(h), task_id=task.id, control_run_id=cid, now=_now()) is False
    h.session.rollback()
    assert _reservation(rdb, cid)["developer_declared_at"] is None

    rdb.update_reservation(_reservation(rdb, cid)["id"], developer_declared_at=ts(4))  # aceito!
    assert _reservation(rdb, cid)["developer_declared_at"] is not None


# ------------------------------------------------------------------ gate de efeitos (unidade)


def test_effects_gate_cobre_cada_motivo_e_exige_identidade_e_declaracao(
    h: Harness, rdb: Db
) -> None:
    task = h.approve()
    admission = h.admit(task.id)
    assert admission.run is not None
    cid = admission.run.id
    s = h.session

    assert effects_gate(s, task_id=task.id, control_run_id=cid) is EffectsGate.OK
    assert effects_gate(s, task_id=task.id, control_run_id=cid, require_developer=True) is (
        EffectsGate.DEVELOPER_NOT_DECLARED
    )
    assert effects_gate(s, task_id=task.id, control_run_id="x") is EffectsGate.MISSING
    # a reserva é da OUTRA task: o `task_id` também precisa casar
    assert effects_gate(s, task_id="y", control_run_id=cid) is EffectsGate.MISSING

    _request_drain(rdb, cid)
    s.expire_all()
    s.commit()
    assert effects_gate(s, task_id=task.id, control_run_id=cid) is EffectsGate.DRAIN_REQUESTED
    s.commit()
    rdb.update_reservation(_reservation(rdb, cid)["id"], state="teardown_pending")
    assert effects_gate(s, task_id=task.id, control_run_id=cid) is EffectsGate.NOT_ACTIVE


# ------------------------------------------------------------------------ Test Runner


def test_o_claim_do_runner_exige_reserva_active_sem_drenagem(h: Harness, rdb: Db) -> None:
    rig = build_rig(h)
    _request_drain(rdb, rig.control_run_id)
    h.session.commit()

    claim = em.open_runner_run(_fresh(h), rig.dev.task.id, spec=runner_spec(rig))

    assert claim.outcome is ClaimOutcome.REFUSED and claim.code is PreparationCode.DRAIN_REQUESTED
    with h.factory() as fresh:
        assert (
            fresh.scalar(select(Run.id).where(Run.invocation_id == runner_spec(rig).invocation_id))
            is None
        )


# ------------------------------------------------------------ cancelamento × claim


def test_cancelar_antes_do_claim_recusa_o_claim_sem_declarar(h: Harness, rdb: Db) -> None:
    rig = build_scripted(h)
    cancel(_fresh(h), rig.task.id)
    h.session.commit()

    claim = em.open_developer_run(_fresh(h), rig.task.id, spec=_spec(rig))

    assert claim.outcome is ClaimOutcome.REFUSED
    assert _dev_runs(h, rig.task.id) == []
    row = _reservation(rdb, rig.control_run_id)
    assert row["developer_declared_at"] is None and row["drain_requested_at"] is not None


def test_cancel_concorrente_durante_o_claim_nunca_deixa_estado_pela_metade(
    h: Harness, rdb: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O claim está dentro da transação (lock de escrita tomado) quando o cancelamento chega em
    outra conexão. SQLite serializa: o claim vence e o cancelamento — que leu antes — perde a
    corrida (409), sem sobrescrever nada; repetido depois, ele passa e grava o pedido APÓS a
    declaração. Nunca existe declaração depois do pedido nem pedido sem cancelamento."""
    rig = build_scripted(h)
    real = declare_developer
    outcome: dict[str, Any] = {}

    def cancel_in_other_connection() -> None:
        try:
            with h.factory() as other:
                cancel(other, rig.task.id)
                other.commit()
            outcome["error"] = None
        except Exception as error:
            outcome["error"] = error

    def declare_while_cancel_waits(*args: Any, **kwargs: Any) -> bool:
        thread = threading.Thread(target=cancel_in_other_connection)
        thread.start()
        thread.join(timeout=0.5)  # o cancelamento fica preso no lock de escrita do claim
        outcome["thread"] = thread
        outcome["blocked"] = thread.is_alive()
        return real(*args, **kwargs)

    monkeypatch.setattr(
        "app.orchestrator.execution_manager.declare_developer", declare_while_cancel_waits
    )
    claim = em.open_developer_run(_fresh(h), rig.task.id, spec=_spec(rig))
    outcome["thread"].join(timeout=30)

    assert claim.outcome is ClaimOutcome.CLAIMED
    row = _reservation(rdb, rig.control_run_id)
    assert row["developer_declared_at"] is not None
    # o SQLite devolve BUSY na hora a uma leitura que tenta virar escrita com outro escritor ativo:
    # o cancelamento perde a corrida (409) com o claim ainda na transação, sem aplicar nada.
    assert isinstance(outcome["error"], ConcurrentTaskUpdate)
    assert row["drain_requested_at"] is None
    assert h.task(rig.task.id).status.value == "executing"
    cancel(_fresh(h), rig.task.id)  # repetido depois do claim: passa
    h.session.commit()
    row = _reservation(rdb, rig.control_run_id)
    assert row["drain_requested_at"] is not None
    assert row["drain_requested_at"] >= row["developer_declared_at"]
    assert h.task(rig.task.id).status.value == "cancelled"


def _now() -> datetime:
    return datetime.now(UTC)
