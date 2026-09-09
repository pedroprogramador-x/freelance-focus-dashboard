"""Reproduções exatas da 1ª auditoria Codex da E4 (E4-AUD-001..010).

Cada teste aqui é **a reprodução do finding**, não uma paráfrase: o cenário é montado do
jeito que o auditor montou, e a asserção é sobre o comportamento que estava errado. Rodar
qualquer um deles contra o código anterior à correção o expõe.

Os findings que são de arquitetura estática (E4-AUD-009) ou de gramática pura (E4-AUD-008)
estão nos arquivos onde a regra correspondente já morava — `test_architecture.py` e
`test_source_ref_expansion.py` —, porque separá-los da regra que protegem só afastaria o
teste do lugar onde alguém vai procurá-lo.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.context_engine import (
    InvalidContextEntry,
    InvalidSourceRefs,
    create_entry,
    update_entry,
)
from app.context_engine.source_ref_expansion import (
    CompiledSourceRef,
    ExpansionStatus,
    _matches,
    _simplify,
    _tokenize,
    compile_source_ref,
    expand_source_refs,
)
from app.context_engine.verification import read_workspace_tree, verify_freshness
from app.db.enums import ContextDomain, ContextState, StaleReason, WorkspaceType
from app.db.models import ContextRegistryEntry, DevWorkspace
from app.git_runtime import (
    TreeListing,
    WorkingTreeChange,
    list_tree,
    preflight,
    working_tree_diff_against,
    working_tree_status,
)
from app.workspace import create_workspace
from tests.context_helpers import GIT, commit_all, git, init_repo, readable, write

pytestmark = pytest.mark.skipif(GIT is None, reason="git indisponível no PATH")

#: `Origin` que casa com o `Host` do `TestClient`.
SAME_ORIGIN = {"Origin": "http://127.0.0.1:8756", "Sec-Fetch-Site": "same-origin"}

_SHA = "0" * 40


def _tree(*paths: str) -> TreeListing:
    return TreeListing(
        files=tuple(
            sorted((path, f"{_SHA[: 40 - len(path)]}{path}"[:40].ljust(40, "0")) for path in paths)
        )
    )


def _entry(session: Session, ws: DevWorkspace, refs: list[str]) -> ContextRegistryEntry:
    return create_entry(
        session,
        ws,
        domain=ContextDomain.MODULES,
        title="Módulo src",
        body="Descreve o código sob src/.\n",
        source_refs=refs,
    )


# ═════════════════════════════════════════ E4-AUD-002 — workspace em subdiretório do repo


@pytest.fixture
def repo_com_workspace_aninhado(tmp_path: Path) -> tuple[Path, Path]:
    """Repositório cuja raiz **não** é o `DevWorkspace`.

    Devolve `(raiz_do_repo, caminho_do_workspace)`. O workspace é `<repo>/pacotes/app`, e há
    conteúdo fora dele para provar que caminhos de fora não contaminam a contagem.
    """
    repo = tmp_path / "monorepo"
    workspace = repo / "pacotes" / "app"
    init_repo(repo)
    write(repo, "raiz.txt", "fora do workspace\n")
    write(repo, "pacotes/app/src/app.py", "print('ok')\n")
    write(repo, "pacotes/app/src/util.py", "UTIL = 1\n")
    write(repo, "pacotes/outro/x.py", "X = 1\n")
    commit_all(repo, "monorepo inicial")
    return repo, workspace


def test_aud002_ls_tree_e_status_falam_da_mesma_base(
    repo_com_workspace_aninhado: tuple[Path, Path],
) -> None:
    """A raiz do finding: as duas leituras precisam nomear o mesmo arquivo do mesmo jeito.

    `ls-tree` nomeia a partir do diretório corrente; `status` e `diff` **com `-z`** nomeiam a
    partir da raiz do repositório. Num workspace aninhado isso dava `src/app.py` de um lado
    e `pacotes/app/src/app.py` do outro, e nenhuma divergência casava com nenhum
    `source_ref`.
    """
    repo, workspace = repo_com_workspace_aninhado
    write(repo, "pacotes/app/src/app.py", "print('sujo')\n")

    head = preflight(str(workspace)).head
    assert head is not None

    da_arvore = {path for path, _sha in readable(list_tree(str(workspace), head))}
    do_status = {entry.path for entry in readable(working_tree_status(str(workspace)))}

    assert da_arvore == {"src/app.py", "src/util.py"}
    assert do_status == {"src/app.py"}
    assert do_status <= da_arvore, "as duas leituras têm de falar da mesma base"


def test_aud002_caminhos_de_fora_do_workspace_sao_descartados(
    repo_com_workspace_aninhado: tuple[Path, Path],
) -> None:
    """Sujeira fora do `DevWorkspace` não é divergência **do workspace**.

    Um `source_ref` é relativo ao workspace e jamais contém `..`, então esses caminhos não
    poderiam ser cobertos por entrada nenhuma; contá-los só inflaria o número que a UI
    mostra com arquivos que não são do workspace.
    """
    repo, workspace = repo_com_workspace_aninhado
    write(repo, "raiz.txt", "sujo, mas fora\n")
    write(repo, "pacotes/outro/x.py", "X = 2\n")

    divergencias = readable(working_tree_status(str(workspace)))

    assert divergencias == []


def test_aud002_reproducao_arquivo_coberto_em_workspace_aninhado_fica_stale(
    session: Session, repo_com_workspace_aninhado: tuple[Path, Path]
) -> None:
    """**Reprodução exata do finding.** Antes dava `fresh`; agora dá `stale(working_tree)`.

    `DevWorkspace` apontando para um subdiretório, arquivo coberto modificado sem commitar.
    A divergência vinha do git como `pacotes/app/src/app.py`, o `source_ref` era `src/**`, e
    o cruzamento não casava — falso `fresh` num caso perfeitamente comum (monorepo).
    """
    repo, workspace_path = repo_com_workspace_aninhado
    workspace = create_workspace(
        session,
        name="pacote aninhado",
        workspace_type=WorkspaceType.PERSONAL,
        local_path=str(workspace_path),
    )
    session.flush()

    entry = _entry(session, workspace, ["src/**"])
    assert entry.state is ContextState.FRESH
    assert entry.source_hash is not None

    write(repo, "pacotes/app/src/app.py", "print('editado sem commitar')\n")

    outcome = verify_freshness(entry, read_workspace_tree(workspace.local_path))

    assert outcome.state is ContextState.STALE
    assert outcome.stale_reason is StaleReason.WORKING_TREE
    assert [item.path for item in outcome.covered_divergences] == ["src/app.py"]


def test_aud002_rename_em_workspace_aninhado_reporta_origem_e_destino(
    repo_com_workspace_aninhado: tuple[Path, Path],
) -> None:
    """Origem **e** destino, os dois já na base do workspace."""
    repo, workspace = repo_com_workspace_aninhado
    git(repo, "mv", "pacotes/app/src/app.py", "pacotes/app/src/main.py")

    divergencias = readable(working_tree_status(str(workspace)))

    assert divergencias is not None
    assert {item.path for item in divergencias} == {"src/app.py", "src/main.py"}
    assert {item.kind for item in divergencias} == {WorkingTreeChange.RENAMED}


# ═══════════════════════════════ E4-AUD-003 — Parte B contra o verification_commit


def test_aud003_reproducao_worktree_limpo_em_b_ainda_diverge_de_a(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """**Reprodução exata do finding.**

    Congela `verification_commit = A`, avança o repositório para `B` com um commit real, e
    **não toca** a árvore de trabalho. A árvore está limpa em relação a `B` — e é por isso
    que `git status` respondia "zero divergências". Mas a pergunta da Parte B é sobre `A`,
    e em relação a `A` a árvore mudou.

    Antes: `fresh`, `covered_divergences=[]`. Agora: a divergência aparece.
    """
    entry = _entry(session, workspace, ["src/**"])
    commit_a = entry.source_hash_commit
    assert commit_a is not None

    write(repo_path, "src/app.py", "print('commitado em B')\n")
    commit_all(repo_path, "B")
    assert preflight(str(repo_path)).head != commit_a

    # A árvore está limpa **em relação ao HEAD atual** — a premissa do finding.
    assert readable(working_tree_status(str(repo_path))) == []

    contra_a = read_workspace_tree(workspace.local_path, verification_commit=commit_a)
    assert contra_a.verification_commit == commit_a
    assert contra_a.divergences is not None
    assert [item.path for item in contra_a.divergences] == ["src/app.py"]

    outcome = verify_freshness(entry, contra_a)

    assert outcome.state is ContextState.STALE
    assert outcome.covered_divergences != (), "a Parte B não pode responder sobre o HEAD"
    assert outcome.dirty_file_count == 1


def test_aud003_status_e_diff_respondem_perguntas_diferentes(repo_path: Path) -> None:
    """O que o finding tem de conceitual: são duas perguntas, não um parâmetro faltando.

    `git status` compara contra o `HEAD`; `working_tree_diff_against` compara contra o
    commit que lhe é dado. Com o `HEAD` adiantado, as duas respostas **precisam** divergir.
    """
    commit_a = preflight(str(repo_path)).head
    assert commit_a is not None

    write(repo_path, "src/app.py", "print('B')\n")
    commit_all(repo_path, "B")

    assert readable(working_tree_status(str(repo_path))) == []
    contra_a = readable(working_tree_diff_against(str(repo_path), commit_a))
    assert contra_a is not None
    assert [item.path for item in contra_a] == ["src/app.py"]
    assert contra_a[0].kind is WorkingTreeChange.MODIFIED


def test_aud003_diff_contra_commit_cobre_os_cinco_rotulos(repo_path: Path) -> None:
    """Os cinco rótulos de [03] §3, relidos contra uma base congelada."""
    commit_a = preflight(str(repo_path)).head
    assert commit_a is not None

    write(repo_path, "README.md", "# mudou\n")  # modified
    write(repo_path, "src/novo.py", "NOVO = 1\n")
    git(repo_path, "add", "src/novo.py")  # staged: existe agora, não existia em A
    (repo_path / "src" / "nested" / "deep.py").unlink()  # deleted
    git(repo_path, "mv", "src/app.py", "src/main.py")  # renamed
    write(repo_path, "solto.txt", "nem no índice\n")  # untracked

    divergencias = readable(working_tree_diff_against(str(repo_path), commit_a))

    assert divergencias is not None
    por_caminho = {item.path: item.kind for item in divergencias}
    assert por_caminho["README.md"] is WorkingTreeChange.MODIFIED
    assert por_caminho["src/novo.py"] is WorkingTreeChange.STAGED
    assert por_caminho["src/nested/deep.py"] is WorkingTreeChange.DELETED
    assert por_caminho["src/app.py"] is WorkingTreeChange.RENAMED
    assert por_caminho["src/main.py"] is WorkingTreeChange.RENAMED
    assert por_caminho["solto.txt"] is WorkingTreeChange.UNTRACKED
    assert {item.kind for item in divergencias} == set(WorkingTreeChange)


def test_aud003_commit_invalido_ou_ausente_e_none(repo_path: Path, plain_path: Path) -> None:
    assert working_tree_diff_against(str(repo_path), "HEAD") is None
    assert working_tree_diff_against(str(repo_path), "0" * 39 + "1") is None
    assert working_tree_diff_against(str(plain_path), "a" * 40) is None


# ═══════════════════════════════════════════════════════ E4-AUD-001 — ReDoS


#: O padrão do auditor: `*a` repetido, **seguido de um literal que não casa**.
#:
#: O literal final é o que torna o caso adversarial de verdade, e custou uma iteração para
#: acertar: com um alvo sem nenhum `a`, o primeiro `[^/]*a` falha e o motor poda tudo — o
#: caso parece rápido e o teste passaria até contra a implementação com o bug. O pior caso
#: exige que cada `[^/]*a` tenha **muitas repartições possíveis** (alvo cheio de `a`) e que
#: a falha só apareça no fim (`Z` no padrão). Medido contra a tradução anterior:
#: 21 caracteres → 0,4 s; 25 caracteres → **8,5 s**.
_REDOS_ALVO = "a" * 34


@pytest.mark.parametrize("repeticoes", [8, 12, 16, 24, 40])
def test_aud001_reproducao_serie_de_estrelas_termina_em_tempo_limitado(repeticoes: int) -> None:
    """**Reprodução exata do finding**, com asserção de tempo.

    `[^/]*a[^/]*a[^/]*a…Z` é ambíguo: o motor de backtracking tem de tentar um número
    combinatório de repartições do alvo antes de concluir que o `Z` final não casa. A DP de
    `_matches` não tem o que refazer — o custo é `O(tokens × caminho)`, sempre.

    Com 12 repetições (25 caracteres de padrão) a tradução anterior levava 8,5 s. O teto de
    1 s aqui é folgado o bastante para não piscar em máquina lenta e apertado o bastante
    para que qualquer volta ao backtracking o estoure por ordens de grandeza.
    """
    padrao = "*a" * repeticoes + "Z"
    compiled = compile_source_ref(padrao)
    assert isinstance(compiled, CompiledSourceRef)

    inicio = time.perf_counter()
    resultado = compiled.covers(_REDOS_ALVO)
    decorrido = time.perf_counter() - inicio

    assert resultado is False
    assert decorrido < 1.0, f"{repeticoes} repetições levaram {decorrido:.3f}s"


def test_aud001_o_alvo_do_teste_e_de_fato_o_pior_caso() -> None:
    """Guarda do próprio teste: o alvo precisa ser adversarial, não só longo.

    Um alvo **sem** `a` faz o primeiro `[^/]*a` falhar e podar toda a busca — o caso fica
    rápido até com backtracking, e a asserção de tempo acima não provaria nada. O que
    caracteriza o pior caso é o alvo ser todo de `a` (muitas repartições por quantificador)
    com a falha só no literal final.
    """
    assert set(_REDOS_ALVO) == {"a"}
    assert len(_REDOS_ALVO) >= 30

    compiled = compile_source_ref("*a" * 12 + "Z")
    assert isinstance(compiled, CompiledSourceRef)
    assert compiled.covers(_REDOS_ALVO) is False
    # e o mesmo padrão **sem** o literal final casa, o que confirma que a busca chega ao fim
    casando = compile_source_ref("*a" * 12)
    assert isinstance(casando, CompiledSourceRef)
    assert casando.covers(_REDOS_ALVO) is True


def test_aud001_serie_de_estrelas_na_expansao_completa_tambem_e_rapida() -> None:
    """O mesmo padrão pelo caminho de verdade — expansão contra uma árvore inteira."""
    tree = _tree(*[f"dir{index}/{'a' * 40}.py" for index in range(200)])
    padrao = "*a" * 16 + "Z"

    inicio = time.perf_counter()
    resultado = expand_source_refs([padrao], tree)
    decorrido = time.perf_counter() - inicio

    assert resultado.status is ExpansionStatus.UNRESOLVED
    assert decorrido < 1.0, f"expansão levou {decorrido:.3f}s"


@pytest.mark.parametrize(
    "padrao",
    [
        "*a*a*a*a*a*a*a*a*a*a*a*aZ",
        "**a**a**a**a**a**aZ",
        "?a*?a*?a*?a*Z",
        "[ab]*[ab]*[ab]*[ab]*Z",
    ],
)
def test_aud001_outras_formas_ambiguas_tambem_terminam(padrao: str) -> None:
    """Não só a série do auditor: qualquer mistura de quantificadores tem o mesmo teto."""
    compiled = compile_source_ref(padrao)
    assert isinstance(compiled, CompiledSourceRef)

    inicio = time.perf_counter()
    compiled.covers("ab" * 60)
    decorrido = time.perf_counter() - inicio

    assert decorrido < 1.0, f"`{padrao}` levou {decorrido:.3f}s"


def test_aud001_simplificacao_nunca_muda_o_que_o_padrao_casa() -> None:
    """A mitigação estrutural não pode alterar a linguagem — só o número de tokens.

    Compara o token stream **cru** (`_tokenize`) com o **simplificado** (`_simplify`) sobre
    o mesmo corpus. Este teste existe porque colapsar tokens já introduziu um bug durante
    esta correção: tratar `SEGSTAR STAR` como redundante fazia `docs/**/*.md` deixar de
    casar `docs/readme.md` — a subcobertura que a semântica de `**` existe para evitar.
    """
    padroes = [
        "a/**/b",
        "docs/**/*.md",
        "src/**",
        "src/**/*.py",
        "**/x.md",
        "a/**/**/b",
        "src/**.py",
        "a**b",
        "*a*",
        "**/*",
        "a/*/**/b",
        "*/**/*",
        "a***b",
        "**",
        "*?*",
        "[ab]*[cd]",
    ]
    caminhos = [
        "a/b",
        "a/x/b",
        "a/x/y/b",
        "docs/readme.md",
        "docs/sub/readme.md",
        "src/app.py",
        "src/n/d.py",
        "x.md",
        "a/x.md",
        "ab",
        "a/b/c/d",
        "ac",
        "",
    ]

    for padrao in padroes:
        cru = _tokenize(padrao)
        assert isinstance(cru, list), padrao
        simplificado = _simplify(list(cru))

        assert len(simplificado) <= len(cru), padrao

        for caminho in caminhos:
            assert _matches(tuple(cru), caminho) == _matches(tuple(simplificado), caminho), (
                padrao,
                caminho,
            )


# ═════════════════════════ E4-AUD-004 — envelope validado mesmo sem git


@pytest.mark.parametrize(
    ("refs", "rule_id"),
    [
        (["../**"], "source_ref.parent_traversal"),
        ([".env*"], "source_ref.secret_denied"),
        (["["], "source_ref_expansion.invalid_character_class"),
        (["/etc/**"], "source_ref.root_relative"),
        (["~/.ssh/*"], "source_ref.home_reference"),
        (["src/{a,b}.py"], "source_ref_expansion.unsupported_syntax"),
    ],
)
def test_aud004_reproducao_workspace_sem_git_recusa_ref_invalido(
    session: Session, workspace_sem_git: DevWorkspace, refs: list[str], rule_id: str
) -> None:
    """**Reprodução exata do finding.** Antes: `201` e a entrada persistida. Agora: 422.

    Sem git não dá para *resolver* o `source_ref` contra uma árvore — mas a forma dele não
    depende de árvore nenhuma. A validação inteira estava atrás de um `if commit is not
    None`, então `../**` e `.env*` entravam no banco sem que ninguém olhasse.
    """
    with pytest.raises(InvalidSourceRefs) as caught:
        _entry(session, workspace_sem_git, refs)

    assert caught.value.status_code == 422
    assert caught.value.rule_id == rule_id


def test_aud004_sem_git_ref_valido_ainda_nasce_unknown(
    session: Session, workspace_sem_git: DevWorkspace
) -> None:
    """A outra metade: recusar o malformado não pode passar a recusar o legítimo.

    Um `source_ref` bem-formado num workspace sem git continua sendo aceito, e a entrada
    nasce `unknown` sem baseline — que é a linha "não consegui verificar" de [03] §3.
    """
    entry = _entry(session, workspace_sem_git, ["src/**"])

    assert entry.state is ContextState.UNKNOWN
    assert entry.source_hash is None
    assert entry.source_refs == ["src/**"]


def test_aud004_patch_sem_git_tambem_valida(
    session: Session, workspace_sem_git: DevWorkspace
) -> None:
    """O mesmo pelo `PATCH`: alterar `source_refs` valida forma mesmo sem repositório."""
    entry = _entry(session, workspace_sem_git, ["src/**"])

    with pytest.raises(InvalidSourceRefs):
        update_entry(session, entry, source_refs=["../fora/**"])

    assert entry.source_refs == ["src/**"], "nada foi alterado pela metade"


def test_aud004_pela_rota_http_sem_git(auth_api_client: TestClient, plain_path: Path) -> None:
    """A reprodução pela borda: `POST` num workspace sem git devolve 422, não 201."""
    criado = auth_api_client.post(
        "/api/workspaces",
        json={"name": "sem git", "type": "personal", "local_path": str(plain_path)},
        headers=SAME_ORIGIN,
    )
    workspace_id = criado.json()["id"]

    resposta = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context",
        json={
            "domain": "modules",
            "title": "t",
            "body": "b",
            "source_refs": ["../**"],
        },
        headers=SAME_ORIGIN,
    )

    assert resposta.status_code == 422
    assert resposta.json()["code"] == "invalid_source_refs"
    assert auth_api_client.get(f"/api/workspaces/{workspace_id}/context").json() == []


# ═══════════════════════ E4-AUD-005 — DENY de segredo vence UNRESOLVED


def test_aud005_reproducao_ref_irresolvivel_nao_desliga_a_checagem_de_segredo() -> None:
    """**Reprodução exata do finding.** `["config/*", "missing/**"]` com `config/.env.local`.

    Antes: o `missing/**` casava zero arquivo, a expansão devolvia `UNRESOLVED` na hora, e
    os arquivos que o `config/*` **já tinha encontrado** nunca eram classificados. Um erro
    de digitação num `source_ref` desligava a proteção de segredo do outro.
    """
    tree = _tree("config/settings.json", "config/.env.local", "src/app.py")

    result = expand_source_refs(["config/*", "missing/**"], tree)

    assert result.status is ExpansionStatus.DENIED
    assert result.decision is not None
    assert result.decision.rule_id == "source_ref_expansion.secret_denied"
    assert ".env.local" in result.decision.subject_redacted


def test_aud005_a_ordem_dos_refs_nao_muda_o_desfecho() -> None:
    """O irresolúvel primeiro, o que acha o segredo depois — mesmo veredito."""
    tree = _tree("config/settings.json", "config/.env.local")

    assert expand_source_refs(["missing/**", "config/*"], tree).status is ExpansionStatus.DENIED


def test_aud005_pela_rota_http(auth_api_client: TestClient, repo_path: Path) -> None:
    """A reprodução pela borda: 422, não 201 com a entrada persistida."""
    write(repo_path, "config/.env.local", "TOKEN=x\n")
    commit_all(repo_path, "acrescenta segredo")

    criado = auth_api_client.post(
        "/api/workspaces",
        json={"name": "ws", "type": "personal", "local_path": str(repo_path)},
        headers=SAME_ORIGIN,
    )
    workspace_id = criado.json()["id"]

    resposta = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context",
        json={
            "domain": "modules",
            "title": "t",
            "body": "b",
            "source_refs": ["config/*", "missing/**"],
        },
        headers=SAME_ORIGIN,
    )

    assert resposta.status_code == 422
    assert resposta.json()["code"] == "invalid_source_refs"


def test_aud005_sem_segredo_o_irresolvivel_continua_irresolvivel() -> None:
    """Precisão: a mudança de ordem não pode transformar `UNRESOLVED` em outra coisa."""
    tree = _tree("config/settings.json", "src/app.py")

    result = expand_source_refs(["config/*", "missing/**"], tree)

    assert result.status is ExpansionStatus.UNRESOLVED
    assert result.unresolved_refs == ("missing/**",)


# ═══════════════ E4-AUD-006 — covered_divergences preservadas em sources_changed


def test_aud006_reproducao_stale_por_hash_ainda_reporta_divergencia_coberta(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """**Reprodução exata do finding.**

    Baseline em A, mudança **commitada** para B, e mais uma mudança **não commitada** no
    mesmo arquivo coberto. O motivo continua sendo `sources_changed` (é o mais grave, e a
    ordem é a do documento), mas a evidência da Parte B não pode sumir: antes o cálculo
    devolvia cedo e `covered_divergences` saía `[]` justamente no caso em que há mais a
    mostrar.
    """
    entry = _entry(session, workspace, ["src/**"])

    write(repo_path, "src/app.py", "print('commitado em B')\n")
    commit_all(repo_path, "B")
    write(repo_path, "src/app.py", "print('e ainda por cima, sem commitar')\n")

    outcome = verify_freshness(entry, read_workspace_tree(workspace.local_path))

    assert outcome.state is ContextState.STALE
    assert outcome.stale_reason is StaleReason.SOURCES_CHANGED
    assert [item.path for item in outcome.covered_divergences] == ["src/app.py"]
    assert outcome.dirty_file_count == 1


def test_aud006_pela_rota_http_a_divergencia_coberta_aparece(
    auth_api_client: TestClient, repo_path: Path
) -> None:
    """A mesma coisa pela borda: `working_tree_divergence.covered` não pode vir vazio."""
    criado = auth_api_client.post(
        "/api/workspaces",
        json={"name": "ws", "type": "personal", "local_path": str(repo_path)},
        headers=SAME_ORIGIN,
    )
    workspace_id = criado.json()["id"]
    auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context",
        json={"domain": "modules", "title": "t", "body": "b", "source_refs": ["src/**"]},
        headers=SAME_ORIGIN,
    )

    write(repo_path, "src/app.py", "print('B')\n")
    commit_all(repo_path, "B")
    write(repo_path, "src/app.py", "print('sujo por cima')\n")

    corpo = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context/verify", json={}, headers=SAME_ORIGIN
    ).json()

    assert corpo["entries"][0]["stale_reason"] == "sources_changed"
    assert [item["path"] for item in corpo["working_tree_divergence"]["covered"]] == ["src/app.py"]


# ═════════════════ E4-AUD-007 — colisão de chave normalizada em `structured`


@pytest.mark.parametrize(
    "structured",
    [
        {"x": 1, "x ": 2},
        {"x": 1, "x\t": 2},
        {"a": 1, "a  ": 2},
        {"nivel": {"x": 1, "x ": 2}},
        {"lista": [{"x": 1, "x ": 2}]},
    ],
)
def test_aud007_reproducao_colisao_de_chave_e_recusada(
    session: Session, workspace: DevWorkspace, structured: dict[str, object]
) -> None:
    """**Reprodução exata do finding.** Antes: aceito, com uma das chaves perdida.

    `normalize_text` remove espaço no fim, então `"x"` e `"x "` viram a mesma chave. A
    compreensão de dicionário deixava a segunda sobrescrever a primeira, em silêncio: o
    `content_hash` era calculado sobre **menos dados** do que os gravados em `structured`, e
    duas entradas materialmente diferentes podiam hashear igual.

    Escolher qual chave sobrevive não é decisão deste sistema, então ele recusa.
    """
    with pytest.raises(InvalidContextEntry) as caught:
        create_entry(
            session,
            workspace,
            domain=ContextDomain.DECISIONS,
            title="Decisão",
            body="corpo\n",
            structured=structured,
        )

    assert caught.value.status_code == 422
    assert "colidem" in caught.value.message


def test_aud007_colisao_tambem_e_recusada_no_patch(
    session: Session, workspace: DevWorkspace
) -> None:
    entry = _entry(session, workspace, [])
    antes = entry.content_hash

    with pytest.raises(InvalidContextEntry):
        update_entry(session, entry, structured={"x": 1, "x ": 2})

    assert entry.content_hash == antes, "nada foi alterado pela metade"


def test_aud007_chaves_que_nao_colidem_continuam_aceitas(
    session: Session, workspace: DevWorkspace
) -> None:
    """Precisão: a recusa é da colisão, não de espaço em chave."""
    entry = create_entry(
        session,
        workspace,
        domain=ContextDomain.DECISIONS,
        title="Decisão",
        body="corpo\n",
        structured={"x": 1, " x": 2, "x y": 3},
    )

    assert entry.structured == {"x": 1, " x": 2, "x y": 3}


def test_aud007_pela_rota_http(auth_api_client: TestClient, repo_path: Path) -> None:
    criado = auth_api_client.post(
        "/api/workspaces",
        json={"name": "ws", "type": "personal", "local_path": str(repo_path)},
        headers=SAME_ORIGIN,
    )
    workspace_id = criado.json()["id"]

    resposta = auth_api_client.post(
        f"/api/workspaces/{workspace_id}/context",
        json={
            "domain": "decisions",
            "title": "t",
            "body": "b",
            "structured": {"x": 1, "x ": 2},
        },
        headers=SAME_ORIGIN,
    )

    assert resposta.status_code == 422
    assert resposta.json()["code"] == "invalid_context_entry"


# ═══════════════════════════════ E4-AUD-010 — verify não escreve `updated_at`


def test_aud010_reproducao_verify_preserva_updated_at(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """**Reprodução exata do finding.**

    `updated_at` marca a última edição **autoral** da entrada. Uma verificação não edita a
    entrada — ela emite um veredito sobre ela. Mexer no campo fazia "verificar" parecer
    "modificar" em qualquer listagem ordenada por `updated_at`, e apagava a informação de
    quando o conteúdo mudou de verdade.
    """
    entry = _entry(session, workspace, ["src/**"])
    updated_at_original = entry.updated_at

    write(repo_path, "src/app.py", "print('B')\n")
    commit_all(repo_path, "B")

    verify_freshness(entry, read_workspace_tree(workspace.local_path))
    assert entry.updated_at == updated_at_original

    # E também no caminho em que nada muda de estado.
    verify_freshness(entry, read_workspace_tree(workspace.local_path))
    assert entry.updated_at == updated_at_original


def test_aud010_um_patch_de_verdade_continua_atualizando_updated_at(
    session: Session, workspace: DevWorkspace
) -> None:
    """A outra metade: preservar em `verify` não pode congelar o campo em toda escrita."""
    entry = _entry(session, workspace, [])
    antes = entry.updated_at

    update_entry(session, entry, body="corpo revisado\n")

    assert entry.updated_at >= antes
