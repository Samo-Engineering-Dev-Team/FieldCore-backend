-- ============================================================
--  PRE-PRODUCTION DATABASE CLEAN
--  Removes ALL operational data. Preserves:
--    users            (authentication / login)
--    system_settings  (SLA thresholds, email config, etc.)
--    webhooks         (MS Exchange / notification config)
--
--  Run this in the Supabase SQL Editor.
--  All 16 operational tables are deleted in FK-safe order
--  (most-dependent tables first).
-- ============================================================

BEGIN;

-- ── Tier 1: leaf tables (nothing points TO these) ─────────────

-- Fault communication log
DELETE FROM incident_updates;

-- Incident PDF reports
DELETE FROM incident_reports;

-- Sub-rows of reports
DELETE FROM routine_issues;
DELETE FROM routine_checks;
DELETE FROM routine_inspections;

-- Weekly route-drive observation reports
DELETE FROM route_patrols;

-- Recurring site maintenance schedules
DELETE FROM maintenance_schedules;

-- ── Tier 2: reports + notifications ───────────────────────────

-- Field reports (Repeater, Diesel, etc.)
DELETE FROM reports;

-- In-app push notifications
DELETE FROM notifications;

-- Pending site-access requests
DELETE FROM access_requests;

-- Presence / online sessions
DELETE FROM user_sessions;

-- ── Tier 3: tasks + incidents ─────────────────────────────────

-- Tasks (must come before incidents if tasks.incident_id FK exists)
DELETE FROM tasks;

-- Faults / incidents
DELETE FROM incidents;

-- ── Tier 4: reference / profile data ──────────────────────────

-- Technician profiles (linked to users — re-add via admin after clean)
DELETE FROM technicians;

-- Fibre sites / repeater locations
DELETE FROM sites;

-- Clients (SEACOM, etc.)
DELETE FROM clients;

-- ── Preserved: users, system_settings, webhooks ───────────────
-- (no DELETE statements for these)

COMMIT;

-- ============================================================
--  After running this script:
--  1. Log in as admin — your account still exists.
--  2. Recreate technician profiles (Admin → Technicians).
--  3. Recreate sites (Admin → Sites).
--  4. Reassign maintenance schedules per technician.
--  5. Run migration 0023 if not yet applied:
--       scripts/0023_fix_maintenance_schedule_constraint.sql
-- ============================================================
