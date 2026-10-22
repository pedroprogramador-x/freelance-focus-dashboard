# CLAUDE.md

@AGENTS.md

Camada específica do Claude Code. As regras comuns (arquitetura congelada,
segredos, persistências, execução operacional, comandos, Git) estão em
`AGENTS.md`, importado acima; não as duplique aqui.

## Skills e subagente do projeto

Em `.claude/`. As skills são de invocação manual (`disable-model-invocation`):

- `/tino-plan <incremento>` — planeja um incremento sem implementar: escopo,
  estado atual, riscos, interfaces, contraprovas, critérios de aceite, modelo
  e esforço, pacote de auditoria para o Codex.
- `/tino-validate [dry-run]` — prepara e, com sua autorização, executa gates
  direcionados e gera relatório. Nunca declara GREEN; o veredito é do Codex.
- `test-investigator` (subagente) — investiga falhas de teste e devolve causa
  provável, evidências, contraprova e confiança. Sem Edit/Write; o Bash dele
  depende da confirmação de cada comando, não é isolamento garantido.

## Como trabalhar

- Antes de alteração complexa ou que cruze módulos, planeje primeiro
  (`/tino-plan` ou modo Plan) e aguarde aprovação do Pedro.
- Escolha modelo e esforço de forma consciente e declare-os (ver
  `AGENTS.md`). Padrão: Sonnet com esforço alto; Opus só com justificativa
  concreta de complexidade (concorrência, recovery, mudança de contrato).
- Prefira `Explore`/`Plan` nativos para busca e planejamento amplos. Peça uma
  leitura parcial do `AGENT_LOG.md`, nunca a integral.

## Permissões e confirmações

- Comandos de shell passam pela confirmação normal de permissão; não
  contorne com `bypassPermissions` nem amplie permissões por conta própria.
- Escritas que o Pedro não pediu (commit, push, PR, merge, edição de `docs/`,
  de `.claude/settings*` ou de configurações globais) exigem autorização
  explícita.
