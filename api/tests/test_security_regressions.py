"""Regressões nomeadas dos blockers da auditoria E2.

Cada teste aqui **falha** contra a implementação anterior e passa depois da correção.
São a rede de segurança contra o defeito voltar por descuido.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import path_runtime
from app.path_runtime import inspect
from app.safety import PathForm, classify_path_form, decide_path, prevalidate_path_syntax
from app.safety.secrets import is_secret_path
from app.safety.source_refs import GLOB_METACHARACTERS, validate_source_ref
from app.safety.types import Tri
from tests.conftest import supports_symlinks

# ================================================================ E2-AUD-002
#
# A denylist normativa é `.env*`, um padrão só. A implementação anterior usava
# `.env` + `.env.*`, o que deixava passar tudo que não tem ponto depois de `env`.


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        ".env.production",
        # As três que escapavam antes:
        ".envrc",
        ".environment",
        ".envrc.bak",
        # E as mesmas em subdiretório:
        "src/.env",
        "src/.envrc",
        "foo/bar/.environment",
    ],
)
def test_dotenv_prefix_is_secret(path: str) -> None:
    assert is_secret_path(path), f"`{path}` casa `.env*` e precisa ser segredo"


def test_dotenv_prefix_nao_engole_nomes_legitimos() -> None:
    """`.env*` casa pelo **início do basename**, não por conter `env`."""
    assert not is_secret_path("envios/relatorio.txt")
    assert not is_secret_path("src/environment.ts")
    assert not is_secret_path("docs/env.md")


def test_env_example_segue_secreto_por_padrao() -> None:
    """Decisão mantida: o freeze diz `.env*`, e a E2 não abre exceção por conta própria."""
    assert is_secret_path(".env.example")


def test_classificacao_de_env_nao_abre_arquivo(tmp_path: Path) -> None:
    """Nenhum `.envrc` precisa existir para ser classificado — a decisão é por nome."""
    assert is_secret_path("nunca/criado/.envrc")
    assert not (tmp_path / ".envrc").exists()


# ================================================================ E2-AUD-003
#
# Duas tentativas anteriores falharam: sondas finitas (não provavam ausência) e prova por
# interseção de segmentos (a gramática do validador divergia da do expansor, padrões de
# caminho completo eram burláveis e classes `[]` nem estavam modeladas).
#
# Decisão da E2.3: **somente caminho literal relativo**. Glob volta com o expansor
# canônico do Context Engine (E4), quando houver uma única gramática.


@pytest.mark.parametrize(
    "glob",
    [
        # `*` e `**`
        "src/**",
        "docs/**",
        "foo/**",
        "**",
        "**/*",
        "*/**",
        "app/db/**",
        "src/*.py",
        "src/**/config.py",
        "src/.env*",
        "**/*secret*",
        "src/*secret*",
        "src/**/secret/config.py",
        # `?`
        "?/config.py",
        "src/a?.py",
        # classes de caractere
        "src/[s]ecret.py",
        "src/[.]env",
        "src/[abc].py",
        # expansão de chaves
        "src/{foo,bar}.py",
        "{a,b}/c.py",
        # negação
        "!src/main.py",
    ],
)
def test_source_ref_globs_are_fail_closed_in_e2(glob: str) -> None:
    """Qualquer sintaxe de glob é recusada — inclusive as que a prova anterior aprovava.

    `src/**/config.py` e `?/config.py` passavam na versão por interseção de segmentos.
    Eram "prováveis" segundo *aquela* gramática; nenhuma garantia de que o expansor real
    concordaria.
    """
    result = validate_source_ref(glob)

    assert not result.decision.allow, f"`{glob}` tem sintaxe de glob e precisa ser recusado"
    assert result.normalized is None


@pytest.mark.parametrize("metachar", sorted(GLOB_METACHARACTERS))
def test_cada_metacaractere_isolado_e_recusado(metachar: str) -> None:
    """Cobertura por caractere, para nenhum escapar quando o conjunto crescer."""
    result = validate_source_ref(f"src/arquivo{metachar}.py")

    assert not result.decision.allow
    assert result.decision.rule_id == "source_ref.glob_not_supported"


def test_glob_recusado_nomeia_os_metacaracteres_encontrados() -> None:
    result = validate_source_ref("src/*.py")

    assert result.decision.rule_id == "source_ref.glob_not_supported"
    assert "*" in result.decision.reason


def test_glob_nao_e_sanitizado_para_literal() -> None:
    """Transformar `src/*.py` num literal exigiria escolher uma gramática de escape."""
    assert validate_source_ref("src/*.py").normalized is None


@pytest.mark.parametrize(
    "glob",
    ["src/main.py", "src/app/main.py", "app/db/models.py", "README.md"],
)
def test_literal_safe_source_ref_is_allowed(glob: str) -> None:
    result = validate_source_ref(glob)

    assert result.decision.allow
    assert result.normalized == glob


def test_literal_secreto_continua_recusado_por_segredo_e_nao_por_glob() -> None:
    """O motivo mais grave prevalece: `.envrc` é segredo, não um problema de sintaxe."""
    result = validate_source_ref("src/.envrc")

    assert not result.decision.allow
    assert result.decision.rule_id == "source_ref.secret_denied"


def test_traversal_prevalece_sobre_glob() -> None:
    """`../**` tem os dois problemas; o registrado precisa ser a travessia."""
    result = validate_source_ref("../src/main.py")

    assert result.decision.rule_id == "source_ref.parent_traversal"


@pytest.mark.parametrize(
    ("glob", "rule"),
    [
        ("../**", "source_ref.parent_traversal"),
        ("/etc/**", "source_ref.root_relative"),
        ("\\\\server\\**", "source_ref.unc"),
        ("//server/share/**", "source_ref.unc"),
        ("\\\\?\\C:\\x", "source_ref.device_namespace"),
        ("C:segredo/**", "source_ref.drive_relative"),
        ("\\raiz\\**", "source_ref.root_relative"),
    ],
)
def test_source_ref_com_sintaxe_perigosa_e_negado(glob: str, rule: str) -> None:
    """Sintaxe de caminho é avaliada antes do glob: o motivo registrado é o mais grave."""
    result = validate_source_ref(glob)

    assert not result.decision.allow
    assert result.decision.rule_id == rule


# ================================================================ E2-AUD-004
#
# `inspect` resolvia o alvo e só então subia pelos pais — mas `resolve()` já tinha
# atravessado os links, então a cadeia inspecionada era a real, não a pedida.


def test_internal_symlink_is_not_erased_by_resolution(tmp_path: Path, workspace_root: Path) -> None:
    """Symlink **interno**: o alvo final continua na raiz, mas segue sendo um link.

    Este é o caso que a resolução prévia apagava: `src/atalho/deep.txt` resolvia para
    `src/nested/deep.txt` e o resultado dizia "nenhum link".
    """
    if not supports_symlinks(tmp_path):
        pytest.skip("symlink indisponível neste ambiente")

    (workspace_root / "src" / "atalho").symlink_to(
        workspace_root / "src" / "nested", target_is_directory=True
    )

    facts = inspect("src/atalho/deep.txt", workspace_root)

    assert facts.contained.is_true, "o alvo final está dentro da raiz"
    assert facts.is_symlink.is_true, "a evidência do link não pode desaparecer"
    assert facts.ancestor_link_outside_root.is_false, "este link não sai da raiz"

    decision = decide_path(facts)
    assert not decision.allow
    assert decision.rule_id == "path.symlink_denied"


def test_external_symlink_e_detectado_como_link_e_como_escape(
    tmp_path: Path, workspace_root: Path
) -> None:
    if not supports_symlinks(tmp_path):
        pytest.skip("symlink indisponível neste ambiente")

    fora = tmp_path / "outside"
    (fora / "sub").mkdir(parents=True, exist_ok=True)
    (fora / "sub" / "alvo.txt").write_text("x\n", encoding="utf-8")
    (workspace_root / "src" / "fuga").symlink_to(fora / "sub", target_is_directory=True)

    facts = inspect("src/fuga/alvo.txt", workspace_root)

    assert facts.is_symlink.is_true
    assert facts.ancestor_link_outside_root.is_true
    assert not decide_path(facts).allow


def test_symlink_intermediario_e_visto_mesmo_com_alvo_final_comum(
    tmp_path: Path, workspace_root: Path
) -> None:
    """O link está no **meio** do caminho, e o último componente é um arquivo comum."""
    if not supports_symlinks(tmp_path):
        pytest.skip("symlink indisponível neste ambiente")

    (workspace_root / "src" / "nested" / "sub").mkdir()
    (workspace_root / "src" / "nested" / "sub" / "f.txt").write_text("f\n", encoding="utf-8")
    (workspace_root / "ponte").symlink_to(
        workspace_root / "src" / "nested", target_is_directory=True
    )

    facts = inspect("ponte/sub/f.txt", workspace_root)

    assert facts.is_symlink.is_true
    assert not decide_path(facts).allow


def test_varredura_visita_os_componentes_lexicais_pedidos(
    monkeypatch: pytest.MonkeyPatch, workspace_root: Path
) -> None:
    """A prova de E2-AUD-004 que **não** depende de symlink real.

    Criar symlink no Windows exige privilégio, então os testes de integração acima são
    pulados nesta máquina (rodam na CI Linux). Este aqui ataca a causa direto: registra
    quais caminhos foram submetidos a `lstat` e exige que sejam **os componentes pedidos,
    de cima para baixo**.

    A implementação anterior resolvia o alvo e subia pelos pais do caminho **resolvido** —
    a ordem e o conjunto eram outros, e um link intermediário nunca era visitado.
    """
    visited: list[Path] = []

    def spy(path: Path) -> tuple[Tri, Tri, Tri]:
        visited.append(path)
        return Tri.FALSE, Tri.FALSE, Tri.FALSE

    monkeypatch.setattr(path_runtime, "_link_facts", spy)
    path_runtime.inspect("src/nested/deep.txt", workspace_root)

    assert [item.name for item in visited] == ["src", "nested", "deep.txt"]
    for item in visited:
        assert item.is_relative_to(workspace_root), "a varredura saiu da árvore pedida"


@pytest.mark.parametrize("component", ["src", "nested", "deep.txt"])
def test_link_simulado_em_qualquer_componente_e_detectado(
    monkeypatch: pytest.MonkeyPatch, workspace_root: Path, component: str
) -> None:
    """Um link em **qualquer** posição — primeira, intermediária ou última — é visto."""

    def fake(path: Path) -> tuple[Tri, Tri, Tri]:
        if path.name == component:
            return Tri.TRUE, Tri.FALSE, Tri.FALSE
        return Tri.FALSE, Tri.FALSE, Tri.FALSE

    monkeypatch.setattr(path_runtime, "_link_facts", fake)
    facts = path_runtime.inspect("src/nested/deep.txt", workspace_root)

    assert facts.is_symlink.is_true
    decision = decide_path(facts)
    assert not decision.allow
    assert decision.rule_id == "path.symlink_denied"


def test_junction_simulada_em_componente_intermediario_e_detectada(
    monkeypatch: pytest.MonkeyPatch, workspace_root: Path
) -> None:
    """Junction é conceito de NTFS e pode exigir privilégio; a lógica é testável assim."""

    def fake(path: Path) -> tuple[Tri, Tri, Tri]:
        if path.name == "nested":
            return Tri.FALSE, Tri.TRUE, Tri.TRUE
        return Tri.FALSE, Tri.FALSE, Tri.FALSE

    monkeypatch.setattr(path_runtime, "_link_facts", fake)
    facts = path_runtime.inspect("src/nested/deep.txt", workspace_root)

    assert facts.is_junction.is_true
    assert not decide_path(facts).allow


def test_link_desconhecido_em_um_componente_contamina_a_cadeia(
    monkeypatch: pytest.MonkeyPatch, workspace_root: Path
) -> None:
    """`UNKNOWN` num componente não pode virar `FALSE` no agregado."""

    def fake(path: Path) -> tuple[Tri, Tri, Tri]:
        if path.name == "nested":
            return Tri.UNKNOWN, Tri.UNKNOWN, Tri.UNKNOWN
        return Tri.FALSE, Tri.FALSE, Tri.FALSE

    monkeypatch.setattr(path_runtime, "_link_facts", fake)
    facts = path_runtime.inspect("src/nested/deep.txt", workspace_root)

    assert facts.is_symlink.is_unknown
    assert not decide_path(facts).allow


def test_link_confirmado_vence_componente_desconhecido(
    monkeypatch: pytest.MonkeyPatch, workspace_root: Path
) -> None:
    """Achar um link é fato; incerteza em outro componente não o apaga."""

    def fake(path: Path) -> tuple[Tri, Tri, Tri]:
        if path.name == "src":
            return Tri.TRUE, Tri.FALSE, Tri.FALSE
        return Tri.UNKNOWN, Tri.UNKNOWN, Tri.UNKNOWN

    monkeypatch.setattr(path_runtime, "_link_facts", fake)
    facts = path_runtime.inspect("src/nested/deep.txt", workspace_root)

    assert facts.is_symlink.is_true


def test_caminho_sem_link_permanece_limpo(workspace_root: Path) -> None:
    """A varredura léxica não pode gerar falso positivo em caminho normal."""
    facts = inspect("src/nested/deep.txt", workspace_root)

    assert facts.is_symlink.is_false
    assert facts.ancestor_link_outside_root.is_false
    assert decide_path(facts).allow


def test_estado_de_link_desconhecido_fecha_por_padrao() -> None:
    """Junction no Windows pode exigir API indisponível: `UNKNOWN`, nunca `FALSE`."""
    from tests.test_safety_paths import facts as build_facts

    decision = decide_path(build_facts(is_junction=Tri.UNKNOWN))

    assert not decision.allow
    assert decision.rule_id == "path.junction_unverified"


# ------------------------------------------------- truncamento no componente 128
#
# `_lexical_chain_facts` cortava a varredura em `parts[:128]`. Um link no componente 150
# não era inspecionado e os fatos voltavam **limpos** — `FALSE`, não `UNKNOWN` —, então a
# política liberava um caminho que ninguém tinha verificado.
#
# Estes testes chamam `_lexical_chain_facts` diretamente, e não `inspect`: uma cadeia com
# 200 componentes ultrapassa o `MAX_PATH` do Windows, e o `resolve()` interno de `inspect`
# tornaria o teste dependente de configuração do sistema. A função varrida é exatamente
# onde o defeito estava. Nada aqui depende de privilégio de symlink.


def _deep_chain(depth: int) -> str:
    return "/".join(f"d{index}" for index in range(depth)) + "/alvo.txt"


@pytest.mark.parametrize("depth", [129, 130, 256])
def test_lexical_chain_does_not_silently_truncate_after_128(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, depth: int
) -> None:
    visited: list[Path] = []

    def spy(path: Path) -> tuple[Tri, Tri, Tri]:
        visited.append(path)
        return Tri.FALSE, Tri.FALSE, Tri.FALSE

    monkeypatch.setattr(path_runtime, "_link_facts", spy)
    path_runtime._lexical_chain_facts(tmp_path, _deep_chain(depth), tmp_path)

    assert len(visited) == depth + 1, "todo componente pedido precisa ser inspecionado"
    assert visited[-1].name == "alvo.txt"


@pytest.mark.parametrize("position", [128, 129, 200])
def test_link_fact_after_component_128_cannot_disappear(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, position: int
) -> None:
    """Um link além do antigo corte precisa continuar visível para a política."""
    marcado = f"d{position}"

    def fake(path: Path) -> tuple[Tri, Tri, Tri]:
        if path.name == marcado:
            return Tri.TRUE, Tri.FALSE, Tri.FALSE
        return Tri.FALSE, Tri.FALSE, Tri.FALSE

    monkeypatch.setattr(path_runtime, "_link_facts", fake)
    chain = path_runtime._lexical_chain_facts(tmp_path, _deep_chain(256), tmp_path)

    assert chain.is_symlink.is_true, f"link em d{position} desapareceu da cadeia"


def test_junction_apos_128_tambem_e_preservada(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake(path: Path) -> tuple[Tri, Tri, Tri]:
        if path.name == "d199":
            return Tri.FALSE, Tri.TRUE, Tri.TRUE
        return Tri.FALSE, Tri.FALSE, Tri.FALSE

    monkeypatch.setattr(path_runtime, "_link_facts", fake)
    chain = path_runtime._lexical_chain_facts(tmp_path, _deep_chain(256), tmp_path)

    assert chain.is_junction.is_true
    assert chain.is_reparse_point.is_true


def test_incerteza_apos_128_nao_vira_falso(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`UNKNOWN` num componente profundo precisa contaminar o agregado, não sumir."""

    def fake(path: Path) -> tuple[Tri, Tri, Tri]:
        if path.name == "d180":
            return Tri.UNKNOWN, Tri.UNKNOWN, Tri.UNKNOWN
        return Tri.FALSE, Tri.FALSE, Tri.FALSE

    monkeypatch.setattr(path_runtime, "_link_facts", fake)
    chain = path_runtime._lexical_chain_facts(tmp_path, _deep_chain(256), tmp_path)

    assert chain.is_symlink.is_unknown


# ================================================================ E2-AUD-005
#
# Com `allow_absolute=True`, `\arquivo` era aceito. Ele não é absoluto: resolve contra o
# drive corrente do processo, que qualquer chamada pode mudar.


@pytest.mark.parametrize("value", ["\\file.txt", "\\folder\\file.txt", "\\"])
def test_windows_root_relative_is_rejected_even_when_absolute_allowed(value: str) -> None:
    negado = prevalidate_path_syntax(value, allow_absolute=True)

    assert not negado.allow
    assert negado.rule_id == "path.root_relative"


@pytest.mark.parametrize("value", ["/foo", "/folder/file", "/"])
def test_barra_inicial_e_root_relative_sob_semantica_windows(value: str) -> None:
    negado = prevalidate_path_syntax(value, allow_absolute=True, windows_semantics=True)

    assert not negado.allow
    assert negado.rule_id == "path.root_relative"


@pytest.mark.parametrize("value", ["/home/user/ws", "/srv/projeto"])
def test_barra_inicial_e_absoluto_qualificado_sob_semantica_posix(value: str) -> None:
    """Em POSIX o mesmo texto é um absoluto legítimo — e só então `allow_absolute` vale."""
    assert prevalidate_path_syntax(value, allow_absolute=True, windows_semantics=False).allow
    assert not prevalidate_path_syntax(value, allow_absolute=False, windows_semantics=False).allow


def test_absolute_qualified_continua_liberavel() -> None:
    assert prevalidate_path_syntax("C:\\workspace\\file.txt", allow_absolute=True).allow
    assert not prevalidate_path_syntax("C:\\workspace\\file.txt", allow_absolute=False).allow


@pytest.mark.parametrize(
    ("value", "rule"),
    [
        ("C:segredo.txt", "path.drive_relative"),
        ("\\\\servidor\\share\\x", "path.unc"),
        ("//servidor/share/x", "path.unc"),
        ("\\\\?\\C:\\Windows", "path.device_namespace"),
        ("\\\\.\\PhysicalDrive0", "path.device_namespace"),
    ],
)
def test_allow_absolute_nao_libera_as_outras_formas(value: str, rule: str) -> None:
    """`allow_absolute` libera **uma** forma; as demais continuam recusadas."""
    decision = prevalidate_path_syntax(value, allow_absolute=True)

    assert not decision.allow
    assert decision.rule_id == rule


@pytest.mark.parametrize(
    ("value", "windows", "expected"),
    [
        ("src/app.py", True, PathForm.RELATIVE),
        ("src/app.py", False, PathForm.RELATIVE),
        ("C:\\ws\\a", True, PathForm.ABSOLUTE_QUALIFIED),
        ("C:/ws/a", True, PathForm.ABSOLUTE_QUALIFIED),
        ("C:ws", True, PathForm.DRIVE_RELATIVE),
        ("\\ws\\a", True, PathForm.ROOT_RELATIVE),
        ("\\ws\\a", False, PathForm.ROOT_RELATIVE),
        ("/ws/a", True, PathForm.ROOT_RELATIVE),
        ("/ws/a", False, PathForm.ABSOLUTE_QUALIFIED),
        ("\\\\srv\\share", True, PathForm.UNC),
        ("//srv/share", True, PathForm.UNC),
        ("\\\\?\\C:\\x", True, PathForm.DEVICE_NAMESPACE),
        ("\\\\.\\PIPE", True, PathForm.DEVICE_NAMESPACE),
    ],
)
def test_classificacao_das_cinco_formas(value: str, windows: bool, expected: PathForm) -> None:
    assert classify_path_form(value, windows_semantics=windows) is expected
