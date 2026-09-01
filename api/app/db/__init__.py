"""Camada de persistência.

`db/` concentra engine, sessão e **todas** as tabelas
([01](../../../docs/architecture/01-v1-architecture.md) §2). As sete entidades vivem num
único módulo para que o Alembic enxergue uma `MetaData` só e para que nenhuma dependência
circular entre módulos de domínio precise existir.

Direção de dependência: `db/` é infraestrutura (L1). Ele **não** importa
`workspace`, `context_engine` ou `orchestrator`.
"""
