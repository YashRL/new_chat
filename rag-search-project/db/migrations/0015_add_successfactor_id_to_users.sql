-- Migration: 0015_add_successfactor_id_to_users.sql
            -- Created: 2026-01-16T10:22:50.821346Z
            -- Add idempotent DDL here (use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)
            BEGIN;

            -- Add successfactorID column (optional, numeric)
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS successfactorID INTEGER;

            -- Optional: add index for faster lookup (only for non-null values)
            CREATE INDEX IF NOT EXISTS idx_users_successfactorID
            ON users (successfactorID)
            WHERE successfactorID IS NOT NULL;

            COMMIT;
            
            