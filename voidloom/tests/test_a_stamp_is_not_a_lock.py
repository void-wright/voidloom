"""A shipped checksum in a stamp says what embedded a row. It says nothing about a lock.

THE CLAIM UNDER TEST
  * a brain with no lock, no history and no row in `metadata` is a legal pre-lock brain, whatever stamps sit in it or
    beside it. A store, a `tier set` build and a website store all write shipped checksums with no lock in sight, and
    each of those shapes keeps reading, writing and passing `doctor`, from a second process;
  * a lock that was written is known only from what `write_lock` left: its receipt, and the row it wrote into the brain,
    which outlives both files. A rollback to a lockless source clears that row;
  * the refusal names only records that exist, and its printed command repairs the brain.
"""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORGANS = ROOT / "organs"
if str(ORGANS) not in sys.path:
    sys.path.insert(0, str(ORGANS))

from voidloom.tests.test_tier_set import (TABLES, _connect, _vec_available, fake_encode, fake_load,  # noqa: E402
                                          make_brain)

PROBE = r"""
import json, sqlite3, sys
sys.path.insert(0, sys.argv[1])
import embedder_lock as el, embed_resolver, memory_write as mw, sqlite_vec
c = sqlite3.connect(sys.argv[2]); c.enable_load_extension(True); sqlite_vec.load(c); c.enable_load_extension(False)
out = {}
try:
    el.refuse_lost_lock(c); out["write"] = "open"
except el.EmbedderLockError as e:
    out["write"] = e.code
try:
    out["table"] = mw.provenance_filter(c).vec_table
except el.EmbedderLockError as e:
    out["table"] = e.code
print(json.dumps(out))
"""


def probe(db: Path) -> dict:
    """What a SECOND process reads: the write gate and the table the search would use."""
    r = subprocess.run([sys.executable, "-c", PROBE, str(ORGANS), str(db)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def stamp_under(db: Path, tier: str, n: int = 2) -> None:
    """Rows as a build leaves them: real stamps under a shipped checksum, in that tier's own table, before any lock."""
    import embedder_lock as el
    import memory_write as mw
    e = el.catalogue_tiers()[tier]
    c = _connect(db)
    c.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {e['vec_table']} USING vec0(memory_id INTEGER PRIMARY KEY, "
              f"embedding float[{e['dim']}])")
    for mid, text in c.execute("SELECT id, content FROM memories ORDER BY id LIMIT ?", (n,)).fetchall():
        c.execute(f"DELETE FROM {e['vec_table']} WHERE memory_id = ?", (mid,))
        c.execute(f"INSERT INTO {e['vec_table']} (memory_id, embedding) VALUES (?, ?)", (mid, bytes(4 * int(e["dim"]))))
        c.execute("INSERT OR REPLACE INTO embedding_provenance VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (mid, e["vec_table"], e["model_id"], e["revision"], e["weights_hash"], int(e["dim"]),
                   mw.content_hash(text), "2026-10-01T00:00:00+00:00", "tier_set", "text", None))
    c.commit()
    c.close()


@unittest.skipUnless(_vec_available(), "sqlite_vec absent on this host: NOT MEASURED")
class _Room(unittest.TestCase):
    def setUp(self):
        import embedder_lock as el
        import tier_set
        self.el, self.ts = el, tier_set
        el.clear_loaded()
        self._seams = (tier_set._load, tier_set._encode)
        tier_set._load, tier_set._encode = fake_load, fake_encode
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self._env = {k: os.environ.pop(k, None) for k in ("WIZARD_EMBED_MODEL", "WIZARD_LIBRARY_DB")}

    def tearDown(self):
        self.ts._load, self.ts._encode = self._seams
        self.el.clear_loaded()
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v
        self._td.cleanup()

    def row(self, db: Path):
        c = sqlite3.connect(str(db))
        try:
            r = c.execute("SELECT value FROM metadata WHERE key = ?", (self.el.BRAIN_LOCK_KEY,)).fetchone()
        finally:
            c.close()
        return None if r is None else r[0]


class AStampProvesNoLock(_Room):
    def assert_legal(self, db, table):
        self.assertIsNone(self.el.lost_lock(db.parent, db))
        self.assertEqual(probe(db), {"write": "open", "table": table})

    def test_saboteur_an_ambient_brain_writes_its_first_stamp_and_keeps_writing(self):
        db = make_brain(self.dir, "nomic")
        (self.dir / "embedder.lock.json").unlink()
        (self.dir / "embedder.lock.history.jsonl").unlink()
        c = sqlite3.connect(str(db)); c.execute("DELETE FROM metadata WHERE key = ?", (self.el.BRAIN_LOCK_KEY,))
        c.commit(); c.close()
        stamp_under(db, "cpu")
        self.assert_legal(db, "memory_embeddings")

    def test_saboteur_a_jina_brain_mid_build_keeps_serving_from_a_second_process(self):
        db = make_brain(self.dir, "jina")
        seen = {}

        def encode_then_look(texts, lock):
            out = fake_encode(texts, lock)
            if seen:
                seen.setdefault("later", probe(db))
                raise KeyboardInterrupt("killed")
            seen["first"] = True
            return out
        self.ts._encode = encode_then_look
        with self.assertRaises(KeyboardInterrupt):
            self.ts.set_tier(db, "qwen", batch_size=2)
        self.assertEqual(seen["later"], {"write": "open", "table": "memory_embeddings_v2"}, seen)
        self.assert_legal(db, "memory_embeddings_v2")

    def test_saboteur_sigkill_during_the_build_on_a_jina_brain_leaves_it_serving_and_resumable(self):
        db = make_brain(self.dir, "jina", n=7)
        child = r"""
import sys, time
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])
import tier_set
from voidloom.tests.test_tier_set import fake_load, fake_encode
from pathlib import Path
tier_set._load = fake_load
n = []
def enc(texts, lock):
    n.append(1)
    if len(n) == 2:
        print("BUILT", flush=True); time.sleep(120)
    return fake_encode(texts, lock)
tier_set._encode = enc
tier_set.set_tier(Path(sys.argv[3]), "qwen", batch_size=2)
"""
        p = subprocess.Popen([sys.executable, "-c", child, str(ORGANS), str(ROOT), str(db)], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True)
        self.assertEqual(p.stdout.readline().strip(), "BUILT")
        p.send_signal(signal.SIGKILL)
        p.wait(30)
        p.stdout.close()
        c = sqlite3.connect(str(db))
        built = c.execute("SELECT COUNT(*) FROM embedding_provenance WHERE vec_table = 'memory_embeddings_v3'").fetchone()[0]
        c.close()
        self.assertGreater(built, 0, "the kill must land after a committed batch")
        self.assert_legal(db, "memory_embeddings_v2")
        with self.assertRaises(self.ts.TierRefused) as cm:
            self.ts.rollback(db)
        self.assertEqual(cm.exception.code, "NOTHING_TO_ROLL_BACK")
        env = self.ts.set_tier(db, "qwen", batch_size=2)
        self.assertEqual(env["receipt"]["reused"], built, env["receipt"])
        self.assertEqual(self.el.resolve(db)["tier"], "gpu")

    def test_saboteur_a_website_store_beside_a_jina_brain_says_nothing_about_the_brain(self):
        db = make_brain(self.dir, "jina")
        site = self.dir / "website_memory.db"
        import embedder_lock as el
        c = _connect(site)
        c.executescript("CREATE TABLE embedding_provenance (memory_id INTEGER, weights_hash TEXT);")
        c.execute("INSERT INTO embedding_provenance VALUES (1, ?)", (el.catalogue_tiers()["gpu"]["weights_hash"],))
        c.commit(); c.close()
        self.assert_legal(db, "memory_embeddings_v2")

    def test_saboteur_a_pre_image_beside_the_brain_says_nothing_about_the_brain(self):
        db = make_brain(self.dir, "jina")
        stamped = make_brain(Path(tempfile.mkdtemp(dir=self.dir)), "qwen")
        os.replace(stamped, self.dir / "claude_memory.pre-upgrade-20261105.db")
        self.assert_legal(db, "memory_embeddings_v2")

    def test_saboteur_a_killed_re_embed_on_a_pre_lock_brain_stays_pre_lock(self):
        db = make_brain(self.dir, "nomic")
        (self.dir / "embedder.lock.json").unlink()
        (self.dir / "embedder.lock.history.jsonl").unlink()
        c = sqlite3.connect(str(db)); c.execute("DELETE FROM metadata WHERE key = ?", (self.el.BRAIN_LOCK_KEY,))
        c.execute("DELETE FROM embedding_provenance WHERE memory_id > 1"); c.commit(); c.close()
        calls = []

        def dies(texts, lock):
            calls.append(1)
            if len(calls) == 2:
                raise KeyboardInterrupt("killed")
            return fake_encode(texts, lock)
        self.ts._encode = dies
        with self.assertRaises(KeyboardInterrupt):
            self.ts.re_embed_under_lock(db, batch_size=2)
        self.ts._encode = fake_encode
        self.assert_legal(db, "memory_embeddings")
        self.assertEqual(self.ts.re_embed_under_lock(db, batch_size=2)["status"], "ok")
        self.assertFalse((self.dir / "embedder.lock.json").exists(), "re-embed pins nothing")
        self.assert_legal(db, "memory_embeddings")


class OnlyWhatWriteLockLeftProvesALock(_Room):
    def lose_both(self):
        (self.dir / "embedder.lock.json").unlink()
        (self.dir / "embedder.lock.history.jsonl").unlink()

    def test_saboteur_tier_set_leaves_the_row_and_deleting_both_files_is_still_seen(self):
        db = make_brain(self.dir, "jina")
        self.ts.set_tier(db, "qwen")
        self.assertEqual(self.row(db), "gpu")
        self.lose_both()
        self.assertEqual(self.el.lost_lock(self.dir, db), {"tier": "gpu"})
        self.assertEqual(probe(db), {"write": "LOCK_MISSING", "table": "LOCK_MISSING"})
        with self.assertRaises(self.el.EmbedderLockError) as cm:
            self.el.resolve(db)
        self.assertNotIn(self.el.LOCK_HISTORY_NAME, str(cm.exception), "no file that is not there is named")
        self.assertIn("init --tier gpu", cm.exception.detail["retry"][0])

    def test_saboteur_the_row_of_an_attached_brain_is_read_from_that_brain(self):
        db = make_brain(self.dir, "nomic")
        other = self.dir / "brain.db"
        os.replace(db, other)
        c = sqlite3.connect(str(other))
        self.el.mark_brain(c, "cpu")
        c.commit(); c.close()
        self.lose_both()
        self.assertEqual(self.el.lost_lock(self.dir, other), {"tier": "cpu"})
        self.assertEqual(probe(other)["write"], "LOCK_MISSING")
        self.assertIs(self.el.describe(other)["refusal"]["was_locked"], True, "what `doctor` reads")

    def test_saboteur_a_rollback_to_a_lockless_source_clears_the_row(self):
        db = make_brain(self.dir, "jina")
        self.ts.set_tier(db, "qwen")
        self.ts.rollback(db)
        self.assertEqual(self.row(db), "", "cleared, and cleared is a word the reader keeps")
        self.assertIsNone(self.el.lost_lock(self.dir, db))
        self.assertEqual(probe(db), {"write": "open", "table": "memory_embeddings_v2"})

    def test_saboteur_a_rollback_killed_before_its_receipt_still_reads_lockless(self):
        db = make_brain(self.dir, "jina")
        self.ts.set_tier(db, "qwen")
        real = self.ts._receipt

        def dies(*a, **k):
            raise KeyboardInterrupt("killed")
        self.ts._receipt = dies
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.ts.rollback(db)
        finally:
            self.ts._receipt = real
        self.assertIsNone(self.el.lost_lock(self.dir, db), "the row committed with the rollback: the history is late")
        self.assertEqual(probe(db), {"write": "open", "table": "memory_embeddings_v2"})

    def test_saboteur_a_rollback_to_a_locked_source_keeps_the_row_on_the_source(self):
        db = make_brain(self.dir, "nomic")
        self.ts.set_tier(db, "qwen")
        self.assertEqual(self.row(db), "gpu")
        self.ts.rollback(db)
        self.assertEqual(self.row(db), "cpu")

    def test_saboteur_a_re_run_init_gives_an_older_locked_brain_its_row(self):
        db = make_brain(self.dir, "nomic")
        c = sqlite3.connect(str(db)); c.execute("DELETE FROM metadata WHERE key = ?", (self.el.BRAIN_LOCK_KEY,))
        c.commit(); c.close()
        self.el.write_lock(self.dir, "cpu", brain=db)
        self.assertEqual(self.row(db), "cpu")

    def test_control_a_brain_too_busy_for_the_row_still_gets_its_lock(self):
        db = make_brain(self.dir, "nomic")
        (self.dir / "embedder.lock.json").unlink()
        c = sqlite3.connect(str(db)); c.execute("DROP TABLE metadata"); c.commit(); c.close()
        self.assertEqual(self.el.write_lock(self.dir, "cpu", brain=db)["tier"], "cpu")
        self.assertTrue((self.dir / "embedder.lock.json").exists())

    def test_control_a_tier_nothing_recorded_prints_no_placeholder(self):
        make_brain(self.dir, "jina")
        (self.dir / self.el.LOCK_HISTORY_NAME).write_text("{not json\n", encoding="utf-8")
        with self.assertRaises(self.el.EmbedderLockError) as cm:
            self.el.resolve(self.dir / "claude_memory.db")
        self.assertIs(cm.exception.detail["was_locked"], True)
        self.assertNotIn("retry", cm.exception.detail)
        self.assertNotIn("<tier>", str(cm.exception))
        self.assertIn("not recorded", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
