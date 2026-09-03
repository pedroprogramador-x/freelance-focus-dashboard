# Agent Log

Registro de sessões de agentes de IA neste projeto. Todo agente deve
ler este arquivo antes de iniciar uma tarefa e adicionar uma entrada
ao final ao terminar.

Formato de cada entrada:
## [DATA] — [AGENTE] — [TAREFA]
- Arquivos alterados:
- Decisões tomadas:
- Pendências:

---

## 2026-09-01 — Claude Sonnet 5 — Entrada retroativa: fases 1A–E2.5 + auditorias

Consolida o trabalho feito antes da criação deste log. Detalhe completo
está em `docs/` (arquitetura congelada) e no histórico Git.

- Arquivos alterados:
  - `docs/` — 18 arquivos: `docs/README.md`, `docs/architecture/01..07-*.md`,
    `docs/adr/0001..0010-*.md`. Auditoria arquitetural (1A), arquitetura V1
    (1B), correções pós-auditoria Codex (1B.1, 1B.3). Congelado no commit
    `67d4df4` ("docs: freeze AI Dev Workspace V1 architecture").
  - `api/` — 39 arquivos: FastAPI + SQLite + Alembic (7 entidades), Safety
    Kernel puro, Path Runtime, LocalSessionToken/auth, 434 testes. Fases
    E2 + correções E2.1 e E2.3. Commitado em `ec418f4`
    ("feat: add AI Dev Workspace backend safety foundation").
  - `.gitignore` — regras de ignore do backend (caches Python, venv, `*.db`).
  - `.github/workflows/api-ci.yml` — CI da API, separada do `deploy.yml`.
  - `src/` (frontend React) — **não tocado** em nenhuma fase.

- Decisões tomadas:
  - Arquitetura V1 congelada (Architecture Freeze). ADRs 0001–0010 em
    `docs/adr/`. Não alterar sem autorização explícita do Pedro.
  - `DeveloperProvider.execute_commands = disabled` — o Developer nunca
    recebe shell cru; efeitos de arquivo são mediados e tipados.
  - Persistências disjuntas: `localStorage` (domínio comercial) vs SQLite
    (AI Dev Workspace). O backend não conhece o domínio comercial.
  - `source_ref` na E2: só caminho literal relativo; qualquer sintaxe de
    glob é recusada (fail-closed) até o expansor canônico existir (E4).
  - `.env*` classificado como segredo por padrão (inclui `.env.example`).
  - Ambiente Python fica FORA do repo (OneDrive):
    `C:\Users\pedro\AppData\Local\FreelanceFocus\venvs\api\`.
  - Commit único por fase, criado só após auditoria GREEN.

- Pendências:
  - E2 recebeu auditoria independente Codex final: **GREEN**
    (E2 IMPLEMENTATION APPROVED / E2 COMMIT AUTHORIZED).
  - Push confirmado no GitHub: `origin/main` → `67d4df4`,
    `origin/claude/ai-dev-e2-backend-safety-foundation` → `ec418f4`.
    Sem PR aberto, sem merge.
  - Próxima fase: E3 (Workspace Registry). Ver
    `docs/architecture/07-roadmap-v1.md`. Não iniciada.
  - CLAUDE.md: proposta de conteúdo entregue nesta sessão, aguardando
    revisão do Pedro antes de criar o arquivo.

---

## 2026-09-01 — Claude Sonnet 5 — Criação do AGENT_LOG.md e do CLAUDE.md

- Arquivos alterados: `AGENT_LOG.md` (criado), `CLAUDE.md` (criado).
- Decisões tomadas: estrutura do log definida (entrada por sessão, ler
  antes / registrar depois). CLAUDE.md redigido pelo critério "cortar tudo
  que Claude infere sozinho" e revisado/editado pelo Pedro antes de aplicar
  (~70 linhas: regras não-inferíveis, comandos de teste por lado,
  venv fora do repo, git).
- Pendências: nenhuma. Ambos os arquivos ainda não commitados/pushados —
  aguardando autorização do Pedro.

---

## 2026-09-01 — Claude Sonnet 5 — Confirmação da criação do CLAUDE.md

- Arquivos alterados: nenhum reescrito. `CLAUDE.md` (raiz) já existia,
  idêntico ao conteúdo aprovado pelo Pedro (67 linhas, 2470 bytes). Nova
  tarefa pedia recriar com conteúdo colado, mas o placeholder do prompt
  veio vazio — verifiquei que o arquivo em disco já bate com a versão
  aprovada e não sobrescrevi.
- Decisões tomadas: nenhuma.
- Pendências: `CLAUDE.md` e `AGENT_LOG.md` seguem untracked, sem
  commit/push — aguardando revisão do diff pelo Pedro.

---

## 2026-09-01 — Claude Sonnet 5 (effort medium) — Configuração do CodeGraph (MCP + índice)

Ferramenta de ambiente Claude Code, não do projeto. Binário já instalado
pelo Pedro fora da sessão (`install.ps1` oficial, `@colbymchenry/codegraph`
v1.6.0, em `%LOCALAPPDATA%\codegraph\current\`). Esta sessão só configurou
e validou.

- Arquivos alterados:
  - `.gitignore` — adicionada linha `.codegraph/` (único arquivo do projeto
    tocado; `src/`, `api/`, `docs/` intactos). O `.codegraph/` já traz um
    `.gitignore` próprio auto-ignorante, mas a regra na raiz também suprime
    o `.codegraph/.gitignore` do `git status`.
  - `~/.claude.json` — registrado MCP server `codegraph` (stdio,
    `command: "codegraph"`, `args: ["serve","--mcp"]`). Ambiente, fora do repo.
  - `~/.claude/settings.json` — `codegraph install` adicionou um hook
    `UserPromptSubmit` → `codegraph.cmd prompt-hook`. Não pedi esse hook
    explicitamente; veio junto do `install`. `--no-permissions` evitou a
    lista de auto-allow. Ambiente, fora do repo.
  - `~/.claude/CLAUDE.md` — `codegraph install` anexou um bloco
    `<!-- CODEGRAPH_START/END -->` (~800 B) instruindo o agente a preferir
    `codegraph_explore`/`codegraph explore` a grep em repos indexados.
    Ambiente global, fora do repo.
  - PATH de usuário (Windows) — `install --yes` adicionou
    `%LOCALAPPDATA%\codegraph\current\bin`.
  - `.codegraph/` (novo, ignorado) — índice local: 75 arquivos, 1.030 nós,
    2.987 arestas, DB SQLite 3,58 MB. Só este projeto.

- Decisões tomadas:
  - `codegraph init` rodado só na raiz deste repo. `codegraph status`
    confirma projeto único. Nenhum índice global.
  - Telemetria DESATIVADA: `codegraph telemetry off`
    (`~/.codegraph/telemetry.json` → `enabled:false`, `consent_source:cli`);
    fila local não enviada foi apagada.
  - MCP registrado só para o alvo `claude` (não cursor/codex/etc.).
    `--location global`, `--no-permissions`.
  - RTK e CodeBurn NÃO instalados nesta sessão (ver conversa: RTK sem
    suporte oficial a Git Bash/Windows nativo; CodeBurn pendente).

- Pendências:
  - `claude mcp list` nesta sessão: `codegraph` conecta OK quando o bin
    está no PATH; falha (`CONNECTION_CLOSED`) numa sessão que começou antes
    da atualização do PATH. **Reiniciar o Claude Code** para o MCP subir
    limpo.
  - Teste comparativo real com as tools MCP (`codegraph_explore`) e os
    números de token do CodeBurn: exige sessão nova. Nesta sessão a
    comparação foi feita via CLI (`codegraph context`): 1 chamada vs
    ~9 (find + leituras) da abordagem grep/Read.
  - Avaliar se o hook `UserPromptSubmit` e o bloco no `~/.claude/CLAUDE.md`
    devem ficar ou ser removidos (`codegraph uninstall` reverte).
  - `.gitignore` alterado sem commit — aguardando Pedro.

---

## 2026-09-01 — Claude Sonnet 5 (effort medium) — Validação prática da integração CodeGraph

Tarefa só de validação: nada de código alterado, nada instalado.

- Arquivos alterados: só este `AGENT_LOG.md` (esta entrada).
- Verificações:
  - MCP server `codegraph` **connected**. Expõe **uma** tool:
    `mcp__codegraph__codegraph_explore` (o `codegraph_context` do prompt-hook
    é injeção de contexto via `UserPromptSubmit`, não tool MCP). A tool estava
    *deferred*; carregada via tool search e usada.
  - Teste 1 (SafetyPolicy × PathRuntime): 1ª ferramenta = `codegraph_explore`.
    Uma chamada devolveu policy.py + paths.py + path_runtime.py + types.py com
    blast radius. Sem grep/Read.
  - Teste 2 (validação de `source_ref` / glob): 1ª ferramenta =
    `codegraph_explore`. Uma chamada devolveu `source_refs.py` inteiro +
    `prevalidate_path_syntax`; reaproveitou paths.py já trazido no teste 1.
    Sem grep/Read.
  - Nenhum caso de "disponível mas não usado". Integração funcionando na prática.
  - **Não existe** `codegraph gain` nem comando de economia de tokens no
    CodeGraph 1.6.0 (subcomandos: init/index/sync/status/query/explore/context/
    node/files/daemon/callers/callees/impact/affected/install/telemetry/upgrade).
    `codegraph status`: 75 arquivos, 1.030 nós, 2.987 arestas, DB 3,58 MB,
    índice up to date. Métrica de economia é do CodeBurn, não instalado.
- Pendências: instalar CodeBurn fica para tarefa separada (não feito aqui).

---

## 2026-09-01 — Claude Sonnet 5 — Convenção de branch por fase + merge da E2 em main

- Arquivos alterados: `.gitignore` (+`.idea/`), `AGENT_LOG.md`, `CLAUDE.md`
  commitados em `9d9cc1b` (autoria: Pedro). Identidade global do Git
  corrigida para `Pedro Henrique Bezerra de Lima <pedrophbezerra@gmail.com>`.
- Decisões tomadas — **convenção de branch por fase**:
  - Cada fase (E2, E3, E4…) é desenvolvida em branch própria e mesclada em
    `main` via **fast-forward** assim que fechar auditoria GREEN.
  - A próxima fase sempre abre branch nova a partir de `main` atualizado.
  - `main` remoto agora em `9d9cc1b` (E2 completa + CLAUDE.md/AGENT_LOG.md).
  - Branch `claude/ai-dev-e2-backend-safety-foundation` mantida por ora
    (não deletar até o Pedro confirmar).
- Pendências: E3 (Workspace Registry) não iniciada. Nova branch a partir de
  `main@9d9cc1b` quando começar.

---

## 2026-09-03 — Claude Sonnet 5 (effort medium) — Notas de roadmap: projeto do zero, checkpoint pós-E9, decisões adiadas

Tarefa SOMENTE DOCUMENTAÇÃO. E3 não iniciada; nenhum código funcional escrito.

- Arquivos alterados: `docs/architecture/07-roadmap-v1.md`, `AGENT_LOG.md`.
  Nenhum outro arquivo tocado. `docs/` alterado com autorização explícita do
  Pedro nesta conversa (escopo restrito a este arquivo de roadmap).
- Decisões tomadas (todas notas explicativas — nenhum Entregável, Gate, ADR
  ou invariante de dados alterado):
  - Nova seção "Projeto criado do zero — fluxo até um `HEAD` válido" após a
    tabela de Etapas: workspace pode ser registrado sem Git (só contexto,
    execução bloqueada, conforme `04-safety-and-git-runtime.md` §8); para
    estágios que exigem `git_head`/`planning_base_commit`/`HEAD`, o usuário
    roda `git init` + commit inicial **manualmente, fora do backend**.
  - **Correção registrada explicitamente:** o backend **nunca** executa
    `git init`, automaticamente nem em nome de um agente. Alinha com a
    decisão adiada já existente "Workspace sem git executar tarefas".
  - Criação da estrutura inicial de arquivos NÃO faz parte do passo manual —
    vira a primeira tarefa normal do Developer mediado quando E7/E8 existirem.
  - Novo checkpoint de leitura "Núcleo seguro pronto" após E9 (não é gate
    novo): ciclo planejar→implementar mediado→testar→auditar fechado; ressalva
    de que UI sem SSE até E11 e correção manual até E10; marco de segurança
    técnica, distinto de um futuro "Freelance Ready" (não decidido agora).
  - Duas linhas novas em "Decisões adiadas": scaffold assistido de projeto
    novo (via Developer mediado a partir de E8, reusa `WriteFile`/`ApplyPatch`,
    sem capacidade nova, sem `git init` automatizado) e camada educacional
    opcional (endpoint de explicação em linguagem simples a partir de E9,
    reusa plan+diff+findings, sem nova entidade de banco, não bloqueia o
    fluxo principal, formato exato indefinido).
- Pendências: aguardando revisão do diff pelo Pedro antes de commitar. Nada
  commitado ou pushado.

---

## 2026-09-03 — Claude Sonnet 5 (effort high) — E3 Workspace Registry (implementação)

Plano revisado/aprovado pelo Pedro em sessão de planejamento; seguidas as 6
sub-etapas na ordem, cada uma com seu quality gate. **Nada commitado/pushado.**

### Arquivos criados

- **Backend**
  - `api/app/git_runtime/__init__.py` — `preflight(local_path) -> GitPreflight`
    (`is_git_repo/head/branch/dirty_file_count`); só leitura, nunca lança,
    timeout 5s, `git` via `shutil.which`.
  - `api/app/workspace/{__init__,errors,service,purge,purge_tokens}.py` — service
    layer: `create/list/get_workspace`, `update_workspace_status` (active⇄archived),
    `validate_local_path`, `git_preflight`, `purge_preview`, `execute_purge`,
    `PurgeTokenStore` (memória, TTL 60s, uso único, vinculado a workspace_id).
  - `api/app/api/workspaces.py` — router `/api/workspaces` (POST/GET, GET/PATCH
    `{id}`, GET `{id}/git`, GET `{id}/purge-preview`, POST `{id}/purge`); schemas
    Pydantic `extra="forbid"`.
  - `api/tests/test_git_runtime.py`, `test_workspace_service.py`,
    `test_api_workspaces.py`, `test_purge_tokens.py`, `test_api_workspace_purge.py`.
- **Frontend**
  - `src/config/appMode.ts` — `getAppMode()` / `isWorkspaceModeEnabled()` lê
    `import.meta.env.VITE_APP_MODE`; default = `HOSTED_COMMERCIAL_ONLY`.
  - `src/vite-env.d.ts` — `vite/client` + tipo de `VITE_APP_MODE` (não existia).
  - `src/services/workspaceApi.ts` — cliente fetch; lê o token da
    `<meta name="ff-session-token">`, remove a meta do DOM, monta
    `Authorization: Bearer`; em HOSTED **rejeita antes de chamar `fetch`**.
  - `src/context/WorkspaceProvider.tsx` — provider separado do `AppContext`,
    estado remoto, sem `localStorage`.
  - `src/pages/DevWorkspaces.tsx`, `src/pages/WorkspaceDetail.tsx` — lista+criação
    e aba Overview (git preflight + divergência + purga com prévia obrigatória e
    modal de confirmação); abas Context/Tasks desabilitadas.
  - `src/test/workspace-api.test.ts`, `dev-workspaces-ui.test.tsx`,
    `workspace-detail-ui.test.tsx`.

### Arquivos alterados

- `api/app/main.py` — engine/session_factory + `purge_token_store` em
  `app.state`; router registrado com `prefix="/api"`;
  `add_exception_handler(WorkspaceError, ...)`.
- `api/tests/conftest.py` — fixtures `api_client`/`auth_api_client` (schema migrado).
- `api/tests/test_architecture.py` — `subprocess` liberado **só** em
  `app/git_runtime/`, banido no resto (nova asserção); novo
  `test_git_runtime_e_somente_leitura` (varre verbos mutantes).
- `api/tests/test_auth_and_bootstrap.py` — 2 asserções ajustadas às rotas novas
  (comportamento de auth inalterado; `GET /api/health` segue a única rota pública).
- `src/App.tsx`, `src/components/Layout.tsx` (nav `navigation[]`), `src/main.tsx`
  (`<WorkspaceProvider>`), `src/styles/global.css` (bloco `.workspace-*`).

### Gates

1. git_runtime — repo limpo/sujo/não-repo/inexistente + `ruff`/`mypy`. **verde**
2. service — 5 tipos, sem `linked_project_id`, dup 409, inexistente 422,
   não-diretório 422, transição válida/inválida. **verde**
3. router TestClient — fluxos felizes/erro por rota + sem Bearer = 401. **verde**
4. purga — sem prévia/token de outro ws/expirado(mock)/reutilizado/ativo
   recusados; archive→preview→purge feliz. **verde**
5. `npm run lint` 0 warnings, `npm test` 82 passa, `npm run build` ok; HOSTED sem
   nenhuma chamada de rede (testado explicitamente em 2 arquivos). **verde**
6. suíte completa: backend 507 passed / 6 skipped (`ruff`/`ruff format`/`mypy`
   limpos); frontend 82 passed / lint / build. `app/workspace/` não importa
   `api`/`orchestrator`/`agent_runtime`/`tool_executor` (verificado). **verde**

### Decisões de implementação não 100% especificadas no prompt

1. **`path_runtime.inspect()` não sabe percorrer a cadeia léxica de um caminho
   raiz absoluto** (o ramo `allow_absolute` nunca foi ligado a
   `_lexical_chain_facts` na E2): com a policy padrão,
   `decide_path(inspect(local_path, root=local_path, allow_absolute=True))` nega
   **todo** diretório absoluto válido como `path.symlink_unverified`. Contornado
   validando o alvo-raiz com `require_verified_link_status=False`; UNC, device
   namespace, drive-relative, contenção, cross-volume e segredos seguem valendo, e
   `prevalidate_path_syntax` roda com a policy cheia. **Follow-up recomendado:**
   ensinar `_lexical_chain_facts` o caso absoluto.
2. Incluí `prevalidate_path_syntax` no `validate_local_path` (o prompt citou só
   `inspect` + `decide_path`) — fail-closed, pega `..`/nome reservado/ADS/8.3/`~`.
3. `test_architecture.py`: banir `subprocess` em todo lugar (E2) era incompatível
   com o git preflight da E3 (deliverable de roadmap, na interface de
   `git_runtime/`). Passou a ser liberado só sob `app/git_runtime/`.
4. "Transição de status inválida" = pedir o status atual (a máquina só tem
   `active⇄archived`) → 409. Valor fora do enum → 422 no Pydantic.
5. Campos exatos dos schemas Pydantic derivados de `02-data-model.md` §1 (o plano
   referenciado não estava neste contexto). Todos `extra="forbid"`.
6. `repository_url` redigido **na resposta** (`safety.redact` em `_to_response`);
   valor cru mantido no banco.
7. Proteção de dados de benchmark (`02` §11 regra 6) **não implementada** — sem
   `Run`/`AuditFinding` para exercitar; adiada para E9+.
8. `PurgeTokenStore.consume` descarta o token em qualquer lookup (inclusive
   workspace errado / expirado), não só no sucesso.
9. Frontend: modo por `VITE_APP_MODE` (unset ⇒ HOSTED, então `deploy.yml`
   intocado segue produzindo o build hospedado); o build servido pelo backend
   precisa passar `VITE_APP_MODE=local_dev_workspace`.
10. Item de menu "Dev Workspaces" aparece nos dois modos; em HOSTED a página
    mostra "disponível apenas na execução local" e não chama a API.
11. `linked_project_id` é `input` de texto simples no formulário (sem seletor de
    `Project` comercial) — mantém `WorkspaceProvider` desacoplado do `AppContext`.
12. `useWorkspaces()` devolve um fallback desabilitado quando não há
    `WorkspaceProvider` montado (não lança) — preserva testes que renderizam
    `<App>` sem o provider.

- Pendências: **aguardando revisão do Pedro antes de qualquer commit.** E4 não
  iniciada. Nenhuma migration nova (as 7 tabelas da E2 já bastam). `docs/` não
  tocado.

---

## 2026-09-03 — Claude Sonnet 5 (effort high) — E3: correção da auditoria E3-AUD-001..007

Rodada de correção sobre os findings da auditoria Codex da E3 (o relatório completo
do Codex **não** foi colado nesta sessão — só as instruções de remediação por
finding, no prompt do Pedro). **Nada commitado/pushado**; ainda precisa de segunda
rodada do Codex confirmando GREEN.

### E3-AUD-002 (bloqueador do gate) — mesma origem, fim a fim

- `vite.config.ts`: `base` agora é `/` quando `VITE_APP_MODE=local_dev_workspace`
  (build servido pelo backend); `/freelance-focus-dashboard/` **só** no build sem essa
  variável (Pages) — `deploy.yml` intocado. Verificado: build local emite
  `src="/assets/…"`, build Pages emite `/freelance-focus-dashboard/assets/…`.
- `vite.config.ts` modo dev: `server.proxy` de `/api` → FastAPI
  (`FF_DEV_API_TARGET`, default `http://127.0.0.1:8756`); plugin
  `ff-local-session-token` injeta `<meta name="ff-session-token">` no HTML servido
  pelo Vite dev a partir de `process.env.FF_DEV_SESSION_TOKEN` (canal privado, sem
  prefixo `VITE_`, nunca em `.env`) e marca esse HTML como `no-store`; `host`
  `127.0.0.1`, `cors:false`, `allowedHosts` de loopback.
- **GATE**: `api/tests/test_web_spa_integration.py` — carrega `/` (dist com forma de
  build real, base `/`), resolve e busca o asset `/assets/…` real servido pelo
  FastAPI, extrai o token da `<meta>` do HTML **servido** (não um mock) e faz
  `GET`+`POST /api/workspaces` autenticados de verdade. Passa.

### E3-AUD-003 — git preflight estritamente somente-leitura

- `app/git_runtime/__init__.py`: toda invocação agora é
  `git -c core.fsmonitor=false -C <path> …`, com `env` **mínimo** (allowlist de
  ~20 vars; fora ficam `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE`/`GIT_CONFIG`/…)
  mais `GIT_OPTIONAL_LOCKS=0` e `GIT_TERMINAL_PROMPT=0`.
- Testes (`test_git_runtime.py`): (a) monkeypatch de `subprocess.run` confirma as
  flags/env em toda chamada; (b) `.git/index` (bytes + mtime) inalterado após o
  preflight mesmo com mtime de arquivo rastreado forçado; (c) `core.fsmonitor`
  configurado com hook-sentinela — sanity prova que dispara num `git status` normal,
  e o preflight **não** o dispara.

### E3-AUD-005 — validação de Origin / Sec-Fetch-Site em métodos mutantes

- `app/api/security.py`: `MUTATING_METHODS`, `origin_is_local`,
  `sec_fetch_site_allows_write`, `same_origin_write_allowed` (casamento total, mesmo
  rigor de `host_is_local`; `null` e `*.evil.com` recusados; `same-site` aceito para
  o proxy do Vite dev).
- `app/main.py`: `local_guard` recusa `POST/PATCH/PUT/DELETE` sob `/api/` com
  `403 {"code":"cross_origin_denied"}` quando `Origin` é de outra origem ou
  `Sec-Fetch-Site: cross-site` — **antes** da checagem de token.
- `api/tests/test_origin_validation.py`: helpers puros + HTTP real (Origin ausente
  passa; loopback passa; divergente 403; `cross-site` 403; `GET` divergente ainda
  passa; 403 vem antes do token). Clientes não-browser (TestClient) não mandam esses
  headers → suíte E3 anterior intacta.

### E3-AUD-006 — não afirmar remoção de artefato que não acontece

- `app/workspace/purge.py`: `_count_purgeable` agora fixa `artifacts=0` (era
  `count(distinct rendered_context_hash)`), com comentário: a purga não remove blob
  de disco — GC por conteúdo ([02] §5, §11 regra 5) não existe ainda. Docstring do
  módulo e de `PurgeCounts` reforçados. `manifests` continua sendo contagem real.
- `test_api_workspace_purge.py`: novo teste insere um `ContextManifest` real e
  confirma `manifests == 1` mas `artifacts == 0` na prévia.

### E3-AUD-007 — invariante `name ≤ 120` na camada de domínio

- `app/workspace/errors.py`: `InvalidWorkspaceName` (422, `invalid_workspace_name`).
- `app/workspace/service.py`: `create_workspace` valida `1 ≤ len(name.strip()) ≤ 120`
  antes de tocar o banco (SQLite não força tamanho de VARCHAR). Constante
  `_MAX_WORKSPACE_NAME_LEN`.
- Testes: serviço (121 → 422, 120 → ok, só-espaços → 422) e HTTP (121 → 422).

### E3-AUD-001 — aceitar e documentar (sem código)

- `app/workspace/service.py`: `# TODO(E3-AUD-001, antes de E4/E7)` explícito na linha
  do `dataclasses.replace(..., require_verified_link_status=False)`, apontando que a
  correção real é ensinar `path_runtime._lexical_chain_facts` o caso de raiz
  absoluta. Escopo confirmado correto pela auditoria. **Não** implementado agora.
- **Item de atenção**: antes de E4/E7, `_lexical_chain_facts` precisa percorrer a
  cadeia de um caminho-raiz absoluto; enquanto isso, a verificação de link do
  alvo-raiz do workspace fica desligada (as demais checagens de path seguem ativas).

### E3-AUD-004 — aceitar e documentar (sem código)

- `app/workspace/purge.py`: docstring do módulo agora diz explicitamente que a
  proteção de dados de benchmark ([02] §11 regra 6) é **decisão consciente adiada**
  para E9/E12, não esquecimento (risco real hoje = 0, sem `Run`/`AuditFinding`).
- `docs/architecture/07-roadmap-v1.md` (autorização explícita do Pedro nesta sessão,
  escopo restrito à tabela "Decisões adiadas"): nova linha "Proteção de dados de
  benchmark na purga" + nota na linha "Forma exata da confirmação de purga"
  registrando que a E3 escolheu o token de curta duração.

### Verificação final

- Backend: **541 passed / 6 skipped** (os 6 skips são pré-existentes: symlink/volume
  no Windows). `ruff` · `ruff format` · `mypy` limpos.
- Frontend: **82 passed** (16 arquivos) · `eslint` · `tsc -b` · `vite build` (Pages e
  local) ok.
- `docs/` alterado **apenas** na tabela "Decisões adiadas" de `07-roadmap-v1.md`,
  com autorização.
- Pendências: **segunda rodada do Codex** precisa confirmar GREEN antes do commit.

---

## 2026-09-03 — Claude Sonnet 5 (effort high) — E3: correção da 2ª rodada de auditoria (E3-AUD2-001..005)

Segunda rodada Codex sobre a E3. Desta vez **todos os 5 findings são para corrigir**
(nenhum "aceitar e documentar"). Relatório completo do Codex não colado nesta sessão —
só as instruções de remediação por finding, no prompt do Pedro. **Nada commitado.**

### E3-AUD2-002 — teste fim a fim com build real (feito primeiro)

- `api/tests/test_web_spa_integration.py` reescrito: fixture session-scoped roda
  `npx vite build --outDir <tmp> --emptyOutDir` **de verdade** com
  `VITE_APP_MODE=local_dev_workspace`; nada de HTML/JS/CSS fabricado. O teste carrega
  `/`, extrai os `/assets/<hash>.{js,css}` do `index.html` **real**, baixa cada um do
  FastAPI (200 + conteúdo), confirma que o JS é o nosso bundle
  (`"workspace_mode_disabled" in js_body`), extrai o token da `<meta>` injetada pela
  FastAPI e faz `GET`+`POST /api/workspaces` autenticados. Pula (não falha) se `npx` ou
  `node_modules` faltarem — o CI do backend não tem Node.
- **Por que prova a regra, não o caso:** o `base` e os nomes de asset agora vêm do
  Vite de verdade; qualquer regressão em `vite.config.ts` que quebre o `base` local, ou
  no `web.py` que quebre a injeção do token, ou no mount `/assets`, faz o teste falhar.

### E3-AUD2-001 — hot-reload adiado para E11 (decisão do Pedro)

- **Não** construído o launcher. `vite.config.ts`: removido o plugin
  `localSessionTokenPlugin` (era infra de launcher — lia `FF_DEV_SESSION_TOKEN`);
  mantidos `base` e `server` (proxy `/api` com `changeOrigin:false`, `host`, `cors:false`,
  `allowedHosts`).
- `CLAUDE.md`: nova subseção "AI Dev Workspace — fluxo de dev local (até a E11)":
  `VITE_APP_MODE=local_dev_workspace npm run build` servido pela FastAPI é o fluxo
  suportado; `npm run dev` isolado **não** é (sem token → 401).
- `docs/architecture/07-roadmap-v1.md` "Decisões adiadas": nova linha "Launcher de
  hot-reload → E11"; e a linha "Proteção de dados de benchmark na purga" foi **removida**
  de lá (deixou de ser adiada — implementada agora, ver AUD2-005). `docs/` alterado só
  nessa tabela, com a autorização do prompt.
- Gate oficial do fluxo suportado: o teste do AUD2-002.

### E3-AUD2-003 — allowlist de env do git por igualdade de conjunto

- `app/git_runtime/__init__.py`: **removidos** `GIT_EXEC_PATH` e `GIT_TEMPLATE_DIR` da
  allowlist (git resolve exec-path do próprio binário; template dir só serve `git init`).
  Agora **nenhum `GIT_*` entra**. `_git_env(source=None)` ganhou parâmetro opcional só
  para teste; overrides isoladas em `_GIT_ENV_OVERRIDES`.
- `test_git_runtime.py::test_git_env_e_exatamente_a_allowlist`: passa um ambiente
  contaminado com ~25 `GIT_*` arbitrários + lixo e afirma
  `_git_env(contaminado) == _EXPECTED_GIT_ENV` — **igualdade de conjunto**, não checagem
  de nomes conhecidos. `test_preflight_passa_travas_de_somente_leitura` passou a afirmar
  `env == _git_env()` e `nenhuma chave GIT_* fora das overrides` em toda invocação real.
- **Por que prova a regra:** qualquer variável a mais (nomeada ou não) ou valor de
  allowlist alterado quebra a asserção. É o fechamento que faltou nas 2 rodadas
  anteriores, onde `GIT_EXEC_PATH` passou porque o teste só olhava nomes conhecidos.

### E3-AUD2-004 — Origin tem de bater com o Host

- `app/api/security.py`: `origin_is_local` → `origin_matches_host(origin, host, *,
  request_scheme)` — compara **esquema+host+porta** do Origin contra a autoridade do
  `Host` efetivo. Loopback numa porta diferente da servida é **negado**; `localhost` ≠
  `127.0.0.1`; esquema diferente negado; porta default por esquema. `sec_fetch_site_
  allows_write` restrito a `{same-origin, none}` — `same-site` e **qualquer valor
  desconhecido** (inclusive `""`) recusados. `same_origin_write_allowed` ganhou
  `host_header` + `request_scheme`. `app/main.py` passa `request.headers.get("host")` e
  `request.url.scheme`.
- Proxy do Vite dev tratado: `changeOrigin:false` preserva o `Host` (`localhost:5173`),
  igual ao `Origin` → casa. Documentado no `vite.config.ts`.
- `test_origin_validation.py` reescrito: `origin_matches_host` parametrizado (porta
  loopback diferente NEGADA; origem exata aceita; nome de host diferente negado; esquema
  diferente negado; porta default; `null`); `sec_fetch_site` (`same-site` e desconhecido
  negados); HTTP real (porta diferente 403; origem exata 201; `Sec-Fetch-Site`
  desconhecido 403; proxy Vite dev simulado com `Host` sobrescrito 201).
- **Por que prova a regra:** os casos negados são justamente "loopback mas não a origem
  servida" — o buraco que o Codex apontou. E o proxy-dev tem seu próprio caso positivo,
  então apertar a regra não quebrou o fluxo futuro.

### E3-AUD2-005 — proteção de dados de benchmark implementada (não adiada)

- `app/workspace/errors.py`: `WorkspaceBenchmarkProtected` (409,
  `workspace_purge_benchmark_protected`).
- `app/workspace/purge.py`: `_has_benchmark_evaluation(session, workspace_id)` — parte
  das tasks do workspace, sobe ao `benchmark_group_id`, e procura `Run` **ou**
  `AuditFinding` de `purpose = benchmark_evaluation` em **qualquer** task do grupo
  (inclusive de outro workspace). `PurgeCounts` ganhou `benchmark_protected: bool`;
  `_count_purgeable` o preenche; `execute_purge` recusa antes do `session.delete` se
  `True` (token já consumido continua consumido). Docstring do módulo reescrito: a regra
  6 de §11 passou de "adiada (E3-AUD-004)" para "implementada (E3-AUD2-005)" — a
  justificativa do Codex é que as colunas existem desde a E2 e a regra é congelada (R8).
- `app/api/workspaces.py`: `PurgePreviewResponse` ganhou `benchmark_protected`; a rota de
  prévia o devolve. `PurgeResultResponse` (o que foi removido) segue com 6 chaves.
- `test_api_workspace_purge.py`: (1) avaliação numa task de **outro** workspace do mesmo
  `benchmark_group_id` → prévia `benchmark_protected:true`, purga 409, workspace intacto;
  (2) ramo do `Run(purpose=benchmark_evaluation)`; (3) grupo de benchmark **sem**
  avaliação → `benchmark_protected:false`, purga acontece normal.
- **Por que prova a regra:** o caso (1) coloca a avaliação fora do workspace purgado —
  uma checagem "runs deste workspace" passaria batido. O caso (3) prova precisão (ter
  `benchmark_group_id` não basta). Cobre os dois ramos da condição OR.

### Verificação final

- Backend: **552 passed / 6 skipped** (skips pré-existentes: symlink/volume no Windows).
  `ruff` · `ruff format` · `mypy` limpos.
- Frontend: **82 passed** (16 arquivos) · `eslint` · `tsc -b` · `vite build` (Pages e
  local, `base` correto em cada um).
- `docs/` alterado só na tabela "Decisões adiadas" de `07-roadmap-v1.md`. `CLAUDE.md`
  ganhou a subseção do fluxo de dev local.
- Pendências: **terceira rodada do Codex** (focada e curta) antes de qualquer commit.

---

## 2026-09-03 — Claude Sonnet 5 (effort medium) — E3: correção da 3ª rodada de auditoria (E3-AUD3-001, E3-AUD3-002)

3ª rodada Codex sobre a E3. Dois findings, decisão do Pedro já tomada (especificação a
seguir, não decisão em aberto). Escopo de arquivos **restrito** pelo prompt. **Nada
commitado.** Relatório completo do Codex não colado nesta sessão — só as instruções.

### Arquivos permitidos e tocados

- `docs/architecture/07-roadmap-v1.md` — linhas E2, E11, tabela "Decisões adiadas", + nota.
- `docs/adr/0001-local-backend-fastapi-sqlite.md` — **só addendum no fim** (+30 linhas,
  0 remoções; item 7 e tudo antes intactos, confirmado por `git diff`).
- `.github/workflows/cross-stack-ci.yml` — **novo**.
- `api/tests/test_web_spa_integration.py` — só o comportamento de skip/fail.
- `AGENT_LOG.md`.
- **`api-ci.yml` e `deploy.yml`: `git diff` VAZIO** — preservados intactos (ADR-0001 item 5).

### E3-AUD3-002 — a E2 não entregou o canal Vite dev do ADR-0001 item 7

Correção **coordenada** em 3 pontos + 1 nota, sem reabrir a E2 (que continua concluída e
auditada — só a descrição textual muda):

- **Roadmap, linha E2:** removida do Entregável a afirmação de que o `transformIndexHtml`
  por canal em memória no Vite dev fora entregue. Ficou "`LocalSessionToken` injetado no
  HTML **pela FastAPI no build compilado**". No Gate, "**a única rota sem token** nos dois
  fluxos locais" → "a única rota sem token"; "o token **de dev** não entra no bundle" →
  "o token não entra no bundle" (o eco da mesma sobre-alegação).
- **Roadmap, nota adjacente à tabela de Etapas:** blockquote registrando o que a E2 de
  fato entregou, que a parte launcher/Vite dev foi identificada na auditoria da E3 como
  não implementada e diferida para a E11, e que nenhuma implementação da E2 foi reaberta.
- **Roadmap, linha E11:** Entregável/Gate ampliados para listar **explicitamente os 6
  itens** — (1) launcher local do Vite; (2) canal privado em memória para o
  `LocalSessionToken`; (3) `transformIndexHtml` injetando a `<meta>` no modo dev com
  `no-store`; (4) garantias de não vazamento do token (bundle/`.env`/log); (5)
  loopback/`Host`/CORS do Vite dev **ligados ao guarda `same_origin_write_allowed` /
  `origin_matches_host` já feito na E3**; (6) **gate automatizado** que sobe FastAPI +
  Vite dev de verdade e prova o fluxo ponta a ponta (não só "o código existe").
- **ADR-0001, addendum "auditoria da E3 (2026-09-03)"** ao final: registra com data que a
  parte launcher/Vite dev do item 7 **não foi entregue pela E2**; que a **auditoria
  original da E2 não detectou** — só a da E3 (E3-AUD3-002); que foi **diferida para a
  E11**; e que **a decisão do item 7 permanece válida** — só a entrega é reagendada.
- **Roadmap, "Decisões adiadas", linha do launcher:** "Reabre quando" agora aponta para
  as **duas fontes** (addendum do ADR-0001 + linha da E11), não é mais nota solta.

### E3-AUD3-001 — workflow cross-stack dedicado

- **`.github/workflows/cross-stack-ci.yml` (novo):** dispara em `api/**`, `vite.config.ts`,
  `src/**`, `index.html`, `package*.json`, `.nvmrc`, e o próprio arquivo. Setup Node
  (`node-version-file: .nvmrc` → 22) **e** Python 3.11. `npm ci` + instala o backend
  (mesmo comando do `api-ci.yml`). Roda **exclusivamente**
  `pytest tests/test_web_spa_integration.py`. **Sem `needs`**, não é `needs` de ninguém,
  não toca `api-ci.yml`/`deploy.yml` — totalmente independente (ADR-0001 item 5). Define
  `FF_CROSS_STACK_CI=1`.
- **`test_web_spa_integration.py`:** nova função `_node_unavailable(reason) -> NoReturn`.
  `_CROSS_STACK_GATE = os.environ.get("FF_CROSS_STACK_CI") == "1"` (só esse workflow
  define). Com o gate ligado E Node ausente → `pytest.fail` (o gate não pode ficar mudo).
  Sem o gate → `pytest.skip` como antes. Docstring atualizado.

### Quality gates verificados localmente

1. Node presente (local): `test_web_spa_integration.py` **passa** exatamente como antes.
2. `FF_CROSS_STACK_CI=1` + `npx` fora do `PATH` (só a pasta do Node removida, resto do
   ambiente intacto): pytest **exit 1** (`Failed`/`ERROR` no fixture). Sem a variável, o
   mesmo cenário → **exit 0**, `SKIPPED`. Provado antes de depender do GitHub Actions.
3. `git diff .github/workflows/api-ci.yml` e `git diff .github/workflows/deploy.yml`:
   **ambos vazios**.
4. ADR-0001: `git diff` = `30 insertions(+), 0 deletions` — item 7 e "## Revisões"
   intactos, só o addendum novo no fim.
5. Suíte completa: backend **552 passed / 6 skipped**, `ruff`/`format`/`mypy` limpos;
   frontend **82 passed**, `eslint` limpo.

### Nota de implementação

O `pytest.fail` no fixture session-scoped é reportado como `ERROR` (pytest classifica
falha em fixture assim), não `FAILED` — mas o efeito é idêntico: exit code 1, CI vermelha,
gate não silencioso. `pytest.fail()` foi chamado exatamente como o prompt pede.

- Pendências: **quarta rodada do Codex** (focada e curta) antes de qualquer commit.

---

## 2026-09-03 — Claude Sonnet 5 (effort low) — E3: correção E3-AUD4-001 (pin do AnyIO)

Finding pontual da 4ª rodada Codex. Arquivos permitidos: `api/pyproject.toml`,
`AGENT_LOG.md`. **Nada commitado.**

### Problema

Instalação limpa do backend (`pip install -e ".[dev]"`) resolvia **AnyIO 4.15.0**, que
transformou `anyio.abc.BlockingPortal` num lazy-import que emite `DeprecationWarning` ao
ser acessado. O Starlette 0.46.2 (que a FastAPI 0.115 traz) ainda referencia esse alias no
nível de módulo (`starlette/testclient.py:37`,
`_PortalFactoryType = ...ContextManager[anyio.abc.BlockingPortal]`). Com
`[tool.pytest.ini_options] filterwarnings = ["error"]` o aviso vira erro e **a coleta dos
testes trava** já no import do `conftest.py`. O venv local das rodadas anteriores tinha
AnyIO 4.14.2 (instalado antes do 4.15 sair) e **mascarava** o problema.

### Correção

Uma linha em `api/pyproject.toml`, dentro de `[project.dependencies]`:
`"anyio<4.15"` (com comentário explicando o porquê). **Teto mínimo** — não trava mais do
que o necessário: o piso `>=3.6.2` já vem do Starlette, e `<4.15` é exatamente o ponto em
que o alias passou a avisar. `filterwarnings = ["error"]` **não** foi tocado — a política
está correta, o problema era a versão da dependência.

### Verificação num venv NOVO (não o `.venv` local já usado nas rodadas anteriores)

Comando exato para criar o venv limpo:
`"C:/Users/pedro/AppData/Local/Programs/Python/Python311/python.exe" -m venv "<scratchpad>/clean-venv-aud4"`
(interpretador base Python 3.11.9, fora do repo e fora do OneDrive).

1. `pip install -e ".[dev]"` (rodado de `api/`, mesmo comando de `api-ci.yml` /
   `cross-stack-ci.yml`) → resolveu **anyio 4.14.2** (satisfaz `<4.15`), starlette 0.46.2,
   httpx 0.28.1, fastapi 0.115.14.
2. `import starlette.testclient` sob `warnings.simplefilter("error")` (réplica de
   `filterwarnings=["error"]`) → **OK**.
3. **Suíte completa nesse venv novo**: `552 passed, 6 skipped` (os 6 skips são os
   pré-existentes de symlink/volume no Windows).
4. `ruff check .` → *All checks passed*; `ruff format --check .` → *49 files already
   formatted*; `mypy` → *Success: no issues found in 47 source files*.
5. **Contrafactual** (no mesmo venv, forçando `pip install "anyio>=4.15"` por cima):
   `pytest --co` falha com `DeprecationWarning: The anyio.abc.BlockingPortal alias is
   deprecated` → `ImportError while loading conftest`. Reinstalar `-e ".[dev]"` traz o
   4.14.2 de volta pelo pin. Confirma que o pin é *load-bearing*, não cosmético.

- Pendências: **5ª rodada do Codex** (confirmação pontual deste finding) antes de commit.

---

## 2026-09-03 — Claude Sonnet 5 (effort low) — E3: 5ª rodada de auditoria Codex — VEREDITO GREEN

Rodada de confirmação pontual do finding E3-AUD4-001 (pin do AnyIO). **Nenhum finding
novo.**

- Venv **Python 3.11.9 novo e isolado** (não o `.venv` das rodadas anteriores) confirmou
  que o pin `anyio<4.15` faz a instalação limpa resolver **AnyIO 4.14.2**.
- Suíte completa nesse venv novo: **552 passed / 6 skipped** (os 6 skips são os
  pré-existentes de symlink/volume no Windows).
- `ruff check` / `ruff format --check` / `mypy` — **limpos**.
- **Contrafactual** reproduzido: forçar `anyio>=4.15` reintroduz o
  `DeprecationWarning: anyio.abc.BlockingPortal ...` → `ImportError while loading
  conftest`, confirmando que o pin é a **causa raiz** da correção, não paliativo.

**Veredito: GREEN. Commit e push liberados.**

### Fechamento do processo da E3

- 5 rodadas de auditoria independente (Codex): E3-AUD1 (007 findings), E3-AUD2 (005),
  E3-AUD3 (002), E3-AUD4 (001), E3-AUD5 (confirmação). Todos os findings corrigidos e
  reverificados; os que exigiam ambiente limpo foram checados em venv novo.
- **Convenção de branch por fase** (registrada em 2026-09-01): a E3 é commitada na branch
  `claude/ai-dev-e3-workspace-registry` e mesclada em `main` por **fast-forward** — mesmo
  padrão da E2 (`claude/ai-dev-e2-backend-safety-foundation`).
- Arquivos da E3: ver `git show --stat` do commit. Cobre backend (`app/workspace/`,
  `app/git_runtime/`, guarda de origem em `app/api/security.py`), frontend
  (`WorkspaceProvider`, telas `DevWorkspaces`/`WorkspaceDetail`, `workspaceApi`), docs
  (addendum do ADR-0001, correções no roadmap) e CI (`cross-stack-ci.yml`).
- Pendências: **nenhuma** para a E3. Próxima fase: E4 (Context Registry), branch nova a
  partir de `main` atualizado.

---
