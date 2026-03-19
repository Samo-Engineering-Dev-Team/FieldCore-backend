from fastapi import APIRouter, Query, Response as FastAPIResponse
from fastapi.responses import StreamingResponse, Response
from typing import List
from uuid import UUID

from app.models import ReportCreate, ReportUpdate, ReportResponse
from app.services import ReportService, CurrentUser
from app.database import Session
from app.utils.enums import ReportStatus, ReportType, UserRole
from app.exceptions.http import UnauthorizedException

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.post("/", response_model=ReportResponse, status_code=201)
def create_report(
    payload: ReportCreate,
    service: ReportService,
    session: Session,
    current_user: CurrentUser,
) -> ReportResponse:
    """"""
    return service.create_report(payload, session)


@router.get("/", response_model=List[ReportResponse], status_code=200)
def read_reports(
    service: ReportService,
    session: Session,
    current_user: CurrentUser,
    response: FastAPIResponse,
    report_type: ReportType | None = Query(None),
    status: ReportStatus | None = Query(None),
    technician_id: UUID | None = Query(None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000)
) -> List[ReportResponse]:
    """"""
    response.headers["Cache-Control"] = "private, max-age=60, stale-while-revalidate=30"
    return service.read_reports(session, report_type, status, technician_id, offset, limit)


@router.get("/{report_id}", response_model=ReportResponse, status_code=200)
def read_report(
    report_id: UUID,
    service: ReportService,
    session: Session,
    current_user: CurrentUser,
    response: FastAPIResponse,
) -> ReportResponse:
    """"""
    response.headers["Cache-Control"] = "private, max-age=60, stale-while-revalidate=30"
    return service.read_report(report_id, session)


@router.patch("/{report_id}", response_model=ReportResponse, status_code=200)
def update_report(
    report_id: UUID,
    payload: ReportUpdate,
    service: ReportService,
    session: Session,
    current_user: CurrentUser,
) -> ReportResponse:
    """"""
    return service.update_report(report_id, payload, session)


@router.delete("/{report_id}", status_code=204)
def delete_report(
    report_id: UUID,
    service: ReportService,
    session: Session,
    current_user: CurrentUser,
) -> None:
    """"""
    service.delete_report(report_id, session)


@router.patch("/{report_id}/start", response_model=ReportResponse, status_code=200)
def start_report(
    report_id: UUID,
    service: ReportService,
    session: Session,
    current_user: CurrentUser,
) -> ReportResponse:
    """"""
    return service.start_report(report_id, session)


@router.patch("/{report_id}/complete", response_model=ReportResponse, status_code=200)
def complete_report(
    report_id: UUID,
    service: ReportService,
    session: Session,
    current_user: CurrentUser,
) -> ReportResponse:
    """"""
    return service.complete_report(report_id, session)


@router.get("/{report_id}/export/pdf", status_code=200)
def export_report_pdf(
    report_id: UUID,
    service: ReportService,
    session: Session,
    current_user: CurrentUser
) -> Response:
    """
    Export a completed report as a PDF document.
    Only accessible to NOC, Manager, and Admin roles.
    """
    allowed_roles = [UserRole.NOC, UserRole.MANAGER, UserRole.ADMIN]
    if current_user.role not in allowed_roles:
        raise UnauthorizedException("You do not have permission to export reports.")
    
    pdf_buffer, filename = service.export_report_pdf(report_id, session)
    
    # Get the PDF bytes from the buffer
    pdf_bytes = pdf_buffer.getvalue()
    
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Length": str(len(pdf_bytes))
        }
    )
