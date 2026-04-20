from unittest.mock import MagicMock

import pytest

from app.exceptions.http import ForbiddenException
from app.services.tenant_scope import (
    assert_storage_path_in_tenant,
    build_tenant_storage_key,
    get_tenant_notification_recipients,
)


def test_build_tenant_storage_key_prefixes_upload_root() -> None:
    key = build_tenant_storage_key(
        "tenant-alpha",
        "photo.png",
        folder="incident-report-photos",
    )

    assert key.startswith("tenants/tenant-alpha/uploads/incident-report-photos/")
    assert key.endswith("/photo.png")


def test_assert_storage_path_in_tenant_rejects_cross_tenant_access() -> None:
    with pytest.raises(ForbiddenException):
        assert_storage_path_in_tenant(
            "tenants/tenant-beta/uploads/incident-report-photos/photo.png",
            "tenant-alpha",
        )


def test_get_tenant_notification_recipients_prefers_tenant_setting(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tenant_scope.get_tenant_setting_value",
        lambda session, key, tenant_id: {"emails": ["tenant-noc@example.com"]},
    )
    monkeypatch.setattr(
        "app.services.tenant_scope.list_tenant_user_emails",
        lambda session, roles, tenant_id: ["fallback@example.com"],
    )

    recipients = get_tenant_notification_recipients(MagicMock(), "tenant-alpha")

    assert recipients == ["tenant-noc@example.com"]


def test_get_tenant_notification_recipients_falls_back_to_tenant_users(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tenant_scope.get_tenant_setting_value",
        lambda session, key, tenant_id: None,
    )
    monkeypatch.setattr(
        "app.services.tenant_scope.list_tenant_user_emails",
        lambda session, roles, tenant_id: ["noc@tenant-alpha.com", "manager@tenant-alpha.com"],
    )

    recipients = get_tenant_notification_recipients(MagicMock(), "tenant-alpha")

    assert recipients == ["noc@tenant-alpha.com", "manager@tenant-alpha.com"]
