"""Reproduções da **3ª auditoria Codex** da E4 — E4-AUD3-001.

## O finding

`_strip_prefix` fazia `path.replace("\\", "/")` antes de comparar com o prefixo do
workspace. Uma barra invertida **literal** dentro do nome de um arquivo — legal em Git, em
Linux e em macOS — era assim reinterpretada como separador de diretório, e o caminho
**mudava de identidade**:

| Workspace | Caminho do Git | O que `_strip_prefix` devolvia | O que ele é de verdade |
| --- | --- | --- | --- |
| `ws/` | `ws\\outside.py` | `outside.py` — de dentro do workspace | arquivo da **raiz** do repo |
| raiz | `ws\\outside.py` | `ws/outside.py` — filho de `ws/` | arquivo da **raiz** do repo |

Nos dois sentidos o efeito é o mesmo: um arquivo é atribuído a um lugar onde ele não está.
No primeiro caso ele entra no `source_hash` de um workspace que não o contém (e uma edição
nele derruba a entrada para `stale` sem razão); no segundo ele passa a casar contra
`source_refs` como `ws/**`, e uma edição nele **não** derruba nada, porque a identidade
usada na Parte A e a usada na Parte B podem divergir. Falso `stale` de um lado, falso
`fresh` do outro.

## Por que a correção é *fail closed*, e não uma normalização melhor

Duas afirmações, e as duas têm teste aqui:

1. **O Git não é ambíguo.** Ele emite `/` como separador em toda plataforma e em toda saída
   de caminho. Uma `\\` na saída do Git é sempre um caractere do **nome**. A tradução não
   resolvia ambiguidade nenhuma — ela **criava** a ambiguidade.
2. **O nosso envelope é que não tem a palavra.** `normalize_source_ref` faz
   `value.replace("\\", "/")` — e faz certo, porque é entrada de usuário no Windows. A
   consequência é que **nenhum** `source_ref`, presente ou futuro, produz a barra invertida
   literal necessária para casar contra esse caminho.

Somadas: o arquivo existe, o Git o vê, e o registro não consegue nomeá-lo. Inventar uma
gramática de escape só para ele seria uma segunda gramática de caminho — exatamente o que
E2-AUD-003 fechou. Então ele é excluído do resultado normal (nunca reinterpretado) e a
leitura inteira devolve `UnrepresentablePaths`, que o Context Engine traduz no `unknown`
que [03] §3 já define para "declara depender de código e não consegui verificar" — com os
caminhos junto, como motivo registrado.

## Montagem do cenário

`git mktree -z` + `git commit-tree`, a reprodução exata do auditor. Nenhum sistema de
arquivos Windows cria um nome com `\\`, mas o banco de objetos do Git aceita qualquer byte
que não seja NUL nem `/` — e é a saída do Git que este código lê. O teste vale nos três
sistemas.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.git_runtime as git_runtime
from app.context_engine.source_ref_expansion import ExpansionStatus, expand_against_commit
from app.context_engine.verification import evaluate_freshness, read_workspace_tree
from app.db.enums import ContextState
from app.git_runtime import (
    _strip_prefix,
    list_tree,
    working_tree_diff_against,
    working_tree_status,
)
from app.safety.source_refs import normalize_source_ref
from tests.context_helpers import GIT, init_repo, readable
from tests.test_api_context import SAME_ORIGIN, _create, _workspace

pytestmark = pytest.mark.skipif(GIT is None, reason="git indisponível no PATH")

#: O nome que o auditor usou. **Não** é o caminho `ws/outside.py`: é um arquivo só, na raiz
#: do repositório, cujo nome contém uma barra invertida — irmão do diretório `ws`, não filho.
BLOB_AMBIGUO = "ws\\outside.py"

#: O que a versão anterior devolvia para ele com o workspace na raiz. Nenhum arquivo tem
#: essa identidade no repositório; ela era **inventada** pela tradução.
IDENTIDADE_INVENTADA = "ws/outside.py"


def _git_out(cwd: Path, *args: str, stdin: bytes | None = None) -> str:
    """`git` de montagem de cenário que **devolve o stdout** — o `git()` do helper não devolve."""
    assert GIT is not None
    result = subprocess.run(  # noqa: S603 — git de teste, argv literal, sem shell
        [GIT, *args],
        cwd=cwd,
        capture_output=True,
        input=stdin,
        check=True,
    )
    return result.stdout.decode("utf-8").strip()


@pytest.fixture
def repo_backslash(tmp_path: Path) -> tuple[Path, str, str]:
    """Repositório real com `ws\\outside.py` na raiz e `ws/inside.py` num subdiretório.

    Devolve `(raiz, commit, blob_de_dentro)`. O `HEAD` aponta para o commit, então as rotas
    da API também enxergam o cenário; a árvore de trabalho fica vazia de propósito — o que
    interessa aqui é o banco de objetos, e nenhum `checkout` conseguiria materializar o
    nome com `\\` no Windows.
    """
    root = tmp_path / "repo"
    init_repo(root)

    blob_fora = _git_out(root, "hash-object", "-w", "--stdin", stdin=b"fora do workspace\n")
    blob_dentro = _git_out(root, "hash-object", "-w", "--stdin", stdin=b"dentro do workspace\n")

    tree_ws = _git_out(
        root,
        "mktree",
        "-z",
        stdin=f"100644 blob {blob_dentro}\tinside.py\x00".encode(),
    )
    # `\x00`, nunca `\0`: numa f-string, `\0040000` é lido como o octal `\004` seguido de
    # `0000`, os dois registros do `mktree` saem colados num só e a árvore vira vazia.
    tree_raiz = _git_out(
        root,
        "mktree",
        "-z",
        stdin=(
            f"100644 blob {blob_fora}\t{BLOB_AMBIGUO}\x00040000 tree {tree_ws}\tws\x00"
        ).encode(),
    )
    commit = _git_out(root, "commit-tree", tree_raiz, "-m", "raiz com backslash literal")
    _git_out(root, "update-ref", "HEAD", commit)

    # O diretório precisa existir no disco para ser um `cwd` válido; o conteúdo não importa,
    # porque `ls-tree` lê o banco de objetos e não a árvore de trabalho.
    (root / "ws").mkdir(exist_ok=True)
    return root, commit, blob_dentro


# ================================================ o cenário é o que o auditor descreveu


def test_aud3_001_o_git_emite_os_dois_caminhos_como_irmaos(
    repo_backslash: tuple[Path, str, str],
) -> None:
    """Premissa do finding: o Git distingue `ws/inside.py` de `ws\\outside.py`. Nós é que não.

    Sem esta asserção o resto do arquivo poderia estar testando um cenário que o Git nem
    aceita montar — e aí todos passariam sem provar nada.
    """
    root, commit, _blob = repo_backslash

    saida = _git_out(root, "ls-tree", "-r", "--full-tree", "--name-only", "-z", commit, "--")
    nomes = [nome for nome in saida.split("\0") if nome]

    assert sorted(nomes) == ["ws/inside.py", BLOB_AMBIGUO]


def test_aud3_001_nenhum_source_ref_consegue_nomear_o_caminho() -> None:
    """Por que *fail closed* e não "casar assim mesmo": o vocabulário não tem a palavra.

    `normalize_source_ref` traduz `\\` em `/` — e está certo, porque é entrada de usuário no
    Windows. O efeito colateral é que os dois `source_refs` concebíveis colapsam no mesmo
    valor, e **nenhum** deles produz a barra invertida literal. Não existe texto que o
    usuário possa escrever significando "o arquivo da raiz", então qualquer casamento
    contra ele seria um chute do sistema sobre a intenção de alguém.
    """
    assert normalize_source_ref(BLOB_AMBIGUO) == IDENTIDADE_INVENTADA
    assert normalize_source_ref(IDENTIDADE_INVENTADA) == IDENTIDADE_INVENTADA


# ============================================ E4-AUD3-001 — `list_tree` nos dois workspaces


def test_aud3_001_workspace_em_subdiretorio_nao_adota_o_arquivo_da_raiz(
    repo_backslash: tuple[Path, str, str],
) -> None:
    """Workspace em `ws/`: o arquivo da raiz **não** vira `outside.py` de dentro dele.

    Era o pior dos dois sentidos: o arquivo entrava no `source_hash` de um workspace que
    não o contém, e uma edição nele derrubava para `stale` uma entrada que não descreve
    nada relacionado a ele.
    """
    root, commit, blob_dentro = repo_backslash

    resultado = readable(list_tree(str(root / "ws"), commit))

    assert resultado == [("inside.py", blob_dentro)]


def test_aud3_001_workspace_na_raiz_falha_fechado_com_o_caminho_como_motivo(
    repo_backslash: tuple[Path, str, str],
) -> None:
    """Workspace na raiz: o arquivo é dele, o registro não sabe nomeá-lo, e ele é reportado.

    Exclusão silenciosa seria *fail open*: o `source_hash` sairia sobre um conjunto
    incompleto que se apresenta como completo. O caminho sai de ``files`` e entra em
    ``unrepresentable``, para o veredito que ele afeta ter um motivo legível.

    **Desde E4-AUD5-001 os dois vêm juntos** (era um ou outro): os arquivos legíveis da
    árvore continuam ali, ao lado do diagnóstico. Era esse descarte que apagava a recusa
    de um segredo legível por causa de um arquivo ilegível sem relação nenhuma.
    """
    root, commit, _blob = repo_backslash

    resultado = list_tree(str(root), commit)

    assert resultado is not None
    assert resultado.partial
    assert [item.display for item in resultado.unrepresentable] == [BLOB_AMBIGUO]
    # e os arquivos legíveis **não** foram descartados junto (E4-AUD5-001)
    assert [path for path, _sha in resultado.files] == ["ws/inside.py"]


@pytest.mark.parametrize("subdiretorio", ["", "ws"])
def test_aud3_001_a_identidade_inventada_nao_aparece_em_lugar_nenhum(
    repo_backslash: tuple[Path, str, str], subdiretorio: str
) -> None:
    """A asserção central do finding, dos dois lados: `ws/outside.py` **não existe**.

    Nenhum dos dois workspaces pode devolver essa identidade — nem como caminho relativo
    (`outside.py`, no workspace `ws/`), nem como caminho completo (`ws/outside.py`, no
    workspace da raiz). Um arquivo com esse nome não está no commit.
    """
    root, commit, _blob = repo_backslash

    resultado = list_tree(str(root / subdiretorio) if subdiretorio else str(root), commit)

    assert resultado is not None
    devolvidos = {path for path, _sha in resultado.files} | {
        item.display for item in resultado.unrepresentable
    }
    assert IDENTIDADE_INVENTADA not in devolvidos
    assert "outside.py" not in devolvidos


# ================================================= a correção fecha as três leituras, não uma


def test_aud3_001_parte_b_tambem_falha_fechado(repo_backslash: tuple[Path, str, str]) -> None:
    """`working_tree_diff_against` (a Parte B) tem o mesmo desfecho, pelo mesmo motivo.

    Corrigir só a Parte A recriaria a assimetria de base que as rodadas 1 e 2 gastaram
    quatro findings para eliminar: as duas metades da verificação precisam concordar sobre
    o que é um caminho antes de concordarem sobre o que diverge.
    """
    root, commit, _blob = repo_backslash

    resultado = working_tree_diff_against(str(root), commit)

    assert resultado is not None
    assert BLOB_AMBIGUO in [item.display for item in resultado.unrepresentable]


def test_aud3_001_status_contra_head_tambem_falha_fechado(
    repo_backslash: tuple[Path, str, str],
) -> None:
    """`working_tree_status` idem — ela não é a Parte B, mas alimenta a UI e o preflight."""
    root, _commit, _blob = repo_backslash

    resultado = working_tree_status(str(root))

    assert resultado is not None
    assert BLOB_AMBIGUO in [item.display for item in resultado.unrepresentable]


def test_aud3_001_a_expansao_e_incerta_so_para_quem_poderia_alcancar_o_caminho(
    repo_backslash: tuple[Path, str, str],
) -> None:
    """O caminho ilegível torna incerto o `source_ref` que **poderia** alcançá-lo. Só ele.

    ## Este teste mudou de veredito em E4-AUD5-001 — de propósito

    Até a 4ª rodada, `ws/**` aqui dava `UNRESOLVED`: bastava existir um caminho ilegível em
    qualquer canto da árvore para **toda** expansão virar incerta. Era grosseiro demais, e
    E4-AUD5-001 mostrou o preço — um `.env` legível e resolvido deixava de ser recusado
    porque um arquivo sem relação nenhuma tinha nome inválido no mesmo commit.

    A pergunta passou a ser por ref, decidida nos **bytes**: `ws/**` só casa caminhos que
    começam pelos bytes `ws/`, e `ws\\outside.py` começa por `ws\\`. Ele **não pode**
    alcançá-lo — então a resolução dele está completa, e dizer o contrário seria inventar
    uma incerteza que não existe.

    O finding da rodada 3 continua inteiro e testado nos outros casos deste arquivo: o
    arquivo da raiz **não** é reatribuído a `ws/`, **não** ganha a identidade inventada
    `ws/outside.py`, e aparece no diagnóstico. O que mudou é a consequência, que deixou de
    ser coletiva.
    """
    root, commit, _blob = repo_backslash

    # `ws/**` não alcança `ws\outside.py`: prefixo de bytes `ws/` contra `ws\`.
    alcanca_nao = expand_against_commit(str(root), commit, ["ws/**"])
    assert alcanca_nao.status is ExpansionStatus.RESOLVED
    assert alcanca_nao.paths == ("ws/inside.py",)
    assert alcanca_nao.source_hash is not None
    # ...e o diagnóstico viaja junto mesmo assim: a árvore está incompleta, e isso é um
    # fato sobre o repositório que o operador merece ver.
    assert alcanca_nao.unrepresentable_paths == (BLOB_AMBIGUO,)

    # `**` (zero ou mais segmentos, prefixo literal vazio) poderia alcançar qualquer coisa.
    alcanca_sim = expand_against_commit(str(root), commit, ["**"])
    assert alcanca_sim.status is ExpansionStatus.UNRESOLVED
    assert alcanca_sim.incomplete_refs == ("**",)
    assert alcanca_sim.unrepresentable_paths == (BLOB_AMBIGUO,)


def test_aud3_001_verificacao_vira_unknown_para_o_ref_que_alcanca(
    repo_backslash: tuple[Path, str, str],
) -> None:
    """`unknown` com motivo — para a entrada cujos refs poderiam alcançar o caminho ilegível.

    Sem `stale_reason`: os dois que [03] §3 define **afirmam** divergência, e afirmar
    divergência é exatamente o que não se pode fazer sobre um arquivo que ninguém leu. O
    motivo viaja em `unrepresentable_paths`.

    O snapshot mudou em E4-AUD5-001: ele carrega as divergências **legíveis** e os caminhos
    ilegíveis lado a lado. Antes o caminho ilegível zerava `divergences` — e com isso a
    verificação perdia divergências que conhecia perfeitamente.
    """
    root, commit, _blob = repo_backslash

    snapshot = read_workspace_tree(str(root), verification_commit=commit)
    # As duas metades, juntas (E4-AUD5-001): a divergência legível **e** o caminho ilegível.
    assert snapshot.divergences is not None
    assert [entry.path for entry in snapshot.divergences] == ["ws/inside.py"]
    assert snapshot.unrepresentable_paths == (BLOB_AMBIGUO,)

    resultado = evaluate_freshness(
        source_refs=["**"],
        baseline_source_hash="baseline-anterior",
        snapshot=snapshot,
    )

    assert resultado.state is ContextState.UNKNOWN
    assert resultado.stale_reason is None
    assert resultado.unrepresentable_paths == (BLOB_AMBIGUO,)


def test_aud3_001_a_api_devolve_o_motivo(
    auth_api_client: TestClient, repo_backslash: tuple[Path, str, str]
) -> None:
    """*Fail closed* que não chega à resposta é indistinguível de "o git não respondeu"."""
    root, _commit, _blob = repo_backslash
    workspace_id = _workspace(auth_api_client, root)
    status, _entrada = _create(auth_api_client, workspace_id, source_refs=["**"])
    assert status == 201

    resposta = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context/verify", json={}, headers=SAME_ORIGIN
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["working_tree_divergence"]["unrepresentable_paths"] == [BLOB_AMBIGUO]
    assert [entrada["state"] for entrada in corpo["entries"]] == ["unknown"]


# ====================================== *fail closed* é do workspace afetado, não do mundo


def test_aud3_001_o_workspace_vizinho_continua_verificavel(
    repo_backslash: tuple[Path, str, str],
) -> None:
    """O workspace em `ws/` verifica normalmente — o arquivo sem nome está **fora** dele.

    Esta é a contrapartida do fail-closed, e ela precisa de teste próprio: se a correção
    tivesse promovido o repositório inteiro a `unknown`, um arquivo esquisito na raiz
    tornaria inverificável todo workspace do monorepo. O que basta para `unknown` é o
    caminho ser de **dentro** do workspace; o de fora continua sendo descartado como
    sempre foi, porque nenhum `source_ref` poderia alcançá-lo de qualquer modo.
    """
    root, commit, blob_dentro = repo_backslash

    assert readable(list_tree(str(root / "ws"), commit)) == [("inside.py", blob_dentro)]

    expansao = expand_against_commit(str(root / "ws"), commit, ["*.py"])
    assert expansao.status is ExpansionStatus.RESOLVED
    assert expansao.paths == ("inside.py",)


# =============================================== contrafactual: o teste pega o bug original


def _strip_prefix_anterior(path: str, prefix: str) -> str | None:
    """A versão de antes de E4-AUD3-001, copiada palavra por palavra.

    Existe para que o contrafactual não dependa de eu reverter o arquivo de produção à mão:
    a diferença fica no próprio teste, legível, e a asserção compara as duas.
    """
    normalized = path.replace("\\", "/")
    if not prefix:
        return normalized
    if not normalized.startswith(prefix):
        return None
    return normalized[len(prefix) :] or None


@pytest.mark.parametrize(
    ("prefix", "antes", "agora"),
    [
        # Workspace em `ws/`: adotava um arquivo da raiz como se fosse dele.
        ("ws/", "outside.py", None),
        # Workspace na raiz: dava ao arquivo um diretório-pai que não existe.
        ("", IDENTIDADE_INVENTADA, BLOB_AMBIGUO),
    ],
)
def test_aud3_001_a_versao_anterior_trocava_a_identidade(
    prefix: str, antes: str | None, agora: str | None
) -> None:
    """Prova de que a reprodução mira o defeito real, e não um cenário que já passava.

    A versão anterior devolvia uma identidade **diferente** em cada um dos dois workspaces,
    e nenhuma das duas é o arquivo que está no commit.
    """
    assert _strip_prefix_anterior(BLOB_AMBIGUO, prefix) == antes
    assert _strip_prefix(BLOB_AMBIGUO, prefix) == agora
    assert _strip_prefix_anterior(BLOB_AMBIGUO, prefix) != _strip_prefix(BLOB_AMBIGUO, prefix)


def test_aud3_001_o_prefixo_do_workspace_tambem_nao_e_traduzido() -> None:
    """`_strip_prefix` compara **literalmente**, sem normalizar nenhum dos dois lados.

    Os dois vêm do mesmo Git, na mesma base, com `/` como separador — normalizar um lado só
    os desalinha, e normalizar os dois recria a troca de identidade. Um workspace cujo
    **próprio nome** contenha `\\` continua funcionando: o prefixo é removido inteiro, e o
    que sobra é nomeável.
    """
    assert _strip_prefix("a\\b/src/app.py", "a\\b/") == "src/app.py"
    assert _strip_prefix("a/b/src/app.py", "a\\b/") is None


def test_aud3_001_nenhuma_leitura_do_git_traduz_barra_invertida() -> None:
    """Trava estrutural: nenhuma tradução de `\\` sobreviveu no `git_runtime`.

    Barreira contra a reintrodução por reflexo — "estamos no Windows, então tem de
    normalizar a barra" é a intuição que produziu o finding, e ela vai ocorrer de novo a
    quem ler este módulo daqui a seis meses. A checagem é sobre **mecanismo** (qualquer
    transformação de string com `\\` num literal), não sobre a linha específica que existia,
    no mesmo espírito de E4-AUD2-005.
    """
    fonte = Path(git_runtime.__file__).read_text(encoding="utf-8")

    traducoes = [
        ast.unparse(node)
        for node in ast.walk(ast.parse(fonte))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"replace", "translate", "maketrans"}
        and any(
            isinstance(argumento, ast.Constant)
            and isinstance(argumento.value, str)
            and "\\" in argumento.value
            for argumento in node.args
        )
    ]

    assert traducoes == []
