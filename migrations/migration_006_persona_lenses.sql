-- Migration 006: Persona-Specific Recall Lenses (L3-C5)
-- Idempotent: safe to re-run. All CREATE IF NOT EXISTS.
-- Schema version bump: 2.4-L2-sleep-loop -> 3.1-L3-persona-lenses

-- Each automation persona gets a recall lens that reranks results
-- based on tag/category/kind/project weight multipliers.
-- The underlying data is SHARED — lenses are POST-RETRIEVAL reranking,
-- never splitting or partitioning data.
CREATE TABLE IF NOT EXISTS persona_recall_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_slug TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    description TEXT DEFAULT NULL,
    tag_weights TEXT NOT NULL DEFAULT '{}',      -- JSON: {"tag": weight_multiplier}
    category_weights TEXT NOT NULL DEFAULT '{}', -- JSON: {"category": weight_multiplier}
    kind_weights TEXT NOT NULL DEFAULT '{}',     -- JSON: {"kind": weight_multiplier}
    project_weights TEXT NOT NULL DEFAULT '{}',  -- JSON: {"project": weight_multiplier}
    exclude_tags TEXT NOT NULL DEFAULT '[]',     -- JSON array of tags to always exclude
    boost_recent_days INTEGER DEFAULT NULL,      -- if set, boost memories from last N days
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_persona_slug ON persona_recall_profiles(persona_slug);
CREATE INDEX IF NOT EXISTS idx_persona_active ON persona_recall_profiles(is_active);

-- Bump schema version.
INSERT OR REPLACE INTO metadata (key, value) VALUES ('schema_version', '3.1-L3-persona-lenses');
