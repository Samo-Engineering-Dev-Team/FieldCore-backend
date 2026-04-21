# Tenant Isolation Rollout Runbook

Last reviewed: 2026-04-21

## Goal

Roll out tenant isolation without running production data changes blindly. Production migration execution is out of scope for this change set.

## Pre-Checks

1. Confirm latest backup/snapshot exists and restore path has been tested in staging.
2. Confirm `scripts/0041_tenant_isolation_rollout_support.sql` has been reviewed.
3. Confirm CI passes tenant isolation tests:

```powershell
pytest tests/test_tenant_isolation.py tests/test_tenant_scope.py tests/test_report_permissions.py tests/test_report_export_permissions.py
```

4. Identify tenant mapping rules:

```text
tenant_id:
tenant_name:
tenant_slug:
user_email_domain:
include_admins: yes/no
webhooks: backfill/skip
settings: copy/skip
```

## Staging Validation

1. Restore a fresh production-like backup into staging.
2. Apply support migration:

```powershell
psql "$env:DATABASE_URL" -f scripts/0041_tenant_isolation_rollout_support.sql
```

3. Run dry-run and save JSON output for review:

```powershell
python scripts/backfill_tenant_scope.py dry-run `
  --tenant-id tenant-alpha `
  --tenant-name "Tenant Alpha" `
  --user-email-domain example.com `
  --database-url "$env:DATABASE_URL"
```

4. Review counts. Do not proceed if users/webhooks/settings counts differ from expected mapping.
5. Apply in staging:

```powershell
python scripts/backfill_tenant_scope.py apply `
  --tenant-id tenant-alpha `
  --tenant-name "Tenant Alpha" `
  --user-email-domain example.com `
  --database-url "$env:DATABASE_URL" `
  --strict
```

6. Verify:

```powershell
python scripts/backfill_tenant_scope.py verify `
  --tenant-id tenant-alpha `
  --user-email-domain example.com `
  --database-url "$env:DATABASE_URL" `
  --strict
```

7. Validate app behavior in staging:

- Tenant manager cannot list/view/export another tenant task/report.
- Token tenant spoofing does not change authenticated tenant scope.
- Tenant settings resolve from tenant rows, with global settings still present.
- Webhooks fire only for matching tenant.
- Dashboard/report exports show only expected tenant data.

## Production Window

1. Schedule low-traffic maintenance window.
2. Pause background jobs that create tasks/reports/webhooks/settings.
3. Take final database snapshot immediately before migration.
4. Apply `0041` support migration.
5. Run dry-run and have reviewer approve output.
6. Run `apply --strict`.
7. Run `verify --strict`.
8. Smoke-test login, users list, task list/detail, report list/detail/export, webhooks, settings.
9. Resume background jobs.

## Rollback

Preferred rollback: restore pre-window database snapshot.

If snapshot restore is not required and no conflicting writes occurred, reverse only the tenant touched:

```sql
UPDATE users
SET tenant_id = NULL
WHERE tenant_id = 'tenant-alpha'
  AND LOWER(email) LIKE '%@example.com';

UPDATE webhooks
SET tenant_id = NULL
WHERE tenant_id = 'tenant-alpha';

DELETE FROM system_settings
WHERE tenant_id = 'tenant-alpha';

DELETE FROM tenants
WHERE id = 'tenant-alpha'
  AND NOT EXISTS (SELECT 1 FROM users WHERE tenant_id = 'tenant-alpha')
  AND NOT EXISTS (SELECT 1 FROM webhooks WHERE tenant_id = 'tenant-alpha')
  AND NOT EXISTS (SELECT 1 FROM system_settings WHERE tenant_id = 'tenant-alpha');
```

Then run `verify --strict` and replay smoke tests.

## Review Artifacts

Attach these to the rollout ticket:

- CI tenant isolation test output.
- Staging dry-run JSON.
- Staging apply JSON.
- Staging verify JSON.
- Production dry-run JSON.
- Production verify JSON.
- Backup snapshot ID and restore owner.
