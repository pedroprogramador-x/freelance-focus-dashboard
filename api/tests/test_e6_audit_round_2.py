"""Regressões da **segunda auditoria independente da E6** (Codex, 2026-09-13).

Um teste — ou um grupo nomeado — por finding, com o identificador no nome, no mesmo formato
de `test_e6_audit_round_1.py`. Cada um reproduz o cenário exato do relatório e falhava antes
da correção desta rodada.

Os cinco findings desta rodada têm um parentesco que vale registrar: **quatro deles são a
mesma correção da primeira rodada aplicada a uma superfície que ficou de fora.** AUD2-001 é
a redação de AUD-007 numa segunda saída; AUD2-002 é o tratamento de conflito de AUD-004
numa segunda fronteira de escrita; AUD2-003 é a delegação de AUD-009 aplicada aos
candidatos e não só ao objetivo; AUD2-005 é o fallback da própria redação de AUD-007. É o
padrão que a auditoria nomeia sem nomear: corrigir *um ponto* de um problema que tem *uma
classe* deixa os irmãos vivos.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.db.enums import TaskStatus
from app.db.models import DevWorkspace, WorkspaceTask
from app.db.session import session_scope
from app.orchestrator import execution_manager as em
from app.orchestrator.analyzer import analyze, evaluate_hard_rules
from app.orchestrator.errors import ConcurrentTaskUpdate, TransitionGuardFailed
from app.safety import redact
from app.safety.secrets import SecretPolicy, classify_path_secrecy
from tests import context_helpers
from tests.test_api_tasks import VALID_TEST_CONFIG, repo, task_id, workspace_id

__all__ = ["repo", "task_id", "workspace_id"]

#: Credencial **sintética**, no formato que o redator canônico reconhece. Nenhuma
#: credencial real participa de teste nenhum deste arquivo.
SECRET = "sk-" + ("A" * 24)


def _plan(client: TestClient, task: str, paths: tuple[str, ...] = ()) -> dict[str, Any]:
    response = client.post(f"/api/tasks/{task}/plan", json={"candidate_paths": list(paths)})
    assert response.status_code == 200, response.text
    return dict(response.json())


def _approve(client: TestClient, task: str, fingerprint: str) -> Any:
    return client.post(f"/api/tasks/{task}/approve", json={"execution_fingerprint": fingerprint})


def _reload(factory: sessionmaker[Session], task: str) -> WorkspaceTask:
    with factory() as fresh:
        loaded = fresh.get(WorkspaceTask, task)
        assert loaded is not None
        fresh.expunge(loaded)
        return loaded


# ================================================================= E6-AUD2-001


def test_aud2_001_runner_id_secreto_nao_sai_em_nenhuma_resposta_de_task(
    auth_api_client: TestClient, workspace_id: str, task_id: str
) -> None:
    """`execution_fingerprint_parts.test_binding.runner` é texto livre, e saía cru.

    O que o auditor observou: a resposta de **workspace** mascarava o `runner_id`
    corretamente (a correção de AUD-007), e `POST /plan`, `GET /tasks/{id}` antes de
    aprovar e `GET /tasks/{id}` depois de aprovar devolviam a credencial integral. A UI
    renderiza esse mesmo campo como nome do runner.

    O schema aceita `runner_id` livre de propósito — é um identificador declarado ([04] §6,
    "declarado, nunca descoberto"). Aceitar texto livre e tratá-lo como público são duas
    coisas diferentes.
    """
    config = deepcopy(VALID_TEST_CONFIG)
    config["runner_id"] = SECRET
    assert redact(SECRET) != SECRET, "a credencial de teste precisa ser reconhecível"

    patched = auth_api_client.patch(f"/api/workspaces/{workspace_id}", json={"test_config": config})
    assert patched.status_code == 200, patched.text
    assert SECRET not in patched.text

    planejada = _plan(auth_api_client, task_id)
    assert SECRET not in str(planejada), "POST /plan devolveu o runner_id cru"
    assert planejada["execution_fingerprint_parts"]["test_binding"]["runner"] != SECRET

    antes = auth_api_client.get(f"/api/tasks/{task_id}")
    assert SECRET not in antes.text, "GET /tasks/{id} devolveu o runner_id cru"

    aprovada = _approve(auth_api_client, task_id, planejada["execution_fingerprint"])
    assert aprovada.status_code == 200, aprovada.text
    assert SECRET not in aprovada.text, "POST /approve devolveu o runner_id cru"

    depois = auth_api_client.get(f"/api/tasks/{task_id}")
    assert SECRET not in depois.text, "GET /tasks/{id} aprovado devolveu o runner_id cru"

    listagem = auth_api_client.get(f"/api/workspaces/{workspace_id}/tasks")
    assert SECRET not in listagem.text


def test_aud2_001_os_hashes_atravessam_o_redator_intactos(
    auth_api_client: TestClient, task_id: str
) -> None:
    """Redigir a projeção não pode mutilar o que o cliente precisa devolver.

    O `execution_fingerprint` é o valor que `POST /approve` exige de volta, e as partes são
    o que a UI compara para dizer qual campo divergiu. Se o redator tocasse hexadecimal, a
    correção de AUD2-001 quebraria o fluxo inteiro de aprovação em silêncio.
    """
    planejada = _plan(auth_api_client, task_id)
    partes = planejada["execution_fingerprint_parts"]

    for campo in ("plan_hash", "manifest_hash", "rendered_context_hash", "tool_profile_hash"):
        assert len(partes[campo]) == 64, f"{campo} foi mutilado pela redação"
    assert partes["base_commit"] == planejada["planning_base_commit"]

    aceita = _approve(auth_api_client, task_id, planejada["execution_fingerprint"])
    assert aceita.status_code == 200, aceita.text


def test_aud2_001_o_fingerprint_e_calculado_sobre_o_valor_cru(
    auth_api_client: TestClient, workspace_id: str, task_id: str
) -> None:
    """A redação é da projeção. Hashear o valor mascarado seria encobrir, não proteger.

    Dois `runner_id` distintos que caem na mesma máscara têm de produzir fingerprints
    **diferentes** — senão trocar o Test Runner deixaria de invalidar a aprovação, que é
    precisamente o que [04] §7 exige que ele faça.
    """
    url = f"/api/workspaces/{workspace_id}"

    auth_api_client.patch(
        url, json={"test_config": {**VALID_TEST_CONFIG, "runner_id": "sk-" + ("A" * 24)}}
    )
    um = _plan(auth_api_client, task_id)["execution_fingerprint"]

    assert auth_api_client.post(f"/api/tasks/{task_id}/reject", json={}).status_code == 200
    auth_api_client.patch(
        url, json={"test_config": {**VALID_TEST_CONFIG, "runner_id": "sk-" + ("B" * 24)}}
    )
    outro = _plan(auth_api_client, task_id)["execution_fingerprint"]

    assert um != outro


def test_aud2_001_existe_uma_unica_caminhada_recursiva_de_redacao() -> None:
    """Análise estática: `redact_document` é a única recursão de redação do backend.

    O defeito não foi "esqueceram de redigir uma projeção" — foi **três cópias** da mesma
    caminhada em três módulos (`api.tasks`, `api.main`, `safety.test_policy`), o que fez de
    "aplicar a redação aqui" uma decisão a lembrar em cada ponto de saída. Com uma função
    só, o ponto de saída novo que esquecer dela é visível; com três, ele é indistinguível
    de mais uma cópia.
    """
    import ast

    raiz = Path(__file__).resolve().parents[1] / "app"

    def e_caminhada_de_redacao(no: ast.FunctionDef) -> bool:
        """Chama `redact` **e** a si mesma: é uma recursão que redige um documento."""
        chamadas = {
            ast.unparse(filho.func) for filho in ast.walk(no) if isinstance(filho, ast.Call)
        }
        return "redact" in chamadas and no.name in chamadas

    definidores = [
        f"{caminho.relative_to(raiz).as_posix()}::{no.name}"
        for caminho in sorted(raiz.rglob("*.py"))
        for no in ast.walk(ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho)))
        if isinstance(no, ast.FunctionDef) and e_caminhada_de_redacao(no)
    ]

    assert definidores == ["safety/redaction.py::redact_document"], (
        f"esperava exatamente uma caminhada recursiva de redação; achei {definidores}"
    )


# ================================================================= E6-AUD2-002


@pytest.mark.parametrize("via_http", [False, True])
def test_aud2_002_cancel_concorrente_durante_o_plano_vira_conflito_de_dominio(
    auth_api_client: TestClient,
    task_id: str,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    via_http: bool,
) -> None:
    """A escrita que estoura fica **dentro** de `plan_task`, longe do CAS final.

    O que o auditor observou: interceptar a fronteira de `select_context` durante um
    `POST /plan`, cancelar a task numa sessão real e commitar, e então retomar a seleção.
    A escrita seguinte no snapshot antigo levanta `OperationalError` com
    `sqlite_errorname = SQLITE_BUSY_SNAPSHOT`, e a resposta HTTP real era
    **500 `internal_error`** — um conflito previsto por [02] §4 chegando ao cliente como
    defeito do backend.

    O tratamento da primeira rodada envolvia só o `UPDATE` de `_compare_and_set`. Este
    interleaving nunca chega lá.
    """
    from app.context_engine import select_context

    def cancelar_no_meio(*args: Any, **kwargs: Any) -> Any:
        with session_scope(session_factory) as vencedora:
            em.cancel(vencedora, task_id)
        return select_context(*args, **kwargs)

    monkeypatch.setattr("app.orchestrator.planner.select_context", cancelar_no_meio)

    if via_http:
        with TestClient(
            auth_api_client.app,
            base_url="http://127.0.0.1:8756",
            raise_server_exceptions=False,
        ) as cliente:
            cliente.headers["Authorization"] = auth_api_client.headers["Authorization"]
            resposta = cliente.post(f"/api/tasks/{task_id}/plan", json={})
        assert resposta.status_code == 409, resposta.text
        assert resposta.json()["code"] == "concurrent_task_update"
    else:
        artifacts = auth_api_client.app.state.settings.artifacts_dir  # type: ignore[attr-defined]
        with (
            pytest.raises(ConcurrentTaskUpdate),
            session_scope(session_factory) as ativa,
        ):
            em.plan(ativa, task_id, artifacts_dir=artifacts)

    # A vencedora **não** foi sobrescrita: é a metade do finding que já estava correta e
    # que a correção não pode ter quebrado.
    assert _reload(session_factory, task_id).status is TaskStatus.CANCELLED


def test_aud2_002_erro_de_banco_que_nao_e_conflito_nao_vira_concorrencia(
    auth_api_client: TestClient,
    task_id: str,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contrafactual: a tradução é por código de erro, não por "aconteceu durante o plano".

    Um `RuntimeError` no meio do planejamento continua sendo o defeito que é — e leva a
    task a `failed(internal_error)`, não a um `409` que sugeriria "tente de novo".
    """

    def explodir(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("falha independente do context engine")

    monkeypatch.setattr("app.orchestrator.planner.select_context", explodir)

    with pytest.raises(RuntimeError):
        auth_api_client.post(f"/api/tasks/{task_id}/plan", json={})

    depois = _reload(session_factory, task_id)
    assert depois.status is TaskStatus.FAILED
    assert depois.failure_reason is not None
    assert depois.failure_reason.value == "internal_error"


# ================================================================= E6-AUD2-003


@pytest.mark.parametrize("path", ["client.p12", "client.pfx", "id_rsa", "id_ed25519", ".pypirc"])
def test_aud2_003_candidato_secreto_pela_politica_canonica_e_high(
    auth_api_client: TestClient, workspace_id: str, path: str
) -> None:
    """Os cinco caminhos que a tabela local do Analyzer não tinha.

    O que o auditor observou: `classify_path_secrecy` classifica cada um como `secret`, e
    `POST /plan` com esse candidato devolvia **`risk=low`, `complexity=trivial`** nos cinco
    — o atalho trivial ainda se aplicava, porque a tabela paralela do Analyzer não os
    reconhecia.
    """
    assert classify_path_secrecy(path).verdict.value == "secret"

    criada = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/tasks",
        json={"title": "so candidato", "goal": "corrigir typo"},
    )
    assert criada.status_code == 201

    planejada = _plan(auth_api_client, criada.json()["id"], (path,))

    assert (planejada["risk"], planejada["complexity"]) != ("low", "trivial")
    assert planejada["risk"] == "high"
    assert "path:secret_policy" in planejada["plan"]["analysis"]["hard_rule"]["matched"]


def test_aud2_003_o_analyzer_nao_tem_mais_tabela_propria_de_segredo() -> None:
    """Análise estática: a categoria sumiu, e nenhum padrão da denylist sobrou solto.

    Não basta renomear a chave: o teste confere que **nenhum** dos padrões canônicos de
    [04] §5 aparece como literal em `orchestrator/`, e que a delegação de fato acontece.
    """
    import ast

    from app.orchestrator.analyzer import HIGH_RISK_PATH_RULES
    from app.safety.secrets import DEFAULT_SECRET_PATTERNS

    assert "secrets" not in HIGH_RISK_PATH_RULES

    orchestrator = Path(__file__).resolve().parents[1] / "app" / "orchestrator"
    for caminho in orchestrator.rglob("*.py"):
        fonte = caminho.read_text(encoding="utf-8")
        repetidos = [padrao for padrao in DEFAULT_SECRET_PATTERNS if f'"{padrao}"' in fonte]
        assert not repetidos, (
            f"orchestrator/{caminho.name} repete padrão(ões) da denylist de segredo "
            f"{repetidos}: reconhecer segredo por path é `safety.secrets` ([04] §5)"
        )

    # E a delegação existe de fato, pelo nome canônico.
    analyzer = (orchestrator / "analyzer.py").read_text(encoding="utf-8")
    chamadas = {
        ast.unparse(no.func) for no in ast.walk(ast.parse(analyzer)) if isinstance(no, ast.Call)
    }
    assert "is_secret_path" in chamadas


def test_aud2_003_um_override_restritivo_do_workspace_eleva_o_risco() -> None:
    """A delegação carrega a política **efetiva**, não o default global.

    É o que torna a fonte única útil em vez de só menos duplicada: um override que
    acrescenta um padrão à denylist ([04] §5, "só restringe") passa a elevar o risco pelo
    mesmo caminho, sem que o Analyzer saiba que existe override.
    """
    padrao = "infra/*.tfvars"
    assert analyze("mexer no arquivo", ["infra/prod.tfvars"]).risk.value != "high"

    apertada = SecretPolicy(patterns=(*SecretPolicy().patterns, padrao))
    elevada = analyze("mexer no arquivo", ["infra/prod.tfvars"], secret_policy=apertada)

    assert elevada.risk.value == "high"
    assert "path:secret_policy" in elevada.hard_rule.matched


def test_aud2_003_as_categorias_que_nao_sao_segredo_continuam_na_tabela_do_analyzer() -> None:
    """Contrafactual: a delegação não podia levar junto o que é risco, e não segredo.

    `migrations/**` e `.github/workflows/**` não são segredos e a denylist de [04] §5 não
    os conhece — eles são "mexer aqui é caro de errar", que é semântica do Analyzer.
    """
    from app.orchestrator.analyzer import HIGH_RISK_PATH_RULES

    assert set(HIGH_RISK_PATH_RULES) == {
        "migrations_schema",
        "auth_permissions",
        "ci_cd_deploy",
        "dependencies",
    }

    for path, esperado in (
        ("api/migrations/0001.py", "path:migrations_schema"),
        (".github/workflows/deploy.yml", "path:ci_cd_deploy"),
        ("package.json", "path:dependencies"),
    ):
        assert esperado in evaluate_hard_rules("mexer", [path]).matched


# ================================================================= E6-AUD2-004


class TestAud2004RecuperacaoDeHeadInvalido:
    """A orientação tem de apontar para a ação que **existe**.

    O que o auditor observou: depois de a guarda de entrada invalidar a aprovação por `HEAD`
    divergente, a task fica `awaiting_approval` com candidato nulo. A mensagem da guarda e a
    resposta de `GET /tasks/{id}/context` mandavam rodar `POST /plan` — que responde `409`,
    porque `awaiting_approval → planning` não é aresta de [02] §4. E a UI continuava
    mostrando "aguardando aprovação", "Aprovar" desabilitado e nenhum botão Planejar.

    O estado é seguro (nenhuma aprovação indevida, nenhum leitor quebra). O que faltava era
    coerência: o caminho real, comprovado, é `reject → draft → plan → approve`.
    """

    @staticmethod
    def _invalidar(
        client: TestClient, task: str, repo: Path, factory: sessionmaker[Session]
    ) -> TransitionGuardFailed:
        planejada = _plan(client, task, ("README.md",))
        assert _approve(client, task, planejada["execution_fingerprint"]).status_code == 200

        context_helpers.write(repo, "README.md", "# mudou depois do plano\n")
        context_helpers.commit_all(repo, "novo head")

        with (
            pytest.raises(TransitionGuardFailed) as erro,
            session_scope(factory) as ativa,
        ):
            em.start_execution(ativa, task)
        assert erro.value.as_payload()["requires_replan"] is True
        return erro.value

    def test_a_mensagem_da_guarda_nao_manda_fazer_o_que_devolve_409(
        self,
        auth_api_client: TestClient,
        task_id: str,
        repo: Path,
        session_factory: sessionmaker[Session],
    ) -> None:
        erro = self._invalidar(auth_api_client, task_id, repo, session_factory)

        assert "reject" in erro.message.lower(), "a mensagem não nomeia o caminho real"
        # E o que ela nomeia funciona; o que ela deixou de nomear sozinho, não.
        assert auth_api_client.post(f"/api/tasks/{task_id}/plan", json={}).status_code == 409
        assert auth_api_client.post(f"/api/tasks/{task_id}/reject", json={}).status_code == 200
        assert auth_api_client.post(f"/api/tasks/{task_id}/plan", json={}).status_code == 200

    def test_o_detalhe_marca_o_plano_como_historico(
        self,
        auth_api_client: TestClient,
        task_id: str,
        repo: Path,
        session_factory: sessionmaker[Session],
    ) -> None:
        """`approval_state` é a distinção que `status` sozinho não faz."""

        def estado() -> str:
            return str(auth_api_client.get(f"/api/tasks/{task_id}").json()["approval_state"])

        planejada = _plan(auth_api_client, task_id, ("README.md",))
        assert estado() == "pending"

        aprovada = _approve(auth_api_client, task_id, planejada["execution_fingerprint"])
        assert aprovada.status_code == 200
        assert estado() == "approved"

        context_helpers.write(repo, "README.md", "# mudou depois do plano\n")
        context_helpers.commit_all(repo, "novo head")
        with (
            pytest.raises(TransitionGuardFailed),
            session_scope(session_factory) as ativa,
        ):
            em.start_execution(ativa, task_id)

        detalhe = auth_api_client.get(f"/api/tasks/{task_id}").json()
        assert detalhe["status"] == "awaiting_approval"
        assert detalhe["approval_state"] == "requires_replan"
        # O plano continua visível — ele é o registro do que foi planejado —, mas o cliente
        # agora consegue dizer que ele é histórico.
        assert detalhe["plan"] is not None
        assert detalhe["execution_fingerprint"] is None

    def test_o_contexto_404_explica_o_motivo_certo(
        self,
        auth_api_client: TestClient,
        task_id: str,
        repo: Path,
        session_factory: sessionmaker[Session],
    ) -> None:
        """Dois motivos para não haver manifest, e eles pedem ações diferentes.

        Desde E6-CONS4 (adendo a [06] §2) o `404` **descreve** o motivo com um `reason`
        estruturado e não ensina mais comandos: o caminho real — rejeitar agora, planejar
        depois — é `TaskResponse.planning`, e o plano exibido é marcado por
        `plan_standing`. As duas metades desta asserção continuam as mesmas: os motivos são
        distinguíveis, e o cliente sabe que o plano é histórico.
        """
        nunca_planejada = auth_api_client.get(f"/api/tasks/{task_id}/context")
        assert nunca_planejada.status_code == 404
        assert nunca_planejada.json()["reason"] == "not_planned"

        self._invalidar(auth_api_client, task_id, repo, session_factory)

        invalidada = auth_api_client.get(f"/api/tasks/{task_id}/context")
        assert invalidada.status_code == 404
        assert invalidada.json()["reason"] == "approval_invalidated"

        detalhe = auth_api_client.get(f"/api/tasks/{task_id}").json()
        assert detalhe["planning"]["transition"] == "after_reject", "o caminho real é reject"
        assert detalhe["planning"]["checked"] is True
        assert detalhe["plan_standing"] == "historical"

    def test_approval_state_cobre_os_quatro_casos(
        self, auth_api_client: TestClient, task_id: str
    ) -> None:
        def estado() -> str:
            return str(auth_api_client.get(f"/api/tasks/{task_id}").json()["approval_state"])

        assert estado() == "not_planned"
        _plan(auth_api_client, task_id)
        assert estado() == "pending"

        # Encontrado em self-review: `reject` limpa o candidato e **deixa** `plan_hash`, que
        # é a assinatura de `requires_replan`. Uma task rejeitada não é isso — ela está em
        # `draft`, e o caminho é `POST /plan` direto. Dizer "rejeite e replaneje" a quem
        # acabou de rejeitar repetiria na projeção a instrução impossível do finding.
        assert auth_api_client.post(f"/api/tasks/{task_id}/reject", json={}).status_code == 200
        assert estado() == "not_planned"
        assert auth_api_client.post(f"/api/tasks/{task_id}/plan", json={}).status_code == 200

    def test_uma_task_cancelada_nao_fica_pendente_de_aprovacao(
        self, auth_api_client: TestClient, task_id: str
    ) -> None:
        """Contrafactual do mesmo cuidado: terminal não tem decisão pendente.

        Cancelar a partir de `awaiting_approval` preserva o candidato — e uma projeção que
        olhasse só para os campos diria `pending` sobre uma task que ninguém pode aprovar.
        """
        _plan(auth_api_client, task_id)
        assert auth_api_client.post(f"/api/tasks/{task_id}/cancel", json={}).status_code == 200

        detalhe = auth_api_client.get(f"/api/tasks/{task_id}").json()
        assert detalhe["status"] == "cancelled"
        assert detalhe["approval_state"] == "not_planned"


# ================================================================= E6-AUD2-005


@pytest.mark.parametrize("campo", ["network_policy", "cwd_mode", "output_limits"])
def test_aud2_005_documento_malformado_no_banco_nao_vaza_valor_cru(
    auth_api_client: TestClient,
    workspace_id: str,
    session_factory: sessionmaker[Session],
    campo: str,
) -> None:
    """O fallback confiava no **nome** do campo, não no valor.

    O que o auditor observou: persistir a credencial sintética diretamente na coluna JSON,
    em `network_policy`, em `cwd_mode` ou em `output_limits.max_stdout_bytes`, e
    `GET /workspaces/{id}` devolvia 200 com o valor cru nos três casos.

    **Pré-condição do finding, preservada aqui:** o `PATCH` normal recusa esses documentos.
    Esta reprodução exige dado inválido já no banco — por isso a severidade baixa, e por
    isso a correção é de robustez do fallback, não de um buraco alcançável por HTTP.
    """
    documento = deepcopy(VALID_TEST_CONFIG)
    documento[campo] = {"max_stdout_bytes": SECRET} if campo == "output_limits" else SECRET

    with session_scope(session_factory) as ativa:
        workspace = ativa.get(DevWorkspace, workspace_id)
        assert workspace is not None
        workspace.test_config = documento

    resposta = auth_api_client.get(f"/api/workspaces/{workspace_id}")
    assert resposta.status_code == 200
    assert SECRET not in resposta.text, f"{campo} malformado devolveu o valor cru"

    assert SECRET not in auth_api_client.get("/api/workspaces").text


def test_aud2_005_o_valor_esperado_do_enum_continua_publicavel(
    auth_api_client: TestClient, workspace_id: str
) -> None:
    """Contrafactual: a correção não podia mascarar a configuração legítima.

    A regra é *allowlist de valor*, não de campo — o valor único que a V1 aceita continua
    saindo como está, e é o que a UI precisa para exibir a política.
    """
    assert (
        auth_api_client.patch(
            f"/api/workspaces/{workspace_id}", json={"test_config": VALID_TEST_CONFIG}
        ).status_code
        == 200
    )

    config = auth_api_client.get(f"/api/workspaces/{workspace_id}").json()["test_config"]

    assert config["network_policy"] == "unrestricted"
    assert config["cwd_mode"] == "task_worktree"
    assert config["output_limits"] == VALID_TEST_CONFIG["output_limits"]
    assert config["argv"] == VALID_TEST_CONFIG["argv"]
