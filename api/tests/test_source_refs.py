"""Validação de `source_refs` — fundação do Context Registry (E4).

Política da E2: **somente caminho literal relativo**. Qualquer sintaxe de glob é recusada
até que o expansor canônico do Context Engine exista e as duas semânticas — validador e
expansor — possam ser a mesma.
"""

from __future__ import annotations

import pytest

from app.safety.source_refs import (
    GLOB_METACHARACTERS,
    find_glob_metacharacters,
    normalize_source_ref,
    validate_source_ref,
    validate_source_refs,
)


@pytest.mark.parametrize(
    "value",
    [
        "app/db/models.py",
        "src/main.py",
        "src/app/main.py",
        "src/nested/deep.txt",
        "README.md",
        "docs/architecture/01-v1-architecture.md",
    ],
)
def test_literal_safe_source_ref_is_allowed(value: str) -> None:
    resultado = validate_source_ref(value)

    assert resultado.decision.allow
    assert resultado.normalized == value


@pytest.mark.parametrize(
    ("value", "rule"),
    [
        ("", "source_ref.empty"),
        ("../fora/arquivo.py", "source_ref.parent_traversal"),
        ("src/../../fora", "source_ref.parent_traversal"),
        # `source_ref` é sempre relativo: `/x` é lido com a semântica mais estrita
        # (root-relative) em qualquer sistema, para o motivo não variar por plataforma.
        ("/etc/passwd", "source_ref.root_relative"),
        ("C:\\Windows\\notepad.exe", "source_ref.absolute_not_allowed"),
        ("C:segredo", "source_ref.drive_relative"),
        ("//servidor/share/x", "source_ref.unc"),
        ("\\\\?\\C:\\x", "source_ref.device_namespace"),
        ("~/segredos/chave", "source_ref.home_reference"),
        ("src/CON", "source_ref.reserved_name"),
        ("src/a.txt:fluxo", "source_ref.alternate_data_stream"),
    ],
)
def test_source_refs_perigosos_sao_recusados(value: str, rule: str) -> None:
    resultado = validate_source_ref(value)

    assert not resultado.decision.allow
    assert resultado.decision.rule_id == rule
    assert resultado.normalized is None


@pytest.mark.parametrize(
    "value",
    [".env", ".env.local", ".envrc", "certs/server.key", "secrets/token.txt", ".ssh/id_rsa"],
)
def test_source_ref_literal_para_segredo_e_recusado(value: str) -> None:
    resultado = validate_source_ref(value)

    assert not resultado.decision.allow
    assert resultado.decision.rule_id == "source_ref.secret_denied"


def test_normalizacao_e_determinista() -> None:
    variantes = ["src\\app.py", "./src/app.py", "src//app.py", "  src/app.py  "]

    normalizados = {normalize_source_ref(value) for value in variantes}

    assert normalizados == {"src/app.py"}


def test_lista_normalizada_e_ordenada_e_sem_duplicata() -> None:
    """E4 precisa de ordem estável para calcular `source_hash` de forma reproduzível."""
    aceitos, recusados = validate_source_refs(
        ["src/b.py", "src/a.py", "./src/a.py", "../fora", ".env"]
    )

    assert aceitos == ["src/a.py", "src/b.py"]
    assert len(recusados) == 2


def test_mesma_entrada_produz_mesma_saida() -> None:
    entrada = ["docs/guia.md", "src/app.py", "app/db/models.py"]

    primeiro, _ = validate_source_refs(entrada)
    segundo, _ = validate_source_refs(list(reversed(entrada)))

    assert primeiro == segundo


def test_metacaracteres_reconhecidos_sao_os_documentados() -> None:
    assert set(GLOB_METACHARACTERS) == set("*?[]{}!")


def test_deteccao_de_metacaractere_preserva_ordem_e_nao_repete() -> None:
    assert find_glob_metacharacters("src/*[a]*.py") == ["*", "[", "]"]
    assert find_glob_metacharacters("src/app.py") == []
