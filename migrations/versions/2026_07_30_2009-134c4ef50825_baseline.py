"""baseline

Revision ID: 134c4ef50825
Revises:
Create Date: 2026-07-30 20:09:03.823304

This repo has never used Alembic before this revision — schema has come
from `SQLModel.metadata.create_all()` plus hand-numbered scripts in
`scripts/0001..0044*.sql`. This revision exists purely as the marker Alembic
needs to start tracking from here on (`alembic stamp head` on the live DB —
never `alembic upgrade head`, see below); it intentionally does nothing.

`alembic revision --autogenerate` against the live DB surfaced real,
pre-existing drift between the SQLModel classes and what the numbered SQL
scripts actually built: custom-named indexes (including GIST spatial
indexes on `sites.location`, `technicians.current_location`/`home_base` —
load-bearing for proximity queries), an `incidents.severity` VARCHAR vs
Enum mismatch, TEXT vs VARCHAR column types, comments, and NOT NULL
constraints the models don't declare. None of that is safe to apply blindly
— autogenerate doesn't know which side (model or live schema) is the
"wrong" one, and at least one item (dropping the GIST indexes) would
actively break working queries.

Two tables/columns are deliberately excluded from Alembic's view entirely
(see `_FOREIGN_OR_UNMANAGED_TABLES` in `migrations/env.py`) rather than
included and then left undiffed here:
  - A separate tenant/licensing product shares this Postgres project
    (`tenants`, `license_*`, `entitlements`, `audit_log`, etc.) — zero
    references anywhere in this codebase.
  - `webhooks` collides by name with that other product's table (INTEGER id
    + a `tenant_id` column vs FieldCore's own UUID `Webhook` model) —
    FieldCore's own webhook feature may never have actually worked against
    this table. Flagged separately for the team; not touched here.
  - `user_sessions` (real FieldCore table, used by `app/services/presence.py`)
    was never imported into `app/models/__init__.py`, so it has always
    lived outside `SQLModel.metadata`. Being retired when auth moves to
    Supabase — left alone rather than fixed.
  - `login_audit` (real FieldCore data, `app/services/auth.py`) is managed
    only via raw SQL by design, never a SQLModel table.

To see the full drift catalogue again: revert this file's upgrade/downgrade
to `pass`, delete it, and re-run
`uv run alembic revision --autogenerate -m "baseline"` against a copy of
this state.
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '134c4ef50825'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Deliberately empty — see module docstring. Stamp, never upgrade, onto
    the live DB this baseline was generated from."""
    pass


def downgrade() -> None:
    """Deliberately empty — see module docstring."""
    pass
