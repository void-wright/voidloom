#!/usr/bin/env python3
"""test_dreamfix_tranche_a.py -- the dream fix, Tranche A (ruler VL-DF-v1.0), on brains the test builds itself.

Every brain here is the PACKAGE's (seed/claude_memory_blank.db brought up by the shipped migrator, via
_owned_brain.packaged_brain), populated with synthetic rows whose vectors come from a deterministic bag-of-words
encoder -- no model, no GPU, no network, no host brain. Model calls are a fake mouth / a patched seam.

What each class binds (SYNTHESIS §TRANCHE A):
  A1  TestStorage          REM stores every meaningful dream (id on the dream_log row), birth 0.40, derived_from,
                           a real summary, taint, an active-tier vector; a store failure keeps is_meaningful=1.
  A2  TestPairing          three pools, derived rows never selected, persona SELF tags (persona-design out), the
                           time box leaves no pool unattempted.
  A3  TestChannels         dreams partitioned out BEFORE the reranker; `results` memories only; the `dreams` key;
                           floors, quotas, --leads 0, the total cap; zero extra reranker calls; the registry generic.
  A4  TestLabel            the frame + provenance; the preamble mirror; _SELECT_TRUSTED excludes by provenance.
  A5  TestTensionFadeEvidence  RELATION parsed before NULL; the tension edge; the read-time fade; the three evidence
                           paths +0.10 capped 0.65; a recall moves nothing (write diff) and no learning path reads
                           the met ledger (differential over every reader).
  A6  TestAlarm            the row invariant: BROKEN / STARVED / HEALTHY, backfill excluded, the met WARN, the
                           sentinel's reading and doctor's section.
  A7  TestBackfill         plan / apply / rollback: 240-style selector, no links invented, the protected tag row
                           untouched, taint carried, archived parents excluded, the scan queue unchanged.
  A8  TestPlaybooks        dedup plan / apply (archived, never deleted) / rollback; emission idempotent.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import struct
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "organs"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _owned_brain import packaged_brain  # noqa: E402

DIM = 1024


def fake_vec(text: str) -> bytes:
    """Deterministic bag-of-words vector: shared words -> shared dimensions -> real cosine structure."""
    v = [0.0] * DIM
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(w) < 3:
            continue
        v[int(hashlib.sha1(w.encode()).hexdigest(), 16) % DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return struct.pack(f"{DIM}f", *[x / n for x in v])


def fake_encoder(text: str) -> bytes:
    return fake_vec(text)


def _connect(db: str) -> sqlite3.Connection:
    c = sqlite3.connect(db)
    import sqlite_vec
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.enable_load_extension(False)
    return c


def _ts(days_ago: float = 0.0) -> str:
    return (datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


class Brain:
    """A packaged brain + helpers to plant rows."""

    _cache: dict = {}

    def __init__(self, tmp: Path):
        src = Brain._cache.get("seed")
        if src is None or not Path(src).exists():
            seed_dir = Path(tempfile.mkdtemp(prefix="dfa-seed-", dir=os.environ.get("TMPDIR")))
            src = str(packaged_brain(seed_dir / "seed.db"))
            Brain._cache["seed"] = src
        self.path = str(tmp / "brain.db")
        a = sqlite3.connect(src)
        b = sqlite3.connect(self.path)
        a.backup(b)
        a.close()
        b.close()
        self.c = _connect(self.path)
        self.c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', "
                       "'memory_embeddings_v2')")
        self.c.commit()

    def add(self, content, *, summary=None, tags=(), kind="episodic", category="insight", project="proj",
            derived_from=None, confidence=1.0, days_ago=0.0, archived=0, untrusted=0, vector=True):
        cur = self.c.execute(
            """INSERT INTO memories (content, summary, category, importance, source_project, kind, confidence,
                   derived_from, created_at, updated_at, last_accessed, is_archived)
               VALUES (?, ?, ?, 0.5, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (content, summary or content[:60], category, project, kind, confidence, derived_from,
             _ts(days_ago), _ts(days_ago), _ts(days_ago), archived))
        mid = cur.lastrowid
        if untrusted:
            self.c.execute("UPDATE memories SET untrusted_source=1 WHERE id=?", (mid,))
        for t in tags:
            self.c.execute("INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)", (mid, t))
        if vector:
            self.c.execute("INSERT INTO memory_embeddings_v2 (memory_id, embedding) VALUES (?, ?)",
                           (mid, fake_vec(content)))
        self.c.commit()
        return mid

    def one(self, sql, args=()):
        return self.c.execute(sql, args).fetchone()

    def all(self, sql, args=()):
        return self.c.execute(sql, args).fetchall()


def _populate_for_rem(b: Brain):
    ids = {}
    words = ["kafka retries backpressure queue", "sourdough hydration crumb oven", "sqlite wal checkpoint",
             "bicycle derailleur cassette", "violin rosin bow", "tax filing deadline form", "orchid watering bark",
             "rocket nozzle thrust chamber", "chess opening gambit", "glacier moraine ice"]
    for i, w in enumerate(words):
        ids[f"far{i}"] = b.add(f"{w} note number {i}", project=f"p{i}", category="insight")
    # close-context pairs: near-identical text, different project + category + kind
    for i in range(6):
        t = f"retry budget exhausted on upstream timeout pattern {i % 2}"
        ids[f"cA{i}"] = b.add(t, project="svcA", category="error_pattern", kind="episodic", days_ago=40 + i)
        ids[f"cB{i}"] = b.add(t + " again", project="svcB", category="technical_decision", kind="semantic")
    # persona: SELF tag rows, and a persona-design row that must never be drawn
    for i in range(3):
        ids[f"self{i}"] = b.add(f"I trusted my own description of a fix {i}", tags=("testpersona-persona",),
                                project="persona")
    ids["design"] = b.add("design notes for some other persona voice", tags=("persona-design", "persona-growth"),
                          project="persona")
    # derived rows that must never be selected
    ids["oldream"] = b.add("[Dream synthesis — divergent]\nSource memories: #1 and #2\n\nAn old dream about queues.",
                           kind="reflection", derived_from="dream_synthesis", confidence=0.4,
                           project="memory-dreaming")
    ids["digest"] = b.add("weekly whisper digest text", kind="reflection", derived_from="whisper_synthesis",
                          confidence=0.8)
    return ids


def _mouth(relation="connection"):
    def m(key, _ctx):
        kind, pair = key.split(":", 1)
        return (f"RELATION: {relation}\nThe first memory about {kind} pair {pair} and the second one share a "
                f"hidden dependency on timing that neither states.")
    return m


def _rem_cfg(tmp: Path, **kw):
    cfg = {"_synthesis_mouth": _mouth(), "_encoder": fake_encoder, "persona_name": "TESTPERSONA",
           "lock_file": str(tmp / "dream.lock"), "_deterministic_seed": 11}
    cfg.update(kw)
    return cfg


class _TmpCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="dfa-", dir=os.environ.get("TMPDIR"))
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()


# =====================================================================================================================
class TestStorage(_TmpCase):
    def test_a_rem_run_stores_every_meaningful_dream_with_the_stamps(self):
        import dream_pipeline as dp
        b = Brain(self.tmp)
        _populate_for_rem(b)
        res = dp.run_dream_cycle(b.path, config=_rem_cfg(self.tmp))
        self.assertEqual(res["status"], "ok", res)
        rows = b.all("SELECT dream_type, is_meaningful, dream_memory_id, context_difference FROM dream_log")
        self.assertTrue(rows, "no dream attempts")
        meaningful = [r for r in rows if r[1] == 1]
        self.assertTrue(meaningful)
        self.assertEqual([r for r in meaningful if r[2] is None], [], "a meaningful dream without its memory")
        for _t, _m, mid, ctx in meaningful:
            conf, df, summ, content, kind = b.one("SELECT confidence, derived_from, summary, content, kind "
                                                  "FROM memories WHERE id=?", (mid,))
            self.assertEqual((conf, df, kind), (0.40, "dream_synthesis", "reflection"))
            self.assertNotRegex(summ, r"[Mm]emory [AB]\b|memories #\d+")
            self.assertNotIn("RELATION", content)
            self.assertEqual(json.loads(ctx)["relation"], "connection")
            self.assertIsNotNone(b.one("SELECT 1 FROM memory_embeddings_v2 WHERE memory_id=?", (mid,)),
                                 "the dream is not embedded in the active tier")
            self.assertEqual(b.one("SELECT COUNT(*) FROM memory_provenance WHERE derived_memory_id=?",
                                   (mid,))[0], 2)

    def test_a_store_failure_keeps_is_meaningful_and_the_watch_reads_broken(self):
        import dream_pipeline as dp
        import memory_dreaming as md
        import dream_ledger as dl
        b = Brain(self.tmp)
        _populate_for_rem(b)
        with mock.patch.object(md, "_vl_attach", side_effect=RuntimeError("forced store failure")):
            res = dp.run_dream_cycle(b.path, config=_rem_cfg(self.tmp))
        self.assertEqual(res["status"], "ok")
        rows = b.all("SELECT is_meaningful, dream_memory_id, context_difference FROM dream_log")
        self.assertTrue(rows)
        self.assertTrue(all(r[0] == 1 and r[1] is None for r in rows), rows[:3])
        self.assertIn("store_error", json.loads(rows[0][2]))
        self.assertEqual(b.one("SELECT COUNT(*) FROM memories WHERE derived_from='dream_synthesis' "
                               "AND content LIKE '%hidden dependency%'")[0], 0, "a failed store left a row")
        w = dl.dream_watch(b.c)
        self.assertEqual(w["verdict"], "BROKEN", w)

    def test_taint_is_inherited_and_archived_endpoints_are_not_stored(self):
        import memory_dreaming as md
        b = Brain(self.tmp)
        a = b.add("clean note about caching layers")
        u = b.add("web page text about caching", untrusted=1)
        x = b.add("retired note about caching", archived=1)
        rep = {}
        mid = md._store_dream_as_reflection(b.c, "RELATION: tension\nThe clean note and the web text disagree "
                                                 "about cache invalidation.", a, u, "divergent",
                                            encoder=fake_encoder, report=rep)
        self.assertTrue(mid, rep)
        self.assertEqual(b.one("SELECT untrusted_source FROM memories WHERE id=?", (mid,))[0], 1)
        rep2 = {}
        self.assertIsNone(md._store_dream_as_reflection(b.c, "A dream about a retired memory and a clean one.",
                                                        a, x, "divergent", encoder=fake_encoder, report=rep2))
        self.assertEqual(rep2.get("skipped"), "endpoint_archived")


# =====================================================================================================================
class TestPairing(_TmpCase):
    def test_one_run_shows_all_three_types_with_memory_ids(self):
        import dream_pipeline as dp
        b = Brain(self.tmp)
        _populate_for_rem(b)
        res = dp.run_dream_cycle(b.path, config=_rem_cfg(self.tmp))
        by = {t: (n, s) for t, n, s in b.all(
            "SELECT dream_type, COUNT(*), SUM(dream_memory_id IS NOT NULL) FROM dream_log GROUP BY 1")}
        self.assertEqual(set(by), {"divergent", "close_context", "persona"}, (by, res))
        for t, (n, s) in by.items():
            self.assertGreater(s, 0, f"{t} has no stored dream")

    def test_derived_rows_are_never_selected_and_the_persona_pool_uses_self_tags(self):
        import memory_dreaming as md
        b = Brain(self.tmp)
        ids = _populate_for_rem(b)
        derived = {ids["oldream"], ids["digest"]}
        seen = set()
        for _ in range(40):
            for a, bb, *_r in md.select_divergent_pairs(b.c, count=7):
                seen |= {a, bb}
            for a, bb, *_r in md.select_close_context_pairs(b.c, count=7, distance_max=0.40):
                seen |= {a, bb}
            for a, bb, *_r in md.select_persona_pairs(b.c, count=2, persona_name="TESTPERSONA"):
                seen |= {a, bb}
                self.assertIn(a, {ids["self0"], ids["self1"], ids["self2"]},
                              "a persona pair's persona side is not a SELF-tagged row")
        self.assertEqual(seen & derived, set(), "a derived row was selected")
        # (a persona-design row may still meet the brain as an ordinary memory -- a divergent endpoint or a
        # persona pair's OTHER side; what it may never be is the persona side, asserted above)

    def test_the_time_box_fires_and_every_pool_still_gets_an_attempt(self):
        import dream_pipeline as dp
        b = Brain(self.tmp)
        _populate_for_rem(b)

        def slow(key, ctx):
            time.sleep(0.25)
            return _mouth()(key, ctx)
        res = dp.run_dream_cycle(b.path, config=_rem_cfg(self.tmp, _synthesis_mouth=slow, rem_time_box_s=0.1))
        rem = next(p for p in res["phases"] if p["phase"] == "rem_dreaming")
        pools = rem["result"]["pools"]
        self.assertTrue(rem["result"]["timeboxed"], rem)
        for k, v in pools.items():
            if v["planned"]:
                self.assertGreaterEqual(v["attempts"], 1, (k, pools))
        self.assertEqual(rem["status"], "timeout")

    def test_an_undeclared_persona_skips_the_pool_and_says_so(self):
        import dream_pipeline as dp
        b = Brain(self.tmp)
        _populate_for_rem(b)
        res = dp.run_dream_cycle(b.path, config=_rem_cfg(self.tmp, persona_name=None))
        rem = next(p for p in res["phases"] if p["phase"] == "rem_dreaming")["result"]
        self.assertEqual(rem["pools"]["persona"]["attempts"], 0)
        self.assertIn("skipped", rem["pool_notes"].get("persona", ""))


# =====================================================================================================================
def _recall_ns(query, top_k=5, leads=None, explore=False):
    return argparse.Namespace(query=query, top_k=top_k, threshold=None, category=None, project=None, kind=None,
                              hybrid=True, vector_only=False, bm25_only=False, no_rerank=False, multi_hop=0,
                              no_log_usage=False, no_lens=False, persona=None, no_cross_tier=True, no_boost=False,
                              no_ppr=False, no_diversity=False, no_temporal_rerank=False, explain=False,
                              leads=leads, explore=explore)


class _RerankSpy:
    def __init__(self):
        self.calls = 0
        self.pairs = 0
        self.ids = []

    def __call__(self, conn, query, candidate_ids, top_k=10):
        self.calls += 1
        ids = list(candidate_ids)
        self.pairs += len(ids)
        self.ids.extend(ids)
        q = fake_vec(query)
        qv = struct.unpack(f"{DIM}f", q)
        out = []
        for mid in ids:
            row = conn.execute("SELECT content FROM memories WHERE id=?", (mid,)).fetchone()
            v = struct.unpack(f"{DIM}f", fake_vec(row[0] if row else ""))
            out.append((mid, sum(x * y for x, y in zip(qv, v))))
        out.sort(key=lambda t: t[1], reverse=True)
        return out[:top_k]


def run_recall(db, query, top_k=5, leads=None, explore=False, spy=None):
    import claude_memory as cm
    import hybrid_retrieval as hr
    spy = spy or _RerankSpy()
    buf = io.StringIO()
    with mock.patch.object(cm, "DB_PATH", Path(db)), \
            mock.patch.object(cm, "_encode", lambda t, p="search_document": fake_vec(t)), \
            mock.patch.object(hr, "reranker_score", spy), contextlib.redirect_stdout(buf):
        cm.cmd_recall(_recall_ns(query, top_k, leads, explore))
    return json.loads(buf.getvalue().strip().splitlines()[-1]), spy


def _plant_channel_brain(b: Brain, n_dreams=3):
    ids = {}
    ids["m1"] = b.add("gradle build cache warm daemon speeds the pipeline", project="build")
    ids["m2"] = b.add("gradle daemon memory pressure kills the build", project="build")
    for i in range(8):
        b.add(f"unrelated filler memory {i} about gardening tomatoes and compost", project="garden")
    for i in range(n_dreams):
        d = b.add(f"[Dream synthesis — divergent]\nSource memories: #{ids['m1']} and #{ids['m2']}\n\n"
                  f"The gradle build cache and the daemon memory pressure share one cause variant {i}.",
                  summary=f"The gradle build cache and the daemon memory pressure share one cause {i}.",
                  kind="reflection", derived_from="dream_synthesis", confidence=0.40, project="memory-dreaming",
                  days_ago=i)
        b.c.execute("INSERT INTO dream_log (dream_type, memory_a_id, memory_b_id, dream_content, dream_memory_id, "
                    "is_meaningful, context_difference) VALUES ('divergent', ?, ?, 'x', ?, 1, ?)",
                    (ids["m1"], ids["m2"], d, json.dumps({"relation": "connection"})))
        ids[f"d{i}"] = d
    b.c.commit()
    return ids


class TestChannels(_TmpCase):
    def test_results_are_memories_only_and_the_dream_rides_in_its_own_key(self):
        b = Brain(self.tmp)
        ids = _plant_channel_brain(b)
        out, spy = run_recall(b.path, "gradle build cache daemon memory pressure")
        self.assertEqual(out["status"], "ok")
        rids = [r["id"] for r in out["results"]]
        dream_ids = {ids["d0"], ids["d1"], ids["d2"]}
        self.assertEqual(set(rids) & dream_ids, set(), "a dream in results")
        self.assertIn("dreams", out)
        self.assertEqual(len(out["dreams"]), 1, out["dreams"])
        d = out["dreams"][0]
        self.assertIn(d["id"], dream_ids)
        self.assertEqual(d["provenance"], "dream")
        self.assertTrue(d["content"].startswith("[dream — an unverified lead the brain synthesised on "), d)
        self.assertIn("not a recorded fact]", d["frame"])
        self.assertGreaterEqual(d["cosine"], 0.50)
        self.assertEqual(set(spy.ids) & dream_ids, set(), "the reranker scored a dream")

    def test_leads_zero_removes_the_key_and_quotas_hold(self):
        b = Brain(self.tmp)
        _plant_channel_brain(b)
        q = "gradle build cache daemon memory pressure"
        off, _ = run_recall(b.path, q, leads=0)
        self.assertNotIn("dreams", off)
        self.assertEqual(len(run_recall(b.path, q, top_k=10)[0]["dreams"]), 2)
        self.assertEqual(len(run_recall(b.path, q, explore=True)[0]["dreams"]), 3)
        self.assertLessEqual(len(run_recall(b.path, q, leads=9)[0]["dreams"]), 3, "the total side cap")

    def test_the_reranker_does_identical_work_with_the_channel_on_and_off(self):
        b = Brain(self.tmp)
        _plant_channel_brain(b)
        q = "gradle build cache daemon memory pressure"
        on_spy, off_spy = _RerankSpy(), _RerankSpy()
        run_recall(b.path, q, spy=on_spy)
        run_recall(b.path, q, leads=0, spy=off_spy)
        self.assertEqual((on_spy.calls, on_spy.pairs), (off_spy.calls, off_spy.pairs))

    def test_the_partition_happens_before_the_reranker(self):
        import hybrid_retrieval as hr
        b = Brain(self.tmp)
        ids = _plant_channel_brain(b)
        spy = _RerankSpy()
        seen = []
        with mock.patch.object(hr, "reranker_score", spy):
            hr.recall_hybrid(b.c, "gradle build cache daemon", fake_vec("gradle build cache daemon"), top_k=5,
                             divert=lambda xs: {x for x in xs if x in {ids["d0"], ids["d1"], ids["d2"]}},
                             diverted_out=seen)
        self.assertTrue(seen, "nothing was diverted")
        self.assertEqual(set(spy.ids) & set(seen), set())

    def test_a_faded_dream_below_the_gate_and_an_archived_endpoint_never_show(self):
        b = Brain(self.tmp)
        ids = _plant_channel_brain(b, n_dreams=1)
        b.c.execute("UPDATE memories SET created_at=? WHERE id=?", (_ts(90), ids["d0"]))   # 0.40 -> 0.05 -> 0.10
        b.c.commit()
        self.assertEqual(run_recall(b.path, "gradle build cache daemon memory pressure")[0]["dreams"], [])
        b.c.execute("UPDATE memories SET created_at=? WHERE id=?", (_ts(0), ids["d0"]))
        b.c.execute("UPDATE memories SET is_archived=1 WHERE id=?", (ids["m2"],))
        b.c.commit()
        self.assertEqual(run_recall(b.path, "gradle build cache daemon memory pressure")[0]["dreams"], [])

    def test_the_registry_is_generic_and_the_override_file_toggles_only(self):
        import recall_channels as rc
        b = Brain(self.tmp)
        _plant_channel_brain(b, n_dreams=1)
        n = b.add("a note synthesised by some other organ about gradle build cache daemon",
                  kind="reflection", derived_from="test_synthesis", confidence=0.5)
        reg = rc.registry(base={**rc.CHANNELS, "notes": {**rc.CHANNELS["playbooks"],
                                                         "predicate": {"column": "derived_from",
                                                                       "equals": "test_synthesis"},
                                                         "provenance": "note", "default": True,
                                                         "quota": {"default": 1, "top_k_ge_10": 1, "explore": 1},
                                                         "access_type": "recall:note-lead"}})
        out, diag = rc.build(b.c, query="gradle build cache daemon", query_vec=fake_vec("gradle build cache daemon"),
                             results=[], top_k=5, reg=reg)
        self.assertEqual([r["id"] for r in out["notes"]], [n], diag)
        self.assertIn(n, rc.divert_ids(b.c, [n], reg))
        (Path(b.path).parent / "recall_channels.json").write_text(json.dumps(
            {"dreams": {"enabled": False, "predicate": {"column": "id", "equals": 1}, "frame": "X"}}))
        r2 = rc.registry(str(Path(b.path).parent))
        self.assertFalse(r2["dreams"]["enabled"])
        self.assertEqual(r2["dreams"]["predicate"]["column"], "derived_from", "an override changed a predicate")
        self.assertTrue(r2["dreams"]["frame"].startswith("[dream"))
        self.assertIn("dreams", rc.hidden_channels(r2), "dreams OFF must stay out of results (when_off hidden)")

    def test_batch_recall_carries_the_channel_keys(self):
        from voidloom import _batch
        fake = {"status": "ok", "results": [{"id": 1}], "dreams": [{"id": 9, "channel": "dreams"}],
                "rerank": {"channels": {"dreams": {"shown": 1}, "total_side_cap": 3}}}
        with mock.patch("voidloom._facade.recall", return_value=fake):
            recs = _batch.recall_batch(["q"], db_path=str(self.tmp / "x.db"))
        self.assertEqual(recs[0]["dreams"], [{"id": 9, "channel": "dreams"}])
        self.assertEqual(recs[0]["results"], [{"id": 1}])


# =====================================================================================================================
class TestLabel(_TmpCase):
    def test_the_preamble_mirror_and_the_trusted_select(self):
        import recall_channels as rc
        import voidloom_preamble as vp
        self.assertEqual(vp.DREAM_FRAME, rc.CHANNELS["dreams"]["frame"])
        b = Brain(self.tmp)
        d = b.add("a dream", summary="DREAMSUMMARY", kind="reflection", derived_from="dream_synthesis",
                  confidence=0.65)
        p = b.add("a playbook", summary="PLAYBOOKSUMMARY", kind="procedural", derived_from="playbook_synthesis",
                  confidence=1.0)
        b.c.execute("UPDATE memories SET importance=1.0 WHERE id IN (?, ?)", (d, p))
        b.c.commit()
        got = {r[0] for r in b.all(vp._SELECT_TRUSTED, (50,))}
        self.assertNotIn(d, got)
        self.assertNotIn(p, got)


# =====================================================================================================================
class TestTensionFadeEvidence(_TmpCase):
    def test_relation_is_parsed_before_the_null_test(self):
        import memory_dreaming as md
        self.assertEqual(md.parse_relation("RELATION: connection\nNULL"), ("connection", "NULL"))
        self.assertTrue(md._is_null_body(md.parse_relation("RELATION: connection\nNULL")[1]))
        self.assertEqual(md.parse_relation("no header here, just a long enough body")[0], "connection")

        class R:
            text = "RELATION: connection\nNULL"
        with mock.patch.object(md._thinker, "think", return_value=R()):
            self.assertIsNone(md.dream_on_pair({"id": 1, "summary": "a", "content": "a", "category": "x",
                                                "source_project": "p", "created_at": "2026-01-01"},
                                               {"id": 2, "summary": "b", "content": "b", "category": "x",
                                                "source_project": "p", "created_at": "2026-01-01"},
                                               "divergent"))

    def test_a_narrated_body_under_the_header_takes_the_loud_path(self):
        import memory_dreaming as md

        class R:
            text = "RELATION: connection\nLet me read the file and check the directory for the config first."
        fails = []
        with mock.patch.object(md._thinker, "think", return_value=R()):
            out = md.dream_on_pair({"id": 1, "summary": "a", "content": "a", "category": "x",
                                    "source_project": "p", "created_at": "2026-01-01"},
                                   {"id": 2, "summary": "b", "content": "b", "category": "x",
                                    "source_project": "p", "created_at": "2026-01-01"},
                                   "divergent", on_failure=fails.append)
        if md._thinker.reply_screen("Let me read the file and check the directory for the config first.", "text"):
            self.assertIsNone(out)
            self.assertTrue(fails and "body" in fails[0])

    def test_a_contradiction_writes_the_tension_edge_and_a_null_is_not_stored(self):
        import dream_pipeline as dp
        b = Brain(self.tmp)
        _populate_for_rem(b)
        dp.run_dream_cycle(b.path, config=_rem_cfg(self.tmp, _synthesis_mouth=_mouth("contradiction")))
        pairs = b.all("SELECT memory_a_id, memory_b_id FROM dream_log WHERE is_meaningful=1")
        self.assertTrue(pairs)
        for a, bb in pairs:
            row = b.one("SELECT confidence, metadata FROM memory_edges WHERE predicate='references' AND "
                        "((source_id=? AND target_id=?) OR (source_id=? AND target_id=?))", (a, bb, bb, a))
            self.assertIsNotNone(row, (a, bb))
            meta = json.loads(row[1])
            self.assertTrue(meta.get("tag") == "tension" or meta.get("tension"))
        b2 = Brain(Path(tempfile.mkdtemp(dir=str(self.tmp))))
        _populate_for_rem(b2)
        dp.run_dream_cycle(b2.path, config=_rem_cfg(self.tmp, _synthesis_mouth=lambda k, c:
                                                      "RELATION: connection\nNULL",
                                                      lock_file=str(self.tmp / "l2.lock")))
        self.assertEqual(b2.one("SELECT COUNT(*) FROM dream_log WHERE is_meaningful=1")[0], 0)
        self.assertEqual(b2.one("SELECT COUNT(*) FROM memories WHERE derived_from='dream_synthesis' "
                                "AND content NOT LIKE '%old dream%'")[0], 0)

    def test_the_fade_is_derived_at_read_time(self):
        import dream_ledger as dl
        now = datetime.utcnow()
        self.assertAlmostEqual(dl.faded_confidence(0.40, _ts(0), now), 0.40, places=3)
        self.assertAlmostEqual(dl.faded_confidence(0.40, _ts(30), now), 0.20, places=3)
        self.assertAlmostEqual(dl.faded_confidence(0.40, _ts(60), now), 0.10, places=3)
        self.assertEqual(dl.faded_confidence(0.40, _ts(400), now), 0.10)
        self.assertAlmostEqual(dl.faded_confidence(1.0, _ts(0), now), 0.40, places=3,
                               msg="a hand-raised birth value is clamped")

    def test_the_three_evidence_paths_add_a_step_each_and_the_cap_holds(self):
        import dream_ledger as dl
        b = Brain(self.tmp)
        ids = _plant_channel_brain(b, n_dreams=1)
        d = ids["d0"]
        base = dl.effective_map(b.c, [d])[d]["effective"]
        self.assertAlmostEqual(base, 0.40, places=2)
        dl.confirm(b.c, d)
        self.assertAlmostEqual(dl.effective_map(b.c, [d])[d]["effective"], 0.50, places=2)
        ans = b.add("a recorded memory that settles the gradle question")
        self.assertEqual(dl.record_answer(b.c, ans, d)["status"], "ok")
        b.c.commit()
        self.assertAlmostEqual(dl.effective_map(b.c, [d])[d]["effective"], 0.60, places=2)
        b.c.execute("INSERT INTO edge_proposals (proposal_kind, source_id, target_id, predicate, status) "
                    "VALUES ('add_contradicts', ?, ?, 'contradicts', 'enacted')", (ids["m1"], ids["m2"]))
        b.c.commit()
        self.assertEqual(dl.effective_map(b.c, [d])[d]["effective"], 0.65, "the cap")
        self.assertEqual(b.one("SELECT confidence FROM memories WHERE id=?", (d,))[0], 0.40, "birth is immutable")
        self.assertEqual(dl.record_answer(b.c, d, d)["status"], "error", "a synthesis is not an answer")

    def test_a_recall_moves_nothing_but_the_met_ledger(self):
        b = Brain(self.tmp)
        ids = _plant_channel_brain(b, n_dreams=1)
        d = ids["d0"]
        tables = ("memories", "memory_edges", "memory_tags", "memory_provenance", "belief_scan_queue")

        def snap():
            s = {t: b.all(f"SELECT * FROM {t} ORDER BY 1") for t in tables}
            s["access_log"] = b.all("SELECT memory_id, access_type FROM access_log ORDER BY id")
            return s
        before = snap()
        out, _ = run_recall(b.path, "gradle build cache daemon memory pressure")
        self.assertEqual([x["id"] for x in out["dreams"]], [d])
        after = snap()
        # the dream row is untouched (no access_count / last_accessed bump); only lane-1 rows were bumped
        drow_b = [r for r in before["memories"] if r[0] == d]
        drow_a = [r for r in after["memories"] if r[0] == d]
        self.assertEqual(drow_b, drow_a)
        for t in ("memory_edges", "memory_tags", "memory_provenance", "belief_scan_queue"):
            self.assertEqual(before[t], after[t], t)
        new = after["access_log"][len(before["access_log"]):]
        self.assertIn((d, "recall:dream-lead"), new)
        self.assertEqual([r for r in new if r[0] == d], [(d, "recall:dream-lead")])

    def test_no_learning_path_reads_the_met_ledger(self):
        """Differential: every access_log reader, run on a brain WITH dream-lead rows and one WITHOUT, answers the
        same. (NREM co-recall, the activity profile, quickening, gap mapping, anti-recency stability, the probe
        labels, the self-eval recall count.)"""
        import dream_pipeline as dp
        import quickening as qk
        import gap_mapping as gm
        import anti_recency as ar
        import monthly_self_eval as mse
        # R1-L9: the GENERIC '-lead' rule is bound, not only the 'dream' machine marker the base code already
        # excluded for three readers: a whisper lead (the declared next channel) must be invisible too
        for at in ("recall:dream-lead", "recall:whisper-lead"):
            self.assertFalse(dp._is_real_session_access(at), at)
            self.assertFalse(qk._is_real_session_access_type(at), at)
        self.assertTrue(dp._is_real_session_access("recall:hybrid"))
        for lead_type in ("recall:dream-lead", "recall:whisper-lead"):
            self._differential(lead_type, dp, qk, gm, ar, mse)

    def _differential(self, lead_type, dp, qk, gm, ar, mse):
        outs = []
        for with_leads in (False, True):
            b = Brain(Path(tempfile.mkdtemp(dir=str(self.tmp))))
            ids = _plant_channel_brain(b, n_dreams=1)
            for i in range(6):
                for m in (ids["m1"], ids["m2"]):
                    b.c.execute("INSERT INTO access_log (memory_id, access_type, query_text, relevance_score, "
                                "accessed_at) VALUES (?, 'recall:hybrid', 'gradle build question', -0.9, ?)",
                                (m, _ts(0.01 * i)))
            if with_leads:
                for i in range(30):
                    for m in (ids["d0"], ids["m1"]):
                        b.c.execute("INSERT INTO access_log (memory_id, access_type, query_text, relevance_score, "
                                    "accessed_at) VALUES (?, ?, 'a lead query here', NULL, ?)",
                                    (m, lead_type, _ts(0.001 * i)))
            b.c.commit()
            cfg = {**dp.DEFAULT_CONFIG}
            r = {}
            r["nrem"] = dp.phase_nrem_consolidation(b.c, cfg, None)
            r["edges"] = b.all("SELECT source_id, target_id, confidence FROM memory_edges ORDER BY 1, 2")
            r["profile"] = dp.rebuild_activity_profile(b.c, cfg)
            r["gaps"] = gm.detect_weak_recalls(b.c)
            r["stability"] = ar._get_access_stability(b.c)
            r["labels"] = dp._derive_probe_labels(b.c, cfg)
            r["spike"] = qk.detect_recall_spike(b.c)
            r["cadence"] = qk.compute_access_cadence(b.c)
            r["self_eval_recall_count"] = mse._score_module_only_piece(
                b.c, "hybrid_retrieval", datetime.utcnow().strftime("%Y-%m"), "recall")
            outs.append(json.loads(json.dumps(r, default=str)))
        self.assertEqual(outs[0], outs[1], lead_type)


# =====================================================================================================================
class TestAlarm(_TmpCase):
    def _log(self, b, n, meaningful, with_id, days_ago=0.1, tag=None):
        a = b.add("endpoint a")
        bb = b.add("endpoint b")
        for _ in range(n):
            mid = None
            if with_id:
                mid = b.add("[Dream synthesis — divergent]\nx", kind="reflection", derived_from="dream_synthesis",
                            confidence=0.4, tags=((tag,) if tag else ()), vector=False)
            b.c.execute("INSERT INTO dream_log (dream_type, memory_a_id, memory_b_id, is_meaningful, "
                        "dream_memory_id, created_at) VALUES ('divergent', ?, ?, ?, ?, ?)",
                        (a, bb, meaningful, mid, _ts(days_ago)))
        b.c.commit()

    def test_the_row_invariant_verdicts(self):
        import dream_ledger as dl
        cases = [
            (dict(n=5, meaningful=0, with_id=False), "BROKEN"),        # attempts, 0 stored
            (dict(n=5, meaningful=1, with_id=False), "BROKEN"),        # meaningful without an id
            (dict(n=5, meaningful=1, with_id=True), "HEALTHY"),
            (dict(n=0, meaningful=0, with_id=False), "STARVED"),
            (dict(n=5, meaningful=1, with_id=True, tag="dream-backfill-20260928"), "STARVED"),  # backfill excluded
        ]
        for kw, want in cases:
            b = Brain(Path(tempfile.mkdtemp(dir=str(self.tmp))))
            self._log(b, **kw)
            if want == "STARVED":
                b.c.execute("INSERT INTO dream_phase_log (phase_name, status, detail, created_at) VALUES "
                            "('rem_dreaming', 'ok', '14 LLM call(s) failed; fell back', ?)", (_ts(0.2),))
                b.c.commit()
            w = dl.dream_watch(b.c)
            self.assertEqual(w["verdict"], want, (kw, w))
            if want == "STARVED":
                self.assertEqual(w["llm_failures"], 14)

    def test_the_met_warning_fires_and_clears(self):
        import dream_ledger as dl
        b = Brain(self.tmp)
        d = b.add("[Dream synthesis — divergent]\nx", kind="reflection", derived_from="dream_synthesis",
                  confidence=0.4, days_ago=15)
        # the fixed code's stored dream always carries its dream_log row (R1-M1: the WARN reads those)
        e1, e2 = b.add("endpoint one"), b.add("endpoint two")
        b.c.execute("INSERT INTO dream_log (dream_type, memory_a_id, memory_b_id, is_meaningful, dream_memory_id, "
                    "created_at) VALUES ('divergent', ?, ?, 1, ?, ?)", (e1, e2, d, _ts(15)))
        b.c.commit()
        self.assertTrue(dl.dream_watch(b.c)["met_warning"])
        b.c.execute("INSERT INTO access_log (memory_id, access_type) VALUES (?, 'recall:dream-lead')", (d,))
        b.c.commit()
        self.assertFalse(dl.dream_watch(b.c)["met_warning"])

    def test_the_sentinel_and_doctor_read_the_same_invariant(self):
        import organ_yield_sentinel as oys
        from voidloom import cli
        b = Brain(self.tmp)
        self._log(b, n=5, meaningful=1, with_id=False)
        b.c.close()
        rs, meta = oys.scan(b.path)
        r = next(x for x in rs if x.organ == "dream_pipeline.phase_rem_dreaming")
        self.assertEqual(r.verdict, "BROKEN")
        self.assertEqual(cli._doctor_dreams(b.path)["verdict"], "BROKEN")


# =====================================================================================================================
def _plant_old_dreams(b: Brain):
    ids = {}
    ids["a"] = b.add("queue retries note")
    ids["b"] = b.add("oven hydration note")
    ids["arch"] = b.add("retired note", archived=1)
    ids["web"] = b.add("web text", untrusted=1)
    for i in range(3):
        m = b.add(f"[Dream synthesis — divergent]\nSource memories: #{ids['a']} and #{ids['b']}\n\n"
                  f"Memory A (#{ids['a']}) and Memory B (#{ids['b']}) share a timing rule {i}.",
                  summary=f"Dream (divergent): memories #{ids['a']} + #{ids['b']}", kind="reflection",
                  project="memory-dreaming", confidence=1.0, vector=(i == 0))
        b.c.execute("INSERT INTO dream_log (dream_type, memory_a_id, memory_b_id, dream_memory_id, is_meaningful, "
                    "created_at) VALUES ('divergent', ?, ?, ?, 1, ?)", (ids["a"], ids["b"], m, _ts(100)))
        ids[f"old{i}"] = m
    ids["laptop"] = b.add("[Dream synthesis — close_context]\nSource memories: #9 and #10\n\nA laptop dream.",
                          kind="reflection", project="memory-dreaming", confidence=1.0, vector=False)
    ids["protected"] = b.add("Sleep loop pattern lesson", tags=("dream",), kind="procedural", confidence=0.85)
    for n, (x, y, dago) in enumerate([(ids["a"], ids["b"], 3), (ids["a"], ids["arch"], 4), (ids["a"], ids["web"], 5),
                                      (ids["a"], ids["b"], 40)]):
        b.c.execute("INSERT INTO dream_log (dream_type, memory_a_id, memory_b_id, dream_content, is_meaningful, "
                    "created_at) VALUES ('divergent', ?, ?, ?, 1, ?)",
                    (x, y, f"The two notes {n} disagree about when to retry a failed bake.", _ts(dago)))
    b.c.commit()
    return ids


class TestBackfill(_TmpCase):
    def test_plan_apply_rollback(self):
        import dream_ledger as dl
        b = Brain(self.tmp)
        ids = _plant_old_dreams(b)
        q0 = b.one("SELECT COUNT(*) FROM belief_scan_queue")[0]
        p = dl.plan(b.c)
        self.assertEqual(p["restamp"]["rows"], 4)
        self.assertEqual(p["restamp"]["to_restamp"], 4)
        self.assertEqual(p["restamp"]["linked"], 3)
        self.assertEqual(p["restamp"]["unlinked_no_links_invented"], 1)
        self.assertEqual(p["restamp"]["dream_tagged_rows_outside_selector_untouched"], 1)
        self.assertEqual(p["backfill"]["rows"], 2)                       # 30 nights, archived parent out
        self.assertEqual(p["backfill"]["excluded_archived_or_missing_parent"], 1)
        self.assertEqual(p["backfill"]["tainted_carried"], 1)
        pre = str(self.tmp / "pre.json")
        r = dl.apply(b.c, p, ["restamp", "backfill"], pre, encoder=fake_encoder, stamp_date="20260928")
        self.assertEqual(r["status"], "ok", r)
        for k in ("old0", "old1", "old2", "laptop"):
            conf, df, summ = b.one("SELECT confidence, derived_from, summary FROM memories WHERE id=?", (ids[k],))
            self.assertEqual((conf, df), (0.40, "dream_synthesis"))
            self.assertNotRegex(summ or "", r"[Mm]emory [AB]\b|memories #\d+")
            self.assertIsNotNone(b.one("SELECT 1 FROM memory_embeddings_v2 WHERE memory_id=?", (ids[k],)), k)
        self.assertEqual(b.one("SELECT COUNT(*) FROM memory_provenance WHERE derived_memory_id=?",
                               (ids["laptop"],))[0], 0, "a link was invented for the laptop row")
        self.assertEqual(b.one("SELECT COUNT(*) FROM memory_provenance WHERE derived_memory_id=?",
                               (ids["old1"],))[0], 2)
        self.assertEqual(b.one("SELECT confidence, derived_from FROM memories WHERE id=?", (ids["protected"],)),
                         (0.85, None))
        bf = b.all("SELECT m.id, m.untrusted_source, m.created_at FROM memories m JOIN memory_tags t "
                   "ON t.memory_id=m.id WHERE t.tag='dream-backfill-20260928'")
        self.assertEqual(len(bf), 2)
        self.assertEqual(sum(x[1] for x in bf), 1, "taint carried")
        self.assertEqual(b.one("SELECT COUNT(*) FROM belief_scan_queue")[0], q0, "the scan queue moved")
        p2 = dl.plan(b.c)
        self.assertEqual(p2["restamp"]["rows"], 6, "the 4 old rows + the 2 backfilled rows match the selector")
        self.assertEqual(p2["restamp"]["to_restamp"], 0, "a re-run of the verb would touch a stamped row")
        w = dl.dream_watch(b.c, window_days=10)
        # 3 historical rows in the window: 2 now backfilled (EXCLUDED), 1 with an archived parent (still no id)
        self.assertEqual((w["attempts"], w["stored"]), (1, 0), w)
        self.assertNotEqual(w["verdict"], "HEALTHY", "the backfill forged a pass")
        # rollback EXECUTED
        rb = dl.rollback(b.c, json.load(open(pre)))
        self.assertEqual(rb["backfill_archived"], 2)
        self.assertEqual(b.one("SELECT confidence, derived_from FROM memories WHERE id=?", (ids["old0"],)),
                         (1.0, None))
        self.assertEqual(b.one("SELECT COUNT(*) FROM memories m JOIN memory_tags t ON t.memory_id=m.id "
                               "WHERE t.tag='dream-backfill-20260928' AND m.is_archived=0")[0], 0)
        self.assertEqual(b.one("SELECT COUNT(*) FROM dream_log WHERE dream_memory_id IN (SELECT memory_id FROM "
                               "memory_tags WHERE tag='dream-backfill-20260928')")[0], 0)


class TestPlaybooks(_TmpCase):
    def test_dedup_archives_never_deletes_and_emission_is_idempotent(self):
        import dream_ledger as dl
        import dream_pipeline as dp
        b = Brain(self.tmp)
        for proj in ("alpha", "beta"):
            for i in range(4):
                b.add(f"procedure {i} for {proj}", kind="procedural", project=proj)
        cfg = {**dp.DEFAULT_CONFIG}
        seqs = dp._detect_repeated_sequences(b.c, cfg)
        self.assertEqual(sorted(x[0] for x in seqs), ["alpha", "beta"])
        for _ in range(3):   # the base behaviour, simulated: three nights of re-emission, pre-fix
            for label, occ, ev in seqs:
                content = (f"[Playbook -- dream-synthesis, unvalidated]\nA repeated procedural pattern was observed "
                           f"in [{label}] across {occ} procedural memories. Candidate for graduation behind the "
                           f"raised dream-authored evidence bar (evidence={ev}).")
                b.add(content, summary=f"Playbook candidate [{label}] ({occ}x)", kind="procedural",
                      project="dream-pipeline", category="workflow_pattern", vector=False)
        total0 = b.one("SELECT COUNT(*) FROM memories")[0]
        p = dl.plan(b.c)
        self.assertEqual((p["playbooks"]["active_rows"], p["playbooks"]["kept"], p["playbooks"]["archive"]),
                         (6, 2, 4))
        pre = str(self.tmp / "pb.json")
        # R1-L5: the STAMP rides on the re-stamp (part of the fix); --playbooks archives duplicates only
        dl.apply(b.c, p, ["restamp", "playbooks"], pre, stamp_date="20260928")
        self.assertEqual(b.one("SELECT COUNT(*) FROM memories")[0], total0, "a row was deleted")
        self.assertEqual(b.one(f"SELECT COUNT(*) FROM memories WHERE {dl.PLAYBOOK_WHERE} AND is_archived=0")[0], 2)
        self.assertEqual(b.all(f"SELECT DISTINCT derived_from, confidence FROM memories WHERE {dl.PLAYBOOK_WHERE} "
                               f"AND is_archived=0"), [("playbook_synthesis", 0.40)])
        res = dp.phase_playbook_emission(b.c, cfg, None)
        self.assertEqual((res["emitted"], res["duplicates_skipped"]), (0, 2), res)
        dl.rollback(b.c, json.load(open(pre)))
        self.assertEqual(b.one(f"SELECT COUNT(*) FROM memories WHERE {dl.PLAYBOOK_WHERE} AND is_archived=0")[0], 6)
        self.assertEqual(b.all(f"SELECT DISTINCT derived_from, confidence FROM memories WHERE {dl.PLAYBOOK_WHERE}"),
                         [(None, 1.0)], "the rollback restores the stamp too")


if __name__ == "__main__":
    unittest.main()
