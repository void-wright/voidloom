-- ============================================================
-- Wizard Library L2 Migration 001: FTS5 Hybrid Retrieval
-- ============================================================
-- Adds SQLite FTS5 virtual table mirroring memories(summary, content)
-- with content_rowid=id so BM25 keyword search complements the
-- existing vector search (memory_embeddings via sqlite-vec).
--
-- IDEMPOTENT: re-running is safe.
--   - CREATE VIRTUAL TABLE IF NOT EXISTS protects the FTS5 table
--   - DROP TRIGGER IF EXISTS before each CREATE TRIGGER protects trigger ddl
--   - The FTS5 'rebuild' insert drops + re-populates the index, so we
--     get the same end state every time it runs
--   - INSERT OR REPLACE on metadata keeps a fresh timestamp
--
-- Wrapped in a single BEGIN/COMMIT transaction so partial failure rolls back.
-- The Python runner (`apply_fts5_migration` in hybrid_retrieval.py) takes a
-- file-level backup (`*.pre-hybrid-retrieval.bak`) BEFORE invoking this SQL.
--
-- Reference design: Phase 0 memory #3911 (canonical), #3812 (pattern).
-- ============================================================

BEGIN TRANSACTION;

-- ------------------------------------------------------------
-- 1. The FTS5 virtual table.
-- ------------------------------------------------------------
-- content='memories' = external-content table mode. The FTS5 index stores
-- only the rowid + token offsets; the actual `summary` and `content` text
-- is read from `memories` on demand. This means:
--   * zero text duplication on disk
--   * any text change in `memories` must be propagated via triggers (below)
-- content_rowid='id' wires FTS rowid to memories.id, so we can join on id.
-- tokenize: 'porter' = English stemming (runs → run), 'unicode61' = Unicode
-- normalisation. (We deliberately omit `remove_diacritics 1` for v1 — the
-- corpus is overwhelmingly ASCII English narrative memories; iter-2 may
-- revisit if multilingual content grows. Changing tokenizer requires a
-- DROP + CREATE + rebuild cycle, not just IF NOT EXISTS.)
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    summary,
    content,
    content='memories',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- ------------------------------------------------------------
-- 2. Sync triggers — keep memories_fts current with memories.
-- ------------------------------------------------------------
-- These use the FTS5 "external content" sync protocol:
--   * INSERT new row → insert into memories_fts(rowid, ...)
--   * DELETE old row → insert into memories_fts(memories_fts='delete', rowid, ...)
--   * UPDATE old row → delete + insert
-- Drop existing triggers first so re-runs after schema changes work cleanly.
DROP TRIGGER IF EXISTS memories_fts_after_insert;
DROP TRIGGER IF EXISTS memories_fts_after_delete;
DROP TRIGGER IF EXISTS memories_fts_after_update;

CREATE TRIGGER memories_fts_after_insert AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, summary, content)
    VALUES (new.id, new.summary, new.content);
END;

CREATE TRIGGER memories_fts_after_delete AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, summary, content)
    VALUES('delete', old.id, old.summary, old.content);
END;

CREATE TRIGGER memories_fts_after_update AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, summary, content)
    VALUES('delete', old.id, old.summary, old.content);
    INSERT INTO memories_fts(rowid, summary, content)
    VALUES (new.id, new.summary, new.content);
END;

-- ------------------------------------------------------------
-- 3. Backfill the FTS index from existing memories.
-- ------------------------------------------------------------
-- The 'rebuild' command drops all FTS rows and re-indexes from the external
-- content table. Safe to re-run; produces the same end state every time.
-- After this completes:
--   SELECT COUNT(*) FROM memories_fts == SELECT COUNT(*) FROM memories
-- (the Python migration runner verifies this match before declaring success.)
INSERT INTO memories_fts(memories_fts) VALUES('rebuild');

-- ------------------------------------------------------------
-- 4. Audit + schema version metadata.
-- ------------------------------------------------------------
-- migration_001_fts5_applied_at is overwritten on every re-apply (latest
-- timestamp wins) — useful for debugging "when did we last touch this DB?"
-- schema_version is bumped from 1.0 to 2.0-L2-hybrid-retrieval to signal
-- that downstream tools (consolidation, edge extraction) can expect FTS5.
INSERT OR REPLACE INTO metadata (key, value) VALUES
    ('migration_001_fts5_applied_at', datetime('now')),
    ('schema_version', '2.0-L2-hybrid-retrieval');

COMMIT;

-- ============================================================
-- POST-MIGRATION VERIFICATION (run separately, NOT in transaction)
-- ============================================================
-- The Python runner does this automatically; these queries are for humans.
--
-- 1. Row-count match:
--      SELECT
--          (SELECT COUNT(*) FROM memories)     AS memories_count,
--          (SELECT COUNT(*) FROM memories_fts) AS fts_count;
--    Both numbers must be equal.
--
-- 2. Trigger smoke test:
--      INSERT INTO memories (content, summary, category) VALUES ('test', 'test', 'project_knowledge');
--      SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH 'test';   -- expect >0
--      DELETE FROM memories WHERE summary='test';
--      SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH 'test';   -- expect previous - 1
--
-- 3. BM25 query smoke test:
--      SELECT rowid, bm25(memories_fts) FROM memories_fts
--      WHERE memories_fts MATCH 'researchgate' ORDER BY bm25(memories_fts) LIMIT 5;
--    Expect ~5 rows with negative bm25 scores (FTS5: lower-magnitude = better match).
--
-- 4. Schema version:
--      SELECT value FROM metadata WHERE key='schema_version';
--    Expect '2.0-L2-hybrid-retrieval'.
-- ============================================================
