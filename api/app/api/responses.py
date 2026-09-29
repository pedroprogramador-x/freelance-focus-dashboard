"""O **boundary de saída** de [04](../../../docs/architecture/04-safety-and-git-runtime.md)
§5: toda resposta JSON de `/api/*` atravessa o redator antes de virar bytes.

## Por que uma classe de resposta, e não uma chamada por projeção

Até a E6-AUD3 a redação era *opt-in*: cada projeção chamava `redact`/`redact_document` por
conta própria. O modelo funciona enquanto ninguém esquece — e três auditorias seguidas
encontraram exatamente a mesma falha, cada vez numa superfície diferente que a correção
anterior não tinha alcançado:

* **E6-AUD-007** — `test_config` nas respostas de workspace;
* **E6-AUD2-001** — `runner_id` em `execution_fingerprint_parts`: uma segunda saída do
  mesmo dado que a correção anterior já tinha protegido num lugar;
* **E6-AUD3-001** — `name`, `local_path`, `default_branch`, `linked_project_id`, o nome de
  branch do preflight, a projeção inteira do Context Registry, e o `detail[].input` do
  `422` de validação.

O padrão não é descuido de quem escreveu cada projeção: é o modelo estar invertido. Numa
garantia *opt-in* a superfície nova nasce **desprotegida** e alguém precisa lembrar de
protegê-la; o esquecimento é silencioso e só aparece quando um auditor procura. Invertendo
para *opt-out*, a superfície nova nasce **protegida** e escapar exige um ato deliberado —
construir um `Unredacted`, que é uma linha rastreável por AST e que `test_architecture`
limita a um único ponto em todo o backend.

## Onde exatamente ele age

`JSONResponse.__init__` chama `render(content)`, e é ali — **antes** de `json.dumps`, com o
conteúdo ainda como `dict`/`list`/escalar — que a caminhada roda. [04] §5 exige isso em vez
de reprocessar JSON já serializado: *reparsing* precisaria desfazer o encoding para
inspecionar, refazer depois, e erraria em tudo que não sobrevive ao *round-trip*
(precisão de float, ordem de chave, `NaN`). O ponto certo é o único em que a estrutura
ainda existe.

**Não há exceção por rota, por schema ou por nome de campo.** A classe não sabe qual rota a
produziu e não consulta metadado de campo nenhum — se soubesse, a decisão voltaria a ser
distribuída, que é o defeito que ela existe para remover.

## O que fica fora, e por quê

O HTML de bootstrap (`api/web.py`), os assets estáticos e o SSE futuro da E11 têm contrato
próprio, declarado em [04] §5. O `204 No Content` do registry também: um corpo vazio não
tem string para redigir, e forçá-lo por aqui produziria o `null` que o status proíbe.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from app.safety.redaction import redact_document


class RedactingJSONResponse(JSONResponse):
    """`JSONResponse` que redige o conteúdo inteiro antes de serializar.

    É o `default_response_class` da aplicação **e** a classe usada por todo *exception
    handler* — inclusive o de `RequestValidationError`, que era a saída do `422` com o
    valor recusado ecoado em `detail[].input` (E6-AUD3-001). Uma resposta de erro carrega
    exatamente o que o cliente mandou, o que faz dela a superfície mais provável de
    devolver uma credencial digitada por engano; tratá-la como "resposta de erro, não conta"
    seria a exceção por rota que este módulo existe para não ter.
    """

    def render(self, content: Any) -> bytes:
        return super().render(redact_document(content))


class EditViewJSONResponse(JSONResponse):
    """A **única** resposta JSON de `/api/*` que não redige. Um tipo, não uma flag.

    Ela serve `POST /api/context/{entry_id}/edit-view` e nada mais: o conteúdo cru de uma
    entrada do Context Registry, para que quem edita veja o que realmente está gravado em
    vez da projeção redigida. Sem ela o editor lê `«redigido»`, salva, e o marcador
    substitui para sempre o trecho que escondia (E6-AUD4-004).

    ## Por que uma classe de resposta, e não `Unredacted` campo a campo

    `Unredacted` é o marcador de **um valor** — o `purge_token`, um segredo que a resposta
    tem a função de entregar. Aqui o que escapa é a resposta **inteira**, e envolver cada
    campo em `Unredacted` transformaria o escape hatch de um construtor rastreável em um
    idioma reutilizável: o teste de arquitetura que hoje afirma "existe exatamente um
    `Unredacted(...)` em todo o backend" perderia o sentido no dia em que fossem quatro.

    Duas exceções, dois tipos, cada um com **um** ponto de uso — e
    `test_architecture.py::test_o_conjunto_de_respostas_sem_redacao_e_fechado` enumera os
    dois e falha em qualquer terceiro.

    ## O que o chamador precisa garantir

    Esta resposta carrega conteúdo que pode conter credencial de verdade. Ela **não** pode
    ser logada, virar `SafetyEvent`, entrar em mensagem de erro ou em telemetria, e sai com
    `Cache-Control: no-store` para não ficar em cache de navegador nenhum. A rota que a usa
    é a mais curta da API de propósito: ler, projetar, devolver.

    Sem corpo: ela é um `JSONResponse` comum, e é justamente isso que a distingue. O que a
    torna uma exceção auditável é o **nome**, que o teste de arquitetura enumera.
    """


__all__ = ["EditViewJSONResponse", "RedactingJSONResponse"]
