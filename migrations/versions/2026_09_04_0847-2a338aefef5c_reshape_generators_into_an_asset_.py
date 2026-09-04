"""reshape generators into an asset register

Revision ID: 2a338aefef5c
Revises: 25fb973d1356
Create Date: 2026-09-04 08:47:43.116225

Turns the finance-only generator registry (site_id + gen_no + label) into the
asset register: name, model, serial, run hours and service history, with an
optional site. See docs/GENERATOR_IMPROVEMENT_PLAN.md §2.

Hand-written, not the raw autogenerate output. Autogenerate against this
database also surfaces the pre-existing baseline drift documented in
2026_07_30_2009-134c4ef50825_baseline.py (tasks/technicians indexes,
users.tenant_id, and so on) — that drift is deliberately unresolved and is not
this migration's business, so everything unrelated to `generators` was removed.
Autogenerate also added `name` as NOT NULL in one step, which fails against any
database that already holds rows; the add/backfill/constrain split below is the
reason this file is hand-written.

⚠ LOCAL ONLY for now. Pending for the Live branch migration — Live still holds
real gen_no/label rows, so the backfill below is the part that matters there and
is a no-op against a freshly cleared local database. Test it by restoring a dump
of the pre-wipe data into a scratch database, not by running it locally and
seeing no error.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 — SQLModel column types (e.g. AutoString) render using this name

# revision identifiers, used by Alembic.
revision: str = "2a338aefef5c"
down_revision: Union[str, Sequence[str], None] = "25fb973d1356"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. New columns, `name` nullable for now so existing rows survive the add.
    op.add_column(
        "generators",
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True),
    )
    op.add_column(
        "generators",
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True),
    )
    op.add_column(
        "generators",
        sa.Column(
            "serial_no", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True
        ),
    )
    op.add_column(
        "generators", sa.Column("current_run_seconds", sa.Integer(), nullable=True)
    )
    op.add_column(
        "generators", sa.Column("last_service_date", sa.Date(), nullable=True)
    )
    op.add_column(
        "generators", sa.Column("run_seconds_at_service", sa.Integer(), nullable=True)
    )
    op.add_column("generators", sa.Column("legacy_gen_no", sa.Integer(), nullable=True))

    # 2. Preserve gen_no before it is dropped. Legacy diesel report JSON names a
    #    unit only by this number and is never rewritten, so it stays the only
    #    way to resolve a historical fill to a unit.
    op.execute("UPDATE generators SET legacy_gen_no = gen_no")

    # 3. Backfill `name`, normalising the shouty labels the sites dialog wrote
    #    ("GEN 1") so the register does not ship mixed casing against the seeded
    #    rows, which have no label and fall back to "Gen 1".
    op.execute(
        r"""
        UPDATE generators
        SET name = CASE
          WHEN label ~* '^\s*gen\s*[0-9]+\s*$'
            THEN 'Gen ' || regexp_replace(label, '\D', '', 'g')
          WHEN NULLIF(btrim(label), '') IS NOT NULL
            THEN btrim(label)
          ELSE 'Gen ' || gen_no
        END
        """
    )

    # 4. Every row now has a name, so the constraint can go on.
    op.alter_column("generators", "name", existing_type=sa.String(length=100), nullable=False)

    # 5. A unit may now exist unassigned, waiting to be placed at a site.
    op.alter_column(
        "generators", "site_id", existing_type=sa.UUID(), nullable=True
    )

    # 6. Swap the indexes. The old uniqueness rule was (site_id, gen_no); with
    #    gen_no gone, serial is what must not collide — when it is present.
    op.drop_index(
        op.f("uq_generators_site_gen_no"),
        table_name="generators",
        postgresql_where="(deleted_at IS NULL)",
    )
    op.create_index(
        "ix_generators_site_id",
        "generators",
        ["site_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_generators_serial_no",
        "generators",
        ["serial_no"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND serial_no IS NOT NULL"),
    )

    # 7. Superseded by `name`; gen_no survives as legacy_gen_no.
    op.drop_column("generators", "label")
    op.drop_column("generators", "gen_no")


def downgrade() -> None:
    """Downgrade schema.

    gen_no is restored from legacy_gen_no rather than defaulted, so a
    downgrade/upgrade round trip does not silently renumber the units. Rows
    registered after the upgrade have no legacy number and fall back to 1 — the
    same rule the read side already applies to fill-ups with no usable gen_no.
    """
    op.add_column(
        "generators", sa.Column("gen_no", sa.INTEGER(), autoincrement=False, nullable=True)
    )
    op.add_column(
        "generators",
        sa.Column("label", sa.VARCHAR(length=100), autoincrement=False, nullable=True),
    )

    op.execute("UPDATE generators SET gen_no = COALESCE(legacy_gen_no, 1)")
    op.execute("UPDATE generators SET label = name")

    # Re-creating the old partial unique index would fail wherever two units at
    # one site fell back to gen_no = 1, so collapse the duplicates first by
    # renumbering per site in a stable order.
    op.execute(
        """
        WITH renumbered AS (
          SELECT id, row_number() OVER (
                   PARTITION BY site_id ORDER BY COALESCE(legacy_gen_no, 9999), created_at, id
                 ) AS n
          FROM generators
          WHERE deleted_at IS NULL
        )
        UPDATE generators g SET gen_no = r.n FROM renumbered r WHERE g.id = r.id
        """
    )

    op.alter_column("generators", "gen_no", existing_type=sa.INTEGER(), nullable=False)

    op.drop_index(
        "uq_generators_serial_no",
        table_name="generators",
        postgresql_where=sa.text("deleted_at IS NULL AND serial_no IS NOT NULL"),
    )
    op.drop_index(
        "ix_generators_site_id",
        table_name="generators",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        op.f("uq_generators_site_gen_no"),
        "generators",
        ["site_id", "gen_no"],
        unique=True,
        postgresql_where="(deleted_at IS NULL)",
    )

    # site_id was mandatory before the asset register existed. Any unassigned
    # unit has to go somewhere, and there is no correct answer — fail loudly
    # rather than invent an assignment.
    unassigned = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM generators WHERE site_id IS NULL"))
        .scalar()
    )
    if unassigned:
        raise RuntimeError(
            f"{unassigned} generator(s) are unassigned; site_id cannot be made NOT NULL "
            "again. Assign or delete them before downgrading."
        )
    op.alter_column("generators", "site_id", existing_type=sa.UUID(), nullable=False)

    op.drop_column("generators", "legacy_gen_no")
    op.drop_column("generators", "run_seconds_at_service")
    op.drop_column("generators", "last_service_date")
    op.drop_column("generators", "current_run_seconds")
    op.drop_column("generators", "serial_no")
    op.drop_column("generators", "model")
    op.drop_column("generators", "name")
