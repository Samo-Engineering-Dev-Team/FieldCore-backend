from __future__ import annotations

import re
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends
from pydantic import EmailStr
from sqlalchemy import delete, func, inspect, or_, text, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core import SecurityUtils
from app.exceptions.http import BadRequestException, ConflictException, NotFoundException
from app.models import (
    LicenseHistory,
    SystemSetting,
    Tenant,
    TenantBootstrapRequest,
    TenantBootstrapResponse,
    TenantComplianceRecord,
    TenantFeatureUsageEvent,
    TenantImportSettingAction,
    TenantImportUserAction,
    TenantLicense,
    TenantOffboardMode,
    TenantOffboardRequest,
    TenantOffboardResponse,
    TenantOperationLog,
    TenantOperationType,
    TenantOperationalImportRequest,
    TenantOperationalImportResponse,
    TenantResponse,
    TenantRowAction,
    TenantSettingImportItem,
    TenantStatus,
    TenantUsageDaily,
    User,
    Webhook,
)
from app.models.notification import Notification
from app.models.passkey import PasskeyChallenge, PasskeyCredential
from app.models.user_session import UserSession
from app.utils.enums import UserRole, UserStatus
from app.utils.funcs import utcnow


TENANT_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,126}[a-z0-9])?$")

DEFAULT_TENANT_SETTINGS: tuple[dict[str, Any], ...] = (
    {
        "key": "debug_mode",
        "value": False,
        "description": "Enable verbose tenant diagnostics",
        "category": "system",
    },
    {
        "key": "maintenance_mode",
        "value": False,
        "description": "Temporarily disable tenant access for maintenance",
        "category": "system",
    },
    {
        "key": "incident_sla_hours",
        "value": 4,
        "description": "Default tenant incident resolution SLA hours",
        "category": "sla",
    },
    {
        "key": "task_sla_hours",
        "value": 24,
        "description": "Default tenant task completion SLA hours",
        "category": "sla",
    },
    {
        "key": "critical_threshold_percent",
        "value": 75,
        "description": "SLA critical threshold percentage",
        "category": "sla",
    },
    {
        "key": "at_risk_threshold_percent",
        "value": 50,
        "description": "SLA at-risk threshold percentage",
        "category": "sla",
    },
    {
        "key": "enable_location_tracking",
        "value": True,
        "description": "Enable GPS location tracking for tenant technicians",
        "category": "location",
    },
    {
        "key": "location_stale_threshold_hours",
        "value": 24,
        "description": "Hours before technician location is stale",
        "category": "location",
    },
    {
        "key": "geofence_default_radius_meters",
        "value": 100,
        "description": "Default site geofence radius in meters",
        "category": "location",
    },
    {
        "key": "enable_email_notifications",
        "value": True,
        "description": "Enable tenant email notifications",
        "category": "notifications",
    },
    {
        "key": "enable_sms_notifications",
        "value": False,
        "description": "Enable tenant SMS notifications",
        "category": "notifications",
    },
    {
        "key": "alert_on_sla_breach",
        "value": True,
        "description": "Send alerts when tenant SLA is breached",
        "category": "notifications",
    },
    {
        "key": "alert_on_critical",
        "value": True,
        "description": "Send alerts for critical tenant incidents",
        "category": "notifications",
    },
)


class _TenantService:
    def bootstrap_tenant(
        self,
        payload: TenantBootstrapRequest,
        session: Session,
        *,
        actor_user_id: UUID | None = None,
    ) -> TenantBootstrapResponse:
        tenant_id = self._normalize_required_tenant_id(payload.slug)
        admin_email = str(payload.admin_email).strip().lower()
        self._ensure_tenant_available(session, tenant_id)
        self._ensure_admin_email_available(session, admin_email)

        tenant = Tenant(id=tenant_id, slug=tenant_id, name=payload.name.strip())
        admin = User(
            name=payload.admin_name.strip(),
            surname=payload.admin_surname.strip(),
            email=admin_email,
            role=UserRole.ADMIN,
            tenant_id=tenant.id,
            password_hash=SecurityUtils.hash_password(payload.admin_password),
            must_change_password=True,
            status=UserStatus.ACTIVE,
        )

        settings = self._build_default_settings(
            tenant_id=tenant.id,
            admin_email=admin_email,
            overrides=payload.settings,
        )
        log = TenantOperationLog(
            tenant_id=tenant.id,
            operation=TenantOperationType.BOOTSTRAP,
            dry_run=False,
            actor_user_id=actor_user_id,
            message="Tenant bootstrap created tenant, default settings, and initial admin user",
            details={
                "tenant_id": tenant.id,
                "setting_keys": [setting.key for setting in settings],
                "admin_email": admin_email,
            },
        )

        try:
            session.add(tenant)
            session.add(admin)
            for setting in settings:
                session.add(setting)
            session.add(log)
            session.commit()
            session.refresh(tenant)
            session.refresh(admin)
            session.refresh(log)
        except IntegrityError as exc:
            session.rollback()
            raise ConflictException(f"Tenant bootstrap conflict: {exc.orig}")

        return TenantBootstrapResponse(
            tenant=self._to_response(tenant),
            admin_user_id=admin.id,
            setting_count=len(settings),
            operation_log_id=log.id,
        )

    def import_operational_data(
        self,
        tenant_id: str,
        payload: TenantOperationalImportRequest,
        session: Session,
        *,
        actor_user_id: UUID | None = None,
    ) -> TenantOperationalImportResponse:
        scoped_tenant_id = self._normalize_required_tenant_id(tenant_id)
        self._get_active_tenant(session, scoped_tenant_id)
        if not payload.dry_run:
            self._ensure_confirmed(scoped_tenant_id, payload.confirm_tenant_id)

        user_actions = self._preview_user_imports(
            session,
            scoped_tenant_id,
            payload.user_emails,
        )
        setting_actions = self._preview_setting_imports(
            session,
            scoped_tenant_id,
            payload.settings,
        )
        conflict_count = sum(1 for action in user_actions if action.action == "conflict")

        if not payload.dry_run and conflict_count:
            log = self._write_log(
                session,
                tenant_id=scoped_tenant_id,
                operation=TenantOperationType.IMPORT,
                dry_run=False,
                actor_user_id=actor_user_id,
                status="blocked",
                message="Tenant import blocked by conflicting rows",
                details=self._import_details(user_actions, setting_actions, conflict_count),
            )
            session.commit()
            raise BadRequestException(
                f"Import has {conflict_count} conflict(s). Run dry-run and resolve before applying."
            )

        applied = False
        if not payload.dry_run:
            self._apply_user_imports(session, scoped_tenant_id, user_actions)
            self._apply_setting_imports(session, scoped_tenant_id, payload.settings)
            applied = True

        log = self._write_log(
            session,
            tenant_id=scoped_tenant_id,
            operation=TenantOperationType.IMPORT,
            dry_run=payload.dry_run,
            actor_user_id=actor_user_id,
            message="Tenant import dry-run preview" if payload.dry_run else "Tenant import applied",
            details=self._import_details(user_actions, setting_actions, conflict_count),
        )
        session.commit()
        session.refresh(log)

        return TenantOperationalImportResponse(
            tenant_id=scoped_tenant_id,
            dry_run=payload.dry_run,
            applied=applied,
            user_actions=user_actions,
            setting_actions=setting_actions,
            conflict_count=conflict_count,
            operation_log_id=log.id,
        )

    def offboard_tenant(
        self,
        tenant_id: str,
        payload: TenantOffboardRequest,
        session: Session,
        *,
        actor_user_id: UUID | None = None,
    ) -> TenantOffboardResponse:
        scoped_tenant_id = self._normalize_required_tenant_id(tenant_id)
        tenant = self._get_tenant(session, scoped_tenant_id)
        row_actions = self._build_offboard_row_actions(session, scoped_tenant_id, payload.mode)
        blockers = self._delete_blockers(session, scoped_tenant_id) if payload.mode == TenantOffboardMode.DELETE else []
        safe_to_delete = not blockers

        if payload.dry_run:
            log = self._write_log(
                session,
                tenant_id=scoped_tenant_id,
                operation=TenantOperationType.OFFBOARD,
                dry_run=True,
                actor_user_id=actor_user_id,
                message="Tenant offboard dry-run preview",
                details=self._offboard_details(payload, row_actions, blockers),
            )
            session.commit()
            session.refresh(log)
            return TenantOffboardResponse(
                tenant_id=scoped_tenant_id,
                mode=payload.mode,
                dry_run=True,
                applied=False,
                safe_to_delete=safe_to_delete,
                blockers=blockers,
                row_actions=row_actions,
                operation_log_id=log.id,
            )

        self._ensure_confirmed(scoped_tenant_id, payload.confirm_tenant_id)
        if payload.mode == TenantOffboardMode.DELETE and not payload.acknowledge_data_loss:
            raise BadRequestException("acknowledge_data_loss must be true for delete offboarding")
        if payload.mode == TenantOffboardMode.DELETE and blockers:
            log = self._write_log(
                session,
                tenant_id=scoped_tenant_id,
                operation=TenantOperationType.OFFBOARD,
                dry_run=False,
                actor_user_id=actor_user_id,
                status="blocked",
                message="Tenant delete offboard blocked by dependent operational data",
                details=self._offboard_details(payload, row_actions, blockers),
            )
            session.commit()
            raise BadRequestException("Tenant delete blocked by dependent operational data")

        if payload.mode == TenantOffboardMode.ARCHIVE:
            self._archive_tenant_rows(session, tenant, actor_user_id=actor_user_id)
        else:
            self._delete_tenant_rows(session, scoped_tenant_id)

        log = self._write_log(
            session,
            tenant_id=scoped_tenant_id,
            operation=TenantOperationType.OFFBOARD,
            dry_run=False,
            actor_user_id=actor_user_id,
            message=f"Tenant offboard {payload.mode.value} applied",
            details=self._offboard_details(payload, row_actions, blockers),
        )
        session.commit()
        session.refresh(log)

        return TenantOffboardResponse(
            tenant_id=scoped_tenant_id,
            mode=payload.mode,
            dry_run=False,
            applied=True,
            safe_to_delete=safe_to_delete,
            blockers=blockers,
            row_actions=row_actions,
            operation_log_id=log.id,
        )

    def _build_default_settings(
        self,
        *,
        tenant_id: str,
        admin_email: str,
        overrides: dict[str, Any],
    ) -> list[SystemSetting]:
        remaining_overrides = dict(overrides)
        settings: list[SystemSetting] = []

        for default in DEFAULT_TENANT_SETTINGS:
            key = default["key"]
            value = remaining_overrides.pop(key, default["value"])
            settings.append(
                SystemSetting(
                    tenant_id=tenant_id,
                    key=key,
                    value=value,
                    description=default["description"],
                    category=default["category"],
                )
            )

        noc_key = "noc_email_addresses"
        noc_value = remaining_overrides.pop(noc_key, {"emails": [admin_email]})
        settings.append(
            SystemSetting(
                tenant_id=tenant_id,
                key=noc_key,
                value=noc_value,
                description="Tenant notification recipients",
                category="notifications",
            )
        )

        for key, value in remaining_overrides.items():
            settings.append(
                SystemSetting(
                    tenant_id=tenant_id,
                    key=key,
                    value=value,
                    description="Tenant bootstrap override",
                    category="general",
                )
            )

        return settings

    def _preview_user_imports(
        self,
        session: Session,
        tenant_id: str,
        emails: list[EmailStr],
    ) -> list[TenantImportUserAction]:
        actions: list[TenantImportUserAction] = []
        seen: set[str] = set()

        for raw_email in emails:
            email = str(raw_email).strip().lower()
            if email in seen:
                continue
            seen.add(email)

            user = session.exec(
                select(User).where(User.email == email, User.deleted_at.is_(None))
            ).first()
            if user is None:
                actions.append(
                    TenantImportUserAction(
                        email=email,
                        action="missing",
                        reason="No active user found for email",
                    )
                )
                continue
            if user.tenant_id == tenant_id:
                actions.append(TenantImportUserAction(email=email, action="unchanged"))
                continue
            if user.tenant_id:
                actions.append(
                    TenantImportUserAction(
                        email=email,
                        action="conflict",
                        reason=f"User already belongs to tenant '{user.tenant_id}'",
                    )
                )
                continue

            actions.append(TenantImportUserAction(email=email, action="assign"))

        return actions

    def _preview_setting_imports(
        self,
        session: Session,
        tenant_id: str,
        settings: list[TenantSettingImportItem],
    ) -> list[TenantImportSettingAction]:
        actions: list[TenantImportSettingAction] = []
        seen: set[str] = set()

        for setting in settings:
            key = setting.key.strip()
            if key in seen:
                actions.append(
                    TenantImportSettingAction(
                        key=key,
                        action="skipped",
                        reason="Duplicate key in import payload",
                    )
                )
                continue
            seen.add(key)

            existing = session.exec(
                select(SystemSetting).where(
                    SystemSetting.tenant_id == tenant_id,
                    SystemSetting.key == key,
                )
            ).first()
            actions.append(
                TenantImportSettingAction(
                    key=key,
                    action="update" if existing else "create",
                )
            )

        return actions

    def _apply_user_imports(
        self,
        session: Session,
        tenant_id: str,
        actions: list[TenantImportUserAction],
    ) -> None:
        emails_to_assign = [str(action.email).lower() for action in actions if action.action == "assign"]
        if not emails_to_assign:
            return

        users = session.exec(
            select(User).where(
                User.email.in_(emails_to_assign),  # type: ignore[attr-defined]
                User.deleted_at.is_(None),
                User.tenant_id.is_(None),
            )
        ).all()
        for user in users:
            user.tenant_id = tenant_id
            user.touch()
            session.add(user)

    def _apply_setting_imports(
        self,
        session: Session,
        tenant_id: str,
        settings: list[TenantSettingImportItem],
    ) -> None:
        seen: set[str] = set()
        for incoming in settings:
            key = incoming.key.strip()
            if key in seen:
                continue
            seen.add(key)
            self._upsert_setting(
                session,
                tenant_id=tenant_id,
                key=key,
                value=incoming.value,
                description=incoming.description,
                category=incoming.category,
            )

    def _upsert_setting(
        self,
        session: Session,
        *,
        tenant_id: str,
        key: str,
        value: Any,
        description: str | None,
        category: str,
    ) -> None:
        existing = session.exec(
            select(SystemSetting).where(
                SystemSetting.tenant_id == tenant_id,
                SystemSetting.key == key,
            )
        ).first()
        if existing:
            existing.value = value
            existing.description = description
            existing.category = category
            existing.updated_at = utcnow()
            session.add(existing)
            return

        session.add(
            SystemSetting(
                tenant_id=tenant_id,
                key=key,
                value=value,
                description=description,
                category=category,
            )
        )

    def _build_offboard_row_actions(
        self,
        session: Session,
        tenant_id: str,
        mode: TenantOffboardMode,
    ) -> list[TenantRowAction]:
        action = "delete" if mode == TenantOffboardMode.DELETE else "archive"
        return [
            TenantRowAction(table="tenants", rows=self._count_tenant(session, tenant_id), action=action),
            TenantRowAction(table="users", rows=self._count_model(session, User, User.tenant_id == tenant_id), action=action),
            TenantRowAction(
                table="system_settings",
                rows=self._count_model(session, SystemSetting, SystemSetting.tenant_id == tenant_id),
                action=action if mode == TenantOffboardMode.DELETE else "retain_for_audit",
            ),
            TenantRowAction(
                table="webhooks",
                rows=self._count_model(session, Webhook, Webhook.tenant_id == tenant_id),
                action=action if mode == TenantOffboardMode.DELETE else "deactivate",
            ),
            TenantRowAction(
                table="tenant_licenses",
                rows=self._count_model(session, TenantLicense, TenantLicense.tenant_id == tenant_id),
                action=action if mode == TenantOffboardMode.DELETE else "end_active_assignments",
            ),
            TenantRowAction(
                table="license_history",
                rows=self._count_model(session, LicenseHistory, LicenseHistory.tenant_id == tenant_id),
                action=action if mode == TenantOffboardMode.DELETE else "retain_for_audit",
            ),
            TenantRowAction(
                table="tenant_feature_usage_events",
                rows=self._count_model(session, TenantFeatureUsageEvent, TenantFeatureUsageEvent.tenant_id == tenant_id),
                action=action if mode == TenantOffboardMode.DELETE else "retain_for_audit",
            ),
            TenantRowAction(
                table="tenant_usage_daily",
                rows=self._count_model(session, TenantUsageDaily, TenantUsageDaily.tenant_id == tenant_id),
                action=action if mode == TenantOffboardMode.DELETE else "retain_for_audit",
            ),
            TenantRowAction(
                table="tenant_compliance_records",
                rows=self._count_model(session, TenantComplianceRecord, TenantComplianceRecord.tenant_id == tenant_id),
                action=action if mode == TenantOffboardMode.DELETE else "retain_for_audit",
            ),
        ]

    def _archive_tenant_rows(
        self,
        session: Session,
        tenant: Tenant,
        *,
        actor_user_id: UUID | None,
    ) -> None:
        now = utcnow()
        tenant.archive()
        session.add(tenant)

        users = session.exec(
            select(User).where(User.tenant_id == tenant.id, User.deleted_at.is_(None))
        ).all()
        user_ids = [user.id for user in users]
        for user in users:
            user.status = UserStatus.DISABLED
            user.deleted_at = now
            user.updated_at = now
            session.add(user)

        if user_ids and self._table_exists(session, UserSession.__tablename__):
            session.execute(
                update(UserSession)
                .where(UserSession.user_id.in_(user_ids))  # type: ignore[attr-defined]
                .values(is_active=False, updated_at=now)
            )

        if user_ids and self._table_exists(session, PasskeyCredential.__tablename__):
            session.execute(
                update(PasskeyCredential)
                .where(PasskeyCredential.user_id.in_(user_ids))  # type: ignore[attr-defined]
                .values(deleted_at=now, updated_at=now)
            )

        if user_ids and self._table_exists(session, PasskeyChallenge.__tablename__):
            session.execute(
                update(PasskeyChallenge)
                .where(PasskeyChallenge.user_id.in_(user_ids))  # type: ignore[attr-defined]
                .values(deleted_at=now, updated_at=now)
            )

        if self._table_exists(session, Webhook.__tablename__):
            session.execute(
                update(Webhook)
                .where(Webhook.tenant_id == tenant.id)
                .values(is_active=False, updated_at=now)
            )

        if self._table_exists(session, TenantLicense.__tablename__):
            session.execute(
                update(TenantLicense)
                .where(
                    TenantLicense.tenant_id == tenant.id,
                    or_(TenantLicense.ends_at.is_(None), TenantLicense.ends_at > now),
                )
                .values(
                    ends_at=now,
                    unassigned_by_user_id=actor_user_id,
                    updated_at=now,
                )
            )

    def _delete_tenant_rows(self, session: Session, tenant_id: str) -> None:
        user_ids = list(
            session.exec(select(User.id).where(User.tenant_id == tenant_id)).all()
        )

        self._delete_model_by_tenant(session, LicenseHistory, tenant_id)
        self._delete_model_by_tenant(session, TenantComplianceRecord, tenant_id)
        self._delete_model_by_tenant(session, TenantUsageDaily, tenant_id)
        self._delete_model_by_tenant(session, TenantFeatureUsageEvent, tenant_id)
        self._delete_model_by_tenant(session, TenantLicense, tenant_id)
        self._delete_model_by_tenant(session, Webhook, tenant_id)
        self._delete_model_by_tenant(session, SystemSetting, tenant_id)

        self._delete_user_child_rows(session, user_ids)
        if self._table_exists(session, User.__tablename__):
            session.execute(delete(User).where(User.tenant_id == tenant_id))

        session.execute(delete(Tenant).where(Tenant.id == tenant_id))

    def _delete_user_child_rows(self, session: Session, user_ids: list[UUID]) -> None:
        if not user_ids:
            return

        child_models = (
            PasskeyChallenge,
            PasskeyCredential,
            UserSession,
            Notification,
        )
        for model in child_models:
            if self._table_exists(session, model.__tablename__):
                session.execute(
                    delete(model).where(model.user_id.in_(user_ids))  # type: ignore[attr-defined]
                )

    def _delete_model_by_tenant(self, session: Session, model: type[Any], tenant_id: str) -> None:
        if not self._table_exists(session, model.__tablename__):
            return
        session.execute(delete(model).where(model.tenant_id == tenant_id))

    def _delete_blockers(self, session: Session, tenant_id: str) -> list[str]:
        blockers: list[str] = []
        if self._table_exists(session, "technicians") and self._table_exists(session, "users"):
            count = int(
                session.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM technicians t
                        JOIN users u ON u.id = t.user_id
                        WHERE u.tenant_id = :tenant_id
                        """
                    ),
                    {"tenant_id": tenant_id},
                ).scalar_one()
            )
            if count:
                blockers.append(
                    f"{count} technician row(s) linked to tenant users; archive first or migrate operational data"
                )
        return blockers

    def _count_tenant(self, session: Session, tenant_id: str) -> int:
        return self._count_model(session, Tenant, Tenant.id == tenant_id)

    def _count_model(self, session: Session, model: type[Any], *criteria: Any) -> int:
        if not self._table_exists(session, model.__tablename__):
            return 0
        statement = select(func.count()).select_from(model)
        for criterion in criteria:
            statement = statement.where(criterion)
        return int(session.exec(statement).one())

    def _table_exists(self, session: Session, table_name: str | None) -> bool:
        if not table_name:
            return False
        return bool(inspect(session.connection()).has_table(table_name))

    def _ensure_tenant_available(self, session: Session, tenant_id: str) -> None:
        existing = session.exec(select(Tenant).where(Tenant.id == tenant_id)).first()
        if existing:
            raise ConflictException("Tenant already exists")

    def _ensure_admin_email_available(self, session: Session, email: str) -> None:
        existing = session.exec(
            select(User).where(User.email == email.lower(), User.deleted_at.is_(None))
        ).first()
        if existing:
            raise ConflictException("Admin email already belongs to an active user")

    def _get_active_tenant(self, session: Session, tenant_id: str) -> Tenant:
        tenant = self._get_tenant(session, tenant_id)
        if tenant.status != TenantStatus.ACTIVE:
            raise BadRequestException("Tenant is not active")
        return tenant

    def _get_tenant(self, session: Session, tenant_id: str) -> Tenant:
        tenant = session.exec(
            select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
        ).first()
        if not tenant:
            raise NotFoundException("Tenant not found")
        return tenant

    def _normalize_required_tenant_id(self, tenant_id: str | None) -> str:
        normalized = (tenant_id or "").strip().lower()
        if not normalized:
            raise BadRequestException("tenant_id is required")
        if not TENANT_SLUG_RE.match(normalized):
            raise BadRequestException(
                "tenant_id must use lowercase letters, numbers, and hyphens only"
            )
        return normalized

    def _ensure_confirmed(self, tenant_id: str, confirm_tenant_id: str | None) -> None:
        confirmed = self._normalize_required_tenant_id(confirm_tenant_id)
        if confirmed != tenant_id:
            raise BadRequestException("confirm_tenant_id must match tenant_id")

    def _write_log(
        self,
        session: Session,
        *,
        tenant_id: str,
        operation: TenantOperationType,
        dry_run: bool,
        actor_user_id: UUID | None,
        message: str,
        details: dict[str, Any],
        status: str = "completed",
    ) -> TenantOperationLog:
        log = TenantOperationLog(
            tenant_id=tenant_id,
            operation=operation,
            dry_run=dry_run,
            actor_user_id=actor_user_id,
            status=status,
            message=message,
            details=details,
        )
        session.add(log)
        return log

    def _import_details(
        self,
        user_actions: list[TenantImportUserAction],
        setting_actions: list[TenantImportSettingAction],
        conflict_count: int,
    ) -> dict[str, Any]:
        return {
            "users": [action.model_dump(mode="json") for action in user_actions],
            "settings": [action.model_dump(mode="json") for action in setting_actions],
            "conflict_count": conflict_count,
        }

    def _offboard_details(
        self,
        payload: TenantOffboardRequest,
        row_actions: list[TenantRowAction],
        blockers: list[str],
    ) -> dict[str, Any]:
        return {
            "mode": payload.mode.value,
            "reason": payload.reason,
            "row_actions": [action.model_dump(mode="json") for action in row_actions],
            "blockers": blockers,
        }

    def _to_response(self, tenant: Tenant) -> TenantResponse:
        return TenantResponse(**tenant.model_dump())


def get_tenant_service() -> _TenantService:
    return _TenantService()


TenantServiceDep = Annotated[_TenantService, Depends(get_tenant_service)]
