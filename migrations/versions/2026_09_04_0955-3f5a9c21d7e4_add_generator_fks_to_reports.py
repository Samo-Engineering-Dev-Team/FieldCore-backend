"""add generator FKs to reports

Revision ID: 3f5a9c21d7e4
Revises: 7c4d1e88b5a2
Create Date: 2026-09-04 09:55:00.000000

Links the gen1/gen2 sections of a REPEATER report to a registered unit.

The repeater site visit — not `routine_inspections`, which holds no rows — is
where generator inspections are actually captured: 117 reports carry a `gen1`
key, identifying the unit only by a free-text `serialNumber`.

Both columns are nullable and are only ever set on new submissions. Existing
payloads are left byte-identical, per the additive-only constraint on report
JSON, and the read side falls back to the captured `serialNumber` when there is
no link.

Hand-written for the same reason as 2a338aefef5c — autogenerate against this
database also sweeps up the pre-existing baseline drift.

⚠ LOCAL ONLY for now. Pending for the Live branch migration.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "3f5a9c21d7e4"
down_revision: Union[str, Sequence[str], None] = "7c4d1e88b5a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("reports", sa.Column("gen1_generator_id", sa.Uuid(), nullable=True))
    op.add_column("reports", sa.Column("gen2_generator_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_reports_gen1_generator_id", "reports", "generators", ["gen1_generator_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_reports_gen2_generator_id", "reports", "generators", ["gen2_generator_id"], ["id"]
    )
    # Partial on NOT NULL: the pre-register reports are the bulk of the table
    # and are never the target of a per-unit lookup.
    op.create_index(
        "ix_reports_gen1_generator_id",
        "reports",
        ["gen1_generator_id"],
        postgresql_where=sa.text("gen1_generator_id IS NOT NULL"),
    )
    op.create_index(
        "ix_reports_gen2_generator_id",
        "reports",
        ["gen2_generator_id"],
        postgresql_where=sa.text("gen2_generator_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema.

    Only the links are lost. Report payloads were never rewritten, so every
    section still carries the `serialNumber` it was captured with.
    """
    op.drop_index(
        "ix_reports_gen2_generator_id",
        table_name="reports",
        postgresql_where=sa.text("gen2_generator_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_reports_gen1_generator_id",
        table_name="reports",
        postgresql_where=sa.text("gen1_generator_id IS NOT NULL"),
    )
    op.drop_constraint("fk_reports_gen2_generator_id", "reports", type_="foreignkey")
    op.drop_constraint("fk_reports_gen1_generator_id", "reports", type_="foreignkey")
    op.drop_column("reports", "gen2_generator_id")
    op.drop_column("reports", "gen1_generator_id")
