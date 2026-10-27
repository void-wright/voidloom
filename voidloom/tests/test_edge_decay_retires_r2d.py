# -*- coding: utf-8 -*-
"""Round 2, side lane R2d (Option A of the owner's M7 fork): decay pruning RETIRES an edge, it never deletes one.

MEASURED (r2_R2c_handoff, on a backup-API copy of the reference brain): 115 nights of the since-fixed per-run
compounding left 3,460 edges at stored confidences 0.004-0.029, all older than 45 days. The owner's catch-up run would
DELETE them for good. Option A: the prune sets valid_until (UTC, the brain's datetime format) and records why in
metadata, so one documented SQL statement brings any of them back.

Each test is RED on f1b968c unless its docstring calls it a guard (GREEN on both: the meaning that must not move).
Every brain is a scratch file under tmp_path; no model is loaded. "Now" is fixed by replacing the module's `datetime`.
"""
import json
import os
import re
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

F = 0.995
SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE memory_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER NOT NULL, target_id INTEGER NOT NULL,
    predicate TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0.8, valid_from TEXT, valid_until TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')), metadata TEXT, extraction_method TEXT DEFAULT 'manual',
    UNIQUE(source_id, target_id, predicate));
"""
T0 = datetime(2026, 10, 15, 12, 0, 0)
STAMP = T0.strftime("%Y-%m-%d %H:%M:%S")


def _clock(monkeypatch, at):
    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.replace(tzinfo=timezone.utc).astimezone(tz) if tz else at
    monkeypatch.setattr(temporal_edges, "datetime", _DT)


def _brain(tmp_path, edges):
    """edges: [(confidence, days_old, metadata_text_or_None)] -> ids 1..n."""
    c = sqlite3.connect(tmp_path / "b.db")
    c.executescript(SCHEMA)
    for i, (conf, days_old, meta) in enumerate(edges):
        c.execute("INSERT INTO memory_edges (source_id, target_id, predicate, confidence, created_at, metadata,"
                  " extraction_method) VALUES (?, ?, 'references', ?, ?, ?, 'sleep_loop')",
                  (i + 1, i + 1001, conf, (T0 - timedelta(days=days_old)).strftime("%Y-%m-%d %H:%M:%S"), meta))
    c.commit()
    return c


def _run(c, **kw):
    r = temporal_edges.decay_confidence(c, **{"factor": F, "min_threshold": 0.15, "max_age_days": 45, **kw})
    c.commit()
    return r


def _row(c, eid):
    return c.execute("SELECT confidence, valid_until, metadata FROM memory_edges WHERE id = ?", (eid,)).fetchone()


# old+weak (retired), young+weak (kept), old+strong (kept), old+weak with NULL metadata (retired)
EDGES = [(0.01, 60, '{"reason": "cosine 0.71"}'), (0.01, 10, None), (0.9, 60, '{"reason": "x"}'), (0.02, 90, None)]


def test_prune_retires_and_deletes_nothing(tmp_path, monkeypatch):
    _clock(monkeypatch, T0)
    c = _brain(tmp_path, EDGES)
    r = _run(c)
    assert r["pruned_count"] == 2, r
    assert c.execute("SELECT COUNT(*) FROM memory_edges").fetchone()[0] == 4          # nothing deleted
    assert [i for (i,) in c.execute("SELECT id FROM memory_edges WHERE valid_until IS NULL ORDER BY id")] == [2, 3]
    for eid, prior in ((1, '{"reason": "cosine 0.71"}'), (4, None)):
        conf, vu, meta = _row(c, eid)
        assert vu == STAMP                                                             # UTC, the brain's format
        m = json.loads(meta)
        assert m["retired_by"] == "decay-prune" and m["retired_at"] == STAMP
        assert m["prior_metadata"] == prior                                            # verbatim, NULL kept NULL
        assert m["confidence_before"] == conf                                          # the column is not touched
        assert 0 < m["retired_confidence"] < 0.15


def test_an_already_retired_edge_is_never_retired_or_counted_again(tmp_path, monkeypatch):
    _clock(monkeypatch, T0)
    c = _brain(tmp_path, EDGES)
    _run(c)
    before = c.execute("SELECT * FROM memory_edges ORDER BY id").fetchall()
    assert [b[0] for b in before if b[6] is not None] == [1, 4]                       # retired, still here
    for night in (1, 2, 30):
        _clock(monkeypatch, T0 + timedelta(days=night))
        r = _run(c)
        assert r["pruned_count"] == 0, (night, r)
    after = c.execute("SELECT * FROM memory_edges ORDER BY id").fetchall()
    assert [a for a in after if a[0] in (1, 4)] == [b for b in before if b[0] in (1, 4)]   # byte-identical rows


def test_dry_run_reports_the_number_the_real_run_retires(tmp_path, monkeypatch):
    """The dry run's counts equal the real run's, and remaining_count is the CURRENT edge count in both."""
    _clock(monkeypatch, T0)
    c = _brain(tmp_path, EDGES)
    snap = c.execute("SELECT * FROM memory_edges ORDER BY id").fetchall()
    dry = temporal_edges.decay_confidence(c, factor=F, min_threshold=0.15, max_age_days=45, dry_run=True)
    assert c.execute("SELECT * FROM memory_edges ORDER BY id").fetchall() == snap
    real = _run(c)
    assert (dry["decayed_count"], dry["pruned_count"], dry["remaining_count"]) == \
           (real["decayed_count"], real["pruned_count"], real["remaining_count"]) == (2, 2, 2)
    assert real["retired_count"] == real["pruned_count"] and real["deleted_count"] == 0
    # a second dry run after the real one sees nothing left to retire (the retired are not current)
    dry2 = temporal_edges.decay_confidence(c, factor=F, min_threshold=0.15, max_age_days=45, dry_run=True)
    assert dry2["pruned_count"] == 0 and dry2["remaining_count"] == 2


def test_the_documented_restore_statement_brings_them_back(tmp_path, monkeypatch):
    _clock(monkeypatch, T0)
    c = _brain(tmp_path, EDGES)
    _run(c)
    expected = {eid: json.loads(_row(c, eid)[2]) for eid in (1, 4)}
    n = c.execute(temporal_edges.DECAY_RESTORE_SQL).rowcount
    c.commit()
    assert n == 2
    for eid, m in expected.items():
        conf, vu, meta = _row(c, eid)
        assert vu is None
        assert meta == m["prior_metadata"]                                             # exact bytes, NULL is NULL
        assert conf == m["retired_confidence"]                                         # where the clock has it
    assert c.execute("SELECT COUNT(*) FROM memory_edges WHERE valid_until IS NULL").fetchone()[0] == 4
    # Same UTC day: the clock already counted today, so a run re-retires nothing.
    _clock(monkeypatch, T0 + timedelta(hours=1))
    assert _run(c)["pruned_count"] == 0


def test_restore_leaves_other_non_current_edges_alone(tmp_path, monkeypatch):
    """A belief-invalidated or quarantined edge (valid_until set by someone else) is not the decay's to restore,
    including one whose metadata is not JSON at all (the statement must not raise on it)."""
    _clock(monkeypatch, T0)
    c = _brain(tmp_path, EDGES + [(0.8, 5, '{"authored_by": "belief_engine"}'), (0.8, 5, "not json")])
    c.execute("UPDATE memory_edges SET valid_until = '2026-06-10T17:51:58.554479' WHERE id IN (5, 6)")
    c.commit()
    _run(c)
    c.execute(temporal_edges.DECAY_RESTORE_SQL)
    c.commit()
    assert c.execute("SELECT valid_until FROM memory_edges WHERE id IN (5, 6)").fetchall() == \
        [("2026-06-10T17:51:58.554479",)] * 2


def _norm(s):
    return re.sub(r"\s+", " ", s).strip()


def test_the_restore_statement_is_documented_verbatim():
    stmt = _norm(temporal_edges.DECAY_RESTORE_SQL)
    assert stmt in _norm(temporal_edges.__doc__)
    assert stmt in _norm((ROOT / "docs" / "FIRST_NIGHT.md").read_text(encoding="utf-8"))


def test_guard_young_or_strong_edges_are_never_pruned(tmp_path, monkeypatch):
    """Guard (GREEN on both): the prune rule itself (below threshold AND older than max_age_days) did not move."""
    _clock(monkeypatch, T0)
    c = _brain(tmp_path, [(0.01, 10, None), (0.9, 400, None), (0.149, 46, None), (0.151, 46, None)])
    c.execute("INSERT INTO metadata (key, value) VALUES ('edge_decay_last_run', ?)",
              ((T0 - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),))         # one day to decay, not a lifetime
    r = _run(c)
    assert r["pruned_count"] == 1
    assert [i for (i,) in c.execute("SELECT id FROM memory_edges WHERE valid_until IS NULL ORDER BY id")] == [1, 2, 4]
