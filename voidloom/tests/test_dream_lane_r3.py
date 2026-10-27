# -*- coding: utf-8 -*-
"""Round 3 G2 item 5 (model-choice; ruler VL-MC-v1.2 + VL-DF-v1.1): the memory lane stays memories-only on EVERY
surface and never comes back short.

R2-06  `context` read memories without recall's channel partition: a dream (stored at importance 0.55) outranked every
       owner memory at the 0.5 default, and `context` bumped its access_count -- the met-ledger rule says showing a
       dream never does. It now excludes exactly the rows a recall keeps out of `results` (the registry).
R2-08  the partition failed OPEN: the registry module absent, the registry raising, or its SELECT failing left the
       memory lane UNpartitioned. It now fails CLOSED on the two synthesis origins, on every surface (recall, the
       legacy path, anticipation).
R2-09  the partition took channel rows out of a FIXED pool, so a recall whose pool held dreams / playbook candidates
       came back short (owner's brain: bm25-only 17 of 17 adversarial queries short, some empty). Each leg is now
       refilled after the partition; `divert=None` stays byte-identical.

Brains are the PACKAGE's (the dream-fix suite's own fixture, a packaged blank brain + deterministic vectors): no
model, no GPU, no host brain. RED rows are RED on the N bytes (voidloom f8f0c30 = artifacts_r2).
"""
import argparse
import contextlib
import io
import json
import sqlite3
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_dreamfix_tranche_a as T  # noqa: E402  (the dream-fix suite's fixtures: Brain, fake_vec, run_recall)

import claude_memory as cm  # noqa: E402
import hybrid_retrieval as hr  # noqa: E402


@pytest.fixture
def brain(tmp_path):
    return T.Brain(tmp_path)


def _context(db, limit=100, project=None):
    buf = io.StringIO()
    with mock.patch.object(cm, "DB_PATH", Path(db)), contextlib.redirect_stdout(buf):
        cm.cmd_context(argparse.Namespace(project=project, limit=limit))
    return json.loads(buf.getvalue().strip().splitlines()[-1])


def _plant_dreams_and_playbooks(b):
    ids = T._plant_channel_brain(b, n_dreams=2)
    b.c.execute("UPDATE memories SET importance = 0.55 WHERE derived_from = 'dream_synthesis'")   # the store's value
    ids["pb"] = b.add("A repeated procedural pattern was observed in [build] across 4 procedural memories",
                      kind="procedural", derived_from="playbook_synthesis", confidence=0.4, project="dream-pipeline")
    b.c.execute("UPDATE memories SET importance = 0.6 WHERE id = ?", (ids["pb"],))
    b.c.commit()
    return ids


# ---------------------------------------------------------------- R2-06: context is memories only, and bumps no dream
@pytest.mark.parametrize("project", [None, "memory-dreaming"], ids=["all", "project-memory-dreaming"])
def test_context_lists_memories_only(brain, project):
    ids = _plant_dreams_and_playbooks(brain)
    synth = {ids["d0"], ids["d1"], ids["pb"]}
    out = _context(brain.path, project=project)
    assert out["status"] == "ok"
    got = {r["id"] for r in out["results"]}
    assert not (got & synth), "context listed synthesis rows %s" % sorted(got & synth)


def test_context_never_bumps_a_dreams_access_count(brain):
    ids = _plant_dreams_and_playbooks(brain)
    before = {i: brain.one("SELECT access_count FROM memories WHERE id=?", (i,))[0] for i in (ids["d0"], ids["pb"])}
    _context(brain.path)
    c = sqlite3.connect(brain.path)
    after = {i: c.execute("SELECT access_count FROM memories WHERE id=?", (i,)).fetchone()[0] for i in before}
    assert after == before, (before, after)
    assert c.execute("SELECT access_count FROM memories WHERE id=?", (ids["m1"],)).fetchone()[0] >= 1   # a memory is


def test_context_keeps_memories_with_no_origin(brain):
    """NULL-safe: the exclusion never drops a memory whose derived_from is NULL (most of them)."""
    _plant_dreams_and_playbooks(brain)
    out = _context(brain.path, limit=500)
    n_plain = brain.one("SELECT COUNT(*) FROM memories WHERE is_archived=0 AND derived_from IS NULL")[0]
    assert n_plain > 5 and len([r for r in out["results"]]) >= n_plain, (n_plain, len(out["results"]))


# ---------------------------------------------------------------- R2-08: the partition fails CLOSED
Q = "gradle build cache daemon memory pressure"


def _synth(b, ids):
    return {ids["d0"], ids["d1"], ids["pb"]}


def test_recall_without_the_registry_module_keeps_synthesis_out(brain):
    ids = _plant_dreams_and_playbooks(brain)
    with mock.patch.object(cm, "recall_channels", None):
        out, spy = T.run_recall(brain.path, Q, top_k=10)
    got = {r["id"] for r in out["results"]}
    assert not (got & _synth(brain, ids)), sorted(got & _synth(brain, ids))
    assert not (set(spy.ids) & _synth(brain, ids)), "the reranker scored synthesis rows"


def test_recall_with_a_raising_registry_keeps_synthesis_out(brain):
    ids = _plant_dreams_and_playbooks(brain)
    import recall_channels as rc

    def boom(*a, **k):
        raise RuntimeError("registry fault")
    with mock.patch.object(rc, "registry", boom):
        out, _spy = T.run_recall(brain.path, Q, top_k=10)
    assert not ({r["id"] for r in out["results"]} & _synth(brain, ids))


def test_a_registry_predicate_the_brain_cannot_answer_still_keeps_synthesis_out(brain):
    ids = _plant_dreams_and_playbooks(brain)
    import recall_channels as rc
    real = rc.registry

    def bad_column(*a, **k):
        reg = real(*a, **k)
        reg["dreams"]["predicate"] = {"column": "no_such_column", "equals": "dream_synthesis"}
        return reg
    with mock.patch.object(rc, "registry", bad_column):
        out, _spy = T.run_recall(brain.path, Q, top_k=10)
    assert not ({r["id"] for r in out["results"]} & _synth(brain, ids))


def test_the_legacy_vector_path_fails_closed_too(brain):
    """The legacy path (no hybrid module) with no registry module: synthesis stays out of `results` there too."""
    ids = _plant_dreams_and_playbooks(brain)
    with mock.patch.object(cm, "recall_channels", None), mock.patch.object(cm, "hybrid_retrieval", None):
        out, _spy = T.run_recall(brain.path, Q, top_k=10)
    got = {r["id"] for r in out["results"]}
    assert out.get("mode") == "vector-only-legacy" and got and not (got & _synth(brain, ids)), (out.get("mode"),
                                                                                                sorted(got))


# ---------------------------------------------------------------- R2-09: the pool is refilled after the partition
def _flood(b, n=60):
    """n playbook-candidate rows that win BM25 for the query (the 3,624 the owner's brain still holds)."""
    for i in range(n):           # the query's words, twice: they outrank every real note on BOTH legs
        b.add("gradle build cache daemon memory pressure gradle build cache daemon memory pressure %d" % i,
              kind="procedural", derived_from="playbook_synthesis", confidence=0.4, project="dream-pipeline")
    for i in range(12):          # real notes that DO answer the query (every word once, among others)
        b.add("gradle build cache daemon memory pressure seen in a real session, note number %d of the week" % i,
              project="build")


@pytest.mark.parametrize("mode", ["bm25-only", "vector-only", "hybrid"])
def test_a_recall_whose_pool_was_mostly_synthesis_is_not_short(brain, mode):
    _flood(brain)
    conn = T._connect(brain.path)
    synth_ids = {r[0] for r in conn.execute("SELECT id FROM memories WHERE derived_from IS NOT NULL")}
    divert = lambda ids: {i for i in ids if i in synth_ids}      # noqa: E731  (hybrid's refill, isolated)
    qv = T.fake_vec(Q)
    top_k = 10
    spy = T._RerankSpy()
    with mock.patch.object(hr, "reranker_score", spy):
        res = hr.recall_hybrid(conn, Q, qv, top_k=top_k, mode=mode, divert=divert)
    ids = [r["id"] for r in res]
    synth = {r[0] for r in conn.execute("SELECT id FROM memories WHERE derived_from IS NOT NULL")}
    assert not (set(ids) & synth)
    assert len(ids) == top_k, "%s returned %d of %d after the partition" % (mode, len(ids), top_k)


def test_without_a_divert_the_pipeline_is_byte_identical(brain):
    """divert=None: the same pool, the same order, the same reranker work as the N bytes' pipeline."""
    _flood(brain, n=20)
    conn = T._connect(brain.path)
    qv = T.fake_vec(Q)
    spy = T._RerankSpy()
    with mock.patch.object(hr, "reranker_score", spy):
        res = hr.recall_hybrid(conn, Q, qv, top_k=10, mode="hybrid")
    fused = hr.rrf_fuse(hr._vector_search(conn, qv, hr._VEC_CANDIDATES), hr.fts_match(conn, Q, top_k=hr._BM25_CANDIDATES),
                        k=hr._RRF_K, top_k=hr._RRF_CANDIDATES)
    assert spy.ids == [m for m, _s in fused] and len(res) == 10


def test_the_refill_is_bounded(brain):
    """A corpus that is ALL synthesis for the query: the legs grow at most _REFILL_MAX_FACTOR x, then stop. (A bound,
    GREEN on both trees: the N bytes never refilled at all.)"""
    for i in range(40):
        brain.add("zzqx flood %d" % i, kind="procedural", derived_from="playbook_synthesis", confidence=0.4)
    conn = T._connect(brain.path)
    calls = []
    real = hr.fts_match

    def counted(c, q, top_k=50, **kw):
        calls.append(top_k)
        return real(c, q, top_k=top_k, **kw)
    synth = {r[0] for r in conn.execute("SELECT id FROM memories WHERE derived_from IS NOT NULL")}
    divert = lambda ids: {i for i in ids if i in synth}          # noqa: E731  (any divert: the bound is hybrid's)
    with mock.patch.object(hr, "fts_match", counted):
        res = hr.recall_hybrid(conn, "zzqx flood", None, top_k=5, mode="bm25-only", bm25_candidates=4, divert=divert)
    assert res == [] and max(calls) <= 4 * getattr(hr, "_REFILL_MAX_FACTOR", 1) and len(calls) <= 4, calls


# ---------------------------------------------------------------- R2-06 at Tier 0 (the lite engine; G1's file)
def test_tier0_context_lists_memories_only(brain):
    """voidloom/README.md: "On Tier 0 ... dreams are still stored and kept out of `results`". The lite engine's
    `context` listed them (G2's one-hunk patch for G1: iterations/scratch_records/r3_g2/for_g1/r2_06_lite_context.patch)."""
    import claude_memory_lite as lite
    ids = _plant_dreams_and_playbooks(brain)
    buf = io.StringIO()
    with mock.patch.object(lite, "DB_PATH", Path(brain.path)), contextlib.redirect_stdout(buf):
        lite.cmd_context(argparse.Namespace(project=None, limit=100))
    out = json.loads(buf.getvalue().strip().splitlines()[-1])
    got = {r["id"] for r in out["results"]}
    assert got and not (got & {ids["d0"], ids["d1"], ids["pb"]}), sorted(got & {ids["d0"], ids["d1"], ids["pb"]})
