---
name: tino-plan
description: Planeja um incremento do Tino (fase/sub-fase do roadmap) sem implementar código. Invocação manual.
disable-model-invocation: true
argument-hint: "<incremento, ex.: E8.4.5 I5>"
allowed-tools: Read, Grep, Glob
model: sonnet
effort: high
---

# tino-plan — planejamento de incremento

Alvo: `$ARGUMENTS` (se vazio, pergunte qual incremento planejar antes de continuar).

Esta skill **só planeja**. Não edite arquivos, não implemente código, não faça
commit/push/merge/PR e não emita veredito de auditoria independente: a
auditoria é do Codex, feita por humano. Se algo exigir aprovação do Pedro,
liste como pendência em vez de assumir.

## 1. Leitura (nesta ordem, mínima necessária)

1. `CLAUDE.md` e `AGENTS.md` — regras vigentes (siga o que está escrito; se
   algum caminho de usuário do Windows parecer desatualizado, não o use:
   resolva o ambiente atual pelo sistema).
2. `docs/architecture/07-roadmap-v1.md` — escopo da fase e "Decisões adiadas".
3. ADRs de `docs/adr/` e documentos de `docs/architecture/` relevantes ao
   alvo (use Grep para localizar; `docs/` é **arquitetura congelada**: leia,
   nunca proponha editar sem autorização explícita do Pedro).
4. **`AGENT_LOG.md` — NUNCA carregue inteiro** (centenas de KB). Use Grep em
   `^## ` para listar os títulos, leia o cabeçalho (primeiras ~15 linhas) e
   somente as entradas mais recentes e as do incremento alvo/anterior, com
   `offset`/`limit`.
5. Código atual do que será tocado (`api/app/...`, `api/tests/...`, `src/...`),
   a partir de Grep/Glob. Confirme o estado real no código; não confie só no log.

Não leia `.env*`. Respeite: `execute_commands = disabled`, persistências
disjuntas (localStorage vs SQLite), `source_ref` literal.

## 2. Saída (todas as seções, nesta ordem)

1. **Escopo e dependências** — o que entra, o que fica fora, pré-requisitos.
2. **Estado atual da implementação** — o que já existe (com `arquivo:linha`),
   o que o log afirma e o código confirma ou não.
3. **Riscos arquiteturais** — contratos congelados, ADRs afetados, riscos de
   segurança/concorrência/recovery.
4. **Interfaces propostas** — assinaturas, tipos, schemas/migrations (como
   proposta, sem criar nada).
5. **Contraprovas e matriz de testes** — para cada invariante, um teste que
   falharia se ela fosse quebrada; indique testes novos vs. existentes.
6. **Critérios de aceite** — verificáveis; inclua gates (lint, tipos, testes
   direcionados e regressões).
7. **Modelo e esforço recomendados** — padrão: Sonnet com esforço alto.
   Recomende Opus **somente** com justificativa concreta de complexidade
   (ex.: invariantes de concorrência/recovery, mudança de contrato).
8. **Pacote de auditoria para o Codex** — lista de arquivos a revisar,
   invariantes e contraprovas a atacar, comandos de verificação sugeridos,
   pontos de dúvida. Sem veredito.

Termine com "Pendências de aprovação humana" e **pare**. Não inicie a
implementação.
