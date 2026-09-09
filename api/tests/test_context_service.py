"""Gate 4 da E4 — CRUD, `content_hash` e a **regra do baseline**.

O que este arquivo prova:

* entrada criada em cada um dos **8** domínios de [02] §2;
* `content_hash` obedece à fórmula canônica de [03] §2 — inclusive na normalização — e
  **não** depende de `tags`, `source_refs`, `state`, `origin` nem de timestamp;
* `PATCH` que toca só `body`/`title`/`tags`/`structured` muda o `content_hash` quando
  aplicável e deixa `source_hash`, `source_hash_commit`, `state` e `stale_reason`
  **idênticos** — o teste central desta sub-etapa;
* `PATCH` que altera `source_refs` estabelece **novo** baseline, no `verification_commit`
  capturado naquele momento, e recalcula o `state`;
* `DELETE` é livre;
* `source_refs` vazio → `state=fresh`, `source_hash=null`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.context_engine import (
    ContextEntryNotFound,
    InvalidContextEntry,
    InvalidSourceRefs,
    compute_content_hash,
    create_entry,
    delete_entry,
    get_entry,
    list_entries,
    normalize_text,
    update_entry,
)
from app.db.enums import ContextDomain, ContextOrigin, ContextState, StaleReason
from app.db.models import ContextRegistryEntry, DevWorkspace
from app.git_runtime import preflight
from tests.context_helpers import GIT, commit_all, write

#: Todo este arquivo monta repositórios git de verdade para exercitar o baseline.
pytestmark = pytest.mark.skipif(GIT is None, reason="git indisponível no PATH")


def _new(
    session: Session,
    ws: DevWorkspace,
    **overrides: object,
) -> ContextRegistryEntry:
    payload: dict[str, object] = {
        "domain": ContextDomain.MODULES,
        "title": "Módulo de exemplo",
        "body": "Descrição do módulo.\n",
    }
    payload.update(overrides)
    return create_entry(session, ws, **payload)  # type: ignore[arg-type]


# ===================================================================== os 8 domínios


@pytest.mark.parametrize("domain", list(ContextDomain))
def test_cria_entrada_em_cada_um_dos_oito_dominios(
    session: Session, workspace: DevWorkspace, domain: ContextDomain
) -> None:
    entry = _new(session, workspace, domain=domain, title=f"Entrada {domain.value}")

    assert entry.id
    assert entry.domain is domain
    assert entry.workspace_id == workspace.id
    assert entry.origin is ContextOrigin.MANUAL
    assert entry.state is ContextState.FRESH
    assert entry.stale_reason is None
    assert entry.source_refs == []
    assert entry.source_hash is None
    assert entry.source_hash_commit is None
    assert len(entry.content_hash) == 64


def test_os_oito_dominios_sao_exatamente_os_de_02_secao_2() -> None:
    assert {domain.value for domain in ContextDomain} == {
        "objective",
        "architecture",
        "stack",
        "requirements",
        "modules",
        "decisions",
        "risks",
        "contracts",
    }


def test_lista_por_workspace_e_por_dominio(session: Session, workspace: DevWorkspace) -> None:
    _new(session, workspace, domain=ContextDomain.STACK, title="Stack")
    _new(session, workspace, domain=ContextDomain.RISKS, title="Risco")
    _new(session, workspace, domain=ContextDomain.RISKS, title="Outro risco")

    assert len(list_entries(session, workspace.id)) == 3
    assert len(list_entries(session, workspace.id, domain=ContextDomain.RISKS)) == 2
    assert list_entries(session, "outro-workspace") == []


# ======================================================================= content_hash


def test_content_hash_e_a_formula_do_documento(session: Session, workspace: DevWorkspace) -> None:
    """Verificado contra a definição de [03] §2, não contra a implementação."""
    entry = _new(
        session,
        workspace,
        domain=ContextDomain.DECISIONS,
        title="Usar SQLite",
        body="Banco local, um arquivo.\n",
        structured={"decision": "SQLite", "reason": "local", "date": None},
    )

    esperado = hashlib.sha256(
        json.dumps(
            {
                "v": 1,
                "domain": "decisions",
                "title": "Usar SQLite",
                "body": "Banco local, um arquivo.\n",
                "structured": {"decision": "SQLite", "reason": "local", "date": None},
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    assert entry.content_hash == esperado


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("linha\r\nlinha2", "linha\nlinha2"),  # CRLF→LF
        ("linha   \nlinha2", "linha\nlinha2"),  # espaços finais por linha
        ("linha\t\nlinha2", "linha\nlinha2"),
        ("corpo\n\n\n", "corpo\n"),  # quebras finais colapsadas em uma
        ("corpo\n", "corpo\n"),
        ("cão", "cão".replace("cã", "cã")),  # NFC
    ],
)
def test_normalizacao_do_content_hash(a: str, b: str) -> None:
    assert normalize_text(a) == normalize_text(b)
    assert compute_content_hash(
        domain="modules", title="t", body=a, structured=None
    ) == compute_content_hash(domain="modules", title="t", body=b, structured=None)


def test_normalizacao_nao_apaga_diferenca_real() -> None:
    assert compute_content_hash(
        domain="modules", title="t", body="a\nb", structured=None
    ) != compute_content_hash(domain="modules", title="t", body="a\nc", structured=None)
    # "nenhuma quebra final" continua distinguindo de "uma quebra final"? Não: [03] §2
    # colapsa `n` quebras em uma, e zero continua sendo zero.
    assert normalize_text("a") == "a"
    assert normalize_text("a\n") == "a\n"
    assert normalize_text("a") != normalize_text("a\n")


def test_normalizacao_alcanca_structured() -> None:
    """`structured` é conteúdo autoral; um `\\r\\n` lá não pode gerar outro hash."""
    assert compute_content_hash(
        domain="decisions", title="t", body="b", structured={"reason": "um\r\ndois   "}
    ) == compute_content_hash(
        domain="decisions", title="t", body="b", structured={"reason": "um\ndois"}
    )


def test_content_hash_nao_depende_do_que_03_secao_2_exclui(
    session: Session, workspace: DevWorkspace
) -> None:
    """`tags`, `source_refs`, `state`, `origin`, `id` e timestamps ficam de fora."""
    base = _new(session, workspace, title="Igual", body="Mesmo corpo.\n")
    com_tags = _new(
        session,
        workspace,
        title="Igual",
        body="Mesmo corpo.\n",
        tags=["a", "b"],
        origin=ContextOrigin.GENERATED,
        source_refs=["src/app.py"],
    )

    assert base.id != com_tags.id
    assert base.tags != com_tags.tags
    assert base.source_refs != com_tags.source_refs
    assert base.origin is not com_tags.origin
    assert base.content_hash == com_tags.content_hash


def test_content_hash_muda_com_cada_campo_que_entra(
    session: Session, workspace: DevWorkspace
) -> None:
    base = _new(session, workspace, title="T", body="B\n")

    outro_titulo = update_entry(session, _new(session, workspace, title="T", body="B\n"), title="U")
    outro_corpo = update_entry(session, _new(session, workspace, title="T", body="B\n"), body="C\n")
    outro_struct = update_entry(
        session, _new(session, workspace, title="T", body="B\n"), structured={"x": 1}
    )
    outro_dominio = _new(session, workspace, domain=ContextDomain.RISKS, title="T", body="B\n")

    assert base.content_hash != outro_titulo.content_hash
    assert base.content_hash != outro_corpo.content_hash
    assert base.content_hash != outro_struct.content_hash
    assert base.content_hash != outro_dominio.content_hash


# ========================================================== baseline: criação


def test_source_refs_vazio_nasce_fresh_sem_baseline(
    session: Session, workspace: DevWorkspace
) -> None:
    entry = _new(session, workspace, source_refs=[])

    assert entry.state is ContextState.FRESH
    assert entry.stale_reason is None
    assert entry.source_hash is None
    assert entry.source_hash_commit is None


def test_criacao_com_source_refs_estabelece_baseline(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    head = preflight(str(repo_path)).head

    entry = _new(session, workspace, source_refs=["src/**"])

    assert entry.source_hash is not None
    assert entry.source_hash_commit == head
    assert entry.state is ContextState.FRESH
    assert entry.stale_reason is None


def test_criacao_com_source_ref_recusado_e_422(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    write(repo_path, "config/.env.local", "TOKEN=x\n")
    commit_all(repo_path, "acrescenta segredo")

    with pytest.raises(InvalidSourceRefs) as caught:
        _new(session, workspace, source_refs=["config/*"])

    assert caught.value.status_code == 422
    assert caught.value.rule_id == "source_ref_expansion.secret_denied"


def test_criacao_com_travessia_e_422(session: Session, workspace: DevWorkspace) -> None:
    with pytest.raises(InvalidSourceRefs) as caught:
        _new(session, workspace, source_refs=["../**"])

    assert caught.value.rule_id == "source_ref.parent_traversal"


def test_criacao_sem_git_nasce_unknown_sem_baseline(
    session: Session, workspace_sem_git: DevWorkspace
) -> None:
    """ "Declara depender de código e não consegui verificar" — [03] §3."""
    entry = _new(session, workspace_sem_git, source_refs=["src/**"])

    assert entry.state is ContextState.UNKNOWN
    assert entry.stale_reason is None
    assert entry.source_hash is None, "baseline nunca é fabricado sem leitura de verdade"
    assert entry.source_hash_commit is None


def test_criacao_com_ref_irresolvivel_nasce_unknown(
    session: Session, workspace: DevWorkspace
) -> None:
    entry = _new(session, workspace, source_refs=["nao/existe/**"])

    assert entry.state is ContextState.UNKNOWN
    assert entry.source_hash is None


# ============================== O TESTE CENTRAL: PATCH sem source_refs não toca baseline


def test_patch_de_conteudo_nao_toca_o_baseline(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """`body`/`title`/`tags`/`structured` mudam o `content_hash` e **mais nada**.

    Este é o invariante que [03] §3 tornou normativo no planejamento desta fase: uma
    correção de digitação não pode "curar" uma entrada, nem mover a linha de base contra a
    qual o código é comparado.
    """
    entry = _new(session, workspace, source_refs=["src/**"])

    baseline_hash = entry.source_hash
    baseline_commit = entry.source_hash_commit
    conteudo_antes = entry.content_hash
    assert baseline_hash is not None

    # o código muda e é commitado: a entrada está objetivamente desatualizada
    write(repo_path, "src/app.py", "print('mudou')\n")
    commit_all(repo_path, "muda o código")

    atualizada = update_entry(
        session,
        entry,
        title="Outro título",
        body="Outro corpo.\n",
        tags=["b", "a", "a"],
        structured={"nota": "editada"},
    )

    assert atualizada.content_hash != conteudo_antes
    assert atualizada.title == "Outro título"
    assert atualizada.tags == ["a", "b"]

    assert atualizada.source_hash == baseline_hash
    assert atualizada.source_hash_commit == baseline_commit
    assert atualizada.state is ContextState.FRESH
    assert atualizada.stale_reason is None
    assert atualizada.source_refs == ["src/**"]


def test_patch_de_conteudo_nao_ressuscita_entrada_stale(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """Uma entrada já marcada `stale` continua `stale` depois de um `PATCH` de texto."""
    entry = _new(session, workspace, source_refs=["src/**"])
    entry.state = ContextState.STALE
    entry.stale_reason = StaleReason.SOURCES_CHANGED
    session.flush()

    atualizada = update_entry(session, entry, body="corpo revisado\n")

    assert atualizada.state is ContextState.STALE
    assert atualizada.stale_reason is StaleReason.SOURCES_CHANGED


def test_patch_sem_nenhum_campo_e_inofensivo(session: Session, workspace: DevWorkspace) -> None:
    entry = _new(session, workspace, source_refs=["src/**"])
    antes = (entry.content_hash, entry.source_hash, entry.source_hash_commit, entry.state)

    atualizada = update_entry(session, entry)

    assert (
        atualizada.content_hash,
        atualizada.source_hash,
        atualizada.source_hash_commit,
        atualizada.state,
    ) == antes


# ============================================ PATCH que altera source_refs: novo baseline


def test_patch_de_source_refs_estabelece_novo_baseline_no_commit_do_momento(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    entry = _new(session, workspace, source_refs=["src/app.py"])
    baseline_inicial = entry.source_hash
    commit_inicial = entry.source_hash_commit

    write(repo_path, "src/novo.py", "NOVO = 1\n")
    commit_all(repo_path, "acrescenta novo.py")
    novo_head = preflight(str(repo_path)).head
    assert novo_head != commit_inicial

    atualizada = update_entry(session, entry, source_refs=["src/**"])

    assert atualizada.source_hash != baseline_inicial
    assert atualizada.source_hash_commit == novo_head, (
        "o `verification_commit` do baseline é o do momento do PATCH"
    )
    assert atualizada.state is ContextState.FRESH
    assert atualizada.stale_reason is None


def test_patch_de_source_refs_para_lista_vazia_zera_o_baseline(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """[03] §3: lista vazia remove o baseline e devolve o estado a `fresh` sem motivo."""
    entry = _new(session, workspace, source_refs=["src/**"])
    entry.state = ContextState.STALE
    entry.stale_reason = StaleReason.WORKING_TREE
    session.flush()
    assert entry.source_hash is not None

    atualizada = update_entry(session, entry, source_refs=[])

    assert atualizada.source_refs == []
    assert atualizada.source_hash is None
    assert atualizada.source_hash_commit is None
    assert atualizada.state is ContextState.FRESH
    assert atualizada.stale_reason is None


def test_patch_de_source_refs_recusado_nao_altera_nada(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """422 e a entrada **não** fica alterada pela metade no que importa para o baseline."""
    write(repo_path, "config/.env.local", "TOKEN=x\n")
    commit_all(repo_path, "acrescenta segredo")

    entry = _new(session, workspace, source_refs=["src/**"])
    baseline = entry.source_hash
    conteudo = entry.content_hash

    with pytest.raises(InvalidSourceRefs):
        update_entry(session, entry, source_refs=["config/*"], title="Novo título")

    # A recusa acontece **antes** de qualquer atribuição: nem os campos de conteúdo, que
    # nem eram o problema, foram tocados. Sem isso, a atomicidade dependeria do `rollback`
    # do chamador — o que vale pela rota HTTP e não valeria para um chamador interno.
    assert entry.source_refs == ["src/**"]
    assert entry.source_hash == baseline
    assert entry.content_hash == conteudo
    assert entry.title != "Novo título"

    recarregada = session.get(ContextRegistryEntry, entry.id)
    assert recarregada is not None
    assert recarregada.source_refs == ["src/**"]
    assert recarregada.source_hash == baseline


def test_source_refs_informado_igual_ao_atual_ainda_refaz_o_baseline(
    session: Session, workspace: DevWorkspace, repo_path: Path
) -> None:
    """Informar `source_refs` é um ato explícito, mesmo que o valor não mude.

    [03] §3 condiciona a escrita do baseline a `source_refs` ser "explicitamente alterado
    via `PATCH`". A distinção que o serviço faz é entre **informado** e **ausente** — que é
    a única que a borda HTTP consegue transmitir sem adivinhar intenção.
    """
    entry = _new(session, workspace, source_refs=["src/app.py"])
    commit_inicial = entry.source_hash_commit

    write(repo_path, "outro.md", "x\n")
    commit_all(repo_path, "commit que não toca src/")
    novo_head = preflight(str(repo_path)).head

    atualizada = update_entry(session, entry, source_refs=["src/app.py"])

    assert atualizada.source_hash_commit == novo_head != commit_inicial
    assert atualizada.state is ContextState.FRESH


# ========================================================================= DELETE livre


def test_delete_e_livre(session: Session, workspace: DevWorkspace) -> None:
    """[02] §2: "Exclusão **livre**." Sem guarda de estado, de origem ou de referência."""
    entry = _new(session, workspace, source_refs=["src/**"])
    entry.state = ContextState.STALE
    entry.stale_reason = StaleReason.SOURCES_CHANGED
    session.flush()
    entry_id = entry.id

    delete_entry(session, entry)

    with pytest.raises(ContextEntryNotFound):
        get_entry(session, entry_id)


def test_delete_de_entrada_importada_tambem_e_livre(
    session: Session, workspace: DevWorkspace
) -> None:
    entry = _new(session, workspace, origin=ContextOrigin.IMPORTED_PLANNING)
    delete_entry(session, entry)
    assert list_entries(session, workspace.id) == []


# ======================================================================== validação


def test_title_vazio_e_body_vazio_sao_recusados(session: Session, workspace: DevWorkspace) -> None:
    with pytest.raises(InvalidContextEntry):
        _new(session, workspace, title="   ")
    with pytest.raises(InvalidContextEntry):
        _new(session, workspace, body="   ")


def test_title_longo_demais_e_recusado(session: Session, workspace: DevWorkspace) -> None:
    """O SQLite não recusa VARCHAR longo (E3-AUD-007) — o invariante vive no domínio."""
    with pytest.raises(InvalidContextEntry):
        _new(session, workspace, title="x" * 256)


def test_structured_nao_canonizavel_e_recusado(session: Session, workspace: DevWorkspace) -> None:
    with pytest.raises(InvalidContextEntry):
        _new(session, workspace, structured={"quando": object()})
    with pytest.raises(InvalidContextEntry):
        _new(session, workspace, structured={"n": float("nan")})


def test_get_de_id_inexistente_e_404(session: Session) -> None:
    with pytest.raises(ContextEntryNotFound) as caught:
        get_entry(session, "nao-existe")
    assert caught.value.status_code == 404


def test_ausente_e_lista_vazia_sao_caminhos_opostos(
    session: Session, workspace: DevWorkspace
) -> None:
    """A distinção que o sentinela `UNSET` existe para carregar, provada por comportamento.

    O mesmo `PATCH`, com `source_refs` **ausente** e com `source_refs=[]`, leva a estados
    opostos: o primeiro preserva o baseline, o segundo o zera. Se um `None` fizesse os dois
    papéis, um deles seria impossível de expressar.

    No nível de tipo a distinção também é real: `UNSET` é o único membro de um `Enum`
    próprio, e o mypy recusa até compará-lo com `[]` como comparação impossível — é isso que
    faz `source_refs is not UNSET` estreitar para `list[str]` sem `cast`.
    """
    ausente = _new(session, workspace, source_refs=["src/**"])
    vazio = _new(session, workspace, source_refs=["src/**"])
    baseline = ausente.source_hash
    assert baseline is not None

    update_entry(session, ausente, body="só o corpo\n")
    update_entry(session, vazio, source_refs=[])

    assert ausente.source_hash == baseline
    assert ausente.source_refs == ["src/**"]

    assert vazio.source_hash is None
    assert vazio.source_refs == []
    assert vazio.state is ContextState.FRESH
