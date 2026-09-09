"""Reproduções da **5ª auditoria Codex** da E4 — E4-AUD5-001.

## O finding

`list_tree()` devolvia **ou** os arquivos legíveis, **ou** o aviso de que havia caminho não
representável — nunca os dois. Um tipo-**soma**. Bastava um nome ilegível em qualquer canto
da árvore para os registros válidos já coletados serem descartados.

Isso propagava para a expansão: com pelo menos um caminho não representável, um glob que
cobre arquivos **válidos** devolvia `UNRESOLVED` em vez de classificá-los. E `UNRESOLVED`
não classifica segredo. O resultado é o defeito de segurança que dá nome a esta rodada:

> um `.env` conhecido, legível e resolvível deixava de ser recusado (`DENIED`) só porque
> **outro** arquivo, sem relação nenhuma, tinha nome ilegível no mesmo commit.

A ignorância sobre um arquivo virava ignorância sobre todos — e ignorância, aqui, é o
mesmo que permissão.

## A correção

Tipo-**produto**: `TreeListing` carrega `files` **e** `unrepresentable`, sempre. Quem chama
recebe os dados válidos junto com a informação de que a leitura está parcial, e decide com
as duas coisas na mão.

Na expansão, a incompletude passou a ser avaliada **por `source_ref`**, e a pergunta é
decidível em bytes sem interpretar gramática nenhuma sobre o alvo ilegível
(`source_ref_expansion._may_cover`):

* ref **literal** — a mesma regra de `_literal_covers`, byte a byte;
* ref com **glob** — todo caminho que ele casa começa pelo seu prefixo literal (os
  caracteres antes do primeiro metacaractere, que viram tokens `LITERAL` ancorados no
  início). Se os bytes do caminho ilegível não começam por aí, o ref não pode alcançá-lo.

Sobre-aproximação segura: nunca diz "não pode" quando poderia.

## A precedência que esta rodada tinha de preservar inteira

**`DENIED` > incompleto (`UNRESOLVED`) > `RESOLVED`.** As duas metades vêm de auditorias
diferentes e não podem enfraquecer uma à outra:

* `DENIED` > `UNRESOLVED` é de E4-AUD-005 (um ref com erro de digitação não pode desligar a
  checagem de segredo dos outros);
* `DENIED` > incompleto é desta rodada (um arquivo ilegível não pode desligar a checagem de
  segredo dos arquivos legíveis).

A forma geral das duas é a mesma: **o que já se sabe nunca é apagado pelo que não se sabe.**
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.context_engine.source_ref_expansion import (
    ExpansionStatus,
    expand_against_commit,
    expand_source_refs,
)
from app.context_engine.verification import evaluate_freshness, read_workspace_tree
from app.db.enums import ContextState
from app.git_runtime import TreeListing, UnrepresentablePath, list_tree
from tests.context_helpers import GIT, init_repo
from tests.test_api_context import _create, _workspace

pytestmark = pytest.mark.skipif(GIT is None, reason="git indisponível no PATH")

#: O cenário exato do auditor: um nome com byte inválido entre dois nomes válidos, e
#: `zgood.py` **depois** dele na ordem da árvore — para provar que o registro problemático
#: não interrompe nem o que veio antes nem o que vem depois.
NOME_INVALIDO = b"bad-" + bytes([0xFF]) + b".py"


def _git_out(cwd: Path, *args: str, stdin: bytes | None = None) -> str:
    assert GIT is not None
    result = subprocess.run(  # noqa: S603 — git de teste, argv literal, sem shell
        [GIT, *args], cwd=cwd, capture_output=True, input=stdin, check=True
    )
    return result.stdout.decode("utf-8").strip()


def _commit_with_blobs(root: Path, blobs: list[tuple[bytes, bytes]]) -> str:
    """Monta blobs, árvore e commit com `mktree -z`/`commit-tree`, em bytes crus."""
    records = b""
    for name, content in blobs:
        sha = _git_out(root, "hash-object", "-w", "--stdin", stdin=content)
        records += b"100644 blob " + sha.encode("ascii") + b"\t" + name + b"\x00"
    tree = _git_out(root, "mktree", "-z", stdin=records)
    commit = _git_out(root, "commit-tree", tree, "-m", "cenario da 5a auditoria")
    _git_out(root, "update-ref", "HEAD", commit)
    return commit


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    init_repo(root)
    return root


# ============================ 1 — `list_tree` devolve os válidos **e** o diagnóstico


def test_aud5_001_list_tree_devolve_os_validos_junto_com_o_diagnostico(repo: Path) -> None:
    """Reprodução exata: `bad-\\xFF.py`, `good.py`, `zgood.py` na mesma árvore.

    Os dois legíveis saem normalmente — **não** uma lista vazia —, e o ilegível aparece
    separado. Um de cada lado da mesma resposta, que é o ponto inteiro de E4-AUD5-001.

    `zgood.py` está aqui por vir **depois** do registro problemático na ordem de bytes da
    árvore: prova que o ilegível não interrompe nem o que já tinha sido coletado nem o que
    ainda viria.
    """
    commit = _commit_with_blobs(
        repo,
        [(NOME_INVALIDO, b"ruim\n"), (b"good.py", b"bom\n"), (b"zgood.py", b"bom tambem\n")],
    )

    resultado = list_tree(str(repo), commit)

    assert resultado is not None
    assert [path for path, _sha in resultado.files] == ["good.py", "zgood.py"]
    assert len(resultado.unrepresentable) == 1
    assert resultado.partial


# ============================ 2 — um ref que não alcança o ilegível resolve normalmente


def test_aud5_001_expandir_good_py_resolve_normalmente(repo: Path) -> None:
    """`good.py` é literal e não alcança `bad-\\xFF.py`: `RESOLVED`, com `source_hash`.

    Era aqui que a versão anterior devolvia `UNRESOLVED` — e um `UNRESOLVED` não classifica
    segredo. Este é o degrau que o teste decisivo (mais abaixo) sobe.
    """
    commit = _commit_with_blobs(
        repo,
        [(NOME_INVALIDO, b"ruim\n"), (b"good.py", b"bom\n"), (b"zgood.py", b"bom tambem\n")],
    )

    expansao = expand_against_commit(str(repo), commit, ["good.py"])

    assert expansao.status is ExpansionStatus.RESOLVED
    assert expansao.paths == ("good.py",)
    assert expansao.source_hash is not None
    # O diagnóstico continua viajando: a árvore está incompleta, mesmo que **esta** entrada
    # não seja afetada por isso.
    assert expansao.unrepresentable_paths


def test_aud5_001_um_glob_que_alcancaria_o_ilegivel_continua_incerto(repo: Path) -> None:
    """A contrapartida, sem a qual o teste acima seria afrouxamento e não precisão.

    `*` tem prefixo literal vazio — poderia casar qualquer coisa, inclusive o caminho que
    ninguém conseguiu ler. A resolução dele **está** incompleta, e dizer `RESOLVED` seria um
    falso `fresh` esperando acontecer.
    """
    commit = _commit_with_blobs(repo, [(NOME_INVALIDO, b"ruim\n"), (b"good.py", b"bom\n")])

    expansao = expand_against_commit(str(repo), commit, ["*"])

    assert expansao.status is ExpansionStatus.UNRESOLVED
    assert expansao.incomplete_refs == ("*",)
    # E o ref **não** foi parar em `unresolved_refs`: ele casou arquivos, não casou zero.
    assert expansao.unresolved_refs == ()


# ============================ 3 e 4 — o teste decisivo: o DENY não pode sumir


def test_aud5_001_deny_de_segredo_sem_o_arquivo_ilegivel(
    auth_api_client: TestClient, repo: Path
) -> None:
    """Linha de base: `.env` + `good.py`, `source_refs=["*"]` → 422 por segredo."""
    _commit_with_blobs(repo, [(b".env", b"TOKEN=1\n"), (b"good.py", b"bom\n")])
    workspace_id = _workspace(auth_api_client, repo)

    status, corpo = _create(auth_api_client, workspace_id, source_refs=["*"])

    assert status == 422, corpo


def test_aud5_001_deny_de_segredo_sobrevive_ao_arquivo_ilegivel(
    auth_api_client: TestClient, repo: Path
) -> None:
    """**O teste decisivo do finding.** A mesma árvore, mais `bad-\\xFF.py`: ainda 422.

    Um arquivo ilegível não pode fazer o `DENY` do `.env` desaparecer. Antes de
    E4-AUD5-001, podia: o ilegível apagava a lista de arquivos, a expansão virava
    `UNRESOLVED` antes de classificar segredo nenhum, e a entrada era **criada** — descrevendo
    um `.env` que ninguém deveria alcançar.

    Note que `*` aqui é, ele próprio, um ref incompleto (poderia alcançar o ilegível). O
    teste afirma que **`DENIED` vence incompleto**, não que a incompletude sumiu.
    """
    _commit_with_blobs(
        repo,
        [(b".env", b"TOKEN=1\n"), (b"good.py", b"bom\n"), (NOME_INVALIDO, b"ruim\n")],
    )
    workspace_id = _workspace(auth_api_client, repo)

    status, corpo = _create(auth_api_client, workspace_id, source_refs=["*"])

    assert status == 422, corpo


def test_aud5_001_a_precedencia_inteira_numa_arvore_so() -> None:
    """`DENIED` > incompleto > `RESOLVED`, nos três desfechos da **mesma** árvore parcial.

    Função pura, sem git: a árvore é montada à mão para os três casos verem exatamente os
    mesmos dados e a diferença ser só o `source_ref`.
    """
    tree = TreeListing(
        files=(("config/.env", "a" * 40), ("src/app.py", "b" * 40)),
        unrepresentable=(UnrepresentablePath(raw=b"src/bad-\xff.py", display="src/bad-\\xff.py"),),
    )

    # DENIED: alcança o segredo — e vence, embora `config/*` também seja incompleto.
    assert expand_source_refs(["config/*"], tree).status is ExpansionStatus.DENIED

    # incompleto: `src/*` poderia alcançar `src/bad-\xff.py` (prefixo de bytes `src/`).
    incompleto = expand_source_refs(["src/*"], tree)
    assert incompleto.status is ExpansionStatus.UNRESOLVED
    assert incompleto.incomplete_refs == ("src/*",)

    # RESOLVED: literal, e demonstravelmente fora do alcance do ilegível.
    resolvido = expand_source_refs(["src/app.py"], tree)
    assert resolvido.status is ExpansionStatus.RESOLVED
    assert resolvido.paths == ("src/app.py",)


# ============================ Parte B — divergência conhecida não é apagada


def test_aud5_001_divergencia_legivel_sobrevive_ao_registro_ilegivel(repo: Path) -> None:
    """O mesmo princípio na Parte B: o que se sabe não é apagado pelo que não se sabe.

    A leitura das divergências passa a carregar as duas metades. Antes, o registro ilegível
    zerava `divergences` — e a verificação perdia divergências que conhecia perfeitamente,
    inclusive as cobertas pelos `source_refs` da entrada.
    """
    commit = _commit_with_blobs(repo, [(NOME_INVALIDO, b"ruim\n"), (b"src-app.py", b"antes\n")])

    snapshot = read_workspace_tree(str(repo), verification_commit=commit)

    # A árvore de trabalho está vazia (o commit veio de `mktree`), então os dois blobs
    # aparecem como divergentes — o legível como divergência, o ilegível como diagnóstico.
    assert snapshot.divergences is not None
    assert [entry.path for entry in snapshot.divergences] == ["src-app.py"]
    assert snapshot.unrepresentable_paths

    # E a cobertura é calculada sobre a divergência conhecida, com um ref que não alcança o
    # caminho ilegível: veredito definitivo, apesar da leitura parcial.
    resultado = evaluate_freshness(
        source_refs=["src-app.py"],
        baseline_source_hash="baseline-que-nao-confere",
        snapshot=snapshot,
    )
    assert resultado.state is ContextState.STALE
    assert [entry.path for entry in resultado.covered_divergences] == ["src-app.py"]
