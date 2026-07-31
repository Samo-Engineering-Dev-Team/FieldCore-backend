"""add datacenter_inspection and pop_inspection to the schedule_type check

Revision ID: 4a1c7e9b2f30
Revises: 2992dcb83d5d
Create Date: 2026-07-31 12:30:00.000000

The DC/POP work added `datacenter_inspection` and `pop_inspection` to
`SCHEDULE_TYPES` and mapped them in `SITE_TYPE_SCHEDULE_TYPES`
(`app/services/maintenance_schedule.py`), but only migrated the `reporttype`
enum. The CHECK constraint on `maintenance_schedules.schedule_type` was left
at the original three values, so:

    IntegrityError: new row for relation "maintenance_schedules" violates
    check constraint "maintenance_schedules_schedule_type_check"

fires as soon as a technician is assigned to a POP or Datacenter site —
`_ensure_schedules()` auto-generates a schedule row per site type, and the
insert is rejected.

`schedule_type` is a plain varchar guarded by this CHECK (not a native enum),
so the fix is a straight drop-and-recreate. Unlike `ALTER TYPE ... ADD VALUE`
this is transactional, so no autocommit_block is needed.

`sites.site_type` needs nothing: it is varchar with no CHECK at all. The
`sitetype` native enum in the database is an unused leftover — the Site model
declares `native_enum=False` — which is why it can lack DATACENTER without
breaking anything.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '4a1c7e9b2f30'
down_revision: Union[str, Sequence[str], None] = '2992dcb83d5d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = "maintenance_schedules_schedule_type_check"
TABLE = "maintenance_schedules"

# Must stay in step with SCHEDULE_TYPES in app/models/maintenance_schedule.py.
ALL_TYPES = (
    "routine_drive",
    "repeater_site_visit",
    "generator_diesel_refill",
    "datacenter_inspection",
    "pop_inspection",
)

ORIGINAL_TYPES = (
    "routine_drive",
    "repeater_site_visit",
    "generator_diesel_refill",
)


def _values(types: Sequence[str]) -> str:
    return ", ".join(f"'{t}'" for t in types)


def upgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT {CONSTRAINT} "
        f"CHECK (schedule_type IN ({_values(ALL_TYPES)}))"
    )


def downgrade() -> None:
    """Restore the three-value constraint.

    Fails if any datacenter_inspection or pop_inspection rows exist — that is
    deliberate. Silently dropping schedules would lose real inspection work;
    delete or retype those rows first if you genuinely need to go back.
    """
    op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT {CONSTRAINT} "
        f"CHECK (schedule_type IN ({_values(ORIGINAL_TYPES)}))"
    )
