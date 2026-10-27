-- Migration 003: Temporal Edges (Wizard Library L2 Component 4)
-- Adds the memory_edges table with 8 typed predicates and bitemporal validity.
-- Idempotent: safe to run multiple times.
--
-- Schema version: 2.0-L2-hybrid-retrieval -> 2.2-L2-temporal-edges
-- (C2 Kind Taxonomy bumps to 2.1; C4 Temporal Edges to 2.2)
--
-- Depends on: memories table (base schema), migration_001_fts5.sql (schema 2.0)
-- Phase 0 design: memory #3914 (temporal edges design), #3816 (bitemporal pattern)

-- The memory_edges table stores typed directed edges between memories.
-- Each edge has bitemporal validity:
--   valid_from / valid_until  = when the relationship holds in the real world
--   created_at                = when we recorded the edge (system time)
CREATE TABLE IF NOT EXISTS memory_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    predicate TEXT NOT NULL CHECK(predicate IN (
        'causes', 'contradicts', 'supersedes', 'refines',
        'generalizes', 'instantiates', 'references', 'depends_on'
    )),
    confidence REAL NOT NULL DEFAULT 0.8 CHECK(confidence >= 0.0 AND confidence <= 1.0),
    valid_from TEXT DEFAULT NULL,        -- ISO datetime, NULL = always valid
    valid_until TEXT DEFAULT NULL,        -- ISO datetime, NULL = currently valid
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata TEXT DEFAULT NULL,           -- JSON for predicate-specific context
    extraction_method TEXT DEFAULT 'manual' CHECK(extraction_method IN (
        'manual', 'heuristic', 'llm_haiku', 'llm_sonnet', 'sleep_loop'
    )),
    UNIQUE(source_id, target_id, predicate),
    CHECK(source_id != target_id)
);

-- Indexes for efficient traversal and querying.
CREATE INDEX IF NOT EXISTS idx_edges_source ON memory_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON memory_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_predicate ON memory_edges(predicate);
CREATE INDEX IF NOT EXISTS idx_edges_valid ON memory_edges(valid_until) WHERE valid_until IS NULL;
CREATE INDEX IF NOT EXISTS idx_edges_confidence ON memory_edges(confidence);
CREATE INDEX IF NOT EXISTS idx_edges_created ON memory_edges(created_at);

-- Bump schema version.
INSERT OR REPLACE INTO metadata (key, value)
VALUES ('schema_version', '2.2-L2-temporal-edges');

INSERT OR REPLACE INTO metadata (key, value)
VALUES ('temporal_edges_migration_applied', datetime('now'));

-- Post-migration verification queries (for manual inspection):
--   SELECT COUNT(*) FROM memory_edges;  -- should be 0 on fresh migration
--   SELECT value FROM metadata WHERE key = 'schema_version';  -- should be 2.2-L2-temporal-edges
--   SELECT sql FROM sqlite_master WHERE name = 'memory_edges';  -- inspect schema
--   SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'memory_edges';  -- 6 indexes
