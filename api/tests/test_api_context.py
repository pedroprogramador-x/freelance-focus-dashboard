"""Gates 6 e 7 da E4 — as quatro rotas do Context Registry, via `TestClient`.

Gate 6: fluxo feliz e de erro de cada rota, **incluindo a herança de autenticação** — que é
verificada usando o mesmo padrão da E3 (o middleware de `app.main`), sem reimplementar
nada. Uma rota nova sob `/api/` nasce protegida; o teste confirma que nasceu.

Gate 7: import do seed de planejamento — contagem e conteúdo de cada entrada criada, e a
prova de que reimportar **cria um segundo conjunto** em vez de sobrescrever o primeiro.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.context_helpers import GIT, commit_all, write

pytestmark = pytest.mark.skipif(GIT is None, reason="git indisponível no PATH")

#: `Origin` que casa com o `Host` servido pelo `TestClient` — a guarda de mesma origem da
#: E3 (`origin_matches_host`) recusa qualquer outro em método que altera estado.
SAME_ORIGIN = {"Origin": "http://127.0.0.1:8756", "Sec-Fetch-Site": "same-origin"}

SEED: dict[str, Any] = {
    "problem": "Clientes perdem prazo por falta de acompanhamento.",
    "objective": "Entregar um painel único de acompanhamento.",
    "functional_requirements": ["Cadastrar cliente", "Listar propostas"],
    "non_functional_requirements": ["Funcionar offline", "Responder em menos de 1s"],
    "stack": ["React", "TypeScript", "FastAPI"],
    "architecture": "SPA no browser, backend local em FastAPI.",
    "technical_decisions": [
        {"title": "Usar SQLite", "decision": "Banco local em arquivo", "reason": "Zero setup"},
        {"title": "Sem embeddings", "decision": "Verificação por hash", "reason": "Determinismo"},
    ],
    "risks": [
        {"description": "OneDrive corrompe o SQLite", "mitigation": "data_dir fora do OneDrive"},
        {"description": "Segredo vazar no contexto", "mitigation": "Denylist por caminho"},
    ],
}


def _workspace(client: TestClient, path: Path, *, name: str = "ws") -> str:
    response = client.post(
        "/api/workspaces",
        json={"name": name, "type": "personal", "local_path": str(path)},
        headers=SAME_ORIGIN,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _create(client: TestClient, workspace_id: str, **overrides: Any) -> tuple[int, dict[str, Any]]:
    payload: dict[str, Any] = {
        "domain": "modules",
        "title": "Módulo src",
        "body": "Descreve o código sob src/.",
    }
    payload.update(overrides)
    response = client.post(
        f"/api/workspaces/{workspace_id}/context", json=payload, headers=SAME_ORIGIN
    )
    return response.status_code, response.json()


# ============================================= herança de autenticação (mesmo padrão da E3)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/workspaces/qualquer/context", None),
        (
            "POST",
            "/api/workspaces/qualquer/context",
            {"domain": "stack", "title": "t", "body": "b"},
        ),
        ("PATCH", "/api/context/qualquer", {"title": "t"}),
        ("DELETE", "/api/context/qualquer", None),
        ("POST", "/api/workspaces/qualquer/context/verify", {}),
        ("POST", "/api/workspaces/qualquer/context/import", SEED),
    ],
)
def test_toda_rota_de_contexto_exige_token(
    api_client: TestClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    """As rotas novas herdam o middleware — nenhuma delas anota autenticação por conta própria.

    O 401 vem **antes** de qualquer lógica: nem o workspace inexistente é consultado, então
    a rota não vira um oráculo de existência para quem não tem credencial.
    """
    response = api_client.request(method, path, json=body, headers=SAME_ORIGIN)

    assert response.status_code == 401, f"{method} {path}"
    assert response.json()["code"] == "unauthorized"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/workspaces/qualquer/context"),
        ("PATCH", "/api/context/qualquer"),
        ("DELETE", "/api/context/qualquer"),
        ("POST", "/api/workspaces/qualquer/context/verify"),
        ("POST", "/api/workspaces/qualquer/context/import"),
    ],
)
def test_escrita_de_outra_origem_e_recusada(
    auth_api_client: TestClient, method: str, path: str
) -> None:
    """A guarda de mesma origem da E3 (`origin_matches_host`) também cobre as rotas novas."""
    response = auth_api_client.request(
        method,
        path,
        json={},
        headers={"Origin": "http://127.0.0.1:9999", "Sec-Fetch-Site": "same-origin"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "cross_origin_denied"


# ========================================================================= GET e POST


def test_criar_e_listar_entrada(auth_api_client: TestClient, repo_path: Path) -> None:
    workspace_id = _workspace(auth_api_client, repo_path)

    status, criada = _create(
        auth_api_client, workspace_id, tags=["backend"], source_refs=["src/**"]
    )

    assert status == 201, criada
    assert criada["workspace_id"] == workspace_id
    assert criada["domain"] == "modules"
    assert criada["origin"] == "manual"
    assert criada["state"] == "fresh"
    assert criada["stale_reason"] is None
    assert criada["source_refs"] == ["src/**"]
    assert criada["source_hash"] is not None
    assert len(criada["source_hash_commit"]) == 40
    assert len(criada["content_hash"]) == 64

    listagem = auth_api_client.get(f"/api/workspaces/{workspace_id}/context")
    assert listagem.status_code == 200
    assert [item["id"] for item in listagem.json()] == [criada["id"]]


def test_listar_filtra_por_dominio(auth_api_client: TestClient, repo_path: Path) -> None:
    workspace_id = _workspace(auth_api_client, repo_path)
    _create(auth_api_client, workspace_id, domain="risks", title="Risco A")
    _create(auth_api_client, workspace_id, domain="risks", title="Risco B")
    _create(auth_api_client, workspace_id, domain="stack", title="Stack")

    resposta = auth_api_client.get(f"/api/workspaces/{workspace_id}/context?domain=risks")

    assert resposta.status_code == 200
    assert len(resposta.json()) == 2


def test_workspace_inexistente_e_404(auth_api_client: TestClient) -> None:
    resposta = auth_api_client.get("/api/workspaces/nao-existe/context")

    assert resposta.status_code == 404
    assert resposta.json()["code"] == "workspace_not_found"


def test_dominio_invalido_e_422(auth_api_client: TestClient, repo_path: Path) -> None:
    workspace_id = _workspace(auth_api_client, repo_path)
    status, _corpo = _create(auth_api_client, workspace_id, domain="inventado")
    assert status == 422


def test_campo_desconhecido_e_recusado(auth_api_client: TestClient, repo_path: Path) -> None:
    """`extra="forbid"`: `sourceRefs` em camelCase não vira "campo ausente" em silêncio."""
    workspace_id = _workspace(auth_api_client, repo_path)
    status, _corpo = _create(auth_api_client, workspace_id, sourceRefs=["src/**"])
    assert status == 422


def test_origin_nao_pode_ser_forjado_na_criacao(
    auth_api_client: TestClient, repo_path: Path
) -> None:
    """`origin` não está no schema: quem cria por esta rota cria `manual`, e ponto."""
    workspace_id = _workspace(auth_api_client, repo_path)
    status, _corpo = _create(auth_api_client, workspace_id, origin="imported_planning")
    assert status == 422


def test_source_ref_com_segredo_e_422(auth_api_client: TestClient, repo_path: Path) -> None:
    write(repo_path, "config/.env.local", "TOKEN=x\n")
    commit_all(repo_path, "acrescenta segredo")
    workspace_id = _workspace(auth_api_client, repo_path)

    status, corpo = _create(auth_api_client, workspace_id, source_refs=["config/*"])

    assert status == 422
    assert corpo["code"] == "invalid_source_refs"


def test_source_ref_com_travessia_e_422(auth_api_client: TestClient, repo_path: Path) -> None:
    workspace_id = _workspace(auth_api_client, repo_path)
    status, corpo = _create(auth_api_client, workspace_id, source_refs=["../**"])

    assert status == 422
    assert corpo["code"] == "invalid_source_refs"


def test_title_vazio_e_422(auth_api_client: TestClient, repo_path: Path) -> None:
    workspace_id = _workspace(auth_api_client, repo_path)
    assert _create(auth_api_client, workspace_id, title="   ")[0] == 422
    assert _create(auth_api_client, workspace_id, body="")[0] == 422


# ============================================================================ PATCH


def test_patch_de_conteudo_nao_toca_o_baseline(
    auth_api_client: TestClient, repo_path: Path
) -> None:
    """O invariante central da fase, agora pela borda HTTP."""
    workspace_id = _workspace(auth_api_client, repo_path)
    _status, criada = _create(auth_api_client, workspace_id, source_refs=["src/**"])

    write(repo_path, "src/app.py", "print('B')\n")
    commit_all(repo_path, "muda o código")

    resposta = auth_api_client.patch(
        f"/api/context/{criada['id']}",
        json={"title": "Outro título", "body": "Outro corpo.", "tags": ["z", "a"]},
        headers=SAME_ORIGIN,
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["content_hash"] != criada["content_hash"]
    assert corpo["tags"] == ["a", "z"]
    assert corpo["source_hash"] == criada["source_hash"]
    assert corpo["source_hash_commit"] == criada["source_hash_commit"]
    assert corpo["state"] == criada["state"]


def test_patch_de_source_refs_move_o_baseline(auth_api_client: TestClient, repo_path: Path) -> None:
    workspace_id = _workspace(auth_api_client, repo_path)
    _status, criada = _create(auth_api_client, workspace_id, source_refs=["src/app.py"])

    write(repo_path, "src/novo.py", "NOVO = 1\n")
    commit_all(repo_path, "acrescenta novo.py")

    resposta = auth_api_client.patch(
        f"/api/context/{criada['id']}",
        json={"source_refs": ["src/**"]},
        headers=SAME_ORIGIN,
    )

    corpo = resposta.json()
    assert resposta.status_code == 200
    assert corpo["source_refs"] == ["src/**"]
    assert corpo["source_hash"] != criada["source_hash"]
    assert corpo["source_hash_commit"] != criada["source_hash_commit"]
    assert corpo["state"] == "fresh"


def test_patch_com_source_refs_vazio_zera_o_baseline(
    auth_api_client: TestClient, repo_path: Path
) -> None:
    workspace_id = _workspace(auth_api_client, repo_path)
    _status, criada = _create(auth_api_client, workspace_id, source_refs=["src/**"])

    resposta = auth_api_client.patch(
        f"/api/context/{criada['id']}", json={"source_refs": []}, headers=SAME_ORIGIN
    )

    corpo = resposta.json()
    assert corpo["source_refs"] == []
    assert corpo["source_hash"] is None
    assert corpo["source_hash_commit"] is None
    assert corpo["state"] == "fresh"


def test_patch_vazio_e_inofensivo(auth_api_client: TestClient, repo_path: Path) -> None:
    """Corpo `{}`: nenhum campo informado, nada muda — nem o baseline, nem o conteúdo."""
    workspace_id = _workspace(auth_api_client, repo_path)
    _status, criada = _create(auth_api_client, workspace_id, source_refs=["src/**"])

    resposta = auth_api_client.patch(f"/api/context/{criada['id']}", json={}, headers=SAME_ORIGIN)

    corpo = resposta.json()
    assert resposta.status_code == 200
    for campo in ("content_hash", "source_hash", "source_hash_commit", "state", "source_refs"):
        assert corpo[campo] == criada[campo], campo


def test_patch_de_entrada_inexistente_e_404(auth_api_client: TestClient) -> None:
    resposta = auth_api_client.patch(
        "/api/context/nao-existe", json={"title": "x"}, headers=SAME_ORIGIN
    )

    assert resposta.status_code == 404
    assert resposta.json()["code"] == "context_entry_not_found"


def test_patch_com_source_ref_recusado_e_422(auth_api_client: TestClient, repo_path: Path) -> None:
    write(repo_path, "config/.env.local", "TOKEN=x\n")
    commit_all(repo_path, "acrescenta segredo")
    workspace_id = _workspace(auth_api_client, repo_path)
    _status, criada = _create(auth_api_client, workspace_id, source_refs=["src/**"])

    resposta = auth_api_client.patch(
        f"/api/context/{criada['id']}", json={"source_refs": ["config/*"]}, headers=SAME_ORIGIN
    )

    assert resposta.status_code == 422
    assert resposta.json()["code"] == "invalid_source_refs"

    # e a entrada continua como estava
    depois = auth_api_client.get(f"/api/workspaces/{workspace_id}/context").json()[0]
    assert depois["source_refs"] == ["src/**"]
    assert depois["source_hash"] == criada["source_hash"]


# =========================================================================== DELETE


def test_delete_remove_e_e_livre(auth_api_client: TestClient, repo_path: Path) -> None:
    workspace_id = _workspace(auth_api_client, repo_path)
    _status, criada = _create(auth_api_client, workspace_id, source_refs=["src/**"])

    resposta = auth_api_client.delete(f"/api/context/{criada['id']}", headers=SAME_ORIGIN)

    assert resposta.status_code == 204
    assert auth_api_client.get(f"/api/workspaces/{workspace_id}/context").json() == []


def test_delete_de_entrada_inexistente_e_404(auth_api_client: TestClient) -> None:
    resposta = auth_api_client.delete("/api/context/nao-existe", headers=SAME_ORIGIN)

    assert resposta.status_code == 404


# =========================================================================== VERIFY


def test_verify_marca_stale_working_tree_e_registra_a_divergencia(
    auth_api_client: TestClient, repo_path: Path
) -> None:
    workspace_id = _workspace(auth_api_client, repo_path)
    _status, criada = _create(auth_api_client, workspace_id, source_refs=["src/**"])

    write(repo_path, "src/app.py", "print('sujo')\n")
    write(repo_path, "README.md", "# também sujo, mas fora da cobertura\n")

    resposta = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context/verify", json={}, headers=SAME_ORIGIN
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["verification_commit"] == criada["source_hash_commit"]

    entrada = corpo["entries"][0]
    assert entrada["state"] == "stale"
    assert entrada["stale_reason"] == "working_tree"
    assert entrada["source_hash"] == criada["source_hash"], "verify não move o baseline"
    assert entrada["last_verified_commit"] == corpo["verification_commit"]

    divergencia = corpo["working_tree_divergence"]
    assert divergencia["dirty_file_count"] == 2, "o workspace inteiro é contado"
    assert [item["path"] for item in divergencia["covered"]] == ["src/app.py"]
    assert divergencia["covered"][0]["kind"] == "modified"
    assert divergencia["covered"][0]["entry_id"] == criada["id"]


def test_verify_marca_sources_changed_e_nao_cura_na_repeticao(
    auth_api_client: TestClient, repo_path: Path
) -> None:
    workspace_id = _workspace(auth_api_client, repo_path)
    _status, criada = _create(auth_api_client, workspace_id, source_refs=["src/**"])

    write(repo_path, "src/app.py", "print('B')\n")
    commit_all(repo_path, "B")

    primeiro = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context/verify", json={}, headers=SAME_ORIGIN
    ).json()
    segundo = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context/verify", json={}, headers=SAME_ORIGIN
    ).json()

    for corpo in (primeiro, segundo):
        entrada = corpo["entries"][0]
        assert entrada["state"] == "stale"
        assert entrada["stale_reason"] == "sources_changed"
        assert entrada["source_hash"] == criada["source_hash"]
        assert entrada["source_hash_commit"] == criada["source_hash_commit"]
        assert entrada["last_verified_commit"] != criada["source_hash_commit"]


def test_verify_sem_git_devolve_unknown(auth_api_client: TestClient, plain_path: Path) -> None:
    workspace_id = _workspace(auth_api_client, plain_path, name="sem-git")
    _create(auth_api_client, workspace_id, source_refs=["src/**"])

    corpo = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context/verify", json={}, headers=SAME_ORIGIN
    ).json()

    assert corpo["verification_commit"] is None
    assert corpo["entries"][0]["state"] == "unknown"
    assert corpo["working_tree_divergence"]["dirty_file_count"] is None
    assert corpo["working_tree_divergence"]["covered"] == []


def test_verify_de_workspace_sem_entradas(auth_api_client: TestClient, repo_path: Path) -> None:
    workspace_id = _workspace(auth_api_client, repo_path)

    corpo = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context/verify", json={}, headers=SAME_ORIGIN
    ).json()

    assert corpo["entries"] == []
    assert corpo["working_tree_divergence"]["dirty_file_count"] == 0


def test_verify_de_workspace_inexistente_e_404(auth_api_client: TestClient) -> None:
    resposta = auth_api_client.post(
        "/api/workspaces/nao-existe/context/verify", json={}, headers=SAME_ORIGIN
    )

    assert resposta.status_code == 404


# ============================================================ Gate 7 — IMPORT do seed


def _import(client: TestClient, workspace_id: str, seed: dict[str, Any]) -> dict[str, Any]:
    resposta = client.post(
        f"/api/workspaces/{workspace_id}/context/import", json=seed, headers=SAME_ORIGIN
    )
    assert resposta.status_code == 201, resposta.text
    return dict(resposta.json())


def test_import_cria_as_entradas_da_tabela_de_03_secao_1(
    auth_api_client: TestClient, repo_path: Path
) -> None:
    """Contagem e conteúdo de cada entrada, contra a tabela do documento.

    1 `objective` (problema + objetivo juntos) · 2 `requirements` · 1 `stack` ·
    1 `architecture` · 2 `decisions` · 2 `risks` = **9**.
    """
    workspace_id = _workspace(auth_api_client, repo_path)

    corpo = _import(auth_api_client, workspace_id, SEED)

    assert corpo["created"] == 9
    entradas = corpo["entries"]

    por_dominio: dict[str, list[dict[str, Any]]] = {}
    for entrada in entradas:
        por_dominio.setdefault(entrada["domain"], []).append(entrada)

    assert {chave: len(valor) for chave, valor in por_dominio.items()} == {
        "objective": 1,
        "requirements": 2,
        "stack": 1,
        "architecture": 1,
        "decisions": 2,
        "risks": 2,
    }

    # todas nascem com a mesma procedência e sem dependência de código ([03] §1)
    for entrada in entradas:
        assert entrada["origin"] == "imported_planning"
        assert entrada["source_refs"] == []
        assert entrada["state"] == "fresh"
        assert entrada["stale_reason"] is None
        assert entrada["source_hash"] is None
        assert entrada["source_hash_commit"] is None

    objetivo = por_dominio["objective"][0]
    assert SEED["problem"] in objetivo["body"]
    assert SEED["objective"] in objetivo["body"]

    requisitos = {item["title"]: item["body"] for item in por_dominio["requirements"]}
    assert "Cadastrar cliente" in requisitos["Requisitos funcionais"]
    assert "Funcionar offline" in requisitos["Requisitos não funcionais"]
    assert "Cadastrar cliente" not in requisitos["Requisitos não funcionais"]

    assert "React" in por_dominio["stack"][0]["body"]
    assert por_dominio["architecture"][0]["body"] == SEED["architecture"]

    decisoes = {item["title"]: item for item in por_dominio["decisions"]}
    assert set(decisoes) == {"Usar SQLite", "Sem embeddings"}
    assert decisoes["Usar SQLite"]["structured"] == {
        "decision": "Banco local em arquivo",
        "reason": "Zero setup",
        "date": None,
    }

    riscos = {item["title"]: item for item in por_dominio["risks"]}
    assert "OneDrive corrompe o SQLite" in riscos
    assert riscos["OneDrive corrompe o SQLite"]["structured"] == {
        "mitigation": "data_dir fora do OneDrive"
    }


def test_reimportar_cria_um_segundo_conjunto_e_nao_sobrescreve(
    auth_api_client: TestClient, repo_path: Path
) -> None:
    """[03] §1 + [ADR-0006] item 2: reimportar **nunca** sobrescreve em silêncio.

    Sincronização bidirecional reintroduziria o acoplamento que [ADR-0002] elimina; e uma
    entrada editada à mão depois do seed seria desfeita sem aviso por um import que
    sobrescrevesse. A duplicata é visível e o usuário decide o que fazer com ela.
    """
    workspace_id = _workspace(auth_api_client, repo_path)

    primeiro = _import(auth_api_client, workspace_id, SEED)
    ids_primeiro = {item["id"] for item in primeiro["entries"]}

    # uma entrada do primeiro conjunto é editada à mão
    alvo = next(item for item in primeiro["entries"] if item["domain"] == "stack")
    auth_api_client.patch(
        f"/api/context/{alvo['id']}", json={"body": "Editado à mão."}, headers=SAME_ORIGIN
    )

    segundo = _import(auth_api_client, workspace_id, SEED)
    ids_segundo = {item["id"] for item in segundo["entries"]}

    assert segundo["created"] == 9
    assert ids_primeiro.isdisjoint(ids_segundo), "nenhum id foi reaproveitado"

    todas = auth_api_client.get(f"/api/workspaces/{workspace_id}/context").json()
    assert len(todas) == 18

    # a edição manual sobreviveu ao reimport
    editada = next(item for item in todas if item["id"] == alvo["id"])
    assert editada["body"] == "Editado à mão."


def test_import_pula_campo_vazio(auth_api_client: TestClient, repo_path: Path) -> None:
    """`body` não pode ser vazio ([02] §2): campo em branco não vira entrada de ruído."""
    workspace_id = _workspace(auth_api_client, repo_path)

    corpo = _import(
        auth_api_client,
        workspace_id,
        {"objective": "Só o objetivo.", "stack": ["Python"]},
    )

    assert corpo["created"] == 2
    assert {item["domain"] for item in corpo["entries"]} == {"objective", "stack"}


def test_import_de_seed_completamente_vazio_cria_nada(
    auth_api_client: TestClient, repo_path: Path
) -> None:
    workspace_id = _workspace(auth_api_client, repo_path)
    corpo = _import(auth_api_client, workspace_id, {})

    assert corpo["created"] == 0
    assert corpo["entries"] == []


def test_import_com_campo_desconhecido_e_422(auth_api_client: TestClient, repo_path: Path) -> None:
    """`extra="forbid"`: o contrato é `snake_case`, e camelCase do frontend é erro explícito."""
    workspace_id = _workspace(auth_api_client, repo_path)

    resposta = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context/import",
        json={"functionalRequirements": ["x"]},
        headers=SAME_ORIGIN,
    )

    assert resposta.status_code == 422


def test_import_em_workspace_inexistente_e_404(auth_api_client: TestClient) -> None:
    resposta = auth_api_client.post(
        "/api/workspaces/nao-existe/context/import", json=SEED, headers=SAME_ORIGIN
    )

    assert resposta.status_code == 404


def test_titulo_longo_de_risco_e_truncado(auth_api_client: TestClient, repo_path: Path) -> None:
    """A descrição de um risco pode ser longa; o `title` da coluna é `String(255)`."""
    workspace_id = _workspace(auth_api_client, repo_path)
    longa = "R" * 400

    corpo = _import(auth_api_client, workspace_id, {"risks": [{"description": longa}]})

    entrada = corpo["entries"][0]
    assert len(entrada["title"]) <= 255
    assert entrada["title"].endswith("…")
    assert entrada["body"] == longa, "o corpo preserva a descrição inteira"
