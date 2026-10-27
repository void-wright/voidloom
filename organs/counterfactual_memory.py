"""Counterfactual Memory — Wizard Library L4 Piece 5.

At significant decision points, the system snapshots what was considered
but NOT chosen — the paths not taken. The Council of Mages (Piece 3,
council.py) provides a decision_weight_score that determines which
decisions are worth tracking. Quarterly, the system revisits old
counterfactuals: "was that the right call? Has the world changed?"

Public API
----------
``capture_counterfactual(conn, decision_memory_id, topic, chosen_path,
                         alternative_paths, ...) -> dict``
    Store a new counterfactual decision record.

``revisit_counterfactual(conn, counterfactual_id, outcome, notes, ...) -> dict``
    Record a revisit outcome for a counterfactual. Updates status lifecycle.

``get_pending_revisits(conn, as_of=None, limit=50) -> dict``
    Return counterfactuals due for revisit (active/revisited + interval elapsed).

``compute_decision_weight(conn, reversibility, scope, urgency,
                          council_session_id=None) -> dict``
    Compute a 3-dimension composite weight score. Optionally updates the
    council session. Returns the weight for counterfactual capture gating.

``get_counterfactual_history(conn, limit=20, status=None,
                             min_weight=None) -> dict``
    List counterfactual decisions with filters.

``get_counterfactual(conn, counterfactual_id) -> dict``
    Retrieve a single counterfactual with full revisit history.

``archive_counterfactual(conn, counterfactual_id, reason=None) -> dict``
    Move a counterfactual to 'archived' status.

``auto_capture_from_council(conn, session_id, chosen_path,
                            alternative_paths, ...) -> dict``
    Convenience: capture a counterfactual directly from a completed
    council session, pulling topic and weight automatically.

``get_counterfactual_stats(conn) -> dict``
    Aggregate statistics for the counterfactual table.

``ensure_counterfactual_table(db_path) -> dict``
    Apply migration_019 if counterfactual_decisions table does not exist.

``is_counterfactual_migration_applied(conn) -> bool``
    Check if migration 019 has been applied.

``apply_counterfactual_migration(db_path, migration_sql_path) -> dict``
    Idempotent migration runner.

``unit_test_counterfactual_memory()``
    Self-contained unit tests.

Companion module pattern: follows council.py and calibration_memory.py.
  - Standalone: importable, all functions accept conn arguments
  - Integrated: claude_memory.py imports and wires commands to argparse
  - Testable: unit tests use temp databases, no globals

Phase 0 memories: council (Piece 3), calibration (Piece 4), gap-mapping (Piece 6).
Council design note: decision_weight_score feeds directly into this module
from migration_018 field council_sessions.decision_weight_score.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIGRATION_FILENAME = "migration_019_counterfactual.sql"

VALID_STATUSES = frozenset({
    "active", "revisited", "validated", "reversed", "archived",
})

VALID_CAPTURED_BY = frozenset({
    "manual", "council_auto", "cron_revisit", "cmd_store",
})

VALID_REVISIT_OUTCOMES = frozenset({
    "validated",      # right call, no change needed
    "still_active",   # too early to tell, keep watching
    "reversed",       # wrong call, course-correcting
    "partially_wrong", # some aspects wrong, adjusting
    "context_changed", # world changed, decision needs reassessment
})

# Default threshold: only auto-capture decisions with weight >= this value.
# Below this, the decision is too trivial for counterfactual tracking.
DEFAULT_WEIGHT_THRESHOLD = 0.4

# Quarterly revisit default (days).
DEFAULT_REVISIT_INTERVAL = 90

# Weight formula coefficients — irreversibility matters most.
WEIGHT_COEFF_REVERSIBILITY = 0.5
WEIGHT_COEFF_SCOPE = 0.3
WEIGHT_COEFF_URGENCY = 0.2

# No token and no vendor binary here: the thinker seam resolves both from the owner's record.

# --- THE THINKER SEAM (model-choice unit, Round 1) --------------------------------------------------------------
# This organ builds a prompt and parses an answer; WHO answers is the owner's recorded choice (<brain>/THINKER_RECORD,
# written only by organs/thinker.py after a probe in the timers' environment), and HOW the call is fenced -- text only,
# no tools, no ambient instructions, a fresh empty cwd, stdin closed, a hard timeout -- is the catalog's
# (organs/thinkers.json). No vendor argv, token or output format lives in this file any more.
try:
    from . import thinker as _thinker  # type: ignore
except (ImportError, ValueError):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import thinker as _thinker  # type: ignore
# --- end the thinker seam -----------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------

def is_counterfactual_migration_applied(conn: sqlite3.Connection) -> bool:
    """Check if the counterfactual_decisions table exists."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    return "counterfactual_decisions" in tables


def apply_counterfactual_migration(
    db_path: str,
    migration_sql_path: Optional[str] = None,
) -> dict:
    """Apply migration_019 if not already applied. Idempotent."""
    conn = _connect_busy(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        if is_counterfactual_migration_applied(conn):
            return {"status": "ok", "action": "apply_migration", "result": "already_applied"}

        if migration_sql_path is None:
            migration_sql_path = str(Path(__file__).parent.parent / "migrations" / MIGRATION_FILENAME)

        sql_path = Path(migration_sql_path)
        if not sql_path.exists():
            return {"status": "error", "message": f"{MIGRATION_FILENAME} not found at {sql_path}"}

        sql = sql_path.read_text()
        conn.executescript(sql)
        conn.commit()
        return {"status": "ok", "action": "apply_migration", "result": "applied"}
    finally:
        conn.close()


def ensure_counterfactual_table(db_path: str) -> dict:
    """Ensure the counterfactual_decisions table exists. Idempotent."""
    return apply_counterfactual_migration(db_path)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

# Narrow pass N5(b) (model-choice; the host, Oct 1 08:54 EDT, OLD code): `--revisit` died with
# `sqlite3.OperationalError: database is locked` at its revisit UPDATE, fired in the same second as the self-eval's
# catch-up after the host woke -- a concurrent writer held the brain's write lock longer than Python's DEFAULT busy
# wait (5 s). The brain is opened the way gap_mapping.py opens it for the same reason (its docstring: the start-up storm
# of overdue Persistent=true timers; two recorded deaths on the 5 s default): the busy handler waits up to 60 s.
# `timeout=` IS that handler; the PRAGMA restates it so a copy of this call that drops the keyword keeps the intent.
BUSY_TIMEOUT_S = 60.0


def _connect_busy(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_S)
    conn.execute("PRAGMA busy_timeout=%d" % int(BUSY_TIMEOUT_S * 1000))
    return conn


def _get_conn(db_path: str) -> sqlite3.Connection:
    """Open a connection with WAL mode and foreign keys enabled (and a 60 s busy wait: N5(b) above)."""
    conn = _connect_busy(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _now_utc() -> str:
    """Return current UTC datetime as ISO string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _validate_alternative_paths(paths: Any) -> Tuple[bool, str]:
    """Validate the alternative_paths structure.

    Must be a list of dicts, each with 'path' key at minimum.
    """
    if isinstance(paths, str):
        try:
            paths = json.loads(paths)
        except json.JSONDecodeError:
            return False, "alternative_paths is not valid JSON"

    if not isinstance(paths, list):
        return False, "alternative_paths must be a JSON array"

    if len(paths) == 0:
        return False, "alternative_paths must have at least one alternative"

    for i, alt in enumerate(paths):
        if not isinstance(alt, dict):
            return False, f"alternative_paths[{i}] must be a dict"
        if "path" not in alt:
            return False, f"alternative_paths[{i}] missing required 'path' key"

    return True, ""


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def capture_counterfactual(
    conn: sqlite3.Connection,
    decision_memory_id: int,
    topic: str,
    chosen_path: str,
    alternative_paths: Any,
    council_weight_score: Optional[float] = None,
    council_session_id: Optional[int] = None,
    criteria_for_revisit: Optional[str] = None,
    revisit_interval_days: int = DEFAULT_REVISIT_INTERVAL,
    captured_by: str = "manual",
) -> dict:
    """Capture a new counterfactual decision.

    Args:
        conn: Database connection.
        decision_memory_id: The memory ID recording this decision.
        topic: Human-readable description of the decision.
        chosen_path: What was actually chosen.
        alternative_paths: JSON array of {path, predicted_outcome, rationale}.
            Can be a list of dicts or a JSON string.
        council_weight_score: 0.0-1.0 decision weight. Optional.
        council_session_id: Link to council session. Optional.
        criteria_for_revisit: When to revisit this decision. Optional.
        revisit_interval_days: Days between revisits. Default 90.
        captured_by: Provenance tag. Default 'manual'.

    Returns:
        Dict with counterfactual_id and metadata.
    """
    # Validate alternative_paths
    valid, err = _validate_alternative_paths(alternative_paths)
    if not valid:
        return {"status": "error", "message": err}

    # Normalize to JSON string
    if isinstance(alternative_paths, list):
        alt_json = json.dumps(alternative_paths)
    else:
        alt_json = alternative_paths

    # Validate weight
    if council_weight_score is not None:
        if not (0.0 <= council_weight_score <= 1.0):
            return {"status": "error", "message": "council_weight_score must be 0.0-1.0"}

    # Validate captured_by
    if captured_by not in VALID_CAPTURED_BY:
        return {"status": "error", "message": f"Invalid captured_by: {captured_by}. Valid: {sorted(VALID_CAPTURED_BY)}"}

    # Validate revisit_interval_days
    if revisit_interval_days < 1:
        return {"status": "error", "message": "revisit_interval_days must be >= 1"}

    # Verify the decision memory exists
    mem_row = conn.execute(
        "SELECT id FROM memories WHERE id = ? AND is_archived = 0",
        (decision_memory_id,),
    ).fetchone()
    if not mem_row:
        return {"status": "error", "message": f"Memory {decision_memory_id} not found or archived"}

    # If council_session_id given, verify it exists
    if council_session_id is not None:
        sess_row = conn.execute(
            "SELECT id, decision_weight_score FROM council_sessions WHERE id = ?",
            (council_session_id,),
        ).fetchone()
        if not sess_row:
            return {"status": "error", "message": f"Council session {council_session_id} not found"}
        # Auto-pull weight from council if not explicitly provided
        if council_weight_score is None and sess_row["decision_weight_score"] is not None:
            council_weight_score = sess_row["decision_weight_score"]

    now = _now_utc()

    cursor = conn.execute(
        """INSERT INTO counterfactual_decisions
           (decision_memory_id, council_session_id, topic, chosen_path,
            alternative_paths, council_weight_score, criteria_for_revisit,
            revisit_interval_days, captured_at, captured_by, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
        (decision_memory_id, council_session_id, topic, chosen_path,
         alt_json, council_weight_score, criteria_for_revisit,
         revisit_interval_days, now, captured_by),
    )
    counterfactual_id = cursor.lastrowid
    conn.commit()

    return {
        "status": "ok",
        "action": "capture_counterfactual",
        "counterfactual_id": counterfactual_id,
        "decision_memory_id": decision_memory_id,
        "council_session_id": council_session_id,
        "topic": topic,
        "council_weight_score": council_weight_score,
        "revisit_interval_days": revisit_interval_days,
        "captured_by": captured_by,
        "captured_at": now,
    }


def revisit_counterfactual(
    conn: sqlite3.Connection,
    counterfactual_id: int,
    outcome: str,
    notes: str = "",
    revisited_by: str = "manual",
) -> dict:
    """Record a revisit outcome for a counterfactual.

    Updates the status lifecycle based on outcome:
      - validated -> status = 'validated' (terminal)
      - reversed -> status = 'reversed' (terminal)
      - still_active -> status = 'revisited' (stays in rotation)
      - partially_wrong -> status = 'revisited' (stays in rotation)
      - context_changed -> status = 'revisited' (stays in rotation)

    Args:
        conn: Database connection.
        counterfactual_id: The counterfactual to revisit.
        outcome: One of the VALID_REVISIT_OUTCOMES.
        notes: Free-text notes about the revisit.
        revisited_by: Who performed the revisit.

    Returns:
        Dict with updated status and revisit count.
    """
    if outcome not in VALID_REVISIT_OUTCOMES:
        return {"status": "error", "message": f"Invalid outcome: {outcome}. Valid: {sorted(VALID_REVISIT_OUTCOMES)}"}

    row = conn.execute(
        "SELECT * FROM counterfactual_decisions WHERE id = ?",
        (counterfactual_id,),
    ).fetchone()

    if not row:
        return {"status": "error", "message": f"Counterfactual {counterfactual_id} not found"}

    current_status = row["status"]
    if current_status in ("validated", "reversed", "archived"):
        return {"status": "error", "message": f"Counterfactual {counterfactual_id} is {current_status} (terminal state)"}

    now = _now_utc()

    # Build revisit log entry
    revisit_entry = {
        "revisited_at": now,
        "outcome": outcome,
        "notes": notes,
        "revisited_by": revisited_by,
    }

    # Parse existing revisit notes
    existing_notes = json.loads(row["revisit_notes"]) if row["revisit_notes"] else []
    existing_notes.append(revisit_entry)

    # Determine new status
    if outcome == "validated":
        new_status = "validated"
    elif outcome == "reversed":
        new_status = "reversed"
    else:
        new_status = "revisited"

    new_count = (row["revisit_count"] or 0) + 1

    conn.execute(
        """UPDATE counterfactual_decisions
           SET status = ?, last_revisited = ?, revisit_count = ?,
               revisit_notes = ?
           WHERE id = ?""",
        (new_status, now, new_count, json.dumps(existing_notes), counterfactual_id),
    )
    conn.commit()

    return {
        "status": "ok",
        "action": "revisit_counterfactual",
        "counterfactual_id": counterfactual_id,
        "outcome": outcome,
        "new_status": new_status,
        "revisit_count": new_count,
        "last_revisited": now,
        "topic": row["topic"],
    }


def get_pending_revisits(
    conn: sqlite3.Connection,
    as_of: Optional[str] = None,
    limit: int = 50,
) -> dict:
    """Return counterfactuals due for revisit.

    A counterfactual is due when:
      1. status IN ('active', 'revisited') — not terminal
      2. Either never revisited (last_revisited IS NULL) AND
         captured_at + revisit_interval_days <= as_of
      3. Or last_revisited + revisit_interval_days <= as_of

    Args:
        conn: Database connection.
        as_of: Reference datetime (ISO string). Default: now.
        limit: Max results.

    Returns:
        Dict with list of pending counterfactuals.
    """
    if as_of is None:
        as_of = _now_utc()

    rows = conn.execute(
        """SELECT cd.*, m.summary as memory_summary
           FROM counterfactual_decisions cd
           LEFT JOIN memories m ON cd.decision_memory_id = m.id
           WHERE cd.status IN ('active', 'revisited')
             AND (
               (cd.last_revisited IS NULL
                AND datetime(cd.captured_at, '+' || cd.revisit_interval_days || ' days') <= ?)
               OR
               (cd.last_revisited IS NOT NULL
                AND datetime(cd.last_revisited, '+' || cd.revisit_interval_days || ' days') <= ?)
             )
           ORDER BY cd.council_weight_score DESC NULLS LAST, cd.captured_at ASC
           LIMIT ?""",
        (as_of, as_of, limit),
    ).fetchall()

    pending = []
    for row in rows:
        d = dict(row)
        if d.get("alternative_paths"):
            try:
                d["alternative_paths"] = json.loads(d["alternative_paths"])
            except json.JSONDecodeError:
                pass
        if d.get("revisit_notes"):
            try:
                d["revisit_notes"] = json.loads(d["revisit_notes"])
            except json.JSONDecodeError:
                pass
        pending.append(d)

    return {
        "status": "ok",
        "action": "get_pending_revisits",
        "pending": pending,
        "count": len(pending),
        "as_of": as_of,
    }


def compute_decision_weight(
    reversibility: float = 0.5,
    scope: float = 0.5,
    urgency: float = 0.5,
    conn: Optional[sqlite3.Connection] = None,
    council_session_id: Optional[int] = None,
) -> dict:
    """Compute a decision weight score.

    The weight is a composite of three dimensions:
      - reversibility: 0.0 = easily reversible, 1.0 = irreversible
      - scope: 0.0 = affects one component, 1.0 = affects entire system
      - urgency: 0.0 = can defer, 1.0 = must decide now

    Formula: 0.5 * reversibility + 0.3 * scope + 0.2 * urgency

    If council_session_id is given and conn is provided, also updates
    the council session's decision_weight_score.

    Args:
        reversibility: 0.0-1.0.
        scope: 0.0-1.0.
        urgency: 0.0-1.0.
        conn: Optional database connection (needed for council update).
        council_session_id: Optional council session to update.

    Returns:
        Dict with computed weight and dimensional breakdown.
    """
    for name, val in [("reversibility", reversibility), ("scope", scope), ("urgency", urgency)]:
        if not (0.0 <= val <= 1.0):
            return {"status": "error", "message": f"{name} must be 0.0-1.0, got {val}"}

    weight = (
        WEIGHT_COEFF_REVERSIBILITY * reversibility
        + WEIGHT_COEFF_SCOPE * scope
        + WEIGHT_COEFF_URGENCY * urgency
    )
    weight = round(weight, 4)

    # Optionally update council session
    if council_session_id is not None and conn is not None:
        row = conn.execute(
            "SELECT id FROM council_sessions WHERE id = ?",
            (council_session_id,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE council_sessions SET decision_weight_score = ? WHERE id = ?",
                (weight, council_session_id),
            )
            conn.commit()

    result = {
        "status": "ok",
        "action": "compute_decision_weight",
        "decision_weight_score": weight,
        "exceeds_threshold": weight >= DEFAULT_WEIGHT_THRESHOLD,
        "threshold": DEFAULT_WEIGHT_THRESHOLD,
        "dimensions": {
            "reversibility": reversibility,
            "scope": scope,
            "urgency": urgency,
        },
        "formula": f"{WEIGHT_COEFF_REVERSIBILITY} * reversibility + {WEIGHT_COEFF_SCOPE} * scope + {WEIGHT_COEFF_URGENCY} * urgency",
    }
    if council_session_id is not None:
        result["council_session_id"] = council_session_id
    return result


def get_counterfactual_history(
    conn: sqlite3.Connection,
    limit: int = 20,
    status: Optional[str] = None,
    min_weight: Optional[float] = None,
) -> dict:
    """List counterfactual decisions.

    Args:
        conn: Database connection.
        limit: Max results.
        status: Filter by status. Optional.
        min_weight: Filter by minimum weight. Optional.

    Returns:
        Dict with counterfactual list.
    """
    if status is not None and status not in VALID_STATUSES:
        return {"status": "error", "message": f"Invalid status: {status}. Valid: {sorted(VALID_STATUSES)}"}

    query = """
        SELECT cd.*, m.summary as memory_summary
        FROM counterfactual_decisions cd
        LEFT JOIN memories m ON cd.decision_memory_id = m.id
    """
    conditions = []
    params: list[Any] = []

    if status:
        conditions.append("cd.status = ?")
        params.append(status)

    if min_weight is not None:
        conditions.append("cd.council_weight_score >= ?")
        params.append(min_weight)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY cd.captured_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    results = []
    for row in rows:
        d = dict(row)
        for json_field in ("alternative_paths", "revisit_notes"):
            if d.get(json_field) and isinstance(d[json_field], str):
                try:
                    d[json_field] = json.loads(d[json_field])
                except json.JSONDecodeError:
                    pass
        results.append(d)

    return {
        "status": "ok",
        "action": "get_counterfactual_history",
        "counterfactuals": results,
        "count": len(results),
    }


def get_counterfactual(
    conn: sqlite3.Connection,
    counterfactual_id: int,
) -> dict:
    """Retrieve a single counterfactual with full data.

    Args:
        conn: Database connection.
        counterfactual_id: The counterfactual to retrieve.

    Returns:
        Dict with full counterfactual data.
    """
    row = conn.execute(
        """SELECT cd.*, m.summary as memory_summary, m.content as memory_content
           FROM counterfactual_decisions cd
           LEFT JOIN memories m ON cd.decision_memory_id = m.id
           WHERE cd.id = ?""",
        (counterfactual_id,),
    ).fetchone()

    if not row:
        return {"status": "error", "message": f"Counterfactual {counterfactual_id} not found"}

    d = dict(row)
    for json_field in ("alternative_paths", "revisit_notes"):
        if d.get(json_field) and isinstance(d[json_field], str):
            try:
                d[json_field] = json.loads(d[json_field])
            except json.JSONDecodeError:
                pass

    return {
        "status": "ok",
        "action": "get_counterfactual",
        "counterfactual": d,
    }


def archive_counterfactual(
    conn: sqlite3.Connection,
    counterfactual_id: int,
    reason: Optional[str] = None,
) -> dict:
    """Move a counterfactual to archived status.

    Args:
        conn: Database connection.
        counterfactual_id: The counterfactual to archive.
        reason: Optional reason for archival.

    Returns:
        Dict with status.
    """
    row = conn.execute(
        "SELECT status, topic FROM counterfactual_decisions WHERE id = ?",
        (counterfactual_id,),
    ).fetchone()

    if not row:
        return {"status": "error", "message": f"Counterfactual {counterfactual_id} not found"}

    if row["status"] == "archived":
        return {"status": "error", "message": f"Counterfactual {counterfactual_id} already archived"}

    now = _now_utc()

    # If reason provided, append to revisit_notes as an archival entry
    if reason:
        existing = conn.execute(
            "SELECT revisit_notes FROM counterfactual_decisions WHERE id = ?",
            (counterfactual_id,),
        ).fetchone()
        notes = json.loads(existing["revisit_notes"]) if existing["revisit_notes"] else []
        notes.append({
            "revisited_at": now,
            "outcome": "archived",
            "notes": reason,
            "revisited_by": "manual",
        })
        conn.execute(
            "UPDATE counterfactual_decisions SET status = 'archived', revisit_notes = ? WHERE id = ?",
            (json.dumps(notes), counterfactual_id),
        )
    else:
        conn.execute(
            "UPDATE counterfactual_decisions SET status = 'archived' WHERE id = ?",
            (counterfactual_id,),
        )

    conn.commit()

    return {
        "status": "ok",
        "action": "archive_counterfactual",
        "counterfactual_id": counterfactual_id,
        "topic": row["topic"],
        "previous_status": row["status"],
        "reason": reason,
    }


def auto_capture_from_council(
    conn: sqlite3.Connection,
    session_id: int,
    chosen_path: str,
    alternative_paths: Any,
    criteria_for_revisit: Optional[str] = None,
    revisit_interval_days: int = DEFAULT_REVISIT_INTERVAL,
    weight_threshold: float = DEFAULT_WEIGHT_THRESHOLD,
) -> dict:
    """Capture a counterfactual from a completed council session.

    Pulls topic, weight, and decision_memory_id from the council session.
    Only captures if the session's decision_weight_score exceeds the threshold.

    Args:
        conn: Database connection.
        session_id: Council session ID.
        chosen_path: What was chosen.
        alternative_paths: Alternatives considered.
        criteria_for_revisit: When to revisit. Optional.
        revisit_interval_days: Revisit interval. Default 90.
        weight_threshold: Minimum weight to capture. Default 0.4.

    Returns:
        Dict with capture result or skip reason.
    """
    row = conn.execute(
        """SELECT id, topic, decision_weight_score, decision_memory_id, status
           FROM council_sessions WHERE id = ?""",
        (session_id,),
    ).fetchone()

    if not row:
        return {"status": "error", "message": f"Council session {session_id} not found"}

    if row["status"] != "completed":
        return {"status": "error", "message": f"Council session {session_id} is {row['status']}, not completed"}

    weight = row["decision_weight_score"]

    # Gate on weight threshold
    if weight is None or weight < weight_threshold:
        return {
            "status": "ok",
            "action": "auto_capture_from_council",
            "result": "skipped",
            "reason": f"weight {weight} below threshold {weight_threshold}",
            "session_id": session_id,
            "decision_weight_score": weight,
        }

    decision_memory_id = row["decision_memory_id"]
    if decision_memory_id is None:
        return {
            "status": "error",
            "message": f"Council session {session_id} has no decision_memory_id. Link a reflection memory first.",
        }

    return capture_counterfactual(
        conn=conn,
        decision_memory_id=decision_memory_id,
        topic=row["topic"],
        chosen_path=chosen_path,
        alternative_paths=alternative_paths,
        council_weight_score=weight,
        council_session_id=session_id,
        criteria_for_revisit=criteria_for_revisit,
        revisit_interval_days=revisit_interval_days,
        captured_by="council_auto",
    )


def get_counterfactual_stats(conn: sqlite3.Connection) -> dict:
    """Aggregate statistics for the counterfactual table.

    Args:
        conn: Database connection.

    Returns:
        Dict with counts by status, average weight, etc.
    """
    if not is_counterfactual_migration_applied(conn):
        return {"status": "error", "message": "counterfactual_decisions table not found"}

    total = conn.execute("SELECT COUNT(*) FROM counterfactual_decisions").fetchone()[0]

    if total == 0:
        return {
            "status": "ok",
            "action": "get_counterfactual_stats",
            "total": 0,
            "by_status": {},
            "avg_weight": None,
            "avg_revisit_count": 0,
            "pending_revisits": 0,
        }

    by_status = {}
    for row in conn.execute(
        "SELECT status, COUNT(*) as cnt FROM counterfactual_decisions GROUP BY status"
    ).fetchall():
        by_status[row[0]] = row[1]

    avg_weight_row = conn.execute(
        "SELECT AVG(council_weight_score) FROM counterfactual_decisions WHERE council_weight_score IS NOT NULL"
    ).fetchone()
    avg_weight = round(avg_weight_row[0], 4) if avg_weight_row[0] is not None else None

    avg_revisit = conn.execute(
        "SELECT AVG(revisit_count) FROM counterfactual_decisions"
    ).fetchone()[0]
    avg_revisit = round(avg_revisit, 2) if avg_revisit else 0

    pending_result = get_pending_revisits(conn, limit=1000)
    pending_count = pending_result.get("count", 0)

    by_captured_by = {}
    for row in conn.execute(
        "SELECT captured_by, COUNT(*) as cnt FROM counterfactual_decisions GROUP BY captured_by"
    ).fetchall():
        by_captured_by[row[0]] = row[1]

    return {
        "status": "ok",
        "action": "get_counterfactual_stats",
        "total": total,
        "by_status": by_status,
        "by_captured_by": by_captured_by,
        "avg_weight": avg_weight,
        "avg_revisit_count": avg_revisit,
        "pending_revisits": pending_count,
    }


# ---------------------------------------------------------------------------
# Quarterly revisit cycle (for cron invocation)
# ---------------------------------------------------------------------------

def run_quarterly_revisit(
    db_path: str,
    dry_run: bool = False,
    use_llm: bool = True,
) -> dict:
    """Run the quarterly revisit cycle.

    For each pending counterfactual:
      1. Load the decision memory + alternatives
      2. If use_llm: invoke `claude -p` to analyze whether the decision
         was correct given what we know now
      3. Record the revisit outcome

    In dry_run mode, lists pending revisits without modifying anything.

    Args:
        db_path: Path to the memory database.
        dry_run: If True, only report what would be revisited.
        use_llm: If True, use `claude -p` for analysis. If False,
                 mark as 'still_active' (manual review needed).

    Returns:
        Dict with revisit results.
    """
    t0 = time.time()
    conn = _get_conn(db_path)

    try:
        if not is_counterfactual_migration_applied(conn):
            return {"status": "error", "message": "counterfactual_decisions table not found"}

        pending = get_pending_revisits(conn)
        pending_list = pending.get("pending", [])

        if dry_run:
            return {
                "status": "ok",
                "action": "run_quarterly_revisit",
                "mode": "dry_run",
                "pending_count": len(pending_list),
                "pending": [
                    {"id": p["id"], "topic": p["topic"],
                     "weight": p["council_weight_score"],
                     "revisit_count": p["revisit_count"]}
                    for p in pending_list
                ],
                "duration_seconds": round(time.time() - t0, 2),
            }

        results = []
        for p in pending_list:
            cf_id = p["id"]

            if use_llm:
                analysis = _llm_revisit_analysis(p)
            else:
                analysis = {
                    "outcome": "still_active",
                    "notes": "Automated revisit without LLM — needs manual review.",
                }

            outcome = analysis.get("outcome", "still_active")
            notes = analysis.get("notes", "")

            result = revisit_counterfactual(
                conn=conn,
                counterfactual_id=cf_id,
                outcome=outcome,
                notes=notes,
                revisited_by="cron_revisit",
            )
            results.append({
                "counterfactual_id": cf_id,
                "topic": p["topic"],
                "outcome": outcome,
                "new_status": result.get("new_status"),
            })

        duration = round(time.time() - t0, 2)

        return {
            "status": "ok",
            "action": "run_quarterly_revisit",
            "mode": "llm" if use_llm else "mark_pending",
            "revisited_count": len(results),
            "results": results,
            "duration_seconds": duration,
        }

    finally:
        conn.close()


def _llm_revisit_analysis(counterfactual: dict) -> dict:
    """Use `claude -p` to analyze whether a past decision was correct.

    Constructs a prompt with the decision context, chosen path, alternatives,
    and any prior revisit notes, then asks for a structured assessment.

    Args:
        counterfactual: Dict with counterfactual data (from get_pending_revisits).

    Returns:
        Dict with 'outcome' (a VALID_REVISIT_OUTCOMES value) and 'notes'.
    """
    topic = counterfactual.get("topic", "unknown")
    chosen = counterfactual.get("chosen_path", "unknown")
    alts = counterfactual.get("alternative_paths", [])
    if isinstance(alts, str):
        try:
            alts = json.loads(alts)
        except json.JSONDecodeError:
            alts = []

    weight = counterfactual.get("council_weight_score", "N/A")
    prior_notes = counterfactual.get("revisit_notes", [])
    if isinstance(prior_notes, str):
        try:
            prior_notes = json.loads(prior_notes)
        except json.JSONDecodeError:
            prior_notes = []

    memory_summary = counterfactual.get("memory_summary", "")
    criteria = counterfactual.get("criteria_for_revisit", "")

    # Build the prompt
    prompt_parts = [
        "You are reviewing a past decision to determine if it was the right call.",
        "",
        f"DECISION TOPIC: {topic}",
        f"DECISION WEIGHT: {weight} (0=trivial, 1=irreversible)",
        f"CHOSEN PATH: {chosen}",
        "",
        "ALTERNATIVES CONSIDERED:",
    ]

    for i, alt in enumerate(alts, 1):
        path = alt.get("path", "?")
        predicted = alt.get("predicted_outcome", "not specified")
        rationale = alt.get("rationale", "not specified")
        prompt_parts.append(f"  {i}. {path}")
        prompt_parts.append(f"     Predicted outcome: {predicted}")
        prompt_parts.append(f"     Rationale: {rationale}")

    if memory_summary:
        prompt_parts.extend(["", f"DECISION MEMORY: {memory_summary}"])

    if criteria:
        prompt_parts.extend(["", f"REVISIT CRITERIA: {criteria}"])

    if prior_notes:
        prompt_parts.extend(["", "PRIOR REVISIT HISTORY:"])
        for note in prior_notes:
            prompt_parts.append(
                f"  - {note.get('revisited_at', '?')}: {note.get('outcome', '?')} — {note.get('notes', '')}"
            )

    prompt_parts.extend([
        "",
        "Based on what you know, assess this decision. Respond with EXACTLY this JSON format:",
        '{"outcome": "<one of: validated, still_active, reversed, partially_wrong, context_changed>",',
        ' "notes": "<2-3 sentence assessment>"}',
        "",
        "Be honest. If you cannot assess the decision with confidence, use 'still_active'.",
    ])

    prompt = "\n".join(prompt_parts)

    # Through the thinker seam (text only). A failure keeps this organ's documented degrade: still_active + why.
    try:
        output = _thinker.think(prompt, purpose="counterfactual_memory", timeout_s=120).text.strip()
    except _thinker.ThinkTimeout:
        return {"outcome": "still_active", "notes": "LLM analysis timed out (120s)."}
    except _thinker.ThinkError as e:
        logger.warning("thinker %s: %s", e.kind, e)
        return {"outcome": "still_active", "notes": f"LLM analysis failed: {str(e)[:100]}"}

    return _parse_revisit_reply(output)


# Round 3 G2 item 4 (R2-05's sibling; the dream parser's classes, applied to this organ's JSON answer). The prompt shows
# its answer SHAPE with two placeholders -- "<one of: validated, ...>" and "<2-3 sentence assessment>". On the N bytes a
# reply that echoed the NOTES placeholder beside a valid outcome was recorded as the assessment -- and 'validated' /
# 'reversed' are TERMINAL (revisit_counterfactual): a decision could be closed for good on template text. Now:
#   * a placeholder echoed in either field, the shape's own words, or notes missing / empty / not text -> the analysis
#     is REJECTED: still_active (the organ's documented degrade, the prompt's own "if you cannot assess ... use
#     still_active") with a note that says what came back, and a warning in the journal;
#   * a genuine answer DECORATED the way models decorate (the outcome in another case, in quotes or backticks, with a
#     space for the underscore; the JSON inside prose or a fence) is READ, not lost to 'not parseable'.
_CF_PLACEHOLDER = re.compile(r"^\s*<[^<>]{5,}>\s*$")
_CF_SHAPE_WORDS = ("2-3 sentence assessment", "one of: validated")


def _json_objects(text: str):
    """Each balanced {...} in ``text``, in order (string-aware), as text."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield text[start:i + 1]
                    break
        start = text.find("{", start + 1)


def _first_answer(text: str) -> "Optional[tuple]":
    """(the object's text, the parsed dict) of the first {...} in ``text`` that parses to a dict carrying 'outcome'
    -- the answer inside prose or a fence -- or None."""
    for cand in _json_objects(text):
        try:
            obj = json.loads(cand)
        except ValueError:
            continue
        if isinstance(obj, dict) and "outcome" in obj:
            return cand, obj
    return None


def _norm_outcome(v) -> str:
    return re.sub(r"[\s-]+", "_", str(v or "").strip().strip("`'\"<>[]()*_ ").strip().lower())


def _parse_revisit_reply(output: str) -> dict:
    """The organ's parser for one revisit answer: ``{'outcome', 'notes'}`` (plus ``'rejected'`` when the answer is
    not an assessment). Never raises."""
    try:
        raw = (output or "").strip()
        found = _first_answer(raw)
        if found is None:
            return {"outcome": "still_active", "notes": f"LLM output not parseable as JSON: {raw[:200]}"}
        body, parsed = found
        got_outcome, notes = parsed.get("outcome"), parsed.get("notes")
        echoed = [f for f, v in (("outcome", got_outcome), ("notes", notes)) if isinstance(v, str) and (
            _CF_PLACEHOLDER.match(v) or any(w in v.lower() for w in _CF_SHAPE_WORDS))]
        if echoed:
            logger.warning("counterfactual revisit: the thinker echoed the prompt's placeholder in %s",
                           " and ".join(echoed))
            return {"outcome": "still_active", "rejected": "placeholder",
                    "notes": "LLM analysis rejected: the thinker echoed the prompt's placeholder in %s instead of an "
                             "assessment (%s)" % (" and ".join(echoed), body[:160])}
        if not isinstance(notes, str) or not notes.strip():
            logger.warning("counterfactual revisit: the thinker gave no assessment (notes %r)", notes)
            return {"outcome": "still_active", "rejected": "no-notes",
                    "notes": "LLM analysis rejected: no assessment in the notes (%s)" % body[:160]}
        outcome = _norm_outcome(got_outcome)
        if outcome not in VALID_REVISIT_OUTCOMES:
            return {"outcome": "still_active",
                    "notes": f"LLM returned invalid outcome '{got_outcome}'. Raw: {body[:200]}"}
        return {"outcome": outcome, "notes": notes.strip()}
    except (json.JSONDecodeError, ValueError):
        return {"outcome": "still_active", "notes": f"LLM output not parseable as JSON: {(output or '')[:200]}"}
    except Exception as e:
        return {"outcome": "still_active", "notes": f"LLM analysis error: {str(e)[:200]}"}


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

def unit_test_counterfactual_memory():
    """Self-contained unit tests against a temporary database."""
    import tempfile

    test_db = tempfile.mktemp(suffix=".db")
    passed = 0
    failed = 0
    errors = []

    def assert_eq(actual, expected, label):
        nonlocal passed, failed
        if actual == expected:
            passed += 1
        else:
            failed += 1
            errors.append(f"FAIL: {label}: expected {expected!r}, got {actual!r}")

    def assert_true(value, label):
        nonlocal passed, failed
        if value:
            passed += 1
        else:
            failed += 1
            errors.append(f"FAIL: {label}: expected truthy, got {value!r}")

    def assert_in(value, container, label):
        nonlocal passed, failed
        if value in container:
            passed += 1
        else:
            failed += 1
            errors.append(f"FAIL: {label}: {value!r} not in {container!r}")

    try:
        # Setup: create minimal schema with memories + council_sessions tables
        conn = sqlite3.connect(test_db)
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
                kind TEXT DEFAULT 'episodic',
                derived_from TEXT DEFAULT NULL,
                consolidated_at TEXT DEFAULT NULL,
                confidence REAL DEFAULT 1.0,
                foundation_score REAL DEFAULT NULL,
                skill_tier TEXT DEFAULT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS council_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_type TEXT NOT NULL,
                topic TEXT NOT NULL,
                topic_category TEXT DEFAULT NULL,
                rounds_completed INTEGER NOT NULL DEFAULT 0,
                transcript TEXT DEFAULT NULL,
                synthesis TEXT DEFAULT NULL,
                drew_input TEXT DEFAULT NULL,
                decision_memory_id INTEGER DEFAULT NULL,
                decision_weight_score REAL DEFAULT NULL,
                participants TEXT DEFAULT '["orin","vael","dessa","kael-em","milamber"]',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at TEXT DEFAULT NULL,
                status TEXT NOT NULL DEFAULT 'in_progress'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY, value TEXT, updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("INSERT INTO metadata (key, value) VALUES ('schema_version', '3.10-test')")

        # Insert test memories
        conn.execute(
            "INSERT INTO memories (id, content, summary, category) VALUES (1, 'Decision: use SQLite', 'Use SQLite for memory storage', 'technical_decision')"
        )
        conn.execute(
            "INSERT INTO memories (id, content, summary, category) VALUES (2, 'Decision: use council', 'Council-weighted decisions', 'technical_decision')"
        )
        conn.execute(
            "INSERT INTO memories (id, content, summary, category) VALUES (3, 'Archived memory', 'Archived', 'technical_decision')"
        )
        conn.execute("UPDATE memories SET is_archived = 1 WHERE id = 3")

        # Insert test council sessions
        conn.execute(
            """INSERT INTO council_sessions
               (id, trigger_type, topic, status, decision_weight_score, decision_memory_id, completed_at)
               VALUES (1, 'manual', 'Should we use SQLite?', 'completed', 0.72, 1, datetime('now'))"""
        )
        conn.execute(
            """INSERT INTO council_sessions
               (id, trigger_type, topic, status, decision_weight_score, decision_memory_id, completed_at)
               VALUES (2, 'manual', 'Trivial formatting choice', 'completed', 0.15, 2, datetime('now'))"""
        )
        conn.execute(
            """INSERT INTO council_sessions
               (id, trigger_type, topic, status)
               VALUES (3, 'manual', 'In progress topic', 'in_progress')"""
        )
        conn.commit()
        conn.close()

        # --- Migration tests ---

        # T1: apply migration
        result = apply_counterfactual_migration(test_db)
        assert_eq(result["status"], "ok", "T1: migration status")
        assert_eq(result["result"], "applied", "T1: migration applied")

        # T2: idempotent
        result = apply_counterfactual_migration(test_db)
        assert_eq(result["result"], "already_applied", "T2: migration idempotent")

        # T3: is_counterfactual_migration_applied
        conn = sqlite3.connect(test_db)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        assert_true(is_counterfactual_migration_applied(conn), "T3: migration check")

        # --- capture_counterfactual tests ---

        # T4: basic capture
        result = capture_counterfactual(
            conn,
            decision_memory_id=1,
            topic="Use SQLite for memory storage",
            chosen_path="SQLite with WAL mode",
            alternative_paths=[
                {"path": "PostgreSQL", "predicted_outcome": "Better concurrency", "rationale": "Standard choice for multi-user"},
                {"path": "Redis", "predicted_outcome": "Faster reads", "rationale": "In-memory for hot path"},
            ],
            council_weight_score=0.72,
            council_session_id=1,
            criteria_for_revisit="When memory DB exceeds 1GB or concurrent writes needed",
        )
        assert_eq(result["status"], "ok", "T4: capture status")
        assert_true(result["counterfactual_id"] >= 1, "T4: got ID")
        cf_id_1 = result["counterfactual_id"]

        # T5: capture with string JSON alternative_paths
        result = capture_counterfactual(
            conn,
            decision_memory_id=2,
            topic="Council deliberation format",
            chosen_path="Round-robin then synthesis",
            alternative_paths='[{"path": "Free-form", "predicted_outcome": "More organic discussion"}]',
            council_weight_score=0.55,
        )
        assert_eq(result["status"], "ok", "T5: string JSON capture")
        cf_id_2 = result["counterfactual_id"]

        # T6: invalid alternative_paths (empty array)
        result = capture_counterfactual(
            conn, decision_memory_id=1, topic="t", chosen_path="c",
            alternative_paths=[],
        )
        assert_eq(result["status"], "error", "T6: empty alternatives rejected")

        # T7: invalid alternative_paths (missing path key)
        result = capture_counterfactual(
            conn, decision_memory_id=1, topic="t", chosen_path="c",
            alternative_paths=[{"option": "foo"}],
        )
        assert_eq(result["status"], "error", "T7: missing path key rejected")

        # T8: invalid weight
        result = capture_counterfactual(
            conn, decision_memory_id=1, topic="t", chosen_path="c",
            alternative_paths=[{"path": "x"}], council_weight_score=1.5,
        )
        assert_eq(result["status"], "error", "T8: invalid weight rejected")

        # T9: archived memory rejected
        result = capture_counterfactual(
            conn, decision_memory_id=3, topic="t", chosen_path="c",
            alternative_paths=[{"path": "x"}],
        )
        assert_eq(result["status"], "error", "T9: archived memory rejected")

        # T10: nonexistent memory
        result = capture_counterfactual(
            conn, decision_memory_id=9999, topic="t", chosen_path="c",
            alternative_paths=[{"path": "x"}],
        )
        assert_eq(result["status"], "error", "T10: nonexistent memory rejected")

        # T11: nonexistent council session
        result = capture_counterfactual(
            conn, decision_memory_id=1, topic="t", chosen_path="c",
            alternative_paths=[{"path": "x"}], council_session_id=9999,
        )
        assert_eq(result["status"], "error", "T11: nonexistent session rejected")

        # T12: invalid captured_by
        result = capture_counterfactual(
            conn, decision_memory_id=1, topic="t", chosen_path="c",
            alternative_paths=[{"path": "x"}], captured_by="invalid",
        )
        assert_eq(result["status"], "error", "T12: invalid captured_by rejected")

        # T13: auto-pull weight from council when not provided
        result = capture_counterfactual(
            conn, decision_memory_id=1, topic="Auto weight",
            chosen_path="auto", alternative_paths=[{"path": "x"}],
            council_session_id=1,
        )
        assert_eq(result["status"], "ok", "T13: auto-pull weight status")
        assert_eq(result["council_weight_score"], 0.72, "T13: weight pulled from council")

        # --- get_counterfactual tests ---

        # T14: get existing
        result = get_counterfactual(conn, cf_id_1)
        assert_eq(result["status"], "ok", "T14: get status")
        assert_eq(result["counterfactual"]["topic"], "Use SQLite for memory storage", "T14: topic")
        assert_true(isinstance(result["counterfactual"]["alternative_paths"], list), "T14: alts parsed")
        assert_eq(len(result["counterfactual"]["alternative_paths"]), 2, "T14: 2 alternatives")

        # T15: get nonexistent
        result = get_counterfactual(conn, 9999)
        assert_eq(result["status"], "error", "T15: nonexistent cf")

        # --- get_counterfactual_history tests ---

        # T16: list all
        result = get_counterfactual_history(conn)
        assert_eq(result["status"], "ok", "T16: history status")
        assert_true(result["count"] >= 2, "T16: at least 2 counterfactuals")

        # T17: filter by status
        result = get_counterfactual_history(conn, status="active")
        assert_true(result["count"] >= 1, "T17: active filter")

        # T18: invalid status filter
        result = get_counterfactual_history(conn, status="invalid")
        assert_eq(result["status"], "error", "T18: invalid status rejected")

        # T19: filter by min_weight
        result = get_counterfactual_history(conn, min_weight=0.7)
        assert_true(result["count"] >= 1, "T19: weight filter")

        # --- revisit_counterfactual tests ---

        # T20: revisit with validated outcome
        result = revisit_counterfactual(conn, cf_id_1, "validated", "SQLite still the right call at 500MB.")
        assert_eq(result["status"], "ok", "T20: revisit status")
        assert_eq(result["new_status"], "validated", "T20: validated terminal")
        assert_eq(result["revisit_count"], 1, "T20: revisit count")

        # T21: cannot revisit terminal state
        result = revisit_counterfactual(conn, cf_id_1, "still_active", "trying again")
        assert_eq(result["status"], "error", "T21: terminal state blocked")

        # T22: revisit with still_active outcome
        result = revisit_counterfactual(conn, cf_id_2, "still_active", "Too early to tell.")
        assert_eq(result["status"], "ok", "T22: still_active status")
        assert_eq(result["new_status"], "revisited", "T22: stays in rotation")

        # T23: second revisit on same counterfactual
        result = revisit_counterfactual(conn, cf_id_2, "context_changed", "World shifted.")
        assert_eq(result["status"], "ok", "T23: second revisit ok")
        assert_eq(result["revisit_count"], 2, "T23: count incremented")

        # T24: revisit log preserved
        cf_data = get_counterfactual(conn, cf_id_2)
        notes = cf_data["counterfactual"]["revisit_notes"]
        assert_eq(len(notes), 2, "T24: two revisit log entries")
        assert_eq(notes[0]["outcome"], "still_active", "T24: first entry correct")
        assert_eq(notes[1]["outcome"], "context_changed", "T24: second entry correct")

        # T25: invalid outcome
        result = revisit_counterfactual(conn, cf_id_2, "invalid_outcome")
        assert_eq(result["status"], "error", "T25: invalid outcome rejected")

        # T26: nonexistent counterfactual
        result = revisit_counterfactual(conn, 9999, "validated")
        assert_eq(result["status"], "error", "T26: nonexistent cf rejected")

        # --- archive tests ---

        # T27: archive with reason
        cf_id_3 = capture_counterfactual(
            conn, decision_memory_id=1, topic="Archivable decision",
            chosen_path="x", alternative_paths=[{"path": "y"}],
        )["counterfactual_id"]
        result = archive_counterfactual(conn, cf_id_3, "Project ended.")
        assert_eq(result["status"], "ok", "T27: archive status")
        assert_eq(result["previous_status"], "active", "T27: was active")

        # T28: cannot archive already archived
        result = archive_counterfactual(conn, cf_id_3)
        assert_eq(result["status"], "error", "T28: double archive blocked")

        # T29: archive nonexistent
        result = archive_counterfactual(conn, 9999)
        assert_eq(result["status"], "error", "T29: nonexistent archive")

        # --- compute_decision_weight tests ---

        # T30: basic weight computation
        result = compute_decision_weight(reversibility=0.8, scope=0.6, urgency=0.3)
        assert_eq(result["status"], "ok", "T30: weight status")
        expected = round(0.5 * 0.8 + 0.3 * 0.6 + 0.2 * 0.3, 4)
        assert_eq(result["decision_weight_score"], expected, "T30: weight value")
        assert_true(result["exceeds_threshold"], "T30: exceeds threshold")

        # T31: below threshold
        result = compute_decision_weight(reversibility=0.1, scope=0.1, urgency=0.1)
        assert_true(not result["exceeds_threshold"], "T31: below threshold")

        # T32: all zero
        result = compute_decision_weight(reversibility=0.0, scope=0.0, urgency=0.0)
        assert_eq(result["decision_weight_score"], 0.0, "T32: all zero")

        # T33: all one
        result = compute_decision_weight(reversibility=1.0, scope=1.0, urgency=1.0)
        assert_eq(result["decision_weight_score"], 1.0, "T33: all one")

        # T34: invalid dimension
        result = compute_decision_weight(reversibility=1.5)
        assert_eq(result["status"], "error", "T34: invalid dimension")

        # T35: weight with council session update
        result = compute_decision_weight(
            reversibility=0.9, scope=0.7, urgency=0.5,
            conn=conn, council_session_id=3,
        )
        assert_eq(result["status"], "ok", "T35: weight with council update")
        # Verify the session was updated
        sess = conn.execute(
            "SELECT decision_weight_score FROM council_sessions WHERE id = 3"
        ).fetchone()
        assert_eq(sess[0], result["decision_weight_score"], "T35: session weight updated")

        # --- auto_capture_from_council tests ---

        # T36: auto capture from high-weight session
        result = auto_capture_from_council(
            conn, session_id=1, chosen_path="SQLite with WAL",
            alternative_paths=[{"path": "PostgreSQL", "predicted_outcome": "Better concurrency"}],
        )
        assert_eq(result["status"], "ok", "T36: auto capture ok")
        assert_eq(result["captured_by"], "council_auto", "T36: captured_by")

        # T37: auto capture skipped for low-weight session
        result = auto_capture_from_council(
            conn, session_id=2, chosen_path="whatever",
            alternative_paths=[{"path": "other"}],
        )
        assert_eq(result["result"], "skipped", "T37: low weight skipped")

        # T38: auto capture from nonexistent session
        result = auto_capture_from_council(
            conn, session_id=9999, chosen_path="x",
            alternative_paths=[{"path": "y"}],
        )
        assert_eq(result["status"], "error", "T38: nonexistent session")

        # T39: auto capture from in-progress session
        result = auto_capture_from_council(
            conn, session_id=3, chosen_path="x",
            alternative_paths=[{"path": "y"}],
        )
        assert_eq(result["status"], "error", "T39: incomplete session")

        # --- get_pending_revisits tests ---

        # T40: pending revisits with future date (should be empty since all are new)
        result = get_pending_revisits(conn, as_of="2026-01-01 00:00:00")
        assert_eq(result["count"], 0, "T40: no pending in past")

        # T41: pending revisits with far-future date
        result = get_pending_revisits(conn, as_of="2027-12-31 00:00:00")
        # Should find active/revisited ones that are past their interval
        assert_true(result["count"] >= 0, "T41: future query works")

        # --- get_counterfactual_stats tests ---

        # T42: stats
        result = get_counterfactual_stats(conn)
        assert_eq(result["status"], "ok", "T42: stats status")
        assert_true(result["total"] >= 3, "T42: total >= 3")
        assert_true("active" in result["by_status"] or "validated" in result["by_status"],
                     "T42: by_status populated")

        # --- edge cases ---

        # T43: capture with minimal alternative_paths (just path key)
        result = capture_counterfactual(
            conn, decision_memory_id=2, topic="Minimal",
            chosen_path="x", alternative_paths=[{"path": "y"}],
        )
        assert_eq(result["status"], "ok", "T43: minimal alts accepted")

        # T44: invalid revisit_interval_days
        result = capture_counterfactual(
            conn, decision_memory_id=1, topic="t", chosen_path="c",
            alternative_paths=[{"path": "x"}], revisit_interval_days=0,
        )
        assert_eq(result["status"], "error", "T44: zero interval rejected")

        # T45: reversed outcome sets terminal status
        cf_id_4 = capture_counterfactual(
            conn, decision_memory_id=1, topic="Reversible",
            chosen_path="A", alternative_paths=[{"path": "B"}],
        )["counterfactual_id"]
        result = revisit_counterfactual(conn, cf_id_4, "reversed", "Should have picked B.")
        assert_eq(result["new_status"], "reversed", "T45: reversed is terminal")

        # T46: cannot revisit reversed
        result = revisit_counterfactual(conn, cf_id_4, "validated")
        assert_eq(result["status"], "error", "T46: reversed is terminal")

        # T47: partially_wrong keeps in rotation
        cf_id_5 = capture_counterfactual(
            conn, decision_memory_id=2, topic="Partial",
            chosen_path="C", alternative_paths=[{"path": "D"}],
        )["counterfactual_id"]
        result = revisit_counterfactual(conn, cf_id_5, "partially_wrong", "Some aspects wrong.")
        assert_eq(result["new_status"], "revisited", "T47: partially_wrong stays active")

        # T48: archive without reason
        cf_id_6 = capture_counterfactual(
            conn, decision_memory_id=1, topic="No reason archive",
            chosen_path="E", alternative_paths=[{"path": "F"}],
        )["counterfactual_id"]
        result = archive_counterfactual(conn, cf_id_6)
        assert_eq(result["status"], "ok", "T48: archive without reason")

        # === Eval Scenario 4: Full revisit lifecycle chain ===
        # active -> still_active (revisited) -> partially_wrong (revisited)
        # -> context_changed (revisited) -> validated (terminal)
        cf_lifecycle = capture_counterfactual(
            conn, decision_memory_id=1, topic="Lifecycle chain test",
            chosen_path="Path A", alternative_paths=[{"path": "Path B", "predicted_outcome": "faster"}],
            council_weight_score=0.85,
        )["counterfactual_id"]

        # Step 1: active -> revisited (still_active)
        r1 = revisit_counterfactual(conn, cf_lifecycle, "still_active", "Too early to tell.")
        assert_eq(r1["status"], "ok", "T49-lifecycle: step 1 ok")
        assert_eq(r1["new_status"], "revisited", "T49-lifecycle: still_active -> revisited")
        assert_eq(r1["revisit_count"], 1, "T49-lifecycle: count 1")

        # Step 2: revisited -> revisited (partially_wrong)
        r2 = revisit_counterfactual(conn, cf_lifecycle, "partially_wrong", "One aspect was wrong.")
        assert_eq(r2["status"], "ok", "T50-lifecycle: step 2 ok")
        assert_eq(r2["new_status"], "revisited", "T50-lifecycle: partially_wrong -> revisited")
        assert_eq(r2["revisit_count"], 2, "T50-lifecycle: count 2")

        # Step 3: revisited -> revisited (context_changed)
        r3 = revisit_counterfactual(conn, cf_lifecycle, "context_changed", "World shifted.")
        assert_eq(r3["status"], "ok", "T51-lifecycle: step 3 ok")
        assert_eq(r3["new_status"], "revisited", "T51-lifecycle: context_changed -> revisited")
        assert_eq(r3["revisit_count"], 3, "T51-lifecycle: count 3")

        # Step 4: revisited -> validated (terminal)
        r4 = revisit_counterfactual(conn, cf_lifecycle, "validated", "Confirmed correct in hindsight.")
        assert_eq(r4["status"], "ok", "T52-lifecycle: step 4 ok")
        assert_eq(r4["new_status"], "validated", "T52-lifecycle: validated terminal")
        assert_eq(r4["revisit_count"], 4, "T52-lifecycle: count 4")

        # Step 5: terminal -> blocked
        r5 = revisit_counterfactual(conn, cf_lifecycle, "still_active", "Try again.")
        assert_eq(r5["status"], "error", "T53-lifecycle: terminal blocked")
        assert_true("terminal" in r5.get("message", ""), "T53-lifecycle: error mentions terminal")

        # Verify full revisit log integrity
        cf_full = get_counterfactual(conn, cf_lifecycle)
        log = cf_full["counterfactual"]["revisit_notes"]
        assert_eq(len(log), 4, "T54-lifecycle: 4 log entries")
        assert_eq(log[0]["outcome"], "still_active", "T54-lifecycle: log[0] correct")
        assert_eq(log[1]["outcome"], "partially_wrong", "T54-lifecycle: log[1] correct")
        assert_eq(log[2]["outcome"], "context_changed", "T54-lifecycle: log[2] correct")
        assert_eq(log[3]["outcome"], "validated", "T54-lifecycle: log[3] correct")

        # === Eval Scenario 6: Pending revisits ordering by weight ===
        # Create 3 counterfactuals with known weights and past capture dates,
        # then verify get_pending_revisits returns them weight-DESC.
        past_ts = "2025-01-01 00:00:00"
        future_ts = "2027-06-01 00:00:00"

        cf_low = capture_counterfactual(
            conn, decision_memory_id=1, topic="Low weight pending",
            chosen_path="L", alternative_paths=[{"path": "M"}],
            council_weight_score=0.45,
        )["counterfactual_id"]

        cf_mid = capture_counterfactual(
            conn, decision_memory_id=2, topic="Mid weight pending",
            chosen_path="N", alternative_paths=[{"path": "O"}],
            council_weight_score=0.65,
        )["counterfactual_id"]

        cf_high = capture_counterfactual(
            conn, decision_memory_id=1, topic="High weight pending",
            chosen_path="P", alternative_paths=[{"path": "Q"}],
            council_weight_score=0.90,
        )["counterfactual_id"]

        # Backdate captured_at so they are past their 90-day interval
        for cid in (cf_low, cf_mid, cf_high):
            conn.execute(
                "UPDATE counterfactual_decisions SET captured_at = ? WHERE id = ?",
                (past_ts, cid),
            )
        conn.commit()

        pending = get_pending_revisits(conn, as_of=future_ts)
        assert_true(pending["count"] >= 3, "T55-ordering: at least 3 pending")

        # Extract just the ones we created (by topic match)
        ordering_topics = [
            p["topic"] for p in pending["pending"]
            if p["topic"] in ("Low weight pending", "Mid weight pending", "High weight pending")
        ]
        assert_eq(len(ordering_topics), 3, "T56-ordering: all 3 found in pending")

        # Verify weight-DESC ordering: High (0.90) before Mid (0.65) before Low (0.45)
        idx_high = ordering_topics.index("High weight pending")
        idx_mid = ordering_topics.index("Mid weight pending")
        idx_low = ordering_topics.index("Low weight pending")
        assert_true(idx_high < idx_mid, "T57-ordering: high before mid")
        assert_true(idx_mid < idx_low, "T58-ordering: mid before low")

        # === Eval Scenario 7: as_of parameter gating ===
        # Counterfactual captured "today" with 90-day interval should NOT appear
        # when as_of is only 30 days out.
        cf_fresh = capture_counterfactual(
            conn, decision_memory_id=1, topic="Fresh decision",
            chosen_path="X", alternative_paths=[{"path": "Y"}],
            council_weight_score=0.70,
            revisit_interval_days=90,
        )["counterfactual_id"]
        # Set captured_at to a known date
        known_date = "2026-03-01 00:00:00"
        conn.execute(
            "UPDATE counterfactual_decisions SET captured_at = ? WHERE id = ?",
            (known_date, cf_fresh),
        )
        conn.commit()

        # 30 days out: should NOT be pending (90-day interval not met)
        pending_30 = get_pending_revisits(conn, as_of="2026-03-31 00:00:00")
        fresh_ids_30 = [p["id"] for p in pending_30["pending"] if p["id"] == cf_fresh]
        assert_eq(len(fresh_ids_30), 0, "T59-as_of: not pending at 30 days")

        # 91 days out: SHOULD be pending
        pending_91 = get_pending_revisits(conn, as_of="2026-06-01 00:00:00")
        fresh_ids_91 = [p["id"] for p in pending_91["pending"] if p["id"] == cf_fresh]
        assert_eq(len(fresh_ids_91), 1, "T60-as_of: pending at 91 days")

        # After revisit, next interval should reset from last_revisited
        revisit_counterfactual(conn, cf_fresh, "still_active", "Checking in.")
        # Update last_revisited to a known date
        conn.execute(
            "UPDATE counterfactual_decisions SET last_revisited = ? WHERE id = ?",
            ("2026-06-01 00:00:00", cf_fresh),
        )
        conn.commit()

        # 30 days after last revisit: NOT pending
        pending_30b = get_pending_revisits(conn, as_of="2026-07-01 00:00:00")
        fresh_ids_30b = [p["id"] for p in pending_30b["pending"] if p["id"] == cf_fresh]
        assert_eq(len(fresh_ids_30b), 0, "T61-as_of: not pending 30d after revisit")

        # 91 days after last revisit: pending again
        pending_91b = get_pending_revisits(conn, as_of="2026-09-01 00:00:00")
        fresh_ids_91b = [p["id"] for p in pending_91b["pending"] if p["id"] == cf_fresh]
        assert_eq(len(fresh_ids_91b), 1, "T62-as_of: pending 91d after revisit")

        # Terminal counterfactual should NEVER appear in pending
        revisit_counterfactual(conn, cf_fresh, "reversed", "Wrong call.")
        pending_term = get_pending_revisits(conn, as_of="2030-01-01 00:00:00")
        fresh_ids_term = [p["id"] for p in pending_term["pending"] if p["id"] == cf_fresh]
        assert_eq(len(fresh_ids_term), 0, "T63-as_of: terminal never pending")

        conn.close()

    finally:
        try:
            os.unlink(test_db)
        except OSError:
            pass

    print(f"\n{'='*60}")
    print(f"Counterfactual memory unit tests: {passed} passed, {failed} failed")
    print(f"{'='*60}")
    for e in errors:
        print(f"  {e}")
    if failed == 0:
        print("  ALL TESTS PASSED")

    return {"passed": passed, "failed": failed, "errors": errors}


# ---------------------------------------------------------------------------
# CLI entry point (standalone + cron)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Counterfactual Memory — L4 Piece 5")
    parser.add_argument("--test", action="store_true", help="Run unit tests")
    parser.add_argument("--revisit", action="store_true",
                        help="Run quarterly revisit cycle")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="Dry run (no modifications)")
    parser.add_argument("--no-llm", dest="no_llm", action="store_true",
                        help="Skip LLM analysis during revisit")
    parser.add_argument("--capture", action="store_true",
                        help="Capture a new counterfactual decision")
    parser.add_argument("--decision-memory-id", dest="decision_memory_id",
                        type=int, default=None,
                        help="Memory ID recording this decision (required with --capture)")
    parser.add_argument("--topic", default=None,
                        help="Human-readable description of the decision")
    parser.add_argument("--chosen-path", dest="chosen_path", default=None,
                        help="What was actually chosen")
    parser.add_argument("--alternatives", default=None,
                        help="JSON array of {path, predicted_outcome, rationale} alternatives")
    parser.add_argument("--council-weight", dest="council_weight",
                        type=float, default=None,
                        help="0.0-1.0 decision weight score")
    parser.add_argument("--criteria", default=None,
                        help="When to revisit this decision")
    parser.add_argument("--revisit-days", dest="revisit_days",
                        type=int, default=DEFAULT_REVISIT_INTERVAL,
                        help=f"Days between revisits (default {DEFAULT_REVISIT_INTERVAL})")
    parser.add_argument("--captured-by", dest="captured_by", default="manual",
                        help=f"Provenance tag. Valid: {sorted(VALID_CAPTURED_BY)} (default 'manual')")
    parser.add_argument("db_path", nargs="?", default=None,
                        help="Path to memory database")

    args = parser.parse_args()

    if args.test:
        result = unit_test_counterfactual_memory()
        sys.exit(0 if result["failed"] == 0 else 1)
    elif args.capture:
        if not args.db_path:
            print("ERROR: db_path required for --capture", file=sys.stderr)
            sys.exit(1)
        if args.decision_memory_id is None:
            print("ERROR: --decision-memory-id required for --capture", file=sys.stderr)
            sys.exit(1)
        if not args.topic or not args.chosen_path or args.alternatives is None:
            print("ERROR: --topic, --chosen-path, and --alternatives required for --capture",
                  file=sys.stderr)
            sys.exit(1)

        # Parse --alternatives at the CLI boundary for a clean error envelope.
        try:
            alternatives = json.loads(args.alternatives)
        except json.JSONDecodeError as e:
            result = {"status": "error", "message": f"--alternatives is not valid JSON: {e}"}
            print(json.dumps(result, indent=2))
            sys.exit(1)

        ensure_counterfactual_table(args.db_path)
        conn = _get_conn(args.db_path)
        try:
            result = capture_counterfactual(
                conn=conn,
                decision_memory_id=args.decision_memory_id,
                topic=args.topic,
                chosen_path=args.chosen_path,
                alternative_paths=alternatives,
                council_weight_score=args.council_weight,
                criteria_for_revisit=args.criteria,
                revisit_interval_days=args.revisit_days,
                captured_by=args.captured_by,
            )
        finally:
            conn.close()
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["status"] == "ok" else 1)
    elif args.revisit:
        if not args.db_path:
            print("ERROR: db_path required for --revisit", file=sys.stderr)
            sys.exit(1)
        result = run_quarterly_revisit(
            db_path=args.db_path,
            dry_run=args.dry_run,
            use_llm=not args.no_llm,
        )
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["status"] == "ok" else 1)
    else:
        parser.print_help()
