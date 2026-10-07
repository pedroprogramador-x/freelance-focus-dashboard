"""E8.3 — verificação pós-execução: árvore principal antes × depois e a worktree do run.

Tudo com `git` de verdade: um repositório principal com `app/` e `other/` (monorepo) e a
worktree da task criada pela E7.4 (`create_worktree`). Os "efeitos do run" são escritas diretas
no disco — exatamente o que o código do projeto, rodando pelo Test Runner, poderia fazer sem
passar pelo `ToolExecutor`.

Mapa: PE1–PE20 do planejamento E8.3; a correção da auditoria independente — MAIN_* (AUD-002),
FILTER_* (AUD-003), SHA256_* (AUD-004), BOUND_* (AUD-005) —; e os contrafactuais descritos em
cada teste.
"""

from __future__ import annotations

import contextlib
import inspect
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app import execution_verification as ev
from app.execution_verification import (
    MainTreeSnapshot,
    PostExecutionResult,
    PostExecutionStatus,
    PostExecutionVerifier,
)
from app.git_runtime import (
    VerificationBudget,
    VerificationFailure,
    WorktreeChangeKind,
    create_worktree,
    main_tree_state,
    repository_layout,
    worktree_changes,
)
from app.git_runtime import post_execution as pe
from app.git_runtime.mediated import GitFailure, MediatedGit, MediatedGitError
from app.git_runtime.worktree import _is_reparse
from app.path_runtime import CheckedTreeWriter, bind_root, prepare_worktree_root
from app.safety import DOT_GIT_RULE_ID
from app.safety.policy import SafetyPolicy
from app.safety.types import ObjectIdentity
from tests.conftest import supports_symlinks
from tests.test_git_worktree_e7_4 import git, rev

_GIT = shutil.which("git")
IS_WINDOWS = sys.platform == "win32"
pytestmark = pytest.mark.skipif(_GIT is None, reason="git indisponível no PATH")
posix_only = pytest.mark.skipif(IS_WINDOWS, reason="comportamento específico do POSIX")

TASK = "abcdef12-3456-4789-8abc-def012345678"
V = PostExecutionVerifier()
S = PostExecutionStatus
K = WorktreeChangeKind
F = VerificationFailure
SENTINEL = "E83_SYNTHETIC_SECRET_DO_NOT_LEAK"
#: Campos extras que o `os.stat_result` sintético precisa no Windows.
_NO_ATTRIBUTES = {"st_file_attributes": 0, "st_reparse_tag": 0}


def never() -> bool:
    return False


@dataclass
class Rig:
    tmp: Path
    repo: Path
    base: str
    worktree: Path
    workspace_path: str
    prefix: str
    local_path: str
    #: A identidade registrada **no binding** (antes do run), como o `ResolvedWorkspace`.
    root_identity: ObjectIdentity

    def capture(self, verifier: PostExecutionVerifier = V) -> MainTreeSnapshot:
        return verifier.capture_main_tree(self.local_path, is_cancelled=never)

    def verify(
        self,
        before: MainTreeSnapshot,
        *,
        verifier: PostExecutionVerifier = V,
        policy: SafetyPolicy | None = None,
        **changes: Any,
    ) -> PostExecutionResult:
        arguments: dict[str, Any] = {
            "before": before,
            "workspace_path": self.workspace_path,
            "workspace_prefix": self.prefix,
            "root_identity": self.root_identity,
            "base_commit": self.base,
            "policy": SafetyPolicy() if policy is None else policy,
            "is_cancelled": never,
        }
        arguments.update(changes)
        return verifier.verify(**arguments)

    def run(self, effect: Callable[[], object], **changes: Any) -> PostExecutionResult:
        """Captura, aplica o "efeito do run", verifica."""
        before = self.capture()
        effect()
        return self.verify(before, **changes)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))


def _seed(repo: Path, *, ordinary: bool = False) -> None:
    if ordinary:  # um diretório comum do base, com subdiretório (FINAL-002)
        write(repo / "app" / "ordinary" / "f.txt", "F\n")
        write(repo / "app" / "ordinary" / "deep" / "g.txt", "G\n")
    write(repo / "app" / "a.py", "A = 1\n")
    write(repo / "app" / "keep.txt", "keep\n")
    write(repo / "other" / "x.txt", "X\n")
    write(repo / "README.md", "readme\n")
    write(repo / ".gitignore", "*.log\nbuild/\nignored.txt\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")


def _worktree_root(tmp: Path, repo: Path) -> Any:
    root_dir = tmp / "worktrees"
    root_dir.mkdir()
    layout = repository_layout(str(repo))
    assert layout is not None
    return prepare_worktree_root(
        root_dir,
        sync_roots=(),
        repository_toplevel=layout.toplevel,
        git_common_dir=layout.git_common_dir,
    )


def make_rig(tmp: Path, *, subdir: bool = True, ordinary: bool = False) -> Rig:
    tmp.mkdir(parents=True, exist_ok=True)
    repo = tmp / "repo"
    git(tmp, "init", "-q", "-b", "main", str(repo))
    _seed(repo, ordinary=ordinary)
    base = rev(repo)
    root = _worktree_root(tmp, repo)
    local_path = str(repo / "app") if subdir else str(repo)
    outcome = create_worktree(
        local_path, base_commit=base, task_id=TASK, root=root, tree_writer=CheckedTreeWriter
    )
    assert outcome.created and outcome.path and outcome.workspace_path
    return Rig(
        tmp=tmp,
        repo=repo,
        base=base,
        worktree=Path(outcome.path),
        workspace_path=outcome.workspace_path,
        prefix="app/" if subdir else "",
        local_path=local_path,
        root_identity=bind_root(outcome.workspace_path).identity,
    )


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return make_rig(tmp_path)


@pytest.fixture
def flat(tmp_path: Path) -> Rig:
    return make_rig(tmp_path, subdir=False)


def paths(result: PostExecutionResult) -> set[tuple[str, WorktreeChangeKind]]:
    return {(change.path, change.kind) for change in result.changes}


def findings(result: PostExecutionResult) -> set[tuple[str, str]]:
    return {(finding.path, finding.rule_id) for finding in result.findings}


def no_absolute(rig: Rig, *objects: object) -> None:
    """PE17: nenhum caminho absoluto do teste aparece em resultado, repr ou erro."""
    for obj in objects:
        for text in (str(obj), repr(obj)):
            for absolute in (str(rig.tmp), str(rig.tmp).replace("\\", "/"), rig.workspace_path):
                assert absolute.lower() not in text.lower(), text


def budget(**changes: Any) -> VerificationBudget:
    return VerificationBudget(is_cancelled=changes.pop("is_cancelled", never), **changes)


# ============================================================== árvore principal (PE1–PE6)


def test_pe1_nada_mudou_e_verified(rig: Rig) -> None:
    result = rig.run(lambda: None)
    assert result == PostExecutionResult(status=S.VERIFIED, reason=None)
    no_absolute(rig, result)


def test_pe2_arquivo_rastreado_da_principal_alterado(rig: Rig) -> None:
    result = rig.run(lambda: write(rig.repo / "app" / "a.py", "A = 2\n"))
    assert (result.status, result.reason) == (S.OUT_OF_WORKTREE_WRITE, ev.MAIN_TREE_CHANGED)
    assert result.changes == () and result.findings == ()
    no_absolute(rig, result)


def test_pe3_arquivo_novo_na_principal(rig: Rig) -> None:
    result = rig.run(lambda: write(rig.repo / "app" / "novo.py", "x\n"))
    assert result.status is S.OUT_OF_WORKTREE_WRITE


def test_pe4_mudanca_no_indice_da_principal(rig: Rig) -> None:
    write(rig.repo / "app" / "a.py", "A = 2\n")  # sujo antes
    result = rig.run(lambda: git(rig.repo, "add", "app/a.py"))  # só o índice muda
    assert result.status is S.OUT_OF_WORKTREE_WRITE


def test_pe5_remocao_e_rename_na_principal(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    removed = rig.run(lambda: (rig.repo / "other" / "x.txt").unlink())
    assert removed.status is S.OUT_OF_WORKTREE_WRITE
    rig = make_rig(tmp_path / "2")
    moved = rig.run(lambda: (rig.repo / "README.md").rename(rig.repo / "LEIAME.md"))
    assert moved.status is S.OUT_OF_WORKTREE_WRITE


def test_pe6_workspace_em_subdiretorio_nao_cria_ponto_cego(rig: Rig) -> None:
    """M8: um snapshot só de `app/` (o `local_path`) não veria `other/` mudar."""
    assert rig.local_path.endswith("app") and rig.prefix == "app/"
    result = rig.run(lambda: write(rig.repo / "other" / "x.txt", "mudou\n"))
    assert result.status is S.OUT_OF_WORKTREE_WRITE
    result = rig.run(lambda: write(rig.repo / "raiz-nova.txt", "x\n"))
    assert result.status is S.OUT_OF_WORKTREE_WRITE


def test_estado_e_do_repositorio_inteiro_e_canonico(rig: Rig) -> None:
    write(rig.repo / "other" / "x.txt", "sujo\n")
    of_subdir = main_tree_state(str(rig.repo / "app"), budget())
    of_root = main_tree_state(str(rig.repo), budget())
    assert isinstance(of_root, pe.MainTreeState) and of_subdir == of_root
    assert "entries" in repr(of_root) and str(rig.repo) not in repr(of_root)
    assert of_root.index_digest is not None and len(of_root.filesystem_digest) == 64


def test_pe16_especie_da_divergencia_faz_parte_do_estado(rig: Rig) -> None:
    """M9: um estado que olhasse só `set(paths)` acharia igual (`a.py` diverge antes e depois)."""
    write(rig.repo / "app" / "a.py", "A = 2\n")
    git(rig.repo, "add", "app/a.py")
    result = rig.run(lambda: write(rig.repo / "app" / "a.py", "A = 3\n"))
    assert result.status is S.OUT_OF_WORKTREE_WRITE


def test_principal_suja_e_estavel_nao_bloqueia(rig: Rig) -> None:
    """Árvore principal suja **antes** e igual depois: não é escrita do run ([04] §8)."""
    write(rig.repo / "app" / "a.py", "A = 2\n")
    write(rig.repo / "notas.txt", "n\n")
    write(rig.repo / "ignored.txt", "i\n")
    git(rig.repo, "add", "notas.txt")
    assert rig.run(lambda: None).status is S.VERIFIED


# ============================================ AUD-002: o estado não depende do `git status`


def test_main_assume_1_assume_unchanged_reescrito_e_detectado(rig: Rig) -> None:
    """A4: com `assume-unchanged` o `git status` não mostra nada; o filesystem mostra."""
    git(rig.repo, "update-index", "--assume-unchanged", "app/a.py")
    result = rig.run(lambda: write(rig.repo / "app" / "a.py", "A = 99\n"))
    assert (result.status, result.reason) == (S.OUT_OF_WORKTREE_WRITE, ev.MAIN_TREE_CHANGED)


def test_main_skip_1_skip_worktree_reescrito_e_detectado(rig: Rig) -> None:
    git(rig.repo, "update-index", "--skip-worktree", "app/a.py")
    result = rig.run(lambda: write(rig.repo / "app" / "a.py", "A = 99\n"))
    assert result.status is S.OUT_OF_WORKTREE_WRITE


def test_main_ignored_new_arquivo_ignorado_novo_e_detectado(rig: Rig) -> None:
    """A5: o caminho ignorado deixou de estar fora do snapshot."""
    result = rig.run(lambda: write(rig.repo / "ignored.txt", "novo\n"))
    assert result.status is S.OUT_OF_WORKTREE_WRITE
    result = rig.run(lambda: write(rig.repo / "build" / "out.log", "x\n"))
    assert result.status is S.OUT_OF_WORKTREE_WRITE


def test_main_ignored_existing_conteudo_trocado_e_detectado(rig: Rig) -> None:
    write(rig.repo / "ignored.txt", "antes\n")
    result = rig.run(lambda: write(rig.repo / "ignored.txt", "depois\n"))
    assert result.status is S.OUT_OF_WORKTREE_WRITE


def test_main_untracked_existing_mesmo_tamanho_conteudo_trocado(rig: Rig) -> None:
    """A6: comparar só caminho e tamanho deixaria isto passar — o conteúdo participa."""
    write(rig.repo / "notas.txt", "aaaa\n")
    result = rig.run(lambda: write(rig.repo / "notas.txt", "bbbb\n"))
    assert result.status is S.OUT_OF_WORKTREE_WRITE


def test_main_dirty_existing_rastreado_ja_sujo_reescrito(rig: Rig) -> None:
    write(rig.repo / "app" / "a.py", "A = 2\n")
    result = rig.run(lambda: write(rig.repo / "app" / "a.py", "A = 7\n"))
    assert result.status is S.OUT_OF_WORKTREE_WRITE


def test_main_repositorio_aninhado_tem_os_arquivos_observados(rig: Rig) -> None:
    nested = rig.repo / "nested"
    git(rig.repo, "init", "-q", str(nested))
    write(nested / "src" / "x.py", "x = 1\n")
    result = rig.run(lambda: write(nested / "src" / "x.py", "x = 2\n"))
    assert result.status is S.OUT_OF_WORKTREE_WRITE


def test_main_admin_dir_nao_e_percorrido_mas_o_indice_sim(rig: Rig) -> None:
    """O `.git` da raiz não é inventário: escrever num arquivo dele que não é o índice não muda
    o estado (metadado fora do contrato); mudar o índice muda."""
    result = rig.run(lambda: write(rig.repo / ".git" / "description", "outro\n"))
    assert result.status is S.VERIFIED
    result = rig.run(lambda: git(rig.repo, "update-index", "--chmod=+x", "app/a.py"))
    assert result.status is S.OUT_OF_WORKTREE_WRITE


@posix_only
def test_main_bit_de_modo_e_observado(rig: Rig) -> None:
    result = rig.run(lambda: (rig.repo / "README.md").chmod(0o755))
    assert result.status is S.OUT_OF_WORKTREE_WRITE


@posix_only
def test_main_symlink_e_observado_sem_ser_seguido(rig: Rig) -> None:
    outside = rig.tmp / "fora"
    write(outside / "alvo.txt", "1\n")
    os.symlink(outside / "alvo.txt", rig.repo / "link.txt")
    # Mudar o conteúdo do **alvo** (fora da raiz) não é mudança da árvore: o link não é seguido.
    assert rig.run(lambda: write(outside / "alvo.txt", "2\n")).status is S.VERIFIED

    def retarget() -> None:
        os.unlink(rig.repo / "link.txt")
        os.symlink(outside / "outro.txt", rig.repo / "link.txt")

    assert rig.run(retarget).status is S.OUT_OF_WORKTREE_WRITE


@posix_only
def test_main_nome_nao_utf8_e_identidade_pelos_bytes(rig: Rig) -> None:
    raw = os.fsencode(rig.repo) + b"/\xff.bin"

    def create() -> None:
        os.close(os.open(raw, os.O_CREAT | os.O_WRONLY))

    assert rig.run(create).status is S.OUT_OF_WORKTREE_WRITE
    assert rig.run(lambda: Path(os.fsdecode(raw)).write_bytes(b"x")).status is (
        S.OUT_OF_WORKTREE_WRITE
    )


@posix_only
def test_bound_12_fifo_na_principal_e_observado_sem_ser_aberto(rig: Rig) -> None:
    """Abrir um FIFO sem escritor travaria; o tipo vem do `lstat` e o objeto nunca é aberto."""
    os.mkfifo(rig.repo / "pipe")  # type: ignore[attr-defined, unused-ignore]
    assert rig.run(lambda: None).status is S.VERIFIED
    assert rig.run(lambda: (rig.repo / "pipe").unlink()).status is S.OUT_OF_WORKTREE_WRITE


def test_bound_12_especial_nunca_e_aberto(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def forbidden(*args: object) -> None:
        raise AssertionError("arquivo especial aberto")

    monkeypatch.setattr(pe, "_read_regular", forbidden)
    fifo = os.stat_result((stat.S_IFIFO | 0o644, 1, 1, 1, 0, 0, 0, 0, 0, 0), _NO_ATTRIBUTES)
    record, descend = pe._entry_record(str(tmp_path / "p"), b"p", fifo, budget())
    assert record.startswith(b"other\0") and not descend


def test_pe11_anti_replay_snapshot_de_outro_repositorio(tmp_path: Path) -> None:
    """Snapshot do repositório A + verificação de uma worktree do repositório B: recusado."""
    first = make_rig(tmp_path / "a")
    second = make_rig(tmp_path / "b")
    before = first.capture()
    result = second.verify(before)
    assert result.status in (S.INTEGRITY_VIOLATION, S.UNVERIFIABLE)  # nunca VERIFIED


# ======================================== AUD-003: nenhum helper do projeto é executado

HELPER = """import sys
open(sys.argv[1], "a").write(sys.argv[2] + chr(10))
# `process`/`fsmonitor` falam um protocolo; o rastro basta, e sair já evita esperar o Git.
if sys.argv[2] not in ("process", "fsmonitor"):
    sys.stdout.buffer.write(sys.stdin.buffer.read())
"""


@pytest.fixture
def helper(tmp_path: Path) -> Callable[[str], tuple[str, Path]]:
    """`(comando, sentinela)`: um helper que, se executado, deixa rastro **fora** da raiz."""
    script = tmp_path / "helpers" / "helper.py"
    write(script, HELPER)
    sentinel = tmp_path / "helpers" / "sentinel.txt"

    def command(label: str) -> tuple[str, Path]:
        line = (
            f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}" '
            f'"{sentinel.as_posix()}" {label}'
        )
        return line, sentinel

    return command


def _configure(rig: Rig, pairs: dict[str, str], attributes: str) -> None:
    for key, value in pairs.items():
        git(rig.repo, "config", key, value)
    write(rig.repo / ".git" / "info" / "attributes", attributes)
    write(rig.repo / "app" / "keep.txt", "keep modificado\n")  # sujo: força comparação


@pytest.mark.parametrize(
    ("label", "keys", "attributes", "control"),
    [
        ("clean", ["filter.e83.clean"], "*.txt filter=e83\n", ["add", "app/keep.txt"]),
        ("smudge", ["filter.e83.smudge"], "*.txt filter=e83\n", ["checkout", "--", "app/keep.txt"]),
        ("process", ["filter.e83.process"], "*.txt filter=e83\n", ["add", "app/keep.txt"]),
        ("fsmonitor", ["core.fsmonitor"], "", ["status"]),
        ("textconv", ["diff.e83.textconv"], "*.txt diff=e83\n", ["diff"]),
        ("external", ["diff.external"], "", ["diff"]),
    ],
    ids=[
        "FILTER_1-clean",
        "FILTER_3-smudge",
        "FILTER_2-process",
        "FILTER_4-fsmonitor",
        "FILTER_5-textconv",
        "FILTER_5-external",
    ],
)
def test_filter_helpers_nunca_executam(
    rig: Rig,
    helper: Callable[[str], tuple[str, Path]],
    label: str,
    keys: list[str],
    attributes: str,
    control: list[str],
) -> None:
    """A3/A16: capture e verify não rodam nenhum helper; o controle prova que a config é viva."""
    command, sentinel = helper(label)
    _configure(rig, dict.fromkeys(keys, command), attributes)
    before = rig.capture()
    result = rig.verify(before)
    assert result.status is S.VERIFIED  # nada mudou entre as duas leituras
    write(rig.worktree / "app" / "keep.txt", "na worktree também\n")
    rig.verify(rig.capture())
    assert not sentinel.exists(), f"o helper {label} rodou durante a verificação"

    # Controle, só no harness de teste e **depois**: um comando Git comum aciona o helper.
    # O helper já deixa o rastro ao nascer; o Git do controle pode ficar esperando o protocolo.
    with contextlib.suppress(subprocess.TimeoutExpired):
        subprocess.run(  # noqa: S603 — argv literal, teste
            [_GIT or "git", "-C", str(rig.repo), *control],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=60,
        )
    assert sentinel.exists(), f"controle: a config do helper {label} não estava viva"


# ==================================================================== worktree (PE7–PE11)


def test_pe7_mudanca_valida_dentro_do_prefixo(rig: Rig) -> None:
    result = rig.run(lambda: write(rig.worktree / "app" / "a.py", "A = 2\n"))
    assert result.status is S.VERIFIED and result.reason is None
    assert paths(result) == {("app/a.py", K.MODIFIED)}


def test_criacao_remocao_e_diretorio_dentro_do_prefixo(rig: Rig) -> None:
    def effect() -> None:
        write(rig.worktree / "app" / "novo" / "b.py", "B\n")
        (rig.worktree / "app" / "keep.txt").unlink()
        (rig.worktree / "app" / "vazio").mkdir()

    result = rig.run(effect)
    assert result.status is S.VERIFIED
    assert paths(result) == {
        ("app/novo", K.DIRECTORY),
        ("app/novo/b.py", K.ADDED),
        ("app/keep.txt", K.DELETED),
        ("app/vazio", K.DIRECTORY),
    }


def test_ignorado_na_worktree_tambem_e_mudanca(rig: Rig) -> None:
    result = rig.run(lambda: write(rig.worktree / "app" / "build" / "out.log", "x\n"))
    assert result.status is S.VERIFIED
    assert ("app/build/out.log", K.ADDED) in paths(result)


def test_conteudo_binario_e_crlf_sao_bytes(rig: Rig) -> None:
    result = rig.run(lambda: (rig.worktree / "app" / "a.py").write_bytes(b"A = 1\r\n"))
    assert ("app/a.py", K.MODIFIED) in paths(result)


def test_pe8_mudanca_fora_do_prefixo_dentro_da_worktree(rig: Rig) -> None:
    """M11: ignorar o que está fora do `workspace_prefix` aprovaria este run."""
    result = rig.run(lambda: write(rig.worktree / "other" / "x.txt", "mudou\n"))
    assert result.status is S.OUTSIDE_WORKSPACE_CHANGE
    assert result.reason == ev.CHANGE_OUTSIDE_WORKSPACE
    assert findings(result) == {("other/x.txt", ev.CHANGE_OUTSIDE_WORKSPACE)}


@pytest.mark.parametrize(
    "effect",
    [
        lambda wt: write(wt / "raiz.txt", "x\n"),
        lambda wt: (wt / "README.md").unlink(),
        lambda wt: write(wt / ".pytest_cache" / "v" / "x", "x\n"),
        lambda wt: write(wt / "apple" / "x.txt", "prefixo de texto não é prefixo de caminho\n"),
        lambda wt: write(wt / "app2" / "x.txt", "x\n"),
        lambda wt: write(wt / "app-old" / "x.txt", "x\n"),
    ],
    ids=["criado-na-raiz", "removido-fora", "cache-fora", "prefixo-textual", "app2", "app-old"],
)
def test_pe8_toda_forma_de_mudanca_fora_do_prefixo(
    rig: Rig, effect: Callable[[Path], object]
) -> None:
    assert rig.run(lambda: effect(rig.worktree)).status is S.OUTSIDE_WORKSPACE_CHANGE


def test_workspace_na_raiz_nao_tem_fora(flat: Rig) -> None:
    result = flat.run(lambda: write(flat.worktree / "other" / "x.txt", "mudou\n"))
    assert result.status is S.VERIFIED and paths(result) == {("other/x.txt", K.MODIFIED)}


@pytest.mark.parametrize(
    "relative",
    [".env", "sub/.env.local", "chave.pem", "secrets/token.txt", "id_rsa"],
)
def test_pe9_segredo_criado_pelo_teste_e_negado_pela_politica(rig: Rig, relative: str) -> None:
    result = rig.run(lambda: write(rig.worktree / "app" / relative, "S=1\n"))
    assert (result.status, result.reason) == (S.PATH_POLICY_DENIED, "path_policy_denied")
    assert f"app/{relative}" in {path for path, _rule in findings(result)}
    assert all(
        finding.status is S.PATH_POLICY_DENIED and finding.rule_id.startswith("path.")
        for finding in result.findings
    )


def test_pe9_segredo_ignorado_pelo_gitignore_tambem_e_negado(rig: Rig) -> None:
    write(rig.worktree / "app" / ".gitignore", ".env\n")  # o próprio teste esconde do Git
    result = rig.run(lambda: write(rig.worktree / "app" / ".env", "S=1\n"))
    assert result.status is S.PATH_POLICY_DENIED
    assert ("app/.env", K.ADDED) in paths(result)


def test_dot_git_aninhado_e_negado(rig: Rig) -> None:
    result = rig.run(lambda: write(rig.worktree / "app" / "sub" / ".git" / "config", "x\n"))
    assert result.status is S.PATH_POLICY_DENIED
    assert DOT_GIT_RULE_ID in {rule for _path, rule in findings(result)}


def test_politica_composta_mais_restritiva_e_respeitada(rig: Rig) -> None:
    strict = SafetyPolicy(secret_patterns=(*SafetyPolicy().secret_patterns, "**/*.py"))
    result = rig.run(lambda: write(rig.worktree / "app" / "a.py", "A = 2\n"), policy=strict)
    assert result.status is S.PATH_POLICY_DENIED


def test_remocao_de_caminho_e_validada_sem_recriar(rig: Rig) -> None:
    strict = SafetyPolicy(secret_patterns=(*SafetyPolicy().secret_patterns, "**/keep.txt"))
    result = rig.run(lambda: (rig.worktree / "app" / "keep.txt").unlink(), policy=strict)
    assert result.status is S.PATH_POLICY_DENIED
    assert not (rig.worktree / "app" / "keep.txt").exists()


def test_mudanca_no_indice_da_worktree_e_vista(rig: Rig) -> None:
    def effect() -> None:
        write(rig.worktree / "app" / ".env", "S=1\n")
        git(rig.worktree, "add", "-f", "app/.env")
        (rig.worktree / "app" / ".env").unlink()  # some do disco, fica no índice

    result = rig.run(effect)
    assert result.status is S.PATH_POLICY_DENIED
    assert ("app/.env", K.INDEX) in paths(result)


def test_intent_to_add_na_worktree_e_vista(rig: Rig) -> None:
    def effect() -> None:
        write(rig.worktree / "app" / "n.py", "")
        git(rig.worktree, "add", "-N", "app/n.py")

    result = rig.run(effect)
    assert {("app/n.py", K.INDEX), ("app/n.py", K.ADDED)} <= paths(result)


def _link(target: Path, link: Path) -> bool:
    if IS_WINDOWS:
        import _winapi

        try:
            _winapi.CreateJunction(str(target), str(link))  # type: ignore[attr-defined, unused-ignore]
        except OSError:
            return False
        return True
    os.symlink(target, link)
    return True


def test_pe10_link_ou_reparse_na_worktree_falha_fechado(rig: Rig) -> None:
    outside = rig.tmp / "fora"
    outside.mkdir()
    result = rig.run(lambda: _link(outside, rig.worktree / "app" / "atalho") or pytest.skip())
    assert (result.status, result.reason) == (S.INTEGRITY_VIOLATION, "link_or_reparse")


def test_pe10_symlink_de_arquivo_falha_fechado(rig: Rig, tmp_path: Path) -> None:
    if not supports_symlinks(tmp_path):
        pytest.skip("sem privilégio para symlink")
    target = rig.tmp / "alvo.txt"
    target.write_text("x", encoding="utf-8")
    result = rig.run(lambda: os.symlink(target, rig.worktree / "app" / "l.txt"))
    assert result.status is S.INTEGRITY_VIOLATION


def test_pe10_raiz_do_workspace_trocada_falha_fechado(rig: Rig) -> None:
    before = rig.capture()
    moved = rig.tmp / "app-movido"
    Path(rig.workspace_path).rename(moved)
    if not _link(moved, Path(rig.workspace_path)):
        pytest.skip("sem junction/symlink")
    assert rig.verify(before).status is S.INTEGRITY_VIOLATION


@posix_only
def test_pe11_nome_nao_representavel_na_worktree_e_unverifiable(rig: Rig) -> None:
    result = rig.run(lambda: write(rig.worktree / "app" / "a\\b.py", "x\n"))
    assert (result.status, result.reason) == (S.UNVERIFIABLE, "unrepresentable_path")


@posix_only
def test_pe11_bytes_nao_utf8_na_worktree_e_unverifiable(rig: Rig) -> None:
    def effect() -> None:
        fd = os.open(os.fsencode(rig.worktree / "app") + b"/\xff.py", os.O_CREAT | os.O_WRONLY)
        os.close(fd)

    assert rig.run(effect).status is S.UNVERIFIABLE


def test_pe11_surrogate_solto_no_windows_e_unverifiable(rig: Rig) -> None:
    if not IS_WINDOWS:
        pytest.skip("caso específico do Windows (UTF-16)")

    def effect() -> None:
        try:
            write(rig.worktree / "app" / "x\ud800.py", "x\n")
        except (OSError, UnicodeError):
            pytest.skip("o filesystem recusou o nome")

    result = rig.run(effect)
    assert result.status is S.UNVERIFIABLE and result.failure is F.UNREPRESENTABLE_PATH


@posix_only
def test_fifo_na_worktree_e_entrada_nao_suportada(rig: Rig) -> None:
    result = rig.run(lambda: os.mkfifo(rig.worktree / "app" / "pipe"))  # type: ignore[attr-defined, unused-ignore]
    assert (result.status, result.reason) == (S.INTEGRITY_VIOLATION, "unsupported_entry")


@posix_only
def test_bit_de_execucao_alterado_e_mudanca(rig: Rig) -> None:
    target = rig.worktree / "app" / "a.py"
    result = rig.run(lambda: target.chmod(0o755))
    assert ("app/a.py", K.MODIFIED) in paths(result)


# ============================================================ integridade da worktree


def test_head_da_worktree_movido_e_violacao(rig: Rig) -> None:
    def effect() -> None:
        write(rig.worktree / "app" / "a.py", "A = 2\n")
        git(rig.worktree, "commit", "-q", "-am", "commit do teste")

    result = rig.run(effect)
    assert (result.status, result.reason) == (S.INTEGRITY_VIOLATION, "head_moved")


def test_dot_git_da_worktree_reescrito_e_violacao(rig: Rig) -> None:
    other = rig.tmp / "outro"
    git(rig.tmp, "init", "-q", str(other))

    def effect() -> None:
        # `r+b`: no Windows o `.git` da worktree é oculto, e `CREATE_ALWAYS` num oculto é negado.
        with open(rig.worktree / ".git", "r+b") as handle:
            handle.truncate(0)
            handle.write(f"gitdir: {(other / '.git').as_posix()}\n".encode())

    result = rig.run(effect)
    assert (result.status, result.reason) == (S.INTEGRITY_VIOLATION, "not_linked_worktree")


def test_prefixo_divergente_do_binding_e_violacao(rig: Rig) -> None:
    before = rig.capture()
    assert rig.verify(before, workspace_prefix="other/").status is S.INTEGRITY_VIOLATION


def test_worktree_que_e_a_propria_principal_e_recusada(rig: Rig) -> None:
    before = V.capture_main_tree(str(rig.repo), is_cancelled=never)
    result = rig.verify(
        before,
        workspace_path=str(rig.repo / "app"),
        root_identity=bind_root(rig.repo / "app").identity,
    )
    assert (result.status, result.reason) == (S.INTEGRITY_VIOLATION, "not_linked_worktree")


def test_operacao_em_andamento_e_violacao(rig: Rig) -> None:
    layout = repository_layout(str(rig.worktree))
    assert layout is not None
    admin = Path(layout.git_common_dir) / "worktrees" / rig.worktree.name
    assert admin.is_dir()
    result = rig.run(lambda: write(admin / "MERGE_HEAD", rig.base + "\n"))
    assert (result.status, result.reason) == (S.INTEGRITY_VIOLATION, "operation_in_progress")


# =================================================================== falha de leitura


def test_pe12_estado_ilegivel_antes(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ev, "main_tree_state", lambda path, budget: F.UNVERIFIABLE)
    before = rig.capture()
    monkeypatch.undo()
    assert not before.verifiable and before.failure is F.UNVERIFIABLE
    result = rig.verify(before)
    assert (result.status, result.reason) == (S.UNVERIFIABLE, ev.MAIN_TREE_UNVERIFIABLE_BEFORE)
    assert result.failure is F.UNVERIFIABLE


def test_pe12_local_path_que_nao_e_repositorio(tmp_path: Path, rig: Rig) -> None:
    plain = tmp_path / "sem-git"
    plain.mkdir()
    before = V.capture_main_tree(str(plain), is_cancelled=never)
    assert rig.verify(before).status is S.UNVERIFIABLE


def test_pe13_estado_ilegivel_depois(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    before = rig.capture()
    monkeypatch.setattr(shutil, "which", lambda name: None)  # git "sumiu" durante o run
    result = rig.verify(before)
    assert (result.status, result.reason) == (S.UNVERIFIABLE, ev.MAIN_TREE_UNVERIFIABLE_AFTER)


def test_pe14_analise_da_worktree_incompleta_e_unverifiable(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(self: object) -> object:
        raise MediatedGitError(GitFailure.OUTPUT_UNVERIFIABLE)

    before = rig.capture()
    monkeypatch.setattr(MediatedGit, "list_index", broken)
    result = rig.verify(before)
    assert (result.status, result.failure) == (S.UNVERIFIABLE, F.UNVERIFIABLE)


def test_pe14_listagem_do_disco_que_falha_e_unverifiable(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = rig.capture()
    real = pe._scandir

    def flaky(path: str) -> Any:
        if "ff-task-" in str(path):
            raise PermissionError("negado")
        return real(path)

    monkeypatch.setattr(pe, "_scandir", flaky)
    assert rig.verify(before).status is S.UNVERIFIABLE


def test_pe14_base_commit_fora_do_formato(rig: Rig) -> None:
    before = rig.capture()
    result = rig.verify(before, base_commit="HEAD")
    assert (result.status, result.failure) == (S.UNVERIFIABLE, F.UNSUPPORTED_OBJECT_FORMAT)


# ===================================================== AUD-004: formato de objeto do Git


def test_parse_de_formato_e_oids() -> None:
    assert pe.parse_object_format(b"sha1\n") == "sha1"
    assert pe.parse_object_format(b"sha256\n") == "sha256"
    for raw in (b"sha1", b"SHA1\n", b"sha512\n", b"sha1\nsha256\n", b"", None):
        assert pe.parse_object_format(raw) is None
    assert pe.oid_pattern("sha1").fullmatch("a" * 40)
    assert not pe.oid_pattern("sha1").fullmatch("a" * 64)
    assert pe.oid_pattern("sha256").fullmatch("a" * 64)
    assert not pe.oid_pattern("sha256").fullmatch("a" * 40)
    with pytest.raises(KeyError):
        pe.oid_pattern("md5")


def _sha256_supported(tmp: Path) -> bool:
    probe = subprocess.run(  # noqa: S603 — argv literal, teste
        [_GIT or "git", "init", "-q", "--object-format=sha256", str(tmp / "probe")],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


@pytest.fixture
def sha256(tmp_path: Path) -> Rig:
    """Repositório **SHA-256** real e uma worktree vinculada (criada pelo harness: a criação da
    E7.4 só aceita SHA-1 — limitação dela, não da verificação)."""
    if not _sha256_supported(tmp_path):
        pytest.skip("o Git instalado não suporta --object-format=sha256")
    repo = tmp_path / "repo"
    git(tmp_path, "init", "-q", "-b", "main", "--object-format=sha256", str(repo))
    git(repo, "config", "core.autocrlf", "false")
    _seed(repo)
    base = rev(repo)
    assert len(base) == 64
    target = tmp_path / "worktrees" / "ff-task-abcdef12"
    target.parent.mkdir()
    git(
        repo,
        "-c",
        "core.autocrlf=false",
        "worktree",
        "add",
        "-q",
        "-b",
        "ff/task-abcdef12",
        str(target),
        base,
    )
    workspace = target / "app"
    return Rig(
        tmp=tmp_path,
        repo=repo,
        base=base,
        worktree=target,
        workspace_path=str(workspace),
        prefix="app/",
        local_path=str(repo / "app"),
        root_identity=bind_root(workspace).identity,
    )


def test_sha256_1_intacto_e_verified(sha256: Rig) -> None:
    assert sha256.run(lambda: None) == PostExecutionResult(status=S.VERIFIED, reason=None)


def test_sha256_2_modificado_e_detectado(sha256: Rig) -> None:
    """A7/A8/A9: formato fixo em sha1, oid de 40 ou hash sempre SHA-1 quebram aqui."""
    result = sha256.run(lambda: write(sha256.worktree / "app" / "a.py", "A = 2\n"))
    assert result.status is S.VERIFIED and paths(result) == {("app/a.py", K.MODIFIED)}


def test_sha256_3_remocao(sha256: Rig) -> None:
    result = sha256.run(lambda: (sha256.worktree / "app" / "keep.txt").unlink())
    assert paths(result) == {("app/keep.txt", K.DELETED)}


def test_sha256_4_nao_rastreado(sha256: Rig) -> None:
    result = sha256.run(lambda: write(sha256.worktree / "app" / "novo.py", "x\n"))
    assert paths(result) == {("app/novo.py", K.ADDED)}


def test_sha256_5_indice_divergente(sha256: Rig) -> None:
    def effect() -> None:
        write(sha256.worktree / "app" / "novo.py", "x\n")
        git(sha256.worktree, "add", "app/novo.py")

    assert ("app/novo.py", K.INDEX) in paths(sha256.run(effect))


def test_sha256_6_fora_do_prefixo(sha256: Rig) -> None:
    result = sha256.run(lambda: write(sha256.worktree / "other" / "x.txt", "fora\n"))
    assert result.status is S.OUTSIDE_WORKSPACE_CHANGE


def test_sha256_7_segredo(sha256: Rig) -> None:
    result = sha256.run(lambda: write(sha256.worktree / "app" / ".env", "S=1\n"))
    assert result.status is S.PATH_POLICY_DENIED


def test_sha256_base_commit_sha1_e_recusado(sha256: Rig) -> None:
    before = sha256.capture()
    result = sha256.verify(before, base_commit="a" * 40)
    assert result.failure is F.UNSUPPORTED_OBJECT_FORMAT


# ============================================================ AUD-005: orçamento limitado


class FakeDir:
    """Um diretório sintético de ``n`` entradas (ou infinito), que conta o que foi consumido."""

    def __init__(self, n: int | None) -> None:
        self.n = n
        self.yielded = 0

    def __enter__(self) -> FakeDir:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def __iter__(self) -> Iterator[SimpleNamespace]:
        while self.n is None or self.yielded < self.n:
            self.yielded += 1
            yield SimpleNamespace(name=f"f{self.yielded}", path=f"/sintetico/f{self.yielded}")


_FAKE_FILE = os.stat_result((stat.S_IFREG | 0o644, 7, 1, 1, 0, 0, 0, 0, 0, 0), _NO_ATTRIBUTES)


def _fake_dir(monkeypatch: pytest.MonkeyPatch, directory: FakeDir) -> None:
    monkeypatch.setattr(pe, "_scandir", lambda path: directory)
    monkeypatch.setattr(pe, "_lstat", lambda path: _FAKE_FILE)


def test_bound_1_cem_mil_entradas_sao_permitidas(monkeypatch: pytest.MonkeyPatch) -> None:
    directory = FakeDir(pe.MAX_ENTRIES)
    _fake_dir(monkeypatch, directory)
    meter = budget()
    assert len(pe._children("/sintetico", meter)) == 100_000
    assert meter.entries_seen == 100_000 == directory.yielded


def test_bound_2_e_11_a_entrada_100001_para_imediatamente(monkeypatch: pytest.MonkeyPatch) -> None:
    """A10: `list(iterator)` antes de cobrar consumiria o iterador infinito para sempre."""
    directory = FakeDir(None)
    _fake_dir(monkeypatch, directory)
    meter = budget()
    with pytest.raises(pe.BudgetExhausted) as info:
        pe._children("/sintetico", meter)
    assert info.value.failure is F.LIMIT_EXCEEDED
    assert directory.yielded == 100_001 and meter.entries_seen == 100_000


def test_bound_3_e_4_um_gib_exato_e_o_byte_seguinte() -> None:
    meter = budget()
    meter.consume_bytes(pe.MAX_BYTES_READ)  # contadores, não 1 GiB alocado
    assert meter.bytes_read == 1_073_741_824
    with pytest.raises(pe.BudgetExhausted) as info:
        meter.consume_bytes(1)
    assert info.value.failure is F.LIMIT_EXCEEDED
    assert (pe.MAX_ENTRIES, pe.MAX_BYTES_READ, pe.DEADLINE_S) == (100_000, 1_073_741_824, 60.0)


class Reader:
    """Arquivo sintético que **cresce**: devolve sempre o que pedirem, e conta."""

    def __init__(self) -> None:
        self.requests: list[int] = []

    def __call__(self, size: int) -> bytes:
        self.requests.append(size)
        return b"x" * size


def test_bound_5_arquivo_que_cresce_e_cobrado_pelos_bytes_reais() -> None:
    """A11: cobrar pelo `stat` (5 bytes) deixaria ler sem limite; cobra-se o que veio."""
    meter = budget()
    meter.consume_bytes(pe.MAX_BYTES_READ - 3)
    reader = Reader()
    with pytest.raises(pe.BudgetExhausted) as info:
        pe.stream_digest(reader, 5, lambda chunk: None, meter)
    assert info.value.failure is F.LIMIT_EXCEEDED
    assert reader.requests == [4]  # no máximo o que resta do orçamento + 1; e parou ali
    assert meter.bytes_read == pe.MAX_BYTES_READ + 1


def test_bound_5_crescimento_sem_orcamento_esgotado_e_changed_during_read() -> None:
    reader = Reader()
    with pytest.raises(pe._Failed) as info:
        pe.stream_digest(reader, 10, lambda chunk: None, budget())
    assert info.value.failure is F.CHANGED_DURING_READ
    assert reader.requests == [11]  # o tamanho + 1 byte para ver o crescimento, e nada mais


def test_arquivo_grande_e_lido_em_blocos_sem_buffer_inteiro() -> None:
    reader = Reader()
    seen: list[int] = []
    pe.stream_digest(
        lambda n: reader(n) if sum(reader.requests) < 3 * pe._CHUNK else b"",
        3 * pe._CHUNK,
        lambda chunk: seen.append(len(chunk)),
        budget(),
    )
    assert max(seen) <= pe._CHUNK and sum(seen) == 3 * pe._CHUNK


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_bound_6_prazo_cruzado_no_hash_e_deadline_exceeded() -> None:
    """A12: sem checagem de prazo no laço, o hash seguiria até o fim."""
    clock = Clock()
    meter = budget(clock=clock)
    calls: list[int] = []

    def read(size: int) -> bytes:
        calls.append(size)
        clock.now = 61.0  # o relógio cruza 60 s durante o hash
        return b"x"

    with pytest.raises(pe.BudgetExhausted) as info:
        pe.stream_digest(read, 10, lambda chunk: None, meter)
    assert info.value.failure is F.DEADLINE_EXCEEDED and len(calls) == 1


def test_bound_6_prazo_cruzado_na_travessia(rig: Rig) -> None:
    clock = Clock()
    calls = {"n": 0}

    def ticking() -> float:
        calls["n"] += 1
        if calls["n"] > 40:
            clock.now = 61.0
        return clock.now

    snapshot = rig.capture(PostExecutionVerifier(clock=ticking))
    assert snapshot.failure is F.DEADLINE_EXCEEDED
    result = rig.verify(snapshot)
    assert (result.status, result.failure) == (S.UNVERIFIABLE, F.DEADLINE_EXCEEDED)


def test_bound_7_cancelamento_na_travessia(monkeypatch: pytest.MonkeyPatch) -> None:
    """A14: sem checar o cancelamento na enumeração, o diretório seria lido até o fim."""
    directory = FakeDir(None)
    _fake_dir(monkeypatch, directory)
    meter = budget(is_cancelled=lambda: directory.yielded >= 10)
    with pytest.raises(pe.BudgetExhausted) as info:
        pe._children("/sintetico", meter)
    assert info.value.failure is F.CANCELLED and directory.yielded == 10  # parou na hora


def test_bound_8_cancelamento_no_hash() -> None:
    """A13: sem checar o cancelamento no laço de leitura, o hash seguiria."""
    flag = {"cancel": False}
    calls: list[int] = []

    def read(size: int) -> bytes:
        calls.append(size)
        flag["cancel"] = True
        return b"x"

    with pytest.raises(pe.BudgetExhausted) as info:
        pe.stream_digest(read, 10, lambda chunk: None, budget(is_cancelled=lambda: flag["cancel"]))
    assert info.value.failure is F.CANCELLED and len(calls) == 1


def test_cancelamento_antes_e_depois_da_leitura_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[float | None] = []

    def fake_run(git: str, cwd: str, *args: str, **kwargs: Any) -> Any:
        seen.append(kwargs.get("timeout"))
        flag["cancel"] = True  # o cancelamento chega **durante** o processo
        return SimpleNamespace(returncode=0, stdout=b"")

    flag = {"cancel": True}
    monkeypatch.setattr(pe, "_run_git", fake_run)
    with pytest.raises(pe.BudgetExhausted):  # antes: nem inicia
        pe._git(budget(is_cancelled=lambda: flag["cancel"]), "git", str(tmp_path), "rev-parse")
    assert seen == []
    flag["cancel"] = False
    with pytest.raises(pe.BudgetExhausted) as info:  # depois: a saída não é usada
        pe._git(budget(is_cancelled=lambda: flag["cancel"]), "git", str(tmp_path), "rev-parse")
    assert info.value.failure is F.CANCELLED and len(seen) == 1


def test_bound_9_leitura_git_recebe_o_prazo_restante(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    timeouts: list[float] = []

    def fake_run(git: str, cwd: str, *args: str, **kwargs: Any) -> SimpleNamespace:
        timeouts.append(kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout=b"")

    monkeypatch.setattr(pe, "_run_git", fake_run)
    clock = Clock()
    meter = budget(clock=clock)
    clock.now = 58.0
    pe._git(meter, "git", str(tmp_path), "rev-parse")
    assert timeouts == [pytest.approx(2.0)]
    clock.now = 60.0
    with pytest.raises(pe.BudgetExhausted):
        pe._git(meter, "git", str(tmp_path), "rev-parse")
    assert len(timeouts) == 1


def test_bound_9_run_git_nunca_passa_do_prazo_fixo_nem_do_restante(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import git_runtime as gr

    recorded: list[float] = []

    def fake_run(*args: object, **kwargs: Any) -> SimpleNamespace:
        recorded.append(kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    gr._run_git("git", ".", "rev-parse", timeout=1.5)
    gr._run_git("git", ".", "rev-parse", timeout=500.0)
    gr._run_git("git", ".", "rev-parse")
    assert recorded == [1.5, gr._TIMEOUT_SECONDS, gr._TIMEOUT_SECONDS]


def test_bound_10_trabalho_incompleto_nunca_e_verified(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = rig.capture()
    write(rig.worktree / "app" / "a.py", "A = 2\n")
    monkeypatch.setattr(pe, "MAX_ENTRIES", 3)  # o "depois" da principal já não cabe
    result = rig.verify(before)
    assert (result.status, result.failure) == (S.UNVERIFIABLE, F.LIMIT_EXCEEDED)


def test_combinado_um_orcamento_so_para_o_verify_inteiro(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A15: um orçamento novo por subpasso faria cada metade caber e liberaria o run."""
    state = main_tree_state(rig.local_path, budget())
    assert isinstance(state, pe.MainTreeState)
    meter = budget()
    observed = worktree_changes(
        rig.workspace_path,
        base_commit=rig.base,
        expected_prefix=rig.prefix,
        main_toplevel=state.toplevel,
        main_git_common_dir=state.git_common_dir,
        budget=meter,
    )
    assert isinstance(observed, pe.WorktreeChanges)
    main_entries, worktree_entries = state.entry_count, meter.entries_seen
    before = rig.capture()
    monkeypatch.setattr(pe, "MAX_ENTRIES", max(main_entries, worktree_entries) + 1)
    result = rig.verify(before)
    assert (result.status, result.failure) == (S.UNVERIFIABLE, F.LIMIT_EXCEEDED)


def test_cancelamento_durante_o_verify(rig: Rig) -> None:
    before = rig.capture()
    calls = {"n": 0}

    def cancel_later() -> bool:
        calls["n"] += 1
        return calls["n"] > 25

    result = rig.verify(before, is_cancelled=cancel_later)
    assert (result.status, result.failure) == (S.UNVERIFIABLE, F.CANCELLED)


def test_cancelamento_que_levanta_e_cancelamento(rig: Rig) -> None:
    def explode() -> bool:
        raise RuntimeError(SENTINEL)

    snapshot = V.capture_main_tree(rig.local_path, is_cancelled=explode)
    assert snapshot.failure is F.CANCELLED and SENTINEL not in repr(snapshot)


# ==================================== FINAL-001: nenhum VERIFIED sem a checagem final


class Timed:
    """Um verificador com relógio controlado e um cancelamento comandável."""

    def __init__(self) -> None:
        self.clock = Clock()
        self.cancel = False
        self.verifier = PostExecutionVerifier(clock=self.clock)

    def is_cancelled(self) -> bool:
        return self.cancel

    def expire(self) -> None:
        self.clock.now = 61.0  # o prazo de 60 s passa

    def cancel_now(self) -> None:
        self.cancel = True


def _after(
    monkeypatch: pytest.MonkeyPatch, owner: object, name: str, effect: Callable[[], None]
) -> list[int]:
    """Troca ``owner.name`` por um wrapper que roda a **original** e depois ``effect``."""
    original = getattr(owner, name)
    calls: list[int] = []

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        calls.append(1)
        effect()
        return result

    monkeypatch.setattr(owner, name, wrapper)
    return calls


def _timed_run(rig: Rig, timed: Timed, arm: Callable[[], object]) -> PostExecutionResult:
    """Captura, modifica um arquivo **permitido** da worktree, arma o gatilho e verifica."""
    before = rig.capture(timed.verifier)
    write(rig.worktree / "app" / "a.py", "A = 2\n")
    arm()
    return rig.verify(before, verifier=timed.verifier, is_cancelled=timed.is_cancelled)


def test_f001_8_caminho_feliz_com_orcamento_valido_e_verified(rig: Rig) -> None:
    """Contraprova dos F001: o mesmo cenário, sem gatilho, libera."""
    timed = Timed()
    result = _timed_run(rig, timed, lambda: None)
    assert result.status is S.VERIFIED and result.failure is None
    assert paths(result) == {("app/a.py", K.MODIFIED)}


@pytest.mark.parametrize(
    ("trigger", "failure"),
    [("expire", F.DEADLINE_EXCEEDED), ("cancel_now", F.CANCELLED)],
    ids=["F001_1-prazo", "F001_2-cancelamento"],
)
def test_f001_1_e_2_cruza_durante_a_ultima_inspecao(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, trigger: str, failure: VerificationFailure
) -> None:
    """A contraprova do Codex: a inspeção original roda e o prazo/cancelamento cruza nela."""
    timed = Timed()
    calls: list[list[int]] = []
    result = _timed_run(
        rig,
        timed,
        lambda: calls.append(_after(monkeypatch, ev, "inspect", getattr(timed, trigger))),
    )
    assert calls[0] == [1]  # a inspeção original rodou, uma vez (a última)
    assert result.status is S.UNVERIFIABLE and result.failure is failure
    assert result.reason == failure.value


@pytest.mark.parametrize(
    ("trigger", "failure"),
    [("expire", F.DEADLINE_EXCEEDED), ("cancel_now", F.CANCELLED)],
    ids=["F001_3-prazo", "F001_4-cancelamento"],
)
def test_f001_3_e_4_cruza_depois_da_ultima_chamada_git(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, trigger: str, failure: VerificationFailure
) -> None:
    """`index_divergence` é a última chamada Git mediada da análise da worktree."""
    timed = Timed()
    calls: list[list[int]] = []
    result = _timed_run(
        rig,
        timed,
        lambda: calls.append(
            _after(monkeypatch, MediatedGit, "index_divergence", getattr(timed, trigger))
        ),
    )
    assert calls[0] == [1]  # a chamada Git original completou
    assert result.status is S.UNVERIFIABLE and result.failure is failure


@pytest.mark.parametrize(
    ("trigger", "failure"),
    [("expire", F.DEADLINE_EXCEEDED), ("cancel_now", F.CANCELLED)],
    ids=["prazo", "cancelamento"],
)
def test_f001_imediatamente_antes_do_return_verified(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, trigger: str, failure: VerificationFailure
) -> None:
    """A decisão de política é o último passo antes de liberar: só o gate final o vê (MFINAL1)."""
    timed = Timed()
    result = _timed_run(
        rig, timed, lambda: _after(monkeypatch, ev, "decide_path", getattr(timed, trigger))
    )
    assert result.status is S.UNVERIFIABLE and result.failure is failure


def test_f001_gate_final_e_o_unico_construtor_de_verified() -> None:
    fonte = Path(ev.__file__).read_text(encoding="utf-8")
    assert fonte.count("status=PostExecutionStatus.VERIFIED") == 1
    gate = inspect.getsource(ev._verified)
    assert "status=PostExecutionStatus.VERIFIED" in gate
    assert gate.index("budget.check()") < gate.index("status=PostExecutionStatus.VERIFIED")


class SupervisorSpy:
    """Espia o `run_supervised` do Git mediado: registra o `timeout_s` **pedido** e roda o
    processo de verdade com folga (o teste mede o pedido, não a velocidade da máquina)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, after: Callable[[], None] = lambda: None):
        from app.git_runtime import mediated
        from app.process_runtime import run_supervised

        self.timeouts: list[float] = []
        self.real = run_supervised
        self.after = after
        monkeypatch.setattr(mediated, "run_supervised", self)

    def __call__(self, spec: Any, is_cancelled: Callable[[], bool]) -> Any:
        self.timeouts.append(spec.timeout_s)
        result = self.real(replace(spec, timeout_s=60.0), is_cancelled)
        self.after()
        return result


def test_f001_5_restando_1s_o_git_mediado_recebe_no_maximo_1s(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    timed = Timed()
    spy = SupervisorSpy(monkeypatch)

    def arm() -> None:  # 59 s consumidos assim que a identidade da worktree é conferida
        _after(monkeypatch, pe, "_check_identity", lambda: setattr(timed.clock, "now", 59.0))

    result = _timed_run(rig, timed, arm)
    assert len(spy.timeouts) >= 4  # object_format, ls-tree, ls-files, diff-index
    assert all(0.0 < timeout <= 1.0 for timeout in spy.timeouts), spy.timeouts
    assert result.status is S.VERIFIED


def test_f001_6_cada_chamada_git_recalcula_o_restante(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    timed = Timed()
    remaining_at_call: list[float] = []

    def tick() -> None:
        timed.clock.now += 0.2

    spy = SupervisorSpy(monkeypatch, after=tick)
    real_call = spy.__call__

    def arm() -> None:
        _after(monkeypatch, pe, "_check_identity", lambda: setattr(timed.clock, "now", 59.0))

    def recording(spec: Any, is_cancelled: Callable[[], bool]) -> Any:
        remaining_at_call.append(60.0 - timed.clock.now)
        return real_call(spec, is_cancelled)

    from app.git_runtime import mediated

    monkeypatch.setattr(mediated, "run_supervised", recording)
    result = _timed_run(rig, timed, arm)
    assert len(spy.timeouts) >= 4
    assert spy.timeouts == pytest.approx(remaining_at_call)  # nunca o valor da criação
    assert all(a > b for a, b in zip(spy.timeouts, spy.timeouts[1:], strict=False))
    assert spy.timeouts[0] == pytest.approx(1.0)
    assert result.status is S.VERIFIED


def test_f001_7_sem_prazo_restante_o_git_nem_inicia(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = SupervisorSpy(monkeypatch)
    for remaining in (0.0, -1.0, float("nan")):
        gitx = MediatedGit(
            str(tmp_path),
            expected_prefix="",
            is_cancelled=never,
            capture_bytes=1024,
            timeout_s=20.0,
            remaining_s=lambda value=remaining: value,  # type: ignore[misc]
        )
        with pytest.raises(MediatedGitError) as info:
            _ = gitx.object_format
        assert info.value.failure is GitFailure.TIMEOUT

    def broken() -> float:
        raise RuntimeError(SENTINEL)

    gitx = MediatedGit(
        str(tmp_path),
        expected_prefix="",
        is_cancelled=never,
        capture_bytes=1024,
        remaining_s=broken,
    )
    with pytest.raises(MediatedGitError) as info:
        _ = gitx.object_format
    assert SENTINEL not in str(info.value)
    assert spy.timeouts == []  # nenhum processo nasceu

    # o leitor `rev-parse` (runner de leitura): o prazo acaba entre a checagem e o cálculo
    started: list[object] = []
    monkeypatch.setattr(pe, "_run_git", lambda *a, **k: started.append(k))
    ticks = iter([0.0, 59.5, 60.0])  # criação, `check`, `remaining_s`
    meter = budget(clock=lambda: next(ticks))
    with pytest.raises(pe.BudgetExhausted) as exhausted:
        pe._git(meter, "git", str(tmp_path), "rev-parse")
    assert exhausted.value.failure is F.DEADLINE_EXCEEDED and started == []


def test_f001_mediated_git_timeout_e_por_chamada_e_o_default_e_historico(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.git_runtime import mediated

    spy = SupervisorSpy(monkeypatch)
    for remaining_s, expected in (
        (None, mediated.TIMEOUT_S),
        (lambda: 1.0, 1.0),
        (lambda: 500.0, 20.0),
    ):
        gitx = MediatedGit(
            str(tmp_path),
            expected_prefix="",
            is_cancelled=never,
            capture_bytes=1024,
            timeout_s=None if remaining_s is None else 20.0,
            remaining_s=remaining_s,
        )
        with contextlib.suppress(MediatedGitError):  # `tmp_path` não é repositório
            _ = gitx.object_format
        assert spy.timeouts[-1] == pytest.approx(expected)  # nunca aumenta o restante


# ============================== FINAL-002: diretório revalidado antes e depois de descer


def _is_target(path: str | os.PathLike[str], *, worktree: bool) -> bool:
    text = os.path.normcase(os.fspath(path))
    inside = "ff-task-" in text
    return text.endswith(os.path.normcase(os.path.join("app", "ordinary"))) and inside is worktree


def _after_first_lstat(
    monkeypatch: pytest.MonkeyPatch, action: Callable[[], None], *, worktree: bool = True
) -> dict[str, bool]:
    """Depois do **primeiro** `lstat` de `app/ordinary` (a descoberta, que o vê diretório
    comum), executa ``action`` — a troca acontece enquanto ele espera na pilha."""
    real = pe._lstat
    state = {"done": False}

    def hooked(path: Any) -> os.stat_result:
        info = real(path)
        if not state["done"] and _is_target(path, worktree=worktree):
            state["done"] = True
            action()
        return info

    monkeypatch.setattr(pe, "_lstat", hooked)
    return state


def _enumerations_after_swap(
    monkeypatch: pytest.MonkeyPatch, state: dict[str, bool], *, worktree: bool = True
) -> list[str]:
    """Quantas vezes `app/ordinary` foi **enumerado** depois da troca: a revalidação anterior
    precisa recusar antes do `scandir` — o diretório externo nunca é lido (MFINAL5)."""
    real = pe._scandir
    after: list[str] = []

    def spying(path: Any) -> Any:
        if state["done"] and _is_target(path, worktree=worktree):
            after.append("scandir")
        return real(path)

    monkeypatch.setattr(pe, "_scandir", spying)
    return after


def _to_link(rig: Rig, directory: Path, *, symlink: bool = False) -> Callable[[], None]:
    """Move o diretório para fora (conteúdo **equivalente**) e põe um link no lugar."""
    outside = rig.tmp / f"externo-{directory.parent.parent.name}"

    def swap() -> None:
        directory.rename(outside)
        if symlink:
            os.symlink(outside, directory, target_is_directory=True)
        elif not _link(outside, directory):
            pytest.skip("sem junction/symlink neste ambiente")

    return swap


@pytest.fixture
def ordinary(tmp_path: Path) -> Rig:
    return make_rig(tmp_path, ordinary=True)


def test_f002_contraprova_sem_troca_e_verified(ordinary: Rig) -> None:
    assert ordinary.run(lambda: None).status is S.VERIFIED


def test_f002_1_junction_trocada_depois_da_descoberta_na_worktree(
    ordinary: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A contraprova do Codex: `lstat` viu diretório, a junction entra antes da descida e
    aponta para um diretório externo de conteúdo equivalente."""
    before = ordinary.capture()
    target = ordinary.worktree / "app" / "ordinary"
    state = _after_first_lstat(monkeypatch, _to_link(ordinary, target))
    enumerated = _enumerations_after_swap(monkeypatch, state)
    result = ordinary.verify(before)
    assert state["done"] and enumerated == []
    assert (result.status, result.failure) == (S.INTEGRITY_VIOLATION, F.LINK_OR_REPARSE)


def test_f002_1_junction_trocada_depois_da_descoberta_na_principal(
    ordinary: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = ordinary.capture()
    target = ordinary.repo / "app" / "ordinary"
    state = _after_first_lstat(monkeypatch, _to_link(ordinary, target), worktree=False)
    enumerated = _enumerations_after_swap(monkeypatch, state, worktree=False)
    result = ordinary.verify(before)
    assert state["done"] and enumerated == []
    assert (result.status, result.reason, result.failure) == (
        S.UNVERIFIABLE,
        ev.MAIN_TREE_UNVERIFIABLE_AFTER,
        F.LINK_OR_REPARSE,
    )


class _SwapOnExit:
    """O iterador real do `scandir`; ao sair (enumeração completa), executa a troca."""

    def __init__(self, real: Any, action: Callable[[], None]) -> None:
        self.real = real
        self.action = action

    def __enter__(self) -> Any:
        return self.real.__enter__()

    def __exit__(self, *exc: object) -> None:
        self.real.__exit__(*exc)
        self.action()


def test_f002_2_troca_durante_a_enumeracao_e_vista_na_revalidacao_posterior(
    ordinary: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checagem **anterior** passou; a troca acontece durante a enumeração. Sem a checagem
    posterior, os filhos seriam lidos através da junction e tudo "bateria" (MFINAL6)."""
    before = ordinary.capture()
    target = ordinary.worktree / "app" / "ordinary"
    swap = _to_link(ordinary, target)
    real = pe._scandir
    state = {"done": False}

    def hooked(path: Any) -> Any:
        if not state["done"] and _is_target(path, worktree=True):
            state["done"] = True
            return _SwapOnExit(real(path), swap)
        return real(path)

    monkeypatch.setattr(pe, "_scandir", hooked)
    result = ordinary.verify(before)
    assert state["done"]
    assert (result.status, result.failure) == (S.INTEGRITY_VIOLATION, F.LINK_OR_REPARSE)


def test_f002_3_diretorio_trocado_por_outro_diretorio_comum(
    ordinary: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outro diretório **ordinário**, mesmo nome, mesmo conteúdo: só a identidade denuncia
    (MFINAL7 — usar o fato antigo da pilha deixaria passar)."""
    before = ordinary.capture()
    target = ordinary.worktree / "app" / "ordinary"

    def replace_dir() -> None:
        moved = ordinary.tmp / "ordinary-original"
        target.rename(moved)
        shutil.copytree(moved, target)

    state = _after_first_lstat(monkeypatch, replace_dir)
    result = ordinary.verify(before)
    assert state["done"]
    assert (result.status, result.failure) == (S.INTEGRITY_VIOLATION, F.CHANGED_DURING_READ)


def test_f002_4_diretorio_virou_arquivo(ordinary: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    before = ordinary.capture()
    target = ordinary.worktree / "app" / "ordinary"

    def to_file() -> None:
        shutil.rmtree(target)
        target.write_bytes(b"agora sou arquivo\n")

    state = _after_first_lstat(monkeypatch, to_file)
    result = ordinary.verify(before)
    assert state["done"]
    assert (result.status, result.failure) == (S.INTEGRITY_VIOLATION, F.CHANGED_DURING_READ)


def test_f002_5_diretorio_virou_symlink(
    ordinary: Rig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """POSIX (CI) e Windows com privilégio de symlink; o mesmo princípio da junction."""
    if not supports_symlinks(tmp_path):
        pytest.skip("sem privilégio para symlink")
    before = ordinary.capture()
    target = ordinary.worktree / "app" / "ordinary"
    state = _after_first_lstat(monkeypatch, _to_link(ordinary, target, symlink=True))
    result = ordinary.verify(before)
    assert state["done"]
    assert (result.status, result.failure) == (S.INTEGRITY_VIOLATION, F.LINK_OR_REPARSE)


def test_f002_revalidacao_respeita_prazo_e_cancelamento(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MFINAL8: a revalidação nova também checa o orçamento — antes e depois do `lstat`."""
    directory = tmp_path / "d"
    directory.mkdir()
    identity = _identity_of(os.lstat(directory))
    pe._revalidate_directory(str(directory), identity, budget=budget())  # válido

    real = pe._lstat
    seen: list[object] = []

    def observing(path: Any) -> os.stat_result:
        seen.append(path)
        return real(path)

    monkeypatch.setattr(pe, "_lstat", observing)
    clock = Clock()
    meter = VerificationBudget(is_cancelled=never, clock=clock)
    clock.now = 61.0  # o prazo já passou: a revalidação nem observa
    with pytest.raises(pe.BudgetExhausted) as info:
        pe._revalidate_directory(str(directory), identity, budget=meter)
    assert info.value.failure is F.DEADLINE_EXCEEDED and seen == []

    for trigger, failure in (("expire", F.DEADLINE_EXCEEDED), ("cancel_now", F.CANCELLED)):
        timed = Timed()
        meter = VerificationBudget(is_cancelled=timed.is_cancelled, clock=timed.clock)
        _crossing_lstat(monkeypatch, real, getattr(timed, trigger))
        with pytest.raises(pe.BudgetExhausted) as info:
            pe._revalidate_directory(str(directory), identity, budget=meter)
        assert info.value.failure is failure


def _identity_of(info: os.stat_result) -> ObjectIdentity:
    return ObjectIdentity(volume_id=info.st_dev, file_id=info.st_ino)


def _crossing_lstat(
    monkeypatch: pytest.MonkeyPatch,
    real: Callable[[Any], os.stat_result],
    action: Callable[[], None],
) -> None:
    """O `lstat` da revalidação, durante o qual o prazo/cancelamento cruza."""

    def during(path: Any) -> os.stat_result:
        info = real(path)
        action()
        return info

    monkeypatch.setattr(pe, "_lstat", during)


# ===================== FINAL-002 (identidade): `file_id == 0` não prova continuidade


def _without_file_id(info: os.stat_result) -> os.stat_result:
    """O mesmo `stat`, como um filesystem **sem índice estável** o devolveria: `file_id == 0`."""
    fields = (
        info.st_mode,
        0,
        info.st_dev,
        info.st_nlink,
        info.st_uid,
        info.st_gid,
        info.st_size,
        int(info.st_atime),
        int(info.st_mtime),
        int(info.st_ctime),
    )
    extra = {
        name: getattr(info, name)
        for name in ("st_file_attributes", "st_reparse_tag")
        if hasattr(info, name)
    }
    return os.stat_result(fields, extra)


def _zero_ids(
    monkeypatch: pytest.MonkeyPatch,
    match: Callable[[Any], bool],
    *,
    from_call: int = 1,
    on_first: Callable[[], None] = lambda: None,
) -> dict[str, int]:
    """Os `lstat` de quem ``match`` passam a vir sem `file_id` a partir da chamada ``from_call``;
    ``on_first`` roda depois da primeira observação (a descoberta)."""
    real = pe._lstat
    calls = {"n": 0}

    def hooked(path: Any) -> os.stat_result:
        info = real(path)
        if not match(path):
            return info
        calls["n"] += 1
        if calls["n"] == 1:
            on_first()
        return _without_file_id(info) if calls["n"] >= from_call else info

    monkeypatch.setattr(pe, "_lstat", hooked)
    return calls


def _never_enumerated(monkeypatch: pytest.MonkeyPatch, match: Callable[[Any], bool]) -> list[str]:
    real = pe._scandir
    seen: list[str] = []

    def spying(path: Any) -> Any:
        if match(path):
            seen.append("scandir")
        return real(path)

    monkeypatch.setattr(pe, "_scandir", spying)
    return seen


def _ordinary_in(*, worktree: bool) -> Callable[[Any], bool]:
    return lambda path: _is_target(path, worktree=worktree)


def test_f002_zero_1_diretorio_sem_file_id_nao_e_aceito_na_worktree(
    ordinary: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = ordinary.capture()
    _zero_ids(monkeypatch, _ordinary_in(worktree=True))
    enumerated = _never_enumerated(monkeypatch, _ordinary_in(worktree=True))
    result = ordinary.verify(before)
    assert (result.status, result.failure) == (S.INTEGRITY_VIOLATION, F.INTEGRITY_UNVERIFIABLE)
    assert result.reason == "integrity_unverifiable" and enumerated == []
    no_absolute(ordinary, result)


def test_f002_zero_1_diretorio_sem_file_id_nao_e_aceito_na_principal(
    ordinary: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = ordinary.capture()
    _zero_ids(monkeypatch, _ordinary_in(worktree=False))
    result = ordinary.verify(before)
    assert (result.status, result.reason, result.failure) == (
        S.UNVERIFIABLE,
        ev.MAIN_TREE_UNVERIFIABLE_AFTER,
        F.INTEGRITY_UNVERIFIABLE,
    )


def test_f002_zero_2_troca_entre_dois_diretorios_comuns_sem_identidade(
    ordinary: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relevância: sem `file_id`, A e B são "o mesmo" para `(volume, 0) == (volume, 0)`. B é
    um diretório comum, sem link/reparse, de conteúdo equivalente — tipo não prova nada."""
    before = ordinary.capture()
    target = ordinary.worktree / "app" / "ordinary"

    def swap_for_b() -> None:
        moved = ordinary.tmp / "ordinary-a"
        target.rename(moved)
        shutil.copytree(moved, target)  # B: ordinário, outro objeto

    calls = _zero_ids(monkeypatch, _ordinary_in(worktree=True), on_first=swap_for_b)
    enumerated = _never_enumerated(monkeypatch, _ordinary_in(worktree=True))
    result = ordinary.verify(before)
    assert calls["n"] >= 1 and enumerated == []  # B nunca é lido como se fosse A
    assert (result.status, result.failure) == (S.INTEGRITY_VIOLATION, F.INTEGRITY_UNVERIFIABLE)


def test_f002_zero_3_identidade_pos_descida_nao_verificavel(
    ordinary: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Descoberta e revalidação anterior com identidade real; a **posterior** vem sem `file_id`:
    não dá para afirmar que é o mesmo diretório."""
    before = ordinary.capture()
    calls = _zero_ids(monkeypatch, _ordinary_in(worktree=True), from_call=3)
    result = ordinary.verify(before)
    assert calls["n"] == 3  # descoberta, antes, depois
    assert (result.status, result.failure) == (S.INTEGRITY_VIOLATION, F.INTEGRITY_UNVERIFIABLE)


def test_f002_zero_identidade_esperada_nao_verificavel_nem_observa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "d"
    directory.mkdir()
    seen: list[object] = []
    monkeypatch.setattr(pe, "_lstat", lambda path: seen.append(path))
    unproven = ObjectIdentity(volume_id=os.lstat(directory).st_dev, file_id=0)
    with pytest.raises(pe._Failed) as info:
        pe._revalidate_directory(str(directory), unproven, budget=budget())
    assert info.value.failure is F.INTEGRITY_UNVERIFIABLE and seen == []


def _same_dir(target: Path) -> Callable[[Any], bool]:
    wanted = os.path.normcase(os.path.normpath(str(target)))
    return lambda path: os.path.normcase(os.path.normpath(os.fspath(path))) == wanted


def test_f002_zero_raiz_da_principal_sem_file_id_falha_fechado(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FOCAL_5 (principal): a raiz ancora o anti-replay e a travessia — sem `file_id`, falha."""
    calls = _zero_ids(monkeypatch, _same_dir(rig.repo))
    snapshot = rig.capture()
    assert calls["n"] >= 1 and snapshot.failure is F.INTEGRITY_UNVERIFIABLE
    monkeypatch.undo()
    assert rig.verify(snapshot).failure is F.INTEGRITY_UNVERIFIABLE


def test_f002_zero_raiz_da_worktree_sem_file_id_falha_fechado(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FOCAL_5 (worktree)."""
    state = main_tree_state(rig.local_path, budget())
    assert isinstance(state, pe.MainTreeState)
    calls = _zero_ids(monkeypatch, _same_dir(rig.worktree))
    observed = worktree_changes(
        rig.workspace_path,
        base_commit=rig.base,
        expected_prefix=rig.prefix,
        main_toplevel=state.toplevel,
        main_git_common_dir=state.git_common_dir,
        budget=budget(),
    )
    assert calls["n"] >= 1 and observed is F.INTEGRITY_UNVERIFIABLE


def test_f002_zero_raiz_do_workspace_sem_file_id_continua_recusada_pelo_bind_root(
    rig: Rig,
) -> None:
    """Regra já coberta pelo `bind_root` (E7.5): preservada, não duplicada."""
    before = rig.capture()
    unproven = ObjectIdentity(volume_id=rig.root_identity.volume_id, file_id=0)
    result = rig.verify(before, root_identity=unproven)
    assert (result.status, result.reason) == (S.INTEGRITY_VIOLATION, ev.WORKSPACE_ROOT_INTEGRITY)


def test_f002_zero_arquivo_regular_sem_file_id_nao_tem_leitura_estavel(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leitura estável compara `lstat` × `fstat` × `lstat`: sem `file_id`, não prova nada."""
    before = rig.capture()
    is_a_py = lambda path: (  # noqa: E731
        "ff-task-" in os.fspath(path) and os.fspath(path).endswith("a.py")
    )
    _zero_ids(monkeypatch, is_a_py)
    result = rig.verify(before)
    assert (result.status, result.failure) == (S.INTEGRITY_VIOLATION, F.INTEGRITY_UNVERIFIABLE)
    with pytest.raises(pe._Failed) as info:
        pe._same_object(_without_file_id(os.stat(__file__)), _without_file_id(os.stat(__file__)))
    assert info.value.failure is F.INTEGRITY_UNVERIFIABLE


def test_f002_raiz_trocada_e_detectada(tmp_path: Path) -> None:
    root = tmp_path / "raiz"
    root.mkdir()
    identity = _identity_of(os.stat(root))
    root.rename(tmp_path / "raiz-velha")
    root.mkdir()  # mesma grafia, outro objeto
    with pytest.raises(pe._Failed) as info:
        pe._revalidate_directory(str(root), identity, budget=budget())
    assert info.value.failure is F.CHANGED_DURING_READ


# ========================= FOCAL-001: a raiz recebe a mesma prova de qualquer diretório


def _move_and_link(rig: Rig, directory: Path) -> Callable[[], None]:
    """Move o diretório e põe, no caminho original, uma junction (symlink no POSIX) para o
    **mesmo** diretório movido: a identidade do alvo continua igual à ancorada."""

    def swap() -> None:
        moved = rig.tmp / f"{directory.name}-movido"
        directory.rename(moved)
        if not _link(moved, directory):
            pytest.skip("sem junction/symlink neste ambiente")

    return swap


def _after_anchor(
    monkeypatch: pytest.MonkeyPatch,
    match: Callable[[Any], bool],
    action: Callable[[], None],
    *,
    nth: int = 1,
) -> dict[str, Any]:
    """Depois da ``nth``-ésima ancoragem (`_anchor_directory`) do caminho que ``match`` aceita,
    roda ``action``."""
    real = pe._anchor_directory
    state: dict[str, Any] = {"done": False, "anchored": None, "seen": 0}

    def hooked(path: str, budget: VerificationBudget) -> ObjectIdentity:
        identity = real(path, budget)
        if not state["done"] and match(path):
            state["seen"] += 1
            if state["seen"] == nth:
                state["done"], state["anchored"] = True, identity
                action()
        return identity

    monkeypatch.setattr(pe, "_anchor_directory", hooked)
    return state


def _scans_after(
    monkeypatch: pytest.MonkeyPatch, state: dict[str, Any], match: Callable[[Any], bool]
) -> list[str]:
    """Enumerações do caminho que ``match`` aceita **depois** da troca (deve ser nenhuma)."""
    real = pe._scandir
    scans: list[str] = []

    def spying(path: Any) -> Any:
        if state["done"] and match(path):
            scans.append("scandir")
        return real(path)

    monkeypatch.setattr(pe, "_scandir", spying)
    return scans


def test_focal_1_junction_persistente_na_raiz_da_worktree(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A contraprova do Codex: a raiz ordinária é ancorada, vira junction para **o mesmo**
    diretório (identidade igual) e continua junction até o fim. Só o reparse denuncia."""
    before = rig.capture()
    is_root = _same_dir(rig.worktree)
    state = _after_anchor(monkeypatch, is_root, _move_and_link(rig, rig.worktree))
    scans = _scans_after(monkeypatch, state, is_root)
    result = rig.verify(before)
    assert state["done"]
    assert _identity_of(os.stat(rig.worktree)) == state["anchored"]  # o alvo é o mesmo objeto
    assert _is_reparse(os.lstat(rig.worktree))  # e o caminho **é** reparse até o fim
    assert scans == []  # o alvo da junction nunca é enumerado
    assert (result.status, result.reason, result.failure) == (
        S.INTEGRITY_VIOLATION,
        "link_or_reparse",
        F.LINK_OR_REPARSE,
    )


@pytest.mark.parametrize(
    "nth",
    [1, 2],
    ids=["depois-da-ancora-do-caminho-pedido", "depois-da-ancora-do-toplevel"],
)
def test_focal_2_junction_persistente_na_raiz_da_principal(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, nth: int
) -> None:
    """A raiz da principal falha **pela própria** prova de reparse — não por um
    `not_linked_worktree` (nem um `main_tree_changed`) incidental depois. Duas janelas: entre a
    prova do caminho pedido e a resolução pelo Git (1ª âncora) e entre a âncora do `toplevel` e
    a descida (2ª)."""
    before = rig.capture()
    is_root = _same_dir(rig.repo)
    state = _after_anchor(monkeypatch, is_root, _move_and_link(rig, rig.repo), nth=nth)
    scans = _scans_after(monkeypatch, state, is_root)
    result = rig.verify(before)
    assert state["done"] and scans == []
    assert _identity_of(os.stat(rig.repo)) == state["anchored"]
    assert (result.status, result.reason, result.failure) == (
        S.UNVERIFIABLE,
        ev.MAIN_TREE_UNVERIFIABLE_AFTER,
        F.LINK_OR_REPARSE,
    )


def test_focal_2_raiz_da_principal_ja_junction_antes_da_operacao(rig: Rig) -> None:
    """A junction já está lá quando a operação começa: a âncora recusa (captura e verify)."""
    before = rig.capture()
    _move_and_link(rig, rig.repo)()
    result = rig.verify(before)
    assert (result.status, result.reason, result.failure) == (
        S.UNVERIFIABLE,
        ev.MAIN_TREE_UNVERIFIABLE_AFTER,
        F.LINK_OR_REPARSE,
    )
    assert V.capture_main_tree(str(rig.repo), is_cancelled=never).failure is F.LINK_OR_REPARSE


def test_focal_3_raiz_vira_junction_durante_a_enumeracao(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = rig.capture()
    is_root = _same_dir(rig.worktree)
    swap = _move_and_link(rig, rig.worktree)
    real = pe._scandir
    state = {"done": False}

    def hooked(path: Any) -> Any:
        if not state["done"] and is_root(path):
            state["done"] = True
            return _SwapOnExit(real(path), swap)
        return real(path)

    monkeypatch.setattr(pe, "_scandir", hooked)
    result = rig.verify(before)
    assert state["done"]
    assert (result.status, result.failure) == (S.INTEGRITY_VIOLATION, F.LINK_OR_REPARSE)


def test_focal_4_raiz_trocada_por_outro_diretorio_ordinario(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem junction, mesmo conteúdo, outro objeto: a identidade denuncia."""
    before = rig.capture()

    def replace_root() -> None:
        moved = rig.tmp / "worktree-original"
        rig.worktree.rename(moved)
        shutil.copytree(moved, rig.worktree)

    state = _after_anchor(monkeypatch, _same_dir(rig.worktree), replace_root)
    result = rig.verify(before)
    assert state["done"]
    assert (result.status, result.failure) == (S.INTEGRITY_VIOLATION, F.CHANGED_DURING_READ)


def test_focal_6_raizes_validas_continuam_verified(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raiz da worktree **tem** um arquivo `.git` — isso não a torna reparse."""
    assert (rig.worktree / ".git").is_file()
    anchored: list[str] = []
    real = pe._anchor_directory

    def counting(path: str, budget: VerificationBudget) -> ObjectIdentity:
        anchored.append(path)
        return real(path, budget)

    monkeypatch.setattr(pe, "_anchor_directory", counting)
    result = rig.run(lambda: write(rig.worktree / "app" / "a.py", "A = 2\n"))
    assert result.status is S.VERIFIED and paths(result) == {("app/a.py", K.MODIFIED)}
    assert any(_same_dir(rig.repo)(path) for path in anchored)
    assert any(_same_dir(rig.worktree)(path) for path in anchored)


def test_focal_ancora_nao_segue_junction_nem_aceita_nao_diretorio(tmp_path: Path) -> None:
    """MFINAL11: a âncora não pode ser a identidade do alvo de um link."""
    target = tmp_path / "alvo"
    target.mkdir()
    assert pe._anchor_directory(str(target), budget()) == _identity_of(os.lstat(target))
    link = tmp_path / "atalho"
    if not _link(target, link):
        pytest.skip("sem junction/symlink neste ambiente")
    with pytest.raises(pe._Failed) as info:
        pe._anchor_directory(str(link), budget())
    assert info.value.failure is F.LINK_OR_REPARSE
    regular = tmp_path / "arquivo"
    regular.write_bytes(b"x")
    with pytest.raises(pe._Failed) as info:
        pe._anchor_directory(str(regular), budget())
    assert info.value.failure is F.UNVERIFIABLE
    clock = Clock()
    meter = VerificationBudget(is_cancelled=never, clock=clock)
    clock.now = 61.0
    with pytest.raises(pe.BudgetExhausted) as exhausted:
        pe._anchor_directory(str(target), meter)
    assert exhausted.value.failure is F.DEADLINE_EXCEEDED


# ================================================================ contrato (PE15–PE20)


def test_pe15_snapshot_antes_nao_se_fabrica_depois(rig: Rig) -> None:
    state = main_tree_state(rig.local_path, budget())
    with pytest.raises(TypeError):
        MainTreeSnapshot(state)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        MainTreeSnapshot(state, _capture=object())
    with pytest.raises(TypeError):
        rig.verify(state)  # type: ignore[arg-type]
    before = rig.capture()
    with pytest.raises(AttributeError):
        before._state = None
    rig.verify(before)
    with pytest.raises(ValueError, match="já foi usado"):
        rig.verify(before)  # um "antes" vale uma verificação só


def test_cancelamento_e_obrigatorio(rig: Rig) -> None:
    with pytest.raises(TypeError):
        V.capture_main_tree(rig.local_path)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        V.capture_main_tree(rig.local_path, is_cancelled=None)  # type: ignore[arg-type]


def test_pe17_nenhum_caminho_absoluto_no_resultado(rig: Rig) -> None:
    write(rig.worktree / "other" / "x.txt", "fora\n")
    result = rig.run(lambda: write(rig.worktree / "app" / ".env", "S=1\n"))
    no_absolute(rig, result, *result.changes, *result.findings, rig.capture(), V, budget())
    assert all(not os.path.isabs(change.path) for change in result.changes)


def _state(root: Path) -> dict[str, bytes | None]:
    state: dict[str, bytes | None] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames:
            state[os.path.relpath(os.path.join(dirpath, name), root)] = None
        for name in filenames:
            full = os.path.join(dirpath, name)
            state[os.path.relpath(full, root)] = Path(full).read_bytes()
    return state


def test_pe18_verificacao_nao_escreve_nada(rig: Rig) -> None:
    write(rig.repo / "app" / "a.py", "sujo\n")
    write(rig.worktree / "app" / "a.py", "mudado\n")
    write(rig.worktree / "other" / "x.txt", "fora\n")
    repo_before, wt_before = _state(rig.repo), _state(rig.worktree)
    result = rig.verify(rig.capture())
    assert result.status is S.OUTSIDE_WORKSPACE_CHANGE
    assert _state(rig.repo) == repo_before  # inclui `.git/index` e `.git/worktrees/*`
    assert _state(rig.worktree) == wt_before


def test_pe19_git_runtime_e_a_unica_fonte_das_mudancas(rig: Rig) -> None:
    """M12: nenhum parâmetro aceita lista de arquivos vinda de provider/Test Runner."""
    for function in (V.verify, worktree_changes):
        parametros = set(inspect.signature(function).parameters)
        assert not parametros & {"files", "files_changed", "files_read", "changes", "paths"}
    result = rig.run(lambda: write(rig.worktree / "app" / "sorrateiro.py", "x\n"))
    assert ("app/sorrateiro.py", K.ADDED) in paths(result)


def test_pe20_verificador_nao_toca_banco_nem_safety_event() -> None:
    fonte = Path(ev.__file__).read_text(encoding="utf-8")
    assert "app.db" not in fonte and "app.orchestrator" not in fonte
    assert "record_safety_event" not in fonte
    assert repr(V) == "<PostExecutionVerifier>"


def test_resultado_tem_invariantes() -> None:
    with pytest.raises(ValueError):
        PostExecutionResult(status=S.VERIFIED, reason="x")
    with pytest.raises(ValueError):
        PostExecutionResult(status=S.VERIFIED, reason=None, failure=F.CANCELLED)
    with pytest.raises(ValueError):
        PostExecutionResult(status=S.OUT_OF_WORKTREE_WRITE, reason=None)
    with pytest.raises(TypeError):
        PostExecutionResult(status="verified", reason=None)  # type: ignore[arg-type]
    assert {status.value for status in S} == {
        "verified",
        "out_of_worktree_write",
        "outside_workspace_change",
        "path_policy_denied",
        "integrity_violation",
        "unverifiable",
    }
    assert {"limit_exceeded", "deadline_exceeded", "cancelled"} <= {f.value for f in F}
