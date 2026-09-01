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
