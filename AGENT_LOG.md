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
