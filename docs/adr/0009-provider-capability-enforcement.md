# ADR-0009 — Enforcement de capability de provider e execução de ferramentas mediada

- **Status:** Aceito
- **Data:** 2026-08-31 · **Revisado na Fase 1B.3**
- **Fase:** 1B.1 (finding **AUD-001, P0**), revisto em 1B.3 (finding **REAUD-001, P0**)

## Contexto

A documentação da Fase 1B afirmava que "toda operação de arquivo passa pelo Safety Gate".

A auditoria mostrou que a afirmação é **falsa**. Providers modernos — Claude CLI, Codex
CLI, APIs com *tool use* — trazem ferramentas próprias de shell, filesystem, edição,
escrita e execução de processo. Essas ferramentas rodam **dentro do provider**. Se o
runtime apenas entrega um prompt e lê a resposta, ele não vê, não media e não pode negar
nada do que aconteceu no meio.

Sem correção, todo o desenho de segurança da V1 descrevia um controle que não existia.

## Decisão

### 1. Princípio

> **Providers não recebem autoridade irrestrita.**

Toda capability sensível deve estar, em cada execução, **desabilitada no provider** ou
**mediada pelo runtime** — e, quando tecnicamente possível, as duas coisas.

### 2. `ProviderCapabilityProfile`

Capabilities: `read_files`, `write_files`, `execute_commands`, `git_read`, `git_write`,
`network`, `external_paths`.

Modos de enforcement: `disabled`, `mediated`, **`fixed_operations_only`** *(1B.3)*,
`unmediated`.

`fixed_operations_only` significa: apenas operações nomeadas, com **`argv` formado pelo
runtime**. O provider nomeia a operação e passa parâmetros tipados; nunca fornece linha de
comando.

### 2b. Perfis oficiais *(revisado em 1B.3 — REAUD-001)*

| Capability | **Developer** | **Auditor** |
| --- | --- | --- |
| `read_files` | `mediated` | `disabled` |
| `write_files` | `mediated` | `disabled` |
| `execute_commands` | **`disabled`** | `disabled` |
| `git_read` | `fixed_operations_only` | `disabled` |
| `git_write` | `disabled` | `disabled` |
| `network` | `disabled` | `disabled` |
| `external_paths` | `disabled` | `disabled` |

> **`DeveloperProvider.execute_commands = disabled`. Sem exceção na V1.**

A versão 1B.1 admitia `execute_commands = mediated`. A reauditoria mostrou que isso é um
**trampolim para autoridade transitiva**: mediar `python -c "…"`, `node -e "…"`,
`npm run <script>` ou qualquer script do projeto valida o **`argv`**, e argv nenhum
descreve o que o processo fará depois. A mediação viraria teatro.

O Developer **não possui**: shell, terminal, exec arbitrário, `python -c`, `node -e`,
`npm`, `pytest`, scripts do projeto ou `subprocess` de qualquer natureza.

O **Auditor não recebe capability alguma**: diff, critérios de aceite e resumo de testes
chegam no próprio request. É uma função pura sobre o payload — mais fácil de confiar e mais
barata de operar.

### 3. Declaração acompanhada de prova

Cada adaptador declara `supported_capabilities`, `effective_capabilities`,
`enforcement_method` (`cli_flag` · `config_file` · `tool_allowlist` · `api_tool_schema` ·
`process_env` · `not_enforceable`) e `enforcement_evidence` — a configuração concretamente
aplicada e, onde viável, uma sonda de preflight.

**Declaração não é prova.** Onde o adaptador só consegue declarar, a permissão efetiva não
é concedida.

### 4. Fail closed

Perfil exigido que o adaptador não consegue provar → **execução recusada antes de subir
qualquer processo**, `SafetyEvent(capability_unenforceable)`,
`failed(capability_unenforceable)`.

### 5. `ToolExecutor` e a superfície fechada

Componente novo, em `api/app/tool_executor/`. É o **único** que produz efeito colateral em
nome de um agente:

```text
Provider (raciocínio) → ToolRequest tipado → ToolExecutor → PathRuntime (fatos)
                                                          → SafetyPolicy (decisão)
                                                          → Filesystem · Git (read)
```

**(1B.3) Operações disponíveis ao Developer — lista fechada:**

| Operação | Capability |
| --- | --- |
| `ReadFile`, `ListDirectory`, `SearchText` | `read_files` |
| `WriteFile`, `ApplyPatch` | `write_files` |
| `GitStatus`, `GitDiff`, `GitShow`, `GitListTree` | `git_read` — argv formado pelo runtime |

**Não existe `ExecCommand` genérico exposto ao Developer.** Leitura de git é representada
por operações específicas, não por shell genérico.

**(1B.3) Ciclo de vida:** o composition root injeta uma **`ToolExecutorFactory`** (vida da
aplicação); o `ToolExecutor` é criado **por run** e vinculado a `ExecutionWorkspaceRef`,
política composta, perfil efetivo e metadados de task/run. Nenhum executor atravessa runs.

Toda negação volta ao provider como negação explícita — não há caminho alternativo. A
medição do `ToolExecutor` é a fonte autoritativa de `files_read`; como toda leitura do
Developer é mediada, `files_read_source = reported` passa a ser a regra.

### 5b. Test Runner é separado *(1B.3)*

**Testes não são ferramentas do Developer.** O Developer não pode invocá-los, direta nem
indiretamente. O Execution Manager chama o Test Runner depois que o Developer termina, e o
Test Runner executa apenas segundo a `TestPolicy` configurada pelo sistema.

> A mediação de ferramentas protege a autoridade do **provider**. Ela **não** transforma o
> código do projeto executado pelo Test Runner em código confinado. Isso permanece risco
> residual aceito e **não é sandbox**.

### 6. Neutralidade

Nenhuma suposição de que Claude CLI, Codex CLI e APIs tenham os mesmos controles. Cada
adaptador prova a própria capacidade. O contrato funciona igual para CLI hoje e API amanhã.

### 7. Vínculo com a aprovação

`tool_profile_hash` entra no `execution_fingerprint`: mudar o perfil de capability
invalida a aprovação anterior.

## Consequências

**Positivas**

- A documentação passa a descrever um controle que existe.
- Providers com ferramentas internas não mediáveis são **recusados** para escrita, em vez
  de silenciosamente confiados.
- `files_read` ganha proveniência confiável quando mediado.
- A separação raciocínio/efeito vale para qualquer transporte.

**Negativas e mitigações**

- Um módulo a mais. Justificado: é o P0 da auditoria, e sem ele o gate é decorativo.
- Mediação tem custo — cada operação vira uma ida e volta. Aceito: o volume é de dezenas de
  operações por run.
- Alguns providers podem não suportar o perfil exigido e ficar inutilizáveis como
  Developer. É o comportamento correto: preferir recusar a fingir enforcement.

## Limitação residual

Mediação cobre o que passa pelo `ToolExecutor`. Com `execute_commands = disabled`, o
Developer não tem como escapar dela. **Resta o Test Runner**, que executa código do projeto
e não é confinado à worktree — isso exige isolamento de sistema operacional, ausente na V1
e declarado em [04](../architecture/04-safety-and-git-runtime.md) §0 e §6, com estudo em
E14.

**Custo aceito:** um Developer sem execução de comandos pode não concluir certas tarefas.
É risco registrado (R14 no [roadmap](../architecture/07-roadmap-v1.md)) e medido em E8.
Preferimos um agente limitado a um agente com autoridade que não sabemos conter.

## Reabertura

Permitir shell, `python`, `node`, `npm` ou comandos arbitrários ao Developer **exige
reabrir esta decisão de segurança**.

**Gatilho:** isolamento de processo/SO comprovadamente forte — contêiner, VM, *job
object*/isolamento do Windows mais forte, ou outro mecanismo demonstrado. Avaliado em E14,
com a evidência de E8 sobre o quanto a limitação realmente custou. **Fora da V1.**

## Alternativas consideradas

| Alternativa | Recusada porque |
| --- | --- |
| Confiar na configuração do provider | É exatamente o que a auditoria reprovou: confiança sem verificação |
| Só detectar depois (post-hoc diff) | Detecta na árvore principal, não previne, e não vê escrita em outros lugares |
| Bloquear qualquer provider com ferramentas internas | Eliminaria os providers reais; a mediação com fail-closed é proporcional |
| Colocar o `ToolExecutor` dentro de `agent_runtime/` | Criaria dependência circular e confundiria "quem decide o efeito" com "quem conversa com o provider" |
| **(1B.3)** `execute_commands = mediated` para o Developer | Mediar o `argv` não media o que o processo faz depois: `python -c` e `npm run` dão efeito arbitrário. Autoridade transitiva |
| **(1B.3)** Allowlist de comandos "seguros" para o Developer | Qualquer runtime da allowlist (`node`, `python`) executa código arbitrário por definição; a allowlist daria falsa garantia |
| **(1B.3)** Deixar o Developer chamar o Test Runner | Reintroduziria execução por via indireta e apagaria a fronteira entre agente e infraestrutura |
| **(1B.3)** `ToolExecutor` global no composition root | O executor depende de dados que só existem em tempo de execução; um executor global faria um run herdar autoridade de outro |

## Revisões

| Fase | Mudança |
| --- | --- |
| 1B.1 | Versão original — capabilities, prova de enforcement, fail closed, `ToolExecutor` |
| **1B.3** | **`Developer.execute_commands = disabled`, sem exceção** (**REAUD-001, P0**); modo `fixed_operations_only`; superfície fechada de operações; Auditor sem capability; Test Runner separado; `ToolExecutorFactory` com escopo de run (**REAUD-006**); gatilho de reabertura registrado |

## Referências

[04 — Segurança e Git runtime](../architecture/04-safety-and-git-runtime.md) §1–§2 ·
[05 — Contratos de provider](../architecture/05-provider-contracts.md) ·
[01 — Arquitetura V1](../architecture/01-v1-architecture.md) §2
