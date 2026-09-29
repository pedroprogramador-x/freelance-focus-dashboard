"""`LocalSessionToken`, autenticação da API e bootstrap pelo HTML.

Regressão de **E2-AUD-001**: a arquitetura congelada prevê o token e a proteção da API
para a E2, e a implementação anterior não os tinha — além de expor `/api/openapi.json`
como uma segunda superfície aberta.
"""

from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.security import (
    ALLOWED_HOSTNAMES,
    PUBLIC_API_ENDPOINTS,
    SESSION_TOKEN_META_NAME,
    extract_bearer_token,
    generate_session_token,
    host_is_local,
    is_api_path,
    requires_session_token,
    token_is_valid,
)
from app.api.web import render_bootstrap_html
from app.config import AppSettings
from app.main import create_app
from tests.conftest import LOCAL_BASE_URL

APP_ROOT = Path(__file__).resolve().parents[1] / "app"


# ------------------------------------------------------------------ o token


def test_token_e_forte_e_nao_vazio() -> None:
    token = generate_session_token()

    assert token
    # `token_urlsafe(32)` são 256 bits; em base64url isso passa de 40 caracteres.
    assert len(token) >= 40


def test_apps_distintos_recebem_tokens_distintos(temp_settings: AppSettings) -> None:
    """Rotação a cada reinício ([06] §1): dois processos nunca compartilham token."""
    first = create_app(temp_settings)
    second = create_app(temp_settings)

    assert first.state.session_token != second.state.session_token


def test_token_nao_e_persistido_em_disco(temp_settings: AppSettings, tmp_path: Path) -> None:
    app = create_app(temp_settings)
    token = app.state.session_token

    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert token not in path.read_text(encoding="utf-8", errors="ignore")


# ------------------------------------------------------------- autenticação


def test_health_e_publico(client: TestClient) -> None:
    assert client.get("/api/health").status_code == 200


def test_only_get_health_is_public(client: TestClient) -> None:
    """A exceção é o par **(método, caminho)**, não o caminho sozinho.

    Guardar só `/api/health` deixava `POST /api/health` chegar ao router sem credencial.
    """
    assert client.get("/api/health").status_code == 200

    for method in ("post", "put", "patch", "delete", "options", "head"):
        response = getattr(client, method)("/api/health")
        assert response.status_code == 401, f"{method.upper()} /api/health ficou público"


def test_metodo_nao_publico_com_token_chega_ao_router(auth_client: TestClient) -> None:
    """Com credencial, o 405 vem do roteamento — prova que a auth roda **antes** dele."""
    response = auth_client.post("/api/health")

    assert response.status_code == 405


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/api/health", False),
        ("get", "/api/health", False),
        ("POST", "/api/health", True),
        ("HEAD", "/api/health", True),
        ("OPTIONS", "/api/health", True),
        ("GET", "/api/workspaces", True),
        ("GET", "/", False),
    ],
)
def test_regra_de_exigencia_de_token(method: str, path: str, expected: bool) -> None:
    assert requires_session_token(method, path) is expected


def test_rota_api_sem_token_devolve_401(client: TestClient) -> None:
    """Vale inclusive para caminho inexistente: o guarda roda **antes** do roteamento.

    Isso é deliberado — responder 404 sem credencial permitiria enumerar quais rotas
    existem.
    """
    response = client.get("/api/workspaces")

    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_token_invalido_devolve_401(client: TestClient) -> None:
    response = client.get("/api/workspaces", headers={"Authorization": "Bearer nao-e-o-token"})

    assert response.status_code == 401


@pytest.mark.parametrize(
    "header",
    ["", "Bearer", "Bearer ", "Basic abc", "token abc", "abcdef"],
)
def test_authorization_malformado_devolve_401(client: TestClient, header: str) -> None:
    response = client.get("/api/workspaces", headers={"Authorization": header})

    assert response.status_code == 401


def test_token_valido_passa_pelo_guarda(auth_client: TestClient) -> None:
    """Com credencial válida o 401 some; o 404 é do roteamento, não da autenticação."""
    assert auth_client.get("/api/rota-que-nao-existe").status_code == 404
    assert auth_client.get("/api/health").status_code == 200


def test_token_de_outro_app_nao_serve(client: TestClient, temp_settings: AppSettings) -> None:
    outro = create_app(temp_settings)

    response = client.get(
        "/api/workspaces",
        headers={"Authorization": f"Bearer {outro.state.session_token}"},
    )

    assert response.status_code == 401


def test_token_nunca_vai_na_query_string(client: TestClient, session_token: str) -> None:
    """Nenhuma rota aceita o token fora do header ([06] §1)."""
    response = client.get(f"/api/workspaces?token={session_token}")

    assert response.status_code == 401


# --------------------------------------------------------- host validation


def test_host_nao_loopback_e_recusado(temp_settings: AppSettings) -> None:
    """Defesa contra DNS rebinding ([06] §1, defesa 2)."""
    with TestClient(create_app(temp_settings), base_url="http://exemplo.invalido") as evil:
        response = evil.get("/api/health")

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_host"


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost:8000",
        "127.0.0.1",
        "127.0.0.1:8000",
        "[::1]",
        "[::1]:8000",
        "LOCALHOST:8756",
        "localhost:1",
        "localhost:65535",
    ],
)
def test_hosts_de_loopback_sao_aceitos(host: str) -> None:
    assert host_is_local(host)


@pytest.mark.parametrize(
    "host",
    [
        None,
        "",
        # Externos comuns
        "exemplo.com",
        "evil.localhost.attacker.com",
        "127.0.0.1.evil.com",
        "localhost.evil.com",
        # Malformados que a versão por prefixo aceitava (E2.2)
        "[::1]evil.com",
        "[::1]evil",
        "[::1]evil:8000",
        "[::1]:abc",
        "[::1]:8000evil",
        "[::1]:",
        "localhost:abc",
        "localhost:",
        "localhost:-1",
        "localhost:+8000",
        "localhost:8000evil",
        "localhost:80:90",
        "127.0.0.1:abc",
        "127.0.0.1:",
        "127.0.0.1:0x50",
        # Fora do intervalo e com espaços
        "localhost:0",
        "localhost:65536",
        "localhost:99999",
        " localhost",
        "localhost ",
        "localhost: 8000",
        "localhost\n",
        "localhost\r",
        "localhost\t",
        # Dígitos Unicode: `int()` os aceitaria; `[0-9]` não.
        "localhost:８０",  # noqa: RUF001 — dígitos fullwidth de propósito
        "::1",
        "[::1",
        "::1]",
    ],
)
def test_malformed_loopback_hosts_are_rejected(host: str | None) -> None:
    """Casamento **total**, nunca por prefixo: a string inteira precisa ser consumida."""
    assert not host_is_local(host)


@pytest.mark.parametrize("host", ["[::1]evil.com", "localhost:abc", "127.0.0.1:8000evil"])
def test_host_malformado_e_recusado_no_http_real(temp_settings: AppSettings, host: str) -> None:
    """Não basta o helper puro: o guarda precisa recusar a requisição de verdade."""
    with TestClient(create_app(temp_settings), base_url=LOCAL_BASE_URL) as local:
        response = local.get("/api/health", headers={"Host": host})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_host"


# ---------------------------------------------------- superfície pública


def test_health_e_a_unica_rota_api_publica() -> None:
    assert set(PUBLIC_API_ENDPOINTS) == {("GET", "/api/health")}


@pytest.mark.parametrize(
    "path", ["/api/openapi.json", "/openapi.json", "/docs", "/redoc", "/api/docs"]
)
def test_openapi_e_docs_nao_estao_expostos(client: TestClient, path: str) -> None:
    """OpenAPI seria uma segunda superfície pública; a arquitetura admite exatamente uma."""
    response = client.get(path)

    assert response.status_code in (401, 404)
    assert "paths" not in response.text


def test_enumeracao_de_rotas_prova_a_superficie(client: TestClient) -> None:
    """§28: cada rota registrada é pública **por decisão**, não por esquecimento.

    Se alguém acrescentar uma rota `/api/*` sem entrada na allowlist, ela nasce protegida
    e este teste continua verde. Se acrescentar uma rota pública fora de `/api`, este
    teste falha e obriga a decisão a ser explícita.
    """
    app = client.app
    paths = {route.path for route in app.routes if hasattr(route, "path")}  # type: ignore[attr-defined]

    api_paths = {path for path in paths if is_api_path(path)}
    assert "/api/health" in api_paths
    assert "/api/workspaces" in api_paths  # E3 acrescentou o Workspace Registry

    non_api_paths = {path for path in paths if not is_api_path(path)}
    assert non_api_paths == {"/"}, "rota pública fora de /api precisa ser decisão explícita"

    assert "/api/session/bootstrap" not in paths
    for path in api_paths:
        publico = ("GET", path) in PUBLIC_API_ENDPOINTS
        assert requires_session_token("GET", path) is not publico

    # Mesmo com rotas novas, `GET /api/health` continua sendo a única pública.
    assert {path for path in api_paths if not requires_session_token("GET", path)} == {
        "/api/health"
    }


def test_qualquer_rota_api_nova_nasce_protegida() -> None:
    """A regra é por prefixo, não rota a rota."""
    assert requires_session_token("GET", "/api/workspaces")
    assert requires_session_token("GET", "/api/tasks/123/plan")
    assert requires_session_token("GET", "/api")
    assert not requires_session_token("GET", "/api/health")
    assert not requires_session_token("GET", "/")
    assert not requires_session_token("GET", "/apidocs")


# ------------------------------------------------------- não vazamento


def test_token_nao_aparece_na_saude(client: TestClient, session_token: str) -> None:
    response = client.get("/api/health")

    assert session_token not in response.text
    assert set(response.json()) == {"status", "version"}


def test_token_nao_aparece_em_erro_de_autenticacao(client: TestClient, session_token: str) -> None:
    response = client.get("/api/workspaces", headers={"Authorization": "Bearer errado"})

    assert session_token not in response.text
    assert "errado" not in response.text, "o erro não pode ecoar o que foi apresentado"


def _print_aliases(tree: ast.Module) -> frozenset[str]:
    """Nomes locais que um `import` ligou a `print`.

    `from builtins import print as escrever` faz `escrever(...)` ser `print(...)` com outro
    nome. Sem resolver o alias, o detector só reconheceria a grafia literal — e a grafia
    literal é justamente a que quem quer contornar o teste não usaria.
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "builtins":
            aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == "print"
            )
    return frozenset(aliases)


def _calls_print(func: ast.expr, aliases: frozenset[str]) -> bool:
    """Esta expressão chamada escreve em stdout?

    Duas formas, e a segunda é o que E6-AUD-006 encontrou faltando:

    * `ast.Name` — `print(...)`, e qualquer alias de import resolvido para ele;
    * `ast.Attribute` com `attr == "print"` — `builtins.print(...)`, `b.print(...)` depois
      de `import builtins as b`, e qualquer outro receptor.

    O segundo caso é deliberadamente amplo: **não** se tenta provar que o receptor é o
    módulo `builtins`. Um `.print(...)` de qualquer objeto — um `Console` de biblioteca, um
    logger que alguém chamou assim — escreve na saída do mesmo jeito, que é a propriedade
    que este teste protege. A assimetria de custo decide: um falso positivo custa uma
    conversa e um `noqa` deliberado; um falso negativo custa um token em log.

    Continua **não** sendo busca por substring: `_recompute_fingerprint(` não casa nem como
    `Name` nem como `Attribute`, que era o falso positivo que motivou a troca.
    """
    if isinstance(func, ast.Name):
        return func.id == "print" or func.id in aliases
    return isinstance(func, ast.Attribute) and func.attr == "print"


def _print_calls(path: Path) -> list[int]:
    """Linhas em que `print(...)` é de fato **chamado**. Por AST, não por substring.

    A verificação era `"print(" not in source`, e ela passou a acusar falso positivo na E6:
    `_recompute_fingerprint(` **contém** `print(` como substring. O termo "fingerprint" é
    central em [02] §7 e aparece em dezenas de identificadores do Orchestrator, então
    contornar o detector renomeando a função seria deformar o código para caber num teste
    quebrado.

    Mesma correção — e mesma justificativa — de
    `test_architecture.test_expansor_e_o_unico_a_decidir_gramatica_dentro_do_safety`: "a
    verificação é por AST, não por substring", porque o módulo *fala* sobre o que não pode
    fazer.

    **E6-AUD-006:** a primeira versão só reconhecia `ast.Name`, e por isso deixou de pegar
    `builtins.print(...)` — que a busca por substring antiga pegava. A afirmação "mais
    estrito" só passou a ser verdadeira com `_calls_print`, que cobre as duas formas.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    aliases = _print_aliases(tree)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _calls_print(node.func, aliases)
    ]


def test_backend_nao_imprime_nada() -> None:
    """`print` é o caminho mais curto para um token acabar em log."""
    for path in APP_ROOT.rglob("*.py"):
        linhas = _print_calls(path)
        assert not linhas, f"{path.name} chama print() na(s) linha(s) {linhas}"


#: Cada caso é `(fonte, o detector antigo por substring pegava?, deve imprimir "probe")`.
#: As três primeiras linhas são literalmente a tabela de E6-AUD-006; as duas últimas cobrem
#: o alias de import e o nome que só *parece* um print.
_CASOS_DE_PRINT: tuple[tuple[str, bool, bool], ...] = (
    ('print("probe")', True, True),
    ('print ("probe")', False, True),
    ('import builtins\nbuiltins.print("probe")', True, True),
    ('from builtins import print as escrever\nescrever("probe")', False, True),
    ("def _recompute_fingerprint(x):\n    return x\n", True, False),
)


@pytest.mark.parametrize(("fonte", "substring_pegava", "imprime"), _CASOS_DE_PRINT)
def test_o_detector_de_print_reconhece_toda_forma_que_de_fato_imprime(
    tmp_path: Path, fonte: str, substring_pegava: bool, imprime: bool
) -> None:
    """Contrafactual do detector, pelo **próprio** `_print_calls`.

    O caso anterior reimplementava o predicado dentro do teste, e foi por isso que ele
    continuou verde enquanto o detector real perdia `builtins.print` (E6-AUD-006): o teste
    não exercitava o código que estava errado. Aqui o arquivo é escrito em disco e passa
    pela mesma função que a suíte usa contra `app/`.

    Cada caso também é **executado**, e a saída conferida: `imprime` não é uma anotação do
    autor sobre o que a linha deveria fazer, é o que o Python de fato faz com ela. É o que
    impede a tabela de afirmar uma premissa falsa sobre o próprio contrafactual.
    """
    arquivo = tmp_path / "caso.py"
    arquivo.write_text(fonte, encoding="utf-8")

    saida = io.StringIO()
    with contextlib.redirect_stdout(saida):
        exec(compile(fonte, "<caso>", "exec"), {})  # noqa: S102 — fonte literal desta tabela
    assert (saida.getvalue() == "probe\n") is imprime, "a premissa do caso está errada"

    assert bool(_print_calls(arquivo)) is imprime, (
        f"o detector discorda do que a fonte realmente faz (substring antiga: "
        f"{substring_pegava}); {fonte!r}"
    )


# -------------------------------------------------------------- bootstrap


def test_html_injeta_o_token_com_no_store(temp_settings: AppSettings, web_dist: Path) -> None:
    settings = temp_settings.model_copy(update={"web_dist_dir": web_dist})

    with TestClient(create_app(settings), base_url=LOCAL_BASE_URL) as local:
        token = local.app.state.session_token  # type: ignore[attr-defined]
        response = local.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert f'name="{SESSION_TOKEN_META_NAME}"' in response.text
    assert token in response.text


def test_sem_build_a_rota_nao_inventa_html(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 404
    assert response.json()["code"] == "web_ui_unavailable"


def test_bootstrap_nao_e_uma_rota_de_api(client: TestClient) -> None:
    """O HTML é a exceção arquitetural; ela não pode virar um endpoint JSON de token."""
    assert client.get("/api/session/bootstrap").status_code == 401


def test_render_substitui_meta_preexistente() -> None:
    """Um build antigo com token obsoleto não pode conviver com o atual."""
    template = (
        f'<html><head><meta name="{SESSION_TOKEN_META_NAME}" content="antigo">'
        "</head><body></body></html>"
    )

    rendered = render_bootstrap_html(template, "novo")

    assert "antigo" not in rendered
    assert rendered.count(f'name="{SESSION_TOKEN_META_NAME}"') == 1
    assert 'content="novo"' in rendered


def test_render_escapa_o_valor() -> None:
    rendered = render_bootstrap_html("<html><head></head></html>", 'a"><script>x</script>')

    assert "<script>" not in rendered
    assert "&quot;" in rendered or "&gt;" in rendered


def test_render_funciona_sem_head() -> None:
    rendered = render_bootstrap_html("<html><body>x</body></html>", "tok")

    assert SESSION_TOKEN_META_NAME in rendered
    assert "<body>x</body>" in rendered


# ------------------------------------------------------------ utilitários


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),
        ("BEARER   abc  ", "abc"),
        ("Bearer", None),
        ("Basic abc", None),
        ("", None),
        (None, None),
    ],
)
def test_extracao_do_bearer(header: str | None, expected: str | None) -> None:
    assert extract_bearer_token(header) == expected


def test_comparacao_de_token_rejeita_vazio_e_diferente() -> None:
    assert token_is_valid("abc", "abc")
    assert not token_is_valid("abcd", "abc")
    assert not token_is_valid("", "abc")
    assert not token_is_valid(None, "abc")


def test_allowlist_de_host_e_apenas_loopback() -> None:
    """`::1` sem colchetes sai da lista: num `Host`, literal IPv6 é sempre `[::1]`.

    Aceitar a forma nua tornaria `::1:8000` ambíguo — não dá para dizer onde termina o
    endereço e começa a porta.
    """
    assert set(ALLOWED_HOSTNAMES) == {"127.0.0.1", "localhost", "[::1]"}
