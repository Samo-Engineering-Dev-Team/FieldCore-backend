-- Enable typo-tolerant search for human-entered names and addresses.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS ix_sites_name_trgm_active
ON sites USING gin (name gin_trgm_ops)
WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_sites_address_trgm_active
ON sites USING gin (address gin_trgm_ops)
WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_clients_name_trgm_active
ON clients USING gin (name gin_trgm_ops)
WHERE deleted_at IS NULL AND is_active = true;
