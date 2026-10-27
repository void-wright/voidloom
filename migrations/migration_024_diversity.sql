-- migration_024_diversity.sql
-- L5-B recall-diversity organ: the lexical-miss telemetry that feeds the
-- dormant-lane wake-gate (gate_should_wake_lane). This migration is ACTIVE
-- (applied today). It creates ONLY the small telemetry table + a state bump;
-- it does NOT create any sparse/ColBERT index (those are the write-but-don't-
-- apply migration_025/026 .DEFERRED scaffolds).
--
-- Idempotent: every statement is IF NOT EXISTS / INSERT OR REPLACE, so re-running
-- is a no-op. Safe to apply to a live brain. ASCII-only (no em-dashes) so the
-- runner never chokes under an ascii locale.

BEGIN;

-- ----------------------------------------------------------------------------
-- lexical_miss_log: one row per tracked recall, recording whether the FTS5/BM25
-- leg returned any hits. fts_hits == 0 is a "lexical miss" -- a query the
-- keyword leg could not serve, which is exactly the gap a future sparse/ColBERT
-- lane would fill. The ratio of zero-hit rows over a recent window is the
-- miss-rate signal the wake-gate reads (AUTO-DETECT, never AUTO-PULL).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lexical_miss_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    query     TEXT    NOT NULL,
    ts        TEXT    NOT NULL DEFAULT (datetime('now')),
    fts_hits  INTEGER NOT NULL DEFAULT 0
);

-- Index for the gate's "last N rows" window scan (ORDER BY id DESC LIMIT N) and
-- for any miss-rate aggregation over fts_hits.
CREATE INDEX IF NOT EXISTS idx_lexical_miss_log_ts
    ON lexical_miss_log (ts);
CREATE INDEX IF NOT EXISTS idx_lexical_miss_log_hits
    ON lexical_miss_log (fts_hits);

-- ----------------------------------------------------------------------------
-- Migration marker (idempotent, additive). The metadata.value for the existing
-- 'schema_version' key is a meaningful STRING ('3.13-L4-self-eval' on the live
-- brain), NOT an integer -- so we do NOT clobber it. We record this migration
-- under its own dedicated keys, the same additive pattern the sibling organs
-- use, so the gate and the cmd_recall wiring can introspect readiness without
-- guessing. INSERT OR REPLACE keeps the marker re-runnable.
-- ----------------------------------------------------------------------------
INSERT OR REPLACE INTO metadata (key, value)
    VALUES ('migration_024_diversity', datetime('now'));

-- Record the diversity organ + dormant-lane scaffold as installed.
INSERT OR REPLACE INTO metadata (key, value)
    VALUES ('diversity_organ_installed', datetime('now'));
INSERT OR REPLACE INTO metadata (key, value)
    VALUES ('dormant_lanes', 'sparse,colbert');

COMMIT;
