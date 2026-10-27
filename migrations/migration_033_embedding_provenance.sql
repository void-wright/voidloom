-- migration_033_embedding_provenance.sql
--
-- PER-ROW VECTOR PROVENANCE. Written by `organs/memory_write.py`,
-- read by `memory_write.provenance_coverage`, and by nothing else until a reader
-- is taught to filter on it.
--
-- THE DEFECT THIS ENDS. Every vector table is (memory_id, embedding) and nothing
-- on disk records WHICH MODEL produced a given row. The producing model lives in
-- ONE MUTABLE metadata row (`active_embedding_model`) that is overwritten on each
-- tier change. Two shipped tiers -- jina (memory_embeddings_v2) and Qwen
-- (memory_embeddings_v3) -- are BOTH 1024 floats wide, so a mixed population is
-- shape-identical to a clean one and no reader can tell them apart by looking at
-- the bytes. Measured 2026-09-07: `embedding_upgrade_log` accounts for 2,606 of
-- 14,992 vectors; 82.6% had NO recorded producer at all.
--
-- A SIDECAR, NOT A COLUMN. 19 files / 123 sites name the vector tables by literal
-- string, and a column on a vec0 virtual table is an unverified capability of the
-- installed extension. A sidecar changes ZERO existing readers; the reader that
-- needs provenance JOINS on it. The primary key is (memory_id, vec_table): a vec0
-- table's own key is memory_id, so a memory holds at most ONE vector per table,
-- hence at most one stamp per (memory, table). The producing model is an
-- attribute of that row, and the reader filters on (vec_table, model_id) derived
-- from the tier bijection -- never on a literal.
--
-- ⚠ NO COLUMN HERE HAS A DEFAULT. EVER. Every value is supplied BY THE WRITE.
--   A DEFAULT stamps every existing row identically -- including the ~3,451 active
--   memories that have no vector at all and every row written before a tier moved
--   -- so the column would assert a producer for rows nobody embedded, and the
--   migration would look complete the instant it ran. NOT NULL without DEFAULT is
--   the opposite discipline: the write MUST say, or the write fails. NULL means
--   UNKNOWN; UNKNOWN IS NOT ZERO. The test for this migration plants exactly the
--   DEFAULT-style stamp on a brain with vectorless rows and requires the invariant
--   to go RED on exactly those rows.
--
--   The CHECK constraints below are not defaults: they REFUSE a write that
--   supplies an empty or malformed value, which is the same discipline pointed
--   the other way.
--
-- content_hash is the sha256 of the TEXT THAT WAS EMBEDDED. The brain edits
-- itself (3,411 memories had updated_at > created_at on 2026-09-07); a presence
-- check cannot see a memory whose text changed after embedding, a hash can. This
-- is the column Amendment 1 / A2's content-hash predicate reads (M1-C10).
--
-- model_ref is the identity of the WEIGHTS: the revision the code pins for that
-- tier when it pins one (embedding_upgrade.JINA_REVISION / NOMIC_REVISION), else
-- the commit the HF cache's refs/main resolved to when the weights were fetched,
-- else the literal 'unresolved' -- a VALUE the write supplies, distinguishable
-- from a row that was never stamped.
--
-- Additive and idempotent: IF NOT EXISTS on both objects, applied statement-at-a-
-- time by schema_migrator (never executescript -- see memory_write for why).
-- Drew's live brain is not a subject of this migration (THE SPLIT); applying it
-- there creates an empty sidecar and touches no vector.
CREATE TABLE IF NOT EXISTS embedding_provenance (
    memory_id    INTEGER NOT NULL,
    vec_table    TEXT    NOT NULL CHECK (vec_table <> ''),      -- the table holding the vector; joins the bijection
    model_id     TEXT    NOT NULL CHECK (model_id <> ''),       -- e.g. 'Qwen/Qwen3-Embedding-0.6B', from EMBED_TIERS
    model_ref    TEXT    NOT NULL CHECK (model_ref <> ''),      -- weights revision, or the literal 'unresolved'
    dim          INTEGER NOT NULL CHECK (dim > 0),              -- asserted equal to the vector's width at write
    content_hash TEXT    NOT NULL CHECK (length(content_hash) = 64),  -- sha256 hex of the embedded text
    embedded_at  TEXT    NOT NULL CHECK (embedded_at <> ''),    -- ISO 8601 UTC, the writer's clock
    organ        TEXT    NOT NULL CHECK (organ <> ''),          -- who wrote it
    PRIMARY KEY (memory_id, vec_table)
);
CREATE INDEX IF NOT EXISTS idx_embedding_provenance_model
    ON embedding_provenance (vec_table, model_id);
