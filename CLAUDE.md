# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run dev server (with hot reload)
uv run uvicorn app.main:app --reload

# Run tests
uv run pytest -q

# Run a single test file
uv run pytest tests/test_presence.py -q

# Run a single test by name
uv run pytest tests/test_presence.py::test_function_name -q

# Start local infrastructure (PostgreSQL/PostGIS on port 5433, Redis on 6379)
docker compose up -d postgres redis

# Apply SQL migrations (Linux)
ls scripts/00*.sql | sort | xargs -I {} psql -h localhost -p 5433 -U postgres -d seacom_experimental_db -f {}
```

## Architecture

### Request flow

```
HTTP request
  → CORS middleware (outermost)
  → DebugMiddleware
  → SlowAPI rate limiter
  → app/api/__init__.py  (prefix: /api)
  → app/api/v1/__init__.py  (prefix: /v1)
  → route handler (app/api/v1/<resource>.py)
  → service function (app/services/<resource>.py)
  → SQLModel session (app/database/database.py)
```

All `/api/v1/` routers except `auth`, `public_system_settings`, and `webhook` have `Depends(get_current_user)` applied at the router level in `app/api/v1/__init__.py`.

### Layer responsibilities

| Layer | Location | Purpose |
|-------|----------|---------|
| Routes | `app/api/v1/` | HTTP boundary: parse request, call service, return response |
| Services | `app/services/` | Business logic, DB queries, cross-entity operations |
| Models | `app/models/` | SQLModel table classes + Pydantic request/response shapes |
| Core | `app/core/` | Settings, security (JWT/Argon2), rate limiter, debug middleware |
| Database | `app/database/database.py` | `Database` singleton — engine lifecycle, `get_session` DI factory |
| Utils | `app/utils/` | Enums (`enums.py`) and helpers (`funcs.py`, `sla_utils.py`) |

### Models pattern

Every DB table inherits `BaseDB` (`app/models/base.py`), which provides:
- `id: UUID` (primary key, auto-generated)
- `created_at`, `updated_at`, `deleted_at` (timezone-aware)
- `touch()` — updates `updated_at`
- `soft_delete()` — sets `deleted_at`; queries must filter `deleted_at.is_(None)` manually

Each resource typically has four shapes: `Model` (table), `ModelCreate`, `ModelUpdate`, `ModelResponse`.

### Authentication & authorization

- JWT access tokens (1h) + refresh tokens (7d), HS256, Argon2 password hashing
- Passkey/WebAuthn support via `py-webauthn` (`app/services/auth.py`)
- `get_current_user` dependency returns `TokenData` (user_id, role, name, surname, must_change_password)
- Role enforcement lives in `app/services/authorization.py`:
  - `MANAGEMENT_ROLES = (ADMIN, MANAGER, NOC)`
  - `ADMIN_MANAGER_ROLES = (ADMIN, MANAGER)`
  - Use `require_roles()`, `require_management()`, `assert_self_or_roles()` — never inline role checks

### Database

- PostgreSQL 16 with PostGIS 3.4 (geospatial queries on technician locations)
- `Database` class in `app/database/database.py` is a singleton that manages the SQLAlchemy engine
- `Database.get_session()` is the FastAPI dependency (`Session = Annotated[_Session, Depends(Database.get_session)]`)
- `Database.session()` is a context manager for use outside DI (background tasks, services)
- `Database.init()` calls `SQLModel.metadata.create_all()` on startup and applies inline schema fixes for legacy columns

### SQL migrations

Numbered scripts in `scripts/` (e.g., `0008_*.sql`, `0032_*.sql`) must be applied in order after initial setup. `scripts/fix_trigger.sql` and `scripts/0011_enforce_single_active_report_per_task.sql` are critical for report update reliability. Move deprecated scripts to `scripts/archive/` rather than deleting them.

### Presence system

`app/services/presence.py` — `PresenceService` is pluggable:
- `PRESENCE_BACKEND=db` (default): stores heartbeats in the `user_sessions` table
- `PRESENCE_BACKEND=redis` + `REDIS_URL`: uses Redis sorted sets + hashes; automatically falls back to DB on connection failure

### File storage

Attachments are stored in **Supabase Storage** (`app/services/file.py`). Configure `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, and `SUPABASE_STORAGE_BUCKET` in `.env`.

### PDF generation

`app/services/pdf.py` generates reports using Reportlab. Incident reports and diesel/repeater/routine-drive reports each have distinct layouts. Penalty calculations (Annexure H SLA) live in `app/services/penalty_calculator.py`.

### Domain context

This is the SAMO/SEACOM maintenance platform. Key domain concepts:
- **Incident severity**: CRITICAL (on-site 2h), MAJOR (4h), MINOR (next business day), QUERY (20 business days) — from Annexure H
- **SLA milestones**: `responded_at`, `arrived_on_site_at`, `temporarily_restored_at`, `permanently_restored_at` on the `Incident` model
- **Penalty exposure**: 10–30% of quarterly fee (R2.7M) per breach; aggregate cap 20%; SEACOM can terminate after 3 missed faults/quarter
- **Regions**: South African provinces (Gauteng, KZN, Western Cape, etc.) used for technician assignment

### Dev-only endpoints

Set `ALLOW_DEV_ENDPOINTS=true` to mount `app/api/v1/dev_client.py` — never enable in production.

### Tests

Tests live in `tests/`. The `db_session` fixture in `conftest.py` skips any test that uses it (DB tests are disabled during Supabase migration). Tests covering auth, PDF layout, permissions, and presence run without a live DB.
