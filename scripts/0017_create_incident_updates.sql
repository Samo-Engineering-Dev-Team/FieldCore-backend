-- Migration 0017: Create incident_updates table for fault communication log
-- Tracks the mandatory update intervals per Annexure H:
--   Critical: hourly before restore, daily after
--   Major:    every 2 hours, twice-weekly after

CREATE TABLE incident_updates (
  id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_id  UUID        NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  update_type  VARCHAR(20) NOT NULL CHECK (update_type IN ('phone_call','email','app_update')),
  message      TEXT        NOT NULL,
  sent_by      UUID        NOT NULL REFERENCES users(id),
  is_overdue   BOOLEAN     NOT NULL DEFAULT FALSE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at   TIMESTAMPTZ
);

CREATE INDEX idx_incident_updates_incident ON incident_updates(incident_id)
  WHERE deleted_at IS NULL;
CREATE INDEX idx_incident_updates_created  ON incident_updates(created_at DESC);
