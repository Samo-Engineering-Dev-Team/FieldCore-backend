from app.services.pdf import PDFService


def _svc(supabase_url: str = "https://proj.supabase.co") -> PDFService:
    svc = PDFService()
    svc.supabase_url = supabase_url
    return svc


def test_rejects_non_http_scheme() -> None:
    svc = _svc()
    assert svc._is_safe_remote_url("file:///etc/passwd") is False
    assert svc._is_safe_remote_url("ftp://host/x") is False


def test_rejects_host_outside_supabase_allowlist() -> None:
    svc = _svc()
    assert svc._is_safe_remote_url("http://evil.example.com/x.png") is False


def test_rejects_loopback_and_private_ips() -> None:
    # No Supabase configured -> falls back to IP-safety check only.
    svc = _svc(supabase_url="")
    assert svc._is_safe_remote_url("http://127.0.0.1/x.png") is False
    assert svc._is_safe_remote_url("http://10.0.0.1/x.png") is False
    assert svc._is_safe_remote_url("http://169.254.169.254/latest/meta-data") is False
    assert svc._is_safe_remote_url("http://[::1]/x.png") is False


def test_rejects_empty_or_garbage() -> None:
    svc = _svc()
    assert svc._is_safe_remote_url("") is False
    assert svc._is_safe_remote_url("not a url") is False
