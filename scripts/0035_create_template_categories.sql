-- Create the dynamic template_categories table (the form "type IDs") and the
-- linktarget enum. Replaces the hardcoded ReportType enum as the template type.
--
-- linktarget labels are the Python enum NAMES (SQLAlchemy stores enum .name),
-- so they are uppercase: NONE, TASK, INCIDENT.

-- 1. linktarget enum (CREATE TYPE has no IF NOT EXISTS; guard with DO block).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'linktarget') THEN
        CREATE TYPE linktarget AS ENUM ('NONE', 'TASK', 'INCIDENT');
    END IF;
END$$;

-- 2. template_categories table.
CREATE TABLE IF NOT EXISTS template_categories (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code          VARCHAR(50)  NOT NULL,
    name          VARCHAR(200) NOT NULL,
    description   VARCHAR(2000),
    requires_link linktarget   NOT NULL DEFAULT 'NONE',
    is_system     BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    deleted_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_template_categories_code ON template_categories (code);

-- Code unique among non-deleted categories.
CREATE UNIQUE INDEX IF NOT EXISTS uq_template_categories_code_active
    ON template_categories (code) WHERE deleted_at IS NULL;

-- 3. Seed the built-in categories (is_system = TRUE, cannot be deleted).
INSERT INTO template_categories (code, name, description, requires_link, is_system)
VALUES
    ('TASK',          'Task',          'General task report.',                  'TASK',     TRUE),
    ('SHEQ',          'SHEQ',          'Safety, Health, Environment & Quality.', 'NONE',     TRUE),
    ('INCIDENT',      'Incident',      'Incident report.',                      'INCIDENT', TRUE),
    ('ROUTINE_DRIVE', 'Routine Drive', 'Routine drive / fibre route patrol.',   'TASK',     TRUE),
    ('DIESEL',        'Diesel',        'Generator diesel refill report.',       'TASK',     TRUE),
    ('REPEATER',      'Repeater',      'Repeater site visit report.',           'TASK',     TRUE)
ON CONFLICT DO NOTHING;
