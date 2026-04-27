-- Migration: 0005_assessments_table.sql
-- Created: 2025-10-29T05:36:22.631107Z
-- Add idempotent DDL here (use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)
BEGIN;

-- Create assessments table
CREATE TABLE IF NOT EXISTS assessments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE, -- User who uploaded the assessment
    username TEXT NOT NULL,  -- Username of the person who deployed the assessment
    title TEXT NOT NULL,  -- Title of the assessment
    subject TEXT NOT NULL,  -- Subject of the assessment
    categories TEXT[] DEFAULT '{}',  -- Categories for the assessment (multiple values)
    content JSONB NOT NULL,  -- Main content of the assessment (JSON object)
    created_at TIMESTAMPTZ DEFAULT now(),  -- Record creation timestamp
    updated_at TIMESTAMPTZ DEFAULT now()   -- Record update timestamp
);

-- Create an index on user_id for better performance on user-specific queries
CREATE INDEX IF NOT EXISTS idx_assessments_user_id ON assessments(user_id);

-- Create a trigger to automatically update the 'updated_at' field on modification
CREATE OR REPLACE FUNCTION set_assessment_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply the trigger on the assessments table
DROP TRIGGER IF EXISTS trg_assessments_updated_at ON assessments;
CREATE TRIGGER trg_assessments_updated_at
BEFORE UPDATE ON assessments
FOR EACH ROW EXECUTE PROCEDURE set_assessment_updated_at();

COMMIT;
