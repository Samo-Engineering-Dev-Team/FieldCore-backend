"""Presence management with optional Redis backend.

When `app_settings.PRESENCE_BACKEND == 'redis'` and `REDIS_URL` is set, presence
uses Redis sorted-sets + hashes for low-latency heartbeats and pub/sub for events.
Otherwise the code falls back to the persisted SQLModel implementation.
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import json
import time
from uuid import UUID, uuid4

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


def _utc_iso_from_timestamp(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _normalize_role(role: str) -> str:
    return str(role).strip().lower()


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
    _ZKEY_ROLE = "presence:role:{role}"          # sorted set: member=user_id score=last_seen_ts
    _HASH_USER = "presence:user:{user_id}"       # hash: lightweight presence metadata
    _KEY_SESSION_LOOKUP = "presence:sid:{session_id}"  # string: session_id -> user_id

    @classmethod
    def _use_redis(cls) -> bool:
        return (
            app_settings.PRESENCE_BACKEND.lower() == "redis"
            and bool(app_settings.REDIS_URL)
            and _get_redis() is not None
        )

    # -------------------- Redis-backed implementations --------------------
    @classmethod
    def _record_ttl_seconds(cls) -> int:
        return max(int(cls.HEARTBEAT_TTL.total_seconds()) * 2, 1)

    @classmethod
    def _role_key(cls, role: str) -> str:
        return cls._ZKEY_ROLE.format(role=_normalize_role(role))

    @classmethod
    def _user_key(cls, user_id) -> str:
        return cls._HASH_USER.format(user_id=str(user_id))

    @classmethod
    def _session_lookup_key(cls, session_id: str) -> str:
        return cls._KEY_SESSION_LOOKUP.format(session_id=session_id)

    @classmethod
    def _all_roles(cls) -> tuple[str, ...]:
        return tuple(_normalize_role(role) for role in UserRole)

    @classmethod
    def _load_user_meta(cls, r, user_id) -> dict:
        return r.hgetall(cls._user_key(user_id)) or {}

    @classmethod
    def _prune_role(cls, r, role: str) -> None:
        stale_cutoff = int(time.time()) - cls._record_ttl_seconds()
        r.zremrangebyscore(cls._role_key(role), "-inf", stale_cutoff)

    @classmethod
    def _build_meta(
        cls,
        user_id,
        role: str,
        session_id: Optional[str],
        now_ts: int,
        existing: Optional[dict] = None,
        expires_at: Optional[datetime] = None,
    ) -> dict:
        existing = existing or {}
        resolved_session_id = session_id or existing.get("session_id") or str(uuid4())
        meta = {
            "user_id": str(user_id),
            "role": _normalize_role(role),
            "session_id": resolved_session_id,
            "last_seen": _utc_iso_from_timestamp(now_ts),
        }
        if expires_at:
            meta["expires_at"] = expires_at.isoformat()
        elif existing.get("expires_at"):
            meta["expires_at"] = existing["expires_at"]
        if existing.get("fullname"):
            meta["fullname"] = existing["fullname"]
        return meta

    @classmethod
    def _sync_redis_presence(cls, r, meta: dict, previous_meta: Optional[dict] = None) -> dict:
        previous_meta = previous_meta or {}
        user_id = meta["user_id"]
        role = meta["role"]
        previous_role = previous_meta.get("role")
        previous_session_id = previous_meta.get("session_id")
        ttl_seconds = cls._record_ttl_seconds()

        cls._prune_role(r, role)

        pipe = r.pipeline()
        if previous_role and previous_role != role:
            pipe.zrem(cls._role_key(previous_role), user_id)
        if previous_session_id and previous_session_id != meta["session_id"]:
            pipe.delete(cls._session_lookup_key(previous_session_id))
        pipe.hset(cls._user_key(user_id), mapping=meta)
        pipe.expire(cls._user_key(user_id), ttl_seconds)
        pipe.zadd(cls._role_key(role), {user_id: int(time.time())})
        pipe.set(cls._session_lookup_key(meta["session_id"]), user_id, ex=ttl_seconds)
        pipe.execute()
        return meta

    @classmethod
    def _publish_presence_event(cls, event_type: str, data: dict) -> None:
        r = _get_redis()
        try:
            r.publish(
                app_settings.PRESENCE_PUBSUB_CHANNEL,
                json.dumps({"type": event_type, "data": data}),
            )
        except Exception:
            pass

    @classmethod
    def _get_user_fullnames(cls, user_ids: List[str]) -> dict[str, str]:
        uuid_map = {}
        for raw_user_id in user_ids:
            try:
                uuid_map[UUID(str(raw_user_id))] = str(raw_user_id)
            except (TypeError, ValueError):
                continue

        if not uuid_map:
            return {}

        try:
            with Database.session() as s:
                rows = s.exec(select(User).where(User.id.in_(list(uuid_map.keys())))).all()
        except Exception as e:
            LOG.warning("Failed to enrich Redis presence names from DB: {}", e)
            return {}

        return {
            str(user_row.id): f"{user_row.name} {user_row.surname}".strip()
            for user_row in rows
        }

    @classmethod
    def _redis_upsert(cls, user_id, role: str, session_id: str, expires_at: Optional[datetime] = None) -> dict:
        r = _get_redis()
        now_ts = int(time.time())
        user_id = str(user_id)
        normalized_role = _normalize_role(role)
        existing = cls._load_user_meta(r, user_id)
        meta = cls._build_meta(
            user_id=user_id,
            role=normalized_role,
            session_id=session_id,
            now_ts=now_ts,
            existing=existing,
            expires_at=expires_at,
        )
        cls._sync_redis_presence(r, meta, existing)
        cls._publish_presence_event("presence_upsert", meta)
        return meta

    @classmethod
    def _redis_heartbeat(cls, user_id, role: str, session_id: Optional[str] = None) -> dict:
        r = _get_redis()
        now_ts = int(time.time())
        user_id = str(user_id)
        normalized_role = _normalize_role(role)
        existing = cls._load_user_meta(r, user_id)
        meta = cls._build_meta(
            user_id=user_id,
            role=normalized_role,
            session_id=session_id,
            now_ts=now_ts,
            existing=existing,
        )
        cls._sync_redis_presence(r, meta, existing)
        return meta

    @classmethod
    def _redis_deactivate(cls, user_id=None, session_id: Optional[str] = None) -> None:
        r = _get_redis()
        user_id = str(user_id) if user_id else None
        if not user_id and session_id:
            user_id = r.get(cls._session_lookup_key(session_id))
        if not user_id:
            return

        meta = cls._load_user_meta(r, user_id)
        session_ids_to_delete = {
            sid for sid in [session_id, meta.get("session_id")] if sid
        }

        pipe = r.pipeline()
        for role_name in cls._all_roles():
            pipe.zrem(cls._role_key(role_name), user_id)
        pipe.delete(cls._user_key(user_id))
        for sid in session_ids_to_delete:
            pipe.delete(cls._session_lookup_key(sid))
        pipe.execute()

        cls._publish_presence_event(
            "presence_remove",
            {"session_id": meta.get("session_id") or session_id, "user_id": user_id},
        )

    @classmethod
    def _redis_list_active_noc(cls, cutoff_minutes: int = 10) -> List[dict]:
        r = _get_redis()
        cutoff_ts = int(time.time()) - (cutoff_minutes * 60)
        cls._prune_role(r, UserRole.NOC)
        key = cls._role_key(UserRole.NOC)
        members = r.zrangebyscore(key, cutoff_ts, "+inf")
        if not members:
            return []

        pipe = r.pipeline()
        for user_id in members:
            pipe.hgetall(cls._user_key(user_id))
        meta_rows = pipe.execute()

        stale_members = []
        results = []
        for user_id, meta in zip(members, meta_rows):
            if not meta:
                stale_members.append(user_id)
                continue
            if _normalize_role(meta.get("role", "")) != _normalize_role(UserRole.NOC):
                stale_members.append(user_id)
                continue
            results.append({
                "user_id": meta.get("user_id") or str(user_id),
                "fullname": meta.get("fullname") or "",
                "role": meta.get("role"),
                "session_id": meta.get("session_id"),
                "is_active": True,
                "last_seen": meta.get("last_seen"),
            })

        if stale_members:
            r.zrem(key, *stale_members)

        fullnames = cls._get_user_fullnames([row["user_id"] for row in results])
        for row in results:
            row["fullname"] = fullnames.get(row["user_id"], row["fullname"])

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
        role = _normalize_role(role)
        if cls._use_redis():
            try:
                return cls._redis_upsert(user_id, role, session_id, expires_at)
            except Exception as e:
                LOG.warning("Redis presence upsert failed, falling back to DB: {}", e)
        return cls._db_upsert(user_id, role, session_id, expires_at)

    @classmethod
    def heartbeat(cls, user_id, role: str, session_id: Optional[str] = None) -> dict:
        role = _normalize_role(role)
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
