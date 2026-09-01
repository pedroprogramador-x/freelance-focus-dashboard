"""Safety Kernel: decisões puras sobre path.

Os `PathFacts` aqui são construídos à mão. É o ponto: a política decide **sem tocar o
filesystem**, então testá-la não exige filesystem nenhum — e o teste roda igual em
qualquer sistema operacional.
"""

from __future__ import annotations

import pytest

from app.safety import (
    ObjectIdentity,
    PathFacts,
    PathIntent,
    SafetyPolicy,
    Tri,
    decide_path,
    decide_post_open,
    policy_hash,
    prevalidate_path_syntax,
)
from app.safety.policy import SafetyPolicyOverride


def facts(**overrides: object) -> PathFacts:
    """Fatos de um arquivo comum, contido e sem link. Testes alteram só o que importa."""
    base: dict[str, object] = {
        "requested_path": "src/app.py",
        "canonical_root": "/ws",
        "canonical_target": "/ws/src/app.py",
        "exists": True,
        "parent_identity": ObjectIdentity(1, 10),
        "target_identity": ObjectIdentity(1, 11),
        "volume": "1",
        "root_volume": "1",
        "is_symlink": Tri.FALSE,
        "is_junction": Tri.FALSE,
        "is_reparse_point": Tri.FALSE,
        "is_unc": Tri.FALSE,
        "is_device_namespace": Tri.FALSE,
        "is_drive_relative": Tri.FALSE,
        "contained": Tri.TRUE,
        "ancestor_link_outside_root": Tri.FALSE,
    }
    base.update(overrides)
    return PathFacts(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------- pré-validação


@pytest.mark.parametrize(
    ("value", "rule"),
    [
        ("", "path.empty"),
        ("   ", "path.empty"),
        ("src/\x00evil", "path.nul_byte"),
        ("src/\x07bell", "path.control_char"),
        ("../etc/passwd", "path.parent_traversal"),
        ("src/../../etc/passwd", "path.parent_traversal"),
        ("~/segredo", "path.home_reference"),
        ("\\\\servidor\\share\\x", "path.unc"),
        ("//servidor/share/x", "path.unc"),
        ("\\\\?\\C:\\Windows", "path.device_namespace"),
        ("\\\\.\\PhysicalDrive0", "path.device_namespace"),
        ("C:arquivo.txt", "path.drive_relative"),
        ("C:\\Windows\\System32", "path.absolute_not_allowed"),
        ("\\etc\\passwd", "path.root_relative"),
        ("/etc/passwd", "path.root_relative"),
        ("src/CON", "path.reserved_name"),
        ("src/nul.txt", "path.reserved_name"),
        ("src/COM1.log", "path.reserved_name"),
        ("src/arquivo.txt:fluxo", "path.alternate_data_stream"),
        ("src/pasta./x", "path.trailing_dot_or_space"),
        ("src/pasta /x", "path.trailing_dot_or_space"),
        ("PROGRA~1/app.exe", "path.short_name_alias"),
    ],
)
def test_sintaxe_recusada(value: str, rule: str) -> None:
    # Semântica Windows explícita: `/foo` é root-relative lá e absoluto em POSIX. Fixar o
    # parâmetro faz o teste afirmar a mesma coisa em qualquer sistema onde a suíte rode.
    decision = prevalidate_path_syntax(value, windows_semantics=True)

    assert not decision.allow
    assert decision.rule_id == rule


@pytest.mark.parametrize(
    "value",
    ["src/app.py", "src/nested/deep.txt", "./src/app.py", "a.py", "src\\windows\\style.py"],
)
def test_sintaxe_aceita(value: str) -> None:
    assert prevalidate_path_syntax(value).allow


def test_absoluto_aceito_apenas_quando_explicitamente_permitido() -> None:
    """`local_path` de workspace e raiz de worktree são os únicos casos legítimos.

    E `allow_absolute` libera **só** a forma absoluta qualificada — nunca root-relative,
    drive-relative, UNC ou device namespace (E2-AUD-005).
    """
    assert not prevalidate_path_syntax("/ws/projeto", windows_semantics=False).allow
    assert prevalidate_path_syntax(
        "/ws/projeto", allow_absolute=True, windows_semantics=False
    ).allow

    assert not prevalidate_path_syntax("C:\\ws\\projeto", windows_semantics=True).allow
    assert prevalidate_path_syntax(
        "C:\\ws\\projeto", allow_absolute=True, windows_semantics=True
    ).allow


def test_limite_de_tamanho_do_caminho() -> None:
    longo = "a/" * 200

    assert not prevalidate_path_syntax(longo).allow
    assert prevalidate_path_syntax(longo).rule_id == "path.too_long"


# ----------------------------------------------------------------- decide_path


def test_caminho_contido_e_permitido() -> None:
    decision = decide_path(facts())

    assert decision.allow
    assert decision.rule_id == "path.allowed"


def test_escape_da_raiz_e_negado() -> None:
    decision = decide_path(facts(contained=Tri.FALSE, canonical_target="/outside/loot.txt"))

    assert not decision.allow
    assert decision.rule_id == "path.escapes_root"


def test_contencao_nao_verificada_fecha() -> None:
    """Dúvida sobre contenção nunca vira permissão."""
    decision = decide_path(facts(contained=Tri.UNKNOWN))

    assert not decision.allow
    assert decision.rule_id == "path.containment_unverified"


def test_volume_diferente_e_negado() -> None:
    decision = decide_path(facts(volume="2", root_volume="1"))

    assert not decision.allow
    assert decision.rule_id == "path.cross_volume"


def test_ancestral_com_link_para_fora_e_negado() -> None:
    """Um `node_modules/x -> C:\\` no meio do caminho passaria por checagem só do final."""
    decision = decide_path(facts(ancestor_link_outside_root=Tri.TRUE))

    assert not decision.allow
    assert decision.rule_id == "path.ancestor_link_escapes_root"


@pytest.mark.parametrize(
    ("field", "rule"),
    [
        ("is_symlink", "path.symlink_denied"),
        ("is_junction", "path.junction_denied"),
        ("is_reparse_point", "path.reparse_point_denied"),
    ],
)
def test_links_sao_negados_por_padrao(field: str, rule: str) -> None:
    decision = decide_path(facts(**{field: Tri.TRUE}))

    assert not decision.allow
    assert decision.rule_id == rule


@pytest.mark.parametrize(
    ("field", "rule"),
    [
        ("is_symlink", "path.symlink_unverified"),
        ("is_junction", "path.junction_unverified"),
        ("is_reparse_point", "path.reparse_point_unverified"),
        ("ancestor_link_outside_root", "path.ancestor_link_unverified"),
    ],
)
def test_estado_de_link_nao_verificado_fecha(field: str, rule: str) -> None:
    """`UNKNOWN` significa *não verifiquei*, e a política fecha — nunca assume `FALSE`."""
    decision = decide_path(facts(**{field: Tri.UNKNOWN}))

    assert not decision.allow
    assert decision.rule_id == rule


def test_politica_pode_liberar_link_interno_explicitamente() -> None:
    permissiva = SafetyPolicy(allow_links_inside_root=True, require_verified_link_status=False)

    assert decide_path(facts(is_symlink=Tri.TRUE), policy=permissiva).allow


def test_erro_de_inspecao_fecha() -> None:
    decision = decide_path(facts(inspection_error="permissão negada"))

    assert not decision.allow
    assert decision.rule_id == "path.inspection_failed"


def test_segredo_dentro_da_raiz_e_negado() -> None:
    decision = decide_path(facts(requested_path=".env", canonical_target="/ws/.env"))

    assert not decision.allow
    assert decision.rule_id == "path.secret_denied"


def test_intencao_aparece_no_motivo() -> None:
    decision = decide_path(facts(), intent=PathIntent.WRITE)

    assert decision.allow
    assert "write" in decision.reason


# ------------------------------------------------------------ decide_post_open


def test_pos_abertura_aceita_identidade_estavel() -> None:
    identity = ObjectIdentity(1, 11)
    decision = decide_post_open(facts(target_identity=identity, post_open_identity=identity))

    assert decision.allow


def test_pos_abertura_detecta_troca_de_objeto() -> None:
    decision = decide_post_open(
        facts(target_identity=ObjectIdentity(1, 11), post_open_identity=ObjectIdentity(1, 99))
    )

    assert not decision.allow
    assert decision.rule_id == "path.toctou_recheck_failed"


def test_pos_abertura_sem_identidade_fecha() -> None:
    decision = decide_post_open(facts(post_open_identity=None))

    assert not decision.allow
    assert decision.rule_id == "path.post_open_unverified"


def test_pos_abertura_recusa_caminho_re_derivado_fora_da_raiz() -> None:
    identity = ObjectIdentity(1, 11)
    decision = decide_post_open(
        facts(
            target_identity=identity,
            post_open_identity=identity,
            post_open_target="/outside/loot.txt",
        )
    )

    assert not decision.allow
    assert decision.rule_id == "path.post_open_escapes_root"


# ---------------------------------------------------------------- composição


def test_override_so_restringe() -> None:
    base = SafetyPolicy(
        secret_allow_exceptions=frozenset({".env.example"}),
        require_verified_link_status=False,
        allow_links_inside_root=True,
        max_path_bytes=400,
    )

    apertado = base.compose(
        SafetyPolicyOverride(
            add_secret_patterns=("*.custom",),
            keep_secret_allow_exceptions=frozenset(),
            require_verified_link_status=True,
            allow_links_inside_root=False,
            max_path_bytes=120,
        )
    )

    assert "*.custom" in apertado.secret_patterns
    assert apertado.secret_allow_exceptions == frozenset()
    assert apertado.require_verified_link_status is True
    assert apertado.allow_links_inside_root is False
    assert apertado.max_path_bytes == 120


def test_override_nao_consegue_afrouxar() -> None:
    base = SafetyPolicy()

    tentativa = base.compose(
        SafetyPolicyOverride(
            require_verified_link_status=False,
            allow_links_inside_root=True,
            max_path_bytes=100_000,
        )
    )

    assert tentativa.require_verified_link_status is True
    assert tentativa.allow_links_inside_root is False
    assert tentativa.max_path_bytes == base.max_path_bytes


def test_policy_hash_e_estavel_e_sensivel() -> None:
    base = SafetyPolicy()

    assert policy_hash(base) == policy_hash(SafetyPolicy())
    assert policy_hash(base) != policy_hash(SafetyPolicy(max_path_bytes=255))
