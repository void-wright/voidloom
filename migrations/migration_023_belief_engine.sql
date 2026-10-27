-- ============================================================================
-- migration_023_belief_engine.sql
-- Voidloom L5-D -- The Belief Engine substrate.
--
-- Adds three structures that let the brain notice genuine disagreements
-- without fabricating fake ones, and route every contradiction/supersede
-- decision through a Drew-gated PROPOSALS surface (never writing edges from
-- a scan, never deleting):
--
--   1. edge_proposals      -- the proposal queue the belief engine writes to.
--                            An add_contradicts proposal, when approved, CREATES
--                            a memory_edges row (extraction_method='belief_engine').
--                            An invalidate_edge / supersede proposal, when
--                            approved, SETS memory_edges.valid_until (NEVER deletes).
--   2. belief_scan_queue   -- the cheap store-path dirty-set. store() enqueues a
--                            memory_id (NO NLI on the hot path); the nightly sweep
--                            drains it, NLIs only dirty x top-k neighbours, then
--                            marks rows processed. O(dirty), never O(n^2).
--   3. belief_engine_state -- a tiny key/value watermark table. Holds
--                            'last_belief_scan_at' (the sweep watermark) +
--                            'rejected_pairs_suppressed' bookkeeping. Kept
--                            separate from `metadata` so the belief engine owns
--                            its own state and a teammate brain without the
--                            metadata conventions still works.
--
-- IDEMPOTENT: every object is CREATE ... IF NOT EXISTS, and the watermark seed
-- is INSERT OR IGNORE. Re-running this migration is a no-op.
--
-- REVERSIBILITY (documented, not automated -- the orchestrator decides):
--   DROP TABLE IF EXISTS edge_proposals;
--   DROP TABLE IF EXISTS belief_scan_queue;
--   DROP TABLE IF EXISTS belief_engine_state;
--   DELETE FROM metadata WHERE key='migration_023_belief_engine_applied_at';
-- Dropping these tables loses only the PROPOSAL queue + the dirty watermark;
-- NO memory_edges row is touched by this migration, so the edge graph is
-- unchanged by apply OR rollback. (Approved proposals already wrote their
-- effects into memory_edges via create_edge / valid_until -- those persist.)
--
-- same_as is OUT OF SCOPE (L5-D.2). This migration does NOT touch the
-- memory_edges predicate CHECK constraint and adds NO new predicate.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. edge_proposals -- the Drew-gated proposal queue.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS edge_proposals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- WHAT is being proposed.
    --   add_contradicts : create a NEW contradicts edge (source<->target).
    --   invalidate_edge : set valid_until on an EXISTING edge_id (remediation).
    --   supersede       : set valid_until on the superseded edge (time-travel).
    proposal_kind   TEXT NOT NULL CHECK (proposal_kind IN (
                        'add_contradicts', 'invalidate_edge', 'supersede')),

    -- The pair the proposal concerns. For invalidate_edge these mirror the
    -- target edge's endpoints (denormalised for display/audit). NULL-tolerant
    -- because an invalidate proposal is keyed by edge_id, not by the pair.
    source_id       INTEGER,
    target_id       INTEGER,

    -- The predicate involved (always 'contradicts' for add_contradicts; the
    -- existing edge's predicate for invalidate_edge/supersede).
    predicate       TEXT,

    -- For invalidate_edge / supersede: the memory_edges.id to invalidate.
    -- NULL for add_contradicts (no edge exists yet).
    edge_id         INTEGER,

    -- The evidence that drove the decision (the f(cosine, NLI) tuple).
    nli_label       TEXT,      -- 'contradiction' | 'entailment' | 'neutral' | NULL (gate-rejected)
    nli_score       REAL,      -- the winning-label softmax score, or NULL
    cosine          REAL,      -- memory_embeddings_v2 cosine between the endpoints
    edge_authority  REAL,      -- trust_tier.edge_authority(a,b) = min(trust(a),trust(b))

    -- elevated=1 when either endpoint is a foundation memory (foundation_score
    -- >= the engine's threshold). The banner/council surface these first.
    elevated        INTEGER NOT NULL DEFAULT 0 CHECK (elevated IN (0, 1)),

    rationale       TEXT,      -- a human-readable one-liner explaining the decision

    -- Batching: one report_batch string per nightly sweep / per remediation run,
    -- so the approvals banner increments read_pending_count by 1, not by N.
    report_batch    TEXT,

    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                        'pending', 'approved', 'rejected', 'enacted')),

    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at      TEXT,                 -- when approved/rejected
    enacted_at      TEXT,                 -- when the edge create / valid_until write landed
    enacted_edge_id INTEGER,              -- the memory_edges.id created (add_contradicts)
    error_log       TEXT,                 -- enactment failure detail, if any

    -- A scan should not re-propose the same pair+kind while one is pending.
    UNIQUE (proposal_kind, source_id, target_id, edge_id)
);

CREATE INDEX IF NOT EXISTS idx_edge_proposals_status
    ON edge_proposals (status);
CREATE INDEX IF NOT EXISTS idx_edge_proposals_batch
    ON edge_proposals (report_batch);
CREATE INDEX IF NOT EXISTS idx_edge_proposals_pair
    ON edge_proposals (source_id, target_id);
-- Fast "is this pair already decided (rejected/approved)?" suppression lookup.
CREATE INDEX IF NOT EXISTS idx_edge_proposals_kind_pair_status
    ON edge_proposals (proposal_kind, source_id, target_id, status);


-- ----------------------------------------------------------------------------
-- 2. belief_scan_queue -- the cheap store-path dirty-set (NO NLI on store).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS belief_scan_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id     INTEGER NOT NULL,
    enqueued_at   TEXT NOT NULL DEFAULT (datetime('now')),
    processed_at  TEXT                    -- NULL = still dirty; set when the sweep handles it
);

-- A memory may be enqueued at most once WHILE UNPROCESSED (a PARTIAL unique
-- index on the pending rows only). This is the correct shape of the intent:
-- it dedupes a still-dirty memory but allows the SAME memory to be re-enqueued
-- after a prior enqueue has been processed (its row's processed_at is non-NULL,
-- which the partial index ignores). A whole-row UNIQUE(memory_id, processed_at)
-- would collide on two same-second processed rows for one memory.
CREATE UNIQUE INDEX IF NOT EXISTS uq_belief_scan_queue_pending
    ON belief_scan_queue (memory_id)
    WHERE processed_at IS NULL;


-- ----------------------------------------------------------------------------
-- 3. belief_engine_state -- the engine's own key/value watermark store.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS belief_engine_state (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Seed the watermark to the epoch so the first sweep treats the whole dirty
-- set as new. INSERT OR IGNORE keeps re-application idempotent.
INSERT OR IGNORE INTO belief_engine_state (key, value)
    VALUES ('last_belief_scan_at', '1970-01-01T00:00:00');
INSERT OR IGNORE INTO belief_engine_state (key, value)
    VALUES ('cosine_gate_threshold', '0.50');


-- ----------------------------------------------------------------------------
-- Migration bookkeeping (mirrors the other migrations' metadata stamp).
-- ----------------------------------------------------------------------------
INSERT OR REPLACE INTO metadata (key, value)
    VALUES ('migration_023_belief_engine_applied_at', datetime('now'));
