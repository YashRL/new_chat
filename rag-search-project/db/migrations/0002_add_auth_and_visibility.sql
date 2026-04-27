-- Migration: 0002_add_auth_and_visibility.sql
-- Created: 2025-10-16T10:14:58.626142Z
-- Add idempotent DDL here (use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)
BEGIN;

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE,
    password TEXT,
    display_name TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Roles table
CREATE TABLE IF NOT EXISTS roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL, -- Admin, User, Guest
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Groups table
CREATE TABLE IF NOT EXISTS groups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Many-to-many user/group link
CREATE TABLE IF NOT EXISTS user_groups (
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    group_id UUID REFERENCES groups(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, group_id)
);

-- Folder/document ACLs
CREATE TABLE IF NOT EXISTS folder_acl (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    resource_type TEXT NOT NULL, -- 'document' or 'folder'
    resource_id UUID NOT NULL,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    group_id UUID REFERENCES groups(id) ON DELETE CASCADE,
    role_id UUID REFERENCES roles(id) ON DELETE CASCADE,
    permission TEXT NOT NULL, -- 'read', 'write', 'admin'
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Extend documents + paragraphs
ALTER TABLE documents
ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES users(id),
ADD COLUMN IF NOT EXISTS updated_by UUID REFERENCES users(id),
ADD COLUMN IF NOT EXISTS visibility JSONB DEFAULT '{"everyone": true}';

ALTER TABLE paragraphs
ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES users(id),
ADD COLUMN IF NOT EXISTS updated_by UUID REFERENCES users(id);

-- Role seeding
INSERT INTO roles (name, description)
VALUES 
    ('Admin', 'Full access'),
    ('User', 'Standard user'),
    ('Guest', 'Guest user with limited access')
ON CONFLICT (name) DO NOTHING;

-- Guest user seeding
INSERT INTO users (email, display_name)
VALUES ('guest@system.local', 'Guest User')
ON CONFLICT (email) DO NOTHING;

-- Assign guest ownership and visibility to existing docs
UPDATE documents
SET created_by = (SELECT id FROM users WHERE email='guest@system.local'),
    updated_by = (SELECT id FROM users WHERE email='guest@system.local'),
    visibility = '{"everyone": true}'
WHERE created_by IS NULL;

COMMIT;
