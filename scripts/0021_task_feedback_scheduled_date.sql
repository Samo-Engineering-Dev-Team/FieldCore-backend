-- Migration 0021: Task feedback field + maintenance schedule updates
--
-- Adds:
--   tasks.feedback            — free-text RHS completion summary (no formal report)
--   maintenance_schedules.scheduled_date — tech-selected target date for completing the task
--   maintenance_schedules schedule_type check constraint updated to the 3 mandatory types

-- tasks: RHS feedback
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS feedback TEXT;

-- maintenance_schedules: self-scheduled date
ALTER TABLE maintenance_schedules ADD COLUMN IF NOT EXISTS scheduled_date TIMESTAMPTZ;

-- Drop the old check constraint that only allowed legacy types.
-- The auto-generated name from migration 0018 is maintenance_schedules_schedule_type_check.
ALTER TABLE maintenance_schedules DROP CONSTRAINT IF EXISTS maintenance_schedules_schedule_type_check;

-- Migrate existing rows from old type values to the 3 mandatory types.
-- fibre_route / transmission → routine_drive
-- facility                  → repeater_site_visit
-- manhole                   → generator_diesel_refill
UPDATE maintenance_schedules SET schedule_type = 'routine_drive'           WHERE schedule_type IN ('fibre_route', 'transmission');
UPDATE maintenance_schedules SET schedule_type = 'repeater_site_visit'     WHERE schedule_type = 'facility';
UPDATE maintenance_schedules SET schedule_type = 'generator_diesel_refill' WHERE schedule_type = 'manhole';

-- Re-add constraint with the correct allowed values
ALTER TABLE maintenance_schedules
    ADD CONSTRAINT maintenance_schedules_schedule_type_check
    CHECK (schedule_type IN ('routine_drive', 'repeater_site_visit', 'generator_diesel_refill'));
