-- Login Audit Log
-- Tracks every login attempt (success and failure) for security monitoring.

CREATE TABLE IF NOT EXISTS login_audit (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        REFERENCES users(id) ON DELETE SET NULL,
    email       TEXT        NOT NULL,
    ip_address  TEXT,
    user_agent  TEXT,
    success     BOOLEAN     NOT NULL,
    failure_reason TEXT,
    role        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS login_audit_user_id_idx   ON login_audit(user_id);
CREATE INDEX IF NOT EXISTS login_audit_created_at_idx ON login_audit(created_at DESC);
CREATE INDEX IF NOT EXISTS login_audit_success_idx    ON login_audit(success);
