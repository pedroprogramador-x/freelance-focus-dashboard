# Auditoria E5 — rodada 4: prefixos, fragmentos e contexto de reconhecimento

Data: 2026-09-11. Auditor: Codex. HEAD auditado: `9793c1a2907b93405d3656a27547d1954f5541d9`, com alterações locais descritas abaixo. Nenhuma correção de código, mudança de arquitetura ou commit realizado.

**Veredito: NÃO GREEN.** As reproduções exatas de E5-AUD3-002 e E5-AUD3-003 estão corrigidas. E5-AUD3-001 é política aceita, não foi reaberto. O Foco 2 falha por vazamento parcial e perda do prefixo; um cenário adicional revela vazamento integral de credenciais de URL por contexto em lookaround não representado no `recognition_span`.

## 1. Contexto e estado inicial

Lidos os relatórios das rodadas 1–3, as notas de implementação das rodadas 3–4, especialmente os dois intervalos e a decisão de manter `whole`, a seção 4 de `03-context-architecture.md` e o AGENT_LOG (histórico e entradas E5). Os quatro módulos untracked `file_map.py`, `manifest.py`, `rendering.py` e `selection.py` e as três suítes E5 foram lidos integralmente. Também foram examinados o conteúdo completo de `safety/redaction.py`, os diffs rastreados e as travas de arquitetura. O stat não foi usado como substituto da leitura dos untracked.

Primeiro comando: `git status --short`, saída completa:

```text
 M AGENT_LOG.md
 M api/app/context_engine/__init__.py
 M api/app/safety/redaction.py
 M api/tests/context_helpers.py
 M api/tests/test_architecture.py
?? api/app/context_engine/file_map.py
?? api/app/context_engine/manifest.py
?? api/app/context_engine/rendering.py
?? api/app/context_engine/selection.py
?? api/tests/test_context_redaction_e5_round3.py
?? api/tests/test_context_redaction_e5_round4.py
?? api/tests/test_context_router_e5.py
?? docs/audits/
```

`git diff --stat`, saída completa:

```text
 AGENT_LOG.md                       |  44 +++++
 api/app/context_engine/__init__.py | 105 ++++++++++++
 api/app/safety/redaction.py        | 336 ++++++++++++++++++++++++++++++++++++-
 api/tests/context_helpers.py       |  51 +++++-
 api/tests/test_architecture.py     |  55 ++++++
 5 files changed, 581 insertions(+), 10 deletions(-)
```

Git também emitiu avisos de acesso negado ao ignore global e de conversão futura LF→CRLF. Os dois comandos concluíram normalmente.

## 2. Execução e evidência

Ambiente existente `%TEMP%/e4-aud6-venv`, Windows, Python 3.12.14 e pytest 8.4.2. Testes executados de `api/`, com `PYTHONDONTWRITEBYTECODE=1` e `-p no:cacheprovider`. Bancos migrados, repositórios sintéticos e artifacts ficam no TEMP. Os commits dos fixtures pertencem exclusivamente aos repositórios sintéticos, não ao projeto auditado.

- **Suíte completa do backend:** `python -m pytest -p no:cacheprovider -o addopts='' -q` terminou em **1115 passed, 8 skipped, 1 failed, 1 error**, 408,42 s. As duas ocorrências foram acesso negado do sandbox: `test_fatos_de_sintaxe_windows_sao_registrados` ao consultar o UNC sintético e `test_real_build_same_origin_bootstrap_and_authenticated_roundtrip` ao carregar a configuração Vite. Reexecutados somente esses dois testes fora do sandbox: **2 passed em 16,76 s**. Resultado agregado **1117 aprovados, 8 pulados, nenhuma falha pendente na suíte existente**, sem modificar código/configuração. Não confundir esse agregado com uma segunda execução completa ou com os 1119/6 registrados pela implementação em outro ambiente.
- `ruff check --no-cache`: passou.
- `ruff format --check --no-cache`: 77 arquivos já formatados.
- `mypy`, cache externo: passou, 75 arquivos.
- Provas independentes finais: **12 passed, 3 failed em 27,67 s**. As três falhas são asserts dos requisitos, detalhados nos findings. Os 12 testes aprovados incluem provas que afirmam a presença do defeito observado, não 12 atestados de segurança.
- Complemento histórico: **2 passed, 8 deselected em 10,68 s**, reexecutando o falso positivo aceito e a prova forte de normalização de título/identidade de caminhos da rodada 3.
- Comparação independente de `redact()` com o código histórico real do HEAD: **588 entradas, zero divergências nos bytes UTF-8**.

Script e saída completos preservados fora do repositório:

```text
C:/Users/pedro/.codex/visualizations/2026/09/09/01a086e1-754d-77c1-9a77-4af609dd6d66/e5-round4/test_audit.py
C:/Users/pedro/.codex/visualizations/2026/09/09/01a086e1-754d-77c1-9a77-4af609dd6d66/e5-round4/results.txt
```

Reprodução de `api/`, com Python que contenha as dependências do backend:

```powershell
$env:PYTHONPATH = (Get-Location).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
python -m pytest -c pyproject.toml -p tests.conftest -p no:cacheprovider 'C:/Users/pedro/.codex/visualizations/2026/09/09/01a086e1-754d-77c1-9a77-4af609dd6d66/e5-round4/test_audit.py' -s -q -o addopts=''
```

Os cenários usam `create_entry` → `select_context` → `freeze_manifest` → `Path.read_bytes()` do arquivo apontado pelo manifest. SHA-256 dos bytes foi conferido em cada prova observacional. Os testes `test_required_gate` falham quando o valor proibido aparece nos bytes ou o prefixo exigido desaparece do texto relido.

## 3. Findings

### E5-AUD4-001 — Alta/P1 — A interação pedida deixa a cauda do segredo no artifact

**Arquivo/linha:** `api/app/context_engine/rendering.py:406–408`, `:433–434`, `_is_greedy_extension` em `:441–459`.

**Reprodução real, dois fragmentos sem folhas adicionais:**

```python
title = "password: sk-123"
body = "456789ABC"
structured = None
```

Texto dos bytes finais:

```text
### contexto · stack

## password: «redigido»

456789ABC
```

O prefixo foi preservado, mas a cauda sensível `456789ABC` foi emitida integralmente. `transformations` contém apenas `normalize`, `redact`; não há marcador de travessia.

**Causa comprovada:** `password: sk-123` já casa `assigned_secret` sozinho: `sk-123` tem seis caracteres, exatamente o piso desse padrão. Isolado, o motor devolve reconhecimento `[0,16)` e substituição `[10,16)`; concatenado, reconhecimento `[0,25)` e substituição `[10,25)`. A fronteira está em 16. A travessia é reconhecida corretamente, porém rejeitada porque o início 0 já pertence a `own_starts`. A projeção inteira aplica a mesma rejeição. A propagação conhece apenas `sk-123`, não a cauda.

Isso corrige uma premissa do exemplo do prompt: o valor completo só existe concatenado, mas a primeira parte **já é reconhecível como assigned_secret**. O teste não perde validade: é precisamente a interação entre o prefixo contextual e a decisão de tratar um match parcial como credencial completa.

**Relação com histórico:** manifestação do limite residual 2 já declarado, não uma nova causa-raiz. Foi numerado por violar diretamente o critério explícito desta rodada (“nem vazamento parcial”). A aceitação de falso positivo por par arbitrário não constitui aceitação desse falso negativo.

**Recomendação:** não descartar a extensão reconhecida apenas por igualdade da posição inicial. Mapear os intervalos de substituição reconhecidos para todos os fragmentos alcançados, preservando o rótulo, em conformidade com a prioridade V1 por evitar falso negativo. Se a heurística for mantida, este caso não pode ser apresentado como fechado; exigiria aceitação explícita dessa exceção ao critério pedido.

**Provas:** `test_bytes[requested_split…]` confirma o comportamento; `test_required_gate[password: sk-123…]` falha pela cauda presente.

### E5-AUD4-002 — Média/P2 — Detecção correta do split apaga também o prefixo preservável

**Arquivo/linha:** `api/app/context_engine/rendering.py:409–410`, `:435` e `:515–516`.

**Reprodução real mínima:**

```python
title = "password: sk-12"
body = "3456789ABC"
structured = None
```

Nenhum fragmento isolado casa; juntos casam `assigned_secret`. O intervalo de substituição exclui os dez caracteres de `password: `. Entretanto o texto persistido é:

```text
### contexto · stack

## «redigido»

«redigido»
```

As duas partes do segredo desapareceram, mas o rótulo também. A chamada isolada `redact("password: sk-123456789ABC")` preserva `password: `; a perda ocorre no consumo pelo renderer, não na substituição de `redact()`.

Também executei `body="password: sk-123"`, `structured={"resto":"456789ABC"}`, com título comum. Nessa disposição as duas partes desaparecem, mas o corpo inteiro e o rótulo da folha são apagados. Uma travessia adicional com o rótulo e a projeção integral mascaram o vazamento que fica visível na reprodução mínima do finding 001. Logo esse resultado seguro isolado não demonstra fechamento da interação. Variantes abaixo do piso com `password: sk-12` e `Bearer ABC` também redigem o prefixo inteiro.

**Causa:** as leituras entre fragmentos usam corretamente `recognition_span` para decidir que houve travessia, mas depois acrescentam os índices completos a `whole`. Na emissão, `whole` prevalece sobre os recortes locais. Não é correto afirmar que a substituição cross-fragment usa somente `replacement_span`.

**Julgamento sobre §1.1 das notas:** manter `whole` é uma escolha declarada, mas não atende à exigência atual de preservar o rótulo além do necessário. Não estou reabrindo E5-AUD3-001: aceitar uma coincidência de pares como detecção sensível é distinto de apagar caracteres que o próprio motor identifica como contexto preservado.

**Recomendação:** usar o reconhecimento para atribuir proveniência e projetar a interseção de `replacement_span` com cada fragmento para redigir o valor. Manter marca inteira para subárvore sensível/fallback quando apropriado. Isso conserva a política de examinar todos os pares e evita perda desnecessária do prefixo nos casos mapeáveis.

**Provas:** `test_bytes[prefix_minimal…]`, `[short_assigned…]`, `[short_bearer…]`; `test_required_gate[password: sk-12…]` falha pela ausência de `password: `.

### E5-AUD4-003 — Alta/P1 — `recognition_span` omite contexto em lookaround e deixa credencial de URL inteira passar

**Arquivo/linha:** `api/app/safety/redaction.py:67`, `:245–252`; consumo em `api/app/context_engine/rendering.py:406` e `:421–428`.

**Reprodução nova, contexto preservado:**

```python
title = "https://"
body = "reader:passcode@host.test"
structured = None
```

Nenhum campo isolado é reconhecido. A concatenação `https://reader:passcode@host.test` casa o padrão já existente `url_credentials`. Porém os dois intervalos retornados são `[8,23)`: abrangem `reader:passcode`, sem `://` nem `@`. A fronteira também está em 8, então `crosses(8)` é falso; a projeção inteira identifica apenas um fragmento coberto e descarta a detecção.

Texto efetivamente gravado:

```text
### contexto · stack

## https://

reader:passcode@host.test
```

**Segunda prova da mesma causa:** `title="https://reader:passcode"`, `body="@host.test"`. O lookahead necessário está no segundo fragmento. A credencial também sobrevive, agora no título emitido e propagado para `origin.title` e `manifest.entries[].title`.

**Causa:** `match.start()/match.end()` cobrem só o texto consumido, não o texto consultado por `(?<=://)` e `(?=@)`. O nome `recognition_span` promete proveniência de reconhecimento, mas sua implementação não a representa para esse padrão. Não é segredo desconhecido pelo catálogo: o próprio motor o reconhece quando recebe os fragmentos concatenados. Não é o residual de extensão gulosa nem uma divisão não canônica em três partes.

**Recomendação:** representar no motor único o contexto necessário à detecção, inclusive assertions de lookbehind/lookahead, separado do intervalo de substituição. Para `url_credentials`, a proveniência deve abranger `://` e `@`, preservando-os ao substituir apenas usuário/senha. Não duplicar regex no Context Engine. Adicionar as duas provas de fronteira, especialmente a variante em metadados.

**Provas:** `test_bytes[url_context…]`, `[url_lookahead…]`; `test_required_gate[https://…]` falha pela presença de `reader:passcode` nos bytes.

## 4. Confirmação das duas correções solicitadas

**E5-AUD3-002, reprodução exata:** `title="Bearer"`, `body=" ABCDEFGHIJKLMNOP"` não emite `ABCDEFGHIJKLMNOP` em nenhum byte do artifact. `recognition_span=[0,23)` cruza a fronteira 6; `replacement_span=[7,23)` representa só a credencial. O marcador cross-fragment é emitido. O título também é redigido, por `whole`, conforme finding 002 desta rodada.

**Uso dos intervalos:** `merge_spans`, `redact`, recortes locais da linha `caminho: valor` e recortes de propagação usam `replacement_span`. Pares e projeção integral usam `recognition_span` para determinar a travessia. Correto para o caso bearer, insuficiente para lookaround (003) e para preservar prefixos na emissão cross-fragment (002).

**E5-AUD3-003, reprodução exata:**

```text
Contagem publica: 123456 itens.

password: «redigido»
quantidade: 123456
```

Confirmado nos bytes: número protegido sob password, duas ocorrências públicas legíveis. O `continue` em `rendering.py:492–493` protege as duas portas de `_propagation_values`; não há uma terceira inserção no conjunto fora desse laço. A propagação de strings sensíveis continua funcionando.

**Compatibilidade histórica de `redact()`:** a metodologia diferencial documentada é sólida como teste de regressão: oráculo anterior em cascata, concatenações/sobreposições e corpus repetido após ajustes. Separar o reconhecimento não muda os intervalos usados pela substituição. Não refiz os 7.552 casos nem tratei esse número como prova universal; o corpus completo/semente daquele ensaio não está persistido nas notas. Acrescentei 588 entradas determinísticas contra a implementação histórica obtida diretamente por `git show HEAD:api/app/safety/redaction.py`, com zero divergências UTF-8. As falhas acima são de proveniência/consumo de spans no contexto fragmentado, não diferenças observadas em `redact()`.

## 5. Tabela dos 22 cenários: condição sensível preservada

A tabela real está em `CENARIOS`, `api/tests/test_context_redaction_e5_round4.py`; as notas descrevem sua metodologia. Revisei cada entrada, não apenas a lista de valores:

| # | Cenário | Condição que torna o valor sensível |
| --- | --- | --- |
| 1 | Anthropic no título | Token completo casa `anthropic_key`. |
| 2 | Anthropic no corpo | Token completo casa `anthropic_key`. |
| 3 | AWS no corpo | Chave completa, prefixo e comprimento válidos. |
| 4 | GitHub token | Token completo `ghp_…`. |
| 5 | GitHub PAT | Token completo `github_pat_…`. |
| 6 | Password escalar | Valor sob chave sensível password. |
| 7 | Lista sob password | Sensibilidade herdada pela folha da lista. |
| 8 | Objeto sob password | Sensibilidade herdada pela folha current. |
| 9 | Profundidade 4 | Password ancestral permanece na árvore. |
| 10 | CamelCase aninhado | dbPassword marca descendentes de listas/objetos. |
| 11 | auth.apiKey opaco | apiKey marca o valor opaco. |
| 12 | Cópia sem rótulo | A mesma entrada mantém password como evidência. |
| 13 | Cópia no corpo | Password original permanece em structured. |
| 14 | Cópia no título | Password original permanece em structured. |
| 15 | Split mínimo | body + folha recompõem token OpenAI reconhecível. |
| 16 | Split com folha interposta | A folha de pontuação não remove o par relevante. |
| 17 | Split com password extra | O par relevante continua presente e há segredo independente. |
| 18 | Prefixo bearer | Título Bearer + corpo com espaço recompõem atribuição reconhecível. |
| 19 | Prefixo assigned_secret | Título password: + corpo recompõem atribuição reconhecível. |
| 20 | PEM partido | Cabeçalho, conteúdo e fechamento estão nas duas partes. |
| 21 | Chave é token | A própria chave contém token completo reconhecível. |
| 22 | Chave com separador | A chave contém `password: hunter2`, atribuição completa. |

**As 22 condições são genuínas.** A execução isolada é uma boa proteção contra um cenário esconder o outro; a execução conjunta verifica presença de 22 blocos e hash dos bytes. Entretanto a busca usa um `segredo` por cenário: em alguns splits verifica apenas uma parte, não todas. A tabela não cobre toda combinação de posição/padrão e não contém o caso URL. Isso limita a conclusão de exaustividade, sem invalidar os 22 testes. Os testes de regressão complementares verificam as duas metades em vários exemplos históricos.

**Três cenários adicionais fora da tabela:**

1. URL com `://` separado: **falha**, finding 003; confirmado também com `@` separado.
2. `vault.API-KEY[0].v="Q9-private-X"`, copiado para o corpo, com folha pública `visivel`: segredo ausente, folha pública preservada.
3. `title="authorization="`, `body=" QWERTY12345"`: segredo ausente dos bytes, marcador cross-fragment presente. O título inteiro sai, com a limitação de granularidade já registrada.

Não foi possível confirmar que todos os cenários novos passam: o primeiro refuta isso.

## 6. Regressão dos 17 findings e motor único

As regressões nomeadas abaixo passaram na execução completa e nos complementos indicados. A única condição antiga deliberadamente mantida é E5-AUD3-001, aceita como política.

| Finding histórico | Resultado desta rodada |
| --- | --- |
| E5-AUD-001 | Regressão de identidade NFC/NFD e complemento forte de mesmo blob/mesmo commit. |
| E5-AUD-002 | Token no título protegido em texto, origin, manifest e disco. |
| E5-AUD-003 | Password escalar permanece redigido. |
| E5-AUD-004 | Split mínimo histórico permanece redigido nas duas partes. |
| E5-AUD-005 | Desempate por microssegundos preservado. |
| E5-AUD-006 | Normalização de título consistente; complemento altera a mesma entrada. |
| E5-AUD-007 | Contagem confrontada com texto decodificado dos bytes finais. |
| E5-AUD-008 | Literal exato vence glob para candidato inexistente. |
| E5-AUD-009 | Artifact corrompido é reconstruído. |
| E5-AUD2-001 | Casos históricos com folha interposta/password adicional protegidos. A generalização para prefixo+split tem a exceção do finding 001. |
| E5-AUD2-002 | Cópias de strings sob chave sensível protegidas, inclusive título. |
| E5-AUD2-003 | Chaves com token/separador reservado protegidas. |
| E5-AUD2-004 | Sensibilidade atravessa listas e objetos. |
| E5-AUD2-005 | Medição final com REDACT sem NORMALIZE permanece coberta. |
| E5-AUD3-001 | Falso positivo reproduzido, mantido como decisão V1 aceita; não bloqueia. |
| E5-AUD3-002 | Reprodução exata fechada; fechamento geral da proveniência não demonstrado, finding 003. |
| E5-AUD3-003 | Reprodução exata fechada, números públicos preservados. |

Confirmado por leitura, busca e travas de arquitetura: uma lista `_SENSITIVE_KEY_WORDS`, um catálogo `_PATTERNS`, `is_sensitive_key` e `assigned_secret` derivados da mesma fonte. `redact()` chama `detect_secret_spans`. Nenhum regex de segredo duplicado em `context_engine/`; o casamento de source_refs pertence a outra gramática e não é um segundo motor de redação. A propagação usa comparação/substituição literal de valores já marcados.

As regressões nomeadas sustentam ausência de retorno dos exemplos históricos. Não equivalem a prova de fechamento de classe: os findings 001 e 003 mostram que a proteção posicional ainda tem exceções concretas.

Identificação dos arquivos centrais auditados (SHA-256 ao concluir): `rendering.py` = `40bdc1ee38c6fe16d150717bbe4755b449f70f6dfc9008bfc501a0c78ab686d0`; `safety/redaction.py` = `916d2d918b1dfa49022490a3a0ae69ad5a22acb2cfc8458646fc005763307889`.

## 7. Veredito

**NÃO GREEN — não libera commit.** Bloqueiam **E5-AUD4-001 (Alta/P1, cauda sensível emitida no Foco 2)**, **E5-AUD4-002 (Média/P2, prefixo preservável apagado, contrariando o Foco 2)** e **E5-AUD4-003 (Alta/P1, credencial de URL reconhecida na concatenação e emitida integralmente)**.

E5-AUD3-002 e E5-AUD3-003 estão fechados nos casos exatos. A política aceita de E5-AUD3-001 permanece respeitada. As únicas alterações desta auditoria no repositório são este relatório e uma entrada breve no AGENT_LOG; nenhum código foi corrigido e nenhum commit foi criado.
