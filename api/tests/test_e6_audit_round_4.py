"""As reproduções exatas da **quarta** auditoria independente da E6, como regressão.

Cada bloco abaixo é a sonda do Codex reescrita com os nomes deste repositório, e cada um
falhava antes desta rodada:

* **E6-AUD4-001** — segredo concatenado sem separador escapava da fronteira de palavra. Os
  padrões de token opaco começam com ``\\b``, e entre duas cópias coladas não existe
  fronteira nenhuma: a segunda não era reconhecida. Em ``AKIA``, que tinha fronteira dos
  **dois** lados, nem a primeira era.
* **E6-AUD4-002** — o placeholder de chave redigida podia colidir com um nome de chave
  literal que já tivesse a mesma forma, e num `dict` uma apagava a outra.
* **E6-AUD4-003** — a `HTTPException` do Starlette (`404` de rota, `405` de método) não
  passava pelo boundary de redação.
* **E6-AUD4-004** — o fluxo de edição do Context Registry: a projeção sai redigida, o
  editor a lia, salvava, e o marcador virava conteúdo. Sem leitura de conteúdo cru e sem
  controle de concorrência.

O que estes testes **não** afirmam: que `detect_secret_spans` reconheça todo segredo. Um
segredo colado a uma palavra comum (`tokenizerghp_…`) continua fora do alcance da fronteira
de palavra, como sempre esteve — ver `test_a_fronteira_de_palavra_continua_valendo_contra
_texto_comum`, que afirma isso em vez de fingir o contrário.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings
from app.db.models import ContextRegistryEntry, DevWorkspace, SafetyEvent
from app.db.session import create_engine, create_session_factory, session_scope
from app.main import create_app
from app.safety.redaction import (
    REDACTED,
    REDACTED_KEY_PREFIX,
    REDACTED_KEY_SUFFIX,
    contains_redaction_marker,
    redact,
    redact_document,
)
from tests import context_helpers

# ====================================================================== E6-AUD4-001

#: Os marcadores **sintéticos** da reprodução do Codex. Nenhuma credencial real.
MARCADOR_GITHUB = "ghp_FourthAuditSynthetic0123456789AB"
MARCADOR_OPENAI = "sk-FourthAuditSynthetic0123456789AB"
MARCADOR_AWS = "AKIAIOSFODNN7EXAMPLE"
MARCADOR_ANTHROPIC = "sk-ant-FourthAuditSynthetic0123456789AB"

TODOS_OS_MARCADORES = (
    MARCADOR_GITHUB,
    MARCADOR_OPENAI,
    MARCADOR_AWS,
    MARCADOR_ANTHROPIC,
)


def _edit_hash(client: TestClient, entry_id: str) -> str:
    """O `edit_hash` corrente, lido pelo caminho que o contrato define: `POST edit-view`."""
    resposta = client.post(f"/api/context/{entry_id}/edit-view")
    assert resposta.status_code == 200, resposta.text
    return str(resposta.json()["edit_hash"])


@pytest.mark.parametrize("marcador", TODOS_OS_MARCADORES)
@pytest.mark.parametrize("n", [2, 3, 5, 9])
def test_segredo_repetido_e_colado_nao_sobrevive(marcador: str, n: int) -> None:
    """A reprodução literal do finding: `redact(M * n)` para n = 2, 3, 5, 9.

    Antes desta rodada sobreviviam `n - 2` cópias inteiras no caso do GitHub e do `sk-`
    (a guloseima comia o prefixo da segunda, e da terceira em diante não havia fronteira
    nenhuma), e **todas** as `n` no caso da AWS, que tinha fronteira também à direita e
    por isso não reconhecia nem a primeira.
    """
    saida = redact(marcador * n)

    assert marcador not in saida, (
        f"{n} cópias coladas de {marcador[:8]}… deixaram passar uma inteira: {saida!r}"
    )
    assert REDACTED in saida


@pytest.mark.parametrize("primeiro", TODOS_OS_MARCADORES)
@pytest.mark.parametrize("segundo", TODOS_OS_MARCADORES)
def test_segredos_distintos_colados_nao_sobrevivem(primeiro: str, segundo: str) -> None:
    """ "o mesmo segredo **ou segredos distintos** colado sem separador" — as 16 ordens.

    O caso que motivou tratar a fronteira à direita como regra do motor, e não como `\\b`
    dentro do regex, é `AKIA…` seguido de `ghp_…`: o `\\b` final da AWS falhava porque o
    vizinho era alfanumérico, e o `\\b` inicial do GitHub falhava pelo mesmo motivo. Os
    dois segredos atravessavam intactos, cada um por causa do outro.
    """
    saida = redact(primeiro + segundo)

    assert primeiro not in saida, f"o primeiro sobreviveu: {saida!r}"
    assert segundo not in saida, f"o segundo sobreviveu: {saida!r}"


@pytest.mark.parametrize("marcador", TODOS_OS_MARCADORES)
def test_a_correcao_e_aditiva_nos_casos_isolados(marcador: str) -> None:
    """O caso de sempre — um segredo isolado, com texto em volta — não mudou de forma."""
    assert (
        redact(f"a chave é {marcador} e o resto do texto")
        == f"a chave é {REDACTED} e o resto do texto"
    )


def test_os_casos_ja_fechados_da_e5_continuam_iguais() -> None:
    """Prefixo preservado e lookaround: o que as cinco rodadas da E5 fecharam, intacto.

    É a metade defensiva desta correção. Um relaxamento de fronteira mal colocado apagaria
    o rótulo `Bearer ` (que `redact` sempre preservou) ou comeria o `://` e o `@` que
    provam que um par `usuário:senha` é credencial.
    """
    assert redact("Bearer abcdef0123456789abcdef") == f"Bearer {REDACTED}"
    # Com o rótulo `Authorization:` na frente, `assigned_secret` também casa e come a
    # palavra `Bearer` — comportamento histórico, preservado byte a byte pelo GATE 1 da E5.
    assert redact("Authorization: Bearer abcdef0123456789abcdef") == (
        f"Authorization: {REDACTED} {REDACTED}"
    )
    assert redact("https://reader:passcode@host.test") == f"https://{REDACTED}@host.test"
    assert redact("password: hunter2 e mais texto") == f"password: {REDACTED} e mais texto"
    assert redact("tokenizer é uma palavra legítima") == "tokenizer é uma palavra legítima"


def test_a_fronteira_de_palavra_continua_valendo_contra_texto_comum() -> None:
    """O que esta correção deliberadamente **não** mudou, dito em voz alta.

    O relaxamento é ancorado em segredo: uma ocorrência pode começar onde outra terminou.
    Um segredo colado a uma palavra comum (`tokenizerghp_…`) continua não sendo reconhecido
    — a fronteira de palavra existe justamente para não casar no meio de uma palavra, e
    relaxá-la em qualquer lugar transformaria o detector num casador de substring.

    Este teste afirma o comportamento atual em vez de deixá-lo implícito: se alguém decidir
    alargar o relaxamento, é aqui que a decisão fica visível.
    """
    colado_em_palavra = "tokenizer" + MARCADOR_GITHUB
    assert MARCADOR_GITHUB in redact(colado_em_palavra)


@pytest.mark.parametrize("marcador", TODOS_OS_MARCADORES)
def test_a_deteccao_converge(marcador: str) -> None:
    """`redact` é idempotente e determinística — o fecho de adjacência não oscila.

    O fecho repete até estabilizar. Um passo mal condicionado produziria saídas diferentes
    em execuções sucessivas, ou um laço infinito; este teste fecha as duas portas.
    """
    entrada = marcador * 4 + " texto " + marcador * 3
    uma_vez = redact(entrada)

    assert redact(uma_vez) == uma_vez
    assert redact(entrada) == uma_vez


# ====================================================================== E6-AUD4-002


def _placeholder(numero: int) -> str:
    return f"{REDACTED_KEY_PREFIX}{numero}{REDACTED_KEY_SUFFIX}"


@pytest.mark.parametrize("ordem", ["secreta primeiro", "literal primeiro"])
def test_placeholder_de_chave_nao_colide_com_nome_literal(ordem: str) -> None:
    """A reprodução exata: três entradas, duas ordens de inserção, nenhuma sobrescrita.

    Antes desta rodada o contador começava em 1 sem olhar o documento. Uma chave literal
    já chamada `«chave redigida 1»` recebia por cima o placeholder da primeira chave
    secreta, e num `dict` isso não é colisão visível: é um campo que **desaparece**.
    """
    literal = _placeholder(1)
    documento: dict[str, Any]
    if ordem == "secreta primeiro":
        documento = {
            MARCADOR_OPENAI: "valor secreto",
            literal: "valor público",
            MARCADOR_GITHUB: "outro valor secreto",
        }
    else:
        documento = {
            literal: "valor público",
            MARCADOR_OPENAI: "valor secreto",
            MARCADOR_GITHUB: "outro valor secreto",
        }

    saida = redact_document(documento)
    assert isinstance(saida, dict)

    assert len(saida) == 3, f"um campo desapareceu por colisão de placeholder: {saida!r}"
    assert sorted(saida.values()) == sorted(documento.values())
    assert literal in saida, "a chave literal foi sobrescrita pelo placeholder"
    assert saida[literal] == "valor público"
    assert MARCADOR_OPENAI not in str(saida)
    assert MARCADOR_GITHUB not in str(saida)


def test_duas_chaves_secretas_no_mesmo_dict_nunca_colidem() -> None:
    """O invariante que numerar existe para garantir, com reservas ocupando vários números."""
    documento = {
        _placeholder(1): "a",
        _placeholder(2): "b",
        _placeholder(3): "c",
        MARCADOR_OPENAI: "d",
        MARCADOR_GITHUB: "e",
        MARCADOR_AWS + "X" * 4: "f",
    }

    saida = redact_document(documento)
    assert isinstance(saida, dict)
    assert len(saida) == len(documento)
    assert sorted(saida.values()) == ["a", "b", "c", "d", "e", "f"]


def test_a_reserva_atravessa_a_profundidade() -> None:
    """Um placeholder literal num ramo reserva o número no documento **inteiro**.

    O contador é por documento — dois `«chave redigida 1»` em ramos diferentes pareceriam
    a mesma chave —, então a reserva tem de ter o mesmo alcance.
    """
    documento = {
        "ramo": [{"fundo": {_placeholder(1): "literal"}}],
        MARCADOR_OPENAI: "segredo",
    }

    saida = redact_document(documento)
    assert isinstance(saida, dict)
    assert _placeholder(1) not in saida, "o placeholder gerado colidiu com o reservado no ramo"
    assert _placeholder(2) in saida


# ====================================================================== E6-AUD4-003


@pytest.fixture
def app_com_sonda(temp_settings: AppSettings, migrated_url: str) -> FastAPI:
    """A aplicação real, com uma rota temporária que levanta `HTTPException`.

    A sonda precisa de uma `HTTPException` do framework cujo `detail` carregue texto — é o
    caminho que o handler embutido do Starlette servia com `JSONResponse` cru.
    """
    del migrated_url
    app = create_app(temp_settings)

    @app.get("/api/_sonda_aud4_003")
    def _sonda() -> None:
        raise HTTPException(status_code=418, detail=f"a chave é {MARCADOR_OPENAI}")

    return app


def test_http_exception_do_starlette_sai_redigida(app_com_sonda: FastAPI) -> None:
    """A reprodução: `HTTPException` com marcador no `detail` (E6-AUD4-003)."""
    with TestClient(app_com_sonda, base_url="http://127.0.0.1") as cliente:
        cliente.headers["Authorization"] = f"Bearer {app_com_sonda.state.session_token}"
        resposta = cliente.get("/api/_sonda_aud4_003")

    assert resposta.status_code == 418
    assert MARCADOR_OPENAI not in resposta.text, (
        "o detail saiu cru: a resposta não passou pelo boundary"
    )
    assert REDACTED in resposta.text
    assert resposta.json()["code"] == "http_error"


def test_o_roteamento_do_framework_continua_igual(auth_api_client: TestClient) -> None:
    """`404` de rota inexistente e `405` de método, com o `Allow` preservado.

    O handler novo cobre o caminho do framework inteiro; se ele engolisse `status_code` ou
    `headers`, quebraria o roteamento em vez de proteger a saída.
    """
    faltando = auth_api_client.get("/api/rota-que-nao-existe")
    assert faltando.status_code == 404
    assert faltando.json()["code"] == "http_error"

    metodo = auth_api_client.put("/api/health")
    assert metodo.status_code == 405
    assert "Allow" in metodo.headers, "o header Allow do 405 foi engolido pelo handler"
    assert "GET" in metodo.headers["Allow"]


# ====================================================================== E6-AUD4-004


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    context_helpers.init_repo(root)
    context_helpers.write(root, "src/app.py", "valor = 1\n")
    context_helpers.commit_all(root, "inicial")
    return root


@pytest.fixture
def workspace_id(auth_api_client: TestClient, repo: Path) -> str:
    resposta = auth_api_client.post(
        "/api/workspaces",
        json={"name": "ws", "type": "personal", "local_path": str(repo)},
    )
    assert resposta.status_code == 201, resposta.text
    return str(resposta.json()["id"])


@pytest.fixture
def entrada(auth_api_client: TestClient, workspace_id: str) -> dict[str, Any]:
    """Uma entrada cujo corpo carrega um segredo de verdade — logo, sai redigida."""
    resposta = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context",
        json={
            "domain": "objective",
            "title": "Objetivo",
            "body": f"a chave é {MARCADOR_OPENAI} e o resto do texto",
            "structured": {"campo": "valor"},
            "source_refs": [],
        },
    )
    assert resposta.status_code == 201, resposta.text
    return dict(resposta.json())


@pytest.mark.parametrize(
    "corpo",
    [
        pytest.param({"title": f"titulo com {REDACTED}"}, id="title"),
        pytest.param({"body": f"corpo com {REDACTED}"}, id="body"),
        pytest.param({"structured": {"campo": REDACTED}}, id="structured-valor"),
        pytest.param(
            {"structured": {"n1": {"n2": [{"n3": f"fundo {REDACTED}"}]}}},
            id="structured-aninhado",
        ),
        pytest.param({"structured": {_placeholder(1): "x"}}, id="structured-chave"),
        pytest.param({"tags": [f"tag {REDACTED}"]}, id="tags"),
    ],
)
def test_patch_recusa_marcador_de_redacao(
    auth_api_client: TestClient, entrada: dict[str, Any], corpo: dict[str, Any]
) -> None:
    """A reprodução: um `PATCH` que devolve texto redigido é recusado, e nada é persistido.

    Sem a recusa, `«redigido»` substituiria para sempre o trecho que ele escondia — e o
    `content_hash` passaria a atestar o texto mutilado.
    """
    resposta = auth_api_client.patch(
        f"/api/context/{entrada['id']}",
        json={"expected_edit_hash": _edit_hash(auth_api_client, entrada["id"]), **corpo},
    )

    assert resposta.status_code == 422, resposta.text
    assert resposta.json()["code"] == "redacted_content_rejected"

    depois = auth_api_client.post(f"/api/context/{entrada['id']}/edit-view").json()
    assert depois["content_hash"] == entrada["content_hash"], "algo foi persistido apesar do 422"


def test_edit_view_devolve_conteudo_cru_e_no_store(
    auth_api_client: TestClient, workspace_id: str, entrada: dict[str, Any]
) -> None:
    """A rota nova: conteúdo **como está gravado**, com `Cache-Control: no-store`."""
    projecao = auth_api_client.get(f"/api/workspaces/{workspace_id}/context").json()[0]
    assert MARCADOR_OPENAI not in projecao["body"], "a projeção de leitura deixou de redigir"
    assert REDACTED in projecao["body"]

    resposta = auth_api_client.post(f"/api/context/{entrada['id']}/edit-view")

    assert resposta.status_code == 200, resposta.text
    assert resposta.headers["Cache-Control"] == "no-store"

    corpo = resposta.json()
    assert corpo["body"] == f"a chave é {MARCADOR_OPENAI} e o resto do texto"
    assert corpo["content_hash"] == entrada["content_hash"]
    assert set(corpo) == {"entry_id", "title", "body", "structured", "content_hash", "edit_hash"}


def test_edit_view_nao_deixa_rastro_do_conteudo(
    auth_api_client: TestClient,
    temp_settings: AppSettings,
    entrada: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """O conteúdo cru não vai para log, `SafetyEvent` nem qualquer outro canal.

    A verificação é sobre **o que foi escrito**, não sobre a ausência de erro: a suíte lê o
    log capturado e a tabela de eventos depois da chamada.
    """
    with caplog.at_level(0):
        resposta = auth_api_client.post(f"/api/context/{entrada['id']}/edit-view")
    assert resposta.status_code == 200

    assert MARCADOR_OPENAI not in caplog.text, "o conteúdo cru apareceu em log"

    engine = create_engine(temp_settings)
    with create_session_factory(engine)() as sessao:
        eventos = sessao.execute(select(SafetyEvent)).scalars().all()
        assert not [
            evento
            for evento in eventos
            if MARCADOR_OPENAI in f"{evento.subject} {evento.detail} {evento.rule_id}"
        ], "o conteúdo cru virou SafetyEvent"
    engine.dispose()


def test_patch_com_hash_divergente_e_409_sem_sobrescrever(
    auth_api_client: TestClient, entrada: dict[str, Any]
) -> None:
    """Concorrência otimista, com edição concorrente **real** no meio (E6-AUD4-004).

    Nada é travado: a entrada segue editável o tempo todo. O que o `expected_edit_hash`
    detecta é que ela mudou desde a leitura — e a segunda gravação para em vez de apagar a
    primeira.
    """
    leitura = auth_api_client.post(f"/api/context/{entrada['id']}/edit-view").json()
    hash_lido = leitura["edit_hash"]

    # Alguém escreve no meio, com o hash correto.
    concorrente = auth_api_client.patch(
        f"/api/context/{entrada['id']}",
        json={"expected_edit_hash": hash_lido, "body": "corpo escrito pelo outro editor"},
    )
    assert concorrente.status_code == 200, concorrente.text

    conflito = auth_api_client.patch(
        f"/api/context/{entrada['id']}",
        json={"expected_edit_hash": hash_lido, "body": "corpo que chegaria por cima"},
    )

    assert conflito.status_code == 409, conflito.text
    assert conflito.json()["code"] == "context_changed"

    atual = auth_api_client.post(f"/api/context/{entrada['id']}/edit-view").json()
    assert atual["body"] == "corpo escrito pelo outro editor", "a escrita do outro foi apagada"


def test_patch_com_hash_correto_segue_normal(
    auth_api_client: TestClient, entrada: dict[str, Any]
) -> None:
    """O caminho feliz: hash em dia, edição aplicada, hash novo devolvido."""
    resposta = auth_api_client.patch(
        f"/api/context/{entrada['id']}",
        json={
            "expected_edit_hash": _edit_hash(auth_api_client, entrada["id"]),
            "body": "corpo revisado",
        },
    )

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["body"] == "corpo revisado"
    assert corpo["content_hash"] != entrada["content_hash"]


def test_patch_sem_expected_edit_hash_e_422(
    auth_api_client: TestClient, entrada: dict[str, Any]
) -> None:
    """Obrigatório, não opcional: um cliente que não o mande não escreve.

    Opcional seria a mesma inversão de modelo que E6-AUD3-001 encontrou na redação — a
    garantia existiria só para quem lembrasse de pedi-la.
    """
    resposta = auth_api_client.patch(
        f"/api/context/{entrada['id']}", json={"body": "corpo sem hash"}
    )

    assert resposta.status_code == 422, resposta.text
    assert resposta.json()["code"] == "validation_error"


def test_a_recusa_vale_para_toda_escrita_do_registry(
    auth_api_client: TestClient, workspace_id: str
) -> None:
    """ "PATCH **e qualquer outro endpoint de escrita do registry**" — `POST` e o seed.

    A guarda mora nos limpadores do serviço, e as três escritas passam por eles. Pô-la só
    no `PATCH` deixaria as irmãs abertas, que é o padrão que as rodadas 1 a 3 desta
    auditoria encontraram três vezes.
    """
    criacao = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context",
        json={
            "domain": "objective",
            "title": "Objetivo",
            "body": f"corpo com {REDACTED}",
        },
    )
    assert criacao.status_code == 422, criacao.text
    assert criacao.json()["code"] == "redacted_content_rejected"

    importacao = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context/import",
        json={"problem": f"problema com {REDACTED}", "objective": "objetivo"},
    )
    assert importacao.status_code == 422, importacao.text
    assert importacao.json()["code"] == "redacted_content_rejected"

    assert auth_api_client.get(f"/api/workspaces/{workspace_id}/context").json() == []


@contextmanager
def _escritor_intermediario(
    factory: sessionmaker[Session], workspace: str, prefixo: str
) -> Iterator[tuple[list[str], list[str]]]:
    """Outra conexão commita entre a leitura e a escrita da requisição em curso.

    Reproduz o `SQLITE_BUSY_SNAPSHOT` de verdade, sem simular exceção: é o mesmo
    instrumento que a auditoria usou, trazido para cá porque a sonda dela deixou de
    compilar contra o contrato novo (ela não manda `expected_edit_hash`).
    """
    observado: list[str] = []
    erros: list[str] = []

    def antes(conn, cursor, statement, params, ctx, many) -> None:  # type: ignore[no-untyped-def]
        if observado or not statement.lower().startswith(prefixo):
            return
        observado.append(statement)
        with session_scope(factory) as outra:
            vencedora = outra.get(DevWorkspace, workspace)
            assert vencedora is not None
            vencedora.name = "vencedora durável"

    def no_erro(ctx) -> None:  # type: ignore[no-untyped-def]
        nome = getattr(ctx.original_exception, "sqlite_errorname", None)
        if nome:
            erros.append(str(nome))

    event.listen(Engine, "before_cursor_execute", antes)
    event.listen(Engine, "handle_error", no_erro)
    try:
        yield observado, erros
    finally:
        event.remove(Engine, "before_cursor_execute", antes)
        event.remove(Engine, "handle_error", no_erro)


def test_a_corrida_de_escrita_do_registry_continua_virando_409(
    auth_api_client: TestClient,
    session_factory: sessionmaker[Session],
    workspace_id: str,
    entrada: dict[str, Any],
) -> None:
    """O `write_conflict` do `PATCH` de contexto, agora **com** o hash obrigatório.

    Exigir `expected_edit_hash` invalidou a sonda da rodada 4 que cobria este caminho
    (ela mandava só `body`). A cobertura não pode sumir junto com a sonda: aqui o conflito
    é provocado por uma segunda conexão real, com o hash correto no corpo, e o resultado
    tem de continuar sendo `409 write_conflict` — não `422`, e não `500`.
    """
    auth_api_client._transport.raise_server_exceptions = False  # type: ignore[attr-defined]

    with _escritor_intermediario(
        session_factory, workspace_id, "update context_registry_entry"
    ) as (visto, erros):
        conflito = auth_api_client.patch(
            f"/api/context/{entrada['id']}",
            json={
                "expected_edit_hash": _edit_hash(auth_api_client, entrada["id"]),
                "body": "corpo que perde a corrida",
            },
        )

    assert visto, "a escrita do registry não chegou a ser tentada"
    assert "SQLITE_BUSY_SNAPSHOT" in erros, f"a corrida não aconteceu de verdade: {erros}"
    assert conflito.status_code == 409, conflito.text
    assert conflito.json()["code"] == "write_conflict"

    with session_scope(session_factory) as sessao:
        entry = sessao.get(ContextRegistryEntry, entrada["id"])
        assert entry is not None
        assert entry.body != "corpo que perde a corrida", "a perdedora gravou mesmo assim"
        workspace = sessao.get(DevWorkspace, workspace_id)
        assert workspace is not None
        assert workspace.name == "vencedora durável"

    retry = auth_api_client.patch(
        f"/api/context/{entrada['id']}",
        json={
            "expected_edit_hash": _edit_hash(auth_api_client, entrada["id"]),
            "body": "corpo do retry",
        },
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["body"] == "corpo do retry"


def test_o_reconhecedor_de_marcador_tem_dono_unico() -> None:
    """`contains_redaction_marker` reconhece as duas formas, e só elas."""
    assert contains_redaction_marker(f"texto com {REDACTED} no meio")
    assert contains_redaction_marker(f"texto com {_placeholder(7)} no meio")
    assert not contains_redaction_marker("texto comum sem marcador nenhum")
    assert not contains_redaction_marker(REDACTED_KEY_PREFIX + "sem número" + REDACTED_KEY_SUFFIX)
