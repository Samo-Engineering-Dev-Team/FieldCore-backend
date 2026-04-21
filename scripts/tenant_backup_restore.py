#!/usr/bin/env python
"""Tenant-scoped backup, restore, and staging validation utility."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine


GEOMETRY_COLUMNS: dict[str, set[str]] = {
    "sites": {"location"},
    "technicians": {"current_location", "home_base"},
}


@dataclass(frozen=True)
class BackupQuery:
    table: str
    sql: str
    required_tables: tuple[str, ...]
    reference_data: bool = False


BACKUP_QUERIES: tuple[BackupQuery, ...] = (
    BackupQuery("license_products", """
        SELECT DISTINCT lp.*
        FROM license_products lp
        JOIN license_plans plan ON plan.license_product_id = lp.id
        JOIN tenant_licenses tl ON tl.license_plan_id = plan.id
        WHERE tl.tenant_id = :tenant_id
    """, ("license_products", "license_plans", "tenant_licenses"), True),
    BackupQuery("license_plans", """
        SELECT DISTINCT plan.*
        FROM license_plans plan
        JOIN tenant_licenses tl ON tl.license_plan_id = plan.id
        WHERE tl.tenant_id = :tenant_id
    """, ("license_plans", "tenant_licenses"), True),
    BackupQuery("entitlements", """
        SELECT DISTINCT e.*
        FROM entitlements e
        JOIN license_plans plan ON plan.id = e.license_plan_id
        JOIN tenant_licenses tl ON tl.license_plan_id = plan.id
        WHERE tl.tenant_id = :tenant_id
    """, ("entitlements", "license_plans", "tenant_licenses"), True),
    BackupQuery("sites", """
        SELECT DISTINCT s.*
        FROM sites s
        WHERE s.id IN (
            SELECT task.site_id
            FROM tasks task
            JOIN technicians t ON t.id = task.technician_id
            JOIN users u ON u.id = t.user_id
            WHERE u.tenant_id = :tenant_id
            UNION
            SELECT i.site_id
            FROM incidents i
            JOIN technicians t ON t.id = i.technician_id
            JOIN users u ON u.id = t.user_id
            WHERE u.tenant_id = :tenant_id
            UNION
            SELECT ar.site_id
            FROM access_requests ar
            JOIN technicians t ON t.id = ar.technician_id
            JOIN users u ON u.id = t.user_id
            WHERE u.tenant_id = :tenant_id
            UNION
            SELECT rp.site_id
            FROM route_patrols rp
            JOIN technicians t ON t.id = rp.technician_id
            JOIN users u ON u.id = t.user_id
            WHERE u.tenant_id = :tenant_id AND rp.site_id IS NOT NULL
            UNION
            SELECT ms.site_id
            FROM maintenance_schedules ms
            JOIN technicians t ON t.id = ms.assigned_technician_id
            JOIN users u ON u.id = t.user_id
            WHERE u.tenant_id = :tenant_id AND ms.assigned_technician_id IS NOT NULL
        )
    """, ("sites", "tasks", "incidents", "access_requests", "route_patrols", "maintenance_schedules", "technicians", "users"), True),
    BackupQuery("clients", """
        SELECT DISTINCT c.*
        FROM clients c
        JOIN incidents i ON i.client_id = c.id
        JOIN technicians t ON t.id = i.technician_id
        JOIN users u ON u.id = t.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("clients", "incidents", "technicians", "users"), True),
    BackupQuery("tenants", "SELECT * FROM tenants WHERE id = :tenant_id", ("tenants",)),
    BackupQuery("users", """
        SELECT *
        FROM users
        WHERE tenant_id = :tenant_id
    """, ("users",)),
    BackupQuery("system_settings", """
        SELECT *
        FROM system_settings
        WHERE tenant_id = :tenant_id
    """, ("system_settings",)),
    BackupQuery("webhooks", """
        SELECT *
        FROM webhooks
        WHERE tenant_id = :tenant_id
    """, ("webhooks",)),
    BackupQuery("tenant_templates", """
        SELECT *
        FROM tenant_templates
        WHERE tenant_id = :tenant_id
    """, ("tenant_templates",)),
    BackupQuery("tenant_licenses", """
        SELECT *
        FROM tenant_licenses
        WHERE tenant_id = :tenant_id
    """, ("tenant_licenses",)),
    BackupQuery("license_history", """
        SELECT *
        FROM license_history
        WHERE tenant_id = :tenant_id
    """, ("license_history",)),
    BackupQuery("tenant_feature_usage_events", """
        SELECT *
        FROM tenant_feature_usage_events
        WHERE tenant_id = :tenant_id
    """, ("tenant_feature_usage_events",)),
    BackupQuery("tenant_usage_daily", """
        SELECT *
        FROM tenant_usage_daily
        WHERE tenant_id = :tenant_id
    """, ("tenant_usage_daily",)),
    BackupQuery("tenant_compliance_records", """
        SELECT *
        FROM tenant_compliance_records
        WHERE tenant_id = :tenant_id
    """, ("tenant_compliance_records",)),
    BackupQuery("audit_log", """
        SELECT *
        FROM audit_log
        WHERE tenant_id = :tenant_id
    """, ("audit_log",)),
    BackupQuery("tenant_operation_logs", """
        SELECT *
        FROM tenant_operation_logs
        WHERE tenant_id = :tenant_id
    """, ("tenant_operation_logs",)),
    BackupQuery("passkey_credentials", """
        SELECT pc.*
        FROM passkey_credentials pc
        JOIN users u ON u.id = pc.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("passkey_credentials", "users")),
    BackupQuery("passkey_challenges", """
        SELECT pc.*
        FROM passkey_challenges pc
        JOIN users u ON u.id = pc.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("passkey_challenges", "users")),
    BackupQuery("user_sessions", """
        SELECT us.*
        FROM user_sessions us
        JOIN users u ON u.id = us.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("user_sessions", "users")),
    BackupQuery("notifications", """
        SELECT n.*
        FROM notifications n
        JOIN users u ON u.id = n.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("notifications", "users")),
    BackupQuery("technicians", """
        SELECT t.*
        FROM technicians t
        JOIN users u ON u.id = t.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("technicians", "users")),
    BackupQuery("technician_sites", """
        SELECT ts.*
        FROM technician_sites ts
        JOIN technicians t ON t.id = ts.technician_id
        JOIN users u ON u.id = t.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("technician_sites", "technicians", "users")),
    BackupQuery("tasks", """
        SELECT task.*
        FROM tasks task
        JOIN technicians t ON t.id = task.technician_id
        JOIN users u ON u.id = t.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("tasks", "technicians", "users")),
    BackupQuery("access_requests", """
        SELECT ar.*
        FROM access_requests ar
        JOIN technicians t ON t.id = ar.technician_id
        JOIN users u ON u.id = t.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("access_requests", "technicians", "users")),
    BackupQuery("incidents", """
        SELECT i.*
        FROM incidents i
        JOIN technicians t ON t.id = i.technician_id
        JOIN users u ON u.id = t.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("incidents", "technicians", "users")),
    BackupQuery("reports", """
        SELECT r.*
        FROM reports r
        JOIN technicians t ON t.id = r.technician_id
        JOIN users u ON u.id = t.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("reports", "technicians", "users")),
    BackupQuery("routine_inspections", """
        SELECT ri.*
        FROM routine_inspections ri
        JOIN technicians t ON t.id = ri.technician_id
        JOIN users u ON u.id = t.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("routine_inspections", "technicians", "users")),
    BackupQuery("routine_checks", """
        SELECT rc.*
        FROM routine_checks rc
        JOIN reports r ON r.id = rc.report_id
        JOIN technicians t ON t.id = r.technician_id
        JOIN users u ON u.id = t.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("routine_checks", "reports", "technicians", "users")),
    BackupQuery("routine_issues", """
        SELECT ri.*
        FROM routine_issues ri
        JOIN reports r ON r.id = ri.report_id
        JOIN technicians t ON t.id = r.technician_id
        JOIN users u ON u.id = t.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("routine_issues", "reports", "technicians", "users")),
    BackupQuery("incident_reports", """
        SELECT ir.*
        FROM incident_reports ir
        JOIN technicians t ON t.id = ir.technician_id
        JOIN users u ON u.id = t.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("incident_reports", "technicians", "users")),
    BackupQuery("incident_updates", """
        SELECT iu.*
        FROM incident_updates iu
        JOIN incidents i ON i.id = iu.incident_id
        JOIN technicians t ON t.id = i.technician_id
        JOIN users u ON u.id = t.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("incident_updates", "incidents", "technicians", "users")),
    BackupQuery("route_patrols", """
        SELECT rp.*
        FROM route_patrols rp
        JOIN technicians t ON t.id = rp.technician_id
        JOIN users u ON u.id = t.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("route_patrols", "technicians", "users")),
    BackupQuery("maintenance_schedules", """
        SELECT ms.*
        FROM maintenance_schedules ms
        JOIN technicians t ON t.id = ms.assigned_technician_id
        JOIN users u ON u.id = t.user_id
        WHERE u.tenant_id = :tenant_id
    """, ("maintenance_schedules", "technicians", "users")),
)


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)


def database_url(explicit: str | None) -> str:
    if explicit:
        return explicit
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]

    host = os.getenv("DB_HOST")
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    name = os.getenv("DB_NAME")
    port = os.getenv("DB_PORT", "5432")
    if host and user and password and name:
        return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"

    raise SystemExit("No database URL supplied. Use --database-url or DATABASE_URL/DB_* env vars.")


def existing_tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def query_available(query: BackupQuery, tables: set[str]) -> bool:
    return all(table in tables for table in query.required_tables)


def row_without_unsupported(table: str, row: dict[str, Any]) -> dict[str, Any]:
    blocked = GEOMETRY_COLUMNS.get(table, set())
    return {key: value for key, value in row.items() if key not in blocked}


def fetch_rows(connection: Connection, query: BackupQuery, tenant_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(text(query.sql), {"tenant_id": tenant_id}).mappings().all()
    return [row_without_unsupported(query.table, dict(row)) for row in rows]


def backup_tenant(engine: Engine, tenant_id: str) -> dict[str, Any]:
    tables = existing_tables(engine)
    backup: dict[str, Any] = {
        "metadata": {
            "tenant_id": tenant_id,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "source_database": engine.url.database,
            "format": "fieldcore-tenant-json-v1",
            "warnings": [
                "PostGIS geometry columns are omitted; recreate location/home-base coordinates after restore if needed.",
                "Supabase object storage files are external; copy tenant storage keys separately.",
            ],
        },
        "tables": {},
        "counts": {},
        "skipped_tables": [],
    }

    with engine.begin() as connection:
        for query in BACKUP_QUERIES:
            if not query_available(query, tables):
                backup["skipped_tables"].append(query.table)
                continue
            rows = fetch_rows(connection, query, tenant_id)
            backup["tables"][query.table] = rows
            backup["counts"][query.table] = len(rows)

    if backup["counts"].get("tenants", 0) != 1:
        raise SystemExit(f"Tenant '{tenant_id}' not found in source database.")
    return backup


def ensure_staging_target(engine: Engine, allow_production_restore: bool) -> None:
    if allow_production_restore:
        return
    database_name = (engine.url.database or "").lower()
    if any(token in database_name for token in ("staging", "stage", "test", "dev")):
        return
    raise SystemExit(
        "Restore target database name must include staging/stage/test/dev. "
        "Use --allow-production-restore only for an approved emergency restore."
    )


def restore_backup(
    engine: Engine,
    backup: dict[str, Any],
    *,
    confirm_tenant_id: str,
    allow_production_restore: bool,
) -> dict[str, int]:
    tenant_id = backup.get("metadata", {}).get("tenant_id")
    if tenant_id != confirm_tenant_id:
        raise SystemExit("confirm_tenant_id does not match backup metadata tenant_id.")

    ensure_staging_target(engine, allow_production_restore)

    metadata = MetaData()
    metadata.reflect(bind=engine)
    restored: dict[str, int] = {}

    with engine.begin() as connection:
        for table_name, rows in backup.get("tables", {}).items():
            if not rows or table_name not in metadata.tables:
                restored[table_name] = 0
                continue
            table = metadata.tables[table_name]
            valid_columns = set(table.columns.keys())
            filtered_rows = [
                {key: value for key, value in row.items() if key in valid_columns}
                for row in rows
            ]
            if not filtered_rows:
                restored[table_name] = 0
                continue

            primary_keys = [column.name for column in table.primary_key.columns]
            if engine.dialect.name == "postgresql" and primary_keys:
                statement = pg_insert(table).values(filtered_rows)
                update_columns = {
                    column.name: getattr(statement.excluded, column.name)
                    for column in table.columns
                    if column.name not in primary_keys
                }
                if update_columns:
                    statement = statement.on_conflict_do_update(
                        index_elements=[table.c[name] for name in primary_keys],
                        set_=update_columns,
                    )
                else:
                    statement = statement.on_conflict_do_nothing(
                        index_elements=[table.c[name] for name in primary_keys],
                    )
                connection.execute(statement)
            else:
                connection.execute(table.insert(), filtered_rows)
            restored[table_name] = len(filtered_rows)

    return restored


def validate_restore(engine: Engine, backup: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    observed_backup = backup_tenant(engine, tenant_id)
    expected_counts = backup.get("counts", {})
    observed_counts = observed_backup.get("counts", {})
    mismatches = {
        table: {
            "expected": expected,
            "observed": observed_counts.get(table, 0),
        }
        for table, expected in expected_counts.items()
        if observed_counts.get(table, 0) < expected
    }
    return {
        "tenant_id": tenant_id,
        "ok": not mismatches,
        "mismatches": mismatches,
        "observed_counts": observed_counts,
    }


def load_backup(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_backup(path: Path, backup: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(backup, handle, indent=2, default=json_default, sort_keys=True)
        handle.write("\n")


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, default=json_default, sort_keys=True))


def cmd_backup(args: argparse.Namespace) -> None:
    engine = create_engine(database_url(args.database_url))
    backup = backup_tenant(engine, args.tenant_id)
    write_backup(Path(args.output), backup)
    print_json(
        {
            "tenant_id": args.tenant_id,
            "output": args.output,
            "counts": backup["counts"],
            "skipped_tables": backup["skipped_tables"],
        }
    )


def cmd_restore(args: argparse.Namespace) -> None:
    backup = load_backup(Path(args.input))
    engine = create_engine(database_url(args.database_url))
    restored = restore_backup(
        engine,
        backup,
        confirm_tenant_id=args.confirm_tenant_id,
        allow_production_restore=args.allow_production_restore,
    )
    print_json({"tenant_id": args.confirm_tenant_id, "restored": restored})


def cmd_validate_restore(args: argparse.Namespace) -> None:
    backup = load_backup(Path(args.input))
    engine = create_engine(database_url(args.staging_database_url))
    restore_backup(
        engine,
        backup,
        confirm_tenant_id=args.confirm_tenant_id,
        allow_production_restore=args.allow_production_restore,
    )
    result = validate_restore(engine, backup, args.confirm_tenant_id)
    print_json(result)
    if not result["ok"]:
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="Export one tenant to JSON")
    backup.add_argument("--tenant-id", required=True)
    backup.add_argument("--output", required=True)
    backup.add_argument("--database-url")
    backup.set_defaults(func=cmd_backup)

    restore = subparsers.add_parser("restore", help="Restore one tenant JSON backup")
    restore.add_argument("--input", required=True)
    restore.add_argument("--confirm-tenant-id", required=True)
    restore.add_argument("--database-url")
    restore.add_argument("--allow-production-restore", action="store_true")
    restore.set_defaults(func=cmd_restore)

    validate = subparsers.add_parser(
        "validate-restore",
        help="Restore into staging then compare tenant counts",
    )
    validate.add_argument("--input", required=True)
    validate.add_argument("--confirm-tenant-id", required=True)
    validate.add_argument("--staging-database-url")
    validate.add_argument("--allow-production-restore", action="store_true")
    validate.set_defaults(func=cmd_validate_restore)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
