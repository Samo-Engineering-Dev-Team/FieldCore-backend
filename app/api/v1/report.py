from fastapi import APIRouter, Query, Response as FastAPIResponse
from fastapi.responses import Response
from datetime import datetime
from typing import List
from uuid import UUID

from app.models import ReportCreate, ReportUpdate, ReportResponse
from app.models.report_data import DieselSiteHistory
from app.services import ReportService, CurrentUser
from app.database import SessionDep
from app.utils.enums import ReportStatus, ReportType

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.post("/", response_model=ReportResponse, status_code=201)
def create_report(
    payload: ReportCreate,
    service: ReportService,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReportResponse:
    """"""
    return service.create_report(payload, session, current_user)


@router.get("/", response_model=List[ReportResponse], status_code=200)
def read_reports(
    service: ReportService,
    session: SessionDep,
    current_user: CurrentUser,
    response: FastAPIResponse,
    report_type: ReportType | None = Query(None),
    status: ReportStatus | None = Query(None),
    technician_id: UUID | None = Query(None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, le=1000),
) -> List[ReportResponse]:
    """"""
    response.headers["Cache-Control"] = "private, max-age=60, stale-while-revalidate=30"
    return service.read_reports(
        session,
        current_user,
        report_type,
        status,
        technician_id,
        offset,
        limit,
    )


# NOTE: the diesel-history routes must stay above `/{report_id}` — FastAPI matches
# in declaration order and the UUID path would otherwise swallow the literal segment.


@router.get(
    "/diesel-history/{site_id}", response_model=DieselSiteHistory, status_code=200
)
def read_diesel_site_history(
    site_id: UUID,
    service: ReportService,
    session: SessionDep,
    current_user: CurrentUser,
    date_from: datetime | None = Query(
        None, description="Only include fill-ups from this date onward"
    ),
    date_to: datetime | None = Query(
        None, description="Only include fill-ups up to this date"
    ),
) -> DieselSiteHistory:
    """Every diesel fill-up recorded against a site, split by generator."""
    return service.read_diesel_site_history(
        site_id, session, current_user, date_from, date_to
    )


@router.get("/diesel-history/{site_id}/export/pdf", status_code=200)
def export_diesel_site_history_pdf(
    site_id: UUID,
    service: ReportService,
    session: SessionDep,
    current_user: CurrentUser,
    date_from: datetime | None = Query(
        None, description="Only include fill-ups from this date onward"
    ),
    date_to: datetime | None = Query(
        None, description="Only include fill-ups up to this date"
    ),
) -> Response:
    """Export a site's full diesel fill-up history as a PDF document."""
    pdf_buffer, filename = service.export_diesel_site_history_pdf(
        site_id, session, current_user, date_from, date_to
    )

    pdf_bytes = pdf_buffer.getvalue()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


@router.get("/{report_id}", response_model=ReportResponse, status_code=200)
def read_report(
    report_id: UUID,
    service: ReportService,
    session: SessionDep,
    current_user: CurrentUser,
    response: FastAPIResponse,
) -> ReportResponse:
    """"""
    response.headers["Cache-Control"] = "private, max-age=60, stale-while-revalidate=30"
    return service.read_report(report_id, session, current_user)


@router.patch("/{report_id}", response_model=ReportResponse, status_code=200)
def update_report(
    report_id: UUID,
    payload: ReportUpdate,
    service: ReportService,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReportResponse:
    """"""
    return service.update_report(report_id, payload, session, current_user)


@router.delete("/{report_id}", status_code=204)
def delete_report(
    report_id: UUID,
    service: ReportService,
    session: SessionDep,
    current_user: CurrentUser,
) -> None:
    """"""
    service.delete_report(report_id, session, current_user)


@router.patch("/{report_id}/start", response_model=ReportResponse, status_code=200)
def start_report(
    report_id: UUID,
    service: ReportService,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReportResponse:
    """"""
    return service.start_report(report_id, session, current_user)


@router.patch("/{report_id}/complete", response_model=ReportResponse, status_code=200)
def complete_report(
    report_id: UUID,
    service: ReportService,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReportResponse:
    """"""
    return service.complete_report(report_id, session, current_user)


@router.get("/{report_id}/export/pdf", status_code=200)
def export_report_pdf(
    report_id: UUID,
    service: ReportService,
    session: SessionDep,
    current_user: CurrentUser,
) -> Response:
    """
    Export a completed report as a PDF document.
    Only accessible to report owners and report-review roles.
    """
    pdf_buffer, filename = service.export_report_pdf(report_id, session, current_user)

    # Get the PDF bytes from the buffer
    pdf_bytes = pdf_buffer.getvalue()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Length": str(len(pdf_bytes)),
        },
    )
