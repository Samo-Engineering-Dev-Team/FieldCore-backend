-- Add DATACENTER and POP to the reporttype enum.
-- SQLAlchemy maps Python enum members by NAME, so ReportType.DATACENTER is
-- stored as the label 'DATACENTER'.
-- Note: ALTER TYPE ... ADD VALUE must run outside a transaction block (autocommit).

ALTER TYPE reporttype ADD VALUE IF NOT EXISTS 'DATACENTER';
ALTER TYPE reporttype ADD VALUE IF NOT EXISTS 'POP';

-- sites.site_type and maintenance_schedules.schedule_type are plain VARCHAR
-- (confirmed against the live DB, not the orphan `sitetype` Postgres enum
-- type, which exists but does not back the sites.site_type column) — no
-- migration needed for SiteType.DATACENTER or the two new schedule types.
