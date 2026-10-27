-- Migration 005: Sleep Loop Consolidation (L2-C3)
-- Idempotent: safe to re-run. All CREATE IF NOT EXISTS.
-- Schema version bump: 2.3-L2-procedural-memory -> 2.4-L2-sleep-loop

-- Track every sleep loop run for auditability.
CREATE TABLE IF NOT EXISTS sleep_loop_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT DEFAULT NULL,
    status TEXT DEFAULT 'running' CHECK(status IN ('running', 'success', 'partial', 'error')),
    memories_consolidated INTEGER DEFAULT 0,
    clusters_found INTEGER DEFAULT 0,
    edges_extracted INTEGER DEFAULT 0,
    edges_decayed INTEGER DEFAULT 0,
    edges_pruned INTEGER DEFAULT 0,
    reflections_generated INTEGER DEFAULT 0,
    importance_adjustments INTEGER DEFAULT 0,
    consolidation_mode TEXT DEFAULT 'heuristic' CHECK(consolidation_mode IN ('heuristic', 'llm')),
    error_log TEXT DEFAULT NULL,
    config_snapshot TEXT DEFAULT NULL,
    duration_seconds REAL DEFAULT NULL
);

-- Track which source episodic memories contributed to each consolidated semantic memory.
-- Provides provenance: from any consolidated memory, trace back to its sources.
CREATE TABLE IF NOT EXISTS consolidation_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    consolidated_memory_id INTEGER NOT NULL REFERENCES memories(id),
    source_memory_id INTEGER NOT NULL REFERENCES memories(id),
    cluster_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_consol_target ON consolidation_sources(consolidated_memory_id);
CREATE INDEX IF NOT EXISTS idx_consol_source ON consolidation_sources(source_memory_id);

-- Bump schema version.
INSERT OR REPLACE INTO metadata (key, value) VALUES ('schema_version', '2.4-L2-sleep-loop');
