import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated

from fastapi import Depends
from sqlalchemy import func, or_
from sqlmodel import Session, select

from app.exceptions.http import BadRequestException
from app.models import (
    Entitlement,
    LicensePlan,
    LicenseProduct,
    TenantComplianceMetric,
    TenantComplianceOverviewResponse,
    TenantComplianceRecord,
    TenantComplianceRunSummary,
    TenantComplianceTenantSnapshot,
    TenantFeatureUsageEvent,
    TenantLicense,
    TenantUsageDaily,
    User,
)
from app.utils.enums import UserStatus
from app.utils.funcs import utcnow


def _normalize_tenant_id(tenant_id: str | None) -> str | None:
    if tenant_id is None:
        return None
    normalized = tenant_id.strip()
    return normalized or None


def _normalize_feature_key(feature_key: str) -> str:
    normalized = feature_key.strip().lower()
    if not normalized:
        raise BadRequestException("feature_key is required")
    return normalized


def _utc_day_window(usage_date: date) -> tuple[datetime, datetime]:
    day_start = datetime.combine(usage_date, time.min, tzinfo=timezone.utc)
    return day_start, day_start + timedelta(days=1)


def _coerce_usage_date(usage_date: date | None) -> date:
    resolved = usage_date or utcnow().date()
    if resolved > utcnow().date():
        raise BadRequestException("usage_date cannot be in the future")
    return resolved


def _parse_numeric_limit(value: str | None) -> int | None:
    if value is None:
        return None

    normalized = value.strip().lower()
    if not normalized or normalized in {"unlimited", "enabled", "true", "yes", "inf", "infinite"}:
        return None

    try:
        parsed = int(normalized)
    except ValueError:
        return None

    return max(parsed, 0)


@dataclass
class UsageRowData:
    tenant_id: str
    usage_date: date
    feature_key: str
    feature_name: str
    usage_value: int
    source: str


@dataclass
class EntitlementAggregate:
    feature_name: str
    enabled: bool
    entitlement_value: str | None
    entitlement_limit: int | None
    plan_codes: list[str]


class LicensingComplianceService:
    def compute_daily_metering(
        self,
        session: Session,
        *,
        usage_date: date | None = None,
        tenant_id: str | None = None,
    ) -> TenantComplianceRunSummary:
        resolved_usage_date = _coerce_usage_date(usage_date)
        scoped_tenant_id = _normalize_tenant_id(tenant_id)

        usage_rows_by_key = self._build_usage_rows(
            session,
            usage_date=resolved_usage_date,
            tenant_id=scoped_tenant_id,
        )
        self._sync_usage_rows(session, usage_rows_by_key, resolved_usage_date, scoped_tenant_id)

        compliance_rows_by_key = self._build_compliance_rows(
            session,
            usage_rows_by_key=usage_rows_by_key,
            usage_date=resolved_usage_date,
            tenant_id=scoped_tenant_id,
        )
        self._sync_compliance_rows(session, compliance_rows_by_key, resolved_usage_date, scoped_tenant_id)
        session.commit()

        overage_tenants = {
            tenant_key
            for tenant_key, feature_key in compliance_rows_by_key
            if compliance_rows_by_key[(tenant_key, feature_key)].status in {"over_limit", "not_entitled"}
        }
        processed_tenants = {tenant_key for tenant_key, _ in usage_rows_by_key}

        return TenantComplianceRunSummary(
            usage_date=resolved_usage_date,
            processed_at=utcnow(),
            processed_tenant_count=len(processed_tenants),
            usage_row_count=len(usage_rows_by_key),
            compliance_row_count=len(compliance_rows_by_key),
            overage_tenant_count=len(overage_tenants),
        )

    def get_compliance_overview(
        self,
        session: Session,
        *,
        usage_date: date | None = None,
        tenant_id: str | None = None,
    ) -> TenantComplianceOverviewResponse:
        scoped_tenant_id = _normalize_tenant_id(tenant_id)
        resolved_usage_date = usage_date or self._latest_usage_date(session, scoped_tenant_id)
        if resolved_usage_date is None:
            return TenantComplianceOverviewResponse(generated_at=utcnow(), usage_date=None)

        statement = select(TenantComplianceRecord).where(
            TenantComplianceRecord.deleted_at.is_(None),
            TenantComplianceRecord.usage_date == resolved_usage_date,
        )
        if scoped_tenant_id is not None:
            statement = statement.where(TenantComplianceRecord.tenant_id == scoped_tenant_id)

        rows = list(
            session.exec(
                statement.order_by(TenantComplianceRecord.tenant_id, TenantComplianceRecord.feature_key)
            ).all()
        )

        grouped: dict[str, list[TenantComplianceMetric]] = {}
        for row in rows:
            grouped.setdefault(row.tenant_id, []).append(
                TenantComplianceMetric(
                    feature_key=row.feature_key,
                    feature_name=row.feature_name,
                    entitlement_value=row.entitlement_value,
                    entitlement_limit=row.entitlement_limit,
                    entitlement_is_enabled=row.entitlement_is_enabled,
                    usage_value=row.usage_value,
                    overage_value=row.overage_value,
                    status=row.status,
                    source=row.source,
                    plan_codes=self._deserialize_plan_codes(row.plan_codes_json),
                )
            )

        tenants: list[TenantComplianceTenantSnapshot] = []
        for tenant_key, metrics in grouped.items():
            overage_count = sum(
                1 for metric in metrics if metric.status in {"over_limit", "not_entitled"}
            )
            tenants.append(
                TenantComplianceTenantSnapshot(
                    tenant_id=tenant_key,
                    usage_date=resolved_usage_date,
                    has_overages=overage_count > 0,
                    overage_count=overage_count,
                    metrics=metrics,
                )
            )

        tenants.sort(key=lambda snapshot: snapshot.tenant_id)

        return TenantComplianceOverviewResponse(
            generated_at=utcnow(),
            usage_date=resolved_usage_date,
            tenant_count=len(tenants),
            overage_tenant_count=sum(1 for tenant in tenants if tenant.has_overages),
            tenants=tenants,
        )

    def _build_usage_rows(
        self,
        session: Session,
        *,
        usage_date: date,
        tenant_id: str | None,
    ) -> dict[tuple[str, str], UsageRowData]:
        tracked_tenants = self._tracked_tenants(session, usage_date=usage_date, tenant_id=tenant_id)
        usage_rows: dict[tuple[str, str], UsageRowData] = {}

        if not tracked_tenants:
            return usage_rows

        seat_counts = self._active_seat_counts(session, tracked_tenants)
        for tenant_key in tracked_tenants:
            usage_rows[(tenant_key, "seats")] = UsageRowData(
                tenant_id=tenant_key,
                usage_date=usage_date,
                feature_key="seats",
                feature_name="Active Seats",
                usage_value=seat_counts.get(tenant_key, 0),
                source="active_users",
            )

        day_start, next_day = _utc_day_window(usage_date)
        feature_events_statement = (
            select(
                TenantFeatureUsageEvent.tenant_id,
                TenantFeatureUsageEvent.feature_key,
                func.max(TenantFeatureUsageEvent.feature_name),
                func.sum(TenantFeatureUsageEvent.usage_quantity),
            )
            .where(
                TenantFeatureUsageEvent.deleted_at.is_(None),
                TenantFeatureUsageEvent.occurred_at >= day_start,
                TenantFeatureUsageEvent.occurred_at < next_day,
                TenantFeatureUsageEvent.tenant_id.in_(tracked_tenants),
            )
            .group_by(TenantFeatureUsageEvent.tenant_id, TenantFeatureUsageEvent.feature_key)
        )

        for event_tenant_id, feature_key, feature_name, usage_value in session.exec(
            feature_events_statement
        ).all():
            normalized_feature_key = _normalize_feature_key(feature_key)
            usage_rows[(event_tenant_id, normalized_feature_key)] = UsageRowData(
                tenant_id=event_tenant_id,
                usage_date=usage_date,
                feature_key=normalized_feature_key,
                feature_name=(feature_name or normalized_feature_key).strip() or normalized_feature_key,
                usage_value=int(usage_value or 0),
                source="feature_events",
            )

        return usage_rows

    def _build_compliance_rows(
        self,
        session: Session,
        *,
        usage_rows_by_key: dict[tuple[str, str], UsageRowData],
        usage_date: date,
        tenant_id: str | None,
    ) -> dict[tuple[str, str], TenantComplianceRecord]:
        tracked_tenants = sorted({tenant_key for tenant_key, _ in usage_rows_by_key})
        if tenant_id is not None and tenant_id not in tracked_tenants:
            tracked_tenants.append(tenant_id)

        compliance_rows: dict[tuple[str, str], TenantComplianceRecord] = {}

        for tenant_key in tracked_tenants:
            entitlements = self._active_entitlements_for_tenant(
                session,
                tenant_id=tenant_key,
                usage_date=usage_date,
            )
            relevant_feature_keys = {
                feature_key for scoped_tenant_id, feature_key in usage_rows_by_key if scoped_tenant_id == tenant_key
            }
            relevant_feature_keys.update(entitlements.keys())

            for feature_key in sorted(relevant_feature_keys):
                usage = usage_rows_by_key.get((tenant_key, feature_key))
                entitlement = entitlements.get(feature_key)

                usage_value = usage.usage_value if usage is not None else 0
                feature_name = (
                    usage.feature_name
                    if usage is not None
                    else entitlement.feature_name if entitlement is not None else feature_key
                )
                source = usage.source if usage is not None else None
                entitlement_value = entitlement.entitlement_value if entitlement is not None else None
                entitlement_limit = entitlement.entitlement_limit if entitlement is not None else None
                entitlement_is_enabled = entitlement.enabled if entitlement is not None else False
                plan_codes = entitlement.plan_codes if entitlement is not None else []

                if not entitlement_is_enabled:
                    status = "not_entitled" if usage_value > 0 else "no_entitlement"
                    overage_value = usage_value if usage_value > 0 else 0
                elif entitlement_limit is None:
                    status = "within_limit"
                    overage_value = 0
                elif usage_value > entitlement_limit:
                    status = "over_limit"
                    overage_value = usage_value - entitlement_limit
                else:
                    status = "within_limit"
                    overage_value = 0

                compliance_rows[(tenant_key, feature_key)] = TenantComplianceRecord(
                    tenant_id=tenant_key,
                    usage_date=usage_date,
                    feature_key=feature_key,
                    feature_name=feature_name,
                    entitlement_value=entitlement_value,
                    entitlement_limit=entitlement_limit,
                    entitlement_is_enabled=entitlement_is_enabled,
                    usage_value=usage_value,
                    overage_value=overage_value,
                    status=status,
                    source=source,
                    plan_codes_json=json.dumps(plan_codes),
                    evaluated_at=utcnow(),
                )

        return compliance_rows

    def _tracked_tenants(
        self,
        session: Session,
        *,
        usage_date: date,
        tenant_id: str | None,
    ) -> list[str]:
        if tenant_id is not None:
            return [tenant_id]

        day_start, next_day = _utc_day_window(usage_date)
        tenants = {
            tenant_key
            for tenant_key in session.exec(
                select(TenantLicense.tenant_id).where(
                    TenantLicense.deleted_at.is_(None),
                    TenantLicense.starts_at < next_day,
                    or_(TenantLicense.ends_at.is_(None), TenantLicense.ends_at > day_start),
                )
            ).all()
            if tenant_key
        }
        tenants.update(
            tenant_key
            for tenant_key in session.exec(
                select(User.tenant_id).where(
                    User.deleted_at.is_(None),
                    User.tenant_id.is_not(None),
                )
            ).all()
            if tenant_key
        )
        tenants.update(
            tenant_key
            for tenant_key in session.exec(
                select(TenantFeatureUsageEvent.tenant_id).where(
                    TenantFeatureUsageEvent.deleted_at.is_(None),
                    TenantFeatureUsageEvent.occurred_at >= day_start,
                    TenantFeatureUsageEvent.occurred_at < next_day,
                )
            ).all()
            if tenant_key
        )

        return sorted(tenants)

    def _active_seat_counts(self, session: Session, tenant_ids: list[str]) -> dict[str, int]:
        if not tenant_ids:
            return {}

        rows = session.exec(
            select(User.tenant_id, func.count(User.id))
            .where(
                User.deleted_at.is_(None),
                User.status == UserStatus.ACTIVE,
                User.tenant_id.in_(tenant_ids),
            )
            .group_by(User.tenant_id)
        ).all()

        return {tenant_key: int(count or 0) for tenant_key, count in rows if tenant_key}

    def _active_entitlements_for_tenant(
        self,
        session: Session,
        *,
        tenant_id: str,
        usage_date: date,
    ) -> dict[str, EntitlementAggregate]:
        day_start, next_day = _utc_day_window(usage_date)
        rows = session.exec(
            select(Entitlement, LicensePlan)
            .join(LicensePlan, Entitlement.license_plan_id == LicensePlan.id)
            .join(LicenseProduct, LicensePlan.license_product_id == LicenseProduct.id)
            .join(TenantLicense, TenantLicense.license_plan_id == LicensePlan.id)
            .where(
                Entitlement.deleted_at.is_(None),
                LicensePlan.deleted_at.is_(None),
                LicensePlan.is_active == True,  # noqa: E712
                LicenseProduct.deleted_at.is_(None),
                LicenseProduct.is_active == True,  # noqa: E712
                TenantLicense.deleted_at.is_(None),
                TenantLicense.tenant_id == tenant_id,
                TenantLicense.starts_at < next_day,
                or_(TenantLicense.ends_at.is_(None), TenantLicense.ends_at > day_start),
            )
        ).all()

        grouped: dict[str, dict[str, object]] = {}
        for entitlement, plan in rows:
            feature_key = _normalize_feature_key(entitlement.feature_key)
            aggregate = grouped.setdefault(
                feature_key,
                {
                    "feature_name": entitlement.feature_name or feature_key,
                    "enabled": False,
                    "has_unlimited": False,
                    "numeric_limits": [],
                    "plan_codes": set(),
                },
            )

            aggregate["feature_name"] = entitlement.feature_name or aggregate["feature_name"]
            cast_plan_codes = aggregate["plan_codes"]
            if isinstance(cast_plan_codes, set):
                cast_plan_codes.add(plan.code)

            if not entitlement.is_enabled:
                continue

            aggregate["enabled"] = True
            parsed_limit = _parse_numeric_limit(entitlement.grant_value)
            if parsed_limit is None:
                aggregate["has_unlimited"] = True
            else:
                cast_limits = aggregate["numeric_limits"]
                if isinstance(cast_limits, list):
                    cast_limits.append(parsed_limit)

        resolved: dict[str, EntitlementAggregate] = {}
        for feature_key, aggregate in grouped.items():
            enabled = bool(aggregate["enabled"])
            has_unlimited = bool(aggregate["has_unlimited"])
            numeric_limits = aggregate["numeric_limits"] if isinstance(aggregate["numeric_limits"], list) else []
            plan_codes = sorted(aggregate["plan_codes"]) if isinstance(aggregate["plan_codes"], set) else []

            entitlement_limit: int | None = None
            entitlement_value: str | None = None
            if enabled:
                if has_unlimited:
                    entitlement_value = "unlimited"
                else:
                    entitlement_limit = sum(int(limit) for limit in numeric_limits)
                    entitlement_value = str(entitlement_limit)

            resolved[feature_key] = EntitlementAggregate(
                feature_name=str(aggregate["feature_name"]),
                enabled=enabled,
                entitlement_value=entitlement_value,
                entitlement_limit=entitlement_limit,
                plan_codes=plan_codes,
            )

        return resolved

    def _sync_usage_rows(
        self,
        session: Session,
        usage_rows_by_key: dict[tuple[str, str], UsageRowData],
        usage_date: date,
        tenant_id: str | None,
    ) -> None:
        statement = select(TenantUsageDaily).where(
            TenantUsageDaily.deleted_at.is_(None),
            TenantUsageDaily.usage_date == usage_date,
        )
        if tenant_id is not None:
            statement = statement.where(TenantUsageDaily.tenant_id == tenant_id)

        existing_rows = {
            (row.tenant_id, row.feature_key): row
            for row in session.exec(statement).all()
        }

        for usage_key, usage_data in usage_rows_by_key.items():
            existing = existing_rows.pop(usage_key, None)
            if existing is None:
                session.add(
                    TenantUsageDaily(
                        tenant_id=usage_data.tenant_id,
                        usage_date=usage_data.usage_date,
                        feature_key=usage_data.feature_key,
                        feature_name=usage_data.feature_name,
                        usage_value=usage_data.usage_value,
                        source=usage_data.source,
                        last_computed_at=utcnow(),
                    )
                )
                continue

            existing.feature_name = usage_data.feature_name
            existing.usage_value = usage_data.usage_value
            existing.source = usage_data.source
            existing.last_computed_at = utcnow()
            existing.touch()
            session.add(existing)

        for stale_row in existing_rows.values():
            stale_row.soft_delete()
            stale_row.touch()
            session.add(stale_row)

    def _sync_compliance_rows(
        self,
        session: Session,
        compliance_rows_by_key: dict[tuple[str, str], TenantComplianceRecord],
        usage_date: date,
        tenant_id: str | None,
    ) -> None:
        statement = select(TenantComplianceRecord).where(
            TenantComplianceRecord.deleted_at.is_(None),
            TenantComplianceRecord.usage_date == usage_date,
        )
        if tenant_id is not None:
            statement = statement.where(TenantComplianceRecord.tenant_id == tenant_id)

        existing_rows = {
            (row.tenant_id, row.feature_key): row
            for row in session.exec(statement).all()
        }

        for compliance_key, compliance_data in compliance_rows_by_key.items():
            existing = existing_rows.pop(compliance_key, None)
            if existing is None:
                session.add(compliance_data)
                continue

            existing.feature_name = compliance_data.feature_name
            existing.entitlement_value = compliance_data.entitlement_value
            existing.entitlement_limit = compliance_data.entitlement_limit
            existing.entitlement_is_enabled = compliance_data.entitlement_is_enabled
            existing.usage_value = compliance_data.usage_value
            existing.overage_value = compliance_data.overage_value
            existing.status = compliance_data.status
            existing.source = compliance_data.source
            existing.plan_codes_json = compliance_data.plan_codes_json
            existing.evaluated_at = compliance_data.evaluated_at
            existing.touch()
            session.add(existing)

        for stale_row in existing_rows.values():
            stale_row.soft_delete()
            stale_row.touch()
            session.add(stale_row)

    def _latest_usage_date(self, session: Session, tenant_id: str | None) -> date | None:
        statement = select(func.max(TenantComplianceRecord.usage_date)).where(
            TenantComplianceRecord.deleted_at.is_(None)
        )
        if tenant_id is not None:
            statement = statement.where(TenantComplianceRecord.tenant_id == tenant_id)

        return session.exec(statement).one()

    def _deserialize_plan_codes(self, plan_codes_json: str | None) -> list[str]:
        if not plan_codes_json:
            return []
        try:
            parsed = json.loads(plan_codes_json)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [value for value in parsed if isinstance(value, str)]


def get_licensing_compliance_service() -> LicensingComplianceService:
    return LicensingComplianceService()


LicensingComplianceServiceDep = Annotated[
    LicensingComplianceService,
    Depends(get_licensing_compliance_service),
]
