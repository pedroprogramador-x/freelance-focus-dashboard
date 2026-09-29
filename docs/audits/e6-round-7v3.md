# Reverificação E6-AUD7V2-001

## 1. Veredito

**GREEN**

A metodologia de tripletas passou todas as reproduções limpas e sob perturbação. A candidata quadrática foi rejeitada de forma consistente. Não reproduzi falso positivo ou falso negativo razoável.

## 2. Auditoria das tripletas

A implementação em [test\_e6\_audit\_round\_7.py (line 813)](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/tests/test_e6_audit_round_7.py:813) está correta.

### Balanceamento

[\`\_ORDEM\_DAS\_TRIPLAS\` (line 823)](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/tests/test_e6_audit_round_7.py:823) contém as seis permutações, mais uma repetição de `(n, 2n, 4n)`:
```
(0,1,2), (2,0,1), (1,2,0), (0,2,1),
(1,0,2), (2,1,0), (0,1,2)
```

Frequência de cada tamanho por posição:

| Tamanho1ª posição2ª posição3ª posição |   |   |   |
| ------------------------------------- | - | - | - |
| n                                     | 3 | 2 | 2 |
| 2n                                    | 2 | 3 | 2 |
| 4n                                    | 2 | 2 | 3 |

Com sete tripletas, divisão perfeitamente uniforme é impossível. A distribuição `3/2/2`, rotacionada entre os tamanhos, é balanceada e não deixa nenhum tamanho fixo numa única fase temporal.

### Matemática

Validei [\`\_razoes\_por\_triplas\_intercaladas\` (line 858)](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/tests/test_e6_audit_round_7.py:858) com três oráculos sintéticos:

- Tempos constantes `10/20/40`: sete razões `2/2`.
- Escala comum diferente em cada tripleta: razões permaneceram `2/2`.
- Razões deliberadas `1,7,2,6,3,5,4` e sua inversa: nenhuma foi perdida e ambas as medianas foram exatamente `4`.

Também confirmei que:

- cada array contém sete razões e sete tripletas de tempos;
- os tempos são armazenados na ordem lógica `(n, 2n, 4n)`, independentemente da ordem de medição;
- as divisões usam os elementos corretos;
- denominadores zero produzem `inf`;
- [\`\_mensagem\_de\_falha\_triplas\` (line 922)](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/tests/test_e6_audit_round_7.py:922) corresponde aos tempos e razões efetivamente calculados;
- `gc.collect()` ocorre antes de cada janela medida;
- os três warm-ups estão fora das janelas medidas;
- o alvo continua sendo `redact()` real.

## 3. REV2 limpa

Executei os dois testes de crescimento, incluindo os cinco casos parametrizados, em dez processos pytest independentes. Todos passaram.

Medianas da reprodução exata `"BEGIN " * n`:

| ExecuçãoT(2n)/T(n)T(4n)/T(2n)Resultado |          |          |      |
| -------------------------------------- | -------- | -------- | ---- |
| 1                                      | 1,991929 | 2,011939 | PASS |
| 2                                      | 1,986413 | 2,012751 | PASS |
| 3                                      | 1,976357 | 2,028612 | PASS |
| 4                                      | 2,013552 | 2,020939 | PASS |
| 5                                      | 2,010591 | 2,008209 | PASS |
| 6                                      | 1,921371 | 1,983863 | PASS |
| 7                                      | 2,016058 | 2,002528 | PASS |
| 8                                      | 2,007783 | 2,012835 | PASS |
| 9                                      | 1,883037 | 2,014897 | PASS |
| 10                                     | 2,068648 | 2,054555 | PASS |

**Resultado: 10/10 PASS.**

As outras cinco famílias também passaram nas dez execuções.

## 4. REV2 sob perturbação

### A. Carga CPU contínua

Oito processos CPU-bound, cinco rodadas:

- T(2n)/T(n): `1,899446–2,106699`.
- T(4n)/T(2n): `1,928889–2,112199`.
- Resultado: **5/5 PASS**.

### B. Rajadas localizadas

Usei doze processos por 450 ms, iniciados separadamente durante:

- uma amostra de `n`;
- uma amostra de `2n`;
- uma amostra de `4n`;
- a fronteira entre duas tripletas.

Resultado: **4/4 PASS**. Algumas razões individuais passaram de 2,75, inclusive `2,864×`, mas atingiram somente tripletas isoladas e não deslocaram a mediana.

### C. Perturbação independente

Duas rajadas de oito processos por 300 ms, iniciadas em tripletas distantes:

- três rodadas;
- todas passaram;
- maior mediana: `2,102733×`.

### Rajada longa

Quinze processos por 1,5 segundo, três rodadas:

| RodadaT(2n)/T(n)T(4n)/T(2n) |          |          |
| --------------------------- | -------- | -------- |
| 1                           | 1,889763 | 1,934896 |
| 2                           | 2,219351 | 1,970796 |
| 3                           | 1,983072 | 2,033800 |

Resultado: **3/3 PASS**.

A metodologia antiga, em blocos por tamanho, falhou **3/3** sob a mesma classe de rajada:
```
2,827589×
3,016079×
2,903866×
```

Isso reproduz independentemente a classe de E6-AUD7V2-001 e demonstra a melhora da agregação atual.

## 5. Controle negativo quadrático

O teste em [test\_e6\_audit\_round\_7.py (line 1030)](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/tests/test_e6_audit_round_7.py:1030) é legítimo:

- usa exatamente `BEGIN[A-Z ]*PRIVATE KEY-----`;
- substitui o localizador chamado dinamicamente por `_scan_pem_block`;
- não altera o catálogo nem o restante de `redact`;
- confirmei dinamicamente que o localizador monkeypatched foi chamado;
- o atributo foi restaurado corretamente;
- processos pytest independentes eliminam dependência de ordem.

Dez execuções pytest limpas:

| ExecuçãoT(2n)/T(n)T(4n)/T(2n) |          |          |
| ----------------------------- | -------- | -------- |
| 1                             | 3,908446 | 3,901866 |
| 2                             | 3,737348 | 3,990395 |
| 3                             | 3,595013 | 4,036926 |
| 4                             | 3,068656 | 4,052448 |
| 5                             | 3,593712 | 3,982147 |
| 6                             | 3,601224 | 3,986825 |
| 7                             | 3,462934 | 3,751802 |
| 8                             | 3,717446 | 3,917145 |
| 9                             | 3,662142 | 3,925110 |
| 10                            | 3,804216 | 3,674252 |

**Resultado: 10/10 PASS do teste**, isto é, 10/10 rejeições corretas da candidata quadrática.

Sob carga:

- carga contínua, cinco rodadas: **5/5 rejeitadas**;
- rajadas sobre `n`, `2n`, `4n` e fronteira: **4/4 rejeitadas**;
- duas rajadas separadas: **2/2 rejeitadas**.

Nenhuma perturbação fez a candidata quadrática parecer linear.

## 6. Distribuições e threshold

Distribuição agregada da reprodução exata:

| ImplementaçãoCenáriosRazõesMínimoMedianaMáximo |                                |    |          |          |          |
| ---------------------------------------------- | ------------------------------ | -- | -------- | -------- | -------- |
| REV2                                           | limpa + 15 rodadas perturbadas | 50 | 1,883037 | 2,012345 | 2,219351 |
| Quadrática                                     | limpa + 11 rodadas perturbadas | 42 | 3,068656 | 3,751746 | 4,052448 |

Para `_LIMIAR_RAZAO = 2.75`:

- margem acima do pior REV2: **0,530649**;
- margem abaixo da melhor quadrática: **0,318656**;
- separação entre as distribuições: **0,849305**;
- nenhuma sobreposição observada.

O limite continua discriminando comportamento algorítmico. Não encontrei motivo concreto para alterá-lo.

## 7. Testes funcionais

Permaneceram intactos e verdes:

- contraexemplo PEM original;
- igualdade entre scanner e regex canônica;
- diferenciais combinatórios;
- fuzz determinístico;
- spans e grupos;
- `recognition_span`;
- `replacement_span`;
- aberturas sobrepostas;
- HTTP fail-closed.

Nenhum código de produção foi alterado nesta subcorreção.

## 8. Gates

| GateResultado                               |                                                               |
| ------------------------------------------- | ------------------------------------------------------------- |
| Grupo de crescimento em 10 processos pytest | **10/10 PASS**                                                |
| Controle quadrático em 10 processos pytest  | **10/10 PASS**                                                |
| `test_e6_audit_round_7.py`                  | **35 passed**, `PYTEST_EXIT=0`                                |
| 14 suítes E5/E6/boundary/arquitetura        | `SUBSET_EXIT=0`                                               |
| Suíte completa do backend                   | **1.796 coletados; 1.790 passed, 6 skipped**, `PYTEST_EXIT=0` |
| `ruff check .`                              | PASS                                                          |
| `ruff format --check .`                     | PASS, 108 arquivos                                            |
| `mypy .`                                    | PASS, 105 fontes                                              |
| `git diff --check`                          | PASS                                                          |

## 9. Working tree

Estado final:

- 38 arquivos rastreados modificados.
- 27 entradas untracked, incluindo o novo relatório V2.
- `git diff --stat`: **4.615 inserções e 197 remoções**.
- `git diff --check`: limpo.

A correção de E6-AUD7V2-001 ficou limitada a:

- [api/tests/test\_e6\_audit\_round\_7.py (line 813)](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/tests/test_e6_audit_round_7.py:813);
- [\`AGENT\_LOG.md\`](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/AGENT_LOG.md);
- [\`docs/audits/e6-round-7v2.md\`](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/docs/audits/e6-round-7v2.md).

[redaction.py (line 132)](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/app/safety/redaction.py:132) permanece com última escrita em 18 de setembro, anterior aos três artefatos V2 de 21 de setembro. Nenhum fonte de produção recebeu nova alteração.

## 10. Findings

Nenhum finding novo.

## 11. Conclusão

**E6-AUD7-001 VERIFIED RESOLVED**\
**E6-AUD7V-001 VERIFIED RESOLVED**\
**E6-AUD7V2-001 VERIFIED RESOLVED**\
**E6-AUD7-002 REMAINS OPEN**

Nada commitado.\
Nada pushado.
