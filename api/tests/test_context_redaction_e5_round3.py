"""E5 rodada 3 — o pipeline de redação em três camadas, sob ataque.

Cobre, com reprodução exata, os findings das duas auditorias
([e5-round-1](../../docs/audits/e5-round-1.md),
[e5-round-2](../../docs/audits/e5-round-2.md)) e as combinações que a rodada 2 pediu.

O desenho sob teste está normativo em
[03](../../docs/architecture/03-context-architecture.md) §4: camada estrutural
(`is_sensitive_key` marcando subárvore), camada posicional (`detect_secret_spans`
mapeado de volta ao fragmento de origem) e camada de propagação (valor comprovadamente
sensível redigido em toda ocorrência literal).

**Nenhum teste aqui afirma que o vazamento não existe porque a implementação diz que
não.** Cada um procura o valor cru em todas as superfícies que o carregam:
`blocks[].text`, `blocks[].origin.title`, `ContextManifest.entries[].title`, e os
**bytes relidos do disco** do artifact.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.config import AppSettings
from app.context_engine import (
    Transformation,
    create_entry,
    freeze_manifest,
    render_block_text,
    select_context,
)
from app.db.enums import ContextDomain
from app.db.models import ContextRegistryEntry, DevWorkspace
from app.safety.redaction import REDACTED, detect_secret_spans, is_sensitive_key
from tests import context_helpers

#: Um segredo reconhecível pelo padrão `anthropic_key`, longo o bastante para não haver
#: dúvida de que o redator o vê quando ele está inteiro num campo só.
CHAVE_ANTHROPIC = "sk-ant-" + "A" * 40


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


def _superficies(
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
    entry: ContextRegistryEntry,
) -> dict[str, str]:
    """Todas as superfícies emitidas, incluindo os **bytes relidos do disco**.

    Devolve um dicionário `nome -> texto` para que a busca pelo valor cru seja feita em
    todas de uma vez: um vazamento em `origin.title` que não aparece em `blocks[].text`
    foi exatamente o finding `E5-AUD-002`.
    """
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

    caminho = temp_settings.artifacts_dir / f"{manifest.rendered_context_hash}.json"
    bruto = caminho.read_bytes().decode("utf-8")
    payload = json.loads(bruto)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)
    linha = next(item for item in manifest.entries if item["entry_id"] == entry.id)
    escolhido = next(item for item in selection.selected if item.entry_id == entry.id)

    return {
        "blocks[].text": bloco["text"],
        "blocks[].origin.title": bloco["origin"]["title"],
        "manifest.entries[].title": str(linha["title"]),
        "ScoredEntry.title": escolhido.title,
        "bytes do artifact": bruto,
    }


def _assert_nao_vaza(superficies: dict[str, str], *valores: str) -> None:
    vazamentos = [
        f"{valor!r} em {nome}"
        for nome, texto in superficies.items()
        for valor in valores
        if valor and valor in texto
    ]
    assert not vazamentos, f"valor autoral cru sobreviveu: {vazamentos}"


# ============================================================== camada única de detecção


def test_um_so_motor_de_deteccao() -> None:
    """`is_sensitive_key` e `assigned_secret` leem a mesma lista; `redact` é wrapper de
    `detect_secret_spans`.

    Prova por comportamento, não por inspeção: todo nome que `is_sensitive_key` aceita
    também dispara o padrão `assigned_secret` quando escrito como atribuição.
    """
    from app.safety.redaction import _SENSITIVE_KEY_NAMES, redact

    for nome in sorted(_SENSITIVE_KEY_NAMES):
        assert is_sensitive_key(nome), nome
        assert REDACTED in redact(f"{nome}: valor-secreto-longo"), nome


def test_detect_secret_spans_aponta_para_o_texto_original() -> None:
    """Os spans são coordenadas do texto **recebido** — é o que permite mapear de volta
    para o fragmento de origem em vez de recortar o resultado por delimitador."""
    texto = "prefixo " + CHAVE_ANTHROPIC + " sufixo"
    spans = detect_secret_spans(texto)

    assert spans
    assert any(
        texto[span.replacement_span.start : span.replacement_span.end] == CHAVE_ANTHROPIC
        for span in spans
    )


def test_secret_span_separa_reconhecimento_de_substituicao() -> None:
    """`E5-AUD3-002`: os dois intervalos são coisas diferentes em padrão de prefixo
    preservado, e tratá-los como um só foi o vazamento da rodada 3.

    `Bearer ` é contexto que **prova** a detecção (entra em `recognition_span`) mas
    **não** é apagado (fora de `replacement_span`) — é o que mantém `redact()` idêntico
    ao que sempre produziu.
    """
    texto = "Bearer ABCDEFGHIJKLMNOP"
    span = next(item for item in detect_secret_spans(texto) if item.pattern_name == "bearer")

    assert texto[span.recognition_span.start : span.recognition_span.end] == texto
    assert texto[span.replacement_span.start : span.replacement_span.end] == "ABCDEFGHIJKLMNOP"
    assert span.recognition_span.start < span.replacement_span.start


def test_intervalos_coincidem_em_padrao_sem_prefixo_preservado() -> None:
    """Na maioria dos padrões os dois intervalos são o mesmo — a distinção só existe
    onde há prefixo a preservar."""
    texto = "chave " + CHAVE_ANTHROPIC
    span = next(item for item in detect_secret_spans(texto) if item.pattern_name == "anthropic_key")

    assert span.recognition_span == span.replacement_span


def test_is_sensitive_key_nao_dispara_em_nome_legitimo() -> None:
    """`tokenizer` contém `token`, `secretary` contém `secret` — nenhum é campo sensível.

    Casar por substring redigiria `{"tokenizer": "gpt-4"}` inteiro. A comparação é por
    **componente** do nome, não por substring.
    """
    for sensivel in ("password", "api_key", "apiKey", "db_password", "auth.token"):
        assert is_sensitive_key(sensivel), sensivel
    for legitimo in ("tokenizer", "secretary", "keyboard", "title", "reason", "decision"):
        assert not is_sensitive_key(legitimo), legitimo


# ================================================= rodada 1 — regressão, não pode voltar


def test_r1_segredo_no_titulo_nao_sobrevive_em_nenhuma_superficie(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """`E5-AUD-002`: o título era redigido em `blocks[].text` e sobrevivia cru em
    `origin.title`, `entries[].title` e nos bytes do artifact."""
    entry = _entry(session, workspace, title=f"chave {CHAVE_ANTHROPIC}")

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)

    _assert_nao_vaza(superficies, CHAVE_ANTHROPIC)
    assert REDACTED in superficies["blocks[].origin.title"]
    assert REDACTED in superficies["manifest.entries[].title"]


def test_r1_password_escalar_em_structured(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """`E5-AUD-003`: `{"password": "hunter2"}` chegava intacto ao artifact."""
    entry = _entry(session, workspace, structured={"password": "hunter2"})

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)

    _assert_nao_vaza(superficies, "hunter2")


def test_r1_segredo_partido_entre_body_e_structured(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """`E5-AUD-004`: as duas metades sobreviviam porque o framing as separava antes da
    detecção."""
    entry = _entry(session, workspace, body="sk-123456", structured={"resto": "7890ABCDEF"})

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)

    _assert_nao_vaza(superficies, "sk-123456", "7890ABCDEF")


# ========================================================= rodada 2 — os cinco findings


def test_e5_aud2_001a_segredo_partido_com_campo_password_adicional(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """REPRODUÇÃO EXATA: `body="sk-123456"`, `structured={"a":"7890ABCDEF",
    "password":"hunter2"}`.

    A abordagem de contagem media uma redação independente (a folha `password`) contra a
    redação da leitura colada e concluía que nada de novo havia aparecido — as duas
    metades do token partido saíam intactas. Aqui não há contagem: o par ordenado
    `(body, a)` é lido e o span que atravessa a fronteira marca os dois fragmentos,
    independentemente de quantas outras redações existam no bloco.
    """
    entry = _entry(
        session,
        workspace,
        body="sk-123456",
        structured={"a": "7890ABCDEF", "password": "hunter2"},
    )

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)

    _assert_nao_vaza(superficies, "sk-123456", "7890ABCDEF", "hunter2")


def test_e5_aud2_001b_segredo_partido_com_folha_interposta(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """REPRODUÇÃO EXATA: `body="sk-123456"`, `structured={"a":"!!!",
    "resto":"7890ABCDEF"}`.

    Na ordem canônica, `a` fica **entre** `body` e `resto`, e a leitura colada testava
    `sk-123456!!!7890ABCDEF` — nunca a fronteira que importava. O par ordenado
    `(body, resto)` é lido diretamente, então a folha interposta é irrelevante.
    """
    entry = _entry(
        session,
        workspace,
        body="sk-123456",
        structured={"a": "!!!", "resto": "7890ABCDEF"},
    )

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)

    _assert_nao_vaza(superficies, "sk-123456", "7890ABCDEF")
    # A folha inocente continua legível: a proteção é dirigida, não um apagão.
    assert "!!!" in superficies["blocks[].text"]


def test_e5_aud2_002_copia_do_mesmo_valor_em_folha_sem_rotulo(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """REPRODUÇÃO EXATA: `{"password":"hunter2", "copy":"hunter2"}` — `copy` não tem
    rótulo sensível, e a redação por ocorrência isolada a deixava passar."""
    entry = _entry(session, workspace, structured={"password": "hunter2", "copy": "hunter2"})

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)

    _assert_nao_vaza(superficies, "hunter2")


def test_e5_aud2_002_copia_do_mesmo_valor_no_corpo(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """REPRODUÇÃO EXATA: o mesmo valor de `password` copiado para o `body`, sem rótulo."""
    entry = _entry(session, workspace, body="hunter2", structured={"password": "hunter2"})

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)

    _assert_nao_vaza(superficies, "hunter2")


def test_e5_aud2_002_copia_do_mesmo_valor_no_titulo(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """REPRODUÇÃO EXATA: o valor de `password` copiado para o **título**, que se propaga
    para `origin.title` e `entries[].title` — a variante que a rodada 2 apontou como a
    que alcança os metadados do manifest."""
    entry = _entry(
        session, workspace, title="hunter2", body="body", structured={"password": "hunter2"}
    )

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)

    _assert_nao_vaza(superficies, "hunter2")
    assert superficies["blocks[].origin.title"] == REDACTED
    assert superficies["manifest.entries[].title"] == REDACTED


def test_e5_aud2_003a_chave_que_e_token_vira_marcador_fixo(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """REPRODUÇÃO EXATA: `{"sk-ant-…": "ordinary"}`.

    `_redact_leaf` redigia a linha e devolvia só a parte interpretada como valor; o
    caminho cru era reintroduzido inteiro na montagem final. O rótulo agora vira marcador
    fixo — **nunca** reconstrução parcial da chave.
    """
    chave = "sk-ant-" + "A" * 30
    entry = _entry(session, workspace, structured={chave: "ordinary"})

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)

    _assert_nao_vaza(superficies, chave)
    assert "<chave protegida>" in superficies["blocks[].text"]


def test_e5_aud2_003b_chave_com_separador_reservado(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """REPRODUÇÃO EXATA: chave `"password: hunter2"`.

    `partition(": ")` cortava na subsequência que o **próprio autor** forneceu,
    reintroduzindo a credencial no rótulo e corrompendo o valor emitido. O separador é
    reservado: um caminho que o contenha não é apresentável, e vira marcador fixo.
    """
    entry = _entry(session, workspace, structured={"password: hunter2": "ordinary"})

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)

    _assert_nao_vaza(superficies, "hunter2")
    assert "<chave protegida>" in superficies["blocks[].text"]


def test_e5_aud2_004a_lista_sob_chave_sensivel(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """REPRODUÇÃO EXATA: `{"password": ["hunter2"]}` linearizava para `password[0]:
    hunter2`, e o `[0]` destruía a adjacência de que o regex dependia."""
    entry = _entry(session, workspace, structured={"password": ["hunter2"]})

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)

    _assert_nao_vaza(superficies, "hunter2")


def test_e5_aud2_004b_objeto_aninhado_sob_chave_sensivel(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """REPRODUÇÃO EXATA: `{"password": {"current": "hunter2"}}` linearizava para
    `password.current: hunter2`, com o mesmo efeito."""
    entry = _entry(session, workspace, structured={"password": {"current": "hunter2"}})

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)

    _assert_nao_vaza(superficies, "hunter2")


# ================================================================ combinações exigidas


def test_subarvore_sensivel_em_profundidade_quatro(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """Chave sensível → dict → list → dict → valor. A marca da camada 1 desce por todos
    os níveis; nenhuma folha da subárvore escapa."""
    entry = _entry(
        session,
        workspace,
        structured={
            "password": {"historico": [{"anterior": "valor_secreto_antigo"}]},
            "publico": "valor_inocente_visivel",
        },
    )

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)

    _assert_nao_vaza(superficies, "valor_secreto_antigo")
    # A folha fora da subárvore sensível continua legível.
    assert "valor_inocente_visivel" in superficies["blocks[].text"]


def test_multiplos_segredos_diferentes_nao_se_mascaram(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """Cada segredo é redigido por conta própria. Era exatamente aqui que a contagem
    falhava: uma redação em outro lugar equilibrava o número e a fronteira ficava
    aberta."""
    entry = _entry(
        session,
        workspace,
        title=f"titulo com {CHAVE_ANTHROPIC}",
        body="sk-123456",
        structured={
            "a": "7890ABCDEF",
            "password": "hunter2",
            "token": "outro_segredo_longo",
            "aws": "AKIAIOSFODNN7EXAMPLE",
        },
    )

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)

    _assert_nao_vaza(
        superficies,
        CHAVE_ANTHROPIC,
        "sk-123456",
        "7890ABCDEF",
        "hunter2",
        "outro_segredo_longo",
        "AKIAIOSFODNN7EXAMPLE",
    )


def test_escalar_generico_sob_chave_sensivel_nao_propaga(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """`{"password": 42}` é redigido **localmente** pela camada 1, mas `42` não entra na
    propagação global: um `42` sem relação em outro campo continua visível.

    Propagar escalar genérico redigiria o documento por coincidência, não por segredo.
    """
    entry = _entry(
        session,
        workspace,
        body="a contagem final foi 42 itens",
        structured={"password": 42, "quantidade": 42, "ativo": True, "vazio": None},
    )

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)
    texto = superficies["blocks[].text"]

    assert "password: «redigido»" in texto
    assert "quantidade: 42" in texto
    assert "a contagem final foi 42 itens" in texto


def test_valor_sensivel_longo_propaga_valor_curto_nao(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """O limiar de propagação é por comprimento: `"hunter2"` (7) propaga, `"ok"` (2) não.

    Sem limiar, um `password` curto como `"ok"` redigiria todo `ok` do documento.
    """
    entry = _entry(
        session,
        workspace,
        body="resposta: ok, valor longo hunter2 aqui",
        structured={"password": "hunter2", "status": "ok"},
    )

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)
    texto = superficies["blocks[].text"]

    _assert_nao_vaza(superficies, "hunter2")
    assert "status: ok" in texto


def test_segredo_no_fim_do_corpo_consome_o_titulo_vizinho(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """**Este teste mudou de sinal na rodada 5** (`E5-AUD4-001`).

    Até a rodada 4 ele afirmava o contrário — que um segredo completo no fim do `body`
    **não** apagava o título vizinho — e o mecanismo que garantia isso era
    `_is_greedy_extension`: a travessia era descartada quando o reconhecimento já começava
    dentro do primeiro fragmento sozinho.

    A rodada 4 provou que esse mesmo descarte deixava a **cauda** de um segredo partido
    sair crua (`password: sk-123` | `456789ABC`), porque o sinal usado não distingue
    "extensão gulosa sobre vizinho legítimo" de "segredo genuinamente continuado". Sem
    heurística de confiança — que a V1 recusa — os dois casos são indistinguíveis, então a
    função saiu e a over-redaction passou a ser o comportamento aceito ([03] §4).

    O que continua valendo: o dano para onde o padrão para. `eee ` sobrevive porque o
    espaço não está no alfabeto que a chave consome.
    """
    entry = _entry(session, workspace, title="Combinante", body=f"eee {CHAVE_ANTHROPIC}")

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)

    _assert_nao_vaza(superficies, CHAVE_ANTHROPIC)
    assert superficies["blocks[].origin.title"] == REDACTED
    assert "eee " in superficies["blocks[].text"]


def test_conteudo_legitimo_atravessa_intacto(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """A prova negativa: sem segredo, nada é redigido. Um pipeline que redige tudo
    passaria em todos os testes acima e seria inútil."""
    entry = _entry(
        session,
        workspace,
        title="Decisão de banco",
        body="Usamos SQLite local, sem servidor.",
        structured={"reason": "simplicidade operacional", "tokenizer": "gpt-4"},
    )

    superficies = _superficies(session, workspace, base_commit, temp_settings, entry)
    texto = superficies["blocks[].text"]

    assert REDACTED not in texto
    assert "Decisão de banco" in texto
    assert "Usamos SQLite local, sem servidor." in texto
    assert "reason: simplicidade operacional" in texto
    assert "tokenizer: gpt-4" in texto


def test_execucao_repetida_produz_bytes_identicos(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """Determinismo: a mesma entrada, renderizada duas vezes, dá o mesmo texto e o mesmo
    título — inclusive com todas as três camadas ativas."""
    entry = _entry(
        session,
        workspace,
        title=f"chave {CHAVE_ANTHROPIC}",
        body="sk-123456",
        structured={"a": "7890ABCDEF", "password": "hunter2", "publico": "visivel"},
    )

    primeiro = render_block_text(entry)
    segundo = render_block_text(entry)

    assert primeiro == segundo
    assert primeiro.text.encode("utf-8") == segundo.text.encode("utf-8")


def test_fail_closed_em_profundidade_patologica(session: Session, workspace: DevWorkspace) -> None:
    """Estrutura profunda demais não vira melhor esforço: o bloco inteiro é redigido."""
    profundo: dict[str, Any] = {"folha": "valor_no_fundo_do_poco"}
    for _ in range(40):
        profundo = {"nivel": profundo}

    entry = _entry(session, workspace, structured=profundo)
    rendered = render_block_text(entry)

    assert "valor_no_fundo_do_poco" not in rendered.text
    assert rendered.title == REDACTED
    assert rendered.text.count(REDACTED) >= 2


def test_transformacoes_sem_redact_continuam_recusadas(
    session: Session, workspace: DevWorkspace
) -> None:
    """A redação não é opcional — o redesenho não abriu essa porta."""
    entry = _entry(session, workspace)

    with pytest.raises(ValueError, match="redação de segredo não é opcional"):
        render_block_text(entry, [Transformation.NORMALIZE])
