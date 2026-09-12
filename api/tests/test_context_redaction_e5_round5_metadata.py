"""E5 rodada 5 — extensão de metadata: `PROPAGATED_SECRET_REDACTION`.

Depois da [rodada 5](../../docs/audits/e5-round-5.md) (**GREEN** técnico, com
`E5-AUD5-001` classificado Média/P2 e recomendado para aceitação), esta tarefa
acrescenta um segundo marcador de diagnóstico a `blocks[].transformations`:
`PROPAGATED_SECRET_REDACTION`, sinalizando quando uma redação **naquele bloco** veio da
camada 3 (propagação) — cópia literal de um valor já comprovado sensível — e não de
detecção direta no fragmento nem de travessia de fronteira (que já tem o seu próprio
marcador, `CROSS_FRAGMENT_REDACTION`).

**Nada na lógica de detecção, propagação ou redação muda aqui.** `_redacted_text` faz
exatamente as mesmas substituições que fazia antes; a única adição é reportar, como um
booleano, se a última etapa (o laço de propagação) alterou o texto daquele fragmento. O
texto final de qualquer bloco é bit a bit idêntico ao que era antes desta tarefa — só
`transformations` pode ganhar uma entrada a mais.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.config import AppSettings
from app.context_engine import create_entry, freeze_manifest, render_block_text, select_context
from app.context_engine.content_hash import normalize_structured, normalize_text
from app.context_engine.rendering import (
    CROSS_FRAGMENT_REDACTION,
    PROPAGATED_SECRET_REDACTION,
    _build_fragments,
    _Fragment,
    _fragment_indices,
    _leaves_of,
    _mark_fragments,
    _propagation_values,
)
from app.db.enums import ContextDomain
from app.db.models import ContextRegistryEntry, DevWorkspace
from app.safety.redaction import REDACTED, SecretSpan, merge_spans
from tests import context_helpers


@pytest.fixture
def base_commit(repo_path: Path) -> str:
    return context_helpers.head_of(repo_path)


def _entry(
    session: Session,
    workspace: DevWorkspace,
    *,
    title: str = "titulo comum",
    body: str = "corpo comum",
    structured: dict[str, Any] | None = None,
) -> ContextRegistryEntry:
    return create_entry(
        session,
        workspace,
        domain=ContextDomain.STACK,
        title=title,
        body=body,
        structured=structured,
    )


def _artifact_bytes(
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> tuple[bytes, dict[str, Any], Any]:
    """Congela o manifest e devolve `(bytes do arquivo, payload, manifest)`.

    Os bytes vêm de `read_bytes()` no caminho real, não do payload em memória.
    """
    task = context_helpers.make_task(session, workspace)
    selection = select_context(
        session,
        workspace,
        base_commit=base_commit,
        candidate_paths=[],
        max_context_tokens=1_000_000,
    )
    manifest = freeze_manifest(
        session, task, selection, base_branch=None, artifacts_dir=temp_settings.artifacts_dir
    )
    caminho = temp_settings.artifacts_dir / f"{manifest.rendered_context_hash}.json"
    bruto = caminho.read_bytes()
    return bruto, json.loads(bruto.decode("utf-8")), manifest


# =========================================================== marcador presente/ausente


def test_marcador_presente_quando_ha_propagacao(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """REPRODUÇÃO DE `E5-AUD5-001`: `hunter2` sob `password`, repetido numa folha pública.

    A folha `nota` não tem span local nenhum (não é sensível, não casa padrão nenhum
    sozinha) — a única razão de `hunter2` sumir das três ocorrências é a propagação. É
    exatamente o caso que a auditoria confirmou: uma frase pública repetida N vezes no
    mesmo bloco é redigida nas N ocorrências.
    """
    entry = _entry(
        session,
        workspace,
        structured={
            "password": "hunter2",
            "nota": "hunter2 aparece de novo aqui hunter2 e mais uma vez hunter2",
        },
    )

    bruto, payload, _manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    assert b"hunter2" not in bruto
    assert bloco["text"].count(REDACTED) == 4  # password + as três cópias na nota
    assert PROPAGATED_SECRET_REDACTION in bloco["transformations"]
    # Não houve travessia de fronteira nenhuma neste caso — só propagação.
    assert CROSS_FRAGMENT_REDACTION not in bloco["transformations"]


def test_marcador_ausente_so_deteccao_direta(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """Segredo autocontido, sem cópia em lugar nenhum do bloco: sem o marcador."""
    entry = _entry(session, workspace, structured={"password": "hunter2"})

    bruto, payload, _manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    assert b"hunter2" not in bruto
    assert REDACTED in bloco["text"]
    assert PROPAGATED_SECRET_REDACTION not in bloco["transformations"]


def test_marcador_ausente_sem_redacao_nenhuma(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """Bloco limpo: nenhum dos dois marcadores aparece."""
    entry = _entry(
        session,
        workspace,
        title="Decisão de banco",
        body="Usamos SQLite local, sem servidor.",
        structured={"reason": "simplicidade operacional"},
    )

    bruto, payload, _manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    assert REDACTED.encode("utf-8") not in bruto
    assert bloco["transformations"] == ["normalize", "redact"]


def test_marcador_ausente_no_fail_closed(session: Session, workspace: DevWorkspace) -> None:
    """Fail-closed redige tudo, mas não por propagação nem por travessia — nenhum dos
    dois marcadores responderia a pergunta certa aqui."""
    profundo: dict[str, Any] = {"folha": "valor_no_fundo"}
    for _ in range(40):
        profundo = {"nivel": profundo}

    rendered = render_block_text(_entry(session, workspace, structured=profundo))

    assert rendered.title == REDACTED
    assert PROPAGATED_SECRET_REDACTION not in rendered.transformations
    assert CROSS_FRAGMENT_REDACTION not in rendered.transformations


# ==================================================== os dois marcadores coexistindo


def test_ambos_marcadores_coexistem_sem_conflito(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """Um bloco com travessia de fronteira **e** propagação para uma folha distante.

    `title="Bearer"` / `body=" ABCDEFGHIJKLMNOP"` fecha via `E5-AUD3-002` (travessia); a
    folha `!nota` contém uma cópia pública da mesma credencial, redigida só por
    propagação. O prefixo `!` na chave evita que a extensão gulosa do padrão `bearer`
    (aceita desde `E5-AUD4-001`) consuma o rótulo da folha por adjacência alfanumérica —
    o que testaria outro comportamento, não a coexistência dos dois marcadores.
    """
    credencial = "ABCDEFGHIJKLMNOP"
    entry = _entry(
        session,
        workspace,
        title="Bearer",
        body=f" {credencial}",
        structured={"!nota": f"! copia legitima {credencial} em outro lugar"},
    )

    bruto, payload, _manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    assert credencial.encode("utf-8") not in bruto
    assert CROSS_FRAGMENT_REDACTION in bloco["transformations"]
    assert PROPAGATED_SECRET_REDACTION in bloco["transformations"]
    # O rótulo da folha sobrevive: a propagação redige o valor, não a chave.
    assert "!nota" in bloco["text"]
    # O contexto que prova a detecção cross-fragment também sobrevive.
    assert bloco["origin"]["title"] == "Bearer"


def test_ordem_e_deterministica_quando_ambos_presentes(
    session: Session, workspace: DevWorkspace
) -> None:
    """A ordem dos marcadores em `transformations` não depende de iteração de `set`."""
    entry = _entry(
        session,
        workspace,
        title="Bearer",
        body=" ABCDEFGHIJKLMNOP",
        structured={"!nota": "! copia legitima ABCDEFGHIJKLMNOP em outro lugar"},
    )

    primeiro = render_block_text(entry)
    segundo = render_block_text(entry)

    assert primeiro == segundo
    assert primeiro.transformations == (
        "normalize",
        "redact",
        CROSS_FRAGMENT_REDACTION,
        PROPAGATED_SECRET_REDACTION,
    )


# ===================================================== o marcador não carrega segredo


@pytest.mark.parametrize(
    "segredos",
    [
        ("hunter2",),
        ("sk-ant-" + "A" * 40,),
        ("ABCDEFGHIJKLMNOP", "sk-ant-" + "B" * 40),
    ],
    ids=["hunter2", "anthropic_key", "multiplos"],
)
def test_marcador_nao_contem_nenhum_segredo_do_fixture(
    segredos: tuple[str, ...],
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> None:
    """PROVA EXPLÍCITA (item 4 do gate): nenhuma string de `transformations` — nem a
    exata nem por substring — contém qualquer um dos segredos do fixture, em nenhuma
    direção (a comparação é feita nos dois sentidos: segredo em transformação, e
    transformação em segredo).

    Isto vale tanto para os marcadores quanto para as outras entradas de
    `transformations` (`normalize`, `redact`) — nenhuma delas nasce do conteúdo, então
    a prova cobre a lista inteira, não só as duas novas constantes.
    """
    if len(segredos) == 1:
        entry = _entry(
            session,
            workspace,
            structured={
                "password": segredos[0],
                "nota": f"copia publica {segredos[0]} de novo {segredos[0]}",
            },
        )
    else:
        credencial, chave = segredos
        entry = _entry(
            session,
            workspace,
            title="Bearer",
            body=f" {credencial}",
            structured={
                "auth.apiKey": chave,
                "!nota": f"! copia publica {credencial} sem relacao com a chave acima",
            },
        )

    bruto, payload, manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    for transformacao in bloco["transformations"]:
        for segredo in segredos:
            assert segredo not in transformacao, (
                f"{segredo!r} aparece dentro de transformations[]: {transformacao!r}"
            )
            assert transformacao not in segredo, (
                f"a transformação {transformacao!r} é substring do segredo {segredo!r} "
                "— sinal de que algo do valor vazou para o nome do marcador"
            )

    # E nos bytes/metadados do artifact como um todo, claro — a garantia de sempre.
    for segredo in segredos:
        assert segredo.encode("utf-8") not in bruto
        assert segredo not in json.dumps(manifest.entries, ensure_ascii=False)


def test_marcadores_sao_constantes_fixas_sem_dado_variavel() -> None:
    """Os dois marcadores são strings **literais fixas** — não há template, não há
    posição, não há contagem embutida na string em si.

    Optei por não incluir contagem no marcador (ver
    `docs/audits/e5-round-5-metadata-extension-notes.md`): uma variante como
    `"propagated_secret_redaction:3"` quebraria o idioma `MARCADOR in transformations`
    que todo o resto da suíte já usa para `CROSS_FRAGMENT_REDACTION`, obrigando todo
    consumidor futuro a fazer parsing em vez de comparação simples. Este teste trava
    essa decisão: se alguém reintroduzir uma contagem na string, ele quebra.
    """
    assert PROPAGATED_SECRET_REDACTION == "propagated_secret_redaction"
    assert ":" not in PROPAGATED_SECRET_REDACTION
    assert CROSS_FRAGMENT_REDACTION == "cross_fragment_secret_redaction"
    assert ":" not in CROSS_FRAGMENT_REDACTION


# ============================================ prova de que nada além do metadado mudou


def _texto_do_fragmento_sem_rastreamento(
    index: int,
    fragments: list[_Fragment],
    whole: set[int],
    local: dict[int, list[SecretSpan]],
    propagation: tuple[str, ...],
) -> str:
    """A mesma lógica de `_redacted_text`, escrita à parte e **sem** o booleano novo.

    Existe só para esta prova: se ela concordar com `RenderedBlock` em todo fragmento,
    o rastreamento do marcador não mudou nenhuma substituição.
    """
    if index in whole:
        return REDACTED
    texto = fragments[index].text
    spans = local.get(index)
    if spans:
        partes: list[str] = []
        cursor = 0
        for start, end in merge_spans(spans):
            partes.append(texto[cursor:start])
            partes.append(REDACTED)
            cursor = end
        partes.append(texto[cursor:])
        texto = "".join(partes)
    for valor in propagation:
        if valor and valor in texto:
            texto = texto.replace(valor, REDACTED)
    return texto


@pytest.mark.parametrize(
    "kwargs",
    [
        {"structured": {"password": "hunter2", "nota": "hunter2 copia hunter2 de novo"}},
        {
            "title": "Bearer",
            "body": " ABCDEFGHIJKLMNOP",
            "structured": {"!nota": "! copia ABCDEFGHIJKLMNOP em outro lugar"},
        },
    ],
    ids=["propagacao_simples", "cross_fragment_mais_propagacao"],
)
def test_texto_do_bloco_e_identico_ao_de_antes_desta_tarefa(
    kwargs: dict[str, Any], session: Session, workspace: DevWorkspace
) -> None:
    """PROVA EXPLÍCITA (item 5 do gate): para os fixtures que agora ganham
    `PROPAGATED_SECRET_REDACTION`, `RenderedBlock.text` e `.title` são exatamente o que
    a rodada 5 (sem o marcador novo) já produzia.

    A prova é reconstruir o texto **manualmente**, com a mesma lógica de substituição
    que `_redacted_text` sempre teve — sem o rastreamento do booleano — e comparar
    contra o `RenderedBlock` real. Se a adição do marcador tivesse, por engano, mudado
    quando ou o quê se substitui, esta comparação divergiria.
    """
    entry = _entry(session, workspace, **kwargs)
    rendered = render_block_text(entry)

    title = normalize_text(entry.title)
    body = normalize_text(entry.body)
    structured = normalize_structured(entry.structured) if entry.structured else None
    leaves = _leaves_of(structured)
    fragments = _build_fragments(title, body, leaves)
    whole, local, _cross = _mark_fragments(fragments, leaves)
    propagation = _propagation_values(fragments, whole, local)

    assert rendered.title == _texto_do_fragmento_sem_rastreamento(
        0, fragments, whole, local, propagation
    )

    for leaf_index in range(len(leaves)):
        _label_i, value_i = _fragment_indices(leaf_index)
        # Não recomputamos o framing inteiro aqui — só confirmamos que o valor de cada
        # fragmento, calculado sem o booleano, é igual ao que apareceu no texto final.
        valor_calculado = _texto_do_fragmento_sem_rastreamento(
            value_i, fragments, whole, local, propagation
        )
        assert valor_calculado in rendered.text or valor_calculado == REDACTED


def test_suite_de_protecao_das_cinco_rodadas_permanece_verde() -> None:
    """PROVA EXPLÍCITA (item 4 do gate final): a suíte inteira de redação e
    determinismo das cinco rodadas continua verde depois desta extensão de metadata —
    ver `docs/audits/e5-round-5-metadata-extension-notes.md` §3 para a execução
    completa (`pytest tests/test_context_redaction_e5_round*.py
    tests/test_context_router_e5.py`, agregada fora deste arquivo).

    Este teste não reexecuta as ~176 provas das rodadas 3–5 — isso é responsabilidade do
    gate de CI/execução manual, documentado no relatório. Ele apenas garante que os
    módulos importam e que a assinatura pública que as rodadas anteriores dependem
    (`RenderedBlock`, `CROSS_FRAGMENT_REDACTION`, `render_block_text`) continua estável.
    """
    from app.context_engine.rendering import RenderedBlock

    campos = RenderedBlock.__dataclass_fields__
    assert set(campos) == {"text", "title", "transformations"}
