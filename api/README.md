# AI Dev Workspace — backend

Fundação da **E2**. Especificação normativa: [`docs/`](../docs/README.md) na raiz do
repositório (architecture freeze `67d4df497b4f855acab1a74b8ab95a7b1fc07d96`).

## O que existe

| Área | Estado |
| --- | --- |
| FastAPI com `GET /api/health` | ✅ única rota pública; informação mínima |
| **`LocalSessionToken` + autenticação da API** | ✅ token efêmero, injetado no HTML inicial; todo `/api/*` exige `Authorization: Bearer` |
| **Validação de `Host`** | ✅ só loopback — defesa contra DNS rebinding |
| **OpenAPI / Swagger / ReDoc** | ✅ **desligados**: seriam superfícies públicas adicionais |
| SQLite + Alembic | ✅ migration inicial com as sete entidades |
| Safety Kernel (`app/safety/`) | ✅ política **pura**: path, segredos, `source_refs`, redator, `policy_hash` |
| Path Runtime (`app/path_runtime.py`) | ✅ varredura **léxica** dos componentes e abertura verificada (leitura) |

### Superfície HTTP

| Rota | Autenticação |
| --- | --- |
| `GET /api/health` | **pública** — a única, e **só neste método** |
| qualquer outro método em `/api/health` | `Authorization: Bearer <LocalSessionToken>` |
| qualquer outra `/api/*` | `Authorization: Bearer <LocalSessionToken>` |
| `GET /` | pública por desenho: é o canal que **entrega** o token |
| `/assets/*` | estática, montada só quando há build compilado |

A exceção pública é o par **(método, caminho)**, não o caminho sozinho: `POST /api/health`
exige credencial. A regra é aplicada por **prefixo** num middleware, não rota a rota —
qualquer rota nova sob `/api/` nasce protegida. Não existe `/api/session/bootstrap`: o
token vai no HTML (`<meta name="ff-session-token">`, servido com `Cache-Control:
no-store`), nunca em rota JSON, URL, log ou `localStorage`.

O header `Host` é validado por um parser **fechado** que consome a string inteira — só
`localhost`, `127.0.0.1` e `[::1]`, com porta opcional em `1..65535` e dígitos ASCII.
Nada de casamento por prefixo.

## O que **não** existe — e por quê

Interface do Dev Workspace e Workspace Registry (E3) · Context Registry e Router (E4–E5) ·
Orchestrator (E6) · `ToolExecutor`, política de comandos, worktrees, capability enforcement
(E7) · providers, agentes, Test Runner de projeto (E8+) · Ruflo (E13).

O frontend React em `src/` **não é tocado**: a E2 entrega o mecanismo de bootstrap, e a UI
que o consome é E3. Quando não há build compilado, `GET /` responde
`404 web_ui_unavailable` e a API segue funcionando.

Nenhum SDK de provider é dependência. `subprocess` não é importado em lugar nenhum — o
teste de arquitetura falha se alguém tentar.

## Ambiente

Python **3.11+**. O ambiente virtual fica **fora do OneDrive** — o repositório está numa
pasta sincronizada, e ADR-0004 mantém dados operacionais longe dela:

```bash
python -m venv "$LOCALAPPDATA/FreelanceFocus/venvs/api"
"$LOCALAPPDATA/FreelanceFocus/venvs/api/Scripts/python.exe" -m pip install -e ".[dev]"
```

## Comandos

Todos a partir de `api/`, com o Python do venv:

```bash
python -m pytest                  # testes
python -m ruff check .            # lint
python -m ruff format --check .   # formatação
python -m mypy                    # typecheck (strict)
python -m alembic upgrade head    # aplica o schema
python -m alembic downgrade base  # reverte
python -m uvicorn app.main:app --host 127.0.0.1 --port 8756
```

## Dados de runtime

`data_dir` resolve para `%LOCALAPPDATA%\FreelanceFocus` (Windows) ou
`$XDG_DATA_HOME/freelance-focus` (POSIX). O banco é `workspace.db` dentro dele.
**Nada é criado no import**: a pasta só nasce quando `ensure_data_dir()` é chamado, e os
testes usam sempre diretório temporário.

Sobrescritas por ambiente, prefixo `FF_`: `FF_DATA_DIR`, `FF_DATABASE_URL`, `FF_PORT`,
`FF_WEB_DIST_DIR`. `FF_HOST` só aceita loopback. **Nenhum segredo é lido do ambiente
nesta fase** — não há provider, e `SecretSettings` nasce sem campos.

## Limites declarados

**TOCTOU.** A validação de path faz três fases — pré-validação sintática, inspeção
léxica, revalidação pós-abertura — e **estreita** a janela sem fechá-la. Isso **não é
sandbox**; o risco residual está em
[`docs/architecture/04`](../docs/architecture/04-safety-and-git-runtime.md) §0 e §4.

**`source_refs`: glob não é suportado na E2.** Só **caminho literal relativo** é aceito.
Qualquer `*`, `?`, `[`, `]`, `{`, `}` ou `!` é recusado com
`source_ref.glob_not_supported` — *fail closed*, e sem tentar "sanitizar" o valor para
virar literal.

O motivo é de projeto, não de preguiça: validar glob exige uma gramática, e o dono dessa
gramática é o **expansor canônico** do Context Engine, que ainda não existe. Duas
tentativas anteriores falharam exatamente aí — uma testava o glob contra caminhos-exemplo
finitos (sonda mostra presença, nunca ausência) e a outra provava interseção segundo uma
gramática própria, que podia divergir da do expansor real. Um validador e um expansor que
discordam são um buraco com aparência de rigor.

Suporte a glob volta na **E4**, junto do expansor, para que
`semântica do validador == semântica do expansor`. Até lá a fundação é deliberadamente
conservadora.

**Symlink no Windows.** Criar symlink exige privilégio, então os testes de integração de
link são pulados nesta máquina e rodam na CI Linux. A lógica da varredura é coberta por
testes unitários que não dependem de symlink real. Junction e reparse point não
verificáveis viram `UNKNOWN`, e a política fecha — nunca `FALSE`.

**Varredura de path sem corte.** A inspeção léxica percorre **todos** os componentes
pedidos. Não há limite artificial: um corte silencioso devolveria fatos limpos para
componentes nunca inspecionados, que é o pior resultado possível — a política liberaria um
caminho que ninguém verificou.
