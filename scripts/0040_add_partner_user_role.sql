-- Add PARTNER to the userrole enum for external report-only access.
-- SQLAlchemy maps Python enum members by NAME, so UserRole.PARTNER is stored
-- as the label 'PARTNER'.
-- Note: ALTER TYPE ... ADD VALUE must run outside a transaction block (autocommit).

ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'PARTNER';
