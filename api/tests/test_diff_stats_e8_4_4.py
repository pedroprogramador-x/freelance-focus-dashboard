"""E8.4.4 — métricas do diff da worktree (`git_runtime.diff_stats` + `measure_diff`).

`git` de verdade (a mesma montagem da E8.3: repositório com `app/` e `other/`, worktree da task pela
E7.4). As métricas são **todas ou nenhuma**; qualquer inconsistência, limite, binário ou conteúdo
diferente do verificado devolve uma `DiffMeasureFailure` — nunca um valor parcial.
"""

from __future__ import annotations

import os
import random
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.execution_verification import (
    PostExecutionResult,
    PostExecutionStatus,
    PostExecutionVerifier,
)
from app.git_runtime import diff_stats as ds
from app.git_runtime.diff_stats import (
    DiffMeasureFailure,
    DiffMetrics,
    count_changes,
    split_lines,
)
from app.process_runtime import run_supervised
from tests.test_git_worktree_e7_4 import git
from tests.test_post_execution_verification_e8_3 import Rig, make_rig, never, write

pytestmark = pytest.mark.skipif(
    __import__("shutil").which("git") is None, reason="git indisponível no PATH"
)

V = PostExecutionVerifier()
F = DiffMeasureFailure


def run_and_measure(
    rig: Rig,
    effect: Callable[[], object],
    *,
    verifier: PostExecutionVerifier = V,
    is_cancelled: Callable[[], bool] = never,
    between: Callable[[], object] | None = None,
) -> DiffMetrics | DiffMeasureFailure:
    before = rig.capture()
    effect()
    result = rig.verify(before)
    assert result.status is PostExecutionStatus.VERIFIED, result
    if between is not None:
        between()
    return verifier.measure_diff(
        result,
        workspace_path=rig.workspace_path,
        workspace_prefix=rig.prefix,
        base_commit=rig.base,
        is_cancelled=is_cancelled,
    )


def three_new_files(rig: Rig) -> None:
    for index in range(3):
        write(inside(rig, f"n{index}.txt"), "x\n")


def inside(rig: Rig, relative: str) -> Path:
    return Path(rig.workspace_path) / relative


# ----------------------------------------------------------------------- contagem pura


def test_split_lines_preserva_terminadores_e_a_ultima_sem_terminador() -> None:
    assert split_lines(b"") == []
    assert split_lines(b"a") == [b"a"]
    assert split_lines(b"a\n") == [b"a\n"]
    assert split_lines(b"a\nb") == [b"a\n", b"b"]
    assert split_lines(b"\n\n") == [b"\n", b"\n"]
    assert split_lines(b"a\r\nb\r\n") == [b"a\r\n", b"b\r\n"]


@pytest.mark.parametrize(
    ("base", "current", "expected"),
    [
        (b"", b"a\nb\n", (2, 0)),
        (b"a\nb\n", b"", (0, 2)),
        (b"a\n", b"a\n", (0, 0)),
        (b"a\nb\nc\n", b"a\nX\nc\n", (1, 1)),
        (b"a", b"a\n", (1, 1)),  # falta de terminador conta como mudança de linha
        (b"a\nb\n", b"a\nb\nc\nd\n", (2, 0)),
        (b"a\r\n", b"a\n", (1, 1)),
    ],
)
def test_count_changes_casos(base: bytes, current: bytes, expected: tuple[int, int]) -> None:
    assert count_changes(base, current) == expected


def test_count_changes_e_consistente_com_o_tamanho_das_linhas() -> None:
    rng = random.Random(844)  # noqa: S311 — semente fixa de teste
    alphabet = [b"a\n", b"b\n", b"c\n", b"d\n", b"\n"]
    for _ in range(200):
        before = b"".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))
        after = b"".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))
        added, removed = count_changes(before, after)
        assert added - removed == len(split_lines(after)) - len(split_lines(before))
        assert added >= 0 and removed >= 0
        assert count_changes(before, after) == (added, removed)  # determinística


# ------------------------------------------------------------------ medição verificada


def test_sem_mudanca_mede_zero(rig_factory: Callable[..., Rig]) -> None:
    rig = rig_factory()
    assert run_and_measure(rig, lambda: None) == DiffMetrics((), 0, 0)


def test_arquivo_modificado_adicionado_e_removido(rig_factory: Callable[..., Rig]) -> None:
    rig = rig_factory()

    def effect() -> None:
        write(inside(rig, "a.py"), "A = 2\nB = 3\n")  # base: "A = 1\n"  → +2 -1
        write(inside(rig, "novo.py"), "x\ny\nz\n")  # +3
        (inside(rig, "keep.txt")).unlink()  # base "keep\n" → -1

    out = run_and_measure(rig, effect)
    assert out == DiffMetrics(("app/a.py", "app/keep.txt", "app/novo.py"), 5, 2)


def test_diretorio_novo_nao_e_arquivo_mudado(rig_factory: Callable[..., Rig]) -> None:
    rig = rig_factory()

    def effect() -> None:
        (inside(rig, "pasta_nova")).mkdir()
        write(inside(rig, "pasta_nova/f.txt"), "1\n")

    out = run_and_measure(rig, effect)
    assert out == DiffMetrics(("app/pasta_nova/f.txt",), 1, 0)


def test_mudanca_staged_entra_uma_vez(rig_factory: Callable[..., Rig]) -> None:
    rig = rig_factory()

    def effect() -> None:
        write(inside(rig, "a.py"), "A = 9\n")
        git(rig.worktree, "add", "app/a.py")

    out = run_and_measure(rig, effect)
    assert out == DiffMetrics(("app/a.py",), 1, 1)


def test_indice_divergente_sem_mudanca_de_conteudo_conta_o_arquivo_com_zero_linhas(
    rig_factory: Callable[..., Rig],
) -> None:
    rig = rig_factory()

    def effect() -> None:
        write(inside(rig, "a.py"), "A = 9\n")
        git(rig.worktree, "add", "app/a.py")
        write(inside(rig, "a.py"), "A = 1\n")  # volta ao conteúdo do base; o índice segue diferente

    out = run_and_measure(rig, effect)
    assert out == DiffMetrics(("app/a.py",), 0, 0)


@pytest.mark.skipif(sys.platform == "win32", reason="bit de execução é do POSIX")
def test_mudanca_so_de_modo_conta_o_arquivo_com_zero_linhas(
    rig_factory: Callable[..., Rig],
) -> None:
    rig = rig_factory()
    path = inside(rig, "a.py")
    out = run_and_measure(rig, lambda: path.chmod(path.stat().st_mode | stat.S_IXUSR))
    assert out == DiffMetrics(("app/a.py",), 0, 0)


def test_resultado_e_determinístico_e_sem_caminho_absoluto(rig_factory: Callable[..., Rig]) -> None:
    rig = rig_factory()
    out = run_and_measure(rig, lambda: write(inside(rig, "a.py"), "A = 2\n"))
    assert isinstance(out, DiffMetrics)
    for text in (repr(out), str(out)):
        assert str(rig.tmp).lower() not in text.lower()


# --------------------------------------------------------- tudo ou nada: o que não se mede


def test_binario_em_qualquer_lado_zera_tudo(rig_factory: Callable[..., Rig]) -> None:
    rig = rig_factory()

    def effect() -> None:
        write(inside(rig, "a.py"), "A = 2\n")  # mensurável
        inside(rig, "bin.dat").write_bytes(b"\x00\x01\x02")  # binário

    assert run_and_measure(rig, effect) is F.BINARY


def test_binario_no_base_tambem(rig_factory: Callable[..., Rig]) -> None:
    rig = rig_factory(binary_base=True)
    assert run_and_measure(rig, lambda: write(inside(rig, "blob.dat"), "texto agora\n")) is F.BINARY


def test_arquivo_acima_do_limite_zera_tudo(rig_factory: Callable[..., Rig]) -> None:
    rig = rig_factory()
    big = "x" * (ds.MAX_FILE_BYTES + 1)  # uma linha só: o teto de bytes é o único que vale
    assert run_and_measure(rig, lambda: write(inside(rig, "big.txt"), big)) is F.TOO_LARGE


def test_linhas_acima_do_limite_zeram_tudo(
    rig_factory: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = rig_factory()
    monkeypatch.setattr(ds, "MAX_LINES", 3)
    out = run_and_measure(rig, lambda: write(inside(rig, "n.txt"), "1\n2\n3\n4\n"))
    assert out is F.TOO_LARGE


def test_volume_total_acima_do_limite_zera_tudo(
    rig_factory: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = rig_factory()
    monkeypatch.setattr(ds, "MAX_TOTAL_BYTES", 10)

    def effect() -> None:
        write(inside(rig, "a.py"), "1\n" * 4)
        write(inside(rig, "b.py"), "2\n" * 4)

    out = run_and_measure(rig, effect)
    assert out is F.TOO_LARGE


def test_custo_de_comparacao_acima_do_limite_zera_tudo(
    rig_factory: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = rig_factory()
    monkeypatch.setattr(ds, "MAX_DIFF_OPS", 1)
    out = run_and_measure(rig, lambda: write(inside(rig, "a.py"), "A = 2\nB = 3\n"))
    assert out is F.TOO_LARGE


def test_caminhos_demais_zeram_tudo(
    rig_factory: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = rig_factory()
    monkeypatch.setattr(ds, "MAX_PATHS", 2)
    out = run_and_measure(rig, lambda: three_new_files(rig))
    assert out is F.TOO_MANY_PATHS


def test_o_limite_exato_ainda_mede(
    rig_factory: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = rig_factory()
    monkeypatch.setattr(ds, "MAX_PATHS", 3)
    out = run_and_measure(rig, lambda: three_new_files(rig))
    assert isinstance(out, DiffMetrics) and out.diff_added == 3


# ------------------------------------------------- conteúdo diferente do verificado (TOCTOU)


def test_conteudo_trocado_depois_da_verificacao_nao_e_medido(
    rig_factory: Callable[..., Rig],
) -> None:
    rig = rig_factory()
    out = run_and_measure(
        rig,
        lambda: write(inside(rig, "a.py"), "A = 2\n"),
        between=lambda: write(inside(rig, "a.py"), "A = 3\nB = 4\n"),
    )
    assert out is F.CHANGED_SINCE_VERIFIED


def test_arquivo_adicionado_trocado_nao_e_medido(rig_factory: Callable[..., Rig]) -> None:
    rig = rig_factory()
    out = run_and_measure(
        rig,
        lambda: write(inside(rig, "n.txt"), "um\n"),
        between=lambda: write(inside(rig, "n.txt"), "dois\ntres\n"),
    )
    assert out is F.CHANGED_SINCE_VERIFIED


def test_arquivo_removido_depois_da_verificacao_nao_e_medido(
    rig_factory: Callable[..., Rig],
) -> None:
    rig = rig_factory()
    out = run_and_measure(
        rig,
        lambda: write(inside(rig, "a.py"), "A = 2\n"),
        between=lambda: inside(rig, "a.py").unlink(),
    )
    assert out is F.CHANGED_SINCE_VERIFIED


def test_arquivo_apagado_que_reaparece_nao_e_medido(rig_factory: Callable[..., Rig]) -> None:
    rig = rig_factory()
    out = run_and_measure(
        rig,
        lambda: inside(rig, "keep.txt").unlink(),
        between=lambda: write(inside(rig, "keep.txt"), "voltou\n"),
    )
    assert out is F.CHANGED_SINCE_VERIFIED


def test_arquivo_trocado_por_diretorio_nao_e_medido(rig_factory: Callable[..., Rig]) -> None:
    rig = rig_factory()

    def swap() -> None:
        inside(rig, "n.txt").unlink()
        inside(rig, "n.txt").mkdir()

    out = run_and_measure(rig, lambda: write(inside(rig, "n.txt"), "x\n"), between=swap)
    assert isinstance(out, DiffMeasureFailure) and out is not F.BINARY


def test_head_movido_depois_da_verificacao_nao_e_medido(rig_factory: Callable[..., Rig]) -> None:
    rig = rig_factory()

    def move_head() -> None:
        git(rig.worktree, "commit", "-q", "--allow-empty", "-m", "outro")

    out = run_and_measure(rig, lambda: write(inside(rig, "a.py"), "A = 2\n"), between=move_head)
    assert out is F.CHANGED_SINCE_VERIFIED


# ----------------------------------------------------------- cancelamento, prazo, uso


def test_cancelamento_durante_a_medicao(rig_factory: Callable[..., Rig]) -> None:
    rig = rig_factory()
    before = rig.capture()
    write(inside(rig, "a.py"), "A = 2\n")
    result = rig.verify(before)
    out = V.measure_diff(
        result,
        workspace_path=rig.workspace_path,
        workspace_prefix=rig.prefix,
        base_commit=rig.base,
        is_cancelled=lambda: True,
    )
    assert out is F.CANCELLED


def test_prazo_durante_a_medicao(rig_factory: Callable[..., Rig]) -> None:
    rig = rig_factory()
    before = rig.capture()
    write(inside(rig, "a.py"), "A = 2\n")
    result = rig.verify(before)
    ticks = iter([0.0] + [10_000.0] * 10_000)
    verifier = PostExecutionVerifier(clock=lambda: next(ticks))
    out = verifier.measure_diff(
        result,
        workspace_path=rig.workspace_path,
        workspace_prefix=rig.prefix,
        base_commit=rig.base,
        is_cancelled=never,
    )
    assert out is F.DEADLINE_EXCEEDED


def test_so_mede_resultado_verified_de_verify(rig_factory: Callable[..., Rig]) -> None:
    rig = rig_factory()
    bad = PostExecutionResult(status=PostExecutionStatus.UNVERIFIABLE, reason="unverifiable")
    with pytest.raises(ValueError):
        V.measure_diff(
            bad,
            workspace_path=rig.workspace_path,
            workspace_prefix=rig.prefix,
            base_commit=rig.base,
            is_cancelled=never,
        )
    forged = PostExecutionResult(status=PostExecutionStatus.VERIFIED, reason=None)  # sem âncora
    with pytest.raises(ValueError):
        V.measure_diff(
            forged,
            workspace_path=rig.workspace_path,
            workspace_prefix=rig.prefix,
            base_commit=rig.base,
            is_cancelled=never,
        )


def test_nunca_usa_git_diff_status_nem_numstat(
    rig_factory: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = rig_factory()
    seen: list[tuple[str, ...]] = []
    real = run_supervised

    def spy(spec: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append(tuple(spec.argv))
        return real(spec, *args, **kwargs)

    monkeypatch.setattr("app.git_runtime.mediated.run_supervised", spy)
    out = run_and_measure(rig, lambda: write(inside(rig, "a.py"), "A = 2\n"))
    assert isinstance(out, DiffMetrics)
    assert seen, "a medição deveria ler o blob do base pelo Git mediado"
    tokens = {token for argv in seen for token in argv}
    assert not tokens & {"diff", "status", "--numstat", "--textconv", "--ext-diff", "add"}


def test_defeito_inesperado_e_indisponivel_nunca_parcial(
    rig_factory: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = rig_factory()

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("defeito")

    monkeypatch.setattr(ds, "_measure", boom)
    out = run_and_measure(rig, lambda: write(inside(rig, "a.py"), "A = 2\n"))
    assert out is F.UNVERIFIABLE


# --------------------------------------------------------------------------- fábrica


@pytest.fixture
def rig_factory(tmp_path: Path) -> Callable[..., Rig]:
    counter = {"n": 0}

    def make(*, binary_base: bool = False, base_files: dict[str, bytes] | None = None) -> Rig:
        counter["n"] += 1
        root = tmp_path / f"r{counter['n']}"
        rig = make_rig(root)
        if binary_base or base_files:
            # Recria o rig com um blob binário rastreado no base.
            root = tmp_path / f"rb{counter['n']}"
            root.mkdir()
            repo = root / "repo"
            git(root, "init", "-q", "-b", "main", str(repo))
            write(repo / "app" / "a.py", "A = 1\n")
            (repo / "app" / "blob.dat").write_bytes(b"\x00\x01binario\x00")
            for name, data in (base_files or {}).items():
                (repo / "app" / name).write_bytes(data)
            write(repo / ".gitignore", "*.log\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "base")
            from app.git_runtime import create_worktree, repository_layout
            from app.path_runtime import CheckedTreeWriter, bind_root, prepare_worktree_root
            from tests.test_git_worktree_e7_4 import rev
            from tests.test_post_execution_verification_e8_3 import TASK

            base = rev(repo)
            layout = repository_layout(str(repo))
            assert layout is not None
            wroot = root / "worktrees"
            wroot.mkdir()
            prepared = prepare_worktree_root(
                wroot,
                sync_roots=(),
                repository_toplevel=layout.toplevel,
                git_common_dir=layout.git_common_dir,
            )
            local = str(repo / "app")
            outcome = create_worktree(
                local, base_commit=base, task_id=TASK, root=prepared, tree_writer=CheckedTreeWriter
            )
            assert outcome.created and outcome.path and outcome.workspace_path
            rig = Rig(
                tmp=root,
                repo=repo,
                base=base,
                worktree=Path(outcome.path),
                workspace_path=outcome.workspace_path,
                prefix="app/",
                local_path=local,
                root_identity=bind_root(outcome.workspace_path).identity,
            )
        return rig

    return make


_ = os
