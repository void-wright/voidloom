-- migration_039_gap_research_log_thinker.sql
--
-- NUMBER: first written as 034, then renumbered 039 so it orders after every migration number already claimed
-- elsewhere (the gaps 025/026 would also have ordered this file ahead of 027-033, which it was tested after). The migrator keys files by FULL FILENAME and decides 'already applied' from the
-- brain's schema, so a brain that applied it as 034 skips this file (executed: iterations/scratch_records/r2_w1/
-- mig039_rename.json).
--
-- WIDEN gap_research_log's two closed CHECK lists so the overnight thinker's rows can be written honestly.
-- (model-choice unit, Round 1, Wave B -- the coordinator's ruling on Wave A: a live-brain schema change is a NUMBERED
-- MIGRATION applied by the installer's own migration step, which gives it a pre-image, the journal and a read-back;
-- an organ never rebuilds a table at run time.)
--
-- WHY. migration_015 closed the two lists:
--     dispatch_method IN ('claude_cli', 'agy', 'manual')
--     status          IN ('running', 'success', 'error', 'timeout')
-- The thinker seam dispatches every gap as 'thinker' (the backend is named in dispatch_command), a backend with no
-- web makes a gap 'skipped', and the Warden HOLD path has always written 'held' -- which the old CHECK refuses (latent
-- until the first HOLD). SQLite cannot relax a CHECK with ALTER TABLE, so this is the house table rebuild
-- (migration_022's shape): CREATE the new table, COPY every row, DROP the old one, RENAME the new one into place,
-- RECREATE the index. Nothing references gap_research_log (a leaf: no view, no trigger, no foreign key points at it);
-- its own foreign key to gap_inventory(id) is kept, word for word.
--
-- WHAT IS ASSERTED, inside the migration's one transaction (organs/schema_migrator.py runs every file ALL-OR-NOTHING:
-- a failed assertion rolls the whole file back, nothing kept, and the installer's migration step names the failure):
--   * the table is the one this migration knows -- exactly migration_015's eleven columns, no more, no fewer (a
--     table with a column this file does not copy is refused, never truncated to eleven);
--   * every row is copied, and every row is there after the rename;
--   * foreign-key violations after == before (a pre-existing orphan is not this file's to fix, and none is added);
--   * the AUTOINCREMENT high-water mark (sqlite_sequence) is kept, so no id is ever handed out twice.
-- Each assertion is a named CHECK on a TEMP table (a PRAGMA cannot run inside the file's transaction: the migrator
-- runs PRAGMAs first); the failure message is the CHECK's name.
--
-- ALREADY APPLIED is decided from the WORLD (schema_migrator.migration_deltas): a CHECK list is invisible to its
-- object/column signature, so this file also creates the index idx_gap_research_log_method_status. That index is
-- created in the same transaction as the rebuild, so its presence means the rebuild committed, and a second apply is
-- skipped (no statement runs). It also serves the doctor's per-outcome reads of the log.
--
-- PRAGMA legacy_alter_table=ON: the RENAME below must not re-validate every view in the brain (migration_022: one
-- dangling view anywhere aborts a rename) and must not rewrite any reference (there are none). It is hoisted before the
-- transaction by the migrator; renames later in the same run are new-name-to-old-name, which it leaves unchanged.
--
-- ROLLBACK (executed in iterations/scratch_records/r1_waveB/migration_034_proof.* (recorded under the file's first number, 034)): restore the installer's pre-image
-- of the brain, or rebuild the table back with migration_015's lists -- possible only while no 'thinker'/'skipped'/'held'
-- row exists, which is why the pre-image is the rollback of record.

PRAGMA legacy_alter_table=ON;

CREATE TEMP TABLE _m039_before (
    n_rows INTEGER NOT NULL,
    n_fk   INTEGER NOT NULL,
    seq    INTEGER NOT NULL
);
INSERT INTO _m039_before (n_rows, n_fk, seq)
SELECT (SELECT COUNT(*) FROM gap_research_log),
       (SELECT COUNT(*) FROM pragma_foreign_key_check('gap_research_log')),
       COALESCE((SELECT seq FROM sqlite_sequence WHERE name = 'gap_research_log'), 0);

CREATE TEMP TABLE _m039_shape (
    n INTEGER CONSTRAINT gap_research_log_is_not_the_eleven_column_table_migration_039_knows CHECK (n = 11)
);
INSERT INTO _m039_shape (n) SELECT COUNT(*) FROM pragma_table_info('gap_research_log');
INSERT INTO _m039_shape (n)
SELECT COUNT(*) FROM pragma_table_info('gap_research_log')
 WHERE name IN ('id', 'gap_id', 'dispatch_method', 'dispatch_command', 'started_at', 'completed_at', 'status',
                'result_memory_id', 'wall_clock_seconds', 'error_message', 'raw_output_length');

CREATE TABLE _gap_research_log_039 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gap_id INTEGER NOT NULL REFERENCES gap_inventory(id),
    dispatch_method TEXT NOT NULL CHECK(dispatch_method IN ('claude_cli', 'agy', 'manual', 'thinker')),
    dispatch_command TEXT DEFAULT NULL,    -- the command, or 'thinker:<backend> | <prompt head>'
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT DEFAULT NULL,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'success', 'error', 'timeout', 'held', 'skipped')),
    result_memory_id INTEGER DEFAULT NULL, -- memory created from research
    wall_clock_seconds REAL DEFAULT NULL,
    error_message TEXT DEFAULT NULL,
    raw_output_length INTEGER DEFAULT NULL
);

INSERT INTO _gap_research_log_039
       (id, gap_id, dispatch_method, dispatch_command, started_at, completed_at, status, result_memory_id,
        wall_clock_seconds, error_message, raw_output_length)
SELECT id, gap_id, dispatch_method, dispatch_command, started_at, completed_at, status, result_memory_id,
       wall_clock_seconds, error_message, raw_output_length
  FROM gap_research_log;

CREATE TEMP TABLE _m039_copied (
    want INTEGER NOT NULL,
    got  INTEGER NOT NULL,
    CONSTRAINT migration_039_did_not_copy_every_row_of_gap_research_log CHECK (want = got)
);
INSERT INTO _m039_copied (want, got) SELECT n_rows, (SELECT COUNT(*) FROM _gap_research_log_039) FROM _m039_before;

DROP TABLE gap_research_log;
ALTER TABLE _gap_research_log_039 RENAME TO gap_research_log;
CREATE INDEX IF NOT EXISTS idx_gap_research_gap ON gap_research_log(gap_id);
CREATE INDEX IF NOT EXISTS idx_gap_research_log_method_status ON gap_research_log(dispatch_method, status);

-- the AUTOINCREMENT high-water mark: never lower than it was before the rebuild
UPDATE sqlite_sequence
   SET seq = (SELECT MAX(sqlite_sequence.seq, _m039_before.seq) FROM _m039_before)
 WHERE name = 'gap_research_log';
INSERT INTO sqlite_sequence (name, seq)
SELECT 'gap_research_log', seq FROM _m039_before
 WHERE seq > 0 AND NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'gap_research_log');

CREATE TEMP TABLE _m039_after (
    want_rows INTEGER NOT NULL,
    got_rows  INTEGER NOT NULL,
    want_fk   INTEGER NOT NULL,
    got_fk    INTEGER NOT NULL,
    want_seq  INTEGER NOT NULL,
    got_seq   INTEGER NOT NULL,
    CONSTRAINT migration_039_lost_rows_of_gap_research_log CHECK (want_rows = got_rows),
    CONSTRAINT migration_039_changed_the_foreign_key_violations_of_gap_research_log CHECK (want_fk = got_fk),
    CONSTRAINT migration_039_lowered_the_autoincrement_mark_of_gap_research_log CHECK (got_seq >= want_seq)
);
INSERT INTO _m039_after (want_rows, got_rows, want_fk, got_fk, want_seq, got_seq)
SELECT n_rows, (SELECT COUNT(*) FROM gap_research_log),
       n_fk, (SELECT COUNT(*) FROM pragma_foreign_key_check('gap_research_log')),
       seq, COALESCE((SELECT seq FROM sqlite_sequence WHERE name = 'gap_research_log'), 0)
  FROM _m039_before;

DROP TABLE _m039_after;
DROP TABLE _m039_copied;
DROP TABLE _m039_shape;
DROP TABLE _m039_before;

INSERT OR REPLACE INTO metadata (key, value, updated_at)
    VALUES ('migration_039_gap_research_log_thinker', 'applied', datetime('now'));
