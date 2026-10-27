# -*- coding: utf-8 -*-
"""2026-10-26 diagnostics: the weekly cognitive map timed out.

Measured on the owner's brain (read-only): `get_edge_graph` ranked memories by
degree with a per-memory `(source_id = m.id OR target_id = m.id)` subquery that
could use neither index, so it scanned every live edge once per memory -- 143 s
of a job whose limit is 120 s, growing every week.

The cost test counts SQLite VM steps (a progress handler), not seconds, so it is
deterministic: doubling the brain must roughly double the work. The quadratic
query quadruples it.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "organs") not in sys.path:
    sys.path.insert(0, str(ROOT / "organs"))

import cognitive_map  # noqa: E402


def _brain(n: int, descending: bool = False) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE memories (id INTEGER PRIMARY KEY, summary TEXT, kind TEXT,
                               is_archived INTEGER DEFAULT 0);
        CREATE TABLE memory_edges (id INTEGER PRIMARY KEY, source_id INTEGER,
                                   target_id INTEGER, predicate TEXT, valid_until TEXT);
        CREATE INDEX idx_edges_source ON memory_edges(source_id);
        CREATE INDEX idx_edges_target ON memory_edges(target_id);
        CREATE INDEX idx_edges_valid ON memory_edges(valid_until) WHERE valid_until IS NULL;
    """)
    order = range(n, 0, -1) if descending else range(1, n + 1)
    c.executemany("INSERT INTO memories (id, summary, kind) VALUES (?, ?, 'episodic')",
                  [(i, f"m{i}") for i in order])
    edges = []
    for i in range(1, n + 1):
        edges.append((i, (i % n) + 1, "references", None))
        edges.append((i, ((i * 7) % n) + 1, "relates_to", None))
    edges.append((1, 2, "contradicts", "2026-01-01"))          # retired: never counted
    c.executemany("INSERT INTO memory_edges (source_id, target_id, predicate, valid_until) "
                  "VALUES (?, ?, ?, ?)", edges)
    c.commit()
    c.row_factory = sqlite3.Row
    return c


def _steps(conn: sqlite3.Connection) -> int:
    count = [0]

    def tick():
        count[0] += 1
        return 0
    conn.set_progress_handler(tick, 100)
    cognitive_map.get_edge_graph(conn)
    conn.set_progress_handler(None, 0)
    return count[0]


def _naive_degree(conn: sqlite3.Connection) -> dict:
    deg = {r[0]: 0 for r in conn.execute("SELECT id FROM memories")}
    for s, t in conn.execute("SELECT source_id, target_id FROM memory_edges WHERE valid_until IS NULL"):
        deg[s] += 1
        deg[t] += 1
    return deg


def test_degree_matches_a_plain_count_and_ignores_retired_edges():
    c = _brain(300)
    nodes = cognitive_map.get_edge_graph(c)["nodes"]
    naive = _naive_degree(c)
    assert len(nodes) == 100
    for n in nodes:
        assert n["degree"] == naive[n["id"]]
    top = sorted(naive.values(), reverse=True)[:100]
    assert sorted((n["degree"] for n in nodes), reverse=True) == top


def test_the_tie_break_is_pinned_in_the_query():
    """SQLite's rowid scan already ascends, so no fixture can observe the tie-break today
    (review 2, N10); it is pinned structurally against a future plan change."""
    import inspect
    import re
    assert re.search(r"ORDER BY degree DESC,\s*m\.id", inspect.getsource(cognitive_map.get_edge_graph))


def test_ranking_is_deterministic_on_ties():
    c = _brain(300, descending=True)
    a = [n["id"] for n in cognitive_map.get_edge_graph(c)["nodes"]]
    b = [n["id"] for n in cognitive_map.get_edge_graph(c)["nodes"]]
    assert a == b
    # within one degree, ids ascend
    nodes = cognitive_map.get_edge_graph(c)["nodes"]
    for x, y in zip(nodes, nodes[1:]):
        if x["degree"] == y["degree"]:
            assert x["id"] < y["id"]


def test_doubling_the_brain_does_not_quadruple_the_work():
    small, big = _steps(_brain(1500)), _steps(_brain(3000))
    ratio = big / max(small, 1)
    # linear work doubles (~2); the old per-memory scan quadrupled (~4)
    assert ratio < 3.0, f"degree ranking scaled x{ratio:.2f} when the brain doubled"
