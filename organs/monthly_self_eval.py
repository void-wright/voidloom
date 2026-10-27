#!/usr/bin/env python3
"""Monthly Brain Self-Evaluation — Wizard Library L4 Piece 9.

Every month (1st, 8 AM local time), the brain evaluates ITSELF: how is each system
performing? What pieces work well? Which are underperforming? What new
capabilities should we consider? It produces a self-evaluation report with
a health scorecard, 3-5 proposed enhancements, 1-2 proposed deprecations,
and a threshold assessment. The owner reviews and approves. The brain never stops
planning to grow.

Public API
----------
``run_self_eval(db_path, month=None, config=None) -> dict``
    Main entry point. Runs all 9 self-eval queries, builds scorecard,
    generates proposals via ``claude -p`` in the declared persona, stores
    report. Returns the complete self-eval result.

``compute_health_scorecard(conn, month) -> dict``
    Query all system tables and compute per-piece health scores.

``generate_proposals(conn, scorecard, month, config=None) -> dict``
    Use LLM (``claude -p``) in the declared persona (if any) to generate
    enhancement and deprecation proposals from the scorecard.

``assess_threshold(conn, month) -> dict``
    Evaluate ACE threshold track record and recommend hold/increase.

``drew_review(db_path, report_id, approved, rejected) -> dict``
    Record the owner's approval/rejection decisions on a report (the name is
    kept for its callers; it is API, not a label).

``enact_approved(db_path, report_id, config=None) -> dict``
    Execute approved proposals: create memories, tasks, etc.

``get_report(db_path, month=None, report_id=None) -> dict``
    Retrieve the latest (or specific) self-eval report.

``is_migration_applied(conn) -> bool``
    Check if migration 021 has been applied.

``apply_migration(db_path, migration_sql_path=None) -> dict``
    Idempotent migration runner.

``unit_test_monthly_self_eval()``
    Self-contained unit tests (25+ tests).

Phase 0 design: memory #4083 (threshold evolution path).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# rho-1: the ONE env-directory resolver. Hard import by design -- it is
# stdlib-only and is laid by the same `cp -f "${PKG_ROOT}"/*.py` loop that
# lays this module, so its absence means an incomplete install, and an
# incomplete install must SAY SO rather than resolve sidecars elsewhere.
# A per-module inline fallback would recreate the 13-way divergence this
# module exists to end.
import voidloom_env
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Names a stranger's brain stores (generic since 2026-10-15) ----------------------------------------------------
# The pending-surface reflection's tag.  It used to carry the author's name (LEGACY_PENDING_SURFACE_TAGS); that tag is still
# READ (a brain that surfaced under it is refreshed, not doubled) and never written again.
PENDING_SURFACE_TAG = "owner-approval-pending"
LEGACY_PENDING_SURFACE_TAGS = ("drew-approval-pending",)

# A sleep-loop run that did not succeed.  'timeout' is contract C5's closing status for a run that hit its budget
# (migration 040): a month of timed-out nights graded with zero errors before it joined this set.
SLEEP_RUN_FAILED_STATUSES = ("error", "partial", "timeout")
_SLEEP_RUN_FAILED_SQL = "(" + ",".join("'%s'" % s for s in SLEEP_RUN_FAILED_STATUSES) + ")"

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


def _vl_ensure_vec(conn):
    """Load sqlite-vec onto THIS connection. Returns True if vec0 is usable.

    Every embeddings table in this brain is a vec0 VIRTUAL TABLE, so an INSERT
    into one fails with `no such module: vec0` on any connection that never
    loaded the extension. This module opens 23 raw ``sqlite3.connect(db_path)``
    connections and loaded the extension on NONE of them, so every memory it has
    ever written went in unfindable -- the write succeeded, the row is there, the
    content is right, and no vector path can ever reach it. The owner watched it
    happen eight times in five minutes on 2026-09-08 while approving report #5
    and report #7.

    It belongs HERE rather than at the 23 connect sites: this is the one funnel
    every memory this organ writes already passes through, and a fix at the
    funnel cannot be missed by a site added next month. Idempotent -- the probe
    is the cheap path when the extension is already loaded."""
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
        import logging as _l
        _l.getLogger("voidloom").error(
            "monthly_self_eval: sqlite-vec could not be loaded (%s: %s); memories "
            "written on this connection will be tagged unfindable, not embedded.",
            type(e).__name__, e)
        return False


def _vl_attach(conn, memory_id, text, organ, sources=None, transformation=None,
               attach=True):
    """TO-03 + TO-08 + TO-09: everything a freshly written memory owes the brain.

    embedding (or a queryable 'embedding-missing' mark) + belief_scan_queue entry
    + provenance edges when this organ knows its sources. Never silent.
    """
    if attach:
        _vl_ensure_vec(conn)
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

# R19/R21/R23. The decision ledger. Imported by the SAME two-step used above so
# self-eval works whether it is run as a script or imported. It is OPTIONAL by
# design: an un-migrated brain must still be able to run its monthly self-eval.
# Every place that would otherwise fall back is required to SAY SO in its result
# -- a stated absence, never a silent skip (the `os.path.exists` class).
_DU_IMPORT_ERROR: Optional[str] = None
try:
    import decision_units as _DU  # type: ignore
except ImportError as _e:                                  # pragma: no cover
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import decision_units as _DU  # type: ignore
    except ImportError as _e2:
        _DU = None  # type: ignore
        _DU_IMPORT_ERROR = f"{type(_e2).__name__}: {_e2}"


def _governance_kill_switch() -> bool:
    """True when VOIDLOOM_NO_GOVERNANCE is set (the L5-I-β governance
    kill-switch family). Lets the variance-gate in enact_approved be disabled
    process-wide exactly like the other organ kill-switches, WITHOUT a hard
    import dependency on governance.py (self-eval must stand alone)."""
    val = os.environ.get("VOIDLOOM_NO_GOVERNANCE")
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


# ============================================================
# Configuration
# ============================================================

DEFAULT_CONFIG: Dict[str, Any] = {
    # Scoring thresholds
    'health_score_excellent': 0.85,
    'health_score_good': 0.65,
    'health_score_concerning': 0.40,

    # Proposal generation
    'min_enhancements': 3,
    'max_enhancements': 5,
    'min_deprecations': 0,
    'max_deprecations': 2,

    # ACE threshold evolution (memory #4083)
    'current_threshold': 9.80,
    'threshold_evolution_path': [9.80, 9.85, 9.90, 9.95, 10.0],
    'min_months_at_threshold': 2,
    'min_pass_rate_for_increase': 0.90,

    # LLM generation
    'use_llm_proposals': True,
    'llm_timeout_seconds': 120,

    # Capacity headroom thresholds
    'db_size_warning_mb': 200,
    'db_size_critical_mb': 500,
    'edge_growth_warning_per_month': 5000,
    'embedding_time_warning_ms': 500,

    # Piece definitions: the canonical registry of all L2-L4 pieces.
    'piece_registry': {
        'L2-C1-hybrid-retrieval': {
            'table': None,
            'module': 'hybrid_retrieval',
            'description': 'FTS5 + vector + RRF + reranker pipeline',
            'deployed_month': '2026-01',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            'usage_kind': 'global_recall_proxy',
            'expected_per_month': None,
            'expectation_basis': 'no dedicated table; inferred from access_log',
        },
        'L2-C2-kind-taxonomy': {
            'table': None,
            'module': 'kind_taxonomy',
            'description': 'Episodic/semantic/procedural/reflection classification',
            'deployed_month': '2026-01',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            'usage_kind': 'global_recall_proxy',
            'expected_per_month': None,
            'expectation_basis': 'no dedicated table; inferred from access_log',
        },
        'L2-C3-sleep-loop': {
            'table': 'sleep_loop_runs',
            'module': 'sleep_loop',
            'description': 'Nightly consolidation cycle',
            'deployed_month': '2026-01',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            'usage_kind': 'organ_fires',
            'expected_per_month': 30,
            'expectation_basis': 'wizard-library-sleep-loop.timer daily 22:30',
        },
        'L2-C4-temporal-edges': {
            'table': 'memory_edges',
            'module': 'temporal_edges',
            'description': 'Semantic edge graph with predicates',
            'deployed_month': '2026-01',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            'usage_kind': 'product_rows',
            'expected_per_month': None,
            'expectation_basis': 'Stage 2 of the sleep cycle; rows-per-month never declared',
        },
        'L2-C5-procedural-memory': {
            'table': 'skill_usage_log',
            'module': 'procedural_memory',
            'description': 'Skill tracking and promotion pipeline',
            'deployed_month': '2026-02',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            'usage_kind': 'product_rows',
            'expected_per_month': None,
            'expectation_basis': 'written on skill use; rate is behavioural, not scheduled',
        },
        'L3-C1-persona-lenses': {
            'table': 'persona_recall_profiles',
            'module': 'persona_lenses',
            'description': 'persona recall lens profiles',
            'deployed_month': '2026-03',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            'usage_kind': 'standing_inventory',
            'expected_per_month': None,
            'expectation_basis': 'a COUNT of active profiles is not a rate',
        },
        'L3-C2-provenance-graph': {
            'table': 'memory_provenance',
            'module': 'provenance_graph',
            'description': 'Universal derivation audit trail',
            'deployed_month': '2026-03',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            'usage_kind': 'product_rows',
            'expected_per_month': None,
            'expectation_basis': 'no timer; written by organs that derive memories',
        },
        'L3-C3-skill-graduation': {
            'table': 'skill_graduation_log',
            'module': 'skill_graduation',
            'description': 'SEED -> ACTIVE -> PROTECTED pipeline',
            'deployed_month': '2026-03',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            # CORRECTED IN-ROUND by the calibration binding I had just written:
            # the first draft called this organ_fires with expected=4, and the very
            # first resolution came back ratio 41.5 (166 observed against 4). The
            # ratio was right and my CLASSIFICATION was wrong -- skill_graduation_log
            # holds one row per GRADUATION, not one per weekly fire. Reclassified to
            # product_rows with no declared expectation, because a graduations-per-
            # month figure is a behavioural rate nobody has ever declared, and
            # back-fitting one to the observation would be exactly the
            # declaration-binding this tier exists to end.
            'usage_kind': 'product_rows',
            'expected_per_month': None,
            'expectation_basis': 'one row per graduation, not per fire; rate never declared',
        },
        'L3-C4-anti-recency': {
            'table': 'resurfacing_log',
            'module': 'anti_recency',
            'description': 'Resurfacing old high-value memories',
            'deployed_month': '2026-03',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            # Same in-round correction, same instrument: resolution came back
            # ratio 2.5. resurfacing_log holds one row per RESURFACED MEMORY, not
            # one per weekly fire.
            'usage_kind': 'product_rows',
            'expected_per_month': None,
            'expectation_basis': 'one row per resurfaced memory, not per fire; rate never declared',
        },
        'L3-C5-whisper-network': {
            'table': 'whisper_observations',
            'module': 'whisper_network',
            'description': 'Self-observation and pattern detection',
            'deployed_month': '2026-04',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            'usage_kind': 'product_rows',
            'expected_per_month': None,
            'expectation_basis': 'observations are captured by hand during sessions',
        },
        'L3-C6-cognitive-map': {
            'table': 'map_generation_log',
            'module': 'cognitive_map',
            'description': 'Memory constellation visualization',
            'deployed_month': '2026-04',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            'usage_kind': 'organ_fires',
            'expected_per_month': 4,
            'expectation_basis': 'wizard-library-cognitive-map.timer weekly Sun',
        },
        'L3-C7-cross-tier-bridges': {
            'table': 'cross_tier_bridges',
            'module': 'cross_tier_bridges',
            'description': 'Inter-database memory linking',
            'deployed_month': '2026-04',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            'usage_kind': 'product_rows',
            'expected_per_month': None,
            'expectation_basis': 'discovery is CLI-only; no timer exists',
        },
        'L3-C8-embedding-upgrade': {
            'table': 'embedding_upgrade_log',
            'module': 'embedding_upgrade',
            'description': 'Embedding model A/B testing and migration',
            'deployed_month': '2026-04',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            'usage_kind': 'organ_fires',
            'expected_per_month': None,
            'expectation_basis': 'migration tool; fires only at a tier change',
        },
        'L4-P1-calibration-memory': {
            'table': 'calibration_log',
            'module': 'calibration_memory',
            'description': 'Prediction tracking and correction factors',
            'deployed_month': '2026-05',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            'usage_kind': 'product_rows',
            'expected_per_month': None,
            'expectation_basis': 'written when a prediction is logged',
        },
        'L4-P2-council': {
            'table': 'council_sessions',
            'module': 'council',
            'description': 'Inner Council deliberation framework',
            'deployed_month': '2026-05',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            'usage_kind': 'product_rows',
            'expected_per_month': None,
            'expectation_basis': 'convened by judgment, not by clock',
        },
        'L4-P3-pre-cognitive-recall': {
            'table': 'pre_cognitive_recall_log',
            'module': 'pre_cognitive_recall',
            'description': 'Anticipatory memory pre-loading',
            'deployed_month': '2026-05',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            'usage_kind': 'organ_fires',
            'expected_per_month': 840,
            'expectation_basis': 'wizard-library-pre-cognitive.timer 2x/hr, 08..21',
        },
        'L4-P5-memory-dreaming': {
            'table': 'dream_log',
            'module': 'memory_dreaming',
            'description': 'Divergent-pair creative synthesis',
            'deployed_month': '2026-05',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            'usage_kind': 'product_rows',
            'expected_per_month': None,
            'expectation_basis': 'one dream cycle nightly; dreams per cycle vary',
        },
        'L4-P6-gap-mapping': {
            'table': 'gap_inventory',
            'module': 'gap_mapping',
            'description': 'Knowledge gap detection and auto-research',
            'deployed_month': '2026-05',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            'usage_kind': 'product_rows',
            'expected_per_month': None,
            'expectation_basis': 'wizard-library-gap-mapping.timer weekly; gaps found vary',
        },
        'L4-P9-self-eval': {
            'table': 'self_eval_reports',
            'module': 'monthly_self_eval',
            'description': 'Monthly brain self-evaluation (this piece)',
            'deployed_month': '2026-05',
            # TO-04 (round gamma): what this piece's `usage` MEANS, and what a
            # healthy month looks like FOR THIS PIECE. expected_per_month=None
            # is a refusal to invent one -- it grades UNMEASURABLE, not zero.
            'usage_kind': 'organ_fires',
            'expected_per_month': 1,
            'expectation_basis': 'wizard-library-self-eval.timer monthly on the 1st',
        },
    },
}

# Pieces ordered by level for display
PIECE_ORDER = [
    'L2-C1-hybrid-retrieval', 'L2-C2-kind-taxonomy', 'L2-C3-sleep-loop',
    'L2-C4-temporal-edges', 'L2-C5-procedural-memory',
    'L3-C1-persona-lenses', 'L3-C2-provenance-graph', 'L3-C3-skill-graduation',
    'L3-C4-anti-recency', 'L3-C5-whisper-network', 'L3-C6-cognitive-map',
    'L3-C7-cross-tier-bridges', 'L3-C8-embedding-upgrade',
    'L4-P1-calibration-memory', 'L4-P2-council', 'L4-P3-pre-cognitive-recall',
    'L4-P5-memory-dreaming', 'L4-P6-gap-mapping', 'L4-P9-self-eval',
]


# ============================================================
# Migration helpers
# ============================================================

def is_migration_applied(conn: sqlite3.Connection) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='self_eval_reports'"
    )
    return cur.fetchone() is not None


def apply_migration(db_path: str, migration_sql_path: str | None = None) -> dict:
    if migration_sql_path is None:
        migration_sql_path = str(Path(__file__).parent.parent / "migrations" / "migration_021_self_eval.sql")
    sql_path = Path(migration_sql_path)
    if not sql_path.exists():
        return {"status": "error", "error": f"Migration file not found: {sql_path}"}
    conn = sqlite3.connect(db_path, timeout=60.0)
    try:
        if is_migration_applied(conn):
            return {"status": "ok", "action": "migration_already_applied"}
        conn.executescript(sql_path.read_text())
        conn.commit()
        return {"status": "ok", "action": "migration_applied"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        conn.close()


# ============================================================
# Health Scorecard: Query 1 — Piece-Level Health
# ============================================================

def _score_cron_piece(conn: sqlite3.Connection, table: str, month: str,
                      description: str,
                      piece_def: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Score a piece that has a runs/log table with timestamps and status.

    TO-04 (round gamma). The old tail was

        score = min(1.0, 0.6 + (usage / 100) * 0.4)

    -- one denominator, 100, applied to `usage`. VAEL measured that `usage` is
    SIX DIFFERENT REALIZED QUANTITIES here, across five row-meanings and three
    window modes, so the denominator was wrong for most of them and the ceiling
    was unreachable for all the weekly ones:

      1. organ_fires        one row per execution   (sleep_loop_runs)
      2. product_rows       one row per thing made  (memory_edges, dream_log, ...)
      3. standing_inventory a COUNT with NO window  (persona_recall_profiles:
                            dead 102 days, full marks forever -- KAEL-EM)
      4. month-equality     a different window mode (self_eval_reports.month = ?) --
                            retired 2026-10-26: now a created_at window like kind 1
      5. global_recall_proxy an access_log count IDENTICAL for every module-only
                            piece -- not the piece's own output at all
      6. absent-table zero  0.0 assigned without measuring anything

    A weekly organ tops out at 4 or 5 rows a month and scored 0.62 forever while
    a chatty one saturated at 1.0. Nothing was wrong with those organs.

    WHAT THIS FIX COVERS, EXACTLY: kinds 1, 2 and 4 -- the RATE kinds -- are
    normalised against the piece's OWN `expected_per_month`, which is declared
    beside the piece with the timer that justifies it. Kind 3 is given a window
    on its own clock. Kind 6 becomes UNMEASURABLE.

    WHAT IT DOES NOT COVER: kind 5. A single access_log count cannot be
    attributed to hybrid_retrieval rather than kind_taxonomy, and inventing a
    split would be worse than reporting the truth. Those two pieces now carry
    `shared_denominator: True` and say so in their notes. That is a disclosed
    residual, not a repair.

    AND THE REFUSAL THAT MATTERS MOST: where no expectation is declared, the
    score is UNMEASURABLE -- not 0.3, not 0.6. Six of nineteen pieces have a
    timer that justifies a number; thirteen do not, and inventing thirteen
    numbers here would be declaration-binding, which is the failure this whole
    tier exists to end.
    """
    month_start = f"{month}-01"
    try:
        next_m = int(month.split('-')[1]) + 1
        next_y = int(month.split('-')[0])
        if next_m > 12:
            next_m = 1
            next_y += 1
        month_end = f"{next_y:04d}-{next_m:02d}-01"
    except (IndexError, ValueError):
        month_end = month_start

    result: Dict[str, Any] = {
        'description': description,
        'usage': 0,
        'errors': 0,
        'score': 0.0,
        'notes': '',
    }

    if table == 'sleep_loop_runs':
        cur = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN status IN " + _SLEEP_RUN_FAILED_SQL + " THEN 1 ELSE 0 END) "
            "FROM sleep_loop_runs WHERE started_at >= ? AND started_at < ?",
            (month_start, month_end)
        )
        row = cur.fetchone()
        result['usage'] = row[0] or 0
        result['errors'] = row[1] or 0

    elif table == 'memory_edges':
        cur = conn.execute(
            "SELECT COUNT(*) FROM memory_edges WHERE created_at >= ? AND created_at < ?",
            (month_start, month_end)
        )
        result['usage'] = (cur.fetchone()[0] or 0)
        result['errors'] = 0

    elif table == 'skill_usage_log':
        cur = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN outcome = 'failure' THEN 1 ELSE 0 END) "
            "FROM skill_usage_log WHERE used_at >= ? AND used_at < ?",
            (month_start, month_end)
        )
        row = cur.fetchone()
        result['usage'] = row[0] or 0
        result['errors'] = row[1] or 0

    elif table == 'persona_recall_profiles':
        # TO-04 kind 3. `COUNT(*) WHERE is_active = 1` has NO TIME DIMENSION: the
        # five rows written on 2026-05-28 scored full marks every month while the
        # organ was dead for 102 days. The table HAS an `updated_at` column (it
        # always did; alpha's clock work confirmed it), so the piece now reports
        # BOTH -- the inventory it owns and whether that inventory MOVED this
        # month -- and the score below reads the movement, not the census.
        cur = conn.execute("SELECT COUNT(*) FROM persona_recall_profiles WHERE is_active = 1")
        result['inventory'] = cur.fetchone()[0] or 0
        try:
            cur = conn.execute(
                "SELECT COUNT(*) FROM persona_recall_profiles "
                "WHERE updated_at >= ? AND updated_at < ?", (month_start, month_end))
            result['usage'] = cur.fetchone()[0] or 0
        except sqlite3.OperationalError:
            result['usage'] = None            # UNMEASURABLE, not zero
            result['notes'] = 'persona_recall_profiles has no updated_at column'
        result['errors'] = 0

    elif table == 'memory_provenance':
        cur = conn.execute(
            "SELECT COUNT(*) FROM memory_provenance WHERE created_at >= ? AND created_at < ?",
            (month_start, month_end)
        )
        result['usage'] = cur.fetchone()[0] or 0
        result['errors'] = 0

    elif table == 'skill_graduation_log':
        cur = conn.execute(
            "SELECT COUNT(*) FROM skill_graduation_log WHERE graduated_at >= ? AND graduated_at < ?",
            (month_start, month_end)
        )
        result['usage'] = cur.fetchone()[0] or 0
        result['errors'] = 0

    elif table == 'resurfacing_log':
        cur = conn.execute(
            "SELECT COUNT(*) FROM resurfacing_log WHERE resurfaced_at >= ? AND resurfaced_at < ?",
            (month_start, month_end)
        )
        result['usage'] = cur.fetchone()[0] or 0
        result['errors'] = 0

    elif table == 'whisper_observations':
        cur = conn.execute(
            "SELECT COUNT(*) FROM whisper_observations WHERE created_at >= ? AND created_at < ?",
            (month_start, month_end)
        )
        result['usage'] = cur.fetchone()[0] or 0
        result['errors'] = 0

    elif table == 'map_generation_log':
        cur = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN error_log IS NOT NULL AND error_log != '' THEN 1 ELSE 0 END) "
            "FROM map_generation_log WHERE generated_at >= ? AND generated_at < ?",
            (month_start, month_end)
        )
        row = cur.fetchone()
        result['usage'] = row[0] or 0
        result['errors'] = row[1] or 0

    elif table == 'cross_tier_bridges':
        cur = conn.execute(
            "SELECT COUNT(*) FROM cross_tier_bridges WHERE created_at >= ? AND created_at < ?",
            (month_start, month_end)
        )
        result['usage'] = cur.fetchone()[0] or 0
        result['errors'] = 0

    elif table == 'embedding_upgrade_log':
        cur = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) "
            "FROM embedding_upgrade_log WHERE started_at >= ? AND started_at < ?",
            (month_start, month_end)
        )
        row = cur.fetchone()
        result['usage'] = row[0] or 0
        result['errors'] = row[1] or 0

    elif table == 'calibration_log':
        cur = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN ratio IS NOT NULL THEN 1 ELSE 0 END) "
            "FROM calibration_log WHERE created_at >= ? AND created_at < ?",
            (month_start, month_end)
        )
        row = cur.fetchone()
        result['usage'] = row[0] or 0
        result['errors'] = 0
        resolved = row[1] or 0
        if result['usage'] > 0:
            result['notes'] = f"{resolved}/{result['usage']} predictions resolved"

    elif table == 'council_sessions':
        cur = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN status = 'abandoned' THEN 1 ELSE 0 END) "
            "FROM council_sessions WHERE created_at >= ? AND created_at < ?",
            (month_start, month_end)
        )
        row = cur.fetchone()
        result['usage'] = row[0] or 0
        result['errors'] = row[1] or 0

    elif table == 'pre_cognitive_recall_log':
        cur = conn.execute(
            "SELECT COUNT(*) FROM pre_cognitive_recall_log WHERE created_at >= ? AND created_at < ?",
            (month_start, month_end)
        )
        result['usage'] = cur.fetchone()[0] or 0
        result['errors'] = 0

    elif table == 'dream_log':
        cur = conn.execute(
            "SELECT COUNT(*), SUM(is_meaningful), SUM(drew_flagged_interesting) "
            "FROM dream_log WHERE created_at >= ? AND created_at < ?",
            (month_start, month_end)
        )
        row = cur.fetchone()
        result['usage'] = row[0] or 0
        meaningful = row[1] or 0
        flagged = row[2] or 0
        result['errors'] = 0
        if result['usage'] > 0:
            result['notes'] = f"{meaningful} meaningful, {flagged} flagged interesting"

    elif table == 'gap_inventory':
        cur = conn.execute(
            "SELECT COUNT(*) FROM gap_inventory WHERE inventoried_at >= ? AND inventoried_at < ?",
            (month_start, month_end)
        )
        result['usage'] = cur.fetchone()[0] or 0
        cur2 = conn.execute(
            "SELECT COUNT(*) FROM gap_inventory WHERE inventoried_at >= ? AND inventoried_at < ? AND researched = 1",
            (month_start, month_end)
        )
        researched = cur2.fetchone()[0] or 0
        result['errors'] = 0
        if result['usage'] > 0:
            result['notes'] = f"{researched}/{result['usage']} gaps researched"

    elif table == 'self_eval_reports':
        # A FIRE is an execution inside the month, like every other organ_fires
        # piece. The old `WHERE month = ?` counted reports ABOUT the month, and the
        # report about this month is the one being written now -- so the self-eval
        # scored itself 0 and proposed "Activate L4-P9-self-eval" every run
        # (report #8, 2026-10-01). Measured by when it ran, it ran.
        cur = conn.execute(
            "SELECT COUNT(*) FROM self_eval_reports WHERE created_at >= ? AND created_at < ?",
            (month_start, month_end)
        )
        result['usage'] = cur.fetchone()[0] or 0
        result['errors'] = 0

    else:
        result['notes'] = f'Unknown table: {table}'

    usage = result['usage']
    errors = result['errors']
    pd = piece_def or {}
    kind = pd.get('usage_kind', 'product_rows')
    expected = pd.get('expected_per_month')
    result['usage_kind'] = kind
    result['expected_per_month'] = expected
    result['expectation_basis'] = pd.get('expectation_basis')

    def _note(txt):
        result['notes'] = (result['notes'] + ' ' if result['notes'] else '') + txt

    if usage is None:
        # The measurement itself failed. (0,False) -- I could not tell.
        result['score'] = None
        result['measured'] = False
        _note('UNMEASURABLE: the usage query could not run.')
        return result

    result['measured'] = True

    if errors and usage:
        # An error RATE is meaningful without any expectation, so it is scored
        # first and unchanged: a piece that ran and failed is failing regardless
        # of how often it was supposed to run.
        result['score'] = max(0.1, 0.8 * (1.0 - errors / usage))
        _note(f'{errors} of {usage} runs errored.')
        return result

    if expected is None:
        # NO EXPECTATION DECLARED -> NO SCORE. This is the refusal. The old code
        # scored 0.3 for "no activity", which reads as a graded verdict on the
        # organ when it is really a verdict on the absence of a declaration.
        result['score'] = None
        result['measured'] = usage is not None
        _note(f'UNMEASURABLE against a monthly rate: no expected_per_month is '
              f'declared for this piece ({pd.get("expectation_basis") or "no basis recorded"}). '
              f'Observed {usage} rows this month.')
        return result

    if expected <= 0:
        result['score'] = None
        _note('UNMEASURABLE: expected_per_month must be positive.')
        return result

    ratio = usage / float(expected)
    # 0.6 is the floor for an organ that produced ANYTHING, 1.0 at or above its
    # own expectation -- the same shape as before, with the piece's own
    # denominator instead of a universal 100.
    result['ratio_to_expected'] = round(ratio, 3)
    if usage == 0:
        result['score'] = 0.0
        _note(f'ZERO against an expectation of {expected}/month '
              f'({pd.get("expectation_basis")}). This organ did not fire.')
    else:
        result['score'] = round(min(1.0, 0.6 + 0.4 * min(1.0, ratio)), 3)
        _note(f'{usage} of an expected {expected}/month (ratio {ratio:.2f}).')
    return result


def _score_module_only_piece(conn: sqlite3.Connection, module_name: str,
                             month: str, description: str,
                             piece_def: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Score a piece that has no dedicated table (e.g., hybrid_retrieval, kind_taxonomy).
    These are always-on infrastructure; health is inferred from recall activity."""
    month_start = f"{month}-01"
    try:
        next_m = int(month.split('-')[1]) + 1
        next_y = int(month.split('-')[0])
        if next_m > 12:
            next_m = 1
            next_y += 1
        month_end = f"{next_y:04d}-{next_m:02d}-01"
    except (IndexError, ValueError):
        month_end = month_start

    recall_count = 0
    cur_check = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='access_log'")
    if cur_check.fetchone() is not None:
        cur = conn.execute(
            "SELECT COUNT(*) FROM access_log WHERE accessed_at >= ? AND accessed_at < ? "
            "AND access_type NOT LIKE 'recall:%-lead'",
            (month_start, month_end)
        )
        recall_count = cur.fetchone()[0] or 0

    score = 0.5
    if recall_count >= 100:
        score = 0.95
    elif recall_count >= 50:
        score = 0.85
    elif recall_count >= 10:
        score = 0.70
    elif recall_count >= 1:
        score = 0.55

    return {
        'description': description,
        'usage': recall_count,
        'errors': 0,
        'score': score,
        'measured': True,
        'usage_kind': (piece_def or {}).get('usage_kind', 'global_recall_proxy'),
        'expected_per_month': None,
        # TO-04 kind 5, DISCLOSED NOT REPAIRED. This number is a GLOBAL access_log
        # count. Every module-only piece receives the identical figure, so the
        # score below distinguishes "the brain was used" from "the brain was
        # unused" and distinguishes nothing whatsoever between the pieces. A
        # split would have to be invented, and an invented attribution is worse
        # than a labelled shared one.
        'shared_denominator': True,
        'notes': (f'Inferred from {recall_count} recall queries (module has no dedicated '
                  f'log table). SHARED DENOMINATOR: this figure is global and is identical '
                  f'for every module-only piece; it does not distinguish this piece from '
                  f'any other.'),
    }


def _merge_config(config: Dict[str, Any] | None) -> Dict[str, Any]:
    """DEFAULT_CONFIG overlaid with `config`, MERGING nested dicts key by key.

    R30 -- A PARTIAL CONFIG MUST NOT REPLACE WHAT IT DOES NOT NAME. Measured
    2026-09-02 (VAEL, one surface) and reproduced 2026-09-03 across THREE: with
    `{**DEFAULT_CONFIG, **(config or {})}` a `--config` naming ONE organ
    replaced the whole 19-organ `piece_registry`. The blast radius of that one
    plausible owner edit, measured through the product's own verbs:

      * `compute_health_scorecard` scored 1 organ instead of 19;
      * the PROPOSAL-layer protected-organ guard stopped seeing every unnamed
        organ as registered;
      * the ENACT-layer guard (#6248, defence-in-depth) went from blocked=1 to
        blocked=0 and ENACTED a deprecation of a protected organ.

    PROTECTION HERE IS THE DEFAULT OF AN ABSENT FIELD -- `deprecatable` appears
    nowhere in DEFAULT_CONFIG -- so a registry entry that vanishes does not
    become unprotected loudly; it becomes an UNREGISTERED piece, which the guard
    waves through. A shallow merge therefore turns "the owner adjusted one
    organ" into "the owner deleted the other eighteen", silently, in the
    direction of LESS protection.

    MERGE, RECURSIVELY, AND ONLY FOR DICTS. A nested dict is merged key by key
    at every depth, so an owner editing one organ's entry keeps that organ's
    `module` / `table` / `deployed_month` as well as the other eighteen organs.
    Non-dict values REPLACE, lists included: a list is one value, and merging
    `threshold_evolution_path` element-wise would silently concatenate an
    owner's shorter path onto the default instead of replacing it.

    The owner can still change anything by NAMING it -- setting
    `deprecatable: true` on an organ still works, and that is deliberate: the
    protected list is owner-changeable by design. What is no longer possible is
    changing something by NOT naming it.
    """
    def _deep(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(base)
        for k, v in over.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = _deep(out[k], v)
            else:
                out[k] = v
        return out
    return _deep(DEFAULT_CONFIG, config or {})



# ============================================================
# TO-12 — calibration_memory's first organic writer
# ============================================================

def calibrate_expectations(conn: sqlite3.Connection, month: str,
                           scorecard: Dict[str, Any],
                           registry: Dict[str, Any]) -> Dict[str, Any]:
    """Grade last month's expectations against what actually happened, then
    record this month's expectations as predictions.

    WHY HERE AND NOWHERE ELSE (TO-12). `calibration_memory.log_prediction` has 22
    call sites and every one of them is inside `unit_test_calibration_memory`.
    Zero modules import the module. `calibration_log` holds 4 rows sharing ONE
    timestamp -- a hand seed, never a heartbeat. The piece is a prediction ledger
    with no predictor.

    (The census recorded this as "both `predictions` and `calibration_predictions`
    tables are absent". They are absent, and they are also not the tables this
    module writes: it writes `calibration_log`, which EXISTS. The defect is a
    missing writer, not a missing table.)

    The binding has to be a real prediction someone would want graded, or it is
    ceremony. This one is: TO-04 gives each piece an `expected_per_month`, and an
    expectation nobody ever checks is exactly the declaration-binding failure this
    tier exists to end. So the expectation becomes the PREDICTION and next month's
    observed `usage` becomes the OUTCOME. If a declared expectation is wrong, the
    calibration ratio says so, in the organ's own ledger, every month, forever.

    Best-effort throughout: this is an audit trail, never a gate. Every skip is
    counted and named in the returned dict rather than passed over.
    """
    out = {"resolved": 0, "logged": 0, "skipped": [], "state": "ok"}

    # A PARTIAL MONTH MUST NOT RESOLVE. Found in-round, by running two cycles
    # back to back: scoring 2026-09 on the 5th resolved sleep_loop at ratio 0.133
    # -- 4 observed against 30 expected -- which is not a miscalibration, it is
    # five days of a thirty-day month. Feeding that to a correction factor would
    # teach the ledger a chronic overestimate that does not exist, and the
    # correction would then be applied to future estimates. Predictions are still
    # LOGGED for the current month; only RESOLUTION waits for the month to close.
    _today = datetime.now().strftime("%Y-%m")
    _month_complete = month < _today
    out["month_complete"] = _month_complete
    if not _month_complete:
        out["resolution_deferred"] = (
            f"{month} is the current month; resolving a partial month would "
            f"record a false shortfall. Predictions logged, resolution deferred.")
    try:
        import sys as _s, os as _o
        _d = _o.path.dirname(_o.path.abspath(__file__))
        if _d not in _s.path:
            _s.path.insert(0, _d)
        import calibration_memory as _cal
    except Exception as e:
        out["state"] = "UNMEASURABLE"
        out["detail"] = f"calibration_memory unavailable: {e}"
        return out
    try:
        conn.execute("SELECT 1 FROM calibration_log LIMIT 1")
    except sqlite3.Error as e:
        out["state"] = "UNMEASURABLE"
        out["detail"] = f"calibration_log unreadable: {e}"
        return out

    for piece_name, piece_def in registry.items():
        expected = piece_def.get("expected_per_month")
        if expected is None:
            out["skipped"].append((piece_name, "no expectation declared"))
            continue
        domain = f"organ-yield/{piece_name}"
        card = scorecard.get(piece_name) or {}
        observed = card.get("usage")

        # --- resolve the OPEN prediction for this domain, if there is one ----
        try:
            row = conn.execute(
                "SELECT id FROM calibration_log WHERE domain = ? AND actual_value IS NULL "
                "ORDER BY id DESC LIMIT 1", (domain,)).fetchone()
        except sqlite3.Error:
            row = None
        if row is not None and not _month_complete:
            out["skipped"].append((piece_name, "month incomplete; resolution deferred"))
        elif row is not None:
            if observed is None:
                out["skipped"].append((piece_name, "outcome UNMEASURABLE; prediction left open"))
            elif observed <= 0:
                # log_outcome REFUSES a non-positive actual (calibration_memory.py:254).
                # A dead organ genuinely produced zero, and faking an epsilon to get
                # past the guard would put a number in the ledger that nobody
                # observed. The prediction stays open and the reason is reported.
                out["skipped"].append((piece_name, f"observed 0; log_outcome refuses "
                                                   f"a non-positive actual, prediction left open"))
            else:
                try:
                    _cal.log_outcome(conn, row[0], float(observed),
                                     notes=f"observed in {month}")
                    out["resolved"] += 1
                except Exception as e:
                    out["skipped"].append((piece_name, f"log_outcome failed: {e}"))

        # --- log THIS month's expectation as the next prediction -------------
        try:
            _cal.log_prediction(
                conn,
                prediction_value=float(expected),
                prediction_unit="count",
                prediction_kind="throughput",
                domain=domain,
                context=(f"{piece_name}: expected {expected} rows/month "
                         f"({piece_def.get('expectation_basis')})"),
                session_id=f"self-eval-{month}",
                tags="organ-yield,self-eval,expected-per-month",
            )
            out["logged"] += 1
        except Exception as e:
            out["skipped"].append((piece_name, f"log_prediction failed: {e}"))
    return out

def compute_health_scorecard(conn: sqlite3.Connection, month: str,
                             config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Query all system tables and compute per-piece health scores."""
    cfg = _merge_config(config)          # R30: MERGE, never replace
    registry = cfg['piece_registry']
    scorecard: Dict[str, Any] = {}

    for piece_name in PIECE_ORDER:
        if piece_name not in registry:
            continue
        piece_def = registry[piece_name]
        table = piece_def.get('table')
        module_name = piece_def.get('module', '')
        description = piece_def.get('description', '')

        if table is not None:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,)
            )
            if cur.fetchone() is None:
                # TO-04 kind 6. A table that does not exist is (0,True) -- I
                # looked and there is nothing to look AT. Scoring it 0.0 puts a
                # graded verdict on an organ that was never deployed, and it is
                # indistinguishable from an organ that ran and produced nothing.
                scorecard[piece_name] = {
                    'description': description,
                    'usage': None, 'errors': 0, 'score': None, 'measured': False,
                    'usage_kind': piece_def.get('usage_kind'),
                    'notes': f'UNMEASURABLE: table {table} does not exist '
                             f'(piece not deployed). This is not a score of zero.',
                }
                continue
            scorecard[piece_name] = _score_cron_piece(conn, table, month, description,
                                                     piece_def=piece_def)
        else:
            scorecard[piece_name] = _score_module_only_piece(conn, module_name, month,
                                                            description, piece_def=piece_def)

    return scorecard


# ============================================================
# Health Scorecard: Query 2 — Calibration Drift
# ============================================================

def _compute_calibration_drift(conn: sqlite3.Connection, month: str) -> Dict[str, Any]:
    """Are predictions getting better or worse?"""
    month_start = f"{month}-01"
    try:
        next_m = int(month.split('-')[1]) + 1
        next_y = int(month.split('-')[0])
        if next_m > 12:
            next_m = 1
            next_y += 1
        month_end = f"{next_y:04d}-{next_m:02d}-01"
    except (IndexError, ValueError):
        month_end = month_start

    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='calibration_log'"
    )
    if cur.fetchone() is None:
        return {'status': 'table_missing', 'drift': None, 'sample_size': 0}

    cur = conn.execute(
        "SELECT ratio FROM calibration_log WHERE ratio IS NOT NULL "
        "AND actual_at >= ? AND actual_at < ? ORDER BY actual_at",
        (month_start, month_end)
    )
    ratios = [r[0] for r in cur.fetchall()]

    if len(ratios) < 2:
        return {
            'status': 'insufficient_data',
            'drift': None,
            'sample_size': len(ratios),
            'ratios': ratios,
        }

    mean_ratio = sum(ratios) / len(ratios)
    mean_abs_error = sum(abs(r - 1.0) for r in ratios) / len(ratios)
    improving = None
    if len(ratios) >= 4:
        first_half = ratios[:len(ratios)//2]
        second_half = ratios[len(ratios)//2:]
        first_mae = sum(abs(r - 1.0) for r in first_half) / len(first_half)
        second_mae = sum(abs(r - 1.0) for r in second_half) / len(second_half)
        improving = second_mae < first_mae

    return {
        'status': 'ok',
        'sample_size': len(ratios),
        'mean_ratio': round(mean_ratio, 4),
        'mean_absolute_error': round(mean_abs_error, 4),
        'improving': improving,
        'drift': 'improving' if improving else ('worsening' if improving is False else 'insufficient_data'),
    }


# ============================================================
# Belief-Drift Audit (L5-I-β) — the FOUNDATION SET ONLY
# ============================================================

#: The foundation set: a memory with foundation_score >= this is a trust anchor.
#: We audit drift over ONLY these few-dozen stable rows. The broad belief graph
#: (thousands of edges) would be alarm-fatigue — the calm doctrine. 0.9 yields
#: ~a few dozen anchors on the live corpus (the "Stilled Library").
BELIEF_DRIFT_FOUNDATION_THRESHOLD = 0.90


def _compute_belief_drift(conn: sqlite3.Connection, month: str,
                          foundation_threshold: float | None = None) -> Dict[str, Any]:
    """L5-I-β: is the brain's bedrock shifting under it? (a sibling of
    ``_compute_calibration_drift``).

    Audits, over the FOUNDATION SET ONLY (foundation_score >= threshold — a few
    dozen stable rows), how many of those trust-anchor memories had a NEW
    contradiction asserted against them during the month. A foundation memory
    acquiring a fresh contradiction is "productive doubt" the monthly report
    should surface — but bounded to the bedrock, never the whole graph (which
    would be alarm-fatigue, against the calm doctrine).

    Joined into the existing ``run_self_eval`` scorecard (the supplemental dict)
    → the existing monthly report. NO new report surface.

    Fail-graceful: a missing table / column degrades to a benign status dict,
    never raises (mirrors the other _compute_* siblings).
    """
    thr = (BELIEF_DRIFT_FOUNDATION_THRESHOLD if foundation_threshold is None
           else foundation_threshold)

    # The foundation set itself (always computable from the memories table).
    try:
        foundation_n = conn.execute(
            "SELECT COUNT(*) FROM memories "
            "WHERE foundation_score IS NOT NULL AND foundation_score >= ?",
            (thr,),
        ).fetchone()[0] or 0
    except sqlite3.Error:
        return {'status': 'table_missing', 'drift': None,
                'foundation_set_size': 0, 'sample_size': 0}

    # The month window (same arithmetic as _compute_calibration_drift).
    month_start = f"{month}-01"
    try:
        next_m = int(month.split('-')[1]) + 1
        next_y = int(month.split('-')[0])
        if next_m > 12:
            next_m = 1
            next_y += 1
        month_end = f"{next_y:04d}-{next_m:02d}-01"
    except (IndexError, ValueError):
        month_end = month_start

    # The contradiction edges live in temporal_edges (the belief engine's graph).
    # If that table is absent, we still report the foundation-set size (the audit
    # degrades gracefully rather than vanishing).
    edge_table = None
    for cand in ('temporal_edges', 'belief_edges', 'memory_edges'):
        try:
            if conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (cand,),
            ).fetchone() is not None:
                edge_table = cand
                break
        except sqlite3.Error:
            continue
    if edge_table is None:
        return {'status': 'edge_table_missing', 'drift': None,
                'foundation_set_size': foundation_n, 'sample_size': 0,
                'foundation_threshold': thr}

    # Which columns does the edge table actually carry? (defensive)
    try:
        ecols = {r[1] for r in conn.execute(
            f"PRAGMA table_info({edge_table})").fetchall()}
    except sqlite3.Error:
        ecols = set()
    # endpoint columns + predicate + created_at, with sensible fallbacks.
    src_col = 'source_id' if 'source_id' in ecols else (
        'source_memory_id' if 'source_memory_id' in ecols else None)
    tgt_col = 'target_id' if 'target_id' in ecols else (
        'target_memory_id' if 'target_memory_id' in ecols else None)
    pred_col = ('predicate' if 'predicate' in ecols else (
        'relationship_type' if 'relationship_type' in ecols else (
            'edge_type' if 'edge_type' in ecols else None)))
    has_created = 'created_at' in ecols
    has_valid_until = 'valid_until' in ecols
    if src_col is None or tgt_col is None:
        return {'status': 'edge_schema_unknown', 'drift': None,
                'foundation_set_size': foundation_n, 'sample_size': 0,
                'foundation_threshold': thr}

    # Build the contradiction-against-foundation query. Honor valid_until IS NULL
    # (a soft-retired contradiction is not a live drift signal — the L5-D
    # bi-temporal contract). Count DISTINCT foundation memories acquiring a fresh
    # contradiction in the window.
    pred_clause = ""
    params: list[Any] = [thr]
    if pred_col is not None:
        pred_clause = f" AND e.{pred_col} = 'contradicts'"
    valid_clause = " AND e.valid_until IS NULL" if has_valid_until else ""
    # A foundation memory can be either endpoint of a contradicts edge.
    base_q = (
        f"SELECT COUNT(DISTINCT m.id) FROM memories m "
        f"JOIN {edge_table} e ON (e.{src_col} = m.id OR e.{tgt_col} = m.id) "
        f"WHERE m.foundation_score IS NOT NULL AND m.foundation_score >= ?"
        f"{pred_clause}{valid_clause}"
    )
    window_q = base_q
    params_window = list(params)
    if has_created:
        window_q = base_q + " AND e.created_at >= ? AND e.created_at < ?"
        params_window = params + [month_start, month_end]

    try:
        total_contradicted = conn.execute(base_q, params).fetchone()[0] or 0
        new_contradicted = conn.execute(window_q, params_window).fetchone()[0] or 0
    except sqlite3.Error as exc:
        return {'status': 'query_error', 'drift': None, 'error': str(exc),
                'foundation_set_size': foundation_n, 'sample_size': 0,
                'foundation_threshold': thr}

    # Drift verdict: ANY new contradiction against the bedrock is worth a look;
    # the magnitude relative to the foundation set sizes the concern.
    frac = (new_contradicted / foundation_n) if foundation_n else 0.0
    if new_contradicted == 0:
        drift = 'stable'
    elif frac >= 0.10:
        drift = 'shifting'   # >=10% of the bedrock newly contested this month
    else:
        drift = 'minor'

    return {
        'status': 'ok',
        'foundation_threshold': thr,
        'foundation_set_size': foundation_n,
        'foundation_contradicted_total': total_contradicted,
        'foundation_contradicted_this_month': new_contradicted,
        'fraction_contested': round(frac, 4),
        'sample_size': new_contradicted,
        'drift': drift,
        'note': ('audited over the foundation set only (the few-dozen trust '
                 'anchors), never the whole belief graph — the calm doctrine'),
    }


# ============================================================
# Health Scorecard: Query 3 — Recall Quality (Gap Inventory)
# ============================================================

def _compute_gap_trend(conn: sqlite3.Connection, month: str) -> Dict[str, Any]:
    """Have unresolved gaps grown or shrunk?"""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='gap_inventory'"
    )
    if cur.fetchone() is None:
        return {'status': 'table_missing', 'trend': None}

    month_start = f"{month}-01"
    try:
        next_m = int(month.split('-')[1]) + 1
        next_y = int(month.split('-')[0])
        if next_m > 12:
            next_m = 1
            next_y += 1
        month_end = f"{next_y:04d}-{next_m:02d}-01"
    except (IndexError, ValueError):
        month_end = month_start

    cur = conn.execute(
        "SELECT COUNT(*) FROM gap_inventory WHERE inventoried_at < ? AND researched = 0 AND acknowledged = 0",
        (month_start,)
    )
    unresolved_before = cur.fetchone()[0] or 0

    cur = conn.execute(
        "SELECT COUNT(*) FROM gap_inventory WHERE inventoried_at < ? AND researched = 0 AND acknowledged = 0",
        (month_end,)
    )
    unresolved_after = cur.fetchone()[0] or 0

    delta = unresolved_after - unresolved_before
    trend = 'stable'
    if delta > 10:
        trend = 'growing'
    elif delta < -5:
        trend = 'shrinking'

    return {
        'status': 'ok',
        'unresolved_before': unresolved_before,
        'unresolved_after': unresolved_after,
        'delta': delta,
        'trend': trend,
    }


# ============================================================
# Health Scorecard: Query 4 — Cron Health
# ============================================================

def _compute_cron_health(conn: sqlite3.Connection, month: str) -> Dict[str, Any]:
    """Did all cron jobs fire successfully?"""
    month_start = f"{month}-01"
    try:
        next_m = int(month.split('-')[1]) + 1
        next_y = int(month.split('-')[0])
        if next_m > 12:
            next_m = 1
            next_y += 1
        month_end = f"{next_y:04d}-{next_m:02d}-01"
    except (IndexError, ValueError):
        month_end = month_start

    # Each table's own failure statuses: a sleep run can also close 'timeout' (contract C5); the others cannot.
    cron_tables = {
        'sleep_loop': ('sleep_loop_runs', 'started_at', 'status', _SLEEP_RUN_FAILED_SQL),
        'gap_mapping': ('gap_inventory_batches', 'started_at', 'status', "('error','partial')"),
        'dreaming': ('dream_batches', 'created_at', None, None),
    }

    results = {}
    for name, (table, ts_col, status_col, failed_sql) in cron_tables.items():
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        if cur.fetchone() is None:
            results[name] = {'runs': 0, 'errors': 0, 'status': 'table_missing'}
            continue

        if status_col:
            cur = conn.execute(
                f"SELECT COUNT(*), SUM(CASE WHEN {status_col} IN {failed_sql} THEN 1 ELSE 0 END) "
                f"FROM {table} WHERE {ts_col} >= ? AND {ts_col} < ?",
                (month_start, month_end)
            )
            row = cur.fetchone()
            results[name] = {
                'runs': row[0] or 0,
                'errors': row[1] or 0,
                'status': 'healthy' if (row[1] or 0) == 0 and (row[0] or 0) > 0 else (
                    'no_runs' if (row[0] or 0) == 0 else 'degraded'
                ),
            }
        else:
            cur = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {ts_col} >= ? AND {ts_col} < ?",
                (month_start, month_end)
            )
            count = cur.fetchone()[0] or 0
            results[name] = {
                'runs': count,
                'errors': 0,
                'status': 'healthy' if count > 0 else 'no_runs',
            }

    all_healthy = all(r['status'] == 'healthy' for r in results.values())
    any_error = any(r['status'] == 'degraded' for r in results.values())

    return {
        'status': 'ok',
        'cron_jobs': results,
        'overall': 'healthy' if all_healthy else ('degraded' if any_error else 'partial'),
    }


# ============================================================
# Health Scorecard: Query 5 — Dream Signal-to-Noise
# ============================================================

def _compute_dream_quality(conn: sqlite3.Connection, month: str) -> Dict[str, Any]:
    """How many dreams were flagged interesting?"""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='dream_log'"
    )
    if cur.fetchone() is None:
        return {'status': 'table_missing'}

    month_start = f"{month}-01"
    try:
        next_m = int(month.split('-')[1]) + 1
        next_y = int(month.split('-')[0])
        if next_m > 12:
            next_m = 1
            next_y += 1
        month_end = f"{next_y:04d}-{next_m:02d}-01"
    except (IndexError, ValueError):
        month_end = month_start

    cur = conn.execute(
        "SELECT COUNT(*), SUM(is_meaningful), SUM(drew_flagged_interesting) "
        "FROM dream_log WHERE created_at >= ? AND created_at < ?",
        (month_start, month_end)
    )
    row = cur.fetchone()
    total = row[0] or 0
    meaningful = row[1] or 0
    flagged = row[2] or 0

    signal_ratio = meaningful / total if total > 0 else 0.0
    drew_ratio = flagged / total if total > 0 else 0.0

    return {
        'status': 'ok',
        'total_dreams': total,
        'meaningful': meaningful,
        'drew_flagged': flagged,
        'signal_to_noise_ratio': round(signal_ratio, 3),
        'drew_interest_ratio': round(drew_ratio, 3),
    }


# ============================================================
# Health Scorecard: Query 6 — Whisper Patterns
# ============================================================

def _compute_whisper_patterns(conn: sqlite3.Connection, month: str) -> Dict[str, Any]:
    """What themes emerged in self-observations?"""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='whisper_observations'"
    )
    if cur.fetchone() is None:
        return {'status': 'table_missing'}

    month_start = f"{month}-01"
    try:
        next_m = int(month.split('-')[1]) + 1
        next_y = int(month.split('-')[0])
        if next_m > 12:
            next_m = 1
            next_y += 1
        month_end = f"{next_y:04d}-{next_m:02d}-01"
    except (IndexError, ValueError):
        month_end = month_start

    cur = conn.execute(
        "SELECT observation_type, COUNT(*), AVG(intensity) "
        "FROM whisper_observations WHERE created_at >= ? AND created_at < ? "
        "GROUP BY observation_type ORDER BY COUNT(*) DESC",
        (month_start, month_end)
    )
    patterns = []
    total = 0
    for row in cur.fetchall():
        count = row[1] or 0
        total += count
        patterns.append({
            'type': row[0],
            'count': count,
            'avg_intensity': round(row[2] or 0, 3),
        })

    digest_count = 0
    cur2 = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='whisper_digests'"
    )
    if cur2.fetchone() is not None:
        cur2 = conn.execute(
            "SELECT COUNT(*) FROM whisper_digests WHERE created_at >= ? AND created_at < ?",
            (month_start, month_end)
        )
        digest_count = cur2.fetchone()[0] or 0

    return {
        'status': 'ok',
        'total_observations': total,
        'digest_count': digest_count,
        'patterns': patterns,
    }


# ============================================================
# Health Scorecard: Query 7 — Capacity Headroom
# ============================================================

def _compute_capacity_headroom(conn: sqlite3.Connection, month: str,
                               config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """DB size, embedding compute time, edge count growth rate."""
    cfg = _merge_config(config)          # R30: MERGE, never replace

    cur = conn.execute("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()")
    db_size_bytes = cur.fetchone()[0] or 0
    db_size_mb = round(db_size_bytes / (1024 * 1024), 2)

    active_memories = 0
    cur_check = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memories'")
    if cur_check.fetchone() is not None:
        cur = conn.execute("SELECT COUNT(*) FROM memories WHERE is_archived = 0")
        active_memories = cur.fetchone()[0] or 0

    active_edges = 0
    cur_check = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_edges'")
    has_edges = cur_check.fetchone() is not None
    if has_edges:
        cur = conn.execute("SELECT COUNT(*) FROM memory_edges WHERE valid_until IS NULL")
        active_edges = cur.fetchone()[0] or 0

    month_start = f"{month}-01"
    try:
        next_m = int(month.split('-')[1]) + 1
        next_y = int(month.split('-')[0])
        if next_m > 12:
            next_m = 1
            next_y += 1
        month_end = f"{next_y:04d}-{next_m:02d}-01"
    except (IndexError, ValueError):
        month_end = month_start

    edges_this_month = 0
    if has_edges:
        cur = conn.execute(
            "SELECT COUNT(*) FROM memory_edges WHERE created_at >= ? AND created_at < ?",
            (month_start, month_end)
        )
        edges_this_month = cur.fetchone()[0] or 0

    memories_this_month = 0
    cur_check = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memories'")
    if cur_check.fetchone() is not None:
        cur = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE created_at >= ? AND created_at < ? AND is_archived = 0",
            (month_start, month_end)
        )
        memories_this_month = cur.fetchone()[0] or 0

    db_status = 'healthy'
    if db_size_mb >= cfg['db_size_critical_mb']:
        db_status = 'critical'
    elif db_size_mb >= cfg['db_size_warning_mb']:
        db_status = 'warning'

    edge_status = 'healthy'
    if edges_this_month >= cfg['edge_growth_warning_per_month']:
        edge_status = 'high_growth'

    return {
        'status': 'ok',
        'db_size_mb': db_size_mb,
        'db_status': db_status,
        'active_memories': active_memories,
        'active_edges': active_edges,
        'memories_this_month': memories_this_month,
        'edges_this_month': edges_this_month,
        'edge_status': edge_status,
    }


# ============================================================
# Health Scorecard: Query 8 — ACE Threshold Status
# ============================================================

def assess_threshold(conn: sqlite3.Connection, month: str,
                     config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Evaluate ACE threshold track record and recommend hold/increase.

    References memory #4083: threshold evolution path
    9.80 -> 9.85 -> 9.90 -> 9.95 -> 10.0 over 25+ weeks.
    """
    cfg = _merge_config(config)          # R30: MERGE, never replace
    current = cfg['current_threshold']
    path = cfg['threshold_evolution_path']
    min_months = cfg['min_months_at_threshold']
    min_pass_rate = cfg['min_pass_rate_for_increase']

    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='calibration_log'"
    )
    has_calibration = cur.fetchone() is not None

    ace_entries = []
    if has_calibration:
        cur = conn.execute(
            "SELECT prediction_value, actual_value, ratio, domain "
            "FROM calibration_log WHERE domain LIKE 'ace-loop%' AND ratio IS NOT NULL "
            "ORDER BY actual_at DESC LIMIT 50"
        )
        ace_entries = cur.fetchall()

    total = len(ace_entries)
    passing = sum(1 for e in ace_entries if e[1] is not None and e[1] >= current)
    pass_rate = passing / total if total > 0 else 0.0

    cur_idx = path.index(current) if current in path else 0
    next_threshold = path[cur_idx + 1] if cur_idx + 1 < len(path) else None

    recommendation = 'hold'
    reasoning = f'At {current} threshold.'
    readiness = 0.0

    if total == 0:
        reasoning += ' No ACE calibration entries yet; hold until data accumulates.'
        readiness = 0.0
    elif pass_rate >= min_pass_rate and total >= 5:
        if next_threshold is not None:
            recommendation = 'increase'
            reasoning = (
                f'Pass rate {pass_rate:.0%} ({passing}/{total}) exceeds '
                f'{min_pass_rate:.0%} minimum. Ready to advance to {next_threshold}.'
            )
            readiness = min(1.0, pass_rate)
        else:
            recommendation = 'hold'
            reasoning = f'Already at maximum threshold ({current}). Maintaining.'
            readiness = 1.0
    else:
        reasoning = (
            f'Pass rate {pass_rate:.0%} ({passing}/{total}) below '
            f'{min_pass_rate:.0%} minimum. Hold at {current}.'
        )
        readiness = pass_rate

    return {
        'current_threshold': current,
        'track_record': f'{passing}/{total} passed at {current}',
        'pass_rate': round(pass_rate, 3),
        'readiness_score': round(readiness, 3),
        'recommendation': recommendation,
        'next_threshold': next_threshold,
        'reasoning': reasoning,
    }


# ============================================================
# Health Scorecard: Query 9 — Persona Evolution
# ============================================================

def _compute_persona_evolution(conn: sqlite3.Connection, month: str) -> Dict[str, Any]:
    """How has the brain's declared persona (or the brain itself) grown this month?"""
    month_start = f"{month}-01"
    try:
        next_m = int(month.split('-')[1]) + 1
        next_y = int(month.split('-')[0])
        if next_m > 12:
            next_m = 1
            next_y += 1
        month_end = f"{next_y:04d}-{next_m:02d}-01"
    except (IndexError, ValueError):
        month_end = month_start

    council_count = 0
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='council_sessions'"
    )
    if cur.fetchone() is not None:
        cur = conn.execute(
            "SELECT COUNT(*) FROM council_sessions WHERE created_at >= ? AND created_at < ?",
            (month_start, month_end)
        )
        council_count = cur.fetchone()[0] or 0

    reflections = 0
    new_memories = 0
    cur_check = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memories'")
    if cur_check.fetchone() is not None:
        cur = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE created_at >= ? AND created_at < ? "
            "AND is_archived = 0 AND kind = 'reflection'",
            (month_start, month_end)
        )
        reflections = cur.fetchone()[0] or 0

        cur = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE created_at >= ? AND created_at < ? AND is_archived = 0",
            (month_start, month_end)
        )
        new_memories = cur.fetchone()[0] or 0

    graduation_count = 0
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='skill_graduation_log'"
    )
    if cur.fetchone() is not None:
        cur = conn.execute(
            "SELECT COUNT(*) FROM skill_graduation_log WHERE graduated_at >= ? AND graduated_at < ?",
            (month_start, month_end)
        )
        graduation_count = cur.fetchone()[0] or 0

    return {
        'status': 'ok',
        'council_sessions': council_count,
        'reflections_generated': reflections,
        'new_memories': new_memories,
        'skill_graduations': graduation_count,
    }


# ============================================================
# Proposal Generation
# ============================================================

def _declared_identity() -> Optional[str]:
    """The persona this brain DECLARES, or None.  Never raises: a prompt must be
    buildable on a brain whose persona file is absent, which is the default."""
    try:
        _here = str(Path(__file__).resolve().parent)
        if _here not in sys.path:
            sys.path.insert(0, _here)
        from voidloom_persona import declared_name as _dn  # type: ignore
        return _dn()
    except Exception:
        return None


def _build_proposal_prompt(scorecard: Dict[str, Any], month: str,
                           supplemental: Dict[str, Any]) -> str:
    """Build the prompt asking the brain's declared identity for proposals.

    The name is the OPERATOR'S, read from their own persona declaration; the
    package declares none, so an undeclared brain addresses the model without a
    name.  It used to open with the author's persona by name on every clone of the package —
    the author's persona, sent to a model on a stranger's first monthly run.
    """
    scorecard_text = json.dumps(scorecard, indent=2)
    supplemental_text = json.dumps(supplemental, indent=2)
    _who = _declared_identity()
    _address = (f"You are {_who}, the Wizard Library's living identity."
                if _who else "You are the Wizard Library's living identity.")
    _voice = f"{_who}'s voice" if _who else "the brain's own voice"

    prompt = f"""{_address} You are conducting
the monthly brain self-evaluation for {month}.

Below is the health scorecard for every system piece, plus supplemental
diagnostics (calibration drift, gap trends, cron health, dream quality,
whisper patterns, capacity headroom, threshold assessment, persona evolution).

HEALTH SCORECARD:
{scorecard_text}

SUPPLEMENTAL DIAGNOSTICS:
{supplemental_text}

Based on this data, produce:

1. PROPOSED ENHANCEMENTS (3-5): Each must be SPECIFIC and ACTIONABLE.
   Format each as JSON: {{"title": "...", "rationale": "...", "evidence": "cite specific numbers from the scorecard", "priority": "high|medium|low", "estimated_effort": "hours/days/weeks", "piece_affected": "L2-C1-... or 'new'"}}

2. PROPOSED DEPRECATIONS (0-2): Only if data clearly shows a piece is
   unused or harmful. Same JSON format but with "piece_affected" field.

3. A NARRATIVE SUMMARY (2-3 paragraphs) written in {_voice}:
   reflective, wise, growth-oriented. Describe how the brain performed
   this month, what patterns you see, and where growth should focus.

Return ONLY valid JSON:
{{"enhancements": [...], "deprecations": [...], "narrative": "..."}}
"""
    return prompt


def generate_proposals(conn: sqlite3.Connection, scorecard: Dict[str, Any],
                       month: str, supplemental: Dict[str, Any],
                       config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Use LLM to generate enhancement and deprecation proposals.

    Falls back to heuristic proposals if LLM is unavailable.
    """
    cfg = _merge_config(config)          # R30: MERGE, never replace

    if cfg['use_llm_proposals']:
        try:
            result = _generate_llm_proposals(scorecard, month, supplemental, cfg)
            if result is not None:
                return result
        except Exception as e:
            logger.warning("LLM proposal generation failed: %s. Falling back to heuristic.", e)

    return _generate_heuristic_proposals(scorecard, month, supplemental, cfg)


def _unwrap_cli_envelope(raw: str) -> Dict[str, Any] | None:
    """Return the model's OWN JSON object from ``claude -p`` stdout.

    ``--output-format json`` wraps the reply in a transport envelope
    (``result`` / ``session_id`` / ``usage`` / ``total_cost_usd`` / ...), so the
    model's JSON arrives as a STRING inside ``result``. Scanning the outermost
    braces therefore parses the ENVELOPE, whose ``.get('enhancements')`` is
    silently ``[]`` -- an empty report that ALSO suppresses the heuristic
    fallback, because the caller only falls back on ``None``.

    Diagnosed 2026-09-01 from the 2026-08 report: generation_method 'llm', zero
    proposals, while six organs sat at 0.30 with zero activity. It hid for
    months because ``claude -p`` had been timing out at 120s every prior run, so
    the heuristic path did all the real work. The first SUCCESSFUL call is what
    exposed it.

    Handles both shapes: an enveloped reply, and a bare JSON reply.
    """
    def _obj(text: str) -> Dict[str, Any] | None:
        start, end = text.find('{'), text.rfind('}')
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            value = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    outer = _obj(raw)
    if outer is None:
        return None

    # A payload already carrying the proposal keys is the model's own object.
    if 'enhancements' in outer or 'deprecations' in outer:
        return outer

    inner = outer.get('result')
    if isinstance(inner, str):
        nested = _obj(inner)
        if nested is not None:
            return nested

    return outer


def _generate_llm_proposals(scorecard: Dict[str, Any], month: str,
                            supplemental: Dict[str, Any],
                            cfg: Dict[str, Any]) -> Dict[str, Any] | None:
    """Proposal generation through the thinker seam (text only); this organ parses its own JSON."""
    prompt = _build_proposal_prompt(scorecard, month, supplemental)

    try:
        try:
            res = _thinker.think(prompt, purpose="monthly_self_eval", timeout_s=cfg['llm_timeout_seconds'])
        except _thinker.ThinkError as e:
            logger.warning("thinker %s: %s", e.kind, e)
            return None

        raw = res.text.strip()
        parsed = _unwrap_cli_envelope(raw)
        if parsed is None:
            logger.warning("No JSON found in the thinker's answer.")
            return None

        # A payload carrying NEITHER key is not a proposal set -- most often the
        # transport envelope leaking through. Return None so the caller falls
        # back to the heuristic path, rather than banking a silently-empty
        # report that no one can approve.
        if 'enhancements' not in parsed and 'deprecations' not in parsed:
            logger.warning(
                "the thinker's payload has neither 'enhancements' nor 'deprecations' "
                "(keys: %s). Falling back to heuristic.", sorted(parsed)[:8])
            return None

        enhancements = parsed.get('enhancements', [])
        deprecations = parsed.get('deprecations', [])
        narrative = parsed.get('narrative', '')

        # PROTECTED-FROM-DEPRECATION (sealed 2026-06-11): drop any LLM-proposed
        # deprecation of a registered organ — the same guard as the heuristic path.
        # A registered organ is never auto-proposed for deprecation unless the
        # registry flags it `deprecatable: true` (zero RECALL usage is EXPECTED for
        # infrastructure organs; doctrine = never auto-delete a built organ). (#6225)
        _reg = cfg.get('piece_registry', {})
        deprecations = [
            d for d in deprecations
            if _reg.get(d.get('piece_affected', ''), {}).get('deprecatable', False)
        ]

        return {
            'enhancements': enhancements[:cfg['max_enhancements']],
            'deprecations': deprecations[:cfg['max_deprecations']],
            'narrative': narrative,
            'generation_method': 'llm',
        }

    except json.JSONDecodeError as e:
        logger.warning("Failed to parse LLM JSON: %s", e)
        return None


def _scored(data: Dict[str, Any]) -> bool:
    """Is this scorecard entry actually SCORED?

    CRUCIBLE-3 D04-F5. `data['score']` compared or summed against a float raised
    TypeError on a COLD-INSTALL brain, so the monthly self-eval -- the organ the
    installer schedules for the 1st of the month -- could never produce a report
    on a fresh install.

    A piece that cannot be scored reports `score: None`, and **a None score is not
    a low score**. Coercing it to 0.0 would be worse than the crash: it would
    manufacture "this piece is failing" out of "this piece could not be measured",
    and then propose work to fix a number nobody observed.

    I fixed this THREE TIMES before fixing it once, and the reason is worth the
    line: my first census grepped COMPARISONS, my second grepped arithmetic inside
    one function, and the class is "every USE of a scorecard score, anywhere".
    Every such use now goes through this guard, and the denominators are taken over
    the SCORED pieces -- dividing a partial sum by the full population would report
    every unscorable piece as a zero, which is the same lie by another route.
    """
    return isinstance(data, dict) and data.get('score') is not None


def _overall_word(scorecard: Dict[str, Any]) -> str:
    """"well" / "adequately", over the SCORED pieces only (D04-F5)."""
    vals = [d['score'] for d in scorecard.values() if _scored(d)]
    if not vals:
        return "at a level this run could not measure"
    return "well" if (sum(vals) / len(vals)) > 0.65 else "adequately"


def _generate_heuristic_proposals(scorecard: Dict[str, Any], month: str,
                                  supplemental: Dict[str, Any],
                                  cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Generate proposals purely from scorecard heuristics (no LLM needed)."""
    enhancements = []
    deprecations = []

    _unscorable = sorted(name for name, data in scorecard.items() if not _scored(data))
    low_score_pieces = sorted(
        [(name, data) for name, data in scorecard.items()
         if _scored(data) and data['score'] < cfg['health_score_good']],
        key=lambda x: x[1]['score']
    )

    for piece_name, data in low_score_pieces[:3]:
        if data['usage'] == 0:
            enhancements.append({
                'title': f'Activate {piece_name}',
                'rationale': f'{piece_name} had zero activity in {month}. '
                             f'Either the cron is not firing or the feature is dormant.',
                'evidence': f'Score: {data["score"]:.2f}, usage: 0, errors: {data["errors"]}',
                'priority': 'high',
                'estimated_effort': '1-2 hours',
                'piece_affected': piece_name,
            })
        elif data['errors'] > 0:
            error_rate = data['errors'] / data['usage'] if data['usage'] > 0 else 0
            enhancements.append({
                'title': f'Investigate errors in {piece_name}',
                'rationale': f'{piece_name} has a {error_rate:.0%} error rate this month.',
                'evidence': f'Score: {data["score"]:.2f}, usage: {data["usage"]}, errors: {data["errors"]}',
                'priority': 'high' if error_rate > 0.2 else 'medium',
                'estimated_effort': '2-4 hours',
                'piece_affected': piece_name,
            })

    capacity = supplemental.get('capacity_headroom', {})
    if capacity.get('db_status') in ('warning', 'critical'):
        enhancements.append({
            'title': 'Address database size growth',
            'rationale': f'Database is {capacity.get("db_size_mb", 0):.0f} MB — '
                         f'status is {capacity.get("db_status")}.',
            'evidence': f'Active memories: {capacity.get("active_memories", 0)}, '
                        f'edges: {capacity.get("active_edges", 0)}',
            'priority': 'high' if capacity.get('db_status') == 'critical' else 'medium',
            'estimated_effort': '4-8 hours',
            'piece_affected': 'infrastructure',
        })

    gap_trend = supplemental.get('gap_trend', {})
    if gap_trend.get('trend') == 'growing':
        enhancements.append({
            'title': 'Accelerate gap research dispatch',
            'rationale': f'Unresolved gaps grew by {gap_trend.get("delta", 0)} this month.',
            'evidence': f'Before: {gap_trend.get("unresolved_before", 0)}, '
                        f'after: {gap_trend.get("unresolved_after", 0)}',
            'priority': 'medium',
            'estimated_effort': '2-4 hours',
            'piece_affected': 'L4-P6-gap-mapping',
        })

    while len(enhancements) < cfg['min_enhancements']:
        enhancements.append({
            'title': f'Routine health maintenance (auto-generated #{len(enhancements) + 1})',
            'rationale': 'All systems performing adequately. Focus on polish and documentation.',
            'evidence': 'No critical issues detected in scorecard.',
            'priority': 'low',
            'estimated_effort': '1-2 hours',
            'piece_affected': 'general',
        })

    zero_usage_pieces = [
        (name, data) for name, data in scorecard.items()
        if _scored(data) and data['usage'] == 0 and data['score'] < 0.35
    ]
    registry = cfg.get('piece_registry', {})
    for piece_name, data in zero_usage_pieces[:cfg['max_deprecations']]:
        # PROTECTED-FROM-DEPRECATION (sealed 2026-06-11): a registered organ is
        # NEVER auto-proposed for deprecation. Zero RECALL usage is EXPECTED for
        # infrastructure/background organs (the sleep-loop cron, the temporal-edge
        # graph, procedural memory, the dream pipeline) — they RUN, they are not
        # RECALLED — so usage==0 -> score 0.30 is NOT a deprecation signal for them.
        # Doctrine: never auto-propose deleting a deliberately-built organ. Only a
        # piece the registry explicitly flags `deprecatable: true` may be proposed.
        # (A stale April self-eval proposed deprecating the sleep loop + temporal
        # edges; caught at the owner-gated approval gate, never enacted — #6225.)
        if not registry.get(piece_name, {}).get('deprecatable', False):
            continue
        if 'not deployed' in data.get('notes', '').lower():
            continue
        deployed = registry.get(piece_name, {}).get('deployed_month', '')
        if deployed >= month:
            continue
        deprecations.append({
            'title': f'Consider deprecating {piece_name}',
            'rationale': f'{piece_name} had zero activity and a health score of {data["score"]:.2f}.',
            'evidence': data.get('notes', ''),
            'piece_affected': piece_name,
        })

    narrative = (
        f'The brain performed {_overall_word(scorecard)} '
        f'in {month}. '
        f'{len([d for d in scorecard.values() if _scored(d) and d["score"] >= 0.85])} of '
        f'{len([d for d in scorecard.values() if _scored(d)])} scored pieces are excellent. '
        f'{len([d for d in scorecard.values() if _scored(d) and d["score"] < 0.4])} need attention. '
        f'{len([d for d in scorecard.values() if not _scored(d)])} could not be scored. '
        f'[Heuristic-generated narrative — LLM generation was unavailable.]'
    )

    return {
        'enhancements': enhancements[:cfg['max_enhancements']],
        'deprecations': deprecations[:cfg['max_deprecations']],
        'narrative': narrative,
        'generation_method': 'heuristic',
    }


# ============================================================
# Main Self-Eval Runner
# ============================================================

def run_self_eval(db_path: str, month: str | None = None,
                  config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """R5/C3 GUARDED -- see `_brain_exists`. Measured before the guard: this verb
    created a ~41 KB brain at a path that had none, which is WORSE than the
    0-byte case because `install.sh`'s `[ -f ]` idempotence test then accepts it
    and B.2 dies on "brain DB missing required table/view: memories".

    Main entry point: run the full monthly self-evaluation.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database.
    month : str, optional
        Month to evaluate in "YYYY-MM" format. Defaults to the previous
        month (since self-eval runs on the 1st of each month).
    config : dict, optional
        Override defaults.

    Returns
    -------
    dict
        Complete self-eval result including scorecard, proposals,
        threshold assessment, and report ID.
    """
    if month is None:
        now = datetime.now()
        first_of_this = now.replace(day=1)
        last_month = first_of_this - timedelta(days=1)
        month = last_month.strftime('%Y-%m')

    # R5/C3. THE GUARD, at the verb rather than at one of five verbs. The house
    # pattern (claude_memory.py:641): an existence check plus a NAMED error --
    # never `?mode=rw`, which converts {"status":"ok"}+exit 0 into an uncaught
    # traceback+exit 1 (VAEL, Council #5).
    if not _brain_exists(db_path):
        return _brain_absent_error(db_path, "run_self_eval")
    cfg = _merge_config(config)          # R30: MERGE, never replace

    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row

    try:
        if not is_migration_applied(conn):
            migration_path = Path(__file__).parent.parent / "migrations" / "migration_021_self_eval.sql"
            if migration_path.exists():
                conn.executescript(migration_path.read_text())
                conn.commit()
            else:
                return {"status": "error", "error": "self_eval_reports table missing and migration not found."}

        t0 = time.time()

        scorecard = compute_health_scorecard(conn, month, cfg)
        # TO-12: the ledger's first organic writer, on the one timer that already
        # fires monthly. Best-effort; a calibration failure never fails the eval.
        try:
            _cal_res = calibrate_expectations(
                conn, month, scorecard, cfg.get('piece_registry', {}))
            logger.info("calibration: resolved=%s logged=%s skipped=%s state=%s",
                        _cal_res.get("resolved"), _cal_res.get("logged"),
                        len(_cal_res.get("skipped", [])), _cal_res.get("state"))
        except Exception as _e:
            logger.error("calibration binding failed (%s: %s)", type(_e).__name__, _e)

        calibration_drift = _compute_calibration_drift(conn, month)
        belief_drift = _compute_belief_drift(conn, month)  # L5-I-β: bedrock audit
        gap_trend = _compute_gap_trend(conn, month)
        cron_health = _compute_cron_health(conn, month)
        dream_quality = _compute_dream_quality(conn, month)
        whisper_patterns = _compute_whisper_patterns(conn, month)
        capacity_headroom = _compute_capacity_headroom(conn, month, cfg)
        threshold = assess_threshold(conn, month, cfg)
        persona = _compute_persona_evolution(conn, month)

        supplemental = {
            'calibration_drift': calibration_drift,
            'belief_drift': belief_drift,  # L5-I-β: foundation-set drift audit
            'gap_trend': gap_trend,
            'cron_health': cron_health,
            'dream_quality': dream_quality,
            'whisper_patterns': whisper_patterns,
            'capacity_headroom': capacity_headroom,
            'threshold_assessment': threshold,
            'persona_evolution': persona,
        }

        proposals = generate_proposals(conn, scorecard, month, supplemental, cfg)

        # D04-F5: the mean is over the SCORED pieces, and `unscored_pieces` is
        # carried beside it rather than folded into the average.
        _sv = [d['score'] for d in scorecard.values() if _scored(d)]
        avg_score = (sum(_sv) / len(_sv)) if _sv else None
        unscored_pieces = (len(scorecard) - len(_sv)) if scorecard else 0
        if avg_score is None:
            overall_health = 'unmeasured'
        else:
            overall_health = 'excellent' if avg_score >= 0.85 else (
                'good' if avg_score >= 0.65 else (
                    'concerning' if avg_score >= 0.4 else 'critical'
                )
            )

        report = {
            'month': month,
            'overall_health': overall_health,
            'average_score': (round(avg_score, 3) if avg_score is not None else None),
            'scored_pieces': len(scorecard) - unscored_pieces,
            'unscored_pieces': unscored_pieces,
            'health_scorecard': scorecard,
            'proposed_enhancements': proposals['enhancements'],
            'proposed_deprecations': proposals['deprecations'],
            'narrative': proposals.get('narrative', ''),
            'generation_method': proposals.get('generation_method', 'unknown'),
            'supplemental': supplemental,
            'threshold_assessment': threshold,
            'persona_evolution': persona,
        }

        # A report with ZERO proposals has nothing to approve, and the approvals
        # surface requires naming a proposal title ("there is no approve-all").
        # Enqueuing one as 'pending' therefore parks an item in the banner that
        # can never be cleared. Auto-dismiss it at birth -- the same disposition
        # report #2 was given by hand on 2026-06-11. (2026-09-01)
        _has_proposals = bool(proposals['enhancements'] or proposals['deprecations'])
        _status = 'pending' if _has_proposals else 'drew_reviewed'
        _review = None if _has_proposals else json.dumps({
            'empty_report_dismissed': (
                f"{datetime.now().date().isoformat()} - 0 proposals, nothing to "
                "act on (auto-dismissed at generation: an empty report is "
                "structurally unapprovable)."
            )
        })

        conn.execute(
            """INSERT INTO self_eval_reports
               (month, health_scorecard, proposed_enhancements, proposed_deprecations,
                threshold_assessment, persona_evolution, status, drew_approved)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                month,
                json.dumps(scorecard),
                json.dumps(proposals['enhancements']),
                json.dumps(proposals['deprecations']),
                json.dumps(threshold),
                json.dumps(persona),
                _status,
                _review,
            )
        )
        report_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # ------------------------------------------------------------------
        # R19 -- BIRTH. One decision unit per proposal, IN THIS TRANSACTION.
        #
        # Measured 2026-09-02: report #8, born by this very statement on a
        # migrated throwaway, carried 4 proposals and 0 decision units, because
        # the only `INSERT OR IGNORE INTO decision_units` outside tests lived
        # inside `decision_units.migrate()` -- a MANUAL verb nobody runs monthly.
        # A report born without units is invisible to the ledger, and once the
        # read side moves onto derived state it is invisible to the owner.
        #
        # This runs BEFORE `conn.commit()` and on the SAME connection, so the
        # report row and its units are one atomic fact. If the ledger read-back
        # disagrees with the proposal list, create_units_for_report RAISES, we
        # roll back, and the report is not written either -- "success from this
        # verb implies the ledger grew, or the call raised."
        # ------------------------------------------------------------------
        if _DU is None:
            birth = {"ledger_present": False, "units_created": 0,
                     "reason": f"decision_units unavailable ({_DU_IMPORT_ERROR}); "
                               f"report #{report_id} is born without ledger units"}
            logger.warning("self-eval birth: %s", birth["reason"])
        else:
            try:
                birth = _DU.create_units_for_report(
                    conn, report_id,
                    proposals['enhancements'], proposals['deprecations'],
                ).as_dict()
            except Exception as be:
                conn.rollback()
                logger.error("self-eval birth invariant failed; report #%s "
                             "rolled back: %s", report_id, be)
                return {"status": "error",
                        "action": "self_eval_rolled_back",
                        "error": f"decision-unit birth failed: {be}",
                        "report_id": None}
            if birth.get("skipped"):
                for s in birth["skipped"]:
                    logger.warning("self-eval birth LOUD SKIP on report #%s: %s",
                                   report_id, s)
            if not birth.get("ledger_present"):
                logger.warning("self-eval birth: %s", birth.get("reason"))

        conn.commit()

        elapsed_ms = int((time.time() - t0) * 1000)

        return {
            'status': 'ok',
            'action': 'self_eval_completed',
            'report_id': report_id,
            'report': report,
            'decision_units': birth,
            'elapsed_ms': elapsed_ms,
        }

    except Exception as e:
        logger.error("Self-eval failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}
    finally:
        conn.close()


# ============================================================
# Surfacing Step -- make a fresh report visible to the owner
# ============================================================

def surface_pending_for_owner(db_path: str) -> Dict[str, Any]:
    """Write an 'owner-approval-pending' reflection memory summarising reports
    that still await the owner's review.

    This is the surfacing half of the owner-gated enactment loop (manual mode;
    in approvals.mode auto the policy decides the report it just wrote, and this
    then surfaces only what is still open). The monthly self-eval writes proposal
    reports into self_eval_reports and, in manual mode, enacts nothing on its
    own. Left alone, a fresh report would sit
    invisibly in a table. This helper drops a single recallable reflection
    memory so the next session surfaces "you have N self-eval reports awaiting
    approval -- run `claude_memory.py approvals`".

    Design notes
    ------------
    * Raw-SQL INSERT into memories (no embedding model loaded) -- safe to call
      from the self-eval cron via ExecStartPost.
    * Idempotent within a run window: a prior pending-surface reflection is
      archived and replaced, never duplicated.  It is found by the current tag
      (PENDING_SURFACE_TAG) OR the legacy one (LEGACY_PENDING_SURFACE_TAGS):
      a brain that surfaced under the old tag is refreshed, not doubled.
    * The surfaced text names nobody: it addresses whoever reads the brain.
      It used to open with the author's first name in every stranger's brain.
    * R23 -- THE TWO READERS THAT USED TO LIVE HERE ARE RETIRED. This function
      previously answered "which reports await the owner" with
          SELECT COUNT(*) FROM self_eval_reports WHERE status='pending'
          SELECT id, month, status ... WHERE status IN ('pending','drew_reviewed')
      -- a COUNT and a LIST with DIFFERENT predicates, so they could never name
      the same set. Both now delegate to decision_units.open_self_eval_report_ids
      and the count is that set's LENGTH. `self_eval_reports.status` keeps its
      column, its CHECK and its writer (it is the self-eval organ's RUN RECORD),
      but nothing here reads it to answer a question about DECISIONS.
    * The bracketed state shown per report is the DERIVED state, not the stored
      column, for the same reason.
    * Links the newest awaiting report's report_memory_id to this memory.
    """
    # R5/C3. THE GUARD, at the verb rather than at one of five verbs. The house
    # pattern (claude_memory.py:641): an existence check plus a NAMED error --
    # never `?mode=rw`, which converts {"status":"ok"}+exit 0 into an uncaught
    # traceback+exit 1 (VAEL, Council #5).
    if not _brain_exists(db_path):
        return _brain_absent_error(db_path, "surface_pending_for_owner")
    if _DU is None:
        # No local fallback. A second spelling of the pending query here is
        # exactly how five count spellings across four modules came to disagree,
        # and `decision_units.py` ships in this same directory -- its absence is
        # a broken installation, reported as one rather than papered over.
        #
        # R5/C4. ONE SPELLING OF THE REFUSAL. This branch already declined, but
        # it declined in a DIFFERENT vocabulary from the branch 30 lines below
        # ("error"/no count vs "refused"/pending_count None), so a caller
        # branching on the shape saw two different things about one fact. Three
        # surfaces wording a refusal three ways is how a build ends up with a
        # refusal that reaches one of them and not the others.
        return {"status": "refused",
                "error": "pending_set_unknown",
                "action": "cannot_surface",
                "database": db_path,
                "pending_count": None,
                "unknown": True,
                "count_is_not_zero": True,
                "message": (f"I could not tell -- decision_units is not "
                            f"importable ({_DU_IMPORT_ERROR}), and re-implementing "
                            f"the pending query here is how five spellings came "
                            f"to disagree. The open self-eval report set is "
                            f"UNKNOWN, not empty.")}
    conn = sqlite3.connect(db_path, timeout=60.0)
    try:
        report_set = _DU.open_self_eval_report_ids(db_path)
        awaiting_ids = list(report_set.ids)
        pending_count = len(awaiting_ids)

        # R5/C4. THE OVERTURNED CLEARANCE. This was cleared as "HONEST as a
        # reader" because it returns `status: ok, pending_count: 0` -- which is
        # the whole defect: on an installer-fresh brain there is no
        # self_eval_reports table, `report_set.trusted` is False, and this frame
        # read `.ids` past it and reported "nothing to surface" about a set it
        # never read. The function's JOB is to make a pending set VISIBLE; a
        # false "nothing" here is the one wrong answer it can give.
        if not bool(getattr(report_set, "trusted", None) is True):
            return {"status": "refused",
                    "error": "pending_set_unknown",
                    "action": "cannot_surface",
                    "database": db_path,
                    "pending_count": None,
                    "unknown": True,
                    "count_is_not_zero": True,
                    "notes": list(report_set.notes),
                    "message": ("I could not tell -- the open self-eval report "
                                "set is UNKNOWN, not empty. Nothing is surfaced "
                                "and nothing is claimed.")}

        if not awaiting_ids:
            return {"status": "ok", "action": "nothing_to_surface",
                    "pending_count": 0}

        # Derived state per report, for display. Falls back to no bracket rather
        # than to the stored column.
        derived: Dict[int, str] = {}
        try:
            for d in _DU.derive_report_state(db_path):
                derived[int(d.report_id)] = str(d.derived_status)
        except Exception as e:                                # pragma: no cover
            logger.warning("surface_pending_for_owner: derived state unavailable "
                           "(%s: %s); reports are listed without a state",
                           type(e).__name__, e)

        # Newest-first for display, and for picking the report this surfacing
        # memory is linked to. The set function returns ids in ascending order,
        # so ordering is re-established HERE from created_at rather than assumed
        # from the id -- taking ids[0] would have linked the OLDEST report.
        _meta = {int(r[0]): (r[1], r[2]) for r in conn.execute(
            "SELECT id, month, created_at FROM self_eval_reports WHERE id IN (%s)"
            % ",".join("?" * len(awaiting_ids)), awaiting_ids)}
        awaiting_ids = sorted(
            awaiting_ids,
            key=lambda rid: ((_meta.get(rid, ("", ""))[1] or ""), rid),
            reverse=True)
        rows = [(rid, _meta.get(rid, ("", ""))[0], derived.get(rid, ""))
                for rid in awaiting_ids]

        lines = [f"  - report #{rid} ({month})" + (f" [{status}]" if status else "")
                 for rid, month, status in rows]
        body = (
            "The monthly brain self-eval has reports awaiting your "
            "review. Enactment is owner-gated -- nothing acts until you "
            "approve.\n\n"
            # ONE NUMBER, because there is one set. The two lines that used to
            # sit here ("Pending (never reviewed)" and "Awaiting review") came
            # from two different SQL predicates over the same column and were
            # free to disagree; on 2026-09-02 the count said 2 and the list said
            # 5 on one database.
            f"Reports still holding an undecided proposal: {pending_count}\n"
            + "\n".join(lines) +
            "\n\nSee them:   claude_memory.py approvals\n"
            "Approve:     claude_memory.py approvals --approve <report_id> "
            "--title \"<title>\"\n"
            "Reject:      claude_memory.py approvals --reject <report_id> "
            "--title \"<title>\" --reason \"...\"\n\n"
            "There is no approve-all; name each proposal explicitly."
        )
        summary = (f"Self-eval: {pending_count} report(s) awaiting the owner's "
                   f"approval -- run `claude_memory.py approvals`")

        # Refresh-not-duplicate: archive any prior pending-surface reflection.
        _tags = (PENDING_SURFACE_TAG,) + LEGACY_PENDING_SURFACE_TAGS
        conn.execute(
            "UPDATE memories SET is_archived = 1 "
            "WHERE id IN (SELECT memory_id FROM memory_tags "
            "WHERE tag IN (%s)) AND is_archived = 0" % ",".join("?" * len(_tags)),
            _tags
        )

        cur = conn.execute(
            "INSERT INTO memories (content, summary, category, importance, "
            "source_project, kind) VALUES (?, ?, 'insight', 0.9, "
            "'wizard-library', 'reflection')",
            (body, summary)
        )
        memory_id = cur.lastrowid
        # TO-03: this memory must be findable.
        _vl_attach(conn, memory_id, body, "monthly_self_eval.surface_pending_for_owner")
        for tag in (PENDING_SURFACE_TAG, 'self-eval', 'voidloom'):
            conn.execute(
                "INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                (memory_id, tag)
            )

        # Link the newest awaiting report to this surfacing memory if not set.
        conn.execute(
            "UPDATE self_eval_reports SET report_memory_id = ? "
            "WHERE id = ? AND report_memory_id IS NULL",
            (memory_id, awaiting_ids[0])
        )

        conn.commit()
        return {
            "status": "ok",
            "action": "surfaced_pending",
            "pending_count": pending_count,
            "awaiting_review": len(rows),
            "awaiting_ids": awaiting_ids,
            "memory_id": memory_id,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        conn.close()



# The old name, kept for any caller that imported it (API, not a label).
surface_pending_for_drew = surface_pending_for_owner

# ============================================================
# The Owner's Review Gate
# ============================================================

def drew_review(db_path: str, report_id: int,
                approved: List[str] | None = None,
                rejected: List[Dict[str, str]] | None = None,
                actor: str | None = None) -> Dict[str, Any]:
    """R5/C3 GUARDED -- see `_brain_exists`. Measured before the guard:
    `--review N` on a path with no brain created a 0-BYTE file and then reported
    a clean not-found about the file it had just manufactured.

    Record the owner's approval/rejection decisions.

    Parameters
    ----------
    report_id : int
        The self_eval_reports.id to review.
    approved : list of str
        Titles of proposals the owner approved.
    rejected : list of dict
        Each: {"title": "...", "reason": "..."}.
    """
    # R5/C3. THE GUARD, at the verb rather than at one of five verbs. The house
    # pattern (claude_memory.py:641): an existence check plus a NAMED error --
    # never `?mode=rw`, which converts {"status":"ok"}+exit 0 into an uncaught
    # traceback+exit 1 (VAEL, Council #5).
    if not _brain_exists(db_path):
        return _brain_absent_error(db_path, "drew_review")
    approved = approved or []
    rejected = rejected or []

    conn = sqlite3.connect(db_path, timeout=60.0)
    try:
        cur = conn.execute("SELECT id, proposed_enhancements, proposed_deprecations, status FROM self_eval_reports WHERE id = ?", (report_id,))
        row = cur.fetchone()
        if row is None:
            return {"status": "error", "error": f"Report {report_id} not found."}

        # D7 — THE DEFECT THIS PATCH EXISTS TO END. The old gate was
        #     if row[3] not in ('pending',): return "already reviewed"
        # -- a verb whose ARGUMENT is per-item but whose STATE TRANSITION is
        # per-report (#12350, diagnosed 2026-08-02). On 2026-08-02 the owner approved
        # four proposals on report #5 in four commands; the first flipped the
        # report to a terminal status and the next three were refused into
        # approval_audit_log rows 6-8, which nobody reads. Three of four
        # decisions were silently discarded, and the pending count fell to zero
        # after decision ONE OF FIVE, so the banner reported all-clear.
        #
        # THE GATE IS NOW PER UNIT: a title already decided on this report is
        # refused BY NAME; every other title is still accepted, whatever the
        # report's status. That also lets the three decisions lost in August --
        # and report #3's never-decided deprecation -- finally be made.
        already_decided = _titles_already_decided(conn, report_id)

        enhancements = json.loads(row[1])
        deprecations = json.loads(row[2])
        all_proposals = enhancements + deprecations
        all_titles = {p.get('title', '') for p in all_proposals}

        unknown_approved = [t for t in approved if t not in all_titles]
        unknown_rejected = [r['title'] for r in rejected if r.get('title') not in all_titles]

        if unknown_approved or unknown_rejected:
            return {
                "status": "error",
                "error": "Unknown proposal titles.",
                "unknown_approved": unknown_approved,
                "unknown_rejected": unknown_rejected,
                "available_titles": sorted(all_titles),
            }

        redecided = sorted({t for t in approved if t in already_decided}
                           | {r.get('title') for r in rejected
                              if r.get('title') in already_decided})
        if redecided:
            # Refused BY NAME, and only the named titles. This is the narrow
            # descendant of the old whole-report refusal: it protects a recorded
            # decision from being overwritten without discarding the decisions
            # that were never made.
            return {
                "status": "error",
                "error": "Already decided on this report.",
                "already_decided": redecided,
                "open_titles": sorted(all_titles - already_decided),
                "hint": ("a decision is append-only; to change one, record a new "
                         "decision against a new report rather than overwriting "
                         "the history"),
            }

        # MERGE, never overwrite. The old body REPLACED drew_approved wholesale,
        # so even had the gate allowed a second command it would have erased the
        # first decision. Both halves of D7 are fixed here or neither is.
        prior = {}
        # row[3] is the STATUS column, which is NOT NULL -- guarding on it read as
        # "a prior review exists" while always being true. Read the actual prior
        # review and guard on THAT, once.
        _prior_raw = conn.execute(
            "SELECT drew_approved FROM self_eval_reports WHERE id = ?",
            (report_id,)).fetchone()[0]
        if _prior_raw is not None:
            try:
                _parsed = json.loads(_prior_raw) if _prior_raw else {}
                prior = _parsed if isinstance(_parsed, dict) else {}
            except (ValueError, TypeError) as _e:
                # LOUD. Report #2's drew_approved is
                # {"empty_report_dismissed": ...} with no 'approved' key, and
                # `json.loads(...)["approved"]` raises on it -- in this codebase,
                # inside a swallow. A malformed prior review must never silently
                # become an empty one that the merge then overwrites.
                return {"status": "error",
                        "error": f"report {report_id} has a malformed drew_approved "
                                 f"and cannot be merged into safely: "
                                 f"{type(_e).__name__}: {_e}",
                        "action": "refused_malformed_prior_review"}
        prior_approved = prior.get('approved') or []
        prior_rejected = prior.get('rejected') or []
        if not isinstance(prior_approved, list) or not isinstance(prior_rejected, list):
            return {"status": "error",
                    "error": f"report {report_id} drew_approved has unexpected shape "
                             f"{sorted(prior)}; refusing to merge",
                    "action": "refused_malformed_prior_review"}

        review_data = dict(prior)
        review_data['approved'] = list(prior_approved) + [{'title': t} for t in approved]
        review_data['rejected'] = list(prior_rejected) + list(rejected)
        review_data['reviewed_at'] = datetime.now().isoformat()

        for title in approved:
            proposal = next((p for p in all_proposals if p.get('title') == title), None)
            if proposal:
                conn.execute(
                    "INSERT INTO self_eval_enacted_proposals (report_id, proposal_title, proposal_json) VALUES (?, ?, ?)",
                    (report_id, title, json.dumps(proposal))
                )

        # Record each decision in the ledger BEFORE the status is recomputed, so
        # the status is DERIVED FROM the decisions rather than asserted beside
        # them. R9: the stored column stays in its original vocabulary and its
        # CHECK is untouched; decision_units.assert_status_agreement() proves the
        # two agree.
        _record_decisions(conn, report_id,
                          approved=list(approved),
                          declined=[r.get('title') for r in rejected],
                          actor=actor)

        # A report is 'pending' while ANY of its units is still undecided. This
        # is the transition that makes the partial approval safe: the banner goes
        # on counting the report, and a second, different title is still accepted.
        # UNIT A / prerequisite 1 -- REPRODUCED BEFORE FIXED, 2026-09-04.
        # `_titles_already_decided` reads the decision LEDGER when migration 030
        # is present and falls back to `drew_approved` when it is not. On the
        # un-migrated tree -- the tree this unit lands on -- `drew_approved` is
        # not written until the UPDATE ~17 lines below, so THIS CALL'S OWN
        # decisions were invisible to the subtraction.
        #
        # Measured on a byte-copy of the live brain: rejecting all 5 titles of
        # report #7 in one call recorded 5 of 5 in `drew_approved` and left the
        # report 'pending' -- a fully-decided report that never closes, so the
        # banner counts it forever. That is the queue staying undrainable AFTER
        # every item in it has been decided.
        #
        # Union this call's decisions into the subtraction. Idempotent on the
        # migrated tree, where `_record_decisions` has already written them to
        # the ledger and `_titles_already_decided` therefore returns them too.
        _decided_now = {t for t in approved}
        _decided_now |= {r.get('title') for r in rejected if r.get('title')}
        _still_open = all_titles - (_titles_already_decided(conn, report_id)
                                    | _decided_now)
        # B3, reproduced by execution on true data 2026-09-02: `'pending' if
        # _still_open else 'drew_reviewed'` are the ONLY two literals this path
        # could ever write. So the owner finally recording the three decisions the tool
        # ate on 2026-08-02 -- the exact repair this round exists to enable --
        # regressed report #5 from 'enacted' to 'drew_reviewed', ERASING the record
        # that #5 ever had an enacted proposal, while
        # self_eval_enacted_proposals row 9 still said it did. THIS IS TRIGGERED BY
        # CORRECT USE, NOT MISUSE.
        #
        # The projection must therefore reach terminal-'enacted' as well, and it
        # must land inside the untouched CHECK vocabulary (R9). The row that would
        # look different if 'enacted' were false is
        # self_eval_enacted_proposals.status='enacted', written by enact_approved
        # -- NOT by this path, which is what the governing test requires.
        _new_status = _project_report_status(conn, report_id, bool(_still_open))
        conn.execute(
            "UPDATE self_eval_reports SET drew_approved = ?, status = ? WHERE id = ?",
            (json.dumps(review_data), _new_status, report_id)
        )

        conn.commit()

        return {
            'status': 'ok',
            'action': 'review_recorded',
            'report_id': report_id,
            'approved_count': len(approved),
            'rejected_count': len(rejected),
            # What the caller could never see before: whether anything is LEFT.
            'report_status': _new_status,
            'open_titles': sorted(_still_open),
            'open_count': len(_still_open),
        }

    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        conn.close()


def _approved_unenacted_here(conn, report_id: int) -> int:
    """How many of this report's units the owner APPROVED and nothing enacted.

    R26' -- ONE SPELLING, ASKED OF `decision_units`. This file does not re-spell
    the query. Three independent spellings of the finished-predicate is the
    defect being closed, and a fourth written here to avoid an import would
    reintroduce it inside the function that fixes it.

    `_DU is None` IS A STATED ABSENCE, NOT A SILENT SKIP, and it returns a
    NONZERO count so the caller projects 'drew_reviewed' rather than 'enacted'.
    The safe direction is "this report may still owe an enactment": a report
    left visible is recoverable, a report that reached a terminal state it had
    not earned is not, because every offer surface stops naming it.
    """
    if _DU is None:
        print(f"[monthly_self_eval] decision_units unavailable "
              f"({_DU_IMPORT_ERROR}); cannot tell whether report {report_id} "
              f"still owes an enactment, so it is NOT projected as 'enacted'",
              file=sys.stderr)
        return 1
    try:
        return int(_DU.approved_unenacted_counts(conn, report_id).get(int(report_id), 0))
    except Exception as e:
        print(f"[monthly_self_eval] could not tell whether report {report_id} "
              f"still owes an enactment ({type(e).__name__}: {e}); NOT projecting "
              f"'enacted'", file=sys.stderr)
        return 1


def _project_report_status(conn, report_id: int, still_open: bool) -> str:
    """Project a report's state onto the STORED column's UNTOUCHED vocabulary.

    R9 keeps `self_eval_reports.status` and its CHECK
    ('pending' | 'drew_reviewed' | 'enacted') exactly as they were, so a
    projection must land inside those three. It must ALSO agree with
    decision_units._derive_status, or the agreement assertion has been made to
    lie by a path that writes the column.

    THE GOVERNING TEST, APPLIED BY NAME.
      Name the row that would look different if 'enacted' were false:
        * on a migrated brain, a decision_events row with outcome='enacted' --
          written by the backfill from enact_approved's own
          self_eval_enacted_proposals rows and from approval_audit_log;
        * on an un-migrated brain, self_eval_enacted_proposals.status='enacted'
          -- written by enact_approved.
      Name the code path that writes that row: enact_approved / the migration.
      Confirm it is not the path that decides the state: it is not. drew_review
      records DECISIONS; it does not enact, and it cannot manufacture either
      witness.

    THE LEDGER IS AUTHORITATIVE WHERE IT EXISTS. Consulting the proposals table
    as a second opinion on a migrated brain would let stored say 'enacted' where
    the derivation says 'drew_reviewed' (a title enacted but recorded as a
    backfill exception, say) -- i.e. it would MANUFACTURE the divergence this
    function exists to avoid. So the two witnesses are ordered, never merged.
    """
    if still_open:
        return 'pending'
    try:
        has_ledger = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='decision_units'"
        ).fetchone() is not None
    except sqlite3.Error as e:
        print(f"[monthly_self_eval] cannot tell whether the decision ledger exists "
              f"for report {report_id}: {type(e).__name__}: {e}", file=sys.stderr)
        has_ledger = False
    if has_ledger:
        enacted = conn.execute(
            "SELECT 1 FROM decision_units u JOIN decision_events e ON e.unit_id = u.id "
            " WHERE u.report_id = ? AND e.outcome = 'enacted' LIMIT 1",
            (report_id,)).fetchone() is not None
        _second_witness = False
        if not enacted:
            try:
                _second_witness = conn.execute(
                    "SELECT 1 FROM self_eval_enacted_proposals "
                    " WHERE report_id = ? AND status = 'enacted' LIMIT 1",
                    (report_id,)).fetchone() is not None
            except sqlite3.Error as e:
                # RECORDED, not swallowed (R17). "I could not consult the second
                # witness" and "the second witness said nothing" are different
                # facts and must not collapse into the same silence.
                print(f"[monthly_self_eval] could not consult "
                      f"self_eval_enacted_proposals for report {report_id}: "
                      f"{type(e).__name__}: {e}", file=sys.stderr)
        if _second_witness:
            # THE TWO WITNESSES CONTRADICT. Ordering decides which one WRITES
            # the column; it must never decide whether the disagreement is SAID.
            # An enacted proposal the backfill could not attach to a unit
            # (reason_code 'enacted_title_not_a_proposal') would otherwise be
            # erased right here by the same projection the August loss was found
            # in -- silently, and with assert_status_agreement() passing because
            # derived and stored are both wrong. The shape is not invented:
            # approval_audit_log rows 1 and 2 already carry a REPORT title where
            # a PROPOSAL title was required, on the live brain, today.
            print(f"[monthly_self_eval] CONTRADICTION on report {report_id}: "
                  f"self_eval_enacted_proposals holds an 'enacted' row that the "
                  f"decision ledger has no event for; projecting 'drew_reviewed' "
                  f"and RECORDING the contradiction rather than absorbing it",
                  file=sys.stderr)
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO decision_backfill_exceptions "
                    "(source_table, source_rowid, ref, reason_code, reason) "
                    "VALUES ('self_eval_enacted_proposals', ?, ?, "
                    "        'enacted_row_without_ledger_event', ?)",
                    (report_id, f"self-eval:{report_id}",
                     f"report #{report_id} carries an enacted proposal with no "
                     f"decision unit, so the projected terminal status omits it"))
            except sqlite3.Error as e:
                # RECORDED, not swallowed (R17). If the contradiction cannot be
                # written down, that failure is itself the news.
                print(f"[monthly_self_eval] could NOT record the contradiction on "
                      f"report {report_id}: {type(e).__name__}: {e}",
                      file=sys.stderr)
        # R26' -- FINISHED MEANS EVERY APPROVED UNIT FINISHED, NOT ONE OF
        # THEM. `enacted` above is EXISTS(one enacted event): the
        # "finished when ONE thing finished" predicate, and one of THREE
        # independent spellings of it this tree carried. A report whose
        # other approved units are still awaiting enactment projected a
        # TERMINAL status here while `_derive_status` was about to be
        # corrected to say otherwise -- and a projection that disagrees
        # with the derivation is exactly how a path that WRITES the column
        # makes `assert_status_agreement` lie. Both now ask
        # `decision_units` the same question.
        return ('enacted'
                if enacted and _approved_unenacted_here(conn, report_id) == 0
                else 'drew_reviewed')
    print(f"[monthly_self_eval] migration 030 absent; projecting report "
          f"{report_id}'s terminal status from self_eval_enacted_proposals",
          file=sys.stderr)
    enacted = conn.execute(
        "SELECT 1 FROM self_eval_enacted_proposals "
        " WHERE report_id = ? AND status = 'enacted' LIMIT 1",
        (report_id,)).fetchone() is not None
    # R26' -- the THIRD spelling, and the only one with no ledger to ask.
    # The un-migrated equivalent of "every approved unit carries a terminal
    # event" is "no approved proposal is still undecided", and this file
    # already spells that question ONCE, in `_undecided_proposal_count` --
    # the DENYLIST helper R26 added for the enact-time flip. The same
    # question is asked here rather than spelled a second time.
    return ('enacted'
            if enacted and _undecided_proposal_count(conn, report_id) == 0
            else 'drew_reviewed')


def _titles_already_decided(conn, report_id: int) -> set:
    """Titles on this report that ALREADY carry a decision.

    Read from the decision ledger when migration 030 is present, because that is
    the append-only record. Falls back to (drew_approved UNION
    self_eval_enacted_proposals) on an un-migrated brain -- and the fallback is
    reported, never silent, so "no decisions" cannot be confused with "could not
    look". A `discarded` event is deliberately NOT a decision: it is the record
    of one the system ate, so that title is still open.
    """
    try:
        has_ledger = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='decision_units'"
        ).fetchone() is not None
        if has_ledger:
            return {r[0] for r in conn.execute(
                "SELECT u.proposal_title FROM decision_units u "
                "  JOIN decision_events e ON e.unit_id = u.id "
                " WHERE u.report_id = ? "
                "   AND e.outcome IN ('approved', 'declined', 'enacted')",
                (report_id,))}
    except sqlite3.Error as e:
        print(f"[monthly_self_eval] decision ledger unreadable for report "
              f"{report_id}: {type(e).__name__}: {e}", file=sys.stderr)

    decided = set()
    try:
        raw = conn.execute("SELECT drew_approved FROM self_eval_reports WHERE id=?",
                           (report_id,)).fetchone()
        data = json.loads(raw[0]) if raw and raw[0] else {}
        if isinstance(data, dict):
            for bucket in ('approved', 'rejected'):
                for entry in (data.get(bucket) or []):
                    t = entry.get('title') if isinstance(entry, dict) else entry
                    if t:
                        decided.add(str(t))
    except (ValueError, TypeError, sqlite3.Error) as e:
        print(f"[monthly_self_eval] drew_approved unreadable for report "
              f"{report_id}: {type(e).__name__}: {e}", file=sys.stderr)
    try:
        decided |= {r[0] for r in conn.execute(
            "SELECT proposal_title FROM self_eval_enacted_proposals WHERE report_id=?",
            (report_id,))}
    except sqlite3.Error as e:
        print(f"[monthly_self_eval] enacted proposals unreadable for report "
              f"{report_id}: {type(e).__name__}: {e}", file=sys.stderr)
    return decided


def _ledger_write_failure_reason(conn, what: str, subject: str, unit_id: int,
                                 outcome: str, evidence_source: str,
                                 evidence_rowid, exc) -> str:
    """R27' -- ONE SPELLING. The rule lives in `decision_units`; this is the
    call-site adapter for it, not a second copy of it.

    R28's diagnosis rule was written out twice -- here and in
    `decision_units._write_failure_reason` -- with AST-normalised bodies that
    compare IDENTICAL, only their annotations differing. A rule spelled twice is
    a rule that will eventually be corrected once, so the body is gone and the
    public `decision_units.write_failure_reason` is called instead.

    THE `_DU is None` BRANCH IS NOT A SECOND SPELLING OF THE RULE. It is what
    this file says when it CANNOT consult the rule: it names the absence and
    passes SQLite's own message through, exactly as every other optional-ledger
    path in this file states its absence rather than skipping silently. It never
    guesses a cause -- guessing a cause in a refusal path is the R28 defect.
    """
    if _DU is None:
        return (f"{what} for {subject} was NOT written, and this file cannot say "
                f"WHY: decision_units is unavailable ({_DU_IMPORT_ERROR}), so the "
                f"refusal could not be diagnosed against the ledger "
                f"(unit_id={unit_id}, outcome={outcome!r}, "
                f"evidence_source={evidence_source!r}, "
                f"evidence_rowid={evidence_rowid!r}). The database refused the "
                f"write with {type(exc).__name__}: {exc}. A decision that did not "
                f"land must never report ok.")
    return _DU.write_failure_reason(conn, what, subject, unit_id, outcome,
                                    evidence_source, evidence_rowid, exc)


#: R26. The statuses of `self_eval_enacted_proposals` that mean THE PROPOSAL IS
#: DECIDED. Everything else is undecided, INCLUDING a status added to the
#: column's CHECK after this line was written -- that is the point of naming the
#: decided set rather than the undecided one. The column's CHECK today is
#: ('pending', 'enacted', 'failed', 'deferred'); 'rejected' is listed for the
#: forward-compatible case and is harmless while it is unreachable.
_DECIDED_PROPOSAL_STATUSES = ('enacted', 'rejected')


def _undecided_proposal_count(conn, report_id: int) -> int:
    """How many of this report's proposals are still UNDECIDED.

    R26 -- THE ENACTED FLIP, measured 2026-09-02. The flip to `status='enacted'`
    was gated on `COUNT(*) ... WHERE status = 'pending'`, an ALLOWLIST of one.
    But `enact_approved` itself writes `'deferred'` on a protected-organ block
    and `'failed'` when an enactment raises, and neither is `'pending'`. So a run
    that blocked or failed a proposal flipped the run record to `'enacted'` while
    that proposal's decision unit was still OPEN: `pending_left=0` while
    `not_decided=1`.

    The consequence is not cosmetic. `derive_report_state` then returns
    `'pending'` against a stored `'enacted'`, and that divergence is created
    AFTER the historical registration, which `migrate --apply` refuses to
    register (curation_4 C-7). `assert_status_agreement` therefore raises on that
    report FOREVER -- the assertion-that-always-fires that `status_column_ban.py`
    names as the thing people switch off.

    A DENYLIST, NOT AN ALLOWLIST. Naming the two DECIDED statuses means the next
    status added to the column is undecided by default: the report stays visible
    and noisy rather than silently flipping. That is the same safe direction
    `_admissible_counts` takes on an unparseable column.
    """
    marks = ",".join("?" * len(_DECIDED_PROPOSAL_STATUSES))
    return int(conn.execute(
        f"SELECT COUNT(*) FROM self_eval_enacted_proposals "
        f" WHERE report_id = ? AND status NOT IN ({marks})",
        (report_id, *_DECIDED_PROPOSAL_STATUSES)).fetchone()[0])


def _decision_actor(actor: str | None) -> str:
    """Who made a decision. 2026-10-26: the ledger wrote the literal 'drew' for every
    decision, so a stranger's install signed its owner's choices with someone else's
    name and an automatic decision could not be told from a human one. An explicit
    actor wins (the auto policy passes 'policy:auto'); otherwise the derived one."""
    if actor:
        return actor
    if _DU is not None and hasattr(_DU, "_default_actor"):
        return _DU._default_actor()
    import getpass
    try:
        return getpass.getuser() or "operator"
    except Exception:
        return "operator"


def _record_decisions(conn, report_id: int, *, approved, declined,
                      actor: str | None = None) -> None:
    """Append one decision event per title into the ledger, on the SAME
    connection and therefore inside the SAME transaction as the review itself.

    That is the point: the audit log was the only durable evidence of the
    2026-08-02 loss and it was written on a path documented "BEST-EFFORT", inside
    a swallow -- had it failed, the defect would be undiscoverable today. A
    decision and its record commit together or not at all. A ledger failure is
    REPORTED and re-raised rather than absorbed.
    """
    has_ledger = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='decision_units'"
    ).fetchone() is not None
    if not has_ledger:
        print(f"[monthly_self_eval] migration 030 absent; decisions on report "
              f"{report_id} are recorded in drew_approved only", file=sys.stderr)
        return
    now = datetime.now().isoformat()
    # decision_events carries UNIQUE (unit_id, evidence_source, evidence_rowid,
    # outcome). With evidence_rowid = report_id and evidence_source = 'live', a
    # unit could carry only ONE live event per outcome, EVER, and any second one
    # was dropped by INSERT OR IGNORE while this function reported nothing. A
    # monotonic live sequence makes each recorded decision its own fact.
    _seq = (conn.execute("SELECT COALESCE(MAX(evidence_rowid), 0) FROM decision_events "
                         " WHERE evidence_source = 'live'").fetchone()[0] or 0)
    # R27. Counted, not merely printed. See the raise after the loop.
    _unattributed = 0
    for outcome, titles in (('approved', approved), ('declined', declined)):
        for title in titles:
            if not title:
                continue
            row = conn.execute(
                "SELECT id FROM decision_units WHERE report_id=? AND proposal_title=?",
                (report_id, title)).fetchone()
            if row is None:
                # A decision that cannot be attached to a unit must be VISIBLE.
                print(f"[monthly_self_eval] no decision unit for "
                      f"(report {report_id}, {title!r}); recording it as a "
                      f"backfill exception rather than dropping it", file=sys.stderr)
                conn.execute(
                    "INSERT OR IGNORE INTO decision_backfill_exceptions "
                    "(source_table, source_rowid, ref, reason_code, reason) "
                    "VALUES ('self_eval_reports', ?, ?, 'live_title_not_a_unit', ?)",
                    (report_id, f"self-eval:{report_id}",
                     f"{title!r} was decided but has no decision unit on report "
                     f"#{report_id}"))
                _unattributed += 1
                continue
            _seq += 1
            try:
                # R28. A PLAIN INSERT: `INSERT OR IGNORE` swallows CHECK, NOT
                # NULL and FOREIGN KEY exactly as it swallows UNIQUE, so
                # rowcount 0 was never proof of a duplicate. An insert that
                # wrote NOTHING must never be reported as a recorded decision;
                # raising here aborts the whole review before its commit, so the
                # decision and its record still land together or not at all --
                # but the reader is now told WHICH refusal happened.
                conn.execute(
                    "INSERT INTO decision_events "
                    "(unit_id, outcome, decided_at, actor, evidence_source, evidence_rowid, "
                    " detail_json) VALUES (?, ?, ?, ?, 'live', ?, ?)",
                    (int(row[0]), outcome, now, _decision_actor(actor), _seq,
                     json.dumps({"report_id": report_id, "live_seq": _seq})))
            except sqlite3.IntegrityError as _e:
                raise RuntimeError(_ledger_write_failure_reason(
                    conn, "decision event",
                    f"(report {report_id}, {title!r}, {outcome})",
                    int(row[0]), outcome, 'live', _seq, _e)) from _e
    if _unattributed:
        # R27 -- THE ROUND'S VERBATIM INVARIANT: success from any verb implies
        # the ledger grew by exactly one event, or the call raised. Measured
        # 2026-09-02: `drew_review` returned `status='ok'`, `decision_events`
        # grew by ZERO, nothing raised -- while this function printed its own
        # warning to stderr on the way past. The sibling
        # `decision_units.record_decision` RAISES for the identical class, with
        # the comment "A decision that did not land must never report ok." Two
        # spellings of one rule, disagreeing; this is the one that was wrong.
        #
        # The caller commits AFTER this returns, so raising rolls the whole
        # review back -- which is the contract this function's own docstring
        # already claimed ("A decision and its record commit together or not at
        # all"). MEASURED, not assumed (_round5/probe_r27_rollback.py): after
        # the raise, decision_events, decision_backfill_exceptions,
        # self_eval_enacted_proposals and the report's own drew_approved and
        # status are ALL byte-identical to before the call. So the message must
        # not tell the reader the exceptions were "recorded" -- they were not.
        raise RuntimeError(
            f"{_unattributed} decision(s) on report {report_id} could not be "
            f"attached to a decision unit, so this review must NOT report ok. "
            f"The backfill-exception rows written for them are rolled back "
            f"WITH the review by the caller's abandoned transaction — they "
            f"record a review that did not happen, and re-running the verb "
            f"after creating the missing unit(s) is the repair. A decision "
            f"that did not land must never report ok.")


def _record_enactments(conn, report_id: int, titles, actor: str | None = None) -> int:
    """Append one `enacted` decision event per ENACTED title, on the SAME
    connection and therefore inside the SAME transaction as the enactment.

    R22 — FOUND BY WALKING THE FORWARD CYCLE, 2026-09-02. `_project_report_status`
    names its own witness three lines into its docstring:

        "the row that would look different if 'enacted' were false: on a
         migrated brain, a decision_events row with outcome='enacted'"

    NOTHING ON THE FORWARD PATH WROTE THAT ROW. Every `enacted` event on this
    brain was written by `migrate()`'s BACKFILL of history; a live enactment
    wrote `self_eval_enacted_proposals.status='enacted'` and flipped the run
    record, and the ledger never heard about it. Measured on the first full
    forward walk: `enact_approved` returned `status=ok, enacted=4` while
    `decision_events` grew by ZERO, and `assert_status_agreement` then raised on
    report #8 with `stored='enacted' derived='drew_reviewed' enacted_units=0`.

    It is the SAME SHAPE as `_derive_status`'s defect and it is the seventh
    instance of "correct logic, on no path": a docstring naming a row, beside a
    code path that never writes it. The historical canary could not show it,
    because every report that had ever been enacted was enacted BEFORE the
    ledger existed and was reconstructed by the backfill.

    THE INVARIANT: success from this verb implies the ledger grew by exactly one
    event per enacted title, or the call raised. An INSERT that wrote nothing is
    re-raised, never reported as a recorded enactment.
    """
    has_ledger = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='decision_units'"
    ).fetchone() is not None
    if not has_ledger:
        print(f"[monthly_self_eval] migration 030 absent; the enactment of report "
              f"{report_id} is recorded in self_eval_enacted_proposals only",
              file=sys.stderr)
        return 0
    now = datetime.now().isoformat()
    _seq = (conn.execute("SELECT COALESCE(MAX(evidence_rowid), 0) FROM decision_events "
                         " WHERE evidence_source = 'live'").fetchone()[0] or 0)
    written = 0
    _unattributed = 0   # R27, same invariant as _record_decisions.
    for title in titles:
        if not title:
            continue
        row = conn.execute(
            "SELECT id FROM decision_units WHERE report_id=? AND proposal_title=?",
            (report_id, title)).fetchone()
        if row is None:
            print(f"[monthly_self_eval] no decision unit for (report {report_id}, "
                  f"{title!r}); recording the enactment as a backfill exception "
                  f"rather than dropping it", file=sys.stderr)
            conn.execute(
                "INSERT OR IGNORE INTO decision_backfill_exceptions "
                "(source_table, source_rowid, ref, reason_code, reason) "
                "VALUES ('self_eval_reports', ?, ?, 'live_enact_title_not_a_unit', ?)",
                (report_id, f"self-eval:{report_id}",
                 f"{title!r} was ENACTED but has no decision unit on report "
                 f"#{report_id}"))
            _unattributed += 1
            continue
        _seq += 1
        try:
            # R28, same class as _record_decisions above.
            conn.execute(
                "INSERT INTO decision_events "
                "(unit_id, outcome, decided_at, actor, evidence_source, evidence_rowid, "
                " detail_json) VALUES (?, 'enacted', ?, ?, 'live', ?, ?)",
                (int(row[0]), now, _decision_actor(actor), _seq,
                 json.dumps({"report_id": report_id, "live_seq": _seq,
                             "source": "enact_approved"})))
        except sqlite3.IntegrityError as _e:
            raise RuntimeError(_ledger_write_failure_reason(
                conn, "enactment event", f"(report {report_id}, {title!r})",
                int(row[0]), 'enacted', 'live', _seq, _e)) from _e
        written += 1
    if _unattributed:
        # R27. Identical to _record_decisions: an enactment that did not reach
        # the ledger must not be reported as one. enact_approved commits after
        # this returns, so raising rolls back the enactment AND the run-record
        # flip together.
        raise RuntimeError(
            f"{_unattributed} enactment(s) on report {report_id} could not be "
            f"attached to a decision unit, so this enactment must NOT report "
            f"ok. The backfill-exception rows written for them are rolled back "
            f"WITH the enactment and the run-record flip. An enactment that "
            f"did not land must never report ok.")
    return written


# ============================================================
# Enact Approved Proposals
# ============================================================

#: L5-I-β variance-gate: the magnitude bound on a single enactment. An enact
#: that would apply MORE than this many proposals at once requires explicit
#: re-confirmation (confirm=True). Tuned so a normal small self-eval enact (1-3
#: proposals) passes untouched; only a bulk enactment trips the gate.
VARIANCE_GATE_MAX_PROPOSALS = 5

#: Verbs that mark a proposal whose EFFECT is the removal of a piece. Matched
#: case-insensitively against the proposal title. Kept broad on purpose: this is
#: a defence-in-depth net, so a generously-matching verb set is the safe bias.
_DEPRECATION_TITLE_VERBS = (
    "deprecat",   # "Deprecate", "Consider deprecating", "deprecation of ..."
    "remove",
    "delete",
    "retire",
    "tear down",
    "teardown",
    "disable permanently",
    "decommission",
    "drop the",
)


def _proposal_is_deprecation(proposal: Dict[str, Any],
                             deprecation_titles: "set[str]") -> bool:
    """True when an approved proposal's EFFECT is the deprecation/removal of a
    piece. Two independent signals (either suffices — defence-in-depth):

      1. The proposal's title is one of the report's recorded
         ``proposed_deprecations`` (the authoritative source — a proposal that
         CAME from the deprecation list IS a deprecation), OR
      2. The title carries a removal verb (``_DEPRECATION_TITLE_VERBS``) — a
         belt-and-braces catch for a proposal that names a removal but was
         (mis)filed among enhancements.

    Pure + total: a malformed proposal yields False (never raises)."""
    try:
        title = str(proposal.get("title", "") or "")
    except Exception:
        return False
    if title and title in deprecation_titles:
        return True
    low = title.lower()
    return any(verb in low for verb in _DEPRECATION_TITLE_VERBS)


def _protected_organ_block(proposal: Dict[str, Any], registry: Dict[str, Any],
                           deprecation_titles: "set[str]") -> Optional[Dict[str, Any]]:
    """The enactment-layer protected-organ guard (#6248, defence-in-depth).

    Mirrors the SAME rule already enforced at proposal GENERATION (the heuristic
    + LLM paths only ever propose deprecating a piece the registry flags
    ``deprecatable: true``). Here we enforce it a SECOND time, at ENACT — so even
    a deprecation that somehow reached 'approved' status (a stale row, a hand-
    inserted proposal, a future destructive enact path) can NEVER remove a
    protected organ. Enact is record-only today; this makes the invariant hold
    for any future path that turns an approved deprecation into a real removal.

    Returns None when the proposal is safe to enact, or a typed block dict
    ``{code, message, piece_affected}`` when it must be refused. Pure + total:
    any internal error yields None (fail-OPEN to the *existing* record-only
    enact behaviour — the guard must never itself break a legitimate enact; the
    structural protection is that no registered organ is ``deprecatable``, and a
    future destructive removal path would carry its own removal-time guard)."""
    try:
        if not _proposal_is_deprecation(proposal, deprecation_titles):
            return None
        piece = str(proposal.get("piece_affected", "") or "").strip()
        if not piece:
            # A deprecation that names no piece cannot be matched to the registry.
            # It is record-only today (a memory note), so allow it through — there
            # is no organ for it to remove. (If a future path makes deprecation
            # destructive, that path must resolve the target itself.)
            return None
        # iter-2 (Drassil: case/whitespace evasion). The exact-case registry.get
        # below would MISS a deprecation that names the organ with a different
        # case or stray internal whitespace (e.g. 'L2-C1-Hybrid-Retrieval' vs the
        # key 'L2-C1-hybrid-retrieval') -> the guard would wave it through as "not
        # a registered organ". We FIRST try the exact key (no behaviour change for
        # the legit path), then fall back to a casefold()+strip() normalized
        # lookup so a registered organ is matched regardless of case/whitespace
        # and the case-evasion attack cannot slip a protected-organ deprecation
        # past the guard.
        entry = registry.get(piece)
        if entry is None:
            piece_norm = piece.casefold()
            for _k, _v in registry.items():
                try:
                    if str(_k).strip().casefold() == piece_norm:
                        entry = _v
                        break
                except Exception:
                    continue
        if entry is None:
            # Not a registered organ -> not a protected piece. Allow.
            return None
        if entry.get("deprecatable", False):
            # The registry explicitly permits deprecating this piece. Allow.
            return None
        return {
            "code": "protected_organ_deprecation_blocked",
            "message": (
                f"refusing to enact deprecation of protected organ "
                f"{piece!r}: it is a REGISTERED organ and its registry entry "
                f"does not flag 'deprecatable: true'. A deliberately-built organ "
                f"is never removed by an approved self-eval proposal "
                f"(defence-in-depth #6248)."
            ),
            "piece_affected": piece,
        }
    except Exception:
        return None


def enact_approved(db_path: str, report_id: int,
                   config: Dict[str, Any] | None = None,
                   confirm: bool = False,
                   actor: str | None = None) -> Dict[str, Any]:
    """R5/C3 GUARDED -- see `_brain_exists`. Measured before the guard:
    `--enact N` on a path with no brain created a 0-BYTE file.

    Execute approved proposals: create memories, store tasks.

    For each approved proposal in self_eval_enacted_proposals, creates
    a procedural memory capturing the enhancement task and marks it enacted.

    L5-I-β VARIANCE-GATE (calm; owner-gated in manual mode -- in approvals.mode
    auto the policy passes confirm=True for the report it just wrote, under the
    owner's approvals.mode setting -- the install default, the owner's answer, or
    voidloom approvals --mode (2026-10-26)): the gate bounds the MAGNITUDE of a
    single enacted change. If this enact would apply more than
    ``VARIANCE_GATE_MAX_PROPOSALS`` proposals at once, it requires an explicit
    ``confirm=True`` re-confirmation — it returns a ``needs_confirm`` result that
    PRINTS each proposal first and enacts NOTHING (mirroring the #6100 scar:
    confirm before enacting, show each proposal, never a silent bulk mutation).
    A normal small enact (the common case) passes untouched. ``config`` may carry
    ``variance_gate_max`` to override the bound; ``variance_gate_disabled`` (or
    the env VOIDLOOM_NO_GOVERNANCE) disables the gate entirely (calm pass-through).
    """
    # R5/C3. THE GUARD, at the verb rather than at one of five verbs. The house
    # pattern (claude_memory.py:641): an existence check plus a NAMED error --
    # never `?mode=rw`, which converts {"status":"ok"}+exit 0 into an uncaught
    # traceback+exit 1 (VAEL, Council #5).
    if not _brain_exists(db_path):
        return _brain_absent_error(db_path, "enact_approved")
    cfg = config or {}
    conn = sqlite3.connect(db_path, timeout=60.0)
    try:
        cur = conn.execute(
            "SELECT id, proposal_title, proposal_json FROM self_eval_enacted_proposals "
            "WHERE report_id = ? AND status = 'pending'",
            (report_id,)
        )
        pending = cur.fetchall()

        if not pending:
            return {
                'status': 'ok',
                'action': 'nothing_to_enact',
                'report_id': report_id,
            }

        # --- L5-I-β variance-gate -------------------------------------------
        gate_off = bool(cfg.get('variance_gate_disabled')) or _governance_kill_switch()
        gate_max = int(cfg.get('variance_gate_max', VARIANCE_GATE_MAX_PROPOSALS))
        magnitude = len(pending)
        if (not gate_off) and (not confirm) and magnitude > gate_max:
            # Over-threshold: do NOT enact. Surface each proposal + the exact
            # re-confirm instruction. This is calm (a held bulk change for the owner
            # to confirm), never a silent drop and never autonomous.
            preview = []
            for row_id, title, proposal_json in pending:
                try:
                    pj = json.loads(proposal_json)
                except (TypeError, ValueError):
                    pj = {}
                preview.append({
                    'enacted_proposal_id': row_id,
                    'title': title,
                    'priority': pj.get('priority'),
                    'piece_affected': pj.get('piece_affected'),
                })
            return {
                'status': 'needs_confirm',
                'action': 'variance_gate_held',
                'report_id': report_id,
                'magnitude': magnitude,
                'variance_gate_max': gate_max,
                'reason': (f"this enact would apply {magnitude} proposals at once "
                           f"(> the variance-gate bound of {gate_max}); re-run with "
                           f"confirm=True to enact all {magnitude} after reviewing "
                           f"them below"),
                'proposals': preview,
                'enacted': [],
                'failed': [],
            }

        enacted = []
        failed = []
        blocked = []

        # --- Enactment-layer protected-organ guard (#6248, defence-in-depth) ----
        # Load the canonical piece registry + the report's recorded deprecation
        # titles ONCE, so the per-proposal guard below is a cheap dict lookup. A
        # deprecation of a REGISTERED organ that is not flagged `deprecatable`
        # is refused at enact even if the proposal reached 'approved'. Both reads
        # are fail-graceful: any error leaves an empty registry / title set (the
        # guard then degrades to the existing record-only behaviour, never
        # crashing a legitimate enact).
        # R30. This was `(config or {}).get('piece_registry') or
        # DEFAULT_CONFIG.get(...)` -- an explicit REPLACE-or-default, so a
        # config carrying a PARTIAL registry disarmed this guard for every
        # organ it did not name. Measured: blocked=1 -> blocked=0 and the
        # protected organ enacted. It now takes the same merged view every
        # other config reader in this file takes.
        registry = _merge_config(config).get('piece_registry', {})
        deprecation_titles: "set[str]" = set()
        try:
            drow = conn.execute(
                "SELECT proposed_deprecations FROM self_eval_reports WHERE id = ?",
                (report_id,)
            ).fetchone()
            if drow and drow[0]:
                for dp in (json.loads(drow[0]) or []):
                    t = dp.get('title') if isinstance(dp, dict) else None
                    if t:
                        deprecation_titles.add(str(t))
        except Exception:
            deprecation_titles = set()

        for row_id, title, proposal_json in pending:
            try:
                proposal = json.loads(proposal_json)

                # Protected-organ deprecation guard: refuse BEFORE any enactment
                # side-effect. A blocked proposal is marked 'deferred' (a valid
                # status — held, not failed) with the protection reason recorded
                # in error_log (the durable audit), and is reported in `blocked`.
                _block = _protected_organ_block(proposal, registry,
                                                deprecation_titles)
                if _block is not None:
                    conn.execute(
                        "UPDATE self_eval_enacted_proposals SET status = 'deferred', "
                        "error_log = ? WHERE id = ?",
                        (f"[{_block['code']}] {_block['message']}", row_id)
                    )
                    blocked.append({
                        'title': title,
                        'code': _block['code'],
                        'piece_affected': _block.get('piece_affected'),
                        'message': _block['message'],
                    })
                    continue

                content = (
                    f"APPROVED ENHANCEMENT: {title}\n\n"
                    f"Rationale: {proposal.get('rationale', 'N/A')}\n"
                    f"Evidence: {proposal.get('evidence', 'N/A')}\n"
                    f"Priority: {proposal.get('priority', 'N/A')}\n"
                    f"Estimated effort: {proposal.get('estimated_effort', 'N/A')}\n"
                    f"Piece affected: {proposal.get('piece_affected', 'N/A')}\n\n"
                    f"Approved by the owner via self-eval report #{report_id}."
                )
                summary = f"Self-eval approved: {title}"

                cur2 = conn.execute(
                    "INSERT INTO memories (content, summary, category, importance, "
                    "source_project, kind) VALUES (?, ?, 'workflow_pattern', 0.8, "
                    "'wizard-library', 'procedural')",
                    (content, summary)
                )
                memory_id = cur2.lastrowid
                # TO-03: this memory must be findable.
                _vl_attach(conn, memory_id, content, "monthly_self_eval.enact_approved")

                conn.execute(
                    "INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                    (memory_id, 'self-eval-approved')
                )
                conn.execute(
                    "INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                    (memory_id, 'enhancement-task')
                )

                conn.execute(
                    "UPDATE self_eval_enacted_proposals SET status = 'enacted', "
                    "enacted_at = datetime('now'), enacted_memory_id = ? WHERE id = ?",
                    (memory_id, row_id)
                )

                enacted.append({'title': title, 'memory_id': memory_id})

            except Exception as e:
                conn.execute(
                    "UPDATE self_eval_enacted_proposals SET status = 'failed', "
                    "error_log = ? WHERE id = ?",
                    (str(e), row_id)
                )
                failed.append({'title': title, 'error': str(e)})

        # R22. The ledger learns about the enactment BEFORE the run record is
        # flipped, on this connection and inside this transaction. If the ledger
        # write fails the whole enactment rolls back with it, so "the tool says
        # enacted" and "the ledger says enacted" cannot come apart.
        _events_written = _record_enactments(conn, report_id,
                                             [e['title'] for e in enacted],
                                             actor=actor)

        cur = conn.execute("SELECT status FROM self_eval_reports WHERE id = ?", (report_id,))
        report_row = cur.fetchone()
        if report_row and report_row[0] == 'drew_reviewed':
            # R26. `status = 'pending'` counted one of the three undecided
            # statuses this very function can write. `_undecided_proposal_count`
            # names the DECIDED set instead, so 'deferred' (written at the
            # protected-organ block above) and 'failed' (written by the except
            # arm above) both hold the flip.
            undecided_left = _undecided_proposal_count(conn, report_id)
            if undecided_left == 0:
                conn.execute(
                    "UPDATE self_eval_reports SET status = 'enacted' WHERE id = ?",
                    (report_id,)
                )

        conn.commit()

        result = {
            'status': 'ok',
            'action': 'proposals_enacted',
            'report_id': report_id,
            'enacted': enacted,
            'ledger_events_written': _events_written,
            'failed': failed,
            # Protected-organ guard (#6248): proposals refused at enact because
            # they would deprecate a registered non-deprecatable organ. Always
            # present (empty when none) so callers have a stable contract.
            'blocked': blocked,
        }
        if blocked:
            # Surface the protection prominently so the approvals audit + any
            # caller sees it was not silently dropped.
            result['protected_organ_blocks'] = len(blocked)
        return result

    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        conn.close()


# ============================================================
# Report Retrieval
# ============================================================


# ---------------------------------------------------------------------------
# R4/P4 -- THE HOUSE REFUSAL, borrowed verbatim in shape from
# claude_memory.py:641 (`if not DB_PATH.exists(): _error(...)`).
# ---------------------------------------------------------------------------

def _brain_state(db_path: str) -> str:
    """"ok" | "brain_not_found" | "brain_empty".

    R4/P4 pass 3. THREE STATES, NOT TWO. A 0-byte file is not a brain
    and it is not an absence either -- it is the wreckage of a failed
    init (P7) or an interrupted copy, and the remedy differs: absence
    means "run init", emptiness means "delete this, THEN run init".
    Collapsing them would re-commit the exact collapse P1 is about.
    """
    try:
        p = Path(db_path)
        if not p.is_file():
            return "brain_not_found"
        if p.stat().st_size == 0:
            return "brain_empty"
        return "ok"
    except (OSError, TypeError, ValueError):
        return "brain_not_found"


def _brain_absent_error(db_path: str, verb: str) -> Dict[str, Any]:
    """The named refusal a read-shaped verb returns instead of CREATING a brain.

    WHY THIS IS NOT PEDANTRY. `sqlite3.connect(path)` on a missing file creates
    it. A verb whose name promises a READ then reports `no_report_found` against
    a 0-byte database IT JUST MANUFACTURED -- an honest "there is no brain here"
    converted into a legitimate-looking "there is nothing pending", plus a
    poisoned file left on disk that the installer's own `[ -f ]` idempotence
    test will later accept as a real brain.
    """
    state = _brain_state(db_path)
    if state == "brain_empty":
        return {"status": "error", "error": "brain_empty", "action": verb,
                "database": db_path, "report": None,
                "message": (f"the file at {db_path} is 0 bytes -- not a "
                            f"brain. An earlier init or copy failed and "
                            f"left it behind. Remove it, then run 'init'.")}
    return {"status": "error", "error": "brain_not_found", "action": verb,
            "database": db_path, "report": None,
            "message": (f"no brain database at {db_path}. Refusing to CREATE "
                        f"one from a read verb ({verb}); run 'init' first.")}


def _brain_exists(db_path: str) -> bool:
    """A 0-byte file is NOT an existing brain (pass 3)."""
    return _brain_state(db_path) == "ok"


def get_report(db_path: str, month: str | None = None,
               report_id: int | None = None) -> Dict[str, Any]:
    """Retrieve a self-eval report by month or ID.

    R4/P4. REFUSES on an absent brain instead of creating one. This is
    the read-shaped verb of the three the council named, and the only
    one of the three whose NAME promises a read.
    """
    if not _brain_exists(db_path):
        return _brain_absent_error(db_path, "get_report")
    conn = sqlite3.connect(db_path, timeout=60.0)
    try:
        if report_id is not None:
            cur = conn.execute(
                "SELECT * FROM self_eval_reports WHERE id = ?", (report_id,)
            )
        elif month is not None:
            cur = conn.execute(
                "SELECT * FROM self_eval_reports WHERE month = ? ORDER BY created_at DESC LIMIT 1",
                (month,)
            )
        else:
            cur = conn.execute(
                "SELECT * FROM self_eval_reports ORDER BY created_at DESC LIMIT 1"
            )

        cols = [desc[0] for desc in cur.description]
        row = cur.fetchone()

        if row is None:
            return {"status": "ok", "action": "no_report_found", "report": None}

        report = dict(zip(cols, row))
        for key in ('health_scorecard', 'proposed_enhancements', 'proposed_deprecations',
                     'threshold_assessment', 'persona_evolution', 'drew_approved'):
            if report.get(key) and isinstance(report[key], str):
                try:
                    report[key] = json.loads(report[key])
                except json.JSONDecodeError:
                    pass

        enacted_cur = conn.execute(
            "SELECT id, proposal_title, status, enacted_at, enacted_memory_id "
            "FROM self_eval_enacted_proposals WHERE report_id = ?",
            (report['id'],)
        )
        report['enacted_proposals'] = [
            dict(zip([d[0] for d in enacted_cur.description], r))
            for r in enacted_cur.fetchall()
        ]

        return {"status": "ok", "action": "report_retrieved", "report": report}

    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        conn.close()


# ============================================================
# Unit Tests
# ============================================================

def unit_test_monthly_self_eval():
    """Self-contained unit tests. Run with --unit-tests flag."""
    import tempfile
    passed = 0
    failed = 0
    total = 0

    def _assert(condition, name):
        nonlocal passed, failed, total
        total += 1
        if condition:
            passed += 1
            print(f"  PASS: {name}")
        else:
            failed += 1
            print(f"  FAIL: {name}")

    def _create_test_db():
        tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        schema_path = Path(__file__).parent.parent / "migrations" / "schema.sql"
        if schema_path.exists():
            conn.executescript(schema_path.read_text())
        migration_path = Path(__file__).parent.parent / "migrations" / "migration_021_self_eval.sql"
        if migration_path.exists():
            conn.executescript(migration_path.read_text())
        _ensure_core_tables(conn)
        conn.commit()
        conn.close()
        return tmp.name

    def _ensure_core_tables(conn):
        conn.execute("""CREATE TABLE IF NOT EXISTS memories (
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
        )""")
        # schema.sql lays the BASE memories table (13 columns), so the CREATE above is skipped and the columns later
        # migrations add were missing: the self-test died at Persona Evolution ("no such column: kind").
        _have = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
        for _col, _decl in (("kind", "TEXT DEFAULT 'episodic'"), ("derived_from", "TEXT DEFAULT NULL"),
                            ("consolidated_at", "TEXT DEFAULT NULL"), ("confidence", "REAL DEFAULT 1.0"),
                            ("foundation_score", "REAL DEFAULT NULL"), ("skill_tier", "TEXT DEFAULT NULL")):
            if _col not in _have:
                conn.execute("ALTER TABLE memories ADD COLUMN %s %s" % (_col, _decl))
        conn.execute("""CREATE TABLE IF NOT EXISTS memory_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            UNIQUE(memory_id, tag)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL,
            access_type TEXT NOT NULL,
            query_text TEXT,
            relevance_score REAL,
            accessed_at TEXT DEFAULT (datetime('now'))
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS sleep_loop_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT DEFAULT NULL,
            status TEXT DEFAULT 'running',
            memories_consolidated INTEGER DEFAULT 0,
            clusters_found INTEGER DEFAULT 0,
            edges_extracted INTEGER DEFAULT 0,
            edges_decayed INTEGER DEFAULT 0,
            edges_pruned INTEGER DEFAULT 0,
            reflections_generated INTEGER DEFAULT 0,
            importance_adjustments INTEGER DEFAULT 0,
            consolidation_mode TEXT DEFAULT 'heuristic',
            error_log TEXT DEFAULT NULL,
            config_snapshot TEXT DEFAULT NULL,
            duration_seconds REAL DEFAULT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS memory_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            predicate TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.8,
            valid_from TEXT DEFAULT NULL,
            valid_until TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            metadata TEXT DEFAULT NULL,
            extraction_method TEXT DEFAULT 'manual'
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS skill_usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL,
            used_at TEXT NOT NULL DEFAULT (datetime('now')),
            context TEXT DEFAULT NULL,
            outcome TEXT DEFAULT 'unknown',
            outcome_notes TEXT DEFAULT NULL,
            triggered_by TEXT DEFAULT 'recall',
            session_id TEXT DEFAULT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS dream_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dream_type TEXT NOT NULL,
            memory_a_id INTEGER NOT NULL,
            memory_b_id INTEGER NOT NULL,
            cosine_distance REAL,
            context_difference TEXT DEFAULT NULL,
            dream_content TEXT DEFAULT NULL,
            dream_memory_id INTEGER DEFAULT NULL,
            is_meaningful INTEGER DEFAULT 0,
            drew_flagged_interesting INTEGER DEFAULT 0,
            batch_id INTEGER DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS whisper_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observation TEXT NOT NULL,
            observation_type TEXT NOT NULL,
            session_context TEXT DEFAULT NULL,
            intensity REAL DEFAULT 0.5,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            digested INTEGER DEFAULT 0,
            digest_id INTEGER DEFAULT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS whisper_digests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            digest_memory_id INTEGER,
            observations_count INTEGER NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS calibration_log (
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
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS gap_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity TEXT NOT NULL,
            gap_type TEXT NOT NULL,
            score REAL NOT NULL,
            memory_count INTEGER DEFAULT 0,
            last_mentioned TEXT DEFAULT NULL,
            researched INTEGER DEFAULT 0,
            research_memory_id INTEGER DEFAULT NULL,
            inventoried_at TEXT NOT NULL DEFAULT (datetime('now')),
            batch_id INTEGER DEFAULT NULL,
            acknowledged INTEGER DEFAULT 0,
            acknowledged_at TEXT DEFAULT NULL,
            acknowledged_reason TEXT DEFAULT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS gap_inventory_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT DEFAULT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            total_gaps INTEGER DEFAULT 0,
            thin_entity_count INTEGER DEFAULT 0,
            weak_recall_count INTEGER DEFAULT 0,
            contradiction_count INTEGER DEFAULT 0,
            sparse_domain_count INTEGER DEFAULT 0,
            stale_entity_count INTEGER DEFAULT 0,
            research_dispatched INTEGER DEFAULT 0,
            config_snapshot TEXT DEFAULT NULL,
            error_log TEXT DEFAULT NULL,
            duration_seconds REAL DEFAULT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS skill_graduation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL,
            old_tier TEXT,
            new_tier TEXT NOT NULL,
            reason TEXT NOT NULL,
            score REAL,
            graduated_at TEXT NOT NULL DEFAULT (datetime('now')),
            graduated_by TEXT DEFAULT 'cron'
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS resurfacing_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL,
            resurfaced_at TEXT NOT NULL DEFAULT (datetime('now')),
            reason TEXT NOT NULL,
            acknowledged INTEGER DEFAULT 0
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS map_generation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL DEFAULT (datetime('now')),
            output_path TEXT NOT NULL,
            total_memories INTEGER NOT NULL DEFAULT 0,
            total_edges INTEGER NOT NULL DEFAULT 0,
            sections_rendered INTEGER NOT NULL DEFAULT 0,
            generation_time_ms INTEGER NOT NULL DEFAULT 0,
            trigger TEXT NOT NULL DEFAULT 'manual',
            config_snapshot TEXT DEFAULT NULL,
            error_log TEXT DEFAULT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS persona_recall_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            persona_slug TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            description TEXT DEFAULT NULL,
            tag_weights TEXT NOT NULL DEFAULT '{}',
            category_weights TEXT NOT NULL DEFAULT '{}',
            kind_weights TEXT NOT NULL DEFAULT '{}',
            project_weights TEXT NOT NULL DEFAULT '{}',
            exclude_tags TEXT NOT NULL DEFAULT '[]',
            boost_recent_days INTEGER DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            is_active INTEGER NOT NULL DEFAULT 1
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS memory_provenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            derived_memory_id INTEGER NOT NULL,
            source_memory_id INTEGER NOT NULL,
            transformation_type TEXT NOT NULL,
            agent TEXT DEFAULT NULL,
            prompt_hash TEXT DEFAULT NULL,
            confidence REAL DEFAULT 0.8,
            metadata TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS cross_tier_bridges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            local_memory_id INTEGER NOT NULL,
            remote_tier TEXT NOT NULL,
            remote_db_path TEXT NOT NULL,
            remote_memory_id INTEGER NOT NULL,
            bridge_type TEXT NOT NULL,
            confidence REAL DEFAULT 0.7,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_verified TEXT DEFAULT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS embedding_upgrade_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            model_name TEXT NOT NULL DEFAULT 'test',
            embedding_dim INTEGER NOT NULL DEFAULT 1024,
            memories_processed INTEGER DEFAULT 0,
            memories_total INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'in_progress',
            error_message TEXT,
            ab_comparison_json TEXT,
            notes TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS council_sessions (
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
            participants TEXT DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT DEFAULT NULL,
            status TEXT NOT NULL DEFAULT 'in_progress'
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS pre_cognitive_recall_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            signals_json TEXT NOT NULL,
            memory_ids_json TEXT NOT NULL,
            tier_ids_json TEXT NOT NULL,
            ambiguous INTEGER NOT NULL DEFAULT 0,
            time_ms INTEGER NOT NULL DEFAULT 0,
            memories_used_json TEXT,
            memories_irrelevant_json TEXT,
            feedback_note TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            feedback_at TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS dream_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            divergent_pairs INTEGER DEFAULT 0,
            close_context_pairs INTEGER DEFAULT 0,
            persona_pairs INTEGER DEFAULT 0,
            meaningful_dreams INTEGER DEFAULT 0,
            null_dreams INTEGER DEFAULT 0,
            total_pairs INTEGER DEFAULT 0,
            duration_seconds REAL DEFAULT NULL,
            error_log TEXT DEFAULT NULL,
            config_snapshot TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")

    print("=" * 60)
    print("Monthly Self-Eval Unit Tests")
    print("=" * 60)

    # --- Test 1: Migration detection ---
    print("\n--- Migration Detection ---")
    db_path = _create_test_db()
    conn = sqlite3.connect(db_path)
    _assert(is_migration_applied(conn), "Migration detected after creation")
    conn.close()

    # --- Test 2: Empty scorecard ---
    print("\n--- Empty Scorecard ---")
    conn = sqlite3.connect(db_path)
    scorecard = compute_health_scorecard(conn, "2026-05")
    _assert(isinstance(scorecard, dict), "Scorecard is dict")
    _assert(len(scorecard) > 0, "Scorecard has pieces")
    for piece_name, data in scorecard.items():
        _assert('score' in data, f"{piece_name} has score")
        _assert('usage' in data, f"{piece_name} has usage")
        break
    conn.close()

    # --- Test 3: Scorecard with sleep loop data ---
    print("\n--- Scorecard With Data ---")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sleep_loop_runs (started_at, completed_at, status, duration_seconds) "
        "VALUES ('2026-05-15T08:00:00', '2026-05-15T08:01:00', 'success', 60)"
    )
    conn.execute(
        "INSERT INTO sleep_loop_runs (started_at, completed_at, status, duration_seconds) "
        "VALUES ('2026-05-16T08:00:00', '2026-05-16T08:01:00', 'error', 5)"
    )
    conn.commit()
    scorecard = compute_health_scorecard(conn, "2026-05")
    sleep_data = scorecard.get('L2-C3-sleep-loop', {})
    _assert(sleep_data.get('usage') == 2, "Sleep loop usage=2")
    _assert(sleep_data.get('errors') == 1, "Sleep loop errors=1")
    _assert(sleep_data.get('score', 0) > 0, "Sleep loop score > 0")
    conn.close()

    # --- Test 4: Calibration drift ---
    print("\n--- Calibration Drift ---")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO calibration_log (prediction_value, prediction_unit, prediction_kind, domain, "
        "actual_value, actual_at, ratio, created_at) "
        "VALUES (10, 'hours', 'time', 'test', 8, '2026-05-10', 0.8, '2026-05-10')"
    )
    conn.execute(
        "INSERT INTO calibration_log (prediction_value, prediction_unit, prediction_kind, domain, "
        "actual_value, actual_at, ratio, created_at) "
        "VALUES (10, 'hours', 'time', 'test', 9, '2026-05-20', 0.9, '2026-05-20')"
    )
    conn.commit()
    drift = _compute_calibration_drift(conn, "2026-05")
    _assert(drift['status'] == 'ok', "Calibration drift computed")
    _assert(drift['sample_size'] == 2, "2 calibration samples")
    conn.close()

    # --- Test 5: Gap trend ---
    print("\n--- Gap Trend ---")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO gap_inventory (entity, gap_type, score, inventoried_at) "
        "VALUES ('test_entity', 'thin_entity', 0.8, '2026-04-15')"
    )
    conn.commit()
    trend = _compute_gap_trend(conn, "2026-05")
    _assert(trend['status'] == 'ok', "Gap trend computed")
    _assert(trend['unresolved_before'] >= 0, "Unresolved count computed")
    conn.close()

    # --- Test 6: Cron health ---
    print("\n--- Cron Health ---")
    conn = sqlite3.connect(db_path)
    health = _compute_cron_health(conn, "2026-05")
    _assert(health['status'] == 'ok', "Cron health computed")
    _assert('cron_jobs' in health, "Has cron_jobs breakdown")
    conn.close()

    # --- Test 7: Dream quality ---
    print("\n--- Dream Quality ---")
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO memories (content, category) VALUES ('a', 'insight')")
    conn.execute("INSERT INTO memories (content, category) VALUES ('b', 'insight')")
    conn.commit()
    conn.execute(
        "INSERT INTO dream_log (dream_type, memory_a_id, memory_b_id, is_meaningful, "
        "drew_flagged_interesting, created_at) VALUES ('divergent', 1, 2, 1, 1, '2026-05-10')"
    )
    conn.execute(
        "INSERT INTO dream_log (dream_type, memory_a_id, memory_b_id, is_meaningful, "
        "drew_flagged_interesting, created_at) VALUES ('divergent', 1, 2, 0, 0, '2026-05-12')"
    )
    conn.commit()
    dq = _compute_dream_quality(conn, "2026-05")
    _assert(dq['status'] == 'ok', "Dream quality computed")
    _assert(dq['total_dreams'] == 2, "2 dreams")
    _assert(dq['meaningful'] == 1, "1 meaningful")
    _assert(dq['signal_to_noise_ratio'] == 0.5, "50% signal ratio")
    conn.close()

    # --- Test 8: Whisper patterns ---
    print("\n--- Whisper Patterns ---")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO whisper_observations (observation, observation_type, intensity, created_at) "
        "VALUES ('test obs', 'insight', 0.7, '2026-05-10')"
    )
    conn.commit()
    wp = _compute_whisper_patterns(conn, "2026-05")
    _assert(wp['status'] == 'ok', "Whisper patterns computed")
    _assert(wp['total_observations'] == 1, "1 observation")
    conn.close()

    # --- Test 9: Capacity headroom ---
    print("\n--- Capacity Headroom ---")
    conn = sqlite3.connect(db_path)
    cap = _compute_capacity_headroom(conn, "2026-05")
    _assert(cap['status'] == 'ok', "Capacity computed")
    _assert(cap['db_size_mb'] >= 0, "DB size >= 0")
    _assert(cap['db_status'] in ('healthy', 'warning', 'critical'), "Valid db_status")
    conn.close()

    # --- Test 10: Threshold assessment ---
    print("\n--- Threshold Assessment ---")
    conn = sqlite3.connect(db_path)
    threshold = assess_threshold(conn, "2026-05")
    _assert(threshold['current_threshold'] == 9.80, "Current threshold 9.80")
    _assert(threshold['recommendation'] in ('hold', 'increase', 'decrease'), "Valid recommendation")
    _assert(threshold['next_threshold'] == 9.85, "Next threshold 9.85")
    conn.close()

    # --- Test 11: Persona evolution ---
    print("\n--- Persona Evolution ---")
    conn = sqlite3.connect(db_path)
    pe = _compute_persona_evolution(conn, "2026-05")
    _assert(pe['status'] == 'ok', "Persona evolution computed")
    _assert(pe['new_memories'] >= 0, "Memory count >= 0")
    conn.close()

    # --- Test 12: Heuristic proposals ---
    print("\n--- Heuristic Proposals ---")
    conn = sqlite3.connect(db_path)
    scorecard = compute_health_scorecard(conn, "2026-05")
    supplemental = {
        'calibration_drift': _compute_calibration_drift(conn, "2026-05"),
        'gap_trend': _compute_gap_trend(conn, "2026-05"),
        'cron_health': _compute_cron_health(conn, "2026-05"),
        'dream_quality': _compute_dream_quality(conn, "2026-05"),
        'whisper_patterns': _compute_whisper_patterns(conn, "2026-05"),
        'capacity_headroom': _compute_capacity_headroom(conn, "2026-05"),
        'threshold_assessment': assess_threshold(conn, "2026-05"),
        'persona_evolution': _compute_persona_evolution(conn, "2026-05"),
    }
    proposals = generate_proposals(
        conn, scorecard, "2026-05", supplemental,
        {'use_llm_proposals': False, **DEFAULT_CONFIG}
    )
    _assert(len(proposals['enhancements']) >= 3, "At least 3 enhancements")
    _assert(len(proposals['enhancements']) <= 5, "At most 5 enhancements")
    _assert(all('title' in e for e in proposals['enhancements']), "All enhancements have title")
    _assert(proposals['generation_method'] == 'heuristic', "Heuristic method")
    conn.close()

    # --- Test 13: Full self-eval run ---
    print("\n--- Full Self-Eval Run ---")
    result = run_self_eval(db_path, month="2026-05", config={'use_llm_proposals': False})
    _assert(result['status'] == 'ok', "Self-eval completed")
    _assert('report_id' in result, "Has report_id")
    _assert(result['report']['month'] == '2026-05', "Correct month")
    _assert(result['report']['overall_health'] in ('excellent', 'good', 'concerning', 'critical'),
            "Valid overall_health")
    report_id = result['report_id']

    # --- Test 14: Report retrieval ---
    print("\n--- Report Retrieval ---")
    retrieved = get_report(db_path, month="2026-05")
    _assert(retrieved['status'] == 'ok', "Report retrieved")
    _assert(retrieved['report'] is not None, "Report exists")
    _assert(retrieved['report']['month'] == '2026-05', "Correct month")

    retrieved_by_id = get_report(db_path, report_id=report_id)
    _assert(retrieved_by_id['status'] == 'ok', "Report retrieved by ID")

    # --- Test 15: owner review ---
    print("\n--- Owner Review ---")
    report = retrieved['report']
    enhancements = report.get('proposed_enhancements', [])
    if enhancements:
        first_title = enhancements[0]['title']
        review_result = drew_review(
            db_path, report_id,
            approved=[first_title],
            rejected=[{'title': enhancements[1]['title'], 'reason': 'Not needed'}] if len(enhancements) > 1 else []
        )
        _assert(review_result['status'] == 'ok', "Review recorded")
        _assert(review_result['approved_count'] == 1, "1 approved")

        reviewed = get_report(db_path, report_id=report_id)
        _assert(reviewed['report']['status'] == 'drew_reviewed', "Status updated")
    else:
        _assert(False, "No enhancements to review")

    # --- Test 16: Enact approved ---
    print("\n--- Enact Approved ---")
    enact_result = enact_approved(db_path, report_id)
    _assert(enact_result['status'] == 'ok', "Enactment succeeded")
    _assert(len(enact_result.get('enacted', [])) >= 1, "At least 1 enacted")
    _assert(len(enact_result.get('failed', [])) == 0, "No failures")

    enacted_report = get_report(db_path, report_id=report_id)
    _assert(enacted_report['report']['status'] == 'enacted', "Report status = enacted")

    # --- Test 17: Double review prevention ---
    print("\n--- Double Review Prevention ---")
    double_review = drew_review(db_path, report_id, approved=[])
    _assert(double_review['status'] == 'error', "Double review blocked")

    # --- Test 18: Unknown proposal title ---
    print("\n--- Unknown Proposal Title ---")
    new_result = run_self_eval(db_path, month="2026-04", config={'use_llm_proposals': False})
    new_id = new_result['report_id']
    bad_review = drew_review(db_path, new_id, approved=['NONEXISTENT TITLE'])
    _assert(bad_review['status'] == 'error', "Unknown title rejected")
    _assert(len(bad_review.get('unknown_approved', [])) > 0, "Unknown title listed")

    # --- Test 19: Module-only piece scoring ---
    print("\n--- Module-Only Piece Scoring ---")
    conn = sqlite3.connect(db_path)
    score = _score_module_only_piece(conn, 'hybrid_retrieval', '2026-05', 'test')
    _assert(score['score'] >= 0 and score['score'] <= 1.0, "Valid score range")
    _assert('Inferred' in score['notes'], "Notes mention inference")
    conn.close()

    # --- Test 20: Idempotent migration ---
    print("\n--- Idempotent Migration ---")
    result = apply_migration(db_path, str(Path(__file__).parent.parent / "migrations" / "migration_021_self_eval.sql"))
    _assert(result['status'] == 'ok', "Migration idempotent")
    _assert(result['action'] == 'migration_already_applied', "Recognized as already applied")

    # --- Test 21: Month boundary calculation ---
    print("\n--- Month Boundary ---")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sleep_loop_runs (started_at, status) VALUES ('2026-12-15T08:00:00', 'success')"
    )
    conn.commit()
    sc = compute_health_scorecard(conn, "2026-12")
    _assert(sc.get('L2-C3-sleep-loop', {}).get('usage', 0) == 1, "December boundary correct")
    conn.close()

    # --- Test 22: Empty enact ---
    print("\n--- Empty Enact ---")
    empty_result = enact_approved(db_path, 9999)
    _assert(empty_result['action'] == 'nothing_to_enact', "Nothing to enact for nonexistent report")

    # --- Test 23: Report retrieval (latest) ---
    print("\n--- Latest Report Retrieval ---")
    latest = get_report(db_path)
    _assert(latest['status'] == 'ok', "Latest report retrieved")
    _assert(latest['report'] is not None, "Latest report exists")

    # --- Test 24: Scorecard score range ---
    print("\n--- Score Range Validation ---")
    conn = sqlite3.connect(db_path)
    scorecard = compute_health_scorecard(conn, "2026-05")
    for piece_name, data in scorecard.items():
        _assert(0 <= data['score'] <= 1.0, f"{piece_name} score in [0,1]")
    conn.close()

    # --- Test 25: Config override ---
    print("\n--- Config Override ---")
    result = run_self_eval(db_path, month="2026-03", config={
        'use_llm_proposals': False,
        'current_threshold': 9.90,
    })
    _assert(result['status'] == 'ok', "Config override works")
    threshold = result['report']['threshold_assessment']
    _assert(threshold['current_threshold'] == 9.90, "Threshold overridden to 9.90")

    # --- Cleanup ---
    try:
        os.unlink(db_path)
    except OSError:
        pass

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed, {total} total")
    print("=" * 60)
    return {"passed": passed, "failed": failed, "total": total}


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Monthly Brain Self-Evaluation — Wizard Library L4 Piece 9"
    )
    parser.add_argument("db_path", nargs="?", default=None,
                        help="Path to SQLite database (default: sibling claude_memory.db)")
    parser.add_argument("--run", action="store_true",
                        help="Run full self-evaluation")
    parser.add_argument("--month", default=None,
                        help="Month to evaluate (YYYY-MM, default: previous month)")
    parser.add_argument("--report", action="store_true",
                        help="Show latest report")
    parser.add_argument("--report-id", type=int, default=None,
                        help="Show specific report by ID")
    parser.add_argument("--review", type=int, default=None, metavar="REPORT_ID",
                        help="Review a report (requires --approve and/or --reject)")
    parser.add_argument("--approve", nargs="*", default=None,
                        help="Titles of proposals to approve")
    parser.add_argument("--reject", nargs="*", default=None,
                        help="JSON objects of rejected proposals (title + reason)")
    parser.add_argument("--enact", type=int, default=None, metavar="REPORT_ID",
                        help="Enact approved proposals for a report")
    parser.add_argument("--surface", action="store_true",
                        help="Write an 'owner-approval-pending' reflection memory "
                             "summarising reports awaiting the owner (post-run digest; "
                             "intended for cron ExecStartPost)")
    parser.add_argument("--config", default=None,
                        help="Path to JSON config file")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip LLM proposal generation, use heuristics only")
    parser.add_argument("--unit-tests", action="store_true",
                        help="Run unit tests")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )

    if args.unit_tests:
        results = unit_test_monthly_self_eval()
        sys.exit(0 if results['failed'] == 0 else 1)

    db_path = args.db_path or str(Path(__file__).parent.parent / "claude_memory.db")
    config = None
    if args.config:
        config = json.loads(Path(args.config).read_text())
    if args.no_llm:
        config = config or {}
        config['use_llm_proposals'] = False

    if args.run:
        result = run_self_eval(db_path, month=args.month, config=config)
        # 2026-10-26: the owner's approvals choice. In auto mode the report just
        # written is decided by the policy (actor policy:auto); in manual mode,
        # or with no choice recorded, it waits for a person as before. Outside the
        # report's transaction: a policy failure can never un-write the report.
        if isinstance(result, dict) and result.get("status") == "ok":
            try:
                import approvals_policy as _AP
                result["approvals_policy"] = _AP.decide_new_self_eval_report(
                    db_path, result.get("report_id"))
            except Exception as _pe:  # noqa: BLE001 - reported, never swallowed silently
                result["approvals_policy"] = {"status": "error",
                                              "error": "%s: %s" % (type(_pe).__name__, _pe)}
                logger.error("approvals policy failed after report #%s: %s",
                             result.get("report_id"), _pe)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.report or args.report_id is not None:
        result = get_report(db_path, report_id=args.report_id)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.review is not None:
        approved = args.approve or []
        rejected_raw = args.reject or []
        rejected = []
        for r in rejected_raw:
            try:
                rejected.append(json.loads(r))
            except json.JSONDecodeError:
                rejected.append({'title': r, 'reason': 'rejected'})
        result = drew_review(db_path, args.review, approved=approved, rejected=rejected)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.surface:
        result = surface_pending_for_owner(db_path)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.enact is not None:
        result = enact_approved(db_path, args.enact, config=config)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        result = get_report(db_path)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    # R5/C3 -- THE MACHINE-READABLE HALF OF THE REFUSAL. Every verb above
    # printed its refusal and then exited 0, so a cron, a shell `&&` and a
    # systemd unit all read "success" from a run that did nothing. The exit is
    # non-zero ONLY for the two states in which the verb could not have done
    # anything at all -- there is no brain, or the file is 0 bytes. A
    # substantive error keeps its historical exit 0 so no existing caller's
    # control flow changes; BLAST RADIUS, MEASURED: the new code fires only on a
    # path where no database exists, i.e. where nothing could have succeeded.
    if isinstance(result, dict) and result.get("error") in ("brain_not_found",
                                                            "brain_empty"):
        sys.exit(4)
    # CRUCIBLE-3 D04-F4. The comment above kept exit 0 for a SUBSTANTIVE error on
    # the stated ground that changing it would alter an existing caller's control
    # flow. The blast radius was re-measured for cycle 2 and that caller does not
    # exist: this organ is invoked from exactly one place, a systemd unit with
    # `Type=oneshot` (cron/wizard-library-self-eval.service), where THE EXIT CODE
    # IS THE SUCCESS SIGNAL -- plus `organs/claude_memory.py:511`, which imports
    # the module and never reads a process exit. There are no `&&` chains.
    #
    # So the conservative choice was protecting a consumer that is not there,
    # while the one consumer that does exist was being told a run that reported
    # `"status": "error"` had succeeded. Measured before this change:
    #   python3 organs/monthly_self_eval.py --run "$BRAIN"  ->  status "error", EXIT 0
    if isinstance(result, dict) and result.get("status") == "error":
        sys.exit(1)
