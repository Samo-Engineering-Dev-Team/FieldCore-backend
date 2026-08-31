"""add standalone reconciliations and per-technician reference numbers

Revision ID: 25fb973d1356
Revises: b7f21c9e40aa
Create Date: 2026-08-31 11:35:00.000000

A reconciliation could previously only account for a specific released
disbursement (spec §4, one-per-disbursement). A technician who spends less
than a disbursement covers and puts the leftover to a different, unrequested
use (e.g. R250 left over from a R700 trip, spent on a generator refuel) had no
way to account for that spend — it just sat as part of the original
disbursement's outstanding balance forever.

This migration lets a reconciliation stand on its own:
  - `reconciliations.disbursement_id` becomes nullable.
  - `reconciliations.declared_amount` holds the technician's own declaration of
    what they are accounting for, playing the role the linked disbursement's
    `amount_issued` plays for a normal recon.
  - `reconciliations.description` says what a standalone recon is for.
  - `reconciliations.technician_id` is now set directly on every recon
    (standalone or linked), so ownership and the reference sequence below
    don't depend on walking disbursement -> funds_request -> technician.
  - `reconciliations.reference_no` is a per-technician sequence ("FR-01",
    "FR-02", ...) for talking about a recon in a conversation with Finance
    without reading out a UUID.
  - `technicians.recon_sequence` backs that sequence: incremented with an
    UPDATE ... RETURNING in the same transaction as the recon insert, so the
    row lock serialises concurrent creates for one technician.

NOT purely additive: technician_id and reference_no are backfilled for every
existing reconciliation (there is exactly one source they can come from — the
linked disbursement's funds request — since standalone recons didn't exist
before this migration), then locked to NOT NULL. technicians.recon_sequence is
seeded to each technician's existing recon count so a newly created recon
continues the sequence rather than colliding with a backfilled reference_no.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "25fb973d1356"
down_revision: Union[str, None] = "b7f21c9e40aa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "technicians",
        sa.Column(
            "recon_sequence", sa.Integer(), nullable=False, server_default="0"
        ),
    )

    op.alter_column("reconciliations", "disbursement_id", nullable=True)
    op.add_column(
        "reconciliations", sa.Column("technician_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "reconciliations",
        sa.Column("declared_amount", sa.Numeric(precision=12, scale=2), nullable=True),
    )
    op.add_column(
        "reconciliations", sa.Column("description", sa.String(length=500), nullable=True)
    )
    op.add_column(
        "reconciliations",
        sa.Column("reference_no", sa.String(length=20), nullable=True),
    )

    # Backfill technician_id for every existing (disbursement-linked) recon.
    op.execute(
        """
        UPDATE reconciliations r
        SET technician_id = fr.technician_id
        FROM disbursements d
        JOIN funds_requests fr ON fr.id = d.funds_request_id
        WHERE r.disbursement_id = d.id
        """
    )

    # Backfill reference_no as a per-technician sequence ordered by when the
    # recon was created, oldest first — so "FR-01" is whichever recon actually
    # happened first for that technician.
    op.execute(
        """
        WITH numbered AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY technician_id ORDER BY created_at
            ) AS rn
            FROM reconciliations
        )
        UPDATE reconciliations r
        SET reference_no = 'FR-' || lpad(numbered.rn::text, 2, '0')
        FROM numbered
        WHERE numbered.id = r.id
        """
    )

    # Seed each technician's counter to their existing recon count, so the next
    # one created continues the sequence instead of reusing a number.
    op.execute(
        """
        UPDATE technicians t
        SET recon_sequence = sub.cnt
        FROM (
            SELECT technician_id, COUNT(*) AS cnt
            FROM reconciliations
            GROUP BY technician_id
        ) sub
        WHERE t.id = sub.technician_id
        """
    )

    op.alter_column("reconciliations", "technician_id", nullable=False)
    op.alter_column("reconciliations", "reference_no", nullable=False)

    op.create_foreign_key(
        "fk_reconciliations_technician_id",
        "reconciliations",
        "technicians",
        ["technician_id"],
        ["id"],
    )
    # Scoped per technician, not global: two technicians can each have "FR-01".
    op.create_index(
        "uq_reconciliations_technician_reference",
        "reconciliations",
        ["technician_id", "reference_no"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_reconciliations_technician_reference", table_name="reconciliations"
    )
    op.drop_constraint(
        "fk_reconciliations_technician_id", "reconciliations", type_="foreignkey"
    )
    op.drop_column("reconciliations", "reference_no")
    op.drop_column("reconciliations", "description")
    op.drop_column("reconciliations", "declared_amount")
    op.drop_column("reconciliations", "technician_id")
    # Only valid if no standalone recon was created while this revision was
    # live — downgrading past this point assumes the feature is being retired,
    # not that mid-flight standalone data needs preserving.
    op.alter_column("reconciliations", "disbursement_id", nullable=False)
    op.drop_column("technicians", "recon_sequence")
