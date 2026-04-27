
-- 0001_initial.sql
-- Initial schema for graph-aware multi-granularity document store
-- Idempotent where possible (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)

-- Extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- Documents
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_name TEXT NOT NULL,
    document_type TEXT NOT NULL,
    keywords TEXT[] DEFAULT '{}',
    meta JSONB DEFAULT '{}',
    total_tokens INTEGER DEFAULT 0,
    file_hash TEXT UNIQUE,
    text_hash TEXT UNIQUE,
    minhash_sig BYTEA,
    simhash BIGINT,
    semantic_hash TEXT,
    canonical_doc_id UUID REFERENCES documents(id) ON DELETE SET NULL,
    version_of UUID REFERENCES documents(id) ON DELETE SET NULL,
    version_label TEXT,
    dedup_status TEXT DEFAULT 'new',
    source_path TEXT,
    mime_type TEXT,
    language TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Sections
CREATE TABLE IF NOT EXISTS sections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    parent_id UUID REFERENCES sections(id) ON DELETE CASCADE,
    title TEXT,
    order_index INTEGER,
    level INTEGER,
    meta JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Headings
CREATE TABLE IF NOT EXISTS headings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    section_id UUID REFERENCES sections(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    title TEXT,
    order_index INTEGER,
    meta JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Paragraphs
CREATE TABLE IF NOT EXISTS paragraphs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section_id UUID REFERENCES sections(id) ON DELETE SET NULL,
    heading_id UUID REFERENCES headings(id) ON DELETE SET NULL,
    text TEXT NOT NULL,
    norm_text TEXT,
    token_count INTEGER DEFAULT 0,
    char_start INTEGER,
    char_end INTEGER,
    page_number INTEGER,
    paragraph_index INTEGER,
    chunk_hash TEXT,
    embedding_id UUID,
    dup_cluster_id UUID,
    dedup_score FLOAT DEFAULT 0.0,
    meta JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Embeddings
CREATE TABLE IF NOT EXISTS embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    paragraph_id UUID NOT NULL UNIQUE REFERENCES paragraphs(id) ON DELETE CASCADE,
    embedding vector(1024),
    model_name TEXT,
    model_version TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Dedup clusters
CREATE TABLE IF NOT EXISTS dedup_clusters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    members UUID[] DEFAULT '{}',
    dedup_score FLOAT DEFAULT 0.0,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- schema_migrations (migration bookkeeping)
CREATE TABLE IF NOT EXISTS schema_migrations (
    id SERIAL PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Full-text search tsvector
ALTER TABLE paragraphs ADD COLUMN IF NOT EXISTS search_tsv tsvector;
CREATE INDEX IF NOT EXISTS idx_paragraphs_search_tsv ON paragraphs USING GIN(search_tsv);

-- Trigger for tsvector
DROP FUNCTION IF EXISTS paragraphs_search_tsv_trigger();
CREATE FUNCTION paragraphs_search_tsv_trigger() RETURNS trigger AS $$
BEGIN
    NEW.search_tsv := to_tsvector('simple', coalesce(NEW.norm_text, NEW.text));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_paragraphs_search_tsv ON paragraphs;
CREATE TRIGGER trg_paragraphs_search_tsv
BEFORE INSERT OR UPDATE ON paragraphs
FOR EACH ROW EXECUTE PROCEDURE paragraphs_search_tsv_trigger();

-- Dedup / filter indexes
CREATE UNIQUE INDEX IF NOT EXISTS idx_paragraphs_chunk_hash ON paragraphs(chunk_hash);
CREATE INDEX IF NOT EXISTS idx_documents_file_hash ON documents(file_hash);
CREATE INDEX IF NOT EXISTS idx_documents_text_hash ON documents(text_hash);
CREATE INDEX IF NOT EXISTS idx_documents_semantic_hash ON documents(semantic_hash);

-- Vector index HNSW (pgvector)
DO $$
BEGIN
    -- guard in case vector extension isn't available
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE indexname = 'idx_embeddings_hnsw'
        ) THEN
            EXECUTE 'CREATE INDEX idx_embeddings_hnsw ON embeddings USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 200);';
        END IF;
    END IF;
END$$;

-- Metadata indexes
CREATE INDEX IF NOT EXISTS idx_paragraphs_document_id ON paragraphs(document_id);
CREATE INDEX IF NOT EXISTS idx_paragraphs_page_number ON paragraphs(page_number);
CREATE INDEX IF NOT EXISTS idx_documents_doc_type ON documents(document_type);
CREATE INDEX IF NOT EXISTS idx_documents_keywords ON documents USING GIN (keywords);
CREATE INDEX IF NOT EXISTS idx_documents_meta ON documents USING GIN (meta jsonb_path_ops);
