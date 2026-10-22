# AGENTS.md

Instruções compartilhadas para agentes de IA (Claude Code, Codex, Antigravity,
Jules). Repositório híbrido: **frontend** React 18 + TS + Vite em `src/` ·
**backend** Python/FastAPI em `api/` (o "AI Dev Workspace", infra interna).
Orientações específicas do Claude Code ficam em `CLAUDE.md`.

## Antes de qualquer tarefa

1. Consulte o `AGENT_LOG.md` de forma **seletiva** (ele é longo): leia o
   cabeçalho (formato das entradas), liste os títulos (`^## `) e leia só as
   entradas mais recentes e as do incremento em questão. Ao terminar, registre
   uma entrada no fim do arquivo.
2. Todo prompt de agente deve declarar modelo e effort level.

## Regras que não podem ser inferidas do código

- **`docs/` é arquitetura CONGELADA** (Architecture Freeze, commit `67d4df4`).
  ADRs 0001–0010 em `docs/adr/`. Não altere nada em `docs/` sem autorização
  explícita do Pedro nesta conversa.
- **`api/` — o Developer provider não recebe shell cru.**
  `execute_commands = disabled` é decisão arquitetural (ver
  `docs/adr/0009` e `docs/architecture/04`). Efeitos de arquivo são
  mediados e tipados.
- **`source_ref`** — duas camadas, não confunda:
  - `app.safety.source_refs.validate_source_ref` é o envelope de segurança
    (sem `..`, absoluto, UNC, segredo etc.). Por padrão aceita **só caminho
    literal relativo** e recusa qualquer sintaxe de glob (fail-closed).
  - A gramática de glob existe em um único lugar, o expansor canônico
    `app.context_engine.source_ref_expansion`, que chama o validador com
    `allow_glob_syntax=True` (apenas pula a recusa de metacaracteres) e nega o
    `source_ref` inteiro se qualquer arquivo expandido for segredo.
  - Interfaces que não passam pelo expansor continuam exigindo caminho
    literal. Não presuma suporte a glob fora do Context Engine; confirme no
    código e nos contratos antes de usar.
- **`.env*` é segredo por padrão** na safety policy — inclui `.env.example`.
  Não leia nem imprima.
- **Persistências disjuntas**: `localStorage` = domínio comercial (frontend);
  SQLite = AI Dev Workspace (backend). O backend não importa nada do domínio
  comercial.
- **Execução operacional (E8.4.5)**: nenhuma execução operacional é habilitada
  antes de (1) reserva durável independente do estado terminal da task,
  (2) encerramento comprovado dos recursos e (3) recuperação idempotente após
  crash (`docs/architecture/02-data-model.md`). I1–I4 entregaram só parte
  disso; não habilite caminhos operacionais, liberação de reserva nem
  finalização de recursos sem aprovação explícita do Pedro.
- **Auditoria independente**: o Codex é o auditor independente. Quem
  implementa ou valida não declara GREEN/APPROVED em nome dele; entregue fatos
  (comandos, exit codes, testes ignorados) e aguarde a auditoria.
- **Roadmap por fase** (E1, E2, E3…) em
  `docs/architecture/07-roadmap-v1.md`. Trabalho incremental: quality gates →
  self-review → auditoria independente quando fizer sentido → commit só
  depois de aprovado. Um commit por fase.

## Comandos

### Frontend (`src/`) — Node 22, rodar da raiz do repo

Valem em PowerShell e em Bash:

```bash
npm run lint          # eslint --max-warnings 0
npm test              # vitest run
npm run build         # tsc -b && vite build
```

### AI Dev Workspace — fluxo de dev local (até a E11)

O frontend do AI Dev Workspace só funciona autenticado quando **a FastAPI serve o
SPA compilado** (ela injeta o `LocalSessionToken` no HTML). O fluxo suportado é:

`$PY` é o Python do venv, definido na seção "Backend" abaixo (com a checagem de
existência). Variável de ambiente por comando só funciona em Bash; em PowerShell
use `$env:`.

PowerShell:

```powershell
# da raiz do repo; base '/', assets em /assets/
$viteWasSet = Test-Path Env:VITE_APP_MODE
$viteOriginal = $env:VITE_APP_MODE
$env:VITE_APP_MODE = 'local_dev_workspace'
try { npm run build }
finally {
    if ($viteWasSet) { $env:VITE_APP_MODE = $viteOriginal }
    else { Remove-Item Env:VITE_APP_MODE -ErrorAction SilentlyContinue }
}

# da pasta api/; serve dist/ + /api
& $PY -m uvicorn app.main:app
```

Bash:

```bash
VITE_APP_MODE=local_dev_workspace npm run build   # da raiz; base '/', assets em /assets/
"$PY" -m uvicorn app.main:app                      # da pasta api/; serve dist/ + /api
```

`npm run dev` **isolado não é suportado** até a E11: sem o launcher de hot-reload
(FastAPI + Vite com token compartilhado em memória), o Vite dev server não tem token
para injetar e toda chamada à API responde 401. O launcher está adiado para a E11
(ver "Decisões adiadas" em `docs/architecture/07-roadmap-v1.md`). O `server.proxy`
em `vite.config.ts` já deixa o caminho pronto.

### Backend (`api/`) — venv Python FICA FORA DO REPO

O venv vive em `%LOCALAPPDATA%\FreelanceFocus\venvs\api\` (sempre no perfil do
usuário atual). Se não existir, pare e informe; não crie um no repo.

PowerShell (rodar da pasta `api/`):

```powershell
$PY = Join-Path $env:LOCALAPPDATA 'FreelanceFocus/venvs/api/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $PY -PathType Leaf)) { throw "venv ausente: $PY" }

& $PY -m pytest
& $PY -m ruff check .
& $PY -m ruff format --check .
& $PY -m mypy
```

Bash (Git Bash no Windows; rodar da pasta `api/`). O caminho do venv é o do
Windows: em Linux/macOS o ambiente é outro (o CI usa o do workflow), então não
reutilize este bloco como está.

```bash
PY="$LOCALAPPDATA/FreelanceFocus/venvs/api/Scripts/python.exe"
if [ ! -f "$PY" ]; then
  echo "venv ausente: $PY" >&2
  false   # o bloco termina com falha; nada abaixo roda
else
  "$PY" -m pytest
  "$PY" -m ruff check .
  "$PY" -m ruff format --check .
  "$PY" -m mypy
fi
```

- **Nunca** crie venv, `.db` ou `data_dir` dentro do repo. `data_dir` de
  runtime é `%LOCALAPPDATA%\FreelanceFocus\`.
- `.github/workflows/api-ci.yml` roda os gates do backend no CI; não mexa
  em `deploy.yml` (frontend/Pages).

### Migrações (Alembic) — fora dos gates rotineiros

Operações Alembic (`upgrade`, `downgrade`) **alteram o estado do banco** e não
fazem parte dos gates acima. Regras:

- Exigem confirmação explícita do Pedro antes de cada execução.
- Antes, identifique o banco de destino (qual arquivo/`data_dir`) e avalie os
  efeitos sobre os dados.
- `downgrade base` pode **remover estruturas e dados**; nunca o execute sem
  essa confirmação e sem saber qual banco será afetado.
- Skills e subagentes nunca executam operações Alembic automaticamente.
- Este documento não traz comandos Alembic executáveis de propósito.

## Git

- Conventional Commits, minúsculas, sem escopo: `feat:`, `fix:`, `docs:`.
- Não faça commit, push, PR nem merge sem autorização explícita do Pedro.
