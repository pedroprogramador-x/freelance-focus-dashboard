"""E7.5-A (fecho) — integridade sem decisão, identidade não verificável e `tolerate_absent_tail`.

1. As primitivas novas de `path_runtime` relatam falha de integridade como **fato**
   (`PathIntegrityViolation`); nunca constroem `SafetyDecision` nem levantam `PathAccessDenied`.
2. Identidade com `file_id == 0` não prova nada: fail closed onde a foundation depende dela.
3. `inspect(..., tolerate_absent_tail=True)` só relaxa **ausência comprovada**.

Só fixtures locais benignas num diretório temporário; "falhas" de `lstat` são simuladas por
`monkeypatch`, nunca por permissão real nem por helper externo.
"""

from __future__ import annotations

import ast
import errno
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from app import path_runtime
from app.path_runtime import (
    BoundRoot,
    IntegrityFailure,
    PathAccessDenied,
    PathFailure,
    PathIntegrityViolation,
    PathOperationFailed,
    bind_root,
    create_exclusive,
    delete_if_identity,
    discard_created,
    inspect,
    list_directory,
    open_checked,
    open_existing,
    verify_root,
)
from app.safety import (
    ObjectIdentity,
    PathFacts,
    SafetyDecision,
    Tri,
    decide_path,
    decide_post_create,
    prevalidate_mediated_path,
)
from app.safety.paths import PathIntent

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

if sys.platform == "win32":
    import _winapi

    def make_dir_link(link: Path, target: Path) -> bool:
        _winapi.CreateJunction(str(target), str(link))
        return True

else:

    def make_dir_link(link: Path, target: Path) -> bool:
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            return False
        return True


@pytest.fixture
def root(tmp_path: Path) -> BoundRoot:
    directory = tmp_path / "ws"
    directory.mkdir()
    return bind_root(directory)


def base(root: BoundRoot) -> Path:
    return Path(root.path)


def authorized(relative: str, root: BoundRoot, intent: PathIntent = PathIntent.WRITE) -> PathFacts:
    assert prevalidate_mediated_path(relative).allow
    facts = inspect(relative, root.path, tolerate_absent_tail=True)
    decision = decide_path(facts, intent=intent)
    assert decision.allow, decision
    return facts


# ========================================== A. integridade é fato, não decisão de política


def _novo_codigo() -> list[ast.AST]:
    """Os nós do módulo a partir da seção E7.5-A (`class PathFailure` em diante)."""
    tree = ast.parse((APP_ROOT / "path_runtime.py").read_text(encoding="utf-8"))
    inicio = next(
        n.lineno for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PathFailure"
    )
    return [n for n in tree.body if n.lineno >= inicio]


def test_o_codigo_novo_de_path_runtime_nao_constroi_decisao_nem_levanta_access_denied() -> None:
    nomes: set[str] = set()
    for node in _novo_codigo():
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                nomes.add(sub.id)
            elif isinstance(sub, ast.Attribute):
                nomes.add(sub.attr)
    assert "SafetyDecision" not in nomes
    assert "PathAccessDenied" not in nomes  # a docstring pode citar; o código não referencia


def test_violacao_de_integridade_e_um_fato_estruturado() -> None:
    violation = PathIntegrityViolation(IntegrityFailure.REPARSE_DETECTED)
    assert violation.category is IntegrityFailure.REPARSE_DETECTED
    assert str(violation) == "reparse_detected"
    assert not isinstance(violation, PathAccessDenied | PermissionError | OSError)
    assert not hasattr(violation, "decision")
    assert {c.value for c in IntegrityFailure} >= {
        "root_identity_changed",
        "parent_identity_changed",
        "target_identity_changed",
        "reparse_detected",
        "integrity_unverifiable",
    }


def test_o_open_checked_legado_continua_levantando_path_access_denied(root: BoundRoot) -> None:
    (base(root) / ".env").write_text("X=1", encoding="utf-8")
    with pytest.raises(PathAccessDenied) as caught, open_checked(".env", root.path):
        pass
    assert isinstance(caught.value.decision, SafetyDecision)
    assert caught.value.decision.rule_id == "path.secret_denied"


def test_o_legado_tambem_mantem_a_regra_antiga_para_alvo_inexistente(root: BoundRoot) -> None:
    """`ausente = UNKNOWN` continua sendo o comportamento padrão (E2-AUD)."""
    with pytest.raises(PathAccessDenied) as caught, open_checked("nada.txt", root.path):
        pass
    assert caught.value.decision.rule_id == "path.symlink_unverified"


# ========================================================= C. identidade não verificável


def zero_ids(monkeypatch: pytest.MonkeyPatch, *, keep: set[int] | None = None) -> None:
    """A plataforma passa a devolver `file_id == 0` — exceto nos inodes em ``keep``."""
    real = path_runtime._identity
    keep = keep or set()

    def fake(info: os.stat_result) -> ObjectIdentity:
        identity = real(info)
        return identity if info.st_ino in keep else ObjectIdentity(identity.volume_id, 0)

    monkeypatch.setattr(path_runtime, "_identity", fake)


def inode(path: Path) -> int:
    return os.stat(path).st_ino


def test_duas_identidades_zero_nunca_sao_prova() -> None:
    a, b = ObjectIdentity(7, 0), ObjectIdentity(7, 0)
    assert a == b  # a igualdade nominal existe...
    assert not a.is_verifiable and not b.is_verifiable
    assert path_runtime._same_identity(a, b) is False  # ...mas não vale como prova
    assert path_runtime._same_identity(ObjectIdentity(7, 3), ObjectIdentity(7, 3)) is True
    assert path_runtime._same_identity(ObjectIdentity(7, 3), ObjectIdentity(7, 4)) is False
    with pytest.raises(PathIntegrityViolation) as unverifiable:
        path_runtime._require_same_identity(a, b, IntegrityFailure.TARGET_IDENTITY_CHANGED)
    assert unverifiable.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE
    with pytest.raises(PathIntegrityViolation) as changed:
        path_runtime._require_same_identity(
            ObjectIdentity(7, 3), ObjectIdentity(7, 4), IntegrityFailure.TARGET_IDENTITY_CHANGED
        )
    assert changed.value.category is IntegrityFailure.TARGET_IDENTITY_CHANGED
    path_runtime._require_same_identity(
        ObjectIdentity(7, 3), ObjectIdentity(7, 3), IntegrityFailure.TARGET_IDENTITY_CHANGED
    )


def test_a_verificabilidade_e_um_unico_fato_centralizado() -> None:
    assert ObjectIdentity(1, 5).is_verifiable
    assert ObjectIdentity(0, 5).is_verifiable  # volume 0 é legítimo em alguns dispositivos
    assert not ObjectIdentity(1, 0).is_verifiable
    assert not ObjectIdentity(0, 0).is_verifiable


def test_bind_root_recusa_raiz_com_identidade_nao_verificavel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "ws"
    directory.mkdir()
    zero_ids(monkeypatch)
    with pytest.raises(PathIntegrityViolation) as caught:
        bind_root(directory)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE


def test_bind_root_recusa_identidade_esperada_zero_mesmo_igual_a_observada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "ws"
    directory.mkdir()
    real = bind_root(directory)
    zero = ObjectIdentity(real.identity.volume_id, 0)
    with pytest.raises(PathIntegrityViolation) as caught:
        bind_root(directory, expected_identity=zero)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE


def test_verify_root_recusa_quando_a_identidade_deixa_de_ser_verificavel(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    verify_root(root)
    zero_ids(monkeypatch)
    with pytest.raises(PathIntegrityViolation) as caught:
        verify_root(root)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE


def test_verify_root_recusa_raiz_vinculada_com_identidade_zero(root: BoundRoot) -> None:
    forged = BoundRoot(path=root.path, identity=ObjectIdentity(root.identity.volume_id, 0))
    with pytest.raises(PathIntegrityViolation) as caught:
        verify_root(forged)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE


def test_pai_com_identidade_nao_verificavel_nao_deixa_criar(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    (base(root) / "sub").mkdir()
    facts = authorized("sub/novo.txt", root)
    zero_ids(monkeypatch, keep={root.identity.file_id})
    with pytest.raises(PathIntegrityViolation) as caught:
        create_exclusive(facts, root)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE
    assert not (base(root) / "sub" / "novo.txt").exists()


def test_pai_com_identidade_nao_verificavel_nao_deixa_apagar(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    (base(root) / "sub").mkdir()
    (base(root) / "sub" / "f.txt").write_bytes(b"x")
    facts = authorized("sub/f.txt", root)
    zero_ids(monkeypatch, keep={root.identity.file_id})
    with pytest.raises(PathIntegrityViolation) as caught:
        delete_if_identity(facts, root)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE
    assert (base(root) / "sub" / "f.txt").exists()


def test_alvo_com_identidade_nao_verificavel_nao_abre_para_update(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = base(root) / "a.txt"
    target.write_bytes(b"intacto")
    zero_ids(monkeypatch, keep={root.identity.file_id})
    facts = inspect("a.txt", root.path)  # a base inspecionada também sai com `file_id == 0`
    assert facts.target_identity is not None and facts.target_identity.file_id == 0
    with (
        pytest.raises(PathIntegrityViolation) as caught,
        open_existing(facts, root, for_update=True),
    ):
        pass
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE
    assert target.read_bytes() == b"intacto"


def test_alvo_so_com_o_handle_nao_verificavel_tambem_recusa(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Base inspecionada válida, mas o handle aberto devolve `file_id == 0`."""
    target = base(root) / "a.txt"
    target.write_bytes(b"x")
    facts = authorized("a.txt", root, PathIntent.READ)
    zero_ids(monkeypatch, keep={root.identity.file_id})
    with pytest.raises(PathIntegrityViolation) as caught, open_existing(facts, root):
        pass
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE


def test_arquivo_criado_com_identidade_nao_verificavel_levanta_e_nao_e_removido(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(P2-002) Não há `CreatedFile` parcial: a criação sai como violação de integridade, o
    vazio permanece e nada é removido. Detalhes em `test_e7_5a_audit_p2.py`."""
    facts = authorized("novo.txt", root)
    zero_ids(monkeypatch, keep={root.identity.file_id})
    target = base(root) / "novo.txt"
    with pytest.raises(PathIntegrityViolation) as caught:
        create_exclusive(facts, root)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE
    assert target.exists() and target.stat().st_size == 0  # nenhum byte, e o vazio fica


def test_cleanup_recusa_identidade_criada_zero_mesmo_igual_a_observada(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = create_exclusive(authorized("novo.txt", root), root)
    created.close()
    created.identity = ObjectIdentity(created.identity.volume_id, 0) if created.identity else None
    zero_ids(monkeypatch, keep={root.identity.file_id})
    assert discard_created(created) is False
    assert (base(root) / "novo.txt").exists()


def test_delete_recusa_alvo_e_identidade_esperada_nao_verificaveis(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = base(root) / "a.txt"
    target.write_bytes(b"x")
    facts = authorized("a.txt", root)
    # identidade esperada explícita com `file_id == 0`
    with pytest.raises(PathIntegrityViolation) as expected:
        delete_if_identity(facts, root, expected_identity=ObjectIdentity(1, 0))
    assert expected.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE
    # a plataforma passa a devolver zero também na observação (esperada == observada == zero)
    zero_ids(monkeypatch, keep={root.identity.file_id})
    zero_facts = inspect("a.txt", root.path)
    with pytest.raises(PathIntegrityViolation) as observed:
        delete_if_identity(zero_facts, root)
    assert observed.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE
    assert target.exists()


def test_list_directory_recusa_diretorio_e_entradas_nao_verificaveis(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    (base(root) / "sub").mkdir()
    (base(root) / "sub" / "f.txt").write_bytes(b"x")
    (base(root) / "a.txt").write_bytes(b"x")
    zero_ids(monkeypatch, keep={root.identity.file_id})
    with pytest.raises(PathIntegrityViolation) as caught:
        list_directory(inspect("sub", root.path), root)
    assert caught.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE
    # (P2-001) a raiz é verificável, as entradas não: a enumeração **aborta**; não há entrada
    # "normal" com `identity=None` nem terceiro estado silencioso.
    with pytest.raises(PathIntegrityViolation) as entries:
        list_directory(inspect(".", root.path), root)
    assert entries.value.category is IntegrityFailure.INTEGRITY_UNVERIFIABLE


def test_decide_post_create_nega_identidades_zero() -> None:
    zero, parent = ObjectIdentity(1, 0), ObjectIdentity(1, 5)
    facts = PathFacts(
        requested_path="a.txt",
        canonical_root="R",
        canonical_target="R/a.txt",
        exists=True,
        parent_identity=parent,
        target_identity=None,
        volume="C:",
        root_volume="C:",
        is_symlink=Tri.FALSE,
        is_junction=Tri.FALSE,
        is_reparse_point=Tri.FALSE,
        is_unc=Tri.FALSE,
        is_device_namespace=Tri.FALSE,
        is_drive_relative=Tri.FALSE,
        contained=Tri.TRUE,
        ancestor_link_outside_root=Tri.FALSE,
        post_open_identity=zero,
        post_create_path_identity=zero,
        post_create_parent_identity=parent,
        post_create_regular=Tri.TRUE,
        post_create_reparse=Tri.FALSE,
    )
    # duas identidades zero iguais NÃO são prova de que o caminho aponta para o handle criado
    decision = decide_post_create(facts)
    assert not decision.allow and decision.rule_id == "path.post_create_unverified"
    from dataclasses import replace

    ok_ident = ObjectIdentity(1, 9)
    parent_zero = replace(
        facts,
        post_open_identity=ok_ident,
        post_create_path_identity=ok_ident,
        parent_identity=zero,
        post_create_parent_identity=zero,
    )
    denied = decide_post_create(parent_zero)
    assert not denied.allow and denied.rule_id == "path.post_create_no_parent_baseline"


# ====================================================== D/E. tolerate_absent_tail


def patch_lstat(
    monkeypatch: pytest.MonkeyPatch, behaviors: dict[Path, object]
) -> Callable[[], None]:
    real = Path.lstat

    def fake(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        behavior = behaviors.get(self)
        if isinstance(behavior, BaseException):
            raise behavior
        if behavior is not None:
            return behavior  # type: ignore[return-value]
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", fake)
    return lambda: None


def test_padrao_legado_ausente_continua_unknown_e_a_politica_fecha(root: BoundRoot) -> None:
    facts = inspect("a/b/c.txt", root.path)
    assert facts.is_symlink.is_unknown
    decision = decide_path(facts, intent=PathIntent.WRITE)
    assert not decision.allow and decision.rule_id == "path.symlink_unverified"


def test_com_a_flag_ausencia_comprovada_vira_falso_e_a_politica_autoriza(
    root: BoundRoot,
) -> None:
    facts = inspect("a/b/c.txt", root.path, tolerate_absent_tail=True)
    assert facts.is_symlink.is_false and facts.is_junction.is_false
    assert facts.is_reparse_point.is_false and facts.ancestor_link_outside_root.is_false
    assert decide_path(facts, intent=PathIntent.WRITE).allow


def test_a_existe_a_b_ausente_a_b_c_ausente_a_criacao_prossegue(root: BoundRoot) -> None:
    (base(root) / "a").mkdir()
    facts = authorized("a/b/c.txt", root)
    assert not facts.exists
    with create_exclusive(facts, root) as created:
        assert decide_post_create(created.facts).allow
    assert (base(root) / "a" / "b" / "c.txt").exists()


def test_link_existente_na_cadeia_continua_sendo_visto_com_a_flag(
    root: BoundRoot, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    if not make_dir_link(base(root) / "a", outside):
        pytest.skip("link indisponível neste ambiente")
    facts = inspect("a/b/c.txt", root.path, tolerate_absent_tail=True)
    assert facts.is_symlink.is_true or facts.is_reparse_point.is_true
    assert not decide_path(facts, intent=PathIntent.WRITE).allow


@pytest.mark.parametrize(
    "error",
    [
        PermissionError(errno.EACCES, "negado"),
        OSError(errno.EIO, "erro de E/S"),
        OSError(errno.EINVAL, "ambíguo"),
        ValueError("embedded null byte"),
    ],
    ids=["permissao", "io", "ambiguo", "valueerror"],
)
def test_erro_ambiguo_em_a_nao_e_ausencia_e_a_criacao_nao_prossegue(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    (base(root) / "a").mkdir()
    patch_lstat(monkeypatch, {Path(root.path) / "a": error})
    facts = inspect("a/b/c.txt", root.path, tolerate_absent_tail=True)
    assert facts.is_symlink.is_unknown and facts.is_reparse_point.is_unknown
    # B-AUD-003: a falha de coleta agora é **explícita** (código fixo, sem texto do erro) e a
    # política a recusa pela regra técnica — a criação continua não prosseguindo
    assert facts.inspection_error == "chain_lstat_failed"
    decision = decide_path(facts, intent=PathIntent.WRITE)
    assert not decision.allow and decision.rule_id == "path.inspection_failed"


def test_erro_em_um_componente_mais_fundo_tambem_nao_vira_ausencia(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    (base(root) / "a").mkdir()
    (base(root) / "a" / "b").mkdir()
    patch_lstat(monkeypatch, {Path(root.path) / "a" / "b": PermissionError(errno.EACCES, "negado")})
    facts = inspect("a/b/c.txt", root.path, tolerate_absent_tail=True)
    assert facts.is_symlink.is_unknown
    assert not decide_path(facts, intent=PathIntent.WRITE).allow


def test_so_filenotfound_e_ausencia_direta() -> None:
    absent = path_runtime._absence_is_proven
    assert absent(Path("nao-existe-xyz-123")) is True


def test_notadirectory_so_prova_ausencia_se_o_pai_e_lido_e_nao_e_diretorio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file_parent = tmp_path / "f.txt"
    file_parent.write_bytes(b"x")
    child = file_parent / "x"
    fake_dir = os.stat(tmp_path)  # um stat de diretório de verdade
    enotdir = NotADirectoryError(errno.ENOTDIR, "não é diretório")

    # pai é arquivo → ausência lógica comprovada
    patch_lstat(monkeypatch, {child: enotdir})
    assert path_runtime._absence_is_proven(child) is True
    monkeypatch.undo()

    # pai é diretório → ENOTDIR é incoerente: sem prova
    patch_lstat(monkeypatch, {child: enotdir, child.parent: fake_dir})
    assert path_runtime._absence_is_proven(child) is False
    monkeypatch.undo()

    # pai ilegível → sem prova
    patch_lstat(
        monkeypatch, {child: enotdir, child.parent: PermissionError(errno.EACCES, "negado")}
    )
    assert path_runtime._absence_is_proven(child) is False


def test_erro_de_stat_ambiguo_no_alvo_final_tambem_nao_e_ausencia(
    root: BoundRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_lstat(
        monkeypatch,
        {Path(root.path) / "novo.txt": OSError(errno.EIO, "erro de E/S")},
    )
    facts = inspect("novo.txt", root.path, tolerate_absent_tail=True)
    assert facts.is_symlink.is_unknown
    assert not decide_path(facts, intent=PathIntent.WRITE).allow


def test_falha_de_canonicalizacao_continua_fail_closed_com_a_flag(
    tmp_path: Path,
) -> None:
    facts = inspect("a.txt", tmp_path / "raiz-que-nao-existe", tolerate_absent_tail=True)
    assert facts.inspection_error is not None and facts.contained.is_unknown
    assert not decide_path(facts, intent=PathIntent.WRITE).allow


def test_pai_que_e_arquivo_a_flag_nao_cria_nada_e_a_criacao_falha_tecnicamente(
    root: BoundRoot,
) -> None:
    (base(root) / "a").write_bytes(b"sou arquivo")
    facts = inspect("a/b/c.txt", root.path, tolerate_absent_tail=True)
    # ausência lógica comprovada (nada existe sob um arquivo): a política não tem o que objetar...
    assert decide_path(facts, intent=PathIntent.WRITE).allow
    # ...e a primitiva recusa tecnicamente, sem criar nada
    with pytest.raises(PathOperationFailed) as caught:
        create_exclusive(facts, root)
    assert caught.value.category is PathFailure.NOT_DIRECTORY
    assert (base(root) / "a").read_bytes() == b"sou arquivo"


def test_a_flag_nao_muda_o_comportamento_de_alvo_existente(root: BoundRoot) -> None:
    (base(root) / "a.txt").write_bytes(b"x")
    plain = inspect("a.txt", root.path)
    relaxed = inspect("a.txt", root.path, tolerate_absent_tail=True)
    assert plain == relaxed


def test_a_flag_e_so_um_opt_in_e_o_default_e_falso() -> None:
    import inspect as std_inspect

    for function in (path_runtime.inspect, path_runtime._lexical_chain_facts):
        parameter = std_inspect.signature(function).parameters["tolerate_absent_tail"]
        assert parameter.default is False
        assert parameter.kind is std_inspect.Parameter.KEYWORD_ONLY


def test_symlink_final_dangling_e_visto_como_link_nao_como_ausencia(
    root: BoundRoot, tmp_path: Path
) -> None:
    """`lstat` de um link quebrado **existe**: não é ausência, é link."""
    alvo_inexistente = tmp_path / "nada"
    link = base(root) / "quebrado"
    try:
        link.symlink_to(alvo_inexistente, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink indisponível neste ambiente")
    facts = inspect("quebrado", root.path, tolerate_absent_tail=True)
    assert facts.is_symlink.is_true
    assert not decide_path(facts, intent=PathIntent.WRITE).allow
    assert stat.S_ISLNK(os.lstat(link).st_mode)
