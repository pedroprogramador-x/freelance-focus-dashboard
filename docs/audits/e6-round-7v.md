# Verificação independente — E6-AUD7-001 REV2

## BLOCKED

**O scanner REV2 corrigiu o vazamento PEM e mostrou crescimento linear nas sondas independentes.** O bloqueio restante neste escopo é o teste de desempenho: ele reprovou a implementação atual em uma execução normal e passou nas repetições, tornando o gate instável.

Reproduzi o contraexemplo original. A regex canônica e o scanner atual retornaram, na mesma ordem, os intervalos `(0,52)`, `(69,157)` e `(174,226)`. `recognition_span` e `replacement_span` coincidiram com esses matches. Em **36.860 entradas próprias**, incluindo aberturas sobrepostas, múltiplos `BEGIN` e `PRIVATE KEY`, variações de traços e blocos válidos e inválidos, não encontrei diferença de quantidade, ordem, limites, texto ou grupos.

A estratégia de uma corrida maximal `[A-Z ]+` é consistente com a regex de corpo: um corpo válido precisa terminar em `PRIVATE KEY` imediatamente antes dos cinco traços; dentro da corrida, o primeiro `BEGIN` é o início preferido pela regex. Cada corrida é processada uma vez. As buscas de `_PEM_END` avançam por blocos disjuntos ou encerram na primeira falha. Medi crescimento próximo de **2× por dobra** em oito famílias, inclusive `BEGIN ` repetido, terminadores quase válidos, blocos grandes e aberturas sobrepostas com `END` distante. Para `BEGIN ` repetido, `redact()` levou **78,54 / 157,50 / 321,07 ms** em n/2n/4n. A primeira candidata, com regex de corpo, levou **26,86 / 107,37 / 429,78 ms** em tamanhos menores: aproximadamente **4× por dobra**. A versão da rodada 6 voltou a omitir o match `(69,157)` na reprodução em memória.

Num `POST /api/workspaces/{id}/tasks` real, o material `MIIE_AUD7_SYNTHETIC_PRIVATE_MATERIAL` não apareceu no objeto redigido, no JSON nem nos bytes crus. Quatro variações adicionais também ficaram mascaradas.

**E6-AUD7V-001 — Média/P2 — gate temporal reprova código linear de modo intermitente.** Em [test_e6_audit_round_7.py:781](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/tests/test_e6_audit_round_7.py:781), o limite `razão < 3,0` falhou na bateria focada: **3,41×**, com tempos de **123,16 / 419,78 / 694,30 ms**. A mesma implementação passou em quatro repetições isoladas do grupo de crescimento e na suíte completa. A amostra intermediária inflada explica a razão; as medições independentes e a inspeção do algoritmo não indicam custo superlinear. **Impacto:** o gate pode bloquear CI ou auditorias sem regressão real. **Critério de resolução:** tornar a medição estável sob variação normal de carga e demonstrar que ela ainda reprova a primeira candidata quadrática.

Gates: bateria focada **159 passed, 1 failed** pelo teste acima; quatro repetições isoladas dos cinco testes de crescimento **passaram**; suíte completa **1789 passed, 6 skipped**; `ruff check`, `ruff format --check`, `mypy` e `git diff --check` **passaram**. `git status --short` e `git diff --stat` foram conferidos ao fim: 38 arquivos rastreados modificados, 33 untracked físicos; o diff rastreado registra 4.311 inserções e 197 remoções. Não alterei arquivos nem fiz commit ou push.

**E6-AUD7-001 REMAINS BLOCKED** — apenas pela confiabilidade do teste necessário; a correção funcional e o custo linear da REV2 foram verificados.  
**E6-AUD7-002 REMAINS OPEN** — fora do escopo desta auditoria.
