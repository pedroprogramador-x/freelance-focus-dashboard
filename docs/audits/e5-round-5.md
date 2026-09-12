# Auditoria E5 — rodada 5: interseção, lookaround e propagação

Data: 2026-09-12. Auditor: Codex. HEAD: `46f84a5404a0c6e7e8fa95a3409f047c54e59c58`, com alterações locais abaixo. Nenhuma correção de código ou commit realizado.

**Veredito técnico: GREEN para a política V1 documentada.** E5-AUD4-001/002/003 fechados nas reproduções e nos gates executados. A amplificação de falso positivo foi comprovada e registrada como **E5-AUD5-001, Média/P2, risco não bloqueante**. Recomendo aceitar esse risco e manter a propagação de recortes; excluir esses recortes perde proteção de cópias de segredos genuinamente partidos. Esta é a recomendação técnica solicitada, não uma nova decisão de produto atribuída a Pedro.

## 1. Contexto e estado auditado

Lidos o relatório da rodada 4, as notas da implementação da rodada 5 (incluindo §5 e §7), a arquitetura 03 §4, o AGENT_LOG e as entradas E5. Lidos integralmente `safety/redaction.py` e `context_engine/rendering.py`, além da nova suíte da rodada 5; examinadas as regressões anteriores e as travas de arquitetura. O conteúdo untracked foi considerado, não apenas o diff.

Primeiro comando de estado, `git status --short`, saída completa:

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
?? api/tests/test_context_redaction_e5_round5.py
?? api/tests/test_context_router_e5.py
?? docs/audits/e5-round-5-implementation-notes.md
```

`git diff --stat`, saída completa:

```text
 AGENT_LOG.md                       |  49 +++-
 api/app/context_engine/__init__.py | 105 +++++++++
 api/app/safety/redaction.py        | 457 +++++++++++++++++++++++++++++++++++--
 api/tests/context_helpers.py       |  51 ++++-
 api/tests/test_architecture.py     |  55 +++++
 5 files changed, 696 insertions(+), 21 deletions(-)
```

Git emitiu avisos sobre ignore global e conversão futura LF/CRLF; comandos concluíram. Correção de proveniência: `redaction.py` está **rastreado e modificado**, enquanto `rendering.py` e a nova suíte estão **untracked**. O diferencial está persistido no workspace, mas ainda não commitado, apesar dessa expressão nas notas/pedido.

## 2. Execução real

Windows, Python 3.12.14, pytest 8.4.2, ambiente existente `%TEMP%/e4-aud6-venv`. Bancos, artifacts e repositórios sintéticos dos fixtures ficam no TEMP. Commits dos fixtures não são commits do projeto auditado. Execução com `PYTHONDONTWRITEBYTECODE=1` e sem cache pytest no repositório.

| Verificação | Resultado executado |
| --- | --- |
| Suíte completa, `python -m pytest -p no:cacheprovider -o addopts='' -q`, em `api/` | **1163 passed, 6 skipped**, 436,85 s; nenhuma falha. |
| Provas independentes desta auditoria | **15 passed**, 19,23 s. |
| `ruff check --no-cache` | Aprovado. |
| `ruff format --check --no-cache` | 78 arquivos já formatados. |
| `mypy`, cache externo | Aprovado, 76 arquivos. |
| Diferencial independente com código histórico real do HEAD | **5.564 strings únicas**, zero divergências UTF-8. |
| Interseção de spans, todos os cortes de quatro strings com padrões combinados | **294 janelas**, cobertura exata dos caracteres substituídos. |

A suíte completa foi executada com elevação aprovada pelo mecanismo automático, para evitar os impedimentos de acesso UNC/Vite documentados na rodada 4. Não se trata de agregar resultados de duas execuções completas.

As provas independentes usam os fixtures reais: criação de entrada, seleção, congelamento do manifest, leitura binária do artifact físico. Em cada artifact conferem SHA-256 contra `rendered_context_hash`, texto relido e metadados. Testes que afirmam a presença do falso positivo são evidências do risco, não atestados de ausência de over-redaction.

Script e saída preservados fora do repositório:

```text
C:/Users/pedro/.codex/visualizations/2026/09/09/01a086e1-754d-77c1-9a77-4af609dd6d66/e5-round5/test_audit.py
C:/Users/pedro/.codex/visualizations/2026/09/09/01a086e1-754d-77c1-9a77-4af609dd6d66/e5-round5/results.txt
```

Reprodução, de `api/`, com as dependências do backend instaladas:

```powershell
$env:PYTHONPATH = (Get-Location).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
python -m pytest -c pyproject.toml -p tests.conftest -p no:cacheprovider 'C:/Users/pedro/.codex/visualizations/2026/09/09/01a086e1-754d-77c1-9a77-4af609dd6d66/e5-round5/test_audit.py' -s -q -o addopts=''
```

Durante a montagem das provas, corrigi somente asserts do script externo: acesso ao título dentro de `origin`, contagem que confundia `texto` com o cabeçalho `contexto` e espaço final removido pela normalização. A execução final acima está limpa; essas falhas de instrumentação não foram classificadas como defeitos do produto.

## 3. E5-AUD5-001 — Média/P2 — Recorte público de seis caracteres apaga cópias distantes

**Status:** risco confirmado, não bloqueante sob a prioridade V1; recomendação explícita de aceitação. Amplia o alcance do falso positivo já declarado, sem revelar nova classe de vazamento.

**Arquivo/linha:** `api/app/context_engine/rendering.py:418` e `:441` acrescentam recortes; `:514–517` os promovem à propagação; `:548–550` substituem todas as ocorrências literais. O conjunto é construído dentro de `render_block_text`, em `:607`.

**Reprodução executada:**

```python
title = "titulo comum"
body = "sk-ant-ABCDEFGH"  # credencial sintética completa reconhecível
structured = {"!nota": "! o titulo continua publico; "}
```

O par `(body, title)` produz `sk-ant-ABCDEFGHtitulo comum`. A regex gulosa consome `titulo`; `_clip` produz os seis caracteres públicos. Saída real:

```text
### contexto · stack

## «redigido» comum

«redigido»

!nota: ! o «redigido» continua publico;
```

O teste inspeciona o fragmento de valor de `!nota`: **não pertence a `whole` e não tem span em `local`**. Com o mesmo mapa e propagação vazia, esse valor fica integralmente legível. O conjunto real é `('sk-ant-ABCDEFGH', 'titulo')`; habilitá-lo remove a ocorrência pública. Assim, a causa distante é propagação, não um segundo par que teria marcado aquela cópia por conta própria.

Repeti `o titulo continua publico; ` **100 vezes** no valor distante: **100 ocorrências removidas**, ainda sem span local nesse valor. Controle com a palavra `texto`, de cinco caracteres: o recorte no título é redigido, mas **as 100 ocorrências distantes sobrevivem**, pois não alcança o limiar de propagação.

**Avaliação de severidade:** perda real de informação não relacionada, em volume que não se limita ao tamanho da extensão original. É mais amplo que apagar apenas o vizinho. Continua sendo over-redaction do contexto derivado, não exfiltração, alteração do registry autoral ou corrupção do banco. A propagação é **por entrada/bloco**, não por todas as entradas do artifact nem por todo o processo. Também não é uma realimentação ilimitada: o conjunto é calculado antes das substituições, sem gerar novos valores a partir da saída. Classifico como Média/P2 de qualidade do contexto.

**Contraprova executada para a recomendação:**

```python
title = "Resumo"
body = "sk-123456"
structured = {
    "!a": "7890ABCDEF",
    "!copia": "! copia 7890ABCDEF publica sem contexto",
}
```

As duas metades juntas formam `sk-1234567890ABCDEF`, reconhecido pelo catálogo. A cópia distante novamente não tem marca local nem estrutural. A propagação contém `7890ABCDEF`; sem ela, a cópia permanece. Com o pipeline real, o artifact contém `!copia: ! copia «redigido» publica sem contexto` e nenhum byte de `7890ABCDEF`. Excluir os recortes cross-fragment sacrificaria justamente essa proteção de cópia de um valor sensível.

**Recomendação objetiva:** **aceitar a amplificação e manter os recortes na propagação**. Os mesmos bytes e posições podem representar tanto continuação real quanto texto público; a intenção autoral não está nesses dados. Não há distinção semântica geral possível com esses sinais sem acrescentar informação externa ou uma heurística que a V1 recusou. Excluir todos os recortes é determinístico, mas escolhe falsos negativos concretos em troca de qualidade. A prioridade normativa favorece a escolha atual.

Recomendo registrar a amplificação como parte explícita da aceitação, com o exemplo de 100 ocorrências; não descrevê-la apenas como dano adjacente. Não proponho limiar de confiança, restrição a pares vizinhos ou reintrodução de `_is_greedy_extension`. Não alterei a norma nem o código.

## 4. Rótulo de folha vizinha consumido

Reprodução exata executada: `body="password: sk-123"`, `structured={"resto":"456789ABC"}`. Resultado:

```text
password: «redigido»

<chave protegida>: «redigido»
```

O par com o label recompõe `password: sk-123resto`: o detector consome `resto`, embora seja rótulo legítimo. Na emissão (`rendering.py:621–624`), qualquer alteração do label aciona o marcador fixo, como exige a proteção de chaves da rodada 2. `password: ` sobrevive e `456789ABC` desaparece dos bytes.

**Julgamento:** consequência aceitável da política documentada. A informação `resto` perde-se, mas no exemplo a própria folha contém a continuação sensível. Não há evidência aqui de perda desproporcional que justifique uma exceção capaz de reemitir uma chave realmente secreta. Labels parcialmente consumidos também podem acionar ocultação da linha inteira por essa regra de apresentação; a garantia local de `_clip` não deve ser confundida com garantia universal de preservação do bloco final.

## 5. Fechamento de E5-AUD4-001/002/003

As quatro provas independentes passaram sobre arquivos físicos, com hash conferido e pesquisa dos valores proibidos também em `manifest.entries`:

| Título original | Corpo original | Resultado relido |
| --- | --- | --- |
| `password: sk-123` | `456789ABC` | Título `password: «redigido»`; corpo `«redigido»`; nenhuma das duas partes sobrevive. |
| `password: sk-12` | `3456789ABC` | Título `password: «redigido»`; corpo `«redigido»`; prefixo preservado. |
| `https://` | `reader:passcode@host.test` | Título `https://`; corpo `«redigido»@host.test`; credencial ausente dos bytes. |
| `https://reader:passcode` | `@host.test` | Título `https://«redigido»`; corpo `@host.test`; credencial ausente de texto, `origin.title`, `entries[].title` e bytes. |

Confirmado no código: `_is_greedy_extension`/`own_starts` removidos; reconhecimento decide travessia; a substituição projetada usa somente a interseção de `replacement_span`. No motor, `url_credentials` declara lookbehind e lookahead, e `_recognition_window` inclui `://` e `@` no reconhecimento, sem incluí-los na substituição.

O complemento de 294 janelas confrontou a união dos caracteres cobertos pelos spans originais com a união das interseções, inclusive padrões combinados e sobrepostos. Zero divergências. Para todas as 5.564 entradas do corpus também se afirmou `0 <= recognition.start <= replacement.start < replacement.end <= recognition.end <= len(text)`.

**Precisão sobre a propriedade das notas §3:** a equivalência demonstrada é de **cobertura dos caracteres originais substituídos por cada leitura**. Não é igualdade literal entre o bloco final e uma única chamada a `redact()` do colado: um segredo partido pode produzir dois marcadores, há união de leituras diferentes, propagação e regra de label protegido. Nenhum desses efeitos invalida a interseção, mas a frase de igualdade literal é forte demais.

## 6. Trava de import e motor único

Criei cópias temporárias do módulo real, acrescentei um `_Pattern` inválido imediatamente antes da chamada incondicional `_validate_patterns(_PATTERNS)` e importei cada cópia em **subprocess Python novo**:

| Regex sem metadado | Resultado do boot |
| --- | --- |
| `(?<=prefix)abcdef` | Falha, `ValueError`, lookbehind sem declaração. |
| `abcdef(?=suffix)` | Falha, `ValueError`, lookahead sem declaração. |
| `(?i:(?<=prefix)abcdef)` | Mesma rejeição. |
| `(?i:abcdef(?=suffix))` | Mesma rejeição. |

Logo a trava em `safety/redaction.py:131–153` é real e não depende de executar pytest. O tipo concreto é **ValueError durante o import**, equivalente a falha de boot para o requisito, não literalmente `ImportError` como dizem as notas.

Limite confirmado: é uma busca lexical conservadora, não um parser semântico de regex. `[(?=]abcdef` contém os caracteres como literais numa classe e também é recusado, embora não tenha lookahead. Isso é rejeição excessiva de um padrão hipotético de desenvolvedor, não furo no catálogo atual. A trava exige presença do metadado; não prova que qualquer metadado futuro arbitrariamente errado descreva corretamente o contexto. A tabela de todos os oito padrões, seus intervalos e seus cortes complementa essa obrigação de revisão.

Motor único confirmado por código e testes: uma fonte de nomes sensíveis, um catálogo de oito padrões, `redact()` delegando ao detector central. Context Engine usa APIs de safety e substituição literal dos valores marcados; não contém regex duplicada de detecção de segredos. O limiar de seis caracteres e os tetos de 256 fragmentos/32 níveis permanecem; reduzem custo/colisões triviais, sem eliminar o risco do finding 001.

## 7. Compatibilidade histórica e regressões

O diferencial persistido usa regex literais da cascata anterior, sem importar o catálogo atual como oráculo; combina 28 peças em pares e as primeiras 18 em triplas, deduplicando em `set`. É uma metodologia sólida de regressão: determinística no conteúdo, cobre prefixos, metades, contexto de URL, padrões sobrepostos e texto público, e acusa mudança de regex ou substituição.

**Contagem medida: 5.564 strings únicas**, não 5.800+. Comparei cada resultado em bytes UTF-8 contra **dois oráculos**: a cascata persistida no teste e a implementação histórica real obtida por `git show HEAD:api/app/safety/redaction.py`. **Zero divergências** entre os três. O gate persistido também passou na suíte completa. Isto sustenta compatibilidade no corpus e a revisão do mecanismo; não é uma prova matemática para todas as strings Python. A discrepância numérica das notas não altera o resultado.

A varredura corrente executa 29 cenários (22 herdados e sete novos), isoladamente e juntos, em artifact binário com hash. As condições de sensibilidade continuam presentes. Uma busca apenas pelo segredo inteiro não basta quando ele está partido; os testes específicos de cada padrão e as quatro provas acima complementam essa limitação verificando as partes e as superfícies de metadados.

Regressões dos **20 findings anteriores**, na suíte completa e nos complementos descritos:

| Finding | Resultado |
| --- | --- |
| E5-AUD-001 | Identidades NFC/NFD de caminhos continuam distintas nos gates de file map/manifest. |
| E5-AUD-002 | Segredo em título ausente das superfícies finais. |
| E5-AUD-003 | Password escalar redigido. |
| E5-AUD-004 | Split body/structured protegido. |
| E5-AUD-005 | Desempate por microssegundos passa. |
| E5-AUD-006 | Normalização consistente de título passa. |
| E5-AUD-007 | Medição confrontada com bytes finais passa. |
| E5-AUD-008 | Literal exato vence glob para candidato inexistente. |
| E5-AUD-009 | Artifact corrompido reconstruído. |
| E5-AUD2-001 | Folha interposta e password adicional não mascaram o split. |
| E5-AUD2-002 | Cópias sensíveis protegidas; contraprova nova demonstra a necessidade dos recortes. |
| E5-AUD2-003 | Chaves secretas/separador reservado não são reemitidos. |
| E5-AUD2-004 | Listas e objetos herdam sensibilidade. |
| E5-AUD2-005 | Medição sem NORMALIZE continua correta. |
| E5-AUD3-001 | Falso positivo continua possível, política aceita; não é finding corrigido. |
| E5-AUD3-002 | Bearer partido protegido, agora com prefixo preservado. |
| E5-AUD3-003 | Escalares numéricos não propagam globalmente; cópias públicas permanecem. |
| E5-AUD4-001 | Cauda do caso exato removida, heurística retirada. |
| E5-AUD4-002 | Prefixo do caso exato preservado por interseção. |
| E5-AUD4-003 | URL com lookbehind/lookahead separado protegida, inclusive nos títulos. |

Nenhum retorno dos defeitos corrigidos foi observado. Isso não transforma testes finitos em prova de exaustividade. Seguem os limites declarados do catálogo e de três ou mais fragmentos em ordem não canônica. O antigo teste que preservava título vizinho foi invertido conforme a decisão normativa; não se ocultou essa mudança de expectativa como uma correção sem custo.

Os dois links de `AGENT_LOG.md:2238–2239` estão corrigidos para `api/app/context_engine/file_map.py` e `api/app/context_engine/selection.py`, sem `../`. Resolvem no workspace; seus alvos ainda precisam ser incluídos no commit futuro para resolver numa cópia somente do histórico.

Identificação dos módulos auditados, SHA-256:

```text
rendering.py        4fd4ae34e436630e960523b7e5f6e3a5d178c43199938b6719f5d116a529a715
safety/redaction.py 22e31d65bb21ea4d5f4b4de127437901fae0ebd024c3d397dda08e2762b84c54
```

## 8. Veredito

**GREEN — nenhum bloqueador técnico encontrado nesta rodada sob a política V1 documentada.** Os três findings da rodada 4 estão fechados; suíte completa e provas adicionais verdes.

**Recomendação explícita do Foco 1:** aceitar o risco E5-AUD5-001 de over-redaction em cascata, mantendo a propagação dos recortes. A amplificação é maior que a perda apenas adjacente, mas excluir esses recortes abre a perda de proteção de cópias reais demonstrada nesta auditoria. A decisão de produto continua sendo de Pedro; este relatório fornece a recomendação e a reprodução concreta.

Somente este relatório e a entrada breve no AGENT_LOG foram alterados pela auditoria dentro do repositório. Nenhum código corrigido, nenhuma mudança de norma, nenhum commit criado.
