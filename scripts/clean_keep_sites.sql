-- ============================================================
--  DATABASE CLEAN — KEEP SITES, LOGINS & SLA
--  Removes all operational/transactional data. Preserves:
--    users            (logins / authentication)
--    system_settings  (SLA thresholds, email config, etc.)
--    webhooks         (MS Exchange / notification config)
--    sites            (fibre sites / repeater locations)
--    clients          (SEACOM, Vodacom, etc.)
--
--  Run this in the Supabase SQL Editor.
--  Tables are deleted in FK-safe order (most-dependent first).
-- ============================================================

BEGIN;

-- ── Tier 1: leaf tables ────────────────────────────────────

-- Fault communication updates
DELETE FROM incident_updates;

-- Incident PDF reports
DELETE FROM incident_reports;

-- Sub-rows of field reports
DELETE FROM routine_issues;
DELETE FROM routine_checks;
DELETE FROM routine_inspections;

-- Weekly route-drive observation reports
DELETE FROM route_patrols;

-- Recurring site maintenance schedules
DELETE FROM maintenance_schedules;

-- ── Tier 2: reports, notifications, sessions ───────────────

-- Field reports (Repeater, Diesel, etc.)
DELETE FROM reports;

-- In-app push notifications
DELETE FROM notifications;

-- Pending site-access requests
DELETE FROM access_requests;

-- Presence / online sessions
DELETE FROM user_sessions;

-- Login audit trail
DELETE FROM login_audit;

-- ── Tier 3: tasks & incidents ──────────────────────────────

-- Tasks (references incidents, sites, technicians)
DELETE FROM tasks;

-- Faults / incidents (references sites, technicians, clients)
DELETE FROM incidents;

-- ── Tier 4: technician associations ───────────────────────

-- Technician ↔ Site assignments (FK to both — must go before technicians)
DELETE FROM technician_sites;

-- Technician profiles
DELETE FROM technicians;

-- ── Preserved ─────────────────────────────────────────────
--   sites, clients, users, system_settings, webhooks
-- (no DELETE statements for these)

COMMIT;

-- ============================================================
--  After running this script:
--  1. Log in as admin — your account still exists.
--  2. Sites and clients are intact — no need to recreate.
--  3. Recreate technician profiles (Admin → Technicians).
--  4. Reassign technicians to sites (Admin → Technicians → Sites).
--  5. Reassign maintenance schedules per technician as needed.
-- ============================================================
