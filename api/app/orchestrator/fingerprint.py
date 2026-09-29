"""`execution_fingerprint` — [02](../../../docs/architecture/02-data-model.md) §7.

    execution_fingerprint = sha256( canonical_json( { v, plan_hash, manifest_hash,
        rendered_context_hash, base_commit, developer_binding, auditor_binding,
        test_binding, agents, tool_profile_hash, safety_policy_hash,
        workflow_policy_hash, execution_limits } ) )

## As três coisas que este módulo existe para garantir

**1. Ausência é `null` explícito, nunca chave omitida.** [02] §7 é literal: "auditor
ausente: se, na fase da execução, o auditor ainda não existe e a política de workflow
permite ausência, `auditor_binding` recebe **`null` explícito**. Omitir a chave faria dois
cenários distintos colidirem no mesmo hash." A mesma regra vale para o `test_binding` de um
workspace sem `test_config` — lá o `null` é por campo, não pelo objeto inteiro
(`NULL_TEST_BINDING`), porque a chave `test_binding` sempre existe no schema.

**2. `tool_profile_hash` é o perfil REQUERIDO/APROVADO.** Corrigido em 2026-09-12: ele é
declarado e congelado **na aprovação (E6)**, e nunca provado nesse momento. A prova
acontece em E7+, quando o adaptador declara `effective_capabilities` ([05]) e a guarda
`approved → executing` exige que o perfil efetivo comprovado satisfaça exatamente este
perfil aprovado. O perfil **comprovado** vive em outro lugar — `Run.tool_profile_hash`, por
run — e não é este. Confundir os dois foi o que a correção de terminologia daquela data
fechou.

**3. Recalcular é a operação normal, não a excepcional.** [02] §7: "recalculado no
`approve` e novamente na guarda `approved → executing`". Por isso `compute` é uma função
pura sobre partes, e `parts` é persistido junto: a UI de [06] §4 precisa dizer **qual
campo** mudou, e comparar dois hashes só responde *que* mudou.

## O perfil de capability da V1

[ADR-0009] e [01] §5: `execute_commands = disabled`, sem exceção; `read_files = mediated`
obrigatório. O perfil requerido da V1 é, portanto, uma **constante** — não há configuração
que o altere, e um adaptador que não o prove é inutilizável como Developer (R4). Ele entra
no fingerprint como hash de um documento canônico para que, quando E7 introduzir perfis
variáveis, a forma do fingerprint não mude.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.safety.canonical import canonical_json, canonical_sha256

#: Versão da forma do objeto hasheado ([02] §7, "Versão"). `v1` é a estrutura canônica
#: final de [02] §7, tal como congelada em 1B.3.
FINGERPRINT_VERSION = 1

#: Versão da forma do documento de perfil de capability. Separada de `FINGERPRINT_VERSION`
#: porque o perfil pode ganhar campos (E7) sem que a estrutura do fingerprint mude.
TOOL_PROFILE_VERSION = 1

#: O perfil de capability **requerido** da V1 ([ADR-0009], [01] §5). Constante: nenhuma
#: configuração o afrouxa, e `execute_commands` nunca sai de `disabled` na V1.
REQUIRED_TOOL_PROFILE: dict[str, Any] = {
    "v": TOOL_PROFILE_VERSION,
    "execute_commands": "disabled",
    "read_files": "mediated",
    "write_files": "mediated",
    "network_access": "disabled",
}

#: Versão da forma do documento de política de workflow.
WORKFLOW_POLICY_VERSION = 1


@dataclass(frozen=True, slots=True)
class WorkflowPolicy:
    """O que `workflow_policy_hash` cobre ([02] §7: "auditoria obrigatória, gates, rodadas").

    ``audit_required_on_nonempty_diff`` é `True` a partir da E9 ([03] §7 regra 1). Na E6 o
    auditor não existe, então a política **declara** a obrigatoriedade futura e o
    `auditor_binding` sai `null` — os dois fatos são distintos e os dois entram no hash.
    """

    audit_required_on_nonempty_diff: bool = True
    audit_skip_docs_only: bool = False
    block_done_on_high_open_finding: bool = True
    max_fix_rounds: int = 0
    max_attempts: int = 0

    def as_canonical(self) -> dict[str, Any]:
        return {
            "v": WORKFLOW_POLICY_VERSION,
            "audit_required_on_nonempty_diff": self.audit_required_on_nonempty_diff,
            "audit_skip_docs_only": self.audit_skip_docs_only,
            "block_done_on_high_open_finding": self.block_done_on_high_open_finding,
            "max_fix_rounds": self.max_fix_rounds,
            "max_attempts": self.max_attempts,
        }

    def policy_hash(self) -> str:
        return canonical_sha256(self.as_canonical())


def tool_profile_hash(profile: dict[str, Any] | None = None) -> str:
    """Hash do perfil de capability **requerido/aprovado**. Ver o docstring do módulo.

    Não é — e não pode virar — o perfil comprovado de um `Run`. A prova é E7+.
    """
    return canonical_sha256(profile if profile is not None else REQUIRED_TOOL_PROFILE)


@dataclass(frozen=True, slots=True)
class FingerprintParts:
    """Os componentes de [02] §7, antes de virarem um hash.

    Persistido em `WorkspaceTask.approved_fingerprint_parts` para que a invalidação diga
    **qual campo** mudou ([04] §7). É a razão de esta ser uma dataclass e não um dicionário
    montado na hora: os campos são fechados, e um campo novo que alguém esqueça de incluir
    no `as_canonical` falharia o teste de reprodutibilidade em vez de sair do hash em
    silêncio.
    """

    plan_hash: str
    manifest_hash: str
    rendered_context_hash: str
    base_commit: str
    developer_binding: dict[str, Any] | None
    auditor_binding: dict[str, Any] | None
    test_binding: dict[str, Any]
    agents: tuple[str, ...]
    tool_profile_hash: str
    safety_policy_hash: str
    workflow_policy_hash: str
    execution_limits: dict[str, Any]

    def as_canonical(self) -> dict[str, Any]:
        """A estrutura de [02] §7, com **todas** as chaves sempre presentes.

        `agents` sai como `list` preservando a ordem — [02] §7 nomeia `agents` entre os
        arrays cuja ordem é semanticamente relevante, e ordená-lo faria
        `[developer, architect]` e `[architect, developer]` colidirem.
        """
        return {
            "v": FINGERPRINT_VERSION,
            "plan_hash": self.plan_hash,
            "manifest_hash": self.manifest_hash,
            "rendered_context_hash": self.rendered_context_hash,
            "base_commit": self.base_commit,
            "developer_binding": self.developer_binding,
            "auditor_binding": self.auditor_binding,
            "test_binding": self.test_binding,
            "agents": list(self.agents),
            "tool_profile_hash": self.tool_profile_hash,
            "safety_policy_hash": self.safety_policy_hash,
            "workflow_policy_hash": self.workflow_policy_hash,
            "execution_limits": self.execution_limits,
        }

    def compute(self) -> str:
        """`sha256(canonical_json(...))` — a fórmula literal de [02] §7."""
        return canonical_sha256(self.as_canonical())


def diverged_fields(stored: dict[str, Any] | None, current: dict[str, Any]) -> tuple[str, ...]:
    """Quais chaves de topo mudaram entre o que foi aprovado e o que vale agora.

    É o "qual campo mudou" de [04] §7 e de [06] §4. A comparação é por
    `canonical_json` de cada valor, e não por `==` de Python, pelo mesmo motivo que o hash
    é: dois dicionários com as mesmas chaves em ordem diferente são iguais para o `==` mas
    poderiam ter serializações diferentes sob outra normalização — comparar pela forma que
    de fato entra no hash é o que garante que "nenhum campo divergiu" e "o hash bate" nunca
    discordem.

    ``stored`` `None` devolve todas as chaves: uma task sem `approved_fingerprint_parts`
    não tem base de comparação, e afirmar "nada mudou" seria a resposta errada.
    """
    if stored is None:
        return tuple(sorted(current))

    return tuple(
        sorted(
            key
            for key in set(stored) | set(current)
            if canonical_json(stored.get(key)) != canonical_json(current.get(key))
        )
    )


__all__ = [
    "FINGERPRINT_VERSION",
    "REQUIRED_TOOL_PROFILE",
    "TOOL_PROFILE_VERSION",
    "WORKFLOW_POLICY_VERSION",
    "FingerprintParts",
    "WorkflowPolicy",
    "diverged_fields",
    "tool_profile_hash",
]
