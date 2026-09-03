# 07 — Roadmap oficial da V1

> Escopo: etapas, gates, dependências, riscos residuais e decisões adiadas.
> **Revisado na Fase 1B.1** (AUD-009, AUD-013) e na **Fase 1B.3** (REAUD-001, REAUD-004,
> REAUD-007).

Cada etapa termina em um **gate verificável**. Quatro regras de ordem são inegociáveis:

1. **Safety Kernel e Path Runtime antes de qualquer módulo que valide path ou glob** — E2.
2. **Full Safety Runtime e mediação de ferramentas antes de qualquer agente escrever
   código** — E7 precede E8 ([ADR-0004](../adr/0004-safety-before-agents.md)).
3. **Auditor antes do laço de correção** — E9 precede E10.
4. **Baseline de dois modos antes do experimento com Ruflo** — E12 precede E13, sobre os
   **mesmos casos congelados** ([ADR-0010](../adr/0010-benchmark-protocol.md)).

O esquema de `Run` nasce completo em **E2**, para que nenhuma execução da história do
projeto fique sem medição.

---

## Divisão da segurança em duas etapas

O módulo `safety/` é **um só**; o que se divide é a ordem de implementação, sem duplicar
regra alguma. Em 1B.3 o **Path Runtime** entra junto do kernel, porque validar path exige
IO real e E3/E4 já dependem disso.

| Safety Kernel + Path Runtime — **E2** | Full Safety Runtime — **E7** |
| --- | --- |
| `safety/` como **política pura**: recebe `PathFacts`, decide | política de comandos |
| `path_runtime.py`: canonicalização, inspeção de reparse point, handles, identidade, verificação pós-abertura | enforcement de escrita |
| pré-validação sintática de path | `ToolExecutor` + `ToolExecutorFactory` (escopo de run) |
| política básica de segredos + redator | **enforcement de capability e fail closed** |
| validação de `source_refs` e globs | supervisão de processo, timeout, cancelamento, kill de árvore |
| carregamento e composição de política, `safety_policy_hash` | worktrees e isolamento git; `TestPolicy` |

---

## Etapas

| # | Etapa | Objetivo | Entregável | Gate | Depende de | Risco principal |
| --- | --- | --- | --- | --- | --- | --- |
| **E0** | Foundation | Conhecer o repositório real | Auditoria da Fase 1A | Baseline confirmado, suíte verde | — | — |
| **E1** | Architecture | Fechar a arquitetura antes de mexer na estrutura | `docs/` + 10 ADRs (Fases 1B, 1B.1, 1B.3) | **Reauditoria externa em verde** | E0 | Decidir demais no papel |
| **E2** | Backend skeleton + SQLite + **Safety Kernel + Path Runtime** | Processo local que sobe, responde, persiste e **já sabe validar path corretamente** | `api/` com FastAPI, `config`, `db`, Alembic, 7 tabelas, `GET /api/health`, `LocalSessionToken` injetado no HTML (FastAPI no build compilado; `transformIndexHtml` por canal somente em memória no Vite dev), `safety/` puro, `path_runtime.py`, CI da API | `/api/health` responde e é **a única rota sem token** nos dois fluxos locais; o token de dev não entra no bundle, `.env` ou log; `alembic upgrade head` cria o schema com `Run.invocation_id` UNIQUE e **sem** a constraint antiga; suíte cobre `..`, symlink, junction, UNC, device namespace, drive-relative, alias 8.3 e **revalidação pós-abertura**; `npm test` do web **inalterado** | E1 | Ambiente Python no Windows |
| **E3** | Workspace Registry | Criar e listar workspaces | CRUD de `DevWorkspace`, validação de `local_path` **via kernel + path runtime**, git preflight, **archive/purge-preview**, telas *Dev Workspaces* e *Overview*, SPA servido pelo backend | Criar workspace dos 5 tipos, inclusive sem `linked_project_id`; mesma origem funcionando; **não existe `DELETE` destrutivo**; Pages segue sem nenhuma chamada de rede à API | E2 | Path do Windows/OneDrive; modo de build mal configurado |
| **E4** | Context Registry | Registrar e verificar conhecimento | Entradas, `content_hash` canônico, `source_hash`, **verificação dupla com working tree**, import do seed, aba *Context* | Importar um planejamento real; **editar arquivo sem commitar e ver a entrada virar `stale(working_tree)`**; glob para `.env` recusado | E3 | Falso `fresh` |
| **E5** | Context Router + file map | Selecionar contexto sem LLM | `build_file_map`, scoring determinístico, `freeze_manifest`, **Rendered Context Artifact**, orçamento de tokens | Manifest e `rendered_context_hash` reproduzíveis; **apagar a entrada de origem e o artefato continuar respondendo o que foi entregue**; segredos só em `excluded` | E4 | Seleção pobre degradando tudo adiante |
| **E6** | Orchestrator Planner | Planejar sem executar nada | Analyzer, Resource Router, Planner, congelamento do `base_commit`, **`execution_fingerprint` completo** (developer/auditor/test bindings, `workflow_policy_hash`), `plan`/`approve`/`reject`, contratos de `purge-preview`/`purge` para task, *Task Detail* | Plano aprovável; **nenhum arquivo escrito**; hard rule não reduzida por LLM; **mudar modelo, comando de teste, política de workflow ou limite invalida a aprovação e a UI diz qual campo mudou**; auditor ausente serializa como `null` explícito; task não terminal não pode ser purgada | E5 | Gastar token no que uma regra resolve |
| **E7** | **Full Safety Runtime + Git isolation + Tool mediation** | Tornar a execução segura **antes** de existir execução | Política de comandos, `ToolExecutorFactory`, `ToolExecutor` com escopo de run, superfície fechada (`ReadFile`/`ListDirectory`/`SearchText`/`WriteFile`/`ApplyPatch`/`Git*`), enforcement de capability, timeout, cancelamento, kill de árvore, worktree, `TestPolicy` | **Nenhum `ExecCommand` genérico existe na superfície**; adaptador com `enforcement_method = not_enforceable` recusa a execução; escrita nunca ocorre antes da validação pós-abertura; executor não atravessa runs; worktree criada fora do OneDrive | E6 | **Etapa mais crítica.** Um furo aqui é irreversível |
| **E8** | Developer + Test Runner (**passe único**) | Primeira escrita de código, mediada e isolada | `DeveloperProvider` com **`execute_commands = disabled` provado**, `TestRunner` como infraestrutura sob `TestPolicy`, verificação pós-execução. **Sem laço automático de correção** | Tarefa real implementada **sem o Developer executar um único comando**; adaptador que não desliga shell é recusado; árvore principal intacta; `files_changed` do Git Runtime; `files_read` do `ToolExecutor` com `source = reported`; falha de teste leva a `needs_fix`, **não a retry automático** | **E7** | Adaptador incapaz de desligar shell; supervisão de processo no Windows |
| **E9** | Codex Auditor | Verificação independente | `AuditorProvider` **sem capability alguma**, `Run` de auditoria com `subject_run_id`, `AuditFinding` com `purpose`, gate de `high` | Auditoria obrigatória em diff não vazio; finding `high`/`open` da **auditoria vigente** impede `done`; **reauditar cria `Run` novo com `supersedes_run_id`, sem reabrir nem sobrescrever nada** | E8 | Custo por tarefa; ruído de findings |
| **E10** | Audit-driven correction loop | Fechar o ciclo de correção | Rodadas dirigidas por findings e testes, `max_fix_rounds` **agora ativo** | Rodada de correção consome finding real; teto respeitado; cada rodada gera `invocation_id` e `Run` próprios | E9 | Laço que gasta token sem convergir |
| **E11** | UI completa | Fechar o laço para o usuário | SSE via `fetch`, diff, findings por `purpose` e vigência, runs com proveniência, prévia de purga de workspace/task, descarte de worktree | Fluxo completo pela interface, sem terminal; nenhum dado não sanitizado no browser | E10 | Vazamento na serialização |
| **E12** | **Baseline Benchmark** | Responder se a orquestração vale a pena — **sem depender do Ruflo** | Casos **congelados**, execução de `claude_only` e `orchestrated`, `/api/metrics/summary`, página global de métricas, avaliação dos dois modos | Mesmo objetivo executado nos dois modos sobre casos congelados; avaliação com **mesmo avaliador, mesma rubrica, mesmo momento**; nenhum finding de benchmark alterando execução; **comparação de dois modos já utilizável** | E11 | `token_source = unavailable` esvaziar a comparação |
| **E13** | **Ruflo Experiment + benchmark de três modos** *(opcional)* | Testar a hipótese sem criar dependência | Adaptadores `MemoryProvider` / `WorkflowStateStore` / `Coordinator`; execução de `orchestrated_ruflo` sobre **os mesmos casos congelados de E12**; avaliação final dos três modos | **Sistema funciona integralmente com Ruflo removido da máquina**; os três modos avaliados com mesmo avaliador e mesma rubrica sobre artefatos congelados | E12 | Acoplamento acidental |
| **E14** | Hardening + estudo de isolamento de SO | Fechar a V1 | Limites revisados, retenção, GC de worktree e de artefatos, recuperação de OneDrive, **estudo de contêiner / job object / usuário dedicado**, avaliação de reabrir `execute_commands` | Suíte completa verde; nenhuma aresta de importação proibida; decisão registrada sobre isolamento | E12 | Escopo infinito de hardening |
| **—** | **V1 Release** | — | — | E14 concluída e riscos residuais reconfirmados | E14 | — |

### Mudanças em relação ao roadmap da Fase 1B.1

| Mudança | Motivo |
| --- | --- |
| **E2 ganha o Path Runtime** | `safety/` puro não pode fazer o IO que a validação de path exige *(REAUD-004)* |
| **E7 explicita a superfície fechada e a factory** | O gate passa a exigir que **nenhum `ExecCommand` genérico exista** *(REAUD-001)* |
| **E8 exige `execute_commands = disabled` provado** | Mediar execução de comando era trampolim para efeito arbitrário *(REAUD-001)* |
| **E9 exige `Run` novo por auditoria** | A constraint e a "substituição de findings" anteriores impediam reauditoria legítima *(REAUD-005)* |
| **E12 vira Baseline Benchmark de dois modos** | O benchmark não pode ficar refém de um componente opcional *(REAUD-007)* |
| **E13 absorve o benchmark de três modos** | Ruflo executa sobre os mesmos casos congelados e só então há avaliação dos três |
| **E3 entrega archive/purge-preview** | `DELETE …?hard=true` colocava ação destrutiva a um parâmetro de distância *(REAUD-008)* |

---

## Projeto criado do zero — fluxo até um `HEAD` válido

Nota explicativa; não altera nenhum Entregável ou Gate. Descreve como um
`DevWorkspace` sem repositório Git avança pelo roadmap.

- Um `DevWorkspace` novo pode ser **registrado** mesmo sem ser um repositório
  Git, preservando o comportamento já definido em
  [`04-safety-and-git-runtime.md`](04-safety-and-git-runtime.md) §8: nesse
  estado ele serve para **contexto**, mas **execução permanece bloqueada**.
- Para avançar até qualquer estágio que exija `git_head`,
  `planning_base_commit` ou um `HEAD` válido, o usuário prepara o repositório
  **manualmente, fora do backend**: `git init` e um commit inicial.
- O backend **nunca** executa `git init`, automaticamente ou em nome de um
  agente.
- Com um `HEAD` válido, o workspace segue normalmente pelo Context Registry
  (E4), Context Router (E5) e planejamento/aprovação (E6), respeitando os
  invariantes já existentes.
- A **criação da estrutura inicial de arquivos** do software não faz parte
  desse passo manual. Quando E7/E8 existirem, ela pode ser feita como a
  primeira tarefa normal do Developer mediado.

---

## Checkpoint após E9 — "Núcleo seguro pronto"

Marco de leitura, não um novo gate. Alcançado quando **E9** fecha.

- **Critérios:** ciclo completo planejar → implementar mediado (sem shell) →
  testar → auditar, **fechado e funcional**.
- **Ressalva explícita:** a UI ainda **não tem SSE** (só chega em E11); a
  correção de findings ainda é **manual** (`max_fix_rounds` só ativa em E10).
- **Nota:** este é o marco de **segurança técnica**, não de conforto de uso.
  Distinguir de uma futura avaliação separada de "Freelance Ready" mais
  ampla — critério não decidido agora.

---

## Riscos residuais

| # | Risco | Nível | Situação |
| --- | --- | --- | --- |
| **R1** | **Fronteira de execução confiável não existe integralmente na V1.** O Test Runner executa código do projeto, que não é confinado à worktree | **CRÍTICO** | **Aceito e declarado** ([04](04-safety-and-git-runtime.md) §0 e §6). **Reduzido em 1B.3**: o Developer perdeu toda autoridade de execução; a superfície restante é o Test Runner sob `TestPolicy`. Estudo de isolamento em E14 |
| **R2** | **TOCTOU residual em resolução de path.** A validação pós-abertura estreita a janela; não a fecha | **ALTO** | **Aceito e declarado** ([04](04-safety-and-git-runtime.md) §4). Mitigado pela ordem obrigatória: nada é truncado antes da decisão pós-abertura |
| **R3** | **OneDrive — mitigação parcial.** Worktrees ficam fora, mas `.git/worktrees`, refs, objetos e `.git/index` seguem dentro | **ALTO** | **Aceito**, com recuperação documentada. Alternativa mais eficaz: mover o repositório para fora do OneDrive |
| **R4** | **Adaptador incapaz de desligar a própria ferramenta de shell** | ALTO | Fail closed: sem prova de `execute_commands = disabled`, o adaptador é **inutilizável como Developer** na V1. Pode restringir a escolha de providers |
| **R5** | Custo de token sem controle | ALTO | Orçamento por run e por task, auditoria sobre diff, conjunto mínimo de agentes. Só E12 dirá se basta |
| **R6** | Backend local como alvo de outras abas | MÉDIO | Mesma origem, sem CORS, token no HTML de mesma origem e em header, validação de `Host`/`Origin`, uma única rota aberta, zero chamadas a localhost no Pages |
| **R7** | `token_source = unavailable` esvaziar o benchmark | MÉDIO | Registrado com honestidade em vez de zerado. Se dominar as amostras, antecipa a decisão CLI → API |
| **R8** | **Purga acidental de dados de benchmark** | MÉDIO | Prévia obrigatória, confirmação forte, e **recusa** quando o grupo já tem `benchmark_evaluation` ([02](02-data-model.md) §11) |
| **R9** | Link quebrado de `linked_project_id` | MÉDIO | O workspace nunca depende do link |
| **R10** | Limpar dados do navegador destrói o comercial | MÉDIO | Comportamento já existente; aviso e export regular |
| **R11** | Qualidade do plano define tudo adiante | MÉDIO | Critérios de aceite explícitos, aprovação humana, auditoria independente |
| **R12** | Windows: 260 caracteres, kill de árvore, caixa de path | MÉDIO | Nomes curtos, enumeração recursiva, normalização. Precisa de teste real na máquina |
| **R13** | Findings ruidosos viram carimbo | MÉDIO | Só `high` de `workflow_audit` vigente bloqueia; findings têm `status` |
| **R14** | **Superfície mediada insuficiente para tarefas reais** | MÉDIO | *Novo em 1B.3.* Sem executar comandos, o Developer pode não conseguir concluir certas tarefas. E8 mede isso; a reabertura tem gatilho definido (E14) |
| **R15** | Ambiente Python no Windows | BAIXO | Documentado em E2; versão fixada em `pyproject.toml` |
| **R16** | Dependência acidental de Ruflo | BAIXO | Teste de arquitetura + gate de E13 |

---

## Decisões adiadas

| Decisão | Reabre quando |
| --- | --- |
| **Permitir `execute_commands` ao Developer** | **Só com isolamento de processo/SO comprovadamente forte** — contêiner, VM, *job object* mais forte ou mecanismo demonstrado. Avaliado em E14, com R14 como evidência |
| **CLI vs API** para Claude e Codex | R7 se confirmar em E12, ou quando um adaptador não conseguir provar `execute_commands = disabled` |
| **Isolamento de SO** | E14 — ou antes, se R1 se materializar |
| **Forma exata da confirmação de purga** (`confirm_phrase` ou token curto) | Ao implementar E3; o contrato já exige que **não** seja parâmetro de query |
| **Mover `src/` para `web/`** | A convivência com `api/` virar fricção real |
| **Mover o repositório para fora do OneDrive** | Se R3 se materializar; é a mitigação mais simples e mais eficaz |
| **Unificar as persistências** em SQLite | Multi-dispositivo, login, ou consulta cruzada no servidor |
| **`MetricSample` genérica** | Métrica esparsa e irregular, com chave desconhecida em tempo de projeto |
| **`max_parallel_agents > 1`** | Tarefa demonstravelmente paralelizável e `Coordinator` existente |
| **Re-rank de contexto por LLM** | Se a seleção determinística se mostrar insuficiente em E8/E9 |
| **Rotação de `LocalSessionToken` sem reinício** | Se sessões longas tornarem o reinício incômodo |
| **Workspace sem git executar tarefas** | Não previsto: sem git não há isolamento |
| **Agentes além dos cinco previstos** | Só com tarefa concreta que os cinco não concluam |
| **Watcher de filesystem para staleness** | Depois de E14, e provavelmente nunca em pasta do OneDrive |
| **Scaffold assistido de projeto novo** (criar estrutura inicial de arquivos) | Disponível como a primeira tarefa comum via Developer mediado, a partir de E8 — reutiliza `WriteFile`/`ApplyPatch` já previstos; sem capacidade nova, sem `git init` automatizado |
| **Camada educacional opcional** (endpoint de explicação em linguagem simples) | A partir de E9, reaproveitando `plan`+`diff`+`findings` já existentes; formato exato não decidido agora; sem nova entidade de banco e sem bloquear o fluxo principal |

---

## Próxima ação

**E2 — Backend skeleton + SQLite + Safety Kernel + Path Runtime**, após a reauditoria desta
correção.

Primeiras ações de E2, que exigem autorização por saírem do escopo somente-documentação:

1. Criar `api/` com `pyproject.toml`, `config.py`, `db/`, `main.py`, `GET /api/health`,
   `LocalSessionToken`, `safety/` puro e `path_runtime.py`.
2. Acrescentar ao `.gitignore`: `data/`, `api/.venv/`, `__pycache__/`, `*.db`.
3. Acrescentar workflow de CI da API, **sem tocar** em `deploy.yml`.
