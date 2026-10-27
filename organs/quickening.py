#!/usr/bin/env python3
"""The Quickening -- Voidloom Level-5 recall-tide sense (L5-L).

The brain feels the rise and fall of its OWN attention. Two cheap halves,
pure stdlib + numpy (numpy is already transitive via torch -> zero new install
surface). The council REJECTED any time-series foundation model for v1: 93 days
of batch-contaminated history with 58% of memories accessed <=2x ever punishes a
model. Moving-average + z-score gives >=90% of the value with zero new dependency.

Half (i) -- Spike detector (the alive half)
    ``detect_recall_spike(conn, scope, window=28)`` over ``access_log``:
    moving-average + z-score, computed PER-MEMORY and PER-TAG. ``z > 3`` mints a
    ``surprise``-type whisper (via ``whisper_network.capture_whisper``) and seeds
    a dream-pool prompt (recorded as a ``seed_bias``-shaped payload on
    ``quickening_log`` -- the SAME dream/whisper sink, never a parallel one).
    Stateless. Runs as a sleep-loop stage. Triggers WONDER, not prediction.

Half (ii) -- Predictive resurfacing (the hygiene half)
    ``compute_access_cadence(conn, scope)`` + a ``predicted_lapse`` flag fold into
    ``anti_recency.find_resurfacing_candidates`` -- flips the lens from reactive
    (already-lapsed) to predictive (about-to-lapse). Pure datetime.

NON-NEGOTIABLE DISCIPLINE (council)
-----------------------------------
  * FILTER MACHINE TRAFFIC before ANY mu/sigma math (load-bearing correctness).
    Cron + council recall storms create four-digit spikes; un-filtered, the
    detector forecasts cron schedules. We filter on THREE axes:
      1. ``access_type``   -- reuse dream_pipeline's real-session convention
                              (exclude cron/sleep/dream/probe/maintenance/machine
                              markers; forward-compatible with future tags).
      2. ``query_text``    -- the empirically-observed machine patterns on THIS
                              corpus (systematic 'memory id NNNN' sweeps,
                              digest/maintenance sentinels). access_type alone is
                              NOT enough: today's live storms wear a 'recall'/
                              'recall:hybrid' label, indistinguishable by type.
      3. sub-minute clustering -- a batch recall logs one top-k read per memory at
                              an IDENTICAL second; collapse a burst sharing
                              (query_text, minute) to ONE logical event so a
                              single human recall is not counted top_k times.
  * Detection vs forecasting geometry: spike DETECTION needs only a baseline ->
    works per-memory AND per-tag. Cadence/predictive needs history -> operates
    per-TAG/topic, NOT per-single-memory (too sparse: 58% of memories <=2x).
  * One sink, two sensors: L5-L use-time spike + L5-G encoding-time
    ``encoding_surprise`` are distinct detectors feeding the SAME salience/dream/
    whisper sink. REUSE the whisper + dream-seed interfaces. NEVER write the
    arousal spike into ``encoding_surprise`` (G is EPISTEMIC-only -- novelty +
    contradiction; arousal-surprise as salience is explicitly forbidden).
  * Stays pure-stdlib+numpy. No dependency-contract change.

Public API
----------
``ensure_quickening_migration(conn) -> bool``        idempotent table guard
``is_quickening_migration_applied(conn) -> bool``    presence check
``detect_recall_spike(conn, scope, window=28, ...) -> List[dict]``
``compute_access_cadence(conn, scope, ...) -> Dict[str, dict]``
``predicted_lapse(cadence, last_access, now=None, ...) -> bool``
``run_quickening_stage(conn, cfg=None, batch_id=None) -> dict``  (fail-soft)

CLI
---
::

    python3 quickening.py --unit-tests                 # self-test (synthetic DB)
    python3 quickening.py --detect <db_path>           # show current spikes
    python3 quickening.py --cadence <db_path>          # show per-tag cadences
    python3 quickening.py --stage <db_path>            # run the sleep-loop stage

Schema: 5.12-L5-L-quickening.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy is transitive via torch
    np = None  # the module degrades to a no-op stage; never crashes the cycle

logger = logging.getLogger(__name__)

# ============================================================
# Configuration (overridable via the sleep-loop cfg dict)
# ============================================================

# Kill-switch matching the Voidloom env family
# (VOIDLOOM_NO_PPR / _NO_DIVERSITY / _NO_TEMPORAL_RERANK / ...).
ENV_KILL_SWITCH = "VOIDLOOM_NO_QUICKENING"

DEFAULT_QUICKENING_CONFIG: Dict[str, Any] = {
    # --- spike detection ---
    "window_days": 28,            # trailing baseline window (days)
    "z_threshold": 3.0,           # z > 3 -> a spike (the wonder trigger)
    "min_baseline_days": 7,       # need >= this many non-zero baseline days
    "min_baseline_std": 0.5,      # floor on sigma (guards divide-by-tiny -> false +)
    "min_spike_events": 3,        # the spike day must clear this many real events
    "max_spikes_per_run": 25,     # cap whispers/seeds per stage (anti-alarm)
    "per_memory": True,           # detect per-memory
    "per_tag": True,              # detect per-tag/topic
    "min_tag_members": 1,         # a tag scope needs >= this many tagged memories
    # --- cadence / predictive resurfacing ---
    "cadence_lookback_days": 90,  # history window for per-tag cadence
    "cadence_min_events": 3,      # need >= this many distinct access days
    "lapse_multiplier": 1.5,      # overdue if gap > cadence * this
    # --- whisper + dream seed ---
    "emit_whispers": True,
    "whisper_intensity_floor": 0.5,
    "whisper_intensity_ceil": 0.95,
    "seed_dream_pool": True,
    # --- determinism (tests) ---
    "_now": None,                 # ISO string to pin "now" (tests); None -> utcnow
}

# Reuse dream_pipeline's real-session convention so a future 'recall:cron' tag is
# excluded automatically here too (one filter doctrine, forward-compatible).
_REAL_RECALL_PREFIX = "recall"
_MACHINE_RECALL_MARKERS = (
    "cron", "dream", "sleep", "probe", "maintenance", "machine",
    "council", "digest", "reflect", "consolidat",
)

# Empirically-observed machine query_text patterns on THIS corpus. A systematic
# 'memory id NNNN' sweep (1,289 live rows) and digest/maintenance sentinels are
# machine map-building, not a human recall, yet they wear a 'recall' access_type.
# Matched case-insensitively as a prefix / substring as noted.
_MACHINE_QUERY_PREFIXES = (
    "memory id ",          # direct-id systematic sweep (map building)
    "context --project",   # session-start context dump
    "[machine]",
    "[cron]",
    "[digest]",
    "[sleep]",
)
_MACHINE_QUERY_SUBSTR = (
    "whisper-digest",
    "weekly digest",
    "sleep cycle",
    "self-eval",
    "self eval",
)


# ============================================================
# Migration (idempotent; mirrors dream_pipeline's _has_column/IF NOT EXISTS guard)
# ============================================================

def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def is_quickening_migration_applied(conn: sqlite3.Connection) -> bool:
    """True iff the quickening_log observability table exists."""
    return _has_table(conn, "quickening_log")


_MIGRATION_SQL = """
-- L5-L "The Quickening" observability surface. Makes the recall-tide sense
-- falsifiable in self-eval: every fired spike is one row (memory- or tag-scoped),
-- with the z-score and the dream-seed payload it emitted. NO salience columns --
-- the sink is the EXISTING one (the surprise whisper + the activity-tilted dream
-- pool). This table only records that a spike was SEEN and what it seeded.
CREATE TABLE IF NOT EXISTS quickening_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_kind    TEXT NOT NULL CHECK(scope_kind IN ('memory', 'tag')),
    scope_key     TEXT NOT NULL,           -- memory_id (as text) or tag string
    z_score       REAL NOT NULL,           -- the spike's z over its own baseline
    spike_events  INTEGER NOT NULL,        -- de-duplicated real events on the day
    baseline_mean REAL,                    -- trailing-window mean (events/day)
    baseline_std  REAL,                    -- trailing-window std
    window_days   INTEGER NOT NULL,
    seed_bias     TEXT,                    -- JSON dream-seed payload (reused shape)
    whisper_id    INTEGER,                 -- id of the surprise whisper emitted
    batch_id      INTEGER,                 -- the sleep-loop run that observed it
    fired_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_quickening_scope
    ON quickening_log(scope_kind, scope_key);
CREATE INDEX IF NOT EXISTS idx_quickening_fired
    ON quickening_log(fired_at);
"""


def ensure_quickening_migration(conn: sqlite3.Connection) -> bool:
    """Apply the quickening_log migration idempotently. Returns True if present
    after the call (whether it pre-existed or was just created)."""
    try:
        conn.executescript(_MIGRATION_SQL)
        conn.commit()
        return is_quickening_migration_applied(conn)
    except sqlite3.Error as e:  # pragma: no cover - defensive
        logger.error("quickening migration failed: %s", e)
        return False


# ============================================================
# Machine-traffic filter (THE load-bearing correctness requirement)
# ============================================================

def _is_real_session_access_type(access_type: Optional[str]) -> bool:
    """True iff this access_type is a real human-session recall (not machine).

    Mirrors dream_pipeline._is_real_session_access so a future machine reader that
    tags 'recall:cron' / 'recall:sleep' / ... is excluded here automatically.
    """
    if not access_type:
        return False
    at = access_type.strip().lower()
    if not at.startswith(_REAL_RECALL_PREFIX):
        return False
    suffix = at[len(_REAL_RECALL_PREFIX):].lstrip(":")
    if suffix.endswith("-lead"):
        return False   # a side-channel DISPLAY (the met ledger, e.g. recall:dream-lead) is attention, not a recall
    return not any(suffix.startswith(m) for m in _MACHINE_RECALL_MARKERS)


def _is_machine_query_text(query_text: Optional[str]) -> bool:
    """True iff query_text matches a known machine map-building / maintenance
    pattern. On THIS corpus these wear a 'recall' access_type, so access_type
    alone cannot catch them -- this is the second filter axis."""
    if query_text is None:
        return False  # a null query is not, by itself, machine; clustering handles bursts
    q = query_text.strip().lower()
    if not q:
        return False
    if any(q.startswith(p) for p in _MACHINE_QUERY_PREFIXES):
        return True
    if any(s in q for s in _MACHINE_QUERY_SUBSTR):
        return True
    return False


def _minute_bucket(accessed_at: str) -> str:
    """Truncate an 'YYYY-MM-DD HH:MM:SS' timestamp to the minute for clustering."""
    # 16 chars == 'YYYY-MM-DD HH:MM'. Robust to a trailing '.ffffff' or 'T'.
    s = accessed_at.replace("T", " ")
    return s[:16]


def _filter_machine_traffic(rows: List[Tuple[Any, ...]]) -> List[Dict[str, Any]]:
    """Filter machine traffic from raw access_log rows BEFORE any mu/sigma math.

    Each input row is ``(memory_id, access_type, query_text, accessed_at)``.

    Three axes, applied in order:
      1. drop rows whose access_type is a machine recall marker;
      2. drop rows whose query_text is a known machine map-building pattern;
      3. sub-minute time-clustering: collapse a burst of reads sharing the same
         (query_text, minute) -- a batch recall logs one top-k read per memory at
         an identical second -- to ONE logical event. We keep ONE representative
         row per (memory_id, query_text, minute) so a single human recall that
         touched a memory is counted once, not top_k times, while distinct
         memories in the same human recall are each still counted once.

    Returns a list of surviving event dicts:
      ``{memory_id, access_type, query_text, accessed_at, day}``
    Order-stable (first occurrence wins within a cluster).
    """
    seen_cluster: set = set()
    out: List[Dict[str, Any]] = []
    for memory_id, access_type, query_text, accessed_at in rows:
        if accessed_at is None:
            continue
        # Axis 1: machine access_type.
        if not _is_real_session_access_type(access_type):
            continue
        # Axis 2: machine query_text pattern.
        if _is_machine_query_text(query_text):
            continue
        # Axis 3: sub-minute clustering -> one event per (memory, query, minute).
        minute = _minute_bucket(str(accessed_at))
        cluster_key = (memory_id, (query_text or ""), minute)
        if cluster_key in seen_cluster:
            continue
        seen_cluster.add(cluster_key)
        out.append({
            "memory_id": memory_id,
            "access_type": access_type,
            "query_text": query_text,
            "accessed_at": str(accessed_at),
            "day": str(accessed_at)[:10],
        })
    return out


# ============================================================
# Spike detection (the alive half) -- moving-average + z-score
# ============================================================

def _now_dt(cfg: Dict[str, Any]) -> datetime:
    pinned = cfg.get("_now")
    if pinned:
        try:
            return datetime.strptime(str(pinned)[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                return datetime.strptime(str(pinned)[:10], "%Y-%m-%d")
            except ValueError:
                pass
    return datetime.utcnow()


def _fetch_clean_events(
    conn: sqlite3.Connection,
    window_days: int,
    now: datetime,
    extra_days: int = 1,
) -> List[Dict[str, Any]]:
    """Pull access_log rows within (window_days + extra_days) of ``now`` and run
    the machine-traffic filter. ``extra_days`` includes the spike day itself."""
    if not _has_table(conn, "access_log"):
        return []
    floor = (now - timedelta(days=window_days + extra_days)).strftime("%Y-%m-%d %H:%M:%S")
    ceil = now.strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """SELECT memory_id, access_type, query_text, accessed_at
           FROM access_log
           WHERE accessed_at >= ? AND accessed_at <= ?""",
        (floor, ceil),
    ).fetchall()
    return _filter_machine_traffic(rows)


def _daily_counts(events: List[Dict[str, Any]], key_fn) -> Dict[Any, Dict[str, int]]:
    """Group surviving events into per-scope, per-day event counts.

    ``key_fn(event) -> iterable of scope keys`` (a memory yields one key; a tag
    scope yields one key per tag the memory carries).
    Returns ``{scope_key: {day: count}}``.
    """
    out: Dict[Any, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for ev in events:
        for k in key_fn(ev):
            out[k][ev["day"]] += 1
    return {k: dict(v) for k, v in out.items()}


def _zscore_for_scope(
    day_counts: Dict[str, int],
    spike_day: str,
    window_days: int,
    cfg: Dict[str, Any],
) -> Optional[Dict[str, float]]:
    """Compute the z-score of ``spike_day`` vs the trailing ``window_days``
    baseline (the days strictly before the spike day). Returns a stats dict or
    None if there is not enough baseline signal to judge."""
    spike_events = int(day_counts.get(spike_day, 0))
    if spike_events < int(cfg["min_spike_events"]):
        return None

    spike_dt = datetime.strptime(spike_day, "%Y-%m-%d")
    # Build a dense per-day series over the baseline window (zeros included): a
    # quiet memory that suddenly spikes MUST register, so absent days are real 0s.
    baseline_vals: List[float] = []
    nonzero_days = 0
    for d in range(1, window_days + 1):
        day = (spike_dt - timedelta(days=d)).strftime("%Y-%m-%d")
        c = int(day_counts.get(day, 0))
        baseline_vals.append(float(c))
        if c > 0:
            nonzero_days += 1

    if nonzero_days < int(cfg["min_baseline_days"]):
        return None
    if np is None or not baseline_vals:
        return None

    arr = np.asarray(baseline_vals, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std())  # population std (ddof=0) -- baseline IS the population
    eff_std = max(std, float(cfg["min_baseline_std"]))
    z = (spike_events - mean) / eff_std
    return {
        "z": round(z, 4),
        "spike_events": spike_events,
        "mean": round(mean, 4),
        "std": round(std, 4),
        "eff_std": round(eff_std, 4),
        "nonzero_baseline_days": nonzero_days,
    }


def detect_recall_spike(
    conn: sqlite3.Connection,
    scope: str = "all",
    window: int = 28,
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Detect recall spikes over ``access_log`` via moving-average + z-score.

    Machine traffic is filtered FIRST (the load-bearing correctness requirement).
    Detection is stateless and triggers WONDER, not prediction.

    Args:
        conn: sqlite3 connection.
        scope: 'memory' (per-memory only), 'tag' (per-tag only), or 'all' (both).
        window: trailing baseline window in days.
        config: overrides for DEFAULT_QUICKENING_CONFIG.

    Returns:
        A list of spike dicts, each:
          ``{scope_kind, scope_key, z_score, spike_events, baseline_mean,
             baseline_std, window_days, spike_day}``
        sorted by z_score DESC, capped at ``max_spikes_per_run``.
    """
    cfg = {**DEFAULT_QUICKENING_CONFIG, **(config or {})}
    cfg["window_days"] = int(window)
    now = _now_dt(cfg)
    z_thresh = float(cfg["z_threshold"])

    events = _fetch_clean_events(conn, cfg["window_days"], now)
    if not events:
        return []

    # The spike day is the most recent clean-event day in range (typically today).
    spike_day = max(ev["day"] for ev in events)

    do_memory = cfg["per_memory"] and scope in ("memory", "all")
    do_tag = cfg["per_tag"] and scope in ("tag", "all")

    spikes: List[Dict[str, Any]] = []

    # --- per-memory scope ---
    if do_memory:
        per_mem = _daily_counts(events, key_fn=lambda ev: (ev["memory_id"],))
        for mem_id, day_counts in per_mem.items():
            stats = _zscore_for_scope(day_counts, spike_day, cfg["window_days"], cfg)
            if stats and stats["z"] > z_thresh:
                spikes.append({
                    "scope_kind": "memory",
                    "scope_key": str(mem_id),
                    "z_score": stats["z"],
                    "spike_events": stats["spike_events"],
                    "baseline_mean": stats["mean"],
                    "baseline_std": stats["std"],
                    "window_days": cfg["window_days"],
                    "spike_day": spike_day,
                })

    # --- per-tag scope ---
    if do_tag:
        tag_map = _load_tag_map(conn, [ev["memory_id"] for ev in events])

        def _tag_keys(ev):
            return tag_map.get(ev["memory_id"], ())

        per_tag = _daily_counts(events, key_fn=_tag_keys)
        for tag, day_counts in per_tag.items():
            stats = _zscore_for_scope(day_counts, spike_day, cfg["window_days"], cfg)
            if stats and stats["z"] > z_thresh:
                spikes.append({
                    "scope_kind": "tag",
                    "scope_key": tag,
                    "z_score": stats["z"],
                    "spike_events": stats["spike_events"],
                    "baseline_mean": stats["mean"],
                    "baseline_std": stats["std"],
                    "window_days": cfg["window_days"],
                    "spike_day": spike_day,
                })

    spikes.sort(key=lambda s: s["z_score"], reverse=True)
    return spikes[: int(cfg["max_spikes_per_run"])]


def _load_tag_map(conn: sqlite3.Connection, memory_ids: List[int]) -> Dict[int, Tuple[str, ...]]:
    """Map memory_id -> tuple of tags, for the given (de-duplicated) memory ids."""
    if not memory_ids or not _has_table(conn, "memory_tags"):
        return {}
    uniq = sorted(set(int(m) for m in memory_ids))
    out: Dict[int, List[str]] = defaultdict(list)
    # Chunk the IN-list (SQLite parameter limit safety).
    chunk = 800
    for i in range(0, len(uniq), chunk):
        part = uniq[i:i + chunk]
        qmarks = ",".join("?" * len(part))
        for mem_id, tag in conn.execute(
            f"SELECT memory_id, tag FROM memory_tags WHERE memory_id IN ({qmarks})",
            part,
        ).fetchall():
            out[mem_id].append(tag)
    return {k: tuple(v) for k, v in out.items()}


# ============================================================
# Cadence (the hygiene half) -- per-TAG/topic, NOT per-single-memory
# ============================================================

def compute_access_cadence(
    conn: sqlite3.Connection,
    scope: str = "tag",
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Compute the typical access cadence (mean days between accesses) per TAG.

    Forecasting needs HISTORY -> we operate per-tag/topic, never per-single-memory
    (the corpus is too sparse: 58% of memories accessed <=2x ever). Machine
    traffic is filtered first. Returns ``{tag: {cadence_days, events, span_days,
    last_access, regularity}}`` for tags with enough signal.

    ``regularity`` in [0,1]: 1.0 == perfectly periodic (low gap variance),
    -> 0 as gaps become erratic. Computed as 1/(1+cv) of the inter-access gaps.
    """
    cfg = {**DEFAULT_QUICKENING_CONFIG, **(config or {})}
    now = _now_dt(cfg)
    lookback = int(cfg["cadence_lookback_days"])
    min_events = int(cfg["cadence_min_events"])

    if not _has_table(conn, "access_log"):
        return {}

    floor = (now - timedelta(days=lookback)).strftime("%Y-%m-%d %H:%M:%S")
    ceil = now.strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """SELECT memory_id, access_type, query_text, accessed_at
           FROM access_log
           WHERE accessed_at >= ? AND accessed_at <= ?""",
        (floor, ceil),
    ).fetchall()
    events = _filter_machine_traffic(rows)
    if not events:
        return {}

    tag_map = _load_tag_map(conn, [ev["memory_id"] for ev in events])

    # Collect the set of distinct access DAYS per tag (cadence is a day-grain
    # signal; multiple same-day events count once after clustering already, but
    # different memories under one tag on one day still mean "the topic was hot
    # that day" -> one cadence tick).
    tag_days: Dict[str, set] = defaultdict(set)
    for ev in events:
        for tag in tag_map.get(ev["memory_id"], ()):
            tag_days[tag].add(ev["day"])

    out: Dict[str, Dict[str, Any]] = {}
    for tag, days in tag_days.items():
        if len(days) < min_events:
            continue
        sorted_days = sorted(datetime.strptime(d, "%Y-%m-%d") for d in days)
        gaps = [
            (sorted_days[i] - sorted_days[i - 1]).days
            for i in range(1, len(sorted_days))
        ]
        gaps = [g for g in gaps if g > 0]
        if not gaps:
            continue
        if np is not None:
            garr = np.asarray(gaps, dtype=float)
            mean_gap = float(garr.mean())
            std_gap = float(garr.std())
        else:  # pragma: no cover
            mean_gap = sum(gaps) / len(gaps)
            std_gap = 0.0
        cv = (std_gap / mean_gap) if mean_gap > 0 else 0.0
        regularity = 1.0 / (1.0 + cv)
        last_access = sorted_days[-1].strftime("%Y-%m-%d")
        span_days = (sorted_days[-1] - sorted_days[0]).days
        out[tag] = {
            "cadence_days": round(mean_gap, 3),
            "std_gap_days": round(std_gap, 3),
            "events": len(sorted_days),
            "span_days": span_days,
            "last_access": last_access,
            "regularity": round(regularity, 4),
        }
    return out


def predicted_lapse(
    cadence: Dict[str, Any],
    last_access: Optional[str],
    now: Optional[datetime] = None,
    lapse_multiplier: float = 1.5,
) -> bool:
    """Predict whether a topic is OVERDUE for the next access (about-to-lapse).

    A topic lapses predictively when the time since its last access exceeds its
    own learned cadence by ``lapse_multiplier``. This is the future-looking
    complement to anti_recency's reactive ``last_accessed < cutoff`` -- it fires
    BEFORE a memory has gone fully stale, using the topic's rhythm.

    Args:
        cadence: a per-tag cadence dict from ``compute_access_cadence`` (uses
                 ``cadence_days``); falsey/empty -> no prediction (False).
        last_access: ISO datetime of the most recent access for the scope.
        now: reference time (defaults to utcnow).
        lapse_multiplier: overdue factor.

    Returns:
        True iff the scope is predicted to be lapsing.
    """
    if not cadence or not last_access:
        return False
    cadence_days = cadence.get("cadence_days")
    if not cadence_days or cadence_days <= 0:
        return False
    ref = now or datetime.utcnow()
    try:
        last_dt = datetime.strptime(str(last_access)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            last_dt = datetime.strptime(str(last_access)[:10], "%Y-%m-%d")
        except ValueError:
            return False
    days_since = (ref - last_dt).total_seconds() / 86400.0
    return days_since > (cadence_days * float(lapse_multiplier))


# ============================================================
# Whisper + dream-seed emission (the SAME sink, never a parallel one)
# ============================================================

def _import_whisper_network():
    """Best-effort import of whisper_network (graceful-absent)."""
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import whisper_network  # type: ignore
        return whisper_network
    except Exception as e:  # pragma: no cover
        logger.warning("whisper_network unavailable: %s", e)
        return None


def _spike_intensity(z: float, cfg: Dict[str, Any]) -> float:
    """Map a z-score to a whisper intensity in [floor, ceil] (saturating)."""
    floor = float(cfg["whisper_intensity_floor"])
    ceil = float(cfg["whisper_intensity_ceil"])
    # z just over threshold -> floor; z >= 2*threshold -> ceil.
    thresh = float(cfg["z_threshold"])
    span = max(1e-6, thresh)  # one threshold-width of head-room
    frac = max(0.0, min(1.0, (z - thresh) / span))
    return round(floor + frac * (ceil - floor), 4)


def _spike_whisper_text(spike: Dict[str, Any]) -> str:
    """Human-readable surprise observation for the whisper sink."""
    if spike["scope_kind"] == "memory":
        what = f"memory #{spike['scope_key']}"
    else:
        what = f"the '{spike['scope_key']}' topic"
    return (
        f"Attention surged toward {what} -- {spike['spike_events']} recalls on "
        f"{spike['spike_day']} vs a baseline of {spike['baseline_mean']:.2f}/day "
        f"(z={spike['z_score']:.1f}). Something is quickening here; worth a look "
        f"at why it is suddenly alive."
    )


def _seed_bias_payload(spike: Dict[str, Any]) -> Dict[str, Any]:
    """A dream-seed payload in the SAME shape dream_pipeline records on
    dream_log.seed_bias. We do NOT write encoding_surprise (G is epistemic-only);
    the genuine spike already tilts the activity-profile-driven pool. This payload
    is the falsifiable record of WHAT the quickening asked the night-brain to
    dwell on, and the bridge a later dream_pipeline edit can read directly."""
    return {
        "mode": "quickening_spike",
        "source": "L5-L",
        "scope_kind": spike["scope_kind"],
        "scope_key": spike["scope_key"],
        "z_score": spike["z_score"],
        "spike_events": spike["spike_events"],
        "spike_day": spike["spike_day"],
    }


def _emit_spike(
    conn: sqlite3.Connection,
    spike: Dict[str, Any],
    cfg: Dict[str, Any],
    batch_id: Optional[int],
    whisper_mod,
) -> Dict[str, Any]:
    """Emit ONE spike: surprise whisper + dream-seed payload + quickening_log row.
    Returns a small emission record. Never raises (caller wraps, but we are
    defensive so one bad spike does not lose the others)."""
    seed_bias = _seed_bias_payload(spike) if cfg["seed_dream_pool"] else None
    whisper_id: Optional[int] = None

    if cfg["emit_whispers"] and whisper_mod is not None:
        try:
            whisper_id = whisper_mod.capture_whisper(
                conn,
                _spike_whisper_text(spike),
                "surprise",
                session_context="quickening:recall-spike",
                intensity=_spike_intensity(spike["z_score"], cfg),
            )
        except Exception as e:  # pragma: no cover - defensive per-spike
            logger.warning("whisper emit failed for spike %s: %s", spike["scope_key"], e)

    # Falsifiable observability + the dream-seed record.
    try:
        conn.execute(
            """INSERT INTO quickening_log
               (scope_kind, scope_key, z_score, spike_events, baseline_mean,
                baseline_std, window_days, seed_bias, whisper_id, batch_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                spike["scope_kind"], spike["scope_key"], spike["z_score"],
                spike["spike_events"], spike["baseline_mean"], spike["baseline_std"],
                spike["window_days"],
                json.dumps(seed_bias) if seed_bias is not None else None,
                whisper_id, batch_id,
            ),
        )
        conn.commit()
    except Exception as e:  # pragma: no cover - defensive per-spike
        logger.warning("quickening_log insert failed for %s: %s", spike["scope_key"], e)

    return {
        "scope_kind": spike["scope_kind"],
        "scope_key": spike["scope_key"],
        "z_score": spike["z_score"],
        "whisper_id": whisper_id,
        "seeded_dream": seed_bias is not None,
    }


# ============================================================
# Sleep-loop stage (fail-soft) -- the orchestrator entry point
# ============================================================

def run_quickening_stage(
    conn: sqlite3.Connection,
    cfg: Optional[Dict[str, Any]] = None,
    batch_id: Optional[int] = None,
) -> Dict[str, Any]:
    """The Quickening sleep-loop stage. FAIL-SOFT: this function NEVER raises into
    the orchestrator -- it returns a result dict with a ``status`` key. The
    orchestrator's success/partial math is unaffected (this is observability, not
    one of the five core stages).

    Gated by the ``VOIDLOOM_NO_QUICKENING`` env kill-switch (matches the family).

    Returns:
        ``{status, spikes_detected, spikes_emitted, whispers, dreams_seeded,
           emissions, reason?}``
    """
    result: Dict[str, Any] = {
        "status": "ok",
        "spikes_detected": 0,
        "spikes_emitted": 0,
        "whispers": 0,
        "dreams_seeded": 0,
        "emissions": [],
    }

    # Panic switch wins.
    if os.environ.get(ENV_KILL_SWITCH, "").strip():
        result["status"] = "skipped"
        result["reason"] = f"{ENV_KILL_SWITCH} set"
        return result

    try:
        conf = {**DEFAULT_QUICKENING_CONFIG, **(cfg or {})}

        if np is None:
            result["status"] = "skipped"
            result["reason"] = "numpy unavailable"
            return result

        if not ensure_quickening_migration(conn):
            result["status"] = "skipped"
            result["reason"] = "quickening_log migration could not be applied"
            return result

        spikes = detect_recall_spike(
            conn, scope="all", window=int(conf["window_days"]), config=conf
        )
        result["spikes_detected"] = len(spikes)
        if not spikes:
            return result

        whisper_mod = _import_whisper_network() if conf["emit_whispers"] else None

        for spike in spikes:
            emission = _emit_spike(conn, spike, conf, batch_id, whisper_mod)
            result["emissions"].append(emission)
            result["spikes_emitted"] += 1
            if emission.get("whisper_id") is not None:
                result["whispers"] += 1
            if emission.get("seeded_dream"):
                result["dreams_seeded"] += 1

        return result

    except Exception as e:
        # Fail-soft contract: log + report, NEVER propagate.
        logger.error("quickening stage failed (soft): %s", e)
        result["status"] = "error"
        result["reason"] = str(e)
        return result


# ============================================================
# CLI
# ============================================================

def _open_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _cli_detect(db_path: str) -> None:
    conn = _open_db(db_path)
    spikes = detect_recall_spike(conn, scope="all")
    print(json.dumps({"spikes": spikes, "count": len(spikes)}, indent=2))
    conn.close()


def _cli_cadence(db_path: str) -> None:
    conn = _open_db(db_path)
    cad = compute_access_cadence(conn, scope="tag")
    print(json.dumps({"cadence": cad, "tags": len(cad)}, indent=2))
    conn.close()


def _cli_stage(db_path: str) -> None:
    conn = _open_db(db_path)
    res = run_quickening_stage(conn, batch_id=None)
    print(json.dumps(res, indent=2))
    conn.close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="The Quickening (L5-L) -- recall-tide sense")
    parser.add_argument("--unit-tests", action="store_true", help="run self-tests")
    parser.add_argument("--detect", metavar="DB", help="show current spikes for DB")
    parser.add_argument("--cadence", metavar="DB", help="show per-tag cadences for DB")
    parser.add_argument("--stage", metavar="DB", help="run the sleep-loop stage on DB")
    args = parser.parse_args(argv)

    if args.unit_tests:
        try:
            from test_quickening import run_all  # type: ignore
        except ImportError:
            here = str(Path(__file__).resolve().parent)
            if here not in sys.path:
                sys.path.insert(0, here)
            from test_quickening import run_all  # type: ignore
        ok = run_all()
        return 0 if ok else 1
    if args.detect:
        _cli_detect(args.detect)
        return 0
    if args.cadence:
        _cli_cadence(args.cadence)
        return 0
    if args.stage:
        _cli_stage(args.stage)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
