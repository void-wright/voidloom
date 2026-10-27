-- migration_030_decision_units.sql
-- Voidloom APPROVALS LIFECYCLE — G1 (the decision ledger) + G2a (the run ledger)
-- Ruler: VL-LIFECYCLE-v2.0
--
-- STRICTLY ADDITIVE. This file contains NO ALTER, NO DROP, NO UPDATE, NO DELETE.
--
-- R9 (CONSTRAINT, load-bearing): `self_eval_reports.status` is NOT dropped and its
-- CHECK is NOT widened. SQLite cannot ALTER a CHECK; widening it is a 12-step table
-- rebuild over `self_eval_enacted_proposals.report_id`, which is
-- `NOT NULL REFERENCES self_eval_reports(id)` across 9 live rows and carries its own
-- overlapping status vocabulary ('pending','enacted','failed','deferred'). Rollback
-- would stop being a DROP TABLE and become a forensic reconstruction OF THE VERY
-- RECORD that proves Drew's decisions were eaten. So: derived state is computed
-- ALONGSIDE the stored column (see v_report_derived_state) and the two are asserted
-- to agree by decision_units.assert_status_agreement().
--
-- ROLLBACK (complete, in this order):
--     DROP VIEW  IF EXISTS v_report_derived_state;
--     DROP VIEW  IF EXISTS v_decision_unit_state;
--     DROP TABLE IF EXISTS decision_events;
--     DROP TABLE IF EXISTS decision_status_divergence;
--     DROP TABLE IF EXISTS decision_backfill_exceptions;
--     DROP TABLE IF EXISTS unit_attention;
--     DROP TABLE IF EXISTS decision_units;
--     DROP TABLE IF EXISTS organ_run_log;
--     DROP TABLE IF EXISTS organ_declared_units;
--     DELETE FROM metadata WHERE key='migration_030_decision_units';
-- Nothing outside these objects is touched, so rollback costs exactly that.

-- ===========================================================================
-- G1 — THE DECISION LEDGER
-- ===========================================================================

-- R6 (THE KEY): a unit is identified by (surface, report_id, proposal_title).
-- `(metric, work)` — here `suppression_key` — is a SUPPRESSION KEY ONLY and is
-- DELIBERATELY NOT UNIQUE and NOT part of any identity. Rationale, measured:
-- "Activate L3-C7-cross-tier-bridges" was approved on report #4 (2026-07-01,
-- succeeded) and again on report #5 (2026-08-02, discarded). A (metric, work)
-- identity MERGES those two and the success absorbs the failure — and that pair
-- is the only durable proof the tool ate Drew's decisions. The model's dedup
-- would BE the loss.
CREATE TABLE IF NOT EXISTS decision_units (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_key        TEXT    NOT NULL UNIQUE,   -- 'self-eval:<report>:<title-digest>'
    surface         TEXT    NOT NULL DEFAULT 'self-eval',
    report_id       INTEGER NOT NULL REFERENCES self_eval_reports(id),
    proposal_title  TEXT    NOT NULL,
    proposal_kind   TEXT    NOT NULL
                    CHECK (proposal_kind IN ('enhancement', 'deprecation')),
    proposal_json   TEXT    NOT NULL,
    piece_affected  TEXT    DEFAULT NULL,
    -- SUPPRESSION ONLY. Never an identity. Never unique. See the note above.
    suppression_key TEXT    DEFAULT NULL,
    first_seen_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (surface, report_id, proposal_title)
);
CREATE INDEX IF NOT EXISTS idx_dunit_report      ON decision_units(report_id);
CREATE INDEX IF NOT EXISTS idx_dunit_suppression ON decision_units(suppression_key);
CREATE INDEX IF NOT EXISTS idx_dunit_surface     ON decision_units(surface);

-- APPEND-ONLY. A decision is a historical fact: nothing the world does later
-- makes it false, so no row here is ever UPDATEd or DELETEd.
--
-- The four outcomes, each with the row that would look different if it were false
-- (VAEL's executable form of the governing principle) and the code path that
-- writes that row -- confirmed in every case NOT to be the path that decides:
--   approved  <- self_eval_reports.drew_approved.approved[] / an audit approve row
--                with outcome_status='ok'   [writer: drew_review / the CLI dispatch]
--   declined  <- drew_approved.rejected[]   / an audit reject row, outcome ok
--                                            [writer: drew_review / CLI dispatch]
--   enacted   <- self_eval_enacted_proposals.status='enacted' AND a non-null
--                enacted_memory_id pointing at a real memories row
--                                            [writer: enact_approved]
--   discarded <- an approval_audit_log row with outcome_status='error' whose
--                outcome_json names "already reviewed" -- i.e. Drew decided and
--                the system dropped it       [writer: write_audit, from the CLI]
-- No closure state (verified/refuted/stale/wont-do) appears here: those are G3
-- and are deliberately OUT of this round's scope.
CREATE TABLE IF NOT EXISTS decision_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id         INTEGER NOT NULL REFERENCES decision_units(id),
    outcome         TEXT    NOT NULL
                    CHECK (outcome IN ('approved', 'declined', 'enacted', 'discarded')),
    decided_at      TEXT    NOT NULL,
    -- (applied history: every live writer passes the actor since 2026-10-26, derived from
    -- VOIDLOOM_ACTOR / the OS user, or 'policy:auto'; this default is no longer reached)
    actor           TEXT    NOT NULL DEFAULT 'drew',
    -- WHICH record proves this event. The migration NEVER merges two records into
    -- one event: report #5's four commands stay four events precisely because each
    -- carries its own approval_audit_log rowid.
    evidence_source TEXT    NOT NULL
                    CHECK (evidence_source IN ('approval_audit_log',
                                               'self_eval_enacted_proposals',
                                               'drew_approved', 'live')),
    evidence_rowid  INTEGER DEFAULT NULL,
    detail_json     TEXT    DEFAULT NULL,
    recorded_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    -- One event per (unit, source, source-row, outcome): re-running the backfill
    -- is idempotent and can never duplicate a decision.
    UNIQUE (unit_id, evidence_source, evidence_rowid, outcome)
);
CREATE INDEX IF NOT EXISTS idx_devent_unit    ON decision_events(unit_id);
CREATE INDEX IF NOT EXISTS idx_devent_outcome ON decision_events(outcome);
CREATE INDEX IF NOT EXISTS idx_devent_when    ON decision_events(decided_at);

-- R7: a record that CANNOT be attributed is recorded as unattributable, with its
-- reason. It is never guessed. approval_audit_log row 9 is
-- {"titles": null, "ids": null} against the empty report #6 -- there is no title
-- to attach it to, and inventing one would be the defect this gate exists to end.
CREATE TABLE IF NOT EXISTS decision_backfill_exceptions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_table  TEXT    NOT NULL,
    source_rowid  INTEGER NOT NULL,
    ref           TEXT    DEFAULT NULL,
    reason_code   TEXT    NOT NULL,
    reason        TEXT    NOT NULL,
    detail_json   TEXT    DEFAULT NULL,
    recorded_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source_table, source_rowid, reason_code)
);

-- R9's other half. Where the derived state and the stored `status` disagree on a
-- PRE-EXISTING row, the divergence is registered here WITH ITS EVIDENCE rather
-- than dissolved by weakening the derivation. Registering it is what lets the
-- agreement assertion pass on all 7 reports without erasing the finding; any
-- UNREGISTERED disagreement -- i.e. any new one -- fails the assertion loudly.
--
-- KEYED ON THE EVIDENCE, NOT ON THE NAME (corrected 2026-09-02, iteration 2).
-- The first version keyed the amnesty on `report_id` alone. Reproduced by
-- execution: register report #5, then add ONE open unit to it (open_units 3->4
-- while this table still said 3) and assert_status_agreement() stayed clean.
-- A registry keyed on an IDENTIFIER grants amnesty to a NAME and the name never
-- changes, so the amnesty is permanent. A registry keyed on the EVIDENCE grants
-- amnesty to a FACT, and facts expire on their own: `divergence_fingerprint` is
-- a digest over every number that produced this divergence, so ANY change to the
-- underlying facts yields a different fingerprint, matches nothing here, and the
-- assertion fires. Nothing has to remember to expire it.
CREATE TABLE IF NOT EXISTS decision_status_divergence (
    report_id       INTEGER PRIMARY KEY REFERENCES self_eval_reports(id),
    stored_status   TEXT    NOT NULL,
    derived_status  TEXT    NOT NULL,
    unit_count      INTEGER NOT NULL,
    open_units      INTEGER NOT NULL,
    enacted_units   INTEGER NOT NULL,
    discarded_units INTEGER NOT NULL,
    open_titles     TEXT    NOT NULL,   -- JSON array: the decisions still outstanding
    -- sha256 over the canonical JSON of every field above. THE AMNESTY KEY.
    divergence_fingerprint TEXT NOT NULL,
    evidence        TEXT    NOT NULL,   -- prose + the rowids that prove it
    registered_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_divergence_fp
    ON decision_status_divergence(divergence_fingerprint);

-- R14: the ATTENTION record. Separate table, separate subject, separate verbs.
-- `dismiss` / `snooze` / `defer` / `ack` write HERE and write NOTHING to
-- decision_units or decision_events -- the lazy path leaves a unit's decision
-- state byte-identical.
--
-- R13: quiet is PER UNIT, never global. A snooze grants silence over the units
-- that existed when it was granted; a unit that arrives afterwards has no row
-- here, therefore no quiet, therefore it still fires. The live global snooze runs
-- to 2026-10-01; had the banner obeyed it, the 2026-09-01 envelope-bug failure
-- would have been hidden for a month.
-- TWO INSTANTS, BECAUSE THEY ARE TWO DIFFERENT FACTS.
--   first_seen_at   -- when the DECISION arrived (the report's creation instant
--                      for a backfilled unit). Honest about age; it is what makes
--                      "outstanding since 2026-08-01" sayable.
--   row_created_at  -- when the unit first became PRESENTABLE, i.e. when it
--                      entered the attention set at all.
-- A SNOOZE SILENCES WHAT IT WAS GRANTED OVER, and it can only have been granted
-- over what was presentable. Measured: the live global snooze was set
-- 2026-09-01 14:27:35, when NO report was stored 'pending' (report #7 was
-- created at 22:21, eight hours later) and no ledger existed -- so report #5's
-- three August-eaten decisions and report #3's never-decided deprecation were
-- not merely un-snoozed, they were INVISIBLE. Bounding the legacy snooze by
-- first_seen_at alone made those four QUIET until 2026-10-01: the four
-- decisions this entire round exists to recover, counted and unable to fire.
CREATE TABLE IF NOT EXISTS unit_attention (
    unit_key         TEXT PRIMARY KEY,
    surface          TEXT    NOT NULL,
    first_seen_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    row_created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    acknowledged_at  TEXT    DEFAULT NULL,
    dismiss_count    INTEGER NOT NULL DEFAULT 0,
    last_dismissed_at TEXT   DEFAULT NULL,
    quiet_until      TEXT    DEFAULT NULL,
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_uattn_quiet   ON unit_attention(quiet_until);
CREATE INDEX IF NOT EXISTS idx_uattn_surface ON unit_attention(surface);

-- ===========================================================================
-- G2a — THE RUN LEDGER
-- ===========================================================================

-- R1 (THE KEY): the ledger is keyed on the DECLARED unit set, joined against
-- observed runs -- never on observed runs alone. Measured:
-- wizard-library-arcane-manifest.timer is `disabled`/`inactive`, journalctl
-- returns "-- No entries --", and `systemctl --user list-timers` shows only 18 of
-- the 19 declared units. A ledger keyed on runs observed cannot see it, because
-- absence of rows is indistinguishable from "hasn't reached cadence yet."
CREATE TABLE IF NOT EXISTS organ_declared_units (
    unit_name        TEXT PRIMARY KEY,          -- 'wizard-library-arcane-manifest.timer'
    unit_file        TEXT    NOT NULL,
    service_name     TEXT    NOT NULL,
    -- R3: the unit's OWN OnCalendar lines, verbatim, ALL of them. Never a single
    -- global interval and never a cadence guessed from the Description.
    -- counterfactual-revisit emulates quarterly with FOUR OnCalendar lines.
    oncalendar_json  TEXT    NOT NULL,
    cadence_class    TEXT    NOT NULL,          -- derived by MEASURING occurrences
    cadence_seconds  INTEGER DEFAULT NULL,      -- median measured inter-occurrence gap
    persistent       INTEGER NOT NULL DEFAULT 0,
    unit_file_state  TEXT    DEFAULT NULL,      -- enabled | disabled | ...
    active_state     TEXT    DEFAULT NULL,      -- active  | inactive
    first_declared_at TEXT   NOT NULL DEFAULT (datetime('now')),
    last_observed_at  TEXT   NOT NULL DEFAULT (datetime('now'))
);

-- R2: this table is written by an OBSERVER. Nothing in the ledger path notifies,
-- writes an approval unit, or touches the banner -- DESSA: the stop condition
-- belongs on the notifier, not the observer; a ledger that can alarm can be
-- switched off, and then nothing observes.
CREATE TABLE IF NOT EXISTS organ_run_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_name         TEXT    NOT NULL,
    service_name      TEXT    NOT NULL,
    run_started_at    TEXT    NOT NULL,         -- ISO, from the systemd trigger stamp
    run_ended_at      TEXT    DEFAULT NULL,
    result            TEXT    DEFAULT NULL,     -- success | exit-code | timeout | ...
    -- R3/VAEL-R7: `Persistent=true` on all 18 loaded timers converts a MISSED
    -- WINDOW into a LATE RUN. Measured: a nightly backup unit (23:45) and
    -- anti-recency (an 08:00 unit) both last ran 08:41:06 -- a boot catch-up. An
    -- interval watchdog sees a run inside the window and reports health. So each
    -- row records the SCHEDULED OCCURRENCE it satisfies and its lateness.
    scheduled_for     TEXT    DEFAULT NULL,
    lateness_seconds  INTEGER DEFAULT NULL,
    observation_source TEXT   NOT NULL,         -- 'systemctl-show' | 'journal'
    observed_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (unit_name, run_started_at)
);
CREATE INDEX IF NOT EXISTS idx_orunlog_unit  ON organ_run_log(unit_name);
CREATE INDEX IF NOT EXISTS idx_orunlog_start ON organ_run_log(run_started_at);

-- ===========================================================================
-- DERIVED VIEWS — R9's "alongside", made literal
-- ===========================================================================

CREATE VIEW IF NOT EXISTS v_decision_unit_state AS
SELECT
    u.id              AS unit_id,
    u.unit_key        AS unit_key,
    u.report_id       AS report_id,
    u.proposal_title  AS proposal_title,
    u.proposal_kind   AS proposal_kind,
    u.suppression_key AS suppression_key,
    (SELECT COUNT(*) FROM decision_events e WHERE e.unit_id = u.id)
                      AS event_count,
    (SELECT COUNT(*) FROM decision_events e WHERE e.unit_id = u.id AND e.outcome = 'approved')
                      AS approved_count,
    (SELECT COUNT(*) FROM decision_events e WHERE e.unit_id = u.id AND e.outcome = 'declined')
                      AS declined_count,
    (SELECT COUNT(*) FROM decision_events e WHERE e.unit_id = u.id AND e.outcome = 'enacted')
                      AS enacted_count,
    -- A DISCARDED event is Drew deciding and the system dropping it. It is
    -- recorded, and it deliberately does NOT close the unit: the decision still
    -- has to be made, and the pair (decided, still open) is the proof.
    (SELECT COUNT(*) FROM decision_events e WHERE e.unit_id = u.id AND e.outcome = 'discarded')
                      AS discarded_count,
    CASE WHEN EXISTS (SELECT 1 FROM decision_events e
                      WHERE e.unit_id = u.id
                        AND e.outcome IN ('approved', 'declined', 'enacted'))
         THEN 0 ELSE 1 END AS is_open
FROM decision_units u;

-- The stored column sits in column 2 of this view, beside the derived counts, so
-- the pair R9 requires is visible in one SELECT.
CREATE VIEW IF NOT EXISTS v_report_derived_state AS
SELECT
    r.id      AS report_id,
    r.status  AS stored_status,
    (SELECT COUNT(*) FROM decision_units u WHERE u.report_id = r.id)
              AS unit_count,
    (SELECT COUNT(*) FROM v_decision_unit_state v
      WHERE v.report_id = r.id AND v.is_open = 1)
              AS open_units,
    (SELECT COUNT(*) FROM v_decision_unit_state v
      WHERE v.report_id = r.id AND v.enacted_count > 0)
              AS enacted_units,
    (SELECT COUNT(*) FROM v_decision_unit_state v
      WHERE v.report_id = r.id AND v.discarded_count > 0)
              AS discarded_units,
    (SELECT COUNT(*) FROM v_decision_unit_state v
      WHERE v.report_id = r.id AND v.is_open = 0)
              AS decided_units
FROM self_eval_reports r;

INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('migration_030_decision_units', '1', datetime('now'));
