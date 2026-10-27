#!/usr/bin/env python3
"""Provenance Graph — Wizard Library L3 Component 2.

A stand-alone module that tracks HOW each derived memory was produced:
which source memories contributed, what transformation was applied, which
agent/process performed the work, and a prompt hash for reproducibility.

This module extends the existing ``consolidation_sources`` table (migration 005,
narrow to sleep-loop consolidation only) into a universal ``memory_provenance``
table (migration 010) that covers all derivation types: consolidation, reflection,
distillation, dream synthesis, council deliberation, manual derivation, and
edge inference.

Public API
----------
``is_provenance_migration_applied(conn) -> bool``
    Check whether the memory_provenance table exists.

``apply_provenance_migration(db_path, migration_sql_path) -> dict``
    Apply migration 010 idempotently.

``record_provenance(conn, derived_memory_id, source_memory_ids, transformation_type, ...) -> List[int]``
    Record provenance edges for a derived memory.

``get_provenance_chain(conn, memory_id, depth=5) -> List[dict]``
    Recursive traversal: walk the chain of ancestors back to leaf sources.

``get_derived_from(conn, memory_id) -> List[dict]``
    Direct parents (one level up): what sources produced this memory?

``get_derivatives_of(conn, memory_id) -> List[dict]``
    Direct children (one level down): what was derived FROM this memory?

``trace_full_lineage(conn, memory_id) -> dict``
    Both directions: ancestors (provenance chain upward) AND descendants.

``backfill_from_consolidation_sources(conn, dry_run=False) -> dict``
    Migrate existing consolidation_sources data INTO the provenance graph.

``get_provenance_stats(conn) -> dict``
    Aggregate statistics on the provenance graph.

CLI interface
-------------
::

    python3 provenance_graph.py --unit-tests                  # self-test
    python3 provenance_graph.py --backfill <db_path>          # migrate consolidation_sources
    python3 provenance_graph.py --backfill --dry-run <db_path>
    python3 provenance_graph.py --stats <db_path>

Phase 0 design: L3-C2 provenance-graph task specification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
import unittest
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

VALID_TRANSFORMATION_TYPES = (
    'consolidation', 'reflection', 'distillation', 'dream_synthesis',
    'council_deliberation', 'manual_derivation', 'edge_inference',
)

PROVENANCE_MIGRATION_SQL = Path(__file__).parent.parent / "migrations" / "migration_010_provenance_graph.sql"


# ============================================================
# Migration helpers
# ============================================================

def is_provenance_migration_applied(conn: sqlite3.Connection) -> bool:
    """Check whether the memory_provenance table exists."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    return "memory_provenance" in tables


def apply_provenance_migration(
    db_path: str,
    migration_sql_path: Optional[str] = None,
) -> dict:
    """Apply migration 010 idempotently.

    Args:
        db_path: path to the SQLite database.
        migration_sql_path: path to migration_010_provenance_graph.sql.
            Defaults to the file next to this module.

    Returns:
        {"status": "ok"} or {"status": "already-applied"} or {"status": "error", "message": ...}
    """
    if migration_sql_path is None:
        migration_sql_path = str(PROVENANCE_MIGRATION_SQL)

    sql_path = Path(migration_sql_path)
    if not sql_path.exists():
        return {"status": "error", "message": f"Migration SQL not found: {migration_sql_path}"}

    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")
    if is_provenance_migration_applied(conn):
        conn.close()
        return {"status": "already-applied"}

    try:
        migration_text = sql_path.read_text()
        conn.executescript(migration_text)
        conn.commit()
        conn.close()
        return {"status": "ok"}
    except Exception as e:
        conn.close()
        return {"status": "error", "message": str(e)}


# ============================================================
# Core API
# ============================================================

def compute_prompt_hash(prompt_text: str) -> str:
    """Compute a stable SHA-256 hash of the consolidation/derivation prompt.

    This is for reproducibility auditing: given the same prompt, the same
    hash is produced. We store only the first 16 hex chars (64 bits of
    collision resistance, sufficient for audit trails).
    """
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]


def record_provenance(
    conn: sqlite3.Connection,
    derived_memory_id: int,
    source_memory_ids: List[int],
    transformation_type: str,
    agent: Optional[str] = None,
    prompt_hash: Optional[str] = None,
    confidence: float = 0.8,
    metadata: Optional[dict] = None,
) -> List[int]:
    """Record provenance edges linking a derived memory to its source(s).

    Args:
        conn: sqlite3 connection (with foreign_keys=ON).
        derived_memory_id: the ID of the memory that was produced.
        source_memory_ids: list of source memory IDs that contributed.
        transformation_type: one of VALID_TRANSFORMATION_TYPES.
        agent: which persona/process performed the derivation (e.g., 'sleep_loop', 'curator').
        prompt_hash: hash of the prompt used (for reproducibility).
        confidence: confidence in the derivation (0.0-1.0).
        metadata: arbitrary JSON-serializable dict (cluster_threshold, similarity_scores, etc.).

    Returns:
        List of inserted provenance row IDs.

    Raises:
        ValueError: if transformation_type is invalid, confidence is out of range,
            or source_memory_ids is empty.
        sqlite3.IntegrityError: if derived_memory_id or a source_memory_id does not
            exist in the memories table, or if a duplicate provenance edge is recorded.
    """
    if transformation_type not in VALID_TRANSFORMATION_TYPES:
        raise ValueError(
            f"Invalid transformation_type '{transformation_type}'. "
            f"Valid: {VALID_TRANSFORMATION_TYPES}"
        )
    if not source_memory_ids:
        raise ValueError("source_memory_ids must not be empty")
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"confidence must be 0.0-1.0, got {confidence}")

    metadata_json = json.dumps(metadata) if metadata else None
    # MUST match this column's own DEFAULT (datetime('now')) -- SQLite UTC,
    # space-separated, second precision. The round-gamma repair wrote naive LOCAL
    # isoformat() here, putting 62 ISO-T local rows in a column whose other 205
    # rows are space-UTC. Three live consequences, all silent:
    #   * monthly_self_eval.py:611 string-range-compares this column, and the
    #     sleep loop fires at 22:30 LOCAL -- inside the exact window where local
    #     and UTC disagree about the DATE, so month-end counts land on the wrong
    #     side of the boundary, in the direction that flatters the month closed;
    #   * 'T' (0x54) > ' ' (0x20), so MAX(created_at) always prefers an ISO-T row
    #     regardless of true time;
    #   * two organs' timestamps read four hours apart while naming one instant.
    # One clock per column, asserted:
    #   SELECT COUNT(DISTINCT (created_at LIKE '%T%')) FROM <table>  -- must be 1
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    inserted_ids: List[int] = []
    for source_id in source_memory_ids:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO memory_provenance
               (derived_memory_id, source_memory_id, transformation_type,
                agent, prompt_hash, confidence, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (derived_memory_id, source_id, transformation_type,
             agent, prompt_hash, confidence, metadata_json, now_iso),
        )
        if cursor.lastrowid and cursor.rowcount > 0:
            inserted_ids.append(cursor.lastrowid)
    return inserted_ids


def get_derived_from(
    conn: sqlite3.Connection,
    memory_id: int,
) -> List[dict]:
    """Get the direct source memories (parents) of a derived memory.

    Returns a list of dicts with provenance metadata for each source edge.
    """
    rows = conn.execute(
        """SELECT p.id, p.source_memory_id, p.transformation_type, p.agent,
                  p.prompt_hash, p.confidence, p.metadata, p.created_at,
                  m.summary, m.kind, m.category
           FROM memory_provenance p
           LEFT JOIN memories m ON m.id = p.source_memory_id
           WHERE p.derived_memory_id = ?
           ORDER BY p.confidence DESC, p.created_at ASC""",
        (memory_id,),
    ).fetchall()

    results = []
    for r in rows:
        entry = {
            "provenance_id": r[0],
            "source_memory_id": r[1],
            "transformation_type": r[2],
            "agent": r[3],
            "prompt_hash": r[4],
            "confidence": r[5],
            "metadata": json.loads(r[6]) if r[6] else None,
            "created_at": r[7],
            "source_summary": r[8],
            "source_kind": r[9],
            "source_category": r[10],
        }
        results.append(entry)
    return results


def get_derivatives_of(
    conn: sqlite3.Connection,
    memory_id: int,
) -> List[dict]:
    """Get all memories derived FROM this memory (children).

    Returns a list of dicts with provenance metadata for each derivation edge.
    """
    rows = conn.execute(
        """SELECT p.id, p.derived_memory_id, p.transformation_type, p.agent,
                  p.prompt_hash, p.confidence, p.metadata, p.created_at,
                  m.summary, m.kind, m.category
           FROM memory_provenance p
           LEFT JOIN memories m ON m.id = p.derived_memory_id
           WHERE p.source_memory_id = ?
           ORDER BY p.created_at DESC""",
        (memory_id,),
    ).fetchall()

    results = []
    for r in rows:
        entry = {
            "provenance_id": r[0],
            "derived_memory_id": r[1],
            "transformation_type": r[2],
            "agent": r[3],
            "prompt_hash": r[4],
            "confidence": r[5],
            "metadata": json.loads(r[6]) if r[6] else None,
            "created_at": r[7],
            "derived_summary": r[8],
            "derived_kind": r[9],
            "derived_category": r[10],
        }
        results.append(entry)
    return results


def get_provenance_chain(
    conn: sqlite3.Connection,
    memory_id: int,
    depth: int = 5,
) -> List[dict]:
    """Recursive traversal: walk the ancestors back to leaf sources.

    If A was derived from B which was derived from C, this returns:
    [
        {"level": 0, "memory_id": A, ...},   # the query target itself
        {"level": 1, "memory_id": B, ...},   # direct parent of A
        {"level": 2, "memory_id": C, ...},   # parent of B (grandparent of A)
    ]

    Args:
        conn: sqlite3 connection.
        memory_id: the memory to trace back from.
        depth: maximum recursion depth (1-10). Default 5.

    Returns:
        List of dicts, each containing level, memory_id, summary, kind,
        transformation_type (how it was derived), and provenance metadata.
    """
    depth = max(1, min(depth, 10))
    chain: List[dict] = []
    visited: Set[int] = set()

    # Level 0: the query target itself.
    target_row = conn.execute(
        "SELECT id, summary, kind, category FROM memories WHERE id = ?",
        (memory_id,),
    ).fetchone()
    if not target_row:
        return []

    chain.append({
        "level": 0,
        "memory_id": target_row[0],
        "summary": target_row[1],
        "kind": target_row[2],
        "category": target_row[3],
        "transformation_type": None,
        "agent": None,
        "confidence": None,
        "provenance_edges": [],
    })
    visited.add(memory_id)

    # BFS traversal upward through provenance edges.
    queue: deque = deque()
    queue.append((memory_id, 1))

    while queue:
        current_id, current_level = queue.popleft()
        if current_level > depth:
            continue

        parents = get_derived_from(conn, current_id)
        for parent in parents:
            source_id = parent["source_memory_id"]
            if source_id in visited:
                continue
            visited.add(source_id)

            chain.append({
                "level": current_level,
                "memory_id": source_id,
                "summary": parent.get("source_summary"),
                "kind": parent.get("source_kind"),
                "category": parent.get("source_category"),
                "transformation_type": parent["transformation_type"],
                "agent": parent["agent"],
                "confidence": parent["confidence"],
                "provenance_edges": [parent],
            })

            if current_level < depth:
                queue.append((source_id, current_level + 1))

    return chain


def trace_full_lineage(
    conn: sqlite3.Connection,
    memory_id: int,
    ancestor_depth: int = 5,
    descendant_depth: int = 5,
) -> dict:
    """Trace both ancestors (provenance chain upward) AND descendants (what was derived FROM this memory).

    Returns a dict with 'ancestors' and 'descendants' lists plus summary stats.
    """
    ancestors = get_provenance_chain(conn, memory_id, depth=ancestor_depth)

    # Descendants: BFS downward through derivation edges.
    descendants: List[dict] = []
    visited: Set[int] = {memory_id}
    queue: deque = deque()
    queue.append((memory_id, 1))

    while queue:
        current_id, current_level = queue.popleft()
        if current_level > descendant_depth:
            continue

        children = get_derivatives_of(conn, current_id)
        for child in children:
            derived_id = child["derived_memory_id"]
            if derived_id in visited:
                continue
            visited.add(derived_id)

            descendants.append({
                "level": current_level,
                "memory_id": derived_id,
                "summary": child.get("derived_summary"),
                "kind": child.get("derived_kind"),
                "category": child.get("derived_category"),
                "transformation_type": child["transformation_type"],
                "agent": child["agent"],
                "confidence": child["confidence"],
            })

            if current_level < descendant_depth:
                queue.append((derived_id, current_level + 1))

    # Classify leaf sources vs intermediate nodes.
    ancestor_ids = {a["memory_id"] for a in ancestors if a["level"] > 0}
    leaf_sources = []
    for a in ancestors:
        if a["level"] > 0:
            a_parents = get_derived_from(conn, a["memory_id"])
            if not a_parents:
                leaf_sources.append(a["memory_id"])

    return {
        "memory_id": memory_id,
        "ancestors": ancestors,
        "ancestor_count": len(ancestors) - 1,  # exclude level-0 (self)
        "leaf_source_ids": leaf_sources,
        "descendants": descendants,
        "descendant_count": len(descendants),
        "total_lineage_size": len(ancestors) - 1 + len(descendants),
    }


# ============================================================
# Backfill from consolidation_sources
# ============================================================

def backfill_from_consolidation_sources(
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> dict:
    """Migrate existing consolidation_sources data INTO the provenance graph.

    The L2-C3 Sleep Loop already populated consolidation_sources with rows
    mapping (consolidated_memory_id -> source_memory_id, cluster_id). This
    function reads that table and creates corresponding memory_provenance
    rows with transformation_type='consolidation', agent='sleep_loop'.

    Idempotent: the UNIQUE index on (derived_memory_id, source_memory_id,
    transformation_type) prevents duplicates via INSERT OR IGNORE.

    Args:
        conn: sqlite3 connection.
        dry_run: if True, count eligible rows without inserting.

    Returns:
        {"rows_found", "rows_inserted", "rows_skipped", "clusters_found"}
    """
    if not is_provenance_migration_applied(conn):
        return {"status": "error", "message": "memory_provenance table does not exist"}

    # Check consolidation_sources exists.
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "consolidation_sources" not in tables:
        return {"status": "error", "message": "consolidation_sources table does not exist"}

    rows = conn.execute(
        """SELECT cs.consolidated_memory_id, cs.source_memory_id, cs.cluster_id, cs.created_at
           FROM consolidation_sources cs
           ORDER BY cs.consolidated_memory_id, cs.source_memory_id""",
    ).fetchall()

    clusters: Set[int] = set()
    inserted = 0
    skipped = 0

    for consolidated_id, source_id, cluster_id, created_at in rows:
        clusters.add(cluster_id)

        if dry_run:
            inserted += 1
            continue

        cursor = conn.execute(
            """INSERT OR IGNORE INTO memory_provenance
               (derived_memory_id, source_memory_id, transformation_type,
                agent, prompt_hash, confidence, metadata, created_at)
               VALUES (?, ?, 'consolidation', 'sleep_loop', NULL, 0.75,
                       ?, ?)""",
            (
                consolidated_id,
                source_id,
                json.dumps({"cluster_id": cluster_id, "backfilled": True}),
                created_at or datetime.now().isoformat(),
            ),
        )
        if cursor.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    if not dry_run:
        conn.commit()

    return {
        "status": "ok",
        "rows_found": len(rows),
        "rows_inserted": inserted,
        "rows_skipped": skipped,
        "clusters_found": len(clusters),
        "dry_run": dry_run,
    }


# ============================================================
# Statistics
# ============================================================

def get_provenance_stats(conn: sqlite3.Connection) -> dict:
    """Aggregate statistics on the provenance graph."""
    if not is_provenance_migration_applied(conn):
        return {"status": "error", "message": "memory_provenance table does not exist"}

    total_edges = conn.execute("SELECT COUNT(*) FROM memory_provenance").fetchone()[0]
    by_type = {}
    for row in conn.execute(
        "SELECT transformation_type, COUNT(*) FROM memory_provenance GROUP BY transformation_type"
    ).fetchall():
        by_type[row[0]] = row[1]

    by_agent = {}
    for row in conn.execute(
        "SELECT COALESCE(agent, 'unknown'), COUNT(*) FROM memory_provenance GROUP BY agent"
    ).fetchall():
        by_agent[row[0]] = row[1]

    derived_count = conn.execute(
        "SELECT COUNT(DISTINCT derived_memory_id) FROM memory_provenance"
    ).fetchone()[0]

    source_count = conn.execute(
        "SELECT COUNT(DISTINCT source_memory_id) FROM memory_provenance"
    ).fetchone()[0]

    avg_sources = conn.execute(
        """SELECT AVG(cnt) FROM (
               SELECT COUNT(*) as cnt FROM memory_provenance
               GROUP BY derived_memory_id
           )"""
    ).fetchone()[0] or 0.0

    max_sources = conn.execute(
        """SELECT MAX(cnt) FROM (
               SELECT COUNT(*) as cnt FROM memory_provenance
               GROUP BY derived_memory_id
           )"""
    ).fetchone()[0] or 0

    avg_confidence = conn.execute(
        "SELECT AVG(confidence) FROM memory_provenance"
    ).fetchone()[0] or 0.0

    # Max chain depth (approximate via recursive CTE, limited to 10).
    max_depth_row = conn.execute("""
        WITH RECURSIVE chain(memory_id, depth) AS (
            SELECT DISTINCT derived_memory_id, 0 FROM memory_provenance
            UNION ALL
            SELECT p.source_memory_id, c.depth + 1
            FROM chain c
            JOIN memory_provenance p ON p.derived_memory_id = c.memory_id
            WHERE c.depth < 10
        )
        SELECT MAX(depth) FROM chain
    """).fetchone()
    max_chain_depth = max_depth_row[0] if max_depth_row and max_depth_row[0] else 0

    return {
        "total_edges": total_edges,
        "by_transformation_type": by_type,
        "by_agent": by_agent,
        "derived_memories": derived_count,
        "source_memories": source_count,
        "avg_sources_per_derived": round(avg_sources, 2),
        "max_sources_per_derived": max_sources,
        "avg_confidence": round(avg_confidence, 3),
        "max_chain_depth": max_chain_depth,
    }


# ============================================================
# Unit Tests
# ============================================================

class TestProvenanceGraph(unittest.TestCase):
    """Self-contained unit tests using an in-memory database."""

    def setUp(self):
        """Create an in-memory database with the required schema."""
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                summary TEXT,
                category TEXT NOT NULL,
                importance REAL DEFAULT 0.5,
                source_project TEXT,
                source_context TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                last_accessed TEXT DEFAULT (datetime('now')),
                access_count INTEGER DEFAULT 0,
                is_archived INTEGER DEFAULT 0,
                version INTEGER DEFAULT 1,
                kind TEXT DEFAULT 'episodic',
                derived_from TEXT DEFAULT NULL,
                consolidated_at TEXT DEFAULT NULL,
                confidence REAL DEFAULT 1.0
            );
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS consolidation_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                consolidated_memory_id INTEGER NOT NULL REFERENCES memories(id),
                source_memory_id INTEGER NOT NULL REFERENCES memories(id),
                cluster_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        # Apply provenance migration inline (since we can't read the SQL file in tests).
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory_provenance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                derived_memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                source_memory_id INTEGER NOT NULL REFERENCES memories(id),
                transformation_type TEXT NOT NULL CHECK(transformation_type IN (
                    'consolidation', 'reflection', 'distillation', 'dream_synthesis',
                    'council_deliberation', 'manual_derivation', 'edge_inference'
                )),
                agent TEXT DEFAULT NULL,
                prompt_hash TEXT DEFAULT NULL,
                confidence REAL DEFAULT 0.8,
                metadata TEXT DEFAULT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_prov_unique_edge
                ON memory_provenance(derived_memory_id, source_memory_id, transformation_type);
        """)
        self.conn.commit()

        # Seed test memories.
        for i in range(1, 8):
            kind = "episodic" if i <= 5 else "semantic"
            self.conn.execute(
                """INSERT INTO memories (id, content, summary, category, kind, confidence)
                   VALUES (?, ?, ?, 'domain_knowledge', ?, 1.0)""",
                (i, f"Memory content {i}", f"Summary {i}", kind),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_is_migration_applied(self):
        self.assertTrue(is_provenance_migration_applied(self.conn))

    def test_record_provenance_basic(self):
        ids = record_provenance(
            self.conn,
            derived_memory_id=6,
            source_memory_ids=[1, 2, 3],
            transformation_type="consolidation",
            agent="sleep_loop",
            confidence=0.75,
        )
        self.conn.commit()
        self.assertEqual(len(ids), 3)

        # Verify rows.
        rows = self.conn.execute(
            "SELECT * FROM memory_provenance WHERE derived_memory_id = 6"
        ).fetchall()
        self.assertEqual(len(rows), 3)

    def test_record_provenance_with_metadata(self):
        meta = {"cluster_threshold": 0.7, "similarity_scores": [0.85, 0.91]}
        ids = record_provenance(
            self.conn,
            derived_memory_id=6,
            source_memory_ids=[1],
            transformation_type="reflection",
            agent="curator",
            prompt_hash="abc123",
            confidence=0.9,
            metadata=meta,
        )
        self.conn.commit()
        self.assertEqual(len(ids), 1)

        row = self.conn.execute(
            "SELECT metadata, agent, prompt_hash, confidence FROM memory_provenance WHERE id = ?",
            (ids[0],),
        ).fetchone()
        self.assertEqual(json.loads(row[0]), meta)
        self.assertEqual(row[1], "curator")
        self.assertEqual(row[2], "abc123")
        self.assertAlmostEqual(row[3], 0.9)

    def test_record_provenance_idempotent(self):
        record_provenance(self.conn, 6, [1, 2], "consolidation", agent="sleep_loop")
        self.conn.commit()
        # Second call: same edges should be ignored (OR IGNORE).
        ids2 = record_provenance(self.conn, 6, [1, 2], "consolidation", agent="sleep_loop")
        self.conn.commit()
        self.assertEqual(len(ids2), 0)  # nothing new inserted

        total = self.conn.execute(
            "SELECT COUNT(*) FROM memory_provenance WHERE derived_memory_id = 6"
        ).fetchone()[0]
        self.assertEqual(total, 2)

    def test_record_provenance_invalid_type(self):
        with self.assertRaises(ValueError):
            record_provenance(self.conn, 6, [1], "invalid_type")

    def test_record_provenance_empty_sources(self):
        with self.assertRaises(ValueError):
            record_provenance(self.conn, 6, [], "consolidation")

    def test_record_provenance_invalid_confidence(self):
        with self.assertRaises(ValueError):
            record_provenance(self.conn, 6, [1], "consolidation", confidence=1.5)

    def test_get_derived_from(self):
        record_provenance(self.conn, 6, [1, 2, 3], "consolidation", agent="sleep_loop", confidence=0.75)
        self.conn.commit()
        parents = get_derived_from(self.conn, 6)
        self.assertEqual(len(parents), 3)
        source_ids = {p["source_memory_id"] for p in parents}
        self.assertEqual(source_ids, {1, 2, 3})
        for p in parents:
            self.assertEqual(p["transformation_type"], "consolidation")
            self.assertEqual(p["agent"], "sleep_loop")

    def test_get_derivatives_of(self):
        record_provenance(self.conn, 6, [1], "consolidation", agent="sleep_loop")
        record_provenance(self.conn, 7, [1], "reflection", agent="curator")
        self.conn.commit()
        children = get_derivatives_of(self.conn, 1)
        self.assertEqual(len(children), 2)
        derived_ids = {c["derived_memory_id"] for c in children}
        self.assertEqual(derived_ids, {6, 7})

    def test_get_provenance_chain_simple(self):
        # Chain: 7 <- 6 <- [1, 2]
        record_provenance(self.conn, 6, [1, 2], "consolidation", agent="sleep_loop")
        record_provenance(self.conn, 7, [6], "reflection", agent="curator")
        self.conn.commit()

        chain = get_provenance_chain(self.conn, 7, depth=5)
        self.assertGreaterEqual(len(chain), 3)  # 7 (level 0), 6 (level 1), 1 and 2 (level 2)

        # Level 0 is the query target.
        self.assertEqual(chain[0]["memory_id"], 7)
        self.assertEqual(chain[0]["level"], 0)

        # Level 1 should be memory 6.
        level_1 = [c for c in chain if c["level"] == 1]
        self.assertEqual(len(level_1), 1)
        self.assertEqual(level_1[0]["memory_id"], 6)
        self.assertEqual(level_1[0]["transformation_type"], "reflection")

        # Level 2 should be memories 1 and 2.
        level_2 = [c for c in chain if c["level"] == 2]
        self.assertEqual(len(level_2), 2)
        level_2_ids = {c["memory_id"] for c in level_2}
        self.assertEqual(level_2_ids, {1, 2})

    def test_get_provenance_chain_depth_limit(self):
        # Chain: 7 <- 6 <- [1, 2], with depth=1 should only return 7 and 6.
        record_provenance(self.conn, 6, [1, 2], "consolidation", agent="sleep_loop")
        record_provenance(self.conn, 7, [6], "reflection", agent="curator")
        self.conn.commit()

        chain = get_provenance_chain(self.conn, 7, depth=1)
        levels = {c["level"] for c in chain}
        self.assertIn(0, levels)
        self.assertIn(1, levels)
        self.assertNotIn(2, levels)

    def test_get_provenance_chain_no_parents(self):
        chain = get_provenance_chain(self.conn, 1, depth=5)
        self.assertEqual(len(chain), 1)  # only the target itself
        self.assertEqual(chain[0]["memory_id"], 1)

    def test_trace_full_lineage(self):
        # 7 <- 6 <- [1, 2, 3]
        record_provenance(self.conn, 6, [1, 2, 3], "consolidation", agent="sleep_loop")
        record_provenance(self.conn, 7, [6], "distillation", agent="ingester")
        self.conn.commit()

        lineage = trace_full_lineage(self.conn, 6)
        self.assertEqual(lineage["memory_id"], 6)

        # Ancestors: 6 has parents [1, 2, 3].
        self.assertEqual(lineage["ancestor_count"], 3)

        # Descendants: 6 has child [7].
        self.assertEqual(lineage["descendant_count"], 1)
        self.assertEqual(lineage["descendants"][0]["memory_id"], 7)

    def test_backfill_from_consolidation_sources(self):
        # Seed consolidation_sources.
        self.conn.execute(
            "INSERT INTO consolidation_sources (consolidated_memory_id, source_memory_id, cluster_id) VALUES (6, 1, 100)"
        )
        self.conn.execute(
            "INSERT INTO consolidation_sources (consolidated_memory_id, source_memory_id, cluster_id) VALUES (6, 2, 100)"
        )
        self.conn.execute(
            "INSERT INTO consolidation_sources (consolidated_memory_id, source_memory_id, cluster_id) VALUES (6, 3, 100)"
        )
        self.conn.commit()

        # Dry run.
        result = backfill_from_consolidation_sources(self.conn, dry_run=True)
        self.assertEqual(result["rows_found"], 3)
        self.assertEqual(result["rows_inserted"], 3)
        self.assertTrue(result["dry_run"])
        # Nothing actually inserted.
        count = self.conn.execute("SELECT COUNT(*) FROM memory_provenance").fetchone()[0]
        self.assertEqual(count, 0)

        # Real run.
        result = backfill_from_consolidation_sources(self.conn, dry_run=False)
        self.assertEqual(result["rows_found"], 3)
        self.assertEqual(result["rows_inserted"], 3)
        self.assertEqual(result["clusters_found"], 1)

        # Idempotent: second run should skip all.
        result2 = backfill_from_consolidation_sources(self.conn, dry_run=False)
        self.assertEqual(result2["rows_inserted"], 0)
        self.assertEqual(result2["rows_skipped"], 3)

    def test_backfill_metadata_contains_cluster_id(self):
        self.conn.execute(
            "INSERT INTO consolidation_sources (consolidated_memory_id, source_memory_id, cluster_id) VALUES (6, 1, 42)"
        )
        self.conn.commit()
        backfill_from_consolidation_sources(self.conn, dry_run=False)

        row = self.conn.execute(
            "SELECT metadata FROM memory_provenance WHERE derived_memory_id = 6 AND source_memory_id = 1"
        ).fetchone()
        meta = json.loads(row[0])
        self.assertEqual(meta["cluster_id"], 42)
        self.assertTrue(meta["backfilled"])

    def test_get_provenance_stats(self):
        record_provenance(self.conn, 6, [1, 2, 3], "consolidation", agent="sleep_loop", confidence=0.75)
        record_provenance(self.conn, 7, [4, 5], "reflection", agent="curator", confidence=0.9)
        self.conn.commit()

        stats = get_provenance_stats(self.conn)
        self.assertEqual(stats["total_edges"], 5)
        self.assertEqual(stats["by_transformation_type"]["consolidation"], 3)
        self.assertEqual(stats["by_transformation_type"]["reflection"], 2)
        self.assertEqual(stats["by_agent"]["sleep_loop"], 3)
        self.assertEqual(stats["by_agent"]["curator"], 2)
        self.assertEqual(stats["derived_memories"], 2)
        self.assertEqual(stats["source_memories"], 5)

    def test_compute_prompt_hash_deterministic(self):
        h1 = compute_prompt_hash("Distill these 5 memories")
        h2 = compute_prompt_hash("Distill these 5 memories")
        h3 = compute_prompt_hash("Distill these 6 memories")
        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, h3)
        self.assertEqual(len(h1), 16)

    def test_same_source_different_types_allowed(self):
        record_provenance(self.conn, 6, [1], "consolidation", agent="sleep_loop")
        record_provenance(self.conn, 6, [1], "reflection", agent="curator")
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT * FROM memory_provenance WHERE derived_memory_id = 6 AND source_memory_id = 1"
        ).fetchall()
        self.assertEqual(len(rows), 2)

    def test_chain_cycle_protection(self):
        # Create a cycle: 6 <- 7, 7 <- 6. The BFS should not infinite-loop.
        record_provenance(self.conn, 6, [7], "reflection", agent="test")
        record_provenance(self.conn, 7, [6], "reflection", agent="test")
        self.conn.commit()

        chain = get_provenance_chain(self.conn, 6, depth=5)
        # Should terminate: visit 6, then 7, then try 6 again but it's visited.
        self.assertLessEqual(len(chain), 3)

    def test_trace_full_lineage_leaf_detection(self):
        # 7 <- 6 <- 1 (1 has no parents, so it's a leaf source).
        record_provenance(self.conn, 6, [1], "consolidation", agent="sleep_loop")
        record_provenance(self.conn, 7, [6], "reflection", agent="curator")
        self.conn.commit()

        lineage = trace_full_lineage(self.conn, 7)
        self.assertIn(1, lineage["leaf_source_ids"])

    def test_multiple_derivation_types_from_same_memory(self):
        record_provenance(self.conn, 6, [1, 2], "consolidation", agent="sleep_loop")
        record_provenance(self.conn, 7, [1, 3], "distillation", agent="ingester")
        self.conn.commit()

        # Memory 1 is a source for both 6 and 7.
        children = get_derivatives_of(self.conn, 1)
        self.assertEqual(len(children), 2)
        types = {c["transformation_type"] for c in children}
        self.assertEqual(types, {"consolidation", "distillation"})


# ============================================================
# CLI
# ============================================================

def _cli():
    parser = argparse.ArgumentParser(description="Provenance Graph — L3 Component 2")
    parser.add_argument("--unit-tests", action="store_true", help="Run self-tests")
    parser.add_argument("--backfill", action="store_true",
                        help="Backfill from consolidation_sources into memory_provenance")
    parser.add_argument("--dry-run", action="store_true",
                        help="Dry-run mode (use with --backfill)")
    parser.add_argument("--stats", action="store_true",
                        help="Show provenance graph statistics")
    parser.add_argument("db_path", nargs="?", default=None,
                        help="Path to SQLite database")

    args = parser.parse_args()

    if args.unit_tests:
        sys.argv = sys.argv[:1]
        unittest.main(module=__name__, exit=True, verbosity=2)
        return

    if not args.db_path:
        parser.error("db_path is required for --backfill and --stats")

    conn = sqlite3.connect(args.db_path, timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")

    if args.backfill:
        if not is_provenance_migration_applied(conn):
            print(json.dumps({"status": "error", "message": "Run migration_010 first"}))
            conn.close()
            sys.exit(1)
        result = backfill_from_consolidation_sources(conn, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
    elif args.stats:
        result = get_provenance_stats(conn)
        print(json.dumps(result, indent=2))
    else:
        parser.print_help()

    conn.close()


if __name__ == "__main__":
    _cli()
