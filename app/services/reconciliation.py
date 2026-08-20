"""Reconciliation: accounting for a disbursement with itemised spend and slips.

Phase 3 of FINANCE_TECHNICIAN_IMPLEMENTATION_PLAN.md, implementing
docs/FieldCore_Finance_Technician_Workflow_Spec.md §3.1.5–7 and §3.4.

Deliberately separate from the diesel/generator report: that is operational proof
(litres, runtime, site photos), this is financial proof (what was spent, on what,
with a slip). Finance and Operations sign off on different things, so the two
records link by FK and are never merged.

One reconciliation per disbursement (spec §4). Approval is the event that clears
a technician for their next request (§3.1.7) — which is why nothing else in this
service sets APPROVED, and why a merely SUBMITTED recon still counts as
outstanding in the eligibility check.

Money is Decimal throughout. Totals are recomputed from the lines on every
change rather than incremented, so a stored total can never drift from the lines
that justify it.
"""

from decimal import Decimal
from typing import Annotated, List
from uuid import UUID

from fastapi import Depends
from loguru import logger as LOG
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.exceptions.http import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
)
from app.models import (
    Disbursement,
    FundsRequest,
    Reconciliation,
    ReconciliationCreate,
    ReconciliationLine,
    ReconciliationLineCreate,
    ReconciliationLineResponse,
    ReconciliationLineUpdate,
    ReconciliationResponse,
    Technician,
    User,
)
from app.models.auth import TokenData
from app.services.authorization import (
    ADMIN_MANAGER_ROLES,
    can_read_all_funds,
    get_technician_id_for_user,
    require_funds_capability,
)
from app.services.notification import NotificationTemplates, get_notification_service
from app.utils.enums import (
    FundsCapability,
    FundsRequestStatus,
    ReconciliationStatus,
    UserRole,
)
from app.utils.funcs import utcnow

_ZERO = Decimal("0.00")

# A technician may edit their lines while the recon is DRAFT, and again after
# Finance sends it back. Anything else is locked: a SUBMITTED recon must not
# change under the reviewer, and an APPROVED one is a closed record.
EDITABLE_STATUSES = (ReconciliationStatus.DRAFT, ReconciliationStatus.REJECTED)


def _money(value: Decimal | None) -> float:
    """Decimal → float, once, at the response boundary."""
    return float(value) if value is not None else 0.0


def _format_rand(amount: Decimal) -> str:
    return f"R{amount:,.2f}".replace(",", " ")


class _ReconciliationService:
    # ── Response mapping ──────────────────────────────────────────────────

    def _to_line_response(self, line: ReconciliationLine) -> ReconciliationLineResponse:
        return ReconciliationLineResponse(
            id=line.id,
            created_at=line.created_at,
            updated_at=line.updated_at,
            deleted_at=line.deleted_at,
            reconciliation_id=line.reconciliation_id,
            category=line.category,
            description=line.description,
            incurred_on=line.incurred_on,
            amount=_money(line.amount),
            slip_file_path=line.slip_file_path,
        )

    def _to_response(
        self, recon: Reconciliation, session: Session
    ) -> ReconciliationResponse:
        disbursement = recon.disbursement
        request = disbursement.funds_request if disbursement else None

        technician_name = "Unknown Technician"
        region = None
        if request and request.technician:
            region = request.technician.region
            if request.technician.user:
                technician_name = (
                    f"{request.technician.user.name} {request.technician.user.surname}"
                )

        approver_name = None
        if recon.finance_approved_by_user_id:
            approver = session.exec(
                select(User).where(User.id == recon.finance_approved_by_user_id)
            ).first()
            if approver:
                approver_name = f"{approver.name} {approver.surname}"

        return ReconciliationResponse(
            id=recon.id,
            created_at=recon.created_at,
            updated_at=recon.updated_at,
            deleted_at=recon.deleted_at,
            disbursement_id=recon.disbursement_id,
            status=recon.status,
            total_used=_money(recon.total_used),
            outstanding_balance=_money(recon.outstanding_balance),
            amount_issued=_money(disbursement.amount_issued) if disbursement else 0.0,
            period_start=recon.period_start,
            period_end=recon.period_end,
            submitted_at=recon.submitted_at,
            finance_approved_at=recon.finance_approved_at,
            rejection_reason=recon.rejection_reason,
            is_overdue=recon.is_overdue,
            lines=[
                self._to_line_response(line)
                for line in recon.lines
                if line.deleted_at is None
            ],
            technician_name=technician_name,
            technician_region=region.value if region else None,
            funds_request_id=request.id if request else None,
            funds_request_type=request.type.value if request else None,
            finance_approved_by_name=approver_name,
        )

    # ── Lookups ───────────────────────────────────────────────────────────

    def _get(self, reconciliation_id: UUID, session: Session) -> Reconciliation:
        recon = session.exec(
            select(Reconciliation).where(
                Reconciliation.id == reconciliation_id,
                Reconciliation.deleted_at.is_(None),  # type: ignore
            )
        ).first()
        if not recon:
            raise NotFoundException("reconciliation not found")
        return recon

    def _get_disbursement(
        self, disbursement_id: UUID, session: Session
    ) -> Disbursement:
        disbursement = session.exec(
            select(Disbursement).where(
                Disbursement.id == disbursement_id,
                Disbursement.deleted_at.is_(None),  # type: ignore
            )
        ).first()
        if not disbursement:
            raise NotFoundException("disbursement not found")
        return disbursement

    def _get_line(
        self, recon: Reconciliation, line_id: UUID
    ) -> ReconciliationLine:
        for line in recon.lines:
            if line.id == line_id and line.deleted_at is None:
                return line
        raise NotFoundException("reconciliation line not found")

    def _technician_id_for(self, recon: Reconciliation) -> UUID | None:
        disbursement = recon.disbursement
        if disbursement is None or disbursement.funds_request is None:
            return None
        return disbursement.funds_request.technician_id

    # ── Access ────────────────────────────────────────────────────────────

    def _assert_can_read(
        self, recon: Reconciliation, session: Session, current_user: TokenData
    ) -> None:
        if can_read_all_funds(current_user):
            return
        self._assert_is_owner(recon, session, current_user)

    def _assert_is_owner(
        self, recon: Reconciliation, session: Session, current_user: TokenData
    ) -> None:
        if current_user.role in ADMIN_MANAGER_ROLES:
            return
        if current_user.role == UserRole.TECHNICIAN:
            own_id = get_technician_id_for_user(current_user.user_id, session)
            if self._technician_id_for(recon) == own_id:
                return
        raise ForbiddenException("You may only work on your own reconciliations.")

    def _assert_editable(self, recon: Reconciliation) -> None:
        if recon.status not in EDITABLE_STATUSES:
            raise ConflictException(
                f"A {recon.status.value} reconciliation cannot be edited. "
                "Finance must send it back before further changes."
            )

    # ── Create ────────────────────────────────────────────────────────────

    def create_reconciliation(
        self,
        payload: ReconciliationCreate,
        session: Session,
        current_user: TokenData,
    ) -> ReconciliationResponse:
        """Open a DRAFT against a released disbursement.

        Lines may be supplied up front or added over the week — a technician
        collects slips as they spend, not in one sitting on Thursday.
        """
        disbursement = self._get_disbursement(payload.disbursement_id, session)
        request = disbursement.funds_request

        # Nothing to account for until the money actually reached the technician.
        # Approval and loading are not disbursement (spec §6).
        if disbursement.released_at is None:
            raise ConflictException(
                "These funds have not been released yet, so there is nothing to "
                "reconcile."
            )

        if current_user.role == UserRole.TECHNICIAN:
            own_id = get_technician_id_for_user(current_user.user_id, session)
            if request is None or request.technician_id != own_id:
                raise ForbiddenException(
                    "You may only reconcile your own disbursements."
                )
        elif current_user.role not in ADMIN_MANAGER_ROLES:
            raise ForbiddenException(
                "Only the technician, an administrator or a manager may open a "
                "reconciliation."
            )

        existing = session.exec(
            select(Reconciliation).where(
                Reconciliation.disbursement_id == disbursement.id,
                Reconciliation.deleted_at.is_(None),  # type: ignore
            )
        ).first()
        if existing is not None:
            raise ConflictException(
                "This disbursement already has a reconciliation. Continue editing "
                "that one instead of starting another."
            )

        recon = Reconciliation(
            disbursement_id=disbursement.id,
            status=ReconciliationStatus.DRAFT,
            # Copied from the request so the recon reports in the period the money
            # was raised in, not the period it happened to be captured in.
            period_start=request.period_start if request else utcnow(),
            period_end=request.period_end if request else utcnow(),
        )
        for line_payload in payload.lines:
            recon.lines.append(self._build_line(recon.id, line_payload))
        recon.recompute(disbursement.amount_issued)

        try:
            session.add(recon)
            session.commit()
            session.refresh(recon)
            return self._to_response(recon, session)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error opening reconciliation: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error opening reconciliation: {e}"
            )

    def _build_line(
        self, reconciliation_id: UUID, payload: ReconciliationLineCreate
    ) -> ReconciliationLine:
        return ReconciliationLine(
            reconciliation_id=reconciliation_id,
            category=payload.category,
            description=payload.description,
            incurred_on=payload.incurred_on,
            amount=payload.amount,
            slip_file_path=payload.slip_file_path,
        )

    # ── Lines ─────────────────────────────────────────────────────────────

    def add_line(
        self,
        reconciliation_id: UUID,
        payload: ReconciliationLineCreate,
        session: Session,
        current_user: TokenData,
    ) -> ReconciliationResponse:
        recon = self._get(reconciliation_id, session)
        self._assert_is_owner(recon, session, current_user)
        self._assert_editable(recon)

        recon.lines.append(self._build_line(recon.id, payload))
        return self._commit_lines(recon, session)

    def update_line(
        self,
        reconciliation_id: UUID,
        line_id: UUID,
        payload: ReconciliationLineUpdate,
        session: Session,
        current_user: TokenData,
    ) -> ReconciliationResponse:
        recon = self._get(reconciliation_id, session)
        self._assert_is_owner(recon, session, current_user)
        self._assert_editable(recon)

        line = self._get_line(recon, line_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(line, field, value)
        line.touch()
        return self._commit_lines(recon, session)

    def remove_line(
        self,
        reconciliation_id: UUID,
        line_id: UUID,
        session: Session,
        current_user: TokenData,
    ) -> ReconciliationResponse:
        recon = self._get(reconciliation_id, session)
        self._assert_is_owner(recon, session, current_user)
        self._assert_editable(recon)

        # Soft delete: the slip may already be in storage and a removed line is
        # still part of what the technician once claimed.
        self._get_line(recon, line_id).soft_delete()
        return self._commit_lines(recon, session)

    def _commit_lines(
        self, recon: Reconciliation, session: Session
    ) -> ReconciliationResponse:
        recon.recompute(recon.disbursement.amount_issued)
        try:
            session.add(recon)
            session.commit()
            session.refresh(recon)
            return self._to_response(recon, session)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error updating reconciliation lines: {e}"
            )

    # ── Submit ────────────────────────────────────────────────────────────

    def submit(
        self, reconciliation_id: UUID, session: Session, current_user: TokenData
    ) -> ReconciliationResponse:
        """Hand the recon to Finance. Locks the lines until it is approved or
        sent back."""
        recon = self._get(reconciliation_id, session)
        self._assert_is_owner(recon, session, current_user)
        self._assert_editable(recon)

        live_lines = [line for line in recon.lines if line.deleted_at is None]
        if not live_lines:
            raise BadRequestException(
                "Add at least one expense line before submitting. If none of the "
                "funds were spent, add a line for what was spent and return the "
                "balance — the outstanding amount is calculated from the lines."
            )

        # Spec §3.1.5: proof for every expense. Enforced at submit rather than at
        # line entry so a technician can capture a line on a bad connection and
        # attach the photo once it uploads.
        missing = [
            line for line in live_lines if not (line.slip_file_path or "").strip()
        ]
        if missing:
            raise BadRequestException(
                f"{len(missing)} of {len(live_lines)} expense line(s) have no slip "
                "attached. Every expense needs proof before Finance can approve it."
            )

        recon.recompute(recon.disbursement.amount_issued)
        recon.mark_submitted()

        try:
            session.add(recon)
            session.commit()
            session.refresh(recon)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error submitting reconciliation: {e}"
            )

        self._notify_finance_leads(recon, session)
        return self._to_response(recon, session)

    # ── Finance sign-off ──────────────────────────────────────────────────

    def approve(
        self, reconciliation_id: UUID, session: Session, current_user: TokenData
    ) -> ReconciliationResponse:
        """Finance accepts the accounting. This is what clears the technician for
        their next request (spec §3.1.7)."""
        require_funds_capability(current_user, FundsCapability.FINANCE_LEAD, session)
        recon = self._get(reconciliation_id, session)

        if recon.status is not ReconciliationStatus.SUBMITTED:
            raise ConflictException(
                f"Only a submitted reconciliation can be approved; this one is "
                f"{recon.status.value}."
            )

        recon.mark_approved(current_user.user_id)
        try:
            session.add(recon)
            session.commit()
            session.refresh(recon)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error approving reconciliation: {e}"
            )

        self._notify_technician(
            recon,
            NotificationTemplates.reconciliation_approved(
                _format_rand(recon.total_used),
                _format_rand(recon.outstanding_balance),
            ),
            session,
        )
        return self._to_response(recon, session)

    def reject(
        self,
        reconciliation_id: UUID,
        reason: str,
        session: Session,
        current_user: TokenData,
    ) -> ReconciliationResponse:
        """Send it back to the technician. Stays REJECTED rather than reverting to
        DRAFT, so the dashboard can tell an untouched recon from a returned one."""
        require_funds_capability(current_user, FundsCapability.FINANCE_LEAD, session)
        recon = self._get(reconciliation_id, session)

        if recon.status is not ReconciliationStatus.SUBMITTED:
            raise ConflictException(
                f"Only a submitted reconciliation can be sent back; this one is "
                f"{recon.status.value}."
            )

        recon.mark_rejected(current_user.user_id, reason)
        try:
            session.add(recon)
            session.commit()
            session.refresh(recon)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error rejecting reconciliation: {e}"
            )

        self._notify_technician(
            recon,
            NotificationTemplates.reconciliation_rejected(reason),
            session,
        )
        return self._to_response(recon, session)

    # ── Reads ─────────────────────────────────────────────────────────────

    def read_reconciliations(
        self,
        session: Session,
        current_user: TokenData,
        status: ReconciliationStatus | None = None,
        technician_id: UUID | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[ReconciliationResponse]:
        statement = (
            select(Reconciliation)
            .join(Disbursement, Disbursement.id == Reconciliation.disbursement_id)  # type: ignore[arg-type]
            .join(FundsRequest, FundsRequest.id == Disbursement.funds_request_id)  # type: ignore[arg-type]
            .where(Reconciliation.deleted_at.is_(None))  # type: ignore
        )

        if not can_read_all_funds(current_user):
            own_id = get_technician_id_for_user(current_user.user_id, session)
            statement = statement.where(FundsRequest.technician_id == own_id)
        elif technician_id is not None:
            statement = statement.where(FundsRequest.technician_id == technician_id)

        if status is not None:
            statement = statement.where(Reconciliation.status == status)

        # Oldest submission first: Finance works the queue in the order technicians
        # handed it over, and an unsubmitted draft sorts last by having no date.
        statement = (
            statement.order_by(Reconciliation.submitted_at.asc())  # type: ignore[attr-defined]
            .offset(offset)
            .limit(limit)
        )
        return [self._to_response(r, session) for r in session.exec(statement).all()]

    def read_reconciliation(
        self, reconciliation_id: UUID, session: Session, current_user: TokenData
    ) -> ReconciliationResponse:
        recon = self._get(reconciliation_id, session)
        self._assert_can_read(recon, session, current_user)
        return self._to_response(recon, session)

    def read_for_disbursement(
        self, disbursement_id: UUID, session: Session, current_user: TokenData
    ) -> ReconciliationResponse | None:
        """The recon for one disbursement, or None. Lets the mobile app decide
        between "start reconciling" and "continue" without a 404 round trip."""
        recon = session.exec(
            select(Reconciliation).where(
                Reconciliation.disbursement_id == disbursement_id,
                Reconciliation.deleted_at.is_(None),  # type: ignore
            )
        ).first()
        if recon is None:
            return None
        self._assert_can_read(recon, session, current_user)
        return self._to_response(recon, session)

    def read_outstanding(
        self, session: Session, current_user: TokenData
    ) -> List[dict]:
        """Released disbursements with no approved reconciliation.

        The technician-facing "what do I still owe" list, and the same condition
        the eligibility check uses — released, and not yet signed off.
        """
        statement = (
            select(FundsRequest, Disbursement, Reconciliation)
            .join(Disbursement, Disbursement.funds_request_id == FundsRequest.id)  # type: ignore[arg-type]
            .outerjoin(
                Reconciliation,
                (Reconciliation.disbursement_id == Disbursement.id)  # type: ignore[arg-type]
                & Reconciliation.deleted_at.is_(None),  # type: ignore
            )
            .where(
                FundsRequest.deleted_at.is_(None),  # type: ignore
                FundsRequest.status == FundsRequestStatus.RELEASED,
                Disbursement.deleted_at.is_(None),  # type: ignore
                Disbursement.released_at.is_not(None),  # type: ignore
                (Reconciliation.id.is_(None))  # type: ignore
                | (Reconciliation.status != ReconciliationStatus.APPROVED),
            )
        )

        if not can_read_all_funds(current_user):
            own_id = get_technician_id_for_user(current_user.user_id, session)
            statement = statement.where(FundsRequest.technician_id == own_id)

        rows = session.exec(statement).all()
        return [
            {
                "funds_request_id": request.id,
                "disbursement_id": disbursement.id,
                "reconciliation_id": recon.id if recon else None,
                "reconciliation_status": recon.status.value if recon else None,
                "type": request.type.value,
                "amount_issued": _money(disbursement.amount_issued),
                "total_used": _money(recon.total_used) if recon else 0.0,
                "outstanding_balance": (
                    _money(recon.outstanding_balance)
                    if recon
                    else _money(disbursement.amount_issued)
                ),
                "released_at": disbursement.released_at,
                "period_end": request.period_end,
                "is_overdue": recon.is_overdue if recon else utcnow() > request.period_end,
            }
            for request, disbursement, recon in rows
        ]

    # ── Notifications (best effort) ────────────────────────────────────────

    def _notify_finance_leads(self, recon: Reconciliation, session: Session) -> None:
        try:
            from app.services.authorization import users_with_funds_capability

            recipients = users_with_funds_capability(
                FundsCapability.FINANCE_LEAD, session
            )
            if not recipients:
                LOG.warning(
                    "No active finance_lead holder — reconciliation {} will sit "
                    "unreviewed until one is assigned",
                    recon.id,
                )
                return
            request = (
                recon.disbursement.funds_request if recon.disbursement else None
            )
            technician_name = "A technician"
            if request and request.technician and request.technician.user:
                technician_name = (
                    f"{request.technician.user.name} {request.technician.user.surname}"
                )
            get_notification_service().create_notifications_from_template(
                recipients,
                NotificationTemplates.reconciliation_submitted(
                    technician_name,
                    _format_rand(recon.total_used),
                    _format_rand(recon.outstanding_balance),
                ),
                session,
            )
        except Exception as e:  # pragma: no cover - best effort
            LOG.error("Failed to notify finance leads for recon {}: {}", recon.id, e)

    def _notify_technician(
        self, recon: Reconciliation, template, session: Session
    ) -> None:
        try:
            technician_id = self._technician_id_for(recon)
            if technician_id is None:
                return
            technician = session.exec(
                select(Technician).where(Technician.id == technician_id)
            ).first()
            if technician is None:
                return
            get_notification_service().create_notification_from_template(
                technician.user_id, template, session
            )
        except Exception as e:  # pragma: no cover - best effort
            LOG.error("Failed to notify technician for recon {}: {}", recon.id, e)


def get_reconciliation_service() -> _ReconciliationService:
    return _ReconciliationService()


ReconciliationService = Annotated[
    _ReconciliationService, Depends(get_reconciliation_service)
]
