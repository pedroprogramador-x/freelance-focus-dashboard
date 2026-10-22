---
name: tino-validate
description: Prepara e, com autorização, executa gates e testes relevantes às alterações; gera relatório para auditoria. Invocação manual.
disable-model-invocation: true
argument-hint: "[dry-run] [escopo opcional]"
allowed-tools: Read, Grep, Glob
model: sonnet
effort: medium
---

# tino-validate — gates e relatório de validação

Argumentos: `$ARGUMENTS`. Se contiver `dry-run` (ou `seco`), **não execute
nenhum comando**: apenas produza o plano da seção 3 e o relatório com tudo
marcado como "não executado".

Regras fixas:

- Não faça commit, push, merge nem PR. Não edite código, testes, fixtures,
  migrations, configurações ou `docs/` para "fazer passar".
- **Nunca declare GREEN/APPROVED.** Você reporta fatos; o veredito
  independente é do Codex, acionado pelo Pedro.
- Esta skill roda no contexto principal (sem `context: fork`) de propósito: os
  comandos precisam de confirmação explícita do Pedro, que um subagente
  isolado não garante. Não há `allowed-tools` para Bash: cada comando de shell
  passa pela confirmação normal de permissão.
- Não execute a suíte inteira automaticamente (regressão ampla só com pedido
  explícito do Pedro nesta conversa).

## 1. Descobrir o que mudou (somente leitura)

Com Bash (peça confirmação): `git status --short`, `git diff --stat`,
`git diff --name-only` e, para comparar com a base, `git diff --stat main...HEAD`
se o Pedro pedir. Nunca use comandos que alterem o repositório.

## 2. Mapear módulos impactados

Para cada arquivo alterado, identifique o módulo (ex.: `api/app/orchestrator`,
`api/app/safety`, `src/...`) e os testes correspondentes via Glob/Grep em
`api/tests/` ou `src/**/*.test.*`. Aponte arquivos sem teste correspondente.

## 3. Plano de comandos (propor, não rodar sem autorização)

Separe em dois blocos e mostre cada comando exato:

- **Direcionados** (padrão): `pytest` apenas nos arquivos/módulos impactados;
  `ruff check` e `ruff format --check` nos caminhos alterados; `mypy` se tipos
  mudaram; frontend: `npm run lint` e `npm test -- <arquivo>` só se `src/`
  mudou.
- **Regressão ampla** (somente sob pedido): `pytest` completo em `api/`,
  `npm test`, `npm run build`.

Ambiente Python: o venv fica **fora do repo**, em
`%LOCALAPPDATA%\FreelanceFocus\venvs\api\Scripts\python.exe`. Resolva o
caminho do usuário atual pelo ambiente; não use caminhos de outro usuário
vistos em documentos. Rode da pasta `api/`. Use `-p no:cacheprovider` no
pytest para não criar cache; nunca crie venv, `.db` ou `data_dir` no repo.

## 4. Executar (apenas com autorização)

Execute os comandos aprovados um a um. Para cada um registre: comando exato,
diretório, **exit code**, resumo (passados/falhos/ignorados), e a lista de
testes `skipped`/`xfail`/`deselected` com o motivo. Em falha, não tente
corrigir: reporte e sugira usar o subagente `test-investigator`.

## 5. Checklist de higiene (somente leitura)

Sem alterações em `docs/`, `.env*`, migrations ou testes fora do escopo
declarado; sem venv/`.db` dentro do repo; `AGENT_LOG.md` com entrada
pendente (apenas informe, não escreva).

## 6. Relatório para auditoria independente

Entregue: branch e HEAD; arquivos alterados; módulos impactados; tabela de
comandos (comando | exit code | resultado | ignorados); o que **não** foi
executado e por quê; riscos residuais; itens para o Codex atacar. Termine com
"Veredito: não emitido — aguarda auditoria independente (Codex)".
