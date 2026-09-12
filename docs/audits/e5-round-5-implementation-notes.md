# E5 rodada 5 — notas de implementação: `E5-AUD4-001`, `E5-AUD4-002`, `E5-AUD4-003`

Data: 2026-09-12. Autor: Claude Opus 5 (effort high). Escopo: os três findings da
[rodada 4](e5-round-4.md), mais a correção de dois links relativos quebrados no
`AGENT_LOG.md`. **Nada commitado** — segue para a quinta rodada do Codex.

Os três findings são a mesma pergunta feita de três ângulos: *quem decide o quê, entre
`recognition_span` e `replacement_span`?* A rodada 4 mostrou que a resposta estava errada
nas três pontas — o reconhecimento não cobria tudo que a detecção leu (003), a travessia
era descartada por uma heurística que não conseguia distinguir os casos (001), e a
substituição usava o fragmento inteiro em vez do intervalo (002).

A norma que esta implementação satisfaz foi escrita **antes** dela, em
[03 §4](../architecture/03-context-architecture.md), e commitada em `8f59cf7`.

## 1. `E5-AUD4-003` — lookaround entra no motor único

Comecei por aqui: 001 e 002 são decisões de consumo, e não fazia sentido acertá-las sobre
um `recognition_span` que mentia.

**O defeito.** `recognition_span` vinha de `match.start()`/`match.end()`, que cobrem só o
texto **consumido**. `url_credentials` é `(?<=://)[^/\s:@]+:[^/\s:@]+(?=@)`: o `://` e o
`@` que provam que aquilo é credencial são lidos por asserção de largura zero e ficam de
fora do match. Com `title="https://"` e `body="reader:passcode@host.test"`, os dois
intervalos eram `[8,23)` — idênticos, inteiramente dentro do corpo, fronteira em 8 não
cruzada, detecção descartada, credencial crua nos bytes.

**A correção.** `_PATTERNS` deixou de ser uma tupla de `(nome, regex)` e virou uma tupla
de `_Pattern`, onde cada padrão declara **tudo** que o motor precisa saber sobre ele:

```python
_Pattern(
    "url_credentials",
    re.compile(r"(?<=://)[^/\s:@]+:[^/\s:@]+(?=@)"),
    lookbehind=re.compile(r"://\Z"),
    lookahead=re.compile(r"@"),
)
```

`_recognition_window` amplia o intervalo com o que essas duas asserções exigem; o
`match` continua sendo a única autoridade sobre o que vira `«redigido»`. Resultado:
`recognition_span` = `://reader:passcode@`, `replacement_span` = `reader:passcode`.

**A trava, que é a parte que importa.** Declarar o contexto não resolve nada se o próximo
padrão com lookaround esquecer de declarar. `_validate_patterns` roda **no import** e
recusa qualquer padrão cujo fonte contenha `(?<=` ou `(?=` sem o campo correspondente. Não
é um teste — é `ImportError` no boot, porque quem acrescenta um padrão não é quem lê a
docstring. A suíte afirma a mesma regra em
`test_todo_lookaround_positivo_do_catalogo_e_declarado`, para que a falha também apareça
vermelha.

### 1.1 Revisão padrão a padrão — o que a busca por lookaround encontrou

| Padrão | Asserções de largura zero | Precisa declarar? |
| --- | --- | --- |
| `pem_block` | nenhuma | não |
| `url_credentials` | `(?<=://)`, `(?=@)` | **sim** — é o finding |
| `bearer` | `\b` | não (grupo 1 já é consumido) |
| `anthropic_key` | `\b` | não |
| `openai_key` | `\b` | não |
| `github_token` | `\b` | não |
| `aws_access_key` | `\b` nas duas pontas | não |
| `assigned_secret` | `\b` | não (grupo 1 já é consumido) |

**`\b` foi deliberadamente deixado de fora**, e a decisão não é por omissão. `\b` não exige
que *exista* texto adicional: exige uma **propriedade** do caractere vizinho (ser não-palavra,
ou não existir). Incluir esse caractere em `recognition_span` inventaria travessia onde não
há — `title="abc "`, `body="sk-ANTHROPICKEY…"` passaria a "atravessar" a fronteira por causa
de um espaço, quando o segredo está inteiro no corpo e a leitura isolada já o pega. O
validador de import trata `(?<=`/`(?=` positivos; lookaround **negativo** (`(?!`, `(?<!`)
também fica de fora, e por outra razão: ele exige ausência, não há texto lido para incluir.

## 2. `E5-AUD4-001` — `_is_greedy_extension` removida, sem substituta

A função e o `own_starts` que a alimentava saíram. Nenhuma condição nova tomou o lugar —
`test_e5_aud4_001_a_heuristica_nao_existe_mais` verifica isso **no fonte**, não só no
comportamento, porque um teste comportamental passaria se a heurística voltasse com outro
nome.

**Por que o sinal não servia.** Ele perguntava "esta posição já era início de detecção no
fragmento isolado?". Em `title="password: sk-123"` / `body="456789ABC"` a resposta é sim —
`sk-123` tem exatamente os seis caracteres do piso de `assigned_secret` — e a travessia para
a cauda era descartada. Mas a resposta é sim tanto quando o vizinho é conteúdo legítimo
engolido por gulodice de alfabeto quanto quando ele é a continuação genuína do segredo. Um
sinal que dá a mesma resposta nos dois casos não os distingue, e distingui-los exigiria
heurística de confiança ou comprimento — que a V1 recusa.

**A consequência, aceita e agora afirmada em teste.** O que era
`test_segredo_no_fim_do_corpo_nao_apaga_o_titulo` (rodada 3) virou
`test_segredo_no_fim_do_corpo_consome_o_titulo_vizinho`: o título vizinho **é** redigido.
Registro a inversão em vez de apagar o teste, para que a mudança de política fique
rastreável na suíte e não só no relatório.

## 3. `E5-AUD4-002` — emissão por interseção

`whole` deixou de ser o que a travessia produz. `_clip(span, start, end)` devolve a
interseção de `replacement_span` com a janela de cada fragmento, em coordenadas locais
dela, ou `None` quando a substituição não alcança aquela janela.

A propriedade que isso estabelece, e que acho a forma mais curta de auditar a correção:
**o resultado fragmento a fragmento é o que `redact()` produziria sobre o texto colado,
recortado de volta na fronteira.** Não é uma aproximação nem uma política nova — é a mesma
substituição, projetada.

`whole` continua existindo, para as duas coisas que ele de fato descreve: folha sob
subárvore sensível (camada 1) e chave que carrega o segredo (leitura (c)). Era a
recomendação do próprio auditor.

Nos três casos das reproduções:

| Entrada | Antes | Agora |
| --- | --- | --- |
| `password: sk-12` \| `3456789ABC` | os dois fragmentos inteiros | `password: «redigido»` \| `«redigido»` |
| `https://` \| `reader:passcode@host.test` | credencial crua | `https://` \| `«redigido»@host.test` |
| `Bearer` \| ` ABCDEFGHIJKLMNOP` | título apagado | `Bearer` \| ` «redigido»` |

## 4. Verificação

- **Suíte completa: 1163 passed / 6 skipped** (era 1119/6 na rodada 4; 1169 coletados). `ruff check` · `ruff format --check` ·
  `mypy` limpos.
- **GATE 1 — `redact()` inalterado.** `test_gate_1_redact_identico_a_cascata_historica`
  compara `redact()` contra a cascata histórica de `re.sub` sobre **5.800+ casos**
  gerados de um pool de 28 peças (segredo completo, metade de segredo, âncora de
  lookaround isolada, rótulo isolado, texto legítimo): **0 divergências**. Duas escolhas
  deliberadas: a cascata está escrita **no teste, com os fontes dos regex literais**, não
  importados de `_PATTERNS` — então o ensaio também trava o catálogo; e ele passou a ser
  um teste **commitado**, não um script de scratchpad como nas rodadas 3 e 4, que foi uma
  crítica justa do auditor (o corpus daquelas rodadas não estava persistido).
- **GATE 2 — varredura de bytes.** 22 cenários das rodadas 1–4 (importados de
  `test_context_redaction_e5_round4.py`, não copiados) + 7 novos = **29**, cada um
  sozinho no artifact e todos juntos num único arquivo físico, aberto em modo binário,
  com `sha256` conferido contra o `rendered_context_hash` registrado.
- **Regressão por padrão.** `PADROES` tem uma entrada por padrão do catálogo, com o corte
  que põe a fronteira exatamente no ponto do seu contexto de lookaround.
  `test_a_tabela_cobre_o_catalogo_inteiro` compara a tabela com `_PATTERNS` e falha se um
  padrão novo entrar sem caso — "revisei todos os padrões" deixa de valer só para hoje.
- **Suítes E5:** rodada 3 com 26, rodada 4 com 35, rodada 5 com 44, router com 45.

## 5. Limites residuais — o que esta rodada piorou, e por quê

Três consequências que considero honesto declarar, todas na direção de redigir demais:

1. **A propagação global ficou mais agressiva.** Os recortes de `_clip` entram em
   `_propagation_values` como qualquer span local. No caso invertido do finding 002, o
   título `"titulo comum"` perde `"titulo"` por gulodice, e esses 6 caracteres (o piso de
   `_MIN_PROPAGATION_LENGTH`) passam a redigir todo `"titulo"` do documento. Antes, com
   `whole`, o que propagava era o fragmento inteiro — string maior, colisão menos
   provável. Considerei excluir recortes cross-fragment da propagação e **não fiz**: numa
   divisão genuína, o recorte é metade de um segredo real, e não propagá-lo abriria
   exatamente a classe `E5-AUD2-002`.
2. **Rótulo de folha vizinho pode ser consumido.** Em `body="password: sk-123"` com
   `structured={"resto": "456789ABC"}`, o par `(body, label)` faz o padrão consumir
   `resto`, e a linha sai como `<chave protegida>`. Não é apagamento "além do
   necessário" no sentido do finding — nada fora da interseção é apagado —, mas é perda
   de contexto visível, e a mesma remoção de 001 a causou.
3. **`RENDERER_VERSION` foi para `e5.block.v5`.** O texto emitido mudou em três classes
   de entrada, então todo `rendered_context_hash` anterior deixa de reproduzir.

Os três limites da [rodada 3](e5-round-3-implementation-notes.md) seguem: segredo partido
entre três ou mais fragmentos em ordem não canônica continua sendo coberto só pela
projeção canônica; e a detecção continua sendo a de `safety/redaction.py` — as camadas
garantem que o que ele **reconhece** não escape por estrutura, posição ou cópia, não que
ele reconheça tudo. O limite 2 daquela lista (`_is_greedy_extension`) deixou de existir.

`E5-AUD3-001` permanece política declarada, não corrigida.

## 6. Fora do escopo dos findings

Os dois links relativos quebrados em `AGENT_LOG.md` (`../api/app/context_engine/…` →
`api/app/context_engine/…`) foram corrigidos. Eles apontavam para fora do repositório —
`AGENT_LOG.md` está na raiz. Os alvos continuam untracked; o link só resolve quando o
código da E5 for commitado.

## 7. O que a rodada 5 do Codex precisa confirmar

1. a propriedade do §3 — emissão fragmento a fragmento idêntica a `redact()` do colado —
   vale para sobreposição de spans de padrões diferentes, não só para um span por vez;
2. `_recognition_window` não pode **encolher** um intervalo nem em entrada patológica
   (`min`/`max` contra o intervalo de substituição existem para isso; vale conferir);
3. a trava de import de `_validate_patterns` não tem furo — por exemplo um lookaround
   escrito com flags inline ou dentro de classe de caracteres que o regex do validador
   não veja;
4. a amplificação da propagação descrita em §5.1 é aceitável, ou merece finding próprio;
5. o corpus do GATE 1 é representativo o bastante para sustentar "byte a byte idêntico".
