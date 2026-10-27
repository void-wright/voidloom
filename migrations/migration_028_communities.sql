-- Migration 028: Leiden Cognitive-Map Communities (L5-E-COMM)
-- Schema version: 3.13-L4-self-eval -> 3.14-L5-communities
--
-- Adds the THREE PURE-CONSUMER tables for community detection + lazy summaries.
-- This organ reads the SETTLED temporal-edge graph (memory_edges, valid-only,
-- confidence-weighted, contradicts/weak excluded) and writes ONLY here. It
-- NEVER mutates memory_edges or any pre-existing table -- a post-run fingerprint
-- of memory_edges MUST be byte-identical.
--
-- Idempotent: safe to re-run. Every statement uses IF NOT EXISTS / OR REPLACE.
-- No new column is added to any existing table.
--
-- Depends on: memories table (base schema), memory_edges (migration_003),
--             metadata table (base schema), memory_tags (optional, for labels).
-- Council ruling: memory #5829 (L5-E SPLIT -> E-COMM + E-PRED).

PRAGMA foreign_keys=ON;

-- One row per detected community per run-epoch. community_id is STABLE across
-- nights via Jaccard carry-forward (see communities.py); `epoch` distinguishes
-- successive detections so history is queryable without churning the id.
CREATE TABLE IF NOT EXISTS memory_communities (
    community_id   INTEGER NOT NULL,          -- STABLE theme id (carry-forward)
    epoch          INTEGER NOT NULL,          -- detection run-epoch (monotonic)
    method         TEXT NOT NULL CHECK(method IN ('leiden','group_by','connected_components')),
    size           INTEGER NOT NULL,          -- member count
    label          TEXT,                       -- short human label (e.g. top tag)
    member_edge_hash TEXT,                     -- content-hash of valid member edges
    seed           INTEGER,                    -- the pinned seed used (provenance)
    batch_id       INTEGER,                    -- the sleep-loop run that detected it
    detected_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (community_id, epoch)
);
CREATE INDEX IF NOT EXISTS idx_comm_epoch  ON memory_communities(epoch);
CREATE INDEX IF NOT EXISTS idx_comm_method ON memory_communities(method);

-- Membership: one row per (memory, community, epoch). A memory belongs to at
-- most one community per epoch (the partition is disjoint).
CREATE TABLE IF NOT EXISTS memory_community_members (
    community_id INTEGER NOT NULL,
    epoch        INTEGER NOT NULL,
    memory_id    INTEGER NOT NULL,
    PRIMARY KEY (epoch, memory_id),
    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_commmem_community ON memory_community_members(community_id, epoch);
CREATE INDEX IF NOT EXISTS idx_commmem_memory    ON memory_community_members(memory_id);

-- Lazy summaries: one CURRENT row per stable community_id, regenerated ONLY when
-- the content-hash of its currently-valid member edges changes (single-flight).
CREATE TABLE IF NOT EXISTS community_summaries (
    community_id   INTEGER PRIMARY KEY,        -- stable id (NOT epoch-scoped)
    summary        TEXT,
    member_edge_hash TEXT NOT NULL,            -- the hash this summary was built for
    summary_mode   TEXT NOT NULL DEFAULT 'extractive',
    member_count   INTEGER NOT NULL DEFAULT 0,
    generated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Schema version bump (additive; does not gate any existing reader).
INSERT OR REPLACE INTO metadata (key, value)
VALUES ('schema_version', '3.14-L5-communities');
INSERT OR REPLACE INTO metadata (key, value)
VALUES ('migration_028_communities_applied_at', datetime('now'));

-- Post-migration verification queries (manual inspection):
--   SELECT value FROM metadata WHERE key='schema_version';  -- 3.14-L5-communities
--   SELECT name FROM sqlite_master WHERE type='table'
--     AND name IN ('memory_communities','memory_community_members','community_summaries');  -- 3 rows
--   SELECT COUNT(*) FROM memory_communities;  -- 0 on a fresh migration
