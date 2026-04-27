-- Migration: 0008_assessment_submissions.sql
-- Created: 2025-11-04T10:27:37.836372Z
-- Add idempotent DDL here (use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)
BEGIN;

-- ===========================================================
-- ASSESSMENT SUBMISSIONS (NEW TABLE)
-- ===========================================================
CREATE TABLE assessment_submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assignment_id UUID NOT NULL REFERENCES assessment_assignments(id) ON DELETE CASCADE,
    assessment_id UUID NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    submitted_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    answers JSONB NOT NULL, -- stores all question responses
    submitted_at TIMESTAMPTZ DEFAULT now(),
    status TEXT CHECK (status IN ('Submitted', 'Reviewed', 'Returned')) DEFAULT 'Submitted',
    feedback TEXT,
    score FLOAT CHECK (score >= 0) DEFAULT 0
);

CREATE INDEX idx_assessment_submissions_assignment_id ON assessment_submissions(assignment_id);
CREATE INDEX idx_assessment_submissions_assessment_id ON assessment_submissions(assessment_id);
CREATE INDEX idx_assessment_submissions_submitted_by ON assessment_submissions(submitted_by);


COMMIT;
