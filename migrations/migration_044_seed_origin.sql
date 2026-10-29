-- migration_044_seed_origin.sql
--
-- WHAT: whether a memory came from the seed pack is a COLUMN of `memories`, not a tag convention.
--   seed    INTEGER NOT NULL DEFAULT 0   1 on exactly the rows the seed loader writes; 0 ("not a seed") everywhere
--                                        else. A tag-shaped flag would be absent by default on most rows, and a
--                                        filter over tags would pass vacuously; a column is present on every row.
--   origin  TEXT                         NULL on every row that is not a seed; on a seed row, the pack record's
--                                        identity `seed_pack:<seed_pack_version>#<content sha256[:16]>`. It is the
--                                        loader's idempotency key: a load that is killed and restarted converges
--                                        with no duplicates, because the key is checked before every insert.
--
-- The DEFAULT on `seed` is the honest value for every existing row: rows written before this column existed were
-- written by the owner's own writers, not by the seed loader.
--
-- ALREADY APPLIED is decided from the WORLD (schema_migrator.migration_deltas): the two columns are this file's
-- additive delta, so a brain that carries them skips the file. Applied statement-at-a-time inside ONE transaction.
ALTER TABLE memories ADD COLUMN seed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memories ADD COLUMN origin TEXT;
CREATE INDEX IF NOT EXISTS idx_memories_seed_origin ON memories(seed, origin);
