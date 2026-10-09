"""Teste de arquitetura: as arestas de importação proibidas.

[01](../../docs/architecture/01-v1-architecture.md) §3 lista dependências que não podem
existir. Aqui elas viram falha de suíte, e não item de checklist de revisão.

Usa só `ast` da biblioteca padrão — trazer uma ferramenta de contratos de importação para
verificar meia dúzia de regras seria desproporcional.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

#: Nada disto pode ser importado em lugar nenhum do backend na E2 — nem em módulo, nem em
#: teste. Providers e agentes chegam a partir da E8.
FORBIDDEN_EVERYWHERE = (
    "anthropic",
    "openai",
    "claude",
    "codex",
    "ruflo",
    "mcp",
    "langchain",
    "llama_index",
    "chromadb",
    "faiss",
    "celery",
    "redis",
)

#: `multiprocessing` e `pty` são superfície de execução de processo e pertencem ao Full
#: Safety Runtime (E7) e ao Test Runner, nenhum dos dois existindo ainda.
FORBIDDEN_PROCESS = ("multiprocessing", "pty")

#: `subprocess` é liberado **exclusivamente** em:
#:
#: * `git_runtime/` — para as leituras (E3+, [01] contrato de `git_runtime/`, [07] gate E3); a
#:   operação mutante de worktree da E7.4 não usa `subprocess` direto, e sim o
#:   `process_runtime` (adendo E7.4; ver `test_worktree_py_so_cria_processo_pelo_supervisor`);
#: * `process_runtime/` — o Supervisor de Processos da E7.3 (adendo E7.3 a [01] §2, D3).
#:
#: Em `orchestrator`, `agent_runtime`, `tool_executor`, `safety`, `api`, `db` e em qualquer
#: outro módulo continua proibido.
SUBPROCESS_ALLOWED_UNDER = (APP_ROOT / "git_runtime", APP_ROOT / "process_runtime")


def _python_files() -> list[Path]:
    return sorted(APP_ROOT.rglob("*.py"))


def _module_name(path: Path) -> str:
    relative = path.relative_to(APP_ROOT.parent).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


ALL_FILES = _python_files()


def test_existem_modulos_para_analisar() -> None:
    assert ALL_FILES, "nenhum módulo encontrado — o teste estaria passando à toa"


#: E8.2: o SDK da Claude Messages API (e o cliente HTTP dele) só pode ser importado pelo
#: adaptador concreto. Nenhum outro módulo — nem o composition root — o importa direto.
_ADAPTADOR_MESSAGES_API = APP_ROOT / "agent_runtime" / "adapters" / "anthropic_messages"
_SDK_DO_ADAPTADOR = frozenset({"anthropic", "httpx2", "httpcore2"})


@pytest.mark.parametrize("path", ALL_FILES, ids=_module_name)
def test_nenhum_provider_ou_agente_e_importado(path: Path) -> None:
    for imported in _imports(path):
        root = imported.split(".")[0].lower()
        if root in _SDK_DO_ADAPTADOR and path.is_relative_to(_ADAPTADOR_MESSAGES_API):
            continue
        assert root not in FORBIDDEN_EVERYWHERE and root not in _SDK_DO_ADAPTADOR, (
            f"{_module_name(path)} importa `{imported}`: providers só no adaptador concreto"
        )


@pytest.mark.parametrize("path", ALL_FILES, ids=_module_name)
def test_nenhuma_execucao_de_processo(path: Path) -> None:
    for imported in _imports(path):
        root = imported.split(".")[0].lower()
        assert root not in FORBIDDEN_PROCESS, (
            f"{_module_name(path)} importa `{imported}`: execução de processo é E7"
        )
        if root == "subprocess":
            assert any(path.is_relative_to(allowed) for allowed in SUBPROCESS_ALLOWED_UNDER), (
                f"{_module_name(path)} importa `subprocess` fora de git_runtime/ e "
                "process_runtime/: execução de processo só nesses dois pacotes"
            )


def test_git_runtime_e_somente_leitura() -> None:
    """[01]/[04] §8: `git_runtime/` nunca **constrói** verbo que altere o repositório.

    A única exceção é `worktree add` (adendo E7.4), travada à parte em
    `test_worktree_add_e_o_unico_verbo_mutante`. `reset --hard` continua proibido como comando
    direto — o `reset` que o próprio `git worktree add` executa dentro da worktree nova é
    detalhe interno do Git (adendo E7.4 de [04] §8, item 2).
    """
    mutating_verbs = (
        "commit",
        "merge",
        "push",
        "rebase",
        "reset",
        "--hard",
        "checkout",
        "clean",
        "-fdx",
        "init",
        "apply",
        "stash",
        "cherry-pick",
        "restore",
        "switch",
        "tag",
        "fetch",
        "pull",
        "gc",
        "prune",
        "repair",
        "remove",
        "move",
        "lock",
        "unlock",
        "update-ref",
        "filter-branch",
        "set-url",
        "--global",
        "--system",
    )
    for path in (APP_ROOT / "git_runtime").rglob("*.py"):
        if path == _MEDIATED_MODULE:
            continue  # estrutural, abaixo: `commit` lá é **tipo de objeto** do `cat-file`
        source = path.read_text(encoding="utf-8")
        for verb in mutating_verbs:
            assert f'"{verb}"' not in source, (
                f"git_runtime/{path.name} usa o verbo git `{verb}`: o adaptador é só leitura"
            )
    constantes = _str_constants_of(_MEDIATED_MODULE)
    for verb in mutating_verbs:
        if verb == "commit":
            # Só como tipo de objeto, dentro de `read_commit` (`cat-file commit <oid>`).
            assert _enclosing_functions(_MEDIATED_MODULE, "commit") == ["read_commit"]
            continue
        assert verb not in constantes, f"mediated.py constrói `{verb}`"


_WORKTREE_MODULE = APP_ROOT / "git_runtime" / "worktree.py"
#: E7.5-D: as quatro leituras Git do Developer, sob o Supervisor (adendo E7.5-D).
_MEDIATED_MODULE = APP_ROOT / "git_runtime" / "mediated.py"
_SUPERVISED_GIT_MODULES = (_WORKTREE_MODULE, _MEDIATED_MODULE)

#: P2-001/P2-002: nada no módulo de worktree passa conteúdo pela conversão de working tree
#: do Git nem consulta atributos. Conferido por **constante no AST** (não por texto: a
#: docstring nomeia esses comandos justamente para dizer que não são usados).
_MATERIALIZADORES_E_ATRIBUTOS_PROIBIDOS = (
    "checkout",
    "checkout-index",
    "switch",
    "restore",
    "reset",
    "--hard",
    "-u",
    "--update",
    "archive",
    "status",
    "check-attr",
    "--attr-source",
    "--filters",
    "--textconv",
    "update-index",
    "--refresh",
    "diff",
)


def _enclosing_functions(path: Path, value: str) -> list[str]:
    """Nome da função que envolve cada constante `value` no módulo (ou `<module>`)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []

    def visit(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, ast.FunctionDef) else owner
            if isinstance(child, ast.Constant) and child.value == value:
                found.append(owner)
            visit(child, name)

    visit(tree, "<module>")
    return found


def test_worktree_add_e_read_tree_sao_os_unicos_verbos_mutantes() -> None:
    """Adendo E7.4: `worktree add` só em `_add_argv`, `read-tree` só em `_read_tree_argv`,
    uma vez cada, e só em `git_runtime/worktree.py`."""
    for path in (APP_ROOT / "git_runtime").rglob("*.py"):
        for verb, owner in (("add", "_add_argv"), ("read-tree", "_read_tree_argv")):
            owners = _enclosing_functions(path, verb)
            if path == _WORKTREE_MODULE:
                assert owners == [owner], f"`{verb}` fora de `{owner}`: {owners}"
            else:
                assert owners == [], f"git_runtime/{path.name} constrói `{verb}`"


def test_worktree_nao_materializa_pelo_git_nem_consulta_atributos() -> None:
    for literal in _MATERIALIZADORES_E_ATRIBUTOS_PROIBIDOS:
        assert _enclosing_functions(_WORKTREE_MODULE, literal) == [], (
            f"worktree.py constrói `{literal}`: materialização é raw (P2-001/P2-002)"
        )


def test_diff_index_so_compara_arvore_e_indice() -> None:
    """Reauditoria P2-002: `diff-index` só em `_index_diff_argv`, e só na forma que não lê o
    working tree nem gera conteúdo de diff (`--cached --raw`, sem textconv/externo/rename)."""
    from app.git_runtime import worktree

    for path in (APP_ROOT / "git_runtime").rglob("*.py"):
        owners = _enclosing_functions(path, "diff-index")
        assert owners == (["_index_diff_argv"] if path == _WORKTREE_MODULE else []), owners
    argv = worktree._index_diff_argv("a" * 40)
    assert argv[argv.index("diff-index") + 1 :] == (
        "--cached",
        "--ita-invisible-in-index",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
        "--ignore-submodules=none",
        "--raw",
        "-z",
        "--exit-code",
        "a" * 40 + "^{commit}",
        "--",
    )
    assert "--no-lazy-fetch" in argv


def test_passos_mutantes_sao_add_sem_checkout_e_read_tree_sem_u() -> None:
    """D2/P2: argv endurecido, `--no-checkout`, `read-tree` sem `-u`, start-point `^{commit}`."""
    from app.git_runtime import worktree

    names = worktree.task_worktree_names("abcdef12-3456-4789-8abc-def012345678")
    add = worktree._add_argv("/usr/bin/git", "/repo", names, "/wt/x", "a" * 40)
    read_tree = worktree._read_tree_argv("/usr/bin/git", "/wt/x", "a" * 40)
    for argv in (add, read_tree):
        pares = {argv[i + 1] for i, item in enumerate(argv[:-1]) if item == "-c"}
        assert {
            "core.hooksPath=/dev/null",
            "core.fsmonitor=false",
            "submodule.recurse=false",
            "branch.autoSetupMerge=false",
            "worktree.useRelativePaths=false",
            "core.sparseCheckout=false",
            "core.sparseCheckoutCone=false",
        } <= pares
        assert "--no-pager" in argv and "--no-lazy-fetch" in argv
        assert not any(item.startswith("filter.") for item in argv)
        assert "-u" not in argv
        assert argv[-1] == "a" * 40 + "^{commit}"
    assert "--no-checkout" in add
    assert add[-3:-1] == ("--", "/wt/x")
    assert read_tree[-3:] == ("read-tree", "--no-sparse-checkout", "a" * 40 + "^{commit}")


def test_worktree_py_so_cria_processo_pelo_supervisor() -> None:
    """D6: os passos mutantes passam por `process_runtime.run_supervised`, nunca `subprocess`
    direto; e `run_supervised` só é chamado dentro de `_run_git_step`."""
    for call in _calls(_WORKTREE_MODULE):
        dotted = _dotted(call.func)
        assert (
            dotted is None
            or dotted[0] not in {"subprocess", "os"}
            or dotted[1]
            not in {
                "run",
                "Popen",
                "call",
                "check_call",
                "check_output",
                "system",
                "popen",
            }
        ), f"worktree.py cria processo por {dotted}"
    assert _enclosing_functions_calls(_WORKTREE_MODULE, "run_supervised") == ["_run_git_step"]


def test_git_runtime_nao_escreve_arquivo_por_conta_propria() -> None:
    """D7 + P2: toda escrita de conteúdo passa pelo `TreeWriter` do `path_runtime` (injetado).
    O `git_runtime` só lê: nenhum `os.mkdir`/`os.write`/`open(..., "w")`/`write_bytes`."""
    proibidas = {("os", "mkdir"), ("os", "makedirs"), ("os", "write"), ("os", "replace")}
    for path in (APP_ROOT / "git_runtime").rglob("*.py"):
        for call in _calls(path):
            dotted = _dotted(call.func)
            assert dotted not in proibidas, f"git_runtime/{path.name} chama {dotted}"
            func = call.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name not in {"write_bytes", "write_text", "mkdir"}, (
                f"git_runtime/{path.name} escreve com `{name}`"
            )
            if isinstance(func, ast.Name) and func.id == "open" and len(call.args) > 1:
                mode = call.args[1]
                assert isinstance(mode, ast.Constant) and set(str(mode.value)) <= {"r", "b"}, (
                    f"git_runtime/{path.name} abre arquivo para escrita"
                )
        source = path.read_text(encoding="utf-8")
        for flag in ("O_CREAT", "O_WRONLY", "O_RDWR", "O_APPEND", "O_TRUNC"):
            assert flag not in source, f"git_runtime/{path.name} usa `os.{flag}`"


def test_tree_writer_so_existe_no_path_runtime() -> None:
    definicoes = [
        _module_name(path)
        for path in ALL_FILES
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef) and node.name == "CheckedTreeWriter"
    ]
    assert definicoes == ["app.path_runtime"]


def _enclosing_functions_calls(path: Path, func_name: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []

    def visit(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, ast.FunctionDef) else owner
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == func_name
            ):
                found.append(owner)
            visit(child, name)

    visit(tree, "<module>")
    return found


def test_git_runtime_so_importa_safety_e_supervisor_no_modulo_de_worktree() -> None:
    """[01] §2 + adendos E7.4/E7.5-D: `safety` (e o próprio pacote); `process_runtime` só em
    `worktree.py` e `mediated.py`. Nunca `db`, `orchestrator`, `agent_runtime`,
    `tool_executor`, `path_runtime`, `context_engine`, `api`."""
    for path in (APP_ROOT / "git_runtime").rglob("*.py"):
        for imported in _imports(path):
            if not imported.startswith("app."):
                continue
            permitido: tuple[str, ...] = ("app.safety", "app.git_runtime")
            if path in _SUPERVISED_GIT_MODULES:
                permitido += ("app.process_runtime",)
            assert imported.startswith(permitido), f"git_runtime/{path.name} importa `{imported}`"


def test_worktree_root_so_e_construida_pelo_path_runtime() -> None:
    """D7: a `WorktreeRoot` validada nasce num único ponto, depois da decisão da `safety`."""
    construtores = []
    for path in ALL_FILES:
        for call in _calls(path):
            if isinstance(call.func, ast.Name) and call.func.id == "WorktreeRoot":
                construtores.append(_module_name(path))
    assert construtores == ["app.path_runtime"]


def test_safety_e_puro() -> None:
    """`safety/` recebe fatos e decide. Não faz IO, não abre banco, não conhece HTTP."""
    proibidos = {
        "fastapi",
        "starlette",
        "sqlalchemy",
        "alembic",
        "pydantic_settings",
        "app.path_runtime",
        "app.db",
        "app.api",
        "app.main",
        "app.config",
        "pathlib",
        "os",
        "io",
        "shutil",
        "socket",
        "requests",
        "httpx",
    }

    for path in (APP_ROOT / "safety").rglob("*.py"):
        for imported in _imports(path):
            root = imported.split(".")[0]
            assert imported not in proibidos and root not in proibidos, (
                f"safety/{path.name} importa `{imported}`: o kernel precisa continuar puro"
            )


def test_safety_nao_importa_path_runtime() -> None:
    """A dependência corre só na direção `path_runtime → safety` ([04] §4)."""
    for path in (APP_ROOT / "safety").rglob("*.py"):
        assert not any("path_runtime" in imported for imported in _imports(path))


def test_path_runtime_nao_importa_camadas_superiores() -> None:
    proibidos = {"app.db", "app.api", "app.main", "fastapi", "sqlalchemy", "alembic"}

    for imported in _imports(APP_ROOT / "path_runtime.py"):
        assert imported not in proibidos, f"path_runtime importa `{imported}`"


def test_db_nao_importa_camadas_superiores() -> None:
    proibidos = {"app.api", "app.main", "app.path_runtime", "fastapi"}

    for path in (APP_ROOT / "db").rglob("*.py"):
        for imported in _imports(path):
            assert imported not in proibidos, f"db/{path.name} importa `{imported}`"


def test_api_nao_importa_infraestrutura_de_execucao() -> None:
    """Uma rota nunca executa processo nem git diretamente ([01] §3)."""
    proibidos = {"app.agent_runtime", "app.tool_executor", "app.git_runtime", "app.path_runtime"}

    for path in (APP_ROOT / "api").rglob("*.py"):
        for imported in _imports(path):
            assert imported not in proibidos, f"api/{path.name} importa `{imported}`"


def test_context_engine_nao_importa_camadas_superiores() -> None:
    """Regra de módulo da E4: `context_engine/` lê contexto, não orquestra nem serve HTTP.

    Pode importar `db`, `git_runtime` (leitura), `path_runtime`, `safety` e `config`.
    Qualquer um dos quatro abaixo o transformaria em orquestrador disfarçado.
    """
    proibidos = {
        "app.agent_runtime",
        "app.tool_executor",
        "app.orchestrator",
        "app.api",
        "app.main",
        "fastapi",
        "starlette",
    }

    for path in (APP_ROOT / "context_engine").rglob("*.py"):
        for imported in _imports(path):
            root = imported.split(".")[0]
            assert imported not in proibidos and root not in proibidos, (
                f"context_engine/{path.name} importa `{imported}`"
            )


#: Literais que só aparecem em código que **interpreta** metacaractere de glob.
_GLOB_METACHAR_LITERALS = frozenset({"*", "**", "?", "[", "[!", "*/", "/**", "**/"})

#: Nomes de `re` que efetivamente casam alguma coisa (`sub`/`escape` não casam).
_RE_MATCHERS = frozenset({"compile", "match", "fullmatch", "search", "finditer", "findall"})

#: Métodos de `str` que **transformam** uma string caractere a caractere ou trecho a
#: trecho. É por um destes que uma tradução glob→regex alternativa passa, qualquer que seja
#: a forma escolhida: `.replace` encadeado, `str.translate` com tabela, `re.sub`, ou uma
#: junção de partes mapeadas. A lista é de *mecanismos*, não de padrões de código
#: conhecidos — foi a lista fechada de padrões que E4-AUD2-005 furou.
_STRING_TRANSFORMS = frozenset({"replace", "translate", "maketrans", "join", "format", "sub"})


def _string_constants(node: ast.AST) -> set[str]:
    """Todo literal de string dentro da subárvore."""
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def _transform_calls(tree: ast.AST) -> list[tuple[str, ast.Call]]:
    """`(nome do método, chamada)` para toda transformação de string, em qualquer receptor."""
    return [
        (node.func.attr, node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _STRING_TRANSFORMS
    ]


def _dict_literals_mapping_glob(tree: ast.AST) -> list[ast.AST]:
    """Dicionários/conjuntos literais cujas **chaves** são metacaracteres de glob.

    É a forma que `str.maketrans({...})` e uma tabela de tradução manual tomam, e nenhum
    dos dois passa por `.replace`.
    """
    encontrados: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            chaves = {
                key.value
                for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            if chaves & _GLOB_METACHAR_LITERALS:
                encontrados.append(node)
    return encontrados


def glob_translation_findings(source: str, module_name: str) -> list[str]:
    """Sinais de que `source` implementa uma **segunda** tradução glob→regex.

    Existe como função — e não embutida no teste — para que os contrafactuais da auditoria
    possam ser rodados contra ela diretamente, sem escrever arquivo dentro de `app/`. É o
    que torna o próprio detector testável.

    ## O que ele procura

    1. **importar `fnmatch`/`glob`** — o caminho óbvio;
    2. **as assinaturas literais** da tradução para regex (`[^/]*`, `[^/]`, `(?:.*/)?`) —
       o caminho por cópia;
    3. **a forma estrutural**: qualquer *transformação de string* (`.replace`, `.translate`,
       `.maketrans`, `.join`, `.format`, `re.sub`) cujos literais incluam metacaractere de
       glob, **ou** um dicionário literal com metacaractere nas chaves, no mesmo módulo em
       que se chama uma função de casamento de `re`. É por (3) que uma implementação
       alternativa passa, e generalizá-la de "lista de padrões conhecidos" para "lista de
       mecanismos" foi o que E4-AUD2-005 exigiu: o contrafactual daquela rodada usava
       `str.translate`, que a versão anterior — presa a `.replace` — não via.

    ## O que ele **não** é

    > **Esta é uma barreira heurística contra descuido, não uma prova formal de unicidade de
    > gramática.** Um segundo casador pode ser escrito sem tocar em `re` (comparando
    > caractere a caractere, como o próprio dono da gramática faz), e nenhuma análise
    > estática barata distingue isso de um parser qualquer. O detector existe para que uma
    > segunda implementação **acidental** — a que alguém escreve com pressa por não saber
    > que o expansor já existe — pare no CI. Contra uma segunda implementação **deliberada**,
    > a defesa real continua sendo a revisão de código e o `__init__.py` do
    > `context_engine`, que expõe um ponto de entrada só.

    Assumir isso explicitamente é melhor do que fingir cobertura: um teste que se apresenta
    como prova e não é vira exatamente a falsa segurança que a E2 já pagou uma vez.
    """
    findings: list[str] = []
    tree = ast.parse(source, filename=module_name)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            nomes = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            nomes = [node.module]
        else:
            continue
        for nome in nomes:
            if nome.split(".")[0] in ("fnmatch", "glob"):
                findings.append(f"{module_name} importa `{nome}`")

    for assinatura in ('"[^/]*"', "'[^/]*'", '"[^/]"', "'[^/]'", '"(?:.*/)?"', "'(?:.*/)?'"):
        if assinatura in source:
            findings.append(f"{module_name} contém a assinatura de tradução {assinatura}")

    matchers = {
        f"re.{node.func.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "re"
        and node.func.attr in _RE_MATCHERS
    }
    if not matchers:
        return findings

    for metodo, call in _transform_calls(tree):
        metacaracteres = _string_constants(call) & _GLOB_METACHAR_LITERALS
        if metacaracteres:
            findings.append(
                f"{module_name} transforma metacaractere {sorted(metacaracteres)} via "
                f"`.{metodo}(...)` e casa com {sorted(matchers)}"
            )

    for tabela in _dict_literals_mapping_glob(tree):
        del tabela
        findings.append(
            f"{module_name} mapeia metacaractere de glob numa tabela literal e casa com "
            f"{sorted(matchers)}"
        )

    return findings


def test_gramatica_de_glob_tem_dono_unico() -> None:
    """Nenhuma segunda implementação de casamento de padrão de `source_ref` existe.

    Este é o invariante central da E4 e o fechamento de E2-AUD-003: um validador e um
    expansor que discordam sobre o que `a/**/b` significa é um buraco de segurança com
    aparência de rigor.

    `app/safety/secrets.py` é a exceção **nomeada e distinta**: ele casa a denylist de
    segredos de [04] §5 — outra gramática, outro documento, congelada desde a E2, e nunca
    aplicada a um `source_ref` como padrão. Nomeá-la aqui é o que impede que uma terceira
    apareça sem ninguém notar.

    Ver o docstring de `glob_translation_findings` para o que este teste **não** garante.
    """
    dono = APP_ROOT / "context_engine" / "source_ref_expansion.py"
    excecao_denylist = APP_ROOT / "safety" / "secrets.py"

    for path in ALL_FILES:
        if path in (dono, excecao_denylist):
            continue
        findings = glob_translation_findings(path.read_text(encoding="utf-8"), _module_name(path))
        assert not findings, (
            "a gramática de glob tem dono único "
            f"(context_engine/source_ref_expansion.py): {findings}"
        )


#: Os contrafactuais das duas auditorias, lado a lado. Cada um é uma tradução alternativa
#: que **não** importa `fnmatch` e **não** copia as assinaturas literais do dono — só muda o
#: mecanismo de transformação. Ficam como constantes para que a detecção seja testável.
_CONTRAFACTUAIS_DE_AUDITORIA = {
    # 1ª rodada (E4-AUD-009): cadeia de `.replace`.
    "replace encadeado": "\n".join(
        [
            "import re",
            "",
            "",
            "def casa_source_ref(pattern: str, path: str) -> bool:",
            '    convertido = pattern.replace("**", "\\x00").replace("*", "[^/]+")',
            '    convertido = convertido.replace("\\x00", ".+").replace("?", ".")',
            "    return re.fullmatch(convertido, path) is not None",
        ]
    ),
    # 2ª rodada (E4-AUD2-005): `str.translate` com tabela — a versão anterior do detector,
    # presa a `.replace`, não via isto.
    "str.translate": "\n".join(
        [
            "import re",
            "",
            'TABELA = str.maketrans({"*": "[^/]*", "?": "[^/]"})',
            "",
            "",
            "def casa_source_ref(pattern: str, path: str) -> bool:",
            "    return re.fullmatch(pattern.translate(TABELA), path) is not None",
        ]
    ),
    # Variação: tabela como dicionário literal, sem `maketrans` no mesmo módulo.
    "tabela literal + join": "\n".join(
        [
            "import re",
            "",
            'MAPA = {"*": "[^/]*", "?": "[^/]", "**": ".*"}',
            "",
            "",
            "def casa_source_ref(pattern: str, path: str) -> bool:",
            '    convertido = "".join(MAPA.get(c, c) for c in pattern)',
            "    return re.search(convertido, path) is not None",
        ]
    ),
}


@pytest.mark.parametrize("nome", sorted(_CONTRAFACTUAIS_DE_AUDITORIA))
def test_contrafactual_traducao_alternativa_escondida_e_detectada(nome: str) -> None:
    """**Reproduções de E4-AUD-009 e E4-AUD2-005.** O detector precisa pegar as três.

    Nenhuma importa `fnmatch`, nenhuma copia as assinaturas literais do dono, e todas são
    uma segunda gramática — com semântica **diferente** da real. A primeira versão do
    detector via só `.replace`; generalizar para "qualquer transformação de string com
    metacaractere + qualquer casamento de `re`" é o que cobre as outras duas.
    """
    findings = glob_translation_findings(_CONTRAFACTUAIS_DE_AUDITORIA[nome], "app.modulo_travesso")

    assert findings, f"o detector deixou passar o contrafactual `{nome}`"


def test_contrafactual_os_sinais_antigos_tambem_continuam_pegando() -> None:
    """As duas detecções anteriores não foram substituídas — foram somadas."""
    por_import = glob_translation_findings("import fnmatch\n", "app.x")
    por_assinatura = glob_translation_findings('PADRAO = "[^/]*"\n', "app.y")

    assert por_import and "fnmatch" in por_import[0]
    assert por_assinatura and "assinatura" in por_assinatura[0]


def test_detector_de_gramatica_nao_acusa_codigo_inocente() -> None:
    """Precisão: `.replace` e `re` são comuns; só a **combinação** com glob é sinal.

    Sem este caso, o detector poderia virar um alarme que todo mundo aprende a ignorar.
    """
    inocente = [
        # `.replace` sem metacaractere de glob
        'import re\nlimpo = valor.replace("\\\\", "/")\nre.fullmatch("a", limpo)\n',
        # metacaractere de glob sem casamento de regex
        'rotulo = valor.replace("*", "estrela")\n',
        # `re.sub` não casa nada — é normalização
        'import re\nlimpo = re.sub(r"/{2,}", "/", valor.replace("*", ""))\n',
    ]

    for fonte in inocente:
        assert glob_translation_findings(fonte, "app.inocente") == [], fonte


#: Funções de `re` que **casam** alguma coisa. `re.sub`/`re.escape` ficam de fora: a
#: primeira é normalização de string, a segunda é o oposto de interpretar metacaractere.
_RE_MATCHING_FUNCTIONS = frozenset({"compile", "match", "fullmatch", "search", "finditer"})


def _re_matching_calls(path: Path) -> set[str]:
    """Chamadas a `re.<função de casamento>` no código — docstring e comentário não contam."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re"
            and node.func.attr in _RE_MATCHING_FUNCTIONS
        ):
            found.add(f"re.{node.func.attr}")
    return found


def test_expansor_e_o_unico_a_decidir_gramatica_dentro_do_safety() -> None:
    """`safety/source_refs.py` valida o envelope e **nunca** interpreta metacaractere.

    `allow_glob_syntax=True` só desliga uma checagem. Se algum dia alguém escrever ali a
    lógica que decide o que `*` casa, este teste quebra — que é exatamente o momento em que
    a regra precisa ser lembrada.

    A verificação é por AST, não por substring: o módulo **fala** sobre `fnmatch` e sobre
    glob no docstring o tempo todo, e é justamente esse texto que documenta a regra. O que
    não pode existir é a **chamada**.
    """
    alvo = APP_ROOT / "safety" / "source_refs.py"

    for imported in _imports(alvo):
        root = imported.split(".")[0]
        assert root not in ("fnmatch", "glob"), (
            f"safety/source_refs.py importa `{imported}`: gramática de glob pertence a "
            "context_engine/source_ref_expansion.py"
        )

    casamentos = _re_matching_calls(alvo)
    assert not casamentos, (
        f"safety/source_refs.py chama {sorted(casamentos)}: casar padrão é decidir "
        "gramática, e isso pertence a context_engine/source_ref_expansion.py "
        "(`re.sub` de normalização continua permitido)"
    )


def test_orchestrator_nao_importa_camadas_superiores_nem_adaptador() -> None:
    """Regra de módulo da E6 ([01] §2, contrato de `orchestrator/`).

    Pode importar `db`, `context_engine`, `git_runtime`, `safety`, `workspace` (leitura) e
    `config`. **Não** pode importar `api` — isso inverteria a direção de L4→L3 — nem
    `agent_runtime`/`tool_executor`, que são L1 e chegam **por injeção**.

    A proibição de adaptador concreto já é coberta por
    `test_nenhum_provider_ou_agente_e_importado`, que roda sobre todos os módulos.
    """
    proibidos = {
        "app.api",
        "app.main",
        "app.agent_runtime",
        "app.tool_executor",
        "fastapi",
        "starlette",
    }

    for path in (APP_ROOT / "orchestrator").rglob("*.py"):
        for imported in _imports(path):
            root = imported.split(".")[0]
            assert imported not in proibidos and root not in proibidos, (
                f"orchestrator/{path.name} importa `{imported}`: [01] §2 proíbe"
            )


def test_orchestrator_recebe_as_portas_por_injecao() -> None:
    """[01] §2: o Orchestrator "recebe por injeção — não os procura".

    As duas portas desta fase (`AnalyzerEnrichmentPort`, `CapabilityProver`) são
    `Protocol` e chegam por parâmetro. Um *registry*, um `importlib` ou um dicionário de
    nome→classe seria o lookup que a neutralidade de provider proíbe.
    """
    for path in (APP_ROOT / "orchestrator").rglob("*.py"):
        imports = _imports(path)
        assert "importlib" not in imports, (
            f"orchestrator/{path.name} importa `importlib`: resolução dinâmica de "
            "implementação é lookup, e o Orchestrator não faz lookup"
        )
        assert "pkgutil" not in imports
        assert "entry_points" not in path.read_text(encoding="utf-8")


def test_somente_o_orchestrator_escreve_as_quatro_entidades() -> None:
    """[01] §2: "único módulo que escreve `WorkspaceTask`, `Run`, `AuditFinding` e
    `SafetyEvent`".

    A verificação é por **construção do modelo** (`WorkspaceTask(...)`, `SafetyEvent(...)`)
    e por `session.delete`, que são as duas formas de um módulo criar ou remover uma dessas
    linhas. `tests/` fica de fora: `context_helpers.make_task` existe justamente para
    satisfazer FK sem passar pela máquina de estados, e é declarado como não-produção.

    `workspace/purge.py` é a exceção **nomeada**: [02] §11 dá a purga de workspace ao
    agregado `workspace/`, e a remoção em cascata leva as tasks junto. Ela remove linhas
    por FK, nunca transiciona nem cria nenhuma.
    """
    entidades = {"WorkspaceTask", "Run", "AuditFinding", "SafetyEvent"}
    dono = APP_ROOT / "orchestrator"
    excecao_purga = APP_ROOT / "workspace" / "purge.py"

    for path in ALL_FILES:
        if path.is_relative_to(dono) or path == excecao_purga:
            continue
        # `db/models.py` **define** as classes; definir não é escrever.
        if path.is_relative_to(APP_ROOT / "db"):
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        construcoes = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in entidades
        }
        assert not construcoes, (
            f"{_module_name(path)} constrói {sorted(construcoes)}: só o Execution Manager "
            "escreve essas entidades ([01] §2)"
        )


def test_orchestrator_nao_reimplementa_hash_nem_casamento_de_padrao() -> None:
    """Nenhuma segunda `canonical_json`, nenhum segundo casador de glob, nenhum `sha256`
    solto.

    O Orchestrator **compõe** hashes (`canonical_sha256` de `safety/`) e **consulta**
    cobertura (`SourceRefMatcher` de `context_engine/`). Um `hashlib.sha256` direto aqui
    seria uma segunda definição de "como se hasheia", que é o defeito que
    `safety/canonical.py` existe para impedir ([02] §7: "uma única definição, para que dois
    módulos não inventem duas normalizações").
    """
    for path in (APP_ROOT / "orchestrator").rglob("*.py"):
        imports = _imports(path)
        assert "hashlib" not in imports, (
            f"orchestrator/{path.name} importa `hashlib`: hashing canônico é "
            "`safety.canonical` ([02] §7)"
        )
        assert "json" not in imports, (
            f"orchestrator/{path.name} importa `json`: serialização canônica é "
            "`safety.canonical.canonical_json`"
        )


#: Onde `detect_sensitive_objective_signals` pode morar. É `safety/`, e só: ela compõe
#: `is_sensitive_key` com `classify_path_secrecy`, e o Orchestrator a **consome**
#: (E6-AUD-009).
_OBJECTIVE_SIGNALS_OWNER = APP_ROOT / "safety" / "objective_signals.py"

#: Quantos nomes canônicos, citados como literal, caracterizam uma cópia da lista. Um ou
#: dois podem aparecer numa mensagem de erro legítima; três já é a tabela reescrita.
_NOMES_QUE_CARACTERIZAM_COPIA = 3


def test_sinal_de_segredo_no_objetivo_vive_so_em_safety() -> None:
    """E6-AUD-009: o Orchestrator importa a função pronta; não a reimplementa.

    Escrevê-la em `orchestrator/analyzer.py` exigiria uma segunda lista de nomes sensíveis
    e uma segunda denylist de caminho — a mesma classe de defeito que E2-AUD-003 fechou
    para glob e que `test_deteccao_de_segredo_tem_dono_unico` já barra para os padrões de
    conteúdo. Aqui a regra é sobre a **função**: ela é definida num lugar só.
    """
    definidores = [
        path
        for path in ALL_FILES
        if "def detect_sensitive_objective_signals" in path.read_text(encoding="utf-8")
    ]

    assert definidores == [_OBJECTIVE_SIGNALS_OWNER], (
        "`detect_sensitive_objective_signals` tem de ser definida só em "
        f"safety/objective_signals.py; encontrada em {[_module_name(p) for p in definidores]}"
    )


def test_orchestrator_nao_tem_segunda_lista_de_nomes_sensiveis_nem_denylist() -> None:
    """Nenhuma tabela de segredo dentro de `orchestrator/`.

    A verificação é sobre **conteúdo de dado**: os nomes de campo que
    `safety/redaction.py` lista, e os padrões de caminho que `safety/secrets.py` lista.
    `HIGH_RISK_PATH_RULES` do Analyzer é uma tabela de **risco** que cita alguns dos
    mesmos caminhos de propósito ([03] §5 manda citá-los), então a regra não pode ser "não
    mencione `.env`" — ela é "não redefina a *lista de nomes de credencial*", que é o que
    duplicaria `_SENSITIVE_KEY_WORDS`.
    """
    from app.safety.redaction import _SENSITIVE_KEY_NAMES

    for path in (APP_ROOT / "orchestrator").rglob("*.py"):
        fonte = path.read_text(encoding="utf-8")
        # Uma lista literal com ≥ 3 dos nomes canônicos é, na prática, a lista copiada.
        repetidos = [nome for nome in _SENSITIVE_KEY_NAMES if f'"{nome}"' in fonte]
        assert len(repetidos) < _NOMES_QUE_CARACTERIZAM_COPIA, (
            f"orchestrator/{path.name} parece conter uma segunda lista de nomes "
            f"sensíveis ({sorted(repetidos)}): ela pertence a safety/redaction.py"
        )


# ------------------------------------------------- boundary de saída ([04] §5, E6-AUD3)

#: O único módulo autorizado a nomear o `JSONResponse` cru do Starlette: é onde
#: `RedactingJSONResponse` o subclassa.
_RESPONSE_BOUNDARY_OWNER = APP_ROOT / "api" / "responses.py"

#: As únicas classes que uma rota pode declarar em `response_class=`.
#:
#: `HTMLResponse` e `Response` vivem fora do boundary porque não carregam JSON: o HTML de
#: bootstrap e o `204` sem corpo, os dois com contrato próprio em [04] §5.
#: `EditViewJSONResponse` carrega JSON e **não** redige — é a exceção estreita de
#: E6-AUD4-004, e é `test_o_conjunto_de_respostas_sem_redacao_e_fechado` que a mantém em
#: um único ponto de uso. Estar nesta lista autoriza o nome; não autoriza a segunda rota.
_NON_JSON_RESPONSES = frozenset(
    {"HTMLResponse", "Response", "RedactingJSONResponse", "EditViewJSONResponse"}
)


def _route_functions(tree: ast.Module) -> list[ast.FunctionDef]:
    """Funções decoradas com `@router.<verbo>(...)` — os *endpoints* propriamente ditos."""
    encontradas: list[ast.FunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            alvo = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(alvo, ast.Attribute) and ast.unparse(alvo).startswith("router."):
                encontradas.append(node)
                break
    return encontradas


def test_nenhuma_resposta_json_escapa_do_boundary_de_redacao() -> None:
    """Nada em `api/` ou `main.py` constrói um `JSONResponse` cru ([04] §5, E6-AUD3-001).

    A garantia de saída é *opt-out*: `RedactingJSONResponse` é o `default_response_class` e
    a classe de todo *exception handler*. Um `JSONResponse` construído à mão em qualquer
    outro lugar seria uma resposta fora do boundary — exatamente o que fazia o `422` de
    validação ecoar `detail[].input` cru.

    O único lugar onde o nome pode aparecer é `api/responses.py`, que o subclassa.
    """
    alvos = [*(APP_ROOT / "api").rglob("*.py"), APP_ROOT / "main.py"]

    for caminho in alvos:
        if caminho == _RESPONSE_BOUNDARY_OWNER:
            continue
        tree = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "JSONResponse":
                raise AssertionError(
                    f"{_module_name(caminho)}:{node.lineno} constrói `JSONResponse` cru; "
                    "toda resposta JSON passa por `api.responses.RedactingJSONResponse`"
                )
            if isinstance(node, ast.ImportFrom) and "JSONResponse" in {
                alias.name for alias in node.names
            }:
                raise AssertionError(
                    f"{_module_name(caminho)}:{node.lineno} importa `JSONResponse`; o único "
                    "módulo que pode nomeá-lo é api/responses.py"
                )


def test_nenhum_endpoint_devolve_dicionario_cru() -> None:
    """Um *endpoint* devolve modelo Pydantic ou o response class central — nunca um `dict`.

    Um `dict` literal devolvido por rota escapa da validação de forma **e** do
    `response_model`, e o FastAPI o serializaria com o `default_response_class` — que hoje
    redige, mas deixaria a rota sem contrato. É o par do teste acima: um proíbe a resposta
    fora do boundary, este proíbe o corpo sem schema.
    """
    for caminho in (APP_ROOT / "api").rglob("*.py"):
        tree = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
        for funcao in _route_functions(tree):
            for node in ast.walk(funcao):
                if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
                    raise AssertionError(
                        f"{_module_name(caminho)}:{node.lineno} — a rota `{funcao.name}` "
                        "devolve `dict` literal; devolva um modelo ou o response class"
                    )


def test_as_classes_de_resposta_das_rotas_sao_as_declaradas() -> None:
    """`response_class=` explícito só para os casos sem JSON, e nomeados.

    Uma rota que declare `response_class=JSONResponse` reintroduziria o bypass por uma
    porta que o teste de construção não vê. As exceções legítimas são as de [04] §5: HTML
    de bootstrap e `204` sem corpo.
    """
    for caminho in (APP_ROOT / "api").rglob("*.py"):
        tree = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
        for funcao in _route_functions(tree):
            for decorator in funcao.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                for keyword in decorator.keywords:
                    if keyword.arg != "response_class":
                        continue
                    declarada = ast.unparse(keyword.value)
                    assert declarada in _NON_JSON_RESPONSES, (
                        f"{_module_name(caminho)}: a rota `{funcao.name}` declara "
                        f"`response_class={declarada}`, fora do boundary de [04] §5"
                    )


# ------------------- toda subclasse de Response é enumerada (E6-AUD5-005)

#: Nomes de classe de resposta que vêm do framework. Qualquer classe do projeto que herde
#: de um destes — direta ou indiretamente — é uma saída HTTP, e portanto uma candidata a
#: escapar do boundary de [04] §5.
_FRAMEWORK_RESPONSES = frozenset(
    {
        "Response",
        "JSONResponse",
        "HTMLResponse",
        "PlainTextResponse",
        "FileResponse",
        "StreamingResponse",
        "RedirectResponse",
        "ORJSONResponse",
        "UJSONResponse",
    }
)

#: As **únicas** subclasses de resposta que o projeto define, e onde cada uma pode ser
#: construída. Uma terceira classe — mesmo que nunca declarada em `response_class=` —
#: quebra esta lista antes de chegar perto de uma rota.
_RESPONSES_DO_PROJETO: dict[str, str] = {
    "RedactingJSONResponse": "app.api.responses",
    "EditViewJSONResponse": "app.api.responses",
}

#: Onde cada classe de resposta pode ser **construída**. `Response` cru é o `204` sem corpo
#: e o bootstrap; `HTMLResponse` é o HTML de bootstrap.
_CONSTRUCAO_AUTORIZADA: dict[str, frozenset[str]] = {
    "Response": frozenset({"app.api.context", "app.api.web", "app.main"}),
    "HTMLResponse": frozenset({"app.api.web"}),
    # `main` (handlers e middleware), `web` (o 404 de bootstrap) e as duas prévias de purga,
    # que montam a resposta à mão porque o Pydantic rebaixa o `Unredacted` do `purge_token`.
    "RedactingJSONResponse": frozenset(
        {"app.main", "app.api.web", "app.api.tasks", "app.api.workspaces"}
    ),
    "EditViewJSONResponse": frozenset({"app.api.context"}),
}


def _subclasses_de_resposta(arvore: ast.Module) -> dict[str, str]:
    """As classes do módulo que herdam de alguma classe de resposta conhecida."""
    encontradas: dict[str, str] = {}
    conhecidas = set(_FRAMEWORK_RESPONSES) | set(_RESPONSES_DO_PROJETO)
    for node in ast.walk(arvore):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            nome = ast.unparse(base).split(".")[-1]
            if nome in conhecidas:
                encontradas[node.name] = nome
    return encontradas


def _apelidos_de_resposta(arvore: ast.Module) -> dict[str, str]:
    """`from x import Y as Z` → `{"Z": "Y"}`, para os nomes de resposta que importam.

    Sem isto a regra é contornável por uma linha: importar com outro nome e construir o
    apelido. O mutante de E6-AUD5-005 nem precisou disso — construiu uma classe nova
    diretamente —, mas a barreira tem de valer para as duas formas.
    """
    conhecidas = set(_FRAMEWORK_RESPONSES) | set(_RESPONSES_DO_PROJETO)
    apelidos: dict[str, str] = {}
    for node in ast.walk(arvore):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in conhecidas and alias.asname:
                    apelidos[alias.asname] = alias.name
    return apelidos


def test_o_projeto_nao_define_uma_terceira_classe_de_resposta() -> None:
    """A lista de subclasses de `Response` do projeto é **fechada** (E6-AUD5-005).

    A quinta auditoria mostrou o buraco da versão anterior: ela procurava nomes conhecidos e
    inspecionava `response_class=`, então uma classe nova, importada e **construída
    diretamente** dentro de uma rota, passava por todos os 143 testes e devolvia conteúdo
    cru. Contar os usos das duas exceções conhecidas não enumera as que ainda não existem.

    Esta regra inverte a pergunta: em vez de "esta classe conhecida está sendo usada onde
    deve?", ela pergunta "existe alguma classe de resposta que eu não conheço?". Uma
    `ThirdAuditResponse(JSONResponse)` quebra aqui no momento em que é **definida**, muito
    antes de ser usada.
    """
    definidas: dict[str, tuple[str, str]] = {}
    for caminho in ALL_FILES:
        arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
        for nome, base in _subclasses_de_resposta(arvore).items():
            definidas[nome] = (_module_name(caminho), base)

    assert set(definidas) == set(_RESPONSES_DO_PROJETO), (
        "o conjunto de subclasses de Response do projeto mudou: "
        f"{sorted(definidas)} != {sorted(_RESPONSES_DO_PROJETO)}. Uma classe de resposta "
        "nova é uma saída HTTP nova, e precisa entrar na lista de exceções de [04] §5 por "
        "decisão, não por herança"
    )
    for nome, (modulo, _base) in definidas.items():
        assert modulo == _RESPONSES_DO_PROJETO[nome], (
            f"`{nome}` migrou para {modulo}; classes de resposta moram em api/responses.py"
        )


def test_nenhuma_classe_de_resposta_e_construida_fora_do_lugar() -> None:
    """Onde cada classe de resposta pode ser **construída**, incluindo apelidos de import.

    O par do teste acima: aquele proíbe a classe nova, este proíbe usar uma classe conhecida
    num lugar novo. Juntos, cobrem a forma que E6-AUD5-005 reproduziu — uma rota que devolve
    um objeto `Response` já construído, sem `response_class=` no decorador, e por isso
    invisível para a regra que só lia decoradores.
    """
    alvos = [*(APP_ROOT / "api").rglob("*.py"), APP_ROOT / "main.py"]

    # As classes conhecidas incluem **toda** subclasse de resposta que existir na árvore, e
    # não só as duas declaradas: era essa a porta de E6-AUD5-005, em que uma classe nova
    # construída direto na rota não era reconhecida como classe de resposta por ninguém.
    conhecidas = set(_FRAMEWORK_RESPONSES) | set(_RESPONSES_DO_PROJETO)
    for caminho in ALL_FILES:
        conhecidas.update(_subclasses_de_resposta(ast.parse(caminho.read_text(encoding="utf-8"))))

    for caminho in alvos:
        modulo = _module_name(caminho)
        arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
        apelidos = _apelidos_de_resposta(arvore)
        locais = set(_subclasses_de_resposta(arvore))

        for node in ast.walk(arvore):
            if not isinstance(node, ast.Call):
                continue
            nome = ast.unparse(node.func).split(".")[-1]
            nome = apelidos.get(nome, nome)
            if nome not in conhecidas or nome in locais:
                continue
            autorizados = _CONSTRUCAO_AUTORIZADA.get(nome, frozenset())
            assert modulo in autorizados, (
                f"{modulo}:{node.lineno} constrói `{nome}`, que só pode ser construída em "
                f"{sorted(autorizados) or 'nenhum módulo'}. Uma resposta construída à mão "
                "escapa do `default_response_class` de [04] §5"
            )


# --------------------------- o conjunto fechado de saídas sem redação (E6-AUD3, E6-AUD4)

#: **Toda** a lista de exceções à Camada 3 de [04] §5, em um lugar só. São duas, e cada uma
#: tem um motivo que a outra não tem:
#:
#: * `Unredacted` — **um valor**: o `purge_token` de [02] §11, um segredo que a resposta tem
#:   a função de entregar. Redigi-lo quebraria a confirmação forte da purga.
#: * `EditViewJSONResponse` — **uma resposta inteira**: o conteúdo cru de uma entrada do
#:   Context Registry, para quem vai editá-la. Sem ela o editor lê `«redigido»`, salva, e o
#:   marcador vira conteúdo (E6-AUD4-004).
#:
#: Cada uma tem **um** ponto de uso, e este teste é o que impede uma terceira de aparecer.
#: A pergunta que ele existe para forçar não é "esta rota precisa de conteúdo cru?" — é
#: "por que a lista de exceções passou de duas para três?".
_SAIDAS_SEM_REDACAO: dict[str, tuple[str, str]] = {
    # nome -> (módulo onde pode ser usado, o que ele libera)
    "Unredacted": ("app.workspace.purge_tokens", "o purge_token de [02] §11"),
    "EditViewJSONResponse": ("app.api.context", "o conteúdo cru de edit-view"),
}


def _usos_de_nome(nome: str) -> list[str]:
    """Onde `nome` aparece como chamada ou como valor de `response_class=`, em todo o app."""
    encontrados: list[str] = []
    for caminho in ALL_FILES:
        if caminho == _RESPONSE_BOUNDARY_OWNER or caminho.name == "redaction.py":
            continue  # onde os dois tipos são **definidos**
        tree = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and ast.unparse(node.func) == nome) or (
                isinstance(node, ast.keyword)
                and node.arg == "response_class"
                and ast.unparse(node.value) == nome
            ):
                encontrados.append(f"{_module_name(caminho)}:{node.lineno}")
    return encontrados


def test_o_conjunto_de_respostas_sem_redacao_e_fechado() -> None:
    """Exatamente duas saídas escapam da Camada 3, cada uma com **um** ponto de uso.

    Uma terceira rota que queira devolver conteúdo não redigido quebra aqui — seja
    construindo `Unredacted`, seja declarando `response_class=EditViewJSONResponse`, seja
    inventando uma classe nova (que cai em `_NON_JSON_RESPONSES`).
    """
    for nome, (modulo, _papel) in _SAIDAS_SEM_REDACAO.items():
        usos = _usos_de_nome(nome)
        assert len(usos) == 1, (
            f"esperava exatamente um uso de `{nome}` em todo o backend; encontrei {usos}. "
            "Acrescentar uma exceção à Camada 3 é uma decisão de arquitetura, não uma "
            "linha de código"
        )
        assert usos[0].split(":")[0] == modulo, f"`{nome}` migrou de {modulo} para {usos[0]}"


def test_a_rota_de_edit_view_nao_escreve_em_canal_nenhum() -> None:
    """A rota que devolve conteúdo cru não chama nada além de ler e projetar.

    Um `record_safety_event`, um `logger.info` ou uma mensagem de erro composta com o
    conteúdo entregariam ao disco exatamente o que a rota existe para manter na resposta —
    e num canal que **não** passa pelo boundary. A regra é uma allowlist de chamadas porque
    a lista negra ("não use logging") envelhece: qualquer canal novo passaria por ela.
    """
    tree = ast.parse((APP_ROOT / "api" / "context.py").read_text(encoding="utf-8"))
    rota = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "edit_view"
    )

    # Só o corpo: o decorador `@router.post(...)` é uma chamada, e é a declaração da rota.
    chamadas = {
        ast.unparse(node.func)
        for corpo in rota.body
        for node in ast.walk(corpo)
        if isinstance(node, ast.Call)
    }

    # A allowlist é literal de propósito: um canal novo (logging, evento, métrica) só entra
    # aqui depois de alguém explicar por que o conteúdo cru pode passar por ele.
    permitido = {
        "get_entry",  # lê a entrada
        "edit_hash_of",  # deriva a versão de edição
        "ContextEditViewResponse",  # projeta
        "ContextEntryUnreadable",  # erro tipado, mensagem constante (E6-AUD5-007)
        "error.errors",  # só os nomes de campo do erro de forma
        "str",  # idem, para compor o nome
        "', '.join",  # idem
    }
    assert chamadas <= permitido, (
        f"`edit_view` chama {sorted(chamadas - permitido)}, fora da lista autorizada; "
        "só pode ler a entrada, derivar o hash e projetá-la"
    )


# ------------------------------------------------- escape hatch do `purge_token` (E6-AUD3)


def test_unredacted_tem_exatamente_um_ponto_de_construcao() -> None:
    """`Unredacted(...)` é construído **uma** vez em todo o backend ([04] §5).

    É o que separa um *escape hatch* de uma flag: uma flag genérica pode ser adotada por
    qualquer campo novo "porque também não deveria ser redigido", e a exceção deixa de ser
    auditável. Um tipo nominal com um único `call site` mantém a lista de exceções em um
    item, verificável por AST, e obriga qualquer segunda exceção a passar por revisão.

    O ponto é `PurgeTokenStore.issue` — o `purge_token` de [02] §11, que a prévia de purga
    existe para entregar.
    """
    esperado = APP_ROOT / "workspace" / "purge_tokens.py"
    construcoes: list[str] = []

    for caminho in ALL_FILES:
        tree = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "Unredacted":
                construcoes.append(f"{_module_name(caminho)}:{node.lineno}")

    assert construcoes == [f"{_module_name(esperado)}:{_linha_do_issue(esperado)}"], (
        f"esperava exatamente um `Unredacted(...)`, em {_module_name(esperado)}; "
        f"encontrei {construcoes}"
    )


def _linha_do_issue(caminho: Path) -> int:
    """Linha do `Unredacted(...)` dentro de `PurgeTokenStore.issue`, lida do próprio código.

    Lida e não fixada: prender a linha exata faria o teste quebrar a cada edição de
    docstring, e o que ele afirma é "existe um só", não "está na linha 87".
    """
    tree = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "Unredacted":
            return node.lineno
    raise AssertionError(f"nenhum `Unredacted(...)` em {caminho}")


# --------------------------------- unidade de trabalho de conflito de escrita (E6-AUD3-002)


#: Funções públicas de `execution_manager` que recebem `session` e **não** são comandos.
#: Duas naturezas, ambas legítimas:
#:
#: * leitura pura (`get_task`, `list_tasks`, `latest_manifest`, `workspace_of`) — não
#:   escreve, logo não tem corrida de escrita a perder;
#: * primitiva de escrita (`record_safety_event`) — escreve, mas **sempre dentro** de um
#:   comando já envolvido. Um segundo boundary aqui capturaria o conflito no nível errado:
#:   quem precisa decidir o que fazer com a transação é o comando, não o `INSERT`.
#:
#: A lista é explícita de propósito. Acrescentar um comando novo não exige tocá-la (ele
#: só precisa do decorador); acrescentar uma função **sem** o decorador exige declarar
#: aqui por que ela não é um comando — que é a revisão que este teste existe para forçar.
_NAO_SAO_COMANDOS = frozenset(
    {"get_task", "list_tasks", "latest_manifest", "workspace_of", "record_safety_event"}
)


def test_todo_comando_do_execution_manager_passa_pela_unidade_de_trabalho() -> None:
    """Toda função pública que recebe `session` é decorada com `@command`.

    E6-AUD3-002 aconteceu porque o tratamento de conflito vivia dentro de `_abort_planning`,
    que só participa do `plan`: `approve` escrevia `SafetyEvent` e reverificava contexto
    **antes** do *compare-and-set*, e essas escritas não tinham tradução — viravam `500`.

    O decorador é o que torna a cobertura verificável em vez de lembrada. Este teste é a
    metade que garante que ninguém esqueça: um comando novo sem `@command` quebra a suíte
    aqui, e não em produção.
    """
    from app.orchestrator.execution_manager import GUARDED_COMMANDS

    caminho = APP_ROOT / "orchestrator" / "execution_manager.py"
    tree = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))

    faltando: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        argumentos = [arg.arg for arg in node.args.args]
        if not argumentos or argumentos[0] != "session":
            continue
        if node.name in _NAO_SAO_COMANDOS:
            continue
        decoradores = {ast.unparse(decorator) for decorator in node.decorator_list}
        if "command" not in decoradores:
            faltando.append(node.name)

    assert not faltando, (
        f"funções públicas de execution_manager que recebem `session` sem `@command`: "
        f"{faltando}. Toda escrita pode perder uma corrida ([02] §4)"
    )
    assert GUARDED_COMMANDS, "o registro de comandos guardados está vazio"


def test_o_classificador_de_conflito_tem_dono_unico() -> None:
    """`is_write_conflict` é definido só em `db/conflicts.py`.

    Ele nasceu dentro do Orchestrator (E6-AUD-004), e **ficar** lá foi o que permitiu
    E6-AUD3-002: `db.session.session_scope`, que é quem de fato commita, não podia
    consultá-lo sem inverter a direção das dependências. Duas cópias divergiriam pelo mesmo
    mecanismo que fez nascer a segunda lista de segredo do Analyzer.
    """
    dono = APP_ROOT / "db" / "conflicts.py"
    definidores = [
        _module_name(caminho)
        for caminho in ALL_FILES
        if any(
            isinstance(node, ast.FunctionDef) and node.name == "is_write_conflict"
            for node in ast.walk(ast.parse(caminho.read_text(encoding="utf-8")))
        )
    ]

    assert definidores == [_module_name(dono)], (
        f"`is_write_conflict` tem de ser definido só em db/conflicts.py; achei {definidores}"
    )

    # A checagem é sobre **código**, não sobre prosa: `execution_manager` cita
    # `SQLITE_BUSY_SNAPSHOT` no docstring que explica de onde o conflito vem, e citar não é
    # reimplementar. O que não pode reaparecer é o código como literal executável.
    for caminho in ALL_FILES:
        if caminho == dono:
            continue
        tree = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
        docstrings = {
            id(no.body[0].value)
            for no in ast.walk(tree)
            if isinstance(no, ast.Module | ast.ClassDef | ast.FunctionDef)
            and no.body
            and isinstance(no.body[0], ast.Expr)
            and isinstance(no.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith(("SQLITE_BUSY", "SQLITE_LOCKED"))
                and id(node) not in docstrings
            ):
                raise AssertionError(
                    f"{_module_name(caminho)}:{node.lineno} repete um código de conflito "
                    "do SQLite como literal: a lista pertence a db/conflicts.py"
                )


def test_backend_nao_conhece_o_dominio_comercial() -> None:
    """[ADR-0002]: o backend não importa `Client`, `Proposal`, `Project` nem lê localStorage."""
    termos = ("localstorage", "freelance_focus_data", "projectplanning")

    for path in ALL_FILES:
        conteudo = path.read_text(encoding="utf-8").lower()
        for termo in termos:
            assert termo not in conteudo, f"{_module_name(path)} referencia `{termo}`"


def test_nenhum_shell_true() -> None:
    for path in ALL_FILES:
        assert "shell=True" not in path.read_text(encoding="utf-8")


#: O dono único da detecção de segredo. Nenhum outro módulo compila padrão de segredo,
#: lista nome sensível ou casa regex sobre conteúdo autoral.
_REDACTION_OWNER = APP_ROOT / "safety" / "redaction.py"

#: Assinaturas de padrão de segredo. Se aparecerem fora do dono, alguém escreveu um
#: segundo motor de detecção — foi o defeito que a E5 fechou nas rodadas 1 e 2.
_SECRET_PATTERN_SIGNATURES = (
    "PRIVATE KEY",
    "sk-ant-",
    "AKIA",
    "github_pat_",
    r"gh[pousr]_",
)


def test_deteccao_de_segredo_tem_dono_unico() -> None:
    """`safety/redaction.py` é o único lugar que reconhece segredo.

    O Context Engine consome `is_sensitive_key`/`detect_secret_spans` e decide **o que
    fazer** com o resultado (mapear de volta ao campo, propagar valor conhecido). Ele não
    decide **o que é** segredo: uma segunda lista de nomes sensíveis ou um segundo
    conjunto de regex divergiria da primeira exatamente como as duas gramáticas de glob
    divergiam antes de E2-AUD-003.
    """
    for path in ALL_FILES:
        if path == _REDACTION_OWNER:
            continue
        fonte = path.read_text(encoding="utf-8")
        for assinatura in _SECRET_PATTERN_SIGNATURES:
            assert assinatura not in fonte, (
                f"{_module_name(path)} contém a assinatura de padrão de segredo "
                f"{assinatura!r}: reconhecer segredo pertence a safety/redaction.py"
            )


def test_context_engine_nao_casa_regex_sobre_conteudo_autoral() -> None:
    """`context_engine/` não tem motor de casamento próprio para conteúdo autoral.

    A exceção nomeada é `source_ref_expansion.py`, dono da gramática de glob — que casa
    **caminho**, nunca texto de entrada. Qualquer `re.<casamento>` fora dele em
    `context_engine/` é um segundo detector nascendo.
    """
    excecao = APP_ROOT / "context_engine" / "source_ref_expansion.py"

    for path in (APP_ROOT / "context_engine").rglob("*.py"):
        if path == excecao:
            continue
        casamentos = _re_matching_calls(path)
        assert not casamentos, (
            f"context_engine/{path.name} chama {sorted(casamentos)}: detecção sobre "
            "conteúdo autoral pertence a safety/redaction.py "
            "(`is_sensitive_key`/`detect_secret_spans`)"
        )


# ------------------------------------------------------------------------------ E7.2

#: Adaptadores concretos (E8.2+) vivem fora do "só contratos": têm SDK, asyncio e threads.
_ADAPTADORES = APP_ROOT / "agent_runtime" / "adapters"

#: E8.3: o Test Runner concreto (`generic-subprocess-v1`). Infraestrutura do sistema, não
#: provider: é o **único** módulo de `agent_runtime` que pode alcançar o Supervisor
#: (`process_runtime`, a aresta `agent_runtime → process_runtime` prevista em [01] §2 para a E8)
#: e o binding interno (`tool_executor.workspace.bind_workspace`, o mesmo da factory).
_RUNNERS = APP_ROOT / "agent_runtime" / "runners"
_TEST_RUNNER_MODULE = _RUNNERS / "local_subprocess.py"

#: Stdlib que o runner concreto pode usar: resolução de executável por `stat`/`os.path` e o
#: ambiente imutável. **Sem** `subprocess`, `shutil` (o `which` consulta o `cwd` no Windows),
#: `asyncio`, `socket`, `logging`.
_STDLIB_DO_TEST_RUNNER = {
    "__future__",
    "collections",
    "os",
    "re",
    "stat",
    "sys",
    "types",
}

_STDLIB_PERMITIDA_NOS_CONTRATOS = {
    "__future__",
    "dataclasses",
    "enum",
    "re",
    "types",
    "typing",
}


def _imports_do_projeto(pasta: str) -> dict[str, set[str]]:
    resultado: dict[str, set[str]] = {}
    for path in (APP_ROOT / pasta).rglob("*.py"):
        resultado[path.name] = {i for i in _imports(path) if i.split(".")[0] == "app"}
    return resultado


def _externos(pasta: str) -> dict[str, set[str]]:
    resultado: dict[str, set[str]] = {}
    for path in (APP_ROOT / pasta).rglob("*.py"):
        if path.is_relative_to(_ADAPTADORES):
            continue  # E8.2: adaptador concreto tem SDK/asyncio; fronteira no teste E8.2 próprio
        if path.is_relative_to(_RUNNERS):
            continue  # E8.3: runner concreto; fronteira em `test_test_runner_*` abaixo
        resultado[path.name] = {i for i in _imports(path) if i.split(".")[0] != "app"}
    return resultado


#: Os módulos de `tool_executor` que são só tipos (E7.2): continuam sem IO e só com `safety`.
_CONTRATOS_DO_TOOL_EXECUTOR = {"contracts.py", "validation.py"}


def test_tool_executor_so_importa_o_permitido() -> None:
    """[01] §2: `tool_executor` pode importar `safety`, `path_runtime`, `git_runtime`, `config`
    e stdlib. `→ agent_runtime` criaria ciclo; `→ db`/`orchestrator`/`context_engine`/`api`
    nunca; e **não** usa `process_runtime` (o Developer não tem processo).

    Os módulos de contrato (E7.2) continuam só com `safety`.
    """
    for nome, importados in _imports_do_projeto("tool_executor").items():
        permitido: tuple[str, ...] = ("app.safety", "app.tool_executor")
        if nome not in _CONTRATOS_DO_TOOL_EXECUTOR:
            permitido += ("app.path_runtime", "app.git_runtime", "app.config")
        for imported in importados:
            assert imported.startswith(permitido), (
                f"tool_executor/{nome} importa `{imported}`: fora do permitido"
            )


_PROIBIDOS_NO_TOOL_EXECUTOR = (
    "app.db",
    "app.agent_runtime",
    "app.orchestrator",
    "app.context_engine",
    "app.api",
    "app.main",
    "app.process_runtime",
    "app.workspace",
    "fastapi",
    "starlette",
    "sqlalchemy",
    "subprocess",
    "multiprocessing",
)


def test_tool_executor_nao_importa_banco_provider_nem_processo() -> None:
    for path in (APP_ROOT / "tool_executor").rglob("*.py"):
        for imported in _imports(path):
            assert not imported.startswith(_PROIBIDOS_NO_TOOL_EXECUTOR), (
                f"tool_executor/{path.name} importa `{imported}`"
            )


#: IO de filesystem e rede é do `path_runtime`/`git_runtime`; o executor só coordena a decisão.
_IO_PROIBIDO_NO_EXECUTOR = {
    "os",
    "shutil",
    "pathlib",
    "tempfile",
    "socket",
    "io",
    "glob",
    "fnmatch",
}


def test_tool_executor_nao_faz_io_por_conta_propria() -> None:
    for path in (APP_ROOT / "tool_executor").rglob("*.py"):
        for imported in _imports(path):
            assert imported.split(".")[0] not in _IO_PROIBIDO_NO_EXECUTOR, (
                f"tool_executor/{path.name} importa `{imported}`: IO é do path_runtime"
            )
        for call in _calls(path):
            func = call.func
            assert not (isinstance(func, ast.Name) and func.id == "open"), (
                f"tool_executor/{path.name} chama open()"
            )
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name not in {"write_bytes", "write_text", "unlink", "rmdir", "mkdir"}, (
                f"tool_executor/{path.name} chama `{name}`"
            )


def test_path_runtime_nao_importa_o_executor_nem_o_cancel_token() -> None:
    """As primitivas E7.5-A cooperam com cancelamento por `Callable[[], bool]`; não há
    dependência reversa de `path_runtime` para `tool_executor`/`agent_runtime`."""
    for imported in _imports(APP_ROOT / "path_runtime.py"):
        assert not imported.startswith(("app.tool_executor", "app.agent_runtime")), imported
    tree = ast.parse((APP_ROOT / "path_runtime.py").read_text(encoding="utf-8"))
    nomes = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    assert "CancelToken" not in nomes  # a docstring pode citá-lo; o código não


def test_path_runtime_nao_decide_politica_do_developer() -> None:
    """Capability, segredo e `.git` são decididos por `safety`; `path_runtime` só coleta e
    revalida fatos. Nem a regra mediada nem a classificação de segredo são chamadas aqui."""
    tree = ast.parse((APP_ROOT / "path_runtime.py").read_text(encoding="utf-8"))
    usados = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            usados |= {alias.name for alias in node.names}
    for nome in (
        "decide_mediated_path",
        "prevalidate_mediated_path",
        "classify_path_secrecy",
        "require_v1",
        "Capability",
        "DOT_GIT_RULE_ID",
    ):
        assert nome not in usados, f"path_runtime referencia `{nome}`: política não é dele"


def test_agent_runtime_so_ve_os_contratos_do_tool_executor() -> None:
    """O executor concreto (factory, executor, resolver, journal) nunca é importado por
    `agent_runtime`: os providers recebem só as portas de `contracts`."""
    for path in (APP_ROOT / "agent_runtime").rglob("*.py"):
        permitidos = {
            "app.tool_executor",
            "app.tool_executor.contracts",
            "app.tool_executor.validation",
        }
        if path == _TEST_RUNNER_MODULE:
            # E8.3: só o runner — que não é provider — confere o binding do run pela mesma
            # função da factory. Nenhum adaptador de provider vê o `ResolvedWorkspace`.
            permitidos |= {"app.tool_executor.workspace"}
        for imported in _imports(path):
            if imported.startswith("app.tool_executor"):
                assert imported in permitidos, f"agent_runtime/{path.name} importa `{imported}`"


def test_agent_runtime_so_depende_de_safety_e_tool_executor() -> None:
    """[01] §2: `agent_runtime` pode importar `safety` e `tool_executor`; **não** `db`,
    `orchestrator`, `context_engine`, `api`."""
    for path in (APP_ROOT / "agent_runtime").rglob("*.py"):
        permitido: tuple[str, ...] = ("app.safety", "app.tool_executor", "app.agent_runtime")
        if path == _TEST_RUNNER_MODULE:
            permitido += ("app.process_runtime",)  # E8.3: a aresta prevista em [01] §2
        for imported in _imports(path):
            if imported.split(".")[0] != "app":
                continue
            assert imported.startswith(permitido), (
                f"agent_runtime/{path.name} importa `{imported}`: [01] §2 proíbe"
            )


def test_contratos_usam_so_stdlib_de_tipos() -> None:
    """Sem `os`, `subprocess`, `socket`, `pathlib`, SDK: os contratos não têm efeito."""
    for pasta in ("agent_runtime", "tool_executor"):
        for nome, externos in _externos(pasta).items():
            if pasta == "tool_executor" and nome not in _CONTRATOS_DO_TOOL_EXECUTOR:
                continue  # executor concreto (E7.5-A): coberto pelos testes de IO acima
            for imported in externos:
                assert imported.split(".")[0] in _STDLIB_PERMITIDA_NOS_CONTRATOS, (
                    f"{pasta}/{nome} importa `{imported}`: contratos não têm IO nem SDK"
                )


#: Os módulos que podem importar `agent_runtime`: o próprio pacote e a **ponte** do composition
#: root (E7.6), que existe justamente para que `orchestrator` e `agent_runtime` não se
#: importem. `main.py` e a ponte são os únicos que enxergam as duas camadas.
_COMPOSITION_ROOT_MODULES = {"app.capability_wiring", "app.developer_wiring"}


def test_so_agent_runtime_importa_tool_executor_e_ninguem_importa_agent_runtime() -> None:
    """Sem ciclo e sem atalho: camadas superiores recebem as portas por injeção.

    E7.6: a única exceção a "ninguém importa `agent_runtime`" é a ponte do composition root
    (`app.capability_wiring`). Nenhum outro módulo — nem `orchestrator`, nem `api`.
    """
    for path in ALL_FILES:
        modulo = _module_name(path)
        for imported in _imports(path):
            if imported.startswith("app.agent_runtime"):
                assert modulo.startswith("app.agent_runtime") or (
                    modulo in _COMPOSITION_ROOT_MODULES
                ), f"{modulo} importa `{imported}`: só o composition root poderá (E7.6)"
            if imported.startswith("app.tool_executor"):
                assert modulo.startswith(("app.tool_executor", "app.agent_runtime")), (
                    f"{modulo} importa `{imported}`: fora do combinado em [01] §3"
                )


def test_nao_ha_adaptador_concreto_e_o_executor_tem_so_as_operacoes_ate_a_e7_5c() -> None:
    """Adaptadores (`agent_runtime/adapters/`) são E8+. O executor concreto tem as operações de
    arquivo (E7.5-B, `fs_ops`) e o `ApplyPatch` (E7.5-C, `patch_ops` + o parser puro
    `unified_diff`); nenhum módulo de Git mediado ainda (E7.5-D)."""
    # E8.2: existe um único adaptador concreto, o da Claude Messages API (E9 trará o do Codex).
    assert {p.name for p in _ADAPTADORES.iterdir() if p.is_dir() and p.name != "__pycache__"} == {
        "anthropic_messages"
    }
    assert {p.name for p in (APP_ROOT / "tool_executor").glob("*.py")} == {
        "__init__.py",
        "contracts.py",
        "validation.py",
        "workspace.py",
        "journal.py",
        "facade.py",
        "reasons.py",
        "executor.py",
        "factory.py",
        "outcome.py",
        "fs_ops.py",
        "patch_ops.py",
        "unified_diff.py",
        "git_ops.py",
        "diff_render.py",
    }


# ------------------------------------------------------------------- E7.5-D: Git mediado

#: Os **únicos** subcomandos que o Git mediado constrói: só objetos e índice.
_SUBCOMANDOS_MEDIADOS = {"rev-parse", "merge-base", "ls-tree", "ls-files", "cat-file"}

#: Nada que leia a worktree pelo Git (filtros/`textconv`), gere patch ou toque rede/config.
_PROIBIDOS_NO_GIT_MEDIADO = (
    "status",
    "diff",
    "diff-files",
    "diff-index",
    "diff-tree",
    "show",
    "log",
    "-p",
    "--patch",
    "--textconv",
    "--filters",
    "--batch",
    "check-attr",
    "update-index",
    "--refresh",
    "checkout",
    "fetch",
    "config",
    "submodule",
)


def _str_constants_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def test_git_mediado_so_usa_subcomandos_de_objeto_e_indice() -> None:
    constantes = _str_constants_of(_MEDIATED_MODULE)
    for proibido in _PROIBIDOS_NO_GIT_MEDIADO:
        assert proibido not in constantes, f"mediated.py constrói `{proibido}`"
    usados = {c for c in constantes if c in _SUBCOMANDOS_MEDIADOS}
    assert usados == _SUBCOMANDOS_MEDIADOS


def test_diff_index_do_git_mediado_e_o_da_e7_4() -> None:
    """D-AUD-001: o único `diff-index` do Git mediado é o argv já auditado da E7.4
    (`worktree._index_diff_argv`: árvore × índice, `--cached --raw -z`, sem diff externo/textconv),
    usado só em `MediatedGit.index_divergence` — nunca diff de worktree, nunca patch."""
    from app.git_runtime import worktree

    assert "app.git_runtime.worktree" in _imports(_MEDIATED_MODULE)
    assert _enclosing_functions_calls(_MEDIATED_MODULE, "_index_diff_argv") == ["index_divergence"]
    argv = worktree._index_diff_argv("a" * 40)
    assert {"--cached", "--raw", "-z", "--no-ext-diff", "--no-textconv", "--no-renames"} <= set(
        argv
    )
    assert not {"-p", "--patch", "--stat", "--textconv", "--ext-diff"} & set(argv)


def _calls_in_order(path: Path, function: str) -> list[str]:
    """Nomes chamados dentro de ``function``, na ordem do código-fonte."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
            calls.sort(key=lambda n: (n.lineno, n.col_offset))
            return [
                c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")
                for c in calls
            ]
    raise AssertionError(function)


def test_indice_e_arvore_sao_validados_antes_de_virar_mapa() -> None:
    """D-AUD-002: nenhum mapa por caminho sem antes validar unicidade/estágios."""
    for function, validator in (
        ("list_index", "_validate_index_entries"),
        ("list_tree", "_validate_tree_entries"),
    ):
        names = _calls_in_order(_MEDIATED_MODULE, function)
        assert validator in names and names.index(validator) < names.index("_freeze"), function


def test_cancelamento_e_checado_na_volta_do_supervisor() -> None:
    """D-AUD-004: em `MediatedGit._run`, o token é consultado antes **e depois** de
    `run_supervised`, antes de qualquer interpretação do desfecho."""
    names = _calls_in_order(_MEDIATED_MODULE, "_run")
    run = names.index("run_supervised")
    assert "_cancelled" in names[:run] and "_cancelled" in names[run + 1 :]


def test_git_mediado_so_cria_processo_pelo_supervisor() -> None:
    """Nenhum `subprocess` no Git mediado; `run_supervised` só dentro de `MediatedGit._run`."""
    assert not any(i.split(".")[0] == "subprocess" for i in _imports(_MEDIATED_MODULE))
    assert _enclosing_functions_calls(_MEDIATED_MODULE, "run_supervised") == ["_run"]


def test_git_mediado_fixa_opcoes_e_ambiente() -> None:
    from app.git_runtime import _git_env, mediated

    opcoes = mediated._MEDIATED_GIT_OPTIONS
    pares = {opcoes[i + 1] for i, item in enumerate(opcoes[:-1]) if item == "-c"}
    assert {
        "core.fsmonitor=false",
        "core.quotepath=false",
        "diff.relative=false",
        "status.relativePaths=false",
        "protocol.allow=never",
        "credential.helper=",
        "core.hooksPath=/dev/null",
        "core.untrackedCache=false",
    } <= pares
    assert {"--no-pager", "--no-lazy-fetch", "--no-replace-objects"} <= set(opcoes)
    env = _git_env({"GIT_DIR": "x", "GIT_EXTERNAL_DIFF": "x", "GIT_CONFIG": "x", "PATH": "p"})
    assert env == {
        "PATH": "p",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_LAZY_FETCH": "1",
    }


def test_tool_executor_nao_forma_argv_git() -> None:
    """`git_runtime` é o único dono do argv Git: nenhum subcomando aparece em `tool_executor`."""
    for path in (APP_ROOT / "tool_executor").glob("*.py"):
        constantes = _str_constants_of(path)
        proibidos = _SUBCOMANDOS_MEDIADOS | {"diff-tree", "diff-index", "diff-files", "--patch"}
        assert not constantes & proibidos, (path.name, constantes & proibidos)


def test_renderizador_de_diff_e_puro() -> None:
    path = APP_ROOT / "tool_executor" / "diff_render.py"
    assert {i.split(".")[0] for i in _imports(path)} <= {"__future__", "difflib"}


def test_git_mediado_nao_aceita_argv_de_fora() -> None:
    """A API pública de `MediatedGit` não tem parâmetro de argv/flag/config/ambiente."""
    import inspect as pyinspect

    from app.git_runtime.mediated import MediatedGit

    proibidos = {"argv", "args", "flags", "options", "config", "env", "command"}
    for name, member in vars(MediatedGit).items():
        if name.startswith("_") and name != "__init__":
            continue
        if callable(member):
            parametros = set(pyinspect.signature(member).parameters)
            assert not parametros & proibidos, (name, parametros & proibidos)


#: O parser de unified diff (E7.5-C) é **puro**: só estas bibliotecas, nenhum módulo do projeto.
_STDLIB_DO_PARSER_DE_PATCH = {"__future__", "collections", "dataclasses", "enum", "re"}


def test_parser_de_patch_e_puro() -> None:
    """`unified_diff` transforma texto em estrutura: zero IO, zero `SafetyPolicy`, zero banco,
    zero processo, zero git, zero `path_runtime` — e nenhum outro módulo de `app`."""
    path = APP_ROOT / "tool_executor" / "unified_diff.py"
    for imported in _imports(path):
        assert imported.split(".")[0] in _STDLIB_DO_PARSER_DE_PATCH, (
            f"unified_diff importa `{imported}`: o parser não pode ter dependência"
        )
    for call in _calls(path):
        if isinstance(call.func, ast.Name):  # builtins com efeito (`re.compile` é atributo)
            assert call.func.id not in {"open", "exec", "eval", "compile", "__import__"}


def test_patch_ops_so_produz_efeito_pelas_primitivas_do_path_runtime() -> None:
    """`patch_ops` coordena: os efeitos são `create_exclusive`/`write_fd`/`delete_if_identity`.
    Nenhum `os`/`shutil`/`pathlib`/`subprocess`, nenhum git e nenhuma escrita direta."""
    path = APP_ROOT / "tool_executor" / "patch_ops.py"
    for imported in _imports(path):
        assert imported.split(".")[0] not in {"os", "shutil", "pathlib", "subprocess", "io"}, (
            imported
        )
        assert not imported.startswith(("app.git_runtime", "app.process_runtime")), imported
    nomes = {
        (call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", ""))
        for call in _calls(path)
    }
    proibidos = {"open", "write_text", "write_bytes", "unlink", "remove", "rename", "replace"}
    assert not nomes & proibidos, nomes & proibidos
    assert {"create_exclusive", "write_fd", "delete_if_identity"} <= nomes


def test_so_o_executor_constroi_output_preparado() -> None:
    """C-AUD-001: `PreparedToolOutput` é a saída **já finalizada** pela fronteira central
    (redação uma vez + teto). Só `executor.py` a constrói; nenhuma operação a fabrica."""
    for path in (APP_ROOT / "tool_executor").glob("*.py"):
        for call in _calls(path):
            name = (
                call.func.attr
                if isinstance(call.func, ast.Attribute)
                else getattr(call.func, "id", "")
            )
            if name == "PreparedToolOutput":
                assert path.name == "executor.py", f"{path.name} constrói PreparedToolOutput"


def test_trace_de_mutacao_nao_conhece_politica() -> None:
    """C-AUD-002: `CreateMutationTrace` é só observabilidade do `path_runtime`."""
    tree = ast.parse((APP_ROOT / "path_runtime.py").read_text(encoding="utf-8"))
    (classe,) = [
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "CreateMutationTrace"
    ]
    nomes = {n.id for n in ast.walk(classe) if isinstance(n, ast.Name)}
    assert nomes <= {"dataclass", "int", "bool", "created_parent_count", "created_target"}, nomes


# ------------------------------------------------------------------------------ E7.3

_PROCESS_RUNTIME = APP_ROOT / "process_runtime"

#: Stdlib que o Supervisor pode usar. Sem `psutil`, `pywin32` ou qualquer dependência nova.
_STDLIB_DO_PROCESS_RUNTIME = {
    "__future__",
    "collections",
    "contextlib",
    "ctypes",
    "dataclasses",
    "enum",
    "math",
    "msvcrt",
    "os",
    "pathlib",
    "select",
    "signal",
    "subprocess",
    "sys",
    "threading",
    "time",
    "types",
    "typing",
}

#: As únicas funções Win32 que `_windows.py` pode ligar: todas documentadas e todas de
#: `kernel32` (nada de `ntdll`/`NtResumeProcess`).
_WIN32_DOCUMENTADAS = {
    "CreateJobObjectW",
    "SetInformationJobObject",
    "QueryInformationJobObject",
    "AssignProcessToJobObject",
    "IsProcessInJob",
    "TerminateJobObject",
    "OpenProcess",
    "OpenThread",
    "WaitForSingleObject",
    "ResumeThread",
    "CloseHandle",
    "CreateToolhelp32Snapshot",
    "Thread32First",
    "Thread32Next",
    "Process32FirstW",
    "Process32NextW",
    "QueryFullProcessImageNameW",
    "GetSystemDirectoryW",
    "PeekNamedPipe",
}

#: Maneiras de criar processo que o Supervisor **não** usa: tudo passa por um único
#: `subprocess.Popen` com `argv` em lista.
_CRIACAO_DE_PROCESSO_PROIBIDA = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "startfile"),
    ("os", "posix_spawn"),
    ("os", "posix_spawnp"),
    ("subprocess", "run"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("subprocess", "getoutput"),
    ("subprocess", "getstatusoutput"),
}


def _calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _dotted(node: ast.expr) -> tuple[str, str] | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id, node.attr
    return None


def test_process_runtime_e_folha_so_stdlib() -> None:
    """Adendo E7.3 a [01] §2: nenhum `app.*` de fora e nenhuma dependência externa."""
    for path in _PROCESS_RUNTIME.rglob("*.py"):
        for imported in _imports(path):
            root = imported.split(".")[0]
            if root == "app":
                assert imported.startswith("app.process_runtime"), (
                    f"process_runtime/{path.name} importa `{imported}`: o pacote é folha"
                )
            else:
                assert root in _STDLIB_DO_PROCESS_RUNTIME, (
                    f"process_runtime/{path.name} importa `{imported}`: fora da stdlib permitida"
                )


def test_so_o_modulo_de_worktree_importa_process_runtime() -> None:
    """Consumidores de provider (TestRunner/adaptadores, composition root) chegam na E8. Até
    lá, os **únicos** consumidores são `git_runtime/worktree.py` (o `worktree add` mutante,
    adendo E7.4) e `git_runtime/mediated.py` (as leituras Git do Developer, adendo E7.5-D).
    `orchestrator`, `agent_runtime`, `tool_executor`, `safety`, `api` e `db` continuam sem
    depender do Supervisor — nenhum provider o alcança."""
    for path in ALL_FILES:
        if path.is_relative_to(_PROCESS_RUNTIME) or path in _SUPERVISED_GIT_MODULES:
            continue
        if path == _TEST_RUNNER_MODULE:
            continue  # E8.3: o Test Runner concreto (aresta prevista em [01] §2 para a E8)
        for imported in _imports(path):
            assert not imported.startswith("app.process_runtime"), (
                f"{_module_name(path)} importa `{imported}`: só worktree.py/mediated.py e o "
                "Test Runner podem"
            )
    for path in (*_SUPERVISED_GIT_MODULES, _TEST_RUNNER_MODULE):
        assert any(i.startswith("app.process_runtime") for i in _imports(path))


def test_nenhuma_chamada_liga_shell() -> None:
    """Complementa `test_nenhum_shell_true` (textual) com a forma estrutural: nenhum
    argumento `shell` diferente do literal falso, em lugar nenhum; no Supervisor, nem isso."""
    for path in ALL_FILES:
        for call in _calls(path):
            for keyword in call.keywords:
                if keyword.arg != "shell":
                    continue
                assert not path.is_relative_to(_PROCESS_RUNTIME), (
                    f"{_module_name(path)} passa `shell`: o Supervisor nunca menciona shell"
                )
                assert isinstance(keyword.value, ast.Constant) and keyword.value.value is False, (
                    f"{_module_name(path)} liga shell"
                )


def test_process_runtime_so_cria_processo_por_popen_com_argv_estruturado() -> None:
    popens = 0
    for path in _PROCESS_RUNTIME.rglob("*.py"):
        for call in _calls(path):
            dotted = _dotted(call.func)
            assert dotted not in _CRIACAO_DE_PROCESSO_PROIBIDA, (
                f"process_runtime/{path.name} usa `{dotted}`: só `subprocess.Popen`"
            )
            if dotted and dotted[0] == "os" and dotted[1].startswith(("spawn", "exec", "fork")):
                raise AssertionError(f"process_runtime/{path.name} usa `os.{dotted[1]}`")
            if dotted != ("subprocess", "Popen"):
                continue
            popens += 1
            argv = ast.unparse(call.args[0]) if call.args else None
            executable = {k.arg: ast.unparse(k.value) for k in call.keywords}.get("executable")
            assert argv == "list(spec.argv)", f"{path.name}: argv precisa ser a lista da spec"
            assert executable == "spec.argv[0]", f"{path.name}: executável precisa ser argv[0]"
    assert popens == 2, "um `Popen` por backend (Windows e POSIX)"


def test_backend_windows_so_liga_apis_documentadas_de_kernel32() -> None:
    windows = _PROCESS_RUNTIME / "_windows.py"
    bound: set[str] = set()
    for call in _calls(windows):
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name in {"WinDLL", "CDLL", "OleDLL", "PyDLL", "LoadLibrary"}:
            assert [ast.unparse(a) for a in call.args[:1]] == ["'kernel32'"], (
                "só kernel32 pode ser carregada"
            )
        if name == "_bind":
            first = call.args[0]
            assert isinstance(first, ast.Constant) and isinstance(first.value, str)
            bound.add(first.value)
    assert bound == _WIN32_DOCUMENTADAS
    tree = ast.parse(windows.read_text(encoding="utf-8"))
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not attributes & {"windll", "oledll", "NtResumeProcess", "NtSuspendProcess"}


# ------------------------------------------- E8.3: Test Runner e verificação pós-execução

_EXECUTION_VERIFICATION = APP_ROOT / "execution_verification.py"
_POST_EXECUTION_GIT = APP_ROOT / "git_runtime" / "post_execution.py"


def test_test_runner_so_usa_a_stdlib_permitida() -> None:
    """O runner resolve o executável por `os`/`stat` — nunca `shutil.which` (no Windows/3.11 ele
    consulta o `cwd` antes do `PATH`), nunca `subprocess`, `asyncio`, `socket` ou `logging`."""
    externos = {i for i in _imports(_TEST_RUNNER_MODULE) if i.split(".")[0] != "app"}
    assert {i.split(".")[0] for i in externos} <= _STDLIB_DO_TEST_RUNNER, externos
    for path in _RUNNERS.rglob("*.py"):
        if path != _TEST_RUNNER_MODULE:
            assert all(i.startswith(("app.agent_runtime",)) for i in _imports(path)), path.name


def test_test_runner_so_cria_processo_pelo_supervisor() -> None:
    """O processo de teste nasce **só** por `run_supervised`, uma vez, dentro de `run`."""
    proibidas = {
        ("os", "system"),
        ("os", "popen"),
        ("os", "startfile"),
        ("os", "posix_spawn"),
        ("os", "posix_spawnp"),
    }
    for call in _calls(_TEST_RUNNER_MODULE):
        dotted = _dotted(call.func)
        assert dotted not in proibidas, f"runner cria processo por {dotted}"
        assert not (dotted and dotted[0] == "os" and dotted[1].startswith(("spawn", "exec"))), (
            dotted
        )
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        assert name not in {"which", "create_subprocess_exec", "create_subprocess_shell"}, name
    assert _enclosing_functions_calls(_TEST_RUNNER_MODULE, "run_supervised") == ["run"]


def test_test_runner_nao_le_o_ambiente_do_processo() -> None:
    """Nada de `os.environ`/`os.getenv`: o ambiente do filho é construído do zero a partir do
    ambiente confiável **injetado**, e o `PATH` da resolução vem do mesmo lugar (M4)."""
    tree = ast.parse(_TEST_RUNNER_MODULE.read_text(encoding="utf-8"))
    atributos = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not atributos & {"environ", "environb", "getenv", "putenv", "getcwd", "chdir"}


def test_execution_verification_so_importa_o_permitido() -> None:
    """A camada neutra da E8.3 compõe `git_runtime`, `path_runtime` e `safety` — e nada de
    banco, HTTP, orchestrator, `agent_runtime`, `tool_executor`, processo ou SDK."""
    for imported in _imports(_EXECUTION_VERIFICATION):
        root = imported.split(".")[0]
        if root == "app":
            assert imported.startswith(("app.git_runtime", "app.path_runtime", "app.safety")), (
                imported
            )
        else:
            assert root in {"__future__", "collections", "dataclasses", "enum", "time"}, imported


def test_execution_verification_nao_escreve_nem_grava_evento() -> None:
    """Só leitura: nenhuma escrita de arquivo, nenhum `SafetyEvent`, nenhum processo."""
    tree = ast.parse(_EXECUTION_VERIFICATION.read_text(encoding="utf-8"))
    nomes = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    # A docstring cita `SafetyEvent` para dizer quem o grava (E8.4); o código não o referencia.
    assert not nomes & {"record_safety_event", "SafetyEvent", "Session", "session_scope"}
    for call in _calls(_EXECUTION_VERIFICATION):
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        assert name not in {
            "open",
            "write",
            "write_text",
            "write_bytes",
            "unlink",
            "mkdir",
            "rename",
            "replace",
            "rmdir",
            "create_exclusive",
            "delete_if_identity",
            "write_fd",
        }, name


#: Subcomandos e opções do Git que leem o working tree pelo pipeline de conversão (filtros
#: `clean`/`smudge`/`process`, `textconv`, diff externo, `fsmonitor`) ou que escrevem.
_GIT_QUE_LE_O_WORKTREE = (
    "status",
    "diff",
    "diff-files",
    "add",
    "checkout",
    "restore",
    "hash-object",
    "update-index",
    "show",
    "log",
    "ls-files",
    "--textconv",
    "--filters",
    "--refresh",
)


def test_verificacao_e_filter_free() -> None:
    """E8.3-AUD-002/003 (A3): o estado da árvore principal não passa por `git status` nem por
    nada que rode helper do projeto. O único verbo Git **construído** em `post_execution.py` é
    `rev-parse`; índice, árvore e diff-index da worktree vêm do Git mediado já auditado (E7.5-D),
    que nunca lê o working tree."""
    constantes = _str_constants_of(_POST_EXECUTION_GIT)
    for literal in _GIT_QUE_LE_O_WORKTREE:
        assert literal not in constantes, f"post_execution.py constrói `{literal}`"
    for path in (_POST_EXECUTION_GIT, _EXECUTION_VERIFICATION):
        fonte = path.read_text(encoding="utf-8")
        assert "git status" not in fonte.split('"""', 2)[-1], path.name  # fora da docstring
    assert "rev-parse" in constantes
    for call in _calls(_POST_EXECUTION_GIT):
        assert _dotted(call.func) not in {("subprocess", "run"), ("subprocess", "Popen")}


def test_verificacao_nao_tem_sha1_escondido() -> None:
    """A9: o oid de blob da worktree sai do formato do repositório (`MediatedGit.blob_hasher`);
    o resumo local do estado é SHA-256. Nenhum `hashlib.sha1` nos módulos da E8.3."""
    for path in (_POST_EXECUTION_GIT, _EXECUTION_VERIFICATION, _TEST_RUNNER_MODULE):
        for call in _calls(path):
            assert _dotted(call.func) != ("hashlib", "sha1"), path.name


def test_verificacao_nunca_materializa_um_iterador_de_diretorio() -> None:
    """A10: nada de `list(os.scandir(...))`/`list(iterator)` — o orçamento é cobrado **durante**
    a enumeração (`_children`)."""
    for path in (_POST_EXECUTION_GIT, _EXECUTION_VERIFICATION):
        for call in _calls(path):
            if isinstance(call.func, ast.Name) and call.func.id in {"list", "tuple", "sorted"}:
                fonte = ast.unparse(call)
                assert "scandir" not in fonte and "iterator" not in fonte, fonte
        assert "listdir" not in path.read_text(encoding="utf-8")


def test_start_execution_continua_no_limite_da_e8_4() -> None:
    """E8.3 não liga o Test Runner nem a verificação ao `start_execution`: a função ainda para no
    `NotImplementedError` depois da guarda, e nenhuma rota de execução existe."""
    texto = (APP_ROOT / "orchestrator" / "execution_manager.py").read_text(encoding="utf-8")
    assert "NotImplementedError" in texto
    for nome in (
        "LocalSubprocessTestRunner",
        "PostExecutionVerifier",
        "execution_verification",
        "run_supervised",
        "create_worktree",
        "bind_workspace",
    ):
        assert nome not in texto, nome
    for path in (APP_ROOT / "api").rglob("*.py"):
        for imported in _imports(path):
            assert not imported.startswith(("app.execution_verification", "app.process_runtime"))
        assert "/execute" not in path.read_text(encoding="utf-8")


# ------------------------------------------------- E8.4.2: preparação da execution workspace

_EXECUTION_WORKSPACE = APP_ROOT / "orchestrator" / "execution_workspace.py"
_WORKSPACE_CONTRACT = APP_ROOT / "orchestrator" / "workspace_contract.py"


def test_orchestrator_recebe_path_runtime_e_verificacao_por_injecao() -> None:
    """E8.4.2: a preparação usa o Path Runtime, a captura da E8.3 e o binding concreto **por
    injeção** (portas estruturais, dados neutros). Nenhum módulo do Orchestrator importa
    `path_runtime`, `execution_verification`, `process_runtime`, `tool_executor` ou
    `agent_runtime` — nem dentro de `TYPE_CHECKING` (`_imports` percorre o arquivo inteiro)."""
    proibidos = (
        "app.path_runtime",
        "app.execution_verification",
        "app.process_runtime",
        "app.tool_executor",
        "app.agent_runtime",
    )
    for path in (APP_ROOT / "orchestrator").rglob("*.py"):
        for imported in _imports(path):
            assert not imported.startswith(proibidos), (
                f"orchestrator/{path.name} importa `{imported}`: chega por injeção"
            )
    assert _EXECUTION_WORKSPACE.is_file() and _WORKSPACE_CONTRACT.is_file()


def test_preparacao_nao_e_operacional() -> None:
    """E8.4.2 é componente interno: nenhum módulo da aplicação (rota, `main.py`, startup, ponte do
    composition root) importa o serviço de preparação. A composição integrada é da E8.4.6."""
    for path in ALL_FILES:
        if path in (_EXECUTION_WORKSPACE, _WORKSPACE_CONTRACT):
            continue
        for imported in _imports(path):
            assert not imported.startswith("app.orchestrator.execution_workspace"), (
                f"{_module_name(path)} importa a preparação: integração é E8.4.6"
            )
        assert "ExecutionWorkspaceService" not in path.read_text(encoding="utf-8"), path.name


def test_admissao_continua_sem_efeito_externo_da_preparacao() -> None:
    """A admissão (E8.4.1) não chama a preparação nem as escritas dela: worktree, snapshot e
    binding só nascem depois, fora da transação de admissão."""
    tree = ast.parse((APP_ROOT / "orchestrator" / "execution_manager.py").read_text("utf-8"))
    [admit] = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "admit_execution"
    ]
    chamadas = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(admit)
        if isinstance(node, ast.Call)
    }
    assert not chamadas & {
        "publish_prepared_workspace",
        "record_workspace_preparation_failure",
        "read_preparation_facts",
        "capture_main_tree",
        "bind_run",
    }
