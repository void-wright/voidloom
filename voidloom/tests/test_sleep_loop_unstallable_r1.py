# -*- coding: utf-8 -*-
"""Round 1, lane L3: the nightly sleep loop cannot stall, and when it is stopped it says so.

MEASURED (health check 2026-10-14, diagnosis.json `sleep_loop_root_cause`, reproduced on a backup-API copy of the
reference brain): the loop timed out EVERY night from 2026-10-06 to 10-13.
  * Stage 2's Tier-3 query JOINed a whole project to the vec0 tier table before its LIMIT 200 -- ~1.9-2.5 s per memory
    (89.6% of 36,277 py-spy samples at temporal_edges.py:563);
  * the Stage-2 watermark was the last SUCCESSFUL run, so each failed night re-faced a larger backlog (939 -> 3,245);
  * nothing closed a run row when systemd killed the process: rows 116-123 sat 'running', counters 0;
  * run times were LOCAL isoformat ('T') compared with UTC memories.created_at (a space);
  * Stage 1's pure-Python pairwise cosine took 161.8 s for 607 episodics; the friend pack's 1,532 project to ~1,030 s;
  * launchd gets no timeout at all, so on macOS nothing would ever end a stalled run.

Each test below is RED on f275d09 and GREEN on lane/L3 unless its docstring says it is a guard (an equivalence the fix
must keep). Every brain here is a scratch file under tmp_path; no model is loaded (Stage 4's embed is never reached:
the fixtures hold fewer episodics than min_unreflected_for_reflection, and Stage 1 writes are dry-run or stubbed).
"""
import json
import math
import os
import random
import re
import signal
import sqlite3
import struct
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGANS = ROOT / "organs"
MIGRATIONS = ROOT / "migrations"
if str(ORGANS) not in sys.path:
    sys.path.insert(0, str(ORGANS))

import sleep_loop  # noqa: E402
import temporal_edges  # noqa: E402

UTC_FMT = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
QUIET = {"quickening_enabled": False, "communities_enabled": False, "min_unreflected_for_reflection": 10 ** 9}


# ----------------------------------------------------------------------------------------------------------------------
# fixture brain
# ----------------------------------------------------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL, summary TEXT, category TEXT DEFAULT 'project_knowledge', importance REAL DEFAULT 0.5,
    source_project TEXT, source_context TEXT, kind TEXT DEFAULT 'semantic', confidence REAL DEFAULT 1.0,
    derived_from TEXT, consolidated_at TEXT, is_archived INTEGER DEFAULT 0, access_count INTEGER DEFAULT 0,
    last_accessed TEXT, created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')),
    version INTEGER DEFAULT 1
);
CREATE INDEX idx_memories_project ON memories(source_project);
CREATE INDEX idx_memories_created ON memories(created_at);
CREATE INDEX idx_memories_archived ON memories(is_archived);
CREATE TABLE memory_tags (memory_id INTEGER, tag TEXT, UNIQUE(memory_id, tag));
"""


def _vec(rng, dim, base=None, noise=1.0):
    """A unit vector; with `base`, base plus noise of norm ~`noise` (cosine to base ~ 1/sqrt(1+noise^2))."""
    v = [rng.gauss(0, 1) * noise / math.sqrt(dim) + (base[i] if base else 0.0) for i in range(dim)]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _pack(v):
    return struct.pack("%df" % len(v), *v)


def make_brain(path, with_040=True, counting_view=False):
    """A brain with the sleep loop's tables (migration 003 + 005 [+ 040] from THIS tree) and a declared nomic tier.

    counting_view: the tier 'table' is a VIEW over the real store that counts every vector a query reads through it,
    via the SQL function read_vec(); `conn.reads` holds the count. That is how the Tier-3 cost is measured without a
    clock: a JOIN-then-LIMIT reads every project vector; ids-first reads only the candidates'.
    """
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    if counting_view:
        conn.executescript("""
            CREATE TABLE emb_store (memory_id INTEGER PRIMARY KEY, embedding BLOB);
            CREATE VIEW memory_embeddings AS SELECT memory_id, read_vec(embedding) AS embedding FROM emb_store;
        """)
    else:
        conn.execute("CREATE TABLE memory_embeddings (memory_id INTEGER PRIMARY KEY, embedding BLOB)")
    conn.execute("INSERT INTO metadata (key, value) VALUES ('active_embedding_table', 'memory_embeddings')")
    conn.executescript((MIGRATIONS / "migration_003_temporal_edges.sql").read_text())
    conn.executescript((MIGRATIONS / "migration_005_sleep_loop.sql").read_text())
    if with_040:
        m040 = sorted(MIGRATIONS.glob("migration_040_*.sql"))
        assert m040, "migration 040 (the sleep run lifecycle) is not in this tree"
        conn.executescript(m040[0].read_text())
    conn.commit()
    conn.close()
    return str(path)


class _CountingConnection(sqlite3.Connection):
    reads = 0


def counting_conn(db):
    conn = sqlite3.connect(db, factory=_CountingConnection)
    conn.reads = 0

    def read_vec(b):
        conn.reads += 1
        return b

    conn.create_function("read_vec", 1, read_vec, deterministic=True)
    return conn


def add_memory(conn, project, emb=None, created_at=None, kind="semantic", archived=0, content=None, store=None):
    cur = conn.execute(
        "INSERT INTO memories (content, summary, source_project, kind, is_archived, created_at) "
        "VALUES (?, ?, ?, ?, ?, COALESCE(?, datetime('now')))",
        (content or "memory about %s %d" % (project, random.random() * 1e9), "s", project, kind, archived,
         created_at))
    mid = cur.lastrowid
    if emb is not None:
        conn.execute("INSERT INTO %s (memory_id, embedding) VALUES (?, ?)" % (store or "memory_embeddings"),
                     (mid, _pack(emb)))
    return mid


def runs(db):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute("SELECT * FROM sleep_loop_runs ORDER BY id")]
    finally:
        c.close()


def watermark(db):
    c = sqlite3.connect(db)
    try:
        r = c.execute("SELECT value FROM metadata WHERE key = 'edge_watermark_id'").fetchone()
        return int(r[0]) if r else None
    finally:
        c.close()


@pytest.fixture(autouse=True)
def _no_real_writer(monkeypatch):
    """No test here may reach the real embedding writer (a model load)."""
    monkeypatch.setattr(sleep_loop, "_vl_mw", None, raising=False)


# ----------------------------------------------------------------------------------------------------------------------
# Stage 2, Tier 3: ids first
# ----------------------------------------------------------------------------------------------------------------------

def test_stage2_reads_candidate_vectors_not_the_whole_project(tmp_path):
    """The stall's mechanism, measured by vectors read. 1,000 memories in one project + 40 new ones: the JOIN read every
    project vector for every input (~40,000); ids-first + the per-pass cache read ~one candidate set per project plus
    each input's own vector."""
    db = make_brain(tmp_path / "b.db", counting_view=True)
    rng = random.Random(7)
    conn = sqlite3.connect(db)
    old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    for _ in range(1000):
        add_memory(conn, "proj", _vec(rng, 16), created_at=old, store="emb_store")
    conn.execute("INSERT INTO metadata (key, value) VALUES ('edge_watermark_id', ?)",
                 (str(conn.execute("SELECT MAX(id) FROM memories").fetchone()[0]),))
    for _ in range(40):
        add_memory(conn, "proj", _vec(rng, 16), store="emb_store")
    conn.commit()
    conn.close()

    c = counting_conn(db)
    res = sleep_loop.extract_new_edges(c, dry_run=True)
    c.close()
    assert res["memories_processed"] == 40, res
    assert c.reads <= 2 * 201 + 2 * 40 + 50, (
        "Stage 2 read %d vectors for 40 inputs in a 1,040-memory project: the Tier-3 candidates are re-read for every "
        "memory (and on the vec0 tier the JOIN-then-LIMIT scanned the whole table: the 2026-10-06..13 stall)" % c.reads)


def _old_tier3_targets(conn, mid, threshold=0.85, top_k=10):
    """What f275d09's Tier-3 block proposed, computed here from its own query (the reference the fix must equal)."""
    proj = conn.execute("SELECT source_project FROM memories WHERE id = ?", (mid,)).fetchone()[0]
    me = conn.execute("SELECT embedding FROM memory_embeddings WHERE memory_id = ?", (mid,)).fetchone()
    if not me or not proj:
        return {}
    rows = conn.execute(
        """SELECT m.id, e.embedding FROM memories m JOIN memory_embeddings e ON e.memory_id = m.id
           WHERE m.source_project = ? AND m.id != ? AND m.is_archived = 0
           ORDER BY m.created_at DESC LIMIT 200""", (proj, mid)).fetchall()
    sims = [(oid, temporal_edges._cosine_similarity_from_bytes(me[0], e)) for oid, e in rows]
    sims = sorted([s for s in sims if s[1] > threshold], key=lambda x: x[1], reverse=True)[:top_k]
    return {oid: round(s, 4) for oid, s in sims}


@pytest.mark.parametrize("use_cache", [False, True])
def test_tier3_candidates_equal_the_join_it_replaced(tmp_path, use_cache):
    """GUARD (green on both trees): the same 'refines' proposals as f275d09's JOIN, on a brain with 260 memories in a
    project (some archived, some with no vector, a second project), distinct timestamps, near-duplicates."""
    db = make_brain(tmp_path / "b.db")
    rng = random.Random(11)
    conn = sqlite3.connect(db)
    base = _vec(rng, 24)
    t0 = datetime(2026, 9, 1)
    ids = []
    for i in range(260):
        ts = (t0 + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S")
        emb = None if i % 13 == 0 else _vec(rng, 24, base=base if i % 3 else None, noise=0.25 if i % 3 else 1.0)
        ids.append(add_memory(conn, "p" if i % 7 else "q", emb, created_at=ts, archived=1 if i % 17 == 0 else 0))
    conn.commit()
    if not hasattr(temporal_edges, "Tier3Cache"):
        if use_cache:
            pytest.skip("this tree has no Tier3Cache")
        cache = None
    else:
        cache = temporal_edges.Tier3Cache() if use_cache else None
    checked = 0
    for mid in ids[::5]:
        want = _old_tier3_targets(conn, mid)
        kw = {"tier3_cache": cache} if cache is not None else {}
        got = {p["target_id"]: p["metadata"]["cosine_similarity"]
               for p in temporal_edges.extract_edges_heuristic(conn, mid, top_k=10, **kw)
               if p["predicate"] == "refines"}
        assert set(got) == set(want), (mid, sorted(got), sorted(want))
        for k in want:
            assert abs(got[k] - want[k]) <= 1e-4, (mid, k, got[k], want[k])
        checked += bool(want)
    assert checked >= 10, "the fixture produced too few Tier-3 proposals to compare"
    if cache is not None:
        assert cache.loads == 2, cache.loads            # one candidate set per project, read once


def test_tier3_cache_pure_python_equals_numpy(tmp_path):
    """GUARD: a Tier-0 interpreter (no numpy) gets the same Tier-3 answer."""
    if not hasattr(temporal_edges, "Tier3Cache"):
        pytest.fail("this tree has no Tier3Cache: Stage 2 re-reads each project's candidates for every memory")
    pytest.importorskip("numpy")
    db = make_brain(tmp_path / "b.db")
    rng = random.Random(3)
    conn = sqlite3.connect(db)
    base = _vec(rng, 32)
    ids = [add_memory(conn, "p", _vec(rng, 32, base=base, noise=0.3)) for _ in range(120)]
    conn.commit()
    a, b = temporal_edges.Tier3Cache(use_numpy=True), temporal_edges.Tier3Cache(use_numpy=False)
    for mid in ids[::7]:
        emb = conn.execute("SELECT embedding FROM memory_embeddings WHERE memory_id = ?", (mid,)).fetchone()[0]
        x = sorted((i, round(s, 6)) for i, s in a.similar(conn, "p", mid, emb))
        y = sorted((i, round(s, 6)) for i, s in b.similar(conn, "p", mid, emb))
        assert x == y and x, (mid, x[:3], y[:3])


# ----------------------------------------------------------------------------------------------------------------------
# Stage 2: the id watermark carries progress across runs
# ----------------------------------------------------------------------------------------------------------------------

def _work_clock(monkeypatch):
    """A clock that advances one second per memory Stage 2 processes (and not otherwise): deterministic budgets."""
    state = {"t": 1000.0, "seen": []}
    real = temporal_edges.extract_edges_heuristic

    def ticking(conn, mid, *a, **k):
        state["t"] += 1.0
        state["seen"].append(mid)
        return real(conn, mid, *a, **k)

    monkeypatch.setattr(temporal_edges, "extract_edges_heuristic", ticking)
    monkeypatch.setattr(sleep_loop, "_clock", lambda: state["t"], raising=False)
    return state


def test_a_run_cut_at_its_budget_keeps_its_progress_and_the_next_resumes(tmp_path, monkeypatch):
    db = make_brain(tmp_path / "b.db")
    rng = random.Random(5)
    conn = sqlite3.connect(db)
    base = _vec(rng, 16)
    ids = [add_memory(conn, "p", _vec(rng, 16, base=base, noise=0.2)) for _ in range(60)]
    conn.commit()
    conn.close()
    state = _work_clock(monkeypatch)

    r1 = sleep_loop.run_sleep_cycle(db, config=dict(QUIET, budget_seconds=40, stage2_reserve_seconds=10,
                                                    edge_batch_size=7))
    assert r1["status"] == "partial", (r1["status"], r1.get("error_log"))
    first = list(state["seen"])
    assert 0 < len(first) < 60, len(first)
    wm1 = watermark(db)
    assert wm1 == first[-1], (wm1, first[-1])
    row1 = runs(db)[-1]
    assert row1["status"] == "partial" and row1["completed_at"], row1

    r2 = sleep_loop.run_sleep_cycle(db, config=dict(QUIET, budget_seconds=0, edge_batch_size=7))
    assert r2["status"] == "success", (r2["status"], r2.get("error_log"))
    second = state["seen"][len(first):]
    assert first + second == ids, "a memory was processed twice, or one was skipped"
    assert watermark(db) == ids[-1]
    assert [r["status"] for r in runs(db)] == ["partial", "success"]


def test_bootstrap_reads_the_legacy_local_completed_at_as_local(tmp_path, monkeypatch):
    """A brain upgraded from f275d09 has no watermark; its last success row says completed_at in LOCAL time with a 'T'.
    Memories created (UTC) after that instant are Stage 2's input; earlier ones are not. On f275d09 the string compare
    of 'YYYY-MM-DD HH..' (UTC) against 'YYYY-MM-DDTHH..' (local) dropped them (a ' ' sorts before 'T')."""
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        db = make_brain(tmp_path / "b.db")
        conn = sqlite3.connect(db)
        now_utc = datetime.now(timezone.utc)
        done_local = (now_utc - timedelta(hours=2)).astimezone().replace(tzinfo=None).isoformat()
        conn.execute("INSERT INTO sleep_loop_runs (started_at, completed_at, status) VALUES (?, ?, 'success')",
                     (done_local, done_local))
        before = add_memory(conn, "p", None, created_at=(now_utc - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"))
        after = add_memory(conn, "p", None, created_at=(now_utc - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"))
        conn.commit()
        seen = []
        real = temporal_edges.extract_edges_heuristic
        monkeypatch.setattr(temporal_edges, "extract_edges_heuristic",
                            lambda c, mid, *a, **k: (seen.append(mid), real(c, mid, *a, **k))[1])
        res = sleep_loop.extract_new_edges(conn, dry_run=True)
        conn.close()
        assert after in seen and before not in seen, (seen, before, after, res)
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


# ----------------------------------------------------------------------------------------------------------------------
# the run row always closes
# ----------------------------------------------------------------------------------------------------------------------

def test_run_rows_are_utc_in_the_brains_own_format(tmp_path):
    db = make_brain(tmp_path / "b.db")
    res = sleep_loop.run_sleep_cycle(db, config=dict(QUIET))
    assert res["status"] == "success", res.get("error_log")
    row = runs(db)[-1]
    for col in ("started_at", "completed_at"):
        assert UTC_FMT.match(row[col] or ""), (col, row[col])
    c = sqlite3.connect(db)
    now = c.execute("SELECT datetime('now')").fetchone()[0]
    c.close()
    skew = abs((datetime.fromisoformat(now) - datetime.fromisoformat(row["completed_at"])).total_seconds())
    assert skew < 120, (row["completed_at"], now)


def test_an_orphaned_running_row_is_closed_by_the_next_run(tmp_path):
    db = make_brain(tmp_path / "b.db")
    c = sqlite3.connect(db)
    c.execute("INSERT INTO sleep_loop_runs (started_at, status) VALUES ('2026-10-13T22:30:24.807375', 'running')")
    c.commit()
    c.close()
    sleep_loop.run_sleep_cycle(db, config=dict(QUIET))
    rows = runs(db)
    assert [r["status"] for r in rows] == ["timeout", "success"], [(r["id"], r["status"]) for r in rows]
    assert "orphaned" in (rows[0]["error_log"] or ""), rows[0]["error_log"]
    assert rows[0]["completed_at"]


def test_an_interrupt_in_a_later_stage_still_closes_the_row(tmp_path, monkeypatch):
    """Ctrl-C (or any BaseException a stage lets escape) used to leave the row 'running' forever."""
    db = make_brain(tmp_path / "b.db")

    def boom(*a, **k):
        raise KeyboardInterrupt()

    monkeypatch.setattr(sleep_loop, "decay_and_prune_edges", boom)
    try:
        res = sleep_loop.run_sleep_cycle(db, config=dict(QUIET))
    except KeyboardInterrupt:
        res = None
    rows = runs(db)
    assert rows and rows[-1]["status"] == "timeout", [(r["status"], r.get("stage_reached")) for r in rows]
    assert rows[-1].get("stage_reached") == "decay", rows[-1].get("stage_reached")
    assert res is not None and res["status"] == "timeout"


_CHILD = r'''
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
import sleep_loop, temporal_edges
db, ready = sys.argv[2], sys.argv[3]
real = temporal_edges.extract_edges_heuristic
def slow(conn, mid, *a, **k):
    if not os.path.exists(ready):
        open(ready, "w").close()
    time.sleep(0.05)
    return real(conn, mid, *a, **k)
temporal_edges.extract_edges_heuristic = slow
sleep_loop._vl_mw = None
cfg = {"quickening_enabled": False, "communities_enabled": False, "min_unreflected_for_reflection": 10 ** 9,
       "budget_seconds": 0, "edge_batch_size": 5}
res = sleep_loop.run_sleep_cycle(db, config=cfg)
print(json.dumps({"status": res.get("status"), "stage_reached": res.get("stage_reached")}))
'''


def test_sigterm_mid_stage2_closes_the_row_timeout_and_keeps_committed_batches(tmp_path):
    """systemd's TimeoutStartSec sends SIGTERM. On f275d09 the process died and the row stayed 'running' (rows
    116-123). Now the row closes 'timeout' with stage_reached 'edges', and every committed batch's watermark stays."""
    db = make_brain(tmp_path / "b.db")
    conn = sqlite3.connect(db)
    rng = random.Random(9)
    for _ in range(400):
        add_memory(conn, "p", _vec(rng, 8))
    conn.commit()
    conn.close()
    ready = tmp_path / "ready"
    env = {k: v for k, v in os.environ.items() if not k.startswith("WIZARD_")}
    env.update(WIZARD_LIBRARY_DB=db, HOME=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.Popen([sys.executable, "-B", "-c", _CHILD, str(ORGANS), db, str(ready)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=str(tmp_path))
    t0 = time.monotonic()
    while not ready.exists() and p.poll() is None and time.monotonic() - t0 < 60:
        time.sleep(0.05)
    assert ready.exists(), p.communicate(timeout=30)
    time.sleep(1.0)                                   # ~20 memories in: several batches of 5 committed
    p.send_signal(signal.SIGTERM)
    out, err = p.communicate(timeout=60)
    rows = runs(db)
    assert rows[-1]["status"] == "timeout", (rows[-1]["status"], out[-400:], err[-800:])
    assert rows[-1]["stage_reached"] == "edges", rows[-1]
    assert rows[-1]["completed_at"] and "stopped during stage edges" in (rows[-1]["error_log"] or "")
    wm = watermark(db)
    assert wm is not None and wm >= 5, wm


def test_without_migration_040_a_timeout_still_closes_the_row(tmp_path, monkeypatch):
    """A brain the installer has not migrated yet: 'timeout' is not in its CHECK, so the row closes 'partial' with the
    timeout named (never left 'running', never a CHECK failure that leaves it open)."""
    db = make_brain(tmp_path / "b.db", with_040=False)

    def boom(*a, **k):
        raise KeyboardInterrupt()

    monkeypatch.setattr(sleep_loop, "decay_and_prune_edges", boom)
    try:
        sleep_loop.run_sleep_cycle(db, config=dict(QUIET))
    except KeyboardInterrupt:
        pass
    row = runs(db)[-1]
    assert row["status"] == "partial" and "timeout" in (row["error_log"] or ""), row


def test_a_dry_run_writes_nothing(tmp_path):
    """GUARD: no row, no watermark, no edge."""
    db = make_brain(tmp_path / "b.db")
    conn = sqlite3.connect(db)
    rng = random.Random(1)
    base = _vec(rng, 8)
    for _ in range(30):
        add_memory(conn, "p", _vec(rng, 8, base=base, noise=0.1))
    conn.commit()
    conn.close()
    res = sleep_loop.run_sleep_cycle(db, config=dict(QUIET), dry_run=True)
    assert res["status"] in ("success", "partial"), res
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM sleep_loop_runs").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM memory_edges").fetchone()[0] == 0
    c.close()
    assert watermark(db) is None


# ----------------------------------------------------------------------------------------------------------------------
# Stage 1
# ----------------------------------------------------------------------------------------------------------------------

def _episodics(conn, rng, n, dim, groups=12):
    centers = [_vec(rng, dim) for _ in range(groups)]
    t = datetime.now(timezone.utc) - timedelta(days=1)
    for i in range(n):
        add_memory(conn, "p", _vec(rng, dim, base=centers[i % groups], noise=0.08), kind="episodic",
                   created_at=(t + timedelta(seconds=i)).strftime("%Y-%m-%d %H:%M:%S"))


def test_stage1_is_vectorised(tmp_path):
    """700 episodics x 384 dims (245k cosines): f275d09's pure-Python double loop needs ~14 s for them [4.6 s measured
    at 400 on this host, n^2]; the night budget cannot afford n^2 Python at the friend pack's 1,532."""
    pytest.importorskip("numpy")
    db = make_brain(tmp_path / "b.db")
    conn = sqlite3.connect(db)
    _episodics(conn, random.Random(2), 700, 384)
    conn.commit()
    t = time.monotonic()
    out = sleep_loop.consolidate_episodics(conn, lookback_days=7, dry_run=True)
    dt = time.monotonic() - t
    conn.close()
    assert out, "the fixture should cluster"
    assert dt < 3.0, "Stage 1 took %.1fs for 700 episodics" % dt


def test_stage1_numpy_and_pure_python_cluster_identically(tmp_path, monkeypatch):
    """GUARD: the vectorised Stage 1 finds exactly the clusters the pure loop finds (same members, same order)."""
    pytest.importorskip("numpy")
    db = make_brain(tmp_path / "b.db")
    conn = sqlite3.connect(db)
    _episodics(conn, random.Random(4), 150, 48, groups=9)
    conn.commit()
    fast = sleep_loop.consolidate_episodics(conn, lookback_days=7, dry_run=True)
    monkeypatch.setattr(sleep_loop, "_np", None, raising=False)
    slow = sleep_loop.consolidate_episodics(conn, lookback_days=7, dry_run=True)
    conn.close()
    assert [c["source_ids"] for c in fast] == [c["source_ids"] for c in slow] and fast


def test_stage1_window_is_utc(tmp_path, monkeypatch):
    """Three near-identical episodics created (UTC) 10 minutes inside the 7-day window must cluster. f275d09 built its
    cutoff as a LOCAL isoformat ('T'): on the same date a 'T' sorts after the memory's ' ', so they fell outside."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        now = datetime.now(timezone.utc)
        created = now - timedelta(days=7) + timedelta(minutes=10)
        if created.date() != (now - timedelta(days=7)).date():
            pytest.skip("the 10-minute margin crosses midnight UTC right now")
        db = make_brain(tmp_path / "b.db")
        conn = sqlite3.connect(db)
        rng = random.Random(8)
        base = _vec(rng, 16)
        for _ in range(3):
            add_memory(conn, "p", _vec(rng, 16, base=base, noise=0.01), kind="episodic",
                       created_at=created.strftime("%Y-%m-%d %H:%M:%S"))
        conn.commit()
        out = sleep_loop.consolidate_episodics(conn, lookback_days=7, dry_run=True)
        conn.close()
        assert len(out) == 1, out
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


# ----------------------------------------------------------------------------------------------------------------------
# the deadline is in the process, packaged, and below the unit's wall
# ----------------------------------------------------------------------------------------------------------------------

def test_the_packaged_budget_sits_inside_the_units_outer_wall():
    cfg = json.loads((ROOT / "cron" / "sleep-loop-config.json").read_text())
    roster = json.loads((ROOT / "install" / "cron_roster.json").read_text())["units"]["sleep-loop"]
    unit = (ROOT / "cron" / "wizard-library-sleep-loop.service").read_text()
    wall = int(re.search(r"^TimeoutStartSec=(\d+)", unit, re.M).group(1))
    assert "budget_seconds" in cfg, "no in-process budget is packaged: on macOS (launchd) nothing ends a stalled run"
    b = cfg["budget_seconds"]
    assert 0 < b <= wall - 30 and b <= roster["timeout"] - 30, (b, wall, roster["timeout"])
    assert 0 < cfg["stage2_reserve_seconds"] < b / 2, cfg["stage2_reserve_seconds"]


def test_catch_up_flag_runs_without_a_budget_and_shows_progress(tmp_path):
    """The owner's one catch-up run (decision D4): `sleep_loop.py --run <db> --catch-up` -- no time limit, a progress
    line on stderr, the row closed."""
    db = make_brain(tmp_path / "b.db")
    conn = sqlite3.connect(db)
    rng = random.Random(6)
    for _ in range(25):
        add_memory(conn, "p", _vec(rng, 8))
    conn.commit()
    conn.close()
    cfgp = tmp_path / "cfg.json"
    cfgp.write_text(json.dumps(dict(QUIET, budget_seconds=1)))       # the catch-up flag must override this
    env = {k: v for k, v in os.environ.items() if not k.startswith("WIZARD_")}
    env.update(WIZARD_LIBRARY_DB=db, HOME=str(tmp_path), PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1")
    p = subprocess.run([sys.executable, "-B", str(ORGANS / "sleep_loop.py"), "--run", db, "--config", str(cfgp),
                        "--catch-up"], capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=300)
    assert p.returncode == 0, (p.returncode, p.stdout[-500:], p.stderr[-1500:])
    assert "catch-up run" in p.stderr and re.search(r"edges \d+/\d+", p.stderr), p.stderr[-1500:]
    out = json.loads(p.stdout)
    assert out["status"] == "success" and out["config"]["budget_seconds"] == 0, out["status"]
    assert runs(db)[-1]["status"] == "success"


# ----------------------------------------------------------------------------------------------------------------------
# migration 040
# ----------------------------------------------------------------------------------------------------------------------

def _mig040():
    files = sorted(MIGRATIONS.glob("migration_040_*.sql"))
    assert files, "migration 040 (sleep run lifecycle) is not in this tree"
    return files[0]


def _legacy_brain(path):
    conn = sqlite3.connect(str(path))
    conn.executescript((MIGRATIONS / "schema.sql").read_text())
    conn.executescript((MIGRATIONS / "migration_005_sleep_loop.sql").read_text())
    for i in range(5):
        conn.execute("INSERT INTO sleep_loop_runs (started_at, status) VALUES (?, ?)",
                     ("2026-10-0%dT22:30:00" % (i + 1), "running" if i % 2 else "success"))
    conn.execute("DELETE FROM sleep_loop_runs WHERE id = 5")          # the AUTOINCREMENT mark (5) must survive
    conn.commit()
    conn.close()


def test_migration_040_widens_the_status_and_keeps_every_row(tmp_path):
    import schema_migrator
    db = tmp_path / "legacy.db"
    _legacy_brain(db)
    conn = sqlite3.connect(str(db))
    r = schema_migrator._run_file(conn, _mig040())
    assert not r["errors"], r["errors"]
    conn.commit()
    cols = {x[1] for x in conn.execute("PRAGMA table_info(sleep_loop_runs)")}
    assert {"stage_reached", "stage_log"} <= cols, cols
    assert conn.execute("SELECT COUNT(*) FROM sleep_loop_runs").fetchone()[0] == 4
    assert conn.execute("SELECT seq FROM sqlite_sequence WHERE name='sleep_loop_runs'").fetchone()[0] == 5
    conn.execute("INSERT INTO sleep_loop_runs (status, stage_reached) VALUES ('timeout', 'edges')")
    assert conn.execute("SELECT MAX(id) FROM sleep_loop_runs").fetchone()[0] == 6
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO sleep_loop_runs (status) VALUES ('bogus')")
    conn.close()


def test_migration_040_refuses_a_table_it_does_not_know(tmp_path):
    """A sleep_loop_runs with a column 040 would not copy is refused whole: nothing is truncated."""
    import schema_migrator
    db = tmp_path / "odd.db"
    _legacy_brain(db)
    conn = sqlite3.connect(str(db))
    conn.execute("ALTER TABLE sleep_loop_runs ADD COLUMN someone_elses TEXT")
    conn.commit()
    r = schema_migrator._run_file(conn, _mig040())
    assert r["errors"] and "fifteen_column" in json.dumps(r["errors"]), r
    cols = {x[1] for x in conn.execute("PRAGMA table_info(sleep_loop_runs)")}
    assert "someone_elses" in cols and "stage_reached" not in cols
    assert conn.execute("SELECT COUNT(*) FROM sleep_loop_runs").fetchone()[0] == 4
    conn.close()


def test_migration_040_is_applied_once_by_the_installers_migrator(tmp_path):
    """The installer's step (schema_migrator.apply) runs 040 on a brain that lacks it and skips it on the next run."""
    import schema_migrator
    db = tmp_path / "brain.db"
    _legacy_brain(db)
    r1 = schema_migrator.apply(str(db), MIGRATIONS)
    name = _mig040().name
    assert name not in r1["skipped_already_applied"], "040 was not applied"
    assert not [f for f in r1["files_with_errors"] if f["file"] == name], r1["files_with_errors"]
    r2 = schema_migrator.apply(str(db), MIGRATIONS)
    assert name in r2["skipped_already_applied"], r2["skipped_already_applied"]


def test_exit_status_names_the_outcome():
    f = getattr(sleep_loop, "exit_code_for", None)
    assert f is not None, "no exit mapping for partial/timeout"
    assert f({"status": "success"}) == 0
    assert f({"status": "partial", "error_log": None}) == 0          # a budget slice with the backlog carried
    assert f({"status": "partial", "error_log": "Stage 3 (decay): x"}) == 1
    assert f({"status": "timeout"}) == 1
    assert f({"status": "skipped"}) == 75
