# Auditoria independente E5 — rodada 1 (reconstrução fiel)

Auditoria original: 2026-09-09, Codex (GPT-6). Reconstrução persistida durante a rodada 2, em 2026-09-09, por autorização expressa do usuário.

**Proveniência:** o texto final da primeira auditoria e o histórico de ferramentas estavam disponíveis nesta conversa. O relatório original não havia sido gravado no repositório; scripts/resultados estavam fora dele. Este documento reconstrói fielmente os nove findings, reproduções e veredito originais. Não é uma nova execução da versão anterior e não afirma que os defeitos ainda existem na versão corrigida. A situação atual é avaliada em [e5-round-2.md](e5-round-2.md).

As referências de linhas abaixo pertencem à implementação auditada na primeira rodada; os arquivos foram modificados desde então.

## Escopo e execução originais

Lidos: entrada E5 completa do AGENT_LOG.md, com as 20 decisões; git status --short e git diff --stat completos; 02-data-model.md §5 e §7, incluindo a exceção V1 de objective; 03-context-architecture.md §4; ADR-0006; 01-v1-architecture.md.

Nenhuma correção e nenhum commit foram realizados no workspace. A suíte existente E5 + arquitetura teve 130 testes executados com sucesso. Foram executados 17 cenários independentes de prova, com Python 3.12.14 e pytest 8.4.2 no Windows. Bancos e repositórios Git eram temporários; os commits dos fixtures pertenciam somente aos repositórios sintéticos.

## Findings originais

### E5-AUD-001 — Alta/P1 — Colisão semântica entre caminhos Unicode distintos

**Arquivo/linha histórica:** api/app/context_engine/manifest.py:216,231.

**Reprodução:** no mesmo commit, criados src/caf\u00e9.py e src/cafe\u0301.py com bytes iguais. Alterar legitimamente source_refs da mesma entrada entre os dois arquivos mudou source_files, mas preservou manifest_hash. A normalização NFC eliminava a distinção Git antes do SHA-256; não era colisão criptográfica.

**Recomendação original:** preservar identidade exata dos caminhos na representação hasheada, ou recusar ambiguidades, mantendo o canonical_json único.

### E5-AUD-002 — Alta/P1 — Segredo no título escapa nos metadados

**Arquivo/linha histórica:** selection.py:537 e manifest.py:114, em api/app/context_engine/.

**Reprodução:** título com chave sintética sk-ant-… era redigido em blocks[].text, mas permanecia em blocks[].origin.title, ContextManifest.entries[].title e bytes do artifact.

**Recomendação original:** redigir também todos os metadados autorais propagados nas três camadas.

### E5-AUD-003 — Alta/P1 — JSON de structured impede reconhecer atribuição de segredo

**Arquivo/linha histórica:** api/app/context_engine/rendering.py:120.

**Reprodução:** structured={"password":"hunter2"} chegou intacto ao artifact, enquanto redact("password=hunter2") funcionava. A aspa entre password e o sinal de atribuição impedia o padrão assigned_secret de reconhecer o contexto.

**Recomendação original:** preservar o contexto chave–valor para o redator antes da serialização, ou permitir que o redator único reconheça atribuições JSON; não perder o contexto ao redigir apenas o valor isolado.

### E5-AUD-004 — Alta/P1 — Framing interrompe valor partido entre campos

**Arquivo/linha histórica:** api/app/context_engine/rendering.py:141.

**Reprodução:** sk-1234567890ABCDEF era redigido quando inteiro. Separado em body="sk-123456" e structured={"resto":"7890ABCDEF"}, ambas as partes sobreviveram sem marcador no texto e artifact. Quebras, cerca Markdown e sintaxe JSON interrompiam o token antes da redação. O teste PEM existente passava por causa de DOTALL e não provava o caso geral.

**Recomendação original:** analisar valores antes do framing e mapear as detecções de volta às partes originais para redigi-las integralmente.

### E5-AUD-005 — Média/P2 — Desempate perde microssegundos

**Arquivo/linha histórica:** api/app/context_engine/selection.py:259.

**Reprodução:** mesmo score, IDs A<B, updated_at .100000 para A e .900000 para B no mesmo segundo; após persistência/recarga, orçamento para uma entrada escolheu A, a mais antiga. Entre segundos distintos, a seleção funcionou.

**Recomendação original:** epoch em microssegundos inteiros, incluindo delta.microseconds e sem datetime.timestamp()/float.

### E5-AUD-006 — Média/P2 — Títulos equivalentes alteram hashes

**Arquivo/linha histórica:** selection.py:194 e manifest.py:114, em api/app/context_engine/.

**Reprodução:** na MESMA entrada, título "Line A  \r\nLine B" alterado para "Line A\nLine B". content_hash e render_block_text permaneceram iguais, mas manifest_hash e rendered_context_hash mudaram por causa da cópia crua do título nos metadados.

**Recomendação original:** aplicar a normalização autoral única aos títulos registrados nas três camadas.

### E5-AUD-007 — Média/P2 — REDACT isolado permite normalização depois da medição

**Arquivo/linha histórica:** rendering.py:133 e manifest.py:156, em api/app/context_engine/.

**Reprodução:** body="e\u0301" * 100, transformations=(REDACT,). Artifact declarava 233 caracteres e 59 tokens aproximados; blocks[].text relido dos bytes tinha 133 caracteres e 34 tokens pela fórmula do módulo. canonical_json fazia NFC depois da contagem.

**Recomendação original:** normalizar antes da medição, ou tornar NORMALIZE obrigatório. Este finding tratava de contagem, não de identidade de caminhos.

### E5-AUD-008 — Média/P2 — Precisão não prevalece sobre cobertura/proximidade

**Arquivo/linha histórica:** api/app/context_engine/selection.py:382.

**Reprodução:** candidato novo src/new.py; entrada ampla src/** versus literal preciso src/new.py. Ambas unknown, sinais restantes iguais, precisa mais recente: ampla venceu por130×100 devido à proximidade de outros arquivos já existentes. Para candidato existente, ambas pontuavam140 e a recência decidia; não havia preferência intrínseca por precisão.

**Qualificação original:** a implementação seguia os pesos então registrados, mas não sustentava a garantia adicional “nunca” exigida no ataque15 da missão.

**Recomendação original:** explicitar no contrato e implementar como a precisão deve prevalecer.

### E5-AUD-009 — Média/P2 — Artifact existente é aceito sem validar conteúdo

**Arquivo/linha histórica:** api/app/context_engine/manifest.py:163.

**Reprodução:** renderizar seleção, substituir o blob de teste por bytes corrompidos e renderizar novamente. Retorno com hash esperado e written=False, embora SHA-256 do disco divergisse. freeze_manifest persistia a referência inválida.

**Recomendação original:** verificar bytes em modo binário antes do reaproveitamento e recusar/reconstruir conteúdo divergente.

## Demais resultados originais

- Quatro etapas repetidas, inserção direta/inversa com IDs fixos, pai e subprocessos com PYTHONHASHSEED 0,1,12345,4294967295, tasks diferentes e leitura rb: hashes idênticos no cenário íntegro.
- Objective: orçamento1, 20.036 caracteres e 5.009 tokens aproximados, incluída inteira, truncated=false. Explicitamente CORRETO na V1; não corrigir.
- Colisões de chaves normalizadas em structured, inclusive aninhadas/NFC, foram recusadas. Alterações legítimas de valor mudaram o manifest.
- Uma definição de canonical_json, sem implementação paralela; isolamento de context_engine confirmado.
- Greedy com custos13,258,13,13 e orçamento39 selecionou E0,E2,E3, preservando ordem. Ressalva: duas passadas físicas (objectives e demais), mais ordenação final; não literalmente uma única passagem pela coleção toda.
- Cache: 400 chamadas concorrentes, 80 chaves, threads e asyncio.to_thread, sem mapa incorreto; o esvaziamento não invalidou referências locais a objetos imutáveis.
- Listas vazias, ausência de REDACT e None foram recusados; ordem invertida não desligou redação. REDACT isolado preservou redação, mas revelou E5-AUD-007.
- open() executáveis da fase usavam wb/rb. Wrappers de texto sem newline="" foram identificados em context_helpers.py:65 e test_context_router_e5.py:801,978,1051,1054, todos caminhos de teste.

## Veredito original

**NÃO GREEN — bloqueavam os nove findings E5-AUD-001 a E5-AUD-009.** O relatório original não autorizou correções nem commits. Este registro retrospectivo conserva esse veredito histórico; o veredito da implementação atual está na rodada2.
