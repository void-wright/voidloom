# -*- coding: utf-8 -*-
"""Round 2, lane R2c, finding M7: edge decay is per DAY, never per RUN.

MEASURED (r1 reflection, on a backup-API copy of the reference brain): temporal_edges.decay_confidence() multiplied the
STORED confidence by factor^(days since created_at) on every run. Two runs 18 s apart both decayed ~28,800 edges, and
the first catch-up after the stall hard-deleted 3,460 edges merely by being run.

Each test is RED on 6bb45ee and GREEN on lane/R2c unless its docstring calls it a guard. Every brain is a scratch file
under tmp_path; no model is loaded. "Now" is driven by replacing the module's `datetime` with a subclass whose now()
is fixed, which both the old and the new code read.
"""
import os
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
    created_at TEXT NOT NULL DEFAULT (datetime('now')), metadata TEXT, extraction_method TEXT DEFAULT 'manual');
"""
# A fixed instant at UTC midday, so a test's "+N days" never straddles a midnight by accident.
T0 = datetime(2026, 10, 15, 12, 0, 0)


def _clock(monkeypatch, at):
    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.replace(tzinfo=timezone.utc).astimezone(tz) if tz else at
    monkeypatch.setattr(temporal_edges, "datetime", _DT)


def _brain(tmp_path, edges, ledger=None):
    """edges: [(confidence, days_old, method)]; ledger: completed_at string of the last successful sleep run."""
    db = tmp_path / "b.db"
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    for i, (conf, days_old, method) in enumerate(edges):
        c.execute("INSERT INTO memory_edges (source_id, target_id, predicate, confidence, created_at, extraction_method)"
                  " VALUES (?, ?, 'references', ?, ?, ?)",
                  (i, i + 1000, conf, (T0 - timedelta(days=days_old)).strftime("%Y-%m-%d %H:%M:%S"), method))
    if ledger is not None:
        c.execute("CREATE TABLE sleep_loop_runs (id INTEGER PRIMARY KEY, started_at TEXT, completed_at TEXT, status TEXT)")
        c.execute("INSERT INTO sleep_loop_runs (started_at, completed_at, status) VALUES (?, ?, 'success')",
                  (ledger, ledger))
    c.commit()
    return c


def _confs(c):
    return dict(c.execute("SELECT id, confidence FROM memory_edges ORDER BY id").fetchall())


def _run(c, **kw):
    kw = {"factor": F, "min_threshold": 0.15, "max_age_days": 45, **kw}
    r = temporal_edges.decay_confidence(c, **kw)
    c.commit()
    return r


EDGES = [(0.5, 60, "heuristic"), (0.8, 10, "manual"), (0.16, 50, "sleep_loop"), (0.6, 120, "sleep_loop")]


def test_second_run_on_the_same_day_changes_nothing(tmp_path, monkeypatch):
    """The owner's catch-up then the scheduled night (or any two runs in a row): the second is a no-op."""
    _clock(monkeypatch, T0)
    c = _brain(tmp_path, EDGES)
    _run(c)
    first = _confs(c)
    _clock(monkeypatch, T0 + timedelta(seconds=18))
    r2 = _run(c)
    assert r2["decayed_count"] == 0 and r2["pruned_count"] == 0, r2
    assert _confs(c) == first


def test_nightly_runs_equal_one_decay_over_the_same_days(tmp_path, monkeypatch):
    """Five nights of runs leave an edge at factor^(age in days), as if decayed once -- not compounded five times."""
    _clock(monkeypatch, T0)
    c = _brain(tmp_path, [(0.9, 20, "manual"), (0.9, 20, "heuristic")])
    for night in range(5):
        _clock(monkeypatch, T0 + timedelta(days=night))
        _run(c)
    got = _confs(c)
    assert got[1] == pytest.approx(0.9 * F ** 24, abs=5e-6)
    assert got[2] == pytest.approx(0.9 * (F ** 2) ** 24, abs=5e-6)


def test_catch_up_counts_from_the_last_completed_run_not_the_edge_age(tmp_path, monkeypatch):
    """A brain stamped by the pre-round-2 code (no metadata stamp; a ledger in LOCAL 'T' time) catches up only the
    days since its last completed run: those confidences already carry every earlier day."""
    _clock(monkeypatch, T0)
    last_local = (T0 - timedelta(days=9)).replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None).isoformat()
    c = _brain(tmp_path, [(0.6, 120, "sleep_loop"), (0.16, 50, "sleep_loop")], ledger=last_local)
    r = _run(c)
    assert r["pruned_count"] == 0, r
    got = _confs(c)
    assert got[1] == pytest.approx(round(0.6 * F ** 9, 6), abs=1e-6)
    assert got[2] == pytest.approx(round(0.16 * F ** 9, 6), abs=1e-6)


def test_a_small_daily_step_is_not_lost(tmp_path, monkeypatch):
    """A low-confidence edge (one day's step < 0.001) still decays night after night: the clock never skips it."""
    c = _brain(tmp_path, [(0.15, 0, "manual")])
    for night in range(1, 11):
        _clock(monkeypatch, T0 + timedelta(days=night))
        _run(c, min_threshold=0.01)
    assert _confs(c)[1] == pytest.approx(0.15 * F ** 10, abs=5e-6)


def test_guard_old_weak_edges_are_still_pruned(tmp_path, monkeypatch):
    """Guard (GREEN on both): pruning still happens -- below threshold and older than max_age_days."""
    _clock(monkeypatch, T0)
    c = _brain(tmp_path, [(0.1, 60, "sleep_loop"), (0.1, 10, "sleep_loop"), (0.9, 60, "sleep_loop")])
    r = _run(c)
    assert r["pruned_count"] == 1
    # R2d (Option A): a pruned edge is retired (valid_until set), not deleted -- it leaves the CURRENT graph.
    assert [i for (i,) in c.execute("SELECT id FROM memory_edges WHERE valid_until IS NULL ORDER BY id")] == [2, 3]


def test_dry_run_counts_what_the_run_does_and_writes_nothing(tmp_path, monkeypatch):
    _clock(monkeypatch, T0)
    c = _brain(tmp_path, EDGES + [(0.1, 60, "sleep_loop")])
    before = _confs(c)
    dry = temporal_edges.decay_confidence(c, factor=F, min_threshold=0.15, max_age_days=45, dry_run=True)
    assert _confs(c) == before
    assert c.execute("SELECT COUNT(*) FROM metadata").fetchone()[0] == 0
    real = _run(c)
    assert (dry["decayed_count"], dry["pruned_count"], dry["remaining_count"]) == \
           (real["decayed_count"], real["pruned_count"], real["remaining_count"])


def test_the_stamp_is_written_in_the_brains_utc_format(tmp_path, monkeypatch):
    _clock(monkeypatch, T0)
    c = _brain(tmp_path, EDGES)
    _run(c)
    v = c.execute("SELECT value FROM metadata WHERE key = 'edge_decay_last_run'").fetchone()[0]
    assert v == T0.strftime("%Y-%m-%d %H:%M:%S")
