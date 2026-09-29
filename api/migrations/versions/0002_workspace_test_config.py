"""dev_workspace.test_config: a TestPolicy por workspace, em JSON estruturado

[04](../../../docs/architecture/04-safety-and-git-runtime.md) §6 define a `TestPolicy`:
executáveis permitidos, `argv` **configurado/normalizado — nunca string de shell livre**,
`cwd`, timeout, allowlist de ambiente, política de rede declarada e limites de saída. Os
hashes derivados dela (`command_hash`, `policy_hash`) entram no `test_binding` do
`execution_fingerprint` ([02] §7).

A coluna é **nullable**: `NULL` significa "este workspace não tem Test Runner
configurado", e nesse caso os três campos de `test_binding` saem `null` — a invariante
tudo-ou-nada de [02] §7. Um default não-nulo aqui afirmaria uma política que ninguém
escolheu.

`argv` é `list[str]` e não string exatamente pelo motivo de [04] §6: uma string de shell
teria de ser dividida por alguém, e quem divide decide gramática de shell — a mesma classe
de erro que E2-AUD-003 fechou para glob.

Revision ID: 0002_workspace_test_config
Revises: 0001_initial_schema
Create Date: 2026-09-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_workspace_test_config"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("dev_workspace", sa.Column("test_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("dev_workspace", "test_config")
