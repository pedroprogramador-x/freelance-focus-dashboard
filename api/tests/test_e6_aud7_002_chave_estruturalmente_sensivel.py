"""E6-AUD7-002 — a classificação estrutural da chave precisa alcançar o valor.

`is_sensitive_key("token")` é verdadeiro desde a E5, mas até esta correção a resposta
`{"token": "AUD7_SYNTHETIC_CREDENTIAL"}` saía **crua** pela API: `redact_document`
percorria chaves e valores separadamente, aplicando `is_sensitive_key` às chaves e o
detector textual aos valores. Um valor que não casasse com nenhum padrão do catálogo
atravessava intacto, mesmo debaixo de uma chave que o próprio sistema classifica como
"o valor aqui é segredo".

É o buraco que [04] §5 registra como obrigação aberta:

> **Chave estruturalmente sensível** *(E6-AUD7-002)*. `is_sensitive_key` é a resposta do
> sistema para "não existe adjacência textual que um regex veja": `{"token": "x"}` não
> contém `token: x` em lugar nenhum até alguém linearizar. A projeção pública precisa
> tratar uma chave assim como classificação estrutural: **o valor sob ela — escalar,
> objeto ou lista inteira — é sensível, e a subárvore é projetada *fail-closed***, sem
> depender de o detector reconhecer forma de segredo no conteúdo.

## A representação não é nova

A semântica implementada aqui é a que o Context Engine (E5) já aplica em
`context_engine/rendering.py::_collect_leaves`: uma chave que `is_sensitive_key`
reconhece marca a subárvore inteira, a marca desce por `dict`, por `list`, por índice e
por qualquer chave intermediária, e cada **folha** sob ela sai como `REDACTED` inteiro,
"sem olhar o conteúdo" (`_mark_fragments`, camada 1). Os **caminhos** continuam
visíveis lá; aqui, analogamente, as chaves continuam com a semântica que já tinham
(`_redact_key`) e só os valores-folha viram o marcador. Nenhum formato público novo foi
inventado para esta correção.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.responses import EditViewJSONResponse, RedactingJSONResponse
from app.safety.redaction import (
    REDACTED,
    Unredacted,
    is_sensitive_key,
    redact_document,
)

#: O material sintético do relatório da Rodada 7. Escolhido justamente por **não** casar
#: com nenhum padrão do catálogo: se ele aparecer na saída, foi a classificação
#: estrutural que falhou, não o detector textual.
MATERIAL = "AUD7_SYNTHETIC_CREDENTIAL"

SAME_ORIGIN = {"Origin": "http://127.0.0.1:8756", "Sec-Fetch-Site": "same-origin"}


# ================================================== o contrato, no nível de `redact_document`


def test_e6_aud7_002_a_premissa_do_finding_continua_valida() -> None:
    """Antes de qualquer asserção sobre a correção: as chaves do finding são mesmo
    classificadas como sensíveis, e o material sintético mesmo **não** é reconhecido
    pelo detector textual.

    Se este teste falhar, a causa não é a propagação estrutural — é a lista de nomes ou o
    catálogo terem mudado, e aí os demais testes deste módulo estariam medindo outra
    coisa.
    """
    for nome in ("token", "password", "api_key", "secret", "auth_token"):
        assert is_sensitive_key(nome), nome
    for nome in ("safe", "items", "outer", "value", "metadata", "owner", "nested"):
        assert not is_sensitive_key(nome), nome

    # O detector textual, sozinho, não vê segredo nenhum nestes valores.
    for valor in (MATERIAL, "ordinary-looking-value", "NOT_REGEX_SECRET", "public-looking"):
        assert redact_document(valor) == valor, valor


def test_e6_aud7_002_escalar_sob_chave_sensivel_e_redigido() -> None:
    """O caso literal do relatório: `{"token": "AUD7_SYNTHETIC_CREDENTIAL"}`."""
    assert redact_document({"token": MATERIAL}) == {"token": REDACTED}
    assert redact_document({"password": "ordinary-looking-value"}) == {"password": REDACTED}


def test_e6_aud7_002_subarvore_sob_chave_sensivel_e_redigida_inteira() -> None:
    """`dict` e `list` sob chave sensível: nenhuma folha escapa por parecer pública.

    As chaves intermediárias continuam visíveis — é a mesma escolha do E5, que preserva o
    caminho (`password.current`) e redige só o valor. O que não pode sobreviver é
    **conteúdo** de folha.
    """
    obtido = redact_document(
        {"api_key": {"value": "NOT_REGEX_SECRET", "metadata": {"owner": "public-looking"}}}
    )
    assert obtido == {"api_key": {"value": REDACTED, "metadata": {"owner": REDACTED}}}

    profundo = redact_document({"token": {"nested": ["A", {"value": "B"}]}})
    assert profundo == {"token": {"nested": [REDACTED, {"value": REDACTED}]}}


def test_e6_aud7_002_chave_sensivel_aninhada_marca_a_subarvore() -> None:
    """A chave sensível não precisa estar na raiz."""
    assert redact_document({"outer": {"token": "SYNTHETIC"}}) == {"outer": {"token": REDACTED}}


def test_e6_aud7_002_chave_sensivel_dentro_de_objeto_em_lista() -> None:
    """Objeto dentro de lista: a classificação vale por objeto, não por posição."""
    obtido = redact_document({"items": [{"password": "ordinary"}, {"safe": "visible"}]})
    assert obtido == {"items": [{"password": REDACTED}, {"safe": "visible"}]}


def test_e6_aud7_002_irmaos_publicos_continuam_visiveis() -> None:
    """A classificação de `token` não contamina `name` nem `status`.

    Fail-closed é sobre a subárvore classificada, não sobre o documento. Transformar o
    documento inteiro em segredo porque um campo é sensível destruiria a projeção pública
    sem ganho de confidencialidade nenhum.
    """
    obtido = redact_document({"name": "Pedro", "token": "SECRET", "status": "active"})
    assert obtido == {"name": "Pedro", "token": REDACTED, "status": "active"}

    vizinhos = redact_document({"safe": "AUD7_PUBLIC_VALUE", "token": MATERIAL})
    assert vizinhos == {"safe": "AUD7_PUBLIC_VALUE", "token": REDACTED}


def test_e6_aud7_002_multiplas_chaves_sensiveis_na_mesma_arvore() -> None:
    obtido = redact_document(
        {
            "password": "a",
            "publico": "b",
            "nivel": {"api_key": ["c", "d"], "outro": "e"},
        }
    )
    assert obtido == {
        "password": REDACTED,
        "publico": "b",
        "nivel": {"api_key": [REDACTED, REDACTED], "outro": "e"},
    }


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("", REDACTED),
        (None, REDACTED),
        (0, REDACTED),
        (1234, REDACTED),
        (False, REDACTED),
        (True, REDACTED),
        (3.5, REDACTED),
        ([], REDACTED),
        ({}, REDACTED),
    ],
)
def test_e6_aud7_002_escalares_genericos_sob_chave_sensivel(valor: Any, esperado: Any) -> None:
    """Número, booleano, `None`, string vazia e contêiner vazio sob chave sensível também
    são redigidos (E6-AUD7-002V-001, achado pelo Codex na verificação independente).

    Um PIN, um número de conta ou um booleano de "tem credencial configurada" são
    exatamente o tipo de valor que o detector textual nunca reconheceria — e é por isso
    que a classificação é **estrutural**. É também o que o E5 já faz: `_Leaf.generic_scalar`
    é "redigido localmente quando sensível", e um contêiner vazio vira exatamente esse
    tipo de folha em `_collect_leaves` (`rendering.py:268`/`275`).

    `[]`/`{}` sob chave sensível **não** continuam vazios: um contêiner vazio não tem
    elemento para a recursão alcançar, então sem tratá-lo como folha ele nunca passava
    pela pergunta "isto está sob uma marca sensível?" e saía intacto — revelando o tipo do
    contêiner e a condição de vazio de um campo classificado como segredo. Contêiner vazio
    **fora** de subárvore sensível continua vazio (ver
    `test_e6_aud7_002_irmaos_publicos_com_containers_vazios_intactos`, abaixo).
    """
    assert redact_document({"secret": valor}) == {"secret": esperado}


def test_e6_aud7_002_valor_que_ja_seria_redigido_textualmente_nao_muda_de_forma() -> None:
    """Um valor que o detector já reconhecia continua saindo como marcador — a
    classificação estrutural cobre mais casos, nunca menos.
    """
    assert redact_document({"token": "ghp_" + "a" * 20}) == {"token": REDACTED}
    assert redact_document({"publico": "ghp_" + "a" * 20}) == {"publico": REDACTED}


def test_e6_aud7_002_semantica_das_chaves_permanece() -> None:
    """A projeção da **chave** não muda: chave com segredo continua virando placeholder
    numerado, chave comum continua literal — inclusive dentro de subárvore sensível.
    """
    obtido = redact_document({"token": {"ghp_" + "b" * 20: "x", "comum": "y"}})
    assert isinstance(obtido, dict)
    interno = obtido["token"]
    assert isinstance(interno, dict)
    assert "comum" in interno
    assert interno["comum"] == REDACTED
    assert any(chave.startswith("«chave redigida ") for chave in interno)


def test_e6_aud7_002_chave_nao_textual_nao_quebra_a_travessia() -> None:
    """Chave que não é `str` (o `json` nem a emitiria) não pode explodir a caminhada."""
    assert redact_document({1: "a", "token": "b"}) == {1: "a", "token": REDACTED}


# ================================================== E6-AUD7-002V-001: contêineres vazios


def test_e6_aud7_002v_001_lista_vazia_sensivel_na_raiz() -> None:
    assert redact_document({"token": []}) == {"token": REDACTED}


def test_e6_aud7_002v_001_dict_vazio_sensivel_na_raiz() -> None:
    assert redact_document({"token": {}}) == {"token": REDACTED}


def test_e6_aud7_002v_001_containers_vazios_aninhados_sob_chave_sensivel() -> None:
    """Um contêiner vazio a dois níveis de profundidade dentro da subárvore sensível
    ainda é folha — a marca `_sensitive` desce por `dict` e `list` intermediários.
    """
    assert redact_document({"password": {"nested": []}}) == {"password": {"nested": REDACTED}}
    assert redact_document({"password": {"nested": {}}}) == {"password": {"nested": REDACTED}}
    assert redact_document({"password": {"a": {"b": []}}}) == {"password": {"a": {"b": REDACTED}}}


def test_e6_aud7_002v_001_containers_vazios_dentro_de_lista_sensivel() -> None:
    """Elementos vazios de uma lista sob chave sensível — cada elemento é a folha, não a
    lista como um todo.
    """
    assert redact_document({"api_key": [{"value": {}}, []]}) == {
        "api_key": [{"value": REDACTED}, REDACTED]
    }


def test_e6_aud7_002v_001_mistura_de_folhas_preenchidas_e_containers_vazios() -> None:
    obtido = redact_document(
        {"token": {"preenchido": "AUD7_SYNTHETIC_CREDENTIAL", "vazio_lista": [], "vazio_dict": {}}}
    )
    assert obtido == {
        "token": {"preenchido": REDACTED, "vazio_lista": REDACTED, "vazio_dict": REDACTED}
    }


def test_e6_aud7_002v_001_irmaos_publicos_com_containers_vazios_intactos() -> None:
    """`[]`/`{}` fora de subárvore sensível continuam vazios — a correção não redige
    contêiner vazio por ser vazio, só por estar sob uma chave sensível.
    """
    obtido = redact_document({"public": [], "token": [], "status": "active"})
    assert obtido == {"public": [], "token": REDACTED, "status": "active"}

    obtido2 = redact_document({"metadata": {}, "password": {}})
    assert obtido2 == {"metadata": {}, "password": REDACTED}


def test_e6_aud7_002v_001_multiplas_chaves_sensiveis_com_containers_vazios() -> None:
    obtido = redact_document({"token": [], "api_key": {}, "publico": [], "secret": "x"})
    assert obtido == {"token": REDACTED, "api_key": REDACTED, "publico": [], "secret": REDACTED}


def test_e6_aud7_002v_001_boundary_central_redige_container_vazio_sensivel() -> None:
    """A prova na fronteira central, para além do teste unitário de `redact_document`."""
    brutos = RedactingJSONResponse(content={"token": [], "publico": []}).body
    assert b'"token":[]' not in brutos
    assert REDACTED.encode() in brutos
    assert b'"publico":[]' in brutos


def test_e6_aud7_002v_001_unredacted_nao_e_afetado_pelo_caso_vazio() -> None:
    """`Unredacted` é sempre `str`, nunca contêiner — a checagem de vazio não pode
    interferir no escape hatch, mesmo para a string vazia.
    """
    vazio = Unredacted("")
    assert redact_document({"purge_token": vazio}) == {"purge_token": vazio}


def test_e6_aud7_002v_001_context_registry_post_e_listagem_nao_vazam_container_vazio(
    auth_api_client: TestClient, repo_path: Path
) -> None:
    """Reprodução HTTP do achado do Codex: POST e a listagem real do Context Registry.

    A API não oferece GET individual desta entrada — só a listagem por workspace.
    """
    workspace_id = _workspace(auth_api_client, repo_path)

    criacao = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context",
        json={
            "domain": "modules",
            "title": "t",
            "body": "b",
            "structured": {"token": [], "password": {"nested": []}, "status": "active"},
        },
        headers=SAME_ORIGIN,
    )
    assert criacao.status_code == 201, criacao.text
    assert b'"token":[]' not in criacao.content
    assert criacao.json()["structured"] == {
        "token": REDACTED,
        "password": {"nested": REDACTED},
        "status": "active",
    }

    listagem = auth_api_client.get(f"/api/workspaces/{workspace_id}/context")
    assert listagem.status_code == 200, listagem.text
    assert b'"token":[]' not in listagem.content
    entradas = listagem.json()
    assert len(entradas) == 1
    assert entradas[0]["structured"] == {
        "token": REDACTED,
        "password": {"nested": REDACTED},
        "status": "active",
    }


# ================================================== os dois escape hatches deliberados


def test_e6_aud7_002_unredacted_continua_vencendo_a_classificacao_estrutural() -> None:
    """`is_sensitive_key("purge_token")` é **verdadeiro** — o componente `token` está lá.

    Sem esta precedência a correção quebraria a purga: `PurgeTokenStore.issue` devolve um
    `Unredacted`, e a rota o entrega sob a chave literal `purge_token`. `Unredacted` é o
    marcador de "este valor a resposta tem a função de entregar" ([04] §5), e continua
    sendo a decisão mais específica — a classificação estrutural da chave não a revoga.
    """
    assert is_sensitive_key("purge_token")

    token = Unredacted("PURGE_TOKEN_VALUE_XYZ")
    assert redact_document({"purge_token": token}) == {"purge_token": token}

    # Também dentro de uma subárvore sensível, que é o caso real: `purge_token` é irmão
    # das contagens num corpo que atravessa `RedactingJSONResponse` inteiro.
    aninhado = redact_document({"token": {"interno": Unredacted("ENTREGUE")}})
    assert aninhado == {"token": {"interno": "ENTREGUE"}}


def test_e6_aud7_002_edit_view_continua_sem_redacao() -> None:
    """A segunda exceção deliberada não passa por `redact_document` — nem antes, nem agora."""
    corpo = {"structured": {"token": MATERIAL}}
    assert MATERIAL.encode() in EditViewJSONResponse(content=corpo).body


# ================================================== a fronteira HTTP central


def test_e6_aud7_002_boundary_central_redige_chave_sensivel() -> None:
    """`RedactingJSONResponse` é a fronteira única de [04] §5: provar nela é provar para
    toda rota JSON, sem um teste HTTP por endpoint.
    """
    brutos = RedactingJSONResponse(content={"token": MATERIAL}).body
    assert MATERIAL.encode() not in brutos
    assert REDACTED.encode() in brutos

    aninhado = RedactingJSONResponse(
        content={"dados": {"items": [{"api_key": {"v": MATERIAL}}]}}
    ).body
    assert MATERIAL.encode() not in aninhado


def test_e6_aud7_002_boundary_central_preserva_valor_sob_chave_publica() -> None:
    """O comportamento normal de campo público não muda."""
    brutos = RedactingJSONResponse(content={"titulo": "conteúdo público"}).body
    assert "conteúdo público".encode() in brutos


def test_e6_aud7_002_boundary_central_preserva_purge_token() -> None:
    brutos = RedactingJSONResponse(
        content={"counts": {"runs": 2}, "purge_token": Unredacted("TOKEN_ENTREGUE")}
    ).body
    assert b"TOKEN_ENTREGUE" in brutos


# ================================================== regressão HTTP do Context Registry


def _workspace(client: TestClient, path: Path) -> str:
    resposta = client.post(
        "/api/workspaces",
        json={"name": "ws", "type": "personal", "local_path": str(path)},
        headers=SAME_ORIGIN,
    )
    assert resposta.status_code == 201, resposta.text
    return str(resposta.json()["id"])


def test_e6_aud7_002_context_registry_post_e_get_nao_vazam(
    auth_api_client: TestClient, repo_path: Path
) -> None:
    """A reprodução literal do relatório, ponta a ponta: POST e GET.

    O relatório criou a entrada com `structured={"token": "AUD7_SYNTHETIC_CREDENTIAL"}` e
    observou o valor integral nas duas respostas. A inspeção é sobre **bytes crus**, não
    sobre o objeto desserializado: uma projeção que "parece" redigida mas serializa o
    valor original continuaria sendo o mesmo vazamento.

    O que o banco guarda internamente não é o assunto aqui — o finding é a projeção
    pública, e a persistência não foi alterada.
    """
    workspace_id = _workspace(auth_api_client, repo_path)

    criacao = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context",
        json={
            "domain": "modules",
            "title": "Módulo src",
            "body": "Descreve o código sob src/.",
            "structured": {"token": MATERIAL},
        },
        headers=SAME_ORIGIN,
    )
    assert criacao.status_code == 201, criacao.text
    assert MATERIAL.encode() not in criacao.content
    assert criacao.json()["structured"] == {"token": REDACTED}

    listagem = auth_api_client.get(f"/api/workspaces/{workspace_id}/context")
    assert listagem.status_code == 200, listagem.text
    assert MATERIAL.encode() not in listagem.content

    entradas = listagem.json()
    assert len(entradas) == 1
    assert entradas[0]["structured"] == {"token": REDACTED}


def test_e6_aud7_002_context_registry_subarvore_aninhada_nao_vaza(
    auth_api_client: TestClient, repo_path: Path
) -> None:
    """A mesma rota, com a subárvore do item C da tarefa."""
    workspace_id = _workspace(auth_api_client, repo_path)

    criacao = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context",
        json={
            "domain": "modules",
            "title": "Módulo src",
            "body": "corpo",
            "structured": {
                "api_key": {"value": "NOT_REGEX_SECRET", "metadata": {"owner": "public-looking"}},
                "publico": "VISIVEL_AUD7",
            },
        },
        headers=SAME_ORIGIN,
    )
    assert criacao.status_code == 201, criacao.text
    assert b"NOT_REGEX_SECRET" not in criacao.content
    assert b"public-looking" not in criacao.content
    assert b"VISIVEL_AUD7" in criacao.content

    listagem = auth_api_client.get(f"/api/workspaces/{workspace_id}/context")
    assert listagem.status_code == 200, listagem.text
    assert b"NOT_REGEX_SECRET" not in listagem.content
    assert b"public-looking" not in listagem.content
    assert b"VISIVEL_AUD7" in listagem.content
