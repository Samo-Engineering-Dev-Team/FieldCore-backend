-- Add SHEQ to the userrole enum.
-- SQLAlchemy maps Python enum members by NAME, so existing labels are uppercase
-- (SUPER_ADMIN, ADMIN, MANAGER, TECHNICIAN, NOC). The new member UserRole.SHEQ
-- is stored as the label 'SHEQ'.
-- Note: ALTER TYPE ... ADD VALUE must run outside a transaction block (autocommit).

ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'SHEQ';
