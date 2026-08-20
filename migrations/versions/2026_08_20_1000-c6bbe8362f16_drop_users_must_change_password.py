"""drop users.must_change_password

Revision ID: c6bbe8362f16
Revises: 97f73cf72ab8
Create Date: 2026-08-20 10:00:00.000000

`users.must_change_password` was never a real Alembic-managed column — it was
added by the runtime compatibility shim in `app/database/database.py`
(`Database._apply_schema_fixes`), which is why it doesn't appear in the
baseline migration despite existing on live databases. Forced first-login
password changes are being removed entirely, so this drops the column
directly (guarded with `IF EXISTS` since some environments never ran the
shim) and the shim's ADD COLUMN branch for it is removed in the same change.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c6bbe8362f16'
down_revision: Union[str, Sequence[str], None] = '97f73cf72ab8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS must_change_password")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT FALSE"
    )
