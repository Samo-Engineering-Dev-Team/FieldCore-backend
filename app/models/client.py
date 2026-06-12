from sqlmodel import SQLModel, Field, Relationship, Column
from sqlalchemy import String
from typing import TYPE_CHECKING, List

from .base import BaseDB

if TYPE_CHECKING:
    from .incident import Incident


class BaseClient(SQLModel):
    """Base client fields."""

    name: str = Field(max_length=100, nullable=False, unique=True, index=True)
    # code field removed
    is_active: bool = Field(default=True)


class Client(BaseDB, BaseClient, table=True):
    """Client model - represents companies that assign work (SEACOM, Vodacom, Cell C, etc.)"""

    __tablename__ = "clients"  # type: ignore

    # Add `code` column at the DB level to satisfy existing schema (deprecated for API use)
    code: str = Field(
        default="",
        sa_column=Column(
            String(20), nullable=False, unique=True, index=True, server_default=""
        ),
    )

    incidents: List["Incident"] = Relationship(back_populates="client")


class ClientCreate(BaseClient):
    """Schema for creating a new client."""

    pass


class ClientUpdate(SQLModel):
    """Schema for updating a client."""

    name: str | None = Field(default=None, max_length=100)
    # code field removed
    is_active: bool | None = None


class ClientResponse(BaseDB, BaseClient):
    """Schema for client response."""

    pass
