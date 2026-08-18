"""Generator unit registry (spec §4).

Small CRUD service. Exists in Phase 2 because a technician cannot raise a
generator refuel request without picking a unit, and the units have to be
registered before that picker has anything in it.
"""

from typing import Annotated, List
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
        return GeneratorResponse(
            id=generator.id,
            created_at=generator.created_at,
            updated_at=generator.updated_at,
            deleted_at=generator.deleted_at,
            site_id=generator.site_id,
            gen_no=generator.gen_no,
            label=generator.label,
            is_active=generator.is_active,
            display_name=generator.display_name,
            site_name=generator.site.name if generator.site else "",
        )

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

    def read_generators(
        self,
        session: Session,
        site_id: UUID | None = None,
        include_inactive: bool = False,
    ) -> List[GeneratorResponse]:
        statement = select(Generator).where(Generator.deleted_at.is_(None))  # type: ignore
        if site_id is not None:
            statement = statement.where(Generator.site_id == site_id)
        if not include_inactive:
            statement = statement.where(Generator.is_active)  # type: ignore[arg-type]
        statement = statement.order_by(Generator.site_id, Generator.gen_no)  # type: ignore[arg-type]
        return [self._to_response(g) for g in session.exec(statement).all()]

    def read_generator(self, generator_id: UUID, session: Session) -> GeneratorResponse:
        return self._to_response(self._get(generator_id, session))

    def create_generator(
        self, payload: GeneratorCreate, session: Session, current_user: TokenData
    ) -> GeneratorResponse:
        require_management(current_user, "Only management may register a generator")

        site = session.exec(
            select(Site).where(
                Site.id == payload.site_id,
                Site.deleted_at.is_(None),  # type: ignore
            )
        ).first()
        if site is None:
            raise NotFoundException("site not found")

        generator = Generator(**payload.model_dump())
        try:
            session.add(generator)
            session.commit()
            session.refresh(generator)
            return self._to_response(generator)
        except IntegrityError:
            session.rollback()
            # The partial unique index on (site_id, gen_no) is the only realistic
            # cause; name it rather than surfacing the raw constraint error.
            raise ConflictException(
                f"{site.name} already has a Gen {payload.gen_no}. "
                "Use a different unit number, or reactivate the existing one."
            )
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
            raise ConflictException(
                f"Another active unit at this site already uses Gen {payload.gen_no}."
            )
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Unexpected error updating generator: {e}")

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
