# E6 — Auditoria consolidada, rodada 5 (relatório original, verbatim)

> Persistido verbatim conforme instrução do Pedro (2026-09-23). Não editado, não
> resumido, não reformatado além da conversão para Markdown de bloco de citação.

BLOCKED. Reproduzi os quatro casos CONS4 corrigidos: a elegibilidade bloqueia planejamento
em workspace arquivado, sem Git, sem HEAD e com `test_config` inválida; uma `draft` nova
mostra estado vazio sem alerta; e plano, contexto e fingerprint preservados aparecem como
históricos. A auditoria do novo contrato encontrou três findings.

1. E6-CONS5-001 — Média/P2 — falhas de leitura do Git recebem diagnóstico incorreto. Em um
   repositório temporário com commit existente, corrompi `.git/config`. O detalhe informou
   `workspace_not_git_repo`, e `POST /plan` respondeu 409 com o mesmo `reason`. Ao corromper
   a referência de HEAD depois de um commit válido, ambos informaram `repository_without_head`.
   O classificador e o comando concordam, mas confundem Git ilegível com ausência de
   repositório ou de primeiro commit, levando a orientações erradas na interface. A causa
   está no tratamento de retornos não zero em
   [probe_head (line 370)](../../api/app/git_runtime/__init__.py:370). Critério: só emitir
   essas duas categorias quando forem confirmadas; classificar outras falhas de leitura como
   `git_unverifiable` e cobrir configuração e referência corrompidas.
2. E6-CONS5-002 — Baixa/P3 — perda de corrida mantém a ação obsoleta na tela. Renderizei a
   interface em memória com elegibilidade inicial positiva e fiz `POST /plan` responder
   `409 concurrent_task_update`. O alerta apareceu, mas houve apenas um GET da tarefa e o
   botão "Planejar" continuou visível. Uma perda de compare-and-set significa que o estado
   observado pode ter mudado. O
   [tratamento do erro (line 598)](../../src/pages/WorkspaceTasks.tsx:598) só relê a tarefa
   para os códigos selecionados em
   [tasksApi.ts (line 333)](../../src/services/tasksApi.ts:333). Critério: reler uma vez
   após conflito concorrente, preservando o alerta e sem criar loop.
3. E6-CONS5-003 — Baixa/P3 — estado inconsistente atribui falsamente a causa ao HEAD. Após
   um planejamento real, mantive o HEAD intacto e forcei em banco temporário `needs_fix` sem
   manifesto. A API retornou `approval_state=requires_replan`, `planning.transition=allowed`
   e `plan_standing=historical`; planejar diretamente continua permitido. Na interface
   renderizada, o
   [PlanStandingNotice (line 141)](../../src/pages/WorkspaceTasks.tsx:141) afirmou que o HEAD
   mudou. Esse estado exige alteração direta do banco nesta fase, portanto o impacto é
   defensivo e limitado; a ação oferecida permanece correta. Critério: reservar a explicação
   específica de divergência de HEAD ao caso que a comprova e usar aviso histórico neutro nas
   demais combinações.

A reprodução própria também confirmou a precedência de bloqueios simultâneos e ausência de
efeitos de escrita no GET. Medi 20 detalhes: 2 leituras Git por GET, mediana de 162 ms;
listagem e `/context` fizeram 0 sondagens. Com duas leituras artificialmente lentas, o
detalhe levou 919 ms enquanto health e listagem responderam em 3 ms e 19 ms. O possível
`ContextError` posterior à sondagem permanece investigação separada: elegibilidade positiva
não garante sucesso diante de falha posterior de E/S.

Todos os gates passaram: backend focado 250, backend completo 1871 passed, 6 skipped,
`PYTEST_EXIT=0`; frontend focado 43, completo 160; Ruff check, Ruff format, mypy, lint,
build e `git diff --check` com saída 0. A árvore terminou com 40 arquivos rastreados
modificados e 45 untracked. O diff desde HEAD é acumulado; não havia artefato independente
do diff isolado para confirmar a contagem declarada da CONS4. Nenhum arquivo do projeto foi
alterado por esta auditoria. Nada commitado ou enviado.
