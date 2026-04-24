# Scripts Directory Guide

## Purpose
This folder contains SQL migrations, bootstrap SQL, and operational helper scripts for the backend.

## Categories

### 1) Runtime / deployment critical
- `init-postgis.sql`
- `fix_trigger.sql`
- Numbered migration files (`0008_...` onward)
- `backfill_tenant_scope.py` for dry-run/apply/verify tenant scope backfills.

### 2) Baseline setup SQL
- `01_create_experimental_db.sql`
- `02_enable_postgis.sql`
- `03_create_webhooks_table.sql`
- `01_create_management_dashboard_views.sql`

### 3) Utility scripts
- `seed_db.py`
- `seed_test_users.py`
- `fix_db_issues.py`
- `create_dashboard_views.py`
- `deploy_supabase_schema.ps1`
- `test_send_email.py`
- `run_tests.py`
- `targeted_checks.py`
- `print_settings.py`

### 4) Archived / deprecated
- `scripts/archive/deprecated/*`

## Migration Execution
Use explicit ordering for numbered migration files:

```powershell
Get-ChildItem scripts\00*.sql |
  Sort-Object Name |
  ForEach-Object {
    psql -h localhost -p 5433 -U postgres -d seacom_experimental_db -f $_.FullName
  }
```

Apply baseline scripts separately when needed.

## Hygiene Rules
- Do not edit old migration files already applied in shared environments.
- Add new changes as new migration files with the next sequence.
- Move deprecated one-off scripts to `scripts/archive/` instead of deleting immediately.
- Never commit `__pycache__` artifacts.

## Tenant Scope Backfill

Review `docs/tenant-isolation-rollout-runbook.md` first. Typical staging flow:

```powershell
psql "$env:DATABASE_URL" -f scripts/0041_tenant_isolation_rollout_support.sql
python scripts/backfill_tenant_scope.py dry-run --tenant-id tenant-alpha --user-email-domain example.com --database-url "$env:DATABASE_URL"
python scripts/backfill_tenant_scope.py apply --tenant-id tenant-alpha --user-email-domain example.com --database-url "$env:DATABASE_URL" --strict
python scripts/backfill_tenant_scope.py verify --tenant-id tenant-alpha --user-email-domain example.com --database-url "$env:DATABASE_URL" --strict
```

Samo rollout helper (data-preserving):

```powershell
python scripts/backfill_tenant_scope.py dry-run --database-url "$env:DATABASE_URL" --include-admins --seed-full-access-license --platform-owner-email "bongani@example.com"
python scripts/backfill_tenant_scope.py apply --database-url "$env:DATABASE_URL" --include-admins --seed-full-access-license --platform-owner-email "bongani@example.com" --strict
python scripts/backfill_tenant_scope.py verify --database-url "$env:DATABASE_URL" --include-admins --seed-full-access-license --platform-owner-email "bongani@example.com" --strict
```

Replace `bongani@example.com` with Bongani's real login email. Defaults create tenant `samo-telecoms` named `Samo Telecoms`.
