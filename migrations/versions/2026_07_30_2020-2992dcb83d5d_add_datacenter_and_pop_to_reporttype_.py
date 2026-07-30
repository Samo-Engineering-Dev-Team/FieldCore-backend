"""add datacenter and pop to reporttype enum

Revision ID: 2992dcb83d5d
Revises: 134c4ef50825
Create Date: 2026-07-30 20:20:23.580679

Supersedes scripts/0044_add_datacenter_pop_report_types.sql, which is
removed in this same change now that Alembic is set up — this is the first
real (non-baseline) migration.

SQLAlchemy maps Python enum members by NAME, so ReportType.DATACENTER is
stored as the label 'DATACENTER'. `ALTER TYPE ... ADD VALUE` cannot run
inside a transaction block; `autocommit_block()` handles that the same way
the old script's manual autocommit caveat did.

Not applied to the live DB as part of this change — see
DC_POP_REPORTS_IMPLEMENTATION_PLAN.md §10.1, this is a deploy-time step
requiring its own explicit go-ahead.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '2992dcb83d5d'
down_revision: Union[str, Sequence[str], None] = '134c4ef50825'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE reporttype ADD VALUE IF NOT EXISTS 'DATACENTER'")
        op.execute("ALTER TYPE reporttype ADD VALUE IF NOT EXISTS 'POP'")


def downgrade() -> None:
    """No-op. Postgres has no `ALTER TYPE ... DROP VALUE` — removing an enum
    label requires recreating the type (and every column/index using it),
    which is out of scope for an automatic downgrade. If DATACENTER/POP ever
    need to be removed, that's a hand-written migration of its own."""
    pass
