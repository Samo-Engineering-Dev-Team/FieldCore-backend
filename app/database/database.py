from contextlib import contextmanager
from typing import Annotated, Generator, List

from fastapi import Depends
from loguru import logger as LOG
from sqlalchemy import Engine, inspect, text
from sqlmodel import SQLModel, Session as _Session, create_engine


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
                pool_pre_ping=True,
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

        has_users = inspector.has_table("users")
        user_columns = (
            {column["name"] for column in inspector.get_columns("users")}
            if has_users
            else set()
        )
        missing_user_columns = (
            {
                "must_change_password",
                "credentials_updated_at",
                "sessions_revoked_at",
            }
            - user_columns
            if has_users
            else set()
        )

        has_access_requests = inspector.has_table("access_requests")
        access_request_columns = (
            {column["name"] for column in inspector.get_columns("access_requests")}
            if has_access_requests
            else set()
        )
        missing_access_request_columns = (
            {"report_type"} - access_request_columns
            if has_access_requests
            else set()
        )

        has_tasks = inspector.has_table("tasks")
        task_columns = (
            {column["name"] for column in inspector.get_columns("tasks")}
            if has_tasks
            else set()
        )
        missing_task_columns = {"report_type"} - task_columns if has_tasks else set()

        has_technicians = inspector.has_table("technicians")
        technician_indexes = (
            {index["name"] for index in inspector.get_indexes("technicians")}
            if has_technicians
            else set()
        )
        needs_technician_unique_index_fix = has_technicians and (
            "technicians_phone_key" in technician_indexes
            or "technicians_id_no_key" in technician_indexes
            or "uq_active_technicians_phone" not in technician_indexes
            or "uq_active_technicians_id_no" not in technician_indexes
        )

        if (
            not missing_user_columns
            and not missing_access_request_columns
            and not missing_task_columns
            and not needs_technician_unique_index_fix
        ):
            return

        with cls.connection.begin() as connection:
            if "sessions_revoked_at" in missing_user_columns:
                connection.execute(
                    text(
                        """
                        ALTER TABLE users
                        ADD COLUMN sessions_revoked_at TIMESTAMPTZ
                        """
                    )
                )
                LOG.warning(
                    "Applied schema compatibility fix: added users.sessions_revoked_at column"
                )

            if "must_change_password" in missing_user_columns:
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

            if "credentials_updated_at" in missing_user_columns:
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

            if "report_type" in missing_access_request_columns:
                connection.execute(
                    text(
                        """
                        ALTER TABLE access_requests
                        ADD COLUMN report_type VARCHAR DEFAULT 'general'
                        """
                    )
                )
                LOG.warning(
                    "Applied schema compatibility fix: added access_requests.report_type column"
                )

            if "report_type" in missing_task_columns:
                connection.execute(
                    text(
                        """
                        ALTER TABLE tasks
                        ADD COLUMN report_type VARCHAR
                        """
                    )
                )
                LOG.warning(
                    "Applied schema compatibility fix: added tasks.report_type column"
                )

            if needs_technician_unique_index_fix:
                connection.execute(
                    text(
                        """
                        ALTER TABLE technicians
                        DROP CONSTRAINT IF EXISTS technicians_phone_key
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        ALTER TABLE technicians
                        DROP CONSTRAINT IF EXISTS technicians_id_no_key
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS uq_active_technicians_phone
                        ON technicians (phone)
                        WHERE deleted_at IS NULL
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS uq_active_technicians_id_no
                        ON technicians (id_no)
                        WHERE deleted_at IS NULL
                        """
                    )
                )
                LOG.warning(
                    "Applied schema compatibility fix: technician phone/id_no uniqueness is active-row scoped"
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
        """Context manager for database sessions."""
        if not cls.connection:
            LOG.critical("Cannot get session. Database is not connected.")
            raise RuntimeError("Cannot get session. Database is not connected.")
        with _Session(cls.connection) as session:
            yield session


SessionDep = Annotated[_Session, Depends(Database.get_session)]
