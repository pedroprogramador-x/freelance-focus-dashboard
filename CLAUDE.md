# CLAUDE.md

Repositório híbrido: **frontend** React 18 + TS + Vite em `src/` · **backend**
Python/FastAPI em `api/` (o "AI Dev Workspace", infra interna).

## Antes de qualquer tarefa

1. Leia `AGENT_LOG.md` na raiz. Registre uma entrada ao terminar (formato no
   topo do arquivo).
2. Todo prompt de agente deve declarar modelo e effort level.

## Regras que não podem ser inferidas do código

- **`docs/` é arquitetura CONGELADA** (Architecture Freeze, commit `67d4df4`).
  ADRs 0001–0010 em `docs/adr/`. Não altere nada em `docs/` sem autorização
  explícita do Pedro nesta conversa.
- **`api/` — o Developer provider não recebe shell cru.**
  `execute_commands = disabled` é decisão arquitetural (ver
  `docs/adr/0009` e `docs/architecture/04`). Efeitos de arquivo são
  mediados e tipados; `source_ref` aceita só caminho literal relativo (glob
  é fail-closed até E4).
- **`.env*` é segredo por padrão** na safety policy — inclui `.env.example`.
- **Persistências disjuntas**: `localStorage` = domínio comercial (frontend);
  SQLite = AI Dev Workspace (backend). O backend não importa nada do domínio
  comercial.
- **Roadmap por fase** (E1, E2, E3…) em
  `docs/architecture/07-roadmap-v1.md`. Trabalho incremental: quality gates →
  self-review → auditoria independente quando fizer sentido → commit só
  depois de aprovado. Um commit por fase.

## Comandos

### Frontend (`src/`) — Node 22, rodar da raiz do repo

```bash
npm run lint          # eslint --max-warnings 0
npm test              # vitest run
npm run build         # tsc -b && vite build
npm run dev
```

### Backend (`api/`) — venv Python FICA FORA DO REPO

O repo está no OneDrive; o venv vive em
`C:\Users\pedro\AppData\Local\FreelanceFocus\venvs\api\`.

```bash
# rodar da pasta api/, usando o Python do venv:
PY="/c/Users/pedro/AppData/Local/FreelanceFocus/venvs/api/Scripts/python.exe"

"$PY" -m pytest
"$PY" -m ruff check .
"$PY" -m ruff format --check .
"$PY" -m mypy
"$PY" -m alembic upgrade head   # aplica o schema
"$PY" -m alembic downgrade base
```

- **Nunca** crie venv, `.db` ou `data_dir` dentro do repo. `data_dir` de
  runtime é `%LOCALAPPDATA%\FreelanceFocus\` (fora do OneDrive).
- `.github/workflows/api-ci.yml` roda os gates do backend no CI; não mexa
  em `deploy.yml` (frontend/Pages).

## Git

- Conventional Commits, minúsculas, sem escopo: `feat:`, `fix:`, `docs:`.
- Não commite/pushe sem autorização explícita do Pedro.
