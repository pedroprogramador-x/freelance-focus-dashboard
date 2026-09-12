# E5 — rechecagem final da extensão de metadata

Data: 2026-09-12. Auditor: Codex. HEAD: `46f84a5404a0c6e7e8fa95a3409f047c54e59c58`, com alterações locais. Escopo limitado ao marcador de propagação, bump v6 e regressões críticas; não é uma nova auditoria integral do pipeline. Nenhum código corrigido ou commit criado.

## Contexto e estado inicial

Lidos [rodada 5](e5-round-5.md), [notas da extensão](e5-round-5-metadata-extension-notes.md), arquitetura 03 §4, AGENT_LOG e entradas posteriores ao GREEN. Inspecionados o código real do rastreamento e emissão, o payload em `manifest.py` e os 13 testes novos. `rendering.py` continua untracked; o stat não representa seu conteúdo. Nenhum AGENTS.md encontrado na busca do workspace.

`git status --short`, executado primeiro:

```text
 M AGENT_LOG.md
 M api/app/context_engine/__init__.py
 M api/app/safety/redaction.py
 M api/tests/context_helpers.py
 M api/tests/test_architecture.py
 M docs/architecture/03-context-architecture.md
?? api/app/context_engine/file_map.py
?? api/app/context_engine/manifest.py
?? api/app/context_engine/rendering.py
?? api/app/context_engine/selection.py
?? api/tests/test_context_redaction_e5_round3.py
?? api/tests/test_context_redaction_e5_round4.py
?? api/tests/test_context_redaction_e5_round5.py
?? api/tests/test_context_redaction_e5_round5_metadata.py
?? api/tests/test_context_router_e5.py
?? docs/audits/e5-round-5-implementation-notes.md
?? docs/audits/e5-round-5-metadata-extension-notes.md
?? docs/audits/e5-round-5.md
```

`git diff --stat`, completo:

```text
 AGENT_LOG.md                                 | 184 ++++++++++-
 api/app/context_engine/__init__.py           | 105 ++++++
 api/app/safety/redaction.py                  | 457 ++++++++++++++++++++++++++-
 api/tests/context_helpers.py                 |  51 ++-
 api/tests/test_architecture.py               |  55 ++++
 docs/architecture/03-context-architecture.md |  25 ++
 6 files changed, 856 insertions(+), 21 deletions(-)
```

Git emitiu os avisos de ignore global e futura conversão LF/CRLF, sem impedir a leitura. As notas §6 descrevem o estado anterior sem bump; a entrada posterior do AGENT_LOG registra a mudança para v6. Não usei a nota histórica como descrição do código atual.

## Resultados dos focos

1. **100 ocorrências — confirmado no artifact físico.** `title="titulo comum"`, `body="sk-ant-ABCDEFGH"`, `structured={"!nota": "! " + "o titulo continua publico; " * 100}`. As 100 cópias foram redigidas. O valor distante não tinha span local nem marca inteira. Lista relida: `["normalize", "redact", "cross_fragment_secret_redaction", "propagated_secret_redaction"]`. SHA-256 dos bytes confere com o manifest.
2. **Ausência — confirmada.** Bloco limpo e bloco apenas com `structured={"password":"abcdefghi"}`, sem cópia: lista `["normalize", "redact"]`, sem marcador de propagação. Os testes existentes de detecção direta e fallback também integram a suíte completa.
3. **Coexistência — confirmada.** O cenário de 100 ocorrências contém ambos os marcadores, uma vez cada e na ordem fixa acima. `_redacted_text` é chamado antes de `propagated = propagated or hit` (`rendering.py:641–642`); o curto-circuito de `or` não pula renderização de fragmentos. A emissão acrescenta as constantes em ordem fixa (`:670–673`).
4. **Ataque à metadata — nenhuma informação reconstruível nova encontrada.** Variei valores sintéticos de 6, 64 e 512 caracteres e 1/100 cópias. A metadata permaneceu exatamente `["normalize", "redact", "propagated_secret_redaction"]`; nenhum novo campo de posição, contagem, comprimento ou hash foi introduzido. Nos bytes, texto e títulos do manifest, os valores sintéticos ficaram protegidos.

O código confirma a origem do marcador: literal fixo em `rendering.py:136`; booleano em `:574–580`; acumulação em `:637–642`; emissão em `:673`. Não há caminho que serialize o valor propagado no marcador. Sua presença revela somente o fato de que houve propagação naquele bloco, diagnóstico explicitamente autorizado pela norma; não revela quantas cópias ou o comprimento do segredo. Manter o marcador sem contagem é tecnicamente adequado.

Também escolhi deliberadamente **o próprio literal público** `propagated_secret_redaction` como valor de password. O valor foi redigido no texto e nos títulos, mas a constante continuou aparecendo em transformations, igual às demais provas. Isso demonstra por que testar apenas “segredo é substring de marcador” não prova vazamento: coincidência escolhida com um literal público não é transporte de dado sensível. Não foi tratado como finding.

5. **Versão — confirmada.** `rendering.py:89` define `RENDERER_VERSION = "e5.block.v6"`. Busca nos testes não encontrou `e5.block.v5` como valor esperado. Artifact físico e manifest registram v6.
6. **Texto integral — igualdade UTF-8 confirmada em 31 entradas.** Comparei os 29 cenários herdados, o caso de 100 ocorrências e um bloco limpo com o comportamento anterior à metadata, incluindo título, framing e structured completo. Reconstruí em memória o renderer sem o booleano nem o append do marcador, usando a substituição anterior escrita à parte. Isto é uma reconstrução explícita do comportamento anterior, não execução de um blob histórico de `rendering.py`, que permanece untracked.

O ensaio renderizou seleções completas nas duas versões e releu os dois arquivos físicos. Retirando apenas o marcador novo e substituindo `renderer_version` v6 por v5, os payloads ficam **integralmente iguais**; os hashes físicos originais diferem. Não houve diferença em `blocks[].text`, títulos, ordem, contagem de caracteres ou tokens. A ressalva à frase “só transformations muda” é o próprio campo `renderer_version`, também intencionalmente alterado e hasheado. O bump pode mudar o hash até de um bloco sem propagação; isso é versionamento esperado.

O teste persistido da implementação confronta título e presença de valores, sem comparar todo o framing; a prova independente acima cobre essa lacuna de verificação. O SHA-256 de `safety/redaction.py` permanece idêntico ao registrado na rodada 5.

## Execução e regressões críticas

Ambiente Windows/Python 3.12.14, pytest 8.4.2, `%TEMP%/e4-aud6-venv`. Fixtures e artifacts no TEMP; nenhum commit no projeto. Suíte completa executada com elevação aprovada pelo mecanismo automático, para permitir os fixtures UNC/Vite, sem cache pytest no repositório:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest -p no:cacheprovider -o addopts='' -q
```

Resultado da suíte completa: **1176 passed, 6 skipped**, 532,31 s (8 min 52 s), nenhuma falha. Os 13 testes da extensão estão incluídos nessa execução.

Provas independentes: **9 casos aprovados no agregado**. Primeira execução: oito aprovados e uma falha de instrumentação ao localizar o fragmento com espaço final; corrigido apenas o comparador no script externo, o caso de 100 ocorrências passou em reexecução isolada (**1 passed, 8 deselected**, 1,83 s). Nenhum defeito de produto foi inferido dessa falha do teste.

Reexecução direcionada: **9 passed, 96 deselected**, 17,94 s:

| Finding | Reprodução verificada |
| --- | --- |
| E5-AUD-002 | Segredo no título não sobrevive nas superfícies finais. |
| E5-AUD2-002 | Cópias sem rótulo, no corpo e no título permanecem protegidas. |
| E5-AUD3-002 | Bearer e assigned_secret com prefixo em outro fragmento permanecem protegidos. |
| E5-AUD4-001 | `password: sk-123` / `456789ABC`: cauda ausente. |
| E5-AUD4-003 | URL com `://` ou `@` separado: credencial ausente, inclusive em títulos. |

Comando direcionado, em `api/`: mesmos argumentos pytest acima, arquivos `tests/test_context_redaction_e5_round3.py`, `tests/test_context_redaction_e5_round4.py`, `tests/test_context_redaction_e5_round5.py`, filtro `-k 'r1_segredo_no_titulo or e5_aud2_002 or e5_aud3_002 or e5_aud4_001_cauda or e5_aud4_003_url_credentials'`.

Evidência independente preservada fora do repositório:

```text
C:/Users/pedro/.codex/visualizations/2026/09/09/01a086e1-754d-77c1-9a77-4af609dd6d66/e5-final/test_final.py
C:/Users/pedro/.codex/visualizations/2026/09/09/01a086e1-754d-77c1-9a77-4af609dd6d66/e5-final/results.txt
C:/Users/pedro/.codex/visualizations/2026/09/09/01a086e1-754d-77c1-9a77-4af609dd6d66/e5-final/hundred.txt
```

Reexecução desse script, em `api/`: definir `PYTHONPATH` para o diretório atual e executar `python -m pytest -c pyproject.toml -p tests.conftest -p no:cacheprovider <caminho-absoluto-do-script> -s -q -o addopts=''`.

SHA-256 do código auditado:

```text
rendering.py        bdcb2ac18c1a8524e4999893381bcf813dd224acb5e22568c257ceea0c4d0b3f
safety/redaction.py 22e31d65bb21ea4d5f4b4de127437901fae0ebd024c3d397dda08e2762b84c54
```

## Fechamento

**GREEN — a E5 está pronta para commit e push.** Nenhum finding E5-AUDF novo; suíte completa e verificações específicas sem falha pendente. Somente este relatório e uma entrada breve no AGENT_LOG foram alterados pela auditoria dentro do repositório; nenhum código corrigido, commit ou push executado.

Total: **5 rodadas de auditoria + esta rechecagem final** (6 verificações).
**19 findings corrigidos**, por identificador: 9 da rodada 1, 5 da rodada 2, 2 da rodada 3 e 3 da rodada 4; E5-AUD3-001 é política aceita, não correção.
**E5-AUD5-001 permanece risco residual aceito:** over-redaction em escala restrita à mesma entrada/bloco, agora sinalizada por metadata fixa.
A aceitação está documentada em [03-context-architecture.md §4](../architecture/03-context-architecture.md), no parágrafo E5-AUD5-001, com a reprodução e recomendação na [rodada 5](e5-round-5.md).
