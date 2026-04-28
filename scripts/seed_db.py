"""
Seed script for the experimental database.
Creates test users for development.

Run with:
    set SEED_USER_PASSWORD=...
    uv run python scripts/seed_db.py
"""

import os
import sys

# Add parent directory to path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select

from app.core import SecurityUtils, app_settings
from app.database import Database
from app.models import User
from app.utils.enums import UserRole, UserStatus


def seed_users() -> None:
    """Create test users for development."""
    seed_password = os.getenv("SEED_USER_PASSWORD")
    if not seed_password:
        raise RuntimeError("Set SEED_USER_PASSWORD before running this script.")

    users_to_create = [
        {
            "name": "Admin",
            "surname": "User",
            "email": "admin@seacom-dev.com",
            "role": UserRole.ADMIN,
        },
        {
            "name": "Manager",
            "surname": "User",
            "email": "manager@seacom-dev.com",
            "role": UserRole.MANAGER,
        },
        {
            "name": "NOC",
            "surname": "Operator",
            "email": "noc@seacom-dev.com",
            "role": UserRole.NOC,
        },
        {
            "name": "John",
            "surname": "Technician",
            "email": "tech@seacom-dev.com",
            "role": UserRole.TECHNICIAN,
        },
    ]

    with Session(Database.connection) as session:
        for user_data in users_to_create:
            existing = session.exec(
                select(User).where(User.email == user_data["email"])
            ).first()

            if existing:
                print(f"  User {user_data['email']} already exists, skipping...")
                continue

            user = User(
                name=user_data["name"],
                surname=user_data["surname"],
                email=user_data["email"],
                password_hash=SecurityUtils.hash_password(seed_password),
                role=user_data["role"],
                status=UserStatus.ACTIVE,
            )
            session.add(user)
            print(f"  Created {user_data['role'].value}: {user_data['email']}")

        session.commit()


def main() -> None:
    print("\nSeeding experimental database...")
    print(f"   Database: {app_settings.DB_NAME}")
    print(f"   Host: {app_settings.DB_HOST}:{app_settings.DB_PORT}")
    print()

    Database.connect(app_settings.database_url)

    print("Creating test users...")
    seed_users()

    print()
    print("Seeding complete.")
    print("Test user password is set via SEED_USER_PASSWORD.")

    Database.disconnect()


if __name__ == "__main__":
    main()
