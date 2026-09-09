"""Gate 5 da E4 — `verify_freshness`, que **lê** o baseline e nunca o escreve.

Os três testes combinados para esta sub-etapa, mais o que os torna significativos:

1. **`verify` não cura `stale`.** Baseline em A, código muda para B, `verify` →
   `stale(sources_changed)`; `source_hash`/`source_hash_commit` continuam **em A**;
   `last_verified_commit` é **B**; um segundo `verify` continua `stale`.
2. **`PATCH` sem tocar `source_refs` não altera o baseline** — reconfirmado aqui dentro do
   fluxo de verificação, e não só no CRUD.
3. **Sem git / repositório inexistente → `unknown`.**
4. **Caminho coberto divergente no working tree, sem commitar → `stale(working_tree)`.**

E o invariante estrutural que sustenta os quatro: `verify_freshness` **recebe** o
`verification_commit` e nunca lê `HEAD` por conta própria ([03] §3). É o que vai permitir à
E6 chamá-la com o `planning_base_commit` sem duplicar lógica.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.context_engine.service import create_entry, update_entry
from app.context_engine.verification import (
    FreshnessOutcome,
    read_workspace_tree,
    verify_freshness,
    verify_workspace_entries,
)
from app.db.enums import ContextDomain, ContextState, StaleReason
from app.db.models import ContextRegistryEntry, DevWorkspace
from app.git_runtime import WorkingTreeChange, preflight
from tests.context_helpers import GIT, commit_all, git, write

pytestmark = pytest.mark.skipif(GIT is None, reason="git indisponível no PATH")


def state_of(entry: ContextRegistryEntry) -> ContextState:
    """Lê `entry.state` **através de uma função**, para não perder o tipo declarado.

    O mypy estreita `entry.state` para o literal da primeira asserção e não tem como saber
    que `verify_freshness` reescreve o atributo depois; a partir daí toda asserção seguinte
    vira "comparação impossível". Ler por função devolve `ContextState` cheio e permite que
    os testes afirmem, em sequência, sobre a **entrada persistida** — que é onde o veredito
    de fato tem de aparecer.
    """
    return entry.state


def reason_of(entry: ContextRegistryEntry) -> StaleReason | None:
    """Mesmo motivo de `state_of`, para `stale_reason`."""
    return entry.stale_reason


def _entry(
    session: Session,
    ws: DevWorkspace,
    refs: list[str],
    *,
    title: str = "Módulo src",
) -> ContextRegistryEntry:
    return create_entry(
        session,
        ws,
        domain=ContextDomain.MODULES,
        title=title,
        body="Descreve o código sob src/.\n",
        source_refs=refs,
    )


def _verify(session: Session, ws: DevWorkspace, entry: ContextRegistryEntry) -> FreshnessOutcome:
    """Verificação sob demanda da E4: o `HEAD` é lido **uma vez**, pelo chamador."""
    del session
    return verify_freshness(entry, read_workspace_tree(ws.local_path))


# ============================================ 1 — verify NÃO cura stale (o teste central)


def test_verify_nao_cura_stale(session: Session, workspace: DevWorkspace, repo_path: Path) -> None:
    """O baseline fica onde está; só o veredito e o carimbo de verificação mudam.

    Se `verify()` reescrevesse `source_hash`, a entrada voltaria a `fresh` na primeira
    verificação depois da mudança — o baseline andaria junto com o código e a divergência
    sumiria exatamente quando alguém foi conferi-la. É o defeito que [03] §3 tornou
    normativo impedir no planejamento desta fase.
    """
    entry = _entry(session, workspace, ["src/**"])
    baseline_a = entry.source_hash
    commit_a = entry.source_hash_commit
    assert baseline_a is not None
    assert state_of(entry) is ContextState.FRESH

    # O código muda e é commitado: o repositório está em B.
    write(repo_path, "src/app.py", "print('versão B')\n")
    commit_all(repo_path, "muda para B")
    commit_b = preflight(str(repo_path)).head
    assert commit_b != commit_a

    primeiro = _verify(session, workspace, entry)

    assert state_of(entry) is ContextState.STALE
    assert reason_of(entry) is StaleReason.SOURCES_CHANGED
    # o baseline **continua em A**
    assert entry.source_hash == baseline_a
    assert entry.source_hash_commit == commit_a
    # e o carimbo da verificação é **B**
    assert entry.last_verified_commit == commit_b
    assert entry.last_verified_at is not None
    assert primeiro is not None

    carimbo_primeiro = entry.last_verified_at

    # segundo `verify`, sem nada ter mudado: continua stale, pelo mesmo motivo
    _verify(session, workspace, entry)

    assert state_of(entry) is ContextState.STALE
    assert reason_of(entry) is StaleReason.SOURCES_CHANGED
    assert entry.source_hash == baseline_a
    assert entry.source_hash_commit == commit_a
    assert entry.last_verified_commit == commit_b
    assert entry.last_verified_at >= carimbo_primeiro


def test_verify_nao_escreve_nenhum_campo_alem_dos_quatro_autorizados(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """[03] §3 autoriza `state`, `stale_reason`, `last_verified_at`, `last_verified_commit`.

    Todo o resto — inclusive `content_hash`, `source_refs`, `body`, `title` — tem de sair
    da verificação byte a byte igual ao que entrou.
    """
    entry = _entry(session, workspace, ["src/**"])
    write(repo_path, "src/app.py", "print('B')\n")
    commit_all(repo_path, "B")

    congelados = {
        "workspace_id": entry.workspace_id,
        "domain": entry.domain,
        "title": entry.title,
        "body": entry.body,
        "structured": entry.structured,
        "tags": list(entry.tags),
        "source_refs": list(entry.source_refs),
        "content_hash": entry.content_hash,
        "source_hash": entry.source_hash,
        "source_hash_commit": entry.source_hash_commit,
        "origin": entry.origin,
        "created_at": entry.created_at,
    }

    _verify(session, workspace, entry)

    for campo, valor in congelados.items():
        atual = getattr(entry, campo)
        assert (list(atual) if isinstance(atual, list) else atual) == valor, campo


def test_voltar_o_codigo_ao_baseline_devolve_fresh(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """`stale` não é irreversível — é só que **o código** precisa voltar, não o baseline.

    O par é: `verify` sozinho nunca cura; desfazer a mudança cura, porque o hash volta a
    conferir contra o mesmo baseline armazenado.
    """
    entry = _entry(session, workspace, ["src/app.py"])
    baseline = entry.source_hash

    write(repo_path, "src/app.py", "print('B')\n")
    commit_all(repo_path, "B")
    _verify(session, workspace, entry)
    assert state_of(entry) is ContextState.STALE

    write(repo_path, "src/app.py", "print('ok')\n")  # conteúdo original
    commit_all(repo_path, "volta para A")
    _verify(session, workspace, entry)

    assert state_of(entry) is ContextState.FRESH
    assert reason_of(entry) is None
    assert entry.source_hash == baseline, "o baseline nunca se moveu"


# ============================================ 2 — PATCH de conteúdo dentro do fluxo de verify


def test_patch_de_conteudo_entre_verificacoes_nao_altera_o_baseline(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """Confirmação do gate 4, agora intercalada com verificações de verdade."""
    entry = _entry(session, workspace, ["src/**"])
    baseline = entry.source_hash
    commit_baseline = entry.source_hash_commit

    _verify(session, workspace, entry)
    assert state_of(entry) is ContextState.FRESH

    write(repo_path, "src/app.py", "print('B')\n")
    commit_all(repo_path, "B")
    _verify(session, workspace, entry)
    assert state_of(entry) is ContextState.STALE

    update_entry(session, entry, title="Título revisado", body="Corpo revisado.\n")

    assert entry.source_hash == baseline
    assert entry.source_hash_commit == commit_baseline
    assert state_of(entry) is ContextState.STALE
    assert reason_of(entry) is StaleReason.SOURCES_CHANGED

    _verify(session, workspace, entry)
    assert state_of(entry) is ContextState.STALE, "editar o texto não curou a entrada"


def test_patch_de_source_refs_e_o_unico_caminho_que_move_o_baseline(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """O contraste: reapontar `source_refs` **é** um ato de reconfirmação, e move a base."""
    entry = _entry(session, workspace, ["src/**"])
    baseline = entry.source_hash

    write(repo_path, "src/app.py", "print('B')\n")
    commit_all(repo_path, "B")
    _verify(session, workspace, entry)
    assert state_of(entry) is ContextState.STALE

    update_entry(session, entry, source_refs=["src/**"])

    assert entry.source_hash != baseline
    assert state_of(entry) is ContextState.FRESH
    assert reason_of(entry) is None


# ================================================================= 3 — unknown


def test_sem_git_e_unknown(session: Session, workspace_sem_git: DevWorkspace) -> None:
    entry = _entry(session, workspace_sem_git, ["src/**"])
    _verify(session, workspace_sem_git, entry)

    assert state_of(entry) is ContextState.UNKNOWN
    assert reason_of(entry) is None
    assert entry.last_verified_commit is None


def test_repositorio_que_deixou_de_existir_e_unknown(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """O diretório some depois de a entrada ter baseline: nem `fresh` nem `stale`.

    O repositório é **movido**, não apagado: no Windows os objetos de `.git/` são
    somente-leitura e um `rmtree` falharia por permissão, o que testaria o `shutil` e não o
    Context Engine. O efeito observável é o mesmo — `local_path` deixa de existir.
    """
    entry = _entry(session, workspace, ["src/**"])
    assert entry.source_hash is not None

    repo_path.rename(repo_path.parent / "movido-para-longe")
    assert not repo_path.exists()

    _verify(session, workspace, entry)

    assert state_of(entry) is ContextState.UNKNOWN
    assert entry.source_hash is not None, "o baseline sobrevive ao sumiço do repositório"


def test_ref_que_deixou_de_casar_arquivo_e_unknown(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """Refs irresolvíveis é a linha `unknown` de [03] §3, não um `fresh` sobre o vazio."""
    entry = _entry(session, workspace, ["src/nested/**"])
    assert state_of(entry) is ContextState.FRESH

    git(repo_path, "rm", "-q", "-r", "src/nested")
    commit_all(repo_path, "remove src/nested")

    _verify(session, workspace, entry)
    assert state_of(entry) is ContextState.UNKNOWN


def test_entrada_sem_baseline_com_refs_e_unknown(
    session: Session, workspace_sem_git: DevWorkspace
) -> None:
    """Nasceu `unknown` sem baseline; `verify` não inventa um para poder comparar."""
    entry = _entry(session, workspace_sem_git, ["src/**"])
    assert entry.source_hash is None

    _verify(session, workspace_sem_git, entry)

    assert state_of(entry) is ContextState.UNKNOWN
    assert entry.source_hash is None


def test_entrada_sem_source_refs_e_sempre_fresh(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """Entrada autoral: não declara depender de código, então árvore suja não a afeta."""
    entry = _entry(session, workspace, [])
    write(repo_path, "src/app.py", "print('sujo')\n")

    _verify(session, workspace, entry)

    assert state_of(entry) is ContextState.FRESH
    assert reason_of(entry) is None
    assert entry.source_hash is None


# =========================================== 4 — divergência do working tree, sem commitar


def test_arquivo_coberto_modificado_sem_commitar_e_stale_working_tree(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """O caso que AUD-004 corrigiu: `git ls-tree` sozinho diria `fresh`.

    O commit **não** mudou, então a Parte A confere. A divergência só aparece na Parte B.
    """
    entry = _entry(session, workspace, ["src/**"])
    baseline = entry.source_hash

    write(repo_path, "src/app.py", "print('editado sem commitar')\n")

    outcome = _verify(session, workspace, entry)

    assert state_of(entry) is ContextState.STALE
    assert reason_of(entry) is StaleReason.WORKING_TREE
    assert entry.source_hash == baseline, "o baseline não se move nem aqui"
    assert entry.last_verified_commit == preflight(str(repo_path)).head
    assert [item.path for item in outcome.covered_divergences] == ["src/app.py"]


@pytest.mark.parametrize(
    ("preparar", "esperado"),
    [
        ("modified", WorkingTreeChange.MODIFIED),
        ("staged", WorkingTreeChange.STAGED),
        ("deleted", WorkingTreeChange.DELETED),
        ("renamed", WorkingTreeChange.RENAMED),
        ("untracked", WorkingTreeChange.UNTRACKED),
    ],
)
def test_cada_tipo_de_divergencia_coberta_produz_stale_working_tree(
    session: Session,
    workspace: DevWorkspace,
    repo_path: Path,
    preparar: str,
    esperado: WorkingTreeChange,
) -> None:
    """Os cinco tipos de [03] §3 Parte B, cada um sozinho, todos impedindo `fresh`.

    O caso `untracked` é o que prova que a cobertura é calculada pelo **padrão**, e não pela
    lista já expandida: `src/novo.py` não existe na árvore do `verification_commit`, mas é
    coberto por `src/**`.
    """
    entry = _entry(session, workspace, ["src/**"])

    if preparar == "modified":
        write(repo_path, "src/app.py", "print('mod')\n")
    elif preparar == "staged":
        # Contra uma base congelada, `staged` é "existe agora e não existia no commit"
        # (E4-AUD-003): um arquivo novo já no índice. Uma **modificação** staged de arquivo
        # existente aparece como `modified`, porque contra o commit é o conteúdo que difere
        # — a distinção índice-versus-árvore é sobre o `HEAD`, não sobre a base.
        write(repo_path, "src/novo.py", "NOVO = 1\n")
        git(repo_path, "add", "src/novo.py")
    elif preparar == "deleted":
        (repo_path / "src" / "app.py").unlink()
    elif preparar == "renamed":
        git(repo_path, "mv", "src/app.py", "src/main.py")
    else:
        write(repo_path, "src/novo.py", "NOVO = 1\n")

    outcome = _verify(session, workspace, entry)

    assert state_of(entry) is ContextState.STALE, preparar
    assert reason_of(entry) is StaleReason.WORKING_TREE, preparar
    assert esperado in {item.kind for item in outcome.covered_divergences}


def test_divergencia_fora_da_cobertura_nao_deixa_a_entrada_stale(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """Precisão: sujar o repositório fora dos `source_refs` não afeta a entrada.

    Sem este caso, "toda árvore suja marca tudo `stale`" passaria pelo teste anterior e o
    estado deixaria de significar alguma coisa.
    """
    entry = _entry(session, workspace, ["src/**"])

    write(repo_path, "README.md", "# mexido fora da cobertura\n")
    write(repo_path, "config/settings.json", '{"b": 2}\n')

    outcome = _verify(session, workspace, entry)

    assert state_of(entry) is ContextState.FRESH
    assert reason_of(entry) is None
    assert outcome.covered_divergences == ()
    assert outcome.dirty_file_count == 2, "a divergência do workspace é registrada mesmo assim"


def test_sources_changed_vence_working_tree(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """A ordem da tabela de [03] §3: com as duas condições, o motivo é `sources_changed`."""
    entry = _entry(session, workspace, ["src/**"])

    write(repo_path, "src/app.py", "print('commitado')\n")
    commit_all(repo_path, "muda e commita")
    write(repo_path, "src/util.py", "def util():\n    return 2\n")  # e suja também

    _verify(session, workspace, entry)

    assert reason_of(entry) is StaleReason.SOURCES_CHANGED


def test_rename_de_arquivo_coberto_conta_pela_origem(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """Renomear para **fora** da cobertura ainda invalida quem cobria o caminho original."""
    entry = _entry(session, workspace, ["src/nested/**"])

    git(repo_path, "mv", "src/nested/deep.py", "fora.py")

    outcome = _verify(session, workspace, entry)

    assert state_of(entry) is ContextState.STALE
    assert reason_of(entry) is StaleReason.WORKING_TREE
    assert [item.path for item in outcome.covered_divergences] == ["src/nested/deep.py"]


# ============================ o invariante estrutural: o commit é recebido, não lido aqui


def test_verify_usa_o_commit_recebido_e_nao_o_head(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """A verificação enxerga o SHA que lhe deram — é o que a E6 vai precisar.

    Com o baseline em A e o `HEAD` já em B, verificar **contra A** faz a Parte A conferir
    (o hash é recalculado em A, que é onde o baseline foi medido) e a Parte B acusar: a
    árvore de trabalho de hoje não é a de A. Verificar contra o `HEAD` acusa pela Parte A,
    porque aí o hash é recalculado em B.

    Os dois `last_verified_commit` diferentes são a prova de que a função usou o SHA
    recebido, e não foi atrás do `HEAD` por conta própria.
    """
    entry = _entry(session, workspace, ["src/**"])
    commit_a = entry.source_hash_commit
    assert commit_a is not None

    write(repo_path, "src/app.py", "print('B')\n")
    commit_all(repo_path, "B")

    contra_a = read_workspace_tree(workspace.local_path, verification_commit=commit_a)
    resultado_a = verify_freshness(entry, contra_a)
    assert entry.last_verified_commit == commit_a
    assert resultado_a.state is ContextState.STALE
    assert resultado_a.stale_reason is StaleReason.WORKING_TREE
    assert [item.path for item in resultado_a.covered_divergences] == ["src/app.py"]

    contra_head = read_workspace_tree(workspace.local_path)
    resultado_head = verify_freshness(entry, contra_head)
    assert entry.last_verified_commit != commit_a
    assert resultado_head.state is ContextState.STALE
    assert resultado_head.stale_reason is StaleReason.SOURCES_CHANGED


def test_uma_verificacao_usa_um_unico_snapshot_para_todas_as_entradas(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """[03] §3: o `verification_commit` é capturado uma vez e é imutável durante a chamada."""
    coberta = _entry(session, workspace, ["src/**"], title="Cobre src")
    autoral = _entry(session, workspace, [], title="Sem fontes")
    fora = _entry(session, workspace, ["config/**"], title="Cobre config")

    write(repo_path, "src/app.py", "print('sujo')\n")

    snapshot, resultados = verify_workspace_entries(session, workspace)

    assert snapshot.verification_commit == preflight(str(repo_path)).head
    por_id = {entry.id: outcome for entry, outcome in resultados}
    assert len(por_id) == 3
    assert all(
        outcome.verification_commit == snapshot.verification_commit for outcome in por_id.values()
    )

    assert state_of(coberta) is ContextState.STALE
    assert reason_of(coberta) is StaleReason.WORKING_TREE
    assert state_of(autoral) is ContextState.FRESH
    assert state_of(fora) is ContextState.FRESH


def test_dirty_file_count_conta_o_workspace_inteiro(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """A divergência é registrada mesmo quando não afeta entrada nenhuma ([03] §3)."""
    _entry(session, workspace, ["src/**"])

    write(repo_path, "README.md", "# a\n")
    write(repo_path, "config/settings.json", '{"b": 2}\n')
    write(repo_path, "novo-arquivo.txt", "x\n")

    snapshot, _resultados = verify_workspace_entries(session, workspace)

    assert snapshot.dirty_file_count == 3
