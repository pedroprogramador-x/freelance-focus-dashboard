# Reverificação E6-AUD7V-001

## 1. Veredito

**BLOCKED**

A REV2 continua funcionalmente correta e apresenta crescimento linear. Porém, o gate temporal ainda produziu um falso positivo sob carga razoável do sistema.

## 2. Auditoria da metodologia

A implementação de [`_tempo_mediano_ms` (line 770)](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/tests/test_e6_audit_round_7.py:770) foi inspecionada diretamente:

- O warm-up ocorre fora da janela medida e não modifica estado funcional da aplicação.
- `gc.collect()` ocorre antes do início de cada medição.
- A mediana de sete amostras é calculada corretamente.
- A conversão de nanossegundos para milissegundos, dividindo por `1e6`, está correta.
- `_razoes` protege contra divisão por zero.
- O alvo continua sendo o `redact()` real importado.
- Os testes funcionais, diferenciais, de fuzz, spans, sobreposição e HTTP continuam presentes e passaram.
- O controle negativo demonstra que o teste continua distinguindo a implementação quadrática em ambiente limpo.

A fragilidade remanescente está na execução sequencial por tamanho, usando wall-clock. Uma perturbação assimétrica que alcance quatro ou mais amostras de apenas um tamanho ainda desloca a mediana. Isso aconteceu na reprodução sob carga.

A decisão de manter `perf_counter_ns()` é razoável:

- `perf_counter_ns`: mínimo observado de 2.235 ns, mediana de 2.863 ns.
- `process_time_ns`: passos fixos de 15.625.000 ns.

`process_time_ns()` teria quantização excessiva para essas durações.

## 3. Distribuição REV2

Bateria limpa, usando o helper real, `redact()` real e dez tripletas independentes:

| Rodada | T(2n)/T(n) | T(4n)/T(2n) |
| --- | --- | --- |
| 1 | 1,8946 | 1,9911 |
| 2 | 1,9906 | 2,0998 |
| 3 | 1,9801 | 1,9832 |
| 4 | 1,9994 | 2,0254 |
| 5 | 2,0041 | 2,0353 |
| 6 | 2,0196 | 2,0379 |
| 7 | 2,0242 | 2,0123 |
| 8 | 1,9972 | 2,0796 |
| 9 | 1,7976 | 1,9849 |
| 10 | 2,0172 | 2,0143 |

- Mínimo: **1,7976×**
- Mediana: **2,0082×**
- Máximo: **2,0998×**
- Resultado limpo: **10/10 PASS**

Além disso, o grupo pytest completo de crescimento foi executado em dez processos independentes: **10/10 com `PYTEST_EXIT=0`**.

Segunda bateria com dois processos concorrentes de CPU em máquina de 16 CPUs:

| Rodada | T(2n)/T(n) | T(4n)/T(2n) |
| --- | --- | --- |
| 1 | **3,5066** | 1,1922 |
| 2 | 2,0136 | 1,9891 |
| 3 | 2,0598 | 1,9731 |
| 4 | 1,9954 | 2,0376 |
| 5 | 2,0473 | 1,9515 |
| 6 | 2,0072 | 2,1116 |
| 7 | 1,9929 | 2,0080 |
| 8 | 1,9812 | 1,9554 |
| 9 | 1,9984 | 2,0105 |
| 10 | 2,0060 | 2,0177 |

- Mínimo: **1,1922×**
- Mediana: **2,0066×**
- Máximo: **3,5066×**
- Resultado: **1/10 rodadas reprovaria código linear**.

Na rodada problemática, os tempos foram `98,199 / 344,347 / 410,535 ms`.

## 4. Distribuição candidata quadrática

Recriei a primeira candidata substituindo somente o localizador PEM pelo padrão `BEGIN[A-Z ]*PRIVATE KEY-----`; o restante do pipeline permaneceu real. Foi usada exatamente a metodologia atual: warm-up, sete amostras, mediana e limite 2,75.

| Rodada | T(2n)/T(n) | T(4n)/T(2n) |
| --- | --- | --- |
| 1 | 3,5710 | 3,7575 |
| 2 | 3,5494 | 3,8010 |
| 3 | 3,6216 | 3,7766 |
| 4 | 3,6234 | 3,7609 |
| 5 | 3,6147 | 3,7610 |
| 6 | 3,5461 | 3,7361 |
| 7 | 3,5750 | 3,7689 |
| 8 | 3,6374 | 3,7224 |
| 9 | 3,5606 | 3,7866 |
| 10 | 3,5398 | 3,8352 |

- Mínimo: **3,5398×**
- Mediana: **3,6799×**
- Máximo: **3,8352×**
- Resultado: **10/10 FAIL**, como esperado.

## 5. Avaliação do threshold

Em bancada limpa, `2,75` possui boa separação:

- Margem sobre o pior REV2 limpo: `2,75 − 2,0998 = 0,6502`.
- Margem abaixo da melhor razão quadrática: `3,5398 − 2,75 = 0,7898`.
- As distribuições limpas não se sobrepõem.

O controle negativo mostra baixo risco de falso negativo para a candidata quadrática. Entretanto, a bateria sob carga mostrou risco concreto de falso positivo: **3,5066×** para a implementação linear.

O problema não é o valor `2,75` isoladamente. A agregação de uma única tripleta sequencial ainda permite que uma fase de interferência concentrada em determinado tamanho decida o gate.

## 6. Gates

| Gate | Resultado |
| --- | --- |
| `test_e6_audit_round_7.py` | PASS, `PYTEST_EXIT=0` |
| Grupo de crescimento em 10 processos pytest | 10/10 PASS |
| Suíte completa do backend | 1.795 coletados; 1.789 passed, 6 skipped, `PYTEST_EXIT=0` |
| Suítes E5/E6/boundary/arquitetura | Incluídas na suíte completa, PASS |
| `ruff check .` | PASS |
| `ruff format --check .` | PASS, 108 arquivos |
| `mypy .` | PASS, 105 fontes |
| `git diff --check` | PASS, exit 0 |

## 7. Working tree

Estado final:

- 38 arquivos rastreados modificados.
- 26 entradas untracked no `git status`.
- `git diff --stat`: **4.459 inserções e 197 remoções**.
- `git diff --check`: limpo.

A subcorreção está limitada a teste e documentação/log. Nenhum fonte de produção foi alterado depois dela. Em particular:

- [redaction.py (line 132)](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/app/safety/redaction.py:132): última escrita às 19:09.
- [test_e6_audit_round_7.py (line 767)](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/tests/test_e6_audit_round_7.py:767): última escrita às 20:45.
- `AGENT_LOG.md`: última escrita às 21:28.
- `docs/audits/e6-round-7v.md`: última escrita às 20:28.

A árvore já continha o conjunto amplo de mudanças E6 antes desta auditoria. Nenhum arquivo foi corrigido por mim.

## 8. Findings

### E6-AUD7V2-001 — Média/P2 — gate temporal ainda apresenta falso positivo sob carga

**Arquivo/linha:** [api/tests/test_e6_audit_round_7.py (line 770)](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/tests/test_e6_audit_round_7.py:770), com limite em [`:767` (line 767)](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/tests/test_e6_audit_round_7.py:767).

**Reprodução:** em máquina de 16 CPUs, mantive dois processos CPU-bound concorrentes e executei dez tripletas pelo helper real. A primeira produziu `98,199 / 344,347 / 410,535 ms`, resultando em `3,5066× / 1,1922×`. O primeiro valor reprovaria o REV2 linear.

**Impacto:** um runner de CI com contenção assimétrica pode bloquear a integração sem regressão algorítmica. A mediana de sete protege contra perturbações curtas, mas não contra uma fase que contamine quatro ou mais amostras de um único tamanho.

**Critério exato de resolução:** agregar múltiplas tripletas independentes ou usar uma prova determinística de trabalho realizado, reduzindo a influência de uma fase isolada. A solução deve:

1. Passar 10/10 rodadas limpas com REV2.
2. Passar 10/10 sob o mesmo reproducer de carga.
3. Reprovar 10/10 rodadas da candidata quadrática.
4. Manter intactos os testes funcionais atuais.

## 9. Conclusão

**E6-AUD7-001 REMAINS BLOCKED**
**E6-AUD7-002 REMAINS OPEN**

Nada commitado.
Nada pushado.
