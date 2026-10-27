#!/usr/bin/env python3
"""test_dreamfix_r2w2a.py -- the dream fix's reflection findings (Tranche A iteration 1, ruler VL-DF-v1.1), fixed in
model-choice Round 2 wave 2A, plus the floor decision (DREAM_FLOOR_DECISION.md, 2026-09-28).

Every brain here is the PACKAGE's (the same packaged-brain + bag-of-words helpers as test_dreamfix_tranche_a.py):
no model, no GPU, no network, no host brain. Each class binds ONE finding and is RED on the merged tree before
wave 2A's fixes (main 0b061d7 + the model-choice wave-1 bytes + the dream-fix series).

  R1-M1  TestLegacyRowsAreReportedNotJudged   pre-fix dream_log rows never make a landed fix read BROKEN
  R1-M2  TestTheFloorIsTheDreamsOwn           one absolute COSINE_FLOOR; never "out-score the weakest memory"
  R1-M3  TestSurfacingReport                  shown-rate over the met ledger + the corpus over time, in doctor
  R1-M4  TestTensionLinkIsNeverAttention      NREM never strengthens it; anti_recency never counts it
  R1-M5  TestRetirementIsNotEvidence          an approved invalidate_edge / supersede is never evidence FOR a dream
  R1-M6  TestStoreAnswersOnThePrimaryVerbs    `voidloom store --answers` and batch-store `answers`
  R1-M7  TestRollbackRemovesWhatItAdded       the re-stamp's vectors / provenance rows go with the rollback
  R1-M8  TestAnticipationNeverPreloadsADream  cmd_anticipate diverts; the vector fallback excludes synthesis
  R1-L1  TestOneLineRelation                  'RELATION: tension. <body>' keeps its body
  R1-L2  TestWhispersDeclaredOff              declared OFF, when_off 'lane1' (digests stay ordinary memories)
  R1-L3  TestOnePlaybookPerLabel              emission + dedup keyed on the label
  R1-L4  TestRaptorSummariesOfSynthesis       the RAPTOR summaries of synthesis clusters are archived with them
  R1-L5  TestPlaybookStampRidesTheRestamp     the stamp is the re-stamp's; --playbooks archives only
  R1-L6  TestChannelScansOnlyEligibleRows     the per-recall scan is bounded by age-or-evidence
  R1-L7  TestMultiHopNeverFeedsTheReranker    an expanded dream is diverted before it is hydrated
  R1-L8  TestTaintIsVisibleAndShowIsSanitised the untrusted mark in the channel row; `dream show` sanitised
  R1-L12 TestTheKnobReadsTheTopFiveByRank     the (default-off) relative clause reads the top 5 by score
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
for _p in (str(_ROOT), str(_ROOT / "organs"), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from test_dreamfix_tranche_a import (  # noqa: E402  (helpers only -- never a Test* class, so nothing re-collects)
    Brain, _RerankSpy, _TmpCase, _plant_channel_brain, _plant_old_dreams, _populate_for_rem, _rem_cfg, _recall_ns,
    _ts, fake_encoder, fake_vec, run_recall)


def _dream(b: Brain, a: int, bb: int, text: str, days_ago: float = 0.0, batch=None, with_log=True, **kw) -> int:
    d = b.add("[Dream synthesis — divergent]\nSource memories: #%d and #%d\n\n%s" % (a, bb, text), summary=text,
              kind="reflection", derived_from="dream_synthesis", confidence=0.40, project="memory-dreaming",
              days_ago=days_ago, **kw)
    if with_log:
        b.c.execute("INSERT INTO dream_log (dream_type, memory_a_id, memory_b_id, dream_content, dream_memory_id, "
                    "is_meaningful, batch_id, created_at) VALUES ('divergent', ?, ?, 'x', ?, 1, ?, ?)",
                    (a, bb, d, batch, _ts(days_ago)))
        b.c.commit()
    return d


# =====================================================================================================================
class TestLegacyRowsAreReportedNotJudged(_TmpCase):
    """R1-M1: dream_watch counted every row in the window, including rows the UNFIXED code wrote before landing, so a
    landed fix read BROKEN for window_days whether or not it worked (and the Oct 1 09:15 proof depended on the owner
    taking the optional backfill)."""

    def _legacy(self, b: Brain, n: int):
        a, bb = b.add("legacy endpoint a"), b.add("legacy endpoint b")
        # the UNFIXED code's batch (a real dream_batches row, as the host's 14:00 / 22:00 runs write), no 'pools'
        batch = b.c.execute("INSERT INTO dream_batches (config_snapshot, created_at) VALUES ('{}', ?)",
                            (_ts(0.5),)).lastrowid
        b.c.execute("INSERT INTO dream_phase_log (batch_id, phase_name, status, result_json, created_at) VALUES "
                    "(?, 'rem_dreaming', 'ok', ?, ?)", (batch, json.dumps({"status": "ok", "dreamed": n}), _ts(0.5)))
        for _ in range(n):
            b.c.execute("INSERT INTO dream_log (dream_type, memory_a_id, memory_b_id, dream_content, is_meaningful, "
                        "batch_id, created_at) VALUES ('divergent', ?, ?, 'an old dream the old code never stored', "
                        "1, ?, ?)", (a, bb, batch, _ts(0.5)))
        b.c.commit()

    def test_a_fixed_rem_batch_makes_the_watch_judge_only_its_own_rows(self):
        import dream_ledger as dl
        import dream_pipeline as dp
        b = Brain(self.tmp)
        self._legacy(b, 5)
        self.assertEqual(dl.dream_watch(b.c)["verdict"], "BROKEN", "no fixed batch yet: every row is judged")
        _populate_for_rem(b)
        res = dp.run_dream_cycle(b.path, config=_rem_cfg(self.tmp))
        self.assertEqual(res["status"], "ok", res)
        c = sqlite3.connect(b.path)
        w = dl.dream_watch(c)
        self.assertEqual(w["verdict"], "HEALTHY", w)
        self.assertEqual(w["legacy_meaningful_without_id"], 5, w)
        self.assertIsNotNone(w.get("fixed_since_batch"))
        self.assertIn("5 pre-fix row(s) without an id", w["detail"], "reported where the sentinel and doctor read it")

    def test_a_brain_that_never_ran_fixed_is_judged_on_every_row(self):
        import dream_ledger as dl
        b = Brain(self.tmp)
        self._legacy(b, 5)
        w = dl.dream_watch(b.c)
        self.assertEqual(w["verdict"], "BROKEN", w)
        self.assertNotIn("fixed_since_batch", w)

    def test_the_met_warning_ignores_dreams_stored_before_the_fixed_batch(self):
        import dream_ledger as dl
        b = Brain(self.tmp)
        a, bb = b.add("endpoint one"), b.add("endpoint two")
        _dream(b, a, bb, "an old re-stamped dream from May", days_ago=100, batch=1)
        self.assertTrue(dl.dream_watch(b.c)["met_warning"], "no fixed batch: the old stored dream counts")
        b.c.execute("INSERT INTO dream_phase_log (batch_id, phase_name, status, result_json, created_at) VALUES "
                    "(2, 'rem_dreaming', 'ok', ?, ?)", (json.dumps({"status": "ok", "pools": {}}), _ts(0.1)))
        b.c.commit()
        self.assertFalse(dl.dream_watch(b.c)["met_warning"], "a re-stamped May dream fired the WARN on landing day")


# =====================================================================================================================
class TestTheFloorIsTheDreamsOwn(_TmpCase):
    """R1-M2 + the floor decision: the relative clause is a knob that defaults OFF (ruling 2: never by out-scoring a
    memory); the ABSOLUTE 0.50 is ONE named setting every channel references."""

    def test_one_named_floor_every_channel_references(self):
        import recall_channels as rc
        self.assertEqual(rc.COSINE_FLOOR, 0.50)
        for name, spec in rc.CHANNELS.items():
            self.assertIs(spec["floor"]["cosine_min"], rc.COSINE_FLOOR, name)
            self.assertFalse(spec["floor"]["at_least_weakest_top5_memory"], name)
        src = Path(rc.__file__).read_text(encoding="utf-8")
        lits = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Constant) and n.value in (0.5,)
                and not isinstance(n.value, bool)]
        self.assertEqual(len(lits), 1, "the floor is typed in more than one place")

    def test_a_dream_below_the_weakest_memory_but_above_the_floor_is_shown(self):
        import recall_channels as rc
        b = Brain(self.tmp)
        q = "gradle build cache daemon memory pressure"
        mems = [b.add(q + " exact note %d" % i) for i in range(5)]         # lane 1: near-identical to the query
        a, bb = mems[0], mems[1]
        d = _dream(b, a, bb, "gradle build cache daemon memory pressure share one cause")   # cosine ~0.63
        results = [{"id": m, "score": 1.0 - 0.01 * i} for i, m in enumerate(mems)]
        out, diag = rc.build(b.c, query=q, query_vec=fake_vec(q), results=results, top_k=5)
        cos = diag["dreams"]
        self.assertIsNotNone(cos.get("weakest_top5_memory_cosine"), "the diagnostic stays")
        self.assertGreater(cos["weakest_top5_memory_cosine"], 0.8)
        self.assertEqual([r["id"] for r in out["dreams"]], [d], diag)
        self.assertEqual(cos["floor"], 0.5)


# =====================================================================================================================
class TestSurfacingReport(_TmpCase):
    """R1-M3 (owner ruling 3): a REPORT over the met ledger -- shown-rate per recall-with-results, the corpus over
    time, the dreams eligible under the gate -- in the watch and on doctor's dreams line."""

    def test_the_report_counts_recalls_showings_and_the_corpus(self):
        import dream_ledger as dl
        b = Brain(self.tmp)
        ids = _plant_channel_brain(b, n_dreams=2)
        old = _dream(b, ids["m1"], ids["m2"], "an old dream past the half-life", days_ago=60)
        for i in range(4):                       # 4 recalls with results; the 2nd and 4th showed a dream
            at = _ts(1 + i * 0.1)
            b.c.execute("INSERT INTO access_log (memory_id, access_type, query_text, accessed_at) VALUES "
                        "(?, 'recall:hybrid', ?, ?)", (ids["m1"], "q%d" % i, at))
            if i % 2:
                b.c.execute("INSERT INTO access_log (memory_id, access_type, query_text, accessed_at) VALUES "
                            "(?, 'recall:dream-lead', ?, ?)", (ids["d0"], "q%d" % i, at))
        b.c.commit()
        s = dl.dream_watch(b.c)["surfacing_7d"]
        self.assertEqual((s["recalls_with_results"], s["recalls_showing_a_dream"], s["dreams_shown"]), (4, 2, 2), s)
        self.assertEqual(s["shown_rate"], 0.5)
        self.assertEqual(s["dream_corpus"], 3)
        self.assertEqual(s["dreams_within_half_life"], 2)
        self.assertEqual(s["dreams_eligible_under_gate"], 2, "the 60-day dream (0.10) is below the 0.20 gate")
        self.assertEqual(s["dream_corpus_over_time"]["28d_ago"], 1)
        self.assertGreater(s["authored_memories"], 0)
        self.assertIsNotNone(old)

    def test_doctor_names_the_shown_rate(self):
        from voidloom import cli
        b = Brain(self.tmp)
        _plant_channel_brain(b, n_dreams=1)
        b.c.close()
        line = cli._doctor_dreams(b.path)["line"]
        self.assertIn("7d: shown on 0 of 0 recalls, corpus 1 dreams", line)


# =====================================================================================================================
class TestTensionLinkIsNeverAttention(_TmpCase):
    """R1-M4: the 0.2 tension link was strengthened by NREM co-recall (0.2000 -> 0.2199) and counted in anti_recency's
    degree (which feeds recall's foundation boost)."""

    def _tension_brain(self):
        b = Brain(self.tmp)
        a, bb = sorted((b.add("kafka retries note for the tension pair"), b.add("oven hydration note tension pair")))
        b.c.execute("INSERT INTO memory_edges (source_id, target_id, predicate, confidence, valid_from, created_at, "
                    "extraction_method, metadata) VALUES (?, ?, 'references', 0.2, datetime('now'), datetime('now'), "
                    "'sleep_loop', ?)", (a, bb, json.dumps({"tag": "tension", "origin": "dream-synthesis",
                                                            "relation": "tension"})))
        for s in range(12):                  # co-recall sessions: the pair recalled together, many times
            for m in (a, bb):
                b.c.execute("INSERT INTO access_log (memory_id, access_type, query_text, relevance_score, accessed_at)"
                            " VALUES (?, 'recall:hybrid', ?, -0.9, ?)", (m, "session %d" % s, _ts(0.3 + 0.05 * s)))
        b.c.commit()
        return b, a, bb

    def test_nrem_co_recall_never_strengthens_the_tension_link(self):
        import dream_pipeline as dp
        b, a, bb = self._tension_brain()
        dp.phase_nrem_consolidation(b.c, {**dp.DEFAULT_CONFIG}, None)
        rows = b.all("SELECT confidence, metadata FROM memory_edges WHERE source_id=? AND target_id=? "
                     "AND predicate='references'", (a, bb))
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0][0], 0.2, rows)
        self.assertNotIn("nrem", json.loads(rows[0][1]), "NREM merged its co-recall mark into the tension link")

    def test_anti_recency_degree_never_counts_it_and_survives_bad_metadata(self):
        import anti_recency as ar
        b, a, bb = self._tension_brain()
        self.assertEqual(ar._get_edge_counts(b.c).get(a, 0), 0, "the tension link raised a foundation score")
        c = b.add("a third note")
        b.c.execute("INSERT INTO memory_edges (source_id, target_id, predicate, confidence, valid_from, created_at, "
                    "extraction_method, metadata) VALUES (?, ?, 'depends_on', 0.7, datetime('now'), datetime('now'), "
                    "'manual', 'not json at all')", (a, c))
        b.c.commit()
        counts = ar._get_edge_counts(b.c)
        self.assertEqual((counts.get(a, 0), counts.get(c, 0)), (1, 1), "a malformed metadata row broke the degree")


# =====================================================================================================================
class TestRetirementIsNotEvidence(_TmpCase):
    """R1-M5: an approved invalidate_edge / supersede RETIRES a link; both copies of the rule counted it FOR the
    dream (+0.10)."""

    def test_the_ledger_counts_only_add_contradicts(self):
        import dream_ledger as dl
        b = Brain(self.tmp)
        a, bb = b.add("endpoint a for evidence"), b.add("endpoint b for evidence")
        d = _dream(b, a, bb, "a dream about the two endpoints")
        e0 = dl.effective_map(b.c, [d])[d]["effective"]
        for kind in ("invalidate_edge", "supersede"):
            b.c.execute("INSERT INTO edge_proposals (proposal_kind, source_id, target_id, predicate, status) "
                        "VALUES (?, ?, ?, 'references', 'enacted')", (kind, a, bb))
        b.c.commit()
        self.assertEqual(dl.effective_map(b.c, [d])[d]["effective"], e0, "a retirement raised the dream")
        b.c.execute("INSERT INTO edge_proposals (proposal_kind, source_id, target_id, predicate, status) "
                    "VALUES ('add_contradicts', ?, ?, 'contradicts', 'enacted')", (a, bb))
        b.c.commit()
        self.assertAlmostEqual(dl.effective_map(b.c, [d])[d]["effective"], e0 + 0.10, places=3)

    def test_the_channel_eligibility_counts_only_add_contradicts(self):
        import recall_channels as rc
        b = Brain(self.tmp)
        a, bb = b.add("endpoint a for the channel"), b.add("endpoint b for the channel")
        d = _dream(b, a, bb, "an old dream below the gate", days_ago=45)       # 0.40 * 0.5**1.5 = 0.14 < 0.20
        b.c.execute("INSERT INTO edge_proposals (proposal_kind, source_id, target_id, predicate, status) "
                    "VALUES ('invalidate_edge', ?, ?, 'references', 'approved')", (a, bb))
        b.c.commit()
        self.assertNotIn(d, rc._eligible_dreams(b.c, rc.CHANNELS["dreams"], datetime.utcnow()))


# =====================================================================================================================
class TestStoreAnswersOnThePrimaryVerbs(_TmpCase):
    """R1-M6: `store --answers` worked only through the engine script; `voidloom store` refused the flag and
    batch-store refused the field, so an agent following the doctrine could never settle a dream."""

    def test_the_cli_accepts_answers(self):
        from voidloom import cli
        ns = cli.build_parser().parse_args(["store", "--content", "x", "--category", "insight", "--answers", "5,6"])
        self.assertEqual(ns.answers, "5,6")

    def test_voidloom_store_answers_settles_the_dream(self):
        from voidloom import _facade, cli
        import dream_ledger as dl
        b = Brain(self.tmp)
        a, bb = b.add("endpoint a settled"), b.add("endpoint b settled")
        d = _dream(b, a, bb, "a dream the owner then settled")
        e0 = dl.effective_map(b.c, [d])[d]["effective"]
        engine = _facade._import_engine()
        ns = cli.build_parser().parse_args(["--db", b.path, "store", "--content", "we measured it and the two notes "
                                            "do share the timing rule", "--category", "insight",
                                            "--answers", str(d), "--json"])
        with mock.patch.object(engine, "_encode", lambda t, p="search_document": fake_vec(t)):
            env = cli.verb_store(ns)
        self.assertEqual(env["status"], "ok", env)
        self.assertEqual(env["data"]["answers"][0]["status"], "ok", env["data"])
        c = sqlite3.connect(b.path)
        import sqlite_vec
        c.enable_load_extension(True)
        sqlite_vec.load(c)
        self.assertAlmostEqual(dl.effective_map(c, [d])[d]["effective"], e0 + 0.10, places=3)

    def test_batch_store_items_carry_answers(self):
        from voidloom import _batch
        kw = _batch._validate_store_item({"content": "c", "category": "insight", "answers": [7, 8]})
        self.assertEqual(kw["answers"], "7,8")
        self.assertIsNone(_batch._validate_store_item({"content": "c", "category": "insight"})["answers"])

    def test_a_shown_dream_says_how_to_settle_it(self):
        b = Brain(self.tmp)
        _plant_channel_brain(b, n_dreams=1)
        out, _ = run_recall(b.path, "gradle build cache daemon memory pressure")
        self.assertIn("--answers %d" % out["dreams"][0]["id"], out["dreams"][0]["settle"])


# =====================================================================================================================
class TestRollbackRemovesWhatItAdded(_TmpCase):
    """R1-M7: the rollback restored the re-stamped fields but kept the vectors / provenance rows the apply added, so
    a rolled-back brain exposed MORE unlabelled 1.0 dreams to the vector leg than before the apply."""

    def test_after_apply_and_rollback_the_selector_rows_have_their_pre_apply_index(self):
        import dream_ledger as dl
        b = Brain(self.tmp)
        ids = _plant_old_dreams(b)
        sel = [ids[k] for k in ("old0", "old1", "old2", "laptop")]
        q = ",".join("?" * len(sel))

        def counts():
            return (b.one(f"SELECT COUNT(*) FROM memory_embeddings_v2 WHERE memory_id IN ({q})", sel)[0],
                    b.one(f"SELECT COUNT(*) FROM embedding_provenance WHERE memory_id IN ({q})", sel)[0],
                    b.one(f"SELECT COUNT(*) FROM memory_provenance WHERE derived_memory_id IN ({q})", sel)[0])
        before = counts()
        p = dl.plan(b.c)
        pre = str(self.tmp / "pre.json")
        r = dl.apply(b.c, p, ["restamp"], pre, encoder=fake_encoder, stamp_date="20260928")
        self.assertEqual(r["status"], "ok", r)
        self.assertGreater(counts()[0], before[0], "the apply added no vector (the test cannot fail)")
        rb = dl.rollback(b.c, json.load(open(pre)))
        self.assertEqual(counts(), before, rb)
        self.assertEqual(b.one("SELECT COUNT(*) FROM memories")[0] >= len(sel), True)
        self.assertEqual(b.one("SELECT confidence, derived_from FROM memories WHERE id=?", (ids["old0"],)),
                         (1.0, None))


    def test_the_restamp_embeds_the_body_as_the_store_does(self):
        """Wave 2A class sweep (what text a dream is embedded from): the store embeds the BODY; the re-stamp embedded
        the whole content, header included."""
        import dream_ledger as dl
        b = Brain(self.tmp)
        ids = _plant_old_dreams(b)
        p = dl.plan(b.c)
        dl.apply(b.c, p, ["restamp"], str(self.tmp / "pre.json"), encoder=fake_encoder, stamp_date="20260928")
        content = b.one("SELECT content FROM memories WHERE id=?", (ids["old1"],))[0]
        vec = b.one("SELECT embedding FROM memory_embeddings_v2 WHERE memory_id=?", (ids["old1"],))[0]
        self.assertEqual(vec, fake_vec(dl.embedding_text(dl.dream_body(content))))
        self.assertNotEqual(vec, fake_vec(content))

    def test_a_dream_is_embedded_without_the_thinkers_framing(self):
        """The floor decision's endpoint-embedding measurement, ADOPTED: the vector's text drops the 'Both memories
        ...:' opener and the 'Memory A/B' / '#id' tokens; the stored content keeps them."""
        import dream_ledger as dl
        import dream_pipeline as dp
        body = ("Both memories encode the same underlying discipline: distrust the coarse proxy and verify the granular "
                "reality. Memory A checks inputs before trusting a finding, while Memory B rejects #1234 estimates.")
        t = ("distrust the coarse proxy and verify the granular reality. checks inputs before trusting a finding, "
             "while rejects estimates.")                              # the measured F2 cleaning, spelled out
        b = Brain(self.tmp)
        _populate_for_rem(b)
        mouth = lambda key, _ctx: "RELATION: connection\n" + body
        dp.run_dream_cycle(b.path, config=_rem_cfg(self.tmp, _synthesis_mouth=mouth))
        c = sqlite3.connect(b.path)
        import sqlite_vec
        c.enable_load_extension(True)
        sqlite_vec.load(c)
        mid, content = c.execute("SELECT id, content FROM memories WHERE derived_from='dream_synthesis' "
                                 "AND content LIKE '%distrust the coarse proxy%' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIn("Both memories", content, "the stored content is untouched")
        vec = c.execute("SELECT embedding FROM memory_embeddings_v2 WHERE memory_id=?", (mid,)).fetchone()[0]
        self.assertEqual(vec, fake_vec(t), "the stored dream's vector is not the framing-free text")   # behaviour first
        self.assertEqual(dl.embedding_text(body), t)
        for tok in ("Both memories", "Memory A", "Memory B", "#1234"):
            self.assertNotIn(tok, dl.embedding_text(body))
        self.assertEqual(dl.embedding_text("A short plain body with no framing at all, twenty-plus."),
                         "A short plain body with no framing at all, twenty-plus.")


# =====================================================================================================================
class TestAnticipationNeverPreloadsADream(_TmpCase):
    """R1-M8: cmd_anticipate handed the bare recall_hybrid to pre_cognitive_recall (no divert), and the vector
    fallback had no provenance filter, so a dream reached the banner's 'anticipated' line with an access bump."""

    def test_the_vector_fallback_excludes_synthesis(self):
        import pre_cognitive_recall as pcr
        b = Brain(self.tmp)
        ids = _plant_channel_brain(b, n_dreams=1)
        hits = pcr._vector_search_fallback(b.c, fake_vec(b.one("SELECT content FROM memories WHERE id=?",
                                                                  (ids["d0"],))[0]), top_k=5)
        self.assertTrue(hits)
        self.assertNotIn(ids["d0"], [h["id"] for h in hits])

    def test_cmd_anticipate_diverts_before_anything_is_loaded(self):
        import claude_memory as cm
        import pre_cognitive_recall as pcr
        b = Brain(self.tmp)
        ids = _plant_channel_brain(b, n_dreams=1)
        dvec = fake_vec(b.one("SELECT content FROM memories WHERE id=?", (ids["d0"],))[0])
        acc0 = b.one("SELECT access_count FROM memories WHERE id=?", (ids["d0"],))[0]
        seen = {}

        def fake_anticipatory(conn, focus_vec, encode_fn, top_k, show_anticipated, recall_hybrid_fn, signals):
            hits = pcr._single_recall(conn, "gradle build cache daemon", dvec, recall_hybrid_fn, top_k=10)
            seen["ids"] = [h["id"] for h in hits]
            return {"memories": hits, "memories_loaded": len(hits), "ambiguous": False, "tiers": {}}
        buf = io.StringIO()
        with mock.patch.object(cm, "DB_PATH", Path(b.path)), \
                mock.patch.object(pcr, "collect_signals", lambda project_root=None: {}), \
                mock.patch.object(pcr, "compute_focus_vector", lambda s, e: dvec), \
                mock.patch.object(pcr, "anticipatory_recall", fake_anticipatory), \
                mock.patch.object(cm, "_encode", lambda t, p="search_document": fake_vec(t)), \
                contextlib.redirect_stdout(buf):
            cm.cmd_anticipate(argparse.Namespace(project_root=None, top_k=10, show_anticipated=False, log=False,
                                                 session_id=None))
        self.assertTrue(seen.get("ids"), "nothing was anticipated (the test cannot fail)")
        self.assertNotIn(ids["d0"], seen["ids"])
        c = sqlite3.connect(b.path)
        self.assertEqual(c.execute("SELECT access_count FROM memories WHERE id=?", (ids["d0"],)).fetchone()[0], acc0)


# =====================================================================================================================
class TestOneLineRelation(unittest.TestCase):
    """R1-L1: the header regex ate the whole first line, so a one-line reply was judged NULL and lost silently."""

    def test_a_one_line_reply_keeps_its_body(self):
        import memory_dreaming as md
        rel, body = md.parse_relation("RELATION: tension. The two notes disagree about the retry budget ordering.")
        self.assertEqual((rel, body), ("tension", "The two notes disagree about the retry budget ordering."))
        self.assertFalse(md._is_null_body(body))
        self.assertTrue(md._is_null_body(md.parse_relation("RELATION: connection\nNULL")[1]))
        self.assertEqual(md.parse_relation("**RELATION:** contradiction\nA body on the next line, long enough.")[0],
                         "contradiction")


# =====================================================================================================================
class TestPlaceholderEchoIsNotADream(unittest.TestCase):
    """W2B-04 (found LIVE by wave 2B: 3 of 3 local-model dreams opened with the prompt's own placeholder and were kept):
    a body that opens with an angle-bracketed placeholder takes the loud path, like a refusal."""

    def test_a_placeholder_echo_takes_the_loud_path(self):
        import memory_dreaming as md

        class R:
            text = ("RELATION: contradiction\n<a single concise paragraph (2-4 sentences) stating the insight> The two "
                    "notes disagree about when the cache is warm.")
        fails = []
        a = {"id": 1, "summary": "a", "content": "a", "category": "x", "source_project": "p", "created_at": "2026-01-01"}
        b = {"id": 2, "summary": "b", "content": "b", "category": "x", "source_project": "p", "created_at": "2026-01-01"}
        with mock.patch.object(md._thinker, "think", return_value=R()):
            self.assertIsNone(md.dream_on_pair(a, b, "divergent", on_failure=fails.append))
        self.assertTrue(fails and "placeholder" in fails[0], fails)

        class OK:
            text = "RELATION: contradiction\nThe two notes disagree about when the cache is warm, and neither says why."
        with mock.patch.object(md._thinker, "think", return_value=OK()):
            self.assertTrue(md.dream_on_pair(a, b, "divergent"), "a genuine body must still be kept (control)")


# =====================================================================================================================
class TestWhispersDeclaredOff(_TmpCase):
    """R1-L2 (owner #22536): whispers are the declared next channel -- OFF, when_off 'lane1', so the whisper digests
    stay ordinary memories until the channel passes its own battery."""

    def test_declared_off_and_the_digests_stay_in_lane_one(self):
        import recall_channels as rc
        reg = rc.registry()
        self.assertIn("whispers", reg)
        self.assertFalse(reg["whispers"]["enabled"])
        self.assertEqual(reg["whispers"]["when_off"], "lane1")
        self.assertNotIn("whispers", rc.hidden_channels(reg))
        b = Brain(self.tmp)
        w = b.add("weekly whisper digest about gradle build cache daemon", kind="reflection",
                  derived_from="whisper_synthesis", confidence=0.8)
        self.assertEqual(rc.divert_ids(b.c, [w], reg), set())
        out, _ = run_recall(b.path, "weekly whisper digest gradle build cache daemon")
        self.assertNotIn("whispers", out)
        self.assertIn(w, [r["id"] for r in out["results"]])


# =====================================================================================================================
def _playbook(b: Brain, label: str, occ: int, ev: float = 0.5) -> int:
    content = (f"[Playbook -- dream-synthesis, unvalidated]\nA repeated procedural pattern was observed in [{label}] "
               f"across {occ} procedural memories. Candidate for graduation behind the raised dream-authored "
               f"evidence bar (evidence={ev}).")
    return b.add(content, summary=f"Playbook candidate [{label}] ({occ}x)", kind="procedural",
                 project="dream-pipeline", category="workflow_pattern", vector=False)


class TestOnePlaybookPerLabel(_TmpCase):
    """R1-L3: idempotence on the exact text still minted a variant each time a project's count moved."""

    def test_emission_skips_a_label_that_already_has_an_active_candidate(self):
        import dream_pipeline as dp
        b = Brain(self.tmp)
        _playbook(b, "alpha", 4)
        pid = dp._emit_playbook(b.c, {**dp.DEFAULT_CONFIG}, "alpha", 5, 0.6)
        self.assertIsNone(pid, "a second variant for the same label was emitted")
        self.assertIsNotNone(dp._emit_playbook(b.c, {**dp.DEFAULT_CONFIG}, "beta", 5, 0.6))

    def test_the_dedup_keeps_one_per_label(self):
        import dream_ledger as dl
        b = Brain(self.tmp)
        for occ in (4, 5, 6):
            _playbook(b, "alpha", occ)
        _playbook(b, "beta", 3)
        p = dl.plan(b.c)["playbooks"]
        self.assertEqual((p["active_rows"], p["kept"], p["archive"]), (4, 2, 2), p)     # behaviour first
        self.assertEqual(p["distinct_labels"], 2, p)


# =====================================================================================================================
class TestRaptorSummariesOfSynthesis(_TmpCase):
    """R1-L4: the RAPTOR summaries of the dream-pipeline / memory-dreaming clusters stayed in lane 1 unstamped."""

    def test_the_plan_archives_them_with_the_playbooks(self):
        import dream_ledger as dl
        b = Brain(self.tmp)
        s1 = b.add("These entries are recurring playbook candidate memory summaries", kind="semantic")
        s2 = b.add("A cluster about ordinary work", kind="semantic")
        for label, sid in (("dream-pipeline", s1), ("build", s2)):
            b.c.execute("INSERT INTO raptor_clusters (cluster_signature, member_count, synthesis_memory_id, "
                        "cluster_label) VALUES (?, 3, ?, ?)", ("sig-" + label, sid, label))
        b.c.commit()
        p = dl.plan(b.c)["playbooks"]
        self.assertIn(s1, p["archive_ids"])                                   # behaviour first
        self.assertNotIn(s2, p["archive_ids"])
        self.assertEqual(p["raptor_summaries_of_synthesis"], 1)


# =====================================================================================================================
class TestPlaybookStampRidesTheRestamp(_TmpCase):
    """R1-L5: the playbook stamp lived inside the optional --playbooks archive; declining it left every candidate
    unstamped in lane 1 at 1.0."""

    def test_restamp_alone_stamps_every_candidate_and_rolls_back(self):
        import dream_ledger as dl
        b = Brain(self.tmp)
        for occ in (4, 5):
            _playbook(b, "alpha", occ)
        p = dl.plan(b.c)
        pre = str(self.tmp / "pre.json")
        dl.apply(b.c, p, ["restamp"], pre, encoder=fake_encoder, stamp_date="20260928")
        self.assertEqual(b.all(f"SELECT DISTINCT derived_from, confidence FROM memories WHERE {dl.PLAYBOOK_WHERE}"),
                         [("playbook_synthesis", 0.40)])                    # behaviour first
        self.assertEqual(p["restamp"]["playbooks_to_stamp"], 2)
        self.assertEqual(b.one(f"SELECT COUNT(*) FROM memories WHERE {dl.PLAYBOOK_WHERE} AND is_archived=0")[0], 2,
                         "the re-stamp archived something")
        dl.rollback(b.c, json.load(open(pre)))
        self.assertEqual(b.all(f"SELECT DISTINCT derived_from, confidence FROM memories WHERE {dl.PLAYBOOK_WHERE}"),
                         [(None, 1.0)])


# =====================================================================================================================
class TestChannelScansOnlyEligibleRows(_TmpCase):
    """R1-L6: every dream row was read, faded and IN-listed on every recall; the scan now reads only rows that CAN
    clear the gate (young enough, or carrying evidence)."""

    def test_old_rows_without_evidence_are_not_read(self):
        import dream_ledger as dl
        import recall_channels as rc
        b = Brain(self.tmp)
        a, bb = b.add("endpoint a scan"), b.add("endpoint b scan")
        young = _dream(b, a, bb, "young dream", days_ago=2)
        old = _dream(b, a, bb, "old dream no evidence", days_ago=200)
        confirmed = _dream(b, a, bb, "old dream confirmed", days_ago=200)
        b.c.execute("INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)", (confirmed, dl.CONFIRM_TAG))
        b.c.commit()
        got = {r[0] for r in rc._channel_rows_aged(b.c, rc.CHANNELS["dreams"])}
        self.assertEqual(got, {young, confirmed})
        self.assertNotIn(old, got)


# =====================================================================================================================
class TestMultiHopNeverFeedsTheReranker(_TmpCase):
    """R1-L7: with --multi-hop, a dream reached through a refines answer edge was hydrated and reranked, then dropped
    by the late guard: extra reranker work."""

    def test_an_expanded_dream_is_diverted_before_the_reranker(self):
        import claude_memory as cm
        import hybrid_retrieval as hr
        b = Brain(self.tmp)
        ids = _plant_channel_brain(b, n_dreams=1)
        b.c.execute("INSERT INTO memory_edges (source_id, target_id, predicate, confidence, valid_from, created_at, "
                    "extraction_method, metadata) VALUES (?, ?, 'refines', 0.8, datetime('now'), datetime('now'), "
                    "'manual', ?)", (ids["m1"], ids["d0"], json.dumps({"origin": "dream_answer"})))
        b.c.commit()
        spy = _RerankSpy()
        ns = _recall_ns("gradle build cache warm daemon", top_k=5)
        ns.multi_hop = 2
        buf = io.StringIO()
        with mock.patch.object(cm, "DB_PATH", Path(b.path)), \
                mock.patch.object(cm, "_encode", lambda t, p="search_document": fake_vec(t)), \
                mock.patch.object(hr, "reranker_score", spy), contextlib.redirect_stdout(buf):
            cm.cmd_recall(ns)
        self.assertTrue(spy.calls, "the reranker never ran (the test cannot fail)")
        self.assertNotIn(ids["d0"], spy.ids, "a dream reached the reranker through the expansion")


# =====================================================================================================================
class TestTaintIsVisibleAndShowIsSanitised(_TmpCase):
    """R1-L8: a tainted dream's channel row looked like any other, and `dream show` printed raw model text."""

    def test_the_channel_row_carries_the_untrusted_mark(self):
        b = Brain(self.tmp)
        ids = _plant_channel_brain(b, n_dreams=1)
        b.c.execute("UPDATE memories SET untrusted_source=1 WHERE id=?", (ids["d0"],))
        b.c.commit()
        d = run_recall(b.path, "gradle build cache daemon memory pressure")[0]["dreams"][0]
        self.assertIn("untrusted", d["provenance_frame_reason"])
        self.assertEqual(d["untrusted_source"], 1)

    def test_dream_show_is_sanitised(self):
        import dream_ledger as dl
        b = Brain(self.tmp)
        a, bb = b.add("endpoint a show"), b.add("endpoint b show")
        d = _dream(b, a, bb, "a dream with \x1b[31mred\x1b[0m escape codes and a bell \x07 in it")
        b.c.close()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = dl.run(["--db", b.path, "show", str(d), "--json"])
        env = rc if isinstance(rc, dict) else json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertNotIn("\x1b", env["data"]["content"])
        self.assertNotIn("\x07", env["data"]["content"])


# =====================================================================================================================
class TestTheKnobReadsTheTopFiveByRank(_TmpCase):
    """R1-L12: the (now default-off) relative clause took positions [:5] of the Lost-in-the-Middle-placed list."""

    def test_the_weakest_top5_is_computed_over_the_top_five_by_score(self):
        import recall_channels as rc
        b = Brain(self.tmp)
        q = "gradle build cache daemon memory pressure"
        strong = [b.add(q + " strong %d" % i) for i in range(5)]
        weak = [b.add("tomato compost garden %d" % i) for i in range(5)]
        # LitM placement: r0, r2, r4, r6, r8, ..., r1 -- positions [:5] mix strong and weak
        ranked = [(m, 1.0 - 0.01 * i) for i, m in enumerate(strong)] + [(m, 0.5 - 0.01 * i) for i, m in enumerate(weak)]
        litm = [ranked[i] for i in (0, 2, 4, 6, 8, 9, 7, 5, 3, 1)]
        results = [{"id": m, "score": s} for m, s in litm]
        reg = json.loads(json.dumps(rc.CHANNELS))
        reg["dreams"]["floor"]["at_least_weakest_top5_memory"] = True
        for spec in reg.values():
            spec["enabled"] = bool(spec.get("default"))
        _dream(b, strong[0], strong[1], q + " a dream")
        _out, diag = rc.build(b.c, query=q, query_vec=fake_vec(q), results=results, top_k=10, reg=reg)
        self.assertGreater(diag["dreams"]["weakest_top5_memory_cosine"], 0.8, diag)


if __name__ == "__main__":
    unittest.main()
