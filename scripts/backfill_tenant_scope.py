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


def apply_backfill(connection: Connection, options: TenantBackfillOptions, plan: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(connection)
    upsert_tenant(connection, options)
    applied: dict[str, int] = {"tenants_upserted": 1}

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
            "details": json.dumps({"plan": plan, "applied": applied}, default=json_default),
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
    findings["passed"] = passed
    return findings


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, TenantBackfillOptions]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("dry-run", "apply", "verify"))
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--tenant-name", default=None)
    parser.add_argument("--tenant-slug", default=None)
    parser.add_argument("--user-email-domain", default=None)
    parser.add_argument("--include-admins", action="store_true")
    parser.add_argument("--skip-users", action="store_true")
    parser.add_argument("--skip-webhooks", action="store_true")
    parser.add_argument("--skip-settings", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when verify fails")
    args = parser.parse_args(argv)

    tenant_id = normalize_identifier(args.tenant_id, "tenant_id")
    tenant_slug = normalize_identifier(args.tenant_slug or tenant_id, "tenant_slug")
    tenant_name = (args.tenant_name or tenant_slug.replace("-", " ").title()).strip()

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
