-- Migration 0022: Track which NOC/Admin user assigned each task or incident
-- assigned_by_user_id: FK to users.id (nullable — null for records created before this migration)
-- assigned_by_name:    Denormalised display name for fast read without extra join

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS assigned_by_user_id UUID REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS assigned_by_name    VARCHAR(200);

COMMENT ON COLUMN tasks.assigned_by_user_id IS 'The NOC/Admin user who created/assigned this task';
COMMENT ON COLUMN tasks.assigned_by_name    IS 'Denormalised full name of the assigning user for display';

ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS assigned_by_user_id UUID REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS assigned_by_name    VARCHAR(200);

COMMENT ON COLUMN incidents.assigned_by_user_id IS 'The NOC/Admin user who created/assigned this incident';
COMMENT ON COLUMN incidents.assigned_by_name    IS 'Denormalised full name of the assigning user for display';
