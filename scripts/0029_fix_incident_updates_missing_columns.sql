-- Migration 0029: Add missing columns to incident_updates
-- The original migration 0017 omitted updated_at (required by BaseDB)
-- and sent_by_name (denormalised display field on the FaultUpdate model).

ALTER TABLE incident_updates
  ADD COLUMN IF NOT EXISTS updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD COLUMN IF NOT EXISTS sent_by_name VARCHAR(200) NOT NULL DEFAULT '';
