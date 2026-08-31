# ADR-0001 — Backend local em FastAPI + SQLite; frontend permanece na raiz

- **Status:** Aceito
- **Data:** 2026-08-31
- **Fase:** 1B

## Contexto

O Freelance Focus é hoje uma SPA React + TypeScript + Vite na raiz do repositório, com
build e deploy para GitHub Pages via `.github/workflows/deploy.yml`, 67 testes verdes e
lint com `--max-warnings 0`.

O AI Dev Workspace precisa de capacidades que um browser não tem: filesystem, Git,
execução de processos e comunicação com CLIs de provider. Isso exige um processo local.

Duas tentações estruturais apareceram: mover o frontend para `apps/web/` e criar pacotes
npm para `orchestrator`, `context-engine` e `agent-runtime`.

## Decisão

1. **O frontend permanece na raiz.** Nenhum movimento de `src/` na V1.
2. **O backend vive em `api/`**, em Python com FastAPI e SQLite.
3. **Não haverá npm workspaces** nem pacotes npm para orquestração, contexto ou runtime de
   agentes. Essas responsabilidades são do backend Python.
4. **Não haverá `packages/shared-types`.** Os contratos nascem em Pydantic, são publicados
   no OpenAPI da FastAPI e o cliente TypeScript é **gerado** a partir dele.
5. `deploy.yml` não é alterado. A CI da API entra em workflow separado.
6. **(1B.1) O backend serve o SPA compilado em execução local**, com a API sob `/api` →
   **mesma origem**. Em desenvolvimento, o Vite faz proxy de `/api`. A aplicação passa a
   ter dois modos fixados em build time: `LOCAL_DEV_WORKSPACE` e
   `HOSTED_COMMERCIAL_ONLY` (GitHub Pages), e **no Pages não há nenhuma chamada de rede à
   API** — nem uma sonda.
7. **(1B.3) O `LocalSessionToken` é injetado no HTML inicial**
   (`<meta name="ff-session-token">`, com `Cache-Control: no-store`), lido para memória e
   removido do DOM. No build compilado, a FastAPI faz a injeção. No Vite dev, o launcher
   local recebe o token da FastAPI somente em memória, inicia o Vite com um canal privado
   de processo (sem prefixo `VITE_`, sem `.env`) e um `transformIndexHtml` injeta a mesma
   meta; o token não entra no bundle nem em log. **Não existe rota de bootstrap de
   sessão:** `GET /api/health` é a **única** rota não autenticada. Servir ou transformar o
   HTML deixa de ser conveniência e passa a ser parte do mecanismo de autenticação local.
   Por isso o Vite dev também faz bind em loopback, valida `Host` por allowlist e nega
   CORS explicitamente.

## Consequências

**Positivas**

- Mudança estrutural mínima: nenhum dos ~35 arquivos de `src/` se move, nenhum import
  muda, o deploy do Pages continua idêntico.
- Uma fonte por contrato. DTO duplicado à mão entre Python e TypeScript é uma fonte
  clássica de divergência silenciosa; a geração a partir do OpenAPI a elimina.
- Orquestração fica onde estão as ferramentas: subprocessos, git e SDKs de provider são
  mais diretos em Python do que em Node aqui.

**Negativas e mitigações**

- Layout misto por um tempo (`src/` e `api/` lado a lado). Aceito; mover depois é barato e
  está registrado como decisão adiada.
- Duas toolchains (Node 22 e Python). Mitigado com CI separada, para que uma falha na API
  nunca bloqueie o deploy do web.
- O passo de geração do cliente TypeScript precisa rodar quando o contrato muda. Mitigado
  por uma verificação de *drift* na CI.
- **(1B.1)** O backend passa a ter a responsabilidade extra de servir arquivos estáticos.
  É trivial em FastAPI e paga por si: mesma origem elimina CORS, mixed content e *Private
  Network Access* de uma vez, e reduz o risco R6 a validação de `Host`/`Origin` mais um
  token em header.

## Alternativas consideradas

| Alternativa | Recusada porque |
| --- | --- |
| Split completo agora (`apps/web`, `apps/api`, `packages/*`) | Move dezenas de arquivos e arrisca o deploy antes de qualquer valor ser entregue |
| Backend em Node, dentro de workspaces npm | Perderia a naturalidade de Python para processos, git e SDKs; e ainda assim exigiria a separação de processo |
| Repositório separado para a API | Coordenação de contrato entre repositórios e CI dupla, sem benefício com um único autor |
| `packages/shared-types` mantido à mão | Duplicação garantida entre Pydantic e TypeScript |

## Revisões

| Fase | Mudança |
| --- | --- |
| 1B | Versão original |
| **1B.1** | Acrescentado o item 6 (same-origin, dois modos de build) em resposta ao finding **AUD-006**. As decisões 1–5 permanecem inalteradas |
| **1B.3** | Acrescentado o item 7: token no HTML inicial, **sem rota de bootstrap**, única rota aberta é `/api/health`; explicitado o canal somente em memória que alimenta o `transformIndexHtml` no Vite dev (**REAUD-003**) |

## Referências

[01 — Arquitetura V1](../architecture/01-v1-architecture.md) §4 ·
[06 — Fronteiras de API e UI](../architecture/06-api-and-ui-boundaries.md) §1
