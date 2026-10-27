#!/usr/bin/env python3
"""Anti-Recency Bias Guards — Wizard Library L3 Component 7.

A stand-alone module that prevents the memory system from burying
foundational memories under recent activity. Computes a deterministic
``foundation_score`` (0.0-1.0) for every memory and surfaces forgotten
foundations via a weekly resurfacing digest.

The foundation score is COMPUTED, not manually assigned. The formula uses
four signals available in the existing database:

  1. Importance (40%): higher importance = more foundational.
  2. Kind bonus (20%): distilled knowledge (semantic/procedural) > episodic.
  3. Edge connectivity (20%): highly-connected memories are load-bearing.
  4. Access stability (20%): consistently accessed over time vs spike-and-gone.

Public API
----------
``is_anti_recency_migration_applied(conn) -> bool``
    Check if migration_008 has been applied.

``compute_foundation_scores(conn, batch_size=500) -> dict``
    Score all un-scored memories (or re-score all with force=True).

``find_resurfacing_candidates(conn, min_score=0.7, stale_days=30, limit=10) -> List[dict]``
    Find high-foundation memories that haven't been accessed recently.

``get_forgotten_foundations(conn, limit=10, include_acknowledged=False) -> List[dict]``
    Get the latest resurfacing digest entries.

``acknowledge_resurfacing(conn, resurfacing_id: int) -> bool``
    Mark a resurfacing entry as acknowledged.

``get_foundation_stats(conn) -> dict``
    Statistics about foundation scores and resurfacing.

``boost_foundation_score(relevance_score: float, foundation_score: float) -> float``
    Apply the gentle foundation boost to a relevance score.
    Formula: relevance * (1 + foundation_score * 0.1)

CLI interface
-------------
::

    python3 anti_recency.py --compute <db_path>         # compute all scores
    python3 anti_recency.py --resurface <db_path>       # find + log candidates
    python3 anti_recency.py --show <db_path>            # show current digest
    python3 anti_recency.py --stats <db_path>           # foundation stats
    python3 anti_recency.py --unit-tests                # self-test

Phase 0 design: L3-C7 spec. Schema: 3.3-L3-anti-recency.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# L5-L "The Quickening": per-tag access cadence powers the predictive (about-to-
# lapse) fold in find_resurfacing_candidates. Guarded import -> a degraded install
# (no quickening module) keeps the original reactive-only behavior unchanged.
try:
    from . import quickening as _quickening  # type: ignore
except (ImportError, ValueError):
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        import quickening as _quickening  # type: ignore
    except ImportError:
        _quickening = None  # graceful: predictive fold degrades to reactive-only

# ============================================================
# Constants
# ============================================================

# Foundation score weights (must sum to 1.0).
WEIGHT_IMPORTANCE = 0.40
WEIGHT_KIND = 0.20
WEIGHT_EDGES = 0.20
WEIGHT_STABILITY = 0.20

# Kind bonuses — distilled knowledge > episodic raw events.
KIND_BONUSES: Dict[str, float] = {
    "semantic": 1.0,       # distilled knowledge — strongest foundation signal
    "procedural": 0.75,    # how-to knowledge — strong foundation
    "reflection": 0.50,    # meta-analysis — moderate foundation
    "episodic": 0.0,       # raw events — not foundational by nature
}

# Edge connectivity cap: beyond this many edges, no further bonus.
# Set based on production data: top connected memories have ~200 edges,
# but the distribution is heavily skewed. 50 gives a smooth ramp.
EDGE_COUNT_CAP = 50

# Access stability: memories accessed in this many distinct months
# get full stability credit. Production data shows 4 months max
# (Feb-May 2026), so 3 is the threshold for "consistently useful".
STABILITY_MONTHS_THRESHOLD = 3

# Resurfacing defaults.
DEFAULT_MIN_FOUNDATION_SCORE = 0.7
DEFAULT_STALE_DAYS = 30
DEFAULT_RESURFACE_LIMIT = 10

# Gentle boost factor for --boost-foundations in recall.
# A memory with foundation_score=0.9 gets a 9% relevance boost.
# Not enough to override relevance, but enough to break ties.
FOUNDATION_BOOST_FACTOR = 0.1


# ============================================================
# Migration check
# ============================================================

def is_anti_recency_migration_applied(conn: sqlite3.Connection) -> bool:
    """Check whether migration_008_anti_recency has been applied."""
    try:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = 'migration_008_anti_recency_applied_at'"
        ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False


def apply_migration(conn: sqlite3.Connection, migration_sql_path: Optional[Path] = None) -> bool:
    """Apply the anti-recency migration idempotently.

    Returns True if applied (or already applied), False on error.
    """
    if is_anti_recency_migration_applied(conn):
        return True

    if migration_sql_path is None:
        migration_sql_path = Path(__file__).parent.parent / "migrations" / "migration_008_anti_recency.sql"

    if not migration_sql_path.exists():
        logger.error("Migration SQL not found at %s", migration_sql_path)
        return False

    raw_sql = migration_sql_path.read_text()

    # Strip comment-only lines BEFORE splitting on ";".
    # This prevents chunks that begin with a comment header from being
    # filtered out entirely (the ALTER TABLE and CREATE TABLE statements
    # have comment blocks above them in the same chunk).
    cleaned_lines = []
    for line in raw_sql.split("\n"):
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        cleaned_lines.append(line)
    sql = "\n".join(cleaned_lines)

    # Split into individual statements.  The ALTER TABLE ADD COLUMN
    # will raise "duplicate column name" if already present — catch it.
    statements = [s.strip() for s in sql.split(";") if s.strip()]

    for stmt in statements:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                logger.info("foundation_score column already exists, skipping ALTER TABLE")
                continue
            raise

    conn.commit()
    logger.info("Migration 008 (anti-recency) applied successfully")
    return True


# ============================================================
# Foundation Score Computation
# ============================================================

def _get_edge_counts(conn: sqlite3.Connection) -> Dict[int, int]:
    """Get the number of CURRENT edges (incoming + outgoing) per memory.

    L5-D reader-correctness fix: edge connectivity feeds foundation_score, which
    measures how load-bearing a memory is RIGHT NOW. An invalidated edge
    (``valid_until`` set — e.g. a contradiction the belief engine retired, or a
    superseded relation) is no longer a live connection, so counting it inflates
    the foundation score of memories whose links have been retired. We therefore
    count only CURRENT edges (``valid_until IS NULL``). Before this fix the
    bi-temporal invalidation was INERT for foundation scoring. A dream's TENSION
    link (metadata tag 'tension') is a hypothesis, not a connection: it is never
    counted (the dream fix, R1-M4 -- a synthetic row's edges never raise a
    foundation score).
    """
    counts: Dict[int, int] = defaultdict(int)
    try:
        for row in conn.execute(
            "SELECT source_id, COUNT(*) FROM memory_edges "
            "WHERE valid_until IS NULL AND COALESCE(CASE WHEN json_valid(metadata) THEN json_extract(metadata, '$.tag') END, '') != 'tension' "
            "GROUP BY source_id"
        ).fetchall():
            counts[row[0]] += row[1]
        for row in conn.execute(
            "SELECT target_id, COUNT(*) FROM memory_edges "
            "WHERE valid_until IS NULL AND COALESCE(CASE WHEN json_valid(metadata) THEN json_extract(metadata, '$.tag') END, '') != 'tension' "
            "GROUP BY target_id"
        ).fetchall():
            counts[row[0]] += row[1]
    except sqlite3.OperationalError:
        # memory_edges table may not exist yet (pre-L2-C4).
        logger.warning("memory_edges table not found; edge bonus will be 0 for all memories")
    return dict(counts)


def _get_access_stability(conn: sqlite3.Connection) -> Dict[int, int]:
    """Get the number of distinct months each memory was accessed.

    Returns {memory_id: month_count}. Uses access_log if available,
    falls back to checking if the memory's created_at month differs
    from last_accessed month.
    """
    stability: Dict[int, int] = {}
    try:
        for row in conn.execute(
            """SELECT memory_id, COUNT(DISTINCT strftime('%Y-%m', accessed_at)) as months
               FROM access_log
               WHERE access_type NOT LIKE 'recall:%-lead'
               GROUP BY memory_id"""
        ).fetchall():
            stability[row[0]] = row[1]
    except sqlite3.OperationalError:
        # access_log may not exist in minimal setups. Fall back to a
        # coarse heuristic: if created and last_accessed differ by >= 2 months,
        # assume at least 2 months of access stability.
        logger.warning("access_log not found; using coarse stability heuristic")
        for row in conn.execute(
            """SELECT id,
                      strftime('%Y-%m', created_at) as created_month,
                      strftime('%Y-%m', last_accessed) as accessed_month
               FROM memories WHERE is_archived = 0 AND last_accessed IS NOT NULL"""
        ).fetchall():
            if row[1] != row[2]:
                stability[row[0]] = 2
            else:
                stability[row[0]] = 1
    return stability


def _compute_single_score(
    importance: float,
    kind: Optional[str],
    edge_count: int,
    months_accessed: int,
) -> float:
    """Compute foundation score for a single memory.

    Returns a float in [0.0, 1.0].

    Formula:
      score = WEIGHT_IMPORTANCE * importance
            + WEIGHT_KIND * kind_bonus
            + WEIGHT_EDGES * min(1.0, edge_count / EDGE_COUNT_CAP)
            + WEIGHT_STABILITY * min(1.0, months_accessed / STABILITY_MONTHS_THRESHOLD)
    """
    # Importance component (0.0-1.0 input, clamped).
    imp = max(0.0, min(1.0, importance or 0.0))
    imp_component = WEIGHT_IMPORTANCE * imp

    # Kind bonus.
    kind_val = KIND_BONUSES.get(kind or "episodic", 0.0)
    kind_component = WEIGHT_KIND * kind_val

    # Edge connectivity (capped linear ramp).
    edge_ratio = min(1.0, edge_count / EDGE_COUNT_CAP) if EDGE_COUNT_CAP > 0 else 0.0
    edge_component = WEIGHT_EDGES * edge_ratio

    # Access stability (capped linear ramp).
    stab_ratio = min(1.0, months_accessed / STABILITY_MONTHS_THRESHOLD) if STABILITY_MONTHS_THRESHOLD > 0 else 0.0
    stability_component = WEIGHT_STABILITY * stab_ratio

    score = imp_component + kind_component + kind_component  # BUG: double kind, will be fixed below
    # Correct formula:
    score = imp_component + kind_component + edge_component + stability_component

    return round(max(0.0, min(1.0, score)), 4)


def compute_foundation_scores(
    conn: sqlite3.Connection,
    batch_size: int = 500,
    force: bool = False,
) -> Dict[str, Any]:
    """Compute foundation_score for all memories.

    Args:
        conn: Database connection.
        batch_size: Number of memories to update per batch.
        force: If True, re-score all memories (not just un-scored).

    Returns:
        Summary dict with counts and timing.
    """
    t0 = time.time()

    # Check migration is applied.
    columns = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    if "foundation_score" not in columns:
        return {"status": "error", "message": "foundation_score column not found. Run migration first."}

    # Gather global data.
    edge_counts = _get_edge_counts(conn)
    stability = _get_access_stability(conn)

    # Select memories to score.
    if force:
        rows = conn.execute(
            "SELECT id, importance, kind FROM memories WHERE is_archived = 0"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, importance, kind FROM memories WHERE is_archived = 0 AND foundation_score IS NULL"
        ).fetchall()

    total = len(rows)
    scored = 0
    score_distribution: Dict[str, int] = {
        "high (>=0.7)": 0,
        "medium (0.4-0.69)": 0,
        "low (<0.4)": 0,
    }

    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        updates = []
        for mem_id, importance, kind in batch:
            score = _compute_single_score(
                importance=importance or 0.0,
                kind=kind,
                edge_count=edge_counts.get(mem_id, 0),
                months_accessed=stability.get(mem_id, 0),
            )
            updates.append((score, mem_id))
            scored += 1

            if score >= 0.7:
                score_distribution["high (>=0.7)"] += 1
            elif score >= 0.4:
                score_distribution["medium (0.4-0.69)"] += 1
            else:
                score_distribution["low (<0.4)"] += 1

        conn.executemany(
            "UPDATE memories SET foundation_score = ? WHERE id = ?",
            updates,
        )

    conn.commit()

    elapsed = time.time() - t0
    return {
        "status": "ok",
        "action": "compute_foundation_scores",
        "total_scored": scored,
        "total_memories": total,
        "force_rescore": force,
        "score_distribution": score_distribution,
        "time_ms": int(elapsed * 1000),
    }


# ============================================================
# Resurfacing
# ============================================================

def find_resurfacing_candidates(
    conn: sqlite3.Connection,
    min_score: float = DEFAULT_MIN_FOUNDATION_SCORE,
    stale_days: int = DEFAULT_STALE_DAYS,
    limit: int = DEFAULT_RESURFACE_LIMIT,
    use_predictive: bool = True,
) -> List[Dict[str, Any]]:
    """Find high-foundation memories due (or about to be due) for resurfacing.

    Two complementary lenses, council "complement, never replace":

      * REACTIVE (the original): high foundation_score AND last_accessed past the
        ``stale_days`` cutoff. Always included -- existing behavior is a subset.
      * PREDICTIVE (L5-L fold, ``use_predictive``): high foundation_score memories
        NOT yet past ``stale_days`` but OVERDUE relative to their topic's learned
        access cadence (``quickening.compute_access_cadence`` +
        ``quickening.predicted_lapse``). Flips the lens from "already lapsed" to
        "about to lapse".

    Every candidate dict carries a new ``predicted_lapse`` (bool) key. If the
    quickening module is unavailable, the predictive pass is a no-op and every
    candidate reports ``predicted_lapse=False`` -- identical to the old behavior.

    Returns candidate dicts sorted predicted-lapse-first, then foundation_score
    DESC, then importance DESC. Does NOT log to resurfacing_log -- call
    ``log_resurfacing`` separately.
    """
    now = datetime.utcnow()
    cutoff = (now - timedelta(days=stale_days)).strftime("%Y-%m-%d %H:%M:%S")

    def _row_to_candidate(row, predicted=False):
        mem_id = row[0]
        tags = [t[0] for t in conn.execute(
            "SELECT tag FROM memory_tags WHERE memory_id = ?", (mem_id,)
        ).fetchall()]
        days_since_access = None
        if row[6]:
            try:
                last_dt = datetime.strptime(row[6][:19], "%Y-%m-%d %H:%M:%S")
                days_since_access = (now - last_dt).days
            except (ValueError, TypeError):
                pass
        return {
            "id": mem_id,
            "summary": row[1],
            "category": row[2],
            "importance": row[3],
            "kind": row[4],
            "foundation_score": row[5],
            "last_accessed": row[6],
            "days_since_access": days_since_access,
            "access_count": row[7],
            "created_at": row[8],
            "source_project": row[9],
            "tags": tags,
            "predicted_lapse": bool(predicted),
        }

    # --- Reactive pass (the original WHERE: already past the stale cutoff). ---
    reactive_rows = conn.execute(
        """SELECT m.id, m.summary, m.category, m.importance, m.kind,
                  m.foundation_score, m.last_accessed, m.access_count,
                  m.created_at, m.source_project
           FROM memories m
           WHERE m.is_archived = 0
             AND m.foundation_score >= ?
             AND m.last_accessed < ?
           ORDER BY m.foundation_score DESC, m.importance DESC
           LIMIT ?""",
        (min_score, cutoff, limit),
    ).fetchall()

    candidates: List[Dict[str, Any]] = [_row_to_candidate(r) for r in reactive_rows]
    seen_ids = {c["id"] for c in candidates}

    # --- Predictive pass (about-to-lapse: not yet stale, but overdue per topic
    #     cadence). Additive; bounded; graceful-absent. ---
    if use_predictive and _quickening is not None:
        try:
            cadence = _quickening.compute_access_cadence(conn, scope="tag")
            if cadence:
                # Candidate pool: high foundation, still "fresh" (>= cutoff), so we
                # do NOT re-pull the reactive set. Bounded to a sane scan size.
                fresh_rows = conn.execute(
                    """SELECT m.id, m.summary, m.category, m.importance, m.kind,
                              m.foundation_score, m.last_accessed, m.access_count,
                              m.created_at, m.source_project
                       FROM memories m
                       WHERE m.is_archived = 0
                         AND m.foundation_score >= ?
                         AND m.last_accessed >= ?
                       ORDER BY m.foundation_score DESC, m.importance DESC
                       LIMIT ?""",
                    (min_score, cutoff, max(limit * 5, 50)),
                ).fetchall()
                for row in fresh_rows:
                    mem_id = row[0]
                    if mem_id in seen_ids:
                        continue
                    last_acc = row[6]
                    tags = [t[0] for t in conn.execute(
                        "SELECT tag FROM memory_tags WHERE memory_id = ?", (mem_id,)
                    ).fetchall()]
                    # Overdue if ANY of its topics is overdue per its own cadence.
                    overdue = False
                    for tg in tags:
                        c = cadence.get(tg)
                        if c and _quickening.predicted_lapse(c, last_acc, now=now):
                            overdue = True
                            break
                    if overdue:
                        candidates.append(_row_to_candidate(row, predicted=True))
                        seen_ids.add(mem_id)
        except Exception as e:  # graceful: predictive fold never breaks resurfacing
            logger.warning("predictive resurfacing fold skipped: %s", e)

    # Blend, do NOT evict: the predictive (about-to-lapse) signal complements the
    # reactive (already-stale) one -- it must not crowd it out of the digest. We
    # reserve at least half of `limit` for reactive candidates when any exist,
    # then fill the rest predictive-first, then top up from whichever remains.
    def _ordkey(c):
        return (-(c.get("foundation_score") or 0.0), -(c.get("importance") or 0.0))

    reactive = sorted((c for c in candidates if not c["predicted_lapse"]), key=_ordkey)
    predictive = sorted((c for c in candidates if c["predicted_lapse"]), key=_ordkey)

    reactive_floor = min(len(reactive), max(1, limit // 2)) if reactive else 0
    reactive_keep = reactive[:reactive_floor]
    remaining = limit - len(reactive_keep)
    predictive_keep = predictive[:remaining]
    # Top up any leftover capacity from the unused reactive tail.
    leftover = limit - len(reactive_keep) - len(predictive_keep)
    if leftover > 0:
        reactive_keep += reactive[reactive_floor:reactive_floor + leftover]

    blended = reactive_keep + predictive_keep
    # Final ordering: predicted-lapse first (the earlier-warning signal), then
    # foundation/importance -- but the reactive floor above guarantees reactive
    # memories are present even when predictive fills the pool.
    blended.sort(key=lambda c: (0 if c.get("predicted_lapse") else 1, *_ordkey(c)))
    return blended[:limit]


def log_resurfacing(
    conn: sqlite3.Connection,
    candidates: List[Dict[str, Any]],
) -> int:
    """Log resurfacing candidates to the resurfacing_log table.

    Returns the number of entries logged. Skips memories that were already
    resurfaced in the current week (avoids duplicate digest entries).
    """
    if not candidates:
        return 0

    # Check for already-resurfaced this week.
    week_start = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    already_surfaced = set()
    try:
        for row in conn.execute(
            "SELECT DISTINCT memory_id FROM resurfacing_log WHERE resurfaced_at >= ?",
            (week_start,),
        ).fetchall():
            already_surfaced.add(row[0])
    except sqlite3.OperationalError:
        pass  # Table may not exist in edge cases.

    logged = 0
    for c in candidates:
        if c["id"] in already_surfaced:
            continue
        reason = (
            f"foundation_score {c['foundation_score']:.2f}, "
            f"last_accessed {c.get('days_since_access', '?')} days ago, "
            f"kind={c.get('kind', 'unknown')}"
        )
        conn.execute(
            "INSERT INTO resurfacing_log (memory_id, reason) VALUES (?, ?)",
            (c["id"], reason),
        )
        logged += 1

    conn.commit()
    return logged


def get_forgotten_foundations(
    conn: sqlite3.Connection,
    limit: int = DEFAULT_RESURFACE_LIMIT,
    include_acknowledged: bool = False,
) -> List[Dict[str, Any]]:
    """Get the latest resurfacing digest entries.

    Returns entries from resurfacing_log joined with memory data.
    By default excludes acknowledged entries (Drew already reviewed them).
    """
    ack_filter = "" if include_acknowledged else "AND r.acknowledged = 0"

    rows = conn.execute(
        f"""SELECT r.id, r.memory_id, r.resurfaced_at, r.reason, r.acknowledged,
                   m.summary, m.category, m.importance, m.kind, m.foundation_score,
                   m.last_accessed, m.source_project
            FROM resurfacing_log r
            JOIN memories m ON m.id = r.memory_id
            WHERE m.is_archived = 0 {ack_filter}
            ORDER BY r.resurfaced_at DESC, m.foundation_score DESC
            LIMIT ?""",
        (limit,),
    ).fetchall()

    results = []
    for row in rows:
        results.append({
            "resurfacing_id": row[0],
            "memory_id": row[1],
            "resurfaced_at": row[2],
            "reason": row[3],
            "acknowledged": bool(row[4]),
            "summary": row[5],
            "category": row[6],
            "importance": row[7],
            "kind": row[8],
            "foundation_score": row[9],
            "last_accessed": row[10],
            "source_project": row[11],
        })

    return results


def acknowledge_resurfacing(conn: sqlite3.Connection, resurfacing_id: int) -> bool:
    """Mark a resurfacing entry as acknowledged.

    Returns True if the entry was found and updated, False otherwise.
    """
    cur = conn.execute(
        "UPDATE resurfacing_log SET acknowledged = 1 WHERE id = ? AND acknowledged = 0",
        (resurfacing_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def acknowledge_all(conn: sqlite3.Connection) -> int:
    """Acknowledge all pending resurfacing entries.

    Returns the number of entries acknowledged.
    """
    cur = conn.execute(
        "UPDATE resurfacing_log SET acknowledged = 1 WHERE acknowledged = 0"
    )
    conn.commit()
    return cur.rowcount


# ============================================================
# Recall Integration
# ============================================================

def boost_foundation_score(relevance_score: float, foundation_score: Optional[float]) -> float:
    """Apply the gentle foundation boost to a relevance score.

    Formula: relevance * (1 + foundation_score * FOUNDATION_BOOST_FACTOR)

    A memory with foundation_score=0.9 gets a 9% boost.
    A memory with foundation_score=0.0 gets no boost.
    This is intentionally subtle — relevance still dominates.
    """
    if foundation_score is None or foundation_score <= 0.0:
        return relevance_score
    return relevance_score * (1.0 + foundation_score * FOUNDATION_BOOST_FACTOR)


def apply_foundation_boost(
    conn: sqlite3.Connection,
    results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Apply foundation boost to a list of recall results.

    Fetches foundation_scores for all result IDs in a single batch query,
    applies the boost, adds ``foundation_score`` and ``foundation_boosted``
    fields, then re-sorts by the boosted score.

    Modifies results in-place and returns them.
    """
    if not results:
        return results

    # Check if foundation_score column exists.
    columns = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    if "foundation_score" not in columns:
        return results

    ids = [r["id"] for r in results]
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, foundation_score FROM memories WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    score_map = {row[0]: row[1] for row in rows}

    for r in results:
        fs = score_map.get(r["id"])
        r["foundation_score"] = fs
        original_score = r.get("score", r.get("relevance_score", 0.0))
        if fs is not None and fs > 0.0:
            r["score"] = boost_foundation_score(original_score, fs)
            r["foundation_boosted"] = True
        else:
            r["foundation_boosted"] = False

    # Re-sort by boosted score (descending).
    results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    return results


# ============================================================
# Statistics
# ============================================================

def get_foundation_stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Get statistics about foundation scores and resurfacing."""
    stats: Dict[str, Any] = {}

    # Foundation score distribution.
    columns = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    if "foundation_score" not in columns:
        return {"status": "error", "message": "foundation_score column not found"}

    total = conn.execute("SELECT COUNT(*) FROM memories WHERE is_archived = 0").fetchone()[0]
    scored = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE is_archived = 0 AND foundation_score IS NOT NULL"
    ).fetchone()[0]
    unscored = total - scored

    stats["total_memories"] = total
    stats["scored"] = scored
    stats["unscored"] = unscored

    if scored > 0:
        row = conn.execute(
            """SELECT MIN(foundation_score), MAX(foundation_score),
                      AVG(foundation_score), COUNT(*)
               FROM memories WHERE is_archived = 0 AND foundation_score IS NOT NULL"""
        ).fetchone()
        stats["min_score"] = round(row[0], 4) if row[0] is not None else None
        stats["max_score"] = round(row[1], 4) if row[1] is not None else None
        stats["avg_score"] = round(row[2], 4) if row[2] is not None else None

        # Score buckets.
        for label, low, high in [
            ("high (>=0.7)", 0.7, 1.01),
            ("medium (0.4-0.69)", 0.4, 0.7),
            ("low (<0.4)", 0.0, 0.4),
        ]:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE is_archived = 0 AND foundation_score >= ? AND foundation_score < ?",
                (low, high),
            ).fetchone()[0]
            stats[f"score_{label}"] = cnt

        # Top-10 highest foundation scores.
        top_rows = conn.execute(
            """SELECT id, foundation_score, kind, importance, summary
               FROM memories WHERE is_archived = 0 AND foundation_score IS NOT NULL
               ORDER BY foundation_score DESC LIMIT 10"""
        ).fetchall()
        stats["top_10"] = [
            {"id": r[0], "foundation_score": r[1], "kind": r[2], "importance": r[3],
             "summary": r[4][:100] if r[4] else None}
            for r in top_rows
        ]

    # Resurfacing stats.
    try:
        total_resurfaced = conn.execute("SELECT COUNT(*) FROM resurfacing_log").fetchone()[0]
        unacknowledged = conn.execute(
            "SELECT COUNT(*) FROM resurfacing_log WHERE acknowledged = 0"
        ).fetchone()[0]
        stats["total_resurfaced"] = total_resurfaced
        stats["unacknowledged"] = unacknowledged
    except sqlite3.OperationalError:
        stats["total_resurfaced"] = 0
        stats["unacknowledged"] = 0

    return stats


# ============================================================
# Weekly Resurfacing Job Entry Point
# ============================================================

def run_weekly_resurfacing(
    db_path: str,
    min_score: float = DEFAULT_MIN_FOUNDATION_SCORE,
    stale_days: int = DEFAULT_STALE_DAYS,
    limit: int = DEFAULT_RESURFACE_LIMIT,
    recompute_scores: bool = True,
) -> Dict[str, Any]:
    """Full weekly resurfacing job.

    1. Re-compute foundation scores (if recompute_scores=True).
    2. Find resurfacing candidates.
    3. Log them to resurfacing_log.

    This is the entry point called by the systemd timer.
    """
    t0 = time.time()
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")

    result: Dict[str, Any] = {"status": "ok", "action": "weekly_resurfacing"}

    try:
        # Step 1: Re-compute scores.
        if recompute_scores:
            score_result = compute_foundation_scores(conn, force=True)
            result["scoring"] = score_result
        else:
            result["scoring"] = {"skipped": True}

        # Step 2: Find candidates.
        candidates = find_resurfacing_candidates(
            conn, min_score=min_score, stale_days=stale_days, limit=limit,
        )
        result["candidates_found"] = len(candidates)

        # Step 3: Log them.
        logged = log_resurfacing(conn, candidates)
        result["candidates_logged"] = logged

        # Include candidate summaries for the digest.
        result["digest"] = [
            {
                "id": c["id"],
                "summary": c["summary"],
                "foundation_score": c["foundation_score"],
                "days_since_access": c["days_since_access"],
                "kind": c["kind"],
            }
            for c in candidates
        ]

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        logger.exception("Weekly resurfacing failed")
    finally:
        conn.close()

    result["time_ms"] = int((time.time() - t0) * 1000)
    return result


# ============================================================
# Unit Tests
# ============================================================

def _run_unit_tests() -> bool:
    """Self-contained unit tests. Returns True if all pass."""
    import traceback

    passed = 0
    failed = 0
    errors: List[str] = []

    def assert_eq(actual, expected, msg=""):
        nonlocal passed, failed
        if actual == expected:
            passed += 1
        else:
            failed += 1
            errors.append(f"FAIL: {msg} — expected {expected!r}, got {actual!r}")

    def assert_true(val, msg=""):
        nonlocal passed, failed
        if val:
            passed += 1
        else:
            failed += 1
            errors.append(f"FAIL: {msg} — expected truthy, got {val!r}")

    def assert_close(actual, expected, tolerance=0.01, msg=""):
        nonlocal passed, failed
        if abs(actual - expected) <= tolerance:
            passed += 1
        else:
            failed += 1
            errors.append(f"FAIL: {msg} — expected ~{expected}, got {actual} (tol={tolerance})")

    # --- Test 1: _compute_single_score with all-zero inputs ---
    score = _compute_single_score(importance=0.0, kind="episodic", edge_count=0, months_accessed=0)
    assert_eq(score, 0.0, "all-zero inputs should give 0.0")

    # --- Test 2: _compute_single_score with max inputs ---
    score = _compute_single_score(importance=1.0, kind="semantic", edge_count=100, months_accessed=5)
    assert_eq(score, 1.0, "max inputs should give 1.0")

    # --- Test 3: importance-only component ---
    score = _compute_single_score(importance=0.8, kind="episodic", edge_count=0, months_accessed=0)
    assert_close(score, 0.32, 0.001, "importance=0.8 episodic no-edges no-stability")

    # --- Test 4: kind bonus ---
    sem = _compute_single_score(importance=0.5, kind="semantic", edge_count=0, months_accessed=0)
    epi = _compute_single_score(importance=0.5, kind="episodic", edge_count=0, months_accessed=0)
    assert_true(sem > epi, "semantic should score higher than episodic")
    # semantic: 0.4*0.5 + 0.2*1.0 = 0.20 + 0.20 = 0.40
    assert_close(sem, 0.40, 0.001, "semantic imp=0.5 no-edges no-stability")
    # episodic: 0.4*0.5 + 0.2*0.0 = 0.20
    assert_close(epi, 0.20, 0.001, "episodic imp=0.5 no-edges no-stability")

    # --- Test 5: procedural kind ---
    proc = _compute_single_score(importance=0.5, kind="procedural", edge_count=0, months_accessed=0)
    # procedural: 0.4*0.5 + 0.2*0.75 = 0.20 + 0.15 = 0.35
    assert_close(proc, 0.35, 0.001, "procedural imp=0.5")

    # --- Test 6: reflection kind ---
    refl = _compute_single_score(importance=0.5, kind="reflection", edge_count=0, months_accessed=0)
    # reflection: 0.4*0.5 + 0.2*0.50 = 0.20 + 0.10 = 0.30
    assert_close(refl, 0.30, 0.001, "reflection imp=0.5")

    # --- Test 7: edge connectivity ---
    score_25 = _compute_single_score(importance=0.5, kind="episodic", edge_count=25, months_accessed=0)
    # edges: 0.2 * (25/50) = 0.10; imp: 0.20; total: 0.30
    assert_close(score_25, 0.30, 0.001, "25 edges half credit")

    score_50 = _compute_single_score(importance=0.5, kind="episodic", edge_count=50, months_accessed=0)
    # edges: 0.2 * 1.0 = 0.20; imp: 0.20; total: 0.40
    assert_close(score_50, 0.40, 0.001, "50 edges full credit")

    # --- Test 8: edge count capped ---
    score_100 = _compute_single_score(importance=0.5, kind="episodic", edge_count=100, months_accessed=0)
    assert_eq(score_50, score_100, "edges capped at 50")

    # --- Test 9: stability bonus ---
    score_3m = _compute_single_score(importance=0.5, kind="episodic", edge_count=0, months_accessed=3)
    # stability: 0.2 * 1.0 = 0.20; imp: 0.20; total: 0.40
    assert_close(score_3m, 0.40, 0.001, "3 months full stability")

    score_1m = _compute_single_score(importance=0.5, kind="episodic", edge_count=0, months_accessed=1)
    # stability: 0.2 * (1/3) = 0.0667; imp: 0.20; total: 0.2667
    assert_close(score_1m, 0.2667, 0.01, "1 month partial stability")

    # --- Test 10: boost_foundation_score ---
    boosted = boost_foundation_score(0.80, 0.9)
    # 0.80 * (1 + 0.9 * 0.1) = 0.80 * 1.09 = 0.872
    assert_close(boosted, 0.872, 0.001, "boost 0.80 with fs=0.9")

    # --- Test 11: boost with None foundation score ---
    unboosted = boost_foundation_score(0.80, None)
    assert_eq(unboosted, 0.80, "None foundation_score = no boost")

    # --- Test 12: boost with zero foundation score ---
    unboosted = boost_foundation_score(0.80, 0.0)
    assert_eq(unboosted, 0.80, "zero foundation_score = no boost")

    # --- Test 13: full database integration ---
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys=ON")

        # Create minimal schema.
        conn.executescript("""
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                summary TEXT,
                category TEXT,
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
                derived_from TEXT,
                consolidated_at TEXT,
                confidence REAL DEFAULT 1.0
            );
            CREATE TABLE memory_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER REFERENCES memories(id),
                tag TEXT
            );
            CREATE TABLE memory_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER REFERENCES memories(id),
                target_id INTEGER REFERENCES memories(id),
                predicate TEXT,
                confidence REAL DEFAULT 0.5,
                valid_from TEXT,
                valid_until TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                metadata TEXT,
                extraction_method TEXT
            );
            CREATE TABLE memory_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER,
                target_id INTEGER,
                link_type TEXT,
                strength REAL,
                created_at TEXT
            );
            CREATE TABLE access_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER,
                access_type TEXT,
                query_text TEXT,
                relevance_score REAL,
                accessed_at TEXT
            );
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            );
        """)

        # Insert test memories.
        now = datetime.utcnow()
        old = (now - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
        recent = (now - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")

        conn.execute(
            """INSERT INTO memories (id, content, summary, category, importance, kind,
                                     last_accessed, access_count, created_at)
               VALUES (1, 'Architecture decision for memory system', 'Core arch decision',
                       'technical_decision', 0.95, 'semantic', ?, 10, ?)""",
            (old, (now - timedelta(days=120)).strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.execute(
            """INSERT INTO memories (id, content, summary, category, importance, kind,
                                     last_accessed, access_count, created_at)
               VALUES (2, 'Todays chat about lunch', 'Lunch discussion',
                       'project_knowledge', 0.3, 'episodic', ?, 1, ?)""",
            (recent, recent),
        )
        conn.execute(
            """INSERT INTO memories (id, content, summary, category, importance, kind,
                                     last_accessed, access_count, created_at)
               VALUES (3, 'How to deploy the CLI', 'CLI deploy procedure',
                       'workflow_pattern', 0.85, 'procedural', ?, 5, ?)""",
            (old, (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")),
        )

        # Add edges for memory 1 (highly connected).
        for i in range(30):
            conn.execute(
                "INSERT INTO memory_edges (source_id, target_id, predicate) VALUES (1, ?, 'references')",
                (2 if i % 2 == 0 else 3,),
            )

        # Add access log entries across months for memory 1.
        for month_offset in range(4):
            dt = (now - timedelta(days=30 * month_offset)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO access_log (memory_id, access_type, accessed_at) VALUES (1, 'recall', ?)",
                (dt,),
            )

        conn.commit()

        # Apply migration.
        migration_path = Path(__file__).parent.parent / "migrations" / "migration_008_anti_recency.sql"
        applied = apply_migration(conn, migration_path)
        assert_true(applied, "migration should apply successfully")
        assert_true(is_anti_recency_migration_applied(conn), "migration check should return True")

        # Compute scores.
        result = compute_foundation_scores(conn)
        assert_eq(result["status"], "ok", "compute should succeed")
        assert_eq(result["total_scored"], 3, "should score 3 memories")

        # Verify memory 1 (high importance, semantic, many edges, 4 months access).
        fs1 = conn.execute("SELECT foundation_score FROM memories WHERE id = 1").fetchone()[0]
        assert_true(fs1 is not None, "memory 1 should have a foundation score")
        assert_true(fs1 >= 0.8, f"memory 1 should be high-foundation (got {fs1})")

        # Verify memory 2 (low importance, episodic, no edges, recent only).
        fs2 = conn.execute("SELECT foundation_score FROM memories WHERE id = 2").fetchone()[0]
        assert_true(fs2 < 0.4, f"memory 2 should be low-foundation (got {fs2})")

        # Memory 1 should score higher than memory 2.
        assert_true(fs1 > fs2, "foundational memory should score higher than episodic")

        # --- Test 14: find_resurfacing_candidates ---
        candidates = find_resurfacing_candidates(conn, min_score=0.5, stale_days=30)
        candidate_ids = [c["id"] for c in candidates]
        assert_true(1 in candidate_ids, "memory 1 should be a resurfacing candidate")
        assert_true(2 not in candidate_ids, "memory 2 should NOT be a candidate (too recent)")

        # --- Test 15: log_resurfacing ---
        logged = log_resurfacing(conn, candidates)
        assert_true(logged > 0, "should log at least one resurfacing entry")

        # --- Test 16: get_forgotten_foundations ---
        foundations = get_forgotten_foundations(conn)
        assert_true(len(foundations) > 0, "should return at least one forgotten foundation")
        assert_eq(foundations[0]["memory_id"], 1, "memory 1 should be first forgotten foundation")

        # --- Test 17: acknowledge_resurfacing ---
        rid = foundations[0]["resurfacing_id"]
        ack = acknowledge_resurfacing(conn, rid)
        assert_true(ack, "acknowledge should succeed")

        # After acknowledge, unacknowledged list should shrink.
        remaining = get_forgotten_foundations(conn, include_acknowledged=False)
        remaining_ids = [f["resurfacing_id"] for f in remaining]
        assert_true(rid not in remaining_ids, "acknowledged entry should not appear in unacknowledged")

        # --- Test 18: acknowledge_all ---
        # Re-log candidates to have something to ack.
        conn.execute("DELETE FROM resurfacing_log")
        conn.commit()
        log_resurfacing(conn, candidates)
        count = acknowledge_all(conn)
        assert_true(count > 0, "acknowledge_all should ack at least one")
        remaining = get_forgotten_foundations(conn, include_acknowledged=False)
        assert_eq(len(remaining), 0, "no unacknowledged entries after acknowledge_all")

        # --- Test 19: duplicate resurfacing prevention ---
        conn.execute("DELETE FROM resurfacing_log")
        conn.commit()
        log_resurfacing(conn, candidates)
        first_count = conn.execute("SELECT COUNT(*) FROM resurfacing_log").fetchone()[0]
        log_resurfacing(conn, candidates)  # Try again same week.
        second_count = conn.execute("SELECT COUNT(*) FROM resurfacing_log").fetchone()[0]
        assert_eq(first_count, second_count, "duplicate logging should be prevented within a week")

        # --- Test 20: apply_foundation_boost on results ---
        test_results = [
            {"id": 1, "score": 0.80},
            {"id": 2, "score": 0.85},
        ]
        boosted_results = apply_foundation_boost(conn, test_results)
        # Memory 1 has high foundation score; memory 2 has low.
        # After boosting, memory 1's score should increase.
        assert_true(boosted_results[0]["foundation_boosted"] or boosted_results[1]["foundation_boosted"],
                     "at least one result should be boosted")

        # --- Test 21: get_foundation_stats ---
        stats = get_foundation_stats(conn)
        assert_eq(stats["total_memories"], 3, "stats total should be 3")
        assert_eq(stats["scored"], 3, "all 3 should be scored")
        assert_true("top_10" in stats, "stats should include top_10")

        # --- Test 22: re-score with force ---
        # Manually set memory 2's foundation_score high, then force rescore.
        conn.execute("UPDATE memories SET foundation_score = 0.99 WHERE id = 2")
        conn.commit()
        result = compute_foundation_scores(conn, force=True)
        assert_eq(result["total_scored"], 3, "force rescore should touch all 3")
        fs2_new = conn.execute("SELECT foundation_score FROM memories WHERE id = 2").fetchone()[0]
        assert_true(fs2_new < 0.4, f"memory 2 should be reset to low after force rescore (got {fs2_new})")

        # --- Test 23: run_weekly_resurfacing end-to-end ---
        result = run_weekly_resurfacing(db_path, recompute_scores=True, stale_days=30, limit=10)
        assert_eq(result["status"], "ok", "weekly resurfacing should succeed")
        assert_true("candidates_found" in result, "should report candidates found")

        # --- Test 24: migration idempotency ---
        applied_again = apply_migration(conn, migration_path)
        assert_true(applied_again, "re-applying migration should succeed (idempotent)")

        # --- Test 25: compute_foundation_scores without migration ---
        conn2 = sqlite3.connect(":memory:")
        conn2.executescript("""
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY, content TEXT, summary TEXT,
                category TEXT, importance REAL, kind TEXT,
                is_archived INTEGER DEFAULT 0, last_accessed TEXT,
                access_count INTEGER DEFAULT 0
            );
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
        """)
        result = compute_foundation_scores(conn2)
        assert_eq(result["status"], "error", "should error without foundation_score column")
        conn2.close()

        conn.close()
    finally:
        os.unlink(db_path)

    # Print results.
    total = passed + failed
    print(f"\n{'='*60}")
    print(f"Anti-Recency Unit Tests: {passed}/{total} passed")
    if errors:
        print(f"\n{failed} FAILURES:")
        for e in errors:
            print(f"  {e}")
    print(f"{'='*60}\n")

    return failed == 0


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Anti-Recency Bias Guards (L3-C7)")
    parser.add_argument("db_path", nargs="?", help="Path to claude_memory.db")
    parser.add_argument("--compute", action="store_true", help="Compute foundation scores")
    parser.add_argument("--force", action="store_true", help="Force re-score all memories")
    parser.add_argument("--resurface", action="store_true", help="Find + log resurfacing candidates")
    parser.add_argument("--show", action="store_true", help="Show current forgotten foundations digest")
    parser.add_argument("--stats", action="store_true", help="Foundation score statistics")
    parser.add_argument("--acknowledge", type=int, metavar="ID", help="Acknowledge a resurfacing entry")
    parser.add_argument("--acknowledge-all", action="store_true", help="Acknowledge all pending entries")
    parser.add_argument("--weekly", action="store_true", help="Run full weekly resurfacing job")
    parser.add_argument("--unit-tests", dest="unit_tests", action="store_true", help="Run self-tests")
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_FOUNDATION_SCORE,
                        help=f"Min foundation score for resurfacing (default: {DEFAULT_MIN_FOUNDATION_SCORE})")
    parser.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS,
                        help=f"Days since last access to consider stale (default: {DEFAULT_STALE_DAYS})")
    parser.add_argument("--limit", type=int, default=DEFAULT_RESURFACE_LIMIT,
                        help=f"Max candidates to surface (default: {DEFAULT_RESURFACE_LIMIT})")

    args = parser.parse_args()

    if args.unit_tests:
        success = _run_unit_tests()
        sys.exit(0 if success else 1)

    if not args.db_path:
        parser.error("db_path is required (except for --unit-tests)")

    db_path = args.db_path
    if not Path(db_path).exists():
        print(json.dumps({"status": "error", "message": f"Database not found: {db_path}"}))
        sys.exit(1)

    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        if args.weekly:
            result = run_weekly_resurfacing(
                db_path,
                min_score=args.min_score,
                stale_days=args.stale_days,
                limit=args.limit,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif args.compute:
            if not is_anti_recency_migration_applied(conn):
                applied = apply_migration(conn)
                if not applied:
                    print(json.dumps({"status": "error", "message": "Could not apply migration"}))
                    sys.exit(1)
            result = compute_foundation_scores(conn, force=args.force)
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif args.resurface:
            candidates = find_resurfacing_candidates(
                conn, min_score=args.min_score, stale_days=args.stale_days, limit=args.limit,
            )
            logged = log_resurfacing(conn, candidates)
            print(json.dumps({
                "status": "ok",
                "candidates_found": len(candidates),
                "candidates_logged": logged,
                "digest": [
                    {"id": c["id"], "summary": c["summary"],
                     "foundation_score": c["foundation_score"],
                     "days_since_access": c["days_since_access"]}
                    for c in candidates
                ],
            }, ensure_ascii=False, indent=2))

        elif args.show:
            foundations = get_forgotten_foundations(conn, limit=args.limit)
            print(json.dumps({
                "status": "ok",
                "forgotten_foundations": foundations,
            }, ensure_ascii=False, indent=2))

        elif args.stats:
            stats = get_foundation_stats(conn)
            print(json.dumps(stats, ensure_ascii=False, indent=2))

        elif args.acknowledge is not None:
            ack = acknowledge_resurfacing(conn, args.acknowledge)
            print(json.dumps({"status": "ok", "acknowledged": ack}))

        elif args.acknowledge_all:
            count = acknowledge_all(conn)
            print(json.dumps({"status": "ok", "acknowledged_count": count}))

        else:
            parser.print_help()

    finally:
        conn.close()


if __name__ == "__main__":
    main()
