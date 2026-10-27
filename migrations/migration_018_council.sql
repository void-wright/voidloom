-- Migration 018: Inner Council of Mages — L4 Piece 3
-- Wizard Library Level 4 Component 3
--
-- Tracks council deliberation sessions: multi-perspective reasoning
-- with bespoke persona lenses (Seer, Skeptic, Architect, Dreamer)
-- synthesised by MILAMBER.
--
-- Idempotent: safe to re-run. All DDL uses IF NOT EXISTS.
-- Schema version: 3.10-L4-inner-council
--
-- Note: migration 016 is pre_cognitive_recall, migration 017 is
-- memory_dreaming (L4-P1). This council migration is 018.
--
-- Design notes:
--   - transcript stored as JSON array of {speaker, content, round}
--   - decision_weight_score is a float [0.0, 1.0] expressing how
--     consequential the decision was — feeds into L4 Piece 5
--     (Counterfactual Filter) for surfacing "what if we'd chosen
--     differently?" analysis on high-weight decisions.
--   - decision_memory_id links the completed council deliberation to
--     the reflection memory that records its outcome.

CREATE TABLE IF NOT EXISTS council_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- How the council was invoked
    trigger_type TEXT NOT NULL
        CHECK(trigger_type IN ('manual', 'auto_detect')),

    -- The question or decision under deliberation
    topic TEXT NOT NULL,

    -- Optional: narrower classification of the topic
    topic_category TEXT DEFAULT NULL
        CHECK(topic_category IS NULL OR topic_category IN (
            'architecture', 'strategy', 'process', 'personnel',
            'trade_off', 'risk', 'scope', 'priority', 'technical',
            'creative', 'meta'
        )),

    -- Deliberation progress
    rounds_completed INTEGER NOT NULL DEFAULT 0,

    -- JSON array: [{speaker: str, content: str, round: int, timestamp: str}]
    transcript TEXT DEFAULT NULL,

    -- MILAMBER's synthesis (extracted from transcript for quick access)
    synthesis TEXT DEFAULT NULL,

    -- Drew's optional closing voice
    drew_input TEXT DEFAULT NULL,

    -- Link to the reflection memory that captures the decision
    decision_memory_id INTEGER DEFAULT NULL,

    -- How consequential this decision was (for counterfactual filter)
    -- 0.0 = trivial, 1.0 = architectural / irreversible
    decision_weight_score REAL DEFAULT NULL
        CHECK(decision_weight_score IS NULL
              OR (decision_weight_score >= 0.0 AND decision_weight_score <= 1.0)),

    -- Which council members participated (JSON array of persona slugs)
    -- Default is all four + MILAMBER; allows future partial councils
    participants TEXT DEFAULT '["orin","vael","dessa","kael-em","milamber"]',

    -- Lifecycle timestamps
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT DEFAULT NULL,

    -- Session status
    status TEXT NOT NULL DEFAULT 'in_progress'
        CHECK(status IN ('in_progress', 'completed', 'abandoned'))
);

-- Indexes for common access patterns
CREATE INDEX IF NOT EXISTS idx_council_status
    ON council_sessions(status);

CREATE INDEX IF NOT EXISTS idx_council_created
    ON council_sessions(created_at);

CREATE INDEX IF NOT EXISTS idx_council_topic_cat
    ON council_sessions(topic_category)
    WHERE topic_category IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_council_weight
    ON council_sessions(decision_weight_score)
    WHERE decision_weight_score IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_council_memory
    ON council_sessions(decision_memory_id)
    WHERE decision_memory_id IS NOT NULL;

-- Schema version bump
INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('schema_version', '3.10-L4-inner-council', datetime('now'));
