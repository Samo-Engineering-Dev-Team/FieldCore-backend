"""End-to-end site scoping against a real Postgres.

The rest of the site-scoping suite (`test_site_scoping.py`) fakes the session,
so it never exercises the actual SQL — in particular the coverage join and the
`Site.id.in_(...)` filter. This module runs the real routes, real service and
real queries against a throwaway database.

Opt-in. Skipped unless `SCOPE_IT_DB_URL` is set, so `uv run pytest -q` behaves
exactly as before. **Point it at a disposable database only — it drops and
recreates every table.** Never at the Supabase URL in `.env`.

    docker run -d --rm --name fc_scope_test \
      -e POSTGRES_PASSWORD=testpw -e POSTGRES_DB=fc_scope_test \
      -p 5434:5432 postgis/postgis:16-3.4
    docker exec fc_scope_test psql -U postgres -d fc_scope_test \
      -c "CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS pg_trgm;"

    SCOPE_IT_DB_URL=postgresql://postgres:testpw@127.0.0.1:5434/fc_scope_test \
      uv run pytest tests/test_site_scoping_integration.py -q
"""

import os
from datetime import timedelta
from uuid import uuid4

import pytest

DB_URL = os.environ.get("SCOPE_IT_DB_URL")

pytestmark = pytest.mark.skipif(
    not DB_URL, reason="set SCOPE_IT_DB_URL to a disposable Postgres to run these"
)

if DB_URL and "supabase" in DB_URL:  # pragma: no cover - guard rail
    raise RuntimeError("refusing to run destructive integration tests against Supabase")


@pytest.fixture(scope="module")
def seeded():
    """Drop, rebuild and seed the scoping fixtures. Yields ids, client, app."""
    from fastapi.testclient import TestClient
    from sqlmodel import Session, SQLModel

    from app.database.database import Database
    from app.main import fastapi_app
    from app.models import (
        MaintenanceSchedule,
        MaintenanceScheduleCoverage,
        Site,
        Technician,
        TechnicianSite,
        User,
    )
    from app.services.maintenance_schedule import _week_bounds
    from app.utils.enums import Region, SiteType, UserRole

    assert DB_URL  # guaranteed by the module-level skipif
    Database.connect(DB_URL)
    engine = Database.connection
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)

    ids = {
        k: uuid4()
        for k in (
            "assigned_site", "covered_site", "foreign_site",
            "tech_user", "other_tech_user", "noc_user", "orphan_user",
            "tech", "other_tech",
        )
    }

    with Session(engine) as s:
        s.add_all([
            Site(id=ids["assigned_site"], name="Alpha Assigned",
                 region=Region.GAUTENG, site_type=SiteType.TASK_SITE),
            Site(id=ids["covered_site"], name="Bravo Covered",
                 region=Region.GAUTENG, site_type=SiteType.TASK_SITE),
            Site(id=ids["foreign_site"], name="Charlie Foreign",
                 region=Region.KZN, site_type=SiteType.TASK_SITE),
            User(id=ids["tech_user"], name="Tee", surname="Tech",
                 email="tech@example.com", role=UserRole.TECHNICIAN, password_hash="x"),
            User(id=ids["other_tech_user"], name="Otto", surname="Other",
                 email="other@example.com", role=UserRole.TECHNICIAN, password_hash="x"),
            User(id=ids["noc_user"], name="Nia", surname="Noc",
                 email="noc@example.com", role=UserRole.NOC, password_hash="x"),
            User(id=ids["orphan_user"], name="Orph", surname="An",
                 email="orphan@example.com", role=UserRole.TECHNICIAN, password_hash="x"),
        ])
        s.commit()
        s.add_all([
            Technician(id=ids["tech"], user_id=ids["tech_user"],
                       phone="0790000001", id_no="T1"),
            Technician(id=ids["other_tech"], user_id=ids["other_tech_user"],
                       phone="0790000002", id_no="T2"),
        ])
        s.commit()

        # Alpha is the only standing assignment.
        s.add(TechnicianSite(technician_id=ids["tech"], site_id=ids["assigned_site"]))

        week_start, week_end = _week_bounds()

        # Bravo is reachable ONLY through this week's coverage grant.
        live_sched = uuid4()
        s.add(MaintenanceSchedule(
            id=live_sched, site_id=ids["covered_site"], schedule_type="routine_drive",
            frequency="weekly", assigned_technician_id=ids["other_tech"],
            next_due_at=week_start,
        ))
        # Charlie has a grant too, but it expired last week — must stay invisible.
        stale_sched = uuid4()
        s.add(MaintenanceSchedule(
            id=stale_sched, site_id=ids["foreign_site"], schedule_type="routine_drive",
            frequency="weekly", assigned_technician_id=ids["other_tech"],
            next_due_at=week_start,
        ))
        s.commit()

        s.add_all([
            MaintenanceScheduleCoverage(
                schedule_id=live_sched, week_start_at=week_start, week_end_at=week_end,
                original_technician_id=ids["other_tech"],
                assigned_technician_id=ids["tech"],
                assigned_by_user_id=ids["noc_user"], reason="Annual leave cover",
            ),
            MaintenanceScheduleCoverage(
                schedule_id=stale_sched,
                week_start_at=week_start - timedelta(days=7),
                week_end_at=week_end - timedelta(days=7),
                original_technician_id=ids["other_tech"],
                assigned_technician_id=ids["tech"],
                assigned_by_user_id=ids["noc_user"], reason="Last week, already ended",
            ),
        ])
        s.commit()

    yield ids, TestClient(fastapi_app), fastapi_app
    fastapi_app.dependency_overrides.clear()


def _as(app, user_id, role):
    from app.models.auth import TokenData
    from app.services.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: TokenData(
        user_id=user_id, role=role, name="T", surname="U", must_change_password=False
    )


def _names(resp):
    return sorted(item["name"] for item in resp.json())


def test_technician_sees_assigned_plus_current_coverage(seeded):
    from app.utils.enums import UserRole

    ids, client, app = seeded
    _as(app, ids["tech_user"], UserRole.TECHNICIAN)

    resp = client.get("/api/v1/sites/")
    assert resp.status_code == 200
    assert _names(resp) == ["Alpha Assigned", "Bravo Covered"]


def test_expired_coverage_does_not_grant_access(seeded):
    """Charlie has a coverage row, but for last week. It must not count."""
    from app.utils.enums import UserRole

    ids, client, app = seeded
    _as(app, ids["tech_user"], UserRole.TECHNICIAN)

    assert client.get(f"/api/v1/sites/{ids['foreign_site']}").status_code == 403


def test_search_is_scoped(seeded):
    from app.utils.enums import UserRole

    ids, client, app = seeded
    _as(app, ids["tech_user"], UserRole.TECHNICIAN)

    # "orei" matches only "Charlie Foreign" — a leak would surface here.
    assert _names(client.get("/api/v1/sites/search", params={"q": "orei"})) == []
    # In-scope search still returns results.
    assert "Bravo Covered" in _names(
        client.get("/api/v1/sites/search", params={"q": "Bravo"})
    )


def test_technician_can_read_assigned_and_covered_sites(seeded):
    from app.utils.enums import UserRole

    ids, client, app = seeded
    _as(app, ids["tech_user"], UserRole.TECHNICIAN)

    assert client.get(f"/api/v1/sites/{ids['assigned_site']}").status_code == 200
    assert client.get(f"/api/v1/sites/{ids['covered_site']}").status_code == 200


def test_noc_is_unrestricted(seeded):
    from app.utils.enums import UserRole

    ids, client, app = seeded
    _as(app, ids["noc_user"], UserRole.NOC)

    assert _names(client.get("/api/v1/sites/")) == [
        "Alpha Assigned", "Bravo Covered", "Charlie Foreign",
    ]
    assert client.get(f"/api/v1/sites/{ids['foreign_site']}").status_code == 200
    # Same query the technician got nothing for — proves that empty result was
    # scoping rather than a broken search.
    assert "Charlie Foreign" in _names(
        client.get("/api/v1/sites/search", params={"q": "orei"})
    )


def test_technician_with_no_assignments_sees_nothing(seeded):
    """The load-bearing case: empty scope must not mean unrestricted."""
    from app.utils.enums import UserRole

    ids, client, app = seeded
    _as(app, ids["other_tech_user"], UserRole.TECHNICIAN)

    assert client.get("/api/v1/sites/").json() == []
    assert client.get(f"/api/v1/sites/{ids['assigned_site']}").status_code == 403


def test_technician_role_without_technician_row_fails_closed(seeded):
    from app.utils.enums import UserRole

    ids, client, app = seeded
    _as(app, ids["orphan_user"], UserRole.TECHNICIAN)

    assert client.get("/api/v1/sites/").json() == []
