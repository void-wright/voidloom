"""`load-seed --embed` is ONE transaction: a refusal or a kill at entry k leaves the brain exactly as it was.

THE DEFECT THIS BINDS. The rows are written by memory_write.write_memory, which takes a SAVEPOINT. A SAVEPOINT on a
connection with no open transaction BEGINS one, and its outermost RELEASE COMMITS. The loader opened no transaction
before its loop, so every row committed as it landed: an encoder refusal at entry 33 left 32 rows on disk under an
envelope that said "nothing was written", and a kill at entry 33 left the same 32.

THE CLAIM UNDER TEST
  * an encoder that refuses at entry k: rc 3, the brain holds exactly the rows it held before, and the envelope's
    own counts (memories_before == memories_after) say so; a re-run converges to one row per pack entry with no
    duplicate origin, and a third run calls the encoder zero times;
  * a SIGKILL at entry k: the same (nothing committed; the journal rolls the open transaction back);
  * the anchor-less branch (--force) refused at entry k leaves the loaded brain as it was;
  * the mechanism itself, without the product: SAVEPOINT/RELEASE frames with no BEGIN survive a rollback, and with
    BEGIN IMMEDIATE first they do not -- so a Python whose sqlite3 behaves otherwise fails here, not in a user's brain.
No model is loaded: the child replaces memory_write.default_encoder with a stand-in that counts its calls.
Scratch brains only: the brain resolvers are removed from every child's environment.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
LITE = ROOT / "organs" / "claude_memory_lite.py"
PACK = ROOT / "seed" / "seed_pack.json"
BLANK = ROOT / "seed" / "claude_memory_blank.db"
MIGRATOR = ROOT / "organs" / "schema_migrator.py"
_RESOLVERS = ("WIZARD_LIBRARY_DB", "WIZARD_LIBRARY_HOME", "CLAUDE_MEMORY_DB")
REFUSE_AT = 33

# The child: a locked nomic brain (vector table, metadata, install lock, the process register), then the verb with
# a stand-in encoder that refuses (returns None) or kills the process at call K.
CHILD = textwrap.dedent('''
    import hashlib, os, signal, sqlite3, struct, sys
    root, db, pack, k, mode = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
    sys.path.insert(0, os.path.join(root, "organs"))
    os.environ["WIZARD_LIBRARY_DB"] = db
    import memory_write as mw
    import embedder_lock as el
    calls = {"n": 0}
    def enc(text, conn=None):
        calls["n"] += 1
        if calls["n"] == k:
            if mode == "kill":
                os.kill(os.getpid(), signal.SIGKILL)
            return None
        _table, dim = mw._resolve_tier(conn)
        h = hashlib.sha256(text.encode("utf-8")).digest()
        return struct.pack("%df" % dim, *[(h[i % 32] - 128) / 128.0 for i in range(dim)])
    mw.default_encoder = enc
    lock = el.resolve(db)
    el.register_loaded(model_id=lock["model_id"], revision=lock["revision"], weights_hash=lock["weights_hash"],
                       dim=int(lock["dim"]), vec_table=lock["vec_table"], tier=lock.get("tier"))
    import claude_memory_lite as lite
    sys.argv = ["claude_memory_lite.py", "load-seed", "--file", pack, "--embed"] + sys.argv[6:]
    rc = lite.main()
    sys.stderr.write("ENCODER_CALLS %d\\n" % calls["n"])
    sys.stderr.write("MODEL_LIBS %s\\n" % sorted(m for m in ("torch", "sentence_transformers", "transformers")
                                                 if m in sys.modules))
    sys.exit(rc)
''')


def _vec_available() -> bool:
    try:
        import sqlite_vec  # noqa: F401
        c = sqlite3.connect(":memory:")
        c.enable_load_extension(True)
        sqlite_vec.load(c)
        c.close()
        return True
    except Exception:
        return False


def _envelope(text):
    lines = [ln for ln in text.strip().splitlines() if ln.startswith("{")]
    return json.loads(lines[-1]) if lines else {}


class SavepointMechanism(unittest.TestCase):
    """The stdlib behaviour the loader's BEGIN exists for, measured on the interpreter running this suite."""

    def _rows_after_rollback(self, begin_first: bool) -> int:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE t (x INTEGER)")
        c.commit()
        if begin_first:
            c.execute("BEGIN IMMEDIATE")
        for i in range(3):
            c.execute("SAVEPOINT sp")
            c.execute("INSERT INTO t VALUES (?)", (i,))
            c.execute("RELEASE sp")
        c.rollback()
        n = c.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        c.close()
        return n

    def test_savepoints_with_no_open_transaction_commit_each_frame(self):
        self.assertEqual(self._rows_after_rollback(begin_first=False), 3)

    def test_savepoints_inside_begin_immediate_roll_back_whole(self):
        self.assertEqual(self._rows_after_rollback(begin_first=True), 0)


@unittest.skipUnless(_vec_available(), "sqlite_vec absent on this host: NOT MEASURED")
class SeedLoadIsOneTransaction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n_pack = len(json.loads(PACK.read_text(encoding="utf-8"))["memories"])

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.child = self.tmp / "seed_child.py"
        self.child.write_text(CHILD, encoding="utf-8")

    def tearDown(self):
        self._td.cleanup()

    def env(self, **extra):
        e = {k: v for k, v in os.environ.items() if k not in _RESOLVERS}
        e.update(HOME=str(self.tmp), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONDONTWRITEBYTECODE="1")
        e.update(extra)
        return e

    def brain(self) -> Path:
        d = self.tmp / "brain"
        d.mkdir()
        db = d / "claude_memory.db"
        shutil.copy2(BLANK, db)
        r = subprocess.run([sys.executable, "-B", str(MIGRATOR), "apply", "--db", str(db), "--migrations",
                            str(ROOT / "migrations"), "--json"], capture_output=True, text=True, timeout=300,
                           env=self.env())
        self.assertEqual(r.returncode, 0, r.stdout[-400:] + r.stderr[-400:])
        sys.path.insert(0, str(ROOT / "organs"))
        try:
            import embedder_lock as el
            import sqlite_vec
            c = sqlite3.connect(str(db))
            c.enable_load_extension(True)
            sqlite_vec.load(c)
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings USING vec0("
                      "memory_id INTEGER PRIMARY KEY, embedding float[768])")
            for k, v in (("active_embedding_table", "memory_embeddings"),
                         ("active_embedding_model", "nomic-ai/nomic-embed-text-v1.5"),
                         ("active_embedding_dim", "768")):
                c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (k, v))
            c.commit()
            c.close()
            el.write_lock(d, "cpu")
        finally:
            sys.path.remove(str(ROOT / "organs"))
        return db

    def load(self, db: Path, k: int, mode: str = "refuse", *extra):
        r = subprocess.run([sys.executable, "-B", str(self.child), str(ROOT), str(db), str(PACK), str(k), mode]
                           + list(extra), capture_output=True, text=True, timeout=600, env=self.env())
        calls = [ln for ln in r.stderr.splitlines() if ln.startswith("ENCODER_CALLS ")]
        libs = [ln for ln in r.stderr.splitlines() if ln.startswith("MODEL_LIBS ")]
        if libs:
            self.assertEqual(libs[-1], "MODEL_LIBS []", "a model library was imported by a stand-in-encoder load")
        return r, (int(calls[-1].split()[1]) if calls else None)

    def census(self, db: Path):
        """(memories, seed rows, active duplicate origins, sha256 of the memories table's rows)"""
        c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        try:
            rows = c.execute("SELECT id, content FROM memories ORDER BY id").fetchall()
            return (len(rows),
                    c.execute("SELECT COUNT(*) FROM memories WHERE seed = 1").fetchone()[0],
                    c.execute("SELECT COUNT(*) FROM (SELECT origin FROM memories WHERE seed = 1 AND "
                              "COALESCE(is_archived, 0) = 0 GROUP BY origin HAVING COUNT(*) > 1)").fetchone()[0],
                    hashlib.sha256(repr(rows).encode("utf-8")).hexdigest())
        finally:
            c.close()

    def assert_converges(self, db: Path):
        r, calls = self.load(db, 10 ** 6)
        self.assertEqual(r.returncode, 0, r.stdout[-600:] + r.stderr[-600:])
        self.assertEqual(calls, self.n_pack, "the re-run embeds exactly the entries that are missing")
        self.assertEqual(self.census(db)[:3], (self.n_pack, self.n_pack, 0))
        r, calls = self.load(db, 10 ** 6)
        self.assertEqual((r.returncode, calls, _envelope(r.stdout).get("loaded")), (0, 0, 0))

    def test_control_a_load_that_is_never_refused_writes_every_entry_once(self):
        db = self.brain()
        r, calls = self.load(db, 10 ** 6)
        self.assertEqual(r.returncode, 0, r.stdout[-600:] + r.stderr[-600:])
        self.assertEqual((calls, _envelope(r.stdout)["loaded"]), (self.n_pack, self.n_pack))
        self.assertEqual(self.census(db)[:3], (self.n_pack, self.n_pack, 0))

    def test_a_refusal_at_entry_k_leaves_the_brain_as_it_was_and_the_envelope_counts_it(self):
        db = self.brain()
        before = self.census(db)
        r, calls = self.load(db, REFUSE_AT)
        self.assertEqual(r.returncode, 3, r.stdout[-600:] + r.stderr[-600:])
        self.assertEqual(calls, REFUSE_AT)
        self.assertEqual(self.census(db), before, "rows written before the refused entry reached the file")
        env = _envelope(r.stdout)
        self.assertEqual((env.get("error"), env.get("memories_before"), env.get("memories_after")),
                         ("SEED_EMBED_REFUSED", before[0], before[0]), env)
        self.assertIn("nothing was written", env.get("note", ""))
        self.assert_converges(db)

    @unittest.skipUnless(hasattr(signal, "SIGKILL"), "no SIGKILL on this platform: NOT MEASURED")
    def test_a_kill_at_entry_k_leaves_the_brain_as_it_was(self):
        db = self.brain()
        before = self.census(db)
        r, _calls = self.load(db, REFUSE_AT, "kill")
        self.assertEqual(r.returncode, -signal.SIGKILL, r.stdout[-600:] + r.stderr[-600:])
        self.assertEqual(self.census(db), before, "rows written before the kill reached the file")
        self.assert_converges(db)

    def test_the_forced_branch_refused_at_entry_k_leaves_the_loaded_brain_as_it_was(self):
        db = self.brain()
        r, _ = self.load(db, 10 ** 6)
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        loaded = self.census(db)
        r, calls = self.load(db, REFUSE_AT, "refuse", "--force")
        self.assertEqual((r.returncode, calls), (3, REFUSE_AT), r.stdout[-600:] + r.stderr[-600:])
        self.assertEqual(self.census(db), loaded, "the forced load committed rows before its refusal")
        env = _envelope(r.stdout)
        self.assertEqual((env.get("memories_before"), env.get("memories_after")), (loaded[0], loaded[0]), env)


if __name__ == "__main__":
    unittest.main()
