-- Add the required category_id FK to form_templates.
-- Backfills any existing rows by mapping the legacy template key -> category code,
-- then enforces NOT NULL. Safe on an empty table (remote) and on seeded envs.

ALTER TABLE form_templates ADD COLUMN IF NOT EXISTS category_id UUID;

-- Backfill existing templates by legacy key. Unknown keys fall back to TASK.
UPDATE form_templates ft
SET category_id = tc.id
FROM template_categories tc
WHERE ft.category_id IS NULL
  AND tc.code = CASE ft.key
      WHEN 'diesel'                        THEN 'DIESEL'
      WHEN 'repeater'                      THEN 'REPEATER'
      WHEN 'routine-drive'                 THEN 'ROUTINE_DRIVE'
      WHEN 'incident-report'               THEN 'INCIDENT'
      WHEN 'routine-generator-inspection'  THEN 'ROUTINE_DRIVE'
      WHEN 'route-patrol'                  THEN 'ROUTINE_DRIVE'
      ELSE 'TASK'
  END;

-- FK constraint (no IF NOT EXISTS for ADD CONSTRAINT; guard with DO block).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_form_templates_category'
    ) THEN
        ALTER TABLE form_templates
            ADD CONSTRAINT fk_form_templates_category
            FOREIGN KEY (category_id) REFERENCES template_categories (id);
    END IF;
END$$;

ALTER TABLE form_templates ALTER COLUMN category_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS ix_form_templates_category_id ON form_templates (category_id);
