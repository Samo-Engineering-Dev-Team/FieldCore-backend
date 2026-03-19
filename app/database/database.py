from sqlmodel import SQLModel, Session as _Session, create_engine
from sqlalchemy import Engine
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
