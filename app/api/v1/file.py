from typing import List

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.services import CurrentUser
from app.services.authorization import require_management
from app.services.file import FileService

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
ALLOWED_FOLDERS = {"incidents", "reports", "tasks", "routine", "avatars", "misc"}

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
    require_management(
        current_user, "Only NOC, managers, or admins can generate signed URLs."
    )
    file_service = FileService()
    signed_url = await file_service.get_signed_url(file_path, expires_in)
    return {"signed_url": signed_url, "expires_in": expires_in}
