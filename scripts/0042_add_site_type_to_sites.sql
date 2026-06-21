ALTER TABLE sites
ADD COLUMN IF NOT EXISTS site_type VARCHAR NOT NULL DEFAULT 'task_site';

UPDATE sites
SET site_type = 'task_site'
WHERE site_type IS NULL;
