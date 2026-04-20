-- Migration 0035: add tenant seat/feature metering + compliance snapshots
-- Notes:
-- - users.tenant_id aligns existing frontend tenant scoping with backend persistence.
-- - tenant_usage_daily stores one row per tenant/date/feature_key.
-- - tenant_compliance_records stores evaluated usage vs entitlement for dashboard reads.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(128);

CREATE INDEX IF NOT EXISTS ix_users_tenant_id
    ON users(tenant_id)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS tenant_feature_usage_events (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at          TIMESTAMPTZ,
    tenant_id           TEXT        NOT NULL,
    feature_key         VARCHAR(120) NOT NULL,
    feature_name        VARCHAR(120),
    usage_quantity      INTEGER     NOT NULL DEFAULT 1,
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    recorded_by_user_id UUID
);

CREATE TABLE IF NOT EXISTS tenant_usage_daily (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at       TIMESTAMPTZ,
    tenant_id        TEXT        NOT NULL,
    usage_date       DATE        NOT NULL,
    feature_key      VARCHAR(120) NOT NULL,
    feature_name     VARCHAR(120) NOT NULL,
    usage_value      INTEGER     NOT NULL DEFAULT 0,
    source           VARCHAR(64) NOT NULL DEFAULT 'unknown',
    last_computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tenant_usage_daily_tenant_date_feature
        UNIQUE (tenant_id, usage_date, feature_key)
);

CREATE TABLE IF NOT EXISTS tenant_compliance_records (
    id                     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at             TIMESTAMPTZ,
    tenant_id              TEXT        NOT NULL,
    usage_date             DATE        NOT NULL,
    feature_key            VARCHAR(120) NOT NULL,
    feature_name           VARCHAR(120) NOT NULL,
    entitlement_value      VARCHAR(120),
    entitlement_limit      INTEGER,
    entitlement_is_enabled BOOLEAN     NOT NULL DEFAULT FALSE,
    usage_value            INTEGER     NOT NULL DEFAULT 0,
    overage_value          INTEGER     NOT NULL DEFAULT 0,
    status                 VARCHAR(32) NOT NULL,
    source                 VARCHAR(64),
    plan_codes_json        TEXT,
    evaluated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tenant_compliance_records_tenant_date_feature
        UNIQUE (tenant_id, usage_date, feature_key)
);

CREATE INDEX IF NOT EXISTS ix_tenant_feature_usage_events_lookup
    ON tenant_feature_usage_events(tenant_id, feature_key, occurred_at DESC);

CREATE INDEX IF NOT EXISTS ix_tenant_usage_daily_lookup
    ON tenant_usage_daily(tenant_id, usage_date, feature_key);

CREATE INDEX IF NOT EXISTS ix_tenant_compliance_records_lookup
    ON tenant_compliance_records(tenant_id, usage_date, status);
