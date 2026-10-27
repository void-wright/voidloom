-- Migration 008: Anti-Recency Bias Guards (L3-C7)
-- Idempotent: safe to re-run. All CREATE IF NOT EXISTS + column check.
-- Schema version bump: 2.4-L2-sleep-loop -> 3.3-L3-anti-recency
--
-- Purpose: Prevent recency bias from burying foundational memories.
-- Adds a computed foundation_score (0.0-1.0) to each memory and a
-- resurfacing_log that tracks when forgotten foundations are surfaced.
--
-- Design: memory #3914 (Phase 0), L3-C7 spec.

-- Add foundation_score column to memories (nullable — NULL means not yet scored).
-- SQLite ALTER TABLE ADD COLUMN is idempotent-safe: if column exists, it raises
-- "duplicate column name" which we catch at the application level. This SQL is
-- written so that it either succeeds or the column already exists.
-- The application code wraps this in a try/except for the duplicate-column case.
ALTER TABLE memories ADD COLUMN foundation_score REAL DEFAULT NULL;

-- Track resurfacing events for the "forgotten foundations" digest.
CREATE TABLE IF NOT EXISTS resurfacing_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL REFERENCES memories(id),
    resurfaced_at TEXT NOT NULL DEFAULT (datetime('now')),
    reason TEXT NOT NULL,
    acknowledged INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_resurfacing_memory ON resurfacing_log(memory_id);
CREATE INDEX IF NOT EXISTS idx_resurfacing_date ON resurfacing_log(resurfaced_at);

-- Index on foundation_score for efficient candidate selection.
CREATE INDEX IF NOT EXISTS idx_memories_foundation_score
    ON memories(foundation_score) WHERE foundation_score IS NOT NULL;

-- Composite index for the resurfacing candidate query:
-- WHERE foundation_score >= 0.7 AND last_accessed < threshold AND is_archived = 0
CREATE INDEX IF NOT EXISTS idx_memories_foundation_candidates
    ON memories(foundation_score, last_accessed)
    WHERE is_archived = 0 AND foundation_score IS NOT NULL;

-- Bump schema version.
INSERT OR REPLACE INTO metadata (key, value, updated_at)
    VALUES ('schema_version', '3.3-L3-anti-recency', datetime('now'));

-- Record migration timestamp.
INSERT OR REPLACE INTO metadata (key, value, updated_at)
    VALUES ('migration_008_anti_recency_applied_at', datetime('now'), datetime('now'));
