"""`file_map` — o artefato derivado do repositório, de [ADR-0006] item 4.

> "**`file_map` não é entrada** — é artefato derivado do repositório, gerado sob demanda e
> cacheado por `(workspace_id, git_head)`. Guardá-lo como entrada editável criaria uma
> cópia que envelhece a cada commit e que poderia ser editada para algo falso."

Daí decorrem as três regras deste módulo, e nenhuma delas é detalhe de implementação:

1. **A fonte é `list_tree(base_commit)`, nunca a árvore de trabalho.** O mapa descreve o
   commit congelado ([02] §6), que é o que a execução vai enxergar. Ler o disco traria
   arquivo não commitado para dentro de um artefato que se apresenta como sendo do commit.
2. **A ordenação é explícita, por `path`.** Ordem de dict, de set ou de retorno de git não
   é ordenação — é coincidência que sobrevive até a próxima versão de alguma coisa.
3. **O hash sai de `canonical_json`**, a mesma função de `policy_hash`, `content_hash` e
   `source_hash` ([02] §7). Uma segunda normalização seria uma segunda chance de divergir.

Puro quanto à ordenação e ao hash; o único IO é o `list_tree` que `build_file_map` faz por
`git_runtime` — e `build_file_map_from_tree` nem esse tem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.git_runtime import TreeListing, list_tree
from app.safety.canonical import canonical_sha256

#: Versão da forma do item de mapa. Muda se a estrutura mudar — e aí todo `file_map_hash`
#: gravado passa a ser de outra versão, que é exatamente o efeito desejado ([02] §7,
#: "Versão").
FILE_MAP_VERSION = 2


def encode_path_identity(value: str) -> str:
    """Pré-codifica identidade de caminho antes de entrar em `canonical_json` ([02] §7,
    "Identidade de caminho vs. normalização textual").

    `canonical_json` normaliza toda string em NFC — correto para texto autoral, errado
    para caminho: um path grafado em NFC e o mesmo path grafado em NFD são **arquivos
    diferentes** para o Git (o blob SHA já prova isso), e a normalização NFC os colapsaria
    no mesmo texto, escondendo a diferença bem no campo cuja função é provar identidade.

    Hex dos bytes UTF-8 exatos: o alfabeto `0-9a-f` não tem variante de normalização
    Unicode nenhuma, então atravessa `canonical_json` bit a bit. **Não** é ofuscação —
    é o único helper de referência que a nota de [02] §7 pede; qualquer campo de
    identidade de caminho que entrar em hash passa por aqui antes.
    """
    return value.encode("utf-8").hex()


@dataclass(frozen=True, slots=True)
class FileMapItem:
    """Um arquivo do commit, na forma que o scoring de proximidade consulta.

    ``dir_path`` é o diretório que contém o arquivo, relativo ao workspace, com separador
    `/` e **sem** barra final; a raiz é `""`. ``dir_depth`` é a quantidade de segmentos de
    ``dir_path`` — `0` na raiz. ``extension`` inclui o ponto (`".py"`) e é `""` quando não
    há extensão; um nome que **começa** com ponto e não tem outro (`.env`, `.gitignore`)
    não tem extensão — o ponto ali é prefixo, não separador.
    """

    path: str
    dir_path: str
    dir_depth: int
    extension: str

    def as_canonical(self) -> dict[str, Any]:
        """Forma canônica do item. As quatro chaves, sempre — nunca omissão ([02] §7).

        `path`, `dir_path` e `extension` entram **pré-codificados** por
        `encode_path_identity` — são identidade de caminho, não texto para leitura
        humana, e um deles (`extension`) é substring do próprio `path`, então carrega o
        mesmo risco de colisão NFC/NFD. Os atributos do dataclass (`self.path` etc.)
        continuam legíveis — só a forma que entra no hash muda.
        """
        return {
            "path": encode_path_identity(self.path),
            "dir_path": encode_path_identity(self.dir_path),
            "dir_depth": self.dir_depth,
            "extension": encode_path_identity(self.extension),
        }


@dataclass(frozen=True, slots=True)
class FileMap:
    """O mapa inteiro: os itens ordenados por `path`, mais a identidade dele.

    ``partial`` repete o que `TreeListing` já dizia: houve caminho no workspace que a
    leitura não soube nomear (E4-AUD3-001, E4-AUD4-002). O mapa continua sendo o que deu
    para ler — o mesmo tipo-produto de E4-AUD5-001 — e quem consome decide o que fazer com
    a incompletude, em vez de recebê-la como ausência de mapa.
    """

    items: tuple[FileMapItem, ...]
    hash: str
    partial: bool = False

    @property
    def item_count(self) -> int:
        return len(self.items)

    def as_derived_record(self) -> dict[str, Any]:
        """A linha de `ContextManifest.derived` de [02] §5: `{kind, hash, item_count}`."""
        return {"kind": "file_map", "hash": self.hash, "item_count": self.item_count}


def _split_path(path: str) -> FileMapItem:
    """`path` → item. Só decomposição de string: sem disco, sem `os.path`.

    `os.path` decidiria por separador **da plataforma corrente**, e o caminho aqui é sempre
    o do git: relativo ao workspace, com `/`. Usar a biblioteca de caminho do sistema faria
    o mesmo commit produzir mapas diferentes no Windows e no Linux — que é exatamente a
    classe de não-determinismo que esta fase existe para fechar.
    """
    head, separator, name = path.rpartition("/")
    dir_path = head if separator else ""
    dir_depth = len(dir_path.split("/")) if dir_path else 0

    stem, dot, suffix = name.rpartition(".")
    # `stem` vazio com ponto presente é o caso `.env`: o ponto abre o nome, não separa uma
    # extensão. `dot` vazio é o caso `Makefile`, sem ponto nenhum.
    extension = f".{suffix}" if dot and stem else ""

    return FileMapItem(path=path, dir_path=dir_path, dir_depth=dir_depth, extension=extension)


def compute_file_map_hash(items: tuple[FileMapItem, ...]) -> str:
    """`sha256(canonical_json({v, items}))` — a mesma serialização de [02] §7.

    Os itens entram **na ordem em que estão**, e quem os monta já os ordenou por `path`:
    `canonical_json` preserva ordem de array de propósito e não tem como adivinhar que esta
    lista é um conjunto ordenado.
    """
    return canonical_sha256(
        {"v": FILE_MAP_VERSION, "items": [item.as_canonical() for item in items]}
    )


def build_file_map_from_tree(tree: TreeListing) -> FileMap:
    """Monta o mapa a partir de uma árvore já lida. **Função pura.**

    Recebe a `TreeListing` em vez de buscá-la é o que permite exercitar a forma do mapa
    contra árvores construídas à mão, e é o mesmo desenho de `expand_source_refs` na E4.

    A ordenação é feita aqui, por `path`, mesmo `list_tree` já devolvendo ordenado: quem
    hasheia é responsável pela ordem do que hasheia ([02] §7), e depender da ordenação de
    outro módulo é depender de um detalhe que ninguém prometeu manter.
    """
    items = tuple(sorted((_split_path(path) for path, _blob_sha in tree.files), key=_sort_key))
    return FileMap(items=items, hash=compute_file_map_hash(items), partial=tree.partial)


def _sort_key(item: FileMapItem) -> str:
    """A chave de ordenação total do mapa: o `path`, que já é único na árvore.

    `list_tree` recusa a árvore inteira quando dois registros repetem o mesmo caminho, então
    `path` é chave **total** aqui — não há empate para um segundo critério desfazer.
    """
    return item.path


#: Cache por `(workspace_id, base_commit)`, exatamente a chave de [ADR-0006] item 4. O par
#: identifica o conteúdo por construção: o mesmo commit tem a mesma árvore, sempre. Uma
#: leitura que **falhou** nunca entra — falha é transitória (git ausente, repo travado) e
#: cacheá-la transformaria um problema de momento em resposta permanente.
_CACHE: dict[tuple[str, str], FileMap] = {}

#: Teto do cache. Não há política de recência: qualquer entrada serve para descartar,
#: porque o valor é reconstruível e idêntico. Um LRU aqui seria complexidade para escolher
#: entre coisas equivalentes.
_CACHE_MAX_ENTRIES = 64


def build_file_map(workspace_id: str, local_path: str, base_commit: str) -> FileMap | None:
    """O mapa de `base_commit`, cacheado por `(workspace_id, base_commit)`.

    `None` quando a árvore não pôde ser lida — não é repositório, `git` indisponível,
    `base_commit` não é um SHA-1 completo. É o mesmo `None` de `list_tree`, propagado sem
    reinterpretação: quem chama decide se isso é `unknown` ou erro.

    ``local_path`` não entra na chave do cache **de propósito**: o par de [ADR-0006] é
    `(workspace_id, git_head)`, e o `local_path` de um workspace é imutável e único
    (`DevWorkspace.local_path` é `UNIQUE`), então incluí-lo não distinguiria nada e faria a
    chave divergir do documento.
    """
    key = (workspace_id, base_commit)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    tree = list_tree(local_path, base_commit)
    if tree is None:
        return None

    file_map = build_file_map_from_tree(tree)
    if len(_CACHE) >= _CACHE_MAX_ENTRIES:
        _CACHE.clear()
    _CACHE[key] = file_map
    return file_map


def clear_file_map_cache() -> None:
    """Esvazia o cache. Existe para os testes; nenhum caminho de produção chama."""
    _CACHE.clear()


__all__ = [
    "FILE_MAP_VERSION",
    "FileMap",
    "FileMapItem",
    "build_file_map",
    "build_file_map_from_tree",
    "clear_file_map_cache",
    "compute_file_map_hash",
    "encode_path_identity",
]
