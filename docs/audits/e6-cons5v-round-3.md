# E6 — Reverificação CONS5V, rodada 3 (relatório original, verbatim)

> Persistido verbatim conforme instrução do Pedro (2026-09-28). Não editado, não
> resumido, não reformatado além da conversão para Markdown.

BLOCKED. Reproduzi independentemente a resolução de E6-CONS5V2-001 e 002: o worktree vinculado com commit anterior, sem reflog e com HEAD quebrado retorna unverifiable; git init → git add permanece no_head e passa a ok após o primeiro commit. A API real apresentou, respectivamente, git_unverifiable e repository_without_head tanto na elegibilidade quanto em POST /plan, sem expor caminho ou stderr. A nova leitura de objetos, porém, tem uma falha concreta de E/S.

E6-CONS5V3-001 — Média/P2 — falha de leitura dos objetos não é tratada de modo conservador. Em um repositório temporário com arquivo staged, injetei PermissionError na leitura de .git/objects: probe_head lançou a exceção; GET /api/tasks/{id} e POST /plan responderam 500, em vez de informar git_unverifiable. Em outra reprodução, havia um commit válido com reflogs desativados e HEAD quebrado; ao tornar ilegível somente o diretório que continha o objeto desse commit, a sondagem retornou no_head. A API repetiu o diagnóstico falso repository_without_head. A causa está nas chamadas a os.scandir sem tratamento e no except OSError: continue (line 542), que transforma uma leitura incompleta em ausência de histórico. Critério de resolução: qualquer falha ao examinar o armazenamento de objetos deve produzir unverifiable, sem exceção nem conclusão no_head; cobrir a sondagem e as duas respostas da API com falhas de leitura injetadas. Não houve vazamento de caminho no 500 observado.

Os controles pedidos passaram. Diretório sem Git, Git recém-inicializado, HEAD válido, corrupção na raiz e em ancestral, worktree válido, commit compactado e objeto tag tiveram classificações compatíveis. blob e tree isolados mantiveram no_head; com 512 blobs também, e com 513 a resposta foi conservadoramente unverifiable. Medi cerca de 1,2 s por sondagem no limite de 512 objetos, contra 0,1 s no repositório vazio. No caminho normal, contei apenas duas leituras Git, ou três com o branch; nenhuma busca adicional em disco ocorreu. Os testes de arquitetura confirmaram os limites de subprocess e Git somente de leitura.

GATES:
Novos testes CONS5V2: 4 passed; exit 0.
CONS5V2, CONS5V, CONS5, CONS4, Git e arquitetura: 208 passed; exit 0.
Backend completo: 1894 passed, 6 skipped; PYTEST_EXIT=0.
Ruff check / format / mypy: 0 / 0 / 0.
git diff --check: 0.

A árvore permaneceu com 40 arquivos rastreados modificados e 51 não rastreados. O diff desde HEAD (+6461/-197) acumula toda a E6 e não foi atribuído a esta subcorreção. CONS5-002 e CONS5-003 permanecem verificados, mas a E6 ainda não está pronta para a revisão final de inventário e commit. Nenhum arquivo foi alterado, commitado ou enviado por esta auditoria.
