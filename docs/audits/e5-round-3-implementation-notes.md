# E5 rodada 3 — notas de implementação do pipeline de redação em três camadas

Data: 2026-09-10. Autor: Claude Opus 5 (effort high). Escopo: implementação do redesenho
normativo de [03 §4](../architecture/03-context-architecture.md), fechando
`E5-AUD2-001..005` de [e5-round-2.md](e5-round-2.md). **Nada commitado** — segue para a
terceira rodada do Codex.

Este documento registra o lado da **implementação** no mesmo protocolo que já aplicamos
às auditorias: decisões que a especificação não fixou, limiares escolhidos e por quê,
desvios do texto normativo, e limites residuais declarados. O `AGENT_LOG.md` aponta para
cá em vez de carregar tudo.

## 1. O que mudou, em uma frase por camada

| Camada | Onde | Fecha |
| --- | --- | --- |
| Motor único de detecção | `safety/redaction.py`: `detect_secret_spans`, `is_sensitive_key`, `SecretSpan` | pré-requisito das três |
| Estrutural | `context_engine/rendering.py`: `_collect_leaves` | `E5-AUD2-004` |
| Posicional | `context_engine/rendering.py`: `_mark_fragments` | `E5-AUD2-001`, `E5-AUD2-003` |
| Propagação | `context_engine/rendering.py`: `_propagation_values` | `E5-AUD2-002` |
| Medição pós-framing | `render_block_text`, NFC final | `E5-AUD2-005` |

`RENDERER_VERSION` foi para `e5.block.v3`. **Não existe contagem de matches em lugar
nenhum** — a abordagem que as duas rodadas reprovaram foi removida, não ajustada.

## 2. Decisões não fixadas pela especificação

### 2.1 `redact()` preservado byte a byte — cascata simulada, não "todos os padrões de uma vez"

A refatoração exigia que `redact()` continuasse produzindo **exatamente** o mesmo texto.
A primeira tentativa rodou todos os padrões sobre o texto original e fundiu os spans
sobrepostos. Um teste diferencial contra a implementação anterior, sobre 7.214 entradas
(fragmentos conhecidos, todos os pares concatenados com e sem separador, e 5.000
combinações aleatórias de três), achou **253 divergências** — todas em concatenações
exóticas onde um padrão só passava a casar *depois* de outro ter substituído, ou onde
dois spans sobrepostos viravam um marcador em vez de dois.

Nenhuma delas vazava segredo (várias, na verdade, preservavam mais texto legítimo), mas
"quase igual" não é o requisito. `detect_secret_spans` passou a **simular a cascata**: a
cada padrão o texto de trabalho é reprojetado a partir do original mais as regiões já
reconhecidas, e as posições encontradas são mapeadas de volta para coordenadas do
original (`_project`, `_to_original_start`, `_to_original_end`). Numa região já
substituída o mapa não é caractere a caractere: qualquer posição dentro do marcador
corresponde à região inteira que ele cobre — é isso que reproduz o comportamento de a
cascata "engolir" texto vizinho quando um marcador removeu os espaços que separavam.

Repetido o mesmo teste diferencial: **0 divergências em 7.214 casos**.

### 2.2 `is_sensitive_key` casa **componentes**, não substring nem nome inteiro

Três opções estavam abertas:

- **nome inteiro** — `password` sim, `db_password` não. Deixa passar o caso mais comum de
  nomenclatura real;
- **substring** — `token` casaria `tokenizer`, e `{"tokenizer": "gpt-4"}` seria redigido
  inteiro. Conteúdo legítimo destruído por coincidência de letras;
- **componentes** (escolhida) — o nome é quebrado por separador (`_`, `-`, `.`, espaço) e
  por fronteira camelCase, e **qualquer subsequência contígua** de componentes é
  comparada com a lista.

Resultado: `password`, `api_key`, `apiKey`, `API-KEY`, `db_password`, `my_api_key`,
`auth.token`, `refresh_token` → sensíveis. `tokenizer`, `secretary`, `keyboard`, `title`,
`reason`, `decision` → não. Coberto por `test_is_sensitive_key_nao_dispara_em_nome_legitimo`.

A lista de nomes vive em `_SENSITIVE_KEY_WORDS` em **forma de palavras**
(`("api", "key")`), e as duas leituras saem dela: `is_sensitive_key` compara a junção sem
separador (`apikey`), e o padrão `assigned_secret` monta a alternância com separador
opcional (`api[_-]?key`). O texto do regex resultante é idêntico ao que sempre foi.

### 2.3 Limiar de propagação: 6 caracteres

A camada 3 propaga o valor **literal** já comprovado sensível para toda ocorrência dele.
Sem limiar, um `{"password": "ok"}` redigiria todo `ok` do documento, e `{"password": 1}`
todo `1`. O limiar é o mesmo `{6,}` que o padrão `assigned_secret` já usa para o valor de
uma atribuição — não um número novo inventado aqui.

Além do limiar, **escalares genéricos** (número, booleano, `null`) não entram no conjunto
de propagação em hipótese nenhuma, mesmo longos: são redigidos **localmente** pela camada
1 quando estão sob chave sensível, mas `42` sob `password` não torna todo `42` do
documento suspeito. Coberto por `test_escalar_generico_sob_chave_sensivel_nao_propaga` e
`test_valor_sensivel_longo_propaga_valor_curto_nao`.

### 2.4 Rótulo alterado vira marcador fixo — qualquer alteração, não só detecção

`E5-AUD2-003` foi causado por recortar a linha `caminho: valor` num delimitador que o
**próprio autor** podia fornecer. A regra agora não tem recorte: o rótulo só é
apresentado quando atravessa as três camadas **intacto** e não contém o separador
reservado `": "`. Qualquer alteração — span local, marca inteira, ou substituição da
camada 3 — significa que ele carregava segredo, e a linha inteira vira
`<chave protegida>: «redigido»`.

Isso é mais forte do que o texto normativo pede ("se a própria chave for insegura para
apresentar"): uma chave que só foi *tocada* pela propagação também vira marcador, em vez
de virar `«redigido»key`. A reconstrução parcial de uma chave é exatamente a forma que o
finding tomou; não deixei nenhuma variante dela viva.

### 2.5 Desvio declarado: a camada posicional faz **quatro** leituras, não uma

[03 §4](../architecture/03-context-architecture.md) diz que `detect_secret_spans` "roda
**uma única vez** sobre essa projeção". Implementado literalmente, isso **não fecha**
`E5-AUD2-001b`: na ordem canônica a folha `a` fica entre `body` e `resto`, e a projeção
única testa `sk-123456!!!7890ABCDEF`, nunca a fronteira que importa. Era esse exato caso
que a rodada 2 usou para reprovar a versão anterior.

O que implementei alimenta um único conjunto de marcas a partir de quatro leituras:

1. cada fragmento **isolado** — segredo autocontido num campo só;
2. a linha `caminho: valor` — a adjacência que `assigned_secret` precisa, mapeada por
   posição;
3. cada **par ordenado** de fragmentos colados — o segredo partido, imune a folha
   interposta e à ordem canônica;
4. a projeção canônica inteira — corridas contíguas cobrindo três ou mais fragmentos.

**Leio "uma única vez" como o contraste que a frase faz explicitamente**: contra a
abordagem abandonada, que rodava leituras separadas *para comparar quantidades entre
elas*. Aqui nenhuma leitura olha o resultado de outra; cada uma só acrescenta marcas.
Não há contagem em lugar nenhum.

Ainda assim, **a letra do documento diz "uma única vez" e a implementação faz quatro
leituras.** Não alterei `docs/architecture/` — não havia autorização para isso nesta
tarefa. Fica para decisão de Pedro: ou o texto de §4 é ajustado para "um único conjunto
de marcas, alimentado por leituras que não se comparam", ou a divergência é registrada
como aceita.

### 2.6 Extensão gulosa não marca o fragmento vizinho

Encontrado ao implementar, não pelas auditorias: um segredo **completo** no fim do `body`,
colado ao `title` para a leitura de par, fazia o padrão continuar consumindo os
caracteres alfanuméricos do título (o alfabeto de `sk-ant-[A-Za-z0-9._-]{8,}` engole o
que vier). O par então marcava os **dois** fragmentos inteiros, e um título legítimo
virava `«redigido»` só por estar ao lado.

`_is_greedy_extension` distingue os dois casos por uma pergunta **estrutural**, não por
contagem: *esta posição já era início de detecção no fragmento isolado?* Se sim, o
segredo já está sendo redigido no campo onde de fato está, e a continuação para dentro do
vizinho é gulodice de alfabeto. Se não — o caso de `E5-AUD2-001`, onde `sk-123456` sozinho
não casa nada — é segredo partido de verdade, e os dois fragmentos saem inteiros.

Coberto nos dois sentidos: `test_segredo_no_fim_do_corpo_nao_apaga_o_titulo` (não
over-redige) e `test_e5_aud2_001a/b` (não deixa passar).

### 2.7 NFC no fim, sempre — inclusive sem `NORMALIZE`

`canonical_json` aplica NFC ao serializar o artefato. Medir antes disso mede uma forma que
os bytes gravados não têm — é `E5-AUD2-005`. `render_block_text` normaliza o texto e o
título em NFC como **último** passo, mesmo quando `Transformation.NORMALIZE` não foi
pedida.

Consequência assumida: com `transformations=(REDACT,)`, a forma NFD do corpo **não**
sobrevive no texto emitido. Ela não sobreviveria de qualquer jeito — `canonical_json` a
comporia na serialização. A diferença é que agora a contagem sabe disso. O teste antigo
`test_medicao_usa_a_representacao_final_normalizada_e_redigida`, que a rodada 2 apontou
como insuficiente (`assert len(x) == len(x)`, sem tocar em `select_context`/
`render_context`), foi **substituído** por
`test_e5_aud2_005_medicao_bate_com_os_bytes_gravados`, que lê `json.loads` dos bytes reais
do arquivo e compara `total_chars`/`approx_tokens`/`emitted_chars` com o `blocks[].text`
que está lá.

### 2.8 Formato de `structured` no bloco: linhas `caminho: valor`, mantido

A rodada 2 já examinou a troca de JSON por linhas rotuladas e concluiu que é válida
(§6 de [e5-round-2.md](e5-round-2.md)): nenhum documento normativo exige cerca JSON
dentro de `blocks[].text`, e `renderer_version` distingue a mudança. Mantida, com os dois
efeitos colaterais que a auditoria apontou agora fechados — chave crua (`E5-AUD2-003`) e
contexto de contêiner (`E5-AUD2-004`).

As perdas de distinção que a rodada 2 catalogou (`{"a.b":1}` e `{"a":{"b":1}}` produzindo
o mesmo texto; ordem lexicográfica de rótulos pondo `[10]` antes de `[2]`) **continuam**.
Não são vazamento e não têm consumidor implementado; ficam declaradas aqui, não
corrigidas.

## 3. Custo e tetos

A leitura de pares é O(n²) em fragmentos, com `n = 2 + 2 × folhas` (título, corpo, e
rótulo + valor por folha). Medido nesta máquina:

| Folhas | Fragmentos | Tempo |
| ---: | ---: | ---: |
| 5 | 12 | 2,3 ms |
| 10 | 22 | 7,3 ms |
| 20 | 42 | 27 ms |
| 40 | 82 | 101 ms |
| 60 | 122 | 221 ms |
| 127 | 256 | 966 ms |

Entrada típica de contexto tem menos de 20 folhas — dezenas de milissegundos, numa
operação que roda uma vez por planejamento. O teto é `_MAX_FRAGMENTS = 256` (127 folhas),
e acima dele o bloco vai para o fail-closed em vez de degradar para melhor esforço. O
outro teto é `_MAX_STRUCTURED_DEPTH = 32`.

Os dois números são escolha desta implementação, não do documento. O critério: o pior
caso do teto (≈1 s) é aceitável para uma operação de planejamento, e 127 folhas ou 32
níveis de aninhamento num `structured` de entrada de contexto já é patológico o bastante
para que perder o bloco seja preferível a emitir metade de um segredo.

## 4. Limites residuais declarados

1. **Segredo partido entre três ou mais fragmentos, fora da ordem canônica.** As leituras
   de par cobrem qualquer divisão em **dois** fragmentos, em qualquer ordem. A projeção
   canônica cobre corridas contíguas de qualquer tamanho. Uma divisão em três ou mais
   fragmentos que só se recompõe numa ordem que não é a canônica **não** é detectada. Para
   isso, os fragmentos do meio teriam de ser inteiramente consumidos pelo padrão — curtos
   e inteiramente alfanuméricos. Não fechei: a alternativa é o produto cartesiano de
   ordens, que é fatorial. Declarado, não escondido.
2. **`_is_greedy_extension` assume que o fragmento onde a detecção começa é onde o
   segredo está.** Se um campo terminar com um token completo e o campo seguinte começar
   com a continuação real do mesmo segredo, o primeiro é redigido e a continuação
   sobrevive. O valor identificador (o prefixo reconhecível) sai; a cauda fica. É o preço
   de não redigir todo campo vizinho de um campo com segredo — ver 2.6.
3. **A detecção continua sendo a de `safety/redaction.py`.** Nenhuma das três camadas
   torna o redator exaustivo; elas garantem que o que ele **reconhece** não escape por
   estrutura, por posição ou por cópia. Um segredo que nenhum padrão reconhece continua
   passando, como o próprio módulo sempre declarou.

## 5. Verificação

- **Suíte completa: 1080 passed / 6 skipped** (era 1056; +24 do
  `tests/test_context_redaction_e5_round3.py`). `ruff check` · `ruff format --check` ·
  `mypy` limpos (74 arquivos).
- **`redact()` byte a byte idêntico**: 7.214 casos, 0 divergências contra a
  reconstrução da implementação anterior.
- **As 10 reproduções da rodada 2 falham contra a implementação anterior** e passam
  contra esta — verificado por reconstrução standalone da versão antiga, fora do working
  tree, sem alterar nenhum arquivo. As 3 reproduções da rodada 1 já passavam na versão
  anterior (foram fechadas na correção daquela rodada); entram como regressão.
- **Travas estruturais novas** em `tests/test_architecture.py`:
  `test_deteccao_de_segredo_tem_dono_unico` (assinatura de padrão de segredo fora de
  `safety/redaction.py`) e `test_context_engine_nao_casa_regex_sobre_conteudo_autoral`
  (`re.<casamento>` em `context_engine/` fora do dono da gramática de glob). Contrafactual
  executado: as duas detectam violação.
- **Análise estática**: uma só definição de padrões de segredo (`safety/redaction.py`),
  uma só lista de nomes sensíveis (`_SENSITIVE_KEY_WORDS`), zero `finditer`/`.sub(` em
  `context_engine/`.

## 6. O que a rodada 3 do Codex precisa confirmar

O pedido é uma rodada **curta**, focada em confirmar que as três camadas fecham a
**classe** de vazamento — não em descobrir mais uma variação. Concretamente:

1. As três camadas cobrem as três formas de escapar que a rodada 2 explorou (estrutura,
   posição, cópia), e não só os casos nomeados;
2. O desvio de 2.5 (quatro leituras vs. "uma única vez") é aceitável ou exige ajuste do
   texto normativo;
3. Os limites residuais de §4 são aceitáveis como declarados;
4. Os limiares de §2.3 e os tetos de §3 são razoáveis.
