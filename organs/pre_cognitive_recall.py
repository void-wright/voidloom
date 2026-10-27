#!/usr/bin/env python3
"""Pre-Cognitive Recall — Wizard Library L4 Piece 2B.

A session-start module that pre-loads the most relevant memories BEFORE the
owner states the task. By analysing recent activity signals (last edits, recent
automation completions, current git branch, recent memory stores, time of day),
it computes a "current focus vector" and pre-loads the top-15 memories weighted
by the adaptive recall map tiers.

The package's defaults (TIER_1_QUERIES, TIER_2_QUERIES, DEFAULT_TIER_WEIGHTS,
TIME_PATTERNS, DAY_PATTERNS) name no owner.  An owner's own -- his persona's
identity query, his tags, his week -- live in an optional per-brain file,
``<brain>/pre_cognitive_recall.json`` (beside persona_lens_weights.json), which
``recall_defaults()`` overlays; absent file -> the package defaults, unchanged.

Public API
----------
``collect_signals(project_root=None) -> dict``
    Gather lightweight signals about recent activity: recent file edits,
    last 5 stored memories, git branch name, day-of-week + hour, recent
    automation completions. All operations are fast (<500 ms total).

``compute_focus_vector(signals, encode_fn) -> bytes``
    Concatenate the signal texts into a single narrative, then embed it
    using the production Nomic encoder. Returns serialised float32 vector
    ready for sqlite-vec queries. One embedding call (~100 ms GPU).

``anticipatory_recall(conn, focus_vec, encode_fn, top_k=15,
                      tier_weights=None, show_anticipated=False) -> dict``
    Run three-tier recall: Tier 1 (identity+foundation, always), Tier 2
    (brain state, always), Tier 3 (focus-vector-dependent). Returns a dict
    with ``memories_loaded``, ``tiers``, ``focus_summary``, ``time_ms``.

``format_context_injection(recall_result, verbose=False) -> str``
    Format the recall result for injection into session context. If verbose
    is False (default), produces a compact summary. If True, includes full
    memory content for each loaded memory.

``is_precognitive_migration_applied(conn) -> bool``
    Check whether migration 015 (pre_cognitive_recall_log table) has been
    applied.

``apply_precognitive_migration(db_path, migration_sql_path) -> dict``
    Idempotent migration runner for the pre-cognitive log table.

``unit_test_pre_cognitive_recall()``
    Self-contained unit tests (33 tests).

``recall_defaults(conn=None, path=None) -> dict``
    The package defaults overlaid with the owner's optional file; reports the
    file used, any error and every rejected value.  Never raises.

Phase 0 memories: #4070 (persona self-evolution), #3827 (persona naming),
#3955 (compounding principle), #1508 (mandatory startup protocol).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import struct
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# --- TO-02 repair: resolve the embedding tier instead of hardcoding it -------
# The write path moved to memory_embeddings_v2 on 2026-05-28; this reader was not
# moved with it. embed_resolver REFUSES rather than defaulting to the dormant
# table (architecture-009) and resolves from the CALLER'S connection.
from embed_resolver import resolve as _resolve_embed_tier  # noqa: E402
# ---------------------------------------------------------------------------


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum seconds for the entire anticipatory recall operation.
MAX_ANTICIPATE_SECONDS = 5.0

# Default number of memories to pre-load.
DEFAULT_TOP_K = 15

# Maximum file edits to consider (most-recently modified).
MAX_RECENT_EDITS = 5

# Maximum recent memory stores to inspect.
MAX_RECENT_STORES = 5

# Tier-1 queries (identity + foundation — always loaded).  The package's are
# generic: the brain's declared persona and its owner are found by what they
# ARE, not by name.  An owner names his own in <brain>/pre_cognitive_recall.json.
TIER_1_QUERIES = [
    "persona identity voice and character of this brain",
    "wizard library architecture compounding augmentation Level 2 3 4",
    "owner preferences working style honest assessment",
]

# Tier-2 queries (brain state — always loaded).
TIER_2_QUERIES = [
    "sleep loop consolidation status recent nightly cron results",
    "anti-recency resurfaced foundation score high importance forgotten",
    "skill graduation pipeline promoted demoted SEED ACTIVE PROTECTED",
    "whisper network self-observations recent persona-evolution",
]

# Cluster weights from the adaptive recall map.  Keys match memory tags;
# values are multiplicative boosts applied to the base relevance score
# during post-retrieval reranking.
DEFAULT_TIER_WEIGHTS: Dict[str, float] = {
    # Always-Priority (weight 1.0)
    "wizard-library": 1.0,
    "architecture": 1.0,
    "compounding-augmentation": 1.0,
    "persona-feedback": 1.0,
    "persona-evolution": 1.0,
    "personality-growth": 1.0,
    "ace-loop": 1.0,
    "ace-curator": 1.0,
    "satisfactory": 1.0,
    # High-Priority (weight 0.85)
    "session-closure": 0.85,
    "sleep-loop-consolidated": 0.85,
    "skill-graduation": 0.85,
    "four-layer-pattern": 0.85,
    "living-persona": 0.85,
    # Medium-Priority (weight 0.65)
    "persona-roster": 0.65,
    "cron": 0.65,
    "sleep-loop": 0.65,
    "anti-recency": 0.65,
    "wiki": 0.65,
    "incident": 0.65,
    "production-fix": 0.65,
    # Low-Priority (weight 0.35)
    "campaign": 0.35,
    "archived": 0.35,
    "deprecated": 0.35,
    "setup": 0.35,
    "installation": 0.35,
}

# Tags whose memories are excluded from automatic pre-loading.
EXCLUDED_TAGS = frozenset({"test", "debug", "duplicate", "superseded"})

# Time-of-day pattern mapping: a default rhythm.  An owner's observed week goes
# in their own file (time_patterns / day_patterns), which replaces these key by key.
TIME_PATTERNS: Dict[str, str] = {
    "early_morning": "infrastructure maintenance and scheduled jobs",       # 5-8 AM
    "morning": "planned work priorities for today",                         # 8-12 PM
    "afternoon": "focused build work architecture and components",         # 12-5 PM
    "evening": "deep work on the current project",                        # 5-9 PM
    "night": "review reflection planning next steps",                      # 9 PM-5 AM
}

# Day-of-week signal hints.
DAY_PATTERNS: Dict[int, str] = {
    0: "Monday planning week sprint",       # Monday
    1: "focused production work",            # Tuesday
    2: "midweek deep work build",            # Wednesday
    3: "focused production work",            # Thursday
    4: "Friday wrap-up closure review",      # Friday
    5: "weekend personal projects",          # Saturday
    6: "Sunday planning reflection",         # Sunday
}


# ---------------------------------------------------------------------------
# The owner's recall defaults -- OUT of the code, in an optional per-brain file
# ---------------------------------------------------------------------------
# <brain>/pre_cognitive_recall.json, beside the brain's DB (and beside persona_lens_weights.json).  Shape:
#   {"tier_1_queries": ["..."], "tier_2_queries": ["..."],      a list REPLACES the package's list
#    "tier_weights":  {"<tag>": 1.0},                            MERGES key by key (the file wins)
#    "time_patterns": {"morning": "..."},                        MERGES key by key; keys are TIME_PATTERNS' periods
#    "day_patterns":  {"0": "..."}}                              MERGES key by key; keys are weekdays 0..6
# Every value is validated: a bad one is skipped and named in `rejected`, the rest still applies -- a typo never
# breaks session start.  Absent file: the package defaults, unchanged.  Keys starting with "_" are comments.
PRE_COGNITIVE_DEFAULTS_BASENAME = "pre_cognitive_recall.json"
_LIST_KEYS = ("tier_1_queries", "tier_2_queries")
_OWNER_KEYS = _LIST_KEYS + ("tier_weights", "time_patterns", "day_patterns")


def _default_db_path() -> Path:
    """The brain this organ reads when no connection is handed to it (the same resolution as its other readers)."""
    return Path(os.environ.get("WIZARD_LIBRARY_DB")
                or (Path(__file__).resolve().parent.parent / "claude_memory.db"))


def owner_defaults_path(conn: Optional[sqlite3.Connection] = None) -> Optional[Path]:
    """<brain>/pre_cognitive_recall.json for the brain `conn` is attached to (else the default brain); None for an
    in-memory database."""
    if conn is not None:
        try:
            for _seq, name, path in conn.execute("PRAGMA database_list").fetchall():
                if name == "main":
                    return (Path(path).resolve().parent / PRE_COGNITIVE_DEFAULTS_BASENAME) if path else None
        except sqlite3.Error:
            pass
    return _default_db_path().resolve().parent / PRE_COGNITIVE_DEFAULTS_BASENAME


def _is_weight(v: Any) -> bool:
    """A finite real number -- never a bool (True is an int to Python), never a string."""
    import math
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def recall_defaults(conn: Optional[sqlite3.Connection] = None,
                    path: Optional[Any] = None) -> Dict[str, Any]:
    """The package defaults overlaid with the owner's optional file.  NEVER raises.

    Returns {tier_1_queries, tier_2_queries, tier_weights, time_patterns, day_patterns,
             owner_file (path used, or None), owner_error (or None), rejected [...]}."""
    out: Dict[str, Any] = {
        "tier_1_queries": list(TIER_1_QUERIES),
        "tier_2_queries": list(TIER_2_QUERIES),
        "tier_weights": dict(DEFAULT_TIER_WEIGHTS),
        "time_patterns": dict(TIME_PATTERNS),
        "day_patterns": dict(DAY_PATTERNS),
        "owner_file": None,
        "owner_error": None,
        "rejected": [],
    }
    fpath = Path(path) if path is not None else owner_defaults_path(conn)
    if fpath is None or not fpath.is_file():
        return out
    out["owner_file"] = str(fpath)
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        out["owner_error"] = "%s: %s" % (type(e).__name__, e)
        return out
    if not isinstance(data, dict):
        out["owner_error"] = "the file is not a JSON object"
        return out
    rej: List[str] = out["rejected"]
    for key, val in data.items():
        if key.startswith("_"):
            continue
        if key not in _OWNER_KEYS:
            rej.append("%s: not a recall default (known: %s)" % (key, ", ".join(_OWNER_KEYS)))
        elif key in _LIST_KEYS:
            if not isinstance(val, list):
                rej.append("%s: not a list of strings" % key)
                continue
            good = [q for q in val if isinstance(q, str) and q.strip()]
            rej.extend("%s[%d]: %r is not a non-empty string" % (key, n, q)
                       for n, q in enumerate(val) if not (isinstance(q, str) and q.strip()))
            out[key] = good
        elif not isinstance(val, dict):
            rej.append("%s: not a JSON object" % key)
        elif key == "tier_weights":
            for tag, w in val.items():
                if _is_weight(w):
                    out["tier_weights"][str(tag)] = float(w)
                else:
                    rej.append("tier_weights.%s: %r is not a finite number" % (tag, w))
        elif key == "time_patterns":
            for period, text in val.items():
                if period in TIME_PATTERNS and isinstance(text, str):
                    out["time_patterns"][period] = text
                else:
                    rej.append("time_patterns.%s: unknown period or not a string" % period)
        else:  # day_patterns
            for day, text in val.items():
                try:
                    d = int(day)
                except (TypeError, ValueError):
                    d = -1
                if 0 <= d <= 6 and str(d) == str(day).strip() and isinstance(text, str):
                    out["day_patterns"][d] = text
                else:
                    rej.append("day_patterns.%s: not a weekday 0..6 with a string" % day)
    return out


# ---------------------------------------------------------------------------
# Signal Collection
# ---------------------------------------------------------------------------

def collect_signals(project_root: Optional[str] = None) -> Dict[str, Any]:
    """Gather lightweight activity signals for focus vector computation.

    All operations are fast (<500 ms total): file stat calls, a few DB
    queries, and one git command. No network calls, no model loading.

    Args:
        project_root: Absolute path to the project directory. If None,
            defaults to the current working directory.

    Returns:
        dict with keys:
            recent_edits: list of (filename, mtime_iso) tuples
            recent_stores: list of {id, summary, category, tags} dicts
            git_branch: str or None
            time_of_day: str (early_morning/morning/afternoon/evening/night)
            day_of_week: int (0=Monday ... 6=Sunday)
            day_name: str
            hour: int
            temporal_hint: str (concatenated day + time pattern text)
            automation_hint: str or None (most recent automation completion)
            project_slug: str (derived from project_root)
    """
    signals: Dict[str, Any] = {}
    root = Path(project_root) if project_root else Path.cwd()

    # 1. Recent file edits (mtime-based, skip hidden dirs and __pycache__)
    signals["recent_edits"] = _recent_file_edits(root)

    # 2. Recent memory stores (last 5 stored memories)
    signals["recent_stores"] = _recent_memory_stores()

    # 3. Git branch name
    signals["git_branch"] = _git_branch(root)

    # 4. Temporal signals
    now = datetime.now()
    signals["hour"] = now.hour
    signals["day_of_week"] = now.weekday()
    signals["day_name"] = now.strftime("%A")
    signals["time_of_day"] = _classify_time_of_day(now.hour)
    signals["temporal_hint"] = _temporal_hint(now)

    # 5. Automation hint (most recent automation-tagged memory)
    signals["automation_hint"] = _recent_automation_hint()

    # 6. Project slug
    signals["project_slug"] = _derive_project_slug(root)

    return signals


def _recent_file_edits(root: Path) -> List[Tuple[str, str]]:
    """Return the most recently modified files under root (fast stat scan)."""
    candidates = []
    try:
        skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv",
                     ".claude", ".mypy_cache", ".pytest_cache", "ACE_Workspaces"}
        for item in root.iterdir():
            if item.name.startswith(".") and item.name != ".claude":
                continue
            if item.name in skip_dirs:
                continue
            if item.is_file() and item.suffix in {".py", ".md", ".json", ".sql",
                                                   ".yaml", ".yml", ".toml", ".sh"}:
                try:
                    mtime = item.stat().st_mtime
                    candidates.append((item.name, mtime))
                except OSError:
                    continue
            elif item.is_dir():
                # One level deep only (fast)
                try:
                    for child in item.iterdir():
                        if child.is_file() and child.suffix in {
                            ".py", ".md", ".json", ".sql", ".yaml", ".yml",
                            ".toml", ".sh",
                        }:
                            try:
                                mtime = child.stat().st_mtime
                                candidates.append(
                                    (f"{item.name}/{child.name}", mtime)
                                )
                            except OSError:
                                continue
                except PermissionError:
                    continue
    except PermissionError:
        pass

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [
        (name, datetime.fromtimestamp(mt).isoformat(timespec="seconds"))
        for name, mt in candidates[:MAX_RECENT_EDITS]
    ]


def _recent_memory_stores(limit: int = MAX_RECENT_STORES) -> List[Dict[str, Any]]:
    """Fetch the most recently stored memories from the narrative DB.

    This is a direct, lightweight SQL query -- no model loading needed.
    """
    # The pre-centralisation path holds a 4,096-byte SQLite file with ZERO
    # tables. It EXISTS, so the `if not db_path.exists()` guard below never
    # fires — this reader has been querying an empty husk and returning
    # nothing since the Great Centralization moved the brain (2026-05-28).
    db_path = Path(os.environ.get("WIZARD_LIBRARY_DB")
                   or (Path(__file__).resolve().parent.parent / "claude_memory.db"))
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        conn.execute("PRAGMA journal_mode=WAL")
        rows = conn.execute(
            """SELECT m.id, m.summary, m.category, m.created_at
               FROM memories m
               WHERE m.is_archived = 0 AND COALESCE(m.derived_from, '') NOT IN ('dream_synthesis', 'playbook_synthesis')
               ORDER BY m.created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()

        results = []
        for row in rows:
            tags = [
                t[0]
                for t in conn.execute(
                    "SELECT tag FROM memory_tags WHERE memory_id = ?", (row[0],)
                ).fetchall()
            ]
            results.append({
                "id": row[0],
                "summary": row[1] or "",
                "category": row[2],
                "created_at": row[3],
                "tags": tags,
            })
        conn.close()
        return results
    except (sqlite3.Error, OSError) as exc:
        logger.warning("Failed to query recent stores: %s", exc)
        return []


def _git_branch(root: Path) -> Optional[str]:
    """Return the current git branch name, or None if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2,
            cwd=str(root),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _classify_time_of_day(hour: int) -> str:
    """Map hour to a named time period."""
    if 5 <= hour < 8:
        return "early_morning"
    elif 8 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 21:
        return "evening"
    else:
        return "night"


def _temporal_hint(now: datetime, defaults: Optional[Dict[str, Any]] = None) -> str:
    """Build a temporal context string from day + time patterns (the owner's, when his file declares them)."""
    d = defaults if defaults is not None else recall_defaults()
    time_hint = d["time_patterns"].get(_classify_time_of_day(now.hour), "")
    day_hint = d["day_patterns"].get(now.weekday(), "")
    return f"{day_hint} {time_hint}".strip()


def _recent_automation_hint() -> Optional[str]:
    """Find the most recent automation-completion memory summary."""
    # The pre-centralisation path holds a 4,096-byte SQLite file with ZERO
    # tables. It EXISTS, so the `if not db_path.exists()` guard below never
    # fires — this reader has been querying an empty husk and returning
    # nothing since the Great Centralization moved the brain (2026-05-28).
    db_path = Path(os.environ.get("WIZARD_LIBRARY_DB")
                   or (Path(__file__).resolve().parent.parent / "claude_memory.db"))
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        conn.execute("PRAGMA journal_mode=WAL")
        row = conn.execute(
            """SELECT m.summary
               FROM memories m
               JOIN memory_tags mt ON mt.memory_id = m.id
               WHERE mt.tag IN ('automation-complete', 'session-closure')
                 AND m.is_archived = 0
               ORDER BY m.created_at DESC
               LIMIT 1""",
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except (sqlite3.Error, OSError):
        return None


def _derive_project_slug(root: Path) -> str:
    """Derive a project slug from the directory path."""
    name = root.name
    # OPERATOR-SUPPLIED overrides, empty by default. This was five hardcoded
    # directory names -- one operator's employer and three of its clients -- in a
    # module that ships. The generic derivation below was always the real
    # behaviour for everyone else; the map was a private alias table sitting in
    # front of it. Format: "Dir_Name=slug,Other_Dir=other-slug".
    #
    #   export VOIDLOOM_PROJECT_SLUGS="My_Big_Project=bigproj,Client_A=client-a"
    for pair in os.environ.get("VOIDLOOM_PROJECT_SLUGS", "").split(","):
        if "=" not in pair:
            continue
        dirname, _, slug = pair.partition("=")
        dirname, slug = dirname.strip(), slug.strip()
        if dirname and slug and dirname in str(root):
            return slug
    return name.lower().replace("_", "-").replace(" ", "-")


# ---------------------------------------------------------------------------
# Focus Vector Computation
# ---------------------------------------------------------------------------

def compute_focus_vector(
    signals: Dict[str, Any],
    encode_fn: Callable[[str, str], bytes],
) -> bytes:
    """Compute a focus vector by embedding the concatenated signal text.

    The signal text is a natural-language narrative constructed from all
    collected signals. One embedding call (~100 ms on GPU).

    Args:
        signals: Output of ``collect_signals()``.
        encode_fn: The production ``_encode(text, prefix)`` function from
            claude_memory.py. Called as ``encode_fn(text, "search_query")``.

    Returns:
        Serialised float32 vector (bytes) suitable for sqlite-vec queries.
    """
    narrative = _build_signal_narrative(signals)
    return encode_fn(narrative, "search_query")


def _build_signal_narrative(signals: Dict[str, Any]) -> str:
    """Build a natural-language narrative from collected signals.

    This text gets embedded to produce the focus vector. The embedding
    model (Nomic v1.5) performs best on natural text, so we write a
    paragraph rather than concatenating keywords.
    """
    parts = []

    # Recent edits
    edits = signals.get("recent_edits", [])
    if edits:
        filenames = [e[0] for e in edits]
        parts.append(
            f"Recently edited files: {', '.join(filenames)}."
        )

    # Recent stores
    stores = signals.get("recent_stores", [])
    if stores:
        summaries = [s["summary"][:120] for s in stores if s.get("summary")]
        if summaries:
            parts.append(
                f"Recent memory activity: {'; '.join(summaries[:3])}."
            )

    # Git branch
    branch = signals.get("git_branch")
    if branch and branch not in ("main", "master", "HEAD"):
        parts.append(f"Working on git branch: {branch}.")

    # Project
    slug = signals.get("project_slug", "")
    if slug:
        parts.append(f"Current project: {slug}.")

    # Temporal
    temporal = signals.get("temporal_hint", "")
    if temporal:
        day_name = signals.get("day_name", "")
        hour = signals.get("hour", 0)
        parts.append(
            f"It is {day_name} at {hour:02d}:00. Context: {temporal}."
        )

    # Automation hint
    auto = signals.get("automation_hint")
    if auto:
        parts.append(f"Recent automation: {auto[:150]}.")

    if not parts:
        parts.append("General wizard library session start.")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Priority-Weighted Recall
# ---------------------------------------------------------------------------

def anticipatory_recall(
    conn: sqlite3.Connection,
    focus_vec: bytes,
    encode_fn: Callable[[str, str], bytes],
    top_k: int = DEFAULT_TOP_K,
    tier_weights: Optional[Dict[str, float]] = None,
    show_anticipated: bool = False,
    recall_hybrid_fn: Optional[Callable] = None,
    signals: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run three-tier anticipatory recall.

    Tier 1: Identity + Foundation queries (always, ~3 queries).
    Tier 2: Brain State queries (always, ~4 queries).
    Tier 3: Focus-vector-dependent recall (the actual anticipation).

    The results are deduplicated across tiers (Tier 1 wins on collision,
    then Tier 2, then Tier 3) and capped at top_k total.

    Args:
        conn: Open sqlite3 connection with sqlite-vec loaded.
        focus_vec: Output of ``compute_focus_vector()``.
        encode_fn: Production ``_encode`` function.
        top_k: Maximum total memories to return.
        tier_weights: Override cluster weights (default: recall_defaults(conn),
            i.e. DEFAULT_TIER_WEIGHTS overlaid with the owner's file).
        show_anticipated: If True, emit verbose per-tier detail.
        recall_hybrid_fn: The ``hybrid_retrieval.recall_hybrid`` callable.
            If None, falls back to direct vector search.

    Returns:
        dict with keys:
            memories_loaded: int
            tiers: {1: [ids], 2: [ids], 3: [ids]}
            memories: list of memory dicts (id, summary, category, tags, score, tier)
            focus_summary: str (the narrative that was embedded)
            ambiguous: bool (True if signals were too weak for strong focus)
            time_ms: int
    """
    t0 = time.time()
    defaults = recall_defaults(conn)
    weights = tier_weights or defaults["tier_weights"]

    seen_ids: set = set()
    all_memories: List[Dict[str, Any]] = []
    tier_ids: Dict[int, List[int]] = {1: [], 2: [], 3: []}

    # Budget: allocate 4 memories to Tier 1, 4 to Tier 2, rest to Tier 3.
    tier_1_budget = min(4, top_k)
    tier_2_budget = min(4, max(0, top_k - tier_1_budget))
    tier_3_budget = max(0, top_k - tier_1_budget - tier_2_budget)

    # --- Tier 1: Identity + Foundation ---
    tier_1_results = _run_tier_queries(
        conn, defaults["tier_1_queries"], encode_fn, recall_hybrid_fn,
        per_query_k=3, budget=tier_1_budget,
    )
    for mem in tier_1_results:
        if mem["id"] not in seen_ids:
            seen_ids.add(mem["id"])
            mem["tier"] = 1
            all_memories.append(mem)
            tier_ids[1].append(mem["id"])

    # --- Tier 2: Brain State ---
    tier_2_results = _run_tier_queries(
        conn, defaults["tier_2_queries"], encode_fn, recall_hybrid_fn,
        per_query_k=3, budget=tier_2_budget,
    )
    for mem in tier_2_results:
        if mem["id"] not in seen_ids:
            seen_ids.add(mem["id"])
            mem["tier"] = 2
            all_memories.append(mem)
            tier_ids[2].append(mem["id"])

    # --- Tier 3: Focus-vector dependent ---
    tier_3_results = _run_focus_recall(
        conn, focus_vec, recall_hybrid_fn,
        budget=tier_3_budget, seen_ids=seen_ids,
    )

    # Apply weight reranking to Tier 3
    tier_3_reranked = _apply_tier_weights(tier_3_results, weights)

    for mem in tier_3_reranked:
        if mem["id"] not in seen_ids:
            seen_ids.add(mem["id"])
            mem["tier"] = 3
            all_memories.append(mem)
            tier_ids[3].append(mem["id"])

    # Check ambiguity: if Tier 3 produced fewer than 3 results, signals
    # were too weak for strong focus.
    ambiguous = len(tier_ids[3]) < 3

    elapsed_ms = int((time.time() - t0) * 1000)

    result = {
        "memories_loaded": len(all_memories),
        "tiers": tier_ids,
        "memories": all_memories,
        "focus_summary": _build_signal_narrative(
            signals if signals is not None else collect_signals()
        ) if show_anticipated else "",
        "ambiguous": ambiguous,
        "time_ms": elapsed_ms,
        "owner_defaults_file": defaults["owner_file"],
    }

    return result


def _run_tier_queries(
    conn: sqlite3.Connection,
    queries: List[str],
    encode_fn: Callable[[str, str], bytes],
    recall_hybrid_fn: Optional[Callable],
    per_query_k: int = 3,
    budget: int = 4,
) -> List[Dict[str, Any]]:
    """Run a list of tier queries and return deduplicated results up to budget."""
    results: List[Dict[str, Any]] = []
    seen: set = set()

    for query_text in queries:
        if len(results) >= budget:
            break
        query_vec = encode_fn(query_text, "search_query")
        hits = _single_recall(
            conn, query_text, query_vec, recall_hybrid_fn, top_k=per_query_k,
        )
        for hit in hits:
            if hit["id"] not in seen and len(results) < budget:
                seen.add(hit["id"])
                results.append(hit)

    return results


def _single_recall(
    conn: sqlite3.Connection,
    query: str,
    query_vec: bytes,
    recall_hybrid_fn: Optional[Callable],
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Execute a single recall query, using hybrid if available, else vector-only."""
    if recall_hybrid_fn is not None:
        try:
            return recall_hybrid_fn(
                conn, query=query, query_vec=query_vec,
                top_k=top_k, mode="no-rerank",
            )
        except Exception as exc:
            logger.warning("Hybrid recall failed for anticipatory query, "
                           "falling back to vector-only: %s", exc)

    # Fallback: direct vector search (fast, no reranker overhead).
    return _vector_search_fallback(conn, query_vec, top_k)


def _vector_search_fallback(
    conn: sqlite3.Connection,
    query_vec: bytes,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Pure vector search fallback. No FTS5, no reranker. Synthetic rows (dream / playbook synthesis) are never
    anticipated as memories (the dream fix, R1-M8): the hybrid path diverts them before the reranker, and this path,
    the recent-stores focus signal and the hydration exclude them by provenance."""
    try:
        rows = conn.execute(
            f"""SELECT me.memory_id, me.distance
               FROM {_resolve_embed_tier(conn)[0]} me
               JOIN memories m ON m.id = me.memory_id
               WHERE me.embedding MATCH ? AND k = ?
                 AND m.is_archived = 0 AND COALESCE(m.derived_from, '') NOT IN ('dream_synthesis', 'playbook_synthesis')
               LIMIT ?""",
            # F-C2 (curation_BETA). sqlite-vec applies `k` INSIDE the KNN, so
            # `AND m.is_archived = 0` filters AFTER the k nearest are chosen: any
            # archived neighbour silently costs a result. Measured over 200 seeded-random
            # probes at k=5 the shortfall was 19.8%, and 5 of 200 returned ZERO where raw
            # KNN returned 5. Over-fetch, then trim.
            (query_vec, top_k * 4, top_k),
        ).fetchall()
    except sqlite3.OperationalError:
        # sqlite-vec might need loading
        try:
            import sqlite_vec
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            rows = conn.execute(
                f"""SELECT me.memory_id, me.distance
                   FROM {_resolve_embed_tier(conn)[0]} me
                   JOIN memories m ON m.id = me.memory_id
                   WHERE me.embedding MATCH ? AND k = ?
                     AND m.is_archived = 0 AND COALESCE(m.derived_from, '') NOT IN ('dream_synthesis', 'playbook_synthesis')
                   LIMIT ?""",
                # F-C2 (curation_BETA). sqlite-vec applies `k` INSIDE the KNN, so
                # `AND m.is_archived = 0` filters AFTER the k nearest are chosen: any
                # archived neighbour silently costs a result. Measured over 200 seeded-random
                # probes at k=5 the shortfall was 19.8%, and 5 of 200 returned ZERO where raw
                # KNN returned 5. Over-fetch, then trim.
                (query_vec, top_k * 4, top_k),
            ).fetchall()
        except Exception:
            return []

    if not rows:
        return []

    ids = [r[0] for r in rows]
    dist_map = {r[0]: r[1] for r in rows}
    placeholders = ",".join("?" * len(ids))
    mem_rows = conn.execute(
        f"""SELECT id, content, summary, category, importance,
                   source_project, access_count, created_at
            FROM memories
            WHERE id IN ({placeholders}) AND is_archived = 0
              AND COALESCE(derived_from, '') NOT IN ('dream_synthesis', 'playbook_synthesis')""",
        ids,
    ).fetchall()

    results = []
    for mr in mem_rows:
        tags = [
            t[0]
            for t in conn.execute(
                "SELECT tag FROM memory_tags WHERE memory_id = ?", (mr[0],)
            ).fetchall()
        ]
        results.append({
            "id": mr[0],
            "content": mr[1],
            "summary": mr[2],
            "category": mr[3],
            "importance": mr[4],
            "source_project": mr[5],
            "access_count": mr[6],
            "created_at": mr[7],
            "tags": tags,
            "distance": dist_map.get(mr[0], 999.0),
            "score": 1.0 - dist_map.get(mr[0], 1.0),
        })

    results.sort(key=lambda x: x["distance"])
    return results


def _run_focus_recall(
    conn: sqlite3.Connection,
    focus_vec: bytes,
    recall_hybrid_fn: Optional[Callable],
    budget: int = 7,
    seen_ids: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Run the focus-vector recall for Tier 3. Oversamples then filters."""
    seen = seen_ids or set()
    oversample = budget * 3

    if recall_hybrid_fn is not None:
        try:
            # Use no-rerank mode for speed (<500ms vs ~1s with reranker).
            # Pre-cognitive recall must be fast; the reranker overhead is
            # not justified for anticipatory loading where false positives
            # are harmless (unused memories just get ignored).
            candidates = recall_hybrid_fn(
                conn, query="", query_vec=focus_vec,
                top_k=oversample, mode="no-rerank",
            )
        except Exception as exc:
            logger.warning("Hybrid focus recall failed: %s", exc)
            candidates = _vector_search_fallback(conn, focus_vec, oversample)
    else:
        candidates = _vector_search_fallback(conn, focus_vec, oversample)

    # Filter out already-seen + excluded tags.
    filtered = []
    for c in candidates:
        if c["id"] in seen:
            continue
        c_tags = set(c.get("tags", []))
        if c_tags & EXCLUDED_TAGS:
            continue
        filtered.append(c)

    return filtered[:budget * 2]  # Return extra for weight-based reranking.


def _apply_tier_weights(
    memories: List[Dict[str, Any]],
    weights: Dict[str, float],
) -> List[Dict[str, Any]]:
    """Apply adaptive recall map weights to memory candidates.

    Each memory's score is boosted by the maximum weight among its tags.
    Memories whose tags match Always-Priority clusters get the full boost;
    Low-Priority memories are dampened. Memories with no matching tags
    get a neutral weight of 0.5.
    """
    scored = []
    for mem in memories:
        mem_tags = set(mem.get("tags", []))
        tag_weights = [weights[t] for t in mem_tags if t in weights]
        max_weight = max(tag_weights) if tag_weights else 0.5

        # Also boost high-importance memories.
        importance = mem.get("importance", 0.5)
        importance_boost = 1.0 if importance >= 0.9 else (0.9 if importance >= 0.8 else 0.8)

        composite = mem.get("score", 0.5) * max_weight * importance_boost
        mem["anticipated_score"] = composite
        scored.append(mem)

    scored.sort(key=lambda x: x["anticipated_score"], reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Context Injection Formatting
# ---------------------------------------------------------------------------

def format_context_injection(
    recall_result: Dict[str, Any],
    verbose: bool = False,
) -> str:
    """Format anticipatory recall results for session context injection.

    Default (verbose=False): compact one-liner per memory.
    Verbose (verbose=True): full summary + category + tags per memory.
    """
    memories = recall_result.get("memories", [])
    if not memories:
        return "[Pre-Cognitive Recall: no anticipatory memories loaded]"

    lines = []
    tier_labels = {1: "Identity", 2: "BrainState", 3: "Anticipated"}

    if verbose:
        lines.append(
            f"=== Pre-Cognitive Recall ({recall_result['memories_loaded']} memories, "
            f"{recall_result['time_ms']}ms) ==="
        )
        if recall_result.get("ambiguous"):
            lines.append(
                "[Ambiguous focus — fell back to identity + brain state defaults]"
            )
        if recall_result.get("focus_summary"):
            lines.append(f"Focus: {recall_result['focus_summary']}")
        lines.append("")

    for mem in memories:
        tier = mem.get("tier", 3)
        label = tier_labels.get(tier, "?")
        mid = mem["id"]
        summary = (mem.get("summary") or "")[:200]
        category = mem.get("category", "?")

        if verbose:
            tags = ", ".join(mem.get("tags", [])[:5])
            lines.append(
                f"  [{label}] #{mid} ({category}) — {summary}"
            )
            if tags:
                lines.append(f"    Tags: {tags}")
        else:
            lines.append(
                f"  #{mid} [{label}] {summary[:120]}"
            )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Log Table (for tracking anticipatory recall accuracy over time)
# ---------------------------------------------------------------------------

def log_anticipation(
    conn: sqlite3.Connection,
    signals: Dict[str, Any],
    recall_result: Dict[str, Any],
    session_id: Optional[str] = None,
) -> Optional[int]:
    """Log an anticipatory recall event for future analysis.

    Only works if migration 015 has been applied (silently skips otherwise).
    """
    if not is_precognitive_migration_applied(conn):
        return None

    try:
        cursor = conn.execute(
            """INSERT INTO pre_cognitive_recall_log
               (session_id, signals_json, memory_ids_json, tier_ids_json,
                ambiguous, time_ms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                session_id,
                json.dumps(signals, default=str),
                json.dumps([m["id"] for m in recall_result.get("memories", [])]),
                json.dumps(recall_result.get("tiers", {})),
                1 if recall_result.get("ambiguous") else 0,
                recall_result.get("time_ms", 0),
            ),
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.Error as exc:
        logger.warning("Failed to log anticipation: %s", exc)
        return None


def log_anticipation_feedback(
    conn: sqlite3.Connection,
    log_id: int,
    memories_used: List[int],
    memories_irrelevant: Optional[List[int]] = None,
    feedback_note: Optional[str] = None,
) -> bool:
    """Record which anticipated memories were actually used in the session.

    This feeds the self-evolution loop: memories that are consistently
    anticipated but never used should get their cluster weight reduced.
    """
    if not is_precognitive_migration_applied(conn):
        return False

    try:
        conn.execute(
            """UPDATE pre_cognitive_recall_log
               SET memories_used_json = ?,
                   memories_irrelevant_json = ?,
                   feedback_note = ?,
                   feedback_at = datetime('now')
               WHERE id = ?""",
            (
                json.dumps(memories_used),
                json.dumps(memories_irrelevant or []),
                feedback_note,
                log_id,
            ),
        )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        logger.warning("Failed to log anticipation feedback: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

MIGRATION_016_SQL = """
-- Pre-Cognitive Recall Log (L4 Piece 2B)
-- Tracks anticipatory recall events and their feedback for self-evolution.
CREATE TABLE IF NOT EXISTS pre_cognitive_recall_log (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id             TEXT,
    signals_json           TEXT NOT NULL,
    memory_ids_json        TEXT NOT NULL,
    tier_ids_json          TEXT NOT NULL,
    ambiguous              INTEGER NOT NULL DEFAULT 0,
    time_ms                INTEGER NOT NULL DEFAULT 0,
    memories_used_json     TEXT,
    memories_irrelevant_json TEXT,
    feedback_note          TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    feedback_at            TEXT
);

CREATE INDEX IF NOT EXISTS idx_pcrl_session ON pre_cognitive_recall_log(session_id);
CREATE INDEX IF NOT EXISTS idx_pcrl_created ON pre_cognitive_recall_log(created_at);
CREATE INDEX IF NOT EXISTS idx_pcrl_ambiguous ON pre_cognitive_recall_log(ambiguous);

-- Update schema version.
INSERT OR REPLACE INTO metadata (key, value, updated_at)
VALUES ('schema_version', '3.9-L4-pre-cognitive-recall', datetime('now'));
"""


def is_precognitive_migration_applied(conn: sqlite3.Connection) -> bool:
    """Check whether the pre-cognitive recall log table exists."""
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pre_cognitive_recall_log'"
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def apply_precognitive_migration(
    db_path: str,
    migration_sql: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply migration 015 idempotently."""
    conn = sqlite3.connect(db_path, timeout=60.0)
    if is_precognitive_migration_applied(conn):
        conn.close()
        return {"status": "ok", "action": "migration_015_already_applied"}

    sql = migration_sql or MIGRATION_016_SQL
    try:
        conn.executescript(sql)
        conn.commit()
        conn.close()
        return {"status": "ok", "action": "migration_015_applied"}
    except sqlite3.Error as exc:
        conn.close()
        return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# Unit Tests
# ---------------------------------------------------------------------------

def unit_test_pre_cognitive_recall():
    """Self-contained unit tests. Run via:
        python3 pre_cognitive_recall.py --test
    """
    import tempfile
    import shutil

    passed = 0
    failed = 0
    errors = []

    def check(name: str, condition: bool, detail: str = ""):
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
            errors.append(name)

    print("=" * 60)
    print("Pre-Cognitive Recall — Unit Tests")
    print("=" * 60)

    # -----------------------------------------------------------------------
    # Signal Collection Tests
    # -----------------------------------------------------------------------
    print("\n--- Signal Collection ---")

    # Test 1: collect_signals returns expected keys
    sigs = collect_signals(project_root="/tmp")
    expected_keys = {
        "recent_edits", "recent_stores", "git_branch",
        "time_of_day", "day_of_week", "day_name", "hour",
        "temporal_hint", "automation_hint", "project_slug",
    }
    check("collect_signals returns all keys",
          expected_keys.issubset(set(sigs.keys())),
          f"Missing: {expected_keys - set(sigs.keys())}")

    # Test 2: time_of_day classification
    check("classify 6am as early_morning",
          _classify_time_of_day(6) == "early_morning")
    check("classify 10am as morning",
          _classify_time_of_day(10) == "morning")
    check("classify 14pm as afternoon",
          _classify_time_of_day(14) == "afternoon")
    check("classify 19pm as evening",
          _classify_time_of_day(19) == "evening")
    check("classify 23pm as night",
          _classify_time_of_day(23) == "night")
    check("classify 3am as night",
          _classify_time_of_day(3) == "night")

    # Test 3: project slug derivation
    # Assert the MECHANISM, not one operator's alias table. The old test pinned
    # two real directory names, so it passed only on the machine that inspired it
    # and told a stranger nothing about whether the function works.
    _prev = os.environ.get("VOIDLOOM_PROJECT_SLUGS")
    try:
        os.environ["VOIDLOOM_PROJECT_SLUGS"] = "Some_Big_Project=bigproj"
        check("slug honours an operator override",
              _derive_project_slug(Path("/somewhere/Some_Big_Project")) == "bigproj")
        check("slug falls through when the override does not match",
              _derive_project_slug(Path("/tmp/my_cool_project")) == "my-cool-project")
        os.environ["VOIDLOOM_PROJECT_SLUGS"] = ""
        check("slug derives generically with no overrides set",
              _derive_project_slug(Path("/tmp/Some_Big_Project")) == "some-big-project")
        check("slug ignores a malformed override entry",
              _derive_project_slug(Path("/tmp/my_cool_project")) == "my-cool-project")
    finally:
        if _prev is None:
            os.environ.pop("VOIDLOOM_PROJECT_SLUGS", None)
        else:
            os.environ["VOIDLOOM_PROJECT_SLUGS"] = _prev

    # Test 4: temporal hint
    from datetime import datetime as dt_cls
    mon_9am = dt_cls(2026, 5, 25, 9, 0, 0)  # Monday 9am (May 25, 2026 is Monday)
    hint = _temporal_hint(mon_9am)
    check("temporal hint for Monday 9am contains 'Monday'",
          "Monday" in hint or "planning" in hint,
          f"Got: {hint}")

    # -----------------------------------------------------------------------
    # Signal Narrative Tests
    # -----------------------------------------------------------------------
    print("\n--- Signal Narrative ---")

    # Test 5: empty signals produce default narrative
    empty_sigs = {
        "recent_edits": [], "recent_stores": [],
        "git_branch": None, "project_slug": "",
        "temporal_hint": "", "automation_hint": None,
        "day_name": "", "hour": 0,
    }
    narrative = _build_signal_narrative(empty_sigs)
    check("empty signals produce default narrative",
          "wizard library" in narrative.lower() or "session start" in narrative.lower(),
          f"Got: {narrative}")

    # Test 6: signals with edits
    rich_sigs = {
        "recent_edits": [("claude_memory.py", "2026-05-27T09:00:00")],
        "recent_stores": [{"summary": "Tested pre-cognitive recall module", "tags": []}],
        "git_branch": "feature/precog",
        "project_slug": "engineering-workstation",
        "temporal_hint": "midweek deep work build wizard library",
        "automation_hint": "Completed sleep loop consolidation",
        "day_name": "Tuesday",
        "hour": 14,
    }
    narrative = _build_signal_narrative(rich_sigs)
    check("narrative includes file names",
          "claude_memory.py" in narrative)
    check("narrative includes branch",
          "precog" in narrative)
    check("narrative includes project",
          "engineering-workstation" in narrative)

    # -----------------------------------------------------------------------
    # Focus Vector Tests (mock encode)
    # -----------------------------------------------------------------------
    print("\n--- Focus Vector ---")

    def mock_encode(text: str, prefix: str) -> bytes:
        """Deterministic mock encoder: hash the text into a 768-dim vector."""
        import hashlib
        h = hashlib.sha256(f"{prefix}: {text}".encode()).digest()
        vec = [float(b) / 255.0 for b in h] * 24  # 32 * 24 = 768
        return struct.pack(f"{768}f", *vec[:768])

    vec = compute_focus_vector(rich_sigs, mock_encode)
    check("focus vector is bytes", isinstance(vec, bytes))
    check("focus vector length is 768 floats",
          len(vec) == 768 * 4,
          f"Got {len(vec)} bytes, expected {768 * 4}")

    # Determinism: same signals produce same vector.
    vec2 = compute_focus_vector(rich_sigs, mock_encode)
    check("focus vector is deterministic", vec == vec2)

    # -----------------------------------------------------------------------
    # Tier Weight Tests
    # -----------------------------------------------------------------------
    print("\n--- Tier Weights ---")

    test_memories = [
        {"id": 1, "tags": ["wizard-library", "architecture"], "importance": 0.95, "score": 0.8},
        {"id": 2, "tags": ["campaign", "outreach"], "importance": 0.5, "score": 0.9},
        {"id": 3, "tags": ["sleep-loop-consolidated"], "importance": 0.85, "score": 0.7},
        {"id": 4, "tags": [], "importance": 0.5, "score": 0.85},
    ]
    reranked = _apply_tier_weights(test_memories, DEFAULT_TIER_WEIGHTS)

    check("wizard-library memory ranks first",
          reranked[0]["id"] == 1,
          f"Got id={reranked[0]['id']}")
    check("campaign memory ranks low",
          reranked[-1]["id"] == 2 or reranked[-2]["id"] == 2,
          f"Campaign at position {[m['id'] for m in reranked].index(2)}")
    check("all memories have anticipated_score",
          all("anticipated_score" in m for m in reranked))

    # -----------------------------------------------------------------------
    # Migration Tests
    # -----------------------------------------------------------------------
    print("\n--- Migration ---")

    tmp_db = tempfile.mktemp(suffix=".db")
    try:
        conn = sqlite3.connect(tmp_db)
        conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        conn.commit()

        check("migration not yet applied",
              not is_precognitive_migration_applied(conn))

        result = apply_precognitive_migration(tmp_db)
        check("migration applies successfully",
              result["status"] == "ok",
              f"Got: {result}")

        conn2 = sqlite3.connect(tmp_db)
        check("migration is now applied",
              is_precognitive_migration_applied(conn2))

        # Idempotent re-application.
        result2 = apply_precognitive_migration(tmp_db)
        check("migration is idempotent",
              result2.get("action", "").endswith("already_applied"),
              f"Got: {result2}")

        conn2.close()
        conn.close()
    finally:
        try:
            os.unlink(tmp_db)
        except OSError:
            pass

    # -----------------------------------------------------------------------
    # Log + Feedback Tests
    # -----------------------------------------------------------------------
    print("\n--- Log + Feedback ---")

    tmp_db2 = tempfile.mktemp(suffix=".db")
    try:
        conn = sqlite3.connect(tmp_db2)
        conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        conn.executescript(MIGRATION_016_SQL)
        conn.commit()

        test_signals = {"recent_edits": [], "project_slug": "test"}
        test_result = {
            "memories_loaded": 3,
            "memories": [{"id": 10}, {"id": 20}, {"id": 30}],
            "tiers": {1: [10], 2: [20], 3: [30]},
            "ambiguous": False,
            "time_ms": 150,
        }

        log_id = log_anticipation(conn, test_signals, test_result, session_id="test-001")
        check("log_anticipation returns an ID",
              log_id is not None and log_id > 0,
              f"Got: {log_id}")

        fb_ok = log_anticipation_feedback(conn, log_id, memories_used=[10, 20])
        check("log_anticipation_feedback succeeds", fb_ok)

        row = conn.execute(
            "SELECT memories_used_json FROM pre_cognitive_recall_log WHERE id = ?",
            (log_id,),
        ).fetchone()
        check("feedback is persisted",
              row is not None and "10" in row[0],
              f"Got: {row}")

        conn.close()
    finally:
        try:
            os.unlink(tmp_db2)
        except OSError:
            pass

    # -----------------------------------------------------------------------
    # Format Tests
    # -----------------------------------------------------------------------
    print("\n--- Formatting ---")

    test_recall = {
        "memories_loaded": 2,
        "time_ms": 120,
        "ambiguous": False,
        "focus_summary": "Working on wizard library pre-cognitive recall",
        "memories": [
            {"id": 100, "tier": 1, "summary": "persona identity",
             "category": "project_knowledge", "tags": ["persona-evolution"]},
            {"id": 200, "tier": 3, "summary": "Recent ACE loop pattern",
             "category": "skill_lesson", "tags": ["ace-loop"]},
        ],
    }

    compact = format_context_injection(test_recall, verbose=False)
    check("compact format includes memory IDs",
          "#100" in compact and "#200" in compact)
    check("compact format includes tier labels",
          "Identity" in compact and "Anticipated" in compact)

    verbose_out = format_context_injection(test_recall, verbose=True)
    check("verbose format includes header",
          "Pre-Cognitive Recall" in verbose_out)
    check("verbose format includes categories",
          "project_knowledge" in verbose_out)

    empty_recall = {"memories_loaded": 0, "memories": []}
    empty_out = format_context_injection(empty_recall)
    check("empty recall produces fallback message",
          "no anticipatory memories" in empty_out.lower())

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print("\n" + "=" * 60)
    total = passed + failed
    print(f"Results: {passed}/{total} passed, {failed} failed")
    if errors:
        print(f"Failed tests: {', '.join(errors)}")
    print("=" * 60)

    return {"passed": passed, "failed": failed, "total": total, "errors": errors}


# ---------------------------------------------------------------------------
# CLI entry point (for standalone testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--test" in sys.argv:
        result = unit_test_pre_cognitive_recall()
        sys.exit(0 if result["failed"] == 0 else 1)
    elif "--collect-signals" in sys.argv:
        root = sys.argv[sys.argv.index("--collect-signals") + 1] if len(sys.argv) > sys.argv.index("--collect-signals") + 1 else None
        signals = collect_signals(project_root=root)
        print(json.dumps(signals, indent=2, default=str))
    elif "--narrative" in sys.argv:
        root = sys.argv[sys.argv.index("--narrative") + 1] if len(sys.argv) > sys.argv.index("--narrative") + 1 else None
        signals = collect_signals(project_root=root)
        print(_build_signal_narrative(signals))
    else:
        print("Usage:")
        print("  python3 pre_cognitive_recall.py --test")
        print("  python3 pre_cognitive_recall.py --collect-signals [path]")
        print("  python3 pre_cognitive_recall.py --narrative [path]")
        sys.exit(0)
