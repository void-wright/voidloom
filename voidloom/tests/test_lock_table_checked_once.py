"""A lock that names a table this package does not build is refused once, when it is read.

THE CLAIM UNDER TEST
  Every caller that takes a vector table from the install lock reads it through
  `embedder_lock.read_lock` (directly, or through `embedder_lock.resolve`). The lock
  is only ever WRITTEN by `write_lock`, which copies a catalogue tier's own
  (vec_table, dim) pair, so a lock naming any other pair was not written by this
  package. `read_lock` refuses it as LOCK_MALFORMED, so no caller can search, stamp
  or load under it:

    embed_resolver.resolve        (every lock-first reader: recall, dreams, diversity)
    claude_memory._active_embed_model   (the engine's tier; a lock naming the jina
                                   table would otherwise select the jina loader)
    embedding_upgrade.brain_lock  (the one loader)
    tier_set._installed_lock      (the tier switch)
    embedder_lock.resolve         (model_fetch, the installers' pre-cache, self-check)

  Absence is still absence: a brain with no lock reads LOCK_MISSING and keeps the
  pre-lock path. A lock whose pair IS a shipped tier's still reads, whatever its
  weights say: whether a checksum is shipped is decided after the rows are counted.

No model is loaded and no network is used.
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "organs", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import embedder_lock as el  # noqa: E402

# (description, the lock's tier, vec_table, dim) -- each a pair this package does not build for that tier
FORGED = (
    ("a table nothing creates", "cpu", "memory_embeddings_v9", 768),
    ("the jina table under the cpu tier", "cpu", "memory_embeddings_v2", 1024),
    ("another shipped tier's pair", "cpu", "memory_embeddings_v3", 1024),
    ("the right table at another width", "cpu", "memory_embeddings", 1024),
    ("the jina table under the gpu tier", "gpu", "memory_embeddings_v2", 1024),
)


def _brain(d: Path, tier: str = "cpu") -> Path:
    db = d / "claude_memory.db"
    c = sqlite3.connect(db)
    c.executescript(
        "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);"
        "CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT, is_archived INTEGER DEFAULT 0);")
    c.commit()
    c.close()
    el.write_lock(d, tier)
    return db


def _forge(d: Path, tier: str, table: str, dim: int) -> None:
    lp = d / el.LOCK_NAME
    lock = json.loads(lp.read_text(encoding="utf-8"))
    lock.update({"tier": tier, "vec_table": table, "dim": dim})
    lp.write_text(json.dumps(lock), encoding="utf-8")


class LockTableCheckedOnce(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.pop("WIZARD_EMBED_MODEL", None)
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
        if self._env is not None:
            os.environ["WIZARD_EMBED_MODEL"] = self._env

    # ---- every caller, asked the same question; the answer is a code or a value
    def answers(self, db: Path) -> dict:
        out = {}

        def ask(name, fn):
            try:
                out[name] = ("OK", fn())
            except Exception as e:  # noqa: BLE001 -- a typed refusal is an answer
                out[name] = ("REFUSED", getattr(e, "code", type(e).__name__))

        ask("embedder_lock.read_lock", lambda: el.read_lock(el.lock_path_for(db))["vec_table"])
        ask("embedder_lock.resolve", lambda: el.resolve(db)["vec_table"])
        conn = sqlite3.connect(db)
        try:
            ask("embed_resolver.resolve", lambda: importlib.import_module("embed_resolver").resolve(conn)[0])
            ask("embedding_upgrade.brain_lock",
                lambda: importlib.import_module("embedding_upgrade").brain_lock(conn, tier="nomic")["vec_table"])
        finally:
            conn.close()
        ask("tier_set._installed_lock",
            lambda: (importlib.import_module("tier_set")._installed_lock(db) or {}).get("vec_table"))
        cm = importlib.import_module("claude_memory")
        saved = cm.DB_PATH
        cm.DB_PATH = db
        cm._active_model_cache.clear()
        try:
            ask("claude_memory._active_embed_model", cm._active_embed_model)
        finally:
            cm.DB_PATH = saved
            cm._active_model_cache.clear()
        return out

    def test_control_a_written_lock_reads_the_same_table_everywhere(self):
        for tier, table, model_tier in (("cpu", "memory_embeddings", "nomic"),
                                        ("gpu", "memory_embeddings_v3", "qwen")):
            with self.subTest(tier=tier):
                d = self.d / tier
                d.mkdir()
                got = self.answers(_brain(d, tier))
                want = {k: ("OK", table) for k in got}
                want["claude_memory._active_embed_model"] = ("OK", model_tier)
                self.assertEqual(got, want)

    def test_saboteur_a_forged_pair_is_LOCK_MALFORMED_for_every_caller(self):
        for i, (what, tier, table, dim) in enumerate(FORGED):
            with self.subTest(what):
                d = self.d / f"f{i}"
                d.mkdir()
                db = _brain(d, "cpu")
                _forge(d, tier, table, dim)
                got = self.answers(db)
                self.assertEqual({k: v[0] for k, v in got.items()}, {k: "REFUSED" for k in got}, got)
                # the stamp path and the engine re-type nothing; the resolver keeps the lock's code
                self.assertEqual({k: v[1] for k, v in got.items()},
                                 {k: "LOCK_MALFORMED" for k in got}, got)

    def test_saboteur_the_jina_pair_never_selects_the_jina_loader(self):
        # the member a per-caller check missed: the engine maps the jina TABLE to the
        # jina TIER, so a lock naming it would have loaded the non-shipped model by name
        db = _brain(self.d, "cpu")
        _forge(self.d, "cpu", "memory_embeddings_v2", 1024)
        got = self.answers(db)["claude_memory._active_embed_model"]
        self.assertNotEqual(got, ("OK", "jina"))
        self.assertEqual(got, ("REFUSED", "LOCK_MALFORMED"))

    def test_control_no_lock_is_still_absence_not_malformed(self):
        db = self.d / "claude_memory.db"
        sqlite3.connect(db).close()
        with self.assertRaises(el.EmbedderLockError) as cm:
            el.resolve(db)
        self.assertEqual(cm.exception.code, "LOCK_MISSING")
        self.assertIsNone(importlib.import_module("tier_set")._installed_lock(db))

    def test_control_foreign_weights_on_a_shipped_pair_still_read(self):
        # the table check is a shape check, not the shipped-weights policy
        db = _brain(self.d, "cpu")
        lp = self.d / el.LOCK_NAME
        lock = json.loads(lp.read_text(encoding="utf-8"))
        lock["weights_hash"] = "f" * 64
        lp.write_text(json.dumps(lock), encoding="utf-8")
        self.assertEqual(el.resolve(db)["weights_hash"], "f" * 64)


if __name__ == "__main__":
    unittest.main()
