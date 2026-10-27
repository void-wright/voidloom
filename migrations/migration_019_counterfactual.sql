-- Migration 019: Counterfactual Memory — L4 Piece 5
-- Wizard Library Level 4 Component 5
--
-- At significant decision points, the system snapshots what was considered
-- but NOT chosen — the paths not taken. The Council of Mages (Piece 3)
-- provides a decision_weight_score that determines which decisions are
-- worth tracking. Quarterly, the system revisits old counterfactuals:
-- "was that the right call? Has the world changed?"
--
-- Idempotent: safe to re-run. All DDL uses IF NOT EXISTS.
-- Schema version: 3.11-L4-counterfactual
--
-- Prerequisite: migration_018_council.sql (council_sessions table).
-- This table references memories(id) for the decision memory and
-- optionally council_sessions(id) for council-weighted decisions.
--
-- Design notes:
--   - alternative_paths stored as JSON array of objects:
--     [{path: str, predicted_outcome: str, rationale: str}]
--   - council_weight_score is copied from council_sessions at capture time
--     (denormalized for fast filtering without joins)
--   - status lifecycle: active -> revisited -> validated | reversed | archived
--   - revisit_interval_days default 90 = quarterly cadence
--   - revisit_log stored as JSON array in revisit_notes for audit trail:
--     [{revisited_at: str, outcome: str, notes: str}]
--   - captured_by tracks provenance: manual, council_auto, cron_revisit, cmd_store

CREATE TABLE IF NOT EXISTS counterfactual_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- The memory that records the decision itself
    decision_memory_id INTEGER NOT NULL REFERENCES memories(id),

    -- Optional link to the council session that produced this decision.
    -- No REFERENCES constraint: council_session_id is nullable (manual captures
    -- have no session), and council sessions are never deleted. Application-level
    -- validation in capture_counterfactual() checks session existence when provided.
    council_session_id INTEGER DEFAULT NULL,

    -- Human-readable topic (may differ from council topic if manual)
    topic TEXT NOT NULL,

    -- What was actually chosen
    chosen_path TEXT NOT NULL,

    -- JSON array: [{path: str, predicted_outcome: str, rationale: str}]
    alternative_paths TEXT NOT NULL,

    -- Denormalized from council_sessions.decision_weight_score (or manual)
    -- 0.0 = trivial, 1.0 = architectural / irreversible
    council_weight_score REAL DEFAULT NULL
        CHECK(council_weight_score IS NULL
              OR (council_weight_score >= 0.0 AND council_weight_score <= 1.0)),

    -- When should this decision be revisited? Free-text criteria.
    criteria_for_revisit TEXT DEFAULT NULL,

    -- How often to revisit (days). Default 90 = quarterly.
    revisit_interval_days INTEGER DEFAULT 90,

    -- When was this last revisited?
    last_revisited TEXT DEFAULT NULL,

    -- How many times has this been revisited?
    revisit_count INTEGER DEFAULT 0,

    -- JSON array of revisit log entries:
    -- [{revisited_at: str, outcome: str, notes: str, revisited_by: str}]
    revisit_notes TEXT DEFAULT NULL,

    -- Lifecycle status
    status TEXT DEFAULT 'active' CHECK(status IN (
        'active',       -- decision is live, may be revisited
        'revisited',    -- has been revisited at least once, still active
        'validated',    -- revisit confirmed: right call
        'reversed',     -- revisit found: wrong call, course-corrected
        'archived'      -- no longer relevant (project ended, etc.)
    )),

    -- When this counterfactual was captured
    captured_at TEXT NOT NULL DEFAULT (datetime('now')),

    -- How this counterfactual was captured
    captured_by TEXT DEFAULT 'manual' CHECK(captured_by IN (
        'manual',           -- Drew explicitly captured via cmd
        'council_auto',     -- Auto-captured from high-weight council session
        'cron_revisit',     -- Created during cron revisit cycle
        'cmd_store'         -- Captured via --with-alternatives on store
    ))
);

-- Indexes for common access patterns

-- Fast lookup of pending revisits: active + last_revisited old enough
CREATE INDEX IF NOT EXISTS idx_cf_status_revisit
    ON counterfactual_decisions(status, last_revisited);

-- Filter by weight for high-importance decisions
CREATE INDEX IF NOT EXISTS idx_cf_weight
    ON counterfactual_decisions(council_weight_score)
    WHERE council_weight_score IS NOT NULL;

-- Link back to the decision memory
CREATE INDEX IF NOT EXISTS idx_cf_memory
    ON counterfactual_decisions(decision_memory_id);

-- Link back to council session
CREATE INDEX IF NOT EXISTS idx_cf_council
    ON counterfactual_decisions(council_session_id)
    WHERE council_session_id IS NOT NULL;

-- Captured-at for chronological listing
CREATE INDEX IF NOT EXISTS idx_cf_captured
    ON counterfactual_decisions(captured_at);

-- Schema version bump
INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('schema_version', '3.11-L4-counterfactual', datetime('now'));
