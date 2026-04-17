ALTER TABLE users
ADD COLUMN IF NOT EXISTS credentials_updated_at TIMESTAMPTZ;

UPDATE users
SET credentials_updated_at = COALESCE(created_at, NOW())
WHERE credentials_updated_at IS NULL;

ALTER TABLE users
ALTER COLUMN credentials_updated_at SET DEFAULT NOW();

ALTER TABLE users
ALTER COLUMN credentials_updated_at SET NOT NULL;
