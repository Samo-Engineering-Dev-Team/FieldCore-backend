# Seacom App Documentation

## Purpose
Seacom is an operations platform used by NOC teams, technicians, and managers to coordinate telecom field work, incidents, and SLA-sensitive responses.

This repository contains the backend API and related database scripts.

## Core Capabilities
- Authentication and role-based access control.
- Incident and task lifecycle management.
- Technician assignment and status tracking.
- Maintenance scheduling and route patrol support.
- Report workflows, notifications, and webhook integrations.
- Management dashboard data endpoints.

## Primary Roles
- `ADMIN`: full platform configuration and user administration.
- `MANAGER`: operational oversight and performance monitoring.
- `NOC`: live coordination, escalation, and response management.
- `TECHNICIAN`: task execution, updates, and reporting.

## System Architecture
- API framework: FastAPI.
- Data layer: SQLModel/SQLAlchemy + PostgreSQL.
- Geospatial support: PostGIS.
- Optional presence backend: Redis.
- API style: REST endpoints under `/api/v1/*`.

## Key API Surface (High Level)
- Authentication: login and token management.
- User and role management.
- Technicians, sites, tasks, incidents, and reports.
- Maintenance schedules and route patrol.
- Notifications and webhooks.
- Management dashboard endpoints.
- System settings and presence/session heartbeat.

The exact route registration is defined in:
- `app/api/__init__.py`
- `app/api/v1/__init__.py`

## Runtime Configuration
Primary environment variables are documented in `.env.example`, including:
- Database connection (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`)
- Auth settings (`JWT_*`)
- CORS (`ALLOWED_ORIGINS`)
- Presence backend (`PRESENCE_BACKEND`, `REDIS_URL`)
- SMTP and notification email settings

## Deployment Notes
- For local development, `docker-compose.yml` provides PostGIS + Redis.
- SQL setup/migrations are in `scripts/`.
- Script execution order and classification are documented in `scripts/README.md`.

## Related Documents
- `README.md`: quick start and commands.
- `SEACOM_DEVELOPER_DOCUMENTATION.md`: engineering details.
- `SEACOM_USER_TRAINING_GUIDE.md`: operational user training.
