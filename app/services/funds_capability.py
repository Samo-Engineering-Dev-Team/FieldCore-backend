"""Assigning chain authority to people (spec §2).

This is the service that makes "no person is hardcoded" true in practice. The
spec names an approver, a loader, two releasers and a finance lead; all five are
rows created through here, reassignable when staff change.

Administered by admin/manager only. Note that holding a capability is what lets
someone move money, so granting one is itself a privileged act — and because
there is no management override in the chain (see authorization.py), an admin who
wants to approve something must first grant themselves APPROVE, which leaves a
row saying so.
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
    FundsCapabilityAssignment,
    FundsCapabilityAssignmentCreate,
    FundsCapabilityAssignmentResponse,
    FundsCapabilityAssignmentUpdate,
    User,
)
from app.models.auth import TokenData
from app.services.authorization import require_admin_or_manager
from app.utils.enums import FundsCapability, UserStatus


class _FundsCapabilityService:
    def _to_response(
        self, assignment: FundsCapabilityAssignment, user: User | None = None
    ) -> FundsCapabilityAssignmentResponse:
        holder = user or assignment.user
        return FundsCapabilityAssignmentResponse(
            id=assignment.id,
            created_at=assignment.created_at,
            updated_at=assignment.updated_at,
            deleted_at=assignment.deleted_at,
            user_id=assignment.user_id,
            capability=assignment.capability,
            is_fallback=assignment.is_fallback,
            is_active=assignment.is_active,
            user_name=f"{holder.name} {holder.surname}" if holder else "",
            user_email=holder.email if holder else "",
            user_role=holder.role if holder else None,
        )

    def _get(self, assignment_id: UUID, session: Session) -> FundsCapabilityAssignment:
        assignment = session.exec(
            select(FundsCapabilityAssignment).where(
                FundsCapabilityAssignment.id == assignment_id,
                FundsCapabilityAssignment.deleted_at.is_(None),  # type: ignore
            )
        ).first()
        if not assignment:
            raise NotFoundException("funds capability assignment not found")
        return assignment

    def read_assignments(
        self,
        session: Session,
        current_user: TokenData,
        capability: FundsCapability | None = None,
        include_inactive: bool = False,
    ) -> List[FundsCapabilityAssignmentResponse]:
        require_admin_or_manager(
            current_user, "Only an administrator or manager may view funds capabilities"
        )
        statement = select(FundsCapabilityAssignment).where(
            FundsCapabilityAssignment.deleted_at.is_(None)  # type: ignore
        )
        if capability is not None:
            statement = statement.where(FundsCapabilityAssignment.capability == capability)
        if not include_inactive:
            statement = statement.where(FundsCapabilityAssignment.is_active)  # type: ignore[arg-type]
        return [self._to_response(a) for a in session.exec(statement).all()]

    def assign(
        self,
        payload: FundsCapabilityAssignmentCreate,
        session: Session,
        current_user: TokenData,
    ) -> FundsCapabilityAssignmentResponse:
        require_admin_or_manager(
            current_user, "Only an administrator or manager may assign funds capabilities"
        )

        user = session.exec(
            select(User).where(
                User.id == payload.user_id,
                User.deleted_at.is_(None),  # type: ignore
            )
        ).first()
        if user is None:
            raise NotFoundException("user not found")
        if user.status is not UserStatus.ACTIVE:
            raise ConflictException(
                f"{user.name} {user.surname} is disabled and cannot hold a funds "
                "capability."
            )

        assignment = FundsCapabilityAssignment(**payload.model_dump())
        try:
            session.add(assignment)
            session.commit()
            session.refresh(assignment)
            return self._to_response(assignment, user)
        except IntegrityError:
            session.rollback()
            raise ConflictException(
                f"{user.name} {user.surname} already holds the "
                f"'{payload.capability.value}' capability."
            )
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error assigning funds capability: {e}"
            )

    def update_assignment(
        self,
        assignment_id: UUID,
        payload: FundsCapabilityAssignmentUpdate,
        session: Session,
        current_user: TokenData,
    ) -> FundsCapabilityAssignmentResponse:
        require_admin_or_manager(
            current_user, "Only an administrator or manager may change funds capabilities"
        )
        assignment = self._get(assignment_id, session)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(assignment, field, value)
        assignment.touch()
        try:
            session.add(assignment)
            session.commit()
            session.refresh(assignment)
            return self._to_response(assignment)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error updating funds capability: {e}"
            )

    def revoke(
        self, assignment_id: UUID, session: Session, current_user: TokenData
    ) -> None:
        """Soft delete, so past approvals stay explainable — a disbursement's
        approver must remain resolvable long after they stop holding the role."""
        require_admin_or_manager(
            current_user, "Only an administrator or manager may revoke funds capabilities"
        )
        assignment = self._get(assignment_id, session)
        assignment.soft_delete()
        session.add(assignment)
        session.commit()

    def read_my_capabilities(
        self, session: Session, current_user: TokenData
    ) -> List[FundsCapability]:
        """What the caller may act on. Drives which chain buttons the UI shows —
        every one of them is re-checked server-side regardless."""
        rows = session.exec(
            select(FundsCapabilityAssignment.capability).where(
                FundsCapabilityAssignment.user_id == current_user.user_id,
                FundsCapabilityAssignment.is_active,  # type: ignore[arg-type]
                FundsCapabilityAssignment.deleted_at.is_(None),  # type: ignore
            )
        ).all()
        return list(rows)


def get_funds_capability_service() -> _FundsCapabilityService:
    return _FundsCapabilityService()


FundsCapabilityService = Annotated[
    _FundsCapabilityService, Depends(get_funds_capability_service)
]
