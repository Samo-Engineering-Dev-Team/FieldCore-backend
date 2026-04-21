-- Migration 0040: add tenant subscription lifecycle state table.
-- Billing provider integration stays mockable; provider-specific IDs live in billing_metadata.

CREATE TABLE IF NOT EXISTS tenant_subscriptions (
    id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    deleted_at       TIMESTAMPTZ,
    tenant_id        VARCHAR(128) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    state            TEXT         NOT NULL DEFAULT 'trial',
    billing_metadata JSONB        NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT ck_tenant_subscriptions_state
        CHECK (state IN ('trial', 'active', 'overdue', 'suspended', 'cancelled')),
    CONSTRAINT uq_tenant_subscriptions_tenant_id UNIQUE (tenant_id)
);

CREATE INDEX IF NOT EXISTS ix_tenant_subscriptions_state
    ON tenant_subscriptions(state);

CREATE INDEX IF NOT EXISTS ix_tenant_subscriptions_tenant_state
    ON tenant_subscriptions(tenant_id, state);
