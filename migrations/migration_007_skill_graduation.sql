-- Migration 007: Skill Graduation Pipeline (L3 Component 6)
-- Schema version: 2.4-L2-sleep-loop -> 3.2-L3-skill-graduation
--
-- Idempotent: safe to re-run. All statements use IF NOT EXISTS or
-- check column existence before ALTER TABLE.
--
-- Dependencies: memories table with kind column (migration 002),
--               skill_usage_log table (migration 004).
--
-- Purpose: Automatic promotion/demotion of procedural memories through
-- graduated tiers (seed -> active -> protected) based on usage success
-- rates. Weekly cron evaluates all procedural memories and applies
-- tier changes logged to skill_graduation_log.
--
-- Phase 0 memories: #3823 (L3 skill graduation design).

PRAGMA foreign_keys=ON;

-- Add skill_tier column to memories table.
-- NULL means "not yet classified" (non-procedural memories stay NULL).
-- We cannot use ALTER TABLE ... IF NOT EXISTS in SQLite, so wrap in a
-- no-op-if-exists pattern: attempt ALTER, ignore 'duplicate column' error
-- via the application layer. The SQL below is written for executescript()
-- which stops on error; the application must catch and continue.
--
-- NOTE: The application layer (skill_graduation.py) handles the idempotent
-- ALTER TABLE via try/except. This file ONLY creates the graduation_log
-- table and updates metadata. The ALTER TABLE is documented here for
-- completeness but executed programmatically.

-- Graduation history log — tracks every tier transition.
CREATE TABLE IF NOT EXISTS skill_graduation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    old_tier TEXT,
    new_tier TEXT NOT NULL
        CHECK(new_tier IN ('seed', 'active', 'protected', 'review', 'archived')),
    reason TEXT NOT NULL,
    score REAL,
    graduated_at TEXT NOT NULL DEFAULT (datetime('now')),
    graduated_by TEXT DEFAULT 'cron'
        CHECK(graduated_by IN ('cron', 'manual', 'curator'))
);

CREATE INDEX IF NOT EXISTS idx_graduation_memory ON skill_graduation_log(memory_id);
CREATE INDEX IF NOT EXISTS idx_graduation_time ON skill_graduation_log(graduated_at);
CREATE INDEX IF NOT EXISTS idx_graduation_tier ON skill_graduation_log(new_tier);

-- Schema version bump
INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('schema_version', '3.2-L3-skill-graduation', datetime('now'));

-- Record migration application timestamp
INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('migration_007_skill_graduation_applied_at', datetime('now'), datetime('now'));
