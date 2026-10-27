-- migration_040_sleep_run_lifecycle.sql
--
-- NUMBER: 040 is reserved for lane L3 by the round-1 task spec (DESSA §2: "040 -> L3, 041 -> L5"); main tops out at 039.
--
-- WHAT (contract C5, frozen before generation): the sleep loop's run rows can say how a run ENDED and how far it got.
--   * status gains 'timeout': the run's own budget ended where work cannot be carried over, or the process was told to
--     stop (systemd's TimeoutStartSec SIGTERM, a stop, Ctrl-C on the owner's catch-up run). 'error' is KEPT: rows
--     already hold it and the organ still writes it when no stage succeeds. The set is
--     {running, success, partial, error, timeout}.
--   * stage_reached TEXT: the last stage the run BEGAN (start, consolidation, edges, decay, reflection, importance,
--     quickening, communities, finalize). It is written and COMMITTED at each stage boundary, so a run killed without
--     warning (SIGKILL, the OOM killer, a power cut) still says where it died.
--   * stage_log TEXT: JSON, one entry per stage that ran -- its seconds and its counters (for Stage 2: input, processed,
--     remaining, watermark from/to). The per-stage log the journal also carries, kept with the row.
--   The Stage-2 progress mark itself is NOT a column: it is metadata.edge_watermark_id (C5), written by the organ in
--   the same transaction as the edges it accounts for. This file does not write it; a brain that has none bootstraps
--   it once from the last completed run (sleep_loop.bootstrap_edge_watermark).
--
-- WHY (health check 2026-10-14, diagnosis.json sleep_loop_root_cause): rows 116-123 sat 'running' with completed_at NULL
-- and every counter 0 -- eight nights killed by systemd at 600 s with nothing to close them -- and the organ-yield
-- sentinel rated the loop HEALTHY throughout. A row that can only say running/success/partial/error cannot say "timed
-- out in Stage 2", which is the one thing the owner needed to read.
--
-- HOW: SQLite cannot relax a CHECK with ALTER TABLE, so this is the house table rebuild (migration_022's and
-- migration_039's shape): CREATE the new table, COPY every row, DROP the old one, RENAME the new one into place.
-- Nothing references sleep_loop_runs by foreign key, view or trigger (measured on a backup copy of the reference brain:
-- sqlite_master holds no other object whose SQL names it). arcane_manifest's consolidation-sources JOIN reads it by id.
--
-- WHAT IS ASSERTED, inside the migration's one transaction (organs/schema_migrator.py runs every file ALL-OR-NOTHING):
--   * the table is the one this migration knows -- exactly migration_005's fifteen columns (a table with a column this
--     file does not copy is refused, never truncated);
--   * every row is copied, and every row is there after the rename;
--   * the AUTOINCREMENT high-water mark (sqlite_sequence) is kept, so no run id is ever handed out twice.
-- Each assertion is a named CHECK on a TEMP table; the failure message is the CHECK's name.
--
-- ALREADY APPLIED is decided from the WORLD (schema_migrator.migration_deltas): this file adds the columns
-- sleep_loop_runs.stage_reached and sleep_loop_runs.stage_log, so a brain that has them skips the file.
--
-- PRAGMA legacy_alter_table=ON: the RENAME must not re-validate every view in the brain (migration_022: one dangling
-- view anywhere aborts a rename). Hoisted before the transaction by the migrator.
--
-- ROLLBACK: the columns are additive and the old code names its columns explicitly (INSERT ... (started_at,
-- config_snapshot, consolidation_mode), UPDATE ... SET <named columns>), so code reverted to f275d09 runs on a migrated
-- brain unchanged; it would only fail to write 'timeout', which it never writes. A row the new code closed as 'timeout'
-- reads to the old code as "not success/partial" -- the same as 'error'. Restoring the pre-image brain is the full
-- rollback of record; rebuilding back to migration_005's CHECK is possible only while no 'timeout' row exists.

PRAGMA legacy_alter_table=ON;

CREATE TEMP TABLE _m040_before (
    n_rows INTEGER NOT NULL,
    seq    INTEGER NOT NULL
);
INSERT INTO _m040_before (n_rows, seq)
SELECT (SELECT COUNT(*) FROM sleep_loop_runs),
       COALESCE((SELECT seq FROM sqlite_sequence WHERE name = 'sleep_loop_runs'), 0);

CREATE TEMP TABLE _m040_shape (
    n INTEGER CONSTRAINT sleep_loop_runs_is_not_the_fifteen_column_table_migration_040_knows CHECK (n = 15)
);
INSERT INTO _m040_shape (n) SELECT COUNT(*) FROM pragma_table_info('sleep_loop_runs');
INSERT INTO _m040_shape (n)
SELECT COUNT(*) FROM pragma_table_info('sleep_loop_runs')
 WHERE name IN ('id', 'started_at', 'completed_at', 'status', 'memories_consolidated', 'clusters_found',
                'edges_extracted', 'edges_decayed', 'edges_pruned', 'reflections_generated',
                'importance_adjustments', 'consolidation_mode', 'error_log', 'config_snapshot', 'duration_seconds');

CREATE TABLE _sleep_loop_runs_040 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT DEFAULT NULL,
    status TEXT DEFAULT 'running' CHECK(status IN ('running', 'success', 'partial', 'error', 'timeout')),
    memories_consolidated INTEGER DEFAULT 0,
    clusters_found INTEGER DEFAULT 0,
    edges_extracted INTEGER DEFAULT 0,
    edges_decayed INTEGER DEFAULT 0,
    edges_pruned INTEGER DEFAULT 0,
    reflections_generated INTEGER DEFAULT 0,
    importance_adjustments INTEGER DEFAULT 0,
    consolidation_mode TEXT DEFAULT 'heuristic' CHECK(consolidation_mode IN ('heuristic', 'llm')),
    error_log TEXT DEFAULT NULL,
    config_snapshot TEXT DEFAULT NULL,
    duration_seconds REAL DEFAULT NULL,
    stage_reached TEXT DEFAULT NULL,      -- the last stage the run BEGAN (contract C5)
    stage_log TEXT DEFAULT NULL           -- JSON: per-stage seconds + counters
);

INSERT INTO _sleep_loop_runs_040
       (id, started_at, completed_at, status, memories_consolidated, clusters_found, edges_extracted, edges_decayed,
        edges_pruned, reflections_generated, importance_adjustments, consolidation_mode, error_log, config_snapshot,
        duration_seconds)
SELECT id, started_at, completed_at, status, memories_consolidated, clusters_found, edges_extracted, edges_decayed,
       edges_pruned, reflections_generated, importance_adjustments, consolidation_mode, error_log, config_snapshot,
       duration_seconds
  FROM sleep_loop_runs;

CREATE TEMP TABLE _m040_copied (
    want INTEGER NOT NULL,
    got  INTEGER NOT NULL,
    CONSTRAINT migration_040_did_not_copy_every_row_of_sleep_loop_runs CHECK (want = got)
);
INSERT INTO _m040_copied (want, got) SELECT n_rows, (SELECT COUNT(*) FROM _sleep_loop_runs_040) FROM _m040_before;

DROP TABLE sleep_loop_runs;
ALTER TABLE _sleep_loop_runs_040 RENAME TO sleep_loop_runs;
CREATE INDEX IF NOT EXISTS idx_sleep_loop_runs_status ON sleep_loop_runs(status);

-- the AUTOINCREMENT high-water mark: never lower than it was before the rebuild
UPDATE sqlite_sequence
   SET seq = (SELECT MAX(sqlite_sequence.seq, _m040_before.seq) FROM _m040_before)
 WHERE name = 'sleep_loop_runs';
INSERT INTO sqlite_sequence (name, seq)
SELECT 'sleep_loop_runs', seq FROM _m040_before
 WHERE seq > 0 AND NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'sleep_loop_runs');

CREATE TEMP TABLE _m040_after (
    want_rows INTEGER NOT NULL,
    got_rows  INTEGER NOT NULL,
    want_seq  INTEGER NOT NULL,
    got_seq   INTEGER NOT NULL,
    CONSTRAINT migration_040_lost_rows_of_sleep_loop_runs CHECK (want_rows = got_rows),
    CONSTRAINT migration_040_lowered_the_autoincrement_mark_of_sleep_loop_runs CHECK (got_seq >= want_seq)
);
INSERT INTO _m040_after (want_rows, got_rows, want_seq, got_seq)
SELECT n_rows, (SELECT COUNT(*) FROM sleep_loop_runs),
       seq, COALESCE((SELECT seq FROM sqlite_sequence WHERE name = 'sleep_loop_runs'), 0)
  FROM _m040_before;

DROP TABLE _m040_after;
DROP TABLE _m040_copied;
DROP TABLE _m040_shape;
DROP TABLE _m040_before;

INSERT OR REPLACE INTO metadata (key, value, updated_at)
    VALUES ('migration_040_sleep_run_lifecycle', 'applied', datetime('now'));
