-- Migration 0023: Fix maintenance_schedules schedule_type check constraint
--
-- Migration 0018 created maintenance_schedules with a check constraint that only
-- allowed legacy types: fibre_route, manhole, facility, transmission.
-- Migration 0021 attempted to UPDATE those values to the three mandatory types
-- but did not DROP the constraint first, causing those UPDATEs to fail on
-- databases where 0018 had already been applied.
--
-- This migration is idempotent and safe to run even if 0021 applied correctly.

-- Step 1: Drop the legacy check constraint (safe even if it no longer exists)
ALTER TABLE maintenance_schedules DROP CONSTRAINT IF EXISTS maintenance_schedules_schedule_type_check;

-- Step 2: Ensure scheduled_date column exists (added by 0021, idempotent)
ALTER TABLE maintenance_schedules ADD COLUMN IF NOT EXISTS scheduled_date TIMESTAMPTZ;

-- Step 3: Migrate any remaining rows with old type values
UPDATE maintenance_schedules SET schedule_type = 'routine_drive'           WHERE schedule_type IN ('fibre_route', 'transmission');
UPDATE maintenance_schedules SET schedule_type = 'repeater_site_visit'     WHERE schedule_type = 'facility';
UPDATE maintenance_schedules SET schedule_type = 'generator_diesel_refill' WHERE schedule_type = 'manhole';

-- Step 4: Re-apply constraint with the correct three mandatory types
--         (DROP IF EXISTS first handles case where 0021 already re-added it)
ALTER TABLE maintenance_schedules DROP CONSTRAINT IF EXISTS maintenance_schedules_schedule_type_check;
ALTER TABLE maintenance_schedules
    ADD CONSTRAINT maintenance_schedules_schedule_type_check
    CHECK (schedule_type IN ('routine_drive', 'repeater_site_visit', 'generator_diesel_refill'));
