-- migration_022_bridge_tiers_and_scope.sql
-- Phase 4 (Cross-Brain Bridge Activation) — schema prep.
-- Consolidates plan steps 4.1 (extended tiers) + 4.3 (bridge_scope) into one clean
-- table rebuild, because SQLite cannot ALTER a CHECK constraint (requires rebuild).
--
-- Changes:
--   1. remote_tier CHECK extended: + 'eddie-windows', 'milamber-wsl' (dual-brain tiers).
--   2. NEW column bridge_scope ('shared'|'personal'|'work-only'|'isolated', default 'shared')
--      for context-aware filtering at recall time.
--   3. PURGES the 293 legacy bridges (200 self-referential after centralization made the
--      'global' tier path == the main brain's own home; 93 project_local whose targets were
--      absorbed into the main brain in Phase 2). We do NOT copy old rows — fresh discovery
--      repopulates. (Council #1, May 28 2026; memory #4830.)
--   4. Recreates the two indexes + the UNIQUE constraint.
--
-- Migration-Topology Rule (Council #1): relocating a tier's physical DB is a topology change;
-- this migration is the audit/repair step for the centralization that orphaned the bridges.
--
-- Idempotency: guarded by the schema_version check in the runner; safe to apply once.
-- Reversible: the pre-migration snapshot is the revert anchor.

BEGIN TRANSACTION;

-- New table with extended tier CHECK + bridge_scope.
CREATE TABLE cross_tier_bridges_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    local_memory_id INTEGER NOT NULL REFERENCES memories(id),
    remote_tier TEXT NOT NULL CHECK(remote_tier IN (
        'project_local', 'global', 'eddie-windows', 'milamber-wsl'
    )),
    remote_db_path TEXT NOT NULL,
    remote_memory_id INTEGER NOT NULL,
    bridge_type TEXT NOT NULL CHECK(bridge_type IN (
        'same_topic', 'extends', 'contradicts', 'supersedes', 'references'
    )),
    bridge_scope TEXT NOT NULL DEFAULT 'shared' CHECK(bridge_scope IN (
        'shared', 'personal', 'work-only', 'isolated'
    )),
    confidence REAL DEFAULT 0.7,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_verified TEXT DEFAULT NULL,
    UNIQUE(local_memory_id, remote_tier, remote_memory_id, bridge_type)
);

-- PURGE: intentionally copy NO rows from the old table (the 293 legacy bridges are stale).
-- (If a future migration needs to preserve specific bridges, INSERT...SELECT with a WHERE here.)

DROP TABLE cross_tier_bridges;
ALTER TABLE cross_tier_bridges_new RENAME TO cross_tier_bridges;

-- Recreate indexes.
CREATE INDEX idx_bridge_local ON cross_tier_bridges(local_memory_id);
CREATE INDEX idx_bridge_remote ON cross_tier_bridges(remote_tier, remote_memory_id);

-- Optional helper index for scope-filtered recall.
CREATE INDEX idx_bridge_scope ON cross_tier_bridges(bridge_scope);

COMMIT;
