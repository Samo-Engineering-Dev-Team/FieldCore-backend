-- scripts/0012_create_incident_reports.sql
-- Create the incident_reports table for structured post-resolution reports.

CREATE TABLE IF NOT EXISTS incident_reports (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Foreign keys
    incident_id  UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    technician_id UUID NOT NULL REFERENCES technicians(id),

    -- Auto-populated fields
    site_name       VARCHAR(255)  NOT NULL,
    report_date     TIMESTAMPTZ   NOT NULL DEFAULT now(),
    technician_name VARCHAR(255)  NOT NULL,

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
    pdf_url  VARCHAR(2048),

    -- Audit columns
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ

    -- Soft-delete-aware unique constraint: one active report per incident
    -- (a new report can be created after the previous one is soft-deleted)
);

-- Partial unique index enforces one active report per incident
CREATE UNIQUE INDEX IF NOT EXISTS uq_incident_report_incident_active
    ON incident_reports (incident_id)
    WHERE deleted_at IS NULL;

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_incident_reports_incident_id
    ON incident_reports (incident_id);

CREATE INDEX IF NOT EXISTS idx_incident_reports_technician_id
    ON incident_reports (technician_id);

CREATE INDEX IF NOT EXISTS idx_incident_reports_created_at
    ON incident_reports (created_at DESC);

-- Auto-update updated_at trigger (reuses the pattern from other tables)
-- If the trigger function already exists from another migration, this is a no-op.
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
