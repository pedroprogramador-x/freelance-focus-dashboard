"""`tool_executor` — único executor de efeitos de agente ([01] §2).

* E7.2 — `contracts.py` e `validation.py`: tipos e Protocols, sem IO.
* E7.5-A — fundação do executor concreto: binding da execution workspace (`workspace.py`),
  `DecisionJournal` (`journal.py`), facade (`facade.py`), frases fixas (`reasons.py`), o
  esqueleto run-scoped (`executor.py`) e a factory (`factory.py`). **Nenhuma das nove operações
  é executada ainda**; os handlers entram a partir da E7.5-B.

Nada é importado aqui de propósito: quem precisa do executor importa o módulo que o define, e
`agent_runtime` só enxerga `contracts` ([01] §3).
"""
