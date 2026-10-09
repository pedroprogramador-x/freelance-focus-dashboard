"""E841-AUD-002 (reauditoria) — o cuidado com `PRAGMA foreign_keys` no `env.py` é à prova de falha.

Invariante: depois de qualquer erro ocorrido a partir do momento em que o `env.py` mexe no
pragma, **ou** a restauração está comprovada, **ou** a conexão física está fechada e não volta
ao uso. Nunca uma exceção com a conexão sem FKs reutilizável.

Tudo pelo fluxo real (`command.upgrade`/`downgrade` → `env.py::_run`), com SQLite real e a
conexão em `config.attributes["connection"]`. As falhas são do próprio SQLite ou do driver:

* `sqlite3.set_authorizer` negando `PRAGMA foreign_keys=ON` (a contraprova original do Codex);
* uma subclasse de `sqlite3.Connection` (`connect_args={"factory": ...}`) que falha na leitura
  de conferência, deixa transação aberta ou fecha a conexão depois do commit, ou cujo `close()`
  falha.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import Connection, Engine, event

from tests.conftest import run_migrations
from tests.test_migration_atomicity_e8_4_1 import (
    FALHA,
    HEAD,
    _app_engine,
    _config,
    _fk,
    _injetar,
    _legacy_engine,
    _raw_in_transaction,
    _snapshot,
    _url,
)
from tests.test_run_lifecycle_e8_4_1 import HEAD_BEFORE, _seed_legacy

ERRO_FK = "ForeignKeysRestoreError"
FINDING_ORFAO = (
    "INSERT INTO audit_finding (id, run_id, purpose, severity, category, summary, status,"
    " created_at) VALUES ('orfao', 'nao-existe', 'workflow_audit', 'low', 'x', 's', 'open',"
    " '2026-01-01')"
)

# ----------------------------------------------------------------------------- apoio


def _ligado(valor: str | None) -> bool:
    return valor is not None and valor.strip().lower() in {"on", "1", "true", "yes"}


def _negar_religar(engine: Engine) -> None:
    """Contraprova do Codex: o SQLite nega `PRAGMA foreign_keys=ON` (o `OFF` passa)."""

    @event.listens_for(engine, "connect")
    def _instalar(dbapi: Any, _record: Any) -> None:
        def autorizar(acao: int, arg1: str | None, arg2: str | None, *_: Any) -> int:
            if acao == sqlite3.SQLITE_PRAGMA and arg1 == "foreign_keys" and _ligado(arg2):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        dbapi.set_authorizer(autorizar)


def _pragma_fk(sql: str) -> str | None:
    """`None` se não for `PRAGMA foreign_keys`; `""` para leitura; o valor para escrita."""
    compacto = "".join(sql.split()).lower()
    if not compacto.startswith("pragmaforeign_keys"):
        return None
    return compacto.removeprefix("pragmaforeign_keys").removeprefix("=")


class ConexaoComFalhas(sqlite3.Connection):
    """`sqlite3.Connection` real, com falhas programáveis por instância."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.falhas: set[str] = set()
        self.viu_off = False
        self.viu_on = False
        self.off_alguma_vez = False

    def execute(self, sql: str, *args: Any) -> sqlite3.Cursor:
        pragma = _pragma_fk(sql)
        if pragma == "":  # leitura do pragma
            if self.viu_on and self.off_alguma_vez and "ler_depois_do_on" in self.falhas:
                raise sqlite3.OperationalError(f"{FALHA}: leitura depois do ON")
            if self.off_alguma_vez and "ler_depois_do_off_sempre" in self.falhas:
                raise sqlite3.OperationalError(f"{FALHA}: leitura depois do OFF")
            if self.viu_off and "ler_depois_do_off_uma_vez" in self.falhas:
                self.falhas.discard("ler_depois_do_off_uma_vez")
                raise sqlite3.OperationalError(f"{FALHA}: leitura depois do OFF")
        cursor = super().execute(sql, *args)
        if pragma is not None and pragma != "":
            self.viu_on = _ligado(pragma)
            self.viu_off = not self.viu_on
            self.off_alguma_vez |= self.viu_off
        return cursor

    def commit(self) -> None:
        super().commit()
        if "transacao_aberta_depois_do_commit" in self.falhas:
            super().execute("BEGIN")
        if "fechada_depois_do_commit" in self.falhas:
            super().close()

    def close(self) -> None:
        if "close_falha" in self.falhas:
            raise sqlite3.OperationalError(f"{FALHA}: close")
        super().close()


def _engine_com_falhas(url: str) -> Engine:
    engine = sa.create_engine(url, connect_args={"factory": ConexaoComFalhas})

    @event.listens_for(engine, "connect")
    def _ligar(dbapi: Any, _record: Any) -> None:
        dbapi.execute("PRAGMA foreign_keys=ON")

    return engine


def _raw(connection: Connection) -> Any:
    return connection.connection.dbapi_connection


def _fechada(raw: sqlite3.Connection) -> bool:
    try:
        raw.execute("SELECT 1")
    except sqlite3.ProgrammingError as exc:
        return "closed" in str(exc)
    return False


def _orfao_recusado(connection: Connection) -> None:
    """A conexão (física nova) aplica FKs: finding órfão é recusado pelo banco."""
    assert _fk(connection) == 1
    with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
        connection.exec_driver_sql(FINDING_ORFAO)
    connection.rollback()


def _sem_reuso_inseguro(engine: Engine, connection: Connection, raw_antigo: Any) -> None:
    """Nem a mesma `Connection` nem o pool devolvem a conexão física insegura."""
    assert connection.invalidated
    assert _fechada(raw_antigo), "a conexão física sem FKs continua aceitando comandos"
    # A mesma `Connection` reconecta com uma conexão física NOVA, que aplica FKs.
    assert _raw(connection) is not raw_antigo
    _orfao_recusado(connection)
    # Uma conexão nova do pool também.
    with engine.connect() as outra:
        assert _raw(outra) is not raw_antigo
        _orfao_recusado(outra)


def _executar(
    engine: Engine, url: str, acao: str, falhas: set[str] | None = None
) -> tuple[BaseException | None, Connection, Any]:
    """Roda a migração pela conexão do engine; devolve (erro, conexão, conexão física usada)."""
    connection = engine.connect()
    raw = _raw(connection)
    if falhas:
        raw.falhas.update(falhas)
    erro: BaseException | None = None
    try:
        if acao == "upgrade":
            command.upgrade(_config(url, connection), HEAD)
        else:
            command.downgrade(_config(url, connection), HEAD_BEFORE)
    except Exception as exc:  # o teste decide o que era esperado
        erro = exc
    return erro, connection, raw


def _banco(tmp_path: Path, versao: str) -> tuple[str, Path]:
    path = tmp_path / "fk.db"
    url = _url(path)
    run_migrations(url, HEAD_BEFORE)
    _seed_legacy(path)
    if versao == HEAD:
        run_migrations(url)
    return url, path


def _integro(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


@pytest.fixture
def engines() -> Iterator[list[Engine]]:
    criados: list[Engine] = []
    yield criados
    for engine in criados:
        engine.dispose()


# ============================== TEST-FK-01/05 — religar negado pelo SQLite (contraprova do Codex)


@pytest.mark.parametrize("acao", ["upgrade", "downgrade"])
@pytest.mark.parametrize("motor", ["legado_fk_on", "aplicacao"])
def test_fk_01_05_religar_negado_invalida_e_fecha_a_conexao_fisica(
    tmp_path: Path, engines: list[Engine], motor: str, acao: str
) -> None:
    """Migração comitada pelo `_run` real; o `ON` é negado; a conexão física não volta ao uso."""
    url, path = _banco(tmp_path, HEAD_BEFORE if acao == "upgrade" else HEAD)
    if motor == "aplicacao":
        engine = _app_engine(url, tmp_path)
    else:
        engine = _legacy_engine(url, foreign_keys=True)
    engines.append(engine)
    _negar_religar(engine)

    erro, connection, raw = _executar(engine, url, acao)
    try:
        assert type(erro).__name__ == ERRO_FK, erro
        assert "not authorized" in repr(erro.restore_error)  # type: ignore[union-attr]
        assert erro.original is None  # type: ignore[union-attr]
        assert erro.invalidation_errors == ()  # type: ignore[union-attr]
        assert erro.physically_closed is True  # type: ignore[union-attr]
        assert "CONEXÃO INSEGURA" not in str(erro)
        _sem_reuso_inseguro(engine, connection, raw)
    finally:
        connection.close()

    # A migração já tinha sido comitada pelo Alembic: o erro não a desfaz, e o banco está íntegro.
    assert _snapshot(path)["versao"] == (HEAD if acao == "upgrade" else HEAD_BEFORE)
    _integro(path)


def test_fk_05_migracao_falha_e_restauracao_falha_preserva_a_causa_original(
    tmp_path: Path, engines: list[Engine]
) -> None:
    """Rollback do Alembic + `ON` negado: o erro estruturado carrega a falha da migração."""
    url, path = _banco(tmp_path, HEAD_BEFORE)
    antes = _snapshot(path)
    engine = _legacy_engine(url, foreign_keys=True)
    engines.append(engine)
    _injetar(engine, "durante_a_copia")
    _negar_religar(engine)

    erro, connection, raw = _executar(engine, url, "upgrade")
    try:
        assert type(erro).__name__ == ERRO_FK, erro
        assert FALHA in str(erro.original)  # type: ignore[union-attr]
        assert "erro original da migração" in str(erro) and FALHA in str(erro)
        # e a cadeia de exceções também preserva as duas
        assert erro.__cause__ is erro.restore_error  # type: ignore[union-attr]
        _sem_reuso_inseguro(engine, connection, raw)
    finally:
        connection.close()
    assert _snapshot(path) == antes  # E841-AUD-001 continua valendo


# ================================ TEST-FK-02 — falha ao comprovar a restauração (depois do ON)


@pytest.mark.parametrize(
    "falha", ["ler_depois_do_on", "transacao_aberta_depois_do_commit", "fechada_depois_do_commit"]
)
def test_fk_02_restauracao_nao_comprovada_invalida_a_conexao(
    tmp_path: Path, engines: list[Engine], falha: str
) -> None:
    url, path = _banco(tmp_path, HEAD_BEFORE)
    engine = _engine_com_falhas(url)
    engines.append(engine)

    erro, connection, raw = _executar(engine, url, "upgrade", {falha})
    try:
        assert type(erro).__name__ == ERRO_FK, erro
        assert erro.physically_closed is True  # type: ignore[union-attr]
        _sem_reuso_inseguro(engine, connection, raw)
    finally:
        connection.close()
    assert _snapshot(path)["versao"] == HEAD
    _integro(path)


# =================================== TEST-FK-03 — falha na conferência logo depois do OFF


def test_fk_03_falha_na_conferencia_depois_do_off_e_restaurada_com_a_causa_original(
    tmp_path: Path, engines: list[Engine]
) -> None:
    """Falha antes do Alembic: o `env.py` religa, **confere**, e a causa original sobe intacta."""
    url, path = _banco(tmp_path, HEAD_BEFORE)
    antes = _snapshot(path)
    engine = _engine_com_falhas(url)
    engines.append(engine)

    erro, connection, raw = _executar(engine, url, "upgrade", {"ler_depois_do_off_uma_vez"})
    try:
        assert isinstance(erro, sqlite3.OperationalError), erro
        assert "leitura depois do OFF" in str(erro)
        assert not connection.invalidated and _raw(connection) is raw
        assert _fk(connection) == 1  # restaurado e comprovado na MESMA conexão física
        assert _raw_in_transaction(connection) is False
        _orfao_recusado(connection)
    finally:
        connection.close()
    assert _snapshot(path) == antes


def test_fk_03_falha_persistente_depois_do_off_invalida_a_conexao(
    tmp_path: Path, engines: list[Engine]
) -> None:
    """Nem a conferência do `OFF` nem a do `ON` funcionam: não há prova — a conexão é fechada."""
    url, path = _banco(tmp_path, HEAD_BEFORE)
    antes = _snapshot(path)
    engine = _engine_com_falhas(url)
    engines.append(engine)

    erro, connection, raw = _executar(engine, url, "upgrade", {"ler_depois_do_off_sempre"})
    try:
        assert type(erro).__name__ == ERRO_FK, erro
        assert "leitura depois do OFF" in str(erro.original)  # type: ignore[union-attr]
        assert erro.physically_closed is True  # type: ignore[union-attr]
        _sem_reuso_inseguro(engine, connection, raw)
    finally:
        connection.close()
    assert _snapshot(path) == antes


# ================================================ TEST-FK-04 — a própria invalidação falha


def test_fk_04_invalidate_do_sqlalchemy_falha_a_conexao_e_fechada_e_o_erro_relata(
    tmp_path: Path, engines: list[Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, path = _banco(tmp_path, HEAD_BEFORE)
    engine = _legacy_engine(url, foreign_keys=True)
    engines.append(engine)
    _negar_religar(engine)

    connection = engine.connect()
    raw = _raw(connection)

    original = connection.invalidate
    chamadas: list[object] = []

    def invalidate_quebrado(*args: Any, **kwargs: Any) -> None:
        chamadas.append(args)
        if len(chamadas) == 1:  # só a invalidação pedida pelo `env.py` falha
            raise RuntimeError(f"{FALHA}: invalidate")
        original(*args, **kwargs)

    monkeypatch.setattr(connection, "invalidate", invalidate_quebrado)
    try:
        with pytest.raises(Exception) as info:
            command.upgrade(_config(url, connection), HEAD)
        erro: Any = info.value
        assert type(erro).__name__ == ERRO_FK
        assert [str(e) for e in erro.invalidation_errors] == [f"{FALHA}: invalidate"]
        assert "a invalidação falhou" in str(erro)  # não suprimida
        assert erro.physically_closed is True  # o fechamento direto funcionou
        assert _fechada(raw)
        # A `Connection` não foi invalidada pelo `env.py`, mas a física está fechada: o próximo uso
        # falha (e o SQLAlchemy, ao detectar, a invalida); nada é escrito sem FKs.
        with pytest.raises(sa.exc.DBAPIError, match="closed database"):
            connection.exec_driver_sql(FINDING_ORFAO)
        assert connection.invalidated
    finally:
        monkeypatch.undo()
        connection.close()
    with engine.connect() as outra:
        _orfao_recusado(outra)
    _integro(path)


def test_fk_04_fechamento_fisico_falha_e_a_conexao_e_declarada_insegura(
    tmp_path: Path, engines: list[Engine]
) -> None:
    """O pool do SQLAlchemy só registra em log o erro do `close()`; o `env.py` não aceita isso."""
    url, _path = _banco(tmp_path, HEAD_BEFORE)
    engine = _engine_com_falhas(url)
    engines.append(engine)

    erro, connection, raw = _executar(engine, url, "upgrade", {"ler_depois_do_on", "close_falha"})
    try:
        assert type(erro).__name__ == ERRO_FK, erro
        assert erro.physically_closed is False  # type: ignore[union-attr]
        assert "CONEXÃO INSEGURA" in str(erro)
        assert "não volta ao uso" not in str(erro)  # nada classificado como seguro
        assert [str(e) for e in erro.invalidation_errors] == [  # type: ignore[union-attr]
            f"{FALHA}: close"
        ]
        # O SQLAlchemy considera a conexão invalidada (o erro do `close()` ficou só no log) e o
        # pool largou essa conexão física — mas ela continua aberta, e o erro diz isso.
        assert not _fechada(raw)
        assert connection.invalidated and _raw(connection) is not raw
        _orfao_recusado(connection)
    finally:
        connection.close()
        raw.falhas.discard("close_falha")
        raw.close()
