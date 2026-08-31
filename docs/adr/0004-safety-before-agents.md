# ADR-0004 — Segurança antes de agentes; isolamento por Git worktree

- **Status:** Aceito
- **Data:** 2026-08-31 · **Revisado nas Fases 1B.1 e 1B.3**
- **Fase:** 1B, revisto em 1B.1 (AUD-005, AUD-009, AUD-010) e 1B.3
  (REAUD-001, REAUD-004)
- **Complementado por:** [ADR-0009](0009-provider-capability-enforcement.md)

## Contexto

Um agente que escreve arquivos numa máquina de desenvolvimento é a capacidade mais
perigosa deste projeto: escrita fora do workspace, leitura de segredos, comandos
destrutivos, processos órfãos após timeout, perda de trabalho não commitado.

O repositório vive em `C:\Users\pedro\OneDrive\Desktop\freelance-focus-dashboard` — pasta
sincronizada pelo OneDrive, filesystem case-insensitive, limite de 260 caracteres de
caminho, com junctions e reparse points como vetores de escape.

## Decisão

1. **Ordem obrigatória:**
   `Orchestrator Planner → Full Safety Runtime + Git isolation + Tool mediation → Developer`.
   Nenhum agente ganha capacidade de escrita antes de a etapa de segurança estar completa e
   testada. A inversão é proibida.
2. **(1B.1) A segurança é implementada em duas etapas, sem duplicar regras.** O módulo
   `safety/` é um só; o que se divide é a ordem:
   - **Safety Kernel + Path Runtime (E2)** — `safety/` puro (pré-validação sintática,
     `decide_path`, `decide_post_open`), `path_runtime.py` (canonicalização, inspeção de
     reparse point, handles, identidade, verificação pós-abertura), contenção em root,
     política básica de segredos e redator, validação de `source_refs`, carregamento e
     composição de política. Precisa existir cedo porque E3 e E4 já validam `local_path` e
     globs.
   - **Full Safety Runtime (E7)** — política de comandos, enforcement de escrita,
     `ToolExecutor`, supervisão de processo, timeout, cancelamento, kill de árvore,
     worktrees e **enforcement de capability**.
3. **Toda execução ocorre em Git worktree isolada**, fora da árvore principal, criada a
   partir de um `base_commit` **congelado no planejamento**.
4. **Worktrees ficam fora do OneDrive** — `data_dir` em `%LOCALAPPDATA%`, nomes curtos
   (`ff-task-<id8>`). **(1B.1) Mitigação parcial** — ver abaixo.
5. **Commit, merge e push permanecem humanos.** `commit`, `merge`, `push`, `rebase` e
   `reset --hard` estão na denylist. O sistema entrega worktree e diff.
6. **O Safety Gate é determinístico e puro.** `safety/` não faz IO, não abre banco, não roda
   processo, não conhece a UI. **(1B.3)** Como validar path exige IO real, o IO sai para um
   módulo próprio, **`path_runtime.py`**, que **coleta `PathFacts`** e não decide nada;
   `safety/` recebe os fatos e decide. A dependência é apenas `path_runtime → safety`,
   nunca o contrário.
7. **Nenhum LLM participa de uma decisão de segurança.** O gate é chamado pelo
   `ToolExecutor` e pelo Execution Manager, nunca pelo agente. Texto dentro de um
   repositório é **dado, nunca instrução**.
8. **(1B.1) A aprovação é vinculada ao `execution_fingerprint` completo** — plano,
   manifest, payload renderizado, base commit, adaptador, modelos, agentes,
   `tool_profile_hash`, `safety_policy_hash` e limites. Mudou qualquer campo coberto, a
   aprovação é inválida.
9. **Verificação pós-execução:** `git status` da árvore principal antes e depois de cada run
   que escreve. Diferença → `SafetyEvent(out_of_worktree_write)` e parada.
10. **Override de política por workspace só pode restringir, nunca afrouxar.**
11. **(1B.1) A validação de path tem três fases** — pré-validação, abertura, pós-abertura —
    com revalidação a partir do handle aberto. Canonicalização com verificação de prefixo é
    necessária, **não suficiente**.
12. **(1B.3) Escrita ou truncamento nunca ocorrem antes da validação pós-abertura**, onde
    tecnicamente possível. Abrir em modo truncante antes da decisão destruiria o arquivo
    mesmo quando a decisão fosse negar.
13. **(1B.3) O Developer não executa comandos** — `execute_commands = disabled`, sem
    exceção ([ADR-0009](0009-provider-capability-enforcement.md)). A superfície de execução
    que resta é o **Test Runner**, infraestrutura do sistema sob `TestPolicy`, jamais
    invocável pelo agente.

## Consequências

**Positivas**

- A capacidade perigosa só existe depois de a contenção existir.
- Trabalho do usuário nunca é commitado, mesclado ou enviado sem decisão humana.
- A política é exaustivamente testável porque é pura e determinística.
- **(1B.1)** Módulos anteriores a E7 deixam de validar path por conta própria.

**Negativas e mitigações**

- Uma etapa inteira antes de qualquer código gerado. É exatamente o ponto.
- Worktrees consomem disco. GC por idade, só em tarefas terminais, via
  `git worktree remove`.
- Alterações não commitadas ficam invisíveis para o agente. Mitigado por registro da
  divergência no manifest e aviso explícito antes da aprovação.

## Limitações residuais — aceitas e declaradas

> **(1B.1) Estas três substituem a formulação anterior, que era otimista demais.**

### 1. Não existe fronteira de execução confiável na V1

`cwd` na worktree, `argv` controlado e ambiente por allowlist **não são um sandbox de
sistema operacional**.

**(1B.3)** A superfície encolheu: com `execute_commands = disabled`, o **Developer** deixou
de ser um vetor — ele não inicia processo nenhum. **Resta o Test Runner**: `npm test` e
`pytest` executam código do projeto, que pode fazer tudo o que o usuário pode — ler
qualquer arquivo, abrir rede, escrever fora da worktree.

> A mediação de ferramentas protege a autoridade do **provider**. Ela **não** transforma o
> código do projeto executado pelo Test Runner em código confinado.

A verificação pós-execução **detecta** escrita na árvore principal; não previne, e não vê
escrita em outros lugares do disco. Isolamento real (contêiner, *job object*, AppContainer,
usuário dedicado) é objeto de estudo em E14.

### 2. TOCTOU residual na resolução de path

Entre a pré-validação e a abertura, um componente do caminho pode ser trocado. A validação
pós-abertura **estreita** a janela; não a fecha. Fechá-la exigiria primitivas de resolução
atômica (`openat2(RESOLVE_BENEATH)` no Linux; sem equivalente portável no Windows) que a
biblioteca padrão do Python não expõe de forma uniforme.

### 3. OneDrive — mitigação parcial, não eliminação

Colocar worktrees fora do OneDrive **reduz** o risco. Não o elimina: os metadados da
worktree vinculada (`.git/worktrees/<nome>/`), refs, objetos e `.git/index` continuam no
`.git` do repositório principal, dentro do OneDrive, e sofrem lock e sincronização
concorrente.

Recuperação documentada: `git worktree list` → `prune` → `repair` → em último caso, remoção
do diretório de metadados obsoleto. Alternativas futuras, em ordem de simplicidade:
**(1)** mover o repositório para fora do OneDrive; **(2)** clone/cache operacional fora do
OneDrive; **(3)** repositório operacional *bare*/mirror.

Nenhuma das três é implementada na V1.

## Alternativas consideradas

| Alternativa | Recusada porque |
| --- | --- |
| Developer primeiro, segurança depois | Produz um incidente irreversível antes de existir contenção |
| Toda a segurança só em E7 (versão 1B) | Deixava E3 e E4 validando `local_path` e globs sem política |
| Executar direto na árvore principal com backup | Backup não protege trabalho não commitado nem processos concorrentes |
| Commit automático em branch próprio | Autoria e histórico do usuário viram efeito colateral |
| Fundir `safety/` com `git_runtime/` | Contaminaria a política com `subprocess` e destruiria a testabilidade |
| Declarar o conjunto como "sandbox" | Criaria confiança que a implementação não sustenta |

## Revisões

| Fase | Mudança |
| --- | --- |
| 1B | Versão original |
| **1B.1** | Divisão Safety Kernel / Full Safety Runtime (AUD-009); validação de path em três fases e TOCTOU residual (AUD-005); OneDrive como mitigação **parcial** (AUD-010); risco do Test Runner explicitado (§3); aprovação vinculada ao `execution_fingerprint` (AUD-003); enforcement de capability delegado a [ADR-0009](0009-provider-capability-enforcement.md) (AUD-001) |
| **1B.3** | `safety/` volta a ser estritamente puro e o IO migra para `path_runtime.py`, que produz `PathFacts` (**REAUD-004**); ordem obrigatória de truncamento após validação pós-abertura; Developer sem execução de comandos, restando o Test Runner como única superfície de execução (**REAUD-001**) |

## Referências

[04 — Segurança e Git runtime](../architecture/04-safety-and-git-runtime.md) ·
[07 — Roadmap](../architecture/07-roadmap-v1.md) ·
[ADR-0009](0009-provider-capability-enforcement.md)
