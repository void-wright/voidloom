-- Migration 021: Monthly Brain Self-Evaluation — Wizard Library L4 Piece 9
-- The continuous growth mandate: the brain evaluates itself monthly.
--
-- Idempotent: safe to re-run. Uses CREATE TABLE IF NOT EXISTS.
--
-- Dependencies: memories table (core), sleep_loop_runs, dream_log,
--   whisper_digests, calibration_log, gap_inventory, skill_graduation_log,
--   memory_edges, council_sessions, persona_recall_profiles.
--
-- Phase 0 design: memory #4083 (threshold evolution path).

CREATE TABLE IF NOT EXISTS self_eval_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Which month this evaluation covers: "YYYY-MM" format.
    month TEXT NOT NULL,

    -- JSON object: {piece_name: {score: float, usage: int, errors: int, notes: str}}
    -- Covers every L2-L4 piece with quantitative health signals.
    health_scorecard TEXT NOT NULL,

    -- JSON array of enhancement proposals.
    -- Each: {title: str, rationale: str, evidence: str, priority: "high"|"medium"|"low",
    --         estimated_effort: str, piece_affected: str}
    proposed_enhancements TEXT NOT NULL,

    -- JSON array of deprecation proposals.
    -- Each: {title: str, rationale: str, evidence: str, piece_affected: str}
    proposed_deprecations TEXT DEFAULT '[]',

    -- JSON: {current_threshold: float, track_record: str,
    --         readiness_score: float, recommendation: "hold"|"increase"|"decrease",
    --         next_threshold: float|null, reasoning: str}
    threshold_assessment TEXT DEFAULT NULL,

    -- Persona evolution narrative: how MILAMBER grew this month.
    persona_evolution TEXT DEFAULT NULL,

    -- Drew's approval decisions.
    -- JSON: {approved: [{title: str, notes: str}], rejected: [{title: str, reason: str}],
    --         reviewed_at: str}
    drew_approved TEXT DEFAULT NULL,

    -- Link to the memory that stores this report for recall.
    report_memory_id INTEGER REFERENCES memories(id),

    -- Lifecycle timestamps.
    created_at TEXT NOT NULL DEFAULT (datetime('now')),

    -- Status gate: only enacted proposals move to 'enacted'.
    status TEXT DEFAULT 'pending'
        CHECK(status IN ('pending', 'drew_reviewed', 'enacted'))
);

CREATE INDEX IF NOT EXISTS idx_self_eval_month
    ON self_eval_reports(month);

CREATE INDEX IF NOT EXISTS idx_self_eval_status
    ON self_eval_reports(status);

CREATE INDEX IF NOT EXISTS idx_self_eval_created
    ON self_eval_reports(created_at);

-- Separate table for tracking enacted proposals across months.
-- When Drew approves a proposal, it creates a row here; enact_approved()
-- then executes each one (creates memories, tasks, etc.) and marks it done.
CREATE TABLE IF NOT EXISTS self_eval_enacted_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES self_eval_reports(id),
    proposal_title TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    enacted_at TEXT DEFAULT NULL,
    enacted_memory_id INTEGER DEFAULT NULL REFERENCES memories(id),
    status TEXT DEFAULT 'pending'
        CHECK(status IN ('pending', 'enacted', 'failed', 'deferred')),
    error_log TEXT DEFAULT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_enacted_report
    ON self_eval_enacted_proposals(report_id);

CREATE INDEX IF NOT EXISTS idx_enacted_status
    ON self_eval_enacted_proposals(status);
