-- Migration: 0006_add_file_hash_to_schema_migrations.sql
-- Created: 2025-10-30T09:22:11.958389Z
-- Add idempotent DDL here (use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)
BEGIN;

ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS file_hash TEXT;

COMMIT;
