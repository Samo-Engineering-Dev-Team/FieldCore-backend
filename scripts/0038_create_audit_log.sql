-- Migration 0038: append-only audit trail for critical admin, tenant, licensing, and billing operations.
-- tenant_id remains VARCHAR(128) because FieldCore tenant scope is string-based.

CREATE TABLE IF NOT EXISTS audit_log (
    id            SERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor_user_id UUID,
    tenant_id     VARCHAR(128),
    action_type   VARCHAR(120) NOT NULL,
    resource      VARCHAR(200) NOT NULL,
    before        JSONB,
    after         JSONB,
    request_id    VARCHAR(128)
);

CREATE INDEX IF NOT EXISTS ix_audit_log_created_at
    ON audit_log(created_at DESC);

CREATE INDEX IF NOT EXISTS ix_audit_log_tenant_created
    ON audit_log(tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_audit_log_action_type
    ON audit_log(action_type);

CREATE INDEX IF NOT EXISTS ix_audit_log_actor_created
    ON audit_log(actor_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_audit_log_request_id
    ON audit_log(request_id);

CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_log_no_update ON audit_log;
CREATE TRIGGER trg_audit_log_no_update
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW
    EXECUTE FUNCTION prevent_audit_log_mutation();
