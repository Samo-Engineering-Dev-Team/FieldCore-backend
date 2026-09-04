"""The funds release chain: submit → approve → load → release.

Phase 2 of FINANCE_TECHNICIAN_IMPLEMENTATION_PLAN.md, implementing
docs/FieldCore_Finance_Technician_Workflow_Spec.md §2, §3 and §6.

Division of labour: FundsRequest owns the transition table and mutates its own
status (pure, session-free, DB-testable). This service owns everything a
transition *means* — the capability check, the Disbursement write, the
notification fan-out and the eligibility gate.

Two rules from the spec shape almost every method here:

  1. Approval alone never releases funds (§6). The three stages are separate
     acts by separate capability holders, so there is no "approve and release"
     shortcut anywhere in this file.
  2. The diesel price is always manually entered and stored per request (§3.1).
     Nothing in this service derives, defaults or back-fills it. A weekly trip
     without a price is rejected at validation rather than completed from a
     lookup table.
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
    FundsRequestCreate,
    FundsRequestResponse,
    FundsRequestUpdate,
    Generator,
    Reconciliation,
    Technician,
    User,
)
from app.models.auth import TokenData
from app.models.funds_request import InvalidFundsTransition
from app.services.authorization import (
    ADMIN_MANAGER_ROLES,
    can_read_all_funds,
    get_technician_id_for_user,
    require_funds_capability,
    users_with_funds_capability,
)
from app.services.notification import NotificationTemplates, get_notification_service
from app.services.system_settings import get_system_settings_service
from app.utils.enums import (
    FundsCapability,
    FundsPriority,
    FundsRequestStatus,
    FundsRequestType,
    ReconciliationStatus,
    UserRole,
)
from app.utils.funcs import funds_period, utcnow

# Which capability may act on a request sitting at a given status. One map drives
# both advancing and rejecting: whoever owns the current stage owns the decision
# at that stage, including the decision to refuse it.
STAGE_CAPABILITY: dict[FundsRequestStatus, FundsCapability] = {
    FundsRequestStatus.PENDING: FundsCapability.APPROVE,
    FundsRequestStatus.APPROVED: FundsCapability.LOAD,
    FundsRequestStatus.LOADED: FundsCapability.RELEASE,
}

HARD_BLOCK_SETTING_KEY = "funds.hard_block_unreconciled"
"""When true, a technician with an unreconciled prior disbursement cannot raise a
new request. Defaults to FALSE: the meeting behind the spec implies Finance
discretion ("so they can take a decision"), and §7 Q1 left enforcement open. A
system setting rather than a constant so Finance can turn enforcement on without
a deploy once they have decided."""


def _money(value: Decimal | float | None) -> float:
    """Decimal → float, once, at the response boundary. Storage and arithmetic
    stay in Decimal; this exists only so the API emits numbers rather than the
    quoted strings Pydantic produces for Decimal in JSON mode."""
    return float(value) if value is not None else 0.0


def _format_rand(amount: Decimal) -> str:
    """R1 234.56 for notification text.

    Plain ASCII space as the thousands separator, not a non-breaking space:
    notification bodies travel through the DB, the API and potentially email,
    and a U+00A0 in there is an encoding surprise for no visual gain.
    """
    return f"R{amount:,.2f}".replace(",", " ")


class _FundsRequestService:
    # ── Response mapping ──────────────────────────────────────────────────

    def _to_response(
        self, request: FundsRequest, session: Session
    ) -> FundsRequestResponse:
        technician_name = "Unknown Technician"
        region = None
        if request.technician:
            region = request.technician.region
            if request.technician.user:
                technician_name = (
                    f"{request.technician.user.name} {request.technician.user.surname}"
                )

        disbursement = self._find_disbursement(request.id, session)
        recon_status = None
        recon_id = None
        amount_issued = None
        if disbursement is not None:
            amount_issued = _money(disbursement.amount_issued)
            recon = self._find_reconciliation(disbursement.id, session)
            if recon is not None:
                recon_id = recon.id
                recon_status = recon.status.value

        return FundsRequestResponse(
            id=request.id,
            created_at=request.created_at,
            updated_at=request.updated_at,
            deleted_at=request.deleted_at,
            technician_id=request.technician_id,
            type=request.type,
            status=request.status,
            priority=request.priority,
            requested_amount=_money(request.requested_amount),
            diesel_price_per_liter=(
                _money(request.diesel_price_per_liter)
                if request.diesel_price_per_liter is not None
                else None
            ),
            expected_amount=(
                _money(request.expected_amount)
                if request.expected_amount is not None
                else None
            ),
            amount_variance=(
                _money(request.amount_variance)
                if request.amount_variance is not None
                else None
            ),
            distance_km=request.distance_km,
            vehicle_efficiency_l_per_100km=request.vehicle_efficiency_l_per_100km,
            site_id=request.site_id,
            generator_id=request.generator_id,
            requested_liters=request.requested_liters,
            gen_runtime_hours=request.gen_runtime_hours,
            description=request.description,
            period_start=request.period_start,
            period_end=request.period_end,
            submitted_at=request.submitted_at,
            rejection_reason=request.rejection_reason,
            technician_name=technician_name,
            technician_region=region.value if region else None,
            site_name=request.site.name if request.site else None,
            generator_display_name=(
                request.generator.name if request.generator else None
            ),
            disbursement_id=disbursement.id if disbursement else None,
            amount_issued=amount_issued,
            reconciliation_id=recon_id,
            reconciliation_status=recon_status,
        )

    # ── Lookups ───────────────────────────────────────────────────────────

    def _get_request(self, request_id: UUID, session: Session) -> FundsRequest:
        request = session.exec(
            select(FundsRequest).where(
                FundsRequest.id == request_id,
                FundsRequest.deleted_at.is_(None),  # type: ignore
            )
        ).first()
        if not request:
            raise NotFoundException("funds request not found")
        return request

    def _find_disbursement(
        self, request_id: UUID, session: Session
    ) -> Disbursement | None:
        return session.exec(
            select(Disbursement).where(
                Disbursement.funds_request_id == request_id,
                Disbursement.deleted_at.is_(None),  # type: ignore
            )
        ).first()

    def _find_reconciliation(
        self, disbursement_id: UUID, session: Session
    ) -> Reconciliation | None:
        return session.exec(
            select(Reconciliation).where(
                Reconciliation.disbursement_id == disbursement_id,
                Reconciliation.deleted_at.is_(None),  # type: ignore
            )
        ).first()

    def _require_disbursement(
        self, request_id: UUID, session: Session
    ) -> Disbursement:
        disbursement = self._find_disbursement(request_id, session)
        if disbursement is None:
            # Only reachable if a request was advanced past PENDING without its
            # disbursement being written — a bug, not a user error.
            raise InternalServerErrorException(
                "funds request has no disbursement record; cannot continue the chain"
            )
        return disbursement

    # ── Validation ────────────────────────────────────────────────────────

    def _validate_for_type(
        self, payload: FundsRequestCreate | FundsRequest, session: Session
    ) -> None:
        """Per-type required fields. Enforced here rather than on the model so the
        message can name the field the technician actually sees on their form."""
        if payload.type is FundsRequestType.WEEKLY_TRIP:
            if payload.diesel_price_per_liter is None:
                raise BadRequestException(
                    "Diesel price per litre is required on a weekly trip request. "
                    "Enter the price from the pump you used — it is never filled in "
                    "for you, because stations and regions differ on the same day."
                )
            return

        if payload.type is FundsRequestType.GENERATOR_REFUEL:
            missing = [
                name
                for name, value in (
                    ("site", payload.site_id),
                    ("generator", payload.generator_id),
                    ("litres requested", payload.requested_liters),
                )
                if value is None
            ]
            if missing:
                raise BadRequestException(
                    "A generator refuel request needs "
                    f"{', '.join(missing)} so the refuel can be traced to a unit "
                    "for invoicing."
                )
            self._assert_generator_belongs_to_site(
                payload.generator_id, payload.site_id, session
            )
            return

        if payload.type is FundsRequestType.MISC and not (payload.description or "").strip():
            raise BadRequestException(
                "Describe what the funds are for on a miscellaneous request."
            )

    def _assert_generator_belongs_to_site(
        self, generator_id: UUID | None, site_id: UUID | None, session: Session
    ) -> None:
        if generator_id is None or site_id is None:
            return
        generator = session.exec(
            select(Generator).where(
                Generator.id == generator_id,
                Generator.deleted_at.is_(None),  # type: ignore
            )
        ).first()
        if generator is None:
            raise NotFoundException("generator not found")
        if generator.site_id is None:
            # A unit may now be registered without a site. It cannot be refuelled
            # until it is placed, because the refuel is invoiced against the site.
            raise BadRequestException(
                f"{generator.name} is not assigned to a site yet. Assign it to the "
                "site being refuelled before raising the request."
            )
        if generator.site_id != site_id:
            raise BadRequestException(
                "That generator belongs to a different site. Pick a unit at the "
                "site being refuelled, or the refuel cannot be invoiced correctly."
            )
        if not generator.is_active:
            raise BadRequestException(
                f"{generator.name} is decommissioned and cannot be refuelled."
            )

    # ── Eligibility (spec §6, §7 Q1) ──────────────────────────────────────

    def outstanding_reconciliations(
        self, technician_id: UUID, session: Session
    ) -> List[tuple[FundsRequest, Disbursement, Reconciliation | None]]:
        """Released disbursements whose reconciliation is missing or unapproved.

        Approval of the recon is what clears a technician (§3.1.7), so a
        SUBMITTED-but-unapproved recon still counts as outstanding — the money is
        not accounted for until Finance says it is. Returns the disbursement and
        (if any) reconciliation alongside the request, since eligibility needs the
        actual issued/spent figures, not the originally requested amount.
        """
        rows = session.exec(
            select(FundsRequest, Disbursement, Reconciliation)
            .join(Disbursement, Disbursement.funds_request_id == FundsRequest.id)  # type: ignore[arg-type]
            .outerjoin(
                Reconciliation,
                (Reconciliation.disbursement_id == Disbursement.id)  # type: ignore[arg-type]
                & Reconciliation.deleted_at.is_(None),  # type: ignore
            )
            .where(
                FundsRequest.technician_id == technician_id,
                FundsRequest.deleted_at.is_(None),  # type: ignore
                Disbursement.deleted_at.is_(None),  # type: ignore
                Disbursement.released_at.is_not(None),  # type: ignore
                (Reconciliation.id.is_(None))  # type: ignore
                | (Reconciliation.status != ReconciliationStatus.APPROVED),
            )
        ).all()
        return list(rows)

    def outstanding_standalone_reconciliations(
        self, technician_id: UUID, session: Session
    ) -> List[Reconciliation]:
        """Standalone recons (no disbursement) still unapproved. Counts toward
        eligibility the same as a disbursement-linked one: an open reconciliation
        is an open reconciliation regardless of where the money it accounts for
        came from."""
        rows = session.exec(
            select(Reconciliation).where(
                Reconciliation.technician_id == technician_id,
                Reconciliation.disbursement_id.is_(None),  # type: ignore
                Reconciliation.deleted_at.is_(None),  # type: ignore
                Reconciliation.status != ReconciliationStatus.APPROVED,
            )
        ).all()
        return list(rows)

    def released_disbursements(
        self, technician_id: UUID, session: Session
    ) -> List[tuple[FundsRequest, Disbursement, Reconciliation | None]]:
        """Every released disbursement, whatever its reconciliation's status.

        Unlike outstanding_reconciliations, this is NOT scoped to unapproved
        recons — it's for "funds still physically held", and approval signs off
        the paperwork, it does not hand any leftover cash back. A R450
        disbursement reconciled for R400 and approved still leaves the
        technician holding R50 until they account for it (e.g. via a later
        standalone recon), whatever Finance's sign-off status on the R400 says.
        """
        rows = session.exec(
            select(FundsRequest, Disbursement, Reconciliation)
            .join(Disbursement, Disbursement.funds_request_id == FundsRequest.id)  # type: ignore[arg-type]
            .outerjoin(
                Reconciliation,
                (Reconciliation.disbursement_id == Disbursement.id)  # type: ignore[arg-type]
                & Reconciliation.deleted_at.is_(None),  # type: ignore
            )
            .where(
                FundsRequest.technician_id == technician_id,
                FundsRequest.deleted_at.is_(None),  # type: ignore
                Disbursement.deleted_at.is_(None),  # type: ignore
                Disbursement.released_at.is_not(None),  # type: ignore
            )
        ).all()
        return list(rows)

    def all_standalone_reconciliations(
        self, technician_id: UUID, session: Session
    ) -> List[Reconciliation]:
        """Every standalone recon, whatever its status — same reasoning as
        released_disbursements above."""
        rows = session.exec(
            select(Reconciliation).where(
                Reconciliation.technician_id == technician_id,
                Reconciliation.disbursement_id.is_(None),  # type: ignore
                Reconciliation.deleted_at.is_(None),  # type: ignore
            )
        ).all()
        return list(rows)

    def check_eligibility(
        self,
        technician_id: UUID,
        request_type: FundsRequestType,
        session: Session,
    ) -> dict:
        """Soft flag always; hard block only when Finance has switched it on.

        Generator refuels are never blocked, whatever the setting says. Refusing
        a refuel because a *trip* recon is late means a site's generator runs dry
        — the operational risk the spec calls out in §3.2. Enforcement exists to
        chase paperwork, not to strand a site.
        """
        outstanding = self.outstanding_reconciliations(technician_id, session)
        standalone = self.outstanding_standalone_reconciliations(technician_id, session)
        settings = get_system_settings_service()
        enforced = bool(settings.get_setting(HARD_BLOCK_SETTING_KEY, session, False))
        exempt = request_type is FundsRequestType.GENERATOR_REFUEL

        blocked = bool(outstanding or standalone) and enforced and not exempt

        # What is actually unaccounted for, i.e. still needs Finance's sign-off:
        # issued minus whatever has already been reconciled (even in a
        # SUBMITTED, not-yet-approved recon) — not the full issued amount, or a
        # partially-documented disbursement would still read as fully
        # outstanding. Scoped to unapproved recons only — this is the compliance
        # queue, and approval is what clears it (spec §3.1.7).
        unreconciled_amount = sum(
            (
                recon.outstanding_balance if recon is not None else disbursement.amount_issued
                for _, disbursement, recon in outstanding
            ),
            start=Decimal("0.00"),
        ) + sum(
            (recon.outstanding_balance for recon in standalone),
            start=Decimal("0.00"),
        )

        # Funds still physically held, independent of sign-off: approval settles
        # the paperwork on whatever WAS documented, it does not hand back
        # whatever wasn't spent. A R450 disbursement reconciled for R400 and
        # approved still leaves R50 in the technician's pocket until they
        # account for it — so this sums every released disbursement and every
        # standalone recon regardless of status, clamping each to >= 0 (an
        # overspent line means they're owed money, not holding any).
        all_disbursements = self.released_disbursements(technician_id, session)
        all_standalone = self.all_standalone_reconciliations(technician_id, session)
        funds_in_possession = sum(
            (
                max(
                    Decimal("0.00"),
                    recon.outstanding_balance if recon is not None else disbursement.amount_issued,
                )
                for _, disbursement, recon in all_disbursements
            ),
            start=Decimal("0.00"),
        ) + sum(
            (max(Decimal("0.00"), recon.outstanding_balance) for recon in all_standalone),
            start=Decimal("0.00"),
        )

        return {
            "eligible": not blocked,
            "enforcement_enabled": enforced,
            "exempt_from_enforcement": exempt,
            "outstanding_count": len(outstanding) + len(standalone),
            "outstanding_request_ids": [r.id for r, _, _ in outstanding],
            # Kept as the true unaccounted-for figure (was previously the sum of
            # requested_amount, which overstated a partially-reconciled disbursement).
            "outstanding_total": _money(unreconciled_amount),
            "funds_in_possession": _money(funds_in_possession),
            "unreconciled_amount": _money(unreconciled_amount),
        }

    # ── Create / update / cancel ──────────────────────────────────────────

    def create_funds_request(
        self,
        payload: FundsRequestCreate,
        session: Session,
        current_user: TokenData,
    ) -> FundsRequestResponse:
        technician_id = self._resolve_technician_id(payload.technician_id, session, current_user)
        self._validate_for_type(payload, session)

        eligibility = self.check_eligibility(technician_id, payload.type, session)
        if not eligibility["eligible"]:
            raise ConflictException(
                f"{eligibility['outstanding_count']} prior disbursement(s) are still "
                "unreconciled. Submit and have those reconciliations approved before "
                "requesting more funds."
            )

        period_start, period_end = funds_period()

        request = FundsRequest(
            technician_id=technician_id,
            type=payload.type,
            # Forced server-side, never read from the client (spec §2).
            priority=(
                FundsPriority.HIGH
                if payload.type is FundsRequestType.GENERATOR_REFUEL
                else FundsPriority.NORMAL
            ),
            status=FundsRequestStatus.PENDING,
            requested_amount=payload.requested_amount,
            diesel_price_per_liter=payload.diesel_price_per_liter,
            distance_km=payload.distance_km,
            vehicle_efficiency_l_per_100km=payload.vehicle_efficiency_l_per_100km,
            site_id=payload.site_id,
            generator_id=payload.generator_id,
            requested_liters=payload.requested_liters,
            gen_runtime_hours=payload.gen_runtime_hours,
            description=payload.description,
            period_start=period_start,
            period_end=period_end,
            submitted_at=utcnow(),
        )

        try:
            session.add(request)
            session.commit()
            session.refresh(request)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error creating funds request: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error creating funds request: {e}"
            )

        self._notify_stage_holders(request, FundsCapability.APPROVE, session)
        return self._to_response(request, session)

    def _resolve_technician_id(
        self,
        supplied: UUID | None,
        session: Session,
        current_user: TokenData,
    ) -> UUID:
        """A technician submits for themselves; management may submit on behalf.

        Mirrors AccessRequestCreate: the wire field is optional and resolved from
        the token when absent.
        """
        if current_user.role == UserRole.TECHNICIAN:
            own_id = get_technician_id_for_user(current_user.user_id, session)
            if supplied is not None and supplied != own_id:
                raise ForbiddenException(
                    "You can only raise funds requests for yourself."
                )
            return own_id

        if supplied is None:
            raise BadRequestException(
                "technician_id is required when raising a request on someone's behalf."
            )
        if current_user.role not in ADMIN_MANAGER_ROLES:
            raise ForbiddenException(
                "Only a technician, an administrator or a manager may raise a funds request."
            )
        technician = session.exec(
            select(Technician).where(
                Technician.id == supplied,
                Technician.deleted_at.is_(None),  # type: ignore
            )
        ).first()
        if technician is None:
            raise NotFoundException("technician not found")
        return supplied

    def update_funds_request(
        self,
        request_id: UUID,
        payload: FundsRequestUpdate,
        session: Session,
        current_user: TokenData,
    ) -> FundsRequestResponse:
        """Editable only while PENDING — once an approver has acted, the figures
        they approved must not move under them."""
        request = self._get_request(request_id, session)
        self._assert_own_or_management(request, session, current_user)

        if request.status is not FundsRequestStatus.PENDING:
            raise ConflictException(
                f"A {request.status.value} request can no longer be edited. "
                "Ask the approver to reject it and raise a corrected request."
            )

        data = payload.model_dump(exclude_unset=True)
        for field, value in data.items():
            setattr(request, field, value)
        # Re-validate against the type after the edit; a technician can clear a
        # field that their request type still requires.
        self._validate_for_type(request, session)
        request.touch()

        try:
            session.add(request)
            session.commit()
            session.refresh(request)
            return self._to_response(request, session)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error updating funds request: {e}"
            )

    def cancel_funds_request(
        self, request_id: UUID, session: Session, current_user: TokenData
    ) -> FundsRequestResponse:
        request = self._get_request(request_id, session)
        self._assert_own_or_management(request, session, current_user)
        try:
            request.mark_cancelled()
        except InvalidFundsTransition as e:
            raise ConflictException(str(e))

        session.add(request)
        session.commit()
        session.refresh(request)
        return self._to_response(request, session)

    def _assert_own_or_management(
        self, request: FundsRequest, session: Session, current_user: TokenData
    ) -> None:
        if current_user.role in ADMIN_MANAGER_ROLES:
            return
        if current_user.role == UserRole.TECHNICIAN:
            own_id = get_technician_id_for_user(current_user.user_id, session)
            if request.technician_id == own_id:
                return
        raise ForbiddenException("You may only act on your own funds requests.")

    # ── The chain ─────────────────────────────────────────────────────────

    def approve(
        self, request_id: UUID, session: Session, current_user: TokenData
    ) -> FundsRequestResponse:
        """Stage 1. Creates the Disbursement, seeded with the requested amount.

        Approval records intent and identity — it does NOT put money anywhere.
        The loader states what was actually loaded at stage 2 (spec §6).
        """
        request = self._get_request(request_id, session)
        assignment = self._authorise_stage(
            request,
            FundsRequestStatus.APPROVED,
            FundsCapability.APPROVE,
            session,
            current_user,
        )
        request.transition_to(FundsRequestStatus.APPROVED)

        disbursement = Disbursement(
            funds_request_id=request.id,
            amount_issued=request.requested_amount,
            approved_by_user_id=current_user.user_id,
            approved_at=utcnow(),
            is_fallback_approval=assignment.is_fallback,
        )
        if assignment.is_fallback:
            LOG.info(
                "Funds request {} approved by fallback approver {}",
                request.id,
                current_user.user_id,
            )

        return self._commit_stage(
            request, session, extra=disbursement, notify=FundsCapability.LOAD
        )

    def load(
        self,
        request_id: UUID,
        amount_issued: Decimal | None,
        session: Session,
        current_user: TokenData,
    ) -> FundsRequestResponse:
        """Stage 2. The loader may correct the amount to what was really loaded —
        spec §3.4 has them reporting weekly on amount issued, so the issued figure
        is theirs to state rather than an echo of the request."""
        request = self._get_request(request_id, session)
        self._authorise_stage(
            request,
            FundsRequestStatus.LOADED,
            FundsCapability.LOAD,
            session,
            current_user,
        )
        # Only fetched once the move is known to be legal; before that its
        # absence is expected, not an error worth a 500.
        disbursement = self._require_disbursement(request.id, session)
        request.transition_to(FundsRequestStatus.LOADED)

        if amount_issued is not None:
            disbursement.amount_issued = amount_issued
        disbursement.loaded_by_user_id = current_user.user_id
        disbursement.loaded_at = utcnow()
        disbursement.touch()

        return self._commit_stage(
            request, session, extra=disbursement, notify=FundsCapability.RELEASE
        )

    def release(
        self, request_id: UUID, session: Session, current_user: TokenData
    ) -> FundsRequestResponse:
        """Stage 3. The technician can now act on the funds."""
        request = self._get_request(request_id, session)
        self._authorise_stage(
            request,
            FundsRequestStatus.RELEASED,
            FundsCapability.RELEASE,
            session,
            current_user,
        )
        disbursement = self._require_disbursement(request.id, session)
        request.transition_to(FundsRequestStatus.RELEASED)

        disbursement.released_by_user_id = current_user.user_id
        disbursement.released_at = utcnow()
        disbursement.touch()

        response = self._commit_stage(request, session, extra=disbursement, notify=None)
        self._notify_technician(
            request,
            NotificationTemplates.funds_request_released(
                _format_rand(disbursement.amount_issued),
                request.type.value,
                request.is_high_priority,
            ),
            session,
        )
        return response

    def reject(
        self, request_id: UUID, reason: str, session: Session, current_user: TokenData
    ) -> FundsRequestResponse:
        """Available at every live stage, to whoever holds that stage."""
        request = self._get_request(request_id, session)
        self._require_stage_capability(request, session, current_user)

        try:
            request.mark_rejected(current_user.user_id, reason)
        except InvalidFundsTransition as e:
            raise ConflictException(str(e))

        session.add(request)
        session.commit()
        session.refresh(request)

        self._notify_technician(
            request,
            NotificationTemplates.funds_request_rejected(
                request.type.value, _format_rand(request.requested_amount), reason
            ),
            session,
        )
        return self._to_response(request, session)

    def _authorise_stage(
        self,
        request: FundsRequest,
        target: FundsRequestStatus,
        capability: FundsCapability,
        session: Session,
        current_user: TokenData,
    ):
        """Check the capability the ACTION needs, then that the move is legal.

        Order matters. Capability first, so an unauthorised caller gets 403
        rather than a description of the request's state. Transition second, so
        calling /load on a pending request gets a 409 naming the stage problem
        instead of a 500 about a disbursement that was never supposed to exist
        yet.

        The capability is the one the action requires, NOT the one the current
        stage happens to map to — those coincide on the happy path and diverge
        precisely when the caller is doing something invalid, which is when a
        clear message matters most.
        """
        assignment = require_funds_capability(current_user, capability, session)
        if not request.can_transition_to(target):
            allowed = ", ".join(
                s.value for s in request.ALLOWED_TRANSITIONS.get(request.status, ())
            ) or "nothing"
            raise ConflictException(
                f"Cannot move a {request.status.value} request to {target.value}. "
                f"From {request.status.value} the only valid next step is: {allowed}."
            )
        return assignment

    def _require_stage_capability(
        self, request: FundsRequest, session: Session, current_user: TokenData
    ):
        capability = STAGE_CAPABILITY.get(request.status)
        if capability is None:
            raise ConflictException(
                f"A {request.status.value} request has no remaining stage to act on."
            )
        return require_funds_capability(current_user, capability, session)

    def _commit_stage(
        self,
        request: FundsRequest,
        session: Session,
        extra: Disbursement | None,
        notify: FundsCapability | None,
    ) -> FundsRequestResponse:
        try:
            session.add(request)
            if extra is not None:
                session.add(extra)
            session.commit()
            session.refresh(request)
        except IntegrityError as e:
            session.rollback()
            raise ConflictException(f"Error advancing funds request: {e.orig}")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(
                f"Unexpected error advancing funds request: {e}"
            )

        if notify is not None:
            self._notify_stage_holders(request, notify, session)
        return self._to_response(request, session)

    # ── Notifications ─────────────────────────────────────────────────────
    #
    # Best-effort: a notification failure must never roll back a released
    # disbursement. Same posture as the presence heartbeats.

    def _notify_stage_holders(
        self, request: FundsRequest, capability: FundsCapability, session: Session
    ) -> None:
        try:
            recipients = users_with_funds_capability(capability, session)
            if not recipients:
                LOG.warning(
                    "No active holder of the '{}' funds capability — request {} will "
                    "sit unactioned until one is assigned",
                    capability.value,
                    request.id,
                )
                return
            technician_name = self._technician_name(request, session)
            amount = _format_rand(request.requested_amount)
            if capability is FundsCapability.APPROVE:
                template = NotificationTemplates.funds_request_submitted(
                    technician_name, request.type.value, amount, request.is_high_priority
                )
            elif capability is FundsCapability.LOAD:
                template = NotificationTemplates.funds_request_approved(
                    technician_name, amount, request.is_high_priority
                )
            else:
                template = NotificationTemplates.funds_request_loaded(
                    technician_name, amount, request.is_high_priority
                )
            get_notification_service().create_notifications_from_template(
                recipients, template, session
            )
        except Exception as e:  # pragma: no cover - best effort
            LOG.error("Failed to notify '{}' holders: {}", capability.value, e)

    def _notify_technician(self, request: FundsRequest, template, session: Session) -> None:
        try:
            technician = session.exec(
                select(Technician).where(Technician.id == request.technician_id)
            ).first()
            if technician is None:
                return
            get_notification_service().create_notification_from_template(
                technician.user_id, template, session
            )
        except Exception as e:  # pragma: no cover - best effort
            LOG.error("Failed to notify technician for request {}: {}", request.id, e)

    def _technician_name(self, request: FundsRequest, session: Session) -> str:
        row = session.exec(
            select(User)
            .join(Technician, Technician.user_id == User.id)  # type: ignore[arg-type]
            .where(Technician.id == request.technician_id)
        ).first()
        return f"{row.name} {row.surname}" if row else "A technician"

    # ── Reads ─────────────────────────────────────────────────────────────

    def read_funds_requests(
        self,
        session: Session,
        current_user: TokenData,
        status: FundsRequestStatus | None = None,
        request_type: FundsRequestType | None = None,
        technician_id: UUID | None = None,
        site_id: UUID | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[FundsRequestResponse]:
        statement = select(FundsRequest).where(FundsRequest.deleted_at.is_(None))  # type: ignore

        # A technician sees only their own requests, whatever filters they pass.
        if not can_read_all_funds(current_user):
            own_id = get_technician_id_for_user(current_user.user_id, session)
            statement = statement.where(FundsRequest.technician_id == own_id)
        elif technician_id is not None:
            statement = statement.where(FundsRequest.technician_id == technician_id)

        if status is not None:
            statement = statement.where(FundsRequest.status == status)
        if request_type is not None:
            statement = statement.where(FundsRequest.type == request_type)
        if site_id is not None:
            statement = statement.where(FundsRequest.site_id == site_id)

        # High priority first, then oldest first: a generator refuel should surface
        # above a week-old trip request in the approver's queue (spec §3.2.3).
        statement = (
            statement.order_by(
                FundsRequest.priority.desc(),  # type: ignore[attr-defined]
                FundsRequest.submitted_at.asc(),  # type: ignore[attr-defined]
            )
            .offset(offset)
            .limit(limit)
        )
        return [self._to_response(r, session) for r in session.exec(statement).all()]

    def read_funds_request(
        self, request_id: UUID, session: Session, current_user: TokenData
    ) -> FundsRequestResponse:
        request = self._get_request(request_id, session)
        if not can_read_all_funds(current_user):
            self._assert_own_or_management(request, session, current_user)
        return self._to_response(request, session)

    def read_my_eligibility(self, session: Session, current_user: TokenData) -> dict:
        technician_id = get_technician_id_for_user(current_user.user_id, session)
        return self.check_eligibility(
            technician_id, FundsRequestType.WEEKLY_TRIP, session
        )


def get_funds_request_service() -> _FundsRequestService:
    return _FundsRequestService()


FundsRequestService = Annotated[
    _FundsRequestService, Depends(get_funds_request_service)
]
