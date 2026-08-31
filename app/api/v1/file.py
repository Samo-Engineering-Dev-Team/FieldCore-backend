from typing import List

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.core.settings import app_settings
from app.services import CurrentUser
from app.services.authorization import is_management, require_management
from app.services.file import FileService, verify_local_upload_token, write_local_file
from app.utils.enums import UserRole

router = APIRouter(prefix="/files", tags=["Files"])

# Allowed file types
ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# Folders a caller is allowed to write into (prevents path injection via ?folder=).
# "sheq" holds both signature PNGs and checklist evidence photos
# (SHEQ-CHECKLISTS-PLAN.md §7.3) — a flat folder, like every other entry here;
# _validate_folder below is an exact match, not a path-prefix check.
# "recon-slips" holds expense slips attached to reconciliation lines
# (docs/FieldCore_Finance_Technician_Workflow_Spec.md §3.1.5). Flat, like every
# other entry — _validate_folder is an exact match, not a path-prefix check.
ALLOWED_FOLDERS = {
    "incidents",
    "reports",
    "tasks",
    "routine",
    "avatars",
    "misc",
    "sheq",
    "recon-slips",
}

# Max files a client may request signed URLs for in one call.
MAX_FILES_PER_REQUEST = 10


def _validate_folder(folder: str) -> str:
    if folder not in ALLOWED_FOLDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid folder '{folder}'. Allowed: {', '.join(sorted(ALLOWED_FOLDERS))}",
        )
    return folder


class SignedUploadItem(BaseModel):
    """A single file the client intends to upload (metadata only, no bytes)."""

    filename: str
    content_type: str


class SignedUploadRequest(BaseModel):
    """Request body for minting signed upload URLs."""

    files: List[SignedUploadItem]


class SignedUpload(BaseModel):
    """A minted signed upload URL for one object."""

    original_name: str
    path: str
    token: str
    public_url: str
    bucket: str
    content_type: str


class SignedUploadResponse(BaseModel):
    """Response containing signed upload URLs for each requested file."""

    uploads: List[SignedUpload]


@router.post(
    "/signed-upload-urls", response_model=SignedUploadResponse, status_code=201
)
async def create_signed_upload_urls(
    current_user: CurrentUser,
    payload: SignedUploadRequest,
    folder: str = Query(
        default="incidents", description="Folder to store the files in"
    ),
) -> SignedUploadResponse:
    """
    Mint short-lived signed upload URLs so the client can PUT bytes directly
    to Supabase Storage, bypassing the serverless request-body size cap.

    The object path for each file is chosen server-side; the client only
    supplies filename + content type. File size + MIME enforcement is handled
    by the Supabase bucket policy.

    Supported file types: JPEG, PNG, GIF, WebP, PDF, DOC, DOCX
    Max files per request: 10
    """
    _validate_folder(folder)

    if len(payload.files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {MAX_FILES_PER_REQUEST} files can be requested at once",
        )
    if not payload.files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one file is required",
        )

    for item in payload.files:
        if item.content_type not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{item.filename}: file type '{item.content_type}' not allowed. "
                    f"Allowed types: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
                ),
            )

    file_service = FileService()
    uploads: List[SignedUpload] = []
    for item in payload.files:
        minted = await file_service.create_signed_upload_url(
            filename=item.filename or "unnamed",
            folder=folder,
        )
        uploads.append(
            SignedUpload(
                original_name=item.filename or "unnamed",
                path=minted["path"],
                token=minted["token"],
                public_url=minted["public_url"],
                bucket=minted["bucket"],
                content_type=item.content_type,
            )
        )

    return SignedUploadResponse(uploads=uploads)


@router.put("/local-upload/{file_path:path}", status_code=204)
async def local_upload(
    file_path: str,
    request: Request,
    current_user: CurrentUser,
    token: str = Query(...),
) -> None:
    """
    Development-only counterpart to Supabase's direct-PUT signed upload: the
    client PUTs raw bytes here instead of to Supabase, and they land on this
    machine's disk (see FileService / LOCAL_UPLOAD_ROOT). Never minted outside
    ENVIRONMENT=development — see create_signed_upload_urls above — so this
    404s if someone calls it directly against a staging/production backend.
    """
    if not app_settings.is_development:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    _validate_folder(file_path.split("/", 1)[0] if "/" in file_path else "")
    if not verify_local_upload_token(file_path, token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired upload token.",
        )

    body = await request.body()
    write_local_file(file_path, body)


@router.delete("/{file_path:path}", status_code=204)
async def delete_file(file_path: str, current_user: CurrentUser) -> None:
    """Delete a file from storage."""
    require_management(current_user, "Only NOC, managers, or admins can delete files.")
    file_service = FileService()
    deleted = await file_service.delete_file(file_path)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found or could not be deleted",
        )


@router.get("/signed-url/{file_path:path}")
async def get_signed_url(
    file_path: str,
    current_user: CurrentUser,
    expires_in: int = Query(
        default=3600, ge=60, le=86400, description="URL expiration in seconds"
    ),
) -> dict:
    """
    Get a signed URL for a file.

    Useful for private files that need temporary access.
    """
    # Finance reviews reconciliations, which means opening the expense slips
    # attached to them (ReconciliationReviewDialog) — recon-slips is opened to
    # Finance on top of the usual management roles. Every other folder stays
    # management-only.
    folder = file_path.split("/", 1)[0] if "/" in file_path else ""
    allowed = is_management(current_user) or (
        folder == "recon-slips" and current_user.role == UserRole.FINANCE
    )
    if not allowed:
        require_management(
            current_user, "Only NOC, managers, or admins can generate signed URLs."
        )
    file_service = FileService()
    signed_url = await file_service.get_signed_url(file_path, expires_in)
    return {"signed_url": signed_url, "expires_in": expires_in}
