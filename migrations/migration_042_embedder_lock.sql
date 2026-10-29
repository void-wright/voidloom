-- migration_042_embedder_lock.sql
--
-- WHAT: every provenance stamp can name the WEIGHTS that produced its vector, not only the hub's name for them.
-- embedding_provenance (migration_033) named the producer by `model_id` (what the hub calls the model) and
-- `model_ref` (the hub revision it was fetched at). Neither is a fact a reader can re-derive from the bytes on disk.
-- This file adds three columns:
--   weights_hash   sha256 over the sha256sum lines of the model files the embedder LOADED (weights, config,
--                  tokenizer, sentence-transformers modules, remote-code *.py). The install lock
--                  (<data-dir>/embedder.lock.json) carries the same value as the promise; a reader compares the two.
--   input_kind     'text' (the row's content was encoded) or 'centroid' (an average of member vectors).
--   inputs         JSON array of member memory ids for a centroid; NULL for text.
-- `model_ref` keeps its name; its meaning narrows to "the instruction for fetching the bytes", never their identity.
--
-- NO COLUMN HAS A DEFAULT (migration_033's rule). SQLite cannot ADD a NOT NULL column without one, so this is the
-- house table rebuild (migration_039's and migration_040's shape): CREATE the new table, COPY every row, DROP the old
-- one, RENAME the new one into place, recreate the indexes.
--
-- LEGACY ROWS (written before weights_hash existed) are carried forward, never dropped and never back-filled with a
-- checksum:
--   weights_hash = 'unresolved'  a declared absence, NOT an identity: nobody recorded the checksum of the weights that
--                                produced these vectors. It is the same spelling model_ref already uses for "unknown",
--                                and a reader that compares against an install lock never matches it.
--   input_kind   = 'text'        a fact: every writer before this file encoded text.  inputs = NULL follows.
-- A row leaves 'unresolved' only by being re-embedded under an install lock. Every writer after this file states all
-- three values itself.
--
-- WHAT IS ASSERTED, inside the migration's one transaction (organs/schema_migrator.py runs every file ALL-OR-NOTHING):
--   * the table is the one this file knows -- exactly migration_033's eight columns (a table with a column this file
--     does not copy is refused, never truncated);
--   * every row is copied, and every row is there after the rename.
-- Each assertion is a named CHECK on a TEMP table; the failure message is the CHECK's name.
--
-- ALREADY APPLIED is decided from the WORLD (schema_migrator.migration_deltas): this file adds the columns
-- embedding_provenance.weights_hash / input_kind / inputs, so a brain that has them skips the file.
--
-- Nothing references embedding_provenance by foreign key, view or trigger (measured on a backup copy of a 31,904-memory
-- brain at migration_041: sqlite_master holds only the table, its primary-key autoindex and
-- idx_embedding_provenance_model).
--
-- ROLLBACK: restoring the pre-image database is the rollback of record. Code that predates this file names its
-- columns explicitly on INSERT and cannot write the NOT NULL columns, so it must not run against a migrated brain.

PRAGMA legacy_alter_table=ON;

CREATE TEMP TABLE _m042_before (
    n_rows INTEGER NOT NULL
);
INSERT INTO _m042_before (n_rows) SELECT COUNT(*) FROM embedding_provenance;

CREATE TEMP TABLE _m042_shape (
    n INTEGER CONSTRAINT embedding_provenance_is_not_the_eight_column_table_migration_042_knows CHECK (n = 8)
);
INSERT INTO _m042_shape (n) SELECT COUNT(*) FROM pragma_table_info('embedding_provenance');
INSERT INTO _m042_shape (n)
SELECT COUNT(*) FROM pragma_table_info('embedding_provenance')
 WHERE name IN ('memory_id', 'vec_table', 'model_id', 'model_ref', 'dim', 'content_hash', 'embedded_at', 'organ');

CREATE TABLE embedding_provenance_new (
    memory_id    INTEGER NOT NULL,
    vec_table    TEXT    NOT NULL CHECK (vec_table <> ''),        -- the table holding the vector
    model_id     TEXT    NOT NULL CHECK (model_id <> ''),         -- the hub name, e.g. 'nomic-ai/nomic-embed-text-v1.5'
    model_ref    TEXT    NOT NULL CHECK (model_ref <> ''),        -- the hub FETCH revision (an instruction, not an identity)
    weights_hash TEXT    NOT NULL CHECK (weights_hash <> ''),     -- sha256 of the loaded files' sha256sum lines, or 'unresolved'
    dim          INTEGER NOT NULL CHECK (dim > 0),                -- asserted equal to the vector's width at write
    content_hash TEXT    NOT NULL CHECK (length(content_hash) = 64),  -- sha256 of the embedded text, or of the member vectors' bytes
    embedded_at  TEXT    NOT NULL CHECK (embedded_at <> ''),      -- ISO 8601 UTC, the writer's clock
    organ        TEXT    NOT NULL CHECK (organ <> ''),            -- who wrote it
    input_kind   TEXT    NOT NULL CHECK (input_kind IN ('text', 'centroid')),
    inputs       TEXT    CHECK ((input_kind = 'text' AND inputs IS NULL)
                                OR (input_kind = 'centroid' AND inputs IS NOT NULL AND inputs <> '')),
    PRIMARY KEY (memory_id, vec_table)
);

INSERT INTO embedding_provenance_new
       (memory_id, vec_table, model_id, model_ref, weights_hash, dim, content_hash, embedded_at, organ,
        input_kind, inputs)
SELECT memory_id, vec_table, model_id, model_ref, 'unresolved', dim, content_hash, embedded_at, organ,
       'text', NULL
  FROM embedding_provenance;

CREATE TEMP TABLE _m042_copied (
    want INTEGER NOT NULL,
    got  INTEGER NOT NULL,
    CONSTRAINT migration_042_did_not_copy_every_row_of_embedding_provenance CHECK (want = got)
);
INSERT INTO _m042_copied (want, got) SELECT n_rows, (SELECT COUNT(*) FROM embedding_provenance_new) FROM _m042_before;

DROP TABLE embedding_provenance;
ALTER TABLE embedding_provenance_new RENAME TO embedding_provenance;
CREATE INDEX IF NOT EXISTS idx_embedding_provenance_model
    ON embedding_provenance (vec_table, model_id);
CREATE INDEX IF NOT EXISTS idx_embedding_provenance_lock
    ON embedding_provenance (weights_hash, vec_table, memory_id);

CREATE TEMP TABLE _m042_after (
    want_rows INTEGER NOT NULL,
    got_rows  INTEGER NOT NULL,
    CONSTRAINT migration_042_lost_rows_of_embedding_provenance CHECK (want_rows = got_rows)
);
INSERT INTO _m042_after (want_rows, got_rows)
SELECT n_rows, (SELECT COUNT(*) FROM embedding_provenance) FROM _m042_before;

DROP TABLE _m042_after;
DROP TABLE _m042_copied;
DROP TABLE _m042_shape;
DROP TABLE _m042_before;
