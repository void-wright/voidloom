#!/usr/bin/env python3
"""Temporal Edges for Wizard Library L2 Component 4.

A stand-alone module that adds typed directed edges with bitemporal validity
to the Claude narrative memory system. Designed to be imported by the patched
``claude_memory.py``; can also be exercised stand-alone via CLI flags.

Public API
----------
``create_edge(conn, source_id, target_id, predicate, ...) -> int``
    Create a single edge. Handles contradicts symmetry, validates constraints.

``extract_edges_heuristic(conn, memory_id, top_k=10) -> List[dict]``
    Heuristic edge extraction for a single memory. Returns edge proposals.

``extract_edges_llm(conn, memory_id, model='haiku', ...) -> List[dict]``
    LLM-assisted edge extraction (stub -- Sleep Loop C3 will provide real calls).

``multi_hop_expand(conn, seed_ids, depth=2, ...) -> List[int]``
    Recursive CTE graph walk from seed memories. Returns expanded IDs.

``decay_confidence(conn, factor=0.99, min_threshold=0.2, max_age_days=30, dry_run=False) -> dict``
    Decay edge confidences by factor per UTC day elapsed since the last decay (metadata.edge_decay_last_run), so a
    second run on the same day changes nothing. Prune below threshold once older than max_age_days. A pruned edge
    is RETIRED, never deleted (see "Pruning retires" below).

``revive_decay_retired(conn, source_id, target_id, predicate, ...) -> Optional[int]``
    A writer re-asserting a (source, target, predicate) that a decay retired takes that row back as a fresh current
    edge -- what its INSERT did when the prune deleted the row and freed the UNIQUE triple.

Pruning retires (round 2, lane R2d)
-----------------------------------
A pruned edge keeps its row. The prune sets ``valid_until`` to the run's UTC time (``YYYY-MM-DD HH:MM:SS``, the
brain's ``datetime('now')`` format) and replaces ``metadata`` with a JSON record of why::

    {"retired_by": "decay-prune", "retired_at": <the same time>, "retired_confidence": <what the decay computed>,
     "confidence_before": <the stored confidence, which the prune leaves as it was>,
     "prior_metadata": <the edge's metadata text, verbatim, or null>}

Every reader of the current graph already asks for ``valid_until IS NULL``, so a retired edge is out of recall, PPR,
multi-hop, communities and the belief engine exactly as a deleted one was. The decay reads only current edges, so a
retired edge is never decayed, retired or counted again. To bring every decay-retired edge back to current (its
confidence set to where the decay clock had it, its metadata restored byte for byte), run this one statement::

    UPDATE memory_edges
       SET valid_until = NULL,
           confidence = json_extract(metadata, '$.retired_confidence'),
           metadata = json_extract(metadata, '$.prior_metadata')
     WHERE valid_until IS NOT NULL
       AND CASE WHEN json_valid(metadata) THEN json_extract(metadata, '$.retired_by') END = 'decay-prune'
       AND valid_until = CASE WHEN json_valid(metadata) THEN json_extract(metadata, '$.retired_at') END;

(``DECAY_RESTORE_SQL`` below is that text.) To restore one night only, add
``AND valid_until = '<that night's retired_at>'``. It touches no edge that something else ended (a belief
invalidation or quarantine sets ``valid_until`` without that record). A restored edge still below the threshold
and older than max_age_days is retired again by the next decay on a LATER UTC day, unless its confidence is raised.

``repair_decay(conn, apply=False, undo=False) -> dict``   (CLI: ``temporal_edges.py repair-decay <db> [--apply|--undo]``)
    The one-time repair of the confidences the pre-round-2 per-run compounding lowered: replays the old code over
    sleep_loop_runs from each creation confidence the edge's writer can produce, repairs only an edge exactly one
    start reproduces, to its per-UTC-day value at the decay anchor. Dry run by default; reversible; idempotent.

``get_edges(conn, memory_id, direction='both', ...) -> List[dict]``
    Query edges for a specific memory.

Phase 0 design: memories #3914, #3816 (bitemporal pattern), #3924 (dependency map).
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import struct
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# --- TO-02 repair: resolve the embedding tier instead of hardcoding it -------
# The write path moved to memory_embeddings_v2 on 2026-05-28; this reader was not
# moved with it. embed_resolver REFUSES rather than defaulting to the dormant
# table (architecture-009) and resolves from the CALLER'S connection.
from embed_resolver import resolve as _resolve_embed_tier  # noqa: E402
# ---------------------------------------------------------------------------


logger = logging.getLogger(__name__)

# ============================================================
# Constants
# ============================================================

VALID_PREDICATES = frozenset({
    'causes', 'contradicts', 'supersedes', 'refines',
    'generalizes', 'instantiates', 'references', 'depends_on',
})

VALID_EXTRACTION_METHODS = frozenset({
    'manual', 'heuristic', 'llm_haiku', 'llm_sonnet', 'sleep_loop',
})

# contradicts is the only SYMMETRIC predicate: if A contradicts B, B contradicts A.
SYMMETRIC_PREDICATES = frozenset({'contradicts'})

# CTE safety limits.
MAX_CTE_DEPTH = 3
MAX_EXPANDED_RESULTS = 50

# Heuristic extraction confidence levels.
_CONF_REFERENCES = 0.6
_CONF_REFINES = 0.7
_CONF_SUPERSEDES = 0.8
_CONF_CONTRADICTS = 0.7
_CONF_CAUSES = 0.7
_CONF_GENERALIZES = 0.6
_CONF_DEPENDS_ON = 0.7

# --- L5-D Belief-Engine DEMOTION (stop the bleeding) ------------------------
# The keyword-regex `contradicts`/`supersedes` heuristic is the SOURCE of the
# 416-poison: an ungated keyword match ("incorrect", "wrong", "conflicts",
# "replaces"...) between two memories that merely SHARE a memory-id reference is
# not evidence of a genuine semantic disagreement. NLI gated behind an
# embedding-relatedness threshold (belief_engine.py) is now the SOLE author of
# those two predicates.
#
# When _BELIEF_ENGINE_DEMOTE_STRONG is True (the default after L5-D), the
# heuristic NO LONGER emits `contradicts` or `supersedes`. Instead each such
# match is downgraded to a plain `references` edge (it preserves the structural
# "these two memories are related / one cites the other" link that the memory-id
# reference already implies, WITHOUT asserting the unproven strong relation).
# The refines / causes / depends_on / references heuristics are UNTOUCHED.
#
# Set to False ONLY to restore the legacy (poison-prone) behaviour for an audit.
_BELIEF_ENGINE_DEMOTE_STRONG = True

# The predicate + confidence a demoted strong-match falls back to.
_DEMOTED_PREDICATE = 'references'
_DEMOTED_CONF = _CONF_REFERENCES

# Nomic embedding dimension (for cosine similarity computation).
_NOMIC_DIM = 768

# --- P1 (iter-2): Negative-context filter for meta-discussion content ---
# When a predicate keyword (e.g. "contradicts") appears near other predicate
# keywords or schema-documentation words, the text is DISCUSSING predicates
# rather than ASSERTING a relationship. Suppress the heuristic in that case.
_META_DISCUSSION_KEYWORDS = frozenset({
    'supersedes', 'refines', 'references', 'causes', 'generalizes',
    'instantiates', 'depends_on', 'predicate', 'predicates',
    'edge', 'edges', 'schema', 'table', 'select', 'insert', 'check',
    'constraint', 'migration', 'bitemporal', 'valid_from', 'valid_until',
})

# --- P2 (iter-2): Ratio guard thresholds for index/catalog detection ---
_RATIO_GUARD_MIN_TRIGGERS = 2   # Trigger-word count must be >= this
_RATIO_GUARD_MAX_REFS = 5       # Ref count above this signals index memory
_INDEX_SUMMARY_KEYWORDS = frozenset({
    'index', 'cluster', 'master', 'registry', 'catalog', 'catalogue',
    'table of contents', 'inventory', 'manifest',
})


# ============================================================
# Edge creation
# ============================================================

def create_edge(
    conn: sqlite3.Connection,
    source_id: int,
    target_id: int,
    predicate: str,
    confidence: float = 0.8,
    valid_from: Optional[str] = None,
    valid_until: Optional[str] = None,
    metadata: Optional[dict] = None,
    extraction_method: str = 'manual',
) -> int:
    """Create a single edge between two memories.

    Validates constraints (no self-edges, both IDs exist, predicate valid).
    Handles ``contradicts`` symmetry by auto-inserting the reverse edge.

    Args:
        conn: sqlite3 connection with memory_edges table.
        source_id: source memory ID.
        target_id: target memory ID.
        predicate: one of VALID_PREDICATES.
        confidence: 0.0-1.0 edge confidence.
        valid_from: ISO datetime when relationship begins (None = always).
        valid_until: ISO datetime when relationship ends (None = still valid).
        metadata: optional JSON-serialisable dict for predicate-specific context.
        extraction_method: one of VALID_EXTRACTION_METHODS.

    Returns:
        The edge ID of the primary edge created.

    Raises:
        ValueError: on invalid predicate, extraction_method, self-edge, or missing memory.
        sqlite3.IntegrityError: on duplicate (source, target, predicate). A row the decay RETIRED is not a duplicate:
            it is taken back as this edge (revive_decay_retired), as the INSERT did when the prune deleted it.
    """
    if source_id == target_id:
        raise ValueError(f"Self-edges forbidden: source_id == target_id == {source_id}")

    if predicate not in VALID_PREDICATES:
        raise ValueError(
            f"Invalid predicate {predicate!r}; valid: {sorted(VALID_PREDICATES)}"
        )

    if extraction_method not in VALID_EXTRACTION_METHODS:
        raise ValueError(
            f"Invalid extraction_method {extraction_method!r}; valid: {sorted(VALID_EXTRACTION_METHODS)}"
        )

    if confidence < 0.0 or confidence > 1.0:
        raise ValueError(f"confidence must be in [0.0, 1.0], got {confidence}")

    # Validate both memories exist.
    for mem_id in (source_id, target_id):
        row = conn.execute("SELECT id FROM memories WHERE id = ?", (mem_id,)).fetchone()
        if row is None:
            raise ValueError(f"Memory {mem_id} not found")

    meta_json = json.dumps(metadata) if metadata else None
    values = dict(confidence=confidence, metadata=meta_json, extraction_method=extraction_method,
                  valid_from=valid_from, valid_until=valid_until)

    # A triple the decay RETIRED is free, as it was when the prune deleted it (round 2, R2d): take the row back.
    edge_id = revive_decay_retired(conn, source_id, target_id, predicate, **values)
    if edge_id is None:
        cursor = conn.execute(
            """INSERT INTO memory_edges
               (source_id, target_id, predicate, confidence, valid_from, valid_until, metadata, extraction_method)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (source_id, target_id, predicate, confidence, valid_from, valid_until, meta_json, extraction_method),
        )
        edge_id = cursor.lastrowid

    # Symmetric predicates: auto-insert reverse edge.
    if predicate in SYMMETRIC_PREDICATES:
        if revive_decay_retired(conn, target_id, source_id, predicate, **values) is None:
            try:
                conn.execute(
                    """INSERT INTO memory_edges
                       (source_id, target_id, predicate, confidence, valid_from, valid_until, metadata, extraction_method)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (target_id, source_id, predicate, confidence, valid_from, valid_until, meta_json, extraction_method),
                )
            except sqlite3.IntegrityError:
                pass  # Reverse already exists (idempotent).

    return edge_id


# ============================================================
# Heuristic edge extraction
# ============================================================

def _cosine_similarity_from_bytes(vec_a: bytes, vec_b: bytes) -> float:
    """Compute cosine similarity between two serialised float32 vectors."""
    dim = len(vec_a) // 4
    a = struct.unpack(f"{dim}f", vec_a)
    b = struct.unpack(f"{dim}f", vec_b)
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _get_memory_tags(conn: sqlite3.Connection, memory_id: int) -> Set[str]:
    """Fetch tag set for a memory."""
    rows = conn.execute(
        "SELECT tag FROM memory_tags WHERE memory_id = ?", (memory_id,)
    ).fetchall()
    return {r[0] for r in rows}


def _get_memory_content(conn: sqlite3.Connection, memory_id: int) -> Optional[str]:
    """Fetch content for a memory."""
    row = conn.execute(
        "SELECT content FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    return row[0] if row else None


def _get_memory_embedding(conn: sqlite3.Connection, memory_id: int) -> Optional[bytes]:
    """Fetch raw embedding bytes for a memory. Returns None if vec0 not loaded."""
    try:
        _t, _d = _resolve_embed_tier(conn)
        row = conn.execute(
            f"SELECT embedding FROM {_t} WHERE memory_id = ?", (memory_id,)
        ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def _get_memory_project(conn: sqlite3.Connection, memory_id: int) -> Optional[str]:
    """Fetch source_project for a memory."""
    row = conn.execute(
        "SELECT source_project FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    return row[0] if row else None


# Regex patterns for heuristic content analysis.
_RE_SUPERSEDES = re.compile(
    r'\b(supersedes?|replaces?|updates?|deprecates?|obsoletes?|overwrites?)\b',
    re.IGNORECASE,
)
_RE_CONTRADICTS = re.compile(
    r'\b(contradicts?|disagrees?|conflicts?|incompatible|incorrect|wrong|inaccurate)\b',
    re.IGNORECASE,
)
_RE_CAUSES = re.compile(
    r'\b(because|caused?\s+by|led\s+to|triggered|resulted?\s+in|drove|prompted)\b',
    re.IGNORECASE,
)
_RE_DEPENDS_ON = re.compile(
    r'\b(depends?\s+on|requires?|prerequisite|blocked\s+(?:on|by)|needs?\s+(?:memory|#)|gated\s+by)\b',
    re.IGNORECASE,
)
_RE_MEMORY_REF = re.compile(r'(?:memory\s*)?#(\d+)\b')


def _is_meta_discussion(content: str, match_obj: re.Match, window: int = 120) -> bool:
    """P1 (iter-2): Check if a regex match occurs in meta-discussion context.

    A match is considered meta-discussion if the surrounding text (120-char window)
    contains 2+ other predicate keywords or schema-documentation words. This
    catches content that DESCRIBES predicates (e.g. "predicate types: causes,
    contradicts, supersedes...") rather than ASSERTING a relationship.
    """
    start = max(0, match_obj.start() - window)
    end = min(len(content), match_obj.end() + window)
    window_text = content[start:end].lower()
    # Count how many distinct meta-discussion keywords appear in the window.
    hits = sum(1 for kw in _META_DISCUSSION_KEYWORDS if kw in window_text)
    return hits >= 2


def _should_demote_to_references(
    content: str,
    summary: Optional[str],
    trigger_regex: re.Pattern,
    ref_ids: List[str],
) -> bool:
    """P2 (iter-2): Ratio guard for index/catalog memories.

    Returns True if the memory looks like an index/catalog that should have
    its strong-predicate edges demoted to 'references'.

    Criteria (any of):
      1. trigger-word count < _RATIO_GUARD_MIN_TRIGGERS AND ref count > _RATIO_GUARD_MAX_REFS
      2. summary contains index/catalog keywords
    """
    trigger_count = len(trigger_regex.findall(content))
    ref_count = len(set(ref_ids))
    if trigger_count < _RATIO_GUARD_MIN_TRIGGERS and ref_count > _RATIO_GUARD_MAX_REFS:
        return True
    if summary:
        summary_lower = summary.lower()
        if any(kw in summary_lower for kw in _INDEX_SUMMARY_KEYWORDS):
            return True
    return False


def extract_edges_heuristic(
    conn: sqlite3.Connection,
    memory_id: int,
    top_k: int = 10,
    tier3_cache: Optional["Tier3Cache"] = None,
) -> List[dict]:
    """Heuristic edge extraction for a single memory.

    Finds candidate edges via:
      - Tag co-occurrence (rare shared tags) -> references (confidence 0.6)
      - Same project + high cosine similarity (>0.85) -> refines (confidence 0.7)
      - Content patterns ("supersedes"/"replaces") -> supersedes (confidence 0.8)
      - Content patterns ("contradicts"/"conflicts") -> contradicts (confidence 0.7)
      - Content patterns ("because"/"caused by") + memory refs -> causes (confidence 0.7)
      - Content patterns ("depends on"/"requires") + memory refs -> depends_on (confidence 0.7)

    Iter-2 filters:
      - P1: Negative-context filter suppresses predicate matches in meta-discussion text
      - P2: Ratio guard demotes index/catalog memories from strong predicates to 'references'
      - P4: depends_on heuristic added for "depends on", "requires", etc.

    Args:
        conn: sqlite3 connection.
        memory_id: the memory to extract edges from.
        top_k: max edges to propose per heuristic tier.

    Returns:
        List of proposed edge dicts (NOT yet inserted). Each dict has:
        source_id, target_id, predicate, confidence, extraction_method, metadata.
    """
    proposals: List[dict] = []
    seen_pairs: Set[Tuple[int, int, str]] = set()

    def _add_proposal(src: int, tgt: int, pred: str, conf: float, meta: Optional[dict] = None):
        key = (src, tgt, pred)
        if key not in seen_pairs and src != tgt:
            seen_pairs.add(key)
            proposals.append({
                'source_id': src,
                'target_id': tgt,
                'predicate': pred,
                'confidence': conf,
                'extraction_method': 'heuristic',
                'metadata': meta,
            })

    content = _get_memory_content(conn, memory_id)
    if content is None:
        return []

    # Fetch summary for P2 ratio-guard index detection.
    summary_row = conn.execute(
        "SELECT summary FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    my_summary = summary_row[0] if summary_row else None

    my_tags = _get_memory_tags(conn, memory_id)
    my_project = _get_memory_project(conn, memory_id)
    my_embedding = _get_memory_embedding(conn, memory_id)

    # Pre-compute all memory ID references once (used by multiple Tier 1 blocks).
    all_ref_strs = _RE_MEMORY_REF.findall(content)

    # --- Tier 1: Content pattern matching (strongest signal) ---

    # Supersedes: look for explicit language + memory ID references.
    # P1: suppress if trigger word is in meta-discussion context.
    # P2: demote to 'references' if index/catalog pattern detected.
    supersedes_match = _RE_SUPERSEDES.search(content)
    if supersedes_match:
        if _is_meta_discussion(content, supersedes_match):
            logger.debug("P1 filter: supersedes match in meta-discussion context for memory %d", memory_id)
        elif _should_demote_to_references(content, my_summary, _RE_SUPERSEDES, all_ref_strs):
            for ref_id_str in all_ref_strs:
                ref_id = int(ref_id_str)
                if ref_id != memory_id:
                    row = conn.execute("SELECT id FROM memories WHERE id = ?", (ref_id,)).fetchone()
                    if row:
                        _add_proposal(memory_id, ref_id, 'references', _CONF_REFERENCES,
                                      {'reason': 'ratio_guard_demoted_from_supersedes',
                                       'demoted_from': 'supersedes'})
        elif _BELIEF_ENGINE_DEMOTE_STRONG:
            # L5-D demotion: the belief engine is the sole author of supersedes.
            # An ungated keyword match becomes a plain references edge.
            for ref_id_str in all_ref_strs:
                ref_id = int(ref_id_str)
                if ref_id != memory_id:
                    row = conn.execute("SELECT id FROM memories WHERE id = ?", (ref_id,)).fetchone()
                    if row:
                        _add_proposal(memory_id, ref_id, _DEMOTED_PREDICATE, _DEMOTED_CONF,
                                      {'reason': 'belief_engine_demoted_from_supersedes',
                                       'demoted_from': 'supersedes'})
        else:
            for ref_id_str in all_ref_strs:
                ref_id = int(ref_id_str)
                if ref_id != memory_id:
                    row = conn.execute("SELECT id FROM memories WHERE id = ?", (ref_id,)).fetchone()
                    if row:
                        _add_proposal(memory_id, ref_id, 'supersedes', _CONF_SUPERSEDES,
                                      {'reason': 'content_pattern_supersedes'})

    # Contradicts: look for explicit contradiction language + memory refs.
    # P1: suppress if trigger word is in meta-discussion context.
    contradicts_match = _RE_CONTRADICTS.search(content)
    if contradicts_match:
        if _is_meta_discussion(content, contradicts_match):
            logger.debug("P1 filter: contradicts match in meta-discussion context for memory %d", memory_id)
        elif _BELIEF_ENGINE_DEMOTE_STRONG:
            # L5-D demotion: the belief engine is the sole author of contradicts.
            # An ungated keyword match (the 416-poison source) becomes a plain
            # references edge instead of a fabricated contradiction.
            for ref_id_str in all_ref_strs:
                ref_id = int(ref_id_str)
                if ref_id != memory_id:
                    row = conn.execute("SELECT id FROM memories WHERE id = ?", (ref_id,)).fetchone()
                    if row:
                        _add_proposal(memory_id, ref_id, _DEMOTED_PREDICATE, _DEMOTED_CONF,
                                      {'reason': 'belief_engine_demoted_from_contradicts',
                                       'demoted_from': 'contradicts'})
        else:
            for ref_id_str in all_ref_strs:
                ref_id = int(ref_id_str)
                if ref_id != memory_id:
                    row = conn.execute("SELECT id FROM memories WHERE id = ?", (ref_id,)).fetchone()
                    if row:
                        _add_proposal(memory_id, ref_id, 'contradicts', _CONF_CONTRADICTS,
                                      {'reason': 'content_pattern_contradicts'})

    # Causes: look for causal language + memory refs.
    # P1: suppress if trigger word is in meta-discussion context.
    # P2 (iter-3): demote to 'references' if index/catalog pattern detected.
    causes_match = _RE_CAUSES.search(content)
    if causes_match:
        if _is_meta_discussion(content, causes_match):
            logger.debug("P1 filter: causes match in meta-discussion context for memory %d", memory_id)
        elif _should_demote_to_references(content, my_summary, _RE_CAUSES, all_ref_strs):
            for ref_id_str in all_ref_strs:
                ref_id = int(ref_id_str)
                if ref_id != memory_id:
                    row = conn.execute("SELECT id FROM memories WHERE id = ?", (ref_id,)).fetchone()
                    if row:
                        _add_proposal(memory_id, ref_id, 'references', _CONF_REFERENCES,
                                      {'reason': 'ratio_guard_demoted_from_causes',
                                       'demoted_from': 'causes'})
        else:
            for ref_id_str in all_ref_strs:
                ref_id = int(ref_id_str)
                if ref_id != memory_id:
                    row = conn.execute("SELECT id FROM memories WHERE id = ?", (ref_id,)).fetchone()
                    if row:
                        _add_proposal(memory_id, ref_id, 'causes', _CONF_CAUSES,
                                      {'reason': 'content_pattern_causes'})

    # P4 (iter-2): Depends_on: look for dependency language + memory refs.
    # P1: suppress if trigger word is in meta-discussion context.
    # P2 (iter-3): demote to 'references' if index/catalog pattern detected.
    depends_match = _RE_DEPENDS_ON.search(content)
    if depends_match:
        if _is_meta_discussion(content, depends_match):
            logger.debug("P1 filter: depends_on match in meta-discussion context for memory %d", memory_id)
        elif _should_demote_to_references(content, my_summary, _RE_DEPENDS_ON, all_ref_strs):
            for ref_id_str in all_ref_strs:
                ref_id = int(ref_id_str)
                if ref_id != memory_id:
                    row = conn.execute("SELECT id FROM memories WHERE id = ?", (ref_id,)).fetchone()
                    if row:
                        _add_proposal(memory_id, ref_id, 'references', _CONF_REFERENCES,
                                      {'reason': 'ratio_guard_demoted_from_depends_on',
                                       'demoted_from': 'depends_on'})
        else:
            for ref_id_str in all_ref_strs:
                ref_id = int(ref_id_str)
                if ref_id != memory_id:
                    row = conn.execute("SELECT id FROM memories WHERE id = ?", (ref_id,)).fetchone()
                    if row:
                        _add_proposal(memory_id, ref_id, 'depends_on', _CONF_DEPENDS_ON,
                                      {'reason': 'content_pattern_depends_on'})

    # Generic references: any memory ID mentioned in content.
    for ref_id_str in all_ref_strs:
        ref_id = int(ref_id_str)
        if ref_id != memory_id:
            row = conn.execute("SELECT id FROM memories WHERE id = ?", (ref_id,)).fetchone()
            if row:
                _add_proposal(memory_id, ref_id, 'references', _CONF_REFERENCES,
                              {'reason': 'content_memory_id_reference'})

    # --- Tier 2: Tag co-occurrence (weaker signal, but structural) ---

    if my_tags:
        # Find tags that are "rare" (appear in < 50 memories) for stronger signal.
        rare_tags = []
        for tag in my_tags:
            count = conn.execute(
                "SELECT COUNT(*) FROM memory_tags WHERE tag = ?", (tag,)
            ).fetchone()[0]
            if 2 <= count <= 50:  # Skip singleton tags and very common ones.
                rare_tags.append(tag)

        if rare_tags:
            placeholders = ",".join("?" * len(rare_tags))
            # Find memories sharing at least 2 rare tags (stronger co-occurrence signal).
            rows = conn.execute(
                f"""SELECT memory_id, COUNT(*) AS shared
                    FROM memory_tags
                    WHERE tag IN ({placeholders}) AND memory_id != ?
                    GROUP BY memory_id
                    HAVING shared >= 2
                    ORDER BY shared DESC
                    LIMIT ?""",
                (*rare_tags, memory_id, top_k),
            ).fetchall()

            for target_mid, shared_count in rows:
                _add_proposal(memory_id, target_mid, 'references', _CONF_REFERENCES,
                              {'reason': 'tag_co_occurrence', 'shared_tags': shared_count})

    # --- Tier 3: Same project + high cosine similarity -> refines ---
    # This tier requires sqlite-vec. If vec0 is not loaded, skip gracefully.
    #
    # IDS FIRST (round 1, L3). The query this replaced JOINed the project to the vec0 tier table and only THEN applied
    # ORDER BY/LIMIT 200; SQLite planned it as a full scan of the virtual table (`SCAN e VIRTUAL TABLE INDEX 3:2`), so
    # every call read every vector in the brain. MEASURED on a backup copy of the reference brain (2026-10-14, py-spy,
    # 36,277 samples): 89.6% of the stalled night sat in that fetchall, ~1.9-2.5 s per memory of the 5,365-memory
    # project, and the nightly loop timed out eight nights running. Now the 200 candidate IDS are chosen from
    # `memories` alone and only their vectors are read (`tier3_candidates`); a Stage-2 pass shares one candidate set
    # per project (`Tier3Cache`). The candidate set is the same one the JOIN chose: the newest `limit` memories of the
    # project that HAVE a vector (ties now broken by id, newest first, where the JOIN left them to the planner).

    if my_embedding and my_project:
        try:
            if tier3_cache is not None:
                similarities = tier3_cache.similar(conn, my_project, memory_id, my_embedding, threshold=0.85)
            else:
                similarities = []
                for other_id, other_emb in tier3_candidates(conn, my_project, memory_id):
                    sim = _cosine_similarity_from_bytes(my_embedding, other_emb)
                    if sim > 0.85:
                        similarities.append((other_id, sim))

            # Sort by similarity descending, take top_k.
            similarities.sort(key=lambda x: x[1], reverse=True)
            for other_id, sim in similarities[:top_k]:
                _add_proposal(memory_id, other_id, 'refines', _CONF_REFINES,
                              {'reason': 'high_cosine_same_project', 'cosine_similarity': round(sim, 4)})
        except sqlite3.OperationalError as e:
            # vec0 not loaded or memory_embeddings inaccessible -- skip Tier 3.
            logger.debug("Tier 3 cosine similarity skipped for memory %d: %s", memory_id, e)

    return proposals


# ============================================================
# Tier 3 candidates: ids first, then only those vectors
# ============================================================

TIER3_CANDIDATES = 200          # the newest N same-project memories that hold a vector (the JOIN's LIMIT 200)
_TIER3_PAGE = 256               # ids read per page while looking for N that hold a vector


def _vectors_for(conn: sqlite3.Connection, table: str, ids: Sequence[int]) -> Dict[int, bytes]:
    """The stored vectors of exactly `ids` (point lookups on the tier table's primary key; never a scan)."""
    out: Dict[int, bytes] = {}
    for mid in ids:
        row = conn.execute(f"SELECT embedding FROM {table} WHERE memory_id = ?", (mid,)).fetchone()
        if row is not None and row[0] is not None:
            out[mid] = row[0]
    return out


def tier3_candidates(conn: sqlite3.Connection, project: str, exclude_id: Optional[int],
                     limit: int = TIER3_CANDIDATES) -> List[Tuple[int, bytes]]:
    """The newest `limit` live memories of `project` (other than `exclude_id`) that hold a vector, newest first.

    Chosen from `memories` alone, a page of ids at a time, and only those ids' vectors are read -- so the cost is
    `limit` point lookups however large the brain's vector table is. A memory with no vector is passed over exactly as
    the inner JOIN passed it over, and the next page is read until `limit` are found or the project is exhausted.
    """
    table = _resolve_embed_tier(conn)[0]
    out: List[Tuple[int, bytes]] = []
    offset = 0
    while len(out) < limit:
        ids = [r[0] for r in conn.execute(
            """SELECT id FROM memories
               WHERE source_project = ? AND id != ? AND is_archived = 0
               ORDER BY created_at DESC, id DESC
               LIMIT ? OFFSET ?""",
            (project, -1 if exclude_id is None else exclude_id, _TIER3_PAGE, offset),
        ).fetchall()]
        if not ids:
            break
        vecs = _vectors_for(conn, table, ids)
        for mid in ids:
            if mid in vecs:
                out.append((mid, vecs[mid]))
                if len(out) >= limit:
                    break
        offset += len(ids)
        if len(ids) < _TIER3_PAGE:
            break
    return out


try:                                    # numpy is optional: a Tier-0 interpreter has none and runs the pure loop
    import numpy as _np                 # type: ignore
except Exception:                       # pragma: no cover - exercised by the pure-Python test path
    _np = None


class Tier3Cache:
    """One Stage-2 pass's Tier-3 candidate sets, read ONCE per project instead of once per memory.

    Exact, not approximate: for a project it holds the newest `limit + 1` memories that have a vector, so the
    candidates of any memory are the first `limit` of those once that memory itself is set aside -- the same set
    `tier3_candidates` returns. Valid while the pass runs: Stage 2 writes edges, never memories. (A memory another
    process stores in the same project mid-pass is not a candidate this night; it is processed itself, against the
    rest, on the next one.)

    The cosine is numpy's when numpy is importable (float64, the same arithmetic the pure loop does in Python
    floats), else `_cosine_similarity_from_bytes`, so a Tier-0 interpreter keeps working.
    """

    def __init__(self, limit: int = TIER3_CANDIDATES, use_numpy: Optional[bool] = None):
        self.limit = int(limit)
        self.use_numpy = (_np is not None) if use_numpy is None else (bool(use_numpy) and _np is not None)
        self._sets: Dict[str, Any] = {}
        self.loads = 0                  # how many candidate sets were read from the brain (one per project)

    def _load(self, conn: sqlite3.Connection, project: str):
        rows = tier3_candidates(conn, project, None, limit=self.limit + 1)
        self.loads += 1
        ids = [r[0] for r in rows]
        if self.use_numpy and rows:
            dims = {len(r[1]) for r in rows}
            if len(dims) == 1:
                mat = _np.frombuffer(b"".join(r[1] for r in rows), dtype="<f4").astype(_np.float64)
                mat = mat.reshape(len(rows), dims.pop() // 4)
                norms = _np.sqrt(_np.einsum("ij,ij->i", mat, mat))
                entry = ("np", ids, mat, norms)
            else:                       # mixed widths cannot share a matrix; the pure loop handles them pairwise
                entry = ("py", ids, [r[1] for r in rows])
        else:
            entry = ("py", ids, [r[1] for r in rows])
        self._sets[project] = entry
        return entry

    def similar(self, conn: sqlite3.Connection, project: str, memory_id: int, embedding: bytes,
                threshold: float = 0.85) -> List[Tuple[int, float]]:
        """(candidate id, cosine) for every candidate of `memory_id` whose cosine exceeds `threshold`."""
        entry = self._sets.get(project) or self._load(conn, project)
        ids = entry[1]
        keep = [i for i, mid in enumerate(ids) if mid != memory_id][: self.limit]
        out: List[Tuple[int, float]] = []
        if entry[0] == "np" and len(embedding) // 4 == entry[2].shape[1]:
            _, _, mat, norms = entry
            v = _np.frombuffer(embedding, dtype="<f4").astype(_np.float64)
            nv = float(_np.sqrt(v.dot(v)))
            if nv == 0.0 or not keep:
                return out
            idx = _np.asarray(keep, dtype=_np.int64)
            dots = mat[idx].dot(v)
            den = norms[idx] * nv
            for k, d, n in zip(idx.tolist(), dots.tolist(), den.tolist()):
                sim = d / n if n != 0.0 else 0.0
                if sim > threshold:
                    out.append((ids[k], sim))
            return out
        vecs = entry[2] if entry[0] == "py" else None
        if vecs is None:                # numpy matrix but a query vector of another width: compare pairwise
            vecs = [entry[2][i].astype("<f4").tobytes() for i in range(len(ids))]
        for i in keep:
            if len(vecs[i]) != len(embedding):
                continue
            sim = _cosine_similarity_from_bytes(embedding, vecs[i])
            if sim > threshold:
                out.append((ids[i], sim))
        return out


# ============================================================
# LLM-assisted edge extraction (stub for C3 Sleep Loop)
# ============================================================

_LLM_PROMPT_TEMPLATE = """You are analyzing relationships between memories in a knowledge graph.

Given the SOURCE memory and CANDIDATE memories, identify edges (relationships) that exist.

SOURCE MEMORY (ID: {source_id}):
{source_content}

CANDIDATE MEMORIES:
{candidates_text}

For each relationship found, respond with a JSON array of objects:
[
  {{
    "source_id": <int>,
    "target_id": <int>,
    "predicate": "<one of: causes, contradicts, supersedes, refines, generalizes, instantiates, references, depends_on>",
    "confidence": <float 0.0-1.0>,
    "reasoning": "<brief explanation>"
  }}
]

Rules:
- Only propose edges where there is clear evidence in the text.
- contradicts is SYMMETRIC (both directions).
- supersedes means the source REPLACES the target (target is now stale).
- refines means the source ADDS DETAIL to the target.
- causes means the source LED TO or TRIGGERED the target.
- references means the source CITES the target without stronger semantic relation.
- depends_on means the source REQUIRES the target to function.
- generalizes means the source ABSTRACTS from the target.
- instantiates means the source is a SPECIFIC CASE of the target.

Respond ONLY with the JSON array. If no edges found, respond with [].
"""


def extract_edges_llm(
    conn: sqlite3.Connection,
    memory_id: int,
    model: str = 'haiku',
    top_k: int = 10,
    skip_llm: bool = True,
) -> List[dict]:
    """LLM-assisted edge extraction.

    Stub implementation for C4. Sleep Loop (C3) will call this with
    skip_llm=False and provide the actual Anthropic API call.

    Args:
        conn: sqlite3 connection.
        memory_id: the memory to extract edges from.
        model: 'haiku' or 'sonnet'.
        top_k: max candidate memories to consider.
        skip_llm: if True (default), returns the prompt + candidates without
                  making an API call. Allows testing the prompt construction.

    Returns:
        List of edge proposal dicts. If skip_llm=True, returns a list with
        a single dict containing the constructed prompt for inspection.
    """
    content = _get_memory_content(conn, memory_id)
    if content is None:
        return []

    # Retrieve top candidates via embedding similarity.
    my_embedding = _get_memory_embedding(conn, memory_id)
    if my_embedding is None:
        return []

    # Use vector search to find candidates.
    try:
        rows = conn.execute(
            f"""SELECT emb.memory_id, emb.distance, m.content, m.summary
               FROM {_resolve_embed_tier(conn)[0]} AS emb
               JOIN memories AS m ON m.id = emb.memory_id
               WHERE emb.embedding MATCH ? AND k = ?
                 AND m.is_archived = 0 AND m.id != ?""",
            (my_embedding, top_k + 5, memory_id),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    if not rows:
        return []

    # Build candidates text.
    candidates_lines = []
    for mid, dist, cand_content, cand_summary in rows[:top_k]:
        preview = (cand_summary or cand_content[:200] or "").replace("\n", " ")
        candidates_lines.append(f"  ID {mid}: {preview}")

    candidates_text = "\n".join(candidates_lines)

    prompt = _LLM_PROMPT_TEMPLATE.format(
        source_id=memory_id,
        source_content=content[:2000],
        candidates_text=candidates_text,
    )

    extraction_method = f"llm_{model}"

    if skip_llm:
        return [{
            '_stub': True,
            'prompt': prompt,
            'candidate_ids': [r[0] for r in rows[:top_k]],
            'extraction_method': extraction_method,
            'note': 'LLM call skipped (--skip-llm). Sleep Loop C3 will provide real integration.',
        }]

    # Real LLM call would go here. Sleep Loop (C3) provides this.
    # For now, return empty.
    return []


# ============================================================
# Multi-hop graph expansion via recursive CTE
# ============================================================

def multi_hop_expand(
    conn: sqlite3.Connection,
    seed_ids: Sequence[int],
    depth: int = 2,
    current_only: bool = True,
    min_confidence: float = 0.3,
) -> List[int]:
    """Walk the edge graph from seed memories up to `depth` hops.

    Uses a recursive CTE for efficient SQLite traversal. Follows BOTH
    outgoing (source->target) and incoming (target->source) edges to
    handle bidirectional predicates and catch reverse relationships.

    Args:
        conn: sqlite3 connection with memory_edges table.
        seed_ids: starting memory IDs (typically the RRF-fused top-30).
        depth: max hops (1-3, clamped to MAX_CTE_DEPTH=3).
        current_only: if True, only follow edges where valid_until IS NULL.
        min_confidence: minimum edge confidence to traverse.

    Returns:
        Deduplicated list of discovered memory IDs (excluding seeds).
        Limited to MAX_EXPANDED_RESULTS (50).
    """
    if not seed_ids:
        return []

    depth = min(max(depth, 1), MAX_CTE_DEPTH)
    seed_set = set(seed_ids)
    seed_list = list(seed_set)

    placeholders = ",".join("?" * len(seed_list))

    # Build the validity clause.
    validity_clause = "AND e.valid_until IS NULL" if current_only else ""

    # The CTE walks both outgoing AND incoming edges.
    # This handles symmetric predicates (contradicts) and catches
    # reverse relationships (if A refines B, walking from B should find A).
    sql = f"""
    WITH RECURSIVE expanded(id, depth) AS (
        -- Base case: outgoing edges from seeds.
        SELECT e.target_id, 1
        FROM memory_edges e
        WHERE e.source_id IN ({placeholders})
          AND e.confidence >= ?
          {validity_clause}
        UNION
        -- Base case: incoming edges to seeds.
        SELECT e.source_id, 1
        FROM memory_edges e
        WHERE e.target_id IN ({placeholders})
          AND e.confidence >= ?
          {validity_clause}
        UNION
        -- Recursive: outgoing from already-expanded.
        SELECT e.target_id, expanded.depth + 1
        FROM memory_edges e
        JOIN expanded ON e.source_id = expanded.id
        WHERE expanded.depth < ?
          AND e.confidence >= ?
          {validity_clause}
        UNION
        -- Recursive: incoming to already-expanded.
        SELECT e.source_id, expanded.depth + 1
        FROM memory_edges e
        JOIN expanded ON e.target_id = expanded.id
        WHERE expanded.depth < ?
          AND e.confidence >= ?
          {validity_clause}
    )
    SELECT DISTINCT id FROM expanded
    WHERE id NOT IN ({placeholders})
    LIMIT ?
    """

    # Build params: seed_list appears 3 times (outgoing base, incoming base, exclusion).
    # min_confidence appears 4 times (outgoing base, incoming base, recursive outgoing, recursive incoming).
    # depth appears 2 times (recursive outgoing, recursive incoming).
    params = (
        *seed_list, min_confidence,          # outgoing base
        *seed_list, min_confidence,          # incoming base
        depth, min_confidence,               # recursive outgoing
        depth, min_confidence,               # recursive incoming
        *seed_list, MAX_EXPANDED_RESULTS,    # exclusion + limit
    )

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        logger.warning("Multi-hop CTE failed: %s", e)
        return []

    return [row[0] for row in rows]


# ============================================================
# Confidence decay + pruning
# ============================================================

# The decay clock (round 2, M7). MEASURED on a backup copy of the reference brain (r1 reflection): this function
# multiplied the STORED confidence by factor^(days since created_at) on EVERY run, so the decay compounded per run, not
# per day -- two runs 18 s apart both decayed ~28,800 edges, and the first catch-up after the stall hard-deleted 3,460.
# It now applies only the UTC day boundaries crossed since the last decay ran (metadata.edge_decay_last_run), so an
# edge's total decay is factor^(UTC days since it was created) however many times the loop runs, and a second run on
# the same UTC day changes nothing and deletes nothing.
EDGE_DECAY_STAMP_KEY = 'edge_decay_last_run'


def _utc_naive(ts: Optional[str]) -> Optional[datetime]:
    """A timestamp this brain holds, as a naive UTC datetime (None when unreadable).

    A space-separated value is SQLite's datetime('now'): already UTC. A value with a 'T' and no offset is what the
    sleep loop wrote into sleep_loop_runs before round 1: LOCAL time, so it is read as local and converted.
    """
    if not ts:
        return None
    s = str(ts).strip()
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    if 'T' in s:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)     # naive -> the host's local zone
    return dt


# The prune's record (round 2, lane R2d): pruning RETIRES an edge (valid_until + this record), it never deletes one.
DECAY_RETIRED_BY = 'decay-prune'

DECAY_RESTORE_SQL = """UPDATE memory_edges
   SET valid_until = NULL,
       confidence = json_extract(metadata, '$.retired_confidence'),
       metadata = json_extract(metadata, '$.prior_metadata')
 WHERE valid_until IS NOT NULL
   AND CASE WHEN json_valid(metadata) THEN json_extract(metadata, '$.retired_by') END = 'decay-prune'
   AND valid_until = CASE WHEN json_valid(metadata) THEN json_extract(metadata, '$.retired_at') END;"""


def is_decay_retired(valid_until: Optional[str], metadata: Optional[str]) -> bool:
    """True iff the row was ended by the decay's prune (and not by anything else): valid_until is set AND it carries
    the prune's record with the same time. Never raises on metadata that is not JSON."""
    if valid_until is None or not metadata:
        return False
    try:
        m = json.loads(metadata)
    except (TypeError, ValueError):
        return False
    return isinstance(m, dict) and m.get('retired_by') == DECAY_RETIRED_BY and m.get('retired_at') == valid_until


def _retirement_record(prior_metadata: Optional[str], stamp: str, retired_confidence: float,
                       confidence_before: float) -> str:
    return json.dumps({
        'retired_by': DECAY_RETIRED_BY,
        'retired_at': stamp,
        'retired_confidence': retired_confidence,
        'confidence_before': confidence_before,
        'prior_metadata': prior_metadata,
    })


def revive_decay_retired(
    conn: sqlite3.Connection,
    source_id: int,
    target_id: int,
    predicate: str,
    *,
    confidence: float,
    metadata: Optional[str],
    extraction_method: str,
    valid_from: Optional[str] = None,
    valid_until: Optional[str] = None,
) -> Optional[int]:
    """If a DECAY-RETIRED row holds this (source, target, predicate), make it the fresh edge a writer is asserting and
    return its id; else None (the caller INSERTs as before).

    Before round 2 (R2d) the prune DELETEd the row, so the UNIQUE triple was free and the writer's INSERT made a new
    current edge (created now, its own confidence and metadata). Retirement keeps the row, so the writer would hit the
    UNIQUE constraint, or INSERT OR IGNORE would silently leave the edge retired. This rewrites the retired row to
    exactly what that INSERT wrote, created_at included (the decay ages an edge from it). A row ended by anything else
    (a belief invalidation, a quarantine) is not touched: it is the caller's, as it always was.
    metadata is the JSON TEXT the caller would have inserted (or None)."""
    row = conn.execute(
        "SELECT id, valid_until, metadata FROM memory_edges WHERE source_id = ? AND target_id = ? AND predicate = ?",
        (source_id, target_id, predicate),
    ).fetchone()
    if row is None or not is_decay_retired(row[1], row[2]):
        return None
    conn.execute(
        """UPDATE memory_edges
              SET confidence = ?, valid_from = ?, valid_until = ?, metadata = ?, extraction_method = ?,
                  created_at = datetime('now')
            WHERE id = ? AND valid_until = ?""",
        (confidence, valid_from, valid_until, metadata, extraction_method, row[0], row[1]),
    )
    return row[0]


def decay_anchor(conn: sqlite3.Connection) -> Tuple[Optional[datetime], str]:
    """When this brain's edge confidences were last decayed, and how that was known.

    1. metadata.edge_decay_last_run -- written by every decay run since round 2.
    2. else the last success/partial sleep run's completed_at: the code before round 2 decayed every edge to its full
       age on each run, so a stored confidence already carries every day up to that run (and more). Counting from it
       is what stops the first run after landing from applying an edge's whole age a second time.
    3. else None: this brain has never been decayed, so each edge counts from its own created_at.
    """
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key = ?", (EDGE_DECAY_STAMP_KEY,)).fetchone()
    except sqlite3.Error:
        row = None
    dt = _utc_naive(row[0]) if row and row[0] else None
    if dt is not None:
        return dt, "metadata.%s" % EDGE_DECAY_STAMP_KEY
    try:
        row = conn.execute("""SELECT completed_at FROM sleep_loop_runs
                              WHERE status IN ('success', 'partial') AND completed_at IS NOT NULL
                              ORDER BY id DESC LIMIT 1""").fetchone()
    except sqlite3.Error:
        row = None
    dt = _utc_naive(row[0]) if row and row[0] else None
    if dt is not None:
        return dt, "the last completed sleep run"
    return None, "never decayed: each edge from its created_at"


def _write_decay_stamp(conn: sqlite3.Connection, now: datetime) -> None:
    """Record this decay INSIDE the caller's transaction, so it commits with the confidences it accounts for."""
    stamp = now.strftime('%Y-%m-%d %H:%M:%S')
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(metadata)").fetchall()}
    except sqlite3.Error:
        return
    if not cols:
        return
    if 'updated_at' in cols:
        conn.execute("INSERT OR REPLACE INTO metadata (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                     (EDGE_DECAY_STAMP_KEY, stamp))
    else:
        conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (EDGE_DECAY_STAMP_KEY, stamp))


def decay_confidence(
    conn: sqlite3.Connection,
    factor: float = 0.99,
    min_threshold: float = 0.2,
    max_age_days: int = 30,
    dry_run: bool = False,
) -> dict:
    """Decay edge confidences for the days elapsed since the last decay, and prune stale edges.

    The clock is the UTC calendar day. For each edge the run applies
        new_confidence = confidence * (factor ^ ticks)
    where ticks is the number of UTC midnights between max(created_at, last decay) and now. Over any sequence of runs an
    edge's ticks sum to the UTC days since it was created, so the decay is per DAY and never per RUN: a second run on the
    same UTC day changes no confidence and deletes no edge. Heuristic edges decay at factor^2 per day.

    Edges below min_threshold AND older than max_age_days (UTC days since created) are pruned, and a pruned edge is
    RETIRED, never deleted: valid_until is set to this run's UTC time and metadata becomes the prune's record (the
    module docstring's "Pruning retires" gives the record and the one statement that restores them). Only current
    edges (valid_until IS NULL) are read, so a retired edge is never decayed, retired or counted again, and a dry run
    counts exactly what the real run retires.

    Args:
        conn: sqlite3 connection.
        factor: per-day decay factor (0.99 = 1% decay per day).
        min_threshold: prune edges below this confidence.
        max_age_days: only prune edges older than this.
        dry_run: count what the run would do and write nothing (no confidence, no prune, no stamp).

    Returns:
        {decayed_count, pruned_count (= retired_count), retired_count, deleted_count (always 0),
         remaining_count (CURRENT edges after the run), anchor, anchor_basis}.
    """
    # UTC, naive: memory_edges.created_at is SQLite's datetime('now'). Local time put every edge's age up to a day off.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today = now.date()
    anchor, basis = decay_anchor(conn)

    stamp = now.strftime('%Y-%m-%d %H:%M:%S')       # the brain's datetime('now') format, UTC

    # Only CURRENT edges: a retired edge (by this prune or anything else) is never decayed, retired or counted again.
    rows = conn.execute(
        "SELECT id, confidence, created_at, extraction_method, metadata FROM memory_edges WHERE valid_until IS NULL"
    ).fetchall()

    decayed_count = 0
    pruned_count = 0
    retirements: List[Tuple[str, str, int]] = []

    for edge_id, confidence, created_at_str, extraction_method, metadata in rows:
        try:
            created_at = datetime.fromisoformat(created_at_str)      # SQLite datetime('now'): UTC, naive
        except (ValueError, TypeError):
            continue
        if created_at.tzinfo is not None:
            created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
        since = created_at if anchor is None or created_at > anchor else anchor
        ticks = (today - since.date()).days
        if ticks <= 0:
            continue
        age_days = (today - created_at.date()).days

        # Heuristic edges decay at factor^2 per day (faster).
        effective_factor = factor ** 2 if extraction_method == 'heuristic' else factor
        new_confidence = round(confidence * (effective_factor ** ticks), 6)

        if new_confidence < min_threshold and age_days > max_age_days:
            retirements.append((stamp, _retirement_record(metadata, stamp, new_confidence, confidence), edge_id))
            pruned_count += 1
        elif new_confidence != confidence:
            # Every change is written: the clock advances below, so a skipped small step would be lost for good.
            if not dry_run:
                conn.execute("UPDATE memory_edges SET confidence = ? WHERE id = ?", (new_confidence, edge_id))
            decayed_count += 1

    # Prune = RETIRE (round 2, R2d): valid_until + the record, in the caller's transaction. Never DELETE: one statement
    # (DECAY_RESTORE_SQL) brings any of them back. The `valid_until IS NULL` guard keeps a retirement from landing twice.
    if retirements and not dry_run:
        conn.executemany("UPDATE memory_edges SET valid_until = ?, metadata = ? WHERE id = ? AND valid_until IS NULL",
                         retirements)

    if not dry_run:
        _write_decay_stamp(conn, now)

    # The CURRENT graph after the run (a dry run: what it would be).
    remaining = conn.execute("SELECT COUNT(*) FROM memory_edges WHERE valid_until IS NULL").fetchone()[0]
    if dry_run:
        remaining -= pruned_count

    return {
        'decayed_count': decayed_count,
        'pruned_count': pruned_count,            # = retired: the sleep loop's edges_pruned keeps this meaning
        'retired_count': pruned_count,
        'deleted_count': 0,
        'remaining_count': remaining,
        'anchor': anchor.strftime('%Y-%m-%d %H:%M:%S') if anchor else None,
        'anchor_basis': basis,
    }


# ============================================================
# One-time repair of the per-run compounding (round 2, lane R2d, option C; owner ruling 2026-10-15)
# ============================================================
#
# Before round 2 the decay multiplied the STORED confidence by factor^(age in days) on EVERY sleep run, so an edge
# decayed by its whole age again each night. That code (unchanged from the repo's first commit, 742e5da, to
# 9e36990) was, per current edge:
#     days = (datetime.now() - created_at).days          # LOCAL naive now minus a UTC naive created_at
#     if days <= 0: skip
#     new = confidence * (ef ** days)                    # ef = factor**2 for 'heuristic', else factor
#     if new < prune_threshold and days > max_age_days: DELETE
#     elif abs(new - confidence) > 0.001: confidence = round(new, 6)
# and every run left its row in sleep_loop_runs: started_at / completed_at in the same LOCAL naive clock ('T' form),
# its status, and config_snapshot with the decay_factor / prune_threshold / max_age_days it used.
#
# The repair re-derives, per edge, WHICH confidence it was created with by REPLAYING that code over the ledger from
# each creation confidence the code that wrote the edge can produce, and accepts an edge only when exactly one
# creation confidence reproduces its stored value to the last digit. The repaired value is what per-UTC-day decay
# holds at the brain's decay anchor: c0 * ef^(anchor UTC date - created UTC date). The next decay run carries it
# from the anchor to today, so no day is counted twice. Nothing is guessed: an edge whose history does not replay
# exactly, replays from more than one start, or was also moved by another writer (NREM) is left as it is and counted.

DECAY_REPAIR_KEY = 'edge_decay_repair'
_REPAIR_STAGE2_CONFS = (0.6, 0.7, 0.8)      # _CONF_* above: every confidence Stage 2 / --auto-edges can create with
_REPAIR_MAX_STATES = 64
_REPAIR_EPS = 1e-9


def _repair_creation_confidences(metadata: Optional[str]) -> Tuple[Optional[Tuple[float, ...]], str]:
    """The creation confidences the code that wrote this edge can produce, from its metadata; (None, reason) when
    the edge's confidence was also moved by a writer that kept no ledger, or its writer is not known."""
    try:
        m = json.loads(metadata) if metadata else {}
    except (TypeError, ValueError):
        return None, 'origin-unknown'
    if not isinstance(m, dict):
        return None, 'origin-unknown'
    if 'nrem' in m:
        return None, 'nrem-moved'           # NREM potentiation / downscale changed it; no ledger of those steps
    if 'reason' in m:
        return _REPAIR_STAGE2_CONFS, 'stage2'
    if m.get('authored_by') == 'belief_engine':
        nli = m.get('nli_score')
        if nli is None:
            return (0.8,), 'belief'
        try:
            return (max(0.0, min(1.0, float(nli))),), 'belief'
        except (TypeError, ValueError):
            return None, 'origin-unknown'
    if m.get('tag') == 'tension':
        return (0.2,), 'tension'
    if m.get('origin') == 'dream_answer':
        return (0.8,), 'dream-answer'
    return None, 'origin-unknown'


def _repair_ledger(conn: sqlite3.Connection) -> Tuple[Optional[List[tuple]], Optional[str]]:
    """The old code's runs: [(t_lo, t_hi, certain, factor, threshold, max_age)], LOCAL naive datetimes, or
    (None, reason) when the ledger cannot be replayed with certainty."""
    try:
        rows = conn.execute("SELECT id, started_at, completed_at, status, config_snapshot FROM sleep_loop_runs "
                            "ORDER BY id").fetchall()
    except sqlite3.Error:
        return None, 'no sleep_loop_runs ledger'
    runs = []
    for i, (rid, started, completed, status, snap) in enumerate(rows):
        if not started or 'T' not in str(started):
            # a ' '-form row is written by the round-1+ code, which never compounded; it must not be replayed
            return None, 'run %s was not written by the compounding code (started_at %r)' % (rid, started)
        try:
            lo = datetime.fromisoformat(str(started))
        except ValueError:
            return None, 'run %s has an unreadable started_at %r' % (rid, started)
        if completed:
            try:
                hi = datetime.fromisoformat(str(completed))
            except ValueError:
                return None, 'run %s has an unreadable completed_at %r' % (rid, completed)
        else:
            # a run that never closed: its decay (if it reached it) fell before the next run began
            nxt = rows[i + 1][1] if i + 1 < len(rows) else None
            try:
                hi = datetime.fromisoformat(str(nxt)) if nxt else lo + timedelta(days=1)
            except ValueError:
                hi = lo + timedelta(days=1)
        try:
            cfg = json.loads(snap) if snap else None
        except (TypeError, ValueError):
            cfg = None
        if not isinstance(cfg, dict) or not all(k in cfg for k in ('decay_factor', 'prune_threshold', 'max_age_days')):
            return None, 'run %s has no config_snapshot with the decay settings' % rid
        # success = every stage ran, so the decay ran; any other status may or may not have reached it -> both
        runs.append((lo, hi, status == 'success', float(cfg['decay_factor']), float(cfg['prune_threshold']),
                     int(cfg['max_age_days'])))
    return runs, None


def _repair_replay(c0: float, created: datetime, method: Optional[str], runs: List[tuple]) -> Optional[set]:
    """Every value the old code can have left from c0 (None: too many branches to be certain)."""
    states = {c0}
    for lo, hi, certain, factor, thr, max_age in runs:
        d_lo, d_hi = (lo - created).days, (hi - created).days
        if d_hi <= 0:
            continue
        ef = factor ** 2 if method == 'heuristic' else factor
        nxt = set() if certain else set(states)
        for d in range(d_lo, d_hi + 1):
            for c in states:
                if d <= 0:
                    nxt.add(c)
                    continue
                new = c * (ef ** d)
                if new < thr and d > max_age:
                    continue                      # that branch deleted the edge; the edge is here, so not taken
                nxt.add(round(new, 6) if abs(new - c) > 0.001 else c)
        states = nxt
        if not states or len(states) > _REPAIR_MAX_STATES:
            return states if not states else None
    return states


def _replay_edge(conf: float, created: datetime, method: Optional[str], cands: Tuple[float, ...],
                 runs: List[tuple]) -> Tuple[Optional[float], str]:
    """(c0, 'unique') when exactly one creation confidence replays to the stored value; else (None, why)."""
    hits = []
    for c0 in cands:
        st = _repair_replay(c0, created, method, runs)
        if st is None:
            return None, 'ambiguous-history'
        if any(abs(v - conf) <= _REPAIR_EPS for v in st):
            hits.append(c0)
    if not hits:
        return None, 'history-does-not-replay'
    if len(hits) > 1:
        return None, 'more-than-one-start-replays'
    return hits[0], 'unique'


def _settle_uncertain_runs(edges: List[tuple], runs: List[tuple]) -> Tuple[List[tuple], List[dict]]:
    """A run whose status does not prove its decay ran ('running', 'timeout', 'error', 'partial') is settled by the
    data, not assumed: starting from "none of them decayed", each is tried as "it decayed" over every edge whose
    writer is known, and is taken as decayed only if strictly more edges then replay exactly. The decision and both
    counts are reported for every such run. Returns the settled ledger (every run certain)."""
    unc = [i for i, r in enumerate(runs) if not r[2]]
    if not unc:
        return runs, []

    def ledger(ran: Set[int]) -> List[tuple]:
        return [(r[0], r[1], True) + tuple(r[3:]) for i, r in enumerate(runs) if r[2] or i in ran]

    def unique(rs: List[tuple]) -> int:
        return sum(1 for conf, created, method, cands in edges
                   if _replay_edge(conf, created, method, cands, rs)[1] == 'unique')

    ran: Set[int] = set()
    base = unique(ledger(ran))
    notes = []
    for i in unc:
        flipped = unique(ledger(ran | {i}))
        took = flipped > base
        notes.append({'run_started_at': runs[i][0].isoformat(), 'replay_if_not_decayed': base,
                      'replay_if_decayed': flipped, 'settled_as': 'decayed' if took else 'did not decay'})
        if took:
            ran.add(i)
            base = flipped
    return ledger(ran), notes


def _read_repair_record(conn: sqlite3.Connection) -> Optional[dict]:
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key = ?", (DECAY_REPAIR_KEY,)).fetchone()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None
    try:
        rec = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def _put_metadata(conn: sqlite3.Connection, key: str, value: Optional[str]) -> None:
    if value is None:
        conn.execute("DELETE FROM metadata WHERE key = ?", (key,))
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(metadata)").fetchall()}
    if 'updated_at' in cols:
        conn.execute("INSERT OR REPLACE INTO metadata (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                     (key, value))
    else:
        conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (key, value))


def _prune_outlook(conn: sqlite3.Connection, conf_of: Dict[int, float], factor: float, thr: float, max_age: int,
                   anchor: datetime) -> List[int]:
    """Which current edges the NEXT decay run (today, from this anchor) would retire, given these confidences."""
    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    out = []
    for eid, created_s, method in conn.execute(
            "SELECT id, created_at, extraction_method FROM memory_edges WHERE valid_until IS NULL"):
        try:
            created = datetime.fromisoformat(created_s)
        except (TypeError, ValueError):
            continue
        since = created if created > anchor else anchor
        ticks = (today - since.date()).days
        if ticks <= 0:
            continue
        ef = factor ** 2 if method == 'heuristic' else factor
        if round(conf_of[eid] * (ef ** ticks), 6) < thr and (today - created.date()).days > max_age:
            out.append(eid)
    return out


def _histogram(values) -> Dict[str, int]:
    edges = [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0000001]
    out = {}
    vals = list(values)
    for a, b in zip(edges, edges[1:]):
        out['[%.2f,%.2f)' % (a, min(b, 1.0))] = sum(1 for v in vals if a <= v < b)
    return out


def repair_decay(conn: sqlite3.Connection, apply: bool = False, undo: bool = False,
                 next_run: Optional[dict] = None) -> dict:
    """The one-time repair of the per-run compounding (dry run unless apply=True; undo=True reverses an applied one).

    Reversible: every repaired edge's pre-repair confidence is kept in metadata[edge_decay_repair] (no new table).
    Idempotent: an edge already in that record is not repaired again, so a second apply changes nothing.
    Refuses (changes nothing) once the per-day decay has run on this brain (metadata.edge_decay_last_run is set by
    it), because the replay is only valid over a ledger the compounding code wrote alone.
    next_run: the decay settings of the catch-up that will follow (default: the ledger's last), for the outlook.
    """
    if undo:
        return _undo_repair(conn)
    rec = _read_repair_record(conn)
    done: Dict[str, Any] = (rec or {}).get('edges') or {}
    stamp_row = conn.execute("SELECT value FROM metadata WHERE key = ?", (EDGE_DECAY_STAMP_KEY,)).fetchone()
    if stamp_row and stamp_row[0] and not (rec and rec.get('wrote_stamp') == stamp_row[0]):
        return {'status': 'refused', 'apply': apply,
                'reason': 'the per-day decay has already run on this brain (metadata.%s = %s); the replay is only '
                          'valid over a ledger written by the compounding code alone' % (EDGE_DECAY_STAMP_KEY,
                                                                                         stamp_row[0])}
    runs, why = _repair_ledger(conn)
    if runs is None:
        return {'status': 'refused', 'apply': apply, 'reason': why}
    factors = {r[3] for r in runs}
    if len(factors) != 1:
        return {'status': 'refused', 'apply': apply,
                'reason': 'the ledger used more than one decay_factor %s' % sorted(factors)}
    factor = factors.pop()
    anchor, basis = decay_anchor(conn)
    if anchor is None:
        return {'status': 'refused', 'apply': apply, 'reason': 'this brain has never been decayed: nothing to repair'}
    anchor_date = anchor.date()

    reasons: Dict[str, int] = {}
    repaired: Dict[int, Tuple[float, float, float]] = {}
    before: Dict[int, float] = {}
    examined = 0
    todo: List[tuple] = []
    for eid, conf, created_s, method, meta in conn.execute(
            "SELECT id, confidence, created_at, extraction_method, metadata FROM memory_edges "
            "WHERE valid_until IS NULL ORDER BY id").fetchall():
        examined += 1
        before[eid] = conf
        if str(eid) in done:
            reasons['already-repaired'] = reasons.get('already-repaired', 0) + 1
            continue
        try:
            created = datetime.fromisoformat(created_s)
        except (TypeError, ValueError):
            reasons['unreadable-created_at'] = reasons.get('unreadable-created_at', 0) + 1
            continue
        cands, origin = _repair_creation_confidences(meta)
        if cands is None:
            reasons[origin] = reasons.get(origin, 0) + 1
            continue
        todo.append((eid, conf, created, method, cands, origin))

    runs, settled = _settle_uncertain_runs([t[1:5] for t in todo], runs)
    by_origin: Dict[str, int] = {}
    for eid, conf, created, method, cands, origin in todo:
        c0, why = _replay_edge(conf, created, method, cands, runs)
        if c0 is None:
            reasons[why] = reasons.get(why, 0) + 1
            continue
        ef = factor ** 2 if method == 'heuristic' else factor
        days = max(0, (anchor_date - created.date()).days)
        true = round(min(c0, c0 * (ef ** days)), 6)          # never above the reconstructed original
        if true <= conf + 1e-6:
            reasons['undamaged'] = reasons.get('undamaged', 0) + 1
            continue
        repaired[eid] = (conf, true, c0)
        label = '%s, created at %s' % (origin, c0) if origin != 'belief' else 'belief, created at its nli_score'
        by_origin[label] = by_origin.get(label, 0) + 1

    after = dict(before)
    after.update({eid: v[1] for eid, v in repaired.items()})
    nr = next_run or {'decay_factor': runs[-1][3], 'prune_threshold': runs[-1][4], 'max_age_days': runs[-1][5]}
    out_before = _prune_outlook(conn, before, nr['decay_factor'], nr['prune_threshold'], nr['max_age_days'], anchor)
    out_after = set(_prune_outlook(conn, after, nr['decay_factor'], nr['prune_threshold'], nr['max_age_days'],
                                   anchor))
    report = {
        'status': 'applied' if apply else 'dry-run',
        'apply': apply,
        'edges_examined': examined,
        'edges_repaired': len(repaired),
        'edges_left_unchanged': examined - len(repaired),
        'left_unchanged_by_reason': dict(sorted(reasons.items())),
        'anchor': anchor.strftime('%Y-%m-%d %H:%M:%S'), 'anchor_basis': basis,
        'ledger_runs_replayed': len(runs), 'decay_factor': factor,
        'runs_whose_status_did_not_prove_a_decay': settled,
        'repaired_by_writer': dict(sorted(by_origin.items())),
        'distribution_before': _histogram(before.values()),
        'distribution_after': _histogram(after.values()),
        'next_run_would_retire_without_repair': len(out_before),
        'of_those_survive_after_repair': sum(1 for e in out_before if e not in out_after),
        'next_run_would_retire_after_repair': len(out_after),
    }
    if not apply:
        return report
    if not repaired:
        report['status'] = 'nothing-to-repair'       # a second --apply lands here: it changes nothing
        return report

    rec = rec or {'edges': {}}
    rec.setdefault('edges', {})
    for eid, (b, a, c0) in repaired.items():
        conn.execute("UPDATE memory_edges SET confidence = ? WHERE id = ? AND valid_until IS NULL", (a, eid))
        rec['edges'][str(eid)] = [b, a, c0]
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S')
    rec['applied_at'] = rec.get('applied_at') or now
    rec['anchor'] = report['anchor']
    rec['decay_factor'] = factor
    if not (stamp_row and stamp_row[0]):
        # Pin the anchor the repair decayed to, so the next run decays from it forward and from nowhere else.
        _put_metadata(conn, EDGE_DECAY_STAMP_KEY, report['anchor'])
        rec['wrote_stamp'] = report['anchor']
    _put_metadata(conn, DECAY_REPAIR_KEY, json.dumps(rec))
    return report


def _undo_repair(conn: sqlite3.Connection) -> dict:
    """Put every repaired edge's pre-repair confidence back -- only where the repair's value is still there (an edge
    a later decay moved is reported, not overwritten) -- and remove the record and the anchor stamp it wrote."""
    rec = _read_repair_record(conn)
    if not rec:
        return {'status': 'nothing-to-undo'}
    restored = moved = gone = 0
    for eid, (b, a, _c0) in (rec.get('edges') or {}).items():
        row = conn.execute("SELECT confidence FROM memory_edges WHERE id = ?", (int(eid),)).fetchone()
        if row is None:
            gone += 1
        elif abs(row[0] - a) <= _REPAIR_EPS:
            conn.execute("UPDATE memory_edges SET confidence = ? WHERE id = ?", (b, int(eid)))
            restored += 1
        else:
            moved += 1
    stamp_removed = False
    if rec.get('wrote_stamp') and not moved:
        row = conn.execute("SELECT value FROM metadata WHERE key = ?", (EDGE_DECAY_STAMP_KEY,)).fetchone()
        if row and row[0] == rec['wrote_stamp']:
            _put_metadata(conn, EDGE_DECAY_STAMP_KEY, None)
            stamp_removed = True
    if moved:
        # keep the record for the edges not restored, so nothing is lost
        rec['edges'] = {k: v for k, v in rec['edges'].items()
                        if (conn.execute("SELECT confidence FROM memory_edges WHERE id = ?", (int(k),)).fetchone()
                            or [None])[0] != v[0]}
        _put_metadata(conn, DECAY_REPAIR_KEY, json.dumps(rec))
    else:
        _put_metadata(conn, DECAY_REPAIR_KEY, None)
    return {'status': 'undone' if restored or not moved else 'nothing-undone', 'restored': restored,
            'moved_since_repair_left_as_is': moved,
            'edge_no_longer_exists': gone, 'anchor_stamp_removed': stamp_removed}


# ============================================================
# Edge query
# ============================================================

def get_edges(
    conn: sqlite3.Connection,
    memory_id: int,
    direction: str = 'both',
    predicate: Optional[str] = None,
    current_only: bool = True,
) -> List[dict]:
    """Query edges for a specific memory.

    Args:
        conn: sqlite3 connection.
        memory_id: the memory to query edges for.
        direction: 'outgoing' (source=memory_id), 'incoming' (target=memory_id), or 'both'.
        predicate: filter to a specific predicate (None = all).
        current_only: if True, only return edges where valid_until IS NULL.

    Returns:
        List of edge dicts with: id, source_id, target_id, predicate,
        confidence, valid_from, valid_until, created_at, metadata, extraction_method.
    """
    clauses: List[str] = []
    params: List[Any] = []

    if direction == 'outgoing':
        clauses.append("source_id = ?")
        params.append(memory_id)
    elif direction == 'incoming':
        clauses.append("target_id = ?")
        params.append(memory_id)
    else:  # both
        clauses.append("(source_id = ? OR target_id = ?)")
        params.extend([memory_id, memory_id])

    if predicate:
        if predicate not in VALID_PREDICATES:
            raise ValueError(f"Invalid predicate {predicate!r}")
        clauses.append("predicate = ?")
        params.append(predicate)

    if current_only:
        clauses.append("valid_until IS NULL")

    where = "WHERE " + " AND ".join(clauses) if clauses else ""

    rows = conn.execute(
        f"""SELECT id, source_id, target_id, predicate, confidence,
                   valid_from, valid_until, created_at, metadata, extraction_method
            FROM memory_edges
            {where}
            ORDER BY confidence DESC, created_at DESC""",
        params,
    ).fetchall()

    edges = []
    for r in rows:
        meta = None
        if r[8]:
            try:
                meta = json.loads(r[8])
            except (json.JSONDecodeError, TypeError):
                meta = r[8]
        edges.append({
            'id': r[0],
            'source_id': r[1],
            'target_id': r[2],
            'predicate': r[3],
            'confidence': r[4],
            'valid_from': r[5],
            'valid_until': r[6],
            'created_at': r[7],
            'metadata': meta,
            'extraction_method': r[9],
        })

    return edges


# ============================================================
# Migration helpers
# ============================================================

def is_temporal_edges_migration_applied(conn: sqlite3.Connection) -> bool:
    """Check if the memory_edges table exists."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_edges'"
    ).fetchone()
    return row is not None


def apply_temporal_edges_migration(
    db_path: str,
    migration_sql_path: str,
) -> dict:
    """Apply migration_003_temporal_edges.sql. Idempotent."""
    result: dict = {"status": "pending"}
    db = Path(db_path)
    if not db.exists():
        return {"status": "error", "message": f"DB not found: {db_path}"}

    conn = sqlite3.connect(str(db), timeout=60.0)
    try:
        migration_text = Path(migration_sql_path).read_text()
    except Exception as e:
        conn.close()
        return {"status": "error", "message": f"Could not read migration SQL: {e}"}

    try:
        conn.executescript(migration_text)
        result["status"] = "ok"
    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)

    # Verify.
    try:
        edge_count = conn.execute("SELECT COUNT(*) FROM memory_edges").fetchone()[0]
        schema_row = conn.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
        result["edge_count"] = edge_count
        result["schema_version"] = schema_row[0] if schema_row else None
    except Exception:
        pass

    conn.close()
    return result


# ============================================================
# Unit tests
# ============================================================

def _create_test_db() -> Tuple[sqlite3.Connection, str]:
    """Create an in-memory test DB with minimal schema."""
    import tempfile
    tf = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tf.close()
    db_path = tf.name

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    # Minimal schema to support temporal_edges.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            summary TEXT,
            category TEXT DEFAULT 'project_knowledge',
            importance REAL DEFAULT 0.5,
            source_project TEXT,
            source_context TEXT,
            is_archived INTEGER DEFAULT 0,
            access_count INTEGER DEFAULT 0,
            version INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            last_accessed TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS memory_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL REFERENCES memories(id),
            tag TEXT NOT NULL,
            UNIQUE(memory_id, tag)
        );
        CREATE TABLE IF NOT EXISTS memory_embeddings (
            memory_id INTEGER PRIMARY KEY REFERENCES memories(id),
            embedding BLOB
        );
        -- The fixture DECLARES its tier (the plain table above): embed_resolver refuses to guess, and without this
        -- row the heuristic-extraction self-test died with EmbedTierUnresolved before asserting anything (measured on
        -- f275d09, round 1 L3; the same defect sleep_loop's suite had in Round 3 G3).
        INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', 'memory_embeddings');
    """)

    # Apply temporal edges migration.
    migration_path = Path(__file__).parent.parent / "migrations" / "migration_003_temporal_edges.sql"
    if migration_path.exists():
        conn.executescript(migration_path.read_text())
    else:
        # Inline migration for standalone testing.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                target_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                predicate TEXT NOT NULL CHECK(predicate IN (
                    'causes', 'contradicts', 'supersedes', 'refines',
                    'generalizes', 'instantiates', 'references', 'depends_on'
                )),
                confidence REAL NOT NULL DEFAULT 0.8 CHECK(confidence >= 0.0 AND confidence <= 1.0),
                valid_from TEXT DEFAULT NULL,
                valid_until TEXT DEFAULT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                metadata TEXT DEFAULT NULL,
                extraction_method TEXT DEFAULT 'manual' CHECK(extraction_method IN (
                    'manual', 'heuristic', 'llm_haiku', 'llm_sonnet', 'sleep_loop'
                )),
                UNIQUE(source_id, target_id, predicate),
                CHECK(source_id != target_id)
            );
            CREATE INDEX IF NOT EXISTS idx_edges_source ON memory_edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON memory_edges(target_id);
            CREATE INDEX IF NOT EXISTS idx_edges_predicate ON memory_edges(predicate);
            CREATE INDEX IF NOT EXISTS idx_edges_valid ON memory_edges(valid_until) WHERE valid_until IS NULL;
            INSERT OR REPLACE INTO metadata (key, value) VALUES ('schema_version', '2.2-L2-temporal-edges');
        """)

    conn.commit()
    return conn, db_path


def _insert_test_memories(conn: sqlite3.Connection, count: int = 10) -> List[int]:
    """Insert test memories. Returns list of IDs."""
    ids = []
    for i in range(1, count + 1):
        cursor = conn.execute(
            "INSERT INTO memories (content, summary, source_project) VALUES (?, ?, ?)",
            (f"Test memory {i} content about topic {i % 3}", f"Test memory {i}", f"project-{i % 2}"),
        )
        ids.append(cursor.lastrowid)
    conn.commit()
    return ids


def unit_test_create_edge() -> dict:
    """Test create_edge: UNIQUE constraint, self-edge rejection, contradicts symmetry."""
    conn, db_path = _create_test_db()
    results: Dict[str, Any] = {}
    failures: List[str] = []

    try:
        ids = _insert_test_memories(conn, 5)

        # Basic edge creation.
        edge_id = create_edge(conn, ids[0], ids[1], 'references', confidence=0.7)
        conn.commit()
        results["basic_create"] = edge_id > 0
        if not edge_id > 0:
            failures.append("basic_create: edge_id not positive")

        # Self-edge rejection.
        try:
            create_edge(conn, ids[0], ids[0], 'references')
            results["self_edge_rejected"] = False
            failures.append("self_edge_rejected: no ValueError raised")
        except ValueError:
            results["self_edge_rejected"] = True

        # Invalid predicate.
        try:
            create_edge(conn, ids[0], ids[2], 'bogus_predicate')
            results["invalid_predicate_rejected"] = False
            failures.append("invalid_predicate_rejected: no ValueError raised")
        except ValueError:
            results["invalid_predicate_rejected"] = True

        # Duplicate edge (UNIQUE constraint).
        try:
            create_edge(conn, ids[0], ids[1], 'references')
            conn.commit()
            results["unique_constraint"] = False
            failures.append("unique_constraint: no IntegrityError raised")
        except sqlite3.IntegrityError:
            conn.rollback()
            results["unique_constraint"] = True

        # Contradicts symmetry: creating A->B contradicts should also create B->A.
        create_edge(conn, ids[1], ids[2], 'contradicts', confidence=0.7)
        conn.commit()
        # Check reverse edge exists.
        reverse = conn.execute(
            "SELECT id FROM memory_edges WHERE source_id = ? AND target_id = ? AND predicate = 'contradicts'",
            (ids[2], ids[1]),
        ).fetchone()
        results["contradicts_symmetry"] = reverse is not None
        if not reverse:
            failures.append("contradicts_symmetry: reverse edge not created")

        # Non-existent memory.
        try:
            create_edge(conn, ids[0], 99999, 'references')
            results["nonexistent_memory_rejected"] = False
            failures.append("nonexistent_memory_rejected: no ValueError raised")
        except ValueError:
            results["nonexistent_memory_rejected"] = True

        # Confidence bounds.
        try:
            create_edge(conn, ids[0], ids[3], 'references', confidence=1.5)
            results["confidence_bounds"] = False
            failures.append("confidence_bounds: no ValueError raised for confidence=1.5")
        except ValueError:
            results["confidence_bounds"] = True

        # Valid confidence range.
        edge_id2 = create_edge(conn, ids[0], ids[3], 'refines', confidence=0.0)
        conn.commit()
        results["zero_confidence"] = edge_id2 > 0

        edge_id3 = create_edge(conn, ids[0], ids[4], 'causes', confidence=1.0)
        conn.commit()
        results["full_confidence"] = edge_id3 > 0

    finally:
        conn.close()
        Path(db_path).unlink(missing_ok=True)

    results["all_pass"] = len(failures) == 0
    results["failures"] = failures
    return results


def unit_test_multi_hop_expand() -> dict:
    """Test multi_hop_expand: 1-hop, 2-hop, 3-hop on a small test graph."""
    conn, db_path = _create_test_db()
    results: Dict[str, Any] = {}
    failures: List[str] = []

    try:
        ids = _insert_test_memories(conn, 8)

        # Build a chain: 0 -> 1 -> 2 -> 3 -> 4
        create_edge(conn, ids[0], ids[1], 'causes', confidence=0.8)
        create_edge(conn, ids[1], ids[2], 'refines', confidence=0.7)
        create_edge(conn, ids[2], ids[3], 'references', confidence=0.6)
        create_edge(conn, ids[3], ids[4], 'supersedes', confidence=0.9)
        # Branch: 1 -> 5
        create_edge(conn, ids[1], ids[5], 'references', confidence=0.5)
        # Disconnected: 6 -> 7 (should NOT appear in expansion from 0)
        create_edge(conn, ids[6], ids[7], 'causes', confidence=0.9)
        conn.commit()

        # 1-hop from 0: should find 1 (outgoing), and possibly others via incoming.
        hop1 = multi_hop_expand(conn, [ids[0]], depth=1, min_confidence=0.3)
        results["1_hop_finds_1"] = ids[1] in hop1
        results["1_hop_excludes_seed"] = ids[0] not in hop1
        results["1_hop_excludes_disconnected"] = ids[6] not in hop1 and ids[7] not in hop1
        if ids[1] not in hop1:
            failures.append(f"1_hop: expected {ids[1]} in {hop1}")

        # 2-hop from 0: should find 1, 2, 5 (1->2 outgoing, 1->5 outgoing).
        hop2 = multi_hop_expand(conn, [ids[0]], depth=2, min_confidence=0.3)
        results["2_hop_finds_1"] = ids[1] in hop2
        results["2_hop_finds_2"] = ids[2] in hop2
        results["2_hop_finds_5"] = ids[5] in hop2
        results["2_hop_excludes_disconnected"] = ids[6] not in hop2 and ids[7] not in hop2
        if ids[2] not in hop2:
            failures.append(f"2_hop: expected {ids[2]} in {hop2}")

        # 3-hop from 0: should additionally find 3.
        hop3 = multi_hop_expand(conn, [ids[0]], depth=3, min_confidence=0.3)
        results["3_hop_finds_3"] = ids[3] in hop3
        results["3_hop_finds_chain"] = all(ids[i] in hop3 for i in range(1, 4))

        # Empty seeds.
        empty = multi_hop_expand(conn, [], depth=2)
        results["empty_seeds"] = empty == []

        # High min_confidence filters out low-confidence edges.
        hop_high = multi_hop_expand(conn, [ids[0]], depth=3, min_confidence=0.8)
        # Only 0->1 (0.8) and 3->4 (0.9) have confidence >= 0.8.
        # From seed 0, outgoing: 0->1 (0.8 ok). From 1: outgoing 1->2 (0.7 filtered), 1->5 (0.5 filtered).
        results["high_confidence_filters"] = ids[2] not in hop_high and ids[5] not in hop_high
        results["high_confidence_keeps_0_to_1"] = ids[1] in hop_high

        # Incoming edges: 2-hop from 4 should find 3 (via incoming 3->4).
        hop_incoming = multi_hop_expand(conn, [ids[4]], depth=2, min_confidence=0.3)
        results["incoming_finds_3"] = ids[3] in hop_incoming
        if ids[3] not in hop_incoming:
            failures.append(f"incoming: expected {ids[3]} in {hop_incoming}")

    finally:
        conn.close()
        Path(db_path).unlink(missing_ok=True)

    results["all_pass"] = len(failures) == 0
    results["failures"] = failures
    return results


def unit_test_decay_confidence() -> dict:
    """Test decay_confidence: decay math, pruning threshold, age filter."""
    conn, db_path = _create_test_db()
    results: Dict[str, Any] = {}
    failures: List[str] = []

    try:
        ids = _insert_test_memories(conn, 4)

        # Create edges with different ages.
        # Edge 1: created 60 days ago, confidence 0.5, heuristic.
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO memory_edges (source_id, target_id, predicate, confidence, created_at, extraction_method)
               VALUES (?, ?, 'references', 0.5, ?, 'heuristic')""",
            (ids[0], ids[1], old_date),
        )

        # Edge 2: created today, confidence 0.9, manual.
        conn.execute(
            """INSERT INTO memory_edges (source_id, target_id, predicate, confidence, extraction_method)
               VALUES (?, ?, 'refines', 0.9, 'manual')""",
            (ids[1], ids[2]),
        )

        # Edge 3: created 45 days ago, confidence 0.15, heuristic (below threshold, should be pruned).
        old_date2 = (datetime.now(timezone.utc) - timedelta(days=45)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO memory_edges (source_id, target_id, predicate, confidence, created_at, extraction_method)
               VALUES (?, ?, 'causes', 0.15, ?, 'heuristic')""",
            (ids[2], ids[3], old_date2),
        )
        conn.commit()

        result = decay_confidence(conn, factor=0.99, min_threshold=0.2, max_age_days=30)
        conn.commit()

        results["returns_dict"] = isinstance(result, dict)
        results["has_decayed_count"] = 'decayed_count' in result
        results["has_pruned_count"] = 'pruned_count' in result
        results["has_remaining_count"] = 'remaining_count' in result

        # Edge 3 should be pruned (confidence 0.15 < 0.2 threshold, age 45 > 30 days).
        results["pruned_low_confidence_old"] = result['pruned_count'] >= 1
        if result['pruned_count'] < 1:
            failures.append(f"Expected at least 1 pruned edge, got {result['pruned_count']}")

        # Edge 2 (today) should not decay significantly.
        edge2_conf = conn.execute(
            "SELECT confidence FROM memory_edges WHERE source_id = ? AND target_id = ?",
            (ids[1], ids[2]),
        ).fetchone()
        if edge2_conf:
            results["recent_edge_not_decayed"] = edge2_conf[0] >= 0.89
        else:
            results["recent_edge_not_decayed"] = True  # Not decayed means not touched.

    finally:
        conn.close()
        Path(db_path).unlink(missing_ok=True)

    results["all_pass"] = len(failures) == 0
    results["failures"] = failures
    return results


def unit_test_heuristic_extraction() -> dict:
    """Test extract_edges_heuristic: tag overlap, content patterns."""
    conn, db_path = _create_test_db()
    results: Dict[str, Any] = {}
    failures: List[str] = []

    try:
        # Insert memories with specific content patterns.
        # Insert m2 first so we know its ID before creating m1's content.
        c2 = conn.execute(
            "INSERT INTO memories (content, summary, source_project) VALUES (?, ?, ?)",
            ("Original WSL 2.4 status documentation.", "WSL 2.4 status", "ew"),
        )
        m2 = c2.lastrowid

        c1 = conn.execute(
            "INSERT INTO memories (content, summary, source_project) VALUES (?, ?, ?)",
            (f"This update supersedes memory #{m2}. It replaces the old WSL version info.", "WSL upgrade", "ew"),
        )
        m1 = c1.lastrowid

        c3 = conn.execute(
            "INSERT INTO memories (content, summary, source_project) VALUES (?, ?, ?)",
            (f"This finding contradicts memory #{m2}. The version was actually different.", "Contradiction", "ew"),
        )
        m3 = c3.lastrowid

        c4 = conn.execute(
            "INSERT INTO memories (content, summary, source_project) VALUES (?, ?, ?)",
            (f"Because of memory #{m1}, we decided to update the pipeline. This led to changes.", "Causal chain", "ew"),
        )
        m4 = c4.lastrowid

        # Add shared tags for tag co-occurrence.
        for mid in [m1, m2, m3]:
            conn.execute("INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)", (mid, "wsl"))
            conn.execute("INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)", (mid, "infrastructure"))

        conn.commit()

        # Extract edges from m1 (has "supersedes" pattern + ref to #2).
        # L5-D: while _BELIEF_ENGINE_DEMOTE_STRONG holds (the default), the heuristic never authors supersedes or
        # contradicts -- a keyword match becomes a `references` edge that records what it was demoted from. These
        # rows expected the pre-L5-D predicates and failed on every tree since (measured on f275d09, round 1 L3).
        def _strong_or_demoted(proposals, predicate, target):
            if _BELIEF_ENGINE_DEMOTE_STRONG:
                return any(p['predicate'] == _DEMOTED_PREDICATE and p['target_id'] == target
                           and (p.get('metadata') or {}).get('demoted_from') == predicate for p in proposals)
            return any(p['predicate'] == predicate and p['target_id'] == target for p in proposals)

        proposals_m1 = extract_edges_heuristic(conn, m1)
        results["m1_finds_supersedes"] = _strong_or_demoted(proposals_m1, 'supersedes', m2)
        results["m1_has_proposals"] = len(proposals_m1) > 0
        if not results["m1_finds_supersedes"]:
            failures.append(f"m1: expected supersedes (or its L5-D demotion) to {m2}, got: "
                            f"{[(p['predicate'], (p.get('metadata') or {}).get('demoted_from')) for p in proposals_m1]}")

        # Extract edges from m3 (has "contradicts" pattern + ref to m2).
        proposals_m3 = extract_edges_heuristic(conn, m3)
        results["m3_finds_contradicts"] = _strong_or_demoted(proposals_m3, 'contradicts', m2)
        if not results["m3_finds_contradicts"]:
            failures.append(f"m3: expected contradicts (or its L5-D demotion) to {m2}")

        # Extract edges from m4 (has "because" pattern + ref to m1).
        proposals_m4 = extract_edges_heuristic(conn, m4)
        results["m4_finds_causes"] = any(
            p['predicate'] == 'causes' and p['target_id'] == m1 for p in proposals_m4
        )
        if not results["m4_finds_causes"]:
            failures.append(f"m4: expected causes edge to {m1}")

        # All proposals have required keys.
        for proposals in [proposals_m1, proposals_m3, proposals_m4]:
            for p in proposals:
                for key in ('source_id', 'target_id', 'predicate', 'confidence', 'extraction_method'):
                    if key not in p:
                        failures.append(f"Missing key {key} in proposal")
                        break

        results["all_proposals_have_required_keys"] = not any(
            "Missing key" in f for f in failures
        )

    finally:
        conn.close()
        Path(db_path).unlink(missing_ok=True)

    results["all_pass"] = len(failures) == 0
    results["failures"] = failures
    return results


def unit_test_get_edges() -> dict:
    """Test get_edges: direction filtering, predicate filtering."""
    conn, db_path = _create_test_db()
    results: Dict[str, Any] = {}
    failures: List[str] = []

    try:
        ids = _insert_test_memories(conn, 4)

        create_edge(conn, ids[0], ids[1], 'references', confidence=0.7)
        create_edge(conn, ids[0], ids[2], 'causes', confidence=0.8)
        create_edge(conn, ids[2], ids[0], 'refines', confidence=0.6)
        conn.commit()

        # Both directions.
        all_edges = get_edges(conn, ids[0], direction='both')
        results["both_direction_count"] = len(all_edges) == 3

        # Outgoing only.
        outgoing = get_edges(conn, ids[0], direction='outgoing')
        results["outgoing_count"] = len(outgoing) == 2

        # Incoming only.
        incoming = get_edges(conn, ids[0], direction='incoming')
        results["incoming_count"] = len(incoming) == 1

        # Predicate filter.
        causes_only = get_edges(conn, ids[0], direction='both', predicate='causes')
        results["predicate_filter"] = len(causes_only) == 1 and causes_only[0]['predicate'] == 'causes'

        # current_only filter.
        conn.execute(
            "UPDATE memory_edges SET valid_until = datetime('now') WHERE source_id = ? AND target_id = ? AND predicate = 'references'",
            (ids[0], ids[1]),
        )
        conn.commit()
        current = get_edges(conn, ids[0], direction='outgoing', current_only=True)
        results["current_only_excludes_expired"] = len(current) == 1 and current[0]['predicate'] == 'causes'

    finally:
        conn.close()
        Path(db_path).unlink(missing_ok=True)

    results["all_pass"] = len(failures) == 0
    results["failures"] = failures
    return results


# ============================================================
# CLI test runners
# ============================================================

def run_unit_tests() -> dict:
    """Run all unit tests. Returns combined results."""
    all_results = {}
    test_funcs = [
        ("create_edge", unit_test_create_edge),
        ("multi_hop_expand", unit_test_multi_hop_expand),
        ("decay_confidence", unit_test_decay_confidence),
        ("heuristic_extraction", unit_test_heuristic_extraction),
        ("get_edges", unit_test_get_edges),
    ]

    overall_pass = True
    for name, func in test_funcs:
        try:
            result = func()
            all_results[name] = result
            if not result.get("all_pass"):
                overall_pass = False
                print(f"  FAIL: {name} -- {result.get('failures', [])}", file=sys.stderr)
            else:
                print(f"  PASS: {name}", file=sys.stderr)
        except Exception as e:
            all_results[name] = {"all_pass": False, "error": str(e)}
            overall_pass = False
            print(f"  ERROR: {name} -- {e}", file=sys.stderr)

    all_results["overall_pass"] = overall_pass
    return all_results


def run_test_multi_hop(db_path: str) -> dict:
    """Run multi-hop expansion test against a real DB with edges populated."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    # Check if memory_edges exists.
    if not is_temporal_edges_migration_applied(conn):
        conn.close()
        return {"status": "error", "message": "memory_edges table not found. Apply migration first."}

    edge_count = conn.execute("SELECT COUNT(*) FROM memory_edges").fetchone()[0]
    if edge_count == 0:
        conn.close()
        return {"status": "error", "message": "No edges in DB. Run heuristic extraction first."}

    # Pick some seed IDs that have edges.
    seed_rows = conn.execute(
        "SELECT DISTINCT source_id FROM memory_edges LIMIT 5"
    ).fetchall()
    seed_ids = [r[0] for r in seed_rows]

    results = {
        "edge_count": edge_count,
        "seed_ids": seed_ids,
        "expansions": {},
    }

    for depth in [1, 2, 3]:
        t0 = time.time()
        expanded = multi_hop_expand(conn, seed_ids, depth=depth, min_confidence=0.3)
        elapsed_ms = int((time.time() - t0) * 1000)
        results["expansions"][f"depth_{depth}"] = {
            "expanded_count": len(expanded),
            "expanded_ids": expanded[:20],
            "elapsed_ms": elapsed_ms,
        }

    conn.close()
    return results


def _load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """Attempt to load sqlite-vec extension. Returns True on success."""
    try:
        conn.execute("SELECT vec_version()")
        return True
    except sqlite3.OperationalError:
        pass
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception as e:
        logger.warning("sqlite-vec not available: %s -- cosine-similarity heuristic disabled", e)
        return False


def run_heuristic_extraction_batch(db_path: str, sample_size: int = 100) -> dict:
    """Run heuristic extraction on a sample of memories and insert edges."""
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA foreign_keys = ON")
    _load_sqlite_vec(conn)  # Best-effort; Tier 3 silently skips if unavailable.

    if not is_temporal_edges_migration_applied(conn):
        conn.close()
        return {"status": "error", "message": "memory_edges table not found"}

    # Select diverse sample: mix of categories + projects + recent + high-importance.
    rows = conn.execute(
        """SELECT id FROM memories WHERE is_archived = 0
           ORDER BY importance DESC, created_at DESC
           LIMIT ?""",
        (sample_size,)
    ).fetchall()
    sample_ids = [r[0] for r in rows]

    total_proposals = 0
    total_inserted = 0
    predicate_counts: Dict[str, int] = {}
    errors = 0

    for mem_id in sample_ids:
        try:
            proposals = extract_edges_heuristic(conn, mem_id, top_k=10)
            total_proposals += len(proposals)

            for p in proposals:
                try:
                    create_edge(
                        conn,
                        source_id=p['source_id'],
                        target_id=p['target_id'],
                        predicate=p['predicate'],
                        confidence=p['confidence'],
                        extraction_method=p['extraction_method'],
                        metadata=p.get('metadata'),
                    )
                    total_inserted += 1
                    predicate_counts[p['predicate']] = predicate_counts.get(p['predicate'], 0) + 1
                except (sqlite3.IntegrityError, ValueError):
                    pass  # Duplicate or invalid -- skip.
        except Exception as e:
            errors += 1
            logger.warning("Heuristic extraction error for memory %d: %s", mem_id, e)

    conn.commit()

    # Gather graph stats.
    total_edges = conn.execute("SELECT COUNT(*) FROM memory_edges").fetchone()[0]
    unique_sources = conn.execute("SELECT COUNT(DISTINCT source_id) FROM memory_edges").fetchone()[0]
    unique_targets = conn.execute("SELECT COUNT(DISTINCT target_id) FROM memory_edges").fetchone()[0]

    conn.close()

    return {
        "status": "ok",
        "sample_size": len(sample_ids),
        "total_proposals": total_proposals,
        "total_inserted": total_inserted,
        "total_edges_in_db": total_edges,
        "unique_sources": unique_sources,
        "unique_targets": unique_targets,
        "predicate_counts": predicate_counts,
        "errors": errors,
    }


# ============================================================
# Main (CLI)
# ============================================================

def _repair_cli(argv: List[str]) -> int:
    """`temporal_edges.py repair-decay <db> [--apply | --undo]` -- dry run by default. Prints one JSON report."""
    import argparse
    ap = argparse.ArgumentParser(prog="temporal_edges.py repair-decay",
                                 description="One-time repair of the edge confidences the per-run compounding "
                                             "lowered (round 2). Dry run unless --apply; --undo reverses it.")
    ap.add_argument("db")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="write the repair (one transaction)")
    g.add_argument("--undo", action="store_true", help="put back every repaired edge's pre-repair confidence")
    a = ap.parse_args(argv)
    if not Path(a.db).is_file():
        print(json.dumps({"status": "error", "error": "no such brain: %s" % a.db}))
        return 2
    conn = sqlite3.connect(a.db, timeout=60.0)
    try:
        conn.execute("BEGIN IMMEDIATE")
        rep = repair_decay(conn, apply=a.apply, undo=a.undo)
        if (a.apply and rep.get('status') == 'applied') or (a.undo and rep.get('status') == 'undone'):
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()
    print(json.dumps(rep, indent=1))
    return 1 if rep.get('status') in ('refused', 'error', 'nothing-undone') else 0


if __name__ == "__main__":
    import argparse

    if len(sys.argv) > 1 and sys.argv[1] == "repair-decay":
        sys.exit(_repair_cli(sys.argv[2:]))

    parser = argparse.ArgumentParser(description="Temporal Edges Module (L2 Component 4)")
    parser.add_argument("--unit-tests", action="store_true", help="Run all unit tests")
    parser.add_argument("--test-multi-hop", type=str, metavar="DB_PATH",
                        help="Run multi-hop expansion test against a DB")
    parser.add_argument("--extract-heuristic", type=str, metavar="DB_PATH",
                        help="Run heuristic extraction batch on a DB")
    parser.add_argument("--extract-sample-size", type=int, default=100,
                        help="Sample size for heuristic extraction (default: 100)")

    args = parser.parse_args()

    if args.unit_tests:
        print("Running unit tests...", file=sys.stderr)
        results = run_unit_tests()
        import json as _json
        print(_json.dumps(results, indent=2))
        sys.exit(0 if results["overall_pass"] else 1)

    elif args.test_multi_hop:
        results = run_test_multi_hop(args.test_multi_hop)
        import json as _json
        print(_json.dumps(results, indent=2))

    elif args.extract_heuristic:
        results = run_heuristic_extraction_batch(args.extract_heuristic, args.extract_sample_size)
        import json as _json
        print(_json.dumps(results, indent=2))

    else:
        parser.print_help()
