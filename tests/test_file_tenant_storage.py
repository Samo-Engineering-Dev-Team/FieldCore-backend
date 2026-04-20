from app.services.file import FileService


class _DummyResponse:
    status_code = 200
    text = ""


class _DummyClient:
    def __init__(self) -> None:
        self.url = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, headers=None, content=None):
        self.url = url
        return _DummyResponse()


def test_upload_file_sync_prefixes_path_with_tenant(monkeypatch) -> None:
    client = _DummyClient()
    monkeypatch.setattr("app.services.file.httpx.Client", lambda: client)
    monkeypatch.setattr(
        FileService,
        "get_signed_url_sync",
        lambda self, file_path, expires_in=3600, tenant_id=None: f"signed://{file_path}",
    )

    service = FileService()
    service.supabase_url = "https://example.supabase.co"
    service.service_key = "service-key"
    service.bucket = "attachments"

    result = service.upload_file_sync(
        file_content=b"abc123",
        filename="photo.png",
        content_type="image/png",
        folder="incident-report-photos",
        tenant_id="tenant-alpha",
    )

    assert result["file_path"].startswith("tenants/tenant-alpha/uploads/incident-report-photos/")
    assert "/tenants/tenant-alpha/uploads/incident-report-photos/" in client.url
