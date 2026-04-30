from uuid import UUID
from fastapi import Depends
from typing import List, Annotated
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError

from app.utils.enums import RoutineIssueSeverity
from app.models import (
    RoutineIssue,
    RoutineIssueCreate,
    RoutineIssueUpdate,
    RoutineIssueResponse,
)
from app.exceptions.http import (
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
)


class _RoutineIssueService:
    def routine_issue_to_response(
        self, routine_issue: RoutineIssue
    ) -> RoutineIssueResponse:
        return RoutineIssueResponse(
            **routine_issue.model_dump(),
        )

    def create_routine_issue(
        self, data: RoutineIssueCreate, session: Session
    ) -> RoutineIssueResponse:
        routine_issue: RoutineIssue = RoutineIssue(**data.model_dump())
        try:
            session.add(routine_issue)
            session.commit()
            session.refresh(routine_issue)
            return self.routine_issue_to_response(routine_issue)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error creating routine_issue: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error creating routine_issue: {e}"
            )

    def read_routine_issue(
        self, routine_issue_id: UUID, session: Session
    ) -> RoutineIssueResponse:
        routine_issue = self._get_routine_issue(routine_issue_id, session)
        return self.routine_issue_to_response(routine_issue)

    def read_routine_issues(
        self,
        session: Session,
        status: RoutineIssueSeverity | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[RoutineIssueResponse]:
        statement = select(RoutineIssue).where(RoutineIssue.deleted_at.is_(None))  # type: ignore

        if status is not None:
            statement = statement.where(RoutineIssue.severity == status)

        statement = statement.offset(offset).limit(limit)
        routine_issues = session.exec(statement).all()
        return [
            self.routine_issue_to_response(routine_issue)
            for routine_issue in routine_issues
        ]

    def update_routine_issue(
        self, routine_issue_id: UUID, data: RoutineIssueUpdate, session: Session
    ) -> RoutineIssueResponse:
        routine_issue = self._get_routine_issue(routine_issue_id, session)
        update_data = data.model_dump(
            exclude_none=True, exclude_defaults=True, exclude_unset=True
        )

        if not update_data:
            return self.routine_issue_to_response(routine_issue)

        for k, v in update_data.items():
            setattr(routine_issue, k, v)

        routine_issue.touch()

        try:
            session.commit()
            session.refresh(routine_issue)
            return self.routine_issue_to_response(routine_issue)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error updating routine_issue: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error updating routine_issue: {e}"
            )

    def delete_routine_issue(self, routine_issue_id: UUID, session: Session) -> None:
        routine_issue = self._get_routine_issue(routine_issue_id, session)
        routine_issue.soft_delete()
        session.commit()

    def _get_routine_issue(
        self, routine_issue_id: UUID, session: Session
    ) -> RoutineIssue:
        statement = select(RoutineIssue).where(
            RoutineIssue.id == routine_issue_id, RoutineIssue.deleted_at.is_(None)
        )  # type: ignore
        routine_issue: RoutineIssue | None = session.exec(statement).first()
        if not routine_issue:
            raise NotFoundException("routine_issue not found")
        return routine_issue


def get_routine_issue_service() -> _RoutineIssueService:
    return _RoutineIssueService()


RoutineIssueService = Annotated[
    _RoutineIssueService, Depends(get_routine_issue_service)
]
