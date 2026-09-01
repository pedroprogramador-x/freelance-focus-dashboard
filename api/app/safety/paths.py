"""Decisões de path — puras.

Três funções, na ordem do fluxo de [04](../../../docs/architecture/04-safety-and-git-runtime.md) §4:

1. ``prevalidate_path_syntax`` — só string, rejeita cedo e barato;
2. ``decide_path`` — decide sobre `PathFacts` coletados por `app.path_runtime`;
3. ``decide_post_open`` — decide de novo, sobre os fatos do handle já aberto.

Nenhuma delas toca o filesystem. A contenção **não** é decidida por prefixo textual: ela
chega pronta em ``PathFacts.contained``, calculada sobre caminhos já canonizados. O único
uso de prefixo aqui é derivar o caminho relativo para a política de segredos, e isso opera
sobre valores que `path_runtime` já resolveu.

Todas as regras valem em **todas** as plataformas. Uma sintaxe UNC não é perigosa só no
Windows; e aplicar o mesmo conjunto em todo lugar mantém a decisão determinística e os
testes independentes do sistema onde rodam.
"""

from __future__ import annotations

import re
import sys
from enum import Enum

from app.safety.policy import SafetyPolicy
from app.safety.redaction import redact_path
from app.safety.secrets import SecretVerdict, classify_path_secrecy
from app.safety.types import PathFacts, SafetyDecision


class PathForm(str, Enum):
    """Forma sintática de um caminho.

    Distinguir as cinco formas é o que impede `allow_absolute=True` de liberar, por
    tabela, coisas que **não** são caminho absoluto qualificado (E2-AUD-005).
    """

    RELATIVE = "relative"
    #: `C:\\foo` no Windows, `/foo` em POSIX. A única forma que `allow_absolute` libera.
    ABSOLUTE_QUALIFIED = "absolute_qualified"
    #: `\\foo` — depende do **drive corrente**. Nunca liberado.
    ROOT_RELATIVE = "root_relative"
    #: `C:foo` — depende do **diretório corrente daquele drive**. Nunca liberado.
    DRIVE_RELATIVE = "drive_relative"
    UNC = "unc"
    DEVICE_NAMESPACE = "device_namespace"


class PathIntent(str, Enum):
    """Intenção da operação.

    Na E2 as duas seguem exatamente as mesmas regras: a diferença — escrita só dentro da
    worktree da task — pertence ao Full Safety Runtime (E7). O parâmetro existe para que
    a assinatura não mude depois e para que o motivo registrado diga o que se tentou.
    """

    READ = "read"
    WRITE = "write"


_RESERVED_STEMS = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)

_SHORT_NAME_ALIAS = re.compile(r"[^~/\\]{1,6}~\d{1,4}(\.[^.~/\\]{1,3})?", re.IGNORECASE)
_DRIVE_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_DRIVE_RELATIVE = re.compile(r"^[A-Za-z]:(?![\\/])")


def classify_path_form(value: str, *, windows_semantics: bool | None = None) -> PathForm:
    """Classifica a forma sintática, sem tocar o filesystem.

    ``windows_semantics`` decide o único caso genuinamente ambíguo: um caminho iniciado
    por ``/``. No Windows ele é **root-relative** — resolve contra o drive corrente, que
    o processo pode mudar. Em POSIX é um absoluto legítimo. O parâmetro é explícito para
    que os testes verifiquem os dois comportamentos em qualquer sistema, em vez de
    dependerem de onde a suíte roda.

    Um ``\\`` inicial é tratado como root-relative em **todas** as plataformas: o resto do
    módulo já normaliza ``\\`` como separador, e classificá-lo de outro jeito abriria a
    porta que E2-AUD-005 fechou.
    """
    windows = sys.platform == "win32" if windows_semantics is None else windows_semantics

    if value.startswith(("\\\\?\\", "\\\\.\\")):
        return PathForm.DEVICE_NAMESPACE
    if value.startswith(("\\\\", "//")):
        return PathForm.UNC
    if _DRIVE_ABSOLUTE.match(value):
        return PathForm.ABSOLUTE_QUALIFIED
    if _DRIVE_RELATIVE.match(value):
        return PathForm.DRIVE_RELATIVE
    if value.startswith("\\"):
        return PathForm.ROOT_RELATIVE
    if value.startswith("/"):
        return PathForm.ROOT_RELATIVE if windows else PathForm.ABSOLUTE_QUALIFIED
    return PathForm.RELATIVE


def _deny(rule_id: str, reason: str, subject: str) -> SafetyDecision:
    return SafetyDecision(
        allow=False, rule_id=rule_id, reason=reason, subject_redacted=redact_path(subject)
    )


def _allow(rule_id: str, reason: str, subject: str) -> SafetyDecision:
    return SafetyDecision(
        allow=True, rule_id=rule_id, reason=reason, subject_redacted=redact_path(subject)
    )


def prevalidate_path_syntax(
    requested: str,
    *,
    policy: SafetyPolicy | None = None,
    allow_absolute: bool = False,
    windows_semantics: bool | None = None,
) -> SafetyDecision:
    """Rejeita sintaxes perigosas antes de qualquer IO.

    ``allow_absolute`` existe porque dois casos legítimos usam caminho absoluto: o
    ``local_path`` de um `DevWorkspace` (digitado pelo usuário) e a raiz de uma worktree.
    Tudo o mais — tool request, `source_ref` — é relativo ao workspace.

    **`allow_absolute` libera exclusivamente `PathForm.ABSOLUTE_QUALIFIED`.** Root-relative,
    drive-relative, UNC e device namespace continuam recusados: nenhum deles é um caminho
    absoluto, todos dependem de estado mutável do processo ou saem da máquina.
    """
    active = policy or SafetyPolicy()

    if not requested or not requested.strip():
        return _deny("path.empty", "caminho vazio", requested)

    if "\x00" in requested:
        return _deny("path.nul_byte", "caminho contém byte nulo", requested)

    if any(ord(char) < 32 for char in requested):
        return _deny("path.control_char", "caminho contém caractere de controle", requested)

    if len(requested.encode("utf-8")) > active.max_path_bytes:
        return _deny(
            "path.too_long",
            f"caminho excede {active.max_path_bytes} bytes",
            requested,
        )

    form = classify_path_form(requested, windows_semantics=windows_semantics)

    if form is PathForm.DEVICE_NAMESPACE:
        return _deny("path.device_namespace", "device namespace do Windows", requested)

    if form is PathForm.UNC:
        return _deny("path.unc", "caminho UNC", requested)

    if form is PathForm.DRIVE_RELATIVE:
        return _deny(
            "path.drive_relative",
            "caminho relativo a drive (`C:arquivo`) resolve contra o diretório corrente do drive",
            requested,
        )

    if form is PathForm.ROOT_RELATIVE:
        # Recusado **mesmo com `allow_absolute=True`** (E2-AUD-005): `\arquivo` não é
        # absoluto — resolve contra o drive corrente, que qualquer chamada pode mudar.
        return _deny(
            "path.root_relative",
            "caminho relativo à raiz depende do drive corrente do processo",
            requested,
        )

    if form is PathForm.ABSOLUTE_QUALIFIED and not allow_absolute:
        return _deny(
            "path.absolute_not_allowed",
            "caminho absoluto onde só relativo é aceito",
            requested,
        )

    if requested.startswith("~"):
        return _deny("path.home_reference", "referência ao diretório home (`~`)", requested)

    body = requested
    if _DRIVE_ABSOLUTE.match(body):
        body = body[2:]

    components = [part for part in re.split(r"[\\/]+", body) if part not in ("", ".")]
    if not components:
        return _deny("path.no_components", "caminho sem componente utilizável", requested)

    for component in components:
        if component == "..":
            return _deny("path.parent_traversal", "componente `..`", requested)

        if component.endswith((".", " ")):
            return _deny(
                "path.trailing_dot_or_space",
                "componente termina com ponto ou espaço; o Windows os remove e cria um alias",
                requested,
            )

        if ":" in component:
            return _deny(
                "path.alternate_data_stream",
                "componente contém `:` (alternate data stream)",
                requested,
            )

        if component.split(".", 1)[0].lower() in _RESERVED_STEMS:
            return _deny(
                "path.reserved_name",
                "nome de dispositivo reservado do Windows",
                requested,
            )

        if _SHORT_NAME_ALIAS.fullmatch(component):
            return _deny(
                "path.short_name_alias",
                "possível alias 8.3 (`PROGRA~1`); recusado por conservadorismo",
                requested,
            )

    return _allow("path.syntax_ok", "sintaxe aceita", requested)


def _relative_to_root(facts: PathFacts) -> str | None:
    """Caminho relativo, derivado de valores **já canonizados** por `path_runtime`.

    Não é verificação de contenção — essa vem pronta em ``facts.contained``. É só a chave
    que a política de segredos precisa.
    """
    target = facts.canonical_target
    if target is None:
        return None

    root = facts.canonical_root.replace("\\", "/").rstrip("/")
    normalized = target.replace("\\", "/")

    if normalized.lower() == root.lower():
        return ""
    prefix = f"{root}/"
    if normalized.lower().startswith(prefix.lower()):
        return normalized[len(prefix) :]
    return None


def decide_path(
    facts: PathFacts,
    *,
    policy: SafetyPolicy | None = None,
    intent: PathIntent = PathIntent.READ,
) -> SafetyDecision:
    """Decide sobre fatos coletados. Fecha em qualquer dúvida."""
    active = policy or SafetyPolicy()
    subject = facts.requested_path

    if facts.inspection_error is not None:
        return _deny(
            "path.inspection_failed",
            f"inspeção falhou: {facts.inspection_error}",
            subject,
        )

    if facts.is_device_namespace.is_true:
        return _deny("path.device_namespace", "device namespace do Windows", subject)

    if facts.is_unc.is_true:
        return _deny("path.unc", "caminho UNC", subject)

    if facts.is_drive_relative.is_true:
        return _deny("path.drive_relative", "caminho relativo a drive", subject)

    if facts.contained.is_false:
        return _deny("path.escapes_root", "alvo canônico fora da raiz do workspace", subject)

    if facts.contained.is_unknown:
        return _deny(
            "path.containment_unverified",
            "não foi possível verificar contenção; fail closed",
            subject,
        )

    if (
        facts.volume is not None
        and facts.root_volume is not None
        and facts.volume != facts.root_volume
    ):
        return _deny("path.cross_volume", "alvo em volume diferente da raiz", subject)

    if facts.ancestor_link_outside_root.is_true:
        return _deny(
            "path.ancestor_link_escapes_root",
            "um ancestral é link/junction que sai da raiz",
            subject,
        )

    if facts.ancestor_link_outside_root.is_unknown and active.require_verified_link_status:
        return _deny(
            "path.ancestor_link_unverified",
            "estado de link dos ancestrais não verificado; fail closed",
            subject,
        )

    link_facts = {
        "symlink": facts.is_symlink,
        "junction": facts.is_junction,
        "reparse_point": facts.is_reparse_point,
    }
    for label, value in link_facts.items():
        if value.is_true and not active.allow_links_inside_root:
            return _deny(f"path.{label}_denied", f"alvo é {label}", subject)
        if value.is_unknown and active.require_verified_link_status:
            return _deny(
                f"path.{label}_unverified",
                f"estado de {label} não verificado; fail closed",
                subject,
            )

    relative = _relative_to_root(facts)
    if relative is None:
        return _deny(
            "path.relative_underivable",
            "não foi possível derivar caminho relativo à raiz",
            subject,
        )

    if relative:
        classification = classify_path_secrecy(relative, active.secret_policy())
        if classification.verdict is SecretVerdict.SECRET:
            return _deny(
                "path.secret_denied",
                f"caminho classificado como segredo (padrão `{classification.matched_pattern}`)",
                subject,
            )

    return _allow("path.allowed", f"contenção e política satisfeitas para {intent.value}", subject)


def decide_post_open(
    facts: PathFacts,
    *,
    policy: SafetyPolicy | None = None,
) -> SafetyDecision:
    """Revalida a partir do objeto **já aberto**.

    Estreita a janela TOCTOU comparando a identidade do objeto aberto com a inspecionada.
    Não a fecha — o risco residual está declarado em [04] §4 e não é contornado aqui.

    Escopo E2: alvos **pré-existentes** (leitura). O fluxo de criação/escrita, onde não há
    identidade prévia para comparar, pertence ao Full Safety Runtime (E7).
    """
    active = policy or SafetyPolicy()
    subject = facts.requested_path

    if facts.inspection_error is not None:
        return _deny(
            "path.post_open_inspection_failed",
            f"inspeção pós-abertura falhou: {facts.inspection_error}",
            subject,
        )

    if facts.post_open_identity is None:
        if active.require_verified_link_status:
            return _deny(
                "path.post_open_unverified",
                "identidade pós-abertura indisponível; fail closed",
                subject,
            )
        return _allow("path.post_open_skipped", "verificação pós-abertura indisponível", subject)

    if facts.target_identity is None:
        return _deny(
            "path.post_open_no_baseline",
            "sem identidade prévia para comparar; criação de arquivo é escopo da E7",
            subject,
        )

    if facts.target_identity != facts.post_open_identity:
        return _deny(
            "path.toctou_recheck_failed",
            "objeto aberto difere do inspecionado; caminho trocado entre inspeção e abertura",
            subject,
        )

    if facts.post_open_target is not None:
        root = facts.canonical_root.replace("\\", "/").rstrip("/").lower()
        opened = facts.post_open_target.replace("\\", "/").lower()
        if opened != root and not opened.startswith(f"{root}/"):
            return _deny(
                "path.post_open_escapes_root",
                "caminho re-derivado do handle está fora da raiz",
                subject,
            )

    return _allow("path.post_open_ok", "identidade estável entre inspeção e abertura", subject)
