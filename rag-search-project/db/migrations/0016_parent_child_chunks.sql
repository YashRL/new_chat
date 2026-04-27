-- Migration: 0016_parent_child_chunks.sql
-- Adds parent_chunk_id to paragraphs for parent-child chunking strategy
-- Small chunks (128 tokens) for precise retrieval, parent chunks (512 tokens) for full context

BEGIN;

ALTER TABLE paragraphs
    ADD COLUMN IF NOT EXISTS parent_chunk_id UUID REFERENCES paragraphs(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS chunk_type TEXT DEFAULT 'standalone';

CREATE INDEX IF NOT EXISTS idx_paragraphs_parent_chunk_id ON paragraphs(parent_chunk_id);
CREATE INDEX IF NOT EXISTS idx_paragraphs_chunk_type ON paragraphs(chunk_type);

COMMENT ON COLUMN paragraphs.parent_chunk_id IS 'Points to the larger parent chunk containing this small chunk';
COMMENT ON COLUMN paragraphs.chunk_type IS 'standalone | parent | child';

COMMIT;
