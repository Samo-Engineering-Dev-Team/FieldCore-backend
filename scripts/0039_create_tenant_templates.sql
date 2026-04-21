-- Migration 0039: tenant-scoped template overrides for emails and PDFs.
-- tenant_id NULL rows are platform defaults; tenant_id rows override them.

CREATE TABLE IF NOT EXISTS tenant_templates (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_name VARCHAR(150) NOT NULL,
    content       JSONB        NOT NULL,
    tenant_id     VARCHAR(128),
    version       INTEGER     NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_tenant_templates_tenant_name
    ON tenant_templates(tenant_id, template_name);

CREATE INDEX IF NOT EXISTS ix_tenant_templates_name_version
    ON tenant_templates(template_name, version);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_templates_global_name_version_active
    ON tenant_templates(template_name, version)
    WHERE tenant_id IS NULL AND deleted_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_templates_tenant_name_version_active
    ON tenant_templates(tenant_id, template_name, version)
    WHERE tenant_id IS NOT NULL AND deleted_at IS NULL;
