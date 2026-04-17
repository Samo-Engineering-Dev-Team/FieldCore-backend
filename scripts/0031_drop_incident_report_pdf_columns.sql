-- scripts/0031_drop_incident_report_pdf_columns.sql
-- Incident report PDF exports are now download-only and no longer persist
-- storage metadata on the incident_reports table.

ALTER TABLE incident_reports
    DROP COLUMN IF EXISTS pdf_path,
    DROP COLUMN IF EXISTS pdf_url;
