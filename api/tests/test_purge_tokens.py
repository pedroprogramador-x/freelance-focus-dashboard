"""Gate 4 da E3 (unidade) — `PurgeTokenStore`: TTL, uso único, vínculo a workspace.

O relógio é injetado para que "token expirado" seja determinístico, sem `sleep`.
"""

from __future__ import annotations

from app.workspace import PurgeTokenStore


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _store(clock: FakeClock, *, ttl_seconds: float = 60.0) -> PurgeTokenStore:
    return PurgeTokenStore(clock=clock, ttl_seconds=ttl_seconds)


def test_token_emitido_e_consumido_uma_vez() -> None:
    clock = FakeClock()
    store = _store(clock)

    token = store.issue("ws-1")
    assert store.consume("ws-1", token) is True
    # segundo consumo do mesmo token: recusado (uso único)
    assert store.consume("ws-1", token) is False


def test_token_expira_apos_o_ttl() -> None:
    clock = FakeClock()
    store = _store(clock, ttl_seconds=60.0)

    token = store.issue("ws-1")
    clock.advance(59.9)
    # ainda dentro do prazo — mas consumir aqui gastaria o token; use um token à parte
    outro = store.issue("ws-1")
    assert store.consume("ws-1", outro) is True

    clock.advance(0.2)  # agora 60.1s desde a emissão de `token`
    assert store.consume("ws-1", token) is False


def test_token_de_outro_workspace_e_recusado() -> None:
    clock = FakeClock()
    store = _store(clock)

    token = store.issue("ws-1")
    assert store.consume("ws-2", token) is False


def test_token_desconhecido_e_recusado() -> None:
    store = _store(FakeClock())
    assert store.consume("ws-1", "nunca-foi-emitido") is False


def test_repr_nao_expoe_tokens() -> None:
    clock = FakeClock()
    store = _store(clock)
    token = store.issue("ws-1")

    assert token not in repr(store)
    assert "ws-1" not in repr(store)


def test_prune_remove_entradas_expiradas_na_emissao() -> None:
    clock = FakeClock()
    store = _store(clock, ttl_seconds=10.0)

    antigo = store.issue("ws-1")
    clock.advance(11.0)
    store.issue("ws-2")  # dispara _prune

    assert store.consume("ws-1", antigo) is False
