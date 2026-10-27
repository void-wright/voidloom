-- Migration 013: Embedding Upgrade (Jina v5-text-small, 1024-dim)
-- Wizard Library Level 3 Component 1
--
-- Creates a parallel vec0 table for 1024-dimensional embeddings produced by
-- jinaai/jina-embeddings-v5-text-small. The old memory_embeddings (768-dim,
-- Nomic v1.5) is preserved as a fallback until the upgrade is verified.
--
-- Idempotent: safe to re-run.
-- Schema version: 3.7-L3-embedding-upgrade

-- New vec0 virtual table for 1024-dim Jina v5 embeddings.
-- Uses the same rowid-as-memory_id pattern as the existing table.
CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings_v2 USING vec0(
    memory_id INTEGER PRIMARY KEY,
    embedding float[1024]
);

-- Metadata tracking for the embedding upgrade process.
CREATE TABLE IF NOT EXISTS embedding_upgrade_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    model_name TEXT NOT NULL DEFAULT 'jinaai/jina-embeddings-v5-text-small',
    embedding_dim INTEGER NOT NULL DEFAULT 1024,
    memories_processed INTEGER DEFAULT 0,
    memories_total INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'in_progress',
    -- status: in_progress, completed, failed, aborted
    error_message TEXT,
    ab_comparison_json TEXT,
    -- Stores the full A/B comparison result as JSON
    notes TEXT
);

-- Schema version bump
INSERT OR REPLACE INTO metadata (key, value) VALUES ('schema_version', '3.7-L3-embedding-upgrade');
INSERT OR REPLACE INTO metadata (key, value) VALUES ('embedding_upgrade_started', datetime('now'));
