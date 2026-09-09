"""Reproduções da **4ª auditoria Codex** da E4 — E4-AUD4-001 e E4-AUD4-002.

## Os dois findings, e a causa única

`_run_git` chamava `subprocess.run(..., text=True, encoding="utf-8", errors="strict")`.
Isso liga o **modo texto** do `subprocess`, e o modo texto faz duas coisas implícitas que
nada no resto do módulo controlava:

1. **AUD4-001 — tradução automática de quebra de linha.** O modo texto do Python aplica
   *universal newlines* na decodificação: `\r\n` e `\r` viram `\n`, mesmo dentro de um nome
   de arquivo que os contenha como bytes **literais**, e mesmo com `-z` (que só protege
   contra *C-quoting*, não contra a tradução de newline — são dois mecanismos diferentes
   dentro do `io.TextIOWrapper`). Dois blobs distintos, `line\rname.py` e `line\nname.py`,
   viravam o mesmo texto depois da tradução.

2. **AUD4-002 — decodificação que escapa do `except` de `_run_git`.** Com
   `capture_output=True` e modo texto, a decodificação acontece dentro da máquina interna
   de `Popen.communicate()` — em algumas condições (notavelmente no Windows, onde
   `communicate()` sempre lê `stdout`/`stderr` em threads separadas) a decodificação roda
   **dentro da thread leitora**, e uma falha ali não é o `UnicodeDecodeError` limpo que o
   `except (OSError, ValueError, subprocess.SubprocessError)` de `_run_git` está preparado
   para pegar — é um erro de estado interno (a thread morre sem preencher o buffer que
   `communicate()` depois indexa). Byte inválido em qualquer lugar da árvore virava
   exceção não tratada e 500, não um resultado neutro.

## A correção

`_run_git` não passa mais `text=True` (nem `encoding=`/`errors=`, que também ligam o modo
texto implicitamente) — `stdout`/`stderr` saem como bytes crus, sem tradução de newline e
sem decodificação escondida em nenhuma thread. A conversão para texto é sempre um passo
explícito **deste módulo**, via `_decode_text`, chamado dentro de um `try` comum que este
módulo já controla.

Separação de registro (`\0`) e de campo (espaço, tab) passou a acontecer em **bytes**,
antes de qualquer decodificação — os delimitadores são bytes ASCII fixos que o git usa,
localizá-los não exige que o conteúdo entre eles seja UTF-8 válido. Só o **último** campo
de cada registro (o caminho) pode falhar ao decodificar; os anteriores (`XY`, modo, hash)
são sempre ASCII, e uma falha ali é violação de formato (mesmo tratamento de sempre — o
registro inteiro é rejeitado), não nome de arquivo. Um caminho que não decodifica entra em
``unrepresentable`` e o laço **continua** para os próximos registros — o registro
problemático é isolado, não interrompe a leitura dos demais.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.git_runtime as git_runtime
from app.context_engine.source_ref_expansion import compute_source_hash
from app.git_runtime import list_tree
from tests.context_helpers import GIT, init_repo, readable
from tests.test_api_context import SAME_ORIGIN, _create, _workspace

pytestmark = pytest.mark.skipif(GIT is None, reason="git indisponível no PATH")


def _git_out(cwd: Path, *args: str, stdin: bytes | None = None) -> str:
    """`git` de montagem de cenário que devolve o stdout — mesmo helper do round3."""
    assert GIT is not None
    result = subprocess.run(  # noqa: S603 — git de teste, argv literal, sem shell
        [GIT, *args],
        cwd=cwd,
        capture_output=True,
        input=stdin,
        check=True,
    )
    return result.stdout.decode("utf-8").strip()


def _mktree_record(sha_hex: str, name: bytes) -> bytes:
    """Um registro de `git mktree -z`: `<mode> blob <sha>\\t<nome>\\0`, `name` em bytes crus.

    Existe porque um nome com bytes que não são UTF-8 válido (E4-AUD4-002) não pode passar
    por uma f-string — a montagem do registro precisa acontecer inteiramente em bytes.
    """
    return b"100644 blob " + sha_hex.encode("ascii") + b"\t" + name + b"\x00"


def _commit_with_blobs(root: Path, names_and_content: list[tuple[bytes, bytes]]) -> str:
    """Cria um blob por `(nome, conteúdo)`, monta a árvore e o commit. Devolve o commit."""
    records = b""
    for name, content in names_and_content:
        sha = _git_out(root, "hash-object", "-w", "--stdin", stdin=content)
        records += _mktree_record(sha, name)
    tree = _git_out(root, "mktree", "-z", stdin=records)
    commit = _git_out(root, "commit-tree", tree, "-m", "cenario da 4a auditoria")
    _git_out(root, "update-ref", "HEAD", commit)
    return commit


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    init_repo(root)
    return root


# ========================================== E4-AUD4-001 — CR e LF literais não colidem


def test_aud4_001_cr_e_lf_no_nome_sao_identidades_diferentes(repo: Path) -> None:
    """Reprodução exata: `line\\rname.py` (CR) e `line\\nname.py` (LF) — dois commits reais.

    Mesmo conteúdo nos dois blobs, de propósito: isola que a diferença de `source_hash`
    vem do **caminho**, não do conteúdo. Com `text=True`, o modo texto do Python traduzia
    os dois nomes no mesmo texto (`\\r` → `\\n`), e as duas identidades colidiam.
    """
    conteudo = b"mesmo conteudo nos dois\n"

    commit_cr = _commit_with_blobs(repo, [(b"line\rname.py", conteudo)])
    tree_cr = readable(list_tree(str(repo), commit_cr))

    commit_lf = _commit_with_blobs(repo, [(b"line\nname.py", conteudo)])
    tree_lf = readable(list_tree(str(repo), commit_lf))

    assert [path for path, _sha in tree_cr] == ["line\rname.py"]
    assert [path for path, _sha in tree_lf] == ["line\nname.py"]
    assert tree_cr != tree_lf

    hash_cr = compute_source_hash(tuple(tree_cr))
    hash_lf = compute_source_hash(tuple(tree_lf))
    assert hash_cr != hash_lf


def test_aud4_001_crlf_no_nome_e_uma_terceira_identidade(repo: Path) -> None:
    """Variação CRLF: `line\\r\\nname.py` não colide com nenhuma das duas anteriores.

    O modo texto colapsava `\\r\\n` numa única tradução para `\\n` — dois bytes de nome
    virando um. As três formas (`\\r`, `\\n`, `\\r\\n`) precisam ser três identidades.
    """
    conteudo = b"mesmo conteudo nos tres\n"
    nomes = [b"line\rname.py", b"line\nname.py", b"line\r\nname.py"]

    hashes = set()
    caminhos = set()
    for nome in nomes:
        commit = _commit_with_blobs(repo, [(nome, conteudo)])
        tree = readable(list_tree(str(repo), commit))
        assert len(tree) == 1
        caminhos.add(tree[0][0])
        hashes.add(compute_source_hash(tuple(tree)))

    assert caminhos == {"line\rname.py", "line\nname.py", "line\r\nname.py"}
    assert len(hashes) == 3, "as tres formas de quebra de linha colidiram no mesmo hash"


def test_aud4_001_as_tres_formas_convivem_na_mesma_arvore(repo: Path) -> None:
    """As três variações, **no mesmo commit** — nenhuma sobrescreve as outras via dedupe.

    `list_tree` recusa (`None`) um caminho duplicado depois da redução ao workspace — se a
    tradução de newline ainda estivesse ativa, as três colidiriam no mesmo texto e essa
    trava dispararia, escondendo o defeito atrás de um `unknown` genérico em vez de expor a
    colisão de identidade.
    """
    commit = _commit_with_blobs(
        repo,
        [
            (b"line\rname.py", b"a\n"),
            (b"line\nname.py", b"b\n"),
            (b"line\r\nname.py", b"c\n"),
        ],
    )

    tree = readable(list_tree(str(repo), commit))

    assert sorted(path for path, _sha in tree) == [
        "line\nname.py",
        "line\r\nname.py",
        "line\rname.py",
    ]


# ================================================== E4-AUD4-002 — byte inválido, sem 500


def test_aud4_002_byte_invalido_nao_lanca_e_produz_resultado_neutro(repo: Path) -> None:
    """Reprodução exata: um blob com `0xFF` no nome. `list_tree` não lança, não é `None` cru.

    `0xFF` sozinho nunca é um byte inicial válido de UTF-8 — decodificação falha sempre.
    """
    nome_invalido = b"bad" + bytes([0xFF]) + b".py"
    commit = _commit_with_blobs(repo, [(nome_invalido, b"conteudo\n")])

    resultado = list_tree(str(repo), commit)  # não deve lançar

    assert resultado is not None
    assert resultado.unrepresentable  # o motivo não fica vazio


def test_aud4_002_registro_invalido_nao_interrompe_os_demais(repo: Path) -> None:
    """**Dois** registros inválidos no mesmo commit — os dois aparecem, não só o primeiro.

    Prova que o laço de `list_tree` isola o registro problemático e **continua** (E4-AUD4),
    em vez de abortar no primeiro byte ruim — o mesmo princípio de "registro parcial nunca
    vira `None` silencioso" que já valia para formato, agora também para decodificação.
    """
    commit = _commit_with_blobs(
        repo,
        [
            (b"ok1.py", b"1\n"),
            (b"bad" + bytes([0xFF]) + b"-um.py", b"2\n"),
            (b"ok2.py", b"3\n"),
            (b"bad" + bytes([0xFE]) + b"-dois.py", b"4\n"),
        ],
    )

    resultado = list_tree(str(repo), commit)

    assert resultado is not None
    assert len(resultado.unrepresentable) == 2, resultado.unrepresentable
    # e os dois legíveis continuam na lista, ao lado do diagnóstico (E4-AUD5-001)
    assert [path for path, _sha in resultado.files] == ["ok1.py", "ok2.py"]


def test_aud4_002_workspace_sem_o_byte_invalido_continua_normal(repo: Path) -> None:
    """Um repositório **sem** o problema não é afetado por outro que o tenha.

    O contrapeso do fail-closed: a correção isola o registro e o commit corrompidos, não a
    capacidade de `list_tree` funcionar em geral.
    """
    commit_ruim = _commit_with_blobs(repo, [(b"bad" + bytes([0xFF]) + b".py", b"x\n")])
    ruim = list_tree(str(repo), commit_ruim)
    assert ruim is not None and ruim.partial

    outro_repo = repo.parent / "outro"
    init_repo(outro_repo)
    commit_bom = _commit_with_blobs(outro_repo, [(b"normal.py", b"y\n")])

    tree_bom = readable(list_tree(str(outro_repo), commit_bom))
    assert [path for path, _sha in tree_bom] == ["normal.py"]


# =================================================== confirmação via HTTP real — sem 500


def test_aud4_002_post_verify_nao_retorna_500(auth_api_client: TestClient, repo: Path) -> None:
    """`POST /context/verify` com o cenário do auditor: nunca 500, em nenhuma circunstância."""
    nome_invalido = b"bad" + bytes([0xFF]) + b".py"
    _commit_with_blobs(repo, [(nome_invalido, b"conteudo\n"), (b"ok.py", b"ok\n")])

    workspace_id = _workspace(auth_api_client, repo)
    status, _entrada = _create(auth_api_client, workspace_id, source_refs=["*"])
    assert status == 201

    resposta = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context/verify", json={}, headers=SAME_ORIGIN
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["working_tree_divergence"]["unrepresentable_paths"]
    assert [entrada["state"] for entrada in corpo["entries"]] == ["unknown"]


def test_aud4_001_post_verify_com_cr_no_nome_nao_retorna_500(
    auth_api_client: TestClient, repo: Path
) -> None:
    """O mesmo, para o cenário de newline literal — a rota é indiferente a qual dos dois é."""
    _commit_with_blobs(repo, [(b"line\rname.py", b"conteudo\n")])

    workspace_id = _workspace(auth_api_client, repo)
    status, _entrada = _create(auth_api_client, workspace_id, source_refs=["*"])
    assert status == 201

    resposta = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context/verify", json={}, headers=SAME_ORIGIN
    )

    assert resposta.status_code == 200


# ============================================ trava estrutural: nunca mais `text=True`


def _is_subprocess_run_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    )


def test_aud4_subprocess_run_nunca_liga_o_modo_texto() -> None:
    """Barreira contra a reintrodução de `text=True` — ou qualquer forma equivalente.

    `subprocess.run` liga o modo texto com `text=True`, com o alias legado
    `universal_newlines=True`, e **implicitamente** com `encoding=` ou `errors=` mesmo sem
    `text` explícito — as quatro formas produzem a mesma tradução de newline e a mesma
    decodificação fora do controle deste módulo (E4-AUD4-001, E4-AUD4-002). A trava varre a
    AST à procura de **qualquer uma** das quatro keywords em uma chamada a
    `subprocess.run`, não só a forma que existia — no espírito das trava estruturais já
    usadas em E4-AUD2-005 e E4-AUD3-001: mecanismo, não a linha específica que existia.
    """
    fonte = Path(git_runtime.__file__).read_text(encoding="utf-8")
    proibidas = {"text", "universal_newlines", "encoding", "errors"}

    ofensores = [
        kw.arg
        for node in ast.walk(ast.parse(fonte))
        if _is_subprocess_run_call(node)
        for kw in node.keywords  # type: ignore[attr-defined]
        if kw.arg in proibidas
    ]

    assert ofensores == []


def test_aud4_subprocess_run_e_a_unica_chamada_do_modulo() -> None:
    """Trava de escopo (task 3 do prompt): `subprocess` só é chamado em `_run_git`.

    Não há um segundo ponto de leitura de saída de git no módulo — nem no resto do
    backend, onde `test_architecture.py` já trava que `subprocess` só aparece aqui. Se
    algum dia surgir uma segunda chamada, ela precisa da mesma disciplina de bytes — esta
    trava garante que ela não passe despercebida.
    """
    fonte = Path(git_runtime.__file__).read_text(encoding="utf-8")
    chamadas = [node for node in ast.walk(ast.parse(fonte)) if _is_subprocess_run_call(node)]

    assert len(chamadas) == 1
