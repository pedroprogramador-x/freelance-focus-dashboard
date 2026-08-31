# 05 — Contratos de provider

> Escopo: interfaces abstratas, perfis de capability, ferramentas mediadas, DTOs,
> composition root e extensões opcionais. Decisões relacionadas:
> [ADR-0005](../adr/0005-ruflo-optional-adapter.md),
> [ADR-0009](../adr/0009-provider-capability-enforcement.md).
>
> **Revisado na Fase 1B.3** (REAUD-001, REAUD-002, REAUD-006).

Objetivo: **trocar CLI por API, ou trocar de fornecedor, sem tocar no Orchestrator — e sem
que nenhum deles receba autoridade de execução de comandos.**

---

## 1. As interfaces

`typing.Protocol` em `agent_runtime/`. Nenhuma menciona Claude, Codex ou Ruflo.

```text
DeveloperProvider.capability_profile()             -> ProviderCapabilityProfile
DeveloperProvider.run(DeveloperExecutionRequest,
                      MediatedTools)               -> AgentRunResult

AuditorProvider.capability_profile()               -> ProviderCapabilityProfile
AuditorProvider.audit(AuditRequest)                -> AuditResult

TestRunner.run(TestRequest)                        -> TestSummary

ToolExecutorFactory.create(...)                    -> ToolExecutor
ToolExecutor.execute(ToolRequest)                  -> ToolResult
ToolExecutor.usage()                               -> MediatedUsage
```

Todos são canceláveis por token de cancelamento e executados em background pelo Execution
Manager.

## 2. Composition root e ciclo de vida *(REAUD-006)*

`main.py` é o **único** lugar que conhece implementações concretas. No startup ele lê
`config.py` e injeta **interfaces** no Execution Manager:

| Escopo | Objeto |
| --- | --- |
| **Vida da aplicação** | `SafetyPolicy` base, `ToolExecutorFactory`, `DeveloperProvider`, `AuditorProvider`, `TestRunner` |
| **Vida do run** | `ToolExecutor`, criado pela factory |

O composition root **não cria um `ToolExecutor` global**: o executor depende de
`ExecutionWorkspaceRef`, política composta, perfil de capability efetivo e metadados de
task/run — todos só conhecidos em tempo de execução.

```text
início de um run:
  tool_executor = factory.create(workspace_ref, composed_policy,
                                 effective_capability_profile, task/run metadata)
  provider.run(request, mediated_tools=tool_executor.mediated_tools())
fim do run:
  executor descartado; medições consolidadas no Run
```

Um executor **nunca atravessa runs** — nenhum run herda autoridade ou contagem de outro.

O Orchestrator não faz lookup, não consulta registry, não conhece nome de fornecedor e não
importa adaptador concreto — proibição verificada pelo teste de arquitetura
([01](01-v1-architecture.md) §3).

## 3. Perfis de capability *(REAUD-001)*

| Capability | **Developer** | **Auditor** |
| --- | --- | --- |
| `read_files` | `mediated` | `disabled` |
| `write_files` | `mediated` | `disabled` |
| `execute_commands` | **`disabled`** | `disabled` |
| `git_read` | `fixed_operations_only` | `disabled` |
| `git_write` | `disabled` | `disabled` |
| `network` | `disabled` | `disabled` |
| `external_paths` | `disabled` | `disabled` |

> **`DeveloperProvider.execute_commands = disabled`, sem exceção na V1.** Sem shell, sem
> terminal, sem `python -c`, sem `node -e`, sem `npm`, sem `pytest`, sem scripts do
> projeto, sem `subprocess`. Mediar execução de comando seria mediar o `argv`, e argv
> nenhum descreve o que o processo fará depois. Ver
> [04](04-safety-and-git-runtime.md) §1 e [ADR-0009](../adr/0009-provider-capability-enforcement.md).

O **Auditor não recebe capability alguma**: diff, critérios de aceite e resumo de testes
chegam no próprio request. É uma função pura sobre o payload.

Cada adaptador declara e **prova** `supported_capabilities`, `effective_capabilities`,
`enforcement_method` e `enforcement_evidence`. Perfil não provável ⇒ execução recusada
antes de subir processo (*fail closed*).

## 4. `MediatedTools` — a superfície do Developer

O Developer recebe uma superfície **fechada e tipada**, com escopo do run:

| Operação | Capability |
| --- | --- |
| `ReadFile`, `ListDirectory`, `SearchText` | `read_files` |
| `WriteFile`, `ApplyPatch` | `write_files` |
| `GitStatus`, `GitDiff`, `GitShow`, `GitListTree` | `git_read` (argv formado pelo runtime) |

**Não existe `ExecCommand` genérico.** O provider nomeia a operação e passa parâmetros
tipados; nunca fornece linha de comando crua. Toda negação volta como negação explícita —
não há caminho alternativo.

## 5. `ExecutionWorkspaceRef`

```text
ExecutionWorkspaceRef = {
  kind: "local_worktree" | "remote",     # a V1 implementa apenas local_worktree
  id,
  base_commit,
  capabilities_hint
}
```

O contrato **não expõe caminho de filesystem**. `Run.worktree_path` existe no banco apenas
como diagnóstico local.

## 6. DTOs mínimos

### `DeveloperExecutionRequest`

| Campo | Notas |
| --- | --- |
| `task_id`, `run_id`, `invocation_id` | correlação e idempotência |
| `goal` | pedido em linguagem natural |
| `acceptance_criteria[]` | do Execution Planner; é o que o auditor confere |
| `plan_steps[]` | passos aprovados |
| `rendered_context` | payload **já renderizado e já redigido** |
| `rendered_context_hash`, `context_manifest_id` | ponteiros para o artefato imutável e o manifest |
| `workspace_ref` | `ExecutionWorkspaceRef` |
| `capability_profile` | perfil efetivo exigido |
| `limits` | `{max_tokens, timeout_s}` |
| `cancel_token` | |

As ferramentas chegam **à parte**, como `MediatedTools` com escopo do run — não como um
executor global embutido no request.

### `AgentRunResult`

| Campo | Notas |
| --- | --- |
| `status` | `ok` · `error` · `timeout` · `cancelled` · `blocked` |
| `provider`, `provider_adapter`, `adapter_version`, `model`, `transport` | identificação |
| `input_tokens`, `output_tokens` | podem ser nulos |
| `token_source` | `reported` · `estimated` · `unavailable` |
| `duration_ms` | |
| `files_read` | **nullable**; `null` quando indisponível — **nunca `[]` para ausência** |
| `files_read_source` | `reported` · `inferred` · `unavailable` |
| `summary` | curto, redigido — **resultado, não raciocínio** |
| `error_summary` | redigido |
| `log_ref` | log completo já redigido, em `data_dir` |

`files_changed`, `diff_added` e `diff_removed` **não estão no contrato**: são derivados
pelo Git Runtime a partir do diff da worktree contra `base_commit`. Um provider não é fonte
confiável para medir a própria mudança. Como toda leitura do Developer é mediada,
`files_read` vem do `ToolExecutor` com `files_read_source = reported`.

**Nunca contém** *chain-of-thought*, blocos de *thinking*, prompt completo ou trecho
marcado pelo redator.

### `AuditRequest` / `AuditResult`

`AuditRequest`: `goal`, `acceptance_criteria[]`, `unified_diff`, `test_summary`,
`context_manifest_id`, `subject_run_id`, `purpose`
(`workflow_audit` · `benchmark_evaluation`), `rubric_version`, `limits`, `cancel_token`.

O auditor recebe **o diff, não o repositório** — é isso que mantém o custo proporcional ao
tamanho da mudança.

`AuditResult`: `verdict` (`pass` · `pass_with_findings` · `fail`), `findings[]`
(`severity`, `category`, `file`, `line`, `summary`, `detail`), mais os campos de uso e
duração do `AgentRunResult`.

### `TestRequest` / `TestSummary`

`TestRequest`: `workspace_ref`, `test_policy` (executável, `argv` normalizado, `cwd`,
`timeout`, allowlist de ambiente, política de rede declarada, limites de saída),
`cancel_token`.

**O comando nunca é string de shell livre.** É representado de forma normalizada:

```text
{ executable: "npm", argv: ["test", "--", "--run"] }
```

`command_hash` deriva dessa representação e, junto com `policy_hash`, forma o
`test_binding` do `execution_fingerprint` ([02](02-data-model.md) §7).

`TestSummary`: `framework`, `exit_code`, `passed`, `failed`, `skipped`, `duration_ms`,
`output_ref`.

O Test Runner é **infraestrutura do sistema, não ferramenta do Developer**
([04](04-safety-and-git-runtime.md) §6). Executar testes executa código do projeto: sem
isolamento de SO, isso permanece risco residual e **não** é confinamento.

## 7. Papéis

| Papel | Agente | Faz | Não faz |
| --- | --- | --- | --- |
| **Implementador** | Claude | Escreve código pela superfície mediada, seguindo o plano aprovado | Não executa comandos, não roda testes, não commita, não faz push, não decide segurança, não escolhe o próprio contexto |
| **Auditor independente** | Codex | Revisa o diff contra critérios de aceite e resumo de testes | Não edita código, não corrige, não toca o filesystem |
| **Consultivo** | Architect / Researcher | Propõem entradas de contexto (`origin = generated`) | Nunca escrevem código |

A independência do auditor é a razão de serem fornecedores diferentes: um revisor que é o
mesmo modelo que escreveu tende a validar as próprias premissas.

### CLI hoje, API amanhã

Irrelevante para o Orchestrator: `transport`, `provider_adapter` e `adapter_version` são
detalhes do adaptador; `workspace_ref` não pressupõe filesystem local; tokens nulos vêm com
`token_source`; ambos recebem `rendered_context` pronto; **e ambos precisam provar o perfil
de capability**, inclusive `execute_commands = disabled`.

Um adaptador que **não consiga desligar** a própria ferramenta de shell é inutilizável como
Developer na V1.

## 8. Extensões opcionais

Interfaces previstas, **com implementação nativa obrigatória**. O core nunca importa Ruflo.

| Interface | Implementação nativa da V1 | Papel possível do Ruflo |
| --- | --- | --- |
| `MemoryProvider` | `ContextRegistryEntry` + histórico de `Run` em SQLite | memória persistente entre tarefas |
| `WorkflowStateStore` | `WorkspaceTask.status`/`phase` + `plan` em SQLite | checkpoint e retomada |
| `Coordinator` | Execution Manager com `max_parallel_agents = 1` | coordenação em paralelo |

**Critério de aceite, verificável por teste:** remover Ruflo da máquina e desligar sua
configuração deixa o sistema **integralmente funcional**. Nenhum módulo tem `import ruflo`
fora de `agent_runtime/adapters/`; o teste de arquitetura verifica.
