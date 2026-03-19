-- scripts/0013_alter_incident_reports_add_missing_columns.sql
-- Adds columns that were missing from the initial incident_reports table creation.

ALTER TABLE incident_reports
    ADD COLUMN IF NOT EXISTS findings            TEXT,
    ADD COLUMN IF NOT EXISTS actions_taken       TEXT,
    ADD COLUMN IF NOT EXISTS root_cause_analysis TEXT,
    ADD COLUMN IF NOT EXISTS conclusion          TEXT,
    ADD COLUMN IF NOT EXISTS attachments         JSONB,
    ADD COLUMN IF NOT EXISTS pdf_path            VARCHAR(1024),
    ADD COLUMN IF NOT EXISTS pdf_url             VARCHAR(2048);
