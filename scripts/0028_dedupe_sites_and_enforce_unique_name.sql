-- ============================================================
-- Migration 0028: De-duplicate sites and enforce active uniqueness
--
-- Why:
-- - Site seeds can be run multiple times.
-- - Without an active-name uniqueness guard, duplicates can appear.
--
-- What this does:
-- 1) Picks one canonical row per active site (same lower(name)+region)
-- 2) Re-points foreign-key references from duplicate rows to canonical rows
-- 3) Deletes duplicate site rows
-- 4) Adds a unique partial index to prevent future active duplicates
--
-- Safe to run multiple times (idempotent).
-- ============================================================

BEGIN;

DROP TABLE IF EXISTS _site_dedup_map;

CREATE TEMP TABLE _site_dedup_map AS
WITH ranked AS (
    SELECT
        s.id,
        s.name,
        s.region,
        row_number() OVER (
            PARTITION BY lower(s.name), s.region
            ORDER BY s.created_at ASC, s.id ASC
        ) AS rn
    FROM sites s
    WHERE s.deleted_at IS NULL
),
keepers AS (
    SELECT
        lower(name) AS normalized_name,
        region,
        id AS keep_id
    FROM ranked
    WHERE rn = 1
),
dupes AS (
    SELECT
        lower(name) AS normalized_name,
        region,
        id AS dup_id
    FROM ranked
    WHERE rn > 1
)
SELECT
    k.keep_id,
    d.dup_id
FROM keepers k
JOIN dupes d
  ON d.normalized_name = k.normalized_name
 AND d.region = k.region;

-- Repoint all known FK references
UPDATE tasks t
SET site_id = m.keep_id
FROM _site_dedup_map m
WHERE t.site_id = m.dup_id;

UPDATE incidents i
SET site_id = m.keep_id
FROM _site_dedup_map m
WHERE i.site_id = m.dup_id;

UPDATE access_requests a
SET site_id = m.keep_id
FROM _site_dedup_map m
WHERE a.site_id = m.dup_id;

UPDATE routine_inspections r
SET site_id = m.keep_id
FROM _site_dedup_map m
WHERE r.site_id = m.dup_id;

UPDATE maintenance_schedules ms
SET site_id = m.keep_id
FROM _site_dedup_map m
WHERE ms.site_id = m.dup_id;

UPDATE route_patrols rp
SET site_id = m.keep_id
FROM _site_dedup_map m
WHERE rp.site_id = m.dup_id;

-- technician_sites has a composite PK, so upsert canonical pairs first
INSERT INTO technician_sites (technician_id, site_id, created_at)
SELECT
    ts.technician_id,
    m.keep_id,
    ts.created_at
FROM technician_sites ts
JOIN _site_dedup_map m
  ON ts.site_id = m.dup_id
ON CONFLICT (technician_id, site_id) DO NOTHING;

DELETE FROM technician_sites ts
USING _site_dedup_map m
WHERE ts.site_id = m.dup_id;

-- Remove duplicate site rows
DELETE FROM sites s
USING _site_dedup_map m
WHERE s.id = m.dup_id;

-- Prevent future active duplicates
CREATE UNIQUE INDEX IF NOT EXISTS uq_sites_active_name_region
ON sites ((lower(name)), region)
WHERE deleted_at IS NULL;

COMMIT;

-- Optional verification:
-- SELECT name, region, COUNT(*) AS c
-- FROM sites
-- WHERE deleted_at IS NULL
-- GROUP BY name, region
-- HAVING COUNT(*) > 1;
