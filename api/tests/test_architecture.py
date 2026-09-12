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

#: `subprocess` é liberado **exclusivamente** em `git_runtime/`, e só para o preflight de
#: LEITURA da E3 ([01] contrato de `git_runtime/`, [07] gate E3). Em qualquer outro módulo
#: continua proibido até E7.
SUBPROCESS_ALLOWED_UNDER = APP_ROOT / "git_runtime"


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


@pytest.mark.parametrize("path", ALL_FILES, ids=_module_name)
def test_nenhum_provider_ou_agente_e_importado(path: Path) -> None:
    for imported in _imports(path):
        root = imported.split(".")[0].lower()
        assert root not in FORBIDDEN_EVERYWHERE, (
            f"{_module_name(path)} importa `{imported}`: providers e agentes só a partir da E8"
        )


@pytest.mark.parametrize("path", ALL_FILES, ids=_module_name)
def test_nenhuma_execucao_de_processo(path: Path) -> None:
    for imported in _imports(path):
        root = imported.split(".")[0].lower()
        assert root not in FORBIDDEN_PROCESS, (
            f"{_module_name(path)} importa `{imported}`: execução de processo é E7"
        )
        if root == "subprocess":
            assert path.is_relative_to(SUBPROCESS_ALLOWED_UNDER), (
                f"{_module_name(path)} importa `subprocess` fora de git_runtime/: "
                "execução de processo fora do preflight de leitura é E7"
            )


def test_git_runtime_e_somente_leitura() -> None:
    """[01]: `git_runtime/` nunca executa verbo que altere o repositório do usuário."""
    mutating_verbs = (
        "commit",
        "merge",
        "push",
        "rebase",
        "reset",
        "checkout",
        "clean",
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
    )
    for path in (APP_ROOT / "git_runtime").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for verb in mutating_verbs:
            assert f'"{verb}"' not in source, (
                f"git_runtime/{path.name} usa o verbo git `{verb}`: o adaptador é só leitura"
            )


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
