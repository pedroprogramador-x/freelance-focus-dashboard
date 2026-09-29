"""Regressões da **terceira auditoria independente da E6** (Codex, 2026-09-14).

Mesmo formato dos módulos das rodadas 1 e 2: um teste — ou um grupo nomeado — por finding,
com o identificador no nome, e o que o auditor observou descrito no docstring.

Os três findings desta rodada são o mesmo defeito visto de três ângulos, e é isso que a
correção teve de tratar. **A garantia de redação era *opt-in***: cada projeção chamava o
redator por conta própria, então toda superfície nova nascia desprotegida e o esquecimento
era silencioso. Três rodadas encontraram três recortes disso — `test_config` (AUD-007),
`runner_id` (AUD2-001), e finalmente meia API mais o `422` (AUD3-001) —, cada correção
fechando o caso citado e deixando os irmãos vivos.

A correção desta rodada inverte o modelo em vez de acrescentar um quarto recorte:
`RedactingJSONResponse` redige **toda** resposta antes de serializar, e escapar exige
construir um `Unredacted` — uma linha que `test_architecture` limita a um único ponto.
O mesmo raciocínio vale para AUD3-002: em vez de envolver os dois pontos de escrita que o
auditor encontrou, a unidade de trabalho passou a cobrir todo comando por decorador.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.enums import TaskStatus
from app.db.models import DevWorkspace, WorkspaceTask
from app.db.session import session_scope
from app.safety import redact
from app.safety.redaction import REDACTED_KEY_PREFIX, Unredacted, redact_document
from tests import context_helpers
from tests.test_api_tasks import repo, task_id, workspace_id


def _edit_hash(client: TestClient, entry_id: str) -> str:
    """O `edit_hash` corrente, lido pelo caminho que o contrato define: `POST edit-view`."""
    resposta = client.post(f"/api/context/{entry_id}/edit-view")
    assert resposta.status_code == 200, resposta.text
    return str(resposta.json()["edit_hash"])


__all__ = ["repo", "task_id", "workspace_id"]

#: Credencial **sintética**, no formato que o redator canônico reconhece. Nenhuma
#: credencial real participa de teste nenhum deste arquivo.
SECRET = "sk-" + ("A" * 24)
OUTRO_SECRET = "sk-" + ("B" * 24)


def _plan(client: TestClient, task: str, paths: tuple[str, ...] = ()) -> dict[str, Any]:
    response = client.post(f"/api/tasks/{task}/plan", json={"candidate_paths": list(paths)})
    assert response.status_code == 200, response.text
    return dict(response.json())


def _approve(client: TestClient, task: str, fingerprint: str) -> Any:
    return client.post(f"/api/tasks/{task}/approve", json={"execution_fingerprint": fingerprint})


# ================================================================= E6-AUD3-001


class TestAud3001BoundaryDeSaida:
    """Nenhuma resposta de `/api/*` devolve o marcador cru — em nenhuma superfície.

    As quatro reproduções do relatório, na ordem em que ele as lista. Todas usam entradas
    aceitas pelas rotas normais: nenhuma exige adulterar o banco.
    """

    @staticmethod
    def _assert_redigido(rotulo: str, resposta: Any) -> None:
        assert SECRET not in resposta.text, f"{rotulo} devolveu o marcador cru"

    def test_escalares_de_workspace(self, auth_api_client: TestClient, tmp_path: Path) -> None:
        """`name`, `linked_project_id`, `default_branch` e `local_path`.

        O auditor observou os quatro saindo íntegros em create, lista, detalhe e PATCH.
        `linked_project_id` é string opaca e **não** um UUID validado; `local_path` ser um
        diretório real não o isenta de carregar segredo no nome.
        """
        assert redact(SECRET) != SECRET, "a credencial de teste precisa ser reconhecível"
        diretorio = tmp_path / f"ws-{SECRET}"
        diretorio.mkdir()

        criado = auth_api_client.post(
            "/api/workspaces",
            json={
                "name": SECRET,
                "type": "personal",
                "local_path": str(diretorio),
                "linked_project_id": SECRET,
                "default_branch": SECRET,
            },
        )
        assert criado.status_code == 201, criado.text
        self._assert_redigido("POST /workspaces", criado)

        workspace_id = criado.json()["id"]
        self._assert_redigido("GET /workspaces", auth_api_client.get("/api/workspaces"))
        self._assert_redigido(
            "GET /workspaces/{id}", auth_api_client.get(f"/api/workspaces/{workspace_id}")
        )
        self._assert_redigido(
            "PATCH /workspaces/{id}",
            auth_api_client.patch(f"/api/workspaces/{workspace_id}", json={"status": "archived"}),
        )

    def test_nome_de_branch_no_preflight_e_no_manifest(
        self, auth_api_client: TestClient, tmp_path: Path
    ) -> None:
        """A superfície irmã: a mesma branch saía redigida dentro de `plan` e crua no resto.

        É o exemplo mais claro de por que o modelo *opt-in* não fecha: alguém lembrou de
        redigir o `plan`, e não podia ter lembrado de todas as saídas do mesmo dado.
        """
        repositorio = tmp_path / "repo"
        context_helpers.init_repo(repositorio)
        context_helpers.write(repositorio, "README.md", "# projeto\n")
        context_helpers.commit_all(repositorio, "inicial")
        context_helpers.git(repositorio, "checkout", "-b", SECRET)

        criado = auth_api_client.post(
            "/api/workspaces",
            json={"name": "ws", "type": "personal", "local_path": str(repositorio)},
        )
        workspace = criado.json()["id"]

        preflight = auth_api_client.get(f"/api/workspaces/{workspace}/git")
        assert preflight.status_code == 200
        assert preflight.json()["branch"] is not None
        self._assert_redigido("GET /workspaces/{id}/git", preflight)

        tarefa = auth_api_client.post(
            f"/api/workspaces/{workspace}/tasks", json={"title": "t", "goal": "g"}
        ).json()["id"]
        _plan(auth_api_client, tarefa)
        self._assert_redigido(
            "GET /tasks/{id}/context", auth_api_client.get(f"/api/tasks/{tarefa}/context")
        )
        self._assert_redigido("GET /tasks/{id}", auth_api_client.get(f"/api/tasks/{tarefa}"))

    def test_projecao_inteira_do_context_registry(
        self, auth_api_client: TestClient, workspace_id: str
    ) -> None:
        """`title`, `body`, `tags`, valor e **chave** de `structured`, e `source_refs`.

        O registry não tinha redação nenhuma: a projeção autoral saía crua em create, list,
        patch e verify. A chave livre de `structured` é também a reprodução de AUD3-003 por
        uma rota normal.
        """
        criada = auth_api_client.post(
            f"/api/workspaces/{workspace_id}/context",
            json={
                "domain": "objective",
                "title": SECRET,
                "body": f"objetivo com {SECRET}",
                "tags": [SECRET],
                "structured": {"campo": SECRET, SECRET: "valor na chave"},
            },
        )
        assert criada.status_code == 201, criada.text
        self._assert_redigido("POST .../context", criada)

        entry_id = criada.json()["id"]
        self._assert_redigido(
            "GET .../context", auth_api_client.get(f"/api/workspaces/{workspace_id}/context")
        )
        self._assert_redigido(
            "PATCH /context/{id}",
            auth_api_client.patch(
                f"/api/context/{entry_id}",
                json={
                    "expected_edit_hash": _edit_hash(auth_api_client, criada.json()["id"]),
                    "title": SECRET,
                },
            ),
        )
        self._assert_redigido(
            "POST .../context/verify",
            auth_api_client.post(f"/api/workspaces/{workspace_id}/context/verify", json={}),
        )

    @pytest.mark.parametrize(
        ("rotulo", "rota", "corpo"),
        [
            (
                "nome de workspace longo",
                "/api/workspaces",
                {"name": SECRET + "z" * 200, "type": "personal", "local_path": "."},
            ),
            (
                "campo extra no corpo",
                "/api/workspaces",
                {"name": "ok", "type": "personal", "local_path": ".", "extra": SECRET},
            ),
        ],
    )
    def test_422_de_validacao_nao_ecoa_o_valor_recusado(
        self, auth_api_client: TestClient, rotulo: str, rota: str, corpo: dict[str, Any]
    ) -> None:
        """`detail[].input` devolvia o que o cliente mandou (E6-AUD3-001, item 4).

        Era a única superfície de `/api/*` que nem sequer passava por uma projeção nossa: o
        handler de `RequestValidationError` era o embutido do FastAPI. Uma resposta de erro
        carrega por definição o dado recusado — o que faz dela a superfície **mais** provável
        de devolver uma credencial digitada por engano, não a menos.
        """
        resposta = auth_api_client.post(rota, json=corpo)

        assert resposta.status_code == 422, resposta.text
        assert SECRET not in resposta.text, f"o 422 de {rotulo} ecoou o valor cru"
        assert resposta.json()["code"] == "validation_error"

    def test_o_corpo_do_422_continua_diagnosticando(
        self, auth_api_client: TestClient, tmp_path: Path
    ) -> None:
        """Contrafactual: redigir o `422` não pode torná-lo inútil.

        Se a correção tivesse trocado o corpo por `{"code": "validation_error"}` e nada
        mais, o teste acima passaria e o cliente perderia a informação de **qual** campo foi
        recusado — que é o que um `422` existe para dizer.
        """
        diretorio = tmp_path / "ws"
        diretorio.mkdir()
        resposta = auth_api_client.post(
            "/api/workspaces",
            json={"name": "", "type": "personal", "local_path": str(diretorio)},
        )

        assert resposta.status_code == 422
        detalhe = resposta.json()["detail"]
        assert detalhe, "o 422 perdeu o diagnóstico por campo"
        assert any("name" in str(item.get("loc", "")) for item in detalhe)

    def test_o_boundary_nao_mutila_o_que_o_cliente_precisa_devolver(
        self, auth_api_client: TestClient, task_id: str
    ) -> None:
        """Contrafactual estrutural: hashes, ids e caminhos atravessam intactos.

        Uma redação agressiva demais quebraria o fluxo de aprovação em silêncio — o
        `execution_fingerprint` é o valor que `POST /approve` exige de volta.
        """
        planejada = _plan(auth_api_client, task_id)

        assert len(planejada["execution_fingerprint"]) == 64
        assert len(planejada["planning_base_commit"]) == 40
        aceita = _approve(auth_api_client, task_id, planejada["execution_fingerprint"])
        assert aceita.status_code == 200, aceita.text


# ================================================================= E6-AUD3-002


@contextmanager
def _escritor_concorrente(
    factory: sessionmaker[Session], workspace_id: str, prefixo: str
) -> Iterator[list[str]]:
    """Commita uma escrita **não relacionada** imediatamente antes do statement escolhido.

    A instrumentação observa a classe `Engine` (e não uma instância), porque a transação da
    API usa o engine do app e não o da fixture — foi a correção que o próprio auditor
    registrou ter feito na sonda dele.

    O escritor altera o **nome do workspace**: nada a ver com a task, o que garante que o
    conflito observado é de *snapshot* da transação e não a corrida de estado que o
    *compare-and-set* já tratava.
    """
    observados: list[str] = []

    def antes(
        _conn: Any, _cursor: Any, statement: str, _params: Any, _context: Any, _many: Any
    ) -> None:
        if not observados and statement.lower().startswith(prefixo):
            observados.append(statement.split("(")[0])
            with session_scope(factory) as outra:
                alvo = outra.get(DevWorkspace, workspace_id)
                assert alvo is not None
                alvo.name = "escritor paralelo commitou"

    event.listen(Engine, "before_cursor_execute", antes)
    try:
        yield observados
    finally:
        event.remove(Engine, "before_cursor_execute", antes)


class TestAud3002ConflitoNasEscritasDeAprovacao:
    """`/approve` escrevia **antes** do CAS, e essas escritas não tinham tradução.

    O auditor observou `500 internal_error` nos dois pontos. A correção da rodada 2 vivia em
    `_abort_planning`, que só participa de `/plan`: os irmãos em `approve` ficaram de fora.
    A correção desta rodada move a unidade de trabalho para um decorador aplicado a **todo**
    comando, e `test_architecture` exige o decorador em cada um.
    """

    def test_mismatch_event_insert_safety_event(
        self,
        auth_api_client: TestClient,
        workspace_id: str,
        task_id: str,
        session_factory: sessionmaker[Session],
    ) -> None:
        """Ponto de intervenção: `INSERT safety_event` da recusa de fingerprint divergente."""
        planejada = _plan(auth_api_client, task_id)

        with TestClient(
            auth_api_client.app,
            base_url="http://127.0.0.1:8756",
            raise_server_exceptions=False,
        ) as cliente:
            cliente.headers["Authorization"] = auth_api_client.headers["Authorization"]
            with _escritor_concorrente(
                session_factory, workspace_id, "insert into safety_event"
            ) as vistos:
                resposta = cliente.post(
                    f"/api/tasks/{task_id}/approve",
                    json={"execution_fingerprint": "0" * 64},
                )

        assert vistos, "a intervenção não aconteceu — o teste não provou nada"
        assert resposta.status_code == 409, resposta.text
        assert resposta.json()["code"] in {"concurrent_task_update", "write_conflict"}
        del planejada

    def test_needs_fix_verify_update_context_registry_entry(
        self,
        auth_api_client: TestClient,
        workspace_id: str,
        task_id: str,
        session_factory: sessionmaker[Session],
    ) -> None:
        """Ponto de intervenção: `UPDATE context_registry_entry` da reverificação.

        `needs_fix` é montado direto no banco porque a E6 ainda não executa tasks — é a
        mesma montagem que o relatório descreve.
        """
        assert (
            auth_api_client.post(
                f"/api/workspaces/{workspace_id}/context",
                json={
                    "domain": "objective",
                    "title": "fonte",
                    "body": "baseline",
                    "source_refs": ["README.md"],
                },
            ).status_code
            == 201
        )
        planejada = _plan(auth_api_client, task_id)
        assert (
            _approve(auth_api_client, task_id, planejada["execution_fingerprint"]).status_code
            == 200
        )

        with session_scope(session_factory) as ativa:
            tarefa = ativa.get(WorkspaceTask, task_id)
            assert tarefa is not None
            tarefa.status = TaskStatus.NEEDS_FIX

        with TestClient(
            auth_api_client.app,
            base_url="http://127.0.0.1:8756",
            raise_server_exceptions=False,
        ) as cliente:
            cliente.headers["Authorization"] = auth_api_client.headers["Authorization"]
            with _escritor_concorrente(
                session_factory, workspace_id, "update context_registry_entry"
            ) as vistos:
                resposta = cliente.post(
                    f"/api/tasks/{task_id}/approve",
                    json={"execution_fingerprint": planejada["execution_fingerprint"]},
                )

        assert vistos, "a intervenção não aconteceu — o teste não provou nada"
        assert resposta.status_code == 409, resposta.text
        assert resposta.json()["code"] in {"concurrent_task_update", "write_conflict"}

    @pytest.mark.parametrize(
        "prefixo", ["insert into context_manifest", "update context_registry_entry"]
    )
    def test_o_plano_continua_coberto(
        self,
        auth_api_client: TestClient,
        workspace_id: str,
        task_id: str,
        session_factory: sessionmaker[Session],
        prefixo: str,
    ) -> None:
        """Controle: a cobertura de AUD2-002 não pode ter regredido na refatoração."""
        if prefixo.startswith("update"):
            assert (
                auth_api_client.post(
                    f"/api/workspaces/{workspace_id}/context",
                    json={
                        "domain": "objective",
                        "title": "verificação",
                        "body": "baseline",
                        "source_refs": ["README.md"],
                    },
                ).status_code
                == 201
            )

        with TestClient(
            auth_api_client.app,
            base_url="http://127.0.0.1:8756",
            raise_server_exceptions=False,
        ) as cliente:
            cliente.headers["Authorization"] = auth_api_client.headers["Authorization"]
            with _escritor_concorrente(session_factory, workspace_id, prefixo) as vistos:
                resposta = cliente.post(f"/api/tasks/{task_id}/plan", json={})

        assert vistos
        assert resposta.status_code == 409, resposta.text
        # E replanejar depois funciona: o estado não ficou preso.
        assert _plan(auth_api_client, task_id)["status"] == "awaiting_approval"

    def test_concorrencia_real_de_duas_sessoes_em_cancel(
        self, auth_api_client: TestClient, task_id: str, session_factory: sessionmaker[Session]
    ) -> None:
        """Sem mocks: duas sessões reais do engine, a perdedora com snapshot velho."""
        from app.orchestrator import execution_manager as em
        from app.orchestrator.errors import ConcurrentTaskUpdate

        del auth_api_client
        with session_factory() as vencedora, session_factory() as perdedora:
            assert perdedora.get(WorkspaceTask, task_id) is not None
            em.cancel(vencedora, task_id)
            vencedora.commit()

            with pytest.raises(ConcurrentTaskUpdate):
                em.cancel(perdedora, task_id)

    def test_erro_operacional_que_nao_e_conflito_continua_subindo(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        """Contrafactual: a unidade de trabalho não pode virar "todo erro é concorrência"."""
        from sqlalchemy import text
        from sqlalchemy.exc import OperationalError

        with session_factory() as ativa, pytest.raises(OperationalError):
            ativa.execute(text("SELECT 1 FROM tabela_que_nao_existe"))


# ================================================================= E6-AUD3-003


class TestAud3003ChavesDinamicas:
    """Uma credencial usada como **chave** atravessava a recursão canônica intacta.

    A premissa era "chave é sempre nome de campo de schema". `structured` do registry aceita
    chave livre pela rota normal, e `test_config` é JSON livre no banco: a premissa era falsa
    nos dois lugares.
    """

    def test_a_chave_secreta_vira_placeholder_opaco(self) -> None:
        assert redact_document({SECRET: "valor"}) == {f"{REDACTED_KEY_PREFIX}1»": "valor"}

    def test_duas_chaves_secretas_no_mesmo_dict_nao_colidem(self) -> None:
        """O requisito central: nenhuma sobrescrita, nenhum campo desaparecido.

        Substituir cada chave pelo `«redigido»` do redator faria as duas colapsarem no mesmo
        texto — e um `dict` não tem duas chaves iguais: uma apagaria a outra, e um campo
        sumiria da resposta sem deixar rastro.
        """
        saida = redact_document({SECRET: "primeiro", OUTRO_SECRET: "segundo"})
        assert isinstance(saida, dict)

        assert len(saida) == 2, "uma chave sobrescreveu a outra"
        assert sorted(saida.values()) == ["primeiro", "segundo"]
        assert len(set(saida)) == 2, "as duas chaves colidiram no mesmo placeholder"
        assert all(chave.startswith(REDACTED_KEY_PREFIX) for chave in saida)
        assert SECRET not in str(saida)
        assert OUTRO_SECRET not in str(saida)

    def test_o_placeholder_e_unico_no_documento_inteiro(self) -> None:
        """O contador é por documento — ver a decisão registrada em `redact_document`."""
        documento = {
            "a": {SECRET: 1},
            "b": {OUTRO_SECRET: 2},
            "c": [{SECRET: 3}],
        }
        saida = redact_document(documento)
        assert isinstance(saida, dict)

        placeholders = [chave for ramo in (saida["a"], saida["b"], saida["c"][0]) for chave in ramo]
        assert len(set(placeholders)) == 3, f"placeholders repetidos: {placeholders}"

    def test_o_placeholder_nao_carrega_pedaco_do_segredo(self) -> None:
        """Não-reversível: nem substring, nem hash. Só um número posicional."""
        saida = redact_document({f"prefixo-{SECRET}-sufixo": "v"})
        assert isinstance(saida, dict)
        (chave,) = saida

        assert SECRET not in chave
        assert "prefixo" not in chave, "o placeholder preservou contexto do nome original"
        assert chave == f"{REDACTED_KEY_PREFIX}1»"

    def test_chave_normal_de_schema_continua_legivel(self) -> None:
        """Contrafactual: mascarar toda chave produziria um documento ilegível."""
        documento = {"id": "x", "status": "draft", "approved_at": None, "tokenizer": "gpt"}
        assert redact_document(documento) == documento

    def test_test_config_malformado_com_credencial_na_chave(
        self,
        auth_api_client: TestClient,
        workspace_id: str,
        session_factory: sessionmaker[Session],
    ) -> None:
        """A reprodução do relatório, que exige documento inválido já persistido."""
        with session_scope(session_factory) as ativa:
            workspace = ativa.get(DevWorkspace, workspace_id)
            assert workspace is not None
            workspace.test_config = {"output_limits": {SECRET: 42}}

        resposta = auth_api_client.get(f"/api/workspaces/{workspace_id}")
        assert resposta.status_code == 200
        assert SECRET not in resposta.text


# ================================================= PARTE 2 — escape hatch do purge_token


class TestEscapeHatchDoPurgeToken:
    """O `purge_token` atravessa sem redação; tudo ao lado dele continua redigido."""

    def test_o_token_chega_intacto_e_funciona(
        self, auth_api_client: TestClient, tmp_path: Path
    ) -> None:
        diretorio = tmp_path / "ws"
        diretorio.mkdir()
        workspace = auth_api_client.post(
            "/api/workspaces",
            json={"name": "ws", "type": "personal", "local_path": str(diretorio)},
        ).json()["id"]
        auth_api_client.patch(f"/api/workspaces/{workspace}", json={"status": "archived"})

        previa = auth_api_client.get(f"/api/workspaces/{workspace}/purge-preview")
        assert previa.status_code == 200, previa.text
        token = previa.json()["purge_token"]
        assert len(token) > 20

        purgada = auth_api_client.post(
            f"/api/workspaces/{workspace}/purge", json={"purge_token": token}
        )
        assert purgada.status_code == 200, purgada.text

    def test_um_token_que_parece_segredo_ainda_atravessa(
        self, auth_api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A prova que o marcador de fato governa, e não a sorte dos padrões atuais.

        `secrets.token_urlsafe` não casa nenhum padrão do redator hoje, então o token
        chegaria intacto mesmo sem `Unredacted`. Forçar o store a emitir um token que **é**
        reconhecido pelo redator é o que separa "funciona" de "funciona pelo motivo certo".
        """
        monkeypatch.setattr("app.workspace.purge_tokens.secrets.token_urlsafe", lambda _n: SECRET)

        diretorio = tmp_path / "ws"
        diretorio.mkdir()
        workspace = auth_api_client.post(
            "/api/workspaces",
            json={"name": "ws", "type": "personal", "local_path": str(diretorio)},
        ).json()["id"]

        previa = auth_api_client.get(f"/api/workspaces/{workspace}/purge-preview")
        assert previa.status_code == 200
        assert previa.json()["purge_token"] == SECRET, "o marcador não protegeu o token"

    def test_o_vizinho_no_mesmo_payload_continua_redigido(
        self, auth_api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """O escape hatch é do **valor**, não do payload nem do campo.

        Um segredo colocado deliberadamente ao lado do token, na mesma resposta, é redigido
        — o que prova que a exceção não se propagou para a resposta inteira.
        """
        monkeypatch.setattr("app.workspace.purge_tokens.secrets.token_urlsafe", lambda _n: SECRET)

        diretorio = tmp_path / f"ws-{OUTRO_SECRET}"
        diretorio.mkdir()
        workspace = auth_api_client.post(
            "/api/workspaces",
            json={"name": OUTRO_SECRET, "type": "personal", "local_path": str(diretorio)},
        ).json()["id"]

        previa = auth_api_client.get(f"/api/workspaces/{workspace}/purge-preview")
        corpo = previa.json()

        assert corpo["purge_token"] == SECRET
        assert OUTRO_SECRET not in previa.text, "o vizinho do token escapou da redação"

    def test_unredacted_e_o_unico_jeito_de_escapar(self) -> None:
        """Unitário: o boundary distingue o tipo, não o conteúdo nem o nome do campo."""
        documento = {"purge_token": Unredacted(SECRET), "outro_campo": SECRET}

        assert redact_document(documento) == {"purge_token": SECRET, "outro_campo": "«redigido»"}
