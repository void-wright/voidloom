-- Migration 011: Cognitive Map Dashboard (L3 Component 3)
-- Adds map_generation_log table for tracking dashboard generation events.
-- Schema version bump: 3.4-L3-whisper-network -> 3.5-L3-cognitive-map
--
-- Idempotent: safe to re-run (CREATE TABLE IF NOT EXISTS).

-- Track each map generation event for audit + staleness tracking.
CREATE TABLE IF NOT EXISTS map_generation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL DEFAULT (datetime('now')),
    output_path TEXT NOT NULL,
    total_memories INTEGER NOT NULL DEFAULT 0,
    total_edges INTEGER NOT NULL DEFAULT 0,
    sections_rendered INTEGER NOT NULL DEFAULT 0,
    generation_time_ms INTEGER NOT NULL DEFAULT 0,
    trigger TEXT NOT NULL DEFAULT 'manual'
        CHECK(trigger IN ('manual', 'cron', 'cmd_map')),
    config_snapshot TEXT DEFAULT NULL,  -- JSON of generation parameters
    error_log TEXT DEFAULT NULL
);

-- Bump schema version.
INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('schema_version', '3.5-L3-cognitive-map', datetime('now'));
