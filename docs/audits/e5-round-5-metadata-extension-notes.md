# E5 rodada 5 — extensão de metadata: `PROPAGATED_SECRET_REDACTION`

Data: 2026-09-12. Autor: Claude Sonnet 5 (effort medium). Escopo: acrescentar um
segundo marcador de diagnóstico a `blocks[].transformations`, sinalizando redação por
propagação (`E5-AUD5-001`), depois da [rodada 5](e5-round-5.md) (**GREEN** técnico, com
esse risco classificado Média/P2 e recomendado para aceitação). **Nada commitado** —
segue para uma rechecagem curta do Codex.

Esta tarefa é **só sinalização**: nenhuma linha de `detect_secret_spans`,
`_mark_fragments`, `_propagation_values` ou da substituição literal foi tocada. O que
muda é que `_redacted_text` agora **relata** se a última etapa que executava (o laço de
propagação) alterou o texto — um booleano — e `render_block_text` acrescenta o marcador
quando qualquer fragmento do bloco relatou `True`.

## 1. O que foi acrescentado

```python
PROPAGATED_SECRET_REDACTION = "propagated_secret_redaction"
```

Ao lado de `CROSS_FRAGMENT_REDACTION`, mesma natureza: constante literal fixa, sem
template, sem posição, sem contagem embutida na string.

`_redacted_text` passou a devolver `tuple[str, bool]` em vez de `str`. O texto é
calculado **exatamente como antes** — span local primeiro, laço de propagação depois; o
booleano só registra se aquele laço, para aquele fragmento, encontrou e substituiu
alguma ocorrência. `render_block_text` acumula esse booleano com `or` por todos os
fragmentos do bloco (título, corpo, cada rótulo, cada valor) e acrescenta o marcador ao
final de `transformations` — depois de `CROSS_FRAGMENT_REDACTION`, se ambos se
aplicarem.

## 2. Por que sem contagem

O prompt pedia para avaliar uma variante como `"propagated_secret_redaction:3"`. Decidi
**não** incluir contagem, e o motivo é o próprio código já existente: toda a suíte usa o
idioma `MARCADOR in bloco["transformations"]` ou igualdade exata de lista
(`bloco["transformations"] == ["normalize", "redact", CROSS_FRAGMENT_REDACTION]`) para
reconhecer o marcador de travessia. Uma string com contagem variável quebra os dois —
`in` deixaria de casar, e a igualdade exata precisaria saber o número de antemão. Todo
consumidor futuro (o backend não tem nenhum hoje além de gravar no manifest, mas o
propósito do marcador é ser lido por humanos revisando um contexto que veio mais pobre
do que o esperado) passaria a precisar de `.startswith()`/parsing em vez de comparação
simples, por um ganho de diagnóstico que o próprio texto de [03] §4 já qualificou como
condicional ("se o formato suportar **sem ampliar escopo**"). Julguei que amplia.
`test_marcadores_sao_constantes_fixas_sem_dado_variavel` trava essa decisão.

## 3. O que a garantia do item 3 exigia, e como ela é mantida

O booleano nunca teve acesso a mais informação do que "houve substituição neste
fragmento" — ele não vê **qual** valor foi substituído, **onde**, nem **quantas** vezes.
Não há, em lugar nenhum do código novo, um `hash()`, um `len()` guardado, ou uma
referência ao valor propagado que sobreviva além do `bool`. A prova de que isso é
suficiente não é de leitura de código só: `test_marcador_nao_contem_nenhum_segredo_do_fixture`
(parametrizado com três fixtures, incluindo dois segredos simultâneos) confere, depois
de congelar o artifact de verdade, que **nenhuma** string de `transformations` contém
qualquer segredo do fixture como substring, nas duas direções — e que nenhum segredo é
substring de uma transformação (o inverso, para pegar o caso degenerado de um marcador
acidentalmente curto).

## 4. Verificação

- **Suíte de redação e determinismo das 5 rodadas (rounds 3, 4, 5, 5-metadata, router):
  132 passed**, nenhuma falha, nenhum teste alterado nas rodadas anteriores.
- **Suíte completa do backend: 1176 passed / 6 skipped** (era 1163/6; 1182 coletados). `ruff check` · `ruff format --check` · `mypy`
  limpos.
- **Prova de que o texto não mudou** (item 4/5 do gate):
  `test_texto_do_bloco_e_identico_ao_de_antes_desta_tarefa` reconstrói, com uma função
  escrita à parte (`_texto_do_fragmento_sem_rastreamento`, sem o booleano), o texto de
  cada fragmento a partir de `_build_fragments`/`_mark_fragments`/`_propagation_values`
  — as mesmas funções que `render_block_text` chama — e compara contra o
  `RenderedBlock` real, para os dois fixtures que ganham o marcador novo. Nenhuma
  divergência.
- **Prova de que o marcador não carrega segredo** (item 3 do "AO FINAL"):
  `test_marcador_nao_contem_nenhum_segredo_do_fixture`, três variantes.
- Os quatro cenários pedidos nos testes obrigatórios: marcador presente com propagação
  isolada, ausente com detecção direta isolada, ausente sem redação nenhuma, e os dois
  marcadores coexistindo — `test_ambos_marcadores_coexistem_sem_conflito`.

## 5. Uma armadilha na escolha do fixture "ambos os marcadores"

A primeira tentativa de reproduzir "cross-fragment **e** propagação juntos" usava uma
folha comum (`"nota"`) ao lado de `title="Bearer"` / `body=" ABCDEFGHIJKLMNOP"`. Ela
expôs — sem relação com esta tarefa — a extensão gulosa que `E5-AUD4-001` já aceitou
como comportamento esperado: na leitura de projeção canônica (e), o padrão `bearer`
continuou consumindo caracteres alfanuméricos através de **três** fragmentos
(`Bearer ABCDEFGHIJKLMNOPnotaref…`), comendo o rótulo `nota` inteiro. Não é um defeito
desta mudança — é a mesma política que a rodada 5 documentou e testou —, mas confundia
o que este teste precisava mostrar. Troquei o rótulo da folha para `"!nota"` (o mesmo
truque que os fixtures da própria rodada 5 já usam): o `!` não é alfanumérico, então a
extensão gulosa para exatamente na fronteira do corpo, e o teste isola limpo o efeito da
propagação. Registro isso porque quase virou um relatório de "encontrei um bug" que na
verdade era o comportamento já aceito, só mal escolhido no fixture.

## 6. Observação sobre `RENDERER_VERSION` e `rendered_context_hash`

`RENDERER_VERSION` **não** avançou — o pedido explícito era confirmar que não precisa,
já que nenhum byte de `text` muda. Isso é verdade. Mas vale registrar, porque não é
óbvio de fora: `transformations` **entra no payload que é hasheado**
(`build_rendered_payload` em `manifest.py`, `_block()`). Um bloco que passa a carregar
`PROPAGATED_SECRET_REDACTION` — porque agora existe propagação onde antes não havia
sinalização dela — produz um `rendered_context_hash` **diferente** do que produziria
antes desta mudança, com `renderer_version` idêntico. Não é regressão de conteúdo
(nenhum caractere de `text` mudou, só a lista de metadados), mas quem dependesse de
`rendered_context_hash` para detectar mudança de *conteúdo* especificamente precisa
saber que metadado participa do hash tanto quanto texto. Deixei um comentário no código,
ao lado de `RENDERER_VERSION`, registrando isso — não avancei a versão porque o pedido
foi explícito em não fazê-lo, mas a observação fica para quem decidir se essa distinção
merece um campo hasheado separado numa fase futura.

## 7. O que a rechecagem do Codex precisa confirmar

1. `_redacted_text` retornando `tuple[str, bool]` não introduziu nenhuma ordem de
   avaliação que dependa de iteração de `set`/`dict` não ordenada —
   `test_ordem_e_deterministica_quando_ambos_presentes` cobre isso para um caso, mas
   vale revisar o acumulador `propagated = propagated or hit` dentro de `final()` quanto
   ao determinismo geral;
2. a decisão do §2 (sem contagem) é aceitável, ou o ganho de diagnóstico justifica a
   complexidade de parsing que ela evitaria;
3. o §6 é só observação ou merece virar finding — nenhum código foi alterado para
   tratá-lo além do comentário.
