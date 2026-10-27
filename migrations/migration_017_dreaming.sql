-- Migration 017: Memory Dreaming — L4 Piece 1
-- Wizard Library Level 4 Component 1
--
-- Dual-pool random-walk synthesis: nightly background job that pulls
-- divergent pairs (cosine distance > 0.6) and close-but-different-context
-- pairs (cosine distance < 0.3, different source context) and asks an LLM
-- to surface unexpected connections.
--
-- Idempotent: safe to re-run. All DDL uses IF NOT EXISTS.
-- Schema version: 3.9-L4-memory-dreaming
--
-- Design reference: spec memory #4071 (persona dreams), Phase 0 memory #4065.

-- Log every dream attempt, including nulls (most dreams produce nothing).
CREATE TABLE IF NOT EXISTS dream_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Dream type: divergent (far apart), close_context (similar but different
    -- context), persona (one memory tagged persona-related).
    dream_type TEXT NOT NULL
        CHECK(dream_type IN ('divergent', 'close_context', 'persona')),

    -- The two memory IDs that formed this dream pair.
    memory_a_id INTEGER NOT NULL REFERENCES memories(id),
    memory_b_id INTEGER NOT NULL REFERENCES memories(id),

    -- Cosine distance between the pair's embeddings (1 - cosine_similarity).
    cosine_distance REAL,

    -- JSON object describing what made the pair "different context".
    -- For close_context: {"different_project": true, "days_apart": 45, ...}
    -- For divergent: null (distance itself is the criterion).
    context_difference TEXT DEFAULT NULL,

    -- The LLM's synthesized insight text. NULL if the LLM returned nothing.
    dream_content TEXT DEFAULT NULL,

    -- If the dream was meaningful, it gets stored as a reflection memory.
    -- This is the ID of that stored reflection.
    dream_memory_id INTEGER DEFAULT NULL REFERENCES memories(id),

    -- 1 if the dream produced a non-null, non-trivial insight.
    is_meaningful INTEGER DEFAULT 0,

    -- Drew can flag dreams as interesting via cmd_dream or API.
    drew_flagged_interesting INTEGER DEFAULT 0,

    -- Links to the batch that produced this dream.
    batch_id INTEGER DEFAULT NULL,

    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Performance indices for dream_log.
CREATE INDEX IF NOT EXISTS idx_dream_type
    ON dream_log(dream_type);
CREATE INDEX IF NOT EXISTS idx_dream_meaningful
    ON dream_log(is_meaningful)
    WHERE is_meaningful = 1;
CREATE INDEX IF NOT EXISTS idx_dream_flagged
    ON dream_log(drew_flagged_interesting)
    WHERE drew_flagged_interesting = 1;
CREATE INDEX IF NOT EXISTS idx_dream_batch
    ON dream_log(batch_id);
CREATE INDEX IF NOT EXISTS idx_dream_created
    ON dream_log(created_at);
CREATE INDEX IF NOT EXISTS idx_dream_memory_a
    ON dream_log(memory_a_id);
CREATE INDEX IF NOT EXISTS idx_dream_memory_b
    ON dream_log(memory_b_id);

-- Track each nightly dream batch for audit and monitoring.
CREATE TABLE IF NOT EXISTS dream_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    divergent_pairs INTEGER DEFAULT 0,
    close_context_pairs INTEGER DEFAULT 0,
    persona_pairs INTEGER DEFAULT 0,
    meaningful_dreams INTEGER DEFAULT 0,
    null_dreams INTEGER DEFAULT 0,
    total_pairs INTEGER DEFAULT 0,
    duration_seconds REAL DEFAULT NULL,
    error_log TEXT DEFAULT NULL,
    config_snapshot TEXT DEFAULT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Schema version bump.
INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('schema_version', '3.9-L4-memory-dreaming', datetime('now'));

-- Record migration application.
INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('migration_017_dreaming_applied_at', datetime('now'), datetime('now'));
