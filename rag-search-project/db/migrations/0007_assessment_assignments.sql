-- Migration: 0007_assessment_assignments.sql
-- Created: 2025-11-04T05:53:41.397681Z
-- Add idempotent DDL here (use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)
BEGIN;

-- ===========================================================
-- ASSESSMENT ASSIGNMENTS (New)
-- ===========================================================
CREATE TABLE assessment_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id UUID NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    assigned_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    assigned_to UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    priority TEXT CHECK (priority IN ('Low', 'Medium', 'High')) DEFAULT 'Medium',
    message TEXT,
    status TEXT CHECK (status IN ('Pending', 'In Progress', 'Completed', 'Cancelled')) DEFAULT 'Pending',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_assessment_assignments_assessment_id ON assessment_assignments(assessment_id);
CREATE INDEX idx_assessment_assignments_assigned_to ON assessment_assignments(assigned_to);
CREATE INDEX idx_assessment_assignments_assigned_by ON assessment_assignments(assigned_by);

CREATE TRIGGER trg_assessment_assignments_updated_at
BEFORE UPDATE ON assessment_assignments
FOR EACH ROW EXECUTE PROCEDURE set_updated_at();


COMMIT;
