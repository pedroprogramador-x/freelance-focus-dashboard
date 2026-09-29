# E6 — Reverificação CONS5, rodada 1 (relatório original, verbatim)

> Persistido verbatim conforme instrução do Pedro (2026-09-28). Não editado, não
> resumido, não reformatado além da conversão para Markdown.

**BLOCKED.** CONS5-002 e CONS5-003 passaram na reverificação, mas CONS5-001 ainda produz diagnósticos Git incorretos em dois casos reproduzidos.

**E6-CONS5V-001 — Média/P2 — ausência de evidência tratada como prova.** Em um repositório temporário, fiz um commit válido com reflogs desativados e depois quebrei a referência de `HEAD`. [`probe_head` (line 459)](../../api/app/git_runtime/__init__.py:459) retornou `no_head`. Na API real, a elegibilidade mostrou `repository_without_head` e `POST /plan` devolveu 409 com o mesmo motivo, afirmando que nunca houve commit. O SHA do commit anterior havia sido confirmado. A ausência de `logs/HEAD` não prova que o repositório seja recém-inicializado.

Reproduzi também um workspace em subdiretório de um repositório com commit: após corromper o `.git/config` do diretório pai, [`probe_head` (line 438)](../../api/app/git_runtime/__init__.py:438) retornou `not_a_repo`; a API mostrou `workspace_not_git_repo` tanto na elegibilidade quanto no 409 de `POST /plan`. A busca local por `.git` não considera o ancestral, embora o Git reconhecesse esse workspace antes da corrupção. **Impacto:** a interface orienta criar o primeiro commit ou inicializar Git quando o problema real é uma leitura impossível. **Critério de resolução:** classificar ambos os estados ambíguos como `git_unverifiable`, mantendo `no_head` e `not_a_repo` somente quando houver confirmação suficiente. Não observei exposição de caminho ou stderr nas respostas examinadas.

Os demais cenários Git pedidos tiveram o resultado esperado: diretório comum `not_a_repo`; repositório recém-inicializado `no_head`; configuração ou conteúdo de `HEAD` corrompido na raiz e referência quebrada com reflog `unverifiable`; Git indisponível ou falha de leitura `unverifiable`; worktree válido com `.git` em arquivo `ok`.

Na interface, uma simulação independente de `409 concurrent_task_update` produziu **exatamente dois GETs** no total, preservou o alerta e atualizou botão e bloqueio, sem loop. Erros de rede, 401 e 500 permaneceram visíveis e não provocaram releitura. Para CONS5-003, somente o caso `awaiting_approval` com divergência de HEAD exibiu a explicação específica; `needs_fix` com HEAD intacto, `draft` histórico e plano preservado após rejeição receberam aviso neutro e mantiveram as ações adequadas.

| Gate | Resultado |
| --- | --- |
| 13 testes CONS5 | 13 passed; exit 0 |
| Frontend focado | 44 passed; exit 0 |
| Backend completo | 1884 passed, 6 skipped; `PYTEST_EXIT=0` |
| Frontend completo | 161 passed; exit 0 |
| Ruff check / format / mypy | 0 / 0 / 0 |
| Lint / build frontend | 0 / 0 |
| `git diff --check` | 0 |

A árvore permaneceu com **40 arquivos rastreados modificados e 47 não rastreados**; o diff acumulado é de 5970 inserções e 197 remoções. `cons5_before/` e `cons5_isolado.diff` não estão disponíveis, portanto não verifiquei historicamente o diff isolado. Nenhum arquivo foi alterado, commitado ou enviado.
