-- Migration 0016: Add severity field and SLA milestone timestamps to incidents
-- Aligns with Annexure H of the SAMO/SEACOM Maintenance Agreement

-- Add dedicated severity field (replaces parsing from description prefix)
ALTER TABLE incidents
  ADD COLUMN severity VARCHAR(10) NOT NULL DEFAULT 'minor'
    CHECK (severity IN ('critical','major','minor','query'));

-- Add the three contractual SLA milestone timestamps
ALTER TABLE incidents ADD COLUMN responded_at            TIMESTAMPTZ;
ALTER TABLE incidents ADD COLUMN arrived_on_site_at      TIMESTAMPTZ;
ALTER TABLE incidents ADD COLUMN temporarily_restored_at TIMESTAMPTZ;
ALTER TABLE incidents ADD COLUMN permanently_restored_at TIMESTAMPTZ;

-- Backfill severity from existing [PRIORITY] description prefixes
UPDATE incidents SET severity = 'critical' WHERE description ILIKE '[CRITICAL]%';
UPDATE incidents SET severity = 'major'    WHERE description ILIKE '[HIGH]%';
UPDATE incidents SET severity = 'minor'
  WHERE description ILIKE '[MEDIUM]%' OR description ILIKE '[LOW]%';
-- All others remain 'minor' (the safe default)

-- Index for fast SLA queries by severity
CREATE INDEX idx_incidents_severity ON incidents(severity) WHERE deleted_at IS NULL;
