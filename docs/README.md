# Documentação arquitetural — Freelance Focus

Este diretório contém **somente documentação**. Nenhum código funcional, migration ou
configuração vive aqui.

O produto continua sendo **uma única aplicação** para o usuário. O *AI Dev Workspace* é
uma capacidade nova que se soma ao Freelance Focus, não um produto separado.

## Estado

| Fase | Escopo | Situação |
| --- | --- | --- |
| 1A | Baseline e auditoria arquitetural do repositório real | concluída |
| 1B | Arquitetura oficial da V1 | auditada pelo Codex — veredito **RED** |
| 1B.1 | Correção pós-auditoria (AUD-001…AUD-013) | reauditada — veredito **RED** |
| **1B.3** | **Correção final pós-reauditoria (REAUD-001…REAUD-008)** | **concluída — aguardando reauditoria independente** |
| E2+ | Implementação incremental (ver roadmap) | não iniciada |

Nada em `src/`, `package.json`, CI ou dependências foi alterado em nenhuma dessas fases.

## Como ler

Leia na ordem. Cada documento assume o anterior.

| # | Documento | Responde |
| --- | --- | --- |
| 01 | [Arquitetura V1](architecture/01-v1-architecture.md) | Módulos, o que cada um pode importar, composition root, contrato frontend ↔ backend |
| 02 | [Modelo de dados](architecture/02-data-model.md) | Entidades, invariantes, máquina de estados, `execution_fingerprint`, proveniência de métricas |
| 03 | [Arquitetura de contexto](architecture/03-context-architecture.md) | Registry, hash canônico, *staleness*, manifest, artefato renderizado, analyzer, benchmark |
| 04 | [Segurança e Git runtime](architecture/04-safety-and-git-runtime.md) | Capabilities, `ToolExecutor`, isolamento de path, política de comandos, protocolo de worktree |
| 05 | [Contratos de provider](architecture/05-provider-contracts.md) | Interfaces, perfil de capability, `ExecutionWorkspaceRef`, DTOs |
| 06 | [Fronteiras de API e UI](architecture/06-api-and-ui-boundaries.md) | Segurança da API local, endpoints, modos da aplicação, telas mínimas |
| 07 | [Roadmap oficial da V1](architecture/07-roadmap-v1.md) | Etapas, gates, riscos residuais, decisões adiadas |

## Decisões (ADRs)

Numeração iniciada em 0001 — o repositório não possuía ADRs anteriores.

| ADR | Título | Status |
| --- | --- | --- |
| [0001](adr/0001-local-backend-fastapi-sqlite.md) | Backend local FastAPI + SQLite; frontend permanece na raiz | Aceito · rev. 1B.1, 1B.3 |
| [0002](adr/0002-disjoint-persistence.md) | Persistências disjuntas: localStorage (comercial) e SQLite (workspace) | Aceito |
| [0003](adr/0003-devworkspace-independent-of-project.md) | `DevWorkspace` é entidade independente de `Project` | Aceito |
| [0004](adr/0004-safety-before-agents.md) | Segurança antes de agentes; isolamento por Git worktree | Aceito · rev. 1B.1, 1B.3 |
| [0005](adr/0005-ruflo-optional-adapter.md) | Ruflo é adaptador opcional, nunca dependência | Aceito · rev. 1B.3 |
| [0006](adr/0006-context-registry-selective-context.md) | Context Registry próprio, com contexto seletivo e determinístico | Aceito · rev. 1B.1 |
| [0007](adr/0007-metrics-as-typed-columns.md) | Métricas como colunas tipadas; sem entidade `MetricSample` | Aceito · rev. 1B.1, 1B.3 |
| [0008](adr/0008-workspace-task-state-machine.md) | Máquina de estados enxuta com `phase` e `failure_reason` | Aceito · rev. 1B.1, 1B.3 |
| [0009](adr/0009-provider-capability-enforcement.md) | **Enforcement de capability e execução de ferramentas mediada** — inclui `Developer.execute_commands = disabled` | Aceito (1B.1) · rev. 1B.3 |
| [0010](adr/0010-benchmark-protocol.md) | **Protocolo de benchmark: auditoria de workflow ≠ avaliação comparativa** | Aceito (1B.1) · rev. 1B.3 |

## Invariantes que a arquitetura não pode perder

Verificação rápida — cada item tem documento e ADR de origem.

1. **O Developer não executa comandos arbitrários na V1.** `execute_commands = disabled`,
   sem exceção: sem shell, sem `python -c`, sem `node -e`, sem `npm`, sem `pytest`, sem
   scripts do projeto. [ADR-0009](adr/0009-provider-capability-enforcement.md)
2. **Todo write do Developer é mediado pelo `ToolExecutor`**, por uma superfície fechada
   (`ReadFile`, `ListDirectory`, `SearchText`, `WriteFile`, `ApplyPatch`, `Git*`) — não
   existe `ExecCommand` genérico. [04](architecture/04-safety-and-git-runtime.md) §2
3. **O Test Runner é separado e não implica sandbox.** Executar testes executa código do
   projeto; a mediação protege a autoridade do provider, não confina o projeto.
   [04](architecture/04-safety-and-git-runtime.md) §0 e §6
4. **Provider incapaz de provar o perfil de capability: fail closed** — execução recusada
   antes de subir processo. [ADR-0009](adr/0009-provider-capability-enforcement.md)
5. **A aprovação vincula toda a configuração de execução** via `execution_fingerprint`,
   incluindo bindings de Developer, Auditor e Test Runner.
   [02](architecture/02-data-model.md) §7
6. **O contexto efetivamente entregue tem artefato imutável** endereçado por conteúdo — não
   é reconstruído. [02](architecture/02-data-model.md) §5
7. **`SafetyPolicy` é pura; `PathRuntime` coleta os fatos.** Nada é truncado antes da
   validação pós-abertura. [04](architecture/04-safety-and-git-runtime.md) §4
8. **`Run` finalizado é append-only; nova auditoria = novo `Run`**, encadeado por
   `subject_run_id`/`supersedes_run_id`. [02](architecture/02-data-model.md) §9
9. **Ruflo ausente não quebra a V1**, e o benchmark de dois modos (E12) não depende dele.
   [ADR-0005](adr/0005-ruflo-optional-adapter.md) · [ADR-0010](adr/0010-benchmark-protocol.md)
10. **Segurança precede escrita por agentes**, e commit, merge e push permanecem humanos.
    [ADR-0004](adr/0004-safety-before-agents.md)

Complementares, igualmente firmes: **nenhum LLM participa de uma `SafetyDecision`**;
**`planning_base_commit == manifest.git_head == run.base_commit`**; **`stale` cobre working
tree, não só o commit**; **`files_changed` e `diff_stat` vêm do Git Runtime**; **`archive`
é reversível e `purge` exige prévia e confirmação forte**; **`GET /api/health` é a única
rota não autenticada**.

## Convenções

- **Termos em inglês** para identificadores, enums e nomes de entidade; **texto em
  português** para a prosa, seguindo o padrão do `README.md` e do código.
- Um ADR aceito não é reescrito em silêncio: revisões ficam registradas na seção
  **Revisões** de cada um, e uma decisão nova o **substitui**
  (`Substituído por ADR-XXXX`).
- "V1" significa a primeira versão utilizável do AI Dev Workspace: criar um workspace,
  registrar contexto, planejar uma tarefa, aprovar, executar de forma mediada e isolada
  por Git worktree (**sem sandbox de sistema operacional**), testar, auditar e ver o diff
  — com commit e push permanecendo humanos.
