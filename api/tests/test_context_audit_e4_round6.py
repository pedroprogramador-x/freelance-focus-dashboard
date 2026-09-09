"""Auditoria 6: prefixo conservador em bytes e precedencia sobre leitura parcial.

Os alvos invalidos existem no banco de objetos Git, inclusive nos testes Windows.
Nenhum caminho e reparado/decodificado para decidir o resultado esperado.
"""

from __future__ import annotations

from itertools import permutations, product
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.context_engine.source_ref_expansion import (
    CompiledSourceRef,
    ExpansionStatus,
    _may_cover,
    compile_source_ref,
    expand_against_commit,
    validate_and_compile,
)
from app.git_runtime import list_tree
from app.safety.types import SafetyDecision
from tests.context_helpers import GIT, init_repo
from tests.test_api_context import _create, _workspace
from tests.test_context_audit_e4_round5 import _git_out

pytestmark = pytest.mark.skipif(GIT is None, reason="git indisponivel no PATH")


def _commit_paths(root: Path, paths: list[bytes]) -> str:
    """Arvores recursivas reais; '/' delimita arvore, nunca entra no nome do blob."""
    init_repo(root)
    blob = _git_out(root, "hash-object", "-w", "--stdin", stdin=b"audit\n")

    def tree(names: list[bytes]) -> str:
        records = []
        directories: dict[bytes, list[bytes]] = {}
        for name in names:
            head, separator, tail = name.partition(b"/")
            if separator:
                directories.setdefault(head, []).append(tail)
            else:
                records.append(b"100644 blob " + blob.encode() + b"\t" + head + b"\0")
        for directory, children in directories.items():
            sha = tree(children)
            records.append(b"040000 tree " + sha.encode() + b"\t" + directory + b"\0")
        return _git_out(root, "mktree", "-z", stdin=b"".join(records))

    commit = _git_out(root, "commit-tree", tree(paths), "-m", "audit 6")
    _git_out(root, "update-ref", "HEAD", commit)
    return commit


@pytest.mark.parametrize(
    ("pattern", "readable", "raw", "expected"),
    [
        ("a[bc]*", "ab.py", b"ab-\xff.py", True),
        ("a[bc]*", "ac.py", b"ac-\xff.py", True),
        ("a[]]*", "a].py", b"a]-\xff.py", True),
        ("a[!b]*", "ac.py", b"ac-\xff.py", True),
        ("a]*", "a].py", b"a]-\xff.py", True),
        ("a]*", "a].py", b"ab-\xff.py", False),
        ("café*", "café.py", b"caf\xc3\xa9-\xff.py", True),
        ("café*", "café.py", b"caf\xc3A-\xff.py", False),
        ("café*", "café.py", b"caf\xc3\xaa-\xff.py", False),
        ("**", "src/good.py", b"unrelated/deep/\xff.py", True),
        ("**/good.py", "src/good.py", b"unrelated/deep/\xff.py", True),
        ("Src/*", "Src/good.py", b"src/\xff.py", False),
        ("Src/*", "Src/good.py", b"Src/\xff.py", True),
        ("ws/**", "ws/good.py", b"ws\\outside.py", False),
        ("ws", "ws/good.py", b"ws/\xff.py", True),
        ("ws", "ws/good.py", b"ws2/\xff.py", False),
    ],
)
def test_aud6_prefixo_em_objetos_git(
    tmp_path: Path, pattern: str, readable: str, raw: bytes, expected: bool
) -> None:
    root = tmp_path / "repo"
    commit = _commit_paths(root, [readable.encode("utf-8"), raw])
    listing = list_tree(str(root), commit)
    assert listing is not None
    assert [path for path, _ in listing.files] == [readable]
    assert [item.raw for item in listing.unrepresentable] == [raw]
    compiled = validate_and_compile([pattern])
    assert not isinstance(compiled, SafetyDecision)
    assert compiled[0].covers(readable)
    assert _may_cover(compiled[0], raw) is expected
    result = expand_against_commit(str(root), commit, [pattern])
    assert result.unrepresentable_paths
    if expected:
        assert result.status is ExpansionStatus.UNRESOLVED
        assert result.incomplete_refs == (pattern,)
        assert result.unresolved_refs == ()
        assert result.source_hash is None
    else:
        assert result.status is ExpansionStatus.RESOLVED
        assert result.paths == (readable,)
        assert result.source_hash is not None


@pytest.mark.parametrize("pattern", ["!a*", "src/!a*", "!**", "src/!good.py"])
def test_aud6_negacao_de_segmento_recusada(tmp_path: Path, pattern: str) -> None:
    root = tmp_path / "repo"
    commit = _commit_paths(root, [b"good.py", b"a-\xff.py"])
    result = expand_against_commit(str(root), commit, [pattern])
    assert result.status is ExpansionStatus.DENIED
    assert result.decision is not None
    assert result.decision.rule_id == "source_ref_expansion.unsupported_syntax"


@pytest.mark.parametrize("secret", [False, True])
def test_aud6_dois_ilegveis_e_precedencia(
    tmp_path: Path, auth_api_client: TestClient, secret: bool
) -> None:
    root = tmp_path / "repo"
    paths = [b"src/good.py", b"src/bad-\xff.py", b"other/bad-\xfe.py"]
    if secret:
        paths.append(b"src/.env")
    commit = _commit_paths(root, paths)
    compiled = validate_and_compile(["src/*"])
    assert not isinstance(compiled, SafetyDecision)
    assert _may_cover(compiled[0], paths[1])
    assert not _may_cover(compiled[0], paths[2])
    listing = list_tree(str(root), commit)
    assert listing is not None and len(listing.unrepresentable) == 2
    for refs in permutations(["src/*", "missing/**", "src/good.py"]):
        result = expand_against_commit(str(root), commit, list(refs))
        if secret:
            assert result.status is ExpansionStatus.DENIED
            assert result.decision is not None
            assert result.decision.rule_id == "source_ref_expansion.secret_denied"
        else:
            assert result.status is ExpansionStatus.UNRESOLVED
            assert result.incomplete_refs == ("src/*",)
            assert result.unresolved_refs == ("missing/**",)
    workspace_id = _workspace(auth_api_client, root)
    status, body = _create(auth_api_client, workspace_id, source_refs=["src/*"])
    assert status == (422 if secret else 201), body
    if not secret:
        assert body["state"] == "unknown"
        assert body["source_hash"] is None
    resolved = expand_against_commit(str(root), commit, ["src/good.py"])
    assert resolved.status is ExpansionStatus.RESOLVED
    assert len(resolved.unrepresentable_paths) == 2


def test_aud6_todo_casamento_implica_may_cover() -> None:
    """Propriedade de inclusao contra o matcher completo, sem duplicar o prefixo.

    Inclui backslash (UTF-8 valido mas nao representavel), Unicode, classes e globstar.
    Para bytes invalidos nao existe texto/oraculo de casamento: os casos Git acima
    verificam as fronteiras sem usar decodificacao com substituicao como identidade.
    """
    fragments = ["a", "]", "é", "*", "**", "?", "[ab]", "[!a]", "[]]", "**/"]
    paths = ["".join(chars) for size in range(1, 4) for chars in product("ab]/é\\", repeat=size)]
    matches = 0
    for parts in product(fragments, repeat=2):
        compiled = compile_source_ref("".join(parts))
        assert isinstance(compiled, CompiledSourceRef)
        for path in paths:
            if compiled.covers(path):
                matches += 1
                assert _may_cover(compiled, path.encode("utf-8")), (compiled.normalized, path)
    assert matches > 1000
