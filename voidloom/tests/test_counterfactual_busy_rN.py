# -*- coding: utf-8 -*-
"""Narrow pass N5(b) (model-choice): the quarterly counterfactual revisit's write survives a concurrent writer.

On the reference host (Oct 1 08:54 EDT, the OLD code) `wizard-library-counterfactual-revisit` died with
`sqlite3.OperationalError: database is locked` at its revisit UPDATE: the self-eval's catch-up, fired in the same second
after the host woke, held the brain's write lock longer than Python's DEFAULT busy wait (5 s). Here a second connection
holds a write transaction (BEGIN IMMEDIATE + a write) for HOLD_S > 5 s while `run_quarterly_revisit` runs: on the Round 2
bytes the revisit raises `database is locked` after ~5 s (RED); with the 60 s busy wait it waits, then records the revisit
(GREEN). A control without the writer passes on both.
"""
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import counterfactual_memory as cm

HOLD_S = 7.0


@pytest.fixture
def brain(tmp_path):
    db = tmp_path / "brain.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, summary TEXT, content TEXT, is_archived INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO memories (id, summary, content) VALUES (1, 'a decision', 'we chose A over B')")
    conn.commit()
    conn.close()
    assert cm.apply_counterfactual_migration(str(db))["status"] == "ok"
    conn = cm._get_conn(str(db))
    r = cm.capture_counterfactual(conn, 1, "A or B", "A", [{"path": "B", "predicted_outcome": "x", "rationale": "y"}],
                                  council_weight_score=0.9)
    assert r["status"] == "ok", r
    conn.execute("UPDATE counterfactual_decisions SET captured_at = '2025-01-01 00:00:00'")   # due for revisit
    conn.commit()
    conn.close()
    return db


def _hold_write_lock(db, held: threading.Event, done: dict):
    """The concurrent writer (the self-eval's shape): one write transaction held open for HOLD_S, then committed."""
    w = sqlite3.connect(str(db), isolation_level=None)
    w.execute("BEGIN IMMEDIATE")
    w.execute("UPDATE memories SET content = content || '' WHERE id = 1")
    held.set()
    time.sleep(HOLD_S)
    w.execute("COMMIT")
    done["released_at"] = time.monotonic()
    w.close()


def _revisit(db):
    t0 = time.monotonic()
    try:
        res = cm.run_quarterly_revisit(str(db), use_llm=False)
        return res, None, time.monotonic() - t0
    except sqlite3.OperationalError as e:
        return None, str(e), time.monotonic() - t0


def test_the_revisit_write_waits_out_a_concurrent_writer(brain):
    held, done = threading.Event(), {}
    t = threading.Thread(target=_hold_write_lock, args=(brain, held, done), daemon=True)
    t.start()
    assert held.wait(10)
    res, err, took = _revisit(brain)
    t.join(HOLD_S + 10)
    assert err is None, "the revisit died while another writer held the brain: %s (after %.1f s)" % (err, took)
    assert res["status"] == "ok" and res["revisited_count"] == 1, res
    assert took >= HOLD_S - 1.5, took                    # it WAITED for the writer (not a lucky ordering)
    row = sqlite3.connect(str(brain)).execute(
        "SELECT status, revisit_count, revisit_notes FROM counterfactual_decisions").fetchone()
    assert row[0] == "revisited" and row[1] == 1 and json.loads(row[2])[0]["outcome"] == "still_active", row


def test_control_without_a_concurrent_writer(brain):
    res, err, took = _revisit(brain)
    assert err is None and res["revisited_count"] == 1 and took < 5, (res, err, took)


def test_the_connection_carries_the_busy_wait(brain):
    conn = cm._get_conn(str(brain))
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 60000
    finally:
        conn.close()
