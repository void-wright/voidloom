-- Migration 004: Procedural Memory Promotion Pipeline
-- Schema version: 2.1-L2-kind-taxonomy -> 2.3-L2-procedural-memory
-- (2.2 reserved for C4 Temporal Edges)
--
-- Idempotent: safe to re-run. All statements use IF NOT EXISTS.
-- Dependencies: memories table with kind column (migration 002).
--
-- Purpose: Tracks deliberate usage events for procedural memories
-- with outcome signals (success/partial/failure/unknown), enabling
-- a data-driven promotion/demotion pipeline.

PRAGMA foreign_keys=ON;

-- Granular event-level tracking for procedural memory usage.
-- Complements the aggregate last_accessed/access_count on memories table.
CREATE TABLE IF NOT EXISTS skill_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    used_at TEXT NOT NULL DEFAULT (datetime('now')),
    context TEXT DEFAULT NULL,
    outcome TEXT DEFAULT 'unknown'
        CHECK(outcome IN ('success', 'partial', 'failure', 'unknown')),
    outcome_notes TEXT DEFAULT NULL,
    triggered_by TEXT DEFAULT 'recall'
        CHECK(triggered_by IN ('recall', 'manual', 'cron', 'sleep_loop')),
    session_id TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_memory ON skill_usage_log(memory_id);
CREATE INDEX IF NOT EXISTS idx_usage_outcome ON skill_usage_log(outcome);
CREATE INDEX IF NOT EXISTS idx_usage_time ON skill_usage_log(used_at);
CREATE INDEX IF NOT EXISTS idx_usage_session ON skill_usage_log(session_id);

-- Schema version bump
INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('schema_version', '2.3-L2-procedural-memory', datetime('now'));
