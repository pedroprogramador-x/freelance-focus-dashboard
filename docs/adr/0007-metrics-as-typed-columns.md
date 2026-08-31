# ADR-0007 — Métricas como colunas tipadas; sem entidade `MetricSample` na V1

- **Status:** Aceito
- **Data:** 2026-08-31
- **Fase:** 1B

## Contexto

O modelo conceitual da Fase 1A previa uma entidade `MetricSample` genérica
(`key`, `value`, `unit`, `mode`) para registrar métricas de execução. A Fase 1B pediu
avaliação crítica dessa escolha.

As métricas necessárias são: tokens de entrada e saída, duração, agente, provider, modelo,
arquivos lidos, arquivos alterados, resultado de teste, tentativas, findings de auditoria e
resultado da tarefa. O objetivo final é comparar `claude_only`, `orchestrated` e
`orchestrated_ruflo`.

## Decisão

1. **`MetricSample` não existe na V1.**
2. As métricas por execução são **colunas tipadas de `Run`**: `input_tokens`,
   `output_tokens`, `token_source`, `duration_ms`, `agent`, `provider`, `model`,
   `transport`, `files_read`, `files_changed`, `diff_added`, `diff_removed`,
   `test_summary`, `status`.
3. As métricas por tarefa que são **estado real** ficam em `WorkspaceTask`: `attempts`,
   `fix_rounds`, `status`, `failure_reason`.
4. **Nenhum rollup denormalizado.** Totais de token e duração por tarefa são calculados
   por agregação SQL sobre os runs. Em dezenas ou centenas de runs, o custo é irrelevante e
   não há risco de o cache divergir do fato.
5. **A dimensão do benchmark é `WorkspaceTask.execution_mode`**, com valores
   `claude_only`, `orchestrated`, `orchestrated_ruflo`. A comparação é um `GROUP BY` sobre
   `Run ⋈ WorkspaceTask`.
6. **`token_source`** (`reported` · `estimated` · `unavailable`) acompanha os tokens, para
   que um transporte que não reporta uso não seja registrado como zero.
7. `metrics.py` é um **módulo simples de leitura**, não um pacote — só agregação.
8. **(1B.1) Proveniência por métrica, onde ela significa algo** *(AUD-008)*:

   | Métrica | Fonte | Regra |
   | --- | --- | --- |
   | `files_read` | `ToolExecutor` quando mediado; provider quando reportado por contrato | **Nullable.** `[]` significa *medido, e nada foi lido*. Indisponível é **`null`** — **nunca `[]`** |
   | `files_read_source` | — | `reported` (ToolExecutor mediado ou API contratual) · `inferred` (log/heurística) · `unavailable` |
   | `files_changed` | **Git Runtime, sempre** | Derivado do diff da worktree contra `base_commit`. **Nunca vem do provider** — não há campo de proveniência porque só existe uma fonte |
   | `diff_added` / `diff_removed` | **Git Runtime, sempre** | Idem |

   Não há campo genérico `usage_quality`.
9. **(1B.1)** `Run.purpose` e `WorkspaceTask.benchmark_group_id` sustentam o protocolo de
   benchmark ([ADR-0010](0010-benchmark-protocol.md)); agregações de custo filtram
   `purpose` para não atribuir o custo do avaliador aos modos comparados.
10. **(1B.3)** A chave de idempotência de `Run` passa a ser **`invocation_id` UNIQUE**; a
    constraint `(task_id, agent, attempt_index, fix_round, purpose)` foi removida por
    impedir reauditoria legítima. `attempt_index` e `fix_round` seguem como metadados
    informativos — úteis para agrupar métricas, sem restringir nada.
11. **(1B.3)** Dados de benchmark são **protegidos contra purga**: um `benchmark_group_id`
    com avaliação registrada não pode ser removido por exclusão acidental
    ([02](../architecture/02-data-model.md) §11). Métrica que some não é métrica.
12. **(1B.3)** Com `execute_commands = disabled` e `read_files = mediated`, **toda leitura
    do Developer é mediada**: `files_read_source = reported` passa a ser a regra, não a
    exceção.

## Consequências

**Positivas**

- Tipagem preservada; `NOT NULL` e checagens continuam verificáveis pelo banco.
- Consultas de benchmark sem `JOIN` e `GROUP BY` extras sobre uma tabela chave/valor.
- Nenhuma métrica silenciosamente falsa: `token_source` distingue medido de indisponível, e
  **(1B.1)** `files_read_source` faz o mesmo para telemetria de leitura.
- **(1B.1)** As duas métricas que o Git pode provar — arquivos alterados e tamanho do diff
  — deixam de depender da palavra do provider sobre o próprio trabalho.
- Um módulo a menos na estrutura do backend.

**Negativas e mitigações**

- Acrescentar métrica nova exige migração de coluna. Com Alembic e SQLite local, isso é
  barato — e a disciplina de nomear cada métrica é um benefício, não um custo.
- Métrica esparsa não tem onde morar. É exatamente o gatilho de reabertura.

## Alternativas consideradas

| Alternativa | Recusada porque |
| --- | --- |
| `MetricSample` genérica (EAV) | Perde tipagem, acrescenta join em toda consulta, e nenhum invariante fica verificável pelo banco — sem nenhum ganho nesta escala |
| Rollups denormalizados em `WorkspaceTask` | Cache que diverge do fato, sem ganho perceptível de desempenho |
| Métricas só em arquivo de log | Impossível agregar e comparar sem pipeline de ingestão |
| **(1B.1)** `files_read = []` para telemetria ausente | Confunde "não leu nada" com "não sei", e o erro contamina toda média |
| **(1B.1)** `files_changed` reportado pelo provider | Um agente não é fonte confiável para medir a própria mudança quando o Git pode prová-la |
| **(1B.1)** Campo genérico `usage_quality` | Proveniência genérica não diz *de que* fonte se duvida; declarar por métrica é mais informativo e mais barato |

## Revisões

| Fase | Mudança |
| --- | --- |
| 1B | Versão original |
| **1B.1** | Acrescentados os itens 8 e 9 — proveniência por métrica e suporte ao protocolo de benchmark (**AUD-008**). A decisão central, sem `MetricSample`, permanece |
| **1B.3** | Itens 10–12: chave de idempotência `invocation_id` e remoção da constraint antiga (**REAUD-005**); proteção de dados de benchmark contra purga (**REAUD-008**); leitura mediada como regra (**REAUD-001**). A decisão central permanece |

## Reabertura

Quando aparecer métrica **esparsa e irregular** — presente em alguns runs e não em outros,
com chave desconhecida em tempo de projeto. Nesse caso `MetricSample` entra **ao lado** das
colunas, para o irregular, sem substituir o que já é fixo.

## Referências

[02 — Modelo de dados](../architecture/02-data-model.md) §8–§10 ·
[07 — Roadmap](../architecture/07-roadmap-v1.md)
