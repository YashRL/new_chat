-- Migration: 0012_chunk_hash.sql
-- Created: 2025-11-10T06:39:17.604772Z
-- Add idempotent DDL here (use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)
BEGIN;

-- Drop the old global uniqueness constraint
DROP INDEX IF EXISTS idx_paragraphs_chunk_hash;

-- Add new composite unique constraint: unique per document
ALTER TABLE paragraphs 
    DROP CONSTRAINT IF EXISTS paragraphs_chunk_hash_unique,
    ADD CONSTRAINT paragraphs_chunk_hash_unique 
        UNIQUE (document_id, chunk_hash);

-- Add optimized index for lookups
CREATE INDEX IF NOT EXISTS idx_paragraphs_document_chunk 
    ON paragraphs(document_id, chunk_hash);

-- Optional: Add index for chunk_hash alone (for global dedup queries)
CREATE INDEX IF NOT EXISTS idx_paragraphs_chunk_hash_lookup 
    ON paragraphs(chunk_hash);

COMMIT;
