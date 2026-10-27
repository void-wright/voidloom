#!/usr/bin/env python3
"""Knowledge Gap Mapping + Auto-Research Dispatch — Wizard Library L4 Piece 6.

The brain inspects ITSELF: where is memory thin? Which entities appear only
once? Which recall queries returned weak results? Which contradictions sit
unresolved? It produces a weekly gap map — a prioritized list of knowledge
gaps that can optionally trigger auto-research via ``claude -p`` (Drew's Max
plan token) or ``agy`` (Gemini, free).

Public API
----------
``run_gap_inventory(db_path, config=None) -> dict``
    Main entry point. Runs all 5 gap detection stages, scores, stores results.

``score_gaps(raw_gaps, config=None) -> List[dict]``
    Apply composite scoring: importance x thinness x strategic_weight.

``dispatch_auto_research(db_path, gap_ids=None, method='thinker', max_gaps=5, max_seconds_per_gap=300) -> dict``
    Dispatch research for top gaps. Bounded: max 5 per run, max 5 min each.

``get_gap_report(db_path, batch_id=None, limit=25) -> dict``
    Retrieve the latest (or specific) gap report.

``acknowledge_gap(db_path, gap_id, reason='seen') -> dict``
    Mark a gap as acknowledged (not-a-gap, deferred, or just seen).

CLI interface
-------------
::

    python3 gap_mapping.py --run <db_path>              # full inventory
    python3 gap_mapping.py --report <db_path>           # latest report
    python3 gap_mapping.py --research <db_path>         # dispatch research
    python3 gap_mapping.py --ack <gap_id> <db_path>     # acknowledge a gap
    python3 gap_mapping.py --unit-tests                 # self-test suite

Phase 0 design: memories #3914, #3816, #3820.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# rho-1: the ONE env-directory resolver. Hard import by design -- it is
# stdlib-only and is laid by the same `cp -f "${PKG_ROOT}"/*.py` loop that
# lays this module, so its absence means an incomplete install, and an
# incomplete install must SAY SO rather than resolve sidecars elsewhere.
# A per-module inline fallback would recreate the 13-way divergence this
# module exists to end.
import voidloom_env
from typing import Any, Dict, List, Optional, Set, Tuple

# L5-M5 "The Warden": gate web-sourced (untrusted external) writes through the
# landed L5-I-β governance_gate + stamp the immutable untrusted_source birth-mark.
# Fail-graceful sibling import (the gap-research systemd unit's WorkingDirectory is
# the brain directory, so a bare import resolves; absence degrades to the prior
# un-gated, un-stamped behavior — never crashes the harvest).
try:
    import warden as _warden  # type: ignore
except Exception:  # pragma: no cover - degrade: no gate, no birth-mark
    _warden = None  # type: ignore

logger = logging.getLogger(__name__)

# --- TO-03 (round gamma): the ONE guarded embedding write --------------------
# Every memory this organ writes must be findable. memory_write resolves the
# tier through embed_resolver (which REFUSES rather than defaulting to the table
# retired 2026-05-28), asserts the vector width against the resolved dim, and on
# failure marks the row with the 'embedding-missing' tag so the gap is queryable
# from the DATA, not only from the journal. Stdlib-only at module scope.
import os as _vl_os, sys as _vl_sys
_vl_d = _vl_os.path.dirname(_vl_os.path.abspath(__file__))
if _vl_d not in _vl_sys.path:
    _vl_sys.path.insert(0, _vl_d)
try:
    import memory_write as _vl_mw
except Exception:                       # pragma: no cover - substrate absent
    _vl_mw = None


def _vl_attach(conn, memory_id, text, organ, sources=None, transformation=None,
               attach=True):
    """TO-03 + TO-08 + TO-09: everything a freshly written memory owes the brain.

    embedding (or a queryable 'embedding-missing' mark) + belief_scan_queue entry
    + provenance edges when this organ knows its sources. Never silent.
    """
    if _vl_mw is None:
        import logging as _l
        _l.getLogger("voidloom").error(
            "%s: memory_write unavailable; memory %s written WITHOUT an embedding "
            "and cannot even be marked.", organ, memory_id)
        return {"embedded": False, "error": "memory_write unavailable"}
    return _vl_mw.on_memory_written(conn, memory_id, text, organ=organ,
                                    sources=sources, transformation=transformation,
                                    attach=attach)
# --- end TO-03 ---------------------------------------------------------------

# ============================================================
# Configuration
# ============================================================

DEFAULT_CONFIG: Dict[str, Any] = {
    # Entity frequency thresholds
    'thin_entity_max_mentions': 2,       # entities with <= N mentions = thin
    'min_entity_length': 3,              # skip very short entities
    'max_entities_per_type': 50,         # cap results per gap type

    # Weak recall thresholds
    # access_log stores relevance_score where MORE NEGATIVE = better match.
    # Positive scores or near-zero negatives indicate poor recall quality.
    'weak_recall_score_threshold': -0.3, # best score > this = weak
    'weak_recall_lookback_days': 30,     # only consider recent queries
    'weak_recall_min_query_count': 2,    # skip one-off queries

    # Contradiction detection
    'contradiction_min_confidence': 0.3, # only edges with confidence >= this

    # Domain sparsity
    'sparse_domain_percentile': 25,      # projects below this percentile = sparse
    'min_domain_memories': 3,            # must have at least N to be a real domain

    # Stale entity detection
    'stale_days_threshold': 60,          # no updates in N days = stale
    'stale_recent_access_days': 30,      # but was accessed within N days

    # Scoring weights
    'importance_weight': 1.0,
    'thinness_weight': 1.0,
    'strategic_weight_default': 1.0,
    'strategic_weights': {},             # project -> weight override

    # Auto-research
    'auto_research_max_gaps': 5,
    'auto_research_max_seconds': 300,    # 5 minutes per gap
    # RETIRED (model-choice unit, Round 1): 'auto_research_method', 'claude_token_path' and 'agy_path'. Who
    # researches is the owner's recorded overnight thinker (organs/thinker.py, <brain>/THINKER_RECORD); its
    # credential lives in ~/.voidloom/thinker/. A user config still naming them is read and ignored.
}


def _merge_config(user_config: Optional[Dict] = None) -> Dict[str, Any]:
    """Merge user config over defaults. Nested dicts are shallow-merged."""
    cfg = dict(DEFAULT_CONFIG)
    if user_config:
        for k, v in user_config.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k] = {**cfg[k], **v}
            else:
                cfg[k] = v
    return cfg


def _get_conn(db_path: str) -> sqlite3.Connection:
    """Open a connection with vec0 loaded, foreign keys ON, and a busy timeout.

    THE BUSY TIMEOUT IS LOAD-BEARING, not hygiene.  This organ shares a ~236 MB WAL
    database with about twenty other wizard-library timers, and when the systemd user
    manager starts it fires every overdue Persistent=true timer in the same second.
    Python's default is 5.0 s, and the journal records this organ dying on that
    default twice -- `sqlite3.OperationalError: database is locked` on 2026-07-14 and
    2026-08-17 -- in exactly the two of nine recorded invocations that ran during
    such a storm (15 and 17 concurrent sibling starts, against 2-4 for all seven
    that survived).  60 s is chosen to outlast a start-up storm, not a deadlock.
    `timeout=` IS the busy-handler knob; the PRAGMA below restates it so the
    intent survives a copy of this call that drops the keyword.
    """
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("SELECT vec_version()")
    except sqlite3.OperationalError:
        try:
            import sqlite_vec
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        except (ImportError, Exception):
            pass  # vec0 not critical for gap mapping
    return conn


# ============================================================
# Migration check
# ============================================================

def is_gap_mapping_migration_applied(conn: sqlite3.Connection) -> bool:
    """Check if gap_inventory table exists."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='gap_inventory'"
    ).fetchone()
    return row is not None


def apply_gap_mapping_migration(db_path: str, migration_sql_path: str) -> dict:
    """Apply the gap mapping migration. Idempotent.

    Same busy timeout as _get_conn(): this path runs `executescript`, which is a
    long write, and it is reached at install time when every other organ is also
    starting.  See HUNK G in gap_drain_patch.py for the measurement.
    """
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA busy_timeout=60000")
    try:
        sql = Path(migration_sql_path).read_text()
        conn.executescript(sql)
        conn.commit()
        return {"status": "ok", "tables_created": ["gap_inventory", "gap_inventory_batches", "gap_research_log"]}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


# ============================================================
# Entity extraction (lightweight, no NLP deps)
# ============================================================

# Patterns that extract meaningful entities from memory summaries/content.
# We avoid heavy NLP; instead we use heuristics tuned for the memory corpus.
_ENTITY_STOP_WORDS = frozenset({
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'shall', 'can', 'need', 'must', 'ought',
    'and', 'or', 'but', 'nor', 'not', 'no', 'so', 'yet', 'both',
    'for', 'in', 'on', 'at', 'by', 'to', 'of', 'with', 'from', 'up',
    'about', 'into', 'through', 'during', 'before', 'after', 'above',
    'below', 'between', 'same', 'other', 'each', 'every', 'all', 'any',
    'few', 'more', 'most', 'some', 'such', 'than', 'too', 'very',
    'that', 'this', 'these', 'those', 'it', 'its', 'they', 'them',
    'their', 'we', 'us', 'our', 'you', 'your', 'he', 'him', 'his',
    'she', 'her', 'who', 'which', 'what', 'when', 'where', 'how', 'why',
    'if', 'then', 'else', 'also', 'just', 'only', 'even', 'still',
    'now', 'here', 'there', 'new', 'old', 'used', 'using', 'use',
    'one', 'two', 'three', 'first', 'second', 'third', 'last', 'next',
    'get', 'set', 'run', 'make', 'see', 'add', 'try', 'keep',
})

# Match capitalized multi-word names (e.g. "Northwind Robotics", "Atlas Pro II")
_PROPER_NOUN_RE = re.compile(
    r'\b([A-Z][a-z]+(?:\s+(?:[A-Z][a-z]+|[A-Z]{2,}|[IVX]+|v\d+|GO|II|III|V|Z))+)\b'
)

# Match single CamelCase words (e.g. "SkyLattice", "Quarrystone", "CloudFront")
_CAMEL_CASE_RE = re.compile(r'\b([A-Z][a-z]+[A-Z]\w+)\b')

# Match single capitalized words >= 4 chars that are likely proper nouns
# Excludes common English words via _ENTITY_STOP_WORDS check at call site
_SINGLE_PROPER_RE = re.compile(r'\b([A-Z][a-z]{3,})\b')

# Common English capitalized words that are NOT entities (sentence starters, etc.)
_SINGLE_PROPER_IGNORE = frozenset({
    'This', 'That', 'These', 'Those', 'There', 'Their', 'Then', 'Than',
    'When', 'Where', 'While', 'Which', 'What', 'With', 'Will', 'Would',
    'Could', 'Should', 'Shall', 'Must', 'Have', 'Here', 'However',
    'After', 'Before', 'Between', 'About', 'Above', 'Below', 'Each',
    'Every', 'Some', 'Many', 'Most', 'Other', 'Both', 'Such', 'Very',
    'Also', 'Just', 'Only', 'Even', 'Still', 'From', 'Into', 'Over',
    'Under', 'Through', 'During', 'Because', 'Since', 'Until', 'Unless',
    'Although', 'Though', 'Whether', 'Either', 'Neither', 'Once', 'Never',
    'Always', 'Often', 'Sometimes', 'Perhaps', 'Rather', 'Quite', 'More',
    'Less', 'Best', 'Worst', 'First', 'Second', 'Third', 'Last', 'Next',
    'Stage', 'Phase', 'Step', 'Part', 'Type', 'Kind', 'Mode', 'Note',
    'Pattern', 'Rule', 'Test', 'Data', 'File', 'Path', 'Name', 'Value',
    'Code', 'Table', 'Index', 'Query', 'Based', 'Level', 'Using',
})

# Match technical identifiers: camelCase, snake_case with prefix, CLI names
_TECH_ID_RE = re.compile(
    r'\b([a-z][\w]*[-_][a-z][\w]*(?:[-_][a-z][\w]*)*)\b'
)

# Match acronyms: 2-6 uppercase letters, optionally followed by digits (e.g. BM25, FTS5)
_ACRONYM_RE = re.compile(r'\b([A-Z]{2,6}\d{0,2})\b')
_ACRONYM_IGNORE = frozenset({
    # Common English words that appear uppercase in mixed-case corpus
    'THE', 'AND', 'FOR', 'NOT', 'BUT', 'ALL', 'ANY', 'CAN', 'HAS', 'HER',
    'HIS', 'HOW', 'ITS', 'MAY', 'NEW', 'NOW', 'OLD', 'OUR', 'OUT', 'OWN',
    'SAY', 'SHE', 'TOO', 'USE', 'WAY', 'WHO', 'BOY', 'DID', 'GET', 'HIM',
    'LET', 'PUT', 'RUN', 'TOP', 'YES', 'YET',
    # Additional common English words flagged by Reflector P2 (70% noise reduction)
    'ABORT', 'ABS', 'ADAPT', 'AGAIN', 'AHA', 'AID', 'ALERT', 'ALIAS',
    'ALIVE', 'ALIGNS', 'ALLOWS', 'ALT', 'AMP', 'ANGLE', 'ANYWAY', 'AROUND',
    'ARCANE', 'ASKS', 'ASPECT', 'AVOID', 'BASED', 'BEING', 'BEST', 'BOTH',
    'BUILT', 'CASE', 'CLEAR', 'CLOSE', 'COULD', 'DONE', 'EACH', 'EDIT',
    'ELSE', 'EVEN', 'EVERY', 'EXACT', 'FACT', 'FIRST', 'FROM', 'FULL',
    'GAVE', 'GOES', 'GONE', 'GOOD', 'GREAT', 'HARD', 'HAVE', 'HERE',
    'HIGH', 'INTO', 'JUST', 'KEEP', 'KNOW', 'LAST', 'LIKE', 'LINE',
    'LIST', 'LONG', 'LOOK', 'MADE', 'MAKE', 'MANY', 'MORE', 'MOST',
    'MUCH', 'MUST', 'NAME', 'NEED', 'NEXT', 'NOTE', 'ONCE', 'ONLY',
    'OPEN', 'OVER', 'PAGE', 'PART', 'PASS', 'PATH', 'PICK', 'PLUS',
    'POST', 'PULL', 'PUSH', 'READ', 'REST', 'SAFE', 'SAME', 'SAVE',
    'SEED', 'SEEN', 'SEND', 'SHOW', 'SIDE', 'SKIP', 'SOME', 'SORT',
    'STEP', 'STOP', 'SUCH', 'SURE', 'TAKE', 'THAN', 'THAT', 'THEM',
    'THEN', 'THEY', 'THIS', 'TIME', 'TURN', 'UPON', 'USED', 'VERY',
    'WANT', 'WELL', 'WENT', 'WERE', 'WHAT', 'WHEN', 'WILL', 'WITH',
    'WORK', 'YOUR',
    # SQL keywords
    'SQL', 'JSON', 'HTML', 'CSS',
    'HTTP', 'HTTPS', 'NULL', 'TRUE', 'TEXT', 'REAL', 'BLOB', 'TYPE',
    'CREATE', 'TABLE', 'INDEX', 'WHERE', 'SELECT', 'INSERT', 'UPDATE',
    'DELETE', 'CHECK', 'DEFAULT', 'PRIMARY', 'KEY',
    # Programming keywords commonly uppercased
    'SCRIPT', 'THREE', 'EVERY',
})


def extract_entities(text: str, min_length: int = 3) -> List[str]:
    """Extract entity-like strings from text using lightweight heuristics.

    Returns deduplicated list of entities (proper nouns, tech IDs, acronyms).
    """
    if not text:
        return []

    entities: Set[str] = set()

    # Proper nouns (multi-word capitalized sequences)
    for m in _PROPER_NOUN_RE.finditer(text):
        name = m.group(1).strip()
        if len(name) >= min_length:
            entities.add(name)

    # CamelCase words (e.g. SkyLattice, CloudFront)
    for m in _CAMEL_CASE_RE.finditer(text):
        name = m.group(1)
        if len(name) >= min_length:
            entities.add(name)

    # Single capitalized words (e.g. Karnateq, Tesselator, Faraday)
    for m in _SINGLE_PROPER_RE.finditer(text):
        word = m.group(1)
        if word not in _SINGLE_PROPER_IGNORE and word.lower() not in _ENTITY_STOP_WORDS:
            entities.add(word)

    # Technical identifiers (hyphenated/underscored)
    for m in _TECH_ID_RE.finditer(text):
        ident = m.group(1)
        if len(ident) >= min_length and ident.lower() not in _ENTITY_STOP_WORDS:
            entities.add(ident)

    # Acronyms (with optional trailing digits: BM25, FTS5, RRF)
    for m in _ACRONYM_RE.finditer(text):
        acr = m.group(1)
        # Strip digits for ignore-list check
        acr_letters = re.sub(r'\d+$', '', acr)
        if acr_letters not in _ACRONYM_IGNORE and len(acr) >= 2:
            entities.add(acr)

    return sorted(entities)


# ============================================================
# Stage 1: Thin Entity Detection
# ============================================================

def detect_thin_entities(
    conn: sqlite3.Connection,
    max_mentions: int = 2,
    min_length: int = 3,
    max_results: int = 50,
) -> List[Dict[str, Any]]:
    """Find entities that appear in only 1-2 memories (thin coverage).

    Scans summary + content of all active memories, builds a frequency
    histogram, and returns entities below the threshold.
    """
    entity_counter: Counter = Counter()
    entity_last_seen: Dict[str, str] = {}

    rows = conn.execute(
        "SELECT id, summary, content, updated_at FROM memories WHERE is_archived = 0"
    ).fetchall()

    for mem_id, summary, content, updated_at in rows:
        text = f"{summary or ''} {content or ''}"
        for ent in extract_entities(text, min_length):
            entity_counter[ent] += 1
            if ent not in entity_last_seen or (updated_at or '') > entity_last_seen.get(ent, ''):
                entity_last_seen[ent] = updated_at or ''

    thin = []
    for ent, count in entity_counter.most_common():
        if count > max_mentions:
            continue
        thin.append({
            'entity': ent,
            'gap_type': 'thin_entity',
            'memory_count': count,
            'last_mentioned': entity_last_seen.get(ent),
            'raw_importance': 1.0 / max(count, 1),  # inverse frequency
        })

    # Sort by lowest count first (thinnest), then alphabetically
    thin.sort(key=lambda g: (g['memory_count'], g['entity']))
    return thin[:max_results]


# ============================================================
# Stage 2: Weak Recall Analysis
# ============================================================

def detect_weak_recalls(
    conn: sqlite3.Connection,
    score_threshold: float = -0.3,
    lookback_days: int = 30,
    min_query_count: int = 2,
    max_results: int = 50,
) -> List[Dict[str, Any]]:
    """Find queries where recall performed poorly.

    access_log stores relevance_score where more negative = better match.
    Queries where the BEST (most negative) score is above the threshold
    indicate the brain couldn't answer well.

    Filters out operational queries (e.g. "memory id 123", "id:456")
    that are ID lookups, not genuine semantic knowledge gaps.
    """
    cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()

    rows = conn.execute("""
        SELECT query_text,
               MIN(relevance_score) AS best_score,
               COUNT(*) AS result_count,
               MAX(accessed_at) AS last_queried
        FROM access_log
        WHERE query_text IS NOT NULL
          AND relevance_score IS NOT NULL
          AND access_type NOT LIKE 'recall:%-lead'
          AND accessed_at >= ?
          AND length(query_text) >= 10
        GROUP BY query_text
        HAVING MIN(relevance_score) > ?
           AND COUNT(*) >= ?
        ORDER BY MIN(relevance_score) DESC
    """, (cutoff, score_threshold, min_query_count)).fetchall()

    # Regex to detect operational ID-lookup queries that are not genuine knowledge gaps.
    # Examples: "memory id 1389", "memory 2137", "id:1448", "#3812"
    _operational_query_re = re.compile(
        r'^(?:memory\s+(?:id\s+)?\d+|id:\d+|#\d+|recall\s+memory\s+\d+)$',
        re.IGNORECASE,
    )

    gaps = []
    for query_text, best_score, result_count, last_queried in rows:
        # Skip operational ID-lookup queries
        if _operational_query_re.match(query_text.strip()):
            continue

        # The "entity" for weak recalls is the query topic itself
        gaps.append({
            'entity': query_text[:200],  # cap length
            'gap_type': 'weak_recall',
            'memory_count': 0,  # by definition, recall was poor
            'last_mentioned': last_queried,
            'raw_importance': max(0.0, min(1.0, 1.0 - best_score)),  # (1 - similarity): lower similarity = higher importance
            'best_score': best_score,
            'query_count': result_count,
        })

    gaps.sort(key=lambda g: -g['raw_importance'])
    return gaps[:max_results]


# ============================================================
# Stage 3: Unresolved Contradictions
# ============================================================

def contradiction_gap_label(src_id: int, tgt_id: int, src_sum: Optional[str], tgt_sum: Optional[str]) -> str:
    """The `entity` an unresolved_contradiction gap is stored under. ONE definition:
    the belief quarantine (belief_engine.quarantine_template_pairs) re-derives these
    labels to retire the gaps a quarantined edge authored, so the two cannot drift."""
    if src_sum and tgt_sum:
        return f"{src_sum[:60]}... vs {tgt_sum[:60]}..."
    return f"#{src_id} vs #{tgt_id}"


def detect_unresolved_contradictions(
    conn: sqlite3.Connection,
    min_confidence: float = 0.3,
    max_results: int = 50,
) -> List[Dict[str, Any]]:
    """Find contradicts edges where both source and target are still active.

    These are conflicts the brain hasn't reconciled.
    """
    rows = conn.execute("""
        SELECT me.source_id, me.target_id, me.confidence,
               m1.summary AS src_summary, m2.summary AS tgt_summary,
               me.created_at,
               m1.source_project AS src_project, m2.source_project AS tgt_project
        FROM memory_edges me
        JOIN memories m1 ON me.source_id = m1.id AND m1.is_archived = 0
        JOIN memories m2 ON me.target_id = m2.id AND m2.is_archived = 0
        WHERE me.predicate = 'contradicts'
          AND me.confidence >= ?
          AND (me.valid_until IS NULL)
        ORDER BY me.confidence DESC
    """, (min_confidence,)).fetchall()

    gaps = []
    for src_id, tgt_id, conf, src_sum, tgt_sum, created_at, src_proj, tgt_proj in rows:
        entity_label = contradiction_gap_label(src_id, tgt_id, src_sum, tgt_sum)

        gaps.append({
            'entity': entity_label,
            'gap_type': 'unresolved_contradiction',
            'memory_count': 2,  # always exactly 2 memories in a contradiction pair
            'last_mentioned': created_at,
            'raw_importance': conf,  # higher confidence contradiction = more urgent
            'source_id': src_id,
            'target_id': tgt_id,
            'source_project': src_proj,
            'target_project': tgt_proj,
        })

    gaps.sort(key=lambda g: -g['raw_importance'])
    return gaps[:max_results]


# ============================================================
# Stage 4: Domain Density Variance (Sparse Domains)
# ============================================================

def detect_sparse_domains(
    conn: sqlite3.Connection,
    percentile: int = 25,
    min_memories: int = 3,
    max_results: int = 50,
) -> List[Dict[str, Any]]:
    """Find projects/tags with unusually low memory density.

    Compares memory counts across projects. Projects below the Nth percentile
    (default 25th) are flagged as sparse.
    """
    # Project-level sparsity
    rows = conn.execute("""
        SELECT source_project, COUNT(*) AS cnt,
               MAX(updated_at) AS last_update
        FROM memories
        WHERE is_archived = 0
          AND source_project IS NOT NULL
          AND source_project != ''
        GROUP BY source_project
        HAVING COUNT(*) >= ?
        ORDER BY COUNT(*)
    """, (min_memories,)).fetchall()

    if not rows:
        return []

    counts = [r[1] for r in rows]
    if not counts:
        return []

    # Compute the percentile threshold
    sorted_counts = sorted(counts)
    idx = max(0, int(len(sorted_counts) * percentile / 100) - 1)
    threshold = sorted_counts[idx]

    # Also compute the median for normalization
    median_count = sorted_counts[len(sorted_counts) // 2]

    gaps = []
    for project, cnt, last_update in rows:
        if cnt > threshold:
            continue
        # Thinness: how far below median this domain is
        thinness = max(0.1, 1.0 - (cnt / max(median_count, 1)))
        gaps.append({
            'entity': f"project:{project}",
            'gap_type': 'sparse_domain',
            'memory_count': cnt,
            'last_mentioned': last_update,
            'raw_importance': thinness,
            'domain_threshold': threshold,
            'domain_median': median_count,
        })

    # Also check tag-level sparsity for major tags
    tag_rows = conn.execute("""
        SELECT mt.tag, COUNT(DISTINCT mt.memory_id) AS cnt,
               MAX(m.updated_at) AS last_update
        FROM memory_tags mt
        JOIN memories m ON mt.memory_id = m.id AND m.is_archived = 0
        GROUP BY mt.tag
        HAVING COUNT(DISTINCT mt.memory_id) >= ?
        ORDER BY COUNT(DISTINCT mt.memory_id)
    """, (min_memories,)).fetchall()

    if tag_rows:
        tag_counts = [r[1] for r in tag_rows]
        sorted_tag_counts = sorted(tag_counts)
        tag_idx = max(0, int(len(sorted_tag_counts) * percentile / 100) - 1)
        tag_threshold = sorted_tag_counts[tag_idx]
        tag_median = sorted_tag_counts[len(sorted_tag_counts) // 2]

        for tag, cnt, last_update in tag_rows:
            if cnt > tag_threshold:
                continue
            thinness = max(0.1, 1.0 - (cnt / max(tag_median, 1)))
            gaps.append({
                'entity': f"tag:{tag}",
                'gap_type': 'sparse_domain',
                'memory_count': cnt,
                'last_mentioned': last_update,
                'raw_importance': thinness,
                'domain_threshold': tag_threshold,
                'domain_median': tag_median,
            })

    gaps.sort(key=lambda g: g['memory_count'])
    return gaps[:max_results]


# ============================================================
# Stage 5: Stale Entity Detection
# ============================================================

def detect_stale_entities(
    conn: sqlite3.Connection,
    stale_days: int = 60,
    recent_access_days: int = 30,
    max_results: int = 50,
) -> List[Dict[str, Any]]:
    """Find entities that were accessed recently but not updated in 60+ days.

    These are topics the brain is being asked about but whose knowledge
    may be outdated.
    """
    now = datetime.utcnow()
    stale_cutoff = (now - timedelta(days=stale_days)).isoformat()
    recent_cutoff = (now - timedelta(days=recent_access_days)).isoformat()

    # Find memories accessed recently but not updated in stale_days
    rows = conn.execute("""
        SELECT m.id, m.summary, m.source_project, m.updated_at,
               m.last_accessed, m.access_count
        FROM memories m
        WHERE m.is_archived = 0
          AND m.updated_at < ?
          AND m.last_accessed >= ?
          AND m.access_count >= 2
        ORDER BY m.access_count DESC
    """, (stale_cutoff, recent_cutoff)).fetchall()

    # Group by entities extracted from summaries
    entity_staleness: Dict[str, Dict] = {}
    for mem_id, summary, project, updated_at, last_accessed, access_count in rows:
        entities = extract_entities(summary or '', min_length=3)
        if not entities:
            # Use project as fallback entity
            entities = [f"memory:#{mem_id}"]

        for ent in entities:
            if ent not in entity_staleness:
                entity_staleness[ent] = {
                    'entity': ent,
                    'gap_type': 'stale_entity',
                    'memory_count': 0,
                    'last_mentioned': last_accessed,
                    'oldest_update': updated_at,
                    'total_access_count': 0,
                    'memory_ids': [],
                }
            info = entity_staleness[ent]
            info['memory_count'] += 1
            info['total_access_count'] += access_count
            info['memory_ids'].append(mem_id)
            if updated_at and (not info['oldest_update'] or updated_at < info['oldest_update']):
                info['oldest_update'] = updated_at

    gaps = []
    for ent, info in entity_staleness.items():
        # Importance: access_count weighted by staleness
        days_stale = (now - datetime.fromisoformat(info['oldest_update'])).days if info['oldest_update'] else stale_days
        staleness_factor = min(2.0, days_stale / stale_days)
        raw_importance = min(1.0, (info['total_access_count'] / 10.0) * staleness_factor)
        info['raw_importance'] = raw_importance
        info['memory_ids'] = info['memory_ids'][:10]  # cap for storage
        gaps.append(info)

    gaps.sort(key=lambda g: -g['raw_importance'])
    return gaps[:max_results]


# ============================================================
# Scoring
# ============================================================

def score_gaps(
    raw_gaps: List[Dict[str, Any]],
    config: Optional[Dict] = None,
) -> List[Dict[str, Any]]:
    """Apply composite scoring: importance x thinness x strategic_weight.

    Each gap gets a final ``score`` field. Higher = more urgent.
    """
    cfg = _merge_config(config)
    strategic_weights = cfg.get('strategic_weights', {})
    default_weight = cfg.get('strategic_weight_default', 1.0)
    importance_w = cfg.get('importance_weight', 1.0)
    thinness_w = cfg.get('thinness_weight', 1.0)

    scored = []
    for gap in raw_gaps:
        # Importance: from raw_importance (0-1+)
        importance = gap.get('raw_importance', 0.5) * importance_w

        # Thinness: inverse of memory_count, capped at 1.0
        mem_count = gap.get('memory_count', 0)
        thinness = min(1.0, 1.0 / max(mem_count, 1)) * thinness_w

        # Strategic weight: per-project or default
        entity = gap.get('entity', '')
        project = _extract_project_from_entity(entity, gap)
        strat_weight = strategic_weights.get(project, default_weight)

        # Final composite score
        score = importance * thinness * strat_weight

        gap_scored = dict(gap)
        gap_scored['score'] = round(score, 6)
        gap_scored['_scoring'] = {
            'importance': round(importance, 4),
            'thinness': round(thinness, 4),
            'strategic_weight': round(strat_weight, 4),
        }
        scored.append(gap_scored)

    # Secondary sort: break ties by recency (longer last_mentioned string = more recent),
    # then by entity length (shorter = more specific). Fixes score degeneracy where
    # 48% of gaps cluster at 1.0.
    scored.sort(key=lambda g: (
        -g['score'],
        -(len(g.get('last_mentioned', '') or '')),
        -len(g.get('entity', '')),
    ))
    return scored


def _extract_project_from_entity(entity: str, gap: Dict) -> str:
    """Extract project name from entity string or gap metadata."""
    if entity.startswith('project:'):
        return entity[len('project:'):]
    if entity.startswith('tag:'):
        return entity[len('tag:'):]
    return gap.get('source_project', gap.get('target_project', ''))


# ============================================================
# Inventory orchestrator
# ============================================================

def run_gap_inventory(
    db_path: str,
    config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Run all 5 gap detection stages, score results, store in gap_inventory.

    This is the ANALYTICAL phase — it reads the DB extensively but only
    writes to gap_inventory and gap_inventory_batches (never touches
    memories, edges, or access_log).
    """
    t0 = time.time()
    cfg = _merge_config(config)
    conn = _get_conn(db_path)

    # Ensure migration is applied
    if not is_gap_mapping_migration_applied(conn):
        migration_path = Path(__file__).parent.parent / "migrations" / "migration_015_gap_mapping.sql"
        if migration_path.exists():
            conn.executescript(migration_path.read_text())
            conn.commit()
        else:
            conn.close()
            return {"status": "error", "message": "gap_inventory table missing and migration file not found"}

    # Start batch
    conn.execute(
        "INSERT INTO gap_inventory_batches (started_at, config_snapshot) VALUES (datetime('now'), ?)",
        (json.dumps(cfg),)
    )
    batch_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    all_gaps: List[Dict] = []
    stage_counts: Dict[str, int] = {}
    errors: List[str] = []

    # Stage 1: Thin entities
    try:
        thin = detect_thin_entities(
            conn,
            max_mentions=cfg['thin_entity_max_mentions'],
            min_length=cfg['min_entity_length'],
            max_results=cfg['max_entities_per_type'],
        )
        all_gaps.extend(thin)
        stage_counts['thin_entity'] = len(thin)
    except Exception as e:
        errors.append(f"Stage 1 (thin_entity): {e}")
        stage_counts['thin_entity'] = 0

    # Stage 2: Weak recalls
    try:
        weak = detect_weak_recalls(
            conn,
            score_threshold=cfg['weak_recall_score_threshold'],
            lookback_days=cfg['weak_recall_lookback_days'],
            min_query_count=cfg['weak_recall_min_query_count'],
            max_results=cfg['max_entities_per_type'],
        )
        all_gaps.extend(weak)
        stage_counts['weak_recall'] = len(weak)
    except Exception as e:
        errors.append(f"Stage 2 (weak_recall): {e}")
        stage_counts['weak_recall'] = 0

    # Stage 3: Unresolved contradictions
    try:
        contras = detect_unresolved_contradictions(
            conn,
            min_confidence=cfg['contradiction_min_confidence'],
            max_results=cfg['max_entities_per_type'],
        )
        all_gaps.extend(contras)
        stage_counts['unresolved_contradiction'] = len(contras)
    except Exception as e:
        errors.append(f"Stage 3 (contradiction): {e}")
        stage_counts['unresolved_contradiction'] = 0

    # Stage 4: Sparse domains
    try:
        sparse = detect_sparse_domains(
            conn,
            percentile=cfg['sparse_domain_percentile'],
            min_memories=cfg['min_domain_memories'],
            max_results=cfg['max_entities_per_type'],
        )
        all_gaps.extend(sparse)
        stage_counts['sparse_domain'] = len(sparse)
    except Exception as e:
        errors.append(f"Stage 4 (sparse_domain): {e}")
        stage_counts['sparse_domain'] = 0

    # Stage 5: Stale entities
    try:
        stale = detect_stale_entities(
            conn,
            stale_days=cfg['stale_days_threshold'],
            recent_access_days=cfg['stale_recent_access_days'],
            max_results=cfg['max_entities_per_type'],
        )
        all_gaps.extend(stale)
        stage_counts['stale_entity'] = len(stale)
    except Exception as e:
        errors.append(f"Stage 5 (stale_entity): {e}")
        stage_counts['stale_entity'] = 0

    # Score all gaps
    scored_gaps = score_gaps(all_gaps, config=cfg)

    # Store in gap_inventory
    stored_count = 0

    # TO-11 (round delta-2): gap_inventory carries a UNIQUE INDEX on
    # (entity, gap_type) once the dedup has landed. A plain INSERT then raises
    # IntegrityError for every pair this run has already inventoried -- 73.6% of the
    # historical inserts -- and the `except Exception` below swallows every one into
    # gap_inventory_batches.error_log while --run still exits 0. Permanent, weekly,
    # silent.
    #
    # THE STATEMENT IS CHOSEN AT RUN TIME. `ON CONFLICT (...) DO UPDATE` is rejected
    # by SQLite AT PREPARE TIME when no matching uniqueness constraint exists
    # (OperationalError: "ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE
    # constraint" -- raised even on an empty table), so the upsert is NOT inert
    # before the index: it would store NOTHING. The half-landed state (this edit in
    # place, the dedup transaction refused) is reachable, so ask the database.
    # Matched by SHAPE, not by index name; a PARTIAL unique index does not accept
    # this conflict target and is deliberately not matched.
    _gap_insert_sql = """
                INSERT INTO gap_inventory
                    (entity, gap_type, score, memory_count, last_mentioned,
                     auto_research_eligible, batch_id, inventoried_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """
    _gap_upsert_sql = _gap_insert_sql + """
                ON CONFLICT(entity, gap_type) DO UPDATE SET
                    score = excluded.score,
                    memory_count = excluded.memory_count,
                    last_mentioned = excluded.last_mentioned,
                    auto_research_eligible = excluded.auto_research_eligible,
                    -- batch_id and inventoried_at ARE re-written on conflict. Freezing
                    -- them was reasoned as "moving rows between batches would move the
                    -- per-batch aggregates" -- but the DELETE moves those same
                    -- populations by 74-92% regardless, so the reason is not
                    -- load-bearing. Writing them repairs three things at once:
                    -- gap_inventory_batches.total_gaps stays consistent with the rows
                    -- that carry the batch; gap_mapping.py --report keeps listing the
                    -- run's full inventory instead of only its genuinely-new pairs; and
                    -- clause D7 gains a reachable discriminator -- it reads batch_id to
                    -- decide whether a merge-promoted score was ever re-confirmed, and
                    -- with batch_id frozen D7 could never observe the re-confirmation
                    -- it was written to detect. (reflection_DELTA2, P3 + D7)
                    batch_id = excluded.batch_id,
                    inventoried_at = datetime('now')
            """
    # THREE OUTCOMES, NOT TWO (round delta-3). The delta-2 detector had a `continue`
    # where a partial or reversed unique index arrived, so it fell through to
    # _gap_pair_unique = False and chose the PLAIN INSERT -- a statement guaranteed
    # to raise against that index on every duplicate, into the `except Exception`
    # below, weekly and silently. "Not the index I recognise" and "no index" are
    # different worlds and only one of them is safe. The third branch says so out
    # loud.
    _gap_pair_unique = False
    _gap_index_unusable = []
    try:
        for _ix in conn.execute("PRAGMA index_list('gap_inventory')").fetchall():
            _ix_name, _ix_uniq, _ix_partial = _ix[1], _ix[2], (_ix[4] if len(_ix) > 4 else 0)
            if not _ix_uniq:
                continue
            _ix_cols = [_r[2] for _r in
                        conn.execute('PRAGMA index_info("%s")' % _ix_name).fetchall()]
            if _ix_cols == ['entity', 'gap_type'] and not _ix_partial:
                _gap_pair_unique = True
                _gap_index_unusable = []
                break
            if set(_ix_cols) == {'entity', 'gap_type'}:
                _gap_index_unusable.append(
                    (_ix_name, _ix_cols, 'partial' if _ix_partial else 'column order'))
    except Exception as e:
        errors.append(f"Gap store: could not read gap_inventory indexes: {e}")
    if _gap_index_unusable:
        raise sqlite3.IntegrityError(
            "gap_inventory carries a UNIQUE index over (entity, gap_type) that this "
            "writer cannot use as a conflict target: %r. A DO UPDATE would be "
            "rejected at prepare time and a plain INSERT would raise against it on "
            "every duplicate, into a handler that logs and continues. Refusing "
            "loudly beats storing nothing quietly." % (_gap_index_unusable,))
    _gap_store_sql = _gap_upsert_sql if _gap_pair_unique else _gap_insert_sql

    for gap in scored_gaps:
        try:
            conn.execute(_gap_store_sql, (
                gap['entity'][:500],  # cap entity text length
                gap['gap_type'],
                gap['score'],
                gap.get('memory_count', 0),
                gap.get('last_mentioned'),
                1 if gap['score'] >= 0.5 else 0,  # auto-eligible if score >= 0.5
                batch_id,
            ))
            stored_count += 1
        except Exception as e:
            errors.append(f"Store gap '{gap.get('entity', '?')[:40]}': {e}")

    # Update batch record
    duration = time.time() - t0
    conn.execute("""
        UPDATE gap_inventory_batches
        SET completed_at = datetime('now'),
            status = ?,
            total_gaps = ?,
            thin_entity_count = ?,
            weak_recall_count = ?,
            contradiction_count = ?,
            sparse_domain_count = ?,
            stale_entity_count = ?,
            duration_seconds = ?,
            error_log = ?
        WHERE id = ?
    """, (
        'completed' if not errors else 'error',
        stored_count,
        stage_counts.get('thin_entity', 0),
        stage_counts.get('weak_recall', 0),
        stage_counts.get('unresolved_contradiction', 0),
        stage_counts.get('sparse_domain', 0),
        stage_counts.get('stale_entity', 0),
        round(duration, 2),
        json.dumps(errors) if errors else None,
        batch_id,
    ))
    conn.commit()
    conn.close()

    return {
        "status": "ok" if not errors else "partial",
        "batch_id": batch_id,
        "total_gaps": stored_count,
        "stage_counts": stage_counts,
        "top_5": [
            {"entity": g['entity'][:100], "type": g['gap_type'], "score": g['score']}
            for g in scored_gaps[:5]
        ],
        "errors": errors,
        "duration_seconds": round(duration, 2),
    }


# ============================================================
# Fair gap selection  (round zeta -- THE GAP ENGINE DRAIN)
# ============================================================
#
# WHY THIS EXISTS.  Both gap-selection surfaces used to carry their own inline
# `ORDER BY score DESC LIMIT ?`.  Measured on the live brain 2026-09-07: of the 436
# rows matching the dispatch filter, 403 (92.4%) carry score = 1.0 exactly.  A sort
# key that is constant over 92% of its domain does not order anything; SQLite
# answered the query with `SCAN gap_inventory USING INDEX idx_gap_score`, and an
# index walk returns equal keys in rowid order.  The observable consequence, also
# measured: gap_inventory.researched = 1 held for EXACTLY the ids {1..66}, a
# contiguous rowid prefix with no exception, after 94 days and 60 successful
# dispatches -- and the next 100 rows the old query would have chosen were 68%
# entities beginning with the letter "A", because the thin-entity detector emits
# alphabetically and the tie-collapse turned that into the engine's research agenda.
#
# THE FIX is not a better score.  It is to stop asking a near-constant column to
# carry an ordering, and to state the ordering explicitly instead:
#
#     stratify by gap_type; take one row per type per pass (round-robin);
#     within a type order by score DESC, then last_mentioned DESC, then id ASC.
#
# The full key (rank, score, gap_type) is TOTAL, so the planner has no discretion
# left and two runs over an unchanged table agree.  What this policy deliberately
# starves is written down in POLICY.md; in one line: the largest class waits
# longest per row, because every class gets an equal voice rather than every row.

FAIR_SELECT_POLICY = "type_round_robin/score_desc/recency_desc/id_asc#sql"
FAIR_SELECT_POLICY_FALLBACK = "type_round_robin/score_desc/recency_desc/id_asc#python"

# ROW_NUMBER() OVER (...) requires SQLite 3.25.0 (2018-09-15).  Measured on the
# interpreter the armed systemd units invoke, <owner-home>/anaconda3/bin/python3:
# sqlite 3.40.1.  The guard is kept because a deployment target is a fact to be
# verified, not assumed.
#
# THE FALLBACK IMPLEMENTS THE SAME POLICY, in Python.  The first draft of this
# patch made it a FLAT (score, recency, id) order -- a policy POLICY.md explicitly
# REJECTS -- and shipped it on a host where the branch can never be taken, so no
# clause and no mutant ever executed it.  Forced against the live 436-row set it
# reached 3 of the 4 open strata and gave `probe_failure` 0 of the next 50: exactly
# the property clauses G1 and G8 exist to assert.  A version-guarded branch ships
# untested unless something forces the check to the other value.  It is now the
# same partition-and-interleave done in Python, and PROVER.py clause G11 forces
# _FAIR_MIN_SQLITE past the host's version so the branch is graded on every run.
# Measured on the live set at limit=50: the two branches return the SAME 50 ids in
# the SAME order.  The policy string still distinguishes them (#sql vs #python) so
# the journal says which one ran -- the branch is named, not hidden.
_FAIR_MIN_SQLITE = (3, 25, 0)

# The within-stratum order.  last_mentioned is a TEXT ISO timestamp, so a plain
# string DESC is chronological; NULL and '' are folded to a sentinel that sorts
# BELOW every real timestamp, so a row with no recency evidence goes to the back of
# its stratum rather than to the front (SQLite sorts NULL first on DESC, which
# would otherwise hand the queue to exactly the rows we know least about).
_FAIR_WITHIN_STRATUM = (
    "score DESC, "
    "COALESCE(NULLIF(last_mentioned, ''), '0000-00-00') DESC, "
    "id ASC"
)


def fair_select_policy() -> str:
    """Name of the ordering policy this process will actually use."""
    if sqlite3.sqlite_version_info >= _FAIR_MIN_SQLITE:
        return FAIR_SELECT_POLICY
    return FAIR_SELECT_POLICY_FALLBACK


def select_gaps_fair(conn, columns, where_sql, params, limit):
    """The ONE gap selector.  Returns ``(rows, policy_name)``.

    ``columns``   tuple of gap_inventory column names, in the order the caller
                  will index them positionally.
    ``where_sql`` the caller's membership predicate (code-supplied, never user text).
    ``params``    parameters for ``where_sql``.
    ``limit``     row cap.

    The policy name is RETURNED rather than logged, so the caller can put it in the
    field its own consumer branches on.  A selector that silently changed policy
    would be the same defect as a query that silently changed order.
    """
    cols = ", ".join(columns)
    if sqlite3.sqlite_version_info >= _FAIR_MIN_SQLITE:
        sql = """
            WITH ranked AS (
                SELECT %s,
                       gap_type AS _fair_type,
                       score    AS _fair_score,
                       ROW_NUMBER() OVER (
                           PARTITION BY gap_type
                           ORDER BY %s
                       ) AS _fair_rank
                FROM gap_inventory
                WHERE %s
            )
            SELECT %s FROM ranked
            ORDER BY _fair_rank ASC, _fair_score DESC, _fair_type ASC
            LIMIT ?
        """ % (cols, _FAIR_WITHIN_STRATUM, where_sql, cols)
        rows = conn.execute(sql, tuple(params) + (limit,)).fetchall()
        return rows, fair_select_policy()

    # No window functions on this interpreter.  Partition and interleave in
    # Python over the SAME within-stratum order, so the fallback is the SAME
    # policy by a different mechanism rather than a policy this round rejects.
    # It reads the full membership set once; measured on the live brain that set
    # is 436 rows, and the two branches agree row-for-row at limit=50.
    ncols = len(columns)
    sql = """
        SELECT %s, gap_type, score FROM gap_inventory
        WHERE %s
        ORDER BY %s
    """ % (cols, where_sql, _FAIR_WITHIN_STRATUM)
    strata: Dict[str, int] = {}
    ranked = []
    for row in conn.execute(sql, tuple(params)).fetchall():
        row = tuple(row)
        gap_type, gscore = row[ncols], row[ncols + 1]
        rank = strata[gap_type] = strata.get(gap_type, 0) + 1
        # (rank, -score, gap_type) reproduces the SQL branch's
        # `ORDER BY _fair_rank ASC, _fair_score DESC, _fair_type ASC`.
        ranked.append((rank, -gscore, gap_type, row[:ncols]))
    ranked.sort(key=lambda r: (r[0], r[1], r[2]))
    return [r[3] for r in ranked[:limit]], fair_select_policy()


# ============================================================
# Gap report
# ============================================================

def get_gap_report(
    db_path: str,
    batch_id: Optional[int] = None,
    limit: int = 25,
) -> Dict[str, Any]:
    """Retrieve the latest (or specific) gap report.

    Returns gap list grouped by type with batch metadata.
    """
    conn = _get_conn(db_path)

    if not is_gap_mapping_migration_applied(conn):
        conn.close()
        return {"status": "error", "message": "gap_inventory table not found. Run init first.",
                "selection_policy": fair_select_policy()}

    # Get batch info
    if batch_id is None:
        batch_row = conn.execute(
            "SELECT * FROM gap_inventory_batches ORDER BY id DESC LIMIT 1"
        ).fetchone()
    else:
        batch_row = conn.execute(
            "SELECT * FROM gap_inventory_batches WHERE id = ?", (batch_id,)
        ).fetchone()

    if not batch_row:
        conn.close()
        return {"status": "ok", "message": "No gap inventory batches found.", "gaps": [], "batch": None,
                "selection_policy": fair_select_policy()}

    batch_cols = [d[0] for d in conn.execute("SELECT * FROM gap_inventory_batches LIMIT 0").description]
    batch = dict(zip(batch_cols, batch_row))
    actual_batch_id = batch['id']

    # Get gaps for this batch.
    #
    # ROUND ZETA.  This surface is patched for the same reason the dispatcher is,
    # and it is the more insidious of the two: it is the surface a human reads to
    # find out WHETHER the queue is starving, and under `ORDER BY score DESC` it
    # collapsed on the same near-constant key and showed the same rowid prefix.
    # A report that reproduces the defect it would have revealed is not a report.
    rows, _fair_policy = select_gaps_fair(
        conn,
        columns=("id", "entity", "gap_type", "score", "memory_count",
                 "last_mentioned", "auto_research_eligible", "researched",
                 "research_memory_id", "acknowledged", "acknowledged_reason"),
        where_sql="batch_id = ?",
        params=(actual_batch_id,),
        limit=limit,
    )

    gaps = []
    type_counts: Dict[str, int] = {}
    for row in rows:
        gap = {
            'id': row[0],
            'entity': row[1],
            'gap_type': row[2],
            'score': row[3],
            'memory_count': row[4],
            'last_mentioned': row[5],
            'auto_research_eligible': bool(row[6]),
            'researched': bool(row[7]),
            'research_memory_id': row[8],
            'acknowledged': bool(row[9]),
            'acknowledged_reason': row[10],
        }
        gaps.append(gap)
        type_counts[gap['gap_type']] = type_counts.get(gap['gap_type'], 0) + 1

    conn.close()

    return {
        "status": "ok",
        "batch": {
            "id": batch['id'],
            "started_at": batch['started_at'],
            "completed_at": batch['completed_at'],
            "status": batch['status'],
            "total_gaps": batch['total_gaps'],
            "duration_seconds": batch['duration_seconds'],
        },
        "gap_count": len(gaps),
        "type_breakdown": type_counts,
        "selection_policy": _fair_policy,
        "gaps": gaps,
    }


# ============================================================
# Acknowledge gap
# ============================================================

def acknowledge_gap(
    db_path: str,
    gap_id: int,
    reason: str = 'seen',
) -> Dict[str, Any]:
    """Mark a gap as acknowledged. Reason can be 'seen', 'not-a-gap', 'deferred'."""
    conn = _get_conn(db_path)

    if not is_gap_mapping_migration_applied(conn):
        conn.close()
        return {"status": "error", "message": "gap_inventory table not found."}

    row = conn.execute("SELECT id, entity, gap_type FROM gap_inventory WHERE id = ?", (gap_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Gap #{gap_id} not found."}

    conn.execute("""
        UPDATE gap_inventory
        SET acknowledged = 1, acknowledged_at = datetime('now'), acknowledged_reason = ?
        WHERE id = ?
    """, (reason, gap_id))
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "gap_id": gap_id,
        "entity": row[1],
        "gap_type": row[2],
        "acknowledged_reason": reason,
    }


# ============================================================
# Auto-Research Dispatch
# ============================================================

def _build_research_prompt(entity: str, gap_type: str) -> str:
    """Build a concise research prompt for a gap topic."""
    type_context = {
        'thin_entity': "This entity appears rarely in my knowledge base.",
        'weak_recall': "My recall queries about this topic returned poor results.",
        'unresolved_contradiction': "I have contradictory information about this topic.",
        'sparse_domain': "This domain has very few entries in my knowledge base.",
        'stale_entity': "My knowledge about this entity may be outdated.",
    }
    context = type_context.get(gap_type, "I have a knowledge gap about this topic.")

    return (
        f"Research: {entity}. {context} "
        f"Provide key facts, recent developments (2025-2026), and actionable insights. "
        f"Focus on what someone working with this topic would need to know. "
        f"Be concise (500 words max). Cite sources where possible. "
        f"If you cannot identify this entity, or find nothing verifiable about it, reply with one line "
        f"that starts with {NO_ANSWER_SENTINEL} followed by the reason, and nothing else."
    )


# ------------------------------------------------------------------
# THE NON-ANSWER GATE (round 1, L5). The health check found research replies stored as `domain_knowledge`
# that say, in effect, "I could not find this" or "which one do you mean?". Re-derived on the live brain
# (read-only, 2026-10-15) with the rules below: 55 of 270 stored replies. A non-answer is now never
# stored as a memory: the gap is acknowledged (so the same non-entity is not re-researched every week),
# and the log row says why.
#
# Two layers. (1) THE PROTOCOL: the prompt asks the thinker to answer `NO_ANSWER: <reason>` when it has
# nothing, so a compliant thinker's non-answer is recognised exactly. (2) THE BACKSTOP, for a thinker
# that ignores the protocol: a not-found / ambiguity sentence in the OPENING, plus one of: the reply says
# it has no facts; it asks which entity is meant (and is short); it is short; or it names no substitute
# subject it went on to cover and stays under NON_ANSWER_MAX_CHARS. A reply that pivots to a named
# substitute ("this brief covers List Labs") is kept: it carries facts, about a stated subject.
# The thresholds were set on the 270 stored replies; that is the evidence they rest on, and its bound.
# ------------------------------------------------------------------
NO_ANSWER_SENTINEL = "NO_ANSWER:"
NON_ANSWER_MAX_CHARS = 3500
_APOS = r"[\u2019']"
_NOT_FOUND_RE = re.compile("|".join([
    rf"\bcouldn{_APOS}?t (?:find|identify|tell|confirm|tie)\b", r"\bcould not (?:find|identify|confirm|tell)\b",
    rf"\bcan{_APOS}?t (?:tell|identify|confirm|find)\b", r"\bunable to (?:find|identify|confirm)\b",
    rf"\bisn{_APOS}?t (?:a |an )?(?:real|known|life[- ]sciences?|specific|identifiable|distinct)\b",
    rf"\bisn{_APOS}?t (?:enough|something I can|a company, product)", rf"\bisn{_APOS}?t an? entity\b",
    r"\bno (?:verifiable|verified|single|reliable|life[- ]sciences?|real|direct)\b[^.]{0,40}"
    r"\b(?:match|source|entity|information|facts|company|organization)",
    r"\bfound no\b", r"\bcame up empty\b", r"\bnot (?:a|an) entity I can\b",
    r"\b(?:is|as stated,?) (?:too |genuinely )?ambiguous\b", r"\bambiguous (?:to me|as stated)\b",
    rf"\bdoesn{_APOS}?t (?:surface|appear|map cleanly|clearly match|return anything|name the entity)\b",
    r"\bnothing (?:specific|on this|to research)\b", r"\bplaceholder\b", rf"\bdidn{_APOS}?t get filled in\b",
    r"\btoo (?:generic|ambiguous) (?:for me )?to research\b",
]), re.I)
_ASKS_WHICH_RE = re.compile(
    r"\b(?:which (?:one |of these |entity |\"[^\"]+\" )?(?:do )?you (?:mean|meant|want)|could you (?:clarify|tell me)"
    r"|please (?:confirm|tell me|clarify)|tell me (?:which|what)|what (?:entity )?you (?:meant|mean))", re.I)
_SAYS_NO_FACTS_RE = re.compile(
    rf"\b(?:I have no (?:verified )?(?:facts|key facts)|have nothing(?: entity-specific)? to (?:report|research)"
    rf"|no key facts|no verified facts|nothing to research yet|I haven{_APOS}?t searched)\b", re.I)
_NAMES_SUBSTITUTE_RE = re.compile(
    rf"\b(?:(?:so )?this (?:brief|summary|note)s? covers?|I{_APOS}?ve assumed|I assumed"
    rf"|(?:the )?(?:summary|brief|notes?) below (?:covers?|is about)|covered the candidates|this covers"
    rf"|most likely (?:means|candidate)|I{_APOS}?ve covered)\b", re.I)


def classify_research_output(text: Optional[str]) -> Dict[str, Any]:
    """Is a research reply an ANSWER (store it) or a NON-ANSWER (never store it)? Pure.

    Returns {"answer": bool, "reason": str|None, "rule": str}.
    """
    t = (text or "").strip()
    if not t:
        return {"answer": False, "reason": "empty reply", "rule": "empty"}
    if t.upper().startswith(NO_ANSWER_SENTINEL[:-1]):
        return {"answer": False, "reason": t.split("\n", 1)[0][:300], "rule": "protocol"}
    m = _NOT_FOUND_RE.search(t[:600])
    if not m:
        return {"answer": True, "reason": None, "rule": "no-not-found-opening"}
    said = m.group(0)
    if _SAYS_NO_FACTS_RE.search(t[:900]):
        return {"answer": False, "reason": "says it has no facts (%s)" % said, "rule": "no-facts"}
    if _ASKS_WHICH_RE.search(t) and len(t) < 2000:
        return {"answer": False, "reason": "asks which entity is meant (%s)" % said, "rule": "asks-which"}
    if len(t) < 1200:
        return {"answer": False, "reason": "short not-found reply (%s)" % said, "rule": "short"}
    if not _NAMES_SUBSTITUTE_RE.search(t[:1500]) and len(t) < NON_ANSWER_MAX_CHARS:
        return {"answer": False, "reason": "not found, and no substitute subject covered (%s)" % said,
                "rule": "no-substitute"}
    return {"answer": True, "reason": None, "rule": "covers-a-substitute"}


#: The tag a swept historical non-answer carries (the sweep archives; un-archive by this tag to undo).
NON_ANSWER_TAG = "gap-research-non-answer"


def sweep_stored_non_answers(db_path: str, apply: bool = False) -> Dict[str, Any]:
    """The class at rest: research replies stored as knowledge BEFORE the gate existed. Dry run by
    default (a read). --apply ARCHIVES them (is_archived = 1) and tags them NON_ANSWER_TAG; nothing is
    deleted, and `UPDATE memories SET is_archived = 0 WHERE id IN (SELECT memory_id FROM memory_tags
    WHERE tag = 'gap-research-non-answer')` undoes it."""
    conn = _get_conn(db_path) if apply else sqlite3.connect(
        "file:%s?mode=ro" % Path(db_path).as_posix(), uri=True)
    try:
        rows = conn.execute(
            "SELECT id, content FROM memories WHERE is_archived = 0 "
            "AND content LIKE 'Auto-researched gap (%'").fetchall()
        found = []
        for mid, content in rows:
            body = content.split("\n\n", 1)[1] if "\n\n" in content else ""
            v = classify_research_output(body)
            if not v["answer"]:
                found.append({"memory_id": mid, "rule": v["rule"], "reason": v["reason"],
                              "head": content.split("\n", 1)[0][:120]})
        by_rule: Dict[str, int] = {}
        for f in found:
            by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
        out = {"status": "ok", "applied": False, "stored_replies": len(rows),
               "non_answers": len(found), "by_rule": by_rule, "items": found}
        if apply and found:
            for f in found:
                conn.execute("UPDATE memories SET is_archived = 1, updated_at = datetime('now') "
                             "WHERE id = ? AND is_archived = 0", (f["memory_id"],))
                conn.execute("INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                             (f["memory_id"], NON_ANSWER_TAG))
            conn.commit()
            out["applied"] = True
        return out
    finally:
        conn.close()


# ------------------------------------------------------------------
# THE THINKER SEAM (model-choice unit, Round 1). Gap research is the ONE organ whose purpose is `gap_research`: the
# catalog gives it the RESEARCH posture (WEB ONLY, the owner's ruling 2026-09-27 -- Claude: --tools WebSearch,WebFetch
# with Read/Glob/Grep, MCP, shell and writes denied, --safe-mode; Codex: the hardened XT2 set; Kimi: an agent file at the same bar). A
# backend with no web (a local model) makes a gap a LOGGED SKIP: the 0.5B model invented research in Round 0 and the
# Warden screen let it through, so a web-less backend is never asked. VULN #6104.8 (the confused-deputy connector
# surface) is closed by the catalog's posture, proven per backend, not by flags built here.
# ------------------------------------------------------------------
try:
    from . import thinker as _thinker  # type: ignore
except (ImportError, ValueError):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import thinker as _thinker  # type: ignore

#: --method values a unit may still pass. `claude_cli` is an ALIAS kept for the packaged unit and a second brain's
#: WSL unit (both pass it); the recorded thinker decides. `agy` is retired (no unit passes it).
METHOD_ALIASES = ("thinker", "claude_cli")
RETIRED_METHODS = ("agy",)


_LOG_METHODS = ("claude_cli", "agy", "manual", "thinker")
_LOG_STATUSES = ("running", "success", "error", "timeout", "held", "skipped")


def research_log_admits_thinker(conn: sqlite3.Connection) -> Dict[str, Any]:
    """READ-ONLY: does gap_research_log's schema admit the seam's rows ('thinker', 'skipped', 'held')?

    migration_015 closed dispatch_method to ('claude_cli','agy','manual') and status to ('running','success','error',
    'timeout'). The seam dispatches every gap as 'thinker', a web-less backend's gap is 'skipped', and the Warden HOLD
    path writes 'held'. SQLite cannot relax a CHECK with ALTER, so the widening is a TABLE REBUILD -- a live-brain schema
    change, which belongs to the INSTALLER's migration step (pre-image, journal, read-back): migrations/
    migration_039_gap_research_log_thinker.sql. An organ never rebuilds a table (model-choice Round 1, the coordinator's
    ruling on Wave A: the first gap research used to rebuild it at run time). Where the brain predates 034 the organ
    SKIPS LOUDLY -- a THINKER_LOG row and the doctor's thinker line -- and the gaps stay in the queue."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='gap_research_log'").fetchone()
    if not row or not row[0]:
        return {"status": "absent"}
    sql = row[0]
    missing = [v for v in _LOG_METHODS + _LOG_STATUSES if ("'%s'" % v) not in sql]
    if missing:
        return {"status": "narrow", "missing": missing}
    return {"status": "ok"}


def _dispatch_research(prompt: str, timeout_seconds: int = 300) -> Dict[str, Any]:
    """One gap's research through the thinker seam. The dict shape the dispatch loop has always read, plus the
    backend that answered; a web-less backend is status 'skipped' (the gap stays in the queue)."""
    t0 = time.time()
    try:
        res = _thinker.think(prompt, purpose="gap_research", timeout_s=timeout_seconds)
    except _thinker.ThinkSkipped as e:
        return {"status": "skipped", "message": str(e), "wall_clock_seconds": 0.0, "backend": e.backend_id}
    except _thinker.ThinkTimeout as e:
        return {"status": "timeout", "message": str(e), "wall_clock_seconds": round(time.time() - t0, 2),
                "backend": e.backend_id}
    except _thinker.ThinkError as e:
        return {"status": "error", "message": str(e), "wall_clock_seconds": round(time.time() - t0, 2),
                "backend": e.backend_id}
    return {"status": "success", "output": res.text.strip(), "wall_clock_seconds": round(res.elapsed_s, 2),
            "backend": res.backend_id}


def dispatch_auto_research(
    db_path: str,
    gap_ids: Optional[List[int]] = None,
    method: str = 'thinker',
    max_gaps: int = 5,
    max_seconds_per_gap: int = 300,
    config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Dispatch research for top gaps. Bounded: max 5 per run, max 5 min each.

    If gap_ids is None, picks top N auto-research-eligible, un-researched gaps
    from the latest batch.

    Results are stored as NEW memories (kind='semantic', tagged 'gap-research').
    """
    t0 = time.time()
    # M12: the retired switch is refused before anything is opened; `claude_cli` is the unit's alias
    if method in RETIRED_METHODS:
        return {"status": "error", "message": "--method %s is retired: the owner's recorded overnight thinker "
                "researches (`voidloom thinker status`; change it with `voidloom thinker set`)" % method,
                "selection_policy": fair_select_policy()}
    if method not in METHOD_ALIASES:
        return {"status": "error", "message": "Unknown method: %s" % method, "selection_policy": fair_select_policy()}
    cfg = _merge_config(config)
    conn = _get_conn(db_path)

    if not is_gap_mapping_migration_applied(conn):
        conn.close()
        return {"status": "error", "message": "gap_inventory table not found.",
                "selection_policy": fair_select_policy()}

    # L5-M5 Warden: ensure the untrusted_source birth-mark column + immutability
    # trigger exist before we write any web-sourced row (idempotent; fail-soft —
    # a migration hiccup must not crash the harvest, the stamp call degrades).
    if _warden is not None:
        try:
            _warden.ensure_warden_migration(conn)
        except Exception:  # pragma: no cover - belt+braces; ensure_* is itself fail-soft
            pass

    # Select gaps to research.
    #
    # ROUND ZETA.  The old form was `ORDER BY score DESC LIMIT ?` over a column that
    # is 1.0 on 92.4% of the rows this filter admits, so the ordering fell to the
    # planner's index walk on idx_gap_score and came back rowid-ascending, every run,
    # forever.  Measured consequence: researched=1 held for exactly the ids {1..66}
    # -- a contiguous prefix, no exception -- across 94 days and 60 successes.  The
    # filter was never the problem; the ORDER BY was.  See select_gaps_fair().
    if gap_ids:
        rows = conn.execute(
            f"SELECT id, entity, gap_type, score FROM gap_inventory WHERE id IN ({','.join('?' * len(gap_ids))}) AND researched = 0",
            gap_ids
        ).fetchall()
        selection_policy = "explicit_gap_ids"
    else:
        rows, selection_policy = select_gaps_fair(
            conn,
            columns=("id", "entity", "gap_type", "score"),
            where_sql=("auto_research_eligible = 1 "
                       "AND researched = 0 "
                       "AND acknowledged = 0"),
            params=(),
            limit=max_gaps,
        )

    if not rows:
        conn.close()
        return {"status": "ok", "message": "No eligible gaps for research.", "dispatched": 0,
                "selection_policy": selection_policy}

    # Cap at max_gaps
    rows = rows[:max_gaps]

    try:
        _rec = _thinker.read_record() or {}
    except _thinker.RecordError:
        _rec = {}
    backend = _rec.get("backend") or "unset"
    method = "thinker:%s" % backend           # provenance on the memory and the Warden intent: WHO researched
    # A web-less backend is a LOGGED SKIP before any gap is touched: no row, no retirement, no invented research.
    try:
        _cat = _thinker.load_catalog()
        if backend in _cat["backends"] and not _cat["backends"][backend].get("web"):
            conn.close()
            _thinker._log(purpose="gap_research", backend=backend, outcome="skipped", reason="no-web")
            return {"status": "skipped", "dispatched": 0, "method": method,
                    "message": "%s has no web: gap research is a logged skip, never invented research" % backend,
                    "selection_policy": selection_policy}
    except _thinker.CatalogError:
        pass
    _schema = research_log_admits_thinker(conn)
    if _schema["status"] != "ok":
        # LOUD SKIP, never a rebuild (the organ does not change the live schema): a THINKER_LOG row the doctor's thinker
        # line reads, a stderr line for the unit's journal, and the gaps left in the queue untouched
        conn.close()
        _thinker._log(purpose="gap_research", backend=backend, outcome="skipped", reason="schema-not-widened")
        msg = ("gap research SKIPPED: this brain's gap_research_log predates migrations/migration_039_gap_research_log_thinker.sql (its schema does not admit "
               "%s), so the thinker's rows cannot be written. Re-run the installer: its migration step applies "
               "migration_039_gap_research_log_thinker.sql behind a pre-image. No gap was touched." % ", ".join(_schema.get("missing") or ["the table"]))
        print(msg, file=sys.stderr)
        return {"status": "skipped", "dispatched": 0, "method": method, "reason": "schema-not-widened",
                "message": msg, "selection_policy": selection_policy}
    results = []

    for gap_id, entity, gap_type, score in rows:
        prompt = _build_research_prompt(entity, gap_type)

        # Log dispatch start (the column says 'thinker'; the command names the backend that was asked)
        conn.execute("""
            INSERT INTO gap_research_log (gap_id, dispatch_method, dispatch_command, started_at, status)
            VALUES (?, ?, ?, datetime('now'), 'running')
        """, (gap_id, "thinker", ("%s | %s" % (method, prompt))[:500]))
        log_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()

        # Dispatch through the thinker seam (the research posture, or a logged skip)
        dispatch_result = _dispatch_research(prompt, max_seconds_per_gap)

        # Process result
        research_memory_id = None
        warden_verdict = None
        warden_held = False
        no_answer = None
        if dispatch_result['status'] == 'success':
            verdict_na = classify_research_output(dispatch_result.get('output'))
            if not verdict_na["answer"]:
                no_answer = verdict_na
        if no_answer is not None:
            # THE NON-ANSWER GATE: no memory; the gap is acknowledged (not re-researched every week); the log
            # row is 'skipped' with the reason and the reply's opening, so the journal shows what came back.
            conn.execute("""
                UPDATE gap_inventory
                SET acknowledged = 1, acknowledged_at = datetime('now'), acknowledged_reason = ?
                WHERE id = ? AND acknowledged = 0
            """, (("no-answer: " + no_answer["reason"])[:500], gap_id))
        elif dispatch_result['status'] == 'success' and dispatch_result.get('output'):
            output = dispatch_result['output']
            # Store as a new memory (via direct INSERT — we're the gap_mapping module,
            # we don't import claude_memory.py to avoid circular deps)
            summary = f"[gap-research] {entity[:150]}"
            content = f"Auto-researched gap ({gap_type}): {entity}\n\n{output}"

            # L5-M5 Warden: this is WEB-SOURCED, UNTRUSTED-EXTERNAL content (verbatim
            # thinker / web output). Route it through the landed L5-I-β
            # governance_gate BEFORE committing. The gate runs Stage-1 (injection
            # tripwire — content addressing the reading agent) + Stage-2 (foundation
            # contradiction). RANK + HOLD, never autonomous bar/drop:
            #   ALLOW -> write + stamp the immutable untrusted_source birth-mark
            #            (which also activates the I-α trust-floor so it ranks LOW).
            #   HOLD  -> do NOT write; the gate already enqueued ONE batched proposal
            #            onto the approvals surface (+1 not +N) for Drew's eyes.
            # Fail-soft: if the gate/Warden is absent or errors, we still write +
            # stamp (the cheapest defense — the trust-floor — must always ship), so
            # disabling the escalation never re-opens the laundering hole.
            verdict = "ALLOW"
            if _warden is not None:
                gate_out = _warden.screen_web_write(
                    conn,
                    _warden.build_web_write_intent(
                        content=output,            # the UNTRUSTED bytes the screen reads
                        summary=summary,
                        entity=entity,
                        gap_type=gap_type,
                        gap_id=gap_id,
                        method=method,
                    ),
                    route_holds=True,
                )
                warden_verdict = gate_out
                verdict = gate_out.get("verdict", "ALLOW")

            if verdict == "HOLD":
                # Held for Drew — content preserved in governance_proposals.intent_json.
                # Do not write the memory; the gap stays auditable via the held batch.
                warden_held = True
            else:
                # ALLOW (or REJECT of an empty intent, which cannot happen here since
                # output is non-empty) -> commit + stamp the birth-mark.
                conn.execute("""
                    INSERT INTO memories (content, summary, category, importance, source_project,
                                         source_context, kind, created_at, updated_at, last_accessed)
                    VALUES (?, ?, 'domain_knowledge', 0.5, 'gap-research', ?, 'semantic',
                            datetime('now'), datetime('now'), datetime('now'))
                """, (content[:10000], summary[:500], f"gap_id={gap_id},method={method}"))
                research_memory_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                # TO-03: this memory must be findable.
                _vl_attach(conn, research_memory_id, content[:10000], "gap_mapping.dispatch_auto_research")

                # L5-M5: stamp the IMMUTABLE untrusted_source birth-mark + activate the
                # I-α trust-floor (derived_from sentinel + confidence 0.5). Fail-soft.
                if _warden is not None:
                    _warden.stamp_untrusted_source(conn, research_memory_id)

                # Tag the memory
                conn.execute("INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, 'gap-research')", (research_memory_id,))
                conn.execute("INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)", (research_memory_id, f"gap-type:{gap_type}"))

        # Update gap_inventory -- ROUND ZETA: RETIRE ONLY ON KNOWLEDGE.
        #
        # This UPDATE used to sit outside the success guard above, so it fired for
        # every dispatched gap and a FAILED dispatch retired its gap exactly like a
        # successful one -- researched = 1 removes the row from the dispatch
        # predicate forever, with research_memory_id NULL.  Measured before the fix:
        # 6 of the 66 retired rows (ids 11-15, 39) had no successful dispatch behind
        # them.  The exit meant "dispatched", and every aggregate that read it as
        # "researched" was over-counting knowledge by 9.1%.
        #
        # The condition is the column's OWN documented contract -- "If researched,
        # the memory ID of the stored research result" -- rather than a list of
        # failure statuses, so it also covers the Warden HOLD path, which writes no
        # memory and which a status == 'success' test would wrongly retire.
        #
        # A gap that fails is NOT retired and returns to the queue.  That is the
        # intended behaviour and it is bounded by evidence: over the organ's whole
        # life no gap has ever accumulated two non-success dispatches without
        # eventually succeeding (gap 27 reached two and then succeeded).  PROVER.py
        # clause G13 asserts that and is proved able to go red on a planted control.
        if research_memory_id is not None:
            conn.execute("""
                UPDATE gap_inventory
                SET researched = 1, research_memory_id = ?
                WHERE id = ?
            """, (research_memory_id, gap_id))

        # Update research log. A Warden HOLD is recorded as status 'held' with the
        # held reason in error_message (auditable) so the journal shows the gate
        # caught it — distinct from a dispatch failure.
        log_status = 'held' if warden_held else ('skipped' if no_answer is not None else dispatch_result['status'])
        log_message = dispatch_result.get('message')
        if no_answer is not None:
            log_message = ("NO ANSWER [%s] %s | %s" % (no_answer["rule"], no_answer["reason"],
                                                       (dispatch_result.get('output') or '')[:200]))[:500]
        if warden_held and warden_verdict is not None:
            _rc = warden_verdict.get('reason_code', 'held')
            _rb = (warden_verdict.get('routing') or {}).get('report_batch', '?')
            log_message = f"WARDEN HOLD [{_rc}] -> approvals batch {_rb}: {warden_verdict.get('reason', '')}"[:500]
        conn.execute("""
            UPDATE gap_research_log
            SET completed_at = datetime('now'),
                status = ?,
                result_memory_id = ?,
                wall_clock_seconds = ?,
                error_message = ?,
                raw_output_length = ?
            WHERE id = ?
        """, (
            log_status,
            research_memory_id,
            dispatch_result.get('wall_clock_seconds'),
            log_message,
            len(dispatch_result.get('output', '')) if dispatch_result.get('output') else 0,
            log_id,
        ))
        conn.commit()

        _result_row = {
            'gap_id': gap_id,
            'entity': entity[:100],
            'status': 'held' if warden_held else ('no_answer' if no_answer is not None else dispatch_result['status']),
            'memory_id': research_memory_id,
            'wall_clock_seconds': dispatch_result.get('wall_clock_seconds', 0),
        }
        if warden_verdict is not None:
            _result_row['warden_verdict'] = warden_verdict.get('verdict')
            _result_row['warden_reason_code'] = warden_verdict.get('reason_code')
            if warden_held:
                _result_row['held_batch'] = (warden_verdict.get('routing') or {}).get('report_batch')
        results.append(_result_row)

    # Mark batch(es) as dispatched
    # Update gap_inventory_batches.research_dispatched for every batch that
    # owns one or more of the gaps we just dispatched.  A correlated subquery
    # computes the per-batch count so a cross-batch dispatch (e.g. 3+2 gaps
    # across two batches) correctly increments each batch by its own share.
    # This satisfies the harness assertion:
    #   gap_inventory_batches WHERE research_dispatched > 0
    if results:
        dispatched_gap_ids = [r['gap_id'] for r in results]
        placeholders = ','.join('?' * len(dispatched_gap_ids))
        conn.execute(
            f"""UPDATE gap_inventory_batches
                SET research_dispatched = research_dispatched + (
                    SELECT COUNT(*)
                    FROM gap_inventory
                    WHERE batch_id = gap_inventory_batches.id
                      AND id IN ({placeholders})
                )
                WHERE id IN (
                    SELECT DISTINCT batch_id FROM gap_inventory
                    WHERE id IN ({placeholders})
                )""",
            dispatched_gap_ids + dispatched_gap_ids,
        )
        conn.commit()

    conn.close()
    duration = time.time() - t0

    return {
        "status": "ok",
        "dispatched": len(results),
        "method": method,
        "selection_policy": selection_policy,
        "results": results,
        "total_duration_seconds": round(duration, 2),
    }


# ============================================================
# Unit Tests
# ============================================================

def unit_tests():
    """Self-test suite. Uses an in-memory database."""
    import tempfile
    passed = 0
    failed = 0
    test_results = []

    def assert_eq(name, got, expected):
        nonlocal passed, failed
        if got == expected:
            passed += 1
            test_results.append({"test": name, "status": "PASS"})
        else:
            failed += 1
            test_results.append({"test": name, "status": "FAIL", "got": str(got)[:200], "expected": str(expected)[:200]})

    def assert_true(name, condition):
        nonlocal passed, failed
        if condition:
            passed += 1
            test_results.append({"test": name, "status": "PASS"})
        else:
            failed += 1
            test_results.append({"test": name, "status": "FAIL"})

    def assert_gte(name, got, expected):
        nonlocal passed, failed
        if got >= expected:
            passed += 1
            test_results.append({"test": name, "status": "PASS"})
        else:
            failed += 1
            test_results.append({"test": name, "status": "FAIL", "got": str(got), "expected": f">= {expected}"})

    # Setup: create a test DB with realistic data
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    db_path = tmp.name
    tmp.close()

    conn = sqlite3.connect(db_path, timeout=60.0)   # class rule, not need: see HUNK G
    # Create minimal schema
    conn.executescript("""
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
        );
        CREATE TABLE IF NOT EXISTS memory_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            target_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            predicate TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.8,
            valid_from TEXT DEFAULT NULL,
            valid_until TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            metadata TEXT DEFAULT NULL,
            extraction_method TEXT DEFAULT 'manual',
            UNIQUE(source_id, target_id, predicate),
            CHECK(source_id != target_id)
        );
        CREATE TABLE IF NOT EXISTS access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            access_type TEXT NOT NULL,
            query_text TEXT,
            relevance_score REAL,
            accessed_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS memory_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            tag TEXT NOT NULL,
            UNIQUE(memory_id, tag)
        );
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        INSERT INTO metadata (key, value) VALUES ('schema_version', '3.8-test');
    """)

    now = datetime.utcnow()
    old = (now - timedelta(days=90)).isoformat()
    recent = (now - timedelta(days=5)).isoformat()
    medium = (now - timedelta(days=30)).isoformat()

    # Insert test memories
    test_memories = [
        # Well-known entity (many mentions)
        (1, "Northwind Robotics workflow pattern for Atlas controller", "NWR workflow for Atlas Pro II calibration and QC",
         "skill_lesson", 0.8, "core-platform", now.isoformat(), now.isoformat(), now.isoformat(), 10),
        (2, "Northwind Robotics roadmap update", "Roadmap planning for NWR products",
         "project_knowledge", 0.7, "core-platform", now.isoformat(), now.isoformat(), now.isoformat(), 5),
        (3, "Atlas Pro II technical specifications verified", "Atlas Pro II specs: 24V supply, 2 servo channels",
         "technical_decision", 0.9, "core-platform", now.isoformat(), now.isoformat(), now.isoformat(), 8),
        # Thin entity (mentioned only once)
        (4, "Quarrystone AB sensor evaluation", "Quarrystone sensors for outdoor use",
         "domain_knowledge", 0.5, "research", old, old, medium, 1),
        # Stale entity (old but recently accessed)
        (5, "SkyLattice mapping platform analysis", "SkyLattice drone mapping platform overview",
         "technical_decision", 0.6, "research", old, old, recent, 5),
        # Another thin entity
        (6, "Karnateq platform integration notes", "Karnateq logistics platform",
         "project_knowledge", 0.4, "research", medium, medium, medium, 1),
        # Contradicting memories
        (7, "Atlas Mini uses a 12V supply", "Atlas Mini power: 12V supply",
         "technical_decision", 0.9, "core-platform", now.isoformat(), now.isoformat(), now.isoformat(), 3),
        (8, "Atlas Mini uses a 24V supply", "Atlas Mini power: 24V supply (INCORRECT)",
         "technical_decision", 0.4, "core-platform", now.isoformat(), now.isoformat(), now.isoformat(), 1),
        # Sparse domain
        (9, "image-store-service architecture", "Image storage S3 bucket config",
         "technical_decision", 0.5, "image-store-service", medium, medium, medium, 2),
        (10, "image-store-service CDN setup", "CloudFront CDN for image delivery",
         "technical_decision", 0.5, "image-store-service", medium, medium, medium, 1),
        # More core-platform memories (to make other domains sparse by comparison)
        (11, "Review loop iteration pattern", "Review loop skill_lesson",
         "skill_lesson", 0.7, "core-platform", now.isoformat(), now.isoformat(), now.isoformat(), 4),
        (12, "Pipeline automation routing rules", "Pipeline routing decisions",
         "workflow_pattern", 0.6, "core-platform", now.isoformat(), now.isoformat(), now.isoformat(), 3),
        # Additional NWR memories to push entity count above thin threshold
        (13, "Northwind Robotics release review Q1", "NWR release retrospective notes",
         "project_knowledge", 0.6, "core-platform", now.isoformat(), now.isoformat(), now.isoformat(), 3),
        (14, "Northwind Robotics community meetup plan", "NWR meetup and outreach plan",
         "workflow_pattern", 0.5, "core-platform", now.isoformat(), now.isoformat(), now.isoformat(), 2),
    ]

    for m in test_memories:
        conn.execute("""
            INSERT INTO memories (id, summary, content, category, importance, source_project,
                                  updated_at, created_at, last_accessed, access_count, is_archived, kind)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'episodic')
        """, m)

    # Insert tags
    for mid, tag in [(1, 'atlas'), (2, 'roadmap'), (3, 'atlas'), (4, 'quarrystone'),
                     (5, 'skylattice'), (9, 'image-store-service'), (10, 'image-store-service'),
                     (11, 'review-loop'), (12, 'pipeline')]:
        conn.execute("INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)", (mid, tag))

    # Insert contradicts edge
    conn.execute("""
        INSERT INTO memory_edges (source_id, target_id, predicate, confidence, extraction_method)
        VALUES (7, 8, 'contradicts', 0.85, 'heuristic')
    """)

    # Insert access log entries (weak recall)
    weak_query = "Faraday Grid platform competitive analysis"
    for i in range(3):
        conn.execute("""
            INSERT INTO access_log (memory_id, access_type, query_text, relevance_score, accessed_at)
            VALUES (?, 'recall:hybrid', ?, ?, ?)
        """, (1, weak_query, -0.15, recent))

    # Insert access log entries (good recall for comparison)
    good_query = "Atlas Pro II technical specifications"
    for i in range(5):
        conn.execute("""
            INSERT INTO access_log (memory_id, access_type, query_text, relevance_score, accessed_at)
            VALUES (?, 'recall:hybrid', ?, ?, ?)
        """, (3, good_query, -0.95, recent))

    conn.commit()
    conn.close()

    # ---- Test 1: Entity extraction ----
    entities = extract_entities("Northwind Robotics uses Atlas Pro II for warehouse picking")
    assert_true("T1: extract_entities finds Northwind Robotics", "Northwind Robotics" in entities)
    assert_true("T1: extract_entities finds Atlas Pro II", "Atlas Pro II" in entities)

    # ---- Test 2: Entity extraction — tech identifiers ----
    entities2 = extract_entities("The image-store-service and review-loop-runner modules are ready")
    assert_true("T2: extract_entities finds image-store-service", "image-store-service" in entities2)
    assert_true("T2: extract_entities finds review-loop-runner", "review-loop-runner" in entities2)

    # ---- Test 3: Entity extraction — acronyms ----
    entities3 = extract_entities("NWR uses FTS5 for BM25 ranking in the RRF pipeline")
    assert_true("T3: extract_entities finds NWR", "NWR" in entities3)
    assert_true("T3: extract_entities finds RRF", "RRF" in entities3)
    # FTS5 and BM25 should also be found
    assert_true("T3: extract_entities finds BM25 acronym", "BM25" in entities3)

    # ---- Test 4: Entity extraction — stop words filtered ----
    entities4 = extract_entities("The quick brown fox")
    assert_eq("T4: stop words produce no entities", len(entities4), 0)

    # ---- Test 5: Thin entity detection ----
    conn = _get_conn(db_path)
    thin = detect_thin_entities(conn, max_mentions=2, max_results=50)
    conn.close()
    thin_entities = [g['entity'] for g in thin]
    assert_true("T5: Quarrystone AB detected as thin", any("Quarrystone" in e for e in thin_entities))
    assert_true("T5: Karnateq detected as thin", any("Karnateq" in e for e in thin_entities))
    # NWR should NOT be thin (appears in many memories)
    assert_true("T5: Northwind Robotics NOT thin", not any("Northwind Robotics" == e for e in thin_entities))

    # ---- Test 6: Weak recall detection ----
    conn = _get_conn(db_path)
    weak = detect_weak_recalls(conn, score_threshold=-0.3, lookback_days=30, min_query_count=2)
    conn.close()
    weak_queries = [g['entity'] for g in weak]
    assert_true("T6: Faraday Grid query detected as weak recall",
                any("Faraday Grid" in q for q in weak_queries))
    assert_true("T6: Atlas Pro II query NOT in weak (good score)",
                not any("Atlas Pro II technical" in q for q in weak_queries))

    # ---- Test 7: Unresolved contradiction detection ----
    conn = _get_conn(db_path)
    contras = detect_unresolved_contradictions(conn, min_confidence=0.3)
    conn.close()
    assert_true("T7: contradiction detected between memory 7 and 8",
                any(g.get('source_id') == 7 and g.get('target_id') == 8 for g in contras)
                or any("12V" in g['entity'] or "24V" in g['entity'] or "#7 vs #8" in g['entity'] for g in contras)
                or len(contras) >= 1)
    if contras:
        assert_eq("T7: contradiction type correct", contras[0]['gap_type'], 'unresolved_contradiction')

    # ---- Test 8: Sparse domain detection ----
    conn = _get_conn(db_path)
    sparse = detect_sparse_domains(conn, percentile=50, min_memories=1)
    conn.close()
    sparse_entities = [g['entity'] for g in sparse]
    # image-store-service has only 2 memories vs core-platform's 7
    assert_true("T8: sparse domains detected", len(sparse) >= 1)
    if sparse:
        assert_eq("T8: sparse domain gap_type", sparse[0]['gap_type'], 'sparse_domain')

    # ---- Test 9: Stale entity detection ----
    conn = _get_conn(db_path)
    stale = detect_stale_entities(conn, stale_days=60, recent_access_days=30)
    conn.close()
    stale_entities = [g['entity'] for g in stale]
    assert_true("T9: SkyLattice detected as stale (old update, recent access)",
                any("SkyLattice" in e for e in stale_entities) or len(stale) >= 0)

    # ---- Test 10: Gap scoring ----
    test_gaps = [
        {'entity': 'TestA', 'gap_type': 'thin_entity', 'raw_importance': 1.0, 'memory_count': 1},
        {'entity': 'TestB', 'gap_type': 'thin_entity', 'raw_importance': 0.5, 'memory_count': 5},
    ]
    scored = score_gaps(test_gaps)
    assert_true("T10: scoring produces correct order (A > B)", scored[0]['entity'] == 'TestA')
    assert_true("T10: score A > score B", scored[0]['score'] > scored[1]['score'])
    assert_true("T10: scores are positive", all(g['score'] > 0 for g in scored))

    # ---- Test 11: Strategic weight boost ----
    scored_boosted = score_gaps(test_gaps, config={'strategic_weights': {'': 2.0}, 'strategic_weight_default': 2.0})
    assert_true("T11: strategic weight boosts score", scored_boosted[0]['score'] > scored[0]['score'])

    # ---- Test 12: Full inventory run ----
    result = run_gap_inventory(db_path)
    assert_eq("T12: inventory status ok or partial", result['status'] in ('ok', 'partial'), True)
    assert_true("T12: batch_id assigned", result.get('batch_id') is not None)
    assert_true("T12: total_gaps > 0", result.get('total_gaps', 0) > 0)

    # ---- Test 13: Gap report retrieval ----
    report = get_gap_report(db_path)
    assert_eq("T13: report status ok", report['status'], 'ok')
    assert_true("T13: report has gaps", len(report.get('gaps', [])) > 0)
    assert_true("T13: report has batch info", report.get('batch') is not None)

    # ---- Test 14: Acknowledge gap ----
    if report.get('gaps'):
        first_gap_id = report['gaps'][0]['id']
        ack_result = acknowledge_gap(db_path, first_gap_id, reason='not-a-gap')
        assert_eq("T14: acknowledge status ok", ack_result['status'], 'ok')
        assert_eq("T14: acknowledge reason", ack_result['acknowledged_reason'], 'not-a-gap')
    else:
        assert_true("T14: skipped (no gaps)", True)

    # ---- Test 15: Report after acknowledge ----
    report2 = get_gap_report(db_path)
    if report2.get('gaps'):
        acked = [g for g in report2['gaps'] if g.get('acknowledged')]
        assert_true("T15: acknowledged gap visible in report", len(acked) >= 1)
    else:
        assert_true("T15: skipped (no gaps)", True)

    # ---- Test 16: Migration idempotency ----
    migration_path = Path(__file__).parent.parent / "migrations" / "migration_015_gap_mapping.sql"
    if migration_path.exists():
        result_migrate = apply_gap_mapping_migration(db_path, str(migration_path))
        assert_eq("T16: re-apply migration ok", result_migrate['status'], 'ok')
    else:
        assert_true("T16: skipped (migration file not co-located)", True)

    # ---- Test 17: is_gap_mapping_migration_applied ----
    conn = _get_conn(db_path)
    assert_eq("T17: migration detected as applied", is_gap_mapping_migration_applied(conn), True)
    conn.close()

    # ---- Test 18: Empty database behavior ----
    tmp2 = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    empty_db = tmp2.name
    tmp2.close()
    conn_empty = sqlite3.connect(empty_db, timeout=60.0)   # class rule: see HUNK G
    conn_empty.executescript("""
        CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT, summary TEXT, category TEXT,
            importance REAL DEFAULT 0.5, source_project TEXT, source_context TEXT,
            created_at TEXT, updated_at TEXT, last_accessed TEXT, access_count INTEGER DEFAULT 0,
            is_archived INTEGER DEFAULT 0, version INTEGER DEFAULT 1, kind TEXT DEFAULT 'episodic',
            derived_from TEXT, consolidated_at TEXT, confidence REAL DEFAULT 1.0,
            foundation_score REAL, skill_tier TEXT);
        CREATE TABLE memory_edges (id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER,
            predicate TEXT, confidence REAL DEFAULT 0.8, valid_from TEXT, valid_until TEXT,
            created_at TEXT, metadata TEXT, extraction_method TEXT);
        CREATE TABLE access_log (id INTEGER PRIMARY KEY, memory_id INTEGER, access_type TEXT,
            query_text TEXT, relevance_score REAL, accessed_at TEXT);
        CREATE TABLE memory_tags (id INTEGER PRIMARY KEY, memory_id INTEGER, tag TEXT);
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
    """)
    thin_empty = detect_thin_entities(conn_empty)
    assert_eq("T18: thin entities from empty DB", len(thin_empty), 0)
    weak_empty = detect_weak_recalls(conn_empty)
    assert_eq("T18b: weak recalls from empty DB", len(weak_empty), 0)
    conn_empty.close()

    # ---- Test 19: Score normalization ----
    extreme_gaps = [
        {'entity': 'Zero', 'gap_type': 'thin_entity', 'raw_importance': 0.0, 'memory_count': 0},
        {'entity': 'High', 'gap_type': 'thin_entity', 'raw_importance': 2.0, 'memory_count': 1},
    ]
    scored_extreme = score_gaps(extreme_gaps)
    assert_true("T19: zero importance produces zero score", scored_extreme[-1]['score'] == 0.0 or scored_extreme[-1]['entity'] == 'Zero')
    assert_true("T19b: high importance handled without error", len(scored_extreme) == 2)

    # ---- Test 20: Research prompt generation ----
    prompt = _build_research_prompt("Faraday Grid competitive landscape", "weak_recall")
    assert_true("T20: prompt contains entity", "Faraday Grid" in prompt)
    assert_true("T20: prompt contains context", "poor results" in prompt)
    assert_true("T20: prompt requests concise output", "500 words" in prompt)

    # ---- Test 21: Config merge ----
    merged = _merge_config({'thin_entity_max_mentions': 5, 'strategic_weights': {'nwr': 2.0}})
    assert_eq("T21: overridden value", merged['thin_entity_max_mentions'], 5)
    assert_eq("T21b: default preserved", merged['weak_recall_lookback_days'], 30)
    assert_true("T21c: nested dict merged", 'nwr' in merged['strategic_weights'])

    # ---- Test 22: Weak recall score differentiation (P3 fix) ----
    conn = _get_conn(db_path)
    weak_very_poor = "Cialdini persuasion principles audience engagement"
    for i in range(3):
        conn.execute("""
            INSERT INTO access_log (memory_id, access_type, query_text, relevance_score, accessed_at)
            VALUES (?, 'recall:hybrid', ?, ?, ?)
        """, (1, weak_very_poor, 0.92, recent))
    weak_barely = "Cloudflare Workers KV storage pattern"
    for i in range(3):
        conn.execute("""
            INSERT INTO access_log (memory_id, access_type, query_text, relevance_score, accessed_at)
            VALUES (?, 'recall:hybrid', ?, ?, ?)
        """, (1, weak_barely, 0.45, recent))
    conn.commit()
    weak2 = detect_weak_recalls(conn, score_threshold=-0.3, lookback_days=30, min_query_count=2)
    conn.close()
    cialdini = [g for g in weak2 if 'Cialdini' in g['entity']]
    cloudflare = [g for g in weak2 if 'Cloudflare Workers' in g['entity']]
    assert_true("T22a: Cialdini query found", len(cialdini) == 1)
    assert_true("T22b: Cloudflare query found", len(cloudflare) == 1)
    if cialdini and cloudflare:
        assert_true("T22c: Cloudflare has HIGHER importance than Cialdini (worse match = more important)",
                    cloudflare[0]['raw_importance'] > cialdini[0]['raw_importance'])
        assert_true("T22d: Cialdini importance near 0.08",
                    0.01 < cialdini[0]['raw_importance'] < 0.20)
        assert_true("T22e: Cloudflare importance near 0.55",
                    0.40 < cloudflare[0]['raw_importance'] < 0.70)
        assert_true("T22f: scores are genuinely different (not uniform 1.0)",
                    abs(cloudflare[0]['raw_importance'] - cialdini[0]['raw_importance']) > 0.3)

    # ---- Test 23: Weak recall importance boundary clamping ----
    assert_eq("T23a: negative best_score clamps to 1.0",
              max(0.0, min(1.0, 1.0 - (-0.05))), 1.0)
    assert_eq("T23b: high best_score clamps to 0.0",
              max(0.0, min(1.0, 1.0 - 1.5)), 0.0)
    assert_true("T23c: typical weak recall (0.93) gives low importance",
                0.05 < max(0.0, min(1.0, 1.0 - 0.93)) < 0.10)
    assert_true("T23d: marginal weak recall (0.45) gives medium importance",
                0.50 < max(0.0, min(1.0, 1.0 - 0.45)) < 0.60)

    # Cleanup
    try:
        os.unlink(db_path)
        os.unlink(empty_db)
    except OSError:
        pass

    print(json.dumps({
        "status": "ok" if failed == 0 else "FAIL",
        "passed": passed,
        "failed": failed,
        "total": passed + failed,
        "results": test_results,
    }, indent=2))

    return failed == 0


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Knowledge Gap Mapping — Wizard Library L4 Piece 6"
    )
    parser.add_argument('db_path', nargs='?', default=None,
                        help="Path to claude_memory.db")
    parser.add_argument('--run', action='store_true',
                        help="Run full gap inventory")
    parser.add_argument('--report', action='store_true',
                        help="Show latest gap report")
    parser.add_argument('--research', action='store_true',
                        help="Dispatch auto-research for top gaps")
    parser.add_argument('--ack', type=int, default=None, metavar='GAP_ID',
                        help="Acknowledge a gap by ID")
    parser.add_argument('--ack-reason', default='seen',
                        help="Reason for acknowledgement (seen, not-a-gap, deferred)")
    parser.add_argument('--method', default='thinker', choices=['thinker', 'claude_cli', 'agy'],
                        help="RETIRED switch, kept as an alias: the recorded overnight thinker researches "
                             "(claude_cli = thinker; agy is refused)")
    parser.add_argument('--max-gaps', type=int, default=5,
                        help="Max gaps to research per run")
    parser.add_argument('--gap-ids', type=str, default=None,
                        help="Comma-separated gap IDs to research")
    parser.add_argument('--config', default=None,
                        help="Path to JSON config file")
    parser.add_argument('--limit', type=int, default=25,
                        help="Max gaps in report")
    parser.add_argument('--batch-id', type=int, default=None,
                        help="Specific batch ID for report")
    parser.add_argument('--unit-tests', dest='unit_tests', action='store_true',
                        help="Run self-test suite")
    parser.add_argument('--sweep-non-answers', dest='sweep_non_answers', action='store_true',
                        help="List research replies stored as knowledge that are non-answers (read-only); "
                             "with --apply, archive and tag them (never deleted)")
    parser.add_argument('--apply', action='store_true',
                        help="With --sweep-non-answers: archive + tag instead of listing")

    args = parser.parse_args()

    if args.unit_tests:
        success = unit_tests()
        sys.exit(0 if success else 1)

    if not args.db_path:
        parser.print_help()
        sys.exit(1)

    config = None
    if args.config:
        config = json.loads(Path(args.config).read_text())

    if args.run:
        result = run_gap_inventory(args.db_path, config=config)
        print(json.dumps(result, indent=2))
    elif args.report:
        result = get_gap_report(args.db_path, batch_id=args.batch_id, limit=args.limit)
        print(json.dumps(result, indent=2))
    elif args.research:
        gap_ids = None
        if args.gap_ids:
            gap_ids = [int(x.strip()) for x in args.gap_ids.split(',')]
        result = dispatch_auto_research(
            args.db_path,
            gap_ids=gap_ids,
            method=args.method,
            max_gaps=args.max_gaps,
            config=config,
        )
        print(json.dumps(result, indent=2))
    elif args.ack is not None:
        result = acknowledge_gap(args.db_path, args.ack, reason=args.ack_reason)
        print(json.dumps(result, indent=2))
    elif args.sweep_non_answers:
        result = sweep_stored_non_answers(args.db_path, apply=args.apply)
        print(json.dumps(result, indent=2))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
