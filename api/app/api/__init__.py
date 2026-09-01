"""Camada HTTP.

Rotas ficam sob `/api` para que, a partir da E3, o backend possa servir o SPA compilado
em `/` — mesma origem, sem CORS
([06](../../../docs/architecture/06-api-and-ui-boundaries.md) §1).

Uma rota traduz HTTP ↔ serviço e não carrega regra de negócio. Ela também não importa
`agent_runtime`, `tool_executor` nem `git_runtime` diretamente
([01](../../../docs/architecture/01-v1-architecture.md) §3).
"""
