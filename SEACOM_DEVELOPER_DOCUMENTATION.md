# Seacom Developer Documentation

## 1. Scope
This guide is for engineers working on the `seacom-app-backend` codebase.

## 2. Stack
- Python 3.11+
- FastAPI
- SQLModel / SQLAlchemy
- PostgreSQL + PostGIS
- Optional Redis for presence heartbeat storage
- `uv` for environment and dependency management

## 3. Project Structure
- `app/main.py`: FastAPI app entrypoint and middleware setup.
- `app/api/`: REST API routers.
- `app/services/`: business logic layer.
- `app/models/`: SQLModel entities.
- `app/database/`: connection/session handling.
- `app/core/`: settings, security, middleware, shared utilities.
- `app/graphql/`: GraphQL schema support (currently not mounted in `main.py`).
- `scripts/`: SQL migrations and operational utilities.

## 4. Local Development Workflow
1. Install dependencies:

```bash
uv sync
```

2. Configure environment:

```bash
cp .env.example .env
```

3. Start local services:

```bash
docker compose up -d postgres-experimental redis
```

4. Apply SQL scripts:

```bash
powershell -Command "Get-ChildItem scripts\\00*.sql | Sort-Object Name | ForEach-Object { psql -h localhost -p 5433 -U postgres -d seacom_experimental_db -f $_.FullName }"
```

5. Run API:

```bash
uv run uvicorn app.main:app --reload
```

6. Run tests:

```bash
uv run pytest -q
```

## 5. Routing Model
- Root API prefix: `/api`
- Version prefix: `/v1`
- Effective route base: `/api/v1/*`

Routes are composed in:
- `app/api/__init__.py`
- `app/api/v1/__init__.py`

## 6. Configuration
`app/core/settings.py` reads environment variables via `AppSettings`.

Critical configuration:
- `JWT_SECRET_KEY` must be set and at least 32 chars.
- `ALLOWED_ORIGINS` is comma-separated.
- `PRESENCE_BACKEND` accepts `db` or `redis`.
- SMTP values are optional unless outbound email/reporting is needed.

## 7. Database and Migration Policy
- SQL migrations live in `scripts/` with ordered numeric prefixes (`0008_...`, `0009_...`).
- New migration files must be additive and idempotent where possible.
- Do not edit historical migration files that were already applied in shared environments.
- If you need corrective migrations, add a new file with the next sequence number.

Known note:
- There are two `0021_*` scripts in the current history. Keep their apply order explicit in runbooks and do not rename historical files retroactively in shared environments.

## 8. Script Folder Hygiene
- Active scripts remain in `scripts/`.
- Deprecated placeholders and ad-hoc scripts should move to `scripts/archive/`.
- Keep runtime-required scripts in place:
  - `scripts/init-postgis.sql`
  - `scripts/fix_trigger.sql`
  - migration chain used by deployed environments

## 9. Coding and Review Expectations
- Keep API changes backward-compatible unless versioned.
- Add tests for behavior changes where practical.
- Prefer explicit service-layer logic over bloated route handlers.
- Document operational impact when changing scripts or env variables.
