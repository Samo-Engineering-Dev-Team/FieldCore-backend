-- Migration 0025: Seed Terraco RHS primary sites
--
-- Inserts 4 Terraco data-centre / hosting sites that are frequent
-- destinations for Remote Hand Support tasks.
--
-- Region values use the PostgreSQL native enum labels (uppercase):
--   GAUTENG, KZN, WESTERN_CAPE
--
-- Coordinates sourced from Google Maps / Terraco site listings.
-- Script is idempotent — skips rows where name already exists.

INSERT INTO sites (id, name, region, address, location, geofence_radius, created_at, updated_at)
VALUES

(gen_random_uuid(),
 'Terraco Isando',
 'GAUTENG',
 '5 Brewery Street, Isando, Johannesburg, Gauteng',
 ST_SetSRID(ST_MakePoint(28.198020, -26.138000), 4326),
 200,
 now(), now()),

(gen_random_uuid(),
 'Terraco Riverhorse',
 'KZN',
 'Riverhorse Close, Riverhorse Valley, Newlands, Durban, KwaZulu-Natal',
 ST_SetSRID(ST_MakePoint(30.992610, -29.779300), 4326),
 200,
 now(), now()),

(gen_random_uuid(),
 'Terraco Brackenfell',
 'WESTERN_CAPE',
 '57 Tiber Road, Brackengate 2, Brackenfell, Cape Town, Western Cape',
 ST_SetSRID(ST_MakePoint(18.679360, -33.907417), 4326),
 200,
 now(), now()),

(gen_random_uuid(),
 'Terraco Rondebosch',
 'WESTERN_CAPE',
 'Great Westerford Building, Main Road, Rondebosch, Cape Town, Western Cape',
 ST_SetSRID(ST_MakePoint(18.464900, -33.971200), 4326),
 200,
 now(), now())

ON CONFLICT DO NOTHING;

-- Verification — run after INSERT to confirm 4 Terraco rows:
--
-- SELECT id, name, region,
--        ST_Y(location::geometry) AS latitude,
--        ST_X(location::geometry) AS longitude
-- FROM   sites
-- WHERE  name LIKE 'Terraco%'
-- ORDER  BY name;
