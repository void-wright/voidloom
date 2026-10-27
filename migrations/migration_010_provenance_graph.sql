-- Migration 010: Provenance Graph (L3 Component 2)
-- Schema version: 3.4-L3-whisper-network -> 3.5-L3-provenance-graph
--
-- Idempotent: safe to re-run. All statements use IF NOT EXISTS / OR REPLACE.
--
-- Dependencies: memories table (base schema), consolidation_sources table (migration_005).
--
-- Purpose: Rich provenance tracking for ALL derived memories. Records which
-- source memories contributed, what transformation was applied, which agent/process
-- performed the derivation, the prompt hash (for reproducibility), and a confidence
-- score. Extends the existing consolidation_sources table (narrow: only sleep-loop
-- consolidation) into a universal provenance layer covering reflections, distillations,
-- dream synthesis, council deliberations, and manual derivations.
--
-- The existing `derived_from` JSON field on memories is preserved for quick inline
-- access; this table adds structured, queryable, indexable metadata.
--
-- Phase 0 design: L3-C2 provenance-graph task specification.

PRAGMA foreign_keys=ON;

-- Core provenance table: one row per (derived_memory, source_memory) edge.
-- A derived memory with N sources has N rows in this table.
CREATE TABLE IF NOT EXISTS memory_provenance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    derived_memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    source_memory_id INTEGER NOT NULL REFERENCES memories(id),
    transformation_type TEXT NOT NULL CHECK(transformation_type IN (
        'consolidation', 'reflection', 'distillation', 'dream_synthesis',
        'council_deliberation', 'manual_derivation', 'edge_inference'
    )),
    agent TEXT DEFAULT NULL,
    prompt_hash TEXT DEFAULT NULL,
    confidence REAL DEFAULT 0.8,
    metadata TEXT DEFAULT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Primary access patterns:
-- 1. "What sources produced this memory?" -> idx_prov_derived
-- 2. "What was derived FROM this memory?" -> idx_prov_source
-- 3. "All consolidations" / "All reflections" -> idx_prov_type
-- 4. "Everything the sleep_loop produced" -> idx_prov_agent
CREATE INDEX IF NOT EXISTS idx_prov_derived ON memory_provenance(derived_memory_id);
CREATE INDEX IF NOT EXISTS idx_prov_source ON memory_provenance(source_memory_id);
CREATE INDEX IF NOT EXISTS idx_prov_type ON memory_provenance(transformation_type);
CREATE INDEX IF NOT EXISTS idx_prov_agent ON memory_provenance(agent);

-- Compound uniqueness: a given (derived, source, type) triple should not repeat.
-- This prevents accidental double-recording when backfill runs multiple times.
CREATE UNIQUE INDEX IF NOT EXISTS idx_prov_unique_edge
    ON memory_provenance(derived_memory_id, source_memory_id, transformation_type);

-- Schema version bump.
INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('schema_version', '3.5-L3-provenance-graph', datetime('now'));

-- Record migration application timestamp.
INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('migration_010_provenance_graph_applied_at', datetime('now'), datetime('now'));
