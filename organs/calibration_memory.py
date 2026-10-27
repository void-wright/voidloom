#!/usr/bin/env python3
"""Calibration Memory — Wizard Library L4 Piece 4.

Tracks every numeric prediction Claude makes (time, cost, yield, recovery
rate, character counts, success probability) alongside the actual outcome
when observable. Over time, the library learns chronic miscalibrations per
domain and prediction_kind. Future predictions get auto-corrected.

Public API
----------
``log_prediction(conn, prediction_value, prediction_unit, prediction_kind,
                 domain, ...) -> int``
    Record a numeric prediction. Returns calibration_log entry ID.

``log_outcome(conn, entry_id, actual_value, ...) -> dict``
    Fill in the actual outcome for a previously logged prediction.
    Computes and stores the ratio (actual/predicted).

``compute_correction_factor(conn, domain, prediction_kind, ...) -> dict``
    Compute the weighted-median correction factor for a (domain, kind) pair.
    Returns correction_factor, confidence, sample_size, percentiles.

``suggest_corrected_estimate(conn, raw_estimate, domain, prediction_kind) -> dict``
    Apply correction factor to a raw estimate. Returns raw, corrected,
    factor, confidence, sample_size, note.

``get_calibration_stats(conn, domain=None, prediction_kind=None, ...) -> dict``
    Aggregate calibration statistics. Domain/kind filters optional.

``refresh_factor_cache(conn, domain=None, prediction_kind=None, ...) -> dict``
    Recompute and cache correction factors in calibration_factors table.

``seed_founding_data(conn) -> dict``
    Pre-populate calibration_log with the founding session data (memory #3972).

``is_calibration_migration_applied(conn) -> bool``
    Check if migration 014 has been applied.

``apply_calibration_migration(db_path, migration_sql_path) -> dict``
    Idempotent migration runner.

``unit_test_calibration_memory()``
    Self-contained unit tests (21 tests).

Phase 0 memories: #3972 (founding calibration data), #3951 (ACE calibration
delta tracking), #3929 (L2-to-L4 forward pointer map).
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

# Valid enum values — must match migration_014 CHECK constraints.
VALID_PREDICTION_UNITS = frozenset({
    "hours", "minutes", "seconds", "days", "weeks",
    "dollars", "percent", "count", "probability",
    "lines_of_code", "tokens", "bytes",
})

VALID_PREDICTION_KINDS = frozenset({
    "time", "cost", "yield", "recovery", "character_count",
    "success_probability", "iteration_count", "size",
    "latency", "throughput", "score",
})


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------

def is_calibration_migration_applied(conn: sqlite3.Connection) -> bool:
    """Check whether the calibration_log table exists."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='calibration_log'"
    ).fetchone()
    return row is not None


def apply_calibration_migration(
    db_path: str,
    migration_sql_path: str,
) -> Dict[str, Any]:
    """Apply migration_014 idempotently. Returns status dict."""
    t0 = time.time()
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")

    if is_calibration_migration_applied(conn):
        conn.close()
        return {"status": "already-applied", "time_ms": int((time.time() - t0) * 1000)}

    try:
        sql_text = Path(migration_sql_path).read_text()
        conn.executescript(sql_text)
        conn.commit()
    except Exception as e:
        conn.close()
        return {"status": "error", "message": str(e), "time_ms": int((time.time() - t0) * 1000)}

    # Verify
    if not is_calibration_migration_applied(conn):
        conn.close()
        return {"status": "error", "message": "Table not found after migration",
                "time_ms": int((time.time() - t0) * 1000)}

    conn.close()
    return {"status": "ok", "time_ms": int((time.time() - t0) * 1000)}


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def log_prediction(
    conn: sqlite3.Connection,
    prediction_value: float,
    prediction_unit: str,
    prediction_kind: str,
    domain: str,
    context: Optional[str] = None,
    predicted_at: Optional[str] = None,
    source_memory_id: Optional[int] = None,
    session_id: Optional[str] = None,
    notes: Optional[str] = None,
    tags: Optional[str] = None,
) -> int:
    """Record a numeric prediction. Returns calibration_log entry ID.

    Parameters
    ----------
    prediction_value : float
        The numeric prediction (e.g. 80.0 for "80 hours").
    prediction_unit : str
        Unit of the prediction. Must be in VALID_PREDICTION_UNITS.
    prediction_kind : str
        What type of thing is being predicted. Must be in VALID_PREDICTION_KINDS.
    domain : str
        Hierarchical domain key (e.g. 'ace-loop-construction',
        'content-generation/blog').
    context : str, optional
        Free-text description of what is being predicted.
    predicted_at : str, optional
        ISO timestamp. Defaults to now.
    source_memory_id : int, optional
        Link to the memory where this prediction was made.
    session_id : str, optional
        Session identifier for grouping.
    notes : str, optional
        Additional notes.
    tags : str, optional
        Comma-separated tags.

    Returns
    -------
    int
        The ID of the newly created calibration_log entry.

    Raises
    ------
    ValueError
        If any enum value is invalid, or prediction_value is not positive.
    """
    if prediction_unit not in VALID_PREDICTION_UNITS:
        raise ValueError(
            f"Invalid prediction_unit '{prediction_unit}'. "
            f"Valid: {sorted(VALID_PREDICTION_UNITS)}"
        )
    if prediction_kind not in VALID_PREDICTION_KINDS:
        raise ValueError(
            f"Invalid prediction_kind '{prediction_kind}'. "
            f"Valid: {sorted(VALID_PREDICTION_KINDS)}"
        )
    if prediction_value <= 0:
        raise ValueError(f"prediction_value must be positive, got {prediction_value}")
    if not domain or not domain.strip():
        raise ValueError("domain must be a non-empty string")

    ts = predicted_at or datetime.now().isoformat()

    cursor = conn.execute(
        """INSERT INTO calibration_log
           (prediction_value, prediction_unit, prediction_kind, domain,
            context, predicted_at, source_memory_id, session_id, notes, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (prediction_value, prediction_unit, prediction_kind, domain,
         context, ts, source_memory_id, session_id, notes, tags),
    )
    conn.commit()
    return cursor.lastrowid


def log_outcome(
    conn: sqlite3.Connection,
    entry_id: int,
    actual_value: float,
    actual_at: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Fill in the actual outcome for a previously logged prediction.

    Computes and stores the ratio (actual/predicted). A ratio < 1.0 means
    the prediction was an overestimate; > 1.0 means underestimate.

    Parameters
    ----------
    entry_id : int
        The calibration_log ID from log_prediction().
    actual_value : float
        The observed actual value.
    actual_at : str, optional
        ISO timestamp of when the outcome was observed. Defaults to now.
    notes : str, optional
        Additional notes about the outcome. Appended to existing notes.

    Returns
    -------
    dict
        Contains entry_id, prediction_value, actual_value, ratio, domain,
        prediction_kind.

    Raises
    ------
    ValueError
        If entry_id not found, actual_value is not positive, or outcome
        was already logged.
    """
    row = conn.execute(
        "SELECT prediction_value, actual_value, domain, prediction_kind, notes "
        "FROM calibration_log WHERE id = ?",
        (entry_id,),
    ).fetchone()

    if row is None:
        raise ValueError(f"Calibration entry {entry_id} not found")

    prediction_value, existing_actual, domain, prediction_kind, existing_notes = row

    if existing_actual is not None:
        raise ValueError(
            f"Calibration entry {entry_id} already has an outcome "
            f"(actual={existing_actual})"
        )

    if actual_value <= 0:
        raise ValueError(f"actual_value must be positive, got {actual_value}")

    ratio = actual_value / prediction_value
    ts = actual_at or datetime.now().isoformat()

    # Append notes if both old and new exist.
    combined_notes = existing_notes or ""
    if notes:
        combined_notes = f"{combined_notes}\n[outcome] {notes}".strip()

    conn.execute(
        """UPDATE calibration_log
           SET actual_value = ?, actual_at = ?, ratio = ?,
               notes = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (actual_value, ts, ratio, combined_notes if combined_notes else None, entry_id),
    )
    conn.commit()

    return {
        "entry_id": entry_id,
        "prediction_value": prediction_value,
        "actual_value": actual_value,
        "ratio": round(ratio, 6),
        "domain": domain,
        "prediction_kind": prediction_kind,
        "interpretation": _interpret_ratio(ratio),
    }


def _interpret_ratio(ratio: float) -> str:
    """Human-readable interpretation of a prediction ratio."""
    if ratio < 0.1:
        return f"Massive overestimate ({1/ratio:.1f}x over)"
    elif ratio < 0.5:
        return f"Significant overestimate ({1/ratio:.1f}x over)"
    elif ratio < 0.8:
        return f"Moderate overestimate ({1/ratio:.1f}x over)"
    elif ratio < 1.2:
        return "Well calibrated"
    elif ratio < 2.0:
        return f"Moderate underestimate ({ratio:.1f}x under)"
    elif ratio < 5.0:
        return f"Significant underestimate ({ratio:.1f}x under)"
    else:
        return f"Massive underestimate ({ratio:.1f}x under)"


def compute_correction_factor(
    conn: sqlite3.Connection,
    domain: str,
    prediction_kind: str,
    lookback_days: int = 180,
    min_samples: int = 1,
) -> Dict[str, Any]:
    """Compute the weighted-median correction factor for a (domain, kind) pair.

    Recent outcomes are weighted more heavily via exponential decay (half-life
    = lookback_days / 3). The correction factor is the weighted median of all
    ratios in the lookback window.

    Parameters
    ----------
    domain : str
        Exact domain match, OR prefix match (e.g. 'ace-loop' matches
        'ace-loop-construction').
    prediction_kind : str
        Exact match on prediction_kind.
    lookback_days : int
        How far back to look (default 180 days).
    min_samples : int
        Minimum number of resolved predictions required for a confident
        factor. If below this, returns factor=1.0 with confidence=0.

    Returns
    -------
    dict
        correction_factor, confidence, sample_size, weighted_median,
        p25, p75, domain, prediction_kind.
    """
    cutoff = (datetime.now() - timedelta(days=lookback_days)).isoformat()
    half_life_days = max(lookback_days / 3, 7)

    # Fetch all resolved predictions in the lookback window.
    # Support both exact match and prefix match on domain.
    rows = conn.execute(
        """SELECT ratio, predicted_at
           FROM calibration_log
           WHERE (domain = ? OR domain LIKE ? || '/%')
             AND prediction_kind = ?
             AND ratio IS NOT NULL
             AND predicted_at >= ?
           ORDER BY predicted_at ASC""",
        (domain, domain, prediction_kind, cutoff),
    ).fetchall()

    if len(rows) < min_samples:
        return {
            "correction_factor": 1.0,
            "confidence": 0.0,
            "sample_size": len(rows),
            "weighted_median": None,
            "p25": None,
            "p75": None,
            "domain": domain,
            "prediction_kind": prediction_kind,
            "note": f"Insufficient data ({len(rows)} < {min_samples} samples)",
        }

    now = datetime.now()
    weighted_ratios = []

    for ratio, predicted_at_str in rows:
        try:
            predicted_at = datetime.fromisoformat(predicted_at_str)
        except (ValueError, TypeError):
            predicted_at = now

        days_ago = max((now - predicted_at).total_seconds() / 86400, 0)
        weight = math.exp(-math.log(2) * days_ago / half_life_days)
        weighted_ratios.append((ratio, weight))

    # Weighted median computation.
    weighted_ratios.sort(key=lambda x: x[0])
    total_weight = sum(w for _, w in weighted_ratios)
    cumulative = 0.0
    weighted_median = weighted_ratios[0][0]
    p25 = weighted_ratios[0][0]
    p75 = weighted_ratios[-1][0]
    found_median = False
    found_p25 = False
    found_p75 = False

    for ratio, weight in weighted_ratios:
        cumulative += weight
        fraction = cumulative / total_weight
        if not found_p25 and fraction >= 0.25:
            p25 = ratio
            found_p25 = True
        if not found_median and fraction >= 0.5:
            weighted_median = ratio
            found_median = True
        if not found_p75 and fraction >= 0.75:
            p75 = ratio
            found_p75 = True

    # Confidence: f(sample_size, consistency).
    # Higher sample size = higher confidence. Lower IQR spread = higher confidence.
    iqr = p75 - p25 if p75 > p25 else 0.01
    size_conf = min(1.0, len(rows) / 10)  # saturates at 10 samples
    consistency_conf = min(1.0, 1.0 / (1.0 + iqr))
    confidence = round(size_conf * 0.6 + consistency_conf * 0.4, 4)

    return {
        "correction_factor": round(weighted_median, 6),
        "confidence": confidence,
        "sample_size": len(rows),
        "weighted_median": round(weighted_median, 6),
        "p25": round(p25, 6),
        "p75": round(p75, 6),
        "domain": domain,
        "prediction_kind": prediction_kind,
    }


def suggest_corrected_estimate(
    conn: sqlite3.Connection,
    raw_estimate: float,
    domain: str,
    prediction_kind: str,
    prediction_unit: str = "hours",
    lookback_days: int = 180,
) -> Dict[str, Any]:
    """Apply correction factor to a raw estimate.

    This is the main consumer-facing function. Claude calls this BEFORE
    surfacing a time/cost/yield estimate to Drew.

    Parameters
    ----------
    raw_estimate : float
        Claude's raw numeric prediction.
    domain : str
        Domain key for correction factor lookup.
    prediction_kind : str
        What type of thing is being predicted.
    prediction_unit : str
        Unit label for display (default 'hours').
    lookback_days : int
        Lookback window for correction factor computation.

    Returns
    -------
    dict
        raw, corrected, factor, confidence, sample_size, unit, note.
    """
    factor_info = compute_correction_factor(
        conn, domain, prediction_kind, lookback_days=lookback_days,
    )

    correction = factor_info["correction_factor"]
    corrected = raw_estimate * correction

    if factor_info["confidence"] < 0.1:
        note = (
            f"No calibration data for {domain}/{prediction_kind}. "
            f"Returning raw estimate uncorrected."
        )
    else:
        note = (
            f"Historical {domain}/{prediction_kind}: "
            f"{factor_info['sample_size']} samples, "
            f"median ratio {correction:.2f}x "
            f"(p25={factor_info['p25']:.2f}, p75={factor_info['p75']:.2f}), "
            f"confidence {factor_info['confidence']:.0%}"
        )

    return {
        "raw": raw_estimate,
        "corrected": round(corrected, 4),
        "factor": correction,
        "confidence": factor_info["confidence"],
        "sample_size": factor_info["sample_size"],
        "unit": prediction_unit,
        "note": note,
    }


def get_calibration_stats(
    conn: sqlite3.Connection,
    domain: Optional[str] = None,
    prediction_kind: Optional[str] = None,
    lookback_days: int = 365,
) -> Dict[str, Any]:
    """Aggregate calibration statistics.

    Returns per-domain and per-kind breakdowns, with overall health metrics.
    """
    cutoff = (datetime.now() - timedelta(days=lookback_days)).isoformat()

    # Base counts.
    total_predictions = conn.execute(
        "SELECT COUNT(*) FROM calibration_log"
    ).fetchone()[0]
    total_resolved = conn.execute(
        "SELECT COUNT(*) FROM calibration_log WHERE ratio IS NOT NULL"
    ).fetchone()[0]
    total_pending = total_predictions - total_resolved

    # Per-domain breakdown.
    domain_clause = ""
    params: List[Any] = [cutoff]
    if domain:
        domain_clause = "AND (domain = ? OR domain LIKE ? || '/%')"
        params.extend([domain, domain])
    if prediction_kind:
        domain_clause += " AND prediction_kind = ?"
        params.append(prediction_kind)

    by_domain = {}
    rows = conn.execute(
        f"""SELECT domain, COUNT(*), AVG(ratio),
                   MIN(ratio), MAX(ratio)
            FROM calibration_log
            WHERE ratio IS NOT NULL AND predicted_at >= ?
            {domain_clause}
            GROUP BY domain
            ORDER BY COUNT(*) DESC""",
        params,
    ).fetchall()
    for row in rows:
        by_domain[row[0]] = {
            "count": row[1],
            "avg_ratio": round(row[2], 4) if row[2] else None,
            "min_ratio": round(row[3], 4) if row[3] else None,
            "max_ratio": round(row[4], 4) if row[4] else None,
        }

    # Per-kind breakdown.
    by_kind = {}
    params2: List[Any] = [cutoff]
    kind_clause = ""
    if domain:
        kind_clause = "AND (domain = ? OR domain LIKE ? || '/%')"
        params2.extend([domain, domain])
    if prediction_kind:
        kind_clause += " AND prediction_kind = ?"
        params2.append(prediction_kind)

    rows2 = conn.execute(
        f"""SELECT prediction_kind, COUNT(*), AVG(ratio),
                   MIN(ratio), MAX(ratio)
            FROM calibration_log
            WHERE ratio IS NOT NULL AND predicted_at >= ?
            {kind_clause}
            GROUP BY prediction_kind
            ORDER BY COUNT(*) DESC""",
        params2,
    ).fetchall()
    for row in rows2:
        by_kind[row[0]] = {
            "count": row[1],
            "avg_ratio": round(row[2], 4) if row[2] else None,
            "min_ratio": round(row[3], 4) if row[3] else None,
            "max_ratio": round(row[4], 4) if row[4] else None,
        }

    # Chronic miscalibration detection.
    # Any (domain, kind) pair with avg ratio < 0.3 or > 3.0 is flagged.
    chronic_miscalibrations = []
    rows3 = conn.execute(
        """SELECT domain, prediction_kind, COUNT(*), AVG(ratio)
           FROM calibration_log
           WHERE ratio IS NOT NULL AND predicted_at >= ?
           GROUP BY domain, prediction_kind
           HAVING COUNT(*) >= 2 AND (AVG(ratio) < 0.3 OR AVG(ratio) > 3.0)
           ORDER BY ABS(AVG(ratio) - 1.0) DESC""",
        (cutoff,),
    ).fetchall()
    for row in rows3:
        avg_r = row[3]
        chronic_miscalibrations.append({
            "domain": row[0],
            "prediction_kind": row[1],
            "sample_count": row[2],
            "avg_ratio": round(avg_r, 4),
            "interpretation": _interpret_ratio(avg_r),
            "suggested_action": (
                f"Multiply {row[1]} estimates for {row[0]} by {avg_r:.2f}x"
            ),
        })

    # Recent entries (last 10).
    recent = conn.execute(
        """SELECT id, prediction_value, prediction_unit, prediction_kind,
                  domain, actual_value, ratio, predicted_at, context
           FROM calibration_log
           ORDER BY predicted_at DESC
           LIMIT 10"""
    ).fetchall()
    recent_entries = []
    for r in recent:
        recent_entries.append({
            "id": r[0],
            "prediction_value": r[1],
            "prediction_unit": r[2],
            "prediction_kind": r[3],
            "domain": r[4],
            "actual_value": r[5],
            "ratio": round(r[6], 4) if r[6] else None,
            "predicted_at": r[7],
            "context": r[8],
        })

    return {
        "total_predictions": total_predictions,
        "total_resolved": total_resolved,
        "total_pending": total_pending,
        "resolution_rate": round(total_resolved / max(total_predictions, 1), 4),
        "by_domain": by_domain,
        "by_kind": by_kind,
        "chronic_miscalibrations": chronic_miscalibrations,
        "recent_entries": recent_entries,
        "lookback_days": lookback_days,
    }


def refresh_factor_cache(
    conn: sqlite3.Connection,
    domain: Optional[str] = None,
    prediction_kind: Optional[str] = None,
    lookback_days: int = 180,
) -> Dict[str, Any]:
    """Recompute and cache correction factors in calibration_factors table.

    Called during sleep loop or on-demand. Refreshes all (domain, kind)
    pairs with resolved predictions, or a specific pair if given.

    Returns
    -------
    dict
        Status dict with count of factors refreshed.
    """
    t0 = time.time()

    if domain and prediction_kind:
        pairs = [(domain, prediction_kind)]
    else:
        # Discover all distinct (domain, kind) pairs with resolved predictions.
        rows = conn.execute(
            "SELECT DISTINCT domain, prediction_kind FROM calibration_log WHERE ratio IS NOT NULL"
        ).fetchall()
        pairs = [(r[0], r[1]) for r in rows]

    refreshed = 0
    for d, k in pairs:
        factor_info = compute_correction_factor(
            conn, d, k, lookback_days=lookback_days,
        )
        if factor_info["sample_size"] == 0:
            continue

        conn.execute(
            """INSERT OR REPLACE INTO calibration_factors
               (domain, prediction_kind, correction_factor, confidence,
                sample_size, weighted_median, p25, p75,
                last_computed_at, lookback_days)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)""",
            (d, k, factor_info["correction_factor"], factor_info["confidence"],
             factor_info["sample_size"], factor_info["weighted_median"],
             factor_info["p25"], factor_info["p75"], lookback_days),
        )
        refreshed += 1

    conn.commit()
    return {
        "status": "ok",
        "factors_refreshed": refreshed,
        "pairs_checked": len(pairs),
        "time_ms": int((time.time() - t0) * 1000),
    }


# ---------------------------------------------------------------------------
# Seed founding data
# ---------------------------------------------------------------------------

def seed_founding_data(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Pre-populate calibration_log with the founding session data.

    Memory #3972: L2 build. Plan estimated ~4 weeks (80-120 hours).
    Actual: 4/5 components in ~5.25 hours. Ratio: 0.066 (80hr) to 0.044
    (120hr), i.e. ~15-23x overestimate.

    Also seeds:
    - L3 time estimate: predicted 8-12 weeks, actual ~5.5 hours (extrapolated
      from L2 velocity).
    - Per-component iteration count: predicted 2.0 avg, actual trending 1.0.

    This function is idempotent: checks for existing entries before inserting.

    Returns
    -------
    dict
        Status dict with count of entries seeded.
    """
    # Check if founding data already exists.
    existing = conn.execute(
        "SELECT COUNT(*) FROM calibration_log WHERE source_memory_id = 3972"
    ).fetchone()[0]
    if existing > 0:
        return {"status": "already-seeded", "entries": existing}

    seeded = 0
    founding_ts = "2026-05-26T11:30:00"
    outcome_ts = "2026-05-26T16:44:00"

    # Entry 1: L2 build time — lower bound estimate (80 hours)
    cursor = conn.execute(
        """INSERT INTO calibration_log
           (prediction_value, prediction_unit, prediction_kind, domain,
            context, predicted_at, actual_value, actual_at, ratio,
            source_memory_id, notes, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (80.0, "hours", "time", "ace-loop-construction",
         "Wizard Library Level 2 build (5 components) — Plan #5 lower bound",
         founding_ts, 5.25, outcome_ts, 5.25 / 80.0,
         3972,
         "First calibration entry. Plan estimated 4 weeks (80-120hr). "
         "Actual: 4/5 components in 5.25hr single session. "
         "15x overestimate driven by: (1) parallel ACE subagent velocity, "
         "(2) pattern accumulation across components, "
         "(3) zero idle time with orchestrator.",
         "founding,wizard-library,l2-build,parallel-ace"),
    )
    seeded += 1

    # Entry 2: L2 build time — upper bound estimate (120 hours)
    conn.execute(
        """INSERT INTO calibration_log
           (prediction_value, prediction_unit, prediction_kind, domain,
            context, predicted_at, actual_value, actual_at, ratio,
            source_memory_id, notes, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (120.0, "hours", "time", "ace-loop-construction",
         "Wizard Library Level 2 build (5 components) — Plan #5 upper bound",
         founding_ts, 5.25, outcome_ts, 5.25 / 120.0,
         3972,
         "Upper bound estimate. Ratio 0.044 = 23x overestimate.",
         "founding,wizard-library,l2-build,parallel-ace"),
    )
    seeded += 1

    # Entry 3: Per-component iteration count prediction
    conn.execute(
        """INSERT INTO calibration_log
           (prediction_value, prediction_unit, prediction_kind, domain,
            context, predicted_at, actual_value, actual_at, ratio,
            source_memory_id, notes, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (2.0, "count", "iteration_count", "ace-loop-construction",
         "Average ACE iterations per L2 component — expected based on typical ACE loops",
         founding_ts, 1.6, outcome_ts, 1.6 / 2.0,
         3972,
         "C1=2 iter, C2=2 iter, C4=3 iter, C5=1 iter, avg=2.0 but "
         "C5 achieved first-ever iter-1 closure. Trending toward 1.0 "
         "with pattern accumulation.",
         "founding,wizard-library,iteration-count,ace-loop"),
    )
    seeded += 1

    # Entry 4: L3 code-work time estimate (extrapolated)
    conn.execute(
        """INSERT INTO calibration_log
           (prediction_value, prediction_unit, prediction_kind, domain,
            context, predicted_at, source_memory_id, notes, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (80.0, "hours", "time", "ace-loop-construction/l3",
         "Wizard Library Level 3 build (8 components) — Plan #5 estimate "
         "corrected from 8-12 weeks to ~2-3 days code using L2 velocity data",
         "2026-05-26T20:45:00",
         3972,
         "Pending outcome. L2 velocity suggests ~16-24 hours code-only. "
         "Validation periods (2 weeks) are incompressible.",
         "wizard-library,l3-build,pending"),
    )
    seeded += 1

    conn.commit()
    return {"status": "ok", "entries_seeded": seeded}


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

def unit_test_calibration_memory():
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

        # Apply calibration_log + calibration_factors tables
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calibration_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_value REAL NOT NULL,
                prediction_unit TEXT NOT NULL,
                prediction_kind TEXT NOT NULL,
                domain TEXT NOT NULL,
                context TEXT DEFAULT NULL,
                predicted_at TEXT NOT NULL DEFAULT (datetime('now')),
                actual_value REAL DEFAULT NULL,
                actual_at TEXT DEFAULT NULL,
                ratio REAL DEFAULT NULL,
                source_memory_id INTEGER DEFAULT NULL,
                session_id TEXT DEFAULT NULL,
                notes TEXT DEFAULT NULL,
                tags TEXT DEFAULT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cal_domain ON calibration_log(domain)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cal_kind ON calibration_log(prediction_kind)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cal_ratio ON calibration_log(ratio) WHERE ratio IS NOT NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cal_predicted_at ON calibration_log(predicted_at)")
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cal_domain_kind
            ON calibration_log(domain, prediction_kind) WHERE ratio IS NOT NULL
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS calibration_factors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                prediction_kind TEXT NOT NULL,
                correction_factor REAL NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.0,
                sample_size INTEGER NOT NULL DEFAULT 0,
                weighted_median REAL DEFAULT NULL,
                p25 REAL DEFAULT NULL,
                p75 REAL DEFAULT NULL,
                last_computed_at TEXT NOT NULL DEFAULT (datetime('now')),
                lookback_days INTEGER NOT NULL DEFAULT 180,
                UNIQUE(domain, prediction_kind)
            )
        """)

        return conn

    print("\n=== Calibration Memory Unit Tests ===\n")

    # T1: is_calibration_migration_applied
    try:
        conn = _setup_db()
        _assert(
            is_calibration_migration_applied(conn),
            "T1: migration detection (applied)",
        )
        conn.close()

        conn2 = sqlite3.connect(":memory:")
        conn2.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        _assert(
            not is_calibration_migration_applied(conn2),
            "T1b: migration detection (not applied)",
        )
        conn2.close()
    except Exception as e:
        _assert(False, f"T1: migration detection", str(e))

    # T2: log_prediction — happy path
    try:
        conn = _setup_db()
        entry_id = log_prediction(
            conn, 80.0, "hours", "time", "ace-loop-construction",
            context="L2 build estimate",
        )
        _assert(entry_id > 0, "T2: log_prediction returns positive ID")
        row = conn.execute("SELECT * FROM calibration_log WHERE id = ?", (entry_id,)).fetchone()
        _assert(row is not None, "T2b: prediction stored in DB")
        conn.close()
    except Exception as e:
        _assert(False, f"T2: log_prediction", str(e))

    # T3: log_prediction — validation errors
    try:
        conn = _setup_db()
        caught = False
        try:
            log_prediction(conn, 80.0, "INVALID_UNIT", "time", "test")
        except ValueError:
            caught = True
        _assert(caught, "T3a: rejects invalid prediction_unit")

        caught = False
        try:
            log_prediction(conn, 80.0, "hours", "INVALID_KIND", "test")
        except ValueError:
            caught = True
        _assert(caught, "T3b: rejects invalid prediction_kind")

        caught = False
        try:
            log_prediction(conn, -5.0, "hours", "time", "test")
        except ValueError:
            caught = True
        _assert(caught, "T3c: rejects negative prediction_value")

        caught = False
        try:
            log_prediction(conn, 5.0, "hours", "time", "")
        except ValueError:
            caught = True
        _assert(caught, "T3d: rejects empty domain")
        conn.close()
    except Exception as e:
        _assert(False, f"T3: validation", str(e))

    # T4: log_outcome — happy path
    try:
        conn = _setup_db()
        eid = log_prediction(conn, 80.0, "hours", "time", "test-domain")
        result = log_outcome(conn, eid, 5.25)
        _assert(
            abs(result["ratio"] - (5.25 / 80.0)) < 0.0001,
            "T4: ratio computed correctly",
            f"expected {5.25/80.0:.6f}, got {result['ratio']}"
        )
        _assert(
            "overestimate" in result["interpretation"].lower(),
            "T4b: interpretation says overestimate",
        )
        conn.close()
    except Exception as e:
        _assert(False, f"T4: log_outcome", str(e))

    # T5: log_outcome — double-fill rejection
    try:
        conn = _setup_db()
        eid = log_prediction(conn, 10.0, "hours", "time", "test")
        log_outcome(conn, eid, 12.0)
        caught = False
        try:
            log_outcome(conn, eid, 15.0)
        except ValueError as ve:
            caught = "already" in str(ve).lower()
        _assert(caught, "T5: rejects double outcome fill")
        conn.close()
    except Exception as e:
        _assert(False, f"T5: double-fill", str(e))

    # T6: log_outcome — not found
    try:
        conn = _setup_db()
        caught = False
        try:
            log_outcome(conn, 99999, 5.0)
        except ValueError:
            caught = True
        _assert(caught, "T6: rejects outcome for non-existent entry")
        conn.close()
    except Exception as e:
        _assert(False, f"T6: not found", str(e))

    # T7: compute_correction_factor — single sample
    try:
        conn = _setup_db()
        eid = log_prediction(conn, 100.0, "hours", "time", "domain-a")
        log_outcome(conn, eid, 50.0)
        factor = compute_correction_factor(conn, "domain-a", "time")
        _assert(
            abs(factor["correction_factor"] - 0.5) < 0.01,
            "T7: single-sample correction factor",
            f"got {factor['correction_factor']}"
        )
        _assert(factor["sample_size"] == 1, "T7b: sample_size is 1")
        conn.close()
    except Exception as e:
        _assert(False, f"T7: single-sample factor", str(e))

    # T8: compute_correction_factor — multiple samples, median computation
    try:
        conn = _setup_db()
        # Three predictions: ratios 0.5, 0.3, 0.7. Median should be ~0.5.
        for pred, actual in [(100.0, 50.0), (100.0, 30.0), (100.0, 70.0)]:
            eid = log_prediction(conn, pred, "hours", "time", "domain-b")
            log_outcome(conn, eid, actual)

        factor = compute_correction_factor(conn, "domain-b", "time")
        _assert(
            abs(factor["correction_factor"] - 0.5) < 0.05,
            "T8: multi-sample weighted median",
            f"got {factor['correction_factor']}"
        )
        _assert(factor["sample_size"] == 3, "T8b: sample_size is 3")
        _assert(factor["confidence"] > 0.0, "T8c: confidence > 0")
        conn.close()
    except Exception as e:
        _assert(False, f"T8: multi-sample factor", str(e))

    # T9: compute_correction_factor — no data returns factor=1.0
    try:
        conn = _setup_db()
        factor = compute_correction_factor(conn, "nonexistent", "time")
        _assert(
            factor["correction_factor"] == 1.0,
            "T9: no-data returns factor 1.0"
        )
        _assert(factor["confidence"] == 0.0, "T9b: no-data confidence is 0")
        conn.close()
    except Exception as e:
        _assert(False, f"T9: no-data factor", str(e))

    # T10: suggest_corrected_estimate — applies factor
    try:
        conn = _setup_db()
        eid = log_prediction(conn, 100.0, "hours", "time", "domain-c")
        log_outcome(conn, eid, 25.0)  # ratio = 0.25
        suggestion = suggest_corrected_estimate(conn, 40.0, "domain-c", "time")
        _assert(
            abs(suggestion["corrected"] - 10.0) < 0.5,
            "T10: corrected estimate applies factor",
            f"raw=40, factor={suggestion['factor']}, corrected={suggestion['corrected']}"
        )
        _assert("note" in suggestion, "T10b: note field present")
        conn.close()
    except Exception as e:
        _assert(False, f"T10: suggest_corrected_estimate", str(e))

    # T11: suggest_corrected_estimate — no calibration data
    try:
        conn = _setup_db()
        suggestion = suggest_corrected_estimate(conn, 40.0, "unknown-domain", "time")
        _assert(
            suggestion["corrected"] == 40.0,
            "T11: no calibration data returns raw unchanged",
        )
        _assert(
            "no calibration" in suggestion["note"].lower(),
            "T11b: note mentions no calibration data",
        )
        conn.close()
    except Exception as e:
        _assert(False, f"T11: no-data suggestion", str(e))

    # T12: get_calibration_stats — comprehensive
    try:
        conn = _setup_db()
        for i in range(5):
            eid = log_prediction(
                conn, float(10 + i * 10), "hours", "time", "stats-domain"
            )
            log_outcome(conn, eid, float(5 + i * 3))
        # One pending prediction.
        log_prediction(conn, 100.0, "hours", "time", "stats-domain")

        stats = get_calibration_stats(conn)
        _assert(stats["total_predictions"] == 6, "T12a: total predictions")
        _assert(stats["total_resolved"] == 5, "T12b: total resolved")
        _assert(stats["total_pending"] == 1, "T12c: total pending")
        _assert("stats-domain" in stats["by_domain"], "T12d: domain in breakdown")
        _assert(len(stats["recent_entries"]) > 0, "T12e: recent entries populated")
        conn.close()
    except Exception as e:
        _assert(False, f"T12: calibration stats", str(e))

    # T13: chronic miscalibration detection
    try:
        conn = _setup_db()
        # Create a chronically overestimating domain (ratios all ~0.1).
        for i in range(3):
            eid = log_prediction(conn, 100.0, "hours", "time", "chronic-over")
            log_outcome(conn, eid, 10.0)

        stats = get_calibration_stats(conn)
        chronic = stats["chronic_miscalibrations"]
        _assert(
            any(c["domain"] == "chronic-over" for c in chronic),
            "T13: chronic miscalibration detected",
        )
        conn.close()
    except Exception as e:
        _assert(False, f"T13: chronic miscalibration", str(e))

    # T14: seed_founding_data — idempotent
    try:
        conn = _setup_db()
        # Insert a memory row so source_memory_id FK doesn't fail (memory #3972).
        conn.execute(
            "INSERT INTO memories (id, content, category) VALUES (3972, 'test', 'insight')"
        )
        result1 = seed_founding_data(conn)
        _assert(result1["status"] == "ok", "T14a: first seed succeeds")
        _assert(result1["entries_seeded"] >= 3, "T14b: at least 3 entries seeded")

        result2 = seed_founding_data(conn)
        _assert(result2["status"] == "already-seeded", "T14c: idempotent re-seed")
        conn.close()
    except Exception as e:
        _assert(False, f"T14: seed founding data", str(e))

    # T15: refresh_factor_cache
    try:
        conn = _setup_db()
        for i in range(5):
            eid = log_prediction(conn, 100.0, "hours", "time", "cache-test")
            log_outcome(conn, eid, 50.0 + i * 5)

        result = refresh_factor_cache(conn)
        _assert(result["factors_refreshed"] >= 1, "T15a: at least 1 factor cached")

        cached = conn.execute(
            "SELECT correction_factor, confidence, sample_size "
            "FROM calibration_factors WHERE domain = 'cache-test' AND prediction_kind = 'time'"
        ).fetchone()
        _assert(cached is not None, "T15b: factor in calibration_factors table")
        _assert(cached[2] == 5, "T15c: sample_size = 5")
        conn.close()
    except Exception as e:
        _assert(False, f"T15: refresh factor cache", str(e))

    # T16: domain prefix matching
    try:
        conn = _setup_db()
        eid1 = log_prediction(conn, 100.0, "hours", "time", "ace-loop-construction")
        log_outcome(conn, eid1, 10.0)
        eid2 = log_prediction(conn, 100.0, "hours", "time", "ace-loop-construction/l3")
        log_outcome(conn, eid2, 15.0)

        # Query with prefix should match both.
        factor = compute_correction_factor(conn, "ace-loop-construction", "time")
        _assert(
            factor["sample_size"] >= 2,
            "T16: domain prefix matching includes sub-domains",
            f"sample_size={factor['sample_size']}"
        )
        conn.close()
    except Exception as e:
        _assert(False, f"T16: domain prefix", str(e))

    # T17: _interpret_ratio ranges
    try:
        _assert(
            "massive overestimate" in _interpret_ratio(0.05).lower(),
            "T17a: ratio 0.05 = massive overestimate",
        )
        _assert(
            "well calibrated" in _interpret_ratio(1.0).lower(),
            "T17b: ratio 1.0 = well calibrated",
        )
        _assert(
            "massive underestimate" in _interpret_ratio(10.0).lower(),
            "T17c: ratio 10.0 = massive underestimate",
        )
        _assert(
            "moderate overestimate" in _interpret_ratio(0.6).lower(),
            "T17d: ratio 0.6 = moderate overestimate",
        )
    except Exception as e:
        _assert(False, f"T17: interpret_ratio", str(e))

    # T18: log_outcome with notes appended
    try:
        conn = _setup_db()
        eid = log_prediction(
            conn, 50.0, "hours", "time", "notes-test",
            notes="Initial estimate based on past experience"
        )
        result = log_outcome(conn, eid, 30.0, notes="Finished earlier due to caching")
        row = conn.execute(
            "SELECT notes FROM calibration_log WHERE id = ?", (eid,)
        ).fetchone()
        _assert(
            "[outcome]" in (row[0] or ""),
            "T18: outcome notes appended with [outcome] marker",
        )
        conn.close()
    except Exception as e:
        _assert(False, f"T18: notes append", str(e))

    # T19: correction factor with min_samples threshold
    try:
        conn = _setup_db()
        eid = log_prediction(conn, 100.0, "hours", "time", "min-samples-test")
        log_outcome(conn, eid, 50.0)
        factor = compute_correction_factor(
            conn, "min-samples-test", "time", min_samples=5
        )
        _assert(
            factor["correction_factor"] == 1.0,
            "T19: min_samples threshold returns 1.0 when insufficient data",
        )
        _assert(factor["confidence"] == 0.0, "T19b: confidence 0 when below min_samples")
        conn.close()
    except Exception as e:
        _assert(False, f"T19: min_samples threshold", str(e))

    # T20: multiple prediction kinds in same domain
    try:
        conn = _setup_db()
        eid1 = log_prediction(conn, 100.0, "hours", "time", "multi-kind")
        log_outcome(conn, eid1, 50.0)
        eid2 = log_prediction(conn, 10.0, "dollars", "cost", "multi-kind")
        log_outcome(conn, eid2, 15.0)

        f_time = compute_correction_factor(conn, "multi-kind", "time")
        f_cost = compute_correction_factor(conn, "multi-kind", "cost")
        _assert(
            abs(f_time["correction_factor"] - 0.5) < 0.01,
            "T20a: time factor correct in multi-kind domain",
        )
        _assert(
            abs(f_cost["correction_factor"] - 1.5) < 0.01,
            "T20b: cost factor correct in multi-kind domain",
        )
        conn.close()
    except Exception as e:
        _assert(False, f"T20: multi-kind", str(e))

    # T21: get_calibration_stats with filters
    try:
        conn = _setup_db()
        for i in range(3):
            eid = log_prediction(conn, 100.0, "hours", "time", "filtered-domain")
            log_outcome(conn, eid, 50.0 + i * 10)
        eid = log_prediction(conn, 50.0, "dollars", "cost", "other-domain")
        log_outcome(conn, eid, 100.0)

        stats = get_calibration_stats(conn, domain="filtered-domain")
        _assert(
            "filtered-domain" in stats["by_domain"],
            "T21a: filtered stats contain requested domain",
        )
        stats_kind = get_calibration_stats(conn, prediction_kind="cost")
        _assert(
            "cost" in stats_kind["by_kind"],
            "T21b: kind-filtered stats contain requested kind",
        )
        conn.close()
    except Exception as e:
        _assert(False, f"T21: filtered stats", str(e))

    # Summary
    total = passed + failed
    print(f"\n{'='*50}")
    print(f"Results: {passed}/{total} passed, {failed} failed")
    if errors:
        print("Failures:")
        for e in errors:
            print(f"  {e}")
    print(f"{'='*50}\n")

    return passed, failed


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        passed, failed = unit_test_calibration_memory()
        sys.exit(0 if failed == 0 else 1)
    else:
        print("Usage: python3 calibration_memory.py test")
        print("  Runs self-contained unit tests (21 tests).")
        sys.exit(0)
