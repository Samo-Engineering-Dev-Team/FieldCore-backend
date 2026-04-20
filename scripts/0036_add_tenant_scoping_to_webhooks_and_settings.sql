ALTER TABLE webhooks
ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(128);

CREATE INDEX IF NOT EXISTS ix_webhooks_tenant_event_active
ON webhooks (tenant_id, event_type, is_active);

ALTER TABLE system_settings
ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(128);

CREATE INDEX IF NOT EXISTS ix_system_settings_tenant_key
ON system_settings (tenant_id, key);
