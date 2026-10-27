-- Migration 016: Pre-Cognitive Recall Log (Wizard Library L4 Piece 2B)
--
-- Tracks anticipatory recall events for self-evolution feedback.
-- Each session-start anticipation is logged with the signals that drove it,
-- the memories that were pre-loaded, and (optionally) feedback on which
-- were actually used.
--
-- Idempotent: safe to re-run. Uses IF NOT EXISTS throughout.

CREATE TABLE IF NOT EXISTS pre_cognitive_recall_log (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id               TEXT,
    signals_json             TEXT NOT NULL,
    memory_ids_json          TEXT NOT NULL,
    tier_ids_json            TEXT NOT NULL,
    ambiguous                INTEGER NOT NULL DEFAULT 0,
    time_ms                  INTEGER NOT NULL DEFAULT 0,
    memories_used_json       TEXT,
    memories_irrelevant_json TEXT,
    feedback_note            TEXT,
    created_at               TEXT NOT NULL DEFAULT (datetime('now')),
    feedback_at              TEXT
);

CREATE INDEX IF NOT EXISTS idx_pcrl_session ON pre_cognitive_recall_log(session_id);
CREATE INDEX IF NOT EXISTS idx_pcrl_created ON pre_cognitive_recall_log(created_at);
CREATE INDEX IF NOT EXISTS idx_pcrl_ambiguous ON pre_cognitive_recall_log(ambiguous);

-- Schema version update.
INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('schema_version', '3.9-L4-pre-cognitive-recall', datetime('now'));
