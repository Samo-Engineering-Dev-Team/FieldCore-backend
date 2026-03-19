-- Migration 0021: Remap legacy GENERAL report_type to DIESEL
-- ReportType.GENERAL has been removed from the codebase. Any existing reports
-- that were stored with report_type = 'GENERAL' are remapped to 'DIESEL'
-- (the most appropriate general-purpose field report type).
-- Also fixes tasks that still have report_type = 'general' (lowercase string).

-- Fix reports table (enum stored as uppercase NAME)
UPDATE reports
SET report_type = 'DIESEL'
WHERE report_type = 'GENERAL';

-- Fix tasks table (report_type stored as plain lowercase string)
UPDATE tasks
SET report_type = 'diesel'
WHERE report_type = 'general';
