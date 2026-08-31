# ADR-0008 — Máquina de estados enxuta, com `phase` e `failure_reason`

- **Status:** Aceito
- **Data:** 2026-08-31
- **Fase:** 1B

## Contexto

A Fase 1B propôs onze estados para `WorkspaceTask` — `draft`, `planned`,
`awaiting_approval`, `approved`, `running`, `testing`, `auditing`, `needs_fix`, `done`,
`failed`, `cancelled` — e pediu explicitamente que a lista **não** fosse aceita
automaticamente.

O número de transições a implementar e testar cresce com o quadrado do número de estados.
Estados que representam o mesmo fato, ou que são subetapas de um mesmo momento, cobram esse
preço sem entregar informação nova.

## Decisão

**Nove estados**, mais dois campos que carregam o detalhe:

```text
draft · planning · awaiting_approval · approved · executing · needs_fix
terminais: done · failed · cancelled

phase          (só em executing): implementing · testing · auditing
failure_reason (só em failed):    timeout · limit_exceeded · blocked_by_policy ·
                                  capability_unenforceable · provider_error ·
                                  tests_failed · audit_failed · interrupted · internal_error
```

Mudanças em relação à proposta:

| Mudança | Justificativa |
| --- | --- |
| `planned` + `awaiting_approval` → **`awaiting_approval`** | São o mesmo instante: existe plano e falta humano. Dois estados para um fato só |
| `running` + `testing` + `auditing` → **`executing` + `phase`** | Subfases de uma execução. Como estados de topo triplicavam a tabela de transições; a UI mostra `phase` e vê exatamente o mesmo |
| **`planning` acrescentado** | Planejar chama LLM e leva tempo. Sem esse estado, um crash durante o plano deixa a tarefa indistinguível de `draft` e a UI sem nada a mostrar |
| `approved` **mantido** | Genuinamente durável: com `max_parallel_agents = 1`, uma tarefa aprovada espera outra terminar. É o estado de fila |
| `needs_fix` **mantido** | Durável e distinto: rodadas de correção esgotadas, a decisão volta ao humano |
| *timeout* **não vira estado** | É causa, não estado: `failed` + `failure_reason=timeout`. Vale igualmente para limite de token e bloqueio por política |
| *rejeição de plano* **não vira estado** | Rejeitar devolve para `draft` com nota; o usuário edita o objetivo e replaneja. Um terminal `rejected` só acrescentaria um beco sem saída |

### Correção 1B.1 (AUD-011)

**`needs_fix → usuário desiste` vai para `cancelled`, não `failed`.** Desistir é decisão
humana, não falha do sistema; classificá-la como falha envenenaria a taxa de sucesso, que é
uma das métricas do benchmark. Consequência direta: **`failure_reason` existe apenas em
falhas reais** — nunca em `cancelled`, nunca em `done`.

### Regras estruturais

1. `phase` é não nulo **se e somente se** `status = executing`.
2. `failure_reason` é não nulo **se e somente se** `status = failed`.
3. **Estados terminais são imutáveis.** Retomar trabalho significa criar uma nova tarefa,
   opcionalmente clonando objetivo e contexto.
4. **Só o Execution Manager transiciona.** A API expõe comandos (`plan`, `approve`,
   `reject`, `cancel`), nunca escrita direta de `status`.
5. **(1B.1)** `approve` exige o **`execution_fingerprint` completo** — plano, manifest,
   payload renderizado, base commit, adaptador, modelos, agentes, `tool_profile_hash`,
   `safety_policy_hash` e limites. Divergência gera `409`,
   `SafetyEvent(approval_invalidated)` e a indicação de **qual campo** mudou.
6. No start, `reconcile_on_startup()` move tarefas presas em `planning`/`executing` sem
   processo vivo para `failed(interrupted)`, preservando a worktree.

### Guardas *(novo em 1B.1)*

| Transição | Guardas obrigatórias |
| --- | --- |
| `awaiting_approval → approved` | fingerprint recebido == recalculado; nenhuma entrada `stale` selecionada se `risk = high` |
| `approved → executing` | fingerprint **recalculado** ainda válido; slot livre (`max_parallel_agents`); workspace é repositório git; `HEAD == planning_base_commit`; **perfil de capability provado** pelo adaptador — inclusive `execute_commands = disabled`; `attempts < max_attempts` |
| `executing → done` | testes conforme `TestPolicy`; **diff não vazio em operação normal exige uma auditoria vigente** — o run de auditoria bem-sucedido mais recente, não superado por outro run bem-sucedido; reauditoria falha não invalida a vigente anterior; nenhum `AuditFinding` `workflow_audit` `high`/`open` **nessa auditoria vigente**; verificação pós-execução aprovada |
| `needs_fix → approved` | `attempts < max_attempts` e contexto reverificado |
| qualquer → `cancelled` | estado atual não terminal |

`cancelled` e `done` são terminais. Guarda que falha gera `409` com o motivo, e
`SafetyEvent` quando a causa é política.

### Atomicidade e idempotência *(novo em 1B.1)*

| Situação | Regra |
| --- | --- |
| **`cancel` versus conclusão** | Transição por *compare-and-set* em `(id, status, version)`. Quem commitar primeiro vence; o perdedor vira no-op. Se o cancelamento vence após o trabalho terminar, o `Run` preserva o resultado e `result_summary` registra o fato — o dado não se perde e o estado não mente |
| **Criação de `Run`** | **(1B.3)** Idempotente por **`invocation_id` UNIQUE**. Reenviar a mesma requisição reaproveita o `Run` existente; uma tentativa real nova gera `invocation_id` novo e `Run` novo. A constraint anterior `(task_id, agent, attempt_index, fix_round, purpose)` foi **removida**: ela impedia duas auditorias legítimas sobre o mesmo sujeito |
| **Nova auditoria** | **(1B.3)** Sempre um **`Run` novo**, com `subject_run_id` apontando para o run avaliado e, opcionalmente, `supersedes_run_id` apontando para auditoria anterior do mesmo sujeito e propósito. Só um sucessor `ok` a supera; reauditoria falha preserva a vigente. Um `Run` finalizado é append-only e **nunca é reaberto nem tem seus findings sobrescritos** |
| **Recuperação de crash** | `reconcile_on_startup()` idempotente: rodar duas vezes produz o mesmo estado |

## Consequências

**Positivas**

- Menos transições para implementar, testar e manter, com o mesmo poder de expressão.
- `failure_reason` distingue causas que estados separados confundiriam — `timeout` e
  `limit_exceeded` teriam a mesma transição de saída de qualquer forma.
- A UI mostra `phase` e não perde granularidade de progresso.

**Negativas e mitigações**

- Duas dimensões de estado (`status` + `phase`) exigem cuidado. Mitigado pelo invariante
  "`phase` existe se e somente se `executing`", verificado no banco e em teste.
- Filtrar "tarefas em auditoria" passa a ser um filtro composto. Custo trivial.

## Alternativas consideradas

| Alternativa | Recusada porque |
| --- | --- |
| Aceitar os onze estados propostos | Estados redundantes e subfases promovidas a topo, com tabela de transições muito maior |
| Colapsar tudo em `pending`/`running`/`done` | Perderia a fila (`approved`) e a atenção humana (`needs_fix`), ambos duráveis |
| `timeout` como estado próprio | Multiplica estados por cada causa de falha, todos com a mesma saída |
| **(1B.1)** `needs_fix → failed` quando o usuário desiste | Trata decisão humana como falha do sistema e contamina a taxa de sucesso do benchmark |
| **(1B.1)** Acrescentar estados para cobrir as guardas | Guarda é pré-condição de transição, não estado; expressá-la como estado dobraria a máquina sem ganho |

## Revisões

| Fase | Mudança |
| --- | --- |
| 1B | Versão original — nove estados |
| **1B.1** | `needs_fix → cancelled` quando o usuário desiste; `failure_reason` restrito a falhas reais; guardas explícitas por transição; regras de atomicidade e idempotência; aprovação vinculada ao `execution_fingerprint` completo (**AUD-011**, **AUD-003**). **Os nove estados permanecem** |
| **1B.3** | Idempotência de `Run` por `invocation_id` e constraint antiga removida; **nova auditoria = novo `Run`**, encadeado por `subject_run_id`/`supersedes_run_id`, sem sobrescrever findings; guarda de `done` passa a exigir **auditoria vigente** (**REAUD-005**); guarda de entrada passa a exigir perfil de capability provado, inclusive `execute_commands = disabled` (**REAUD-001**). **Os nove estados permanecem** |

## Referências

[02 — Modelo de dados](../architecture/02-data-model.md) §4, §7–§9
