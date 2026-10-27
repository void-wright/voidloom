# -*- coding: utf-8 -*-
"""Round 2, side lane R2d (Option A): a RETIRED edge must be, to every reader and writer, what a DELETED one was.

The class. Before R2d the decay's prune DELETEd an edge. Now it keeps the row and sets valid_until. Two kinds of code
can tell the difference:
  * a READER that does not ask for `valid_until IS NULL` would count a retired edge as part of the graph;
  * a WRITER re-asserting the same (source, target, predicate) would hit UNIQUE (or INSERT OR IGNORE would silently
    leave it retired), where after a delete its INSERT made a fresh current edge.

Each test builds ONE brain, runs the real decay (which retires), then copies it and deletes the retired rows -- the
deletion world, byte for byte what the pre-R2d prune left. The same reader/writer runs in both worlds and must agree on
the CURRENT graph. RED on 2e3fa17 (the retire commit, sweep not yet applied) unless the docstring says guard; every
member here was named by the sweep (see the R2d handoff for the full list, cleared units included).
"""
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGANS = ROOT / "organs"
if str(ORGANS) not in sys.path:
    sys.path.insert(0, str(ORGANS))

import temporal_edges  # noqa: E402
import dream_ledger  # noqa: E402

T0 = datetime(2026, 10, 15, 12, 0, 0)
DREAM, ANSWER = 10, 11
SCHEMA = (ROOT / "migrations" / "migration_003_temporal_edges.sql").read_text()
BASE = """
CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, summary TEXT, category TEXT,
    kind TEXT DEFAULT 'episodic', is_archived INTEGER DEFAULT 0, derived_from TEXT, source_project TEXT,
    confidence REAL DEFAULT 0.4, importance REAL DEFAULT 0.5, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE memory_tags (id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id INTEGER, tag TEXT, UNIQUE(memory_id, tag));
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE access_log (id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id INTEGER, access_type TEXT, accessed_at TEXT);
"""


def _ts(days_old):
    return (T0 - timedelta(days=days_old)).strftime("%Y-%m-%d %H:%M:%S")


# (source, target, predicate, confidence, days_old, method, metadata) -- conf < 0.15 and > 45 days => retired
EDGES = [
    (1, 2, "references", 0.05, 60, "sleep_loop", {"reason": "cosine"}),          # 1's only outgoing edge
    (2, 3, "refines", 0.90, 60, "sleep_loop", {"reason": "cosine"}),
    (3, 4, "contradicts", 0.05, 60, "manual", {"authored_by": "belief_engine"}),
    (4, 3, "contradicts", 0.05, 60, "manual", {"authored_by": "belief_engine"}),
    (ANSWER, DREAM, "refines", 0.05, 200, "manual", {"origin": dream_ledger.ANSWER_ORIGIN}),
    (5, 6, "references", 0.90, 60, "sleep_loop", {"nrem": "co_recalled"}),
    (5, 7, "references", 0.05, 60, "sleep_loop", {"nrem": "co_recalled"}),
    (8, 9, "references", 0.05, 60, "sleep_loop", {"tag": "tension", "origin": "dream-synthesis"}),
]


def _clock(monkeypatch, at):
    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.replace(tzinfo=timezone.utc).astimezone(tz) if tz else at
    monkeypatch.setattr(temporal_edges, "datetime", _DT)


@pytest.fixture
def worlds(tmp_path, monkeypatch):
    """(retire_conn, delete_conn): the same brain after the same decay; one keeps the retired rows, one deletes them."""
    db = tmp_path / "retire.db"
    c = sqlite3.connect(db)
    c.executescript(BASE)
    c.executescript(SCHEMA)
    for i in range(1, 13):
        c.execute("INSERT INTO memories (id, content, summary, category, derived_from, created_at) VALUES (?,?,?,?,?,?)",
                  (i, "memory %d" % i, "m%d" % i, "insight",
                   dream_ledger.DREAM_DERIVED_FROM if i == DREAM else None, _ts(400)))
    for s, t, p, conf, days, meth, meta in EDGES:
        c.execute("INSERT INTO memory_edges (source_id, target_id, predicate, confidence, created_at, extraction_method,"
                  " metadata) VALUES (?,?,?,?,?,?,?)", (s, t, p, conf, _ts(days), meth, json.dumps(meta)))
    c.commit()
    _clock(monkeypatch, T0)
    r = temporal_edges.decay_confidence(c, factor=0.995, min_threshold=0.15, max_age_days=45)
    c.commit()
    assert r["pruned_count"] == 6 and r["deleted_count"] == 0, r
    c.close()
    shutil.copy(db, tmp_path / "delete.db")
    d = sqlite3.connect(tmp_path / "delete.db")
    gone = [eid for eid, vu, m in d.execute("SELECT id, valid_until, metadata FROM memory_edges")
            if temporal_edges.is_decay_retired(vu, m)]
    assert len(gone) == 6
    d.execute("DELETE FROM memory_edges WHERE id IN (%s)" % ",".join("?" * len(gone)), gone)
    d.commit()
    a = sqlite3.connect(db)
    yield a, d
    a.close()
    d.close()


def _current(c):
    # valid_from: whether it is set (a writer stamps datetime('now'); the two worlds may straddle a second)
    return sorted(c.execute("SELECT source_id, target_id, predicate, round(confidence, 6), metadata, extraction_method,"
                            " valid_from IS NULL FROM memory_edges WHERE valid_until IS NULL").fetchall())


def _retired_rows(c):
    return sorted(r for r in c.execute("SELECT * FROM memory_edges WHERE valid_until IS NOT NULL").fetchall())


def test_guard_the_two_worlds_start_with_the_same_current_graph(worlds):
    """Guard: the fixture itself -- the current graph is identical, the retire world keeps 6 more rows."""
    a, d = worlds
    assert _current(a) == _current(d)
    assert len(_retired_rows(a)) == 6 and _retired_rows(d) == []


# ---------------------------------------------------------------- readers ----

def test_dream_evidence_does_not_count_a_retired_answer_edge(worlds):
    a, d = worlds
    assert dream_ledger.evidence_counts(a, [DREAM]) == dream_ledger.evidence_counts(d, [DREAM])


@pytest.mark.parametrize("with_dream_log", [True, False], ids=["main-path", "no-dream_log-fallback"])
def test_recall_channel_does_not_admit_a_dream_on_a_retired_answer(worlds, with_dream_log):
    import recall_channels
    a, d = worlds
    for c in (a, d):
        if with_dream_log:
            c.execute("CREATE TABLE dream_log (id INTEGER PRIMARY KEY, dream_memory_id INTEGER, memory_a_id INTEGER,"
                      " memory_b_id INTEGER)")
            c.execute("CREATE TABLE edge_proposals (id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER,"
                      " status TEXT, proposal_kind TEXT)")
    spec = {"predicate": {"column": "derived_from", "equals": dream_ledger.DREAM_DERIVED_FROM},
            "confidence_gate": 0.05, "provenance": "dream"}
    rows = lambda c: sorted(r[0] for r in recall_channels._channel_rows_aged(c, spec))  # noqa: E731
    assert rows(a) == rows(d) == []                    # the dream is 400 days old: only evidence could carry it
    if with_dream_log:
        assert sorted(recall_channels._eligible_dreams(a, spec, T0)) == \
               sorted(recall_channels._eligible_dreams(d, spec, T0))


def test_sentinel_counts_a_memory_whose_only_edge_retired_as_having_none(worlds):
    import organ_yield_sentinel as s
    spec = next(w for w in s.WATCH if w["organ"] == "temporal_edges.extract_edges")
    a, d = worlds
    assert a.execute(spec["input_sql"]).fetchone() == d.execute(spec["input_sql"]).fetchone()


def test_stats_edge_state_is_the_current_graph(worlds, monkeypatch):
    import claude_memory
    a, d = worlds
    sa, sd = claude_memory._edge_state(a), claude_memory._edge_state(d)
    assert {k: v for k, v in sa.items() if k != "retired_edges"} == {k: v for k, v in sd.items() if k != "retired_edges"}
    assert sa["retired_edges"] == 6 and sd["retired_edges"] == 0


def test_guard_graph_readers_already_see_only_current_edges(worlds):
    """Guard (GREEN before and after): PPR, multi-hop, communities, belief, gap, anti-recency, supersedes, cognitive
    map -- the readers that already ask for valid_until IS NULL agree across the worlds."""
    import ppr_recall, communities, belief_engine, gap_mapping, anti_recency, temporal_rerank, cognitive_map
    a, d = worlds
    for c in (a, d):
        c.row_factory = None

    def obs(c):
        idx, _adj = ppr_recall.build_ppr_graph(c, min_confidence=0.0)
        g = communities.build_graph(c)
        c.row_factory = sqlite3.Row
        degrees = sorted((n["id"], n["degree"]) for n in cognitive_map.get_edge_graph(c)["nodes"])
        preds = cognitive_map.get_predicate_distribution(c)
        c.row_factory = None
        return (sorted(idx), ppr_recall.is_ppr_available(c),
                sorted(temporal_edges.multi_hop_expand(c, list(range(1, 13)), depth=3, min_confidence=0.0)),
                sorted((k, repr(sorted(v.items()) if isinstance(v, dict) else sorted(v))) for k, v in g.adj.items()),
                belief_engine._existing_contradicts_edge(c, 3, 4),
                sorted(e["id"] for e in belief_engine.beliefs_as_of(c, "2099-01-01")),
                [x["entity"] for x in gap_mapping.detect_unresolved_contradictions(c)],
                sorted(anti_recency._get_edge_counts(c).items()),
                sorted(temporal_rerank.superseded_status(c, list(range(1, 13))).items()),
                degrees, preds,
                [[sorted(e.items()) for e in temporal_edges.get_edges(c, m)] for m in range(1, 13)])
    assert obs(a) == obs(d)


# ---------------------------------------------------------------- writers ----

def test_create_edge_on_a_retired_triple_makes_the_edge_a_delete_would_have(worlds):
    a, d = worlds
    for c in (a, d):
        temporal_edges.create_edge(c, 1, 2, "references", confidence=0.6, metadata={"reason": "again"},
                                   extraction_method="sleep_loop")
        temporal_edges.create_edge(c, 3, 4, "contradicts", confidence=0.7, metadata={"authored_by": "belief_engine"})
        c.commit()
    assert _current(a) == _current(d)
    # created_at is the moment of re-assertion in both worlds (the decay ages an edge from it)
    q = "SELECT created_at FROM memory_edges WHERE source_id = 1 AND target_id = 2"
    assert a.execute(q).fetchone()[0] >= _ts(1) and d.execute(q).fetchone()[0] >= _ts(1)


def test_guard_create_edge_still_refuses_a_live_duplicate(worlds):
    """Guard: a CURRENT duplicate (or one ended by something other than the decay) is still a UNIQUE error."""
    a, _ = worlds
    with pytest.raises(sqlite3.IntegrityError):
        temporal_edges.create_edge(a, 2, 3, "refines", confidence=0.5)


def test_record_answer_on_a_retired_answer_edge_answers_again(worlds):
    a, d = worlds
    for c in (a, d):
        assert dream_ledger.record_answer(c, ANSWER, DREAM)["status"] == "ok"
        c.commit()
    assert _current(a) == _current(d)
    assert dream_ledger.evidence_counts(a, [DREAM]) == dream_ledger.evidence_counts(d, [DREAM])


def test_nrem_creates_afresh_on_a_retired_corecall_edge_and_never_rewrites_retired_rows(worlds):
    import dream_pipeline
    a, d = worlds
    now = datetime.now()
    for c in (a, d):
        for k in range(3):        # 5 and 7 recalled together three times, sessions 2 h apart
            for mid in (5, 7):
                c.execute("INSERT INTO access_log (memory_id, access_type, accessed_at) VALUES (?, 'recall', ?)",
                          (mid, (now - timedelta(hours=2 * k, minutes=1 if mid == 7 else 0)).isoformat()))
        c.commit()
    before = _retired_rows(a)
    cfg = dict(dream_pipeline.DEFAULT_CONFIG)
    for c in (a, d):
        r = dream_pipeline.phase_nrem_consolidation(c, cfg, None)
        assert r["status"] == "ok", r
    assert _current(a) == _current(d)
    assert [r for r in _retired_rows(a) if (r[1], r[2]) != (5, 7)] == [r for r in before if (r[1], r[2]) != (5, 7)]


def test_tension_mark_on_a_retired_references_edge_writes_the_fresh_link(worlds):
    import dream_pipeline
    a, d = worlds
    for c in (a, d):
        assert dream_pipeline._integrate_dream_edge(c, dict(dream_pipeline.DEFAULT_CONFIG), 1, 2, "tension") == 1
        assert dream_pipeline._integrate_dream_edge(c, dict(dream_pipeline.DEFAULT_CONFIG), 9, 8, "tension") == 1
        c.commit()
    assert _current(a) == _current(d)
