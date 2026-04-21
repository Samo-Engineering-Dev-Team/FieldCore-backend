# Tenant Backup / Restore Procedure

Use `scripts/tenant_backup_restore.py` for tenant-scoped JSON exports and staging restore validation.

## Backup

```powershell
python scripts/tenant_backup_restore.py backup `
  --tenant-id tenant-alpha `
  --database-url $env:PRODUCTION_DATABASE_URL `
  --output backups/tenant-alpha.json
```

## Validate Restore In Staging

```powershell
python scripts/tenant_backup_restore.py validate-restore `
  --input backups/tenant-alpha.json `
  --confirm-tenant-id tenant-alpha `
  --staging-database-url $env:STAGING_DATABASE_URL
```

The restore command refuses target database names that do not include `staging`, `stage`, `test`, or `dev` unless `--allow-production-restore` is supplied for an approved emergency restore.

## Notes

- Exports include tenant rows, tenant users, settings, webhooks, licensing records, audit/operation logs, user-owned auth/session rows, technician-owned operational rows, and required global reference rows for sites, clients, license plans, and entitlements.
- Supabase object storage is external. Copy keys under `tenants/<tenant_id>/uploads/` separately when restoring attachments.
- PostGIS geometry columns are omitted from JSON restore; reset site/technician coordinates after validation if needed.
