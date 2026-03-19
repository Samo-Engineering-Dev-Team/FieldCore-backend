-- Migration 0018: Create maintenance_schedules and route_patrols tables
-- Supports Phase 2: recurring maintenance scheduling (Annexure A/B)
-- and route patrol / surveillance tracking (SLA clause: weekly patrols)

CREATE TABLE maintenance_schedules (
  id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id                 UUID        NOT NULL REFERENCES sites(id),
  schedule_type           VARCHAR(30) NOT NULL
    CHECK (schedule_type IN ('fibre_route','manhole','facility','transmission')),
  frequency               VARCHAR(20) NOT NULL
    CHECK (frequency IN ('weekly','monthly','quarterly')),
  assigned_technician_id  UUID        REFERENCES technicians(id),
  is_active               BOOLEAN     NOT NULL DEFAULT TRUE,
  last_run_at             TIMESTAMPTZ,
  next_due_at             TIMESTAMPTZ NOT NULL,
  notes                   TEXT,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at              TIMESTAMPTZ
);

CREATE INDEX idx_maintenance_schedules_site     ON maintenance_schedules(site_id)      WHERE deleted_at IS NULL;
CREATE INDEX idx_maintenance_schedules_due      ON maintenance_schedules(next_due_at)  WHERE deleted_at IS NULL AND is_active = TRUE;
CREATE INDEX idx_maintenance_schedules_tech     ON maintenance_schedules(assigned_technician_id) WHERE deleted_at IS NULL;

CREATE TABLE route_patrols (
  id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  technician_id      UUID        NOT NULL REFERENCES technicians(id),
  site_id            UUID        REFERENCES sites(id),
  route_segment      VARCHAR(200) NOT NULL,
  patrol_date        TIMESTAMPTZ NOT NULL,
  weather_conditions VARCHAR(100),
  anomalies_found    BOOLEAN     NOT NULL DEFAULT FALSE,
  anomaly_details    TEXT,
  -- JSONB array of {url, lat, lon, altitude, speed, captured_at, address}
  photos             JSONB,
  seacom_attested    BOOLEAN     NOT NULL DEFAULT FALSE,
  attested_at        TIMESTAMPTZ,
  attested_by        VARCHAR(200),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at         TIMESTAMPTZ
);

CREATE INDEX idx_route_patrols_technician ON route_patrols(technician_id)  WHERE deleted_at IS NULL;
CREATE INDEX idx_route_patrols_date       ON route_patrols(patrol_date DESC) WHERE deleted_at IS NULL;
CREATE INDEX idx_route_patrols_site       ON route_patrols(site_id)        WHERE deleted_at IS NULL;
