# -*- coding: utf-8 -*-
"""Round 2, lane R2d, option C (owner ruling 2026-10-15): the one-time repair of the per-run compounding.

Each brain here is written the way the live one was: a sleep_loop_runs ledger in the old code's LOCAL 'T' clock with
its config_snapshot, and edge confidences produced by REPLAYING the pre-round-2 decay (copied verbatim below as
`_old_decay`) at each run. The repair must recover each edge's per-UTC-day value from that data alone.
RED on e67ab9d (no repair_decay); every brain is a scratch file; no model is loaded.
"""
import hashlib
import json
import os
import sqlite3
import subprocess
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
CFG = json.dumps({"decay_factor": F, "prune_threshold": 0.15, "max_age_days": 45})
T0 = datetime(2026, 10, 15, 12, 0, 0)            # "now" (UTC) for the repair and the catch-up
SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE memory_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER NOT NULL, target_id INTEGER NOT NULL,
    predicate TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0.8, valid_from TEXT, valid_until TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')), metadata TEXT, extraction_method TEXT DEFAULT 'manual',
    UNIQUE(source_id, target_id, predicate));
CREATE TABLE sleep_loop_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, completed_at TEXT, status TEXT,
    config_snapshot TEXT, edges_decayed INTEGER DEFAULT 0, edges_pruned INTEGER DEFAULT 0);
"""


def _old_decay(c, now, factor=F, min_threshold=0.15, max_age_days=45):
    """The pre-round-2 decay_confidence body (742e5da..9e36990), with `now` passed in instead of datetime.now()."""
    for edge_id, confidence, created_at_str, extraction_method in c.execute(
            "SELECT id, confidence, created_at, extraction_method FROM memory_edges WHERE valid_until IS NULL"
    ).fetchall():
        created_at = datetime.fromisoformat(created_at_str)
        days = (now - created_at).days
        if days <= 0:
            continue
        effective_factor = factor ** 2 if extraction_method == 'heuristic' else factor
        new_confidence = confidence * (effective_factor ** days)
        if new_confidence < min_threshold and days > max_age_days:
            c.execute("DELETE FROM memory_edges WHERE id = ?", (edge_id,))
        elif abs(new_confidence - confidence) > 0.001:
            c.execute("UPDATE memory_edges SET confidence = ? WHERE id = ?", (round(new_confidence, 6), edge_id))


# (c0, created days before T0, method, metadata)
# As on the live brain: the old code deleted every edge it drove under 0.15 past 45 days, so the damaged survivors
# are those that crossed 45 days during the stall (no run). Ages here: 46-54 days at T0, at most 44 at the last run.
EDGES = [
    (0.6, 54, "sleep_loop", {"reason": "cosine"}),
    (0.7, 50, "sleep_loop", {"reason": "same entity"}),
    (0.6, 40, "heuristic", {"reason": "cosine"}),
    (0.8, 30, "sleep_loop", {"reason": "supersedes"}),
    (0.6, 52, "sleep_loop", {"reason": "cosine", "nrem": "co_recalled"}),          # NREM moved it: not repairable
    (0.83, 47, "manual", {"authored_by": "belief_engine", "nli_score": 0.83}),
    (0.5, 49, "manual", {"note": "written by hand"}),                              # writer unknown: not repairable
    (0.6, 1, "sleep_loop", {"reason": "cosine"}),                                  # created after the last run
]


def _clock(monkeypatch, at):
    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.replace(tzinfo=timezone.utc).astimezone(tz) if tz else at
    monkeypatch.setattr(temporal_edges, "datetime", _DT)


def _brain(tmp_path, nights=60, stalled=10):
    """Edges born at their times; a nightly old-code run (LOCAL clock = UTC-4 h here) for `nights` nights up to
    `stalled`+1 days ago, then `stalled` 'running' rows that never reached the decay (as on the live brain)."""
    c = sqlite3.connect(tmp_path / "b.db")
    c.executescript(SCHEMA)
    for i, (c0, days, meth, meta) in enumerate(EDGES):
        c.execute("INSERT INTO memory_edges (source_id, target_id, predicate, confidence, created_at, metadata,"
                  " extraction_method) VALUES (?,?,?,?,?,?,?)",
                  (i + 1, i + 101, "references", c0, (T0 - timedelta(days=days, hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
                   json.dumps(meta), meth))
    for k in range(nights, stalled, -1):
        start_local = (T0 - timedelta(days=k)).replace(hour=22, minute=30) - timedelta(hours=4)
        nrem_bump = k == 20
        _old_decay(c, start_local + timedelta(seconds=30))
        if nrem_bump:                                    # NREM potentiation, which keeps no ledger
            c.execute("UPDATE memory_edges SET confidence = MIN(0.97, confidence + 0.05) WHERE id = 5")
        c.execute("INSERT INTO sleep_loop_runs (started_at, completed_at, status, config_snapshot) VALUES (?,?,?,?)",
                  (start_local.isoformat(), (start_local + timedelta(minutes=2)).isoformat(), "success", CFG))
    for k in range(stalled, 0, -1):
        start_local = (T0 - timedelta(days=k)).replace(hour=22, minute=30) - timedelta(hours=4)
        c.execute("INSERT INTO sleep_loop_runs (started_at, status, config_snapshot) VALUES (?,?,?)",
                  (start_local.isoformat(), "running", CFG))
    c.commit()
    return c


def _confs(c):
    return dict(c.execute("SELECT id, confidence FROM memory_edges ORDER BY id"))


def _fp(c):
    return hashlib.sha256(repr(sorted(_confs(c).items())).encode()).hexdigest()


def test_dry_run_equals_apply_and_recovers_each_known_edge(tmp_path, monkeypatch):
    _clock(monkeypatch, T0)
    c = _brain(tmp_path)
    before = _confs(c)
    dry = temporal_edges.repair_decay(c, apply=False)
    assert _confs(c) == before                                               # a dry run writes nothing
    app = temporal_edges.repair_decay(c, apply=True)
    c.commit()
    for k in ("edges_examined", "edges_repaired", "edges_left_unchanged", "left_unchanged_by_reason",
              "next_run_would_retire_without_repair", "of_those_survive_after_repair"):
        assert dry[k] == app[k], k
    assert app["left_unchanged_by_reason"] == {"nrem-moved": 1, "origin-unknown": 1, "undamaged": 1}
    assert app["edges_repaired"] == 5
    assert [r["settled_as"] for r in app["runs_whose_status_did_not_prove_a_decay"]] == ["did not decay"] * 10
    anchor, _ = temporal_edges.decay_anchor(c)
    after = _confs(c)
    for eid, (c0, days, meth, meta) in enumerate(EDGES, start=1):
        if eid in (5, 7, 8):
            assert after[eid] == before[eid]                                     # unchanged, counted
            continue
        created = (T0 - timedelta(days=days, hours=3))
        ef = F ** 2 if meth == "heuristic" else F
        assert after[eid] == round(c0 * ef ** (anchor.date() - created.date()).days, 6)
        assert before[eid] < after[eid] <= c0                                    # raised, never above the original


def test_the_next_decay_counts_from_the_repairs_anchor_no_day_twice(tmp_path, monkeypatch):
    _clock(monkeypatch, T0)
    c = _brain(tmp_path)
    temporal_edges.repair_decay(c, apply=True)
    c.commit()
    assert c.execute("SELECT value FROM metadata WHERE key = 'edge_decay_last_run'").fetchone() is not None
    r = temporal_edges.decay_confidence(c, factor=F, min_threshold=0.15, max_age_days=45)
    c.commit()
    # only the two edges the repair could not vouch for are retired; without it 1, 2 and 6 go too (guard below)
    assert [i for (i,) in c.execute("SELECT id FROM memory_edges WHERE valid_until IS NOT NULL ORDER BY id")] == [5, 7]
    got = _confs(c)
    for eid in (1, 2, 3, 4, 6):
        c0, days, meth, _m = EDGES[eid - 1]
        ef = F ** 2 if meth == "heuristic" else F
        created = (T0 - timedelta(days=days, hours=3)).date()
        assert got[eid] == pytest.approx(c0 * ef ** (T0.date() - created).days, abs=2e-6)


def test_guard_without_the_repair_the_catch_up_retires_them(tmp_path, monkeypatch):
    """Guard (GREEN before and after): the damage is real on this brain -- the compounding put edges below the line."""
    _clock(monkeypatch, T0)
    c = _brain(tmp_path)
    r = temporal_edges.decay_confidence(c, factor=F, min_threshold=0.15, max_age_days=45)
    c.commit()
    assert [i for (i,) in c.execute("SELECT id FROM memory_edges WHERE valid_until IS NOT NULL ORDER BY id")] == \
        [1, 2, 5, 6, 7], r


def test_undo_restores_the_fingerprint_and_a_second_apply_changes_nothing(tmp_path, monkeypatch):
    _clock(monkeypatch, T0)
    c = _brain(tmp_path)
    fp0 = _fp(c)
    meta0 = sorted(c.execute("SELECT key, value FROM metadata").fetchall())
    temporal_edges.repair_decay(c, apply=True)
    c.commit()
    fp1 = _fp(c)
    second = temporal_edges.repair_decay(c, apply=True)
    c.commit()
    assert second["status"] == "nothing-to-repair" and second["edges_repaired"] == 0 and _fp(c) == fp1
    assert second["left_unchanged_by_reason"]["already-repaired"] == 5
    u = temporal_edges.repair_decay(c, undo=True)
    c.commit()
    assert u["restored"] == 5 and u["moved_since_repair_left_as_is"] == 0 and u["anchor_stamp_removed"]
    assert _fp(c) == fp0
    assert sorted(c.execute("SELECT key, value FROM metadata").fetchall()) == meta0


def test_refuses_once_the_per_day_decay_has_run(tmp_path, monkeypatch):
    _clock(monkeypatch, T0)
    c = _brain(tmp_path)
    temporal_edges.decay_confidence(c, factor=F, min_threshold=0.15, max_age_days=45)
    c.commit()
    fp = _fp(c)
    r = temporal_edges.repair_decay(c, apply=True)
    assert r["status"] == "refused" and _fp(c) == fp


def test_a_stalled_run_that_did_decay_is_settled_as_decayed(tmp_path, monkeypatch):
    """The data, not the status, decides: a 'running' row whose decay DID land is replayed as decayed."""
    _clock(monkeypatch, T0)
    c = _brain(tmp_path)
    first_stalled = c.execute("SELECT started_at FROM sleep_loop_runs WHERE status = 'running' ORDER BY id"
                              " LIMIT 1").fetchone()[0]
    _old_decay(c, datetime.fromisoformat(first_stalled) + timedelta(seconds=30))
    c.commit()
    r = temporal_edges.repair_decay(c, apply=False)
    assert [x["settled_as"] for x in r["runs_whose_status_did_not_prove_a_decay"]] == \
        ["decayed"] + ["did not decay"] * 9
    assert r["left_unchanged_by_reason"].get("history-does-not-replay", 0) == 0
    assert r["edges_repaired"] >= 3


def test_cli_dry_run_by_default(tmp_path, monkeypatch):
    _clock(monkeypatch, T0)
    c = _brain(tmp_path)
    c.close()
    fp = hashlib.sha256((tmp_path / "b.db").read_bytes()).hexdigest()
    p = subprocess.run([sys.executable, str(ORGANS / "temporal_edges.py"), "repair-decay", str(tmp_path / "b.db")],
                       capture_output=True, text=True, env={**os.environ, "CUDA_VISIBLE_DEVICES": ""})
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["status"] == "dry-run"
    assert hashlib.sha256((tmp_path / "b.db").read_bytes()).hexdigest() == fp


def test_undo_after_a_night_has_moved_every_repaired_edge_says_nothing_undone(tmp_path, monkeypatch):
    """Round 2 reflection N1: after a night has run, --undo restored 0 links yet reported 'undone' (rc 0)."""
    _clock(monkeypatch, T0)
    c = _brain(tmp_path)
    temporal_edges.repair_decay(c, apply=True)
    c.commit()
    _clock(monkeypatch, T0 + timedelta(days=2))
    temporal_edges.decay_confidence(c, factor=F, min_threshold=0.15, max_age_days=45)
    c.commit()
    u = temporal_edges.repair_decay(c, undo=True)
    assert u["restored"] == 0 and u["moved_since_repair_left_as_is"] > 0
    assert u["status"] == "nothing-undone", u
