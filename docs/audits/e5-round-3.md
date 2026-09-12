# Auditoria E5 — rodada 3: fechamento de classe do pipeline de redação

Data: 2026-09-10. Auditor: Codex (GPT-6). Rodada curta, focada no redesenho em três camadas e nas decisões §2.5/§2.6. Nenhum código corrigido, nenhuma documentação de arquitetura alterada, nenhum commit realizado no workspace.

**Resultado: NÃO GREEN.** As reproduções históricas passaram, mas há um vazamento posicional novo (Alta/P1) e dois defeitos distintos de over-redaction (Média/P2). A recomendação técnica do Foco 2 é **(b): ajustar a implementação**, pelas razões abaixo; a decisão de aceitar riscos e alterar o contrato continua sendo de Pedro.

## 1. Contexto e execução

Lidos: [rodada 1](e5-round-1.md), [rodada 2](e5-round-2.md), [notas da implementação da rodada 3](e5-round-3-implementation-notes.md), especialmente §2.5 e §2.6; [03-context-architecture.md §4](../architecture/03-context-architecture.md); AGENT_LOG.md, incluindo a entrada de implementação de 2026-09-10; código completo de `api/app/safety/redaction.py` e `api/app/context_engine/rendering.py`; testes de regressão e travas de arquitetura.

Estado inicial completo (`git status --short`):

```text
 M AGENT_LOG.md
 M api/app/context_engine/__init__.py
 M api/app/safety/redaction.py
 M api/tests/context_helpers.py
 M api/tests/test_architecture.py
?? api/app/context_engine/file_map.py
?? api/app/context_engine/manifest.py
?? api/app/context_engine/rendering.py
?? api/app/context_engine/selection.py
?? api/tests/test_context_redaction_e5_round3.py
?? api/tests/test_context_router_e5.py
?? docs/audits/
```

`git diff --stat` completo inicial (não inclui os untracked, que também foram lidos):

```text
 AGENT_LOG.md                       |  38 ++++++
 api/app/context_engine/__init__.py | 105 ++++++++++++++
 api/app/safety/redaction.py        | 272 +++++++++++++++++++++++++++++++++++--
 api/tests/context_helpers.py       |  51 ++++++-
 api/tests/test_architecture.py     |  55 ++++++++
 5 files changed, 511 insertions(+), 10 deletions(-)
```

Git emitiu avisos de acesso negado ao ignore global e de conversão futura LF→CRLF; os comandos concluíram e produziram as listagens acima.

**Execução real:** Windows, Python 3.12.14, pytest 8.4.2, ambiente existente `%TEMP%/e4-aud6-venv`. De `api/`, com `PYTHONDONTWRITEBYTECODE=1`:

```text
python -m pytest tests/test_context_router_e5.py tests/test_context_redaction_e5_round3.py tests/test_architecture.py -p no:cacheprovider -o addopts='' -q
168 passed in 89.38s
```

Foram ainda executadas **10 provas independentes**: oito testes em 13,55 s, uma confirmação histórica forte em 4,46 s e uma prova de escalar genérico. Uma das provas contém uma comparação diferencial de 310 entradas com o `redact` histórico real obtido por `git show HEAD:api/app/safety/redaction.py`. Alguns asserts comprovam o defeito observado: o teste de prova passar não significa que o produto atende ao requisito.

Scripts e saídas ficaram fora do repositório e foram preservados em:

```text
C:/Users/pedro/.codex/visualizations/2026/09/09/01a086e1-754d-77c1-9a77-4af609dd6d66/e5-round3/
  test_r3.py
  results.txt
  historical.txt
  generic.txt
  e5-round3-existing.txt
  source-before-report.json
```

Reprodução do script a partir de `api/`, usando o Python do ambiente do backend:

```powershell
$env:PYTHONPATH = (Get-Location).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
python -m pytest -c pyproject.toml -p tests.conftest -p no:cacheprovider 'C:/Users/pedro/.codex/visualizations/2026/09/09/01a086e1-754d-77c1-9a77-4af609dd6d66/e5-round3/test_r3.py' -s -o addopts='' -q
```

As provas usam entradas aceitas por `create_entry`, migrations, bancos e artifacts temporários. As verificações de superfície percorrem seleção → freeze → bytes reais do artifact, incluindo text, origin.title e entries.title. Commits dos fixtures ocorrem exclusivamente em repositórios sintéticos no TEMP.

## 2. Findings

### E5-AUD3-001 — Média/P2 — Pares artificiais classificam conteúdo legítimo como segredo

**Arquivo/linha:** `api/app/context_engine/rendering.py:356–365` (pares ordenados e marcação integral); a projeção inteira em `:375–387` também pode contribuir.

**Reprodução executada — Foco 1:**

```python
body = "O prefixo de SKU usado pelo catalogo e sk-"
structured = {"alfabeto": "ABCDEFGHIJKLMNOP"}
```

O primeiro fragmento documenta um prefixo de SKU público; o segundo é uma sequência alfabética pública. `detect_secret_spans(body)` e `detect_secret_spans("ABCDEFGHIJKLMNOP")` devolvem vazio. A concatenação artificial `body + "ABCDEFGHIJKLMNOP"` casa o padrão openai_key, apesar de não existir uma credencial nos dados do cenário.

A seleção/artefato real emite:

```text
### contexto · stack

## titulo comum

«redigido»

<chave protegida>: «redigido»
```

O texto legítimo do corpo e o alfabeto desapareceram; a projeção também alcança o rótulo. O registry original não é apagado, mas o conhecimento entregue ao Developer é perdido. `_is_greedy_extension` não evita esse caso: nenhuma detecção existia isoladamente, portanto `own_starts` é vazio.

**Causa:** o par sintético recebe a mesma força probatória de uma sequência existente no conteúdo autoral. Duas strings públicas podem ser indistinguíveis, para esse algoritmo, das mesmas strings usadas deliberadamente como metades de uma credencial. Não existe informação suficiente em seus bytes isolados para resolver essa ambiguidade universalmente.

**Recomendação:** restringir quais relações/concatenações constituem evidência de um segredo dividido, usando contexto ou proveniência de composição, e separar detecção confirmada de hipótese conservadora. Se Pedro optar por aceitar qualquer par como hipótese suficiente, o apagamento de conteúdo público deve ser um trade-off explícito do produto, não descrito como ausência de falso positivo. Múltiplas leituras são compatíveis com um motor único, mas a política de pares irrestritos precisa mudar ou ser aceita conscientemente. Apenas reduzir a marcação ao span diminui o apagamento colateral, mas não elimina o falso positivo.

**Prova:** `test_false_positive`.

### E5-AUD3-002 — Alta/P1 — Um prefixo em outro fragmento faz reconhecer o segredo, mas o renderer descarta a detecção

**Arquivo/linha:** `api/app/context_engine/rendering.py:361` e `:377–378`. Contrato relevante do motor: `api/app/safety/redaction.py:201` devolve início após o prefixo preservado.

**Reprodução executada:**

```python
title = "Bearer"
body = " ABCDEFGHIJKLMNOP"  # credencial sintética, sem chave sensível em structured
structured = None
```

Nenhum fragmento isolado casa. A leitura combinada `Bearer ABCDEFGHIJKLMNOP` é reconhecida pelo motor único e produz `SecretSpan(start=7, end=23, pattern_name="bearer")`. A fronteira entre os fragmentos fica na posição6.

O span cobre somente o valor a substituir: o motor preserva `Bearer `, como deve fazer para manter o comportamento histórico de `redact`. Logo `span.start < boundary < span.end` é falso, e o par descarta uma detecção nova que depende de contexto no outro fragmento. A projeção completa também a descarta porque `len(covered) == 1`. Não existe detecção isolada que a substitua.

Texto efetivamente persistido:

```text
### contexto · stack

## Bearer

 ABCDEFGHIJKLMNOP
```

O valor completo permanece nos bytes do artifact. Não se trata de uma cauda depois de um token já reconhecido: **não havia token completo no primeiro fragmento, e nenhum caractere do valor foi redigido**. Também não exige três fragmentos fora da ordem canônica. Portanto os limites residuais1/2 declarados não descrevem este caso.

**Causa:** “intervalo do valor a substituir” e “intervalo de contexto necessário para reconhecer o segredo” foram tratados como se fossem a mesma coisa. Para padrões com prefixo preservado, são diferentes. A classe é de detecção contextual, não uma nova regex nem uma variação de contagem.

**Recomendação:** preservar a proveniência/contexto do match separadamente do intervalo de substituição, ou mapear detecções contextuais novas para o fragmento do valor mesmo quando o intervalo redigível não cruza a fronteira. Manter o texto de `redact()` inalterado; corrigir o consumo dos spans pelo renderer. Aplicar também a correção à projeção inteira e combiná-la com a política de evidência recomendada em E5-AUD3-001, para não ampliar falsos positivos indiscriminadamente.

**Prova:** `test_prefix_only_boundary`.

### E5-AUD3-003 — Média/P2 — Span local contorna a exclusão de escalares genéricos da propagação

**Arquivo/linha:** `api/app/context_engine/rendering.py:434–437`, em `_propagation_values`; origem do span local na leitura `caminho: valor`, `:334–349`.

**Reprodução executada na avaliação do limiar:**

```python
body = "Contagem publica: 123456 itens."
structured = {"password": 123456, "quantidade": 123456}
```

A regra normativa e §2.3 das notas determinam que o número sob password seja redigido localmente, sem tornar outros números iguais sensíveis. Entretanto `123456` desaparece também do corpo público e de quantidade.

`fragment.propagatable=False` é consultado ao adicionar um fragmento inteiro. O laço seguinte, que adiciona recortes de spans locais, não consulta esse atributo. A linha `password: 123456` gera um span de assigned_secret com seis caracteres; ele entra por essa segunda porta no conjunto global de propagação. A comparação de seis caracteres com o teste existente de42 explica por que o teste atual não detecta o problema.

**Impacto:** over-redaction de métricas/contagens públicas, contrariando uma exceção explícita. É uma causa distinta da concatenação artificial do finding001: existe uma atribuição real, mas o tipo numérico que deveria limitar seu alcance é perdido em um dos caminhos de inserção no conjunto.

**Recomendação:** aplicar a exclusão por `propagatable` a todas as origens de valores para propagação, inclusive spans locais. Manter a redação local do número sob a chave sensível. Adicionar regressão com número de pelo menos seis dígitos em campo sensível e em ocorrências públicas independentes.

**Prova:** `test_generic_scalar_local_span`.

## 3. Fechamento das três causas raiz — um caso novo por causa

| Causa | Ataque novo executado | Resultado |
| --- | --- | --- |
| Estrutura perdida | `connections=[{"dbPassword":{"versions":[[{"payload":"r3-confidential-value"}]]}}]`, com folha pública fora da subárvore | A marca camelCase/ancestral atravessou listas e objetos; segredo ausente de todas as superfícies; `keep-visible` permaneceu. |
| Posição/ordem artificial | Folha `a="EFGHIJKLMNOP"`, `middle="!!!"`, `z="ghp_ABCD"`: o token GitHub só se recompõe no par z→a, inverso à ordem canônica | Ambas as partes foram redigidas; a folha interposta permaneceu legível. Não houve dependência de quantidades. |
| Cópia sem rótulo | Valor opaco `r3-secret-opaque` em auth.apiKey e copiado para título, duas ocorrências no corpo e duplicate.text | Nenhuma cópia sobreviveu, inclusive origin.title e entries.title. |

Esses resultados sustentam o avanço estrutural do redesenho. **Não autorizam afirmar fechamento irrestrito da classe posicional**, refutado pelo finding002, nem da política de propagação de tipos, refutada pelo finding003. A redação de chaves com separador/token e os exemplos antigos também passaram na suíte de regressão.

## 4. `_is_greedy_extension` e limites residuais

Executei o limite residual2 declarado:

```python
body = "sk-ant-ABCDEFGH"
structured = {"resto": "IJKLMNOP"}  # continuação real do mesmo segredo no cenário
```

O prefixo reconhecível completo foi redigido, e `IJKLMNOP` sobreviveu. A implementação faz exatamente a escolha documentada: basta o início do span já existir em `own_starts` para rejeitar a marcação do vizinho. O teste existente que preserva um título legítimo ao lado de um token completo também passou.

Não encontrei, nesta rodada curta, outro defeito **exclusivo da função** `_is_greedy_extension` que justifique uma varredura adicional. Mas não é correto concluir que os limites declarados esgotam o under-redaction do pipeline: E5-AUD3-002 ocorre **antes** da consulta à heurística, na condição de atravessamento. O caso novo perde o valor inteiro, não apenas sua continuação.

O limite residual1 (três ou mais fragmentos em permutação não canônica) permanece declarado, não foi reexplorado nesta rodada. O residual2 e o limiar de propagação também precisam ser descritos como exceções ao invariante absoluto de “nenhum valor cru”: são escolhas de risco, não provas de impossibilidade de vazamento. Sua aceitação pertence a Pedro.

## 5. Verificação rápida do restante

**14 findings históricos.** As reproduções nomeadas das rodadas1/2 passaram nos 168 testes. Para não depender dos testes mais fracos da versão intermediária, reexecutei também as provas fortes de troca de título CRLF/LF na MESMA entrada e de troca de source_refs entre dois arquivos Git NFC/NFD reais com o MESMO blob no MESMO commit: passaram. Esse complemento também confirmou segredo no título, password escalar, split mínimo e reconstrução de artifact corrompido.

| Grupo histórico | Confirmação atual |
| --- | --- |
| R1-001, identidade de caminhos | Hashes distintos para NFC/NFD, inclusive prova real de mesmo blob. |
| R1-002/003/004, título/structured/split | Ausência de segredo nas superfícies e bytes nos exemplos originais. |
| R1-005, recência | Teste de microssegundos passou. |
| R1-006, normalização do título | Mesma entrada atualizada, hashes e título propagado consistentes. |
| R1-007 e R2-005, medição | Teste substituto lê JSON dos bytes gravados e compara caracteres/tokens; passou com REDACT sem NORMALIZE. |
| R1-008, precisão | Literal exato para candidato inexistente vence glob com outros sinais controlados. |
| R1-009, corrupção | Blob corrompido é reescrito. |
| R2-001/002/003/004 | Folha interposta, redação adicional, cópias, chaves inseguras e descendentes de contêineres sensíveis passaram nas regressões. |

**Compatibilidade de `redact()`.** A metodologia diferencial de §2.1 é sólida: comparar com o algoritmo anterior, explorar concatenações e sobreposições, detectar divergências reais (253) e repetir o mesmo corpus após preservar a cascata. O mapa de coordenadas original e a distinção entre spans adjacentes e sobrepostos são relevantes à equivalência. Não refiz os7.214 casos nem converti a alegação em prova formal: a semente/corpus completo daquele ensaio não está persistido nos arquivos lidos. Acrescentei uma checagem independente pequena, de **310 casos, zero divergências UTF-8**, contra a função original carregada diretamente do Git HEAD. Isso sustenta a compatibilidade observada, sem prometer equivalência universal a partir de um corpus finito.

**Motor único.** Confirmados `_SENSITIVE_KEY_WORDS` único, alternância de assigned_secret derivada dele e uma coleção `_PATTERNS` em safety/redaction.py. `redact` consome detect_secret_spans; rendering não duplica regex nem classificação de nomes. As travas de arquitetura passaram. “Um padrão” significa um catálogo central de padrões, não um único regex; matching da gramática de globs e substituição literal de valores conhecidos não são um segundo motor de segredo.

**Limiar6.** Razoável como trade-off de evitar apagar palavras curtas: cinco caracteres não propagam; seis propagam — ambos executados. Isso preserva cópias de senhas curtas fora do campo marcado e precisa ser aceito como limite explícito. A exceção adicional de números/bools/null é sensata, porém não está integralmente implementada: finding003.

**Tetos.** Verificados127 folhas/256 fragmentos aceitos (0,775 s com strings curtas) e128 folhas/258 fragmentos recusados por bloco inteiro protegido (0,001 s). Profundidade32 aceita e33 protegida. Os números são razoáveis para estruturas de contexto típicas e para fallback conservador. Contudo limitam quantidade/profundidade, não tamanho das strings nem tempo total: n(n−1) já dá65.280 pares no limite, cada um processando seus bytes. A medição de ≈1 s das notas não é teto universal de latência. Não expandi esta rodada para benchmark de grandes textos nem classifiquei essa ressalva como finding não reproduzido.

**Determinismo e isolamento.** Regressões de repetição, subprocessos/seeds e arquitetura passaram. Não houve indicação de um segundo canonical_json ou de dependência de provider introduzida pelo redesenho. A exceção V1 que inclui objective inteira acima do orçamento continua correta e não foi tratada como defeito.

## 6. Recomendação explícita do Foco 2 e veredito

**Recomendação técnica: (b), mudar a implementação antes de considerar o desvio seguro.** O problema não é executar mais de uma leitura: um conjunto monotônico de marcas, alimentado pelo mesmo motor, é conceitualmente melhor do que comparar contagens e pode ser documentado dessa forma. Porém a aceitação irrestrita de pares transforma coincidência em evidência (001), enquanto a interpretação do intervalo redigível descarta evidência contextual real (002). Trocar apenas “uma única vez” por “um conjunto único de marcas” não resolve nenhum desses efeitos.

Depois de definir a política de composição e corrigir o consumo de spans, recomendo alinhar §4 com o algoritmo efetivo: **um conjunto único de marcas alimentado por leituras que não comparam quantidades**, explicitando relações aceitas, limites de detecção e risco de over-redaction. Não recomendo retornar cegamente a uma única projeção, que reabriria a folha interposta. A escolha entre restringir as composições e aceitar deliberadamente o apagamento conservador é de Pedro; esta auditoria não alterou essa decisão nem a documentação normativa.

**Veredito: NÃO GREEN — bloqueiam E5-AUD3-001 (Média/P2, falso positivo por pares artificiais), E5-AUD3-002 (Alta/P1, segredo contextual reconhecido mas emitido) e E5-AUD3-003 (Média/P2, propagação indevida de escalar genérico). Recomendação do Foco 2: (b), ajuste da implementação; decisão final de política e documentação reservada a Pedro.**
