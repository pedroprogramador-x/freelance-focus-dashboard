# E6 — Revisão de release hygiene (transcrição parcial + complemento documental)

> **Correção de integridade (2026-09-28).** O corpo abaixo, até "Nenhuma dessas ações foi
> executada nesta revisão.", é o texto que chegou nesta sessão na tarefa original e foi
> rotulado "verbatim" — mas ele referencia duas vezes ("os 94 caminhos literais nos comandos
> abaixo"; "relacionados no relatório original de revisão") um bloco de comandos que nunca
> estava presente. **Esse rótulo estava incorreto**: o que existia até aqui era uma
> transcrição fiel de um texto que era, ele mesmo, incompleto — não uma reprodução íntegra do
> relatório original do Codex. O bloco de comandos que faltava foi fornecido por Pedro nesta
> tarefa posterior, copiado literalmente da mensagem original do Codex (não reconstruído a
> partir do inventário nem de inferência), e está na seção "Comandos originais dos 94
> caminhos" ao final deste arquivo. Com o complemento, o documento agora contém o relatório e
> os comandos que ele referencia; sem o complemento, o texto anterior a esta nota nunca foi,
> sozinho, uma reprodução completa — era uma transcrição parcial.

BLOCKED — RELEASE HYGIENE. A árvore está íntegra, mas o inventário e o log preparados para o commit registram contagens incorretas. Além disso, uma alteração específica na arquitetura congelada ainda precisa de decisão documentada do Pedro. Não executei staging, criação de branch ou commit.

O estado real é main em 8c493a8: 40 arquivos rastreados modificados + 57 novos = 97, nenhum staged. git diff --check retornou 0. A branch e6/fechamento-cons5v está disponível localmente e em origin.

O relatório GREEN foi persistido separadamente em docs/audits/e6-cons5v-round-4v.md; o relatório BLOCKED anterior em docs/audits/e6-cons5v-round-4.md permanece intacto. Examinei o conteúdo dos 57 arquivos novos: todos são texto UTF-8 não vazio, sem conteúdo duplicado, binários, bancos, ambientes virtuais ou caches candidatos. Os padrões de credencial encontrados pertencem a fixtures sintéticas, relatórios que as citam e exemplos de código; não identifiquei credencial real.

As edições de 06 — fronteiras da API (line 215) têm autorização D1 registrada no AGENT_LOG (line 5955) e correspondem a planning, plan_standing e reason implementados. Em 04 — segurança (line 302), os dois escapes JSON, a precisão da âncora, o risco de redação excessiva e o fechamento de AUD7-001/002 também têm decisões expressas no log. A exceção é o bloco "Cobertura de chaves e valores" e a mudança para "recursivo sobre valores e chaves" (linhas 332 e 340). O texto corresponde ao código, mas o próprio log da rodada anterior (line 3917) declarou que essa correção do documento congelado exigia decisão do Pedro. Não encontrei registro posterior que autorize esse trecho específico; isso não prova que nunca tenha havido autorização.

Há correções factuais necessárias antes de incluir os registros de entrega:

- O inventário final (line 13) diz 55 novos / 95 candidatos, mas o estado atual é 57 / 97. A seção de testes novos diz 22, quando são 21; as seções 1 e 2.8 também divergem sobre se os dois documentos finais já entraram na contagem.
- A última entrada do AGENT_LOG (line 6847) repete 55 / 95 e trata a autorização de 04 e 06 como igualmente desconhecida. O registro encontrado resolve a dúvida de 06 e da maior parte de 04.

O conjunto definitivo é 97 caminhos: os 94 caminhos literais nos comandos abaixo, mais AGENT_LOG.md, docs/architecture/04-safety-and-git-runtime.md e docs/audits/e6-final-inventory.md. Esses três devem ficar fora do staging até a correção das contagens e a decisão sobre o trecho de 04. Nenhum outro candidato precisa ser excluído.

Após resolver as pendências, um procedimento seguro é criar a branch com git switch -c e6/fechamento-cons5v; a working tree não commitada acompanha a branch, sem stash ou reset.

Os 94 arquivos anteriormente liberados para staging estão relacionados no relatório original de revisão. Confira os caminhos diretamente no inventário e no repositório antes de preparar novos comandos; não execute staging nesta tarefa.

Somente depois de corrigir os registros e aprovar ou retirar o trecho pendente de 04, adicionar explicitamente os arquivos restantes. Antes de qualquer commit, revisar git status --porcelain=v1 --untracked-files=all, git diff --cached --name-status, o diff staged integral e git diff --cached --check; o staged esperado deve conter exatamente o conjunto decidido, sem arquivos adicionais. Nenhuma dessas ações foi executada nesta revisão.

---

## Comandos originais dos 94 caminhos (complemento documental, 2026-09-28)

> Fornecidos por Pedro numa tarefa posterior a esta, copiados literalmente da mensagem
> original do Codex — não reconstruídos a partir do inventário nem de inferência. É a seção
> que o corpo acima referencia duas vezes ("nos comandos abaixo"; "relacionados no relatório
> original de revisão") e que faltava na primeira persistência deste arquivo.

```
git add -- api/app/api/context.py api/app/api/web.py api/app/api/workspaces.py api/app/context_engine/__init__.py api/app/context_engine/content_hash.py api/app/context_engine/errors.py api/app/context_engine/rendering.py api/app/context_engine/service.py api/app/context_engine/source_ref_expansion.py api/app/db/models.py
git add -- api/app/db/session.py api/app/git_runtime/__init__.py api/app/main.py api/app/safety/__init__.py api/app/safety/redaction.py api/app/workspace/__init__.py api/app/workspace/errors.py api/app/workspace/purge.py api/app/workspace/purge_tokens.py api/app/workspace/service.py
git add -- api/app/api/responses.py api/app/api/tasks.py api/app/db/conflicts.py api/app/orchestrator/__init__.py api/app/orchestrator/analyzer.py api/app/orchestrator/eligibility.py api/app/orchestrator/errors.py api/app/orchestrator/execution_manager.py api/app/orchestrator/fingerprint.py api/app/orchestrator/planner.py
git add -- api/app/orchestrator/purge.py api/app/orchestrator/resource_router.py api/app/orchestrator/state_machine.py api/app/safety/objective_signals.py api/app/safety/test_policy.py api/app/workspace/policy.py api/migrations/versions/0002_workspace_test_config.py
git add -- api/tests/test_api_context.py api/tests/test_api_workspaces.py api/tests/test_architecture.py api/tests/test_auth_and_bootstrap.py api/tests/test_context_audit_e4_round1.py api/tests/test_context_redaction_e5_round5.py api/tests/test_context_service.py api/tests/test_context_verification.py api/tests/test_api_redaction_boundary.py api/tests/test_api_tasks.py
git add -- api/tests/test_e6_aud7_002_chave_estruturalmente_sensivel.py api/tests/test_e6_audit_round_1.py api/tests/test_e6_audit_round_2.py api/tests/test_e6_audit_round_3.py api/tests/test_e6_audit_round_4.py api/tests/test_e6_audit_round_5.py api/tests/test_e6_audit_round_6.py api/tests/test_e6_audit_round_7.py api/tests/test_e6_cons4.py api/tests/test_e6_cons5.py
git add -- api/tests/test_e6_cons5v.py api/tests/test_e6_cons5v2.py api/tests/test_e6_cons5v3.py api/tests/test_e6_cons5v4.py api/tests/test_orchestrator_analyzer.py api/tests/test_orchestrator_planner.py api/tests/test_orchestrator_router_and_fingerprint.py api/tests/test_orchestrator_state_machine.py
git add -- src/pages/WorkspaceContext.tsx src/pages/WorkspaceDetail.tsx src/services/contextApi.ts src/services/workspaceApi.ts src/utils/contextEntries.ts src/pages/WorkspaceTasks.tsx src/services/tasksApi.ts
git add -- src/test/context-entries.test.ts src/test/dev-workspaces-ui.test.tsx src/test/workspace-context-ui.test.tsx src/test/workspace-detail-ui.test.tsx src/test/task-detail-ui.test.tsx
git add -- docs/audits/e6-aud7-002-round-1.md docs/audits/e6-aud7-002-round-2.md docs/audits/e6-cons5v-round-1.md docs/audits/e6-cons5v-round-2.md docs/audits/e6-cons5v-round-3.md docs/audits/e6-cons5v-round-4.md docs/audits/e6-cons5v-round-4v.md docs/audits/e6-consolidated-round-1.md docs/audits/e6-consolidated-round-2.md docs/audits/e6-consolidated-round-3.md
git add -- docs/audits/e6-consolidated-round-4.md docs/audits/e6-consolidated-round-5.md docs/audits/e6-round-7.md docs/audits/e6-round-7v.md docs/audits/e6-round-7v2.md docs/audits/e6-round-7v3.md
git add -- docs/architecture/06-api-and-ui-boundaries.md
```

Conferido nesta correção: os 94 caminhos acima, mais os quatro autorizados posteriormente
(`AGENT_LOG.md`, `docs/architecture/04-safety-and-git-runtime.md`,
`docs/audits/e6-final-inventory.md`, `docs/audits/e6-final-release-review.md`), somam
exatamente os 98 candidatos que `git status --porcelain=v1 --untracked-files=all` lista nesta
sessão — comparação por `comm` entre as duas listas ordenadas, sem diferença em nenhum dos
dois sentidos.
