import pytest

from app.core.settings import app_settings
from app.services.presence import PresenceService


@pytest.mark.skipif(not app_settings.REDIS_URL, reason="REDIS_URL not configured")
def test_redis_presence_cycle():
    # This test only runs when REDIS_URL is configured in the environment (integration test)
    # It verifies that Redis path is exercised and returns the expected shape.
    svc = PresenceService
    if not svc._use_redis():
        pytest.skip("Redis is configured but unavailable")

    # create/upsert
    meta = svc.upsert_session(user_id="00000000-0000-0000-0000-000000000000", role="NOC", session_id="redis-test-sid")
    assert meta.get("session_id") == "redis-test-sid"

    # heartbeat should update last_seen
    hb = svc.heartbeat(user_id="00000000-0000-0000-0000-000000000000", role="NOC", session_id="redis-test-sid")
    assert hb.get("session_id") == "redis-test-sid"

    # list active noc operators (may be empty if user not present in user table)
    listed = svc.list_active_noc_operators(cutoff_minutes=60)
    assert isinstance(listed, list)

    # deactivate
    svc.deactivate_session(session_id="redis-test-sid")

    # ensure removal is idempotent
    svc.deactivate_session(session_id="redis-test-sid")
