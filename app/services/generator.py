"""Generator asset register.

CRUD over the physical units, plus site assignment. Writes are management-only
(`require_management` — super_admin, admin, manager, noc); reads are open to any
authenticated user, because a technician has to list units to pick the one they
are refuelling.

Site assignment is its own operation rather than a field on the generic update:
it is the one change with a real-world action behind it (a unit was moved), it
needs its own validation, and `site_id=None` unassigning is explicit rather than
indistinguishable from "field omitted".
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.exceptions.http import (
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
)
from app.models import (
    Generator,
    GeneratorCreate,
    GeneratorResponse,
    GeneratorUpdate,
    Site,
)
from app.models.auth import TokenData
from app.services.authorization import require_management


class _GeneratorService:
    def _to_response(self, generator: Generator) -> GeneratorResponse:
        return GeneratorResponse.from_generator(generator)

    def _get(self, generator_id: UUID, session: Session) -> Generator:
        generator = session.exec(
            select(Generator).where(
                Generator.id == generator_id,
                Generator.deleted_at.is_(None),  # type: ignore
            )
        ).first()
        if not generator:
            raise NotFoundException("generator not found")
        return generator

    def _get_site(self, site_id: UUID, session: Session) -> Site:
        site = session.exec(
            select(Site).where(
                Site.id == site_id,
                Site.deleted_at.is_(None),  # type: ignore
            )
        ).first()
        if site is None:
            raise NotFoundException("site not found")
        return site

    def _duplicate_serial(self, serial_no: str | None) -> ConflictException:
        # The partial unique index on serial_no is the only realistic cause of an
        # IntegrityError here; name the unit rather than surfacing the raw
        # constraint error.
        return ConflictException(
            f"Serial number {serial_no} is already registered to another generator. "
            "Check the plate, or reactivate the existing unit."
        )

    def read_generators(
        self,
        session: Session,
        site_id: UUID | None = None,
        unassigned: bool = False,
        include_inactive: bool = False,
    ) -> list[GeneratorResponse]:
        """
        List units, newest naming order aside — ordered by name so the grid reads
        alphabetically rather than by insertion.

        `site_id` and `unassigned` are separate filters on purpose: `unassigned`
        cannot be expressed as a site id, and asking for both is a contradiction
        the caller resolves, not this method (site_id wins).
        """
        statement = select(Generator).where(Generator.deleted_at.is_(None))  # type: ignore
        if site_id is not None:
            statement = statement.where(Generator.site_id == site_id)
        elif unassigned:
            statement = statement.where(Generator.site_id.is_(None))  # type: ignore
        if not include_inactive:
            statement = statement.where(Generator.is_active)  # type: ignore[arg-type]
        statement = statement.order_by(Generator.name)  # type: ignore[arg-type]
        return [self._to_response(g) for g in session.exec(statement).all()]

    def read_generator(self, generator_id: UUID, session: Session) -> GeneratorResponse:
        return self._to_response(self._get(generator_id, session))

    def create_generator(
        self, payload: GeneratorCreate, session: Session, current_user: TokenData
    ) -> GeneratorResponse:
        require_management(current_user, "Only management may register a generator")

        # A site is optional — a unit can be registered before it is placed — so
        # it is validated only when one was given.
        if payload.site_id is not None:
            self._get_site(payload.site_id, session)

        generator = Generator(**payload.model_dump())
        try:
            session.add(generator)
            session.commit()
            session.refresh(generator)
            return self._to_response(generator)
        except IntegrityError:
            session.rollback()
            raise self._duplicate_serial(payload.serial_no)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error creating generator: {e}")

    def update_generator(
        self,
        generator_id: UUID,
        payload: GeneratorUpdate,
        session: Session,
        current_user: TokenData,
    ) -> GeneratorResponse:
        require_management(current_user, "Only management may change a generator")
        generator = self._get(generator_id, session)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(generator, field, value)
        generator.touch()
        try:
            session.add(generator)
            session.commit()
            session.refresh(generator)
            return self._to_response(generator)
        except IntegrityError:
            session.rollback()
            raise self._duplicate_serial(payload.serial_no)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error updating generator: {e}")

    def assign_site(
        self,
        generator_id: UUID,
        site_id: UUID | None,
        session: Session,
        current_user: TokenData,
    ) -> GeneratorResponse:
        """Assign a unit to a site, or unassign it when `site_id` is None."""
        require_management(current_user, "Only management may assign a generator to a site")
        generator = self._get(generator_id, session)
        if site_id is not None:
            self._get_site(site_id, session)

        generator.site_id = site_id
        generator.touch()
        try:
            session.add(generator)
            session.commit()
            session.refresh(generator)
            return self._to_response(generator)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error assigning generator: {e}")

    def delete_generator(
        self, generator_id: UUID, session: Session, current_user: TokenData
    ) -> None:
        """Soft delete. Historical refuel records keep pointing at the row so past
        fills stay attributable; prefer is_active=False for a decommissioned unit."""
        require_management(current_user, "Only management may remove a generator")
        generator = self._get(generator_id, session)
        generator.soft_delete()
        session.add(generator)
        session.commit()


def get_generator_service() -> _GeneratorService:
    return _GeneratorService()


GeneratorService = Annotated[_GeneratorService, Depends(get_generator_service)]
