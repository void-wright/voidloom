-- migration_043_retire_encumbered_default.sql
--
-- WHAT: embedding_upgrade_log loses the two DEFAULTs migration_013 gave it, and gains the checksum identity of the
-- tier each upgrade ran under.
--   * migration_013 declared `model_name TEXT NOT NULL DEFAULT '<one 1024-wide model, by repo id>'` and
--     `embedding_dim INTEGER NOT NULL DEFAULT 1024`. A DEFAULT naming one model stamps that model on any row whose
--     writer forgot to say which model it ran -- a fact nobody observed. Every writer now states both columns.
--     (migration_013 itself no longer carries the model DEFAULT, so a fresh brain never had it; this rebuild exists
--     for every brain created before that edit, and runs on fresh brains too, so both paths end in the same DDL.)
--     The 1024-wide model stays selectable for a personal install; only the DEFAULT goes.
--   * weights_hash TEXT (nullable, NO DEFAULT): the same identity every vector row carries since migration_042. NULL
--     on rows written before this column existed; never back-filled, because the hash those runs loaded is not
--     knowable now.
--
-- HOW: SQLite cannot ALTER a DEFAULT away, so this is the house table rebuild: CREATE the new table, COPY every row,
-- DROP the old one, RENAME the new one into place. Nothing references embedding_upgrade_log by foreign key, view or
-- trigger (measured on a backup copy of a 31,904-memory brain at migration_041).
--
-- WHAT IS ASSERTED, inside the migration's one transaction (organs/schema_migrator.py runs every file ALL-OR-NOTHING):
--   * the table is the one this file knows -- exactly migration_013's eleven columns;
--   * every row is copied, and every row is there after the rename;
--   * the AUTOINCREMENT high-water mark (sqlite_sequence) is kept, so no run id is ever handed out twice.
-- Each assertion is a named CHECK on a TEMP table; the failure message is the CHECK's name.
-- Every read and write of the mark names `main.sqlite_sequence`: on the migrator's connection a TEMP
-- sqlite_sequence exists (its vec0 probe creates one), and an unqualified name resolves to TEMP first -- the
-- mark guard would then read and write a table that is not the brain's, and pass vacuously.
--
-- ALREADY APPLIED is decided from the WORLD (schema_migrator.migration_deltas): this file adds the column
-- embedding_upgrade_log.weights_hash, so a brain that has it skips the file.
--
-- ROLLBACK: restoring the pre-image database is the rollback of record. The column is additive; code that predates
-- this file and omits model_name / embedding_dim on INSERT is refused by NOT NULL, loudly, instead of being stamped
-- with a model it may not have run.

PRAGMA legacy_alter_table=ON;

CREATE TEMP TABLE _m043_before (
    n_rows INTEGER NOT NULL,
    seq    INTEGER NOT NULL
);
INSERT INTO _m043_before (n_rows, seq)
SELECT (SELECT COUNT(*) FROM embedding_upgrade_log),
       COALESCE((SELECT seq FROM main.sqlite_sequence WHERE name = 'embedding_upgrade_log'), 0);

CREATE TEMP TABLE _m043_shape (
    n INTEGER CONSTRAINT embedding_upgrade_log_is_not_the_eleven_column_table_migration_043_knows CHECK (n = 11)
);
INSERT INTO _m043_shape (n) SELECT COUNT(*) FROM pragma_table_info('embedding_upgrade_log');
INSERT INTO _m043_shape (n)
SELECT COUNT(*) FROM pragma_table_info('embedding_upgrade_log')
 WHERE name IN ('id', 'started_at', 'completed_at', 'model_name', 'embedding_dim', 'memories_processed',
                'memories_total', 'status', 'error_message', 'ab_comparison_json', 'notes');

CREATE TABLE embedding_upgrade_log_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    model_name TEXT NOT NULL,
    embedding_dim INTEGER NOT NULL,
    memories_processed INTEGER DEFAULT 0,
    memories_total INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'in_progress',
    -- status: in_progress, completed, completed_with_errors, failed, aborted
    error_message TEXT,
    ab_comparison_json TEXT,
    -- Stores the full A/B comparison result as JSON
    notes TEXT,
    weights_hash TEXT
    -- the checksum identity of the tier the upgrade ran under; NULL for rows written before this column existed
);

INSERT INTO embedding_upgrade_log_new
       (id, started_at, completed_at, model_name, embedding_dim, memories_processed, memories_total, status,
        error_message, ab_comparison_json, notes, weights_hash)
SELECT id, started_at, completed_at, model_name, embedding_dim, memories_processed, memories_total, status,
       error_message, ab_comparison_json, notes, NULL
  FROM embedding_upgrade_log;

CREATE TEMP TABLE _m043_copied (
    want INTEGER NOT NULL,
    got  INTEGER NOT NULL,
    CONSTRAINT migration_043_did_not_copy_every_row_of_embedding_upgrade_log CHECK (want = got)
);
INSERT INTO _m043_copied (want, got) SELECT n_rows, (SELECT COUNT(*) FROM embedding_upgrade_log_new) FROM _m043_before;

DROP TABLE embedding_upgrade_log;
ALTER TABLE embedding_upgrade_log_new RENAME TO embedding_upgrade_log;

-- the AUTOINCREMENT high-water mark: never lower than it was before the rebuild
UPDATE main.sqlite_sequence
   SET seq = (SELECT MAX(main.sqlite_sequence.seq, _m043_before.seq) FROM _m043_before)
 WHERE name = 'embedding_upgrade_log';
INSERT INTO main.sqlite_sequence (name, seq)
SELECT 'embedding_upgrade_log', seq FROM _m043_before
 WHERE seq > 0 AND NOT EXISTS (SELECT 1 FROM main.sqlite_sequence WHERE name = 'embedding_upgrade_log');

CREATE TEMP TABLE _m043_after (
    want_rows INTEGER NOT NULL,
    got_rows  INTEGER NOT NULL,
    want_seq  INTEGER NOT NULL,
    got_seq   INTEGER NOT NULL,
    CONSTRAINT migration_043_lost_rows_of_embedding_upgrade_log CHECK (want_rows = got_rows),
    CONSTRAINT migration_043_lowered_the_autoincrement_mark_of_embedding_upgrade_log CHECK (got_seq >= want_seq)
);
INSERT INTO _m043_after (want_rows, got_rows, want_seq, got_seq)
SELECT n_rows, (SELECT COUNT(*) FROM embedding_upgrade_log),
       seq, COALESCE((SELECT seq FROM main.sqlite_sequence WHERE name = 'embedding_upgrade_log'), 0)
  FROM _m043_before;

DROP TABLE _m043_after;
DROP TABLE _m043_copied;
DROP TABLE _m043_shape;
DROP TABLE _m043_before;
