"""`TestPolicy` — o documento de `DevWorkspace.test_config` e os hashes derivados dele.

## Por que este módulo vive em `safety/`

[04](../../../docs/architecture/04-safety-and-git-runtime.md) §5 é explícito na tabela das
três peças de política: `SafetyPolicy` são "allowlists, denylists, limites, globs de
segredo, perfis de capability, **`TestPolicy`**", e onde elas vivem é **`safety/`**. [01]
§2 repete a lista no contrato do módulo.

A consequência prática é o que resolve o problema de fronteira: `workspace/` **grava** a
coluna e `orchestrator/` **consome** o documento, e os dois podem importar `safety/`
([01] §2). O schema num dos dois faria o outro depender de uma definição que não controla —
e `workspace/ → orchestrator/` nem sequer é uma aresta permitida.

Este módulo é **puro**, como todo o resto de `safety/`: valida forma e calcula hash. Ele
não lê arquivo, não abre processo e não sabe o que é um Test Runner — supervisionar o
processo é `agent_runtime/` (E7), e `cwd_mode` existe justamente para que nenhum caminho
físico precise aparecer aqui.

## Os hashes

[04] §6 define os sete itens; [02](../../../docs/architecture/02-data-model.md) §7 define
como eles viram o `test_binding` do `execution_fingerprint`:

    test_binding = { runner, command_hash, policy_hash }
    command_hash = sha256(canonical_json({executable, argv}))
    policy_hash  cobre executáveis permitidos, argv configurado, cwd, timeout,
                 allowlist de ambiente, política de rede declarada e limites de saída

## `argv` é lista, e `cwd_mode` é semântica

Os dois campos que mais facilmente seriam string são deliberadamente não-string:

* **`argv: list[str]`** — [04] §6: "configurado/normalizado; **nunca string de shell
  livre**". Uma string teria de ser dividida por alguém, e quem divide escolhe uma
  gramática de shell. É a mesma classe de defeito que E2-AUD-003 fechou para glob: duas
  gramáticas discordando sobre o mesmo texto.
* **`cwd_mode: "task_worktree"`** — um enum de **semântica**, não um caminho físico. O
  caminho real da worktree só existe em E7, é diferente em cada máquina e muda a cada
  task; gravá-lo aqui poria um transitório dentro de um hash que [02] §7 exige estável
  ("proibidos: timestamps, PIDs, caminhos que variam entre máquinas").

## `network_policy` só aceita `"unrestricted"`

[04] §6 é explícito: a política de rede é **declarada, não imposta tecnicamente na V1**, e
§6 declara em seguida o que a V1 não oferece — "impedir que um script de teste comprometido
[…] abra rede". Aceitar `"disabled"` gravaria no fingerprint a afirmação de uma propriedade
que nenhum código faz valer. Um valor único e honesto é melhor do que dois valores dos
quais um mente.

## A projeção pública é redigida; a operacional não (E6-AUD-007)

`runner_id`, `executable` e cada item de `argv` são **strings livres**. O comentário
original do schema dizia que a configuração não pode carregar segredo porque
`env_allowlist` guarda só nomes de variáveis — verdadeiro para aquele campo, e falso para
os outros três: nada impede alguém de configurar `argv: ["--token", "sk-…"]`, e a resposta
de `PATCH`/`GET`/listagem devolvia o valor inteiro ao browser, contra [01] §4 e [06] §2
("todo corpo JSON de `/api/*` passa pelo redator").

`redacted_document()` é a projeção de **saída**. Ela não toca em nada operacional: os bytes
crus continuam na coluna, `command_hash` e `policy_hash` continuam calculados sobre eles, e
o `execution_fingerprint` continua afirmando o comando que de fato será executado. Redigir
antes de hashear seria o defeito oposto — o fingerprint deixaria de identificar o comando
real, e duas configurações distintas cujo segredo caísse na mesma máscara colidiriam.

A consequência de existir uma projeção diferente do documento é que **ela não é um
documento de entrada válido**. Um cliente que fizesse `GET` e devolvesse o corpo num
`PATCH` gravaria a máscara no lugar do comando, e o Test Runner passaria a "rodar"
`«redigido»` — em silêncio, porque a máscara é uma string perfeitamente válida para o
schema. `parse_test_policy` recusa qualquer campo que a contenha: é o único ponto por onde
um documento entra, e recusar ali é mais barato do que descobrir na E7 por que o teste não
roda.

## `runner_id` é declarado, nunca descoberto

O identificador lógico/versionado do Test Runner exigido entra no fingerprint como
`test_binding.runner`. Ele é **declarado na configuração** — trocar o runner por outro,
ainda que rode o mesmo comando, muda o fingerprint e invalida a aprovação. Descobri-lo em
runtime faria o fingerprint aprovado depender de o que estivesse instalado na hora de
executar, que é exatamente o contrário do que ele existe para garantir.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.safety.canonical import canonical_sha256
from app.safety.redaction import REDACTED, redact_document


class InvalidTestPolicy(ValueError):
    """Documento de `TestPolicy` malformado.

    `ValueError` e não uma exceção de domínio com `status_code`: `safety/` é política pura
    e não conhece HTTP ([01] §2). Quem traduz para `{code, message}` é o módulo consumidor
    — `workspace.errors.InvalidTestConfig` na escrita, `orchestrator.errors.InvalidTestConfig`
    na leitura —, e os dois usam o **mesmo** `code` para que o cliente veja uma resposta só,
    independentemente de qual caminho recusou.

    Mesmo desenho de `SafetyDecision` → `context_engine.errors.InvalidSourceRefs`: o kernel
    produz o veredito, o consumidor o embrulha na própria hierarquia.
    """


#: Versão da forma do documento. Igual em espírito a `MANIFEST_HASH_VERSION`: impede um
#: `policy_hash` desta versão de colidir com o de uma versão futura que acrescente campos.
TEST_POLICY_VERSION = 1

#: [04] §6: declarada, **não imposta**. Ver o docstring do módulo.
NETWORK_POLICY_UNRESTRICTED = "unrestricted"

#: Semântica de `cwd` de [04] §6 ("`cwd`: a worktree da task"). Nunca um caminho físico.
CWD_MODE_TASK_WORKTREE = "task_worktree"

_REQUIRED_KEYS = frozenset(
    {
        "runner_id",
        "executable",
        "argv",
        "timeout_seconds",
        "env_allowlist",
        "network_policy",
        "output_limits",
        "cwd_mode",
    }
)

_OUTPUT_LIMIT_KEYS = frozenset({"max_stdout_bytes", "max_stderr_bytes"})

#: Teto defensivo. Um `timeout` absurdo não é inválido por si, mas um valor fora desta
#: faixa quase certamente é erro de unidade (milissegundos gravados como segundos).
_MAX_TIMEOUT_SECONDS = 24 * 60 * 60

#: Teto de bytes capturados por stream. [04] §6: "limites de saída: tamanho máximo
#: capturado". 64 MiB é folgado para um relatório de teste e barra um log infinito.
_MAX_OUTPUT_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class TestPolicy:
    """A `TestPolicy` de um workspace, já validada. Imutável."""

    runner_id: str
    executable: str
    argv: tuple[str, ...]
    timeout_seconds: int
    env_allowlist: tuple[str, ...]
    network_policy: str
    max_stdout_bytes: int
    max_stderr_bytes: int
    cwd_mode: str

    def as_document(self) -> dict[str, Any]:
        """A forma que vai para a coluna JSON — a mesma que entrou, normalizada.

        `env_allowlist` sai ordenado: é um **conjunto** de nomes, e [02] §7 manda ordenar
        listas sem semântica de ordem antes de serializar. `argv` **não** sai ordenado, e
        essa assimetria é o ponto: a ordem de `argv` é semântica (`pytest -q tests` não é
        `tests -q pytest`) e [02] §7 nomeia `argv` entre os arrays cuja ordem é preservada.
        """
        return {
            "runner_id": self.runner_id,
            "executable": self.executable,
            "argv": list(self.argv),
            "timeout_seconds": self.timeout_seconds,
            "env_allowlist": sorted(self.env_allowlist),
            "network_policy": self.network_policy,
            "output_limits": {
                "max_stdout_bytes": self.max_stdout_bytes,
                "max_stderr_bytes": self.max_stderr_bytes,
            },
            "cwd_mode": self.cwd_mode,
        }

    def command_hash(self) -> str:
        """[02] §7: `sha256(canonical_json({executable, argv}))`.

        Só o comando. Separado do `policy_hash` de propósito: trocar `pytest -q` por
        `pytest -x` muda o comando sem mudar a política, e a UI de [06] §4 diz **qual
        campo** mudou — dois hashes respondem isso, um só não.
        """
        return canonical_sha256({"executable": self.executable, "argv": list(self.argv)})

    def policy_hash(self) -> str:
        """[02] §7: cobre "executáveis permitidos, `argv` configurado, `cwd`, timeout,
        allowlist de ambiente, política de rede declarada e limites de saída".

        `executable` entra aqui **além** de entrar no `command_hash` porque [04] §6 trata
        a allowlist de executáveis como item de política, distinto do comando concreto. Na
        V1 a allowlist tem exatamente um elemento — o `executable` configurado — e gravá-lo
        nos dois lugares é o que mantém o `policy_hash` completo quando a allowlist crescer.
        """
        return canonical_sha256(
            {
                "v": TEST_POLICY_VERSION,
                "allowed_executables": [self.executable],
                "argv": list(self.argv),
                "cwd_mode": self.cwd_mode,
                "timeout_seconds": self.timeout_seconds,
                "env_allowlist": sorted(self.env_allowlist),
                "network_policy": self.network_policy,
                "output_limits": {
                    "max_stdout_bytes": self.max_stdout_bytes,
                    "max_stderr_bytes": self.max_stderr_bytes,
                },
            }
        )

    def as_binding(self) -> dict[str, Any]:
        """O `test_binding` de [02] §7, com os três campos preenchidos."""
        return {
            "runner": self.runner_id,
            "command_hash": self.command_hash(),
            "policy_hash": self.policy_hash(),
        }


#: Os únicos campos cujo valor é publicável **como está**, e só quando é exatamente o valor
#: único que a V1 aceita. Não é "estes campos são enum, logo são seguros": é "este valor
#: específico é conhecido, logo publicá-lo não revela nada".
#:
#: A diferença importa porque a coluna é JSON livre no banco (E6-AUD2-005). Confiar no
#: **nome** do campo devolvia cru qualquer coisa gravada em `network_policy` por uma versão
#: anterior do schema, por uma escrita direta ou por um bug — inclusive uma credencial. Um
#: valor que não é o esperado não é enum coisa nenhuma; é texto livre, e vai para o redator
#: como todo o resto.
_PUBLISHABLE_CONSTANTS: dict[str, str] = {
    "network_policy": NETWORK_POLICY_UNRESTRICTED,
    "cwd_mode": CWD_MODE_TASK_WORKTREE,
}


def redacted_document(document: Any) -> Any:
    """A projeção **pública** de `test_config`, para toda resposta HTTP. Ver o docstring.

    Tudo passa por `redact_document` — o redator recursivo canônico —, com exatamente uma
    exceção: os dois campos de `_PUBLISHABLE_CONSTANTS`, e só quando carregam o valor único
    que a V1 aceita. A regra é *allowlist de valor*, não *allowlist de campo*.

    Um documento que **não** seja o dicionário esperado atravessa redigido do mesmo jeito:
    a coluna é JSON livre no banco, e uma linha malformada gravada por uma versão anterior
    do schema não pode virar o caminho pelo qual um valor cru escapa. Não conseguir
    interpretar não é o mesmo que estar seguro.

    Nada aqui caminha sobre a estrutura por conta própria. A versão anterior tinha a sua
    própria recursão e tratava `output_limits` como um `dict(value)` raso — o que devolvia
    cru qualquer string aninhada nele. A recursão mora num lugar só.
    """
    if not isinstance(document, dict):
        return redact_document(document)

    return {
        key: value if _PUBLISHABLE_CONSTANTS.get(key) == value else redact_document(value)
        for key, value in document.items()
    }


#: `test_binding` quando o workspace **não** tem Test Runner configurado ([02] §7).
#: Os três campos `null`, nunca a chave ausente: [02] §7 exige `null` explícito para que
#: "sem test runner" e "test runner X" não colidam no mesmo hash.
NULL_TEST_BINDING: dict[str, Any] = {"runner": None, "command_hash": None, "policy_hash": None}


def _reject_redacted(key: str, value: str) -> None:
    """Recusa um campo que carregue a máscara de redação. Ver o docstring do módulo.

    Não é uma checagem de segurança — é uma checagem de **integridade**: quem envia
    `«redigido»` está devolvendo a projeção de leitura em vez do valor real, e gravá-la
    produziria um `command_hash` que afirma um comando inexistente.
    """
    if REDACTED in value:
        raise InvalidTestPolicy(
            f"test_config.{key} contém a máscara de redação: a resposta de leitura é uma "
            "projeção redigida e não pode ser reenviada como configuração. Envie o valor real"
        )


def _require_str(document: dict[str, Any], key: str, *, max_length: int) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidTestPolicy(f"test_config.{key} precisa ser uma string não vazia")
    stripped = value.strip()
    if len(stripped) > max_length:
        raise InvalidTestPolicy(f"test_config.{key} excede {max_length} caracteres")
    _reject_redacted(key, stripped)
    return stripped


def _require_str_list(document: dict[str, Any], key: str, *, allow_empty: bool) -> tuple[str, ...]:
    value = document.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidTestPolicy(f"test_config.{key} precisa ser uma lista de strings")
    if not value and not allow_empty:
        raise InvalidTestPolicy(f"test_config.{key} não pode ser vazio")
    if any(not item.strip() for item in value):
        raise InvalidTestPolicy(f"test_config.{key} não pode conter item vazio")
    itens = tuple(item.strip() for item in value)
    for item in itens:
        _reject_redacted(key, item)
    return itens


def _require_int(document: dict[str, Any], key: str, *, minimum: int, maximum: int) -> int:
    value = document.get(key)
    # `bool` é subclasse de `int` em Python: sem esta checagem, `True` viraria `1` segundos.
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidTestPolicy(f"test_config.{key} precisa ser um inteiro")
    if not minimum <= value <= maximum:
        raise InvalidTestPolicy(f"test_config.{key} precisa estar entre {minimum} e {maximum}")
    return value


def parse_test_policy(document: Any) -> TestPolicy | None:
    """Valida o documento de `DevWorkspace.test_config`. `None` entra e `None` sai.

    `None` é o estado legítimo "sem Test Runner configurado" ([02] §7), não um erro. Um
    dicionário **vazio** é erro: quem grava `{}` quase certamente quis `null` e recebeu
    silenciosamente uma política sem nenhum campo.

    Levanta `InvalidTestConfig` (**422**) em qualquer outra forma. A validação é
    tudo-ou-nada por construção: ou o documento produz um `TestConfig` com os oito campos,
    ou não produz nenhum. É o que torna a invariante de [02] §7 — os três campos de
    `test_binding` todos `null` ou todos não-`null` — verdadeira por tipo, e não por
    disciplina de quem monta o fingerprint.
    """
    if document is None:
        return None

    if not isinstance(document, dict):
        raise InvalidTestPolicy("test_config precisa ser um objeto JSON ou null")

    if not document:
        raise InvalidTestPolicy(
            "test_config vazio não é uma política; use null para 'sem Test Runner configurado'"
        )

    unknown = sorted(set(document) - _REQUIRED_KEYS)
    if unknown:
        raise InvalidTestPolicy(f"test_config tem chave(s) desconhecida(s): {', '.join(unknown)}")

    missing = sorted(_REQUIRED_KEYS - set(document))
    if missing:
        raise InvalidTestPolicy(f"test_config sem a(s) chave(s): {', '.join(missing)}")

    network_policy = _require_str(document, "network_policy", max_length=64)
    if network_policy != NETWORK_POLICY_UNRESTRICTED:
        raise InvalidTestPolicy(
            f"test_config.network_policy só aceita `{NETWORK_POLICY_UNRESTRICTED}` na V1: "
            "[04] §6 declara a política de rede, sem impô-la tecnicamente, e gravar outro "
            "valor prometeria uma propriedade que nenhum código faz valer"
        )

    cwd_mode = _require_str(document, "cwd_mode", max_length=64)
    if cwd_mode != CWD_MODE_TASK_WORKTREE:
        raise InvalidTestPolicy(
            f"test_config.cwd_mode só aceita `{CWD_MODE_TASK_WORKTREE}` na V1 ([04] §6)"
        )

    limits = document.get("output_limits")
    if not isinstance(limits, dict):
        raise InvalidTestPolicy("test_config.output_limits precisa ser um objeto JSON")
    unknown_limits = sorted(set(limits) - _OUTPUT_LIMIT_KEYS)
    if unknown_limits:
        raise InvalidTestPolicy(
            f"test_config.output_limits tem chave(s) desconhecida(s): {', '.join(unknown_limits)}"
        )
    missing_limits = sorted(_OUTPUT_LIMIT_KEYS - set(limits))
    if missing_limits:
        raise InvalidTestPolicy(
            f"test_config.output_limits sem a(s) chave(s): {', '.join(missing_limits)}"
        )

    return TestPolicy(
        runner_id=_require_str(document, "runner_id", max_length=128),
        executable=_require_str(document, "executable", max_length=512),
        argv=_require_str_list(document, "argv", allow_empty=True),
        timeout_seconds=_require_int(
            document, "timeout_seconds", minimum=1, maximum=_MAX_TIMEOUT_SECONDS
        ),
        # Allowlist vazia é legítima e é o caso mais restritivo: nenhuma variável do
        # ambiente do backend chega ao processo de teste ([04] §5, "ambiente dos filhos é
        # allowlist").
        env_allowlist=_require_str_list(document, "env_allowlist", allow_empty=True),
        network_policy=network_policy,
        max_stdout_bytes=_require_int(
            limits, "max_stdout_bytes", minimum=1, maximum=_MAX_OUTPUT_BYTES
        ),
        max_stderr_bytes=_require_int(
            limits, "max_stderr_bytes", minimum=1, maximum=_MAX_OUTPUT_BYTES
        ),
        cwd_mode=cwd_mode,
    )


__all__ = [
    "CWD_MODE_TASK_WORKTREE",
    "NETWORK_POLICY_UNRESTRICTED",
    "NULL_TEST_BINDING",
    "TEST_POLICY_VERSION",
    "InvalidTestPolicy",
    "TestPolicy",
    "parse_test_policy",
    "redacted_document",
]
