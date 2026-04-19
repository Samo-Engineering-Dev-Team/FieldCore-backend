-- Migration 0034: add persistent licensing catalog + tenant assignments
-- Notes:
-- - tenant_id stays TEXT for now because frontend tenant context is string-based
--   and backend does not yet own a dedicated tenants table.

CREATE TABLE IF NOT EXISTS license_products (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at  TIMESTAMPTZ,
    sku         VARCHAR(64) NOT NULL,
    name        VARCHAR(120) NOT NULL,
    description TEXT,
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_license_products_sku UNIQUE (sku)
);

CREATE TABLE IF NOT EXISTS license_plans (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at         TIMESTAMPTZ,
    license_product_id UUID        NOT NULL REFERENCES license_products(id) ON DELETE CASCADE,
    code               VARCHAR(64) NOT NULL,
    name               VARCHAR(120) NOT NULL,
    description        TEXT,
    is_active          BOOLEAN     NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_license_plans_product_code UNIQUE (license_product_id, code)
);

CREATE TABLE IF NOT EXISTS entitlements (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at     TIMESTAMPTZ,
    license_plan_id UUID       NOT NULL REFERENCES license_plans(id) ON DELETE CASCADE,
    feature_key    VARCHAR(120) NOT NULL,
    feature_name   VARCHAR(120) NOT NULL,
    description    TEXT,
    grant_value    VARCHAR(120),
    is_enabled     BOOLEAN      NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_entitlements_plan_feature UNIQUE (license_plan_id, feature_key)
);

CREATE TABLE IF NOT EXISTS tenant_licenses (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at           TIMESTAMPTZ,
    tenant_id            TEXT        NOT NULL,
    license_plan_id      UUID        NOT NULL REFERENCES license_plans(id) ON DELETE CASCADE,
    starts_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ends_at              TIMESTAMPTZ,
    assigned_by_user_id  UUID,
    unassigned_by_user_id UUID
);

CREATE TABLE IF NOT EXISTS license_history (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at       TIMESTAMPTZ,
    tenant_license_id UUID       NOT NULL REFERENCES tenant_licenses(id) ON DELETE CASCADE,
    tenant_id        TEXT        NOT NULL,
    license_plan_id  UUID        NOT NULL REFERENCES license_plans(id) ON DELETE CASCADE,
    action           VARCHAR(32) NOT NULL,
    actor_user_id    UUID,
    effective_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    note             TEXT
);

CREATE INDEX IF NOT EXISTS ix_license_products_is_active
    ON license_products(is_active);

CREATE INDEX IF NOT EXISTS ix_license_plans_product_active
    ON license_plans(license_product_id, is_active);

CREATE INDEX IF NOT EXISTS ix_entitlements_plan_feature
    ON entitlements(license_plan_id, feature_key);

CREATE INDEX IF NOT EXISTS ix_tenant_licenses_lookup
    ON tenant_licenses(tenant_id, license_plan_id, starts_at);

CREATE INDEX IF NOT EXISTS ix_license_history_tenant_effective_at
    ON license_history(tenant_id, effective_at DESC);
