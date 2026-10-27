-- Migration 014: Calibration Memory — L4 Piece 4
-- Wizard Library Level 4 Component 4
--
-- Tracks numeric predictions Claude makes alongside actual outcomes.
-- Over time, the library learns chronic miscalibrations per domain and
-- prediction_kind, and auto-corrects future estimates.
--
-- Idempotent: safe to re-run. All DDL uses IF NOT EXISTS / IF NOT EXISTS.
-- Schema version: 3.8-L4-calibration-memory
--
-- Founding data: memory #3972 (L2 build time prediction, ~15x overestimate).

-- The core calibration log table.
CREATE TABLE IF NOT EXISTS calibration_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- What was predicted
    prediction_value REAL NOT NULL,
    prediction_unit TEXT NOT NULL
        CHECK(prediction_unit IN (
            'hours', 'minutes', 'seconds', 'days', 'weeks',
            'dollars', 'percent', 'count', 'probability',
            'lines_of_code', 'tokens', 'bytes'
        )),
    prediction_kind TEXT NOT NULL
        CHECK(prediction_kind IN (
            'time', 'cost', 'yield', 'recovery', 'character_count',
            'success_probability', 'iteration_count', 'size',
            'latency', 'throughput', 'score'
        )),

    -- Domain scoping (hierarchical: e.g. 'ace-loop-construction',
    -- 'content-generation/blog', 'pipeline-processing/fts5')
    domain TEXT NOT NULL,

    -- Free-text context: what was being predicted
    context TEXT DEFAULT NULL,

    -- Timestamps
    predicted_at TEXT NOT NULL DEFAULT (datetime('now')),
    actual_value REAL DEFAULT NULL,
    actual_at TEXT DEFAULT NULL,

    -- Computed on outcome fill: actual/predicted
    -- < 1.0 means overestimate; > 1.0 means underestimate
    ratio REAL DEFAULT NULL,

    -- Links
    source_memory_id INTEGER DEFAULT NULL,
    session_id TEXT DEFAULT NULL,

    -- Metadata
    notes TEXT DEFAULT NULL,
    tags TEXT DEFAULT NULL,

    -- Audit
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Performance indices
CREATE INDEX IF NOT EXISTS idx_cal_domain
    ON calibration_log(domain);
CREATE INDEX IF NOT EXISTS idx_cal_kind
    ON calibration_log(prediction_kind);
CREATE INDEX IF NOT EXISTS idx_cal_ratio
    ON calibration_log(ratio)
    WHERE ratio IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cal_predicted_at
    ON calibration_log(predicted_at);
CREATE INDEX IF NOT EXISTS idx_cal_domain_kind
    ON calibration_log(domain, prediction_kind)
    WHERE ratio IS NOT NULL;

-- Aggregate cache for fast correction factor lookups.
-- Recomputed periodically (e.g. during sleep loop or on-demand).
CREATE TABLE IF NOT EXISTS calibration_factors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    prediction_kind TEXT NOT NULL,
    correction_factor REAL NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    sample_size INTEGER NOT NULL DEFAULT 0,
    weighted_median REAL DEFAULT NULL,
    p25 REAL DEFAULT NULL,
    p75 REAL DEFAULT NULL,
    last_computed_at TEXT NOT NULL DEFAULT (datetime('now')),
    lookback_days INTEGER NOT NULL DEFAULT 180,
    UNIQUE(domain, prediction_kind)
);

CREATE INDEX IF NOT EXISTS idx_calfac_domain_kind
    ON calibration_factors(domain, prediction_kind);

-- Schema version bump
INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('schema_version', '3.8-L4-calibration-memory', datetime('now'));
