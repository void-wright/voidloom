# -*- coding: utf-8 -*-
"""Round 2, lane R2c: the organ-yield sentinel on the night of the owner's catch-up.

m6 -- a 'running' row older than the unit's 600 s wall read STALLED even while the owner's unbudgeted catch-up was
      alive and holding the brain's run-lock (and, after a stall, "no completed run in 36 h" read STALLED too).
l4 -- right after a successful catch-up the run ledger read HEALTHY while temporal_edges.extract_edges read BROKEN
      ("only ONE burst in the last 3d") until the next real night: the ledger vetoed but never vouched.
l5 -- `--json` printed the JSON object and then the human summary on stdout, so json.load(stdout) failed.

The lock is taken with the sleep loop's OWN primitive (sleep_loop._acquire_lock) on the brain's own lock path. The unit
directory is a double. Each test is RED on 6bb45ee and GREEN on lane/R2c unless its docstring calls it a guard.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORGANS = ROOT / "organs"
if str(ORGANS) not in sys.path:
    sys.path.insert(0, str(ORGANS))

import organ_yield_sentinel as S  # noqa: E402
import schema_migrator  # noqa: E402
import sleep_loop  # noqa: E402

UNITS = {
    "wizard-library-sleep-loop.service": "/usr/bin/env python3 -u %h/.claude/memory/organs/claude_memory.py sleep",
    "wizard-library-belief-scan.service": "/usr/bin/env python3 %h/.claude/memory/organs/belief_cli.py scan",
}


@pytest.fixture(scope="module")
def schema_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("schema") / "base.db"
    out = schema_migrator.apply(str(path), ROOT / "migrations")      # 040: stage_reached + stage_log
    assert out["complete"], out["files_with_errors"]
    return path


@pytest.fixture()
def brain(schema_db, tmp_path, monkeypatch):
    path = tmp_path / "brain.db"
    shutil.copy(schema_db, path)
    c = sqlite3.connect(path)
    c.execute("INSERT INTO memories (content, summary, category, importance, kind, created_at, updated_at, "
              "last_accessed) VALUES ('first', 's', 'insight', 0.5, 'episodic', datetime('now','-60 days'), "
              "datetime('now'), datetime('now'))")
    for i in range(8):
        c.execute("INSERT INTO memories (content, summary, category, importance, kind, created_at, updated_at, "
                  "last_accessed) VALUES (?, 's', 'insight', 0.5, 'episodic', datetime('now','-2 days'), "
                  "datetime('now'), datetime('now'))", (f"m{i}",))
    c.commit()
    c.close()
    monkeypatch.setattr(S, "installed_units", lambda: (dict(UNITS), True))
    return path


def _run(path, status, started, done=None, stage=None, stage_log=None):
    c = sqlite3.connect(path)
    c.execute(f"INSERT INTO sleep_loop_runs (status, started_at, completed_at, stage_reached, stage_log) "
              f"VALUES (?, {started}, {done or 'NULL'}, ?, ?)",
              (status, stage, json.dumps(stage_log) if stage_log else None))
    c.commit()
    c.close()


def _reading(path, organ):
    rs, _ = S.scan(str(path))
    return {r.organ: r for r in rs}[organ]


@pytest.fixture()
def held_lock(brain):
    lock, _note = sleep_loop.run_lock_path(str(brain))
    assert sleep_loop._acquire_lock(lock)
    yield lock
    sleep_loop._release_lock(lock)


STALL = ("success", "datetime('now','-10 days')", "datetime('now','-10 days')", "finalize")


def test_a_live_catch_up_past_the_wall_is_not_stalled(brain, held_lock):
    """The owner's catch-up after the 10-day stall, 2 h in, holding the run-lock."""
    _run(brain, *STALL)
    _run(brain, "running", "datetime('now','-2 hours')", None, "edges")
    r = _reading(brain, "sleep_loop.run")
    assert r.verdict == "HEALTHY" and r.detail.startswith("RUNNING:") and "run-lock" in r.detail, r.detail


def test_a_live_run_does_not_veto_the_organs_it_feeds(brain, held_lock):
    """Consolidation on a nightly cadence, a completed run 30 h ago, and tonight's run live for 2 h: the veto
    ('growth is not completion') must not fire on a run that is still working."""
    c = sqlite3.connect(brain)
    ids = [r[0] for r in c.execute("SELECT id FROM memories ORDER BY id LIMIT 4")]
    for k, hours in enumerate((40, 16)):
        c.execute("INSERT INTO consolidation_sources (consolidated_memory_id, source_memory_id, cluster_id, "
                  "created_at) VALUES (?, ?, ?, datetime('now', ?))", (ids[0], ids[k + 1], k, f"-{hours} hours"))
    c.commit()
    c.close()
    _run(brain, "success", "datetime('now','-30 hours')", "datetime('now','-30 hours')", "finalize")
    _run(brain, "running", "datetime('now','-2 hours')", None, "edges")
    r = _reading(brain, "sleep_loop.consolidate_episodics")
    assert r.verdict == "HEALTHY" and "growth is not completion" not in r.detail, r.detail


def test_guard_the_same_rows_without_the_lock_are_stalled(brain):
    """Guard (GREEN on both): no process holds the lock -> the old row was killed -> STALLED."""
    _run(brain, *STALL)
    _run(brain, "running", "datetime('now','-2 hours')", None, "edges")
    lock, _ = sleep_loop.run_lock_path(str(brain))
    Path(lock).write_text("")                                   # a lock FILE alone is not a held lock
    assert _reading(brain, "sleep_loop.run").verdict == "STALLED"


def test_guard_the_probe_leaves_the_lock_free_for_the_next_run(brain):
    """Guard: the sentinel's probe releases at once -- a sleep run can take the lock right after a scan."""
    _run(brain, *STALL)
    _run(brain, "running", "datetime('now','-2 hours')", None, "edges")
    lock, _ = sleep_loop.run_lock_path(str(brain))
    Path(lock).write_text("")
    S.scan(str(brain))
    assert sleep_loop._acquire_lock(lock)
    sleep_loop._release_lock(lock)


def _stage2_burst(path, hours_ago):
    c = sqlite3.connect(path)
    ids = [r[0] for r in c.execute("SELECT id FROM memories ORDER BY id")]
    for k in range(4):
        c.execute("INSERT INTO memory_edges (source_id, target_id, predicate, confidence, created_at, "
                  "extraction_method, metadata) VALUES (?, ?, 'references', 0.5, datetime('now', ?), 'sleep_loop', "
                  "'{\"reason\": \"tag_co_occurrence\"}')", (ids[k], ids[k + 4], f"-{hours_ago} hours"))
    c.commit()
    c.close()


def test_the_catch_up_vouches_for_its_one_burst(brain):
    _run(brain, *STALL)
    _run(brain, "success", "datetime('now','-1 hours')", "datetime('now','-1 hours')", "finalize",
         {"edges": {"processed": 3361, "edges_created": 8616}, "consolidation": {"consolidated": 0}})
    _stage2_burst(brain, 1)
    r = _reading(brain, "temporal_edges.extract_edges")
    assert r.verdict == "HEALTHY" and "vouches" in r.detail, r.detail


def test_guard_a_run_that_did_no_stage_two_vouches_for_nothing(brain):
    """Guard (GREEN on both): a burst with no run whose Stage 2 processed input stays BROKEN (one write is a pulse)."""
    _run(brain, *STALL)
    _run(brain, "success", "datetime('now','-1 hours')", "datetime('now','-1 hours')", "finalize",
         {"edges": {"processed": 0, "edges_created": 0}})
    _stage2_burst(brain, 1)
    assert _reading(brain, "temporal_edges.extract_edges").verdict == "BROKEN"


def test_json_stdout_is_only_json(brain, monkeypatch):
    _run(brain, "success", "datetime('now','-1 hours')", "datetime('now','-1 hours')", "finalize")
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "argv", ["organ_yield_sentinel.py", str(brain), "--json"])
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    S.main()
    doc = json.loads(out.getvalue())
    assert "readings" in doc and "meta" in doc
    assert "watched" in err.getvalue()                          # the human summary went to stderr
