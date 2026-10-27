-- Migration 020: The Quarterly Arcane Manifest — L4 Piece 7
-- Wizard Library Level 4 Component 7
--
-- A quarterly co-authored prose document in which the Council of Mages
-- reflects on what the wizard library built, learned, and became during
-- the quarter. Written in first-person plural ("we"), read like a book.
--
-- Idempotent: safe to re-run. All DDL uses IF NOT EXISTS.
-- Schema version: 3.12-L4-arcane-manifest
--
-- Design notes:
--   - manifest_content holds the full prose narrative (MILAMBER's synthesis).
--   - sections holds per-speaker JSON so individual voices can be re-read.
--   - council_session_id is OPTIONAL — the manifest MAY be generated
--     outside a formal council session (e.g., first ever manifest).
--   - manifest_memory_id links the final manifest to its memory record
--     so recall can surface it.
--   - drew_addendum is Drew's optional closing voice (Round 3 equivalent).
--   - data_snapshot stores the JSON of gathered quarter data so the
--     manifest is self-documenting (what data was available at generation).

CREATE TABLE IF NOT EXISTS arcane_manifests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Quarter identifier (e.g., "2026-Q2")
    quarter TEXT NOT NULL,

    -- Decomposed for indexing and filtering
    year INTEGER NOT NULL,
    quarter_number INTEGER NOT NULL
        CHECK(quarter_number >= 1 AND quarter_number <= 4),

    -- Optional link to a formal council session
    council_session_id INTEGER DEFAULT NULL
        REFERENCES council_sessions(id),

    -- Optional link to the memory storing this manifest
    manifest_memory_id INTEGER DEFAULT NULL,

    -- The full prose document (MILAMBER's final synthesis)
    manifest_content TEXT NOT NULL,

    -- Per-speaker sections: JSON array of {speaker, section_title, section_content}
    sections TEXT NOT NULL,

    -- Drew's optional closing voice
    drew_addendum TEXT DEFAULT NULL,

    -- Word count of manifest_content (for tracking narrative growth)
    word_count INTEGER DEFAULT 0,

    -- JSON snapshot of the data gathered for this manifest
    -- (top memories, sleep run stats, dream insights, gap counts, etc.)
    data_snapshot TEXT DEFAULT NULL,

    -- Lifecycle
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN ('draft', 'final', 'drew_reviewed')),

    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finalized_at TEXT DEFAULT NULL
);

-- Indexes for common access patterns
CREATE INDEX IF NOT EXISTS idx_manifest_quarter
    ON arcane_manifests(quarter);

CREATE INDEX IF NOT EXISTS idx_manifest_year_qtr
    ON arcane_manifests(year, quarter_number);

CREATE INDEX IF NOT EXISTS idx_manifest_status
    ON arcane_manifests(status);

CREATE INDEX IF NOT EXISTS idx_manifest_memory
    ON arcane_manifests(manifest_memory_id)
    WHERE manifest_memory_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_manifest_council
    ON arcane_manifests(council_session_id)
    WHERE council_session_id IS NOT NULL;

-- Schema version bump
INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('schema_version', '3.12-L4-arcane-manifest', datetime('now'));
