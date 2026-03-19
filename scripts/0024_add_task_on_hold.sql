-- Migration 0024: Add on-hold status support to tasks
--
-- Adds two optional columns:
--   hold_reason : free-text reason why the task is on hold (max 500 chars)
--   held_at     : UTC timestamp when the task was put on hold
--
-- The ON_HOLD status value is handled as a plain Python StrEnum string,
-- so no PostgreSQL enum type change is needed.
--
-- Safe to run multiple times (IF NOT EXISTS guards).

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS hold_reason VARCHAR(500);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS held_at     TIMESTAMPTZ;
