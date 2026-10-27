-- ============================================================================
-- Voidloom L5-M5 "The Warden" — migration 029: the untrusted_source birth-mark
-- ----------------------------------------------------------------------------
-- An IMMUTABLE, VISIBLE provenance birth-mark on web-sourced (untrusted external)
-- writes. A sibling to L5-G's surprise birth-certificate (encoding_surprise +
-- surprise_finalized): computed/stamped ONCE at ingestion, with NO clear-path, so
-- "a web claim quietly becomes a trusted fact" (provenance laundering) is
-- structurally impossible — the mark survives consolidation/dreaming/any UPDATE.
--
-- IDEMPOTENT: this file is applied programmatically by warden.ensure_warden_
-- migration(), which guards the ADD COLUMN (SQLite has no ADD COLUMN IF NOT
-- EXISTS) by inspecting PRAGMA table_info first. The TRIGGER + metadata marker
-- use IF NOT EXISTS / INSERT OR IGNORE so a re-apply is a clean no-op.
--
-- PREFER-REUSE NOTE: the existing memory_provenance table is a derivation-EDGE
-- table (derived_memory_id + source_memory_id both NOT NULL) — it models
-- memory->memory derivations, not "this row was born from an untrusted external
-- source with no source memory". The Warden DOES additionally write a
-- memory_provenance row (transformation_type='untrusted_web_ingest', confidence
-- ~0.5) so the L5-I-α trust organ FLOORS the row LOW in recall — but the
-- self-pointing edge it would need for the birth-MARK itself is semantically
-- wrong, so the visible, queryable, trigger-protected flag lives as a dedicated
-- column. The column is the anti-laundering guarantee; the provenance row is the
-- trust-floor activation. Two jobs, two mechanisms.
-- ============================================================================

-- (1) THE BIRTH-MARK COLUMN ---------------------------------------------------
-- 0 = not an untrusted external source (the default for Drew's writes, MILAMBER's
--     dreams, internal consolidation — everything the Warden never touches).
-- 1 = stamped at ingestion by the Warden as a web-sourced / untrusted-external
--     write. NEVER cleared back to 0 (see the trigger).
-- NOTE: the actual ADD COLUMN is issued by warden.ensure_warden_migration() only
-- when the column is absent (PRAGMA-guarded). Kept here for documentation +
-- standalone application on a brain that has never seen the Warden.
ALTER TABLE memories ADD COLUMN untrusted_source INTEGER NOT NULL DEFAULT 0;

-- (2) THE IMMUTABILITY TRIGGER (the anti-laundering guarantee) -----------------
-- A BEFORE UPDATE trigger that RAISES if any UPDATE would clear an already-set
-- birth-mark (1 -> 0 or 1 -> NULL). Consolidation, dreaming, importance decay,
-- access-count bumps, version increments — every other UPDATE passes untouched.
-- Setting the mark (0 -> 1) is allowed exactly once at ingestion; flipping it
-- back is forbidden at the STORAGE layer, so no application code path (present or
-- future) can launder a web claim into a trusted fact by accident or by exploit.
CREATE TRIGGER IF NOT EXISTS trg_untrusted_source_immutable
BEFORE UPDATE OF untrusted_source ON memories
FOR EACH ROW
WHEN OLD.untrusted_source = 1
     AND (NEW.untrusted_source IS NULL OR NEW.untrusted_source = 0)
BEGIN
    SELECT RAISE(ABORT,
        'untrusted_source birth-mark is immutable: a web-sourced memory cannot be laundered into a trusted fact (L5-M5 Warden)');
END;

-- (3) A SUPPORTING INDEX (cheap; lets recall/audit filter the untrusted band) --
CREATE INDEX IF NOT EXISTS idx_memories_untrusted_source
    ON memories(untrusted_source)
    WHERE untrusted_source = 1;

-- (4) ADDITIVE METADATA MARKER (NOT schema_version — additive, like migration_028)
INSERT OR IGNORE INTO metadata (key, value)
    VALUES ('warden_migration_029_applied', '1');
