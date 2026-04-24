#!/usr/bin/env python
"""Dry-run, apply, and verify tenant-scope backfills.

This script is intentionally conservative:
- dry-run and verify never mutate data
- apply is explicit and writes one tenant_operation_logs row
- system settings are copied into tenant scope, not moved out of global scope
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine


PLATFORM_ROLES = ("admin", "super_admin")
DEFAULT_TENANT_ID = "samo-telecoms"
DEFAULT_TENANT_NAME = "Samo Telecoms"
DEFAULT_LICENSE_PRODUCT_SKU = "FIELDCORE"
DEFAULT_LICENSE_PRODUCT_NAME = "FieldCore"
DEFAULT_LICENSE_PLAN_CODE = "OPS-PRO"
DEFAULT_LICENSE_PLAN_NAME = "FieldCore OPS Pro"


@dataclass(frozen=True)
class TenantBackfillOptions:
    phase: str
    tenant_id: str
    tenant_name: str
    tenant_slug: str
    user_email_domain: str | None
    include_admins: bool
    skip_users: bool
    skip_webhooks: bool
    skip_settings: bool
    seed_full_access_license: bool
    platform_owner_emails: tuple[str, ...]
    strict: bool


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=json_default))


def resolve_database_url(raw_url: str | None) -> str:
    if raw_url:
        return raw_url
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]

    required = ("DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_NAME")
    values = {name: os.getenv(name) for name in required}
    if all(values.values()):
        return (
            f"postgresql+psycopg2://{values['DB_USER']}:{values['DB_PASSWORD']}"
            f"@{values['DB_HOST']}:{values['DB_PORT']}/{values['DB_NAME']}"
        )

    raise SystemExit(
        "Database URL missing. Pass --database-url, set DATABASE_URL, or set DB_USER/DB_PASSWORD/DB_HOST/DB_PORT/DB_NAME."
    )


def normalize_identifier(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise SystemExit(f"{field_name} is required")
    return normalized


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def has_table(connection: Connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def scalar(connection: Connection, sql: str, params: dict[str, Any] | None = None) -> int:
    value = connection.execute(text(sql), params or {}).scalar()
    return int(value or 0)


def users_where_clause(options: TenantBackfillOptions) -> tuple[str, dict[str, Any]]:
    conditions = ["tenant_id IS NULL", "deleted_at IS NULL"]
    params: dict[str, Any] = {}

    if not options.include_admins:
        conditions.append("LOWER(role::text) NOT IN ('admin', 'super_admin')")
    else:
        conditions.append("LOWER(role::text) != 'super_admin'")

    if options.platform_owner_emails:
        placeholders: list[str] = []
        for index, email in enumerate(options.platform_owner_emails):
            param_name = f"platform_owner_email_{index}"
            placeholders.append(f":{param_name}")
            params[param_name] = email.lower()
        conditions.append(f"LOWER(email) NOT IN ({', '.join(placeholders)})")

    if options.user_email_domain:
        conditions.append("LOWER(email) LIKE :email_domain")
        params["email_domain"] = f"%@{options.user_email_domain.lower().lstrip('@')}"

    return " AND ".join(conditions), params


def ensure_schema(connection: Connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                id          VARCHAR(128) PRIMARY KEY,
                slug        VARCHAR(128) NOT NULL UNIQUE,
                name        VARCHAR(160) NOT NULL,
                status      VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE',
                archived_at TIMESTAMPTZ,
                created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                deleted_at  TIMESTAMPTZ
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tenant_operation_logs (
                id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id     VARCHAR(128) NOT NULL,
                operation     VARCHAR(32)  NOT NULL,
                dry_run       BOOLEAN      NOT NULL DEFAULT TRUE,
                actor_user_id UUID,
                status        VARCHAR(32)  NOT NULL DEFAULT 'completed',
                message       TEXT,
                details       JSONB        NOT NULL DEFAULT '{}'::jsonb,
                created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
            )
            """
        )
    )

    for table_name in ("users", "webhooks", "system_settings"):
        if has_table(connection, table_name):
            connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(128)"))

    if has_table(connection, "users"):
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_users_tenant_id
                ON users(tenant_id)
                WHERE deleted_at IS NULL
                """
            )
        )
    if has_table(connection, "webhooks"):
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_webhooks_tenant_event_active
                ON webhooks(tenant_id, event_type, is_active)
                """
            )
        )
    if has_table(connection, "system_settings"):
        connection.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_system_settings_tenant_key
                ON system_settings(tenant_id, key)
                WHERE tenant_id IS NOT NULL
                """
            )
        )


def ensure_licensing_schema(connection: Connection) -> None:
    connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS license_products (
                id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                deleted_at  TIMESTAMPTZ,
                sku         VARCHAR(64) NOT NULL,
                name        VARCHAR(120) NOT NULL,
                description TEXT,
                is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
                CONSTRAINT uq_license_products_sku UNIQUE (sku)
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS license_plans (
                id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                deleted_at         TIMESTAMPTZ,
                license_product_id UUID        NOT NULL REFERENCES license_products(id) ON DELETE CASCADE,
                code               VARCHAR(64) NOT NULL,
                name               VARCHAR(120) NOT NULL,
                description        TEXT,
                is_active          BOOLEAN     NOT NULL DEFAULT TRUE,
                CONSTRAINT uq_license_plans_product_code UNIQUE (license_product_id, code)
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS entitlements (
                id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
                created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                deleted_at      TIMESTAMPTZ,
                license_plan_id UUID         NOT NULL REFERENCES license_plans(id) ON DELETE CASCADE,
                feature_key     VARCHAR(120) NOT NULL,
                feature_name    VARCHAR(120) NOT NULL,
                description     TEXT,
                grant_value     VARCHAR(120),
                is_enabled      BOOLEAN      NOT NULL DEFAULT TRUE,
                CONSTRAINT uq_entitlements_plan_feature UNIQUE (license_plan_id, feature_key)
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tenant_licenses (
                id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                deleted_at            TIMESTAMPTZ,
                tenant_id             TEXT        NOT NULL,
                license_plan_id       UUID        NOT NULL REFERENCES license_plans(id) ON DELETE CASCADE,
                starts_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ends_at               TIMESTAMPTZ,
                assigned_by_user_id   UUID,
                unassigned_by_user_id UUID
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS license_history (
                id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                deleted_at        TIMESTAMPTZ,
                tenant_license_id UUID        NOT NULL REFERENCES tenant_licenses(id) ON DELETE CASCADE,
                tenant_id         TEXT        NOT NULL,
                license_plan_id   UUID        NOT NULL REFERENCES license_plans(id) ON DELETE CASCADE,
                action            VARCHAR(32) NOT NULL,
                actor_user_id     UUID,
                effective_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                note              TEXT
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_tenant_licenses_lookup
            ON tenant_licenses(tenant_id, license_plan_id, starts_at)
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_license_history_tenant_effective_at
            ON license_history(tenant_id, effective_at DESC)
            """
        )
    )


def schema_status(connection: Connection) -> dict[str, Any]:
    required = ("tenants", "tenant_operation_logs", "users", "webhooks", "system_settings")
    present = {table: has_table(connection, table) for table in required}
    columns: dict[str, list[str]] = {}
    inspector = inspect(connection)
    for table, exists in present.items():
        if exists:
            columns[table] = [column["name"] for column in inspector.get_columns(table)]

    missing_tenant_id_columns = [
        table
        for table in ("users", "webhooks", "system_settings")
        if present.get(table) and "tenant_id" not in columns.get(table, [])
    ]
    return {
        "tables_present": present,
        "missing_tenant_id_columns": missing_tenant_id_columns,
        "migration_required": any(not present[table] for table in ("tenants", "tenant_operation_logs"))
        or bool(missing_tenant_id_columns),
    }


def build_plan(connection: Connection, options: TenantBackfillOptions) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "phase": options.phase,
        "tenant_id": options.tenant_id,
        "tenant_slug": options.tenant_slug,
        "generated_at": utc_timestamp(),
        "schema": schema_status(connection),
        "counts": {},
        "filters": {
            "user_email_domain": options.user_email_domain,
            "include_admins": options.include_admins,
            "skip_users": options.skip_users,
            "skip_webhooks": options.skip_webhooks,
            "skip_settings": options.skip_settings,
            "seed_full_access_license": options.seed_full_access_license,
            "platform_owner_emails": list(options.platform_owner_emails),
        },
    }

    if has_table(connection, "tenants"):
        plan["counts"]["tenant_exists"] = scalar(
            connection,
            "SELECT COUNT(*) FROM tenants WHERE id = :tenant_id AND deleted_at IS NULL",
            {"tenant_id": options.tenant_id},
        )

    if has_table(connection, "users"):
        where_sql, params = users_where_clause(options)
        plan["counts"]["users_to_backfill"] = 0 if options.skip_users else scalar(
            connection,
            f"SELECT COUNT(*) FROM users WHERE {where_sql}",
            params,
        )
        plan["counts"]["unscoped_active_users_non_platform"] = scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM users
            WHERE tenant_id IS NULL
              AND deleted_at IS NULL
              AND LOWER(role::text) NOT IN ('admin', 'super_admin')
            """,
        )

    if has_table(connection, "webhooks"):
        plan["counts"]["webhooks_to_backfill"] = 0 if options.skip_webhooks else scalar(
            connection,
            "SELECT COUNT(*) FROM webhooks WHERE tenant_id IS NULL",
        )

    if has_table(connection, "system_settings"):
        plan["counts"]["tenant_settings_to_copy"] = 0 if options.skip_settings else scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM system_settings src
            WHERE src.tenant_id IS NULL
              AND NOT EXISTS (
                SELECT 1
                FROM system_settings dst
                WHERE dst.tenant_id = :tenant_id
                  AND dst.key = src.key
              )
            """,
            {"tenant_id": options.tenant_id},
        )

    if has_table(connection, "users") and options.platform_owner_emails:
        owner_placeholders: list[str] = []
        owner_params: dict[str, Any] = {}
        for index, email in enumerate(options.platform_owner_emails):
            param_name = f"owner_email_{index}"
            owner_placeholders.append(f":{param_name}")
            owner_params[param_name] = email.lower()
        plan["counts"]["platform_owner_users_found"] = scalar(
            connection,
            f"""
            SELECT COUNT(*)
            FROM users
            WHERE deleted_at IS NULL
              AND LOWER(email) IN ({', '.join(owner_placeholders)})
            """,
            owner_params,
        )

    if options.seed_full_access_license and all(
        has_table(connection, table_name)
        for table_name in ("tenant_licenses", "license_plans", "license_products")
    ):
        plan["counts"]["active_full_access_license_exists"] = scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM tenant_licenses tl
            JOIN license_plans lp ON lp.id = tl.license_plan_id
            JOIN license_products lprod ON lprod.id = lp.license_product_id
            WHERE tl.tenant_id = :tenant_id
              AND tl.deleted_at IS NULL
              AND tl.starts_at <= NOW()
              AND (tl.ends_at IS NULL OR tl.ends_at > NOW())
              AND lprod.sku = :product_sku
              AND lp.code = :plan_code
            """,
            {
                "tenant_id": options.tenant_id,
                "product_sku": DEFAULT_LICENSE_PRODUCT_SKU,
                "plan_code": DEFAULT_LICENSE_PLAN_CODE,
            },
        )

    return plan


def upsert_tenant(connection: Connection, options: TenantBackfillOptions) -> None:
    connection.execute(
        text(
            """
            INSERT INTO tenants (id, slug, name, status, created_at, updated_at)
            VALUES (:tenant_id, :tenant_slug, :tenant_name, 'ACTIVE', NOW(), NOW())
            ON CONFLICT (id) DO UPDATE
            SET slug = EXCLUDED.slug,
                name = EXCLUDED.name,
                updated_at = NOW(),
                deleted_at = NULL
            """
        ),
        {
            "tenant_id": options.tenant_id,
            "tenant_slug": options.tenant_slug,
            "tenant_name": options.tenant_name,
        },
    )


def promote_platform_owners(connection: Connection, options: TenantBackfillOptions) -> int:
    if not options.platform_owner_emails or not has_table(connection, "users"):
        return 0

    placeholders: list[str] = []
    params: dict[str, Any] = {}
    for index, email in enumerate(options.platform_owner_emails):
        param_name = f"owner_email_{index}"
        placeholders.append(f":{param_name}")
        params[param_name] = email.lower()

    result = connection.execute(
        text(
            f"""
            UPDATE users
            SET role = 'super_admin',
                tenant_id = NULL,
                updated_at = NOW()
            WHERE deleted_at IS NULL
              AND LOWER(email) IN ({', '.join(placeholders)})
            """
        ),
        params,
    )
    return int(result.rowcount or 0)


def seed_full_access_license(connection: Connection, options: TenantBackfillOptions) -> dict[str, Any]:
    ensure_licensing_schema(connection)

    product_id = connection.execute(
        text(
            """
            INSERT INTO license_products (sku, name, description, is_active, created_at, updated_at)
            VALUES (:sku, :name, :description, TRUE, NOW(), NOW())
            ON CONFLICT (sku) DO UPDATE
            SET name = EXCLUDED.name,
                description = EXCLUDED.description,
                is_active = TRUE,
                deleted_at = NULL,
                updated_at = NOW()
            RETURNING id
            """
        ),
        {
            "sku": DEFAULT_LICENSE_PRODUCT_SKU,
            "name": DEFAULT_LICENSE_PRODUCT_NAME,
            "description": "FieldCore licensing catalog for tenant access control.",
        },
    ).scalar_one()

    plan_id = connection.execute(
        text(
            """
            INSERT INTO license_plans (
                license_product_id, code, name, description, is_active, created_at, updated_at
            )
            VALUES (:product_id, :code, :name, :description, TRUE, NOW(), NOW())
            ON CONFLICT (license_product_id, code) DO UPDATE
            SET name = EXCLUDED.name,
                description = EXCLUDED.description,
                is_active = TRUE,
                deleted_at = NULL,
                updated_at = NOW()
            RETURNING id
            """
        ),
        {
            "product_id": product_id,
            "code": DEFAULT_LICENSE_PLAN_CODE,
            "name": DEFAULT_LICENSE_PLAN_NAME,
            "description": "Full FieldCore operations access while tier limits are being formalized.",
        },
    ).scalar_one()

    connection.execute(
        text(
            """
            INSERT INTO entitlements (
                license_plan_id, feature_key, feature_name, description, grant_value,
                is_enabled, created_at, updated_at
            )
            VALUES (
                :plan_id, '*', 'Full platform access',
                'Unlock all current FieldCore screens for the tenant.', 'full',
                TRUE, NOW(), NOW()
            )
            ON CONFLICT (license_plan_id, feature_key) DO UPDATE
            SET feature_name = EXCLUDED.feature_name,
                description = EXCLUDED.description,
                grant_value = EXCLUDED.grant_value,
                is_enabled = TRUE,
                deleted_at = NULL,
                updated_at = NOW()
            """
        ),
        {"plan_id": plan_id},
    )

    existing_license_id = connection.execute(
        text(
            """
            SELECT id
            FROM tenant_licenses
            WHERE tenant_id = :tenant_id
              AND license_plan_id = :plan_id
              AND deleted_at IS NULL
              AND starts_at <= NOW()
              AND (ends_at IS NULL OR ends_at > NOW())
            ORDER BY starts_at DESC
            LIMIT 1
            """
        ),
        {"tenant_id": options.tenant_id, "plan_id": plan_id},
    ).scalar()

    created_assignment = False
    tenant_license_id = existing_license_id
    if tenant_license_id is None:
        tenant_license_id = connection.execute(
            text(
                """
                INSERT INTO tenant_licenses (
                    tenant_id, license_plan_id, starts_at, created_at, updated_at
                )
                VALUES (:tenant_id, :plan_id, NOW(), NOW(), NOW())
                RETURNING id
                """
            ),
            {"tenant_id": options.tenant_id, "plan_id": plan_id},
        ).scalar_one()
        created_assignment = True

        connection.execute(
            text(
                """
                INSERT INTO license_history (
                    tenant_license_id, tenant_id, license_plan_id, action,
                    effective_at, note, created_at, updated_at
                )
                VALUES (
                    :tenant_license_id, :tenant_id, :plan_id, 'assigned',
                    NOW(), :note, NOW(), NOW()
                )
                """
            ),
            {
                "tenant_license_id": tenant_license_id,
                "tenant_id": options.tenant_id,
                "plan_id": plan_id,
                "note": "Seeded by tenant scope rollout script.",
            },
        )

    return {
        "product_id": str(product_id),
        "plan_id": str(plan_id),
        "tenant_license_id": str(tenant_license_id),
        "tenant_license_created": created_assignment,
        "entitlements_upserted": 1,
    }


def apply_backfill(connection: Connection, options: TenantBackfillOptions, plan: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(connection)
    upsert_tenant(connection, options)
    applied: dict[str, int] = {"tenants_upserted": 1}

    promoted_platform_owners = promote_platform_owners(connection, options)
    if promoted_platform_owners:
        applied["platform_owners_promoted"] = promoted_platform_owners

    if has_table(connection, "users") and not options.skip_users:
        where_sql, params = users_where_clause(options)
        result = connection.execute(
            text(f"UPDATE users SET tenant_id = :tenant_id WHERE {where_sql}"),
            {"tenant_id": options.tenant_id, **params},
        )
        applied["users_backfilled"] = int(result.rowcount or 0)

    if has_table(connection, "webhooks") and not options.skip_webhooks:
        result = connection.execute(
            text("UPDATE webhooks SET tenant_id = :tenant_id WHERE tenant_id IS NULL"),
            {"tenant_id": options.tenant_id},
        )
        applied["webhooks_backfilled"] = int(result.rowcount or 0)

    if has_table(connection, "system_settings") and not options.skip_settings:
        result = connection.execute(
            text(
                """
                INSERT INTO system_settings (
                    id, key, tenant_id, value, description, category, created_at, updated_at
                )
                SELECT gen_random_uuid(), src.key, :tenant_id, src.value, src.description,
                       src.category, NOW(), NOW()
                FROM system_settings src
                WHERE src.tenant_id IS NULL
                  AND NOT EXISTS (
                    SELECT 1
                    FROM system_settings dst
                    WHERE dst.tenant_id = :tenant_id
                      AND dst.key = src.key
                  )
                """
            ),
            {"tenant_id": options.tenant_id},
        )
        applied["tenant_settings_copied"] = int(result.rowcount or 0)

    license_seed: dict[str, Any] | None = None
    if options.seed_full_access_license:
        license_seed = seed_full_access_license(connection, options)
        applied["full_access_license_seeded"] = 1

    connection.execute(
        text(
            """
            INSERT INTO tenant_operation_logs
                (tenant_id, operation, dry_run, status, message, details)
            VALUES
                (:tenant_id, 'tenant_scope_backfill', FALSE, 'completed', :message, CAST(:details AS JSONB))
            """
        ),
        {
            "tenant_id": options.tenant_id,
            "message": "Tenant scope backfill applied by scripts/backfill_tenant_scope.py",
            "details": json.dumps(
                {"plan": plan, "applied": applied, "license_seed": license_seed},
                default=json_default,
            ),
        },
    )
    return applied


def verify(connection: Connection, options: TenantBackfillOptions) -> dict[str, Any]:
    findings: dict[str, Any] = {
        "tenant_exists": False,
        "orphan_tenant_refs": {},
        "unscoped_counts": {},
        "tenant_row_counts": {},
    }

    if has_table(connection, "tenants"):
        findings["tenant_exists"] = bool(
            scalar(
                connection,
                "SELECT COUNT(*) FROM tenants WHERE id = :tenant_id AND deleted_at IS NULL",
                {"tenant_id": options.tenant_id},
            )
        )

    for table_name in ("users", "webhooks", "system_settings"):
        if not has_table(connection, table_name) or not has_table(connection, "tenants"):
            continue
        findings["orphan_tenant_refs"][table_name] = scalar(
            connection,
            f"""
            SELECT COUNT(*)
            FROM {table_name} scoped
            LEFT JOIN tenants t ON t.id = scoped.tenant_id
            WHERE scoped.tenant_id IS NOT NULL
              AND t.id IS NULL
            """,
        )

    if has_table(connection, "users"):
        where_sql, params = users_where_clause(options)
        findings["unscoped_counts"]["matching_users"] = scalar(
            connection,
            f"SELECT COUNT(*) FROM users WHERE {where_sql}",
            params,
        )
        findings["unscoped_counts"]["active_non_platform_users"] = scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM users
            WHERE tenant_id IS NULL
              AND deleted_at IS NULL
              AND LOWER(role::text) NOT IN ('admin', 'super_admin')
            """,
        )

    if options.platform_owner_emails and has_table(connection, "users"):
        placeholders: list[str] = []
        params: dict[str, Any] = {}
        for index, email in enumerate(options.platform_owner_emails):
            param_name = f"owner_email_{index}"
            placeholders.append(f":{param_name}")
            params[param_name] = email.lower()
        findings["platform_owner_status"] = {
            "super_admin_tenantless": scalar(
                connection,
                f"""
                SELECT COUNT(*)
                FROM users
                WHERE deleted_at IS NULL
                  AND LOWER(email) IN ({', '.join(placeholders)})
                  AND LOWER(role::text) = 'super_admin'
                  AND tenant_id IS NULL
                """,
                params,
            )
        }

    if options.seed_full_access_license and all(
        has_table(connection, table_name)
        for table_name in ("tenant_licenses", "license_plans", "license_products", "entitlements")
    ):
        findings["license"] = {
            "active_full_access_license": scalar(
                connection,
                """
                SELECT COUNT(*)
                FROM tenant_licenses tl
                JOIN license_plans lp ON lp.id = tl.license_plan_id
                JOIN license_products lprod ON lprod.id = lp.license_product_id
                JOIN entitlements e ON e.license_plan_id = lp.id
                WHERE tl.tenant_id = :tenant_id
                  AND tl.deleted_at IS NULL
                  AND tl.starts_at <= NOW()
                  AND (tl.ends_at IS NULL OR tl.ends_at > NOW())
                  AND lprod.sku = :product_sku
                  AND lp.code = :plan_code
                  AND e.feature_key = '*'
                  AND e.is_enabled IS TRUE
                """,
                {
                    "tenant_id": options.tenant_id,
                    "product_sku": DEFAULT_LICENSE_PRODUCT_SKU,
                    "plan_code": DEFAULT_LICENSE_PLAN_CODE,
                },
            )
        }

    if has_table(connection, "v_tenant_operational_row_counts"):
        rows = connection.execute(
            text(
                """
                SELECT table_name, row_count
                FROM v_tenant_operational_row_counts
                WHERE tenant_id = :tenant_id
                ORDER BY table_name
                """
            ),
            {"tenant_id": options.tenant_id},
        ).mappings()
        findings["tenant_row_counts"] = {row["table_name"]: int(row["row_count"]) for row in rows}

    passed = bool(findings["tenant_exists"])
    passed = passed and all(count == 0 for count in findings["orphan_tenant_refs"].values())
    if options.user_email_domain:
        passed = passed and findings["unscoped_counts"].get("matching_users", 0) == 0
    if options.platform_owner_emails:
        passed = passed and findings.get("platform_owner_status", {}).get("super_admin_tenantless", 0) == len(
            options.platform_owner_emails
        )
    if options.seed_full_access_license:
        passed = passed and findings.get("license", {}).get("active_full_access_license", 0) > 0
    findings["passed"] = passed
    return findings


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, TenantBackfillOptions]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("dry-run", "apply", "verify"))
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    parser.add_argument("--tenant-name", default=None)
    parser.add_argument("--tenant-slug", default=None)
    parser.add_argument("--user-email-domain", default=None)
    parser.add_argument("--include-admins", action="store_true")
    parser.add_argument("--skip-users", action="store_true")
    parser.add_argument("--skip-webhooks", action="store_true")
    parser.add_argument("--skip-settings", action="store_true")
    parser.add_argument(
        "--seed-full-access-license",
        action="store_true",
        help="Create FieldCore OPS Pro wildcard entitlement and assign it to the tenant.",
    )
    parser.add_argument(
        "--platform-owner-email",
        action="append",
        default=[],
        help="Promote this user to tenantless super_admin and exclude them from tenant backfill. Repeat for multiple owners.",
    )
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when verify fails")
    args = parser.parse_args(argv)

    tenant_id = normalize_identifier(args.tenant_id, "tenant_id")
    tenant_slug = normalize_identifier(args.tenant_slug or tenant_id, "tenant_slug")
    tenant_name = (args.tenant_name or (DEFAULT_TENANT_NAME if tenant_id == DEFAULT_TENANT_ID else tenant_slug.replace("-", " ").title())).strip()
    platform_owner_emails = tuple(
        sorted(
            {
                email.strip().lower()
                for email in args.platform_owner_email
                if isinstance(email, str) and email.strip()
            }
        )
    )

    options = TenantBackfillOptions(
        phase=args.phase,
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        tenant_slug=tenant_slug,
        user_email_domain=args.user_email_domain,
        include_admins=args.include_admins,
        skip_users=args.skip_users,
        skip_webhooks=args.skip_webhooks,
        skip_settings=args.skip_settings,
        seed_full_access_license=args.seed_full_access_license,
        platform_owner_emails=platform_owner_emails,
        strict=args.strict,
    )
    return args, options


def main(argv: list[str] | None = None) -> int:
    args, options = parse_args(argv or sys.argv[1:])
    engine = make_engine(resolve_database_url(args.database_url))

    if options.phase == "apply":
        with engine.begin() as connection:
            plan = build_plan(connection, options)
            applied = apply_backfill(connection, options, plan)
            verification = verify(connection, options)
            print_json(
                {
                    "options": asdict(options),
                    "plan": plan,
                    "applied": applied,
                    "verification": verification,
                }
            )
            return 0 if verification["passed"] or not options.strict else 1

    with engine.connect() as connection:
        plan = build_plan(connection, options)
        if options.phase == "dry-run":
            print_json({"options": asdict(options), "plan": plan})
            return 0

        verification = verify(connection, options)
        print_json({"options": asdict(options), "plan": plan, "verification": verification})
        return 0 if verification["passed"] or not options.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
