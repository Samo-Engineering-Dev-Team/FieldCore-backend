from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import inspect, text
from sqlmodel import Session

from app.core.settings import app_settings
from app.exceptions.http import ForbiddenException, NotFoundException
from app.models.support import TenantDiagnosticsResponse, TenantHealthCheck
from app.models.auth import TokenData
from app.utils.enums import UserRole


class _SupportDiagnosticsService:
    def read_tenant_diagnostics(
        self,
        tenant_id: str,
        session: Session,
        current_user: TokenData,
    ) -> TenantDiagnosticsResponse:
        scoped_tenant_id = tenant_id.strip().lower()
        self._assert_can_view_tenant(scoped_tenant_id, current_user)

        tenant = self._tenant_row(session, scoped_tenant_id)
        if tenant is None:
            raise NotFoundException("Tenant not found")

        counts = self._counts(session, scoped_tenant_id)
        recent_operations = self._recent_operations(session, scoped_tenant_id)
        checks = self._checks(tenant, counts)

        return TenantDiagnosticsResponse(
            tenant_id=scoped_tenant_id,
            tenant_name=tenant.get("name"),
            tenant_status=tenant.get("status"),
            checks=checks,
            counts=counts,
            recent_operations=recent_operations,
        )

    def _assert_can_view_tenant(self, tenant_id: str, current_user: TokenData) -> None:
        if current_user.role == UserRole.SUPER_ADMIN:
            return
        if current_user.role == UserRole.ADMIN and current_user.tenant_id is None:
            return
        if current_user.role == UserRole.ADMIN and current_user.tenant_id == tenant_id:
            return
        raise ForbiddenException("You do not have permission to view this tenant diagnostics summary")

    def _tenant_row(self, session: Session, tenant_id: str) -> dict[str, Any] | None:
        if not self._table_exists(session, "tenants"):
            return None
        row = session.execute(
            text(
                """
                SELECT id, name, status, archived_at, created_at, updated_at
                FROM tenants
                WHERE id = :tenant_id AND deleted_at IS NULL
                """
            ),
            {"tenant_id": tenant_id},
        ).mappings().first()
        return dict(row) if row is not None else None

    def _counts(self, session: Session, tenant_id: str) -> dict[str, int]:
        return {
            "users_total": self._count(
                session,
                "users",
                "tenant_id = :tenant_id AND deleted_at IS NULL",
                {"tenant_id": tenant_id},
            ),
            "users_active": self._count(
                session,
                "users",
                "tenant_id = :tenant_id AND deleted_at IS NULL AND status IN ('ACTIVE', 'active')",
                {"tenant_id": tenant_id},
            ),
            "admin_users_active": self._count(
                session,
                "users",
                (
                    "tenant_id = :tenant_id AND deleted_at IS NULL "
                    "AND role IN ('ADMIN', 'admin') AND status IN ('ACTIVE', 'active')"
                ),
                {"tenant_id": tenant_id},
            ),
            "system_settings": self._count(
                session,
                "system_settings",
                "tenant_id = :tenant_id",
                {"tenant_id": tenant_id},
            ),
            "webhooks_active": self._count(
                session,
                "webhooks",
                "tenant_id = :tenant_id AND is_active = :is_active",
                {"tenant_id": tenant_id, "is_active": True},
            ),
            "webhooks_inactive": self._count(
                session,
                "webhooks",
                "tenant_id = :tenant_id AND is_active = :is_active",
                {"tenant_id": tenant_id, "is_active": False},
            ),
            "tenant_operation_logs": self._count(
                session,
                "tenant_operation_logs",
                "tenant_id = :tenant_id",
                {"tenant_id": tenant_id},
            ),
            "audit_events": self._count(
                session,
                "audit_log",
                "tenant_id = :tenant_id",
                {"tenant_id": tenant_id},
            ),
            "tenant_templates": self._count(
                session,
                "tenant_templates",
                "tenant_id = :tenant_id AND deleted_at IS NULL",
                {"tenant_id": tenant_id},
            ),
            "tenant_licenses_active": self._count(
                session,
                "tenant_licenses",
                (
                    "tenant_id = :tenant_id AND deleted_at IS NULL "
                    "AND (ends_at IS NULL OR ends_at > CURRENT_TIMESTAMP)"
                ),
                {"tenant_id": tenant_id},
            ),
            "technicians": self._count_join(
                session,
                """
                SELECT COUNT(*)
                FROM technicians t
                JOIN users u ON u.id = t.user_id
                WHERE u.tenant_id = :tenant_id
                  AND u.deleted_at IS NULL
                  AND t.deleted_at IS NULL
                """,
                {"tenant_id": tenant_id},
                ("technicians", "users"),
            ),
            "tasks": self._count_join(
                session,
                """
                SELECT COUNT(*)
                FROM tasks task
                JOIN technicians t ON t.id = task.technician_id
                JOIN users u ON u.id = t.user_id
                WHERE u.tenant_id = :tenant_id
                  AND u.deleted_at IS NULL
                  AND t.deleted_at IS NULL
                  AND task.deleted_at IS NULL
                """,
                {"tenant_id": tenant_id},
                ("tasks", "technicians", "users"),
            ),
            "open_incidents": self._count_join(
                session,
                """
                SELECT COUNT(*)
                FROM incidents i
                JOIN technicians t ON t.id = i.technician_id
                JOIN users u ON u.id = t.user_id
                WHERE u.tenant_id = :tenant_id
                  AND u.deleted_at IS NULL
                  AND t.deleted_at IS NULL
                  AND i.deleted_at IS NULL
                  AND i.status NOT IN ('RESOLVED', 'resolved')
                """,
                {"tenant_id": tenant_id},
                ("incidents", "technicians", "users"),
            ),
            "reports": self._count_join(
                session,
                """
                SELECT COUNT(*)
                FROM reports r
                JOIN technicians t ON t.id = r.technician_id
                JOIN users u ON u.id = t.user_id
                WHERE u.tenant_id = :tenant_id
                  AND u.deleted_at IS NULL
                  AND t.deleted_at IS NULL
                  AND r.deleted_at IS NULL
                """,
                {"tenant_id": tenant_id},
                ("reports", "technicians", "users"),
            ),
        }

    def _checks(self, tenant: dict[str, Any], counts: dict[str, int]) -> list[TenantHealthCheck]:
        tenant_status = str(tenant.get("status") or "").lower()
        return [
            TenantHealthCheck(name="tenant_exists", ok=True, detail="Tenant row found"),
            TenantHealthCheck(
                name="tenant_active",
                ok=tenant_status == "active",
                detail=f"status={tenant.get('status')}",
            ),
            TenantHealthCheck(
                name="active_admin",
                ok=counts["admin_users_active"] > 0,
                detail=f"{counts['admin_users_active']} active admin user(s)",
            ),
            TenantHealthCheck(
                name="settings_seeded",
                ok=counts["system_settings"] > 0,
                detail=f"{counts['system_settings']} tenant setting(s)",
            ),
            TenantHealthCheck(
                name="license_assignment",
                ok=counts["tenant_licenses_active"] > 0,
                detail=f"{counts['tenant_licenses_active']} active assignment(s)",
            ),
        ]

    def _recent_operations(self, session: Session, tenant_id: str) -> list[dict[str, Any]]:
        if not self._table_exists(session, "tenant_operation_logs"):
            return []
        rows = session.execute(
            text(
                """
                SELECT operation, dry_run, status, message, created_at
                FROM tenant_operation_logs
                WHERE tenant_id = :tenant_id
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {
                "tenant_id": tenant_id,
                "limit": app_settings.SUPPORT_DIAGNOSTICS_RECENT_OPERATION_LIMIT,
            },
        ).mappings().all()
        return [dict(row) for row in rows]

    def _count(
        self,
        session: Session,
        table_name: str,
        where_clause: str,
        params: dict[str, Any],
    ) -> int:
        if not self._table_exists(session, table_name):
            return 0
        value = session.execute(
            text(f"SELECT COUNT(*) FROM {table_name} WHERE {where_clause}"),
            params,
        ).scalar_one()
        return int(value or 0)

    def _count_join(
        self,
        session: Session,
        sql: str,
        params: dict[str, Any],
        tables: tuple[str, ...],
    ) -> int:
        if not all(self._table_exists(session, table_name) for table_name in tables):
            return 0
        value = session.execute(text(sql), params).scalar_one()
        return int(value or 0)

    def _table_exists(self, session: Session, table_name: str) -> bool:
        return bool(inspect(session.connection()).has_table(table_name))


def get_support_diagnostics_service() -> _SupportDiagnosticsService:
    return _SupportDiagnosticsService()


SupportDiagnosticsServiceDep = Annotated[
    _SupportDiagnosticsService,
    Depends(get_support_diagnostics_service),
]
