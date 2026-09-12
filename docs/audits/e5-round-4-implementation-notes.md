# E5 rodada 4 — notas de implementação: `E5-AUD3-002`, `E5-AUD3-003` e a varredura de bytes

Data: 2026-09-11. Autor: Claude Opus 5 (effort high). Escopo: correção dos dois defeitos
de código que a [rodada 3](e5-round-3.md) encontrou, mais o marcador de explicabilidade
que [03 §4](../architecture/03-context-architecture.md) já documenta como planejado.
**Nada commitado** — segue para a quarta rodada do Codex.

`E5-AUD3-001` **não foi corrigido**, por decisão: é o trade-off que a V1 aceita
deliberadamente (falso positivo de redação em troca de nunca deixar passar credencial
real), formalizado em [03 §4](../architecture/03-context-architecture.md) e commitado em
`9793c1a`. Nenhuma restrição de pares e nenhuma heurística de confiança foi adicionada.

## 1. `E5-AUD3-002` — dois intervalos em `SecretSpan`

**O defeito.** `SecretSpan` tinha um intervalo só, e ele era o *intervalo de
substituição*. Para os padrões de prefixo preservado (`bearer`, `assigned_secret`) esse
intervalo começa **depois** do rótulo — `Bearer ` e `password: ` são preservados no texto,
como `redact()` sempre fez. Com `title="Bearer"` e `body=" ABCDEFGHIJKLMNOP"`, a leitura
de par reconhece `Bearer ABCDEFGHIJKLMNOP`, mas o intervalo de substituição é `[7,23)` —
inteiramente dentro do `body`, depois da fronteira em 6. A condição
`start < boundary < end` dava **falso**, a detecção era descartada, e a credencial ia
inteira para os bytes do artifact.

**A correção.** `SecretSpan` passou a ter dois intervalos, e o tipo agora **impede** a
confusão que causou o vazamento — não há mais um `.start` genérico para alguém usar sem
pensar em qual dos dois quis dizer:

| Campo | O que é | Quem usa |
| --- | --- | --- |
| `recognition_span` | o match **completo**, com o contexto que prova a detecção (`Bearer ABCDEFGHIJKLMNOP`) | análise de fronteira entre fragmentos: `(d)` pares ordenados e `(e)` projeção canônica |
| `replacement_span` | só o que vira `«redigido»` (`ABCDEFGHIJKLMNOP`) | `merge_spans` → `redact()`, recortes locais, e o valor que entra na propagação |

Para todos os padrões sem prefixo preservado os dois intervalos são **idênticos** —
`test_intervalos_coincidem_em_padrao_sem_prefixo_preservado` trava isso.

**`redact()` inalterado.** A exigência era manter byte a byte o texto que o backend já
emite. Reexecutei o mesmo ensaio diferencial das rodadas anteriores contra a reconstrução
do `redact` histórico (cascata de `.sub`), agora com `Bearer`/`ABCDEFGHIJKLMNOP` no corpus:
**7.552 casos, 0 divergências**. `test_redact_preserva_o_prefixo_como_sempre` fixa o
comportamento visível (`Bearer «redigido»`, `password: «redigido»`).

### 1.1 Decisão: `whole` continua, não virou recorte cirúrgico

Quando o reconhecimento atravessa a fronteira, os **dois** fragmentos saem por inteiro —
como já era. Considerei mapear o `replacement_span` de volta e redigir só os caracteres
consumidos em cada lado (no caso `Bearer`, isso preservaria o título e apagaria só o valor
no corpo). **Não fiz**, por três razões:

1. o prompt pediu para corrigir a *detecção* da travessia, não a granularidade da marca;
2. seria mudar, sem auditoria, um comportamento que passou pelas rodadas 1–3;
3. over-redaction aqui é exatamente o trade-off que a V1 já aceitou e documentou.

Fica registrado como refinamento possível para uma rodada futura, se a perda de qualidade
de contexto se mostrar cara na prática.

### 1.2 Onde `recognition_span` **não** é usado, e por quê

Na leitura `(c)` — a linha `caminho: valor` que o renderizador reconstrói — a decisão de
marcar o **rótulo** continua sendo pelo `replacement_span`. Usar `recognition_span` ali
marcaria o próprio `password` de `password: hunter2` como segredo, e a linha viraria
`<chave protegida>: «redigido»` em vez de `password: «redigido»`: o nome do campo é o
*contexto que prova* a detecção, não parte do segredo. A distinção entre "contexto que
prova" e "valor a esconder" é justamente o que o finding pediu para separar — aplicá-la
sem olhar o papel de cada leitura teria criado over-redaction nova.

## 2. `E5-AUD3-003` — `propagatable` nas duas portas

**O defeito.** `_propagation_values` tinha dois caminhos de entrada no conjunto global.
O primeiro (fragmento inteiro) consultava `fragment.propagatable`; o segundo (recorte de
span local) não. Um número sob chave sensível gera span pela linha `password: 123456`,
entrava pela segunda porta, e apagava toda contagem pública igual do documento.

**A correção.** O gate virou um `continue` no topo do laço, antes de qualquer porta:

```python
for index, fragment in enumerate(fragments):
    if not fragment.propagatable:
        continue
```

Nenhum escalar genérico entra na propagação por **nenhum** caminho. Ele continua redigido
**localmente** sob a chave sensível (camada estrutural) — o que muda é só deixar de tornar
suspeito todo `123456` que apareça em outro lugar.

O recorte passou a sair explicitamente de `replacement_span`: propagar
`recognition_span` poria a string `password: hunter2` inteira no conjunto, e um `password`
legítimo de outro campo viraria `«redigido»`.

## 3. Marcador de explicabilidade

`CROSS_FRAGMENT_REDACTION = "cross_fragment_secret_redaction"` é acrescentado a
`blocks[].transformations` quando **alguma** marca daquele bloco veio de detecção que
atravessa fronteira — `(d)` ou `(e)`.

Decisões:

- **Não muda decisão de redação nenhuma.** É uma flag que sobe do `_mark_fragments` junto
  com as marcas, sem realimentar nada;
- **Não expõe valor, posição nem qual fragmento** — só o fato, como [03 §4] especifica;
- **`transformations` passou a sair de `render_block_text`**, não do chamador.
  `RenderedBlock` ganhou o campo e `selection.py` deixou de montar a lista: só o
  renderizador sabe se o mecanismo foi acionado, e ter duas fontes para a mesma lista era
  a forma exata de elas divergirem;
- **Fail-closed não recebe o marcador.** Um bloco redigido por não ter sido possível
  analisá-lo não foi redigido por travessia de fronteira; misturar os dois tornaria o
  marcador inútil para a pergunta que ele existe para responder.

`RENDERER_VERSION` foi para `e5.block.v4` — `transformations` faz parte do payload e entra
no `rendered_context_hash`.

## 4. A varredura de bytes, e um erro meu que ela pegou

O gate pedia varrer o artifact físico contra **todo segredo sintético conhecido** de todas
as rodadas. A primeira versão que escrevi fazia isso literalmente: uma lista de valores,
cada um posto num campo qualquer, e a asserção de que nenhum aparece nos bytes.

**Ela falhou — e estava errada.** `hunter2`, `7890ABCDEF`, `valor_secreto_antigo`,
`valorSuperSecreto123` e outros **não são reconhecíveis por padrão nenhum isoladamente**:

```text
Reconhecível por si só (sem contexto)?
  SIM  sk-ant-AAAA…        NAO  hunter2              NAO  sk-123456
  SIM  AKIAIOSFODNN7EXAMPLE NAO  7890ABCDEF          NAO  ABCDEFGHIJKLMNOP
  SIM  ghp_ABCDEF…          NAO  valor_secreto_antigo NAO  MIIEvQIBADANBg
```

São segredo porque estão **sob chave sensível**, porque são **cópia** de um valor que
está, ou porque **completam um padrão** ao serem concatenados a outro fragmento. Ao
colocá-los num campo `credencial` (que não é nome sensível) ou `historico.anterior` (fora
de qualquer subárvore sensível), eu removi a condição que os tornava segredo — e o
pipeline, corretamente, não os redigiu.

Reescrevi a varredura como **tabela de cenários**: cada fixture adversarial das três
rodadas com a condição que o torna segredo preservada (`_Cenario`, 22 entradas). Dois
testes a consomem:

- `test_varredura_de_bytes_por_cenario` — cada cenário **sozinho** no artifact. É o caso
  mais exigente: num arquivo com muitos segredos, a over-redaction de um esconderia o
  vazamento de outro atrás de `«redigido»` alheio;
- `test_varredura_de_bytes_todos_os_cenarios_no_mesmo_artifact` — os 22 como entradas
  separadas no **mesmo** arquivo físico, varrido de uma vez, com `sha256` conferido contra
  o `rendered_context_hash` registrado.

Registro isso porque é o tipo de erro que faz um gate passar sem provar nada: se eu
tivesse escrito a varredura "certa por fora" mas com os valores fora de contexto, ela
passaria hoje e continuaria passando se o pipeline regredisse.

## 5. Verificação

- **Suíte completa: 1119 passed / 6 skipped** (era 1080). `ruff check` ·
  `ruff format --check` · `mypy` limpos (75 arquivos).
- **`redact()` byte a byte idêntico**: 7.552 casos, 0 divergências contra a reconstrução
  do `redact` histórico, com os fixtures de prefixo preservado no corpus.
- **Ambos os defeitos reproduzidos contra a lógica anterior**, simulada fora do working
  tree: `replacement_span` dá `travessia: False` onde `recognition_span` dá `True`; e o
  recorte `'123456'` (6 chars) entrava na propagação sem o gate.
- **Suítes E5**: rodada 3 com 26 testes (2 novos sobre os dois intervalos), rodada 4 com
  35 (13 diretos + 22 cenários parametrizados), router com 45. Nenhuma regressão.

## 6. Limites residuais — sem mudança desde a rodada 3

Os três limites declarados em
[e5-round-3-implementation-notes.md §4](e5-round-3-implementation-notes.md) continuam
valendo, e a rodada 3 já os avaliou:

1. segredo partido entre **três ou mais** fragmentos em ordem não canônica;
2. `_is_greedy_extension` preserva a cauda quando o primeiro fragmento já contém um token
   completo;
3. a detecção continua sendo a de `safety/redaction.py` — as camadas garantem que o que
   ele **reconhece** não escape por estrutura, posição ou cópia, não que ele reconheça
   tudo.

`E5-AUD3-001` (falso positivo por par arbitrário) passa de "finding" a **política
declarada**, com o parágrafo normativo já commitado.

## 7. O que a rodada 4 do Codex precisa confirmar

1. `recognition_span`/`replacement_span` fecham a classe de detecção contextual — não só
   os dois padrões de prefixo preservado nomeados;
2. o gate de `propagatable` não deixou nenhuma terceira porta de entrada na propagação;
3. a tabela de cenários da varredura cobre de fato todos os fixtures das três rodadas, com
   as condições certas;
4. a decisão de §1.1 (manter `whole` em vez de recorte cirúrgico) é aceitável.
