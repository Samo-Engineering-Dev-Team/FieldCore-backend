from fastapi import APIRouter, UploadFile, File, HTTPException, status, Query
from typing import List
from pydantic import BaseModel

from app.services.file import FileService
from app.services import CurrentUser


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

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


class FileUploadResponse(BaseModel):
    """Response model for file upload."""
    file_path: str
    public_url: str
    signed_url: str | None = None
    url: str | None = None
    original_name: str
    content_type: str
    size: int


class MultiFileUploadResponse(BaseModel):
    """Response model for multiple file uploads."""
    files: List[FileUploadResponse]
    failed: List[str]


@router.post("/upload", response_model=FileUploadResponse, status_code=201)
async def upload_file(
    current_user: CurrentUser,
    file: UploadFile = File(...),
    folder: str = Query(default="incidents", description="Folder to store the file in"),
) -> FileUploadResponse:
    """
    Upload a single file to storage.
    
    Supported file types: JPEG, PNG, GIF, WebP, PDF, DOC, DOCX
    Max file size: 10MB
    """
    # Validate content type
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{file.content_type}' not allowed. Allowed types: {', '.join(ALLOWED_CONTENT_TYPES)}"
        )
    
    # Read and validate file size
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File size exceeds maximum allowed size of {MAX_FILE_SIZE // (1024*1024)}MB"
        )
    
    file_service = FileService()
    result = await file_service.upload_file(
        file_content=content,
        filename=file.filename or "unnamed",
        content_type=file.content_type or "application/octet-stream",
        folder=folder
    )
    
    return FileUploadResponse(**result)


@router.post("/upload-multiple", response_model=MultiFileUploadResponse, status_code=201)
async def upload_multiple_files(
    current_user: CurrentUser,
    files: List[UploadFile] = File(...),
    folder: str = Query(default="incidents", description="Folder to store the files in"),
) -> MultiFileUploadResponse:
    """
    Upload multiple files to storage.
    
    Supported file types: JPEG, PNG, GIF, WebP, PDF, DOC, DOCX
    Max file size per file: 10MB
    Max files per request: 10
    """
    if len(files) > 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 10 files can be uploaded at once"
        )
    
    file_service = FileService()
    uploaded: List[FileUploadResponse] = []
    failed: List[str] = []
    
    for file in files:
        try:
            # Validate content type
            if file.content_type not in ALLOWED_CONTENT_TYPES:
                failed.append(f"{file.filename}: Invalid file type '{file.content_type}'")
                continue
            
            # Read and validate file size
            content = await file.read()
            if len(content) > MAX_FILE_SIZE:
                failed.append(f"{file.filename}: File too large (max {MAX_FILE_SIZE // (1024*1024)}MB)")
                continue
            
            result = await file_service.upload_file(
                file_content=content,
                filename=file.filename or "unnamed",
                content_type=file.content_type or "application/octet-stream",
                folder=folder
            )
            uploaded.append(FileUploadResponse(**result))
            
        except Exception as e:
            failed.append(f"{file.filename}: {str(e)}")
    
    return MultiFileUploadResponse(files=uploaded, failed=failed)


@router.delete("/{file_path:path}", status_code=204)
async def delete_file(file_path: str, current_user: CurrentUser) -> None:
    """Delete a file from storage."""
    file_service = FileService()
    deleted = await file_service.delete_file(file_path)
    
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found or could not be deleted"
        )


@router.get("/signed-url/{file_path:path}")
async def get_signed_url(
    file_path: str,
    current_user: CurrentUser,
    expires_in: int = Query(default=3600, ge=60, le=86400, description="URL expiration in seconds"),
) -> dict:
    """
    Get a signed URL for a file.
    
    Useful for private files that need temporary access.
    """
    file_service = FileService()
    signed_url = await file_service.get_signed_url(file_path, expires_in)
    return {"signed_url": signed_url, "expires_in": expires_in}
