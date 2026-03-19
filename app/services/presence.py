"""Presence management with optional Redis backend.

When `app_settings.PRESENCE_BACKEND == 'redis'` and `REDIS_URL` is set, presence
uses Redis sorted-sets + hashes for low-latency heartbeats and pub/sub for events.
Otherwise the code falls back to the persisted SQLModel implementation.
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import json
import time

from sqlmodel import select
from sqlalchemy import and_

from app.database.database import Database
from app.models.user_session import UserSession
from app.models.user import User
from app.utils.enums import UserRole
from app.core.settings import app_settings
from loguru import logger as LOG


# lazy import to keep redis optional
_redis_client = None
_redis_retry_after_ts = 0.0

def _get_redis():
    global _redis_client, _redis_retry_after_ts
    if _redis_client:
        return _redis_client
    now_ts = time.time()
    if now_ts < _redis_retry_after_ts:
        return None
    url = app_settings.REDIS_URL
    if not url:
        LOG.warning("REDIS_URL is not set, falling back to DB presence")
        return None
    import redis
    # Try configured URL first; if non-TLS Redis URL is configured, retry with TLS.
    candidate_urls = [url]
    if url.startswith("redis://"):
        candidate_urls.append("rediss://" + url[len("redis://"):])

    for idx, candidate in enumerate(candidate_urls):
        try:
            if idx == 0:
                LOG.info(f"Connecting to Redis at {candidate.split('@')[-1]}...")
            else:
                LOG.info(f"Retrying Redis with TLS at {candidate.split('@')[-1]}...")
            _redis_client = redis.Redis.from_url(
                candidate,
                decode_responses=True,
                socket_connect_timeout=app_settings.PRESENCE_REDIS_CONNECT_TIMEOUT_SECONDS,
                socket_timeout=app_settings.PRESENCE_REDIS_SOCKET_TIMEOUT_SECONDS,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            _redis_client.ping()
            LOG.info("Redis connection successful")
            return _redis_client
        except Exception as e:
            LOG.error(f"Redis connection failed: {e}")
            _redis_client = None
            _redis_retry_after_ts = time.time() + app_settings.PRESENCE_REDIS_RETRY_COOLDOWN_SECONDS

    return None


class PresenceService:
    HEARTBEAT_TTL = timedelta(seconds=app_settings.PRESENCE_REDIS_TTL_SECONDS)

    # Redis key patterns
    _ZKEY_ROLE = "presence:role:{role}"          # sorted set: member=session_id score=last_seen_ts
    _HASH_SESSION = "presence:session:{session_id}"  # hash: metadata json

    @classmethod
    def _use_redis(cls) -> bool:
        return (
            app_settings.PRESENCE_BACKEND.lower() == "redis"
            and bool(app_settings.REDIS_URL)
            and _get_redis() is not None
        )

    # -------------------- Redis-backed implementations --------------------
    @classmethod
    def _redis_upsert(cls, user_id, role: str, session_id: str, expires_at: Optional[datetime] = None) -> dict:
        r = _get_redis()
        now_ts = int(time.time())
        meta = {
            "user_id": str(user_id),
            "role": role,
            "session_id": session_id,
            "last_seen": datetime.utcfromtimestamp(now_ts).isoformat(),
        }
        if expires_at:
            meta["expires_at"] = expires_at.isoformat()
        # store metadata and add to sorted set
        r.hset(cls._HASH_SESSION.format(session_id=session_id), mapping={"meta": json.dumps(meta)})
        r.zadd(cls._ZKEY_ROLE.format(role=role), {session_id: now_ts})
        r.expire(cls._HASH_SESSION.format(session_id=session_id), int(cls.HEARTBEAT_TTL.total_seconds()) * 2)
        # publish event for SSE consumers if needed
        try:
            r.publish(app_settings.PRESENCE_PUBSUB_CHANNEL, json.dumps({"type": "presence_upsert", "data": meta}))
        except Exception:
            pass
        return meta

    @classmethod
    def _redis_heartbeat(cls, user_id, role: str, session_id: Optional[str] = None) -> dict:
        r = _get_redis()
        now_ts = int(time.time())
        # prefer session_id; try to find by user_id otherwise
        if session_id:
            key = cls._HASH_SESSION.format(session_id=session_id)
            meta_json = r.hget(key, "meta")
            if meta_json:
                meta = json.loads(meta_json)
                meta["last_seen"] = datetime.utcfromtimestamp(now_ts).isoformat()
            else:
                meta = {"user_id": str(user_id), "role": role, "session_id": session_id, "last_seen": datetime.utcfromtimestamp(now_ts).isoformat()}
            r.hset(key, mapping={"meta": json.dumps(meta)})
            r.zadd(cls._ZKEY_ROLE.format(role=role), {session_id: now_ts})
            r.expire(key, int(cls.HEARTBEAT_TTL.total_seconds()) * 2)
            return meta

        # find most recent session for user
        pattern = cls._ZKEY_ROLE.format(role=role)
        # scan sorted set for candidates (small sets expected)
        members = r.zrange(pattern, 0, -1)
        for m in members:
            meta_json = r.hget(cls._HASH_SESSION.format(session_id=m), "meta")
            if not meta_json:
                continue
            meta = json.loads(meta_json)
            if meta.get("user_id") == str(user_id):
                meta["last_seen"] = datetime.utcfromtimestamp(now_ts).isoformat()
                r.hset(cls._HASH_SESSION.format(session_id=m), mapping={"meta": json.dumps(meta)})
                r.zadd(pattern, {m: now_ts})
                r.expire(cls._HASH_SESSION.format(session_id=m), int(cls.HEARTBEAT_TTL.total_seconds()) * 2)
                return meta

        # create new session if none found
        import uuid
        sid = str(uuid.uuid4())
        return cls._redis_upsert(user_id, role, sid, None)

    @classmethod
    def _redis_deactivate(cls, user_id=None, session_id: Optional[str] = None) -> None:
        r = _get_redis()
        if session_id:
            meta_key = cls._HASH_SESSION.format(session_id=session_id)
            meta_json = r.hget(meta_key, "meta")
            if meta_json:
                meta = json.loads(meta_json)
                role = meta.get("role")
                r.zrem(cls._ZKEY_ROLE.format(role=role), session_id)
                r.delete(meta_key)
                r.publish(app_settings.PRESENCE_PUBSUB_CHANNEL, json.dumps({"type": "presence_remove", "data": {"session_id": session_id}}))
                return
        if user_id:
            # remove all sessions for user across roles
            # (we only expect a few entries)
            for role in [UserRole.NOC, UserRole.TECHNICIAN, UserRole.MANAGER, UserRole.ADMIN]:
                members = r.zrange(cls._ZKEY_ROLE.format(role=role), 0, -1)
                for m in members:
                    meta_json = r.hget(cls._HASH_SESSION.format(session_id=m), "meta")
                    if not meta_json:
                        continue
                    meta = json.loads(meta_json)
                    if meta.get("user_id") == str(user_id):
                        r.zrem(cls._ZKEY_ROLE.format(role=role), m)
                        r.delete(cls._HASH_SESSION.format(session_id=m))
                        r.publish(app_settings.PRESENCE_PUBSUB_CHANNEL, json.dumps({"type": "presence_remove", "data": {"session_id": m}}))

    @classmethod
    def _redis_list_active_noc(cls, cutoff_minutes: int = 10) -> List[dict]:
        r = _get_redis()
        cutoff_ts = int(time.time()) - (cutoff_minutes * 60)
        key = cls._ZKEY_ROLE.format(role=UserRole.NOC)
        members = r.zrangebyscore(key, cutoff_ts, "+inf")
        results = []
        for m in members:
            meta_json = r.hget(cls._HASH_SESSION.format(session_id=m), "meta")
            if not meta_json:
                continue
            meta = json.loads(meta_json)
            results.append({
                "user_id": meta.get("user_id"),
                "fullname": meta.get("fullname") or "",
                "role": meta.get("role"),
                "session_id": meta.get("session_id"),
                "is_active": True,
                "last_seen": meta.get("last_seen"),
            })
        return results

    # -------------------- DB (fallback) implementations --------------------
    @classmethod
    def _db_upsert(cls, user_id, role: str, session_id: str, expires_at: Optional[datetime] = None) -> dict:
        now = datetime.utcnow()
        with Database.session() as s:
            stmt = select(UserSession).where(UserSession.session_id == session_id)
            existing = s.exec(stmt).first()
            if existing:
                existing.is_active = True
                existing.last_seen = now
                existing.expires_at = expires_at
                existing.touch()
                s.add(existing)
                s.commit()
                return existing.to_public()

            session = UserSession(
                user_id=user_id,
                role=role,
                session_id=session_id,
                is_active=True,
                last_seen=now,
                expires_at=expires_at,
            )
            s.add(session)
            s.commit()
            s.refresh(session)
            return session.to_public()

    @classmethod
    def _db_heartbeat(cls, user_id, role: str, session_id: Optional[str] = None) -> dict:
        now = datetime.utcnow()
        with Database.session() as s:
            if session_id:
                stmt = select(UserSession).where(UserSession.session_id == session_id)
                existing = s.exec(stmt).first()
                if existing:
                    existing.last_seen = now
                    existing.is_active = True
                    s.add(existing)
                    s.commit()
                    return existing.to_public()

            # fallback: find an active session for the user and update it
            stmt = select(UserSession).where(UserSession.user_id == user_id).order_by(UserSession.last_seen.desc())
            existing = s.exec(stmt).first()
            if existing:
                existing.last_seen = now
                existing.is_active = True
                s.add(existing)
                s.commit()
                return existing.to_public()

            # create a new session_id if none exists
            import uuid
            session_id = session_id or str(uuid.uuid4())
            session = UserSession(user_id=user_id, role=role, session_id=session_id, is_active=True, last_seen=now)
            s.add(session)
            s.commit()
            s.refresh(session)
            return session.to_public()

    @classmethod
    def _db_deactivate(cls, user_id=None, session_id: Optional[str] = None) -> None:
        with Database.session() as s:
            if session_id:
                stmt = select(UserSession).where(UserSession.session_id == session_id)
                existing = s.exec(stmt).first()
                if existing:
                    existing.is_active = False
                    existing.touch()
                    s.add(existing)
                    s.commit()
                    return
            if user_id:
                q = select(UserSession).where(UserSession.user_id == user_id, UserSession.is_active == True)
                rows = s.exec(q).all()
                for r in rows:
                    r.is_active = False
                    r.touch()
                    s.add(r)
                s.commit()

    @classmethod
    def _db_list_active_noc_operators(cls, cutoff_minutes: int = 10) -> List[dict]:
        cutoff = datetime.utcnow() - timedelta(minutes=cutoff_minutes)
        with Database.session() as s:
            q = select(UserSession, User).join(User, User.id == UserSession.user_id).where(
                and_(
                    User.role == UserRole.NOC,
                    UserSession.is_active == True,
                    UserSession.last_seen.is_not(None),
                )
            )
            rows = s.exec(q).all()
            results = []
            for session_row, user_row in rows:
                last_seen_val = session_row.last_seen
                # handle legacy string storage
                if isinstance(last_seen_val, str):
                    try:
                        from datetime import datetime as _dt

                        normalized_raw = last_seen_val.strip()
                        if normalized_raw.endswith("Z"):
                            normalized_raw = f"{normalized_raw[:-1]}+00:00"
                        last_seen_val = _dt.fromisoformat(normalized_raw)
                    except Exception:
                        last_seen_val = None
                if isinstance(last_seen_val, datetime) and last_seen_val.tzinfo is not None:
                    # Compare as naive UTC timestamps for consistent cutoff behavior.
                    last_seen_val = last_seen_val.astimezone(timezone.utc).replace(tzinfo=None)

                if not last_seen_val or last_seen_val < cutoff:
                    continue

                results.append({
                    "user_id": str(user_row.id),
                    "fullname": f"{user_row.name} {user_row.surname}",
                    "role": str(user_row.role),
                    "session_id": session_row.session_id,
                    "is_active": bool(session_row.is_active),
                    "last_seen": last_seen_val.isoformat() if last_seen_val else None,
                })
            return results

    # -------------------- Public API (chooses backend) --------------------
    @classmethod
    def upsert_session(cls, user_id, role: str, session_id: str, expires_at: Optional[datetime] = None) -> dict:
        if cls._use_redis():
            try:
                return cls._redis_upsert(user_id, role, session_id, expires_at)
            except Exception as e:
                LOG.warning("Redis presence upsert failed, falling back to DB: {}", e)
        return cls._db_upsert(user_id, role, session_id, expires_at)

    @classmethod
    def heartbeat(cls, user_id, role: str, session_id: Optional[str] = None) -> dict:
        if cls._use_redis():
            try:
                return cls._redis_heartbeat(user_id, role, session_id)
            except Exception as e:
                LOG.warning("Redis presence heartbeat failed, falling back to DB: {}", e)
        return cls._db_heartbeat(user_id, role, session_id)

    @classmethod
    def deactivate_session(cls, user_id=None, session_id: Optional[str] = None) -> None:
        if cls._use_redis():
            try:
                return cls._redis_deactivate(user_id=user_id, session_id=session_id)
            except Exception as e:
                LOG.warning("Redis presence deactivate failed, falling back to DB: {}", e)
        return cls._db_deactivate(user_id=user_id, session_id=session_id)

    @classmethod
    def list_active_noc_operators(cls, cutoff_minutes: int = 10) -> List[dict]:
        if cls._use_redis():
            try:
                return cls._redis_list_active_noc(cutoff_minutes=cutoff_minutes)
            except Exception as e:
                LOG.warning("Redis presence list failed, falling back to DB: {}", e)
        try:
            return cls._db_list_active_noc_operators(cutoff_minutes=cutoff_minutes)
        except Exception as e:
            LOG.warning("DB presence list failed, returning empty list: {}", e)
            return []
