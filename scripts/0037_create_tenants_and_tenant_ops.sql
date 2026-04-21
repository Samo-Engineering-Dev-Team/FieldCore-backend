-- Migration 0037: dedicated tenants table and safe tenant lifecycle logs.
-- Tenants are not operational clients. Existing tenant_id columns reference tenants.id.

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

-- Legacy schema had global uniqueness on key only. Tenant settings need per-tenant keys.
ALTER TABLE system_settings
    DROP CONSTRAINT IF EXISTS system_settings_key_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_system_settings_global_key
    ON system_settings(key)
    WHERE tenant_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_system_settings_tenant_key
    ON system_settings(tenant_id, key)
    WHERE tenant_id IS NOT NULL;
