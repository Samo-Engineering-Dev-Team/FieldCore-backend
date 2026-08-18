"""Phase 1 unit tests for the Finance–Technician workflow primitives.

Every test here is deliberately DB-free. The `db_session` fixture in
conftest.py currently skips any test that uses it, so coverage of the chain and
the money arithmetic has to come from pure functions on the models and in
app.utils.funcs. That constraint is why FundsRequest.transition_to and
Reconciliation.recompute take no session and touch no query — see
FINANCE_TECHNICIAN_IMPLEMENTATION_PLAN.md Phase 5 item 1.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.funds_request import FundsRequest, InvalidFundsTransition
from app.models.reconciliation import Reconciliation, ReconciliationLine
from app.utils.enums import (
    ExpenseCategory,
    FundsPriority,
    FundsRequestStatus,
    FundsRequestType,
    ReconciliationStatus,
)
from app.utils.funcs import SAST, funds_period, funds_period_for_date

# ── helpers ───────────────────────────────────────────────────────────────


def make_request(
    *,
    status: FundsRequestStatus = FundsRequestStatus.PENDING,
    type_: FundsRequestType = FundsRequestType.WEEKLY_TRIP,
    amount: str = "1000.00",
    distance_km: float | None = None,
    efficiency: float | None = None,
    price: str | None = None,
) -> FundsRequest:
    start, end = funds_period(datetime(2026, 8, 17, 8, 0, tzinfo=SAST))
    return FundsRequest(
        technician_id=uuid4(),
        type=type_,
        status=status,
        priority=(
            FundsPriority.HIGH
            if type_ is FundsRequestType.GENERATOR_REFUEL
            else FundsPriority.NORMAL
        ),
        requested_amount=Decimal(amount),
        diesel_price_per_liter=Decimal(price) if price is not None else None,
        distance_km=distance_km,
        vehicle_efficiency_l_per_100km=efficiency,
        period_start=start,
        period_end=end,
    )


def make_recon(amounts: list[str], *, deleted: list[int] | None = None) -> Reconciliation:
    start, end = funds_period(datetime(2026, 8, 17, 8, 0, tzinfo=SAST))
    recon = Reconciliation(
        disbursement_id=uuid4(), period_start=start, period_end=end
    )
    deleted = deleted or []
    for i, amount in enumerate(amounts):
        line = ReconciliationLine(
            reconciliation_id=recon.id,
            category=ExpenseCategory.FUEL,
            amount=Decimal(amount),
            incurred_on=date(2026, 8, 17),
        )
        if i in deleted:
            line.soft_delete()
        recon.lines.append(line)
    return recon


# ── the release chain (spec §2) ────────────────────────────────────────────


def test_happy_path_walks_the_three_stages():
    req = make_request()
    for target in (
        FundsRequestStatus.APPROVED,
        FundsRequestStatus.LOADED,
        FundsRequestStatus.RELEASED,
    ):
        req.transition_to(target)
    assert req.status is FundsRequestStatus.RELEASED


def test_approval_alone_cannot_release_funds():
    """Spec §6: approval never releases funds. PENDING must not skip to RELEASED,
    and an APPROVED request must still pass through LOADED."""
    pending = make_request()
    with pytest.raises(InvalidFundsTransition):
        pending.transition_to(FundsRequestStatus.RELEASED)
    assert pending.status is FundsRequestStatus.PENDING

    approved = make_request(status=FundsRequestStatus.APPROVED)
    with pytest.raises(InvalidFundsTransition):
        approved.transition_to(FundsRequestStatus.RELEASED)
    assert approved.status is FundsRequestStatus.APPROVED


def test_loading_requires_prior_approval():
    req = make_request()
    with pytest.raises(InvalidFundsTransition):
        req.transition_to(FundsRequestStatus.LOADED)


@pytest.mark.parametrize(
    "terminal",
    [
        FundsRequestStatus.RELEASED,
        FundsRequestStatus.REJECTED,
        FundsRequestStatus.CANCELLED,
    ],
)
def test_terminal_states_admit_no_further_transition(terminal):
    for target in FundsRequestStatus:
        req = make_request(status=terminal)
        with pytest.raises(InvalidFundsTransition):
            req.transition_to(target)


def test_every_reachable_state_rejects_every_disallowed_target():
    """Exhaustive sweep of the transition table — the table itself is the spec,
    so assert against it directly rather than restating it."""
    for source in FundsRequestStatus:
        allowed = FundsRequest.ALLOWED_TRANSITIONS[source]
        for target in FundsRequestStatus:
            req = make_request(status=source)
            if target in allowed:
                req.transition_to(target)
                assert req.status is target
            else:
                with pytest.raises(InvalidFundsTransition):
                    req.transition_to(target)
                assert req.status is source


def test_rejection_records_who_and_why():
    req = make_request()
    approver = uuid4()
    req.mark_rejected(approver, "Diesel price not entered")
    assert req.status is FundsRequestStatus.REJECTED
    assert req.rejected_by_user_id == approver
    assert req.rejection_reason == "Diesel price not entered"
    assert req.rejected_at is not None


def test_a_request_can_be_rejected_at_any_live_stage():
    for source in (
        FundsRequestStatus.PENDING,
        FundsRequestStatus.APPROVED,
        FundsRequestStatus.LOADED,
    ):
        req = make_request(status=source)
        req.mark_rejected(uuid4(), "reason")
        assert req.status is FundsRequestStatus.REJECTED


def test_only_a_pending_request_can_be_cancelled():
    req = make_request()
    req.mark_cancelled()
    assert req.status is FundsRequestStatus.CANCELLED
    assert req.cancelled_at is not None

    for source in (FundsRequestStatus.APPROVED, FundsRequestStatus.LOADED):
        with pytest.raises(InvalidFundsTransition):
            make_request(status=source).mark_cancelled()


def test_generator_refuel_is_high_priority():
    """Spec §2: the single exception to 'no priority tiers'."""
    refuel = make_request(type_=FundsRequestType.GENERATOR_REFUEL)
    assert refuel.is_high_priority
    assert not make_request(type_=FundsRequestType.WEEKLY_TRIP).is_high_priority


# ── trip variance (spec §3.1) ──────────────────────────────────────────────


def test_expected_amount_from_trip_inputs():
    # 400 km at 9 L/100km = 36 L; 36 L at R23.50 = R846.00
    req = make_request(amount="846.00", distance_km=400, efficiency=9, price="23.50")
    assert req.expected_amount == Decimal("846.00")
    assert req.amount_variance == Decimal("0.00")


def test_variance_is_signed_and_exact():
    req = make_request(amount="900.00", distance_km=400, efficiency=9, price="23.50")
    assert req.amount_variance == Decimal("54.00")

    under = make_request(amount="800.00", distance_km=400, efficiency=9, price="23.50")
    assert under.amount_variance == Decimal("-46.00")


def test_expected_amount_uses_decimal_not_float():
    """A float pipeline gives 0.30000000000000004 for this shape. Money maths
    must be exact — this is the reason these columns are NUMERIC."""
    req = make_request(amount="0.30", distance_km=10, efficiency=10, price="0.3000")
    assert req.expected_amount == Decimal("0.30")
    assert isinstance(req.expected_amount, Decimal)


def test_expected_amount_rounds_half_up_to_cents():
    # 100 km at 10 L/100km = 10 L; 10 L at R1.2345 = R12.345 -> R12.35
    req = make_request(amount="12.35", distance_km=100, efficiency=10, price="1.2345")
    assert req.expected_amount == Decimal("12.35")


def test_no_expected_amount_without_a_manually_entered_price():
    """Diesel price is never derived (spec §3.1 rule), so an absent price yields
    no expectation rather than a guess from some other source."""
    req = make_request(distance_km=400, efficiency=9, price=None)
    assert req.expected_amount is None
    assert req.amount_variance is None


def test_no_expected_amount_for_non_trip_types():
    for type_ in (FundsRequestType.GENERATOR_REFUEL, FundsRequestType.MISC):
        req = make_request(type_=type_, distance_km=400, efficiency=9, price="23.50")
        assert req.expected_amount is None


# ── reconciliation arithmetic (spec §3.1.6) ────────────────────────────────


def test_totals_and_balance():
    recon = make_recon(["250.50", "100.25", "49.25"])
    recon.recompute(Decimal("500.00"))
    assert recon.total_used == Decimal("400.00")
    assert recon.outstanding_balance == Decimal("100.00")


def test_balance_is_negative_when_the_technician_overspent():
    recon = make_recon(["600.00"])
    recon.recompute(Decimal("500.00"))
    assert recon.outstanding_balance == Decimal("-100.00")


def test_fully_spent_disbursement_leaves_no_outstanding_balance():
    recon = make_recon(["500.00"])
    recon.recompute(Decimal("500.00"))
    assert recon.outstanding_balance == Decimal("0.00")


def test_recompute_excludes_soft_deleted_lines():
    """BaseDB.soft_delete only sets deleted_at, so every read filters for itself.
    A removed line must stop counting against the balance."""
    recon = make_recon(["100.00", "50.00"], deleted=[1])
    recon.recompute(Decimal("200.00"))
    assert recon.total_used == Decimal("100.00")
    assert recon.outstanding_balance == Decimal("100.00")


def test_empty_reconciliation_accounts_for_nothing():
    recon = make_recon([])
    recon.recompute(Decimal("750.00"))
    assert recon.total_used == Decimal("0.00")
    assert recon.outstanding_balance == Decimal("750.00")


def test_cent_precision_survives_many_lines():
    """Ten lines of R0.10 must total exactly R1.00. In float arithmetic this
    sums to 0.9999999999999999."""
    recon = make_recon(["0.10"] * 10)
    recon.recompute(Decimal("1.00"))
    assert recon.total_used == Decimal("1.00")
    assert recon.outstanding_balance == Decimal("0.00")


def test_approval_is_what_settles_a_reconciliation():
    recon = make_recon(["100.00"])
    recon.mark_submitted()
    assert recon.status is ReconciliationStatus.SUBMITTED
    assert recon.submitted_at is not None
    assert not recon.is_settled

    lead = uuid4()
    recon.mark_approved(lead)
    assert recon.status is ReconciliationStatus.APPROVED
    assert recon.finance_approved_by_user_id == lead
    assert recon.is_settled


def test_rejection_is_distinguishable_from_an_untouched_draft():
    recon = make_recon(["100.00"])
    recon.mark_submitted()
    recon.mark_rejected(uuid4(), "Slip missing for the toll line")
    assert recon.status is ReconciliationStatus.REJECTED
    assert recon.status is not ReconciliationStatus.DRAFT
    assert recon.rejection_reason == "Slip missing for the toll line"
    assert recon.finance_approved_at is None
    assert not recon.is_settled


def test_reapproval_clears_a_previous_rejection_reason():
    recon = make_recon(["100.00"])
    recon.mark_rejected(uuid4(), "Slip missing")
    recon.mark_approved(uuid4())
    assert recon.rejection_reason is None


def test_overdue_only_applies_to_an_unsubmitted_past_period():
    past_start, past_end = funds_period_for_date(date(2026, 1, 5))
    recon = make_recon(["100.00"])
    recon.period_start, recon.period_end = past_start, past_end
    assert recon.is_overdue

    recon.mark_submitted()
    assert not recon.is_overdue


def test_current_period_recon_is_not_yet_overdue():
    recon = make_recon(["100.00"])
    recon.period_start, recon.period_end = funds_period()
    assert not recon.is_overdue


# ── the Friday–Thursday period (spec §3.1/§3.4) ────────────────────────────


def test_period_runs_friday_to_thursday():
    start, end = funds_period(datetime(2026, 8, 17, 8, 0, tzinfo=SAST))
    assert start.astimezone(SAST).strftime("%A") == "Friday"
    assert end.astimezone(SAST).strftime("%A") == "Thursday"
    assert start.astimezone(SAST).hour == 0
    assert start.astimezone(SAST).minute == 0


def test_period_is_inclusive_of_its_bounds():
    start, end = funds_period(datetime(2026, 8, 17, 8, 0, tzinfo=SAST))
    assert end - start == timedelta(days=7) - timedelta(microseconds=1)


@pytest.mark.parametrize(
    "day,label",
    [
        (datetime(2026, 8, 14, 0, 0, tzinfo=SAST), "Friday start"),
        (datetime(2026, 8, 15, 12, 0, tzinfo=SAST), "Saturday"),
        (datetime(2026, 8, 17, 8, 0, tzinfo=SAST), "Monday"),
        (datetime(2026, 8, 20, 23, 59, 59, tzinfo=SAST), "Thursday end"),
    ],
)
def test_every_day_of_one_cycle_maps_to_the_same_period(day, label):
    start, end = funds_period(day)
    assert start.astimezone(SAST).date() == date(2026, 8, 14), label
    assert end.astimezone(SAST).date() == date(2026, 8, 20), label


def test_the_next_friday_opens_a_new_period():
    _, prev_end = funds_period(datetime(2026, 8, 20, 23, 59, 59, tzinfo=SAST))
    next_start, _ = funds_period(datetime(2026, 8, 21, 0, 0, tzinfo=SAST))
    assert next_start > prev_end
    assert next_start - prev_end == timedelta(microseconds=1)


def test_late_thursday_evening_stays_in_its_own_period():
    """The UTC-anchoring trap. 22:30 SAST on Thursday is 20:30 UTC — a
    UTC-anchored cycle would already have rolled over, pushing the last two
    hours of Thursday's recons into the next period and misreporting both
    Outstanding and Recon Rate."""
    thursday_late = datetime(2026, 8, 20, 22, 30, tzinfo=SAST)
    start, end = funds_period(thursday_late)
    assert start.astimezone(SAST).date() == date(2026, 8, 14)
    assert start <= thursday_late.astimezone(timezone.utc) <= end


def test_period_is_independent_of_the_callers_timezone():
    """The same instant expressed in three zones must land in one period."""
    instant = datetime(2026, 8, 20, 22, 30, tzinfo=SAST)
    bounds = {
        funds_period(instant.astimezone(tz))
        for tz in (SAST, timezone.utc, timezone(timedelta(hours=-5)))
    }
    assert len(bounds) == 1


def test_naive_input_is_read_as_utc_not_as_host_local_time():
    naive = datetime(2026, 8, 20, 20, 30)
    assert funds_period(naive) == funds_period(naive.replace(tzinfo=timezone.utc))
