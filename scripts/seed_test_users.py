"""
Seed script: Create test users (one per role) for the experimental Supabase DB.

Usage:
    cd seacom-app-backend
    uv run python scripts/seed_test_users.py

All users share the password from SEED_TEST_USER_PASSWORD.
"""

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text
from app.core.settings import app_settings
from app.core.security import SecurityUtils

# ── Configuration ────────────────────────────────────────────
SHARED_PASSWORD = os.getenv("SEED_TEST_USER_PASSWORD")
EMAIL_DOMAIN = "@samotelecoms.dev"

if not SHARED_PASSWORD:
    raise RuntimeError("Set SEED_TEST_USER_PASSWORD before running this script.")

USERS = [
    {
        "name": "Admin",
        "surname": "User",
        "email": f"admin{EMAIL_DOMAIN}",
        "role": "ADMIN",
    },
    {
        "name": "Manager",
        "surname": "User",
        "email": f"manager{EMAIL_DOMAIN}",
        "role": "MANAGER",
    },
    {
        "name": "Technician",
        "surname": "User",
        "email": f"technician{EMAIL_DOMAIN}",
        "role": "TECHNICIAN",
    },
    {
        "name": "NOC",
        "surname": "User",
        "email": f"noc{EMAIL_DOMAIN}",
        "role": "NOC",
    },
]

# ── Main ─────────────────────────────────────────────────────

def main():
    password_hash = SecurityUtils.hash_password(SHARED_PASSWORD)
    now = datetime.now(timezone.utc)

    engine = create_engine(app_settings.database_url)

    print(f"Connecting to: {app_settings.DB_HOST}/{app_settings.DB_NAME}")
    print(f"Seeding {len(USERS)} test users...\n")

    with engine.begin() as conn:
        for user in USERS:
            # Skip if email already exists
            exists = conn.execute(
                text("SELECT 1 FROM users WHERE email = :email AND deleted_at IS NULL"),
                {"email": user["email"]},
            ).fetchone()

            if exists:
                print(f"  ⏭  {user['role']:<12} {user['email']} — already exists, skipping")
                continue

            user_id = uuid.uuid4()
            conn.execute(
                text("""
                    INSERT INTO users (id, name, surname, email, role, status, password_hash, must_change_password, created_at, updated_at)
                    VALUES (:id, :name, :surname, :email, :role, 'ACTIVE', :password_hash, true, :now, :now)
                """),
                {
                    "id": str(user_id),
                    "name": user["name"],
                    "surname": user["surname"],
                    "email": user["email"],
                    "role": user["role"],
                    "password_hash": password_hash,
                    "now": now,
                },
            )
            print(f"  ✅  {user['role']:<12} {user['email']}  (id: {user_id})")

    print("\n✅ Done! All test users created.")
    print("   Shared test password is set via SEED_TEST_USER_PASSWORD.")
    print("   First login requires password change.")


if __name__ == "__main__":
    main()
