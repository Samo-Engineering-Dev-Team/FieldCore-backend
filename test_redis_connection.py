#!/usr/bin/env python
"""Quick Redis smoke test for local development."""
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import os
import sys
import traceback

import redis
from dotenv import load_dotenv


def _load_environment() -> None:
    env_path = Path(__file__).resolve().with_name(".env")
    load_dotenv(dotenv_path=env_path if env_path.exists() else None)


def _mask_redis_url(url: str) -> str:
    parts = urlsplit(url)
    netloc = parts.netloc
    if "@" in netloc:
        auth, host = netloc.split("@", 1)
        if ":" in auth:
            username, _password = auth.split(":", 1)
            auth = f"{username}:***"
        else:
            auth = "***"
        netloc = f"{auth}@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _candidate_urls(url: str) -> list[str]:
    urls = [url]
    if url.startswith("redis://"):
        urls.append("rediss://" + url[len("redis://"):])
    return urls


def main() -> int:
    _load_environment()

    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        print("FAIL: REDIS_URL not set")
        return 1

    print(f"Testing connection to: {_mask_redis_url(redis_url)}")

    last_error = None
    for index, candidate in enumerate(_candidate_urls(redis_url), start=1):
        label = "configured" if index == 1 else "tls-fallback"
        try:
            print(f"Connecting with {label} URL...")
            client = redis.Redis.from_url(
                candidate,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30,
            )

            pong = client.ping()
            print(f"OK: PING response: {pong}")

            success = client.set("test-key", "test-value", ex=30)
            print(f"OK: SET test-key=test-value: {success}")

            value = client.get("test-key")
            print(f"OK: GET test-key: {value}")

            client.delete("test-key")
            print("OK: Cleanup: deleted test-key")

            print("\nOK: Redis connection working")
            return 0
        except Exception as exc:
            last_error = exc
            print(f"FAIL: {label}: {type(exc).__name__}: {exc}")

    if last_error is not None:
        traceback.print_exception(last_error)
    return 2


if __name__ == "__main__":
    sys.exit(main())
