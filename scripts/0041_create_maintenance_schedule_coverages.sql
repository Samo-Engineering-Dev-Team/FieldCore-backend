CREATE TABLE IF NOT EXISTS maintenance_schedule_coverages (
  id UUID PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at TIMESTAMPTZ,
  schedule_id UUID NOT NULL REFERENCES maintenance_schedules(id),
  week_start_at TIMESTAMPTZ NOT NULL,
  week_end_at TIMESTAMPTZ NOT NULL,
  original_technician_id UUID REFERENCES technicians(id),
  assigned_technician_id UUID NOT NULL REFERENCES technicians(id),
  assigned_by_user_id UUID NOT NULL REFERENCES users(id),
  reason VARCHAR(2000),
  cancelled_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_maintenance_schedule_coverages_schedule_week
  ON maintenance_schedule_coverages (schedule_id, week_start_at, week_end_at)
  WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_maintenance_schedule_coverages_assigned_tech
  ON maintenance_schedule_coverages (assigned_technician_id, week_start_at)
  WHERE deleted_at IS NULL AND cancelled_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_maintenance_schedule_coverage_week
  ON maintenance_schedule_coverages (schedule_id, week_start_at, week_end_at)
  WHERE deleted_at IS NULL AND cancelled_at IS NULL;
