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

## 2026-09-05 — Claude Sonnet 5 (effort medium) — E4 (planejamento): formalização de `verification_commit` e do baseline de `source_hash`

Tarefa **SOMENTE DOCUMENTAÇÃO** — formalização de algo já decidido na sessão de
planejamento da E4, sem decisão nova e sem código. `docs/` alterado com autorização
explícita do Pedro nesta conversa, escopo restrito aos dois arquivos abaixo.

- Arquivos alterados:
  - `docs/architecture/03-context-architecture.md` — §3 (*staleness*): **duas
    subseções novas ao final da seção**, antes do `---`. (a) `verification_commit`:
    SHA capturado uma única vez no início de cada verificação, imutável durante ela;
    tabela mapeando E6+ (`= planning_base_commit`) e E4/`POST /context/verify`
    (`= HEAD` do workspace lido uma vez). (b) `source_hash`/`source_hash_commit` como
    baseline confirmado, escrito só na criação com `source_refs` não vazio ou no
    `PATCH` explícito de `source_refs`; `verify()` nunca escreve nesses campos, só
    atualiza `state`/`stale_reason`/`last_verified_at`/`last_verified_commit`; entrada
    `stale` não volta a `fresh` só por `verify()` repetir; editar
    `body`/`title`/`structured`/`tags` não toca o baseline; `source_refs` → lista
    vazia zera o baseline (`null`) e volta a `fresh` sem motivo.
  - `docs/adr/0006-context-registry-selective-context.md` — **addendum ao final**
    ("planejamento da E4 (2026-09-05)"). Item 6 e todo o texto anterior intactos.
    Registra que a distinção baseline-vs-`verify()` foi tornada normativa em [03] §3
    e que `verification_commit` generaliza o que antes assumia sempre
    `planning_base_commit` — nenhuma decisão original revertida.
  - `AGENT_LOG.md` — esta entrada.

- Decisões tomadas: **nenhuma nova.** Só formalização normativa para eliminar
  ambiguidade antes de implementar a E4.

- GATE: `git diff` dos dois docs = **51 insertions(+), 0 deletions(-)** (03: +41 /
  ADR-0006: +10). Só adição, zero remoção de texto original. Verificado.

- Pendências: diff revisado e commit/push autorizados pelo Pedro nesta sessão —
  commitado direto em `main` (`docs:`, sem branch de fase, por ser só documentação de
  planejamento). Implementação da E4 (Context Registry) não iniciada.

---

## 2026-09-05 — Claude Opus 5 (effort high) — E4: Context Registry (implementação completa)

Implementação da E4 conforme o plano revisado e aprovado pelo Pedro nesta sessão, nas nove
sub-etapas, cada uma com seu quality gate. **Nada commitado, nada pushado.** Vai para
auditoria independente do Codex antes de qualquer commit.

### Arquivos alterados

**Backend — novos**

- `api/app/context_engine/` (pacote novo): `__init__.py`, `source_ref_expansion.py` (o
  expansor canônico), `content_hash.py`, `verification.py`, `service.py`, `errors.py`,
  `types.py`.
- `api/app/api/context.py` — router e schemas Pydantic (`extra="forbid"` em todos).
- `api/tests/`: `test_git_runtime_context_reads.py`, `test_source_refs_glob_syntax.py`,
  `test_source_ref_expansion.py`, `test_context_service.py`,
  `test_context_verification.py`, `test_api_context.py`, `context_helpers.py`.

**Backend — modificados**

- `api/app/git_runtime/__init__.py` — `list_tree` e `working_tree_status`; `_run_git` com
  `encoding="utf-8"` explícito; `_is_hex` → `_SHA1_RE` estrito.
- `api/app/safety/source_refs.py` — `allow_glob_syntax`; correção de
  `normalize_source_ref` (ver "Correções de defeito pré-existente").
- `api/app/main.py` — router registrado; `_workspace_error_handler` →
  `_domain_error_handler`, servindo `WorkspaceError` e `ContextError`.
- `api/tests/conftest.py` — fixtures `repo_path`, `plain_path`, `workspace`,
  `workspace_sem_git`.
- `api/tests/test_architecture.py` — três testes novos (fronteira do `context_engine/`,
  dono único da gramática de glob, `safety/source_refs.py` não casa padrão).

**Frontend — novos**

- `src/services/contextApi.ts`, `src/pages/WorkspaceContext.tsx`,
  `src/utils/contextEntries.ts`, `src/utils/planningSeed.ts`,
  `src/test/context-entries.test.ts`, `src/test/workspace-context-ui.test.tsx`.

**Frontend — modificados**

- `src/pages/WorkspaceDetail.tsx` — aba Contexto ativa; painel por aba; `messageOf`
  deduplicado.
- `src/services/workspaceApi.ts` — `request` → `apiRequest` exportado.
- `src/context/AppContext.tsx` — `useOptionalApp`.
- `src/styles/global.css` — bloco "Context Registry (E4)".
- `src/test/workspace-detail-ui.test.tsx` — a aba Contexto passou de desabilitada a ativa.

**Intocados (confirmado por `git diff` vazio):** `docs/`, `.github/`, `CLAUDE.md`.

### O invariante central da fase, e como ele é sustentado

`source_hash`/`source_hash_commit` são o **baseline confirmado** ([03] §3). Um único ponto
do sistema os escreve — `service._apply_baseline`, chamado só a partir de
`_compute_baseline` —, e ele roda em exatamente dois momentos: criação com `source_refs`
não vazio, e `PATCH` que **informa** `source_refs`. `verification.verify_freshness` escreve
apenas `state`, `stale_reason`, `last_verified_at` e `last_verified_commit`, e nunca o
baseline. Consequência provada em teste: uma entrada `stale` não volta a `fresh` porque
alguém rodou `verify` de novo, nem porque alguém corrigiu uma digitação no corpo.

A distinção "campo ausente ≠ campo nulo" atravessa as três camadas: sentinela `UNSET` no
serviço, `model_fields_set` no router, e `buildEntryPatch` no frontend (que omite
`source_refs` quando a lista não mudou).

### Decisões tomadas — segurança e gramática

- **`source_ref_expansion.py` é o dono único da gramática.** `safety/source_refs.py`
  continua sendo só o envelope de caminho e propaga a `SafetyDecision` **verbatim**; toda
  recusa do expansor vive no prefixo `source_ref_expansion.*`, então dá para dizer pelo
  `rule_id` qual camada negou. Um teste de arquitetura por AST impede que
  `safety/source_refs.py` volte a casar padrão.
- **`allow_glob_syntax=True` desliga exatamente uma checagem** e nada mais. Provado por
  diferença sobre um corpus: para todo valor recusado com `False`, ou o `rule_id` era
  `glob_not_supported`, ou a decisão é idêntica com `True`.
- **`[!abc]` → `[^abc/]`**, com o `/` embutido na negação. Sem isso, a classe negada seria
  a única construção de segmento único capaz de atravessar o separador.
- **Sintaxe não suportada é recusada, nunca reinterpretada como literal:** `{`, `}`, `!`
  fora de classe, `[` sem fechamento, classe vazia, classe com `/`, faixa invertida.
  Tratá-las como literal (o que o `fnmatch` faz com `[` solto) é como nasce a segunda
  gramática que E2-AUD-003 proibiu.
- **Segredo é decidido arquivo por arquivo, depois de expandir**, e um único segredo nega o
  `source_ref` **inteiro** — nunca filtragem parcial. A checagem em `safety` continua, como
  primeira barreira barata que só consegue negar a mais.
- **`source_ref` que casa zero arquivo → `UNRESOLVED` → `state = unknown`**, não conjunto
  vazio. Um `source_hash` sobre `[]` seria legítimo, estável e nunca mais mudaria: falso
  `fresh` por outro caminho.
- **`list_tree`/`working_tree_status` devolvem `None` em falha, não `[]`.** "Não consegui
  ler" e "está vazio/limpo" não podem ser a mesma resposta.
- **`list_tree` só aceita SHA-1 completo** no parâmetro `commit`: ele entra no `argv`, e
  `--upload-pack=…` seria injeção de opção.
- **`-z` obrigatório nas duas leituras novas.** Sem ele o git aplica *C-quoting* em caminho
  não-ASCII, e o expansor casaria contra um caminho que não existe.
- **Sem `--full-tree`.** As duas leituras são relativas ao diretório corrente, que é o
  `local_path` — a mesma base dos `source_refs`. Verificado empiricamente que `ls-tree` e
  `status --porcelain=v2` concordam nessa base, inclusive com `local_path` em subdiretório.
- **Cobertura da Parte B é calculada pelo padrão compilado** (`SourceRefMatcher`), não pela
  lista já expandida: um arquivo `untracked` novo sob `src/` é coberto por `src/**` sem
  existir na árvore do `verification_commit`, e é o caso mais comum de todos.
- **Rename produz duas entradas** (destino e origem): quem cobria o caminho original foi
  igualmente invalidado.

### Correções de defeito pré-existente (fora do escopo pedido, mas *load-bearing*)

- **`normalize_source_ref` (E2).** O `./` era removido **antes** de colapsar `//`, então
  `.//src/app.py` normalizava para `/src/app.py` — a normalização *introduzia* uma barra
  inicial que o valor cru não tinha, e o cru passava a pré-validação como `RELATIVE`. O
  normalizado é justamente o que o expansor da E4 casa contra o `git ls-tree`, e nenhum
  caminho do git começa com `/`: o `source_ref` resolveria silenciosamente para zero
  arquivo. Correção: colapsar as barras primeiro. Nenhum teste da E2 precisou mudar;
  regressão coberta em `test_source_refs_glob_syntax.py`.
- **`_run_git` sem `encoding` explícito.** O `text=True` decodificava pela locale do
  processo, o que no Windows mangleria acentos vindos do `-z`. Passou a `utf-8`/`strict`;
  falha de decodificação vira o resultado neutro (*fail closed*). Afeta também o
  `preflight` da E3, na direção mais segura.
- **`_is_hex`** aceitava `0x…`, sinal e espaço. Substituído por `_SHA1_RE` estrito.

### Gates verificados

| Gate | Resultado |
| --- | --- |
| 1 — `git_runtime` | 27 testes novos; `env` do `subprocess` afirmado **igual** a `_git_env()`, reaproveitando `_EXPECTED_GIT_ENV` da E3 |
| 2 — `allow_glob_syntax` | suíte da E2 (`test_source_refs.py`, `test_security_regressions.py`) passa **sem alteração** — `git status` vazio nos dois arquivos |
| 3 — expansor | os 6 casos obrigatórios cobertos, mais gramática não suportada, árvore malformada e ponta a ponta com git real |
| 4 — CRUD/baseline | 8 domínios; `content_hash` verificado contra a fórmula do documento, não contra a implementação |
| 5 — verify | os 3 testes combinados, mais os 5 tipos de divergência coberta e a precisão (divergência fora da cobertura **não** deixa `stale`) |
| 6 — router | 401 em todas as rotas novas, 403 de outra origem, e os fluxos felizes e de erro de cada uma |
| 7 — import | 9 entradas do seed completo, conteúdo de cada uma, e reimport criando um segundo conjunto com a edição manual intacta |
| 8 — frontend | `eslint --max-warnings 0` limpo; **115 testes** (18 arquivos); `npm run build` nos dois modos |

**Suíte final:** backend **853 passed / 6 skipped** (os 6 skips são os pré-existentes de
symlink/volume no Windows); `ruff check` · `ruff format --check` · `mypy` limpos. Frontend
**115 passed**; `eslint` limpo; `tsc -b` + `vite build` (Pages e local) ok.

**Checagens estáticas dos invariantes de módulo:**

1. `subprocess` aparece em **um** arquivo do backend: `app/git_runtime/__init__.py`.
2. Tradução de metacaractere de glob para regex (`[^/]`, `[^/]*`, `.*`) aparece em **um**
   arquivo: `app/context_engine/source_ref_expansion.py`.
3. `fnmatch` aparece só em `app/safety/secrets.py` — a denylist de segredos de [04] §5,
   congelada desde a E2, sobre outro assunto e nunca aplicada a um `source_ref` como
   padrão. A exceção é **nomeada explicitamente** no teste de arquitetura para que uma
   terceira não passe despercebida.
4. `context_engine/` importa apenas `db`, `git_runtime`, `safety` (e os próprios módulos).
5. Nenhuma biblioteca de *matching* de padrão no frontend.

### Decisões de implementação não 100% especificadas no prompt

Todas estão comentadas no código, no ponto em que valem. As que merecem decisão explícita
do Pedro ou do Codex:

1. **`ProjectPlanningImport` foi nomeado `PlanningImport`.** O prompt pede o primeiro nome,
   mas `test_architecture.py::test_backend_nao_conhece_o_dominio_comercial` (E2) proíbe a
   substring `projectplanning` em **qualquer** arquivo do backend, e [03] §1 diz
   literalmente "o backend não sabe de onde veio". Manter o nome do prompt exigiria
   afrouxar um teste de arquitetura que existe para proteger o [ADR-0002]. Os `dataclass`
   internos seguem o mesmo critério (`PlanningSeed`, `PlanningSeedDecision`,
   `PlanningSeedRisk`), e o contrato HTTP é `snake_case` e anônimo. **Se o Pedro preferir o
   nome original, é o teste de arquitetura que precisa de uma decisão, não o schema.**
2. **`**` implementado literalmente como "qualquer caractere, incluindo `/`"** — a
   gramática escrita no prompt. Consequência: `a/**/b` exige **pelo menos um segmento
   intermediário** e não casa `a/b`, diferente do `gitignore`/`globstar`, onde `**` colapsa
   para nada. Segui a regra escrita, e o lado da divergência é o seguro (expansão menor
   nunca traz arquivo a mais). Coberto por teste explícito, com o comportamento documentado
   no docstring. **Vale confirmar se é a semântica desejada ou se o texto queria a
   convenção do `gitignore`.**
3. **`u` (conflito de merge) do `--porcelain=v2` mapeia para `modified`** — não existe um
   sexto rótulo em [03] §3, e o que importa é a divergência aparecer.
4. **`dirty_file_count` conta caminhos distintos**, então um rename conta 1, embora produza
   2 entradas tipadas.
5. **`state` é recalculado por inteiro na criação e no `PATCH` de `source_refs`**, incluindo
   a Parte B. Logo, uma entrada pode **nascer** `stale(working_tree)` se o caminho coberto
   já estiver sujo. Segue de "state recalculado" no gate 4.
6. **`create_entry`/`update_entry` validam e expandem antes de qualquer mutação.** Um
   `source_ref` recusado levanta 422 sem ter tocado o objeto — a atomicidade não depende do
   `rollback` do chamador.
7. **`origin` não é aceito no `POST`** (sempre `manual`) e **`domain` não é editável no
   `PATCH`**. Forjar a procedência esvaziaria o que [03] §1 usa para explicar de onde o
   contexto veio.
8. **`source_hash` serializa cada par como lista `["path", "sha"]`** — `canonical_json`
   recusa `tuple` por decisão de [02] §7.
9. **`normalize_text` usa `rstrip()`** (espaço, tabulação e `\r` residual), não só espaço;
   `CRLF→LF` é aplicado literalmente como escrito. **`normalize_structured`** aplica a
   normalização recursivamente a toda string de `structured`, chave e valor — o documento
   diz "structured normalizado" sem detalhar.
10. **Seed:** `date: null` nas decisões (inventar `utcnow()` registraria a data da
    importação como se fosse a da decisão); `probability`/`impact` **omitidos** nos riscos
    por serem opcionais em [03] §1; título de risco vem da primeira linha, truncado em 255
    com `…`, e o corpo preserva o texto inteiro; campo vazio não vira entrada.
11. **`DELETE` devolve 204 com `response_class=Response`** — o FastAPI recusa 204 com corpo.
12. **Frontend:** `useOptionalApp` novo (a área do AI Dev Workspace não pode exigir o
    provider comercial); `ContextTab` recebe `plannings` como *prop* opcional com origem
    padrão no contexto do app, deixando a fronteira do [ADR-0002] visível na assinatura;
    a **prévia** da importação é uma estimativa derivada da mesma tabela congelada de [03]
    §1 (a contagem real vem na resposta), porque as rotas fixadas não têm *dry-run*;
    `structured` é editado como JSON com validação antes de chamar a API.
13. **`tests/test_context_verification.py` usa `state_of`/`reason_of`** para ler o atributo
    através de função: o mypy estreita `entry.state` para o literal da primeira asserção e
    não sabe que `verify_freshness` o reescreve.

### Pendências

- **Auditoria independente do Codex.** Dado o componente de segurança novo (o expansor),
  esperam-se múltiplas rodadas, como na E3.
- Nada commitado, nada pushado. Branch de fase (`claude/ai-dev-e4-context-registry`, pelo
  padrão das E2/E3) ainda **não criada** — aguardando GREEN.
- E5 (Context Router + file map) não iniciada.

---

## 2026-09-05 — Claude Opus 5 (effort high) — E4: correção da semântica de `**` e reforço do teste de regressão

Duas correções pedidas pelo Pedro antes da **primeira** rodada de auditoria Codex sobre a
E4. **Nada commitado, nada pushado.**

### Arquivos alterados

- `api/app/context_engine/source_ref_expansion.py` — `_append_double_star` (novo) e
  docstring do módulo reescrito na seção da gramática.
- `api/tests/test_source_ref_expansion.py` — bloco novo de testes normativos de `**`;
  removidos os dois testes que fixavam a semântica antiga.
- `api/tests/test_source_refs_glob_syntax.py` — teste de regressão da normalização
  reescrito, com contrafactual explícito.
- `AGENT_LOG.md` — esta entrada.

**Nada mais foi tocado.** Em particular: `app/safety/` **não** ganhou nenhuma linha de
gramática (a correção é inteiramente do `context_engine/`), e `docs/`, `.github/` e
`CLAUDE.md` seguem com `git diff` vazio.

### Correção 1 — `**` é zero ou mais segmentos

`a/**/b` agora casa `a/b`, `a/x/b` e `a/x/y/b`; `docs/**/*.md` alcança `docs/readme.md`
**e** `docs/sub/readme.md`. Confirmado nesta sessão que nenhum ADR nem documento de
arquitetura especifica a gramática a nível de caractere — a decisão é do `context_engine`,
dono único da gramática, e passou a estar registrada como normativa no docstring do módulo.

**O que estava errado.** Todo `**` virava `.*`, e as duas barras literais em torno dele
continuavam no padrão: `a/**/b` compilava para `a/.*/b`, que exige pelo menos um segmento
intermediário. **A correção é consumir o `/` seguinte junto com o `**`**, para que a barra
também caia dentro do grupo opcional: `a/(?:.*/)?b`. É o `?` sobre o grupo que expressa o
caso "zero segmentos" — sem consumir a barra, ele não teria como.

**Por que "mais restritivo" não era "mais seguro" aqui** (registrado no docstring, porque
contraria o reflexo usual): o risco dominante neste componente é **subcobertura
silenciosa** — um `source_ref` que o autor acredita cobrir um arquivo e não cobre. O
`source_hash` passa a não reagir a mudanças naquele arquivo, e a entrada fica `fresh` para
sempre descrevendo código que mudou: falso negativo de staleness, a classe de defeito de
AUD-004. O risco oposto, sobrecobertura, tem freio próprio e **inalterado**: a checagem de
segredo arquivo a arquivo depois da expansão.

**Três formas de `**`, distinguidas por posição** (tabela em `_append_double_star`):

| Forma | Exemplo | Vira | Casa |
| --- | --- | --- | --- |
| segmento, com `/` depois | `a/**/b`, `**/x` | `(?:.*/)?`, consumindo o `/` | zero ou mais segmentos |
| segmento, no fim | `src/**`, `**` | `.*` | tudo abaixo do prefixo (inalterado) |
| colado a outro caractere | `a**b`, `src/**.py` | `.*` | qualquer coisa, inclusive `/` (inalterado) |

`**` colado a texto continua `.*` e não `[^/]*`: nenhum dialeto define esse caso de forma
consensual, e sob o modelo de risco acima a leitura mais abrangente é a menos perigosa.
`**` adjacentes (`a/**/**/b`) são **colapsados** na tradução — descreveriam a mesma
linguagem e só dariam mais caminhos de backtracking ao motor de regex.

**Testes normativos novos** (em `test_source_ref_expansion.py`), com a nota de que *estes
casos são a especificação* da semântica:

- `a/**/b` casa `a/b` (zero segmentos) — o caso que a implementação anterior não casava;
- `a/**/b` casa `a/x/b` (um segmento) e `a/x/y/b` (múltiplos), e a lista completa fixada;
- `docs/**/*.md` inclui o arquivo direto em `docs/` e o de subpasta, e continua excluindo
  `docs/x.txt` (o sufixo não afrouxou);
- `**/x.md` na raiz e em subpastas (mesmo caso, com prefixo vazio);
- **precisão:** `a/**/b` não casa `a/xb`, `ab` nem `za/b` — "zero ou mais segmentos" não
  virou "qualquer coisa";
- `src/**` no fim continua sendo "tudo abaixo de `src/`", sem pegar `src` nem `srcx.py`;
- `a/**/**/b` compila para o **mesmo padrão** que `a/**/b`;
- `src/**.py` (colado) continua atravessando `/`;
- **a correção não afrouxou a checagem de segredo:** `config/**/*` com `config/.env` no
  nível direto — justamente o nível que a implementação anterior não alcançava — é negado;
  e a mesma árvore sem segredo expande normalmente.

**Dois testes removidos**, porque fixavam a semântica antiga e agora estariam errados:
`test_estrela_dupla_exige_segmento_intermediario` e `test_estrela_dupla_com_sufixo`. O
segundo foi substituído por `test_estrela_dupla_com_sufixo_na_arvore_padrao`, que afirma a
lista maior (agora com `src/app.py` e `src/util.py`).

**Os 6 casos de segurança do Gate 3 anterior foram rodados um a um e continuam passando**
sem alteração: `.env*` direto, `config/*` amplo, glob seguro, sintaxe perigosa dentro do
padrão, unicidade de gramática, literal arquivo/diretório.

### Correção 2 — teste de regressão do defeito de normalização

**O teste já existia** (`test_normalizacao_nunca_introduz_barra_inicial`) e atendia (a),
(b) e (c). Faltavam as duas variações que separam "a correção é da **ordem** das operações"
de "a correção é específica de uma string", e faltava provar (d) em vez de afirmá-la. Foi
reescrito e reforçado; o relatório detalhado está na resposta ao Pedro nesta sessão.

Estado final, em `api/tests/test_source_refs_glob_syntax.py`:

- **Bloco de comentário** com o defeito passo a passo, nas duas ordens, e o motivo de ele
  merecer teste dedicado (o normalizado é o que o expansor casa contra `git ls-tree`).
- **`NORMALIZATION_REGRESSION`** — 4 casos como `(cru, esperado, o que a ordem antiga
  produzia)`: `.//src/app.py`, `.//src/nested/app.py` (subdiretório aninhado),
  `././/src/app.py` (`./.` seguido de `//`) e `.///src/app.py` (barra tripla).
- **`test_normalizacao_nao_introduz_barra_inicial`** — o teste de regressão.
- **`test_contrafactual_a_ordem_antiga_produzia_a_barra_inicial`** — reproduz a ordem
  antiga localmente e afirma, nos mesmos valores, que ela produz a barra inicial **e** que
  ela difere da implementação de produção. Sem isto o teste anterior seria só "o resultado
  é `src/app.py`" — verdadeiro, mas mudo sobre por que existe, e fácil de enfraquecer numa
  refatoração.
- **`test_grafias_equivalentes_convergem_para_o_mesmo_caminho`** — o que sobrou do teste
  original, com o seu próprio motivo (sem convergência, dois `source_refs` que descrevem o
  mesmo arquivo dariam `source_hash` diferentes).

**Verificação empírica de (d), feita e desfeita nesta sessão:** `normalize_source_ref` foi
temporariamente revertida à ordem antiga no arquivo de produção e a suíte de regressão
rodada contra ela → **11 falhas** nos três testes. A ordem correta foi restaurada em
seguida e o `git diff --stat` de `api/app/safety/source_refs.py` voltou a
`84 insertions(+), 20 deletions(-)`, idêntico ao de antes do experimento.

### Verificação final

- Os **9 gates da E4** rodados um a um: todos verdes. Gate 2b (`test_source_refs.py` +
  `test_security_regressions.py`, a suíte da E2) continua passando **e** com `git diff`
  vazio nos dois arquivos.
- Backend: **870 passed / 6 skipped** (era 853; +17 dos testes novos). `ruff check` ·
  `ruff format --check` · `mypy` limpos.
- Frontend: **115 passed** (18 arquivos); `eslint --max-warnings 0` limpo;
  `npm run build` nos dois modos.
- Invariantes estáticos reconferidos: `subprocess` só em `git_runtime/`; tradução de
  metacaractere para regex só em `context_engine/source_ref_expansion.py`; `fnmatch` só em
  `safety/secrets.py` (a denylist de [04] §5, exceção nomeada no teste de arquitetura);
  `safety/source_refs.py` com **zero** chamadas de casamento de padrão (`re.compile`,
  `re.match`, `re.fullmatch`, `re.search`) — só o `re.sub` de normalização.
- `docs/`, `.github/` e `CLAUDE.md`: `git diff` vazio.

### Pendências

- **Primeira rodada de auditoria Codex** sobre a E4, agora com as duas correções.
- Nada commitado, nada pushado. Branch de fase ainda não criada.

---

## 2026-09-05 — Claude Opus 5 (effort high) — E4: correção dos 10 findings da 1ª auditoria Codex (E4-AUD-001..010)

Primeira rodada Codex sobre a E4. **Os 10 findings foram corrigidos — nenhum ficou em
"aceitar e documentar".** Nada commitado, nada pushado; vai para a 2ª rodada.

### Arquivos alterados

- `api/app/git_runtime/__init__.py` — `_workspace_prefix`/`_strip_prefix`/`_add_entry`
  (AUD-002); `working_tree_diff_against` **nova** (AUD-003); `working_tree_status`
  reescrito na base do workspace e com o docstring dizendo o que ele **não** é.
- `api/app/context_engine/source_ref_expansion.py` — casador reescrito de regex para
  **programação dinâmica** (AUD-001, AUD-008); `validate_and_compile` separando etapa (a)
  de (b) (AUD-004); ordem de desfechos invertida (AUD-005); `_tokenize`/`_simplify`
  separados para tornar a simplificação testável.
- `api/app/context_engine/verification.py` — snapshot passa a guardar `divergences` contra
  o `verification_commit` (AUD-003); Parte B calculada **antes** da decisão de estado
  (AUD-006); `updated_at` não é mais tocado (AUD-010).
- `api/app/context_engine/service.py` — envelope validado sempre (AUD-004); colisão de
  chave em `structured` recusada (AUD-007).
- `api/app/context_engine/content_hash.py` — `normalize_structured` detecta colisão
  (AUD-007).
- `api/tests/test_context_audit_e4_round1.py` — **novo**, 46 reproduções.
- `api/tests/test_architecture.py` — detector de gramática por **estrutura** + o
  contrafactual do auditor como teste permanente (AUD-009).
- `api/tests/test_source_ref_expansion.py` — casos de faixa de classe (AUD-008); dois
  testes adaptados à API de tokens.
- `api/tests/test_context_verification.py` — dois testes adaptados à semântica correta da
  Parte B.
- `AGENT_LOG.md` — esta entrada.

**Intocados:** `docs/`, `.github/`, `CLAUDE.md` (`git diff` vazio), e todo o frontend.

### Item por item

**E4-AUD-002 (high) — bases de caminho inconsistentes.** Confirmado empiricamente, e a
causa é mais sutil do que "o git é inconsistente": **é o `-z` que muda a base**. Sem `-z`,
`git status --porcelain=v2` nomeia relativo ao diretório corrente; **com** `-z` — que é o
que o código usa, e precisa usar — ele nomeia a partir da raiz do repositório, enquanto
`ls-tree -r -z` continua relativo ao diretório corrente. Foi exatamente assim que a sonda
que fiz na implementação original passou: rodei sem `-z` e concluí que as bases batiam.
Correção: `_workspace_prefix` lê `rev-parse --show-prefix` uma vez e `_strip_prefix` reduz
tudo à base do workspace; caminhos **fora** do workspace são descartados, não convertidos
em `../algo` — um `source_ref` nunca contém `..` e portanto não poderia cobri-los, e
contá-los inflaria a divergência "do workspace" com arquivos que não são dele. Origem e
destino de rename passam os dois pelo prefixo.

**E4-AUD-003 (o mais importante conceitualmente) — Parte B não usava o
`verification_commit`.** O finding está certo no diagnóstico e na prescrição: `git status`
compara **sempre** contra o `HEAD`, e não existe "status contra commit antigo". A
implementação respondia a pergunta errada. Função nova em `git_runtime/`:
`working_tree_diff_against(local_path, commit)`, que compõe duas leituras — `git diff
--name-status -z <commit>` (árvore atual contra o commit congelado, cobrindo inclusive o
que foi **commitado depois**) e as linhas `?` do `status` (não rastreados, que o `diff` não
enxerga e cuja condição independe de commit). Os cinco rótulos de [03] §3 foram mantidos e
relidos contra uma base congelada; `A` (existe agora, não existia na base) recebe `staged`,
e uma modificação staged de arquivo existente vira `modified`, porque contra um commit é o
conteúdo que difere — a distinção índice-versus-árvore é sobre o `HEAD`, não sobre a base.
**Não** passei pathspec ao `git diff`: manteria caminhos de origem do usuário fora do
`argv`, e a contagem do workspace inteiro é necessária de qualquer jeito.

**E4-AUD-001 (medium hoje, high com agentes escrevendo) — ReDoS.** Duas camadas, como
pedido.
*Mitigação 1 (estrutural):* `_simplify` colapsa quantificadores adjacentes provadamente
redundantes, e a tabela de reduções está no docstring com a justificativa de cada uma.
*Mitigação 2 (garantia de tempo):* o casamento **deixou de ser regex**. `_matches` é uma DP
sobre `(token, posição)` com custo `O(tokens × len(caminho))` — não há caminho ruim a
descobrir porque não há escolha a refazer. Escolhi isso em vez de *timeout* (o `re` do
Python não tem, e `signal` não serve no Windows), de dependência nova (`regex` com
`timeout=`) ou de limite de tamanho (que não daria garantia: com 4 estrelas num segmento de
255 caracteres o pior caso ainda é 255⁴). Há também um teto explícito de tokens, defensivo.
Medição contra a tradução anterior, mesmo alvo: **17 chars → 0,2 s; 25 chars → 9,3 s; 33
chars → 69,8 s**; com a DP, **0,07 ms** nos três — um fator de 10⁶ no caso de 33
caracteres, que é o número que o auditor reportou.

**E4-AUD-004 (medium) — envelope pulado sem commit.** As duas etapas foram separadas de
verdade: `validate_and_compile` é a etapa (a) — envelope e gramática, análise de string,
**sempre roda**; a resolução contra a árvore e a classificação de segredo são a etapa (b) e
dependem de commit. `_compute_baseline` chama (a) incondicionalmente e (b) só se houver
commit. Um `source_ref` bem-formado num workspace sem git continua aceito e nasce
`unknown`, que é a linha "não consegui verificar" de [03] §3.

**E4-AUD-005 (medium) — `UNRESOLVED` tinha precedência sobre o DENY de segredo.** A
classificação de segredo passou a rodar sobre tudo o que foi encontrado **antes** de
qualquer decisão sobre o que não foi. O caso do auditor é desconfortavelmente banal: um
erro de digitação num `source_ref` desligava a proteção do outro.

**E4-AUD-006 (medium) — divergências cobertas sumiam em `sources_changed`.** A Parte B
passou a ser calculada antes de decidir o motivo, e `covered_divergences` é preenchido em
todos os desfechos — inclusive em `unknown` por falta de baseline. A ordem dos motivos
continua a do documento; o que muda é que o motivo escolhido não apaga a evidência do outro.

**E4-AUD-007 (medium) — colisão de chave apagava dado do hash.** `normalize_structured`
deixou de usar compreensão de dicionário e recusa a colisão com erro explícito (→ 422),
recursivamente. Escolher qual chave sobrevive não é decisão que o sistema possa tomar
sozinho, então ele não toma.

**E4-AUD-008 (low) — classe podia casar `/` por faixa.** Resolvido **estruturalmente** pela
reescrita: `_matches` testa `caractere != "/"` **fora** do predicado da classe, então
nenhuma forma de escrever a classe alcança o separador. Contrafactual medido: com a
tradução anterior, `a[.-0]b`, `a[,-0]b` e `a[*-9]b` **todos** casavam `a/b`; agora nenhum
casa.

**E4-AUD-009 (low) — teste de dono único burlável.** O detector virou função testável
(`glob_translation_findings`) com três sinais: import de `fnmatch`/`glob`; as assinaturas
literais; e — o que faltava — a **forma estrutural**, uma cadeia de `.replace(...)` com
metacaractere de glob nos argumentos no mesmo módulo que chama `re.fullmatch`/`match`/
`search`/`compile`. A reprodução do auditor entrou como constante e como teste permanente,
e há um teste de precisão confirmando que `.replace` e `re` inocentes não disparam alarme.

**E4-AUD-010 (low) — `verify` escrevia `updated_at`.** A causa não era `onupdate` do ORM —
era uma linha `entry.updated_at = utcnow()` que eu mesmo escrevi. Removida. O teste de
imutabilidade foi ampliado e há um par que confirma que um `PATCH` de verdade continua
atualizando o campo.

### Prova de que cada reprodução pega o bug original

Rodei um contrafactual que reverte cada correção no arquivo de produção, executa só o teste
da reprodução, e restaura. **Sete de sete falharam com a correção revertida:** AUD-002,
-003, -004, -005, -006, -007 e -010. AUD-001 e AUD-008 não são reversíveis por *patch*
pontual (foram reescritas estruturais) e foram demonstrados medindo a implementação
anterior lado a lado — números acima. AUD-009 já tem o contrafactual como teste permanente.

**Uma iteração que vale registrar:** a primeira versão do teste de ReDoS usava um alvo sem
nenhum `a` (`"b"*120`). Com esse alvo o primeiro `[^/]*a` falha e o motor poda a busca
inteira — o caso é rápido **mesmo com backtracking**, e o teste teria passado contra o
código com o bug. O pior caso exige alvo cheio de `a` (muitas repartições por
quantificador) e a falha só no literal final (`Z`). O teste foi refeito com
`"a"*34` + `"*a"*k + "Z"`, e ganhou um companheiro
(`test_aud001_o_alvo_do_teste_e_de_fato_o_pior_caso`) que afirma essa propriedade do
próprio alvo, para que ninguém a enfraqueça sem perceber.

**Uma segunda iteração:** a camada de simplificação (mitigação 1 do AUD-001) introduziu um
bug ao tratar `SEGSTAR STAR` como redundante — `docs/**/*.md` deixou de casar
`docs/readme.md`, exatamente a subcobertura que a semântica de `**` existe para evitar.
Corrigido, e coberto por
`test_aud001_simplificacao_nunca_muda_o_que_o_padrao_casa`, que compara o token stream cru
com o simplificado sobre um corpus. Foi para isso que `_tokenize` e `_simplify` ficaram
separados.

### Verificação final

- Backend: **926 passed / 6 skipped** (era 870; +56). `ruff check` · `ruff format --check` ·
  `mypy` limpos.
- Frontend: **115 passed** (18 arquivos); `eslint --max-warnings 0` limpo; `npm run build`
  nos dois modos.
- Invariantes estáticos: `subprocess` só em `git_runtime/`; `fnmatch` só em
  `safety/secrets.py` (a denylist de [04] §5, exceção nomeada no teste de arquitetura);
  `context_engine/` sem importar camada superior. O expansor **não usa mais `re`** para
  casar — o módulo não importa `re`.
- `docs/`, `.github/` e `CLAUDE.md`: `git diff` vazio.

### Pendências

- **2ª rodada do Codex.**
- Nada commitado, nada pushado. Branch de fase ainda não criada.

---

## 2026-09-05 — Claude Opus 5 (effort high) — E4: 2ª auditoria Codex (E4-AUD2-001..005) + mudança de estratégia sobre configuração do git

Segunda rodada Codex sobre a E4. Cinco findings corrigidos, **e uma mudança de estratégia**
que é o item mais importante desta entrada. Nada commitado, nada pushado.

### A decisão estratégica: fixar configuração por `-c`, não corrigir caso a caso

Quatro dos findings das duas rodadas — E4-AUD-002, E4-AUD-003, E4-AUD2-001 e E4-AUD2-003 —
foram **a mesma classe de defeito**: a base dos caminhos que o git emite depende de
configuração e de opções que o repositório do usuário controla, e cada variação produzia um
falso `fresh` diferente. Cada correção fechava o caso encontrado; a rodada seguinte trazia
outro. O padrão estava claro e a estratégia de correção é que estava errada.

A regra passou a ser a que a **E3 já aplicava** para as travas de somente-leitura
(E3-AUD-003): **nada de default — a configuração relevante é fixada por `-c`,
explicitamente, em toda invocação.** `_READONLY_GIT_OPTIONS` deixou de ter uma opção e
passou a ter quatro:

| Opção | Papel | Medido |
| --- | --- | --- |
| `core.fsmonitor=false` | somente-leitura (E3) | — |
| `diff.relative=false` | base de caminho | **load-bearing**: com `true`, `M src/one.txt`; com `false`, `M ws/src/one.txt` |
| `core.quotepath=false` | base de caminho | inócuo hoje (todo comando usa `-z`), fixado contra uma leitura futura sem `-z` |
| `status.relativePaths=false` | base de caminho | **medido como no-op**: `--porcelain=v2 -z` ignora a opção |

**Duas decisões dentro dessa, que merecem escrutínio na próxima rodada:**

1. **`status.relativePaths=false`, não `true` como o prompt pedia.** Medi as três formas
   (`true`, `false`, ausente) e o `--porcelain=v2 -z` devolve caminho relativo à **raiz** em
   todas — porcelain força a opção, como a documentação do git diz. Fixar `true` seria um
   no-op que **documenta uma intenção que o código não implementa**: o próximo leitor
   poderia remover o `_strip_prefix` confiando no rótulo. `false` descreve a base que o
   código de fato consome, e é a mesma dos outros dois comandos.

2. **A base uniforme veio junto, e é o que realmente fecha a classe.** Fixar configuração
   sozinho não bastaria, porque a assimetria não era de configuração: o `ls-tree` nomeava
   relativo ao **diretório corrente** e o `status`/`diff` relativo à **raiz**, por desenho.
   `list_tree` passou a usar `--full-tree` e a passar pelo mesmo `_strip_prefix`. Agora são
   **uma base (a raiz do repositório) e uma transformação (`_strip_prefix`)**, nos três
   comandos. O custo é listar a árvore inteira num monorepo e filtrar; a alternativa custava
   um falso `fresh` por rodada de auditoria.

O `diff` ainda leva `--no-relative` **explícito** além do `-c diff.relative=false`. As duas
travas são redundantes de propósito — ver a nota sobre o contrafactual abaixo.

### Item por item

**E4-AUD2-001 (high) — `diff.relative` reintroduzia falso fresh.** Coberto pela estratégia
acima (`-c diff.relative=false` + `--no-relative`), mais os testes específicos pedidos:
workspace em subdiretório com `diff.relative=true` configurado no repositório, arquivo
coberto modificado sem commitar → `stale(working_tree)`.

**E4-AUD2-002 (high) — staged + reversão anulava a divergência.** `git diff <commit>` compara
só a árvore de trabalho. Dar `stage` em B e reescrever A no arquivo deixa a árvore idêntica
ao commit e o `diff` **vazio**, embora o índice ainda contenha B e um `git commit` fosse
gravar B. Acrescentado `git diff --cached <commit>` como segunda fonte, combinada por
**união** — a entrada diverge se **qualquer** das duas divergir, nunca pelo resultado
líquido. O parse virou `_collect_diff_against(..., cached=bool)`, uma função só chamada duas
vezes.

**E4-AUD2-003 (medium) — `.strip()` corrompia prefixo com espaço.** Trocado por
`rstrip("\n").rstrip("\r")`. Medido: para um workspace em `" lead ws"`, o `.strip()`
devolvia `"lead ws/"`, e com o prefixo corrompido `_strip_prefix` deixava de reconhecer
**qualquer** caminho como sendo de dentro do workspace — todas as divergências eram
descartadas como "de fora" e a entrada ficava eternamente `fresh`.

> **Nota sobre o teste:** o espaço **no fim** do nome não é testável e não precisa ser. O
> Windows não consegue criar um diretório assim (descarta o espaço em silêncio) **e** o
> nosso próprio envelope já recusa componente terminado em espaço
> (`path.trailing_dot_or_space`). O espaço no **início** é legal em todo lugar, não é
> recusado por nada, e é o caso do auditor — é ele que o teste usa.

**E4-AUD2-004 (medium) — `UNRESOLVED` ainda apagava a cobertura conhecida.** A Parte B passou
a ser calculada **antes** da expansão, e a cobertura acompanha todos os desfechos, inclusive
`unknown`. "Não sei se está tudo coberto" e "não vou dizer o que sei" são respostas
diferentes. O matcher é montado com **todos** os `source_refs`, inclusive os que não
resolveram contra a árvore: um ref que casa zero arquivo no commit ainda pode cobrir um
caminho não rastreado de hoje.

**E4-AUD2-005 (low) — detector burlável via `str.translate`.** A detecção deixou de ser uma
lista de padrões de código conhecidos e passou a ser uma lista de **mecanismos**: qualquer
transformação de string (`.replace`, `.translate`, `.maketrans`, `.join`, `.format`,
`re.sub`) com metacaractere de glob nos literais, **ou** um dicionário literal com
metacaractere nas chaves, no mesmo módulo em que se chama uma função de casamento de `re`.
Os **três** contrafactuais (o `.replace` da rodada 1, o `str.translate` desta, e uma
variação com tabela literal + `join`) ficaram como tabela parametrizada.

E — o item que o prompt pedia explicitamente que fosse assumido se não desse para fechar —
**está documentado no código, no docstring de `glob_translation_findings`**:

> **Esta é uma barreira heurística contra descuido, não uma prova formal de unicidade de
> gramática.** Um segundo casador pode ser escrito sem tocar em `re` (comparando caractere a
> caractere, como o próprio dono da gramática faz), e nenhuma análise estática barata
> distingue isso de um parser qualquer. O detector existe para que uma segunda implementação
> **acidental** pare no CI. Contra uma segunda implementação **deliberada**, a defesa real
> continua sendo a revisão de código e o `__init__.py` do `context_engine`, que expõe um
> ponto de entrada só.

Assumir isso é melhor do que fingir cobertura: um teste que se apresenta como prova e não é
vira a mesma falsa segurança que a E2 já pagou uma vez.

### Contrafactual — cada reprodução expõe o bug?

Reverti cada correção no arquivo de produção, rodei só a reprodução, restaurei:

| Correção revertida | Resultado |
| --- | --- |
| `--cached` (AUD2-002) | **falha** |
| `.strip()` (AUD2-003) | **falha** |
| cobertura em `unknown` (AUD2-004) | **falha** |
| `--full-tree` (estratégia) | **falha** |
| `-c diff.relative=false` **sozinho** | passa |
| `--no-relative` **sozinho** | passa |
| **as duas travas de AUD2-001 juntas** | **falha** (3 testes) |

As duas linhas que "passam" **não** são teste cego: as travas de AUD2-001 são redundantes
por desenho, e cada uma sozinha basta. Removendo as duas, os três testes de AUD2-001 falham
— inclusive o da estratégia com `diff.relative=true`. Registro isso porque é o tipo de
resultado que parece um furo e não é, e a próxima rodada vai reencontrá-lo.

### Verificação final

- **Rodadas 1 e 2 juntas**: 64 testes, todos passando — nenhuma correção desfez outra.
- Backend: **947 passed / 6 skipped** (era 926; +21). `ruff` · `ruff format --check` ·
  `mypy` limpos.
- Frontend: **115 passed**; `eslint --max-warnings 0` limpo; `npm run build` nos dois modos.
- Todos os gates da E4 rodados um a um: verdes.
- Estáticos: `subprocess` só em `git_runtime/`; `fnmatch` só em `safety/secrets.py`.
- `docs/`, `.github/` e `CLAUDE.md`: `git diff` vazio.

### Um teste da E3 foi endurecido (e por quê)

`test_preflight_passa_travas_de_somente_leitura` afirmava `argv[1:3] == ["-c",
"core.fsmonitor=false"]` — olhava só a **primeira** opção. Com quatro opções no bloco, isso
deixaria três sem cobertura: acrescentar ou remover qualquer uma passaria batido, inclusive
as que fixam a base de caminho, cuja ausência é um falso `fresh`. Passou a afirmar
**igualdade** do prefixo inteiro do `argv` contra `_READONLY_GIT_OPTIONS`, no mesmo espírito
de igualdade-de-conjunto que E3-AUD2-003 estabeleceu para o env. O mesmo foi feito no teste
equivalente da E4.

### Pendências

- **3ª rodada do Codex.** O foco esperado é confirmar que fixar configuração por `-c`
  fecha a **classe** e não só os casos vistos — o teste
  `test_estrategia_configuracao_hostil_no_repo_nao_muda_nada` existe exatamente para essa
  pergunta: ele configura cada uma das quatro opções com o valor oposto, uma por vez, e
  afirma que o veredito não muda.
- Nada commitado, nada pushado. Branch de fase ainda não criada.

---

## 2026-09-08 — Claude Opus 5 (effort medium) — E4: 3ª auditoria Codex (E4-AUD3-001) — identidade de caminho, fail-closed

Um finding só, e ele é da mesma família das três rodadas anteriores: a base/identidade dos
caminhos que vêm do Git. A diferença é que desta vez a correção **não** é mais um caso
tratado — é remover a tradução que criava o problema e assumir que existe caminho que o
registro não sabe nomear. Nada commitado, nada pushado.

### O finding

`_strip_prefix` fazia `path.replace("\\", "/")` antes de comparar com o prefixo do
workspace. Uma barra invertida **literal** no nome de um arquivo — legal no Git, no Linux e
no macOS — era lida como separador, e o caminho **trocava de identidade**:

| Workspace | Caminho do Git | O que voltava | O que é de verdade |
| --- | --- | --- | --- |
| `ws/` | `ws\outside.py` | `outside.py`, de dentro | arquivo da **raiz** do repo |
| raiz | `ws\outside.py` | `ws/outside.py`, filho de `ws/` | arquivo da **raiz** do repo |

Falso `stale` de um lado (arquivo alheio entra no `source_hash` de um workspace que não o
contém), falso `fresh` do outro (a Parte A e a Parte B podem divergir sobre quem é quem).

### A correção, e por que ela é *fail closed* e não uma normalização melhor

Duas constatações, as duas com teste:

1. **O Git não é ambíguo.** Ele emite `/` como separador em toda plataforma e em toda saída
   de caminho — `ls-tree`, `status`, `diff`, `rev-parse --show-prefix`. Uma `\` na saída do
   Git é sempre um caractere do **nome**. A tradução não resolvia ambiguidade nenhuma; ela
   **criava** a ambiguidade. Era um reflexo de Windows aplicado no lugar errado, e o lugar
   certo (`normalize_source_ref`, que trata entrada de **usuário**) já a tinha.
2. **O envelope é que não tem a palavra.** `normalize_source_ref` faz
   `value.replace("\\", "/")` — e faz certo. A consequência é que nenhum `source_ref`,
   presente ou futuro, produz a barra invertida literal necessária para casar contra esse
   caminho. `ws\outside.py` e `ws/outside.py` colapsam no mesmo texto.

Somadas: o arquivo existe, o Git o vê, e o registro não consegue nomeá-lo. Inventar uma
gramática de escape só para ele seria uma **segunda gramática de caminho** — exatamente o
buraco que E2-AUD-003 fechou. Então:

- a tradução saiu de `_strip_prefix` **e** de `_workspace_prefix` (os dois lados vêm do
  mesmo Git, na mesma base; normalizar um só os desalinha, normalizar os dois recria a
  troca de identidade);
- um caminho **de dentro** do workspace que contenha `\` faz a leitura inteira devolver
  `UnrepresentablePaths(paths=...)` — um resultado neutro **tipado**, novo em
  `git_runtime`, que o Context Engine traduz no `unknown` que [03] §3 já define;
- um caminho **de fora** continua sendo descartado em silêncio, como sempre: nenhum
  `source_ref` poderia alcançá-lo de qualquer jeito.

### As três escolhas de desenho que merecem escrutínio

**1. Excluir e sinalizar, não excluir em silêncio.** O prompt deixava a escolha entre
"lista separada de ignorados" e "promover a verificação a `unknown` com motivo". Fiz as
duas coisas, porque separadas nenhuma resolve: a lista sozinha não muda o veredito (e é o
veredito que a UI e a E6 consomem), e o `unknown` sozinho é indistinguível de "o git não
respondeu". `UnrepresentablePaths` exclui do resultado normal **e** carrega os caminhos até
`WorkspaceTreeSnapshot` → `FreshnessOutcome` → `working_tree_divergence.unrepresentable_paths`
na resposta da API.

**2. O workspace inteiro, e não a entrada afetada.** Não dá para saber qual entrada seria
afetada: decidir isso exigiria casar os `source_refs` contra o caminho, e é justamente o
casamento que não é definido para ele. "Não sei quem depende disso" só tem uma resposta.

**3. Mas o workspace, não o repositório.** Um arquivo esquisito na raiz de um monorepo
**não** torna inverificável um workspace em `ws/` — ali ele é de fora, e de fora já era
descartado. `test_aud3_001_o_workspace_vizinho_continua_verificavel` trava essa
contrapartida, porque um fail-closed largo demais seria uma regressão de utilidade
disfarçada de rigor.

### O tipo em união é parte da correção

`list_tree`, `working_tree_status` e `working_tree_diff_against` passaram a devolver
`... | UnrepresentablePaths | None`. O mypy então **exigiu** que todo chamador tratasse o
terceiro desfecho — e apontou 28 pontos, todos em teste. Era exatamente um chamador não
tratando um desfecho que produzia a troca de identidade; deixar o tipo dizer isso vale mais
do que a conveniência de devolver `None` cru. Nos testes que não exercitam o caso, o helper
`tests/context_helpers.py::readable()` **assere** que o desfecho não ocorreu (em vez de
fazer `cast`, que calaria o mypy e deixaria o teste passar por acidente).

### Reprodução e contrafactual

Reprodução exata do auditor: `git mktree -z` + `git commit-tree` com um blob chamado
literalmente `ws\outside.py` na raiz, irmão de um `ws/inside.py` real. Nenhum sistema de
arquivos Windows cria esse nome, mas o banco de objetos do Git aceita qualquer byte que não
seja NUL nem `/` — e é a saída do Git que este código lê, então o teste vale nos três
sistemas. `tests/test_context_audit_e4_round3.py`, 16 testes.

| Correção revertida | Resultado |
| --- | --- |
| tradução de `\` em `_strip_prefix` | **falha** |
| fail-closed da Parte A (`list_tree`) | **falha** |
| fail-closed da Parte B (`_add_entry`) | **falha** |
| tradução de `\` em `_workspace_prefix` | **falha** — só na trava estrutural |

A última linha precisa de nota honesta. Restaurar a tradução no `_workspace_prefix` é
**inerte no Windows**: o prefixo vem do `--show-prefix` e um diretório com `\` no nome não
pode existir aqui, então nenhuma reprodução comportamental a pega. Quem a pega é
`test_aud3_001_nenhuma_leitura_do_git_traduz_barra_invertida`, que varre a AST do módulo
atrás de **qualquer** transformação de string com `\` num literal — mecanismo, não linha
específica, no espírito de E4-AUD2-005. A metade comportamental dessa trava, que vale em
qualquer plataforma, está em `test_aud3_001_o_prefixo_do_workspace_tambem_nao_e_traduzido`,
no nível do `_strip_prefix`. No Linux o caso é real: um workspace em `a\b/` com o prefixo
traduzido para `a/b/` deixaria de reconhecer **qualquer** caminho como sendo de dentro —
todas as divergências descartadas, entrada eternamente `fresh`.

### Verificação final

- **Rodadas 1, 2 e 3 juntas**: 80 testes, todos passando — nenhuma correção desfez outra.
- Backend: **963 passed / 6 skipped** (era 947; +16). `ruff` · `ruff format --check` ·
  `mypy` limpos (65 arquivos).
- Frontend: **115 passed** (18 arquivos); `eslint --max-warnings 0` limpo. Intocado nesta
  rodada — o campo novo da resposta é aditivo e a UI já mostra `unknown`.
- Estáticos: `subprocess` só em `git_runtime/`; `fnmatch` só em `safety/secrets.py`.
- `docs/`, `.github/` e `CLAUDE.md`: `git diff` vazio.

### Pendências

- **4ª rodada do Codex.** Não presumo fechamento. O ponto mais exposto continua sendo a
  fronteira de representabilidade: `_UNREPRESENTABLE_CHARS` hoje tem **um** caractere, e a
  justificativa dele é o `replace` de `normalize_source_ref`. Se um dia o envelope passar a
  normalizar mais alguma coisa, essa constante tem de acompanhar — e nada no CI liga as
  duas hoje.
- Duas decisões ainda pendentes de Pedro, das rodadas anteriores: o rename
  `ProjectPlanningImport` → `PlanningImport`, e a confirmação da semântica de `**` colado a
  texto.
- Nada commitado, nada pushado. Branch de fase ainda não criada.

---

## 2026-09-08 — Claude Sonnet 5 (effort low) — E4: fecha a pendência auto-registrada (E4-AUD3-002 — acoplamento entre normalização e representabilidade)

Fecha a pendência que a entrada anterior deixou explícita: `_UNREPRESENTABLE_CHARS` em
`git_runtime` era uma **cópia** do caractere que `normalize_source_ref` traduz, sem ligação
garantida entre os dois. Nada commitado.

### Opção escolhida: (a) — derivar de uma única constante

`normalize_source_ref` fazia `value.replace("\\", "/")` — um literal solto dentro do corpo
da função, não uma estrutura de dados. Extraí o conjunto para
`safety.source_refs.PATH_SEPARATOR_ALIASES: frozenset[str]`, troquei o `.replace()` por
`.translate()` com uma tabela derivada dele (`str.maketrans`), e `git_runtime` passou a
importar a mesma constante em vez de manter `frozenset("\\")` por conta própria:

```python
# app/safety/source_refs.py
PATH_SEPARATOR_ALIASES: frozenset[str] = frozenset("\\")
_SEPARATOR_TRANSLATION = str.maketrans({char: "/" for char in PATH_SEPARATOR_ALIASES})
# normalize_source_ref: value.translate(_SEPARATOR_TRANSLATION).strip()

# app/git_runtime/__init__.py
from app.safety.source_refs import PATH_SEPARATOR_ALIASES
_UNREPRESENTABLE_CHARS = PATH_SEPARATOR_ALIASES
```

(a) foi direto porque a fonte já era, na prática, um `frozenset` de um elemento escondido
num `.replace()` — trazê-lo para o nível de módulo e importar não exigiu tocar em nenhuma
outra regra de `normalize_source_ref`. Não precisei de (b): um teste de igualdade entre
dois conjuntos mantidos separadamente teria sido um paliativo — continuaria havendo dois
lugares para lembrar de atualizar, só que agora com um terceiro (o teste) para não deixar o
CI mentir sobre isso. Com uma fonte única, não há "os dois" para divergir.

`git_runtime` importar de `safety` é permitido pelo invariante de módulo já congelado em
[01] ("`git_runtime/` pode importar `safety`, `config` e stdlib") — nenhuma regra nova, só
uso de uma que já existia e que a versão anterior desta correção não tinha aproveitado.

### Verificação

- `ruff check` · `ruff format --check` · `mypy`: limpos (65 arquivos).
- Suíte completa: **963 passed / 6 skipped** — mesma contagem de antes, nenhuma regressão.
- `test_architecture.py` (que trava os invariantes de import por módulo) e as três rodadas
  de reprodução de auditoria (`test_context_audit_e4_round1/2/3.py`) passam intactas.
- Frontend não tocado nesta entrada — mudança confinada a `api/`.
- `docs/`, `.github/` e `CLAUDE.md`: sem diff (não verificados de novo nesta entrada além do
  `git status`, já que a mudança não os alcança).

### Pendências

- Nenhuma nova. As duas decisões pendentes de Pedro seguem as mesmas das entradas
  anteriores: o rename `ProjectPlanningImport` → `PlanningImport`, e a confirmação da
  semântica de `**` colado a texto.
- Nada commitado, nada pushado. Branch de fase ainda não criada.

---

## 2026-09-08 — Claude Opus 5 (effort high) — E4: 4ª auditoria Codex (E4-AUD4-001, E4-AUD4-002) — `_run_git` para de usar `text=True`

Dois findings, uma causa: `_run_git` chamava `subprocess.run(..., text=True, encoding="utf-8",
errors="strict")`. Corrigido na raiz — `_run_git` não liga mais o modo texto do `subprocess`
em nenhuma forma — e não em mais uma variação pontual. Nada commitado.

### Os dois findings

**E4-AUD4-001 — tradução automática de quebra de linha.** O modo texto do Python aplica
*universal newlines* na decodificação: CR e CRLF viram LF, mesmo dentro de um nome de
arquivo que os contenha como bytes **literais**. `-z` protege contra *C-quoting*, mas é um
mecanismo diferente dentro do `io.TextIOWrapper` — não protege contra isso. Dois blobs reais,
um com CR literal no nome e outro com LF literal, viravam o mesmo texto depois da tradução:
duas identidades de arquivo colidindo em uma.

**E4-AUD4-002 — decodificação que escapa do `except` de `_run_git`.** Com `capture_output=True`
e modo texto, a decodificação roda dentro da máquina interna de `Popen.communicate()` — no
Windows, que sempre lê `stdout`/`stderr` em threads separadas, ela roda **dentro da thread
leitora**. Uma falha ali não é o `UnicodeDecodeError` limpo que o
`except (OSError, ValueError, subprocess.SubprocessError)` deste módulo está preparado para
pegar — é um erro de estado interno da thread que `communicate()` não trata como decodificação
malsucedida. Byte inválido em qualquer lugar da árvore virava exceção não tratada e 500.

### A correção: parar na causa, não nos dois casos

`_run_git` não passa mais `text=True`, nem `encoding=`/`errors=` — que também ligam o modo
texto implicitamente, mesmo sem `text=True` explícito. `stdout`/`stderr` saem como **bytes
crus**. A conversão para texto virou um passo explícito deste módulo — `_decode_text(data) ->
str | None`, um `bytes.decode("utf-8", errors="strict")` dentro de um `try` comum, chamado
onde antes o `subprocess` decidia por conta própria.

Isso exigiu reescrever a separação de registros e campos nas três leituras da E4 (`list_tree`,
`working_tree_status`, `_collect_diff_against`) para acontecer **em bytes, antes de qualquer
decodificação**:

* separador de registro e separador de campo são bytes ASCII fixos que o git usa como
  delimitador — localizá-los não exige que o conteúdo entre eles seja UTF-8 válido, então a
  separação nunca falha por causa de um nome de arquivo corrompido.
* Só o **último** campo de cada registro é caminho de arquivo; os anteriores (`XY`, modo,
  hash) são sempre ASCII — o próprio git os gera, nunca conteúdo de usuário — e uma falha em
  decodificá-los é violação de **formato** (mesmo tratamento de sempre: o registro inteiro é
  rejeitado), não nome de arquivo ilegível.
* A redução ao prefixo do workspace (`_strip_prefix`) passou a ter uma versão em bytes,
  `_strip_prefix_bytes`, que é a implementação real agora — "isto começa com o prefixo do
  workspace?" é respondida **antes** de qualquer tentativa de decodificar, para que um caminho
  corrompido ainda possa ser classificado como de dentro ou de fora do workspace.
  `_strip_prefix` (texto) virou um wrapper fino sobre ela, para o código e os testes que já
  trabalham com string continuarem funcionando sem duplicar a regra.
* Um caminho que não decodifica entra em ``unrepresentable`` (o mesmo mecanismo de
  E4-AUD3-001) e o laço de parsing **continua** para os próximos registros — o registro
  problemático é isolado, nunca interrompe a leitura dos demais nem devolve `None` cru.
  `_describe_undecodable` (só para diagnóstico, `errors="backslashreplace"`) dá ao motivo
  registrado uma forma legível, e nunca é usado para casar contra `source_ref` nem para
  identidade de arquivo.

### Task 3 do prompt: existe outro lugar lendo git com `text=True`?

Não. `subprocess` é confinado a `git_runtime/` desde a E3 ([01], reforçado por
`test_architecture.py::test_git_runtime_e_somente_leitura` e pela trava nova
`test_aud4_subprocess_run_e_a_unica_chamada_do_modulo`), e dentro do módulo há exatamente
**uma** chamada a `subprocess.run` — em `_run_git`, já corrigida. Não havia um segundo ponto de
leitura da mesma classe de bug para corrigir.

### Trava estrutural contra reintrodução

`test_aud4_subprocess_run_nunca_liga_o_modo_texto` varre a AST do módulo à procura de
`text=True`, `universal_newlines=True`, `encoding=` ou `errors=` em qualquer chamada a
`subprocess.run` — as quatro formas ligam o modo texto, não só a que existia. Mecanismo, não
linha específica, no espírito das travas já usadas em E4-AUD2-005 e E4-AUD3-001.

### Testes obrigatórios — `tests/test_context_audit_e4_round4.py`, 10 testes

* **AUD4-001**: dois commits reais (`git mktree`/`commit-tree`) com dois blobs de mesmo
  conteúdo, um nomeado com CR literal e outro com LF literal — `list_tree` devolve
  identidades diferentes e `compute_source_hash` produz hashes diferentes. Variação CRLF
  como terceira identidade distinta, isolada e também convivendo com as outras duas **no
  mesmo commit** (prova que a trava de duplicata não dispara por colisão de tradução).
* **AUD4-002**: commit real com byte inválido (`0xFF`) no nome — `list_tree` não lança,
  devolve `UnrepresentablePaths` com o motivo preenchido. Dois registros inválidos no mesmo
  commit aparecem os **dois** (prova que o laço não para no primeiro). Um repositório
  vizinho sem o problema continua funcionando normalmente.
* **HTTP real**: `POST /api/workspaces/{id}/context/verify` contra os dois cenários (byte
  inválido e CR literal) devolve **200**, nunca 500; as entradas saem `unknown`, e
  `working_tree_divergence.unrepresentable_paths` carrega o motivo.
* Duas travas estruturais (texto acima).

### Contrafactual — prova de que a reprodução pegaria o bug original

Reverti só o `subprocess.run` de `_run_git` para `text=True, encoding="utf-8",
errors="strict"` (a assinatura exata de antes) e rodei `test_context_audit_e4_round4.py`
inteiro contra o resto do módulo já corrigido:

**9 dos 10 testes falharam** (a exceção foi a trava de "só uma chamada a `subprocess.run`",
que não tem relação com `text=True`). A falha observada foi `AttributeError: 'str' object has
no attribute 'decode'` — porque `_decode_text` (código novo) espera bytes e o `subprocess`
revertido volta a entregar `str`. Isso demonstra exatamente o ponto da correção: **qualquer**
reversão para modo texto quebra visivelmente, de imediato, em vez de deixar passar um bug
silencioso — o desenho novo (bytes em toda a cadeia) não tem um caminho onde `text=True` volte
a funcionar sem gritar.

### Verificação final

- **Rodadas 1, 2, 3 e 4 juntas**: 90 testes, todos passando — nenhuma correção desfez outra.
- Backend: **973 passed / 6 skipped** (era 963; +10). `ruff` · `ruff format --check` · `mypy`
  limpos (66 arquivos).
- Frontend: `eslint --max-warnings 0` limpo. Não tocado nesta rodada.
- Cinco mocks de teste que construíam `subprocess.CompletedProcess` com `stdout` em `str`
  (`test_git_runtime.py`, `test_git_runtime_context_reads.py`) foram atualizados para `bytes`
  — refletem o `_run_git` real, não são parte do finding.
- `docs/`, `.github/` e `CLAUDE.md`: `git diff` vazio.

### Pendências

- **5ª rodada do Codex.** Não presumo fechamento — mas a auditoria já percorreu: base de
  caminho (rodadas 1–2), identidade de caminho não-ASCII/separador (rodada 3), e agora
  identidade de caminho por decodificação/newline (rodada 4). As três primeiras camadas
  fecharam classes inteiras de problema, não casos; espero que a quinta confirme isso ou
  encontre a próxima camada.
- Duas decisões ainda pendentes de Pedro, das rodadas anteriores: o rename
  `ProjectPlanningImport` → `PlanningImport`, e a confirmação da semântica de `**` colado a
  texto.
- Nada commitado, nada pushado. Branch de fase ainda não criada.

---

## 2026-09-08 — Claude Opus 5 (effort high) — E4: 5ª auditoria Codex (E4-AUD5-001) — a leitura parcial deixa de apagar o que já sabe

Um finding, e é o mais grave da fase: **um arquivo ilegível fazia sumir a recusa de um
segredo legível**. Nada commitado.

### O finding

`list_tree()` devolvia **ou** os arquivos legíveis, **ou** o aviso de que havia caminho não
representável — nunca os dois. Um tipo-**soma**, decidido nas rodadas 3 e 4 e que parecia
conservador. Bastava um nome ilegível em qualquer canto da árvore para os registros válidos
já coletados serem descartados, e a expansão de um glob que cobre arquivos **válidos**
devolver `UNRESOLVED` em vez de classificá-los. E `UNRESOLVED` não classifica segredo:

> um `.env` conhecido, legível e resolvível deixava de ser recusado (`DENIED`) só porque
> **outro** arquivo, sem relação nenhuma, tinha nome ilegível no mesmo commit.

Ignorância sobre um arquivo virava ignorância sobre todos — e ignorância, aqui, é o mesmo
que permissão. O "fail closed" das rodadas 3 e 4 estava, neste ponto, *fail open*.

### A correção: tipo-produto, e incompletude por `source_ref`

**Tipo-produto.** `TreeListing` (Parte A) e `WorkingTreeListing` (Parte B) carregam
`files`/`divergences` **e** `unrepresentable`, sempre. `UnrepresentablePaths` (que
substituía a lista) deixou de existir; no lugar veio `UnrepresentablePath`, um item que
guarda os **bytes crus** do caminho além do texto de diagnóstico.

Os bytes são o que torna possível a segunda metade da correção. **A incompletude passou a
ser avaliada por `source_ref`**, e a pergunta "este ref poderia ter alcançado aquele
caminho?" é decidível em bytes, sem interpretar gramática nenhuma sobre um alvo que não é
texto (`source_ref_expansion._may_cover`):

* ref **literal** — a mesma regra de `_literal_covers`, byte a byte (`raw == ref` ou `raw`
  começa com `ref + "/"`);
* ref com **glob** — todo caminho que ele casa começa pelo seu prefixo literal (os
  caracteres antes do primeiro metacaractere, que viram tokens `LITERAL` ancorados no
  início). Se os bytes do caminho ilegível não começam por aí, o ref **não pode** casá-lo.

É uma sobre-aproximação **segura**: nunca diz "não pode" quando poderia. `*` e `**` têm
prefixo vazio, então continuam incertos diante de qualquer caminho ilegível.

### A precedência, preservada inteira

**`DENIED` > incompleto (`UNRESOLVED`) > `RESOLVED`.** As duas metades vêm de auditorias
diferentes e a correção não podia deixar uma enfraquecer a outra:

* `DENIED` > `UNRESOLVED` é de **E4-AUD-005** — um ref com erro de digitação não pode
  desligar a checagem de segredo dos outros;
* `DENIED` > **incompleto** é desta rodada — um arquivo ilegível não pode desligar a
  checagem de segredo dos arquivos legíveis.

A forma geral das duas é a mesma, e virou a frase que organiza o módulo: **o que já se sabe
nunca é apagado pelo que não se sabe.** A classificação de segredo roda sobre tudo o que foi
encontrado, antes de qualquer decisão sobre o que não foi encontrado ou não pôde ser lido.

Na regra de estado (`evaluate_freshness`) a mesma ordem virou posição no código: a checagem
de incompletude entra **depois** dos dois `stale` (que são afirmações definitivas, e
continuam valendo com a leitura parcial) e **antes** do `fresh` (a única afirmação que a
incompletude realmente impede). A Parte B ganhou o mesmo tratamento: uma divergência
conhecida num arquivo legível e coberto continua produzindo `stale`, mesmo que outro
registro da mesma leitura seja ilegível.

### Duas asserções da rodada 3 mudaram de veredito — de propósito

Isto merece escrutínio na 6ª rodada, e está marcado no código e nos testes.

`expand_against_commit(root, commit, ["ws/**"])` com o blob `ws\outside.py` na raiz era
`UNRESOLVED` e passou a ser **`RESOLVED`**. O motivo: `ws/**` só casa caminhos cujos bytes
começam com `ws/`, e `ws\outside.py` começa com `ws\`. Ele **não pode** alcançá-lo, e dizer
o contrário era inventar uma incerteza inexistente. O mesmo vale para
`test_aud3_001_verificacao_vira_unknown_com_o_motivo`, que agora usa `**` (um ref que
*realmente* alcança) para testar o `unknown`.

O finding da rodada 3 continua inteiro e testado: o arquivo da raiz **não** é reatribuído a
`ws/`, **não** ganha a identidade inventada `ws/outside.py`, e aparece no diagnóstico. O que
mudou foi a consequência, que deixou de ser coletiva — que é exatamente o que esta rodada
pediu. Os dois testes reescritos passaram a afirmar **os dois lados**: o ref que não alcança
resolve, e o ref que alcança continua incerto. Ficaram mais fortes do que eram.

### Reprodução — `tests/test_context_audit_e4_round5.py`, 7 testes

Os quatro obrigatórios do prompt, mais três:

1. árvore com `bad-\xFF.py`, `good.py`, `zgood.py` → `list_tree` devolve os **dois** legíveis
   e o diagnóstico do terceiro (`zgood.py` está ali por vir **depois** do registro ruim na
   ordem de bytes: prova que ele não interrompe nem o que veio antes nem o que vem depois);
2. expandir `["good.py"]` na mesma árvore → `RESOLVED`, com `source_hash`;
3. `.env` + `good.py`, `source_refs=["*"]` → **422**;
4. **o teste decisivo** — a mesma árvore mais `bad-\xFF.py`, mesma criação → **ainda 422**;
5. a contrapartida do nº 2: `["*"]` (prefixo literal vazio) continua `UNRESOLVED`, e vai
   para `incomplete_refs`, não para `unresolved_refs` — sem ela, o nº 2 seria afrouxamento
   em vez de precisão;
6. a precedência inteira numa árvore só, como função pura: `DENIED`, incompleto e `RESOLVED`
   sobre os **mesmos** dados, variando só o `source_ref`;
7. Parte B: a divergência legível e coberta sobrevive ao registro ilegível e ainda produz
   `stale`.

### Contrafactual

Reintroduzi o comportamento antigo no ponto exato onde ele existia — a expansão desistindo
de tudo assim que a árvore tem qualquer caminho ilegível, antes da checagem de segredo — e
rodei as reproduções: **5 dos 7 falharam, incluindo o teste decisivo.**

Os dois que passaram, e por quê (é honesto registrar, não é furo): o nº 1 testa o
tipo-**produto** de `list_tree`, que é estrutural e o patch do contrafactual não reverte; e
o nº 3 é a linha de base, uma árvore **sem** arquivo ilegível, que por construção não pode
ser afetada pela reversão.

### Verificação final

- **Rodadas 1 a 5 juntas**: 97 testes; com `test_source_ref_expansion` e
  `test_source_refs_glob_syntax` no mesmo comando, **274 testes**, todos passando.
- **Testes de segredo/DENY isolados** (`-k "segredo or secret or deny or denied"`): **115
  passando** — nenhum dos 18 findings anteriores reabriu.
- Backend: **980 passed / 6 skipped** (era 973; +7). `ruff` · `ruff format --check` ·
  `mypy` limpos (67 arquivos).
- O `mypy` foi ferramenta de correção aqui, não só de verificação: a troca do tipo-soma pelo
  tipo-produto quebrou **54 pontos** de uma vez, todos em teste, e cada um era um lugar que
  assumia o contrato antigo. Nenhum passou despercebido.
- Frontend não tocado. `docs/`, `.github/` e `CLAUDE.md`: `git diff` vazio.

### Pendências

- **6ª rodada do Codex.** O ponto que eu olharia primeiro é `_may_cover`: ele é a única
  afirmação do sistema sobre casamento feita **sem** o texto do alvo, e a correção inteira
  depende de ela ser uma sobre-aproximação de fato. O argumento está no docstring da função
  (os caracteres antes do primeiro metacaractere viram tokens `LITERAL` ancorados no
  início, logo todo caminho casado começa por eles) — é onde eu procuraria um contraexemplo.
- A mudança de veredito das duas asserções da rodada 3, descrita acima.
- Duas decisões ainda pendentes de Pedro, das rodadas anteriores: o rename
  `ProjectPlanningImport` → `PlanningImport`, e a confirmação da semântica de `**` colado a
  texto.
- Nada commitado, nada pushado. Branch de fase ainda não criada.

---

## 2026-09-08 — Codex (GPT-6) — E4: sexta auditoria, adversarial de `_may_cover`

- Arquivos alterados nesta sessão: `api/tests/test_context_audit_e4_round6.py` (novo)
  e esta entrada. Nenhum arquivo de produção alterado pela auditoria.
- Contexto lido: entradas E4 deste log, `git status --short` e `git diff --stat`
  completos, arquitetura 03 e 01, ADR-0006. A árvore de trabalho já continha a
  implementação e as cinco rodadas; essas alterações foram preservadas.
- Resultado da inspeção e dos 23 testes novos: nenhum falso negativo encontrado em
  `_may_cover`, nenhum finding E4-AUD6 novo. O corpus adicional compara 100 padrões
  com 258 caminhos (25.800 pares) e afirma `covers(path) => _may_cover(UTF8(path))`.
  Inclui Unicode, backslash literal, classes, `]` literal e as formas de globstar.

### Argumento e reproduções independentes

- `[` é o primeiro metacaractere em `a[bc]*`; o prefixo obrigatório é `a`, nunca
  `a[bc]`. O fechamento da classe não entra no prefixo. `a[]]*` e `a[!b]*` também
  conservam prefixo `a`; já `a]*` exige `a]`, pois esse `]` está fora de classe.
- `!` no início do padrão ou de um segmento é recusado pela gramática, antes de
  qualquer inferência de cobertura. Não existe negação implícita de source_ref.
- O prefixo é codificado integralmente em UTF-8. `caf\xc3A` não pode representar
  o prefixo `café` (`caf\xc3\xa9`), nem `caf\xc3\xaa` representa o mesmo caractere.
  Alterar/reparar bytes para fabricar outro nome não é uma operação do matcher.
  O teste contrastante mantém `caf\xc3\xa9` e introduz byte inválido depois dele:
  esse alvo é corretamente considerado possível, impedindo hash parcial.
- Prefixo vazio (`**`, inclusive `**/good.py`) permanece incerto até com nome
  inválido em `unrelated/deep/`. A sobrecobertura é intencional; nenhum desses
  casos produz `RESOLVED` ou `source_hash`.
- `Src/` e `src/` são diferentes byte a byte. Nenhum casefold ou normalização
  Unicode é aplicado à identidade retornada pelo Git.
- A mudança de veredito da rodada 3 é sólida: `ws/**` exige `ws/`, enquanto
  `ws\outside.py` é um nome na raiz, com `0x5c` em vez de `0x2f`. A ausência de
  cobertura é demonstrável. `**` continua incompleto sobre o mesmo arquivo.
- Os alvos ilegíveis foram gravados com `hash-object`, `mktree -z` e `commit-tree`
  em repositórios temporários, incluindo árvores aninhadas reais; não são mocks
  de `list_tree`. Foram conferidos os bytes retornados e os arquivos legíveis.
- Cenário com `src/bad-\xff.py` e `other/bad-\xfe.py`: só o primeiro pode ser
  alcançado por `src/*`. Com `src/.env`, todas as seis permutações de referências
  (incluindo uma irresolúvel) resultam em `DENIED`; criação HTTP retorna 422.
  Sem segredo, retorna incompleto/201 `unknown` sem baseline. `src/good.py`
  isolado resolve com os dois diagnósticos preservados.

### Gates e limites desta sessão

- 23 testes novos passaram com Python 3.12 em venv temporário fora do OneDrive.
  O venv indicado no `CLAUDE.md` não existe neste ambiente; foram instaladas as
  dependências declaradas em `api/pyproject.toml`, sem alterar esse arquivo.
- Ruff check, Ruff format e mypy passaram (68 arquivos no mypy).
- Frontend: 115 testes/18 arquivos; ESLint e builds Pages/local passaram.
- Invariantes revistos: subprocess somente em git_runtime; gramática somente no
  expansor; denylist em safety/secrets é a exceção independente; aliases de
  separador derivados de `PATH_SEPARATOR_ALIASES`; imports nas fronteiras previstas.
- Suíte completa do backend: **1003 passed / 6 skipped**, em 254,55 segundos.
  Inclui as quatro reproduções exatas da rodada 5, as duas asserções revisadas
  da rodada 3, todas as regressões anteriores e os gates HTTP de importação de
  seed, stale(working_tree) sem commit e recusa de glob que alcança .env.
- O gate de importação com um planejamento real do usuário depende de localizar
  esse dado: o repositório tem fixtures e defaults com planejamentos vazios. A
  localização foi solicitada ao usuário; importação de fixture não será apresentada
  como importação de dado real. Fechamento GREEN geral ainda pendente desse gate.
- Contagem solicitada: seis rodadas, 19 findings anteriores, zero novos nesta
  auditoria. Nenhum commit ou push realizado.

---

## 2026-09-09 — Claude Sonnet 5 (effort medium) — E4: gate funcional de importação, com dado real do usuário — GREEN

Fecha o único item pendente que a 6ª auditoria tinha deixado em aberto: o gate de
importação exercitado com um planejamento real, não fixture. Nenhum código de produção foi
tocado — é execução de gate, não correção. Nada commitado, nada pushado.

### Dado usado

Payload fornecido por Pedro, extraído por ele do `localStorage` do dashboard (chave
`freelance-focus:data:v3`, `projectPlannings`, projeto **"Job Hunter Bot"**) — aspas soltas
de artefato de copy-paste já removidas por ele antes do envio.

**Uma lacuna no payload, e a decisão que tomei sobre ela**: `PlanningImportDecision.title`
é obrigatório (`min_length=1`) no schema já implementado, e o modelo comercial real
(`src/types/index.ts::TechnicalDecision`) **tem** um campo `title` — mas o extrato que
recebi trazia só `decision` e `reason`. Não inventei um título novo: usei o próprio texto de
`decision` ("Verificação manual obrigatória antes de aplicar via Gupy") como `title`, porque
é dado real já presente no payload, não uma invenção. Isso fica registrado aqui explicitamente
porque **pode não ser** o `title` original que está de fato salvo no registro de origem —
só Pedro pode confirmar isso olhando o `localStorage` de novo. Não bloqueou o gate porque a
tarefa era demonstrar o caminho de importação funcionando com conteúdo real, e o conteúdo
usado é genuíno em todo o resto.

### Execução

Não critaria um teste de suíte permanente para uma execução de gate contra dado pessoal de
um usuário — isso viraria fixture disfarçada de dado real na próxima leitura do arquivo, e
essa é exatamente a distinção que a 6ª auditoria fez questão de preservar. Em vez disso,
rodei um script avulso (fora de `tests/`) que sobe a aplicação real — `AppSettings` com
banco SQLite temporário, `alembic upgrade head` (schema de migration, não `create_all`),
`TestClient` sobre o `app` real — cria um `DevWorkspace` de verdade e chama a rota HTTP real
duas vezes: `POST /context/import` e depois `GET /context` para reler o que foi persistido.

`POST /api/workspaces/{id}/context/import` → **201**, `"created": 7`.

### Contagem: 7, não 9 — batendo com [03] §1

O prompt cogitava 9; a tabela de `docs/architecture/03-context-architecture.md` §1 (e a
implementação em `service.import_planning_seed`, que a segue literalmente) mapeia:

| Campo do seed | Entradas | Neste payload |
| --- | --- | --- |
| `problem` + `objective` | **1** `objective` | 1 |
| `functional_requirements[]` / `non_functional_requirements[]` | **2** `requirements` | 2 |
| `stack[]` | **1** `stack` | 1 |
| `architecture` | **1** `architecture` | 1 |
| `technical_decisions[]` | **N** `decisions` | 1 (N=1) |
| `risks[]` | **N** `risks` | 1 (N=1) |

Total: **7**. O payload tem exatamente uma decisão técnica e um risco — não dois — e
`problem`+`objective` sempre viram **uma** entrada combinada, nunca duas. A contagem de 9
não corresponde a este payload sob o mapeamento congelado; 7 é o número certo, e é o que a
API devolveu.

### Confirmação item a item (não só a contagem)

Reli as 7 entradas via `GET /api/workspaces/{id}/context` — todas `origin=imported_planning`,
`source_refs=[]`, `state=fresh`, como o contrato exige. Conteúdo, uma a uma:

- **`objective`** — corpo é `## Problema\n\n<problem>\n\n## Objetivo\n\n<objective>`, os dois
  textos originais inteiros, sem corte;
- **`requirements` (funcionais)** — os 5 itens de `functional_requirements`, um por linha em
  bullet `- `, na ordem enviada;
- **`requirements` (não funcionais)** — os 2 itens de `non_functional_requirements`, mesma
  forma;
- **`stack`** — as 4 entradas de `stack`, em bullet;
- **`architecture`** — o texto de `architecture` **verbatim**, inclusive a quebra de linha
  interna (`\n` entre "single-user" e "Evolução planejada");
- **`decisions`** — `structured = {decision, reason, date: null}`, os dois textos originais
  preservados; `date` é `null` porque o seed não carrega data (não inventei `utcnow()`, como
  o código já documenta que não faz);
- **`risks`** — `structured = {mitigation}`, texto original preservado.

Nenhum campo veio truncado, reordenado ou reformatado além do que a tabela de [03] §1 já
define (bullets para listas, cabeçalhos markdown para problema/objetivo).

### Suíte completa, mais uma vez

`980` era a contagem antes da 6ª auditoria; a 6ª auditoria já tinha subido para
**1003 passed / 6 skipped** com os 23 testes dela. Rodei a suíte completa de novo, depois da
execução deste gate (que não toca código): **1003 passed / 6 skipped**, idêntico. `ruff
check`, `ruff format --check` e `mypy` (68 arquivos) limpos. `git status`/`git diff --stat`
inalterados por esta entrada — nenhum arquivo de produção ou de teste permanente foi criado
ou editado.

### Veredito

O gate funcional pendente da E4 — "importar um planejamento real" — está **completo**, com
dado genuíno do usuário, e a resposta bate com a arquitetura congelada em todos os pontos
verificados. Combinado com o GREEN geral que a 6ª auditoria já tinha dado nos demais pontos
(seis rodadas, 19 findings, zero novos na 6ª), a E4 está **pronta para commit e push**,
pendente só da decisão de Pedro de seguir com isso — e, se ele quiser, da confirmação do
`title` original da decisão técnica mencionada acima.

### Pendências

- Confirmação do `title` original de `technical_decisions[0]` (ver acima) — não bloqueia o
  commit, mas vale conferir.
- As duas decisões de nomenclatura/semântica já registradas em rodadas anteriores (rename
  `ProjectPlanningImport` → `PlanningImport`; semântica de `**` colado a texto) continuam
  sem confirmação explícita de Pedro, embora não tenham gerado nenhum finding de auditoria.
- Nada commitado, nada pushado — aguardando autorização explícita de Pedro, como pedido.

---

## 2026-09-09 — Claude Sonnet 5 (effort medium) — E5 (formalização): `rendered_context_hash` sem `created_at` + política de orçamento V1

Tarefa **SOMENTE DOCUMENTAÇÃO** — correção de um exemplo ilustrativo e registro de
decisões de design já fechadas nesta sessão (sem decisão nova). `docs/` alterado com
autorização explícita de Pedro nesta conversa, escopo restrito aos dois arquivos abaixo.

- Arquivos alterados:
  - `docs/architecture/02-data-model.md` — §5 (*Rendered Context Artifact*):
    (a) removido `created_at` do bloco de exemplo JSON do artefato — timestamp operacional
    vive só em `ContextManifest.created_at`; (b) frase normativa nova: `rendered_context_hash`
    é o sha256 dos bytes UTF-8 canônicos (sem BOM) do JSON canônico, que **não** inclui
    `created_at`; (c) nota "V1 (E5)" sobre orçamento — sem truncamento parcial de bloco:
    entrada que não cabe inteira em `max_context_tokens` é excluída inteira
    (`excluded`, `reason=budget`); `domain=objective` é sempre incluída mesmo excedendo o
    orçamento (`approx_tokens > max_context_tokens` é esperado nesse caso); campos
    `truncated`/`original_chars`/`emitted_chars` do schema de bloco ficam reservados para
    uso futuro, sempre `truncated=false` nesta fase.
  - `AGENT_LOG.md` — esta entrada.

- Decisões tomadas: **nenhuma nova.** Só formalização normativa para eliminar ambiguidade
  antes de implementar a E5.

- GATE: `git diff` de `02-data-model.md` = só adição de texto + a remoção pontual de
  `created_at` do exemplo. Zero remoção de qualquer outra linha. Verificado.

- Pendências: diff aguardando revisão de Pedro. **Nada commitado, nada pushado.**

---

## 2026-09-09 — Claude Opus 5 (effort high) — E5: Context Router + file map (implementação)

Implementação completa da E5, nas seis sub-etapas do plano aprovado. **Nada commitado,
nada pushado.** `docs/`, `.github/`, `CLAUDE.md` e `src/` (frontend): `git diff` vazio.

### Arquivos alterados

- **Novos** — `api/app/context_engine/file_map.py` (192), `rendering.py` (159),
  `selection.py` (647), `manifest.py` (299); `api/tests/test_context_router_e5.py`
  (1087, **33 testes**).
- **Modificados** — `api/app/context_engine/__init__.py` (reexporta a superfície nova e
  documenta as três regras de dono único da fase); `api/tests/context_helpers.py`
  (`make_task`, `head_of`).
- Nenhuma rota HTTP nova. `select_context`, `freeze_manifest` e `render_context` são
  capacidades internas de `context_engine/`, consumidas pelo Planner a partir da E6. As
  `WorkspaceTask` dos testes são escritas **direto pela sessão de teste** só para
  satisfazer a FK de `ContextManifest.task_id` — não é caminho de produção, e o docstring
  de `make_task` diz isso.

### As quatro regras estruturais do prompt, e onde cada uma virou código

1. **`canonical_json` reaproveitado, nunca reimplementado.** `grep` confirma: uma única
   definição em `app/safety/canonical.py`, importada por `content_hash`, `file_map`,
   `manifest`, `rendering`, `service`, `source_ref_expansion` e `policy`. Zero `json.dumps`
   fora dela em `app/`.
2. **Zero `float`.** Pesos, distâncias e orçamento são `int`; `approx_tokens` é
   `(len + 3) // 4`. `updated_at` vira epoch inteiro por aritmética de `timedelta`
   (`epoch_seconds`), **nunca** `datetime.timestamp()` — que devolve `float` e é
   justamente a comparação que decide o desempate.
3. **Escrita em bytes.** `render_context` abre `"wb"` sobre bytes que ele mesmo produziu
   com `.encode("utf-8")`. Mesma classe de bug de E4-AUD4-001 (`subprocess.run(text=True)`),
   do outro lado da fronteira: lá na leitura, aqui na escrita. Escrita atômica por arquivo
   temporário + `os.replace`, para nunca existir blob truncado com nome de blob íntegro.
4. **Ordenação canônica explícita em tudo que entra em hash ou score**: `entries` (por
   `entry_id`), `source_files` (por `path`), `working_tree_divergence.covered` (por
   `(path, kind)`), `derived` (por `(kind, hash)`), `excluded` (por
   `(reason, path_or_entry)`), itens do file map (por `path`) e candidatos (normalizados,
   deduplicados, ordenados). Nenhuma ordem de `dict`, `set`, inserção ou `SELECT`
   sobrevive até o resultado.

### Sub-etapas

- **1 — `build_file_map`.** `[{path, dir_path, dir_depth, extension}]` ordenado por `path`,
  construído **só** de `list_tree(base_commit)`. Cache por `(workspace_id, base_commit)`
  ([ADR-0006] item 4); `local_path` fica **fora** da chave de propósito, para não divergir
  do documento (`DevWorkspace.local_path` é UNIQUE, então não distinguiria nada). Leitura
  que falhou nunca é cacheada — falha é transitória. `hash` por `canonical_json`.
- **2 — `select_context`.** Tabela inteira: sobreposição `source_refs`=100, domínio
  afetado=100, tags=50, proximidade=`max(0, 30 - distância)`, `fresh`=10. `objective`
  sempre incluída. Chave de ordenação **total**: score desc → `updated_at` (epoch int)
  desc → `entry_id` asc — `entry_id` é PK, então nada sobra para o `SELECT` desempatar.
  Corte por orçamento sobre `render_block_text()`, a representação real.
- **3 — `render_block_text`.** A **única** função que produz texto emitido; não existe
  estimativa de tamanho em lugar nenhum. Pipeline: `normalize_text` de [03] §2 (a mesma de
  `content_hash`) → transformações previstas → `safety.redact` → framing fixo. A redação
  roda sobre **todo o conteúdo autoral de uma vez**, então um segredo partido entre `body`
  e `structured` não escapa por estar em dois campos, e a contagem de caracteres é sempre
  do texto já redigido.
- **4 — `freeze_manifest`.** `manifest_hash` sobre os sete campos semânticos; `id`,
  `task_id`, `created_at`, `rendered_context_*`, `renderer_version`, `approx_tokens`,
  `total_chars` e o próprio `manifest_hash` ficam **fora**.
- **5 — `render_context`.** `objeto canônico → canonical_json → UTF-8 sem BOM → bytes →
  sha256 → artifacts/<sha256>.json`. Idempotente: o nome **é** o hash, então um arquivo já
  presente já tem aquele conteúdo.
- **6 — revisão.** Abaixo.

### Gates

- **Backend: 1044 passed / 6 skipped** (era 1011; +33). `ruff check` · `ruff format --check`
  · `mypy` limpos (73 arquivos).
- **`test_architecture.py` verde**, incluindo
  `test_context_engine_nao_importa_camadas_superiores`. Confirmado também por `grep`: os
  quatro módulos novos importam só `app.db`, `app.git_runtime`, `app.safety`,
  `app.context_engine`, `sqlalchemy` e stdlib. Nenhum `agent_runtime`, `tool_executor`,
  `orchestrator`, `api`, `fastapi` ou `starlette`.
- **Frontend não tocado** (`git diff` vazio em `src/`), logo `npm test`/`npm run lint` não
  foram reexecutados nesta rodada.

### Os 9 testes de determinismo — todos implementados

| # | Teste |
| --- | --- |
| 1 | `test_file_map_e_deterministico_byte_a_byte` |
| 2 | `test_select_context_pontua_conforme_a_tabela` + `..._ordena_por_score_depois_recencia_depois_id` + `..._desempata_por_entry_id...` |
| 3 | `test_manifest_hash_ignora_id_task_id_e_created_at` |
| 4 | `test_render_context_grava_o_artefato_e_e_idempotente` |
| 5 | `test_ordem_de_insercao_no_banco_nao_altera_o_resultado` |
| 6 | `test_render_block_text_ignora_a_ordem_das_chaves_de_structured` + `test_ordem_das_chaves_de_structured_nao_altera_os_hashes` |
| 7 | `test_updated_at_muda_selecao_mas_nao_a_identidade_semantica` + `test_epoch_seconds_nunca_passa_por_float` |
| 8 | `test_processo_python_separado_produz_os_mesmos_hashes` |
| 9 | `test_hash_do_arquivo_em_disco_bate_com_o_registrado` |

O **teste 8 foi implementado de verdade**, e o valor dele está no `PYTHONHASHSEED`: dois
processos filhos rodam com seeds **explicitamente diferentes** (`0` e `12345`), sobre o
mesmo banco e o mesmo commit, e são comparados entre si e com o pai (que tem um terceiro
seed, aleatório). `hash()` de `str` é randomizado por esse seed, e com ele muda a ordem de
iteração de qualquer `set` de strings — se alguma coleção chegasse a um hash sem ordenação
explícita, as três execuções divergiriam. É a reprodução direta da classe de bug que a fase
existe para fechar, e não um "roda de novo e dá igual".

O teste 6 compara a **mesma** entrada reescrita com as chaves em outra ordem, e não duas
entradas: `entry_id` faz parte da identidade do bloco (é o que responde de onde o texto
veio depois de a entrada sumir), então comparar entradas diferentes mediria outra coisa. A
primeira versão do teste caiu exatamente nisso e foi corrigida.

O teste 7 separa explicitamente as duas coisas que o prompt manda não confundir:
`updated_at` **pode** mudar a seleção (é o critério de desempate) e o timestamp
**operacional** (`ContextManifest.created_at`, `id`, `task_id`) **não pode** mudar hash
nenhum.

### Decisões de implementação não 100% especificadas no prompt

1. **Assinatura.** `select_context(session, workspace, *, base_commit, candidate_paths,
   max_context_tokens, affected_domains=(), objective_terms=(), transformations=…,
   policy=None)`. `session` é necessário para ler as entradas e segue o padrão de
   `create_entry`/`verify_workspace_entries`. `affected_domains` e `objective_terms` são a
   saída do Task Analyzer ([03] §5), que só existe na E6 — nascem vazios, e aí os dois
   sinais valem zero para todas as entradas sem quebrar o determinismo.
2. **`candidate_paths` são caminhos literais, não globs.** [03] §4 fala em "globs
   candidatos da análise", mas o parâmetro do plano se chama `candidate_paths` e nenhum
   Analyzer existe ainda para emiti-los. Validação por
   `validate_source_ref(..., allow_glob_syntax=False)`. Os `source_refs` **da entrada**
   continuam podendo ser glob e são casados pelo dono único da gramática. Candidato em
   forma de glob fica para a E6.
3. **Sobreposição é binária (100), não somada por arquivo casado** — senão `src/**` vence
   qualquer entrada precisa só por cobrir mais arquivos.
4. **Proximidade** = menor distância de árvore de diretórios entre os `dir_path` dos
   arquivos que a entrada resolveu e os dos candidatos. Sem arquivo resolvido ou sem
   candidato, o sinal vale `0` — "não sei" não é "perto". Sobreposição dá distância `0` e
   portanto 30 pontos **por cima** dos 100: são sinais diferentes de [03] §4, e nenhum
   absorve o outro.
5. **Casamento de tag** é igualdade exata após NFC + `strip` + `casefold`. Sem substring,
   sem radical, sem sinônimo — qualquer um dos três seria heurística não determinística de
   linguagem dentro de um roteador que precisa ser reproduzível.
6. **Bônus `fresh` só para `state = fresh`.** `unknown` não ganha nada: não é frescor
   confirmado.
7. **O corte de orçamento continua varrendo depois da primeira entrada que não coube** —
   uma menor, mais abaixo no ranking, ainda pode caber. Parar na primeira desperdiçaria
   orçamento sem melhorar a ordem, e a ordem de emissão é preservada de qualquer forma
   porque a lista final é reordenada pela chave de ranking. Toda entrada pulada entra em
   `excluded(budget)`, então "por que o agente não sabia disso?" continua tendo resposta.
8. **`approx_tokens = ceil(chars / 4)`.** Nenhum tokenizador de provider existe nesta fase,
   e importar um criaria a dependência de provider que [03] §4 proíbe ao Context Engine. O
   campo se chama `approx_` no próprio [02] §5.
9. **`renderer_version = "e5.block.v1"`** — valor escolhido aqui; nenhum documento o fixa.
10. **Framing do bloco**: cabeçalho `### contexto · <domain>`, depois `## <título>`, corpo
    e o `structured` canônico numa cerca de código JSON. `structured` entra porque é lá que
    mora a justificativa de uma `decisions`/`risks` ([ADR-0006] item 5) — omiti-lo
    entregaria a decisão sem o porquê. A redação roda sobre a concatenação do conteúdo
    autoral e o cabeçalho fixo é acrescentado depois: "framing" aqui é o cabeçalho e a
    quebra final, e os marcadores markdown que separam os campos ficam **dentro** da string
    redigida, de propósito, para o redator enxergar um segredo partido entre dois campos.
11. **`transformations` não consegue desligar a redação**: lista sem `REDACT` levanta
    `ValueError`, e a ordem do pipeline vem de uma constante, não da ordem em que o
    chamador listou.
12. **`role` = `domain` da entrada; `origin` é objeto** (`kind`, `entry_id`, `domain`,
    `title`, `content_hash`). [02] §5 não tipa nenhum dos dois. `origin` como objeto é o
    que faz o artefato continuar respondendo depois de a entrada ser apagada — um
    `entry_id` órfão não responde nada.
13. **`truncated=false` e `original_chars == emitted_chars`** em todo bloco, imposto pela
    política V1 de [02] §5 (sem truncamento parcial). Gravados em vez de omitidos: a
    ausência precisaria ser reinterpretada depois.
14. **`manifest_hash` inclui `"v": 1`.** Não é campo do `ContextManifest` — é o marcador de
    formato que [02] §7 ("Versão") exige, mesmo padrão de `content_hash`. Constante, então
    não afeta nenhuma comparação semântica.
15. **`rendered_context_ref` é relativo ao store** (`artifacts/<sha256>.json`), não
    absoluto: `data_dir` muda de máquina e o `tmp_path` de teste muda a cada execução, e o
    caminho absoluto tornaria a linha incomparável entre ambientes por um motivo sem
    relação com o contexto selecionado. `RenderedContext.path` carrega o absoluto, só em
    memória.
16. **`ContextTreeUnavailable`** (`context_tree_unavailable`, **409**) — subclasse nova de
    `ContextError` para "não deu para ler a árvore do `base_commit`". Nenhuma rota a
    levanta ainda; ela existe para a E6 não ter de inventar o status na hora.
17. **`working_tree_divergence.covered` usa os `source_refs` das entradas selecionadas**,
    calculado **por entrada** e unido — nunca um matcher único sobre a união dos refs. Um
    ref recusado invalidaria o matcher inteiro e apagaria a cobertura conhecida das outras,
    que é exatamente o *fail open* de E4-AUD5-001. `dirty_file_count` é do workspace inteiro
    e é registrado sempre ([03] §3: "nunca é escondida").
18. **Cache do file map**: `dict` global do processo, teto de 64, esvaziado inteiro no
    estouro — sem LRU, porque todo valor é reconstruível e idêntico e escolher entre
    equivalentes seria complexidade sem ganho. `clear_file_map_cache()` existe só para os
    testes.
19. **`extension` preserva a caixa** e um nome que só tem o ponto inicial (`.gitignore`,
    `.env`) tem extensão `""` — ali o ponto abre o nome, não separa sufixo.
20. **Escrita atômica** (temporário no mesmo diretório + `os.replace`) por cima do modo
    binário exigido. O nome temporário carrega PID + UUID, então dois processos gravando o
    mesmo hash não disputam o intermediário.

### Pendências

- Diff aguardando revisão de Pedro. **Nada commitado, nada pushado.** Branch de fase ainda
  não criada.
- Auditoria independente (Codex) da E5 ainda não rodou.
- **E6 não iniciada**, conforme o prompt.

---

## 2026-09-09 — Claude Sonnet 5 (effort medium) — E5 (addendum): formalização de padrão de identidade de caminho, sinal `exact_literal_match` e ordem redação/estrutura

Tarefa **SOMENTE DOCUMENTAÇÃO** — três notas aditivas pedidas nesta sessão de
planejamento, sobre conflitos reais identificados entre `canonical_json` (NFC) e
identidade de caminho Git, e sobre a ordem exigida entre redação e formatação estrutural.
`docs/` alterado com autorização explícita de Pedro nesta conversa, escopo restrito aos
dois arquivos abaixo.

- Arquivos alterados:
  - `docs/architecture/02-data-model.md` — §7 (`canonical_json`, tabela de regras): nota
    nova "Identidade de caminho vs. normalização textual" — string que representa
    identidade de caminho de arquivo precisa ser pré-codificada (hex/base64) antes de
    entrar em `canonical_json`, para não colapsar duas identidades Git distintas (NFC vs
    NFD) na normalização NFC que a função já faz sobre texto autoral. Não altera a regra
    de NFC existente — é responsabilidade do chamador, aditiva.
  - `docs/architecture/03-context-architecture.md` — §4 (Context Router): linha nova na
    tabela de scoring, `exact_literal_match` (match literal exato entre `source_ref` e
    `candidate_path`, sinal mais alto que sobreposição+proximidade combinadas, mesmo sem o
    candidato existir no `base_commit`) + nota explicando o motivo (precisão de match ≠
    cobertura de arquivos). E nota sobre ordem redação → formatação estrutural: a redação
    roda sobre representação **plana** de título+corpo+`structured` linearizado, antes de
    JSON/markdown/framing; cópias propagadas para `origin`/manifest refletem o resultado
    já redigido.
  - `AGENT_LOG.md` — esta entrada.

- **Discrepância sinalizada e corrigida durante a escrita:** o prompt afirmava, para a
  nota 1, "Ver E5 (file_map, `ContextManifest.source_files`) para o helper de referência"
  — mas **esse helper não existe**. O código da E5 ([file_map.py](api/app/context_engine/file_map.py),
  [selection.py](api/app/context_engine/selection.py)) coloca `path` como string crua
  direto em `canonical_json`, sem nenhuma pré-codificação hex/base64: é exatamente a
  colisão NFC/NFD que a nota descreve, não um caso já resolvido. Removi a referência ao
  helper inexistente e documentei as três notas como **pendências conhecidas da E5** (blocos
  `>` dedicados, um por nota), incluindo a terceira — a ordem redação/JSON de
  `render_block_text` também não segue a garantia mais forte que a nota de redação
  formaliza (o `structured` é serializado via `canonical_json` **antes** do `redact()`
  rodar sobre o texto concatenado; funciona hoje porque o redator opera por regex sobre a
  string inteira, mas não é a ordem descrita). Nenhuma auditoria (Codex) da E5 rodou até
  agora — o prompt também presumia "padrão descoberto por auditoria", e não há registro de
  auditoria no `AGENT_LOG.md`; a formalização documenta a regra que uma auditoria futura
  vai cobrar, não um finding já confirmado.

- Decisões tomadas: **nenhuma nova de arquitetura.** Formalização normativa das três
  notas, com os ajustes de precisão factual acima. Nenhuma mudança na regra de NFC
  existente de `canonical_json`, nenhuma mudança na tabela de sinais além da linha nova.

- GATE: `git diff` dos dois arquivos = só adição (blocos `+`, nenhum `-`, nenhuma linha
  alterada). Verificado.

- Pendências: diff aguardando revisão de Pedro. **Nada commitado, nada pushado.** Três
  itens de código ficam abertos para uma correção futura da E5 (pré-codificação de `path`
  em `file_map`/`source_files`; sinal `exact_literal_match` em `select_context`; ordem
  redação-antes-de-JSON em `render_block_text`) — nenhum implementado nesta tarefa, que é
  só documentação.

---

## 2026-09-09 — Claude Opus 5 (effort high) — E5: correção de 9 findings de auditoria + 3 pendências documentais

Fecha em código os 9 findings (AUD-001..009) e as três "Pendência conhecida (E5)" que a
sessão anterior tinha deixado documentadas mas não implementadas. **Nada commitado.**
Segue para uma segunda rodada do Codex.

**Discrepância sinalizada antes de começar:** o prompt referenciava uma "auditoria
E5-AUD-001..009 completa, já commitada" no `AGENT_LOG.md` — ela não existe no log; a
sessão anterior (addendum documental) registra explicitamente "nenhuma auditoria da E5
rodou ainda". Os 9 findings mapeiam três-para-três nas três pendências que eu mesmo
documentei, mais dois novos (AUD-005 desempate por microssegundo, AUD-009 integridade do
artifact) que são autocontidos e bem especificados — segui a especificação técnica dada,
que é acionável independente do documento de auditoria existir.

### Arquivos alterados

- `api/app/context_engine/rendering.py` — **redesenho completo** do pipeline de
  `render_block_text` (321 linhas).
- `api/app/context_engine/selection.py` — `exact_literal_match`, desempate por
  microssegundo, adaptação ao novo `RenderedBlock` (707 linhas).
- `api/app/context_engine/manifest.py` — pré-codificação de path no `manifest_hash`,
  verificação de integridade do artifact antes de reutilizar (370 linhas).
- `api/app/context_engine/file_map.py` — `encode_path_identity` (o helper de referência
  que a documentação já citava sem que ele existisse) e uso em `as_canonical()`
  (217 linhas).
- `api/app/context_engine/__init__.py` — reexporta a superfície nova/renomeada.
- `api/tests/test_context_router_e5.py` — 45 testes (era 33; +12 líquido — vários testes
  antigos precisaram só de ajuste de API, não são novos).
- `docs/architecture/02-data-model.md`, `docs/architecture/03-context-architecture.md` —
  as três "Pendência conhecida (E5)" substituídas por "Fechado na correção de auditoria",
  apontando para o código e o teste que fecham cada uma.

### GRUPO B (prioridade máxima) — AUD-002/003/004: redesenho de `render_block_text`

**O bug:** a versão anterior serializava `structured` em JSON (`canonical_json`) **antes**
de `redact()` rodar. `{"password":"hunter2"}` vira `{"password":"hunter2"}` no compacto —
o `"` que o JSON insere logo depois de `password` não é espaço nem `:`/`=`, e o padrão
`assigned_secret` (`\s*[:=]\s*` entre o nome do campo e o valor) nunca disparava. Um
segredo partido entre `body` e `structured` nunca se recompunha se os dois fossem
redigidos separadamente. Confirmado por reconstrução standalone da implementação anterior
(fora do working tree, só para o teste): `hunter2` e `sk-1234567890ABCDEF` vazavam os
dois.

**A correção, em duas iterações (a primeira ainda vazava):**

*Primeira tentativa* — linhas "chave: valor" para `structured`, redigidas junto com uma
cópia dos valores **colada ao corpo sem separador** (para pegar o token partido). Um
smoke test manual pegou o problema antes de eu escrever os testes formais: o mesmo valor
(`hunter2`) aparecia **duas vezes** no corpus — uma vez colado ao corpo sem contexto de
campo (onde `assigned_secret` não reconhece nada, porque não há `password` por perto), e
uma vez na linha rotulada (onde reconhece e redige). `redact()` rodando sobre a
concatenação inteira redigia a cópia rotulada e **deixava vazar a cópia sem contexto**.

*Correção final* — `_redact_body_and_structured`: cada valor passa pela redação **uma
única vez** (`_redact_leaf` redige `"<path>: <value>"` isoladamente e recorta de volta só
o valor, pela primeira ocorrência de `": "` — `path` nunca contém essa subsequência).
Segredo partido entre campos é fechado por uma leitura **só de verificação**: cola `body`
a todos os valores de `structured` sem separador, conta quantas redações essa leitura
colada revela, e compara contra a soma das redações já feitas campo a campo. Se a colada
achar **mais**, existe segredo cruzando a fronteira que nenhuma leitura isolada revela por
inteiro — e a resposta é redigir `body` e as folhas envolvidas **por inteiro**, nunca um
recorte que arrisque deixar passar metade de um token. Mesmo princípio que `redact()` já
declara para si ("prefere redigir demais a deixar passar"), estendido à fronteira entre
campos.

O título é redigido à parte (`redact(title)`, sem depender de contexto de outro campo) e
o resultado passa a ser o **único** valor que se propaga: `render_block_text` agora
devolve `RenderedBlock(text, title)`, e `title` substitui `entry.title` em
`ScoredEntry.title` — de onde `blocks[].origin.title` e `ContextManifest.entries[].title`
o leem. Nenhuma cópia paralela do título cru sobrevive em lugar nenhum (antes,
`ScoredEntry.title = entry.title` cru — `origin.title`/`entries[].title` vazavam mesmo
quando `text` já mostrava redigido).

`RENDERER_VERSION` avançou para `"e5.block.v2"` — a forma do texto emitido mudou.

**Testes (reproduções exatas):**
- `test_segredo_no_titulo_propaga_redigido_a_todo_lugar` — confere `blocks[].text`,
  `blocks[].origin.title`, `ContextManifest.entries[].title` **e os bytes do artifact**.
- `test_atribuicao_em_structured_e_reconhecida_apos_linearizacao` — `{"password":
  "hunter2"}`.
- `test_segredo_partido_entre_body_e_structured_como_token_continuo` — `"sk-123456"` em
  `body`, `"7890ABCDEF"` em `structured`; inclui prova negativa de que as duas metades
  coladas **fora** do pipeline batem o padrão, confirmando que o teste exercitaria o
  padrão certo se a implementação vazasse.
- `test_duas_representacoes_do_mesmo_valor_nao_vazam_uma_pela_outra` — regressão do bug
  que a primeira tentativa introduziu.
- `test_render_block_text_redige_segredo_partido_entre_campos` (já existia, PEM split) —
  continua verde, cobre o caso onde a fronteira tolera separador.

### GRUPO A — AUD-001/006/007: `encode_path_identity` + consistência de normalização

**O bug:** `canonical_json` normaliza toda string em NFC — correto para texto autoral,
errado para identidade de caminho: um path em NFC e o mesmo path visualmente em NFD são
**arquivos diferentes** para o Git (blob SHA distinto), e a normalização colapsava os dois
no mesmo texto antes de hashear. Confirmado por reconstrução da serialização anterior:
dois `FileMapItem.as_canonical()` com `path` em NFC e NFD produziam `canonical_json`
**idêntico**.

**A correção:** `encode_path_identity(path) -> path.encode("utf-8").hex()` — hex não tem
variante de normalização Unicode nenhuma, então atravessa `canonical_json` inalterado.
Aplicado em três pontos, todos **só na forma que entra no hash**, nunca no dado
armazenado/exibido:
- `FileMapItem.as_canonical()` — `path`, `dir_path` e `extension` (este último é
  substring do `path`, mesmo risco);
- `manifest.compute_manifest_hash` — `source_files[].path` e
  `working_tree_divergence.covered[].path`, transformados **dentro** da função, sem tocar
  o que `ContextManifest.source_files`/`working_tree_divergence` grava;
- `excluded[].path_or_entry` — só quando `reason` é `secret_policy`/`out_of_workspace`
  (é caminho); em `reason=budget` é um `entry_id` (UUID, sempre ASCII, imune a NFC/NFD) e
  não precisa da codificação — decisão explícita para não misturar um campo que não
  representa caminho na pré-codificação que existe especificamente para caminho.

`FILE_MAP_VERSION` e `MANIFEST_HASH_VERSION` avançaram para `2` — a forma canônica mudou.

**Consistência de normalização (a outra metade do Grupo A):** `content_hash`, o título
propagado para `origin`/`entries[].title`, e a medição (`total_chars`/`approx_tokens`)
já usavam a mesma `normalize_text` antes desta rodada — o redesenho do Grupo B preservou
isso por construção (mesma função, chamada nos mesmos dois lugares). Confirmado por teste
dedicado, não presumido.

**Testes (reproduções exatas):**
- `test_file_map_trata_nfc_e_nfd_como_arquivos_distintos` — `café.py` grafado em NFC e o
  mesmo nome em NFD, **dois arquivos reais** no mesmo commit git (confirmado
  manualmente antes de escrever o teste: NTFS + git preservam os dois como blobs
  distintos no Windows desta máquina — `git ls-tree` devolve dois SHAs diferentes).
- `test_manifest_hash_trata_nfc_e_nfd_como_identidades_distintas` — mesma prova, na
  camada de `compute_manifest_hash`; inclui round-trip do hex de volta para os bytes
  UTF-8 exatos.
- `test_titulo_com_crlf_e_espaco_final_fica_consistente_entre_content_hash_e_manifest` —
  `"Line A  \r\nLine B"` vs. `"Line A\nLine B"`. **Ajuste de escopo em relação ao pedido
  literal:** comparar `manifest_hash` entre duas entradas *diferentes* (título cru vs.
  título já normalizado, cada uma sua própria `ContextRegistryEntry`) não podia dar
  igual — `entry_id` é semanticamente relevante em `manifest_hash` (por design, coberto
  por `test_manifest_hash_ignora_id_task_id_e_created_at`, que testa o oposto: `id`,
  `task_id`, `created_at` **não** entram). O teste ficou em duas camadas: (1)
  `compute_content_hash` chamado diretamente com as duas formas do título, sem banco,
  produz o mesmo hash; (2) uma entrada real com o título cru — `entry.content_hash` e
  `manifest.entries[0]["title"]` concordam com o que as funções puras produziriam
  isoladamente.
- `test_medicao_usa_a_representacao_final_normalizada_e_redigida` — corpo com caractere
  combinante repetido, `transformations=(REDACT,)` (sem `NORMALIZE`): `approx_tokens`
  mede exatamente `RenderedBlock.text`, nunca uma forma intermediária.

### GRUPO C — AUD-008: `exact_literal_match`

`SCORE_EXACT_LITERAL_MATCH = 131` — a menor margem inteira que garante vencer **qualquer**
combinação de `SCORE_SOURCE_REF_OVERLAP + PROXIMITY_MAX` (100 + 30 = 130) sozinho.
`_has_exact_literal_match` verifica, por comparação de string (nunca resolução contra
`base_commit`), se algum `source_ref` compilado da entrada é **literal**
(`CompiledSourceRef.literal`, sem metacaractere de glob) e seu `.normalized` é exatamente
um `candidate_path`. Um `source_ref` glob que resolve para um único arquivo não conta —
`.literal` é `False`. Um `source_ref` literal que não bate **nenhum** candidato também não
conta — não é "todos os refs são literais", é match contra um candidato específico.

**Teste (reprodução exata):** `candidate_path = "src/new.py"`, inexistente no
`base_commit`. Entrada A (`source_ref = "src/new.py"`, literal exato) contra entrada B
(`source_ref = "src/**"`, glob com proximidade favorável) — domínio, tags e frescor
mantidos **iguais** entre as duas de propósito, para que a vitória de A seja atribuível
só a `exact_literal_match` contra a combinação overlap+proximidade de B, não a outro
sinal variando por coincidência. `test_exact_literal_match_vence_glob_mesmo_sem_existir_no_commit`.
Segundo teste, `test_exact_literal_match_exige_candidato_especifico_nao_so_ref_literal`,
cobre o "não é só ser literal" explicitamente.

### GRUPO D — AUD-005: desempate por microssegundo

`epoch_seconds` (granularidade de segundo) renomeado para `epoch_microseconds`:
`(days*86400 + seconds) * 1_000_000 + microseconds`. Sem isso, duas entradas que só
diferiam em microssegundo empatavam no segundo inteiro e caíam para `entry_id` — que não
tem relação nenhuma com recência. `ScoredEntry.updated_at_epoch` renomeado para
`updated_at_epoch_us` (o nome antigo, mantido, teria escondido a mudança de unidade).
Teste: mesmo score, mesmo segundo civil, microssegundo diferente — a mais recente vence
(`test_desempate_por_microssegundo`).

### GRUPO E — AUD-009: integridade do artifact antes de reaproveitar

`render_context` lia só `target.exists()` antes de decidir não regravar — um arquivo com
o nome certo (hash) mas conteúdo corrompido (cópia interrompida, disco, edição por
engano) passava como "já existe, está correto" e o `rendered_context_hash` registrado
deixava de corresponder ao que estava de fato em disco. `_matches_digest` lê os bytes
reais (`"rb"`, o mesmo cuidado da escrita do outro lado da fronteira IO) e compara o
sha256 deles ao hash esperado; só quando batem a escrita é pulada. Qualquer divergência —
incluindo arquivo ilegível — é tratada como "não existe" e o artifact é regravado.

Teste: escreve o artifact, corrompe os bytes no caminho exato do hash, chama
`render_context` de novo com a mesma seleção — confirma que os bytes em disco voltam a
bater com o hash e que `written=True` na segunda chamada (regravou, não confiou no
`exists()` sozinho). `test_render_context_reescreve_artifact_corrompido`.

### Gates

- **Backend: 1056 passed / 6 skipped** (era 1044; +12 líquido em
  `test_context_router_e5.py`, que foi de 33 para 45 testes — vários dos 33 antigos só
  precisaram de ajuste de API, não são testes novos). `ruff check` · `ruff format --check`
  · `mypy` limpos (75 arquivos).
- `test_architecture.py` verde, incluindo
  `test_context_engine_nao_importa_camadas_superiores` e
  `test_gramatica_de_glob_tem_dono_unico`. Confirmado por análise estática dedicada
  (script AST): nenhum arquivo de `context_engine/` importa `agent_runtime`,
  `tool_executor`, `orchestrator`, `api`, `main`, `fastapi` ou `starlette`.
- **Uma só** definição de `canonical_json`/`canonical_sha256`
  (`app/safety/canonical.py`), confirmado por `grep`. Zero `json.dumps` fora dela em
  `app/`.
- **As nove reproduções rodaram contra a implementação anterior antes da correção**, fora
  do working tree (reconstrução standalone das funções antigas, sem alterar nenhum
  arquivo): confirmado que `hunter2` e `sk-1234567890ABCDEF` vazavam sob a serialização
  JSON-antes-da-redação antiga, e que `café.py` em NFC/NFD colapsava no mesmo
  `canonical_json` sob a ausência de `encode_path_identity`. As reproduções de AUD-005 e
  AUD-009 são confirmadas por construção (o código antigo, lido nesta mesma sessão,
  literalmente não tinha a lógica que os testes novos exigem).

### As três "Pendência conhecida (E5)" nos documentos

Todas as três substituídas por "Fechado na correção de auditoria da E5 (AUD-...)",
apontando para o módulo/função e o teste que fecham cada uma —
`docs/architecture/02-data-model.md` §7 e `docs/architecture/03-context-architecture.md`
§4 (as duas notas de scoring/redação). `git diff` dos dois arquivos: só as três
blockquotes substituídas, nenhuma outra linha tocada.

### Decisões de implementação não 100% especificadas no prompt

1. **`RenderedBlock(text, title)` em vez de `str`.** `render_block_text` precisava expor
   o título redigido separadamente para propagá-lo a `ScoredEntry.title` sem reconstruir
   a divisão de um blob combinado — mudança de assinatura não pedida explicitamente, mas
   necessária para a exigência "o resultado redigido do título substitui o valor cru em
   todo lugar".
2. **Estrutura de `structured` no bloco emitido deixou de ser JSON com cerca de
   markdown** e passou a ser linhas `caminho: valor` diretamente — [02]/[03] não impõem
   schema nenhum ao texto de um bloco (é uma string opaca), e a reformatação de volta
   para JSON depois da redação não tinha como preservar a garantia de "nunca duas
   representações do mesmo valor" sem reintroduzir o bug.
3. **A leitura de verificação para segredo partido usa contagem de redações
   (`text.count(REDACTED)`), não diffing de span.** Uma técnica exata de span-tracking
   sobre `redact()` (que só devolve string, não posições de match) seria
   desproporcional; a contagem é uma heurística conservadora — na dúvida (colada acha
   mais do que a soma das isoladas), redige tudo por inteiro. Mantém o princípio já
   documentado do próprio `redact()`.
4. **`excluded[].path_or_entry` só é pré-codificado quando `reason != budget`** — decisão
   explícita para não misturar um UUID (imune a NFC/NFD) na pré-codificação que existe
   especificamente para identidade de caminho.
5. **`FILE_MAP_VERSION`/`MANIFEST_HASH_VERSION` avançaram para `2`** e
   `RENDERER_VERSION` para `"e5.block.v2"` — nenhum documento fixa esses valores; são
   marcadores de formato ([02] §7, "Versão") para que hashes desta versão não colidam
   com uma versão futura que mude a forma de novo.

### Pendências

- Diff aguardando revisão de Pedro. **Nada commitado.** Segue para segunda rodada do
  Codex, conforme instruído.
- A discrepância sobre a auditoria "E5-AUD-001..009" inexistente no log está registrada
  acima; nenhuma ação adicional tomada além de documentá-la e seguir com a especificação
  técnica.

---

## 2026-09-09 — Codex — auditoria E5 rodada 2

- Veredito: **NÃO GREEN** — E5-AUD2-001..005 (quatro Alta/P1, um Média/P2).
- Relatório completo: [docs/audits/e5-round-2.md](docs/audits/e5-round-2.md).
- Histórico disponível reconstruído em [docs/audits/e5-round-1.md](docs/audits/e5-round-1.md). Nenhum código corrigido; nenhum commit realizado.

## 2026-09-09 — Claude Sonnet 5 (effort medium) — E5 (redesenho documental): redação em três camadas, substituindo a comparação de contagem

Tarefa **SOMENTE DOCUMENTAÇÃO** — reescrita da seção de redação de
`docs/architecture/03-context-architecture.md` §4 e nota nova em
`docs/architecture/04-safety-and-git-runtime.md` §5, documentando o redesenho decidido
nesta sessão em resposta aos cinco findings Alta/Média da rodada 2 de auditoria da E5
(`docs/audits/e5-round-2.md`, E5-AUD2-001..005). `docs/` alterado com autorização
explícita de Pedro nesta conversa, escopo restrito aos dois arquivos + este log.

**Discrepância sinalizada:** o prompt pedia para ler "a decisão de redesenho desta
sessão" no `AGENT_LOG.md` — não há entrada de planejamento registrada; a última entrada
antes desta é o resultado da auditoria (rodada 2, NÃO GREEN). A especificação técnica do
redesenho (as três camadas, o invariante, o fallback) veio completa e detalhada no
próprio prompt desta tarefa, então segui ela diretamente — não há decisão anterior
faltando para o trabalho, só a entrada de log que a nomeasse como tal.

Também note-se: `safety.is_sensitive_key`/`safety.detect_secret_spans`, citadas na nova
seção e na nota do §5, **ainda não existem em código** — esta tarefa é só documentação
do redesenho pretendido; implementá-las é trabalho de uma próxima sessão de código.

### Arquivos alterados

- `docs/architecture/03-context-architecture.md` §4 — **reescrita** (não só adição) do
  bloco sobre redação no renderizador:
  - **Removido:** o parágrafo "Ordem da redação em relação à formatação estrutural"
    (versão anterior, que só afirmava "roda sobre representação plana", sem mecanismo);
    o blockquote "Pendência conhecida (E5)" correspondente (já obsoleto — a rodada 1
    achou fechado, a rodada 2 achou reaberto); e o parágrafo "Fechado na correção de
    auditoria da E5 (AUD-002/003/004)" que descrevia a abordagem de **comparação de
    contagem de redações** (leitura colada vs. soma de leituras isoladas) —
    abandonada porque `E5-AUD2-001` provou que ela não isola a fronteira certa quando
    há um campo adicional ou uma folha interposta.
  - **Adicionado:** a descrição das três camadas independentes — estrutural
    (`is_sensitive_key` marca subárvore inteira, chave insegura vira marcador fixo,
    nunca reconstrução parcial — fecha `E5-AUD2-003`/`004`), posicional
    (`detect_secret_spans` sobre uma projeção plana única, com mapa de posição de volta
    — fecha `E5-AUD2-001`), e propagação (valor já comprovado sensível é redigido em
    toda ocorrência literal, escalares genéricos excluídos do conjunto — fecha
    `E5-AUD2-002`). Mais o invariante ("nenhum dado autoral cru reaparece em nenhuma
    superfície emitida", framing depois da redação, medição depois do framing final —
    isto último fecha `E5-AUD2-005`/reabertura de `AUD-007`) e o fallback fail-closed
    (estrutura não redigível com prova suficiente → redige por inteiro ou recusa,
    nunca melhor esforço).
  - A seção de `exact_literal_match` (linhas ~245–255) aparece no `git diff` mas **não
    foi tocada nesta tarefa** — é herança não commitada da sessão anterior (correção
    dos 9 findings da rodada 1), que trocou o blockquote "Pendência conhecida" daquele
    sinal por "Fechado na correção". Ver a seção "Gate" abaixo para a distinção exata.
- `docs/architecture/04-safety-and-git-runtime.md` §5 — nota breve logo após a tabela
  de três camadas, na linha da Camada 3 (Saída): `safety/redaction.py` expõe, além de
  `redact(text)`, `is_sensitive_key(key)` e `detect_secret_spans(text)` — mesmo motor,
  reaproveitado pelo Context Engine; nenhuma segunda lista/motor de regex.
- `AGENT_LOG.md` — esta entrada.

### Decisões tomadas: nenhuma nova de arquitetura

Formalização documental do redesenho especificado no prompt desta sessão. As três
camadas, o invariante e o fallback são exatamente os cinco pontos do prompt, em prosa
consistente com o estilo do documento e ligados aos IDs de finding que cada um fecha.

### GATE

`git diff` dos dois arquivos colado na conversa. Resumo:

- `docs/architecture/03-context-architecture.md`: dois hunks. O primeiro
  (`exact_literal_match`, linhas ~245–255) é herança não commitada da sessão anterior,
  não tocada aqui. O segundo é o trabalho desta tarefa: substitui dois parágrafos +
  um blockquote pela descrição das três camadas + invariante + fallback + referência
  cruzada para `04` §5.
- `docs/architecture/04-safety-and-git-runtime.md`: um hunk, só adição — quatro linhas
  novas depois da tabela de três camadas de proteção de segredos, nenhuma linha
  removida ou alterada.

### Pendências

- Diff aguardando revisão de Pedro. **Nada commitado.**
- `is_sensitive_key`/`detect_secret_spans` documentadas mas **não implementadas** —
  próxima sessão de código precisa: (1) criá-las em `safety/redaction.py`; (2)
  redesenhar `render_block_text` para as três camadas descritas; (3) rodar a suíte de
  ataques da rodada 2 (`E5-AUD2-001..005`) como regressão; (4) terceira rodada de
  auditoria antes de qualquer commit.

---

## 2026-09-10 — Claude Opus 5 (effort high) — E5: pipeline de redação em três camadas (implementação)

Implementa o redesenho normativo de [03](docs/architecture/03-context-architecture.md) §4,
fechando `E5-AUD2-001..005` da segunda auditoria. A abordagem de **contagem de matches**
foi removida, não ajustada. **Nada commitado** — segue para a terceira rodada do Codex.

- **Relatório técnico completo:**
  [docs/audits/e5-round-3-implementation-notes.md](docs/audits/e5-round-3-implementation-notes.md)
  — decisões não fixadas pela especificação, limiares, custo/tetos, limites residuais
  declarados e o desvio de §2.5. Este log aponta para lá em vez de duplicar.

- Arquivos alterados:
  - `api/app/safety/redaction.py` — motor único: `detect_secret_spans` (cascata simulada
    com mapa de volta para coordenadas do texto original), `is_sensitive_key`,
    `SecretSpan`, `merge_spans`. `redact()` virou wrapper fino e continua **byte a byte
    idêntico** (7.214 casos, 0 divergências contra a implementação anterior).
  - `api/app/context_engine/rendering.py` — as três camadas (estrutural, posicional,
    propagação), fail-closed, NFC final. `RENDERER_VERSION` → `e5.block.v3`.
  - `api/tests/test_context_redaction_e5_round3.py` — **novo**, 24 testes adversariais.
  - `api/tests/test_context_router_e5.py` — o teste de medição que a rodada 2 apontou como
    insuficiente foi substituído por um que lê os **bytes gravados** do artifact.
  - `api/tests/test_architecture.py` — duas travas novas de dono único da detecção.

- Gates: **1080 passed / 6 skipped** (era 1056). `ruff` · `ruff format --check` · `mypy`
  limpos. As 10 reproduções da rodada 2 confirmadas **falhando** contra a implementação
  anterior e passando contra esta.

- **Ponto que precisa de decisão de Pedro:** §2.5 do relatório. O texto normativo de
  [03] §4 diz que `detect_secret_spans` roda "uma única vez" sobre a projeção;
  implementado literalmente isso **não fecha** `E5-AUD2-001b` (folha interposta). A
  implementação faz quatro leituras que alimentam um único conjunto de marcas, sem
  nenhuma comparar quantidade com outra. Não alterei `docs/architecture/` — sem
  autorização nesta tarefa.

- Pendências: terceira rodada do Codex. Nada commitado.

---

## 2026-09-10 — Codex — auditoria E5 rodada 3

- Veredito: **NÃO GREEN** — E5-AUD3-001..003 (uma Alta/P1 e duas Média/P2).
- Relatório completo: [docs/audits/e5-round-3.md](docs/audits/e5-round-3.md). Recomendação do Foco 2: ajustar a implementação; decisão de política/documentação reservada a Pedro.
- 168 regressões passaram; 10 provas independentes executadas. Nenhuma correção de código ou arquitetura; nenhum commit.
## 2026-09-10 — Claude Sonnet 5 (effort medium) — E5 §4: formalização do mecanismo posicional e do trade-off de falso positivo

Tarefa **SOMENTE DOCUMENTAÇÃO** — `docs/architecture/03-context-architecture.md` §4 (seção
do redesenho de três camadas) e este log. Formaliza decisões já tomadas nesta sessão em
resposta à [rodada 3 de auditoria](docs/audits/e5-round-3.md); não é decisão nova.

- Arquivos alterados: `docs/architecture/03-context-architecture.md`, `AGENT_LOG.md`.

- **O que foi substituído em §4:**
  - O texto normativo da camada posicional que dizia "`safety.detect_secret_spans` roda
    **uma única vez** sobre essa projeção". A implementação real (rodada 3) faz **várias
    leituras** — fragmento isolado, linha `caminho: valor`, pares ordenados de fragmentos
    colados, projeção canônica inteira — todas alimentando **um único conjunto monotônico
    de marcas**. A garantia normativa passou a ser: *nenhuma leitura decide com base em
    contagem relativa a outra* (era a comparação `glued_hits > isolated_hits` que as
    rodadas 1 e 2 reprovaram), não "uma única chamada".

- **O que foi adicionado em §4:**
  - Parágrafo **"Trade-off aceito na V1 — falso positivo entre pares de fragmentos"**:
    a leitura de pares pode redigir conteúdo legítimo quando duas strings públicas sem
    relação formam, coladas, uma sequência que bate um padrão (`E5-AUD3-001`). A V1
    aceita isso deliberadamente — falso positivo de redação é preferível a falso negativo
    (credencial real no artifact, que entra no `execution_fingerprint`). Registrado
    explicitamente que **não** se restringe a pares adjacentes nem se adiciona heurística
    de confiança/comprimento, porque qualquer das duas reabriria `E5-AUD2-001b`.
  - Parágrafo **"Explicabilidade planejada"**: redação por detecção cross-fragment será
    registrada no metadado `transformations` já existente, sem expor o valor — só para
    explicar, no futuro, por que um bloco veio redigido sem campo isolado com segredo.

- **Pendências / concern sinalizado:** esta formalização cobre **só** a dimensão de
  trade-off de `E5-AUD3-001` (over-redaction por pares). A rodada 3 também encontrou:
  - **`E5-AUD3-002` (Alta/P1)** — vazamento real: um prefixo preservado (`Bearer `,
    `password: `) em outro fragmento faz o motor reconhecer o segredo, mas o span cobre
    só o valor a substituir, então `span.start < boundary < span.end` é falso e o
    renderer **descarta a detecção**. O valor completo permanece nos bytes do artifact.
    Isso é **bug de consumo de span no renderer**, não trade-off de política — precisa de
    correção de código (`rendering.py`), não de documentação.
  - **`E5-AUD3-003` (Média/P2)** — `_propagation_values` consulta `propagatable` ao
    adicionar fragmento inteiro, mas **não** ao adicionar recorte de span local; um
    número ≥6 dígitos sob chave sensível entra na propagação global por essa segunda
    porta e apaga contagens públicas iguais. Também bug de código.
  - Nenhum dos dois é fechado por esta tarefa. A rodada 3 continua **NÃO GREEN** até
    `E5-AUD3-002` e `E5-AUD3-003` serem corrigidos no código.

- Nada commitado.

---

## 2026-09-11 — Claude Opus 5 (effort high) — E5: correção de `E5-AUD3-002`/`E5-AUD3-003` + marcador de explicabilidade

Fecha os dois defeitos de código da [rodada 3](docs/audits/e5-round-3.md) e acrescenta o
marcador de explicabilidade que [03] §4 já documentava como planejado. `E5-AUD3-001`
**não** foi corrigido — é o trade-off aceito e commitado em `9793c1a`. **Nada commitado**;
segue para a quarta rodada do Codex.

- **Relatório técnico completo:**
  [docs/audits/e5-round-4-implementation-notes.md](docs/audits/e5-round-4-implementation-notes.md)
  — inclui a decisão de §1.1 (manter marca inteira em vez de recorte cirúrgico) e o erro
  que eu mesmo cometi na primeira versão da varredura de bytes.

- Arquivos alterados:
  - `api/app/safety/redaction.py` — `Interval` novo; `SecretSpan` passou a ter
    `recognition_span` (match completo, com o contexto que prova a detecção) e
    `replacement_span` (só o que vira `«redigido»`). `merge_spans`/`redact()` usam o
    segundo e continuam **byte a byte idênticos** (7.552 casos, 0 divergências).
  - `api/app/context_engine/rendering.py` — travessia de fronteira decidida por
    `recognition_span` (fecha `E5-AUD3-002`); `propagatable` checado antes das **duas**
    portas da propagação (fecha `E5-AUD3-003`); `CROSS_FRAGMENT_REDACTION` em
    `transformations`, que passou a sair do renderizador. `RENDERER_VERSION` → `e5.block.v4`.
  - `api/app/context_engine/selection.py` — consome `rendered.transformations` em vez de
    montar a lista por conta própria.
  - `api/tests/test_context_redaction_e5_round4.py` — **novo**, 35 testes (13 diretos +
    22 cenários de varredura de bytes parametrizados).
  - `api/tests/test_context_redaction_e5_round3.py` — 2 testes novos sobre a separação dos
    dois intervalos; o teste que usava a API antiga foi atualizado.

- Gates: **1119 passed / 6 skipped** (era 1080). `ruff` · `ruff format --check` · `mypy`
  limpos. Os dois defeitos reproduzidos contra a lógica anterior, simulada fora do working
  tree, antes de confirmar a correção.

- **Nota sobre a varredura de bytes:** a primeira versão que escrevi falhou — e estava
  errada, não o pipeline. A maioria dos valores sintéticos das auditorias (`hunter2`,
  `7890ABCDEF`, `ABCDEFGHIJKLMNOP`) **não** é reconhecível por padrão nenhum isoladamente;
  eles são segredo por estarem sob chave sensível, por serem cópia de um valor que está,
  ou por completarem um padrão ao concatenar. Ao varrê-los fora dessa condição, eu testava
  a afirmação errada. Reescrita como tabela de 22 cenários, cada um preservando a condição
  que torna seu valor segredo. Detalhe no §4 do relatório.

- Pendências: quarta rodada do Codex. Nada commitado.

---

## 2026-09-11 — Codex — auditoria E5 rodada 4

- Relatório completo: [docs/audits/e5-round-4.md](docs/audits/e5-round-4.md).
- Veredito: **NÃO GREEN** — E5-AUD4-001..003: cauda do segredo partido emitida (limite conhecido, contrário ao Foco 2), prefixo preservável apagado e vazamento novo por contexto de URL em lookaround. E5-AUD3-002/003 fechados nas reproduções exatas; E5-AUD3-001 respeitado como política aceita.
- Suíte existente: 1117 aprovados/8 pulados no agregado da execução completa + repetição dos dois testes bloqueados pelo sandbox; ruff/formatação/mypy passaram. Provas independentes: 12 passaram/3 asserts de requisito falharam; complemento histórico com 2 aprovados.
- Arquivos alterados somente: este log e o relatório. Nenhuma correção de código ou arquitetura; nenhum commit. Liberação de commit bloqueada pelos findings.

---

## 2026-09-12 — Claude Sonnet 5 (effort medium) — docs: normativa dos dois intervalos e dos limites de over-redaction (E5-AUD4-001/002/003)

Tarefa **somente de documentação**, depois da [rodada 4](docs/audits/e5-round-4.md)
(**NÃO GREEN**). Formaliza em [03](docs/architecture/03-context-architecture.md) §4 o que
a correção de código ainda por vir deve satisfazer. **Nenhum código alterado, nada
commitado.**

- Arquivos alterados:
  - `docs/architecture/03-context-architecture.md` — três adições, todas em §4:
    1. **camada posicional** — `recognition_span` e `replacement_span` são calculados
       juntos no motor único (`safety.detect_secret_spans`) para **todo** padrão do
       catálogo, incluindo os de lookbehind/lookahead (`url_credentials`);
       `recognition_span` cobre também o texto consultado por asserções de largura zero.
       Nenhum padrão recebe tratamento especial fora de `safety/redaction.py`
       (`E5-AUD4-003`);
    2. **trade-off aceito na V1** — `_is_greedy_extension` removida (`E5-AUD4-001`): o
       sinal que ela usava não distingue extensão gulosa de continuação genuína sem
       heurística de confiança/comprimento, que a V1 recusa. Consequência aceita:
       conteúdo adjacente legítimo passa a ser redigido;
    3. **limite dessa aceitação** — a emissão cross-fragment projeta a **interseção** de
       `replacement_span` com cada fragmento, nunca o fragmento inteiro; rótulo
       preservável fora da interseção nunca é apagado (`E5-AUD4-002`).
  - `AGENT_LOG.md` — esta entrada.

- Decisões tomadas:
  - O item 3 foi para a seção do trade-off, colado ao item 2, porque os dois formam um
    par: o primeiro declara que over-redaction é aceitável, o segundo declara onde ela
    **para**. Separá-los deixaria a aceitação sem limite escrito.
  - Isso **reverte a decisão de §1.1** das
    [notas da rodada 4](docs/audits/e5-round-4-implementation-notes.md) (manter marca
    inteira em vez de recorte por interseção), que a auditoria recusou em `E5-AUD4-002`.
    A norma agora exige a projeção por interseção.

- Pendências:
  - **Nada disso está implementado.** `_is_greedy_extension` ainda existe em
    `api/app/context_engine/rendering.py`, a emissão cross-fragment ainda usa `whole`, e
    `recognition_span` ainda não representa lookaround. A norma foi escrita primeiro,
    de propósito: os três findings da rodada 4 seguem **abertos**.
  - Nada commitado. Commit desta documentação depende de autorização do Pedro.

---

## 2026-09-12 — Claude Opus 5 (effort high) — E5: fecha `E5-AUD4-001/002/003`

Os três findings da [rodada 4](docs/audits/e5-round-4.md), que era **NÃO GREEN**. Implementa
a norma escrita em [03](docs/architecture/03-context-architecture.md) §4 e commitada em
`8f59cf7` — a documentação veio primeiro, de propósito. **Nada commitado**; segue para a
quinta rodada do Codex.

- **Relatório técnico completo:**
  [docs/audits/e5-round-5-implementation-notes.md](docs/audits/e5-round-5-implementation-notes.md)
  — inclui a revisão padrão a padrão do catálogo, por que `\b` ficou de fora, e três
  limites que **esta** rodada piorou (§5).

- Arquivos alterados:
  - `api/app/safety/redaction.py` — `_PATTERNS` virou tupla de `_Pattern`, onde cada padrão
    declara `prefix_preserving`, `lookbehind` e `lookahead`. `_recognition_window` amplia
    `recognition_span` com o texto lido por lookaround positivo (fecha `E5-AUD4-003`).
    `_validate_patterns` roda **no import** e recusa padrão com `(?<=`/`(?=` sem declaração.
  - `api/app/context_engine/rendering.py` — `_is_greedy_extension` e `own_starts`
    **removidos**, sem substituta (fecha `E5-AUD4-001`). `_clip` projeta a interseção de
    `replacement_span` com cada fragmento; travessia não marca mais fragmento inteiro
    (fecha `E5-AUD4-002`). `RENDERER_VERSION` → `e5.block.v5`.
  - `api/tests/test_context_redaction_e5_round5.py` — **novo**, 44 testes.
  - `api/tests/test_context_redaction_e5_round3.py` —
    `test_segredo_no_fim_do_corpo_nao_apaga_o_titulo` **mudou de sinal** e virou
    `..._consome_o_titulo_vizinho`: a over-redaction agora é o comportamento afirmado.
  - `AGENT_LOG.md` — os dois links `../api/app/…` corrigidos para `api/app/…`.

- Decisões tomadas:
  - A trava do lookaround é **`ImportError` no boot**, não teste: quem acrescenta um padrão
    novo não é quem lê a docstring do módulo.
  - A cascata histórica de `redact()` foi escrita **dentro do teste, com os fontes dos
    regex literais** — assim o GATE 1 trava também o catálogo, e o ensaio deixa de ser um
    script de scratchpad (crítica justa do auditor nas rodadas 3 e 4).
  - Os 22 cenários da varredura de bytes são **importados** do arquivo da rodada 4, não
    copiados; somados aos 7 novos, a varredura cobre 29.

- Gates: **1163 passed / 6 skipped (era 1119/6)**. `ruff` · `ruff format --check` · `mypy` limpos (76 arquivos).
  GATE 1: 0 divergências em 5.800+ casos. GATE 2: 29 cenários, cada um sozinho e todos
  juntos num arquivo físico, com `sha256` conferido.

- Pendências: quinta rodada do Codex. Nada commitado. `E5-AUD3-001` segue como política
  aceita, não corrigida.

---

## 2026-09-12 — Codex — auditoria independente E5, rodada 5

- Relatório completo: [docs/audits/e5-round-5.md](docs/audits/e5-round-5.md).
- **GREEN técnico sob a política V1.** E5-AUD4-001/002/003 fechados nas quatro
  reproduções exatas, inclusive bytes do artifact e títulos de metadados.
- **E5-AUD5-001, Média/P2, risco não bloqueante:** recorte público de seis caracteres
  apagou 100 cópias distantes sem spans locais. Recomendação técnica: aceitar a
  amplificação e manter a propagação; contraprova executada mostra que excluir os
  recortes perde proteção de cópia de segredo genuinamente partido. Não substitui
  a decisão de produto de Pedro.
- Validação: **1163 passed / 6 skipped**, 15 provas independentes aprovadas;
  Ruff, formatação e mypy limpos. Diferencial contra o HEAD histórico: **5.564 strings
  únicas**, zero divergências UTF-8; essa é a contagem medida do corpus descrito
  anteriormente como 5.800+. Quatro imports inválidos recusados em subprocess.
- Somente relatório e esta entrada alterados pela auditoria no repositório.
  Nenhuma correção de código, mudança de norma ou commit.

---

## 2026-09-12 — Claude Sonnet 5 (effort medium) — docs: registra E5-AUD5-001 como risco residual aceito

Tarefa **somente de documentação**, depois da [rodada 5](docs/audits/e5-round-5.md)
(**GREEN** para a política V1 documentada, com um risco novo classificado Média/P2 e
recomendação explícita de aceitação). Formaliza em
[03](docs/architecture/03-context-architecture.md) §4 o registro de `E5-AUD5-001`.
**Nenhum código alterado, nada commitado.**

- Arquivos alterados:
  - `docs/architecture/03-context-architecture.md` — novo bloco em §4, logo após o
    parágrafo de `E5-AUD4-002`, registrando `E5-AUD5-001`: um recorte curto
    (6+ caracteres) nascido de fronteira entre fragmentos pode entrar na propagação
    global e redigir toda ocorrência literal daquele texto **na mesma entrada/bloco**,
    mesmo sendo conteúdo público — confirmado por auditoria com uma frase repetida 100
    vezes redigida nas 100 ocorrências. Aceito pela mesma prioridade de `E5-AUD3-001`/
    `E5-AUD4-001` (falso positivo aceitável, falso negativo não); excluir os recortes da
    propagação perderia proteção real de cópias de segredo genuinamente partido,
    também confirmado por auditoria. O raio de propagação é limitado à própria
    entrada/bloco. Registra também um marcador de diagnóstico planejado,
    `propagated_secret_redaction`, separado de `CROSS_FRAGMENT_REDACTION` — sem valor,
    substring, posição ou hash reversível, só o fato (e, se o formato suportar, uma
    contagem) de que a propagação foi acionada.
  - `AGENT_LOG.md` — esta entrada.

- Decisões tomadas:
  - O bloco foi para logo depois do parágrafo de `E5-AUD4-002` — o limite da interseção
    — e antes de "Explicabilidade planejada", porque `E5-AUD5-001` é um risco residual
    da própria camada de propagação, não uma explicação do mecanismo de detecção
    cross-fragment que o parágrafo seguinte já cobre.
  - O marcador `propagated_secret_redaction` é registrado como **norma**, não como
    implementação — o mesmo padrão que `CROSS_FRAGMENT_REDACTION` seguiu em
    [03 §4] antes de existir em código. Nada em `rendering.py` foi tocado nesta tarefa.

- Pendências:
  - **Nada disso está implementado.** `transformations` ainda só conhece
    `CROSS_FRAGMENT_REDACTION`; não há marcador de propagação nem contagem de
    substituições no `RenderedBlock` atual.
  - Nada commitado. Commit desta documentação depende de autorização do Pedro.

---

## 2026-09-12 — Claude Sonnet 5 (effort medium) — E5: marcador `PROPAGATED_SECRET_REDACTION` (`E5-AUD5-001`)

Depois da [rodada 5](docs/audits/e5-round-5.md) (**GREEN** técnico; `E5-AUD5-001`
classificado Média/P2 e recomendado para aceitação). Acrescenta um segundo marcador de
diagnóstico a `blocks[].transformations`, conforme a norma já commitada em
[03](docs/architecture/03-context-architecture.md) §4. **Só sinalização** — nenhuma
lógica de detecção, propagação ou redação foi tocada. **Nada commitado**; segue para
uma rechecagem curta do Codex.

- **Relatório técnico completo:**
  [docs/audits/e5-round-5-metadata-extension-notes.md](docs/audits/e5-round-5-metadata-extension-notes.md)
  — inclui por que a contagem embutida (`"propagated_secret_redaction:3"`) foi
  descartada, e um fixture que quase virou "bug" mas era a gulodice de alfabeto já
  aceita em `E5-AUD4-001`.

- Arquivos alterados:
  - `api/app/context_engine/rendering.py` — `PROPAGATED_SECRET_REDACTION` novo, ao lado
    de `CROSS_FRAGMENT_REDACTION`. `_redacted_text` passou a devolver
    `tuple[str, bool]`: o texto é o mesmo de sempre, o booleano só relata se o laço de
    propagação alterou aquele fragmento. `render_block_text` acrescenta o marcador
    quando qualquer fragmento do bloco relatou `True`. `RENDERER_VERSION` **não**
    avançou (o texto não mudou) — comentário registrando que `transformations` entra no
    payload hasheado, então `rendered_context_hash` de blocos com propagação muda mesmo
    sem bump de versão.
  - `api/tests/test_context_redaction_e5_round5_metadata.py` — **novo**, 13 testes:
    presença/ausência do marcador nos quatro cenários pedidos, os dois marcadores
    coexistindo, prova de que nenhuma string de `transformations` contém segredo do
    fixture (nas duas direções), e reconstrução independente do texto sem o booleano
    para provar que nada além do metadado mudou.

- Decisões tomadas:
  - Sem contagem no marcador: uma string como `"propagated_secret_redaction:3"`
    quebraria o idioma `MARCADOR in transformations` que a suíte inteira já usa para
    `CROSS_FRAGMENT_REDACTION` (`in` e igualdade exata de lista), trocando comparação
    simples por parsing em todo consumidor futuro — o ganho de diagnóstico não pareceu
    justificar isso.

- Gates: suíte de redação/determinismo das 5 rodadas (rounds 3, 4, 5, 5-metadata,
  router) **132 passed**. Suíte completa: **1176 passed / 6 skipped (era 1163/6)**. `ruff` · `ruff format --check` ·
  `mypy` limpos.

- Pendências: rechecagem curta do Codex. Nada commitado. `E5-AUD5-001` segue como
  política aceita — o marcador é diagnóstico, não correção.

---

## 2026-09-12 — Claude Sonnet 5 (effort low) — E5: `RENDERER_VERSION` → `e5.block.v6`

Fecha a lacuna registrada no §6 das
[notas da extensão de metadata](docs/audits/e5-round-5-metadata-extension-notes.md):
`PROPAGATED_SECRET_REDACTION` entra em `transformations`, que participa do payload
hasheado ([02] §5), então o bump segue o mesmo precedente já estabelecido nesta fase
(`v1`→`v2` na troca de formato de `structured`, `v4`→`v5` na correção de
`E5-AUD4-002`): qualquer mudança que afete o payload hasheado avança a constante, mesmo
sem o texto visível mudar. **Nada commitado.**

- Arquivos alterados:
  - `api/app/context_engine/rendering.py` — `RENDERER_VERSION` = `"e5.block.v6"`.
    Comentário reescrito: a nota "**não** avançou esta constante" saiu (não é mais
    verdade); entrou a razão do bump e a referência ao precedente `v1`→`v2`.

- Verificação:
  - Nenhum teste fixava a string `"e5.block.v5"` (ou qualquer versão anterior) como
    valor esperado — todos os consumidores (`manifest.py`, `test_context_router_e5.py`)
    leem a constante `RENDERER_VERSION`, nunca o literal. Confirmado por busca antes de
    editar; nenhum teste precisou de ajuste.
  - Suíte completa: **1176 passed / 6 skipped**, igual à contagem anterior — o bump não
    adiciona nem remove teste nenhum, só muda o valor gravado no manifest.
  - `ruff` · `ruff format --check` · `mypy` limpos.

- Pendências: nenhuma pendência nova. `E5-AUD5-001` segue como política aceita; o
  marcador é diagnóstico, não correção. Nada commitado.

---

## 2026-09-12 — Codex — E5: rechecagem final de metadata e versão

- Relatório: [docs/audits/e5-round-final.md](docs/audits/e5-round-final.md).
- **GREEN — E5 pronta para commit e push.** Marcador de propagação presente nas 100
  cópias, ausente sem propagação, coexistindo em ordem fixa com o de travessia.
  Inspeção e ataques não encontraram dado sensível novo na metadata.
- `e5.block.v6` confirmado. Diferencial de 31 entradas: texto integral UTF-8 idêntico
  ao comportamento anterior reconstruído; só marcador/versão diferem no payload.
- Suíte completa: **1176 passed / 6 skipped**, 532,31 s. Nove regressões direcionadas
  dos findings Alta aprovadas; nove provas independentes aprovadas no agregado.
- Fechamento: **5 rodadas + rechecagem final; 19 findings corrigidos**. E5-AUD3-001 é
  política aceita; E5-AUD5-001 é risco residual aceito em [03 §4](docs/architecture/03-context-architecture.md),
  limitado à mesma entrada/bloco e agora sinalizado, não corrigido.
- Somente relatório e esta entrada alterados no repositório. Nenhum código corrigido,
  commit ou push executado.

---

## 2026-09-12 — Claude Sonnet 5 (effort: medium) — Correção de terminologia: tool_profile_hash e fallback de enriquecimento

- Arquivos alterados:
  - `docs/architecture/02-data-model.md` — dois comentários sobre `tool_profile_hash`
    reescritos para distinguir os dois momentos do mesmo campo conceitual: o hash no
    `execution_fingerprint` da task é o perfil de capability REQUERIDO/APROVADO,
    declarado e congelado na aprovação (E6), nunca provado naquele momento; o hash no
    `Run` é o perfil EFETIVAMENTE COMPROVADO pelo adaptador naquele Run específico
    (E7+, quando `effective_capabilities` — 05 — é declarado e a guarda
    approved→executing exige satisfação exata, fail-closed em divergência).
  - `docs/architecture/03-context-architecture.md` §5 (Task Analyzer) — parágrafo
    sobre falha/timeout do provider de análise estendido: ausência, falha ou timeout
    do enriquecimento (incluindo nenhuma porta de enriquecimento implementada)
    também eleva o piso de risco (`risk = max(hard_rule_risk, medium)`,
    `complexity = max(hard_rule_complexity, medium)`), mantendo `risk_source =
    hard_rule` — é fallback determinístico, não segunda fonte de risco.
- Decisões tomadas: nenhum hash novo criado; apenas terminologia clarificada para
  refletir que o mesmo campo (`tool_profile_hash`) representa coisas distintas em
  momentos distintos do ciclo de vida (aprovado vs. comprovado).
- Pendências: nenhuma. Tarefa somente-documentação, gate de diff já revisado.
  Nada commitado — aguardando autorização do Pedro.

---

## 2026-09-12 — Claude Opus 5 (effort: high) — E6: Orchestrator Planner

Analyzer, Resource Router, Planner, máquina de estados, endpoints e Task Detail.
**Não commitado, não pushado.** Vai para auditoria independente do Codex.

- Arquivos alterados:
  - **Novos** — `api/app/orchestrator/` (10 arquivos: `analyzer`, `resource_router`,
    `planner`, `execution_manager`, `state_machine`, `fingerprint`, `purge`, `errors`,
    `__init__`), `api/app/safety/test_policy.py`, `api/app/workspace/policy.py`,
    `api/app/api/tasks.py`, `api/migrations/versions/0002_workspace_test_config.py`,
    `src/services/tasksApi.ts`, `src/pages/WorkspaceTasks.tsx`, e 5 arquivos de teste
    (`test_orchestrator_{analyzer,router_and_fingerprint,state_machine,planner}.py`,
    `test_api_tasks.py`, `src/test/task-detail-ui.test.tsx`).
  - **Modificados** — `context_engine/source_ref_expansion.py` (+`compile_classification_pattern`
    / `build_classification_matcher`), `db/models.py` (+`DevWorkspace.test_config`),
    `api/workspaces.py` (`PATCH` aceita `test_config`), `main.py` (router de tasks,
    handler de `OrchestratorError` com payload estruturado, lifespan com
    `reconcile_on_startup`), `workspace/{service,errors,purge,purge_tokens,__init__}.py`,
    `tests/test_architecture.py` (+4 regras), e o frontend (`WorkspaceDetail`,
    `workspaceApi`, 2 fixtures de teste).

- Inspeção obrigatória, os três pontos:
  1. **`freeze_manifest` já chama `render_context`** (`manifest.py:323`). O Planner faz
     exatamente **duas** chamadas — `select_context` e `freeze_manifest`.
  2. **Hard rules não podem usar `build_matcher`.** O envelope (`validate_source_ref`)
     aplica a denylist de segredos sobre o padrão tratado como literal, e a tabela de
     [03] §5 **é** essa denylist (`.env*`, `secrets/**`, `*.pem`, `*.key`). Compilá-las
     por lá negaria a regra mais importante do Analyzer — *fail-open* sobre risco. Função
     nova no **dono da gramática**: `compile_classification_pattern` /
     `build_classification_matcher` em `context_engine/source_ref_expansion.py`. Mesma
     gramática (`compile_source_ref`), mesma normalização, **sem** envelope.
     `orchestrator/` não implementa casamento próprio.
  3. **`preflight(local_path) -> GitPreflight`** confirmado; chamado uma única vez, no
     início do planejamento.

- Decisões de implementação **não** 100% especificadas (para a auditoria):
  1. **`candidate_paths` vêm do payload de `POST /plan`**, explícitos e opcionais. [06] §2
     não define a origem. Tokenizar o objetivo em prosa faria toda palavra que não é
     caminho virar `excluded(out_of_workspace)` no manifest — e o `manifest_hash` é
     comparável entre tasks por desenho. Lista vazia é o caso normal.
  2. **`plan_hash = sha256(canonical_json(plan))`** sobre o documento inteiro, não um
     subconjunto: qualquer mudança no plano que o humano leu invalida a aprovação.
  3. **`TestPolicy` mora em `safety/`**, não em `orchestrator/`. [04] §5 e [01] §2 a listam
     entre as peças de `safety/`. É o que deixa `workspace/` (grava) e `orchestrator/`
     (consome) compartilharem um schema só — `workspace/ → orchestrator/` não é aresta
     permitida. Tentei primeiro pôr em `orchestrator/` com import local em `workspace/`;
     isso contornava o `test_architecture.py` em vez de respeitá-lo, e foi desfeito.
  4. **`test_config` entra no `PATCH /workspaces/{id}` existente**, não em rota dedicada:
     [06] §2 fecha a tabela de rotas e criar uma exigiria mudar `docs/`. "Omitido" vs.
     "`null` explícito" é distinguido por `model_fields_set` do Pydantic — nenhum segundo
     sentinela `UNSET` nasceu.
  5. **`network_policy` só aceita `"unrestricted"`.** [04] §6 declara a política de rede sem
     impô-la; aceitar `"disabled"` gravaria no fingerprint a afirmação de uma propriedade
     que nenhum código faz valer.
  6. **`PurgeTokenStore` reaproveitado, com sujeito namespaced** (`workspace:<id>` /
     `task:<id>`). Sem o prefixo, só um `workspace_id` nunca coincidir com um `task_id`
     separaria os dois — verdade prática, invariante não imposta.
  7. **Padrões de hard rule ganharam variantes `**/`** (`**/migrations/**`,
     `**/alembic/**`, `**/.github/workflows/**`). A tabela de [03] §5 supõe repositório de
     projeto único; neste monorepo as migrations vivem em `api/migrations/`, e o padrão
     ancorado na raiz não as alcançava. Pego por teste, não por revisão.
  8. **Coluna de override de política de segurança NÃO foi criada.** [02] §1 congela os
     campos de `DevWorkspace` e não tem esse campo; só `test_config` foi autorizado nesta
     conversa. `resolve_effective_policy` recebe o override por parâmetro e o mecanismo
     real de [04] §5 (`SafetyPolicy.compose`) é exercitado em teste.
  9. **`developer_binding` sai `null`** pelo mesmo motivo que [02] §7 dá para o auditor:
     omitir a chave faria "sem developer" e "developer X" colidirem.
  10. **`reconcile_on_startup` roda no lifespan do servidor**, não no `create_app` (que
      segue sem efeito colateral). Tolera `OperationalError` — e só ele: banco ainda não
      migrado é estado real, e falhar o startup impediria o operador de rodar a migration.
  11. **`approved_manifest_id`/`approved_fingerprint` são preenchidos já no plano**, com
      `approved_at = NULL`. `approved_at` é a única fonte de verdade de "aprovada"; os
      outros três são o **candidato** até lá.
  12. **`WorkspaceApiError` ganhou `details`** no frontend: o transporte descartava o corpo
      do 409, e [06] §2 exige que ele carregue "qual campo divergiu".
  13. **A trilha de auditoria é commitada antes de o 409 subir** (`_commit_audit_trail`).
      Defeito encontrado em self-review e **confirmado por sonda**: `session_scope` faz
      `rollback` em qualquer exceção, então o `SafetyEvent(approval_invalidated)` de
      [ADR-0008] regra 5 e a atualização do fingerprint eram descartados assim que a
      exceção atravessava a camada HTTP. Os testes de serviço não pegavam — eles usam a
      sessão crua, sem a unidade de trabalho da API. A invalidação **é** a transição
      `awaiting_approval → awaiting_approval` de [02] §4, e commitar é papel deste módulo
      ([01] §2: "dono da transação"). Mesmo tratamento na guarda de entrada. Duas
      regressões de ponta a ponta em `test_api_tasks.py` fecham o caso.
  14. **`test_backend_nao_imprime_nada` passou a ser por AST.** Ele era
      `"print(" not in source`, e `_recompute_fingerprint(` **contém** `print(` como
      substring. "fingerprint" é termo central de [02] §7 e aparece em dezenas de
      identificadores do Orchestrator; renomear a função para caber no detector seria
      deformar o código por causa de um teste quebrado. A verificação por AST é **mais**
      estrita (pega `print` em qualquer formatação) e ganhou contrafactual próprio. Mesma
      justificativa de `test_expansor_e_o_unico_a_decidir_gramatica_dentro_do_safety`.
  15. **`test_cria_workspace_e_aparece_na_listagem` passou a esperar `test_config`** no
      conjunto exato de campos da resposta, e a afirmar que ele nasce `null`.

- Gates verificados:
  - **suíte completa do backend verde** — as duas regressões que a E6 introduziu
    (`test_cria_workspace_e_aparece_na_listagem`, `test_backend_nao_imprime_nada`) foram
    corrigidas, ver decisões 14 e 15. Registro honesto: reportei "verde" cedo demais numa
    rodada anterior, lendo o `exit code` do `tail` no fim do *pipe* em vez do do pytest;
  - migration aplica e reverte limpo (0002 ⇄ 0001 ⇄ base);
  - `ruff check` / `ruff format --check` / `mypy`: limpos (94 arquivos);
  - `npm run lint`, `npm run build`, `npm test`: 126 testes, 19 arquivos, verde;
  - análise estática: `orchestrator/` importa só `db`/`context_engine`/`git_runtime`/
    `safety`/`workspace`/`config`; **nenhum** `hashlib`/`json`/`re`/`fnmatch`/`importlib`;
    `execution_manager.py` é o **único** construtor de `WorkspaceTask`/`SafetyEvent`;
  - `approved → executing` recusada com 409 `capability_profile_proven` — com
    contrafactual provando que a guarda não é `raise` incondicional;
  - `reconcile_on_startup` idempotente; 81 pares da máquina de estados conferidos contra
    transcrição independente do diagrama de [02] §4.

- Pendências:
  - **Nada commitado, nada pushado. E7 não iniciada.**
  - `max_fix_rounds` carregado e inerte (E10). `auditor_binding`/`developer_binding`
    `null` até E9/E8. Nenhum `Run` é criado nesta fase.
  - Aguardando auditoria independente do Codex.

---

## 2026-09-13 — Codex — E6: primeira auditoria independente

- Relatório e reproduções, fora do repositório:
  [e6-audit-round-1.md](C:/Users/pedro/.codex/visualizations/2026/09/13/01a098b3-24ec-7202-b7c5-1deddf8aa9ec/e6-audit-round-1.md)
  e `test_e6_audit.py` no mesmo diretório. Inventários completos de status, diff stat e
  arquivos untracked também preservados ali.
- **Sem GREEN.** E6-AUD-001–010: invalidação na entrada preserva approved; recusa de
  contexto stale sem SafetyEvent; planning não durável/observável; corrida SQLite vira
  HTTP 500; plano não reverifica fontes; detector AST perde builtins.print; test_config
  expõe argv/credencial sem redação; startup silencia falhas operacionais de recuperação;
  objetivo sobre .env classificado low/trivial; needs_fix→approved dá falso conflito.
- E6-AUD-011–012: hard rules não reavaliam candidatos do enriquecimento e guarda aceita
  hash efetivo de capability incompatível. Reproduzidos com portas de teste; sem chamador
  de produção E6. Resolver antes de ativar E7/E8.
- A correção original de commit antes do 409 foi confirmada por query independente.
  Hashes reproduzíveis inclusive sob reinserção física inversa do registry; configuração
  parcial/rede inválida recusadas; namespaces de purga resistem ao mesmo UUID; import
  real de workspace não carrega orchestrator; gramática de glob permanece única.
- Validação: **562 testes direcionados passaram**; **25 provas independentes aprovadas
  no agregado**, incluindo crash em subprocesso e concorrência com duas sessões reais.
  As provas afirmam o comportamento observado, inclusive defeitos. Não foi reexecutada
  a suíte completa backend/frontend. Python 3.12.14 do runtime Codex e dependências
  temporárias dentro das faixas do pyproject: o venv indicado no log não existe aqui.
- Nenhuma correção funcional, alteração de arquitetura, commit ou push no projeto.
  Somente esta entrada foi acrescentada ao repositório pela auditoria.

---

## 2026-09-13 — Claude Opus 5 — E6: correção dos 12 findings da primeira auditoria

Rodada de correção de E6-AUD-001 a 012. Nenhum finding foi "aceito e documentado":
todos os doze foram corrigidos. **Nada commitado, nada pushado** — vai para a segunda
rodada do Codex.

- Arquivos alterados:
  - **novos**: `api/app/safety/objective_signals.py`,
    `api/tests/test_e6_audit_round_1.py`;
  - `api/app/orchestrator/execution_manager.py` — o grosso do Grupo A;
  - `api/app/orchestrator/state_machine.py` — guarda de capability com hash efetivo,
    `check_needs_fix_guard`;
  - `api/app/orchestrator/analyzer.py` — sinal de objetivo, segunda passada de hard rules;
  - `api/app/orchestrator/planner.py` — `reverify_context`, `stale_manifest_entry_ids`;
  - `api/app/orchestrator/errors.py` — `TransitionGuardFailed` ganhou `diverged_fields` e
    `requires_replan`;
  - `api/app/safety/test_policy.py` — `redacted_document`;
  - `api/app/safety/__init__.py` — exporta as duas peças novas;
  - `api/app/api/workspaces.py` — projeção redigida de `test_config`;
  - `api/app/main.py` — startup distingue schema ausente de falha operacional;
  - `api/tests/test_auth_and_bootstrap.py`, `api/tests/test_architecture.py`,
    `api/tests/test_orchestrator_state_machine.py`, `api/tests/test_api_tasks.py`.
  - `src/` (frontend) — **não tocado**. Nenhum finding é de frontend, e a superfície HTTP
    que mudou (`requires_replan` na guarda de entrada) não tem rota na E6.

- Revisão sistemática dos pontos de saída do Execution Manager (pedido do Grupo A). Os
  sete pontos, e o que cada um faz hoje:

  | Saída | Trilha commitada antes da exceção? | Estado resultante |
  | --- | --- | --- |
  | `create_task` recusa | n/a — nada foi escrito | inalterado |
  | `plan`, erro de domínio | sim (a transição) | `draft` (AUD-003) |
  | `plan`, erro inesperado | sim (transição + evento) | `failed(internal_error)` (AUD-003) |
  | `approve`, fingerprint divergente | sim (já era, decisão 13) | `awaiting_approval`/`needs_fix` com candidato novo |
  | `approve`, guarda recusa | **sim, novo** (AUD-002) | origem preservada |
  | `start_execution`, guarda invalidante | **sim, novo** — evento **e** transição (AUD-001) | `awaiting_approval` |
  | `start_execution`, guarda não invalidante | sim (já era) | `approved` — é fila, não invalidação |
  | qualquer CAS perdedor | não, e é correto | o vencedor mandou; o perdedor é no-op |

- Decisões tomadas:
  1. **AUD-001, dois desfechos distintos, não um.** Fingerprint divergente com `HEAD`
     intacto rebaixa para `awaiting_approval` **atualizando** o candidato — reaprovar
     resolve. `HEAD` alterado **limpa** o candidato inteiro (o mesmo conjunto que `reject`
     limpa) e exige `POST /plan`. O motivo é que reaprovar não poderia funcionar: o
     fingerprint cobre `base_commit`, que continua sendo o commit congelado — ele bateria
     de novo, a guarda recusaria de novo, e o humano ficaria num laço. Substituir a base
     congelada em silêncio era a outra saída, e [02] §6 a proíbe.
  2. **Só duas das seis guardas de entrada invalidam a aprovação.** `slot_available` é a
     fila de [ADR-0008], `workspace_is_git_repo` é ambiente, `attempts_below_max` é teto e
     as duas de capability são a ausência de E7. Rebaixar a task em qualquer uma delas
     faria o humano reaprovar um plano que não mudou — a correção de AUD-001 viraria um
     defeito novo. Tem contrafactual próprio.
  3. **O erro rico substitui o `TransitionGuardFailed` genérico** quando há invalidação:
     `ApprovalFingerprintMismatch` com `diverged_fields` no caso do fingerprint,
     `TransitionGuardFailed(requires_replan=True)` no caso do `HEAD`. [06] §2 pede "o
     motivo e qual campo divergiu", e a entrada de execução era o único `409` que não
     entregava o segundo.
  4. **AUD-003: o commit fica entre a entrada em `planning` e o trabalho longo.** Separar
     os dois CAS nunca foi suficiente — os dois estavam na mesma transação, e uma
     transação aberta não existe para ninguém de fora. Efeito colateral desejável: o lock
     de escrita fecha antes da análise, em vez de durar a operação inteira.
  5. **Erro recuperável → `draft`; erro inesperado → `failed(internal_error)`.** É a
     leitura literal de [02] §4 ("erro recuperável" / "erro de provider · limite"). O
     critério é `isinstance(error, OrchestratorError)`: erro de domínio é, por definição, o
     que o usuário pode corrigir e replanejar. Mandar um erro desconhecido para `draft`
     afirmaria que replanejar resolve, sem ninguém ter verificado.
  6. **`_abort_planning` nunca levanta.** Ela roda dentro de um `except` cujo trabalho é
     deixar o erro original subir; trocá-lo por um `409` de concorrência levantado durante
     a limpeza esconderia a causa. Quando a limpeza não pode acontecer, o estado já é de
     outra transação e a reconciliação do próximo startup é a rede que sobra.
  7. **AUD-004: o conjunto de códigos de conflito é fechado e vem de `sqlite_errorname`.**
     Nada de casar substring de mensagem para decidir semântica transacional, e nada de
     capturar `OperationalError` inteiro — um erro que o driver não sabe nomear **não** é
     tratado como conflito esperado. Contrafactual: `no such table` continua subindo.
  8. **O `rollback` vem antes de reler.** A sessão perdedora ainda segura o snapshot que o
     SQLite recusou; qualquer releitura antes dele responderia com o estado velho.
  9. **AUD-005 reaproveita `verify_workspace_entries`**, a API canônica da E4, recebendo o
     `planning_base_commit` já congelado. Nenhuma segunda captura de `HEAD`: a função
     aceita `verification_commit` explicitamente desde a E4 justamente para este caso, que
     o docstring dela já antecipava ("o caso da E6, com o `planning_base_commit`
     congelado").
  10. **AUD-009 nasce em `safety/objective_signals.py`, não no Analyzer.** Ela compõe
      `is_sensitive_key` (componentes de termo) com `classify_path_secrecy` (a denylist de
      [04] §5). Escrevê-la no `orchestrator/` exigiria uma segunda lista de nomes e uma
      segunda denylist — a classe de defeito que E2-AUD-003 fechou para glob.
  11. **`_looks_like_path` decide qual classificador recebe cada token, e é ele que evita
      os dois falsos positivos.** `classify_path_secrecy` não pode ver palavra solta: o
      padrão `**/*secret*` casa o *basename* `secretary`. E `is_sensitive_key` não
      reconhece `.env`, que é caminho e não nome de campo. Cada token vai para quem sabe
      respondê-lo. Os seis exemplos do prompt são teste parametrizado.
  12. **O sinal roda dentro de `evaluate_hard_rules`.** É o que torna o atalho trivial
      impossível **por construção**: o passo 2 lê `hard_rule.risk`, que já veio `high`. Não
      há uma segunda checagem a lembrar de fazer.
  13. **O motivo nunca ecoa o texto.** Um objetivo é campo livre e pode ter uma credencial
      colada por engano; devolvê-la no `rule_id` a reimprimiria no `plan`, no `plan_hash` e
      na resposta HTTP. Viaja `rule_id` + categoria, e tem teste com credencial sintética.
  14. **AUD-011: a segunda passada é a mesma `evaluate_hard_rules`, sobre a união.** Só
      roda quando o enriquecimento de fato acrescentou caminho (`merged_paths == paths` a
      dispensa), porque reclassificar à toa poderia produzir `matched` diferente e mudar o
      `plan_hash` de planos idênticos. `risk_source` compara contra a **segunda** passada:
      um `.env` sugerido pelo LLM eleva por hard rule, não por opinião de modelo.
  15. **AUD-007: a redação é da projeção de saída, nunca dos bytes operacionais.**
      `command_hash`/`policy_hash` continuam sobre o valor cru — redigir antes de hashear
      faria o fingerprint deixar de identificar o comando real, e duas configurações cujo
      segredo caísse na mesma máscara colidiriam. Tem teste dos dois lados: nenhuma rota
      devolve o valor cru, e dois `argv` que só diferem no segredo produzem fingerprints
      diferentes.
  16. **`redacted_document` redige também o que não souber interpretar.** A coluna é JSON
      livre; uma linha malformada gravada por uma versão anterior do schema não pode ser o
      caminho pelo qual um valor cru escapa. Não conseguir interpretar não é estar seguro.
  17. **AUD-008 vira uma pergunta, não um `except`.** `_schema_is_migrated` inspeciona a
      tabela. `except OperationalError: pass` descrevia uma classe genérica de falha de
      banco — lock contendido, tabela corrompida, gatilho que aborta — e todas eram
      silenciadas junto com a condição legítima. Perguntar antes devolve toda falha
      operacional ao *fail closed*, sem ampliar o `except` para `Exception`, conforme a
      própria recomendação do Codex.
  18. **AUD-010: `approve` passou a usar a origem real no CAS.** `expected_status` fixo em
      `awaiting_approval` era o que transformava a aresta `needs_fix → approved` num
      `409 concurrent_task_update` falso. A divergência de fingerprint vinda de `needs_fix`
      **não** transiciona (`needs_fix → needs_fix` não é aresta): só atualiza os campos de
      candidato no mesmo estado.
  19. **"Contexto reverificado" em `needs_fix` só pode ser o estado de agora.** Em
      `awaiting_approval` a guarda lê os estados congelados no manifest, porque a seleção
      acabou de acontecer. Em `needs_fix` houve uma execução inteira desde o congelamento,
      e reler o manifest responderia uma pergunta que já não é a certa. `reverify_context`
      é o mesmo ponto de entrada do `plan`, contra o mesmo commit congelado.
  20. **AUD-012: `proven` e hash são conferidos separadamente, nessa ordem.**
      `proven = False` é conferido antes da comparação de hash — trocar a ordem daria ao
      operador o diagnóstico errado sobre o que o adaptador fez. Os quatro desfechos são
      teste parametrizado, mais um caso que prova que o provador recebe o hash **requerido**.
  21. **AUD-006: o detector aceita `ast.Attribute` de qualquer receptor**, sem tentar provar
      que é o módulo `builtins`. Um `.print(...)` de qualquer objeto escreve na saída do
      mesmo jeito. A assimetria de custo decide: falso positivo custa uma conversa, falso
      negativo custa um token em log. O contrafactual agora **executa** cada fonte e confere
      o stdout — a tabela não pode mais afirmar uma premissa falsa sobre si mesma — e chama
      o `_print_calls` real, que era exatamente a recomendação da auditoria.
  22. **`SafetyEventKind` não ganhou valor novo.** O conjunto de [02] §12 é fechado e
      `docs/` está congelado. O mapa por guarda escolhe o item mais próximo: `retry_limit`
      para teto de tentativas, `capability_denied` para hash efetivo incompatível,
      `approval_invalidated` para a recusa de conceder aprovação, `cancelled` para
      "parou sem terminar" (que é o que `reconcile_on_startup` já usava).
  23. **A projeção redigida é recusada na entrada.** Corolário de existir uma projeção
      diferente do documento, encontrado em self-review: um cliente genérico que fizesse
      `GET` e devolvesse o corpo num `PATCH` gravaria `«redigido»` no lugar do comando, e o
      Test Runner "rodaria" a máscara — em silêncio, porque ela é uma string válida para o
      schema. `parse_test_policy` recusa qualquer campo que a contenha. Não é checagem de
      segurança, é de integridade, e o ponto de entrada é um só.
  24. **Abort recuperável não deixa `SafetyEvent`.** [02] §4 pede trilha "quando a causa é
      política", e "este workspace não tem git" é configuração, não política. O `409` já
      carrega o motivo e a volta para `draft` é durável por si. Só o abort **inesperado**
      registra.

- Gates verificados:
  - **suíte completa do backend verde** — ver a linha `PYTEST_EXIT` no relatório desta
    sessão; o exit code lido é o do pytest, não o de um `tail` no fim do pipe;
  - `ruff check` / `ruff format --check` / `mypy`: limpos (96 arquivos, 99 formatados);
  - frontend: `npm run lint`, `npm test` (126 testes, 19 arquivos), `npm run build` verdes;
  - **as sondas do próprio Codex viraram**: das 25, as 18 que afirmavam comportamento
    defeituoso agora **falham**, e as 7 que afirmavam controles confirmados continuam
    passando — incluindo `test_mismatch_is_durable` (a correção original da decisão 13),
    `test_http_reproducibility`, `test_context_insertion_order` e `test_same_uuid_namespaces`.
    A única falha que **não** é um defeito corrigido é `test_static_and_runtime_boundaries`:
    a *allowlist* da própria sonda não previa `matched.extend`, irmão do `matched.append`
    que ela já listava. A reverificação independente confirma: nenhum import de
    `re`/`fnmatch`/`glob`/`importlib`/`hashlib`/`json` em `orchestrator/`, e as únicas
    chamadas de casamento continuam sendo `build_classification_matcher` e `matcher.covers`;
  - análise estática de dono único: `detect_sensitive_objective_signals` é definida em
    **um** arquivo (`safety/objective_signals.py`), e nenhum módulo de `orchestrator/`
    contém uma segunda lista de nomes sensíveis. As duas regras viraram teste permanente em
    `test_architecture.py`.

- Pendências:
  - **Nada commitado, nada pushado. E7 não iniciada.**
  - `max_fix_rounds` segue carregado e inerte (E10); `developer_binding`/`auditor_binding`
    seguem `null` até E8/E9; nenhum `Run` é criado.
  - A guarda de entrada continua sem rota HTTP na E6 — `requires_replan` e o
    `diverged_fields` da entrada são exercitados pelo serviço, não por endpoint. A rota
    chega na E7.
  - Aguardando a segunda rodada de auditoria independente do Codex.

---

## 2026-09-13 — Codex — E6: segunda auditoria independente

- Relatório e sondas, fora do repositório:
  [e6-audit-round-2.md](C:/Users/pedro/.codex/visualizations/2026/09/13/01a098b3-24ec-7202-b7c5-1deddf8aa9ec/e6-audit-round-2.md)
  e `test_e6_audit_round2.py` no mesmo diretório.
- **Sem GREEN.** E6-AUD2-001 a 004 bloqueiam; 005 é achado adicional de baixa severidade,
  condicionado a documento malformado já persistido. Os 12 findings da primeira rodada
  foram reverificados um a um e confirmados corrigidos — com duas coberturas parciais
  (AUD-007 deixou o `runner_id`; AUD-004 cobria só o CAS final).
- 25 sondas próprias, escritas sem importar lógica das anteriores nem do módulo de
  regressões da implementação. Suíte completa reexecutada: 1.571 passed, 6 skipped;
  frontend 126 testes. `matched.extend` confirmado como falso positivo da sonda antiga.
- Nenhuma correção, commit ou alteração de arquivo do projeto nesta rodada.

---

## 2026-09-13 — Claude Opus 5 — E6: correção dos 5 findings da segunda auditoria

Todos os cinco corrigidos. **Nada commitado, nada pushado** — vai para a terceira rodada
do Codex.

**A leitura que organiza esta rodada:** quatro dos cinco findings são a *mesma correção da
primeira rodada aplicada a uma superfície que ficou de fora*. AUD2-001 é a redação de
AUD-007 numa segunda saída; AUD2-002 é o tratamento de conflito de AUD-004 numa segunda
fronteira de escrita; AUD2-003 é a delegação ao Safety Kernel de AUD-009 aplicada aos
candidatos e não só ao objetivo; AUD2-005 é o fallback da própria redação de AUD-007.
Por isso nenhuma das correções é um patch pontual: em cada caso a pergunta foi "onde mais
esta classe de problema existe?", e a resposta virou teste de análise estática.

- Arquivos alterados:
  - **novo**: `api/tests/test_e6_audit_round_2.py`;
  - `api/app/safety/redaction.py` — `redact_document`, a caminhada recursiva canônica;
  - `api/app/safety/test_policy.py` — fallback por *allowlist de valor*;
  - `api/app/safety/__init__.py` — exporta `redact_document`;
  - `api/app/api/tasks.py` — projeção redigida das partes, `approval_state`, `/context`
    com o diagnóstico certo;
  - `api/app/main.py` — o handler de erro passou a usar a caminhada canônica;
  - `api/app/orchestrator/analyzer.py` — a categoria de segredo delega para `safety`;
  - `api/app/orchestrator/planner.py` — resolve a política uma vez, para três consumidores;
  - `api/app/orchestrator/execution_manager.py` — `approval_state`, tradução de conflito
    no trabalho do plano, mensagem da guarda;
  - `api/app/orchestrator/__init__.py`, `api/tests/test_orchestrator_analyzer.py`;
  - `src/services/tasksApi.ts`, `src/pages/WorkspaceTasks.tsx`,
    `src/test/task-detail-ui.test.tsx`.

- Decisões tomadas:
  1. **A recursão de redação virou uma só, em `safety/redaction.py`.** O defeito de
     AUD2-001 não foi "esqueceram de redigir uma projeção" — foi **três cópias** da mesma
     caminhada (`api.tasks._redact_tree`, `api.main._redact_payload`,
     `safety.test_policy._redact_tree`). Com três, "aplicar a redação aqui" é uma decisão a
     lembrar em cada ponto de saída, e o ponto que esquecer é indistinguível de mais uma
     cópia. Com uma, ele é visível. O teste de análise estática afirma que existe
     exatamente uma função que chama `redact` e a si mesma.
  2. **`execution_fingerprint_parts` sai redigido; `execution_fingerprint` não.** O
     segundo é um sha256 e o cliente precisa devolvê-lo no `approve` — redigi-lo quebraria
     o fluxo. Os hashes dentro das partes atravessam o redator intactos (não há span a
     detectar em hexadecimal), o que tem teste próprio: se isso deixasse de valer, a
     correção quebraria a aprovação em silêncio.
  3. **Hashear continua sendo sobre os bytes crus.** Dois `runner_id` distintos que caem na
     mesma máscara produzem fingerprints **diferentes** — senão trocar o Test Runner
     deixaria de invalidar a aprovação, que é o que [04] §7 exige que ele faça. Mesma
     assimetria de AUD-007, agora com teste no lado do fingerprint.
  4. **O `_redact_tree` de `api/tasks.py` virou um wrapper de uma linha, e ficou.** Podia
     ter sumido; o nome local é onde mora o *porquê* desta camada (o `plan` carrega `goal` e
     `acceptance_criteria`, texto livre do usuário), e apagá-lo levaria essa explicação
     junto.
  5. **AUD2-002: a tradução mora em `_abort_planning`, não no ponto de escrita.** Envolver
     cada `flush` de `plan_task` num `try` espalharia a classificação por todo o Planner —
     e o Planner não conhece `ConcurrentTaskUpdate`. `_abort_planning` já é o funil de
     **todo** erro do trabalho longo e já fazia o `rollback` antes de reler; classificar ali
     é uma linha e cobre qualquer escrita futura sem que ninguém precise lembrar.
  6. **A tradução governa a classificação do desfecho, de graça.** Um conflito é um
     `OrchestratorError`, logo recuperável, logo a task volta para `draft` — que é a
     orientação certa, porque replanejar de fato resolve um lock que já passou. Não foi
     preciso um ramo novo para isso.
  7. **A vencedora continua intocada.** Quando o `cancel` concorrente já transicionou a
     task, `_abort_planning` relê, encontra `cancelled` e **não** escreve — só o erro
     traduzido sobe. É a metade do finding que já estava correta, e tem asserção própria
     para que a correção não a quebre.
  8. **`_is_write_conflict` continua sendo o único discriminador.** Um `RuntimeError` no
     meio do plano continua levando a `failed(internal_error)`, e um `OperationalError` que
     não seja um dos códigos de conflito continua subindo como está. Contrafactual próprio.
  9. **AUD2-003: a categoria `"secrets"` do Analyzer foi removida, não corrigida.** A
     tentação era acrescentar os cinco padrões que faltavam (`*.p12`, `*.pfx`, `id_rsa*`,
     `id_ed25519*`, `.pypirc`). Isso consertaria a reprodução e preservaria o defeito: a
     lista voltaria a divergir no próximo padrão que a denylist ganhasse. `is_secret_path`
     é a mesma função que decide o que o Context Engine pode ler.
  10. **A assimetria que a delegação torna explícita:** [04] §5 **nega leitura** de um path
      de segredo; [03] §5 **eleva o risco** de uma task que o toca. Reconhecer é a mesma
      operação; o que se faz com o veredito é que difere. Uma função, dois consumidores.
  11. **O que ficou em `HIGH_RISK_PATH_RULES` é o que não é segredo.**
      `migrations_schema`, `auth_permissions`, `ci_cd_deploy`, `dependencies` — nenhuma
      responde "isto é um segredo"; todas respondem "mexer aqui é caro de errar", que é
      semântica do Analyzer e não da denylist do Safety Kernel. Tem contrafactual: o teste
      afirma o conjunto **exato** das quatro categorias restantes.
  12. **A política que o Analyzer recebe é a efetiva do workspace, não o default global.**
      `plan_task` resolve `resolve_effective_policy` **uma vez** e a passa para os três
      consumidores (Analyzer, detector de objetivo, `select_context`). Resolver duas vezes
      abriria a porta para as duas responderem diferente sobre o mesmo arquivo no mesmo
      planejamento. Um override restritivo ([04] §5, "só restringe") passa a elevar o risco
      pelo mesmo caminho — e isso tem teste, porque é o que torna a fonte única *útil* e não
      só *menos duplicada*.
  13. **O rótulo virou `path:secret_policy`.** Ele nomeia a **origem** do veredito — a
      política de segredo de [04] §5 — e não uma linha de tabela que já não existe.
  14. **AUD2-004: nenhuma aresta nova.** `awaiting_approval → planning` continua proibida,
      e corretamente: replanejar é decisão de quem rejeitou o plano anterior. O que faltava
      era coerência entre três consumidores que diziam coisas diferentes sobre o mesmo
      estado.
  15. **`approval_state` é derivado, não persistido.** Três colunas de [02] §3
      (`approved_at`, `plan_hash`, `approved_manifest_id`) já contêm a resposta; uma coluna
      nova exigiria mudar `docs/`, congelado. Ele existe porque `awaiting_approval`
      significa duas situações que pedem ações **opostas** do humano: `pending` ("há plano
      vigente, aprove") e `requires_replan` ("o plano exibido é histórico, rejeite e
      replaneje"). `status` continua sendo a verdade da máquina de estados.
  16. **A ordem dos testes em `approval_state` importa.** `approved_at` primeiro, porque é
      a única fonte de verdade de "aprovada" ([02] §3). Depois `plan_hash is None`
      (nunca planejada). Só então `approved_manifest_id is None` — que só pode ser o rastro
      que `_invalidate_approval_on_entry` deixa, porque é o único caminho que produz plano
      sem candidato.
  17. **`GET /tasks/{id}/context` continua 404 nos dois casos, com mensagens diferentes.**
      O status está certo — não há manifest — e mudar o `code` quebraria clientes sem
      ganho. O que estava errado era instruir uma ação que o backend recusa: mandar rodar
      `POST /plan` numa task que responde `409` a ele. Agora cada motivo nomeia a ação que
      existe.
  18. **A UI oferece "Rejeitar e replanejar" como ação primária em `requires_replan`,** e
      esconde "Aprovar" em vez de desabilitá-lo. Um botão desabilitado sem explicação foi
      exatamente o que a auditoria encontrou; esconder o que responde `409` e destacar o
      que avança é a diferença entre um beco e um caminho.
  19. **Defeito meu, encontrado em self-review e corrigido antes de fechar:** a primeira
      versão de `approval_state` testava `approved_manifest_id is None` **antes** do
      status, e `reject` limpa o candidato deixando `plan_hash` — então toda task rejeitada
      relatava `requires_replan`, e a UI mandaria "rejeite e replaneje" a quem acabou de
      rejeitar. Seria repetir na projeção nova exatamente a instrução impossível que
      AUD2-004 existe para corrigir. O teste de status vem primeiro, e ganhou dois casos
      próprios (task rejeitada e task cancelada).
  20. **AUD2-005: a regra virou *allowlist de valor*, não de campo.** A versão anterior
      confiava no **nome** (`network_policy`, `cwd_mode` são enum, logo são seguros) e
      devolvia cru qualquer coisa gravada lá. A coluna é JSON livre: um valor que não é o
      esperado não é enum coisa nenhuma, é texto livre, e vai para o redator como todo o
      resto. `output_limits` deixou de ser um `dict(value)` raso e passa pela recursão.
  21. **O contrafactual de AUD2-005 é tão importante quanto a correção:** a configuração
      legítima continua saindo como está, porque a UI precisa dela para exibir a política.

- Gates verificados:
  - suíte completa do backend — ver a linha `PYTEST_EXIT` no relatório desta sessão;
  - `ruff check` / `ruff format --check` / `mypy`: limpos (97 arquivos, 100 formatados);
  - frontend: `npm run lint`, `npm test` (129 testes, 19 arquivos), `npm run build` verdes;
  - **as sondas da segunda rodada do Codex viraram**: as **11** que afirmavam comportamento
    defeituoso (`test_attack_*`) agora **falham** — as cinco de `duplicate_secret_path_table`,
    as três de `malformed_document_redaction`, as duas de `cancel_during_plan_flush` e a de
    `runner_secret_public_fingerprint` —, e as **14** de confirmação continuam passando,
    incluindo `test_001_head_null_manifest_all_consumers` (que afirma o estado **seguro** de
    AUD2-004, preservado: nenhuma aresta nova foi criada), `test_005`, `test_007`,
    `test_009`, a matriz de `test_012` e `test_ast_independent_inventory`;
  - análise estática: exatamente **uma** caminhada recursiva de redação em todo o backend;
    **nenhum** padrão da denylist de [04] §5 aparece como literal em `orchestrator/`; a
    delegação a `is_secret_path` existe por AST.

- Pendências:
  - **Nada commitado, nada pushado. E7 não iniciada.**
  - `approval_state` é projeção de leitura e não entra em hash nenhum — em particular, não
    entra no `execution_fingerprint`.
  - A guarda de entrada continua sem rota HTTP na E6: `requires_replan` e o
    `approval_state = requires_replan` são alcançados pelo serviço, e a rota chega na E7.
  - Aguardando a terceira rodada de auditoria independente do Codex.

---

## 2026-09-14 — Codex — E6: terceira auditoria independente

- Relatório e sondas, fora do repositório:
  [e6-audit-round-3.md](C:/Users/pedro/.codex/visualizations/2026/09/13/01a098b3-24ec-7202-b7c5-1deddf8aa9ec/e6-audit-round-3.md)
  e `test_e6_audit_round3.py` no mesmo diretório.
- **Sem GREEN.** Bloqueadores: **E6-AUD3-001** (Alta/P1) — a garantia de redação não
  cobria toda a superfície de saída da API: `DevWorkspace.name`/`local_path`/
  `linked_project_id`/`default_branch`, nome de branch Git no preflight e no manifest de
  task, toda a projeção do Context Registry (`title`, `body`, `tags`, `structured`,
  `source_refs`), e `detail[].input` de respostas `422` de `RequestValidationError`
  saíam crus — cada superfície corrigida individualmente deixou irmãs de fora, o mesmo
  padrão das rodadas 1 e 2 aplicado num escopo maior; **E6-AUD3-002** (Média/P2) — as
  escritas de `/approve` anteriores ao CAS final (`INSERT SafetyEvent` na divergência de
  fingerprint; `UPDATE ContextRegistryEntry` na reverificação de `needs_fix`) ainda
  propagam conflito de concorrência como `500`, porque `_abort_planning` só participa de
  `/plan` — a tradução de AUD2-002 não cobre `/approve`.
- Dois achados adicionais de baixa severidade: **E6-AUD3-003** — `redact_document`
  percorre valores, não chaves; uma credencial usada como **chave** de um documento JSON
  (`structured`, ou `test_config.output_limits` malformado) escapa da recursão canônica;
  **E6-AUD3-004** — `approval_state` (introduzido na correção da rodada 2) não está
  documentado em `docs/`, incluindo o exemplo `status=cancelled` +
  `approval_state=approved` (aprovação histórica preservada após cancelamento).
- Os cinco findings da segunda rodada foram reverificados com experimentos **novos** (não
  os testes de regressão da implementação) e confirmados corrigidos nos cenários
  reproduzidos — AUD2-002 corrigido para o trabalho do plano (AUD3-002 é o caminho irmão
  em `/approve`); AUD2-003 confirmado com candidatos adicionais (diretório, caixa, sufixo)
  e com override sintético afetando o hash; AUD2-005 confirmado para valores, com o gap
  de chave dinâmica sendo justamente AUD3-003.
- 39 sondas próprias. Suíte completa reexecutada: 1.600 passed, 6 skipped, 542,7s;
  frontend 129 testes em 19 arquivos.
- Nenhuma correção, commit ou alteração de arquivo do projeto nesta rodada.

---

## 2026-09-14 — Claude Sonnet 5 — E6: formaliza o princípio de redação e documenta approval_state (E6-AUD3-004)

Tarefa **somente documentação**, escopo fechado: `docs/architecture/04-safety-and-git-
runtime.md`, `docs/architecture/06-api-and-ui-boundaries.md`, `AGENT_LOG.md`. Não corrige
E6-AUD3-001/002/003 — esses exigem mudança de código e ficam para uma rodada própria.
Endereça só a lacuna documental que a auditoria classificou como não-bloqueante:
E6-AUD3-004.

- Arquivos alterados:
  - `docs/architecture/04-safety-and-git-runtime.md` §5 — a linha "Camada 3 — Saída" da
    tabela de proteção de segredos passou de `safety.redact` para `safety.redact_document`
    (o nome real do ponto de entrada canônico desde a E6-AUD2), e ganhou o parágrafo do
    princípio fail-closed formal, citado quase verbatim do prompt: toda string — valor
    **ou** chave — que sai como JSON por `/api/*` passa pelo sanitizador central por
    padrão, em sucesso e em erro (inclusive `RequestValidationError.detail[].input`), sem
    exceção distribuída por schema, com a única exceção sendo o *escape hatch* estreito e
    testado do `purge_token`, aplicado antes da serialização para bytes (nunca por
    *reparsing*), com HTML de bootstrap/assets/SSE fora deste *boundary*;
  - `docs/architecture/06-api-and-ui-boundaries.md` §1 — item 6 na lista de Defesas,
    referência cruzada curta para o princípio de 04 §5;
  - `docs/architecture/06-api-and-ui-boundaries.md`, seção "O que o Task Detail precisa
    mostrar" — bullet para `approval_state` mais um quadro `#### approval_state
    (E6-AUD3-004)` novo: os quatro valores confirmados no código
    (`app/orchestrator/execution_manager.py`), a regra de derivação, a tabela de ações
    válidas por valor, e o exemplo `status=cancelled` + `approval_state=approved`.

- Decisões tomadas:
  1. **O princípio da Camada 3 é citado sem alteração de conteúdo**, exatamente como o
     prompt o formulou — é a formalização de uma garantia que já orienta o código desde
     E6-AUD2-001 (a caminhada única `redact_document`), não uma promessa nova.
  2. **A tabela de proteção de segredos passou a nomear `redact_document`, não `redact`.**
     `redact` continua existindo (é o motor escalar que `redact_document` invoca), mas o
     ponto de entrada da Camada 3 — o que qualquer resposta de API atravessa — é a
     caminhada recursiva. Nomear o escalar ali estaria descrevendo a peça errada.
  3. **O princípio é documentado como contrato-alvo, não como fato 100% implementado
     hoje** — e isso é dito explicitamente, com um aviso de cobertura logo abaixo da
     citação: `redact_document` redige valores recursivamente; chaves dinâmicas de JSON
     ainda escapam (E6-AUD3-003, aberto). Escrever a garantia como se já valesse por
     completo seria a `docs/` mentir sobre o estado do código — o oposto do que a
     Architecture Freeze existe para impedir. O gap fica nomeado e rastreável, não
     escondido sob uma frase absoluta.
  4. **O cross-reference em 06 §1 entra como item 6 da lista de Defesas, não como
     nota de rodapé.** As defesas 1–5 protegem *quem pode chamar*; a redação protege *o
     que a resposta pode conter* — é uma defesa da mesma classe, não um adendo, e a lista
     numerada é onde esse tipo de garantia já vive nesta seção.
  5. **`approval_state` entra em 06, não em 02.** [02] §3–§4 são o modelo de dados e a
     máquina de estados **congelados**; `approval_state` não é uma coluna nem uma
     transição — é uma projeção de leitura que [06] já é o documento certo para descrever
     (é onde `execution_fingerprint`, `diverged_fields` e o resto do contrato de resposta
     de `/api/tasks` vivem). Não abre ADR nem toca em [02].
  6. **Os quatro valores e a regra de derivação foram confirmados no código antes de
     escrever a doc** (`app/orchestrator/execution_manager.py::approval_state`), não
     copiados do relatório de auditoria nem inferidos do prompt — inclusive o detalhe de
     que a distinção `requires_replan`/`pending` só se aplica dentro de
     `awaiting_approval`/`needs_fix`, e que fora deles (`draft`, terminais sem aprovação)
     o valor é `not_planned` — não "replanejamento necessário", que seria uma leitura mais
     óbvia e errada da tabela.
  7. **A tabela de ações válidas nomeia o `409` que `awaiting_approval → planning` produz**
     explicitamente, para que a doc não repita — desta vez na documentação, e não no
     código — o mesmo tipo de instrução impossível que E6-AUD2-004 corrigiu na mensagem de
     erro e na UI.
  8. **O exemplo obrigatório do prompt entra como parágrafo próprio**, com a consequência
     operacional explícita ("nenhuma ação de aprovação ou planejamento é liberada por
     isso") — é a frase que fecha a possibilidade de alguém ler "approval_state=approved"
     como "posso agir como se estivesse aprovada".

- Pendências (não endereçadas nesta tarefa, por estarem fora do escopo documental):
  - **E6-AUD3-001** (Alta) — cobertura de redação ainda incompleta em várias superfícies de
    API (workspace, preflight, manifest de task, Context Registry, `422`).
  - **E6-AUD3-002** (Média) — conflito de concorrência em `/approve` ainda vira `500`.
  - **E6-AUD3-003** (Baixa) — chaves dinâmicas de JSON fora da recursão de redação —
    citado nesta rodada de documentação como o gap explícito contra o princípio formal.
  - **Nada commitado, nada pushado. E7 não iniciada.**

---

## 2026-09-14 — Claude Opus 5 — E6: boundary de redação por padrão e unidade de trabalho de conflito (E6-AUD3-001/002/003)

Dois redesenhos estruturais, não patches. **Nada commitado, nada pushado** — vai para a
quarta rodada do Codex.

**A leitura que organiza esta rodada.** Os três findings são o mesmo defeito visto de
ângulos diferentes, e o defeito não é nenhum dos campos citados: é o **modelo estar
invertido**. A garantia de redação era *opt-in* — cada projeção chamava o redator por conta
própria —, então toda superfície nova nascia desprotegida e o esquecimento era silencioso.
Três rodadas encontraram três recortes disso (`test_config`, `runner_id`, meia API), e as
duas correções anteriores fecharam o caso citado deixando os irmãos vivos. O mesmo vale
para AUD3-002: o tratamento de conflito morava dentro de `_abort_planning`, que só participa
do `plan`.

Esta rodada inverte os dois modelos em vez de acrescentar um quarto recorte.

- Arquivos alterados:
  - **novos**: `api/app/api/responses.py`, `api/app/db/conflicts.py`,
    `api/tests/test_e6_audit_round_3.py`, `api/tests/test_api_redaction_boundary.py`;
  - `api/app/safety/redaction.py` — `Unredacted`, redação de chave, `redact_document`;
  - `api/app/main.py` — `default_response_class`, handler de `RequestValidationError`,
    handlers e middleware pelo boundary;
  - `api/app/api/web.py` — o `404` de bootstrap saiu do bypass;
  - `api/app/api/workspaces.py`, `api/app/api/tasks.py` — as duas prévias de purga;
  - `api/app/db/session.py` — conflito no `COMMIT` final;
  - `api/app/orchestrator/execution_manager.py` — `@command` + unidade de trabalho;
  - `api/app/orchestrator/purge.py`, `api/app/workspace/purge_tokens.py`;
  - `api/tests/test_architecture.py` — seis regras novas.
  - `src/` (frontend) — **não tocado**. Nenhum finding é de frontend, e o contrato HTTP não
    mudou de forma (só de conteúdo).

### Parte 1 — boundary de saída (fecha E6-AUD3-001)

`RedactingJSONResponse` sobrescreve `render()`, que é onde o FastAPI entrega o conteúdo
ainda como `dict`/`list`/escalar, **antes** do `json.dumps`. É `default_response_class` da
aplicação e a classe de todo *exception handler*.

### Parte 2 — escape hatch do `purge_token` (tipo, não flag)

`Unredacted(str)`, construído em **um** ponto (`PurgeTokenStore.issue`), reconhecido por
`isinstance` na caminhada.

### Parte 3 — chaves redigidas sem colisão (fecha E6-AUD3-003)

Chave que carrega segredo vira `«chave redigida N»`, opaco e único.

### Parte 4 — unidade de trabalho de conflito (fecha E6-AUD3-002)

Decorador `@command` em todos os oito comandos, mais tradução no `COMMIT` final de
`session_scope`.

- Decisões de implementação **não** 100% especificadas no prompt:
  1. **O contador de chaves é por documento, não por `dict` nem por nível.** Por nível
     bastaria para o requisito (duas chaves do mesmo `dict` nunca colidem em nenhum dos dois
     escopos), mas leria pior: dois `«chave redigida 1»` em ramos diferentes do JSON
     pareceriam a mesma chave, e não são. Custa um inteiro e remove a ambiguidade.
  2. **A numeração é posicional, não derivada do conteúdo.** A mesma chave secreta que
     aparecer duas vezes recebe dois números. Derivar o placeholder do valor (hash, prefixo)
     daria a quem observa um **oráculo de igualdade** sobre o segredo — "estas duas chaves
     são a mesma" é informação que não precisa vazar. O preço é não poder correlacionar
     ocorrências, que é o que se quer.
  3. **O gatilho de mascaramento é `redact(key) != key`, e não `is_sensitive_key(key)`.**
     São perguntas diferentes: `is_sensitive_key` classifica um **nome** ("o valor ao lado é
     segredo"), e mascarar por ela transformaria `{"password": "x"}` em
     `{"«chave redigida 1»": "x"}` — destruindo a legibilidade sem proteger nada, porque o
     nome `password` não é o segredo. O que AUD3-003 encontrou foi o caso oposto: a chave
     **contendo** uma credencial.
  4. **A chave sensível é substituída por inteiro, não por span.** `"prefixo-sk-…-sufixo"`
     não vira `"prefixo-«redigido»-sufixo"`: o que sobraria ainda é substring do nome
     original, e duas chaves que compartilhassem o mesmo segredo colapsariam no **mesmo**
     texto — num `dict` uma apagaria a outra, e um campo sumiria da resposta sem rastro.
  5. **`Unredacted` mora em `safety/redaction.py`, não em `api/`.** O tipo existe para ser
     reconhecido pela caminhada, e quem reconhece é o dono do redator. Em `api/` ele seria
     uma segunda autoridade sobre o que escapa da Camada 3.
  6. **As duas rotas de prévia de purga montam a resposta pelo response class, com
     `response_model=None`.** Descoberto empiricamente e não suposto: **o Pydantic descarta
     subclasses de `str` no `model_dump`**, então um `Unredacted` devolvido dentro de um
     modelo chega ao boundary já rebaixado a `str` e seria redigido como qualquer outra
     string. Testei `any_schema`, `is_instance_schema` e serializador plano — nenhum
     preserva. A validação de forma continua acontecendo: o modelo é construído e só o
     `model_dump` dele vira o corpo, com o token reinserido.
  7. **`is_write_conflict` mudou de `orchestrator/` para `db/conflicts.py`.** Ele nasceu no
     Orchestrator (E6-AUD-004), e **ficar lá foi o que permitiu AUD3-002**: `session_scope`,
     que é quem de fato commita, não podia consultá-lo sem inverter a direção das
     dependências. Duas cópias divergiriam pelo mesmo mecanismo que fez nascer a segunda
     lista de segredo do Analyzer.
  8. **`session_scope` também traduz — e isso o prompt não pediu.** O `COMMIT` final
     acontece depois de o comando retornar, fora de qualquer `try` do Orchestrator; um
     conflito ali continuaria virando `500`. É a superfície irmã do finding, e deixá-la
     aberta seria repetir pela quarta vez o padrão que esta rodada existe para quebrar.
     Erro novo: `WriteConflict` (409, `write_conflict`), servido pelo handler único.
  9. **Um decorador, e não `try` em cada ponto de escrita.** Envolver os dois pontos que o
     auditor citou consertaria as duas reproduções e deixaria o terceiro ponto — o que
     ninguém escreveu ainda — descoberto. O decorador mais o teste de AST fazem o comando da
     E7 nascer coberto ou quebrar a suíte.
  10. **`record_safety_event` **não** é comando, e está na lista explícita de exceções.** Ele
      escreve, mas sempre **dentro** de um comando já envolvido; um segundo boundary ali
      capturaria o conflito no nível errado — quem precisa decidir o que fazer com a
      transação é o comando, não o `INSERT`. As outras quatro exceções (`get_task`,
      `list_tasks`, `latest_manifest`, `workspace_of`) são leitura pura.
  11. **`execute_purge` do `orchestrator/purge.py` também foi decorado.** O prompt lista os
      comandos do Execution Manager; este vive noutro módulo e escreve igual. Oito comandos
      guardados no total.
  12. **Os handlers deixaram de chamar `redact` por conta própria.** Redigir duas vezes é
      inofensivo, mas deixa a dúvida sobre qual das duas é a garantia — e é a dúvida que
      produziu a redação distribuída. Uma só.
  13. **O `422` preserva a forma `detail` do FastAPI** e acrescenta `code`/`message`. Um
      cliente que já interpreta `detail` continua funcionando, e a resposta ganha o par
      estável de [06] §2. Tem contrafactual: um teste afirma que o `422` continua dizendo
      **qual** campo foi recusado — sem ele, trocar o corpo por `{"code": …}` passaria.
  14. **`204` e o HTML de bootstrap continuam fora do boundary**, nomeados na allowlist do
      teste de arquitetura. Um corpo vazio não tem string para redigir, e forçá-lo produziria
      o `null` que o status proíbe.

- Encontrado pelos **meus próprios** testes, durante esta rodada:
  - **`api/web.py` estava fora do boundary.** O `404 web_ui_unavailable` construía
    `JSONResponse` cru. Corpo constante, risco baixo — e exatamente o raciocínio ("o
    conteúdo aqui é seguro") que AUD3-001 encontrou errado em seis superfícies. Corrigido; a
    regra de AST é o que o pegou.
  - **Duas das minhas primeiras regras de arquitetura eram largas demais** e acusavam
    leitura pura (`get_task`) e prosa de docstring (`SQLITE_BUSY_SNAPSHOT` citado numa
    explicação). Ambas foram precisadas — a segunda por AST, distinguindo literal executável
    de docstring.

- **Gap descoberto e NÃO corrigido, reportado de propósito:**
  O boundary cobre toda resposta; o **detector** tem um limite que ele herda. O padrão de
  token do GitHub começa com `\b`, e um marcador **repetido e colado em si mesmo**
  (`ghp_Xghp_Xghp_X`) só é redigido na primeira ocorrência — a partir da segunda não há
  fronteira de palavra (`…789ghp_…` tem `9` antes de `g`). Consequência concreta: a sonda
  `test_new_validation_error_echo` do Codex usa `MARK*5`/`MARK*9` para estourar limites de
  tamanho, e **vai continuar encontrando o marcador** em três das quatro variantes. A
  resposta atravessou o boundary (a redação parcial é visível no corpo); o que faltou foi
  reconhecimento.
  Não corrigi porque é uma mudança na semântica de `detect_secret_spans` — o arquivo que a
  E5 auditou em cinco rodadas, com decisões próprias sobre *lookaround* e a separação entre
  span de reconhecimento e de substituição —, e está fora dos três findings desta tarefa.
  Registrado em teste (`test_o_boundary_aplica_o_detector_e_herda_os_limites_dele`), que
  afirma o que é verdade (o boundary rodou) em vez de fingir que o texto saiu limpo.

- Gates verificados:
  - suíte completa do backend — ver a linha `PYTEST_EXIT` no relatório desta sessão;
  - `ruff check` / `ruff format --check` / `mypy`: limpos (101 arquivos);
  - frontend: `npm run lint`, `npm test` (129 testes, 19 arquivos), `npm run build` verdes;
  - **sondas do Codex das três rodadas** reexecutadas — ver o relatório desta sessão para a
    contagem exata de quais viraram e quais continuam confirmando controles;
  - inventário exaustivo da rodada 3 varrido campo a campo em
    `test_api_redaction_boundary.py`: `DevWorkspace` (4 escalares), preflight Git, Context
    Registry (título, corpo, tags, valor **e chave** de `structured`, `source_refs`), import
    de seed, `WorkspaceTask`, `ContextManifest`, prévia de purga e respostas de erro.

- Pendências:
  - **Nada commitado, nada pushado. E7 não iniciada.**
  - O gap de detector descrito acima, aberto por decisão.
  - `safety/test_policy.redacted_document` continua existindo e agora é redundante com o
    boundary (o corpo inteiro passa pelo redator de todo modo). Mantido por ser defesa em
    profundidade barata e ter testes próprios; removê-lo é limpeza, não correção.
  - Aguardando a quarta rodada de auditoria independente do Codex.

---

## 2026-09-14 — Claude Opus 5 — E6: fronteira de segredo no detector e contrato de edição do Context Registry (E6-AUD4-001/002/003/004)

Os quatro findings da quarta auditoria, corrigidos. Nenhum aceito como risco residual — o
relatório pede explicitamente para **não** aceitar AUD4-001 por analogia com os riscos de
*over-redaction* da E5, e a assimetria que ele aponta é correta: o que a E5 aceitou foi
redigir demais; aqui redigia-se de menos. **Nada commitado, nada pushado** — vai para a
quinta rodada do Codex.

- Arquivos alterados:
  - `api/app/safety/redaction.py` — fronteira de segredo (AUD4-001), reserva de placeholder
    (AUD4-002), `contains_redaction_marker` (AUD4-004);
  - `api/app/main.py` — handler de `HTTPException` do Starlette (AUD4-003);
  - `api/app/api/responses.py` — `EditViewJSONResponse`;
  - `api/app/api/context.py` — rota `edit-view`, `ContextEditViewResponse`,
    `expected_content_hash` no `PATCH`;
  - `api/app/context_engine/errors.py` + `__init__.py` — `RedactedContentRejected` (422) e
    `ContextChanged` (409);
  - `api/app/context_engine/service.py` — recusa de marcador nos limpadores, concorrência
    otimista em `update_entry`;
  - `api/app/safety/__init__.py` — reexporta o reconhecedor de marcador;
  - **novo**: `api/tests/test_e6_audit_round_4.py`;
  - `api/tests/test_architecture.py` — o conjunto fechado de saídas sem redação;
  - `api/tests/test_context_redaction_e5_round5.py` — **GATE 1 mudou de forma** (ver abaixo);
  - `src/services/contextApi.ts`, `src/utils/contextEntries.ts`,
    `src/pages/WorkspaceContext.tsx` e dois módulos de teste — o fluxo de edição do lado do
    cliente, que é onde AUD4-004 foi observado;
  - `docs/architecture/04-safety-and-git-runtime.md` — nota curta sobre a fronteira, com a
    autorização explícita do prompt desta tarefa.

### E6-AUD4-001 — a fronteira de palavra ganha uma segunda forma de ser satisfeita

O `\b` que abre os padrões de token opaco existe para não casar no meio de uma palavra. Ele
falha quando o vizinho **também é segredo** — e aí a premissa dele ("o que está ao lado é
uma palavra comum") é simplesmente falsa. Em `AKIA`, que tinha fronteira dos dois lados,
duas chaves coladas não eram reconhecidas **nenhuma** das duas.

A correção não remove fronteira nenhuma: acrescenta uma segunda maneira de satisfazê-la —
**o início ou o fim de outra ocorrência do mesmo catálogo**. Três peças, todas no dono
único:

1. *tempering* condicionado a uma ocorrência **completa**: o match guloso para onde um
   segredo inteiro começa, e só aí;
2. variantes **encadeadas**, derivadas do padrão principal por remoção do `\b` inicial, que
   o motor só ancora no fim de uma região já reconhecida;
3. a fronteira à direita da AWS saiu do regex e virou regra do motor, que a satisfaz
   consultando o catálogo inteiro.

Mais um **fecho de adjacência** que repete até estabilizar, porque a segunda ocorrência abre
a terceira.

### E6-AUD4-002 — reservar antes de alocar

O contador garantia unicidade entre os nomes que ele mesmo emitia, e nada contra os que já
estavam no documento. Agora uma varredura prévia coleta os números já ocupados por chave
literal, em qualquer profundidade, e o contador os pula.

### E6-AUD4-003 — o caminho de erro do framework

`default_response_class` cobre o retorno normal de `APIRoute` e não substitui o handler de
`HTTPException` embutido do Starlette — que é o caminho de todo `404` de rota e `405` de
método, e de qualquer `HTTPException` que uma dependência levante. Registrado handler
próprio, pelo boundary, preservando `status_code` e `headers` verbatim.

### E6-AUD4-004 — projeção de saída não é conteúdo autoral

Quatro peças, que só funcionam juntas: recusa de marcador em **toda** escrita do registry,
rota `edit-view` com o conteúdo cru, `expected_content_hash` obrigatório no `PATCH`, e o
editor do frontend lendo o cru em vez da projeção.

- Decisões de implementação **não** 100% especificadas no prompt:
  1. **O relaxamento de fronteira é ancorado em segredo, e só nele.** Um segredo colado a
     uma palavra comum (`tokenizerghp_…`) **continua** não sendo reconhecido. Remover o `\b`
     em geral transformaria o detector num casador de substring e alargaria a over-redaction
     numa superfície que ninguém mediu; o finding fala de segredo encostado em segredo, e é
     essa a classe fechada. Está afirmado em teste
     (`test_a_fronteira_de_palavra_continua_valendo_contra_texto_comum`), para que a decisão
     fique visível em vez de implícita.
  2. **O *tempering* exige uma ocorrência completa à frente, não só o prefixo.** É o que
     torna a correção **provadamente aditiva**: o match só encurta onde o fecho vai cobrir
     mais do que ele largou. Com a condição fraca (só o prefixo), `ghp_` + 16 seguido de um
     `ghp_` curto demais passaria a redigir **menos** que a cascata histórica.
  3. **A fronteira à direita virou regra do motor, não lookahead no regex.** Expressá-la
     como `(?=…)` funcionaria para AWS-seguida-de-AWS e não para AWS-seguida-de-GitHub, e
     obrigaria a declarar um lookahead que a construção de `recognition_span` ampliaria em
     todo match. Como regra do motor ela consulta o **catálogo inteiro** — sem segunda
     lista — e o `recognition_span` só se amplia quando o vizinho de fato foi lido.
  4. **`anthropic_key` ganhou encadeamento, mas não *tempering*.** A classe do valor já
     inclui `-` e `.`, então cópias coladas são engolidas por uma correspondência só;
     *temperá-la* trocaria um `«redigido»` por N sem fechar nada.
  5. **O GATE 1 da E5 (rodada 5) mudou de forma, e isto é o item que mais merece olhar.**
     Ele afirmava **igualdade de bytes** com a cascata histórica para um corpus de 5.564
     concatenações. A afirmação protegia algo real (a ampliação de `recognition_span` não
     vazou para quem substitui texto) e, como definição de correção, congelava o defeito:
     as 8 divergências que apareceram são exatamente os casos de segredo colado em segredo.
     O gate passa a afirmar duas coisas mais fortes — **monotonicidade** caractere a
     caractere (nada que era redigido deixou de ser) e **divergência só por adjacência** —,
     com a máscara histórica calculada por simulação caractere a caractere, algoritmo
     diferente do que está sob teste.
  6. **A reserva coleta só os nomes com *forma* de placeholder.** Qualquer outro nome é
     incapaz de colidir com o que o contador emite; varrer por igualdade custaria uma
     chamada a `redact` por chave e não responderia nada a mais.
  7. **`expected_content_hash` é obrigatório, não opcional.** Opcional seria a mesma
     inversão de modelo que E6-AUD3-001 encontrou na redação: a garantia existiria só para
     quem lembrasse de pedi-la, e o esquecimento seria silencioso — uma escrita apagando a
     outra sem rastro. O preço foi tocar **todo** chamador de `PATCH`, no backend e no
     frontend, o que é o que torna a decisão visível.
  8. **A recusa de marcador mora nos limpadores do serviço**, não na borda HTTP. `POST`,
     `PATCH` e o seed de importação passam pelos mesmos limpadores, então as três nascem
     cobertas; e um chamador interno não passa pelo Pydantic (a razão de E3-AUD-007).
  9. **Recusar é a única resposta correta, e ela tem um preço aceito.** Um texto que
     legitimamente contenha `«redigido»` — alguém documentando este mecanismo — é recusado.
     O modo de falha do outro lado é silencioso e destrutivo; este é ruidoso e reversível.
  10. **`EditViewJSONResponse` é uma classe de resposta, não `Unredacted` campo a campo.**
      Envolver cada campo transformaria o *escape hatch* de um construtor rastreável num
      idioma reutilizável, e a regra "existe exatamente um `Unredacted(...)`" perderia o
      sentido no dia em que fossem quatro. Duas exceções, dois tipos, um ponto de uso cada,
      enumerados em `test_o_conjunto_de_respostas_sem_redacao_e_fechado`.
  11. **`ContextEditViewResponse` carrega os quatro campos que o prompt declarou, e só.**
      Consequência: `tags` e `source_refs` continuam vindo da projeção redigida. Como o
      `PATCH` só envia o que mudou, isso só aparece se alguém editar uma `tag` cujo texto
      redigido carregue marcador — e aí a escrita é recusada, não corrompida. Registrado
      aqui porque é o canto que sobrou do contrato, não um descuido.
  12. **O frontend foi religado, e o prompt não pediu.** Sem isso a rota existiria sem
      consumidor e o hash obrigatório quebraria o editor — ou seja, a correção estaria pela
      metade exatamente na superfície onde o finding foi observado (`contextEntries.ts`).
  13. **O corpo do erro de `HTTPException` preserva `detail` e acrescenta `code`/`message`.**
      Mesmo desenho do `422` da rodada 3: o cliente que já lê `detail` continua funcionando,
      e a resposta ganha o par estável de [06] §2.
  14. **A nota em `docs/` ficou em [04] §5 e em nenhum outro lugar**, e diz o que mudou na
      garantia normativa do detector. Autorizada pelo prompt desta tarefa, condicionada à
      minha avaliação de que a garantia mudou — ela mudou: a fronteira passou a ter duas
      formas de ser satisfeita.

- Encontrado por mim durante a rodada, e **não** corrigido por falta de autorização:
  - **[04] §5 tem um bloco "Cobertura atual vs. princípio" que ficou falso.** Ele afirma que
    `redact_document` percorre só **valores** e que chaves dinâmicas "ainda não são
    cobertas", rastreando E6-AUD3-003 como gap aberto. O gap foi fechado na rodada 3 (a
    implementação entrou **depois** do commit de documentação `8c493a8`), e a mesma seção
    descreve `redact_document` como "recursivo sobre valores". Um auditor que leia só o
    documento conclui o contrário do que o código faz. `docs/` é *Architecture Freeze*, e a
    autorização desta tarefa cobre a nota de AUD4-001 — não esta correção. **Precisa da
    decisão do Pedro.**
  - O relatório da rodada 4 recomenda "não resolver devolvendo credenciais cruas ao
    browser". O prompt desta tarefa decide o contrário de forma explícita, com as mitigações
    nomeadas (`LocalSessionToken`, `POST` sob mesma origem, `no-store`, tipo estreito, sem
    log nem `SafetyEvent`). Registrado porque é uma divergência deliberada entre auditor e
    dono do projeto, e a quinta rodada vai reencontrá-la.

- **O gap declarado na rodada 3 está fechado.** Era este mesmo: o `\b` do padrão do GitHub
  só casava a primeira ocorrência colada, e a sonda `test_new_validation_error_echo` do
  Codex encontrava o marcador em três das quatro variantes. `redact(M * n)` para n = 2, 3, 5
  e 9 não deixa cópia nenhuma, nas quatro famílias de token.

- Gates verificados: ver o relatório desta sessão para as contagens exatas — suíte completa
  do backend com `PYTEST_EXIT` lido do pytest (e não do fim do *pipe*), `ruff check`,
  `ruff format --check`, `mypy`, e frontend `npm run lint` / `npm test` / `npm run build`.

- Pendências:
  - **Nada commitado, nada pushado. E7 não iniciada.**
  - O bloco estagnado de [04] §5 descrito acima, aguardando decisão.
  - Aguardando a quinta rodada de auditoria independente do Codex.

---

## 2026-09-17 — Claude Opus 5 — E6: motor de convergência no detector, `edit_hash` e as barreiras da quinta rodada (E6-AUD5-001 a 007)

Os sete findings da quinta auditoria. O detector deixou de ter adjacência **dentro** dos
padrões e passou a ter um ponto fixo **em volta** deles. **Nada commitado, nada pushado** —
vai para a sexta rodada do Codex.

### A leitura que organiza esta rodada

E6-AUD5-001 é o finding que importa entender, porque ele reprova o raciocínio da rodada 4,
não só o código. Eu tinha argumentado que o *tempering* era "provadamente aditivo": o match
só encurtaria onde o fecho cobriria mais. A prova tinha um furo que o corpus de 5.564
entradas não continha — quando o payload antes do vizinho é **menor que o mínimo da
expressão**, o match não encurta: ele **desaparece**. `ghp_` + 13 A + `ghp_` + 24 B deixava
de ser reconhecido inteiramente, e `redact(s) == s`. Cobertura histórica perdida, no arquivo
cuja regra número um é nunca perder cobertura.

A lição não é "faltou um caso no corpus". É que **mexer no padrão muda o que ele reconhece
isoladamente**, e nenhuma quantidade de fecho posterior desfaz isso. Mexer no motor não.

- Arquivos alterados:
  - `api/app/safety/redaction.py` — catálogo de volta ao canônico, motor de convergência;
  - `api/app/context_engine/content_hash.py` — `compute_edit_hash`;
  - `api/app/context_engine/service.py` — `edit_hash_of`, `expected_edit_hash`;
  - `api/app/context_engine/errors.py` + `__init__.py` — `ContextEntryUnreadable`;
  - `api/app/api/context.py` — `edit_hash` na resposta, erro tipado no `edit-view`;
  - `api/app/context_engine/rendering.py` — `RENDERER_VERSION` → `e5.block.v7`;
  - **novo**: `api/tests/test_e6_audit_round_5.py`;
  - `api/tests/test_architecture.py` — duas regras novas contra subclasse de `Response`;
  - `api/tests/test_context_redaction_e5_round5.py` — GATE 1 com oráculo de emissão;
  - `src/services/contextApi.ts`, `src/utils/contextEntries.ts`,
    `src/pages/WorkspaceContext.tsx` e dois módulos de teste — `expected_edit_hash`.

### Parte 1 — o motor de convergência (fecha 001, 002 e o gate de adjacência)

Removidos: *tempering* condicionado, variantes `_CHAINED`, `right_word_boundary`. O catálogo
é o histórico, byte a byte.

O motor trabalha em coordenadas do **original**, imutável: cascata canônica → reprojeção →
catálogo de novo, e o catálogo reexecutado a partir de cada **âncora** como se ali começasse
uma string nova. Repete enquanto a união de regiões **crescer**; uma volta que só reencontra
o que já estava coberto encerra o laço. `MAX_REDACTION_PASSES = 8`; atingir o teto **com
progresso** redige o campo inteiro (`UNCONVERGED`), nunca um resultado parcial.

Terminação é aritmética: cada volta aumenta o número de caracteres cobertos, limitado por
`len(text)`.

### Parte 2 — `edit_hash` (fecha 004)

`sha256(canonical_json({v, content_hash, tags, source_refs}))`, com `source_refs` passando
por `encode_path_identity` para que NFC e NFD não colapsem. Derivado, nunca coluna.

### Partes 3, 4, 5

Duas regras de arquitetura novas, `e5.block.v7`, e erro tipado no `edit-view`.

- Decisões de implementação **não** 100% especificadas no prompt:
  1. **O algoritmo literal da Parte 1 não fecha o gate da Parte 1, e eu medi isso antes de
     escrever qualquer linha no repositório.** Uma bancada fora do repo implementou o
     algoritmo exatamente como especificado (cascata canônica + reprojeção + laço) e rodou a
     matriz de 1.035 casos: **248 falhas** no oráculo, incluindo o caso do Pedro (57%
     coberto), AUD5-001 (44%) e AUD5-002 (13–32%). O motivo é estrutural: quando o primeiro
     match consome o prefixo do vizinho, a reprojeção substitui esse prefixo pelo marcador,
     e o segundo token **deixa de existir** no texto reprojetado. Nenhuma quantidade de
     iterações o traz de volta.
     A peça que faltava é a **revarredura ancorada** — o oráculo do próprio prompt virado
     algoritmo: reexecutar o catálogo a partir de cada posição dentro/no fim de uma região
     coberta, tratando o sufixo como string nova. Com ela: **0 falhas** em 1.035 casos.
  2. **As âncoras incluem o início de cada corrida de caracteres de palavra, e ali as duas
     fronteiras são tratadas como borda de janela.** Sem isso a família AWS regride: o
     catálogo canônico tem `\\b` dos **dois** lados, duas *access keys* coladas não casam
     nenhuma das duas, e sem primeira região não há âncora nenhuma para o laço começar —
     AUD4-001 (confirmado corrigido pelo auditor) voltaria a 0% de cobertura. Medido: com
     âncora de corrida, 100%; sem, 0%.
  3. **Isto é uma variante de padrão derivada mecanicamente, que o prompt mandou remover.**
     Reporto como desvio, não como detalhe. A diferença estrutural em relação à rodada 4:
     `_ANCHORED` **não muda o que a varredura canônica reconhece** — o passo 1 é o catálogo
     histórico intacto. As variantes só existem dentro do motor, aplicadas em âncoras. É
     por isso que AUD5-001 não pode se repetir: nenhuma expressão do catálogo mudou.
     Se a leitura for que isto reintroduz o que devia sair, a reversão é de uma linha (não
     povoar `_ANCHORED`) — e o custo medido é: AUD5-001, 002 e o caso do Pedro voltam.
  4. **O limite pedido continua exato.** `tokenizerghp_…` não é reconhecido. Medi a classe
     inteira: das 1.024 combinações da matriz, as que o oráculo **livre** (sem âncora)
     acusaria são 115, e **todas as 115** são dessa classe — segredo colado em palavra
     comum. Zero fora dela.
  5. **O GATE 1 da E5 mudou de forma pela segunda vez, e agora afirma algo mais forte do que
     a igualdade de bytes original.** Três cláusulas: monotonicidade caractere a caractere;
     **emissão segura** (o oráculo aplicado ao que saiu); e **igualdade de conjuntos** — o
     conjunto de casos cujos bytes mudaram é *igual* ao conjunto de casos em que a emissão
     histórica falhava o oráculo. Medido: 345 e 345, com interseção total e zero divergência
     fora. O mutante de AUD5-003 é reprovado (teste próprio).
  6. **A máscara do oráculo vem das regiões do motor, com a emissão confrontada contra elas
     — não de uma reconstrução por bytes.** Reconstruir por bytes é ambíguo: um fragmento
     preservado pode aparecer em duas posições da entrada (`ghp_AAA…ghp_AAA…`), e o
     alinhamento escolhe a errada — produziu 9 falsos positivos na matriz antes de eu trocar.
     O par "máscara das regiões + emissão reconstruída tem de bater" fecha a mesma porta que
     AUD5-003 pediu, sem herdar a ambiguidade.
  7. **`expected_content_hash` foi REMOVIDO do contrato, não mantido como redundância.**
     `edit_hash` já contém o `content_hash`, então nenhuma divergência de conteúdo escapa do
     campo novo: o antigo é estritamente mais fraco e nunca dispararia sozinho. Manter os
     dois só serviria para alguém mandar o fraco e achar que está protegido. `content_hash`
     continua na resposta de leitura e de `edit-view` — ele é normativo sobre outra pergunta
     ([03] §2) e não mudou de significado.
  8. **`EDIT_HASH_VERSION` é separado de `CONTENT_HASH_VERSION`.** Mudar o conjunto de campos
     editáveis avança um, não o outro. Juntá-los faria uma mudança de contrato de edição
     invalidar o hash normativo da E5.
  9. **`ContextEntryUnreadable` é `500`, não `422`.** O cliente não fez nada errado: o dado
     está gravado numa forma que o modelo recusa. `422` diria que o pedido está malformado, e
     o pedido está certo.
  10. **`raise ... from None` no `edit-view`.** Encadear preservaria a `ValidationError`
      original como `__cause__`, e qualquer formatador que imprimisse a cadeia traria o
      `input_value` de volta ao log — que é exatamente o canal de AUD5-007.
  11. **A mensagem do erro nomeia o campo, nunca o valor.** `structured` é diagnóstico
      legítimo; o conteúdo dele não.
  12. **A regra de arquitetura ficou em duas**, e a primeira é a que importa: o conjunto de
      subclasses de `Response` definidas no projeto é **fechado**. O mutante de AUD5-005
      quebra na **definição** da classe, antes de qualquer uso. A segunda cobre construção e
      apelidos de import.
  13. **O teste do mutante monta uma cópia temporária da árvore** e reaponta as regras para
      ela. Nenhum arquivo do projeto é tocado, e o controle positivo (as mesmas regras contra
      a árvore real) está ao lado.
  14. **A borda de janela alarga a cobertura da AWS, e só dela.** `aws_access_key` é o único
      padrão com fronteira à **direita**; recortá-la na âncora passa a reconhecer também
      `AKIA…` seguido de minúscula, que a expressão canônica recusava. É over-redaction na
      direção segura, num formato já distintivo, e está travado em teste com os dois limites
      ao lado: `palavraAKIA…` continua **não** reconhecido, e `AKIA123` curto também não.
  15. **`MAX_REDACTION_PASSES = 8`.** Oito é folgado para o que se observa (a matriz inteira
      converge em ≤ 3), e o comportamento ao estourar é fail-closed, então errar para baixo
      custa over-redaction e não vazamento.

- Encontrado pelo **meu próprio** self-review, antes da suíte:
  - **O laço devolvia spans duplicados.** Ele reencontra a mesma âncora a cada volta, e o
    span repetido chegava a `detect_secret_spans` — que é API pública, consumida pelo
    renderer da E5 para montar `transformations`, campo que participa do payload hasheado
    ([02] §5). Determinístico não é o mesmo que correto: o hash seria estável e a lista,
    errada. Deduplicado na saída, com regressão própria.
  - **A âncora de corrida de palavra usava um alfabeto diferente do `\b`.** Com
    `[A-Za-z0-9_]`, `caféghp_…` virava início de corrida (o `é` não casava) e era redigido,
    enquanto `tokenizerghp_…` não era — o mesmo caso decidido de duas formas. O `\b` do
    Python é Unicode-aware; a âncora passou a usar `\w`, que é a definição dele.

- Gates verificados: ver o relatório desta sessão — suíte completa do backend com
  `PYTEST_EXIT` lido do pytest, `ruff check`, `ruff format --check`, `mypy`, frontend
  `npm run lint` / `npm test` / `npm run build`, e as sondas das cinco rodadas do Codex.

- Pendências:
  - **Nada commitado, nada pushado. E7 não iniciada.**
  - O desvio da decisão 3, que precisa da leitura do Pedro.
  - Aguardando a sexta rodada de auditoria independente do Codex.

---

## 2026-09-18 — Claude Opus 5 — E6: varredura linear do `pem_block` e a quarta cláusula do GATE 1 (E6-AUD6-001 e 002)

Dois findings da sexta rodada, um em cada direção do mesmo arquivo: um de **custo** no
detector, um de **força de ensaio** no gate que protege o detector. **Nada commitado, nada
pushado** — vai para a sétima rodada do Codex.

### A leitura que organiza esta rodada

E6-AUD6-001 não é vazamento, e é justamente por isso que vale registrar: `redact` roda em
**todo** boundary de saída de `/api/*` ([04] §5), então o custo dele é contrato tanto
quanto a cobertura. Um padrão com `.*?` atrás de um terminador que pode não existir
transforma 196 KB de texto numa rota presa por sete segundos — sem erro, sem log, sem nada
que uma suíte funcional perceba. As cinco rodadas anteriores mediram o que o detector
**reconhece**; nenhuma mediu o que ele **custa**.

E6-AUD6-002 é o mesmo tipo de cegueira, um nível acima. As três cláusulas do GATE 1 falam
todas sobre **conjuntos de casos** — quais entradas mudaram de bytes, quais ficaram
inseguras. Nenhuma olha para dentro de um caso cuja divergência já está autorizada. O
mutante do Codex usa exatamente essa brecha: num caso já inseguro, devolve um span
`[0, len)` e apaga o `Bearer ` junto com o valor.

- Arquivos alterados:
  - `api/app/safety/redaction.py` — `_scan_pem_block`, campo `scan` em `_Pattern`;
  - `api/tests/test_context_redaction_e5_round5.py` — quarta cláusula do GATE 1 e
    `_rotulos_protegidos`;
  - **novo**: `api/tests/test_e6_audit_round_6.py` (16 testes).

### E6-AUD6-001 — reconhecimento linear do bloco PEM

`_Pattern` ganhou um campo `scan` opcional e um método `finditer`. O motor passou a chamar
`spec.finditer(working)`; quem não declara `scan` continua usando `pattern.finditer`, então
sete dos oito padrões não sabem que isso existe.

`_scan_pem_block` acha as aberturas com um `finditer` da metade `BEGIN` (que não
retrocede), o terminador de cada uma com **um** `search` a partir do fim dela, e encerra na
primeira abertura sem terminador. Os intervalos varridos pelos `search` são disjuntos e
crescentes, porque o cursor pula para o fim de cada bloco.

O `re.Match` devolvido vem de `pattern.match(text, abertura.start())` — a **expressão
canônica**. É uma segunda passada sobre o bloco, deliberada: o objeto que chega a
`_emit_span` é produzido pelo padrão histórico, então `recognition_span`/`replacement_span`
e grupos não podem divergir do que sempre foram.

Medido, mesma máquina, 196.000 caracteres com 7.000 aberturas e nenhum `END`:

| medição | antes | depois |
|---|---|---|
| em processo | 7.645 ms | **103 ms** |
| pela rota HTTP (`POST /api/workspaces/{id}/context`) | 7,67 s | **0,98 s** |
| razão 4n/n | 15,9x (quadrático) | menos de 8 |
| texto comum do mesmo tamanho | 131 ms | 128 ms |
| 2.500 blocos PEM completos | 403 ms / 2.500 marcadores | 413 ms / 2.500 marcadores |

### E6-AUD6-002 — a quarta cláusula

`_rotulos_protegidos` devolve `rótulo − carga`, calculado **só** das janelas literais de
`_JANELAS`, sem tocar no motor nem na máscara emitida. A cláusula afirma que essas posições
continuam visíveis na emissão. Medido no corpus de 5.564 casos: 1.450 casos têm rótulo
protegido, 12.898 posições no total, zero violações pelo motor real.

- Decisões de implementação **não** 100% especificadas no prompt:
  1. **O `re.Match` vem da expressão canônica, ao custo de uma segunda passada.** A
     alternativa — montar o match à mão a partir do par abertura/terminador — economiza
     metade do trabalho e cria uma segunda fonte de verdade sobre o que o padrão
     reconheceu. É o defeito que este módulo inteiro existe para não ter. Como os blocos
     são disjuntos, a soma continua linear.
  2. **A varredura alternativa é declarada no catálogo, não no motor.** `_Pattern.scan` e
     `_Pattern.finditer` deixam `detect_secret_spans` com um ponto de entrada só; um padrão
     novo entra sem `scan` e se comporta como sempre. A alternativa seria um
     `if spec.name == "pem_block"` dentro do laço — tratamento por nome de padrão, que é a
     forma do defeito da rodada 4.
  3. **A parada na primeira abertura sem terminador é exata, não heurística.** Se não há
     `END` depois da abertura `b`, também não há depois de nenhuma posterior: o fim da
     parte `BEGIN` cresce monotonicamente com a posição dela, porque `-----BEGIN` só
     reaparece dentro de outro nos cinco traços finais. Argumentado na docstring e medido
     em 323.384 casos (23.384 combinatórios mais 300.000 de fuzz) contra o `finditer`
     canônico: zero divergências.
  4. **Os testes de tempo são relativos, não absolutos.** A afirmação é "o `pem_block`
     custa menos de 3x o texto comum do mesmo tamanho" e "quadruplicar a entrada não
     multiplica o custo por mais de 8". Um limite em milissegundos mediria a máquina; estes
     dois medem a complexidade. Contra a árvore revertida eles acusam 56,2x e 15,9x.
  5. **O fuzz é derivado de SHA-256, não de `random`.** `random` não promete a mesma
     sequência entre versões do Python, e num repositório onde `rendered_context_hash` é
     contrato ([02] §5) um gerador que pode mudar de saída é um gerador errado. Efeito
     colateral bem-vindo: nenhuma exceção de lint (`S311`) precisou ser aberta — este repo
     não tem nenhuma, e não é esta tarefa que vai abrir a primeira.
  6. **A cláusula (4) protege `rótulo − carga`, não "todo rótulo".** A versão ingênua
     reprova o motor **correto** em 11 casos do corpus: em `password: password: sk-123` o
     valor atribuído à primeira chave é literalmente o texto `password:`, e escondê-lo é o
     catálogo funcionando. Três desses casos viraram teste parametrizado.
  7. **As âncoras de `_rotulos_protegidos` crescem em ponto fixo.** Com âncoras só nos
     inícios de corrida de palavra, o oráculo enxerga menos carga que o motor e acusa
     over-redaction legítima — medido: 5 falsos positivos. O ponto fixo (a âncora ganha
     toda posição reconhecida como carga) reproduz o que o motor faz, sem consultá-lo.
  8. **A independência da cláusula (4) é verificada por AST, não por substring.** A
     docstring de `_rotulos_protegidos` **cita** `detect_secret_spans` para explicar por que
     não o usa; uma varredura textual confundiria a explicação com o uso.
  9. **O controle negativo roda o GATE 1 inteiro.** `test_e6_aud6_002_o_gate_1_inteiro_reprova_o_mutante`
     troca `redact`/`detect_secret_spans` no módulo do gate por um mutante cirúrgico — span
     total **só** para `"://Bearer password: sk-123"` — e exige `AssertionError` casando
     "esconderam um rótulo". Um mutante global seria pego pela cláusula (3), e o finding
     não é sobre isso.
  10. **`docs/` não foi tocado.** Sem autorização nesta rodada, e nenhuma garantia
      normativa mudou: o `pem_block` reconhece exatamente o que reconhecia.

- Defeito próprio, achado ao puxar o código para responder ao Pedro:
  - **Cinco bytes `0x08` em comentário e docstring de `redaction.py`** (linhas 174, 182,
    186 e 207), onde deveria estar a sequência de fronteira de palavra — resíduo do
    problema de heredoc das rodadas anteriores. Só em texto, nenhum dentro de regex, e por
    isso nenhum teste acusava. Corrigidos; `app/` e `tests/` varridos, nenhum outro.

- Gates verificados:
  - suíte do backend: ~~**1754 passed**, 6 skipped, 0 failed~~ → **1755 passed, 6 skipped,
    0 failed**, `PYTEST_EXIT=0`.
    **Correção registrada em 2026-09-18 (E6-AUD7-005).** O número escrito aqui era o da
    **penúltima** corrida, feita antes de eu acrescentar o 16º teste do módulo da rodada 6
    (`test_e6_aud6_002_o_gate_1_inteiro_reprova_o_mutante`). A corrida final desta sessão
    deu 1755, e a Rodada 7 do Codex confirmou 1755 de forma independente. O valor errado
    fica visível de propósito: foi ele que a auditoria leu;
  - suíte adversarial e de determinismo da E5 mais as auditorias da E4 (14 módulos):
    **357 passed**, `PYTEST_EXIT=0` — `rendered_context_hash`, `manifest_hash` e as
    garantias de `recognition_span`/`replacement_span` intactos;
  - `ruff check`, `ruff format --check`, `mypy`: limpos, 104 fontes;
  - frontend: lint limpo, 130 testes em 19 arquivos, build OK;
  - sondas das cinco rodadas do Codex, recontadas: R1 18/7, R2 11/14, R3 19/20, R4 9/21,
    R5 12/36 — **idênticas** ao fim da rodada 5;
  - árvore revertida (as duas correções desfeitas numa cópia temporária): os 4 testes que
    deviam falhar falham, os outros 12 passam.

- Pendências:
  - **Nada commitado, nada pushado. E7 não iniciada.**
  - O desvio da rodada 5 (`_ANCHORED`), que continua esperando a leitura do Pedro.
  - Aguardando a sétima rodada de auditoria independente do Codex.

---

## 2026-09-18 — Claude Opus 5 — E6 Rodada 7: patch documental do contrato de segurança (decisões A–D)

Etapa **somente documental**, a pedido do Pedro, depois de a sétima rodada do Codex
terminar **BLOCKED**. Nenhuma linha de implementação foi tocada, nenhum teste de
comportamento foi alterado. **Nada commitado, nada pushado.**

### O que a Rodada 7 abriu

| Finding | Prioridade | Tratamento nesta etapa |
| --- | --- | --- |
| E6-AUD7-001 — varredura otimizada do `pem_block` não seria equivalente à expressão canônica | P1 | **continua bug**; documentada só a garantia que a implementação deve satisfazer |
| E6-AUD7-002 — valor/subárvore sob chave sensível (`token`) atravessa `RedactingJSONResponse` | P1 | **continua bug**; documentada só a garantia |
| E6-AUD7-003 — `_ANCHORED` é mais conservador do que o contrato dizia | P2 | contrato corrigido; comportamento **mantido** |
| E6-AUD7-004 — GATE 1 não garante preservação de toda pontuação pública | P3 | risco residual **aceito** e registrado |
| E6-AUD7-005 — `AGENT_LOG.md` registrava 1754 em vez de 1755 | P3 | corrigido de forma rastreável |

Mais o apontamento de que [04] §5 descrevia `purge_token` como exceção **única**, embora
`EditViewJSONResponse` seja uma segunda exceção deliberada e testada desde E6-AUD4-004.

### As quatro decisões do Pedro, registradas como contrato

- **A — `_ANCHORED` fica como está.** As variantes derivadas recortam as **duas** fronteiras
  de palavra, e o efeito é assimétrico: à esquerda o relaxamento só vale em âncora de região
  coberta (por isso `tokenizerghp_…` e `xAKIA…` continuam fora); à direita vale em qualquer
  âncora, e por isso `AKIA0123456789ABCDEFx` → `«redigido»x`. Redige de mais, nunca de
  menos. Hoje o efeito da direita só é observável em `aws_access_key`, o único padrão do
  catálogo com fronteira de palavra à direita.
- **B — risco residual de precisão aceito.** O GATE 1 continua garantindo confidencialidade
  e preservação por posição dos rótulos protegidos; **não** precisa provar que toda
  pontuação pública de uma entrada já autorizada a divergir sobrevive. Não autoriza falso
  negativo, não autoriza apagar rótulo, não mexe no *fail-closed*.
- **C — duas classes de escape da fronteira JSON**, e só duas: `Unredacted` no `purge_token`
  (granularidade de **valor**) e `EditViewJSONResponse` (granularidade de **resposta**).
  Ambas tipadas, localizadas, testadas, não reaproveitáveis. Terceira exige decisão
  explícita antes de existir em código.
- **D — AUD7-001 e AUD7-002 continuam bloqueadores.** A documentação reforça a obrigação,
  não permite o comportamento: o caminho otimizado do PEM tem de preservar a semântica
  canônica em tudo que é observável (conjunto e ordem de matches, spans, grupos, aberturas
  sobrepostas) sem voltar a ser superlinear; e uma chave classificada por `is_sensitive_key`
  tem de tornar **o valor e a subárvore inteira** sensíveis para projeção pública
  *fail-closed*.

- Arquivos alterados (**documentação apenas**):
  - `docs/architecture/04-safety-and-git-runtime.md` — cabeçalho (marca de esclarecimento)
    e §5 (quatro blocos: duas classes de escape, precisão da âncora, risco residual aceito,
    duas garantias abertas);
  - `AGENT_LOG.md` — correção rastreável do 1754 na entrada de 2026-09-18, mais esta
    entrada.

- Decisões de forma **não** 100% especificadas no pedido:
  1. **A afirmação superada foi marcada, não apagada.** A frase do bloco E6-AUD4-001 que
     dizia que segredo colado a palavra comum continua necessariamente fora ficou no texto,
     riscada, com o ponteiro para o bloco novo. Apagá-la faria parecer que o contrato sempre
     esteve certo, e o Architecture Freeze existe justamente para que decisão antiga não
     desapareça em silêncio. O que ela não é mais é **contrato vigente**.
  2. **O bloco novo descreve só o que a implementação faz hoje.** O pedido foi explícito em
     não ampliar. Por isso o texto nomeia a assimetria esquerda/direita, nomeia
     `aws_access_key` como o único padrão onde o efeito da direita aparece hoje, e diz que
     um padrão novo com fronteira final herdaria o mesmo por construção — sem prometer nada
     sobre padrões que não existem.
  3. **As duas garantias abertas viraram um bloco só, rotulado como dívida.** Ficam em [04]
     §5 porque é onde a Camada 3 é definida, e o rótulo diz "findings P1 em aberto" e
     "a rodada terminou BLOCKED", para que ninguém leia a presença delas no documento como
     aceitação.
  4. **O cabeçalho ganhou marca de esclarecimento**, no mesmo formato do "Revisado na Fase
     1B.3" que já existia, delimitando o escopo a §5.
  5. **O link de ADR-0009 no bloco novo aponta para o arquivo real**
     (`0009-provider-capability-enforcement.md`); escrevi um caminho errado na primeira
     versão e corrigi antes de fechar.
  6. **`docs/audits/` não recebeu nada.** Não existe `docs/audits/e6-round-7.md` no
     repositório — nem ele nem nenhum relatório das rodadas 1 a 6 da E6 — e criar um
     resumo meu no lugar do relatório do auditor seria inventar fonte primária. Ver a
     pendência abaixo.

- Verificação do que a auditoria descreve, feita só por inspeção (nada foi alterado):
  - **E6-AUD7-003 confirmado.** `AKIA0123456789ABCDEFx` → `«redigido»x`;
    `AKIA0123456789ABCDEF` + `GH` → `«redigido»GH`; e, do outro lado, `xAKIA…`, `XAKIA…`,
    `9AKIA…`, `_AKIA…` e `tokenizerghp_…` continuam crus. `aws_access_key` é o único padrão
    do catálogo com fronteira de palavra à direita.
  - **E6-AUD7-002 confirmado.** `redact_document({"token": "valor-comum-sem-forma-de-segredo"})`
    devolve o valor intacto; idem para subárvore (`{"token": {"inner": …}}`), lista
    (`{"api_key": ["a","b"]}`) e `{"password": "abcdefgh"}` — enquanto
    `is_sensitive_key("token")` é `True`.
  - **E6-AUD7-001 NÃO reproduzido por mim.** Diferencial de `_scan_pem_block` contra
    `pem_block.finditer`: 69.904 combinações exaustivas de tokens PEM (n ≤ 4) mais 300.000
    sequências longas gerada por SHA-256 — zero divergências. Diferencial do **motor
    inteiro** (`redact`) entre a árvore atual e uma cópia com a varredura otimizada
    desligada, sobre ~209.000 casos misturando PEM com as outras famílias — zero
    divergências. Isso **não** refuta o finding: fui eu que escrevi a otimização e o
    oráculo, e um ponto cego compartilhado entre os dois é exatamente o que um diferencial
    assim não enxerga. O contraexemplo do auditor é o que falta, e é dele que a etapa de
    implementação precisa.

- Pendências:
  - **Nada commitado, nada pushado. Nenhuma implementação alterada. E7 não iniciada.**
  - **E6-AUD7-001 e E6-AUD7-002 continuam abertos e bloqueadores**, para a etapa de
    implementação seguinte.
  - **O relatório `docs/audits/e6-round-7.md` não existe no repositório.** Li os findings
    pelo enunciado da tarefa. Para AUD7-001 falta o contraexemplo concreto do auditor.
  - O desvio da rodada 5 (`_ANCHORED` manter variantes derivadas) deixa de ser pendência:
    a **Decisão A** desta rodada o resolve, mantendo o comportamento e corrigindo o
    contrato.

---

## 2026-09-18 — Claude Sonnet 5 — E6: fechamento documental mínimo pré-implementação (estreitamento do contrato `_ANCHORED`, contraexemplo de E6-AUD7-001)

Microetapa **somente documental**, MODELO Sonnet / EFFORT medium. Nenhuma implementação
alterada, nenhum teste de comportamento alterado. **Nada commitado, nada pushado.**

### 1 — Contrato `_ANCHORED` estreitado

A frase final do bloco "Precisão da âncora" em
[04](docs/architecture/04-safety-and-git-runtime.md) §5 podia ser lida como uma garantia
do **redator inteiro**, e isso entrava em tensão direta com E6-AUD7-001 (P1, aberto): se o
contrato promete "nunca cobre menos" para o redator como um todo, um scanner otimizado que
cobre menos seria uma violação do contrato documentado, não um bug isolado do scanner.

Reescrita para deixar explícito que a garantia é **das variantes derivadas `_ANCHORED`
especificamente** — elas podem produzir *over-redaction*, não podem cobrir menos que a
passada canônica —, e que isso **não afirma nada** sobre outros caminhos/otimizações do
redator (como o scanner do `pem_block`). E6-AUD7-001 é citado como contraexemplo conhecido
e P1 aberto, com referência cruzada para o bloco "Duas garantias abertas" que já existia.
A Decisão A da rodada anterior (manter o comportamento de `_ANCHORED`) não foi alterada —
só a precisão de a quem a garantia se aplica.

### 2 — Persistência da auditoria da Rodada 7: BLOQUEADA nesta etapa

O pedido citava "Vou fornecer o relatório integral junto desta tarefa", mas o relatório
integral do Codex **não veio anexado nem colado** na mensagem — só o enunciado dos cinco
findings (já registrado na entrada anterior) e o trecho de contraexemplo da seção 3.

`docs/audits/e6-round-7.md` **não foi criado nesta etapa.** Criar esse arquivo com
qualquer coisa que não seja o texto verbatim do auditor — inclusive uma reconstrução minha
a partir dos enunciados que recebi — seria exatamente o que a tarefa proíbe ("não
transforme em uma interpretação do Claude", "não invente relatórios"). Pedi ao Pedro o
texto integral antes de prosseguir com este item.

**Os relatórios das rodadas 1–6 da E6 continuam ausentes do repositório** — não existe
`docs/audits/e6-round-1.md` até `e6-round-6.md`. Eles **não serão reconstruídos de memória
ou inferência**. Se forem recuperados de fonte primária depois, podem ser adicionados como
os relatórios originais, verbatim, no mesmo padrão.

### 3 — Contraexemplo primário de E6-AUD7-001, registrado para a etapa de implementação

O Pedro forneceu, diretamente nesta conversa, a reprodução independente do Codex que este
Developer não tinha na etapa anterior (na ocasião eu tinha reportado não conseguir
reproduzir o finding). Verbatim, como recebido:

```python
B = "-----BEGIN PRIVATE KEY-----"
E = "-----END PRIVATE KEY-----"
p = "MIIE_AUD7_SYNTHETIC_PRIVATE_MATERIAL"
u = lambda x: "BEGIN PRIVATE KEY-----" * 2 + x + E
texto = B + E + u(p) + u("")
```

Resultado observado pelo Codex:

* regex canônica: `(0,52), (69,157), (174,226)`
* scanner otimizado: `(0,52), (152,226)`
* consequência: `p` (`MIIE_AUD7_SYNTHETIC_PRIVATE_MATERIAL`) permanece visível em
  `redact(texto)` e atravessa também `RedactingJSONResponse`.
* o Codex reportou ainda **339 divergências em 5.000 composições adicionais** com
  aberturas sobrepostas.

**O Developer agora possui o contraexemplo primário de E6-AUD7-001 para a etapa seguinte.**
Nesta etapa ele não foi usado para corrigir o scanner nem para alterar testes — só
registrado, conforme a instrução explícita da tarefa. Ele precisa ser reproduzido dentro
da suíte de testes (`_scan_pem_block` vs. `pem_block.finditer`) antes de qualquer correção,
e antes de considerar fechado o diferencial que eu tinha rodado na etapa anterior (que não
encontrou divergência com os corpora que gerei — o que este contraexemplo mostra é que
meus geradores não cobriam o padrão de aberturas sobrepostas que o produz).

- Arquivos alterados (**documentação apenas**):
  - `docs/architecture/04-safety-and-git-runtime.md` — uma frase reescrita em "Precisão da
    âncora" (§5);
  - `AGENT_LOG.md` — esta entrada.

- Pendências:
  - **Nada commitado, nada pushado. Nenhuma implementação alterada.**
  - **`docs/audits/e6-round-7.md` continua sem existir** — aguardando o texto verbatim do
    relatório do Codex.
  - **Relatórios das rodadas 1–6 da E6 continuam ausentes**, e não serão reconstruídos.
  - **E6-AUD7-001 e E6-AUD7-002 continuam abertos e bloqueadores** para a etapa de
    implementação seguinte. Para AUD7-001, o contraexemplo primário agora está disponível
    (seção 3 acima).

---

## 2026-09-18 — Claude Sonnet 5 — E6: relatório da Rodada 7 finalmente persistido

Conclusão da Tarefa 2, deixada pendente na microetapa anterior. `docs/audits/e6-round-7.md`
foi criado com o **relatório integral e original do Codex**, fornecido pelo Pedro nesta
conversa — verbatim, sem resumo, sem reescrita, sem correção de estilo/gramática, sem
adaptação de paths, sem reinterpretação de findings. Nenhuma reconstrução ou interpretação
minha foi usada. Ver o arquivo para o conteúdo completo (veredito BLOCKED; findings
E6-AUD7-001 a 005; a tabela de gates; o `git status`/`git diff --stat` capturados pelo
auditor).

**As rodadas 1–6 da E6 continuam ausentes de `docs/audits/`.** Elas não serão
reconstruídas de memória ou inferência. Se as fontes primárias originais forem recuperadas
depois, poderão ser persistidas verbatim, no mesmo padrão.

**E6-AUD7-001 e E6-AUD7-002 continuam P1 abertos** — bloqueadores para a etapa de
implementação seguinte.

- Arquivos alterados:
  - **novo**: `docs/audits/e6-round-7.md` (relatório verbatim do Codex, 212 linhas);
  - `AGENT_LOG.md` — esta entrada.

**Nenhuma implementação foi alterada nesta etapa. Nada commitado, nada pushado.**

---

## 2026-09-18 — Claude Opus 5 — E6: correção concentrada de E6-AUD7-001 (scanner PEM autossobreponível)

Escopo **exclusivo**: `E6-AUD7-001`. `E6-AUD7-002` não foi tocado — continua aberto.
`_ANCHORED` não foi tocado. **Nada commitado, nada pushado.**

`E6-AUD7-001 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`
`E6-AUD7-002 REMAINS OPEN`

### Reprodução, antes de qualquer edição

O contraexemplo independente de `docs/audits/e6-round-7.md` foi reproduzido **ponta a
ponta** na árvore, antes de qualquer alteração:

```python
B = "-----BEGIN PRIVATE KEY-----"
E = "-----END PRIVATE KEY-----"
p = "MIIE_AUD7_SYNTHETIC_PRIVATE_MATERIAL"
u = lambda x: "BEGIN PRIVATE KEY-----" * 2 + x + E
texto = B + E + u(p) + u("")
```

Confirmado: expressão canônica `(0,52), (69,157), (174,226)`; scanner (antes da correção)
`(0,52), (152,226)`; `p` visível em `redact(texto)`. Idêntico ao relatório.

### Causa raiz

Não é "a enumeração pula posições" em geral. É que `_PEM_BEGIN` (a metade usada para
localizar aberturas) **incluía os cinco traços iniciais**
(`-----BEGIN[A-Z ]*PRIVATE KEY-----`) — a mesma expressão **começa e termina** com o
literal de cinco traços, o que a torna **autossobreponível**. Quando uma abertura termina
em `-----` e o texto seguinte é `BEGIN…KEY-----` sem trazer os próprios traços, esses
cinco caracteres servem simultaneamente de fim da primeira ocorrência e início da segunda
— e `finditer`, que nunca sobrepõe matches, consome os traços na primeira e descarta a
segunda inteira. Verificado isoladamente com `_PEM_BEGIN.finditer` puro sobre uma cadeia
sintética: acha 1 ocorrência onde deveriam existir 2.

O corpo **sem** os cinco traços (`BEGIN[A-Z ]*PRIVATE KEY-----`) não tem essa propriedade
— o próprio `[A-Z ]*` exclui `-`, então um corpo sempre termina exatamente onde o próximo
pode começar, e nunca precisa "emprestar" caractere nenhum do vizinho. Verificado: o corpo
sozinho acha corretamente as duas ocorrências, adjacentes, nunca sobrepostas entre si.

### Alternativas consideradas e descartadas

1. **Zero-width lookahead por posição** (`(?=(-----BEGIN...))`, idioma padrão do Python
   para matches sobrepostos). Descartada: reavalia a expressão a cada posição do texto,
   sem a garantia de progresso monotônico que a versão atual tem — haveria caminho para
   `O(n²)` em entradas adversariais com muitas quase-correspondências.
2. **Busca manual por posição** (`search(text, pos)`, avançando `pos+1` a cada achado).
   Descartada pelo mesmo motivo: repetir buscas completas a partir de posições que
   avançam de 1 em 1 pode degenerar para custo quadrático quando há muitos candidatos
   sem terminador espalhados pelo texto.
3. **Reescrever `_PEM_BEGIN` para não ser autossobreponível diretamente** (ex.: exigir
   um caractere de contexto extra). Descartada: mudaria a expressão usada para
   *localizar* candidatos sem mudar a que *decide* — na prática criaria uma segunda
   definição parcial do que é uma abertura válida, o que a Decisão C da rodada 7
   (fonte de verdade única) proíbe.
4. **A escolhida — separar corpo (sem sobreposição) de um teste local de 5 traços.**
   Não muda a expressão canônica (`pattern`, usada em `pattern.match` para decidir o
   bloco de verdade), não introduz uma segunda gramática, e preserva a mesma estrutura de
   custo do algoritmo original (uma passada de localização + um teste `O(1)` por
   candidato + buscas de terminador com custo amortizado por avanço de cursor).

### Por que a correção continua linear

* `_PEM_OPEN_BODY.finditer` é uma única passada sem retrocesso e sem sobreposição
  consigo mesma — custo `O(n)`;
* o teste de cinco traços por corpo é `O(1)`, e há no máximo um corpo por ocorrência de
  `BEGIN` no texto, então a soma de todos os testes é `O(n)`;
* o terminador de cada abertura **ativa** sai de **um** `search` a partir do fim dela.
  Esses `search` cobrem intervalos disjuntos e crescentes — sucesso avança o cursor até o
  fim do bloco, falha encerra a varredura inteira. Nunca há dois `search` caros disputando
  a mesma região;
* a primeira abertura ativa sem terminador encerra tudo, com a mesma prova de
  monotonicidade que já existia (se não há `END` a partir de uma posição, também não há a
  partir de nenhuma posição posterior).

Medido contra formas adversariais desenhadas **especificamente** para esta correção —
cadeias de milhares de aberturas sobrepostas sem terminador, cadeias sobrepostas com um
terminador único e distante, "ilhas" repetidas de sobreposição com terminador, e
terminadores quase válidos intercalados — o crescimento observado dobra ao dobrar o
tamanho da entrada em todos os formatos testados (nunca quadruplica). Ver tabela de
desempenho abaixo.

### Estratégia (resumo)

`_Pattern` continua com o mesmo campo `scan` opcional. `_scan_pem_block` passou a:

1. localizar candidatos via `_PEM_OPEN_BODY` (o corpo, sem os cinco traços iniciais) —
   não sobreponível consigo mesma, então acha **todas** as ocorrências;
2. para cada corpo, testar localmente se os cinco caracteres imediatamente anteriores são
   `-----`; só então o corpo vira uma abertura candidata;
3. delegar a decisão final — inclusive achar o terminador e fixar
   `recognition_span`/`replacement_span`/grupos — exclusivamente a `pattern.match`, a
   expressão canônica, exatamente como antes.

### Testes adicionados

**Novo**: `api/tests/test_e6_audit_round_7.py` (23 testes). Cobre:

* reprodução exata do contraexemplo (spans, grupos, `recognition_span`/`replacement_span`,
  `redact()`);
* **prova contra a árvore anterior** (item 10 da tarefa): a implementação da rodada 6
  reescrita literalmente no teste (não importada) diverge no contraexemplo; o motor real,
  com `_PATTERNS` trocado via `dataclasses.replace` para usar essa versão antiga, volta a
  vazar `MATERIAL_SINTETICO`; desfeito o monkeypatch, a árvore corrigida não vaza;
* causa raiz isolada (autossobreposição de `_PEM_BEGIN` vs. não-sobreposição do corpo) e
  o deslocamento fixo de 5 caracteres;
* diferencial exaustivo scanner-corrigido vs. expressão canônica: **103.448 casos
  combinatórios** (com a família de sobreposição explicitamente incluída — a lacuna dos
  geradores da rodada 6) e **200.000 casos de fuzz determinístico** via SHA-256 (sem
  `random`), dentro do próprio módulo de teste — mais **500.000 casos de fuzz** e
  **103.448 combinatórios** rodados fora da suíte, antes de tocar em produção, para
  validar a ideia antes de implementar;
* os onze cenários nomeados pela tarefa: abertura iniciada nos traços finais de outra,
  múltiplas sobreposições consecutivas, bloco válido seguido de abertura sobreposta,
  abertura sobreposta seguida de bloco válido, muitos candidatos sobrepostos (500 em
  cadeia), muitos `BEGIN` sem `END`, muitos blocos válidos pequenos, um bloco válido muito
  grande, terminadores quase válidos intercalados, terminador muito distante, conteúdo
  semelhante a `BEGIN` que não pode ser confundido, e composição parametrizada em n/2n/4n;
* regressão HTTP real: `POST /api/workspaces/{id}/tasks` com o contraexemplo no campo
  `goal`, confirmando que `MATERIAL_SINTETICO` não aparece nos **bytes crus** da resposta
  (não só no JSON decodificado) — o mesmo *boundary* onde a Rodada 7 encontrou o
  vazamento;
* dois testes de desempenho: a regressão de E6-AUD6-001 (196.000 caracteres, 7.000
  aberturas sem terminador) e um novo para a família de sobreposição, com crescimento
  n/2n/4n em dois formatos (sem terminador e com terminador distante).

Durante a escrita, três testes tinham premissas próprias erradas (não bugs no código):
um literal de saída hardcoded incorretamente, um cenário "bloco válido seguido de
sobreposição" que na verdade não produzia sobreposição nenhuma (o bloco válido consome os
próprios traços finais junto com o terminador, então não sobra traço para reaproveitar —
corrigido para encadear a sobreposição *depois* do bloco válido, não *com* ele), e um
"terminador quase válido" que na verdade era válido (`ENDX` casa `[A-Z ]*` porque `X` é
maiúscula — `END EC PRIVATE KEY-----` já é forma aceita no catálogo; corrigido para usar
um dígito, que o *char class* de fato exclui). Todos os três corrigidos antes de reportar
qualquer resultado.

### Decisões de implementação **não** 100% especificadas na tarefa

1. **O deslocamento de 5 caracteres é uma constante nomeada** (`_PEM_DASH_WIDTH`), não um
   literal solto, porque ele expressa uma propriedade da expressão canônica (exatamente 5
   traços, nem mais nem menos) que teria de mudar junto se o catálogo mudasse.
2. **O nome mudou de `_PEM_BEGIN` para `_PEM_OPEN_BODY`** para deixar explícito na leitura
   que essa expressão não inclui mais os traços — evita que uma manutenção futura
   reintroduza-os "para ficar mais parecido com o nome antigo".
3. **A prova contra a árvore anterior usa `dataclasses.replace` em `_PATTERNS`**, não
   `monkeypatch.setattr` direto em `_scan_pem_block`. `_Pattern` é `frozen`/`slots`, e o
   `scan` de cada entrada é resolvido na **construção** da tupla — trocar o nome de módulo
   depois não afeta a instância já construída. Descoberto ao escrever o teste (a primeira
   versão não fazia o vazamento reaparecer, porque o monkeypatch não tinha efeito nenhum).
4. **O teste da rota HTTP verifica os bytes crus da resposta (`resposta.content`), não só
   o JSON decodificado** — um vazamento que sobrevivesse a uma reserialização não seria
   pego olhando só o `dict`.

### Gates executados

| Gate | Resultado |
| --- | --- |
| `test_e6_audit_round_7.py` (novo, isolado) | 23 passed |
| E5 redaction (rounds 3–5 + metadata) + router + verification + `test_secrets_and_redaction` + boundary HTTP + arquitetura + `test_e6_audit_round_{1..6}` + `test_context_audit_e4_round_{1..6}` | 714 passed |
| `ruff check .` | limpo |
| `ruff format --check .` | 108 arquivos já formatados |
| `mypy` | limpo, 105 fontes (2 erros de tipo no teste novo corrigidos: comparação de tuplas de tamanhos diferentes anotada explicitamente; `_scan_pem_block_com_o_bug_de_aud7_001` reescrita como gerador para bater com `Iterator[Match[str]]`) |
| **suíte completa do backend** | **1778 passed, 6 skipped, 0 failed**, `PYTEST_EXIT=0` (1755 confirmados pela Rodada 7 + 23 testes novos) |

Frontend não tocado nesta etapa — nenhum arquivo compartilhado com ele foi alterado
(só `api/app/safety/redaction.py` e o teste novo), então os gates de frontend não foram
executados.

### Prova contra a árvore anterior

Além do controle embutido no próprio módulo de teste, montei uma cópia física da árvore
com a versão exata da rodada 6 restaurada em `redaction.py` e rodei
`test_e6_audit_round_7.py` contra ela:

```
5 failed, 18 passed
```

Os 5 que falham são exatamente os que testam o vazamento diretamente: a reprodução do
contraexemplo, a igualdade de grupos/spans, a confirmação de que o scanner antigo
realmente falha ali, o teste de troca de árvore via `_PATTERNS`, e — o mais importante —
**a regressão HTTP real**: rodando a suíte contra a árvore anterior, `POST
/api/workspaces/{id}/tasks` com o contraexemplo no `goal` devolve `201` com
`MATERIAL_SINTETICO` visível nos bytes crus da resposta, banco e Git reais, idêntico ao
que o relatório descreveu. Na árvore corrigida, os 23 testes passam.

### Desempenho

| medição | antes (com o bug) | depois (corrigido) |
| --- | --- | --- |
| 196.000 chars, 7.000 aberturas sem terminador — em processo | — (E6-AUD6-001 já resolvido antes desta etapa) | ~0,1–0,2 ms |
| texto comum, mesmo tamanho | — | ~0,1 ms |
| cadeia de 187.000/374.000/748.000 chars de aberturas sobrepostas, sem terminador | — | 0,12 / 0,24 / 0,46 ms (linear) |
| a mesma cadeia + 1 terminador distante | — | 2,99 / 6,12 / 12,06 ms (linear) |
| "ilhas" repetidas de sobreposição + terminador, 235k/470k/940k chars | — | 4,14 / 8,22 / 17,41 ms (linear) |
| terminadores quase válidos intercalados, 106k/212k/424k chars | — | 2,12 / 4,12 / 8,21 ms (linear) |

Todas as razões de crescimento dobram ao dobrar o tamanho da entrada — nenhuma se
aproxima de quadruplicar. A correção não reintroduz custo superlinear.

### Risco residual identificado

Nenhum identificado além do que a rodada anterior já registrou como decisão consciente
(o over-redaction aceito de `_ANCHORED` e a precisão limitada do GATE 1 quanto a pontuação
pública — nenhum dos dois tocado aqui). O diferencial cobre exaustivamente até 5
composições de peças mais fuzz de até 90/70 caracteres; não é uma prova formal de
equivalência para **todo** texto, mas segue o mesmo padrão de evidência que o resto deste
módulo usa (diferencial + fuzz determinístico + prova estrutural da causa raiz), e a causa
raiz identificada (autossobreposição de um literal de largura fixa) foi eliminada por
construção, não por cobertura de casos.

- Arquivos alterados:
  - `api/app/safety/redaction.py` — `_PEM_BEGIN` removido, `_PEM_OPEN_BODY` +
    `_PEM_DASH_WIDTH` adicionados, `_scan_pem_block` reescrito;
  - **novo**: `api/tests/test_e6_audit_round_7.py` (23 testes).

- Pendências:
  - **`E6-AUD7-001 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`.**
  - **`E6-AUD7-002 REMAINS OPEN`** — não tocado nesta etapa.
  - `_ANCHORED`, GATE 1, `redact_document`, `is_sensitive_key`, `EditViewJSONResponse`,
    `Unredacted`, `purge_token`, Analyzer, Planner, Router, state machine, frontend e
    documentação normativa (além do já corrigido na etapa anterior) — nenhum tocado.
  - **Nada commitado, nada pushado.**

---

## 2026-09-18 — Claude Opus 5 — E6: E6-AUD7-001-REV2, a revisão pré-Codex achou um segundo bug de custo na correção candidata

Continuação direta da correção anterior de `E6-AUD7-001`. **Nada commitado, nada
pushado.** `E6-AUD7-002` não foi tocado — continua aberto. `_ANCHORED` não foi tocado.

**Não escondo nem reescrevo a entrada anterior** ("E6: correção concentrada de
E6-AUD7-001…"). Ela registrou corretamente a correção do falso negativo (a sobreposição).
O que ela não pegou — porque nenhum dos meus geradores incluía a família certa — foi que
a própria correção introduziu um **segundo** defeito, de custo, no localizador de
candidatos que ela criou.

### Hipótese

A revisão pré-auditoria apontou: `_PEM_OPEN_BODY = re.compile(r"BEGIN[A-Z ]*PRIVATE
KEY-----")` tem um quantificador guloso (`[A-Z ]*`) seguido de um sufixo **obrigatório
que pode não existir** (`PRIVATE KEY-----`). Em `"BEGIN " * n`, cada ocorrência de
`BEGIN` inicia uma tentativa em que o quantificador consome o resto do texto e depois
retrocede, um caractere de cada vez, procurando o sufixo — que nunca aparece. Isso é
`O(n)` por tentativa, com `O(n)` tentativas: `O(n²)`.

### Reprodução, antes de qualquer mudança

Medido isoladamente, `list(_PEM_OPEN_BODY.finditer("BEGIN " * n))`:

| n | tamanho | tempo |
| --- | --- | --- |
| 2.000 | 12.000 | 30,57 ms |
| 4.000 | 24.000 | 141,62 ms |
| 8.000 | 48.000 | 538,60 ms |

`T(2n)/T(n) ≈ 4,63`; `T(4n)/T(2n) ≈ 3,80`. A hipótese está confirmada: crescimento muito
mais perto de 4x do que de 2x. (Uma primeira tentativa de medir com `n=20.000/40.000/
80.000` ficou presa por mais de 3 minutos sem terminar — o próprio tempo de espera já era
evidência qualitativa antes da medição quantitativa em escala menor.)

### Causa raiz

Não é "a enumeração pula posições" — é a forma estrutural `<algo>[classe]*<sufixo
obrigatório>`. Sempre que o sufixo pode falhar, o motor de regex do Python retrocede por
trás do quantificador guloso, e não há como saber de antemão que vai falhar sem tentar.
Qualquer reescrita que mantenha essa forma (mesmo com um padrão "diferente") herda o
mesmo risco — não é um defeito de UM regex específico, é um defeito da FORMA.

Famílias adicionais medidas, além de `"BEGIN " * n` (todas com a mesma assinatura antes
da correção, todas lineares depois):

* `"BEGIN " + "X" * n` (corrida grande sem sufixo nenhum);
* `("BEGIN " * n) + "X" * n` (muitos `BEGIN` na mesma corrida, sem fechar);
* `("BEGIN PRIVATE KE ") * n` (sufixo quase completo, repetido);
* `"BEGIN " + "X" * n + "PRIVATE KEY-----"` (um único sufixo, distante, depois de ruído).

### Solução

Duas primitivas que não têm a forma `quantificador-guloso + sufixo-obrigatório`:

1. `_PEM_CORRIDA_DE_PALAVRA = re.compile(r"[A-Z ]+")` — uma classe de caractere repetida
   **sem** nada obrigatório depois dela na mesma expressão. Sem sufixo para falhar, não
   há retrocesso possível: achar todas as corridas maximais é `O(n)`, cada caractere
   pertencendo a no máximo uma corrida.
2. Dentro de cada corrida já isolada (uma `str` comum), dois testes de string pura —
   `str.endswith("PRIVATE KEY")` e `str.find("BEGIN")` — decidem se ela fecha e onde
   começa. Nenhum dos dois é regex; nenhum tem quantificador. O custo de cada teste é, no
   pior caso, proporcional ao tamanho *daquela* corrida, e como as corridas são
   disjuntas, a soma é `O(n)`. Os cinco traços finais são conferidos como texto bruto
   (`text[fim:fim+5] == "-----"`), sem regex nenhuma.

`_localizar_corpos_pem(text)` substitui `_PEM_OPEN_BODY.finditer`, devolvendo tuplas
`(início, fim)` em vez de `re.Match` — `_scan_pem_block` foi ajustado de acordo. A
expressão canônica (`pattern.match(...)`) continua sendo a única fonte de verdade sobre
o que é um bloco válido; o localizador só localiza.

**Por que o resultado é idêntico ao do regex antigo.** O quantificador guloso, quando
casa, sempre consome o máximo e recua o mínimo — ou seja, sempre encontra a ocorrência de
`PRIVATE KEY-----` **mais à direita** dentro de uma corrida contígua. `str.endswith`
sobre a corrida inteira pergunta exatamente isso. E `BEGIN` continua sendo achado pela
ocorrência mais à **esquerda** (`str.find`), a mesma preferência do motor de regex ao
escolher onde a busca começa.

### Prova de progresso monotônico / linearidade

* `_PEM_CORRIDA_DE_PALAVRA.finditer` é uma classe repetida sem alternância e sem sufixo
  — o motor nunca tem motivo para retroceder, então é `O(n)` por construção, não por
  medição;
* os dois testes de string por corrida são `O(1)` amortizado, porque as corridas
  particionam o texto (nenhuma sobreposição, soma dos comprimentos ≤ `len(text)`);
* o resto do algoritmo (teste de 5 traços, busca de terminador, avanço de cursor) é
  exatamente o mesmo da correção anterior, cuja prova de linearidade não mudou.

Medido no módulo real, `"BEGIN " * n`:

| n | tamanho | `redact()` |
| --- | --- | --- |
| 20.000 | 120.000 | 108,02 ms |
| 40.000 | 240.000 | 204,16 ms |
| 80.000 | 480.000 | 413,28 ms |
| 160.000 | 960.000 | 1.013,08 ms |

Razões: 1,89 / 2,02 / 2,45 — todas perto de 2x, nenhuma perto de 4x.

Nas cinco famílias adversariais do finding (n/2n/4n cada), razões entre 1,92x e 2,10x —
ver tabela completa no relatório desta etapa.

### Prova de que a sobreposição continua corrigida

Contraexemplo original da Rodada 7, repetido: `(0,52), (69,157), (174,226)` — idêntico à
canônica; `MATERIAL_SINTETICO` não aparece em `redact()` nem na resposta HTTP.

### Testes adicionados

`api/tests/test_e6_audit_round_7.py` ganhou 11 testes na seção "E6-AUD7-001-REV2":

* `test_e6_aud7_001_rev2_localizador_bate_com_o_regex_de_corpo` — diferencial exaustivo
  de `_localizar_corpos_pem` contra `BEGIN[A-Z ]*PRIVATE KEY-----` (o próprio regex com o
  bug, usado só como oráculo, nunca com corpus grande o bastante para expor o custo dele)
  — >25.000 casos combinatórios, 0 divergências;
* `test_e6_aud7_001_rev2_localizador_fuzz_deterministico` — 50.000 casos por SHA-256,
  0 divergências;
* `test_e6_aud7_001_rev2_scanner_continua_batendo_com_o_canonico` — 200.000 casos do
  fuzz original (ponta a ponta, scanner vs. canônica de verdade), 0 divergências: a REV2
  não reabriu nada que a primeira correção fechou;
* `test_e6_aud7_001_rev2_crescimento_e_linear_nas_familias_do_finding` — parametrizado
  nas 5 famílias adversariais, n/2n/4n, limiar de razão **3,0** (não absoluto);
* `test_e6_aud7_001_rev2_reproducao_exata_nao_regride` — a reprodução exata da revisão
  pré-auditoria (`"BEGIN " * n`), mesmo limiar;
* `test_e6_aud7_001_rev2_a_sobreposicao_continua_corrigida` e
  `test_e6_aud7_001_rev2_regressao_http_continua_verde` — requisito 5 da tarefa.

**Autocorreção durante a escrita:** minha primeira versão desses dois últimos testes de
crescimento usava limiar `< 8`, copiado por inércia da tolerância dos testes de
sobreposição da correção anterior (onde 8 fazia sentido para outra família). Rodei os
onze testes REV2 contra uma cópia física da árvore **anterior** a esta correção (com o
`_PEM_OPEN_BODY` regex-based restaurado) e **todos os onze passaram**, inclusive os de
crescimento — porque 8x nunca teria pego uma razão quadrática de ~4x. Apertei o limiar
para **3,0** (meio caminho entre o ~2x medido nesta correção e o ~4x medido na anterior)
e confirmei: com o limiar novo, `test_e6_aud7_001_rev2_reproducao_exata_nao_regride`
**falha de verdade** contra a árvore anterior (`T(2n)/T(n) = 3,90x`), e os testes de
correção (diferencial, sobreposição, HTTP) continuam passando nela — só o desempenho
regride, exatamente como deveria.

### Gates executados

| Gate | Resultado |
| --- | --- |
| `test_e6_audit_round_7.py` completo (34 testes) | 34 passed |
| E5 redaction (rodadas 3–5 + metadata) + router + verification + `test_secrets_and_redaction` + boundary HTTP + arquitetura + `test_e6_audit_round_{1..6}` + `test_context_audit_e4_round_{1..6}` | 725 passed |
| `ruff check .` | limpo |
| `ruff format --check .` | limpo |
| `mypy` | limpo, 105 fontes |
| **suíte completa do backend** | **1789 passed, 6 skipped, 0 failed**, `PYTEST_EXIT=0` |
| árvore anterior à REV2 (cópia física) — testes de correção | 5 passed |
| árvore anterior à REV2 (cópia física) — teste de crescimento | **1 failed** (`T(2n)/T(n) = 3,90x`), como deveria |
| diferencial exaustivo do scanner completo (módulo real) | 161.130 combinatórios + 500.000 fuzz, 0 divergências |

Frontend não tocado — nenhum arquivo compartilhado alterado.

### Rótulo usado

"E6-AUD7-001-REV2" — uma segunda rodada sobre o **mesmo** finding AUD7-001, não deve ser
confundido com `E6-AUD7-002` (a chave estruturalmente sensível), que continua aberto e
não foi tocado.

- Arquivos alterados:
  - `api/app/safety/redaction.py` — `_PEM_OPEN_BODY` (regex) removido; `_localizar_corpos_pem`,
    `_PEM_CORRIDA_DE_PALAVRA`, `_PEM_SUFIXO_SEM_TRACOS` adicionados; `_scan_pem_block`
    ajustado para consumir tuplas em vez de `re.Match` do localizador;
  - `api/tests/test_e6_audit_round_7.py` — docstring do módulo atualizada; 11 testes
    novos na seção E6-AUD7-001-REV2; import de `_localizar_corpos_pem` e `Callable`.

- Pendências:
  - **`E6-AUD7-001 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`** (correção
    funcional **e** linearidade agora demonstradas).
  - **`E6-AUD7-002 REMAINS OPEN`** — não tocado.
  - `_ANCHORED`, GATE 1, `redact_document`, `is_sensitive_key`, `EditViewJSONResponse`,
    `Unredacted`, `purge_token`, Analyzer, Planner, Router, state machine, frontend e
    documentação normativa — nenhum tocado.
  - **Nada commitado, nada pushado.**

---

## 2026-09-18 — Claude Sonnet 5 — E6: verificação independente de E6-AUD7-001-REV2 — BLOCKED por E6-AUD7V-001

Relatório integral da verificação independente (Codex) persistido, verbatim, em
[docs/audits/e6-round-7v.md](docs/audits/e6-round-7v.md). Não duplico o conteúdo aqui.

Resumo: **E6-AUD7-001** foi verificado de forma independente tanto funcionalmente (contraexemplo
original, 36.860 entradas próprias, HTTP real) quanto quanto ao custo (crescimento ~2×/dobra em
oito famílias) — a correção candidata REV2 está correta e linear. A auditoria terminou **BLOCKED**
apenas por **E6-AUD7V-001** (P2): o gate temporal de
[test_e6_audit_round_7.py:781](api/tests/test_e6_audit_round_7.py:781) reprovou a implementação
linear numa execução isolada (razão 3,41×) e passou em repetições — instabilidade de medição, não
regressão de algoritmo. **E6-AUD7-002 continua OPEN**, fora do escopo desta verificação.

Estado após esta entrada: iniciando a correção de E6-AUD7V-001 (só o mecanismo de prova temporal;
nenhum código de produção, nenhum teste funcional/diferencial/overlap/HTTP alterado).

---

## 2026-09-18 — Claude Sonnet 5 — E6: E6-AUD7V-001 corrigido — gate temporal endurecido contra ruído isolado

Continuação direta da entrada anterior. Escopo exclusivo: `E6-AUD7V-001` (P2, gate temporal
instável). **Nenhum código de produção alterado — `redaction.py` intocado.** `E6-AUD7-002`
continua aberto, não tocado. Nada commitado, nada pushado.

### Diagnóstico do teste antigo

`_mais_rapido` ([test_e6_audit_round_7.py:606](api/tests/test_e6_audit_round_7.py:606)) media
`time.perf_counter()` (*wall-clock*) e tomava o **mínimo de apenas 2 repetições**, sem aquecimento
e sem controle de coleta de lixo. Duas repetições só protegem contra ruído se ele atingir no
máximo uma delas — um evento (do SO, ou uma coleta de lixo geracional cujo limiar de alocação é
cruzado bem naquele ponto) que dure o bastante para atingir **as duas** chamadas de uma mesma
medição contamina o mínimo inteiro sem o algoritmo ter mudado de custo.

### Reprodução, antes da mudança

Em bancada limpa e isolada, a metodologia antiga **não reproduziu** a instabilidade: 10 rodadas de
`"BEGIN " * n` (n_base=20.000) em REV2 deram razões 1,91x–2,05x / 1,99x–2,04x; 10 rodadas na
candidata quadrática (mutante físico, n_base=4.000) deram 3,75x–3,90x / 3,77x–3,92x. Consistente
com o próprio relatório da verificação independente ("a mesma implementação passou em quatro
repetições isoladas") — o problema é um evento raro/externo, não uma propriedade determinística.

Reproduzi o padrão exato do achado injetando artificialmente um atraso de 250ms nas 2 primeiras
chamadas do ponto `2n` (simulando um evento que dura o bastante para atingir as duas repetições
que a metodologia antiga usaria):

```
amostras 2n (ms): [392.91, 393.83, 145.63, 143.63, 144.53, 144.6, 146.46]
ANTIGA (mínimo de 2):  razão(2n/n) = 5.30x   <- falso positivo, reproduzido
NOVA   (mediana de 7): razão(2n/n) = 1.97x   <- imune à mesma contaminação
```

### Relógio: `process_time_ns()` medido e descartado

A tarefa sugeriu `time.process_time_ns()` (tempo de CPU) como possível melhoria sobre wall-clock.
Medi a resolução empírica de ambos nesta máquina antes de escolher:

```
resolução empírica perf_counter_ns:  838 ns
resolução empírica process_time_ns:  15.625.000 ns  (~15,6ms)
```

No Windows, `GetProcessTimes()` arredonda para o tique do relógio do sistema (~15,6ms por padrão)
— grande demais frente às medições de dezenas/centenas de ms usadas aqui. Trocar de relógio teria
trocado ruído de agendamento por ruído de quantização, sem ganho. Mantive `perf_counter_ns`.

Também testei se `gc.disable()` durante a janela medida reduziria a variância (30 amostras por
tamanho, com e sem GC habilitado): não ajudou de forma consistente — em n=80.000, a variância com
GC desabilitado foi **maior**, provavelmente por deixar memória acumular sem coleta entre pontos.
Por isso a correção usa `gc.collect()` **antes** de cada amostra (limpa o lixo da amostra
anterior, evitando que uma coleta caia *dentro* da medição atual), não `gc.disable()`.

### Metodologia nova

`_tempo_mediano_ms(texto, amostras=7)`: uma chamada de aquecimento descartada; 7 medições
independentes de `perf_counter_ns`, cada uma precedida por `gc.collect()`; toma-se a **mediana**,
não o mínimo. A mediana de 7 só se desloca se 4 ou mais amostras forem contaminadas na mesma
direção — um único evento transitório, por mais longo que seja, não derruba o teste.

Validada sob contenção de CPU sustentada (15 processos concorrentes ocupando os outros núcleos,
10 rodadas): razões permaneceram em 1,94x–2,03x, sem nenhuma falha espúria. Contenção sustentada
escala todos os pontos proporcionalmente e não distorce a razão; é o ruído *assimétrico* (um pico
isolado, como no item anterior) que a distorce — e é exatamente esse caso que a mediana neutraliza.

### Threshold: derivação

`_LIMIAR_RAZAO = 2.75`, consequência das medições, não ponto de partida:

* pior razão observada em REV2 (linear), agregando bancada limpa + contenção de CPU + verificação
  final com o código real do teste, dezenas de rodadas: **2,10x** (uma amostra isolada da
  verificação final chegou a 2,12x);
* melhor razão observada na candidata quadrática, mesmas condições: **3,44x**;
* margem disponível: **1,34**. `2,75` fica a meio caminho (margem de ~0,65 para cada lado) — longe
  o bastante de ambas as distribuições observadas para não confundir ruído normal com regressão.

Distribuições não se sobrepõem em nenhuma das rodadas medidas: não foi necessário "parar" por
falta de margem confiável.

### Controle negativo (10+ execuções de cada lado, com o código real do teste)

Importando `_tempo_mediano_ms`/`_razoes`/`_LIMIAR_RAZAO` do próprio módulo de teste (não uma
reimplementação paralela), com `redact` de REV2 e do mutante físico via `sys.path`:

* REV2 (n_base=20.000), 10 rodadas: **10/10 passaram** (razões 1,57x–2,12x);
* candidata quadrática (n_base=4.000), 10 rodadas: **10/10 falharam corretamente** (razões
  3,61x–4,10x).

Não rodei os testes parametrizados reais de `pytest` diretamente contra o mutante nos tamanhos de
produção (até 200.000 caracteres nalgumas famílias): o custo `O(n²)` da candidata levaria dezenas
de minutos por caso parametrizado — a própria lentidão é evidência qualitativa adicional de
quadraticidade. Usei tamanhos menores e escalados, com o código idêntico do teste, o que prova a
mesma propriedade discriminante sem o custo proibitivo.

### O que mudou

Só `api/tests/test_e6_audit_round_7.py`: `_tempo_mediano_ms` e `_LIMIAR_RAZAO` novos; `_mais_rapido`
continua existindo, sem alteração, usado pelos dois testes de desempenho anteriores a esta rodada
(`..._desempenho_permanece_linear_no_caso_de_e6_aud6_001`,
`..._desempenho_e_linear_na_familia_de_sobreposicao`) — nenhum dos dois foi apontado como instável,
e seus limiares (8x/12x) já têm folga bem maior do que a instabilidade observada aqui. Só os dois
testes de crescimento apontados pela verificação independente
(`..._crescimento_e_linear_nas_familias_do_finding`, `..._reproducao_exata_nao_regride`) passaram a
usar a métrica nova. Nenhum teste funcional (contraexemplo, diferencial, fuzz, sobreposição, HTTP,
`recognition_span`, `replacement_span`) foi alterado. `redaction.py` intocado.

### Gates executados

| Gate | Resultado |
| --- | --- |
| `test_e6_aud7_001_rev2_crescimento_...`/`..._reproducao_exata...`, 10 rodadas seguidas | 10/10 `PYTEST_EXIT=0` |
| `test_e6_audit_round_7.py` completo | 34 passed |
| E5/E6/boundary/arquitetura (22 módulos) | 725 passed, 0 failed |
| `ruff check .` | limpo |
| `ruff format --check .` | limpo (108 arquivos) |
| `mypy` | limpo, 105 fontes |
| **suíte completa do backend** | **1789 passed, 6 skipped, 0 failed**, `PYTEST_EXIT=0` |
| `git diff --check` | limpo (exit=0) |

### Estado final

* **`E6-AUD7-001 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`** — correção funcional e
  linearidade permanecem demonstradas; o gate que as prova agora é estatisticamente robusto.
* **`E6-AUD7-002 REMAINS OPEN`** — não tocado.
* `_ANCHORED`, GATE 1, `redact_document`, `is_sensitive_key`, `EditViewJSONResponse`,
  `Unredacted`, `purge_token`, orchestrator, frontend, documentação normativa — nenhum tocado.
* **Nada commitado, nada pushado.**

## 2026-09-21 — Claude Sonnet 5 — E6: E6-AUD7V2-001 corrigido — mediana-de-7-por-tamanho trocada por tripletas intercaladas

Escopo **exclusivo**: `E6-AUD7V2-001` (P2, o gate temporal de `_tempo_mediano_ms` ainda
falhava sob contenção assimétrica localizada). **Nenhum código de produção alterado —
`redaction.py` intocado.** `E6-AUD7-002` não foi tocado — continua aberto. Nada
commitado, nada pushado. Relatório integral da reverificação do Codex persistido
verbatim em `docs/audits/e6-round-7v2.md`.

### Finding reproduzido

A reverificação independente (`docs/audits/e6-round-7v2.md`) confirmou de novo a
correção funcional e a linearidade de E6-AUD7-001-REV2, mas achou `_tempo_mediano_ms`
(a correção de E6-AUD7V-001) vulnerável sob dois processos concorrentes de CPU: numa
bateria de dez tripletas, uma rodada produziu T(2n)/T(n) = 3,5066x — acima do limiar
2,75 — com tempos de 98,199 / 344,347 / 410,535 ms. `T(4n)/T(2n)` na mesma rodada ficou
em 1,1922x, confirmando que só o bloco de `2n` foi afetado — a assinatura exata de uma
fase de contenção localizada, não de regressão algorítmica.

### Por que a mediana-de-7-por-tamanho ainda falhava

`_tempo_mediano_ms` mede as 7 amostras de `n`, DEPOIS as 7 de `2n`, DEPOIS as 7 de `4n`:
blocos sequenciais e contíguos por tamanho. Ela protege contra um evento *breve* (a
mediana de 7 só se desloca se 4+ amostras forem contaminadas), mas não contra uma fase
de contenção que dure o bastante para atingir 4+ das 7 amostras de UM bloco inteiro —
nesse caso a mediana daquele tamanho se desloca sozinha, sem que os outros dois tamanhos
sejam tocados, e a razão entre eles sai da faixa esperada mesmo com o algoritmo
continuando linear.

### Hipótese de tripletas intercaladas e experimentos antes de editar

Hipótese: medir as três tripletas de forma que nenhum tamanho seja sempre medido na
mesma fase temporal deveria impedir que uma rajada localizada contamine um bloco
inteiro. Antes de tocar no teste real, prototipei as duas metodologias lado a lado (script
ad-hoc, apagado ao final; não faz parte da suíte):

* **A) atual**: aquecimento + mediana de 7 amostras por tamanho, sequencial por tamanho
  (a implementação de `_tempo_mediano_ms`).
* **B) tripletas intercaladas**: 7 tripletas independentes, cada uma medindo `n`, `2n`
  e `4n` uma vez cada, em 7 ordens diferentes (as 6 permutações de 3 elementos, mais uma
  repetição da primeira) — mesmo custo total (21 janelas medidas). Razão calculada
  DENTRO de cada tripleta; mediana das 7 razões no final.

Testado contra REV2 real e contra a candidata quadrática original (localizador
`BEGIN[A-Z ]*PRIVATE KEY-----` com retrocesso, reintroduzida só via monkeypatch de
`_localizar_corpos_pem` no protótipo — nunca em `redaction.py`), em três cenários:

1. **Máquina sem carga.**
2. **Reproducer do Codex**: dois processos CPU-bound concorrentes o tempo todo. Este
   reproducer, tal como descrito, **não** reproduziu o falso positivo nesta máquina de
   16 CPUs — ambas as metodologias passaram 20/20 rodadas. Isso não invalida o achado
   do Codex (que foi 1 rodada em 10, um evento raro); só mostra que "dois processos o
   tempo todo" é fraco demais para reproduzir de forma determinística.
3. **Rajada agendada** (item 5 da tarefa: "perturbação artificial temporária que atinja
   uma região intermediária da execução"): calibrado a partir da distribuição limpa
   (bloco de `n` ≈ 720ms, bloco de `2n` ≈ 1260ms), uma rajada de 15 processos CPU-bound
   disparada aos 0,65s e mantida por 1,5s — cronometrada para cobrir exatamente a janela
   em que a metodologia A mede `2n`. Isto reproduziu a CLASSE de falha do Codex de forma
   determinística.

### Distribuições medidas (10 rodadas por combinação; REV2 = família `"BEGIN " * n`,
tamanhos 20.000/40.000/80.000; candidata quadrática, tamanhos 2.000/4.000/8.000 — ver
item 11 da tarefa: a 80.000 caracteres o candidato quadrático levaria dezenas de
segundos por chamada, sem provar nada que os tamanhos menores não provem)

| Cenário | Metodologia | min | mediana | max | Resultado |
| --- | --- | --- | --- | --- | --- |
| REV2 limpa | A (atual) | 1,53x/1,87x | 1,97x/2,00x | 2,08x/2,30x | 20/20 PASS |
| REV2 limpa | B (triplas) | 1,96x/1,97x | 1,99x/2,01x | 2,02x/2,01x | 20/20 PASS |
| REV2, 2 processos o tempo todo | A (atual) | 1,15x/1,84x | 2,00x/1,97x | 2,16x/2,09x | 20/20 PASS |
| REV2, 2 processos o tempo todo | B (triplas) | 1,78x/1,79x | 1,99x/1,99x | 2,14x/2,18x | 20/20 PASS |
| REV2, **rajada agendada (2n)** | **A (atual)** | 2,03x/1,07x | 4,15x/1,81x | 4,83x/5,00x | **8/10 rodadas reprovariam código linear** |
| REV2, **rajada agendada (2n)** | **B (triplas)** | 1,78x/1,97x | 2,00x/2,04x | 2,45x/2,28x | **0/20 razões acima do limiar — 10/10 PASS** |
| Quadrática limpa (2k/4k/8k) | A (atual) | 3,47x/3,34x | 3,60x/3,75x | 3,75x/4,05x | 20/20 FAIL (correto) |
| Quadrática limpa (2k/4k/8k) | B (triplas) | 3,40x/3,66x | 3,60x/3,77x | 3,72x/3,92x | 20/20 FAIL (correto) |
| Quadrática sob carga (2k/4k/8k) | A (atual) | 2,14x/2,10x | 3,33x/3,67x | 6,96x/4,10x | 16/20 FAIL — a própria carga também degrada a metodologia A na direção "quase passa" |
| Quadrática sob carga (2k/4k/8k) | B (triplas) | 3,00x/3,46x | 3,73x/3,80x | 4,11x/4,30x | 20/20 FAIL (correto) |

A rajada agendada é a prova direta do item 13 da tarefa: o mesmo mecanismo de
interferência localizada que quebra a metodologia A (8/10 falsos positivos) não quebra
a metodologia B (0/10) — mesma máquina, mesma rajada, mesmo instante absoluto.

Margem final: REV2 (todos os cenários, metodologia B) ≤ 2,45x; candidata quadrática
(todos os cenários, metodologia B) ≥ 3,00x. `_LIMIAR_RAZAO = 2,75` continua no meio,
com folga de ~0,30 de cada lado — **não recalibrado**, conforme item 7 da tarefa: o
problema era a agregação, não o limiar, e os dados confirmam isso.

### Metodologia escolhida e implementação

Critério de aceitação da tarefa (item 6) cumprido integralmente pela metodologia B:
REV2 limpa 10/10 PASS, REV2 sob os dois cenários de carga 10/10 PASS, candidata
quadrática limpa e sob carga 10/10 FAIL, distribuições sem sobreposição. Implementada
em `api/tests/test_e6_audit_round_7.py`:

* `_ORDEM_DAS_TRIPLAS`: as 7 tripletas (índices 0=n, 1=2n, 2=4n).
* `_medir_uma_amostra_ms`: uma medição isolada com `gc.collect()` antes — mesma
  disciplina de `_tempo_mediano_ms`, uma amostra por vez.
* `ResultadoTriplas` (`NamedTuple`) e `_razoes_por_triplas_intercaladas`: aquecimento
  por tamanho, sete tripletas, razão dentro de cada uma, mediana final. Docstring com a
  prova completa (por que o problema era a ordem, não a mediana).
* `_mensagem_de_falha_triplas`: mensagem de diagnóstico com razões por tripleta,
  mediana, limiar e tempos brutos — uma falha agora é depurável sem reinstrumentar.
* `test_e6_aud7_001_rev2_crescimento_e_linear_nas_familias_do_finding` e
  `test_e6_aud7_001_rev2_reproducao_exata_nao_regride`: passaram a chamar
  `_razoes_por_triplas_intercaladas` em vez de `_tempo_mediano_ms`/`_cresce_com_gerador`/
  `_razoes` — removidas por ficarem sem chamador.
* **Controle negativo automatizado novo** (item 11 da tarefa):
  `test_e6_aud7v2_001_a_metodologia_de_triplas_reprova_o_localizador_com_retrocesso` —
  usa `monkeypatch` no atributo do módulo `app.safety.redaction._localizar_corpos_pem`
  (nunca no arquivo) para reintroduzir o localizador com retrocesso só dentro do teste,
  restaurado automaticamente ao final; tamanhos 2.000/4.000/8.000 pelo motivo já citado.
* `_LIMIAR_RAZAO` permanece `2.75`. `_mais_rapido` e os dois testes de desempenho
  anteriores a esta rodada não foram tocados. Nenhum teste funcional (contraexemplo,
  diferencial, fuzz, sobreposição, HTTP, `recognition_span`, `replacement_span`) foi
  alterado.

### Validação (item 12 da tarefa)

| Validação | Resultado |
| --- | --- |
| Grupo de crescimento, 10 processos pytest independentes, limpo | 10/10 `PYTEST_EXIT=0` |
| Grupo de crescimento, 10 processos pytest independentes, sob 2 processos concorrentes | 10/10 `PYTEST_EXIT=0` |
| Controle negativo quadrático, 10 processos pytest independentes | 10/10 `PYTEST_EXIT=0` |
| `test_e6_audit_round_7.py` completo | **35 passed** em 83,81s |
| E5/E6/boundary/arquitetura (14 módulos) | `SUBSET_EXIT=0` |
| `ruff check .` | limpo |
| `ruff format --check .` | limpo, 108 arquivos |
| `mypy .` | limpo, 105 fontes |
| **suíte completa do backend** | **1790 passed, 6 skipped** (era 1789 antes; +1 é o controle negativo novo), `PYTEST_EXIT=0`, 1258,09s |
| `git diff --check` | limpo, exit 0 |

`api/tests/test_e6_audit_round_7.py` é um arquivo untracked (nunca commitado, parte do
volume amplo de trabalho E6 ainda não commitado) — `git diff` não mostra nada para ele;
o diff desta subcorreção está isolado nas 5 edições descritas acima (docstring do
módulo, import de `NamedTuple`, helpers de tripletas substituindo `_tempo_mediano_ms`/
`_cresce_com_gerador`/`_razoes`, os dois testes de crescimento, e o novo controle
negativo). `git status --short` ao final: mesmos 38 arquivos rastreados modificados e
mesmas ~26 entradas untracked de antes desta tarefa, mais `docs/audits/e6-round-7v2.md`
(novo, persistido no passo 1). Não editei nenhum arquivo de produção nesta tarefa.

### Ambiente

O venv em `%LOCALAPPDATA%\FreelanceFocus\venvs\api\` não existia nesta máquina; criado
com Python 3.11.9 e `pip install -e ".[dev]"`, conforme a convenção do projeto — nada
dentro do repo.

### Estado final

* **`E6-AUD7-001 CANDIDATE RESOLVED — AWAITING CODEX RE-VERIFICATION`**
* **`E6-AUD7V-001 CANDIDATE RESOLVED — superseded by V2 verification`**
* **`E6-AUD7V2-001 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`** — agregação
  temporal trocada de mediana-de-7-por-tamanho para mediana de razões sobre 7 tripletas
  intercaladas; limiar `2,75` mantido sem alteração; validado 10/10 em três cenários de
  carga distintos, incluindo o que quebrava a agregação anterior 8/10 vezes.
* **`E6-AUD7-002 REMAINS OPEN`** — não tocado.
* **NO PRODUCTION CODE CHANGED.** Nada commitado. Nada pushado.

## 2026-09-21 — Claude Opus 5 — E6: reverificação GREEN do Codex persistida (E6-AUD7V2-001)

Relatório integral da reverificação independente do Codex persistido verbatim em
`docs/audits/e6-round-7v3.md`. Veredito: **GREEN**, nenhum finding novo. O Codex validou
o balanceamento de `_ORDEM_DAS_TRIPLAS` (3/2/2 rotacionado, o melhor possível com sete
tripletas), a matemática de `_razoes_por_triplas_intercaladas` contra três oráculos
sintéticos, 10/10 execuções limpas, 15 rodadas sob quatro classes de perturbação
(carga contínua, rajadas localizadas em `n`/`2n`/`4n`/fronteira, rajadas independentes,
rajada longa) e 10/10 rejeições corretas da candidata quadrática. Reproduziu
independentemente a classe de E6-AUD7V2-001: a metodologia antiga falhou 3/3 sob a
mesma rajada (2,83x / 3,02x / 2,90x) em que a nova passou 3/3. Separação agregada entre
as distribuições: 0,849305, sem sobreposição; `_LIMIAR_RAZAO = 2.75` confirmado.

Estado registrado por esta entrada:

```
E6-AUD7-001   VERIFIED RESOLVED
E6-AUD7V-001  VERIFIED RESOLVED
E6-AUD7V2-001 VERIFIED RESOLVED
E6-AUD7-002   REMAINS OPEN
```

**PEM scanner / equivalência / complexidade / gate de regressão: FROZEN — não alterar
sem evidência nova.** Isso cobre `_scan_pem_block`, `_localizar_corpos_pem`, a prova de
equivalência contra a expressão canônica, a prova de custo linear, `_ORDEM_DAS_TRIPLAS`,
`_razoes_por_triplas_intercaladas`, `_LIMIAR_RAZAO` e o controle negativo quadrático.

Nada commitado. Nada pushado.

## 2026-09-21 — Claude Opus 5 — E6: E6-AUD7-002 corrigido — a chave sensível passa a classificar a subárvore

Escopo **exclusivo**: `E6-AUD7-002` (P1, valor sob chave estruturalmente sensível
atravessava a projeção pública). Nada do que está FROZEN foi tocado — **NO PEM CHANGES**,
verificado por grep no diff isolado: zero linhas de `_scan_pem_block`,
`_localizar_corpos_pem`, `_PEM_*`, `pem_block`, `_ORDEM_DAS_TRIPLAS`, `_LIMIAR_RAZAO` ou
`_ANCHORED`. Nada commitado, nada pushado.

### Reprodução ANTES da mudança

Executada contra a árvore intacta, com `is_sensitive_key`, `redact_document` e
`RedactingJSONResponse` (bytes crus). Todos os casos vazaram:

| Caso | Entrada | Saída de `redact_document` ANTES |
| --- | --- | --- |
| A | `{"token": "AUD7_SYNTHETIC_CREDENTIAL"}` | valor íntegro |
| B | `{"password": "ordinary-looking-value"}` | valor íntegro |
| C | `{"api_key": {"value": "NOT_REGEX_SECRET", "metadata": {"owner": "public-looking"}}}` | subárvore íntegra |
| D | `{"safe": ..., "token": "AUD7_SYNTHETIC_CREDENTIAL"}` | valor íntegro |
| E | `{"items": [{"password": "ordinary"}, ...]}` | valor íntegro |
| F | `{"token": {"nested": ["A", {"value": "B"}]}}` | subárvore íntegra |
| G | `{"outer": {"token": "SYNTHETIC"}}` | valor íntegro |
| H | `{"pin": 1234, "secret": true, "api_key": null}` | escalares íntegros |

`is_sensitive_key` devolvia `True` para `token`, `password`, `api_key`, `secret`,
`auth_token` **e `purge_token`**. Pela API real: POST e GET do Context Registry
devolviam `AUD7_SYNTHETIC_CREDENTIAL` nos bytes crus.

### Causa raiz

`redact_document` nunca chamava `is_sensitive_key`. A única consulta à classificação de
chave estava em `_redact_key`, que pergunta se a **chave contém** segredo — nunca se ela
**classifica o valor**. Chaves e valores eram processados em ramos separados da mesma
caminhada, e nada atravessava de um para o outro.

### Contrato implementado

Se `is_sensitive_key(key)` é verdadeiro, o valor inteiro associado é sensível por
estrutura, independentemente do conteúdo, do tipo e da profundidade. A marca desce por
`dict`, `list`, índice e qualquer chave intermediária. Sob a marca, **toda folha** vira
`REDACTED` sem o conteúdo ser consultado — inclusive `int`, `float`, `bool`, `None` e
string vazia. A marca **só desce**: irmãos públicos continuam intactos.

### Representação escolhida, e por que ela já era a semântica do projeto

Chaves preservadas (mantêm `_redact_key`), folhas viram `REDACTED`. Não é formato novo:
é o que `context_engine/rendering.py::_collect_leaves` já faz desde a E5 —
`sensitive or is_sensitive_key(key_text)` desce pela árvore, e `_mark_fragments` trata a
folha marcada como "valor sob subárvore sensível sai inteiro, sem olhar o conteúdo",
preservando os **caminhos** e redigindo só o valor. `_Leaf.generic_scalar` (número,
booleano, `null`) é descrito lá como "redigido localmente quando sensível", que é
exatamente o comportamento adotado aqui para os escalares genéricos. Contêiner vazio
continua vazio: não há folha para redigir, e inventar uma acrescentaria informação à
projeção. [04] §5 pedia "a subárvore é projetada *fail-closed*"; isto satisfaz o texto
normativo sem inventar semântica pública nova.

### `Unredacted` vence a classificação estrutural — obrigatório, não cosmético

`is_sensitive_key("purge_token")` é **`True`** (o componente `token` está lá), e é sob
essa chave literal que `GET /api/workspaces/{id}/purge-preview` e a rota equivalente de
task entregam o token emitido por `PurgeTokenStore.issue`. Sem a precedência explícita
de `Unredacted`, esta correção teria quebrado a purga em silêncio. O `isinstance(value,
Unredacted)` continua sendo a **primeira** decisão da função, e há teste dedicado.
`EditViewJSONResponse` não passa por `redact_document` e segue intacta. Nenhuma terceira
exceção foi criada.

### Arquivos alterados

* `api/app/safety/redaction.py` — só `redact_document`: assinatura (`_sensitive: bool`),
  docstring (seção nova de E6-AUD7-002) e corpo. Diff isolado: **63 linhas adicionadas,
  13 removidas**, nenhuma fora dessa função.
* `api/tests/test_e6_aud7_002_chave_estruturalmente_sensivel.py` — **novo**, 26 testes.

### Prova contra a implementação anterior

Os 26 testes rodaram contra a árvore **antes** da correção: **18 failed, 8 passed**
(`PYTEST_EXIT=1`). Depois: **26 passed** (`PYTEST_EXIT=0`). Os 8 que já passavam são a
premissa (`is_sensitive_key` e o catálogo), os dois escape hatches, o campo público e o
`purge_token` — isto é, exatamente o que a correção **não** deveria mudar.

### Gates

| Gate | Resultado |
| --- | --- |
| `test_e6_aud7_002_chave_estruturalmente_sensivel.py` | **26 passed**, `PYTEST_EXIT=0` |
| Focados (boundary, context, E5 r5, E6 r7, arquitetura) | **272 passed** |
| 16 suítes E5/E6/boundary/arquitetura + AUD7-002 | **606 passed**, `SUBSET_EXIT=0` |
| `ruff check .` | limpo |
| `ruff format --check .` | limpo, 109 arquivos |
| `mypy .` | limpo, 106 fontes |
| **suíte completa do backend** | **1816 passed, 6 skipped**, `FULL_EXIT=0` (era 1790+6; +26 é o módulo novo) |
| `git diff --check` | limpo, exit 0 |

### Pendência reportada, não executada

`docs/architecture/04-safety-and-git-runtime.md` §5 ainda lista E6-AUD7-002 em "Duas
garantias abertas", texto agora desatualizado pela implementação. **Não alterei**:
`docs/` é congelado (CLAUDE.md) e a tarefa proíbe *silent rewrite* de documento
normativo. Precisa de autorização explícita do Pedro.

### Estado final

* **`E6-AUD7-001 VERIFIED RESOLVED — FROZEN`**
* **`E6-AUD7V-001 VERIFIED RESOLVED`**
* **`E6-AUD7V2-001 VERIFIED RESOLVED`**
* **`E6-AUD7-002 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`**
* **NO PEM CHANGES.** Nada commitado. Nada pushado.

## 2026-09-22 — Claude Sonnet 5 — E6: reverificação BLOCKED do Codex persistida (E6-AUD7-002V-001)

Relatório integral persistido verbatim em `docs/audits/e6-aud7-002-round-1.md`. Veredito:
**BLOCKED**. A correção de E6-AUD7-002 fecha o vazamento de valor preenchido, mas
`redact_document({"token": []})` e `redact_document({"token": {}})` devolvem o
contêiner vazio intacto — o ramo `list`/`dict` sempre recursa antes de perguntar se
está vazio, e um contêiner vazio recursado devolve a si mesmo, nunca alcançando o
`if _sensitive: return REDACTED` da folha. O renderer da E5
(`rendering.py:268`/`275`) já trata contêiner vazio sob subárvore sensível como folha
`generic_scalar` e o redige inteiro; a projeção JSON diverge desse contrato.

Prosseguindo com a correção concentrada de E6-AUD7-002V-001.

## 2026-09-22 — Claude Sonnet 5 — E6: E6-AUD7-002V-001 corrigido — contêiner vazio sensível vira REDACTED

Escopo **exclusivo**: `E6-AUD7-002V-001` (P2, contêiner vazio sob chave sensível
atravessava a projeção pública). Nada FROZEN foi tocado — **NO PEM CHANGES**, verificado
por grep no diff isolado: zero linhas de `_scan_pem_block`, `_localizar_corpos_pem`,
`_PEM_*`, `pem_block`, `_ORDEM_DAS_TRIPLAS`, `_LIMIAR_RAZAO`, `_ANCHORED`; `is_sensitive_key`
não foi alterada (a única ocorrência do nome no diff é uma linha de contexto de um
comentário pré-existente, citando a função, não modificando-a). Nada commitado, nada
pushado.

### Reprodução ANTES da mudança

Confirmada contra a árvore intacta, para os 5 casos do prompt:

| Entrada | `redact_document` ANTES |
| --- | --- |
| `{"token": []}` | `{"token": []}` |
| `{"token": {}}` | `{"token": {}}` |
| `{"password": {"nested": []}}` | `{"password": {"nested": []}}` |
| `{"api_key": [{"value": {}}, []]}` | `{"api_key": [{"value": {}}, []]}` |
| `{"public": [], "token": [], "status": "active"}` | inalterado |

Confirmada também pela API real: `POST /api/workspaces/{id}/context` com
`structured={"token": [], "password": {"nested": []}, "status": "active"}` devolveu
`"token":[]` e `"nested":[]` intactos nos bytes crus (`b'{"...,"structured":{"token":
[],"password":{"nested":[]},"status":"active"},...'`), e a listagem subsequente
(`GET /api/workspaces/{id}/context` — a API não oferece GET individual desta entrada)
repetiu o mesmo vazamento.

O teste divergente localizado e confirmado: `test_e6_aud7_002_escalares_genericos_
sob_chave_sensivel` (parametrizado) tinha `([], []), ({}, {})` na lista de casos —
afirmando explicitamente o comportamento que a auditoria aponta como o finding.
Rodado isoladamente antes da correção: **9 passed** (incluindo os dois casos vazios,
que passavam por afirmarem o bug).

### Causa

`redact_document` sempre recursava em `list`/`dict` antes de perguntar se o valor
estava vazio: `[redact_document(item, ...) for item in []]` devolve `[]` — a
compreensão nunca itera, e o `if _sensitive: return REDACTED` do ramo de folha (no
final da função) nunca é alcançado para um contêiner vazio, porque o contêiner nunca
chega a esse ramo: ele é capturado antes, pelo `isinstance(value, list)`/`isinstance(
value, dict)`.

### Alteração mínima

Um único desvio nos primeiros ramos de `redact_document`, antes das checagens de
`list`/`dict`/recursão:

```python
if _sensitive and isinstance(value, list | dict) and not value:
    return REDACTED
```

Contêiner **não-vazio** continua caindo nos ramos de recursão normais (propagando
`_sensitive` para os descendentes, como já implementado). Contêiner vazio **fora** de
subárvore sensível (`_sensitive=False`) não entra nesse desvio e continua vazio.
Docstring de `redact_document` atualizada com uma seção nova explicando o porquê,
substituindo a frase antiga ("Contêiner vazio continua vazio") que descrevia
justamente o comportamento incorreto.

### Correção do teste divergente

`test_e6_aud7_002_escalares_genericos_sob_chave_sensivel`: `([], []), ({}, {})` →
`([], REDACTED), ({}, REDACTED)`. Docstring do teste atualizada para explicar a
mudança de contrato e apontar para o teste de irmãos públicos, que cobre o caso
`_sensitive=False` que a asserção antiga também cobria implicitamente.

### Regressões novas (`test_e6_aud7_002_chave_estruturalmente_sensivel.py`, +10 testes)

Lista vazia/dict vazio sensível na raiz; contêineres vazios aninhados (dois e três
níveis) sob chave sensível; contêineres vazios como elementos de lista sensível;
mistura de folha preenchida e contêineres vazios na mesma subárvore; irmãos públicos
com `[]`/`{}` intactos ao lado de contêiner sensível vazio; múltiplas chaves
sensíveis combinando escalar e contêineres vazios; fronteira `RedactingJSONResponse`
central; `Unredacted("")` (string vazia, não contêiner, mas o caso limite de
"vazio" mais próximo do escape hatch) preservado; regressão HTTP completa —
POST e listagem do Context Registry com `structured={"token": [], "password":
{"nested": []}, "status": "active"}`, bytes crus e JSON verificados nas duas
chamadas.

### Prova contra a implementação anterior

Revertido temporariamente só o branch de código (docstring mantida), suíte completa
do módulo executada: **11 failed** — exatamente os testes novos/alterados
(`escalares_genericos[valor7]`, `[valor8]`, e os 9 testes de
`E6-AUD7-002V-001: contêineres vazios`, exceto o de `Unredacted`, que não depende da
correção). Restaurado o branch: **36 passed**.

### Escape hatches preservados

`Unredacted` continua sendo a primeira checagem da função — precede inclusive o novo
desvio de contêiner vazio, então `Unredacted("")` nunca é interceptado por ele (é
`str`, não `list`/`dict`, e mesmo se fosse, o `return value` de `Unredacted` já teria
ocorrido antes). Teste dedicado
(`test_e6_aud7_002v_001_unredacted_nao_e_afetado_pelo_caso_vazio`) confirma.
`EditViewJSONResponse` não passa por `redact_document` — não tocada, sem teste novo
necessário além dos já existentes.

### Gates

| Gate | Resultado |
| --- | --- |
| `test_e6_aud7_002_chave_estruturalmente_sensivel.py` | **36 passed**, `PYTEST_EXIT=0` |
| Focados (AUD7-002, boundary, context, E5, E6 r1-r7, arquitetura, workspaces, tasks) | **671 passed** |
| `ruff check .` | limpo |
| `ruff format --check .` | 1 arquivo precisou reformatação (`test_e6_aud7_002_...py`); aplicado `ruff format`, suíte do módulo revalidada (36 passed); recheck limpo, 109 arquivos |
| `mypy .` | limpo, 106 fontes |
| **suíte completa do backend** | **1826 passed, 6 skipped**, `PYTEST_EXIT=0` (era 1816+6; +10 são as regressões novas) |
| `git diff --check` | limpo, exit 0 |

### Diff isolado desta subcorreção

`redaction.py` já carrega o acumulado de E6-AUD7-002 e das rodadas anteriores, não
commitado — o diff desde `HEAD` não isola esta etapa. Reconstruí a versão
pré-E6-AUD7-002V-001 revertendo as 2 edições desta sessão (a seção nova da docstring e
o branch de código) e comparei com `difflib.unified_diff`: **15 linhas adicionadas, 2
removidas**, inteiramente dentro de `redact_document` — nenhuma outra função tocada.

### Estado final

* **`E6-AUD7-001 VERIFIED RESOLVED — FROZEN`**
* **`E6-AUD7-002V-001 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`**
* **`E6-AUD7-002 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`**
* **NO PEM CHANGES.** Nada commitado. Nada pushado.

## 2026-09-22 — Claude Sonnet 5 — E6: fechamento documental de E6-AUD7-002 (GREEN persistido, §5 atualizada)

Escopo **exclusivo**: fechamento documental. Nenhum código, teste ou frontend alterado.

Relatório GREEN do Codex persistido verbatim em `docs/audits/e6-aud7-002-round-2.md`.
Veredito: nenhum finding novo; contêiner vazio sob chave sensível confirmado redigido
em 300 árvores geradas e um caso de 24 níveis; escape hatches (`Unredacted`,
`EditViewJSONResponse`) delimitados; 36 testes AUD7-002 + 671 focados + suíte completa
(1826 passed, 6 skipped) verdes.

Atualização pontual, expressamente autorizada por Pedro nesta sessão, em
`docs/architecture/04-safety-and-git-runtime.md` §5: o texto original das duas
"garantias abertas" (E6-AUD7-001, E6-AUD7-002) foi **preservado sem alteração**, como
histórico de quando estavam abertas; acrescentei um bloco "Decisão de fechamento"
logo depois, registrando as duas como `VERIFIED RESOLVED` com referência aos quatro
relatórios de E6-AUD7-001 (`e6-round-7*.md`) e aos dois de E6-AUD7-002
(`e6-aud7-002-round-1.md`, `round-2.md`), e uma linha explícita de que a aprovação da
E6 inteira continua pendente de auditoria consolidada — nenhum commit ou merge
autorizado por esta decisão isolada. As duas exceções JSON deliberadas (`purge_token`/
`Unredacted`, `EditViewJSONResponse`), documentadas em outro trecho de §5 (linhas
313-319), não foram tocadas. Nenhuma outra decisão normativa do documento foi
modificada.

Estados verificados:

* **`E6-AUD7-001 VERIFIED RESOLVED — FROZEN`**
* **`E6-AUD7-002V-001 VERIFIED RESOLVED`**
* **`E6-AUD7-002 VERIFIED RESOLVED`**
* A aprovação da E6 inteira permanece pendente de auditoria consolidada.
* Nenhum código, teste ou frontend alterado nesta tarefa. Nada commitado, nada pushado.

## 2026-09-22 — Claude Sonnet 5 — E6: auditoria consolidada do Codex persistida (E6-CONS-001 OPEN)

Relatório integral da auditoria consolidada persistido verbatim em
`docs/audits/e6-consolidated-round-1.md`. Confirma AUD7-001 e AUD7-002 verificados
(scanner PEM equivalente à expressão canônica em 1.200 entradas sintéticas; Context
Registry mascarando valores e contêineres sensíveis via POST/listagem real, com
`edit-view`/`purge_token` preservados) e a máquina de estados estável (planejamento,
aprovação, `requires_replan` por HEAD alterado, retorno a `draft` após rejeição).
Backend completo, ruff, mypy e frontend (lint/testes/build) verdes.

Novo finding: **E6-CONS-001 — Média/P2 — detalhe de tarefa cancelada orienta uma ação
impossível.** `WorkspaceTasks.tsx:214` busca contexto para todo status ≠ `draft`; uma
tarefa cancelada ainda em `draft` não tem contexto congelado, então a consulta devolve
`404 task_not_found` com orientação para `POST /plan` — que responde `409
invalid_transition`, pois `cancelled` é terminal. A tela apresenta erro e uma ação que
o backend proíbe.

**E6-CONS-001 OPEN.** Prosseguindo com a correção concentrada.

## 2026-09-22 — Claude Sonnet 5 — E6: E6-CONS-001 corrigido — contexto de task cancelada não instrui mais `POST /plan`

Escopo **exclusivo**: `E6-CONS-001` (P2, detalhe de tarefa cancelada orientava uma ação
impossível). Máquina de estados **intocada** — `TRANSITIONS`/`TERMINAL_STATUSES` de
`state_machine.py` não aparecem no diff desta correção (verificado por grep). Nenhum
escopo congelado tocado (PEM, redaction, escape hatches, Gate 1, tripletas, Analyzer,
Router, Planner). Nada commitado, nada pushado.

### Reprodução ANTES da mudança (API real)

* Task criada (`draft`) → `POST /cancel` → `200`, `status: "cancelled"`,
  `approval_state: "not_planned"`.
* `GET /tasks/{id}/context` → `404 task_not_found`, mensagem "ainda não tem contexto
  congelado; rode `POST /plan` antes".
* `POST /tasks/{id}/plan` na mesma task → `409 invalid_transition`, "`cancelled` é
  terminal e imutável ([ADR-0008] regra 3)".
* Confirmado também o caso **preservado**: task planejada e depois cancelada
  (`approved_manifest_id` presente antes do cancel) → `approval_state` também vira
  `not_planned` após o cancelamento (o campo computado não distingue os dois casos),
  mas `GET /context` continua `200` com o manifest — a causa não podia ser "olhar
  `approval_state`/`status` no frontend", tinha de ser o resultado real do fetch.
* Task inexistente: `GET /tasks/nao-existe` e `GET /tasks/{id}/context` → `404
  task_not_found`, comportamento inalterado.

### Causa raiz

`context_route` ([tasks.py](C:/Users/pedro/OneDrive/Desktop/freelance-focus-dashboard/api/app/api/tasks.py)),
quando `manifest is None`, só diferenciava `requires_replan` (E6-AUD2-004) do caso
genérico "ainda não planejada, rode `POST /plan`" — nunca checava se `task.status` era
**terminal**. `draft → cancelled` é uma aresta direta da tabela de transições (nunca
passa por `planning`), então essa task cai no `manifest is None` sem nunca ter tido
chance de ter um manifest, e a mensagem genérica manda uma ação que `POST /plan`
recusaria com `409` (estado terminal é imutável).

### Contrato implementado e representação escolhida

Reaproveitei o idioma já estabelecido, sem inventar formato novo: `OrchestratorError.
as_payload()` já permite campos extra estruturados (`TransitionGuardFailed.
requires_replan`, `ApprovalFingerprintMismatch.diverged_fields`). Acrescentei
`TaskNotFound.reason: str | None = None`, com `as_payload()` incluindo-o só quando
setado — `None` (o padrão) preserva o comportamento de "task inexistente" sem mudança
nenhuma no corpo da resposta. `context_route` passa a checar `is_terminal(task.status)`
(helper já existente em `state_machine.py`, já exportado por `app.orchestrator` —
reaproveitado, não recriado) **antes** de cair no caso genérico, e levanta
`TaskNotFound(..., reason="terminal_without_context")` com mensagem que não menciona
`POST /plan`. Os três casos (A: inexistente; B1: `requires_replan`; B2: terminal sem
contexto; B3: ainda não planejada) preservam o mesmo status `404`/código
`task_not_found` — só o `reason` estruturado (e a prosa) diferencia.

Frontend: `contextUnavailableReason()` em `tasksApi.ts`, espelhando exatamente
`enrichApprovalError()`/`ApprovalMismatchError` já existentes — lê `error.details.
reason` de um `WorkspaceApiError`, nunca o texto de `message`. `ContextCard` em
`WorkspaceTasks.tsx` ganhou o estado `'unavailable'` (renderizado como `<p
className="form-hint" role="note">`, não como `role="alert"`): quando
`contextUnavailableReason(error) === 'terminal_without_context'`, mostra "esta tarefa
foi cancelada antes de ser planejada — nenhum contexto foi congelado, e não há
planejamento a retomar", sem instruir nenhuma ação. Confirmei que `canPlan` (linha 451)
já excluía `cancelled` antes desta correção — o botão "Planejar" nunca apareceu; o
defeito era **só** o texto dentro do `ContextCard`.

### Arquivos alterados

* `api/app/orchestrator/errors.py` — `TaskNotFound` ganhou `reason` opcional.
* `api/app/api/tasks.py` — import de `is_terminal`; `context_route` ganhou o ramo
  terminal, entre `requires_replan` e o genérico.
* `src/services/tasksApi.ts` — nova `contextUnavailableReason()`.
* `src/pages/WorkspaceTasks.tsx` — `ContextCard` ganhou o estado `'unavailable'`.

### Testes (regressões novas)

Backend (`api/tests/test_api_tasks.py`, +4 testes, reforçando também o teste
pré-existente `test_context_antes_do_plano_e_404` com as asserções de `reason`
ausente):
`test_cancelada_antes_de_planejar_nao_instrui_post_plan` (reprodução completa:
cancel → 404 com `reason` → `POST /plan` → 409), `test_cancelada_depois_de_planejar_
ainda_devolve_o_manifest` (fluxo autorizado preservado), `test_task_inexistente_
continua_sem_reason`, `test_task_de_outro_workspace_continua_acessivel_por_id`
(documenta que não há isolamento por workspace neste app de usuário único — não
introduzi um).

Frontend (`src/test/task-detail-ui.test.tsx`, +3 testes): cancelada antes de planejar
mostra o estado vazio sem `role="alert"` e sem instruir `POST`/`Planejar`; cancelada
depois de planejar continua mostrando o contexto; outro motivo de 404 (sem `reason`)
continua caindo no alerta de erro comum — controle negativo provando que a nova
ramificação não captura casos que não deveria.

### Prova contra a implementação anterior

Backend: revertidos temporariamente `TaskNotFound.reason` e o ramo `is_terminal` de
`context_route` (script ad-hoc, restaurado em seguida) — `test_api_tasks.py`: **1
failed** (`test_cancelada_antes_de_planejar_nao_instrui_post_plan`, `KeyError:
'reason'`), os outros 39 passaram. Restaurado: **40 passed**.

Frontend: revertido temporariamente o estado `'unavailable'` do `ContextCard` (e o
import de `contextUnavailableReason`) — `task-detail-ui.test.tsx`: **1 failed**
(exatamente o teste novo-alvo), os outros 15 passaram, incluindo o controle negativo
("outro motivo de 404") e o caso "depois de planejar", confirmando que não dependiam
da correção. Restaurado: **16 passed**.

### Gates

| Gate | Resultado |
| --- | --- |
| `test_api_tasks.py` | 40 passed |
| Focados backend (state machine, planner, analyzer, router/fingerprint, arquitetura, tasks, workspaces) | **499 passed** |
| `task-detail-ui.test.tsx` | 16 passed |
| `ruff check .` | limpo |
| `ruff format --check .` | limpo |
| `mypy .` | limpo |
| **suíte completa do backend** | **1830 passed, 6 skipped**, `PYTEST_EXIT=0` (era 1826; +4 desta correção) |
| Frontend: suíte completa (`vitest run`) | **133 passed** (era 130; +3 desta correção) |
| Frontend: `npm run lint` | limpo |
| Frontend: `npm run build` (`tsc -b && vite build`) | limpo |
| `git diff --check` | limpo, exit 0 |

### Diff isolado

Os 6 arquivos tocados (`errors.py`, `tasks.py`, `WorkspaceTasks.tsx`, `tasksApi.ts`,
`test_api_tasks.py`, `task-detail-ui.test.tsx`) são **untracked** — carregam o
acumulado não commitado da E6 desde rodadas anteriores, e `git diff` não mostra nada
para eles. Reconstruí a versão pré-correção de cada um revertendo as edições desta
sessão e comparei com `difflib.unified_diff`: **249 linhas adicionadas, 13 removidas**
no total, isoladas por arquivo. Nenhuma menção a `TRANSITIONS`/`state_machine` no
diff — confirmado por grep.

### Estado final

* **`E6-CONS-001 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`**
* **`E6-AUD7-001 VERIFIED RESOLVED — FROZEN`**
* **`E6-AUD7-002 VERIFIED RESOLVED`**
* **E6 REMAINS BLOCKED** até a reverificação do Codex.
* Nada commitado. Nada pushado.

## 2026-09-22 — Claude Sonnet 5 — E6: reverificação BLOCKED do Codex persistida (E6-CONS2-001, E6-CONS2-002 OPEN)

Relatório integral persistido verbatim em `docs/audits/e6-consolidated-round-2.md`.
Veredito: **BLOCKED**. A correção de E6-CONS-001 funciona para o caso original
(`draft → cancelled`), mas o Codex encontrou dois estados adjacentes com a mesma classe
de defeito — orientação incorreta na ausência de contexto:

* **E6-CONS2-001 — Média/P2** — falha inesperada durante `POST /plan` deixa a task em
  `failed`, sem manifesto; `GET /context` devolve `reason=terminal_without_context`, e o
  frontend interpreta esse motivo como "cancelada" — apresentando uma causa falsa e
  ocultando a falha real.
* **E6-CONS2-002 — Média/P2** — uma task no estado durável `planning` (antes de existir
  manifesto — reconhecível por `reconcile_on_startup`, que trata exatamente isso como
  "processo morreu no meio") ainda recebe "rode `POST /plan` antes", que responderia
  `409 invalid_transition` (`planning → planning` não é aresta).

`E6-CONS-001` confirmado especificamente resolvido. `E6-AUD7-001`/`E6-AUD7-002`
continuam verificados. Backend completo 1830 passed, 6 skipped; frontend 133 passed;
gates de lint/format/mypy/build todos verdes. Nada alterado, commitado ou enviado.

**E6-CONS2-001 OPEN. E6-CONS2-002 OPEN.** Prosseguindo com a correção sistêmica —
matriz completa de estados × existência de manifesto, um único contrato para todos.

## 2026-09-22 — Claude Sonnet 5 — E6: E6-CONS2-001/002 corrigidos — matriz sistêmica de "sem contexto congelado"

Escopo: `E6-CONS2-001` e `E6-CONS2-002`, tratados juntos por serem a mesma causa (o
contrato de ausência de manifesto era incompleto, não dois defeitos independentes).
`TRANSITIONS`, PEM, redaction, `_ANCHORED`, Gate 1, tripletas, Analyzer, Router —
zero tocados (verificado por grep no diff isolado). Nada commitado, nada pushado.

### Reprodução dos dois findings ANTES da mudança

**B (falha inesperada no plan → `failed` sem manifest, E6-CONS2-001):** monkeypatch de
`app.orchestrator.execution_manager.plan_task` para levantar `RuntimeError` (mesmo
idioma de `test_aud003_erro_inesperado_no_plano_vira_failed_internal_error`, com
`pytest.raises(RuntimeError)` em volta do `POST /plan`, já que o `TestClient` reergue
exceções não tratadas por padrão). A task foi a `failed`; `GET /context` devolveu `404`
com `reason=terminal_without_context` e mensagem "foi encerrada (failed)..." —
**a mensagem do backend já estava correta** (usa `task.status.value` desde
E6-CONS-001). O finding real era só a projeção fixa "Esta tarefa foi cancelada" no
`ContextCard`, que ignorava o `status` real da task.

**C (planning durável sem manifest, E6-CONS2-002):** escrita direta na sessão de teste
(`session_factory`) setando `task.status = TaskStatus.PLANNING` sem passar pelo fluxo
normal — simula exatamente o que `reconcile_on_startup` reconhece como "processo
morreu no meio" (E6-AUD-003: a entrada em `planning` é commitada antes do trabalho
longo). `GET /context` devolveu `404` **sem** `reason`, mensagem "rode `POST /plan`
antes"; `POST /plan` na mesma task devolveu `409 invalid_transition` (`planning →
planning` não é aresta). Confirmado também: `POST /plan` continua `invalid_transition`
nos dois casos; `draft` sem manifesto continua orientando `POST /plan` corretamente;
task planejada e cancelada depois ainda consulta o manifesto (precedência preservada).

### Matriz completa (documentada na íntegra na docstring de `context_route`)

Por `status`: `draft` (manifest raro; sem manifest → ainda não planejada, `POST /plan`
permitido, sem `reason`) · `planning` (manifest raro — replan sobre manifest antigo;
sem manifest → planejamento em andamento, `POST /plan` proibido, `reason=
planning_in_progress`, **novo**) · `awaiting_approval`/`needs_fix` (decidíveis; sem
manifest → já tratado por `approval_state() == requires_replan`) · `approved`/
`executing`/`done` (sempre têm manifest pelas guardas atuais — `approve()` exige
manifest via `_recompute_fingerprint`; sem manifest é estruturalmente inalcançável,
fallback defensivo sem `reason`) · `failed`/`cancelled` (dependem de quando
falharam/cancelaram; sem manifest → `terminal_without_context`, mensagem com
`task.status.value` dinâmico).

### Causa comum

`context_route` só diferenciava `requires_replan` (E6-AUD2-004) do caso genérico "rode
`POST /plan`" — nunca checava `status is PLANNING` nem verificava se a aresta para
`PLANNING` de fato existia antes de instruir a ação. O fallback genérico presumia
`POST /plan` correto para qualquer status não coberto pelos outros ramos, violando
"não presuma que ausência de manifest autoriza `POST /plan`".

### Contrato implementado

`context_route` reestruturada: manifest existe → `200` (precedência, sempre) → senão
`requires_replan` (existente) → `is_terminal(status)` (existente, mensagem já
dinâmica) → `status is PLANNING` → **novo** `reason=planning_in_progress` → senão
`can_transition(status, PLANNING)` **verificado** (não presumido) → `POST /plan`
instruído só então → fallback final defensivo, sem `reason`, mensagem neutra.
`can_transition` e `is_terminal` são helpers **já existentes** de `state_machine.py`,
reaproveitados via `app.orchestrator` — nenhum novo mecanismo na máquina de estados.

Frontend: `ContextCard` ganhou o estado `'planning'` (`role="status"`, distinto de
`'unavailable'`/`'error'`/`'loading'`) para `reason === 'planning_in_progress'`. Para
`'terminal_without_context'`, a mensagem agora vem de `unavailableMessage(status)` —
usa o `status` **confiável** da task (já disponível como prop), não mais um texto
fixo — distinguindo `cancelled`/`failed`/outros terminais defensivamente.
`contextUnavailableReason()` (de E6-CONS-001) não precisou mudar: já lia `reason`
genericamente.

### Arquivos alterados nesta sessão

Só 4 — `api/app/orchestrator/errors.py` e `src/services/tasksApi.ts` **não** foram
tocados (confirmado por mtime: 14:xx, da tarefa CONS-001 anterior hoje; `TaskNotFound.
reason` e `contextUnavailableReason()` já existiam e não precisaram de mudança):

* `api/app/api/tasks.py` — import de `can_transition`; `context_route` reescrita.
* `src/pages/WorkspaceTasks.tsx` — `unavailableMessage()` nova; `ContextCard` ganhou
  o estado `'planning'`.
* `api/tests/test_api_tasks.py` — +6 testes (2 detalhados + matriz parametrizada de 4
  casos); removidos 2 imports locais de `Session, sessionmaker` que ficaram
  redundantes com o novo import de módulo.
* `src/test/task-detail-ui.test.tsx` — +3 testes.

### Prova contra a implementação anterior

Backend: revertida só a lógica de `context_route` (docstring mantida) — **2 failed**,
ambos do caso `planning_in_progress` (`test_planning_sem_manifesto_nao_instrui_novo_
post_plan` e a variante `planning` da matriz parametrizada) — confirma que
E6-CONS2-001 já não dependia de mudança de backend. Restaurado: **46 passed**.

Frontend: revertido `unavailableMessage`/estado `'planning'` do `ContextCard` — **2
failed**, exatamente `'falha de planejamento não é apresentada como cancelamento'` e
`'planning em andamento não instrui novo POST /plan'` — os dois findings, e só eles.
Restaurado: **19 passed**.

### Gates

| Gate | Resultado |
| --- | --- |
| `test_api_tasks.py` | 46 passed |
| Focados backend (state machine, planner, analyzer, router/fingerprint, arquitetura, tasks, workspaces, E6 round 1-2) | **561 passed** |
| `task-detail-ui.test.tsx` | 19 passed |
| `ruff check .` | limpo |
| `ruff format --check .` | limpo |
| `mypy .` | limpo |
| **suíte completa do backend** | **1836 passed, 6 skipped**, `PYTEST_EXIT=0` (era 1830; +6 desta correção) |
| Frontend: suíte completa (`vitest run`) | **136 passed** (era 133; +3 desta correção) |
| Frontend: `npm run lint` | limpo |
| Frontend: `npm run build` | limpo |
| `git diff --check` | limpo, exit 0 |

### Diff isolado

`git diff` não mostra nada para os 4 arquivos tocados (todos untracked, acumulado da
E6 não commitado). Reconstruí a versão pré-CONS2 de cada um revertendo as edições
desta sessão e comparei com `difflib.unified_diff`: **390 linhas adicionadas, 51
removidas**, isoladas por arquivo. Grep confirma zero linhas de `TRANSITIONS`, PEM,
`redaction`/`redact_document`, `_ANCHORED`, tripletas, Analyzer ou Router no diff.

### Estado final

* **`E6-CONS-001 VERIFIED RESOLVED`**
* **`E6-CONS2-001 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`**
* **`E6-CONS2-002 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`**
* **`E6-AUD7-001 VERIFIED RESOLVED — FROZEN`**
* **`E6-AUD7-002 VERIFIED RESOLVED`**
* **E6 REMAINS BLOCKED** até a reverificação independente.
* Nada commitado. Nada pushado.

## 2026-09-22 19:14 — E6-CONS3: auditoria consolidada rodada 3 (Codex) — três findings abertos

Relatório persistido verbatim em `docs/audits/e6-consolidated-round-3.md`.

Confirma E6-CONS2-001 e E6-CONS2-002 RESOLVED. Três findings novos abertos:

* **E6-CONS3-001 OPEN** (Média/P2) — tarefa `draft` em workspace arquivado: `/context`
  orienta `POST /plan`, mas o endpoint responde 409 `workspace_not_plannable`; a tela
  também oferece "Planejar" sem checar o workspace.
* **E6-CONS3-002 OPEN** (Baixa/P3) — `needs_fix` sem manifesto (banco inconsistente):
  `/context` recomenda `reject` (que responde 409 `invalid_transition`), quando
  `POST /plan` direto é aceito (200).
  Falha do fluxo real da E6.
* **E6-CONS3-003 OPEN** (Baixa/P3) — tarefa que volta a `draft` após falha recuperável
  no replanejamento, preservando `approved_manifest_id`: `/context` responde 200 com
  o manifesto histórico, mas o `ContextCard` nunca consulta a API quando o status é
  `draft`.

Investigação e correção em andamento nesta entrada.

## 2026-09-22 19:57 — E6: E6-CONS3-001/002/003 corrigidos — contrato sistêmico de disponibilidade de contexto e ações

### Reprodução (antes de editar)

* **E6-CONS3-001** — task `draft` (fixture `workspace_id`/`task_id`), workspace arquivado via
  `PATCH /api/workspaces/{id}` `{"status":"archived"}`. `GET /context` respondia `404` sem
  `reason`, mensagem "rode `POST /plan` antes"; `POST /plan` respondia **`409
  workspace_not_plannable`** (guarda de `plan()`, independente de `can_transition`).
* **E6-CONS3-002** — `needs_fix` sem manifest, forçado via escrita direta no banco
  (`session_factory`, mesma técnica de `_forcar_planning_sem_manifesto`): `approval_state()`
  classificava como `requires_replan` (mesmo teste de coluna que `awaiting_approval` usa) e
  `context_route` instruía "rejeite (`POST /reject`)". `POST /reject` respondia **`409
  invalid_transition`** (`TRANSITIONS[needs_fix]` não tem `draft`); `POST /plan` direto
  respondia `200` (`needs_fix → planning` é aresta).
* **E6-CONS3-003** — plan → força `needs_fix` (mantendo `approved_manifest_id`) →
  monkeypatch de `plan_task` para levantar `InvalidTestConfig` (erro de domínio) → `POST
  /plan`: `_abort_planning` aplica o desfecho "erro recuperável" (`planning → draft`), que
  usa `transition_fields(draft)` — **não** toca `approved_manifest_id`. Task volta a `draft`
  com o manifest antigo intacto; `GET /context` já respondia `200` (o backend sempre
  preferiu o manifest), mas `ContextCard` nunca chamava a API quando `status === 'draft'` —
  a tela mostrava "Planeje a tarefa..." escondendo um manifest que existia.

Todas as três reproduzidas com sucesso antes de qualquer edição.

### Matriz e causa sistêmica

Os três findings compartilham uma causa: **duas confusões repetidas em pontos diferentes**.

1. "A aresta existe na máquina de estados" ≠ "o comando aceitaria a chamada agora".
   `can_transition(status, planning)` só confere `TRANSITIONS`; não confere a guarda extra
   de `plan()` (workspace arquivado) nem a de `reject()` (`needs_fix → draft` não é aresta,
   então nem chega a ter guarda — é `InvalidTransition`, mas o efeito prático é o mesmo:
   "recomendar uma ação que o backend recusa").
2. "`status === 'draft'`" ≠ "sem contexto congelado". Um replan recuperável não limpa
   `approved_manifest_id`; só `reject()` e a invalidação de entrada (`_invalidate_approval_
   on_entry`, que só escreve em `awaiting_approval`) limpam esse campo.

Matriz de `GET /context` sem manifest, revisada nesta rodada (substitui a de E6-CONS2):

| status | pode ter manifest? | sem manifest → reason |
| --- | --- | --- |
| `draft` | sim (replan recuperável) | `workspace_archived` se arquivado; senão nenhum |
| `planning` | raro | `planning_in_progress` |
| `awaiting_approval` | sim, exceto invalidação | nenhum (requires_replan + `can_transition(status,draft)`=true → mensagem de reject) |
| `needs_fix` | sim, herda; sem manifest só com banco inconsistente | mesmo ramo de `draft` (nunca "rejeite" — `can_transition(needs_fix,draft)`=false) |
| `approved`/`executing` | sim, sempre | fallback neutro, sem reason (inalcançável) |
| `done`/`failed`/`cancelled` | depende de quando | `terminal_without_context` |

Tarefa inexistente: `404` sem `reason` (inalterado). Sem token: `401` (inalterado).

### Contrato implementado

* **A) Existência do manifesto** — `context_route` já dava precedência ao manifest
  independente do `status` (nada a mudar no backend); o frontend deixou de presumir
  "`draft` = sem contexto": `ContextCard` consulta a API **sempre**, sem atalho por status.
* **B) Ausência do manifesto** — `context_route` reestruturado: a recomendação de "rejeite"
  só sai quando `can_transition(status, draft)` é verdadeiro (hoje só `awaiting_approval`);
  a recomendação de "`POST /plan`" só sai quando, **além** de `can_transition(status,
  planning)`, o workspace não está arquivado — novo `reason="workspace_archived"` quando
  está. `needs_fix` sem manifest cai no mesmo ramo de `draft` (nunca no de "rejeite").
* **C) Ações** — nenhuma guarda de `TRANSITIONS`/`state_machine.py` foi alterada; a correção
  é só a *leitura* combinada de `can_transition` + guarda de workspace + guarda de reject,
  em `context_route`. `PlanCard`/`ApprovalCard` ganharam `requiresReplanByRejecting(task)`
  (== `approval_state === 'requires_replan' && status === 'awaiting_approval'`), substituindo
  o teste antigo que olhava só `approval_state`.
* **D) Workspace arquivado** — `TaskResponse` ganhou `workspace_archived: bool` (lido via
  `get_workspace` a cada resposta — `_to_response` agora recebe `session`); `ApprovalCard`
  não oferece "Planejar" quando `workspace_archived` é `true` para `draft`/`needs_fix`, e
  mostra um aviso pedindo reativação. O `409` do backend continua a fonte de verdade — o
  campo só evita prometer uma ação na tela que o backend já recusaria.

### Arquivos alterados (5, todos untracked — diff isolado reconstruído por
`difflib.unified_diff` revertendo cada edição desta sessão, técnica de `isolar_diff_cons2.py`)

* `api/app/api/tasks.py` — import de `get_workspace`/`WorkspaceStatus`; `TaskResponse.
  workspace_archived`; `_to_response(session, task)` (6 call sites atualizados);
  `context_route` reescrita (guarda de `can_transition(status, draft)` antes de recomendar
  reject; guarda de workspace arquivado antes de recomendar `POST /plan`); docstring da
  matriz atualizada.
* `src/pages/WorkspaceTasks.tsx` — `requiresReplanByRejecting()` nova (usada em `PlanCard` e
  `ApprovalCard`); `ContextCard` sempre consulta a API (removido o atalho de `draft`, e o
  estado `'idle'`); `ApprovalCard` ganhou `workspaceBlocksPlanning`/`planBlockedByWorkspace`
  e o aviso `workspace-archived-notice`.
* `src/services/tasksApi.ts` — `Task.workspace_archived: boolean`.
* `api/tests/test_api_tasks.py` — +5 testes (`test_workspace_arquivado_nao_promete_post_
  plan_em_draft_sem_contexto`, `test_workspace_ativo_task_recebe_workspace_archived_false`,
  `test_needs_fix_sem_manifesto_nao_instrui_rejeitar` + helper `_forcar_needs_fix_sem_
  manifesto`, `test_needs_fix_com_manifesto_ainda_devolve_o_contexto`, `test_draft_com_
  manifesto_historico_apos_replan_recuperavel_devolve_o_contexto`).
* `src/test/task-detail-ui.test.tsx` — `taskFixture.workspace_archived: false`; +4 testes
  (manifesto histórico em `draft`, workspace arquivado bloqueia Planejar, workspace ativo
  não bloqueia, `needs_fix`+`requires_replan` oferece Planejar sem StalePlanNotice/reject).

### Prova contra a implementação anterior

Backend: revertidas as duas linhas de guarda em `context_route` (voltando ao `if estado ==
APPROVAL_STATE_REQUIRES_REPLAN:` sem `and can_transition(...)`, e ao `POST /plan` sem checar
workspace arquivado) — **2 failed**, exatamente `test_workspace_arquivado_nao_promete_
post_plan_em_draft_sem_contexto` (sem `reason`) e `test_needs_fix_sem_manifesto_nao_
instrui_rejeitar` (mensagem continha "rejeite"). Restaurado: **51 passed** em
`test_api_tasks.py`.

Frontend: revertidos o atalho de `draft` em `ContextCard`, `requiresReplanByRejecting` (nos
dois usos) e os campos derivados de `workspace_archived` em `ApprovalCard` — **3 failed**,
exatamente os três testes novos que dependem dessas mudanças (manifesto histórico,
workspace arquivado, `needs_fix`+`requires_replan`); o quarto teste novo (workspace ativo,
que não depende da correção) continuou passando — **20 passed, 3 failed**. Restaurado:
**23 passed** em `task-detail-ui.test.tsx`.

### Gates

| Gate | Resultado |
| --- | --- |
| `test_api_tasks.py` | 51 passed |
| Focados backend (tasks, workspaces, E6 round 1-3, state machine, arquitetura) | 431 passed |
| `task-detail-ui.test.tsx` | 23 passed |
| `ruff check .` | limpo |
| `ruff format --check .` | limpo |
| `mypy .` | limpo (106 arquivos) |
| Suíte completa do backend | **1841 passed, 6 skipped**, `PYTEST_EXIT=0` (era 1836; +5) |
| Frontend: suíte completa (`vitest run`) | **140 passed** (era 136; +4) |
| Frontend: `npm run lint` | limpo |
| Frontend: `npm run build` | limpo |
| `git diff --check` | limpo, exit 0 |

### Diff isolado

Os 5 arquivos são untracked (`git diff` não mostra nada). Reconstruí a versão pré-rodada de
cada um revertendo as edições desta sessão em ordem inversa (`difflib.unified_diff`): **370
linhas adicionadas, 46 removidas** (contando cabeçalhos `+++`/`---`), isoladas por arquivo.
Grep confirma zero linhas de `TRANSITIONS=`, PEM, `_ANCHORED`, tripletas, Analyzer ou
Resource Router no diff — a única menção de `redact_document` é uma linha de contexto
inalterada.

### Estado final

* **`E6-CONS-001 VERIFIED RESOLVED`**
* **`E6-CONS2-001 VERIFIED RESOLVED`**
* **`E6-CONS2-002 VERIFIED RESOLVED`**
* **`E6-CONS3-001 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`**
* **`E6-CONS3-002 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`**
* **`E6-CONS3-003 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`**
* **`E6-AUD7-001 VERIFIED RESOLVED — FROZEN`**
* **`E6-AUD7-002 VERIFIED RESOLVED`**
* **E6 REMAINS BLOCKED** até nova verificação independente.
* Nada commitado. Nada pushado.

## 2026-09-22 — E6-CONS4: auditoria consolidada rodada 4 — diagnóstico (sem implementação)

Relatório original do Codex **não veio anexado** ao prompt desta etapa:
`docs/audits/e6-consolidated-round-4.md` **não foi criado** (reconstruir é proibido) —
pendente do texto original.

* **E6-CONS4-001 OPEN** — `requires_replan` + workspace arquivado: `/context` orienta
  rejeitar e replanejar; `reject` funciona, `plan` responde 409 `workspace_not_plannable`.
* **E6-CONS4-002 OPEN** — `draft` em workspace ativo sem git / sem HEAD / com o diretório
  removido: `/context` orienta `POST /plan`, que responde 409 `workspace_not_plannable`.
* **E6-CONS4-003 OPEN** — `draft` genuína sem manifest: o 404 normal vira `role=alert`; o
  corpo é estruturalmente idêntico ao 404 de task inexistente (sem `reason`).
* **E6-CONS4-004 OPEN** — manifest preservado em `draft` após replan recuperável aparece
  como "Contexto entregue" sem marca de histórico.

Os quatro reproduzidos contra a API real (banco, data_dir e repos temporários no
scratchpad; nenhum arquivo do repo tocado além deste log). Nenhum código de produção ou teste
alterado. Proposta de contrato entregue na conversa, aguardando autorização.
E6 BLOCKED. Nada commitado. Nada pushado.

## 2026-09-23 — E6-CONS4: implementação estrutural — elegibilidade de planejamento, `plan_standing`, `reason` de `/context`

Modelo: Claude Opus 5.5 · Esforço: HIGH · Autorização: D (não E), D1 (adendo em `docs/architecture/06`),
D2-A (preservar plano e manifest, apresentar como histórico), D3 (distinguir git inválido de não verificável).

**Relatório persistido.** O original do Codex veio com o prompt desta etapa e foi gravado verbatim em
`docs/audits/e6-consolidated-round-4.md` — fecha a pendência registrada na entrada de diagnóstico acima.

### Causa estrutural

A orientação "planeje" era calculada em quatro lugares (texto de `/context`, `canPlan`, `StalePlanNotice`,
banner de `requires_replan`), cada um com parte das guardas reais de `POST /plan`. Cada rodada consolidada
achou a guarda que faltava em um deles. Além disso, o 404 de draft sem plano e o de task inexistente eram
estruturalmente idênticos (sem `reason`).

### Contrato implementado

* **`git_runtime.probe_head`** (D3): 2 `rev-parse` (3 com `symbolic-ref`), sem `status`, somente leitura,
  timeout fixo de 5 s por leitura. Estados `ok` / `not_a_repo` / `no_head` / `unverifiable` (git ausente,
  timeout, IO). `preflight` **não** foi alterado.
* **`planner`**: slugs de bloqueio e uma função por guarda — `workspace_planning_blocker`,
  `classify_git`/`git_planning_blocker`, `invalid_test_config_blocker`, `planning_blocker_error`.
  `_resolve_base_commit` usa `probe_head` + `classify_git`; `execution_manager.plan()` e `create_task` usam o
  mesmo classificador de arquivamento. `WorkspaceNotPlannable` e `InvalidTestConfig` trazem `reason`.
* **`orchestrator/eligibility.py`** (novo): `planning_eligibility(session, task)` — só leitura, chama as
  mesmas funções do Planner. `transition` (`allowed`/`after_reject`/`forbidden`), `checked`, `blockers`,
  `eligible`. Git consultado só com `allowed` ou `after_reject`+`requires_replan`; `forbidden` e
  `after_reject` com plano vigente não consultam (`checked=false`, lista parcial).
* **`TaskResponse.planning`** substitui `workspace_archived` (E6-CONS3); **`TaskResponse.plan_standing`**
  (`none`/`current`/`historical`/`final`, puro, em `execution_manager`). Listagem **sem** `planning`.
* **`/context`**: todo 404 de task existente tem `reason` (`not_planned`, `approval_invalidated`,
  `planning_in_progress`, `terminal_without_context`, `unavailable_in_state`); task inexistente sem `reason`.
  Mensagens descrevem, não ensinam comandos. A rota não consulta git.
* **Frontend**: `canPlan` = `planning.eligible && checked && allowed`; `PlanBlockers` explica pré-condições
  (reativação aponta para o botão "Reativar" existente no cabeçalho); `requires_replan` separa "rejeitar
  agora" de "planejar depois"; `PlanStandingNotice` substitui `StalePlanNotice`; `ContextCard` com estado
  `empty` para `not_planned`/`approval_invalidated` e título por `plan_standing`; fingerprint "vigente" só
  com `current`; recarga da task após 409/422 de pré-condição (`isPlanPreconditionRefusal` — concorrência,
  401 e 5xx fora); `TaskDetail` relê uma vez quando `workspaceStatus` muda.
* **`docs/architecture/06`**: adendo autorizado em §4, após o quadro de `approval_state`, texto anterior
  preservado.

### Custo real das sondagens git

GET de detalhe: 2 leituras em `draft`/`needs_fix` e em `requires_replan`; 0 nos demais status. Listagem: 0
(teste com 12 tasks). `/context`: 0. Respostas de `create`/`reject` (draft): 2. `POST /plan`: o Planner passou
de 4 leituras (`preflight`) para 3 (`probe_head` com branch). Pior caso por GET de detalhe: 10 s de timeout.

### Regressões antes/depois

Arquivos de produção trocados pela cópia pré-rodada, testes novos mantidos. Backend: as 8 regressões dos
quatro findings (`test_cons4_001…`, 5 variantes de `test_cons4_002…`, `test_cons4_003…`, `test_cons4_004…`)
**falham** — sem `planning`/`reason`/`plan_standing`. Frontend: **19 de 43 falham**, entre elas o 404 real de
draft (alerta), o manifest histórico sem marca, `requires_replan`+arquivado e os bloqueios git. Restaurado
(cópia byte a byte conferida por `cmp`): tudo passa.

### Testes alterados por mudança deliberada de contrato

`test_api_tasks.py`: `test_context_antes_do_plano_e_404`, matriz (`draft` agora `not_planned`), os três
testes da CONS3 migrados (`test_workspace_ativo_task_recebe_workspace_archived_false` virou
`test_workspace_ativo_draft_e_elegivel`). `test_e6_audit_round_2.py::test_o_contexto_404_explica_o_motivo_certo`
(o caminho real agora é `reason` + `planning`). `task-detail-ui.test.tsx`: fixtures consistentes com o
backend; o teste "outro motivo de 404" agora usa o corpo real de task inexistente.

### Gates

| Gate | Resultado |
| --- | --- |
| `test_e6_cons4.py` + `test_api_tasks.py` + `test_e6_audit_round_2.py` | 103 passed, exit 0 |
| Backend completo | **1871 passed, 6 skipped**, `PYTEST_EXIT=0` (era 1841; +28 CONS4, +2 `test_architecture` sobre o módulo novo) |
| `ruff check .` / `ruff format --check .` / `mypy` | exit 0 / 0 / 0 (108 arquivos) |
| `task-detail-ui.test.tsx` | 43 passed (era 23) |
| Frontend completo | **160 passed**, exit 0 (era 140) |
| `npm run lint` / `npm run build` | exit 0 / 0 |
| `git status` | 85 entradas: 40 rastreados modificados, 45 untracked |
| `git diff --check` | exit 0 |

### Diff isolado

Cópia literal pré-edição (`cons4_before/`) diffada contra o atual; novos contra vazio; `test_e6_audit_round_2.py`
reconstruído revertendo sua única edição. **16 arquivos, +2041 / -364.** Conferido contra `git diff` nos
rastreados sem mudança anterior: `git_runtime/__init__.py` 61/0 e `docs/architecture/06` 59/0, idênticos.
Nenhuma linha alterada toca `TRANSITIONS`, PEM, redaction, `_ANCHORED`, Gate 1, benchmarks, Analyzer ou
Resource Router.

### Riscos residuais

* **Registrado separadamente, não corrigido:** `ContextError` durante `plan_task` depois de `probe_head`
  (ex.: `ContextTreeUnavailable` de `list_tree`) não é `OrchestratorError`, e `_abort_planning` leva a task
  a `failed(internal_error)` — terminal — com resposta 409. Sugerido como tarefa à parte.
* `eligible` é fotografia: concorrência, IO e árvore ilegível continuam possíveis com `eligible=true`.
* Diretório removido aparece como `workspace_not_git_repo` (o git roda e recusa o `-C`), não como bloqueio
  próprio.
* E7: se a execução real deixar `approved_at` preenchido até um rollback para `draft`, `approval_state`
  diria `approved` para uma draft; `plan_standing` já diria `historical`.

### Estado

* `E6-CONS3-001/002/003` — correções específicas verificadas
* **`E6-CONS4-001 CANDIDATE RESOLVED`**
* **`E6-CONS4-002 CANDIDATE RESOLVED`**
* **`E6-CONS4-003 CANDIDATE RESOLVED`**
* **`E6-CONS4-004 CANDIDATE RESOLVED`**
* **E6 REMAINS BLOCKED** até auditoria independente.
* Nada commitado. Nada pushado.

## 2026-09-23 — E6-CONS5: auditoria consolidada rodada 5 persistida e três findings corrigidos

Relatório BLOCKED do Codex persistido verbatim em `docs/audits/e6-consolidated-round-5.md`. A
arquitetura estrutural CONS4 (elegibilidade compartilhada, `plan_standing`, `reason` de
`/context`) foi preservada; esta rodada corrige exclusivamente os três findings comprovados.

### Os três findings

* **E6-CONS5-001** (Média/P2) — `probe_head` classificava toda falha de leitura Git não-zero
  como `not_a_repo` (config corrompida) ou `no_head` (referência de HEAD corrompida após um
  commit real), porque um retorno não-zero do git é indistinguível, por si, de "nunca foi
  repositório" ou "nunca teve commit" — mesmo código de saída, mesmo stdout vazio, e às
  vezes o **mesmo stderr** (uma referência de HEAD com conteúdo inválido faz até
  `--is-inside-work-tree` falhar com "fatal: not a git repository", idêntico ao caso real).
* **E6-CONS5-002** (Baixa/P3) — uma perda de CAS (`409 concurrent_task_update`) não estava
  na lista de recusas que disparam releitura (`PLAN_REFUSALS`/`isPlanPreconditionRefusal`
  em `src/services/tasksApi.ts`), então `ApprovalCard.run()` mostrava o alerta mas nunca
  relia a task — o botão "Planejar" ficava obsoleto na tela.
* **E6-CONS5-003** (Baixa/P3) — `PlanStandingNotice` (`src/pages/WorkspaceTasks.tsx`)
  afirmava "o HEAD do repositório mudou" só a partir de `approval_state === 'requires_replan'`,
  sem checar `planning.transition`. Essa combinação de colunas também é alcançável (hoje só
  por escrita direta no banco) num `needs_fix` sem manifesto onde o HEAD nunca mudou —
  `replanRequiresReject` (já existente, usado em `ApprovalCard`) já distinguia os dois casos
  e não estava sendo reaproveitado aqui.

### Reprodução (antes da correção)

Cópias íntegras de `api/app/git_runtime/__init__.py`, `src/services/tasksApi.ts`,
`src/pages/WorkspaceTasks.tsx`, `src/test/task-detail-ui.test.tsx` e `AGENT_LOG.md` salvas
em `cons5_before/` **antes** de qualquer edição desta rodada.

* Repositório temporário com commit válido, `.git/config` corrompido (`isto nao e um ini
  valido [[[ lixo`): `probe_head` devolvia `not_a_repo` (esperado: `unverifiable`).
* Mesmo repositório, `.git/HEAD` com conteúdo que não é ref nem SHA: `probe_head` devolvia
  `not_a_repo` — porque até `rev-parse --is-inside-work-tree` falha nesse caso.
* Mesmo repositório, `.git/HEAD` reescrito para `ref: refs/heads/inexistente` (referência
  apontando a um branch que não existe mais): `probe_head` devolvia `no_head`, idêntico ao
  de um repositório recém-criado sem nenhum commit.
* Com o código anterior de `git_runtime/__init__.py` reintroduzido, `api/tests/test_e6_cons5.py`
  reproduz os três: **7 de 13 testes falham** — as três variantes do teste parametrizado de
  contrato, e as três variantes do teste unitário de `probe_head`, mais o teste de
  não-vazamento de detalhe interno (que também assumia a classificação errada).
* Com o `WorkspaceTasks.tsx`/`tasksApi.ts` anteriores reintroduzidos, `task-detail-ui.test.tsx`
  reproduz os outros dois: **2 de 44 falham** — o teste novo de conflito concorrente (o botão
  "Planejar" continua na tela após o 409) e o teste novo de `needs_fix` (o aviso afirma
  "HEAD" quando não deveria). Os dois arquivos foram restaurados e conferidos byte a byte
  (`cmp`) antes de prosseguir.

### Causa raiz e correção

**CONS5-001.** `probe_head` (`api/app/git_runtime/__init__.py`) agora distingue "confirmado"
de "não deu para confirmar" com um segundo sinal, **só de sistema de arquivos** — nunca de
texto de stderr, que é localizável e muda entre versões do git:

* `_resolve_git_dir(local_path)`: existe `.git` (arquivo ou diretório) em `local_path`? Segue
  o `gitdir:` de um worktree/submódulo por completude.
* `_has_git_marker`: `not_a_repo` (nenhum `.git`) vs `unverifiable` (`.git` existe, mas o git
  falhou ao rodar `--is-inside-work-tree`).
* `_has_head_reflog`: `.git/logs/HEAD` só é criado na primeira atualização de referência —
  ausência confirma "nunca teve commit" (`no_head`); presença, com `rev-parse HEAD` falhando
  agora, confirma que a referência existiu e foi corrompida ou apagada depois
  (`unverifiable`, não `no_head`).

`classify_git`/`git_planning_blocker` (`api/app/orchestrator/planner.py`) **não mudaram**: já
eram um mapeamento puro de `HeadProbe.state` para o slug de bloqueio, então corrigir a fonte
única corrige o Planner e a elegibilidade ao mesmo tempo — nenhuma segunda interpretação foi
criada. Custo: a checagem extra só roda no caminho de **falha** do git (nunca no caminho
feliz), é só `os.path.exists`/leitura de um arquivo pequeno, sem subprocess novo — a sondagem
continua 2–3 leituras de git por chamada, como antes.

**CONS5-002.** `isConcurrentTaskConflict` (`src/services/tasksApi.ts`), separado de
`isPlanPreconditionRefusal` de propósito: perda de CAS não é uma pré-condição de
planejamento (o campo que mudou pode ser qualquer um), mas ainda é motivo de releitura.
`ApprovalCard.run()` (`src/pages/WorkspaceTasks.tsx`) chama `onChanged()` para os dois casos,
preservando a mensagem de erro já definida (o componente não desmonta).

**CONS5-003.** `PlanStandingNotice` troca `task.approval_state === 'requires_replan'` por
`replanRequiresReject(task)` — a mesma função que já decide, em `ApprovalCard`, se rejeitar é
o próximo passo. A frase específica de HEAD só aparece quando `planning.transition ===
'after_reject'` também é verdadeiro (a assinatura de `_invalidate_approval_on_entry`); as
demais combinações caem no aviso histórico genérico já existente, que não menciona HEAD.
Nenhum campo público novo foi criado.

### Testes contra o código anterior e o corrigido

* `api/tests/test_e6_cons5.py` (novo, 13 testes): unitários de `probe_head` para os 5
  cenários pedidos (não é repo, sem primeiro commit, config corrompida, HEAD com conteúdo
  inválido, referência de HEAD corrompida após commit) mais os 2 cenários já cobertos por
  CONS4 (diretório removido, git ausente do PATH) para não regredir; um teste parametrizado
  de contrato comparando `planning.blockers` de `GET /tasks/{id}` com `reason` de
  `POST /plan` nos três tipos de corrupção; um teste de não-vazamento (sem caminho, sem
  `fatal:`, sem `Traceback`, sem `config` na resposta pública).
* `src/test/task-detail-ui.test.tsx`: um teste novo de conflito concorrente contando GETs (o
  handler do `POST /plan` devolve `409 concurrent_task_update` e alterna a task que o próximo
  `GET` devolve, provando que a tela leu de novo e não repetiu a resposta antiga — exatamente
  2 GETs, alerta preservado, botão "Planejar" substituído pelo bloqueio novo); a variante
  "concorrência" foi removida do `it.each` que testava "não relê" (ela relê agora, de
  propósito); um teste novo de `needs_fix`+`requires_replan` que verifica a AUSÊNCIA da
  palavra "HEAD" no aviso; o teste existente de `awaiting_approval`+`requires_replan` ganhou
  uma asserção positiva confirmando que "HEAD" continua aparecendo nesse caso (o único
  comprovado).

### Gates

| Gate | Resultado |
| --- | --- |
| `test_e6_cons5.py` | 13 passed, exit 0 |
| Backend completo | **1884 passed, 6 skipped**, `PYTEST_EXIT=0` (era 1871; +13 CONS5) |
| `ruff check .` / `ruff format --check .` / `mypy` | exit 0 / 0 / 0 (109 arquivos) |
| `task-detail-ui.test.tsx` | 44 passed (era 43) |
| Frontend completo | **161 passed**, exit 0 (era 160) |
| `npm run lint` / `npm run build` | exit 0 / 0 |
| `git status --porcelain=v1 --untracked-files=all` | 87 entradas: 40 rastreados modificados, 47 untracked |
| `git diff --stat` | 40 arquivos, +5970/-197 (acumulado desde HEAD, já incluindo este append) |
| `git diff --check` | exit 0 (só aviso de CRLF em `AGENT_LOG.md`, sem erro) |

### Diff isolado

Cópia literal pré-edição desta rodada (`cons5_before/`) diffada contra o atual; os dois
arquivos novos (`test_e6_cons5.py`, `e6-consolidated-round-5.md`) contra vazio. **5 arquivos
tocados + 2 novos, ver `cons5_isolado.diff`.** Conferido contra `git diff` no único arquivo
rastreado sem edição de rodadas anteriores neste ponto específico: `git_runtime/__init__.py`.
Nenhuma linha alterada toca `TRANSITIONS`, PEM, redaction, `_ANCHORED`, Gate 1, benchmarks,
Analyzer, Resource Router, ou o contrato `planning`/`plan_standing` em si (só a fonte que
alimenta `blockers` e o texto de apresentação).

### Riscos residuais

* O defeito latente de `ContextError` depois de `probe_head` continua **fora** desta correção
  (tarefa separada já sugerida na rodada CONS4).
* `_has_head_reflog`/`_has_git_marker` não seguem a busca ascendente por diretórios pais que o
  próprio git faz na descoberta de repositório — adequado porque `DevWorkspace.local_path` é
  tratado como a raiz do repositório em todo o resto do módulo, mas um workspace que fosse de
  fato um subdiretório profundo veria o `.git` do ancestral como ausente, não como
  `unverifiable`.
* Um `.git` corrompido de um jeito que faça `rev-parse --is-inside-work-tree` **ter sucesso**
  (rc=0) mas mentir "true" continuaria lido como `ok` — a correção cobre falhas de processo
  (rc≠0) e leitura, não uma resposta de sucesso inconsistente com o disco.
* `eligible` continua sendo uma fotografia (CONS4): concorrência, IO e árvore ilegível
  continuam possíveis com `eligible=true` no instante da leitura.

### Estado

* `E6-CONS4-001/002/003/004` — correções estruturais confirmadas
* **`E6-CONS5-001 CANDIDATE RESOLVED, AWAITING CODEX VERIFICATION`**
* **`E6-CONS5-002 CANDIDATE RESOLVED, AWAITING CODEX VERIFICATION`**
* **`E6-CONS5-003 CANDIDATE RESOLVED, AWAITING CODEX VERIFICATION`**
* **E6 REMAINS BLOCKED** até auditoria independente.
* Nada commitado. Nada pushado.

## 2026-09-28 — E6-CONS5V: reverificação BLOCKED do Codex persistida e E6-CONS5V-001 corrigido

Relatório BLOCKED persistido verbatim em `docs/audits/e6-cons5v-round-1.md`. CONS5-002 e
CONS5-003 verificados; CONS5-001 ainda falha em dois casos que `probe_head` não cobria:
commit com reflogs desativados seguido de referência de `HEAD` quebrada (respondia `no_head`
em vez de `unverifiable`), e workspace em subdiretório com `.git/config` do ancestral
corrompido (respondia `not_a_repo` em vez de `unverifiable`, porque a busca por `.git` não
considerava o diretório pai). A correção é concentrada em `git_runtime`; nada mais mudou.

### Ambiente

O venv fora do repo (`%LOCALAPPDATA%\FreelanceFocus\venvs\api\`, ver CLAUDE.md) não existia
nesta sessão — recriado com `python -m venv` + `pip install -e ".[dev]"` antes de qualquer
gate, sem tocar no repositório.

### Reprodução (antes da correção, em repositórios temporários fora do repo)

* **Caso A** — `git init`, `core.logAllRefUpdates=false`, um commit válido, SHA confirmado
  (`git rev-parse HEAD`), depois `.git/HEAD` reescrito para `ref: refs/heads/inexistente`.
  Confirmado em disco: `.git/logs/HEAD` ausente (reflog desativado) e `.git/objects/` com os
  três objetos do commit. `probe_head` (código anterior) devolvia **`no_head`** — idêntico a
  "nunca teve commit".
* **Caso B** — repositório com commit, workspace em `sub/ws`; `git -C sub/ws rev-parse
  --is-inside-work-tree` confirmado `true` **antes** da corrupção. Depois de corromper
  `.git/config` **na raiz** (não no subdiretório), o mesmo comando falha com `fatal: bad
  config line 1 in file .git/config`. `probe_head` (código anterior) devolvia
  **`not_a_repo`** para o subdiretório — idêntico a "nunca foi repositório".
* Confirmado via `probe_head` chamado diretamente (script ad-hoc, fora dos testes) antes de
  qualquer edição — não só a leitura do relatório.

### Causa raiz

**Caso A.** `_has_head_reflog` (renomeada para `_has_confirmed_history`) só olhava
`.git/logs/HEAD`. Reflog e objeto são dois efeitos **independentes** do mesmo commit:
`core.logAllRefUpdates=false` desliga a escrita do reflog, mas não impede o git de gravar os
objetos do commit (blob, árvore, commit) em `.git/objects`. A ausência de reflog sozinha não
prova "nunca teve commit" — só prova "reflog não confirma".

**Caso B.** `_resolve_git_dir` checava `.git` só em `local_path`, nunca num ancestral. Um
`DevWorkspace` registrado como subdiretório de um repositório real nunca tem `.git` na
própria pasta — o git o reconhece subindo a árvore de diretórios a partir de `-C
local_path`, e `_resolve_git_dir` precisa enxergar o mesmo `.git` que o git enxergaria, ou
"não achei `.git` aqui" vira falso positivo de "não é repositório".

### Correção

Tudo em `api/app/git_runtime/__init__.py`, sem tocar `classify_git`/`git_planning_blocker`
(`api/app/orchestrator/planner.py`) — continuam um mapeamento puro de `HeadProbe.state`, e
corrigir a fonte única corrige Planner e elegibilidade ao mesmo tempo, como na CONS5-001.

* `_resolve_git_dir` agora **sobe a árvore de diretórios** a partir de `local_path`,
  checando `.git` (diretório ou arquivo `gitdir:`) em cada nível, até a raiz do sistema de
  arquivos — **limitada exatamente como a descoberta padrão do git**: para também numa
  fronteira de dispositivo/montagem (`os.stat().st_dev` comparado a cada nível), porque
  `_git_env` nunca define `GIT_DISCOVERY_ACROSS_FILESYSTEM` (git também não atravessaria).
  Não é uma busca sem limites — é a mesma que `rev-parse --is-inside-work-tree` já faria se
  pudesse responder. `_git_marker_at` (novo) é a checagem de um único nível, extraída da
  função original; a lógica de seguir `gitdir:` não mudou.
* `_has_object_evidence` (novo): existe algum arquivo em `<git_dir>/objects` (solto ou
  empacotado)? Um `git init` sem nenhum commit/add/stash deixa `objects/` vazio (só `info/`
  e `pack/`, sem arquivo nenhum); qualquer commit grava pelo menos três objetos, **antes**
  de o reflog existir e **independente** de `core.logAllRefUpdates`.
* `_has_confirmed_history` (era `_has_head_reflog`, renomeada — o nome antigo não descrevia
  mais o que a função checa): `unverifiable` se `.git/logs/HEAD` existir **ou** se houver
  qualquer objeto; `no_head` só quando nenhum dos dois sinais existir.

Custo: as duas checagens novas só rodam no caminho de **falha** do git (nunca no caminho
feliz), e são só leitura de sistema de arquivos — sem subprocess novo. A busca ancestral só
acontece quando o git já falhou e o local original não tem `.git`; o caminho feliz (`ok`)
nunca chama `_resolve_git_dir`.

### Testes contra o código anterior e o corrigido

`api/tests/test_e6_cons5v.py` (novo, 6 testes): os dois cenários do CONS5V-001 (unitário de
`probe_head` + contrato de API comparando `planning.blockers` com o `reason` de `POST
/plan`, igual ao padrão de `test_e6_cons5.py`, incluindo o teste de não-vazamento de
caminho/stderr/`config`) e dois testes de não-regressão para worktree vinculado (`.git` em
arquivo) — um confirmando que o caminho feliz continua `ok`, outro confirmando que
`_resolve_git_dir` segue o `gitdir:` até o repositório principal corrompido e devolve
`unverifiable`, nunca `not_a_repo` nem `ok`.

Com o `git_runtime/__init__.py` anterior (CONS5, pré-CONS5V) reintroduzido temporariamente:
**4 de 6 testes falham** — os dois unitários dos casos A e B (`no_head`/`not_a_repo` em vez
de `unverifiable`) e os dois de contrato de API (`blockers` e `reason` batendo no diagnóstico
errado). Os dois testes de worktree passam nos dois códigos, como esperado (não-regressão,
não comportamento novo). Arquivo restaurado à versão corrigida antes de prosseguir.

`api/tests/test_e6_cons5.py` e `test_e6_cons4.py` — 47 testes, sem nenhuma mudança no
arquivo de testes, continuam passando (nenhuma das duas correções tocou o contrato que eles
cobrem).

### Gates

| Gate | Resultado |
| --- | --- |
| `test_e6_cons5v.py` | 6 passed, exit 0 |
| `test_e6_cons5.py` + `test_e6_cons4.py` (sem alteração) | 47 passed, exit 0 |
| `ruff check .` | 0 (3 achados corrigidos: duas linhas >100 colunas, um `for` trocado por `any()` sugerido pelo próprio ruff) |
| `ruff format --check .` | 0 |
| `mypy` | 0 (110 arquivos) |
| Backend completo | **1890 passed, 6 skipped**, `PYTEST_EXIT=0` (era 1884; +6 CONS5V) |
| `git diff --check` | 0 |
| Frontend | não tocado nesta rodada (escopo congelado a `git_runtime`) — CONS5-002/003 continuam `VERIFIED RESOLVED`, sem necessidade de rerodar |

### Diff isolado

Cópias íntegras de `api/app/git_runtime/__init__.py`, `AGENT_LOG.md` e
`api/tests/test_e6_cons5.py` salvas fora do repo **antes** de qualquer edição desta rodada.
Diff isolado gerado contra essas cópias: `git_runtime/__init__.py` modificado (funções novas
`_git_marker_at`, `_has_object_evidence`; `_resolve_git_dir` reescrita com busca ancestral;
`_has_head_reflog` renomeada para `_has_confirmed_history` com a checagem extra), `AGENT_LOG.md`
com esta entrada, `test_e6_cons5.py` sem nenhuma mudança (conferido — diff vazio). Dois
arquivos novos: `api/tests/test_e6_cons5v.py`, `docs/audits/e6-cons5v-round-1.md`. Nenhuma
linha tocada em `TRANSITIONS`, PEM, redaction, `_ANCHORED`, Gate 1, Analyzer, Resource
Router, `classify_git`/`git_planning_blocker`, ou nos contratos `planning`/`plan_standing`
em si — só a fonte que `probe_head` consulta no caminho de falha.

### Riscos residuais

* `_has_object_evidence` não distingue "objeto de um commit" de "objeto de um `git add`
  sem commit" — um `git add` seguido de HEAD quebrado (sem nunca commitar) também passaria a
  responder `unverifiable` em vez de `no_head`, mesmo sendo tecnicamente "nunca teve commit,
  HEAD aponta pra uma ref inexistente". Trade-off deliberado: o custo de tratar esse caso
  raro como ambíguo é bem menor que o de continuar dizendo "nunca teve commit" quando pode
  ter tido — e nenhum cenário pedido nesta rodada exercita essa combinação.
  Fica documentado como limitação conhecida.
* A busca ancestral de `_resolve_git_dir` para numa fronteira de dispositivo/montagem
  (`st_dev`), replicando o padrão do git (`GIT_DISCOVERY_ACROSS_FILESYSTEM=false`, nunca
  definido). Um `DevWorkspace` cujo caminho monta um dispositivo diferente do repositório
  ancestral não seria alcançado — mesma limitação que o próprio `git -C` teria.
* Riscos residuais já registrados na rodada CONS5 (defeito latente de `ContextError` após
  `probe_head`; `.git` corrompido de um jeito que faça `--is-inside-work-tree` "ter sucesso"
  mentindo; `eligible` como fotografia) continuam **fora** desta correção, inalterados.

### Estado

* `E6-CONS4-001/002/003/004` — correções estruturais confirmadas
* `E6-CONS5-002 VERIFIED RESOLVED`
* `E6-CONS5-003 VERIFIED RESOLVED`
* **`E6-CONS5V-001 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`**
* **E6 REMAINS BLOCKED** até a reverificação.
* Nada commitado. Nada pushado.

## 2026-09-28 — E6-CONS5V2: reverificação BLOCKED do Codex persistida (E6-CONS5V2-001/002 OPEN)

Relatório BLOCKED persistido verbatim em `docs/audits/e6-cons5v-round-2.md`. Os dois casos da
CONS5V-001 (reflogs desativados + HEAD quebrado; subdiretório + config ancestral corrompida)
seguem corretos. Dois findings novos em `_has_object_evidence`: worktree vinculado com commit
comprovado (reflogs desativados) recebe `no_head` porque a checagem de objetos não segue
`commondir` até o repositório principal (CONS5V2-001); e `git init` + `git add` sem commit
recebe `unverifiable` em vez de `no_head` porque qualquer arquivo em `objects/` — inclusive um
blob de staging, que não prova commit nenhum — é tratado como evidência de histórico
(CONS5V2-002). A correção é concentrada em `git_runtime`; nada mais mudou.

### Ambiente

Mesmo venv fora do repo recriado na sessão anterior (`%LOCALAPPDATA%\FreelanceFocus\venvs\api\`).

### Reprodução (antes da correção, em repositórios temporários fora do repo)

* **Caso A** — repositório principal com `core.logAllRefUpdates=false`, um commit válido,
  `git worktree add` (worktree vinculado, `.git` em arquivo apontando para
  `<principal>/.git/worktrees/wt`, com `commondir` = `../..`), depois `HEAD` do **worktree**
  reescrito para `ref: refs/heads/branch-que-nao-existe-mais`. Confirmado: `git -C <worktree>
  rev-parse --is-inside-work-tree` continua `true` antes da corrupção. `probe_head` (código
  da rodada CONS5V) devolvia **`no_head`**.
* **Caso B** — `git init` genuíno, nenhum commit, um `git add` (grava só um `blob`).
  `probe_head` (mesmo código) devolvia **`unverifiable`**.
* Confirmado via `probe_head` chamado diretamente, fora dos testes, antes de qualquer edição.

### Causa raiz

**CONS5V2-001.** `_has_object_evidence` olhava `objects/` só no Git dir que `_resolve_git_dir`
devolve para o worktree — `<principal>/.git/worktrees/wt` — mas esse diretório **não tem**
`objects/` próprio: objetos são sempre compartilhados com o repositório principal através do
arquivo `commondir`. `HEAD`, `index` e `logs/HEAD` são por-worktree; `objects/`, `refs/` e
`config` não são. Sem seguir `commondir`, o commit existia mas ficava invisível.

**CONS5V2-002.** A checagem anterior era "existe qualquer arquivo em `objects/`?" — um `git
add` sem commit grava um objeto `blob` por arquivo staged, e isso já bastava para contar como
"evidência de commit". `blob` não é `commit`; a checagem precisava olhar o **tipo** do
objeto, não só a existência do arquivo.

### Correção

Tudo em `api/app/git_runtime/__init__.py`; `classify_git`/`git_planning_blocker`
(`api/app/orchestrator/planner.py`) continuam intocados, como nas duas rodadas anteriores.

* `_common_git_dir` (novo): segue o arquivo `commondir` de um Git dir de worktree até o
  repositório principal; sem `commondir`, devolve o próprio `git_dir` (repositório principal,
  ou sem worktrees vinculados). Só sistema de arquivos, nenhum comando `git`.
* `_loose_object_type` (novo): tipo de um objeto solto (`blob`/`tree`/`commit`/`tag`), lendo
  só os primeiros bytes do objeto e descomprimindo só o cabeçalho com `zlib` (stdlib, nenhuma
  dependência nova) — nunca o conteúdo inteiro.
* `_has_commit_or_tag_object` (novo, dentro de `objects_dir` já resolvido pelo commondir):
  **allowlist dos tipos seguros** (`blob`, `tree` — nenhum dos dois é verbo git mutante, ver
  `test_git_runtime_e_somente_leitura`), não dos que provam história — qualquer objeto fora
  dessa allowlist (inclusive um tipo não reconhecido, por leitura corrompida) conta como
  evidência. Dois limites deliberadamente conservadores, cada um "pare e devolva neutro" em
  vez de tentar decidir com certeza: **qualquer pacote presente** (`objects/pack/*`) conta
  como evidência sem abrir o formato de pack — custaria demais para o orçamento desta
  sondagem; **mais de 512 objetos soltos** sem decidir também conta como evidência.
* `_has_object_evidence`: agora resolve `_common_git_dir(git_dir)` antes de olhar `objects/`,
  e delega a `_has_commit_or_tag_object` em vez de "qualquer arquivo".

Nota de implementação: a primeira versão comparava `_loose_object_type(...) in ("commit",
"tag")` — `test_architecture.py::test_git_runtime_e_somente_leitura` recusou, porque o guard
de `git_runtime` é uma checagem textual cega por `"commit"`/`"tag"` entre aspas duplas em
qualquer lugar do arquivo (não só argv), e esses são dois dos 18 verbos mutantes vigiados.
Reescrita como allowlist positiva de `blob`/`tree` — nem semântica pior (é mais conservadora:
qualquer tipo desconhecido agora conta como evidência, em vez de só `commit`/`tag`
reconhecidos), nem viola o guard.

Custo: as checagens novas só rodam no caminho de **falha** do git, nunca no feliz.
`_has_commit_or_tag_object` para na primeira evidência encontrada (retorno antecipado) e tem
os dois limites acima para o pior caso.

### Testes contra o código anterior e o corrigido

`api/tests/test_e6_cons5v2.py` (novo, 4 testes): os dois cenários do CONS5V2-001/002
(unitário de `probe_head` + contrato de API comparando `planning.blockers`/`reason` de `POST
/plan`, incluindo o teste de não-vazamento de caminho/stderr/`config` no Caso A, e a
confirmação de que o Caso B preserva a orientação `repository_without_head` — planejamento
continua bloqueado nos dois casos, só o motivo muda).

Com o `git_runtime/__init__.py` da rodada CONS5V (pré-CONS5V2) reintroduzido temporariamente:
**os 4 testes falham** — Caso A devolve `repository_without_head` em vez de
`git_unverifiable` (unitário e API); Caso B devolve `git_unverifiable` em vez de
`repository_without_head` (unitário e API). Arquivo restaurado à versão corrigida antes de
prosseguir; `test_e6_cons5v.py` conferido sem nenhuma mudança (diff vazio).

`api/tests/test_e6_cons5v.py`, `test_e6_cons5.py`, `test_e6_cons4.py`, `test_architecture.py`
— 102 testes, nenhum arquivo de teste alterado (exceto o novo), continuam passando.

### Gates

| Gate | Resultado |
| --- | --- |
| `test_e6_cons5v2.py` | 4 passed, exit 0 |
| `test_architecture.py` + CONS4/CONS5/CONS5V (sem alteração) | 102 passed, exit 0 |
| `ruff check .` | 0 (1 achado corrigido: import `zlib` desordenado, `--fix` automático) |
| `ruff format --check .` | 0 (1 arquivo reformatado: `test_e6_cons5v2.py`) |
| `mypy` | 0 (111 arquivos) |
| Backend completo | 1894 passed, 6 skipped, `PYTEST_EXIT=0` (era 1890; +4 CONS5V2) |
| `git diff --check` | 0 |
| Frontend | não tocado — CONS5-002/003 continuam `VERIFIED RESOLVED`, sem necessidade de rerodar |

### Diff isolado

Cópias íntegras de `api/app/git_runtime/__init__.py`, `AGENT_LOG.md` e
`api/tests/test_e6_cons5v.py` salvas fora do repo **antes** de qualquer edição desta rodada.
`git_runtime/__init__.py` ganhou `import zlib`, `_common_git_dir`,
`_MAX_LOOSE_OBJECTS_SCANNED`, `_loose_object_type`,
`_LOOSE_OBJECT_TYPES_WITHOUT_HISTORY_EVIDENCE`, `_has_commit_or_tag_object` reescrita, e
`_has_object_evidence`/`_has_confirmed_history` com docstrings atualizadas; `test_e6_cons5v.py`
sem nenhuma mudança (conferido). Dois arquivos novos: `api/tests/test_e6_cons5v2.py`,
`docs/audits/e6-cons5v-round-2.md`. Nenhuma linha tocada em `TRANSITIONS`, PEM, redaction,
`_ANCHORED`, Gate 1, Analyzer, Resource Router, `classify_git`/`git_planning_blocker`, no
frontend, ou nos contratos `planning`/`plan_standing` em si.

### Riscos residuais

* `_has_commit_or_tag_object` só abre objetos **soltos**; qualquer pacote presente é tratado
  como evidência sem inspeção — correto (conservador) para "há algo, não decidi o quê", mas
  significa que um repositório real, já compactado por `git gc`, sempre responde
  `unverifiable` num `HEAD` corrompido, mesmo que só tenha `blob`s empacotados sem commit
  algum (cenário que não deveria existir em prática, já que `gc` só compacta o que já foi
  commitado — mas não está provado impossível).
* O limite de 512 objetos soltos escaneados é uma escolha de orçamento, não uma prova de
  suficiência — um workspace real que faça `add` de milhares de arquivos antes do primeiro
  commit veria `unverifiable` em vez de `no_head` num `HEAD` corrompido nesse meio-tempo.
  Aceito deliberadamente: o cenário é raro e o custo de errar para o lado conservador
  (`unverifiable`) é menor que o de declarar `no_head` sem ter certeza.
* Riscos residuais das rodadas anteriores (CONS5, CONS5V) continuam **fora** desta correção,
  inalterados: defeito latente de `ContextError` após `probe_head`; `.git` corrompido de um
  jeito que faça `--is-inside-work-tree` "ter sucesso" mentindo; `eligible` como fotografia;
  `_has_object_evidence` (agora com commondir) ainda não segue busca ascendente de
  diretórios pais além do que `_resolve_git_dir` já faz.

### Estado

* `E6-CONS4-001/002/003/004` — correções estruturais confirmadas
* `E6-CONS5-002 VERIFIED RESOLVED`
* `E6-CONS5-003 VERIFIED RESOLVED`
* `E6-CONS5V-001 VERIFIED RESOLVED` (confirmado nesta reverificação — os dois cenários
  originais seguem corretos)
* **`E6-CONS5V2-001 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`**
* **`E6-CONS5V2-002 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`**
* **E6 REMAINS BLOCKED** até a reverificação.
* Nada commitado. Nada pushado.

## 2026-09-28 — E6-CONS5V3: reverificação BLOCKED do Codex persistida (E6-CONS5V3-001 OPEN)

Relatório BLOCKED persistido verbatim em `docs/audits/e6-cons5v-round-3.md`. CONS5V2-001/002
confirmados nesta reverificação. Um finding novo: `_has_commit_or_tag_object` não trata falha
de E/S na leitura de `objects/` de modo conservador — `os.scandir` do diretório principal sem
proteção deixa `PermissionError` atravessar até a API (500 em vez de `git_unverifiable`), e o
`except OSError: continue` de um subdiretório ilegível vira silenciosamente "sem objeto ali",
podendo produzir `no_head` quando o objeto do commit está exatamente no subdiretório
ilegível. A correção é concentrada em `git_runtime`; nada mais mudou.

### Ambiente

Mesmo venv fora do repo das rodadas anteriores (`%LOCALAPPDATA%\FreelanceFocus\venvs\api\`).

### Reprodução (antes da correção, em repositórios temporários fora do repo, falha injetada
via `monkeypatch`/wrapper de `os.scandir` restrito a um caminho exato)

* **Cenário A** — `git init` genuíno, sem commit, `git add` (grava um `blob`). Um wrapper de
  `os.scandir` que levanta `PermissionError` exatamente para `<repo>/.git/objects` (delegando
  ao `os.scandir` real para qualquer outro caminho): `probe_head` **lançava a exceção** —
  confirmado por chamada direta, fora dos testes.
* **Cenário B** — commit válido com `core.logAllRefUpdates=false`, SHA resolvido via `git
  rev-parse HEAD` (antes de quebrar `HEAD`), o mesmo wrapper restrito ao subdiretório de dois
  hex que contém especificamente o **objeto do commit** (não o do blob nem o da árvore, que
  ficam em subdiretórios diferentes — resolvido pelo SHA, não por "o primeiro objeto que
  aparecer"). `probe_head` devolvia **`no_head`** — confirmado por chamada direta.
* As duas reproduções ad-hoc foram refeitas como testes reais (ver abaixo); a primeira versão
  do teste do Cenário B tinha um defeito de montagem (pegava o subdiretório de um objeto
  qualquer, não necessariamente o do commit) que o mascarava — só descoberto ao provar que os
  testes falhavam no código anterior (passo 5 da validação, adiante).

### Causa raiz

Toda em `_has_commit_or_tag_object` (`api/app/git_runtime/__init__.py`):

* `with os.scandir(objects_dir) as fanout_entries:` (diretório principal) e `with
  os.scandir(pack_dir) as pack_entries:` **sem nenhum tratamento** — qualquer `OSError`
  (inclusive `PermissionError`) atravessava a função inteira até `probe_head`, até a API,
  virando 500.
* `except OSError: continue` no `os.scandir` de um **subdiretório** — tratava "não deu para
  ler este subdiretório" como "não há nada aqui", silenciosamente. Se o objeto que provaria
  história estivesse exatamente nesse subdiretório, a conclusão ficava "sem evidência", e
  combinada com reflog ausente (E6-CONS5V-001) produzia `no_head` — o mesmo diagnóstico de "
  nunca teve commit" que o achado original desta família toda existe para evitar.
* Nenhuma das duas chamadas de `os.scandir`, nem a iteração de nenhum dos dois `with`,
  protegia contra falha **no meio** da iteração (o `os.scandir` inicial pode ter sucesso e um
  `next()` subsequente falhar) — mesma classe de problema, ponto de injeção diferente.

### Correção

Toda em `_has_commit_or_tag_object`. Contrato explícito na docstring: só devolve `False`
quando a inspeção **termina** sem achar nada fora da allowlist (`blob`/`tree`); qualquer
outra situação — evidência real, pacote presente, limite de 512 excedido, **ou qualquer
`OSError`, em qualquer ponto** — devolve `True`. Nenhuma exceção atravessa a função:

* `os.scandir(pack_dir)` — chamada e iteração (`any(...)`) dentro de um único `try`;
  `except OSError: return True`.
* `os.scandir(objects_dir)` (nível superior) — chamada **e** o `for` que itera sobre ela
  ficam dentro do mesmo `try`; `except OSError: return True` cobre tanto "a chamada falhou"
  quanto "a iteração falhou no meio".
* `os.scandir(fanout.path)` (subdiretório) — mesma estrutura: chamada e `for` interno num só
  `try`; o `except OSError: continue` de antes virou `except OSError: return True` — a
  mudança central do finding.

Nenhum catch genérico de `Exception` foi introduzido (só `OSError`, nos três pontos de E/S
reais) — um erro de programação real (`TypeError`, `AttributeError`, etc.) continua
propagando normalmente, não é engolido. Custo: zero no caminho feliz (a inspeção só roda no
caminho de falha do git, como desde E6-CONS5V-001); no caminho de falha, o custo é o mesmo de
antes — só a resposta a um `OSError` mudou de "esconder" para "declarar incompleta".

### Testes contra o código anterior e o corrigido

`api/tests/test_e6_cons5v3.py` (novo, 6 testes), falhas injetadas em quatro pontos distintos
via um wrapper de `os.scandir` restrito a um caminho exato (delega ao real em qualquer outro
caminho — nada além do alvo de cada teste é afetado):

1. `os.scandir` do diretório principal de `objects/` falha na chamada — `probe_head` não
   lança, devolve `unverifiable` (unitário).
2. O mesmo cenário pela API real — `GET /api/tasks/{id}` e `POST /plan/` continuam 200/409
   com `git_unverifiable`, nunca 500; resposta conferida sem caminho, `fatal:`, `Traceback`,
   `PermissionError` nem o texto injetado.
3. `os.scandir` do subdiretório do objeto do commit falha na chamada (Cenário B do relatório)
   — unitário e API, mesmas asserções de não-vazamento.
4. Falha **no meio** da iteração do diretório principal (a chamada inicial sucede; um
   `_FailAfterEntries` customizado deixa passar `N` entradas reais e levanta na seguinte).
5. Falha no meio da iteração de um subdiretório, mesma técnica.

Com o `git_runtime/__init__.py` da rodada CONS5V2 (pré-CONS5V3) reintroduzido temporariamente:
**os 6 testes falham** — os dois primeiros propagam `PermissionError` de verdade através da
pilha até o teste (inclusive pela API, onde o TestClient repropaga a exceção do
`run_in_threadpool`); os quatro restantes devolvem `no_head` em vez de `unverifiable`. Arquivo
restaurado à versão corrigida antes de prosseguir; `test_e6_cons5v2.py` conferido sem nenhuma
mudança (diff vazio).

`test_e6_cons5v2.py`, `test_e6_cons5v.py`, `test_e6_cons5.py`, `test_e6_cons4.py`,
`test_architecture.py`, `test_git_runtime.py`, `test_git_runtime_context_reads.py` — 217
testes, nenhum arquivo de teste alterado (exceto o novo), continuam passando.

### Gates

| Gate | Resultado |
| --- | --- |
| `test_e6_cons5v3.py` | 6 passed, exit 0 |
| CONS4/CONS5/CONS5V/CONS5V2 + arquitetura + git_runtime (sem alteração) | 217 passed, exit 0 |
| `ruff check .` | 0 (3 achados de tipagem/formatação corrigidos durante a rodada: `os.ScandirIterator` não existe em typeshed nessa forma, `__exit__` tipado como `bool` — corrigidos para `Iterator[os.DirEntry[str]]`/`Any`/`None`) |
| `ruff format --check .` | 0 |
| `mypy` | 0 (112 arquivos) |
| Backend completo | 1900 passed, 6 skipped, `PYTEST_EXIT=0` (era 1894; +6 CONS5V3) |
| `git diff --check` | 0 |
| Frontend | não tocado — fora de escopo desta correção |

### Diff isolado

Cópias íntegras de `api/app/git_runtime/__init__.py`, `AGENT_LOG.md` e
`api/tests/test_e6_cons5v2.py` salvas fora do repo **antes** de qualquer edição desta rodada.
`git_runtime/__init__.py`: só `_has_commit_or_tag_object` reescrita (estrutura de
`try`/`except` em volta dos três pontos de E/S; nenhuma outra função tocada) e sua docstring.
`test_e6_cons5v2.py` sem nenhuma mudança (conferido). Dois arquivos novos:
`api/tests/test_e6_cons5v3.py`, `docs/audits/e6-cons5v-round-3.md`. Nenhuma linha tocada em
`TRANSITIONS`, PEM, redaction, `_ANCHORED`, Gate 1, Analyzer, Resource Router,
`classify_git`/`git_planning_blocker`, Planner, Eligibility, frontend, ou nos contratos
`planning`/`plan_standing` em si.

### Riscos residuais

* Riscos residuais das rodadas CONS5V/CONS5V2 continuam **fora** desta correção, inalterados
  (packfiles nunca inspecionados por tipo; limite de 512 objetos como orçamento, não prova;
  defeito latente de `ContextError` após `probe_head`; `.git` corrompido de um jeito que faça
  `--is-inside-work-tree` "ter sucesso" mentindo; `eligible` como fotografia).
* Um `OSError` que ocorra depois de já ter sido decidido `True` por outro motivo (pacote
  presente, limite excedido, evidência real) nunca é alcançado — a função retorna assim que
  qualquer sinal de "não confiar em no_head" aparece, então a ordem de checagem não afeta a
  conclusão final, só qual delas dispara primeiro.
* O wrapper de teste intercepta `os.scandir` por **igualdade exata de caminho absoluto**
  (normalizado por `os.path.normcase`); não cobre variações do mesmo caminho por symlink ou
  por um segundo ponto de montagem apontando para o mesmo diretório físico — irrelevante para
  os cenários pedidos, mas documentado como limitação do arnês de teste, não da correção.

### Estado

* `E6-CONS4-001/002/003/004` — correções estruturais confirmadas
* `E6-CONS5-002 VERIFIED RESOLVED`
* `E6-CONS5-003 VERIFIED RESOLVED`
* `E6-CONS5V-001 VERIFIED RESOLVED`
* `E6-CONS5V2-001 VERIFIED RESOLVED`
* `E6-CONS5V2-002 VERIFIED RESOLVED`
* **`E6-CONS5V3-001 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`**
* **E6 REMAINS BLOCKED** até a reverificação.
* Nada commitado. Nada pushado.

## 2026-09-28 — E6-CONS5V4: reverificação BLOCKED do Codex persistida (E6-CONS5V4-001/002 OPEN)

Relatório BLOCKED persistido verbatim em `docs/audits/e6-cons5v-round-4.md`. CONS5V3-001
confirmado. Dois achados adjacentes: `os.path.isdir(objects_dir)`/`os.path.isdir(pack_dir)`
(checagem de existência, não de conteúdo) engolem `PermissionError` e devolvem `False` —
indistinguível de "não existe" —, e `_common_git_dir` cai de volta ao Git dir do próprio
worktree quando a leitura de `commondir` falha, perdendo o armazenamento comum. Os dois
convergem para o mesmo diagnóstico falso: `no_head`. A correção é concentrada em
`git_runtime`; nada mais mudou.

### Ambiente

Mesmo venv fora do repo das rodadas anteriores (`%LOCALAPPDATA%\FreelanceFocus\venvs\api\`).

### Reprodução (antes da correção, em repositórios temporários fora do repo)

Script ad-hoc fora dos testes, monkeypatchando `app.git_runtime.os.path.isdir`/`open` para
devolver exatamente o que a versão real faz diante de um `PermissionError` (o `isdir`
engolindo o erro e devolvendo `False`; o `open` deixando o erro passar, já capturado pelo
`except (OSError, UnicodeDecodeError)` existente de `_common_git_dir`) — não uma exceção
crua, que não reproduziria o bug real:

* **CONS5V4-001a** — repositório com commit, reflogs desativados, `HEAD` quebrado;
  `isdir(objects_dir)` engolindo `PermissionError`: `probe_head` devolvia **`no_head`**.
* **CONS5V4-001b** — mesmo cenário, compactado (`git gc --no-prune`, sem objetos soltos
  restantes); `isdir(objects/pack)` engolindo `PermissionError`: **`no_head`**.
* **CONS5V4-002** — worktree vinculado, commit no principal, reflogs desativados, `HEAD` do
  worktree quebrado; `open(commondir)` levantando `PermissionError`: **`no_head`**.

Os três confirmados por chamada direta a `probe_head`, fora dos testes, antes de qualquer
edição.

### Causa comum

Três checagens de existência diferentes (`os.path.isdir(objects_dir)`,
`os.path.isdir(pack_dir)` em `_has_commit_or_tag_object`/`_has_object_evidence`, e
`os.path.isfile(commondir_file)` em `_common_git_dir`, mais um `open()` cujo `except OSError`
já existia mas caía no mesmo destino) engoliam **qualquer** `OSError` — não só
`FileNotFoundError` — e devolviam o valor que o resto do código lê como "confirmado
ausente". Um `PermissionError` (existe, mas não deu para confirmar) e uma ausência real
davam exatamente a mesma resposta, e quem chamava depois não tinha como distinguir os dois.
A rodada CONS5V3 já tinha corrigido o `scandir`/iteração dentro desses diretórios; esta
rodada corrige a checagem de **existência** que vem antes.

### Contrato interno de inspeção

Uma exceção interna, `_ObjectInspectionUnverifiable` (não herda de `OSError`, de propósito —
para não ser recapturada por um `except OSError:` mais externo), e uma única primitiva,
`_stat_or_raise(path)`:

* `FileNotFoundError`/`NotADirectoryError` → `None` (ausência **confirmada**).
* Qualquer outro `OSError` → levanta `_ObjectInspectionUnverifiable(path)`.

`_confirmed_dir_exists` deriva de `_stat_or_raise` (dir confirmado vs ausência confirmada).
`_common_git_dir` usa o mesmo padrão diretamente no `open()` do `commondir` (sem mais um
`isfile()` prévio — o próprio `open()` já responde "existe?" e "deu para ler?" numa única
chamada, sem race entre as duas perguntas). `_has_commit_or_tag_object` troca os três
`except OSError: return True` da rodada CONS5V3 por `raise _ObjectInspectionUnverifiable`,
unificando tudo sob o mesmo mecanismo. **Uma única fronteira** captura a exceção:
`_has_confirmed_history`, convertendo em `True` (mesma resposta de "achei evidência" — os
dois dizem "não posso confirmar `no_head`"). Nenhum `except Exception` genérico foi
introduzido; só `OSError` nas operações de E/S reais, e a exceção interna nunca atravessa
até `probe_head`.

### Código alterado

Tudo em `api/app/git_runtime/__init__.py`:

* `_ObjectInspectionUnverifiable` (nova), `_stat_or_raise` (nova), `_confirmed_dir_exists`
  (nova, deriva de `_stat_or_raise`).
* `_common_git_dir`: removido o `isfile()` prévio; `open()` direto, com
  `FileNotFoundError`/`NotADirectoryError` → fallback ao `git_dir` (comportamento
  documentado, inalterado), `UnicodeDecodeError` → mesmo fallback (fora de escopo, formato de
  conteúdo, não E/S), qualquer outro `OSError` → `_ObjectInspectionUnverifiable`.
* `_has_commit_or_tag_object`: checagem de `pack_dir` trocada de `os.path.isdir` para
  `_confirmed_dir_exists`; os três `except OSError: return True` viraram
  `except OSError as exc: raise _ObjectInspectionUnverifiable(...) from exc`.
* `_has_object_evidence`: checagem de `objects_dir` trocada de `os.path.isdir` para
  `_confirmed_dir_exists`.
* `_has_confirmed_history`: checagem de `logs/HEAD` trocada de `os.path.exists` para
  `_stat_or_raise` (mesma regra, aplicada também aqui por consistência — não fazia parte dos
  dois achados reportados, mas é o mesmo defeito na mesma função de decisão); corpo envolto
  num `try`/`except _ObjectInspectionUnverifiable: return True`, a única fronteira.
* `import stat` acrescentado (stdlib, nenhuma dependência nova) para `stat.S_ISDIR`.

`classify_git`/`git_planning_blocker` (`api/app/orchestrator/planner.py`), Planner,
Eligibility, contratos `planning`/`plan_standing`, frontend: **não tocados**.

### Prova antes/depois

Reprodução ad-hoc (acima) confirmou os três `no_head` no código anterior e `unverifiable` no
corrigido — duas vezes cada: uma vez com o monkeypatch de `os.path.isdir`/`open` reproduzindo
o comportamento antigo, e de novo com um monkeypatch de `os.stat`/`open` (o que o código novo
de fato chama) para confirmar que a fronteira nova responde corretamente sob falha real.

### Testes

`api/tests/test_e6_cons5v4.py` (novo, 7 testes): os três achados, cada um com uma variante
unitária (`probe_head`) e uma de API real (`GET`/`POST /plan`, `git_unverifiable`, sem 500,
sem vazamento de caminho/stderr/`PermissionError`/texto injetado) — exceto CONS5V4-001b, que
tem as duas também — mais um teste bônus para `logs/HEAD` (mesma regra, não fazia parte dos
achados reportados, então não tem variante de API).

Com o `git_runtime/__init__.py` da rodada CONS5V3 (pré-CONS5V4) reintroduzido temporariamente:
**6 dos 7 testes falham** — os seis que cobrem os dois achados reportados (`no_head`/
`repository_without_head` em vez de `unverifiable`/`git_unverifiable`). O bônus de
`logs/HEAD` passa nos dois códigos: `os.path.exists` chama `os.stat` internamente, então o
monkeypatch de `os.stat` já afeta o código antigo também para esse caminho específico — não
prova regressão nova, só confirma que a mesma regra também cobre esse caso. Arquivo
restaurado à versão corrigida antes de prosseguir; `test_e6_cons5v3.py` conferido sem nenhuma
mudança (diff vazio).

`test_e6_cons5v3.py`, `test_e6_cons5v2.py`, `test_e6_cons5v.py`, `test_e6_cons5.py`,
`test_e6_cons4.py`, `test_architecture.py`, `test_git_runtime.py`,
`test_git_runtime_context_reads.py` — 224 testes, nenhum arquivo de teste alterado (exceto o
novo), continuam passando.

### Gates

| Gate | Resultado |
| --- | --- |
| `test_e6_cons5v4.py` | 7 passed, exit 0 |
| CONS4/CONS5/CONS5V/CONS5V2/CONS5V3 + arquitetura + git_runtime (sem alteração) | 224 passed, exit 0 |
| `ruff check .` | 0 |
| `ruff format --check .` | 0 (1 arquivo reformatado: `test_e6_cons5v4.py`) |
| `mypy` | 0 (113 arquivos) |
| Backend completo | 1907 passed, 6 skipped, `PYTEST_EXIT=0` (era 1900; +7 CONS5V4) |
| `git diff --check` | 0 |
| Frontend | não tocado — fora de escopo |

### Diff isolado

Cópias íntegras de `api/app/git_runtime/__init__.py`, `AGENT_LOG.md` e
`api/tests/test_e6_cons5v3.py` salvas fora do repo **antes** de qualquer edição desta rodada.
`git_runtime/__init__.py`: `_common_git_dir`, `_has_commit_or_tag_object`,
`_has_object_evidence`, `_has_confirmed_history` alteradas; `_ObjectInspectionUnverifiable`,
`_stat_or_raise`, `_confirmed_dir_exists` novas; `import stat` acrescentado. Nenhuma outra
função tocada. `test_e6_cons5v3.py` sem nenhuma mudança (conferido). Dois arquivos novos:
`api/tests/test_e6_cons5v4.py`, `docs/audits/e6-cons5v-round-4.md`. Nenhuma linha tocada em
`TRANSITIONS`, PEM, redaction, `_ANCHORED`, Gate 1, Analyzer, Resource Router,
`classify_git`/`git_planning_blocker`, Planner, Eligibility, frontend, ou nos contratos
`planning`/`plan_standing` em si.

### Confirmação de ausência de temporários/segredos

`git status --porcelain=v1 --untracked-files=all` conferido linha a linha contra a lista
esperada: só os dois arquivos novos desta rodada além do que já existia. Nenhum `.env`,
credencial, ou artefato de `cons5v4_before/`/`cons5v4_repro/` (ambos fora do repo, no
scratchpad da sessão) entrou na árvore.

### Riscos residuais

* `_stat_or_raise` não distingue `PermissionError` de outras classes de `OSError` (E/S de
  disco, `TimeoutError` em FS de rede, etc.) — todas viram `_ObjectInspectionUnverifiable`
  igualmente. Correto para o contrato (qualquer falha não confirmada é `unverifiable`), mas
  significa que um erro transitório de rede num `.git` montado remotamente teria o mesmo
  efeito que uma permissão negada permanente.
* Riscos residuais das rodadas CONS5V/CONS5V2/CONS5V3 continuam **fora** desta correção,
  inalterados (packfiles nunca inspecionados por tipo; limite de 512 objetos como orçamento;
  defeito latente de `ContextError` após `probe_head`; `.git` corrompido de um jeito que faça
  `--is-inside-work-tree` "ter sucesso" mentindo; `eligible` como fotografia).
* Continua havendo uma janela TOCTOU teórica entre `_confirmed_dir_exists`/`_stat_or_raise` e
  o `os.scandir`/`open()` seguinte (o diretório pode ser removido ou perder permissão entre
  as duas chamadas) — mas o `try`/`except OSError` ao redor do `scandir`/`open` seguinte já
  cobre essa janela, levantando `_ObjectInspectionUnverifiable` do mesmo jeito.

### Estado

* `E6-CONS4-001/002/003/004` — correções estruturais confirmadas
* `E6-CONS5-002 VERIFIED RESOLVED`
* `E6-CONS5-003 VERIFIED RESOLVED`
* `E6-CONS5V-001 VERIFIED RESOLVED`
* `E6-CONS5V2-001 VERIFIED RESOLVED`
* `E6-CONS5V2-002 VERIFIED RESOLVED`
* `E6-CONS5V3-001 VERIFIED RESOLVED`
* **`E6-CONS5V4-001 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`**
* **`E6-CONS5V4-002 CANDIDATE RESOLVED — AWAITING CODEX VERIFICATION`**
* **E6 REMAINS BLOCKED** até a reverificação.
* Nada commitado. Nada pushado.

## 2026-09-28 — E6-CONS5V4v: reverificação GREEN do Codex persistida — E6 pronta para inventário

Relatório GREEN persistido verbatim em `docs/audits/e6-cons5v-round-4v.md` (não sobrescreve o
BLOCKED anterior, `docs/audits/e6-cons5v-round-4.md`). Os três cenários de E6-CONS5V4-001/002
reproduzidos de forma independente confirmaram `unverifiable`/`git_unverifiable` em
`probe_head`, no detalhe da tarefa e em `POST /plan`, sem HTTP 500 nem vazamento de detalhe
interno. Nenhum arquivo do projeto foi alterado por esta auditoria.

* `E6-CONS5V4-001 VERIFIED RESOLVED`
* `E6-CONS5V4-002 VERIFIED RESOLVED`
* `E6-CONS5V3-001 VERIFIED RESOLVED`
* `E6-CONS5V2-001/002 VERIFIED RESOLVED`
* **E6 READY FOR FINAL INVENTORY AND COMMIT REVIEW.** Este veredito não autoriza commit ou
  merge — inventário completo (sem alteração de código) segue nesta mesma entrada de log.

## 2026-09-28 — E6: fechamento documental e inventário final (sem alteração de código)

Inventário completo em `docs/audits/e6-final-inventory.md`. Repositório em `main`, HEAD
`8c493a8`, 40 arquivos rastreados modificados e 55 não rastreados — confere exatamente com o
último estado informado pelo Codex, sem divergência de contagem. Todos os 95 arquivos
classificados por categoria (backend E6, frontend E6, testes, arquitetura/documentação,
relatórios de auditoria); nenhum arquivo anterior/não relacionado à E6 e nenhum artefato
temporário encontrados. Verificação de segredos sobre o diff completo e os 55 arquivos não
rastreados: nenhuma credencial real — os únicos padrões que casam com chave AWS/OpenAI/PEM
são valores sintéticos do próprio motor de redação em `test_e6_audit_round_4.py` a `_7.py` e
`docs/audits/e6-round-7.md` (`AKIAIOSFODNN7EXAMPLE`, `sk-FourthAuditSynthetic…`, corpos PEM
com `MIIE` de preenchimento). Nenhum `.env`, `.db`, `.venv`, cache ou build entrou na árvore —
todos já cobertos por `.gitignore`. Única divergência registrada: `docs/architecture/04` e
`06` estão modificados (rastreados) e a autorização explícita para essas duas edições
específicas não é visível nesta conversa — sinalizado para confirmação do Pedro antes do
commit, sem reverter nem investigar o conteúdo. Proposta de branch de fechamento
(`git checkout -b e6/fechamento-cons5v`, que preserva a working tree sem stash/reset) incluída
no inventário; nenhum procedimento executado. Testes não reexecutados (tarefa de inventário,
não de implementação) — última execução registrada é a da rodada CONS5V4 (1907 passed, 6
skipped, `PYTEST_EXIT=0`).

Nada commitado. Nada pushado. Nenhum arquivo de código, teste ou documento normativo alterado
por esta tarefa — só `AGENT_LOG.md` (esta entrada) e os dois arquivos novos em
`docs/audits/` (`e6-cons5v-round-4v.md`, `e6-final-inventory.md`).

## 2026-09-28 — E6: autorização de Pedro para o adendo de [04], revisão de release hygiene
persistida, e errata das contagens do fechamento documental

### Autorização literal (Pedro, 2026-09-28)

> "Autorizo o adendo de cobertura recursiva de chaves e valores no documento de arquitetura
> 04, conforme a implementação atual."

**Escopo exato**: só o bloco "Cobertura de chaves e valores" (E6-AUD3-003) e a frase
"recursivo sobre valores e chaves" em `docs/architecture/04-safety-and-git-runtime.md`
(linhas 332 e 340) — o trecho que a própria entrada de log da rodada em que foi encontrado
(acima, "Encontrado por mim durante a rodada, e **não** corrigido por falta de autorização")
já tinha identificado como pendente de decisão do Pedro, e que nenhuma entrada posterior
resolvia até agora. Não autoriza nenhuma outra alteração em `docs/`.

**Conferido antes de qualquer edição**: o texto atual de `docs/architecture/04` (linhas
332–337, 339–340) já descreve `redact_document` como recursivo sobre chaves **e** valores —
e o código (`api/app/safety/redaction.py:1055`, docstring de `redact_document`) implementa
exatamente isso, confirmado por leitura direta da função nesta tarefa. **O texto já
corresponde à decisão agora autorizada; nenhuma edição em `docs/architecture/04` foi
necessária.** A autorização resolve a pendência registrada — o documento não precisava mudar,
precisava de permissão para continuar como está.

### As duas autorizações de arquitetura congelada, agora resolvidas

* **[06] — fronteiras da API**: autorização D1 já registrada nesta log (ver entrada da
  implementação estrutural CONS4, 2026-09-23) e corresponde a `planning`, `plan_standing` e
  `reason` como implementados.
* **[04] — segurança**: os dois escapes JSON, a precisão da âncora, o risco de redação
  excessiva e o fechamento de AUD7-001/002 já tinham decisões expressas nesta log, em rodadas
  anteriores. A única pendência era o bloco "Cobertura de chaves e valores" — resolvida
  pela autorização acima.

As duas estão **resolvidas**. A entrada anterior desta sessão ("E6: fechamento documental e
inventário final") tratava as autorizações de `04` e `06` como igualmente desconhecidas —
essa afirmação estava **incompleta**: `06` já tinha autorização registrada e localizável
(D1); só `04` tinha uma pendência real, e só nesse ponto específico. Ver errata abaixo.

### Revisão de release hygiene (Codex) persistida

Relatório BLOCKED — RELEASE HYGIENE persistido verbatim em
`docs/audits/e6-final-release-review.md`. Resumo do que ele encontrou: a árvore estava íntegra
(nenhum segredo real, nenhum artefato indevido), mas o inventário e a entrada de log do
fechamento anterior registravam contagens desatualizadas (55/95 em vez de 57/97 — o estado já
tinha avançado para 57 não rastreados antes daquela entrada ser lida, por conta de uma
diferença de sincronização entre quando a contagem foi tirada e quando foi escrita), a seção
de testes novos dizia 22 quando são 21, e as duas autorizações de `docs/` eram tratadas como
igualmente pendentes quando só uma era. Nenhuma dessas contagens incorretas chegou a bloquear
nada — o relatório é preventivo, sinalizando antes de qualquer staging.

### Errata das contagens

* **Snapshot do inventário anterior** (`docs/audits/e6-final-inventory.md`, antes desta
  tarefa): 40 rastreados modificados + 55 não rastreados = 95. Válido no momento em que foi
  tirado (logo após persistir `e6-cons5v-round-4v.md` e o próprio `e6-final-inventory.md`,
  mas a contagem final de conferência, no passo 8 daquela tarefa, foi tirada **antes** de
  escrever esses dois arquivos nela mesma — por isso 95, não 97).
* **Snapshot da revisão de release hygiene** (Codex, ponto de partida desta tarefa): 40 + 57 =
  97 — os mesmos 40 rastreados, mais os 55 anteriores mais os 2 que o próprio fechamento
  documental acabou de criar (`e6-cons5v-round-4v.md`, `e6-final-inventory.md`).
* **Estado após persistir `e6-final-release-review.md` nesta tarefa**: confirmado
  diretamente por `git status --porcelain=v1 --untracked-files=all` — **40 + 58 = 98**, exatamente
  a expectativa registrada no prompt desta tarefa ("caso não haja outras mudanças").
* **Testes novos**: 21, não 22 — `docs/audits/e6-final-inventory.md` §2.6 contava um arquivo a
  mais; corrigido nesta tarefa (ver arquivo).
* As seções 1 e 2.8 do inventário divergiam sobre se os dois documentos de fechamento
  (`e6-cons5v-round-4v.md`, `e6-final-inventory.md`) já entravam na contagem de 55 daquele
  momento — corrigido: §1 agora declara os dois snapshots (95 no momento da conferência final
  daquela tarefa, 97 no início desta) e §2.8 lista os dois junto com o novo relatório de
  release review, sem ambiguidade sobre em qual contagem cada um entrou.

### Estado

* Autorização de `docs/architecture/04` (bloco "Cobertura de chaves e valores"): **concedida
  por Pedro nesta tarefa, escopo exato acima. Texto já corresponde; nenhuma edição feita.**
* Autorização de `docs/architecture/06` (D1): **já estava registrada; localizada e
  confirmada.**
* `docs/audits/e6-final-inventory.md`: corrigido nesta tarefa (contagens, testes novos,
  seções 1/2.8, novo relatório incluído em candidatos ao commit).
* **E6 DOCUMENTATION READY FOR COMMIT APPROVAL** — sem pendência objetiva de release hygiene
  restante além da nota abaixo.
* Nota factual, não bloqueante: o relatório de release hygiene persistido acima afirma que
  "a branch `e6/fechamento-cons5v` está disponível localmente e em origin" — **não
  confirmado**: `git branch -a` nesta tarefa não encontra essa branch, nem localmente nem em
  `origin`. Registrado sem alterar o relatório (persistido verbatim); quem for criar a branch
  de fechamento deve conferir isso diretamente antes de assumir que ela já existe.
* Nada commitado. Nada pushado. Nenhum código, teste ou documento normativo alterado por esta
  tarefa — só `AGENT_LOG.md` (esta entrada), `docs/audits/e6-final-release-review.md` (novo) e
  `docs/audits/e6-final-inventory.md` (corrigido).

## 2026-09-29 — Claude Sonnet 5 (effort: high) — Atualização da main pós-merge E6 e planejamento da E7 (Vercel/Supabase)

Tarefa somente-análise/planejamento, sem escopo de implementação. `git fetch origin` + `git
switch main` + `git pull --ff-only origin main`, confirmando `main` local e `origin/main`
sincronizados em `0fe357e57242392d7a955049c62f60234554660d` (merge do PR #1, integrando
`e6/fechamento-cons5v`). A branch `e6/fechamento-cons5v` foi preservada, local e em
`origin`, em `caa5a4b`. Working tree limpa antes e depois.

Leitura direta do código atual (não do roadmap) para calibrar o escopo da E7:
`orchestrator/execution_manager.py`, `orchestrator/resource_router.py`,
`orchestrator/planner.py`, `orchestrator/state_machine.py`, `git_runtime/__init__.py`,
`safety/test_policy.py`, `orchestrator/fingerprint.py`, além de
`docs/architecture/04-safety-and-git-runtime.md`, `05-provider-contracts.md`,
`07-roadmap-v1.md`, ADR-0001, ADR-0004, ADR-0009. Confirmado: `state_machine.CapabilityProver`
(Protocol) e a guarda de entrada `check_entry_guard`/`_check_capability` já existem e já
recusam fail-closed com `prover=None`; `execution_manager.start_execution` já aplica todas as
guardas de `approved → executing` e levanta `NotImplementedError` de propósito — o ponto de
extensão da E7 está pronto e fechado, não é lacuna. `git_runtime/` é hoje **só leitura**
(`preflight`, `probe_head`, `list_tree`, diffs de working tree); nenhuma criação de worktree
existe ainda. `agent_runtime/` e `tool_executor/` (nomeados nas ADRs 0005/0009) **não
existem**. `safety/test_policy.py` já valida e hasheia `TestPolicy`, mas não executa nada.

- Arquivos alterados:
  - `AGENT_LOG.md` — esta entrada.
  - Nenhum outro arquivo. Nenhum código, ADR, documento de `docs/architecture/` ou roadmap
    foi tocado, conforme restrição explícita da tarefa.

- Decisões tomadas: nenhuma decisão de arquitetura foi **fixada** — a tarefa é uma proposta,
  apresentada ao Pedro no chat e aguardando aprovação. Resumo do que foi proposto:
  - **Model Router**: tabela determinística `(risk, complexity, agent_role) → (tier, reasoning_effort)`,
    sem LLM, no mesmo padrão do Resource Router; resolve para `model_id` concreto via config
    do adaptador (preserva neutralidade de provider); entra no fingerprint via
    `model_policy_hash`, nascendo **inerte em E7** (mesmo padrão que `MAX_FIX_ROUNDS` já usa
    desde a E6) e ativando em E8 junto com `developer_binding`.
  - **Escopo mínimo da E7**, em 8 incrementos: `ProviderCapabilityProfile` em `safety/`;
    interfaces/DTOs de `agent_runtime/` (sem adaptador concreto); supervisor de processo
    (timeout/cancelamento/kill de árvore); criação de worktree em `git_runtime/` (só
    `worktree add/remove/prune`, nenhum verbo mutante novo); `ToolExecutor`/`ToolExecutorFactory`
    em `tool_executor/` com a superfície fechada de 9 operações; enforcement de capability
    fail-closed com um `CapabilityProver` **fake** (sem adaptador real); verificação
    pós-execução + `start_execution` fim-a-fim (ainda só com fakes de Developer/TestRunner);
    tabela do Model Router inerte.
  - **Vercel/Supabase**: recomendado **não implementar antes de E11/E12**. Worker local deve
    ser sempre quem **puxa** tarefas (poll autenticado ou canal Realtime), nunca quem escuta
    porta aberta. FastAPI permanece local durante E7–E11; controle de estado/aprovação só
    migraria para um control-plane hospedado depois, como decisão separada. SQLite→Postgres:
    nenhuma mudança necessária agora (SQLAlchemy + Alembic já são migration-friendly); único
    ponto real de atenção identificado é `db/conflicts.py`, que classifica conflito de
    escrita por código de erro **específico do SQLite** (`SQLITE_BUSY_SNAPSHOT`) e vai
    precisar de um classificador irmão para Postgres quando a migração acontecer — registrado,
    não corrigido (fora de escopo).
  - Adaptador do Claude Code para E8: recomendado o **Agent SDK** (loop de tool-use
    programático) em vez de envolver a CLI interativa, para que `MediatedTools` mapeie
    diretamente no schema de ferramentas do SDK (`enforcement_method = api_tool_schema`).

- Pendências (decisões que aguardam aprovação do Pedro, listadas na análise apresentada no
  chat): nomes/local exato de `agent_runtime/`/`tool_executor/`; adaptador do Claude Code
  (Agent SDK vs. CLI); local do supervisor de processo; ativar Model Router inerte já em E7
  vs. adiar para E8; momento de revisitar divisão control-plane/execution-plane para
  Vercel/Supabase; autorização para iniciar a implementação de E7; confirmação de que a
  branch de trabalho de E7 nasce de `main` (`0fe357e`).

Nada commitado. Nada pushado. Nenhuma branch criada. Nenhum código de E7 escrito — tarefa
era só análise e planejamento, conforme solicitado.

## 2026-09-29 — Claude Sonnet 5.5 (effort: high) — E7.1: Provider Capability Profile (contrato puro)

Implementação restrita da E7.1, autorizada pelo Pedro com as seis decisões da revisão
independente. Branch `e7/01-capability-profile`, criada a partir de `main` em `0fe357e` com o
`AGENT_LOG.md` (entrada anterior de planejamento, sem commit) preservado. Sem commit, sem
push, sem PR.

O que foi feito: contrato tipado das **sete** capabilities (`read_files`, `write_files`,
`execute_commands`, `git_read`, `git_write`, `network`, `external_paths`) e dos **quatro**
modos (`disabled`, `mediated`, `fixed_operations_only`, `unmediated`), com política de
aceitação da V1 = igualdade **exata** com o perfil oficial do papel (Developer/Auditor).
`unmediated` é representável (diagnóstico) e nunca aceito. `fingerprint_v1_projection` valida
o perfil completo (`require_v1`, Developer) **antes** de projetar o documento histórico —
necessário porque o documento v1 só cobre 4 capabilities (`network` sai como `network_access`;
`git_read`, `git_write` e `external_paths` não entram no hash).

- Arquivos alterados:
  - `api/app/safety/capability_profile.py` — novo.
  - `api/app/safety/__init__.py` — exports + nota de docstring.
  - `api/tests/test_capability_profile_e7_1.py` — novo. Vetores literais capturados em
    `0fe357e` antes da edição: JSON do perfil requerido, hash `22995a18…040aa1`, e
    fingerprint v1 completo `ac3adc91…27731c`.
  - `AGENT_LOG.md` — esta entrada.
  - NÃO tocados: `docs/`, ADRs, `orchestrator/` (`fingerprint.py` intacto), migrações, `src/`.

- Decisões tomadas:
  - `execution_fingerprint` v1 e `REQUIRED_TOOL_PROFILE` preservados byte a byte; sem
    `model_policy_hash`; `developer_binding`/`auditor_binding` intocados.
  - `orchestrator/` não importa o módulo novo (teste garante); a ligação com a guarda
    `approved → executing` fica para incremento posterior.
  - `enforcement_method`/`enforcement_evidence` ficam fora: pertencem à prova, não ao perfil.
  - Testes usam só dados puros; nenhuma prova de enforcement real foi feita nem alegada.

- Gates: ruff check, ruff format --check e mypy limpos; suíte completa do backend com exit 0
  (3 testes `skipped`, causa não investigada nesta sessão — a linha-resumo do pytest não foi
  capturada). Um mutante deliberado na projeção foi detectado pelos testes.

- Pendências: auditoria independente (Codex); E7.2–E7.8 não iniciadas; decisão Agent SDK vs
  CLI e Model Router ficam para E8; testes reais no Windows para supervisão/isolamento nas
  etapas futuras.

## 2026-09-29 — Claude Sonnet 5.5 (effort: medium) — E7.1: evidências de gates (pós-BLOCKED do Codex)

Só validação; nenhum código alterado. Venv oficial
`C:\Users\pedro\AppData\Local\FreelanceFocus\venvs\api` (Python 3.11.9, pytest 8.4.2,
fastapi 0.115.14, alembic 1.14.1, sqlalchemy 2.0.54, starlette 0.46.2, anyio 4.14.2,
ruff 0.9.10, mypy 1.20.2 — todos dentro das faixas do `pyproject.toml`).

- E7.1: 96 passed. Afetados (E7.1 + architecture + fingerprint + state_machine + planner):
  488 passed. Suíte completa: **2005 passed, 6 skipped, 0 failed, 0 errors** (666 s).
- Skips (6, todos preexistentes, ambiente Windows): 1 "volumes distintos" e 2 "symlink
  indisponível" em `test_path_runtime.py`; 3 "symlink indisponível" em
  `test_security_regressions.py`.
- Correção: a entrada anterior dizia "3 skipped". Estava errada — o `tail` só mostrava o
  segundo grupo de `sss`; o primeiro (78%) ficou fora da janela. O número real é 6.
- ruff check, ruff format --check (118 arquivos) e mypy (115 arquivos): limpos.
- Logs fora do repo: scratchpad da sessão (`run1_e71.log`, `run2_affected.log`,
  `run3_full.log`, `static_*.log`).
- As falhas/erros do Codex (3 failed, 622 errors) vieram de Python 3.14/pytest 9/FastAPI
  0.135 fora das faixas do projeto; não reproduzem no ambiente correto.

## 2026-09-29 — Claude Sonnet 5.5 (effort: high) — E7.2: contratos do Agent Runtime

Branch `e7/02-agent-runtime-contracts`, criada de `main` (`0aef0eb`, contém E7.1 `cf6d041`).
Sem commit, push ou PR.

- Arquivos criados:
  - `api/app/tool_executor/{__init__,contracts,validation}.py` — só contratos: as 9 operações
    (`ToolRequest` = união fechada), `ToolResult`, `MediatedUsage`, `ExecutionWorkspaceRef`,
    `RunScope`, Protocols `MediatedTools`/`ToolExecutor`/`ToolExecutorFactory`.
  - `api/app/agent_runtime/{__init__,dto,declaration,interfaces}.py` — requests/results,
    `RunLimits`, `CancelToken`, Protocols `DeveloperProvider`/`AuditorProvider`/`TestRunner`,
    `EnforcementMethod`/`EnforcementEvidence`/`CapabilityDeclaration`.
  - `api/tests/test_agent_runtime_contracts_e7_2.py` (73 testes).
- Arquivos alterados: `api/tests/test_architecture.py` (+5 testes de fronteira). `docs/`,
  `state_machine.py`, `fingerprint.py`, `execution_manager.py`, `src/` intocados.
- Decisões:
  - Contratos de ferramenta em `tool_executor/contracts.py`; `agent_runtime` importa e
    reexporta (`agent_runtime → tool_executor`, nunca o inverso).
  - **Declaração ≠ prova**: `CapabilityDeclaration` não tem `proven`, não expõe
    `effective_profile_hash` e não constrói `CapabilityProof`. Valida só coerência
    (método sem evidência, `not_enforceable` com evidência, segredo na evidência) e calcula
    `declaration_hash` para correlação. Prova real fica para E7.6/E8. Auditor não herda a
    projeção do fingerprint v1.
  - `TestRequest` reutiliza `safety.TestPolicy`; `command_hash`/`policy_hash` continuam
    métodos dela, nada recalculado.
  - `files_read=None` ⇔ `files_read_source=unavailable`; `()` é "leu zero".
- Gates (venv oficial): novos 73 passed; novos + architecture + E7.1: 337 passed; suíte
  completa **2097 passed, 6 skipped, 0 failed** (741 s, exit 0; skips = os 6 preexistentes
  de symlink/volume no Windows). `ruff check` exit 0; `ruff format --check` exit 0 (126
  arquivos); `mypy` exit 0 (123 arquivos, strict — confere assinaturas dos Protocols por
  atribuição de fakes).
- Pendências: auditoria independente (Codex); E7.3 não iniciada; conexão com
  `start_execution`/`CapabilityProver` só na E7.6/E8.

## 2026-09-29 — Claude Sonnet 5.5 (effort: medium) — E7.2: correção do P2 da auditoria

Auditoria Codex da E7.2: BLOCKED por um único P2. Ainda sem commit.

- Causa raiz: `_ref` em `tool_executor/contracts.py` recusava opção (`-`) e espaço, mas aceitava
  `:`. `GitShow(ref="HEAD:.env")` passava na construção e, para o git, `<rev>:<caminho>`
  lê um arquivo — contornaria a checagem de `path` se o executor validasse só esse campo.
- Correção mínima: `_ref` recusa `:` (nome de ref git válido nunca o contém). `_ref` já é
  usada por `GitShow.ref`, `GitDiff.ref` e `GitListTree.ref` — os únicos campos de ref.
- Testes novos (+14): `HEAD:.env`, `HEAD:README.md`, `HEAD^{tree}:.env`, `:/.env`, `a:b`, `:`
  recusados em `GitShow`/`GitDiff`/`GitListTree` na construção; `HEAD`, sha1/sha256,
  `refs/heads/main`, `HEAD~2`, `v1.0`, `main^{tree}` continuam aceitas.
- Gates: novos+architecture+E7.1 351 passed; ruff check/format --check e mypy exit 0;
  suíte completa **2111 passed, 6 skipped, 0 failed** (776 s). Skips (preexistentes,
  Windows): 1 "volumes distintos" e 2 "symlink indisponível" em `test_path_runtime.py`;
  3 "symlink indisponível" em `test_security_regressions.py`.
- Fora de escopo/inalterado: fingerprint v1, docs, arquitetura de pacotes.

## 2026-09-29 — Claude Opus 5.5 (effort: high) — E7.3: Supervisor de Processos

Branch `e7/03-process-supervisor`, criada de `main` em `2247197` (merge da E7.2, PR #3).
Sem commit, push ou PR — aguarda auditoria independente.

- Autorização registrada: Pedro aprovou o planejamento da E7.3 e as decisões D1–D7 — pacote
  próprio `process_runtime/` (D1); Job Objects no Windows (D2); `subprocess` permitido só em
  `git_runtime/` e `process_runtime/` (D3); término forçado no Windows na ausência de
  mecanismo cooperativo comprovado (D4); executável e `cwd` absolutos, `.bat`/`.cmd`
  recusados (D5); job de CI `windows-latest` só para o Supervisor (D6); `git_runtime` não
  migra (D7). Ajustes aprovados: só APIs Win32 documentadas (retomada por Toolhelp32 +
  `ResumeThread`, sem `NtResumeProcess`); limites > 0; saída limitada durante a leitura.
  Autorizou também um adendo mínimo em `docs/` para registrar isso.
- Arquivos criados: `api/app/process_runtime/{__init__,contracts,supervisor,_windows,_posix}.py`;
  `api/tests/test_process_runtime_e7_3.py` (88 testes: 85 rodam no Windows, 3 só no Linux).
- Arquivos alterados: `api/tests/test_architecture.py` (allowlist de `subprocess` + 5 gates
  E7.3); `.github/workflows/api-ci.yml` (job `process-runtime-windows`);
  `docs/architecture/01-v1-architecture.md` e `04-safety-and-git-runtime.md` (adendos E7.3,
  só acréscimos). NÃO tocados: `agent_runtime/`, `tool_executor/`, `git_runtime/`,
  `orchestrator/`, `safety/`, migrações, roadmap, ADRs, `src/`.
- Decisões de implementação:
  - API pública: `ProcessSpec`, `ProcessOutcome` (`exited`/`timeout`/`cancelled`/
    `supervision_failed`), `ProcessResult`, `InvalidProcessSpec`, `run_supervised(spec,
    is_cancelled)`. Cancelamento é um callable (`cancel_token.is_cancelled`), sem importar
    `agent_runtime` nem duplicar `CancelToken`/`RunLimits`.
  - Precedência: saída observada antes do primeiro sinal > cancelamento > timeout.
    Cancelado antes do início não cria processo.
  - Fail closed: árvore não confirmada morta ou captura sem EOF viram `supervision_failed`;
    fora dele, `tree_confirmed_dead` e `output_complete` são sempre verdadeiros.
  - Windows: a contabilidade do Job (`ActiveProcesses == 0`) zera 3–9 ms antes de os
    processos ficarem sinalizados (medido); a confirmação também espera handles
    `SYNCHRONIZE` dos membros capturados antes do término (validados por `IsProcessInJob`).
  - Windows: o `conhost.exe` do `CREATE_NO_WINDOW` vive no Job ~4 ms após o raiz; é
    excluído de `orphans_killed` pelo caminho da imagem no diretório de sistema (não por
    espera), mas é encerrado como todos.
  - Leitura: `PeekNamedPipe` (Windows) / `poll` (POSIX) + `os.read` em blocos de 64 KiB;
    memória por stream ≤ limite + um bloco; threads leitoras sempre param em prazo fixo.
- Gates (venv oficial; Python 3.11.9, pytest 8.4.2, ruff 0.9.10, mypy 1.20.2), Windows 11:
  E7.3 **85 passed, 3 skipped** (Linux-only), estável em 5 repetições e sob carga;
  architecture 183 passed; afetados (E7.3 + architecture + E7.2 + E7.1 + git_runtime) 488
  passed, 3 skipped; suíte completa **2211 passed, 9 skipped, 0 failed** (810 s; skips = 6
  preexistentes de symlink/volume + 3 Linux-only); `ruff check`, `ruff format --check`
  (132 arquivos), `mypy` (129 arquivos) e `mypy --platform linux` exit 0.
  10 mutantes deliberados (gates de arquitetura, limite de leitura, precedência,
  confirmação, `KILL_ON_JOB_CLOSE`, órfãos, `.bat`) — todos detectados.
- Pendências: caminho POSIX **não executado localmente** (sem WSL/Docker) — valida na CI
  Linux; job Windows da CI ainda não rodou (só no push/PR); auditoria independente
  (Codex); E7.4 não iniciada.

## 2026-09-29 — Claude Opus 5.5 (effort: high) — E7.3: correção do P2 da auditoria (Windows)

Auditoria Codex da E7.3: BLOCKED por um único P2 em `api/app/process_runtime/_windows.py`.
Ainda sem commit, push ou PR; branch `e7/03-process-supervisor`.

- Causa raiz (confirmada por sonda isolada antes de alterar código): `_job_process_ids`
  aceitava resposta com sucesso e `NumberOfProcessIdsInList < NumberOfAssignedProcesses`
  (lista parcial: 1 PID devolvido de 7 ativos); `_capture_members` tratava falha de
  `OpenProcess` e falha da chamada `IsProcessInJob` como "membro ausente" — com o neto
  inobservável ou nenhum membro capturado, `confirm_dead` devolvia `True`. A sonda mostrou
  ainda que, após `TerminateJobObject`, a lista do Job e `ActiveProcesses` zeram na hora,
  4–6 ms antes de os processos ficarem sinalizados: a evidência só pode vir de captura
  anterior ao término, e essa captura precisa ser fechada.
- Correção (só `_windows.py`; contratos, POSIX, docs e arquitetura intocados):
  - `_job_process_ids`: só devolve lista completa (sucesso **e** listados == atribuídos);
    parcial ou `ERROR_MORE_DATA` amplia o buffer (`max(2×, atribuídos+16)`); resposta
    inconsistente, erro da API, teto de 2^20 processos ou 16 consultas sem completar →
    `OSError`.
  - `_freeze`: antes da captura, `ActiveProcessLimit = 1` (com `KILL_ON_JOB_CLOSE`
    mantido). Medido: membros atuais seguem vivos e novo `CreateProcess` no Job falha com
    `ERROR_NOT_ENOUGH_QUOTA` (1816) sem o filho existir.
  - `_open_member`: (D) no Job → handle; (C) `OpenProcess` → `ERROR_INVALID_PARAMETER` →
    já desmontado; (B) `IsProcessInJob` bem-sucedido dizendo que não → PID reutilizado,
    membro já não existe; (A) qualquer outra falha de `OpenProcess` ou falha da chamada
    `IsProcessInJob` → `OSError`. A nunca vira B.
  - `_capture_members`: rodadas até a lista completa não trazer PID novo (máx. 8);
    qualquer exceção (não só `OSError`) marca a captura como falha sem impedir o término.
  - `confirm_dead`: `True` só com `ActiveProcesses == 0`, lista completa vazia, todo handle
    capturado sinalizado, raiz colhido **e** captura completa. Sem captura completa espera
    e colhe o raiz mesmo assim (nada fica pendurado), mas devolve `False`.
- Testes: +18 de regressão em `test_process_runtime_e7_3.py` (lista parcial/`MORE_DATA`/
  inconsistente/nunca completa/acima do teto; `OpenProcess` 87 vs 5; `IsProcessInJob`
  falhando vs `FALSE`; lista que muda entre consultas; lista instável; exceção não-`OSError`;
  `ActiveProcesses == 0` sem lista vazia; ponta a ponta com o neto inobservável →
  `SUPERVISION_FAILED` e árvore morta; congelamento real; captura real com ordem
  `freeze → terminate`). 10 reversões deliberadas ao comportamento anterior — todas
  derrubam o teste correspondente.
- Nota não bloqueante (`time.monotonic`): a anotação do teste foi reescrita com a fonte. No
  Python 3.11 fixado pelo projeto, `time.get_clock_info("monotonic")` reporta
  `GetTickCount64()` e resolução 0,015625 s (passo mínimo observado: 15 ms); a partir do
  Python 3.13 o `monotonic` do Windows usa `QueryPerformanceCounter`. A afirmação anterior
  valia para 3.11, mas não dizia a versão. Lógica de timeout inalterada.
- Gates (venv oficial, Python 3.11.9, Windows 11): regressão P2 18 passed; E7.3 **103
  passed, 3 skipped** (Linux-only), 3 repetições estáveis; árvore real 10 passed;
  architecture 183 passed; afetados 506 passed, 3 skipped; suíte completa **2229 passed, 9
  skipped, 0 failed** (768 s; skips = 6 preexistentes + 3 Linux-only); `ruff check`,
  `ruff format --check` (132), `mypy` (129) e `mypy --platform linux` exit 0. Nenhum
  processo residual.
- Pendências: reverificação independente (Codex); CI Windows/Linux ainda não executada;
  E7.4 não iniciada.

## 2026-09-29 — Claude Opus 5.5 (effort: medium) — E7.3: falso `orphans_killed` no windows-latest (PR #4)

Primeiro gate real da CI Windows falhou em `test_processo_simples_conclui` (`orphans_killed=True`
num processo sem filhos). Quatro commits de diagnóstico temporários no PR #4 identificaram,
no runner (Windows 10.0.26100), o PID extra: `conhost.exe`, filho direto do raiz, ainda no
Job, com `QueryFullProcessImageNameW` negado (`ERROR_ACCESS_DENIED`) e extinto em < 50 ms —
imagem ilegível era contada como órfã. Localmente não reproduz (0/30); no runner, 21/30.

- Correção (só `orphans_alive` e auxiliares em `_windows.py`; `confirm_dead`, `_freeze`,
  `_capture_members` e a correção do P2 intocados): imagem legível segue a regra do caminho
  `System32\conhost.exe`; imagem ilegível usa snapshot Toolhelp32 (`Process32FirstW`/
  `Process32NextW`) — só não é órfão o `conhost.exe` cujo pai é exatamente o raiz; outro
  nome/pai → órfão; ausente do snapshot → relê a lista completa do Job (fora = terminou,
  ainda listado = órfão); snapshot falho → órfão. Afeta só `orphans_killed`.
- Diagnóstico removido integralmente (arquivo de testes voltou ao de `3651664` antes dos
  testes novos). `test_architecture.py`: as duas APIs novas na lista Win32 permitida.
- Testes: 12 unitários de classificação + snapshot real + caso simples repetido 25×.
  4 mutantes da classificação, todos detectados.
- Gates locais (Windows): E7.3 116 passed, 3 skipped (2×); architecture 183; afetados 519
  passed, 3 skipped; ruff check/format, mypy e mypy --platform linux exit 0. Suíte completa
  não repetida (só `orphans_alive` mudou; baseline do PR: 2229 passed).
- Pendências: CI do PR; reverificação do Codex; merge não autorizado; E7.4 não iniciada.

## 2026-09-29 — Claude Opus 5.5 (effort: medium) — E7.3: P2 do `conhost.exe` homônimo (PR #4)

Reverificação do Codex sobre `4b63e539`: P2 — no fallback Toolhelp32 (imagem ilegível), nome
`conhost.exe` + pai = raiz bastava para não contar como órfão; um `C:\Temp\conhost.exe` real,
filho do raiz, vivo no Job, seria escondido (`orphans_killed=False`). `tree_confirmed_dead`
não era afetado.

- Correção (só `ProcessTree.orphans_alive`; por AST, `_freeze`, `_job_process_ids`,
  `_open_member`, `_capture_members`, `confirm_dead`, `kill`, `close`, `start` idênticos):
  nome + pai passam a ser só **candidato**; ele precisa sair sozinho da lista completa do
  Job em até `_CONSOLE_SETTLE_S = 0,25 s` (runner: < 50 ms), senão é órfão. Cada volta
  relê e reclassifica a lista inteira, então PID novo ou remanescente durante a janela é
  classificado normalmente. Imagem legível: regra do caminho inalterada. A janela só
  observa; não sinaliza nada (D4 inalterada) e não entra na confirmação de morte.
- Testes: `test_conhost_homonimo_que_permanece_no_job_e_orfao` + 12 casos (A–J), e o caso
  simples real repetido 25× preservado. O teste do finding e os casos B, C, C2 e D falham
  com o `_windows.py` de `4b63e539` e passam com a correção.
- Gates locais (Windows): E7.3 121 passed, 3 skipped (2×); architecture 183; afetados 524
  passed, 3 skipped; ruff check/format, mypy e mypy --platform linux exit 0.
- Pendências: CI do PR; reverificação pontual do Codex; merge não autorizado; E7.4 não iniciada.

## 2026-09-30 — Claude Opus 5.5 (effort: high) — E7.4: Git Worktree Isolation

Implementação da E7.4 com as decisões D1–D10 do planejamento revisadas por Pedro (D5 alterada:
sem `BRANCH_RESUMABLE`; D6 alterada: `worktree add` sob `process_runtime`). `main` local
avançada por fast-forward (`2247197` → `6d7d226`, `git fetch origin main:main`); branch
`e7/04-worktree-isolation` criada exatamente de `6d7d226`. Branches anteriores preservadas.
Sem commit, sem push, sem PR.

- **Addendum autorizado por Pedro nesta sessão** (estritamente aditivo): `docs/architecture/01`
  §2 (aresta `git_runtime → process_runtime` só para mutação de lifecycle de worktree; leituras
  no runner atual; `git_runtime` dono semântico; provider sem acesso) e `docs/architecture/04`
  §8 (supervisão; `reset --hard` interno do `worktree add` na worktree nova, runtime continua
  proibido de construí-lo; hooks e filtros neutralizados na criação). Nada mais em `docs/`.
- Arquivos: novos `api/app/git_runtime/worktree.py`, `api/app/safety/worktree_location.py`,
  `api/tests/test_git_worktree_e7_4.py`, `api/tests/test_worktree_root_e7_4.py`; alterados
  `git_runtime/__init__.py` (reexportação + docstring), `safety/types.py`
  (`WorktreeRootFacts`, `WorktreeRoot`), `safety/__init__.py`, `path_runtime.py`
  (`inspect_worktree_root`/`prepare_worktree_root`), `config.py` (`worktrees_dir`,
  `extra_sync_roots`, `sync_roots`, `ensure_worktrees_dir`), `tests/test_architecture.py`,
  `.github/workflows/api-ci.yml` (job `process-runtime-windows` estendido, mesmo nome).
  Não tocados: `orchestrator/`, `agent_runtime/`, `tool_executor/`, `db/`, `src/`.
- Decisões de implementação: start-point `<sha>^{commit}` + pós-condição `HEAD == base`
  (ref homônima de 40 hex testada nos 5 namespaces); só `ABSENT` cria, só `REUSABLE` reusa;
  branch `ff/task-<id8>` existente sem worktree reusável → `BRANCH_ORPHANED` (recusa);
  limpeza estrita inclui ignorados e marcadores de operação no git dir **da worktree**;
  `clean=` também neutralizado (para o `status` de conferência); caminho **pedido** com link
  e reparse na cadeia canônica recusam a raiz; `WorktreeRoot` amarrada por identidade ao
  toplevel e ao `.git` comum do repositório.
- Evidência real: `%LOCALAPPDATA%\FreelanceFocus` canoniza para
  `…\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\FreelanceFocus` (MSIX) e é aceito;
  `OneDrive\Desktop` recusado (`worktree_root.sync_root`). Contrafactual descartável (fora da
  suíte): trocando `process_runtime` por `subprocess.run(timeout)`, o `sh.exe` neto do filtro
  sobreviveu com o `git` pai morto e o chamador travou — processos encerrados por PID.
- Gates locais (Windows, Python 3.11.9, git 2.53.0.windows.2, pytest 8.4.2, mypy 1.20.2):
  E7.4 137 passed, 1 skipped; git_runtime/path/safety/architecture/process_runtime 416 passed,
  6 skipped; suíte completa 2393 passed, 10 skipped (exit 0); ruff check, ruff format --check,
  mypy e mypy --platform linux exit 0; `git diff --check` limpo.
- Pendências: auditoria independente do Codex; CI (Linux + Windows) só roda após push, não
  autorizado; Execution Manager (guarda de id8 único, ligação em `start_execution`,
  reconciliação) fica para as próximas subetapas; E7.5/E8 não iniciadas.

## 2026-09-30 — Claude Opus 5.5 (effort: high) — E7.4: finding pré-auditoria (filtros por includeIf)

Fecha o risco "filtros via `includeIf gitdir:`" que o relatório da E7.4 deixara como residual.
Reproduzido **antes** de mudar código, com a implementação anterior e git real em repos
descartáveis: driver `filter.evil.{process,smudge}` só num `includeIf
"gitdir:<repo>/.git/worktrees/**"` + `* filter=evil` → marcador gravado pelo filho
`git reset --hard` interno do `worktree add` (a enumeração de `filter.*` rodava da principal,
que não vê o include). Mesma classe: `core.attributesFile` condicional, `.git/info/attributes`
com driver condicional, `attr.tree` condicional. Achados adjacentes reproduzidos: sparse da
principal copiado para a worktree nova (árvore parcial com `status` limpo → aceita como
`REUSABLE`) e `assume-unchanged` escondendo modificação do `status`. O `status` da inspeção
**não** era vulnerável (enumera no próprio contexto do alvo); agora travado por teste com
controle vivo.

- Correção (só `git_runtime/worktree.py` + `_run_git` ganhou `stdin` opcional):
  `--attr-source=<base>^{commit}` e `-c core.attributesFile=/dev/null` no `add`; drivers =
  os **aplicados** pelo atributo `filter` a algum arquivo do base (`ls-tree` → `check-attr
  --stdin`, sob as mesmas fontes fixadas) ∪ os configurados; `-c core.sparseCheckout=false`
  e `core.sparseCheckoutCone=false`; `--no-pager`; reuso exige `ls-files -v` só com `H`.
  Git < 2.42 (sem `--attr-source`) fecha como `UNVERIFIABLE`.
- Adendo E7.4 de `docs/architecture/04` §8, item 3, **complementado** (mesma autorização da
  sessão, mudança semântica da estratégia): driver aplicado por atributo, fontes fixadas,
  sparse desligado.
- Testes novos: 15 (includeIf process/smudge, attributesFile, info/attributes, attr.tree,
  LFS por includeIf, macro/aninhado, valor inseguro, git sem `--attr-source`, clean no
  `status` da inspeção com dois controles, textconv/diff externo/pager/editor, sparse ×2,
  assume-unchanged/skip-worktree). Contrafactual por mutação real de `worktree.py`
  (restaurado por sha256): M1 5 falham, M2 2 falham, M3 2 falham.
- Gates (Windows): E7.4 152 passed, 1 skipped; git_runtime/architecture/process_runtime/
  path/safety 416 passed, 6 skipped; afetados 79 passed; suíte completa 2407 passed,
  10 skipped (exit 0); ruff check/format, mypy, mypy --platform linux exit 0.
- Pendências: revisão do Pedro antes do Codex; commit/push não autorizados.

## 2026-09-30 — Claude Opus 5.5 (effort: high) — E7.4: correção estrutural dos P2-001/P2-002 (Codex BLOCKED)

Auditoria Codex BLOCKED por dois P2: (001) `set`/`unset`/`unspecified` como **valor** de
`filter=` eram descartados pelo parser do `check-attr` e o driver homônimo executava; (002)
corrida em `.git/info/attributes` entre a enumeração e o checkout executava `filter.two`.
Decisão de Pedro: eliminar o pipeline de checkout/conversão do Git da criação.

- Reprodução antes de mudar código (git real, repos descartáveis): P2-001 executou 3/3
  (`set`, `unset`, `unspecified`) e P2-002 executou em 2/6 rodadas (7077 alternâncias) no
  código anterior; os mesmos reprodutores no código novo: 0 execuções (5987 alternâncias).
- PoC: `worktree add --no-checkout` só cria `git branch` interno (nenhum `reset --hard`), alvo só
  com `.git`, sem índice; `read-tree --no-sparse-checkout` sem `-u`: zero filhos, zero arquivos,
  índice == árvore; `ls-files`/`cat-file --batch`: zero filhos; 162 alternâncias de
  `info/attributes` durante tudo, nenhum marcador. Symlink (`120000`) vira arquivo comum com o
  alvo no Windows (`core.symlinks=false`) e gitlink vira diretório vazio → recusados
  (`UNSUPPORTED_TREE_ENTRY`). Partial clone: `GIT_NO_LAZY_FETCH=1`/`--no-lazy-fetch` → `missing`
  sem filho; sem eles o Git abre `fetch` + transporte.
- Novo modelo (`git_runtime/worktree.py`): snapshot `ls-tree -r -z -l` validado inteiro antes de
  qualquer escrita (`safety.decide_tree_paths`: política de path + `.git`, caixa, arquivo ×
  diretório, `MAX_PATH`); `cat-file --batch-check` confirma blobs locais; `add --no-checkout` e
  `read-tree` sem `-u` sob `process_runtime`; blobs crus em lotes de ≤ 32 MiB (oid recalculado)
  escritos pelo `CheckedTreeWriter` do `path_runtime`, **injetado** (`tree_writer=`; sem aresta
  `git_runtime → path_runtime`); cancelamento entre arquivos. Reuso **e** pós-condição pela
  mesma verificação raw: `ls-files --stage`/`-v` == base e filesystem == snapshot (sem seguir
  link, oid dos bytes, bit de execução POSIX); `git status`, `check-attr`, `--attr-source` e
  toda neutralização de filtros removidos. `_run_git` ganhou `stdin`; `GIT_NO_LAZY_FETCH=1` em
  `_GIT_ENV_OVERRIDES` (vale para as leituras antigas também).
- Addenda E7.4 de `docs/architecture/01` §2 e `04` §8 revisados (mesma autorização; diff contra
  `main` continua só de adição).
- Gates (Windows): E7.4 203 passed, 2 skipped; ruff check/format, mypy, mypy --platform linux
  exit 0. Performance (clone deste repo, 255 arquivos): `add` com checkout 0,46 s vs.
  `create_worktree` raw ~3–5 s (dominado pela verificação raw, linear); reuso 0,87 s.
- Pendências: nova auditoria do Codex; CI só após push (não autorizado); conteúdo materializado é
  o blob cru — sem conversão `autocrlf` (Windows passa a ver LF onde o checkout daria CRLF).

## 2026-09-30 — Claude Opus 5.5 (effort: high) — E7.4: três P2 da reauditoria do Codex

Reauditoria confirmou fechados os P2 de filtros/atributos e abriu três P2 novos. Modelo
no-checkout + read-tree sem `-u` + blobs crus + `CheckedTreeWriter` mantido.

- Arquivos alterados: `api/app/path_runtime.py` (`CheckedTreeWriter`),
  `api/app/git_runtime/worktree.py` (`_index_diff_argv`, `_index_matches_tree`,
  `TreeSnapshot.commit`), `api/app/git_runtime/__init__.py` (só docstring),
  `api/app/safety/worktree_location.py` (`decide_tree_paths`), testes
  `test_worktree_root_e7_4.py`, `test_git_worktree_e7_4.py`, `test_architecture.py`;
  addendum [04] §8 itens 2 e 3 (correção mínima, diff contra `main` segue só de adição).
- P2-001 (cache do writer autorizava): o registro de identidades virou só expectativa; antes de
  cada `mkdir`, da abertura e dos bytes, `_verify_chain` revalida sem reparse do volume até a
  raiz, identidade da raiz e de cada componente até o pai. Reprodutor exato (raiz movida +
  junction no caminho original entre duas chamadas) aceitava a escrita antes; agora recusa.
- P2-002 (intent-to-add invisível): `ls-files --stage/-v/-t/--format` medidos, nenhum expõe a
  flag. Escolhido `diff-index --cached --ita-invisible-in-index --no-renames --no-ext-diff
  --no-textconv --ignore-submodules=none --raw -z --exit-code <base>^{commit}` (árvore ×
  índice, sem working tree; 0 filhos com drivers maliciosos ativos). `--debug` descartado.
  Arquivo vazio do base + ita era REUSABLE antes; agora DIRTY.
- P2-003 (caixa de prefixo): `decide_tree_paths` exige uma só grafia por prefixo em toda a
  árvore (`Dir/a.txt` + `dir/b.txt` → `tree_path.case_collision`), em todo SO. Antes o
  `worktree add` chegava a rodar; agora nenhuma mutação.
- Teste novo de interrupção do `read-tree` (hook `post-index-change` infinito, timeout e
  cancelamento): árvore morta, `CREATED_INVALID`, nada limpo, nenhuma nova tentativa, nunca
  REUSABLE. Sem bug de produção.
- Gates (Windows): E7.4 244 passed, 2 skipped; suíte completa 2504 passed, 11 skipped
  (exit 0); ruff check/format, mypy, mypy --platform linux exit 0; `git diff --check` limpo.
  Performance (clone deste repo, 255 arquivos): create 4,7–6,7 s, reuso ~1 s.
- Pendências: reverificação do Codex; commit/push não autorizados.
