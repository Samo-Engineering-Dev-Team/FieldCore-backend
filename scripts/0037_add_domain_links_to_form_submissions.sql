-- Add the domain-link FKs to form_submissions. Which one is required is
-- dictated at submit time by the template category's requires_link
-- (TASK -> task_id, INCIDENT -> incident_id, NONE -> neither). Both nullable.

ALTER TABLE form_submissions ADD COLUMN IF NOT EXISTS task_id     UUID;
ALTER TABLE form_submissions ADD COLUMN IF NOT EXISTS incident_id UUID;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_form_submissions_task'
    ) THEN
        ALTER TABLE form_submissions
            ADD CONSTRAINT fk_form_submissions_task
            FOREIGN KEY (task_id) REFERENCES tasks (id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_form_submissions_incident'
    ) THEN
        ALTER TABLE form_submissions
            ADD CONSTRAINT fk_form_submissions_incident
            FOREIGN KEY (incident_id) REFERENCES incidents (id);
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS ix_form_submissions_task_id     ON form_submissions (task_id);
CREATE INDEX IF NOT EXISTS ix_form_submissions_incident_id ON form_submissions (incident_id);
