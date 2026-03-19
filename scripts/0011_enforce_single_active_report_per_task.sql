-- Enforce one active report per task.
-- Strategy:
-- 1) Keep the latest active report per task (updated_at desc, created_at desc, id desc).
-- 2) Soft-delete older active duplicates to preserve history.
-- 3) Add a unique partial index for active reports.

BEGIN;

WITH ranked_reports AS (
    SELECT
        id,
        task_id,
        ROW_NUMBER() OVER (
            PARTITION BY task_id
            ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id DESC
        ) AS rank
    FROM reports
    WHERE deleted_at IS NULL
),
duplicates AS (
    SELECT id
    FROM ranked_reports
    WHERE rank > 1
)
UPDATE reports
SET
    deleted_at = NOW(),
    updated_at = NOW()
WHERE id IN (SELECT id FROM duplicates);

CREATE UNIQUE INDEX IF NOT EXISTS ux_reports_task_id_active
    ON reports (task_id)
    WHERE deleted_at IS NULL;

COMMIT;
