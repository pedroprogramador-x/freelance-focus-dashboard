"""E841-AUD-001 e E841-AUD-002 — atomicidade da migration 0003 e `PRAGMA foreign_keys`.

Tudo com SQLite **real**, pelo Alembic de verdade (`command.upgrade`/`downgrade`) e com a conexão
fornecida em `config.attributes["connection"]` — o caminho que o `env.py` oferece a chamadores.
As falhas são injetadas por eventos de cursor do SQLAlchemy, sem tocar no código da migration:

* `antes_da_copia` — imediatamente antes do `INSERT INTO run_new`;
* `durante_a_copia` — um *TEMP trigger* aborta a cópia depois de algumas linhas já copiadas;
* `depois_da_copia` — tudo já reconstruído, falha no carimbo de versão do Alembic (a última
  escrita antes de a migration concluir).

Depois de cada falha o banco é comparado, **byte a byte no schema e linha a linha nos dados**,
com o estado inicial; o índice de exclusão global tem de continuar recusando uma segunda task
`executing`; e o `PRAGMA foreign_keys` da conexão tem de estar como estava.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, event

from app.config import AppSettings
from app.db.base import new_uuid
from app.db.session import create_engine as create_app_engine
from tests.conftest import API_ROOT, run_migrations
from tests.test_run_lifecycle_e8_4_1 import HEAD_BEFORE, _seed_legacy

HEAD = "0003_execution_admission"
FALHA = "falha injetada"

# ----------------------------------------------------------------------------- apoio


def _config(url: str, connection: Connection | None = None) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.attributes["sqlalchemy.url"] = url
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def _url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.as_posix()}"


def _legacy_engine(url: str, *, foreign_keys: bool) -> Engine:
    """Engine no modo padrão do driver (transação implícita só antes de DML)."""
    engine = sa.create_engine(url)
    if foreign_keys:

        @event.listens_for(engine, "connect")
        def _ligar(dbapi: Any, _record: Any) -> None:
            dbapi.execute("PRAGMA foreign_keys=ON")

    return engine


def _app_engine(url: str, tmp_path: Path) -> Engine:
    """O engine da aplicação: `isolation_level=None`, `BEGIN` por evento e FKs ligadas."""
    return create_app_engine(
        AppSettings(data_dir=tmp_path, database_url=url, web_dist_dir=tmp_path / "sem-build")
    )


MODOS = ("legado_fk_off", "legado_fk_on", "aplicacao")


@pytest.fixture(params=MODOS)
def modo(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def _engine(modo: str, url: str, tmp_path: Path) -> Engine:
    if modo == "aplicacao":
        return _app_engine(url, tmp_path)
    return _legacy_engine(url, foreign_keys=modo == "legado_fk_on")


def _fk(connection: Connection) -> int:
    raw: Any = connection.connection.dbapi_connection
    return int(raw.execute("PRAGMA foreign_keys").fetchone()[0])


def _raw_in_transaction(connection: Connection) -> bool:
    raw: Any = connection.connection.dbapi_connection
    return bool(raw.in_transaction)


def _injetar(engine: Engine, quando: str) -> None:
    """Liga a falha `quando` no engine (antes da cópia, durante, depois)."""
    if quando == "antes_da_copia":

        @event.listens_for(engine, "before_cursor_execute")
        def _antes(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
            if statement.lstrip().startswith("INSERT INTO run_new"):
                raise RuntimeError(f"{FALHA}: antes da cópia")

    elif quando == "durante_a_copia":

        @event.listens_for(engine, "after_cursor_execute")
        def _durante(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
            if statement.lstrip().startswith("CREATE TABLE run_new"):
                cursor.execute(
                    "CREATE TEMP TRIGGER falha_na_copia AFTER INSERT ON main.run_new "
                    "WHEN (SELECT COUNT(*) FROM main.run_new) >= 2 "
                    "BEGIN SELECT RAISE(ABORT, 'falha injetada: durante a cópia'); END"
                )

    elif quando == "depois_da_copia":

        @event.listens_for(engine, "before_cursor_execute")
        def _depois(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
            if statement.lstrip().upper().startswith("UPDATE ALEMBIC_VERSION"):
                raise RuntimeError(f"{FALHA}: depois da cópia, no carimbo de versão")

    else:  # pragma: no cover — erro de escrita do teste
        raise AssertionError(quando)


def _snapshot(path: Path) -> dict[str, Any]:
    """Schema inteiro (DDL literal de tabelas, índices e triggers) + dados + versão."""
    connection = sqlite3.connect(path)
    try:
        schema = sorted(
            connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()
        )
        dados = {
            tabela: sorted(connection.execute(f"SELECT * FROM {tabela}").fetchall())  # noqa: S608
            for tabela in ("dev_workspace", "workspace_task", "run", "audit_finding")
        }
        versao = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        integridade = connection.execute("PRAGMA integrity_check").fetchone()[0]
        return {"schema": schema, "dados": dados, "versao": versao, "integridade": integridade}
    finally:
        connection.close()


def _exclusao_global_eficaz(path: Path, workspace_id: str) -> None:
    """Com o índice de exclusão vivo, a segunda task `executing` é recusada pelo banco."""
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")

        def inserir() -> None:
            connection.execute(
                "INSERT INTO workspace_task (id, workspace_id, title, goal, status, phase,"
                " version, risk, complexity, risk_source, execution_mode, agents, attempts,"
                " fix_rounds, cancel_requested, created_at, started_at) VALUES (?, ?, 't', 'g',"
                " 'executing', 'implementing', 1, 'low', 'low', 'hard_rule', 'orchestrated', '[]',"
                " 0, 0, 0, '2026-01-01', '2026-01-01')",
                (new_uuid(), workspace_id),
            )

        inserir()
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            inserir()
        connection.rollback()
    finally:
        connection.close()


def _rodar(
    engine: Engine, url: str, acao: Callable[[Config], None]
) -> tuple[BaseException | None, int, bool]:
    """Roda `acao` com a conexão do engine e devolve (erro, FK depois, transação aberta?)."""
    with engine.connect() as connection:
        erro: BaseException | None = None
        try:
            acao(_config(url, connection))
        except Exception as exc:  # o teste decide o que era esperado
            erro = exc
        return erro, _fk(connection), _raw_in_transaction(connection)


@pytest.fixture
def banco_em_head(tmp_path: Path) -> Iterator[tuple[str, Path, dict[str, str]]]:
    """Banco em 0003 com dados válidos: Runs finais medidos, finding e um workspace."""
    path = tmp_path / "head.db"
    url = _url(path)
    run_migrations(url, HEAD_BEFORE)
    ids = _seed_legacy(path)
    run_migrations(url)
    yield url, path, ids


@pytest.fixture
def banco_em_0002(tmp_path: Path) -> Iterator[tuple[str, Path, dict[str, str]]]:
    path = tmp_path / "anterior.db"
    url = _url(path)
    run_migrations(url, HEAD_BEFORE)
    ids = _seed_legacy(path)
    yield url, path, ids


def _fk_esperado(modo: str) -> int:
    return 0 if modo == "legado_fk_off" else 1


# ============================================================ E841-AUD-001 — downgrade


@pytest.mark.parametrize("quando", ["antes_da_copia", "durante_a_copia", "depois_da_copia"])
def test_aud_001_downgrade_interrompido_volta_ao_estado_inicial(
    banco_em_head: tuple[str, Path, dict[str, str]], tmp_path: Path, modo: str, quando: str
) -> None:
    """AUD-001-A/B/C: nenhum DDL parcial sobrevive, e o índice de exclusão continua eficaz."""
    url, path, ids = banco_em_head
    antes = _snapshot(path)
    engine = _engine(modo, url, tmp_path)
    _injetar(engine, quando)
    try:
        erro, fk_depois, em_transacao = _rodar(
            engine, url, lambda cfg: command.downgrade(cfg, HEAD_BEFORE)
        )
    finally:
        engine.dispose()

    assert erro is not None and FALHA in str(erro), erro
    assert _snapshot(path) == antes, "schema/dados/versão diferentes do estado inicial"
    assert antes["versao"] == HEAD
    assert "uq_workspace_task_single_executing" in {row[1] for row in antes["schema"]}
    _exclusao_global_eficaz(path, ids["workspace"])
    assert fk_depois == _fk_esperado(modo)  # AUD-002-D
    assert em_transacao is False


def test_aud_001_d_downgrade_recusado_pelos_dados_nao_altera_nada(
    banco_em_head: tuple[str, Path, dict[str, str]], tmp_path: Path, modo: str
) -> None:
    url, path, ids = banco_em_head
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "INSERT INTO run (id, invocation_id, task_id, agent, purpose, attempt_index,"
            " fix_round, provider, provider_adapter, transport, status, started_at, token_source,"
            " files_read_source) VALUES (?, 'aberto', ?, 'orchestrator', 'execution', 0, 0, 'p',"
            " 'a', 'process', 'running', '2026-02-01', 'unavailable', 'unavailable')",
            (new_uuid(), ids["task"]),
        )
        connection.commit()
    finally:
        connection.close()
    antes = _snapshot(path)
    engine = _engine(modo, url, tmp_path)
    try:
        erro, fk_depois, em_transacao = _rodar(
            engine, url, lambda cfg: command.downgrade(cfg, HEAD_BEFORE)
        )
    finally:
        engine.dispose()

    assert erro is not None and "downgrade recusado" in str(erro)
    assert _snapshot(path) == antes
    _exclusao_global_eficaz(path, ids["workspace"])
    assert fk_depois == _fk_esperado(modo)
    assert em_transacao is False


def test_downgrade_legitimo_continua_funcionando(
    banco_em_head: tuple[str, Path, dict[str, str]], tmp_path: Path, modo: str
) -> None:
    url, path, ids = banco_em_head
    engine = _engine(modo, url, tmp_path)
    try:
        erro, fk_depois, em_transacao = _rodar(
            engine, url, lambda cfg: command.downgrade(cfg, HEAD_BEFORE)
        )
    finally:
        engine.dispose()

    assert erro is None, erro
    depois = _snapshot(path)
    assert depois["versao"] == HEAD_BEFORE
    nomes = {row[1] for row in depois["schema"]}
    assert "uq_workspace_task_single_executing" not in nomes
    assert "run_append_only_after_finish" in nomes and "run_identity_immutable" not in nomes
    assert len(depois["dados"]["run"]) == 2 and len(depois["dados"]["audit_finding"]) == 1
    assert depois["integridade"] == "ok"
    assert fk_depois == _fk_esperado(modo)  # AUD-002-B
    assert em_transacao is False


# ============================================================== E841-AUD-001 — upgrade


@pytest.mark.parametrize("quando", ["antes_da_copia", "durante_a_copia", "depois_da_copia"])
def test_upgrade_interrompido_volta_ao_estado_inicial(
    banco_em_0002: tuple[str, Path, dict[str, str]], tmp_path: Path, modo: str, quando: str
) -> None:
    url, path, _ids = banco_em_0002
    antes = _snapshot(path)
    engine = _engine(modo, url, tmp_path)
    _injetar(engine, quando)
    try:
        erro, fk_depois, em_transacao = _rodar(engine, url, lambda cfg: command.upgrade(cfg, HEAD))
    finally:
        engine.dispose()

    assert erro is not None and FALHA in str(erro), erro
    assert _snapshot(path) == antes
    assert antes["versao"] == HEAD_BEFORE
    assert fk_depois == _fk_esperado(modo)  # AUD-002-C
    assert em_transacao is False


def test_upgrade_legitimo_preserva_dados_e_cria_a_exclusao(
    banco_em_0002: tuple[str, Path, dict[str, str]], tmp_path: Path, modo: str
) -> None:
    url, path, ids = banco_em_0002
    antes = _snapshot(path)
    engine = _engine(modo, url, tmp_path)
    try:
        erro, fk_depois, em_transacao = _rodar(engine, url, lambda cfg: command.upgrade(cfg, HEAD))
    finally:
        engine.dispose()

    assert erro is None, erro
    depois = _snapshot(path)
    assert depois["versao"] == HEAD
    assert depois["dados"] == antes["dados"]  # Runs finais medidos, finding e task intactos
    assert depois["integridade"] == "ok"
    _exclusao_global_eficaz(path, ids["workspace"])
    assert fk_depois == _fk_esperado(modo)  # AUD-002-A
    assert em_transacao is False


# ================================================================== E841-AUD-002 — FKs


@pytest.mark.parametrize("motor", ["legado_fk_on", "aplicacao"])
def test_aud_002_e_fk_continua_aplicada_na_mesma_conexao_depois_da_migracao(
    banco_em_0002: tuple[str, Path, dict[str, str]], tmp_path: Path, motor: str
) -> None:
    """A conexão que rodou a migração volta com FKs ligadas e **aplicadas**: Run órfão falha."""
    url, _path, _ids = banco_em_0002
    engine = _engine(motor, url, tmp_path)
    try:
        with engine.connect() as connection:
            assert _fk(connection) == 1
            command.upgrade(_config(url, connection), HEAD)
            assert _fk(connection) == 1
            assert _raw_in_transaction(connection) is False
            with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
                connection.exec_driver_sql(
                    "INSERT INTO run (id, invocation_id, task_id, agent, purpose, attempt_index,"
                    " fix_round, provider, provider_adapter, transport, status, started_at,"
                    " token_source, files_read_source) VALUES ('orfao', 'orfao', 'nao-existe',"
                    " 'orchestrator', 'execution', 0, 0, 'p', 'a', 'process', 'running',"
                    " '2026-02-01', 'unavailable', 'unavailable')"
                )
            connection.rollback()

            command.downgrade(_config(url, connection), HEAD_BEFORE)
            assert _fk(connection) == 1  # AUD-002-B
            with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
                connection.exec_driver_sql(
                    "INSERT INTO audit_finding (id, run_id, purpose, severity, category, summary,"
                    " status, created_at) VALUES ('f', 'nao-existe', 'workflow_audit', 'low', 'x',"
                    " 's', 'open', '2026-01-01')"
                )
            connection.rollback()
    finally:
        engine.dispose()


def test_aud_002_conexoes_novas_da_aplicacao_tambem_aplicam_fk(
    banco_em_0002: tuple[str, Path, dict[str, str]], tmp_path: Path
) -> None:
    url, path, _ids = banco_em_0002
    run_migrations(url)  # caminho padrão do projeto (engine próprio do env.py)
    engine = _app_engine(url, tmp_path)
    try:
        with engine.connect() as connection:
            assert _fk(connection) == 1
            with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
                connection.exec_driver_sql(
                    "INSERT INTO run (id, invocation_id, task_id, agent, purpose, attempt_index,"
                    " fix_round, provider, provider_adapter, transport, status, started_at,"
                    " token_source, files_read_source) VALUES ('orfao', 'orfao', 'nao-existe',"
                    " 'orchestrator', 'execution', 0, 0, 'p', 'a', 'process', 'running',"
                    " '2026-02-01', 'unavailable', 'unavailable')"
                )
    finally:
        engine.dispose()
    assert _snapshot(path)["integridade"] == "ok"


def test_transacao_externa_com_fk_ligada_e_recusada_antes_de_qualquer_ddl(
    banco_em_0002: tuple[str, Path, dict[str, str]], tmp_path: Path
) -> None:
    """O chamador comitaria depois de nós: não há como religar as FKs. Recusa, sem efeito."""
    url, path, _ids = banco_em_0002
    antes = _snapshot(path)
    engine = _legacy_engine(url, foreign_keys=True)
    try:
        with engine.connect() as connection:
            transacao = connection.begin()
            with pytest.raises(RuntimeError, match="foreign_keys"):
                command.upgrade(_config(url, connection), HEAD)
            transacao.rollback()
            assert _fk(connection) == 1
    finally:
        engine.dispose()
    assert _snapshot(path) == antes


def test_transacao_externa_com_fk_desligada_fica_inteira_na_transacao_do_chamador(
    banco_em_0002: tuple[str, Path, dict[str, str]], tmp_path: Path
) -> None:
    """A migração não comita a transação do chamador: o rollback dele desfaz tudo."""
    url, path, _ids = banco_em_0002
    antes = _snapshot(path)
    engine = _legacy_engine(url, foreign_keys=False)
    try:
        with engine.connect() as connection:
            transacao = connection.begin()
            command.upgrade(_config(url, connection), HEAD)
            assert _raw_in_transaction(connection) is True  # nada foi comitado por nós
            transacao.rollback()
        assert _snapshot(path) == antes

        with engine.connect() as connection:
            transacao = connection.begin()
            command.upgrade(_config(url, connection), HEAD)
            transacao.commit()
    finally:
        engine.dispose()
    assert _snapshot(path)["versao"] == HEAD


def test_o_env_py_restaura_fk_mesmo_quando_a_migracao_e_recusada(
    tmp_path: Path,
) -> None:
    """Upgrade recusado pelas pré-condições (Run sem `finished_at`): FKs voltam ligadas."""
    path = tmp_path / "recusa.db"
    url = _url(path)
    run_migrations(url, HEAD_BEFORE)
    _seed_legacy(path, extra_runs=("ok",))
    antes = _snapshot(path)
    engine = _legacy_engine(url, foreign_keys=True)
    try:
        erro, fk_depois, em_transacao = _rodar(engine, url, lambda cfg: command.upgrade(cfg, HEAD))
    finally:
        engine.dispose()

    assert erro is not None and "sem `finished_at`" in str(erro)
    assert _snapshot(path) == antes
    assert fk_depois == 1 and em_transacao is False
