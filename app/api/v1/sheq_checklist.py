from datetime import date

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from typing import List
from uuid import UUID

from app.models import SheqSubmissionCreate, SheqSubmissionUpdate, SheqSubmissionResponse, SheqSignatureCreate
from app.services import SheqSubmissionService, SheqComplianceService, CurrentUser, get_pdf_service
from app.services.sheq_compliance import SheqComplianceResponse
from app.database import SessionDep
from app.utils.enums import SheqChecklistType, SheqStatus

router = APIRouter(prefix="/sheq-checklists", tags=["SHEQ Checklists"])


@router.post("/", response_model=SheqSubmissionResponse, status_code=201)
def create_sheq_submission(
    payload: SheqSubmissionCreate,
    service: SheqSubmissionService,
    session: SessionDep,
    current_user: CurrentUser,
) -> SheqSubmissionResponse:
    """Create a SHEQ checklist submission. Both clients POST one complete
    payload (mobile keeps drafts client-side); passing status="draft"
    explicitly saves a genuinely partial submission without §6 validation."""
    return service.create_submission(payload, session, current_user)


@router.get("/", response_model=List[SheqSubmissionResponse], status_code=200)
def read_sheq_submissions(
    service: SheqSubmissionService,
    session: SessionDep,
    current_user: CurrentUser,
    checklist_type: SheqChecklistType | None = Query(None),
    status: SheqStatus | None = Query(None),
    technician_id: UUID | None = Query(None),
    site_id: UUID | None = Query(None),
    task_id: UUID | None = Query(None),
    performed_from: date | None = Query(None),
    performed_to: date | None = Query(None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, le=1000),
) -> List[SheqSubmissionResponse]:
    """List SHEQ checklist submissions. Technicians see only their own; a
    `sheq` officer and management see every submission, unscoped by region."""
    return service.read_submissions(
        session,
        current_user,
        checklist_type,
        status,
        technician_id,
        site_id,
        task_id,
        performed_from,
        performed_to,
        offset,
        limit,
    )


# NOTE: /compliance must stay above /{submission_id} — FastAPI matches routes
# in declaration order and the UUID path param would otherwise swallow it
# (same caveat as report.py's diesel-history routes).
@router.get("/compliance", response_model=SheqComplianceResponse, status_code=200)
def read_sheq_compliance(
    service: SheqComplianceService,
    session: SessionDep,
    current_user: CurrentUser,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    checklist_type: SheqChecklistType | None = Query(None),
    technician_id: UUID | None = Query(None),
    region: str | None = Query(None),
) -> SheqComplianceResponse:
    """SHEQ officer compliance dashboard (§8.4): submission volume, missed
    daily vehicle checks, overdue sign-offs, No-Go rate, top failing items,
    section N/A frequency and signature gaps."""
    return service.get_compliance_report(
        session, current_user, date_from, date_to, checklist_type, technician_id, region
    )


@router.get("/{submission_id}", response_model=SheqSubmissionResponse, status_code=200)
def read_sheq_submission(
    submission_id: UUID,
    service: SheqSubmissionService,
    session: SessionDep,
    current_user: CurrentUser,
) -> SheqSubmissionResponse:
    """"""
    return service.read_submission(submission_id, session, current_user)


@router.patch("/{submission_id}", response_model=SheqSubmissionResponse, status_code=200)
def update_sheq_submission(
    submission_id: UUID,
    payload: SheqSubmissionUpdate,
    service: SheqSubmissionService,
    session: SessionDep,
    current_user: CurrentUser,
) -> SheqSubmissionResponse:
    """Returns 409 once the submission has been signed off — corrections mean
    a new submission, per the plan's immutability decision (§3)."""
    return service.update_submission(submission_id, payload, session, current_user)


@router.delete("/{submission_id}", status_code=204)
def delete_sheq_submission(
    submission_id: UUID,
    service: SheqSubmissionService,
    session: SessionDep,
    current_user: CurrentUser,
) -> None:
    """"""
    service.delete_submission(submission_id, session, current_user)


@router.post("/{submission_id}/signatures", response_model=SheqSubmissionResponse, status_code=201)
def add_sheq_signature(
    submission_id: UUID,
    payload: SheqSignatureCreate,
    request: Request,
    service: SheqSubmissionService,
    session: SessionDep,
    current_user: CurrentUser,
) -> SheqSubmissionResponse:
    """A `supervisor` signature requires management and moves the submission
    to `signed_off` (§7.4). `technician`/`driver`/`employee` require the
    submitting technician (or management on their behalf)."""
    ip = request.headers.get(
        "X-Forwarded-For", request.client.host if request.client else None
    )
    ua = request.headers.get("User-Agent")
    return service.add_signature(submission_id, payload, session, current_user, ip, ua)


@router.get("/{submission_id}/pdf", status_code=200)
def export_sheq_submission_pdf(
    submission_id: UUID,
    service: SheqSubmissionService,
    session: SessionDep,
    current_user: CurrentUser,
) -> Response:
    """"""
    submission, site_name, task_ref = service.get_submission_for_export(submission_id, session, current_user)

    pdf_buffer = get_pdf_service().generate_sheq_checklist_pdf(submission, site_name, task_ref)
    pdf_bytes = pdf_buffer.getvalue()
    filename = f"sheq_{submission.checklist_type.value.replace('-', '_')}_{submission.performed_on}_{str(submission.id)[:8]}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Length": str(len(pdf_bytes)),
        },
    )
