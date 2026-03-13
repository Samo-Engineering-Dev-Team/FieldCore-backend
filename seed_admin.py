"""
Run once after `docker compose up --build` to create the initial admin user.

Usage:
    python seed_admin.py
"""
import os
import sys

# Force local Docker postgres — must happen BEFORE any app imports
os.environ["DB_HOST"]     = "localhost"
os.environ["DB_PORT"]     = "5433"
os.environ["DB_USER"]     = "postgres"
os.environ["DB_PASSWORD"] = "experimental123"
os.environ["DB_NAME"]     = "seacom_experimental_db"

from sqlmodel import Session, select, text
from app.database import Database
from app.core import app_settings, SecurityUtils
from app.models import User
from app.utils.enums import UserRole, UserStatus

ADMIN_NAME     = "Admin"
ADMIN_SURNAME  = "User"
ADMIN_EMAIL    = "admin@samotelecoms.co.za"
ADMIN_PASSWORD = "Admin@1234"

def run_migration(session: Session, path: str) -> None:
    sql = open(path).read()
    session.execute(text(sql))
    session.commit()
    print(f"  Ran migration: {path}")

def main():
    Database.connect(app_settings.database_url)

    with Session(Database.connection) as session:
        # Ensure login_audit table exists
        run_migration(session, "scripts/0030_create_login_audit.sql")

        existing = session.exec(
            select(User).where(User.email == ADMIN_EMAIL, User.deleted_at.is_(None))
        ).first()

        if existing:
            print(f"Admin user already exists: {existing.email}")
            sys.exit(0)

        admin = User(
            name=ADMIN_NAME,
            surname=ADMIN_SURNAME,
            email=ADMIN_EMAIL,
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
            password_hash=SecurityUtils.hash_password(ADMIN_PASSWORD),
        )
        session.add(admin)
        session.commit()
        session.refresh(admin)
        print(f"Admin user created!")
        print(f"  Email:    {ADMIN_EMAIL}")
        print(f"  Password: {ADMIN_PASSWORD}")
        print(f"  ID:       {admin.id}")

    Database.disconnect()

if __name__ == "__main__":
    main()
