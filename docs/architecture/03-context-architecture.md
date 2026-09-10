# 03 — Arquitetura de contexto

> Escopo: Context Registry, hash canônico, *staleness*, seleção, manifest, rendered
> artifact, Task Analyzer, Resource Router e política de auditoria.
> Decisões relacionadas: [ADR-0006](../adr/0006-context-registry-selective-context.md),
> [ADR-0010](../adr/0010-benchmark-protocol.md).
>
> **Revisado na Fase 1B.1** (AUD-002, AUD-004, AUD-007, §8) e na **Fase 1B.3**
> (REAUD-007).

Princípio que atravessa o documento: **não gastar token para decidir o que uma regra
determinística decide bem o bastante.**

---

## 1. O que é entrada e o que não é

| Domínio | Entrada? | Forma |
| --- | --- | --- |
| `objective` | ✅ | uma por workspace |
| `architecture` | ✅ | uma; `source_refs` apontando para os diretórios que a descrevem |
| `stack` | ✅ | uma; lista no corpo |
| `requirements` | ✅ | **duas** — funcionais e não funcionais |
| `modules` | ✅ | uma por módulo relevante |
| `decisions` | ✅ | **uma por decisão**, `structured = {decision, reason, date, supersedes?}` |
| `risks` | ✅ | **uma por risco**, `structured = {mitigation, probability?, impact?}` |
| `contracts` | ✅ | uma por contrato (endpoint, assinatura pública, formato de arquivo) |
| `file_map` | ❌ | **artefato derivado** |

`file_map` é derivado do repositório, não escrito por ninguém. Guardá-lo como entrada
editável criaria uma cópia que envelhece a cada commit e que poderia ser editada para algo
falso. É gerado por `build_file_map(workspace)`, cacheado por
`(workspace_id, base_commit)` e referenciado no manifest pelo hash.

**`body` é o conteúdo autoral canônico.** `structured` existe para a UI filtrar, ordenar e
renderizar formulários. O que o agente recebe é o resultado da renderização (§4), não a
linha do banco.

### Seed a partir de `ProjectPlanning`

O **frontend** envia `ProjectPlanning` para `POST /api/workspaces/{id}/context/import`. O
backend não sabe de onde veio.

| Campo de `ProjectPlanning` | Vira |
| --- | --- |
| `problem` + `objective` | uma entrada `objective` |
| `functionalRequirements[]` / `nonFunctionalRequirements[]` | duas entradas `requirements` |
| `stack[]` | uma entrada `stack` |
| `architecture` | uma entrada `architecture` |
| `technicalDecisions[]` | N entradas `decisions` |
| `risks[]` | N entradas `risks` |

Todas nascem `origin = imported_planning`, `source_refs = []`, `state = fresh`.

A importação acontece **uma vez**. Depois, os dois modelos evoluem independentemente: não
há sincronização bidirecional, que reintroduziria o acoplamento eliminado por
[ADR-0002](../adr/0002-disjoint-persistence.md). Reimportar cria entradas novas e nunca
sobrescreve em silêncio.

---

## 2. `content_hash` — fórmula canônica única *(corrige §8 da auditoria)*

Esta seção é a **única definição normativa**. Os demais documentos referenciam esta.

```text
normalize(s)      = NFC · CRLF→LF · espaços finais removidos por linha ·
                    quebras finais colapsadas em uma
canonical_json(o) = chaves ordenadas · sem espaço supérfluo · UTF-8

content_hash = sha256( canonical_json({
  "v":          1,
  "domain":     <domain>,
  "title":      normalize(title),
  "body":       normalize(body),
  "structured": <structured normalizado, ou null>
}) )
```

**Entra:** `domain`, `title`, `body`, `structured`.
**Não entra:** `id`, `workspace_id`, `tags`, `source_refs`, `state`, `stale_reason`,
`source_hash`, `origin`, timestamps.

Cada hash tem um trabalho distinto, e eles não se confundem:

| Hash | Cobre | Responde |
| --- | --- | --- |
| `content_hash` | conteúdo autoral da entrada | *a entrada foi editada?* |
| `source_hash` | código coberto por `source_refs` | *o código descrito mudou?* |
| `rendered_context_hash` | payload final entregue | *o que o agente recebeu?* |
| `manifest_hash` | conjunto de fontes selecionadas | *o que foi escolhido?* |

`structured` entra em `content_hash` porque é conteúdo autoral editável: mudar a
justificativa de uma decisão é mudar a entrada, mesmo que a renderização atual não a
exiba.

---

## 3. `staleness` — sem embeddings *(corrige AUD-004)*

> **Correção 1B.1.** `git ls-tree` sozinho compara apenas o **estado commitado** e produz
> *falso `fresh`*: um arquivo modificado, staged, deletado ou não rastreado no working tree
> não aparece na árvore do commit. A verificação passa a ser dupla.

Toda verificação é feita **em relação ao `planning_base_commit`**, nunca a um HEAD móvel.

### Parte A — estado commitado

Os globs de `source_refs` são expandidos numa **lista ordenada e congelada** de arquivos.
Sobre ela:

```text
source_hash = sha256( canonical_json( [ (path, blob_sha) ordenados ] ) )
```

Os `blob_sha` vêm de `git ls-tree -r <planning_base_commit>` — o git já mantém o hash de
conteúdo de cada arquivo rastreado, então nenhum arquivo precisa ser lido.

### Parte B — divergência do working tree

```text
git status --porcelain=v2 -z --untracked-files=all
```

Detecta, de forma exaustiva: `modified`, `staged`, `deleted`, `renamed`, `untracked`.
Qualquer path coberto pelos `source_refs` que apareça divergente impede `fresh`.

### Regra de estado — completa

| Situação | `state` | `stale_reason` |
| --- | --- | --- |
| `source_refs` vazio (entrada autoral) | `fresh` | — |
| `source_hash` confere no base commit **e** nenhum path coberto diverge | `fresh` | — |
| `source_hash` difere do armazenado | `stale` | `sources_changed` |
| Path coberto diverge no working tree | `stale` | `working_tree` |
| `source_refs` irresolvíveis · não é repositório git · `git` indisponível | `unknown` | — |

`unknown` significa especificamente *"a entrada declara depender de código e não consegui
verificar"* — nunca uma dúvida vaga.

### Registro no manifest

O `ContextManifest` registra sempre:

```text
working_tree_divergence = {
  dirty_file_count,                        # divergências no workspace inteiro
  covered: [ {path, kind} ]                # divergências em paths cobertos por source_refs
}
```

A divergência é **registrada mesmo quando não afeta nenhuma entrada selecionada**. Ela
nunca é escondida.

### Regra operacional — o que executa

**A execução usa exclusivamente o `planning_base_commit` congelado.** Alterações não
commitadas **não entram automaticamente** na execução: a worktree nasce do commit, e o
agente não as enxerga.

- Isso **não bloqueia** a execução com árvore suja.
- A UI informa, no plano e antes da aprovação, quantos arquivos divergem e quais deles
  estão cobertos pelo contexto. Sem esse aviso, um resultado "errado" ficaria inexplicável.
- **Regra dura mantida:** tarefa com `risk = high` não executa com entrada `stale`
  selecionada — inclusive quando o motivo é `working_tree`.

### Quando a verificação roda

No início de `POST /api/tasks/{id}/plan`, logo após congelar o `planning_base_commit`; e
sob demanda por `POST /api/workspaces/{id}/context/verify`. Sem watcher de filesystem e sem
polling na V1 — um `ls-tree` mais um `status` por verificação é barato, e um watcher em
pasta sincronizada por OneDrive geraria ruído contínuo.

### `verification_commit` — o SHA que a verificação enxerga

Toda verificação captura, **uma única vez no seu início, um `verification_commit`**
imutável durante toda a execução dela. As duas partes da verificação — o `git ls-tree`
da Parte A e o `git status` da Parte B — usam esse mesmo SHA; ele não é relido no meio
da chamada.

| Onde a verificação roda | `verification_commit` |
| --- | --- |
| Dentro do planejamento de uma task (E6+), em `POST /api/tasks/{id}/plan` | `= planning_base_commit` — o commit já congelado para a task |
| Sob demanda, sem task (E4), em `POST /api/workspaces/{id}/context/verify` | `= HEAD` atual do workspace, lido **uma única vez** no início da chamada e tratado como congelado pelo resto dela |

O texto acima ("toda verificação é feita em relação ao `planning_base_commit`") descreve
o caso E6+. `verification_commit` generaliza a regra para o caso E4, em que ainda não
existe `planning_base_commit`: a verificação sob demanda fixa o `HEAD` do workspace no
início e o trata como base congelada, exatamente como o planejamento trata o base commit.

### `source_hash` / `source_hash_commit` — baseline confirmado, escrito só na origem

`source_hash` e `source_hash_commit` juntos são o **baseline confirmado** de uma entrada:
o hash das fontes cobertas e o commit em que esse hash foi medido. Esse par é escrito
**somente** em dois momentos:

- na **criação** de uma entrada cujo `source_refs` não é vazio; ou
- quando `source_refs` é **explicitamente alterado** via `PATCH`.

`verify()` **nunca** escreve nesses dois campos. Ele apenas lê o baseline armazenado,
recomputa o `source_hash` no `verification_commit` e compara. O que `verify()` atualiza é
**exclusivamente** `state`, `stale_reason`, `last_verified_at` e `last_verified_commit`.

Decorre disso:

- **Uma entrada `stale` nunca volta a `fresh` só porque `verify()` rodou de novo.** O
  baseline armazenado não muda quando `verify()` roda, então a comparação continua dando o
  mesmo resultado.
- Editar apenas `body`, `title`, `structured` ou `tags` **não toca** o baseline — muda o
  `content_hash` (§2), não o `source_hash`.
- `source_refs` alterado para **lista vazia** remove o baseline (`source_hash` e
  `source_hash_commit` → `null`) e devolve o estado a `fresh` sem `stale_reason`, pela
  primeira linha da regra de estado.

---

## 4. Seleção, manifest e artefato renderizado

Três camadas com papéis distintos *(corrige AUD-002)*:

```text
Context Selection          →  Context Manifest          →  Rendered Context Artifact
transitória, em memória       linha persistida             blob imutável por conteúdo
"quais candidatos e score"    "quais fontes, qual commit"  "o payload exato entregue"
```

### Context Router — determinístico

Pontuação por entrada:

| Sinal | Peso |
| --- | --- |
| Match literal exato entre `source_ref` e `candidate_path` | `exact_literal_match` — sinal mais alto que qualquer combinação de sobreposição+proximidade, mesmo se o candidato não existir no `base_commit` |
| Sobreposição entre `source_refs` e os globs candidatos da análise | alto |
| `domain` entre os domínios que a análise marcou como afetados | alto |
| Casamento de `tags` com termos do objetivo | médio |
| Proximidade no file map | médio |
| `state = fresh` | bônus pequeno |
| Recência de `updated_at` | desempate |

Este sinal existe para garantir que uma referência precisa nunca perca para um glob largo
por causa de proximidade de file map — precisão de match é distinta de cobertura de
arquivos.

**Fechado na correção de auditoria da E5 (AUD-008).** `SCORE_EXACT_LITERAL_MATCH = 131`
(`app/context_engine/selection.py`) — a menor margem inteira que garante vencer qualquer
combinação de `SCORE_SOURCE_REF_OVERLAP + PROXIMITY_MAX` (100 + 30 = 130) sozinho. Dispara
quando algum `source_ref` da entrada é padrão **literal** (`CompiledSourceRef.literal`,
sem metacaractere de glob) e seu valor normalizado é exatamente um `candidate_path` — por
comparação de string, nunca por resolução contra `base_commit`; um candidato que não
existe na árvore ainda dispara o sinal. Testado com `candidate_path = "src/new.py"`
inexistente no commit, entrada A (`source_ref` literal igual ao candidato) contra entrada
B (`source_ref = "src/**"`, com proximidade favorável e domínio/tags/frescor idênticos a
A): `test_exact_literal_match_vence_glob_mesmo_sem_existir_no_commit`, em
`api/tests/test_context_router_e5.py`.

Entradas `domain = objective` são sempre incluídas. O corte é por orçamento
(`max_context_tokens`); o que não coube entra em `excluded` com `reason = budget`, para que
*"por que o agente não sabia disso?"* tenha resposta.

Re-rank por LLM é **opcional e desligado por padrão**; quando ligado, apenas reordena
candidatos — nunca acrescenta entrada nem contorna exclusão de política.

**O Context Engine não conhece nenhum provider.**

### Rendered Context Artifact

O renderizador transforma a seleção no payload final e grava um snapshot imutável, com:
blocos na ordem emitida, origem de cada bloco, truncamentos, transformações aplicadas,
tamanhos e `renderer_version`. A redação de segredos é aplicada **antes** do hash.

**Redação no renderizador — três camadas independentes, não uma comparação de
contagem.** Duas rodadas de auditoria da E5 ([e5-round-1](../audits/e5-round-1.md),
[e5-round-2](../audits/e5-round-2.md)) sucessivamente reprovaram abordagens que
tentavam decidir "isto é segredo?" olhando o texto já linearizado e comparando *quantas*
redações uma leitura combinada revela contra a soma de leituras isoladas
(`E5-AUD2-001`): um campo `password` adicional, ou uma folha interposta entre duas
metades de um mesmo token, mascarava a contagem sem proteger nada. A segunda rodada
também encontrou chave-como-segredo reemitida crua (`E5-AUD2-003`), contêiner sob uma
chave sensível perdendo a classificação (`E5-AUD2-004`) e cópia do mesmo valor
sobrevivendo em outro campo — inclusive no título propagado para o manifest
(`E5-AUD2-002`). A causa comum: nenhuma dessas versões tinha uma noção **estrutural**
de "isto pertence a um segredo" — só comparações locais de texto já achatado.

O desenho substitui a comparação de contagem por três camadas que rodam em sequência,
cada uma resolvendo uma classe de vazamento que a anterior não cobre:

1. **Camada estrutural.** `structured` é percorrido como **árvore**, antes de qualquer
   linearização em texto. Uma chave que `safety.is_sensitive_key` classifica como
   sensível marca **toda a subárvore descendente** como sensível — independente de
   profundidade, de a chave estar sob uma lista (`password[0]`), um objeto aninhado
   (`password.current`) ou qualquer chave intermediária no caminho. Toda folha dessa
   subárvore é redigida **incondicionalmente**, sem depender de o valor "parecer" um
   segredo. Se a própria chave for insegura para apresentar (contém sequência que
   quebraria o parser de exibição — ex.: um separador reservado do formato de
   linearização), o rótulo emitido é um marcador fixo, **nunca** uma reconstrução
   parcial da chave original: reconstruir parcialmente foi exatamente o que devolvia o
   segredo pela chave em `E5-AUD2-003`.
2. **Camada posicional.** Depois da camada estrutural, `título` + `corpo` + as folhas de
   `structured` (na ordem canônica) viram fragmentos, cada um com um mapa de posição de
   volta para a sua origem. `safety.detect_secret_spans` é chamado sobre **várias
   leituras** desses fragmentos, e todas alimentam **um único conjunto monotônico de
   marcas de redação**:
   - cada fragmento **isolado** — segredo autocontido num campo só;
   - a linha `caminho: valor` reconstruída — a adjacência que o padrão `password: valor`
     precisa, mapeada de volta **por posição**, nunca recortada por delimitador
     (`E5-AUD2-003`);
   - cada **par ordenado** de fragmentos colados sem separador — o segredo partido entre
     dois campos, reconhecido mesmo quando a ordem canônica põe uma folha entre eles
     (`E5-AUD2-001b`);
   - a **projeção canônica inteira** — corridas contíguas cobrindo três ou mais
     fragmentos.

   A garantia que importa **não é "uma única chamada"** — é que **nenhuma leitura decide
   com base em contagem relativa a outra**. Cada leitura só acrescenta marcas ao
   conjunto; nenhuma olha quantas marcas outra fez. Foi a comparação de contagem entre
   leituras (`glued_hits > isolated_hits`) que as rodadas 1 e 2 reprovaram: um campo a
   mais ou uma folha interposta mascarava o número sem proteger nada. Um conjunto de
   marcas que só cresce, alimentado pelo mesmo motor de detecção, não tem essa falha.
3. **Camada de propagação.** Depois das duas camadas acima, os valores textuais **já
   comprovadamente sensíveis** (pela estrutura ou pela posição) são reunidos num
   conjunto; qualquer ocorrência **literal** de um desses valores em `título`, `corpo`
   ou `structured` é redigida — mesmo numa ocorrência sem rótulo de chave sensível
   naquele local específico. É o que fecha `E5-AUD2-002`: o mesmo valor de `password`
   copiado para `body` ou para outro campo, ou para o `título`, é redigido lá também,
   porque já é conhecido como sensível — não porque aquela ocorrência específica
   "pareceu" um segredo. **Escalares genéricos** (número, booleano, `null`) **não**
   entram neste conjunto de propagação global — só são redigidos localmente, se
   estiverem sob uma subárvore marcada sensível pela camada 1; um `3` ou um `true`
   comuns não viram gatilho para redigir todo `3` ou `true` que aparecer em outro lugar.

**Invariante.** Depois de as três camadas produzirem a representação redigida, nenhum
dado autoral cru pode reaparecer em `blocks[].text`, `blocks[].origin.title`,
`ContextManifest.entries[].title`, `structured` renderizado, ou qualquer metadado
autoral equivalente. Framing e formatação estrutural (o cabeçalho fixo, a ordem dos
blocos) vêm **depois** da redação, nunca antes — formatar antes é o que quebrava a
adjacência que o padrão `password: valor` precisa, na primeira versão desta correção.
Medição de tamanho (`total_chars`/`approx_tokens`) vem depois do **framing final**,
sobre exatamente a representação que será persistida — nunca sobre uma forma
intermediária que a serialização final ainda vai transformar (`E5-AUD2-005`, a
reabertura de `AUD-007`: medir antes de `canonical_json` aplicar NFC produzia uma
contagem que não batia com os bytes gravados).

**Fallback fail-closed.** Se uma estrutura não puder ser linearizada, mapeada ou
redigida com prova suficiente de segurança pelas três camadas, o bloco ou a subárvore
envolvida é redigido por completo, ou a renderização é recusada — nunca uma heurística
de melhor esforço que arrisca emitir metade de um segredo por não saber o que fazer com
o resto.

**Trade-off aceito na V1 — falso positivo entre pares de fragmentos.** A leitura de
pares (`E5-AUD2-001b`) concatena dois fragmentos que na verdade **não têm relação
nenhuma**. Duas strings de conteúdo legítimo podem, por coincidência, formar uma
sequência que bate um padrão de segredo quando coladas — um prefixo público num campo,
uma sequência alfanumérica pública noutro — e o resultado é redação de conteúdo que não
era sensível (`E5-AUD3-001`). A V1 **aceita esse risco deliberadamente**. A prioridade é
explícita: um **falso positivo de redação** (perda de qualidade do contexto entregue ao
Developer) é preferível a um **falso negativo** (vazamento de uma credencial real no
Rendered Context Artifact, que entra no `execution_fingerprint` e sobrevive à exclusão
da entrada de origem). **Não** se restringe a leitura a pares adjacentes, nem se
introduz heurística de confiança ou de comprimento para decidir se um par "parece"
credencial: qualquer uma das duas reabriria a classe que `E5-AUD2-001b` provou — um
segredo genuinamente partido entre fragmentos que a ordem canônica não deixa vizinhos.
O registry autoral cru nunca é alterado; o que se perde é conhecimento naquele bloco
específico, recuperável reeditando a entrada.

**Explicabilidade planejada.** Quando um bloco sofre redação **especificamente por
detecção cross-fragment** — um span de reconhecimento que atravessa a fronteira entre
dois fragmentos —, o bloco registra essa transformação no metadado `transformations` que
já existe, **sem** expor o valor redigido nem o par que a disparou. É só para responder,
no futuro, *por que* aquele bloco foi redigido quando nenhum campo isolado continha
segredo — distinguir "coincidência de par" de "segredo autocontido" na hora de revisar
um contexto que veio mais pobre do que o esperado.

`is_sensitive_key`/`detect_secret_spans` são API pura de `safety.redaction`
([04](04-safety-and-git-runtime.md) §5) — o mesmo motor de detecção de `redact()`,
reaproveitado aqui, nunca uma segunda lista de nomes sensíveis ou um segundo motor de
regex dentro de `context_engine`.

Estrutura e propriedades em [02](02-data-model.md) §5. O ponto essencial: por ser um
**snapshot**, ele responde *"qual conhecimento o Developer recebeu?"* mesmo depois de a
entrada de origem ser editada ou apagada — o que o manifest sozinho não conseguia.

`rendered_context_hash` entra no `execution_fingerprint` ([02](02-data-model.md) §7).

---

## 5. Task Analyzer — o que é regra e o que é LLM

### Passo 1 — hard rules (determinístico, custo zero)

Elevam `risk` para **`high`** quando o objetivo ou os arquivos candidatos tocam:

| Categoria | Padrões |
| --- | --- |
| Migrations / schema | `migrations/**`, `alembic/**`, `**/*.sql`, arquivos de modelo |
| Autenticação e permissões | `**/auth*`, `**/permission*`, `**/session*`, `**/*token*` |
| Segredos | `.env*`, `secrets/**`, `*.pem`, `*.key`, `.npmrc`, `.git-credentials` |
| CI/CD e deploy | `.github/workflows/**`, `Dockerfile*`, `*.compose.y*ml` |
| Dependências | `package.json`, `package-lock.json`, `pyproject.toml`, `poetry.lock`, `requirements*.txt` |
| Histórico Git | objetivo com `rebase`, `force push`, `reset --hard`, `filter-branch` |
| Comandos destrutivos | objetivo com `rm -rf`, `drop table`, `truncate`, `apagar tudo` |

Elevam para **`medium`** quando o objetivo cruza mais de um módulo ou toca configuração de
build.

### Passo 2 — atalho determinístico

Se **todas** valerem, nenhum modelo é chamado: risco de hard rule `low`; objetivo casando
com padrão trivial catalogado; e ≤ 1 arquivo alvo. Resultado: `complexity = trivial`,
`agents = [developer]`, zero token gasto em classificação.

### Passo 3 — LLM, só no que sobra

Resumir a intenção; inferir módulos e globs prováveis (o sinal mais valioso para o Router);
refinar `complexity`; propor **critérios de aceite verificáveis**; sinalizar necessidade de
`architect` ou `researcher`.

### A regra que fecha o ciclo

```text
final_risk = max(hard_rule_risk, llm_risk)
```

**Um LLM pode elevar o risco; nunca reduzi-lo abaixo do piso de uma hard rule.** É código,
não instrução de prompt. `risk_source` registra qual prevaleceu.

Falha ou timeout do provider de análise **não bloqueia**: usa as hard rules, assume
`complexity = medium` por conservadorismo e segue. A degradação é para o lado seguro.

---

## 6. Resource Router

Determinístico. Nenhum LLM participa.

```text
prefer_single_agent   = true
max_agents            = 3
max_parallel_agents   = 1
max_attempts          = 2
max_fix_rounds        = 2      # inerte até E10 — ver abaixo
max_context_tokens    = por run   (configurável)
max_total_tokens      = por task  (configurável)
```

> **Correção 1B.1 (AUD-013).** `max_fix_rounds` governa o **laço de correção dirigido por
> auditoria**, que só existe a partir de **E10**. Em E8 e E9 o Developer executa em passe
> único: falha de teste ou finding leva direto a `needs_fix`, sem rodada automática. Um
> laço de correção antes de o auditor existir não teria sinal para consumir.

Estourar `max_context_tokens` ou `max_total_tokens` gera `SafetyEvent(limit_exceeded)` e
`failed(limit_exceeded)`.

### Tabela de seleção

| risco \ complexidade | trivial / low | medium | high |
| --- | --- | --- | --- |
| **low** | developer | developer | developer |
| **medium** | developer | developer | developer (+ architect se sinalizado) |
| **high** | developer | developer (+ architect se sinalizado) | developer + architect |

O auditor não aparece na tabela porque não é opcional em operação normal (§7).
`researcher` entra só quando a análise aponta dependência externa desconhecida, e apenas
produz entradas de contexto — nunca escreve código.

---

## 7. Política de auditoria — operação normal

**Obrigatória.** Todo run que produz diff não vazio é auditado, a partir de E9.

**Custo.** Um run a mais por tarefa. O auditor recebe o **diff**, os critérios de aceite e
o resumo dos testes — **não o repositório**. O custo escala com o tamanho da mudança, não
com o do projeto.

**Benefício.** Verificação independente é o principal mecanismo de qualidade disponível.

**Regras**

1. Diff não vazio → auditoria obrigatória (`purpose = workflow_audit`).
2. Diff vazio → sem auditoria; não há o que auditar.
3. Finding `high` com `status = open` **bloqueia `done`**.
4. Flag `audit.skip_docs_only`, **padrão `false`**. Ligada, pula auditoria quando o diff
   toca apenas `*.md`/comentários **e** `risk = low`.

---

## 8. Protocolo de benchmark *(corrige AUD-007)*

> **Contradição corrigida.** A versão anterior mandava rodar o auditor "fora de banda" em
> `claude_only`. Isso quebrava a comparação por dois motivos: dava a um modo um avaliador
> que os outros recebiam em momento e contexto diferentes, e misturava o auditor **do
> fluxo** com o avaliador **do benchmark**.

Operação normal e benchmark são coisas separadas.

### Execução dos modos

Cada modo executa até o fim **sem qualquer realimentação de avaliador externo**:

| Modo | O que roda | Etapa |
| --- | --- | --- |
| `claude_only` | Developer sozinho. **Sem orquestração e sem auditor de workflow.** | E12 |
| `orchestrated` | Orquestração completa: contexto seletivo, auditor de workflow e laço de correção | E12 |
| `orchestrated_ruflo` | Igual ao anterior, com Ruflo nos pontos de extensão | E13 |

A diferença entre `claude_only` e `orchestrated` **é justamente a orquestração, o auditor
incluído**. Isso é o objeto da medição, não um desvio a corrigir.

### Cronologia em duas etapas *(corrigido em 1B.3 — REAUD-007)*

Ruflo é opcional e chega depois; a comparação não pode ficar refém dele.

| Etapa | O que executa | O que avalia |
| --- | --- | --- |
| **E12 — Baseline Benchmark** | `claude_only` e `orchestrated` sobre **casos congelados** | Avaliação dos **dois** modos: mesmo avaliador, mesma rubrica, artefatos congelados. Já produz comparação preliminar utilizável |
| **E13 — Ruflo Experiment** | `orchestrated_ruflo` sobre **os mesmos casos congelados** | Avaliação final dos **três** modos, novamente com mesmo avaliador, mesma rubrica e artefatos congelados |

Os casos são congelados em E12 e reutilizados em E13 — é o que torna as duas rodadas
comparáveis. Se E13 nunca acontecer, a comparação de dois modos de E12 continua válida e
completa.

### Avaliação de benchmark

Em ambas as etapas, depois de os modos terminarem e seus resultados estarem **congelados**:

- **mesmo avaliador**, **mesma rubrica** (`rubric_version` registrada), **mesmo momento**;
- avalia os outputs congelados (diff + resumo de testes) do mesmo `benchmark_group_id`;
- produz `AuditFinding` com `purpose = benchmark_evaluation`, em `Run` próprio com
  `subject_run_id` apontando para o run avaliado.

**Essa avaliação:** mede qualidade; **não** altera resultado de execução; **não** alimenta
correção; **não** oferece feedback durante a execução; **não** bloqueia `done`.

### Consequências no modelo

- `AuditFinding.purpose` e `Run.purpose` separam `workflow_audit` de
  `benchmark_evaluation` — sem entidade nova.
- Somente `workflow_audit` com `high`/`open`, **na auditoria vigente**, bloqueia conclusão
  ([02](02-data-model.md) §9).
- Agregações de custo por tarefa filtram `Run.purpose`, para que o custo do avaliador não
  seja atribuído a nenhum dos modos comparados.
- `benchmark_group_id` liga as execuções do mesmo objetivo.

Registrado em [ADR-0010](../adr/0010-benchmark-protocol.md).
