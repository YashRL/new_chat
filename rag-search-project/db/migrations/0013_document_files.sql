-- Migration: 0013_document_files.sql
-- Created: 2025-11-14T05:38:34.241434Z
-- Add idempotent DDL here (use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)
BEGIN;

-- ===========================================================
-- NEW: DOCUMENT_FILES TABLE
-- Stores original PDF files linked to documents
-- ===========================================================

CREATE TABLE IF NOT EXISTS document_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- FK to existing document
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,

    -- Actual file binary (PDF)
    file_data BYTEA NOT NULL,

    -- Optional: original file name
    filename TEXT,

    -- Dates
    uploaded_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    expiry_date TIMESTAMPTZ,

    -- Metadata
    mime_type TEXT DEFAULT 'application/pdf',

    UNIQUE (document_id)  -- one stored file per document
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_document_files_doc_id ON document_files(document_id);

-- Update timestamp trigger
CREATE OR REPLACE FUNCTION document_files_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_document_files_updated_at ON document_files;

CREATE TRIGGER trg_document_files_updated_at
BEFORE UPDATE ON document_files
FOR EACH ROW
EXECUTE PROCEDURE document_files_set_updated_at();


COMMIT;
