# ADR-0002 — Persistências disjuntas: localStorage (comercial) e SQLite (workspace)

- **Status:** Aceito
- **Data:** 2026-08-31
- **Fase:** 1B

## Contexto

O domínio comercial — `Client`, `Proposal`, `Project`, `ProjectPlanning`, `ProjectTask`,
`FreelanceService`, `RoadmapTask`, `Settings` — vive em `localStorage` sob a chave
`freelance-focus:data:v3`, com validação exaustiva por type guards, migrações v0→v1→v2→v3
e 67 testes cobrindo persistência, migração e integridade referencial.

Esse conjunto funciona e não tem defeito conhecido. Migrá-lo para SQLite junto com a
introdução do AI Dev Workspace acumularia dois riscos grandes numa mesma mudança.

O AI Dev Workspace, por outro lado, precisa de escrita concorrente, séries de execução,
logs e relacionamentos — coisas para as quais um único blob JSON reserializado a cada
mudança de estado é inadequado.

## Decisão

1. **O domínio comercial permanece em `localStorage` durante toda a V1.** Nenhuma entidade
   comercial migra.
2. **O AI Dev Workspace nasce diretamente em SQLite**, servido por `api/`.
3. **As duas fontes permanecem disjuntas.** Nenhuma entidade existe nos dois lados.
4. O backend **não conhece** o domínio comercial: não importa seus tipos, não lê
   `localStorage`, não resolve identificadores comerciais.
5. O único elo é `DevWorkspace.linked_project_id` — uma string opaca, sem FK, resolvida
   apenas pelo frontend ([ADR-0003](0003-devworkspace-independent-of-project.md)).
6. O seed de contexto vindo de `ProjectPlanning` chega como **payload enviado pelo
   frontend**, uma única vez, sem sincronização posterior.

## Consequências

**Positivas**

- Risco zero de regressão no que já funciona: os 67 testes continuam sendo o gate.
- Cada dado no armazenamento adequado à sua natureza.
- A aplicação publicada no GitHub Pages continua 100% funcional sem backend algum.

**Negativas e mitigações**

- Dois formatos de backup. Mitigado por rotulagem explícita (`…-commercial.json` e o dump
  do SQLite) e por documentação.
- Limpar os dados do navegador destrói o comercial e preserva o workspace. É o
  comportamento que já existe hoje; mitigado por aviso e export regular.
- Nenhuma consulta cruzada no servidor. Aceito: o join acontece no frontend, que é o único
  lugar que enxerga os dois mundos.

## Alternativas consideradas

| Alternativa | Recusada porque |
| --- | --- |
| Migrar tudo para SQLite agora | Acumula risco de regressão com risco de feature nova; quebra o funcionamento no Pages |
| Espelhar o comercial em SQLite para consulta | Duas verdades para o mesmo dado, com sincronização a manter |
| Guardar o workspace em `localStorage` | Inadequado para execuções, logs e séries; e inacessível ao processo local que precisa escrever |

## Reabertura

Reabre quando surgir multi-dispositivo, autenticação ou necessidade real de consulta
cruzada no servidor. Caminho previsto: ingestão do backup JSON v3 num espelho
**somente-leitura**, antes de qualquer migração real.

## Referências

[01 — Arquitetura V1](../architecture/01-v1-architecture.md) ·
[02 — Modelo de dados](../architecture/02-data-model.md)
