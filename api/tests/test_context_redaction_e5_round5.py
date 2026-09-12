"""E5 rodada 5 — `E5-AUD4-001`, `E5-AUD4-002`, `E5-AUD4-003`.

Os três findings da [rodada 4](../../docs/audits/e5-round-4.md) são a mesma pergunta feita
de três ângulos: *quem decide o quê, entre `recognition_span` e `replacement_span`?*

* **`E5-AUD4-001` (Alta/P1)** — `_is_greedy_extension` descartava toda travessia cujo
  reconhecimento já começava dentro do primeiro fragmento. `password: sk-123` **sozinho**
  já casa `assigned_secret`, então a travessia para `456789ABC` era descartada e a cauda
  saía crua. A função foi **removida**, sem substituta.
* **`E5-AUD4-002` (Média/P2)** — detectada a travessia, os dois fragmentos saíam
  **inteiros**, apagando junto o rótulo (`password: `, `https://`) que o próprio motor
  identificou como contexto preservado. A emissão passou a ser a **interseção** de
  `replacement_span` com cada fragmento.
* **`E5-AUD4-003` (Alta/P1)** — `recognition_span` vinha de `match.start()/end()`, que não
  cobrem o texto lido por lookaround. Para `url_credentials`, `://` e `@` ficavam de fora,
  a travessia não aparecia, e a credencial de URL saía inteira. O contexto de lookaround
  passou a ser declarado **no motor único** e a entrar em `recognition_span`.

As duas últimas seções são os gates: o ensaio diferencial que prova que `redact()` não
mudou, e a varredura de bytes sobre o artifact físico contra os fixtures das **quatro**
rodadas.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.config import AppSettings
from app.context_engine import create_entry, freeze_manifest, render_block_text, select_context
from app.context_engine.content_hash import normalize_text
from app.context_engine.rendering import CROSS_FRAGMENT_REDACTION
from app.db.enums import ContextDomain
from app.db.models import ContextRegistryEntry, DevWorkspace
from app.safety.redaction import REDACTED, SecretSpan, detect_secret_spans, redact
from tests import context_helpers
from tests.test_context_redaction_e5_round4 import CENARIOS as CENARIOS_RODADAS_1_A_4
from tests.test_context_redaction_e5_round4 import _Cenario


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

    Os bytes vêm de `read_bytes()` no caminho real — nunca de `RenderedContext.payload`
    em memória, que é o atalho que a rodada 2 apontou como prova fraca.
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


def _span_de(texto: str, nome: str) -> SecretSpan:
    """O span que `nome` produziu em `texto`. Falha se o padrão não reconheceu nada."""
    encontrados = [span for span in detect_secret_spans(texto) if span.pattern_name == nome]
    assert encontrados, f"{nome!r} não reconheceu {texto!r}"
    return encontrados[0]


# ======================================================== E5-AUD4-001 — extensão gulosa


def test_e5_aud4_001_cauda_do_segredo_nao_sobrevive(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """REPRODUÇÃO EXATA (Codex): `title="password: sk-123"`, `body="456789ABC"`.

    O que fazia isto vazar: `password: sk-123` casa `assigned_secret` **sozinho** —
    `sk-123` tem exatamente os seis caracteres do piso do padrão. A travessia para a cauda
    era reconhecida e depois **descartada** por `_is_greedy_extension`, porque a posição
    inicial do reconhecimento (0) já era início de detecção no fragmento isolado. A
    propagação também não ajudava: ela conhecia `sk-123`, não `456789ABC`.
    """
    cauda = "456789ABC"
    entry = _entry(session, workspace, title="password: sk-123", body=cauda)

    # Pré-condição que define o caso: o primeiro fragmento já é reconhecível sozinho.
    # Sem isto o teste não exercita a heurística removida.
    assert detect_secret_spans("password: sk-123")
    assert not detect_secret_spans(cauda)

    bruto, payload, manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    assert cauda.encode("utf-8") not in bruto, "a cauda do segredo sobreviveu nos bytes"
    assert "sk-123" not in bloco["text"]
    assert cauda not in json.dumps(manifest.entries, ensure_ascii=False)
    assert CROSS_FRAGMENT_REDACTION in bloco["transformations"]


def test_e5_aud4_001_over_redaction_agora_acontece(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """O preço da remoção, afirmado como **comportamento esperado**.

    Este teste era, até a rodada 4, a prova de que a over-redaction **não** acontecia
    (`test_segredo_no_fim_do_corpo_nao_apaga_o_titulo`, rodada 3). Ele mudou de sinal: um
    segredo completo no fim de um fragmento, colado a um vizinho alfanumérico, faz o
    padrão continuar consumindo — e o que ele consome **é** redigido, mesmo sendo conteúdo
    legítimo não relacionado.

    É a prioridade declarada em [03] §4: over-redaction é aceitável, vazamento parcial não
    é. O que a interseção garante é que o dano pare onde `replacement_span` para — só os
    caracteres efetivamente consumidos, não o fragmento inteiro por estar ao lado.
    """
    chave = "sk-ant-" + "A" * 40
    entry = _entry(session, workspace, title="Combinante", body=f"eee {chave}")

    bruto, payload, _ = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    assert chave.encode("utf-8") not in bruto
    # O título é consumido pela gulodice do alfabeto — aceito, e agora afirmado.
    assert bloco["origin"]["title"] == REDACTED
    # O dano para onde o padrão para: `eee ` tem espaço, que o alfabeto não consome.
    assert "eee " in bloco["text"]


def test_e5_aud4_001_a_heuristica_nao_existe_mais() -> None:
    """A remoção é verificada no módulo, não só no comportamento.

    Um teste de comportamento passaria se a função voltasse com outro nome e outra
    condição — e o finding era justamente "não reintroduzir heurística de confiança".
    """
    from app.context_engine import rendering

    assert not hasattr(rendering, "_is_greedy_extension")
    fonte = Path(rendering.__file__).read_text(encoding="utf-8")
    assert "own_starts" not in fonte, "o sinal que alimentava a heurística também saiu"


# =============================================== E5-AUD4-002 — emissão por interseção


def test_e5_aud4_002_rotulo_preservado_no_split(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """REPRODUÇÃO EXATA (Codex): `title="password: sk-12"`, `body="3456789ABC"`.

    Nenhum fragmento casa sozinho (`sk-12` tem cinco caracteres, abaixo do piso). Juntos
    casam `assigned_secret`, e o intervalo de substituição exclui os dez caracteres de
    `password: `. Mesmo assim o texto persistido apagava os dois fragmentos inteiros — o
    rótulo sumia sem que um só caractere dele fosse segredo.
    """
    entry = _entry(session, workspace, title="password: sk-12", body="3456789ABC")

    # Pré-condição: o split é genuíno, nenhuma metade é reconhecível sozinha.
    assert not detect_secret_spans("password: sk-12")
    assert not detect_secret_spans("3456789ABC")
    assert detect_secret_spans("password: sk-123456789ABC")

    bruto, payload, _ = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    # O rótulo sobrevive — é o que o finding pediu.
    assert bloco["origin"]["title"] == f"password: {REDACTED}"
    assert b"password: " in bruto
    # E as duas metades do segredo somem.
    for metade in ("sk-12", "3456789ABC", "sk-123456789ABC"):
        assert metade.encode("utf-8") not in bruto, f"{metade!r} sobreviveu"


def test_e5_aud4_002_split_invertido_nao_apaga_corpo_nem_rotulo_de_folha(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """REPRODUÇÃO EXATA (Codex): `body="password: sk-123"`, `structured={"resto": "456789ABC"}`.

    Nesta disposição a rodada 4 relatou o corpo inteiro **e** o rótulo da folha apagados,
    o que mascarava o vazamento visível na reprodução mínima. Com a interseção, o corpo
    perde só o valor.
    """
    entry = _entry(
        session,
        workspace,
        body="password: sk-123",
        structured={"resto": "456789ABC"},
    )

    bruto, payload, _ = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    assert b"456789ABC" not in bruto
    assert "sk-123" not in bloco["text"]
    # O corpo não foi apagado inteiro: o rótulo `password: ` atravessou intacto.
    assert f"password: {REDACTED}" in bloco["text"]


def test_e5_aud4_002_travessia_nao_marca_fragmento_inteiro(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """A forma geral: texto legítimo **fora** da interseção sobrevive dos dois lados."""
    entry = _entry(session, workspace, title="Bearer", body=" ABCDEFGHIJKLMNOP fim do corpo")

    bruto, payload, _ = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    assert b"ABCDEFGHIJKLMNOP" not in bruto
    assert bloco["origin"]["title"] == "Bearer", "o contexto que prova a detecção fica"
    assert "fim do corpo" in bloco["text"], "o texto depois do segredo fica"


# ============================================= E5-AUD4-003 — lookaround no motor único


def test_e5_aud4_003_url_credentials_lookbehind_em_outro_fragmento(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """REPRODUÇÃO EXATA (Codex): `title="https://"`, `body="reader:passcode@host.test"`.

    `(?<=://)` e `(?=@)` não entram em `match.start()/end()`. O `recognition_span` era
    `[8,23)`, idêntico ao de substituição e inteiramente dentro do corpo; a fronteira em 8
    não era cruzada e a detecção morria.
    """
    credencial = "reader:passcode"
    entry = _entry(session, workspace, title="https://", body=f"{credencial}@host.test")

    # Pré-condição: nenhum fragmento isolado é reconhecível.
    assert not detect_secret_spans("https://")
    assert not detect_secret_spans(f"{credencial}@host.test")
    assert detect_secret_spans(f"https://{credencial}@host.test")

    bruto, payload, manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    assert credencial.encode("utf-8") not in bruto
    assert credencial not in json.dumps(manifest.entries, ensure_ascii=False)
    # O contexto que prova a detecção é preservado, como `redact()` sempre fez.
    assert bloco["origin"]["title"] == "https://"
    assert "@host.test" in bloco["text"]


def test_e5_aud4_003_url_credentials_lookahead_em_outro_fragmento(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """REPRODUÇÃO EXATA (Codex): `title="https://reader:passcode"`, `body="@host.test"`.

    O `@` exigido pelo lookahead está no segundo fragmento. Aqui a credencial vazava no
    **título**, e por ele em `origin.title` e `ContextManifest.entries[].title`.
    """
    credencial = "reader:passcode"
    entry = _entry(session, workspace, title=f"https://{credencial}", body="@host.test")

    assert not detect_secret_spans(f"https://{credencial}")
    assert not detect_secret_spans("@host.test")

    bruto, payload, manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)
    linha = next(item for item in manifest.entries if item["entry_id"] == entry.id)

    assert credencial.encode("utf-8") not in bruto
    assert credencial not in bloco["text"]
    assert credencial not in bloco["origin"]["title"]
    assert credencial not in str(linha["title"])
    assert bloco["origin"]["title"] == f"https://{REDACTED}"


def test_e5_aud4_003_recognition_span_cobre_as_duas_asercoes() -> None:
    """No motor, não no renderer: `recognition_span` inclui `://` e `@`."""
    texto = "https://reader:passcode@host.test"
    span = _span_de(texto, "url_credentials")

    assert texto[span.recognition_span.start : span.recognition_span.end] == "://reader:passcode@"
    assert texto[span.replacement_span.start : span.replacement_span.end] == "reader:passcode"
    # E `redact()` continua preservando as duas âncoras.
    assert redact(texto) == f"https://{REDACTED}@host.test"


@pytest.mark.parametrize(
    ("nome", "corte"),
    [
        ("antes do esquema", 5),
        ("entre usuario e senha", 14),
        ("depois da senha", 23),
    ],
)
def test_e5_aud4_003_url_partida_nos_tres_pontos(
    nome: str,
    corte: int,
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> None:
    """Os três cortes possíveis de `https://reader:passcode@host.test`.

    O corte em 5 (`https` | `://reader:…`) é o único em que a fronteira **não** cai dentro
    do reconhecimento: o segundo fragmento contém `://…@` inteiro e a leitura isolada já o
    pega. Os outros dois dependem da travessia.
    """
    texto = "https://reader:passcode@host.test"
    assert texto[:8] == "https://" and texto[23] == "@", "as coordenadas do fixture"

    esquerda, direita = texto[:corte], texto[corte:]
    assert normalize_text(esquerda) == esquerda and normalize_text(direita) == direita

    entry = _entry(session, workspace, title=esquerda, body=direita)
    bruto, payload, _ = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    assert b"reader:passcode" not in bruto, f"{nome}: credencial inteira sobreviveu"
    assert "reader" not in bloco["text"] and "passcode" not in bloco["text"]


# ================================================ regressão: todo padrão do catálogo


@dataclass(frozen=True)
class _Padrao:
    """Um padrão do catálogo com o corte que põe a fronteira dentro do reconhecimento.

    `substituicao_atravessa` é o campo que importa: quando é `False`, a fronteira cai
    **fora** do intervalo de substituição, e só `recognition_span` consegue ver a
    travessia. São exatamente os padrões que `E5-AUD3-002` e `E5-AUD4-003` quebraram.
    """

    nome: str
    texto: str
    reconhecimento: str
    substituicao: str
    corte: int
    substituicao_atravessa: bool


_PEM = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\n-----END PRIVATE KEY-----"

#: Um caso por padrão de `safety.redaction._PATTERNS`, na mesma ordem do catálogo.
PADROES: tuple[_Padrao, ...] = (
    _Padrao(
        nome="pem_block",
        texto=_PEM,
        reconhecimento=_PEM,
        substituicao=_PEM,
        corte=33,
        substituicao_atravessa=True,
    ),
    _Padrao(
        nome="url_credentials",
        texto="https://reader:passcode@host.test",
        reconhecimento="://reader:passcode@",
        substituicao="reader:passcode",
        corte=8,
        substituicao_atravessa=False,
    ),
    _Padrao(
        nome="bearer",
        texto="Bearer ABCDEFGHIJKLMNOP",
        reconhecimento="Bearer ABCDEFGHIJKLMNOP",
        substituicao="ABCDEFGHIJKLMNOP",
        corte=6,
        substituicao_atravessa=False,
    ),
    _Padrao(
        nome="anthropic_key",
        texto="sk-ant-A1B2C3D4E5F6",
        reconhecimento="sk-ant-A1B2C3D4E5F6",
        substituicao="sk-ant-A1B2C3D4E5F6",
        corte=10,
        substituicao_atravessa=True,
    ),
    _Padrao(
        nome="openai_key",
        texto="sk-A1B2C3D4E5F6G7H8",
        reconhecimento="sk-A1B2C3D4E5F6G7H8",
        substituicao="sk-A1B2C3D4E5F6G7H8",
        corte=10,
        substituicao_atravessa=True,
    ),
    _Padrao(
        nome="github_token",
        texto="ghp_A1B2C3D4E5F6G7H8",
        reconhecimento="ghp_A1B2C3D4E5F6G7H8",
        substituicao="ghp_A1B2C3D4E5F6G7H8",
        corte=10,
        substituicao_atravessa=True,
    ),
    _Padrao(
        nome="aws_access_key",
        texto="AKIAIOSFODNN7EXAMPLE",
        reconhecimento="AKIAIOSFODNN7EXAMPLE",
        substituicao="AKIAIOSFODNN7EXAMPLE",
        corte=10,
        substituicao_atravessa=True,
    ),
    _Padrao(
        nome="assigned_secret",
        texto="password: hunter2",
        reconhecimento="password: hunter2",
        substituicao="hunter2",
        corte=9,
        substituicao_atravessa=False,
    ),
)


def test_a_tabela_cobre_o_catalogo_inteiro() -> None:
    """Se um padrão for acrescentado a `_PATTERNS` sem entrar aqui, isto falha.

    Sem esta trava, "revisei todos os padrões" vale só para o dia em que foi escrito.
    """
    from app.safety.redaction import _PATTERNS

    assert tuple(spec.name for spec in _PATTERNS) == tuple(item.nome for item in PADROES)


def test_todo_lookaround_positivo_do_catalogo_e_declarado() -> None:
    """A trava que impede um `E5-AUD4-003` novo.

    O motor já valida isto no import; aqui a suíte afirma a mesma regra, para que a
    falha apareça como teste vermelho e não só como `ImportError`.
    """
    from app.safety.redaction import _PATTERNS

    for spec in _PATTERNS:
        fonte = spec.pattern.pattern
        assert ("(?<=" in fonte) == (spec.lookbehind is not None), spec.name
        assert ("(?=" in fonte) == (spec.lookahead is not None), spec.name


@pytest.mark.parametrize("padrao", PADROES, ids=lambda item: item.nome)
def test_intervalos_de_cada_padrao(padrao: _Padrao) -> None:
    """`recognition_span` cobre o que a detecção leu; `replacement_span`, o que ela apaga."""
    span = _span_de(padrao.texto, padrao.nome)
    reconhecimento, substituicao = span.recognition_span, span.replacement_span

    assert padrao.texto[reconhecimento.start : reconhecimento.end] == padrao.reconhecimento
    assert padrao.texto[substituicao.start : substituicao.end] == padrao.substituicao

    # Invariante estrutural: substituição sempre dentro do reconhecimento.
    assert reconhecimento.start <= substituicao.start
    assert substituicao.end <= reconhecimento.end

    # O corte escolhido põe a fronteira dentro do reconhecimento...
    assert reconhecimento.crosses(padrao.corte)
    # ...e a tabela declara se a substituição também a cruza. Onde não cruza, só
    # `recognition_span` enxerga a travessia — é a classe dos dois findings.
    assert substituicao.crosses(padrao.corte) is padrao.substituicao_atravessa


@pytest.mark.parametrize("padrao", PADROES, ids=lambda item: item.nome)
def test_cada_padrao_partido_na_fronteira_nao_vaza(
    padrao: _Padrao,
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> None:
    """Cada padrão do catálogo, partido exatamente no ponto do seu contexto de lookaround.

    O corpo fica com a primeira metade e uma folha de `structured` com a segunda — a
    disposição em que a ordem canônica separa as duas partes.
    """
    esquerda, direita = padrao.texto[: padrao.corte], padrao.texto[padrao.corte :]
    # As metades têm de sobreviver à normalização, senão o teste exercita outro texto.
    assert normalize_text(esquerda) == esquerda
    assert normalize_text(direita) == direita

    entry = _entry(session, workspace, body=esquerda, structured={"resto": direita})
    bruto, payload, manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    # Cada pedaço da substituição que caiu de um lado tem de sumir daquele lado.
    span = _span_de(padrao.texto, padrao.nome)
    pedacos = (
        padrao.texto[span.replacement_span.start : min(span.replacement_span.end, padrao.corte)],
        padrao.texto[max(span.replacement_span.start, padrao.corte) : span.replacement_span.end],
    )
    for pedaco in pedacos:
        if pedaco:
            assert pedaco.encode("utf-8") not in bruto, f"{padrao.nome}: {pedaco!r} sobreviveu"
    assert padrao.substituicao not in json.dumps(manifest.entries, ensure_ascii=False)
    assert CROSS_FRAGMENT_REDACTION in bloco["transformations"]


@pytest.mark.parametrize(
    "padrao",
    [item for item in PADROES if not item.substituicao_atravessa],
    ids=lambda item: item.nome,
)
def test_contexto_preservado_sobrevive_ao_split(
    padrao: _Padrao,
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> None:
    """Nos padrões de contexto preservado, o contexto continua legível depois do split.

    É `E5-AUD4-002` por padrão, e não só nas duas reproduções: `://`, `Bearer ` e
    `password: ` provam a detecção e não são o segredo.

    O contexto é verificado **por pedaço**, não inteiro: o corte pode cair dentro dele
    (em `bearer`, `Bearer ` fica com o espaço do outro lado da fronteira), e aí a string
    completa não existe contígua em lugar nenhum do artifact — cada metade tem de
    sobreviver no fragmento em que caiu.
    """
    span = _span_de(padrao.texto, padrao.nome)
    inicio, fim = span.recognition_span.start, span.replacement_span.start
    assert inicio < fim, "a tabela diz que este padrão preserva contexto"

    esquerda, direita = padrao.texto[: padrao.corte], padrao.texto[padrao.corte :]
    _entry(session, workspace, body=esquerda, structured={"resto": direita})
    bruto, _payload, _manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)

    pedacos = (
        padrao.texto[inicio : min(fim, padrao.corte)],
        padrao.texto[max(inicio, padrao.corte) : fim],
    )
    for pedaco in pedacos:
        if pedaco:
            assert pedaco.encode("utf-8") in bruto, (
                f"{padrao.nome}: o contexto {pedaco!r} foi apagado junto com o valor"
            )


def test_atribuicao_simples_sem_travessia_preserva_o_rotulo(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """`password: valor` num campo só — o comportamento histórico de `redact()` intacto.

    A regressão que importa aqui é a inversa das outras: nada nesta rodada pode ter
    mudado o caso simples, que não envolve fronteira nenhuma.
    """
    entry = _entry(session, workspace, body="password: hunter2 e mais texto")

    bruto, payload, _ = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    assert b"hunter2" not in bruto
    assert f"password: {REDACTED} e mais texto" in bloco["text"]
    assert redact("password: hunter2 e mais texto") == f"password: {REDACTED} e mais texto"


# ======================================== GATE 1 — `redact()` byte a byte inalterado


#: A cascata histórica, reescrita aqui **com os fontes dos padrões literais**, não
#: importados. Assim o ensaio também trava o catálogo: mudar um regex em
#: `safety/redaction.py` sem mudar esta lista faz o diferencial acusar.
_CASCATA_HISTORICA: tuple[tuple[str, str, int], ...] = (
    (r"-----BEGIN[A-Z ]*PRIVATE KEY-----.*?-----END[A-Z ]*PRIVATE KEY-----", "", re.DOTALL),
    (r"(?<=://)[^/\s:@]+:[^/\s:@]+(?=@)", "", 0),
    (r"(?i)\b(bearer\s+)[A-Za-z0-9._\-+/=]{8,}", r"\1", 0),
    (r"\bsk-ant-[A-Za-z0-9._\-]{8,}", "", 0),
    (r"\bsk-[A-Za-z0-9]{16,}", "", 0),
    (r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})", "", 0),
    (r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "", 0),
    (
        r"(?i)\b((?:api[_-]?key|secret|token|password|passwd|authorization)"
        r"\s*[:=]\s*)(?:\"|')?([^\s\"',;]{6,})",
        r"\1",
        0,
    ),
)


def _redact_historico(texto: str) -> str:
    """`redact()` como era antes de existir `detect_secret_spans`: uma cascata de
    `re.sub`, cada padrão enxergando o resultado do anterior."""
    for fonte, prefixo, flags in _CASCATA_HISTORICA:
        texto = re.compile(fonte, flags).sub(prefixo + REDACTED, texto)
    return texto


#: Peças combinadas para gerar o corpus. Misturam segredo completo, metade de segredo,
#: âncora de lookaround isolada, rótulo isolado e texto legítimo — as combinações em que
#: a cascata e o mapa de posições podem divergir.
_PECAS: tuple[str, ...] = (
    "",
    "https://",
    "://",
    "@",
    "@host.test",
    "reader:passcode",
    "reader:passcode@host.test",
    "Bearer",
    "Bearer ",
    " ABCDEFGHIJKLMNOP",
    "ABCDEFGHIJKLMNOP",
    "password:",
    "password: ",
    "password: sk-123",
    "456789ABC",
    "hunter2",
    "sk-ant-" + "A" * 40,
    "sk-ant-",
    "A" * 40,
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_" + "B" * 20,
    "github_pat_" + "C" * 25,
    "-----BEGIN PRIVATE KEY-----",
    "MIIEvQIBADANBg",
    "-----END PRIVATE KEY-----",
    "texto legitimo qualquer",
    "tokenizer",
    "42",
)


def test_gate_1_redact_identico_a_cascata_historica() -> None:
    """GATE 1: nenhum caso **sem fragmentação** mudou de bytes.

    `recognition_span` só ampliou. Quem substitui texto lê `replacement_span`, e este
    ensaio prova que a ampliação não vazou para lá: para todo caso do corpus, `redact()`
    devolve exatamente o que a cascata histórica devolveria.
    """
    casos = set(_PECAS)
    casos |= {a + b for a, b in itertools.product(_PECAS, repeat=2)}
    casos |= {a + b + c for a, b, c in itertools.product(_PECAS[:18], repeat=3)}

    divergencias = sorted(caso for caso in casos if redact(caso) != _redact_historico(caso))

    assert not divergencias, (
        f"{len(divergencias)} de {len(casos)} casos divergiram; "
        f"primeiro: {divergencias[0]!r} -> {redact(divergencias[0])!r} "
        f"!= {_redact_historico(divergencias[0])!r}"
    )
    assert len(casos) > 5000, "o corpus encolheu — o ensaio perderia força sem avisar"


# ============================ GATE 2 — varredura de bytes das quatro rodadas


#: Os cenários desta rodada, somados aos 22 das rodadas 1–4. Cada um preserva a condição
#: que torna seu valor segredo — ver `_Cenario` em `test_context_redaction_e5_round4.py`.
CENARIOS_RODADA_5: tuple[_Cenario, ...] = (
    _Cenario(
        "url credentials inteira",
        "reader:passcode",
        body="https://reader:passcode@host.test",
    ),
    _Cenario(
        "url credentials lookbehind partido",
        "reader:passcode",
        title="https://",
        body="reader:passcode@host.test",
    ),
    _Cenario(
        "url credentials lookahead partido",
        "reader:passcode",
        title="https://reader:passcode",
        body="@host.test",
    ),
    _Cenario(
        "url credentials partida no meio",
        "reader:passcode",
        title="https://reader",
        body=":passcode@host.test",
    ),
    _Cenario(
        "cauda de extensao gulosa",
        "456789ABC",
        title="password: sk-123",
        body="456789ABC",
    ),
    _Cenario(
        "split abaixo do piso nos dois lados",
        "sk-123456789ABC",
        title="password: sk-12",
        body="3456789ABC",
    ),
    _Cenario(
        "split com rotulo no corpo e cauda em folha",
        "456789ABC",
        body="password: sk-123",
        structured={"resto": "456789ABC"},
    ),
)

CENARIOS_TODAS_AS_RODADAS: tuple[_Cenario, ...] = (
    *CENARIOS_RODADAS_1_A_4,
    *CENARIOS_RODADA_5,
)


@pytest.mark.parametrize("cenario", CENARIOS_RODADA_5, ids=lambda item: item.nome)
def test_gate_2_varredura_por_cenario_novo(
    cenario: _Cenario,
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> None:
    """Cada cenário novo **sozinho** no artifact — a over-redaction de um não pode
    esconder o vazamento de outro atrás de `«redigido»` alheio."""
    _entry(
        session,
        workspace,
        title=cenario.title,
        body=cenario.body,
        structured=cenario.structured,
    )

    bruto, payload, manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)

    assert cenario.segredo.encode("utf-8") not in bruto, (
        f"{cenario.nome}: {cenario.segredo!r} sobreviveu nos bytes"
    )
    assert cenario.segredo not in json.dumps(manifest.entries, ensure_ascii=False)
    assert payload["blocks"] and REDACTED in payload["blocks"][0]["text"]


def test_gate_2_varredura_das_quatro_rodadas_no_mesmo_artifact(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """GATE 2: **todos** os fixtures sintéticos das quatro rodadas, um arquivo físico, uma
    varredura binária.

    O `sha256` dos bytes varridos é conferido contra o `rendered_context_hash` registrado:
    a varredura foi sobre o arquivo que o manifest de fato aponta, não sobre uma cópia em
    memória.
    """
    for indice, cenario in enumerate(CENARIOS_TODAS_AS_RODADAS):
        _entry(
            session,
            workspace,
            title=f"{indice:02d} {cenario.title}",
            body=cenario.body,
            structured=cenario.structured,
        )

    bruto, payload, manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)

    assert len(payload["blocks"]) == len(CENARIOS_TODAS_AS_RODADAS)
    assert len(CENARIOS_TODAS_AS_RODADAS) >= 29, "a tabela encolheu"

    sobreviventes = sorted(
        {
            item.segredo
            for item in CENARIOS_TODAS_AS_RODADAS
            if item.segredo.encode("utf-8") in bruto
        }
    )
    assert not sobreviventes, f"segredo sobreviveu nos bytes: {sobreviventes}"

    metadados = json.dumps(manifest.entries, ensure_ascii=False)
    nos_metadados = sorted(
        {item.segredo for item in CENARIOS_TODAS_AS_RODADAS if item.segredo in metadados}
    )
    assert not nos_metadados, f"segredo sobreviveu em ContextManifest.entries: {nos_metadados}"

    assert hashlib.sha256(bruto).hexdigest() == manifest.rendered_context_hash


def test_render_block_text_continua_deterministico(
    session: Session, workspace: DevWorkspace
) -> None:
    """Duas chamadas, o mesmo `RenderedBlock` — inclusive `transformations`.

    A interseção introduziu ordenação nova (spans locais vindos de permutações de pares).
    Se ela dependesse da ordem de iteração de um `set`, isto acusaria.
    """
    entry = _entry(
        session,
        workspace,
        title="https://reader:passcode",
        body="@host.test e password: sk-12",
        structured={"resto": "3456789ABC", "password": "hunter2"},
    )

    assert render_block_text(entry) == render_block_text(entry)
