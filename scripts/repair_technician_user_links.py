"""Repair technician profiles attached to soft-deleted duplicate users.

Dry run by default:
    uv run python scripts/repair_technician_user_links.py

Apply exact, single-match repairs:
    uv run python scripts/repair_technician_user_links.py --apply
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import create_engine, text

from app.core import app_settings


@dataclass(frozen=True)
class RelinkCandidate:
    new_user_id: UUID
    new_name: str
    new_surname: str
    new_email: str
    technician_id: UUID
    old_user_id: UUID
    old_name: str
    old_surname: str
    old_email: str
    id_no: str
    phone: str


FIND_CANDIDATES_SQL = text(
    """
    WITH active_unlinked_users AS (
        SELECT
            u.id,
            trim(lower(u.name)) AS first_name,
            trim(lower(u.surname)) AS last_name,
            lower(u.email) AS email,
            u.name,
            u.surname,
            u.email AS display_email
        FROM users u
        WHERE u.role = 'TECHNICIAN'
          AND u.deleted_at IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM technicians t
              WHERE t.user_id = u.id
                AND t.deleted_at IS NULL
          )
    ),
    orphaned_profiles AS (
        SELECT
            t.id AS technician_id,
            t.user_id AS old_user_id,
            t.id_no,
            t.phone,
            trim(lower(old_u.name)) AS first_name,
            trim(lower(old_u.surname)) AS last_name,
            lower(old_u.email) AS email,
            old_u.name AS old_name,
            old_u.surname AS old_surname,
            old_u.email AS old_email
        FROM technicians t
        JOIN users old_u ON old_u.id = t.user_id
        WHERE t.deleted_at IS NULL
          AND old_u.deleted_at IS NOT NULL
    ),
    candidate_counts AS (
        SELECT
            au.id AS new_user_id,
            count(*) AS candidate_count
        FROM active_unlinked_users au
        JOIN orphaned_profiles op
          ON op.first_name = au.first_name
         AND op.last_name = au.last_name
         AND op.email = au.email
        GROUP BY au.id
    )
    SELECT
        au.id AS new_user_id,
        au.name AS new_name,
        au.surname AS new_surname,
        au.display_email AS new_email,
        op.technician_id,
        op.old_user_id,
        op.old_name,
        op.old_surname,
        op.old_email,
        op.id_no,
        op.phone
    FROM active_unlinked_users au
    JOIN orphaned_profiles op
      ON op.first_name = au.first_name
     AND op.last_name = au.last_name
     AND op.email = au.email
    JOIN candidate_counts cc ON cc.new_user_id = au.id
    WHERE cc.candidate_count = 1
    ORDER BY au.name, au.surname
    """
)


UPDATE_TECHNICIAN_SQL = text(
    """
    UPDATE technicians
    SET user_id = :new_user_id,
        updated_at = now()
    WHERE id = :technician_id
      AND user_id = :old_user_id
      AND deleted_at IS NULL
    """
)


def find_candidates(connection) -> list[RelinkCandidate]:
    rows = connection.execute(FIND_CANDIDATES_SQL).mappings().all()
    return [RelinkCandidate(**row) for row in rows]


def print_candidates(candidates: list[RelinkCandidate]) -> None:
    if not candidates:
        print("No exact technician/user relink candidates found.")
        return

    print(f"Found {len(candidates)} exact technician/user relink candidate(s):")
    for item in candidates:
        print(
            "- "
            f"{item.new_name} {item.new_surname} <{item.new_email}>: "
            f"technician {item.technician_id} "
            f"({item.id_no}, {item.phone}) "
            f"from deleted user {item.old_user_id} "
            f"to active user {item.new_user_id}"
        )


def apply_candidates(connection, candidates: list[RelinkCandidate]) -> int:
    updated = 0
    for item in candidates:
        result = connection.execute(
            UPDATE_TECHNICIAN_SQL,
            {
                "new_user_id": item.new_user_id,
                "technician_id": item.technician_id,
                "old_user_id": item.old_user_id,
            },
        )
        updated += result.rowcount or 0
    return updated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply relinks. Without this flag the script only prints candidates.",
    )
    args = parser.parse_args()

    engine = create_engine(app_settings.database_url)
    if args.apply:
        with engine.begin() as connection:
            candidates = find_candidates(connection)
            print_candidates(candidates)
            updated = apply_candidates(connection, candidates)
            print(f"Updated {updated} technician profile(s).")
    else:
        with engine.connect() as connection:
            candidates = find_candidates(connection)
            print_candidates(candidates)
            print("Dry run only. Re-run with --apply to update the database.")


if __name__ == "__main__":
    main()
