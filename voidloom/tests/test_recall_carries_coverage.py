"""Recall's vector arm reads the search set, and every recall answer carries coverage.

A locked brain (nomic, 768) with an FTS index: V verified, L legacy (no stamp),
F stamped under another checksum whose vector IS the query. Controls: the arm
returns V and L and never F; the answer carries the reader's figures with
`legacy_unverified` and `vector_arm: ran`. Saboteurs: the vector table dropped
and a malformed lock both turn the arm off, `degraded` with the refusal's code,
never `ok`, while the keyword arm still answers; an empty result still carries
coverage; a batch record carries its query's coverage. No model is loaded.
"""
from __future__ import annotations

import json
import math
import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

TABLE, DIM = "memory_embeddings", 768


def _vec(*pairs) -> bytes:
    v = [0.0] * DIM
    for i, w in pairs:
        v[i] = w
    n = math.sqrt(sum(x * x for x in v))
    return struct.pack(f"{DIM}f", *[x / n for x in v])


Q = _vec((0, 1.0))


class _Brain(unittest.TestCase):
    def setUp(self):
        import sqlite_vec
        import embedder_lock as el
        import hybrid_retrieval as hr
        import memory_write as mw
        self.el, self.mw, self.hr = el, mw, hr
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        c = sqlite3.connect(str(self.d / "claude_memory.db"))
        c.enable_load_extension(True)
        sqlite_vec.load(c)
        c.enable_load_extension(False)
        c.executescript(f"""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO metadata VALUES ('active_embedding_table', '{TABLE}');
            CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, summary TEXT,
                category TEXT DEFAULT 'insight', importance REAL DEFAULT 0.5, source_project TEXT DEFAULT 'p',
                access_count INTEGER DEFAULT 0, created_at TEXT DEFAULT '2026-01-01T00:00:00',
                is_archived INTEGER NOT NULL DEFAULT 0, derived_from TEXT);
            CREATE TABLE memory_tags (memory_id INTEGER, tag TEXT);
            CREATE VIRTUAL TABLE {TABLE} USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[{DIM}]);
        """)
        c.executescript((ROOT / "migrations" / "migration_001_fts5.sql").read_text(encoding="utf-8"))
        mw.ensure_provenance_table(c)
        c.commit()
        self.conn = c
        el.write_lock(self.d, "cpu")
        cpu = el.catalogue_tiers()["cpu"]
        el.register_loaded(model_id=cpu["model_id"], revision=cpu["revision"],
                           weights_hash=cpu["weights_hash"], dim=DIM, vec_table=TABLE)
        self.V = self._stamped("verified otter notes", _vec((0, 1.0), (1, 0.3)))
        self.L = self._bare("legacy otter notes", _vec((0, 1.0), (2, 0.5)))
        self.F = self._stamped("foreign otter notes", Q)
        c.execute("UPDATE embedding_provenance SET weights_hash = ? WHERE memory_id = ?",
                  (el.catalogue_tiers()["gpu"]["weights_hash"], self.F))
        c.commit()

    def tearDown(self):
        self.conn.close()
        self.el.clear_loaded()
        self.tmp.cleanup()

    def _add(self, text: str) -> int:
        return int(self.conn.execute("INSERT INTO memories (content, summary) VALUES (?, ?)",
                                     (text, text)).lastrowid)

    def _stamped(self, text: str, vec: bytes) -> int:
        mid = self._add(text)
        self.mw.attach_embedding(self.conn, mid, text, embedding=vec, encoder=lambda t: vec,
                                 policy="require", organ="test")
        self.conn.commit()
        return mid

    def _bare(self, text: str, vec: bytes) -> int:
        mid = self._add(text)
        self.conn.execute(f"INSERT INTO {TABLE} (memory_id, embedding) VALUES (?, ?)", (mid, vec))
        self.conn.commit()
        return mid

    def recall(self, query: str, mode: str):
        return self.hr.recall_hybrid_with_coverage(self.conn, query, Q, top_k=5, mode=mode)


class VectorArmReadsTheSearchSet(_Brain):
    def test_control_the_arm_returns_verified_and_legacy_and_never_the_foreign_row(self):
        rows, cov = self.recall("nothing lexical here", "vector-only")
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, [self.V, self.L])
        self.assertNotIn(self.F, ids)
        self.assertEqual((cov["ok"], cov["basis"], cov["vector_arm"], cov["degraded"]),
                         (True, "lock", "ran", False))
        self.assertEqual(cov["legacy_unverified"]["text"], "1 of 2")
        self.assertIs(rows[0]["coverage"], cov)

    def test_control_the_list_form_rows_carry_the_same_coverage(self):
        rows = self.hr.recall_hybrid(self.conn, "nothing lexical here", Q, top_k=5, mode="vector-only")
        self.assertEqual(rows[0]["coverage"]["matched"], 2)


class RefusalsAreDegradedNeverOk(_Brain):
    def test_saboteur_the_vector_table_dropped_is_degraded_and_keyword_still_answers(self):
        self.conn.execute(f"DROP TABLE {TABLE}")
        self.conn.commit()
        rows, cov = self.recall("otter", "vector-only")
        self.assertEqual(rows, [])
        self.assertEqual((cov["ok"], cov["degraded"], cov["vector_arm"]), (False, True, "refused"))
        self.assertTrue(cov["refusal"])
        self.assertEqual((cov["matched"], cov["total"]), (0, 3))
        rows, cov = self.recall("otter", "no-rerank")
        self.assertTrue(rows)                                   # the keyword arm answers
        self.assertEqual((cov["ok"], cov["degraded"]), (False, True))

    def test_saboteur_a_malformed_lock_is_degraded_with_its_code(self):
        lp = self.d / self.el.LOCK_NAME
        lock = json.loads(lp.read_text(encoding="utf-8"))
        lock["vec_table"] = "memory_embeddings_v9"
        lp.write_text(json.dumps(lock), encoding="utf-8")
        rows, cov = self.recall("otter", "no-rerank")
        self.assertTrue(rows)
        self.assertEqual((cov["ok"], cov["error"], cov["degraded"], cov["refusal"]),
                         (False, "LOCK_MALFORMED", True, "LOCK_MALFORMED"))

    def test_control_an_empty_result_still_carries_coverage(self):
        rows, cov = self.recall("zebra", "bm25-only")
        self.assertEqual(rows, [])
        self.assertEqual((cov["ok"], cov["vector_arm"], cov["degraded"], cov["matched"], cov["total"]),
                         (True, "off", False, 2, 3))


class CoverageNeverProbesTheVectorTablePerRow(unittest.TestCase):
    """Recall asks for coverage on every call, so its counts must not do one vec0
    point lookup per stamp. Measured on a 28,063-vector brain: the correlated
    `EXISTS (SELECT 1 FROM <vec table> v WHERE v.memory_id = p.memory_id)` took
    4.3 s per query (13.6 s per envelope); `p.memory_id IN (SELECT memory_id
    FROM <vec table>)` reads the same count in 0.015 s."""

    def test_memory_write_has_no_correlated_vector_table_lookup(self):
        import re
        src = (ROOT / "organs" / "memory_write.py").read_text(encoding="utf-8")
        hits = re.findall(r"EXISTS \(SELECT 1 FROM \{[a-z_.]+\} \w+ WHERE \w+\.memory_id = p\.memory_id\)", src)
        self.assertEqual(hits, [])

    # The same shape anywhere in the shipped tree: a subquery on a vector table (a templated
    # name, or a memory_embeddings* literal) correlated on memory_id to an outer row's column.
    # Measured on a 31,904-memory brain: embed_backfill's count 10.76 s, tier_set's rollback
    # gap 12.77 s, its pending set 4.19 s and coverage 4.66 s, seed_shape 4.86 s (with 11,048
    # seed rows); the uncorrelated IN form read the same values in 0.016-0.11 s.
    _SEAM = r"[\"']\s*\n?\s*[fFrR]{0,2}[\"']"          # adjacent string literals, joined first
    _CORRELATED = (r"(?:EXISTS|IN)\s*\(\s*SELECT\s+[\w.*1]+\s+FROM\s+(\{[^}]+\}|memory_embeddings\w*)\s+(\w+)\s+"
                   r"WHERE\s+\2\.memory_id\s*=\s*(?:\w+|\{[^}]+\})\.(?:id|memory_id)\b")
    # {PROVENANCE_TABLE} is embedding_provenance: an ordinary table keyed (memory_id, vec_table), not vec0.
    _NOT_A_VECTOR_TABLE = {"{PROVENANCE_TABLE}"}

    def _correlated_hits(self, text):
        import re
        joined = re.sub(self._SEAM, "", text)
        return [m.group(0) for m in re.finditer(self._CORRELATED, joined, re.I)
                if m.group(1) not in self._NOT_A_VECTOR_TABLE]

    def test_the_detector_fires_on_every_shape_it_forbids(self):
        planted = [
            'f"  AND NOT EXISTS (SELECT 1 FROM {table} e WHERE e.memory_id = m.id)"',
            'f"AND EXISTS (SELECT 1 FROM {table} v WHERE v.memory_id = m.id)", (table,)',
            "              AND EXISTS (SELECT 1 FROM {t} v WHERE v.memory_id = m.id))",
            'gap = conn.execute(f"SELECT COUNT(*) FROM memories m WHERE NOT EXISTS "\n'
            '                   f"(SELECT 1 FROM {restored} v WHERE v.memory_id = m.id)")',
            "WHERE NOT EXISTS (SELECT 1 FROM memory_embeddings_v3 v WHERE v.memory_id = p.memory_id)",
            'f"OR EXISTS (SELECT 1 FROM {flt.vec_table} v0 WHERE v0.memory_id = {mem_alias}.id)"',
        ]
        for sample in planted:
            self.assertEqual(len(self._correlated_hits(sample)), 1, sample)
        # and stays quiet on the shapes that replace it, and on the provenance table
        for clean in ['f"  AND m.id NOT IN (SELECT memory_id FROM {table})"',
                      "AND EXISTS (SELECT 1 FROM {PROVENANCE_TABLE} p WHERE p.memory_id = m.id)"]:
            self.assertEqual(self._correlated_hits(clean), [], clean)

    def test_no_shipped_file_has_a_correlated_vector_table_lookup(self):
        files = [ROOT / "install.sh"]
        for top in ("organs", "install", "voidloom", "tools", "capability"):
            files += [p for p in (ROOT / top).rglob("*")
                      if p.suffix in (".py", ".sh", ".ps1") and "tests" not in p.relative_to(ROOT).parts]
        scanned, hits = 0, []
        for p in files:
            if not p.is_file():
                continue
            scanned += 1
            hits += [f"{p.relative_to(ROOT)}: {h[:90]}"
                     for h in self._correlated_hits(p.read_text(encoding="utf-8", errors="replace"))]
        self.assertGreater(scanned, 100)                # the walk found the tree, not an empty directory
        self.assertEqual(hits, [])


class BatchRecordsCarryCoverage(unittest.TestCase):
    def test_each_record_carries_its_querys_coverage_even_with_no_rows(self):
        from voidloom import _batch, _facade
        cov = {"ok": True, "matched": 0, "total": 0}
        real = _facade.recall
        _facade.recall = lambda q, **kw: {"status": "ok", "results": [], "coverage": cov}
        try:
            recs = _batch.recall_batch(["anything"], db_path="unused.db")
        finally:
            _facade.recall = real
        self.assertEqual((recs[0]["results"], recs[0]["coverage"]), ([], cov))


if __name__ == "__main__":
    unittest.main(verbosity=2)
