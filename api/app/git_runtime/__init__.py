"""Git Runtime — adaptador de Git.

[01](../../../docs/architecture/01-v1-architecture.md) §2: `git_runtime/` é o adaptador de
Git e o ciclo de vida de worktree. Até aqui existem **três leituras**, todas só de leitura:

* `preflight` (E3) — dado um caminho absoluto, responde se é repositório, qual o `HEAD`, o
  branch e quantos arquivos divergem da árvore de trabalho.
* `list_tree` (E4) — `(path, blob_sha)` de **um commit**, a Parte A da verificação dupla de
  [03](../../../docs/architecture/03-context-architecture.md) §3.
* `working_tree_diff_against` (E4, E4-AUD-003) — divergências entre a árvore de trabalho
  **atual** e um commit **congelado**, a Parte B.
* `working_tree_status` (E4) — divergências contra o `HEAD` corrente. Continua existindo
  porque é a pergunta que o preflight e a UI fazem, mas **não** é a Parte B.

[07](../../../docs/architecture/07-roadmap-v1.md) "Projeto criado do zero": um
`DevWorkspace` pode **não** ser repositório Git, e isso não é erro — é
`is_git_repo=False`, com os demais campos `None`.

Invariantes congelados ([01] contrato de `git_runtime/`, [07] gate E3):

* **Só leitura.** Nenhum subcomando que altere o repositório do usuário — sem `commit`,
  `merge`, `push`, `rebase`, `reset`, `checkout`, `clean`, `init`, `apply`, `stash`.
  `test_architecture.py::test_git_runtime_e_somente_leitura` transforma isso em falha de
  suíte.
* **Nunca lança.** Qualquer falha de IO, `timeout`, `git` ausente do `PATH` ou saída
  inesperada vira o resultado neutro. Para `preflight` esse resultado é `_NOT_A_REPO`;
  para as duas leituras da E4 é **`None`**, e a diferença é deliberada: uma lista vazia
  significaria "o commit não tem arquivo nenhum" e produziria um `source_hash` válido
  sobre o conjunto vazio — *fail open*. `None` significa "não consegui ler", e é o que o
  Context Engine traduz em `state = unknown` ([03] §3). Isso vale para a leitura que **não
  aconteceu**. Uma leitura que aconteceu e ficou **incompleta** é outra coisa: ela devolve
  um `TreeListing`/`WorkingTreeListing` com o que leu **e** com o que não conseguiu nomear
  (E4-AUD5-001) — quem chama decide o que fazer com cada metade, e nunca é obrigado a jogar
  fora o que sabe por causa do que não sabe.
* **Timeout curto e fixo.** Um preflight travado não pode segurar uma requisição HTTP.
* **Todo caminho devolvido é relativo ao `local_path`** (E4-AUD-002), e o caminho até lá é
  o mesmo nos três comandos:

  1. a base que o git emite é **fixada na raiz do repositório** — `--full-tree` no
     `ls-tree`, `--no-relative` no `diff`, e as opções `-c` de `_READONLY_GIT_OPTIONS`;
  2. `_workspace_prefix` lê o prefixo do workspace dentro do repo, **uma vez**;
  3. `_strip_prefix` o remove, **byte a byte, sem reinterpretar caractere nenhum**. O que
     estiver fora do workspace é **descartado**, porque um `source_ref` é relativo ao
     workspace por definição ([02] §2) e nunca poderia alcançá-lo.

  Uma base, uma transformação, em todo lugar. A versão anterior deixava o `ls-tree`
  relativo ao diretório corrente e só o resto relativo à raiz, e era essa **assimetria** que
  produzia um falso `fresh` novo a cada rodada de auditoria: bastava um caminho nomear-se de
  um jeito de um lado e de outro do outro para o cruzamento com a cobertura não casar.

* **Nada de reinterpretar `\\`** (E4-AUD3-001). O git emite `/` como separador em **todas**
  as plataformas, em toda saída de caminho — `ls-tree`, `status`, `diff`, `rev-parse
  --show-prefix`. Logo, uma `\\` na saída do git é sempre um **caractere literal do nome do
  arquivo**, nunca um separador. A tradução `\\` → `/` que existia aqui era um reflexo de
  Windows aplicado ao lugar errado, e **criava** a ambiguidade que dizia resolver: um blob
  chamado `ws\\outside.py` na raiz do repositório virava `ws/outside.py` e passava a ser
  contado como filho do workspace `ws/` — troca de identidade de caminho, com falso `fresh`
  ou falso `stale` conforme o lado. Ver `_strip_prefix` e `UnrepresentablePath`.

* **`subprocess.run` nunca recebe `text=True`** (E4-AUD4-001, E4-AUD4-002). `_run_git`
  captura `stdout`/`stderr` como **bytes crus** e a decodificação para texto é sempre um
  passo explícito depois, feito por `_decode_text`. Duas falhas diferentes vinham do mesmo
  `text=True`:

  1. **Tradução automática de quebra de linha.** Modo texto do Python aplica *universal
     newlines* na decodificação — `\r\n` e `\r` viram `\n`, mesmo dentro de um nome de
     arquivo que os contenha como bytes **literais**. Dois blobs distintos, `line\rname.py`
     e `line\nname.py`, colidiam no mesmo texto depois da tradução — dois arquivos
     diferentes produzindo o mesmo `source_hash`, silenciosamente.
  2. **Decodificação numa thread que não devolve o controle a `_run_git`.** Com
     `capture_output=True` e saída grande o bastante (ou, no Windows, sempre — a
     implementação de `Popen.communicate()` lê `stdout`/`stderr` em threads separadas),
     `Popen` decodifica dentro da thread leitora quando `text=True` está ligado. Uma
     decodificação que falha ali levanta dentro da thread, não da chamada de `_run_git` —
     o `except` deste módulo nunca a vê, e o resultado que `communicate()` monta a partir
     de uma thread que morreu sem terminar produz um erro **diferente** do
     `UnicodeDecodeError` esperado (tipicamente `IndexError`, ao indexar um buffer que a
     thread nunca preencheu), que também não está no `except`. Byte inválido em qualquer
     lugar da árvore virava **500** em vez de um resultado neutro.

  Bytes crus não têm nenhuma das duas armadilhas: não há tradução de quebra de linha em
  bytes, e a decodificação acontece só depois, no código deste módulo, dentro de um `try`
  que este módulo controla. Falha de decodificação vira o mesmo `UnrepresentablePath` que
  já existe desde E4-AUD3-001 para "li, e não consigo nomear de volta" — a família de
  motivo cresceu (barra invertida literal, ou bytes que não são UTF-8 válido), a resposta
  continua sendo a mesma.

`git_runtime/` pode importar `safety`, `config` e stdlib — e nada mais ([01]). O
`subprocess` fica confinado a este pacote — o único ponto de chamada é `_run_git` — e
qualquer outro uso continua proibido até o Full Safety Runtime (E7).
"""

from __future__ import annotations

import os
import re
import shutil

# git de LEITURA apenas; verbos mutantes proibidos (ver docstring + test_architecture.py).
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from app.safety.source_refs import PATH_SEPARATOR_ALIASES

#: Folgado para operações locais; curto o bastante para não segurar o event loop.
_TIMEOUT_SECONDS = 5

_SHA1_LENGTH = 40

#: SHA-1 completo, **estrito**: 40 dígitos hexadecimais e nada mais. Estrito de propósito —
#: `int(value, 16)` aceitaria `0x…`, sinal e espaço em volta, e um `commit` assim chegaria
#: ao `argv` do git. Todo commit que entra em `list_tree` passa por aqui primeiro.
_SHA1_RE = re.compile(r"[0-9a-fA-F]{40}")

#: Opções globais em **toda** invocação. Duas famílias, por dois motivos diferentes.
#:
#: ## Somente-leitura (E3-AUD-003)
#:
#: `core.fsmonitor=false` impede o git de iniciar o processo de fsmonitor configurado no
#: repo do usuário; a variável `GIT_OPTIONAL_LOCKS=0` (em `_git_env`) impede o refresh do
#: `.git/index` que um `git status` normal faria.
#:
#: ## Base de caminho determinística (E4-AUD2, decisão estratégica)
#:
#: Quatro findings em duas rodadas de auditoria foram **a mesma classe de defeito**: a base
#: dos caminhos que o git emite depende de configuração que o repositório do usuário pode
#: mudar, e cada variação produzia um falso `fresh` diferente. Corrigir caso a caso estava
#: gerando casos novos. A regra passou a ser a mesma que a E3 já aplicava para leitura:
#: **nada de default — a configuração relevante é fixada por `-c`, explicitamente, em toda
#: invocação**, e o repositório do usuário não consegue mover a base debaixo de nós.
#:
#: **`diff.relative=false`** — *load-bearing*. Com `diff.relative=true` no repo do usuário,
#: o `diff` nomeia a partir do diretório corrente em vez da raiz, e o cruzamento com a
#: cobertura deixa de casar (E4-AUD2-001). Medido: com `true`, `M src/one.txt`; com
#: `false`, `M ws/src/one.txt`. O `diff` ainda leva `--no-relative` explícito, por cima.
#:
#: **`core.quotepath=false`** — seguro hoje, porque todo comando usa `-z` e já recebe bytes
#: crus. Fixado assim mesmo: uma leitura futura sem `-z` herdaria *C-quoting* e passaria a
#: casar contra um caminho que não existe.
#:
#: **`status.relativePaths=false`** — declara a base que o código consome. Medido: o
#: `--porcelain=v2 -z` **ignora** esta opção — a saída é relativa à raiz com `true`, com
#: `false` e sem nada. Fixamos `false` mesmo assim porque é a base que o código de fato
#: assume; escrever `true` documentaria uma intenção que o código não implementa, e o
#: próximo leitor poderia remover o `_strip_prefix` confiando nela.
#:
#: **A base pinada é sempre a raiz do repositório**, nos três comandos, e `_strip_prefix` é
#: a **única** transformação aplicada depois. Uma base, uma transformação, em todo lugar —
#: era a assimetria (o `ls-tree` relativo ao cwd, o resto relativo à raiz) que produzia os
#: casos novos a cada rodada.
_READONLY_GIT_OPTIONS = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.quotepath=false",
    "-c",
    "diff.relative=false",
    "-c",
    "status.relativePaths=false",
)

#: Ambiente **mínimo**: só o que o `git` precisa para funcionar como leitor no SO. **Nenhum
#: `GIT_*` entra** — nem `GIT_EXEC_PATH`/`GIT_TEMPLATE_DIR` (E3-AUD2-003): o git resolve o
#: exec-path a partir do próprio binário, e template dir só importa em `git init`, que
#: nunca chamamos. `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE`/`GIT_CONFIG`/helpers de
#: credencial/editor/pager herdados do processo pai ficam todos de fora.
#: `test_git_runtime.py::test_git_env_e_exatamente_a_allowlist` trava isso por
#: **igualdade de conjunto**, não por checagem de nomes conhecidos.
_GIT_ENV_ALLOWLIST = frozenset(
    name.upper()
    for name in (
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMDATA",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
    )
)

#: As duas únicas variáveis que `_git_env` **adiciona** ao ambiente filtrado.
_GIT_ENV_OVERRIDES = {
    "GIT_OPTIONAL_LOCKS": "0",  # nada de refresh/lock do índice num comando de leitura
    "GIT_TERMINAL_PROMPT": "0",  # nunca abre prompt de credencial
}


@dataclass(frozen=True, slots=True)
class GitPreflight:
    """Estado observável de um diretório quanto a Git. Sem decisão embutida.

    ``head`` é `None` num repositório recém-criado sem nenhum commit (*unborn branch*),
    embora ``is_git_repo`` já seja `True` e ``branch`` possa estar preenchido.
    ``branch`` é `None` quando o `HEAD` está *detached*. ``dirty_file_count`` conta as
    linhas de ``git status --porcelain`` — `0` numa árvore limpa, `None` se a leitura
    falhou.
    """

    is_git_repo: bool
    head: str | None
    branch: str | None
    dirty_file_count: int | None


_NOT_A_REPO = GitPreflight(is_git_repo=False, head=None, branch=None, dirty_file_count=None)


def _git_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Ambiente mínimo para o git: `(source ∩ allowlist) ∪ _GIT_ENV_OVERRIDES`. Nada mais.

    `source` é `os.environ` por padrão; o parâmetro existe só para os testes poderem
    contaminar a entrada sem mexer no ambiente global. As overrides vêm por último e
    vencem qualquer valor herdado (E3-AUD2-003).
    """
    environ = os.environ if source is None else source
    env = {key: value for key, value in environ.items() if key.upper() in _GIT_ENV_ALLOWLIST}
    env.update(_GIT_ENV_OVERRIDES)
    return env


def _run_git(git: str, local_path: str, *args: str) -> subprocess.CompletedProcess[bytes] | None:
    """Executa `git -c core.fsmonitor=false -C <local_path> <args>` sem shell, ambiente mínimo.

    Devolve `None` em qualquer falha. `stdout`/`stderr` saem como **bytes**, nunca texto
    (E4-AUD4-001, E4-AUD4-002 — ver a docstring do módulo): decidir texto aqui dentro do
    `subprocess` arrisca tradução de quebra de linha e decodificação numa thread que este
    `except` não alcança. A conversão para texto é sempre um passo explícito de quem chama,
    via `_decode_text`.
    """
    try:
        return subprocess.run(  # noqa: S603 — sem shell; argv literal; git resolvido por shutil.which
            [git, *_READONLY_GIT_OPTIONS, "-C", local_path, *args],
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
            env=_git_env(),
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        # ValueError cobre, entre outros, byte nulo embutido no caminho — que o
        # `subprocess` recusa antes de chegar ao `git`.
        return None


def _decode_text(data: bytes) -> str | None:
    """Decodifica bytes crus do git como UTF-8 **estrito**. `None` se não for válido.

    Chamada explicitamente, nunca dentro do `subprocess` (E4-AUD4-002): uma decodificação
    que falha aqui é um `try`/`except` comum, dentro de uma função síncrona que quem chamou
    já está preparado para ver devolver `None`. Não há tradução de quebra de linha nenhuma
    — `bytes.decode` não faz *universal newlines*; só `str.splitlines()`/modo texto fazem, e
    nenhum dos dois entra no caminho de um nome de arquivo (E4-AUD4-001).
    """
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None


def _describe_undecodable(raw: bytes) -> str:
    """Representação **só de diagnóstico** de bytes que não são UTF-8 válido.

    `errors="backslashreplace"` aqui, e só aqui: esta é a única leitura de caminho do
    módulo que não promete que o texto devolvido corresponde aos bytes reais — ela existe
    para o motivo registrado em `UnrepresentablePath.display` ser legível por um humano, não
    para ser comparado, casado contra `source_ref` ou usado como identidade de arquivo em
    lugar nenhum.
    """
    return raw.decode("utf-8", errors="backslashreplace")


def _stdout_if_ok(result: subprocess.CompletedProcess[bytes] | None) -> str | None:
    if result is None or result.returncode != 0:
        return None
    text = _decode_text(result.stdout)
    if text is None:
        return None
    value = text.strip()
    return value or None


def preflight(local_path: str) -> GitPreflight:
    """Lê o estado Git de `local_path` (caminho absoluto, canônico). Nunca lança."""
    git = shutil.which("git")
    if git is None:
        return _NOT_A_REPO

    inside = _run_git(git, local_path, "rev-parse", "--is-inside-work-tree")
    if _stdout_if_ok(inside) != "true":
        return _NOT_A_REPO

    head = _stdout_if_ok(_run_git(git, local_path, "rev-parse", "HEAD"))
    if head is not None and not _SHA1_RE.fullmatch(head):
        head = None

    branch = _stdout_if_ok(_run_git(git, local_path, "symbolic-ref", "--quiet", "--short", "HEAD"))

    dirty_file_count: int | None = None
    status = _run_git(git, local_path, "status", "--porcelain")
    if status is not None and status.returncode == 0:
        # `_decode_text` devolve `None` num byte inválido; `dirty_file_count` fica `None`
        # como qualquer outra leitura que não deu para completar — não é identidade de
        # arquivo, é só uma contagem, e "não sei" é a resposta honesta para os dois casos.
        status_text = _decode_text(status.stdout)
        if status_text is not None:
            dirty_file_count = sum(1 for line in status_text.splitlines() if line.strip())

    return GitPreflight(
        is_git_repo=True,
        head=head,
        branch=branch,
        dirty_file_count=dirty_file_count,
    )


def _workspace_prefix(git: str, local_path: str) -> str | None:
    """Caminho de `local_path` **a partir da raiz do repositório**, com `/` no fim, ou `""`.

    `None` quando o git não respondeu. Um repositório cujo `local_path` é a própria raiz
    devolve `""`, e aí `_strip_prefix` é a identidade.

    Existe por causa de E4-AUD-002: `status`/`diff` com `-z` nomeiam a partir da raiz do
    repositório, `ls-tree` nomeia a partir do diretório corrente, e `source_refs` são
    relativos ao workspace. Sem reduzir tudo a uma base só, um `DevWorkspace` em
    subdiretório nunca casa uma divergência com a cobertura.
    """
    result = _run_git(git, local_path, "rev-parse", "--show-prefix")
    if result is None or result.returncode != 0:
        return None
    # `_decode_text` primeiro (E4-AUD4-002): um nome de diretório do workspace que não é
    # UTF-8 válido faz o prefixo inteiro devolver `None`, e as três leituras que dependem
    # dele já sabem tratar isso como "não consegui ler" — não há uma forma parcial de
    # prefixo que faça sentido.
    text = _decode_text(result.stdout)
    if text is None:
        return None
    # `--show-prefix` devolve "" (linha vazia) na raiz e "sub/dir/" num subdiretório.
    #
    # **Só o terminador de linha é removido** (E4-AUD2-003). Um `.strip()` comeria espaço
    # que faz parte do nome real do diretório: para um workspace em `" lead ws/"` ele
    # devolvia `"lead ws/"`, e aí `_strip_prefix` não reconhecia mais nenhum caminho como
    # sendo de dentro do workspace — todas as divergências eram descartadas como "de fora",
    # e a entrada ficava eternamente `fresh`. Espaço no início ou no fim de um nome de
    # diretório é legal em todos os sistemas que suportamos.
    #
    # **Nenhuma tradução de `\\`** (E4-AUD3-001): o `--show-prefix` também usa `/` como
    # separador em toda plataforma, então uma `\\` aqui faz parte do **nome** de um diretório
    # do caminho. Traduzi-la deixaria o prefixo com uma forma que a saída dos outros
    # comandos não tem, e `_strip_prefix` pararia de reconhecer o workspace inteiro.
    #
    # **Nenhuma tradução de `\r`/`\n` embutidos** (E4-AUD4-001): só o terminador de linha
    # real, no fim, é removido — pelo mesmo motivo do `.strip()` acima, um caractere que
    # faz parte do nome não pode ser tratado como delimitador.
    return text.rstrip("\n").rstrip("\r")


def _strip_prefix_bytes(path: bytes, prefix: bytes) -> bytes | None:
    """A mesma regra de `_strip_prefix`, em **bytes** — comparação literal, byte a byte.

    É a implementação real; `_strip_prefix` (texto) delega para cá. Existe separada porque
    um caminho vindo do git pode não ser UTF-8 válido (E4-AUD4-002), e decidir se ele é de
    dentro ou de fora do workspace **não pode depender de conseguir decodificá-lo
    primeiro** — "isto começa com o prefixo do workspace?" é uma pergunta só sobre bytes, e
    respondê-la antes da decodificação é o que permite classificar um caminho corrompido
    como "de fora, descarta" sem nunca precisar saber o que ele diz.

    `None` quando o caminho está **fora** do workspace — e aí ele é descartado, não
    convertido em `../algo`: um `source_ref` jamais contém `..` ([02] §2), então esse
    caminho não pode ser coberto por entrada nenhuma, e mantê-lo só inflaria a contagem de
    divergências do workspace com arquivos que não são dele.
    """
    if not prefix:
        return path
    if not path.startswith(prefix):
        return None
    return path[len(prefix) :] or None


def _strip_prefix(path: str, prefix: str) -> str | None:
    """Converte um caminho relativo à raiz do repo em relativo ao workspace.

    Comparação **literal**, sem normalizar caractere nenhum: os dois lados vêm do mesmo
    git, na mesma base, com `/` como separador (ver a docstring do módulo).

    Delega para `_strip_prefix_bytes` (E4-AUD4-002) — `path` e `prefix` chegam aqui como
    texto **já decodificado com sucesso**, então o `encode`/`decode` de ida e volta é
    sem perdas; a função existe para o código (e os testes) que já trabalham com texto
    poderem continuar chamando com strings, sem duplicar a regra de comparação.

    O `.replace("\\\\", "/")` que existia aqui trocava a **identidade** do caminho
    (E4-AUD3-001): `ws\\outside.py`, um arquivo da raiz do repositório, virava
    `ws/outside.py` e era devolvido como `outside.py` de dentro do workspace `ws/`.
    """
    stripped = _strip_prefix_bytes(path.encode("utf-8"), prefix.encode("utf-8"))
    if stripped is None:
        return None
    return stripped.decode("utf-8")


# ------------------------------------------------------- E4: as duas leituras do contexto


#: Caracteres que o git nomeia sem problema e que o envelope de `source_ref` **não consegue
#: nomear de volta**. Hoje há exatamente um: `\`.
#:
#: **Derivado, não copiado** (E4-AUD3-002): é o mesmo `PATH_SEPARATOR_ALIASES` que
#: `safety.source_refs.normalize_source_ref` usa para traduzir `\` em `/`. Todo
#: `source_ref` que o usuário escreva com `\` vira um caminho com `/` — correto para
#: entrada de usuário no Windows, e **irreversível**: nenhum `source_ref` existente, nem
#: nenhum que possa vir a ser escrito, produz uma `\` literal para casar contra o caminho
#: que o git devolve. O arquivo existe, o git o vê, e o vocabulário do registro não tem uma
#: palavra para ele. Manter uma segunda constante aqui, copiada à mão, é exatamente o tipo
#: de acoplamento implícito que rodadas de auditoria anteriores desta fase já mostraram que
#: apodrece sozinho — um caractere novo entraria na tradução e este conjunto ficaria para
#: trás, silenciosamente, até a próxima auditoria achar o caso. Com a importação, os dois
#: não podem divergir: mudar um muda o outro.
_UNREPRESENTABLE_CHARS = PATH_SEPARATOR_ALIASES


@dataclass(frozen=True, slots=True)
class UnrepresentablePath:
    """Um caminho **de dentro do workspace** que o registro não consegue nomear de volta.

    Duas causas, um tratamento: uma barra invertida literal, que
    `normalize_source_ref` traduz e portanto nenhum `source_ref` consegue produzir
    (E4-AUD3-001); ou bytes que não são UTF-8 válido, que não têm forma textual nenhuma
    (E4-AUD4-002).

    ``raw`` são os bytes fiéis, relativos ao workspace — a **única** forma que sempre
    existe, e a única sobre a qual dá para decidir alguma coisa (por exemplo, se um
    `source_ref` literal poderia ou não alcançá-lo: prefixo de bytes é decidível mesmo
    quando o texto não é). ``display`` é derivado, com perda, só para o motivo chegar
    legível à resposta da API — nunca é comparado, casado ou usado como identidade.
    """

    raw: bytes
    display: str


@dataclass(frozen=True, slots=True)
class TreeListing:
    """A árvore de um commit: o que deu para ler **e** o que não deu, juntos.

    ## Por que os dois juntos, e não um ou outro (E4-AUD5-001)

    Até a 4ª rodada isto era um tipo-**soma**: a leitura devolvia ou a lista de arquivos, ou
    um `UnrepresentablePaths` que substituía a lista inteira. Um único nome ilegível em
    qualquer canto da árvore apagava todos os arquivos legíveis — e, com eles, a
    classificação de segredo que já tinha sido possível fazer sobre eles. Um `.env` real,
    resolvível, deixava de ser recusado porque um arquivo **sem relação nenhuma** tinha nome
    inválido no mesmo commit. O tipo-soma forçava a escolha errada: "não sei nada" quando na
    verdade se sabia quase tudo.

    Agora é um tipo-**produto**. ``files`` traz tudo o que foi lido, sempre; ``unrepresentable``
    diz o que ficou de fora. Quem chama tem os dois e decide com informação completa sobre a
    própria incompletude — que é diferente de não ter informação.
    """

    files: tuple[tuple[str, str], ...]
    unrepresentable: tuple[UnrepresentablePath, ...] = ()

    @property
    def partial(self) -> bool:
        """A leitura funcionou, mas não descreve a árvore inteira."""
        return bool(self.unrepresentable)


@dataclass(frozen=True, slots=True)
class WorkingTreeListing:
    """As divergências da árvore de trabalho, e os caminhos que não deu para nomear.

    O mesmo tipo-produto de `TreeListing`, pelo mesmo motivo (E4-AUD5-001): uma divergência
    **conhecida** num arquivo legível e coberto não pode ser apagada porque outro arquivo,
    sem relação, tem nome ilegível na mesma leitura.
    """

    divergences: tuple[WorkingTreeEntry, ...]
    unrepresentable: tuple[UnrepresentablePath, ...] = ()

    @property
    def partial(self) -> bool:
        return bool(self.unrepresentable)


def _is_representable(path: str) -> bool:
    """O envelope de `source_ref` consegue nomear este caminho? Ver `UnrepresentablePath`."""
    return not _UNREPRESENTABLE_CHARS.intersection(path)


def _collect_unrepresentable(
    bucket: list[UnrepresentablePath], relative_bytes: bytes, decoded: str | None
) -> None:
    """Registra um caminho não representável, preservando os bytes fiéis.

    ``decoded`` é o texto quando os bytes **são** UTF-8 válido (o caso da barra invertida
    literal) e `None` quando não são — aí o ``display`` sai de `_describe_undecodable`, com
    perda e só para leitura humana.
    """
    bucket.append(
        UnrepresentablePath(
            raw=relative_bytes,
            display=decoded if decoded is not None else _describe_undecodable(relative_bytes),
        )
    )


def _freeze_unrepresentable(
    bucket: list[UnrepresentablePath],
) -> tuple[UnrepresentablePath, ...]:
    """Ordena e remove duplicatas **pelos bytes**, que são a identidade real do caminho."""
    unique = {item.raw: item for item in bucket}
    return tuple(unique[raw] for raw in sorted(unique))


class WorkingTreeChange(str, Enum):
    """Tipos de divergência que [03] §3 Parte B exige detectar, **de forma exaustiva**."""

    MODIFIED = "modified"
    STAGED = "staged"
    DELETED = "deleted"
    RENAMED = "renamed"
    UNTRACKED = "untracked"


@dataclass(frozen=True, slots=True)
class WorkingTreeEntry:
    """Um caminho divergente e o tipo da divergência.

    ``path`` é relativo a `local_path`, com separador `/` — a mesma base e a mesma forma
    dos caminhos de `list_tree` e dos `source_refs` ([02] §2: "globs relativos ao
    workspace"). Caminhos de fora do workspace já foram **descartados** por `_strip_prefix`
    (nenhum `source_ref` poderia alcançá-los), e caminhos de dentro que o envelope não sabe
    nomear nunca chegam a virar uma entrada: eles vão para
    `WorkingTreeListing.unrepresentable`, **ao lado** das divergências legíveis
    (E4-AUD3-001, E4-AUD5-001).
    """

    path: str
    kind: WorkingTreeChange


def list_tree(local_path: str, commit: str) -> TreeListing | None:
    """`(path, blob_sha)` de todo blob de `commit`, **ordenado**. `None` se não deu para ler.

    Parte A de [03](../../../docs/architecture/03-context-architecture.md) §3: os
    `blob_sha` vêm do próprio git, então **nenhum arquivo é aberto** para calcular o
    `source_hash`.

    Decisões que o formato impõe:

    * **`-z` é obrigatório.** Sem ele o git aplica *C-quoting* em qualquer caminho
      não-ASCII, e o expansor de glob passaria a casar contra um caminho que não existe.
      Com `-z` os caminhos saem como bytes UTF-8 crus.
    * **`--full-tree`, e depois `_strip_prefix`** (E4-AUD2). Sem ele o git nomeia
      relativamente ao diretório corrente, o que por acaso já seria a base certa — mas era
      a **única** das três leituras a fazer isso, e essa assimetria foi a origem de quatro
      findings. Com `--full-tree` as três emitem a partir da raiz do repositório e passam
      pela mesma e única transformação. Custa listar a árvore inteira num monorepo, e o
      filtro é barato; a alternativa custava um falso `fresh` por rodada.
    * **`commit` precisa ser um SHA-1 completo.** Ele entra no `argv`; um valor como
      `--upload-pack=...` seria injeção de opção. `verification_commit` sempre é um SHA
      completo ([03] §3), então a restrição não custa nada e fecha a porta.
    * **Só `blob`.** `tree` não aparece sem `-t`; `commit` (submódulo) aparece e é
      descartado — um submódulo não é arquivo que o contexto possa cobrir.

    Qualquer registro que não casar com o **formato** faz a função devolver `None` inteira,
    em vez de devolver uma lista parcial que se apresenta como completa: aí o problema é a
    saída do git, e não dá para saber o que mais foi perdido.

    Um caminho **de dentro do workspace que o envelope de `source_ref` não sabe nomear** é
    outra coisa, e desde E4-AUD5-001 tem outro tratamento: ele sai de ``files`` e entra em
    ``unrepresentable``, e a função devolve **os dois** num `TreeListing`. A leitura se
    declara parcial em vez de se anular — quem chama sabe exatamente quais arquivos leu e
    quais não, e pode classificar os legíveis (inclusive recusar um segredo entre eles) sem
    depender da legibilidade de arquivos que não têm nada a ver com ele.
    """
    if not _SHA1_RE.fullmatch(commit):
        return None

    git = shutil.which("git")
    if git is None:
        return None

    prefix = _workspace_prefix(git, local_path)
    if prefix is None:
        return None
    prefix_bytes = prefix.encode("utf-8")

    result = _run_git(git, local_path, "ls-tree", "-r", "-z", "--full-tree", commit, "--")
    if result is None or result.returncode != 0:
        return None

    entries: list[tuple[str, str]] = []
    unrepresentable: list[UnrepresentablePath] = []
    seen: set[str] = set()
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        meta, separator, path = record.partition(b"\t")
        if not separator or not path:
            return None
        # Os metadados (`<mode> <type> <object>`) são sempre ASCII — é o git quem os gera,
        # nunca conteúdo de nome de arquivo. Uma falha de decodificação aqui é violação de
        # formato, não caminho ilegível, e recebe o mesmo tratamento de sempre: `None`
        # inteiro (E4-AUD4-002).
        meta_text = _decode_text(meta)
        if meta_text is None:
            return None
        fields = meta_text.split(" ")
        if len(fields) != _LS_TREE_FIELDS:
            return None
        object_type, blob_sha = fields[1], fields[2]
        if object_type != "blob":
            continue
        if not _SHA1_RE.fullmatch(blob_sha):
            return None
        relative_bytes = _strip_prefix_bytes(path, prefix_bytes)
        if relative_bytes is None:
            continue  # fora do workspace: nenhum `source_ref` poderia cobri-lo
        relative = _decode_text(relative_bytes)
        if relative is None:
            # De dentro do workspace e nem sequer UTF-8 válido (E4-AUD4-002) — não dá nem
            # para checar `_is_representable`, que precisa de texto. Os **bytes** ficam
            # guardados: é sobre eles que o expansor decide, depois, se algum `source_ref`
            # poderia ter alcançado este caminho (E4-AUD5-001).
            _collect_unrepresentable(unrepresentable, relative_bytes, None)
            continue
        if not _is_representable(relative):
            # De **dentro** do workspace e sem nome no vocabulário do registro. Não é
            # descartável como o de fora: ali a exclusão é correta por construção, aqui ela
            # esconderia um arquivo do `source_hash` sem avisar ninguém.
            _collect_unrepresentable(unrepresentable, relative_bytes, relative)
            continue
        if relative in seen:
            return None
        seen.add(relative)
        entries.append((relative, blob_sha))

    entries.sort()
    return TreeListing(
        files=tuple(entries), unrepresentable=_freeze_unrepresentable(unrepresentable)
    )


def working_tree_status(local_path: str) -> WorkingTreeListing | None:
    """Divergências contra o **`HEAD` corrente**, tipadas. `None` se não deu para ler.

    **Esta função não é a Parte B de [03] §3** (E4-AUD-003). `git status` compara sempre
    contra o `HEAD` do momento — não existe "status contra um commit antigo", e forçar o
    comando a responder isso não é possível por natureza. Quem responde a Parte B é
    `working_tree_diff_against`, que compara a árvore atual com o `verification_commit`
    congelado. Esta continua existindo porque "o que está sujo agora?" é a pergunta que a
    UI e o preflight fazem, e ela é legítima — só não é a que a verificação de contexto faz.

    `git status --porcelain=v2 -z --untracked-files=all`. O `v2` traz o
    par `XY` (índice, árvore) em campo próprio, que o `v1` só dá por posição; o `-z` evita
    o *C-quoting*; o `--untracked-files=all` lista arquivo por arquivo em vez de resumir um
    diretório novo numa linha só — resumir esconderia justamente o arquivo coberto.

    Classificação, a partir de `XY` (`.` = igual; `M`/`A`/`D`/`R`/`C`/`T`/`U` = mudou):

    | Registro | `kind` |
    | --- | --- |
    | `?` | `untracked` |
    | `2` (rename/copy) | `renamed` — **duas** entradas: o caminho novo e o original |
    | `1` com `D` em `X` ou `Y` | `deleted` |
    | `1` com `X` diferente de `.` | `staged` |
    | `1` restante (só `Y` mudou) | `modified` |
    | `u` (não mesclado) | `modified` |

    Duas entradas no rename porque um `source_ref` que cobria o caminho **original** foi
    igualmente invalidado: o arquivo não está mais lá. Reportar só o destino deixaria essa
    cobertura silenciosamente `fresh`.

    `u` (conflito de merge) cai em `modified` por não haver um sexto rótulo em [03] §3. O
    que importa para a regra de estado é que a divergência **apareça** — e ela aparece.

    Linhas `#` (cabeçalho) e `!` (ignorado) são descartadas. Qualquer registro fora do
    **formato** faz a função devolver `None`: uma lista parcial de divergências que se
    apresenta como completa é o caminho direto para um falso `fresh`, que é exatamente o
    defeito que AUD-004 corrigiu. Um caminho que o envelope de `source_ref` não sabe nomear
    não é isso — ele vai para ``unrepresentable`` **ao lado** das divergências legíveis
    (E4-AUD5-001), e a leitura se declara parcial em vez de se anular.
    """
    git = shutil.which("git")
    if git is None:
        return None

    prefix = _workspace_prefix(git, local_path)
    if prefix is None:
        return None
    prefix_bytes = prefix.encode("utf-8")

    result = _run_git(git, local_path, "status", "--porcelain=v2", "-z", "--untracked-files=all")
    if result is None or result.returncode != 0:
        return None

    # Separação de registros e campos em **bytes** (E4-AUD4-002), antes de qualquer
    # decodificação: `\0` (registro) e ` ` (campo) são bytes ASCII fixos que o git usa como
    # delimitador, e localizá-los não exige que o conteúdo entre eles seja UTF-8 válido. Só
    # o **último** campo de cada registro é caminho; os anteriores (`XY`, modos, hashes) são
    # sempre ASCII — o próprio git os gera — e uma falha em decodificá-los é violação de
    # formato, não nome de arquivo.
    records = result.stdout.split(b"\0")
    if records and records[-1] == b"":
        records.pop()

    entries: list[WorkingTreeEntry] = []
    unrepresentable: list[UnrepresentablePath] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue

        marker, _, rest = record.partition(b" ")

        if marker in (b"#", b"!"):
            # `#` é cabeçalho (`# branch.oid ...`); `!` só apareceria com `--ignored`.
            continue

        if marker == b"?":
            _add_entry(entries, unrepresentable, rest, WorkingTreeChange.UNTRACKED, prefix_bytes)
            continue

        if marker == b"1":
            fields = rest.split(b" ", _ORDINARY_FIELDS - 1)
            if len(fields) != _ORDINARY_FIELDS:
                return None
            xy = _decode_text(fields[0])
            if xy is None:
                return None
            _add_entry(entries, unrepresentable, fields[-1], _ordinary_kind(xy), prefix_bytes)
            continue

        if marker == b"2":
            fields = rest.split(b" ", _RENAMED_FIELDS - 1)
            if len(fields) != _RENAMED_FIELDS or index >= len(records):
                return None
            original = records[index]
            index += 1
            if not original:
                return None
            # Origem **e** destino: quem cobria o caminho original também foi invalidado.
            _add_entry(
                entries, unrepresentable, fields[-1], WorkingTreeChange.RENAMED, prefix_bytes
            )
            _add_entry(entries, unrepresentable, original, WorkingTreeChange.RENAMED, prefix_bytes)
            continue

        if marker == b"u":
            fields = rest.split(b" ", _UNMERGED_FIELDS - 1)
            if len(fields) != _UNMERGED_FIELDS:
                return None
            _add_entry(
                entries, unrepresentable, fields[-1], WorkingTreeChange.MODIFIED, prefix_bytes
            )
            continue

        return None  # marcador desconhecido: o formato mudou, e adivinhar seria pior

    entries.sort(key=lambda entry: (entry.path, entry.kind.value))
    return WorkingTreeListing(
        divergences=tuple(entries), unrepresentable=_freeze_unrepresentable(unrepresentable)
    )


def working_tree_diff_against(local_path: str, commit: str) -> WorkingTreeListing | None:
    """**A Parte B de [03] §3**: árvore de trabalho **atual** versus um commit **congelado**.

    `None` se não deu para ler. Caminhos relativos a `local_path`, ordenados.

    ## Por que esta função existe (E4-AUD-003)

    A Parte B pergunta *"algum caminho coberto diverge da base que a verificação
    enxerga?"*, e a base é o `verification_commit`. `git status` **não sabe responder
    isso**: ele compara sempre contra o `HEAD` do momento. Com o baseline em `A` e o
    repositório já em `B`, uma árvore limpa em relação a `B` fazia o `status` responder
    "zero divergências" — mesmo que a árvore inteira difira de `A`. A pergunta estava
    errada, não o parâmetro.

    **Três** leituras compõem a resposta, porque nenhuma sozinha a dá:

    1. `git diff --name-status -z <commit>` — a **árvore de trabalho** contra o commit
       congelado. Cobre modificado, apagado, acrescentado e renomeado, **incluindo o que
       foi commitado depois de `<commit>`**, que é o que faltava em E4-AUD-003.
    2. `git diff --cached --name-status -z <commit>` — o **índice** contra o mesmo commit
       (E4-AUD2-002). Sem ele, uma sequência perfeitamente comum some: dar `stage` no
       conteúdo B e depois reescrever A no arquivo deixa a árvore idêntica ao commit, e o
       primeiro `diff` sai **vazio** — embora o índice ainda contenha B, e um `commit` sem
       argumentos fosse gravar B. Divergência que existe e não aparece é falso `fresh`.
    3. `git status --porcelain=v2 -z --untracked-files=all`, só as linhas `?` — arquivos
       **não rastreados**. Nenhum dos dois `diff` os enxerga (não estão no índice), e "não
       rastreado" independe de commit: se o arquivo não está no índice hoje, ele não estava
       em `<commit>` tampouco.

    As três são combinadas por **união**, nunca por substituição: a entrada diverge se
    **qualquer** uma delas diverge. Perguntar só pelo resultado líquido — o que a árvore
    mostra agora — é o que faz um par de operações que se cancelam na superfície esconder
    um estado que ainda difere da base.

    ## Rótulos, relidos contra uma base congelada

    Os cinco rótulos de [03] §3 são mantidos, com o sentido que fazem contra um commit e
    não contra o índice:

    | `git diff` | `kind` | Significa |
    | --- | --- | --- |
    | `M`, `T` | `modified` | conteúdo (ou tipo) difere do commit congelado |
    | `D` | `deleted` | existia no commit congelado e não existe mais |
    | `A` | `staged` | existe agora, **não existia** no commit congelado |
    | `R`, `C` | `renamed` | origem **e** destino, duas entradas |
    | `U`, e qualquer outro | `modified` | divergente; o rótulo mais fraco nunca esconde |
    | `?` do `status` | `untracked` | presente na árvore, fora do índice |

    `A` recebe `staged` por ser o rótulo do conjunto que mais se aproxima de "está no
    índice e não está na base". Inventar um sexto rótulo contrariaria [03] §3; e a precisão
    do rótulo é informativa — o que decide o estado é a divergência **existir**.

    Como em `list_tree`, `commit` precisa ser um SHA-1 completo: ele entra no `argv`.

    A união vale também para o **motivo**: um caminho que o envelope de `source_ref` não
    sabe nomear, visto por qualquer uma das três leituras, entra em ``unrepresentable``
    (E4-AUD3-001) — mas **ao lado** das divergências legíveis, nunca no lugar delas
    (E4-AUD5-001). Omitir a divergência ilegível daria uma lista que se apresenta como
    completa e não é; apagar as legíveis por causa dela jogaria fora informação certa por
    causa de informação ausente.
    """
    if not _SHA1_RE.fullmatch(commit):
        return None

    git = shutil.which("git")
    if git is None:
        return None

    prefix = _workspace_prefix(git, local_path)
    if prefix is None:
        return None
    prefix_bytes = prefix.encode("utf-8")

    entries: list[WorkingTreeEntry] = []
    unrepresentable: list[UnrepresentablePath] = []

    # (1) árvore de trabalho contra o commit congelado, e (2) **índice** contra o mesmo
    # commit. A união dos dois, não a diferença líquida — ver o docstring.
    for cached in (False, True):
        parsed = _collect_diff_against(
            git, local_path, commit, prefix_bytes, unrepresentable, cached=cached
        )
        if parsed is None:
            return None
        entries.extend(parsed)

    # (3) não rastreados, que nenhum dos dois `diff` enxerga
    untracked = working_tree_status(local_path)
    if untracked is None:
        return None
    # As duas metades da terceira leitura entram: as divergências não rastreadas legíveis, e
    # os caminhos que ela não conseguiu nomear. Nenhuma anula a outra (E4-AUD5-001).
    entries.extend(
        entry for entry in untracked.divergences if entry.kind is WorkingTreeChange.UNTRACKED
    )
    unrepresentable.extend(untracked.unrepresentable)

    # Um mesmo caminho chega por mais de uma leitura o tempo todo (staged **e** modificado,
    # por exemplo). A lista é um conjunto de divergências, não um log.
    unique = sorted({(entry.path, entry.kind.value) for entry in entries})
    return WorkingTreeListing(
        divergences=tuple(
            WorkingTreeEntry(path=path, kind=WorkingTreeChange(kind)) for path, kind in unique
        ),
        unrepresentable=_freeze_unrepresentable(unrepresentable),
    )


def _collect_diff_against(
    git: str,
    local_path: str,
    commit: str,
    prefix: bytes,
    unrepresentable: list[UnrepresentablePath],
    *,
    cached: bool,
) -> list[WorkingTreeEntry] | None:
    """Uma passada de `git diff --name-status -z` contra `commit`. `None` em falha.

    ``cached=False`` compara a **árvore de trabalho** com o commit; ``cached=True`` compara
    o **índice**. `--no-relative` é explícito além do `-c diff.relative=false` global: são
    duas travas para a mesma coisa, e a que aparece na linha de comando é a que o leitor vê.
    """
    argv = ["diff", "--no-relative", "--name-status", "-z"]
    if cached:
        argv.append("--cached")
    argv.extend([commit, "--"])

    result = _run_git(git, local_path, *argv)
    if result is None or result.returncode != 0:
        return None

    # Separação em **bytes** (E4-AUD4-002): `\0` delimita registros, e o "campo de status"
    # (`M`, `D`, `R100`, ...) é sempre ASCII — só o caminho, no campo seguinte, pode conter
    # bytes que não decodificam.
    fields = result.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()

    entries: list[WorkingTreeEntry] = []
    index = 0
    while index < len(fields):
        status_bytes = fields[index]
        index += 1
        if not status_bytes:
            continue

        status = _decode_text(status_bytes)
        if status is None:
            return None  # campo de status não-ASCII: violação de formato, não caminho

        letter = status[0]
        if letter in ("R", "C"):
            # `R<score>\0<origem>\0<destino>`: consome dois caminhos.
            if index + 1 >= len(fields):
                return None
            origem, destino = fields[index], fields[index + 1]
            index += 2
            _add_entry(entries, unrepresentable, origem, WorkingTreeChange.RENAMED, prefix)
            _add_entry(entries, unrepresentable, destino, WorkingTreeChange.RENAMED, prefix)
            continue

        if index >= len(fields):
            return None
        path = fields[index]
        index += 1
        _add_entry(
            entries,
            unrepresentable,
            path,
            _DIFF_STATUS_KINDS.get(letter, WorkingTreeChange.MODIFIED),
            prefix,
        )

    return entries


#: Letras de `git diff --name-status` mapeadas nos cinco rótulos de [03] §3. O que não
#: estiver aqui cai em `modified`: o rótulo mais fraco ainda impede `fresh`, e perder a
#: divergência seria pior do que rotulá-la de forma conservadora.
_DIFF_STATUS_KINDS = {
    "M": WorkingTreeChange.MODIFIED,
    "T": WorkingTreeChange.MODIFIED,
    "D": WorkingTreeChange.DELETED,
    "A": WorkingTreeChange.STAGED,
    "U": WorkingTreeChange.MODIFIED,
}

#: `<mode> <type> <object>` — três campos, porque `list_tree` nunca passa `-l` (que
#: acrescentaria o tamanho do blob).
_LS_TREE_FIELDS = 3

#: Contagem de campos por tipo de registro do `--porcelain=v2` (`git-status(1)`), já
#: contando o caminho como o último campo. O `2` tem um campo a mais (`<X><score>`) e ainda
#: consome um segundo registro NUL — o caminho original.
_ORDINARY_FIELDS = 8  # XY sub mH mI mW hH hI path
_RENAMED_FIELDS = 9  # XY sub mH mI mW hH hI Xscore path
_UNMERGED_FIELDS = 10  # XY sub m1 m2 m3 mW h1 h2 h3 path

#: `XY` tem exatamente dois caracteres.
_XY_LENGTH = 2


def _add_entry(
    entries: list[WorkingTreeEntry],
    unrepresentable: list[UnrepresentablePath],
    path: bytes,
    kind: WorkingTreeChange,
    prefix: bytes,
) -> None:
    """Acrescenta a entrada **já reduzida à base do workspace**, ou descarta se for de fora.

    ``path`` chega em **bytes** (E4-AUD4-002): a redução ao prefixo do workspace acontece
    antes de qualquer decodificação (`_strip_prefix_bytes`), porque saber se um caminho é
    de dentro ou de fora não deveria depender de conseguir lê-lo como texto. Só depois de
    reduzido é que a decodificação é tentada — e só então barra invertida literal
    (E4-AUD3-001) ou bytes que não são UTF-8 válido (E4-AUD4-002) vão para
    ``unrepresentable`` em vez de virar uma entrada. Os dois destinos convivem no mesmo
    resultado (E4-AUD5-001): a divergência ilegível não apaga as legíveis.
    """
    relative_bytes = _strip_prefix_bytes(path, prefix)
    if relative_bytes is None:
        return
    relative = _decode_text(relative_bytes)
    if relative is None:
        _collect_unrepresentable(unrepresentable, relative_bytes, None)
        return
    if not _is_representable(relative):
        _collect_unrepresentable(unrepresentable, relative_bytes, relative)
        return
    entries.append(WorkingTreeEntry(path=relative, kind=kind))


def _ordinary_kind(xy: str) -> WorkingTreeChange:
    """`XY` de um registro `1`: `X` é o índice contra o `HEAD`, `Y` a árvore contra o índice."""
    if len(xy) != _XY_LENGTH:
        # Não classificável, mas o caminho **é** divergente: o rótulo mais fraco ainda
        # impede `fresh`, e é melhor do que descartar a linha.
        return WorkingTreeChange.MODIFIED
    staged, worktree = xy[0], xy[1]
    if "D" in (staged, worktree):
        return WorkingTreeChange.DELETED
    if staged != ".":
        return WorkingTreeChange.STAGED
    return WorkingTreeChange.MODIFIED
