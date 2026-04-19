from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import func, or_
from sqlmodel import Session, select

from app.exceptions.http import BadRequestException, ConflictException, NotFoundException
from app.models import (
    Entitlement,
    EntitlementCreate,
    EntitlementResponse,
    LicenseHistory,
    LicensePlan,
    LicensePlanCreate,
    LicensePlanDetail,
    LicensePlanResponse,
    LicenseProduct,
    LicenseProductCatalog,
    LicenseProductCreate,
    LicenseProductResponse,
    TenantLicense,
    TenantLicenseAssign,
    TenantLicenseDetail,
    TenantLicenseResponse,
    TenantLicenseUnassign,
)
from app.utils.enums import LicenseHistoryAction
from app.utils.funcs import utcnow


FAR_FUTURE = datetime(9999, 12, 31, tzinfo=timezone.utc)


def _normalize_required(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise BadRequestException(f"{field_name} is required")
    return normalized


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_feature_key(feature_key: str) -> str:
    return _normalize_required(feature_key, "feature_key").lower()


def _normalize_tenant_id(tenant_id: str) -> str:
    return _normalize_required(tenant_id, "tenant_id")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def tenant_has_entitlement(
    session: Session,
    tenant_id: str,
    feature_key: str,
    *,
    at: datetime | None = None,
) -> bool:
    lookup_at = _as_utc(at or utcnow())
    normalized_tenant_id = _normalize_tenant_id(tenant_id)
    normalized_feature_key = _normalize_feature_key(feature_key)

    statement = (
        select(Entitlement.id)
        .join(LicensePlan, Entitlement.license_plan_id == LicensePlan.id)
        .join(LicenseProduct, LicensePlan.license_product_id == LicenseProduct.id)
        .join(TenantLicense, TenantLicense.license_plan_id == LicensePlan.id)
        .where(
            Entitlement.deleted_at.is_(None),
            Entitlement.feature_key == normalized_feature_key,
            Entitlement.is_enabled == True,  # noqa: E712
            LicensePlan.deleted_at.is_(None),
            LicensePlan.is_active == True,  # noqa: E712
            LicenseProduct.deleted_at.is_(None),
            LicenseProduct.is_active == True,  # noqa: E712
            TenantLicense.deleted_at.is_(None),
            TenantLicense.tenant_id == normalized_tenant_id,
            TenantLicense.starts_at <= lookup_at,
            or_(TenantLicense.ends_at.is_(None), TenantLicense.ends_at > lookup_at),
        )
        .limit(1)
    )
    return session.exec(statement).first() is not None


class LicensingService:
    """Admin catalog + tenant license assignment service."""

    def create_product(
        self,
        payload: LicenseProductCreate,
        session: Session,
    ) -> LicenseProductResponse:
        sku = _normalize_required(payload.sku, "sku")
        name = _normalize_required(payload.name, "name")

        existing = session.exec(
            select(LicenseProduct).where(
                func.lower(LicenseProduct.sku) == sku.lower(),
                LicenseProduct.deleted_at.is_(None),
            )
        ).first()
        if existing:
            raise ConflictException(f"License product with sku '{existing.sku}' already exists")

        product = LicenseProduct(
            sku=sku,
            name=name,
            description=_normalize_optional(payload.description),
            is_active=payload.is_active,
        )
        session.add(product)
        session.commit()
        session.refresh(product)
        return LicenseProductResponse.model_validate(product)

    def list_products(
        self,
        session: Session,
        include_inactive: bool = False,
    ) -> list[LicenseProductCatalog]:
        product_statement = select(LicenseProduct).where(LicenseProduct.deleted_at.is_(None))
        if not include_inactive:
            product_statement = product_statement.where(LicenseProduct.is_active == True)  # noqa: E712
        products = list(session.exec(product_statement.order_by(LicenseProduct.name)).all())
        if not products:
            return []

        product_ids = [product.id for product in products]
        plan_statement = select(LicensePlan).where(
            LicensePlan.deleted_at.is_(None),
            LicensePlan.license_product_id.in_(product_ids),
        )
        if not include_inactive:
            plan_statement = plan_statement.where(LicensePlan.is_active == True)  # noqa: E712
        plans = list(
            session.exec(
                plan_statement.order_by(LicensePlan.license_product_id, LicensePlan.name)
            ).all()
        )

        plan_ids = [plan.id for plan in plans]
        entitlements_by_plan: dict[UUID, list[EntitlementResponse]] = {}
        if plan_ids:
            entitlements = list(
                session.exec(
                    select(Entitlement)
                    .where(
                        Entitlement.deleted_at.is_(None),
                        Entitlement.license_plan_id.in_(plan_ids),
                    )
                    .order_by(Entitlement.license_plan_id, Entitlement.feature_key)
                ).all()
            )
            for entitlement in entitlements:
                entitlements_by_plan.setdefault(entitlement.license_plan_id, []).append(
                    EntitlementResponse.model_validate(entitlement)
                )

        plans_by_product: dict[UUID, list[LicensePlanDetail]] = {}
        for plan in plans:
            plan_response = LicensePlanResponse.model_validate(plan)
            plans_by_product.setdefault(plan.license_product_id, []).append(
                LicensePlanDetail(
                    **plan_response.model_dump(),
                    entitlements=entitlements_by_plan.get(plan.id, []),
                )
            )

        catalog: list[LicenseProductCatalog] = []
        for product in products:
            product_response = LicenseProductResponse.model_validate(product)
            catalog.append(
                LicenseProductCatalog(
                    **product_response.model_dump(),
                    plans=plans_by_product.get(product.id, []),
                )
            )
        return catalog

    def create_plan(
        self,
        payload: LicensePlanCreate,
        session: Session,
    ) -> LicensePlanResponse:
        product = self._get_product(payload.license_product_id, session)
        code = _normalize_required(payload.code, "code")
        name = _normalize_required(payload.name, "name")

        existing = session.exec(
            select(LicensePlan).where(
                LicensePlan.license_product_id == product.id,
                func.lower(LicensePlan.code) == code.lower(),
                LicensePlan.deleted_at.is_(None),
            )
        ).first()
        if existing:
            raise ConflictException(
                f"License plan with code '{existing.code}' already exists for product '{product.sku}'"
            )

        plan = LicensePlan(
            license_product_id=product.id,
            code=code,
            name=name,
            description=_normalize_optional(payload.description),
            is_active=payload.is_active,
        )
        session.add(plan)
        session.commit()
        session.refresh(plan)
        return LicensePlanResponse.model_validate(plan)

    def list_plans(
        self,
        session: Session,
        license_product_id: UUID | None = None,
        include_inactive: bool = False,
    ) -> list[LicensePlanDetail]:
        statement = select(LicensePlan).where(LicensePlan.deleted_at.is_(None))
        if license_product_id is not None:
            statement = statement.where(LicensePlan.license_product_id == license_product_id)
        if not include_inactive:
            statement = statement.where(LicensePlan.is_active == True)  # noqa: E712
        plans = list(session.exec(statement.order_by(LicensePlan.name)).all())
        if not plans:
            return []

        plan_ids = [plan.id for plan in plans]
        entitlements = list(
            session.exec(
                select(Entitlement)
                .where(
                    Entitlement.deleted_at.is_(None),
                    Entitlement.license_plan_id.in_(plan_ids),
                )
                .order_by(Entitlement.license_plan_id, Entitlement.feature_key)
            ).all()
        )

        entitlements_by_plan: dict[UUID, list[EntitlementResponse]] = {}
        for entitlement in entitlements:
            entitlements_by_plan.setdefault(entitlement.license_plan_id, []).append(
                EntitlementResponse.model_validate(entitlement)
            )

        details: list[LicensePlanDetail] = []
        for plan in plans:
            plan_response = LicensePlanResponse.model_validate(plan)
            details.append(
                LicensePlanDetail(
                    **plan_response.model_dump(),
                    entitlements=entitlements_by_plan.get(plan.id, []),
                )
            )
        return details

    def create_entitlement(
        self,
        payload: EntitlementCreate,
        session: Session,
    ) -> EntitlementResponse:
        self._get_plan(payload.license_plan_id, session)
        feature_key = _normalize_feature_key(payload.feature_key)
        feature_name = _normalize_required(payload.feature_name, "feature_name")

        existing = session.exec(
            select(Entitlement).where(
                Entitlement.license_plan_id == payload.license_plan_id,
                func.lower(Entitlement.feature_key) == feature_key.lower(),
                Entitlement.deleted_at.is_(None),
            )
        ).first()
        if existing:
            raise ConflictException(
                f"Entitlement '{existing.feature_key}' already exists for this plan"
            )

        entitlement = Entitlement(
            license_plan_id=payload.license_plan_id,
            feature_key=feature_key,
            feature_name=feature_name,
            description=_normalize_optional(payload.description),
            grant_value=_normalize_optional(payload.grant_value),
            is_enabled=payload.is_enabled,
        )
        session.add(entitlement)
        session.commit()
        session.refresh(entitlement)
        return EntitlementResponse.model_validate(entitlement)

    def list_entitlements(
        self,
        session: Session,
        license_plan_id: UUID | None = None,
    ) -> list[EntitlementResponse]:
        statement = select(Entitlement).where(Entitlement.deleted_at.is_(None))
        if license_plan_id is not None:
            statement = statement.where(Entitlement.license_plan_id == license_plan_id)
        entitlements = list(session.exec(statement.order_by(Entitlement.feature_key)).all())
        return [EntitlementResponse.model_validate(entitlement) for entitlement in entitlements]

    def assign_tenant_license(
        self,
        payload: TenantLicenseAssign,
        session: Session,
        *,
        actor_user_id: UUID | None,
    ) -> TenantLicenseDetail:
        tenant_id = _normalize_tenant_id(payload.tenant_id)
        starts_at = _as_utc(payload.starts_at or utcnow())
        ends_at = _as_utc(payload.ends_at) if payload.ends_at is not None else None
        if ends_at is not None and ends_at <= starts_at:
            raise BadRequestException("ends_at must be after starts_at")

        plan = self._get_plan(payload.license_plan_id, session)
        product = self._get_product(plan.license_product_id, session)
        if not plan.is_active or not product.is_active:
            raise BadRequestException("Cannot assign inactive license catalog entries")

        overlapping = session.exec(
            select(TenantLicense).where(
                TenantLicense.deleted_at.is_(None),
                TenantLicense.tenant_id == tenant_id,
                TenantLicense.license_plan_id == plan.id,
                TenantLicense.starts_at < (ends_at or FAR_FUTURE),
                func.coalesce(TenantLicense.ends_at, FAR_FUTURE) > starts_at,
            )
        ).first()
        if overlapping:
            raise ConflictException(
                "Overlapping tenant license assignment already exists for this tenant and plan"
            )

        tenant_license = TenantLicense(
            tenant_id=tenant_id,
            license_plan_id=plan.id,
            starts_at=starts_at,
            ends_at=ends_at,
            assigned_by_user_id=actor_user_id,
        )
        session.add(tenant_license)
        session.flush()

        self._record_history(
            session,
            tenant_license=tenant_license,
            action=LicenseHistoryAction.ASSIGNED,
            actor_user_id=actor_user_id,
            effective_at=starts_at,
            note=payload.note,
        )

        session.commit()
        session.refresh(tenant_license)
        return self._serialize_tenant_license(tenant_license, plan, product)

    def list_tenant_licenses(
        self,
        session: Session,
        *,
        tenant_id: str | None = None,
        active_only: bool = False,
    ) -> list[TenantLicenseDetail]:
        statement = (
            select(TenantLicense, LicensePlan, LicenseProduct)
            .join(LicensePlan, TenantLicense.license_plan_id == LicensePlan.id)
            .join(LicenseProduct, LicensePlan.license_product_id == LicenseProduct.id)
            .where(
                TenantLicense.deleted_at.is_(None),
                LicensePlan.deleted_at.is_(None),
                LicenseProduct.deleted_at.is_(None),
            )
        )

        if tenant_id is not None:
            statement = statement.where(TenantLicense.tenant_id == _normalize_tenant_id(tenant_id))

        if active_only:
            now = utcnow()
            statement = statement.where(
                TenantLicense.starts_at <= now,
                or_(TenantLicense.ends_at.is_(None), TenantLicense.ends_at > now),
            )

        rows = session.exec(
            statement.order_by(
                TenantLicense.tenant_id,
                LicenseProduct.sku,
                LicensePlan.code,
                TenantLicense.starts_at.desc(),
            )
        ).all()

        return [
            self._serialize_tenant_license(tenant_license, plan, product)
            for tenant_license, plan, product in rows
        ]

    def unassign_tenant_license(
        self,
        tenant_license_id: UUID,
        payload: TenantLicenseUnassign,
        session: Session,
        *,
        actor_user_id: UUID | None,
    ) -> TenantLicenseDetail:
        tenant_license = self._get_tenant_license(tenant_license_id, session)
        plan = self._get_plan(tenant_license.license_plan_id, session)
        product = self._get_product(plan.license_product_id, session)

        effective_end = _as_utc(payload.ends_at or utcnow())
        starts_at = _as_utc(tenant_license.starts_at)
        current_end = (
            _as_utc(tenant_license.ends_at)
            if tenant_license.ends_at is not None
            else None
        )
        if effective_end <= starts_at:
            raise BadRequestException("ends_at must be after starts_at")

        now = _as_utc(utcnow())
        if current_end is not None:
            if current_end <= now:
                raise ConflictException("Tenant license already unassigned")
            if effective_end >= current_end:
                raise ConflictException("Tenant license already ends before requested time")

        tenant_license.ends_at = effective_end
        tenant_license.unassigned_by_user_id = actor_user_id
        tenant_license.touch()
        session.add(tenant_license)

        self._record_history(
            session,
            tenant_license=tenant_license,
            action=LicenseHistoryAction.UNASSIGNED,
            actor_user_id=actor_user_id,
            effective_at=effective_end,
            note=payload.note,
        )

        session.commit()
        session.refresh(tenant_license)
        return self._serialize_tenant_license(tenant_license, plan, product)

    def _get_product(self, product_id: UUID, session: Session) -> LicenseProduct:
        product = session.exec(
            select(LicenseProduct).where(
                LicenseProduct.id == product_id,
                LicenseProduct.deleted_at.is_(None),
            )
        ).first()
        if not product:
            raise NotFoundException(f"License product with ID {product_id} not found")
        return product

    def _get_plan(self, plan_id: UUID, session: Session) -> LicensePlan:
        plan = session.exec(
            select(LicensePlan).where(
                LicensePlan.id == plan_id,
                LicensePlan.deleted_at.is_(None),
            )
        ).first()
        if not plan:
            raise NotFoundException(f"License plan with ID {plan_id} not found")
        return plan

    def _get_tenant_license(self, tenant_license_id: UUID, session: Session) -> TenantLicense:
        tenant_license = session.exec(
            select(TenantLicense).where(
                TenantLicense.id == tenant_license_id,
                TenantLicense.deleted_at.is_(None),
            )
        ).first()
        if not tenant_license:
            raise NotFoundException(f"Tenant license with ID {tenant_license_id} not found")
        return tenant_license

    def _record_history(
        self,
        session: Session,
        *,
        tenant_license: TenantLicense,
        action: LicenseHistoryAction,
        actor_user_id: UUID | None,
        effective_at: datetime,
        note: str | None,
    ) -> None:
        history = LicenseHistory(
            tenant_license_id=tenant_license.id,
            tenant_id=tenant_license.tenant_id,
            license_plan_id=tenant_license.license_plan_id,
            action=action,
            actor_user_id=actor_user_id,
            effective_at=effective_at,
            note=_normalize_optional(note),
        )
        session.add(history)

    def _serialize_tenant_license(
        self,
        tenant_license: TenantLicense,
        plan: LicensePlan,
        product: LicenseProduct,
    ) -> TenantLicenseDetail:
        response = TenantLicenseResponse.model_validate(tenant_license)
        return TenantLicenseDetail(
            **response.model_dump(),
            license_product_id=product.id,
            product_sku=product.sku,
            product_name=product.name,
            plan_code=plan.code,
            plan_name=plan.name,
        )


def get_licensing_service() -> LicensingService:
    return LicensingService()


LicensingServiceDep = Annotated[LicensingService, Depends(get_licensing_service)]
