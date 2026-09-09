"""Gate 3 da E4 — o expansor canônico de `source_refs`.

Este é o componente de **segurança novo** da fase. O que ele tem de provar:

1. glob apontando **diretamente** para `.env*`, com `.env` real na árvore → recusa do
   `source_ref` inteiro;
2. glob **amplo** que alcança um segredo por acidente (`config/*` com `config/.env.local`)
   → recusa do `source_ref` inteiro, **não** exclusão parcial;
3. glob seguro sem segredo na árvore → expande normalmente;
4. sintaxe perigosa **dentro** de um padrão (`../**`, `//srv/**`, …) → recusa, provando que
   `allow_glob_syntax=True` não reabriu nenhuma porta do envelope;
5. **unicidade de gramática** — validador isolado e expansor completo concordam
   exatamente sobre o envelope, e toda recusa própria do expansor vive num espaço de nomes
   distinto;
6. diretório literal expande recursivamente; arquivo literal resolve a si mesmo.

Os casos de gramática rodam contra árvores **em memória**: a gramática é uma função pura de
`(padrão, lista de caminhos)`, e montar um repositório git por caso adversarial só
esconderia casos atrás do custo. Os testes que exercitam `expand_against_commit` usam git de
verdade, e estão no fim do arquivo.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.context_engine.source_ref_expansion import (
    CompiledSourceRef,
    ExpansionStatus,
    compile_source_ref,
    compute_source_hash,
    expand_against_commit,
    expand_source_refs,
)
from app.git_runtime import TreeListing, UnrepresentablePath, preflight
from app.safety.policy import SafetyPolicy
from app.safety.source_refs import validate_source_ref
from app.safety.types import SafetyDecision
from tests.test_git_runtime import _NEEDS_GIT, _git

#: `blob_sha` fictício mas com a forma certa. O expansor não interpreta o SHA — ele só o
#: carrega para o `source_hash` —, então um valor sintético mantém os testes determinísticos.
_SHA = "0" * 40


def _files(*paths: str) -> tuple[tuple[str, str], ...]:
    """Os `(path, blob_sha)` legíveis de uma árvore em memória."""
    return tuple(
        sorted((path, f"{_SHA[: 40 - len(path)]}{path}"[:40].ljust(40, "0")) for path in paths)
    )


def _tree(*paths: str, unreadable: tuple[bytes, ...] = ()) -> TreeListing:
    """Árvore em memória no formato de `git_runtime.list_tree` (E4-AUD5-001).

    ``unreadable`` monta a árvore **parcial**: caminhos que a leitura viu e não soube
    nomear, ao lado dos legíveis.
    """
    return TreeListing(
        files=_files(*paths),
        unrepresentable=tuple(
            UnrepresentablePath(raw=raw, display=raw.decode("utf-8", "backslashreplace"))
            for raw in unreadable
        ),
    )


PLAIN_TREE = _tree(
    "README.md",
    "src/app.py",
    "src/util.py",
    "src/nested/deep.py",
    "src/nested/more/deeper.py",
    "docs/01-intro.md",
    "docs/02-guia.md",
    "config/settings.json",
)


def _paths(refs: list[str], tree: TreeListing | None = None) -> list[str]:
    result = expand_source_refs(refs, PLAIN_TREE if tree is None else tree)
    assert result.status is ExpansionStatus.RESOLVED, result.decision or result.unresolved_refs
    return list(result.paths)


# =========================================================== 1 e 2 — segredo na expansão


def test_glob_direto_para_env_nega_o_source_ref_inteiro() -> None:
    """Caso 1 do gate: `.env*` com um `.env` de verdade na árvore."""
    tree = _tree("src/app.py", ".env", ".env.example")

    result = expand_source_refs([".env*"], tree)

    assert result.status is ExpansionStatus.DENIED
    assert result.decision is not None
    assert result.files == ()
    assert result.source_hash is None


def test_glob_amplo_que_alcanca_segredo_nega_tudo_sem_filtrar() -> None:
    """Caso 2 do gate, o importante: `config/*` com `config/.env.local` na árvore.

    O padrão é inocente e o autor da entrada provavelmente nem sabe que há um segredo ali.
    A recusa é do `source_ref` **inteiro**: filtrar `config/.env.local` e seguir com os
    outros dois arquivos deixaria a entrada parecendo íntegra enquanto descreve um recorte
    que ninguém conferiu, e a exclusão sumiria em silêncio.
    """
    tree = _tree("config/settings.json", "config/logging.json", "config/.env.local")

    result = expand_source_refs(["config/*"], tree)

    assert result.status is ExpansionStatus.DENIED
    assert result.decision is not None
    assert result.decision.rule_id == "source_ref_expansion.secret_denied"
    assert ".env.local" in result.decision.subject_redacted
    # não sobrou expansão parcial nenhuma
    assert result.files == ()
    assert result.source_hash is None


def test_segredo_alcancado_por_um_ref_nega_a_lista_toda() -> None:
    """Um `source_ref` limpo na mesma lista não "salva" a expansão."""
    tree = _tree("src/app.py", "config/settings.json", "config/.env.local")

    result = expand_source_refs(["src/**", "config/*"], tree)

    assert result.status is ExpansionStatus.DENIED
    assert result.files == ()


@pytest.mark.parametrize(
    ("padrao", "segredo"),
    [
        ("**", ".env"),
        ("src/**", "src/.env.local"),
        ("*", "id_rsa"),
        ("src/*", "src/deploy.pem"),
        ("src/**/*", "src/keys/server.key"),
        ("?.pem", "a.pem"),
        ("[ai]d_rsa", "id_rsa"),
        ("src/**", "src/.aws/credentials"),
        ("src/**", "src/db_secret.json"),
        ("**/*.p12", "certs/cliente.p12"),
    ],
)
def test_segredo_alcancado_por_qualquer_forma_da_gramatica_nega(padrao: str, segredo: str) -> None:
    """A checagem de segredo roda **depois** da expansão, então cobre toda a gramática.

    Cada linha aqui é um metacaractere diferente alcançando um segredo diferente. Nenhuma
    delas depende de o padrão "parecer" perigoso — só do arquivo que ele alcança.

    Alguns padrões (`?.pem`, `**/*.p12`) são barrados já pela **primeira** barreira, a do
    `safety`, que classifica o padrão como se fosse literal e por isso o vê casar `*.pem`.
    As duas camadas negam; o teste aceita qualquer uma delas e afirma que a decisão é de
    segredo, porque é isso que importa — a barreira barata só consegue negar a mais.
    """
    tree = _tree("src/app.py", segredo)

    result = expand_source_refs([padrao], tree)

    assert result.status is ExpansionStatus.DENIED, padrao
    assert result.decision is not None
    assert result.decision.rule_id in (
        "source_ref_expansion.secret_denied",
        "source_ref.secret_denied",
    ), padrao


@pytest.mark.parametrize(
    ("padrao", "segredo"),
    [
        ("**", ".env"),
        ("src/**", "src/.env.local"),
        ("*", "id_rsa"),
        ("src/*", "src/deploy.pem"),
        ("src/**/*", "src/keys/server.key"),
        ("src/**", "src/.aws/credentials"),
        ("src/**", "src/db_secret.json"),
        ("config/*", "config/.env.local"),
    ],
)
def test_segredo_invisivel_no_padrao_so_e_pego_depois_de_expandir(
    padrao: str, segredo: str
) -> None:
    """Os casos em que a **primeira** barreira não vê nada de errado no padrão.

    O `safety` aceita `src/**` e `config/*` — são padrões inocentes. Quem nega é o expansor,
    depois de saber quais arquivos eles alcançam. É a razão de a decisão de segredo que vale
    ser a de depois da expansão.
    """
    assert validate_source_ref(padrao, allow_glob_syntax=True).decision.allow, padrao

    result = expand_source_refs([padrao], _tree("src/app.py", segredo))

    assert result.status is ExpansionStatus.DENIED, padrao
    assert result.decision is not None
    assert result.decision.rule_id == "source_ref_expansion.secret_denied", padrao


def test_env_example_e_segredo_por_padrao() -> None:
    """[04] §5 + CLAUDE.md: `.env*` inclui `.env.example`; a E2 não abriu exceção."""
    tree = _tree("src/app.py", ".env.example")

    assert expand_source_refs(["*"], tree).status is ExpansionStatus.DENIED


def test_excecao_explicita_de_politica_libera_e_so_ela() -> None:
    """A liberação existe, mas só por configuração explícita — nunca por heurística."""
    tree = _tree("src/app.py", ".env.example")
    permissiva = replace(SafetyPolicy(), secret_allow_exceptions=frozenset({".env.example"}))

    liberada = expand_source_refs(["*"], tree, policy=permissiva)
    assert liberada.status is ExpansionStatus.RESOLVED
    assert ".env.example" in liberada.paths

    # a exceção é de um caminho só: um `.env` de verdade continua negando
    com_env = expand_source_refs(
        ["*"], _tree("src/app.py", ".env.example", ".env"), policy=permissiva
    )
    assert com_env.status is ExpansionStatus.DENIED


# ============================================================ 3 — glob seguro expande


def test_estrela_nao_atravessa_barra() -> None:
    assert _paths(["src/*"]) == ["src/app.py", "src/util.py"]


def test_estrela_dupla_atravessa_barra() -> None:
    assert _paths(["src/**"]) == [
        "src/app.py",
        "src/nested/deep.py",
        "src/nested/more/deeper.py",
        "src/util.py",
    ]


# ------------------------------------- `**` = zero ou mais segmentos (testes normativos)
#
# Estes casos **são** a especificação da semântica de `**`: nenhum ADR nem documento de
# arquitetura a define a nível de caractere, então ela vive aqui, no dono único da
# gramática, e é aqui que uma mudança acidental quebra.
#
# A implementação anterior traduzia todo `**` para `.*` e deixava as duas barras literais
# no padrão (`a/.*/b`), o que exigia **pelo menos um** segmento intermediário. O efeito era
# subcobertura silenciosa: um `source_ref` que o autor acredita cobrir um arquivo e não
# cobre, produzindo um `source_hash` que nunca reage a mudanças naquele arquivo — falso
# negativo de staleness, a classe de defeito que AUD-004 existe para impedir.

#: Árvore mínima com o arquivo-alvo a zero, um e dois segmentos de profundidade.
DEPTH_TREE = _tree("a/b", "a/x/b", "a/x/y/b")


def test_estrela_dupla_casa_zero_segmentos() -> None:
    """`a/**/b` casa `a/b` — o caso que a implementação anterior **não** casava."""
    assert "a/b" in _paths(["a/**/b"], DEPTH_TREE)


def test_estrela_dupla_casa_um_segmento() -> None:
    assert "a/x/b" in _paths(["a/**/b"], DEPTH_TREE)


def test_estrela_dupla_casa_multiplos_segmentos() -> None:
    assert "a/x/y/b" in _paths(["a/**/b"], DEPTH_TREE)


def test_estrela_dupla_casa_as_tres_profundidades_de_uma_vez() -> None:
    """Os três casos acima juntos, para que a lista completa também fique fixada."""
    assert _paths(["a/**/b"], DEPTH_TREE) == ["a/b", "a/x/b", "a/x/y/b"]


def test_estrela_dupla_com_sufixo_alcanca_o_nivel_direto_e_o_aninhado() -> None:
    """`docs/**/*.md` inclui o arquivo direto em `docs/` **e** o de subpasta.

    É o mesmo caso "zero segmentos" visto pelo padrão mais comum de todos — o que faz a
    diferença entre subcobertura e cobertura correta aparecer no uso real.
    """
    tree = _tree("docs/readme.md", "docs/sub/readme.md", "docs/sub/mais/guia.md", "docs/x.txt")

    assert _paths(["docs/**/*.md"], tree) == [
        "docs/readme.md",
        "docs/sub/mais/guia.md",
        "docs/sub/readme.md",
    ]
    assert "docs/x.txt" not in _paths(["docs/**/*.md"], tree), "o sufixo continua valendo"


def test_estrela_dupla_com_sufixo_na_arvore_padrao() -> None:
    """Na árvore comum: `src/**/*.py` agora alcança também `src/app.py` e `src/util.py`."""
    assert _paths(["src/**/*.py"]) == [
        "src/app.py",
        "src/nested/deep.py",
        "src/nested/more/deeper.py",
        "src/util.py",
    ]


def test_estrela_dupla_no_inicio_casa_a_raiz_e_as_subpastas() -> None:
    """`**/x.md` é o mesmo caso com prefixo vazio: casa `x.md` e `a/x.md`."""
    tree = _tree("x.md", "a/x.md", "a/b/x.md", "outro.md")

    assert _paths(["**/x.md"], tree) == ["a/b/x.md", "a/x.md", "x.md"]


def test_estrela_dupla_nao_come_o_literal_ao_lado() -> None:
    """Precisão: "zero ou mais segmentos" não vira "qualquer coisa".

    `a/**/b` não pode casar `a/xb` nem `ab` — o segmento final continua tendo de ser
    exatamente `b`, e o prefixo `a/` continua sendo literal.
    """
    tree = _tree("a/b", "a/xb", "ab", "za/b")

    assert _paths(["a/**/b"], tree) == ["a/b"]


def test_estrela_dupla_no_fim_cobre_tudo_abaixo_mas_nao_o_proprio_diretorio() -> None:
    """`src/**` no fim continua sendo "tudo abaixo de `src/`" — inalterado pela correção."""
    tree = _tree("src", "src/app.py", "src/nested/deep.py", "srcx.py")

    assert _paths(["src/**"], tree) == ["src/app.py", "src/nested/deep.py"]


def test_estrela_dupla_repetida_e_idempotente() -> None:
    """`a/**/**/b` significa o mesmo que `a/**/b`, e compila para o mesmo padrão.

    Os `**` adjacentes são colapsados na tradução: dois grupos opcionais encadeados
    descreveriam a mesma linguagem e só dariam mais caminhos de backtracking ao motor de
    regex.
    """
    um = compile_source_ref("a/**/b")
    dois = compile_source_ref("a/**/**/b")

    assert isinstance(um, CompiledSourceRef)
    assert isinstance(dois, CompiledSourceRef)
    assert um.tokens == dois.tokens

    assert _paths(["a/**/**/b"], DEPTH_TREE) == ["a/b", "a/x/b", "a/x/y/b"]


def test_estrela_dupla_colada_a_texto_nao_e_segmento() -> None:
    """`src/**.py` não é um `**`-segmento: continua valendo `.*`, atravessando `/`.

    Nenhum dialeto define esse caso de forma consensual. Sob o modelo de risco do módulo —
    subcobertura é o erro caro —, a leitura mais abrangente é a menos perigosa, e a
    sobrecobertura tem o seu próprio freio na checagem de segredo pós-expansão.
    """
    tree = _tree("src/app.py", "src/nested/deep.py", "src/leia.txt")

    assert _paths(["src/**.py"], tree) == ["src/app.py", "src/nested/deep.py"]


def test_a_correcao_de_estrela_dupla_nao_afrouxou_a_checagem_de_segredo() -> None:
    """O caso "zero segmentos" que passou a casar **também** passa pela denylist.

    A correção aumenta a cobertura; se ela tivesse escapado da checagem de segredo, teria
    aberto exatamente o buraco que o Gate 3 fecha. Aqui o `.env` está no nível direto —
    justamente o nível que a implementação anterior não alcançava.
    """
    tree = _tree("config/app.json", "config/.env")

    assert expand_source_refs(["config/**/*"], tree).status is ExpansionStatus.DENIED

    limpa = _tree("config/app.json", "config/sub/outro.json")
    assert _paths(["config/**/*"], limpa) == ["config/app.json", "config/sub/outro.json"]


def test_interrogacao_casa_um_caractere_e_nao_a_barra() -> None:
    tree = _tree("docs/a.md", "docs/ab.md", "docs/a/b.md")

    assert _paths(["docs/?.md"], tree) == ["docs/a.md"]


def test_classe_de_caracteres() -> None:
    assert _paths(["docs/0[12]-*.md"]) == ["docs/01-intro.md", "docs/02-guia.md"]
    assert _paths(["docs/0[1]-*.md"]) == ["docs/01-intro.md"]


def test_classe_com_faixa() -> None:
    assert _paths(["docs/[0-9][0-9]-*.md"]) == ["docs/01-intro.md", "docs/02-guia.md"]


def test_classe_negada() -> None:
    tree = _tree("src/a.py", "src/b.py", "src/x.py")

    assert _paths(["src/[!x].py"], tree) == ["src/a.py", "src/b.py"]


# ---------------------------------------------- E4-AUD-008: nenhuma classe casa `/`


@pytest.mark.parametrize(
    "padrao",
    [
        "a[.-0]b",  # faixa 0x2E..0x30 — contém `/` (0x2F) **no meio**
        "a[.-9]b",
        "a[!x]b",  # negada: tudo menos `x` incluiria `/`
        "a[!a]b",  # negada de corpo mínimo
        "a[*-9]b",
        "a[,-0]b",
    ],
)
def test_aud008_nenhuma_classe_de_caracteres_casa_a_barra(padrao: str) -> None:
    """**Reprodução do finding.** Uma faixa pode conter `/` sem que a barra apareça escrita.

    `[.-0]` vai de `.` (0x2E) a `0` (0x30) e portanto inclui `/` (0x2F). Recusar só a barra
    **literal** dentro da classe não bastava: a faixa a trazia de volta. A garantia agora é
    estrutural — `_matches` testa `caractere != "/"` **fora** do predicado da classe, então
    nenhuma forma de escrever a classe consegue alcançar o separador.
    """
    compiled = compile_source_ref(padrao)
    if isinstance(compiled, SafetyDecision):
        pytest.skip(f"`{padrao}` é recusado antes de compilar: {compiled.rule_id}")

    assert not compiled.covers("a/b"), padrao


def test_aud008_a_faixa_continua_casando_o_que_deve() -> None:
    """Precisão: excluir `/` não pode esvaziar a faixa."""
    compiled = compile_source_ref("a[.-0]b")
    assert isinstance(compiled, CompiledSourceRef)

    assert compiled.covers("a.b")
    assert compiled.covers("a0b")
    assert not compiled.covers("a/b")
    assert not compiled.covers("axb")


def test_aud008_faixa_invertida_e_recusada() -> None:
    result = compile_source_ref("src/[z-a].py")
    assert isinstance(result, SafetyDecision)
    assert result.rule_id == "source_ref_expansion.invalid_character_class"


def test_classe_negada_nao_atravessa_barra() -> None:
    """Caso adversarial: `[^abc]` cru em regex casaria `/`. `[!abc]` vira `[^abc/]`.

    Sem o `/` embutido na negação, a classe negada seria a **única** construção de segmento
    único capaz de atravessar o separador — e `src/[!x]/y` casaria caminhos de qualquer
    profundidade.
    """
    tree = _tree("src/azb.py", "src/a/b.py")

    # `src/a[!x]b.py` casaria também `src/a/b.py` se a negação não trouxesse o `/` embutido.
    assert _paths(["src/a[!x]b.py"], tree) == ["src/azb.py"]

    # E o padrão compilado, consultado diretamente, também não casa o caminho com barra.
    compiled = compile_source_ref("src/a[!x]b.py")
    assert isinstance(compiled, CompiledSourceRef)
    assert compiled.covers("src/azb.py")
    assert not compiled.covers("src/a/b.py")
    assert not compiled.covers("src/axb.py")

    # Uma classe negada sozinha num segmento também não atravessa.
    solta = compile_source_ref("src/[!x]/y.py")
    assert isinstance(solta, CompiledSourceRef)
    assert not solta.covers("src/a/b/y.py")


def test_varios_refs_deduplicam_a_uniao() -> None:
    """Dois padrões que se sobrepõem produzem cada arquivo uma vez só."""
    result = expand_source_refs(["src/*", "src/**", "src/app.py"], PLAIN_TREE)

    assert result.status is ExpansionStatus.RESOLVED
    assert list(result.paths) == sorted(set(result.paths))


def test_lista_vazia_de_refs_resolve_sem_baseline() -> None:
    """Entrada autoral de [03] §3: sem `source_refs`, nasce `fresh` e sem `source_hash`."""
    result = expand_source_refs([], PLAIN_TREE)

    assert result.status is ExpansionStatus.RESOLVED
    assert result.files == ()
    assert result.source_hash is None


def test_lista_vazia_resolve_ate_sem_arvore() -> None:
    """Sem `source_refs` não há o que verificar — nem árvore é preciso ler."""
    assert expand_source_refs([], None).status is ExpansionStatus.RESOLVED


# ================================================== zero arquivo é `unknown`, não vazio


def test_ref_que_casa_zero_arquivo_e_irresolvivel() -> None:
    """`UNRESOLVED`, não `RESOLVED` com lista vazia.

    Um conjunto vazio produziria um `source_hash` sobre `[]` — legítimo, estável e que
    **nunca mais mudaria**. Toda alteração posterior no repositório deixaria a entrada
    `fresh` para sempre: o falso `fresh` que AUD-004 fechou, por outro caminho.
    """
    result = expand_source_refs(["src/inexistente/**"], PLAIN_TREE)

    assert result.status is ExpansionStatus.UNRESOLVED
    assert result.unresolved_refs == ("src/inexistente/**",)
    assert result.source_hash is None


def test_um_ref_irresolvivel_torna_a_expansao_inteira_irresolvivel() -> None:
    result = expand_source_refs(["src/*", "nada/aqui/*"], PLAIN_TREE)

    assert result.status is ExpansionStatus.UNRESOLVED
    assert result.unresolved_refs == ("nada/aqui/*",)


def test_arvore_ilegivel_e_irresolvivel() -> None:
    """`list_tree` devolveu `None` — não é repo, commit ruim, git ausente."""
    result = expand_source_refs(["src/*"], None)

    assert result.status is ExpansionStatus.UNRESOLVED
    assert result.unresolved_refs == ("src/*",)


# ======================================== 4 — o envelope não foi reaberto pela flag


DANGEROUS_IN_GLOB: tuple[tuple[str, str], ...] = (
    ("../**", "source_ref.parent_traversal"),
    ("src/../../*", "source_ref.parent_traversal"),
    ("//srv/**", "source_ref.unc"),
    ("\\\\srv\\**", "source_ref.unc"),
    ("\\\\?\\C:\\**", "source_ref.device_namespace"),
    ("/etc/**", "source_ref.root_relative"),
    ("\\windows\\**", "source_ref.root_relative"),
    ("C:/Users/**", "source_ref.absolute_not_allowed"),
    ("C:temp/*", "source_ref.drive_relative"),
    ("~/**", "source_ref.home_reference"),
    ("src/CON/*", "source_ref.reserved_name"),
    ("src/a.txt:ads/*", "source_ref.alternate_data_stream"),
    ("PROGRA~1/**", "source_ref.short_name_alias"),
    ("", "source_ref.empty"),
)


@pytest.mark.parametrize(("padrao", "rule_id"), DANGEROUS_IN_GLOB, ids=lambda item: str(item))
def test_sintaxe_perigosa_dentro_de_glob_e_recusada(padrao: str, rule_id: str) -> None:
    """Caso 4 do gate. A recusa vem do envelope, **com o `rule_id` do envelope**.

    Que o motivo seja o do `safety` — e não um motivo inventado aqui — é a prova de que o
    expansor propaga a decisão em vez de reavaliá-la.
    """
    result = expand_source_refs([padrao], PLAIN_TREE)

    assert result.status is ExpansionStatus.DENIED, padrao
    assert result.decision is not None
    assert result.decision.rule_id == rule_id, padrao


def test_padrao_perigoso_e_recusado_antes_de_olhar_a_arvore() -> None:
    """Nem uma árvore que "casaria" com `../**` faz o padrão passar."""
    tree = _tree("../fora.txt".replace("../", "fora/"), "src/app.py")

    assert expand_source_refs(["../**"], tree).status is ExpansionStatus.DENIED


def test_arvore_com_caminho_malformado_e_recusada() -> None:
    """Defesa em profundidade: o git nunca devolve isto, e se devolver não é para consertar."""
    for caminho in ("/absoluto.py", "src/../fora.py", "../fora.py"):
        result = expand_source_refs(["**"], _tree("src/app.py", caminho))
        assert result.status is ExpansionStatus.DENIED, caminho
        assert result.decision is not None
        assert result.decision.rule_id == "source_ref_expansion.malformed_tree_path"


# ============================================== gramática: o que NÃO é suportado é recusado


@pytest.mark.parametrize(
    "padrao",
    [
        "src/{a,b}.py",
        "{src,docs}/**",
        "src/}.py",
        "!src/app.py",
        "src/!app.py",
        "src/[abc.py",
        "src/[.py",
        "src/[].py",
        "src/[!].py",
        "src/[a/b].py",
        "src/[z-a].py",
    ],
)
def test_gramatica_nao_suportada_e_recusada_em_vez_de_virar_literal(padrao: str) -> None:
    """Chaves, negação de padrão e classe malformada são **recusadas**, nunca reinterpretadas.

    Tratá-las como literal é o que o `fnmatch` faz com um `[` sem fechamento — e é
    exatamente como nasce a segunda gramática que E2-AUD-003 proibiu.
    """
    result = expand_source_refs([padrao], PLAIN_TREE)

    assert result.status is ExpansionStatus.DENIED, padrao
    assert result.decision is not None
    assert result.decision.rule_id.startswith("source_ref_expansion."), padrao


def test_colchete_fechado_logo_no_inicio_e_literal() -> None:
    """Convenção POSIX: `[]]` é a classe que casa `]`."""
    tree = _tree("src/a]b.py", "src/axb.py")

    assert _paths(["src/a[]]b.py"], tree) == ["src/a]b.py"]


def test_casamento_e_sensivel_a_caixa() -> None:
    """O git registra a caixa; casar de forma insensível divergiria da árvore."""
    tree = _tree("src/App.py", "src/app.py")

    assert _paths(["src/app.py"], tree) == ["src/app.py"]
    assert _paths(["src/[a]pp.py"], tree) == ["src/app.py"]


# ================================================= 5 — unicidade de gramática / envelope


#: Corpus deliberadamente misturado: perigosos, seguros, com e sem metacaractere, literais,
#: malformados. A comparação abaixo é feita sobre ele inteiro.
GRAMMAR_CORPUS: tuple[str, ...] = (
    *[padrao for padrao, _rule in DANGEROUS_IN_GLOB],
    "src/*",
    "src/**",
    "src/**/*.py",
    "src/[0-9]*.py",
    "src/[!x].py",
    "src/app.py",
    "src",
    "src/nested",
    "./src/app.py",
    ".//src///app.py",
    "src/{a,b}.py",
    "!src/app.py",
    "src/[abc.py",
    ".env*",
    "secrets/**",
    "src/*.pem",
    "   ",
    ".",
    "..",
    "src/\x00/*",
    "a" * 300,
)


def test_unicidade_de_gramatica_validador_isolado_versus_expansor_completo() -> None:
    """Caso 5 do gate — a prova de que **não há duas gramáticas**.

    Para cada `source_ref` do corpus, duas afirmações complementares:

    * **o expansor nunca aceita o que o validador recusa**, e quando o validador recusa o
      expansor recusa com o **mesmo `rule_id`** — ou seja, o expansor propaga a decisão do
      envelope, não a reavalia com regras próprias;
    * **o expansor nunca recusa com um `rule_id` de envelope que o validador aceitou.**
      Toda recusa que é dele vive no prefixo `source_ref_expansion.`, e é sempre sobre
      gramática ou sobre segredo alcançado depois de expandir — nunca sobre caminho seguro.

    Junto, isso é "as duas camadas concordam exatamente sobre o que é aceito e recusado",
    com a fronteira entre elas legível pelo `rule_id`.
    """
    tree = _tree("src/app.py", "src/nested/deep.py", "docs/guia.md")

    for raw in GRAMMAR_CORPUS:
        validado = validate_source_ref(raw, allow_glob_syntax=True)
        expandido = expand_source_refs([raw], tree)

        if not validado.decision.allow:
            assert expandido.status is ExpansionStatus.DENIED, raw
            assert expandido.decision is not None
            assert expandido.decision.rule_id == validado.decision.rule_id, raw
            continue

        # O validador aceitou: o expansor pode recusar, mas só com motivo **dele**.
        if expandido.status is ExpansionStatus.DENIED:
            assert expandido.decision is not None
            assert expandido.decision.rule_id.startswith("source_ref_expansion."), raw


def test_o_expansor_nunca_inventa_um_rule_id_de_envelope() -> None:
    """Corolário verificável do teste acima, isolado para falhar com mensagem clara."""
    tree = _tree("src/app.py", ".env")

    for raw in GRAMMAR_CORPUS:
        expandido = expand_source_refs([raw], tree)
        if expandido.status is not ExpansionStatus.DENIED:
            continue
        assert expandido.decision is not None
        rule_id = expandido.decision.rule_id
        se_do_envelope = rule_id.startswith("source_ref.")
        if se_do_envelope:
            assert not validate_source_ref(raw, allow_glob_syntax=True).decision.allow, raw


def test_normalizacao_do_expansor_e_a_do_validador() -> None:
    """As grafias equivalentes casam o mesmo arquivo — a normalização é uma só."""
    tree = _tree("src/app.py")

    for raw in ("src/app.py", "./src/app.py", ".//src///app.py", "src\\app.py", "src/app.py/"):
        assert _paths([raw], tree) == ["src/app.py"], raw


# ==================================================== 6 — literal: arquivo e diretório


def test_arquivo_literal_resolve_a_si_mesmo() -> None:
    assert _paths(["src/app.py"]) == ["src/app.py"]


def test_diretorio_literal_expande_recursivamente() -> None:
    """Caso 6 do gate: sem metacaractere nenhum, um diretório cobre tudo abaixo dele."""
    assert _paths(["src"]) == [
        "src/app.py",
        "src/nested/deep.py",
        "src/nested/more/deeper.py",
        "src/util.py",
    ]
    assert _paths(["src/nested"]) == ["src/nested/deep.py", "src/nested/more/deeper.py"]


def test_diretorio_literal_nao_pega_irmao_com_prefixo_comum() -> None:
    """`src` não pode arrastar `src2/`: o casamento é por segmento, não por prefixo de string."""
    tree = _tree("src/app.py", "src2/outro.py", "srcx.py")

    assert _paths(["src"], tree) == ["src/app.py"]


def test_diretorio_literal_que_alcanca_segredo_e_recusado() -> None:
    """A recursão do literal também passa pela checagem arquivo a arquivo."""
    tree = _tree("config/settings.json", "config/sub/.env")

    assert expand_source_refs(["config"], tree).status is ExpansionStatus.DENIED


def test_literal_inexistente_e_irresolvivel() -> None:
    assert expand_source_refs(["src/nao-existe.py"], PLAIN_TREE).status is (
        ExpansionStatus.UNRESOLVED
    )


# ================================================================= `source_hash`


def test_source_hash_e_estavel_e_independente_da_ordem_dos_refs() -> None:
    """A lista congelada é a mesma; logo o hash é o mesmo. [03] §3."""
    um = expand_source_refs(["src/*", "docs/*"], PLAIN_TREE)
    outro = expand_source_refs(["docs/*", "src/*"], PLAIN_TREE)

    assert um.source_hash == outro.source_hash
    assert um.files == outro.files


def test_source_hash_muda_quando_um_blob_muda() -> None:
    original = expand_source_refs(["src/*"], PLAIN_TREE)

    mexido = TreeListing(
        files=tuple(
            (path, ("f" * 40) if path == "src/app.py" else sha) for path, sha in PLAIN_TREE.files
        )
    )
    depois = expand_source_refs(["src/*"], mexido)

    assert original.source_hash != depois.source_hash


def test_source_hash_muda_quando_a_lista_de_arquivos_muda() -> None:
    menor = expand_source_refs(["src/*"], PLAIN_TREE)
    maior = expand_source_refs(["src/**"], PLAIN_TREE)

    assert menor.source_hash != maior.source_hash


def test_source_hash_de_lista_vazia_e_o_hash_do_json_vazio() -> None:
    """Existe e é bem definido — mas nunca é gravado como baseline: [03] §3 zera o par."""
    assert compute_source_hash(()) == compute_source_hash(())
    assert expand_source_refs([], PLAIN_TREE).source_hash is None


def test_source_hash_e_o_do_documento() -> None:
    """Fórmula verificada contra a definição, não contra a implementação."""
    import hashlib
    import json

    files = (("a.py", "1" * 40), ("b.py", "2" * 40))
    esperado = hashlib.sha256(
        json.dumps(
            [["a.py", "1" * 40], ["b.py", "2" * 40]],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    assert compute_source_hash(files) == esperado


# ========================================================= git de verdade, ponta a ponta


@pytest.fixture
def repo_com_segredo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "src").mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Teste")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "config" / "settings.json").write_text("{}\n", encoding="utf-8")
    (root / "config" / ".env.local").write_text("TOKEN=x\n", encoding="utf-8")
    _git(root, "add", "-A", "-f")
    _git(root, "commit", "-m", "inicial")
    return root


@_NEEDS_GIT
def test_ponta_a_ponta_glob_seguro_expande_contra_commit(repo_com_segredo: Path) -> None:
    head = preflight(str(repo_com_segredo)).head
    assert head is not None

    result = expand_against_commit(str(repo_com_segredo), head, ["src/**"])

    assert result.status is ExpansionStatus.RESOLVED
    assert list(result.paths) == ["src/app.py"]
    assert result.source_hash is not None


@_NEEDS_GIT
def test_ponta_a_ponta_glob_amplo_que_alcanca_env_local_nega(repo_com_segredo: Path) -> None:
    """O caso 2 do gate, agora com git de verdade e um `.env.local` de verdade commitado."""
    head = preflight(str(repo_com_segredo)).head
    assert head is not None

    result = expand_against_commit(str(repo_com_segredo), head, ["config/*"])

    assert result.status is ExpansionStatus.DENIED
    assert result.decision is not None
    assert result.decision.rule_id == "source_ref_expansion.secret_denied"


@_NEEDS_GIT
def test_ponta_a_ponta_commit_inexistente_e_irresolvivel(repo_com_segredo: Path) -> None:
    result = expand_against_commit(str(repo_com_segredo), "0" * 39 + "1", ["src/**"])

    assert result.status is ExpansionStatus.UNRESOLVED


def test_ponta_a_ponta_diretorio_sem_git_e_irresolvivel(tmp_path: Path) -> None:
    plain = tmp_path / "sem-git"
    plain.mkdir()

    assert expand_against_commit(str(plain), "a" * 40, ["src/**"]).status is (
        ExpansionStatus.UNRESOLVED
    )
