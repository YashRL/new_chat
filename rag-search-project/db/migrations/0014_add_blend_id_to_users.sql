-- Migration: 0014_add_blend_id_to_users.sql
            -- Created: 2025-12-17T05:51:24.493555Z
            -- Add idempotent DDL here (use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)
            BEGIN;

            -- Add blendID column (optional, numeric)
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS blendID INTEGER;

            -- Optional: add index for faster lookup (only for non-null values)
            CREATE INDEX IF NOT EXISTS idx_users_blendID
            ON users (blendID)
            WHERE blendID IS NOT NULL;

            COMMIT;
            