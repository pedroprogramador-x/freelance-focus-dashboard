"""E5 — Context Router + file map: gates funcionais e gates de determinismo.

A fase inteira existe para uma propriedade: **a mesma pergunta, feita duas vezes, produz o
mesmo byte**. Os gates funcionais provam que o resultado está certo; os de determinismo
provam que ele é o mesmo resultado sempre — e é a segunda metade que carrega o risco, porque
o Python varia em silêncio de várias formas ao mesmo tempo:

* ordem de iteração de `dict` e de `set` (esta última randomizada por `PYTHONHASHSEED`);
* ordem de retorno de um `SELECT` sem `ORDER BY` total;
* ordem de inserção das linhas no banco;
* ordem em que o autor digitou as chaves de um `structured`;
* `float` em soma ou comparação;
* tradução de `\\n` para `\\r\\n` na escrita em modo texto, só no Windows.

Cada uma delas tem um teste nomeado abaixo.

Nenhum teste aqui usa endpoint HTTP de task: a E5 não cria nenhum. As `WorkspaceTask` vêm
de `context_helpers.make_task`, que escreve direto pela sessão de teste **só** para
satisfazer a FK de `ContextManifest.task_id`.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppSettings
from app.context_engine import (
    RENDERER_VERSION,
    ContextTreeUnavailable,
    Transformation,
    approx_tokens,
    build_file_map,
    build_file_map_from_tree,
    clear_file_map_cache,
    compute_content_hash,
    compute_manifest_hash,
    create_entry,
    encode_path_identity,
    epoch_microseconds,
    freeze_manifest,
    render_block_text,
    render_context,
    select_context,
)
from app.context_engine.selection import (
    PROXIMITY_MAX,
    REASON_BUDGET,
    REASON_OUT_OF_WORKSPACE,
    REASON_SECRET_POLICY,
    SCORE_AFFECTED_DOMAIN,
    SCORE_EXACT_LITERAL_MATCH,
    SCORE_FRESH,
    SCORE_SOURCE_REF_OVERLAP,
    SCORE_TAG_MATCH,
)
from app.db.enums import ContextDomain
from app.db.models import ContextManifest, ContextRegistryEntry, DevWorkspace, WorkspaceTask
from app.git_runtime import TreeListing, list_tree
from app.safety.canonical import canonical_json
from app.safety.redaction import REDACTED
from tests import context_helpers

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _limpa_cache_do_file_map() -> None:
    """O cache é global por processo; um teste não pode herdar o mapa de outro."""
    clear_file_map_cache()


@pytest.fixture
def base_commit(repo_path: Path) -> str:
    return context_helpers.head_of(repo_path)


def _entry(
    session: Session,
    workspace: DevWorkspace,
    *,
    domain: ContextDomain,
    title: str,
    body: str = "corpo da entrada\n",
    tags: list[str] | None = None,
    source_refs: list[str] | None = None,
    structured: dict[str, Any] | None = None,
) -> ContextRegistryEntry:
    return create_entry(
        session,
        workspace,
        domain=domain,
        title=title,
        body=body,
        tags=tags or [],
        source_refs=source_refs or [],
        structured=structured,
    )


def _touch(entry: ContextRegistryEntry, *, seconds: int) -> None:
    """Move `updated_at` de forma determinística, sem depender do relógio da máquina."""
    entry.updated_at = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)


# ============================================================ sub-etapa 1 — build_file_map


def test_file_map_descreve_a_arvore_do_commit(repo_path: Path, base_commit: str) -> None:
    """GATE FUNCIONAL: múltiplos diretórios e múltiplas extensões, mapa correto."""
    file_map = build_file_map("ws-1", str(repo_path), base_commit)

    assert file_map is not None
    forma = {item.path: (item.dir_path, item.dir_depth, item.extension) for item in file_map.items}
    assert forma == {
        "README.md": ("", 0, ".md"),
        "config/settings.json": ("config", 1, ".json"),
        "src/app.py": ("src", 1, ".py"),
        "src/nested/deep.py": ("src/nested", 2, ".py"),
        "src/util.py": ("src", 1, ".py"),
    }
    assert [item.path for item in file_map.items] == sorted(forma)
    assert file_map.item_count == 5
    assert not file_map.partial


def test_file_map_nao_enxerga_o_working_tree(repo_path: Path, base_commit: str) -> None:
    """[ADR-0006] item 4 + [02] §6: o mapa é do commit congelado, não do disco."""
    antes = build_file_map("ws-1", str(repo_path), base_commit)
    context_helpers.write(repo_path, "src/nao_commitado.py", "X = 1\n")
    clear_file_map_cache()
    depois = build_file_map("ws-1", str(repo_path), base_commit)

    assert antes is not None and depois is not None
    assert "src/nao_commitado.py" not in [item.path for item in depois.items]
    assert depois.hash == antes.hash


def test_file_map_e_deterministico_byte_a_byte(repo_path: Path, base_commit: str) -> None:
    """GATE DE DETERMINISMO 1: duas construções sobre o mesmo commit, conteúdo idêntico."""
    tree = list_tree(str(repo_path), base_commit)
    assert tree is not None

    primeiro = build_file_map_from_tree(tree)
    segundo = build_file_map_from_tree(tree)

    assert primeiro.items == segundo.items
    assert primeiro.hash == segundo.hash
    serializado = canonical_json([item.as_canonical() for item in primeiro.items])
    assert serializado.encode("utf-8") == canonical_json(
        [item.as_canonical() for item in segundo.items]
    ).encode("utf-8")


def test_file_map_e_cacheado_por_workspace_e_commit(repo_path: Path, base_commit: str) -> None:
    """[ADR-0006] item 4: a chave é `(workspace_id, git_head)`, e o valor é reaproveitado."""
    primeiro = build_file_map("ws-1", str(repo_path), base_commit)
    segundo = build_file_map("ws-1", str(repo_path), base_commit)
    assert primeiro is segundo

    # Outro workspace no mesmo commit não colide — a chave tem duas partes.
    outro = build_file_map("ws-2", str(repo_path), base_commit)
    assert outro is not primeiro
    assert outro is not None and primeiro is not None
    assert outro.hash == primeiro.hash


def test_file_map_extensao_de_dotfile_e_vazia(repo_path: Path) -> None:
    """`.env` e `.gitignore` não têm extensão: o ponto abre o nome, não separa sufixo."""
    context_helpers.write(repo_path, ".gitignore", "*.pyc\n")
    context_helpers.write(repo_path, "Makefile", "all:\n\techo ok\n")
    context_helpers.write(repo_path, "pacote.tar.gz", "binario\n")
    context_helpers.commit_all(repo_path, "dotfiles")

    tree = list_tree(str(repo_path), context_helpers.head_of(repo_path))
    assert tree is not None
    extensoes = {item.path: item.extension for item in build_file_map_from_tree(tree).items}

    assert extensoes[".gitignore"] == ""
    assert extensoes["Makefile"] == ""
    assert extensoes["pacote.tar.gz"] == ".gz"


def test_file_map_e_none_quando_a_arvore_nao_da_para_ler(plain_path: Path) -> None:
    assert build_file_map("ws-1", str(plain_path), "0" * 40) is None


# ------------------------------------------ GRUPO A (AUD-001/006/007): identidade de path


def test_file_map_trata_nfc_e_nfd_como_arquivos_distintos(repo_path: Path) -> None:
    """REPRODUÇÃO EXATA (Codex): `café.py` grafado em NFC e o mesmo nome grafado em NFD,
    dois arquivos **reais** no mesmo commit. `file_map` precisa tratá-los como
    identidades distintas — dois `FileMapItem`, dois `path` diferentes, `file_map_hash`
    sensível à diferença. Fecha AUD-001/006/007: sem `encode_path_identity`,
    `canonical_json` normalizaria os dois `path` em NFC antes de hashear e os colapsaria
    no mesmo texto, apesar de serem blobs Git genuinamente diferentes.
    """
    nfc_name = "café.py"
    nfd_name = unicodedata.normalize("NFD", nfc_name)
    assert nfc_name != nfd_name  # a pré-condição do teste: são strings Python diferentes

    context_helpers.write(repo_path, nfc_name, "conteúdo nfc\n")
    context_helpers.write(repo_path, nfd_name, "conteúdo nfd\n")
    context_helpers.commit_all(repo_path, "nfc e nfd")

    tree = list_tree(str(repo_path), context_helpers.head_of(repo_path))
    assert tree is not None
    assert not tree.unrepresentable  # os dois são UTF-8 válido, nenhum ilegível

    file_map = build_file_map_from_tree(tree)
    paths = {item.path for item in file_map.items}
    assert nfc_name in paths
    assert nfd_name in paths
    assert len(paths) == len({item.path for item in file_map.items})  # sem colisão

    # Hash sensível à diferença: uma árvore só com a forma NFC hasheia diferente.
    tree_so_nfc = TreeListing(files=tuple((p, sha) for p, sha in tree.files if p == nfc_name))
    file_map_so_nfc = build_file_map_from_tree(tree_so_nfc)
    assert file_map_so_nfc.hash != file_map.hash


def test_manifest_hash_trata_nfc_e_nfd_como_identidades_distintas(
    session: Session,
    workspace: DevWorkspace,
    repo_path: Path,
    temp_settings: AppSettings,
) -> None:
    """A mesma prova de `test_file_map_trata_nfc_e_nfd_como_arquivos_distintos`, na camada
    de `manifest_hash`: `source_files` com um `path` em NFC hasheia diferente do mesmo
    conjunto com o `path` trocado para NFD — `compute_manifest_hash` não pode colapsar as
    duas identidades ao pré-codificar.
    """
    nfc_name = "café.py"
    nfd_name = unicodedata.normalize("NFD", nfc_name)

    def hash_com(path: str) -> str:
        return compute_manifest_hash(
            git_head="a" * 40,
            base_branch=None,
            entries=[],
            source_files=[{"path": path, "blob_sha": "b" * 40}],
            working_tree_divergence={"dirty_file_count": 0, "covered": []},
            derived=[],
            excluded=[],
        )

    assert hash_com(nfc_name) != hash_com(nfd_name)
    # E a pré-codificação em si distingue os dois — não é só um efeito colateral do hash.
    assert encode_path_identity(nfc_name) != encode_path_identity(nfd_name)
    # Round-trip: hex decodifica de volta para os bytes UTF-8 exatos.
    assert bytes.fromhex(encode_path_identity(nfc_name)).decode("utf-8") == nfc_name


def test_titulo_com_crlf_e_espaco_final_fica_consistente_entre_content_hash_e_manifest(
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> None:
    """REPRODUÇÃO EXATA (Codex): título `"Line A  \\r\\nLine B"` vs. `"Line A\\nLine B"`
    (mesmo texto, só a forma difere) — `content_hash` e o título que se propaga para
    `origin`/`entries[].title` ficam **consistentes entre si**, porque as duas camadas
    usam a mesma `normalize_text` de [03] §2.

    A comparação é em duas camadas:

    1. **Função pura** — `compute_content_hash` chamado diretamente com as duas formas do
       título produz o mesmo hash, sem precisar de banco nem de duas entradas (duas
       entradas teriam `entry_id` diferentes, e `entry_id` **é** semanticamente relevante
       em `manifest_hash` — comparar `manifest_hash` entre duas entradas distintas
       mediria a diferença de identidade, não a consistência de normalização).
    2. **Uma entrada real**, com o título cru — `entry.content_hash` (gravado na
       criação) e `manifest.entries[0]["title"]` (propagado por `render_block_text`)
       concordam: os dois refletem a forma **já normalizada**, e são o mesmo texto que
       `compute_content_hash`/`render_block_text` produziriam chamados isoladamente.
    """
    cru = "Line A  \r\nLine B"
    ja_normalizado = "Line A\nLine B"
    assert cru != ja_normalizado  # formas diferentes da mesma informação

    # (1) função pura, sem banco.
    hash_cru = compute_content_hash(domain="stack", title=cru, body="corpo", structured=None)
    hash_normalizado = compute_content_hash(
        domain="stack", title=ja_normalizado, body="corpo", structured=None
    )
    assert hash_cru == hash_normalizado

    # (2) uma entrada real, título cru — as duas camadas concordam sobre a forma final.
    entry = _entry(session, workspace, domain=ContextDomain.STACK, title=cru, body="corpo")
    task = context_helpers.make_task(session, workspace)
    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=[],
        max_context_tokens=100_000,
    )
    manifest = freeze_manifest(
        session, task, selection, base_branch=None, artifacts_dir=temp_settings.artifacts_dir
    )
    linha = next(item for item in manifest.entries if item["entry_id"] == entry.id)

    assert entry.content_hash == compute_content_hash(
        domain="stack", title=cru, body="corpo", structured=None
    )
    assert linha["title"] == ja_normalizado
    assert render_block_text(entry).title == ja_normalizado


def test_e5_aud2_005_medicao_bate_com_os_bytes_gravados(
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> None:
    """REPRODUCAO EXATA (Codex, E5-AUD2-005 / reabertura de AUD-007): corpo com caractere
    combinante repetido (NFD), `transformations=(REDACT,)`.

    A versao anterior media `RenderedBlock.text` **antes** de `canonical_json` aplicar
    NFC ao serializar o artefato: o artifact declarava 233 caracteres e o `blocks[].text`
    relido do disco tinha 133. Este teste le os **bytes reais gravados** (`json.loads` do
    arquivo, nunca `RenderedContext.payload` em memoria) e exige que
    `total_chars`/`approx_tokens` declarados batam com o texto que esta la.

    O teste anterior -- que a rodada 2 apontou como insuficiente -- so chamava
    `render_block_text`/`approx_tokens` e continha um `assert len(x) == len(x)`:
    demonstrava aritmetica sobre a string intermediaria, nao igualdade com a
    representacao final.
    """
    combinante = "é" * 100  # NFD: 200 code points; 100 depois do NFC final
    _entry(
        session,
        workspace,
        domain=ContextDomain.STACK,
        title="Combinante",
        body=combinante,
    )
    task = context_helpers.make_task(session, workspace)

    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=[],
        max_context_tokens=100_000,
        transformations=(Transformation.REDACT,),
    )
    manifest = freeze_manifest(
        session, task, selection, base_branch=None, artifacts_dir=temp_settings.artifacts_dir
    )

    caminho = temp_settings.artifacts_dir / f"{manifest.rendered_context_hash}.json"
    payload = json.loads(caminho.read_bytes().decode("utf-8"))
    bloco = payload["blocks"][0]

    # O texto relido do disco e a referencia -- tudo tem de bater com ele.
    do_disco = bloco["text"]
    assert bloco["emitted_chars"] == len(do_disco)
    assert bloco["original_chars"] == len(do_disco)
    assert payload["total_chars"] == len(do_disco)
    assert payload["approx_tokens"] == approx_tokens(do_disco)
    assert manifest.total_chars == len(do_disco)
    assert manifest.approx_tokens == approx_tokens(do_disco)

    # E a forma NFD do corpo nao sobrevive: `canonical_json` a comporia de qualquer
    # forma, entao o renderizador ja entrega composto -- e a contagem sabe disso.
    assert "é" not in do_disco


# ========================================================= sub-etapa 3 — render_block_text


def test_render_block_text_e_estavel_entre_chamadas(
    session: Session, workspace: DevWorkspace
) -> None:
    """GATE: a mesma entrada produz exatamente o mesmo texto emitido."""
    entry = _entry(
        session,
        workspace,
        domain=ContextDomain.DECISIONS,
        title="Usar SQLite",
        body="Banco local, sem servidor.\n",
        structured={"reason": "simplicidade", "decision": "sqlite"},
    )

    assert render_block_text(entry) == render_block_text(entry)


def test_render_block_text_redige_segredo_antes_de_contar(
    session: Session, workspace: DevWorkspace
) -> None:
    """GATE: a redação muda o `approx_tokens` do resultado, não o texto original."""
    segredo = "sk-ant-" + "A" * 60
    entry = _entry(
        session,
        workspace,
        domain=ContextDomain.STACK,
        title="Chave",
        body=f"A chave é {segredo} e não deveria vazar.\n",
    )

    texto = render_block_text(entry).text

    assert segredo not in texto
    assert REDACTED in texto
    # O corpo original continua intacto no banco: quem encolheu foi o texto emitido.
    assert segredo in entry.body
    assert len(texto) < len(entry.body) + len(entry.title) + 64


def test_render_block_text_redige_segredo_partido_entre_campos(
    session: Session, workspace: DevWorkspace
) -> None:
    """Um segredo dividido entre `body` e `structured` não escapa por estar em dois campos."""
    entry = _entry(
        session,
        workspace,
        domain=ContextDomain.CONTRACTS,
        title="Certificado",
        body="-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\n",
        structured={"resto": "kqhkiG9w0BAQEF\n-----END PRIVATE KEY-----"},
    )

    texto = render_block_text(entry).text

    assert "MIIEvQIBADANBg" not in texto
    assert "kqhkiG9w0BAQEF" not in texto
    assert REDACTED in texto


def test_render_block_text_ignora_a_ordem_das_chaves_de_structured(
    session: Session, workspace: DevWorkspace
) -> None:
    """GATE DE DETERMINISMO 6, na camada do texto: `canonical_json` ordena por code point."""
    primeira = _entry(
        session,
        workspace,
        domain=ContextDomain.DECISIONS,
        title="Decisão",
        structured={"zeta": 1, "alfa": 2, "meio": {"b": 1, "a": 2}},
    )
    segunda = _entry(
        session,
        workspace,
        domain=ContextDomain.DECISIONS,
        title="Decisão",
        structured={"alfa": 2, "meio": {"a": 2, "b": 1}, "zeta": 1},
    )

    assert render_block_text(primeira) == render_block_text(segunda)


def test_render_block_text_recusa_desligar_a_redacao(
    session: Session, workspace: DevWorkspace
) -> None:
    """`transformations` é dado registrado, não interruptor: sem `redact`, é erro."""
    entry = _entry(session, workspace, domain=ContextDomain.STACK, title="Stack")

    with pytest.raises(ValueError, match="redação de segredo não é opcional"):
        render_block_text(entry, [Transformation.NORMALIZE])


def test_render_block_text_ignora_a_ordem_das_transformacoes_pedidas(
    session: Session, workspace: DevWorkspace
) -> None:
    """A ordem do pipeline é da constante, não da lista que o chamador montou."""
    entry = _entry(session, workspace, domain=ContextDomain.STACK, title="Stack")

    direta = render_block_text(entry, [Transformation.NORMALIZE, Transformation.REDACT])
    invertida = render_block_text(entry, [Transformation.REDACT, Transformation.NORMALIZE])

    assert direta == invertida


# ---------------------------------------- GRUPO B (AUD-002/003/004): redação vs. estrutura


def test_segredo_no_titulo_propaga_redigido_a_todo_lugar(
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> None:
    """REPRODUÇÃO EXATA (Codex): segredo no título — redigido em `blocks[].text`,
    `blocks[].origin.title`, `ContextManifest.entries[].title` e nos bytes do artifact.

    Fecha AUD-002: a versão anterior propagava `entry.title` **cru** para
    `origin.title`/`entries[].title`, mesmo quando `blocks[].text` já mostrava o título
    redigido — uma cópia paralela do valor cru sobrevivia exatamente nos dois lugares que
    a UI/o agente de auditoria consultariam para saber "o que foi entregue".
    """
    segredo = "sk-ant-" + "C" * 60
    entry = _entry(
        session,
        workspace,
        domain=ContextDomain.STACK,
        title=f"Credencial {segredo}",
        body="corpo sem segredo nenhum",
    )
    task = context_helpers.make_task(session, workspace)

    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=[],
        max_context_tokens=100_000,
    )
    manifest = freeze_manifest(
        session, task, selection, base_branch=None, artifacts_dir=temp_settings.artifacts_dir
    )

    # blocks[].text
    rendered = render_block_text(entry)
    assert segredo not in rendered.text
    assert REDACTED in rendered.title

    # blocks[].origin.title, via ScoredEntry.title propagada
    selecionado = next(item for item in selection.selected if item.entry_id == entry.id)
    assert segredo not in selecionado.title
    assert REDACTED in selecionado.title

    # ContextManifest.entries[].title
    linha = next(item for item in manifest.entries if item["entry_id"] == entry.id)
    assert segredo not in linha["title"]
    assert REDACTED in linha["title"]

    # bytes do artifact
    bruto = (temp_settings.artifacts_dir / f"{manifest.rendered_context_hash}.json").read_bytes()
    assert segredo.encode("utf-8") not in bruto
    payload = json.loads(bruto.decode("utf-8"))
    bloco = payload["blocks"][0]
    assert segredo not in bloco["origin"]["title"]
    assert segredo not in bloco["text"]
    assert REDACTED in bloco["origin"]["title"]


def test_atribuicao_em_structured_e_reconhecida_apos_linearizacao(
    session: Session, workspace: DevWorkspace
) -> None:
    """REPRODUÇÃO EXATA (Codex): `structured={"password": "hunter2"}` — o redator
    reconhece a atribuição porque a linearização preserva o contexto `chave: valor`
    **antes** de `canonical_json` entrar em cena (fecha AUD-003: a serialização JSON da
    versão anterior inseria `"` logo depois do nome do campo, e `\\s*[:=]\\s*` nunca via
    espaço nem `:`/`=` ali — o padrão simplesmente não disparava).
    """
    entry = _entry(
        session,
        workspace,
        domain=ContextDomain.STACK,
        title="Config",
        body="corpo comum",
        structured={"password": "hunter2"},
    )

    texto = render_block_text(entry).text

    assert "hunter2" not in texto
    assert REDACTED in texto
    # O valor cru sobrevive só onde deve: na linha do banco, nunca no texto emitido.
    assert entry.structured is not None
    assert entry.structured["password"] == "hunter2"


def test_segredo_partido_entre_body_e_structured_como_token_continuo(
    session: Session, workspace: DevWorkspace
) -> None:
    """REPRODUÇÃO EXATA (Codex): `"sk-123456"` em `body`, `"7890ABCDEF"` em `structured`
    — nenhuma metade sozinha forma um token reconhecível (`sk-123456` tem só 6 caracteres
    depois de `sk-`, abaixo dos 16 exigidos); juntos formam `sk-1234567890ABCDEF`, que o
    redator só reconhece quando as duas metades ficam contíguas. Fecha AUD-004.

    Prova negativa: as mesmas duas metades, coladas **fora** do pipeline, batem o padrão
    — confirma que o teste exercitaria o padrão certo se a implementação vazasse.
    """
    from app.safety.redaction import redact as _redact_standalone

    metade_corpo = "prefixo sk-123456"
    metade_estruturada = "7890ABCDEF"
    colado = metade_corpo + metade_estruturada
    assert _redact_standalone(colado) != colado

    entry = _entry(
        session,
        workspace,
        domain=ContextDomain.CONTRACTS,
        title="Chave dividida",
        body=metade_corpo,
        structured={"resto": metade_estruturada},
    )

    texto = render_block_text(entry).text

    assert "sk-123456" not in texto
    assert "7890ABCDEF" not in texto
    assert REDACTED in texto


def test_duas_representacoes_do_mesmo_valor_nao_vazam_uma_pela_outra(
    session: Session, workspace: DevWorkspace
) -> None:
    """Regressão do bug encontrado ao implementar o Grupo B: uma primeira versão desta
    correção colava os valores de `structured` ao corpo **sem** contexto de campo (para
    pegar o caso de token partido) e *também* emitia a linha rotulada `chave: valor` — o
    mesmo valor aparecia duas vezes, uma protegida pelo nome do campo e outra não, e
    `password: hunter2` vazava pela cópia sem contexto mesmo com a cópia rotulada
    corretamente redigida. `_redact_body_and_structured` fecha isso redigindo cada
    representação **uma única vez**.
    """
    entry = _entry(
        session,
        workspace,
        domain=ContextDomain.STACK,
        title="Config",
        body="corpo qualquer",
        structured={"api_key": "abcdef123456"},
    )

    texto = render_block_text(entry).text

    assert "abcdef123456" not in texto
    assert texto.count(REDACTED) >= 1


# ============================================================ sub-etapa 2 — select_context


def test_select_context_pontua_conforme_a_tabela(
    session: Session, workspace: DevWorkspace, base_commit: str
) -> None:
    """GATE FUNCIONAL: candidatos variados, scores corretos."""
    sobreposta = _entry(
        session,
        workspace,
        domain=ContextDomain.MODULES,
        title="Módulo app",
        source_refs=["src/app.py"],
    )
    vizinha = _entry(
        session,
        workspace,
        domain=ContextDomain.MODULES,
        title="Módulo profundo",
        source_refs=["src/nested/deep.py"],
    )
    por_tag = _entry(
        session,
        workspace,
        domain=ContextDomain.RISKS,
        title="Risco",
        tags=["persistencia"],
    )
    solta = _entry(session, workspace, domain=ContextDomain.CONTRACTS, title="Contrato")

    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=["src/app.py"],
        max_context_tokens=100_000,
        affected_domains=[ContextDomain.MODULES],
        objective_terms=["Persistencia"],
    )

    por_id = {item.entry_id: item for item in selection.ranked}

    # `sobreposta` declara `source_refs=["src/app.py"]` e o candidato é exatamente
    # `"src/app.py"`: além de cobrir (sobreposição), é match literal exato — os dois
    # sinais somam, não se substituem ([03] §4 trata `exact_literal_match` como um sinal
    # a mais, não uma reclassificação da sobreposição).
    assert por_id[sobreposta.id].score.exact_literal_match == SCORE_EXACT_LITERAL_MATCH
    assert por_id[sobreposta.id].score.source_ref_overlap == SCORE_SOURCE_REF_OVERLAP
    assert por_id[sobreposta.id].score.affected_domain == SCORE_AFFECTED_DOMAIN
    assert por_id[sobreposta.id].score.proximity == PROXIMITY_MAX
    assert por_id[sobreposta.id].score.fresh == SCORE_FRESH
    assert por_id[sobreposta.id].score.total == 371

    # As outras três não têm `source_ref` igual a nenhum candidato — sinal zerado.
    assert por_id[vizinha.id].score.exact_literal_match == 0
    assert por_id[por_tag.id].score.exact_literal_match == 0
    assert por_id[solta.id].score.exact_literal_match == 0

    # Sem sobreposição; `src/nested` dista 1 salto de `src`.
    assert por_id[vizinha.id].score.source_ref_overlap == 0
    assert por_id[vizinha.id].score.proximity == PROXIMITY_MAX - 1
    assert por_id[vizinha.id].score.total == 100 + 29 + 10

    # Só tag e frescor: nenhum `source_ref`, então nenhuma proximidade.
    assert por_id[por_tag.id].score.tag_match == SCORE_TAG_MATCH
    assert por_id[por_tag.id].score.proximity == 0
    assert por_id[por_tag.id].score.total == SCORE_TAG_MATCH + SCORE_FRESH

    assert por_id[solta.id].score.total == SCORE_FRESH

    assert [item.entry_id for item in selection.ranked] == [
        sobreposta.id,
        vizinha.id,
        por_tag.id,
        solta.id,
    ]


def test_exact_literal_match_vence_glob_mesmo_sem_existir_no_commit(
    session: Session, workspace: DevWorkspace, base_commit: str
) -> None:
    """REPRODUÇÃO EXATA (Codex, AUD-008): `candidate_path = "src/new.py"`, que **não**
    existe no `base_commit`. Entrada A declara `source_ref = "src/new.py"` (literal
    exato); entrada B declara `source_ref = "src/**"` (glob, com proximidade favorável
    porque `src/` tem arquivos reais na árvore). A precisa vencer por
    `exact_literal_match` — não por coincidência de outro sinal.

    `domain`/`tags`/`fresh` são mantidos **iguais** entre A e B de propósito: variar só
    seria uma alavanca a mais para B vencer por outro motivo, e o que este teste prova é
    que `SCORE_EXACT_LITERAL_MATCH` sozinho supera `SCORE_SOURCE_REF_OVERLAP +
    PROXIMITY_MAX` (131 > 130), não que A vence em qualquer cenário.
    """
    literal_exata = _entry(
        session,
        workspace,
        domain=ContextDomain.MODULES,
        title="Arquivo novo, ainda não commitado",
        source_refs=["src/new.py"],
    )
    glob_com_proximidade = _entry(
        session,
        workspace,
        domain=ContextDomain.MODULES,
        title="Cobertura ampla de src",
        source_refs=["src/**"],
    )
    # Mesmo `updated_at`, para que o desempate não decida nada aqui — a prova é pelo score.
    _touch(literal_exata, seconds=0)
    _touch(glob_com_proximidade, seconds=0)
    session.flush()

    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=["src/new.py"],
        max_context_tokens=100_000,
        affected_domains=[ContextDomain.MODULES],
    )

    por_id = {item.entry_id: item for item in selection.ranked}
    a = por_id[literal_exata.id]
    b = por_id[glob_com_proximidade.id]

    assert a.score.exact_literal_match == SCORE_EXACT_LITERAL_MATCH
    assert b.score.exact_literal_match == 0
    # B tem sobreposição + proximidade favorável — o teto que A precisa superar sozinho.
    assert b.score.source_ref_overlap == SCORE_SOURCE_REF_OVERLAP
    assert b.score.proximity > 0
    assert a.score.total > b.score.total
    assert next(item.entry_id for item in selection.ranked) == literal_exata.id


def test_exact_literal_match_exige_candidato_especifico_nao_so_ref_literal(
    session: Session, workspace: DevWorkspace, base_commit: str
) -> None:
    """Um `source_ref` literal que **não** está entre os candidatos não ganha o bônus —
    "todos os `source_refs` são literais" não é a regra; é match contra um candidato
    específico."""
    entry = _entry(
        session,
        workspace,
        domain=ContextDomain.MODULES,
        title="Literal sem candidato correspondente",
        source_refs=["src/util.py"],
    )

    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=["src/app.py"],
        max_context_tokens=100_000,
    )

    scored = next(item for item in selection.ranked if item.entry_id == entry.id)
    assert scored.score.exact_literal_match == 0


def test_select_context_ordena_por_score_depois_recencia_depois_id(
    session: Session, workspace: DevWorkspace, base_commit: str
) -> None:
    """A chave é total: score desc → `updated_at` desc → `entry_id` asc."""
    antiga = _entry(session, workspace, domain=ContextDomain.STACK, title="Antiga")
    recente = _entry(session, workspace, domain=ContextDomain.STACK, title="Recente")
    _touch(antiga, seconds=0)
    _touch(recente, seconds=60)
    session.flush()

    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=[],
        max_context_tokens=100_000,
    )

    assert [item.entry_id for item in selection.ranked] == [recente.id, antiga.id]
    assert all(isinstance(item.updated_at_epoch_us, int) for item in selection.ranked)


def test_select_context_desempata_por_entry_id_quando_o_segundo_tambem_empata(
    session: Session, workspace: DevWorkspace, base_commit: str
) -> None:
    """Mesmo score e mesmo segundo de `updated_at`: sobra o `entry_id`, que é único."""
    primeira = _entry(session, workspace, domain=ContextDomain.STACK, title="A")
    segunda = _entry(session, workspace, domain=ContextDomain.STACK, title="B")
    _touch(primeira, seconds=42)
    _touch(segunda, seconds=42)
    session.flush()

    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=[],
        max_context_tokens=100_000,
    )

    esperado = sorted([primeira.id, segunda.id])
    assert [item.entry_id for item in selection.ranked] == esperado


def test_select_context_corta_por_orcamento_sem_truncar(
    session: Session, workspace: DevWorkspace, base_commit: str
) -> None:
    """GATE FUNCIONAL: o que não cabe inteiro sai inteiro, com `reason=budget`."""
    pequena = _entry(session, workspace, domain=ContextDomain.STACK, title="P", body="x\n")
    grande = _entry(
        session,
        workspace,
        domain=ContextDomain.STACK,
        title="G",
        body="y" * 4_000 + "\n",
    )
    _touch(pequena, seconds=10)
    _touch(grande, seconds=20)
    session.flush()

    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=[],
        max_context_tokens=100,
    )

    selecionados = [item.entry_id for item in selection.selected]
    assert pequena.id in selecionados
    assert grande.id not in selecionados
    assert {item.reason for item in selection.excluded} == {REASON_BUDGET}
    assert [item.path_or_entry for item in selection.excluded] == [grande.id]
    assert selection.approx_tokens <= 100

    # Nada foi truncado: o texto emitido do bloco pequeno é o texto inteiro dele.
    bloco = next(item for item in selection.selected if item.entry_id == pequena.id)
    assert bloco.text == render_block_text(pequena).text


def test_select_context_sempre_inclui_objective_mesmo_estourando_o_orcamento(
    session: Session, workspace: DevWorkspace, base_commit: str
) -> None:
    """GATE FUNCIONAL: `domain=objective` entra mesmo sozinha excedendo `max_context_tokens`."""
    objetivo = _entry(
        session,
        workspace,
        domain=ContextDomain.OBJECTIVE,
        title="Objetivo",
        body="o" * 8_000 + "\n",
    )
    outra = _entry(session, workspace, domain=ContextDomain.STACK, title="Stack", body="s\n")

    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=[],
        max_context_tokens=10,
    )

    assert [item.entry_id for item in selection.selected] == [objetivo.id]
    assert selection.approx_tokens > 10
    assert [item.path_or_entry for item in selection.excluded] == [outra.id]


def test_select_context_exclui_candidato_segredo_e_candidato_fora_do_workspace(
    session: Session, workspace: DevWorkspace, base_commit: str
) -> None:
    """[ADR-0006] item 9: segredo aparece **só** como caminho em `excluded`."""
    _entry(session, workspace, domain=ContextDomain.STACK, title="Stack")

    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=[".env", "../fora.txt", "src/app.py"],
        max_context_tokens=100_000,
    )

    motivos = {item.path_or_entry: item.reason for item in selection.excluded}
    assert motivos[".env"] == REASON_SECRET_POLICY
    assert motivos["../fora.txt"] == REASON_OUT_OF_WORKSPACE
    assert selection.candidate_paths == ("src/app.py",)


def test_select_context_normaliza_e_deduplica_candidatos(
    session: Session, workspace: DevWorkspace, base_commit: str
) -> None:
    _entry(session, workspace, domain=ContextDomain.STACK, title="Stack")

    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=["./src/app.py", "src//app.py", "src/util.py"],
        max_context_tokens=100_000,
    )

    assert selection.candidate_paths == ("src/app.py", "src/util.py")


def test_select_context_registra_divergencia_do_working_tree(
    session: Session, workspace: DevWorkspace, repo_path: Path, base_commit: str
) -> None:
    """[03] §3: a divergência é registrada mesmo quando não afeta entrada selecionada."""
    _entry(
        session,
        workspace,
        domain=ContextDomain.MODULES,
        title="Módulo",
        source_refs=["src/**"],
    )
    context_helpers.write(repo_path, "src/app.py", "print('mudou')\n")

    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=[],
        max_context_tokens=100_000,
    )

    divergencia = selection.working_tree_divergence
    assert divergencia["dirty_file_count"] == 1
    assert divergencia["covered"] == [{"path": "src/app.py", "kind": "modified"}]


def test_select_context_recusa_commit_ilegivel(
    session: Session, workspace_sem_git: DevWorkspace
) -> None:
    with pytest.raises(ContextTreeUnavailable):
        select_context(
            session,
            workspace_sem_git,
            base_commit="0" * 40,
            candidate_paths=[],
            max_context_tokens=100,
        )


# ============================================================ sub-etapa 4 — freeze_manifest


def test_freeze_manifest_grava_todos_os_campos(
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> None:
    """GATE FUNCIONAL: manifest gravado com todos os campos corretos."""
    entry = _entry(
        session,
        workspace,
        domain=ContextDomain.MODULES,
        title="Módulo app",
        source_refs=["src/app.py"],
    )
    task = context_helpers.make_task(session, workspace)

    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=["src/app.py"],
        max_context_tokens=100_000,
    )
    manifest = freeze_manifest(
        session,
        task,
        selection,
        base_branch="main",
        artifacts_dir=temp_settings.artifacts_dir,
    )

    assert manifest.task_id == task.id
    assert manifest.git_head == base_commit
    assert manifest.base_branch == "main"
    assert {linha["entry_id"] for linha in manifest.entries} == {entry.id}
    assert manifest.entries[0]["content_hash"] == entry.content_hash
    assert manifest.entries[0]["state"] == "fresh"
    assert [linha["path"] for linha in manifest.source_files] == ["src/app.py"]
    assert manifest.working_tree_divergence["dirty_file_count"] == 0
    assert manifest.derived[0]["kind"] == "file_map"
    assert manifest.derived[0]["hash"] == selection.file_map.hash
    assert manifest.derived[0]["item_count"] == 5
    assert manifest.renderer_version == RENDERER_VERSION
    assert manifest.approx_tokens == selection.approx_tokens
    assert manifest.total_chars == selection.total_chars
    assert len(manifest.manifest_hash) == 64
    assert manifest.rendered_context_ref == f"artifacts/{manifest.rendered_context_hash}.json"


def test_manifest_hash_ignora_id_task_id_e_created_at(
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> None:
    """GATE DE DETERMINISMO 3 e 7: identidade operacional não entra no hash semântico."""
    _entry(session, workspace, domain=ContextDomain.STACK, title="Stack")

    primeira = context_helpers.make_task(session, workspace, title="primeira")
    segunda = context_helpers.make_task(session, workspace, title="segunda")

    def congela(task: WorkspaceTask) -> ContextManifest:
        selection = select_context(
            session,
            workspace,
            base_commit=base_commit,
            candidate_paths=[],
            max_context_tokens=100_000,
        )
        return freeze_manifest(
            session,
            task,
            selection,
            base_branch="main",
            artifacts_dir=temp_settings.artifacts_dir,
        )

    um = congela(primeira)
    outro = congela(segunda)

    assert um.task_id != outro.task_id
    assert um.id != outro.id
    assert um.created_at is not None and outro.created_at is not None
    assert um.manifest_hash == outro.manifest_hash
    assert um.rendered_context_hash == outro.rendered_context_hash


def test_manifest_hash_ignora_a_ordem_das_colecoes_recebidas() -> None:
    """As cinco coleções são reordenadas por chave estável antes de hashear."""
    entries = [
        {"entry_id": "b", "domain": "stack", "title": "B", "content_hash": "2", "state": "fresh"},
        {"entry_id": "a", "domain": "stack", "title": "A", "content_hash": "1", "state": "fresh"},
    ]
    source_files = [{"path": "z.py", "blob_sha": "2"}, {"path": "a.py", "blob_sha": "1"}]
    covered = [{"path": "z.py", "kind": "modified"}, {"path": "a.py", "kind": "untracked"}]
    excluded = [
        {"path_or_entry": "z", "reason": "budget"},
        {"path_or_entry": "a", "reason": "budget"},
    ]
    derivado = [{"kind": "file_map", "hash": "abc", "item_count": 3}]

    def calcula(invertido: bool) -> str:
        return compute_manifest_hash(
            git_head="a" * 40,
            base_branch="main",
            entries=list(reversed(entries)) if invertido else entries,
            source_files=list(reversed(source_files)) if invertido else source_files,
            working_tree_divergence={
                "dirty_file_count": 2,
                "covered": list(reversed(covered)) if invertido else covered,
            },
            derived=derivado,
            excluded=list(reversed(excluded)) if invertido else excluded,
        )

    assert calcula(False) == calcula(True)


# ============================================================= sub-etapa 5 — render_context


def test_render_context_grava_o_artefato_e_e_idempotente(
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> None:
    """GATE FUNCIONAL + DETERMINISMO 4: bytes idênticos, e o blob não é regravado."""
    _entry(session, workspace, domain=ContextDomain.STACK, title="Stack")
    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=[],
        max_context_tokens=100_000,
    )

    primeiro = render_context(selection, artifacts_dir=temp_settings.artifacts_dir)
    bytes_gravados = primeiro.path.read_bytes()
    segundo = render_context(selection, artifacts_dir=temp_settings.artifacts_dir)

    assert primeiro.written is True
    assert segundo.written is False
    assert segundo.hash == primeiro.hash
    assert segundo.path.read_bytes() == bytes_gravados
    assert list(temp_settings.artifacts_dir.iterdir()) == [primeiro.path]


def test_render_context_reescreve_artifact_corrompido(
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> None:
    """GRUPO E (AUD-009): bytes reais em disco são lidos e o sha256 deles é comparado ao
    hash esperado **antes** de reaproveitar por idempotência. Um arquivo com o nome certo
    mas conteúdo corrompido (cópia interrompida, disco, edição por engano) não passa como
    "já existe, então está correto" — é regravado.
    """
    _entry(session, workspace, domain=ContextDomain.STACK, title="Stack")
    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=[],
        max_context_tokens=100_000,
    )

    primeiro = render_context(selection, artifacts_dir=temp_settings.artifacts_dir)
    assert primeiro.written is True

    # Corrompe o arquivo no caminho exato que o hash aponta.
    primeiro.path.write_bytes(b"{ isto nao e o conteudo correto }")
    corrompido = primeiro.path.read_bytes()
    assert hashlib.sha256(corrompido).hexdigest() != primeiro.hash

    segundo = render_context(selection, artifacts_dir=temp_settings.artifacts_dir)

    assert segundo.hash == primeiro.hash
    assert segundo.written is True  # regravou — não confiou no `exists()` sozinho
    bytes_corrigidos = segundo.path.read_bytes()
    assert hashlib.sha256(bytes_corrigidos).hexdigest() == segundo.hash
    assert bytes_corrigidos != corrompido


def test_hash_do_arquivo_em_disco_bate_com_o_registrado(
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> None:
    """GATE DE DETERMINISMO 9: sha256 dos bytes **relidos do disco**, em modo binário.

    É o teste que pegaria a tradução `\\n` → `\\r\\n` do modo texto no Windows: o hash
    registrado sairia dos bytes em memória e o do arquivo sairia dos bytes traduzidos.
    """
    _entry(
        session,
        workspace,
        domain=ContextDomain.ARCHITECTURE,
        title="Arquitetura",
        body="linha 1\nlinha 2\nlinha 3\n",
    )
    task = context_helpers.make_task(session, workspace)
    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=[],
        max_context_tokens=100_000,
    )
    manifest = freeze_manifest(
        session,
        task,
        selection,
        base_branch=None,
        artifacts_dir=temp_settings.artifacts_dir,
    )

    caminho = temp_settings.artifacts_dir / f"{manifest.rendered_context_hash}.json"
    with open(caminho, "rb") as handle:
        do_disco = hashlib.sha256(handle.read()).hexdigest()

    assert do_disco == manifest.rendered_context_hash
    assert b"\r\n" not in caminho.read_bytes()
    assert not caminho.read_bytes().startswith(b"\xef\xbb\xbf")  # sem BOM


def test_artefato_nao_contem_segredo(
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> None:
    """GATE FUNCIONAL: a redação é aplicada **antes** do hash; nada de `.env` no artefato."""
    segredo = "sk-ant-" + "B" * 60
    _entry(
        session,
        workspace,
        domain=ContextDomain.STACK,
        title="Credenciais",
        body=f"API_KEY={segredo}\n",
    )
    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=[".env"],
        max_context_tokens=100_000,
    )
    rendered = render_context(selection, artifacts_dir=temp_settings.artifacts_dir)

    bruto = rendered.path.read_bytes().decode("utf-8")
    assert segredo not in bruto
    assert REDACTED in bruto
    # `.env` aparece só como caminho excluído, nunca como conteúdo.
    assert [item.path_or_entry for item in selection.excluded] == [".env"]
    assert "SECRET=" not in bruto


def test_artefato_responde_depois_de_a_entrada_ser_apagada(
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> None:
    """[ADR-0006] item 9 e o gate da E5 no roadmap: o snapshot sobrevive à origem."""
    entry = _entry(
        session,
        workspace,
        domain=ContextDomain.DECISIONS,
        title="Decisão que será apagada",
        body="conteúdo que o Developer recebeu\n",
    )
    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=[],
        max_context_tokens=100_000,
    )
    rendered = render_context(selection, artifacts_dir=temp_settings.artifacts_dir)

    session.delete(entry)
    session.flush()
    assert session.get(ContextRegistryEntry, entry.id) is None

    payload = json.loads(rendered.path.read_text(encoding="utf-8"))
    bloco = payload["blocks"][0]
    assert bloco["origin"]["entry_id"] == entry.id
    assert bloco["origin"]["title"] == "Decisão que será apagada"
    assert "conteúdo que o Developer recebeu" in bloco["text"]
    assert bloco["truncated"] is False
    assert "created_at" not in payload


# ====================================================== determinismo: 5, 6, 7 e 8


def _resultado(session: Session, workspace: DevWorkspace, base_commit: str) -> dict[str, Any]:
    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=["src/app.py"],
        max_context_tokens=100_000,
        affected_domains=[ContextDomain.MODULES],
        objective_terms=["persistencia"],
    )
    return {
        "file_map_hash": selection.file_map.hash,
        "ordem": [item.title for item in selection.ranked],
        "scores": [item.score.total for item in selection.ranked],
        "textos": [item.text for item in selection.selected],
    }


def _povoa(session: Session, workspace: DevWorkspace, invertido: bool) -> None:
    """As mesmas quatro entradas, inseridas em duas ordens diferentes."""
    receitas = [
        ("Módulo app", ContextDomain.MODULES, ["src/app.py"], ["persistencia"]),
        ("Módulo profundo", ContextDomain.MODULES, ["src/nested/deep.py"], []),
        ("Risco", ContextDomain.RISKS, [], ["persistencia"]),
        ("Contrato", ContextDomain.CONTRACTS, [], []),
    ]
    for indice, (title, domain, refs, tags) in enumerate(
        reversed(receitas) if invertido else receitas
    ):
        entry = _entry(
            session,
            workspace,
            domain=domain,
            title=title,
            body=f"corpo de {title}\n",
            source_refs=refs,
            tags=tags,
        )
        # `updated_at` é o critério de desempate e precisa ser o **mesmo** nas duas ordens:
        # ele é derivado do título, não da ordem de inserção.
        _touch(entry, seconds=receitas.index((title, domain, refs, tags)))
        del indice
    session.flush()


def test_ordem_de_insercao_no_banco_nao_altera_o_resultado(
    session_factory: sessionmaker[Session], repo_path: Path, base_commit: str
) -> None:
    """GATE DE DETERMINISMO 5: as mesmas entradas, em duas ordens de inserção."""
    resultados = []
    for invertido in (False, True):
        with session_factory() as sessao:
            workspace = context_helpers.make_workspace(sessao, repo_path, name=f"ws-{invertido}")
            _povoa(sessao, workspace, invertido)
            resultados.append(_resultado(sessao, workspace, base_commit))
            sessao.rollback()

    assert resultados[0] == resultados[1]


def test_ordem_das_chaves_de_structured_nao_altera_os_hashes(
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> None:
    """GATE DE DETERMINISMO 6, na camada do artefato.

    A **mesma** entrada é reescrita com as mesmas chaves em outra ordem: `entry_id` faz
    parte da identidade do bloco (é o que responde de onde o texto veio depois de a entrada
    sumir), então comparar duas entradas diferentes mediria outra coisa.
    """
    entry = _entry(
        session,
        workspace,
        domain=ContextDomain.DECISIONS,
        title="Decisão",
        structured={"alfa": 1, "beta": {"x": 1, "y": 2}, "zeta": 3},
    )
    _touch(entry, seconds=7)
    session.flush()

    def hash_atual() -> str:
        selection = select_context(
            session,
            workspace,
            base_commit=base_commit,
            candidate_paths=[],
            max_context_tokens=100_000,
        )
        return render_context(selection, artifacts_dir=temp_settings.artifacts_dir).hash

    direta = hash_atual()

    entry.structured = {"zeta": 3, "beta": {"y": 2, "x": 1}, "alfa": 1}
    session.flush()
    invertida = hash_atual()

    assert direta == invertida


def test_updated_at_muda_selecao_mas_nao_a_identidade_semantica(
    session: Session, workspace: DevWorkspace, base_commit: str
) -> None:
    """GATE DE DETERMINISMO 7: não confundir desempate com identidade.

    `updated_at` **é** critério de desempate e portanto pode mudar a ordem da seleção. O que
    não pode mudar hash nenhum é o timestamp **operacional** — `ContextManifest.created_at`,
    `id`, `task_id` — coberto por `test_manifest_hash_ignora_id_task_id_e_created_at`.
    """
    primeira = _entry(session, workspace, domain=ContextDomain.STACK, title="A")
    segunda = _entry(session, workspace, domain=ContextDomain.STACK, title="B")

    _touch(primeira, seconds=0)
    _touch(segunda, seconds=10)
    session.flush()
    antes = [
        item.entry_id
        for item in select_context(
            session,
            workspace,
            base_commit=base_commit,
            candidate_paths=[],
            max_context_tokens=100_000,
        ).ranked
    ]

    _touch(primeira, seconds=20)
    session.flush()
    depois = [
        item.entry_id
        for item in select_context(
            session,
            workspace,
            base_commit=base_commit,
            candidate_paths=[],
            max_context_tokens=100_000,
        ).ranked
    ]

    assert antes == [segunda.id, primeira.id]
    assert depois == [primeira.id, segunda.id]


def test_epoch_microseconds_nunca_passa_por_float() -> None:
    momento = datetime(2026, 9, 9, 12, 34, 56, 789_123, tzinfo=UTC)
    valor = epoch_microseconds(momento)

    assert isinstance(valor, int)
    segundos_inteiros = int(datetime(2026, 9, 9, 12, 34, 56, tzinfo=UTC).timestamp())
    assert valor == segundos_inteiros * 1_000_000 + 789_123


def test_desempate_por_microssegundo(
    session: Session, workspace: DevWorkspace, base_commit: str
) -> None:
    """GRUPO D (AUD-005): mesmo score, mesmo segundo — a mais recente por microssegundo
    vence. Fecha o finding onde `epoch_seconds` (granularidade de segundo) fazia duas
    entradas só diferindo em microssegundo empatarem e caírem para `entry_id`, que não
    tem relação nenhuma com recência.
    """
    antiga = _entry(session, workspace, domain=ContextDomain.STACK, title="A")
    recente = _entry(session, workspace, domain=ContextDomain.STACK, title="B")

    # Mesmo segundo civil, microssegundo diferente — o segundo inteiro empataria.
    momento_base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    antiga.updated_at = momento_base.replace(microsecond=100)
    recente.updated_at = momento_base.replace(microsecond=999_999)
    session.flush()

    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=[],
        max_context_tokens=100_000,
    )

    assert [item.entry_id for item in selection.ranked] == [recente.id, antiga.id]
    ranked_by_id = {item.entry_id: item for item in selection.ranked}
    assert (
        ranked_by_id[recente.id].updated_at_epoch_us > ranked_by_id[antiga.id].updated_at_epoch_us
    )


_SCRIPT_FILHO = """
import json, sys
sys.path.insert(0, sys.argv[1])

from pathlib import Path

from app.config import AppSettings
from app.context_engine import compute_manifest_hash, render_context, select_context
from app.db.enums import ContextDomain
from app.db.models import DevWorkspace
from app.db.session import create_engine, create_session_factory

pedido = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))

settings = AppSettings(
    data_dir=Path(pedido["data_dir"]),
    database_url=pedido["database_url"],
    web_dist_dir=Path(pedido["data_dir"]) / "sem-build",
)
engine = create_engine(settings)
factory = create_session_factory(engine)

with factory() as session:
    workspace = session.get(DevWorkspace, pedido["workspace_id"])
    selection = select_context(
        session,
        workspace,
        base_commit=pedido["base_commit"],
        candidate_paths=pedido["candidate_paths"],
        max_context_tokens=pedido["max_context_tokens"],
        affected_domains=[ContextDomain(nome) for nome in pedido["affected_domains"]],
        objective_terms=pedido["objective_terms"],
    )
    rendered = render_context(selection, artifacts_dir=Path(pedido["artifacts_dir"]))
    saida = {
        "file_map_hash": selection.file_map.hash,
        "ordem": [item.entry_id for item in selection.ranked],
        "scores": [item.score.total for item in selection.ranked],
        "rendered_context_hash": rendered.hash,
        "manifest_hash": compute_manifest_hash(
            git_head=selection.base_commit,
            base_branch="main",
            entries=[item.as_manifest_entry() for item in selection.selected],
            source_files=[dict(linha) for linha in selection.source_files],
            working_tree_divergence=selection.working_tree_divergence,
            derived=[selection.file_map.as_derived_record()],
            excluded=[item.as_canonical() for item in selection.excluded],
        ),
    }

engine.dispose()
print(json.dumps(saida, sort_keys=True))
"""


def test_processo_python_separado_produz_os_mesmos_hashes(
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
    tmp_path: Path,
) -> None:
    """GATE DE DETERMINISMO 8: outro processo, outro `PYTHONHASHSEED`, mesmos hashes.

    O valor deste teste está no `PYTHONHASHSEED`: o processo pai tem um seed, e os dois
    filhos recebem seeds **explicitamente diferentes**. `hash()` de `str` é randomizado por
    esse seed, e com ele muda a ordem de iteração de qualquer `set` de strings. Se alguma
    coleção chegasse a um hash sem ordenação explícita, as três execuções divergiriam — é a
    reprodução direta da classe de bug que esta fase existe para fechar.
    """
    _povoa(session, workspace, invertido=False)
    session.commit()

    pedido = {
        "data_dir": str(temp_settings.data_dir),
        "database_url": temp_settings.sqlalchemy_url,
        "artifacts_dir": str(tmp_path / "artefatos-do-filho"),
        "workspace_id": workspace.id,
        "base_commit": base_commit,
        "candidate_paths": ["src/app.py"],
        "max_context_tokens": 100_000,
        "affected_domains": ["modules"],
        "objective_terms": ["persistencia"],
    }
    arquivo = tmp_path / "pedido.json"
    arquivo.write_text(json.dumps(pedido), encoding="utf-8")

    script = tmp_path / "filho.py"
    script.write_text(_SCRIPT_FILHO, encoding="utf-8")

    saidas = []
    for seed in ("0", "12345"):
        ambiente = dict(os.environ)
        ambiente["PYTHONHASHSEED"] = seed
        resultado = subprocess.run(  # noqa: S603 — argv literal, sem shell
            [sys.executable, str(script), str(API_ROOT), str(arquivo)],
            capture_output=True,
            text=True,
            check=False,
            env=ambiente,
        )
        assert resultado.returncode == 0, resultado.stderr
        saidas.append(json.loads(resultado.stdout))

    assert saidas[0] == saidas[1]

    # E o pai concorda com os dois.
    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=["src/app.py"],
        max_context_tokens=100_000,
        affected_domains=[ContextDomain.MODULES],
        objective_terms=["persistencia"],
    )
    do_pai = render_context(selection, artifacts_dir=temp_settings.artifacts_dir)

    assert selection.file_map.hash == saidas[0]["file_map_hash"]
    assert [item.entry_id for item in selection.ranked] == saidas[0]["ordem"]
    assert [item.score.total for item in selection.ranked] == saidas[0]["scores"]
    assert do_pai.hash == saidas[0]["rendered_context_hash"]
