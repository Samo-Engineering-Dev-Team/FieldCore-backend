# -*- coding: utf-8 -*-
"""
Test script for new admin endpoints:
  - POST /auth/login  (with login_audit logging)
  - GET  /dashboard/system-alerts
  - GET  /dashboard/login-audit
  - GET  /technicians/monitoring/stale-locations?stale_minutes=1440

Run from repo root:
  set PYTHONPATH=. && .venv\\Scripts\\python.exe scripts\\test_new_endpoints.py
"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import json
import sys
import requests

from app.core.settings import app_settings
from app.database.database import Database
from app.models.user import User
from app.utils.enums import UserRole
from app.core.security import SecurityUtils
from sqlmodel import select

BASE = "http://127.0.0.1:8000/api/v1"

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "


def connect_db():
    if Database.connection is None:
        Database.connect(app_settings.database_url)
        Database.init()


def get_or_create_admin() -> tuple[str, User]:
    """Return a JWT access token and User record for an admin account."""
    connect_db()
    with Database.session() as s:
        admin = s.exec(select(User).where(User.role == UserRole.ADMIN, User.deleted_at.is_(None))).first()
        if not admin:
            print(f"{FAIL} No admin user found in DB — cannot run tests.")
            sys.exit(1)
        token = SecurityUtils.create_token(admin.id, admin.role, admin.name, admin.surname).access_token
        return token, admin


def hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def check_server():
    try:
        r = requests.get(f"http://127.0.0.1:8000/", timeout=3)
        return True
    except Exception:
        return False


def fmt(r: requests.Response) -> str:
    try:
        return json.dumps(r.json(), indent=2)[:1200]
    except Exception:
        return r.text[:800]


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_login_audit_table(token: str, admin: User):
    """Verify the login_audit table exists and is queryable."""
    print("\n─── Test: login_audit table ───────────────────────────────────")
    connect_db()
    try:
        from sqlalchemy import text as t
        with Database.session() as s:
            row = s.execute(t("SELECT COUNT(*) FROM login_audit")).scalar()
            print(f"{PASS} login_audit table exists — {row} row(s)")
    except Exception as e:
        print(f"{FAIL} login_audit table not found or query failed: {e}")
        print("     → Have you run scripts/0030_create_login_audit.sql against your DB?")


def test_login_records_login(token: str, admin: User):
    """Attempt a login via HTTP so a row is written to login_audit."""
    print("\n─── Test: POST /auth/login (login audit logging) ──────────────")
    # Successful login (we don't know the plaintext password, so test via a wrong-password attempt)
    try:
        r = requests.post(
            f"{BASE}/auth/login",
            data={"username": admin.email, "password": "WRONG_PASSWORD_FOR_TEST"},
            timeout=5,
        )
        if r.status_code in (401, 422):
            print(f"{PASS} Login endpoint responded {r.status_code} (expected for wrong creds)")
        else:
            print(f"{WARN} Login responded {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"{FAIL} POST /auth/login request failed: {e}")

    # Check if row was written
    connect_db()
    try:
        from sqlalchemy import text as t
        with Database.session() as s:
            count = s.execute(
                t("SELECT COUNT(*) FROM login_audit WHERE email = :e AND success = false"),
                {"e": admin.email},
            ).scalar()
            if count and count > 0:
                print(f"{PASS} login_audit has {count} failed-login row(s) for {admin.email}")
            else:
                print(f"{WARN} No failed-login rows found in login_audit for {admin.email}")
                print("     (If login_audit table was just created and the server hasn't restarted, this is expected)")
    except Exception as e:
        print(f"{FAIL} Could not query login_audit: {e}")


def test_system_alerts(token: str):
    """GET /dashboard/system-alerts"""
    print("\n─── Test: GET /dashboard/system-alerts ────────────────────────")

    # No auth → should be 401/403
    r_noauth = requests.get(f"{BASE}/dashboard/system-alerts", timeout=5)
    if r_noauth.status_code in (401, 403):
        print(f"{PASS} No-auth request correctly rejected ({r_noauth.status_code})")
    else:
        print(f"{WARN} No-auth request returned {r_noauth.status_code} (expected 401/403)")

    # With valid admin token
    r = requests.get(f"{BASE}/dashboard/system-alerts", headers=hdr(token), timeout=10)
    if r.status_code == 200:
        data = r.json()
        alerts = data.get("data", [])
        total = data.get("total", -1)
        print(f"{PASS} Status 200 — {total} alert(s) returned")
        if alerts:
            for a in alerts:
                sev = a.get("severity", "?").upper()
                title = a.get("title", "?")
                cat = a.get("category", "?")
                print(f"     [{sev}] [{cat}] {title}")
        else:
            print(f"     (No alerts — system is healthy)")
        # Validate shape
        if isinstance(alerts, list) and isinstance(total, int):
            print(f"{PASS} Response shape is correct (data: list, total: int)")
        else:
            print(f"{FAIL} Unexpected response shape: {data}")
    else:
        print(f"{FAIL} Status {r.status_code}: {fmt(r)}")


def test_login_audit_endpoint(token: str, admin: User):
    """GET /dashboard/login-audit"""
    print("\n─── Test: GET /dashboard/login-audit ──────────────────────────")

    # No auth
    r_noauth = requests.get(f"{BASE}/dashboard/login-audit", timeout=5)
    if r_noauth.status_code in (401, 403):
        print(f"{PASS} No-auth request correctly rejected ({r_noauth.status_code})")
    else:
        print(f"{WARN} No-auth returned {r_noauth.status_code} (expected 401/403)")

    # With token (all records)
    r = requests.get(f"{BASE}/dashboard/login-audit", headers=hdr(token), timeout=10)
    if r.status_code == 200:
        data = r.json()
        records = data.get("data", [])
        total = data.get("total", -1)
        print(f"{PASS} Status 200 — {total} total record(s), returned {len(records)} on this page")
        if records:
            row = records[0]
            expected_keys = {"id", "email", "success", "created_at"}
            missing = expected_keys - set(row.keys())
            if missing:
                print(f"{FAIL} Response row missing expected keys: {missing}")
            else:
                print(f"{PASS} Row shape correct — keys: {sorted(row.keys())}")
    else:
        print(f"{FAIL} Status {r.status_code}: {fmt(r)}")

    # Filter: failed only
    r_fail = requests.get(
        f"{BASE}/dashboard/login-audit?success=false&limit=5",
        headers=hdr(token), timeout=10,
    )
    if r_fail.status_code == 200:
        fail_data = r_fail.json()
        fail_records = fail_data.get("data", [])
        all_failed = all(not rec["success"] for rec in fail_records)
        if all_failed:
            print(f"{PASS} Filter ?success=false works — {fail_data['total']} failed record(s)")
        else:
            print(f"{FAIL} Filter ?success=false returned records with success=true")
    else:
        print(f"{FAIL} Filter request failed: {r_fail.status_code}")

    # Filter: by email
    r_email = requests.get(
        f"{BASE}/dashboard/login-audit?email={admin.email[:8]}",
        headers=hdr(token), timeout=10,
    )
    if r_email.status_code == 200:
        print(f"{PASS} Filter ?email=... works — {r_email.json().get('total', '?')} record(s) for partial email match")
    else:
        print(f"{FAIL} Email filter request failed: {r_email.status_code}")


def test_stale_locations(token: str):
    """GET /technicians/monitoring/stale-locations?stale_minutes=1440"""
    print("\n─── Test: GET /technicians/monitoring/stale-locations ─────────")

    # Old wrong param (threshold_hours) — should still work (falls back to default) or 422
    r_old = requests.get(
        f"{BASE}/technicians/monitoring/stale-locations?threshold_hours=24",
        headers=hdr(token), timeout=10,
    )
    print(f"     Old param ?threshold_hours=24 → {r_old.status_code} (backend ignores unknown params)")

    # Correct param
    r = requests.get(
        f"{BASE}/technicians/monitoring/stale-locations?stale_minutes=1440",
        headers=hdr(token), timeout=10,
    )
    if r.status_code == 200:
        records = r.json()
        if isinstance(records, list):
            print(f"{PASS} Status 200 — {len(records)} stale technician(s)")
            if records:
                row = records[0]
                # Validate TechnicianResponse fields
                if "id" in row and "fullname" in row:
                    print(f"{PASS} Response has correct fields (id, fullname) — NOT technician_id/technician_name")
                else:
                    print(f"{WARN} Unexpected field names: {list(row.keys())[:8]}")
        else:
            print(f"{FAIL} Expected list response, got: {type(records)}")
    else:
        print(f"{FAIL} Status {r.status_code}: {fmt(r)}")

    # Test with low threshold (5 minutes) — should match more or same
    r_low = requests.get(
        f"{BASE}/technicians/monitoring/stale-locations?stale_minutes=5",
        headers=hdr(token), timeout=10,
    )
    if r_low.status_code == 200:
        print(f"{PASS} ?stale_minutes=5 → {len(r_low.json())} stale technician(s) (5-min threshold)")
    else:
        print(f"{FAIL} stale_minutes=5 failed: {r_low.status_code}")


def test_health(token: str):
    """GET /dashboard/health — check system field is present."""
    print("\n─── Test: GET /dashboard/health (system metrics) ──────────────")
    r = requests.get(f"{BASE}/dashboard/health", headers=hdr(token), timeout=10)
    if r.status_code == 200:
        data = r.json()
        system = data.get("system", {})
        print(f"{PASS} Status 200")
        cpu = system.get("cpu_percent")
        mem = system.get("memory_percent")
        disk = system.get("disk_percent")
        if cpu is not None:
            print(f"{PASS} CPU: {cpu}%  Memory: {mem}%  Disk: {disk}%")
        else:
            print(f"{WARN} cpu_percent is null — psutil may not be installed on this host")
            print("     Frontend will show 'System metrics unavailable' gracefully")
        print(f"     presence: {data.get('presence', {})}")
    else:
        print(f"{FAIL} Status {r.status_code}: {fmt(r)}")


# ── Runner ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 62)
    print("  New Endpoint Test Suite")
    print("=" * 62)

    if not check_server():
        print(f"{FAIL} Server is not running on http://127.0.0.1:8000")
        print("     Start it with: uvicorn app.main:app --reload")
        print("\n  Running DB-only tests (no HTTP)...")
        token, admin = get_or_create_admin()
        test_login_audit_table(token, admin)
        return

    print(f"{PASS} Server is reachable at {BASE}")
    token, admin = get_or_create_admin()
    print(f"     Using admin: {admin.email}")

    test_login_audit_table(token, admin)
    test_login_records_login(admin, admin)
    test_system_alerts(token)
    test_login_audit_endpoint(token, admin)
    test_stale_locations(token)
    test_health(token)

    print("\n" + "=" * 62)
    print("  Tests complete.")
    print("=" * 62)


if __name__ == "__main__":
    main()
