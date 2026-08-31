# 06 — Fronteiras de API e UI

> Escopo: endpoints, segurança da API local, modos de execução e telas mínimas.
> **Revisado na Fase 1B.1** (AUD-006 e §11) e na **Fase 1B.3** (REAUD-003, REAUD-008).

---

## 1. Segurança da API local

### Mesma origem por construção

| Cenário | Como o browser fala com a API |
| --- | --- |
| **Execução local (produção)** | O **backend serve o SPA compilado**. API sob `/api`. → **mesma origem**, sem CORS |
| **Desenvolvimento** | Vite dev server com **proxy de `/api`** para a FastAPI; o launcher alimenta em memória o `transformIndexHtml` que injeta o token → mesma origem do ponto de vista do browser |
| **GitHub Pages** | **Nenhuma chamada à API.** Nem uma tentativa contra localhost |

Isso elimina, na raiz, requisição cross-origin para loopback, mixed content, *Private
Network Access*, retries e ruído de console.

### Defesas

1. **Bind em `127.0.0.1`**, nunca `0.0.0.0`.
2. **Validação de `Host`** contra `127.0.0.1:<porta>` / `localhost:<porta>` — DNS rebinding.
3. **Validação de `Origin` / `Sec-Fetch-Site`** — requisições que alteram estado exigem
   mesma origem.
4. **CORS negado nos dois fluxos locais.** O proxy de desenvolvimento é server-side e não
   exige headers CORS; somente a validação de `Origin` conhece a origem fixa do Vite.
   Nunca `*`.
5. **`LocalSessionToken` obrigatório em todas as rotas exceto `GET /api/health`.**

### `LocalSessionToken`

| Propriedade | Definição |
| --- | --- |
| Geração | aleatório forte (≥ 256 bits) no startup do backend |
| Rotação | a cada reinício do backend (V1); **efêmero**, nunca persistido em disco |
| **Bootstrap** | **injetado no HTML inicial**: pela FastAPI no build compilado; pelo `transformIndexHtml` alimentado em memória pelo launcher no Vite dev. Forma: `<meta name="ff-session-token" content="…">`. **Não existe rota de bootstrap** |
| Cache | HTML inicial servido com `Cache-Control: no-store` |
| Leitura no cliente | o SPA copia para memória no boot e **remove a `<meta>` do DOM** em seguida |
| Armazenamento no cliente | **memória apenas** — nunca `localStorage`, nunca `sessionStorage`, nunca cookie |
| Transporte | header `Authorization: Bearer …` |
| Proibido | URL, query string, fragmento, log, mensagem de erro, métrica |

**Bootstrap no Vite dev.** A FastAPI continua sendo a emissora do token. O launcher local
recebe o valor somente em memória e inicia o Vite com uma variável privada de processo,
sem prefixo `VITE_` e nunca gravada em `.env`. Um plugin local `transformIndexHtml` lê a
variável no lado servidor, injeta a `<meta>` e marca o HTML como `no-store`; o valor não é
substituído no bundle, não é exposto em `import.meta.env` e não é impresso. O proxy de
`/api` aponta para a mesma instância FastAPI. Assim, o fluxo de desenvolvimento não exige
uma segunda rota não autenticada. Como esse servidor entrega HTML autenticador, o Vite
dev também faz bind em `127.0.0.1`, usa allowlist explícita de `Host` e nega CORS; a
arquitetura não depende dos defaults do Vite.

> **(1B.3, REAUD-003)** `/api/session/bootstrap` foi **eliminada** — era uma segunda rota
> sem autenticação, contradizendo a própria regra. Como o HTML é transformado no servidor
> nos dois fluxos locais — FastAPI no build compilado, Vite no dev — injetar o token ali
> dispensa a rota. **`GET /api/health` é a única rota não autenticada; não existe
> segunda.**

**Header e não cookie:** cookie é anexado automaticamente pelo browser, o que recriaria a
necessidade de token anti-CSRF. Um header exigido, obtenível apenas pelo HTML de mesma
origem, resolve CSRF por construção.

**Aba maliciosa:** não lê o token (está no HTML de mesma origem; CORS nega a leitura), não
forja o header (CORS nega o preflight), e não há cookie para carregar.

**`GET /api/health`** devolve **informação mínima** — estado de prontidão e versão. Sem
caminhos, sem configuração, sem contagens, sem nada que ajude a mapear a máquina.

**Streaming:** `EventSource` **não envia headers customizados**. O progresso usa **SSE
consumido por `fetch` + `ReadableStream`**, que carrega o `Authorization`. Nenhuma rota
aceita token por query string para contornar isso.

## 2. Endpoints

Todas as rotas sob `/api`, para que o SPA possa ser servido em `/`.

| Método | Rota | Papel |
| --- | --- | --- |
| `GET` | `/api/health` | **Única** rota sem token; devolve informação mínima |
| `GET` `POST` | `/api/workspaces` | Listar e criar |
| `GET` `PATCH` | `/api/workspaces/{id}` | Detalhe e edição; `status` alterna `active` ⇄ `archived` |
| `GET` | `/api/workspaces/{id}/purge-preview` | Contagens do que uma purga removeria |
| `POST` | `/api/workspaces/{id}/purge` | **Destrutivo**; só para workspace arquivado e sem task não terminal; exige confirmação forte ([02](02-data-model.md) §11) |
| `GET` | `/api/workspaces/{id}/git` | Preflight: é repo, HEAD, branch, divergência do working tree |
| `GET` `POST` | `/api/workspaces/{id}/context` | Listar e criar entradas |
| `PATCH` `DELETE` | `/api/context/{entry_id}` | Editar e remover |
| `POST` | `/api/workspaces/{id}/context/verify` | Recalcular `fresh`/`stale`/`unknown` |
| `POST` | `/api/workspaces/{id}/context/import` | Seed a partir de `ProjectPlanning` |
| `GET` `POST` | `/api/workspaces/{id}/tasks` | Listar e criar |
| `GET` | `/api/tasks/{id}` | Estado, `phase`, plano, agentes, limites, fingerprint |
| `POST` | `/api/tasks/{id}/plan` | Congela `base_commit`, verifica contexto, seleciona, renderiza, planeja |
| `POST` | `/api/tasks/{id}/approve` | Exige o `execution_fingerprint` completo |
| `POST` | `/api/tasks/{id}/reject` | Volta para `draft` com nota |
| `POST` | `/api/tasks/{id}/cancel` | Em qualquer estado não terminal |
| `GET` | `/api/tasks/{id}/purge-preview` | Contagens do que a purga da task terminal removeria |
| `POST` | `/api/tasks/{id}/purge` | **Destrutivo**; só para task terminal, com prévia vigente e confirmação forte |
| `GET` | `/api/tasks/{id}/runs` | Runs com métricas e proveniência |
| `GET` | `/api/tasks/{id}/context` | Manifest + referência ao artefato renderizado |
| `GET` | `/api/tasks/{id}/diff` | Diff unificado, redigido |
| `GET` | `/api/tasks/{id}/findings` | Findings, filtráveis por `purpose` |
| `PATCH` | `/api/findings/{id}` | Aceitar / descartar |
| `POST` | `/api/tasks/{id}/worktree/discard` | Remove a worktree após o diff ser aproveitado |
| `GET` | `/api/tasks/{id}/events` | SSE de progresso, consumido via `fetch` |
| `GET` | `/api/metrics/summary` | Agregação por `execution_mode` e `benchmark_group_id` |

> **(1B.3)** Não existe `DELETE` em workspace. **Arquivar** é `PATCH` de `status` e é
> reversível; **purgar** é uma operação própria, precedida de prévia obrigatória e
> confirmação forte. Uma ação destrutiva não fica a um parâmetro de query de distância de
> uma rotineira.

Deliberadamente **fora** da V1: reauditoria manual, `GET /api/runs/{id}` isolado, escrita
direta de estado, rota de bootstrap de sessão, e qualquer rota que exponha o filesystem
além do workspace.

### Convenções

- Erros: `{code, message}` estável — nunca stack trace.
- `409` para transição inválida, guarda não satisfeita e fingerprint desatualizado, sempre
  com o motivo e **qual campo divergiu**.
- **Todo corpo JSON de `/api/*` passa pelo redator antes de serializar.** O
  `LocalSessionToken` do HTML inicial não é resposta de API e não passa por essa etapa —
  sua proteção é mesma origem, `no-store`, memória apenas e ausência total em log.
- Paths **relativos ao workspace**; exceção única: o `local_path` que o usuário digitou.
- Contratos nascem em Pydantic, publicados no OpenAPI, com cliente TypeScript **gerado**.

## 3. Modos da aplicação

| Modo | Determinado | Comportamento |
| --- | --- | --- |
| **`LOCAL_DEV_WORKSPACE`** | build local | Backend serve o SPA; API em `/api`; workspace completo |
| **`HOSTED_COMMERCIAL_ONLY`** | build do GitHub Pages | Comercial completo; área de workspace com estado "disponível apenas na execução local" |

**O modo é fixado em build time.** No Pages a aplicação **não faz nenhuma chamada de rede
à API** — sem probe, sem retry, sem timeout, sem console spam. No modo local, a sonda a
`GET /api/health` existe apenas como indicador de saúde: **uma única chamada memoizada por
sessão**, timeout curto, sem retry automático.

## 4. UI

### Telas da V1

```text
Dev Workspaces (lista + criação)
  └── Workspace Detail
        ├── Overview     — git preflight, divergência do working tree, saúde do contexto
        ├── Context      — editor do registry, estado fresh/stale/unknown
        └── Tasks
              └── Task Detail  — plano, contexto entregue, aprovação, progresso,
                                 diff, findings, runs
```

Das seis abas propostas na Fase 1B, três foram mantidas. *Orchestrator* não é um lugar, é
um **momento** de uma tarefa — vive no Task Detail. *Runs* duplicaria o que já aparece na
tarefa. *Metrics* é página global, adiada para E12 (comparação de dois modos) e completada
em E13 (três modos).

### O que o Task Detail precisa mostrar

- o plano e os **critérios de aceite**;
- o contexto selecionado, com estado de cada entrada e **o que foi excluído e por quê**;
- **a divergência do working tree**, com o aviso de que a execução usa o `base_commit`
  congelado e alterações não commitadas não entram;
- os agentes escolhidos, o perfil de capability e os limites;
- o botão de aprovar, que envia o `execution_fingerprint` completo — e, quando a aprovação
  é invalidada, **qual campo mudou**;
- progresso ao vivo por `phase`;
- diff, findings da **auditoria vigente** (separando `workflow_audit` de
  `benchmark_evaluation`, com as auditorias superadas acessíveis como histórico) e runs com
  tokens, duração e **proveniência** das métricas.

Antes de uma purga de workspace ou task, a UI mostra a prévia com as contagens de
workspaces, tasks, runs, findings, manifests e artefatos, e exige confirmação forte.
Arquivar não passa por isso — é reversível.

### Reaproveitamento do frontend atual

| Ativo | Uso |
| --- | --- |
| `components/Modal.tsx` | Aprovação, confirmações, preview de diff |
| `components/ProgressRing.tsx` | Progresso de execução |
| `styles/global.css` | Tokens, tema claro/escuro e grid herdados |
| Padrão de abas de `pages/ProjectDetail.tsx` | Molde do Workspace Detail |
| `UnsavedChangesModal` + `hasUnsavedPlanningChanges` | Editor de contexto |
| `PlanningEditor` | Base do editor de entradas |
| `pages/ProjectsPage.tsx` | Molde da lista de workspaces |
| `components/Layout.tsx` — `navigation[]` | Novo item de menu |

### Estado no frontend

`WorkspaceProvider` é um provider **separado** de `AppContext`. O comercial é síncrono,
local e salvo por inteiro a cada mudança; o workspace é assíncrono, remoto e com estado de
servidor. Misturá-los faria o documento comercial inteiro ser reserializado a cada evento
de progresso de uma execução.
