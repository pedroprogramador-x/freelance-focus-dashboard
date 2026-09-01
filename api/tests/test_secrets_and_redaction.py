"""Classificação de segredos por caminho e redação de saída."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.safety.redaction import REDACTED, redact
from app.safety.secrets import SecretPolicy, SecretVerdict, classify_path_secrecy, is_secret_path


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        ".env.production",
        "sub/.env",
        "config/.env.staging",
        "chave.pem",
        "certs/server.key",
        "cert.p12",
        "cert.pfx",
        "id_rsa",
        "id_rsa.pub",
        "id_ed25519",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
        ".aws/credentials",
        ".ssh/id_rsa",
        "secrets/token.txt",
        "credentials.json",
        "deep/nested/credentials.yaml",
        "app-secret.yaml",
        "meu_secret_interno.txt",
    ],
)
def test_caminhos_secretos_sao_reconhecidos(path: str) -> None:
    assert is_secret_path(path), f"`{path}` deveria ser classificado como segredo"


@pytest.mark.parametrize(
    "path",
    [
        "src/app.py",
        "README.md",
        "docs/architecture/01-v1-architecture.md",
        "package.json",
        "envios/relatorio.txt",
        "keyboard.ts",
    ],
)
def test_caminhos_comuns_nao_sao_segredo(path: str) -> None:
    assert not is_secret_path(path)


def test_caixa_alta_nao_escapa_da_denylist() -> None:
    """Windows é case-insensitive: sem normalizar, `.ENV` passaria."""
    assert is_secret_path(".ENV")
    assert is_secret_path("SUB/.Env.Local")
    assert is_secret_path("Chave.PEM")


def test_env_example_e_segredo_por_padrao() -> None:
    """Decisão registrada: a denylist congelada é `.env*` e não abre exceção.

    A E2 **não** inventa a exceção por conta própria. Ela existe como mecanismo de
    configuração explícita, e nasce desligada.
    """
    assert is_secret_path(".env.example")

    liberado = SecretPolicy(allow_exceptions=frozenset({".env.example"}))
    assert not is_secret_path(".env.example", liberado)
    assert is_secret_path(".env", liberado), "a exceção não pode vazar para o `.env` real"


def test_classificacao_informa_o_padrao_que_casou() -> None:
    resultado = classify_path_secrecy("certs/server.key")

    assert resultado.verdict is SecretVerdict.SECRET
    assert resultado.matched_pattern == "*.key"


def test_classificacao_nao_le_o_arquivo(tmp_path: Path) -> None:
    """A decisão é por nome. Um caminho inexistente é classificado do mesmo jeito."""
    inexistente = "nunca/criado/.env"

    assert is_secret_path(inexistente)
    assert not (tmp_path / inexistente).exists()


# ------------------------------------------------------------------ redação


@pytest.mark.parametrize(
    "texto",
    [
        "Authorization: Bearer abcdef0123456789abcdef",
        "chave = sk-ant-api03-abcdefghijklmnop",
        "OPENAI = sk-abcdefghijklmnopqrstuvwxyz01",
        "token ghp_abcdefghijklmnopqrstuvwxyz0123",
        "aws AKIAIOSFODNN7EXAMPLE fim",
        "clone https://usuario:senha123@github.com/org/repo.git",
        "api_key: 'super-secreto-123'",
        "password=umaSenhaLonga",
    ],
)
def test_segredos_reconheciveis_sao_redigidos(texto: str) -> None:
    saida = redact(texto)

    assert REDACTED in saida


def test_bloco_de_chave_privada_e_redigido() -> None:
    texto = (
        "antes\n-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n"
        "-----END RSA PRIVATE KEY-----\ndepois"
    )

    saida = redact(texto)

    assert "MIIEpAIBAAKCAQEA" not in saida
    assert "antes" in saida
    assert "depois" in saida


def test_url_com_credencial_perde_so_a_credencial() -> None:
    saida = redact("https://usuario:senha@github.com/org/repo.git")

    assert "senha" not in saida
    assert "github.com/org/repo.git" in saida


def test_texto_sem_segredo_passa_intacto() -> None:
    texto = "src/app.py alterado: 3 linhas adicionadas, 1 removida"

    assert redact(texto) == texto


def test_redacao_e_idempotente() -> None:
    texto = "Authorization: Bearer abcdef0123456789abcdef"

    assert redact(redact(texto)) == redact(texto)
