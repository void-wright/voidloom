#!/usr/bin/env python3
"""Persona-Specific Recall Lenses — Wizard Library L3 Component 5.

Stand-alone module that provides per-persona reranking of recall results.
Each lens (a role: knowledge ingester, researcher, site maintainer, resource-hub
maintainer, curator -- or any profile an owner adds) is a custom recall lens: weighted
preferences for which memory tags, categories, kinds, and projects are most relevant
to that role's work.

Architecture: ``apply_lens(results, profile)`` takes ALREADY-RETRIEVED results
from the hybrid pipeline and RE-SCORES them based on the persona's weights.
This is a POST-RETRIEVAL reranking layer — it does NOT replace hybrid retrieval.

Weight semantics:
    1.0 = neutral (no effect)
    >1.0 = boost (e.g. 2.0 doubles the effective score contribution)
    <1.0 = suppress (e.g. 0.5 halves it)
    0.0 = exclude (functionally equivalent to adding to exclude_tags/categories)

Public API
----------
``apply_lens(results, profile, top_k=None) -> List[dict]``
    Reranks and optionally filters results through a persona lens.

``create_profile(conn, slug, display_name, ...) -> int``
    Creates a new persona recall profile. Returns profile ID.

``update_profile(conn, slug, **fields) -> bool``
    Updates an existing profile. Returns True on success.

``get_profile(conn, slug) -> Optional[dict]``
    Retrieves a profile by slug. Returns None if not found.

``list_profiles(conn, active_only=True) -> List[dict]``
    Lists all profiles, optionally filtered to active-only.

``delete_profile(conn, slug, hard=False) -> bool``
    Soft-deactivates or hard-deletes a profile.

``seed_default_profiles(conn) -> dict``
    Creates starter profiles for known personas. Idempotent.

``is_persona_migration_applied(conn) -> bool``
    Check if migration 006 has been applied.

``apply_persona_migration(db_path, migration_sql_path) -> dict``
    Idempotent migration runner. Mirrors C1-C5 patterns.

``unit_test_persona_lenses()``
    Self-contained unit tests (25 tests).

Phase 0 design: Wizard Library Level 3 persona-specific recall lenses.
References: memories #3914 (canonical design), #3930 (L2-C1 hybrid retrieval).
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

PERSONA_MIGRATION_SQL = Path(__file__).parent.parent / "migrations" / "migration_006_persona_lenses.sql"

# Recency boost decay constant (half-life in days).
# If boost_recent_days=14, memories from today get full boost,
# memories from 14 days ago get half boost, older get diminishing returns.
_RECENCY_HALF_LIFE_DIVISOR = 0.693  # ln(2)

# Score components for lens reranking.
# The lens score is a multiplicative overlay on the existing retrieval score.
# This means highly-relevant memories stay high even without lens boost,
# while lens-preferred memories get a meaningful lift.
_BASE_SCORE_WEIGHT = 0.7  # how much the original retrieval score matters
_LENS_SCORE_WEIGHT = 0.3  # how much the lens multiplier matters


# ============================================================
# Migration helpers
# ============================================================

def is_persona_migration_applied(conn: sqlite3.Connection) -> bool:
    """Check if migration 006 (persona_recall_profiles) has been applied."""
    try:
        conn.execute("SELECT 1 FROM persona_recall_profiles LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def apply_persona_migration(db_path: str, migration_sql_path: str) -> dict:
    """Idempotent migration runner. Mirrors C1-C5 patterns."""
    t0 = time.time()

    migration_path = Path(migration_sql_path)
    if not migration_path.exists():
        return {"status": "error", "message": f"Migration file not found: {migration_sql_path}"}

    conn = sqlite3.connect(db_path, timeout=60.0)
    try:
        migration_sql = migration_path.read_text()
        conn.executescript(migration_sql)
        conn.commit()

        # Verify.
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]

        return {
            "status": "ok",
            "tables_after": tables,
            "has_persona_recall_profiles": "persona_recall_profiles" in tables,
            "time_ms": int((time.time() - t0) * 1000),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


# ============================================================
# Core lens logic
# ============================================================

def _compute_lens_multiplier(result: dict, profile: dict) -> float:
    """Compute a multiplicative lens score for a single result.

    The multiplier aggregates tag, category, kind, and project weight
    preferences. A multiplier of 1.0 means the persona has no preference;
    >1.0 means boost; <1.0 means suppress.

    For tags, we use the MAX tag weight among the result's tags (not sum),
    because a memory tagged with one highly-relevant tag is highly relevant
    regardless of its other tags.
    """
    tag_weights = profile.get("tag_weights", {})
    category_weights = profile.get("category_weights", {})
    kind_weights = profile.get("kind_weights", {})
    project_weights = profile.get("project_weights", {})

    # --- Tag contribution (max of matching tag weights, default 1.0) ---
    result_tags = result.get("tags", [])
    if result_tags and tag_weights:
        matching_weights = [tag_weights[t] for t in result_tags if t in tag_weights]
        tag_mult = max(matching_weights) if matching_weights else 1.0
    else:
        tag_mult = 1.0

    # --- Category contribution ---
    result_cat = result.get("category", "")
    cat_mult = category_weights.get(result_cat, 1.0) if category_weights else 1.0

    # --- Kind contribution ---
    result_kind = result.get("kind", "episodic")
    kind_mult = kind_weights.get(result_kind, 1.0) if kind_weights else 1.0

    # --- Project contribution ---
    result_project = result.get("source_project", "") or ""
    proj_mult = 1.0
    if project_weights and result_project:
        # Exact match first, then substring match.
        if result_project in project_weights:
            proj_mult = project_weights[result_project]
        else:
            for proj_key, weight in project_weights.items():
                if proj_key in result_project or result_project in proj_key:
                    proj_mult = weight
                    break

    # Combine multiplicatively. A memory that matches multiple persona
    # preferences gets compounding boosts.
    combined = tag_mult * cat_mult * kind_mult * proj_mult
    return combined


def _compute_recency_boost(result: dict, boost_recent_days: Optional[int]) -> float:
    """Compute a recency boost multiplier if boost_recent_days is set.

    Returns a value between 0.5 and 1.5:
      - Memory from today: 1.5
      - Memory from boost_recent_days ago: ~1.0
      - Memory from 2x boost_recent_days ago: ~0.75
      - Very old memory: approaches 0.5
    """
    if not boost_recent_days or boost_recent_days <= 0:
        return 1.0

    created_at = result.get("created_at")
    if not created_at:
        return 1.0

    try:
        created = datetime.fromisoformat(created_at)
        now = datetime.now()
        age_days = max(0, (now - created).days)

        # Exponential decay from 1.5 to 0.5, with half-life = boost_recent_days.
        decay = math.exp(-_RECENCY_HALF_LIFE_DIVISOR * age_days / boost_recent_days)
        return 0.5 + decay  # ranges from 0.5 (very old) to 1.5 (brand new)
    except (ValueError, TypeError):
        return 1.0


def _should_exclude(result: dict, profile: dict) -> bool:
    """Check if a result should be excluded based on persona's exclude_tags
    or any weight that is explicitly 0.0."""
    exclude_tags = set(profile.get("exclude_tags", []))
    result_tags = set(result.get("tags", []))

    # Explicit exclude list.
    if exclude_tags and result_tags.intersection(exclude_tags):
        return True

    # Check for 0.0 weights (also means exclude).
    tag_weights = profile.get("tag_weights", {})
    for t in result_tags:
        if t in tag_weights and tag_weights[t] == 0.0:
            return True

    category_weights = profile.get("category_weights", {})
    result_cat = result.get("category", "")
    if result_cat in category_weights and category_weights[result_cat] == 0.0:
        return True

    kind_weights = profile.get("kind_weights", {})
    result_kind = result.get("kind", "episodic")
    if result_kind in kind_weights and kind_weights[result_kind] == 0.0:
        return True

    project_weights = profile.get("project_weights", {})
    result_project = result.get("source_project", "") or ""
    if result_project in project_weights and project_weights[result_project] == 0.0:
        return True

    return False


def apply_lens(
    results: List[dict],
    profile: dict,
    top_k: Optional[int] = None,
) -> List[dict]:
    """Rerank recall results through a persona lens.

    Takes already-retrieved results and applies persona-specific weighting.
    Does NOT modify the input list; returns a new sorted list.

    Parameters
    ----------
    results : List[dict]
        Recall results (from hybrid pipeline). Each dict has at minimum:
        id, content, summary, category, tags, source_project, distance/score.
    profile : dict
        Persona profile dict (from get_profile or seed data). Keys:
        tag_weights, category_weights, kind_weights, project_weights,
        exclude_tags, boost_recent_days.
    top_k : Optional[int]
        If set, truncate to this many results after reranking.

    Returns
    -------
    List[dict]
        Reranked results with added 'lens_score' and 'lens_multiplier' fields.
    """
    if not results or not profile:
        return results[:top_k] if top_k else list(results)

    boost_recent_days = profile.get("boost_recent_days")
    reranked = []

    for r in results:
        # Exclusion check first.
        if _should_exclude(r, profile):
            continue

        # Compute lens multiplier.
        lens_mult = _compute_lens_multiplier(r, profile)
        recency_mult = _compute_recency_boost(r, boost_recent_days)

        # Combine with original score.
        # The original 'score' field (from reranker) is the primary signal.
        # Lens multiplier is a secondary signal that adjusts ordering.
        original_score = r.get("score", 0.0)
        # For results with distance-based scoring (lower=better), convert.
        if original_score == 0.0 and "distance" in r:
            original_score = max(0.0, 1.0 - r.get("distance", 0.5))

        # Final lens-adjusted score.
        lens_contribution = lens_mult * recency_mult
        final_score = (
            _BASE_SCORE_WEIGHT * original_score
            + _LENS_SCORE_WEIGHT * original_score * lens_contribution
        )

        # Annotate the result (non-destructive copy).
        entry = dict(r)
        entry["lens_score"] = round(final_score, 6)
        entry["lens_multiplier"] = round(lens_contribution, 4)
        reranked.append(entry)

    # Sort by lens_score descending (higher = more relevant to this persona).
    reranked.sort(key=lambda x: x["lens_score"], reverse=True)

    if top_k:
        reranked = reranked[:top_k]

    return reranked


# ============================================================
# CRUD operations
# ============================================================

def create_profile(
    conn: sqlite3.Connection,
    slug: str,
    display_name: str,
    description: Optional[str] = None,
    tag_weights: Optional[Dict[str, float]] = None,
    category_weights: Optional[Dict[str, float]] = None,
    kind_weights: Optional[Dict[str, float]] = None,
    project_weights: Optional[Dict[str, float]] = None,
    exclude_tags: Optional[List[str]] = None,
    boost_recent_days: Optional[int] = None,
) -> int:
    """Create a new persona recall profile. Returns profile ID.

    Raises sqlite3.IntegrityError if slug already exists.
    """
    cursor = conn.execute(
        """INSERT INTO persona_recall_profiles
           (persona_slug, display_name, description,
            tag_weights, category_weights, kind_weights, project_weights,
            exclude_tags, boost_recent_days)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            slug,
            display_name,
            description,
            json.dumps(tag_weights or {}),
            json.dumps(category_weights or {}),
            json.dumps(kind_weights or {}),
            json.dumps(project_weights or {}),
            json.dumps(exclude_tags or []),
            boost_recent_days,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def update_profile(conn: sqlite3.Connection, slug: str, **fields) -> bool:
    """Update an existing profile.

    Accepted keyword arguments: display_name, description, tag_weights,
    category_weights, kind_weights, project_weights, exclude_tags,
    boost_recent_days, is_active.

    JSON fields (tag_weights, category_weights, etc.) accept either a dict/list
    or a pre-serialized JSON string.

    Returns True on success, False if profile not found.
    """
    existing = conn.execute(
        "SELECT id FROM persona_recall_profiles WHERE persona_slug = ?",
        (slug,),
    ).fetchone()
    if not existing:
        return False

    allowed_fields = {
        "display_name", "description", "tag_weights", "category_weights",
        "kind_weights", "project_weights", "exclude_tags",
        "boost_recent_days", "is_active",
    }
    json_fields = {"tag_weights", "category_weights", "kind_weights",
                   "project_weights", "exclude_tags"}

    updates = []
    params = []
    for key, value in fields.items():
        if key not in allowed_fields:
            continue
        if key in json_fields and not isinstance(value, str):
            value = json.dumps(value)
        updates.append(f"{key} = ?")
        params.append(value)

    if not updates:
        return False

    updates.append("updated_at = datetime('now')")
    params.append(slug)

    conn.execute(
        f"UPDATE persona_recall_profiles SET {', '.join(updates)} WHERE persona_slug = ?",
        params,
    )
    conn.commit()
    return True


# ============================================================
# Generic lens names, and the names they replaced (2026-10-14)
# ============================================================
# The starter lenses are named for their ROLE (ruling vl_generic_persona_names_ruling). Before 2026-10-14 they
# carried one owner's persona names, and brains seeded then STORE those slugs. Both spellings resolve, in both
# directions, so no stored row is ever renamed or migrated: get_profile() falls back to the other spelling,
# seed_default_profiles() treats a stored legacy row as the default already present, and a weights file keyed by a
# legacy slug still applies. This map is the only place the legacy names are needed.
LEGACY_LENS_SLUGS = {
    "kael": "ingester",
    "lyra": "researcher",
    "cassian": "site-maintainer",
    "wren": "hub-maintainer",
    "milamber": "curator",
}
_LEGACY_OF = {v: k for k, v in LEGACY_LENS_SLUGS.items()}
DEFAULT_LENS = "curator"     # recall's lens when neither --persona nor WIZARD_PERSONA names one


def lens_slug_aliases(slug: str) -> List[str]:
    """`slug` first, then its other spelling (generic <-> legacy), if any."""
    other = LEGACY_LENS_SLUGS.get(slug) or _LEGACY_OF.get(slug)
    return [slug] + ([other] if other else [])


def get_profile(conn: sqlite3.Connection, slug: str) -> Optional[dict]:
    """Retrieve a profile by slug, or by its other spelling (generic <-> legacy) when only that one is stored. An exact
    match always wins, so a brain holding both reads the one it was asked for. Returns None if neither is stored."""
    row = None
    for candidate in lens_slug_aliases(slug):
        row = conn.execute(
            """SELECT id, persona_slug, display_name, description,
                      tag_weights, category_weights, kind_weights, project_weights,
                      exclude_tags, boost_recent_days, created_at, updated_at, is_active
               FROM persona_recall_profiles
               WHERE persona_slug = ?""",
            (candidate,),
        ).fetchone()
        if row:
            break

    if not row:
        return None

    return {
        "id": row[0],
        "persona_slug": row[1],
        "display_name": row[2],
        "description": row[3],
        "tag_weights": json.loads(row[4]) if row[4] else {},
        "category_weights": json.loads(row[5]) if row[5] else {},
        "kind_weights": json.loads(row[6]) if row[6] else {},
        "project_weights": json.loads(row[7]) if row[7] else {},
        "exclude_tags": json.loads(row[8]) if row[8] else [],
        "boost_recent_days": row[9],
        "created_at": row[10],
        "updated_at": row[11],
        "is_active": bool(row[12]),
    }


def list_profiles(conn: sqlite3.Connection, active_only: bool = True) -> List[dict]:
    """List all profiles, optionally filtered to active-only."""
    sql = """SELECT id, persona_slug, display_name, description,
                    tag_weights, category_weights, kind_weights, project_weights,
                    exclude_tags, boost_recent_days, created_at, updated_at, is_active
             FROM persona_recall_profiles"""
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY persona_slug"

    rows = conn.execute(sql).fetchall()
    profiles = []
    for row in rows:
        profiles.append({
            "id": row[0],
            "persona_slug": row[1],
            "display_name": row[2],
            "description": row[3],
            "tag_weights": json.loads(row[4]) if row[4] else {},
            "category_weights": json.loads(row[5]) if row[5] else {},
            "kind_weights": json.loads(row[6]) if row[6] else {},
            "project_weights": json.loads(row[7]) if row[7] else {},
            "exclude_tags": json.loads(row[8]) if row[8] else [],
            "boost_recent_days": row[9],
            "created_at": row[10],
            "updated_at": row[11],
            "is_active": bool(row[12]),
        })
    return profiles


def delete_profile(conn: sqlite3.Connection, slug: str, hard: bool = False) -> bool:
    """Soft-deactivate or hard-delete a profile.

    Returns True on success, False if profile not found.
    """
    existing = conn.execute(
        "SELECT id FROM persona_recall_profiles WHERE persona_slug = ?",
        (slug,),
    ).fetchone()
    if not existing:
        return False

    if hard:
        conn.execute(
            "DELETE FROM persona_recall_profiles WHERE persona_slug = ?",
            (slug,),
        )
    else:
        conn.execute(
            "UPDATE persona_recall_profiles SET is_active = 0, updated_at = datetime('now') WHERE persona_slug = ?",
            (slug,),
        )
    conn.commit()
    return True


# ============================================================
# Default persona seed profiles
# ============================================================

_DEFAULT_PROFILES = [
    {
        "slug": "ingester",
        "display_name": "Knowledge ingester",
        "description": "Knowledge-ingestion lens. Prefers research, publications, domain knowledge, and ingestion-related memories.",
        "tag_weights": {
            "ingestion": 2.5,
            "research": 2.0,
            "publications": 1.8,
            "knowledge-distillation": 1.5,
            "sources": 1.3,
        },
        "category_weights": {
            "domain_knowledge": 2.0,
            "skill_lesson": 1.5,
            "project_knowledge": 1.2,
            "campaign_state": 0.5,
            "people_context": 0.6,
        },
        "kind_weights": {
            "semantic": 1.8,
            "procedural": 1.5,
            "episodic": 0.8,
            "reflection": 1.2,
        },
        "project_weights": {},
        "exclude_tags": ["social-media"],
        "boost_recent_days": 30,
    },
    {
        "slug": "researcher",
        "display_name": "Researcher",
        "description": "Deep-research lens. Prefers research methodology, investigation patterns, and the tools that run them.",
        "tag_weights": {
            "deep-research": 2.5,
            "research": 2.0,
            "investigation": 1.8,
            "knowledge-distillation": 1.5,
            "sources": 1.3,
        },
        "category_weights": {
            "domain_knowledge": 2.0,
            "workflow_pattern": 1.8,
            "tool_optimization": 1.5,
            "skill_lesson": 1.3,
            "campaign_state": 0.4,
            "people_context": 0.5,
        },
        "kind_weights": {
            "procedural": 2.0,
            "semantic": 1.5,
            "episodic": 0.7,
            "reflection": 1.3,
        },
        "project_weights": {},
        "exclude_tags": ["social-media"],
        "boost_recent_days": 21,
    },
    {
        "slug": "site-maintainer",
        "display_name": "Site maintainer",
        "description": "Website-maintenance lens. Prefers deployment, hosting, DNS, site care, and infrastructure memories.",
        "tag_weights": {
            "deployment": 2.5,
            "infrastructure": 2.0,
            "dns": 1.8,
            "security": 1.5,
            "architecture": 1.5,
            "ci": 1.3,
        },
        "category_weights": {
            "technical_decision": 2.0,
            "workflow_pattern": 1.5,
            "error_pattern": 1.5,
            "tool_optimization": 1.3,
            "domain_knowledge": 0.7,
            "campaign_state": 0.4,
        },
        "kind_weights": {
            "procedural": 1.8,
            "semantic": 1.3,
            "episodic": 1.0,
            "reflection": 1.0,
        },
        "project_weights": {},
        "exclude_tags": ["campaign_state"],
        "boost_recent_days": 14,
    },
    {
        "slug": "hub-maintainer",
        "display_name": "Content-hub maintainer",
        "description": "Content-hub maintenance lens. Prefers CMS, content publishing, and documentation memories.",
        "tag_weights": {
            "cms": 2.5,
            "content": 1.8,
            "publishing": 1.5,
            "docs": 1.3,
        },
        "category_weights": {
            "workflow_pattern": 2.0,
            "project_knowledge": 1.5,
            "skill_lesson": 1.3,
            "tool_optimization": 1.3,
            "domain_knowledge": 0.8,
            "error_pattern": 1.2,
        },
        "kind_weights": {
            "procedural": 1.8,
            "semantic": 1.2,
            "episodic": 1.0,
            "reflection": 1.0,
        },
        "project_weights": {},
        "exclude_tags": ["deep-research"],
        "boost_recent_days": 14,
    },
    {
        "slug": "curator",
        "display_name": "Curator",
        "description": "Memory-curation lens. Prefers review loops, skillbooks, memory architecture, and meta-cognition memories.",
        "tag_weights": {
            "review-loop": 2.5,
            "skillbook": 2.5,
            "protected": 2.0,
            "active": 1.8,
            "self-heal": 1.5,
            "memory-architecture": 1.5,
            "architecture": 1.5,
        },
        "category_weights": {
            "skill_lesson": 2.0,
            "workflow_pattern": 1.8,
            "technical_decision": 1.5,
            "insight": 1.8,
            "domain_knowledge": 1.0,
            "campaign_state": 0.3,
            "people_context": 0.5,
        },
        "kind_weights": {
            "procedural": 2.0,
            "semantic": 1.8,
            "reflection": 2.0,
            "episodic": 0.6,
        },
        "project_weights": {},
        "exclude_tags": ["social-media", "campaign_state"],
        "boost_recent_days": 7,
    },
]


# ============================================================
# The owner's weights -- OUT of the code, in an optional per-brain file
# ============================================================
# The shipped defaults are NEUTRAL: they name no owner's projects or clients. An owner who wants a persona to favour
# his own projects (or tags) declares it in <brain>/persona_lens_weights.json -- beside the brain's DB -- and seeding
# overlays it. Absent file: the neutral defaults, unchanged. Shape:
#   {"<persona slug>": {"project_weights": {"<project>": 1.5}, "tag_weights": {...},
#                       "category_weights": {...}, "kind_weights": {...},
#                       "exclude_tags": ["..."], "boost_recent_days": 14}, ...}
# Weight maps MERGE key by key over the defaults (the file wins); exclude_tags is a union; boost_recent_days replaces.
# A slug the defaults do not know is reported and skipped (this file tunes the starter profiles, it does not invent them).
PERSONA_LENS_WEIGHTS_BASENAME = "persona_lens_weights.json"
_WEIGHT_MAPS = ("tag_weights", "category_weights", "kind_weights", "project_weights")


def owner_weights_path(conn: sqlite3.Connection) -> Optional[Path]:
    """<brain>/persona_lens_weights.json for the brain `conn` is attached to; None for an in-memory database."""
    try:
        for _seq, name, path in conn.execute("PRAGMA database_list").fetchall():
            if name == "main" and path:
                return Path(path).resolve().parent / PERSONA_LENS_WEIGHTS_BASENAME
    except sqlite3.Error:
        pass
    return None


def load_owner_weights(path: Optional[Path]) -> Tuple[dict, Optional[str]]:
    """(weights, error). Absent file -> ({}, None). An unreadable or malformed file -> ({}, reason): the neutral
    defaults are seeded and the reason is reported, never a guess."""
    if path is None or not Path(path).is_file():
        return {}, None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {}, "%s: %s" % (type(e).__name__, e)
    if not isinstance(data, dict):
        return {}, "the file is not a JSON object"
    return {k: v for k, v in data.items() if isinstance(v, dict) and not k.startswith("_")}, None


def _is_weight(v) -> bool:
    """A weight is a finite real number -- never a bool (True is an int to Python), never a string."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _overlay(profile: dict, owner: dict, slug: str = "", rejected: Optional[List[str]] = None) -> dict:
    """The profile with the owner's values applied. EVERY value is validated: a bad one is skipped and named in
    `rejected` ('<slug>.<field>[.<key>]: <why>'), and the rest of the file still applies -- a typo never aborts seeding.
      weight maps         a JSON object of finite numbers (bool rejected)
      exclude_tags        a list of strings (a non-string element is skipped on its own)
      boost_recent_days   an int >= 0 (bool rejected)"""
    rej = rejected if rejected is not None else []
    out = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in profile.items()}
    for field in _WEIGHT_MAPS:
        if field not in owner:
            continue
        if not isinstance(owner[field], dict):
            rej.append("%s.%s: not a JSON object of weights" % (slug, field))
            continue
        merged = dict(out.get(field) or {})
        for k, v in owner[field].items():
            if _is_weight(v):
                merged[str(k)] = float(v)
            else:
                rej.append("%s.%s.%s: %r is not a finite number" % (slug, field, k, v))
        out[field] = merged
    if "exclude_tags" in owner:
        tags = owner["exclude_tags"]
        if not isinstance(tags, list):
            rej.append("%s.exclude_tags: not a list of strings" % slug)
        else:
            good = []
            for n, t in enumerate(tags):
                if isinstance(t, str):
                    good.append(t)
                else:
                    rej.append("%s.exclude_tags[%d]: %r is not a string" % (slug, n, t))
            out["exclude_tags"] = list(dict.fromkeys(list(out.get("exclude_tags") or []) + good))
    if "boost_recent_days" in owner:
        b = owner["boost_recent_days"]
        if isinstance(b, int) and not isinstance(b, bool) and b >= 0:
            out["boost_recent_days"] = b
        else:
            rej.append("%s.boost_recent_days: %r is not an integer >= 0" % (slug, b))
    return out


def seed_default_profiles(conn: sqlite3.Connection, weights_path: Optional[Path] = None) -> dict:
    """Create starter profiles for known personas. Idempotent.

    The neutral defaults, overlaid with the owner's optional <brain>/persona_lens_weights.json (or `weights_path`).
    Returns dict with counts of created/skipped profiles and what the owner's file contributed.
    """
    created = 0
    skipped = 0
    wpath = Path(weights_path) if weights_path is not None else owner_weights_path(conn)
    owner, owner_err = load_owner_weights(wpath)
    # a weights file written before the generic names keys its lenses by the legacy slugs: they still apply
    for legacy, generic in LEGACY_LENS_SLUGS.items():
        if legacy in owner and generic not in owner:
            owner[generic] = owner.pop(legacy)
    known = {p["slug"] for p in _DEFAULT_PROFILES}

    rejected: List[str] = []
    for profile_data in _DEFAULT_PROFILES:
        if profile_data["slug"] in owner:
            profile_data = _overlay(profile_data, owner[profile_data["slug"]], profile_data["slug"], rejected)
        existing = None
        for candidate in lens_slug_aliases(profile_data["slug"]):     # a brain seeded under the legacy name has it
            existing = conn.execute(
                "SELECT id FROM persona_recall_profiles WHERE persona_slug = ?",
                (candidate,),
            ).fetchone()
            if existing:
                break

        if existing:
            skipped += 1
            continue

        create_profile(
            conn,
            slug=profile_data["slug"],
            display_name=profile_data["display_name"],
            description=profile_data["description"],
            tag_weights=profile_data.get("tag_weights"),
            category_weights=profile_data.get("category_weights"),
            kind_weights=profile_data.get("kind_weights"),
            project_weights=profile_data.get("project_weights"),
            exclude_tags=profile_data.get("exclude_tags"),
            boost_recent_days=profile_data.get("boost_recent_days"),
        )
        created += 1

    return {
        "status": "ok",
        "profiles_created": created,
        "profiles_skipped": skipped,
        "total_default_profiles": len(_DEFAULT_PROFILES),
        "owner_weights_file": (str(wpath) if wpath is not None and Path(wpath).is_file() else None),
        "owner_weights_applied": sorted(set(owner) & known),
        "owner_weights_unknown_slugs": sorted(set(owner) - known),
        "owner_weights_error": owner_err or (
            "%d value(s) rejected and skipped; the rest of the file applied: %s" % (len(rejected), "; ".join(rejected))
            if rejected else None),
        "owner_weights_rejected": rejected,
    }


# ============================================================
# Unit tests
# ============================================================

def unit_test_persona_lenses():
    """Self-contained unit tests for persona_lenses module.

    Creates an in-memory SQLite database, exercises all CRUD operations
    and the apply_lens reranking logic.
    """
    import tempfile
    import os

    print("=" * 60)
    print("PERSONA LENSES — Unit Tests")
    print("=" * 60)

    passed = 0
    failed = 0

    def _assert(condition, msg):
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  PASS: {msg}")
        else:
            failed += 1
            print(f"  FAIL: {msg}")

    # Setup: create temp DB with migration applied.
    tmp_db = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(tmp_db)
    conn.execute("PRAGMA foreign_keys=ON")

    # Create minimal schema for testing.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
        INSERT OR REPLACE INTO metadata (key, value) VALUES ('schema_version', '2.4-L2-sleep-loop');
    """)

    # Apply migration.
    migration_sql = Path(__file__).parent.parent / "migrations" / "migration_006_persona_lenses.sql"
    if migration_sql.exists():
        conn.executescript(migration_sql.read_text())

    # --- Test 1: Migration applied ---
    print("\n[Test Group 1: Migration]")
    _assert(is_persona_migration_applied(conn), "migration_006 detected as applied")

    sv = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
    _assert(sv and sv[0] == "3.1-L3-persona-lenses", "schema version bumped to 3.1")

    # --- Test 2: Create profile ---
    print("\n[Test Group 2: Create Profile]")
    pid = create_profile(
        conn, slug="test-persona", display_name="Test Persona",
        description="A test persona",
        tag_weights={"ace-loop": 2.0, "research": 1.5},
        category_weights={"skill_lesson": 1.8},
        kind_weights={"procedural": 2.0},
        project_weights={"engineering-workstation": 1.5},
        exclude_tags=["social-media"],
        boost_recent_days=14,
    )
    _assert(pid > 0, f"create_profile returned id={pid}")

    # Duplicate slug should fail.
    try:
        create_profile(conn, slug="test-persona", display_name="Dup")
        _assert(False, "duplicate slug should raise IntegrityError")
    except sqlite3.IntegrityError:
        _assert(True, "duplicate slug raises IntegrityError")

    # --- Test 3: Get profile ---
    print("\n[Test Group 3: Get Profile]")
    p = get_profile(conn, "test-persona")
    _assert(p is not None, "get_profile returns non-None")
    _assert(p["persona_slug"] == "test-persona", "slug matches")
    _assert(p["tag_weights"]["ace-loop"] == 2.0, "tag_weights parsed correctly")
    _assert(p["exclude_tags"] == ["social-media"], "exclude_tags parsed correctly")
    _assert(p["boost_recent_days"] == 14, "boost_recent_days correct")

    missing = get_profile(conn, "nonexistent")
    _assert(missing is None, "get_profile for missing slug returns None")

    # --- Test 4: Update profile ---
    print("\n[Test Group 4: Update Profile]")
    ok = update_profile(conn, "test-persona", description="Updated desc",
                        tag_weights={"ace-loop": 3.0})
    _assert(ok is True, "update_profile returns True")

    p2 = get_profile(conn, "test-persona")
    _assert(p2["description"] == "Updated desc", "description updated")
    _assert(p2["tag_weights"]["ace-loop"] == 3.0, "tag_weights updated")

    not_ok = update_profile(conn, "nonexistent", description="X")
    _assert(not_ok is False, "update_profile for missing slug returns False")

    # --- Test 5: List profiles ---
    print("\n[Test Group 5: List Profiles]")
    create_profile(conn, slug="second-persona", display_name="Second")
    profiles = list_profiles(conn, active_only=True)
    _assert(len(profiles) >= 2, f"list_profiles returns >= 2 ({len(profiles)})")
    slugs = [p["persona_slug"] for p in profiles]
    _assert("test-persona" in slugs, "test-persona in list")
    _assert("second-persona" in slugs, "second-persona in list")

    # --- Test 6: Delete profile ---
    print("\n[Test Group 6: Delete Profile]")
    ok = delete_profile(conn, "second-persona", hard=False)
    _assert(ok is True, "soft-delete returns True")
    p_del = get_profile(conn, "second-persona")
    _assert(p_del is not None and p_del["is_active"] is False, "soft-deleted profile is_active=False")

    profiles_active = list_profiles(conn, active_only=True)
    _assert("second-persona" not in [p["persona_slug"] for p in profiles_active],
            "soft-deleted not in active list")

    ok = delete_profile(conn, "second-persona", hard=True)
    _assert(ok is True, "hard-delete returns True")
    _assert(get_profile(conn, "second-persona") is None, "hard-deleted profile is gone")

    # --- Test 7: Seed default profiles ---
    print("\n[Test Group 7: Seed Default Profiles]")
    result = seed_default_profiles(conn)
    _assert(result["profiles_created"] == len(_DEFAULT_PROFILES), f"created {result['profiles_created']} default profiles")
    _assert(result["profiles_skipped"] == 0, "none skipped on first run")

    # Idempotent re-run.
    result2 = seed_default_profiles(conn)
    _assert(result2["profiles_created"] == 0, "second seed creates 0")
    _assert(result2["profiles_skipped"] == len(_DEFAULT_PROFILES), "all skipped on re-run")

    # --- Test 8: apply_lens — basic reranking ---
    print("\n[Test Group 8: apply_lens Reranking]")
    mock_results = [
        {"id": 1, "content": "A", "summary": "A", "category": "skill_lesson",
         "tags": ["ace-loop", "protected"], "source_project": "engineering-workstation",
         "kind": "procedural", "score": 0.6, "distance": 0.4, "created_at": datetime.now().isoformat()},
        {"id": 2, "content": "B", "summary": "B", "category": "campaign_state",
         "tags": ["social-media"], "source_project": "narrative",
         "kind": "episodic", "score": 0.8, "distance": 0.2, "created_at": "2025-01-01T00:00:00"},
        {"id": 3, "content": "C", "summary": "C", "category": "domain_knowledge",
         "tags": ["research"], "source_project": "scientific-knowledge-ingestion",
         "kind": "semantic", "score": 0.7, "distance": 0.3, "created_at": datetime.now().isoformat()},
    ]

    curator_profile = get_profile(conn, "curator")
    reranked = apply_lens(mock_results, curator_profile)

    # The curator lens should boost ace-loop/procedural (id=1) and suppress campaign_state (id=2).
    _assert(len(reranked) <= 2, f"curator excludes social-media tagged (got {len(reranked)})")
    if len(reranked) >= 1:
        _assert(reranked[0]["id"] == 1, "curator ranks ace-loop+procedural first")

    # --- Test 9: apply_lens — exclusion ---
    print("\n[Test Group 9: apply_lens Exclusion]")
    ingester_profile = get_profile(conn, "ingester")
    reranked_ingester = apply_lens(mock_results, ingester_profile)
    ids_ingester = [r["id"] for r in reranked_ingester]
    _assert(2 not in ids_ingester, "ingester excludes social-media tagged result")

    # --- Test 10: apply_lens — empty/None handling ---
    print("\n[Test Group 10: Edge Cases]")
    _assert(apply_lens([], curator_profile) == [], "empty results returns empty")
    _assert(apply_lens(mock_results, {}) == mock_results, "empty profile returns unchanged")
    _assert(apply_lens(mock_results, None) == mock_results, "None profile returns unchanged")

    # --- Summary ---
    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 60)

    conn.close()
    os.unlink(tmp_db)

    return passed, failed


# ============================================================
# Standalone entry point
# ============================================================

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        passed, failed = unit_test_persona_lenses()
        sys.exit(0 if failed == 0 else 1)
    else:
        print("Usage: python3 persona_lenses.py test")
        print("  Runs self-contained unit tests.")
        sys.exit(0)
