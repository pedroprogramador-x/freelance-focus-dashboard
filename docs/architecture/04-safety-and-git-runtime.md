# 04 — Segurança e Git runtime

> Escopo: capabilities de provider, execução mediada, política pura, `PathRuntime`,
> política de testes e protocolo de worktree.
> Decisões relacionadas: [ADR-0004](../adr/0004-safety-before-agents.md),
> [ADR-0009](../adr/0009-provider-capability-enforcement.md).
>
> **Revisado na Fase 1B.3** (REAUD-001 P0, REAUD-004, REAUD-006).
>
> **Esclarecido após a E6 Rodada 7** — §5 apenas: precisão da âncora (E6-AUD7-003), risco
> residual de *over-redaction* aceito (E6-AUD7-004), as duas classes de escape da fronteira
> JSON, e as duas garantias que a implementação ainda deve (E6-AUD7-001, E6-AUD7-002).
> Nenhuma decisão anterior foi reescrita: a afirmação superada está marcada no lugar.
>
> **Este é o documento mais crítico da V1.**

---

## 0. O que a V1 garante e o que não garante

Declaração honesta, antes de tudo, porque condiciona o resto:

> **Não existe, na V1, uma fronteira de execução confiável e completa.**
> `trusted execution boundary = ausente`.
>
> A mediação de ferramentas protege a **autoridade do provider**. Ela **não** transforma o
> código do projeto — executado pelo Test Runner — em código confinado. Sem isolamento de
> sistema operacional, `npm test` e `pytest` continuam podendo fazer tudo o que o usuário
> pode fazer.

Nada neste documento deve ser lido como *sandbox*. Onde a palavra aparece, é para negá-la.

---

## 1. Capabilities de provider *(REAUD-001, P0)*

### O problema que restava

A Fase 1B.1 introduziu a mediação, mas admitia `execute_commands = mediated` para o
Developer. A reauditoria mostrou que isso é um **trampolim**: mediar
`python -c "..."`, `node -e "..."`, `npm run <script>` ou qualquer script do projeto
valida o **argv**, e argv nenhum descreve o que o processo fará depois. A autoridade
escapa por transitividade e a mediação vira teatro.

### Decisão oficial da V1

> **`DeveloperProvider.execute_commands = disabled`. Sem exceção.**

O Developer **não possui**: shell, terminal, exec arbitrário, `python -c`, `node -e`,
`npm`, `pytest`, scripts do projeto ou `subprocess` de qualquer natureza. Ele trabalha
exclusivamente por operações mediadas e tipadas.

### Perfis oficiais

| Capability | **Developer** | **Auditor** |
| --- | --- | --- |
| `read_files` | `mediated` | `disabled` |
| `write_files` | `mediated` | `disabled` |
| `execute_commands` | **`disabled`** | `disabled` |
| `git_read` | `fixed_operations_only` | `disabled` |
| `git_write` | `disabled` | `disabled` |
| `network` | `disabled` | `disabled` |
| `external_paths` | `disabled` | `disabled` |

O **Auditor é uma função pura sobre o payload**: recebe diff, critérios de aceite e resumo
de testes no próprio request, e não precisa de nenhum acesso ao filesystem. Um auditor sem
capability alguma é mais fácil de confiar e mais barato de operar.

### Modos de enforcement

| Modo | Significado |
| --- | --- |
| `disabled` | desligada no provider; ele não tem a ferramenta |
| `mediated` | só alcançável através do `ToolExecutor`, com payload tipado |
| **`fixed_operations_only`** | *(novo em 1B.3)* apenas operações nomeadas, com **argv formado pelo runtime**; o provider nomeia a operação e passa parâmetros tipados, nunca uma linha de comando |
| `unmediated` | provider age sozinho, sem verificação — **nunca aceito na V1** |

#### Adendo autorizado — E8.2 (2026-10-05): alcance de `execute_commands = disabled`

> Adendo aprovado por Pedro (decisão sobre o finding E8.2-AUD-003). Esclarece o alcance da
> decisão acima; não a reescreve nem a relaxa.

`execute_commands = disabled` significa que o Developer/modelo **não recebe autoridade**
sobre comandos ou processos cuja seleção, argv, conteúdo, dados ou disparo ele possa
controlar ou influenciar. Subprocessos **fixos** de infraestrutura/runtime, sem influência
do agente, não constituem capability do Developer: permanecem riscos do host, sujeitos a
hardening.

Este adendo **não** autoriza subprocesso de forma genérica: qualquer processo cujo argv,
dados ou disparo dependa do que o modelo produz continua proibido ao Developer.

**Residual registrado — AUD-003 (Windows host/runtime):** no Windows com CPython 3.11,
`platform.uname()` executa a sonda fixa `cmd /c ver` (alcançável por dependências do
transporte HTTP do provider). Sem influência do agente; classificado como
**RESIDUAL ACCEPTED — HOST/RUNTIME**, com hardening em **E14**.

### O que cada adaptador declara — e prova

| Campo | Significado |
| --- | --- |
| `supported_capabilities` | o que o provider **tecnicamente consegue** fazer |
| `effective_capabilities` | o que ficará **realmente permitido** nesta execução |
| `enforcement_method` | `cli_flag` · `config_file` · `tool_allowlist` · `api_tool_schema` · `process_env` · `not_enforceable` |
| `enforcement_evidence` | flags e configuração concretamente aplicadas; sonda de preflight onde viável |

**Declaração não é prova.** Onde o adaptador só consegue declarar, a capability não é
concedida.

### Fail closed

```text
requisitado:  execute_commands = disabled
adaptador:    não consegue desligar a ferramenta de shell
→ execução RECUSADA antes de subir qualquer processo
→ SafetyEvent(capability_unenforceable)
→ task → failed(failure_reason = capability_unenforceable)
```

Nenhuma suposição de que Claude CLI, Codex CLI ou APIs tenham os mesmos controles. Cada
adaptador prova a própria capacidade, e o contrato funciona igual para CLI hoje e API
amanhã.

`tool_profile_hash` entra no `execution_fingerprint` ([02](02-data-model.md) §7): mudar o
perfil invalida a aprovação.

### Reabertura futura

Permitir shell, `python`, `node`, `npm` ou comandos arbitrários ao Developer **exige
reabrir a decisão de segurança**. Gatilho: isolamento de processo/SO comprovadamente
forte — contêiner, VM, *job object*/isolamento do Windows mais forte, ou outro mecanismo
demonstrado. **Fora da V1.**

---

## 2. `ToolExecutor` — o conjunto fechado de operações

```mermaid
flowchart LR
  P["Provider<br/>(raciocínio)"] -->|ToolRequest tipado| TE["ToolExecutor<br/>(escopo do run)"]
  TE --> PR["PathRuntime<br/>(fatos)"]
  PR --> SP["SafetyPolicy<br/>(decisão pura)"]
  SP -->|allow / deny| TE
  TE -->|operação permitida| FX[["Filesystem · Git (read)"]]
  TE -->|ToolResult| P
  TE -->|SafetyDecision| EM["Execution Manager<br/>(persiste SafetyEvent)"]
```

### Operações disponíveis ao Developer — lista fechada

| Operação | Capability | Notas |
| --- | --- | --- |
| `ReadFile` | `read_files` | caminho relativo ao workspace |
| `ListDirectory` | `read_files` | |
| `SearchText` | `read_files` | busca implementada pelo runtime, não por `grep` externo |
| `WriteFile` | `write_files` | conteúdo integral |
| `ApplyPatch` | `write_files` | diff unificado aplicado pelo runtime |
| `GitStatus` | `git_read` | argv formado pelo runtime |
| `GitDiff` | `git_read` | idem |
| `GitShow` | `git_read` | idem |
| `GitListTree` | `git_read` | idem |

**Não existe `ExecCommand` genérico exposto ao Developer na V1.** Leitura de git é
representada por **operações específicas**, não por shell genérico: o provider nomeia
`GitDiff` com parâmetros tipados, e é o runtime que monta o `argv`. O provider nunca
fornece uma linha de comando crua.

### Regras

- Toda negação produz `SafetyDecision` → `SafetyEvent`, e volta ao provider como negação
  explícita. Não há caminho alternativo.
- O executor é **criado por run** por uma `ToolExecutorFactory` (§3), vinculado a um
  `ExecutionWorkspaceRef`, a uma `SafetyPolicy` composta e a um perfil de capability
  efetivo.
- A medição do `ToolExecutor` é a fonte autoritativa de `files_read`
  ([02](02-data-model.md) §10). Como **toda** leitura do Developer é mediada,
  `files_read_source = reported` passa a ser a regra, não a exceção.

---

## 3. Ciclo de vida — `ToolExecutorFactory` *(REAUD-006)*

| Escopo | Objeto |
| --- | --- |
| **Vida da aplicação** | `ToolExecutorFactory`, construída no composition root |
| **Vida do run** | `ToolExecutor`, criado pela factory para aquele run |

O composition root **não cria um `ToolExecutor` global** — não haveria como: o executor
depende de `ExecutionWorkspaceRef`, política composta, perfil de capability efetivo e
metadados de task/run, que só existem em tempo de execução.

```text
startup            → injeta ToolExecutorFactory no Execution Manager
início de um run   → tool_executor = factory.create(workspace_ref, policy,
                                                    capability_profile, task/run metadata)
                   → provider recebe as ferramentas mediadas com escopo daquele run
fim do run         → executor descartado; medições consolidadas no Run
```

Um executor nunca atravessa runs. Isso torna impossível que um run herde autoridade,
`ExecutionWorkspaceRef` ou contagem de outro.

---

## 4. Política pura e `PathRuntime` *(REAUD-004)*

### A contradição corrigida

`safety/` era declarado puro, mas a validação de path exige IO real: canonicalizar, seguir
symlinks, inspecionar reparse points, abrir handles e comparar identidade de objeto. Os
dois papéis foram separados.

| Componente | Natureza | Faz |
| --- | --- | --- |
| **`safety/`** | **pura** — zero IO | recebe fatos, decide |
| **`path_runtime.py`** | infraestrutura | faz o IO e **produz os fatos** |

### `PathFacts`

Estrutura pura (definida em `safety/`, preenchida por `path_runtime`):

```text
PathFacts {
  requested_path
  canonical_root
  canonical_target
  exists
  parent_identity
  target_identity?          # device+inode | volume serial + file index
  volume
  is_symlink
  is_junction
  is_reparse_point
  is_unc
  is_device_namespace
  is_drive_relative
  contained                 # canonical_target está sob canonical_root
  post_open_target?         # preenchido apenas na fase pós-abertura
}
```

### Fluxo

```text
1. safety.prevalidate_path_syntax(requested)     # puro: string; rejeita ".." , "~",
                                                 # UNC, device namespace, drive-relative,
                                                 # ADS, componentes com ponto/espaço final,
                                                 # nomes reservados, aliases 8.3
2. path_runtime.inspect(requested, root)  → PathFacts        # IO
3. safety.decide_path(facts)              → SafetyDecision   # puro
4. abrir o handle                                            # SEM truncar
5. path_runtime.inspect_opened(handle)    → PathFacts        # IO, com post_open_target
6. safety.decide_post_open(facts)         → SafetyDecision   # puro
7. executar a operação                                       # só aqui trunca/escreve
```

**Regra de ordem, obrigatória:** escrita ou truncamento **nunca** ocorrem antes da
validação pós-abertura, onde tecnicamente possível. Abrir com modo truncante antes do
passo 6 destruiria o arquivo mesmo quando a decisão fosse negar.

`path_runtime` não importa `db`, `orchestrator`, `agent_runtime`, `tool_executor` nem
`api`; e `safety/` **não importa `path_runtime`** — a dependência é só na direção
`path_runtime → safety` (para os tipos).

### Windows — o que a inspeção precisa cobrir

UNC (`\\servidor\share`, `\\?\UNC\…`), device namespace (`\\?\`, `\\.\`), nomes reservados
(`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`), drive-relative (`C:arquivo`),
root-relative (`\arquivo`), alternate data streams (`arquivo.txt:fluxo`), componentes com
ponto ou espaço final, aliases 8.3 (`PROGRA~1`), normalização de drive e caixa, junctions
e reparse points em **todos** os ancestrais entre `root` e o alvo.

Nesta máquina o repositório está em
`C:\Users\pedro\OneDrive\Desktop\freelance-focus-dashboard`: worktrees ficam em
`%LOCALAPPDATA%` com nomes curtos (`ff-task-<id8>`) por causa do limite de 260 caracteres —
**mitigação parcial**, ver §7.

### Risco residual declarado

> Entre a inspeção (passo 2) e a abertura (passo 4), um componente do caminho pode ser
> trocado. A validação pós-abertura **estreita** a janela; não a fecha. Fechá-la exigiria
> primitivas de resolução atômica — `openat2(RESOLVE_BENEATH)` no Linux, sem equivalente
> portável no Windows — que a biblioteca padrão do Python não expõe de forma uniforme.
> **A V1 aceita este risco TOCTOU residual e o declara.**

---

## 5. As três peças de política

| Peça | Natureza | Onde vive |
| --- | --- | --- |
| `SafetyPolicy` | configuração imutável: allowlists, denylists, limites, globs de segredo, perfis de capability, `TestPolicy` | `safety/` |
| `SafetyDecision` | resultado puro: `{allow, rule_id, reason, subject_redacted}` | `safety/` |
| `SafetyEvent` | registro persistido de decisão relevante | tabela, escrita pelo Execution Manager |

**Composição:** política global mais override opcional por workspace. **Um override só
restringe, nunca afrouxa** — a composição é intersecção de permissões.
`safety_policy_hash` cobre a política composta efetiva e entra no fingerprint.

### A regra inegociável

```text
Nenhum LLM participa de uma SafetyDecision.
```

O gate é chamado pelo `ToolExecutor` e pelo Execution Manager — nunca pelo agente. Texto
dentro de um repositório (README, comentário, issue, configuração) é **dado, nunca
instrução**: nada ali eleva permissão.

### Proteção de segredos — três camadas

| Camada | Onde age | O que faz |
| --- | --- | --- |
| **1 — Seleção** | `context_engine` | Glob que casaria com arquivo secreto é rejeitado na escrita da entrada; no manifest, secretos entram em `excluded` com `reason = secret_policy` |
| **2 — Acesso** | `safety` + `path_runtime` + `ToolExecutor` | Nega leitura e escrita de path da denylist, para qualquer operação mediada |
| **3 — Saída** | `safety.redact_document` | Toda string que sai do backend — resposta JSON, log, `summary`, `error_summary`, diff — passa pelo redator |

**Princípio fail-closed da Camada 3 (formalizado após E6-AUD3-001).** "O texto que sai
passa pelo redator" não é orientação de estilo — é garantia estrutural:

> Toda string (valor **ou** chave) que sai como JSON por `/api/*` passa pelo sanitizador
> central por padrão — sucesso, erro, erro de domínio, e `RequestValidationError`
> (inclusive `detail[].input`) — independente de rota, schema, profundidade ou origem do
> campo. Não existe exceção distribuída por schema (nenhum
> `Field(json_schema_extra={"redact": False})` ou equivalente). As exceções são *escape
> hatches* estreitos, explícitos e testados — hoje **exatamente dois**, enumerados abaixo —,
> nunca uma flag genérica reaproveitável por outro campo. O *boundary* de sanitização
> acontece **antes** da serialização para bytes, nunca por *reparsing* de JSON já
> serializado. HTML de bootstrap, assets estáticos e o futuro SSE (E11) têm contrato
> próprio, fora deste *boundary*.

> **As duas classes de escape deliberado** *(corrigido após E6 Rodada 7; a redação
> anterior deste documento dizia "a única exceção … (`purge_token`)", o que já não
> descrevia a implementação desde E6-AUD4-004)*. São duas, e só duas:
>
> | Escape | Granularidade | Onde é construído | O que autoriza |
> | --- | --- | --- | --- |
> | `Unredacted` no `purge_token` | **um valor** | `workspace/purge_tokens.py`, um ponto | o segredo de autorização que a prévia de purga existe para entregar ([02] §11) |
> | `EditViewJSONResponse` | **a resposta inteira** | `api/context.py`, uma rota | o conteúdo cru de uma entrada do Context Registry, para que quem edita não salve `«redigido»` por cima do que ele escondia (E6-AUD4-004) |
>
> Os dois são a mesma decisão em granularidades diferentes, e ambos continuam obrigados a
> ser **tipados**, **localizados** (um ponto de construção cada), **testados** e **não
> reaproveitáveis como bypass genérico**. `test_architecture.py` enumera o conjunto e
> falha na definição de qualquer terceira classe de resposta — não no uso dela, na
> definição. Uma terceira exceção exige decisão e documentação explícitas, aqui, antes de
> existir em código.

A garantia é **por padrão**, não por rota corrigida: cada superfície nova herda a proteção
sem que quem a escreve precise lembrar de invocá-la. `redact_document` é a única
caminhada recursiva que a implementa — nenhum segundo `_redact_tree` ad hoc por módulo.

> **Cobertura de chaves e valores (E6-AUD3-003).** `redact_document` percorre
> recursivamente **valores e chaves** de JSON. Chaves que contêm segredo reconhecido pelo
> detector são substituídas por placeholders opacos numerados. Antes de alocá-los, a
> caminhada reserva os números já usados por chaves literais, inclusive em outros níveis,
> evitando a colisão identificada em E6-AUD4-002. A cobertura depende do reconhecimento
> pelo detector central; percorrer todas as strings não prova reconhecer todo segredo.

`safety/redaction.py` expõe, além de `redact(text)` (escalar) e `redact_document(value)`
(documento, recursivo sobre valores e chaves), a API pura `is_sensitive_key(key)` e
`detect_secret_spans(text)` — mesmo motor de detecção, reaproveitado pelo Context Engine
(E5) para redação estrutural/posicional ([03](03-context-architecture.md) §4). Nenhuma
segunda lista de nomes sensíveis ou segundo motor de regex existe.

> **Fronteira de palavra e segredos colados** *(E6-AUD4-001)*. Os padrões de token opaco
> (`ghp_…`, `sk-…`, `AKIA…`) são ancorados em fronteira de palavra. Entre dois segredos
> colados sem separador não existe fronteira nenhuma, e a âncora deixava passar o segundo —
> e, na *access key* AWS, que tinha âncora dos dois lados, também o primeiro. A âncora passa
> a ser satisfeita de **duas** formas: pela fronteira de palavra, ou pelo início/fim de
> outra ocorrência reconhecida pelo **mesmo** catálogo. ~~O relaxamento é ancorado em
> segredo e só nele: um segredo colado a uma palavra comum continua fora do alcance da
> âncora, como sempre esteve.~~ **(afirmação superada por E6-AUD7-003 — ver "Precisão da
> âncora", logo abaixo. Ela descrevia a fronteira da *esquerda* e foi lida como se valesse
> para os dois lados; a da direita é relaxada em toda âncora.)** A decisão é do motor único
> — nenhum padrão recebe tratamento especial fora de `safety/redaction.py`.

> **Precisão da âncora — o que as variantes derivadas relaxam** *(E6-AUD7-003; decisão
> tomada nesta rodada, não retroativa)*. Durante a convergência, o motor reexecuta o
> catálogo a partir de **âncoras**, usando variantes das expressões com as fronteiras de
> palavra recortadas. As âncoras são de duas origens, e só duas: o **início de uma corrida
> de caracteres de palavra**, e toda posição **dentro ou no fim de uma região já coberta**.
>
> A partir de uma âncora, **as duas** fronteiras da expressão são recortadas — não só a da
> esquerda. As consequências são assimétricas, e é essa assimetria que o texto anterior
> não dizia:
>
> * **à esquerda**, o relaxamento só tem efeito em âncora de região coberta, porque no
>   início de uma corrida de palavra a fronteira já estaria satisfeita. Por isso um segredo
>   colado **depois** de uma palavra comum continua fora de alcance: `tokenizerghp_…` e
>   `xAKIA…` não são reconhecidos, e não há âncora no meio de uma palavra comum;
> * **à direita**, o relaxamento vale em **qualquer** âncora. Um token reconhecido a partir
>   dela é redigido mesmo quando o que vem imediatamente depois é texto comum. Observado:
>   `AKIA0123456789ABCDEFx` → `«redigido»x`, que a expressão canônica isolada não
>   reconheceria por causa da fronteira final.
>
> Hoje o efeito da direita é observável **apenas** em `aws_access_key`, o único padrão do
> catálogo com fronteira de palavra à direita; um padrão novo com fronteira final herdaria
> o mesmo comportamento por construção.
>
> **Este comportamento é mantido deliberadamente.** Ele redige de mais, nunca de menos, e
> neste sistema falso positivo é preferível a falso negativo — a mesma ordem de prioridade
> que [ADR-0009](../adr/0009-provider-capability-enforcement.md) fixa para ausência de
> prova. **A garantia é sobre as variantes derivadas `_ANCHORED` especificamente, não sobre
> o redator como um todo**: elas podem produzir *over-redaction* em relação ao que a
> expressão canônica reconheceria isolada naquela posição, mas não podem cobrir **menos**
> que a passada canônica cobriria. Isto não afirma que qualquer outro caminho ou otimização
> do redator hoje cumpre essa mesma equivalência — `E6-AUD7-001` é um contraexemplo
> conhecido e continua **P1 aberto**, tratado à parte em "Duas garantias abertas", abaixo.

> **Risco residual aceito: precisão da redação** *(E6-AUD7-004; decisão tomada nesta
> rodada)*. O ensaio diferencial que protege o detector (o "GATE 1") garante três coisas
> sobre a emissão — nada deixa de ser escondido em relação à cascata histórica, nada
> reconhecível sobrevive parcialmente, e a divergência de bytes acontece exatamente nos
> casos em que a emissão histórica era insegura — mais a preservação, **por posição**, do
> que o catálogo classifica como rótulo e nunca como carga (`Bearer `, `password: `).
>
> Ele **não** garante que todo caractere público — pontuação, separador, resto de texto
> comum — de uma entrada já autorizada a divergir permaneça visível. Essa entrada pode
> acabar mais coberta do que o estritamente necessário.
>
> A limitação é consciente. Fechá-la exigiria mais uma camada de oráculo, cuja única função
> seria impedir *over-redaction* de pontuação, sem melhorar em nada a garantia de
> confidencialidade. O que a aceitação **não** autoriza, e continua proibido: falso
> negativo de qualquer espécie, apagar rótulo protegido, e qualquer afrouxamento da
> prioridade *fail-closed*.

> **Duas garantias — histórico e fechamento** *(originalmente E6 Rodada 7, findings P1 em
> aberto; a rodada terminou BLOCKED por causa delas. Texto original das duas obrigações
> preservado abaixo sem alteração, como registro histórico de quando estavam abertas)*.
>
> * **Caminho otimizado do bloco PEM** *(E6-AUD7-001)*. A varredura linear do `pem_block`
>   é uma otimização de custo, e otimização de custo não pode mover a fronteira do que se
>   reconhece. Ela precisa preservar a semântica do reconhecimento canônico **em todos os
>   aspectos observáveis** — o conjunto de matches, a ordem deles, os intervalos de
>   `recognition_span`/`replacement_span`, os grupos, e o comportamento em aberturas
>   sobrepostas —, e fazer isso **sem** reintroduzir custo superlinear, que é o que
>   E6-AUD6-001 corrigiu. Enquanto essa equivalência não estiver demonstrada, material PEM
>   visível na saída é um defeito, nunca um comportamento documentado.
> * **Chave estruturalmente sensível** *(E6-AUD7-002)*. `is_sensitive_key` é a resposta do
>   sistema para "não existe adjacência textual que um regex veja": `{"token": "x"}` não
>   contém `token: x` em lugar nenhum até alguém linearizar. A projeção pública precisa
>   tratar uma chave assim como classificação estrutural: **o valor sob ela — escalar,
>   objeto ou lista inteira — é sensível, e a subárvore é projetada *fail-closed***, sem
>   depender de o detector reconhecer forma de segredo no conteúdo. Hoje a Camada 3 aplica
>   `is_sensitive_key` às **chaves** e o detector aos **valores**; a classificação
>   estrutural não atravessa para o valor, e é essa travessia que a implementação deve.
>
> **Decisão de fechamento** *(2026-09-22, autorizada por Pedro nesta sessão — não é
> reescrita silenciosa de documento congelado)*. As duas obrigações acima estão
> **`VERIFIED RESOLVED`**:
>
> * **`E6-AUD7-001 VERIFIED RESOLVED — FROZEN`** — o localizador do corpo PEM
>   (`_localizar_corpos_pem`) substituiu o quantificador guloso por duas primitivas sem
>   retrocesso, preservando a equivalência com o reconhecimento canônico em todos os
>   aspectos observáveis; a prova de custo linear usa a agregação por tripletas
>   intercaladas (`_ORDEM_DAS_TRIPLAS`), que sobreviveu a reverificação independente sob
>   perturbação de carga. Ver `docs/audits/e6-round-7.md`, `e6-round-7v.md`,
>   `e6-round-7v2.md`, `e6-round-7v3.md` (veredito GREEN).
> * **`E6-AUD7-002 VERIFIED RESOLVED`** — `redact_document` passou a aplicar
>   `is_sensitive_key` também à travessia do **valor**: uma chave sensível marca a
>   subárvore inteira, e todo descendente — escalar, objeto, lista, ou contêiner vazio
>   (`E6-AUD7-002V-001`, achado numa reverificação seguinte e corrigido na mesma rodada) —
>   sai como `«redigido»`, sem depender do detector textual reconhecer o conteúdo. Ver
>   `docs/audits/e6-aud7-002-round-1.md` (BLOCKED, achou o caso de contêiner vazio) e
>   `e6-aud7-002-round-2.md` (GREEN).
>
> **A aprovação da E6 inteira permanece pendente de auditoria consolidada.** Estas duas
> garantias resolvidas não substituem uma revisão de conjunto da rodada — outros achados
> de severidade menor (`E6-AUD7-003`, `E6-AUD7-004`, `E6-AUD7-005`, adiante) podem
> continuar abertos, e nenhum commit ou merge da E6 está autorizado só por esta decisão.
>
> As duas exceções JSON deliberadas — `Unredacted` no `purge_token` e
> `EditViewJSONResponse` no edit-view (ver a tabela acima) — não foram alteradas por
> nenhuma das duas correções e continuam exatamente como descritas.

**Denylist inicial** `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa*`, `id_ed25519*`,
`.npmrc`, `.pypirc`, `.git-credentials`, `.aws/**`, `.ssh/**`, `secrets/**`,
`**/credentials*`, `**/*secret*`.

**Ambiente dos filhos é allowlist.** A chave de um provider vai **somente** para o processo
daquele provider — nunca para o Test Runner, nunca para o git.

> **Limite da camada 2:** cobre operações **mediadas**. Com `execute_commands = disabled`
> o Developer não tem como escapar dela. O Test Runner (§6) é outra história.

---

## 6. Test Runner — separado do Developer *(REAUD-001, §3)*

**Testes não são ferramentas do Developer.** O Developer não pode invocá-los, nem direta
nem indiretamente.

```text
Developer conclui as mudanças (só WriteFile / ApplyPatch)
  → Execution Manager
  → Test Runner            ← infraestrutura controlada pelo sistema
  → resultados estruturados
  → próximo estado da task
```

O Test Runner executa `pytest`, `npm test`, lint ou build **somente segundo a `TestPolicy`
configurada pelo sistema** — nunca segundo algo que o provider tenha pedido.

### `TestPolicy`

| Item | Definição |
| --- | --- |
| Executáveis permitidos | allowlist explícita |
| `argv` | configurado/normalizado; nunca string de shell livre |
| `cwd` | a worktree da task |
| `timeout` | por execução |
| Ambiente | allowlist, sem chaves de provider |
| Política de rede | **declarada** (não imposta tecnicamente na V1) |
| Limites de saída | tamanho máximo capturado |

`policy_hash` e `command_hash` derivados dessa configuração entram no `test_binding` do
`execution_fingerprint` ([02](02-data-model.md) §7).

### Declaração explícita

> A mediação de ferramentas protege a autoridade do **provider**. Ela **não** transforma o
> código do projeto executado pelo Test Runner em código confinado.
>
> Executar testes executa código do projeto, que pode ler qualquer arquivo do usuário,
> abrir rede e escrever fora da worktree. **Sem isolamento de SO, isso permanece risco
> residual aceito.** Não é sandbox, e não deve ser chamado assim.

O que a V1 oferece aqui: `cwd` na worktree, ambiente por allowlist, allowlist do **ponto de
entrada**, timeout, terminação de árvore de processos e verificação pós-execução da árvore
principal. O que **não** oferece: impedir que um script de teste comprometido escreva em
`%USERPROFILE%`, leia `~/.ssh` ou abra rede.

---

## 7. Limites, aprovação, verificação e OneDrive

### Limites

| Limite | Ação ao estourar |
| --- | --- |
| `run_timeout_s` | mata a árvore de processos → `Run.timeout` → `failed(timeout)` |
| `task_timeout_s` | idem, no nível da task |
| `max_context_tokens` / `max_total_tokens` | `SafetyEvent(limit_exceeded)` → `failed(limit_exceeded)` |
| `max_attempts` / `max_fix_rounds` | `SafetyEvent(retry_limit)` → `failed` |
| `max_parallel_agents = 1` | tasks aprovadas esperam em fila |

**Terminação de árvore de processos** — sinal cooperativo → período de graça → terminação
da árvore inteira (Windows: enumeração recursiva; POSIX: grupo de processos) → confirmação
de que nenhum descendente sobreviveu. Aplica-se ao Test Runner e ao processo do provider.

#### Adendo autorizado — E7.3 (2026-09-29): mecanismo de terminação de árvore

> Adendo aprovado por Pedro (decisões D2, D4 e D5 do planejamento da E7.3). Mantém o
> objetivo do parágrafo acima — nenhum descendente sobrevive, e isso é **verificado** — e
> troca o mecanismo do Windows. Implementado em `api/app/process_runtime/` (módulo definido
> no adendo E7.3 de [01](01-v1-architecture.md) §2).

**Windows — Job Object no lugar da enumeração recursiva de PIDs.** O Windows não guarda o
vínculo pai→filho depois que o pai sai, e PIDs são reutilizados: enumerar e matar perde quem
nasce no intervalo. Por isso:

1. Job Object criado com `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
2. Processo raiz criado com `CREATE_SUSPENDED` e associado ao Job **antes** de executar
   qualquer instrução — todo descendente nasce dentro do Job, sem janela de corrida.
3. Retomada só por API Win32 documentada (`CreateToolhelp32Snapshot` + `Thread32First`/
   `Thread32Next` + `OpenThread` + `ResumeThread`); sem `NtResumeProcess`.
4. Término por `TerminateJobObject`; confirmação por `ActiveProcesses == 0` **e** por cada
   processo visto no Job estar com o objeto de processo sinalizado.
5. `KILL_ON_JOB_CLOSE` faz parte do ciclo de vida: se o supervisor morrer, o SO fecha o
   handle do Job e a árvore é encerrada.
6. Sem mecanismo de término cooperativo comprovado para uma árvore arbitrária, o
   encerramento no Windows é forçado — não há período de graça genérico (D4).
7. Falha ao criar, configurar, associar ou retomar é fail closed: o que foi criado é
   encerrado e o resultado é `supervision_failed`. Nenhum processo roda sem contenção.

**POSIX — sessão/grupo de processos.** Nova sessão (`start_new_session`) → `SIGTERM` ao
grupo → período de graça → `SIGKILL` → colheita do raiz → verificação de grupo vazio.
Limitações declaradas: descendente que chame `setsid`/`setpgid` escapa do grupo; não há
equivalente simples a `KILL_ON_JOB_CLOSE` (supervisor morto por `SIGKILL` pode deixar
descendentes); sem cgroups, subreaper ou contêiner.

**Executável (D5).** `argv[0]` e `cwd` absolutos; `.bat`/`.cmd` recusados — seriam
interpretados pelo `cmd.exe`, com regras de citação próprias.

**Supervisão de processo não é sandbox.** Timeout, Job Object, ausência de shell e
encerramento de árvore controlam o **ciclo de vida** do processo. Não impedem leitura de
arquivos, escrita fora da worktree, acesso à rede, acesso a segredos, uso de APIs do SO nem
criação de processos por mecanismos externos à árvore (serviços, WMI, COM, Agendador de
Tarefas). O que §6 declara sobre o Test Runner continua valendo integralmente.

#### Adendo autorizado — E8.4.1 (2026-10-08): valores e interpretação dos timeouts

> Adendo aprovado por Pedro (contrato da E8.4.1). Aditivo; a tabela acima e o adendo E7.3 valem
> sem alteração. **Esta etapa fixa os valores; a supervisão operacional é das etapas de runtime.**

* `run_timeout_s = 1200` e `task_timeout_s = 1800`, em `execution_limits` (entram no
  `execution_fingerprint`; [02](02-data-model.md) §7).
* Cada operação — a invocação do Developer, a execução de testes — tem no máximo 20 minutos. A
  tentativa completa — preparação, execução, testes e verificação — tem no máximo 30 minutos. O Run
  de controle acompanha o prazo da task (`started_at + task_timeout_s`).
* O limite de cada operação futura respeita o tempo restante da task
  (`execution_contract.operation_timeout_s`: o menor entre `run_timeout_s`, o que resta da task e o
  timeout da `TestPolicy`/budget). `TestPolicy` e budgets mais restritivos continuam prevalecendo.

### Aprovação vinculada

`approve` exige o `execution_fingerprint` completo ([02](02-data-model.md) §7). Qualquer
campo coberto que mude — plano, manifest, payload renderizado, base commit, binding do
Developer, do Auditor ou do Test Runner, agentes, perfil de capability, hash de política de
segurança, hash de política de workflow, limites — invalida a aprovação: `409`,
`SafetyEvent(approval_invalidated)`, volta a `awaiting_approval` indicando **qual campo**
mudou.

### Verificação pós-execução

1. `git status --porcelain=v2` na **árvore principal**, comparado ao snapshot anterior ao
   run. Diferença → `SafetyEvent(out_of_worktree_write)`, run `blocked`, task para.
2. Arquivos alterados na worktree reconferidos contra a política de path.
3. O diff passa pelo redator antes de ser exposto.

**Alcance real:** detecta escrita na árvore principal. **Não** detecta escrita em outros
lugares do disco. É detecção pontual, não contenção.

#### Adendo autorizado — E8.3 (2026-10-06): Test Runner e verificação pós-execução

> Adendo aprovado por Pedro (decisões D1–D10 da E8.3 e C1–C15 da correção da auditoria
> independente). §6 e os itens 2 e 3 acima continuam valendo sem alteração. O **objetivo** do
> item 1 (detectar escrita na árvore principal) também; o **mecanismo** citado nele — `git
> status` antes × depois — é substituído, por decisão C1 do Product Owner, pelo estado
> *read-only* e *filter-free* descrito abaixo, que detecta estritamente mais e não executa
> nenhum helper do projeto.
> Módulos: `agent_runtime/runners/local_subprocess.py`, `git_runtime/post_execution.py`,
> `execution_verification.py` (arestas no adendo E8.3 de [01](01-v1-architecture.md) §2).

**Test Runner (`generic-subprocess-v1`).** Executa só a `TestPolicy`, sob `process_runtime`:

1. `cwd` = o workspace **dentro** da worktree do run, achado pelo binding explícito
   `(workspace_ref, run_scope)` — a mesma conferência da `ToolExecutorFactory`.
2. Executável lógico → caminho **absoluto** confiável, resolvido só nas entradas **absolutas**
   do `PATH` do ambiente confiável do host — nunca pelo `cwd`, nunca por entrada vazia ou
   relativa, nunca dentro da worktree (pelo caminho e pelo destino real). Caminho absoluto
   configurado é aceito com as mesmas regras. No Windows, só `.exe`/`.com` são lançáveis (o
   `PATHEXT` não participa); `.bat`/`.cmd` são recusados em qualquer SO — um projeto Node usa
   `node.exe` + o *entrypoint* JS, nunca `npm.cmd`. Intérpretes de linha de comando (`cmd`,
   `powershell`/`pwsh`, `bash`, `sh` e afins, hosts de script do Windows, `env`) são recusados
   como executável, pelo nome e pelo destino real: nenhum shell interpreta o comando.
3. Ambiente do filho **construído do zero**: allowlist ∩ ambiente confiável. Nome ausente não
   aparece; `PATH`/`SYSTEMROOT` só entram se allowlisted. Variável de provider, token,
   credencial ou chave — e também **localizador de credencial** (`DOCKER_CONFIG`, `KUBECONFIG`,
   `NETRC`, `GOOGLE_APPLICATION_CREDENTIALS`, `AWS_SHARED_CREDENTIALS_FILE`, `SSH_AUTH_SOCK`,
   `SSH_ASKPASS`, `GIT_ASKPASS`…) e *connection string* com credencial (`DATABASE_URL` e afins)
   — é **recusada antes do processo nascer**, mesmo se allowlisted
   (`safety.test_policy.is_forbidden_test_env_name`, consultada pelo runner a cada execução,
   inclusive para uma `TestPolicy` montada à mão).
4. Processo que terminou (`EXITED`) é `TestSummary`, inclusive com `exit_code != 0`: falha de
   teste não é falha técnica. Timeout, cancelamento e falha de supervisão são falhas
   estruturadas (`TestRunnerFailure`), com a árvore encerrada; nenhum `TestSummary` é inventado.
5. `stdout`/`stderr` são drenados e limitados pelo Supervisor e **descartados**: não vão a
   `TestSummary`, exceção, log, banco nem `SafetyEvent`. `output_ref = None` na V1.

**O Test Runner não é sandbox** — §6 vale integralmente: o código do projeto ainda pode ler
`~/.ssh`, abrir rede, escrever fora da worktree e usar APIs do SO. Risco residual aceito até a E14.

**Verificação pós-execução, item 1 — árvore principal.** O estado é do **repositório
principal inteiro** — a raiz vem do Git (`rev-parse --show-toplevel`), não do `local_path` do
workspace, que pode ser subdiretório de um monorepo — e é **read-only e filter-free**: nenhum
`git status`/`diff`/`add`/`checkout`/`hash-object`, nenhum filtro (`clean`/`smudge`/`process`),
`fsmonitor`, `textconv`, diff externo, hook, shell ou helper do projeto. O único Git é
`rev-parse` (raiz, git dir, `.git` comum, caminho do índice efetivo). O estado combina:

* **identidade da raiz** — toplevel e `.git` comum, por caminho e identidade de objeto (um "antes"
  de um repositório não vale para a worktree de outro);
* **índice efetivo** — os bytes crus do arquivo que o próprio Git aponta, resumidos por SHA-256
  em leitura estável (*staged*, `assume-unchanged`, `skip-worktree` e qualquer outra mudança no
  índice aparecem);
* **filesystem da raiz** — todo *directory entry* ordinário, rastreado, não rastreado e
  **ignorado**, inclusive os arquivos de repositórios aninhados como filesystem comum: caminho em
  bytes, tipo, bits de modo, tamanho e SHA-256 do **conteúdo** (arquivo regular); o alvo do link
  (symlink/junction), que **nunca é seguido**; o tipo de FIFO/socket/device, que **nunca é
  aberto**. Reparse point de outro tipo, ou leitura que não fecha, é não verificável. O git dir
  administrativo da raiz não é percorrido — o índice é a exceção explícita. Cada diretório entra
  na travessia com a identidade observada e é revalidado (tipo, link/reparse, identidade)
  imediatamente **antes** do `scandir` e de novo **depois**: entrada trocada por junction,
  symlink, arquivo ou outro diretório enquanto esperava falha fechado (E8.3-FINAL-002). Não é
  proteção absoluta: troca **e restauração** concorrentes entre as duas observações, sem rastro
  de identidade, são TOCTOU residual declarado (a sandbox é a E14).

Antes × depois diferente → `out_of_worktree_write` — inclusive rastreado limpo ou já sujo,
`assume-unchanged`, `skip-worktree`, não rastreado ou ignorado novo ou com conteúdo trocado,
mudança no índice, remoção, rename, modo e conteúdo em repositório aninhado. Estado ilegível
antes **ou** depois → não verificável (fail closed), nunca "igual". **Fora do contrato,
declarado:** os demais metadados do `.git` (`HEAD`, refs, objetos, config, hooks, `info/exclude`,
reflogs) e *alternate data streams* do NTFS.

**Orçamento.** `capture` e `verify` têm, **cada um**, um orçamento único que cobre todos os seus
subpassos (no `verify`: o estado "depois", a identidade da worktree, os registros do Git, o
filesystem da worktree e o hash): **100 000 entradas** observadas (cobradas durante a enumeração,
nunca depois de materializar o iterador), **1 GiB de bytes efetivamente lidos** (cobrados por
bloco lido, nunca pelo `stat`; arquivo que cresce durante a leitura é recusado), **60 s** de
relógio monotônico e o **cancelamento** do run (porta neutra `is_cancelled`). Prazo e
cancelamento são consultados antes **e depois** de cada passo potencialmente longo (leitura Git,
diretório, bloco, inspeção de caminho); o *timeout* de cada processo Git — inclusive o mediado —
é o que resta do prazo, calculado imediatamente antes de iniciá-lo (sem prazo restante, o
processo não nasce). `VERIFIED` sai de um único ponto, que revalida o orçamento antes de liberar
(E8.3-FINAL-001). Orçamento esgotado é não verificável com causa estruturada
(`limit_exceeded`, `deadline_exceeded`, `cancelled`) — **nunca** `VERIFIED` parcial.

**Item 2 — worktree do run.** O Git Runtime é a única fonte das mudanças: a worktree **inteira**
contra o `base_commit`, com a identidade conferida antes (prefixo confirmado pelo Git, `.git`
comum do repositório principal, `.git` da worktree apontando para o admin dir que a registra,
`HEAD == base_commit`, nenhuma operação em andamento). Como na E7.4/E7.5-D, o Git **não lê** a
worktree: árvore do base, índice e `diff-index --cached` vêm do Git mediado (sob o Supervisor e o
orçamento), **cientes do formato de objeto** do repositório (`sha1` ou `sha256`, lido por
`rev-parse --show-object-format`; outro valor é não verificável); o disco é percorrido no Python,
sem seguir link (com a mesma revalidação de diretório antes e depois da descida, e o mesmo TOCTOU
residual), **ignorados incluídos**, com o oid de blob calculado no formato do repositório
(`b"blob <tamanho>\0" + bytes`, SHA-1 ou SHA-256) em streaming.
Para workspace em subdiretório, **qualquer mudança fora do
`workspace_prefix` é recusada** (`outside_workspace_change`); dentro dele, cada caminho mudado
passa pela pipeline de uma **escrita** mediada (`prevalidate_mediated_path` → `inspect` →
`decide_path(WRITE)`), e caminho apagado é validado pelo próprio caminho, sem recriá-lo. Link,
junction, reparse point, entrada que não é arquivo nem diretório, nome sem representação ou
leitura incompleta falham fechado. Tudo é **só leitura** e devolve um fato estruturado; quem
grava `SafetyEvent`, bloqueia o run e para a task é o Execution Manager (E8.4).

### OneDrive — mitigação parcial

Worktrees fora do OneDrive **reduzem**, mas **não eliminam** o risco: os metadados da
worktree vinculada (`.git/worktrees/<nome>/` com `gitdir`, `HEAD` e índice), refs, objetos
e `.git/index` vivem no `.git` do **repositório principal**, dentro do OneDrive, e seguem
sujeitos a lock e sincronização concorrente.

**Risco residual aceito, com recuperação documentada:** `git worktree list` →
`git worktree prune` → `git worktree repair` → em último caso, remoção do diretório de
metadados obsoleto em `.git/worktrees/<nome>/`.

**Alternativas futuras, em ordem de simplicidade** — nenhuma implementada:
**(1)** mover o repositório para fora do OneDrive; **(2)** clone/cache operacional fora do
OneDrive; **(3)** repositório operacional *bare*/mirror.

---

## 8. Protocolo do Git runtime

O `git_runtime` executa git com `argv` formado pelo runtime, `shell=False` e `cwd` fixado.
Ele é infraestrutura do sistema — **não** é uma ferramenta do provider. O Developer só
alcança git por `GitStatus`/`GitDiff`/`GitShow`/`GitListTree`, que o `ToolExecutor`
traduz.

**Nunca executa** `commit`, `merge`, `push`, `pull`, `fetch`, `rebase`, `reset --hard`,
`clean -fdx`, `filter-branch`, `remote set-url`, `config --global`. Commit, merge e push
permanecem humanos na V1.

```mermaid
sequenceDiagram
  participant EM as Execution Manager
  participant GR as git_runtime
  participant TF as ToolExecutorFactory
  participant AR as agent_runtime
  EM->>GR: preflight + resolve_base_commit
  Note over EM: base_commit congelado no PLANEJAMENTO
  EM->>AR: verificar perfil de capability (fail closed)
  EM->>GR: create_worktree(base_commit, task_id)
  EM->>TF: create(workspace_ref, policy, profile, run) → ToolExecutor do run
  EM->>AR: DeveloperProvider.run(request, mediated_tools)
  AR-->>EM: AgentRunResult
  EM->>AR: TestRunner.run(TestPolicy)
  AR-->>EM: TestSummary
  EM->>GR: diff(worktree, base_commit)
  GR-->>EM: UnifiedDiff + files_changed + diff_stat
  EM->>AR: AuditorProvider.audit(diff, criteria)
  AR-->>EM: AuditResult
  EM->>EM: verificação pós-execução + persistência
  Note over EM: usuário revisa o diff e decide.<br/>Commit, merge e push são humanos.
```

Convenções: worktree em `<data_dir>/worktrees/ff-task-<id8>`, branch `ff/task-<id8>`,
criada a partir do **SHA congelado no planejamento** — nunca de um nome de branch.

### Comportamento em cada situação

| Situação | Comportamento |
| --- | --- |
| **Não é repositório git** | Workspace utilizável para **contexto**; execução bloqueada com motivo. O backend **não** roda `git init` |
| **Árvore principal suja** | Não impede. A worktree nasce do `base_commit` e não enxerga o não commitado. Divergência registrada no manifest e mostrada antes da aprovação |
| **HEAD mudou desde o planejamento** | Fingerprint diverge → aprovação invalidada → replanejar. A execução **nunca** salta para o HEAD novo |
| **`default_branch` inexistente** | Usa o HEAD corrente e registra `base_commit` |
| **Worktree já existe para a task** | Reutilizada se pertence à mesma task e está limpa; caso contrário recusa |
| **Cancelamento / falha** | Árvore de processos morta; worktree **preservada** para diagnóstico |
| **Crash do backend** | `reconcile_on_startup()` cruza `git worktree list` com as tasks; idempotente |
| **Cleanup** | Nunca automático em falha. Em `done`, `POST /api/tasks/{id}/worktree/discard`. GC por idade (14 dias) só em tasks terminais, sempre via `git worktree remove` + `prune` |

O `git_runtime` nunca escreve no banco: devolve fatos, e o Execution Manager persiste. É a
**fonte autoritativa** de `files_changed` e `diff_stat` ([02](02-data-model.md) §10).

#### Adendo autorizado — E7.4 (2026-09-30): criação de worktree

> Adendo aprovado por Pedro (decisões D2, D3, D6 e D8 do planejamento da E7.4; **revisado**
> na correção dos P2-001/P2-002 da auditoria, também autorizada por Pedro). **Estritamente
> aditivo**: a lista "Nunca executa" acima e a tabela de situações continuam valendo sem
> alteração. A aresta de módulo está no adendo E7.4 de [01](01-v1-architecture.md) §2.

1. **O Git não materializa conteúdo.** A criação usa `git worktree add --no-checkout` (branch,
   metadata e o arquivo `.git` da worktree; nenhum arquivo do projeto) e depois
   `git read-tree` **sem `-u`** (só o índice recebe o `base_commit`, com
   `--no-sparse-checkout`). O runtime escreve cada arquivo a partir do **blob cru** do commit
   (`git cat-file --batch`, oid recalculado), pelo escritor verificado do `path_runtime`.
   Nenhum checkout, `reset --hard`, `checkout-index`, `read-tree -u`, `restore`, `switch` ou
   `archive` participa — e portanto nenhum filtro (`smudge`/`process`/`clean`), atributo ou
   `git-lfs`: o pipeline de conversão de working tree do Git fica fora da E7.4, qualquer que
   seja a configuração (`includeIf`, `info/attributes` mudando no meio da operação). Um ponteiro
   LFS versionado é materializado como o ponteiro; nenhuma conversão de fim de linha é
   aplicada. Isto **substitui** a neutralização por enumeração de drivers da versão anterior
   deste adendo, que tinha dois furos reproduzidos (valor literal `set`/`unset`/`unspecified`
   e corrida em `info/attributes`).
2. **Árvore validada antes do primeiro byte.** Só `100644`/`100755` são materializados; symlink
   e gitlink são recusados explicitamente (`UNSUPPORTED_TREE_ENTRY`), sem aproximação. Todo
   caminho passa pela política de path do `safety` (mais: sem `.git`, sem colisão por caixa —
   também entre prefixos de diretório, como `Dir/a` × `dir/b` —, sem conflito arquivo ×
   diretório, dentro de `MAX_PATH` no Windows). Todo blob precisa
   existir localmente: `--no-lazy-fetch` e `GIT_NO_LAZY_FETCH=1` — nunca fetch, nunca rede.
3. **Limpeza verificada sem `git status`.** Reuso e pós-condição usam a **mesma** verificação:
   índice exatamente igual ao `base_commit` — estrutura **e** flags de entrada
   (`ls-files --stage`/`-v`, e `diff-index --cached --ita-invisible-in-index`, que não lê o
   working tree e enxerga `intent-to-add`) — e filesystem exatamente
   igual ao snapshot (sem seguir link; oid de blob calculado dos bytes crus; bit de execução
   no POSIX). Arquivo ou diretório extra, ignorado incluído, é sujo.
4. **Supervisão.** Os dois passos Git mutantes (`worktree add --no-checkout`, `read-tree`)
   rodam sob `process_runtime` — árvore contida, timeout, cancelamento, confirmação de morte.
   Motivo empírico: encerrar só o `git.exe` deixava vivos os processos filhos que ele cria.
   A materialização consulta o cancelamento entre arquivos. Nada é limpo em falha. Leituras
   Git permanecem no runner atual. Isso não dá ao provider acesso a `process_runtime` nem a Git.
5. **Hooks desligados por comando**: `-c core.hooksPath=/dev/null` nos dois passos (o `add`
   atualiza refs; o `read-tree` escreve o índice), sem alterar configuração persistente. O
   `git worktree add` ainda executa, ele próprio, um `git branch` interno; o runtime continua
   **proibido** de construir ou invocar `git reset --hard` (ou qualquer checkout) diretamente.

#### Adendo autorizado — E7.5-D (2026-10-03): as quatro leituras Git do Developer

> Adendo aprovado por Pedro (tarefa E7.5-D). **Estritamente aditivo**: a lista "Nunca executa",
> a tabela de situações e o adendo E7.4 acima continuam valendo sem alteração. A aresta de
> módulo está no adendo E7.5-D de [01](01-v1-architecture.md) §2.

1. **Operações fixas.** `GitStatus`, `GitDiff`, `GitShow` e `GitListTree` exigem `git_read`
   em `fixed_operations_only`; o gate de capability roda **antes** de qualquer processo. O
   provider escolhe só a operação, uma ref tipada e um caminho tipado; o `argv` é formado
   apenas por `git_runtime/mediated.py`. Caminho do provider **nunca** entra no `argv`: a árvore
   é listada inteira e filtrada pelo runtime.
2. **Âncora no `base_commit`.** `ref=None` é o `base_commit` do run. Uma ref nomeada é resolvida
   **uma vez** (`rev-parse --verify --quiet --end-of-options <ref>^{commit}`) para um commit
   completo (SHA-1 ou SHA-256, conforme o repositório), e só vale se for o `base_commit` ou
   ancestral dele (`merge-base --is-ancestor`); fora disso, `DENIED
   git.ref_outside_base_history`, registrado. O `HEAD` ambiente nunca é autoridade —
   `GitStatus` também compara contra o `base_commit`.
3. **O Git nunca lê a worktree.** Só comandos de objeto e índice: `rev-parse`, `merge-base`,
   `ls-tree -r -z -l --full-tree`, `ls-files --stage`/`--others --exclude-standard`,
   `cat-file blob|commit` (oid recalculado) e `diff-index --cached --ita-invisible-in-index
   --no-renames --no-ext-diff --no-textconv --raw -z` — o mesmo argv do adendo E7.4, usado
   **só** para observar árvore × índice (inclusive *intent-to-add*, que `ls-files --stage` não
   expõe), sem conteúdo de diff e sem tocar o disco. Nenhum `status`, `diff`, `diff-files`,
   `diff-index` sem `--cached`, `show`, `log` — eles podem rodar `clean`/`process` de filtros e
   `textconv` do repositório. O conteúdo atual é lido pelo `path_runtime` (inspect → política →
   `open_existing` → pós-abertura → leitura) e comparado em Python sobre os bytes crus (oid de
   blob). Mesma decisão do adendo E7.4: o pipeline de conversão de working tree do Git fica fora.
4. **Sem patch do Git.** `diff -p`, `show -p` e `diff-tree -p` não são usados. O unified diff do
   `GitDiff` é renderizado em Python (`tool_executor/diff_render.py`, puro), a partir dos blobs
   crus e do conteúdo atual; binário e conteúdo acima da captura interna viram marcadores sem
   conteúdo; índice divergente de um arquivo igual ao commit não some (marcador).
5. **Sem rede, helpers, hooks ou filtros.** Opções fixas em toda invocação: as de leitura já
   auditadas (`core.fsmonitor=false`, `core.quotepath=false`, `diff.relative=false`,
   `status.relativePaths=false`) mais `--no-pager`, `--no-lazy-fetch`, `--no-replace-objects`,
   `protocol.allow=never`, `credential.helper=` (vazio), `core.hooksPath=/dev/null`,
   `core.untrackedCache=false`, `gc.auto=0`, `maintenance.auto=false`. Ambiente mínimo com
   `GIT_OPTIONAL_LOCKS=0`, `GIT_TERMINAL_PROMPT=0` e `GIT_NO_LAZY_FETCH=1`; nenhum `GIT_*`
   herdado. Objeto ausente num partial clone é `ERROR git_object_unavailable`, nunca fetch.
   Nenhum subcomando altera worktree, índice, refs, `HEAD` ou config.
6. **Captura interna ≠ saída.** A saída estrutural do Git é capturada até 4 MiB
   (`git_internal_capture_bytes`); passou disso, `ERROR git_output_unverifiable` — nunca um
   prefixo interpretado como resposta completa. `stderr` é limitado e descartado. O
   `ToolResult` segue a fronteira central já auditada: saída completa → redação uma vez →
   256 KiB em bytes UTF-8 → `OK` ou `DENIED limit.tool_result_content_bytes`, sem corte.
   `GitShow(path)` nega blob acima de 256 KiB **antes** de ler o conteúdo.
7. **Caminhos.** Caminho explícito negado → a operação inteira é `DENIED`. Caminho descoberto
   negado → registrado e omitido (nunca lido). Caminho de fora do workspace é descartado (nunca
   `../`). Caminho de dentro que o `ToolExecutor` não sabe nomear (UTF-8 inválido, `\` literal)
   no escopo pedido → `ERROR git_output_unverifiable`; symlink, gitlink ou repositório aninhado
   no escopo → `ERROR unsupported_git_tree_entry`.
8. **Uso medido.** `files_read` conta só o conteúdo entregue: o caminho de `GitShow(path)` e
   cada seção textual do `GitDiff`, depois do `ToolResult OK`. Leituras internas, marcadores e
   metadados não contam.
9. **Correções da auditoria (D-AUD-001 a 004).** O índice é observado pelo inventário
   **e** pela divergência árvore × índice, unidos; nenhum mapa por caminho nasce antes de
   validar que cada caminho tem exatamente um estágio 0 **ou** só estágios de conflito sem
   repetição (duplicata, mesmo idêntica, → `ERROR git_output_unverifiable`). No `GitDiff`, um
   índice divergente do commit sempre sai como `index\t<p>`, diverja o disco ou não. O
   cancelamento é consultado antes de cada processo Git, pelo Supervisor durante, **depois**
   que ele devolve (vence até `EXITED` completo) e de novo antes de cada `OK`.
