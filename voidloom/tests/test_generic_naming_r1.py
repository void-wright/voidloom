# -*- coding: utf-8 -*-
"""GENERIC NAMING, THE REMAINDER (Round 1 lane L6, 2026-10-15).

The package ships to people who are not its author. What their install SAYS -- the prompts it sends a model, the rows it
writes into their brain, its labels and help text -- must name no owner, none of his personas and none of his tools.
After the persona-lens rename (f275d09) four organs still did:

  1  monthly_self_eval   wrote "<owner>, the monthly brain self-eval has reports awaiting your review" into every brain,
                         tagged it with the owner's name, and stamped approved enhancements "Approved by <owner>".
                         (Also here, from lane L3's note: a sleep run that closed 'timeout' -- contract C5 -- was not
                         counted as a failed night, so a month of timed-out nights graded with zero errors.)
  2  council             the synthesizer seat was the owner's persona by slug and by skill folder; round 3 was
                         stored as the owner's first name.
  3  arcane_manifest     the quarterly synthesis prompt told the model "<owner> will read this"; the synthesizer's
                         voice file fell back to the owner's persona folder by a hard-coded name.
  4  pre_cognitive_recall  the always-loaded tier-1 queries and the cluster weights were the owner's identity, name,
                         company CLI and schedule.

Compatibility, the way f275d09 kept it: a brain that STORES the old names keeps working, nothing stored is renamed, and
the owner's own defaults live in an optional per-brain file (`<brain>/pre_cognitive_recall.json`) beside the existing
`persona_lens_weights.json`.

This file names none of the owner's words: they are checked by SHA-256 (the test_leak_clean precedent), and legacy names
are reached through the organs' own LEGACY_* constants.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGANS = ROOT / "organs"

# sha256(word)[:16] for the owner's first name, his two brain personas, three of his lens personas and his company's
# name.  (The Council member names are the package's own -- ruling 3 -- so they are not in this set.)
_OWNER_WORD_HASHES = frozenset({
    "c850c75d30a9360f", "d082d09643c00e14", "3b9d8298f1b5086d",
    "c4ddeffba8c2336a", "2ef14060f7c45647", "b1e3c2ec1a80a8cc",
    "c9d22bd28f57026a",
})


def owner_words(text: str) -> list:
    """The words of `text` that are one of the owner's names (by hash); [] when it names nobody."""
    return sorted({w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
                   if hashlib.sha256(w.encode()).hexdigest()[:16] in _OWNER_WORD_HASHES})


def _blank_brain(path: Path) -> str:
    """A database with the metadata table every migration assumes (the organs' own self-tests lay it the same way)."""
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    c.commit()
    c.close()
    return str(path)


def _load(name: str, rel: str):
    """The organ under test, loaded from ROOT BY PATH -- a plain import could find a copy already in sys.modules."""
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ORGANS))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(ORGANS))
    return mod


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="genericnaming_"))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        # No persona is declared for the brains these tests build, whatever the host has.
        self._env = {k: os.environ.get(k) for k in ("WIZARD_LIBRARY_HOME", "VOIDLOOM_PERSONA_FILE",
                                                    "VOIDLOOM_PERSONA", "VOIDLOOM_SKILLS_DIR",
                                                    "WIZARD_CLAUDE_HOME", "WIZARD_LIBRARY_DB")}
        self.addCleanup(self._restore_env)
        self.home = self.tmp / "brain"
        self.home.mkdir()
        os.environ["WIZARD_LIBRARY_HOME"] = str(self.home)
        os.environ["VOIDLOOM_PERSONA_FILE"] = str(self.home / "persona.json")
        os.environ["VOIDLOOM_SKILLS_DIR"] = str(self.tmp / "skills")
        os.environ["WIZARD_CLAUDE_HOME"] = str(self.tmp / "claude_home")
        os.environ.pop("VOIDLOOM_PERSONA", None)

    def _restore_env(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def declare(self, name: str, with_skill: bool = True) -> str:
        """Declare `name` as this brain's persona (and lay its skill file); returns the skill folder."""
        (self.home / "persona.json").write_text(json.dumps({"default": name, "personas": {name: {"blurb": "x"}}}),
                                                encoding="utf-8")
        folder = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if with_skill:
            d = self.tmp / "skills" / folder
            d.mkdir(parents=True, exist_ok=True)
            (d / "SKILL.md").write_text("# %s\n" % name, encoding="utf-8")
        return folder


# ================================================================================================ 1: monthly_self_eval
class TestMonthlySelfEval(_Tmp):

    def setUp(self):
        super().setUp()
        self.mse = _load("mse_l6_under_test", "organs/monthly_self_eval.py")

    # --- L3's note: a timed-out night is a failed night -----------------------------------------------------------
    def _runs_brain(self):
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE sleep_loop_runs (id INTEGER PRIMARY KEY, started_at TEXT, completed_at TEXT, "
                  "status TEXT, duration_seconds REAL, memories_consolidated INTEGER)")
        c.execute("CREATE TABLE gap_inventory_batches (id INTEGER PRIMARY KEY, started_at TEXT, status TEXT)")
        c.execute("CREATE TABLE dream_batches (id INTEGER PRIMARY KEY, created_at TEXT)")
        # New rows are UTC 'YYYY-MM-DD HH:MM:SS' (contract C5); old rows are local ISO 'YYYY-MM-DDT...'. Both must count.
        for ts, st in (("2026-10-02 03:30:00", "success"), ("2026-10-03 03:30:00", "timeout"),
                       ("2026-10-04T03:30:00", "timeout"), ("2026-10-05 03:30:00", "partial"),
                       ("2026-10-06T03:30:00", "error"), ("2026-09-30 03:30:00", "timeout")):
            c.execute("INSERT INTO sleep_loop_runs (started_at, status) VALUES (?, ?)", (ts, st))
        return c

    def test_a_timed_out_night_is_scored_as_an_error(self):
        res = self.mse._score_cron_piece(self._runs_brain(), "sleep_loop_runs", "2026-10", "sleep loop")
        self.assertEqual((res["usage"], res["errors"]), (5, 4), res)

    def test_cron_health_reads_a_month_of_timeouts_as_degraded(self):
        c = self._runs_brain()
        c.execute("DELETE FROM sleep_loop_runs WHERE status != 'timeout'")
        out = self.mse._compute_cron_health(c, "2026-10")["cron_jobs"]["sleep_loop"]
        self.assertEqual((out["runs"], out["errors"], out["status"]), (2, 2, "degraded"), out)

    def test_timeout_is_a_sleep_status_only(self):
        """'timeout' joins the SLEEP failure set (C5 gives it to sleep runs alone); gap mapping's set is unchanged."""
        c = self._runs_brain()
        c.execute("INSERT INTO gap_inventory_batches (started_at, status) VALUES ('2026-10-02 01:00:00', 'timeout')")
        out = self.mse._compute_cron_health(c, "2026-10")["cron_jobs"]["gap_mapping"]
        self.assertEqual((out["runs"], out["errors"]), (1, 0), out)
        self.assertIn("timeout", self.mse.SLEEP_RUN_FAILED_STATUSES)

    # --- the pending-review reflection names nobody, and refreshes a legacy-tagged one ---------------------------
    def _surface_brain(self):
        db = self.tmp / "claude_memory.db"
        c = sqlite3.connect(str(db))
        c.executescript("""
            CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT, summary TEXT, category TEXT, importance REAL,
                                   source_project TEXT, kind TEXT, is_archived INTEGER DEFAULT 0);
            CREATE TABLE memory_tags (memory_id INTEGER, tag TEXT);
            CREATE TABLE self_eval_reports (id INTEGER PRIMARY KEY, month TEXT, created_at TEXT, status TEXT,
                                            report_memory_id INTEGER);
            INSERT INTO self_eval_reports (id, month, created_at, status) VALUES (7, '2026-10', '2026-10-01', 'pending');
        """)
        # A reflection surfaced before the rename, under the legacy tag.
        c.execute("INSERT INTO memories (id, content, summary, kind) VALUES (1, 'old surface', 'old', 'reflection')")
        c.execute("INSERT INTO memory_tags VALUES (1, ?)", (self.mse.LEGACY_PENDING_SURFACE_TAGS[0],))
        c.commit()
        c.close()
        self.mse._vl_attach = lambda *a, **k: None          # no embedder in this test
        self.mse._DU = SimpleNamespace(
            open_self_eval_report_ids=lambda _db: SimpleNamespace(ids=[7], trusted=True, notes=[]),
            derive_report_state=lambda _db: [SimpleNamespace(report_id=7, derived_status="open")])
        return db

    def _active(self, db, tag):
        c = sqlite3.connect(str(db))
        try:
            return [r for r in c.execute(
                "SELECT m.id, m.content, m.summary FROM memories m JOIN memory_tags t ON t.memory_id = m.id "
                "WHERE t.tag = ? AND m.is_archived = 0", (tag,))]
        finally:
            c.close()

    def test_the_surfaced_reflection_names_nobody(self):
        db = self._surface_brain()
        res = self.mse.surface_pending_for_owner(str(db))
        self.assertEqual(res["status"], "ok", res)
        rows = self._active(db, self.mse.PENDING_SURFACE_TAG)
        self.assertEqual(len(rows), 1)
        _id, content, summary = rows[0]
        self.assertEqual((owner_words(content), owner_words(summary)), ([], []), (content, summary))
        self.assertIn("awaiting your review", content)
        self.assertEqual(owner_words(self.mse.PENDING_SURFACE_TAG), [])

    def test_a_legacy_tagged_surface_is_refreshed_not_doubled(self):
        db = self._surface_brain()
        self.mse.surface_pending_for_owner(str(db))
        self.assertEqual(self._active(db, self.mse.LEGACY_PENDING_SURFACE_TAGS[0]), [])
        self.mse.surface_pending_for_owner(str(db))
        self.assertEqual(len(self._active(db, self.mse.PENDING_SURFACE_TAG)), 1)

    def test_the_old_function_name_still_answers(self):
        """The pre-rename name is still bound to the same function (found by identity, so it is not spelled here)."""
        fn = self.mse.surface_pending_for_owner
        aliases = [k for k, v in vars(self.mse).items() if v is fn and k != "surface_pending_for_owner"]
        self.assertEqual(len(aliases), 1, aliases)
        self.assertTrue(owner_words(aliases[0].replace("_", " ")), aliases)

    def test_help_and_stamped_text_name_nobody(self):
        src = (ROOT / "organs" / "monthly_self_eval.py").read_text(encoding="utf-8")
        for needle in ("via self-eval report #", "summarising reports awaiting", "awaiting the owner's"):
            line = next(ln for ln in src.splitlines() if needle in ln)
            self.assertEqual(owner_words(line), [], line)



# ======================================================================================================== 2: council
class TestCouncilSeat(_Tmp):

    def setUp(self):
        super().setUp()
        self.cm = _load("council_l6_under_test", "organs/council.py")
        self.db = _blank_brain(self.tmp / "council.db")

    def _legacy(self, canonical):
        return [k for k, v in self.cm.LEGACY_SPEAKER_ALIASES.items() if v == canonical]

    def test_the_seats_name_nobody(self):
        cm = self.cm
        self.assertIn(cm.SYNTHESIZER, cm.COUNCIL_MEMBERS)
        for slug, m in cm.COUNCIL_MEMBERS.items():
            self.assertEqual(owner_words(" ".join([slug, m["title"], m["skill_folder"], m["description"]])), [], slug)
        self.assertEqual(owner_words(" ".join(cm.DEFAULT_PARTICIPANTS + [cm.OWNER_SPEAKER])), [])

    def test_a_new_session_stores_no_owner_name(self):
        cm = self.cm
        sid = cm.convene_council(self.db, "Should we restructure?")["session_id"]
        cm.record_contribution(self.db, sid, "orin", "Three precedents.", 1)
        cm.record_contribution(self.db, sid, cm.SYNTHESIZER, "We converge on B.", 2)
        self.assertEqual(cm.add_owner_input(self.db, sid, "Ship it.")["status"], "ok")
        sess = cm.get_session(self.db, sid)["session"]
        stored = json.dumps([sess["participants"], [e["speaker"] for e in sess["transcript"]]])
        self.assertEqual(owner_words(stored), [], stored)
        self.assertEqual(sess["synthesis"], "We converge on B.")
        hist = cm.get_session_history(self.db)["sessions"][0]
        self.assertEqual(owner_words(" ".join(hist.keys())), [], sorted(hist))

    def test_legacy_slugs_still_resolve_and_store_canonically(self):
        cm = self.cm
        self.assertTrue(self._legacy(cm.SYNTHESIZER) and self._legacy(cm.OWNER_SPEAKER))
        sid = cm.convene_council(self.db, "t", participants=["orin"] + self._legacy(cm.SYNTHESIZER))["session_id"]
        for legacy in self._legacy(cm.SYNTHESIZER):
            self.assertEqual(cm.record_contribution(self.db, sid, legacy, "synth", 2)["status"], "ok")
        for legacy in self._legacy(cm.OWNER_SPEAKER):
            self.assertEqual(cm.record_contribution(self.db, sid, legacy, "mine", 3)["status"], "ok")
        sess = cm.get_session(self.db, sid)["session"]
        self.assertEqual(sess["participants"], ["orin", cm.SYNTHESIZER])
        self.assertEqual([e["speaker"] for e in sess["transcript"]], [cm.SYNTHESIZER, cm.OWNER_SPEAKER])
        self.assertEqual(sess["synthesis"], "synth")
        skills = self.tmp / "skills"
        for legacy in self._legacy(cm.SYNTHESIZER):
            self.assertEqual(cm.get_skill_path(legacy, base_path=str(skills)),
                             cm.get_skill_path(cm.SYNTHESIZER, base_path=str(skills)))

    def test_a_transcript_stored_under_legacy_slugs_still_reads(self):
        cm = self.cm
        entries = [{"speaker": legacy, "content": "x"} for legacy in self._legacy(cm.SYNTHESIZER)]
        prompt = cm.build_synthesis_prompt("t", entries)
        self.assertEqual(owner_words(prompt), [], prompt)
        self.assertIn("The Synthesizer", prompt)

    def test_the_old_input_function_still_answers(self):
        cm = self.cm
        olds = [k for k, v in vars(cm).items()
                if callable(v) and k.endswith("_input") and owner_words(k.replace("_", " "))]
        self.assertEqual(len(olds), 1, olds)
        sid = cm.convene_council(self.db, "t")["session_id"]
        self.assertEqual(getattr(cm, olds[0])(self.db, sid, "via the old name")["status"], "ok")
        self.assertEqual(cm.get_session(self.db, sid)["session"]["transcript"][-1]["speaker"], cm.OWNER_SPEAKER)

    def test_an_undeclared_brain_seats_the_packages_synthesizer(self):
        cm = self.cm
        self.assertEqual(cm.declared_synthesizer(), {"name": None, "skill_folder": "council-synthesizer"})
        skills = self.tmp / "skills"
        self.assertEqual(cm.get_skill_path(cm.SYNTHESIZER, base_path=str(skills)),
                         skills / "council-synthesizer" / "SKILL.md")

    def test_a_declared_persona_holds_the_seat(self):
        cm = self.cm
        folder = self.declare("Iris Vale")
        self.assertEqual(cm.declared_synthesizer(), {"name": "IRIS VALE", "skill_folder": folder})
        skills = self.tmp / "skills"
        (skills / "council-synthesizer").mkdir(parents=True)
        (skills / "council-synthesizer" / "SKILL.md").write_text("package", encoding="utf-8")
        self.assertEqual(cm.get_skill_path(cm.SYNTHESIZER, base_path=str(skills)), skills / folder / "SKILL.md")

    def test_a_declared_persona_without_a_skill_yields_to_the_package(self):
        cm = self.cm
        self.declare("Iris Vale", with_skill=False)
        skills = self.tmp / "skills"
        (skills / "council-synthesizer").mkdir(parents=True)
        (skills / "council-synthesizer" / "SKILL.md").write_text("package", encoding="utf-8")
        self.assertEqual(cm.get_skill_path(cm.SYNTHESIZER, base_path=str(skills)),
                         skills / "council-synthesizer" / "SKILL.md")


# ================================================================================================ 3: arcane_manifest
class TestArcaneManifest(_Tmp):

    def setUp(self):
        super().setUp()
        self.am = _load("am_l6_under_test", "organs/arcane_manifest.py")

    def _brain(self) -> str:
        """A brain with the council table the manifest table references (migration 018, then the organ lays 020)."""
        db = _blank_brain(self.tmp / "m.db")
        c = sqlite3.connect(db)
        c.executescript((ROOT / "migrations" / "migration_018_council.sql").read_text(encoding="utf-8"))
        c.commit()
        c.close()
        return db

    def _sections(self):
        return [{"speaker": s, "section_title": "T", "section_content": "C"} for s in self.am.MANIFEST_SPEAKERS]

    def test_the_synthesis_prompt_names_nobody(self):
        prompt = self.am._build_synthesis_prompt(self._sections(), {"quarter": "2026-Q3"})
        self.assertEqual(owner_words(prompt), [], prompt)
        self.assertIn("owner will read this", prompt)

    def test_the_synthesizer_voice_is_the_declared_persona_then_the_package(self):
        am = self.am
        self.assertEqual(am._synthesis_skill_names(), ["council-synthesizer"])
        folder = self.declare("Iris Vale")
        self.assertEqual(am._synthesis_skill_names(), [folder, "council-synthesizer"])
        self.assertEqual(owner_words(json.dumps(am.SYNTHESIS_SKILL)), [])

    def test_the_declared_persona_is_heard_in_the_synthesis(self):
        am = self.am
        folder = self.declare("Iris Vale")
        heard = []
        am._invoke_claude = lambda prompt, system_prompt_path=None, **kw: heard.append(system_prompt_path) or "prose"
        db = self._brain()
        res = am.synthesize_manifest(db, self._sections(), {"data": {"quarter": "2026-Q3"}})
        self.assertEqual(res["status"], "ok", res)
        self.assertEqual(heard, [str(self.tmp / "skills" / folder / "SKILL.md")])

    def test_the_owner_voice_and_the_history_label(self):
        am = self.am
        db = self._brain()
        am.ensure_manifest_table(db)
        c = sqlite3.connect(db)
        c.execute("INSERT INTO arcane_manifests (quarter, year, quarter_number, manifest_content, sections, status) "
                  "VALUES ('2026-Q3', 2026, 3, 'x', '[]', 'draft')")
        c.commit()
        c.close()
        res = am.add_owner_voice(db, 1, "closing words")
        self.assertEqual((res["status"], owner_words(res["action"])), ("ok", []), res)
        row = am.get_manifest_history(db)["manifests"][0]
        self.assertEqual(owner_words(" ".join(row.keys())), [], sorted(row))
        self.assertTrue(row["has_owner_voice"])

    def test_cli_help_names_nobody(self):
        src = (ROOT / "organs" / "arcane_manifest.py").read_text(encoding="utf-8")
        helps = re.findall(r'help="([^"]*)"', src)
        self.assertTrue(helps)
        self.assertEqual([h for h in helps if owner_words(h)], [])


# =========================================================================================== 4: pre_cognitive_recall
class TestPreCognitiveDefaults(_Tmp):

    def setUp(self):
        super().setUp()
        self.pcr = _load("pcr_l6_under_test", "organs/pre_cognitive_recall.py")
        self.db = self.tmp / "brain" / "claude_memory.db"
        self.conn = sqlite3.connect(str(self.db))
        self.addCleanup(self.conn.close)

    def _owner_file(self, data):
        f = self.db.parent / self.pcr.PRE_COGNITIVE_DEFAULTS_BASENAME
        f.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return f

    def test_the_package_defaults_name_nobody(self):
        p = self.pcr
        text = json.dumps([p.TIER_1_QUERIES, p.TIER_2_QUERIES, sorted(p.DEFAULT_TIER_WEIGHTS),
                           p.TIME_PATTERNS, {str(k): v for k, v in p.DAY_PATTERNS.items()}])
        self.assertEqual(owner_words(text), [], text)

    def test_an_absent_file_is_the_package_defaults(self):
        d = self.pcr.recall_defaults(self.conn)
        self.assertEqual((d["owner_file"], d["owner_error"], d["rejected"]), (None, None, []))
        self.assertEqual(d["tier_1_queries"], self.pcr.TIER_1_QUERIES)
        self.assertEqual(d["tier_weights"], self.pcr.DEFAULT_TIER_WEIGHTS)

    def test_the_owner_file_overlays_lists_replace_maps_merge(self):
        self._owner_file({"_comment": "mine", "tier_1_queries": ["my persona", "my week"],
                          "tier_weights": {"my-tag": 0.9, "architecture": 0.4},
                          "time_patterns": {"morning": "my mornings"}, "day_patterns": {"1": "my tuesdays"}})
        d = self.pcr.recall_defaults(self.conn)
        self.assertEqual(d["tier_1_queries"], ["my persona", "my week"])
        self.assertEqual(d["tier_2_queries"], self.pcr.TIER_2_QUERIES)
        self.assertEqual((d["tier_weights"]["my-tag"], d["tier_weights"]["architecture"]), (0.9, 0.4))
        self.assertEqual(d["tier_weights"]["ace-loop"], self.pcr.DEFAULT_TIER_WEIGHTS["ace-loop"])
        self.assertEqual((d["time_patterns"]["morning"], d["day_patterns"][1]), ("my mornings", "my tuesdays"))
        self.assertEqual(d["day_patterns"][0], self.pcr.DAY_PATTERNS[0])
        self.assertEqual((d["owner_error"], d["rejected"]), (None, []))

    def test_bad_values_are_named_and_the_rest_applies(self):
        self._owner_file({"tier_1_queries": ["ok", 3, ""], "tier_weights": {"a": True, "b": "1", "c": 2},
                          "time_patterns": {"noon": "x", "night": 4}, "day_patterns": {"7": "x", "x": "y", "2": "z"},
                          "bogus": 1})
        d = self.pcr.recall_defaults(self.conn)
        self.assertEqual(d["tier_1_queries"], ["ok"])
        self.assertEqual(d["tier_weights"]["c"], 2.0)
        self.assertNotIn("a", d["tier_weights"])
        self.assertEqual(d["day_patterns"][2], "z")
        self.assertEqual(len(d["rejected"]), 9, d["rejected"])

    def test_a_malformed_file_is_the_package_defaults_and_says_why(self):
        self._owner_file("{not json")
        d = self.pcr.recall_defaults(self.conn)
        self.assertTrue(d["owner_error"])
        self.assertEqual(d["tier_1_queries"], self.pcr.TIER_1_QUERIES)

    def test_anticipatory_recall_runs_the_owners_queries_and_weights(self):
        self._owner_file({"tier_1_queries": ["owner query one"], "tier_weights": {"favoured": 5.0}})
        asked = []

        def fake_hybrid(conn, query, query_vec, top_k, mode):
            asked.append(query)
            if query:
                return []
            return [{"id": 1, "tags": ["plain"], "score": 0.9, "importance": 0.5},
                    {"id": 2, "tags": ["favoured"], "score": 0.5, "importance": 0.5}]

        res = self.pcr.anticipatory_recall(self.conn, b"v", lambda t, p: b"v", recall_hybrid_fn=fake_hybrid,
                                           signals={})
        self.assertEqual(asked[0], "owner query one")
        self.assertEqual(res["tiers"][3], [2, 1])
        self.assertEqual(res["owner_defaults_file"], str(self.db.parent / self.pcr.PRE_COGNITIVE_DEFAULTS_BASENAME))

    def test_the_temporal_hint_reads_the_owner_week(self):
        from datetime import datetime
        os.environ["WIZARD_LIBRARY_DB"] = str(self.db)
        self._owner_file({"day_patterns": {"0": "owner mondays"}})
        self.assertIn("owner mondays", self.pcr._temporal_hint(datetime(2026, 5, 25, 9, 0, 0)))


if __name__ == "__main__":
    unittest.main()
