"""The derived layer's figures ship where a customer reads them: `doctor`, `status`, the engine's `stats`.

Built on the report in `memory_write.derived_layer_report`; what the figures mean is in
test_derived_layer.py. Here: a brain in the middle of a model change, read through the
real verbs. A figure that exists but is not in a shipped verb is the failure this file
is for.
"""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))
sys.path.insert(0, str(ROOT))

import embedder_lock as el  # noqa: E402
import memory_write as mw  # noqa: E402
import schema_migrator  # noqa: E402

FOREIGN = "a" * 64


def _env(args):
    from voidloom import cli
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.dispatch(args)
    return rc, json.loads(buf.getvalue())


class MidMigration(unittest.TestCase):
    def setUp(self):
        import sqlite_vec
        el.clear_loaded()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.db = self.d / "claude_memory.db"
        out = schema_migrator.apply(str(self.db), ROOT / "migrations")
        self.assertEqual(out.get("failed") or [], [], out)
        self.h = el.write_lock(self.d, "cpu")["weights_hash"]
        c = sqlite3.connect(str(self.db))
        c.enable_load_extension(True)
        sqlite_vec.load(c)
        c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings USING vec0("
                  "memory_id INTEGER PRIMARY KEY, embedding float[768])")
        for i in range(1, 6):
            c.execute("INSERT INTO memories (content, summary, category, importance, kind) "
                      "VALUES (?, ?, 'insight', 0.5, 'episodic')", (f"m{i}", f"m{i}"))
            c.execute("INSERT INTO memory_embeddings (memory_id, embedding) VALUES (?, ?)",
                      (i, struct.pack("768f", *([1.0] + [0.0] * 767))))
        for s, t, h in ((1, 2, self.h), (2, 3, FOREIGN), (3, 4, FOREIGN), (4, 5, None)):
            c.execute("INSERT INTO memory_links (source_id, target_id, link_type, strength, weights_hash) "
                      "VALUES (?, ?, 'related', 0.7, ?)", (s, t, h))
        c.commit()
        c.close()

    def read(self, verb):
        rc, env = _env([verb, "--db", str(self.db), "--json"])
        self.assertEqual(rc, 0, env)
        return env["data"]["checks"]["embedding_coherence"]["derived_layer"] if verb == "doctor" else env["data"]["derived_layer"]

    def test_control_doctor_and_status_ship_the_stale_count(self):
        for verb in ("doctor", "status"):
            dl = self.read(verb)
            self.assertEqual((dl["stale_derived"], dl["unstamped"], dl["current"]), (2, 1, 1), verb)
            self.assertEqual(dl["tables"]["memory_links"]["stale"], 2, verb)

    def test_control_doctor_and_status_ship_what_is_outside_consolidation(self):
        for verb in ("doctor", "status"):
            c = self.read(verb)["consolidation"]
            self.assertEqual((c["legacy_held"], c["legacy_held_recent"]), (5, 5), verb)

    def test_saboteur_a_brain_whose_vectors_cannot_be_measured_still_reports_the_layer(self):
        (self.d / el.LOCK_NAME).write_text("{ not json", encoding="utf-8")
        _rc, env = _env(["doctor", "--db", str(self.db), "--json"])
        dl = env["data"]["checks"]["embedding_coherence"]["derived_layer"]
        self.assertEqual(dl["stale_derived"], 3)
        self.assertIn("LOCK_MALFORMED", dl["basis"])
        self.assertIsNone(dl["consolidation"]["legacy_held"])

    def test_doctor_writes_nothing(self):
        before = self.db.read_bytes()
        _env(["doctor", "--db", str(self.db), "--json"])
        self.assertEqual(before, self.db.read_bytes())

    def test_control_the_engines_stats_carry_the_layer(self):
        import argparse
        import claude_memory as cm
        buf = io.StringIO()
        with mock.patch.object(cm, "DB_PATH", self.db), contextlib.redirect_stdout(buf):
            cm.cmd_stats(argparse.Namespace())
        out = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertEqual(out["derived_layer"]["stale_derived"], 2)
        self.assertEqual(out["derived_layer"]["consolidation"]["legacy_held"], 5)


if __name__ == "__main__":
    unittest.main()
