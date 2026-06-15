# Backend Security & Quality Audit — FieldCore (SAMO/SEACOM)

Read-only analysis. No code changed. Scope: FastAPI backend (`app/`), config, Docker, tests, deps.
Line numbers reference the state at audit time.

---

## CRITICAL

- [x] **C1 — Webhook endpoints have no authentication** — `app/api/v1/__init__.py:54` / `app/api/v1/webhook.py:16-42` — `webhook_router` is mounted without `Depends(get_current_user)` (unlike every other resource router). Anyone on the internet can `POST /api/v1/webhooks/` to register a webhook URL, `GET` to list them, and `DELETE` to remove them. This is a Server-Side Request Forgery + data-exfiltration primitive: an attacker registers their own URL and the server will POST internal event payloads (SLA breaches, incidents) to it. **Fix:** add `dependencies=[Depends(get_current_user)]` to the router include and require management role on register/list/delete; validate/allowlist the destination URL host.

- [x] **C2 — CORS allows any origin with credentials** — `app/core/settings.py:99-106` + `app/main.py:101-107` — `allowed_origins` falls back to `["*"]` when `ALLOWED_ORIGINS` is empty (it *is* empty in `.env`), and the middleware sets `allow_credentials=True` with `allow_methods=["*"]`/`allow_headers=["*"]`. Starlette then reflects the caller's `Origin` and returns `Access-Control-Allow-Credentials: true`, effectively trusting every origin for credentialed requests — enables cross-site credential/token theft. **Fix:** never default to `["*"]`; require an explicit origin list in prod and fail closed if unset when credentials are enabled.

- [ ] **C3 — Live production secrets sitting in `.env` in the working tree** — `.env` — contains a real DB password, the Supabase **service_role** key (a JWT that bypasses all RLS and grants full storage/DB access), `JWT_SECRET_KEY`, and SMTP creds. Although `.env` is git-ignored (not in history), these are production-grade secrets shared inside the repo directory; the service_role key in particular is a full-account bypass. **Fix:** rotate all of these now (Supabase service key, DB password, JWT secret, SMTP), move to a secrets manager / platform env vars, and keep only a `.env.example` with placeholders.

- [ ] **C4 — Access-token lifetime is 12 hours with no revocation** — `.env` (`JWT_TOKEN_EXPIRE_MINUTES="720"`) + `app/core/security.py:80-103` — CLAUDE.md states a 1h access token, but config issues 720-minute (12h) access tokens plus 7-day refresh tokens, and there is no logout / token blacklist. A leaked access token is valid for 12h regardless of logout. (Partial mitigation: `get_current_user` rejects tokens issued before `credentials_updated_at`.) **Fix:** drop access TTL to ~15-60m, implement a refresh+revocation mechanism (server-side session/refresh store) and a real logout.

---

## HIGH

- [x] **H1 — Webhook secret leaked in responses** *(fixed alongside C1 via `WebhookResponse`)* — `app/api/v1/webhook.py:16,30` + `app/models/webhook.py` — both endpoints use `response_model=Webhook`, the raw table model which includes the `secret` column. Listing/creating webhooks returns the HMAC secret in plaintext. **Fix:** define a `WebhookResponse` schema that excludes `secret`; never serialize the table model directly.

- [ ] **H2 — Critical paths have no executable test coverage** — `tests/conftest.py:4-6` — the `db_session` fixture calls `pytest.skip(...)`, so every DB-backed test is skipped "while migrating to Supabase." Auth/login, incident creation, task state transitions, and all data-mutation endpoints have no running tests. **Fix:** stand up a disposable Postgres/PostGIS test DB (testcontainers or a CI service) and re-enable the fixture; gate the most security-sensitive flows (login, authz, incident/task mutations) first.

- [ ] **H3 — No security response headers** — `app/main.py:101-111` — only CORS, Debug, and SlowAPI middleware are installed. No HSTS, `X-Content-Type-Options: nosniff`, `X-Frame-Options`/frame-ancestors, or `Referrer-Policy`. **Fix:** add a small middleware (or `secweb`/Starlette middleware) setting these headers; enable HSTS behind TLS.

- [ ] **H4 — Rate limiting only on 3 auth routes** — `app/core/rate_limiter.py` + `app/api/v1/auth.py:26,49,60` — `SlowAPIMiddleware` is installed but `@limiter.limit` is only applied to login/auth endpoints. The rest of the API (file upload, dashboards, mutations) has no throttling, and the limiter keys on `get_remote_address`, which is unreliable behind the proxy unless wired to `X-Forwarded-For`. **Fix:** add sensible default limits to expensive/mutating routes and confirm client-IP resolution through the proxy.

- [ ] **H5 — Dockerfile: runs as root, single-stage, unpinned, no healthcheck** — `Dockerfile` — `build-essential`/`gcc` remain in the final image (larger attack surface), the container runs as root (no `USER`), base image `python:3.12-slim` is not pinned by digest, there is no `HEALTHCHECK`, and `pip install .` ignores `uv.lock` so image builds are **not reproducible** (resolves `>=` deps fresh each build). **Fix:** multi-stage build, create and switch to a non-root user, pin base by digest, install from the lockfile (`uv sync --frozen`), add a `HEALTHCHECK`.

- [ ] **H6 — Non-secret config fails open / silently** — `app/core/settings.py:9-18` — only `JWT_SECRET_KEY` is validated; `DB_*` and `SUPABASE_*` default to `""`. A misconfigured deploy will silently start with no DB creds / no storage and surface confusing 503s instead of failing fast at boot. **Fix:** validate required settings on startup (per environment) and refuse to start when missing.

---

## MEDIUM

- [ ] **M1 — Automated SLA / weekly checkers are disabled** — `app/main.py:23-51,66-77` — the SLA background task is fully commented out, so `app/services/sla_checker.py` and `weekly_checker.py` never run automatically. Given penalty exposure (10-30% of quarterly fee per breach) this is domain-critical: breaches are only detected if someone hits the manual endpoint. **Fix:** run these on a proper scheduler (APScheduler/arq/cron container) rather than an in-process loop, with locking so multiple workers don't double-fire.

- [ ] **M2 — DebugMiddleware consumes the request body without re-injecting it** — `app/core/debug_middleware.py:164-183` — when request logging is enabled it `await request.body()` but the promised "re-create the request body" step is only a comment; downstream handlers then read an empty body, breaking all POST/PATCH/PUT routes. **Fix:** re-wrap `request._receive` after reading, or log via a route-level dependency instead of draining the stream.

- [ ] **M3 — Async webhook routes block the event loop** — `app/api/v1/webhook.py:17-39` + `app/services/webhook.py:72-99` — handlers are `async def` but call synchronous `Database.session()` ORM operations directly on the event loop. (Most of the codebase correctly uses sync `def` handlers, which FastAPI offloads to a threadpool — these few async ones are the exception.) **Fix:** make these handlers sync `def`, or move the blocking DB work to `run_in_threadpool`.

- [ ] **M4 — File upload trusts client-declared content type; path not sanitized** — `app/api/v1/file.py:45-80,141-146` — type validation uses `file.content_type` (caller-controlled header), with no magic-byte sniffing, and `folder` (query) / `file_path` (path) are passed into the storage object path without sanitization. **Fix:** verify the actual file signature, allowlist `folder` values, and normalize/validate paths to prevent traversal in the bucket namespace.

- [ ] **M5 — SSRF in PDF image fetcher** — `app/services/pdf.py:1341-1351` (`_fetch_image_bytes`) — performs `urllib.request.urlopen` on an arbitrary URL with a 12s timeout and no host allowlist; if any stored image URL is attacker-influenced, the server will fetch internal addresses. **Fix:** restrict fetches to the known Supabase host(s); reject private/link-local IP ranges.

- [ ] **M6 — Weak password policy with an upper length cap** — `app/models/user.py:91` — `UserCreate.password` is `min_length=8, max_length=16` with no complexity checks. Capping at 16 chars actively blocks strong passphrases. **Fix:** raise/remove the max (Argon2 handles long inputs), set min ~12, and add basic strength/breach checks.

- [ ] **M7 — Exception text leaked to clients** — `app/api/v1/presence.py:15,25` and `app/api/v1/webhook.py:26` — raise `HTTPException(500, detail=str(e))` / `detail=f"...{str(e)}"`, exposing internal error/stack detail. **Fix:** return a generic message and log the exception server-side.

- [ ] **M8 — Swallowed exceptions hide failures** — `app/api/v1/management_dashboard.py:548-721` (multiple `except Exception: pass`) and broad `except Exception` across ~143 sites — silently dropping errors makes dashboard data quietly wrong and hides bugs. **Fix:** narrow the exception types and at least `LOG.warning` the swallowed error.

- [ ] **M9 — Webhook model is inconsistent with the rest of the schema** — `app/models/webhook.py` — uses an auto-increment `int` PK (everything else is `UUID` via `BaseDB`), does not inherit `BaseDB` (no soft-delete/`updated_at` semantics), and uses naive `datetime.utcnow` defaults. **Fix:** align it with `BaseDB` (UUID PK, soft delete, tz-aware timestamps).

- [ ] **M10 — Many routes return without a `response_model`** — `app/api/` — ~138 `response_model` declarations across ~195 routes, leaving ~57 handlers serializing whatever the service returns (risk of leaking table-model fields). **Fix:** audit handlers without `response_model`, especially any returning ORM models, and add explicit response schemas.

---

## LOW

- [ ] **L1 — Docker build ignores the lockfile** — `Dockerfile:21-23` — covered above under reproducibility; also means CVE-patched pins in `uv.lock` are not what ships. **Fix:** install from `uv.lock`.

- [ ] **L2 — In-process BackgroundTasks for notifications/email** — `app/api/v1/incident.py:33,143,163` — fire-and-forget tasks are lost on restart/crash and don't retry. **Fix:** move to a durable queue (arq/Celery) for email/webhook delivery.

- [ ] **L3 — Deprecated pydantic-settings config style** — `app/core/settings.py:118-119` — uses inner `class Config` instead of `SettingsConfigDict`/`model_config` (pydantic v2). **Fix:** migrate to `model_config = SettingsConfigDict(env_file=".env")`.

- [ ] **L4 — `__import__("datetime").datetime.utcnow()` inline** — `app/api/v1/incident.py:300` — code smell and uses the deprecated naive `utcnow()`. **Fix:** use the project's `app.utils.funcs.utcnow` and a normal import.

- [ ] **L5 — Obsolete `version` key in compose; external frontend build path** — `docker-compose.yml:1,55` — `version: '3.8'` is ignored by modern Compose, and `build: ../seacom-app-frontend` couples this repo to a sibling checkout. **Fix:** drop the version key; document/decouple the frontend service.

- [ ] **L6 — Large binary/agent artifacts in the repo dir** — `ruvector.db` (~1.5MB), `agentdb.rvf`, `app/services/ruvector.db` — tooling databases living next to source. **Fix:** git-ignore and move out of `app/`.

- [ ] **L7 — Health check is buried and does no dependency depth** — `app/api/v1/management_dashboard.py:801` — `/health` sits under an authenticated dashboard prefix and is awkward for load balancers. **Fix:** expose an unauthenticated top-level `/health` (liveness) and a `/ready` that checks DB/Redis.

- [ ] **L8 — Root endpoint returns a playful message in debug** — `app/main.py:118-123` — minor; harmless but undocumented behavior. **Fix:** none required, or standardize.

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 4 |
| HIGH     | 6 |
| MEDIUM   | 10 |
| LOW      | 8 |
| **Total** | **28** |

Top priorities: (1) put auth on the webhook router, (2) fix the wildcard-CORS-with-credentials default, (3) rotate the leaked secrets / stop shipping the Supabase service_role key, (4) re-enable real test coverage for auth and mutation paths.
