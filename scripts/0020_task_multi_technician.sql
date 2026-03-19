-- Migration 0020: Support multiple technicians per task (for large shared jobs)
-- The primary technician_id remains the lead. Additional technicians are stored
-- as a JSONB array of UUID strings so no schema changes are needed on existing rows.

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS additional_technician_ids JSONB DEFAULT NULL;

COMMENT ON COLUMN tasks.additional_technician_ids IS
  'Optional array of additional technician UUIDs for shared/large jobs. Lead is technician_id.';
