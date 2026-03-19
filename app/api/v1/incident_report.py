from typing import List
from uuid import UUID

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import StreamingResponse

from app.models.incident_report import (
    IncidentReportCreate,
    IncidentReportResponse,
    IncidentReportUpdate,
)
from app.services import CurrentUser
from app.services.incident_report import IncidentReportService
from app.database import Session

router = APIRouter(prefix="/incident-reports", tags=["Incident Reports"])


@router.post("/", response_model=IncidentReportResponse, status_code=201)
def create_incident_report(
    payload: IncidentReportCreate,
    service: IncidentReportService,
    session: Session,
    current_user: CurrentUser,
) -> IncidentReportResponse:
    """"""
    return service.create_incident_report(payload, session, current_user)


@router.get("/", response_model=List[IncidentReportResponse], status_code=200)
def read_incident_reports(
    service: IncidentReportService,
    session: Session,
    current_user: CurrentUser,
    incident_id: UUID | None = Query(None, description="Filter by incident ID"),
    technician_id: UUID | None = Query(None, description="Filter by technician ID"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000),
) -> List[IncidentReportResponse]:
    """"""
    return service.read_incident_reports(
        session, current_user, incident_id, technician_id, offset, limit
    )


@router.get("/{report_id}", response_model=IncidentReportResponse, status_code=200)
def read_incident_report(
    report_id: UUID,
    service: IncidentReportService,
    session: Session,
    current_user: CurrentUser,
) -> IncidentReportResponse:
    """"""
    return service.read_incident_report(report_id, session, current_user)


@router.get("/{incident_id}/by-incident", response_model=IncidentReportResponse | None, status_code=200)
def get_report_by_incident(
    incident_id: UUID,
    service: IncidentReportService,
    session: Session,
    current_user: CurrentUser,
) -> IncidentReportResponse | None:
    """"""
    return service.get_report_by_incident(incident_id, session, current_user)


@router.patch("/{report_id}", response_model=IncidentReportResponse, status_code=200)
def update_incident_report(
    report_id: UUID,
    payload: IncidentReportUpdate,
    service: IncidentReportService,
    session: Session,
    current_user: CurrentUser,
) -> IncidentReportResponse:
    """"""
    return service.update_incident_report(report_id, payload, session, current_user)


@router.delete("/{report_id}", status_code=204)
def delete_incident_report(
    report_id: UUID,
    service: IncidentReportService,
    session: Session,
    current_user: CurrentUser,
) -> None:
    """"""
    service.delete_incident_report(report_id, session, current_user)


@router.post("/{report_id}/photos", response_model=IncidentReportResponse, status_code=200)
def upload_report_photo(
    report_id: UUID,
    service: IncidentReportService,
    session: Session,
    current_user: CurrentUser,
    file: UploadFile = File(...),
) -> IncidentReportResponse:
    """"""
    file_content = file.file.read()
    return service.upload_report_photo(
        report_id=report_id,
        file_content=file_content,
        filename=file.filename or "photo.jpg",
        content_type=file.content_type or "image/jpeg",
        session=session,
        current_user=current_user,
    )


@router.post("/{report_id}/export-pdf", status_code=200)
def export_incident_report_pdf(
    report_id: UUID,
    service: IncidentReportService,
    session: Session,
    current_user: CurrentUser,
) -> StreamingResponse:
    """"""
    pdf_buffer, filename = service.export_report_pdf(report_id, session, current_user)
    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
