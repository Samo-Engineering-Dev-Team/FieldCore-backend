-- Migration to allow reusing client names after soft-delete
-- This replaces the strict unique constraint with a partial unique index
-- that only enforces uniqueness for active (non-deleted) clients
-- Also makes name uniqueness case-insensitive

-- Step 1: Drop the existing unique constraint on name
ALTER TABLE clients DROP CONSTRAINT IF EXISTS clients_name_key;
DROP INDEX IF EXISTS ix_clients_name;
DROP INDEX IF EXISTS ix_clients_name_active;

-- Step 2: Create a case-insensitive partial unique index that only applies to non-deleted clients
-- This allows creating a new client with the same name as a deleted one
-- and prevents "SEACOM" and "seacom" from coexisting
CREATE UNIQUE INDEX IF NOT EXISTS ix_clients_name_active 
ON clients (LOWER(name)) 
WHERE deleted_at IS NULL;

-- Step 3: Also fix the code column uniqueness (if needed)
ALTER TABLE clients DROP CONSTRAINT IF EXISTS clients_code_key;
DROP INDEX IF EXISTS ix_clients_code;
DROP INDEX IF EXISTS ix_clients_code_active;

CREATE UNIQUE INDEX IF NOT EXISTS ix_clients_code_active 
ON clients (LOWER(code)) 
WHERE deleted_at IS NULL;

-- Note: After running this migration, inactive/deleted clients won't block
-- creating new clients with the same name or code.
-- Names are now case-insensitive: "SEACOM" and "seacom" are treated as the same.
