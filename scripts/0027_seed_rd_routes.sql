-- ============================================================
--  Migration 0027: Seed 24 Routine Drive (RD) route segments
--
--  These are the named patrol routes assigned to technicians
--  for Routine Drive tasks as per the SAMO/SEACOM NLD
--  maintenance agreement.
--
--  Each row represents a distinct route segment or metro hub
--  that a technician patrols. GPS coordinates are set at the
--  midpoint of each segment (routes) or at the hub (metros).
--
--  Route abbreviation key:
--    KLIP  = Kliprivier         PARYS = Parys
--    VEN   = Ventersburg        KLE   = Kleinfontein
--    BLOEM = Bloemfontein       RED   = Reddersburg
--    SMI   = Smithfield         PAR   = Parys
--    KRO   = Kroonstad          ALI   = Aliwal North
--    KAA   = Kaalplaas          ESP   = Esperanza
--    GLE   = Glen Cairn         STU   = Stutterheim
--    EL    = East London
--
--  Region values use the PostgreSQL native enum labels (uppercase):
--    GAUTENG, FREE_STATE, EASTERN_CAPE, KZN, NORTHERN_CAPE
--
--  Script is idempotent — ON CONFLICT DO NOTHING prevents
--  duplicate inserts on repeated runs.
-- ============================================================

INSERT INTO sites (id, name, region, address, location, geofence_radius, created_at, updated_at)
VALUES

-- ── GAUTENG / MPUMALANGA ────────────────────────────────────────────────────

(gen_random_uuid(),
 'KLIP–PARYS NLD 9',
 'GAUTENG',
 'NLD Route 9: Kliprivier (Meyerton) to Parys — R59 corridor, Gauteng/Free State',
 ST_SetSRID(ST_MakePoint(27.774, -26.610), 4326),
 500,
 now(), now()),

(gen_random_uuid(),
 'KLIP–Chris Hani JHB Metro',
 'GAUTENG',
 'NLD Metro Route: Kliprivier to Chris Hani (Soweto/JHB), Gauteng',
 ST_SetSRID(ST_MakePoint(27.939, -26.300), 4326),
 500,
 now(), now()),

(gen_random_uuid(),
 'SANSA',
 'GAUTENG',
 'SANSA Ground Station, R512 Hartebeesthoek, North West',
 ST_SetSRID(ST_MakePoint(27.707, -25.890), 4326),
 300,
 now(), now()),

-- ── FREE STATE ──────────────────────────────────────────────────────────────

(gen_random_uuid(),
 'VEN–KLE NLD 9',
 'FREE_STATE',
 'NLD Route 9: Ventersburg to Kleinfontein — N1 corridor, Free State',
 ST_SetSRID(ST_MakePoint(26.923, -28.437), 4326),
 500,
 now(), now()),

(gen_random_uuid(),
 'KLE–BLOEM NLD 9',
 'FREE_STATE',
 'NLD Route 9: Kleinfontein to Bloemfontein — N1 corridor, Free State',
 ST_SetSRID(ST_MakePoint(26.434, -28.934), 4326),
 500,
 now(), now()),

(gen_random_uuid(),
 'BLOEM IS Metro–KLE NLD Route',
 'FREE_STATE',
 'Bloemfontein IS Metro Hub — NLD Route toward Kleinfontein, Free State',
 ST_SetSRID(ST_MakePoint(26.216, -29.095), 4326),
 300,
 now(), now()),

(gen_random_uuid(),
 'BLOEM–RED NLD 10',
 'FREE_STATE',
 'NLD Route 10: Bloemfontein to Reddersburg — N8/N9 corridor, Free State',
 ST_SetSRID(ST_MakePoint(26.170, -29.365), 4326),
 500,
 now(), now()),

(gen_random_uuid(),
 'SMI–RED NLD 10',
 'FREE_STATE',
 'NLD Route 10: Smithfield to Reddersburg — N9 corridor, Free State',
 ST_SetSRID(ST_MakePoint(26.357, -29.929), 4326),
 500,
 now(), now()),

(gen_random_uuid(),
 'BLOEM IS Metro–RED NLD Route',
 'FREE_STATE',
 'Bloemfontein IS Metro Hub — NLD Route toward Reddersburg, Free State',
 ST_SetSRID(ST_MakePoint(26.175, -29.080), 4326),
 300,
 now(), now()),

(gen_random_uuid(),
 'BLOEM Metro',
 'FREE_STATE',
 'Bloemfontein Metro Hub — Primary and Redundant NLD routes, Free State',
 ST_SetSRID(ST_MakePoint(26.221, -29.117), 4326),
 300,
 now(), now()),

(gen_random_uuid(),
 'PAR–KRO NLD 9',
 'FREE_STATE',
 'NLD Route 9: Parys to Kroonstad — R59 corridor, Free State',
 ST_SetSRID(ST_MakePoint(27.429, -27.244), 4326),
 500,
 now(), now()),

(gen_random_uuid(),
 'KRO–VEN NLD 9',
 'FREE_STATE',
 'NLD Route 9: Kroonstad to Ventersburg — R34 corridor, Free State',
 ST_SetSRID(ST_MakePoint(27.193, -27.844), 4326),
 500,
 now(), now()),

(gen_random_uuid(),
 'Karphone POP–Kroonstad Metro',
 'FREE_STATE',
 'Karphone Point of Presence, Kroonstad Metro, Free State',
 ST_SetSRID(ST_MakePoint(27.247, -27.598), 4326),
 300,
 now(), now()),

-- ── EASTERN CAPE ────────────────────────────────────────────────────────────

(gen_random_uuid(),
 'ALI–SMI NLD 10',
 'EASTERN_CAPE',
 'NLD Route 10: Aliwal North to Smithfield — N9 corridor, Eastern Cape/Free State',
 ST_SetSRID(ST_MakePoint(26.622, -30.458), 4326),
 500,
 now(), now()),

(gen_random_uuid(),
 'ALI–KAA NLD 10',
 'EASTERN_CAPE',
 'NLD Route 10: Aliwal North to Kaalplaas — N9 corridor, Eastern Cape',
 ST_SetSRID(ST_MakePoint(26.764, -30.905), 4326),
 500,
 now(), now()),

(gen_random_uuid(),
 'KAA–ESP NLD 10',
 'EASTERN_CAPE',
 'NLD Route 10: Kaalplaas to Esperanza — N9 corridor, Eastern Cape',
 ST_SetSRID(ST_MakePoint(26.740, -31.330), 4326),
 500,
 now(), now()),

(gen_random_uuid(),
 'ESP–GLE NLD 10',
 'EASTERN_CAPE',
 'NLD Route 10: Esperanza to Glen Cairn — N9 corridor, Eastern Cape',
 ST_SetSRID(ST_MakePoint(26.883, -31.811), 4326),
 500,
 now(), now()),

(gen_random_uuid(),
 'GLE–STU NLD 10',
 'EASTERN_CAPE',
 'NLD Route 10: Glen Cairn to Stutterheim — N6/N2 corridor, Eastern Cape',
 ST_SetSRID(ST_MakePoint(27.263, -32.321), 4326),
 500,
 now(), now()),

(gen_random_uuid(),
 'Queenstown Metro',
 'EASTERN_CAPE',
 'Queenstown (Komani) Metro Hub, Eastern Cape',
 ST_SetSRID(ST_MakePoint(26.869, -31.897), 4326),
 300,
 now(), now()),

(gen_random_uuid(),
 'EL–STU NLD 10',
 'EASTERN_CAPE',
 'NLD Route 10: East London to Stutterheim — N2 corridor, Eastern Cape',
 ST_SetSRID(ST_MakePoint(27.667, -32.794), 4326),
 500,
 now(), now()),

(gen_random_uuid(),
 'EL Metro',
 'EASTERN_CAPE',
 'East London Metro Hub, Eastern Cape',
 ST_SetSRID(ST_MakePoint(27.912, -33.015), 4326),
 300,
 now(), now()),

-- ── KWAZULU-NATAL ───────────────────────────────────────────────────────────

(gen_random_uuid(),
 'Harrismith RD',
 'FREE_STATE',
 'ERF 244, Murray between Percy and Vowe Street, Harrismith, Free State',
 ST_SetSRID(ST_MakePoint(29.128, -28.270), 4326),
 300,
 now(), now()),

(gen_random_uuid(),
 'Estcourt RD',
 'KZN',
 'c/o Alexandra and Keate Street, 99 Alexander Street, Estcourt, KwaZulu-Natal',
 ST_SetSRID(ST_MakePoint(29.865, -29.015), 4326),
 300,
 now(), now()),

-- ── NORTHERN CAPE ───────────────────────────────────────────────────────────

(gen_random_uuid(),
 'Kimberley RD',
 'NORTHERN_CAPE',
 'Kimberley, Northern Cape',
 ST_SetSRID(ST_MakePoint(24.767, -28.732), 4326),
 300,
 now(), now())

ON CONFLICT DO NOTHING;

-- ============================================================
--  Verification — run after INSERT to confirm all 24 rows:
--
--  SELECT id, name, region,
--         ST_Y(location::geometry) AS latitude,
--         ST_X(location::geometry) AS longitude
--  FROM   sites
--  WHERE  name ~ '(NLD|RD|Metro|SANSA|Karphone)'
--  ORDER  BY region, name;
-- ============================================================
