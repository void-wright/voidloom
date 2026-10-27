"""M15 (model-choice round 1): the seed pack loads as a PER-MEMORY ANCHOR DELTA.

Until seed pack 1.5.0, `claude_memory_lite.py load-seed` was all-or-nothing: one memory carrying the pack's common tag
made the whole load a "skipped", so a brain seeded once -- every upgraded brain -- never received an entry added to the
pack later (measured on the base: the 1.5.0 pack onto a 1.4.0-seeded brain -> "skipped", 0 of 8 new entries).

These tests bind the delta on SCRATCH brains (a temp dir; the brain resolvers are removed from the child's
environment, so a WIZARD_LIBRARY_DB exported by the operator can never be the brain written):
  * the shipped pack: every entry carries exactly ONE unique `anchor:seed_<name>` tag, the common tag first, a
    `supersedes` list only of 12-hex revisions, and the kind the kind taxonomy derives (self_check's check 5);
  * an already-seeded brain (an anchor-less older pack) receives EXACTLY the new and changed entries: unchanged rows
    are adopted (tags added, no new row), a changed entry's older row is archived + tagged seed-superseded (never
    deleted), and a second load adds zero;
  * a fresh brain receives every entry; `--plan` reports the same delta and writes nothing;
  * an entry the owner archived is not resurrected, and a revision the pack does not know is left as found.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
LITE = ROOT / "organs" / "claude_memory_lite.py"
PACK = ROOT / "seed" / "seed_pack.json"
BLANK = ROOT / "seed" / "claude_memory_blank.db"
MIGRATOR = ROOT / "organs" / "schema_migrator.py"
_RESOLVERS = ("WIZARD_LIBRARY_DB", "WIZARD_LIBRARY_HOME", "CLAUDE_MEMORY_DB")


def _rev(summary, content):
    return hashlib.sha256(((summary or "") + "\x00" + (content or "")).encode("utf-8")).hexdigest()[:12]


def _envelope(text):
    lines = [ln for ln in text.strip().splitlines() if ln.startswith("{")]
    return json.loads(lines[-1]) if lines else {}


class _Brains(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for p in (LITE, PACK, BLANK, MIGRATOR):
            if not p.is_file():
                raise unittest.SkipTest("the seed surface is incomplete in this tree: %s" % p)
        cls.tmp = Path(tempfile.mkdtemp(prefix="vl_seed_delta_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def brain(self, name):
        db = self.tmp / (name + ".db")
        shutil.copy2(BLANK, db)
        r = subprocess.run([sys.executable, "-B", str(MIGRATOR), "apply", "--db", str(db), "--migrations",
                            str(ROOT / "migrations"), "--json"], capture_output=True, text=True, timeout=300,
                           env=self.env(db))
        self.assertEqual(r.returncode, 0, r.stdout[-400:] + r.stderr[-400:])
        return db

    def env(self, db):
        e = {k: v for k, v in os.environ.items() if k not in _RESOLVERS}
        e["WIZARD_LIBRARY_DB"] = str(db)
        e["PYTHONDONTWRITEBYTECODE"] = "1"
        return e

    def load(self, db, pack, *extra):
        r = subprocess.run([sys.executable, "-B", str(LITE), "load-seed", "--file", str(pack)] + list(extra),
                           capture_output=True, text=True, timeout=300, env=self.env(db))
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        return _envelope(r.stdout)

    def counts(self, db):
        c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        try:
            live = c.execute("SELECT COUNT(*) FROM memories WHERE is_archived=0").fetchone()[0]
            arch = c.execute("SELECT COUNT(*) FROM memories WHERE is_archived=1").fetchone()[0]
            sup = c.execute("SELECT COUNT(*) FROM memory_tags WHERE tag='seed-superseded'").fetchone()[0]
            live_anchors = c.execute(
                "SELECT COUNT(DISTINCT tag) FROM memory_tags WHERE tag LIKE 'anchor:seed_%' AND memory_id IN "
                "(SELECT id FROM memories WHERE is_archived=0)").fetchone()[0]
        finally:
            c.close()
        return {"live": live, "archived": arch, "superseded": sup, "live_anchors": live_anchors}

    def write_pack(self, name, entries, version):
        p = self.tmp / (name + ".json")
        p.write_text(json.dumps({"seed_pack_version": version, "common_tag": "seed-pack-agnostic",
                                 "memories": entries}), encoding="utf-8")
        return p


def _entry(anchor, words, kind="procedural", category="workflow_pattern", supersedes=None):
    tags = "seed-pack-agnostic" + (",anchor:seed_" + anchor if anchor else "") + ",synthetic"
    e = {"summary": "SYNTHETIC %s — %s" % (anchor or "legacy", words), "content": "content of %s: %s" % (anchor, words),
         "category": category, "kind": kind, "importance": 0.8, "tags": tags}
    if supersedes:
        e["supersedes"] = supersedes
    return e


class TestTheShippedPack(unittest.TestCase):

    def setUp(self):
        if not PACK.is_file():
            self.skipTest("seed_pack.json is not in this tree")
        self.data = json.loads(PACK.read_text(encoding="utf-8"))
        self.mems = self.data["memories"]

    def test_every_entry_carries_one_unique_anchor_after_the_common_tag(self):
        self.assertGreater(len(self.mems), 0, "the pack is empty -- every assertion below would be vacuous")
        anchors = []
        for m in self.mems:
            tags = [t.strip() for t in m["tags"].split(",")]
            self.assertEqual(tags[0], "seed-pack-agnostic", m["summary"][:60])
            a = [t for t in tags if t.startswith("anchor:seed_")]
            self.assertEqual(len(a), 1, "exactly one anchor: %s" % m["summary"][:60])
            self.assertRegex(a[0], r"^anchor:seed_[a-z0-9_]+$")
            anchors.append(a[0])
        self.assertEqual(len(anchors), len(set(anchors)), "an anchor is the per-memory key: it must be unique")

    def test_supersedes_lists_only_revisions_and_never_the_entrys_own(self):
        for m in self.mems:
            sup = m.get("supersedes", [])
            self.assertIsInstance(sup, list)
            for r in sup:
                self.assertRegex(r, r"^[0-9a-f]{12}$")
            self.assertNotIn(_rev(m["summary"], m["content"]), sup,
                             "an entry that supersedes its own words would archive itself on every load")

    def test_the_loaders_revision_is_the_one_this_test_computes(self):
        sys.path.insert(0, str(ROOT / "organs"))
        try:
            import importlib
            lite = importlib.import_module("claude_memory_lite")
            self.assertEqual(lite.seed_revision("a", "b"), _rev("a", "b"))
        finally:
            sys.path.pop(0)

    def test_every_declared_kind_is_the_one_the_taxonomy_derives(self):
        sys.path.insert(0, str(ROOT / "organs"))
        try:
            import kind_taxonomy
        finally:
            sys.path.pop(0)
        bad = []
        for m in self.mems:
            tags = [t.strip() for t in m["tags"].split(",") if t.strip()]
            kind, _c = kind_taxonomy.classify_memory(m["summary"], m["content"], m["category"], tags)
            if kind != m["kind"]:
                bad.append((m["summary"][:40], kind, m["kind"]))
        self.assertEqual(bad, [], "self_check's check 5 would fail on these seeds")

    def test_the_pack_teaches_no_pre_model_choice_token_step(self):
        """The class the four revisions fixed: telling a brain to paste a Claude token into the env file."""
        stale = re.compile(r"put it in your env file|place in your own env file|--with-crons install flag, and each "
                           r"one needs YOUR OWN|mint YOUR OWN token with `claude setup-token` if you want")
        hits = [m["summary"][:50] for m in self.mems if stale.search(m["summary"] + " " + m["content"])]
        self.assertEqual(hits, [])


class TestTheDeltaLoad(_Brains):

    def test_an_already_seeded_brain_receives_exactly_the_new_and_changed_anchors(self):
        # v1: three entries from an OLDER pack -- no anchors (the 1.4.0 shape)
        v1 = [_entry(None, "stays the same"), _entry(None, "old words of b"), _entry(None, "pruned by the owner")]
        v1[0]["summary"], v1[0]["content"] = "SYNTHETIC A — stays", "content A"
        v1[1]["summary"], v1[1]["content"] = "SYNTHETIC B — old", "content B old"
        v1[2]["summary"], v1[2]["content"] = "SYNTHETIC C — pruned", "content C"
        old = self.write_pack("v1", v1, "1.0.0")
        db = self.brain("seeded")
        self.assertEqual(self.load(db, old)["loaded"], 3)
        c = sqlite3.connect(str(db))
        c.execute("UPDATE memories SET is_archived=1 WHERE summary='SYNTHETIC C — pruned'")
        c.commit()
        c.close()
        # v2: A unchanged (now anchored), B revised, C unchanged but pruned, D and E new
        a = dict(v1[0], tags="seed-pack-agnostic,anchor:seed_a,synthetic")
        b = {"summary": "SYNTHETIC B — new", "content": "content B new", "category": "workflow_pattern",
             "kind": "procedural", "importance": 0.8, "tags": "seed-pack-agnostic,anchor:seed_b",
             "supersedes": [_rev("SYNTHETIC B — old", "content B old")]}
        cc = dict(v1[2], tags="seed-pack-agnostic,anchor:seed_c")
        new = self.write_pack("v2", [a, b, cc, _entry("d", "brand new"), _entry("e", "also new")], "2.0.0")

        plan = self.load(db, new, "--plan")
        self.assertEqual(plan["mode"], "plan")
        self.assertEqual(plan["loaded"], 0)
        self.assertEqual(sorted(plan["new"]), ["anchor:seed_d", "anchor:seed_e"])
        self.assertEqual(plan["changed"], ["anchor:seed_b"])
        self.assertEqual(plan["adopted"], 2)          # A (live) and C (archived by the owner: kept archived)
        self.assertEqual(self.counts(db), {"live": 2, "archived": 1, "superseded": 0, "live_anchors": 0},
                         "--plan wrote something")

        first = self.load(db, new)
        self.assertEqual(first["mode"], "anchor-delta")
        self.assertEqual(first["loaded"], 3)
        self.assertEqual(sorted(first["new"]), ["anchor:seed_d", "anchor:seed_e"])
        self.assertEqual(first["changed"], ["anchor:seed_b"])
        self.assertEqual(len(first["superseded_ids"]), 1)
        # live: A, B-new, D, E ; archived: C (the owner's), B-old (superseded, never deleted)
        self.assertEqual(self.counts(db), {"live": 4, "archived": 2, "superseded": 1, "live_anchors": 4})

        second = self.load(db, new)
        self.assertEqual(second["loaded"], 0, "a second load must add zero")
        self.assertEqual((second["new"], second["changed"], second["adopted"]), ([], [], 0))
        self.assertEqual(second["present"], 5)
        self.assertEqual(self.counts(db), {"live": 4, "archived": 2, "superseded": 1, "live_anchors": 4})

    def test_a_fresh_brain_receives_every_entry_of_the_shipped_pack_and_a_second_load_adds_zero(self):
        n = len(json.loads(PACK.read_text(encoding="utf-8"))["memories"])
        db = self.brain("fresh")
        first = self.load(db, PACK)
        self.assertEqual((first["mode"], first["loaded"], len(first["new"])), ("anchor-delta", n, n))
        again = self.load(db, PACK)
        self.assertEqual((again["loaded"], again["present"]), (0, n))
        self.assertEqual(self.counts(db), {"live": n, "archived": 0, "superseded": 0, "live_anchors": n})

    def test_a_revision_the_pack_does_not_know_is_left_as_found(self):
        db = self.brain("newer")
        newer = self.write_pack("newer", [_entry("x", "a NEWER revision")], "9.0.0")
        self.assertEqual(self.load(db, newer)["loaded"], 1)
        older = self.write_pack("older", [_entry("x", "an older revision")], "8.0.0")
        r = self.load(db, older)
        self.assertEqual((r["loaded"], r["left_as_found"]), (0, ["anchor:seed_x"]),
                         "an older pack must never overwrite a newer revision")

    def test_a_hand_stored_draft_under_the_anchor_is_superseded_not_left(self):
        """A brain that stored a DRAFT of an entry by hand (the anchor + the common tag, no seed-rev: no pack load wrote
        it) receives the pack's words: the draft is archived + tagged seed-superseded, never left in its place.
        Measured on a backup() copy of the reference brain: 7 such drafts, 'left as found' before this rule."""
        db = self.brain("drafted")
        c = sqlite3.connect(str(db))
        cur = c.execute("INSERT INTO memories (content, summary, category, importance, is_archived) VALUES "
                        "('draft words', 'SYNTHETIC d — a draft', 'workflow_pattern', 0.8, 0)")
        for t in ("seed-pack-agnostic", "anchor:seed_d"):
            c.execute("INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)", (cur.lastrowid, t))
        c.commit()
        c.close()
        pack = self.write_pack("with_d", [_entry("d", "the pack's words")], "2.0.0")
        first = self.load(db, pack)
        self.assertEqual((first["loaded"], first["changed"], first["left_as_found"]), (1, ["anchor:seed_d"], []))
        self.assertEqual(self.counts(db), {"live": 1, "archived": 1, "superseded": 1, "live_anchors": 1})
        self.assertEqual(self.load(db, pack)["loaded"], 0)

    def test_an_anchorless_pack_keeps_the_all_or_nothing_guard(self):
        db = self.brain("legacy")
        old = self.write_pack("legacy", [_entry(None, "one"), _entry(None, "two")], "1.0.0")
        self.assertEqual(self.load(db, old)["loaded"], 2)
        self.assertEqual(self.load(db, old)["status"], "skipped")



class TestNeverADuplicateR3_06(_Brains):
    """R3-06 (Round 3 reflection): the delta saw only rows carrying the pack's common tag, so a memory whose tag rows were
    lost -- or the same words stored by hand -- read as 'new' and was inserted AGAIN. Now an entry whose words (this
    revision or an older one it supersedes) or whose anchor a memory WITHOUT the common tag holds is 'held-untagged':
    nothing is inserted, re-tagged or archived for it (a row of unknown provenance is the owner's); --plan says so."""

    def _row(self, db, summary, content, tags=(), archived=0):
        c = sqlite3.connect(str(db))
        cur = c.execute("INSERT INTO memories (content, summary, category, importance, is_archived) VALUES "
                        "(?, ?, 'workflow_pattern', 0.8, ?)", (content, summary, archived))
        for t in tags:
            c.execute("INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)", (cur.lastrowid, t))
        c.commit()
        c.close()
        return cur.lastrowid

    def _live_with(self, db, summary, content):
        c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        try:
            return [r[0] for r in c.execute("SELECT id FROM memories WHERE is_archived=0 AND summary=? AND content=?",
                                            (summary, content))]
        finally:
            c.close()

    def _held(self, db, pack, row_id, anchor):
        plan = self.load(db, pack, "--plan")
        self.assertEqual((plan["new"], plan["changed"], plan["held_untagged"]), ([], [], [anchor]), plan)
        before = self.counts(db)
        for _ in range(2):
            r = self.load(db, pack)
            self.assertEqual((r["loaded"], r["held_untagged_ids"]), (0, [row_id]), r)
            self.assertEqual(self.counts(db), before, "load-seed wrote over a memory it does not own")
        return before

    def test_an_untagged_copy_of_the_exact_words_is_never_inserted_again(self):
        db = self.brain("u_exact")
        e = _entry("u", "the pack's words")
        rid = self._row(db, e["summary"], e["content"])                         # tags lost, or stored by hand
        self._held(db, self.write_pack("u1", [e], "2.0.0"), rid, "anchor:seed_u")
        self.assertEqual(len(self._live_with(db, e["summary"], e["content"])), 1)

    def test_an_untagged_older_revision_blocks_the_new_entry_never_two_live_versions(self):
        db = self.brain("u_older")
        rid = self._row(db, "SYNTHETIC v — the OLD words", "content of v: old")
        e = _entry("v", "the new words", supersedes=[_rev("SYNTHETIC v — the OLD words", "content of v: old")])
        self._held(db, self.write_pack("v1", [e], "2.0.0"), rid, "anchor:seed_v")
        self.assertEqual(self._live_with(db, e["summary"], e["content"]), [], "a second, newer version went live")

    def test_an_untagged_row_carrying_the_anchor_blocks_the_new_entry(self):
        db = self.brain("u_anchor")
        rid = self._row(db, "SYNTHETIC w — a draft", "draft words", tags=("anchor:seed_w",))
        self._held(db, self.write_pack("w1", [_entry("w", "the pack's words")], "2.0.0"), rid, "anchor:seed_w")

    def test_an_archived_untagged_copy_is_never_resurrected(self):
        db = self.brain("u_arch")
        e = _entry("z", "pruned words")
        rid = self._row(db, e["summary"], e["content"], archived=1)
        self._held(db, self.write_pack("z1", [e], "2.0.0"), rid, "anchor:seed_z")
        self.assertEqual(self._live_with(db, e["summary"], e["content"]), [])

    def test_a_changed_entry_whose_new_words_are_held_untagged_keeps_the_old_row_live(self):
        """The anchored path: a hand draft under the anchor (common tag, no seed-rev) WOULD be superseded -- but a memory
        without the tags already holds the pack's new words, so superseding would leave two live copies of them."""
        db = self.brain("u_changed")
        draft = self._row(db, "SYNTHETIC d — a draft", "draft words", tags=("seed-pack-agnostic", "anchor:seed_d"))
        e = _entry("d", "the pack's words")
        rid = self._row(db, e["summary"], e["content"])
        self._held(db, self.write_pack("d1", [e], "2.0.0"), rid, "anchor:seed_d")
        self.assertEqual(self._live_with(db, "SYNTHETIC d — a draft", "draft words"), [draft])

    def test_an_unanchored_older_row_and_an_untagged_copy_of_the_new_words(self):
        """The un-anchored path: an older pack's row (common tag, no anchor) holds a revision the entry supersedes, and a
        memory without the tags holds the entry's new words: nothing is archived or inserted."""
        db = self.brain("u_unanch")
        old = self._row(db, "SYNTHETIC y — old", "content of y: old", tags=("seed-pack-agnostic",))
        e = _entry("y", "new", supersedes=[_rev("SYNTHETIC y — old", "content of y: old")])
        rid = self._row(db, e["summary"], e["content"])
        self._held(db, self.write_pack("y1", [e], "2.0.0"), rid, "anchor:seed_y")
        self.assertEqual(self._live_with(db, "SYNTHETIC y — old", "content of y: old"), [old])

    def test_control_without_an_untagged_copy_the_changed_entry_still_supersedes(self):
        db = self.brain("u_control")
        self._row(db, "SYNTHETIC y — old", "content of y: old", tags=("seed-pack-agnostic",))
        e = _entry("y", "new", supersedes=[_rev("SYNTHETIC y — old", "content of y: old")])
        r = self.load(db, self.write_pack("y2", [e], "2.0.0"))
        self.assertEqual((r["loaded"], r["changed"], r.get("held_untagged", [])), (1, ["anchor:seed_y"], []), r)
        self.assertEqual(self.counts(db), {"live": 1, "archived": 1, "superseded": 1, "live_anchors": 1})
        self.assertEqual(self.load(db, self.write_pack("y2", [e], "2.0.0"))["loaded"], 0)


if __name__ == "__main__":
    unittest.main()
