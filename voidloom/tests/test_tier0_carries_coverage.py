# -*- coding: utf-8 -*-
"""The degraded answer carries coverage, and `load-seed` can stop at a bound.

Tier 0 is the stdlib engine: no model, no vec0. It is also the answer the front
door falls back to, so its recall has to say how much of the brain a vector search
could have reached, and its `stats` must count that population rather than rows
present in a table. Every figure here is held to the reader's on the same brain.
Each engine call runs in a child, as a stdlib process.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
sys.path.insert(0, str(ROOT / "organs"))

LITE = ROOT / "organs" / "claude_memory_lite.py"
MIGRATIONS = ROOT / "migrations"
NOMIC, JINA = "memory_embeddings", "memory_embeddings_v2"
FOREIGN = "a" * 64


def _vec(dim, i):
    v = [0.0] * dim
    v[i] = 1.0
    return struct.pack(f"{dim}f", *v)


class _Brain(unittest.TestCase):
    LOCK = "cpu"
    TABLE, DIM = NOMIC, 768
    METADATA_TABLE = None

    def setUp(self):
        import embedder_lock as el
        import memory_write as mw
        import schema_migrator
        import sqlite_vec
        self.el, self.mw = el, mw
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.db = self.d / "claude_memory.db"
        out = schema_migrator.apply(str(self.db), MIGRATIONS)
        self.assertEqual(out.get("failed") or [], [], out)
        c = sqlite3.connect(str(self.db))
        c.enable_load_extension(True)
        sqlite_vec.load(c)
        c.enable_load_extension(False)
        self.conn = c
        c.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {self.TABLE} USING vec0("
                  f"memory_id INTEGER PRIMARY KEY, embedding float[{self.DIM}])")
        c.execute("DELETE FROM metadata WHERE key LIKE 'active_embedding%'")
        if self.METADATA_TABLE:
            c.execute("INSERT INTO metadata (key, value) VALUES ('active_embedding_table', ?)", (self.METADATA_TABLE,))
        c.commit()
        self.lock = el.write_lock(self.d, self.LOCK) if self.LOCK else None
        self.n = 0

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def add(self, text):
        mid = int(self.conn.execute("INSERT INTO memories (content, summary, category, importance) "
                                    "VALUES (?, ?, 'insight', 0.5)", (text, text)).lastrowid)
        self.conn.commit()
        return mid

    def bare(self, text):
        mid = self.add(text)
        self.n += 1
        self.conn.execute(f"INSERT INTO {self.TABLE} (memory_id, embedding) VALUES (?, ?)", (mid, _vec(self.DIM, self.n)))
        self.conn.commit()
        return mid

    def stamped(self, text, whash):
        mid = self.bare(text)
        self.conn.execute(
            "INSERT OR REPLACE INTO embedding_provenance (memory_id, vec_table, model_id, model_ref, weights_hash, dim, "
            "content_hash, embedded_at, organ, input_kind, inputs) VALUES "
            "(?, ?, 'm/m', 'r', ?, ?, ?, '2026-01-01T00:00:00+00:00', 't', 'text', NULL)",
            (mid, self.TABLE, whash, self.DIM, self.mw.content_hash(text)))
        self.conn.commit()
        return mid

    def lite(self, *argv, rc=0):
        env = {k: v for k, v in os.environ.items() if k not in ("WIZARD_LIBRARY_HOME", "WIZARD_EMBED_MODEL")}
        env.update({"WIZARD_LIBRARY_DB": str(self.db), "PYTHONDONTWRITEBYTECODE": "1"})
        r = subprocess.run([sys.executable, str(LITE), *argv], capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(r.returncode, rc, r.stdout[-400:] + r.stderr[-400:])
        return json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else None


class RecallCarriesCoverage(_Brain):
    def population(self):
        h = self.lock["weights_hash"]
        self.stamped("lantern verified", h)
        self.bare("lantern legacy")
        self.stamped("lantern foreign", FOREIGN)
        self.stamped("lantern unresolved", "unresolved")
        gone = self.stamped("lantern archived", h)
        self.conn.execute("UPDATE memories SET is_archived = 1 WHERE id = ?", (gone,))
        self.add("lantern with no vector")
        self.conn.commit()

    def test_control_matched_over_total_on_the_envelope_and_every_row(self):
        self.population()
        out = self.lite("recall", "--query", "lantern")
        cov = out["coverage"]
        self.assertEqual((out["mode"], out["result_count"]), ("fts5-keyword", 5))
        self.assertEqual((cov["matched"], cov["total"], cov["verified"], cov["legacy_unverified"]), (3, 5, 1, 2), cov)
        self.assertEqual(cov["coverage"], {"matched": 3, "total": 5, "value": 3 / 5})
        self.assertTrue(all(r["coverage"]["matched"] == 3 for r in out["results"]))

    def test_control_the_figures_are_the_readers_on_the_same_brain(self):
        self.population()
        env = self.mw.coverage_envelope(self.conn)
        cov = self.lite("recall", "--query", "lantern")["coverage"]
        self.assertEqual((cov["matched"], cov["total"]), (env["matched"], env["total"]))
        self.assertEqual((cov["verified"], cov["legacy_unverified"]), (env["verified"], env["legacy_unverified"]["legacy"]))

    def test_control_an_empty_result_still_carries_coverage(self):
        self.population()
        out = self.lite("recall", "--query", "zebra")
        self.assertEqual((out["result_count"], out["coverage"]["matched"], out["coverage"]["total"]), (0, 3, 5))

    def test_saboteur_a_malformed_lock_is_zero_and_says_so(self):
        self.population()
        (self.d / "embedder.lock.json").write_text("{not json", encoding="utf-8")
        cov = self.lite("recall", "--query", "lantern")["coverage"]
        self.assertEqual((cov["matched"], cov["total"]), (0, 5))
        self.assertIn("lock unreadable", cov["basis"])
        self.assertIn("LOCK_MALFORMED", cov["basis"])

    def test_saboteur_no_lock_and_no_declared_tier_is_zero_and_says_so(self):
        self.population()
        (self.d / "embedder.lock.json").unlink()
        (self.d / "embedder.lock.history.jsonl").unlink()
        cov = self.lite("recall", "--query", "lantern")["coverage"]
        self.assertEqual((cov["matched"], cov["total"]), (0, 5))
        self.assertIn("lock unreadable", cov["basis"])

    def test_control_the_lock_is_found_through_a_symlinked_database(self):
        self.stamped("lantern verified", self.lock["weights_hash"])
        other = self.d / "elsewhere"
        other.mkdir()
        link = other / "claude_memory.db"
        link.symlink_to(self.db)
        real, self.db = self.db, link
        try:
            cov = self.lite("recall", "--query", "lantern")["coverage"]
        finally:
            self.db = real
        self.assertEqual((cov["matched"], cov["total"]), (1, 1), cov)

    def test_control_a_stampless_sidecar_reads_as_the_reader_reads_it(self):
        self.conn.execute("DROP TABLE embedding_provenance")
        self.conn.commit()
        self.bare("lantern")
        cov = self.lite("recall", "--query", "lantern")["coverage"]
        self.assertEqual((cov["matched"], cov["total"]), (0, 1))
        self.assertIn("matched 0 by construction", cov["basis"])

    def test_control_coverage_is_counted_without_a_per_row_lookup(self):
        src = LITE.read_text(encoding="utf-8")
        body = src[src.index("def _lock_coverage("):src.index("def cmd_recall(")]
        self.assertNotIn("EXISTS", body)


class RecallOnAJinaBrain(_Brain):
    LOCK = None
    TABLE, DIM = JINA, 1024
    METADATA_TABLE = JINA

    def test_control_the_whole_table_is_the_set(self):
        self.bare("lantern one")
        gone = self.bare("lantern two")
        self.conn.execute("UPDATE memories SET is_archived = 1 WHERE id = ?", (gone,))
        self.conn.commit()
        self.add("lantern with no vector")
        out = self.lite("recall", "--query", "lantern")
        cov = out["coverage"]
        self.assertEqual((out["result_count"], cov["matched"], cov["total"]), (2, 1, 2), cov)
        self.assertEqual((cov["search_rule"], cov["vec_table"]), ("lockless: whole table", JINA))


class StatsCountsTheSearchSet(_Brain):
    def test_control_the_figure_is_matched_not_presence(self):
        h = self.lock["weights_hash"]
        self.stamped("a", h)
        self.bare("b")
        self.stamped("c", FOREIGN)
        gone = self.stamped("d", h)
        self.conn.execute("UPDATE memories SET is_archived = 1 WHERE id = ?", (gone,))
        self.conn.commit()
        presence = self.conn.execute(f"SELECT COUNT(*) FROM {NOMIC}").fetchone()[0]
        out = self.lite("stats")
        self.assertEqual((presence, out["embedded_memories"], out["semantic_tier"]), (4, 2, "active"))
        self.assertEqual((out["coverage"]["matched"], out["coverage"]["total"]), (2, 3))
        self.assertEqual(out["embedded_memories"], self.mw.coverage_envelope(self.conn)["matched"])

    def test_saboteur_every_vector_foreign_reads_zero(self):
        self.stamped("a", FOREIGN)
        out = self.lite("stats")
        self.assertEqual((out["embedded_memories"], out["coverage"]["matched"]), (0, 0))
        self.assertEqual(out["semantic_tier"], "present, no vector in the search set")

    def test_control_no_sidecar_falls_back_to_the_table_and_coverage_says_unmeasured(self):
        self.conn.execute("DROP TABLE embedding_provenance")
        self.conn.commit()
        self.bare("a")
        out = self.lite("stats")
        self.assertEqual((out["embedded_memories"], out["coverage"]["matched"], out["coverage"]["measured"]), (1, 0, False))


class LoadSeedStopsAtItsBound(_Brain):
    def pack(self, n=3):
        p = self.d / "pack.json"
        p.write_text(json.dumps({"seed_pack_version": "9.9.9", "common_tag": "seed-pack-agnostic", "memories": [
            {"content": f"entry number {i} of the test pack", "summary": f"entry {i}", "category": "insight",
             "tags": f"anchor:seed_entry{i}"} for i in range(n)]}), encoding="utf-8")
        return str(p)

    def seeded(self):
        return self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def test_control_each_load_takes_its_bound_and_the_next_resumes(self):
        p = self.pack()
        out = self.lite("load-seed", "--file", p, "--limit", "2")
        self.assertEqual((out["loaded"], len(out["new"]), len(out["deferred_by_limit"]), self.seeded()), (2, 2, 1, 2), out)
        out = self.lite("load-seed", "--file", p, "--limit", "2")
        self.assertEqual((out["loaded"], out["deferred_by_limit"], self.seeded()), (1, [], 3), out)
        out = self.lite("load-seed", "--file", p, "--limit", "2")
        self.assertEqual((out["loaded"], out["present"], self.seeded()), (0, 3, 3), out)

    def test_control_no_limit_loads_everything(self):
        out = self.lite("load-seed", "--file", self.pack())
        self.assertEqual((out["loaded"], out["limit"], out["deferred_by_limit"], self.seeded()), (3, None, [], 3), out)

    def test_control_a_limit_of_zero_writes_nothing(self):
        out = self.lite("load-seed", "--file", self.pack(), "--limit", "0")
        self.assertEqual((out["loaded"], len(out["deferred_by_limit"]), self.seeded()), (0, 3, 0), out)

    def test_saboteur_a_negative_limit_refuses(self):
        self.lite("load-seed", "--file", self.pack(), "--limit", "-1", rc=2)
        self.assertEqual(self.seeded(), 0)

    def test_control_the_forced_path_stops_at_the_bound_too(self):
        out = self.lite("load-seed", "--file", self.pack(), "--force", "--limit", "1")
        self.assertEqual((out["loaded"], self.seeded()), (1, 1), out)


if __name__ == "__main__":
    unittest.main()
