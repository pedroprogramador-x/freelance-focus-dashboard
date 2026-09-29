# E6 — Reverificação CONS5V, rodada 2 (relatório original, verbatim)

> Persistido verbatim conforme instrução do Pedro (2026-09-28). Não editado, não
> resumido, não reformatado além da conversão para Markdown.

**BLOCKED.** Os dois cenários originais de CONS5V-001 foram corrigidos: reproduzi ambos em repositórios temporários e a API real retornou `git_unverifiable` na elegibilidade e no `POST /plan`, sem expor caminho ou stderr. A inspeção dos controles, porém, revelou duas falhas concretas na mesma classificação.

1. **E6-CONS5V2-001 — Média/P2 — worktree vinculado com histórico recebe `no_head`.** Criei um commit válido com reflogs desativados, abri um worktree com `.git` em arquivo e quebrei o HEAD desse worktree. [`probe_head` (line 460)](../../api/app/git_runtime/__init__.py:460) retornou `no_head`; a API exibiu `repository_without_head` e repetiu esse motivo no 409 de `POST /plan`. O worktree tinha o commit comprovado, mas [`_has_object_evidence` (line 454)](../../api/app/git_runtime/__init__.py:454) procura objetos apenas no Git dir próprio, sem considerar o repositório comum indicado por `commondir`. **Impacto:** orientação falsa para fazer um primeiro commit. **Critério:** consultar também o armazenamento comum ou, se o histórico não puder ser verificado, retornar `git_unverifiable`; cobrir sondagem e API.
2. **E6-CONS5V2-002 — Baixa/P3 — `git add` antes do primeiro commit perde o diagnóstico correto.** Em um `git init` genuinamente sem commit, `probe_head` retornou `no_head`. Bastou executar `git add` para passar a `unverifiable`; a API mostrou `git_unverifiable` e recusou o plano com esse motivo. Após o primeiro commit, voltou a `ok`. O objeto criado pelo staging é tratado como evidência de histórico de commits. **Impacto:** no fluxo inicial, a orientação passa de "faça o primeiro commit" para "não foi possível consultar o Git", embora o Git esteja legível. O bloqueio do planejamento continua seguro. **Critério:** distinguir objetos staged de evidência de commit, preservando `unverifiable` para histórico realmente ambíguo.

Os demais controles passaram: diretório comum `not_a_repo`; `git init` vazio `no_head`; HEAD válido `ok`; corrupção de configuração ou HEAD na raiz `unverifiable`; worktree válido `ok`; configuração principal corrompida em worktree `unverifiable`. A busca ancestral respeita a fronteira de montagem no código. No caminho normal, contei apenas duas chamadas Git, ou três quando o branch é solicitado; a busca adicional em disco não ocorreu. A sondagem usa comandos Git de leitura, e os testes de arquitetura confirmam `subprocess` restrito a `git_runtime`. Elegibilidade e `POST /plan` continuam usando a mesma classificação, inclusive quando ela está errada.

| Gate | Resultado |
| --- | --- |
| CONS5V e testes Git relacionados | 102 passed; exit 0 |
| Backend completo | 1890 passed, 6 skipped; `PYTEST_EXIT=0` |
| Ruff check / format / mypy | 0 / 0 / 0 |
| `git diff --check` | 0 |

A árvore terminou como começou: **40 arquivos rastreados modificados e 49 não rastreados**. O diff desde HEAD é acumulado (+6187/−197), não o diff isolado desta correção. CONS5-002 e CONS5-003 permanecem verificados; a E6 ainda **não** está pronta para revisão de commit. Nenhum arquivo foi alterado, commitado ou enviado por esta auditoria.
