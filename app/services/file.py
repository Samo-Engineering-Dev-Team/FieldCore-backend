import uuid
import httpx
from typing import BinaryIO
from fastapi import HTTPException, status

from app.core.settings import app_settings


class FileService:
    """Service for managing file uploads/downloads via Supabase Storage."""
    
    def __init__(self):
        self.supabase_url = app_settings.SUPABASE_URL
        self.service_key = app_settings.SUPABASE_SERVICE_KEY
        self.bucket = app_settings.SUPABASE_STORAGE_BUCKET
        
    @property
    def _headers(self) -> dict:
        """Get headers for Supabase API requests."""
        return {
            "Authorization": f"Bearer {self.service_key}",
            "apikey": self.service_key,
        }
    
    def _get_storage_url(self, path: str = "") -> str:
        """Build Supabase Storage URL."""
        base = f"{self.supabase_url}/storage/v1"
        if path:
            return f"{base}/{path}"
        return base

    def _require_storage_config(self) -> None:
        if not self.supabase_url or not self.service_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="File storage not configured. Please set SUPABASE_URL and SUPABASE_SERVICE_KEY.",
            )

    def _build_file_path(self, filename: str, folder: str) -> str:
        # Generate unique filename to avoid collisions.
        file_ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
        unique_name = f"{uuid.uuid4()}.{file_ext}" if file_ext else str(uuid.uuid4())
        return f"{folder}/{unique_name}"
    
    async def upload_file(
        self, 
        file_content: bytes,
        filename: str,
        content_type: str,
        folder: str = "incidents"
    ) -> dict:
        """
        Upload a file to Supabase Storage.
        
        Args:
            file_content: The file bytes to upload
            filename: Original filename
            content_type: MIME type of the file
            folder: Folder path in the bucket
            
        Returns:
            dict with file_path and public_url
        """
        self._require_storage_config()
        file_path = self._build_file_path(filename, folder)
        
        upload_url = self._get_storage_url(f"object/{self.bucket}/{file_path}")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                upload_url,
                headers={
                    **self._headers,
                    "Content-Type": content_type,
                },
                content=file_content,
            )
            
            if response.status_code not in (200, 201):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to upload file: {response.text}"
                )
        
        # Generate access URLs
        public_url = f"{self.supabase_url}/storage/v1/object/public/{self.bucket}/{file_path}"
        signed_url = None
        try:
            signed_url = await self.get_signed_url(file_path, expires_in=86400)
        except Exception:
            # Bucket may be public or signed URL endpoint may be disabled; keep upload successful.
            signed_url = None
        
        return {
            "file_path": file_path,
            "public_url": public_url,
            "signed_url": signed_url,
            "url": public_url,
            "original_name": filename,
            "content_type": content_type,
            "size": len(file_content),
        }

    def upload_file_sync(
        self,
        file_content: bytes,
        filename: str,
        content_type: str,
        folder: str = "incidents",
    ) -> dict:
        """
        Synchronous variant used by synchronous services (e.g., PDF export flow).
        """
        self._require_storage_config()
        file_path = self._build_file_path(filename, folder)
        upload_url = self._get_storage_url(f"object/{self.bucket}/{file_path}")

        with httpx.Client() as client:
            response = client.post(
                upload_url,
                headers={
                    **self._headers,
                    "Content-Type": content_type,
                },
                content=file_content,
            )

            if response.status_code not in (200, 201):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to upload file: {response.text}",
                )

        public_url = f"{self.supabase_url}/storage/v1/object/public/{self.bucket}/{file_path}"
        signed_url = None
        try:
            signed_url = self.get_signed_url_sync(file_path, expires_in=86400)
        except Exception:
            signed_url = None

        return {
            "file_path": file_path,
            "public_url": public_url,
            "signed_url": signed_url,
            "url": public_url,
            "original_name": filename,
            "content_type": content_type,
            "size": len(file_content),
        }
    
    async def delete_file(self, file_path: str) -> bool:
        """
        Delete a file from Supabase Storage.
        
        Args:
            file_path: Path to the file in the bucket
            
        Returns:
            True if deleted successfully
        """
        if not self.supabase_url or not self.service_key:
            return False
            
        delete_url = self._get_storage_url(f"object/{self.bucket}/{file_path}")
        
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                delete_url,
                headers=self._headers,
            )
            
            return response.status_code in (200, 204)
    
    def get_public_url(self, file_path: str) -> str:
        """
        Get the public URL for a file.
        
        Args:
            file_path: Path to the file in the bucket
            
        Returns:
            Public URL string
        """
        return f"{self.supabase_url}/storage/v1/object/public/{self.bucket}/{file_path}"
    
    async def get_signed_url(self, file_path: str, expires_in: int = 3600) -> str:
        """
        Get a signed URL for private file access.
        
        Args:
            file_path: Path to the file in the bucket
            expires_in: Seconds until URL expires (default 1 hour)
            
        Returns:
            Signed URL string
        """
        if not self.supabase_url or not self.service_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="File storage not configured."
            )
            
        sign_url = self._get_storage_url(f"object/sign/{self.bucket}/{file_path}")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                sign_url,
                headers=self._headers,
                json={"expiresIn": expires_in},
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to generate signed URL: {response.text}"
                )
            
            data = response.json()
            return f"{self.supabase_url}/storage/v1{data['signedURL']}"

    def get_signed_url_sync(self, file_path: str, expires_in: int = 3600) -> str:
        """Synchronous variant for signed URL generation."""
        if not self.supabase_url or not self.service_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="File storage not configured.",
            )

        sign_url = self._get_storage_url(f"object/sign/{self.bucket}/{file_path}")
        with httpx.Client() as client:
            response = client.post(
                sign_url,
                headers=self._headers,
                json={"expiresIn": expires_in},
            )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to generate signed URL: {response.text}",
                )

            data = response.json()
            return f"{self.supabase_url}/storage/v1{data['signedURL']}"
