# Auditoria independente E5 — rodada 2

Data: 2026-09-09. Auditor: Codex (GPT-6). Escopo: Context Router, file map, manifest e artifact, especialmente redação e duplicação de representações. Auditoria de leitura: nenhum código corrigido, nenhum commit realizado no workspace.

**Resultado: NÃO GREEN. Quatro findings Alta/P1 e um Média/P2 bloqueiam: E5-AUD2-001 a E5-AUD2-005.**

A afirmação de que os nove findings anteriores estavam corrigidos não se confirmou integralmente. Oito reproduções originais simples passaram. E5-AUD-007, de medição após a normalização final, continua reproduzível. A correção do segredo partido passa no caso mínimo, mas falha quando há outras folhas/redações. Também existem cópias não protegidas, inclusive no título propagado ao manifest, e vazamentos por chaves e contêineres de structured.

## 1. Contexto, escopo e estado auditado

Lidos: entrada completa de correção E5 no AGENT_LOG.md (início em linha 2267, grupos A–E, testes, decisões e pendências); docs/architecture/02-data-model.md §7 e §5; docs/architecture/03-context-architecture.md §4; ADR-0006 para verificar a obrigação, ou ausência dela, de JSON dentro do texto do bloco. As três notas “Fechado na correção de auditoria” foram comparadas com funções e testes reais. Elas estão em 02-data-model.md:340 e 03-context-architecture.md:245,278 — uma no primeiro documento e duas no segundo, e atualmente são parágrafos em negrito, não blockquotes Markdown.

O histórico da primeira rodada estava disponível nesta conversa. Sua reconstrução fiel foi gravada separadamente em [e5-round-1.md](e5-round-1.md), sem tratar a reconstrução como uma nova execução daquela versão.

`git status --short` completo antes de qualquer escrita desta auditoria:

```text
 M AGENT_LOG.md
 M api/app/context_engine/__init__.py
 M api/tests/context_helpers.py
 M docs/architecture/02-data-model.md
 M docs/architecture/03-context-architecture.md
?? api/app/context_engine/file_map.py
?? api/app/context_engine/manifest.py
?? api/app/context_engine/rendering.py
?? api/app/context_engine/selection.py
?? api/tests/test_context_router_e5.py
```

`git diff --stat` completo inicial:

```text
 AGENT_LOG.md                                 | 254 +++++++++++++++++++++++++++
 api/app/context_engine/__init__.py           | 105 +++++++++++
 api/tests/context_helpers.py                 |  51 +++++-
 docs/architecture/02-data-model.md           |  16 +-
 docs/architecture/03-context-architecture.md |  37 ++--
 5 files changed, 446 insertions(+), 17 deletions(-)
```

Os cinco arquivos untracked não entram nesse stat, mas foram auditados. Git também emitiu avisos de acesso negado a `C:\Users\pedro/.config/git/ignore` e avisos LF→CRLF para os cinco arquivos tracked modificados; os comandos produziram normalmente as listagens acima.

As únicas escritas autorizadas e realizadas no repositório por esta auditoria são este relatório, a reconstrução da rodada 1 e a entrada breve no AGENT_LOG.md. Scripts, bancos, artifacts e repositórios sintéticos de teste ficam fora do workspace. Os fixtures criam commits somente nos repositórios sintéticos, nunca no repositório auditado. Não houve edição de documentação de arquitetura.

## 2. Execução e evidência preservada

Ambiente: Windows, Python 3.12.14, pytest 8.4.2, ambiente já existente `%TEMP%/e4-aud6-venv`. Ativados `PYTHONDONTWRITEBYTECODE=1` e `-p no:cacheprovider` para evitar escrita de caches no repositório.

Suíte existente, executada de `api/`:

```text
python -m pytest tests/test_context_router_e5.py tests/test_architecture.py -p no:cacheprovider -o addopts='' -q
142 passed in 58.18s
```

São 45 testes E5 e 97 testes de arquitetura; não é uma execução da suíte backend completa.

Provas independentes: `20 passed in 42.72s`, mais `2 passed, 20 deselected in 2.36s`. Alguns asserts verificam expressamente a presença do defeito: “passed” no script de prova não significa que o produto passou no requisito.

Evidência suplementar durável nesta máquina, fora do repositório:

```text
C:/Users/pedro/.codex/visualizations/2026/09/09/01a086e1-754d-77c1-9a77-4af609dd6d66/e5-round2/
  test_round2.py
  results.txt
  additional.txt
  e5-round2-existing.txt
  before.json
```

Os fatos, entradas, saídas, linhas e recomendações necessários à revisão estão transcritos abaixo; o relatório não depende exclusivamente desses arquivos locais. Reexecução do script de prova, a partir de `api/`, com um Python que tenha as dependências do backend e pytest:

```powershell
$env:PYTHONPATH = (Get-Location).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
python -m pytest -c pyproject.toml -p tests.conftest -p no:cacheprovider 'C:/Users/pedro/.codex/visualizations/2026/09/09/01a086e1-754d-77c1-9a77-4af609dd6d66/e5-round2/test_round2.py' -s -o addopts='' -q
```

As entradas adversariais são aceitas por `create_entry`/`update_entry`, com schema migrado, e percorrem `select_context`, `freeze_manifest` e `render_context`. Foram inspecionados `ScoredEntry` tanto em ranked quanto em selected, payload retornado, JSON relido dos bytes do artifact e metadados do manifest. No ataque de cópia no título, todas as colunas do ContextManifest foram serializadas para inspeção. O registry autoral cru e as strings de entrada do redator não são confundidos com representações já protegidas: sua existência antes da redação é necessária e não é, por si, um finding.

## 3. Findings

### E5-AUD2-001 — Alta/P1 — Contagem global não demonstra proteção de cada fronteira

**Arquivo e linhas:** `api/app/context_engine/rendering.py:247–255`, especialmente `glued = body + "".join(...)` e `if actual_hits > expected_hits`.

**Reprodução executada A — uma redação independente mascara o segredo partido:**

```python
body = "sk-123456"
structured = {"a": "7890ABCDEF", "password": "hunter2"}
```

O controle sem o campo password redige ambas as metades corretamente. Com password, o texto real emitido contém:

```text
sk-123456

a: 7890ABCDEF
password: «redigido»
```

As metades aparecem em ranked, selected, payload e bytes em disco. Isoladamente, a folha password gera um marcador: `expected_hits=1`. Na leitura colada, o token recomposto gera um marcador: `actual_hits=1`. São ocorrências diferentes, mas a igualdade das contagens impede o fechamento da fronteira. A presença de uma redação em outro lugar não prova proteção do segredo que atravessa campos.

**Reprodução executada B — folha interposta impede testar a fronteira desejada:**

```python
body = "sk-123456"
structured = {"a": "!!!", "resto": "7890ABCDEF"}
```

A função testa `sk-123456!!!7890ABCDEF`, não a fronteira entre body e resto. Nenhuma detecção acontece; ambas as metades são emitidas. O docstring fala em colar body “a cada valor”, mas o código cola body a todos os valores de uma só vez, na ordem das folhas.

**Impacto:** a garantia de segredo partido não é estável diante da adição de um campo independente. A nota de fechamento em `03-context-architecture.md:278` descreve a implementação real, mas extrapola o que a heurística garante.

**Recomendação:** substituir a comparação global de quantidades por identificação de ocorrências e correspondência com os campos/trechos de origem. Uma ocorrência reconhecida deve marcar todas as suas partes para redação, independentemente de outras redações ou da ordenação de folhas não relacionadas. Manter um único dono de padrões de segredo, em safety; não criar outro conjunto de regex no renderer.

**Provas:** `test_adversarial_representations[split_masked_count]` e `[split_interposed]`.

### E5-AUD2-002 — Alta/P1 — O mesmo valor reconhecido como segredo permanece em outras cópias, inclusive origin e manifest

**Arquivo e linhas:** `api/app/context_engine/rendering.py:244–245` (redação independente sem propagar a identidade do valor reconhecido) e `:299` (título isolado). A propagação ocorre em `selection.py:596`, `manifest.py:117` e no método `ScoredEntry.as_manifest_entry`.

**Reproduções executadas:**

```python
body = "body"
structured = {"password": "hunter2", "copy": "hunter2"}
# resultado: copy: hunter2 / password: «redigido»

body = "hunter2"
structured = {"password": "hunter2"}
# resultado: body ainda contém hunter2; password está redigido

# Variante que também alcança os metadados:
title = "hunter2"
body = "body"
structured = {"password": "hunter2"}
```

Na terceira variante, `hunter2` permanece em `blocks[].text`, `blocks[].origin.title`, `ContextManifest.entries[].title`, ranked, selected e nos bytes do artifact. A folha password está redigida em todos esses casos. A inspeção das demais colunas do manifest não identificou outra cópia adicional.

**Impacto:** não reapareceu a duplicação automática exata da primeira tentativa de correção para uma única folha password. Porém a promessa mais forte do foco 1 — nenhuma segunda cópia esquecida do valor reconhecido — falha quando o registro já contém cópias em vários campos. Redigir cada ocorrência isoladamente não é suficiente para um valor que só é reconhecível pelo contexto de uma das ocorrências.

**Recomendação:** uma vez reconhecido um valor sensível dentro da entrada, proteger suas demais ocorrências autorais no escopo daquela entrada, incluindo título e rótulos propagados. Registrar/marcar ocorrências antes de emitir as representações finais, evitando criar uma cópia plana crua em paralelo à protegida.

**Provas:** `test_adversarial_representations[duplicate_password]`, `[duplicate_body]` e `test_duplicate_title_all_surfaces`.

### E5-AUD2-003 — Alta/P1 — Chave autoral é redigida temporariamente e reemitida crua como path

**Arquivo e linhas:** `api/app/context_engine/rendering.py:178`, `:205–212`, `:302`.

**Reprodução executada:**

```python
structured = {"sk-ant-" + "A" * 30: "ordinary"}
```

O token é reconhecível pelo redator padrão. `_redact_leaf` redige a linha que contém a chave, mas devolve somente a parte interpretada como valor. `leaves_alone` conserva o path original; a montagem final reintroduz integralmente o token:

```text
sk-ant-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA: ordinary
```

Ele aparece em ranked, selected, payload e bytes do artifact. É precisamente uma representação protegida descartada seguida de uma segunda representação não protegida emitida.

**Segunda reprodução, refutando a premissa documentada sobre path:**

```python
structured = {"password: hunter2": "ordinary"}
```

Entrada aceita pelo serviço. Saída real:

```text
password: hunter2: «redigido» ordinary
```

O docstring afirma que path nunca contém `": "` e sempre tem forma de identificadores e índices. O serviço não restringe chaves a essa gramática. `partition(": ")` corta na subsequência que o usuário forneceu e também corrompe o valor emitido, além de reintroduzir a credencial na chave.

**Recomendação:** tratar chaves e caminhos linearizados como conteúdo autoral não confiável. Propagar conjuntamente rótulo e valor já protegidos, sem recuperar campos por um delimitador que pode existir na entrada e sem reusar o path cru depois da redação.

**Provas:** `test_adversarial_representations[secret_key]` e `[colon_key]`.

### E5-AUD2-004 — Alta/P1 — Contêineres sob uma chave sensível perdem o contexto de segredo

**Arquivo e linhas:** `api/app/context_engine/rendering.py:178–184` e `:205–206`.

**Reproduções executadas:**

```python
structured = {"password": ["hunter2"]}
# texto: password[0]: hunter2

structured = {"password": {"current": "hunter2"}}
# texto: password.current: hunter2
```

Ambas sobrevivem em ranked, selected, payload e artifact em disco. Como controles, `{"password":"hunter2"}` e `{"auth":{"password":"hunter2"}}` foram corretamente redigidos.

**Causa:** o contexto efetivo do regex é a última parte da string linearizada. Inserir `[0]` ou `.current` depois de password destrói a adjacência exigida pelo padrão, a mesma classe de falha que a remoção das aspas JSON pretendia resolver. A chave sensível ancestral não é herdada pelas folhas.

**Recomendação:** carregar a classificação de sensibilidade através de objetos e listas, de forma estruturada, e proteger as folhas de um contêiner sensível. Índices e nomes de descendentes não devem apagar uma classificação já conhecida.

**Provas:** `test_adversarial_representations[password_list]` e `[password_nested_object]`.

### E5-AUD2-005 — Média/P2 — E5-AUD-007 continua aberto: o artifact normaliza depois da medição

**Arquivo e linhas:** `api/app/context_engine/rendering.py:278–281`, `api/app/context_engine/selection.py:603–604`, `api/app/context_engine/manifest.py:165`. Lacuna no teste citado como fechamento: `api/tests/test_context_router_e5.py:321–349`.

**Reprodução original executada novamente:**

```python
body = "e\u0301" * 100
transformations = (Transformation.REDACT,)
```

Foi executado `select_context`, seguido de `render_context`; o JSON foi lido dos bytes efetivamente gravados, sem depender de `RenderedContext.payload` em memória.

| Medida | Declarada no artifact | Texto relido dos bytes |
| --- | ---: | ---: |
| total_chars / emitted_chars | 233 | 133 |
| approx_tokens, pela fórmula do próprio módulo | 59 | 34 |

A omissão de NORMALIZE conserva NFD em `RenderedBlock.text`. `canonical_json` aplica NFC obrigatoriamente ao serializar o payload, depois de a seleção ter medido a string. A contagem continua sobre uma representação intermediária.

**Por que o teste novo passa:** só chama `render_block_text` e `approx_tokens`; não chama `select_context`/`render_context`, não grava/relê o artifact e inclui `assert len(rendered.text) == len(rendered.text)`. Ele demonstra aritmética sobre a string intermediária, não igualdade com a representação final. A afirmação de fechamento no AGENT_LOG não é sustentada por esse teste.

**Recomendação:** fazer toda normalização inevitável ocorrer antes da contagem e do corte de orçamento; ou tornar NORMALIZE obrigatório. O teste de regressão deve calcular comprimento e tokens do `blocks[].text` obtido por `json.loads(path.read_bytes())`, comparando com todos os metadados registrados.

**Prova:** `test_original_measurement`.

## 4. Confirmação dos nove findings originais

| Finding da rodada 1 | Resultado da reprodução nesta rodada |
| --- | --- |
| E5-AUD-001, paths NFC/NFD | Corrigido no cenário original: dois arquivos reais no mesmo commit, mesmos bytes/blobs, troca legítima de source_refs da mesma entrada produz manifests distintos. Mapas unitários NFC/NFD também distintos. Testadas adicionalmente identidades em source_files, covered e excluded: todas distintas. |
| E5-AUD-002, segredo reconhecível no título | Corrigido no cenário original: título `sk-ant-…` redigido em text, origin.title, entries.title e disco. Uma cópia de senha reconhecida em outro campo ainda vaza como título: E5-AUD2-002. |
| E5-AUD-003, password escalar em structured | Corrigido para `{"password":"hunter2"}`. Contêineres e cópias em outros campos continuam inseguros: E5-AUD2-002/004. |
| E5-AUD-004, segredo partido mínimo | Corrigido para body `sk-123456` e única folha `7890ABCDEF`. A adição de outro campo quebra a garantia: E5-AUD2-001. |
| E5-AUD-005, microssegundos | Corrigido: A com .100000, B com .900000, mesmo segundo/score, IDs A<B e orçamento para uma entrada; B venceu após flush e recarga do banco. |
| E5-AUD-006, título CRLF/LF | Corrigido na prova forte: a MESMA entrada foi atualizada de `Line A  \r\nLine B` para `Line A\nLine B`; content_hash, título propagado, manifest_hash e rendered_context_hash permaneceram consistentes/iguais entre as versões normalizadas. |
| E5-AUD-007, medição final | NÃO corrigido: reproduzido como E5-AUD2-005. |
| E5-AUD-008, precisão | Corrigido: candidato inexistente src/new.py; literal exato recebeu 131 adicionais e total231; glob src/** total130. Domínio/tags/frescor neutralizados, ambas unknown; o sinal exato foi 131 versus0. A vitória não depende de coincidência de outros scores. |
| E5-AUD-009, artifact corrompido | Corrigido: bytes no caminho do hash corrompidos de propósito; nova renderização devolveu written=True e restaurou SHA-256 correspondente ao nome/hash registrado. |

Não confundir a numeração reagrupada usada no texto da implementação com os findings originais: na primeira auditoria, E5-AUD-006 tratava do título cru e E5-AUD-007 da medição, não da codificação de paths. A reconstrução da rodada 1 conserva essa numeração original.

## 5. Matriz do ataque de representações

| Caso | Vazamento observado |
| --- | --- |
| Token reconhecível somente no body | Nenhum nas superfícies emitidas inspecionadas |
| Token reconhecível somente em structured | Nenhum |
| auth.password escalar aninhado | Nenhum |
| Mesmo token sk-ant completo repetido em duas chaves | Nenhum; ambas as cópias reconhecíveis foram redigidas |
| Segredo partido mínimo body/única folha | Nenhum |
| Segredo partido + outro password independente | Duas metades em ranked, selected, payload, disco |
| Segredo partido + folha de pontuação interposta | Duas metades em ranked, selected, payload, disco |
| password e copy com o mesmo valor | Cópia em copy persiste |
| body e password com o mesmo valor | Cópia no body persiste |
| title e password com o mesmo valor | Cópia no texto, origin.title, entries.title e disco persiste |
| password como lista ou objeto | Folhas sensíveis persistem |
| Token usado como chave de structured | Rótulo cru é reemitido |
| Chave contendo `password: hunter2` | Rótulo cru e corte incorreto por delimitador |

Nos ataques sem título sensível, as entradas do ContextManifest não carregam body/structured e não apresentaram os valores crus pesquisados. Isso NÃO protege o artifact referenciado por aquele manifest. No ataque de cópia no título, o próprio ContextManifest contém o valor cru.

## 6. Formato de structured: validade e efeitos colaterais

Nenhum dos documentos normativos lidos exige cerca JSON dentro de `blocks[].text`. O requisito de JSON em 02-data-model.md §5 aplica-se ao artifact exterior. `text` é string; `structured` continua sendo JSON no registry. A troca para linhas `caminho: valor` é válida como escolha de formato, e `renderer_version=e5.block.v2` distingue a mudança.

Os efeitos de segurança dessa escolha são concretos, não apenas potenciais: chaves cruas/delimitadores (E5-AUD2-003) e perda de contexto sensível de contêineres (E5-AUD2-004).

Também executei provas de legibilidade e perda de distinções na representação plana:

```text
{"a.b":1} e {"a":{"b":1}} -> mesmo texto a.b: 1
{"x":null} e {"x":""} -> mesmo texto x:
{"x":[]} e {} -> nenhum texto de structured
{"x":["first"]} e {"x[0]":"first"} -> mesmo texto x[0]: first
```

Uma lista de 12 itens é emitida na ordem lexicográfica de rótulos: índices 0,1,10,11,2,...,9. Isso é determinístico, mas piora a leitura de uma sequência de passos. O teste `test_structured_loss` confirmou todos esses casos.

Esses pares têm content_hash diferentes; não afirmo uma nova colisão de manifest ou artifact por causa do texto igual. Não encontrei consumidor implementado que faça parse de `blocks[].text` como JSON. Portanto não classifico a ausência de JSON ou a falta de round-trip como uma quebra de consumidor atual. São limitações que devem ficar explícitas: consumidores futuros devem usar o schema exterior/renderer_version, e uma eventual exigência de preservar estrutura, tipos ou ordem visual requer escaping de segmentos, marcadores de tipo/contêiner e preservação de índices numéricos. Não é necessário voltar a JSON para corrigir os findings de segurança, mas voltar a serializar antes de redigir reproduziria o defeito antigo.

## 7. Verificação das três notas de fechamento

| Nota normativa | Código/teste real e conclusão |
| --- | --- |
| 02-data-model.md:340 — identidades de caminho | `file_map.encode_path_identity` existe em linha35; as_canonical aplica hex a path/dir_path/extension. `manifest` codifica source_files, covered e excluded. Testes citados existem em test_context_router_e5.py:201 e :233 e passaram. O primeiro usa arquivos reais mas compara mapa completo com subconjunto; o segundo usa compute_manifest_hash com paths sintéticos. A prova independente desta rodada cobre adicionalmente a troca real de arquivos de mesmo blob na mesma entrada/commit. Fechamento de paths sustentado. A associação da nota a AUD-007 não fecha o problema de medição. |
| 03-context-architecture.md:245 — precisão | `_has_exact_literal_match` existe em selection.py:370; o peso131 é usado em :398–399. Teste citado existe em :684 e passou. Prova independente isolou o sinal com total231 versus130. Fechamento sustentado para o cenário exigido. |
| 03-context-architecture.md:278 — redação | Funções e testes citados existem e os exemplos mínimos passam: testes de título :459, password :519, split :546, duplicação da tentativa intermediária :580. Entretanto, a generalização “segredo partido fechado” e a ausência de representações cruas não se sustentam diante dos ataques novos. Nota precisa ser revista quando E5-AUD2-001..004 forem resolvidos; não foi editada nesta auditoria. |

## 8. Reprodutibilidade geral e isolamento

A prova independente executou build_file_map, select_context, freeze_manifest e render_context duas vezes sobre o mesmo estado, esvaziando o cache entre as construções. Repetiu a população com as mesmas entradas/IDs/timestamps em ordem de inserção inversa. Criou WorkspaceTasks diferentes. Comparou o pai com quatro subprocessos reais, usando PYTHONHASHSEED 0, 1, 12345 e 4294967295. SHA-256 do artifact foi relido em modo rb em todas as etapas.

Todos os resultados do cenário controlado foram idênticos:

```text
file_map  ce513f3605945dfd35179286a31120592b1bc5be7d05db594820d4527d5c70df
selection d34610813884cef297c4522729718b77f2dea81f654faed10512ff571753023b
manifest  358cc83f03f5eaae562bce67e7443a0572eca2b585a4b3e41d32b3439d5fce8d
rendered  a865b7ddcb7a869acdede6a95cd03f3fbd5411c05c65bf340d24e8d28f1dad9d
```

O digest de selection é auxiliar da auditoria, sobre uma forma JSON dos campos transitórios; não há campo selection_hash na aplicação. As outras identidades são as retornadas/persistidas pelo código. O commit sintético varia entre execuções novas dos fixtures; a comparação exige e usou o mesmo commit dentro de cada cenário.

Análise AST e busca em app confirmaram uma única definição de canonical_json (`app/safety/canonical.py:58`) e json.dumps somente nesse módulo (:70). Nenhum import de agent_runtime, tool_executor, orchestrator, api, fastapi ou starlette em context_engine. A suíte de arquitetura também passou. Lista vazia, None e ausência de REDACT foram recusados; inverter a ordem das transformações não mudou o resultado. Esses controles não eliminam o problema de NORMALIZE omitido descrito em E5-AUD2-005.

A exceção V1 de objective acima do orçamento permanece normativa e não é finding nem alvo de correção desta rodada.

Ao concluir, SHA-256 dos quatro módulos novos, do teste E5 e dos dois documentos de arquitetura permaneceu idêntico ao snapshot anterior aos testes (sete verificações). O status final acrescenta somente docs/audits/ e a entrada autorizada no AGENT_LOG ao conjunto inicial; nenhum outro arquivo foi escrito pela auditoria.

## 9. Veredito

**NÃO GREEN. Bloqueiam: E5-AUD2-001, E5-AUD2-002, E5-AUD2-003 e E5-AUD2-004 (Alta/P1, vazamentos de segredo), e E5-AUD2-005 (Média/P2, medição incompatível com os bytes finais; E5-AUD-007 não encerrado).**
