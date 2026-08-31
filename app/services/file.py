import hashlib
import hmac
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from storage3 import create_client
from storage3._async.file_api import AsyncBucketProxy
from storage3._sync.file_api import SyncBucketProxy

from app.core.settings import app_settings

# In development, uploads never touch Supabase — they are written to this
# folder on the machine running the backend, so local work needs no cloud
# storage credentials. Resolved from this file's location rather than the
# process cwd, since uvicorn's --reload can be launched from anywhere.
LOCAL_UPLOAD_ROOT = Path(__file__).resolve().parents[2] / "uploads"

LOCAL_UPLOAD_BUCKET = "local"


def _local_disk_path(file_path: str) -> Path:
    """Resolve file_path under LOCAL_UPLOAD_ROOT, rejecting anything that
    escapes it (e.g. "../../etc/passwd") via ".." segments or an absolute
    path. file_path reaches here from client-controlled input (the delete
    endpoint takes it straight off the URL), so containment is enforced here
    rather than trusted at each call site."""
    root = LOCAL_UPLOAD_ROOT.resolve()
    disk_path = (root / file_path).resolve()
    if not disk_path.is_relative_to(root):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file path.",
        )
    return disk_path


def _local_url(file_path: str) -> str:
    base = app_settings.LOCAL_UPLOAD_BASE_URL.rstrip("/")
    return f"{base}/local-uploads/{file_path}"


def sign_local_upload_token(file_path: str) -> str:
    """HMAC over the path, keyed by the JWT secret. Stateless stand-in for a
    Supabase signed-upload token: the local PUT endpoint checks a caller was
    actually handed this exact path by create_signed_upload_url, without a
    server-side token table to expire or clean up."""
    return hmac.new(
        app_settings.JWT_SECRET_KEY.encode(), file_path.encode(), hashlib.sha256
    ).hexdigest()


def verify_local_upload_token(file_path: str, token: str) -> bool:
    return hmac.compare_digest(sign_local_upload_token(file_path), token)


def write_local_file(file_path: str, content: bytes) -> None:
    disk_path = _local_disk_path(file_path)
    disk_path.parent.mkdir(parents=True, exist_ok=True)
    disk_path.write_bytes(content)


class FileService:
    """Manages file uploads/downloads: Supabase Storage in staging/production,
    local disk in development (see LOCAL_UPLOAD_ROOT above)."""

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
        if app_settings.is_development:
            return _local_url(file_path)
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
        """Upload a file (async)."""
        file_path = self._build_file_path(filename, folder)

        if app_settings.is_development:
            write_local_file(file_path, file_content)
        else:
            self._require_storage_config()
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
        directly to storage (bypassing the serverless body cap) — Supabase in
        staging/production, this backend's own local-upload endpoint in
        development.

        The object path is chosen server-side (uuid + sanitized extension),
        so the client can never inject a path or overwrite an arbitrary object.

        Returns:
            dict with path, token, predicted public_url, and bucket
        """
        file_path = self._build_file_path(filename, folder)

        if app_settings.is_development:
            return {
                "path": file_path,
                "token": sign_local_upload_token(file_path),
                "public_url": _local_url(file_path),
                "bucket": LOCAL_UPLOAD_BUCKET,
            }

        self._require_storage_config()
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
        file_path = self._build_file_path(filename, folder)

        if app_settings.is_development:
            write_local_file(file_path, file_content)
        else:
            self._require_storage_config()
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
        """Delete a file. Returns True if deleted."""
        if app_settings.is_development:
            disk_path = _local_disk_path(file_path)
            if not disk_path.is_file():
                return False
            disk_path.unlink()
            return True

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
        if app_settings.is_development:
            return _local_url(file_path)

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
        if app_settings.is_development:
            return _local_url(file_path)

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
