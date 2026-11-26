# -*- coding: utf-8 -*-
"""A brain's name is data its database holds, never a literal the product ships.

WHAT WOULD BREAK WITHOUT IT: a stranger installs the package and every memory they store is labelled with the name of
the brain it was first built for; an older brain cannot take a name of its own because a CHECK allows only two; and a
fix that edits the column's DEFAULT silently relabels every row stored before the column existed, which integrity_check
does not see.
"""
import argparse
import ast
import contextlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tokenize
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wall_brain as wb
from _wall_brain import Brain, Case, ROOT, data

sys.path.insert(0, str(ROOT / "organs"))
import marks
import schema_migrator

LEGACY = marks.LEGACY_BRAINS
OLD_COLUMN = {          # the two spellings the two creation routes left in memories' schema text
    "027 file": "ALTER TABLE memories ADD COLUMN source_brain TEXT NOT NULL DEFAULT '%s'\n    CHECK (source_brain IN ('%s', '%s'))" % (LEGACY[0], *LEGACY),
    "dream pipeline": "ALTER TABLE memories ADD COLUMN source_brain TEXT NOT NULL DEFAULT '%s' CHECK (source_brain IN ('%s', '%s'))" % (LEGACY[0], *LEGACY),
}


def labels(c):
    return {r[0] for r in c.execute("SELECT DISTINCT source_brain FROM memories")}


def old_brain(tmp, route, rows=3):
    """A brain as an older release left it: the column added by one of the two routes, `rows` memories stored before it
    existed (they read its DEFAULT) and one after."""
    db = wb.premark_brain(tmp)
    c = sqlite3.connect(db)
    c.execute("PRAGMA writable_schema = ON")
    sql = c.execute("SELECT sql FROM sqlite_master WHERE name = 'memories'").fetchone()[0]
    c.execute("PRAGMA writable_schema = OFF")
    c.close()
    import shutil
    fresh = tmp / "old.db"
    s = sqlite3.connect(wb.ROOT / "seed" / "claude_memory_blank.db")
    t = sqlite3.connect(fresh)
    s.backup(t)
    s.close()
    cols = {r[1] for r in t.execute("PRAGMA table_info(memories)")}
    if "source_brain" in cols:
        t.execute("ALTER TABLE memories DROP COLUMN source_brain")
    for i in range(rows):
        t.execute("INSERT INTO memories (content, category) VALUES (?, 'insight')", (f"stored before the column {i}",))
    t.execute(OLD_COLUMN[route])
    t.execute("INSERT INTO memories (content, category, source_brain) VALUES ('stored after', 'insight', ?)", (LEGACY[1],))
    t.commit()
    t.close()
    shutil.move(str(fresh), str(db))
    return db


def label_hash(db):
    c = sqlite3.connect(db)
    try:
        return marks._label_hash(c)
    finally:
        c.close()


class Migration047(Case):
    def test_both_routes_lose_the_check_and_keep_every_row(self):
        for n, route in enumerate(OLD_COLUMN):
            db = old_brain(self.tmp / str(n), route)
            before = label_hash(db)
            got = marks.migrate_047(db)
            self.assertEqual((got["status"], got["check_removed"], got["default_changed"]), ("applied", True, False), route)
            self.assertEqual(label_hash(db), before, route)
            c = sqlite3.connect(db)
            self.assertEqual(c.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM memories WHERE source_brain = ?", (LEGACY[0],)).fetchone()[0], 3)
            c.execute("INSERT INTO memories (content, category, source_brain) VALUES ('a name of its own', 'insight', 'atlas')")
            sql = c.execute("SELECT sql FROM sqlite_master WHERE name = 'memories'").fetchone()[0]
            self.assertIn("NOT NULL DEFAULT '%s'" % LEGACY[0], sql)            # a populated brain keeps its DEFAULT
            self.assertEqual(c.execute("SELECT value FROM metadata WHERE key = ?", (marks.SAVED_SQL_KEY,)).fetchone()[0].count("CHECK (source_brain"), 1)
            c.close()
            self.assertEqual(marks.migrate_047(db)["status"], "already", route)

    def test_an_empty_brain_gets_the_generic_default(self):
        db = old_brain(self.tmp, "027 file", rows=0)
        c = sqlite3.connect(db)
        c.execute("DELETE FROM memories")
        c.commit()
        c.close()
        got = marks.migrate_047(db)
        self.assertEqual((got["status"], got["default_changed"]), ("applied", True))
        c = sqlite3.connect(db)
        c.execute("INSERT INTO memories (content, category) VALUES ('x', 'insight')")
        self.assertEqual(labels(c), {"local"})
        sql = c.execute("SELECT sql FROM sqlite_master WHERE name = 'memories'").fetchone()[0]
        self.assertFalse(any(n in sql for n in LEGACY))

    def test_a_default_edit_on_a_populated_brain_is_refused_and_changes_nothing(self):
        db = old_brain(self.tmp, "027 file")
        before, raw = label_hash(db), Path(db).read_bytes()
        got = marks.migrate_047(db, edit=lambda new, rows: new.replace("DEFAULT '%s'" % LEGACY[0], "DEFAULT 'local'"))
        self.assertEqual(got["status"], "aborted")
        self.assertIn("label changed", got["reason"])
        self.assertEqual((label_hash(db), Path(db).read_bytes()), (before, raw))

    def test_a_text_that_breaks_the_database_is_refused(self):
        db = old_brain(self.tmp, "027 file")
        raw = Path(db).read_bytes()
        for bad in (lambda new, rows: new.replace("CREATE TABLE", "CREATE TABL", 1),
                    lambda new, rows: new + " CHECK (source_brain IN ('nobody'))"):
            got = marks.migrate_047(db, edit=bad)
            self.assertEqual(got["status"], "aborted")
            self.assertEqual(Path(db).read_bytes(), raw)

    def test_a_brain_it_cannot_edit_keeps_working_on_the_legacy_label_and_doctor_says_so(self):
        db = old_brain(self.tmp, "027 file")
        os.chmod(db, 0o444)
        try:
            self.assertEqual(marks.migrate_047(db)["status"], "aborted")
        finally:
            os.chmod(db, 0o644)
        c = sqlite3.connect(db)
        c.execute("INSERT INTO metadata (key, value) VALUES (?, 'atlas')", (marks.LABEL_KEY,))
        c.commit()
        info = marks.label_of(c)
        c.close()
        self.assertEqual((info["label"], info["source"], info["legacy_check"]), (LEGACY[0], "legacy-check", True))
        rc, env, _err = wb.full_cli(db, "doctor")
        self.assertIn("still accepts only the legacy labels", data(env)["checks"]["brain_label"]["warning"])
        b = wb.brain_over(db)
        schema_migrator.apply(db)                                      # an apply that can edit it: the label is its own
        self.assertEqual(marks.label_of(b.c)["label"], "atlas")

    def test_rollback_puts_the_text_back_only_while_every_label_is_legacy(self):
        db = old_brain(self.tmp, "dream pipeline")
        c = sqlite3.connect(db)
        old_sql = c.execute("SELECT sql FROM sqlite_master WHERE name = 'memories'").fetchone()[0]
        c.close()
        marks.migrate_047(db)
        c = sqlite3.connect(db)
        c.execute("INSERT INTO memories (content, category, source_brain) VALUES ('x', 'insight', 'atlas')")
        c.commit()
        self.assertEqual(marks.rollback_047(db)["status"], "refused")
        c.execute("DELETE FROM memories WHERE source_brain = 'atlas'")
        c.commit()
        c.close()
        self.assertEqual(marks.rollback_047(db)["status"], "ok")
        c = sqlite3.connect(db)
        self.assertEqual(c.execute("SELECT sql FROM sqlite_master WHERE name = 'memories'").fetchone()[0], old_sql)
        self.assertEqual(c.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_the_migrator_runs_it_and_a_new_brain_names_nobody(self):
        b = Brain(self.tmp)
        sql = b.one("SELECT sql FROM sqlite_master WHERE name = 'memories'")[0]
        self.assertFalse(any(n in sql for n in LEGACY), sql[-200:])
        self.assertIn("DEFAULT 'local'", sql)
        got = schema_migrator.apply(b.path)
        self.assertIn(got["migration_047"]["status"], ("already",))


class Resolver(Case):
    def test_the_database_wins_then_the_environment_then_the_generic_word(self):
        b = Brain(self.tmp)
        with mock.patch.dict(os.environ, {"VOIDLOOM_BRAIN": ""}):
            self.assertEqual(marks.label_of(b.c)["label"], "local")
        with mock.patch.dict(os.environ, {"VOIDLOOM_BRAIN": "env-name"}):
            self.assertEqual(marks.label_of(b.c)["label"], "env-name")
            marks.set_label(b.path, "db-name")
            got = marks.label_of(b.c)
            self.assertEqual((got["label"], got["source"], got["mismatch"]), ("db-name", "database", True))
        with mock.patch.dict(os.environ, {"VOIDLOOM_BRAIN": "Not Valid!"}):
            self.assertEqual(marks.label_of(b.c)["env"], None)
        for bad in ("", "x" * 65, "UPPER", "a b", "é", None):
            with self.assertRaises(marks.MarksError):
                marks.set_label(b.path, bad, force=True)
        with self.assertRaises(marks.MarksError) as e:
            marks.set_label(b.path, "other")
        self.assertEqual(e.exception.code, "LABEL_ALREADY_SET")
        self.assertEqual(marks.set_label(b.path, "other", force=True)["previous"], "db-name")

    def test_a_mismatch_is_written_as_the_brains_own_and_said_never_refused(self):
        b = Brain(self.tmp)
        marks.set_label(b.path, "own-name")
        rc, env, _err = wb.full_cli(b.path, "store", "--content", "a note", "--category", "insight",
                                    env={"VOIDLOOM_BRAIN": "other-name"})
        self.assertEqual(rc, 0, env)
        self.assertEqual(b.one("SELECT source_brain FROM memories WHERE id = ?", (data(env)["memory_id"],))[0], "own-name")
        self.assertIn("other-name", json.dumps(env))
        rc, env, _err = wb.full_cli(b.path, "doctor", env={"VOIDLOOM_BRAIN": "other-name"})
        got = data(env)["checks"]["brain_label"]
        self.assertTrue(got["mismatch"])
        self.assertEqual(got["rows_by_label"], {"own-name": 1})
        self.assertIn("differs from this brain's own label", got["warning"])

    def test_the_verb(self):
        b = Brain(self.tmp)
        rc, env, _err = wb.full_cli(b.path, "brain-label")
        self.assertEqual((rc, data(env)["label"], data(env)["source"]), (0, "local", "default"))
        rc, env, _err = wb.full_cli(b.path, "brain-label", "set", "my-brain")
        self.assertEqual((rc, env["code"]), (0, "brain_label_set"))
        rc, env, _err = wb.full_cli(b.path, "brain-label", "set", "Bad Name")
        self.assertEqual((rc != 0, env["code"]), (True, "LABEL_INVALID"))
        rc, env, _err = wb.full_cli(b.path, "brain-label", "set", "another")
        self.assertEqual((rc != 0, env["code"]), (True, "LABEL_ALREADY_SET"))
        rc, env, _err = wb.full_cli(b.path, "brain-label", "set", "another", "--force")
        self.assertEqual(rc, 0)
        from voidloom import _facade
        self.assertEqual(_facade.brain_label(db_path=b.path)["label"], "another")


def drive_every_writer(case, b):
    """Every writer of a memory row the package has, once each, over brain `b`. Returns the ids each wrote."""
    import memory_write
    import governance
    import monthly_self_eval as mse
    import decision_units
    import memory_dreaming
    wrote = {}
    top = b.top()

    def new():
        nonlocal top
        got = b.new_ids(top)
        top = b.top()
        return got
    for lite in (False, True):
        rc, env, _err = wb.full_cli(b.path, "store", "--content", "a stored note", "--category", "insight",
                                    env={"VOIDLOOM_TIER0_ONLY": "1"} if lite else None)
        case.assertEqual(rc, 0, env)
        wrote["store lite" if lite else "store"] = new()
    rc, env, _err = wb.full_cli(b.path, "batch-store", "--items", json.dumps([dict(content="a batch item", category="insight")]))
    case.assertEqual(rc, 0, env)
    wrote["batch-store"] = new()
    p = subprocess.run([sys.executable, "-B", str(ROOT / "organs" / "claude_memory_lite.py"), "load-seed", "--file",
                        str(ROOT / "seed" / "seed_pack.json")], env=wb.child_env(b.path), capture_output=True, text=True, timeout=300)
    case.assertEqual(p.returncode, 0, p.stderr[-300:])
    wrote["seed pack"] = new()
    wrote["write_memory"] = [memory_write.write_memory(b.c, content="written by an organ", category="insight",
                                                       embed_policy="skip", skip_reason="test").memory_id]
    wrote["derived_insert"] = [marks.derived_insert(b.c, {"content": "derived", "category": "insight"}, [wrote["store"][0]])]
    b.c.commit()
    mid = memory_dreaming._store_dream_as_reflection(b.c, "RELATION: tension\nOne dream between two notes.",
                                                     wrote["store"][0], wrote["batch-store"][0], "divergent")
    b.c.commit()
    wrote["dream"] = [mid] if mid else new()
    for _ in range(3):
        b.c.execute("INSERT INTO governance_proposals (report_batch, writer_kind, verdict, reason, reason_code, intent_json, "
                    "intent_hash, status) VALUES ('b', 'k', 'HOLD', 'r', 'code-x', '{}', 'h', 'pending')")
    b.c.commit()
    governance._maybe_distill_failure(b.c, "code-x", "k", {})
    b.c.commit()
    wrote["governance"] = new()
    what = [{"title": "A thing", "rationale": "r", "evidence": "e", "priority": "low", "piece_affected": "x"}]
    rid = b.c.execute("INSERT INTO self_eval_reports (month, health_scorecard, proposed_enhancements, proposed_deprecations, "
                      "threshold_assessment, persona_evolution, status) VALUES ('2026-01', '{}', ?, '[]', '{}', '{}', 'pending')",
                      (json.dumps(what),)).lastrowid
    decision_units.create_units_for_report(b.c, rid, what, [])
    b.c.commit()
    mse.surface_pending_for_owner(b.path)
    wrote["self-eval surface"] = new()
    b.c.execute("INSERT INTO self_eval_enacted_proposals (report_id, proposal_title, proposal_json, status) VALUES (?, 'A thing', ?, 'pending')",
                (rid, json.dumps(what[0])))
    b.c.commit()
    mse.enact_approved(b.path, rid, confirm=True)
    wrote["self-eval enact"] = new()
    md = case.tmp / "notes"
    md.mkdir(exist_ok=True)
    (md / "note.md").write_text("---\nname: n\ndescription: a migrated note\n---\nA note migrated from a file.\n", encoding="utf-8")
    with wb.engine(b.path) as cm, contextlib.redirect_stdout(io.StringIO()):
        cm.cmd_migrate(argparse.Namespace(source=[str(md / "*.md")], project="p", dry_run=False))
    wrote["migrate"] = new()
    import gap_mapping as gm
    topic = "Zanzibar quokka freezing"
    b.c.execute("INSERT INTO gap_inventory (entity, gap_type, score, auto_research_eligible) VALUES (?, 'thin_entity', 0.9, 1)", (topic,))
    b.c.commit()
    (Path(b.path).parent / "approved_research_topics.json").write_text(json.dumps([topic]), encoding="utf-8")
    with wb.engine(b.path), mock.patch.object(gm, "_dispatch_research", lambda p, t=0: {
            "status": "success", "output": "Quokkas survive, see doi 10.1/x for the measured rate.", "wall_clock_seconds": 0.1}):
        gm.dispatch_auto_research(b.path, gap_ids=[b.one("SELECT MAX(id) FROM gap_inventory")[0]])
    wrote["gap research"] = new()
    for name, ids in wrote.items():
        case.assertTrue(ids, f"{name} wrote nothing, so this proves nothing about it")
    return wrote


class EveryWriter(Case):
    def test_a_fresh_brain_with_no_label_writes_the_generic_word(self):
        b = Brain(self.tmp)
        with mock.patch.dict(os.environ, {"VOIDLOOM_BRAIN": ""}):
            wrote = drive_every_writer(self, b)
        by = {name: {b.one("SELECT source_brain FROM memories WHERE id = ?", (i,))[0] for i in ids} for name, ids in wrote.items()}
        self.assertEqual({n: v for n, v in by.items() if v != {"local"}}, {})

    def test_an_upgraded_labelled_brain_writes_its_label_everywhere(self):
        db = old_brain(self.tmp, "027 file")
        schema_migrator.apply(db)
        b = wb.brain_over(db)
        b.c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', 'memory_embeddings_v2')")
        b.c.commit()
        marks.set_label(db, "x")
        old = b.top()
        with mock.patch.dict(os.environ, {"VOIDLOOM_BRAIN": ""}):
            drive_every_writer(self, b)
        self.assertEqual({r[0] for r in b.all("SELECT DISTINCT source_brain FROM memories WHERE id > ?", (old,))}, {"x"})
        self.assertEqual(b.one("SELECT COUNT(*) FROM memories WHERE id <= ? AND source_brain = ?", (old, LEGACY[0]))[0], 3)

    def test_every_insert_into_memories_names_its_label(self):
        """The static half: each statement the writer census lists stamps source_brain or goes through a writer that does."""
        import test_marks_every_writer_is_classified as w
        sites = sorted((rel, fn) for (rel, fn, verb), kind in w.WRITERS.items()
                       if verb == "INSERT" and kind not in ("fixture", "other", "helper"))
        self.assertEqual(len(sites), 8, sites)
        for rel, fn in sites:
            body = "".join(ast.get_source_segment((ROOT / rel).read_text(encoding="utf-8"), n) or ""
                           for n in ast.walk(ast.parse((ROOT / rel).read_text(encoding="utf-8")))
                           if isinstance(n, ast.FunctionDef) and n.name == fn)
            self.assertTrue(re.search(r"source_brain|stamp_label|_label_row|derived_columns\(|derived_insert\(", body),
                            f"{rel} {fn} inserts a memory and names no label")


class Council(Case):
    def test_no_default_written_by_code_names_the_persona(self):
        import counterfactual_memory as cf
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        cf.ensure_schema(c) if hasattr(cf, "ensure_schema") else None
        src = (ROOT / "organs" / "counterfactual_memory.py").read_text(encoding="utf-8")
        self.assertNotIn(LEGACY[0], src)
        import council
        self.assertEqual(council.canonical_speaker(LEGACY[0]), council.SYNTHESIZER)     # the legacy slug still reads
        self.assertNotIn(LEGACY[0], json.dumps(council.DEFAULT_PARTICIPANTS))

    def test_every_decision_event_names_its_actor(self):
        """The frozen DEFAULT of decision_events.actor names a person; no writer may leave it to the DEFAULT."""
        found = 0
        for rel in ("organs/decision_units.py", "organs/monthly_self_eval.py"):
            for m in re.finditer(r"INTO decision_events\s*\"\s*\"\(([^)]*)\)", (ROOT / rel).read_text(encoding="utf-8")):
                found += 1
                self.assertIn("actor", m.group(1), rel)
        self.assertEqual(found, 4)


class Tiers(Case):
    def test_a_twin_is_named_by_configuration(self):
        import voidloom_preamble as vp
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp)}):
            self.assertTrue(vp.twin_db_path("anyname-wsl").startswith(str(self.tmp)))
            self.assertTrue(vp.twin_db_path("%s-wsl" % LEGACY[0]).startswith(str(self.tmp)))   # a legacy name still reads
        src = (ROOT / "organs" / "claude_memory.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("VOIDLOOM_TWIN_TIER")', src)


# ---------------------------------------------------------------------------------------------------------------------
# What ships: the legacy names only where they are read
# ---------------------------------------------------------------------------------------------------------------------
NAMES = re.compile("|".join(LEGACY), re.I)
#: every place a shipped, non-frozen file may name a legacy label, and how often
ALLOWED = {
    "NOTICE": 0,
    "organs/marks.py": 2,                  # LEGACY_BRAINS: the labels read forever
    "organs/council.py": 1,                # the legacy speaker alias
    "organs/persona_lenses.py": 1,         # the legacy lens alias
    "tools/pii_gate.py": 4,                # the leak gate's allow list
}
FROZEN = ("migrations/", "voidloom/tests/", "seed/claude_memory_blank.db")


def name_census(texts):
    """{file: occurrences} of a legacy label in shipped, non-frozen files; `texts` maps file -> text."""
    out = {}
    for rel, text in texts.items():
        n = len(NAMES.findall(text))
        if n:
            out[rel] = n
    return out


def shipped_texts():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    return {f: (ROOT / f).read_text(encoding="utf-8", errors="replace") for f in files
            if not f.startswith(FROZEN) and (ROOT / f).is_file()}


class WhatShips(Case):
    def setUp(self):
        super().setUp()
        if not (ROOT / ".git").exists():
            raise unittest.SkipTest("not a checkout")

    def test_the_legacy_labels_appear_only_where_they_are_read(self):
        got = name_census(shipped_texts())
        self.assertEqual(got, {k: v for k, v in ALLOWED.items() if v})

    def test_a_name_added_to_a_default_is_seen(self):
        texts = shipped_texts()
        texts["organs/dream_pipeline.py"] += "\nDEFAULT_BRAIN = '%s'\n" % LEGACY[1]
        self.assertIn("organs/dream_pipeline.py", name_census(texts))

    def test_no_printed_or_default_string_names_the_first_owner(self):
        """String values in the code (not comments, not docstrings, not identifiers of the frozen schema)."""
        handle = re.compile(r"(?<![A-Za-z])drew(?![A-Za-z_])", re.I)
        allowed = {("organs/council.py", 1),          # the legacy speaker alias
                   ("organs/monthly_self_eval.py", 1)}  # the legacy tag of the pending surface, read to refresh it
        hits = {}
        for rel, text in shipped_texts().items():
            if not rel.endswith(".py"):
                continue
            prev = None
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type == tokenize.STRING and not (prev is None or prev.type in (tokenize.NEWLINE, tokenize.INDENT,
                                                                                        tokenize.DEDENT, tokenize.NL)):
                    n = len(handle.findall(tok.string))
                    if n:
                        hits[rel] = hits.get(rel, 0) + n
                if tok.type not in (tokenize.NL, tokenize.COMMENT):
                    prev = tok
        self.assertEqual(set(hits.items()), allowed)

    def test_notice_says_what_the_schema_holds(self):
        b = Brain(self.tmp)
        pattern = re.compile(r"\b(?:drew\w*|%s)" % "|".join(re.escape(n) + r"[\w-]*" for n in LEGACY), re.I)
        objects = sorted(n for _t, n, s in b.all("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL") if pattern.search(s))
        self.assertEqual(objects, sorted(["gap_inventory", "dream_log", "idx_dream_flagged", "council_sessions",
                                          "counterfactual_decisions", "arcane_manifests", "self_eval_reports",
                                          "cross_tier_bridges", "decision_events", "v_decision_unit_state"]))
        notice = " ".join((ROOT / "NOTICE").read_text(encoding="utf-8").split())
        self.assertIn("%d schema objects of a new brain carry them" % len(objects), notice)
        self.assertIn("test_names_confined.py", notice)


if __name__ == "__main__":
    unittest.main()
