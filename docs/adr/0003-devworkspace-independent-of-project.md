# ADR-0003 — `DevWorkspace` é entidade independente de `Project`

- **Status:** Aceito
- **Data:** 2026-08-31
- **Fase:** 1B

## Contexto

`Project` foi desenhado como **contrato de execução comercial**. A auditoria da Fase 1A
mostrou que:

- `clientId` é obrigatório e validado em `hasValidEntityReferences` (`src/data/domain.ts`),
  tanto na criação quanto na edição, no carregamento, na migração e na importação;
- `ProjectsPage` impede criar projeto sem cliente ("Cadastre um cliente antes do primeiro
  projeto");
- o schema carrega `amount`, `currency`, `amountReceived`, `paymentStatus`,
  `platformFeePercent`, `exchangeRateToBrl` — todos irrelevantes fora do contexto
  comercial;
- `upsert` **descarta a operação em silêncio** quando a validação referencial reprova.

Projetos pessoais, de faculdade, open-source e experimentos não têm cliente, proposta nem
valor contratado. Encaixá-los em `Project` exigiria inventar um cliente falso, o que
poluiria as métricas de clientes ativos, leads e financeiro.

## Decisão

1. **`DevWorkspace` é entidade nova**, no SQLite, com `type` em
   `personal | freelance | study | experiment | open_source`.
2. **`Project` não é adaptado.** Nenhuma alteração no modelo comercial.
3. `DevWorkspace.linked_project_id` é **anulável** e **opaco**: sem FK, sem validação, sem
   resolução pelo backend.
4. Um link quebrado — projeto excluído no `localStorage` — **não invalida o workspace**. A
   UI mostra "projeto não encontrado" e tudo o mais continua funcionando.
5. `ProjectPlanning` **não é** o Context Registry. Serve como **seed**, importado uma vez;
   depois os dois modelos evoluem independentemente
   ([ADR-0006](0006-context-registry-selective-context.md)).
6. `ProjectTask` permanece comercial. As tarefas do workspace são a entidade distinta
   `WorkspaceTask`.

## Consequências

**Positivas**

- Os cinco tipos de projeto funcionam sem distorcer o domínio comercial nem suas métricas.
- O fluxo `Client → Proposal → Project` permanece intacto, com todas as suas invariantes.
- O backend não precisa conhecer nada do domínio comercial.

**Negativas e mitigações**

- Duas noções de "projeto" no produto. Mitigado por nomes e navegação distintos: *Projetos*
  (comercial) e *Dev Workspaces*.
- Duas noções de "tarefa" (`ProjectTask` e `WorkspaceTask`). O produto já convive com essa
  distinção entre `RoadmapTask` e `ProjectTask`, e o `README.md` já a documenta.
- O join fica no frontend. Aceito: é o único lugar que enxerga os dois mundos.

## Alternativas consideradas

| Alternativa | Recusada porque |
| --- | --- |
| Tornar `Project.clientId` anulável | Quebraria invariantes validadas em cinco pontos e testadas; distorceria métricas comerciais |
| Cliente sintético "Pessoal" | Poluiria clientes ativos, leads e financeiro com dados falsos |
| Estender `Project` com campos de workspace | Mistura contrato comercial com execução técnica numa tabela só |
| FK real entre SQLite e o domínio comercial | Impossível: mundos de persistência distintos ([ADR-0002](0002-disjoint-persistence.md)) |

## Referências

[02 — Modelo de dados](../architecture/02-data-model.md) ·
[03 — Arquitetura de contexto](../architecture/03-context-architecture.md)
