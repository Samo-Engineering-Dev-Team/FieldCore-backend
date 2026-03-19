# Backend Optimization TODO

Status legend: `[x] done` `[~] in progress` `[ ] pending`

## 1) Security and Config Hardening
- [x] Remove hardcoded `SUPABASE_SERVICE_KEY` default from settings.
- [x] Set sane auth defaults (`JWT_TOKEN_EXPIRE_MINUTES`, `JWT_REFRESH_TOKEN_EXPIRE_DAYS`).
- [x] Set sane DB port default (`DB_PORT=5432`).
- [~] Add startup validation for critical env vars by environment (dev/staging/prod).
- [ ] Rotate any leaked Supabase service key and invalidate old key.

## 2) Abuse Protection and API Safety
- [x] Wire SlowAPI limiter into app startup and middleware.
- [x] Add global `429` handler for rate limit errors.
- [x] Stop logging validation request body in global validation handler.
- [~] Audit all endpoints for explicit role-based authorization rules.

## 3) Data Access and Query Performance
- [x] Reduce N+1 in task/report read flows using eager loading.
- [x] Push NOC active cutoff filtering into SQL query.
- [~] Add DB indexes for hot filters (`deleted_at`, status+time composites, presence lookups).
- [ ] Standardize ordering on list endpoints for stable pagination.
- [ ] Consider keyset pagination for heavy dashboard endpoints.

## 4) Presence Reliability and Scalability
- [x] Ensure Redis path only activates when Redis client is reachable.
- [~] Remove Redis O(n) session scans by introducing `user_id -> active session(s)` index key.
- [ ] Add fallback behavior tests when Redis is configured but unavailable.

## 5) Webhooks and External IO
- [x] Reuse one HTTP client per webhook dispatch batch.
- [x] Dispatch webhooks concurrently instead of serial sends.
- [~] Add retry policy with exponential backoff + DLQ/failure counters.
- [ ] Add idempotency key support for webhook payloads.

## 6) Logging and Observability
- [x] Replace startup `print` statements with structured logger calls.
- [~] Replace service-level `print`/traceback with structured logs.
- [ ] Add request correlation ID propagation and include it in logs.
- [ ] Add basic API latency/error metrics (Prometheus/OpenTelemetry).

## 7) Test Coverage and CI Confidence
- [~] Re-enable DB-dependent tests progressively behind dedicated test DB config.
- [ ] Add regression tests for task/report eager loading response paths.
- [ ] Add load-ish tests for dashboard query endpoints.
- [ ] Add integration tests for webhook concurrent delivery path.
