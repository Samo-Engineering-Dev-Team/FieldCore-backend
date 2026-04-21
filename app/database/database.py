from sqlmodel import SQLModel, Session as _Session, create_engine
from sqlalchemy import Engine, inspect, text
from loguru import logger as LOG
from typing import Generator, List, Annotated
from fastapi import Depends
from datetime import datetime
from contextlib import contextmanager

from app.core.settings import app_settings


class Database:
    """Database connection manager."""

    connection: Engine | None = None

    @classmethod
    def connect(cls, url: str) -> None:
        """Establish database connection with connection pooling."""
        if cls.connection:
            LOG.warning(
                "Database is already connected. Disconnecting and reconnecting..."
            )
            cls.disconnect()
        try:
            cls.connection = create_engine(
                url,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,  # Verify connections before using them
            )
            LOG.debug(f"Connected to {cls.connection.url.database} database.")
        except Exception as e:
            message: str = f"Failed to connect to the database: {e}"
            LOG.critical(message)
            raise RuntimeError(message)

    @classmethod
    def disconnect(cls) -> None:
        """Close database connection and dispose of engine."""
        if not cls.connection:
            LOG.warning("Cannot disconnect from the database. Connect first.")
            return
        try:
            db_name: str | None = cls.connection.url.database
            cls.connection.dispose()
            cls.connection = None
            LOG.debug(f"Disconnected from {db_name} database.")
        except Exception as e:
            message: str = f"Failed to disconnect from the database: {e}"
            LOG.exception(message)
            raise RuntimeError(message)

    @classmethod
    def init(cls) -> None:
        """Initialize database schema and create tables."""
        if not cls.connection:
            LOG.warning("Cannot initialize the database. Connect first.")
            return
        try:
            SQLModel.metadata.create_all(cls.connection)
            cls._apply_schema_fixes()
            table_names: List[str] = [key for key in SQLModel.metadata.tables.keys()]
            LOG.debug(
                f"Initialized {cls.connection.url.database} database and created tables: {', '.join(table_names)}"
            )
        except Exception as e:
            message: str = (
                f"Failed to initialize {cls.connection.url.database} database: {e}"
            )
            LOG.error(message)

    @classmethod
    def _apply_schema_fixes(cls) -> None:
        """Apply small idempotent schema fixes for legacy databases."""
        if not cls.connection:
            return

        inspector = inspect(cls.connection)

        with cls.connection.begin() as connection:
            if inspector.has_table("users"):
                user_columns = {column["name"] for column in inspector.get_columns("users")}

                if "must_change_password" not in user_columns:
                    connection.execute(
                        text(
                            """
                            ALTER TABLE users
                            ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT FALSE
                            """
                        )
                    )
                    LOG.warning(
                        "Applied schema compatibility fix: added users.must_change_password column"
                    )

                if "credentials_updated_at" not in user_columns:
                    connection.execute(
                        text(
                            """
                            ALTER TABLE users
                            ADD COLUMN credentials_updated_at TIMESTAMPTZ
                            """
                        )
                    )
                    connection.execute(
                        text(
                            """
                            UPDATE users
                            SET credentials_updated_at = COALESCE(created_at, NOW())
                            WHERE credentials_updated_at IS NULL
                            """
                        )
                    )
                    connection.execute(
                        text(
                            """
                            ALTER TABLE users
                            ALTER COLUMN credentials_updated_at SET DEFAULT NOW()
                            """
                        )
                    )
                    connection.execute(
                        text(
                            """
                            ALTER TABLE users
                            ALTER COLUMN credentials_updated_at SET NOT NULL
                            """
                        )
                    )
                    LOG.warning(
                        "Applied schema compatibility fix: added users.credentials_updated_at column"
                    )

                if "tenant_id" not in user_columns:
                    connection.execute(
                        text(
                            """
                            ALTER TABLE users
                            ADD COLUMN tenant_id VARCHAR(128)
                            """
                        )
                    )
                    LOG.warning(
                        "Applied schema compatibility fix: added users.tenant_id column"
                    )

            if inspector.has_table("webhooks"):
                webhook_columns = {column["name"] for column in inspector.get_columns("webhooks")}
                if "tenant_id" not in webhook_columns:
                    connection.execute(
                        text(
                            """
                            ALTER TABLE webhooks
                            ADD COLUMN tenant_id VARCHAR(128)
                            """
                        )
                    )
                    LOG.warning(
                        "Applied schema compatibility fix: added webhooks.tenant_id column"
                    )

            if inspector.has_table("system_settings"):
                system_settings_columns = {
                    column["name"] for column in inspector.get_columns("system_settings")
                }
                if "tenant_id" not in system_settings_columns:
                    connection.execute(
                        text(
                            """
                            ALTER TABLE system_settings
                            ADD COLUMN tenant_id VARCHAR(128)
                            """
                        )
                    )
                    LOG.warning(
                        "Applied schema compatibility fix: added system_settings.tenant_id column"
                    )

                if cls.connection.dialect.name == "postgresql":
                    connection.execute(
                        text(
                            """
                            ALTER TABLE system_settings
                            DROP CONSTRAINT IF EXISTS system_settings_key_key
                            """
                        )
                    )
                    connection.execute(
                        text(
                            """
                            CREATE UNIQUE INDEX IF NOT EXISTS uq_system_settings_global_key
                            ON system_settings (key)
                            WHERE tenant_id IS NULL
                            """
                        )
                    )
                    connection.execute(
                        text(
                            """
                            CREATE UNIQUE INDEX IF NOT EXISTS uq_system_settings_tenant_key
                            ON system_settings (tenant_id, key)
                            WHERE tenant_id IS NOT NULL
                            """
                        )
                    )

    @classmethod
    def get_session(cls) -> Generator[_Session, None, None]:
        """Get a database session for request handling."""
        if not cls.connection:
            LOG.critical("Cannot get session. Database is not connected.")
            raise RuntimeError("Cannot get session. Database is not connected.")
        with _Session(cls.connection) as session:
            yield session

    @classmethod
    @contextmanager
    def session(cls):
        """Context manager for database sessions (non-dependency injection)."""
        if not cls.connection:
            LOG.critical("Cannot get session. Database is not connected.")
            raise RuntimeError("Cannot get session. Database is not connected.")
        with _Session(cls.connection) as session:
            yield session

    @classmethod
    def get_current_timestamp(cls) -> str:
        """Get current UTC timestamp in ISO format."""
        return datetime.utcnow().isoformat() + "Z"


Session = Annotated[_Session, Depends(Database.get_session)]
