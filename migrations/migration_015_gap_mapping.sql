-- migration_015_gap_mapping.sql
-- Wizard Library Level 4, Piece 6: Knowledge Gap Mapping + Auto-Research Dispatch
--
-- Idempotent: safe to re-run. Uses CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE.
--
-- Depends on: memories, memory_edges, access_log (all present since schema 1.0+)
-- Schema version: 3.8-L4-gap-mapping (bumps from 3.8-L4-calibration-memory)

-- The gap_inventory table stores computed knowledge gaps from weekly inventory runs.
-- Each row is an entity or domain where the brain's knowledge is thin, weak, stale,
-- contradicted, or sparse. Scores are computed from importance x thinness x strategic_weight.
CREATE TABLE IF NOT EXISTS gap_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- The entity, domain, or topic that has a knowledge gap
    entity TEXT NOT NULL,

    -- Classification of the gap
    gap_type TEXT NOT NULL CHECK(gap_type IN (
        'thin_entity',               -- entity appears only 1-2 times in memories
        'weak_recall',               -- recall queries for this topic returned poor similarity
        'unresolved_contradiction',  -- contradicts edges where both memories are still active
        'sparse_domain',             -- project or tag area with low memory density
        'stale_entity'               -- entity referenced recently but no memories updated in 60+ days
    )),

    -- Composite score: importance x thinness x strategic_weight
    score REAL NOT NULL,

    -- How many memories currently reference this entity
    memory_count INTEGER DEFAULT 0,

    -- When this entity was last mentioned in a recall query or memory
    last_mentioned TEXT DEFAULT NULL,

    -- Whether auto-research can be dispatched for this gap
    auto_research_eligible INTEGER DEFAULT 0,

    -- Whether research has been dispatched and completed
    researched INTEGER DEFAULT 0,

    -- If researched, the memory ID of the stored research result
    research_memory_id INTEGER DEFAULT NULL,

    -- When this gap was inventoried
    inventoried_at TEXT NOT NULL DEFAULT (datetime('now')),

    -- Batch identifier: groups gaps from the same inventory run
    batch_id INTEGER DEFAULT NULL,

    -- Acknowledgement: Drew can mark a gap as "seen" or "not-a-gap"
    acknowledged INTEGER DEFAULT 0,
    acknowledged_at TEXT DEFAULT NULL,
    acknowledged_reason TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_gap_score ON gap_inventory(score DESC);
CREATE INDEX IF NOT EXISTS idx_gap_type ON gap_inventory(gap_type);
CREATE INDEX IF NOT EXISTS idx_gap_batch ON gap_inventory(batch_id);
CREATE INDEX IF NOT EXISTS idx_gap_entity ON gap_inventory(entity);

-- gap_inventory_batches: tracks each weekly inventory run
CREATE TABLE IF NOT EXISTS gap_inventory_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT DEFAULT NULL,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'completed', 'error')),
    total_gaps INTEGER DEFAULT 0,
    thin_entity_count INTEGER DEFAULT 0,
    weak_recall_count INTEGER DEFAULT 0,
    contradiction_count INTEGER DEFAULT 0,
    sparse_domain_count INTEGER DEFAULT 0,
    stale_entity_count INTEGER DEFAULT 0,
    research_dispatched INTEGER DEFAULT 0,
    config_snapshot TEXT DEFAULT NULL,     -- JSON of config used for this run
    error_log TEXT DEFAULT NULL,
    duration_seconds REAL DEFAULT NULL
);

-- gap_research_log: tracks auto-research dispatch attempts
CREATE TABLE IF NOT EXISTS gap_research_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gap_id INTEGER NOT NULL REFERENCES gap_inventory(id),
    dispatch_method TEXT NOT NULL CHECK(dispatch_method IN ('claude_cli', 'agy', 'manual')),
    dispatch_command TEXT DEFAULT NULL,    -- the actual command executed
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT DEFAULT NULL,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'success', 'error', 'timeout')),
    result_memory_id INTEGER DEFAULT NULL, -- memory created from research
    wall_clock_seconds REAL DEFAULT NULL,
    error_message TEXT DEFAULT NULL,
    raw_output_length INTEGER DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_gap_research_gap ON gap_research_log(gap_id);

-- Bump schema version
INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('schema_version', '3.8-L4-gap-mapping', datetime('now'));
