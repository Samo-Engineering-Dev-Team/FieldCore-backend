-- Make SUPER_ADMIN a distinct user role.
-- Previously UserRole.SUPER_ADMIN was a Python alias of ADMIN (both "admin"),
-- so super admins were stored with the label 'ADMIN'. The enum member now has
-- its own value ("super_admin") and is persisted under its own label.
-- SQLAlchemy maps Python enum members by NAME, so the label is 'SUPER_ADMIN'.
-- Note: ALTER TYPE ... ADD VALUE must run outside a transaction block (autocommit).

ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'SUPER_ADMIN';
