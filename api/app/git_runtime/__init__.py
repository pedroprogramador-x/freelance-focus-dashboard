"""Git Runtime — adaptador de Git.

[01](../../../docs/architecture/01-v1-architecture.md) §2: `git_runtime/` é o adaptador de
Git e o ciclo de vida de worktree. Até aqui existem **três leituras**, todas só de leitura:

* `preflight` (E3) — dado um caminho absoluto, responde se é repositório, qual o `HEAD`, o
  branch e quantos arquivos divergem da árvore de trabalho.
* `probe_head` (E6-CONS4) — a parte do preflight que o planejamento consome, sem `status`,
  e distinguindo "não é repositório", "sem `HEAD`" e "git não pôde ser consultado".
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

* **Só leitura, aqui.** Nenhum subcomando deste arquivo altera o repositório do usuário —
  sem `commit`, `merge`, `push`, `rebase`, `reset`, `checkout`, `clean`, `init`, `apply`,
  `stash`. As **únicas** operações mutantes do pacote são `git worktree add --no-checkout` e
  `git read-tree` sem `-u`, e elas vivem em `worktree.py` (E7.4), sob `process_runtime` e com
  hooks desligados; o conteúdo da worktree vem dos blobs crus, nunca do checkout do Git —
  adendos E7.4 de [01] §2 e [04] §8. `test_architecture.py` transforma as regras em falha de
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

`git_runtime/` pode importar `safety`, `config` e stdlib ([01]) e, desde o adendo E7.4,
`process_runtime` — **só** para a operação mutante de `worktree.py`. O `subprocess` de
leitura fica confinado a este arquivo — o único ponto de chamada é `_run_git`.

A superfície de worktree (`task_worktree_names`, `repository_layout`, `list_worktrees`,
`inspect_task_worktree`, `classify_task_worktree`, `create_worktree`) é reexportada no fim
deste arquivo: `worktree.py` usa os auxiliares de leitura daqui, então a importação dele
vem depois de todos estarem definidos.
"""

from __future__ import annotations

import os
import re
import shutil
import stat

# git de LEITURA apenas; verbos mutantes proibidos (ver docstring + test_architecture.py).
import subprocess
import zlib
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

#: As únicas variáveis que `_git_env` **adiciona** ao ambiente filtrado.
_GIT_ENV_OVERRIDES = {
    "GIT_OPTIONAL_LOCKS": "0",  # nada de refresh/lock do índice num comando de leitura
    "GIT_TERMINAL_PROMPT": "0",  # nunca abre prompt de credencial
    # E7.4: num partial clone, objeto ausente é erro — nunca fetch preguiçoso, nunca rede.
    "GIT_NO_LAZY_FETCH": "1",
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


def _run_git(
    git: str, local_path: str, *args: str, stdin: bytes | None = None
) -> subprocess.CompletedProcess[bytes] | None:
    """Executa `git -c core.fsmonitor=false -C <local_path> <args>` sem shell, ambiente mínimo.

    Devolve `None` em qualquer falha. `stdout`/`stderr` saem como **bytes**, nunca texto
    (E4-AUD4-001, E4-AUD4-002 — ver a docstring do módulo): decidir texto aqui dentro do
    `subprocess` arrisca tradução de quebra de linha e decodificação numa thread que este
    `except` não alcança. A conversão para texto é sempre um passo explícito de quem chama,
    via `_decode_text`. ``stdin`` (bytes, E7.4) alimenta leituras em lote como o
    `cat-file --batch` — lista de objetos sem limite de linha de comando.
    """
    try:
        return subprocess.run(  # noqa: S603 — sem shell; argv literal; git resolvido por shutil.which
            [git, *_READONLY_GIT_OPTIONS, "-C", local_path, *args],
            capture_output=True,
            input=stdin,
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


#: Os desfechos que `probe_head` distingue (E6-CONS4, decisão D3). `preflight` colapsa
#: os três primeiros em `is_git_repo=False` — correto para a UI de Overview, errado para o
#: planejamento, que precisa dizer ao humano **qual** pré-condição falta: `git init` (fora do
#: backend), o primeiro commit, ou só tentar de novo quando o git voltar a responder.
PROBE_OK = "ok"
PROBE_NOT_A_REPO = "not_a_repo"
PROBE_WITHOUT_HEAD = "no_head"
PROBE_UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True, slots=True)
class HeadProbe:
    """Resultado de `probe_head`. ``head`` só é preenchido quando ``state == "ok"``.

    ``unverifiable`` é "o git não pôde ser consultado, ou respondeu de um jeito que não dá
    para confiar" — `git` ausente do `PATH`, `timeout`, falha de IO ao executar o processo,
    ou uma leitura que falhou sem que o disco confirme a explicação mais simples (ver
    `_has_git_marker`/`_has_confirmed_history`, E6-CONS5-001, E6-CONS5V-001). É diferente de
    ``not_a_repo`` (confirmado: nem o git nem o disco veem repositório algum) e de
    ``no_head`` (confirmado: repositório real, sem nenhum commit ainda). Um diretório
    removido cai em ``not_a_repo``:
    não há `.git` para encontrar, no git nem no disco, e a condição é persistente, não
    transitória.
    """

    state: str
    head: str | None = None
    branch: str | None = None


def _git_marker_at(path: str) -> str | None:
    """`_resolve_git_dir`, sem busca de ancestrais — só o `.git` de `path` em si.

    `None` quando não há `.git` (arquivo ou diretório) exatamente em `path`.
    """
    marker = os.path.join(path, ".git")
    if os.path.isdir(marker):
        return marker
    if not os.path.isfile(marker):
        return None
    try:
        with open(marker, encoding="utf-8", errors="strict") as handle:
            content = handle.read()
    except (OSError, UnicodeDecodeError):
        return marker  # existe, ilegível -- ainda assim confirma "há um .git"
    prefix = "gitdir:"
    line = content.strip()
    if not line.startswith(prefix):
        return marker
    target = line[len(prefix) :].strip()
    return target if os.path.isabs(target) else os.path.join(path, target)


def _resolve_git_dir(local_path: str) -> str | None:
    """Caminho real do diretório `.git` que o git **enxergaria** a partir de `local_path`,
    só no sistema de arquivos — nenhum comando `git`.

    Existe para os casos em que o git **falhou** ao responder (E6-CONS5-001): sem ele, a
    única informação disponível seria o retorno de um processo que não completou, e "não
    é repositório" e "é um repositório corrompido demais para o git nem confirmar isso" dão
    exatamente o mesmo retorno — mesmo código de saída, mesmo stdout vazio, e mesmo stderr
    quando o `.git/HEAD` tem conteúdo inválido (git recusa reconhecer o diretório como
    repositório de jeito nenhum, não só recusa resolver `HEAD`).

    **Busca ancestrais** (E6-CONS5V-001): um `DevWorkspace` pode ser um subdiretório de um
    repositório cuja raiz está mais acima — o próprio git já faz essa descoberta ao subir a
    árvore de diretórios a partir de `-C local_path`, e `_has_git_marker`/`_has_confirmed_history`
    precisam enxergar o mesmo `.git` que o git enxergaria, ou "não há `.git` em `local_path`"
    vira falso positivo de "não é repositório" para um workspace que o git reconhece — o
    diretório pai é que está corrompido, não a ausência de repositório. A busca é **limitada
    do mesmo jeito que a do git por padrão**: para na raiz do sistema de arquivos e nunca
    atravessa uma fronteira de dispositivo/montagem (o git só faz isso com
    `GIT_DISCOVERY_ACROSS_FILESYSTEM=true`, que `_git_env` nunca define) — não é uma busca
    sem limites, é a mesma que `rev-parse --is-inside-work-tree` já faria se pudesse
    responder. Segue o arquivo `gitdir:` de um worktree/submódulo por completude; não abre
    nem interpreta mais nada do conteúdo. `None` só quando nenhum ancestral, até a raiz ou a
    fronteira de dispositivo, tem `.git`.
    """
    current = os.path.abspath(local_path)
    try:
        boundary_dev: int | None = os.stat(current).st_dev
    except OSError:
        boundary_dev = None

    while True:
        found = _git_marker_at(current)
        if found is not None:
            return found
        parent = os.path.dirname(current)
        if parent == current:
            return None  # raiz do sistema de arquivos: fim da busca
        if boundary_dev is not None:
            try:
                parent_dev = os.stat(parent).st_dev
            except OSError:
                return None
            if parent_dev != boundary_dev:
                return None  # fronteira de dispositivo/montagem: git também pararia aqui
        current = parent


def _has_git_marker(local_path: str) -> bool:
    """`not_a_repo` (confirmado) vs `unverifiable` (git falhou, mas há um `.git` no disco).

    Não depende de texto de stderr do git, que é localizável e muda entre versões
    (E6-CONS5-001) — só de `.git` existir ou não em `local_path` ou num ancestral que o git
    alcançaria (E6-CONS5V-001, ver `_resolve_git_dir`).
    """
    return _resolve_git_dir(local_path) is not None


class _ObjectInspectionUnverifiable(Exception):
    """Uma falha de E/S impediu concluir a inspeção de histórico — nem "achei", nem "não
    achei" (E6-CONS5V4-001, E6-CONS5V4-002). Interna deste módulo: levantada por
    `_stat_or_raise`, `_common_git_dir` e `_has_commit_or_tag_object`, e capturada **só** em
    `_has_confirmed_history` — a única fronteira. Nunca atravessa até `probe_head`, e nunca é
    confundida com um `OSError` comum: **não** herda dele de propósito, para que um
    `except OSError:` mais externo (como os de `_has_commit_or_tag_object`) não a intercepte
    por engano — cada `OSError` real vira esta exceção exatamente uma vez, na fronteira mais
    próxima de onde ocorreu, e depois disso viaja como o tipo próprio até ser capturada.
    """


def _stat_or_raise(path: str) -> os.stat_result | None:
    """`os.stat(path)`, ou `None` quando `path` está **comprovadamente** ausente.

    A peça central da regra única de E/S desta família de correções (E6-CONS5V4-001):
    `os.path.isdir`/`os.path.isfile`/`os.path.exists` (as versões anteriores) engolem
    **todo** `OSError` — `FileNotFoundError` (confirmado: não existe) e `PermissionError`
    (não confirmado: pode existir, só não deu para ler) viram o mesmo `False`, e quem chama
    depois não tem como saber qual dos dois aconteceu. Aqui os dois se separam: ausência
    **confirmada** (`FileNotFoundError`, ou `NotADirectoryError` quando um componente do
    caminho não é diretório) devolve `None`; qualquer outra falha de E/S levanta
    `_ObjectInspectionUnverifiable` em vez de devolver um valor que pareça "ausente".
    """
    try:
        return os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise _ObjectInspectionUnverifiable(path) from exc


def _confirmed_dir_exists(path: str) -> bool:
    """`True` só quando `path` é comprovadamente um diretório; `False` só quando está
    comprovadamente ausente (ou existe como outra coisa que não diretório). Ver
    `_stat_or_raise` — qualquer falha de E/S ambígua levanta, nunca vira `False`.
    """
    result = _stat_or_raise(path)
    return result is not None and stat.S_ISDIR(result.st_mode)


def _common_git_dir(git_dir: str) -> str:
    """O Git dir **comum** de `git_dir` — segue `commondir` de um worktree vinculado.

    `objects/`, `refs/` e `config` são sempre compartilhados entre um repositório principal
    e seus worktrees vinculados — nunca "por worktree" (E6-CONS5V2-001). Só `HEAD`, `index`
    e `logs/HEAD` são específicos de cada worktree. Sem seguir `commondir`, um worktree
    vinculado cujo `git_dir` é `<principal>/.git/worktrees/<nome>` nunca vê os objetos do
    commit, porque eles vivem só em `<principal>/.git`.

    `commondir` **comprovadamente ausente** (`FileNotFoundError`/`NotADirectoryError`, nunca
    um `PermissionError` disfarçado — E6-CONS5V4-002) significa que `git_dir` já é o comum
    (repositório principal, ou sem nenhum worktree vinculado) — devolvido sem alteração. Uma
    falha de E/S que não confirma ausência levanta `_ObjectInspectionUnverifiable`: cair de
    volta no `git_dir` do próprio worktree silenciosamente, como a versão anterior fazia,
    perde o armazenamento comum sem avisar ninguém — o mesmo commit que existe no repositório
    principal deixa de ser visto. Conteúdo ilegível como UTF-8 continua melhor esforço (cai de
    volta no próprio `git_dir`) — fora do escopo desta correção, que é só sobre falha de E/S,
    não sobre formato de conteúdo.
    """
    commondir_file = os.path.join(git_dir, "commondir")
    try:
        with open(commondir_file, encoding="utf-8", errors="strict") as handle:
            content = handle.read()
    except (FileNotFoundError, NotADirectoryError):
        return git_dir
    except UnicodeDecodeError:
        return git_dir
    except OSError as exc:
        raise _ObjectInspectionUnverifiable(commondir_file) from exc
    target = content.strip()
    if not target:
        return git_dir
    return target if os.path.isabs(target) else os.path.normpath(os.path.join(git_dir, target))


#: Objetos soltos inspecionados por sondagem, no máximo (E6-CONS5V2-002). Um repositório
#: genuinamente sem commit tem poucos objetos — todos vieram de `add`/`stash` antes do
#: primeiro commit. Mais que isto sem decidir já é incomum o bastante para não valer a pena
#: continuar contando: a resposta seca é tratar como não confirmado (ver
#: `_has_commit_or_tag_object`), o mesmo que a presença de um pacote já faz.
_MAX_LOOSE_OBJECTS_SCANNED = 512


def _loose_object_type(path: str) -> str | None:
    """Tipo (`blob`, `tree`, `commit`, `tag`) de um objeto solto do git, sem `git`.

    Um objeto solto é `zlib(b"<tipo> <tamanho>\\0<conteúdo>")`. Descomprime só os primeiros
    bytes — nunca o conteúdo inteiro do objeto — para ler o cabeçalho. `None` para qualquer
    formato inesperado (arquivo ilegível, não é zlib válido, cabeçalho sem espaço, tipo não
    alfabético): o chamador trata isso como "não é evidência de nada", nunca como
    confirmação de tipo nenhum.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read(64)
    except OSError:
        return None
    try:
        header = zlib.decompressobj().decompress(raw, 32)
    except zlib.error:
        return None
    type_bytes = header.split(b" ", 1)[0]
    if not type_bytes or not type_bytes.isalpha():
        return None
    try:
        return type_bytes.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return None


#: Tipos de objeto solto que, sozinhos, **não** provam commit algum (E6-CONS5V2-002). Um
#: `git add` sem nenhum commit grava um objeto `blob` por arquivo staged; nada mais. Um
#: `tree` também não implica commit por si (é possível escrever uma árvore sem um commit em
#: cima, embora nenhum verbo deste módulo faça isso) — incluído aqui só por serem os dois
#: tipos que um estado "genuinamente sem commit" pode conter, nunca por uma lista do que
#: *conta*: qualquer tipo **fora** deste conjunto (inclusive um tipo que este módulo não
#: reconhece, ou uma leitura que falhou) é tratado como evidência, não o contrário — a
#: allowlist é dos tipos seguros, não dos que "provam" história, de propósito (ver
#: `_has_commit_or_tag_object`).
_LOOSE_OBJECT_TYPES_WITHOUT_HISTORY_EVIDENCE = frozenset(("blob", "tree"))


def _has_commit_or_tag_object(objects_dir: str) -> bool:
    """`objects_dir` tem algum objeto **além** de `blob`/`tree` — solto ou empacotado? Ou a
    inspeção não pôde ser concluída?

    E6-CONS5V2-002: contar "qualquer arquivo em objects/" como evidência de commit (a versão
    anterior) confundia "algo foi staged" com "algo foi commitado" — um `git add` sem commit
    grava só objetos que ficam em
    `_LOOSE_OBJECT_TYPES_WITHOUT_HISTORY_EVIDENCE`, nunca um objeto de commit de verdade.

    Só devolve `False` quando a inspeção **termina** sem achar nada fora da allowlist. `True`
    é evidência encontrada — pacote presente (`objects/pack/*`, sem abrir o formato: poderia
    conter um commit, e decodificá-lo para descartar essa possibilidade custaria muito mais
    do que esta sondagem paga), mais de `_MAX_LOOSE_OBJECTS_SCANNED` objetos soltos sem achar
    nenhum fora da allowlist (não terminar de olhar não é o mesmo que confirmar que não há
    nenhum), ou um objeto de tipo fora da allowlist de verdade. Qualquer falha de E/S — no
    `os.stat` do diretório de pacotes, no `os.scandir` do diretório principal ou de um
    subdiretório, ou levantada no meio da iteração de qualquer um dos dois — levanta
    `_ObjectInspectionUnverifiable` em vez de `True`/`False` (E6-CONS5V3-001,
    E6-CONS5V4-001): o objeto que provaria história pode estar exatamente no pedaço que não
    deu para ler, e "não terminei" não pode virar nem "achei" nem "não achei". Nenhuma
    exceção **comum** (`OSError`) atravessa esta função — todas viram
    `_ObjectInspectionUnverifiable` na fronteira mais próxima de onde ocorrem; quem captura é
    só `_has_confirmed_history`.
    """
    pack_dir = os.path.join(objects_dir, "pack")
    if _confirmed_dir_exists(pack_dir):
        try:
            with os.scandir(pack_dir) as pack_entries:
                if any(True for _entry in pack_entries):
                    return True
        except OSError as exc:
            raise _ObjectInspectionUnverifiable(pack_dir) from exc

    scanned = 0
    try:
        with os.scandir(objects_dir) as fanout_entries:
            for fanout in fanout_entries:
                if not fanout.is_dir() or len(fanout.name) != 2 or fanout.name in ("info", "pack"):
                    continue
                try:
                    with os.scandir(fanout.path) as loose_entries:
                        for loose in loose_entries:
                            if not loose.is_file():
                                continue
                            scanned += 1
                            if scanned > _MAX_LOOSE_OBJECTS_SCANNED:
                                return True
                            object_type = _loose_object_type(loose.path)
                            if object_type not in _LOOSE_OBJECT_TYPES_WITHOUT_HISTORY_EVIDENCE:
                                return True
                except OSError as exc:
                    raise _ObjectInspectionUnverifiable(fanout.path) from exc
    except OSError as exc:
        raise _ObjectInspectionUnverifiable(objects_dir) from exc
    return False


def _has_object_evidence(git_dir: str) -> bool:
    """Existe evidência de **commit** no armazenamento comum de `git_dir`?

    Segue `commondir` até o repositório principal antes de olhar `objects/`
    (E6-CONS5V2-001), e só conta objetos de tipo `commit`/`tag`, não qualquer arquivo
    (E6-CONS5V2-002). A checagem de existência de `objects/` usa `_confirmed_dir_exists`
    (E6-CONS5V4-001), não `os.path.isdir` — a mesma regra de `_common_git_dir` e
    `_has_commit_or_tag_object`, propagada por `_ObjectInspectionUnverifiable` quando a
    inspeção não pode ser concluída com confiança.
    """
    objects_dir = os.path.join(_common_git_dir(git_dir), "objects")
    if not _confirmed_dir_exists(objects_dir):
        return False
    return _has_commit_or_tag_object(objects_dir)


def _has_confirmed_history(local_path: str) -> bool:
    """`no_head` (legitimamente sem commit) vs `unverifiable` (histórico existiu, quebrou, ou
    não deu para confirmar).

    Dois sinais independentes, os dois só de sistema de arquivos, nunca de texto de stderr
    do git (E6-CONS5-001):

    * `.git/logs/HEAD` — criado na primeira atualização de uma referência (primeiro commit,
      checkout, merge). Específico de cada worktree — nunca precisa seguir `commondir`: é a
      pergunta "este HEAD, o de `local_path`, já se moveu alguma vez", não "existe commit em
      algum lugar do repositório".
    * `_has_object_evidence` — objeto de commit/tag em `.git/objects` (E6-CONS5V-001,
      E6-CONS5V2-001/002): um commit feito com `core.logAllRefUpdates=false` não deixa
      `logs/HEAD`, mas deixa os objetos do commit mesmo assim, no armazenamento comum —
      inclusive quando `local_path` é um worktree vinculado. Um `git add` sem commit também
      grava objeto, mas nenhum de tipo `commit`/`tag` — não conta.

    A presença de **qualquer um** dos dois confirma que o repositório não é "genuinamente
    recém-inicializado" — a resposta correta é `unverifiable`, não `no_head`. Só a ausência
    **comprovada** dos dois confirma "nunca teve um commit".

    **Fronteira única** (E6-CONS5V4-001, E6-CONS5V4-002): a checagem de `logs/HEAD` usa
    `_stat_or_raise` (não `os.path.exists`), e o restante da inspeção —
    `_has_object_evidence`, `_common_git_dir`, `_has_commit_or_tag_object` — propaga
    `_ObjectInspectionUnverifiable` sempre que uma falha de E/S impede confirmar ausência.
    Esta função é a **única** fronteira que a captura, convertendo em `True` — a mesma
    resposta de "achei evidência": os dois significam "não posso dizer `no_head`". A exceção
    nunca atravessa até `probe_head`.
    """
    git_dir = _resolve_git_dir(local_path)
    if git_dir is None:
        return False
    try:
        if _stat_or_raise(os.path.join(git_dir, "logs", "HEAD")) is not None:
            return True
        return _has_object_evidence(git_dir)
    except _ObjectInspectionUnverifiable:
        return True


def probe_head(local_path: str, *, resolve_branch: bool = True) -> HeadProbe:
    """Sondagem **leve** do `HEAD`: 2 leituras (3 com o branch), sem `status`. Nunca lança.

    É a leitura que o Planner usa para congelar `planning_base_commit` e a mesma que a
    elegibilidade de planejamento usa para prevê-lo — uma só implementação, para que as
    duas perguntas não divirjam. Os mesmos verbos e o mesmo critério de SHA do `preflight`,
    com o mesmo timeout fixo por leitura.

    E6-CONS5-001: um retorno não-zero do git, sozinho, não confirma "não é repositório" nem
    "sem HEAD" — ele também é o que sai quando `.git/config` está corrompido, ou quando
    `.git/HEAD` tem conteúdo que o git não reconhece como ref nem como SHA. Cada uma das
    duas categorias confirmadas exige um segundo sinal, puramente de disco, que não muda
    com a mensagem de erro do git nem com o idioma do sistema.
    """
    git = shutil.which("git")
    if git is None:
        return HeadProbe(PROBE_UNVERIFIABLE)

    inside = _run_git(git, local_path, "rev-parse", "--is-inside-work-tree")
    if inside is None:
        return HeadProbe(PROBE_UNVERIFIABLE)
    if inside.returncode != 0:
        # O git falhou ao rodar. "Não é repositório" e "há um .git, mas está corrompido
        # demais pro git nem confirmar isso" dão o mesmo retorno aqui — só a existência de
        # `.git` no disco separa os dois.
        return HeadProbe(PROBE_UNVERIFIABLE if _has_git_marker(local_path) else PROBE_NOT_A_REPO)
    inside_value = _stdout_if_ok(inside)
    if inside_value is None:
        # rc == 0 mas a saída não pôde ser lida (decodificação falhou) -- o processo
        # terminou bem, então isto não é "não é repositório": é uma leitura que não deu
        # para confiar.
        return HeadProbe(PROBE_UNVERIFIABLE)
    if inside_value != "true":
        # rc == 0 e o git respondeu com sucesso algo diferente de "true" (por exemplo
        # "false", num repositório bare) -- confirmado, sem ambiguidade nenhuma.
        return HeadProbe(PROBE_NOT_A_REPO)

    head_result = _run_git(git, local_path, "rev-parse", "HEAD")
    if head_result is None:
        return HeadProbe(PROBE_UNVERIFIABLE)
    if head_result.returncode != 0:
        # Confirmado que é uma árvore de trabalho (bloco acima); `HEAD` não resolveu. Um
        # repositório recém-criado, sem primeiro commit, dá exatamente essa falha -- e é o
        # único caso em que `no_head` é a resposta certa. Uma referência que existiu e foi
        # corrompida ou apagada dá a MESMA falha, mas não é "legitimamente sem commit"
        # (E6-CONS5-001, E6-CONS5V-001): reflog ausente sozinho não prova isso -- um commit
        # com reflog desligado também não deixa `logs/HEAD`, mas deixa os objetos do commit.
        return HeadProbe(
            PROBE_UNVERIFIABLE if _has_confirmed_history(local_path) else PROBE_WITHOUT_HEAD
        )
    head = _stdout_if_ok(head_result)
    if head is None or not _SHA1_RE.fullmatch(head):
        # rc == 0 mas a saída não é um SHA-1 -- git respondeu com sucesso algo que não
        # esperávamos. Não é "confirmadamente sem commit": é uma leitura ambígua.
        return HeadProbe(PROBE_UNVERIFIABLE)

    branch = (
        _stdout_if_ok(_run_git(git, local_path, "symbolic-ref", "--quiet", "--short", "HEAD"))
        if resolve_branch
        else None
    )
    return HeadProbe(PROBE_OK, head=head, branch=branch)


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


# ------------------------------------------------ E7.4: worktree de task (reexportação)
#
# No fim de propósito: `worktree.py` importa `_run_git`, `_git_env`, `_decode_text`,
# `_has_git_marker` e `_workspace_prefix` deste módulo, que já existem neste ponto.
from app.git_runtime.worktree import (  # noqa: E402
    InvalidWorktreeRequest,
    RepositoryLayout,
    TaskWorktreeFacts,
    TaskWorktreeNames,
    WorktreeInventory,
    WorktreeOutcome,
    WorktreeRecord,
    WorktreeVerdict,
    classify_task_worktree,
    create_worktree,
    inspect_task_worktree,
    list_worktrees,
    repository_layout,
    task_worktree_names,
)

__all__ = [
    "PROBE_NOT_A_REPO",
    "PROBE_OK",
    "PROBE_UNVERIFIABLE",
    "PROBE_WITHOUT_HEAD",
    "GitPreflight",
    "HeadProbe",
    "InvalidWorktreeRequest",
    "RepositoryLayout",
    "TaskWorktreeFacts",
    "TaskWorktreeNames",
    "TreeListing",
    "UnrepresentablePath",
    "WorkingTreeChange",
    "WorkingTreeEntry",
    "WorkingTreeListing",
    "WorktreeInventory",
    "WorktreeOutcome",
    "WorktreeRecord",
    "WorktreeVerdict",
    "classify_task_worktree",
    "create_worktree",
    "inspect_task_worktree",
    "list_tree",
    "list_worktrees",
    "preflight",
    "probe_head",
    "repository_layout",
    "task_worktree_names",
    "working_tree_diff_against",
    "working_tree_status",
]
