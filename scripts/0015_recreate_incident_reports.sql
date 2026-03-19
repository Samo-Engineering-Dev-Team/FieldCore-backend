-- scripts/0015_recreate_incident_reports.sql
-- The incident_reports table was previously created with a wrong schema
-- (contained extra columns like seacom_ref NOT NULL, status, etc.).
-- This migration drops and fully recreates the table with the correct schema.
-- SAFE ONLY when the table is empty.

-- Clear any stale rows from the old wrong schema before dropping
TRUNCATE incident_reports CASCADE;

DROP TABLE IF EXISTS incident_reports CASCADE;

CREATE TABLE incident_reports (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at    TIMESTAMPTZ NOT NULL    DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL    DEFAULT now(),
    deleted_at    TIMESTAMPTZ,

    -- Foreign keys
    incident_id   UUID        NOT NULL REFERENCES incidents(id)   ON DELETE CASCADE,
    technician_id UUID        NOT NULL REFERENCES technicians(id),

    -- Auto-populated fields
    site_name       VARCHAR(255) NOT NULL,
    report_date     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    technician_name VARCHAR(255) NOT NULL,

    -- Narrative sections
    introduction        TEXT,
    problem_statement   TEXT,
    findings            TEXT,
    actions_taken       TEXT,
    root_cause_analysis TEXT,
    conclusion          TEXT,

    -- Evidence attachments
    attachments JSONB,

    -- Stored PDF metadata (populated on first export)
    pdf_path VARCHAR(1024),
    pdf_url  VARCHAR(2048)
);

-- Soft-delete-aware unique constraint: one active report per incident
CREATE UNIQUE INDEX uq_incident_report_incident_active
    ON incident_reports (incident_id)
    WHERE deleted_at IS NULL;

-- Performance indexes
CREATE INDEX idx_incident_reports_incident_id
    ON incident_reports (incident_id);

CREATE INDEX idx_incident_reports_technician_id
    ON incident_reports (technician_id);

CREATE INDEX idx_incident_reports_created_at
    ON incident_reports (created_at DESC);

-- Auto-update updated_at trigger
CREATE OR REPLACE FUNCTION update_incident_reports_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_incident_reports_updated_at ON incident_reports;
CREATE TRIGGER trg_incident_reports_updated_at
    BEFORE UPDATE ON incident_reports
    FOR EACH ROW EXECUTE FUNCTION update_incident_reports_updated_at();
