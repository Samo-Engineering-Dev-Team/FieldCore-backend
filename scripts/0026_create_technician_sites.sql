-- Migration 0026: Technician route/site assignment (many-to-many)
--
-- Creates a join table so each technician can be assigned to one or more
-- primary service sites (routes), and a single site can be shared by
-- multiple technicians.
--
-- Safe to run multiple times (IF NOT EXISTS guards).

CREATE TABLE IF NOT EXISTS technician_sites (
    technician_id UUID NOT NULL REFERENCES technicians(id) ON DELETE CASCADE,
    site_id       UUID NOT NULL REFERENCES sites(id)       ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (technician_id, site_id)
);

CREATE INDEX IF NOT EXISTS idx_technician_sites_technician ON technician_sites(technician_id);
CREATE INDEX IF NOT EXISTS idx_technician_sites_site       ON technician_sites(site_id);
