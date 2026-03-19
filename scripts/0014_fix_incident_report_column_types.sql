-- scripts/0014_fix_incident_report_column_types.sql
-- Some columns in incident_reports were previously created with incorrect types
-- (e.g., TEXT[] instead of TEXT, or missing attachments JSONB).
-- This migration drops and re-creates them with the correct types.
-- SAFE ONLY when the table is empty (no committed incident report rows).

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM incident_reports LIMIT 1) THEN
        RAISE EXCEPTION 'incident_reports is not empty — back up data before running this migration';
    END IF;
END $$;

ALTER TABLE incident_reports
    DROP COLUMN IF EXISTS actions_taken,
    DROP COLUMN IF EXISTS root_cause_analysis,
    DROP COLUMN IF EXISTS conclusion,
    DROP COLUMN IF EXISTS attachments;

ALTER TABLE incident_reports
    ADD COLUMN IF NOT EXISTS actions_taken       TEXT,
    ADD COLUMN IF NOT EXISTS root_cause_analysis TEXT,
    ADD COLUMN IF NOT EXISTS conclusion          TEXT,
    ADD COLUMN IF NOT EXISTS attachments         JSONB;
