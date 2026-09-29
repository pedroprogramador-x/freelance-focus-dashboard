# E6 — Reverificação CONS5V, rodada 4v (relatório original, verbatim — GREEN)

> Persistido verbatim conforme instrução do Pedro (2026-09-28). Não editado, não
> resumido, não reformatado além da conversão para Markdown. Não sobrescreve o relatório
> BLOCKED anterior, `docs/audits/e6-cons5v-round-4.md`.

GREEN. Reproduzi independentemente CONS5V4-001/002 em repositórios temporários. Falhas de leitura em `objects/`, `objects/pack` e `commondir` agora fazem `probe_head` retornar `unverifiable`. Nos três casos, o detalhe da tarefa apresentou `git_unverifiable` e `POST /plan` respondeu 409 com o mesmo motivo, sem HTTP 500, caminhos locais ou detalhes internos.

A distinção interna também se confirmou: ausência comprovada pode contribuir para `no_head`; `PermissionError` interrompe essa conclusão. Recriei em memória o comportamento anterior, sem editar o código: seis dos sete testes CONS5V4 falharam pelos diagnósticos antigos, enquanto o teste adicional de `logs/HEAD` passou. Na implementação atual, os sete passam. Os controles Git relacionados passaram, incluindo worktree vinculado, objetos compactados e o limite de 512 objetos. O caminho normal manteve duas chamadas Git, ou três com branch, sem varredura adicional.

Gates: 7 testes CONS5V4, 248 relacionados e 1907 backend passaram, com 6 skipped; `PYTEST_EXIT=0`. Ruff check, Ruff format, mypy e `git diff --check` retornaram `0`. A árvore segue em `main`, commit `8c493a8`, com 40 arquivos rastreados modificados e 55 não rastreados. O diff desde HEAD acumula toda a E6 e não foi atribuído apenas a esta correção. Nenhum arquivo do projeto foi alterado, commitado ou enviado por esta auditoria.

E6-CONS5V4-001 VERIFIED RESOLVED
E6-CONS5V4-002 VERIFIED RESOLVED
E6-CONS5V3-001 VERIFIED RESOLVED
E6-CONS5V2-001/002 VERIFIED RESOLVED

E6 READY FOR FINAL INVENTORY AND COMMIT REVIEW. Este veredito não autoriza commit ou merge.
