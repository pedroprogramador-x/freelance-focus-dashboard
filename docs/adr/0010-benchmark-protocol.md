# ADR-0010 — Protocolo de benchmark: auditoria de workflow separada da avaliação comparativa

- **Status:** Aceito
- **Data:** 2026-08-31
- **Fase:** 1B.1 (resposta ao finding **AUD-007**)

## Contexto

A Fase 1B estabeleceu que toda mudança real de código passa por auditoria obrigatória do
Codex, e — para não deixar o modo `claude_only` sem medida de qualidade — mandava rodar o
auditor nesse modo "fora de banda".

A auditoria apontou a contradição. Aquilo misturava duas coisas diferentes:

- o **auditor do fluxo**, que faz parte da orquestração e influencia o resultado;
- o **avaliador do benchmark**, que só mede.

Rodar o auditor em `claude_only` num momento diferente, com contexto diferente e sem
rubrica declarada, produzia uma comparação enviesada — cada modo avaliado em condições
próprias.

## Decisão

### 1. Operação normal

- Todo run que produz **diff não vazio** é auditado (`purpose = workflow_audit`), a partir
  de E9.
- `AuditFinding` `high` com `status = open` **bloqueia `done`**.
- A partir de E10, findings alimentam o laço de correção limitado por `max_fix_rounds`.

### 2. Execução dos modos de benchmark

Cada modo executa até o fim **sem qualquer realimentação de avaliador externo**:

| Modo | O que roda |
| --- | --- |
| `claude_only` | Developer sozinho. **Sem orquestração e sem auditor de workflow.** |
| `orchestrated` | Orquestração completa, auditor de workflow incluído |
| `orchestrated_ruflo` | Igual, com Ruflo nos pontos de extensão |

A diferença entre os modos **é o objeto da medição**. `claude_only` não recebe auditor
porque a ausência de auditoria é parte do que significa não orquestrar.

### 3. Cronologia em duas etapas *(revisado em 1B.3 — REAUD-007)*

Ruflo é opcional; a comparação não pode ficar refém dele. A avaliação acontece em duas
rodadas, sobre **os mesmos casos congelados**:

| Etapa | Executa | Avalia |
| --- | --- | --- |
| **E12 — Baseline Benchmark** | `claude_only` e `orchestrated` sobre casos congelados | Os **dois** modos, com mesmo avaliador, mesma rubrica, artefatos congelados. Já produz comparação utilizável |
| **E13 — Ruflo Experiment** | `orchestrated_ruflo` sobre **os mesmos casos congelados** | Os **três** modos, novamente com mesmo avaliador e mesma rubrica |

Se E13 nunca acontecer, a comparação de dois modos de E12 permanece válida e completa. Os
casos são congelados em E12 e reutilizados em E13 — é isso que torna as rodadas
comparáveis.

### 4. Avaliação comparativa

Em ambas as etapas, depois de os modos terminarem e seus resultados estarem **congelados**:

- **mesmo avaliador**, **mesma rubrica** (`rubric_version` registrada), **mesmo momento**;
- avalia os outputs congelados — diff e resumo de testes — do mesmo `benchmark_group_id`;
- produz `AuditFinding` com `purpose = benchmark_evaluation`, em um `Run` próprio com
  `subject_run_id` apontando para o run avaliado.

Essa avaliação **mede qualidade**; **não** altera resultado de execução, **não** alimenta
correção, **não** oferece feedback durante a execução e **não** bloqueia `done`.

### 5. Modelo de dados — sem entidade nova

- `Run.purpose` ∈ `{execution, workflow_audit, benchmark_evaluation}`;
  `AuditFinding.purpose` ∈ `{workflow_audit, benchmark_evaluation}`.
- `AuditFinding.rubric_version`, obrigatório em `benchmark_evaluation`.
- `WorkspaceTask.benchmark_group_id`, para ligar as execuções do mesmo objetivo.
- `Run.subject_run_id` aponta para o run avaliado; `AuditFinding.run_id` aponta sempre para
  o **run do avaliador**.
- Agregações de custo filtram `Run.purpose`, para que o custo do avaliador não seja
  atribuído a nenhum dos modos comparados.
- **(1B.3)** Dados de um `benchmark_group_id` com avaliação já registrada são **protegidos
  contra purga** ([02](../architecture/02-data-model.md) §11): a prévia sinaliza e a purga
  é recusada. Uma métrica de comparação não pode desaparecer por exclusão acidental.

## Consequências

**Positivas**

- A comparação passa a ser justa: mesmo avaliador, mesma rubrica, mesmo momento, sobre
  saídas congeladas.
- Fica impossível o avaliador de benchmark contaminar o resultado que ele mede.
- Nenhuma entidade nova — três colunas.

**Negativas e mitigações**

- Uma passada de avaliação a mais, com custo próprio. Mitigado por rodar sobre diffs
  congelados, uma vez por grupo, e por ser filtrável nas métricas.
- Exige que os três modos rodem sobre o mesmo objetivo antes de haver comparação. É a
  natureza do experimento.
- A rubrica precisa ser versionada e estável. `rubric_version` obrigatório resolve; mudar
  a rubrica invalida a comparabilidade entre grupos e isso passa a ser visível.

## Alternativas consideradas

| Alternativa | Recusada porque |
| --- | --- |
| Auditor "fora de banda" em `claude_only` (versão 1B) | Avalia cada modo em momento e contexto distintos; enviesa a comparação |
| Nenhuma medida de qualidade em `claude_only` | Restaria "passou nos testes", um piso baixo demais para comparar |
| Dar auditor de workflow a `claude_only` | Deixaria de ser baseline: o modo passaria a ser orquestrado pela metade |
| Entidade `BenchmarkEvaluation` separada | `purpose` em `AuditFinding` e `Run` resolve sem tabela nova |
| **(1B.3)** Esperar os três modos para produzir qualquer comparação | Prenderia todo o benchmark a um componente **opcional**; se Ruflo nunca for adotado, não haveria comparação alguma |
| **(1B.3)** Casos novos em E13 | Tornaria as duas rodadas incomparáveis; os casos precisam ser os mesmos e congelados |

## Revisões

| Fase | Mudança |
| --- | --- |
| 1B.1 | Versão original — separação entre auditoria de workflow e avaliação de benchmark |
| **1B.3** | Cronologia em duas etapas: **E12 baseline de dois modos**, **E13 Ruflo e avaliação de três modos** sobre os mesmos casos congelados (**REAUD-007**); proteção contra purga de dados de benchmark (**REAUD-008**) |

## Referências

[03 — Arquitetura de contexto](../architecture/03-context-architecture.md) §7–§8 ·
[02 — Modelo de dados](../architecture/02-data-model.md) §8–§11 ·
[ADR-0007](0007-metrics-as-typed-columns.md)
