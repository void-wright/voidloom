#!/usr/bin/env python3
"""Skill Graduation Pipeline — Wizard Library L3 Component 6.

Stand-alone module that evaluates procedural memories and graduates them
through tiers based on usage success rates.

Tier hierarchy:
    seed -> active -> protected  (promotion)
    protected -> active -> review -> archived  (demotion)

Graduation rules:
    SEED -> ACTIVE:     usage >= 3 AND success_rate >= 0.5
    ACTIVE -> PROTECTED: usage >= 10 AND success_rate >= 0.7 AND age >= 30 days
    PROTECTED -> ACTIVE: success_rate < 0.5 over last 10 uses
    ACTIVE -> REVIEW:   no usage in 60 days OR success_rate < 0.3
    REVIEW -> ARCHIVED: no usage in 90 days after entering review

Public API
----------
``is_graduation_migration_applied(conn) -> bool``
    Check if migration 007 tables exist.

``apply_graduation_migration(db_path, migration_sql_path) -> dict``
    Idempotent migration runner.

``evaluate_memory(conn, memory_id) -> dict``
    Evaluate a single procedural memory for tier transition.

``graduate_memory(conn, memory_id, new_tier, reason, ...) -> dict``
    Apply a tier change with logging.

``evaluate_all_procedural(conn, dry_run=False) -> dict``
    Bulk evaluation of all procedural memories. The weekly cron entry point.

``get_graduation_history(conn, memory_id=None, limit=50) -> List[dict]``
    Query the graduation log.

``backfill_tiers(conn, dry_run=False) -> dict``
    One-time backfill: existing procedurals -> 'active', all others -> NULL.

``get_tier_stats(conn) -> dict``
    Aggregate tier distribution stats for cmd_stats integration.

``unit_test_skill_graduation()``
    Self-contained unit tests.

Phase 0 memories: #3823 (L3 skill graduation design),
#3918 (procedural memory design), #3965 (C2 closure).
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
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

VALID_TIERS = ("seed", "active", "protected", "review", "archived")
VALID_GRADUATED_BY = ("cron", "manual", "curator")

# Promotion thresholds
_SEED_TO_ACTIVE_USAGE = 3
_SEED_TO_ACTIVE_SUCCESS_RATE = 0.5

_ACTIVE_TO_PROTECTED_USAGE = 10
_ACTIVE_TO_PROTECTED_SUCCESS_RATE = 0.7
_ACTIVE_TO_PROTECTED_AGE_DAYS = 30

# Demotion thresholds
_PROTECTED_DEMOTION_WINDOW = 10  # last N uses
_PROTECTED_DEMOTION_SUCCESS_RATE = 0.5

_ACTIVE_TO_REVIEW_IDLE_DAYS = 60
_ACTIVE_TO_REVIEW_SUCCESS_RATE = 0.3

_REVIEW_TO_ARCHIVED_IDLE_DAYS = 90


# ============================================================
# Migration helpers
# ============================================================

def is_graduation_migration_applied(conn: sqlite3.Connection) -> bool:
    """Check if migration 007 (skill_graduation_log + skill_tier column) is applied."""
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "skill_graduation_log" not in tables:
            return False
        columns = {r[1] for r in conn.execute(
            "PRAGMA table_info(memories)"
        ).fetchall()}
        return "skill_tier" in columns
    except Exception:
        return False


def _has_graduation_log_table(conn: sqlite3.Connection) -> bool:
    """Check if just the graduation_log table exists (partial migration)."""
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        return "skill_graduation_log" in tables
    except Exception:
        return False


def _has_skill_tier_column(conn: sqlite3.Connection) -> bool:
    """Check if the skill_tier column exists on memories."""
    try:
        columns = {r[1] for r in conn.execute(
            "PRAGMA table_info(memories)"
        ).fetchall()}
        return "skill_tier" in columns
    except Exception:
        return False


def apply_graduation_migration(
    db_path: str,
    migration_sql_path: str,
) -> dict:
    """Apply migration 007 idempotently. Returns status dict.

    Two-phase approach:
    1. Execute the SQL file (creates graduation_log table + metadata).
    2. Programmatically ALTER TABLE memories ADD COLUMN skill_tier
       (SQLite has no IF NOT EXISTS for ALTER TABLE).
    """
    t0 = time.time()
    db = Path(db_path)
    mig = Path(migration_sql_path)

    if not db.exists():
        return {"status": "error", "message": f"Database not found: {db_path}"}
    if not mig.exists():
        return {"status": "error", "message": f"Migration SQL not found: {migration_sql_path}"}

    conn = sqlite3.connect(str(db), timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")

    if is_graduation_migration_applied(conn):
        conn.close()
        return {"status": "already-applied", "time_ms": int((time.time() - t0) * 1000)}

    # Phase 1: Execute SQL file (creates table + indexes + metadata).
    if not _has_graduation_log_table(conn):
        sql = mig.read_text()
        try:
            conn.executescript(sql)
            conn.commit()
        except Exception as e:
            conn.close()
            return {"status": "error", "message": f"Migration SQL failed: {e}"}

    # Phase 2: Add skill_tier column if missing.
    if not _has_skill_tier_column(conn):
        try:
            conn.execute(
                "ALTER TABLE memories ADD COLUMN skill_tier TEXT DEFAULT NULL "
                "CHECK(skill_tier IS NULL OR skill_tier IN "
                "('seed', 'active', 'protected', 'review', 'archived'))"
            )
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                conn.close()
                return {"status": "error", "message": f"ALTER TABLE failed: {e}"}

    # Create index on skill_tier for filtered queries.
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_skill_tier "
            "ON memories(skill_tier)"
        )
        conn.commit()
    except Exception:
        pass

    # Verify
    if not is_graduation_migration_applied(conn):
        conn.close()
        return {"status": "error", "message": "Migration applied but verification failed"}

    sv = conn.execute(
        "SELECT value FROM metadata WHERE key='schema_version'"
    ).fetchone()
    conn.close()

    return {
        "status": "ok",
        "schema_version": sv[0] if sv else "unknown",
        "time_ms": int((time.time() - t0) * 1000),
    }


# ============================================================
# Internal helpers
# ============================================================

def _get_usage_stats_for_memory(
    conn: sqlite3.Connection, memory_id: int
) -> Dict[str, Any]:
    """Get usage stats needed for tier evaluation.

    Returns dict with: total_uses, success_rate, last_used_at,
    days_since_last_use, recent_success_rate (over last N uses).
    """
    events = conn.execute(
        "SELECT outcome, used_at FROM skill_usage_log "
        "WHERE memory_id = ? ORDER BY used_at ASC",
        (memory_id,),
    ).fetchall()

    total_uses = len(events)

    if total_uses == 0:
        return {
            "total_uses": 0,
            "success_rate": 0.0,
            "last_used_at": None,
            "days_since_last_use": None,
            "recent_success_rate": 0.0,
            "recent_window_size": 0,
        }

    # Overall success rate (matching L2-C5 formula: partial = 0.5)
    known = 0
    success_score = 0.0
    for outcome, _ in events:
        if outcome in ("success", "partial", "failure"):
            known += 1
            if outcome == "success":
                success_score += 1.0
            elif outcome == "partial":
                success_score += 0.5

    success_rate = success_score / known if known > 0 else 0.5

    # Recency
    last_used_str = events[-1][1]
    try:
        last_used = datetime.fromisoformat(last_used_str)
        days_since = (datetime.now() - last_used).days
    except (ValueError, TypeError):
        days_since = 999
        last_used_str = None

    # Recent success rate (last N uses for demotion checks)
    window_size = min(_PROTECTED_DEMOTION_WINDOW, total_uses)
    recent_events = events[-window_size:]
    recent_known = 0
    recent_success = 0.0
    for outcome, _ in recent_events:
        if outcome in ("success", "partial", "failure"):
            recent_known += 1
            if outcome == "success":
                recent_success += 1.0
            elif outcome == "partial":
                recent_success += 0.5

    recent_success_rate = recent_success / recent_known if recent_known > 0 else 0.5

    return {
        "total_uses": total_uses,
        "success_rate": round(success_rate, 4),
        "last_used_at": last_used_str,
        "days_since_last_use": days_since,
        "recent_success_rate": round(recent_success_rate, 4),
        "recent_window_size": window_size,
    }


def _get_memory_age_days(conn: sqlite3.Connection, memory_id: int) -> int:
    """Return age of memory in days since creation."""
    row = conn.execute(
        "SELECT created_at FROM memories WHERE id = ?",
        (memory_id,),
    ).fetchone()
    if row is None or row[0] is None:
        return 0
    try:
        created = datetime.fromisoformat(row[0])
        return (datetime.now() - created).days
    except (ValueError, TypeError):
        return 0


def _get_review_entry_date(conn: sqlite3.Connection, memory_id: int) -> Optional[str]:
    """Find when a memory entered the 'review' tier (most recent transition)."""
    row = conn.execute(
        "SELECT graduated_at FROM skill_graduation_log "
        "WHERE memory_id = ? AND new_tier = 'review' "
        "ORDER BY graduated_at DESC LIMIT 1",
        (memory_id,),
    ).fetchone()
    return row[0] if row else None


# ============================================================
# Core API
# ============================================================

def evaluate_memory(
    conn: sqlite3.Connection,
    memory_id: int,
) -> Dict[str, Any]:
    """Evaluate a single procedural memory for tier transition.

    Returns dict with: memory_id, current_tier, recommended_tier,
    reason, score_data, should_transition.
    """
    row = conn.execute(
        "SELECT id, kind, is_archived, skill_tier, summary, importance, created_at "
        "FROM memories WHERE id = ?",
        (memory_id,),
    ).fetchone()

    if row is None:
        raise ValueError(f"Memory {memory_id} not found")

    _, kind, is_archived, current_tier, summary, importance, created_at = row

    if kind != "procedural":
        raise ValueError(
            f"Memory {memory_id} has kind='{kind}', not 'procedural'."
        )

    if is_archived:
        return {
            "memory_id": memory_id,
            "summary": summary,
            "current_tier": current_tier,
            "recommended_tier": "archived",
            "reason": "memory is already archived",
            "should_transition": current_tier != "archived",
            "usage_stats": None,
        }

    usage = _get_usage_stats_for_memory(conn, memory_id)
    age_days = _get_memory_age_days(conn, memory_id)

    # Default: no change
    recommended_tier = current_tier
    reason = "no change needed"
    should_transition = False

    if current_tier is None or current_tier == "seed":
        # SEED -> ACTIVE: usage >= 3 AND success_rate >= 0.5
        if (
            usage["total_uses"] >= _SEED_TO_ACTIVE_USAGE
            and usage["success_rate"] >= _SEED_TO_ACTIVE_SUCCESS_RATE
        ):
            recommended_tier = "active"
            reason = (
                f"score {usage['success_rate']:.2f} >= {_SEED_TO_ACTIVE_SUCCESS_RATE}, "
                f"usage {usage['total_uses']} >= {_SEED_TO_ACTIVE_USAGE}"
            )
            should_transition = True
        elif usage["total_uses"] == 0 and current_tier is None:
            # Uninitialized procedural memory — stay as-is until backfill
            reason = "no tier assigned, awaiting backfill or first use"
        else:
            reason = (
                f"insufficient for promotion: "
                f"usage={usage['total_uses']}/{_SEED_TO_ACTIVE_USAGE}, "
                f"success_rate={usage['success_rate']:.2f}/{_SEED_TO_ACTIVE_SUCCESS_RATE}"
            )

    elif current_tier == "active":
        # Check demotion first: ACTIVE -> REVIEW
        if (
            usage["total_uses"] > 0
            and usage["days_since_last_use"] is not None
            and usage["days_since_last_use"] >= _ACTIVE_TO_REVIEW_IDLE_DAYS
        ):
            recommended_tier = "review"
            reason = (
                f"idle {usage['days_since_last_use']} days >= "
                f"{_ACTIVE_TO_REVIEW_IDLE_DAYS} day threshold"
            )
            should_transition = True
        elif (
            usage["total_uses"] >= _SEED_TO_ACTIVE_USAGE
            and usage["success_rate"] < _ACTIVE_TO_REVIEW_SUCCESS_RATE
        ):
            recommended_tier = "review"
            reason = (
                f"success_rate {usage['success_rate']:.2f} < "
                f"{_ACTIVE_TO_REVIEW_SUCCESS_RATE} with "
                f"{usage['total_uses']} uses"
            )
            should_transition = True
        elif (
            usage["total_uses"] == 0
            and age_days >= _ACTIVE_TO_REVIEW_IDLE_DAYS
        ):
            recommended_tier = "review"
            reason = (
                f"no usage after {age_days} days >= "
                f"{_ACTIVE_TO_REVIEW_IDLE_DAYS} day threshold"
            )
            should_transition = True
        # Check promotion: ACTIVE -> PROTECTED
        elif (
            usage["total_uses"] >= _ACTIVE_TO_PROTECTED_USAGE
            and usage["success_rate"] >= _ACTIVE_TO_PROTECTED_SUCCESS_RATE
            and age_days >= _ACTIVE_TO_PROTECTED_AGE_DAYS
        ):
            recommended_tier = "protected"
            reason = (
                f"score {usage['success_rate']:.2f} >= {_ACTIVE_TO_PROTECTED_SUCCESS_RATE}, "
                f"usage {usage['total_uses']} >= {_ACTIVE_TO_PROTECTED_USAGE}, "
                f"age {age_days} >= {_ACTIVE_TO_PROTECTED_AGE_DAYS} days"
            )
            should_transition = True
        else:
            reason = "active — maintaining current tier"

    elif current_tier == "protected":
        # PROTECTED -> ACTIVE (demotion): success_rate < 0.5 over last 10 uses
        if (
            usage["recent_window_size"] >= _PROTECTED_DEMOTION_WINDOW
            and usage["recent_success_rate"] < _PROTECTED_DEMOTION_SUCCESS_RATE
        ):
            recommended_tier = "active"
            reason = (
                f"recent success_rate {usage['recent_success_rate']:.2f} < "
                f"{_PROTECTED_DEMOTION_SUCCESS_RATE} over last "
                f"{usage['recent_window_size']} uses"
            )
            should_transition = True
        else:
            reason = "protected — tier is stable"

    elif current_tier == "review":
        # REVIEW -> ARCHIVED: no usage in 90 days after entering review
        review_entry = _get_review_entry_date(conn, memory_id)
        if review_entry:
            try:
                entry_dt = datetime.fromisoformat(review_entry)
                days_in_review = (datetime.now() - entry_dt).days
            except (ValueError, TypeError):
                days_in_review = 0
        else:
            days_in_review = age_days

        # Check if there's been any usage AFTER entering review
        usage_after_review = 0
        if review_entry:
            row = conn.execute(
                "SELECT COUNT(*) FROM skill_usage_log "
                "WHERE memory_id = ? AND used_at > ?",
                (memory_id, review_entry),
            ).fetchone()
            usage_after_review = row[0] if row else 0

        if days_in_review >= _REVIEW_TO_ARCHIVED_IDLE_DAYS and usage_after_review == 0:
            recommended_tier = "archived"
            reason = (
                f"no usage in {days_in_review} days since entering review "
                f"(>= {_REVIEW_TO_ARCHIVED_IDLE_DAYS} day threshold)"
            )
            should_transition = True
        elif usage_after_review > 0 and usage["success_rate"] >= _SEED_TO_ACTIVE_SUCCESS_RATE:
            # Redemption path: review -> active if usage resumes with decent success
            recommended_tier = "active"
            reason = (
                f"redeemed: {usage_after_review} uses since review entry, "
                f"success_rate {usage['success_rate']:.2f} >= {_SEED_TO_ACTIVE_SUCCESS_RATE}"
            )
            should_transition = True
        else:
            reason = f"in review for {days_in_review} days — awaiting archive threshold"

    elif current_tier == "archived":
        reason = "already archived — no further transitions"

    # Import and call L2-C5 compute_promotion_score for the score_data field.
    # This provides the weighted composite score for reporting.
    score_data = None
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import procedural_memory as _pm
        if usage["total_uses"] > 0:
            score_data = _pm.compute_promotion_score(conn, memory_id)
    except Exception:
        pass

    return {
        "memory_id": memory_id,
        "summary": summary,
        "importance": importance,
        "current_tier": current_tier,
        "recommended_tier": recommended_tier,
        "reason": reason,
        "should_transition": should_transition,
        "usage_stats": usage,
        "age_days": age_days,
        "score_data": score_data,
    }


def graduate_memory(
    conn: sqlite3.Connection,
    memory_id: int,
    new_tier: str,
    reason: str,
    score: Optional[float] = None,
    graduated_by: str = "cron",
) -> Dict[str, Any]:
    """Apply a tier change to a procedural memory with logging.

    Updates memories.skill_tier and inserts into skill_graduation_log.
    Returns the graduation record.

    Raises ValueError if:
    - memory_id doesn't exist
    - new_tier is invalid
    - graduated_by is invalid
    - memory is not procedural
    """
    if new_tier not in VALID_TIERS:
        raise ValueError(f"Invalid tier '{new_tier}'. Valid: {list(VALID_TIERS)}")

    if graduated_by not in VALID_GRADUATED_BY:
        raise ValueError(
            f"Invalid graduated_by '{graduated_by}'. Valid: {list(VALID_GRADUATED_BY)}"
        )

    row = conn.execute(
        "SELECT id, kind, skill_tier FROM memories WHERE id = ?",
        (memory_id,),
    ).fetchone()

    if row is None:
        raise ValueError(f"Memory {memory_id} not found")

    _, kind, old_tier = row

    if kind != "procedural":
        raise ValueError(
            f"Memory {memory_id} has kind='{kind}', not 'procedural'."
        )

    # Apply the tier change
    conn.execute(
        "UPDATE memories SET skill_tier = ?, updated_at = datetime('now') "
        "WHERE id = ?",
        (new_tier, memory_id),
    )

    # Log the graduation event
    cursor = conn.execute(
        "INSERT INTO skill_graduation_log "
        "(memory_id, old_tier, new_tier, reason, score, graduated_by) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (memory_id, old_tier, new_tier, reason, score, graduated_by),
    )
    log_id = cursor.lastrowid

    # If archiving, also set is_archived on the memory
    if new_tier == "archived":
        conn.execute(
            "UPDATE memories SET is_archived = 1, updated_at = datetime('now') "
            "WHERE id = ?",
            (memory_id,),
        )

    conn.commit()

    return {
        "log_id": log_id,
        "memory_id": memory_id,
        "old_tier": old_tier,
        "new_tier": new_tier,
        "reason": reason,
        "score": score,
        "graduated_by": graduated_by,
    }


def evaluate_all_procedural(
    conn: sqlite3.Connection,
    dry_run: bool = False,
    graduated_by: str = "cron",
) -> Dict[str, Any]:
    """Bulk evaluation of all procedural memories — the weekly cron entry point.

    Scans every non-archived procedural memory with a tier, evaluates it,
    and applies recommended transitions.

    Returns summary dict with counts and per-memory details.
    """
    t0 = time.time()

    rows = conn.execute(
        "SELECT id FROM memories "
        "WHERE kind = 'procedural' AND is_archived = 0 "
        "ORDER BY id"
    ).fetchall()

    total = len(rows)
    evaluated = 0
    transitioned = 0
    errors = 0
    transitions: List[Dict[str, Any]] = []
    error_details: List[Dict[str, Any]] = []

    for (memory_id,) in rows:
        try:
            result = evaluate_memory(conn, memory_id)
            evaluated += 1

            if result["should_transition"]:
                if not dry_run:
                    score = None
                    if result["score_data"] is not None:
                        score = result["score_data"].get("score")
                    grad_result = graduate_memory(
                        conn, memory_id,
                        new_tier=result["recommended_tier"],
                        reason=result["reason"],
                        score=score,
                        graduated_by=graduated_by,
                    )
                    transitions.append(grad_result)
                else:
                    transitions.append({
                        "memory_id": memory_id,
                        "old_tier": result["current_tier"],
                        "new_tier": result["recommended_tier"],
                        "reason": result["reason"],
                        "dry_run": True,
                    })
                transitioned += 1

        except Exception as e:
            errors += 1
            error_details.append({
                "memory_id": memory_id,
                "error": str(e),
            })

    return {
        "status": "ok",
        "action": "evaluate_all_procedural",
        "dry_run": dry_run,
        "total_procedural": total,
        "evaluated": evaluated,
        "transitioned": transitioned,
        "errors": errors,
        "transitions": transitions,
        "error_details": error_details if error_details else None,
        "time_ms": int((time.time() - t0) * 1000),
    }


def get_graduation_history(
    conn: sqlite3.Connection,
    memory_id: Optional[int] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Query the graduation log.

    If memory_id is provided, returns history for that specific memory.
    Otherwise returns the most recent graduation events globally.
    """
    if memory_id is not None:
        rows = conn.execute(
            "SELECT g.id, g.memory_id, g.old_tier, g.new_tier, g.reason, "
            "g.score, g.graduated_at, g.graduated_by, m.summary "
            "FROM skill_graduation_log g "
            "JOIN memories m ON m.id = g.memory_id "
            "WHERE g.memory_id = ? "
            "ORDER BY g.id DESC LIMIT ?",
            (memory_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT g.id, g.memory_id, g.old_tier, g.new_tier, g.reason, "
            "g.score, g.graduated_at, g.graduated_by, m.summary "
            "FROM skill_graduation_log g "
            "JOIN memories m ON m.id = g.memory_id "
            "ORDER BY g.id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    return [
        {
            "log_id": r[0],
            "memory_id": r[1],
            "old_tier": r[2],
            "new_tier": r[3],
            "reason": r[4],
            "score": r[5],
            "graduated_at": r[6],
            "graduated_by": r[7],
            "summary": r[8],
        }
        for r in rows
    ]


def backfill_tiers(
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """One-time backfill: assign tiers to existing procedural memories.

    Strategy:
    - Existing procedural memories (already in the library) -> 'active'
      (they're proven useful by virtue of being curated).
    - Non-procedural memories -> tier stays NULL (skill_tier only applies
      to procedurals).

    Only touches memories where skill_tier IS NULL AND kind = 'procedural'.
    Safe to re-run: already-tiered memories are skipped.
    """
    t0 = time.time()

    rows = conn.execute(
        "SELECT id, summary FROM memories "
        "WHERE kind = 'procedural' AND skill_tier IS NULL AND is_archived = 0"
    ).fetchall()

    total = len(rows)
    updated = 0
    transitions: List[Dict[str, Any]] = []

    for memory_id, summary in rows:
        if not dry_run:
            conn.execute(
                "UPDATE memories SET skill_tier = 'active', updated_at = datetime('now') "
                "WHERE id = ?",
                (memory_id,),
            )
            conn.execute(
                "INSERT INTO skill_graduation_log "
                "(memory_id, old_tier, new_tier, reason, graduated_by) "
                "VALUES (?, NULL, 'active', 'backfill: existing procedural memory', 'manual')",
                (memory_id,),
            )
            updated += 1
        transitions.append({
            "memory_id": memory_id,
            "summary": summary[:80] if summary else None,
            "old_tier": None,
            "new_tier": "active",
            "dry_run": dry_run,
        })

    if not dry_run:
        conn.commit()

    return {
        "status": "ok",
        "action": "backfill_tiers",
        "dry_run": dry_run,
        "total_null_procedural": total,
        "updated": updated if not dry_run else 0,
        "would_update": total if dry_run else 0,
        "transitions": transitions[:20],
        "time_ms": int((time.time() - t0) * 1000),
    }


def get_tier_stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Aggregate tier distribution stats for cmd_stats integration."""
    by_tier: Dict[str, int] = {}
    for row in conn.execute(
        "SELECT COALESCE(skill_tier, 'unassigned'), COUNT(*) "
        "FROM memories "
        "WHERE kind = 'procedural' AND is_archived = 0 "
        "GROUP BY skill_tier "
        "ORDER BY COUNT(*) DESC"
    ).fetchall():
        by_tier[row[0]] = row[1]

    total_graduations = conn.execute(
        "SELECT COUNT(*) FROM skill_graduation_log"
    ).fetchone()[0]

    recent_graduations = conn.execute(
        "SELECT COUNT(*) FROM skill_graduation_log "
        "WHERE graduated_at >= datetime('now', '-7 days')"
    ).fetchone()[0]

    promotions = conn.execute(
        "SELECT COUNT(*) FROM skill_graduation_log "
        "WHERE (old_tier = 'seed' AND new_tier = 'active') "
        "   OR (old_tier = 'active' AND new_tier = 'protected')"
    ).fetchone()[0]

    demotions = conn.execute(
        "SELECT COUNT(*) FROM skill_graduation_log "
        "WHERE (old_tier = 'protected' AND new_tier = 'active') "
        "   OR (old_tier = 'active' AND new_tier = 'review') "
        "   OR (old_tier = 'review' AND new_tier = 'archived')"
    ).fetchone()[0]

    return {
        "by_tier": by_tier,
        "total_graduations": total_graduations,
        "recent_graduations_7d": recent_graduations,
        "total_promotions": promotions,
        "total_demotions": demotions,
    }


# ============================================================
# Unit tests
# ============================================================

def unit_test_skill_graduation():
    """Self-contained unit tests. Uses an in-memory database."""
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
                msg += f" -- {detail}"
            print(msg)
            errors.append(msg)

    def _setup_db():
        """Create a fresh in-memory database with the required schema."""
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys=ON")

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
                confidence REAL DEFAULT 1.0,
                skill_tier TEXT DEFAULT NULL
                    CHECK(skill_tier IS NULL OR skill_tier IN
                    ('seed', 'active', 'protected', 'review', 'archived'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)

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

        conn.execute("""
            CREATE TABLE IF NOT EXISTS skill_graduation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                old_tier TEXT,
                new_tier TEXT NOT NULL
                    CHECK(new_tier IN ('seed', 'active', 'protected', 'review', 'archived')),
                reason TEXT NOT NULL,
                score REAL,
                graduated_at TEXT NOT NULL DEFAULT (datetime('now')),
                graduated_by TEXT DEFAULT 'cron'
                    CHECK(graduated_by IN ('cron', 'manual', 'curator'))
            )
        """)

        return conn

    def _insert_memory(conn, kind="procedural", category="skill_lesson",
                       summary="Test memory", importance=0.5, is_archived=0,
                       created_at=None, skill_tier=None):
        if created_at is None:
            created_at = datetime.now().isoformat()
        cursor = conn.execute(
            """INSERT INTO memories
               (content, summary, category, importance, kind, is_archived,
                created_at, skill_tier)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"Content for {summary}", summary, category, importance,
             kind, is_archived, created_at, skill_tier),
        )
        conn.commit()
        return cursor.lastrowid

    def _log_usage(conn, memory_id, outcome="success", used_at=None):
        if used_at is None:
            used_at = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO skill_usage_log (memory_id, outcome, used_at) "
            "VALUES (?, ?, ?)",
            (memory_id, outcome, used_at),
        )
        conn.commit()

    print("\n=== Unit Tests: skill_graduation.py ===\n")

    # --- Test 1: SEED -> ACTIVE promotion ---
    print("[T1] evaluate_memory — SEED -> ACTIVE promotion")
    conn = _setup_db()
    mid = _insert_memory(conn, skill_tier="seed", summary="New pattern")
    for _ in range(3):
        _log_usage(conn, mid, outcome="success")
    result = evaluate_memory(conn, mid)
    _assert(result["should_transition"], "should_transition is True")
    _assert(result["recommended_tier"] == "active", f"recommended=active (got {result['recommended_tier']})")
    _assert("score" in result["reason"], "reason mentions score")
    conn.close()

    # --- Test 2: SEED stays SEED with insufficient usage ---
    print("\n[T2] evaluate_memory — SEED stays SEED with 2 uses")
    conn = _setup_db()
    mid = _insert_memory(conn, skill_tier="seed", summary="Young pattern")
    for _ in range(2):
        _log_usage(conn, mid, outcome="success")
    result = evaluate_memory(conn, mid)
    _assert(not result["should_transition"], "should_transition is False")
    _assert(result["recommended_tier"] == "seed", f"stays seed (got {result['recommended_tier']})")
    conn.close()

    # --- Test 3: SEED stays SEED with low success rate ---
    print("\n[T3] evaluate_memory — SEED stays SEED with low success rate")
    conn = _setup_db()
    mid = _insert_memory(conn, skill_tier="seed", summary="Weak pattern")
    _log_usage(conn, mid, outcome="success")
    _log_usage(conn, mid, outcome="failure")
    _log_usage(conn, mid, outcome="failure")
    _log_usage(conn, mid, outcome="failure")
    result = evaluate_memory(conn, mid)
    _assert(not result["should_transition"], "should_transition is False")
    conn.close()

    # --- Test 4: ACTIVE -> PROTECTED promotion ---
    print("\n[T4] evaluate_memory — ACTIVE -> PROTECTED promotion")
    conn = _setup_db()
    old_date = (datetime.now() - timedelta(days=45)).isoformat()
    mid = _insert_memory(conn, skill_tier="active", summary="Proven pattern",
                         created_at=old_date)
    for _ in range(12):
        _log_usage(conn, mid, outcome="success")
    result = evaluate_memory(conn, mid)
    _assert(result["should_transition"], "should_transition is True")
    _assert(result["recommended_tier"] == "protected", f"recommended=protected (got {result['recommended_tier']})")
    conn.close()

    # --- Test 5: ACTIVE stays ACTIVE (not enough uses for PROTECTED) ---
    print("\n[T5] evaluate_memory — ACTIVE stays ACTIVE with 5 uses")
    conn = _setup_db()
    old_date = (datetime.now() - timedelta(days=45)).isoformat()
    mid = _insert_memory(conn, skill_tier="active", summary="Growing pattern",
                         created_at=old_date)
    for _ in range(5):
        _log_usage(conn, mid, outcome="success")
    result = evaluate_memory(conn, mid)
    _assert(not result["should_transition"], "should_transition is False")
    _assert(result["recommended_tier"] == "active", f"stays active (got {result['recommended_tier']})")
    conn.close()

    # --- Test 6: ACTIVE -> REVIEW (idle) ---
    print("\n[T6] evaluate_memory — ACTIVE -> REVIEW after 60 days idle")
    conn = _setup_db()
    old_usage_date = (datetime.now() - timedelta(days=70)).isoformat()
    mid = _insert_memory(conn, skill_tier="active", summary="Forgotten pattern")
    _log_usage(conn, mid, outcome="success", used_at=old_usage_date)
    result = evaluate_memory(conn, mid)
    _assert(result["should_transition"], "should_transition is True")
    _assert(result["recommended_tier"] == "review", f"recommended=review (got {result['recommended_tier']})")
    _assert("idle" in result["reason"], "reason mentions idle")
    conn.close()

    # --- Test 7: ACTIVE -> REVIEW (low success rate) ---
    print("\n[T7] evaluate_memory — ACTIVE -> REVIEW with success_rate < 0.3")
    conn = _setup_db()
    mid = _insert_memory(conn, skill_tier="active", summary="Failing pattern")
    _log_usage(conn, mid, outcome="failure")
    _log_usage(conn, mid, outcome="failure")
    _log_usage(conn, mid, outcome="failure")
    _log_usage(conn, mid, outcome="success")  # rate = 1/4 = 0.25
    result = evaluate_memory(conn, mid)
    _assert(result["should_transition"], "should_transition is True")
    _assert(result["recommended_tier"] == "review", f"recommended=review (got {result['recommended_tier']})")
    conn.close()

    # --- Test 8: PROTECTED -> ACTIVE (demotion) ---
    print("\n[T8] evaluate_memory — PROTECTED -> ACTIVE demotion on recent failures")
    conn = _setup_db()
    mid = _insert_memory(conn, skill_tier="protected", summary="Degrading pattern")
    # 10 old successes followed by 10 recent failures
    old_date = (datetime.now() - timedelta(days=100)).isoformat()
    for i in range(10):
        _log_usage(conn, mid, outcome="success", used_at=old_date)
    for _ in range(10):
        _log_usage(conn, mid, outcome="failure")
    result = evaluate_memory(conn, mid)
    _assert(result["should_transition"], "should_transition is True")
    _assert(result["recommended_tier"] == "active", f"recommended=active (got {result['recommended_tier']})")
    _assert("recent" in result["reason"], "reason mentions recent")
    conn.close()

    # --- Test 9: PROTECTED stays PROTECTED with good recent record ---
    print("\n[T9] evaluate_memory — PROTECTED stays PROTECTED with good recent uses")
    conn = _setup_db()
    mid = _insert_memory(conn, skill_tier="protected", summary="Solid pattern")
    for _ in range(15):
        _log_usage(conn, mid, outcome="success")
    result = evaluate_memory(conn, mid)
    _assert(not result["should_transition"], "should_transition is False")
    _assert(result["recommended_tier"] == "protected", f"stays protected (got {result['recommended_tier']})")
    conn.close()

    # --- Test 10: REVIEW -> ARCHIVED ---
    print("\n[T10] evaluate_memory — REVIEW -> ARCHIVED after 90 days")
    conn = _setup_db()
    review_entry_date = (datetime.now() - timedelta(days=95)).isoformat()
    mid = _insert_memory(conn, skill_tier="review", summary="Stale pattern")
    # Insert a graduation log entry marking when it entered review
    conn.execute(
        "INSERT INTO skill_graduation_log "
        "(memory_id, old_tier, new_tier, reason, graduated_at) "
        "VALUES (?, 'active', 'review', 'test entry', ?)",
        (mid, review_entry_date),
    )
    conn.commit()
    result = evaluate_memory(conn, mid)
    _assert(result["should_transition"], "should_transition is True")
    _assert(result["recommended_tier"] == "archived", f"recommended=archived (got {result['recommended_tier']})")
    conn.close()

    # --- Test 11: REVIEW -> ACTIVE (redemption) ---
    print("\n[T11] evaluate_memory — REVIEW -> ACTIVE redemption path")
    conn = _setup_db()
    review_date = (datetime.now() - timedelta(days=10)).isoformat()
    mid = _insert_memory(conn, skill_tier="review", summary="Redeemed pattern")
    conn.execute(
        "INSERT INTO skill_graduation_log "
        "(memory_id, old_tier, new_tier, reason, graduated_at) "
        "VALUES (?, 'active', 'review', 'test entry', ?)",
        (mid, review_date),
    )
    conn.commit()
    # Usage after review entry — shows the pattern is being used again
    _log_usage(conn, mid, outcome="success")
    _log_usage(conn, mid, outcome="success")
    result = evaluate_memory(conn, mid)
    _assert(result["should_transition"], "should_transition is True")
    _assert(result["recommended_tier"] == "active", f"recommended=active (got {result['recommended_tier']})")
    _assert("redeemed" in result["reason"], "reason mentions redemption")
    conn.close()

    # --- Test 12: graduate_memory — applies tier change ---
    print("\n[T12] graduate_memory — applies tier change and logs it")
    conn = _setup_db()
    mid = _insert_memory(conn, skill_tier="seed", summary="Graduating pattern")
    result = graduate_memory(conn, mid, "active", "test promotion", score=0.85, graduated_by="manual")
    _assert(result["old_tier"] == "seed", f"old_tier=seed (got {result['old_tier']})")
    _assert(result["new_tier"] == "active", f"new_tier=active (got {result['new_tier']})")
    _assert(result["log_id"] > 0, "log_id is positive")
    # Verify DB state
    row = conn.execute("SELECT skill_tier FROM memories WHERE id = ?", (mid,)).fetchone()
    _assert(row[0] == "active", f"DB tier updated to active (got {row[0]})")
    log_row = conn.execute(
        "SELECT old_tier, new_tier, reason FROM skill_graduation_log WHERE memory_id = ?",
        (mid,),
    ).fetchone()
    _assert(log_row[0] == "seed", "log old_tier is seed")
    _assert(log_row[1] == "active", "log new_tier is active")
    _assert(log_row[2] == "test promotion", "log reason matches")
    conn.close()

    # --- Test 13: graduate_memory — archive sets is_archived ---
    print("\n[T13] graduate_memory — archive sets is_archived = 1")
    conn = _setup_db()
    mid = _insert_memory(conn, skill_tier="review", summary="Archiving pattern")
    graduate_memory(conn, mid, "archived", "test archival")
    row = conn.execute(
        "SELECT skill_tier, is_archived FROM memories WHERE id = ?", (mid,)
    ).fetchone()
    _assert(row[0] == "archived", f"tier=archived (got {row[0]})")
    _assert(row[1] == 1, f"is_archived=1 (got {row[1]})")
    conn.close()

    # --- Test 14: graduate_memory — rejects non-procedural ---
    print("\n[T14] graduate_memory — rejects non-procedural memories")
    conn = _setup_db()
    mid = _insert_memory(conn, kind="semantic", summary="Not a procedure")
    try:
        graduate_memory(conn, mid, "active", "should fail")
        _assert(False, "should reject semantic", "no exception raised")
    except ValueError as e:
        _assert("not 'procedural'" in str(e), "rejects with correct message")
    conn.close()

    # --- Test 15: graduate_memory — rejects invalid tier ---
    print("\n[T15] graduate_memory — rejects invalid tier")
    conn = _setup_db()
    mid = _insert_memory(conn, skill_tier="seed", summary="Test")
    try:
        graduate_memory(conn, mid, "invalid_tier", "bad tier")
        _assert(False, "should reject invalid tier", "no exception raised")
    except ValueError as e:
        _assert("Invalid tier" in str(e), "rejects with correct message")
    conn.close()

    # --- Test 16: evaluate_all_procedural — bulk evaluation ---
    print("\n[T16] evaluate_all_procedural — bulk evaluation")
    conn = _setup_db()
    mid1 = _insert_memory(conn, skill_tier="seed", summary="Ready to promote")
    for _ in range(4):
        _log_usage(conn, mid1, outcome="success")
    mid2 = _insert_memory(conn, skill_tier="active", summary="Stable")
    for _ in range(3):
        _log_usage(conn, mid2, outcome="success")
    mid3 = _insert_memory(conn, kind="semantic", summary="Not procedural")
    result = evaluate_all_procedural(conn, dry_run=True)
    _assert(result["total_procedural"] == 2, f"total=2 (got {result['total_procedural']})")
    _assert(result["evaluated"] == 2, f"evaluated=2 (got {result['evaluated']})")
    _assert(result["transitioned"] >= 1, f"at least 1 transition (got {result['transitioned']})")
    _assert(result["dry_run"] is True, "dry_run flag set")
    conn.close()

    # --- Test 17: evaluate_all_procedural — applies changes when not dry_run ---
    print("\n[T17] evaluate_all_procedural — applies changes when not dry_run")
    conn = _setup_db()
    mid = _insert_memory(conn, skill_tier="seed", summary="Promote me")
    for _ in range(5):
        _log_usage(conn, mid, outcome="success")
    result = evaluate_all_procedural(conn, dry_run=False)
    _assert(result["transitioned"] >= 1, "at least 1 transition applied")
    row = conn.execute("SELECT skill_tier FROM memories WHERE id = ?", (mid,)).fetchone()
    _assert(row[0] == "active", f"tier updated to active (got {row[0]})")
    conn.close()

    # --- Test 18: backfill_tiers ---
    print("\n[T18] backfill_tiers — assigns 'active' to existing procedurals")
    conn = _setup_db()
    mid1 = _insert_memory(conn, kind="procedural", summary="Existing proc 1")
    mid2 = _insert_memory(conn, kind="procedural", summary="Existing proc 2")
    mid3 = _insert_memory(conn, kind="semantic", summary="Not procedural")
    mid4 = _insert_memory(conn, kind="procedural", summary="Already tiered",
                          skill_tier="protected")
    result = backfill_tiers(conn, dry_run=False)
    _assert(result["total_null_procedural"] == 2, f"2 null procedurals (got {result['total_null_procedural']})")
    _assert(result["updated"] == 2, f"2 updated (got {result['updated']})")
    row1 = conn.execute("SELECT skill_tier FROM memories WHERE id = ?", (mid1,)).fetchone()
    row4 = conn.execute("SELECT skill_tier FROM memories WHERE id = ?", (mid4,)).fetchone()
    _assert(row1[0] == "active", f"proc1 now active (got {row1[0]})")
    _assert(row4[0] == "protected", f"proc4 still protected (got {row4[0]})")
    # Verify graduation log entries
    log_count = conn.execute(
        "SELECT COUNT(*) FROM skill_graduation_log WHERE reason LIKE 'backfill%'"
    ).fetchone()[0]
    _assert(log_count == 2, f"2 backfill log entries (got {log_count})")
    conn.close()

    # --- Test 19: get_graduation_history ---
    print("\n[T19] get_graduation_history — returns log entries")
    conn = _setup_db()
    mid = _insert_memory(conn, skill_tier="seed", summary="History test")
    graduate_memory(conn, mid, "active", "first promotion", score=0.75)
    graduate_memory(conn, mid, "protected", "second promotion", score=0.92)
    history = get_graduation_history(conn, memory_id=mid)
    _assert(len(history) == 2, f"2 history entries (got {len(history)})")
    _assert(history[0]["new_tier"] == "protected", "most recent first")
    _assert(history[1]["new_tier"] == "active", "older entry second")
    conn.close()

    # --- Test 20: get_tier_stats ---
    print("\n[T20] get_tier_stats — aggregate tier distribution")
    conn = _setup_db()
    _insert_memory(conn, skill_tier="seed", summary="Seed 1")
    _insert_memory(conn, skill_tier="seed", summary="Seed 2")
    _insert_memory(conn, skill_tier="active", summary="Active 1")
    _insert_memory(conn, skill_tier="protected", summary="Protected 1")
    _insert_memory(conn, kind="semantic", summary="Not procedural")
    stats = get_tier_stats(conn)
    _assert(stats["by_tier"].get("seed", 0) == 2, f"2 seeds (got {stats['by_tier'].get('seed', 0)})")
    _assert(stats["by_tier"].get("active", 0) == 1, f"1 active (got {stats['by_tier'].get('active', 0)})")
    _assert(stats["by_tier"].get("protected", 0) == 1, f"1 protected (got {stats['by_tier'].get('protected', 0)})")
    conn.close()

    # --- Test 21: is_graduation_migration_applied ---
    print("\n[T21] is_graduation_migration_applied")
    conn = _setup_db()
    _assert(is_graduation_migration_applied(conn), "migration detected in test DB")
    conn2 = sqlite3.connect(":memory:")
    conn2.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY)")
    _assert(not is_graduation_migration_applied(conn2), "migration NOT detected in bare DB")
    conn.close()
    conn2.close()

    # --- Test 22: ACTIVE -> REVIEW (no usage, old enough) ---
    print("\n[T22] evaluate_memory — ACTIVE -> REVIEW with zero usage and age >= 60 days")
    conn = _setup_db()
    old_date = (datetime.now() - timedelta(days=65)).isoformat()
    mid = _insert_memory(conn, skill_tier="active", summary="Never used active",
                         created_at=old_date)
    result = evaluate_memory(conn, mid)
    _assert(result["should_transition"], "should_transition is True")
    _assert(result["recommended_tier"] == "review", f"recommended=review (got {result['recommended_tier']})")
    conn.close()

    # --- Test 23: ACTIVE -> PROTECTED blocked by age < 30 days ---
    print("\n[T23] evaluate_memory — ACTIVE -> PROTECTED blocked by young age")
    conn = _setup_db()
    recent_date = (datetime.now() - timedelta(days=5)).isoformat()
    mid = _insert_memory(conn, skill_tier="active", summary="Young high-performer",
                         created_at=recent_date)
    for _ in range(15):
        _log_usage(conn, mid, outcome="success")
    result = evaluate_memory(conn, mid)
    _assert(not result["should_transition"], "should_transition is False")
    _assert(result["recommended_tier"] == "active", f"stays active (got {result['recommended_tier']})")
    conn.close()

    # --- Test 24: CASCADE delete removes graduation log ---
    print("\n[T24] CASCADE delete — graduation logs removed when memory deleted")
    conn = _setup_db()
    mid = _insert_memory(conn, skill_tier="seed", summary="Delete me")
    graduate_memory(conn, mid, "active", "test")
    count_before = conn.execute(
        "SELECT COUNT(*) FROM skill_graduation_log"
    ).fetchone()[0]
    _assert(count_before >= 1, f"at least 1 log before delete (got {count_before})")
    conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
    conn.commit()
    count_after = conn.execute(
        "SELECT COUNT(*) FROM skill_graduation_log"
    ).fetchone()[0]
    _assert(count_after == 0, f"0 logs after cascade delete (got {count_after})")
    conn.close()

    # --- Test 25: evaluate_memory rejects non-procedural ---
    print("\n[T25] evaluate_memory — rejects non-procedural memories")
    conn = _setup_db()
    mid = _insert_memory(conn, kind="episodic", summary="Not a procedure")
    try:
        evaluate_memory(conn, mid)
        _assert(False, "should reject episodic", "no exception raised")
    except ValueError as e:
        _assert("not 'procedural'" in str(e), "rejects episodic with correct message")
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
    parser = argparse.ArgumentParser(
        description="Skill Graduation Pipeline — Wizard Library L3-C6",
    )
    parser.add_argument(
        "db_path", nargs="?",
        default=str(Path(__file__).parent.parent / "claude_memory.db"),
        help="Path to claude_memory.db",
    )
    parser.add_argument(
        "--run", action="store_true",
        help="Run full graduation evaluation cycle (weekly cron entry point)",
    )
    parser.add_argument(
        "--graduated-by", default="cron",
        help="Source attribution for graduations (default: cron)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Evaluate without applying transitions",
    )
    parser.add_argument(
        "--unit-tests", action="store_true",
        help="Run unit tests",
    )
    parser.add_argument(
        "--apply-migration", metavar="MIGRATION_SQL",
        help="Apply graduation migration from SQL file",
    )

    args = parser.parse_args()

    if args.unit_tests:
        result = unit_test_skill_graduation()
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["failed"] == 0 else 1)

    if args.apply_migration:
        result = apply_graduation_migration(args.db_path, args.apply_migration)
        print(json.dumps(result))
        sys.exit(0 if result["status"] in ("ok", "already-applied") else 1)

    if args.run:
        conn = sqlite3.connect(args.db_path, timeout=60.0)
        conn.row_factory = sqlite3.Row
        try:
            # CRUCIBLE-3 D04-F3: ON THE BRAIN THE INSTALLER BUILDS, `memories.skill_tier`
            # DOES NOT EXIST -- and this verb ran anyway and printed
            # `{"transitioned": 0, "errors": 0}`, rc 0, over a schema it cannot use.
            # Measured: apply every packaged migration to the shipped blank brain and
            # `schema_migrator audit` reports `missing: 0` (correctly -- migration 007
            # says in its own text that the ALTER is "executed programmatically"), and
            # the column is still absent. So the organ's nightly unit reported success
            # forever while being structurally incapable of graduating anything.
            #
            # THE APPLIER ALREADY EXISTS, one branch above: `--apply-migration`. It was
            # simply not on this path -- the fifth time this cycle the correct code was
            # present and unreached.
            #
            # This REFUSES rather than auto-applying. An ALTER on the user's `memories`
            # table inside an unattended cron run is a schema change nobody asked for at
            # 08:30; naming the one command that performs it is the honest move, and a
            # refusal a human can act on beats a silent zero they cannot see.
            if not is_graduation_migration_applied(conn):
                _mig = (pathlib.Path(__file__).resolve().parent.parent
                        / "migrations" / "migration_007_skill_graduation.sql")
                print(json.dumps({
                    "status": "refused",
                    "code": "graduation_schema_absent",
                    "message": (
                        "memories.skill_tier and/or skill_graduation_log are absent from "
                        "this brain, so NOTHING could be graduated and nothing was "
                        "examined. This is UNKNOWN, not zero: a previous run printing "
                        "'transitioned: 0, errors: 0' over this state said the opposite."),
                    "remedy": (f"python3 {pathlib.Path(__file__).name} {args.db_path} "
                               f"--apply-migration {_mig}"),
                    "migration_file_present": _mig.exists(),
                }, indent=2))
                sys.exit(3)
            result = evaluate_all_procedural(
                conn,
                dry_run=args.dry_run,
                graduated_by=args.graduated_by,
            )
            print(json.dumps(result, indent=2, default=str))
            # CRUCIBLE-3 D04-F4: this read `status` and never `errors`, so a run
            # that reported errors:30 still exited 0 -- and its only caller is a
            # systemd `Type=oneshot` unit, where the exit code IS the success
            # signal. A run that did nothing, or failed 30 times, was recorded as
            # a success. Both fields are read now, and the failing one is named on
            # stderr so the journal says WHICH condition fired.
            _st = result.get("status", "ok")
            _errs = result.get("errors")
            _n_err = (len(_errs) if isinstance(_errs, (list, tuple))
                      else int(_errs) if isinstance(_errs, int) else 0)
            if _st == "error" or _n_err:
                sys.stderr.write(
                    f"skill_graduation FAILED: status={_st!r}, errors={_n_err}\n")
                sys.exit(1)
            sys.exit(0)
        finally:
            conn.close()

    parser.print_help()
    sys.exit(1)
