"""The seed's three guards: the doctrine set names real pack entries, the denylist scrubs the pack before
any write, and every row the loader writes is flagged seed=1 + origin as COLUMNS (migration_044).

The anchor delta (test_seed_anchor_delta.py) decides WHICH entries a load writes; these tests bind HOW:
  * the doctrine set: 25 user-phrased queries, each naming exactly one pack entry by its anchor, with the origin
    that entry has under the shipped pack version -- a pack change that moves an entry fails here until the set is
    re-keyed -- and no query copies its entry's summary (a paraphrase, or the test proves nothing);
  * the denylist: the shipped pack scrubs to 0 hits (control); a sample of every pattern's class, planted in a copy
    of the pack, is caught (saboteur), and a refusal never prints the matched text; through the verb a hit is rc 4
    with nothing written;
  * the writer: a fresh brain's load flags every row seed=1 with the origin its words derive, a second load adds
    zero; a brain loaded BEFORE the columns existed has its present rows flagged by the next load, words untouched;
    a load whose writer refuses (--embed where no embedder can load) writes nothing at all.
Scratch brains only: the brain resolvers are removed from every child's environment.
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
DENY = ROOT / "seed" / "seed_denylist.json"
DOCTRINE = ROOT / "seed" / "seed_doctrine_queries.json"
BLANK = ROOT / "seed" / "claude_memory_blank.db"
MIGRATOR = ROOT / "organs" / "schema_migrator.py"
_RESOLVERS = ("WIZARD_LIBRARY_DB", "WIZARD_LIBRARY_HOME", "CLAUDE_MEMORY_DB")
sys.path.insert(0, str(ROOT / "organs"))


def _origin(version, content):
    return "seed_pack:%s#%s" % (version, hashlib.sha256(content.encode("utf-8")).hexdigest()[:16])


def _anchors(m):
    return [t.strip() for t in (m.get("tags") or "").split(",") if t.strip().startswith("anchor:seed_")]


def _envelope(text):
    lines = [ln for ln in text.strip().splitlines() if ln.startswith("{")]
    return json.loads(lines[-1]) if lines else {}


# Planted home-directory samples are BUILT, never written as literals: a test file that spells a home path is what
# test_suite_is_portable refuses, and these are placeholders the denylist must catch, not paths anything reads.
_HOME = "/" + "home/someone/"
_USERS = "/" + "Users/yourname/"
# one planted sample per shipped pattern, in pattern order (a pattern added without a sample fails the count check)
_SAMPLES = [_HOME + "notes", _USERS + "notes", "C:\\Users\\user\\notes", "/mnt/c/" + "Users/someone/notes",
            "example-client corp", "Jane Q Example", "build01.internal", "sk-" + "a" * 24, "ghp_" + "b" * 36,
            "AKIA" + "C" * 16, "-----BEGIN RSA PRIVATE KEY-----", "00:1a:2b:3c:4d:5e"]


class DoctrineSet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = json.loads(PACK.read_text(encoding="utf-8"))
        cls.ds = json.loads(DOCTRINE.read_text(encoding="utf-8"))

    def test_the_set_is_keyed_to_the_shipped_pack_version(self):
        self.assertEqual(self.ds["seed_pack_version"], self.pack["seed_pack_version"])

    def test_twenty_five_queries_numbered_once_and_a_bar_within_them(self):
        ns = [q["n"] for q in self.ds["queries"]]
        self.assertEqual(len(ns), 25)
        self.assertEqual(sorted(ns), list(range(1, 26)))
        self.assertLessEqual(self.ds["bar"], 25)
        self.assertIn(self.ds["primary_mode"], self.ds["modes"])

    def test_every_query_names_exactly_one_pack_entry_by_anchor_and_its_origin(self):
        by_anchor = {}
        for i, m in enumerate(self.pack["memories"]):
            for a in _anchors(m):
                by_anchor.setdefault(a, []).append(i)
        for q in self.ds["queries"]:
            hits = by_anchor.get(q["expect_anchor"], [])
            self.assertEqual(len(hits), 1, (q["n"], q["expect_anchor"]))
            m = self.pack["memories"][hits[0]]
            self.assertEqual(q["expect_index"], hits[0], q["n"])
            self.assertEqual(q["expect_origin"], _origin(self.pack["seed_pack_version"], m["content"]), q["n"])

    def test_no_query_copies_its_entrys_summary(self):
        idx = {a: m for m in self.pack["memories"] for a in _anchors(m)}
        for q in self.ds["queries"]:
            summ = (idx[q["expect_anchor"]].get("summary") or "").lower()
            self.assertNotIn(q["query"].lower(), summ, q["n"])
            self.assertNotIn(summ[:40], q["query"].lower(), q["n"])


class Denylist(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import seed_loader
        cls.sl = seed_loader
        cls.tmp = Path(tempfile.mkdtemp(prefix="vl_seed_deny_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_control_the_shipped_pack_scrubs_to_zero(self):
        sc = self.sl.scrub(PACK, DENY)
        self.assertEqual(sc["hits"], 0, sc["hit_list"])
        self.assertGreater(sc["bytes_scanned"], 0)
        self.assertEqual(sc["patterns"], len(_SAMPLES))

    def test_saboteur_every_pattern_catches_its_planted_sample_and_never_prints_it(self):
        dl = json.loads(DENY.read_text(encoding="utf-8"))["patterns"]
        text = PACK.read_text(encoding="utf-8")
        for k, sample in enumerate(_SAMPLES):
            p = self.tmp / ("pack_%d.json" % k)
            p.write_text(text.replace('"common_tag"', '"x_planted": %s, "common_tag"' % json.dumps(sample), 1),
                         encoding="utf-8")
            sc = self.sl.scrub(p, DENY)
            self.assertIn(k, [h["pattern_index"] for h in sc["hit_list"]], (k, dl[k]["class"]))
            with self.assertRaises(self.sl.SeedLoadError) as cm:
                self.sl.refuse_on_hits(p, DENY)
            self.assertEqual(cm.exception.code, "SEED_DENYLIST_HIT")
            self.assertNotIn(sample, json.dumps(cm.exception.as_dict()))


class _Brains(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for p in (LITE, PACK, BLANK, MIGRATOR):
            if not p.is_file():
                raise unittest.SkipTest("the seed surface is incomplete in this tree: %s" % p)
        cls.tmp = Path(tempfile.mkdtemp(prefix="vl_seed_cols_"))
        cls.pack = json.loads(PACK.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def env(self, db, **extra):
        e = {k: v for k, v in os.environ.items() if k not in _RESOLVERS}
        e.update({"WIZARD_LIBRARY_DB": str(db), "PYTHONDONTWRITEBYTECODE": "1"}, **extra)
        return e

    def brain(self, name, before_044=False):
        db = self.tmp / (name + ".db")
        shutil.copy2(BLANK, db)
        self.migrate(db)
        if before_044:
            # a brain from before migration_044: its index, its two columns and its record gone, so the next
            # migrator run applies 044 for real (sqlite_master is the migrator's authority)
            c = sqlite3.connect(str(db))
            try:
                c.execute("DROP INDEX IF EXISTS idx_memories_seed_origin")
                c.execute("ALTER TABLE memories DROP COLUMN origin")
                c.execute("ALTER TABLE memories DROP COLUMN seed")
                c.execute("DELETE FROM schema_migrations WHERE name LIKE 'migration_044%'")
                c.commit()
            finally:
                c.close()
        return db

    def migrate(self, db, mig=ROOT / "migrations"):  # noqa: B008
        r = subprocess.run([sys.executable, "-B", str(MIGRATOR), "apply", "--db", str(db), "--migrations", str(mig),
                            "--json"], capture_output=True, text=True, timeout=300, env=self.env(db))
        self.assertEqual(r.returncode, 0, r.stdout[-400:] + r.stderr[-400:])

    def run_lite(self, db, *argv, **env):
        return subprocess.run([sys.executable, "-B", str(LITE)] + list(argv), capture_output=True, text=True,
                              timeout=300, env=self.env(db, **env))

    def rows(self, db):
        c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        try:
            cols = {r[1] for r in c.execute("PRAGMA table_info(memories)")}
            if "seed" not in cols:
                return [(i, None, None, ct) for i, ct in c.execute("SELECT id, content FROM memories ORDER BY id")]
            return c.execute("SELECT id, seed, origin, content FROM memories ORDER BY id").fetchall()
        finally:
            c.close()


class WriterFlagsColumns(_Brains):
    def test_control_a_fresh_load_flags_every_row_with_the_origin_its_words_derive(self):
        db = self.brain("fresh")
        r = self.run_lite(db, "load-seed", "--file", str(PACK))
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        env = _envelope(r.stdout)
        self.assertIn("memory_write.write_memory", env["writer"])
        self.assertEqual(env["scrub"]["hits"], 0)
        rows = self.rows(db)
        self.assertEqual(len(rows), len(self.pack["memories"]))
        ver = self.pack["seed_pack_version"]
        self.assertEqual(sum(1 for _i, s, o, ct in rows if s == 1 and o == _origin(ver, ct)), len(rows))
        again = self.run_lite(db, "load-seed", "--file", str(PACK))
        self.assertEqual(again.returncode, 0)
        self.assertEqual(_envelope(again.stdout)["loaded"], 0)
        self.assertEqual(len(self.rows(db)), len(rows))
        shape = _envelope(self.run_lite(db, "seed-shape").stdout)
        self.assertEqual((shape["seed_rows"], shape["duplicate_origins_active"], shape["seed_rows_without_origin"],
                          shape["unflagged_pack_tagged_rows"]), (len(rows), 0, 0, 0))

    def test_a_brain_loaded_before_the_columns_existed_has_its_present_rows_flagged_next_load(self):
        db = self.brain("pre044", before_044=True)
        self.assertEqual(self.run_lite(db, "load-seed", "--file", str(PACK)).returncode, 0)
        before = self.rows(db)
        self.assertTrue(before and all(s is None for _i, s, _o, _c in before))
        self.migrate(db)
        self.assertTrue(all(s == 0 for _i, s, _o, _c in self.rows(db)), "044 must flag nothing by itself")
        r = self.run_lite(db, "load-seed", "--file", str(PACK))
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        env = _envelope(r.stdout)
        self.assertEqual((env["loaded"], env["seed_columns_set_on_existing_rows"]), (0, len(before)))
        after = self.rows(db)
        self.assertEqual([(i, ct) for i, _s, _o, ct in after], [(i, ct) for i, _s, _o, ct in before])
        self.assertTrue(all(s == 1 for _i, s, _o, _c in after))

    def test_saboteur_a_denylist_hit_through_the_verb_is_rc4_and_writes_nothing(self):
        db = self.brain("deny")
        bad = self.tmp / "bad_pack.json"
        bad.write_text(PACK.read_text(encoding="utf-8").replace('"common_tag"', '"x": "%sx", "common_tag"' % _HOME, 1),
                       encoding="utf-8")
        r = self.run_lite(db, "load-seed", "--file", str(bad))
        self.assertEqual(r.returncode, 4, r.stdout[-400:])
        env = _envelope(r.stdout)
        self.assertEqual(env["error"], "SEED_DENYLIST_HIT")
        self.assertNotIn("/home/someone", r.stdout + r.stderr)
        self.assertEqual(self.rows(db), [])

    def test_a_load_whose_writer_refuses_writes_nothing_at_all(self):
        db = self.brain("embed_refused")
        empty = self.tmp / "empty_home"
        empty.mkdir(exist_ok=True)
        r = self.run_lite(db, "load-seed", "--file", str(PACK), "--embed", HOME=str(empty), HF_HOME=str(empty / "hf"),
                          HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", WIZARD_ACCELERATOR="cpu")
        self.assertEqual(r.returncode, 3, r.stdout[-600:] + r.stderr[-600:])
        self.assertIn("nothing was written", _envelope(r.stdout).get("note", ""))
        self.assertEqual(self.rows(db), [])


if __name__ == "__main__":
    unittest.main()
