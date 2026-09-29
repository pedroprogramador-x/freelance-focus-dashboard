"""Conflito de escrita concorrente — o **único** classificador, e o erro que ele produz.

## Por que isto vive em `db/`, e não no Orchestrator

A pergunta "este `OperationalError` é o perdedor de uma corrida, ou um defeito?" é sobre a
semântica transacional do **driver**, não sobre tasks. Ela nasceu em
`orchestrator/execution_manager.py` (E6-AUD-004), e ficar ali foi o que permitiu a
E6-AUD3-002: a unidade de trabalho que de fato commita — `db.session.session_scope` — não
podia consultá-la sem inverter a direção das dependências, então o conflito no `COMMIT`
final continuava subindo como `500`.

Aqui as duas camadas alcançam o mesmo classificador:

* `db.session.session_scope` traduz para `WriteConflict` — o conflito visto pela
  infraestrutura, sem saber de que comando veio;
* `orchestrator.execution_manager` traduz para `ConcurrentTaskUpdate` — o mesmo fato dito
  no vocabulário de [02] §4, com o `id` da task.

Os dois status são `409`. A distinção de `code` é honesta: um diz "a linha que você leu
mudou"; o outro diz "a transação não pôde ser fechada". Quem lê o corpo sabe qual foi.

## O conjunto é fechado, e continua fechado

Alargar para "todo `OperationalError` é concorrência" transformaria uma tabela corrompida,
um arquivo sem permissão e um gatilho que aborta num `409 tente de novo` — a mesma classe
de erro que E6-AUD-008 fechou no startup, onde `except OperationalError: pass` escondia
falha real sob a condição esperada. Um erro que o driver não sabe nomear **não** é tratado
como conflito esperado.
"""

from __future__ import annotations

from sqlalchemy.exc import OperationalError

#: Códigos do SQLite que significam **conflito de escrita**, não defeito. `sqlite_errorname`
#: existe no `sqlite3` do CPython desde 3.11 e é o único discriminador estável — casar
#: substring de mensagem para decidir semântica transacional trocaria um defeito por outro.
SQLITE_WRITE_CONFLICTS: frozenset[str] = frozenset(
    {
        "SQLITE_BUSY",
        "SQLITE_BUSY_SNAPSHOT",
        "SQLITE_BUSY_RECOVERY",
        "SQLITE_BUSY_TIMEOUT",
        "SQLITE_LOCKED",
        "SQLITE_LOCKED_SHAREDCACHE",
    }
)


class WriteConflict(Exception):
    """Uma transação perdeu uma corrida de escrita. **409**, nunca `500`.

    Mesma forma (`code`, `status_code`, `message`) das hierarquias de erro de domínio, de
    propósito: o handler único de `app.main` a serve sem ganhar um ramo novo.
    """

    code = "write_conflict"
    status_code = 409

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def is_write_conflict(error: BaseException) -> bool:
    """Este erro é o perdedor de uma corrida de escrita reconhecida?

    Só o nome simbólico decide. Quando o driver não o expõe — outro backend, uma versão
    antiga —, a resposta é `False`: não conseguir classificar não autoriza tratar um erro
    desconhecido como conflito esperado. Ver o docstring do módulo.
    """
    if not isinstance(error, OperationalError):
        return False
    name = getattr(error.orig, "sqlite_errorname", None)
    return isinstance(name, str) and name in SQLITE_WRITE_CONFLICTS


__all__ = ["SQLITE_WRITE_CONFLICTS", "WriteConflict", "is_write_conflict"]
