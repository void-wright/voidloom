#!/usr/bin/env python3
"""Kind Taxonomy module for Wizard Library L2 Component 2.

Stand-alone module that classifies memories into four kinds
(episodic, semantic, procedural, reflection) and manages the
schema migration + backfill pipeline.

Public API
----------
``classify_memory(summary, content, category, tags) -> Tuple[str, float]``
    Returns (kind, confidence) using Tier 1/2/3 heuristic cascade.

``backfill_kinds(db_path, dry_run=False) -> dict``
    Classifies all existing memories. Returns audit dict.

``apply_kind_taxonomy_migration(db_path, migration_sql_path) -> dict``
    Idempotent migration runner. Mirrors C1's apply_fts5_migration pattern.

``unit_test_classify_memory()``
    Self-contained unit tests with >=20 cases across all 4 kinds.

CLI interface
-------------
::

    python3 kind_taxonomy.py apply-migration <db_path> <migration_sql>
    python3 kind_taxonomy.py backfill <db_path> [--dry-run]
    python3 kind_taxonomy.py --unit-tests

Phase 0 memories: #3912 (kind taxonomy design), #3911 (canonical L2),
#3924 (dependency map), #3929 (forward pointer map).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# Constants
# ============================================================

VALID_KINDS = ("episodic", "semantic", "procedural", "reflection")

# ============================================================
# L5-F FadeMem per-kind half-lives (the forgetting curve)
# ============================================================
# Each kind decays at its own rate: durable knowledge (semantic) fades slowly,
# transient state fades fast. These are the half-lives (in days) the L5-F ACT-R
# activation view uses for base-level decay -- a recall-TIME-ONLY derived view,
# never a stored salience column. The 'status' key covers fast-decay status
# notes that may not carry a formal kind. A kind absent here uses DEFAULT.
KIND_HALF_LIFE_DAYS = {
    "semantic":   365.0,   # durable knowledge / principles -- fade slowly
    "procedural": 270.0,   # how-to / skills -- fade slowly
    "reflection": 120.0,   # self-observations -- fade moderately
    "episodic":    60.0,   # events / specific occurrences -- fade faster
    "status":      14.0,   # transient state notes -- fade fast
}
DEFAULT_HALF_LIFE_DAYS = 90.0


def half_life_days(kind):
    """The FadeMem half-life (days) for a memory kind. Unknown kind -> DEFAULT."""
    return KIND_HALF_LIFE_DAYS.get(kind, DEFAULT_HALF_LIFE_DAYS)


def base_level_decay(kind, age_days, recency_count=1):
    """ACT-R base-level activation from FadeMem half-lives. A recall-TIME-ONLY
    derived view (computed on read, never stored). Returns a multiplier in (0,1]:
    1.0 at age 0, halving every half_life_days. recency_count (number of recent
    accesses) gently flattens the decay (well-used memories fade slower)."""
    hl = half_life_days(kind)
    eff_hl = hl * (1.0 + 0.25 * max(0, recency_count - 1))  # usage extends the half-life
    return 0.5 ** (max(0.0, age_days) / max(1.0, eff_hl))


# ------------------------------------------------------------------
# L5-F ACT-R activation -- THE SINGLE salience-decay source (iter-2 P2).
# ------------------------------------------------------------------
# This is the ONE recall-TIME-ONLY derived view that REPLACES the two formerly
# conflicting importance mutators:
#   (1) sleep_loop.rerank_importance (Stage 5) -- which used to WRITE stored
#       importance via a boost/decay rule; and
#   (2) claude_memory.cmd_maintain's decay-sweep -- which used its OWN, DIFFERENT
#       `1 - (days/365)*(1/access_count)` effective formula to decide archiving.
# Both now consume THIS function instead of computing their own decay, so there is
# exactly one decay formula reachable in the substrate. It is NEVER stored as a
# 5th salience column -- it is computed on demand from the row's static fields.
#
# Composition (additive, weighted; clamped to [0,1]):
#   base-level decay   (FadeMem per-kind half-life, recency-flattened)  -- the time term
#   foundation salience (PageRank-derived structural importance)         -- the graph term
#   importance          (the stored importance signal)                   -- the explicit term
# Weights default to base 0.50 / foundation 0.30 / importance 0.20 -- the time term
# dominates (decay is the point) but a load-bearing (high-foundation) or explicitly
# important memory keeps a floor of activation so it never decays into the archive.
ACTR_WEIGHTS = {"base": 0.50, "foundation": 0.30, "importance": 0.20}


def actr_activation(kind, age_days, recency_count=1, foundation_score=0.0,
                    importance=0.0, weights=None):
    """The single ACT-R activation view (recall-TIME-ONLY, never stored).

    Returns a salience score in [0, 1] composed of the FadeMem base-level decay
    (time), the foundation_score (graph-structural salience), and the stored
    importance (explicit salience). This is the ONLY decay formula in the
    substrate: sleep_loop Stage 5 and cmd_maintain both read it, neither computes
    its own. Higher = more active = keep; very low = a genuine fade candidate.

    age_days: days since the memory was last meaningfully accessed (or created).
    recency_count: number of recent accesses (extends the effective half-life).
    foundation_score / importance: the row's static salience signals (None -> 0).
    """
    w = dict(ACTR_WEIGHTS)
    if weights:
        w.update(weights)

    def _f(x):
        try:
            return float(x) if x is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    base = base_level_decay(kind, age_days, recency_count)   # in (0, 1]
    fnd = max(0.0, min(1.0, _f(foundation_score)))
    imp = max(0.0, min(1.0, _f(importance)))

    total_w = w["base"] + w["foundation"] + w["importance"]
    if total_w <= 0:
        return base
    activation = (w["base"] * base + w["foundation"] * fnd + w["importance"] * imp) / total_w
    return max(0.0, min(1.0, activation))


# Backfill confidence: heuristic-assigned, not human-verified.
# Drew can override via `claude_memory.py update --id <N> --kind <X>`.
BACKFILL_CONFIDENCE = 0.85

# Low confidence for ambiguous classifications where content
# heuristics couldn't decide with high certainty.
LOW_CONFIDENCE = 0.65

# ============================================================
# Tier 1: Category-to-kind mapping (covers ~95%)
# ============================================================

_CATEGORY_KIND_MAP: Dict[str, str] = {
    "skill_lesson":        "procedural",
    "workflow_pattern":    "procedural",
    "tool_optimization":   "procedural",
    "technical_decision":  "semantic",
    "domain_knowledge":    "semantic",
    "user_preference":     "semantic",
    "people_context":      "semantic",
    "error_pattern":       "semantic",
    "project_knowledge":   "episodic",
    "campaign_state":      "episodic",
    "insight":             "reflection",
    "correction":          "reflection",
    # Edge categories found in production data:
    "architecture":        "semantic",
    "project-structure":   "semantic",
}

# ============================================================
# Tier 2: Tag-based overrides (~3-5%)
# ============================================================

# tag_pattern -> override_kind
# These override the Tier 1 mapping when present.
_TAG_OVERRIDES: Dict[str, str] = {
    "ace-curator":          "procedural",
    "phase-0-bootstrap":    "semantic",
    "self-heal-candidate":  "procedural",
    "methodology":          "procedural",
    "milestone":            "episodic",
    "calibration":          "reflection",
}

# ============================================================
# Tier 3: Content-based heuristics (~1-2%)
# ============================================================

# (regex_pattern, override_kind, description)
_CONTENT_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r"^SKILL\s", re.IGNORECASE),            "procedural", "SKILL prefix in summary"),
    (re.compile(r"FIRST\s+CLOSURE|COMPLETE", re.IGNORECASE), "episodic",   "milestone language"),
    (re.compile(r"PATTERN:|PRINCIPLE:", re.IGNORECASE),  "semantic",   "distilled pattern/principle"),
    (re.compile(r"\bI\s+noticed\b|observation:", re.IGNORECASE), "reflection", "self-observation"),
    (re.compile(r"\brecipe\b|\bhow[- ]to\b|\bstep[- ]by[- ]step\b", re.IGNORECASE), "procedural", "recipe/howto"),
    (re.compile(r"\bSEED\b.*skill|skill.*\bSEED\b", re.IGNORECASE), "procedural", "SEED skill entry"),
    (re.compile(r"\bPROTECTED\b.*skill|skill.*\bPROTECTED\b", re.IGNORECASE), "procedural", "PROTECTED skill entry"),
    (re.compile(r"\bACTIVE\b.*skill|skill.*\bACTIVE\b", re.IGNORECASE), "procedural", "ACTIVE skill entry"),
]


# ============================================================
# Public API
# ============================================================

def classify_memory(
    summary: str,
    content: str,
    category: str,
    tags: List[str],
) -> Tuple[str, float]:
    """Classify a memory into one of four kinds using tiered heuristics.

    Returns (kind, confidence) where confidence is in [0.0, 1.0].

    Tier 1: Category mapping (highest coverage, ~95%)
    Tier 2: Tag-based overrides (overrides Tier 1 for specific tags)
    Tier 3: Content-based heuristics (for ambiguous Tier 1 results)

    If no heuristic matches, falls back to 'episodic' with LOW_CONFIDENCE.
    """
    summary = summary or ""
    content = content or ""
    tags = tags or []

    # --- Tier 2 first: tag overrides take priority over category ---
    # Check tags before category because certain tags (e.g., ace-curator
    # on a project_knowledge memory) signal a definitive kind.
    for tag in tags:
        tag_lower = tag.lower().strip()
        if tag_lower in _TAG_OVERRIDES:
            return (_TAG_OVERRIDES[tag_lower], BACKFILL_CONFIDENCE)

    # --- Tier 1: Category mapping ---
    tier1_kind = _CATEGORY_KIND_MAP.get(category)
    if tier1_kind:
        # Check Tier 3 for potential override on episodic memories.
        # A project_knowledge memory with "PATTERN:" in summary is
        # more accurately classified as semantic.
        if tier1_kind == "episodic":
            tier3 = _check_content_patterns(summary, content)
            if tier3 is not None:
                return (tier3, BACKFILL_CONFIDENCE)
        return (tier1_kind, BACKFILL_CONFIDENCE)

    # --- Tier 3: Content heuristics for unknown categories ---
    tier3 = _check_content_patterns(summary, content)
    if tier3 is not None:
        return (tier3, LOW_CONFIDENCE)

    # --- Fallback: episodic with low confidence ---
    return ("episodic", LOW_CONFIDENCE)


def _check_content_patterns(summary: str, content: str) -> Optional[str]:
    """Scan summary + content for Tier 3 pattern matches."""
    # Check summary first (more specific), then content
    for pattern, kind, _desc in _CONTENT_PATTERNS:
        if pattern.search(summary):
            return kind
    for pattern, kind, _desc in _CONTENT_PATTERNS:
        if pattern.search(content[:500]):  # only scan first 500 chars of content
            return kind
    return None


def backfill_kinds(db_path: str, dry_run: bool = False) -> Dict[str, Any]:
    """Backfill kind classification for all existing memories.

    Args:
        db_path: Path to the SQLite database.
        dry_run: If True, compute classifications but do NOT modify the DB.

    Returns:
        Audit dict with per-memory classifications and summary statistics.
    """
    t0 = time.time()
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")

    # Verify kind column exists
    columns = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    if "kind" not in columns:
        conn.close()
        return {
            "status": "error",
            "message": "kind column not found. Run apply-migration first.",
        }

    # Fetch all non-archived memories
    rows = conn.execute(
        "SELECT id, summary, content, category FROM memories WHERE is_archived = 0"
    ).fetchall()

    # Build tag lookup: {memory_id: [tag1, tag2, ...]}
    tag_rows = conn.execute(
        "SELECT memory_id, tag FROM memory_tags"
    ).fetchall()
    tags_by_id: Dict[int, List[str]] = {}
    for mem_id, tag in tag_rows:
        tags_by_id.setdefault(mem_id, []).append(tag)

    audit: List[Dict[str, Any]] = []
    kind_counts: Dict[str, int] = {k: 0 for k in VALID_KINDS}
    tier_counts: Dict[str, int] = {"tier1": 0, "tier2": 0, "tier3": 0, "fallback": 0}
    low_confidence_ids: List[int] = []

    for mem_id, summary, content, category in rows:
        mem_tags = tags_by_id.get(mem_id, [])
        kind, confidence = classify_memory(summary, content, category, mem_tags)

        # Track which tier was used
        tier_used = _determine_tier(summary, content, category, mem_tags, kind)
        tier_counts[tier_used] += 1
        kind_counts[kind] += 1

        if confidence < BACKFILL_CONFIDENCE:
            low_confidence_ids.append(mem_id)

        audit.append({
            "memory_id": mem_id,
            "old_category": category,
            "new_kind": kind,
            "tier_used": tier_used,
            "confidence": confidence,
        })

    # Apply updates if not dry-run
    updated = 0
    if not dry_run:
        for entry in audit:
            conn.execute(
                "UPDATE memories SET kind = ?, confidence = ? WHERE id = ?",
                (entry["new_kind"], entry["confidence"], entry["memory_id"]),
            )
            updated += 1
        conn.commit()

    conn.close()

    elapsed_ms = int((time.time() - t0) * 1000)

    return {
        "status": "ok",
        "action": "backfill",
        "dry_run": dry_run,
        "total_memories": len(rows),
        "updated": updated,
        "kind_distribution": kind_counts,
        "tier_distribution": tier_counts,
        "low_confidence_count": len(low_confidence_ids),
        "low_confidence_ids": low_confidence_ids[:20],  # cap for readability
        "time_ms": elapsed_ms,
        "audit": audit,  # full audit for persistence
    }


def _determine_tier(
    summary: str, content: str, category: str, tags: List[str], result_kind: str
) -> str:
    """Determine which tier produced the classification (for audit)."""
    # Check Tier 2 first (tag overrides)
    for tag in tags:
        if tag.lower().strip() in _TAG_OVERRIDES:
            return "tier2"
    # Check Tier 1 (category mapping)
    if category in _CATEGORY_KIND_MAP:
        # If the result differs from Tier 1's mapping, Tier 3 overrode it
        tier1_kind = _CATEGORY_KIND_MAP[category]
        if tier1_kind != result_kind:
            return "tier3"
        return "tier1"
    # Unknown category: Tier 3 or fallback
    if _check_content_patterns(summary or "", content or "") is not None:
        return "tier3"
    return "fallback"


def is_kind_migration_applied(conn: sqlite3.Connection) -> bool:
    """Check if the kind taxonomy migration has been applied."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    return "kind" in columns and "confidence" in columns


def apply_kind_taxonomy_migration(
    db_path: str,
    migration_sql_path: str,
) -> Dict[str, Any]:
    """Apply the kind taxonomy migration idempotently.

    Mirrors C1's apply_fts5_migration pattern:
    1. Check if columns already exist
    2. Apply each ALTER individually with existence checks
    3. Bump schema version
    4. Verify post-conditions

    Args:
        db_path: Path to the SQLite database.
        migration_sql_path: Path to migration_002_kind_taxonomy.sql (reference only;
            we apply ALTERs programmatically for idempotency).

    Returns:
        Dict with migration status and details.
    """
    t0 = time.time()
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")

    existing_columns = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}

    # Track what we actually applied
    applied: List[str] = []
    skipped: List[str] = []

    # Each ALTER is individually guarded
    new_columns = [
        ("kind",            "TEXT DEFAULT 'episodic' CHECK(kind IN ('episodic', 'semantic', 'procedural', 'reflection'))"),
        ("derived_from",    "TEXT DEFAULT NULL"),
        ("consolidated_at", "TEXT DEFAULT NULL"),
        ("confidence",      "REAL DEFAULT 1.0 CHECK(confidence >= 0.0 AND confidence <= 1.0)"),
    ]

    for col_name, col_def in new_columns:
        if col_name in existing_columns:
            skipped.append(col_name)
        else:
            try:
                conn.execute(f"ALTER TABLE memories ADD COLUMN {col_name} {col_def}")
                applied.append(col_name)
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    skipped.append(col_name)
                else:
                    conn.close()
                    return {
                        "status": "error",
                        "message": f"ALTER TABLE failed for {col_name}: {e}",
                        "applied_before_error": applied,
                    }

    # Bump schema version
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("migration_002_kind_taxonomy_applied_at", datetime.now().isoformat()),
    )
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("schema_version", "2.1-L2-kind-taxonomy"),
    )

    conn.commit()

    # Verify post-conditions
    post_columns = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    missing = [c for c, _ in new_columns if c not in post_columns]

    schema_version = conn.execute(
        "SELECT value FROM metadata WHERE key='schema_version'"
    ).fetchone()

    conn.close()
    elapsed_ms = int((time.time() - t0) * 1000)

    if missing:
        return {
            "status": "error",
            "message": f"Post-migration verification failed. Missing columns: {missing}",
            "time_ms": elapsed_ms,
        }

    return {
        "status": "ok",
        "action": "apply-migration",
        "columns_applied": applied,
        "columns_skipped": skipped,
        "schema_version": schema_version[0] if schema_version else None,
        "time_ms": elapsed_ms,
    }


# ============================================================
# Unit tests
# ============================================================

def unit_test_classify_memory() -> Dict[str, Any]:
    """Self-contained unit tests for classify_memory.

    Runs >=20 cases across all 4 kinds + edge cases.
    Returns dict with pass/fail results.
    """
    results: List[Dict[str, Any]] = []
    passed = 0
    failed = 0

    def check(
        test_name: str,
        summary: str,
        content: str,
        category: str,
        tags: List[str],
        expected_kind: str,
        min_confidence: float = 0.0,
    ):
        nonlocal passed, failed
        kind, conf = classify_memory(summary, content, category, tags)
        ok = kind == expected_kind and conf >= min_confidence
        if ok:
            passed += 1
        else:
            failed += 1
        results.append({
            "test": test_name,
            "expected_kind": expected_kind,
            "actual_kind": kind,
            "confidence": round(conf, 2),
            "min_confidence": min_confidence,
            "passed": ok,
        })

    # --- Tier 1: Category mapping (10 cases) ---
    check("T1-01 skill_lesson -> procedural",
          "ACE skill architecture-002", "ACE skill content", "skill_lesson", [],
          "procedural", 0.8)

    check("T1-02 workflow_pattern -> procedural",
          "Git rebase workflow", "Step 1 checkout", "workflow_pattern", [],
          "procedural", 0.8)

    check("T1-03 tool_optimization -> procedural",
          "Gemini CLI -m auto flag", "Use -m auto", "tool_optimization", [],
          "procedural", 0.8)

    check("T1-04 technical_decision -> semantic",
          "Chose SQLite over Postgres", "Decision rationale", "technical_decision", [],
          "semantic", 0.8)

    check("T1-05 domain_knowledge -> semantic",
          "Widget market size 4.2B", "Annual growth 6.1%", "domain_knowledge", [],
          "semantic", 0.8)

    check("T1-06 error_pattern -> semantic",
          "FTS5 syntax error on hyphens", "Wrap in quotes", "error_pattern", [],
          "semantic", 0.8)

    check("T1-07 project_knowledge -> episodic",
          "L2-C1 iter-1 complete", "Closed at 9.62", "project_knowledge", [],
          "episodic", 0.8)

    check("T1-08 campaign_state -> episodic",
          "Q1 2026 launch status", "Shipped 3 releases", "campaign_state", [],
          "episodic", 0.8)

    check("T1-09 insight -> reflection",
          "Reflector catches gaps Generator misses", "Observed across 5 loops", "insight", [],
          "reflection", 0.8)

    check("T1-10 correction -> reflection",
          "CORRECTION to memory #291", "Empirically disproven", "correction", [],
          "reflection", 0.8)

    # --- Tier 2: Tag overrides (6 cases) ---
    check("T2-01 ace-curator overrides project_knowledge",
          "ACE skill from project", "Skill content", "project_knowledge", ["ace-curator"],
          "procedural", 0.8)

    check("T2-02 phase-0-bootstrap overrides project_knowledge",
          "L2 Phase 0 design", "Architecture decisions", "project_knowledge", ["phase-0-bootstrap"],
          "semantic", 0.8)

    check("T2-03 self-heal-candidate overrides insight",
          "Self-heal procedure", "Recovery steps", "insight", ["self-heal-candidate"],
          "procedural", 0.8)

    check("T2-04 methodology overrides project_knowledge",
          "Release checklist", "10-step methodology", "project_knowledge", ["methodology"],
          "procedural", 0.8)

    check("T2-05 milestone keeps episodic",
          "Project milestone reached", "v2 shipped", "project_knowledge", ["milestone"],
          "episodic", 0.8)

    check("T2-06 calibration overrides insight",
          "Calibration data", "Self-est 9.72 vs 9.65", "insight", ["calibration"],
          "reflection", 0.8)

    # --- Tier 3: Content patterns (6 cases) ---
    check("T3-01 SKILL prefix -> procedural",
          "SKILL fts5-sanitizer-wrap-001", "Sanitize FTS5 queries", "project_knowledge", [],
          "procedural", 0.8)

    check("T3-02 PATTERN: in content -> semantic",
          "Memory consolidation", "PATTERN: episodics cluster by topic", "project_knowledge", [],
          "semantic", 0.8)

    check("T3-03 FIRST CLOSURE -> episodic",
          "FIRST CLOSURE of Pipeline Phase 3", "Completed all steps", "project_knowledge", [],
          "episodic", 0.8)

    check("T3-04 I noticed -> reflection",
          "Debugging session", "I noticed that the reranker dominates latency", "project_knowledge", [],
          "reflection", 0.8)

    check("T3-05 SEED skill -> procedural",
          "New SEED skill identified", "SEED skill: monitor-then-create-pattern-001", "project_knowledge", [],
          "procedural", 0.8)

    check("T3-06 PROTECTED skill -> procedural",
          "Skill status", "PROTECTED skill entry for ace-loop-discipline", "project_knowledge", [],
          "procedural", 0.8)

    # --- Edge cases (4 cases) ---
    check("E-01 unknown category falls back",
          "Random observation", "Some content here", "project-structure", [],
          "semantic", 0.0)  # project-structure maps to semantic

    check("E-02 empty tags no crash",
          "Basic memory", "Content", "skill_lesson", [],
          "procedural", 0.8)

    check("E-03 empty summary uses content",
          "", "PATTERN: this is a pattern", "project_knowledge", [],
          "semantic", 0.8)

    check("E-04 completely unknown category + no content match",
          "Random", "Nothing special", "nonexistent_category", [],
          "episodic", 0.0)  # fallback

    # --- Tier 2 precedence over Tier 1 (2 cases) ---
    check("P-01 ace-curator on skill_lesson stays procedural",
          "ACE skill", "Content", "skill_lesson", ["ace-curator"],
          "procedural", 0.8)

    check("P-02 calibration on correction stays reflection",
          "Calibration correction", "Self-assessment", "correction", ["calibration"],
          "reflection", 0.8)

    # --- user_preference + people_context (2 cases) ---
    check("T1-11 user_preference -> semantic",
          "User prefers dark mode", "Dark theme", "user_preference", [],
          "semantic", 0.8)

    check("T1-12 people_context -> semantic",
          # A REAL PERSON'S NAME, BOUND TO A REAL ORG, shipped in a self-check
          # fixture (Crucible-3, pii_gate N:person-bound-to-org). The fixture
          # tests the people_context -> semantic mapping and does not need a
          # real identity to do it.
          "Rian Alderby is the Northwind account contact", "Team info",
          "people_context", [],
          "semantic", 0.8)

    return {
        "status": "ok" if failed == 0 else "failed",
        "total": passed + failed,
        "passed": passed,
        "failed": failed,
        "results": results,
    }


def unit_test_migration_idempotency(db_path: str, migration_sql_path: str) -> Dict[str, Any]:
    """Test that apply_kind_taxonomy_migration is idempotent."""
    results = []

    # First application
    r1 = apply_kind_taxonomy_migration(db_path, migration_sql_path)
    results.append({"run": 1, "status": r1["status"], "applied": r1.get("columns_applied", [])})

    # Second application (should skip all columns)
    r2 = apply_kind_taxonomy_migration(db_path, migration_sql_path)
    results.append({"run": 2, "status": r2["status"], "skipped": r2.get("columns_skipped", [])})

    # Verify: second run should have skipped all 4 columns
    all_skipped = set(r2.get("columns_skipped", [])) == {"kind", "derived_from", "consolidated_at", "confidence"}

    return {
        "status": "ok" if all_skipped and r2["status"] == "ok" else "failed",
        "idempotent": all_skipped,
        "results": results,
    }


def unit_test_fts5_trigger_safety(db_path: str) -> Dict[str, Any]:
    """Verify that kind backfill UPDATE does not corrupt FTS5 data.

    The existing AFTER UPDATE trigger fires on ANY column change,
    including `kind`. This test verifies data integrity is preserved:
    the trigger deletes+re-inserts identical summary/content rows.
    """
    conn = sqlite3.connect(db_path)

    # Count FTS5 rows before
    try:
        fts_before = conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
    except sqlite3.OperationalError:
        conn.close()
        return {"status": "skipped", "reason": "FTS5 table not found"}

    mem_count = conn.execute("SELECT COUNT(*) FROM memories WHERE is_archived = 0").fetchone()[0]

    # Do a single kind UPDATE on one memory
    test_row = conn.execute(
        "SELECT id, kind FROM memories WHERE is_archived = 0 LIMIT 1"
    ).fetchone()
    if not test_row:
        conn.close()
        return {"status": "skipped", "reason": "No memories found"}

    mem_id, old_kind = test_row
    new_kind = "semantic" if old_kind != "semantic" else "procedural"
    conn.execute("UPDATE memories SET kind = ? WHERE id = ?", (new_kind, mem_id))

    # Count FTS5 rows after — should be identical
    fts_after = conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]

    # Restore original kind
    conn.execute("UPDATE memories SET kind = ? WHERE id = ?", (old_kind, mem_id))
    conn.commit()
    conn.close()

    return {
        "status": "ok" if fts_before == fts_after else "failed",
        "fts_before": fts_before,
        "fts_after": fts_after,
        "memory_count": mem_count,
        "test_memory_id": mem_id,
    }


# ============================================================
# CLI entry point
# ============================================================

def _cli():
    parser = argparse.ArgumentParser(
        description="Kind Taxonomy — Wizard Library L2 Component 2"
    )
    parser.add_argument(
        "--unit-tests", action="store_true",
        help="Run unit tests for classify_memory"
    )

    subparsers = parser.add_subparsers(dest="command")

    # apply-migration
    p_migrate = subparsers.add_parser("apply-migration", help="Apply kind taxonomy migration")
    p_migrate.add_argument("db_path", help="Path to SQLite database")
    p_migrate.add_argument("migration_sql", help="Path to migration SQL file")

    # backfill
    p_backfill = subparsers.add_parser("backfill", help="Backfill kind classifications")
    p_backfill.add_argument("db_path", help="Path to SQLite database")
    p_backfill.add_argument("--dry-run", action="store_true", help="Compute without modifying DB")
    p_backfill.add_argument("--audit-output", default=None, help="Path to write audit JSON")

    # test-triggers
    p_triggers = subparsers.add_parser("test-triggers", help="Test FTS5 trigger safety")
    p_triggers.add_argument("db_path", help="Path to SQLite database")

    args = parser.parse_args()

    if args.unit_tests:
        result = unit_test_classify_memory()
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["status"] == "ok" else 1)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "apply-migration":
        result = apply_kind_taxonomy_migration(args.db_path, args.migration_sql)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["status"] == "ok" else 1)

    elif args.command == "backfill":
        result = backfill_kinds(args.db_path, dry_run=args.dry_run)
        # Print summary (not full audit) to stdout
        summary = {k: v for k, v in result.items() if k != "audit"}
        print(json.dumps(summary, indent=2))
        # Write full audit if requested
        if args.audit_output and result.get("audit"):
            with open(args.audit_output, "w") as f:
                json.dump(result["audit"], f, indent=2)
            print(f"\nFull audit written to: {args.audit_output}", file=sys.stderr)
        sys.exit(0 if result["status"] == "ok" else 1)

    elif args.command == "test-triggers":
        result = unit_test_fts5_trigger_safety(args.db_path)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    _cli()
