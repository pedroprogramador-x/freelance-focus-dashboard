# ADR-0006 — Context Registry próprio, com contexto seletivo e determinístico

- **Status:** Aceito
- **Data:** 2026-08-31
- **Fase:** 1B

## Contexto

`ProjectPlanning` já registra problema, objetivo, requisitos funcionais e não funcionais,
stack, arquitetura, decisões técnicas e riscos, com editor pronto e proteção de alterações
não salvas. É o candidato natural a virar contexto de IA.

Mas ele pertence ao domínio comercial em `localStorage`, é único por projeto, e não tem
como saber que o código mudou. Além disso, mandar o repositório inteiro para um agente é
caro e piora o resultado: contexto irrelevante compete com contexto relevante.

## Decisão

1. **O Context Registry é próprio**, no SQLite, com `ContextRegistryEntry`.
   `ProjectPlanning` **não** é o registry.
2. **`ProjectPlanning` é seed**: importado uma vez, via payload enviado pelo frontend, e
   depois os dois modelos evoluem independentemente. **Não há sincronização
   bidirecional** — ela reintroduziria o acoplamento que
   [ADR-0002](0002-disjoint-persistence.md) elimina.
3. **Domínios:** `objective`, `architecture`, `stack`, `requirements`, `modules`,
   `decisions`, `risks`, `contracts`.
4. **`file_map` não é entrada** — é artefato derivado do repositório, gerado sob demanda e
   cacheado por `(workspace_id, git_head)`. Guardá-lo como entrada editável criaria uma
   cópia que envelhece a cada commit e que poderia ser editada para algo falso.
5. **`body` é o conteúdo autoral canônico.** `structured` é metadado tipado para UI e
   filtro, nunca fonte única — evita duas verdades com um renderizador no meio. O que o
   agente recebe é o **payload renderizado** a partir dele (item 9), não a linha do banco.
6. **`stale` é detectado sem embeddings, por verificação dupla** contra o
   `planning_base_commit` congelado:
   - **committed** — `source_hash` = sha256 sobre a lista **congelada e ordenada**
     `(path, blob_sha)` dos arquivos resolvidos pelos globs, com os `blob_sha` vindos de
     `git ls-tree` no base commit;
   - **working tree** — `git status --porcelain=v2 -z --untracked-files=all` detecta
     `modified`, `staged`, `deleted`, `renamed` e `untracked`.

   Estados: sem `source_refs` → `fresh`; hash confere **e** nenhum path coberto diverge →
   `fresh`; hash difere → `stale(sources_changed)`; path coberto diverge no working tree →
   `stale(working_tree)`; refs irresolvíveis ou sem git → `unknown`.

   > **Correção 1B.1 (AUD-004).** `git ls-tree` sozinho compara apenas o estado commitado
   > e produzia **falso `fresh`**: um arquivo modificado e não commitado não aparece na
   > árvore do commit.

   **Regra operacional:** a execução usa **exclusivamente o base commit congelado**.
   Alterações não commitadas não entram automaticamente. A divergência é registrada no
   manifest (`working_tree_divergence`) e mostrada antes da aprovação — nunca escondida.
   Não bloqueia por si só, exceto pela regra do item 8.

   **`content_hash` tem uma única fórmula canônica**, normativa em
   [03](../architecture/03-context-architecture.md) §2:
   `sha256(canonical_json({v, domain, title, body, structured}))`, com normalização NFC,
   CRLF→LF e espaços finais removidos. Entram `domain`, `title`, `body` e `structured`; não
   entram `id`, `tags`, `source_refs`, `state`, `source_hash`, `origin` e timestamps.
   Nenhum outro documento redefine a fórmula.
7. **A seleção de contexto é determinística.** Pontuação por sobreposição de
   `source_refs`, domínio afetado, tags, proximidade no file map, frescor e recência.
   Re-rank por LLM é opcional, desligado por padrão, e só reordena candidatos — nunca
   acrescenta nem contorna exclusão de política.
8. **Uma tarefa de `risk = high` não executa com entrada `stale` selecionada.** Regra dura,
   não sugestão.
9. **O contexto entregue é congelado em três camadas distintas** *(correção 1B.1,
   AUD-002)*:
   - **Context Selection** — transitória: candidatos e pontuação;
   - **`ContextManifest`** — linha persistida e imutável: fontes selecionadas, base commit,
     lista congelada de arquivos, divergência do working tree, exclusões;
   - **Rendered Context Artifact** — blob imutável **endereçado por conteúdo**
     (`data_dir/artifacts/<sha256>.json`) com **o payload exato entregue**: blocos na ordem
     emitida, origem, truncamentos, transformações, tamanhos e `renderer_version`.

   O manifest sozinho não provava o que foi entregue: truncamento, ordem e versão do
   renderizador ficavam de fora, e apagar uma entrada tornava o passado irreconstituível.
   O artefato é um **snapshot**, então sobrevive à edição e à exclusão da entrada de
   origem. A redação de segredos é aplicada **antes** do hash;
   `rendered_context_hash` entra no `execution_fingerprint`.

   Segredos nunca entram em nenhuma das três camadas — aparecem apenas como caminho em
   `excluded`, com `reason = secret_policy`.

## Consequências

**Positivas**

- Responde com precisão "qual conhecimento o Developer recebeu quando implementou isto?" —
  **e agora prova o payload**, não apenas a lista de fontes.
- Custo de contexto controlado por orçamento explícito, com o descartado registrado.
- Nenhuma infraestrutura de embeddings, índice vetorial ou serviço externo.
- O editor de planejamento existente é reaproveitado quase integralmente.

**Negativas e mitigações**

- `source_hash` detecta *que* mudou, não *se a mudança importa*. Um commit de formatação
  marca a entrada como `stale`. Aceito: falso positivo custa uma revisão; falso negativo
  custa uma decisão errada.
- **(1B.1)** A verificação do working tree torna `stale` mais frequente em repositórios com
  trabalho em andamento. É o comportamento correto: antes, esses casos apareciam como
  `fresh` — um falso negativo, exatamente o tipo caro.
- **(1B.1)** O artefato renderizado ocupa espaço em disco. Mitigado pelo endereçamento por
  conteúdo: manifests idênticos compartilham um único arquivo.
- Contexto precisa ser mantido à mão. Mitigado pelo seed, pelos agentes consultivos
  (`architect`, `researcher`) que propõem entradas, e por `verify` sob demanda.
- Duplicação conceitual entre planejamento comercial e contexto técnico. Aceito como
  consequência direta da persistência disjunta.

## Alternativas consideradas

| Alternativa | Recusada porque |
| --- | --- |
| Usar `ProjectPlanning` como registry | Vive no `localStorage`, é único por projeto, não conhece o código e acoplaria os dois mundos |
| Sincronizar planejamento ↔ contexto | Duas verdades bidirecionais; reintroduz o acoplamento que a arquitetura elimina |
| Embeddings + busca semântica | Infraestrutura, custo e não determinismo, para um ganho não demonstrado nesta escala |
| Mandar o repositório inteiro | Caro, e contexto irrelevante compete com o relevante |
| Tudo como key/value genérico | Perde estrutura onde ela existe de verdade (decisões, riscos) |
| **(1B.1)** Manifest sem artefato renderizado | Não prova o payload: truncamento, ordem e versão do renderizador ficam de fora, e apagar a entrada apaga o passado |
| **(1B.1)** Bloquear execução com working tree suja | Rígido demais para trabalho real; registrar e avisar preserva a informação sem travar |

## Revisões

| Fase | Mudança |
| --- | --- |
| 1B | Versão original |
| **1B.1** | *Staleness* com verificação dupla contra o base commit congelado, corrigindo falso `fresh` (**AUD-004**); fórmula canônica única de `content_hash` (§8 da auditoria); separação Selection / Manifest / **Rendered Context Artifact** (**AUD-002**) |

## Referências

[03 — Arquitetura de contexto](../architecture/03-context-architecture.md) ·
[02 — Modelo de dados](../architecture/02-data-model.md)

## Addendum — planejamento da E4 (2026-09-05)

O item 6 já definia a fórmula de `source_hash` e as regras de estado, mas não
formalizava explicitamente que `source_hash`/`source_hash_commit` são um baseline
escrito apenas na criação/edição de `source_refs`, nunca por `verify()`. Esta distinção
foi tornada normativa em [03](../architecture/03-context-architecture.md) §3 durante o
planejamento da E4, para eliminar ambiguidade antes da implementação — nenhuma decisão
original é revertida. Também foi formalizado o conceito de `verification_commit`,
generalizando o que antes assumia sempre a existência de `planning_base_commit`.
