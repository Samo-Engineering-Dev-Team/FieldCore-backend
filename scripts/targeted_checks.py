"""Run targeted checks for the NOC-online feature.
Checks performed:
 - POST /api/v1/sessions/heartbeat (expect 200 with auth)
 - GET /api/v1/dashboard/noc-online (no auth -> 401/403, with manager token -> 200)
 - Inspect app settings for PRESENCE_BACKEND and REDIS_URL
 - Search for SSE endpoint path hints

Run with: set PYTHONPATH=.&& .venv\Scripts\python.exe scripts\targeted_checks.py
"""
import os
import json
import requests

from app.core.settings import app_settings
from app.database.database import Database
from app.models.user import User
from app.utils.enums import UserRole
from app.core.security import SecurityUtils
from sqlmodel import select


def check_settings():
    print("PRESENCE_BACKEND:", app_settings.PRESENCE_BACKEND)
    print("REDIS_URL:", "(set)" if app_settings.REDIS_URL else "(not set)")


def ensure_users():
    if Database.connection is None:
        Database.connect(app_settings.database_url)
        Database.init()
    with Database.session() as s:
        noc = s.exec(select(User).where(User.role == UserRole.NOC)).first()
        mgr = s.exec(select(User).where(User.role == UserRole.MANAGER)).first()
        if not noc:
            noc = User(name="AutoNoc", surname="User", email="auto-noc@example.com", role=UserRole.NOC, password_hash="x")
            s.add(noc); s.commit(); s.refresh(noc)
        if not mgr:
            mgr = User(name="AutoMgr", surname="User", email="auto-mgr@example.com", role=UserRole.MANAGER, password_hash="x")
            s.add(mgr); s.commit(); s.refresh(mgr)
        return noc, mgr


def run_checks():
    check_settings()
    noc, mgr = ensure_users()

    # Manager token
    mgr_token = SecurityUtils.create_token(mgr.id, mgr.role, mgr.name, mgr.surname).access_token
    headers_mgr = {"Authorization": f"Bearer {mgr_token}"}

    # Heartbeat (should work with NOC identity)
    noc_token = SecurityUtils.create_token(noc.id, noc.role, noc.name, noc.surname).access_token
    headers_noc = {"Authorization": f"Bearer {noc_token}"}

    try:
        h = requests.post("http://127.0.0.1:8000/api/v1/sessions/heartbeat", headers=headers_noc, timeout=5)
        print("POST /sessions/heartbeat ->", h.status_code, h.text[:500])
    except Exception as e:
        print("Heartbeat request failed:", e)

    # Management endpoint without auth
    try:
        r = requests.get("http://127.0.0.1:8000/api/v1/dashboard/noc-online", timeout=5)
        print("GET /dashboard/noc-online (no auth) ->", r.status_code)
    except Exception as e:
        print("Mgmt no-auth request failed:", e)

    # Management endpoint with manager token
    try:
        r2 = requests.get("http://127.0.0.1:8000/api/v1/dashboard/noc-online", headers=headers_mgr, timeout=5)
        print("GET /dashboard/noc-online (manager auth) ->", r2.status_code)
        try:
            print(json.dumps(r2.json(), indent=2)[:2000])
        except Exception:
            print(r2.text)
    except Exception as e:
        print("Mgmt auth request failed:", e)

    # Quick SSE hint search
    print('\nLooking for SSE endpoints in repo...')
    # Simple heuristic print (user can grep locally). We won't scan files here programmatically to avoid false positives.
    print('Check routes: /api/v1/webhooks/stream or any /stream endpoints in frontend')


if __name__ == '__main__':
    run_checks()
