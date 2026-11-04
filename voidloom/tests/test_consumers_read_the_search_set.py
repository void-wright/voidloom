"""Every vector consumer reads memory_write's search set, on a LOCKED brain.

One brain, four rows, all in the lock's table (nomic, 768): V verified (its stamp
carries the lock's checksum), W verified, L legacy (no stamp: written before the
lock), and F stamped under a DIFFERENT checksum (the GPU tier's) whose vector IS
the query. A consumer that read the bare table would return F first. Each test is
the control (F absent, L present) for one consumer; reverting that consumer to a
bare-table read turns its test red. No model is loaded: the checksum is
registered and the vectors are fixed.
"""
from __future__ import annotations

import math
import random
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


class _LockedBrain(unittest.TestCase):
    def setUp(self):
        import sqlite_vec
        import embedder_lock as el
        import memory_write as mw
        self.el, self.mw = el, mw
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
        mw.ensure_provenance_table(c)
        c.commit()
        self.conn = c
        el.write_lock(self.d, "cpu")
        cpu = el.catalogue_tiers()["cpu"]
        el.register_loaded(model_id=cpu["model_id"], revision=cpu["revision"],
                           weights_hash=cpu["weights_hash"], dim=DIM, vec_table=TABLE)
        self.V = self._stamped("verified, near", _vec((0, 1.0), (1, 0.3)))
        self.W = self._stamped("verified, far", _vec((3, 1.0)))
        self.L = self._bare("legacy, near", _vec((0, 1.0), (2, 0.5)))
        self.F = self._stamped("foreign, the query itself", Q)
        c.execute("UPDATE embedding_provenance SET weights_hash = ? WHERE memory_id = ?",
                  (el.catalogue_tiers()["gpu"]["weights_hash"], self.F))
        c.commit()

    def tearDown(self):
        self.conn.close()
        self.el.clear_loaded()
        self.tmp.cleanup()

    def _add(self, text: str) -> int:
        cur = self.conn.execute("INSERT INTO memories (content, summary) VALUES (?, ?)", (text, text))
        return int(cur.lastrowid)

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

    def test_the_fixture_puts_the_foreign_row_nearest_in_the_bare_table(self):
        near = self.conn.execute(f"SELECT memory_id FROM {TABLE} WHERE embedding MATCH ? AND k = 1",
                                 (Q,)).fetchone()[0]
        self.assertEqual(near, self.F)


class DreamPipelineReadsTheSearchSet(_LockedBrain):
    def test_fetch_and_novelty_never_see_the_foreign_row(self):
        import dream_pipeline as dp
        self.assertIsNone(dp._fetch_embedding(self.conn, TABLE, self.F))
        self.assertIsNotNone(dp._fetch_embedding(self.conn, TABLE, self.L))
        # The query vector's nearest OTHER row is F at distance 0; with F outside the set it is not 0.
        self.assertGreater(dp.compute_novelty(self.conn, self.V, embedding=Q), 0.01)


    def test_the_eligible_pool_never_holds_the_foreign_row(self):
        import dream_pipeline as dp
        pool, _bias = dp.select_eligible_pool(self.conn, dict(dp.DEFAULT_CONFIG), 10, random.Random(7))
        self.assertEqual(set(pool), {self.V, self.W, self.L})


class RecallDiversityReadsTheSearchSet(_LockedBrain):
    def test_load_embeddings_drops_the_foreign_row_and_keeps_legacy(self):
        import recall_diversity as rd
        self.assertEqual(set(rd._load_embeddings(self.conn, [self.V, self.L, self.F])), {self.V, self.L})

    def test_an_unresolved_tier_gives_no_signal_and_reads_no_table(self):
        import recall_diversity as rd
        from unittest import mock
        with mock.patch.object(rd, "_active_embedding_table", lambda _c: None):
            self.assertEqual(rd._load_embeddings(self.conn, [self.V]), {})
            self.assertFalse(rd.is_diversity_available(self.conn))

    def test_a_tier_that_cannot_resolve_answers_none_never_a_table(self):
        import embed_resolver
        import recall_diversity as rd
        from unittest import mock
        with mock.patch.object(embed_resolver, "resolve", side_effect=RuntimeError("no tier")):
            self.assertIsNone(rd._active_embedding_table(self.conn))

    def test_a_typed_refusal_keeps_relevance_order_and_names_itself(self):
        import recall_diversity as rd
        from unittest import mock
        results = [{"id": self.V, "score": 0.9}, {"id": self.W, "score": 0.8}, {"id": self.L, "score": 0.7}]
        refusal = self.mw.ProvenanceUnreadable("unreadable here")
        with mock.patch.object(rd, "_load_embeddings", side_effect=refusal):
            kept, info = rd.apply_diversity(self.conn, results, "q", top_k=2)
        self.assertEqual(kept, results)
        self.assertEqual(info["refusal"], "PROVENANCE_UNREADABLE")

    def test_the_lane_plan_names_no_tiers_table(self):
        import recall_diversity as rd
        self.assertNotIn("memory_embeddings", rd.VectorLane.wake_cost)


class BeliefEngineReadsTheSearchSet(_LockedBrain):
    def test_neighbours_and_embedding_skip_the_foreign_row(self):
        import belief_engine as be
        self.assertIsNone(be.get_embedding(self.conn, self.F))
        ids = [m for m, _ in be._top_k_neighbors(self.conn, self.V, 5, gate=-1.0)]
        self.assertNotIn(self.F, ids)
        self.assertIn(self.L, ids)


class TemporalEdgesReadsTheSearchSet(_LockedBrain):
    def test_embedding_and_tier3_candidates_skip_the_foreign_row(self):
        import temporal_edges as te
        self.assertIsNone(te._get_memory_embedding(self.conn, self.F))
        ids = [m for m, _ in te.tier3_candidates(self.conn, "p", None)]
        self.assertNotIn(self.F, ids)
        self.assertIn(self.L, ids)


class PreCognitiveRecallReadsTheSearchSet(_LockedBrain):
    def test_vector_fallback_skips_the_foreign_row(self):
        import pre_cognitive_recall as pcr
        ids = [r["id"] for r in pcr._vector_search_fallback(self.conn, Q, top_k=3)]
        self.assertNotIn(self.F, ids)
        self.assertEqual(ids[:2], [self.V, self.L])


class MemoryDreamingReadsTheSearchSet(_LockedBrain):
    def test_divergent_pairs_never_pair_the_foreign_row(self):
        import memory_dreaming as md
        random.seed(7)
        pairs = md.select_divergent_pairs(self.conn, count=20, distance_min=0.0,
                                          candidate_ids=[self.V, self.W, self.L, self.F])
        self.assertTrue(pairs)
        self.assertNotIn(self.F, {x for a, b, _ in pairs for x in (a, b)})

    def test_close_context_pairs_never_pair_the_foreign_row(self):
        import memory_dreaming as md
        random.seed(7)
        # Different contexts everywhere, so the context filter drops nothing the search set let through.
        self.conn.execute("ALTER TABLE memories ADD COLUMN kind TEXT DEFAULT 'semantic'")
        self.conn.execute("UPDATE memories SET source_project = 'p' || id, category = 'c' || id, "
                          "created_at = '2026-0' || id || '-01T00:00:00'")
        self.conn.commit()
        pairs = md.select_close_context_pairs(self.conn, count=20, distance_max=2.0, min_criteria=1)
        self.assertTrue(pairs)
        self.assertNotIn(self.F, {x for p in pairs for x in p[:2]})

    def test_persona_pairs_never_pair_the_foreign_row(self):
        """F is outside the persona pool here, so it is a candidate partner; a bare-table pool would draw it."""
        import memory_dreaming as md
        random.seed(7)
        self.conn.execute("INSERT INTO memory_tags (memory_id, tag) VALUES (?, 'persona-growth')", (self.V,))
        self.conn.commit()
        for _ in range(30):                                     # the partner is a random draw: keep drawing
            pairs = md.select_persona_pairs(self.conn, count=5)
            self.assertTrue(pairs)
            self.assertNotIn(self.F, {x for p in pairs for x in p[:2]})

    def test_a_persona_memory_outside_the_search_set_is_never_an_endpoint(self):
        import memory_dreaming as md
        random.seed(7)
        self.conn.execute("INSERT INTO memory_tags (memory_id, tag) VALUES (?, 'persona-growth'), (?, 'persona-growth')",
                          (self.V, self.F))
        self.conn.commit()
        pairs = md.select_persona_pairs(self.conn, count=5)
        self.assertEqual({p[0] for p in pairs}, {self.V})


if __name__ == "__main__":
    unittest.main(verbosity=2)
