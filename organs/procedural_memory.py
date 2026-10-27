#!/usr/bin/env python3
"""Procedural Memory Promotion Pipeline — Wizard Library L2 Component 5.

Stand-alone module that tracks usage events for procedural memories and
computes promotion/review scores based on outcome quality.

Public API
----------
``log_usage(conn, memory_id, ...) -> int``
    Logs a usage event. Enforces kind='procedural'. Returns log entry ID.

``compute_promotion_score(conn, memory_id) -> dict``
    Computes a weighted promotion score from usage history.

``get_promotion_candidates(conn, min_score=0.7, min_usage=3) -> List[dict]``
    Procedural memories ready for promotion consideration.

``get_review_candidates(conn, max_score=0.3, min_age_days=30) -> List[dict]``
    Procedural memories that should be reviewed for deprecation.

``get_usage_stats(conn, memory_id=None) -> dict``
    Usage history for a specific memory or aggregate stats.

``is_procedural_migration_applied(conn) -> bool``
    Check if migration 004 has been applied.

``apply_procedural_migration(db_path, migration_sql_path) -> dict``
    Idempotent migration runner. Mirrors C1/C2 patterns.

``unit_test_procedural_memory()``
    Self-contained unit tests.

Phase 0 memories: #3918 (procedural design), #3965 (C2 complete),
#3823 (L3 skill graduation preview).
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ============================================================
# Constants
# ============================================================

VALID_OUTCOMES = ("success", "partial", "failure", "unknown")
VALID_TRIGGERS = ("recall", "manual", "cron", "sleep_loop")

# Scoring weights (sum = 1.0)
_W_SUCCESS_RATE = 0.4
_W_USAGE_COUNT = 0.3
_W_RECENCY = 0.2
_W_TREND = 0.1

# Thresholds for recommendations
_PROMOTE_SCORE = 0.7
_PROMOTE_MIN_USAGE = 3
_PROMOTE_MIN_SUCCESS_RATE = 0.6
_REVIEW_SCORE = 0.3
_REVIEW_MIN_SUCCESS_RATE = 0.3
_REVIEW_MIN_AGE_DAYS = 30
_INSUFFICIENT_DATA_USAGE = 2


# ============================================================
# Migration helpers (mirrors C1/C2 patterns)
# ============================================================

def is_procedural_migration_applied(conn: sqlite3.Connection) -> bool:
    """Check if migration 004 (skill_usage_log) has been applied."""
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        return "skill_usage_log" in tables
    except Exception:
        return False


def apply_procedural_migration(
    db_path: str,
    migration_sql_path: str,
) -> dict:
    """Apply migration 004 idempotently. Returns status dict."""
    t0 = time.time()
    db = Path(db_path)
    mig = Path(migration_sql_path)

    if not db.exists():
        return {"status": "error", "message": f"Database not found: {db_path}"}
    if not mig.exists():
        return {"status": "error", "message": f"Migration SQL not found: {migration_sql_path}"}

    conn = sqlite3.connect(str(db), timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")

    if is_procedural_migration_applied(conn):
        conn.close()
        return {"status": "already-applied", "time_ms": int((time.time() - t0) * 1000)}

    sql = mig.read_text()
    try:
        conn.executescript(sql)
        conn.commit()
    except Exception as e:
        conn.close()
        return {"status": "error", "message": f"Migration failed: {e}"}

    # Verify
    if not is_procedural_migration_applied(conn):
        conn.close()
        return {"status": "error", "message": "Migration applied but table not found"}

    sv = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
    conn.close()

    return {
        "status": "ok",
        "schema_version": sv[0] if sv else "unknown",
        "time_ms": int((time.time() - t0) * 1000),
    }


# ============================================================
# Core API
# ============================================================

def log_usage(
    conn: sqlite3.Connection,
    memory_id: int,
    outcome: str = "unknown",
    context: Optional[str] = None,
    outcome_notes: Optional[str] = None,
    triggered_by: str = "recall",
    session_id: Optional[str] = None,
) -> int:
    """Log a usage event for a procedural memory.

    Validates:
    - memory_id exists AND is not archived
    - memory has kind='procedural' (only procedural memories can be logged)
    - outcome is a valid value
    - triggered_by is a valid value

    Returns: the log entry ID.
    Raises: ValueError for invalid inputs.
    """
    # Validate outcome
    if outcome not in VALID_OUTCOMES:
        raise ValueError(
            f"Invalid outcome '{outcome}'. Valid: {list(VALID_OUTCOMES)}"
        )

    # Validate triggered_by
    if triggered_by not in VALID_TRIGGERS:
        raise ValueError(
            f"Invalid triggered_by '{triggered_by}'. Valid: {list(VALID_TRIGGERS)}"
        )

    # Check memory exists and is procedural
    row = conn.execute(
        "SELECT id, kind, is_archived FROM memories WHERE id = ?",
        (memory_id,),
    ).fetchone()

    if row is None:
        raise ValueError(f"Memory {memory_id} not found")

    _, kind, is_archived = row

    if is_archived:
        raise ValueError(f"Memory {memory_id} is archived — cannot log usage")

    if kind != "procedural":
        raise ValueError(
            f"Memory {memory_id} has kind='{kind}', not 'procedural'. "
            f"Only procedural memories can be logged in skill_usage_log."
        )

    cursor = conn.execute(
        """INSERT INTO skill_usage_log
           (memory_id, context, outcome, outcome_notes, triggered_by, session_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (memory_id, context, outcome, outcome_notes, triggered_by, session_id),
    )
    conn.commit()

    return cursor.lastrowid


def compute_promotion_score(
    conn: sqlite3.Connection,
    memory_id: int,
) -> dict:
    """Compute a promotion score for a procedural memory.

    Score formula:
        score = (success_rate * 0.4)
              + (log2(usage_count + 1) / log2(max_expected + 1) * 0.3)
              + (recency_factor * 0.2)
              + (trend_factor * 0.1)

    Where:
        - success_rate = success_count / (success + partial + failure), excluding unknown
        - recency_factor = max(0, 1.0 - days_since_last_use / 180)
        - trend_factor = (recent_half_count - older_half_count) / total, clamped [-1, 1]
        - max_expected = 20 (caps the log contribution to avoid runaway scores)

    Returns dict with: memory_id, score, usage_count, success_rate,
                       recency_days, trend, recommendation.
    """
    # Verify memory exists and is procedural
    mem_row = conn.execute(
        "SELECT id, kind, is_archived, summary, importance FROM memories WHERE id = ?",
        (memory_id,),
    ).fetchone()

    if mem_row is None:
        raise ValueError(f"Memory {memory_id} not found")

    _, kind, is_archived, summary, importance = mem_row

    if kind != "procedural":
        raise ValueError(
            f"Memory {memory_id} has kind='{kind}', not 'procedural'."
        )

    # Fetch all usage events
    events = conn.execute(
        "SELECT outcome, used_at FROM skill_usage_log WHERE memory_id = ? ORDER BY used_at ASC",
        (memory_id,),
    ).fetchall()

    usage_count = len(events)

    if usage_count == 0:
        return {
            "memory_id": memory_id,
            "summary": summary,
            "importance": importance,
            "score": 0.0,
            "usage_count": 0,
            "success_rate": 0.0,
            "recency_days": None,
            "trend": 0.0,
            "recommendation": "insufficient_data",
        }

    # --- Component 1: Success rate (0.4 weight) ---
    outcome_counts = {"success": 0, "partial": 0, "failure": 0, "unknown": 0}
    for outcome, _ in events:
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

    known_outcomes = (
        outcome_counts["success"]
        + outcome_counts["partial"]
        + outcome_counts["failure"]
    )

    if known_outcomes > 0:
        # partial counts as 0.5 success
        success_rate = (
            outcome_counts["success"] + 0.5 * outcome_counts["partial"]
        ) / known_outcomes
    else:
        # All outcomes are 'unknown' — treat as neutral (0.5)
        success_rate = 0.5

    # --- Component 2: Usage count (0.3 weight) ---
    # Logarithmic scale, capped at max_expected=20 to prevent runaway.
    max_expected = 20.0
    usage_factor = math.log2(usage_count + 1) / math.log2(max_expected + 1)
    usage_factor = min(usage_factor, 1.0)  # Hard cap at 1.0

    # --- Component 3: Recency (0.2 weight) ---
    # Linear decay over 180 days. More recent = higher score.
    last_used_str = events[-1][1]  # Last event (sorted ASC)
    try:
        last_used = datetime.fromisoformat(last_used_str)
        days_since = (datetime.now() - last_used).days
    except (ValueError, TypeError):
        days_since = 180  # Worst case if parse fails

    recency_factor = max(0.0, 1.0 - days_since / 180.0)
    recency_days = days_since

    # --- Component 4: Trend (0.1 weight) ---
    # Compare usage in the recent half vs older half of the event timeline.
    if usage_count >= 2:
        midpoint = usage_count // 2
        older_half = midpoint
        recent_half = usage_count - midpoint
        trend = (recent_half - older_half) / usage_count
        trend = max(-1.0, min(1.0, trend))  # Clamp
    else:
        trend = 0.0

    # Map trend from [-1, 1] to [0, 1] for the weighted sum.
    trend_factor = (trend + 1.0) / 2.0

    # --- Weighted score ---
    score = (
        success_rate * _W_SUCCESS_RATE
        + usage_factor * _W_USAGE_COUNT
        + recency_factor * _W_RECENCY
        + trend_factor * _W_TREND
    )
    score = round(score, 4)

    # --- Recommendation ---
    if usage_count < _INSUFFICIENT_DATA_USAGE:
        recommendation = "insufficient_data"
    elif (
        score >= _PROMOTE_SCORE
        and usage_count >= _PROMOTE_MIN_USAGE
        and success_rate >= _PROMOTE_MIN_SUCCESS_RATE
    ):
        recommendation = "promote"
    elif score < _REVIEW_SCORE or success_rate < _REVIEW_MIN_SUCCESS_RATE:
        recommendation = "review"
    else:
        recommendation = "maintain"

    return {
        "memory_id": memory_id,
        "summary": summary,
        "importance": importance,
        "score": score,
        "usage_count": usage_count,
        "success_rate": round(success_rate, 4),
        "recency_days": recency_days,
        "trend": round(trend, 4),
        "recommendation": recommendation,
        "outcome_breakdown": outcome_counts,
    }


def get_promotion_candidates(
    conn: sqlite3.Connection,
    min_score: float = _PROMOTE_SCORE,
    min_usage: int = _PROMOTE_MIN_USAGE,
) -> List[dict]:
    """Return procedural memories ready for promotion consideration.

    Scans all procedural memories with usage data and returns those
    whose promotion score meets the threshold.
    """
    # Find all procedural memories that have at least one usage event.
    rows = conn.execute(
        """SELECT DISTINCT s.memory_id
           FROM skill_usage_log s
           JOIN memories m ON m.id = s.memory_id
           WHERE m.kind = 'procedural' AND m.is_archived = 0"""
    ).fetchall()

    candidates = []
    for (memory_id,) in rows:
        result = compute_promotion_score(conn, memory_id)
        if (
            result["usage_count"] >= min_usage
            and result["score"] >= min_score
            and result["recommendation"] == "promote"
        ):
            candidates.append(result)

    # Sort by score descending
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def get_review_candidates(
    conn: sqlite3.Connection,
    max_score: float = _REVIEW_SCORE,
    min_age_days: int = _REVIEW_MIN_AGE_DAYS,
) -> List[dict]:
    """Return procedural memories that should be reviewed for deprecation.

    Two paths to review:
    1. Has usage data with low score or low success rate.
    2. Old procedural memory with zero usage (never tested in practice).
    """
    candidates = []

    # Path 1: Procedural memories with usage data but poor outcomes.
    rows_with_usage = conn.execute(
        """SELECT DISTINCT s.memory_id
           FROM skill_usage_log s
           JOIN memories m ON m.id = s.memory_id
           WHERE m.kind = 'procedural' AND m.is_archived = 0"""
    ).fetchall()

    for (memory_id,) in rows_with_usage:
        result = compute_promotion_score(conn, memory_id)
        if result["recommendation"] == "review":
            candidates.append(result)

    # Path 2: Old procedural memories with NO usage data at all.
    cutoff = (datetime.now() - timedelta(days=min_age_days)).isoformat()
    rows_no_usage = conn.execute(
        """SELECT m.id, m.summary, m.importance, m.created_at
           FROM memories m
           WHERE m.kind = 'procedural'
             AND m.is_archived = 0
             AND m.created_at <= ?
             AND m.id NOT IN (SELECT DISTINCT memory_id FROM skill_usage_log)""",
        (cutoff,),
    ).fetchall()

    for mem_id, summary, importance, created_at in rows_no_usage:
        try:
            created = datetime.fromisoformat(created_at)
            age_days = (datetime.now() - created).days
        except (ValueError, TypeError):
            age_days = min_age_days + 1

        candidates.append({
            "memory_id": mem_id,
            "summary": summary,
            "importance": importance,
            "score": 0.0,
            "usage_count": 0,
            "success_rate": 0.0,
            "recency_days": None,
            "trend": 0.0,
            "recommendation": "review",
            "reason": f"No usage data after {age_days} days",
            "outcome_breakdown": {"success": 0, "partial": 0, "failure": 0, "unknown": 0},
        })

    # Sort: lowest score first (most urgently needs review).
    candidates.sort(key=lambda x: x["score"])
    return candidates


def get_usage_stats(
    conn: sqlite3.Connection,
    memory_id: Optional[int] = None,
) -> dict:
    """Get usage statistics.

    If memory_id is provided: returns usage history for that specific memory.
    If None: returns aggregate stats across all procedural memories.
    """
    if memory_id is not None:
        # Single memory stats
        events = conn.execute(
            """SELECT id, used_at, context, outcome, outcome_notes, triggered_by, session_id
               FROM skill_usage_log
               WHERE memory_id = ?
               ORDER BY used_at DESC""",
            (memory_id,),
        ).fetchall()

        history = []
        for row in events:
            history.append({
                "log_id": row[0],
                "used_at": row[1],
                "context": row[2],
                "outcome": row[3],
                "outcome_notes": row[4],
                "triggered_by": row[5],
                "session_id": row[6],
            })

        # Get memory summary
        mem = conn.execute(
            "SELECT summary, kind, importance FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()

        score_data = None
        if mem and mem[1] == "procedural":
            try:
                score_data = compute_promotion_score(conn, memory_id)
            except ValueError:
                pass

        return {
            "memory_id": memory_id,
            "summary": mem[0] if mem else None,
            "kind": mem[1] if mem else None,
            "importance": mem[2] if mem else None,
            "total_events": len(history),
            "history": history,
            "score": score_data,
        }

    # Aggregate stats across all procedural memories
    total_procedural = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE kind = 'procedural' AND is_archived = 0"
    ).fetchone()[0]

    total_with_usage = conn.execute(
        """SELECT COUNT(DISTINCT s.memory_id)
           FROM skill_usage_log s
           JOIN memories m ON m.id = s.memory_id
           WHERE m.kind = 'procedural' AND m.is_archived = 0"""
    ).fetchone()[0]

    total_events = conn.execute(
        "SELECT COUNT(*) FROM skill_usage_log"
    ).fetchone()[0]

    by_outcome = {}
    for row in conn.execute(
        "SELECT outcome, COUNT(*) FROM skill_usage_log GROUP BY outcome"
    ).fetchall():
        by_outcome[row[0]] = row[1]

    by_trigger = {}
    for row in conn.execute(
        "SELECT triggered_by, COUNT(*) FROM skill_usage_log GROUP BY triggered_by"
    ).fetchall():
        by_trigger[row[0]] = row[1]

    # Compute average score across memories with usage data
    scored_ids = conn.execute(
        """SELECT DISTINCT s.memory_id
           FROM skill_usage_log s
           JOIN memories m ON m.id = s.memory_id
           WHERE m.kind = 'procedural' AND m.is_archived = 0"""
    ).fetchall()

    scores = []
    for (mid,) in scored_ids:
        try:
            result = compute_promotion_score(conn, mid)
            scores.append(result["score"])
        except ValueError:
            pass

    avg_score = sum(scores) / len(scores) if scores else 0.0

    # Count promotion and review candidates (lightweight — just counts)
    promote_count = 0
    review_count = 0
    for (mid,) in scored_ids:
        try:
            result = compute_promotion_score(conn, mid)
            if result["recommendation"] == "promote":
                promote_count += 1
            elif result["recommendation"] == "review":
                review_count += 1
        except ValueError:
            pass

    return {
        "total_procedural": total_procedural,
        "with_usage": total_with_usage,
        "without_usage": total_procedural - total_with_usage,
        "total_events": total_events,
        "avg_score": round(avg_score, 4),
        "promotion_candidates": promote_count,
        "review_candidates": review_count,
        "by_outcome": by_outcome,
        "by_trigger": by_trigger,
    }


# ============================================================
# Unit tests
# ============================================================

def unit_test_procedural_memory():
    """Self-contained unit tests. Uses an in-memory database."""
    import traceback

    passed = 0
    failed = 0
    errors = []

    def _assert(condition, name, detail=""):
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  PASS: {name}")
        else:
            failed += 1
            msg = f"  FAIL: {name}"
            if detail:
                msg += f" — {detail}"
            print(msg)
            errors.append(msg)

    def _setup_db():
        """Create a fresh in-memory database with the required schema."""
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys=ON")

        # Core memories table (minimal schema matching production)
        conn.execute("""
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
                kind TEXT DEFAULT 'episodic'
                    CHECK(kind IN ('episodic', 'semantic', 'procedural', 'reflection')),
                derived_from TEXT,
                consolidated_at TEXT,
                confidence REAL DEFAULT 1.0
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Apply the skill_usage_log table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS skill_usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                used_at TEXT NOT NULL DEFAULT (datetime('now')),
                context TEXT DEFAULT NULL,
                outcome TEXT DEFAULT 'unknown'
                    CHECK(outcome IN ('success', 'partial', 'failure', 'unknown')),
                outcome_notes TEXT DEFAULT NULL,
                triggered_by TEXT DEFAULT 'recall'
                    CHECK(triggered_by IN ('recall', 'manual', 'cron', 'sleep_loop')),
                session_id TEXT DEFAULT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_memory ON skill_usage_log(memory_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_outcome ON skill_usage_log(outcome)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_time ON skill_usage_log(used_at)")

        return conn

    def _insert_memory(conn, kind="procedural", category="skill_lesson",
                       summary="Test memory", importance=0.5, is_archived=0,
                       created_at=None):
        """Insert a test memory and return its ID."""
        if created_at is None:
            created_at = datetime.now().isoformat()
        cursor = conn.execute(
            """INSERT INTO memories (content, summary, category, importance, kind, is_archived, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (f"Content for {summary}", summary, category, importance, kind, is_archived, created_at),
        )
        conn.commit()
        return cursor.lastrowid

    print("\n=== Unit Tests: procedural_memory.py ===\n")

    # --- Test 1: log_usage basic ---
    print("[T1] log_usage — basic procedural memory logging")
    conn = _setup_db()
    mid = _insert_memory(conn, kind="procedural", summary="ACE loop pattern")
    log_id = log_usage(conn, mid, outcome="success", context="testing")
    _assert(log_id > 0, "log_usage returns positive ID")
    row = conn.execute("SELECT * FROM skill_usage_log WHERE id = ?", (log_id,)).fetchone()
    _assert(row is not None, "log entry exists in table")
    _assert(row[4] == "success", "outcome recorded correctly", f"got={row[4]}")
    conn.close()

    # --- Test 2: log_usage rejects non-procedural ---
    print("\n[T2] log_usage — rejects non-procedural memories")
    conn = _setup_db()
    mid_sem = _insert_memory(conn, kind="semantic", summary="Domain knowledge")
    mid_epi = _insert_memory(conn, kind="episodic", summary="Project event")
    mid_ref = _insert_memory(conn, kind="reflection", summary="Insight memo")

    for mid, kind_name in [(mid_sem, "semantic"), (mid_epi, "episodic"), (mid_ref, "reflection")]:
        try:
            log_usage(conn, mid, outcome="success")
            _assert(False, f"should reject {kind_name}", "no exception raised")
        except ValueError as e:
            _assert(f"not 'procedural'" in str(e), f"rejects {kind_name} with correct message")
    conn.close()

    # --- Test 3: log_usage rejects archived ---
    print("\n[T3] log_usage — rejects archived memories")
    conn = _setup_db()
    mid_arch = _insert_memory(conn, kind="procedural", summary="Old pattern", is_archived=1)
    try:
        log_usage(conn, mid_arch, outcome="success")
        _assert(False, "should reject archived", "no exception raised")
    except ValueError as e:
        _assert("archived" in str(e), "rejects archived with correct message")
    conn.close()

    # --- Test 4: log_usage rejects nonexistent ---
    print("\n[T4] log_usage — rejects nonexistent memory")
    conn = _setup_db()
    try:
        log_usage(conn, 99999, outcome="success")
        _assert(False, "should reject nonexistent", "no exception raised")
    except ValueError as e:
        _assert("not found" in str(e), "rejects nonexistent with correct message")
    conn.close()

    # --- Test 5: log_usage validates outcome ---
    print("\n[T5] log_usage — validates outcome values")
    conn = _setup_db()
    mid = _insert_memory(conn, kind="procedural", summary="Test")
    for valid_outcome in VALID_OUTCOMES:
        log_id = log_usage(conn, mid, outcome=valid_outcome)
        _assert(log_id > 0, f"accepts outcome='{valid_outcome}'")
    try:
        log_usage(conn, mid, outcome="garbage")
        _assert(False, "should reject invalid outcome", "no exception raised")
    except ValueError as e:
        _assert("Invalid outcome" in str(e), "rejects invalid outcome")
    conn.close()

    # --- Test 6: log_usage validates triggered_by ---
    print("\n[T6] log_usage — validates triggered_by values")
    conn = _setup_db()
    mid = _insert_memory(conn, kind="procedural", summary="Test")
    for valid_trigger in VALID_TRIGGERS:
        log_id = log_usage(conn, mid, triggered_by=valid_trigger)
        _assert(log_id > 0, f"accepts triggered_by='{valid_trigger}'")
    try:
        log_usage(conn, mid, triggered_by="invalid_trigger")
        _assert(False, "should reject invalid trigger", "no exception raised")
    except ValueError as e:
        _assert("Invalid triggered_by" in str(e), "rejects invalid trigger")
    conn.close()

    # --- Test 7: compute_promotion_score — zero usage ---
    print("\n[T7] compute_promotion_score — zero usage (insufficient data)")
    conn = _setup_db()
    mid = _insert_memory(conn, kind="procedural", summary="Never used")
    result = compute_promotion_score(conn, mid)
    _assert(result["score"] == 0.0, "score is 0.0 with no usage")
    _assert(result["usage_count"] == 0, "usage_count is 0")
    _assert(result["recommendation"] == "insufficient_data", "recommendation is insufficient_data")
    conn.close()

    # --- Test 8: compute_promotion_score — all success ---
    print("\n[T8] compute_promotion_score — all success (5 uses)")
    conn = _setup_db()
    mid = _insert_memory(conn, kind="procedural", summary="Always works")
    for _ in range(5):
        log_usage(conn, mid, outcome="success")
    result = compute_promotion_score(conn, mid)
    _assert(result["success_rate"] == 1.0, "success_rate is 1.0")
    _assert(result["usage_count"] == 5, "usage_count is 5")
    _assert(result["score"] > 0.7, f"score > 0.7 (got {result['score']})")
    _assert(result["recommendation"] == "promote", f"recommendation=promote (got {result['recommendation']})")
    conn.close()

    # --- Test 9: compute_promotion_score — all failure ---
    print("\n[T9] compute_promotion_score — all failure (3 uses)")
    conn = _setup_db()
    mid = _insert_memory(conn, kind="procedural", summary="Always fails")
    for _ in range(3):
        log_usage(conn, mid, outcome="failure")
    result = compute_promotion_score(conn, mid)
    _assert(result["success_rate"] == 0.0, "success_rate is 0.0")
    # Score is NOT < 0.3 because usage_count + recency contribute even when
    # success_rate = 0.  The review recommendation triggers on success_rate < 0.3,
    # not on the composite score alone.  This is intentional: a frequently-used-
    # but-always-failing procedure should still surface for review.
    _assert(result["score"] < 0.6, f"score < 0.6 (got {result['score']})")
    _assert(result["recommendation"] == "review", f"recommendation=review (got {result['recommendation']})")
    conn.close()

    # --- Test 10: compute_promotion_score — mixed outcomes ---
    print("\n[T10] compute_promotion_score — mixed outcomes (2 success, 1 partial, 1 failure)")
    conn = _setup_db()
    mid = _insert_memory(conn, kind="procedural", summary="Mixed bag")
    log_usage(conn, mid, outcome="success")
    log_usage(conn, mid, outcome="success")
    log_usage(conn, mid, outcome="partial")
    log_usage(conn, mid, outcome="failure")
    result = compute_promotion_score(conn, mid)
    # success_rate = (2 + 0.5*1) / (2+1+1) = 2.5/4 = 0.625
    _assert(abs(result["success_rate"] - 0.625) < 0.01, f"success_rate ~0.625 (got {result['success_rate']})")
    _assert(result["usage_count"] == 4, "usage_count is 4")
    _assert(0.3 <= result["score"] <= 0.9, f"score in [0.3, 0.9] (got {result['score']})")
    conn.close()

    # --- Test 11: compute_promotion_score — all unknown ---
    print("\n[T11] compute_promotion_score — all unknown outcomes")
    conn = _setup_db()
    mid = _insert_memory(conn, kind="procedural", summary="Unknown outcomes")
    for _ in range(5):
        log_usage(conn, mid, outcome="unknown")
    result = compute_promotion_score(conn, mid)
    _assert(result["success_rate"] == 0.5, f"success_rate is 0.5 (neutral) (got {result['success_rate']})")
    _assert(result["recommendation"] == "maintain", f"recommendation=maintain (got {result['recommendation']})")
    conn.close()

    # --- Test 12: compute_promotion_score — single use ---
    print("\n[T12] compute_promotion_score — single use (insufficient data)")
    conn = _setup_db()
    mid = _insert_memory(conn, kind="procedural", summary="Single use")
    log_usage(conn, mid, outcome="success")
    result = compute_promotion_score(conn, mid)
    _assert(result["usage_count"] == 1, "usage_count is 1")
    _assert(result["recommendation"] == "insufficient_data", f"recommendation=insufficient_data (got {result['recommendation']})")
    conn.close()

    # --- Test 13: compute_promotion_score rejects non-procedural ---
    print("\n[T13] compute_promotion_score — rejects non-procedural")
    conn = _setup_db()
    mid_sem = _insert_memory(conn, kind="semantic", summary="Domain fact")
    try:
        compute_promotion_score(conn, mid_sem)
        _assert(False, "should reject semantic", "no exception raised")
    except ValueError as e:
        _assert("not 'procedural'" in str(e), "rejects semantic memory")
    conn.close()

    # --- Test 14: get_promotion_candidates ---
    print("\n[T14] get_promotion_candidates — finds qualifying memories")
    conn = _setup_db()
    mid_good = _insert_memory(conn, kind="procedural", summary="Great pattern")
    mid_ok = _insert_memory(conn, kind="procedural", summary="OK pattern")
    mid_bad = _insert_memory(conn, kind="procedural", summary="Bad pattern")
    # Good: 5 successes
    for _ in range(5):
        log_usage(conn, mid_good, outcome="success")
    # OK: 3 mixed
    log_usage(conn, mid_ok, outcome="success")
    log_usage(conn, mid_ok, outcome="partial")
    log_usage(conn, mid_ok, outcome="failure")
    # Bad: 3 failures
    for _ in range(3):
        log_usage(conn, mid_bad, outcome="failure")
    candidates = get_promotion_candidates(conn)
    candidate_ids = [c["memory_id"] for c in candidates]
    _assert(mid_good in candidate_ids, "good pattern is a promotion candidate")
    _assert(mid_bad not in candidate_ids, "bad pattern is NOT a promotion candidate")
    conn.close()

    # --- Test 15: get_review_candidates (with usage) ---
    print("\n[T15] get_review_candidates — finds poor-performing memories")
    conn = _setup_db()
    mid_bad = _insert_memory(conn, kind="procedural", summary="Consistently fails")
    for _ in range(4):
        log_usage(conn, mid_bad, outcome="failure")
    candidates = get_review_candidates(conn, min_age_days=0)
    candidate_ids = [c["memory_id"] for c in candidates]
    _assert(mid_bad in candidate_ids, "failing memory is a review candidate")
    conn.close()

    # --- Test 16: get_review_candidates (no usage, old) ---
    print("\n[T16] get_review_candidates — finds old unused procedural memories")
    conn = _setup_db()
    old_date = (datetime.now() - timedelta(days=60)).isoformat()
    mid_old = _insert_memory(conn, kind="procedural", summary="Ancient pattern",
                             created_at=old_date)
    candidates = get_review_candidates(conn, min_age_days=30)
    candidate_ids = [c["memory_id"] for c in candidates]
    _assert(mid_old in candidate_ids, "old unused procedural memory is review candidate")
    _assert(
        any(c.get("reason", "").startswith("No usage data") for c in candidates if c["memory_id"] == mid_old),
        "reason mentions no usage data",
    )
    conn.close()

    # --- Test 17: get_usage_stats (single memory) ---
    print("\n[T17] get_usage_stats — single memory history")
    conn = _setup_db()
    mid = _insert_memory(conn, kind="procedural", summary="Stats target")
    log_usage(conn, mid, outcome="success", context="test-session-1")
    log_usage(conn, mid, outcome="failure", context="test-session-2")
    stats = get_usage_stats(conn, memory_id=mid)
    _assert(stats["total_events"] == 2, f"total_events=2 (got {stats['total_events']})")
    _assert(len(stats["history"]) == 2, "history has 2 entries")
    _assert(stats["history"][0]["outcome"] in VALID_OUTCOMES, "history entries have valid outcomes")
    conn.close()

    # --- Test 18: get_usage_stats (aggregate) ---
    print("\n[T18] get_usage_stats — aggregate stats")
    conn = _setup_db()
    mid1 = _insert_memory(conn, kind="procedural", summary="Proc 1")
    mid2 = _insert_memory(conn, kind="procedural", summary="Proc 2")
    _insert_memory(conn, kind="semantic", summary="Sem 1")
    log_usage(conn, mid1, outcome="success")
    log_usage(conn, mid2, outcome="failure")
    stats = get_usage_stats(conn)
    _assert(stats["total_procedural"] >= 2, f"total_procedural >= 2 (got {stats['total_procedural']})")
    _assert(stats["with_usage"] == 2, f"with_usage=2 (got {stats['with_usage']})")
    _assert(stats["total_events"] == 2, f"total_events=2 (got {stats['total_events']})")
    conn.close()

    # --- Test 19: session_id grouping ---
    print("\n[T19] log_usage — session_id recorded correctly")
    conn = _setup_db()
    mid = _insert_memory(conn, kind="procedural", summary="Session test")
    log_usage(conn, mid, outcome="success", session_id="sess-abc-123")
    log_usage(conn, mid, outcome="partial", session_id="sess-abc-123")
    log_usage(conn, mid, outcome="failure", session_id="sess-def-456")
    rows = conn.execute(
        "SELECT session_id FROM skill_usage_log WHERE memory_id = ? ORDER BY id",
        (mid,),
    ).fetchall()
    _assert(rows[0][0] == "sess-abc-123", "first event has correct session")
    _assert(rows[2][0] == "sess-def-456", "third event has different session")
    sess_count = conn.execute(
        "SELECT COUNT(DISTINCT session_id) FROM skill_usage_log WHERE memory_id = ?",
        (mid,),
    ).fetchone()[0]
    _assert(sess_count == 2, f"2 distinct sessions (got {sess_count})")
    conn.close()

    # --- Test 20: is_procedural_migration_applied ---
    print("\n[T20] is_procedural_migration_applied")
    conn = _setup_db()
    _assert(is_procedural_migration_applied(conn), "migration detected in test DB")
    conn2 = sqlite3.connect(":memory:")
    conn2.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY)")
    _assert(not is_procedural_migration_applied(conn2), "migration NOT detected in bare DB")
    conn.close()
    conn2.close()

    # --- Test 21: partial outcomes count as half success ---
    print("\n[T21] compute_promotion_score — partial counts as 0.5 success")
    conn = _setup_db()
    mid = _insert_memory(conn, kind="procedural", summary="Partial test")
    for _ in range(4):
        log_usage(conn, mid, outcome="partial")
    result = compute_promotion_score(conn, mid)
    # success_rate = (0 + 0.5*4) / 4 = 0.5
    _assert(abs(result["success_rate"] - 0.5) < 0.01, f"success_rate=0.5 with all partial (got {result['success_rate']})")
    conn.close()

    # --- Test 22: score components are bounded [0, 1] ---
    print("\n[T22] compute_promotion_score — score bounded [0, 1]")
    conn = _setup_db()
    mid = _insert_memory(conn, kind="procedural", summary="Bound test")
    for _ in range(100):
        log_usage(conn, mid, outcome="success")
    result = compute_promotion_score(conn, mid)
    _assert(0.0 <= result["score"] <= 1.0, f"score in [0,1] even with 100 uses (got {result['score']})")
    conn.close()

    # --- Test 23: outcome_breakdown in score result ---
    print("\n[T23] compute_promotion_score — outcome_breakdown present")
    conn = _setup_db()
    mid = _insert_memory(conn, kind="procedural", summary="Breakdown test")
    log_usage(conn, mid, outcome="success")
    log_usage(conn, mid, outcome="failure")
    log_usage(conn, mid, outcome="unknown")
    result = compute_promotion_score(conn, mid)
    _assert("outcome_breakdown" in result, "outcome_breakdown key present")
    _assert(result["outcome_breakdown"]["success"] == 1, "1 success in breakdown")
    _assert(result["outcome_breakdown"]["failure"] == 1, "1 failure in breakdown")
    _assert(result["outcome_breakdown"]["unknown"] == 1, "1 unknown in breakdown")
    conn.close()

    # --- Test 24: CASCADE delete ---
    print("\n[T24] CASCADE delete — usage logs removed when memory deleted")
    conn = _setup_db()
    mid = _insert_memory(conn, kind="procedural", summary="Delete me")
    log_usage(conn, mid, outcome="success")
    log_usage(conn, mid, outcome="failure")
    count_before = conn.execute("SELECT COUNT(*) FROM skill_usage_log").fetchone()[0]
    _assert(count_before == 2, f"2 log entries before delete (got {count_before})")
    conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
    conn.commit()
    count_after = conn.execute("SELECT COUNT(*) FROM skill_usage_log").fetchone()[0]
    _assert(count_after == 0, f"0 log entries after cascade delete (got {count_after})")
    conn.close()

    # --- Summary ---
    print(f"\n{'='*50}")
    print(f"  Results: {passed} passed, {failed} failed out of {passed + failed}")
    print(f"{'='*50}")

    if errors:
        print("\nFailed tests:")
        for e in errors:
            print(f"  {e}")

    return {"passed": passed, "failed": failed, "errors": errors}


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--unit-tests":
        result = unit_test_procedural_memory()
        sys.exit(0 if result["failed"] == 0 else 1)

    if len(sys.argv) > 1 and sys.argv[1] == "apply-migration":
        if len(sys.argv) < 4:
            print("Usage: procedural_memory.py apply-migration <db_path> <migration_sql>")
            sys.exit(1)
        result = apply_procedural_migration(sys.argv[2], sys.argv[3])
        print(json.dumps(result))
        sys.exit(0 if result["status"] in ("ok", "already-applied") else 1)

    print("Usage:")
    print("  procedural_memory.py --unit-tests")
    print("  procedural_memory.py apply-migration <db_path> <migration_sql>")
    sys.exit(1)
