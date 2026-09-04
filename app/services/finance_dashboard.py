"""Finance Dashboard aggregations (spec §5).

Phase 4 of FINANCE_TECHNICIAN_IMPLEMENTATION_PLAN.md. The weekly view Finance
uses to decide who is cleared for next week's disbursement.

Two things about the data shape are worth stating up front, because they are not
obvious and they drive most of the code below.

1. LITRES AND RAND COME FROM DIFFERENT PLACES.
   Litres actually put into a generator exist only in diesel reports
   (`Report.data["diesel_fillups"]`) — the funds ledger records Rand and
   *requested* litres, never what was finally filled. So Chart 1's litres always
   come from reports, while its Rand comes from the ledger. They are two
   measures from two sources, not two sources of one measure.

2. RAND IS THE ONLY DOUBLE-COUNT RISK, AND IT IS BOUNDED BY A CUTOVER.
   Legacy diesel reports carry their own `amount_used`, so summing both report
   amounts and ledger amounts would count the same refuel twice. The boundary is
   the first released generator-refuel disbursement: before it no ledger existed,
   after it every refuel should have one. That instant is computed rather than
   configured so it cannot drift out of step with the data, but a system setting
   overrides it if the real cutover differed. Either way the value used is
   returned to the client as `legacy_cutover_at`, so the chart can say what it
   did rather than quietly deciding.

All periods are the Friday→Thursday SAST window from `app.utils.funcs`.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends
from loguru import logger as LOG
from sqlmodel import Session, select

from app.models import (
    Disbursement,
    FundsRequest,
    Generator,
    Reconciliation,
    Report,
    Site,
    Task,
    Technician,
    User,
)
from app.models.auth import TokenData
from app.services.authorization import require_finance_read
from app.services.report_support import coerce_diesel_gen_no
from app.services.system_settings import get_system_settings_service
from app.utils.enums import (
    FundsRequestType,
    ReconciliationStatus,
    Region,
    ReportStatus,
    ReportType,
)
from app.utils.funcs import funds_period, utcnow

_ZERO = Decimal("0.00")

# Spec §5.1 proposes these and asks Finance to confirm. Overridable via
# system_settings so that sign-off does not need a deploy — see
# FINANCE_TECHNICIAN_IMPLEMENTATION_PLAN.md assumptions.
RECON_RATE_GOOD_KEY = "funds.recon_rate_good_threshold"
RECON_RATE_EXCELLENT_KEY = "funds.recon_rate_excellent_threshold"
DEFAULT_GOOD_THRESHOLD = 70.0
DEFAULT_EXCELLENT_THRESHOLD = 90.0

# Explicit override for the legacy/ledger boundary (ISO 8601). Normally unset.
LEGACY_CUTOVER_KEY = "funds.legacy_refuel_cutover_at"

UNASSIGNED_REGION = "unassigned"
"""Technicians whose region was never set. The column is nullable because it was
added without backfilling production, so this is an expected state, not bad
data — and Finance still needs those technicians to appear in a regional view."""


def legacy_refuel_cutover(session: Session) -> datetime | None:
    """The instant the funds ledger took over from diesel-report amounts.

    Explicit setting wins. Otherwise the first released generator-refuel
    disbursement: before it there was no ledger, after it every refuel should
    have one. Returns None when neither exists, meaning every diesel report is
    still legacy — correct before go-live, and harmless because the ledger is
    empty then anyway.

    Module-level so the per-generator refuel history applies the same boundary
    as the Finance Dashboard; two definitions would drift and the same fill
    would be counted differently on the two screens.
    """
    configured = get_system_settings_service().get_setting(
        LEGACY_CUTOVER_KEY, session, None
    )
    if configured:
        try:
            return datetime.fromisoformat(str(configured))
        except ValueError:
            LOG.warning(
                "Ignoring unparseable {} setting: {!r}",
                LEGACY_CUTOVER_KEY,
                configured,
            )

    return session.exec(
        select(Disbursement.released_at)
        .join(FundsRequest, FundsRequest.id == Disbursement.funds_request_id)  # type: ignore[arg-type]
        .where(
            FundsRequest.type == FundsRequestType.GENERATOR_REFUEL,
            FundsRequest.deleted_at.is_(None),  # type: ignore
            Disbursement.deleted_at.is_(None),  # type: ignore
            Disbursement.released_at.is_not(None),  # type: ignore
        )
        .order_by(Disbursement.released_at.asc())  # type: ignore[attr-defined]
        .limit(1)
    ).first()


def _money(value: Decimal | float | None) -> float:
    if value is None:
        return 0.0
    return float(value)


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator * 100, 1)


class _FinanceDashboardService:
    # ── Period plumbing ───────────────────────────────────────────────────

    def _resolve_period(
        self,
        period_start: datetime | None,
        period_end: datetime | None,
    ) -> tuple[datetime, datetime]:
        """Explicit bounds win; otherwise the current Friday→Thursday window.

        A caller supplying only one bound is treated as supplying neither: half a
        window silently reported as a full one is worse than ignoring it.
        """
        if period_start is not None and period_end is not None:
            return period_start, period_end
        return funds_period()

    # ── Shared row builders ───────────────────────────────────────────────

    def _disbursements_in_period(
        self,
        session: Session,
        start: datetime,
        end: datetime,
        region: Region | None = None,
        request_type: FundsRequestType | None = None,
    ) -> list[tuple[FundsRequest, Disbursement, Reconciliation | None, Technician, User]]:
        """Every release in the window, with its reconciliation if one exists.

        Keyed on `released_at`, not on the request's period: a request raised late
        in one week and released in the next belongs to the week the money
        actually moved, which is what "Total Issued" means to Finance.
        """
        statement = (
            select(FundsRequest, Disbursement, Reconciliation, Technician, User)
            .join(Disbursement, Disbursement.funds_request_id == FundsRequest.id)  # type: ignore[arg-type]
            .join(Technician, Technician.id == FundsRequest.technician_id)  # type: ignore[arg-type]
            .join(User, User.id == Technician.user_id)  # type: ignore[arg-type]
            .outerjoin(
                Reconciliation,
                (Reconciliation.disbursement_id == Disbursement.id)  # type: ignore[arg-type]
                & Reconciliation.deleted_at.is_(None),  # type: ignore
            )
            .where(
                FundsRequest.deleted_at.is_(None),  # type: ignore
                Disbursement.deleted_at.is_(None),  # type: ignore
                Disbursement.released_at.is_not(None),  # type: ignore
                Disbursement.released_at >= start,  # type: ignore[operator]
                Disbursement.released_at <= end,  # type: ignore[operator]
            )
        )
        if region is not None:
            statement = statement.where(Technician.region == region)
        if request_type is not None:
            statement = statement.where(FundsRequest.type == request_type)
        return list(session.exec(statement).all())  # type: ignore[arg-type]

    # ── KPI cards (spec §5.1) ─────────────────────────────────────────────

    def kpis(
        self,
        session: Session,
        current_user: TokenData,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        region: Region | None = None,
    ) -> dict:
        require_finance_read(
            current_user, "You do not have permission to view finance reporting."
        )
        start, end = self._resolve_period(period_start, period_end)
        rows = self._disbursements_in_period(session, start, end, region)

        total_issued = _ZERO
        total_reconciled = _ZERO
        refuel_amount = _ZERO
        technicians_issued: set[UUID] = set()
        technician_issued: dict[UUID, Decimal] = {}
        technician_reconciled: dict[UUID, Decimal] = {}
        technician_fully_reconciled: dict[UUID, bool] = {}

        for request, disbursement, recon, technician, _user in rows:
            issued = disbursement.amount_issued or _ZERO
            total_issued += issued
            technicians_issued.add(technician.id)
            technician_issued[technician.id] = (
                technician_issued.get(technician.id, _ZERO) + issued
            )
            if request.type is FundsRequestType.GENERATOR_REFUEL:
                refuel_amount += issued

            # Only an APPROVED reconciliation counts as reconciled. Spec §3.1.7
            # makes Finance approval the clearing event, so a submitted-but-
            # unreviewed recon is still money unaccounted for.
            approved = (
                recon is not None
                and recon.status is ReconciliationStatus.APPROVED
            )
            if approved and recon is not None:
                total_reconciled += recon.total_used or _ZERO
                technician_reconciled[technician.id] = (
                    technician_reconciled.get(technician.id, _ZERO)
                    + (recon.total_used or _ZERO)
                )
            # A technician counts as reconciled only when EVERY disbursement in
            # the window is signed off (plan decision 5). One unaccounted refuel
            # is enough to keep them outstanding.
            technician_fully_reconciled[technician.id] = (
                technician_fully_reconciled.get(technician.id, True) and approved
            )

        reconciled_technicians = sum(
            1 for done in technician_fully_reconciled.values() if done
        )
        outstanding_total = total_issued - total_reconciled
        outstanding_technicians = sum(
            1
            for tech_id in technicians_issued
            if (
                technician_issued.get(tech_id, _ZERO)
                - technician_reconciled.get(tech_id, _ZERO)
            )
            != _ZERO
        )

        refuel = self._refuel_totals(session, start, end, region)
        recon_rate = _pct(reconciled_technicians, len(technicians_issued))

        return {
            "period_start": start,
            "period_end": end,
            "total_issued": _money(total_issued),
            "technicians_issued": len(technicians_issued),
            "total_reconciled": _money(total_reconciled),
            "technicians_reconciled": reconciled_technicians,
            "outstanding": _money(outstanding_total),
            "outstanding_technicians": outstanding_technicians,
            # Card 4: Rand is the headline so the five-card row reads in one unit;
            # litres ride along in the subtext for the SECOM invoicing question
            # (resolves spec §7 Q2 both ways).
            "gen_refuel_amount": _money(refuel_amount),
            "gen_refuel_liters": refuel["liters"],
            "sites_refueled": refuel["sites_refueled"],
            "total_sites": refuel["total_sites"],
            "recon_rate": recon_rate,
            "recon_rate_status": self._recon_rate_status_for(
                reconciled_technicians, len(technicians_issued), session
            ),
        }

    def _recon_rate_status_for(
        self, reconciled: int, issued: int, session: Session
    ) -> str | None:
        """Badge for the Recon Rate card, or None when there is nothing to rate.

        A period where nobody received funds has a rate of 0% purely because the
        denominator is zero. Labelling that "Critical" would have Finance chasing
        technicians who were never issued anything, so an empty period reports no
        status at all rather than a failing one.
        """
        if issued <= 0:
            return None
        return self._recon_rate_status(_pct(reconciled, issued), session)

    def _recon_rate_status(self, rate: float, session: Session) -> str:
        settings = get_system_settings_service()
        good = float(
            settings.get_setting(RECON_RATE_GOOD_KEY, session, DEFAULT_GOOD_THRESHOLD)
            or DEFAULT_GOOD_THRESHOLD
        )
        excellent = float(
            settings.get_setting(
                RECON_RATE_EXCELLENT_KEY, session, DEFAULT_EXCELLENT_THRESHOLD
            )
            or DEFAULT_EXCELLENT_THRESHOLD
        )
        # A mis-set pair (good above excellent) would make "Excellent"
        # unreachable and silently mislabel a healthy week as Critical.
        if good > excellent:
            LOG.warning(
                "Recon-rate thresholds are inverted (good={} > excellent={}); "
                "falling back to defaults",
                good,
                excellent,
            )
            good, excellent = DEFAULT_GOOD_THRESHOLD, DEFAULT_EXCELLENT_THRESHOLD
        if rate >= excellent:
            return "Excellent"
        if rate >= good:
            return "Good"
        return "Critical"

    # ── Chart 1: refuelling by site (spec §5.2) ───────────────────────────

    def _legacy_cutover(self, session: Session) -> datetime | None:
        """See `legacy_refuel_cutover` — kept as a method for the callers here."""
        return legacy_refuel_cutover(session)

    def _diesel_fills(
        self, session: Session, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Flatten diesel-report fill-ups in the window into per-unit rows.

        Reports reach a site through their task, mirroring
        ReportService.diesel_site_history — this is the cross-site version of the
        same walk. The JSONB has to cross the wire either way, so the arrays are
        flattened in Python rather than unnested in SQL.
        """
        statement = (
            select(Report, Task.site_id)
            .join(Task, Task.id == Report.task_id)  # type: ignore[arg-type]
            .where(
                Report.report_type == ReportType.DIESEL,
                Report.status == ReportStatus.COMPLETED,
                Report.deleted_at.is_(None),  # type: ignore
                Report.created_at >= start,  # type: ignore[operator]
                Report.created_at <= end,  # type: ignore[operator]
            )
        )
        fills: list[dict[str, Any]] = []
        for report, site_id in session.exec(statement).all():  # type: ignore[misc]
            data = report.data if isinstance(report.data, dict) else {}
            raw_fills = data.get("diesel_fillups")
            if not isinstance(raw_fills, list):
                continue
            for raw in raw_fills:
                if not isinstance(raw, dict):
                    continue
                gen_no, _inferred = coerce_diesel_gen_no(raw.get("gen_no"))
                try:
                    liters = float(raw.get("liters_filled") or 0)
                except (TypeError, ValueError):
                    liters = 0.0
                try:
                    amount = float(raw.get("amount_used") or 0)
                except (TypeError, ValueError):
                    amount = 0.0
                fills.append(
                    {
                        "site_id": site_id,
                        "gen_no": gen_no,
                        "liters": liters,
                        "amount": amount,
                        "created_at": report.created_at,
                    }
                )
        return fills

    def _refuel_totals(
        self,
        session: Session,
        start: datetime,
        end: datetime,
        region: Region | None,
    ) -> dict:
        """Litres and site coverage for the Gen Refuel KPI card."""
        by_site = self.refueling_by_site(
            session, start, end, region, _skip_authz=True
        )
        total_liters = sum(site["liters"] for site in by_site["sites"])
        return {
            "liters": round(total_liters, 2),
            "sites_refueled": len(
                [s for s in by_site["sites"] if s["liters"] > 0 or s["amount"] > 0]
            ),
            "total_sites": by_site["total_sites"],
        }

    def refueling_by_site(
        self,
        session: Session,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        region: Region | None = None,
        current_user: TokenData | None = None,
        _skip_authz: bool = False,
    ) -> dict:
        """Chart 1 — per site, broken down by generator unit.

        Litres come from diesel reports; Rand from the funds ledger, plus legacy
        report amounts for fills before the cutover. See the module docstring.
        """
        if not _skip_authz:
            if current_user is None:
                raise PermissionError("current_user is required")
            require_finance_read(
                current_user, "You do not have permission to view finance reporting."
            )
        start, end = self._resolve_period(period_start, period_end)
        cutover = self._legacy_cutover(session)

        sites = {
            site.id: site
            for site in session.exec(
                select(Site).where(Site.deleted_at.is_(None))  # type: ignore
            ).all()
        }
        # Keyed on legacy_gen_no, not the unit's name: legacy diesel report JSON
        # identifies a unit only by that free-text number and is never rewritten,
        # so it stays the only way to resolve a historical fill. Units registered
        # after the asset register landed carry no legacy number and simply do
        # not resolve here — correctly, since they have no legacy fills.
        generators = {
            (gen.site_id, gen.legacy_gen_no): gen
            for gen in session.exec(
                select(Generator).where(
                    Generator.deleted_at.is_(None),  # type: ignore
                    Generator.site_id.is_not(None),  # type: ignore
                    Generator.legacy_gen_no.is_not(None),  # type: ignore
                )
            ).all()
        }

        # site_id -> gen_no -> {liters, amount}
        buckets: dict[UUID, dict[int, dict[str, float]]] = {}

        def bucket(site_id: UUID, gen_no: int) -> dict[str, float]:
            return buckets.setdefault(site_id, {}).setdefault(
                gen_no, {"liters": 0.0, "amount": 0.0}
            )

        for fill in self._diesel_fills(session, start, end):
            site_id = fill["site_id"]
            if site_id not in sites:
                continue
            site = sites[site_id]
            if region is not None and site.region != region:
                continue
            entry = bucket(site_id, fill["gen_no"])
            # Litres always come from here — the ledger never records what was
            # actually filled.
            entry["liters"] += fill["liters"]
            # Rand only for pre-cutover fills, or when no ledger exists yet.
            if cutover is None or fill["created_at"] < cutover:
                entry["amount"] += fill["amount"]

        for request, disbursement, _recon, technician, _user in (
            self._disbursements_in_period(
                session, start, end, region, FundsRequestType.GENERATOR_REFUEL
            )
        ):
            if request.site_id is None or request.site_id not in sites:
                continue
            gen_no = 1
            if request.generator_id is not None:
                generator = session.get(Generator, request.generator_id)
                if generator is not None and generator.legacy_gen_no is not None:
                    gen_no = generator.legacy_gen_no
            bucket(request.site_id, gen_no)["amount"] += float(
                disbursement.amount_issued or _ZERO
            )

        site_rows = []
        for site_id, units in buckets.items():
            site = sites[site_id]
            site_rows.append(
                {
                    "site_id": site_id,
                    "site_name": site.name,
                    "region": site.region.value if site.region else None,
                    "liters": round(sum(u["liters"] for u in units.values()), 2),
                    "amount": round(sum(u["amount"] for u in units.values()), 2),
                    "generators": sorted(
                        (
                            {
                                "gen_no": gen_no,
                                "display_name": (
                                    generators[(site_id, gen_no)].name
                                    if (site_id, gen_no) in generators
                                    else f"Gen {gen_no}"
                                ),
                                # True when no generators row matches: the fill
                                # came from legacy JSON for a unit that was never
                                # registered, so the chart labels it generically.
                                "registered": (site_id, gen_no) in generators,
                                "liters": round(values["liters"], 2),
                                "amount": round(values["amount"], 2),
                            }
                            for gen_no, values in units.items()
                        ),
                        key=lambda unit: unit["gen_no"],
                    ),
                }
            )
        site_rows.sort(key=lambda row: row["liters"], reverse=True)

        eligible_sites = [
            site
            for site in sites.values()
            if region is None or site.region == region
        ]

        return {
            "period_start": start,
            "period_end": end,
            # Returned so the chart can state which fills were treated as legacy
            # rather than the boundary being invisible.
            "legacy_cutover_at": cutover,
            "sites": site_rows,
            "total_sites": len(eligible_sites),
        }

    # ── Chart 2: recons received (spec §5.2) ──────────────────────────────

    def recons_received(
        self,
        session: Session,
        current_user: TokenData,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        region: Region | None = None,
    ) -> dict:
        require_finance_read(
            current_user, "You do not have permission to view finance reporting."
        )
        start, end = self._resolve_period(period_start, period_end)
        rows = self.technician_rows(
            session, current_user, start, end, region, _skip_authz=True
        )

        by_region: dict[str, dict[str, int]] = {}
        for row in rows:
            key = row["region"] or UNASSIGNED_REGION
            entry = by_region.setdefault(
                key, {"received": 0, "outstanding": 0, "overdue": 0}
            )
            if row["recon_received"] == "received":
                entry["received"] += 1
            elif row["recon_received"] == "overdue":
                entry["overdue"] += 1
            else:
                entry["outstanding"] += 1

        return {
            "period_start": start,
            "period_end": end,
            "technicians": [
                {
                    "technician_id": row["technician_id"],
                    "technician_name": row["technician_name"],
                    "region": row["region"],
                    "recon_received": row["recon_received"],
                    "disbursements": row["disbursement_count"],
                    "approved": row["approved_count"],
                }
                for row in rows
            ],
            "regions": [
                {"region": region_key, **counts}
                for region_key, counts in sorted(by_region.items())
            ],
        }

    # ── Technician grid (spec §5.3) ───────────────────────────────────────

    def technician_rows(
        self,
        session: Session,
        current_user: TokenData,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        region: Region | None = None,
        _skip_authz: bool = False,
    ) -> list[dict]:
        """One row per technician who received funds in the window.

        Technicians with no disbursement are omitted rather than shown as zeroed:
        the grid answers "who is cleared for next week", and someone who took no
        money this week has nothing to be cleared of.
        """
        if not _skip_authz:
            require_finance_read(
                current_user, "You do not have permission to view finance reporting."
            )
        start, end = self._resolve_period(period_start, period_end)
        rows = self._disbursements_in_period(session, start, end, region)

        agg: dict[UUID, dict] = {}
        for request, disbursement, recon, technician, user in rows:
            entry = agg.setdefault(
                technician.id,
                {
                    "technician_id": technician.id,
                    "technician_name": f"{user.name} {user.surname}",
                    "region": technician.region.value if technician.region else None,
                    "amount_issued": _ZERO,
                    "reconciled_amount": _ZERO,
                    "disbursement_count": 0,
                    "approved_count": 0,
                    "overdue_count": 0,
                },
            )
            entry["amount_issued"] += disbursement.amount_issued or _ZERO
            entry["disbursement_count"] += 1

            if recon is not None and recon.status is ReconciliationStatus.APPROVED:
                entry["reconciled_amount"] += recon.total_used or _ZERO
                entry["approved_count"] += 1
            elif recon is not None:
                if recon.is_overdue:
                    entry["overdue_count"] += 1
            else:
                # No reconciliation opened at all. Overdue once the period's
                # Thursday has passed.
                if request.period_end is not None and utcnow() > request.period_end:
                    entry["overdue_count"] += 1

        result = []
        for entry in agg.values():
            fully = entry["approved_count"] == entry["disbursement_count"]
            if fully:
                status = "received"
            elif entry["overdue_count"] > 0:
                status = "overdue"
            else:
                status = "outstanding"
            result.append(
                {
                    **entry,
                    "amount_issued": _money(entry["amount_issued"]),
                    "reconciled_amount": _money(entry["reconciled_amount"]),
                    "outstanding_balance": _money(
                        entry["amount_issued"] - entry["reconciled_amount"]
                    ),
                    "recon_received": status,
                }
            )
        result.sort(key=lambda row: row["outstanding_balance"], reverse=True)
        return result


def get_finance_dashboard_service() -> _FinanceDashboardService:
    return _FinanceDashboardService()


FinanceDashboardService = Annotated[
    _FinanceDashboardService, Depends(get_finance_dashboard_service)
]
