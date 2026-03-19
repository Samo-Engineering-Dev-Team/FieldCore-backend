# Scripts Directory Guide

## Purpose
This folder contains SQL migrations, bootstrap SQL, and operational helper scripts for the backend.

## Categories

### 1) Runtime / deployment critical
- `init-postgis.sql`
- `fix_trigger.sql`
- Numbered migration files (`0008_...` onward)

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
