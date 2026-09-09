"""Reproduções exatas da 2ª auditoria Codex da E4 (E4-AUD2-001..005).

Como na rodada 1, cada teste monta o cenário do jeito que o auditor montou e afirma sobre o
comportamento que estava errado.

O arquivo tem uma seção a mais que as anteriores: **a estratégia**. Quatro findings em duas
rodadas foram a mesma classe de defeito — configuração do repositório do usuário movendo a
base dos caminhos que o git emite. A correção desta rodada não é só mais um patch: é fixar a
configuração relevante por `-c` em toda invocação, do jeito que a E3 já fazia para as travas
de somente-leitura. Os testes de `TestClient de configuração` abaixo existem para provar que
a classe fechou, não só os casos vistos.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.context_engine import create_entry
from app.context_engine.verification import read_workspace_tree, verify_freshness
from app.db.enums import ContextDomain, ContextState, StaleReason, WorkspaceType
from app.db.models import ContextRegistryEntry, DevWorkspace
from app.git_runtime import (
    _READONLY_GIT_OPTIONS,
    WorkingTreeChange,
    list_tree,
    preflight,
    working_tree_diff_against,
    working_tree_status,
)
from app.workspace import create_workspace
from tests.context_helpers import GIT, commit_all, git, init_repo, readable, write

pytestmark = pytest.mark.skipif(GIT is None, reason="git indisponível no PATH")


def _entry(session: Session, ws: DevWorkspace, refs: list[str]) -> ContextRegistryEntry:
    return create_entry(
        session,
        ws,
        domain=ContextDomain.MODULES,
        title="Módulo src",
        body="Descreve o código sob src/.\n",
        source_refs=refs,
    )


def _workspace(session: Session, path: Path, *, name: str = "ws") -> DevWorkspace:
    created = create_workspace(
        session, name=name, workspace_type=WorkspaceType.PERSONAL, local_path=str(path)
    )
    session.flush()
    return created


@pytest.fixture
def monorepo(tmp_path: Path) -> tuple[Path, Path]:
    """Repositório cuja raiz **não** é o `DevWorkspace`. Devolve `(repo, workspace)`."""
    repo = tmp_path / "monorepo"
    workspace = repo / "pacotes" / "app"
    init_repo(repo)
    write(repo, "raiz.txt", "fora do workspace\n")
    write(repo, "pacotes/app/src/app.py", "print('ok')\n")
    write(repo, "pacotes/app/src/util.py", "UTIL = 1\n")
    commit_all(repo, "monorepo inicial")
    return repo, workspace


# ══════════════════ A ESTRATÉGIA — configuração fixada por `-c`, não por default


def test_estrategia_as_opcoes_de_base_estao_fixadas_em_toda_invocacao() -> None:
    """As três opções que fixam a base de caminho estão no bloco global de `-c`.

    Quatro findings em duas rodadas foram a mesma classe: a base que o git emite depende de
    configuração que o repositório do usuário controla. A regra passou a ser a mesma da E3
    para somente-leitura — fixar por `-c`, não confiar em default. Este teste é o que impede
    que uma delas seja removida por engano.
    """
    pares = list(zip(_READONLY_GIT_OPTIONS[::2], _READONLY_GIT_OPTIONS[1::2], strict=True))

    assert all(flag == "-c" for flag, _valor in pares)
    assert {valor for _flag, valor in pares} == {
        "core.fsmonitor=false",  # E3-AUD-003: somente-leitura
        "core.quotepath=false",  # base: nunca C-quoting
        "diff.relative=false",  # base: `diff` nomeia a partir da raiz
        "status.relativePaths=false",  # base: `status` nomeia a partir da raiz
    }


@pytest.mark.parametrize(
    ("chave", "valor"),
    [
        ("diff.relative", "true"),
        ("status.relativePaths", "true"),
        ("core.quotepath", "true"),
        ("core.fsmonitor", "true"),
    ],
)
def test_estrategia_configuracao_hostil_no_repo_nao_muda_nada(
    session: Session, monorepo: tuple[Path, Path], chave: str, valor: str
) -> None:
    """**O teste da classe, não do caso.** Cada opção hostil, uma por vez, sem efeito.

    É este teste — e não os quatro pontuais — que responde "a estratégia fechou a classe?".
    Ele configura no repositório de teste exatamente a opção que o `-c` fixa, com o valor
    oposto, e afirma que a verificação continua chegando ao mesmo veredito.
    """
    repo, workspace_path = monorepo
    git(repo, "config", chave, valor)

    workspace = _workspace(session, workspace_path)
    entry = _entry(session, workspace, ["src/**"])
    assert entry.state is ContextState.FRESH, "linha de base estabelecida com a config hostil"

    write(repo, "pacotes/app/src/app.py", "print('editado sem commitar')\n")

    outcome = verify_freshness(entry, read_workspace_tree(workspace.local_path))

    assert outcome.state is ContextState.STALE, f"{chave}={valor}"
    assert outcome.stale_reason is StaleReason.WORKING_TREE, f"{chave}={valor}"
    assert [item.path for item in outcome.covered_divergences] == ["src/app.py"]


def test_estrategia_as_tres_leituras_concordam_na_base(
    monorepo: tuple[Path, Path],
) -> None:
    """Uma base, uma transformação: `ls-tree`, `status` e `diff` nomeiam igual.

    Era a **assimetria** — o `ls-tree` relativo ao diretório corrente e o resto relativo à
    raiz — que produzia um falso `fresh` novo a cada rodada. Agora os três são fixados na
    raiz do repositório e passam pelo mesmo `_strip_prefix`.
    """
    repo, workspace = monorepo
    git(repo, "config", "diff.relative", "true")
    git(repo, "config", "status.relativePaths", "true")

    head = preflight(str(workspace)).head
    assert head is not None
    write(repo, "pacotes/app/src/app.py", "print('sujo')\n")

    da_arvore = {path for path, _sha in readable(list_tree(str(workspace), head))}
    do_status = {item.path for item in readable(working_tree_status(str(workspace)))}
    do_diff = {item.path for item in readable(working_tree_diff_against(str(workspace), head))}

    assert da_arvore == {"src/app.py", "src/util.py"}
    assert do_status == {"src/app.py"}
    assert do_diff == {"src/app.py"}
    assert do_status <= da_arvore and do_diff <= da_arvore


def test_estrategia_list_tree_descarta_o_que_esta_fora_do_workspace(
    monorepo: tuple[Path, Path],
) -> None:
    """`--full-tree` lista o repo inteiro; o que não é do workspace não vira contexto."""
    repo, workspace = monorepo
    head = preflight(str(workspace)).head
    assert head is not None

    paths = {path for path, _sha in readable(list_tree(str(workspace), head))}

    assert "raiz.txt" not in paths
    assert "pacotes/app/src/app.py" not in paths, "o caminho vem reduzido à base do workspace"
    assert paths == {"src/app.py", "src/util.py"}
    del repo


# ═══════════════════════════ E4-AUD2-001 — `diff.relative` reintroduzia falso fresh


def test_aud2_001_reproducao_diff_relative_true_nao_reintroduz_falso_fresh(
    session: Session, monorepo: tuple[Path, Path]
) -> None:
    """**Reprodução exata do finding.**

    Workspace em subdiretório **e** `diff.relative=true` no repositório. O `diff` passava a
    nomear a partir do diretório corrente (`src/app.py`) enquanto o resto da verificação
    esperava a raiz, o prefixo era removido de um caminho que não o tinha, e a divergência
    era descartada como "de fora do workspace" — falso `fresh`, de novo, pelo mesmo motivo
    de E4-AUD-002 e por uma porta que aquela correção não fechava.
    """
    repo, workspace_path = monorepo
    git(repo, "config", "diff.relative", "true")

    workspace = _workspace(session, workspace_path)
    entry = _entry(session, workspace, ["src/**"])
    assert entry.state is ContextState.FRESH

    write(repo, "pacotes/app/src/app.py", "print('editado sem commitar')\n")

    outcome = verify_freshness(entry, read_workspace_tree(workspace.local_path))

    assert outcome.state is ContextState.STALE
    assert outcome.stale_reason is StaleReason.WORKING_TREE
    assert [item.path for item in outcome.covered_divergences] == ["src/app.py"]


def test_aud2_001_diff_relative_true_tambem_nao_afeta_a_leitura_crua(
    monorepo: tuple[Path, Path],
) -> None:
    """A mesma coisa um nível abaixo, direto em `working_tree_diff_against`."""
    repo, workspace = monorepo
    head = preflight(str(workspace)).head
    assert head is not None

    write(repo, "pacotes/app/src/app.py", "print('sujo')\n")
    sem_config = readable(working_tree_diff_against(str(workspace), head))

    git(repo, "config", "diff.relative", "true")
    com_config = readable(working_tree_diff_against(str(workspace), head))

    assert sem_config == com_config
    assert [item.path for item in com_config] == ["src/app.py"]


# ═══════════════════════ E4-AUD2-002 — staged + reversão anulava a divergência


def test_aud2_002_reproducao_staged_e_revertido_ainda_diverge(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """**Reprodução exata do finding.**

    Commit A com conteúdo A; escreve B e dá `stage`; escreve A de novo no arquivo **sem**
    atualizar o índice. A árvore de trabalho volta a ser idêntica ao commit, então
    `git diff <A>` sai vazio — mas o índice ainda contém B, e um `git commit` sem argumentos
    gravaria B. A divergência existe e não aparecia: falso `fresh`.

    A correção é somar `git diff --cached <A>` por **união**, não trocar uma leitura pela
    outra: a entrada diverge se **qualquer** das duas divergir, não se o resultado líquido
    divergir.
    """
    entry = _entry(session, workspace, ["src/**"])
    commit_a = entry.source_hash_commit
    assert commit_a is not None
    assert entry.state is ContextState.FRESH

    original = (repo_path / "src" / "app.py").read_text(encoding="utf-8")

    write(repo_path, "src/app.py", "print('CONTEUDO B')\n")
    git(repo_path, "add", "src/app.py")  # índice fica em B
    write(repo_path, "src/app.py", original)  # árvore volta para A

    outcome = verify_freshness(entry, read_workspace_tree(workspace.local_path))

    assert outcome.state is ContextState.STALE
    assert outcome.stale_reason is StaleReason.WORKING_TREE
    assert [item.path for item in outcome.covered_divergences] == ["src/app.py"]


def test_aud2_002_a_leitura_crua_ve_o_indice(repo_path: Path) -> None:
    """Um nível abaixo: `working_tree_diff_against` enxerga o índice.

    O contraste com `git diff` puro é o ponto — a árvore está limpa e a divergência está só
    no índice.
    """
    commit_a = preflight(str(repo_path)).head
    assert commit_a is not None
    original = (repo_path / "src" / "app.py").read_text(encoding="utf-8")

    write(repo_path, "src/app.py", "print('B')\n")
    git(repo_path, "add", "src/app.py")
    write(repo_path, "src/app.py", original)

    divergencias = readable(working_tree_diff_against(str(repo_path), commit_a))

    assert divergencias is not None
    assert [item.path for item in divergencias] == ["src/app.py"]
    assert divergencias[0].kind is WorkingTreeChange.MODIFIED


def test_aud2_002_arquivo_novo_so_no_indice_tambem_diverge(repo_path: Path) -> None:
    """Um `add` de arquivo novo, sem commit: existe no índice e não existe na base."""
    commit_a = preflight(str(repo_path)).head
    assert commit_a is not None

    write(repo_path, "src/novo.py", "NOVO = 1\n")
    git(repo_path, "add", "src/novo.py")

    divergencias = readable(working_tree_diff_against(str(repo_path), commit_a))

    assert divergencias is not None
    por_caminho = {item.path: item.kind for item in divergencias}
    assert por_caminho["src/novo.py"] is WorkingTreeChange.STAGED


def test_aud2_002_arvore_de_fato_limpa_continua_limpa(repo_path: Path) -> None:
    """Precisão: somar o índice não pode inventar divergência onde não há."""
    commit_a = preflight(str(repo_path)).head
    assert commit_a is not None

    assert readable(working_tree_diff_against(str(repo_path), commit_a)) == []


# ═══════════════════════════ E4-AUD2-003 — `.strip()` corrompia o prefixo


@pytest.fixture
def repo_com_espaco_no_nome(tmp_path: Path) -> tuple[Path, Path]:
    """Workspace num diretório cujo nome **começa** com espaço (o caso do auditor).

    Espaço **no fim** não entra aqui, por dois motivos independentes: o Windows não consegue
    criar um diretório assim — ele descarta o espaço final em silêncio —, e o nosso próprio
    envelope de caminho já recusa componente terminado em espaço (`path.trailing_dot_or_
    space`), justamente porque o Windows cria um alias. Espaço no **início** é legal em todo
    lugar, não é recusado por nada, e é exatamente o que o `.strip()` comia.
    """
    repo = tmp_path / "repo-espaco"
    workspace = repo / " lead ws"
    init_repo(repo)
    write(repo, "raiz.txt", "fora\n")
    write(repo, " lead ws/src/app.py", "print('ok')\n")
    write(repo, " lead ws/src/util.py", "UTIL = 1\n")
    commit_all(repo, "inicial")
    return repo, workspace


def test_aud2_003_reproducao_prefixo_com_espaco_e_preservado(
    repo_com_espaco_no_nome: tuple[Path, Path],
) -> None:
    """**Reprodução exata do finding.**

    `rev-parse --show-prefix` devolve `" lead ws/"`; o `.strip()` anterior o transformava em
    `"lead ws/"`. Com o prefixo corrompido, `_strip_prefix` deixava de reconhecer qualquer
    caminho como sendo de dentro do workspace: **todas** as divergências eram descartadas
    como "de fora", e a entrada ficava eternamente `fresh`. Espaço no início ou no fim de um
    nome de diretório é legal em todos os sistemas que suportamos.
    """
    repo, workspace = repo_com_espaco_no_nome
    head = preflight(str(workspace)).head
    assert head is not None

    da_arvore = {path for path, _sha in readable(list_tree(str(workspace), head))}
    assert da_arvore == {"src/app.py", "src/util.py"}

    write(repo, " lead ws/src/app.py", "print('sujo')\n")

    do_status = {item.path for item in readable(working_tree_status(str(workspace)))}
    do_diff = {item.path for item in readable(working_tree_diff_against(str(workspace), head))}

    assert do_status == {"src/app.py"}
    assert do_diff == {"src/app.py"}


def test_aud2_003_verificacao_completa_com_espaco_no_prefixo(
    session: Session, repo_com_espaco_no_nome: tuple[Path, Path]
) -> None:
    """O mesmo pelo caminho de verdade: a entrada fica `stale`, não `fresh`."""
    repo, workspace_path = repo_com_espaco_no_nome
    workspace = _workspace(session, workspace_path, name="com espaco")

    entry = _entry(session, workspace, ["src/**"])
    assert entry.state is ContextState.FRESH
    assert entry.source_hash is not None

    write(repo, " lead ws/src/app.py", "print('editado')\n")

    outcome = verify_freshness(entry, read_workspace_tree(workspace.local_path))

    assert outcome.state is ContextState.STALE
    assert outcome.stale_reason is StaleReason.WORKING_TREE
    assert [item.path for item in outcome.covered_divergences] == ["src/app.py"]


# ══════════════════ E4-AUD2-004 — `UNRESOLVED` apagava a cobertura conhecida


def test_aud2_004_reproducao_unknown_preserva_a_cobertura_do_ref_que_resolveu(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """**Reprodução exata do finding.**

    `["src/**", "missing/**"]` com renames sob `src/`. O `missing/**` não resolve, então o
    estado final é `unknown` — correto. Mas o `src/**` resolveu perfeitamente, e havia
    divergência real sob `src/`: a versão anterior devolvia cedo e a evidência sumia.

    "Não sei se está tudo coberto" e "não vou dizer o que sei" são respostas diferentes.
    """
    entry = _entry(session, workspace, ["src/**", "missing/**"])
    assert entry.state is ContextState.UNKNOWN, "o ref irresolúvel domina o estado"

    git(repo_path, "mv", "src/app.py", "src/main.py")

    outcome = verify_freshness(entry, read_workspace_tree(workspace.local_path))

    assert outcome.state is ContextState.UNKNOWN
    assert outcome.stale_reason is None
    assert [item.path for item in outcome.covered_divergences] == ["src/app.py", "src/main.py"]


def test_aud2_004_a_cobertura_preservada_e_a_real_nao_tudo(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """Precisão: preservar a evidência não pode virar "reporta tudo o que está sujo"."""
    entry = _entry(session, workspace, ["src/**", "missing/**"])

    write(repo_path, "src/app.py", "print('coberto')\n")
    write(repo_path, "README.md", "# fora da cobertura\n")

    outcome = verify_freshness(entry, read_workspace_tree(workspace.local_path))

    assert outcome.state is ContextState.UNKNOWN
    assert [item.path for item in outcome.covered_divergences] == ["src/app.py"]
    assert outcome.dirty_file_count == 2, "a divergência do workspace inteiro segue registrada"


def test_aud2_004_sem_git_continua_unknown_sem_cobertura(
    session: Session, workspace_sem_git: DevWorkspace
) -> None:
    """O outro `unknown`: sem repositório não há evidência a preservar, e não se inventa."""
    entry = _entry(session, workspace_sem_git, ["src/**"])

    outcome = verify_freshness(entry, read_workspace_tree(workspace_sem_git.local_path))

    assert outcome.state is ContextState.UNKNOWN
    assert outcome.covered_divergences == ()
    assert outcome.dirty_file_count is None


def test_aud2_004_pela_rota_http(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """A cobertura preservada chega à resposta da verificação, não só ao objeto interno."""
    from app.context_engine.verification import verify_workspace_entries

    _entry(session, workspace, ["src/**", "missing/**"])
    git(repo_path, "mv", "src/app.py", "src/main.py")

    snapshot, resultados = verify_workspace_entries(session, workspace)

    assert snapshot.verification_commit is not None
    _entrada, outcome = resultados[0]
    assert outcome.state is ContextState.UNKNOWN
    assert {item.path for item in outcome.covered_divergences} == {"src/app.py", "src/main.py"}
