-- Migration 012: Cross-Tier Memory Bridges
-- Level 3 Component 4: Wizard Library
-- Schema version: 3.4-L3-whisper-network -> 3.6-L3-cross-tier-bridges
--
-- Idempotent: safe to re-run. Uses CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.
-- Stored in the NARRATIVE tier DB (the most-shared tier, convention from L3 plan).
--
-- Purpose: Enable bridges between the three memory tiers (Narrative, Project-Local, Global)
-- so a single recall can span all surfaces via the --cross-tier flag.

-- ============================================================
-- Table: cross_tier_bridges
-- ============================================================
-- Each row represents a directional relationship between a memory in the
-- narrative tier and a memory in another tier (project_local or global).
-- The narrative tier is always one endpoint (local_memory_id) because
-- bridges are stored here. Remote-to-remote edges are not supported
-- (would require a fourth DB or full federation).

CREATE TABLE IF NOT EXISTS cross_tier_bridges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    local_memory_id INTEGER NOT NULL REFERENCES memories(id),
    remote_tier TEXT NOT NULL CHECK(remote_tier IN ('project_local', 'global')),
    remote_db_path TEXT NOT NULL,
    remote_memory_id INTEGER NOT NULL,
    bridge_type TEXT NOT NULL CHECK(bridge_type IN (
        'same_topic', 'extends', 'contradicts', 'supersedes', 'references'
    )),
    confidence REAL DEFAULT 0.7,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_verified TEXT DEFAULT NULL,
    UNIQUE(local_memory_id, remote_tier, remote_memory_id, bridge_type)
);

-- Index for querying bridges from a local memory outward.
CREATE INDEX IF NOT EXISTS idx_bridge_local ON cross_tier_bridges(local_memory_id);

-- Index for querying bridges from a remote tier + ID inward.
CREATE INDEX IF NOT EXISTS idx_bridge_remote ON cross_tier_bridges(remote_tier, remote_memory_id);

-- ============================================================
-- Schema version bump
-- ============================================================
INSERT OR REPLACE INTO metadata (key, value) VALUES ('schema_version', '3.6-L3-cross-tier-bridges');
