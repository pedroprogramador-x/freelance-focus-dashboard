"""Política de segurança **efetiva** de um workspace, e o `safety_policy_hash` dela.

[04](../../../docs/architecture/04-safety-and-git-runtime.md) §5: "composição: política
global mais override opcional por workspace. **Um override só restringe, nunca afrouxa** —
a composição é intersecção de permissões. `safety_policy_hash` cobre a política composta
efetiva e entra no fingerprint."

## Por que esta função mora em `workspace/` e não em `safety/`

`safety/` é política **pura** e não importa `db` ([01] §2) — ele não pode receber um
`DevWorkspace`. Ele já oferece as duas peças que importam: `SafetyPolicy.compose(override)`
faz a intersecção, e `policy_hash(policy)` produz o hash canônico. O que faltava era o
**resolvedor**: quem lê o override do agregado e aplica a composição. Esse é o `workspace/`,
dono do agregado ([02] §1: "quem altera apenas `workspace/`; o `orchestrator/` só lê").

Pôr o resolvedor no `orchestrator/` teria sido a outra opção, e é a errada: o Orchestrator
passaria a conhecer a forma de armazenamento do override, que é assunto do agregado.

## O override ainda não tem coluna — e isso é deliberado

[02] §1 congela os campos de `DevWorkspace`, e **nenhum deles é um override de política de
segurança**. Acrescentar uma coluna exigiria mudar `docs/`, que está sob Architecture
Freeze. Então, na E6:

* o **mecanismo** de composição é real e é o de `safety/` — testado por
  `test_orchestrator_safety_policy.py`, que compõe um override e confere que o hash muda;
* a **origem** do override é o parâmetro `override`, não uma coluna. Hoje o único chamador
  não passa nenhum, e a política efetiva de todo workspace é a global.

Quando a coluna existir, `resolve_effective_policy` passa a lê-la e nenhum chamador muda.
"""

from __future__ import annotations

from app.db.models import DevWorkspace
from app.safety.policy import SafetyPolicy, SafetyPolicyOverride, policy_hash


def resolve_effective_policy(
    workspace: DevWorkspace,
    *,
    base: SafetyPolicy | None = None,
    override: SafetyPolicyOverride | None = None,
) -> SafetyPolicy:
    """A política composta efetiva deste workspace ([04] §5).

    ``base`` é a política global (a do composition root); `None` usa o default de
    `SafetyPolicy`. ``override`` é o override restritivo do workspace — ver o docstring do
    módulo para por que ele é parâmetro e não coluna nesta fase.

    ``workspace`` é recebido e hoje não é lido. Ele está na assinatura porque é ele que
    **identifica** de quem é a política: um resolvedor que não recebe o agregado não é um
    resolvedor, é um `getter` de constante global com nome enganoso. Quando a coluna de
    override existir, esta função a lê e nenhum chamador muda — que é exatamente o ponto de
    a fronteira já estar no lugar.
    """
    del workspace  # ver o docstring: a origem do override é parâmetro nesta fase

    active = base or SafetyPolicy()
    if override is None:
        return active
    return active.compose(override)


def effective_policy_hash(
    workspace: DevWorkspace,
    *,
    base: SafetyPolicy | None = None,
    override: SafetyPolicyOverride | None = None,
) -> str:
    """`safety_policy_hash` de [02] §7, sobre a política composta efetiva.

    Reaproveita `safety.policy.policy_hash`, que por sua vez reaproveita `canonical_json` —
    nenhuma segunda normalização nasce aqui.
    """
    return policy_hash(resolve_effective_policy(workspace, base=base, override=override))


__all__ = ["effective_policy_hash", "resolve_effective_policy"]
