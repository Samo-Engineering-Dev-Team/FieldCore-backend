# Seacom App Backend

Backend API for the Seacom operations platform (FastAPI + PostgreSQL/PostGIS + optional Redis presence backend).

## Documentation Index
- `README.md`: setup and day-to-day commands.
- `SEACOM_APP_DOCUMENTATION.md`: product and operations overview.
- `SEACOM_DEVELOPER_DOCUMENTATION.md`: architecture and engineering workflow.
- `SEACOM_USER_TRAINING_GUIDE.md`: role-based user training.
- `SEACOM_USER_GUIDE_TRAINING_MANUAL.md`: trainer facilitation checklist.
- `scripts/README.md`: script catalog and migration execution order.

## Requirements
- Python 3.11+
- `uv`
- PostgreSQL/PostGIS
- Optional: Redis (for `PRESENCE_BACKEND=redis`)

## Local Setup
1. Install dependencies:

```bash
uv sync
```

2. Create environment file:

```bash
cp .env.example .env
```

3. Start infrastructure (recommended for local):

```bash
docker compose up -d postgres-experimental redis
```

4. Apply SQL scripts (baseline + migrations):

```bash
# Enable PostGIS helpers (safe to re-run)
psql -h localhost -p 5433 -U postgres -d seacom_experimental_db -f scripts/02_enable_postgis.sql

# Run numbered migrations
powershell -Command "Get-ChildItem scripts\\00*.sql | Sort-Object Name | ForEach-Object { psql -h localhost -p 5433 -U postgres -d seacom_experimental_db -f $_.FullName }"
```

5. Start the API:

```bash
uv run uvicorn app.main:app --reload
```

## Useful Endpoints
- API root/docs redirect: `http://localhost:8000/`
- OpenAPI docs: `http://localhost:8000/docs`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

## Testing
Run the backend test suite:

```bash
uv run pytest -q
```

## Notes
- `scripts/fix_trigger.sql` and `scripts/0011_enforce_single_active_report_per_task.sql` are still critical for report update reliability in older environments.
- Keep script cleanup non-destructive: move historical/deprecated scripts to `scripts/archive/` instead of hard-deleting.
