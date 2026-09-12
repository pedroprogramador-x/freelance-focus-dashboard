"""`ContextManifest` e o Rendered Context Artifact — as duas camadas persistentes de [02] §5.

    Context Selection      →   ContextManifest        →   Rendered Context Artifact
    transitória, em memória    linha persistida           blob imutável por conteúdo
    (`selection.py`)           (`freeze_manifest`)        (`render_context`)

## O artefato: endereçado por conteúdo, e por isso escrito em bytes

O pipeline de [02] §5 é literal e a ordem dele é a garantia:

    objeto canônico → canonical_json → UTF-8 sem BOM → bytes → sha256 → artifacts/<sha>.json

O nome do arquivo **é** o hash do conteúdo. Um único byte diferente entre o que foi
hasheado e o que foi gravado quebra a verificação de integridade e a deduplicação ao mesmo
tempo, e a forma mais fácil de introduzir esse byte no Windows é a mais invisível:

> `open(path, "w")` abre em **modo texto**, e o modo texto traduz `\\n` para `\\r\\n` na
> escrita. O `json` não escreve `\\n` no separador porque `canonical_json` usa
> `separators=(",", ":")` — mas qualquer `\\n` **dentro de uma string** já foi escapado
> para `\\\\n` pelo `json`, e o que sobra são os `\\n` que um dia alguém acrescente ao
> formato. A tradução é silenciosa: nenhum erro, nenhum aviso, e o hash do arquivo em disco
> passa a diferir do hash registrado — **só no Windows**.

É a mesma classe de bug que E4-AUD4-001 caçou em `_run_git` (`subprocess.run(text=True)`
mexendo em bytes sem avisar), do outro lado: lá na leitura, aqui na escrita. A defesa é a
mesma: **nunca deixar o modo texto do Python participar**. Este módulo grava em `"wb"`,
sobre bytes que ele mesmo produziu com `.encode("utf-8")`, e
`test_context_router_e5.py` confere o sha256 dos bytes **relidos do disco** contra o
`rendered_context_hash` registrado.

## O `manifest_hash`: identidade semântica, não identidade de linha

Entram: `git_head`, `base_branch`, `entries` (ordenadas por `entry_id`), `source_files`,
`working_tree_divergence`, `derived`, `excluded`.

**Não** entram: `id`, `task_id`, `created_at`, `rendered_context_hash`,
`rendered_context_ref`, `renderer_version`, `approx_tokens`, `total_chars` e o próprio
`manifest_hash`. Os três primeiros são identidade **operacional** — duas tasks que
selecionaram exatamente o mesmo contexto no mesmo commit têm o mesmo manifest, e é isso
que torna o hash útil para comparar. Os demais são **derivados** do que já entrou:
incluí-los não acrescentaria informação e acoplaria o hash do manifest à versão do
renderizador, fazendo uma mudança de framing parecer mudança de contexto selecionado.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.context_engine.file_map import encode_path_identity
from app.context_engine.rendering import RENDERER_VERSION
from app.context_engine.selection import REASON_BUDGET, ContextSelection, ScoredEntry
from app.db.models import ContextManifest, WorkspaceTask
from app.safety.canonical import canonical_json

#: Versão da forma do objeto hasheado do manifest ([02] §7, "Versão"). Não é um campo do
#: `ContextManifest` — é o marcador de formato que impede um hash desta versão de colidir
#: com o de uma versão futura que inclua ou remova campos.
#: `v2`: `source_files`/`covered`/`excluded` (quando caminho) pré-codificam `path` via
#: `encode_path_identity` antes de entrar em `canonical_json` (fecha AUD-001/006/007).
MANIFEST_HASH_VERSION = 2

#: Prefixo de `rendered_context_ref`. Espelha `AppSettings.artifacts_dir`, que é
#: `data_dir/artifacts` ([02] §5).
ARTIFACT_STORE_PREFIX = "artifacts"


@dataclass(frozen=True, slots=True)
class RenderedContext:
    """O artefato gravado (ou já existente) e a identidade dele."""

    #: `rendered_context_hash` — sha256 dos bytes UTF-8 canônicos, sem BOM.
    hash: str
    #: `rendered_context_ref` — o caminho **dentro do artifact store**, não o absoluto.
    #: `data_dir` muda de máquina para máquina (e o `tmp_path` de um teste muda a cada
    #: execução); gravar o caminho absoluto faria a linha do manifest deixar de ser
    #: comparável entre ambientes por um motivo que não tem nada a ver com o contexto
    #: selecionado. Quem resolve o caminho real é quem conhece o `data_dir` — `config`.
    ref: str
    #: O caminho absoluto efetivamente gravado. Não vai para o banco.
    path: Path
    #: O objeto canônico, exatamente como serializado.
    payload: dict[str, Any]
    approx_tokens: int
    total_chars: int
    #: `False` quando o blob já existia em disco com este hash — a deduplicação de [02] §5.
    written: bool


def _block(order: int, entry: ScoredEntry) -> dict[str, Any]:
    """Um bloco do artefato, no schema de [02] §5.

    ``truncated`` é sempre `False` nesta fase, e ``original_chars`` é necessariamente igual
    a ``emitted_chars``: a política V1 não trunca bloco nenhum — o que não cabe é excluído
    inteiro, com `reason=budget`. Os três campos existem porque o schema os reserva para
    uso futuro; gravá-los com o valor que a política V1 impõe é mais honesto do que
    omiti-los e ter de adivinhar depois o que a ausência significava.

    ``origin`` é um objeto, e não um identificador solto, pela razão que dá sentido ao
    artefato inteiro ([ADR-0006] item 9): ele precisa continuar respondendo *qual
    conhecimento o Developer recebeu* **depois** de a entrada de origem ser editada ou
    apagada. Um `entry_id` órfão não responde nada; `entry_id` + `domain` + `title` +
    `content_hash` responde.
    """
    return {
        "order": order,
        "origin": {
            "kind": "context_entry",
            "entry_id": entry.entry_id,
            "domain": entry.domain,
            "title": entry.title,
            "content_hash": entry.content_hash,
        },
        "role": entry.domain,
        "text": entry.text,
        "truncated": False,
        "original_chars": entry.chars,
        "emitted_chars": entry.chars,
        "transformations": list(entry.transformations),
    }


def build_rendered_payload(selection: ContextSelection) -> dict[str, Any]:
    """O objeto canônico do artefato, antes de virar bytes. **Função pura.**

    A estrutura é a de [02] §5, e o que **não** está aqui é tão normativo quanto o que
    está: nenhum `created_at`. O timestamp operacional vive exclusivamente em
    `ContextManifest.created_at` — dentro do artefato ele faria dois payloads idênticos
    hashearem diferente, destruindo a deduplicação e o teste de reprodutibilidade.
    """
    blocks = [_block(order, entry) for order, entry in enumerate(selection.selected)]
    return {
        "renderer_version": RENDERER_VERSION,
        "blocks": blocks,
        "approx_tokens": selection.approx_tokens,
        "total_chars": selection.total_chars,
    }


def render_context(selection: ContextSelection, *, artifacts_dir: Path) -> RenderedContext:
    """Serializa, hasheia e grava o artefato. **Idempotente — e verificado.**

    O nome é o hash do conteúdo, então um arquivo já presente com aquele nome **deveria**
    já ter aquele conteúdo — mas "deveria" não é "tem": o arquivo pode ter sido truncado
    por uma cópia interrompida, corrompido por disco, ou editado por engano fora deste
    código. Fecha AUD-009: antes de reaproveitar por idempotência, os bytes **reais** em
    disco são lidos e o sha256 deles é comparado ao `digest` esperado. Só quando os dois
    batem a escrita é pulada; qualquer divergência é tratada como se o arquivo não
    existisse, e ele é regravado. A alternativa — confiar em `target.exists()` sozinho —
    devolveria um `rendered_context_hash` que não corresponde ao que está de fato em
    disco, quebrando exatamente a garantia que endereçamento por conteúdo existe para dar.

    A escrita é atômica (arquivo temporário no mesmo diretório + `os.replace`) para que
    ninguém leia um blob truncado com nome de blob íntegro. O nome temporário carrega PID e
    um UUID: dois processos gravando o mesmo hash ao mesmo tempo não disputam o mesmo
    arquivo intermediário, e o resultado final é byte a byte o mesmo de qualquer forma.
    """
    payload = build_rendered_payload(selection)
    data = canonical_json(payload).encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    target = artifacts_dir / f"{digest}.json"

    needs_write = not _matches_digest(target, digest)

    written = False
    if needs_write:
        temporary = artifacts_dir / f".{digest}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        # `"wb"`: o modo texto do Python traduziria `\n` para `\r\n` no Windows e o arquivo
        # deixaria de hashear como o que foi hasheado. Ver o docstring do módulo.
        with open(temporary, "wb") as handle:
            handle.write(data)
        os.replace(temporary, target)
        written = True

    return RenderedContext(
        hash=digest,
        ref=f"{ARTIFACT_STORE_PREFIX}/{digest}.json",
        path=target,
        payload=payload,
        approx_tokens=selection.approx_tokens,
        total_chars=selection.total_chars,
        written=written,
    )


def _matches_digest(target: Path, digest: str) -> bool:
    """Os bytes **em disco** em `target` hasheiam para `digest`? `False` também quando o
    arquivo não existe ou não pôde ser lido — os dois casos pedem a mesma ação (escrever).

    Lê em modo binário: o mesmo cuidado da escrita, do outro lado. Ler em modo texto
    aplicaria *universal newlines* na decodificação e o sha256 calculado não corresponderia
    aos bytes reais do arquivo — o mesmo bug de `_run_git` (E4-AUD4-001), de novo em outra
    fronteira IO.
    """
    if not target.exists():
        return False
    try:
        existing = target.read_bytes()
    except OSError:
        return False
    return hashlib.sha256(existing).hexdigest() == digest


def _encode_source_file(item: dict[str, Any]) -> dict[str, Any]:
    """`{path, blob_sha}` com `path` pré-codificado — identidade de caminho, não texto
    para leitura humana ([02] §7). `blob_sha` já é hex de 40 caracteres ASCII, imune a
    variante de normalização; não precisa da mesma codificação."""
    return {"path": encode_path_identity(str(item["path"])), "blob_sha": str(item["blob_sha"])}


def _encode_covered(item: dict[str, Any]) -> dict[str, Any]:
    """`{path, kind}` com `path` pré-codificado. `kind` é um dos literais fechados de
    `WorkingTreeChange` ([04]/`git_runtime`), sempre ASCII."""
    return {"path": encode_path_identity(str(item["path"])), "kind": str(item["kind"])}


def _encode_excluded(item: dict[str, Any]) -> dict[str, Any]:
    """`{path_or_entry, reason}`. `path_or_entry` só é identidade de caminho quando
    `reason` é `secret_policy`/`out_of_workspace`; em `reason=budget` é um `entry_id`
    (UUID, sempre ASCII, imune a NFC/NFD) — codificar um UUID não estaria errado, mas
    misturaria um campo que não representa caminho na mesma pré-codificação que existe
    especificamente para caminho, sem necessidade nenhuma."""
    subject = str(item["path_or_entry"])
    reason = str(item["reason"])
    encoded_subject = subject if reason == REASON_BUDGET else encode_path_identity(subject)
    return {"path_or_entry": encoded_subject, "reason": reason}


def compute_manifest_hash(
    *,
    git_head: str,
    base_branch: str | None,
    entries: list[dict[str, Any]],
    source_files: list[dict[str, Any]],
    working_tree_divergence: dict[str, Any],
    derived: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
) -> str:
    """`sha256(canonical_json(...))` sobre os sete campos semânticos.

    Cada coleção chega **já ordenada** por quem a montou, e a ordenação é reafirmada aqui:
    `canonical_json` preserva ordem de array de propósito ([02] §7) e não tem como saber
    que estas listas são conjuntos. Ordenar duas vezes é barato; confiar que a outra ponta
    ordenou é como o `entries` acaba saindo na ordem do `SELECT`.

    * `entries` — por `entry_id` (UUID, ASCII — não precisa de pré-codificação de path);
    * `source_files` — por `path` **pré-codificado**, único na árvore do commit;
    * `working_tree_divergence.covered` — por `(path, kind)`, `path` **pré-codificado**;
    * `derived` — por `(kind, hash)`;
    * `excluded` — por `(reason, path_or_entry)`, `path_or_entry` **pré-codificado**
      quando é caminho (não quando é `entry_id`, em `reason=budget`).

    **Pré-codificação de identidade de caminho** ([02] §7, "Identidade de caminho vs.
    normalização textual" — fecha AUD-001/006/007): `source_files`, `covered` e o
    `path_or_entry` de exclusão por caminho entram como `encode_path_identity(path)`, não
    como o `path` cru. Sem isso, `canonical_json` normaliza a string em NFC antes de
    hashear, e dois arquivos Git genuinamente distintos — um grafado em NFC, outro
    visualmente idêntico grafado em NFD — colapsariam no mesmo texto e no mesmo hash. A
    pré-codificação acontece **só aqui**, no ponto de entrada em `canonical_json`; os
    parâmetros recebidos e a linha `ContextManifest` gravada continuam com o `path`
    legível, sem tocar em nada fora desta função.
    """
    encoded_source_files = sorted(
        (_encode_source_file(item) for item in source_files),
        key=lambda item: str(item["path"]),
    )
    encoded_covered = sorted(
        (_encode_covered(item) for item in working_tree_divergence.get("covered", [])),
        key=lambda item: (str(item["path"]), str(item["kind"])),
    )
    encoded_excluded = sorted(
        (_encode_excluded(item) for item in excluded),
        key=lambda item: (str(item["reason"]), str(item["path_or_entry"])),
    )
    return _sha256_of_canonical(
        {
            "v": MANIFEST_HASH_VERSION,
            "git_head": git_head,
            "base_branch": base_branch,
            "entries": sorted(entries, key=lambda item: str(item["entry_id"])),
            "source_files": encoded_source_files,
            "working_tree_divergence": {
                "dirty_file_count": working_tree_divergence.get("dirty_file_count"),
                "covered": encoded_covered,
            },
            "derived": sorted(derived, key=lambda item: (str(item["kind"]), str(item["hash"]))),
            "excluded": encoded_excluded,
        }
    )


def _sha256_of_canonical(value: dict[str, Any]) -> str:
    """`sha256` dos bytes UTF-8 do JSON canônico. Mesma cadeia do artefato, de propósito."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def freeze_manifest(
    session: Session,
    task: WorkspaceTask,
    selection: ContextSelection,
    *,
    base_branch: str | None,
    artifacts_dir: Path,
) -> ContextManifest:
    """Grava o artefato e a linha imutável de `ContextManifest`.

    A ordem é obrigatória: `rendered_context_hash` e `rendered_context_ref` são `NOT NULL`,
    então o artefato existe **antes** da linha. Um manifest apontando para um blob que
    ninguém gravou seria pior do que nenhum manifest — ele afirma provar o payload.

    ``base_branch`` é recebido, não lido aqui: quem congela o `base_commit` ([02] §6) é
    quem sabe de que branch ele veio, e uma segunda leitura de git aqui poderia enxergar um
    branch diferente do que a seleção enxergou.
    """
    rendered = render_context(selection, artifacts_dir=artifacts_dir)

    entries = sorted(
        (entry.as_manifest_entry() for entry in selection.selected),
        key=lambda item: str(item["entry_id"]),
    )
    source_files = [dict(item) for item in selection.source_files]
    derived = [selection.file_map.as_derived_record()]
    excluded = [item.as_canonical() for item in selection.excluded]

    manifest = ContextManifest(
        task_id=task.id,
        git_head=selection.base_commit,
        base_branch=base_branch,
        entries=entries,
        source_files=source_files,
        working_tree_divergence=selection.working_tree_divergence,
        derived=derived,
        excluded=excluded,
        rendered_context_hash=rendered.hash,
        rendered_context_ref=rendered.ref,
        renderer_version=RENDERER_VERSION,
        approx_tokens=rendered.approx_tokens,
        total_chars=rendered.total_chars,
        manifest_hash=compute_manifest_hash(
            git_head=selection.base_commit,
            base_branch=base_branch,
            entries=entries,
            source_files=source_files,
            working_tree_divergence=selection.working_tree_divergence,
            derived=derived,
            excluded=excluded,
        ),
    )
    session.add(manifest)
    session.flush()
    return manifest


__all__ = [
    "ARTIFACT_STORE_PREFIX",
    "MANIFEST_HASH_VERSION",
    "RenderedContext",
    "build_rendered_payload",
    "compute_manifest_hash",
    "freeze_manifest",
    "render_context",
]
