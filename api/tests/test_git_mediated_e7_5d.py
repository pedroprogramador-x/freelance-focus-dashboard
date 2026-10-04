"""E7.5-D — `GitStatus`, `GitDiff`, `GitShow` e `GitListTree` pelo executor concreto.

Repositórios **reais** em diretório temporário (git local, sem rede). Fixtures adversariais usam
um *sentinel* benigno: um comando `touch <arquivo>` configurado como filtro, `textconv`,
`diff.external`, pager, alias, credential helper e hooks — e um **controle vivo** prova que o
mesmo comando dispara quando o Git porcelain é chamado diretamente. Falhas difíceis de produzir
de verdade (saída malformada, truncada, timeout) vêm de um `run_supervised` falso no módulo
mediado. Nenhum shell é usado pelo código testado.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.git_runtime import mediated
from app.git_runtime.mediated import GitFailure, MediatedGit, MediatedGitError
from app.path_runtime import bind_root
from app.process_runtime import ProcessOutcome, ProcessResult
from app.safety import TOOL_LIMITS_V1, EnforcementMode, SafetyPolicy, ToolLimits
from app.safety.capability_profile import DEVELOPER_V1_PROFILE, ProviderCapabilityProfile
from app.safety.redaction import redact as real_redact
from app.tool_executor import executor as executor_module
from app.tool_executor.contracts import (
    DecisionCategory,
    GitDiff,
    GitListTree,
    GitShow,
    GitStatus,
    ReadFile,
    RunScope,
    ToolResult,
    ToolStatus,
)
from app.tool_executor.executor import LocalWorktreeToolExecutor
from app.tool_executor.journal import InMemoryDecisionJournal
from app.tool_executor.workspace import ResolvedWorkspace
from tests.test_tool_file_ops_e7_5b import (
    CountdownToken,
    Token,
    denied,
    errored,
    limits,
    make_dir_link,
    ok,
)

exm: Any = executor_module
md: Any = mediated  # alvo de monkeypatch
GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git ausente")
TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"  # formato de token: fake
PEM_BODY = "MIIEowIBAAKCAQEA" + "q" * 60
PEM = f"-----BEGIN RSA PRIVATE KEY-----\n{PEM_BODY}\n{PEM_BODY}\n-----END RSA PRIVATE KEY-----\n"


# ------------------------------------------------------------------------------------ apoio


def git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    assert GIT is not None
    done = subprocess.run(  # noqa: S603 — fixture de teste; argv literal
        [GIT, *args], cwd=cwd, check=True, capture_output=True, env=env
    )
    return done.stdout.decode("utf-8", "replace").strip()


class GitRig:
    """Um repositório real + um executor ligado ao workspace (a raiz ou um subdiretório)."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        subdir: str | None = None,
        tool_limits: ToolLimits | None = None,
        token: Any = None,
        profile: ProviderCapabilityProfile = DEVELOPER_V1_PROFILE,
    ) -> None:
        self.tmp_path = tmp_path
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True)
        self.g("init", "-q", "-b", "main")
        for key, value in (
            ("core.autocrlf", "false"),
            ("user.name", "t"),
            ("user.email", "t@t"),
            ("commit.gpgsign", "false"),
        ):
            self.g("config", key, value)
        self.ws = self.repo / subdir if subdir else self.repo
        self.ws.mkdir(parents=True, exist_ok=True)
        self.prefix = f"{subdir}/" if subdir else ""
        self.limits = tool_limits if tool_limits is not None else TOOL_LIMITS_V1
        self.token = token if token is not None else Token()
        self.profile = profile
        self.journal = InMemoryDecisionJournal()
        self._executor: LocalWorktreeToolExecutor | None = None
        self.base = ""

    def g(self, *args: str) -> str:
        return git(self.repo, *args)

    def put(self, relative: str, data: bytes | str, *, root: bool = False) -> Path:
        path = (self.repo if root else self.ws) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def commit(self, message: str = "m") -> str:
        self.g("add", "-A")
        self.g("commit", "-q", "--allow-empty", "-m", message)
        return self.g("rev-parse", "HEAD")

    def start(self, base: str | None = None) -> LocalWorktreeToolExecutor:
        self.base = base or self.g("rev-parse", "HEAD")
        root = bind_root(self.ws)
        self._executor = LocalWorktreeToolExecutor(
            workspace=ResolvedWorkspace(
                workspace_id="wt-d",
                task_id="t1",
                run_id="r1",
                base_commit=self.base,
                workspace_path=str(self.ws),
                root_identity=root.identity,
                workspace_prefix=self.prefix,
            ),
            root=root,
            policy=SafetyPolicy(),
            profile=self.profile,
            run_scope=RunScope("t1", "r1", "i1"),
            cancel_token=self.token,
            journal=self.journal,
            limits=self.limits,
        )
        return self._executor

    @property
    def executor(self) -> LocalWorktreeToolExecutor:
        if self._executor is None:
            self.start()
        assert self._executor is not None
        return self._executor

    def run(self, request: Any) -> ToolResult:
        return self.executor.execute(request)

    def rules(self) -> list[str]:
        return [record.rule_id for record in self.journal.records()]

    def state(self) -> dict[str, bytes | None]:
        """Tudo do repositório — worktree **e** `.git` (índice, refs, HEAD, config, objetos)."""
        snapshot: dict[str, bytes | None] = {}
        for path in sorted(self.repo.rglob("*")):
            key = path.relative_to(self.repo).as_posix()
            snapshot[key] = None if path.is_dir() else path.read_bytes()
        return snapshot


@pytest.fixture
def rig(tmp_path: Path) -> GitRig:
    return GitRig(tmp_path)


def lines(content: str) -> list[str]:
    return content.split("\n")


def subcommand(argv: tuple[str, ...]) -> str:
    """O subcomando Git: o primeiro item depois de `-C <cwd>` que não é opção global."""
    return next(item for item in argv[argv.index("-C") + 2 :] if not item.startswith("-"))


class Calls:
    """Conta as chamadas a `run_supervised` do Git mediado (e os subcomandos)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.subcommands: list[str] = []
        self.argvs: list[tuple[str, ...]] = []
        real = md.run_supervised

        def spy(spec: Any, is_cancelled: Callable[[], bool]) -> ProcessResult:
            argv = spec.argv
            self.argvs.append(argv)
            self.subcommands.append(subcommand(argv))
            result: ProcessResult = real(spec, is_cancelled)
            return result

        monkeypatch.setattr(mediated, "run_supervised", spy)


def fake_result(
    stdout: bytes = b"",
    *,
    code: int = 0,
    outcome: ProcessOutcome = ProcessOutcome.EXITED,
    truncated: bool = False,
) -> ProcessResult:
    exited = outcome is ProcessOutcome.EXITED
    return ProcessResult(
        outcome=outcome,
        exit_code=code if exited else None,
        stdout=stdout,
        stderr=b"fatal: C:\\Users\\x\\segredo",
        stdout_truncated=truncated,
        stderr_truncated=False,
        output_complete=True,
        duration_ms=0,
        tree_confirmed_dead=True,
        orphans_killed=False,
        detail=None if outcome is not ProcessOutcome.SUPERVISION_FAILED else "x",
    )


def fake_git(
    monkeypatch: pytest.MonkeyPatch, answers: dict[str, ProcessResult]
) -> list[tuple[str, ...]]:
    """`run_supervised` falso: responde por subcomando; o resto vai ao Git real."""
    real = md.run_supervised
    seen: list[tuple[str, ...]] = []

    def fake(spec: Any, is_cancelled: Callable[[], bool]) -> ProcessResult:
        argv = spec.argv
        sub = subcommand(argv)
        seen.append(argv)
        tail = argv[argv.index(sub) :]
        key = sub if sub in answers else " ".join(tail[:2])
        if key in answers:
            return answers[key]
        result: ProcessResult = real(spec, is_cancelled)
        return result

    monkeypatch.setattr(mediated, "run_supervised", fake)
    return seen


def no_leak(result: ToolResult) -> None:
    text = repr(result)
    for raw in ("fatal", "segredo", "C:\\Users\\x", "Traceback", "Errno"):
        assert raw not in text


# ===================================================================== 1. capability e gate


def test_sem_git_read_nega_sem_iniciar_processo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = dataclasses.replace(DEVELOPER_V1_PROFILE, git_read=EnforcementMode.DISABLED)
    rig = GitRig(tmp_path, profile=profile)
    rig.put("a.txt", "a\n")
    rig.commit()
    calls = Calls(monkeypatch)
    for request in (GitStatus(), GitDiff(), GitShow("HEAD"), GitListTree()):
        denied(rig.run(request), "capability.denied")
    assert calls.subcommands == []
    assert rig.rules() == ["capability.denied"] * 4
    assert {r.category for r in rig.journal.records()} == {DecisionCategory.CAPABILITY_DENIED}


def test_cancelado_antes_nao_inicia_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = GitRig(tmp_path)
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.start()
    calls = Calls(monkeypatch)
    rig.token.cancelled = True
    errored(rig.run(GitStatus()), "operation_cancelled")
    assert calls.subcommands == []


def test_git_ausente_e_erro_tecnico(rig: GitRig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    monkeypatch.setattr(md.shutil, "which", lambda _name: None)
    for request in (GitStatus(), GitDiff(), GitShow("HEAD"), GitListTree()):
        errored(rig.run(request), "git_unavailable")
    assert rig.rules() == []


# ========================================================================= 2. revisões


def _history(rig: GitRig) -> dict[str, str]:
    """A → B → C em `main`; D numa branch paralela a partir de A; E depois de C (futuro)."""
    rig.put("f.txt", "A\n")
    a = rig.commit("A")
    rig.put("f.txt", "B\n")
    b = rig.commit("B")
    rig.g("tag", "tag-b", b)
    rig.put("f.txt", "C\n")
    c = rig.commit("C")
    rig.g("checkout", "-q", "-b", "lateral", a)
    rig.put("segredo-da-branch.txt", "D\n")
    d = rig.commit("D")
    rig.g("checkout", "-q", "main")
    rig.put("f.txt", "E\n")
    e = rig.commit("E")
    rig.g("checkout", "-q", c)  # HEAD destacado em C; `main` aponta para E
    return {"a": a, "b": b, "c": c, "d": d, "e": e}


def test_ancestralidade_aceita_base_e_ancestrais(rig: GitRig) -> None:
    h = _history(rig)
    rig.start(h["c"])
    for ref in (h["c"], h["b"], h["a"], "tag-b", "HEAD", "HEAD~2"):
        assert lines(ok(rig.run(GitShow(ref))))[0].startswith("commit\t")
    assert ok(rig.run(GitShow("tag-b", "f.txt"))) == "B\n"
    assert rig.rules() == []


@pytest.mark.parametrize("ref", ["lateral", "main", "d", "e"])
def test_ref_fora_do_historico_do_base_e_negada(rig: GitRig, ref: str) -> None:
    h = _history(rig)
    rig.start(h["c"])
    name = h[ref] if ref in ("d", "e") else ref
    for request in (GitShow(name), GitListTree(name), GitDiff(name), GitShow(name, "f.txt")):
        result = rig.run(request)
        denied(result, "git.ref_outside_base_history")
        assert name not in (result.reason or "")
    records = rig.journal.records()
    assert {r.category for r in records} == {DecisionCategory.CAPABILITY_DENIED}
    assert {r.subject_redacted for r in records} <= {h["d"], h["e"]}


def test_head_so_vale_se_estiver_no_historico(rig: GitRig) -> None:
    h = _history(rig)
    rig.g("checkout", "-q", "lateral")
    rig.start(h["c"])
    denied(rig.run(GitShow("HEAD")), "git.ref_outside_base_history")


def test_ref_inexistente_e_erro_sem_eco(rig: GitRig) -> None:
    _history(rig)
    result = rig.run(GitShow("nao-existe-xyz"))
    errored(result, "git_ref_not_found")
    assert "nao-existe-xyz" not in repr(result)
    assert rig.rules() == []


def test_ref_e_resolvida_uma_vez_e_nao_troca_no_meio(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _history(rig)
    rig.g("branch", "movel", h["b"])
    rig.start(h["c"])
    resolucoes: list[str] = []
    real_resolve = MediatedGit.resolve_commit
    real_tree = MediatedGit.list_tree

    def conta(self: MediatedGit, ref: str) -> str:
        resolucoes.append(ref)
        return real_resolve(self, ref)

    def move_e_lista(self: MediatedGit, commit: str) -> Any:
        rig.g("branch", "-f", "movel", h["a"])  # a ref anda **durante** o pedido
        return real_tree(self, commit)

    monkeypatch.setattr(MediatedGit, "resolve_commit", conta)
    monkeypatch.setattr(MediatedGit, "list_tree", move_e_lista)
    content = ok(rig.run(GitListTree("movel")))
    assert resolucoes == ["movel"]
    b_blob = rig.g("rev-parse", f"{h['b']}:f.txt")
    assert content == f"100644\t{b_blob}\tf.txt"


def test_base_commit_em_formato_do_repositorio(rig: GitRig) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.start("a" * 64)  # sha256 num repositório sha1
    errored(rig.run(GitListTree()), "git_object_unavailable")


# ============================================================================ 3. GitStatus


def test_status_limpo(rig: GitRig) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    assert ok(rig.run(GitStatus())) == "[clean]"


def test_status_tipos(rig: GitRig) -> None:
    rig.put("mod.txt", "1\n")
    rig.put("del.txt", "2\n")
    rig.put("stage.txt", "3\n")
    rig.put("rm-cached.txt", "4\n")
    rig.put("old-name.txt", "5\n")
    rig.commit()
    rig.put("mod.txt", "1 mudou\n")
    (rig.ws / "del.txt").unlink()
    rig.put("stage.txt", "3 staged\n")
    rig.g("add", "stage.txt")
    rig.g("rm", "-q", "--cached", "rm-cached.txt")
    rig.g("mv", "old-name.txt", "new-name.txt")
    rig.put("novo.txt", "n\n")
    assert lines(ok(rig.run(GitStatus()))) == [
        "deleted\tdel.txt",
        "modified\tmod.txt",
        "renamed\tnew-name.txt",
        "untracked\tnovo.txt",
        "renamed\told-name.txt",
        "deleted\trm-cached.txt",
        "untracked\trm-cached.txt",
        "staged\tstage.txt",
    ]


def test_status_usa_o_base_commit_e_nao_o_head(rig: GitRig) -> None:
    rig.put("a.txt", "a\n")
    base = rig.commit()
    rig.put("a.txt", "b\n")
    rig.commit()  # HEAD andou; o run continua ancorado no base
    rig.start(base)
    assert lines(ok(rig.run(GitStatus()))) == ["staged\ta.txt"]


def test_status_omite_segredo_registra_e_nao_le(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.tool_executor import git_ops

    rig.put("a.txt", "a\n")
    rig.put(".env", "K=v\n")
    rig.commit()
    rig.put(".env", "K=mudou\n")
    rig.put("config/.env.local", "x\n")
    lidos: list[str] = []
    real = git_ops._observe

    def espia(context: Any, gitx: Any, facts: Any, **kw: Any) -> Any:
        lidos.append(facts.requested_path)
        return real(context, gitx, facts, **kw)

    monkeypatch.setattr(git_ops, "_observe", espia)
    content = ok(rig.run(GitStatus()))
    assert content == "[clean]\n[2 paths omitted by policy]"
    assert ".env" not in lidos and "config/.env.local" not in lidos
    assert rig.rules() == ["path.secret_denied", "path.secret_denied"]
    assert rig.executor.usage().denials == 0


def test_status_em_subdiretorio_descarta_o_de_fora(tmp_path: Path) -> None:
    rig = GitRig(tmp_path, subdir="ws")
    rig.put("dentro.txt", "1\n")
    rig.put("fora.txt", "2\n", root=True)
    rig.commit()
    rig.put("dentro.txt", "1 mudou\n")
    rig.put("fora.txt", "2 mudou\n", root=True)
    rig.put("fora-novo.txt", "x\n", root=True)
    assert ok(rig.run(GitStatus())) == "modified\tdentro.txt"


def test_status_prefixo_divergente_do_binding_e_erro(tmp_path: Path) -> None:
    rig = GitRig(tmp_path, subdir="ws")
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.prefix = ""  # binding diz "raiz", o Git diz "ws/"
    errored(rig.run(GitStatus()), "git_output_unverifiable")


def test_status_repositorio_aninhado_nao_suportado(rig: GitRig) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    (rig.ws / "vendor").mkdir()
    git(rig.ws / "vendor", "init", "-q")
    rig.put("vendor/x.txt", "x\n")
    errored(rig.run(GitStatus()), "unsupported_git_tree_entry")


def test_status_e_deterministico(rig: GitRig) -> None:
    for name in ("z.txt", "B.txt", "a.txt", "é.txt"):
        rig.put(name, "1\n")
    rig.commit()
    for name in ("z.txt", "é.txt", "B.txt", "a.txt"):
        rig.put(name, "2\n")
    first = ok(rig.run(GitStatus()))
    assert first == ok(rig.run(GitStatus()))
    assert [line.split("\t")[1] for line in lines(first)] == sorted(
        ("z.txt", "B.txt", "a.txt", "é.txt"), key=str.encode
    )


# ========================================================================= 4. GitListTree


def test_list_tree_base_por_padrao_e_ordenado(rig: GitRig) -> None:
    rig.put("b.txt", "b\n")
    rig.put("dir/a.txt", "a\n")
    rig.put("ação.txt", "c\n")
    base = rig.commit()
    rig.put("depois.txt", "x\n")
    rig.commit()
    rig.start(base)
    content = ok(rig.run(GitListTree()))
    paths = [line.split("\t")[2] for line in lines(content)]
    assert paths == sorted(["b.txt", "dir/a.txt", "ação.txt"], key=str.encode)
    mode, oid, _ = lines(content)[0].split("\t")
    assert mode == "100644" and oid == rig.g("rev-parse", f"{base}:{paths[0]}")


def test_list_tree_ancestral_e_filtro_de_caminho(rig: GitRig) -> None:
    rig.put("src/a.py", "1\n")
    rig.put("src/sub/b.py", "2\n")
    rig.put("docs/x.md", "3\n")
    first = rig.commit()
    rig.put("src/c.py", "4\n")
    rig.commit()
    rig.start()
    assert [line.split("\t")[2] for line in lines(ok(rig.run(GitListTree(first, "src"))))] == [
        "src/a.py",
        "src/sub/b.py",
    ]
    assert lines(ok(rig.run(GitListTree(None, "src/a.py"))))[0].endswith("\tsrc/a.py")
    assert ok(rig.run(GitListTree(None, "nao-existe"))) == "[empty]"


def test_list_tree_caminho_negado_e_filhos_omitidos(rig: GitRig) -> None:
    rig.put("a.txt", "a\n")
    rig.put("cfg/.env", "K=v\n")
    rig.put("cfg/ok.txt", "x\n")
    rig.commit()
    denied(rig.run(GitListTree(None, ".env")), "path.secret_denied")
    denied(rig.run(GitListTree(None, ".git")), "path.dot_git_denied")
    content = ok(rig.run(GitListTree(None, "cfg")))
    assert lines(content) == [lines(content)[0], "[1 paths omitted by policy]"]
    assert ".env" not in content


def test_list_tree_subdiretorio(tmp_path: Path) -> None:
    rig = GitRig(tmp_path, subdir="pkg")
    rig.put("m.py", "1\n")
    rig.put("raiz.txt", "x\n", root=True)
    rig.commit()
    content = ok(rig.run(GitListTree()))
    assert [line.split("\t")[2] for line in lines(content)] == ["m.py"]


@pytest.mark.parametrize("mode", ["120000", "160000"])
def test_list_tree_symlink_e_gitlink_nao_suportados(rig: GitRig, mode: str) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    oid = rig.g("rev-parse", "HEAD" if mode == "160000" else "HEAD:a.txt")
    rig.g("update-index", "--add", "--cacheinfo", f"{mode},{oid},especial")
    rig.g("commit", "-q", "-m", "especial")
    rig.start()
    errored(rig.run(GitListTree()), "unsupported_git_tree_entry")
    errored(rig.run(GitListTree(None, "especial")), "unsupported_git_tree_entry")
    assert lines(ok(rig.run(GitListTree(None, "a.txt"))))[0].endswith("\ta.txt")


def test_list_tree_teto_final_nega_sem_cortar(tmp_path: Path) -> None:
    rig = GitRig(tmp_path, tool_limits=limits(tool_result_content_bytes=100))
    for i in range(10):
        rig.put(f"arquivo-{i:02}.txt", f"{i}\n")
    rig.commit()
    denied(rig.run(GitListTree()), "limit.tool_result_content_bytes")
    assert rig.journal.records()[-1].category is DecisionCategory.LIMIT_EXCEEDED


# ============================================================================= 5. GitShow


def test_show_blob_de_texto_conta_files_read(rig: GitRig) -> None:
    rig.put("src/a.py", "print('oi')\n")
    rig.commit()
    assert ok(rig.run(GitShow("HEAD", "src/a.py"))) == "print('oi')\n"
    assert rig.executor.usage().files_read == ("src/a.py",)


def test_show_historico_ausente_hoje(rig: GitRig) -> None:
    rig.put("velho.txt", "antigo\n")
    old = rig.commit()
    (rig.ws / "velho.txt").unlink()
    rig.commit()
    rig.start()
    assert ok(rig.run(GitShow(old, "velho.txt"))) == "antigo\n"


def test_show_erros_de_caminho(rig: GitRig) -> None:
    rig.put("dir/a.txt", "a\n")
    rig.put("bin.dat", b"\x00\x01\x02")
    rig.put("latin.txt", b"caf\xe9\n")
    rig.commit()
    errored(rig.run(GitShow("HEAD", "nada.txt")), "not_found")
    errored(rig.run(GitShow("HEAD", "dir")), "is_directory")
    errored(rig.run(GitShow("HEAD", "bin.dat")), "not_text")
    errored(rig.run(GitShow("HEAD", "latin.txt")), "not_text")
    assert rig.executor.usage().files_read == ()
    assert rig.rules() == []


def test_show_segredo_e_git_negados(rig: GitRig) -> None:
    rig.put(".env", "K=v\n")
    rig.commit()
    denied(rig.run(GitShow("HEAD", ".env")), "path.secret_denied")
    denied(rig.run(GitShow("HEAD", ".git/config")), "path.dot_git_denied")


def test_show_acima_do_limite_nega_sem_ler_o_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = GitRig(tmp_path, tool_limits=limits(git_show_path_bytes=10))
    rig.put("ok.txt", "0123456789")
    rig.put("grande.txt", "0123456789A")
    rig.commit()
    lidos: list[str] = []
    real = MediatedGit.read_blob

    def espia(self: MediatedGit, oid: str, size: int) -> bytes:
        lidos.append(oid)
        return real(self, oid, size)

    monkeypatch.setattr(MediatedGit, "read_blob", espia)
    assert ok(rig.run(GitShow("HEAD", "ok.txt"))) == "0123456789"
    denied(rig.run(GitShow("HEAD", "grande.txt")), "limit.git_show_path_bytes")
    assert len(lidos) == 1
    assert rig.executor.usage().files_read == ("ok.txt",)


def test_show_limite_v1_real(rig: GitRig) -> None:
    assert TOOL_LIMITS_V1.git_show_path_bytes == 256 * 1024
    rig.put("teto.txt", "x" * (256 * 1024 - 1) + "\n")
    rig.put("acima.txt", "x" * (256 * 1024) + "\n")
    rig.commit()
    assert len(ok(rig.run(GitShow("HEAD", "teto.txt")))) == 256 * 1024
    denied(rig.run(GitShow("HEAD", "acima.txt")), "limit.git_show_path_bytes")


def test_show_redige_segredo_multilinha_no_conteudo(rig: GitRig) -> None:
    rig.put("chave.txt", f"antes\n{PEM}token={TOKEN}\n")
    rig.commit()
    content = ok(rig.run(GitShow("HEAD", "chave.txt")))
    assert PEM_BODY not in content and TOKEN not in content and "antes" in content


def test_show_expansao_da_redacao_nega_e_nao_conta_files_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = GitRig(tmp_path, tool_limits=limits(tool_result_content_bytes=50))
    rig.put("a.txt", "PAD\n")
    rig.commit()
    monkeypatch.setattr(exm, "redact", lambda text: text.replace("PAD", "Z" * 100))
    denied(rig.run(GitShow("HEAD", "a.txt")), "limit.tool_result_content_bytes")
    assert rig.executor.usage().files_read == ()


def test_show_link_atual_no_caminho_nao_amplia_autoridade(rig: GitRig) -> None:
    rig.put("d/a.txt", "a\n")
    rig.commit()
    shutil.rmtree(rig.ws / "d")
    outside = rig.tmp_path / "fora"
    outside.mkdir()
    if not make_dir_link(rig.ws / "d", outside):
        pytest.skip("sem link de diretório")
    denied(rig.run(GitShow("HEAD", "d/a.txt")))


def test_show_commit_metadados_sem_patch(rig: GitRig) -> None:
    rig.put("a.txt", "a\n")
    first = rig.commit("primeiro")
    rig.put("a.txt", "b\n")
    second = rig.commit(f"segundo\n\ncorpo com {TOKEN}\n")
    content = ok(rig.run(GitShow(second)))
    assert lines(content)[:4] == [
        f"commit\t{second}",
        f"parent\t{first}",
        "author\t" + lines(content)[2].split("\t", 1)[1],
        "committer\t" + lines(content)[3].split("\t", 1)[1],
    ]
    assert lines(content)[2].startswith("author\tt <t@t> ")
    assert lines(content)[4] == "" and lines(content)[5] == "segundo"
    assert TOKEN not in content and "@@" not in content and "+b" not in content
    assert rig.executor.usage().files_read == ()


def test_show_commit_mensagem_grande_nega_no_teto(tmp_path: Path) -> None:
    rig = GitRig(tmp_path, tool_limits=limits(tool_result_content_bytes=1000))
    rig.put("a.txt", "a\n")
    sha = rig.commit("m" * 2000)
    denied(rig.run(GitShow(sha)), "limit.tool_result_content_bytes")


def test_show_commit_malformado(rig: GitRig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.start()
    fake_git(monkeypatch, {"cat-file commit": fake_result(b"lixo sem cabecalho")})
    result = rig.run(GitShow("HEAD"))
    errored(result, "git_output_unverifiable")
    no_leak(result)


# ============================================================================= 6. GitDiff


def test_diff_limpo(rig: GitRig) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    assert ok(rig.run(GitDiff())) == "[no differences]"
    assert rig.executor.usage().files_read == ()


def test_diff_modificado_criado_apagado_e_rename(rig: GitRig) -> None:
    rig.put("mod.txt", "1\n2\n3\n")
    rig.put("del.txt", "tchau\n")
    rig.put("velho.txt", "conteudo\n")
    rig.commit()
    rig.put("mod.txt", "1\nDOIS\n3\n")
    (rig.ws / "del.txt").unlink()
    rig.put("novo.txt", "oi\n")
    rig.g("mv", "velho.txt", "renomeado.txt")
    content = ok(rig.run(GitDiff()))
    assert content == "\n".join(
        [
            "--- a/del.txt",
            "+++ /dev/null",
            "@@ -1,1 +0,0 @@",
            "-tchau",
            "--- a/mod.txt",
            "+++ b/mod.txt",
            "@@ -1,3 +1,3 @@",
            " 1",
            "-2",
            "+DOIS",
            " 3",
            "--- /dev/null",
            "+++ b/novo.txt",
            "@@ -0,0 +1,1 @@",
            "+oi",
            "index\trenomeado.txt",  # `git mv`: o índice também diverge (D-AUD-003)
            "--- /dev/null",
            "+++ b/renomeado.txt",
            "@@ -0,0 +1,1 @@",
            "+conteudo",
            "index\tvelho.txt",
            "--- a/velho.txt",
            "+++ /dev/null",
            "@@ -1,1 +0,0 @@",
            "-conteudo",
        ]
    )
    assert rig.executor.usage().files_read == (
        "del.txt",
        "mod.txt",
        "novo.txt",
        "renomeado.txt",
        "velho.txt",
    )


def test_diff_filtro_de_caminho_e_sem_divergencia(rig: GitRig) -> None:
    rig.put("src/a.txt", "a\n")
    rig.put("b.txt", "b\n")
    rig.commit()
    rig.put("src/a.txt", "A\n")
    rig.put("b.txt", "B\n")
    content = ok(rig.run(GitDiff(None, "src")))
    assert content.startswith("--- a/src/a.txt") and "b.txt" not in content
    rig.put("b.txt", "b\n")
    assert ok(rig.run(GitDiff(None, "b.txt"))) == "[no differences]"
    assert ok(rig.run(GitDiff(None, "inexistente.txt"))) == "[no differences]"


def test_diff_binario_e_metadado_sem_files_read(rig: GitRig) -> None:
    rig.put("img.bin", b"\x00\x01")
    rig.put("t.txt", "a\n")
    rig.commit()
    rig.put("img.bin", b"\x00\x02")
    rig.put("t.txt", b"\xff\xfe")  # passou a não ser UTF-8
    assert ok(rig.run(GitDiff())) == "binary\timg.bin\nbinary\tt.txt"
    assert ok(rig.run(GitDiff(None, "img.bin"))) == "binary\timg.bin"
    assert rig.executor.usage().files_read == ()


def test_diff_crlf_e_sem_newline_final(rig: GitRig) -> None:
    rig.put("w.txt", b"a\r\nb\r\n")
    rig.put("n.txt", b"x\ny")
    rig.commit()
    rig.put("w.txt", b"a\r\nB\r\n")
    rig.put("n.txt", b"x\nz")
    assert lines(ok(rig.run(GitDiff()))) == [
        "--- a/n.txt",
        "+++ b/n.txt",
        "@@ -1,2 +1,2 @@",
        " x",
        "-y",
        "\\ No newline at end of file",
        "+z",
        "\\ No newline at end of file",
        "--- a/w.txt",
        "+++ b/w.txt",
        "@@ -1,2 +1,2 @@",
        " a\r",
        "-b\r",
        "+B\r",
    ]


def test_diff_subdiretorio(tmp_path: Path) -> None:
    rig = GitRig(tmp_path, subdir="ws")
    rig.put("a.txt", "1\n")
    rig.put("fora.txt", "x\n", root=True)
    rig.commit()
    rig.put("a.txt", "2\n")
    rig.put("fora.txt", "y\n", root=True)
    content = ok(rig.run(GitDiff()))
    assert content.startswith("--- a/a.txt") and "fora" not in content


def test_diff_segredo_descoberto_omitido_e_nao_lido(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.tool_executor import git_ops

    rig.put("a.txt", "a\n")
    rig.put(".env", "K=v\n")
    rig.commit()
    rig.put(".env", f"K={TOKEN}\n")
    rig.put("a.txt", "b\n")
    lidos: list[str] = []
    real = git_ops._observe

    def espia(context: Any, gitx: Any, facts: Any, **kw: Any) -> Any:
        lidos.append(facts.requested_path)
        return real(context, gitx, facts, **kw)

    monkeypatch.setattr(git_ops, "_observe", espia)
    content = ok(rig.run(GitDiff()))
    assert ".env" not in content and TOKEN not in content
    assert content.endswith("[1 paths omitted by policy]")
    assert lidos == ["a.txt"]
    assert rig.rules() == ["path.secret_denied"]
    denied(rig.run(GitDiff(None, ".env")), "path.secret_denied")


def test_diff_contra_ancestral_e_nao_ancestral(rig: GitRig) -> None:
    h = _history(rig)
    rig.g("checkout", "-q", "-f", h["c"])
    rig.start(h["c"])
    content = ok(rig.run(GitDiff(h["a"])))
    assert "-A" in lines(content) and "+C" in lines(content)
    denied(rig.run(GitDiff(h["d"])), "git.ref_outside_base_history")


def test_diff_indice_divergente_nao_some(rig: GitRig) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.put("a.txt", "staged\n")
    rig.g("add", "a.txt")
    rig.put("a.txt", "a\n")  # árvore igual ao commit, índice não
    assert ok(rig.run(GitDiff())) == "index\ta.txt"


def test_diff_teto_final_nega_sem_files_read(tmp_path: Path) -> None:
    rig = GitRig(tmp_path, tool_limits=limits(tool_result_content_bytes=200))
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.put("a.txt", "".join(f"linha {i}\n" for i in range(100)))
    denied(rig.run(GitDiff()), "limit.tool_result_content_bytes")
    assert rig.executor.usage().files_read == ()


def test_diff_redacao_multilinha_atravessa_linhas_do_diff(rig: GitRig) -> None:
    rig.put("a.txt", "inicio\n")
    rig.commit()
    rig.put("a.txt", f"inicio\n{PEM}fim\n")
    content = ok(rig.run(GitDiff()))
    assert PEM_BODY not in content and "+inicio" not in content and "+fim" in content


def test_diff_grande_vira_marcador(tmp_path: Path) -> None:
    rig = GitRig(tmp_path, tool_limits=limits(git_internal_capture_bytes=4096))
    rig.put("g.txt", "x" * 3000 + "\n")
    rig.commit()
    rig.put("g.txt", "y" * 5000 + "\n")
    assert ok(rig.run(GitDiff())) == "large\tg.txt"
    assert rig.executor.usage().files_read == ()


# ===================================================================== 7. redação e saída


@pytest.mark.parametrize(
    "request_",
    [GitStatus(), GitDiff(), GitShow("HEAD"), GitShow("HEAD", "a.txt"), GitListTree()],
    ids=lambda r: f"{type(r).__name__}-{getattr(r, 'path', None)}",
)
def test_redacao_central_uma_vez_sobre_a_saida_completa(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch, request_: Any
) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.put("a.txt", "b\n")
    chamadas: list[str] = []

    def conta(text: str) -> str:
        chamadas.append(text)
        return str(real_redact(text))

    monkeypatch.setattr(exm, "redact", conta)
    result = rig.run(request_)
    assert result.status is ToolStatus.OK
    assert len(chamadas) == 1 and chamadas[0] == result.content


# ======================================================= 8. config adversarial e somente-leitura


def _armed_repo(rig: GitRig, *, staged: bool = False) -> Path:
    """Repositório com tudo que poderia executar um programa externo apontando para um
    `touch` de sentinel. Devolve o caminho do sentinel. ``staged``: um arquivo novo já no
    índice (adicionado **antes** de armar — depois, o `git add` dispararia o filtro)."""
    sentinel = rig.tmp_path / "sentinel"
    cmd = f"touch '{sentinel.as_posix()}'"
    rig.put("a.txt", "a\n")
    rig.put("b.bin", b"\x00b")
    rig.commit()
    if staged:
        rig.put("staged.txt", "s\n")
        rig.g("add", "staged.txt")
    rig.put(".gitattributes", "* filter=evil diff=evil\n")
    for key, value in (
        ("filter.evil.clean", cmd),
        ("filter.evil.smudge", cmd),
        ("filter.evil.process", cmd),
        ("filter.evil.required", "false"),
        ("diff.evil.textconv", cmd),
        ("diff.external", cmd),
        ("core.fsmonitor", cmd),
        ("core.pager", cmd),
        ("pager.diff", cmd),
        ("credential.helper", f"!{cmd}"),
        ("alias.st", f"!{cmd}"),
        ("protocol.allow", "always"),
    ):
        rig.g("config", key, value)
    hooks = rig.repo / ".git" / "hooks"
    for hook in ("post-index-change", "post-checkout", "reference-transaction", "pre-auto-gc"):
        (hooks / hook).write_text(f"#!/bin/sh\n{cmd}\n", encoding="utf-8")
        (hooks / hook).chmod(0o755)
    rig.put("a.txt", "a mudou\n")
    rig.put("novo.txt", "n\n")
    return sentinel


def test_config_adversarial_nada_externo_executa(rig: GitRig) -> None:
    sentinel = _armed_repo(rig)
    # Controle vivo: o Git porcelain chamado direto **dispara** o sentinel (e pode até
    # falhar depois, já que o "filtro" não fala o protocolo — o que importa é ter rodado).
    subprocess.run([str(GIT), "diff"], cwd=rig.repo, check=False, capture_output=True)  # noqa: S603
    assert sentinel.exists(), "a fixture não armou nada"
    sentinel.unlink()
    rig.start()
    for request in (
        GitStatus(),
        GitDiff(),
        GitDiff(None, "a.txt"),
        GitShow("HEAD"),
        GitShow("HEAD", "a.txt"),
        GitListTree(),
    ):
        assert rig.run(request).status is ToolStatus.OK, request
    assert not sentinel.exists()


def test_quatro_operacoes_nao_alteram_repositorio(rig: GitRig) -> None:
    _armed_repo(rig, staged=True)
    os.utime(rig.ws / "a.txt", (1, 1))  # stat sujo: um `status` refrescaria o índice
    before = rig.state()
    rig.start()
    for request in (
        GitStatus(),
        GitDiff(),
        GitShow("HEAD"),
        GitShow("HEAD", "a.txt"),
        GitListTree(),
    ):
        rig.run(request)
    # também em negação, erro e cancelamento
    rig.run(GitShow("HEAD", ".env"))
    rig.run(GitShow("nao-existe"))
    rig.token.cancelled = True
    rig.run(GitStatus())
    assert rig.state() == before


def test_argv_tem_opcoes_fixas_e_nenhum_caminho_do_provider(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("segredo-de-nome.txt", "a\n")
    rig.commit()
    calls = Calls(monkeypatch)
    for request in (
        GitStatus(),
        GitDiff(None, "segredo-de-nome.txt"),
        GitShow("HEAD", "segredo-de-nome.txt"),
        GitListTree(None, "segredo-de-nome.txt"),
    ):
        ok(rig.run(request))
    assert set(calls.subcommands) <= {
        "rev-parse",
        "merge-base",
        "ls-tree",
        "ls-files",
        "cat-file",
        "diff-index",
    }
    for argv in calls.argvs:
        assert not any("segredo-de-nome" in item for item in argv)
        assert "--no-lazy-fetch" in argv and "protocol.allow=never" in argv
        assert not {"-p", "--patch", "status", "diff", "show"} & set(argv)
        if "diff-index" in argv:  # só árvore × índice, só estrutura
            assert {"--cached", "--raw", "--no-ext-diff", "--no-textconv"} <= set(argv)


# =========================================================================== 9. rede


def test_objeto_ausente_num_partial_clone_nao_busca(tmp_path: Path) -> None:
    rig = GitRig(tmp_path)
    rig.put("big.txt", "conteudo remoto\n")
    rig.commit()
    oid = rig.g("rev-parse", "HEAD:big.txt")
    origin = tmp_path / "origin"
    shutil.copytree(rig.repo, origin)
    for key, value in (
        ("core.repositoryformatversion", "1"),
        ("extensions.partialClone", "origin"),
        ("remote.origin.url", origin.as_posix()),
        ("remote.origin.promisor", "true"),
    ):
        rig.g("config", key, value)
    loose = rig.repo / ".git" / "objects" / oid[:2] / oid[2:]
    loose.chmod(0o666)  # objeto solto é somente-leitura (Windows recusa o unlink)
    loose.unlink()
    # Controle vivo: sem as travas, o Git busca o objeto no "remoto" (numa cópia).
    control = tmp_path / "control"
    shutil.copytree(rig.repo, control)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    assert git(control, "cat-file", "blob", oid, env=env) == "conteudo remoto"
    rig.start()
    errored(rig.run(GitShow("HEAD", "big.txt")), "git_object_unavailable")
    missing = subprocess.run(  # noqa: S603
        [str(GIT), "--no-lazy-fetch", "cat-file", "-e", oid], cwd=rig.repo, check=False
    )
    assert missing.returncode != 0  # nada foi buscado


# ================================================ 10. falhas técnicas, truncamento, cancelamento


@pytest.mark.parametrize(
    ("request_", "sub"),
    [
        (GitStatus(), "ls-files"),
        (GitStatus(), "ls-tree"),
        (GitListTree(), "ls-tree"),
        (GitDiff(), "ls-files"),
        (GitShow("HEAD"), "cat-file"),
        (GitShow("HEAD", "a.txt"), "cat-file"),
        (GitShow("HEAD~0"), "rev-parse"),
    ],
    ids=lambda v: v if isinstance(v, str) else type(v).__name__,
)
def test_saida_estrutural_truncada_nunca_e_parseada(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch, request_: Any, sub: str
) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.start()
    fake_git(monkeypatch, {sub: fake_result(b"100644 blob ", truncated=True)})
    result = rig.run(request_)
    errored(result, "git_output_unverifiable")
    no_leak(result)
    assert rig.rules() == []


def test_captura_interna_real_acima_do_teto(tmp_path: Path) -> None:
    rig = GitRig(tmp_path, tool_limits=limits(git_internal_capture_bytes=200))
    for i in range(20):
        rig.put(f"f{i:02}.txt", "x\n")
    rig.commit()
    errored(rig.run(GitListTree()), "git_output_unverifiable")
    errored(rig.run(GitStatus()), "git_output_unverifiable")


@pytest.mark.parametrize(
    "stdout",
    [
        b"100644 blob abc\tx\0",
        b"100644 tree " + b"a" * 40 + b" -\tx\0",
        b"100644 blob " + b"a" * 40 + b" 1\tx",  # sem NUL final
        b"999999 blob " + b"a" * 40 + b" 1\tx\0",
        b"100644 blob " + b"a" * 40 + b" 1\0",
    ],
)
def test_ls_tree_malformado(rig: GitRig, monkeypatch: pytest.MonkeyPatch, stdout: bytes) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.start()
    fake_git(monkeypatch, {"ls-tree": fake_result(stdout)})
    errored(rig.run(GitListTree()), "git_output_unverifiable")


@pytest.mark.parametrize(
    "stdout",
    [
        b"100644 " + b"a" * 40 + b" 0\tx",  # sem NUL final
        b"100644 " + b"a" * 40 + b" 7\tx\0",  # estágio inválido
        b"100644 " + b"z" * 40 + b" 0\tx\0",  # oid inválido
        b"777777 " + b"a" * 40 + b" 0\tx\0",  # modo desconhecido
        b"100644 " + b"a" * 40 + b"\tx\0",  # campos a menos
    ],
)
def test_ls_files_malformado(rig: GitRig, monkeypatch: pytest.MonkeyPatch, stdout: bytes) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.start()
    fake_git(monkeypatch, {"ls-files --stage": fake_result(stdout)})
    errored(rig.run(GitStatus()), "git_output_unverifiable")
    errored(rig.run(GitDiff()), "git_output_unverifiable")


@pytest.mark.parametrize(
    "raw_path", [b"caf\xe9.txt", b"dir\\nome.txt"], ids=["nao-utf8", "barra-invertida"]
)
def test_caminho_nao_representavel_no_escopo_e_erro(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch, raw_path: bytes
) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.start()
    oid = rig.g("rev-parse", "HEAD:a.txt").encode()
    tree = (
        b"100644 blob " + oid + b" 2\ta.txt\0" + b"100644 blob " + oid + b" 2\t" + raw_path + b"\0"
    )
    fake_git(monkeypatch, {"ls-tree": fake_result(tree)})
    errored(rig.run(GitListTree()), "git_output_unverifiable")
    errored(rig.run(GitDiff()), "git_output_unverifiable")
    errored(rig.run(GitStatus()), "git_output_unverifiable")
    # fora do escopo pedido, o caminho ilegível não contamina a resposta
    assert lines(ok(rig.run(GitListTree(None, "a.txt"))))[0].endswith("\ta.txt")
    assert ok(rig.run(GitShow("HEAD", "a.txt"))) == "a\n"


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (ProcessOutcome.TIMEOUT, "git_timeout"),
        (ProcessOutcome.SUPERVISION_FAILED, "git_failed"),
        (ProcessOutcome.CANCELLED, "operation_cancelled"),
    ],
)
def test_desfechos_do_supervisor(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch, outcome: ProcessOutcome, code: str
) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.start()
    fake_git(monkeypatch, {"ls-tree": fake_result(outcome=outcome)})
    result = rig.run(GitListTree())
    errored(result, code)
    no_leak(result)
    assert rig.rules() == []


def test_timeout_real_encerra_o_git(rig: GitRig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.start()
    monkeypatch.setattr(mediated, "TIMEOUT_S", 0.001)
    errored(rig.run(GitStatus()), "git_timeout")


def test_exit_code_inesperado_e_erro_tecnico(rig: GitRig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.start()
    fake_git(monkeypatch, {"merge-base": fake_result(code=128)})
    errored(rig.run(GitShow("HEAD")), "git_object_unavailable")
    assert rig.rules() == []


def test_varredura_de_cancelamento_sem_efeito(tmp_path: Path) -> None:
    vistos = {"cancelado": 0}
    for after in range(60):
        rig = GitRig(tmp_path / str(after))
        rig.put("a.txt", "a\n")
        rig.commit()
        rig.put("a.txt", "b\n")
        before = rig.state()
        rig.token = CountdownToken(after)
        rig.start()
        result = rig.run(GitDiff())
        assert rig.state() == before
        if result.status is ToolStatus.OK:
            break
        errored(result, "operation_cancelled")
        vistos["cancelado"] += 1
        errored(rig.run(GitStatus()), "operation_cancelled")  # sticky
    else:
        pytest.fail("a varredura não alcançou o fim")
    assert vistos["cancelado"] > 5


def test_journal_indisponivel_na_omissao_envenena(rig: GitRig) -> None:
    class Quebrado(InMemoryDecisionJournal):
        def append(self, record: Any) -> None:
            raise RuntimeError("fora do ar")

    rig.put(".env", "K=v\n")
    rig.put("a.txt", "a\n")
    rig.commit()
    rig.journal = Quebrado()
    rig.start()
    errored(rig.run(GitListTree()), "journal_unavailable")
    errored(rig.run(ReadFile("a.txt")), "journal_unavailable")


# ======================================================================= 11. MediatedGit


def test_mediated_git_nao_roda_nada_no_construtor(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(mediated, "run_supervised", lambda *a: calls.append(a))
    gitx = MediatedGit(
        "C:\\x" if sys.platform == "win32" else "/x",
        expected_prefix="",
        is_cancelled=lambda: False,
        capture_bytes=10,
    )
    assert calls == [] and repr(gitx) == "<MediatedGit>"


def test_read_blob_confere_oid_recalculado(rig: GitRig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.put("a.txt", "a\n")
    rig.commit()
    oid = rig.g("rev-parse", "HEAD:a.txt")
    gitx = MediatedGit(
        str(rig.ws), expected_prefix="", is_cancelled=lambda: False, capture_bytes=1 << 20
    )
    assert gitx.read_blob(oid, 2) == b"a\n"
    fake_git(monkeypatch, {"cat-file": fake_result(b"b\n")})
    with pytest.raises(MediatedGitError) as caught:
        gitx.read_blob(oid, 2)
    assert caught.value.failure is GitFailure.OUTPUT_UNVERIFIABLE


@pytest.mark.parametrize("primitive", ["read_fd", "digest_fd"])
def test_falha_de_leitura_atual_nunca_vira_ausencia(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch, primitive: str
) -> None:
    from app.path_runtime import PathFailure, PathOperationFailed
    from app.tool_executor import git_ops

    rig.put("a.txt", "a\n")
    rig.commit()
    rig.put("a.txt", "b\n")

    def falha(*_a: Any, **_k: Any) -> Any:
        raise PathOperationFailed(PathFailure.IO_ERROR)

    monkeypatch.setattr(git_ops, primitive, falha)
    request = GitDiff() if primitive == "read_fd" else GitStatus()
    result = rig.run(request)
    errored(result, "io_error")  # nunca "deleted" / diff para /dev/null
    assert rig.rules() == []


@pytest.mark.parametrize("sub", ["ls-tree", "ls-files --stage"])
def test_prefixo_truncado_bem_formado_nunca_e_lido_como_completo(
    rig: GitRig, monkeypatch: pytest.MonkeyPatch, sub: str
) -> None:
    """O caso perigoso: a captura parou exatamente numa fronteira de registro. O prefixo é
    sintaticamente perfeito; só a flag de truncamento diz que ele não é a resposta inteira."""
    rig.put("a.txt", "a\n")
    rig.put("b.txt", "b\n")
    rig.commit()
    rig.start()
    oid = rig.g("rev-parse", "HEAD:a.txt").encode()
    prefix = (
        b"100644 blob " + oid + b" 2\ta.txt\0"
        if sub == "ls-tree"
        else b"100644 " + oid + b" 0\ta.txt\0"
    )
    fake_git(monkeypatch, {sub: fake_result(prefix, truncated=True)})
    request = GitListTree() if sub == "ls-tree" else GitStatus()
    errored(rig.run(request), "git_output_unverifiable")
