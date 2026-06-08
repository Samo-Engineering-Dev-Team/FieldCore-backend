from fastapi import APIRouter, Query
from typing import List
from uuid import UUID

from app.models import FormSubmissionCreate, FormSubmissionResponse
from app.services import FormSubmissionService, CurrentUser
from app.database import Session

# DEPRECATED: canonical routes now live under /api/v2/form-templates/{id}/submissions.
# Kept as a shim (same service) for backwards compatibility; remove later.
router = APIRouter(
    prefix="/form-templates/{template_id}/submissions",
    tags=["Form Submissions"],
    deprecated=True,
)


@router.post("/", response_model=FormSubmissionResponse, status_code=201)
def create_form_submission(
    template_id: UUID,
    payload: FormSubmissionCreate,
    service: FormSubmissionService,
    session: Session,
    current_user: CurrentUser,
) -> FormSubmissionResponse:
    """
    Create a submission for a template. The submission is validated server-side
    against the template structure; failures return 422 with a per-field error map.

    Attachments: upload files first via POST /api/v1/files/upload, then pass the
    returned reference objects (keyed by field key) in `attachments`. Per-field
    mime/size constraints are enforced here against that upload metadata.
    """
    return service.create_submission(template_id, payload, session, current_user)


@router.get("/", response_model=List[FormSubmissionResponse], status_code=200)
def read_form_submissions(
    template_id: UUID,
    service: FormSubmissionService,
    session: Session,
    current_user: CurrentUser,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, le=1000),
) -> List[FormSubmissionResponse]:
    """List submissions for a template. Technicians see only their own."""
    return service.read_submissions(template_id, session, current_user, offset, limit)


@router.get("/{submission_id}", response_model=FormSubmissionResponse, status_code=200)
def read_form_submission(
    template_id: UUID,
    submission_id: UUID,
    service: FormSubmissionService,
    session: Session,
    current_user: CurrentUser,
) -> FormSubmissionResponse:
    """Retrieve a single submission."""
    return service.read_submission(template_id, submission_id, session, current_user)
