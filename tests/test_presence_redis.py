import pytest

from app.core.settings import app_settings
from app.services.presence import PresenceService, _get_redis


@pytest.mark.skipif(not app_settings.REDIS_URL, reason="REDIS_URL not configured")
def test_redis_presence_cycle():
    svc = PresenceService
    redis_client = _get_redis()
    assert redis_client is not None

    user_id = "00000000-0000-0000-0000-000000000000"
    session_id = "redis-test-sid"
    stale_user_id = "00000000-0000-0000-0000-000000000001"
    noc_role_key = svc._role_key("noc")

    def cleanup(test_user_id: str, test_session_id: str) -> None:
        redis_client.delete(svc._user_key(test_user_id))
        redis_client.delete(svc._session_lookup_key(test_session_id))
        for role_name in svc._all_roles():
            redis_client.zrem(svc._role_key(role_name), test_user_id)

    cleanup(user_id, session_id)
    cleanup(stale_user_id, "unused-stale-session")

    try:
        redis_client.zadd(noc_role_key, {stale_user_id: 1})

        meta = svc.upsert_session(user_id=user_id, role="NOC", session_id=session_id)
        assert meta.get("session_id") == session_id
        assert meta.get("role") == "noc"

        hb = svc.heartbeat(user_id=user_id, role="NOC")
        assert hb.get("session_id") == session_id
        assert hb.get("role") == "noc"

        stored = redis_client.hgetall(svc._user_key(user_id))
        assert stored.get("user_id") == user_id
        assert stored.get("session_id") == session_id
        assert redis_client.get(svc._session_lookup_key(session_id)) == user_id
        assert redis_client.zscore(noc_role_key, user_id) is not None

        listed = svc.list_active_noc_operators(cutoff_minutes=60)
        assert isinstance(listed, list)
        assert any(
            row.get("user_id") == user_id and row.get("session_id") == session_id
            for row in listed
        )
        assert redis_client.zscore(noc_role_key, stale_user_id) is None

        svc.deactivate_session(session_id=session_id)
        assert redis_client.hgetall(svc._user_key(user_id)) == {}
        assert redis_client.get(svc._session_lookup_key(session_id)) is None
        assert redis_client.zscore(noc_role_key, user_id) is None

        svc.deactivate_session(session_id=session_id)
    finally:
        cleanup(user_id, session_id)
        cleanup(stale_user_id, "unused-stale-session")
