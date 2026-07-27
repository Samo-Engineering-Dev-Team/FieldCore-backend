-- Diesel site history (GET /v1/reports/diesel-history/{site_id}) narrows reports
-- to one site's completed diesel reports before unnesting data->'diesel_fillups'.
--
-- The other two legs of that query are already indexed and need nothing here:
--   tasks(site_id)             -> idx_tasks_site_id
--   technician_sites(site_id)  -> idx_technician_sites_site  (scope check)
--
-- Only the reports-side filter is uncovered: `reports` carries just its pkey and
-- ux_reports_task_id_active (UNIQUE on task_id WHERE deleted_at IS NULL), which
-- does not help a report_type + status scan.
CREATE INDEX IF NOT EXISTS ix_reports_diesel_history
    ON reports (report_type, status, task_id)
    WHERE deleted_at IS NULL;
