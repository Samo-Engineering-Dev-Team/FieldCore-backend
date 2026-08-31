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


def test_reference_amount_uses_declared_amount_when_standalone():
    """A recon with no disbursement (opened for funds a technician already
    holds) is measured against its own declared_amount, not a disbursement."""
    start, end = funds_period(datetime(2026, 8, 17, 8, 0, tzinfo=SAST))
    recon = Reconciliation(
        disbursement_id=None,
        technician_id=uuid4(),
        reference_no="FR-01",
        declared_amount=Decimal("250.00"),
        period_start=start,
        period_end=end,
    )
    assert recon.reference_amount == Decimal("250.00")


def test_reference_amount_is_zero_for_a_standalone_recon_with_no_declaration():
    start, end = funds_period(datetime(2026, 8, 17, 8, 0, tzinfo=SAST))
    recon = Reconciliation(
        disbursement_id=None,
        technician_id=uuid4(),
        reference_no="FR-01",
        period_start=start,
        period_end=end,
    )
    assert recon.reference_amount == Decimal("0.00")


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


# ── Phase 2: stage authority mapping (spec §2, §6) ─────────────────────────
#
# These assert the shape of the chain without a session. The capability check
# itself needs a DB and is therefore covered by the mapping plus the route-level
# integration once the db_session fixture is usable.


def test_every_live_stage_has_exactly_one_owning_capability():
    from app.services.funds_request import STAGE_CAPABILITY
    from app.utils.enums import FundsCapability

    assert STAGE_CAPABILITY == {
        FundsRequestStatus.PENDING: FundsCapability.APPROVE,
        FundsRequestStatus.APPROVED: FundsCapability.LOAD,
        FundsRequestStatus.LOADED: FundsCapability.RELEASE,
    }


def test_terminal_stages_have_no_owning_capability():
    """Nothing can be acted on once a request is released, rejected or cancelled —
    so no capability maps to those states and the service refuses outright."""
    from app.services.funds_request import STAGE_CAPABILITY

    for terminal in (
        FundsRequestStatus.RELEASED,
        FundsRequestStatus.REJECTED,
        FundsRequestStatus.CANCELLED,
    ):
        assert terminal not in STAGE_CAPABILITY


def test_stage_capabilities_are_three_distinct_holders():
    """Spec §6: approval alone never releases funds. If any two stages shared a
    capability, one person could walk a request through both."""
    from app.services.funds_request import STAGE_CAPABILITY

    assert len(set(STAGE_CAPABILITY.values())) == 3


def test_finance_lead_holds_no_chain_stage():
    """The finance lead signs off reconciliations, not disbursements. Keeping them
    out of the chain is what stops recon approval doubling as a release."""
    from app.services.funds_request import STAGE_CAPABILITY
    from app.utils.enums import FundsCapability

    assert FundsCapability.FINANCE_LEAD not in STAGE_CAPABILITY.values()


def test_every_live_status_is_covered_by_the_capability_map():
    """A new live status must not silently become actionable by nobody."""
    from app.services.funds_request import STAGE_CAPABILITY

    live = {
        s
        for s in FundsRequestStatus
        if FundsRequest.ALLOWED_TRANSITIONS[s]  # has somewhere left to go
    }
    assert live == set(STAGE_CAPABILITY)


# ── Phase 2: per-type validation (spec §3.1 rule, §3.2.6, §3.3) ─────────────


def test_weekly_trip_without_a_manually_entered_diesel_price_is_refused():
    """The core control of §3.1: the price is never filled in for the technician,
    so its absence is an error rather than something to default."""
    from app.exceptions.http import BadRequestException
    from app.services.funds_request import get_funds_request_service

    service = get_funds_request_service()
    request = make_request(price=None)
    with pytest.raises(BadRequestException) as exc:
        service._validate_for_type(request, session=None)
    assert "diesel price" in str(exc.value.detail).lower()


def test_weekly_trip_with_a_price_passes_validation():
    from app.services.funds_request import get_funds_request_service

    get_funds_request_service()._validate_for_type(
        make_request(price="23.50"), session=None
    )


def test_misc_request_requires_a_description():
    from app.exceptions.http import BadRequestException
    from app.services.funds_request import get_funds_request_service

    service = get_funds_request_service()
    request = make_request(type_=FundsRequestType.MISC)
    with pytest.raises(BadRequestException):
        service._validate_for_type(request, session=None)

    request.description = "   "  # whitespace is not a description
    with pytest.raises(BadRequestException):
        service._validate_for_type(request, session=None)

    request.description = "USB-C charger for the site laptop"
    service._validate_for_type(request, session=None)


def test_refuel_names_every_missing_field_at_once():
    """A technician on a bad connection should not have to submit three times to
    discover three missing fields."""
    from app.exceptions.http import BadRequestException
    from app.services.funds_request import get_funds_request_service

    service = get_funds_request_service()
    request = make_request(type_=FundsRequestType.GENERATOR_REFUEL)
    with pytest.raises(BadRequestException) as exc:
        service._validate_for_type(request, session=None)
    detail = str(exc.value.detail).lower()
    assert "site" in detail and "generator" in detail and "litres" in detail


# ── Phase 2: money formatting and boundary conversion ──────────────────────


def test_rand_formatting_groups_thousands():
    from app.services.funds_request import _format_rand

    assert _format_rand(Decimal("1234.56")) == "R1 234.56"
    assert _format_rand(Decimal("980.00")) == "R980.00"
    assert _format_rand(Decimal("1000000.00")) == "R1 000 000.00"


def test_money_boundary_conversion_emits_numbers_not_none():
    """Responses expose float so the API emits numbers rather than the quoted
    strings Pydantic produces for Decimal in JSON mode."""
    from app.services.funds_request import _money

    assert _money(Decimal("1234.56")) == 1234.56
    assert isinstance(_money(Decimal("1234.56")), float)
    assert _money(None) == 0.0


def test_hard_block_setting_key_is_stable():
    """The key is written into system_settings by Finance; renaming it silently
    would turn enforcement off."""
    from app.services.funds_request import HARD_BLOCK_SETTING_KEY

    assert HARD_BLOCK_SETTING_KEY == "funds.hard_block_unreconciled"


# ── Phase 3: reconciliation editability and sign-off gating ────────────────


def test_only_draft_and_rejected_reconciliations_are_editable():
    """A SUBMITTED recon must not change under the reviewer, and an APPROVED one
    is a closed record. REJECTED is editable because Finance sent it back."""
    from app.services.reconciliation import EDITABLE_STATUSES

    assert set(EDITABLE_STATUSES) == {
        ReconciliationStatus.DRAFT,
        ReconciliationStatus.REJECTED,
    }


def test_editable_statuses_exclude_submitted_and_approved():
    from app.services.reconciliation import EDITABLE_STATUSES

    assert ReconciliationStatus.SUBMITTED not in EDITABLE_STATUSES
    assert ReconciliationStatus.APPROVED not in EDITABLE_STATUSES


def test_a_returned_reconciliation_becomes_editable_again():
    """The technician has to be able to fix what Finance objected to."""
    from app.services.reconciliation import EDITABLE_STATUSES

    recon = make_recon(["100.00"])
    recon.mark_submitted()
    assert recon.status not in EDITABLE_STATUSES

    recon.mark_rejected(uuid4(), "Toll slip missing")
    assert recon.status in EDITABLE_STATUSES


def test_recon_slips_folder_is_allowed_for_upload():
    """Slips reuse the existing Supabase signed-upload flow; without the folder on
    the allowlist every slip upload 400s."""
    from app.api.v1.file import ALLOWED_FOLDERS

    assert "recon-slips" in ALLOWED_FOLDERS


def test_recon_slip_folder_did_not_displace_existing_folders():
    from app.api.v1.file import ALLOWED_FOLDERS

    for folder in ("incidents", "reports", "tasks", "routine", "avatars", "misc", "sheq"):
        assert folder in ALLOWED_FOLDERS


def test_finance_lead_is_what_signs_off_a_reconciliation():
    """Recon approval is gated on FINANCE_LEAD, which holds no chain stage — so
    signing off a recon can never double as releasing funds."""
    from app.services.funds_request import STAGE_CAPABILITY
    from app.utils.enums import FundsCapability

    assert FundsCapability.FINANCE_LEAD not in STAGE_CAPABILITY.values()


def test_reconciliation_totals_are_recomputed_not_incremented():
    """Recompute must be idempotent: calling it twice on unchanged lines gives the
    same answer. An incrementing total would drift from the lines that justify it.
    """
    recon = make_recon(["120.00", "80.00"])
    recon.recompute(Decimal("300.00"))
    first = (recon.total_used, recon.outstanding_balance)
    recon.recompute(Decimal("300.00"))
    assert (recon.total_used, recon.outstanding_balance) == first
    assert recon.total_used == Decimal("200.00")


def test_removing_a_line_moves_the_balance_back():
    recon = make_recon(["120.00", "80.00"])
    recon.recompute(Decimal("300.00"))
    assert recon.outstanding_balance == Decimal("100.00")

    # Soft delete, as remove_line does — the slip may already be in storage.
    next(line for line in recon.lines if line.amount == Decimal("80.00")).soft_delete()
    recon.recompute(Decimal("300.00"))
    assert recon.total_used == Decimal("120.00")
    assert recon.outstanding_balance == Decimal("180.00")


def test_reconciliation_notification_templates_state_the_numbers():
    """Finance decides from the notification whether to open the recon at all."""
    from app.services.notification import NotificationTemplates as T

    submitted = T.reconciliation_submitted("Thabo M", "R820.00", "R180.00")
    assert "R820.00" in submitted.message and "R180.00" in submitted.message

    approved = T.reconciliation_approved("R820.00", "R180.00")
    assert "cleared" in approved.message.lower()

    rejected = T.reconciliation_rejected("Toll slip missing")
    assert "Toll slip missing" in rejected.message
    assert rejected.priority.value == "high"


# ── Phase 4: Finance Dashboard aggregation rules ───────────────────────────


class _FakeSettings:
    """Stands in for SystemSettingsService without a DB."""

    def __init__(self, values: dict | None = None):
        self._values = values or {}

    def get_setting(self, key, session, default=None):
        return self._values.get(key, default)


def _status_with(rate: float, values: dict | None = None) -> str:
    import app.services.finance_dashboard as fd

    service = fd.get_finance_dashboard_service()
    original = fd.get_system_settings_service
    fd.get_system_settings_service = lambda: _FakeSettings(values)  # type: ignore[assignment]
    try:
        return service._recon_rate_status(rate, session=None)
    finally:
        fd.get_system_settings_service = original  # type: ignore[assignment]


@pytest.mark.parametrize(
    "rate,expected",
    [
        (0.0, "Critical"),
        (69.9, "Critical"),
        (70.0, "Good"),
        (89.9, "Good"),
        (90.0, "Excellent"),
        (100.0, "Excellent"),
    ],
)
def test_recon_rate_thresholds_match_the_spec_proposal(rate, expected):
    """Spec §5.1: <70 Critical, 70–89 Good, >=90 Excellent. Boundaries included
    in the higher band, so exactly 70% is Good rather than Critical."""
    assert _status_with(rate) == expected


def test_recon_rate_thresholds_are_configurable():
    """Finance signs the numbers off after go-live (spec §7 Q3), so they must be
    changeable without a deploy."""
    from app.services.finance_dashboard import (
        RECON_RATE_EXCELLENT_KEY,
        RECON_RATE_GOOD_KEY,
    )

    strict = {RECON_RATE_GOOD_KEY: 85.0, RECON_RATE_EXCELLENT_KEY: 95.0}
    assert _status_with(80.0, strict) == "Critical"
    assert _status_with(85.0, strict) == "Good"
    assert _status_with(95.0, strict) == "Excellent"


def test_inverted_thresholds_fall_back_to_defaults():
    """A mis-set pair would make Excellent unreachable and label a healthy week
    Critical. Falling back is safer than honouring nonsense."""
    from app.services.finance_dashboard import (
        RECON_RATE_EXCELLENT_KEY,
        RECON_RATE_GOOD_KEY,
    )

    inverted = {RECON_RATE_GOOD_KEY: 95.0, RECON_RATE_EXCELLENT_KEY: 60.0}
    assert _status_with(95.0, inverted) == "Excellent"
    assert _status_with(75.0, inverted) == "Good"
    assert _status_with(50.0, inverted) == "Critical"


def test_percentage_helper_guards_division_by_zero():
    """A period where nobody received funds must read 0%, not crash — this is the
    normal state of a quiet week."""
    from app.services.finance_dashboard import _pct

    assert _pct(0, 0) == 0.0
    assert _pct(3, 4) == 75.0
    assert _pct(1, 3) == 33.3


def test_dashboard_period_defaults_to_the_current_friday_thursday_window():
    from app.services.finance_dashboard import get_finance_dashboard_service

    service = get_finance_dashboard_service()
    assert service._resolve_period(None, None) == funds_period()


def test_a_half_specified_period_is_ignored_rather_than_half_applied():
    """Reporting half a window as if it were whole would misstate every KPI."""
    from app.services.finance_dashboard import get_finance_dashboard_service

    service = get_finance_dashboard_service()
    only_start = datetime(2026, 8, 14, tzinfo=SAST)
    assert service._resolve_period(only_start, None) == funds_period()
    assert service._resolve_period(None, only_start) == funds_period()


def test_an_explicit_period_is_used_verbatim():
    from app.services.finance_dashboard import get_finance_dashboard_service

    service = get_finance_dashboard_service()
    start = datetime(2026, 7, 3, tzinfo=SAST)
    end = datetime(2026, 7, 9, 23, 59, 59, tzinfo=SAST)
    assert service._resolve_period(start, end) == (start, end)


def test_money_conversion_at_the_dashboard_boundary():
    from app.services.finance_dashboard import _money

    assert _money(Decimal("1234.56")) == 1234.56
    assert _money(None) == 0.0
    # Legacy diesel amounts arrive as floats; both must pass through unharmed.
    assert _money(99.5) == 99.5


def _status_for(reconciled: int, issued: int) -> str | None:
    import app.services.finance_dashboard as fd

    service = fd.get_finance_dashboard_service()
    original = fd.get_system_settings_service
    fd.get_system_settings_service = lambda: _FakeSettings()  # type: ignore[assignment]
    try:
        return service._recon_rate_status_for(reconciled, issued, session=None)
    finally:
        fd.get_system_settings_service = original  # type: ignore[assignment]


def test_no_recon_rate_status_when_nobody_received_funds():
    """0 out of 0 is 0% only because the denominator is zero. Labelling a quiet
    week "Critical" would have Finance chasing technicians who were never issued
    anything. Found against a real data copy whose current period was empty."""
    assert _status_for(0, 0) is None


def test_a_genuine_zero_percent_is_still_critical():
    """The guard must not swallow a real failure: funds issued to four
    technicians and none reconciled is exactly what Critical is for."""
    assert _status_for(0, 4) == "Critical"


def test_status_returns_for_a_normal_period():
    assert _status_for(4, 4) == "Excellent"
    assert _status_for(3, 4) == "Good"


# ── Stage authorisation ordering (found by walking a real request) ──────────


def test_invalid_stage_move_names_the_stage_not_a_missing_record():
    """Calling /load on a pending request used to fetch the disbursement first
    and 500 with "no disbursement record". A caller acting at the wrong stage is
    a 409 about the stage, not a server error about a record that was never
    meant to exist yet."""
    from app.exceptions.http import ConflictException
    from app.services.funds_request import get_funds_request_service
    from app.utils.enums import FundsCapability

    service = get_funds_request_service()
    request = make_request(status=FundsRequestStatus.PENDING)

    import app.services.funds_request as fr

    original = fr.require_funds_capability
    fr.require_funds_capability = lambda user, cap, session: object()  # type: ignore[assignment]
    try:
        with pytest.raises(ConflictException) as exc:
            service._authorise_stage(
                request,
                FundsRequestStatus.LOADED,
                FundsCapability.LOAD,
                session=None,
                current_user=None,
            )
    finally:
        fr.require_funds_capability = original  # type: ignore[assignment]

    detail = str(exc.value.detail).lower()
    assert "pending" in detail and "loaded" in detail
    assert "disbursement" not in detail


def test_capability_is_checked_before_the_transition():
    """An unauthorised caller gets 403, not a description of the request's
    state — authorise first, then validate."""
    from app.exceptions.http import ForbiddenException
    from app.services.funds_request import get_funds_request_service
    from app.utils.enums import FundsCapability

    service = get_funds_request_service()
    request = make_request(status=FundsRequestStatus.PENDING)

    import app.services.funds_request as fr

    def deny(user, cap, session):
        raise ForbiddenException("no capability")

    original = fr.require_funds_capability
    fr.require_funds_capability = deny  # type: ignore[assignment]
    try:
        # The move is ALSO invalid, but the 403 must win.
        with pytest.raises(ForbiddenException):
            service._authorise_stage(
                request,
                FundsRequestStatus.LOADED,
                FundsCapability.LOAD,
                session=None,
                current_user=None,
            )
    finally:
        fr.require_funds_capability = original  # type: ignore[assignment]


def test_a_valid_move_passes_authorisation():
    from app.services.funds_request import get_funds_request_service
    from app.utils.enums import FundsCapability

    service = get_funds_request_service()
    request = make_request(status=FundsRequestStatus.APPROVED)

    import app.services.funds_request as fr

    sentinel = object()
    original = fr.require_funds_capability
    fr.require_funds_capability = lambda user, cap, session: sentinel  # type: ignore[assignment]
    try:
        assert (
            service._authorise_stage(
                request,
                FundsRequestStatus.LOADED,
                FundsCapability.LOAD,
                session=None,
                current_user=None,
            )
            is sentinel
        )
    finally:
        fr.require_funds_capability = original  # type: ignore[assignment]
