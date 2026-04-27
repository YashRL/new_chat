-- Migration: 0010_chat_memory_enhancements.sql
-- Created: 2025-11-05T10:36:29.562559Z
-- Add idempotent DDL here (use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)
BEGIN;

-- ===========================================================
-- 🔧 Enhance chat_sessions
-- ===========================================================
ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb,  -- store summary, tags, preferences, etc.
    ADD COLUMN IF NOT EXISTS is_archived BOOLEAN DEFAULT FALSE;   -- allow users to archive old chats

-- Automatically update updated_at timestamp on any change
CREATE OR REPLACE FUNCTION update_chat_session_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_chat_session_timestamp ON chat_sessions;
CREATE TRIGGER trg_update_chat_session_timestamp
BEFORE UPDATE ON chat_sessions
FOR EACH ROW
EXECUTE FUNCTION update_chat_session_timestamp();

-- ===========================================================
-- 🔧 Enhance chat_messages
-- ===========================================================
ALTER TABLE chat_messages
    ADD COLUMN IF NOT EXISTS token_count INTEGER DEFAULT 0;       -- track message size (for cost estimation)
    
-- Optional: Index for semantic search speed
CREATE INDEX IF NOT EXISTS idx_chat_messages_embedding
ON chat_messages
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id
ON chat_messages (session_id);

COMMIT;
