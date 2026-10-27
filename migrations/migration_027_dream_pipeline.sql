-- migration_027_dream_pipeline.sql
-- The Unified Dream Pipeline (L5-DR + L5-F + L5-G + L5-H + L5-J-alpha).
--
-- This migration carves the schema hooks the night-brain phase-sequencer needs.
-- It is the cross-cutting spine: actor-stamping columns (J-alpha), the surprise
-- birth-certificate column (G), the activity-profile / phase-log / RAPTOR-cluster
-- tables (DR + H), the seed_bias column on dream_log (DR), and the gap_type
-- extension (H probe_failure).
--
-- DISCIPLINE (non-negotiable):
--   * Idempotent: every statement is IF NOT EXISTS / guarded ALTER, so re-running
--     is a no-op. Safe to apply to a live brain.
--   * NO BACKFILL. Defaulted columns make existing rows correct for free:
--       - source_brain DEFAULT 'milamber'  -> all ~5,900 existing rows read MILAMBER.
--       - source_user  DEFAULT NULL        -> existing rows = process-authored / unknown.
--       - encoding_surprise DEFAULT NULL    -> legacy NULL = honest "unmeasured at birth".
--   * ASCII-only (no em-dashes) so the runner never chokes under an ascii locale.
--   * The schema_version metadata.value is a meaningful STRING on the live brain
--     (e.g. '3.13-L4-self-eval'), NOT an integer -- so we NEVER clobber it. We
--     record this migration under its own additive marker keys.
--   * dream_activity_profile rows are LOCAL-DERIVED STATE (like pre_cognitive_cache):
--     the migration (the table) propagates across brains, but the ROWS never
--     federate -- each brain reads its OWN activity.

BEGIN;

-- ============================================================================
-- J-alpha -- Actor-stamping columns on memories.
--   source_brain: which brain AUTHORED the memory = the RUNNING brain. The
--     DEFAULT 'milamber' makes every existing row correct with no backfill.
--     CHECK constrains to the two known brains; a fresh foreign brain would
--     widen this set in its own migration, never here.
--   source_user:  the human author on a human-author write ('drew'), NULL on a
--     process write (dream / sleep / gap-research). Propagates conservatively.
-- These columns populate the hooks DR (per-brain/per-user dreaming) and I-alpha
-- (trust-authorship) already carved. There is NO query surface and NO CLI here
-- -- they are written by the store path and read by the organs.
-- ============================================================================

-- SQLite cannot add a column conditionally in pure DDL; the Python migration
-- runner (apply via a guarded ALTER helper) checks PRAGMA table_info first.
-- For executescript-based runners we rely on the runner having already verified
-- absence. The companion Python applier (dream_pipeline.apply_migration_027)
-- performs the PRAGMA guard so these ALTERs only run when the column is absent.

ALTER TABLE memories ADD COLUMN source_brain TEXT NOT NULL DEFAULT 'milamber'
    CHECK (source_brain IN ('milamber', 'eddie'));

ALTER TABLE memories ADD COLUMN source_user TEXT DEFAULT NULL;

-- ============================================================================
-- G -- Surprise birth-certificate. ONE immutable column.
--   Written once at INSERT (novelty) and finalized once nightly (contradiction
--   folded in). After finalization it is NEVER updated -- the ABSENCE of an
--   update path is the structural anti-rot guard. Stores EPISTEMIC surprise only
--   (novelty + contradiction), NEVER arousal/affect (that is quarantined to a
--   monthly_self_eval calm-baseline diagnostic, never a salience lever).
--   Legacy rows keep NULL (honest "unmeasured at birth"). NO backfill.
-- The companion column surprise_finalized marks whether the single nightly
-- finalization has folded contradiction in (so the finalizer is a one-shot per
-- row, never a re-write loop).
-- ============================================================================

ALTER TABLE memories ADD COLUMN encoding_surprise REAL DEFAULT NULL;
ALTER TABLE memories ADD COLUMN surprise_finalized INTEGER NOT NULL DEFAULT 0;

-- ============================================================================
-- DR -- The dream activity profile (recall-weighted, exp-decayed running state).
--   One row per (dimension, key) -- e.g. ('source_project', 'engineering-x'),
--   ('tag', 'foo'), ('category', 'bar'). affinity is an exponentially-decayed
--   running weight (~7-day half-life) blended from real session recalls (~0.65)
--   and store-density (~0.35). updated_at lets the reader apply on-read decay.
--   LOCAL-DERIVED: these ROWS never federate. The brain reads its OWN activity.
-- ============================================================================

CREATE TABLE IF NOT EXISTS dream_activity_profile (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dimension   TEXT    NOT NULL CHECK (dimension IN ('source_project', 'tag', 'category')),
    key         TEXT    NOT NULL,
    affinity    REAL    NOT NULL DEFAULT 0.0,
    recall_mass REAL    NOT NULL DEFAULT 0.0,
    store_mass  REAL    NOT NULL DEFAULT 0.0,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (dimension, key)
);

CREATE INDEX IF NOT EXISTS idx_dap_dim_aff
    ON dream_activity_profile (dimension, affinity DESC);

-- ============================================================================
-- The phase log -- the falsifiability surface. One row per phase per batch.
--   Records each phase's name, nocturnal/daytime tag, status (ok/error/skipped/
--   deferred/timeout), duration, and a small JSON result. One phase failing is
--   recorded here as status='error' and NEVER aborts the cycle.
-- ============================================================================

CREATE TABLE IF NOT EXISTS dream_phase_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id     INTEGER,
    phase_name   TEXT    NOT NULL,
    nocturnal    INTEGER NOT NULL DEFAULT 1,
    status       TEXT    NOT NULL DEFAULT 'ok'
                 CHECK (status IN ('ok', 'error', 'skipped', 'deferred', 'timeout', 'disabled')),
    detail       TEXT    DEFAULT NULL,
    result_json  TEXT    DEFAULT NULL,
    duration_s   REAL    DEFAULT NULL,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_dpl_batch  ON dream_phase_log (batch_id);
CREATE INDEX IF NOT EXISTS idx_dpl_phase  ON dream_phase_log (phase_name);
CREATE INDEX IF NOT EXISTS idx_dpl_status ON dream_phase_log (status);

-- ============================================================================
-- RAPTOR clusters (H, daytime/weekly). Keyed on cluster_signature so a
--   re-summarize is an idempotent UPDATE, never a duplicate. synthesis_memory_id
--   points at the kind='semantic' tag='synthesis-node' memory the cluster
--   distilled into. drift_quiz_passed records the last synthesis-drift check.
-- ============================================================================

CREATE TABLE IF NOT EXISTS raptor_clusters (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_signature   TEXT    NOT NULL UNIQUE,
    member_count        INTEGER NOT NULL DEFAULT 0,
    member_ids_json     TEXT    DEFAULT NULL,
    synthesis_memory_id INTEGER DEFAULT NULL,
    cluster_label       TEXT    DEFAULT NULL,
    drift_quiz_passed   INTEGER DEFAULT NULL,
    last_summarized_at  TEXT    DEFAULT NULL,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_raptor_sig ON raptor_clusters (cluster_signature);

-- ============================================================================
-- DR -- dream_log.seed_bias: WHY each seed was chosen (falsifiable in self-eval).
--   A small JSON blob, e.g. {"pool":"divergent","activity_temp":0.7,
--   "surprise_weight":0.42,"anchor_key":"<dim:key>"}.
-- Guarded ALTER (the Python applier checks PRAGMA first).
-- ============================================================================

ALTER TABLE dream_log ADD COLUMN seed_bias TEXT DEFAULT NULL;

-- ============================================================================
-- Idempotent additive marker keys (NEVER clobber schema_version).
-- ============================================================================

CREATE TABLE IF NOT EXISTS metadata (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

INSERT OR REPLACE INTO metadata (key, value, updated_at)
    VALUES ('migration_027_dream_pipeline', 'applied', datetime('now'));
INSERT OR REPLACE INTO metadata (key, value, updated_at)
    VALUES ('dream_pipeline_schema', '1.0-unified-dream', datetime('now'));

COMMIT;

-- ============================================================================
-- gap_type extension (H probe_failure) -- DEFERRED TO THE PYTHON APPLIER.
--   The gap_inventory.gap_type column carries a closed CHECK enum. SQLite cannot
--   relax a CHECK with ALTER. The Python applier (extend_gap_type_for_probe)
--   performs the safe table-rebuild ONLY if 'probe_failure' is not already
--   permitted: rename -> recreate with the widened CHECK -> copy rows -> drop.
--   It is fully idempotent (probes the CHECK by reading sqlite_master) and runs
--   OUTSIDE this transaction so a partial rebuild never corrupts the live enum.
--
--   FK-SAFETY (iter-2 P1 fix): the live `gap_research_log.gap_id` carries a
--   `REFERENCES gap_inventory(id)` foreign key. Under the default (>=3.25) ALTER
--   behaviour, `RENAME gap_inventory -> _gap_inventory_old` REWRITES that sibling
--   FK to `_gap_inventory_old`, and the subsequent DROP leaves it permanently
--   dangling (next write throws `no such table: main._gap_inventory_old`). The
--   applier therefore wraps the rebuild in `PRAGMA legacy_alter_table=ON` (which
--   suppresses the FK-rewrite, keeping gap_research_log pointed at gap_inventory)
--   and asserts post-rebuild that the sibling FK is intact + foreign_key_check is
--   clean before returning ok. See dream_pipeline.extend_gap_type_for_probe.
-- ============================================================================
