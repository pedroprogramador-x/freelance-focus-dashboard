# Auditoria E6 — Rodada 7

## 1. Veredito

### BLOCKED

**A auditoria terminou. A E6 ainda não pode ser fechada.** Reproduzi dois vazamentos de credenciais sintéticas em rotas HTTP reais: um bloco PEM que o novo scanner omite e um valor sob uma chave `token` em `structured`. Há também uma divergência entre o comportamento de `_ANCHORED` e a garantia escrita na arquitetura. Nenhum arquivo do projeto foi corrigido, commitado ou enviado.

## 2. Verificação E6-AUD6-001

A otimização reduziu o custo do caso original de muitos `BEGIN` sem `END`. Medi `redact()` em entradas de 47.250, 94.500 e 189.000 caracteres: **26, 55 e 109 ms**. Também medi prefixos quase válidos, 2.500 blocos pequenos válidos, bloco grande, aberturas dentro do conteúdo, terminadores quase válidos e `END` distante. Os tempos observados cresceram aproximadamente com o tamanho da entrada. O `re.Match` emitido pelo scanner vem da expressão canônica.

**A equivalência semântica, porém, falhou.** A enumeração não sobreposta de `_PEM_BEGIN.finditer` omite uma abertura que começa nos traços finais de outra. Nesta entrada sintética:

```python
B = "-----BEGIN PRIVATE KEY-----"
E = "-----END PRIVATE KEY-----"
p = "MIIE_AUD7_SYNTHETIC_PRIVATE_MATERIAL"
u = lambda x: "BEGIN PRIVATE KEY-----" * 2 + x + E
texto = B + E + u(p) + u("")
```

A regex canônica retorna os intervalos **`(0,52), (69,157), (174,226)`**. O scanner retorna **`(0,52), (152,226)`**. O detector final conserva a omissão; `p` permanece em `redact(texto)` e no corpo de `RedactingJSONResponse`. Em 5.000 composições adicionais geradas independentemente com aberturas sobrepostas, encontrei **339 divergências** entre scanner e regex canônica. Um `POST /api/workspaces/{id}/tasks` real retornou `201` com `p` ainda visível em `goal`.

**Conclusão:** E6-AUD6-001 está resolvido quanto ao custo medido, mas sua correção introduziu **E6-AUD7-001**, um bloqueador de confidencialidade.

## 3. Verificação E6-AUD6-002

A quarta cláusula calcula posições protegidas como **rótulo menos carga sensível**. O catálogo do oráculo é reescrito literalmente, sem importar os spans do detector; isso dá independência aos padrões. A expansão de âncoras em ponto fixo, porém, repete a premissa algorítmica do motor, portanto a independência não é total.

Executei o GATE 1 inteiro contra **dez mutantes em memória**: campo inteiro oculto, apenas rótulo oculto, início e fim deslocados, expansão à esquerda e à direita, união de spans, remoção de span legítimo, spans em posições erradas e emissão aparentemente segura com rótulo destruído. **Os dez foram rejeitados.** A correção captura a regressão específica de E6-AUD6-002.

Três mutações adicionais que ocultavam pontuação pública em um caso já autorizado a divergir **passaram**. Isso não apaga um rótulo protegido e não descreve o comportamento atual, mas mostra que o gate ainda não prova precisão de redação dentro desses casos; registro o limite em E6-AUD7-004.

## 4. `_ANCHORED`

1. **Antes:** não existia em `HEAD`; a rodada 4 havia alterado expressões para tratar adjacência.
2. **Mudança:** a rodada 5 restaurou o catálogo canônico e criou variantes derivadas que removem `\b` nas bordas para buscas em âncoras.
3. **Motivo:** a cascata canônica sozinha não recuperava o segundo segredo quando o primeiro consumia seu prefixo.
4. **Origem:** responde aos findings de adjacência AUD4-001 e AUD5-001/002; o próprio `AGENT_LOG.md` registra a variante como desvio a revisar.
5. **Efeito observável:** sim. Ela amplia o que é redigido, embora não altere as expressões da primeira passada.
6. **Ampliação:** `AKIA0123456789ABCDEFx` vira `«redigido»x`, apesar de a regex canônica, com `\b` final, não reconhecer a chave nesse texto.
7. **Falso negativo próprio:** não identifiquei um causado por `_ANCHORED`; a passada canônica permanece. O falso negativo PEM é do scanner.
8. **Over-redaction:** sim, no exemplo AWS seguido de letra comum.
9. **Garantia alterada:** [04 §5](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/docs/architecture/04-safety-and-git-runtime.md:327) afirma que o relaxamento depende de **outra ocorrência reconhecida** e que segredo colado a palavra comum permanece fora. O exemplo contradiz essa frase.
10. **Documentação:** exige correção normativa caso essa ampliação seja aceita.
11. **Teste:** [test_e6_audit_round_5.py](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/tests/test_e6_audit_round_5.py:314) fixa deliberadamente o comportamento ampliado; o teste não elimina a contradição documental.
12. **Decisão:** **aceito a estratégia de âncoras para adjacência, mas não considero o desvio encerrado**. Revertê-la integralmente reabriria findings anteriores. É preciso alinhar comportamento e arquitetura, ou aprovar explicitamente o novo contrato, antes do fechamento.

## 5. Auditoria transversal da E6

Revisei Analyzer, Resource Router, Planner, máquina de estados, fingerprint, gerenciador de execução, fronteira HTTP, imports e consumidores do redator, além dos testes E4/E5/E6. O Analyzer conserva `final_risk = max(hard_rule_risk, llm_risk)` e delega paths secretos a `safety`; o Router é determinístico e não executa recursos; o Planner reutiliza `select_context` e `freeze_manifest`. As transições e aprovações passam pelo CAS, e o fingerprint inclui plano, manifest, contexto renderizado, base Git, bindings, políticas, agentes e limites. Não encontrei import de `api` no domínio `orchestrator`, execução de shell nova fora de `git_runtime/`, nem segunda implementação de `canonical_json` ou do redator recursivo.

A varredura revelou a segunda falha de saída. `is_sensitive_key("token")` retorna `True`, e o renderizador E5 usa essa classificação para proteger a subárvore. Já [redact_document](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/app/safety/redaction.py:902) redige cada chave e valor isoladamente, sem propagar a sensibilidade da chave. Com SQLite e Git temporários, `POST /api/workspaces/{id}/context` retornou `201` e `GET /api/workspaces/{id}/context` retornou `200`, ambos contendo:

```json
{"structured":{"token":"AUD7_SYNTHETIC_CREDENTIAL"}}
```

O limite do detector não justifica tratar essa chave como desconhecida: o próprio catálogo estrutural a classifica como sensível. Os testes completos não cobrem essa diferença entre artefato E5 e resposta HTTP.

## 6. Gates executados

| Gate | Resultado real |
| --- | --- |
| Backend, `python -m pytest -p no:cacheprovider -o addopts='' -q` | **1755 passed, 6 skipped**, 0 failed, em 11m22s |
| `ruff check .` | Passou |
| `ruff format --check .` | Passou; 107 arquivos formatados |
| `mypy .` | Passou; 104 fontes |
| Frontend, `npm run lint` | Passou |
| Frontend, `npm test` | **130 testes**, 19 arquivos, passaram |
| Frontend, `npm run build` | Passou; 1.607 módulos transformados |
| `git diff --check` | Passou |

A suíte completa inclui os testes adversariais e de determinismo E4/E5/E6; confirmei também `RENDERER_VERSION = "e5.block.v7"`. **O número 1755 foi confirmado independentemente.** O [AGENT_LOG.md](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/AGENT_LOG.md:4211) ainda registra **1754**.

## 7. Working tree

`git status --short` ao fim da auditoria:

```text
 M AGENT_LOG.md
 M api/app/api/context.py
 M api/app/api/web.py
 M api/app/api/workspaces.py
 M api/app/context_engine/__init__.py
 M api/app/context_engine/content_hash.py
 M api/app/context_engine/errors.py
 M api/app/context_engine/rendering.py
 M api/app/context_engine/service.py
 M api/app/context_engine/source_ref_expansion.py
 M api/app/db/models.py
 M api/app/db/session.py
 M api/app/main.py
 M api/app/safety/__init__.py
 M api/app/safety/redaction.py
 M api/app/workspace/__init__.py
 M api/app/workspace/errors.py
 M api/app/workspace/purge.py
 M api/app/workspace/purge_tokens.py
 M api/app/workspace/service.py
 M api/tests/test_api_context.py
 M api/tests/test_api_workspaces.py
 M api/tests/test_architecture.py
 M api/tests/test_auth_and_bootstrap.py
 M api/tests/test_context_audit_e4_round1.py
 M api/tests/test_context_redaction_e5_round5.py
 M api/tests/test_context_service.py
 M api/tests/test_context_verification.py
 M docs/architecture/04-safety-and-git-runtime.md
 M src/pages/WorkspaceContext.tsx
 M src/pages/WorkspaceDetail.tsx
 M src/services/contextApi.ts
 M src/services/workspaceApi.ts
 M src/test/context-entries.test.ts
 M src/test/dev-workspaces-ui.test.tsx
 M src/test/workspace-context-ui.test.tsx
 M src/test/workspace-detail-ui.test.tsx
 M src/utils/contextEntries.ts
?? api/app/api/responses.py
?? api/app/api/tasks.py
?? api/app/db/conflicts.py
?? api/app/orchestrator/
?? api/app/safety/objective_signals.py
?? api/app/safety/test_policy.py
?? api/app/workspace/policy.py
?? api/migrations/versions/0002_workspace_test_config.py
?? api/tests/test_api_redaction_boundary.py
?? api/tests/test_api_tasks.py
?? api/tests/test_e6_audit_round_1.py
?? api/tests/test_e6_audit_round_2.py
?? api/tests/test_e6_audit_round_3.py
?? api/tests/test_e6_audit_round_4.py
?? api/tests/test_e6_audit_round_5.py
?? api/tests/test_e6_audit_round_6.py
?? api/tests/test_orchestrator_analyzer.py
?? api/tests/test_orchestrator_planner.py
?? api/tests/test_orchestrator_router_and_fingerprint.py
?? api/tests/test_orchestrator_state_machine.py
?? src/pages/WorkspaceTasks.tsx
?? src/services/tasksApi.ts
?? src/test/task-detail-ui.test.tsx
```

`git diff --stat` completo:

```text
 AGENT_LOG.md                                   | 605 +++++++++++++++++++++++
 api/app/api/context.py                         | 111 ++++-
 api/app/api/web.py                             |   9 +-
 api/app/api/workspaces.py                      |  91 +++-
 api/app/context_engine/__init__.py             |  14 +
 api/app/context_engine/content_hash.py         |  44 ++
 api/app/context_engine/errors.py               |  56 +++
 api/app/context_engine/rendering.py            |   8 +-
 api/app/context_engine/service.py              |  90 ++++-
 api/app/context_engine/source_ref_expansion.py |  78 +++-
 api/app/db/models.py                           |  10 +
 api/app/db/session.py                          |  25 +
 api/app/main.py                                | 193 ++++++++-
 api/app/safety/__init__.py                     |  27 +-
 api/app/safety/redaction.py                    | 620 +++++++++++++++++++++---
 api/app/workspace/__init__.py                  |  11 +-
 api/app/workspace/errors.py                    |  17 +
 api/app/workspace/purge.py                     |   4 +-
 api/app/workspace/purge_tokens.py              |  58 ++-
 api/app/workspace/service.py                   |  35 ++
 api/tests/test_api_context.py                  |  48 ++-
 api/tests/test_api_workspaces.py               |   4 +
 api/tests/test_architecture.py                 | 645 +++++++++++++++++++++++++
 api/tests/test_auth_and_bootstrap.py           | 114 +++++-
 api/tests/test_context_audit_e4_round1.py      |  21 +-
 api/tests/test_context_redaction_e5_round5.py  | 341 +++++++++++++-
 api/tests/test_context_service.py              |  38 +-
 api/tests/test_context_verification.py         |  17 +-
 docs/architecture/04-safety-and-git-runtime.md |  23 +-
 src/pages/WorkspaceContext.tsx                 |  48 ++-
 src/pages/WorkspaceDetail.tsx                  |   6 +-
 src/services/contextApi.ts                     |  29 +-
 src/services/workspaceApi.ts                   |  29 +-
 src/test/context-entries.test.ts               |  28 +-
 src/test/dev-workspaces-ui.test.tsx            |   1 +
 src/test/workspace-context-ui.test.tsx         |  77 +++-
 src/test/workspace-detail-ui.test.tsx          |  27 +-
 src/utils/contextEntries.ts                    |  20 +-
 38 files changed, 3431 insertions(+), 191 deletions(-)
```

O status agrega `api/app/orchestrator/` em uma linha: são **31 arquivos untracked físicos, 13.126 linhas**, além dos 38 modificados rastreados. Inventariei esses arquivos, examinei os módulos de produção e os testes relevantes, executei `git diff` e buscas estáticas por debug, subprocessos e padrões de credenciais no código de produção. Não apareceu arquivo gerado ou debug novo no status; `git diff --check` não apontou erro. As divergências documentais identificadas constam nos findings.

## 8. Findings

**E6-AUD7-001 — Alta/P1 — scanner PEM omite match canônico e expõe material.**  
Arquivo: [redaction.py](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/app/safety/redaction.py:116). Reprodução: a entrada e os intervalos da seção 2; `redact()` e um `POST` real de task deixam `MIIE_AUD7_SYNTHETIC_PRIVATE_MATERIAL` visível. Impacto: o boundary HTTP não oculta um bloco que a regex canônica reconhece. **Critério de resolução:** scanner e regex canônica devem produzir os mesmos matches, na mesma ordem, com os mesmos spans e grupos, inclusive quando aberturas se sobrepõem nos traços; acrescentar esse caso ao teste de resposta HTTP e manter o custo linear.

**E6-AUD7-002 — Alta/P1 — valor sob chave sensível atravessa resposta JSON.**  
Arquivos: [redaction.py](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/app/safety/redaction.py:902) e [context.py](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/app/api/context.py:328). Reprodução: criar contexto com `structured={"token":"AUD7_SYNTHETIC_CREDENTIAL"}`; POST `201` e GET `200` devolvem o valor integral, enquanto `is_sensitive_key("token")` é verdadeiro. Impacto: a API expõe um segredo estrutural que o Context Engine E5 sabe classificar. **Critério de resolução:** a projeção pública deve propagar a classificação de chave sensível ao valor e à subárvore, com teste HTTP de escrita e leitura; as exceções explícitas, como `purge_token` e `edit-view`, precisam continuar delimitadas.

**E6-AUD7-003 — Média/P2 — `_ANCHORED` contradiz o limite documentado de adjacência.**  
Arquivos: [redaction.py](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/app/safety/redaction.py:284) e [04 §5](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/docs/architecture/04-safety-and-git-runtime.md:327). Reprodução: `redact("AKIA0123456789ABCDEFx") == "«redigido»x"`, embora o `x` seja texto comum e não outra ocorrência reconhecida. Impacto: conteúdo legítimo pode ser mascarado além do contrato aprovado; o teste atual fixa o desvio sem resolver a divergência normativa. **Critério de resolução:** alinhar implementação e garantia escrita, com decisão explícita sobre essa ampliação e teste correspondente.

**E6-AUD7-004 — Baixa/P3 — GATE 1 não detecta toda over-redaction em caso já divergente.**  
Arquivo: [test_context_redaction_e5_round5.py](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/tests/test_context_redaction_e5_round5.py:656). Reprodução: para `://Bearer password: sk-123`, manter os spans legítimos e acrescentar um span que oculte apenas o primeiro `:`; o GATE 1 inteiro passa. Impacto: as quatro cláusulas protegem os rótulos, mas não comprovam que toda pontuação pública permanece visível. **Critério de resolução:** declarar esse limite como permitido ou adicionar uma propriedade posicional independente para bytes públicos que devam ser preservados.

**E6-AUD7-005 — Baixa/P3 — contagem final incorreta no log.**  
Arquivo: [AGENT_LOG.md](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/AGENT_LOG.md:4211). Reprodução: o log diz **1754 passed**; a execução independente terminou em **1755 passed, 6 skipped**. Impacto: o registro de validação da correção não corresponde ao gate atual. **Critério de resolução:** atualizar a contagem e identificar a execução correspondente.

Há ainda uma divergência documental de baixa severidade a tratar junto à revisão de [04 §5](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/docs/architecture/04-safety-and-git-runtime.md:298): o texto chama `purge_token` de única exceção JSON, enquanto [EditViewJSONResponse](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/app/api/responses.py:69) é uma segunda exceção deliberada e testada.

## 9. Conclusão

**E6 REMAINS BLOCKED.** Os testes verdes confirmam ausência de regressão nos casos cobertos, mas os dois vazamentos HTTP reproduzidos e o contrato pendente de `_ANCHORED` impedem a aprovação da implementação.
