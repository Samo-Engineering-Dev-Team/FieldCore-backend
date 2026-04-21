from sqlmodel import SQLModel, Session, create_engine, select

from app.models import (
    AuditLog,
    SystemSetting,
    Tenant,
    TenantBootstrapRequest,
    TenantOffboardMode,
    TenantOffboardRequest,
    TenantOperationLog,
    TenantOperationalImportRequest,
    TenantSettingImportItem,
    User,
    Webhook,
)
from app.services.tenant import get_tenant_service
from app.utils.enums import UserRole, UserStatus


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            Tenant.__table__,
            TenantOperationLog.__table__,
            AuditLog.__table__,
            User.__table__,
            SystemSetting.__table__,
            Webhook.__table__,
        ],
    )
    return Session(engine)


def test_tenant_bootstrap_creates_tenant_defaults_admin_and_log() -> None:
    service = get_tenant_service()

    with _session() as session:
        response = service.bootstrap_tenant(
            TenantBootstrapRequest(
                name="Acme Fibre",
                slug="Acme-Fibre",
                admin_email="admin@acme.example.com",
                admin_name="Ada",
                admin_surname="Admin",
                admin_password="Password1",
            ),
            session,
        )

        assert response.tenant.id == "acme-fibre"
        assert response.setting_count >= 1

        admin = session.exec(select(User).where(User.email == "admin@acme.example.com")).one()
        assert admin.tenant_id == "acme-fibre"
        assert admin.role == UserRole.ADMIN
        assert admin.must_change_password is True

        noc_setting = session.exec(
            select(SystemSetting).where(
                SystemSetting.tenant_id == "acme-fibre",
                SystemSetting.key == "noc_email_addresses",
            )
        ).one()
        assert noc_setting.value == {"emails": ["admin@acme.example.com"]}

        log = session.get(TenantOperationLog, response.operation_log_id)
        assert log is not None
        assert log.operation == "bootstrap"
        assert log.dry_run is False

        audit = session.exec(select(AuditLog).where(AuditLog.tenant_id == "acme-fibre")).one()
        assert audit.action_type == "tenant.bootstrap"
        assert audit.resource == "tenant:acme-fibre"
        assert audit.after["admin_user_id"] == str(admin.id)


def test_operational_import_dry_run_previews_without_mutating_rows() -> None:
    service = get_tenant_service()

    with _session() as session:
        session.add(Tenant(id="tenant-alpha", slug="tenant-alpha", name="Tenant Alpha"))
        session.add(Tenant(id="tenant-beta", slug="tenant-beta", name="Tenant Beta"))
        session.add(
            User(
                name="Una",
                surname="Scoped",
                email="unscoped@example.com",
                role=UserRole.NOC,
                password_hash="hash",
            )
        )
        session.add(
            User(
                name="Con",
                surname="Flict",
                email="conflict@example.com",
                role=UserRole.NOC,
                tenant_id="tenant-beta",
                password_hash="hash",
            )
        )
        session.commit()

        response = service.import_operational_data(
            "tenant-alpha",
            TenantOperationalImportRequest(
                dry_run=True,
                user_emails=["unscoped@example.com", "conflict@example.com"],
                settings=[
                    TenantSettingImportItem(
                        key="noc_email_addresses",
                        value={"emails": ["noc@alpha.test"]},
                        category="notifications",
                    )
                ],
            ),
            session,
        )

        actions = {str(action.email): action.action for action in response.user_actions}
        assert actions == {
            "unscoped@example.com": "assign",
            "conflict@example.com": "conflict",
        }
        assert response.conflict_count == 1
        assert response.setting_actions[0].action == "create"

        unscoped_user = session.exec(select(User).where(User.email == "unscoped@example.com")).one()
        assert unscoped_user.tenant_id is None

        log = session.get(TenantOperationLog, response.operation_log_id)
        assert log is not None
        assert log.operation == "import"
        assert log.dry_run is True

        audit = session.exec(select(AuditLog).where(AuditLog.tenant_id == "tenant-alpha")).one()
        assert audit.action_type == "tenant.import"
        assert audit.after["dry_run"] is True
        assert audit.after["conflict_count"] == 1


def test_archive_offboarding_disables_access_and_keeps_audit_rows() -> None:
    service = get_tenant_service()

    with _session() as session:
        session.add(Tenant(id="tenant-alpha", slug="tenant-alpha", name="Tenant Alpha"))
        session.add(
            User(
                name="Admin",
                surname="Alpha",
                email="admin@alpha.test",
                role=UserRole.ADMIN,
                tenant_id="tenant-alpha",
                password_hash="hash",
            )
        )
        session.add(
            Webhook(
                tenant_id="tenant-alpha",
                url="https://example.test/webhook",
                event_type="incident_created",
                is_active=True,
            )
        )
        session.add(
            SystemSetting(
                tenant_id="tenant-alpha",
                key="noc_email_addresses",
                value={"emails": ["admin@alpha.test"]},
                category="notifications",
            )
        )
        session.commit()

        response = service.offboard_tenant(
            "tenant-alpha",
            TenantOffboardRequest(
                mode=TenantOffboardMode.ARCHIVE,
                dry_run=False,
                confirm_tenant_id="tenant-alpha",
                reason="contract ended",
            ),
            session,
        )

        assert response.applied is True
        assert response.mode == TenantOffboardMode.ARCHIVE

        tenant = session.get(Tenant, "tenant-alpha")
        assert tenant is not None
        assert tenant.status == "archived"
        assert tenant.archived_at is not None

        user = session.exec(select(User).where(User.email == "admin@alpha.test")).one()
        assert user.status == UserStatus.DISABLED
        assert user.deleted_at is not None

        webhook = session.exec(select(Webhook).where(Webhook.tenant_id == "tenant-alpha")).one()
        assert webhook.is_active is False

        setting = session.exec(
            select(SystemSetting).where(SystemSetting.tenant_id == "tenant-alpha")
        ).one()
        assert setting.key == "noc_email_addresses"

        audit = session.exec(select(AuditLog).where(AuditLog.tenant_id == "tenant-alpha")).one()
        assert audit.action_type == "tenant.offboard"
        assert audit.before["tenant"]["status"] == "active"
        assert audit.after["applied"] is True


def test_delete_offboarding_supports_dry_run_then_confirmed_delete() -> None:
    service = get_tenant_service()

    with _session() as session:
        session.add(Tenant(id="tenant-alpha", slug="tenant-alpha", name="Tenant Alpha"))
        session.add(
            User(
                name="Admin",
                surname="Alpha",
                email="admin@alpha.test",
                role=UserRole.ADMIN,
                tenant_id="tenant-alpha",
                password_hash="hash",
            )
        )
        session.add(
            SystemSetting(
                tenant_id="tenant-alpha",
                key="noc_email_addresses",
                value={"emails": ["admin@alpha.test"]},
                category="notifications",
            )
        )
        session.commit()

        preview = service.offboard_tenant(
            "tenant-alpha",
            TenantOffboardRequest(mode=TenantOffboardMode.DELETE, dry_run=True),
            session,
        )
        assert preview.applied is False
        assert preview.safe_to_delete is True
        assert {action.table: action.rows for action in preview.row_actions}["users"] == 1

        response = service.offboard_tenant(
            "tenant-alpha",
            TenantOffboardRequest(
                mode=TenantOffboardMode.DELETE,
                dry_run=False,
                confirm_tenant_id="tenant-alpha",
                acknowledge_data_loss=True,
            ),
            session,
        )

        assert response.applied is True
        assert session.get(Tenant, "tenant-alpha") is None
        assert session.exec(select(User).where(User.tenant_id == "tenant-alpha")).first() is None
        assert session.exec(
            select(SystemSetting).where(SystemSetting.tenant_id == "tenant-alpha")
        ).first() is None
        assert (
            session.exec(
                select(TenantOperationLog).where(TenantOperationLog.tenant_id == "tenant-alpha")
            ).first()
            is not None
        )
