-- ============================================================
-- Wizard Library L2 Migration 002: Kind Taxonomy
-- ============================================================
-- Adds four columns to the `memories` table:
--
--   kind           TEXT    — episodic | semantic | procedural | reflection
--   derived_from   TEXT    — JSON array of source memory IDs (provenance)
--   consolidated_at TEXT   — ISO timestamp of consolidation event
--   confidence     REAL    — 0.0–1.0 confidence score
--
-- IDEMPOTENT: re-running is safe.
--   - PRAGMA table_info() checks prevent duplicate ALTER TABLE
--   - INSERT OR REPLACE on metadata keeps a fresh timestamp
--   - Wrapped in BEGIN/COMMIT so partial failure rolls back
--
-- NOTE on FTS5 triggers: the existing `memories_fts_after_update`
-- trigger fires on ANY column update (not just summary/content).
-- This migration adds columns but does NOT modify data. The
-- subsequent backfill (via kind_taxonomy.py) updates the `kind`
-- column, which triggers FTS5 delete+re-insert of identical
-- summary/content text. This is safe — data stays correct — but
-- incurs ~3944 unnecessary FTS5 operations. Accepted as one-time
-- cost; future C3 Sleep Loop may scope the UPDATE trigger to
-- summary/content columns only via WHEN clause.
--
-- Reference: Phase 0 memory #3912 (kind taxonomy design),
-- #3911 (L2 canonical), #3929 (forward dependency map).
-- ============================================================

BEGIN TRANSACTION;

-- ------------------------------------------------------------
-- 1. Add `kind` column if not present.
-- ------------------------------------------------------------
-- Default 'episodic' matches spec: most interactive memories ARE episodic.
-- CHECK constraint enforces the 4-kind enum at the DB level.
-- We use a sub-select on pragma_table_info to guard idempotency.
-- SQLite does not support IF NOT EXISTS on ALTER TABLE, so we
-- use a runtime check via the application layer (kind_taxonomy.py).
-- This SQL is designed to be run BY kind_taxonomy.py's
-- apply_kind_taxonomy_migration(), which checks column existence
-- before executing each ALTER. The raw SQL below is the canonical
-- reference; the Python runner wraps each in an existence guard.

-- Column: kind
ALTER TABLE memories ADD COLUMN kind TEXT DEFAULT 'episodic'
    CHECK(kind IN ('episodic', 'semantic', 'procedural', 'reflection'));

-- Column: derived_from
-- JSON array of memory IDs this was derived from (provenance chain).
-- e.g., '[3911, 3912, 3913]' for a semantic consolidated from 3 episodics.
-- NULL for episodic memories (they ARE the source, not derived).
ALTER TABLE memories ADD COLUMN derived_from TEXT DEFAULT NULL;

-- Column: consolidated_at
-- ISO timestamp of when consolidation produced this memory.
-- NULL for episodic + manually stored semantic/procedural.
ALTER TABLE memories ADD COLUMN consolidated_at TEXT DEFAULT NULL;

-- Column: confidence
-- 1.0 = full confidence (human-stored or verified)
-- 0.5-0.9 = LLM-consolidated (Sleep Loop output)
-- <0.5 = low confidence (auto-generated, unverified)
ALTER TABLE memories ADD COLUMN confidence REAL DEFAULT 1.0
    CHECK(confidence >= 0.0 AND confidence <= 1.0);

-- ------------------------------------------------------------
-- 2. Schema version bump + audit metadata.
-- ------------------------------------------------------------
INSERT OR REPLACE INTO metadata (key, value) VALUES
    ('migration_002_kind_taxonomy_applied_at', datetime('now')),
    ('schema_version', '2.1-L2-kind-taxonomy');

COMMIT;

-- ============================================================
-- POST-MIGRATION VERIFICATION (run separately, NOT in transaction)
-- ============================================================
-- The Python runner (kind_taxonomy.py) does this automatically.
--
-- 1. Column existence:
--      SELECT name FROM pragma_table_info('memories')
--      WHERE name IN ('kind', 'derived_from', 'consolidated_at', 'confidence');
--    Expect 4 rows.
--
-- 2. Default values:
--      SELECT kind, confidence FROM memories LIMIT 5;
--    Expect kind='episodic', confidence=1.0 for all rows.
--
-- 3. CHECK constraint:
--      UPDATE memories SET kind='invalid' WHERE id=1;
--    Expect CHECK constraint failed error.
--
-- 4. Schema version:
--      SELECT value FROM metadata WHERE key='schema_version';
--    Expect '2.1-L2-kind-taxonomy'.
-- ============================================================
