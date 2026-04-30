from uuid import UUID
from fastapi import Depends
from typing import List, Annotated
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError

from app.utils.enums import RoutineCheckStatus
from app.models import (
    RoutineCheck,
    RoutineCheckCreate,
    RoutineCheckUpdate,
    RoutineCheckResponse,
)
from app.exceptions.http import (
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
)


class _RoutineCheckService:
    def routine_check_to_response(
        self, routine_check: RoutineCheck
    ) -> RoutineCheckResponse:
        return RoutineCheckResponse(
            **routine_check.model_dump(),
        )

    def create_routine_check(
        self, data: RoutineCheckCreate, session: Session
    ) -> RoutineCheckResponse:
        routine_check: RoutineCheck = RoutineCheck(**data.model_dump())
        try:
            session.add(routine_check)
            session.commit()
            session.refresh(routine_check)
            return self.routine_check_to_response(routine_check)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error creating routine_check: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error creating routine_check: {e}"
            )

    def read_routine_check(
        self, routine_check_id: UUID, session: Session
    ) -> RoutineCheckResponse:
        routine_check = self._get_routine_check(routine_check_id, session)
        return self.routine_check_to_response(routine_check)

    def read_routine_checks(
        self,
        session: Session,
        status: RoutineCheckStatus | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[RoutineCheckResponse]:
        statement = select(RoutineCheck).where(RoutineCheck.deleted_at.is_(None))  # type: ignore

        if status is not None:
            statement = statement.where(RoutineCheck.status == status)

        statement = statement.offset(offset).limit(limit)
        routine_checks = session.exec(statement).all()
        return [
            self.routine_check_to_response(routine_check)
            for routine_check in routine_checks
        ]

    def update_routine_check(
        self, routine_check_id: UUID, data: RoutineCheckUpdate, session: Session
    ) -> RoutineCheckResponse:
        routine_check = self._get_routine_check(routine_check_id, session)
        update_data = data.model_dump(
            exclude_none=True, exclude_defaults=True, exclude_unset=True
        )

        if not update_data:
            return self.routine_check_to_response(routine_check)

        for k, v in update_data.items():
            setattr(routine_check, k, v)

        routine_check.touch()

        try:
            session.commit()
            session.refresh(routine_check)
            return self.routine_check_to_response(routine_check)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error updating routine_check: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error updating routine_check: {e}"
            )

    def delete_routine_check(self, routine_check_id: UUID, session: Session) -> None:
        routine_check = self._get_routine_check(routine_check_id, session)
        routine_check.soft_delete()
        session.commit()

    def _get_routine_check(
        self, routine_check_id: UUID, session: Session
    ) -> RoutineCheck:
        statement = select(RoutineCheck).where(
            RoutineCheck.id == routine_check_id, RoutineCheck.deleted_at.is_(None)
        )  # type: ignore
        routine_check: RoutineCheck | None = session.exec(statement).first()
        if not routine_check:
            raise NotFoundException("routine_check not found")
        return routine_check


def get_routine_check_service() -> _RoutineCheckService:
    return _RoutineCheckService()


RoutineCheckService = Annotated[
    _RoutineCheckService, Depends(get_routine_check_service)
]
