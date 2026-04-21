-- Migration 0041: tenant-isolation rollout support.
-- Idempotent DDL only: no production data is mutated here.

CREATE TABLE IF NOT EXISTS tenants (
    id          VARCHAR(128) PRIMARY KEY,
    slug        VARCHAR(128) NOT NULL UNIQUE,
    name        VARCHAR(160) NOT NULL,
    status      VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE',
    archived_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    deleted_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_tenants_status
    ON tenants(status);

CREATE TABLE IF NOT EXISTS tenant_operation_logs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     VARCHAR(128) NOT NULL,
    operation     VARCHAR(32)  NOT NULL,
    dry_run       BOOLEAN      NOT NULL DEFAULT TRUE,
    actor_user_id UUID,
    status        VARCHAR(32)  NOT NULL DEFAULT 'completed',
    message       TEXT,
    details       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_tenant_operation_logs_tenant_created
    ON tenant_operation_logs(tenant_id, created_at);

CREATE INDEX IF NOT EXISTS ix_tenant_operation_logs_operation
    ON tenant_operation_logs(operation);

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(128);

ALTER TABLE webhooks
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(128);

ALTER TABLE system_settings
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(128);

CREATE INDEX IF NOT EXISTS ix_users_tenant_id
    ON users(tenant_id)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_webhooks_tenant_event_active
    ON webhooks(tenant_id, event_type, is_active);

CREATE INDEX IF NOT EXISTS ix_system_settings_tenant_key
    ON system_settings(tenant_id, key);

ALTER TABLE system_settings
    DROP CONSTRAINT IF EXISTS system_settings_key_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_system_settings_global_key
    ON system_settings(key)
    WHERE tenant_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_system_settings_tenant_key
    ON system_settings(tenant_id, key)
    WHERE tenant_id IS NOT NULL;

CREATE OR REPLACE VIEW v_tenant_scope_backfill_status AS
SELECT 'unscoped_active_users_non_platform' AS check_name,
       COUNT(*)::BIGINT AS row_count
FROM users
WHERE tenant_id IS NULL
  AND deleted_at IS NULL
  AND LOWER(role::text) NOT IN ('admin', 'super_admin')
UNION ALL
SELECT 'unscoped_webhooks',
       COUNT(*)::BIGINT
FROM webhooks
WHERE tenant_id IS NULL
UNION ALL
SELECT 'global_system_settings',
       COUNT(*)::BIGINT
FROM system_settings
WHERE tenant_id IS NULL
UNION ALL
SELECT 'orphan_user_tenant_refs',
       COUNT(*)::BIGINT
FROM users u
LEFT JOIN tenants t ON t.id = u.tenant_id
WHERE u.tenant_id IS NOT NULL
  AND t.id IS NULL
UNION ALL
SELECT 'orphan_webhook_tenant_refs',
       COUNT(*)::BIGINT
FROM webhooks w
LEFT JOIN tenants t ON t.id = w.tenant_id
WHERE w.tenant_id IS NOT NULL
  AND t.id IS NULL
UNION ALL
SELECT 'orphan_setting_tenant_refs',
       COUNT(*)::BIGINT
FROM system_settings s
LEFT JOIN tenants t ON t.id = s.tenant_id
WHERE s.tenant_id IS NOT NULL
  AND t.id IS NULL;

CREATE OR REPLACE VIEW v_tenant_operational_row_counts AS
SELECT tenant_id, table_name, SUM(row_count)::BIGINT AS row_count
FROM (
    SELECT u.tenant_id, 'users' AS table_name, COUNT(*)::BIGINT AS row_count
    FROM users u
    WHERE u.tenant_id IS NOT NULL
    GROUP BY u.tenant_id
    UNION ALL
    SELECT u.tenant_id, 'technicians', COUNT(*)::BIGINT
    FROM technicians tech
    JOIN users u ON u.id = tech.user_id
    WHERE u.tenant_id IS NOT NULL
    GROUP BY u.tenant_id
    UNION ALL
    SELECT u.tenant_id, 'tasks', COUNT(*)::BIGINT
    FROM tasks task
    JOIN technicians tech ON tech.id = task.technician_id
    JOIN users u ON u.id = tech.user_id
    WHERE u.tenant_id IS NOT NULL
    GROUP BY u.tenant_id
    UNION ALL
    SELECT u.tenant_id, 'incidents', COUNT(*)::BIGINT
    FROM incidents incident
    JOIN technicians tech ON tech.id = incident.technician_id
    JOIN users u ON u.id = tech.user_id
    WHERE u.tenant_id IS NOT NULL
    GROUP BY u.tenant_id
    UNION ALL
    SELECT u.tenant_id, 'reports', COUNT(*)::BIGINT
    FROM reports report
    JOIN technicians tech ON tech.id = report.technician_id
    JOIN users u ON u.id = tech.user_id
    WHERE u.tenant_id IS NOT NULL
    GROUP BY u.tenant_id
    UNION ALL
    SELECT tenant_id, 'webhooks', COUNT(*)::BIGINT
    FROM webhooks
    WHERE tenant_id IS NOT NULL
    GROUP BY tenant_id
    UNION ALL
    SELECT tenant_id, 'system_settings', COUNT(*)::BIGINT
    FROM system_settings
    WHERE tenant_id IS NOT NULL
    GROUP BY tenant_id
) counts
GROUP BY tenant_id, table_name;
