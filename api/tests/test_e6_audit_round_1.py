"""Regressões da **primeira auditoria independente da E6** (Codex, 2026-09-13).

Um teste — ou um grupo nomeado — por finding, com o identificador no nome. Cada um
reproduz o cenário exato do relatório e **falhava antes** da correção desta rodada: o que
o auditor observou está descrito no docstring, para que a próxima leitura não precise do
relatório ao lado.

Dois findings não moram aqui, e é de propósito:

* **E6-AUD-006** (detector AST de `print`) vive em `test_auth_and_bootstrap.py`, junto do
  detector que ele exercita — a recomendação da auditoria era justamente parar de
  reimplementar o predicado longe dele;
* **E6-AUD-012** (guarda de capability) tem os quatro casos em
  `test_orchestrator_state_machine.py`, onde a guarda pura é testada sem banco.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings
from app.context_engine import select_context
from app.db.enums import RiskLevel, TaskStatus
from app.db.models import SafetyEvent, WorkspaceTask
from app.db.session import session_scope
from app.orchestrator import execution_manager as em
from app.orchestrator.analyzer import AnalyzerEnrichment, analyze
from app.orchestrator.errors import (
    ApprovalFingerprintMismatch,
    ConcurrentTaskUpdate,
    TransitionGuardFailed,
)
from app.safety import redact
from tests import context_helpers
from tests.conftest import LOCAL_BASE_URL
from tests.test_api_tasks import VALID_TEST_CONFIG, repo, task_id, workspace_id

# `repo`/`workspace_id`/`task_id` são reexportados para o pytest enxergá-los como fixtures
# deste módulo; `ruff` não sabe disso sozinho.
__all__ = ["repo", "task_id", "workspace_id"]

#: Alvo do `monkeypatch` que simula a falha do trabalho longo. Pelo caminho textual, e não
#: por `planner.select_context`: o planner **importa** o símbolo do `context_engine`, então
#: substituí-lo lá é o que a chamada real enxerga — e nomear o alvo por string deixa
#: explícito que o objeto patcheado é o do módulo consumidor, não o do dono.
_SELECT_CONTEXT_NO_PLANNER = "app.orchestrator.planner.select_context"


# ------------------------------------------------------------------ utilidades


def _plan(client: TestClient, task: str, paths: tuple[str, ...] = ()) -> dict[str, Any]:
    response = client.post(f"/api/tasks/{task}/plan", json={"candidate_paths": list(paths)})
    assert response.status_code == 200, response.text
    return dict(response.json())


def _approve(client: TestClient, task: str, fingerprint: str) -> Any:
    return client.post(f"/api/tasks/{task}/approve", json={"execution_fingerprint": fingerprint})


def _plan_and_approve(client: TestClient, task: str) -> dict[str, Any]:
    planned = _plan(client, task)
    response = _approve(client, task, planned["execution_fingerprint"])
    assert response.status_code == 200, response.text
    return dict(response.json())


def _events(factory: sessionmaker[Session], task: str) -> list[tuple[str, str]]:
    """Eventos lidos por uma sessão **nova** — a única leitura que prova durabilidade."""
    with factory() as fresh:
        return [
            (event.kind.value, event.rule_id)
            for event in fresh.scalars(select(SafetyEvent).where(SafetyEvent.task_id == task))
        ]


def _reload(factory: sessionmaker[Session], task: str) -> WorkspaceTask:
    with factory() as fresh:
        loaded = fresh.get(WorkspaceTask, task)
        assert loaded is not None
        fresh.expunge(loaded)
        return loaded


# ================================================================== E6-AUD-001


class TestAud001InvalidacaoNaEntradaTransiciona:
    """A recusa na entrada de execução tem de **executar** `approved → awaiting_approval`.

    O que o auditor observou: as guardas recusavam (`fingerprint_still_valid` e
    `head_matches_planning_base_commit`), o `SafetyEvent` era persistido — e a task
    continuava `approved`, com `approved_at` preenchido e o fingerprint velho. Um estado
    que afirma "um humano aprovou exatamente isto" sobre um plano que já não vale, e sem
    saída: `/approve` e `/reject` respondem 409 a partir de `approved`.
    """

    def test_fingerprint_divergente_rebaixa_para_awaiting_approval_com_diagnostico(
        self,
        auth_api_client: TestClient,
        workspace_id: str,
        task_id: str,
        session_factory: sessionmaker[Session],
    ) -> None:
        aprovada = _plan_and_approve(auth_api_client, task_id)

        # Muda algo coberto pelo fingerprint **sem** mexer no `HEAD`.
        assert (
            auth_api_client.patch(
                f"/api/workspaces/{workspace_id}", json={"test_config": VALID_TEST_CONFIG}
            ).status_code
            == 200
        )

        with (
            pytest.raises(ApprovalFingerprintMismatch) as erro,
            session_scope(session_factory) as active,
        ):
            em.start_execution(active, task_id)

        # O diagnóstico que faltava: **qual campo** divergiu ([06] §2).
        assert "test_binding" in erro.value.diverged_fields
        assert erro.value.received == aprovada["execution_fingerprint"]

        depois = _reload(session_factory, task_id)
        assert depois.status is TaskStatus.AWAITING_APPROVAL
        assert depois.approved_at is None
        # O candidato foi atualizado para o valor vigente: reaprovar resolve, sem replanejar.
        assert depois.approved_fingerprint == erro.value.expected
        assert depois.approved_manifest_id is not None

        assert (
            "approval_invalidated",
            "orchestrator.entry_guard.fingerprint_still_valid",
        ) in _events(session_factory, task_id)

        # E a task deixou de ser um beco: reaprovar volta a funcionar.
        reaprovada = _approve(auth_api_client, task_id, erro.value.expected)
        assert reaprovada.status_code == 200, reaprovada.text
        assert reaprovada.json()["status"] == "approved"

    def test_head_alterado_exige_replanejar_e_nao_troca_a_base_congelada(
        self,
        auth_api_client: TestClient,
        task_id: str,
        repo: Path,
        session_factory: sessionmaker[Session],
    ) -> None:
        """[02] §6: a execução nunca salta para o `HEAD` novo, e a base não é substituída.

        Reaprovar **não** poderia resolver: o fingerprint cobre `base_commit`, que continua
        sendo o commit congelado — ele bateria de novo e a guarda recusaria de novo, um
        laço. Por isso o candidato é limpo e o caminho é um `POST /plan` novo.
        """
        aprovada = _plan_and_approve(auth_api_client, task_id)
        base = aprovada["planning_base_commit"]

        context_helpers.write(repo, "next.txt", "commit posterior ao plano\n")
        context_helpers.commit_all(repo, "depois do plano")

        with (
            pytest.raises(TransitionGuardFailed) as erro,
            session_scope(session_factory) as active,
        ):
            em.start_execution(active, task_id)

        assert erro.value.guard == "head_matches_planning_base_commit"
        assert erro.value.as_payload()["requires_replan"] is True

        depois = _reload(session_factory, task_id)
        assert depois.status is TaskStatus.AWAITING_APPROVAL
        assert depois.approved_at is None
        assert depois.approved_manifest_id is None
        assert depois.approved_fingerprint is None
        # A base congelada **não** foi trocada por baixo dos panos.
        assert depois.planning_base_commit == base

        assert (
            "approval_invalidated",
            "orchestrator.entry_guard.head_matches_planning_base_commit",
        ) in _events(session_factory, task_id)

        # Aprovar sem replanejar é recusado com o motivo certo…
        recusa = _approve(auth_api_client, task_id, aprovada["execution_fingerprint"])
        assert recusa.status_code == 409
        assert recusa.json()["code"] == "approval_fingerprint_mismatch"

        # …e o caminho de replanejamento existe e funciona.
        assert auth_api_client.post(f"/api/tasks/{task_id}/reject", json={}).status_code == 200
        replanejada = _plan(auth_api_client, task_id)
        assert replanejada["planning_base_commit"] != base

    def test_guarda_que_nao_invalida_preserva_a_aprovacao(
        self, auth_api_client: TestClient, task_id: str, session_factory: sessionmaker[Session]
    ) -> None:
        """Contrafactual: só as duas guardas de invalidação rebaixam a task.

        `capability_profile_proven` é a ausência de E7, não uma aprovação que caducou.
        Rebaixar a task aqui faria o humano reaprovar um plano que não mudou em nada — e
        faria a correção de AUD-001 virar um defeito novo.
        """
        _plan_and_approve(auth_api_client, task_id)

        with (
            pytest.raises(TransitionGuardFailed) as erro,
            session_scope(session_factory) as active,
        ):
            em.start_execution(active, task_id)

        assert erro.value.guard == "capability_profile_proven"
        depois = _reload(session_factory, task_id)
        assert depois.status is TaskStatus.APPROVED
        assert depois.approved_at is not None


# ================================================================== E6-AUD-002


def test_aud002_recusa_por_contexto_stale_em_alto_risco_deixa_safety_event(
    auth_api_client: TestClient,
    workspace_id: str,
    task_id: str,
    repo: Path,
    session_factory: sessionmaker[Session],
) -> None:
    """Terceiro caminho de recusa, que ficou fora de `_commit_audit_trail`.

    O auditor observou `409 no_stale_entry_when_high_risk` com **zero** `SafetyEvent` numa
    sessão nova: o evento nem chegava a ser criado. É uma recusa de política — exatamente o
    critério de [02] §4 para exigir trilha.
    """
    criada = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context",
        json={
            "domain": "objective",
            "title": "goal",
            "body": "um objetivo valido",
            "source_refs": ["src/app.py"],
        },
    )
    assert criada.status_code == 201, criada.text

    context_helpers.write(repo, "src/app.py", "working tree alterada\n")
    assert (
        auth_api_client.post(f"/api/workspaces/{workspace_id}/context/verify", json={}).status_code
        == 200
    )

    planejada = _plan(auth_api_client, task_id, (".env",))
    assert planejada["risk"] == "high"

    recusa = _approve(auth_api_client, task_id, planejada["execution_fingerprint"])
    assert recusa.status_code == 409, recusa.text
    assert recusa.json()["guard"] == "no_stale_entry_when_high_risk"

    assert (
        "approval_invalidated",
        "orchestrator.approval_guard.no_stale_entry_when_high_risk",
    ) in _events(session_factory, task_id)

    # A recusa não concedeu aprovação nem mexeu no manifest imutável.
    depois = _reload(session_factory, task_id)
    assert depois.status is TaskStatus.AWAITING_APPROVAL
    assert depois.approved_at is None


# ================================================================== E6-AUD-003


_CRASH_SCRIPT = """
import os
import sys
from pathlib import Path

from app.config import AppSettings
from app.db.session import create_engine, create_session_factory, session_scope
from app.orchestrator import execution_manager as em
from app.orchestrator import planner

settings = AppSettings(data_dir=Path(sys.argv[1]), database_url=sys.argv[2])
factory = create_session_factory(create_engine(settings))

# Mata o processo **dentro** do trabalho longo: sem `finally`, sem `rollback`, sem
# `atexit`. É a diferença entre uma exceção Python e um crash de verdade.
planner.select_context = lambda *args, **kwargs: os._exit(23)

with session_scope(factory) as active:
    em.plan(active, sys.argv[3], artifacts_dir=settings.artifacts_dir)
"""


def test_aud003_crash_real_durante_o_planejamento_deixa_planning_recuperavel(
    auth_api_client: TestClient,
    task_id: str,
    temp_settings: AppSettings,
    session_factory: sessionmaker[Session],
) -> None:
    """`planning` precisa existir **no banco**, não só dentro de uma transação aberta.

    O auditor matou o processo com `os._exit(23)` dentro de `select_context` e encontrou a
    task em **`draft`**, com `reconcile_on_startup()` devolvendo `()`. O SQLite descartou a
    transação inteira: separar dois *compare-and-set* não cria estado durável se os dois
    estão na mesma transação. Era o motivo pelo qual [ADR-0008] introduziu o estado.
    """
    del auth_api_client  # só para garantir que a task e o schema existem

    crash = subprocess.run(  # noqa: S603 — argv literal, sem shell
        [
            sys.executable,
            "-c",
            _CRASH_SCRIPT,
            str(temp_settings.data_dir),
            temp_settings.sqlalchemy_url,
            task_id,
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert crash.returncode == 23, crash.stderr

    presa = _reload(session_factory, task_id)
    assert presa.status is TaskStatus.PLANNING, "o estado longo não sobreviveu ao crash"

    with session_scope(session_factory) as active:
        recuperadas = em.reconcile_on_startup(active)
    assert recuperadas == (task_id,), "reconcile_on_startup não encontrou nada a recuperar"

    depois = _reload(session_factory, task_id)
    assert depois.status is TaskStatus.FAILED
    assert depois.failure_reason is not None
    assert depois.failure_reason.value == "interrupted"

    # [ADR-0008] regra 6: rodar duas vezes produz o mesmo estado.
    with session_scope(session_factory) as active:
        assert em.reconcile_on_startup(active) == ()


def test_aud003_planning_e_visivel_para_outra_sessao_durante_o_trabalho_longo(
    auth_api_client: TestClient,
    task_id: str,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A outra metade do finding: o auditor lia **`draft`** enquanto o plano rodava."""
    observado: list[str] = []

    def espiar(*args: Any, **kwargs: Any) -> Any:
        observado.append(_reload(session_factory, task_id).status.value)
        return select_context(*args, **kwargs)

    monkeypatch.setattr(_SELECT_CONTEXT_NO_PLANNER, espiar)
    _plan(auth_api_client, task_id)

    assert observado == ["planning"]


def test_aud003_erro_de_dominio_no_plano_devolve_a_task_para_draft(
    auth_api_client: TestClient, tmp_path: Path, session_factory: sessionmaker[Session]
) -> None:
    """[02] §4, "erro recuperável": a task **não** fica presa, e a volta é durável.

    Este é o contraponto de AUD-003: agora que `planning` é commitado, voltar para `draft`
    passou a ser uma transição explícita — não mais um efeito colateral do `rollback`.
    """
    plano = tmp_path / "sem-git"
    plano.mkdir()
    (plano / "a.txt").write_text("x", encoding="utf-8")

    workspace = auth_api_client.post(
        "/api/workspaces",
        json={"name": "sem-git", "type": "personal", "local_path": str(plano)},
    ).json()
    task = auth_api_client.post(
        f"/api/workspaces/{workspace['id']}/tasks", json={"title": "t", "goal": "g"}
    ).json()

    recusa = auth_api_client.post(f"/api/tasks/{task['id']}/plan", json={})
    assert recusa.status_code == 409
    assert recusa.json()["code"] == "workspace_not_plannable"

    assert _reload(session_factory, task["id"]).status is TaskStatus.DRAFT


def test_aud003_erro_inesperado_no_plano_vira_failed_internal_error(
    auth_api_client: TestClient,
    task_id: str,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[02] §4, "erro de provider · limite": o que não sabemos classificar não vira `draft`.

    Devolver a task para `draft` afirmaria que replanejar resolve — e não sabemos se
    resolve. `failed(internal_error)` é o registro honesto.
    """

    def explodir(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("falha independente do context engine")

    monkeypatch.setattr(_SELECT_CONTEXT_NO_PLANNER, explodir)

    with pytest.raises(RuntimeError):
        auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})

    depois = _reload(session_factory, task_id)
    assert depois.status is TaskStatus.FAILED
    assert depois.failure_reason is not None
    assert depois.failure_reason.value == "internal_error"
    assert ("cancelled", "orchestrator.plan_failed") in _events(session_factory, task_id)


# ================================================================== E6-AUD-004


def test_aud004_corrida_real_do_sqlite_vira_conflito_de_dominio(
    auth_api_client: TestClient, task_id: str, session_factory: sessionmaker[Session]
) -> None:
    """Duas sessões **reais** do engine, sem mock. O perdedor não pode ver `OperationalError`.

    O auditor abriu duas sessões que leram a mesma task em `draft`/v1, cancelou pela
    primeira e tentou cancelar pela segunda mantendo o snapshot: o SQLite recusou com
    `SQLITE_BUSY_SNAPSHOT` **antes** de existir um `rowcount` para o CAS conferir.
    """
    del auth_api_client

    with session_factory() as vencedora, session_factory() as perdedora:
        # As duas leem antes de qualquer escrita: é o que cria o snapshot antigo.
        assert perdedora.get(WorkspaceTask, task_id) is not None
        em.cancel(vencedora, task_id)
        vencedora.commit()

        with pytest.raises(ConcurrentTaskUpdate):
            em.cancel(perdedora, task_id)

    assert _reload(session_factory, task_id).status is TaskStatus.CANCELLED


def test_aud004_o_perdedor_recebe_409_e_nao_500(
    auth_api_client: TestClient,
    task_id: str,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O mesmo conflito atravessando a camada HTTP: `409 concurrent_task_update`.

    O auditor obteve `500 {code: internal_error}` — um conflito previsto por [02] §4
    aparecendo como defeito do backend.
    """
    # O ponto de interleaving é interno de propósito: é onde a corrida real acontece.
    original = em._compare_and_set
    primeira = True

    def interlear(*args: Any, **kwargs: Any) -> Any:
        nonlocal primeira
        if primeira:
            primeira = False
            with session_scope(session_factory) as vencedora:
                em.cancel(vencedora, task_id)
        return original(*args, **kwargs)

    monkeypatch.setattr(em, "_compare_and_set", interlear)

    with TestClient(
        auth_api_client.app, base_url=LOCAL_BASE_URL, raise_server_exceptions=False
    ) as cliente:
        cliente.headers["Authorization"] = auth_api_client.headers["Authorization"]
        resposta = cliente.post(f"/api/tasks/{task_id}/cancel", json={})

    assert resposta.status_code == 409, resposta.text
    assert resposta.json()["code"] == "concurrent_task_update"


def test_aud004_erro_de_banco_que_nao_e_conflito_continua_subindo(
    session_factory: sessionmaker[Session],
) -> None:
    """Contrafactual: a correção **não** pode virar "todo erro de banco é concorrência".

    Um `OperationalError` sem `sqlite_errorname` de conflito — aqui, uma tabela que não
    existe — continua sendo o defeito que é.
    """
    from sqlalchemy.exc import OperationalError

    with session_factory() as active, pytest.raises(OperationalError):
        active.execute(text("SELECT 1 FROM tabela_que_nao_existe"))


# ================================================================== E6-AUD-005


def test_aud005_plan_reverifica_o_contexto_contra_a_base_congelada(
    auth_api_client: TestClient, workspace_id: str, task_id: str, repo: Path
) -> None:
    """[06] §2: "congela `base_commit`, **verifica contexto**, seleciona, renderiza, planeja".

    O auditor criou a entrada no commit A (a API devolveu `fresh`), alterou e **commitou**
    a fonte em B, e planejou: o manifest congelou a entrada como `fresh` e `/approve`
    devolveu 200 em `risk = high`. Só um `/context/verify` manual revelava `stale` depois.
    A guarda de alto risco dependia de o usuário ter clicado em verificar antes de planejar.
    """
    criada = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context",
        json={
            "domain": "objective",
            "title": "goal",
            "body": "um objetivo valido",
            "source_refs": ["src/app.py"],
        },
    )
    assert criada.status_code == 201, criada.text
    assert criada.json()["state"] == "fresh"

    context_helpers.write(repo, "src/app.py", "fonte commitada depois do contexto\n")
    context_helpers.commit_all(repo, "fonte alterada depois do contexto")

    planejada = _plan(auth_api_client, task_id, (".env",))
    assert planejada["risk"] == "high"

    manifest = auth_api_client.get(f"/api/tasks/{task_id}/context").json()
    estados = {item["entry_id"]: item["state"] for item in manifest["entries"]}
    assert estados[criada.json()["id"]] == "stale", "o manifest congelou um `fresh` velho"

    # E a guarda de [02] §4 passa a valer, que é o ponto do finding.
    recusa = _approve(auth_api_client, task_id, planejada["execution_fingerprint"])
    assert recusa.status_code == 409
    assert recusa.json()["guard"] == "no_stale_entry_when_high_risk"


def test_aud005_a_reverificacao_nao_le_head_de_novo(
    auth_api_client: TestClient, workspace_id: str, task_id: str, repo: Path
) -> None:
    """A reverificação usa o commit congelado, não uma segunda captura de `HEAD`.

    Se ela relesse `HEAD`, o `planning_base_commit` do plano e o commit contra o qual o
    contexto foi verificado poderiam ser dois commits diferentes — e o manifest afirmaria
    um frescor medido contra algo que a seleção não enxergou ([02] §6).
    """
    assert (
        auth_api_client.post(
            f"/api/workspaces/{workspace_id}/context",
            json={
                "domain": "objective",
                "title": "goal",
                "body": "um objetivo valido",
                "source_refs": ["src/app.py"],
            },
        ).status_code
        == 201
    )

    planejada = _plan(auth_api_client, task_id)
    manifest = auth_api_client.get(f"/api/tasks/{task_id}/context").json()

    assert manifest["git_head"] == planejada["planning_base_commit"]
    assert manifest["git_head"] == context_helpers.head_of(repo)


# ================================================================== E6-AUD-007


def test_aud007_test_config_nunca_devolve_argv_cru_em_nenhuma_rota(
    auth_api_client: TestClient, workspace_id: str, session_factory: sessionmaker[Session]
) -> None:
    """`argv`, `executable` e `runner_id` são texto livre e vazavam inteiros.

    A credencial é **sintética** e reconhecida pelo redator canônico (`sk-` + 24 letras);
    nenhuma credencial real participa deste teste.
    """
    credencial = "sk-" + ("A" * 24)
    assert redact(credencial) != credencial, "a credencial de teste precisa ser reconhecível"

    url = f"/api/workspaces/{workspace_id}"
    config = {**VALID_TEST_CONFIG, "argv": ["--credential", credencial]}

    patch = auth_api_client.patch(url, json={"test_config": config})
    assert patch.status_code == 200, patch.text

    detalhe = auth_api_client.get(url)
    listagem = auth_api_client.get("/api/workspaces")

    for rotulo, resposta in (("PATCH", patch), ("GET detalhe", detalhe), ("GET lista", listagem)):
        assert credencial not in resposta.text, f"{rotulo} devolveu a credencial crua"

    assert patch.json()["test_config"]["argv"][0] == "--credential"

    # Os bytes operacionais continuam íntegros: só a projeção HTTP é redigida.
    with session_factory() as fresh:
        from app.db.models import DevWorkspace

        workspace = fresh.get(DevWorkspace, workspace_id)
        assert workspace is not None
        assert workspace.test_config is not None
        assert workspace.test_config["argv"] == ["--credential", credencial]


def test_aud007_a_projecao_redigida_nao_pode_ser_reenviada_como_configuracao(
    auth_api_client: TestClient, workspace_id: str
) -> None:
    """O corolário de existir uma projeção diferente do documento.

    Um cliente genérico que fizesse `GET` e devolvesse o corpo num `PATCH` gravaria a
    máscara no lugar do comando — e o Test Runner "rodaria" `«redigido»`, em silêncio,
    porque a máscara é uma string válida para o schema. Recusar na entrada é mais barato do
    que descobrir na E7 por que o teste não roda.
    """
    from app.safety.redaction import REDACTED

    url = f"/api/workspaces/{workspace_id}"
    credencial = "sk-" + ("D" * 24)

    assert (
        auth_api_client.patch(
            url, json={"test_config": {**VALID_TEST_CONFIG, "argv": [credencial]}}
        ).status_code
        == 200
    )

    lido = auth_api_client.get(url).json()["test_config"]
    assert lido["argv"] == [REDACTED]

    devolvido = auth_api_client.patch(url, json={"test_config": lido})
    assert devolvido.status_code == 422, devolvido.text
    assert devolvido.json()["code"] == "invalid_test_config"

    # E o valor real continua intacto.
    assert auth_api_client.get(url).json()["test_config"]["argv"] == [REDACTED]


def test_aud007_o_fingerprint_continua_afirmando_o_comando_real(
    auth_api_client: TestClient, workspace_id: str, task_id: str
) -> None:
    """Redigir a saída **não** pode alterar o hash — seria encobrir, não proteger.

    Duas configurações que só diferem no valor redigido têm de produzir fingerprints
    diferentes: o `command_hash` identifica o comando que de fato vai rodar.
    """
    url = f"/api/workspaces/{workspace_id}"
    primeiro = "sk-" + ("A" * 24)
    segundo = "sk-" + ("B" * 24)

    auth_api_client.patch(url, json={"test_config": {**VALID_TEST_CONFIG, "argv": [primeiro]}})
    um = _plan(auth_api_client, task_id)["execution_fingerprint"]

    assert auth_api_client.post(f"/api/tasks/{task_id}/reject", json={}).status_code == 200
    auth_api_client.patch(url, json={"test_config": {**VALID_TEST_CONFIG, "argv": [segundo]}})
    outro = _plan(auth_api_client, task_id)["execution_fingerprint"]

    assert um != outro


# ================================================================== E6-AUD-008


def test_aud008_falha_operacional_na_recuperacao_nao_e_silenciada(
    auth_api_client: TestClient,
    task_id: str,
    temp_settings: AppSettings,
    session_factory: sessionmaker[Session],
) -> None:
    """Banco **já migrado** + recuperação que falha → o startup não pode subir calado.

    O auditor deixou uma task em `planning` e fez o `UPDATE` da recuperação falhar: o
    lifespan iniciava normalmente, `/health` respondia 200, a task continuava `planning` e
    não havia evento nenhum. `except OperationalError: pass` descrevia uma classe genérica
    de falha de banco — não a condição esperada "schema ainda não migrado".
    """
    from app.main import create_app

    del auth_api_client

    with session_scope(session_factory) as active:
        active.execute(
            text("UPDATE workspace_task SET status = 'planning' WHERE id = :id"),
            {"id": task_id},
        )
        active.execute(
            text(
                "CREATE TRIGGER falha_na_recuperacao BEFORE UPDATE ON workspace_task "
                "BEGIN SELECT RAISE(ABORT, 'falha operacional simulada'); END"
            )
        )

    with (
        pytest.raises(Exception, match="falha operacional simulada"),
        TestClient(create_app(temp_settings), base_url=LOCAL_BASE_URL),
    ):
        pass

    assert _reload(session_factory, task_id).status is TaskStatus.PLANNING


def test_aud008_schema_ainda_nao_migrado_continua_sendo_tolerado(
    temp_settings: AppSettings,
) -> None:
    """A **única** condição esperada, e reconhecida por inspeção em vez de por exceção.

    Falhar o startup aqui impediria o operador de chegar ao terminal para rodar
    `alembic upgrade head`, que é exatamente o que ele precisa fazer.
    """
    from app.main import create_app

    with TestClient(create_app(temp_settings), base_url=LOCAL_BASE_URL) as cliente:
        assert cliente.get("/api/health").status_code == 200


# ================================================================== E6-AUD-009


@pytest.mark.parametrize(
    ("objetivo", "dispara"),
    [
        ("edite o .env", True),
        ("troque a password", True),
        ("atualize api_key", True),
        ("corrija o tokenizer", False),
        ("ajuste secretary page", False),
        ("edite o .env.local", True),
        ("edite o .env.production", True),
    ],
)
def test_aud009_sinal_de_segredo_no_objetivo(objetivo: str, dispara: bool) -> None:
    """[03] §5: `high` quando o objetivo **ou** os candidatos tocam segredos.

    Os cinco primeiros casos são os do relatório; os dois últimos são as variantes que o
    prompt pediu. `tokenizer` e `secretary` são os falsos positivos que uma busca por
    substring produziria — e que a semântica de componentes de `is_sensitive_key` recusa.
    """
    analise = analyze(objetivo, [])

    assert (analise.risk is RiskLevel.HIGH) is dispara
    assert analise.shortcut_applied is False if dispara else True
    marcadas = " ".join(analise.hard_rule.matched)
    assert ("objective_mentions_secret" in marcadas) is dispara


def test_aud009_objetivo_sobre_env_sem_candidatos_nao_sai_low_trivial(
    auth_api_client: TestClient, workspace_id: str
) -> None:
    """A reprodução exata, pela rota: `POST /plan` **sem** `candidate_paths`.

    O auditor obteve `risk=low`, `complexity=trivial`, `shortcut_applied=true` — a forma
    normal que a UI envia.
    """
    criada = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/tasks",
        json={"title": "typo", "goal": "corrigir typo em .env"},
    )
    assert criada.status_code == 201

    planejada = _plan(auth_api_client, criada.json()["id"])
    assert (planejada["risk"], planejada["complexity"]) != ("low", "trivial")
    assert planejada["risk"] == "high"


def test_aud009_o_motivo_nao_ecoa_o_texto_do_objetivo(
    auth_api_client: TestClient, workspace_id: str
) -> None:
    """Um objetivo pode carregar uma credencial colada por engano.

    O diagnóstico da regra viaja como `rule_id` + categoria e **nunca** como o token que
    disparou: ele acabaria no `plan`, no `plan_hash` e na resposta HTTP.
    """
    credencial = "sk-" + ("C" * 24)
    criada = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/tasks",
        json={"title": "t", "goal": f"remova o token {credencial} do .env"},
    )
    assert criada.status_code == 201

    planejada = _plan(auth_api_client, criada.json()["id"])
    marcadas = planejada["plan"]["analysis"]["hard_rule"]["matched"]

    assert "objective:objective_mentions_secret" in marcadas
    assert all(credencial not in item for item in marcadas)


# ================================================================== E6-AUD-010


@pytest.mark.parametrize(("attempts", "esperado"), [(0, 200), (2, 409)])
def test_aud010_needs_fix_para_approved_e_um_comando_real(
    auth_api_client: TestClient,
    task_id: str,
    session_factory: sessionmaker[Session],
    attempts: int,
    esperado: int,
) -> None:
    """A aresta "nova tentativa (`attempts < max_attempts`)" de [02] §4.

    O auditor montou `needs_fix` no banco (a E6 ainda não produz execução) e enviou o
    fingerprint válido: nas **duas** variantes a resposta foi `409 concurrent_task_update`,
    afirmando concorrência inexistente. Com `attempts = 0` isso bloqueava uma aresta
    legítima; com `attempts = 2` dava o motivo errado e não aplicava a guarda da aresta.
    """
    planejada = _plan(auth_api_client, task_id)

    with session_scope(session_factory) as active:
        active.execute(
            text("UPDATE workspace_task SET status = 'needs_fix', attempts = :n WHERE id = :id"),
            {"id": task_id, "n": attempts},
        )

    resposta = _approve(auth_api_client, task_id, planejada["execution_fingerprint"])
    assert resposta.status_code == esperado, resposta.text

    if esperado == 200:
        assert resposta.json()["status"] == "approved"
        assert resposta.json()["approved_at"] is not None
    else:
        corpo = resposta.json()
        assert corpo["code"] == "transition_guard_failed"
        assert corpo["guard"] == "attempts_below_max"
        assert ("retry_limit", "orchestrator.approval_guard.attempts_below_max") in _events(
            session_factory, task_id
        )


def test_aud010_needs_fix_exige_contexto_reverificado(
    auth_api_client: TestClient,
    workspace_id: str,
    task_id: str,
    repo: Path,
    session_factory: sessionmaker[Session],
) -> None:
    """A outra metade da guarda: "e contexto reverificado" ([02] §4).

    Em `awaiting_approval` a guarda lê os estados **congelados no manifest** — a seleção
    acabou de acontecer. Em `needs_fix` houve uma execução inteira desde o congelamento, e
    "reverificado" só pode significar o estado de agora.
    """
    assert (
        auth_api_client.post(
            f"/api/workspaces/{workspace_id}/context",
            json={
                "domain": "objective",
                "title": "goal",
                "body": "um objetivo valido",
                "source_refs": ["src/app.py"],
            },
        ).status_code
        == 201
    )

    planejada = _plan(auth_api_client, task_id, (".env",))
    assert planejada["risk"] == "high"

    # A fonte muda **depois** do plano: o manifest continua dizendo `fresh`.
    context_helpers.write(repo, "src/app.py", "alterada durante a execução\n")

    with session_scope(session_factory) as active:
        active.execute(
            text("UPDATE workspace_task SET status = 'needs_fix', attempts = 1 WHERE id = :id"),
            {"id": task_id},
        )

    recusa = _approve(auth_api_client, task_id, planejada["execution_fingerprint"])
    assert recusa.status_code == 409, recusa.text
    assert recusa.json()["guard"] == "no_stale_entry_when_high_risk"


# ================================================================== E6-AUD-011


def test_aud011_paths_do_enriquecimento_passam_pelas_hard_rules(
    auth_api_client: TestClient, task_id: str
) -> None:
    """A união dos candidatos acontecia **depois** da decisão de risco.

    O auditor injetou uma porta que devolvia `risk=low` e `candidate_paths=(".env",)`: o
    resultado final tinha `.env` entre os candidatos e `risk=low`. As hard rules só tinham
    rodado sobre os candidatos anteriores à porta.
    """
    del auth_api_client

    class AcrescentaSegredo:
        def enrich(self, objective: str, candidate_paths: list[str]) -> AnalyzerEnrichment:
            del objective, candidate_paths
            return AnalyzerEnrichment(
                risk=RiskLevel.LOW, candidate_paths=(".github/workflows/deploy.yml",)
            )

    analise = analyze("mudar algo generico", [], enrichment_port=AcrescentaSegredo())

    assert ".github/workflows/deploy.yml" in analise.candidate_paths
    assert analise.risk is RiskLevel.HIGH
    # Elevou por **hard rule**, não por opinião de modelo.
    assert analise.risk_source.value == "hard_rule"
    assert "path:ci_cd_deploy" in analise.hard_rule.matched


def test_aud011_a_segunda_passada_nao_muda_nada_quando_nao_ha_path_novo(
    auth_api_client: TestClient,
) -> None:
    """Contrafactual: sem candidato novo, a segunda passada é o mesmo objeto.

    Sem isto, a correção poderia estar reclassificando por um caminho diferente e
    produzindo `matched` diferente do da primeira passada — o que mudaria o `plan_hash` de
    planos idênticos.
    """
    del auth_api_client

    class NaoAcrescentaNada:
        def enrich(self, objective: str, candidate_paths: list[str]) -> AnalyzerEnrichment:
            del objective, candidate_paths
            return AnalyzerEnrichment(risk=RiskLevel.LOW)

    com_porta = analyze("mudar algo generico", ["src/app.py"], enrichment_port=NaoAcrescentaNada())
    sem_porta = analyze("mudar algo generico", ["src/app.py"])

    assert com_porta.hard_rule == sem_porta.hard_rule
