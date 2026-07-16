import re
import uuid
from typing import Any

from fastapi import HTTPException, status
from storage3 import create_client
from storage3._async.file_api import AsyncBucketProxy
from storage3._sync.file_api import SyncBucketProxy

from app.core.settings import app_settings


class FileService:
    """Service for managing file uploads/downloads via Supabase Storage."""

    def __init__(self) -> None:
        self.supabase_url: str = app_settings.SUPABASE_URL
        self.service_key: str = app_settings.SUPABASE_SERVICE_KEY
        self.bucket: str = app_settings.SUPABASE_STORAGE_BUCKET

    @property
    def _headers(self) -> dict[str, str]:
        """Headers for Supabase Storage requests (service role bypasses RLS)."""
        return {
            "Authorization": f"Bearer {self.service_key}",
            "apikey": self.service_key,
        }

    def _storage_base_url(self) -> str:
        return f"{self.supabase_url}/storage/v1"

    def _async_bucket(self) -> AsyncBucketProxy:
        client = create_client(
            self._storage_base_url(), self._headers, is_async=True
        )
        return client.from_(self.bucket)

    def _sync_bucket(self) -> SyncBucketProxy:
        client = create_client(
            self._storage_base_url(), self._headers, is_async=False
        )
        return client.from_(self.bucket)

    def _require_storage_config(self) -> None:
        if not self.supabase_url or not self.service_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="File storage not configured. Please set SUPABASE_URL and SUPABASE_SERVICE_KEY.",
            )

    def _build_file_path(self, filename: str, folder: str) -> str:
        # Generate unique filename to avoid collisions.
        raw_ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
        # Sanitize the extension so it can't inject path segments or junk
        # (e.g. "jp/g", "../x") into the storage object path.
        file_ext = re.sub(r"[^A-Za-z0-9]", "", raw_ext).lower()[:10]
        unique_name = f"{uuid.uuid4()}.{file_ext}" if file_ext else str(uuid.uuid4())
        return f"{folder}/{unique_name}"

    def _public_url(self, file_path: str) -> str:
        return (
            f"{self.supabase_url}/storage/v1/object/public/{self.bucket}/{file_path}"
        )

    def _normalize_signed_url(self, signed: str | None) -> str:
        if not signed:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Signed URL response missing URL.",
            )
        if signed.startswith("http"):
            return signed
        return f"{self._storage_base_url()}{signed}"

    async def upload_file(
        self,
        file_content: bytes,
        filename: str,
        content_type: str,
        folder: str = "incidents",
    ) -> dict[str, Any]:
        """Upload a file to Supabase Storage (async)."""
        self._require_storage_config()
        file_path = self._build_file_path(filename, folder)

        try:
            await self._async_bucket().upload(
                file_path, file_content, {"content-type": content_type}
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to upload file: {exc}",
            )

        signed_url: str | None = None
        try:
            signed_url = await self.get_signed_url(file_path, expires_in=86400)
        except Exception:
            # Bucket may be public or signed URL endpoint may be disabled; keep upload successful.
            signed_url = None

        public_url = self._public_url(file_path)
        return {
            "file_path": file_path,
            "public_url": public_url,
            "signed_url": signed_url,
            "url": public_url,
            "original_name": filename,
            "content_type": content_type,
            "size": len(file_content),
        }

    async def create_signed_upload_url(
        self,
        filename: str,
        folder: str = "incidents",
    ) -> dict[str, Any]:
        """
        Mint a short-lived signed upload URL so the client can PUT bytes
        directly to Supabase Storage (bypassing the serverless body cap).

        The object path is chosen server-side (uuid + sanitized extension),
        so the client can never inject a path or overwrite an arbitrary object.

        Returns:
            dict with path, token, predicted public_url, and bucket
        """
        self._require_storage_config()
        file_path = self._build_file_path(filename, folder)

        try:
            result = await self._async_bucket().create_signed_upload_url(file_path)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create signed upload URL: {exc}",
            )

        token = result.get("token")
        if not token:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Signed upload URL response missing token.",
            )

        return {
            "path": file_path,
            "token": token,
            "public_url": self._public_url(file_path),
            "bucket": self.bucket,
        }

    def upload_file_sync(
        self,
        file_content: bytes,
        filename: str,
        content_type: str,
        folder: str = "incidents",
    ) -> dict[str, Any]:
        """
        Synchronous variant used by synchronous services (e.g., PDF export flow).
        """
        self._require_storage_config()
        file_path = self._build_file_path(filename, folder)

        try:
            self._sync_bucket().upload(
                file_path, file_content, {"content-type": content_type}
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to upload file: {exc}",
            )

        signed_url: str | None = None
        try:
            signed_url = self.get_signed_url_sync(file_path, expires_in=86400)
        except Exception:
            signed_url = None

        public_url = self._public_url(file_path)
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
        """Delete a file from Supabase Storage. Returns True if deleted."""
        if not self.supabase_url or not self.service_key:
            return False

        try:
            await self._async_bucket().remove([file_path])
            return True
        except Exception:
            return False

    def get_public_url(self, file_path: str) -> str:
        """Get the public URL for a file."""
        return self._public_url(file_path)

    async def get_signed_url(self, file_path: str, expires_in: int = 3600) -> str:
        """Get a signed URL for private file access (async)."""
        self._require_storage_config()
        try:
            result = await self._async_bucket().create_signed_url(
                file_path, expires_in
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate signed URL: {exc}",
            )
        return self._normalize_signed_url(
            result.get("signedURL") or result.get("signedUrl")
        )

    def get_signed_url_sync(self, file_path: str, expires_in: int = 3600) -> str:
        """Synchronous variant for signed URL generation."""
        self._require_storage_config()
        try:
            result = self._sync_bucket().create_signed_url(file_path, expires_in)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate signed URL: {exc}",
            )
        return self._normalize_signed_url(
            result.get("signedURL") or result.get("signedUrl")
        )
