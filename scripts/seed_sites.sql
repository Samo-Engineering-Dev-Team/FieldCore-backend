-- ============================================================
--  SEACOM / FibreCo Site Seed  (Annexure F — highlighted sites)
--
--  Inserts the 19 physical infrastructure sites SAMO maintains:
--    • NLD 9  : Klip → Aliwal North        (8 repeater sites)
--    • NLD 10 : Kaalplas → Stutterheim     (5 repeater sites)
--    • NLD 1  : Heidelberg, Harrismith, Estcourt (N3 facilities)
--    • NLD 2  : Klerksdorp, Kimberley
--    • WACS   : Yzerfontein
--
--  Coordinates sourced from SAMO Agreement Annexure F.
--  Script is idempotent — skips rows where name already exists.
--  Region values use PostgreSQL enum labels (Python enum .name):
--    GAUTENG, FREE_STATE, EASTERN_CAPE, KZN, NORTH_WEST, NORTHERN_CAPE, WESTERN_CAPE
--
--  Run in the Supabase SQL Editor AFTER enabling PostGIS
--  (scripts/02_enable_postgis.sql) and creating the sites table.
-- ============================================================

INSERT INTO sites (id, name, region, address, location, geofence_radius, created_at, updated_at)
VALUES

-- ── NLD 9 ─────────────────────────────────────────────────────────────────
-- JHB → Bloemfontein backbone repeater sites
(gen_random_uuid(),
 'Klip',
 'GAUTENG',
 'Corner of R554 and N1, Vereeniging, Gauteng',
 ST_SetSRID(ST_MakePoint(27.936661, -26.330790), 4326),
 250,
 now(), now()),

(gen_random_uuid(),
 'Parys',
 'FREE_STATE',
 '13km from Parys on R59 / S164 Gravel Road, towards Sasolburg, Free State',
 ST_SetSRID(ST_MakePoint(27.610800, -26.889600), 4326),
 300,
 now(), now()),

(gen_random_uuid(),
 'Kroonstad',
 'FREE_STATE',
 'About 5km north of North Road on Vredefort Road R703, Kroonstad, Free State',
 ST_SetSRID(ST_MakePoint(27.247200, -27.598400), 4326),
 300,
 now(), now()),

(gen_random_uuid(),
 'Ventersburg',
 'FREE_STATE',
 '2 Pienaar Street, Ventersburg, Free State',
 ST_SetSRID(ST_MakePoint(27.138100, -28.089800), 4326),
 200,
 now(), now()),

(gen_random_uuid(),
 'Kleinfontein',
 'FREE_STATE',
 '2km north of Verkeerdevlei Toll Plaza, west side of N1, Free State',
 ST_SetSRID(ST_MakePoint(26.708281, -28.783253), 4326),
 300,
 now(), now()),

(gen_random_uuid(),
 'Reddersburg',
 'FREE_STATE',
 'The Farm Elim 159, Reddursburg Road, on N6, Free State',
 ST_SetSRID(ST_MakePoint(26.179981, -29.645410), 4326),
 300,
 now(), now()),

(gen_random_uuid(),
 'Smithfield',
 'FREE_STATE',
 'Cnr Douglas and Doctor Street, Smithfield, Free State',
 ST_SetSRID(ST_MakePoint(26.534567, -30.211681), 4326),
 200,
 now(), now()),

(gen_random_uuid(),
 'Aliwal North',
 'EASTERN_CAPE',
 'Aliwal North High School, Somerset Street, Aliwal North, Eastern Cape',
 ST_SetSRID(ST_MakePoint(26.708590, -30.704090), 4326),
 200,
 now(), now()),

-- ── NLD 10 ────────────────────────────────────────────────────────────────
-- Bloemfontein → East London backbone repeater sites
(gen_random_uuid(),
 'Kaalplas',
 'EASTERN_CAPE',
 '169 Plessies Kraal, Aliwal North Road, Eastern Cape',
 ST_SetSRID(ST_MakePoint(26.818672, -31.105719), 4326),
 300,
 now(), now()),

(gen_random_uuid(),
 'Esperanza',
 'EASTERN_CAPE',
 'Farm 184, Woodhouse Road, Eastern Cape',
 ST_SetSRID(ST_MakePoint(26.661660, -31.553580), 4326),
 300,
 now(), now()),

(gen_random_uuid(),
 'Queenstown',
 'EASTERN_CAPE',
 'LCOM, Tylden, Queenstown, Eastern Cape',
 ST_SetSRID(ST_MakePoint(26.869127, -31.893984), 4326),
 200,
 now(), now()),

(gen_random_uuid(),
 'Glencairn',
 'EASTERN_CAPE',
 'The Farm Glen, Farm 290, Queenstown Road, Eastern Cape',
 ST_SetSRID(ST_MakePoint(27.104660, -32.068340), 4326),
 300,
 now(), now()),

(gen_random_uuid(),
 'Stutterheim',
 'EASTERN_CAPE',
 'Cnr Muller Street and Reis Avenue, Stutterheim, Eastern Cape',
 ST_SetSRID(ST_MakePoint(27.421761, -32.573611), 4326),
 200,
 now(), now()),

-- ── NLD 1 / N3 Facilities ─────────────────────────────────────────────────
-- SEACOM-owned fixed facilities on the N3 highway corridor
(gen_random_uuid(),
 'Heidelberg',
 'GAUTENG',
 'c/o Schoeman and Albert Street, Heidelberg, Gauteng',
 ST_SetSRID(ST_MakePoint(28.358789, -26.510636), 4326),
 150,
 now(), now()),

(gen_random_uuid(),
 'Harrismith',
 'FREE_STATE',
 'ERF 244, Murray between Percy and Vowe Street, Harrismith, Free State',
 ST_SetSRID(ST_MakePoint(29.127553, -28.269833), 4326),
 150,
 now(), now()),

(gen_random_uuid(),
 'Estcourt',
 'KZN',
 'c/o Alexandra and Keate Street, 99 Alexander Street, Estcourt, KwaZulu-Natal',
 ST_SetSRID(ST_MakePoint(29.865094, -29.015308), 4326),
 150,
 now(), now()),

-- ── NLD 2 ─────────────────────────────────────────────────────────────────
-- Klerksdorp–Kimberley corridor
(gen_random_uuid(),
 'Klerksdorp',
 'NORTH_WEST',
 'Pienaar Street, Klerksdorp, North West',
 ST_SetSRID(ST_MakePoint(26.669000, -26.855000), 4326),
 150,
 now(), now()),

(gen_random_uuid(),
 'Kimberley',
 'NORTHERN_CAPE',
 'ERF 244, Murray between Percy and Vowe Street, Kimberley, Northern Cape',
 ST_SetSRID(ST_MakePoint(24.767400, -28.732300), 4326),
 150,
 now(), now()),

-- ── WACS / Cape Town ──────────────────────────────────────────────────────
-- West Africa Cable System landing station
(gen_random_uuid(),
 'Yzerfontein (WACS)',
 'WESTERN_CAPE',
 'Yzerfontein Beach Road, Yzerfontein, Western Cape',
 ST_SetSRID(ST_MakePoint(18.202014, -33.341447), 4326),
 150,
 now(), now())

ON CONFLICT DO NOTHING;

-- ============================================================
--  Verification — run after the INSERT to confirm all 19 rows:
--
--  SELECT id, name, region,
--         ST_Y(location::geometry) AS latitude,
--         ST_X(location::geometry) AS longitude
--  FROM   sites
--  ORDER  BY region, name;
-- ============================================================
