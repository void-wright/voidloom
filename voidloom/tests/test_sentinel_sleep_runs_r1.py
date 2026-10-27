# -*- coding: utf-8 -*-
"""Round 1, lane L5: the organ-yield sentinel stops being a blind gate.

Measured (health check, 2026-10-14): eight stalled nights (runs 116-123 left
'running', each killed at the unit's 600 s wall) and the sentinel rated the sleep
loop's organs and the belief engine HEALTHY every morning, because each TABLE
grew. It now reads the run ledger directly (contract C5, as L3 built it in
migration 040), attributes edge yield to Stage 2, and carries a precision canary
for the belief engine.

The run ledger is built here in C5's shape (L3's migration 040 lands in another
lane). The unit directory is a double; nothing reads ~/.config.
"""
from __future__ import annotations

import datetime as dt
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

UNITS = {
    "wizard-library-sleep-loop.service": "/usr/bin/env python3 -u %h/.claude/memory/organs/claude_memory.py sleep",
    "wizard-library-belief-scan.service": "/usr/bin/env python3 %h/.claude/memory/organs/belief_cli.py scan",
}

# C5 as built (L3, migration_040_sleep_run_lifecycle.sql; for_L5/C5_as_built.md).
C5_TABLE = """
CREATE TABLE sleep_loop_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL, completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running','success','partial','error','timeout')),
    memories_consolidated INTEGER DEFAULT 0, clusters_found INTEGER DEFAULT 0, edges_extracted INTEGER DEFAULT 0,
    edges_decayed INTEGER DEFAULT 0, edges_pruned INTEGER DEFAULT 0, reflections_generated INTEGER DEFAULT 0,
    importance_adjustments INTEGER DEFAULT 0, consolidation_mode TEXT, error_log TEXT, config_snapshot TEXT,
    duration_seconds REAL, stage_reached TEXT, stage_log TEXT)
"""


@pytest.fixture(scope="module")
def schema_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("schema") / "base.db"
    out = schema_migrator.apply(str(path), ROOT / "migrations")
    assert out["complete"], out["files_with_errors"]
    c = sqlite3.connect(path)
    c.execute("DROP TABLE IF EXISTS sleep_loop_runs")
    c.execute(C5_TABLE)
    c.commit()
    c.close()
    return path


@pytest.fixture()
def brain(schema_db, tmp_path, monkeypatch):
    path = tmp_path / "brain.db"
    shutil.copy(schema_db, path)
    c = sqlite3.connect(path)
    # A brain 30 days old, with episodics, consolidation output on a nightly cadence, and edges.
    c.execute("INSERT INTO memories (content, summary, category, importance, kind, created_at, updated_at, "
              "last_accessed) VALUES ('first', 's', 'insight', 0.5, 'episodic', datetime('now','-30 days'), "
              "datetime('now'), datetime('now'))")
    for i in range(6):
        c.execute("INSERT INTO memories (content, summary, category, importance, kind, created_at, updated_at, "
                  "last_accessed) VALUES (?, 's', 'insight', 0.5, 'episodic', datetime('now','-1 days'), "
                  "datetime('now'), datetime('now'))", (f"m{i}",))
    c.commit()
    c.close()
    monkeypatch.setattr(S, "installed_units", lambda: (dict(UNITS), True))
    return path


def _runs(path, *rows):
    """rows: (status, started_at_sql_or_literal, completed_or_None, stage)."""
    c = sqlite3.connect(path)
    for status, started, done, stage in rows:
        c.execute(f"INSERT INTO sleep_loop_runs (status, started_at, completed_at, stage_reached) "
                  f"VALUES (?, {started}, {done or 'NULL'}, ?)", (status, stage))
    c.commit()
    c.close()


def _local_iso(hours_ago):
    """The pre-round-1 format: LOCAL time, 'T', microseconds."""
    return "'%s'" % (dt.datetime.now() - dt.timedelta(hours=hours_ago)).isoformat()


def _reading(path, organ):
    rs, _ = S.scan(str(path))
    return {r.organ: r for r in rs}[organ]


def test_a_stale_running_row_is_stalled(brain):
    _runs(brain, ("success", "datetime('now','-30 hours')", "datetime('now','-30 hours')", "finalize"),
          ("running", "datetime('now','-2 hours')", None, "edges"))
    r = _reading(brain, "sleep_loop.run")
    assert r.verdict == "STALLED", r.detail
    assert "stage reached edges" in r.detail


def test_the_measured_stall_shape_is_stalled_in_the_old_local_format(brain):
    """Rows 116-123: eight nights 'running', local 'T' stamps, last success nine days back."""
    _runs(brain, ("success", _local_iso(9 * 24), _local_iso(9 * 24 - 0.05), None),
          *[("running", _local_iso(24 * n + 12), None, None) for n in range(8, 0, -1)])
    r = _reading(brain, "sleep_loop.run")
    assert r.verdict == "STALLED" and "8 run(s) still 'running'" in r.detail, r.detail


def test_a_healthy_ledger_is_healthy(brain):
    _runs(brain, ("timeout", "datetime('now','-50 hours')", "datetime('now','-50 hours')", "edges"),
          ("success", "datetime('now','-26 hours')", "datetime('now','-26 hours')", "finalize"),
          ("partial", "datetime('now','-2 hours')", "datetime('now','-2 hours')", "finalize"))
    r = _reading(brain, "sleep_loop.run")
    assert r.verdict == "HEALTHY", r.detail


def test_a_run_in_progress_is_not_a_stall(brain):
    _runs(brain, ("success", "datetime('now','-20 hours')", "datetime('now','-20 hours')", "finalize"),
          ("running", "datetime('now','-2 minutes')", None, "edges"))
    assert _reading(brain, "sleep_loop.run").verdict == "HEALTHY"


def test_only_cut_runs_for_36_hours_is_stalled(brain):
    _runs(brain, ("success", "datetime('now','-80 hours')", "datetime('now','-80 hours')", "finalize"),
          ("timeout", "datetime('now','-50 hours')", "datetime('now','-50 hours')", "edges"),
          ("timeout", "datetime('now','-26 hours')", "datetime('now','-26 hours')", "edges"),
          ("timeout", "datetime('now','-2 hours')", "datetime('now','-2 hours')", "edges"))
    r = _reading(brain, "sleep_loop.run")
    assert r.verdict == "STALLED" and "3 run(s) since ended timeout/error" in r.detail, r.detail


def test_no_runs_reads_by_brain_age(brain, schema_db, tmp_path, monkeypatch):
    assert _reading(brain, "sleep_loop.run").verdict == "STALLED"     # 30 days old, never slept
    young = tmp_path / "young.db"
    shutil.copy(schema_db, young)
    c = sqlite3.connect(young)
    c.execute("INSERT INTO memories (content, summary, category, importance, kind, created_at, updated_at, "
              "last_accessed) VALUES ('x', 's', 'insight', 0.5, 'episodic', datetime('now','-3 hours'), "
              "datetime('now'), datetime('now'))")
    c.commit()
    c.close()
    assert _reading(young, "sleep_loop.run").verdict == "STARVED"      # the friend's first evening


def test_the_backlog_above_the_watermark_is_reported(brain):
    _runs(brain, ("success", "datetime('now','-2 hours')", "datetime('now','-2 hours')", "finalize"))
    c = sqlite3.connect(brain)
    c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('edge_watermark_id', '3')")
    c.commit()
    c.close()
    r = _reading(brain, "sleep_loop.run")
    assert r.input_n == 4 and "edge backlog 4 memories above watermark 3" in r.detail


# --------------------------------------------------------------------------
# The veto: an organ the stalled run writes cannot read HEALTHY.
# --------------------------------------------------------------------------
def _consolidation_cadence(path):
    c = sqlite3.connect(path)
    ids = [r[0] for r in c.execute("SELECT id FROM memories ORDER BY id LIMIT 4")]
    for k, hours in enumerate((40, 16)):          # two nightly bursts inside the 3-day window
        c.execute("INSERT INTO consolidation_sources (consolidated_memory_id, source_memory_id, cluster_id, "
                  "created_at) VALUES (?, ?, ?, datetime('now', ?))", (ids[0], ids[k + 1], k, f"-{hours} hours"))
    c.commit()
    c.close()


def test_a_growing_table_cannot_hide_a_stalled_run(brain):
    _consolidation_cadence(brain)
    _runs(brain, ("success", "datetime('now','-30 hours')", "datetime('now','-30 hours')", "finalize"))
    assert _reading(brain, "sleep_loop.consolidate_episodics").verdict == "HEALTHY"
    _runs(brain, ("running", "datetime('now','-5 hours')", None, "edges"))
    r = _reading(brain, "sleep_loop.consolidate_episodics")
    assert r.verdict == "STALLED" and "growth is not completion" in r.detail, r.detail


# --------------------------------------------------------------------------
# Attribution: other writers' edges are not Stage 2's yield.
# --------------------------------------------------------------------------
def test_edge_yield_is_attributed_to_stage_two(brain):
    _runs(brain, ("success", "datetime('now','-10 hours')", "datetime('now','-10 hours')", "finalize"))
    c = sqlite3.connect(brain)
    ids = [r[0] for r in c.execute("SELECT id FROM memories ORDER BY id")]
    for k, hours in enumerate((40, 16)):
        c.execute("INSERT INTO memory_edges (source_id, target_id, predicate, confidence, created_at, "
                  "extraction_method, metadata) VALUES (?, ?, 'references', 0.5, datetime('now', ?), "
                  "'sleep_loop', '{\"nrem\": true}')", (ids[k], ids[k + 2], f"-{hours} hours"))
        c.execute("INSERT INTO memory_edges (source_id, target_id, predicate, confidence, created_at, "
                  "extraction_method, metadata) VALUES (?, ?, 'contradicts', 0.9, datetime('now', ?), "
                  "'manual', '{\"authored_by\": \"belief_engine\"}')", (ids[k + 1], ids[k + 3], f"-{hours} hours"))
    c.commit()
    c.close()
    r = _reading(brain, "temporal_edges.extract_edges")
    assert r.verdict == "BROKEN" and r.output_rows == 0, r.detail      # dreams + beliefs are not Stage 2
    c = sqlite3.connect(brain)
    for k, hours in enumerate((40, 16)):
        c.execute("INSERT INTO memory_edges (source_id, target_id, predicate, confidence, created_at, "
                  "extraction_method, metadata) VALUES (?, ?, 'causes', 0.5, datetime('now', ?), "
                  "'sleep_loop', '{\"reason\": \"tier2\"}')", (ids[k + 4], ids[k], f"-{hours} hours"))
    c.commit()
    c.close()
    assert _reading(brain, "temporal_edges.extract_edges").verdict == "HEALTHY"


# --------------------------------------------------------------------------
# The belief engine's precision canary.
# --------------------------------------------------------------------------
def test_the_precision_canary_reads_the_false_class(brain):
    c = sqlite3.connect(brain)
    a = c.execute("INSERT INTO memories (content, summary, category, importance, kind, created_at, updated_at, "
                  "last_accessed) VALUES ('Distilled from 3 episodic memories (x). Core insight: a', 's1', "
                  "'insight', 0.5, 'semantic', datetime('now'), datetime('now'), datetime('now'))").lastrowid
    b = c.execute("INSERT INTO memories (content, summary, category, importance, kind, created_at, updated_at, "
                  "last_accessed) VALUES ('Distilled from 4 episodic memories (y). Core insight: b', 's2', "
                  "'insight', 0.5, 'semantic', datetime('now'), datetime('now'), datetime('now'))").lastrowid
    for s_, t_ in ((a, b), (b, a)):
        c.execute("INSERT INTO memory_edges (source_id, target_id, predicate, confidence, extraction_method, "
                  "metadata) VALUES (?, ?, 'contradicts', 0.99, 'manual', '{\"authored_by\":\"belief_engine\"}')",
                  (s_, t_))
    c.commit()
    c.close()
    r = _reading(brain, "belief_engine.precision")
    assert r.verdict == "BROKEN" and r.output_rows == 2, r.detail
    import belief_engine as BE
    c = sqlite3.connect(brain)
    BE.quarantine_template_pairs(c, apply=True)
    c.close()
    assert _reading(brain, "belief_engine.precision").verdict == "HEALTHY"


def test_stalled_exits_as_a_finding(brain, monkeypatch, capsys):
    _runs(brain, ("running", "datetime('now','-3 hours')", None, "edges"))
    monkeypatch.setattr(sys, "argv", ["organ_yield_sentinel.py", str(brain)])
    rc = S.main()
    out = capsys.readouterr().out
    run_line = [l for l in out.splitlines() if l.strip().startswith("sleep_loop.run ")]
    assert run_line and "STALLED" in run_line[0], out
    assert " 0 UNMEASURABLE" in out, out          # every watch could see this fixture
    assert rc == S.EXIT_BROKEN_FOUND, out          # STALLED is a finding, like BROKEN
