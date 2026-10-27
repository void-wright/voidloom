-- Migration 009: Whisper Network (L3 Component 8)
-- Schema version: 3.3-L3-anti-recency -> 3.4-L3-whisper-network
--
-- Idempotent: safe to re-run. All statements use IF NOT EXISTS.
--
-- Dependencies: memories table (base schema), memory_tags table (base schema).
--
-- Purpose: Privileged self-observation channel for Claude to log quiet
-- observations about its own behavior during sessions — uncertainty,
-- friction, surprise, blocked feelings, emerging patterns. Observations
-- accumulate over the week; a Friday 8 AM local time cron digest synthesizes
-- them into a single reflection memory (kind='reflection', tagged
-- 'whisper-digest') that surfaces to Drew.
--
-- Phase 0 design: task_specification L3-C8 whisper-network.

PRAGMA foreign_keys=ON;

-- Lightweight observation captures. NO embedding at capture time —
-- only the weekly digest gets embedded as a proper memory.
CREATE TABLE IF NOT EXISTS whisper_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation TEXT NOT NULL,
    observation_type TEXT NOT NULL CHECK(observation_type IN (
        'uncertainty', 'friction', 'surprise', 'pattern', 'assumption',
        'blocked', 'insight', 'meta'
    )),
    session_context TEXT DEFAULT NULL,
    intensity REAL DEFAULT 0.5 CHECK(intensity >= 0.0 AND intensity <= 1.0),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    digested INTEGER DEFAULT 0,
    digest_id INTEGER DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_whisper_type ON whisper_observations(observation_type);
CREATE INDEX IF NOT EXISTS idx_whisper_digested ON whisper_observations(digested);
CREATE INDEX IF NOT EXISTS idx_whisper_created ON whisper_observations(created_at);
CREATE INDEX IF NOT EXISTS idx_whisper_intensity ON whisper_observations(intensity);

-- Digest log — each row corresponds to one weekly synthesis run.
CREATE TABLE IF NOT EXISTS whisper_digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_memory_id INTEGER REFERENCES memories(id),
    observations_count INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_digest_memory ON whisper_digests(digest_memory_id);
CREATE INDEX IF NOT EXISTS idx_digest_period ON whisper_digests(period_start, period_end);

-- Schema version bump.
INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('schema_version', '3.4-L3-whisper-network', datetime('now'));

-- Record migration application timestamp.
INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('migration_009_whisper_network_applied_at', datetime('now'), datetime('now'));
