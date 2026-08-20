"""Finance Dashboard endpoints (spec §5).

Four reads, matching the four things on the page: the KPI row, the two charts,
and the technician grid. Split rather than one fat payload so the period filter
can refetch a single chart, and so a slow diesel-report scan cannot hold up the
KPI row.

Every response echoes the resolved period, because the default is derived
server-side (the current Friday→Thursday SAST window) and the client should
display what was actually measured rather than what it assumed.
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Query

from app.database import SessionDep
from app.services import CurrentUser
from app.services.finance_dashboard import FinanceDashboardService
from app.utils.enums import Region

router = APIRouter(prefix="/finance-dashboard", tags=["Finance Dashboard"])


@router.get("/kpis", status_code=200)
def read_kpis(
    service: FinanceDashboardService,
    session: SessionDep,
    current_user: CurrentUser,
    period_start: datetime | None = Query(
        None, description="Defaults to the current Friday-to-Thursday SAST window"
    ),
    period_end: datetime | None = Query(None),
    region: Region | None = Query(None),
) -> dict:
    """The five KPI cards. Supply both period bounds or neither — half a window
    reported as a whole one is worse than ignoring it."""
    return service.kpis(session, current_user, period_start, period_end, region)


@router.get("/refueling-by-site", status_code=200)
def read_refueling_by_site(
    service: FinanceDashboardService,
    session: SessionDep,
    current_user: CurrentUser,
    period_start: datetime | None = Query(None),
    period_end: datetime | None = Query(None),
    region: Region | None = Query(None),
) -> dict:
    """Chart 1. Litres come from diesel reports (the only record of what was
    actually filled); Rand comes from the funds ledger, plus legacy report
    amounts for fills before `legacy_cutover_at`, which is returned so the chart
    can state the boundary it used."""
    return service.refueling_by_site(
        session, period_start, period_end, region, current_user
    )


@router.get("/recons-received", status_code=200)
def read_recons_received(
    service: FinanceDashboardService,
    session: SessionDep,
    current_user: CurrentUser,
    period_start: datetime | None = Query(None),
    period_end: datetime | None = Query(None),
    region: Region | None = Query(None),
) -> dict:
    """Chart 2. Per technician and rolled up per region, so Finance can see at a
    glance who has not submitted."""
    return service.recons_received(
        session, current_user, period_start, period_end, region
    )


@router.get("/technicians", status_code=200)
def read_technician_rows(
    service: FinanceDashboardService,
    session: SessionDep,
    current_user: CurrentUser,
    period_start: datetime | None = Query(None),
    period_end: datetime | None = Query(None),
    region: Region | None = Query(None),
) -> List[dict]:
    """The technician grid, highest outstanding balance first. Only technicians
    who received funds in the window appear — someone who took no money has
    nothing to be cleared of."""
    return service.technician_rows(
        session, current_user, period_start, period_end, region
    )
