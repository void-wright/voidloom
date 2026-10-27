-- migration_041_belief_quarantine.sql
--
-- NUMBER: 041 is reserved for lane L5 by the round-1 task spec (040 -> L3). The integrator owns the number and
-- schema.sql; this file is handed over with the lane.
--
-- WHAT. One new table, belief_quarantine: the reversible record of every object the belief engine's quarantine
-- touched (organs/belief_engine.py, quarantine_template_pairs / release_quarantine / quarantine_report). Additive:
-- no existing table, column, CHECK, index, view or trigger is changed, and no row is written by this file.
--
-- WHY. The health check of 2026-10-14 measured 3,789 enacted `contradicts` pairs, 3,527 of them joining two memories
-- written from the SAME organ template ('Distilled from N episodic memories', '[Dream synthesis', 'Auto-researched
-- gap ('), at NLI 0.88-0.999 on unrelated topics. They fed 458 unresolved_contradiction gaps. They are QUARANTINED for
-- the owner's review, never deleted:
--   edge      memory_edges.valid_until NULL -> the quarantine time
--   proposal  edge_proposals.status 'pending' -> 'rejected'
--   gap       gap_inventory.acknowledged 0 -> 1
-- and each row here holds the PRE-IMAGE of exactly the fields changed (prior_state) and what was written
-- (applied_state). A release restores the pre-image only while the object still equals applied_state.
--
-- ROLLBACK SHAPE (pre-registered outcome rule 6). The quarantine is expressed in columns the OLD code already reads:
-- every reader of a current belief filters `valid_until IS NULL` (gap_mapping.detect_unresolved_contradictions,
-- belief_engine._existing_contradicts_edge, beliefs_as_of, the edge counts), so code reverted to f275d09 reads a
-- quarantined pair as retired, not as active. This table is then ignored by the old code (nothing references it) and
-- still holds every pre-image, so the quarantine can be released by hand with plain SQL after a revert. These four
-- statements do what `belief_engine.release_quarantine(conn, release_all=True)` does: each object -- edge, proposal AND gap -- gets its
-- pre-image back ONLY where it still reads exactly as the quarantine left it, and only those rows are marked
-- released (a changed object stays open, as a conflict, nothing overwritten). Plain correlated subqueries: no
-- UPDATE ... FROM, so any sqlite3 with JSON1 runs them. Run them in one transaction (BEGIN; ... COMMIT;):
--     UPDATE memory_edges SET valid_until = NULL
--      WHERE EXISTS (SELECT 1 FROM belief_quarantine q WHERE q.object_kind = 'edge' AND q.released_at IS NULL
--                     AND q.object_id = memory_edges.id
--                     AND memory_edges.valid_until IS json_extract(q.applied_state, '$.valid_until'));
--     UPDATE edge_proposals
--        SET status = (SELECT json_extract(q.prior_state, '$.status') FROM belief_quarantine q
--                       WHERE q.object_kind = 'proposal' AND q.released_at IS NULL AND q.object_id = edge_proposals.id),
--            decided_at = (SELECT json_extract(q.prior_state, '$.decided_at') FROM belief_quarantine q
--                       WHERE q.object_kind = 'proposal' AND q.released_at IS NULL AND q.object_id = edge_proposals.id),
--            error_log = (SELECT json_extract(q.prior_state, '$.error_log') FROM belief_quarantine q
--                       WHERE q.object_kind = 'proposal' AND q.released_at IS NULL AND q.object_id = edge_proposals.id)
--      WHERE EXISTS (SELECT 1 FROM belief_quarantine q WHERE q.object_kind = 'proposal' AND q.released_at IS NULL
--                     AND q.object_id = edge_proposals.id
--                     AND edge_proposals.status IS json_extract(q.applied_state, '$.status')
--                     AND edge_proposals.decided_at IS json_extract(q.applied_state, '$.decided_at')
--                     AND edge_proposals.error_log IS json_extract(q.applied_state, '$.error_log'));
--     UPDATE gap_inventory
--        SET acknowledged = (SELECT json_extract(q.prior_state, '$.acknowledged') FROM belief_quarantine q
--                       WHERE q.object_kind = 'gap' AND q.released_at IS NULL AND q.object_id = gap_inventory.id),
--            acknowledged_at = (SELECT json_extract(q.prior_state, '$.acknowledged_at') FROM belief_quarantine q
--                       WHERE q.object_kind = 'gap' AND q.released_at IS NULL AND q.object_id = gap_inventory.id),
--            acknowledged_reason = (SELECT json_extract(q.prior_state, '$.acknowledged_reason') FROM belief_quarantine q
--                       WHERE q.object_kind = 'gap' AND q.released_at IS NULL AND q.object_id = gap_inventory.id)
--      WHERE EXISTS (SELECT 1 FROM belief_quarantine q WHERE q.object_kind = 'gap' AND q.released_at IS NULL
--                     AND q.object_id = gap_inventory.id
--                     AND gap_inventory.acknowledged = 1
--                     AND gap_inventory.acknowledged_at IS json_extract(q.applied_state, '$.acknowledged_at')
--                     AND gap_inventory.acknowledged_reason IS json_extract(q.applied_state, '$.acknowledged_reason'));
--     UPDATE belief_quarantine SET released_at = datetime('now'), release_note = 'released by plain SQL after a revert'
--      WHERE released_at IS NULL AND (
--            (object_kind = 'edge' AND EXISTS (SELECT 1 FROM memory_edges e WHERE e.id = object_id
--                 AND e.valid_until IS json_extract(prior_state, '$.valid_until')))
--         OR (object_kind = 'proposal' AND EXISTS (SELECT 1 FROM edge_proposals p WHERE p.id = object_id
--                 AND p.status IS json_extract(prior_state, '$.status')
--                 AND p.decided_at IS json_extract(prior_state, '$.decided_at')
--                 AND p.error_log IS json_extract(prior_state, '$.error_log')))
--         OR (object_kind = 'gap' AND EXISTS (SELECT 1 FROM gap_inventory g WHERE g.id = object_id
--                 AND g.acknowledged IS json_extract(prior_state, '$.acknowledged')
--                 AND g.acknowledged_at IS json_extract(prior_state, '$.acknowledged_at')
--                 AND g.acknowledged_reason IS json_extract(prior_state, '$.acknowledged_reason'))));
-- Removing the table itself (DROP TABLE belief_quarantine) is safe only after every open row is released: the
-- table is the only record of which retired edges were quarantined rather than invalidated by a decision.
--
-- ALREADY APPLIED is decided by organs/schema_migrator.py from the brain's schema signature: the table and its two
-- indexes are this file's whole delta, so a second apply is skipped.

CREATE TABLE belief_quarantine (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,

    -- What was touched: a memory_edges row, an edge_proposals row, or a gap_inventory row.
    object_kind    TEXT NOT NULL CHECK (object_kind IN ('edge', 'proposal', 'gap')),
    object_id      INTEGER NOT NULL,

    -- The memory pair the object concerns (for a gap: the pair whose edge authored its label). Review and release
    -- by pair read these; no foreign key, so the record survives whatever happens to the memories.
    source_id      INTEGER,
    target_id      INTEGER,

    -- The false-class family: a template both ends carry (sleep_consolidation | dream_synthesis | gap_research),
    -- or not_authored (an end not written through store/update; 2026-10-26).
    family         TEXT NOT NULL,
    reason         TEXT NOT NULL,

    -- JSON: the fields the quarantine changed, before (prior_state) and after (applied_state).
    prior_state    TEXT NOT NULL,
    applied_state  TEXT NOT NULL,

    quarantined_at TEXT NOT NULL DEFAULT (datetime('now')),   -- UTC, memories.created_at format
    released_at    TEXT DEFAULT NULL,                          -- set by a release; NULL = still quarantined
    release_note   TEXT DEFAULT NULL
);

-- At most one OPEN quarantine per object.
CREATE UNIQUE INDEX idx_belief_quarantine_open
    ON belief_quarantine (object_kind, object_id) WHERE released_at IS NULL;

-- The review reads (by family, by pair).
CREATE INDEX idx_belief_quarantine_pair
    ON belief_quarantine (source_id, target_id);
