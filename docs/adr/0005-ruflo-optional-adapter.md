# ADR-0005 — Ruflo é adaptador opcional, nunca dependência

- **Status:** Aceito
- **Data:** 2026-08-31
- **Fase:** 1B

## Contexto

Ruflo pode oferecer memória persistente, estado de workflow, roteamento e coordenação de
agentes. Nada disso é indispensável para a V1, e uma dependência dele tornaria o sistema
inoperante na sua ausência.

Nesta fase, Ruflo **não foi instalado, executado nem configurado**, e nenhuma configuração
de MCP foi tocada.

## Decisão

1. **O core nunca importa Ruflo.** Nenhum módulo de `api/app/` tem `import ruflo` fora de
   `agent_runtime/adapters/`.
2. Três interfaces são previstas, **cada uma com implementação nativa obrigatória**:

   | Interface | Implementação nativa da V1 | Papel possível do Ruflo |
   | --- | --- | --- |
   | `MemoryProvider` | `ContextRegistryEntry` + histórico de `Run` em SQLite | memória persistente entre tarefas |
   | `WorkflowStateStore` | `WorkspaceTask.status`/`phase` + `plan` em SQLite | checkpoint e retomada |
   | `Coordinator` | Execution Manager com `max_parallel_agents = 1` | coordenação de agentes em paralelo |

3. **Critério de aceite verificável:** remover Ruflo da máquina e desligar sua
   configuração deixa o sistema **integralmente funcional**. A suíte inclui um teste que
   roda o fluxo completo com toda a configuração de Ruflo ausente.
4. Ruflo aparece no roadmap como **E13, etapa opcional**, **depois** do
   **E12 — Baseline Benchmark** (`claude_only` × `orchestrated`) — para que seu efeito seja
   medido contra uma linha de base já estabelecida, e não adotado por impressão.
   *(Corrigido em 1B.3: a versão anterior dizia E12, o que colidia com o roadmap.)*
5. `execution_mode = orchestrated_ruflo` existe no modelo de dados desde o começo, para que
   a comparação seja possível quando a etapa chegar.
6. **(1B.3)** E13 executa sobre **os mesmos casos congelados de E12**, e só então há
   avaliação dos três modos. Se E13 nunca acontecer, a comparação de dois modos de E12
   permanece válida e completa — o benchmark **não fica refém** de um componente opcional.

## Consequências

**Positivas**

- Nenhum risco de o sistema parar por causa de um componente opcional.
- O valor do Ruflo passa a ser mensurável, não presumido.
- As três interfaces são úteis por si — organizam o core mesmo sem nenhum adaptador.

**Negativas e mitigações**

- Uma camada de indireção que talvez nunca seja usada. Mitigado por serem `Protocol`
  pequenos, com implementação nativa direta, sem fábricas nem registries cerimoniais.
- Risco de acoplamento acidental. Mitigado pelo teste de arquitetura, que falha em qualquer
  importação de Ruflo fora dos adaptadores.

## Alternativas consideradas

| Alternativa | Recusada porque |
| --- | --- |
| Adotar Ruflo como base desde o início | Cria dependência dura em componente não avaliado, antes de existir qualquer medição |
| Ignorar Ruflo por completo | Perderia a hipótese sem testá-la; o custo de prever a interface é baixo |
| Interfaces desenhadas a partir da API do Ruflo | Vazaria o vocabulário dele para o core, tornando a substituição impossível na prática |
| **(1B.3)** Benchmark só produzir resultado com os três modos | Prenderia toda a medição a um componente opcional |

## Revisões

| Fase | Mudança |
| --- | --- |
| 1B | Versão original |
| **1B.3** | Corrigida a referência de etapa: Ruflo é **E13**, depois do baseline de dois modos em E12; acrescentada a regra dos casos congelados compartilhados (**REAUD-007**) |

## Referências

[05 — Contratos de provider](../architecture/05-provider-contracts.md) ·
[07 — Roadmap](../architecture/07-roadmap-v1.md)
