-- migration_046_marks.sql
--
-- WHAT: a memory that came from outside the owner's own words says so in the row, and everything derived from it
-- says so too. `memories.untrusted_source` (public name: `outside`) has held the first half since migration_029.
-- This file adds:
--   * `research_mark` and the run and configuration a research row names, with the registrar tables they must name;
--   * who wrote a row and from what (`author`, `session_id`, `parent_ref`, `source_ids`);
--   * the parents of a derived row (`memory_parents`), kept apart from `memory_provenance` because an edge there
--     changes how its source is ranked, and recording a parent must not;
--   * the triggers that carry a mark from a parent to what was derived from it, whoever writes the edge;
--   * `access_log.event_id` and `machine`, so a recall can be counted without its words being kept.
--
-- WHY TRIGGERS: an older program, a script and a hand edit write the same file. A rule held in one writer binds that
-- writer. Three rules about how they are written:
--   * a trigger that re-fires itself stops after one hop (recursive_triggers is off by default), so the descendants
--     of a marked row are found by a recursive query inside one statement;
--   * json_each raises on text that is not JSON, so every use is guarded by json_valid inside a CASE;
--   * an edge is never refused. A refused edge would leave a derived row with no record of where it came from, so
--     an edge to a marked parent, or to a parent that does not exist, marks the child instead.
-- `memory_provenance` carries two kinds of row: "this was written from that" and "these two were compared"
-- (`edge_inference`). Only the first kind is inheritance, and the list of types below says which those are.
--
-- `memories_fts_after_update` is re-created to fire on a change of `summary` or `content` only. A trigger that marks a
-- row during its own insert updates that row before the full-text index holds it, and the old form answered with an
-- index 'delete' of a row the index had never seen. No indexed text changes: the index holds those two columns.
--
-- NO DATA WORK HERE. Marking the existing children of marked rows is `voidloom marks backfill`, which a person runs.
--
-- ALREADY APPLIED is decided from the WORLD (schema_migrator.migration_deltas): the columns, tables and triggers are
-- this file's delta, so a brain that lost one of them runs the file again. Every statement is safe to run again.
ALTER TABLE memories ADD COLUMN research_mark INTEGER NOT NULL DEFAULT 0 CHECK (research_mark IN (0, 1));
ALTER TABLE memories ADD COLUMN research_run_id TEXT;
ALTER TABLE memories ADD COLUMN research_config_id TEXT;
ALTER TABLE memories ADD COLUMN author TEXT CHECK (author IS NULL OR author IN ('user', 'model', 'tool'));
ALTER TABLE memories ADD COLUMN session_id TEXT;
ALTER TABLE memories ADD COLUMN parent_ref TEXT;
ALTER TABLE memories ADD COLUMN source_ids TEXT;
-- NULL until a rate has been measured; nothing in this package writes it
ALTER TABLE memories ADD COLUMN research_weight REAL;
ALTER TABLE access_log ADD COLUMN event_id TEXT;
ALTER TABLE access_log ADD COLUMN machine INTEGER;
ALTER TABLE gap_research_log ADD COLUMN approval_hash TEXT;
-- what became of a dispatch: 'answered', 'sent_no_answer', 'not_sent', 'held', 'refused', 'error', 'timeout'
ALTER TABLE gap_research_log ADD COLUMN outcome TEXT;

CREATE TABLE IF NOT EXISTS research_configs (
    config_id  TEXT PRIMARY KEY,                 -- sha256 of spec_json
    spec_json  TEXT NOT NULL,                    -- canonical JSON
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS research_runs (
    run_id     TEXT PRIMARY KEY,
    config_id  TEXT NOT NULL REFERENCES research_configs(config_id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS memory_research_runs (
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    run_id    TEXT NOT NULL,
    config_id TEXT NOT NULL,
    inherited INTEGER NOT NULL DEFAULT 0,        -- 0: the row was written by that run; 1: from a parent that was
    PRIMARY KEY (memory_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_research_runs_run ON memory_research_runs(run_id);
-- no foreign key on parent_id: a parent that does not exist has to be recordable, because it marks its child
CREATE TABLE IF NOT EXISTS memory_parents (
    child_id  INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    parent_id INTEGER NOT NULL,
    PRIMARY KEY (child_id, parent_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_parents_parent ON memory_parents(parent_id);
CREATE TABLE IF NOT EXISTS research_dispatch_refusals (
    event_id TEXT PRIMARY KEY,
    gap_type TEXT,
    reason   TEXT NOT NULL,
    at       TEXT NOT NULL DEFAULT (datetime('now'))
);
-- what `forget` archived, so that `unarchive` restores that and nothing else
CREATE TABLE IF NOT EXISTS forget_receipts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id      TEXT NOT NULL,
    memory_id       INTEGER NOT NULL,
    run_id          TEXT,
    before_archived INTEGER NOT NULL,
    after_updated_at TEXT,                       -- the row's updated_at as `forget` left it
    superseded_at   TEXT,                        -- somebody archived or restored the row since: it is theirs now
    at              TEXT NOT NULL DEFAULT (datetime('now')),
    undone_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_forget_receipts_memory ON forget_receipts(memory_id);
-- the rows `marks backfill` changed, as they were
CREATE TABLE IF NOT EXISTS marks_backfill_preimage (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id             TEXT NOT NULL,
    memory_id            INTEGER NOT NULL,
    before_outside       INTEGER NOT NULL,
    before_research_mark INTEGER NOT NULL,
    before_run_id        TEXT,
    before_config_id     TEXT,
    because_outside      TEXT,                   -- JSON: the parents that were outside, and research, when it ran
    because_research     TEXT,
    links_added          TEXT,                   -- JSON: the run links it wrote for this row
    at                   TEXT NOT NULL DEFAULT (datetime('now')),
    undone_at            TEXT
);

DROP TRIGGER IF EXISTS memories_fts_after_update;
CREATE TRIGGER memories_fts_after_update AFTER UPDATE OF id, summary, content ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, summary, content)
    VALUES('delete', old.id, old.summary, old.content);
    INSERT INTO memories_fts(rowid, summary, content)
    VALUES (new.id, new.summary, new.content);
END;

-- the mark is 0 or 1 -----------------------------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_marks_outside_01_insert
BEFORE INSERT ON memories
FOR EACH ROW WHEN NEW.untrusted_source IS NOT 0 AND NEW.untrusted_source IS NOT 1
BEGIN
    SELECT RAISE(ABORT, 'untrusted_source must be 0 or 1');
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_outside_01_update
BEFORE UPDATE OF untrusted_source ON memories
FOR EACH ROW WHEN (NEW.untrusted_source IS NOT 0 AND NEW.untrusted_source IS NOT 1)
               OR (NEW.untrusted_source IS 0 AND OLD.untrusted_source IS NOT 0 AND OLD.untrusted_source IS NOT 1)
BEGIN
    SELECT RAISE(ABORT, 'untrusted_source must be 0 or 1, and a value that is neither is not cleared to 0');
END;

-- INSERT OR REPLACE over a marked row would delete it and leave a clean one under its id: no update happens, so no
-- update trigger can see it. A trigger cannot tell REPLACE from OR IGNORE or an upsert, so every INSERT that names the id
-- of a marked row without its mark is refused; such a row is changed with UPDATE. A row stored without an id has
-- NEW.id = -1 here, which names nothing.
CREATE TRIGGER IF NOT EXISTS trg_marks_replace_refused
BEFORE INSERT ON memories
FOR EACH ROW WHEN EXISTS (SELECT 1 FROM memories m WHERE m.id = NEW.id
                             AND ((m.untrusted_source IS NOT 0 AND NEW.untrusted_source IS 0)
                               OR (m.research_mark = 1 AND NEW.research_mark = 0)))
BEGIN
    SELECT RAISE(ABORT, 'MARKED_ROW_REPLACED: an INSERT that names the id of a marked memory without its mark is refused, whatever its conflict clause (REPLACE, OR IGNORE, ON CONFLICT); change that row with UPDATE');
END;

-- research: marked, named, registered, written once ----------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_marks_research_immutable
BEFORE UPDATE OF research_mark ON memories
FOR EACH ROW WHEN OLD.research_mark = 1 AND NEW.research_mark IS NOT 1
BEGIN
    SELECT RAISE(ABORT, 'research_mark is immutable once set');
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_run_write_once
BEFORE UPDATE OF research_run_id, research_config_id ON memories
FOR EACH ROW WHEN (OLD.research_run_id IS NOT NULL AND NEW.research_run_id IS NOT OLD.research_run_id)
               OR (OLD.research_config_id IS NOT NULL AND NEW.research_config_id IS NOT OLD.research_config_id)
BEGIN
    SELECT RAISE(ABORT, 'research_run_id and research_config_id are written once');
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_run_registered_insert
BEFORE INSERT ON memories
FOR EACH ROW WHEN (NEW.research_run_id IS NOT NULL OR NEW.research_config_id IS NOT NULL)
              AND NOT EXISTS (SELECT 1 FROM research_runs r
                               WHERE r.run_id = NEW.research_run_id AND r.config_id = NEW.research_config_id)
BEGIN
    SELECT RAISE(ABORT, 'RESEARCH_NEEDS_RUN: the run and configuration are not registered');
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_run_registered_update
BEFORE UPDATE OF research_run_id, research_config_id ON memories
FOR EACH ROW WHEN (NEW.research_run_id IS NOT NULL OR NEW.research_config_id IS NOT NULL)
              AND NOT EXISTS (SELECT 1 FROM research_runs r
                               WHERE r.run_id = NEW.research_run_id AND r.config_id = NEW.research_config_id)
BEGIN
    SELECT RAISE(ABORT, 'RESEARCH_NEEDS_RUN: the run and configuration are not registered');
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_run_link_registered
BEFORE INSERT ON memory_research_runs
FOR EACH ROW WHEN NOT EXISTS (SELECT 1 FROM research_runs r
                               WHERE r.run_id = NEW.run_id AND r.config_id = NEW.config_id)
BEGIN
    SELECT RAISE(ABORT, 'RESEARCH_NEEDS_RUN: the run and configuration are not registered');
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_configs_no_update
BEFORE UPDATE ON research_configs
BEGIN
    SELECT RAISE(ABORT, 'research_configs is insert-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_configs_no_delete
BEFORE DELETE ON research_configs
BEGIN
    SELECT RAISE(ABORT, 'research_configs is insert-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_runs_config_exists
BEFORE INSERT ON research_runs
FOR EACH ROW WHEN NOT EXISTS (SELECT 1 FROM research_configs c WHERE c.config_id = NEW.config_id)
BEGIN
    SELECT RAISE(ABORT, 'research_runs names a configuration that is not registered');
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_runs_no_update
BEFORE UPDATE ON research_runs
BEGIN
    SELECT RAISE(ABORT, 'research_runs is insert-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_runs_no_delete
BEFORE DELETE ON research_runs
BEGIN
    SELECT RAISE(ABORT, 'research_runs is insert-only');
END;

-- research implies outside; a named run is linked ------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_marks_research_insert
AFTER INSERT ON memories
FOR EACH ROW WHEN NEW.research_mark = 1 OR NEW.research_run_id IS NOT NULL
BEGIN
    UPDATE memories SET research_mark = 1, untrusted_source = 1
     WHERE id = NEW.id AND (research_mark IS NOT 1 OR untrusted_source IS NOT 1);
    INSERT OR IGNORE INTO memory_research_runs (memory_id, run_id, config_id, inherited)
    SELECT NEW.id, NEW.research_run_id, NEW.research_config_id, 0 WHERE NEW.research_run_id IS NOT NULL;
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_research_named
AFTER UPDATE OF research_run_id ON memories
FOR EACH ROW WHEN OLD.research_run_id IS NULL AND NEW.research_run_id IS NOT NULL
BEGIN
    INSERT OR IGNORE INTO memory_research_runs (memory_id, run_id, config_id, inherited)
    VALUES (NEW.id, NEW.research_run_id, NEW.research_config_id, 0);
END;

-- what an older writer of research can be recognised by ------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_marks_signature_insert
AFTER INSERT ON memories
FOR EACH ROW WHEN NEW.untrusted_source IS 0
              AND (NEW.source_project = 'gap-research' OR NEW.derived_from = 'untrusted_web_source')
BEGIN
    UPDATE memories SET untrusted_source = 1 WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_signature_tag
AFTER INSERT ON memory_tags
FOR EACH ROW WHEN NEW.tag = 'gap-research'
BEGIN
    UPDATE memories SET untrusted_source = 1 WHERE id = NEW.memory_id AND untrusted_source IS NOT 1;
END;

-- a mark set late reaches everything already derived ---------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_marks_late_outside
AFTER UPDATE OF untrusted_source ON memories
FOR EACH ROW WHEN NEW.untrusted_source IS NOT 0 AND OLD.untrusted_source IS NOT NEW.untrusted_source
BEGIN
    UPDATE memories SET untrusted_source = 1
     WHERE untrusted_source IS NOT 1 AND id <> NEW.id AND id IN (
        WITH RECURSIVE reach(id) AS (
            SELECT NEW.id
            UNION
            SELECT m.id FROM reach CROSS JOIN memories m WHERE m.id IN (
                SELECT cs.consolidated_memory_id FROM consolidation_sources cs WHERE cs.source_memory_id = reach.id
                UNION ALL
                SELECT mp.derived_memory_id FROM memory_provenance mp WHERE mp.source_memory_id = reach.id
                   AND mp.transformation_type IN ('consolidation', 'reflection', 'distillation', 'dream_synthesis', 'council_deliberation', 'manual_derivation')
                UNION ALL
                SELECT pa.child_id FROM memory_parents pa WHERE pa.parent_id = reach.id
                UNION ALL
                SELECT rc.synthesis_memory_id FROM raptor_clusters rc,
                       json_each(CASE WHEN json_valid(rc.member_ids_json) THEN rc.member_ids_json ELSE '[]' END) je
                 WHERE rc.synthesis_memory_id IS NOT NULL AND je.value = reach.id))
        SELECT id FROM reach);
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_late_research
AFTER UPDATE OF research_mark ON memories
FOR EACH ROW WHEN NEW.research_mark = 1 AND OLD.research_mark IS NOT 1
BEGIN
    UPDATE memories SET research_mark = 1, untrusted_source = 1
     WHERE (research_mark IS NOT 1 OR untrusted_source IS NOT 1) AND id IN (
        WITH RECURSIVE reach(id) AS (
            SELECT NEW.id
            UNION
            SELECT m.id FROM reach CROSS JOIN memories m WHERE m.id IN (
                SELECT cs.consolidated_memory_id FROM consolidation_sources cs WHERE cs.source_memory_id = reach.id
                UNION ALL
                SELECT mp.derived_memory_id FROM memory_provenance mp WHERE mp.source_memory_id = reach.id
                   AND mp.transformation_type IN ('consolidation', 'reflection', 'distillation', 'dream_synthesis', 'council_deliberation', 'manual_derivation')
                UNION ALL
                SELECT pa.child_id FROM memory_parents pa WHERE pa.parent_id = reach.id
                UNION ALL
                SELECT rc.synthesis_memory_id FROM raptor_clusters rc,
                       json_each(CASE WHEN json_valid(rc.member_ids_json) THEN rc.member_ids_json ELSE '[]' END) je
                 WHERE rc.synthesis_memory_id IS NOT NULL AND je.value = reach.id))
        SELECT id FROM reach);
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_run_linked
AFTER INSERT ON memory_research_runs
FOR EACH ROW
BEGIN
    INSERT OR IGNORE INTO memory_research_runs (memory_id, run_id, config_id, inherited)
    SELECT m.id, NEW.run_id, NEW.config_id, 1 FROM memories m WHERE m.id <> NEW.memory_id AND m.id IN (
        WITH RECURSIVE reach(id) AS (
            SELECT NEW.memory_id
            UNION
            SELECT m.id FROM reach CROSS JOIN memories m WHERE m.id IN (
                SELECT cs.consolidated_memory_id FROM consolidation_sources cs WHERE cs.source_memory_id = reach.id
                UNION ALL
                SELECT mp.derived_memory_id FROM memory_provenance mp WHERE mp.source_memory_id = reach.id
                   AND mp.transformation_type IN ('consolidation', 'reflection', 'distillation', 'dream_synthesis', 'council_deliberation', 'manual_derivation')
                UNION ALL
                SELECT pa.child_id FROM memory_parents pa WHERE pa.parent_id = reach.id
                UNION ALL
                SELECT rc.synthesis_memory_id FROM raptor_clusters rc,
                       json_each(CASE WHEN json_valid(rc.member_ids_json) THEN rc.member_ids_json ELSE '[]' END) je
                 WHERE rc.synthesis_memory_id IS NOT NULL AND je.value = reach.id))
        SELECT id FROM reach);
    UPDATE memories SET research_mark = 1, untrusted_source = 1,
           research_config_id = CASE WHEN research_run_id IS NULL THEN NEW.config_id ELSE research_config_id END,
           research_run_id = CASE WHEN research_run_id IS NULL THEN NEW.run_id ELSE research_run_id END
     WHERE (research_mark IS NOT 1 OR untrusted_source IS NOT 1 OR research_run_id IS NULL) AND id IN (
        WITH RECURSIVE reach(id) AS (
            SELECT NEW.memory_id
            UNION
            SELECT m.id FROM reach CROSS JOIN memories m WHERE m.id IN (
                SELECT cs.consolidated_memory_id FROM consolidation_sources cs WHERE cs.source_memory_id = reach.id
                UNION ALL
                SELECT mp.derived_memory_id FROM memory_provenance mp WHERE mp.source_memory_id = reach.id
                   AND mp.transformation_type IN ('consolidation', 'reflection', 'distillation', 'dream_synthesis', 'council_deliberation', 'manual_derivation')
                UNION ALL
                SELECT pa.child_id FROM memory_parents pa WHERE pa.parent_id = reach.id
                UNION ALL
                SELECT rc.synthesis_memory_id FROM raptor_clusters rc,
                       json_each(CASE WHEN json_valid(rc.member_ids_json) THEN rc.member_ids_json ELSE '[]' END) je
                 WHERE rc.synthesis_memory_id IS NOT NULL AND je.value = reach.id))
        SELECT id FROM reach);
END;

-- a new edge carries its parent's marks to its child ---------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_marks_edge_consolidation
AFTER INSERT ON consolidation_sources
FOR EACH ROW
BEGIN
    UPDATE memories SET untrusted_source = 1
     WHERE id = NEW.consolidated_memory_id AND untrusted_source IS NOT 1
       AND COALESCE((SELECT p.untrusted_source IS NOT 0 FROM memories p WHERE p.id = NEW.source_memory_id), 1);
    UPDATE memories SET research_mark = 1
     WHERE id = NEW.consolidated_memory_id AND research_mark = 0
       AND EXISTS (SELECT 1 FROM memories p WHERE p.id = NEW.source_memory_id AND p.research_mark = 1);
    INSERT OR IGNORE INTO memory_research_runs (memory_id, run_id, config_id, inherited)
    SELECT NEW.consolidated_memory_id, r.run_id, r.config_id, 1 FROM memory_research_runs r
     WHERE r.memory_id = NEW.source_memory_id AND EXISTS (SELECT 1 FROM memories c WHERE c.id = NEW.consolidated_memory_id);
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_edge_provenance
AFTER INSERT ON memory_provenance
FOR EACH ROW
WHEN NEW.transformation_type IN ('consolidation', 'reflection', 'distillation', 'dream_synthesis', 'council_deliberation', 'manual_derivation')
BEGIN
    UPDATE memories SET untrusted_source = 1
     WHERE id = NEW.derived_memory_id AND untrusted_source IS NOT 1
       AND COALESCE((SELECT p.untrusted_source IS NOT 0 FROM memories p WHERE p.id = NEW.source_memory_id), 1);
    UPDATE memories SET research_mark = 1
     WHERE id = NEW.derived_memory_id AND research_mark = 0
       AND EXISTS (SELECT 1 FROM memories p WHERE p.id = NEW.source_memory_id AND p.research_mark = 1);
    INSERT OR IGNORE INTO memory_research_runs (memory_id, run_id, config_id, inherited)
    SELECT NEW.derived_memory_id, r.run_id, r.config_id, 1 FROM memory_research_runs r
     WHERE r.memory_id = NEW.source_memory_id AND EXISTS (SELECT 1 FROM memories c WHERE c.id = NEW.derived_memory_id);
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_edge_parent
AFTER INSERT ON memory_parents
FOR EACH ROW
BEGIN
    UPDATE memories SET untrusted_source = 1
     WHERE id = NEW.child_id AND untrusted_source IS NOT 1
       AND COALESCE((SELECT p.untrusted_source IS NOT 0 FROM memories p WHERE p.id = NEW.parent_id), 1);
    UPDATE memories SET research_mark = 1
     WHERE id = NEW.child_id AND research_mark = 0
       AND EXISTS (SELECT 1 FROM memories p WHERE p.id = NEW.parent_id AND p.research_mark = 1);
    INSERT OR IGNORE INTO memory_research_runs (memory_id, run_id, config_id, inherited)
    SELECT NEW.child_id, r.run_id, r.config_id, 1 FROM memory_research_runs r
     WHERE r.memory_id = NEW.parent_id AND EXISTS (SELECT 1 FROM memories c WHERE c.id = NEW.child_id);
END;

-- an edge that is changed is a new edge; what the old one said stays said
CREATE TRIGGER IF NOT EXISTS trg_marks_edge_consolidation_update
AFTER UPDATE OF consolidated_memory_id, source_memory_id ON consolidation_sources
FOR EACH ROW
BEGIN
    UPDATE memories SET untrusted_source = 1
     WHERE id = NEW.consolidated_memory_id AND untrusted_source IS NOT 1
       AND COALESCE((SELECT p.untrusted_source IS NOT 0 FROM memories p WHERE p.id = NEW.source_memory_id), 1);
    UPDATE memories SET research_mark = 1
     WHERE id = NEW.consolidated_memory_id AND research_mark = 0
       AND EXISTS (SELECT 1 FROM memories p WHERE p.id = NEW.source_memory_id AND p.research_mark = 1);
    INSERT OR IGNORE INTO memory_research_runs (memory_id, run_id, config_id, inherited)
    SELECT NEW.consolidated_memory_id, r.run_id, r.config_id, 1 FROM memory_research_runs r
     WHERE r.memory_id = NEW.source_memory_id AND EXISTS (SELECT 1 FROM memories c WHERE c.id = NEW.consolidated_memory_id);
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_edge_provenance_update
AFTER UPDATE OF derived_memory_id, source_memory_id, transformation_type ON memory_provenance
FOR EACH ROW
WHEN NEW.transformation_type IN ('consolidation', 'reflection', 'distillation', 'dream_synthesis', 'council_deliberation', 'manual_derivation')
BEGIN
    UPDATE memories SET untrusted_source = 1
     WHERE id = NEW.derived_memory_id AND untrusted_source IS NOT 1
       AND COALESCE((SELECT p.untrusted_source IS NOT 0 FROM memories p WHERE p.id = NEW.source_memory_id), 1);
    UPDATE memories SET research_mark = 1
     WHERE id = NEW.derived_memory_id AND research_mark = 0
       AND EXISTS (SELECT 1 FROM memories p WHERE p.id = NEW.source_memory_id AND p.research_mark = 1);
    INSERT OR IGNORE INTO memory_research_runs (memory_id, run_id, config_id, inherited)
    SELECT NEW.derived_memory_id, r.run_id, r.config_id, 1 FROM memory_research_runs r
     WHERE r.memory_id = NEW.source_memory_id AND EXISTS (SELECT 1 FROM memories c WHERE c.id = NEW.derived_memory_id);
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_edge_parent_update
AFTER UPDATE OF child_id, parent_id ON memory_parents
FOR EACH ROW
BEGIN
    UPDATE memories SET untrusted_source = 1
     WHERE id = NEW.child_id AND untrusted_source IS NOT 1
       AND COALESCE((SELECT p.untrusted_source IS NOT 0 FROM memories p WHERE p.id = NEW.parent_id), 1);
    UPDATE memories SET research_mark = 1
     WHERE id = NEW.child_id AND research_mark = 0
       AND EXISTS (SELECT 1 FROM memories p WHERE p.id = NEW.parent_id AND p.research_mark = 1);
    INSERT OR IGNORE INTO memory_research_runs (memory_id, run_id, config_id, inherited)
    SELECT NEW.child_id, r.run_id, r.config_id, 1 FROM memory_research_runs r
     WHERE r.memory_id = NEW.parent_id AND EXISTS (SELECT 1 FROM memories c WHERE c.id = NEW.child_id);
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_raptor_insert
AFTER INSERT ON raptor_clusters
FOR EACH ROW WHEN NEW.synthesis_memory_id IS NOT NULL AND json_valid(NEW.member_ids_json)
BEGIN
    UPDATE memories SET untrusted_source = 1
     WHERE id = NEW.synthesis_memory_id AND untrusted_source IS NOT 1
       AND EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(NEW.member_ids_json) THEN NEW.member_ids_json ELSE '[]' END) je
                    WHERE COALESCE((SELECT p.untrusted_source IS NOT 0 FROM memories p WHERE p.id = je.value), 1));
    UPDATE memories SET research_mark = 1
     WHERE id = NEW.synthesis_memory_id AND research_mark = 0
       AND EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(NEW.member_ids_json) THEN NEW.member_ids_json ELSE '[]' END) je, memories p WHERE p.id = je.value AND p.research_mark = 1);
    INSERT OR IGNORE INTO memory_research_runs (memory_id, run_id, config_id, inherited)
    SELECT NEW.synthesis_memory_id, r.run_id, r.config_id, 1 FROM json_each(CASE WHEN json_valid(NEW.member_ids_json) THEN NEW.member_ids_json ELSE '[]' END) je
      JOIN memory_research_runs r ON r.memory_id = je.value
     WHERE EXISTS (SELECT 1 FROM memories c WHERE c.id = NEW.synthesis_memory_id);
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_raptor_update
AFTER UPDATE OF member_ids_json, synthesis_memory_id ON raptor_clusters
FOR EACH ROW WHEN NEW.synthesis_memory_id IS NOT NULL AND json_valid(NEW.member_ids_json)
BEGIN
    UPDATE memories SET untrusted_source = 1
     WHERE id = NEW.synthesis_memory_id AND untrusted_source IS NOT 1
       AND EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(NEW.member_ids_json) THEN NEW.member_ids_json ELSE '[]' END) je
                    WHERE COALESCE((SELECT p.untrusted_source IS NOT 0 FROM memories p WHERE p.id = je.value), 1));
    UPDATE memories SET research_mark = 1
     WHERE id = NEW.synthesis_memory_id AND research_mark = 0
       AND EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(NEW.member_ids_json) THEN NEW.member_ids_json ELSE '[]' END) je, memories p WHERE p.id = je.value AND p.research_mark = 1);
    INSERT OR IGNORE INTO memory_research_runs (memory_id, run_id, config_id, inherited)
    SELECT NEW.synthesis_memory_id, r.run_id, r.config_id, 1 FROM json_each(CASE WHEN json_valid(NEW.member_ids_json) THEN NEW.member_ids_json ELSE '[]' END) je
      JOIN memory_research_runs r ON r.memory_id = je.value
     WHERE EXISTS (SELECT 1 FROM memories c WHERE c.id = NEW.synthesis_memory_id);
END;

-- an older program records a dispatch as 'running' before it sends anything: without an approval it stops there. An
-- approval is the sha256 of the topic, 64 lowercase hex characters; anything else is none ----------------------------
CREATE TRIGGER IF NOT EXISTS trg_marks_dispatch_closed
BEFORE INSERT ON gap_research_log
FOR EACH ROW WHEN NEW.status = 'running' AND NOT (typeof(NEW.approval_hash) = 'text' AND length(NEW.approval_hash) = 64 AND NEW.approval_hash NOT GLOB '*[^0-9a-f]*')
BEGIN
    SELECT RAISE(ABORT, 'RESEARCH_DISPATCH_CLOSED');
END;

CREATE TRIGGER IF NOT EXISTS trg_marks_dispatch_closed_update
BEFORE UPDATE OF status, approval_hash ON gap_research_log
FOR EACH ROW WHEN NEW.status = 'running' AND OLD.status IS NOT 'running'
              AND NOT (typeof(NEW.approval_hash) = 'text' AND length(NEW.approval_hash) = 64 AND NEW.approval_hash NOT GLOB '*[^0-9a-f]*')
BEGIN
    SELECT RAISE(ABORT, 'RESEARCH_DISPATCH_CLOSED');
END;

-- archiving or restoring a row after `forget` archived it is a later decision: `unarchive` leaves it alone -----------
CREATE TRIGGER IF NOT EXISTS trg_marks_forget_superseded
AFTER UPDATE OF is_archived ON memories
FOR EACH ROW WHEN EXISTS (SELECT 1 FROM forget_receipts f WHERE f.memory_id = NEW.id AND f.undone_at IS NULL
                             AND f.superseded_at IS NULL)
BEGIN
    UPDATE forget_receipts SET superseded_at = datetime('now')
     WHERE memory_id = NEW.id AND undone_at IS NULL AND superseded_at IS NULL;
END;
