"""E5 rodada 4 — `E5-AUD3-002`, `E5-AUD3-003` e a varredura final de bytes.

Fecha os dois defeitos que a [rodada 3](../../docs/audits/e5-round-3.md) encontrou:

* **`E5-AUD3-002` (Alta/P1)** — um prefixo preservado (`Bearer `, `password: `) num
  fragmento faz o motor reconhecer o segredo, mas o `replacement_span` cobria só o valor,
  então a análise de fronteira não via travessia nenhuma e **descartava a detecção**. O
  valor completo ficava nos bytes do artifact. A correção separa `recognition_span` de
  `replacement_span`: a travessia é decidida pelo primeiro, a substituição pelo segundo.
* **`E5-AUD3-003` (Média/P2)** — `_propagation_values` consultava `propagatable` só ao
  acrescentar o fragmento inteiro; o laço dos recortes de span entrava sem checar, e um
  número sob chave sensível apagava toda contagem pública igual.

`E5-AUD3-001` **não** é corrigido aqui: é trade-off aceito e documentado em
[03](../../docs/architecture/03-context-architecture.md) §4 — a V1 prefere falso positivo
de redação a falso negativo de vazamento.

A última seção é a **varredura de bytes**: todo segredo sintético usado em qualquer das
três rodadas anteriores entra numa única entrada, e o arquivo físico do artifact é aberto
em modo binário e verificado contra todos eles de uma vez.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
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
from app.context_engine.rendering import CROSS_FRAGMENT_REDACTION
from app.db.enums import ContextDomain
from app.db.models import ContextRegistryEntry, DevWorkspace
from app.safety.redaction import REDACTED, detect_secret_spans
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

    Os bytes vêm de `read_bytes()` no caminho real — nunca de `RenderedContext.payload`
    em memória, que é justamente o atalho que a rodada 2 apontou como prova fraca.
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


# ===================================================================== E5-AUD3-002


def test_e5_aud3_002_prefixo_em_outro_fragmento(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """REPRODUÇÃO EXATA (Codex): `title="Bearer"`, `body=" ABCDEFGHIJKLMNOP"`.

    Nenhum fragmento isolado casa. A leitura combinada `Bearer ABCDEFGHIJKLMNOP` é
    reconhecida, mas o `replacement_span` fica inteiramente dentro do `body` — a fronteira
    entre os fragmentos parecia intocada, e a detecção era descartada. Com
    `recognition_span` (que inclui a palavra `Bearer`, no outro fragmento), a travessia
    aparece.
    """
    credencial = "ABCDEFGHIJKLMNOP"
    entry = _entry(session, workspace, title="Bearer", body=f" {credencial}")

    # Pré-condição: nenhum fragmento isolado é reconhecível — o defeito não é trivial.
    assert not detect_secret_spans("Bearer")
    assert not detect_secret_spans(f" {credencial}")
    # E a leitura combinada É reconhecível — o teste exercita o padrão certo.
    assert detect_secret_spans(f"Bearer {credencial}")

    bruto, payload, manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)
    linha = next(item for item in manifest.entries if item["entry_id"] == entry.id)

    assert credencial.encode("utf-8") not in bruto
    assert credencial not in bloco["text"]
    assert credencial not in bloco["origin"]["title"]
    assert credencial not in str(linha["title"])


def test_e5_aud3_002_prefixo_assigned_secret_entre_fragmentos(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """A mesma classe pelo outro padrão de prefixo preservado: `password: ` num campo e o
    valor noutro."""
    credencial = "valorSuperSecreto123"
    entry = _entry(session, workspace, title="password:", body=f" {credencial}")

    assert not detect_secret_spans("password:")
    assert not detect_secret_spans(f" {credencial}")
    assert detect_secret_spans(f"password: {credencial}")

    bruto, payload, _manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    assert credencial.encode("utf-8") not in bruto
    assert credencial not in bloco["text"]


def test_redact_preserva_o_prefixo_como_sempre() -> None:
    """A correção **não** pode mudar o texto de `redact()`: `Bearer `/`password: ` ficam.

    Se a substituição passasse a usar `recognition_span`, o rótulo sumiria junto com o
    valor e todo log/resposta JSON do backend mudaria de forma silenciosa.
    """
    from app.safety.redaction import redact

    assert redact("Bearer ABCDEFGHIJKLMNOP") == f"Bearer {REDACTED}"
    assert redact("password: hunter2000") == f"password: {REDACTED}"


# ===================================================================== E5-AUD3-003


def test_e5_aud3_003_escalar_generico_nao_propaga_por_span_local(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """REPRODUÇÃO EXATA (Codex): `body="Contagem publica: 123456 itens."`,
    `structured={"password": 123456, "quantidade": 123456}`.

    O número sob `password` é redigido **localmente** pela camada estrutural — isso é
    correto e continua. O que era bug: o recorte do span da linha `password: 123456`
    entrava no conjunto de propagação global sem consultar `propagatable`, e apagava todo
    `123456` público do documento.
    """
    entry = _entry(
        session,
        workspace,
        body="Contagem publica: 123456 itens.",
        structured={"password": 123456, "quantidade": 123456},
    )

    _bruto, payload, _manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)
    texto = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)[
        "text"
    ]

    # Redigido localmente sob a chave sensível…
    assert f"password: {REDACTED}" in texto
    # …mas as duas ocorrências públicas permanecem legíveis.
    assert "Contagem publica: 123456 itens." in texto
    assert "quantidade: 123456" in texto


def test_escalar_generico_longo_sob_chave_sensivel_nao_propaga(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """Mesmo com o escalar bem acima do limiar de propagação, ele não propaga.

    O gate é o **tipo**, não o comprimento: `propagatable=False` vale por ser número, e
    isso independe de ter 6 ou 20 dígitos.
    """
    entry = _entry(
        session,
        workspace,
        body="o identificador publico e 9876543210987654321 no relatorio",
        structured={"api_key": 9876543210987654321, "referencia": 9876543210987654321},
    )

    _bruto, payload, _manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)
    texto = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)[
        "text"
    ]

    assert f"api_key: {REDACTED}" in texto
    assert "o identificador publico e 9876543210987654321 no relatorio" in texto
    assert "referencia: 9876543210987654321" in texto


def test_string_sensivel_continua_propagando(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """Prova negativa de `E5-AUD3-003`: o gate por tipo **não** desligou a propagação de
    string, que é o que fecha `E5-AUD2-002`."""
    entry = _entry(
        session,
        workspace,
        body="a copia esta aqui: hunter2000",
        structured={"password": "hunter2000", "copia": "hunter2000"},
    )

    bruto, payload, _manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)
    texto = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)[
        "text"
    ]

    assert b"hunter2000" not in bruto
    assert "hunter2000" not in texto


# ============================================================ marcador de explicabilidade


def test_marcador_presente_em_redacao_cross_fragment(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """`E5-AUD2-001a`: segredo partido entre `body` e uma folha. O bloco registra o
    marcador em `transformations`, e ele chega aos bytes do artifact."""
    entry = _entry(
        session,
        workspace,
        body="sk-123456",
        structured={"a": "7890ABCDEF", "password": "hunter2"},
    )

    _bruto, payload, _manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    assert CROSS_FRAGMENT_REDACTION in bloco["transformations"]
    # O marcador não expõe valor, posição nem fragmento — é só o fato.
    assert bloco["transformations"] == ["normalize", "redact", CROSS_FRAGMENT_REDACTION]


def test_marcador_ausente_sem_redacao_cross_fragment(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """Segredo autocontido num campo só: houve redação, mas **não** por travessia de
    fronteira. Sem o marcador — senão ele não distinguiria nada."""
    entry = _entry(session, workspace, structured={"password": "hunter2"})

    _bruto, payload, _manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    assert REDACTED in bloco["text"]  # houve redação…
    assert CROSS_FRAGMENT_REDACTION not in bloco["transformations"]  # …mas não cross-fragment


def test_marcador_ausente_em_bloco_sem_redacao_nenhuma(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    entry = _entry(
        session,
        workspace,
        title="Decisao de banco",
        body="Usamos SQLite local.",
        structured={"reason": "simplicidade"},
    )

    _bruto, payload, _manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)
    bloco = next(item for item in payload["blocks"] if item["origin"]["entry_id"] == entry.id)

    assert REDACTED not in bloco["text"]
    assert bloco["transformations"] == ["normalize", "redact"]


def test_marcador_ausente_no_fail_closed(session: Session, workspace: DevWorkspace) -> None:
    """Fail-closed redige tudo, mas **não** por travessia de fronteira: o marcador
    responderia a pergunta errada."""
    profundo: dict[str, Any] = {"folha": "valor_no_fundo"}
    for _ in range(40):
        profundo = {"nivel": profundo}

    rendered = render_block_text(_entry(session, workspace, structured=profundo))

    assert rendered.title == REDACTED
    assert CROSS_FRAGMENT_REDACTION not in rendered.transformations


def test_marcador_e_deterministico(session: Session, workspace: DevWorkspace) -> None:
    entry = _entry(session, workspace, body="sk-123456", structured={"a": "7890ABCDEF"})

    primeiro = render_block_text(entry)
    segundo = render_block_text(entry)

    assert primeiro == segundo
    assert CROSS_FRAGMENT_REDACTION in primeiro.transformations


def test_marcador_respeita_transformacoes_pedidas(
    session: Session, workspace: DevWorkspace
) -> None:
    """Sem `NORMALIZE`, a lista reflete isso — o marcador é acrescentado à lista real,
    não a uma lista fixa."""
    entry = _entry(session, workspace, body="sk-123456", structured={"a": "7890ABCDEF"})

    rendered = render_block_text(entry, [Transformation.REDACT])

    assert rendered.transformations == ("redact", CROSS_FRAGMENT_REDACTION)


# ================================================== varredura final de bytes (gate 2)


@dataclass(frozen=True)
class _Cenario:
    """Um fixture adversarial de alguma das três rodadas, com a **condição** que o torna
    segredo preservada.

    A condição importa: a maioria dos valores sintéticos das auditorias **não** é
    reconhecível por padrão nenhum isoladamente (`hunter2` é uma palavra comum;
    `ABCDEFGHIJKLMNOP` é uma sequência alfabética). Eles são segredo porque estão sob uma
    chave sensível, porque são cópia de um valor que está, ou porque completam um padrão
    quando concatenados a outro fragmento. Varrer esses valores **fora** da sua condição
    testaria a afirmação errada — o redator é baseado em padrão, não em lista de valores
    conhecidos.
    """

    nome: str
    segredo: str
    title: str = "titulo comum"
    body: str = "corpo comum"
    structured: dict[str, Any] | None = None


#: Um cenário por fixture adversarial das rodadas 1, 2 e 3, cada um mantendo a condição
#: sob a qual aquela auditoria estabeleceu o valor como segredo.
CENARIOS: tuple[_Cenario, ...] = (
    # ---- reconhecíveis por padrão, independentemente de contexto
    _Cenario("anthropic no titulo", "sk-ant-" + "A" * 40, title="chave sk-ant-" + "A" * 40),
    _Cenario("anthropic no corpo", "sk-ant-" + "B" * 40, body="a chave e sk-ant-" + "B" * 40),
    _Cenario("aws no corpo", "AKIAIOSFODNN7EXAMPLE", body="aws AKIAIOSFODNN7EXAMPLE fim"),
    _Cenario(
        "github token",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
        body="token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    ),
    _Cenario(
        "github pat",
        "github_pat_ABCDEFGHIJKLMNOPQRSTUV_0123456789",
        body="pat github_pat_ABCDEFGHIJKLMNOPQRSTUV_0123456789",
    ),
    # ---- segredo por chave sensível (camada estrutural)
    _Cenario("password escalar", "hunter2", structured={"password": "hunter2"}),
    _Cenario("lista sob password", "hunter2", structured={"password": ["hunter2"]}),
    _Cenario("objeto sob password", "hunter2", structured={"password": {"current": "hunter2"}}),
    _Cenario(
        "profundidade 4 sob password",
        "valor_secreto_antigo",
        structured={"password": {"historico": [{"anterior": "valor_secreto_antigo"}]}},
    ),
    _Cenario(
        "camelCase aninhado",
        "r3-confidential-value",
        structured={
            "connections": [{"dbPassword": {"versions": [[{"payload": "r3-confidential-value"}]]}}]
        },
    ),
    _Cenario(
        "auth.apiKey opaco",
        "r3-secret-opaque",
        structured={"auth": {"apiKey": "r3-secret-opaque"}},
    ),
    # ---- segredo por propagação (cópia de valor já comprovado sensível)
    _Cenario("copia sem rotulo", "hunter2", structured={"password": "hunter2", "copia": "hunter2"}),
    _Cenario("copia no corpo", "hunter2", body="hunter2", structured={"password": "hunter2"}),
    _Cenario("copia no titulo", "hunter2", title="hunter2", structured={"password": "hunter2"}),
    # ---- segredo por concatenação entre fragmentos (camada posicional)
    _Cenario("split minimo", "7890ABCDEF", body="sk-123456", structured={"resto": "7890ABCDEF"}),
    _Cenario(
        "split com folha interposta",
        "7890ABCDEF",
        body="sk-123456",
        structured={"a": "!!!", "resto": "7890ABCDEF"},
    ),
    _Cenario(
        "split com password extra",
        "7890ABCDEF",
        body="sk-123456",
        structured={"a": "7890ABCDEF", "password": "hunter2"},
    ),
    _Cenario(
        "prefixo bearer entre fragmentos",
        "ABCDEFGHIJKLMNOP",
        title="Bearer",
        body=" ABCDEFGHIJKLMNOP",
    ),
    _Cenario(
        "prefixo assigned_secret entre fragmentos",
        "valorSuperSecreto123",
        title="password:",
        body=" valorSuperSecreto123",
    ),
    _Cenario(
        "pem partido",
        "MIIEvQIBADANBg",
        body="-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg",
        structured={"resto": "kqhkiG9w0BAQEF\n-----END PRIVATE KEY-----"},
    ),
    # ---- segredo na própria chave
    _Cenario(
        "chave e um token", "sk-ant-" + "A" * 30, structured={"sk-ant-" + "A" * 30: "ordinary"}
    ),
    _Cenario(
        "chave com separador reservado", "hunter2", structured={"password: hunter2": "ordinary"}
    ),
)


@pytest.mark.parametrize("cenario", CENARIOS, ids=lambda item: item.nome)
def test_varredura_de_bytes_por_cenario(
    cenario: _Cenario,
    session: Session,
    workspace: DevWorkspace,
    base_commit: str,
    temp_settings: AppSettings,
) -> None:
    """Cada fixture adversarial, **sozinho** no artifact, com o arquivo físico aberto em
    modo binário e varrido pelo valor cru.

    Sozinho é o caso mais exigente: num artifact com muitos segredos juntos, a
    over-redaction de um esconderia o vazamento de outro atrás de `«redigido»` alheio.
    """
    _entry(
        session,
        workspace,
        title=cenario.title,
        body=cenario.body,
        structured=cenario.structured,
    )

    bruto, payload, manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)

    assert cenario.segredo.encode("utf-8") not in bruto, (
        f"{cenario.nome}: {cenario.segredo!r} sobreviveu nos bytes do artifact"
    )
    assert cenario.segredo not in json.dumps(manifest.entries, ensure_ascii=False), (
        f"{cenario.nome}: {cenario.segredo!r} sobreviveu em ContextManifest.entries"
    )
    # A varredura não passou por vazio: o bloco existe e sofreu redação.
    assert payload["blocks"]
    assert REDACTED in payload["blocks"][0]["text"]


def test_varredura_de_bytes_todos_os_cenarios_no_mesmo_artifact(
    session: Session, workspace: DevWorkspace, base_commit: str, temp_settings: AppSettings
) -> None:
    """GATE FINAL: **todos** os cenários das três rodadas, cada um como uma entrada, todos
    no **mesmo** Rendered Context Artifact — um arquivo físico, uma varredura.

    Cada entrada preserva a condição que torna seu valor segredo; a seleção põe todas no
    mesmo artifact, e o arquivo é lido em modo binário e conferido contra a lista inteira
    de valores conhecidos de uma vez.
    """
    for indice, cenario in enumerate(CENARIOS):
        _entry(
            session,
            workspace,
            title=f"{indice:02d} {cenario.title}",
            body=cenario.body,
            structured=cenario.structured,
        )

    bruto, payload, manifest = _artifact_bytes(session, workspace, base_commit, temp_settings)

    assert len(payload["blocks"]) == len(CENARIOS)

    sobreviventes = sorted(
        {cenario.segredo for cenario in CENARIOS if cenario.segredo.encode("utf-8") in bruto}
    )
    assert not sobreviventes, f"segredo sobreviveu nos bytes do artifact: {sobreviventes}"

    metadados = json.dumps(manifest.entries, ensure_ascii=False)
    nos_metadados = sorted({c.segredo for c in CENARIOS if c.segredo in metadados})
    assert not nos_metadados, f"segredo sobreviveu em ContextManifest.entries: {nos_metadados}"

    # O hash registrado corresponde aos bytes que acabamos de varrer — a varredura foi
    # sobre o arquivo que o manifest realmente aponta.
    assert hashlib.sha256(bruto).hexdigest() == manifest.rendered_context_hash
