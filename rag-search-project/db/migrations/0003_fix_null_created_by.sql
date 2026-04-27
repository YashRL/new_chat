-- Migration: 0003_fix_null_created_by.sql
-- Created: 2025-10-17T11:23:28.752894Z
-- Add idempotent DDL here (use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)
BEGIN;

-- Ensure guest user exists (idempotent)
INSERT INTO users (email, display_name)
VALUES ('guest@system.local', 'Guest User')
ON CONFLICT (email) DO NOTHING;

-- Assign Guest user ownership where missing
UPDATE documents
SET 
    created_by = (SELECT id FROM users WHERE email='guest@system.local'),
    updated_by = (SELECT id FROM users WHERE email='guest@system.local'),
    visibility = COALESCE(visibility, '{"everyone": true}')
WHERE created_by IS NULL;

-- Optional: also ensure paragraphs have valid created_by
UPDATE paragraphs
SET 
    created_by = (SELECT id FROM users WHERE email='guest@system.local'),
    updated_by = (SELECT id FROM users WHERE email='guest@system.local')
WHERE created_by IS NULL;

COMMIT;
