-- Migration: 0011_enhanced_chat_sessions.sql
-- Created: 2025-11-06T04:39:50.001622Z
-- Add idempotent DDL here (use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)
BEGIN;

-- ===========================================================
-- ADD MISSING INDEXES FOR PERFORMANCE
-- ===========================================================

-- Speed up session listing by user
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_updated 
ON chat_sessions (user_id, updated_at DESC, is_archived);

-- Speed up message retrieval
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created 
ON chat_messages (session_id, created_at ASC);

-- Speed up user lookup in middleware
CREATE INDEX IF NOT EXISTS idx_users_email 
ON users (email);

-- ===========================================================
-- ADD SESSION ISOLATION & CONSTRAINTS
-- ===========================================================

-- Ensure messages can't be orphaned
ALTER TABLE chat_messages
    DROP CONSTRAINT IF EXISTS chat_messages_session_id_fkey,
    ADD CONSTRAINT chat_messages_session_id_fkey 
        FOREIGN KEY (session_id) 
        REFERENCES chat_sessions(id) 
        ON DELETE CASCADE;

-- Ensure sessions belong to valid users
ALTER TABLE chat_sessions
    DROP CONSTRAINT IF EXISTS chat_sessions_user_id_fkey,
    ADD CONSTRAINT chat_sessions_user_id_fkey 
        FOREIGN KEY (user_id) 
        REFERENCES users(id) 
        ON DELETE CASCADE;

-- ===========================================================
-- ADD SESSION STATISTICS (for UI/analytics)
-- ===========================================================

ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS message_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_activity TIMESTAMPTZ DEFAULT now();

-- Function to update session stats automatically
CREATE OR REPLACE FUNCTION update_session_stats()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'INSERT') THEN
        UPDATE chat_sessions
        SET 
            message_count = message_count + 1,
            last_activity = NEW.created_at,
            updated_at = NEW.created_at
        WHERE id = NEW.session_id;
    ELSIF (TG_OP = 'DELETE') THEN
        UPDATE chat_sessions
        SET message_count = GREATEST(message_count - 1, 0)
        WHERE id = OLD.session_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to maintain stats
DROP TRIGGER IF EXISTS trg_update_session_stats ON chat_messages;
CREATE TRIGGER trg_update_session_stats
AFTER INSERT OR DELETE ON chat_messages
FOR EACH ROW
EXECUTE FUNCTION update_session_stats();

-- ===========================================================
-- BACKFILL EXISTING DATA
-- ===========================================================

-- Update message_count for existing sessions
UPDATE chat_sessions cs
SET message_count = (
    SELECT COUNT(*) 
    FROM chat_messages 
    WHERE session_id = cs.id
)
WHERE message_count = 0;

-- Update last_activity from latest message
UPDATE chat_sessions cs
SET last_activity = (
    SELECT MAX(created_at) 
    FROM chat_messages 
    WHERE session_id = cs.id
)
WHERE last_activity IS NULL;

-- ===========================================================
-- ADD SOFT DELETE FOR MESSAGES (optional, for recovery)
-- ===========================================================

ALTER TABLE chat_messages
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_chat_messages_not_deleted
ON chat_messages (session_id, created_at)
WHERE deleted_at IS NULL;

-- ===========================================================
-- ADD SESSION SHARING (for future multi-user feature)
-- ===========================================================

CREATE TABLE IF NOT EXISTS chat_session_shares (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
    shared_with_user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    permission TEXT CHECK (permission IN ('view', 'edit')) DEFAULT 'view',
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(session_id, shared_with_user_id)
);

COMMIT;

-- ===========================================================
-- PERFORMANCE: VACUUM & ANALYZE (run outside transaction)
-- ===========================================================
-- Note: Run these manually after migration completes:
-- VACUUM ANALYZE chat_sessions;
-- VACUUM ANALYZE chat_messages;