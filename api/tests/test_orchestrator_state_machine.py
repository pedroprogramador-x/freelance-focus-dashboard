"""Máquina de estados ([02] §4, [ADR-0008]) — cada aresta válida e inválida da tabela.

Gate da sub-etapa 6 do prompt da E6:

* cada transição válida/inválida da tabela testada;
* `approved → executing` **recusada** com 409 e motivo claro (capability não provável);
* `reconcile_on_startup` idempotente.

A tabela é testada de forma **exaustiva**: 9 × 9 = 81 pares, e cada um é conferido contra
uma definição independente da de `state_machine.TRANSITIONS`. Testar contra a mesma
estrutura que o código usa não provaria nada — provaria que `TRANSITIONS == TRANSITIONS`.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from app.db.enums import FailureReason, TaskPhase, TaskStatus
from app.orchestrator.errors import InvalidTransition, TransitionGuardFailed
from app.orchestrator.fingerprint import tool_profile_hash
from app.orchestrator.state_machine import (
    TERMINAL_STATUSES,
    CapabilityProof,
    EntryGuardFacts,
    can_transition,
    check_approval_guard,
    check_entry_guard,
    check_needs_fix_guard,
    is_terminal,
    require_transition,
    transition_fields,
)
from tests.capability_helpers import FixedProver, expected_binding, full_proof

#: As arestas de [02] §4, transcritas **do diagrama** e não do código. É a segunda fonte
#: que torna o teste exaustivo abaixo uma verificação e não uma tautologia.
ARESTAS_DO_DIAGRAMA: frozenset[tuple[TaskStatus, TaskStatus]] = frozenset(
    {
        (TaskStatus.DRAFT, TaskStatus.PLANNING),
        (TaskStatus.PLANNING, TaskStatus.AWAITING_APPROVAL),
        (TaskStatus.PLANNING, TaskStatus.DRAFT),
        (TaskStatus.PLANNING, TaskStatus.FAILED),
        (TaskStatus.AWAITING_APPROVAL, TaskStatus.DRAFT),
        (TaskStatus.AWAITING_APPROVAL, TaskStatus.APPROVED),
        (TaskStatus.AWAITING_APPROVAL, TaskStatus.AWAITING_APPROVAL),
        (TaskStatus.APPROVED, TaskStatus.EXECUTING),
        (TaskStatus.APPROVED, TaskStatus.AWAITING_APPROVAL),
        (TaskStatus.EXECUTING, TaskStatus.DONE),
        (TaskStatus.EXECUTING, TaskStatus.NEEDS_FIX),
        (TaskStatus.EXECUTING, TaskStatus.FAILED),
        (TaskStatus.NEEDS_FIX, TaskStatus.PLANNING),
        (TaskStatus.NEEDS_FIX, TaskStatus.APPROVED),
        (TaskStatus.NEEDS_FIX, TaskStatus.CANCELLED),
        (TaskStatus.DRAFT, TaskStatus.CANCELLED),
        (TaskStatus.PLANNING, TaskStatus.CANCELLED),
        (TaskStatus.AWAITING_APPROVAL, TaskStatus.CANCELLED),
        (TaskStatus.APPROVED, TaskStatus.CANCELLED),
        (TaskStatus.EXECUTING, TaskStatus.CANCELLED),
    }
)

_TODOS_OS_PARES = [(origem, destino) for origem in TaskStatus for destino in TaskStatus]


class _RefusingProver:
    def __init__(self, reason: str = "enforcement_method = not_enforceable") -> None:
        self.reason = reason

    def prove(self, required_profile_hash: str) -> CapabilityProof:
        del required_profile_hash
        return CapabilityProof(proven=False, reason=self.reason)


class _AcceptingProver:
    """Prova **legada** (E6): `proven = True` + hash requerido, sem observação.

    Desde a E7.6 isto **não basta** — é o contrafactual de que a prova legada é recusada
    (`verification_missing`). Também serve de provador "que passaria" nos testes em que uma
    guarda *anterior* deve recusar antes de a capability ser consultada.
    """

    def prove(self, required_profile_hash: str) -> CapabilityProof:
        return CapabilityProof(proven=True, effective_profile_hash=required_profile_hash)


def _facts(**overrides: object) -> EntryGuardFacts:
    defaults: dict[str, object] = {
        "fingerprint_matches": True,
        "slot_available": True,
        "is_git_repo": True,
        "head": "a" * 40,
        "planning_base_commit": "a" * 40,
        "attempts": 0,
        "max_attempts": 2,
    }
    defaults.update(overrides)
    return EntryGuardFacts(**defaults)  # type: ignore[arg-type]


# ------------------------------------------------------------------- tabela exaustiva


@pytest.mark.parametrize(("origem", "destino"), _TODOS_OS_PARES)
def test_cada_par_de_estados_bate_com_o_diagrama(origem: TaskStatus, destino: TaskStatus) -> None:
    """**GATE**: os 81 pares, conferidos contra a transcrição independente do diagrama."""
    esperado = (origem, destino) in ARESTAS_DO_DIAGRAMA

    assert can_transition(origem, destino) is esperado, (
        f"`{origem.value}` → `{destino.value}`: código diz "
        f"{can_transition(origem, destino)}, diagrama diz {esperado}"
    )


#: Ordenados por valor para que a ordem dos casos não dependa da iteração de um `frozenset`.
_TERMINAIS: list[TaskStatus] = sorted(TERMINAL_STATUSES, key=lambda status: status.value)


@pytest.mark.parametrize("origem", _TERMINAIS)
@pytest.mark.parametrize("destino", list(TaskStatus))
def test_nenhuma_aresta_sai_de_estado_terminal(origem: TaskStatus, destino: TaskStatus) -> None:
    """[ADR-0008] regra 3: terminais são imutáveis; retomar é criar uma task nova."""
    assert not can_transition(origem, destino)

    with pytest.raises(InvalidTransition, match="terminal"):
        require_transition(origem, destino)


@pytest.mark.parametrize(
    ("origem", "destino"),
    [
        (TaskStatus.DRAFT, TaskStatus.EXECUTING),
        (TaskStatus.AWAITING_APPROVAL, TaskStatus.EXECUTING),
        (TaskStatus.DRAFT, TaskStatus.APPROVED),
        (TaskStatus.DRAFT, TaskStatus.DONE),
    ],
)
def test_transicoes_invalidas_nomeadas_em_02_secao_4(
    origem: TaskStatus, destino: TaskStatus
) -> None:
    """As transições que [02] §4 lista explicitamente como inválidas."""
    with pytest.raises(InvalidTransition) as exc:
        require_transition(origem, destino)

    assert exc.value.status_code == 409
    assert exc.value.as_payload()["current_status"] == origem.value
    assert exc.value.as_payload()["requested_status"] == destino.value


def test_is_terminal_cobre_exatamente_os_tres() -> None:
    assert {s for s in TaskStatus if is_terminal(s)} == {
        TaskStatus.DONE,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }


# ------------------------------------------------------------------ campos coerentes


def test_phase_existe_se_e_somente_se_executing() -> None:
    """[02] §3, invariante 1 — imposta pela montagem, não pela confiança no chamador."""
    executando = transition_fields(TaskStatus.EXECUTING, phase=TaskPhase.IMPLEMENTING)
    assert executando["phase"] is TaskPhase.IMPLEMENTING

    for status in TaskStatus:
        if status is TaskStatus.EXECUTING:
            continue
        campos = transition_fields(
            status,
            phase=TaskPhase.TESTING,
            failure_reason=FailureReason.TIMEOUT if status is TaskStatus.FAILED else None,
        )
        assert campos["phase"] is None, f"`{status.value}` não pode carregar `phase`"


def test_failure_reason_existe_se_e_somente_se_failed() -> None:
    """[02] §3, invariante 2 / [ADR-0008] AUD-011: nunca em `cancelled`, nunca em `done`."""
    falhou = transition_fields(TaskStatus.FAILED, failure_reason=FailureReason.TIMEOUT)
    assert falhou["failure_reason"] is FailureReason.TIMEOUT

    for status in (TaskStatus.CANCELLED, TaskStatus.DONE, TaskStatus.DRAFT):
        campos = transition_fields(
            status,
            phase=TaskPhase.TESTING if status is TaskStatus.EXECUTING else None,
            failure_reason=FailureReason.TIMEOUT,
        )
        assert campos["failure_reason"] is None, (
            f"`{status.value}` carregou `failure_reason`: desistir não é falhar (AUD-011)"
        )


def test_executing_sem_phase_e_failed_sem_reason_sao_erro_de_programacao() -> None:
    with pytest.raises(ValueError, match="phase"):
        transition_fields(TaskStatus.EXECUTING)

    with pytest.raises(ValueError, match="failure_reason"):
        transition_fields(TaskStatus.FAILED)


# --------------------------------- GATE: approved → executing recusada nesta fase


def test_sem_provador_a_guarda_de_capability_recusa() -> None:
    """**GATE da sub-etapa 6**: `approved → executing` falha, com motivo claro.

    Não é um bug: é [ADR-0004]/[ADR-0009] funcionando. A ausência de prova é tratada como
    prova negativa — *fail closed*.
    """
    with pytest.raises(TransitionGuardFailed) as exc:
        check_entry_guard(_facts(), prover=None)

    assert exc.value.status_code == 409
    assert exc.value.guard == "capability_profile_proven"
    assert "fail closed" in exc.value.message.lower()
    assert exc.value.as_payload() == {"guard": "capability_profile_proven"}


def test_provador_que_recusa_tambem_para_a_execucao() -> None:
    """O outro desfecho de [ADR-0009]: o adaptador existe e **não** prova o perfil."""
    with pytest.raises(TransitionGuardFailed) as exc:
        check_entry_guard(_facts(), prover=_RefusingProver(), expected_binding=expected_binding())

    assert exc.value.guard == "capability_profile_proven"
    assert exc.value.reason_code == "verification_negative"
    # E7.6: o texto livre do provador nunca é interpolado no diagnóstico.
    assert "not_enforceable" not in exc.value.message


def test_a_guarda_nao_e_um_raise_incondicional() -> None:
    """Contrafactual: com uma prova **completa** (E7.6), a guarda **passa**.

    Sem este caso, o teste acima passaria mesmo que a guarda fosse `raise` puro. Desde a
    E7.6 a prova legada (`proven + hash`) não basta: ver `test_capability_enforcement_e7_6`.
    """
    check_entry_guard(
        _facts(), prover=FixedProver(full_proof()), expected_binding=expected_binding()
    )


@pytest.mark.parametrize(
    ("mutacao", "guarda_esperada"),
    [
        ({"fingerprint_matches": False}, "fingerprint_still_valid"),
        ({"slot_available": False}, "slot_available"),
        ({"is_git_repo": False}, "workspace_is_git_repo"),
        ({"head": "b" * 40}, "head_matches_planning_base_commit"),
        ({"head": None}, "head_matches_planning_base_commit"),
        ({"attempts": 2, "max_attempts": 2}, "attempts_below_max"),
    ],
)
def test_cada_guarda_de_entrada_falha_pelo_proprio_motivo(
    mutacao: dict[str, object], guarda_esperada: str
) -> None:
    """As seis guardas de [02] §4, uma a uma, com um provador que passaria."""
    with pytest.raises(TransitionGuardFailed) as exc:
        check_entry_guard(_facts(**mutacao), prover=_AcceptingProver())

    assert exc.value.guard == guarda_esperada


def test_a_ordem_das_guardas_poupa_a_prova_cara() -> None:
    """Capability é a última: uma comparação de string não deve custar um processo.

    Com `head` divergente **e** sem provador, o motivo tem de ser o `HEAD` — provando que a
    prova de capability nem foi tentada.
    """
    with pytest.raises(TransitionGuardFailed) as exc:
        check_entry_guard(_facts(head="b" * 40), prover=None)

    assert exc.value.guard == "head_matches_planning_base_commit"


# ---------------------------------------------- E6-AUD-012: prova positiva **e** hash


class _MismatchedProver:
    """Prova positiva com o hash de **outro** perfil. É o cenário exato do finding.

    Um adaptador que comprovasse `execute_commands = enabled` e respondesse
    `proven = True` entrava em execução: a guarda conferia que ele **respondeu**, não o que
    ele respondeu.
    """

    def __init__(self, effective_profile_hash: str | None) -> None:
        self.effective_profile_hash = effective_profile_hash

    def prove(self, required_profile_hash: str) -> CapabilityProof:
        del required_profile_hash
        return CapabilityProof(proven=True, effective_profile_hash=self.effective_profile_hash)


@pytest.mark.parametrize(
    ("rotulo", "prover_factory", "guarda_esperada"),
    [
        # E7.6: a prova legada (sem observação verificada) deixou de ser aceita.
        (
            "proven=True + hash igual, sem observação (legada)",
            lambda: _AcceptingProver(),
            "capability_profile_proven",
        ),
        ("proven=True + hash igual + observação completa", lambda: FixedProver(full_proof()), None),
        (
            "proven=True + hash diferente",
            lambda: _MismatchedProver("0" * 64),
            "capability_profile_matches_approved",
        ),
        (
            "proven=True + hash ausente",
            lambda: _MismatchedProver(None),
            "capability_profile_matches_approved",
        ),
        ("proven=False + hash igual", lambda: _RefusingProver(), "capability_profile_proven"),
    ],
)
def test_a_guarda_exige_prova_positiva_e_hash_efetivo_compativel(
    rotulo: str,
    prover_factory: Callable[[], object],
    guarda_esperada: str | None,
) -> None:
    """Os quatro desfechos de [02] §7 + [ADR-0009]: um sucesso e três bloqueios.

    `_RefusingProver` devolve `effective_profile_hash=None`, mas o motivo tem de ser
    `capability_profile_proven` — `proven = False` é conferido **antes**, e trocar a ordem
    daria ao operador o diagnóstico errado sobre o que o adaptador fez.
    """
    prover = prover_factory()

    binding = expected_binding()

    if guarda_esperada is None:
        check_entry_guard(_facts(), prover=prover, expected_binding=binding)  # type: ignore[arg-type]
        return

    with pytest.raises(TransitionGuardFailed) as exc:
        check_entry_guard(_facts(), prover=prover, expected_binding=binding)  # type: ignore[arg-type]

    assert exc.value.guard == guarda_esperada, rotulo


def test_o_hash_requerido_chega_ao_provador() -> None:
    """O provador recebe o perfil **requerido**, e é contra ele que o efetivo é comparado.

    Sem isto, a comparação poderia estar acontecendo contra um valor recalculado por dentro
    da guarda, e um adaptador honesto que devolvesse o hash do que lhe foi pedido ainda
    assim falharia.
    """
    recebidos: list[str] = []

    class _Registrando:
        def prove(self, required_profile_hash: str) -> CapabilityProof:
            recebidos.append(required_profile_hash)
            return full_proof()

    check_entry_guard(_facts(), prover=_Registrando(), expected_binding=expected_binding())

    assert recebidos == [tool_profile_hash()]


# ---------------------------------------- E6-AUD-010: a guarda de `needs_fix → approved`


def test_needs_fix_com_tentativas_esgotadas_bloqueia_pelo_motivo_certo() -> None:
    with pytest.raises(TransitionGuardFailed) as exc:
        check_needs_fix_guard(attempts=2, max_attempts=2, stale_entry_ids=(), risk_is_high=False)

    assert exc.value.guard == "attempts_below_max"


def test_needs_fix_com_tentativa_disponivel_ainda_exige_contexto_reverificado() -> None:
    """As duas metades são independentes: passar numa não dispensa a outra."""
    with pytest.raises(TransitionGuardFailed) as exc:
        check_needs_fix_guard(
            attempts=0, max_attempts=2, stale_entry_ids=("e1",), risk_is_high=True
        )

    assert exc.value.guard == "no_stale_entry_when_high_risk"


def test_needs_fix_com_tentativa_e_contexto_fresco_passa() -> None:
    check_needs_fix_guard(attempts=0, max_attempts=2, stale_entry_ids=(), risk_is_high=True)


# ------------------------------------------------------------- guarda de aprovação


def test_high_risk_com_entrada_stale_bloqueia_a_aprovacao() -> None:
    """[02] §4: "nenhuma entrada `stale` selecionada se `risk = high`"."""
    with pytest.raises(TransitionGuardFailed) as exc:
        check_approval_guard(stale_entry_ids=["e1"], risk_is_high=True)

    assert exc.value.guard == "no_stale_entry_when_high_risk"


def test_risco_nao_high_com_stale_e_apenas_um_aviso() -> None:
    """Em `low`/`medium`, `stale` é aviso de [06] §4, não impedimento."""
    check_approval_guard(stale_entry_ids=["e1", "e2"], risk_is_high=False)


def test_high_risk_sem_stale_aprova() -> None:
    check_approval_guard(stale_entry_ids=[], risk_is_high=True)
