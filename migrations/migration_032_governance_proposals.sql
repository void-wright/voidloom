-- migration_032_governance_proposals.sql
--
-- CRUCIBLE-3 / cycle 2. `governance_proposals` was created ONLY by
-- `organs/governance.py:295` at RUNTIME, by a `CREATE TABLE IF NOT EXISTS`
-- inside the organ that uses it. It was in no migration, so:
--
--   * a brain built from the package's own migrations NEVER HAD IT;
--   * `schema_migrator audit` reported `complete: true, missing: 0` on such a
--     brain, because the reference set is built by EXECUTING the migrations and
--     this table is not in any of them -- the audit could not see its absence;
--   * `decision_units` then correctly refused, saying "governance_proposals is
--     ABSENT from this brain, so the governance source was NOT OPENED: its
--     pending count is UNKNOWN, not zero" -- the one honest report in the chain;
--   * and `voidloom approvals --tui` returned `refused` on a fresh brain.
--
-- MEASURED: a brain built with `claude_memory.py init` + every migration applied
-- reported complete=true / missing=0 / 72 objects with this table ABSENT.
--
-- A table the product requires belongs in the set the schema audit measures.
-- The organ's own CREATE stays where it is -- it is idempotent and it is what
-- keeps an already-running brain working -- but the migration is what makes a
-- FRESH brain complete and what puts the table inside the denominator.
--
-- Idempotent and safe on a brain that already has it (the live brain does).
-- Kept BYTE-FOR-BYTE identical to organs/governance.py:295-315 so the two
-- cannot drift into two different shapes of the same table.

CREATE TABLE IF NOT EXISTS governance_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_batch   TEXT NOT NULL,            -- one batch per writer per day (banner +1, not +N)
    writer_kind    TEXT NOT NULL,            -- gap_fill | edge_commit | dream_integrate | ...
    verdict        TEXT NOT NULL DEFAULT 'HOLD',
    reason         TEXT NOT NULL,            -- why it was held (the rule that fired)
    reason_code    TEXT NOT NULL,            -- machine tag of the rule (for distillation)
    intent_json    TEXT NOT NULL,            -- the full write_intent (so a re-attempt is possible)
    intent_hash    TEXT NOT NULL,            -- dedupe within a batch
    trust_score    REAL DEFAULT NULL,        -- the modulating trust (transparency)
    elevated       INTEGER NOT NULL DEFAULT 0,  -- touches a foundation memory -> surfaced first
    target_memory_id INTEGER DEFAULT NULL,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at     TEXT DEFAULT NULL,
    decided_reason TEXT DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_govprop_status ON governance_proposals(status);
CREATE INDEX IF NOT EXISTS idx_govprop_batch  ON governance_proposals(report_batch);
CREATE INDEX IF NOT EXISTS idx_govprop_code   ON governance_proposals(reason_code);
